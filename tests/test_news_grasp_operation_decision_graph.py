from __future__ import annotations

from pathlib import Path

import pytest

from tools import news_grasp_operational_contract as contract


ROOT = Path(__file__).resolve().parents[1]


def test_operation_decision_graph_primary_reaches_typed_terminal() -> None:
    first = contract.transition_operational_state(
        {"operationState": "admitted", "operationEvent": "recovery_required"},
        {},
    )
    assert first.get("schemaVersion") == "NEWS_GRASP_OPERATION_DECISION_V1", (
        "NGC_RED_FINITE_DECISION_GRAPH_MISSING"
    )
    assert first["operationState"] == "recovery_active"
    assert first["terminal"] is False

    final = contract.transition_operational_state(
        {
            "operationState": first["operationState"],
            "operationEvent": "recovery_verified",
        },
        {},
    )
    assert final["operationState"] == "recovery_completed"
    assert final["terminal"] is True
    assert final["transitionCount"] == 2
    assert final["maxTransitionDepth"] == 2


def test_operation_decision_graph_boundary_rejects_self_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        contract,
        "OPERATION_DECISION_GRAPH_RELATIVE_PATH",
        Path(
            "tests/fixtures/constitutional-operations/"
            "operation-decision-self-loop-invalid-v1.json"
        ),
        raising=False,
    )
    try:
        contract.transition_operational_state(
            {"operationState": "admitted", "operationEvent": "retry_same_state"},
            {},
        )
    except ValueError as exc:
        assert str(exc) == "OPERATION_DECISION_GRAPH_CYCLE_INVALID"
    else:
        pytest.fail("NGC_RED_DECISION_GRAPH_SELF_LOOP_NOT_REJECTED")


def test_operation_decision_graph_recovery_stops_and_reenters_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        contract, "OPERATION_CONSUMPTION_ROOT", tmp_path, raising=False
    )
    stopped = contract.transition_operational_state(
        {"operationState": "admitted", "operationEvent": "user_stop"},
        {},
    )
    assert stopped["operationState"] == "user_stopped", (
        "NGC_RED_STOP_OR_CAUSAL_REENTRY_MISSING"
    )
    assert stopped["terminal"] is True

    deferred = contract.transition_operational_state(
        {"operationState": "admitted", "operationEvent": "external_unavailable"},
        {},
    )
    resumed = contract.transition_operational_state(
        {
            "operationState": deferred["operationState"],
            "operationEvent": "fresh_external_authority",
            "dailyOperationLineageId": "lineage-2026-08-12",
            "previousExternalAuthoritySha256": "1" * 64,
            "externalAuthoritySha256": "2" * 64,
            "reentryConsumed": True,
        },
        {},
    )
    assert resumed["operationState"] == "admitted"
    assert resumed["reentryConsumed"] is True
    assert resumed["dailyOperationLineageId"] == "lineage-2026-08-12"

    with pytest.raises(
        ValueError,
        match="^OPERATION_DECISION_REENTRY_ALREADY_CONSUMED$",
    ):
        contract.transition_operational_state(
            {
                "operationState": "operation_deferred",
                "operationEvent": "fresh_external_authority",
                "dailyOperationLineageId": "lineage-2026-08-12",
                "previousExternalAuthoritySha256": "1" * 64,
                "externalAuthoritySha256": "2" * 64,
                "reentryConsumed": False,
            },
            {},
        )
