"""articles.jsonl の thumb フィールド充足率を集計する。

OGP サムネ取得 pipeline の現状把握 (P5 計測) のために用意したスクリプト。
全レコードを 3 状態に分類して集計する:

  - missing : "thumb" キー自体が存在しない
  - null    : 値が None / 空文字 / 空白のみ
  - present : http(s) で始まる有効そうな URL

集計軸: 全体 / ジャンル別 / 日付別 / ドメイン別 (URL ホスト名)。

実行例:
  py tools/thumb_stats.py
  py tools/thumb_stats.py --since 2026-04-28
  py tools/thumb_stats.py --json   # 機械判読用 JSON 出力

Routine の system prompt 改修や fetch_ogp.py 導入の前後比較に使える。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_JSONL = REPO_ROOT / "data" / "articles.jsonl"


def classify(rec: dict) -> str:
    """1 レコードを missing / null / present の 3 状態に分類。"""
    if "thumb" not in rec:
        return "missing"
    val = rec.get("thumb")
    if val is None:
        return "null"
    if isinstance(val, str) and val.strip() == "":
        return "null"
    if isinstance(val, str) and val.strip().lower().startswith(("http://", "https://")):
        return "present"
    return "null"


def domain_of(url: str) -> str:
    try:
        host = urlparse(url).hostname or "(no-host)"
    except Exception:
        return "(parse-error)"
    return host.lower().lstrip("www.") if host.lower().startswith("www.") else host.lower()


def load_records(path: Path, since: str | None):
    """jsonl を 1 行ずつ yield。since が指定されたら date >= since のものに絞る。"""
    with path.open(encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"[warn] line {lineno}: JSON decode error: {e}", file=sys.stderr)
                continue
            if since and rec.get("date", "") < since:
                continue
            yield rec


def aggregate(records):
    overall = Counter()
    by_genre: dict[str, Counter] = defaultdict(Counter)
    by_date: dict[str, Counter] = defaultdict(Counter)
    by_domain: dict[str, Counter] = defaultdict(Counter)
    total = 0

    for rec in records:
        state = classify(rec)
        overall[state] += 1
        by_genre[rec.get("genre", "(unknown)")][state] += 1
        by_date[rec.get("date", "(unknown)")][state] += 1
        by_domain[domain_of(rec.get("url", ""))][state] += 1
        total += 1

    return {
        "total": total,
        "overall": overall,
        "by_genre": by_genre,
        "by_date": by_date,
        "by_domain": by_domain,
    }


def fmt_row(label: str, c: Counter, width: int = 28) -> str:
    miss = c.get("missing", 0)
    nul = c.get("null", 0)
    pre = c.get("present", 0)
    tot = miss + nul + pre
    rate = (pre / tot * 100) if tot else 0.0
    return (
        f"  {label:<{width}}  "
        f"total={tot:4d}  present={pre:4d} ({rate:5.1f}%)  "
        f"null={nul:4d}  missing={miss:4d}"
    )


def print_report(agg: dict, top_domains: int = 20) -> None:
    total = agg["total"]
    print("=" * 78)
    print(f" thumb 充足率レポート  (total records: {total})")
    print("=" * 78)

    print("\n[全体]")
    print(fmt_row("ALL", agg["overall"]))

    print("\n[ジャンル別]")
    for genre in sorted(agg["by_genre"]):
        print(fmt_row(genre, agg["by_genre"][genre]))

    print("\n[日付別]")
    for date in sorted(agg["by_date"]):
        print(fmt_row(date, agg["by_date"][date]))

    print(f"\n[ドメイン別 (件数 TOP {top_domains})]")
    items = sorted(
        agg["by_domain"].items(),
        key=lambda kv: -sum(kv[1].values()),
    )[:top_domains]
    for host, c in items:
        print(fmt_row(host, c, width=44))


def to_jsonable(agg: dict) -> dict:
    return {
        "total": agg["total"],
        "overall": dict(agg["overall"]),
        "by_genre": {k: dict(v) for k, v in agg["by_genre"].items()},
        "by_date": {k: dict(v) for k, v in agg["by_date"].items()},
        "by_domain": {k: dict(v) for k, v in agg["by_domain"].items()},
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--path", type=Path, default=DEFAULT_JSONL, help=f"対象 jsonl (default: {DEFAULT_JSONL})")
    p.add_argument("--since", type=str, default=None, help="この日付以降に絞る (YYYY-MM-DD)")
    p.add_argument("--top-domains", type=int, default=20, help="ドメイン別表示件数 (default: 20)")
    p.add_argument("--json", action="store_true", help="JSON で機械判読用に出力")
    args = p.parse_args()

    if not args.path.exists():
        print(f"error: not found: {args.path}", file=sys.stderr)
        return 2

    agg = aggregate(load_records(args.path, args.since))

    if args.json:
        print(json.dumps(to_jsonable(agg), ensure_ascii=False, indent=2))
    else:
        print_report(agg, top_domains=args.top_domains)
    return 0


if __name__ == "__main__":
    sys.exit(main())
