#!/usr/bin/env python3
"""Codex usage と画像関連 artifact の圧迫度を集計する。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
IMAGE_TOKEN_KEYS = ("image_tokens", "input_image_tokens", "vision_tokens")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not path.exists():
        return records
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def _sum_tokens(records: list[dict[str, Any]], key: str) -> int:
    total = 0
    for record in records:
        value = record.get(key)
        if isinstance(value, int):
            total += value
    return total


def _count_image_artifacts(root: Path | None) -> int:
    if root is None or not root.exists():
        return 0
    return sum(1 for path in root.rglob("*") if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS)


def analyze_usage_pressure(
    e2e_worktrees_root: Path,
    date_stamp: str,
    *,
    ui_review_root: Path | None = None,
) -> dict[str, Any]:
    runs: list[dict[str, Any]] = []
    total_tokens = 0
    image_token_total = 0
    image_token_observed = False

    for worktree in sorted(path for path in e2e_worktrees_root.glob("*") if path.is_dir()):
        usage_dir = worktree / "build" / "codex-usage"
        main_usage = usage_dir / f"{date_stamp}.jsonl"
        records = _read_jsonl(main_usage)
        tokens_used = _sum_tokens(records, "tokens_used")
        run_image_tokens = 0
        for key in IMAGE_TOKEN_KEYS:
            key_total = _sum_tokens(records, key)
            if key_total:
                image_token_observed = True
                run_image_tokens += key_total
        duplicate_usage_files = sorted(usage_dir.glob(f"{date_stamp}.*.jsonl")) if usage_dir.exists() else []

        total_tokens += tokens_used
        image_token_total += run_image_tokens
        runs.append(
            {
                "worktree": worktree.name,
                "usage_log": str(main_usage),
                "tokens_used": tokens_used,
                "token_rows": sum(1 for record in records if isinstance(record.get("tokens_used"), int)),
                "ignored_duplicate_usage_files": len(duplicate_usage_files),
                "image_tokens": run_image_tokens if image_token_observed else None,
            }
        )

    image_artifact_count = _count_image_artifacts(ui_review_root)
    return {
        "date": date_stamp,
        "total_tokens_used": total_tokens,
        "image_token_total": image_token_total if image_token_observed else None,
        "image_token_accounting": "available" if image_token_observed else "unavailable",
        "image_artifact_count": image_artifact_count,
        "runs": runs,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Codex usage と画像関連 artifact の圧迫度を集計する。")
    parser.add_argument("--e2e-worktrees-root", type=Path, default=Path("build/e2e-worktrees"))
    parser.add_argument("--date", required=True)
    parser.add_argument("--ui-review-root", type=Path, default=Path("build/ui-review"))
    args = parser.parse_args(argv)

    report = analyze_usage_pressure(args.e2e_worktrees_root, args.date, ui_review_root=args.ui_review_root)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
