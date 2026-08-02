from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "ops" / "news-grasp-runner.ps1"
DAILY_QUALITY = ROOT / "tools" / "validate_daily_quality.py"
REPAIR_MATRIX = ROOT / "tools" / "repair_coverage_matrix.py"
REPAIR_REGISTRY = ROOT / "tools" / "repair_registry.py"
PUBLISH_COMPLETE = ROOT / "tools" / "daily_self_heal.py"
ROUTES = ROOT / "config" / "deepdive_quality_routes.json"
AUTOMATION = (
    Path.home()
    / ".codex"
    / "automations"
    / "news-grasp-6-40"
    / "automation.toml"
)


def test_runner_uses_shared_quality_engine_before_completion() -> None:
    source = RUNNER.read_text(encoding="utf-8-sig")
    generation = source.index("build_deepdive_dialogue_script")
    shared_gate = source.index("tools.deepdive_quality", generation)
    completion = source.index("tools.validate_daily_quality", shared_gate)
    assert generation < shared_gate < completion
    assert "audit-issue" in source[shared_gate:completion]


def test_runner_preserves_shared_quality_issue_code_for_repair() -> None:
    source = RUNNER.read_text(encoding="utf-8-sig")
    block = source.split("$deepDiveTtsPublishArgs", 1)[1].split(
        "# ===== 2.9 digest/data commit", 1
    )[0]
    assert "GateId = 'deepdive-shared-quality'" in block
    assert "FailureKind = 'content'" in block
    assert "UseAutonomousGate = $true" in block
    assert "Invoke-AutonomousGate -GateId 'deepdive-shared-quality'" in block
    assert "CaptureIssueCodes" not in block
    assert "$deepDiveQualityIssueCode" not in block
    assert "-join ','" not in block
    assert "-GateId 'deepdive-shared-quality'" in block
    assert "-Reason $failureReason" in block


def test_daily_quality_uses_shared_quality_engine() -> None:
    source = DAILY_QUALITY.read_text(encoding="utf-8-sig")
    assert "from tools import deepdive_quality" in source
    assert "deepdive_quality.audit_issue(" in source
    assert "deepdive_url_provenance_invalid" in source
    assert "deepdive_dialogue_value_invalid" in source


def test_repair_matrix_owns_shared_quality_issue_codes() -> None:
    matrix = REPAIR_MATRIX.read_text(encoding="utf-8-sig")
    registry = REPAIR_REGISTRY.read_text(encoding="utf-8-sig")
    for issue_code, handler_id in (
        ("deepdive_url_provenance_invalid", "deepdive-provenance-recapture"),
        ("deepdive_dialogue_value_invalid", "deepdive-dialogue-rebuild"),
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


def test_codex_daily_audit_uses_same_shared_quality_cli() -> None:
    source = AUTOMATION.read_text(encoding="utf-8-sig")
    assert "tools.deepdive_quality" in source
    assert "audit-issue" in source
    assert "audit-period" in source
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
