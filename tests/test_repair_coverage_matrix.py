from __future__ import annotations

import json

import pytest

from tools.repair_coverage_matrix import (
    RepairClass,
    RepairIssue,
    classify_gate_output,
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
    ("public-surface", "public_sentinel_missing"),
    ("public-surface", "distribution_manifest_invalid"),
    ("deepdive-required", "published_docs_missing"),
    ("youtube-podcast", "oauth_invalid_grant"),
    ("youtube-podcast", "youtube_quota_or_permission"),
    ("github-pages", "deploy_workflow_not_success"),
    ("google-api", "google_api_external"),
    ("deploy", "deploy_surface_regression"),
    ("deploy", "deploy_surface_unrelated_red"),
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


def test_generation_quality_multi_issue_prioritizes_state_consistency_before_audio() -> None:
    output = json.dumps(
        {
            "ok": False,
            "errors": [
                {"code": "audio_script_quality_invalid", "artifact": "digest/Summary/2026-06-28-audio-script.md"},
                {"code": "articles_issue_empty", "artifact": "data/articles.jsonl"},
                {"code": "digest_article_url_mismatch", "artifact": "digest/AI/2026-06-28-AI.md"},
            ],
        },
        ensure_ascii=False,
    )

    decision = classify_gate_output("generation-quality", output)

    assert decision.issue_code == "articles_issue_empty"
    assert decision.handler_id == "digest-articles-reconcile-patch"
    assert decision.repair_class == RepairClass.DETERMINISTIC_HANDLER


def test_google_api_external_is_typed_external_not_green() -> None:
    decision = classify_repair_issue(
        RepairIssue(
            gate_id="google-api",
            issue_code="google_api_external",
            evidence={
                "external_system": "google-api",
                "external_kind": "google_api_external",
                "observed_error_code": "403",
                "source_command": "python -m tools.verify_public_surface",
                "detail": "Google API returned 403",
                "observed_at": "2026-06-26T00:00:00Z",
            },
        )
    )

    assert decision.repair_class == RepairClass.TYPED_EXTERNAL
    assert decision.status_on_failure == "blocked_external_readiness"


def test_deploy_surface_unrelated_red_is_fatal_no_rollback() -> None:
    decision = classify_repair_issue(
        RepairIssue(
            gate_id="deploy",
            issue_code="deploy_surface_unrelated_red",
            evidence={"detail": "red surface was not touched by candidate"},
        )
    )

    assert decision.repair_class == RepairClass.TYPED_FATAL
    assert decision.status_on_failure == "deploy_surface_unrelated_red"
