from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

import pytest

from tools import news_grasp_operational_contract as contract
from tools import news_grasp_execution_governance as governance


@pytest.fixture(autouse=True)
def _isolated_consumption_ledger(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(governance, "CONSUMPTION_LEDGER_ROOT", tmp_path, raising=False)


def _consumer() -> Callable[[dict[str, Any]], dict[str, Any]]:
    value = getattr(contract, "evaluate_execution_governance", None)
    if not callable(value):
        pytest.fail(
            "NEWS_GRASP_EXECUTION_GOVERNANCE_CONSUMER_MISSING",
            pytrace=False,
        )
    return value


def _base() -> dict[str, Any]:
    return {
        "schemaVersion": "NEWS_GRASP_EXECUTION_GOVERNANCE_REQUEST_V1",
        "taskPhase": "fixed_implementation",
        "requestedExecutor": "luna_max",
        "reasoningEffort": "max",
        "unresolvedDecisionIds": [],
        "weeklyUsagePercent": 3.1,
        "plannedUsagePercent": 0.2,
        "candidateResources": {
            "localOnly": {
                "acceptanceComplete": True,
                "expectedTotalResource": 10.0,
            },
            "withDelegation": {
                "acceptanceComplete": True,
                "expectedTotalResource": 8.0,
            },
        },
        "delegationRequested": True,
        "retry": {
            "previousFingerprint": "a" * 64,
            "currentFingerprint": "b" * 64,
            "causeInputChanged": True,
            "retryConsumed": False,
        },
        "progress": {
            "previousTodoIds": ["TODO-177", "TODO-178"],
            "proposedTodoIds": ["TODO-177", "TODO-178", "TODO-179"],
            "statuses": ["completed", "completed", "in_progress"],
            "todoEntries": [
                "☑ [TODO-177][1時間|0.1%] inventoryを確定する",
                "☑ [TODO-178][2時間|0.3%] cross-skill graphを確定する",
                "◉ [TODO-179][2時間30分|0.2%] Constitution traceを実装する",
            ],
            "currentTodoId": "TODO-179",
            "durableGoalPresent": True,
            "durableDeltaPacketPresent": True,
        },
        "operationEvent": "continue",
    }


def _expect_code(operation: Callable[[], object], code: str) -> None:
    with pytest.raises(ValueError) as captured:
        operation()
    assert str(captured.value).split(":", 1)[0] == code


def test_execution_governance_primary_routes_model_and_usage_by_fixed_entropy() -> None:
    result = _consumer()(_base())
    assert result["schemaVersion"] == "NEWS_GRASP_EXECUTION_GOVERNANCE_DECISION_V1"
    assert result["status"] == "admitted"
    assert result["executor"] == "luna_max"
    assert result["reasoningEffort"] == "max"
    assert result["delegationAllowed"] is True
    assert result["weeklyUsageAfterPercent"] == 3.3
    assert result["progress"]["currentTodoId"] == "TODO-179"
    assert result["progress"]["todoEntries"][2].startswith("◉ [TODO-179]")

    local = _base()
    local["taskPhase"] = "deterministic_verification"
    local["requestedExecutor"] = "local_tool"
    local["reasoningEffort"] = "deterministic"
    local["delegationRequested"] = False
    assert _consumer()(local)["executor"] == "local_tool"


def test_execution_governance_boundary_rejects_scope_usage_and_same_shape_retry() -> None:
    unresolved = _base()
    unresolved["unresolvedDecisionIds"] = ["D-NEW"]
    _expect_code(
        lambda: _consumer()(unresolved),
        "NEWS_GRASP_EXECUTION_UNRESOLVED_DECISION",
    )

    over_budget = _base()
    over_budget["weeklyUsagePercent"] = 8.7
    over_budget["plannedUsagePercent"] = 0.2
    _expect_code(
        lambda: _consumer()(over_budget),
        "NEWS_GRASP_WEEKLY_USAGE_LIMIT_EXCEEDED",
    )

    wrong_role = _base()
    wrong_role["requestedExecutor"] = "sol_max"
    _expect_code(
        lambda: _consumer()(wrong_role),
        "NEWS_GRASP_EXECUTOR_ROLE_INVALID",
    )

    same_shape = _base()
    same_shape["retry"]["currentFingerprint"] = "a" * 64
    same_shape["retry"]["causeInputChanged"] = False
    result = _consumer()(same_shape)
    assert result["retry"]["allowed"] is False
    assert result["retry"]["reasonCode"] == "SAME_SHAPE_RETRY_FORBIDDEN"

    bad_display = _base()
    bad_display["progress"]["todoEntries"][2] = "TODO-179 Constitution trace"
    _expect_code(
        lambda: _consumer()(bad_display),
        "NEWS_GRASP_TODO_DISPLAY_FORMAT_INVALID",
    )


def test_execution_governance_recovery_preserves_full_progress_and_manual_stop() -> None:
    stopped = _base()
    stopped["operationEvent"] = "user_stop"
    result = _consumer()(stopped)
    assert result["status"] == "terminal"
    assert result["terminal"] == "user_stopped"
    assert result["retry"]["allowed"] is False

    dropped = _base()
    dropped["progress"]["proposedTodoIds"] = ["TODO-178", "TODO-179"]
    dropped["progress"]["statuses"] = ["completed", "in_progress"]
    dropped["progress"]["todoEntries"] = dropped["progress"]["todoEntries"][1:]
    _expect_code(
        lambda: _consumer()(dropped),
        "NEWS_GRASP_TODO_PREFIX_DROPPED",
    )

    duplicate_progress = copy.deepcopy(_base())
    duplicate_progress["progress"]["statuses"] = [
        "completed",
        "in_progress",
        "in_progress",
    ]
    duplicate_progress["progress"]["todoEntries"] = [
        "☑ [TODO-177][1時間|0.1%] inventoryを確定する",
        "◉ [TODO-178][2時間|0.3%] cross-skill graphを確定する",
        "◉ [TODO-179][2時間30分|0.2%] Constitution traceを実装する",
    ]
    _expect_code(
        lambda: _consumer()(duplicate_progress),
        "NEWS_GRASP_TODO_IN_PROGRESS_COUNT_INVALID",
    )

    active = _consumer()(_base())
    assert active["progress"]["restorationOrder"] == [
        "durable_goal",
        "append_only_todo_ledger",
        "durable_delta_packet",
        "worktree",
    ]


def test_execution_governance_retry_consumption_is_not_caller_resettable() -> None:
    first = _consumer()(_base())
    second_payload = _base()
    second_payload["retry"]["retryConsumed"] = False
    second = _consumer()(second_payload)

    assert first["retry"]["allowed"] is True
    assert second["retry"]["allowed"] is False
    assert second["retry"]["reasonCode"] == "CAUSAL_RETRY_ALREADY_CONSUMED"


def test_execution_governance_retry_consumption_is_atomic() -> None:
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: _consumer()(_base()), range(2)))

    assert sum(result["retry"]["allowed"] is True for result in results) == 1
    assert sorted(result["retry"]["reasonCode"] for result in results) == [
        "CAUSAL_INPUT_CHANGED_ONE_SHOT",
        "CAUSAL_RETRY_ALREADY_CONSUMED",
    ]
