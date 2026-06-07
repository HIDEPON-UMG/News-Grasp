#!/usr/bin/env python3
"""Summary digest の reflection 欠落を公開前に検出する gate。"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from tools.generate_pages import parse_reflection

_DATE_FILE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.md$")


def find_latest_summary(summary_dir: Path) -> Path:
    """digest/Summary 内の最新 YYYY-MM-DD.md を返す。"""
    candidates = sorted(p for p in summary_dir.glob("*.md") if _DATE_FILE_RE.match(p.name))
    if not candidates:
        raise FileNotFoundError(f"Summary digest が見つかりません: {summary_dir}")
    return candidates[-1]


def validate_summary_reflection(path: Path) -> list[str]:
    """Summary digest に LP 用 reflection があるか検査し、エラー一覧を返す。"""
    if not path.exists():
        return [f"Summary digest が存在しません: {path}"]
    body = path.read_text(encoding="utf-8-sig", errors="replace")
    reflection = parse_reflection(body)
    lead = reflection.get("lead") or ""
    sections = reflection.get("sections") or {}
    if lead or sections:
        return []
    return [
        f"{path}: reflection が空です。",
        "期待形式: `## § 本日のテーマ考察` 直下に lead blockquote、または `### §01 ...` セクションを出力してください。",
        "このままでは LP の TODAY'S THEME / 本日のテーマ考察が空欄またはフォールバックになります。",
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="最新または指定日の Summary digest に reflection があることを検査します。",
    )
    parser.add_argument(
        "--summary-dir",
        type=Path,
        default=Path("digest") / "Summary",
        help="Summary digest ディレクトリ (default: digest/Summary)",
    )
    parser.add_argument(
        "--date",
        help="検査対象日 YYYY-MM-DD。未指定なら最新 Summary を検査します。",
    )
    args = parser.parse_args(argv)

    target = args.summary_dir / f"{args.date}.md" if args.date else find_latest_summary(args.summary_dir)
    errs = validate_summary_reflection(target)
    if errs:
        for err in errs:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1

    print(f"PASS: summary reflection OK ({target})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
