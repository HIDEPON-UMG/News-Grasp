#!/usr/bin/env python3
"""News-Grasp 重複記事除外ユーティリティ。

Routine の 3-A.5 フェーズで呼び出される。新規候補を articles.jsonl の
既存エントリと照合し、24 時間以内の類似記事を除外する。

使い方（CLI）:

    # 候補を JSON Lines 形式で stdin に流し、通過したものを stdout に返す
    python tools/dedup.py < candidates.jsonl > filtered.jsonl

    # データソース指定
    python tools/dedup.py --jsonl data/articles.jsonl < candidates.jsonl > filtered.jsonl

    # 24 時間ウィンドウを変更（テスト用）
    python tools/dedup.py --window-hours 12 < candidates.jsonl > filtered.jsonl

候補のスキーマ（最低限）:
    {"title": "...", "url": "...", "score": 88, ...}

通過した候補には以下が追加される:
    - "url_norm": 正規化済 URL
    - "is_followup": true/false（24h 超で続報扱いになった場合 true）
    - "matched_with": マッチした既存エントリの URL（続報時のみ）

除外された候補は出力されない（stderr に理由を出力）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode


JST = timezone(timedelta(hours=9))

TRACKING_KEYS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
    "ref", "ref_src", "fbclid", "gclid", "sessionid", "mc_eid", "_x_tr_sl",
    "amp", "output",
}

PUNCT_RE = re.compile(r"[「」『』\"\"''（）()【】\[\]　・\-—–_/、。,.！!？?：:；;]+")
SPACE_RE = re.compile(r"\s+")

# タイトル 2-gram Jaccard の既定閾値。2026-06-03 に 0.5 → 0.42 へ引き下げ。
# Mobility のように主役プールが小さいカテゴリでは、同じイベントを別表現で書いた
# 同言語の見出し (例「WaymoがOjai第6世代…公開開放 — A」vs「…公開開放 — B」) が
# Jaccard 0.45 前後に落ち、0.5 では「別記事」として連日再掲されていた (実測)。
DEFAULT_TITLE_THRESHOLD = 0.42

# ── 言語非依存トークン照合 (cross-language / 別ソースの同一イベント検知) ──────────
# 文字 2-gram は英語⇄日本語の見出し (例「Waymo accelerates … Dallas, Houston」と
# 「Waymo テキサスDallas・Houston…」) を Jaccard 0.1〜0.3 にしか乗せられず、同一
# イベントの再掲を「新規」として通してしまう (2026-06-03 Mobility 重複の主因)。社名・
# 地名・数値は翻訳しても字面が残るため、それらの重なりで同一イベントを補足する。
_TOKEN_STOPWORDS = frozenset({
    "the", "and", "for", "with", "now", "has", "had", "its", "new", "vs", "via",
    "added", "from", "that", "this", "into", "out", "over", "top", "more", "most",
    "than", "not", "you", "are", "was", "were", "will", "what", "why", "how",
    "who", "all", "but", "per", "inc", "ltd", "corp", "ai", "ev", "car", "cars",
    "city", "cities", "news", "report", "first", "his", "her", "their",
})
_ASCII_WORD_RE = re.compile(r"[a-z][a-z]{2,}")   # 3 文字以上の英字ラン (小文字化後)
_NUM_RE = re.compile(r"\d[\d,]*")


def significant_tokens(title: str) -> tuple[set[str], set[str]]:
    """タイトルから言語非依存トークン (英字固有名詞・数値) を抽出する。

    返り値 (words, nums):
      words = 3 文字以上の英字語のうち一般語 (stopword) を除いたもの。社名・地名・
              略号 (waymo / tesla / dallas / nhtsa / byd / fsd 等) を拾う想定。
      nums  = 2 桁以上の数値 (1 桁はノイズが多いので除外)。「1,400」「$20,000」の
              桁区切り/記号は除去して 1400 / 20000 に揃える。
    """
    t = title.lower().replace("　", " ")
    words = {w for w in _ASCII_WORD_RE.findall(t) if w not in _TOKEN_STOPWORDS}
    nums = {n.replace(",", "") for n in _NUM_RE.findall(t)}
    # 2 桁未満 (1 桁) はノイズが多いので除外。西暦らしき 4 桁 (2000〜2099) も日付として
    # ほぼ全タイトルに出るため誤一致源になるので除外する (1400 等の実数値は残す)。
    nums = {n for n in nums if len(n) >= 2 and not (len(n) == 4 and "2000" <= n <= "2099")}
    return words, nums


def same_event_by_tokens(
    w1: set[str], n1: set[str], w2: set[str], n2: set[str],
) -> bool:
    """言語非依存トークンの重なりで「同一イベント (別ソース/別言語の再掲)」を判定する。

    判定 (いずれか成立で同一イベント):
      ① 固有名詞語が 3 つ以上共通 (例 Waymo×Dallas×Houston)
      ② 固有名詞語が 2 つ以上共通 かつ 数値が 1 つ以上共通 (例 Waymo×Tesla×577)
      ③ 固有名詞語が 1 つ以上共通 かつ 数値が 2 つ以上共通 (例 Waymo×1400×11)
         — 日本語見出しは ASCII 固有名詞が少なく語の重なりが伸びないため、社名 1 語+
           特徴的な数値 2 つで同一イベントを補足する経路。

    共通が固有名詞 1 語だけ (例: 同じ会社の別ニュース) では発火させない — 誤検知で
    別イベントを潰さないため。
    """
    shared_w = w1 & w2
    shared_n = n1 & n2
    if len(shared_w) >= 3:
        return True
    if len(shared_w) >= 2 and len(shared_n) >= 1:
        return True
    if len(shared_w) >= 1 and len(shared_n) >= 2:
        return True
    return False


def normalize_url(url: str) -> str:
    """URL 正規化: scheme/host 小文字化、tracking 除去、AMP 解除、末尾 / 統一。"""
    try:
        s = urlsplit(url)
    except ValueError:
        return url
    scheme = s.scheme.lower() or "https"
    host = s.netloc.lower()
    if host.startswith("m."):
        host = host[2:]
    if host.startswith("www."):
        # 任意: www. を残しても良いが、揃えた方が一致しやすいので削る
        host = host[4:]
    path = s.path or "/"
    path = re.sub(r"/amp/?$", "/", path)
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    qs = [
        (k, v) for k, v in parse_qsl(s.query, keep_blank_values=True)
        if k.lower() not in TRACKING_KEYS and not k.lower().startswith("utm_")
    ]
    query = urlencode(qs)
    return urlunsplit((scheme, host, path, query, ""))


def normalize_title(title: str) -> str:
    """タイトル正規化: 全角→半角・小文字・記号除去・連続空白圧縮。"""
    # 全角英数→半角
    t = title.translate(str.maketrans({chr(0xFF01 + i): chr(0x21 + i) for i in range(94)}))
    t = t.translate(str.maketrans({"　": " "}))  # 全角スペース
    t = t.lower()
    t = PUNCT_RE.sub(" ", t)
    t = SPACE_RE.sub(" ", t).strip()
    return t


def char_ngrams(text: str, n: int = 2) -> set[str]:
    if len(text) < n:
        return {text}
    return {text[i : i + n] for i in range(len(text) - n + 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def parse_iso(ts: str) -> datetime:
    """ISO 8601 (with optional TZ) を timezone-aware datetime にする。"""
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(JST)


def load_existing(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    # data/articles.jsonl は UTF-8 BOM 付きで保存されている（日次 append スクリプト由来）。
    # encoding="utf-8" だと先頭行が "Unexpected UTF-8 BOM" で json.loads に失敗するため
    # utf-8-sig で読む（BOM が無いファイルでも透過的に動く）。
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def find_match(
    candidate: dict,
    existing: list[dict],
    title_threshold: float = DEFAULT_TITLE_THRESHOLD,
    ngram_n: int = 2,
) -> tuple[dict | None, str | None]:
    """候補に対して existing の中から最初にマッチするエントリと種別を返す。

    返り値 (entry, match_type):
      - ("url",)   : 正規化 URL が完全一致 = 同一記事そのもの
      - ("title",) : タイトル一致/類似、または言語非依存トークン一致 = 同一トピック
                     (続報の可能性あり)
      - (None, None): マッチなし
    呼び出し側は match_type で「同一記事の再掲(常に除外)」と
    「続報候補(時間窓で判定)」を区別する。
    """
    # 保存済み url_norm は過去バージョンで scheme 有無が不統一なため信頼せず、
    # 毎回 url から再正規化して比較する (取りこぼし防止)。
    cand_url_norm = normalize_url(candidate.get("url", ""))
    cand_title_norm = normalize_title(candidate.get("title", ""))
    cand_ngrams = char_ngrams(cand_title_norm, n=ngram_n)
    cand_w, cand_n = significant_tokens(candidate.get("title", ""))

    for e in existing:
        # A. URL 正規化マッチ (= 同一記事)
        e_url_norm = normalize_url(e["url"]) if e.get("url") else e.get("url_norm", "")
        if cand_url_norm and cand_url_norm == e_url_norm:
            return e, "url"
        # B. タイトル一致 / 類似 (= 同一トピック・続報候補)
        e_title = e.get("title", "")
        e_title_norm = normalize_title(e_title)
        if cand_title_norm and cand_title_norm == e_title_norm:
            return e, "title"
        if jaccard(cand_ngrams, char_ngrams(e_title_norm, n=ngram_n)) >= title_threshold:
            return e, "title"
        # C. 言語非依存トークン一致 (英語⇄日本語・別ソースの同一イベント)。
        #    文字 2-gram では橋渡しできない cross-language の再掲をここで捕捉する。
        e_w, e_n = significant_tokens(e_title)
        if same_event_by_tokens(cand_w, cand_n, e_w, e_n):
            return e, "title"
    return None, None


def dedup_candidates(
    candidates: list[dict],
    existing: list[dict],
    window_hours: float = 24.0,
    title_threshold: float = DEFAULT_TITLE_THRESHOLD,
) -> tuple[list[dict], list[dict]]:
    """重複除外を実行し (passed, dropped) を返す。"""
    now = datetime.now(JST)
    now_iso = now.isoformat()
    window = timedelta(hours=window_hours)
    # 候補側を順に評価しつつ、合格分を「既存」に積み増して同 batch 内重複も弾く
    passed: list[dict] = []
    dropped: list[dict] = []
    pool = list(existing)
    for c in candidates:
        c["url_norm"] = normalize_url(c.get("url", ""))
        match, match_type = find_match(c, pool, title_threshold=title_threshold)
        if match is None:
            c["is_followup"] = False
            c.setdefault("seen_at", now_iso)
            passed.append(c)
            pool.append(c)
            continue
        if match_type == "url":
            # 完全に同じ記事 (正規化 URL 一致) は経過時間に関係なく常に除外する。
            # 続報は必ず別 URL になるので、ここで落ちるのは「同一記事の複数日再掲」だけ。
            # (従来は 24h 窓を超えると続報扱いで通過し、同じ記事が数日連続で載っていた)
            c["dedup_reason"] = (
                f"same article (url match, any age) url={(match.get('url') or '')[:50]}"
            )
            dropped.append(c)
            continue
        # 以降は title 類似マッチ = 同一トピック。時間窓で続報かどうかを判定する。
        seen_at = match.get("seen_at")
        if not seen_at:
            seen_at = f"{match.get('date', '1970-01-01')}T00:00:00+09:00"
        try:
            seen_dt = parse_iso(seen_at)
        except ValueError:
            seen_dt = parse_iso("1970-01-01T00:00:00+09:00")
        delta = now - seen_dt
        if delta <= window:
            c["dedup_reason"] = (
                f"matched url={(match.get('url') or '')[:50]} "
                f"delta={delta.total_seconds()/3600:.1f}h <= {window_hours}h"
            )
            dropped.append(c)
        else:
            c["is_followup"] = True
            c["matched_with"] = match.get("url")
            c.setdefault("seen_at", now_iso)
            passed.append(c)
            pool.append(c)
    return passed, dropped


def main() -> int:
    p = argparse.ArgumentParser(description="News-Grasp 重複記事除外")
    p.add_argument("--jsonl", default="data/articles.jsonl",
                   help="既存記事メタの JSON Lines パス（既定: data/articles.jsonl）")
    p.add_argument("--window-hours", type=float, default=24.0,
                   help="24 時間ウィンドウ（既定: 24）")
    p.add_argument("--title-threshold", type=float, default=DEFAULT_TITLE_THRESHOLD,
                   help=f"タイトル N-gram Jaccard 類似度の閾値（既定: {DEFAULT_TITLE_THRESHOLD}）")
    args = p.parse_args()

    candidates = []
    for line in sys.stdin:
        line = line.strip()
        if line:
            candidates.append(json.loads(line))

    existing = load_existing(Path(args.jsonl))
    passed, dropped = dedup_candidates(
        candidates, existing,
        window_hours=args.window_hours,
        title_threshold=args.title_threshold,
    )

    for c in passed:
        print(json.dumps(c, ensure_ascii=False))
    print(
        f"dedup: {len(passed)} passed, {len(dropped)} dropped "
        f"(window={args.window_hours}h, threshold={args.title_threshold})",
        file=sys.stderr,
    )
    for c in dropped:
        print(f"  DROP: {c.get('title', '')[:60]} | {c.get('dedup_reason', '')}",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
