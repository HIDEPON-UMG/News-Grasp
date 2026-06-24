#!/usr/bin/env python3
"""News-Grasp daily batch の token / duration SLO gate。"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def validate_usage_log(
    usage_log: Path,
    *,
    max_total_tokens: int,
    max_window_sec: int,
    since: str | None = None,
) -> list[str]:
    errors: list[str] = []
    if not usage_log.exists():
        return [f"SLO usage log missing: {usage_log}"]

    since_timestamp = _parse_timestamp(since)
    if since and since_timestamp is None:
        errors.append(f"SLO usage log has invalid since timestamp: {since!r}")

    total_tokens = 0
    timestamps: list[datetime] = []
    malformed = 0
    for line_no, line in enumerate(usage_log.read_text(encoding="utf-8-sig", errors="replace").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        timestamp = _parse_timestamp(record.get("timestamp"))
        if since_timestamp is not None and timestamp is not None and timestamp < since_timestamp:
            continue
        try:
            total_tokens += int(record.get("tokens_used") or 0)
        except (TypeError, ValueError):
            errors.append(f"SLO usage log has invalid tokens_used at line {line_no}: {record.get('tokens_used')!r}")
        if timestamp is not None:
            timestamps.append(timestamp)

    if malformed:
        errors.append(f"SLO usage log has malformed JSON lines: {malformed}")
    if total_tokens > max_total_tokens:
        errors.append(f"SLO token budget exceeded: total_tokens={total_tokens} limit={max_total_tokens}")
    if len(timestamps) >= 2:
        window_sec = int((max(timestamps) - min(timestamps)).total_seconds())
        if window_sec > max_window_sec:
            errors.append(f"SLO duration budget exceeded: window_sec={window_sec} limit={max_window_sec}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate News-Grasp daily batch token/duration SLO.")
    parser.add_argument("--usage-log", type=Path, required=True)
    parser.add_argument("--max-total-tokens", type=int, default=3_000_000)
    parser.add_argument("--max-window-sec", type=int, default=3600)
    parser.add_argument("--since", default=None, help="この timestamp より前の過去試行を SLO 窓から除外する。")
    args = parser.parse_args(argv)

    errors = validate_usage_log(
        args.usage_log,
        max_total_tokens=args.max_total_tokens,
        max_window_sec=args.max_window_sec,
        since=args.since,
    )
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print("batch SLO OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
