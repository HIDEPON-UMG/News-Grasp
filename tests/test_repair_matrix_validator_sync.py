from __future__ import annotations

import ast
import inspect
import textwrap

from tools.repair_coverage_matrix import COVERAGE_ROWS
from tools.repair_registry import AMBIGUOUS_STATUS, REGISTRY, RepairContext, repair_with_registry
from tools.repair_system_completeness import REPAIR_VALIDATOR_CODESETS
from tools.validate_daily_quality import daily_quality_issue_code


GENERATION_QUALITY_ISSUES = REPAIR_VALIDATOR_CODESETS["generation-quality"]


DAILY_QUALITY_MESSAGES = {
    "daily-quality": {
        "Summary hero_left is missing": "summary_hero_missing",
        "card #3 lacks required emphasis": "category_card_emphasis_missing",
        "Summary section lacks required emphasis": "summary_reflection_emphasis_missing",
        "thumbnail is missing": "thumb_missing",
        "Google News 代理サムネです: thumb=https://lh3.googleusercontent.com/proxy": "thumb_invalid",
        "News-Grasp 自己参照 thumb です": "thumb_invalid",
        "Google News RSS URL のままです": "source_url_unresolved",
        "媒体トップまたはカテゴリトップに丸まった URL があります": "source_url_unresolved",
        "data/articles.jsonl: line 10: JSON decode error": "articles_json_invalid",
        "articles jsonl が存在しません: data/articles.jsonl": "articles_data_missing",
        "published docs missing": "published_docs_missing",
        "audio script failed": "audio_script_quality_invalid",
        "TTS 音声原稿が存在しません": "audio_script_missing",
        "TTS latest_audio.json が存在しません": "audio_publish_state_invalid",
        "TTS audio URL が home HTML に反映されていません": "audio_public_reflection_missing",
        "search audit stale URL": "url_dead_or_stale",
        "source URL date 2026-07-19 is outside the 1-day edition window": "url_dead_or_stale",
        "has 3 article(s); search audit missing": "search_audit_missing",
        r"data\search_audit\2026-07-22\ai.json: JSON decode error": "search_audit_invalid",
        (
            r"data\search_audit\2026-07-22\ai.json: "
            "raw_results_total=4; expected at least 10"
        ): "search_audit_collection_shortfall",
        (
            r"data\search_audit\2026-07-22\ai.json: "
            "candidates_total=2; expected at least 5 before quality filtering"
        ): "search_audit_collection_shortfall",
        (
            r"data\search_audit\2026-07-22\ai.json: "
            "coverage_terms_checked missing required terms"
        ): "search_audit_coverage_terms_missing",
        (
            r"data\search_audit\2026-07-22\ai.json: "
            "queries must contain at least 3 search queries"
        ): "search_audit_queries_insufficient",
        (
            r"data\search_audit\2026-07-22\ai.json: "
            "queries must contain at least 3 search queries. recoverable_source=harvest"
        ): "search_audit_queries_recoverable",
        (
            r"data\search_audit\2026-07-22\ai.json: "
            "dropped reasons are required when candidates were excluded"
        ): "search_audit_dropped_evidence_missing",
        (
            r"data\search_audit\2026-07-22\ai.json: "
            "dropped reasons are required when candidates were excluded. "
            "recoverable_sources=dropped_or_not_selected"
        ): "search_audit_dropped_evidence_recoverable",
        (
            r"data\articles.jsonl:1961 [AI]: follow-up matched_with URL date "
            "2026-05-20 is 63 day(s) older than issue 2026-07-22"
        ): "followup_review_required",
        "digest missing for category": "missing_artifact",
        "category digest is not an article page": "category_digest_empty",
        "weekday=月 does not match date 2026-07-22": "summary_weekday_mismatch",
        "Summary unscheduled summary category: game": "summary_unscheduled_category_reference",
        "category hero focus is invalid": "summary_category_focus_invalid",
        "card #1 has repetitive sentence endings": "digest_style_invalid",
        "card #1 has redundant connectors": "digest_style_invalid",
        "card #1 has translationese wording": "digest_style_invalid",
        "関係図レイアウト構築に失敗": "deepdive_layout_invalid",
        "エッジ線 #1 がノード円 #2 を貫通": "deepdive_layout_invalid",
        "Summary digest が存在しません: digest/Summary/2026-07-22.md": "missing_artifact",
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


def test_every_declared_daily_quality_issue_code_has_explicit_matrix_row() -> None:
    """mapper の全 return code を AST から列挙し、sample fixture の追加漏れも防ぐ。"""
    tree = ast.parse(textwrap.dedent(inspect.getsource(daily_quality_issue_code)))
    declared = {
        node.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Return)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        and node.value.value != "unknown"
    }
    missing = sorted(
        issue_code
        for issue_code in declared
        if ("daily-quality", issue_code) not in _matrix_keys()
    )

    assert not missing, "\n".join(f"daily-quality:{issue_code}" for issue_code in missing)


def test_record_schema_thumb_directions_route_separately() -> None:
    from tools.repair_coverage_matrix import classify_gate_output

    missing = classify_gate_output(
        "record-schema",
        "line 10: 必須キー欠落: 'thumb' (title='missing')",
    )
    invalid = classify_gate_output(
        "record-schema",
        "line 11: thumb は 'http(s)://' で始まる str または None: got 'broken' (title='invalid')",
    )

    assert missing.issue_code == "thumb_missing"
    assert invalid.issue_code == "thumb_invalid"
    assert missing.handler_id == "record-thumb-quarantine-patch"
    assert invalid.handler_id == "record-thumb-quarantine-patch"


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


def test_deterministic_matrix_handlers_never_bind_blocked_ambiguous_implementation() -> None:
    """matrix が deterministic と宣言する handler は実処理を所有する。"""
    from tools.repair_coverage_matrix import RepairClass

    failures: list[str] = []
    for row in COVERAGE_ROWS:
        if row.repair_class != RepairClass.DETERMINISTIC_HANDLER:
            continue
        handler = REGISTRY.get(row.handler_id)
        if handler is None:
            failures.append(f"{row.gate_id}:{row.issue_code}:missing:{row.handler_id}")
            continue
        if handler.repair.__name__ == "_blocked_ambiguous":
            failures.append(f"{row.gate_id}:{row.issue_code}:ambiguous:{row.handler_id}")

    assert not failures, "\n".join(failures)


def test_multi_mode_daily_quality_issues_do_not_route_to_incapable_handler() -> None:
    """同じ語を含む別 failure mode を単一 handler へ潰さない。"""
    from tools.repair_coverage_matrix import RepairClass, RepairIssue, classify_repair_issue

    expected = {
        "thumb_missing": (RepairClass.DETERMINISTIC_HANDLER, "url-quarantine-refill", "data/articles.jsonl"),
        "thumb_invalid": (RepairClass.DETERMINISTIC_HANDLER, "url-quarantine-refill", "data/articles.jsonl"),
        "source_url_unresolved": (RepairClass.DETERMINISTIC_HANDLER, "url-quarantine-refill", "data/articles.jsonl"),
        "search_audit_missing": (RepairClass.TYPED_FATAL, "", "data/search_audit/2026-07-22"),
        "search_audit_invalid": (RepairClass.TYPED_FATAL, "", "data/search_audit/2026-07-22/ai.json"),
        "search_audit_collection_shortfall": (
            RepairClass.TYPED_FATAL,
            "",
            "data/search_audit/2026-07-22/ai.json",
        ),
        "audio_script_missing": (
            RepairClass.LLM_GENERATE_MISSING_ARTIFACT,
            "llm-missing-generated-artifact",
            "digest/Summary/2026-07-22-audio-script.md",
        ),
        "audio_publish_state_invalid": (
            RepairClass.TYPED_FATAL,
            "",
            "build/tts/latest_audio.json",
        ),
        "audio_public_reflection_missing": (RepairClass.TYPED_FATAL, "", "docs/index.html"),
    }
    failures: list[str] = []
    for issue_code, (repair_class, handler_id, artifact) in expected.items():
        evidence = {"typed_reason": "missing_artifact"} if issue_code == "audio_script_missing" else {}
        decision = classify_repair_issue(
            RepairIssue(
                gate_id="daily-quality",
                issue_code=issue_code,
                artifact_paths=(artifact,),
                issue_date="2026-07-22",
                evidence=evidence,
            )
        )
        if (decision.repair_class, decision.handler_id) != (repair_class, handler_id):
            failures.append(
                f"{issue_code}: got=({decision.repair_class},{decision.handler_id}) "
                f"expected=({repair_class},{handler_id})"
            )

    assert not failures, "\n".join(failures)
