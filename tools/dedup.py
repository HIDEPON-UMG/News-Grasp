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
import calendar
import json
import re
import sys
from datetime import date, datetime, timezone, timedelta
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
DEFAULT_MAX_SOURCE_AGE_DAYS = 7

_URL_DATE_PATTERNS = (
    re.compile(r"/(?P<y>20\d{2})/(?P<m>\d{1,2})/(?P<d>\d{1,2})(?:/|$)"),
    re.compile(r"/(?P<y>20\d{2})-(?P<m>\d{1,2})-(?P<d>\d{1,2})(?:[-_/]|$)"),
    re.compile(r"/(?P<y>20\d{2})(?P<m>\d{2})(?P<d>\d{2})(?:[-_/]|$)"),
)
_URL_MONTH_DATE_RE = re.compile(
    r"/(?P<y>20\d{2})/(?P<mon>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)/(?P<d>\d{1,2})(?:/|$)",
    re.IGNORECASE,
)
_MONTH_ALIASES = {name.lower(): idx for idx, name in enumerate(calendar.month_abbr) if name}

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

# ── 日本語 (カタカナ) 固有名詞の補足 ─────────────────────────────────────────────
# significant_tokens は英字ラン (_ASCII_WORD_RE) しか拾わないため、「ヨッシー」「スプラ
# トゥーン」のような純カタカナタイトルの固有名詞を取りこぼす (2026-06-03 Game 重複整理で
# ヨッシー 05-18 が「Yoshi 05-01」と照合されず連日再掲を検出できなかった主因)。3 文字以上
# のカタカナラン (先頭カタカナ + 長音/カタカナ) を固有名詞候補として words に足す。
_KATAKANA_RE = re.compile(r"[ァ-ヶ][ァ-ヶー]{2,}")

# 固有名詞でない一般カタカナ語 (これらは words に入れない = 誤検知源を断つ)。News-Grasp の
# ゲーム/IT 文脈で頻出する非固有名詞を列挙。
_KATAKANA_STOPWORDS = frozenset({
    "タイトル", "ゲーム", "リリース", "シリーズ", "プラットフォーム", "ユーザー",
    "スケジュール", "ラインナップ", "エコシステム", "サプライズ", "ファースト",
    "パーティ", "ファミリー", "コントローラー", "ネイティブ", "デジタル", "パッケージ",
    "メモリ", "サブスク", "オンライン", "マルチ", "アクション", "ソフト", "ハード",
    "セール", "キャンペーン", "スタジオ", "グローバル", "コンテンツ", "プレイ",
    "ゲーマー", "インディ", "プレイヤー", "ポート", "アップデート", "ロードマップ",
    "ローンチ", "ダウンロード", "パブリッシャー", "サードパーティ", "ファーストパーティ",
})

# 英日エイリアス: カタカナ固有名詞 → 英字正規形。同一タイトルが英語見出しと日本語見出しで
# 別トークン化される (Yoshi ⇔ ヨッシー) のを揃えて cross-language 照合に乗せる。Game 頻出
# タイトル中心の最小辞書。連日再掲が新たに漏れたらここに 1 行足して契約テストを増やす運用
# (= class of bugs を辞書 1 箇所に集約。音訳ライブラリ依存は増やさない)。
_JA_EN_ALIAS = {
    "ヨッシー": "yoshi",
    "テイルズ": "tales",
    "スプラトゥーン": "splatoon",
    "プラグマタ": "pragmata",
    "ゼルダ": "zelda",
    "マリオ": "mario",
    "ポケモン": "pokemon",
    "スターフォックス": "starfox",
    "メトロイド": "metroid",
    "カービィ": "kirby",
    "ドラゴンクエスト": "dragonquest",
    "ファイナルファンタジー": "finalfantasy",
}


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
    # 日本語カタカナ固有名詞 (一般語は stopword 除外) を英日エイリアスで英字正規形に寄せて
    # words に加える。これで純カタカナの日本語見出しからも社名・タイトルを拾い、英語見出し
    # (Yoshi) と日本語見出し (ヨッシー) を同一トークンとして照合できる (lower 前の原文から
    # 抽出 — カタカナは小文字化の影響を受けないが、エイリアスキーと突き合わせるため)。
    for kw in _KATAKANA_RE.findall(title):
        if kw in _KATAKANA_STOPWORDS:
            continue
        words.add(_JA_EN_ALIAS.get(kw, kw))
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
        # A. URL 正規化マッチ (= 同一記事)。最優先で全 existing を走査する。
        # 同一 URL より前に古いタイトル/トークン一致が見つかると続報扱いで通過するため。
        e_url_norm = normalize_url(e["url"]) if e.get("url") else e.get("url_norm", "")
        if cand_url_norm and cand_url_norm == e_url_norm:
            return e, "url"

    for e in existing:
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


def extract_source_date_from_url(url: str) -> date | None:
    """URL パス内の発行日らしき日付を抽出する。

    CNBC/TechCrunch/新聞系に多い ``/2026/06/01/``、``/2026-06-01-...``、
    ``/20260601-...``、``/2026/jun/04/`` を対象にする。URL に日付が無い場合は
    取得不能として None を返し、鮮度ゲートでは落とさない。
    """
    try:
        path = urlsplit(url).path
    except ValueError:
        return None
    for pat in _URL_DATE_PATTERNS:
        m = pat.search(path)
        if not m:
            continue
        try:
            return date(int(m.group("y")), int(m.group("m")), int(m.group("d")))
        except ValueError:
            return None
    m2 = _URL_MONTH_DATE_RE.search(path)
    if m2:
        mon = _MONTH_ALIASES.get(m2.group("mon").lower())
        if mon:
            try:
                return date(int(m2.group("y")), mon, int(m2.group("d")))
            except ValueError:
                return None
    return None


def _freshness_drop_reason(
    candidate: dict,
    now_date: date,
    max_source_age_days: int,
) -> str | None:
    src_date = extract_source_date_from_url(candidate.get("url", ""))
    if src_date is None:
        return None
    age_days = (now_date - src_date).days
    if age_days > max_source_age_days:
        return (
            f"freshness gate: source date {src_date.isoformat()} "
            f"age={age_days}d > {max_source_age_days}d"
        )
    return None


def _new_material_tokens(candidate: dict, matched: dict) -> tuple[set[str], set[str]]:
    """続報候補が前回掲載時から持ち込んだ「新規 token / 新規数値」を抽出する。

    続報ゲート (3-A.5 E) の機械判定用。LLM の意味判断に頼らず、タイトル + summary
    の significant_tokens を `(候補 - 既存)` で差分するだけ。

    - 新規 token (英字固有名詞 / カタカナ固有名詞のエイリアス済み) が 1 個以上 か
      新規数値 が 1 個以上 → 新材料あり
    - 両方とも 0 個 → 「前回と同じ情報の言い換え」 = 新材料なし = 続報採用すべきでない

    `title` だけでなく `summary` (= digest md の `bullets` を結合した 100×3 字)
    も対象にすることで、見出しは違うが本文が同じ ニュースを取りこぼさない。
    """
    cand_text = f"{candidate.get('title', '')} {candidate.get('summary', '')}"
    matched_text = f"{matched.get('title', '')} {matched.get('summary', '')}"
    cand_w, cand_n = significant_tokens(cand_text)
    m_w, m_n = significant_tokens(matched_text)
    return (cand_w - m_w, cand_n - m_n)


def dedup_candidates(
    candidates: list[dict],
    existing: list[dict],
    window_hours: float = 24.0,
    title_threshold: float = DEFAULT_TITLE_THRESHOLD,
    followup_gate: bool = False,
    freshness_gate: bool = False,
    max_source_age_days: int = DEFAULT_MAX_SOURCE_AGE_DAYS,
    now: datetime | None = None,
) -> tuple[list[dict], list[dict]]:
    """重複除外を実行し (passed, dropped) を返す。

    ``followup_gate=True`` のとき、URL 別 + タイトル類似マッチ + 24h 超で「続報」
    扱いになった候補について、新規 token / 新規数値が 0 個なら drop する (= LLM の
    意味判断に頼らない構造ゲート / feedback_check_design_principles Lv2 境界 1 箇所集約 /
    routine-system.md 3-A.5 E の機械化版・2026-06-05 導入)。
    """
    now = (now or datetime.now(JST)).astimezone(JST)
    now_iso = now.isoformat()
    window = timedelta(hours=window_hours)
    # 候補側を順に評価しつつ、合格分を「既存」に積み増して同 batch 内重複も弾く
    passed: list[dict] = []
    dropped: list[dict] = []
    pool = list(existing)
    for c in candidates:
        c["url_norm"] = normalize_url(c.get("url", ""))
        if freshness_gate:
            reason = _freshness_drop_reason(c, now.date(), max_source_age_days)
            if reason:
                c["dedup_reason"] = reason
                dropped.append(c)
                continue
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
            continue
        # 24h 超 = 続報候補。followup_gate が ON なら新材料 token diff を機械判定。
        if followup_gate:
            new_w, new_n = _new_material_tokens(c, match)
            if not new_w and not new_n:
                # 新材料 0 個 = 前回と同じ情報の言い換え。3-A.5 E ゲートで落とす。
                c["dedup_reason"] = (
                    f"followup gate: 新材料 0 (前回掲載と同じ情報) "
                    f"matched url={(match.get('url') or '')[:50]} "
                    f"delta={delta.total_seconds()/3600:.1f}h > {window_hours}h"
                )
                dropped.append(c)
                continue
            # 通過。dedup_reason に新材料を記録 (後段の編集判断と監査用)
            c["followup_new_words"] = sorted(new_w)
            c["followup_new_nums"] = sorted(new_n)
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
    p.add_argument("--followup-gate", action="store_true",
                   help="続報候補について「新規 token 0 個 = 落とす」機械判定を有効化 "
                        "(routine-system 3-A.5 E の LLM 任せを境界 1 箇所集約・2026-06-05 導入)")
    p.add_argument("--freshness-gate", action="store_true",
                   help="URL パス上の発行日が古すぎる候補を除外する鮮度ゲートを有効化")
    p.add_argument("--max-source-age-days", type=int, default=DEFAULT_MAX_SOURCE_AGE_DAYS,
                   help=f"鮮度ゲートで許容する URL 発行日からの経過日数（既定: {DEFAULT_MAX_SOURCE_AGE_DAYS}）")
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
        followup_gate=args.followup_gate,
        freshness_gate=args.freshness_gate,
        max_source_age_days=args.max_source_age_days,
    )

    for c in passed:
        print(json.dumps(c, ensure_ascii=False))
    print(
        f"dedup: {len(passed)} passed, {len(dropped)} dropped "
        f"(window={args.window_hours}h, threshold={args.title_threshold}, "
        f"freshness_gate={args.freshness_gate}, max_source_age_days={args.max_source_age_days})",
        file=sys.stderr,
    )
    for c in dropped:
        print(f"  DROP: {c.get('title', '')[:60]} | {c.get('dedup_reason', '')}",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
