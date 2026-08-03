from __future__ import annotations

from tools.repair_coverage_matrix import (
    RepairClass,
    RepairIssue,
    _issue_code_from_text,
    classify_repair_issue,
)


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
    assert decision.status_on_failure == "blocked_deterministic_repair_failed"


def test_summary_news_headline_issue_routes_to_typed_rewrite() -> None:
    decision = classify_repair_issue(
        RepairIssue(
            gate_id="generation-quality",
            issue_code="summary_news_headline_invalid",
            message="hero_headline は複数の独立ニュースを接合しない",
            artifact_paths=("digest/Summary/2026-08-03.md",),
            issue_date="2026-08-03",
            category="summary",
            raw_output="summary_news_headline_invalid",
        )
    )

    assert decision.repair_class == RepairClass.LLM_REWRITE_EXISTING_ARTIFACT
    assert decision.handler_id == "summary-headline-rewrite"
    assert decision.verify_gate == "generation-quality"
    assert decision.status_on_failure == "blocked_summary_headline_rewrite_failed"


def test_repair_text_classifier_does_not_route_field_name_only() -> None:
    assert _issue_code_from_text(
        "generation-quality",
        "debug: hero_headline renderer field present",
    ) == "unknown"


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
