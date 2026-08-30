from __future__ import annotations

from dataclasses import replace

from tools.repair_coverage_matrix import COVERAGE_ROWS, CoverageRow, RepairClass
from tools.repair_registry import REGISTRY, RepairContext, RepairHandler, RepairResult
from tools.repair_system_completeness import (
    REPAIR_VALIDATOR_CODESETS,
    audit_repair_system,
    extract_generation_quality_codes,
    extract_registry_repair_entrypoints,
)


def _finding_codes(report) -> set[str]:
    return {finding.code for finding in report.findings}


def _synthetic_handler(handler_id: str, *, verify_gates: tuple[str, ...]) -> RepairHandler:
    def repair(ctx: RepairContext) -> RepairResult:
        return RepairResult(ctx.handler_id, "repaired", True, tuple(ctx.artifacts))

    return RepairHandler(
        handler_id=handler_id,
        kind="deterministic",
        allowed_artifacts=("data/articles.jsonl",),
        verify_gate=verify_gates[0],
        supported_verify_gates=verify_gates,
        repair=repair,
    )


def test_current_repair_system_is_closed_world() -> None:
    report = audit_repair_system()

    assert report.ok, "\n".join(
        f"{finding.code}: {finding.detail}" for finding in report.findings
    )
    assert report.coverage_row_count == len(COVERAGE_ROWS)
    assert report.registry_handler_count == len(REGISTRY)
    assert report.validator_issue_count == sum(
        len(codes) for codes in REPAIR_VALIDATOR_CODESETS.values()
    )
    assert {
        "tools/repair_system_completeness.py",
        "docs/spec.md",
        "tests/test_repair_system_completeness.py",
        "tests/test_repair_matrix_validator_sync.py",
        "tests/test_repair_registry.py",
        "tests/test_auto_repair_orchestrator.py",
        "tests/test_news_grasp_direct_runtime.py",
        "tests/test_historical_failure_scenarios.py",
        "tools/news_grasp_direct_runtime.py",
        "automation/news-grasp-6-40/completion_guard.py",
        "automation/skills/news-grasp-direct-mainline/SKILL.md",
    } <= report.source_observations.keys()
    assert all(
        item["status"] == "present" and item["bytes"] > 0
        for item in report.source_observations.values()
    )


def test_generation_issue_codes_are_extracted_from_validator_source() -> None:
    source = """
def validate(flag):
    errors = [_error("literal_code", "artifact", reason="x", expected="y", actual="z")]
    errors.append(
        _error(
            "recoverable_code" if flag else "fatal_code",
            "artifact",
            reason="x",
            expected="y",
            actual="z",
        )
    )
    return errors
"""

    assert extract_generation_quality_codes(source) == {
        "literal_code",
        "recoverable_code",
        "fatal_code",
    }


def test_registry_repair_entrypoints_are_extracted_by_typed_signature() -> None:
    source = """
def _repair_real(ctx: RepairContext) -> RepairResult:
    return RepairResult(ctx.handler_id, "repaired", True)

def _repair_helper(ctx: RepairContext, field: str) -> RepairResult:
    return RepairResult(ctx.handler_id, "repaired", True)

def _repair_tuple(ctx: RepairContext) -> tuple[list[str], str]:
    return [], ""
"""

    assert extract_registry_repair_entrypoints(source) == {"_repair_real"}


def test_closed_world_audit_rejects_duplicate_and_missing_validator_rows() -> None:
    rows = (*COVERAGE_ROWS, COVERAGE_ROWS[0])
    validator_codes = {
        **REPAIR_VALIDATOR_CODESETS,
        "generation-quality": {
            *REPAIR_VALIDATOR_CODESETS["generation-quality"],
            "brand_new_unrouted_code",
        },
    }

    report = audit_repair_system(rows=rows, validator_codes=validator_codes)

    assert {
        "duplicate_coverage_row",
        "validator_issue_missing_coverage",
    } <= _finding_codes(report)


def test_closed_world_audit_rejects_verify_gate_drift_and_dead_registry_handler() -> None:
    target_index = next(
        index
        for index, row in enumerate(COVERAGE_ROWS)
        if row.repair_class == RepairClass.DETERMINISTIC_HANDLER
    )
    rows = list(COVERAGE_ROWS)
    rows[target_index] = replace(rows[target_index], verify_gate="unowned-gate")
    registry = {
        **REGISTRY,
        "dead-handler": _synthetic_handler(
            "dead-handler",
            verify_gates=("daily-quality",),
        ),
    }

    report = audit_repair_system(rows=tuple(rows), registry=registry)

    assert {
        "handler_verify_gate_mismatch",
        "registry_handler_unreachable",
    } <= _finding_codes(report)


def test_closed_world_audit_rejects_ambiguous_and_scope_incapable_handler() -> None:
    target = next(
        row
        for row in COVERAGE_ROWS
        if row.repair_class == RepairClass.DETERMINISTIC_HANDLER
        and row.allowed_artifacts
    )

    def _blocked_ambiguous(ctx: RepairContext) -> RepairResult:
        return RepairResult(ctx.handler_id, "blocked_ambiguous_repair", False)

    registry = {
        **REGISTRY,
        target.handler_id: RepairHandler(
            handler_id=target.handler_id,
            kind="deterministic",
            allowed_artifacts=("unrelated/path",),
            verify_gate=target.verify_gate or target.gate_id,
            supported_verify_gates=(target.verify_gate or target.gate_id,),
            repair=_blocked_ambiguous,
        ),
    }

    report = audit_repair_system(registry=registry)

    assert {
        "deterministic_handler_ambiguous",
        "handler_artifact_scope_mismatch",
    } <= _finding_codes(report)


def test_closed_world_audit_rejects_repairable_unknown_fallback() -> None:
    unknown_index = next(
        index
        for index, row in enumerate(COVERAGE_ROWS)
        if (row.gate_id, row.issue_code) == ("any", "unknown")
    )
    rows = list(COVERAGE_ROWS)
    rows[unknown_index] = CoverageRow(
        "any",
        "unknown",
        RepairClass.DETERMINISTIC_HANDLER,
        "url-quarantine-refill",
        ("data/articles.jsonl",),
        "daily-quality",
        "blocked_unknown_repair_class",
    )

    report = audit_repair_system(rows=tuple(rows))

    assert "unknown_route_not_fail_closed" in _finding_codes(report)
