from __future__ import annotations

from tools.repair_coverage_matrix import COVERAGE_ROWS
from tools.repair_registry import AMBIGUOUS_STATUS, REGISTRY, RepairContext, repair_with_registry
from tools.validate_daily_quality import daily_quality_issue_code


GENERATION_QUALITY_ISSUES = {
    "articles_issue_empty",
    "articles_json_invalid",
    "audio_script_quality_invalid",
    "audio_script_validator_unavailable",
    "category_article_body_missing",
    "category_article_empty",
    "date_evidence_source_missing",
    "deepdive_structure_invalid",
    "deepdive_validator_unavailable",
    "digest_article_url_mismatch",
    "empty_artifact",
    "filename_date_mismatch",
    "frontmatter_only",
    "invalid_issue_date",
    "issue_date_mismatch",
    "manifest_error",
    "missing_artifact",
    "placeholder_digest",
    "summary_hero_missing",
    "summary_reflection_missing",
}


DAILY_QUALITY_MESSAGES = {
    "daily-quality": {
        "Summary hero_left is missing": "summary_hero_missing",
        "card #3 lacks required emphasis": "category_card_emphasis_missing",
        "Summary section lacks required emphasis": "summary_reflection_emphasis_missing",
        "thumbnail is missing": "thumb_invalid_or_missing",
        "published docs missing": "published_docs_missing",
        "audio script failed": "audio_script_quality_invalid",
        "search audit stale URL": "url_dead_or_stale",
        (
            r"data\search_audit\2026-07-22\ai.json: "
            "coverage_terms_checked missing required terms"
        ): "search_audit_metadata_missing",
        (
            r"data\articles.jsonl:1961 [AI]: follow-up matched_with URL date "
            "2026-05-20 is 63 day(s) older than issue 2026-07-22"
        ): "followup_review_required",
        "digest missing for category": "missing_artifact",
    }
}


def _matrix_keys() -> set[tuple[str, str]]:
    return {(row.gate_id, row.issue_code) for row in COVERAGE_ROWS}


def test_generation_quality_validator_codes_are_explicit_matrix_rows() -> None:
    missing = sorted(
        ("generation-quality", issue_code)
        for issue_code in GENERATION_QUALITY_ISSUES
        if ("generation-quality", issue_code) not in _matrix_keys()
    )

    assert not missing, "\n".join(
        f"gate_id={gate_id} issue_code={issue_code} source=tools/validate_generation_quality.py"
        for gate_id, issue_code in missing
    )


def test_daily_quality_issue_mapper_codes_are_explicit_matrix_rows() -> None:
    missing: list[tuple[str, str, str]] = []
    for gate_id, examples in DAILY_QUALITY_MESSAGES.items():
        for message, expected_code in examples.items():
            issue_code = daily_quality_issue_code(message)
            assert issue_code == expected_code
            if (gate_id, issue_code) not in _matrix_keys():
                missing.append((gate_id, issue_code, message))

    assert not missing, "\n".join(
        f"gate_id={gate_id} issue_code={issue_code} source=tools/validate_daily_quality.py message={message}"
        for gate_id, issue_code, message in missing
    )


def test_typed_external_requires_full_machine_evidence_contract() -> None:
    required = {
        "external_kind": "quota",
        "external_system": "youtube",
        "observed_error_code": "403",
        "source_command": "python -m tools.tts.publish_youtube",
        "detail": "quota exceeded",
        "observed_at": "2026-06-25T08:00:00+09:00",
    }

    for missing_key in required:
        evidence = {key: value for key, value in required.items() if key != missing_key}
        from tools.repair_coverage_matrix import RepairClass, RepairIssue, classify_repair_issue

        decision = classify_repair_issue(
            RepairIssue(
                gate_id="youtube-podcast",
                issue_code="youtube_quota_or_permission",
                message="403 quota exceeded",
                evidence=evidence,
            )
        )

        assert decision.repair_class == RepairClass.TYPED_FATAL
        assert decision.status_on_failure == "blocked_external_evidence_missing"
        assert missing_key in decision.reason


def test_registry_final_green_has_no_ambiguous_handler_results(tmp_path) -> None:
    failures: list[str] = []
    for handler_id, handler in REGISTRY.items():
        sample_artifact = handler.allowed_artifacts[0].replace("{date}", "2026-06-25").replace("{category}", "AI")
        result = repair_with_registry(
            RepairContext(
                repo_root=tmp_path,
                issue="2026-06-25",
                handler_id=handler_id,
                artifacts=[sample_artifact],
            )
        )
        if result.status == AMBIGUOUS_STATUS:
            failures.append(handler_id)

    assert not failures, "blocked_ambiguous_repair remains: " + ", ".join(failures)
