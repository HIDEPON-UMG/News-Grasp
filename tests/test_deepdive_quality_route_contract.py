from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import deepdive_quality


ROOT = Path(__file__).resolve().parents[1]
DIRECT_RUNTIME = ROOT / "tools" / "news_grasp_direct_runtime.py"
DIRECT_COMPLETION = ROOT / "tools" / "news_grasp_direct_completion.py"
DAILY_QUALITY = ROOT / "tools" / "validate_daily_quality.py"
REPAIR_MATRIX = ROOT / "tools" / "repair_coverage_matrix.py"
REPAIR_REGISTRY = ROOT / "tools" / "repair_registry.py"
PUBLISH_COMPLETE = ROOT / "tools" / "daily_self_heal.py"
ROUTES = ROOT / "config" / "deepdive_quality_routes.json"
AUTOMATION = ROOT / "automation" / "news-grasp-6-40" / "automation.toml.template"
DEEPDIVE_PROMPT = ROOT / "prompts" / "deepdive-research-system.md"
DIRECT_SKILL = ROOT / "automation" / "skills" / "news-grasp-direct-mainline" / "SKILL.md"
REPAIR_SKILL = ROOT / "automation" / "skills" / "news-grasp-repair-method" / "SKILL.md"
E2E_SKILL = ROOT / "automation" / "skills" / "news-grasp-e2e-discipline" / "SKILL.md"
SPEC = ROOT / "docs" / "spec.md"
AGENTS = ROOT / "AGENTS.md"
CLAUDE = ROOT / "CLAUDE.md"
CONSTITUTION_TRACE = ROOT / "config" / "news_grasp_constitution_trace_v1.json"
CONSTITUTION_PROJECTION = (
    ROOT / "config" / "news_grasp_constitution_projection_v1.json"
)


V2_ISSUE_CODES = {
    "deepdive_url_provenance_invalid",
    "deepdive_article_value_invalid",
    "deepdive_relation_quality_invalid",
    "deepdive_dialogue_value_invalid",
    "deepdive_research_evidence_insufficient",
    "deepdive_public_surface_invalid",
}

V2_REVIEW_AXES = {
    "theme_specific_insight",
    "evidence_depth",
    "causal_coherence",
    "counterevidence",
    "decision_utility",
    "dialogue_naturalness",
    "relation_map_utility",
}


def test_runner_uses_shared_quality_engine_before_completion() -> None:
    source = DIRECT_RUNTIME.read_text(encoding="utf-8-sig")
    stages = source.split("DIRECT_STAGES = (", 1)[1].split(")", 1)[0]
    deepdive_article = stages.index('"deepdive_article"')
    deepdive_quality = stages.index('"deepdive_quality"')
    daily_quality = stages.index('"daily_quality"')
    assert deepdive_article < deepdive_quality < daily_quality
    assert "verify_public_completion(" in source
    assert "tools.news_grasp_direct_completion" in source


def test_daily_full_recovery_and_resume_share_one_issue_materializer() -> None:
    """RC-02: direct runtimeは個別runner writerを復活させない。"""

    source = DIRECT_RUNTIME.read_text(encoding="utf-8-sig")
    assert '"deepdive_article"' in source
    assert '"deepdive_quality"' in source
    assert "'capture' '--article'" not in source
    assert "tools.tts.build_deepdive_dialogue_script" not in source
    assert not (ROOT / "scripts" / "ops" / "news-grasp-runner.ps1").exists()


def test_runner_preserves_shared_quality_issue_code_for_repair() -> None:
    source = DIRECT_RUNTIME.read_text(encoding="utf-8-sig")
    assert '"deepdive_quality"' in source
    assert "surface_failures" in source
    assert "exact_successor" in source
    assert "CaptureIssueCodes" not in source
    assert "$deepDiveQualityIssueCode" not in source
    assert "-join ','" not in source


def test_daily_quality_uses_shared_quality_engine() -> None:
    source = DAILY_QUALITY.read_text(encoding="utf-8-sig")
    assert "from tools import deepdive_quality" in source
    assert "deepdive_quality.audit_issue(" in source
    for issue_code in (
        "deepdive_url_provenance_invalid",
        "deepdive_article_value_invalid",
        "deepdive_relation_quality_invalid",
        "deepdive_dialogue_value_invalid",
        "deepdive_research_evidence_insufficient",
        "deepdive_public_surface_invalid",
    ):
        assert issue_code in source


def test_post_generation_consumers_require_rendered_public_surface() -> None:
    direct_runtime = DIRECT_RUNTIME.read_text(encoding="utf-8-sig")
    daily_quality = DAILY_QUALITY.read_text(encoding="utf-8-sig")
    publish_complete = PUBLISH_COMPLETE.read_text(encoding="utf-8-sig")

    pre_public_completion = direct_runtime.split("def verify_public_completion(", 1)[0]
    assert "require_rendered_public=True" not in pre_public_completion
    assert "require_rendered_public=True" in daily_quality
    verify_function = publish_complete.split(
        "def verify_publish_complete(", 1
    )[1].split("\n\ndef ", 1)[0]
    assert "require_rendered_public=True" in verify_function


def test_repair_matrix_owns_shared_quality_issue_codes() -> None:
    matrix = REPAIR_MATRIX.read_text(encoding="utf-8-sig")
    registry = REPAIR_REGISTRY.read_text(encoding="utf-8-sig")
    for issue_code, handler_id in (
        ("deepdive_url_provenance_invalid", "deepdive-provenance-recapture"),
        ("deepdive_article_value_invalid", "deepdive-article-value-rewrite"),
        ("deepdive_relation_quality_invalid", "deepdive-relation-quality-rewrite"),
        ("deepdive_dialogue_value_invalid", "deepdive-dialogue-value-rewrite"),
        ("deepdive_research_evidence_insufficient", "deepdive-research-and-rewrite"),
        ("deepdive_public_surface_invalid", "deepdive-rendered-public-rebuild"),
    ):
        assert issue_code in matrix
        assert handler_id in matrix
        if handler_id not in {
            "deepdive-article-value-rewrite",
            "deepdive-relation-quality-rewrite",
            "deepdive-dialogue-value-rewrite",
            "deepdive-research-and-rewrite",
        }:
            assert handler_id in registry
    assert "deepdive-dialogue-rebuild" not in matrix
    assert "deepdive-dialogue-rebuild" not in registry


def test_publish_complete_rechecks_shared_quality_before_public_probe() -> None:
    source = PUBLISH_COMPLETE.read_text(encoding="utf-8-sig")
    function = source.split("def verify_publish_complete(", 1)[1].split(
        "\n\ndef ", 1
    )[0]
    shared_gate = function.index("deepdive_quality.audit_issue(")
    public_probe = function.index("verify_publish(")
    assert shared_gate < public_probe
    assert "deepdive_shared_quality" in function
    assert "deepdive_shared_quality_invalid" in function


def test_codex_mainline_automation_uses_same_shared_quality_cli() -> None:
    source = AUTOMATION.read_text(encoding="utf-8-sig")
    assert "tools.deepdive_quality" in source
    assert "audit-issue" in source
    assert "audit-issue --date YYYY-MM-DD --require-rendered-public" in source
    assert "2026-07-02" not in source


def test_declared_consumer_and_fixture_routes_match_exactly() -> None:
    value = json.loads(ROUTES.read_text(encoding="utf-8"))
    expected = {
        "production_generation",
        "repair_publish",
        "daily_quality",
        "codex_daily_audit",
    }
    assert value["schemaVersion"] == "DEEPDIVE_SHARED_QUALITY_ROUTES_V2"
    assert value["engine"] == "tools.deepdive_quality"
    assert set(value["issueCodes"]) == V2_ISSUE_CODES
    assert set(value["declaredRoutes"]) == expected
    assert set(value["consumerRoutes"]) == expected
    assert set(value["positiveFixtureRoutes"]) == expected
    assert set(value["negativeFixtureRoutes"]) == expected
    assert value["unknownRoutePolicy"] == "fail_closed"


def test_four_consumers_pass_their_exact_route_identity() -> None:
    engine = (ROOT / "tools" / "deepdive_quality.py").read_text(
        encoding="utf-8-sig"
    )
    direct = DIRECT_COMPLETION.read_text(encoding="utf-8-sig")
    repair = PUBLISH_COMPLETE.read_text(encoding="utf-8-sig")
    daily = DAILY_QUALITY.read_text(encoding="utf-8-sig")
    automation = AUTOMATION.read_text(encoding="utf-8-sig")

    assert 'route="production_generation"' in engine
    assert 'route="production_generation"' in direct
    assert 'route="repair_publish"' in repair
    assert 'route="daily_quality"' in daily
    assert "--route codex_daily_audit" in automation


def test_shared_engine_rejects_unknown_route_before_audit(tmp_path: Path) -> None:
    with pytest.raises(
        deepdive_quality.DeepDiveQualityError,
        match="DEEPDIVE_SHARED_QUALITY_ROUTE_UNKNOWN unregistered_route",
    ):
        deepdive_quality.audit_issue(
            repo_root=tmp_path,
            issue_date="2026-08-31",
            include_corpus=False,
            route="unregistered_route",
        )


def test_deepdive_prompt_uses_v2_publication_review_contract_only() -> None:
    source = DEEPDIVE_PROMPT.read_text(encoding="utf-8-sig")
    output_review = source.split("### ステップ 4.5:", 1)[1].split(
        "### ステップ 5:", 1
    )[0]

    assert "DEEPDIVE_QUALITY_REVIEW_V2" in output_review
    assert "data/deepdive-quality-review/{YYYY-MM-DD}.json" in output_review
    assert V2_REVIEW_AXES <= set(output_review.split()) or all(
        axis in output_review for axis in V2_REVIEW_AXES
    )
    assert "各1〜5" in output_review
    assert "いずれか2以下" in output_review
    assert "平均4未満" in output_review
    assert "0 / 1 / 2" not in output_review
    assert "8 点以上" not in output_review
    assert "frenemy" not in source.casefold()
    assert "協調的競合" not in source


def test_repo_local_skills_share_v2_routes_and_remove_legacy_dialogue_rebuild() -> None:
    direct = DIRECT_SKILL.read_text(encoding="utf-8-sig")
    repair = REPAIR_SKILL.read_text(encoding="utf-8-sig")
    e2e = E2E_SKILL.read_text(encoding="utf-8-sig")

    for source in (direct, repair, e2e):
        assert V2_ISSUE_CODES <= {code for code in V2_ISSUE_CODES if code in source}
        assert "DEEPDIVE_QUALITY_REVIEW_V2" in source
        assert "deepdive-dialogue-rebuild" not in source
    for handler_id in (
        "deepdive-provenance-recapture",
        "deepdive-article-value-rewrite",
        "deepdive-relation-quality-rewrite",
        "deepdive-dialogue-value-rewrite",
        "deepdive-research-and-rewrite",
        "deepdive-rendered-public-rebuild",
    ):
        assert handler_id in repair
    assert "合計14個" not in repair


def test_product_contract_projections_name_the_same_v2_schema_routes_and_issues() -> None:
    routes = {
        "production_generation",
        "repair_publish",
        "daily_quality",
        "codex_daily_audit",
    }
    sources = [
        SPEC.read_text(encoding="utf-8-sig"),
        AGENTS.read_text(encoding="utf-8-sig"),
        CLAUDE.read_text(encoding="utf-8-sig"),
    ]
    for source in sources:
        assert "DEEPDIVE_QUALITY_REVIEW_V2" in source
        assert all(issue_code in source for issue_code in V2_ISSUE_CODES)
        assert all(route in source for route in routes)

    covenant = sources[0].split(
        "## DeepDive Source and Podcast Value Covenant", 1
    )[1].split("## Human Commitment", 1)[0]
    assert "各区間はprimaryとsupportの14根拠" not in covenant
    assert "coverage matrixとrepair registryがdeterministic handlerを所有" not in covenant


def test_constitution_trace_and_generated_projection_bind_v2_review() -> None:
    trace = json.loads(CONSTITUTION_TRACE.read_text(encoding="utf-8-sig"))
    binding = next(
        row
        for row in trace["acceptanceBindings"]
        if row["productionRoute"] == "tools/deepdive_quality.py"
        and row["requirementKey"] == "NG-RC-02"
    )
    assert binding["consumerMarker"] == "DEEPDIVE_QUALITY_REVIEW_V2"
    assert binding["stateId"] == "deepdive_publication_quality_v2_bound"
    assert binding["recoveryId"] == "route_v2_red_to_owned_repair"
    assert binding["evidenceId"] == "semantic_review_binding_and_structured_issues"
    assert set(binding["testNodeIds"]) == {
        "test_audit_accepts_bound_v2_quality_review",
        "test_audit_rejects_stale_relation_identity",
        "test_audit_rejects_unknown_review_route",
    }

    projection = CONSTITUTION_PROJECTION.read_text(encoding="utf-8-sig")
    for marker in (
        "DEEPDIVE_QUALITY_REVIEW_V2",
        "deepdive_publication_quality_v2_bound",
        "route_v2_red_to_owned_repair",
        "semantic_review_binding_and_structured_issues",
    ):
        assert marker in projection
