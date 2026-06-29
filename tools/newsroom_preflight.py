#!/usr/bin/env python3
"""Newsroom E2E 前の no-Codex 実装完全性チェック。"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

from tools.config import CATEGORIES
from tools.model_policy import DEFAULT_MODEL_POLICY
from tools.publish_inventory import CATEGORY_ORDER, CATEGORY_PATHS, required_distribution_artifacts
from tools.refill_category_after_quarantine import refill_category_ids


REQUIRED_FILES = [
    "schemas/reporter_fanout_return.schema.json",
    "schemas/reporter_records.schema.json",
    "schemas/editor_summary.schema.json",
    "prompts/newsroom-reporter-system.md",
    "prompts/newsroom-editor-system.md",
    "tools/verify_reporter_output.py",
    "tools/fetch_article_body.py",
    "docs/sw.js",
    "docs/publish-status.json",
]

REQUIRED_MODEL_POLICY_KEYS = (
    ("reporter", "default"),
    ("editor", "default"),
    ("newsroom_editor", "default"),
    ("deepdive", "default"),
)

ALLOWED_PUBLISH_RESULTS = {"published_ok"}
SW_VERSION_RE = re.compile(r"\bSW_VERSION\s*=\s*['\"]([^'\"]+)['\"]")


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


def _non_summary_categories() -> list[str]:
    return [cat_id for cat_id in CATEGORIES if cat_id != "summary"]


def _audit_category_sources() -> list[str]:
    errors: list[str] = []
    canonical = _non_summary_categories()
    inventory_order = list(CATEGORY_ORDER)
    inventory_paths = list(CATEGORY_PATHS)
    refill_ids = list(refill_category_ids())
    if inventory_order != canonical:
        errors.append(
            "category source drift: publish_inventory.CATEGORY_ORDER "
            f"{inventory_order} != tools.config.CATEGORIES {canonical}"
        )
    if inventory_paths != canonical:
        errors.append(
            "category source drift: publish_inventory.CATEGORY_PATHS "
            f"{inventory_paths} != tools.config.CATEGORIES {canonical}"
        )
    if refill_ids != canonical:
        errors.append(
            "category source drift: refill category ids "
            f"{refill_ids} != tools.config.CATEGORIES {canonical}"
        )
    return errors


def _audit_model_policy() -> list[str]:
    errors: list[str] = []
    for role, key in REQUIRED_MODEL_POLICY_KEYS:
        policy = DEFAULT_MODEL_POLICY.get(role)
        if not isinstance(policy, dict):
            errors.append(f"model policy missing required role: {role}")
            continue
        value = policy.get(key)
        if value in (None, ""):
            errors.append(f"model policy missing required key: {role}.{key}")
    return errors


def _audit_sw_version(repo_root: Path) -> list[str]:
    text = (repo_root / "docs/sw.js").read_text(encoding="utf-8-sig")
    match = SW_VERSION_RE.search(text)
    if not match:
        return ["docs/sw.js missing SW_VERSION"]
    return []


def _audit_publish_status(repo_root: Path) -> list[str]:
    path = repo_root / "docs/publish-status.json"
    try:
        payload = _load_json(path)
    except json.JSONDecodeError as exc:
        return [f"publish-status invalid JSON: {exc.msg}"]
    result = payload.get("result")
    if result not in ALLOWED_PUBLISH_RESULTS:
        return [f"publish-status invalid result: {result!r}"]
    if not isinstance(payload.get("date"), str) or not payload.get("date"):
        return ["publish-status missing date"]
    return []


def _audit_distribution_inventory(issue_date: str) -> list[str]:
    artifacts = set(required_distribution_artifacts(issue_date))
    required = {
        "build/tts/latest_audio.json",
        f"build/youtube-podcast/{issue_date}.mp4",
        "build/youtube-podcast/uploads.json",
        "build/tts/deepdive/latest_audio.json",
        f"build/youtube-podcast-deepdive/{issue_date}.mp4",
        "build/youtube-podcast-deepdive/uploads.json",
        f"data/distribution/{issue_date}.json",
    }
    missing = sorted(required - artifacts)
    if missing:
        return [f"distribution inventory missing required sentinel: {missing}"]
    return []


def audit_source_of_truth_drift(repo_root: Path, issue_date: str) -> list[str]:
    errors: list[str] = []
    errors.extend(_audit_category_sources())
    errors.extend(_audit_model_policy())
    errors.extend(_audit_sw_version(repo_root))
    errors.extend(_audit_publish_status(repo_root))
    errors.extend(_audit_distribution_inventory(issue_date))
    return errors


def run(repo_root: Path, issue_date: str | None = None) -> list[str]:
    errors: list[str] = []
    issue_date = issue_date or date.today().isoformat()
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

    errors.extend(audit_source_of_truth_drift(repo_root, issue_date))
    return errors


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    p = argparse.ArgumentParser(description="Newsroom E2E 前 preflight")
    p.add_argument("--repo-root", default=".")
    p.add_argument("--date", default=None)
    args = p.parse_args()
    repo_root = Path(args.repo_root).resolve()
    errors = run(repo_root, issue_date=args.date)
    payload = {"ok": not errors, "errors": errors}
    print(json.dumps(payload, ensure_ascii=False))
    if errors:
        for err in errors:
            print(f"PREFLIGHT FAIL: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
