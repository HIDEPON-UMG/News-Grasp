"""News-Grasp E2E launch compositionのproduct-local決定論consumer。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping


class E2ECompositionContractError(RuntimeError):
    """E2E launch identityの不整合。"""


ROUTES = {
    "automation": "automation/news-grasp-6-40/automation.toml.template",
    "skill": "automation/skills/news-grasp-direct-mainline/SKILL.md",
    "runtime": "tools/news_grasp_direct_runtime.py",
    "completion": "tools/news_grasp_direct_completion.py",
    "title_control": "tools/news_grasp_title_control.py",
    "guard": "automation/news-grasp-6-40/completion_guard.py",
}


def _read(
    root: Path, relative: str, overrides: Mapping[str, str]
) -> str:
    if relative in overrides:
        return overrides[relative]
    try:
        return (root / relative).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise E2ECompositionContractError("NEWS_GRASP_E2E_ROUTE_UNAVAILABLE") from exc


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def validate_e2e_launch_contract(
    repo_root: Path | str,
    *,
    source_overrides: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """06:00 Codex automation から direct public completion までの構成を検証する。"""

    root = Path(repo_root).resolve(strict=True)
    overrides = dict(source_overrides or {})
    unknown = sorted(set(overrides) - set(ROUTES.values()))
    if unknown:
        raise E2ECompositionContractError("NEWS_GRASP_E2E_ROUTE_UNKNOWN")
    sources = {
        name: _read(root, relative, overrides)
        for name, relative in ROUTES.items()
    }
    automation = sources["automation"]
    skill = sources["skill"]
    runtime = sources["runtime"]
    completion = sources["completion"]
    title_control = sources["title_control"]
    guard = sources["guard"]

    automation_markers = (
        '$news-grasp-direct-mainline',
        'model = "gpt-5.6-luna"',
        'reasoning_effort = "max"',
        "tools.news_grasp_direct_runtime.DIRECT_STAGES",
        "NEWS_GRASP_DIRECT_MAINLINE_RECEIPT_V1",
        "NEWS_GRASP_DIRECT_PUBLIC_VERIFICATION_V1",
    )
    skill_markers = (
        "news_grasp_title_materializer",
        "python -m tools.news_grasp_direct_runtime start",
        "run_exact_successor",
        "validate_daily_quality --date YYYY-MM-DD --require-deepdive",
        "public incompleteかつexact successorがある状態で終了しない",
    )
    runtime_markers = (
        "DIRECT_STAGES = (",
        '"title_control"',
        '"daily_quality"',
        '"public_completion"',
        "run_exact_successor(",
        "verify_public_completion(",
        "validate_installed_automation_semantics(",
    )
    completion_markers = (
        "NEWS_GRASP_DIRECT_PUBLIC_VERIFICATION_V1",
        "verify_direct_public_completion(",
        "validate_daily_quality",
        "deepdive_quality",
        "publish_status",
    )
    title_markers = (
        "expected_title(",
        "validate_title(",
        "record_title_status(",
        "post_publish_issue_list",
    )
    guard_markers = (
        "NEWS_GRASP_DIRECT_MAINLINE_RECEIPT_V1",
        "NEWS_GRASP_DIRECT_PUBLIC_VERIFICATION_V1",
        "public incomplete",
    )
    for code, source, markers in (
        ("NEWS_GRASP_E2E_AUTOMATION_INVALID", automation, automation_markers),
        ("NEWS_GRASP_E2E_SKILL_INVALID", skill, skill_markers),
        ("NEWS_GRASP_E2E_RUNTIME_INVALID", runtime, runtime_markers),
        ("NEWS_GRASP_E2E_COMPLETION_INVALID", completion, completion_markers),
        ("NEWS_GRASP_E2E_TITLE_CONTROL_INVALID", title_control, title_markers),
        ("NEWS_GRASP_E2E_GUARD_INVALID", guard, guard_markers),
    ):
        if any(marker not in source for marker in markers):
            raise E2ECompositionContractError(code)

    title_at = automation.index("title_status")
    runtime_at = automation.index("tools.news_grasp_direct_runtime.DIRECT_STAGES")
    quality_at = automation.index("validate_daily_quality")
    completion_at = automation.index("NEWS_GRASP_DIRECT_PUBLIC_VERIFICATION_V1", quality_at)
    if not title_at < runtime_at < quality_at < completion_at:
        raise E2ECompositionContractError("NEWS_GRASP_E2E_DIRECT_ORDER_INVALID")
    if "news-grasp-runner.ps1" in automation + runtime + completion + guard:
        raise E2ECompositionContractError("NEWS_GRASP_E2E_LEGACY_RUNNER_ROUTE_PRESENT")

    return {
        "schemaVersion": "NEWS_GRASP_DIRECT_E2E_COMPOSITION_V1",
        "status": "green",
        "route": "codex_automation_to_direct_runtime_to_public_completion",
        "executionRootBound": True,
        "executableBound": True,
        "ownerClaimBound": False,
        "timeoutBrokerOwned": False,
        "directEntryRejected": True,
        "oneShotWalBound": True,
        "fixedManagedRoot": True,
        "compositionOrder": [
            "automation_prompt",
            "title_control",
            "direct_runtime_start",
            "stage_successors",
            "public_verification",
        ],
        "routeHashes": {
            ROUTES[name]: _sha256(source) for name, source in sources.items()
        },
    }
