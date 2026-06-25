from __future__ import annotations

import pytest

from tools.repair_coverage_matrix import (
    RepairClass,
    RepairIssue,
    classify_repair_issue,
    missing_coverage,
    unimplemented_rows,
)


REQUIRED_ROWS = {
    ("daily-quality", "summary_reflection_emphasis_missing"),
    ("daily-quality", "category_card_emphasis_missing"),
    ("generation-quality", "audio_script_quality_invalid"),
    ("generation-quality", "summary_hero_missing"),
    ("generation-quality", "summary_reflection_missing"),
    ("generation-quality", "missing_artifact"),
    ("generation-quality", "date_evidence_source_missing"),
    ("generation-quality", "deepdive_structure_invalid"),
    ("digest-articles-reconcile", "digest_article_url_mismatch"),
    ("record-schema", "title_ja_missing"),
    ("record-schema", "thumb_invalid_or_missing"),
    ("url-liveness", "url_dead_or_stale"),
    ("public-html", "public_home_fallback"),
    ("deepdive-required", "published_docs_missing"),
    ("youtube-podcast", "youtube_quota_or_permission"),
    ("git-push", "remote_divergence"),
    ("any", "unknown"),
}


def test_repair_coverage_matrix_covers_required_rows_and_has_no_unimplemented() -> None:
    missing = missing_coverage(REQUIRED_ROWS)

    assert not missing, "\n".join(f"{gate_id}:{issue_code}" for gate_id, issue_code in missing)
    assert not unimplemented_rows(), "final Green requires zero handler_unimplemented_red rows"


def test_unknown_issue_never_defaults_to_repairable() -> None:
    decision = classify_repair_issue(
        RepairIssue(
            gate_id="daily-quality",
            issue_code="brand_new_validator_failure",
            message="unexpected validator output",
            artifact_paths=("digest/Summary/2026-06-25.md",),
            issue_date="2026-06-25",
            category="summary",
            raw_output="unexpected validator output",
        )
    )

    assert decision.repair_class == RepairClass.TYPED_FATAL
    assert decision.status_on_failure == "blocked_unknown_repair_class"
    assert decision.handler_id == ""


def test_typed_external_and_fatal_rows_have_evidence_contract() -> None:
    external = classify_repair_issue(
        RepairIssue(
            gate_id="youtube-podcast",
            issue_code="youtube_quota_or_permission",
            message="403 quota exceeded",
            artifact_paths=("build/youtube-podcast/uploads.json",),
            issue_date="2026-06-25",
            category="podcast",
            raw_output="403 quota exceeded",
            evidence={
                "external_system": "youtube",
                "external_kind": "quota",
                "observed_error_code": "403",
                "source_command": "python -m tools.tts.publish_youtube",
                "detail": "quota exceeded",
                "observed_at": "2026-06-25T08:00:00+09:00",
            },
        )
    )
    fatal = classify_repair_issue(
        RepairIssue(
            gate_id="git-push",
            issue_code="remote_divergence",
            message="non-fast-forward",
            artifact_paths=(),
            issue_date="2026-06-25",
            category="publish",
            raw_output="non-fast-forward",
            evidence={"external_system": "git", "external_kind": "repository_safety", "detail": "rejected"},
        )
    )

    assert external.repair_class == RepairClass.TYPED_EXTERNAL
    assert external.external_system == "youtube"
    assert external.external_kind == "quota"
    assert fatal.repair_class == RepairClass.TYPED_FATAL
    assert fatal.status_on_failure == "repository_safety_stop"


def test_repair_coverage_inventory_has_no_missing_known_validator_issue() -> None:
    missing = missing_coverage()

    assert not missing, "\n".join(f"{gate_id}:{issue_code}" for gate_id, issue_code in missing)
