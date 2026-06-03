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
```

exit 0 = 全 URL 健全 / exit 1 = 1 件以上 fatal (= 捏造または恒久 404)。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parent.parent
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from tools.validate_deepdive_urls import UrlRef, verify_urls  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--recent", type=int, default=0,
                    help="直近 N 日に絞る (0 = 全件)")
    ap.add_argument("--max-workers", type=int, default=16)
    ap.add_argument("--gate", action="store_true",
                    help="push gate モード (--recent 7 と同等 + 致命的フェイルで非ゼロ exit)")
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
    refs = [UrlRef(url=url, location=f"{dt}|{title[:40]}") for dt, title, url in items]
    verdicts = verify_urls(refs, max_workers=args.max_workers)

    fatal = [v for v in verdicts if not v.ok]
    print(f"\n結果: {len(verdicts) - len(fatal)}/{len(verdicts)} OK, {len(fatal)} NG")

    if fatal:
        print("\n=== NG URL 一覧 (要差し替え) ===")
        for v in fatal:
            print(f"  [{v.ref.location}] {v.detail}")
            print(f"    {v.ref.url}")
    return 1 if fatal else 0


if __name__ == "__main__":
    sys.exit(main())
