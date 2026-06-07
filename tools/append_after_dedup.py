#!/usr/bin/env python3
"""articles.jsonl 追記の境界スクリプト。

stdin の JSON Lines 候補を `tools.dedup` の重複・続報・鮮度ゲートに通し、
通過したレコードだけを `data/articles.jsonl` に append する。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import dedup


def read_candidates(stdin) -> list[dict]:
    out: list[dict] = []
    for line in stdin:
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def append_records(path: Path, records: list[dict]) -> None:
    if not records:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    prefix = ""
    if path.exists() and path.read_text(encoding="utf-8-sig").strip():
        prefix = "\n"
    payload = "\n".join(json.dumps(r, ensure_ascii=False) for r in records)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(prefix + payload)


def filter_records(
    candidates: list[dict],
    existing: list[dict],
    *,
    window_hours: float,
    title_threshold: float,
    followup_gate: bool,
    freshness_gate: bool,
    max_source_age_days: int,
) -> tuple[list[dict], list[dict]]:
    return dedup.dedup_candidates(
        candidates,
        existing,
        window_hours=window_hours,
        title_threshold=title_threshold,
        followup_gate=followup_gate,
        freshness_gate=freshness_gate,
        max_source_age_days=max_source_age_days,
    )


def main() -> int:
    p = argparse.ArgumentParser(description="dedup 通過後の記事だけ articles.jsonl に追記")
    p.add_argument("--jsonl", default="data/articles.jsonl",
                   help="追記先 articles.jsonl（既定: data/articles.jsonl）")
    p.add_argument("--window-hours", type=float, default=24.0)
    p.add_argument("--title-threshold", type=float, default=dedup.DEFAULT_TITLE_THRESHOLD)
    p.add_argument("--followup-gate", action="store_true", default=True,
                   help="続報候補の新材料ゲートを有効化（既定: 有効）")
    p.add_argument("--no-followup-gate", dest="followup_gate", action="store_false",
                   help="保守用途。続報候補の新材料ゲートを無効化")
    p.add_argument("--freshness-gate", action="store_true", default=True,
                   help="URL 発行日ベースの鮮度ゲートを有効化（既定: 有効）")
    p.add_argument("--no-freshness-gate", dest="freshness_gate", action="store_false",
                   help="保守用途。鮮度ゲートを無効化")
    p.add_argument("--max-source-age-days", type=int, default=dedup.DEFAULT_MAX_SOURCE_AGE_DAYS)
    args = p.parse_args()

    jsonl_path = Path(args.jsonl)
    candidates = read_candidates(sys.stdin)
    existing = dedup.load_existing(jsonl_path)
    passed, dropped = filter_records(
        candidates,
        existing,
        window_hours=args.window_hours,
        title_threshold=args.title_threshold,
        followup_gate=args.followup_gate,
        freshness_gate=args.freshness_gate,
        max_source_age_days=args.max_source_age_days,
    )
    append_records(jsonl_path, passed)

    for r in passed:
        print(json.dumps(r, ensure_ascii=False))
    print(
        f"append_after_dedup: appended {len(passed)}, dropped {len(dropped)} "
        f"to {jsonl_path}",
        file=sys.stderr,
    )
    for r in dropped:
        print(f"  DROP: {r.get('title', '')[:60]} | {r.get('dedup_reason', '')}",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
