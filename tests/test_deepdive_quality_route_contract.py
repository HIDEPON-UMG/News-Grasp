from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIRECT_RUNTIME = ROOT / "tools" / "news_grasp_direct_runtime.py"
DAILY_QUALITY = ROOT / "tools" / "validate_daily_quality.py"
REPAIR_MATRIX = ROOT / "tools" / "repair_coverage_matrix.py"
REPAIR_REGISTRY = ROOT / "tools" / "repair_registry.py"
PUBLISH_COMPLETE = ROOT / "tools" / "daily_self_heal.py"
ROUTES = ROOT / "config" / "deepdive_quality_routes.json"
AUTOMATION = ROOT / "automation" / "news-grasp-6-40" / "automation.toml.template"


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
    assert "deepdive_url_provenance_invalid" in source
    assert "deepdive_dialogue_value_invalid" in source


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
        ("deepdive_dialogue_value_invalid", "deepdive-dialogue-rebuild"),
        ("deepdive_public_surface_invalid", "deepdive-rendered-public-rebuild"),
    ):
        assert issue_code in matrix
        assert handler_id in matrix
        assert handler_id in registry


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
    assert value["schemaVersion"] == "DEEPDIVE_SHARED_QUALITY_ROUTES_V1"
    assert set(value["declaredRoutes"]) == expected
    assert set(value["consumerRoutes"]) == expected
    assert set(value["positiveFixtureRoutes"]) == expected
    assert set(value["negativeFixtureRoutes"]) == expected
    assert value["unknownRoutePolicy"] == "fail_closed"
