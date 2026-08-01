#!/usr/bin/env python3
"""Editor の構造化出力を materialize 前に semantic 検証する。"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

from tools.generate_pages import _SUMMARY_ROLE_TO_KEY
from tools.validate_summary_reflection import validate_summary_reflection

_BLOCK_MARKERS = ("⛔", "処理中断", "生成前に中断", "編集規約違反")
_CATEGORY_GENRES = {
    "fx": "FX",
    "ai": "AI",
    "it": "IT-Consulting",
    "mobility": "Mobility",
    "manufacturing": "Manufacturing",
    "economy": "Economy",
    "game": "Game",
}
_CANONICAL_LANE_LABELS = (
    ("fact", "事実・概要"),
    ("context", "背景・要点"),
    ("outlook", "影響・展望"),
)


def editor_preview_producer_contract() -> str:
    """実parserが読むlane記法を、editor向けの短い生成契約として返す。"""
    lines = [
        "EDITOR_PREVIEW_PRODUCER_CONTRACT_V1",
        "summary_markdown のテーマ直下と各 scheduled category section には、"
        "次の3行を行頭1文字目から正確に出力すること。",
    ]
    for key, label in _CANONICAL_LANE_LABELS:
        if _SUMMARY_ROLE_TO_KEY.get(label) != key:
            raise RuntimeError(f"summary lane parser contract drift: {label} -> {key}")
        lines.append(f"- 【{label}】：1文で記述する。")
    lines.extend([
        "各行の前に `+`、空白、引用記号を付けない。",
        "特に `+- 【事実・概要】：` は禁止。diff記法ではなく完成Markdownだけを返すこと。",
    ])
    return "\n".join(lines)


def _validate_lane_line_syntax(summary: str) -> list[str]:
    labels = "|".join(label for _key, label in _CANONICAL_LANE_LABELS)
    if not re.search(rf"(?m)^\+\-\s*【(?:{labels})】[:：]", summary):
        return []
    return [
        "editor summary lane line has invalid diff prefix '+-'. "
        "行頭を正確に `- 【事実・概要】：` / `- 【背景・要点】：` / "
        "`- 【影響・展望】：` とし、先頭の `+` を削除してください。"
    ]


def _resolve_from_preview(preview_path: Path, rel_or_abs: str) -> Path:
    candidate = Path(rel_or_abs)
    if candidate.is_absolute():
        return candidate
    parts = preview_path.resolve().parts
    if "build" in parts:
        build_index = parts.index("build")
        repo_root = Path(*parts[:build_index])
        repo_candidate = repo_root / candidate
        if repo_candidate.exists():
            return repo_candidate
    sibling_candidate = preview_path.parent / candidate.name
    if sibling_candidate.exists():
        return sibling_candidate
    return Path.cwd() / candidate


def _nonempty_jsonl(path: Path) -> bool:
    try:
        return any(line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines())
    except OSError:
        return False


def _validate_reporter_categories(payload: dict, preview_path: Path) -> list[str]:
    inputs = payload.get("inputs")
    if not isinstance(inputs, dict):
        return []
    manifest_path = ""
    for item in inputs.get("reporter_artifacts") or []:
        text = str(item)
        if text.endswith("editor-input-manifest.json"):
            manifest_path = text
            break
    if not manifest_path:
        return []

    manifest = _resolve_from_preview(preview_path, manifest_path)
    if not manifest.exists():
        return []
    try:
        manifest_payload = json.loads(manifest.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"editor reporter manifest invalid: {exc}"]

    append_genres = {
        str(record.get("genre") or "")
        for record in (payload.get("append_records") or [])
        if isinstance(record, dict)
    }
    errors: list[str] = []
    for category in manifest_payload.get("scheduled_categories") or []:
        category_id = str(category)
        expected_genre = _CATEGORY_GENRES.get(category_id)
        if not expected_genre:
            continue
        records_path = None
        for rel in manifest_payload.get("reporter_artifacts") or []:
            rel_text = str(rel).replace("\\", "/")
            if rel_text.endswith(f"/{category_id}.records.jsonl"):
                records_path = _resolve_from_preview(preview_path, str(rel))
                break
        if records_path is not None and _nonempty_jsonl(records_path) and expected_genre not in append_genres:
            errors.append(f"editor preview dropped nonempty reporter category: {category_id}")
    return errors


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
    errors.extend(_validate_lane_line_syntax(summary))
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
    errors.extend(_validate_reporter_categories(payload, path))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("preview", type=Path, nargs="?")
    parser.add_argument("--date")
    parser.add_argument("--print-producer-contract", action="store_true")
    args = parser.parse_args(argv)
    if args.print_producer_contract:
        print(editor_preview_producer_contract())
        return 0
    if args.preview is None or not args.date:
        parser.error("preview と --date は semantic validation で必須です")
    errors = validate_editor_output_preview(args.preview, issue_date=args.date)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"PASS: editor output preview semantic validation OK ({args.preview})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
