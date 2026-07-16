from __future__ import annotations

from pathlib import Path

from tools.historical_failure_scenarios import (
    compound_failure_scenarios,
    historical_failure_horizontal_audits,
    historical_failure_scenarios,
)


ROOT = Path(__file__).resolve().parent.parent
REQUIRED_HORIZONTAL_LANES = {"runner", "repair", "state", "report"}


def test_historical_failure_matrix_covers_lifecycle_incident_corpus() -> None:
    scenarios = historical_failure_scenarios()
    covered_evidence = {scenario.evidence_path for scenario in scenarios}

    assert {scenario.issue_date for scenario in scenarios} >= {
        "2026-06-12",
        "2026-06-13",
        "2026-06-14",
        "2026-06-16",
        "2026-06-17",
        "2026-06-18",
        "2026-06-19",
        "2026-06-20",
        "2026-06-21",
        "2026-06-22",
        "2026-06-23",
        "2026-06-24",
        "2026-06-25",
        "2026-06-26",
        "2026-07-04",
        "2026-07-05",
        "2026-07-06",
        "2026-07-07",
        "2026-07-08",
        "2026-07-13",
    }
    incident_files = {
        f"docs/incidents/{path.name}"
        for path in (ROOT / "docs" / "incidents").glob("*")
        if path.name.startswith("2026-")
        and path.name != "2026-06-16-claude-audit-prompt.md"
    }
    assert incident_files <= covered_evidence
    assert {
        f"data/gate_attempts/{date}.json"
        for date in ("2026-06-12", "2026-06-13", "2026-06-14", "2026-06-17", "2026-06-24")
    } <= covered_evidence
    assert "build/recovery/proofs/2026-06-26-post-gate-verify-publish-complete.json" in covered_evidence
    assert {scenario.root_pattern for scenario in scenarios} >= {
        "artifact inventory/scope",
        "PowerShell Python boundary",
        "public pre-gate/refill and parser boundary",
        "non-interactive runner contract",
        "resume order and fallback boundary",
        "masked multi-error gate disclosure",
        "multi-gate pre-publish convergence",
        "resume-before-rerun boundary",
        "non-interactive Windows environment boundary",
        "public degradation after nominal batch success",
        "completion gate and source-of-truth drift",
        "publish boundary and distribution manifest",
        "multi-stage repair and deploy convergence boundary",
        "publish convergence and state reconciliation boundary",
        "repair decision routing and report fidelity boundary",
        "summary materialize and compound repair routing boundary",
        "repair coverage gate drift",
        "live runner readiness completion overclaim",
        "live ops bootstrap self-repair gap",
        "repair parser and portable distribution asset boundary",
        "category hero title split contract gap",
    }

    for scenario in scenarios:
        assert scenario.stage
        assert scenario.direct_cause
        assert scenario.missing_invariant
        assert scenario.cheapest_e2e_or_fixture
        assert scenario.expected_status in {"runtime_e2e_required", "fixture_required"}
        assert (ROOT / scenario.evidence_path).exists(), scenario.evidence_path


def test_every_historical_failure_has_runner_repair_state_report_horizontal_scan() -> None:
    """過去障害を単独原因で閉じず、同じ incident 内で 4 レーン影響調査を固定する。"""
    scenarios = historical_failure_scenarios()

    assert scenarios
    for scenario in scenarios:
        lanes = set(getattr(scenario, "horizontal_lanes", ()))
        assert lanes == REQUIRED_HORIZONTAL_LANES, scenario
        assert getattr(scenario, "horizontal_scan_summary", ""), scenario
        summary = scenario.horizontal_scan_summary
        for phrase in [
            "runner",
            "repair",
            "state",
            "report",
        ]:
            assert phrase in summary, scenario


def test_every_historical_failure_has_detailed_four_lane_audit() -> None:
    """4レーン再監査を抽象サマリで済ませず、各 evidence の実査記録として固定する。"""
    scenarios = historical_failure_scenarios()
    audits = historical_failure_horizontal_audits()
    audit_by_key = {
        (audit.issue_date, audit.stage, audit.evidence_path): audit
        for audit in audits
    }

    assert len(audits) == len(scenarios)
    for scenario in scenarios:
        key = (scenario.issue_date, scenario.stage, scenario.evidence_path)
        assert key in audit_by_key, scenario
        audit = audit_by_key[key]
        assert audit.lanes.keys() == REQUIRED_HORIZONTAL_LANES
        for lane_name, lane_text in audit.lanes.items():
            assert len(lane_text) >= 32, (scenario, lane_name, lane_text)
            assert not any(
                placeholder in lane_text.casefold()
                for placeholder in ("todo", "tbd", "generic", "same as above", "未確認")
            ), (scenario, lane_name, lane_text)
        assert audit.confirmed_gap
        assert audit.current_contract
        assert audit.residual_risk
        if scenario.expected_status == "runtime_e2e_required":
            assert any(
                marker in audit.required_followup.casefold()
                for marker in ("runtime", "e2e", "dry-run", "runner")
            ), audit
        if scenario.expected_status == "fixture_required":
            assert any(
                marker in audit.required_followup.casefold()
                for marker in ("fixture", "contract", "pytest")
            ), audit


def test_historical_failure_horizontal_audit_report_covers_entire_corpus() -> None:
    report_path = ROOT / "docs" / "incidents" / "2026-06-28-historical-failure-horizontal-audit-report.html"
    assert report_path.exists()
    html = report_path.read_text(encoding="utf-8")

    assert "過去障害全件詳細再監査" in html
    assert "runner / repair / state / report" in html
    for scenario in historical_failure_scenarios():
        if not scenario.evidence_path.startswith("docs/incidents/"):
            continue
        assert scenario.issue_date in html
        assert Path(scenario.evidence_path).name in html


def test_historical_failure_matrix_marks_runtime_e2e_rows() -> None:
    runtime_rows = [
        scenario
        for scenario in historical_failure_scenarios()
        if scenario.expected_status == "runtime_e2e_required"
    ]

    assert {scenario.issue_date for scenario in runtime_rows} >= {"2026-06-12", "2026-06-19", "2026-06-25"}
    assert any("same gate" in scenario.missing_invariant for scenario in runtime_rows)
    assert any("NoPublish" in scenario.cheapest_e2e_or_fixture for scenario in runtime_rows)


def test_historical_failure_matrix_covers_codex_residual_work_public_reflection() -> None:
    scenarios = historical_failure_scenarios()
    matches = [
        scenario
        for scenario in scenarios
        if scenario.issue_date == "2026-06-30"
        and scenario.root_pattern == "Codex residual work completion boundary"
    ]

    assert len(matches) == 1
    scenario = matches[0]
    joined = " ".join(
        [
            scenario.stage,
            scenario.direct_cause,
            scenario.missing_invariant,
            scenario.cheapest_e2e_or_fixture,
        ]
    )
    for token in [
        "commit",
        "push",
        "GitHub Pages",
        "public CSS",
        "service worker",
        "public DOM",
        "remote HEAD",
        "residual work block",
    ]:
        assert token in joined


def test_compound_failure_matrix_covers_interaction_dimensions() -> None:
    scenarios = compound_failure_scenarios()

    assert {scenario.scenario_id for scenario in scenarios} >= {
        "same_artifact_repair_plus_residual_red",
        "multi_gate_repair_before_publish_boundary",
        "external_block_plus_local_repair",
        "weekday_inventory_plus_distribution_manifest",
        "summary_materialize_missing_plus_downstream_repair_blockers",
    }
    assert any(
        {"record-schema", "residual-schema"} <= set(scenario.gates)
        and scenario.expected_status == "green_after_compound_repair"
        for scenario in scenarios
    )
    assert any(
        scenario.no_publish_required and "fallback_publish" in scenario.forbidden_public_actions
        for scenario in scenarios
    )
    assert any(
        {"summary-reflection", "generation-quality", "record-schema", "pytest-static"} <= set(scenario.gates)
        and scenario.expected_status == "green_after_compound_repair"
        for scenario in scenarios
    )
    for scenario in scenarios:
        assert len(scenario.dimensions) >= 2
        assert scenario.expected_status in {
            "green_after_compound_repair",
            "green_before_publish_boundary_no_public_actions",
            "typed_external_block_handled",
            "green_after_inventory_manifest_reverify",
        }
        assert scenario.expected_status != "blocked_unresolved_compound_failure"
        assert scenario.expected_status != "typed_yellow_not_complete"
        assert scenario.expected_status != "typed_red_not_complete"


def test_2026_07_11_editor_repair_routing_incident_is_registered() -> None:
    scenario = next(item for item in historical_failure_scenarios() if item.issue_date == "2026-07-11")
    assert "newsroom-editor-preview" in scenario.stage
    assert "structured daily-quality metadata" in scenario.cheapest_e2e_or_fixture
    assert scenario.evidence_path.endswith("2026-07-11-daily-batch-editor-repair-routing-report.html")


def test_2026_07_12_editor_materialization_incident_is_registered() -> None:
    scenario = next(item for item in historical_failure_scenarios() if item.issue_date == "2026-07-12")
    assert "editor workspace snapshot" in scenario.stage
    assert "snapshot path contract" in scenario.cheapest_e2e_or_fixture
    assert scenario.evidence_path.endswith("2026-07-12-daily-batch-editor-materialization-report.html")


def test_2026_07_13_daily_batch_repair_path_incident_is_registered() -> None:
    scenario = next(item for item in historical_failure_scenarios() if item.issue_date == "2026-07-13")
    assert "repair parser" in scenario.stage
    assert "warning-prefix JSON parser fixture" in scenario.cheapest_e2e_or_fixture
    assert "podcast cover default asset contract" in scenario.cheapest_e2e_or_fixture
    assert scenario.evidence_path.endswith("2026-07-13-daily-batch-repair-path-report.html")


def test_2026_07_16_pytest_basetemp_incident_is_registered() -> None:
    scenario = next(item for item in historical_failure_scenarios() if item.issue_date == "2026-07-16")
    assert "runtime model dependency audit" in scenario.stage
    assert "nested basetemp" in scenario.root_pattern
    assert "worktree top" in scenario.missing_invariant
    assert "runtime model dependency audit contract" in scenario.cheapest_e2e_or_fixture
    assert scenario.evidence_path.endswith("2026-07-16-pytest-basetemp-recovery-report.html")


def test_compound_failure_matrix_never_treats_internal_block_as_success() -> None:
    scenarios = compound_failure_scenarios()

    forbidden = {
        "blocked_unresolved_compound_failure",
        "typed_red_not_complete",
        "typed_yellow_not_complete",
    }
    assert not {scenario.expected_status for scenario in scenarios} & forbidden
    for scenario in scenarios:
        if scenario.expected_status.startswith("green_"):
            assert "block" not in " ".join(scenario.dimensions).casefold()
