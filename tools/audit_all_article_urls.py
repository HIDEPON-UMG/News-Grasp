#!/usr/bin/env python3
"""articles.jsonl の全 URL を validate_deepdive_urls の境界モジュールで一括検証する。

2026-06-03 三菱UFJ FX_Monthly 捏造事故の追加学習: 日次 digest の Claude セッションも
URL を捏造することが判明 (DeepDive の捏造 URL は実は日次 digest が articles.jsonl に
入れた捏造 URL を継承していた)。本スクリプトは articles.jsonl 全件を一括検証して
捏造 URL を炙り出す監査+ゲートツール。

# 役割

- ad-hoc 監査: 開発者が手で走らせて死リンク棚卸し
- 公開ゲート: news-grasp-runner.ps1 が Claude commit 後 / git push 前に呼び、捏造混入時
  は exit 1 で push を阻止する境界 (= 二度と公開しない構造)
- 契約テスト: tests/test_all_article_urls_live.py から呼ばれ、CI/開発時にも死リンク防止

# CLI

```
./.venv/Scripts/python.exe tools/audit_all_article_urls.py             # 全期間
./.venv/Scripts/python.exe tools/audit_all_article_urls.py --recent 7  # 直近7日のみ
./.venv/Scripts/python.exe tools/audit_all_article_urls.py --gate      # push gate モード
                                                                      # (recent 7 + 厳格 exit)
./.venv/Scripts/python.exe tools/audit_all_article_urls.py --gate --match-session
                                                                      # 案②-Lite: gate に
                                                                      # 加え当日 LLM
                                                                      # session で WebSearch
                                                                      # 確認した URL リスト
                                                                      # (data/_session_urls.json)
                                                                      # と articles.jsonl
                                                                      # 直近 N 日 URL を
                                                                      # 物理照合
```

exit 0 = 全 URL 健全 / exit 1 = 1 件以上 fatal (= 捏造または恒久 404 または session 未確認)。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parent.parent
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from tools.validate_deepdive_urls import UrlRef, verify_urls  # noqa: E402


def _normalize_url_for_match(url: str) -> str:
    """session 照合用の最小限の URL 正規化。

    完全一致だけで照合すると trailing slash 1 つ違いで誤検知が頻発するため、
    `_session_urls.json` 側でも articles.jsonl 側でも同じ正規化を適用する。

    やること:
      - 前後空白を剥がす
      - 末尾 `/` を 1 段だけ削除 (`https://a/` → `https://a`)
      - fragment (`#...`) を削除

    やらないこと (誤検知防止のため意図的に弱い正規化に留める):
      - クエリパラメータの並び替え・除去 → utm 除去まで踏み込むと dedup.py と
        実装が分散するし、session ファイルは LLM が WebSearch 結果をそのまま
        書き出す形なので utm が乗っていれば articles.jsonl 側にも乗っているため
        並列性が保たれる
      - host の小文字化 → 同上、WebSearch 結果と articles.jsonl は同じ source
        なので大文字小文字がズレる経路がない
    """
    s = url.strip()
    # fragment 除去
    if "#" in s:
        s = s.split("#", 1)[0]
    # 末尾スラッシュ削除 (https:// 直後の "//" は壊さない)
    if s.endswith("/") and not s.endswith("://"):
        s = s[:-1]
    return s


def _load_session_urls(repo_root: Path) -> tuple[set[str], Path, str | None]:
    """`data/_session_urls.json` から当日 LLM が WebSearch 200 確認した URL の白リストを読む。

    返り値:
        (正規化 URL set, ファイルパス, 当日 date 文字列 or None)

    ファイル不在/JSON 不正/空の場合は (空 set, パス, None) を返し、呼び出し側で
    degrade (= 警告のみで通過) させる。これは「session ファイルを書き忘れて gate が
    全件 fatal で push 失敗 → 朝のニュース配信が止まる事故」を避けるための fallback
    (本 handoff 3-4 副作用注意の意図的選択)。
    """
    p = repo_root / "data" / "_session_urls.json"
    if not p.exists():
        return set(), p, None
    try:
        with p.open(encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return set(), p, None
    urls_raw = data.get("urls") if isinstance(data, dict) else None
    if not isinstance(urls_raw, list):
        return set(), p, None
    norm = {_normalize_url_for_match(u) for u in urls_raw if isinstance(u, str) and u.startswith("http")}
    sess_date = data.get("date") if isinstance(data, dict) else None
    return norm, p, sess_date if isinstance(sess_date, str) else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--recent", type=int, default=0,
                    help="直近 N 日に絞る (0 = 全件)")
    ap.add_argument("--max-workers", type=int, default=16)
    ap.add_argument("--gate", action="store_true",
                    help="push gate モード (--recent 7 と同等 + 致命的フェイルで非ゼロ exit)")
    ap.add_argument("--match-session", action="store_true",
                    help="案②-Lite: data/_session_urls.json (= 当日 LLM が WebSearch で "
                         "200 確認した URL の白リスト) に articles.jsonl の直近 N 日 URL "
                         "が全て含まれているか物理照合する。含まれない URL は記憶捏造疑い "
                         "として fatal 扱い。session ファイル不在時は degrade (警告のみ・"
                         "従来 gate のみで継続) で朝のバッチを止めない")
    args = ap.parse_args()
    if args.gate and not args.recent:
        args.recent = 7  # gate は直近 7 日のみ走査 (push 速度のため・歴史的死リンクは別 ad-hoc で)

    jsonl = _PKG_ROOT / "data" / "articles.jsonl"
    if not jsonl.exists():
        print(f"no jsonl: {jsonl}", file=sys.stderr)
        return 2

    today = date.today()
    cutoff = today - timedelta(days=args.recent) if args.recent else None

    items: list[tuple[str, str, str]] = []  # (date, title, url)
    with jsonl.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            dt_str = str(d.get("date", "")).strip()
            url = str(d.get("url", "")).strip()
            title = str(d.get("title", "")).strip()
            if not url.startswith("http"):
                continue
            if cutoff:
                try:
                    dt = datetime.strptime(dt_str, "%Y-%m-%d").date()
                except ValueError:
                    continue
                if dt < cutoff:
                    continue
            items.append((dt_str, title, url))

    if not items:
        print("対象 URL が 0 件")
        return 0

    print(f"対象 URL: {len(items)} 件 ({'直近 ' + str(args.recent) + ' 日' if cutoff else '全期間'})")

    # 案②-Lite: session 白リスト照合 (gate と独立に動かせるが、本番運用は --gate と同時指定)
    session_fatal: list[tuple[str, str, str]] = []  # (date, title, url)
    if args.match_session:
        session_norm, session_path, session_date = _load_session_urls(_PKG_ROOT)
        if not session_norm:
            # degrade: session ファイル無し / 空 / 破損 → 物理照合は無効化して従来 gate のみで進む
            print(
                f"WARN: --match-session 指定だが {session_path.relative_to(_PKG_ROOT)} "
                f"が不在 or 空 or 破損のため session 照合を skip (従来 gate のみで継続)。"
                f"LLM が WebSearch 結果を書き出していない可能性があるので runner ログを確認すること",
                file=sys.stderr,
            )
        else:
            today_str = today.strftime("%Y-%m-%d")
            if session_date and session_date != today_str:
                # session date が当日でない (前日のまま残ってる等) → degrade と同じ扱い
                print(
                    f"WARN: _session_urls.json の date={session_date} が当日 {today_str} と "
                    f"不一致のため session 照合を skip (古い session を誤検知に使わないため)",
                    file=sys.stderr,
                )
            else:
                # session の date と同じ date の articles.jsonl エントリのみを照合対象にする。
                # 7 日窓全体ではなく当日分だけ照合する理由:
                #   - 過去の article は別の session で書かれた = 当日の session に居なくて当然
                #   - 全 7 日窓を照合するとロールアウト初日 (session 導入前の article が
                #     7 日窓に残っている時期) で全件 fatal になり朝のバッチが止まる
                #   - LLM の URL 捏造は「今日 LLM が書いた article」でしか起きないので、
                #     当日分だけ照合すれば必要十分 (歴史的 URL は別 ad-hoc audit でカバー)
                target_items = [it for it in items if it[0] == session_date]
                print(
                    f"session 照合: white-list {len(session_norm)} 件と "
                    f"当日 ({session_date}) の articles.jsonl {len(target_items)} 件を物理照合"
                )
                for dt_str, title, url in target_items:
                    norm = _normalize_url_for_match(url)
                    if norm not in session_norm:
                        session_fatal.append((dt_str, title, url))
                if session_fatal:
                    print(f"\n=== session 未確認 URL {len(session_fatal)} 件 (= LLM 捏造疑い) ===")
                    for dt_str, title, url in session_fatal:
                        print(f"  [{dt_str}|{title[:40]}] session に未登録")
                        print(f"    {url}")

    if os.environ.get("NEWS_GRASP_SKIP_URL_CHECK") == "1":
        # validate_deepdive_urls.require_live_urls と同じ環境変数で HEAD/GET を全スキップ。
        # オフライン CI・契約テスト (session 照合だけ見たい場合) の動作合わせのため。
        print("NEWS_GRASP_SKIP_URL_CHECK=1: HEAD/GET 検証を skip")
        verdicts = []
    else:
        refs = [UrlRef(url=url, location=f"{dt}|{title[:40]}") for dt, title, url in items]
        verdicts = verify_urls(refs, max_workers=args.max_workers)

    fatal = [v for v in verdicts if not v.ok]
    if verdicts:
        print(f"\n結果: {len(verdicts) - len(fatal)}/{len(verdicts)} OK (HEAD/GET), {len(fatal)} NG (HEAD/GET)")

    if fatal:
        print("\n=== NG URL 一覧 (要差し替え) ===")
        for v in fatal:
            print(f"  [{v.ref.location}] {v.detail}")
            print(f"    {v.ref.url}")

    # session 未確認 と HEAD/GET fatal の和集合が exit 判定に使われる
    total_fatal = len(fatal) + len(session_fatal)
    if total_fatal:
        if session_fatal:
            print(
                f"\nFATAL: session 未確認 {len(session_fatal)} 件 + HEAD/GET NG {len(fatal)} 件 = "
                f"{total_fatal} 件。push を中止します。", file=sys.stderr,
            )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
