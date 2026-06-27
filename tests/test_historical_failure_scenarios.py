from __future__ import annotations

from pathlib import Path

from tools.historical_failure_scenarios import compound_failure_scenarios, historical_failure_scenarios


ROOT = Path(__file__).resolve().parent.parent


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
    }

    for scenario in scenarios:
        assert scenario.stage
        assert scenario.direct_cause
        assert scenario.missing_invariant
        assert scenario.cheapest_e2e_or_fixture
        assert scenario.expected_status in {"runtime_e2e_required", "fixture_required"}
        assert (ROOT / scenario.evidence_path).exists(), scenario.evidence_path


def test_historical_failure_matrix_marks_runtime_e2e_rows() -> None:
    runtime_rows = [
        scenario
        for scenario in historical_failure_scenarios()
        if scenario.expected_status == "runtime_e2e_required"
    ]

    assert {scenario.issue_date for scenario in runtime_rows} >= {"2026-06-12", "2026-06-19", "2026-06-25"}
    assert any("same gate" in scenario.missing_invariant for scenario in runtime_rows)
    assert any("NoPublish" in scenario.cheapest_e2e_or_fixture for scenario in runtime_rows)


def test_compound_failure_matrix_covers_interaction_dimensions() -> None:
    scenarios = compound_failure_scenarios()

    assert {scenario.scenario_id for scenario in scenarios} >= {
        "same_artifact_repair_plus_residual_red",
        "multi_gate_repair_before_publish_boundary",
        "external_block_plus_local_repair",
        "weekday_inventory_plus_distribution_manifest",
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
