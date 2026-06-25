from __future__ import annotations

from tools.repair_coverage_matrix import RepairClass, RepairIssue, classify_repair_issue


def test_deterministic_issue_routes_to_handler_metadata() -> None:
    decision = classify_repair_issue(
        RepairIssue(
            gate_id="generation-quality",
            issue_code="summary_hero_missing",
            message="Summary hero_left/hero_right is missing",
            artifact_paths=("digest/Summary/2026-06-25.md",),
            issue_date="2026-06-25",
            category="summary",
            raw_output="summary_hero_missing",
        )
    )

    assert decision.repair_class == RepairClass.DETERMINISTIC_HANDLER
    assert decision.handler_id == "summary-hero-patch"
    assert decision.allowed_artifacts == ("digest/Summary/{date}.md",)
    assert decision.verify_gate == "generation-quality"
    assert decision.status_on_failure == "blocked_repair_handler_unimplemented"


def test_llm_missing_artifact_rejects_partial_existing_artifacts() -> None:
    decision = classify_repair_issue(
        RepairIssue(
            gate_id="generation-quality",
            issue_code="missing_artifact",
            message="required generated artifact is missing",
            artifact_paths=("digest/Summary/2026-06-25.md", "digest/DeepDive/2026-06-25.md"),
            issue_date="2026-06-25",
            category="generated",
            raw_output="missing_artifact",
            existing_artifacts=("digest/Summary/2026-06-25.md",),
            evidence={"typed_reason": "missing_artifact"},
        )
    )

    assert decision.repair_class == RepairClass.TYPED_FATAL
    assert decision.status_on_failure == "blocked_existing_artifact_llm_recreate"


def test_llm_missing_artifact_requires_typed_missing_reason() -> None:
    decision = classify_repair_issue(
        RepairIssue(
            gate_id="generation-quality",
            issue_code="missing_artifact",
            message="required generated artifact is missing",
            artifact_paths=("digest/DeepDive/2026-06-25.md",),
            issue_date="2026-06-25",
            category="generated",
            raw_output="missing_artifact",
        )
    )

    assert decision.repair_class == RepairClass.TYPED_FATAL
    assert decision.status_on_failure == "blocked_missing_artifact_reason_required"


def test_typed_external_without_evidence_is_blocked() -> None:
    decision = classify_repair_issue(
        RepairIssue(
            gate_id="youtube-podcast",
            issue_code="youtube_quota_or_permission",
            message="403 quota exceeded",
            artifact_paths=("build/youtube-podcast/uploads.json",),
            issue_date="2026-06-25",
            category="podcast",
            raw_output="403 quota exceeded",
        )
    )

    assert decision.repair_class == RepairClass.TYPED_FATAL
    assert decision.status_on_failure == "blocked_external_evidence_missing"
