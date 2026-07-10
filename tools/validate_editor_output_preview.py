#!/usr/bin/env python3
"""Editor の構造化出力を materialize 前に semantic 検証する。"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from tools.validate_summary_reflection import validate_summary_reflection

_BLOCK_MARKERS = ("⛔", "処理中断", "生成前に中断", "編集規約違反")


def validate_editor_output_preview(path: Path, *, issue_date: str) -> list[str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"editor preview JSON invalid: {exc}"]

    errors: list[str] = []
    if str(payload.get("issue_date") or "") != issue_date:
        errors.append(f"editor preview issue_date mismatch: expected={issue_date}")

    summary = str(payload.get("summary_markdown") or "")
    if any(marker in summary for marker in _BLOCK_MARKERS):
        errors.append("editor summary contains abort/block marker")
    with tempfile.TemporaryDirectory(prefix="news-grasp-editor-preview-") as tmp:
        summary_path = Path(tmp) / f"{issue_date}.md"
        summary_path.write_text(summary, encoding="utf-8", newline="\n")
        errors.extend(validate_summary_reflection(summary_path))

    records = payload.get("append_records") or []
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            errors.append(f"append_records[{index}] is not an object")
            continue
        if str(record.get("date") or "") != issue_date:
            errors.append(f"append_records[{index}] date mismatch")
        url = str(record.get("url") or "")
        if ".invalid/" in url or url.endswith(".invalid"):
            errors.append(f"append_records[{index}] uses reserved .invalid URL")
        joined = " ".join(str(record.get(key) or "") for key in ("title", "title_ja", "summary"))
        if any(marker in joined for marker in _BLOCK_MARKERS):
            errors.append(f"append_records[{index}] contains abort/block marker")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("preview", type=Path)
    parser.add_argument("--date", required=True)
    args = parser.parse_args(argv)
    errors = validate_editor_output_preview(args.preview, issue_date=args.date)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"PASS: editor output preview semantic validation OK ({args.preview})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
