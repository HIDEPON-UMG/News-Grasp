#!/usr/bin/env python3
"""Newsroom E2E 前の no-Codex 実装完全性チェック。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REQUIRED_FILES = [
    "schemas/reporter_fanout_return.schema.json",
    "schemas/reporter_records.schema.json",
    "schemas/editor_summary.schema.json",
    "prompts/newsroom-reporter-system.md",
    "prompts/newsroom-editor-system.md",
    "tools/verify_reporter_output.py",
    "tools/fetch_article_body.py",
]


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _iter_refs(node) -> list[str]:
    refs: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                refs.append(value)
            else:
                refs.extend(_iter_refs(value))
    elif isinstance(node, list):
        for item in node:
            refs.extend(_iter_refs(item))
    return refs


def run(repo_root: Path) -> list[str]:
    errors: list[str] = []
    for rel in REQUIRED_FILES:
        if not (repo_root / rel).exists():
            errors.append(f"missing required file: {rel}")

    if errors:
        return errors

    reporter_schema = _load_json(repo_root / "schemas/reporter_fanout_return.schema.json")
    editor_schema = _load_json(repo_root / "schemas/editor_summary.schema.json")
    reporter_prompt = (repo_root / "prompts/newsroom-reporter-system.md").read_text(encoding="utf-8-sig")
    editor_prompt = (repo_root / "prompts/newsroom-editor-system.md").read_text(encoding="utf-8-sig")
    runner_path = Path.home() / "bin" / "news-grasp-runner.ps1"
    runner = runner_path.read_text(encoding="utf-8-sig") if runner_path.exists() else ""

    reporter_required = set(reporter_schema.get("required", []))
    for key in ["category", "issue_date", "records_file", "digest_file", "search_audit", "selected_count", "titles"]:
        if key not in reporter_required:
            errors.append(f"reporter fanout schema missing required key: {key}")
    reporter_props = set(reporter_schema.get("properties", {}))
    if reporter_props and reporter_required != reporter_props:
        missing = sorted(reporter_props - reporter_required)
        errors.append(f"reporter fanout schema must require all properties for Codex output schema: {missing}")

    source_policy = (
        editor_schema.get("properties", {})
        .get("inputs", {})
        .get("properties", {})
        .get("source_policy", {})
        .get("enum", [])
    )
    if "no_recollection" not in source_policy:
        errors.append("editor schema must require source_policy=no_recollection")
    external_refs = [ref for ref in _iter_refs(editor_schema) if not ref.startswith("#/")]
    if external_refs:
        errors.append(f"editor schema must not use external refs for Codex output schema: {external_refs}")
    editor_required = set(editor_schema.get("required", []))
    editor_props = set(editor_schema.get("properties", {}))
    if editor_props and editor_required != editor_props:
        missing = sorted(editor_props - editor_required)
        errors.append(f"editor schema must require all properties for Codex output schema: {missing}")

    prompt_needles = [
        "schemas/reporter_fanout_return.schema.json",
        "コンパクト JSON",
        "フル record・記事本文・digest md 本文",
    ]
    for needle in prompt_needles:
        if needle not in reporter_prompt:
            errors.append(f"reporter prompt missing compact-return contract: {needle}")

    for needle in ["editor-input-manifest", "source_policy", "no_recollection", "schemas/editor_summary.schema.json"]:
        if needle not in editor_prompt:
            errors.append(f"editor prompt missing artifact integration contract: {needle}")

    if runner:
        if "`$cat" in runner or "`$catDedupFile" in runner or "`schemas" in runner:
            errors.append("runner reporter prompt contains PowerShell backtick escapes that break generated prompt variables")
        if 'Join-Path $RepoDir "build\\codex-usage\\$DateStamp.jsonl"' not in runner:
            errors.append("runner must build CodexUsageLog after DateStamp with dated jsonl path")

    return errors


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    p = argparse.ArgumentParser(description="Newsroom E2E 前 preflight")
    p.add_argument("--repo-root", default=".")
    args = p.parse_args()
    repo_root = Path(args.repo_root).resolve()
    errors = run(repo_root)
    payload = {"ok": not errors, "errors": errors}
    print(json.dumps(payload, ensure_ascii=False))
    if errors:
        for err in errors:
            print(f"PREFLIGHT FAIL: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
