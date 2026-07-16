#!/usr/bin/env python3
"""廃止モデル参照を本番依存と履歴証跡へ分類する。"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Iterable


RETIRED_MODEL_RE = re.compile(
    r"gpt-5\.4(?:-[a-z0-9.-]+)?|gpt-5\.6(?:-|\s+)terra",
    re.IGNORECASE,
)

PRODUCTION_PATHS = {
    "tools/model_policy.py",
    "tools/newsroom_preflight.py",
    "tools/project_daily_model_costs.py",
    "tools/judge_model_benchmark.py",
    "tools/judge_deepdive_triad.py",
    "scripts/ops/news-grasp-runner.ps1",
    "scripts/ops/run_codex_with_timeout.ps1",
    "scripts/ops/install-news-grasp-ops.ps1",
    "prompts/runner-prompt.md",
    "prompts/newsroom-editor-system.md",
}

BENCHMARK_HISTORY_PATTERNS = (
    re.compile(r"^(?:build|_ops)/"),
    re.compile(r"^tools/run_.*benchmark.*\.py$"),
    re.compile(r"^tools/run_model_eval\.py$"),
    re.compile(r"^tools/build_.*report.*\.py$"),
    re.compile(r"^tests/test_.*benchmark.*\.py$"),
    re.compile(r"^tests/test_model_eval.*\.py$"),
    re.compile(r"^tests/test_luna_high_replacement_report\.py$"),
    re.compile(r"^tests/test_external_benchmark_matrix\.py$"),
    re.compile(r"^tests/test_codex_recovery_benchmark\.py$"),
    re.compile(r"^tests/test_deepdive_terra_benchmark\.py$"),
    re.compile(r"^tests/test_model_selection_html_report\.py$"),
    re.compile(r"^tests/test_complete_codex_migration_contract\.py$"),
    re.compile(r"^tests/test_model_policy_and_eval\.py$"),
    re.compile(r"^tests/test_model_judge_policy\.py$"),
    re.compile(r"^tests/test_newsroom_preflight\.py$"),
    re.compile(r"^tests/test_product_spec_contract\.py$"),
    re.compile(r"^tests/test_runtime_model_dependency_audit\.py$"),
    re.compile(r"^prompts/runner-prompt-(?:legacy|\d{4}-\d{2}-\d{2}-backfill)\.md$"),
    re.compile(r"^tasks/reviews/"),
)

CONTENT_EVIDENCE_PATTERNS = (
    re.compile(r"^(?:data|digest|docs)/"),
    re.compile(r"^tools/date_evidence\.py$"),
    re.compile(r"^tests/test_date_evidence\.py$"),
)

TEXT_SUFFIXES = {
    ".py", ".ps1", ".md", ".json", ".jsonl", ".html", ".txt",
    ".yaml", ".yml", ".toml", ".csv", ".js", ".ts",
}


def _candidate_paths(root: Path) -> Iterable[Path]:
    top_completed = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=root,
        capture_output=True,
        check=False,
    )
    git_top: Path | None = None
    if top_completed.returncode == 0:
        raw_top = top_completed.stdout.decode("utf-8", errors="replace").strip()
        if raw_top:
            git_top = Path(raw_top).resolve()
    completed = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard", "-z"],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if completed.returncode == 0 and git_top is not None:
        yielded = False
        for raw in completed.stdout.split(b"\0"):
            if raw:
                path = (git_top / raw.decode("utf-8", errors="replace")).resolve()
                try:
                    path.relative_to(root)
                except ValueError:
                    continue
                yielded = True
                yield path
        if yielded:
            return
    for path in root.rglob("*"):
        if path.is_file() and not any(part in {".git", ".venv", "__pycache__"} for part in path.parts):
            yield path


def _classification(relative: str) -> tuple[str, str]:
    if relative in PRODUCTION_PATHS:
        return "prohibited", "production/runtime path must not reference a retired model"
    if any(pattern.search(relative) for pattern in BENCHMARK_HISTORY_PATTERNS):
        return "allowed_benchmark_history", "explicit benchmark, historical comparison, or retirement contract evidence"
    if any(pattern.search(relative) for pattern in CONTENT_EVIDENCE_PATTERNS):
        return "allowed_content_evidence", "article, generated page, or dated evidence text"
    return "unknown", "retired model reference is outside the typed path inventory"


def audit_runtime_model_dependencies(repo_root: Path) -> dict[str, Any]:
    root = repo_root.resolve()
    report: dict[str, Any] = {
        "status": "pass",
        "prohibited": [],
        "unknown": [],
        "allowed_benchmark_history": [],
        "allowed_content_evidence": [],
    }
    scanned = 0
    seen: set[str] = set()
    for path in _candidate_paths(root):
        try:
            relative = path.resolve().relative_to(root).as_posix()
        except (OSError, ValueError):
            continue
        if relative in seen or path.suffix.casefold() not in TEXT_SUFFIXES:
            continue
        seen.add(relative)
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        scanned += 1
        classification, reason = _classification(relative)
        for line_number, line in enumerate(text.splitlines(), start=1):
            for match in RETIRED_MODEL_RE.finditer(line):
                report[classification].append(
                    {
                        "path": relative,
                        "line": line_number,
                        "token": match.group(0),
                        "classification": classification,
                        "reason": reason,
                    }
                )
    report["scanned_files"] = scanned
    if report["prohibited"] or report["unknown"]:
        report["status"] = "fail"
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--format", choices=("json",), default="json")
    args = parser.parse_args(argv)
    report = audit_runtime_model_dependencies(args.repo_root)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
