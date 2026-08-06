from __future__ import annotations

from pathlib import Path

from tests.helpers.current_launcher_task_adapter import (
    observe_launcher_failure,
    observe_scheduled_task_action,
    observe_task_history_recovery,
)
from tests.helpers.current_runner_adapter import observe_current_runner_failure
from tests.helpers.red_node_evidence import oracle_for, record_red_node


ROOT = Path(__file__).resolve().parents[1]
ISOLATION_ROOT = ROOT.parent


_S128_MODES = {
    "primary": "reject_exit_1",
    "adversarial": "accept_invalid_json",
    "recovery": "authority_missing",
}


def test_runner_adapter_reaches_actual_isolated_runner() -> None:
    observation = observe_current_runner_failure(
        repo=ROOT,
        isolation_root=ISOLATION_ROOT,
        mode="reject_exit_1",
    )
    assert observation["returnCode"] == 76, observation
    assert observation["state"]["status"] == "operation_rejected_high_cost_admission"
    assert "HIGH_COST_OPERATION_ADMISSION_REJECTED" in observation["log"]


def _assert_s128(perspective: str) -> None:
    observation = observe_current_runner_failure(
        repo=ROOT,
        isolation_root=ISOLATION_ROOT,
        mode=_S128_MODES[perspective],
    )
    record_red_node(
        isolation_root=ISOLATION_ROOT,
        case_id="S128",
        perspective=perspective,
        observation=observation,
    )
    node_id = str(oracle_for("S128", perspective)["failureSignature"])
    assert observation["returnCode"] == 76, f"{node_id}:RUNNER_EXIT_NOT_76"
    if perspective == "primary":
        assert observation["failureReceiptExists"] is True, f"{node_id}:FAILURE_RECEIPT_NOT_COMMITTED"
    elif perspective == "adversarial":
        assert observation["continuationState"] != "ABSENT", f"{node_id}:CONTINUATION_STATE_ABSENT"
    else:
        assert observation["state"].get("scheduledFailureRetained") is True, f"{node_id}:SCHEDULED_FAILURE_NOT_RETAINED"


def _make_s128_test(perspective: str):
    def test() -> None:
        _assert_s128(perspective)

    test.__name__ = f"test_s128_{perspective}"
    test.__qualname__ = test.__name__
    return test


for _perspective in ("primary", "adversarial", "recovery"):
    globals()[f"test_s128_{_perspective}"] = _make_s128_test(_perspective)


def test_task_action_adapter_reads_installed_task() -> None:
    observation = observe_scheduled_task_action()
    assert observation["returnCode"] == 0, observation
    assert observation["result"]["Execute"]
    assert "Arguments" in observation["result"]


def _assert_s129(perspective: str) -> None:
    observation = observe_scheduled_task_action()
    observation["input"] = {
        "installedTaskAction": observation.get("result"),
        "scenario": {
            "primary": "installed_action_identity",
            "adversarial": "legacy_trampoline_lineage_unbound",
            "recovery": "repo_launcher_fresh_but_installed_action_stale",
        }[perspective],
    }
    record_red_node(
        isolation_root=ISOLATION_ROOT,
        case_id="S129",
        perspective=perspective,
        observation=observation,
    )
    node_id = str(oracle_for("S129", perspective)["failureSignature"])
    assert observation["returnCode"] == 0, f"{node_id}:TASK_ACTION_READ_FAILED"
    if perspective == "primary":
        assert observation["launcher"] is True, f"{node_id}:VERSIONED_LAUNCHER_NOT_INSTALLED"
    elif perspective == "adversarial":
        assert observation.get("lineageAuthority") == "versioned_launcher", f"{node_id}:LEGACY_TRAMPOLINE_LINEAGE_UNBOUND"
    else:
        assert observation["directRunner"] is False, f"{node_id}:FRESH_REPO_LAUNCHER_MASKED_INSTALLED_DIRECT_RUNNER"


def test_s129_legacy_task_is_typed_superseded_tombstone() -> None:
    legacy = observe_scheduled_task_action("News-Grasp Runner")
    live_runner = Path.home() / "bin" / "news-grasp-runner.ps1"
    source = live_runner.read_text(encoding="utf-8-sig")
    assert legacy["returnCode"] == 0, legacy
    assert legacy["directRunner"] is True, legacy
    assert "NEWS_GRASP_LEGACY_TASK_TOMBSTONE_V1" in source
    assert 'scheduled_attempt_status = "not_started_legacy_tombstone"' in source


def _make_s129_test(perspective: str):
    def test() -> None:
        _assert_s129(perspective)

    test.__name__ = f"test_s129_{perspective}"
    test.__qualname__ = test.__name__
    return test


for _perspective in ("primary", "adversarial", "recovery"):
    globals()[f"test_s129_{_perspective}"] = _make_s129_test(_perspective)


def test_launcher_adapter_reaches_actual_main_without_real_child() -> None:
    observation = observe_launcher_failure(
        repo=ROOT,
        isolation_root=ISOLATION_ROOT,
    )
    assert observation["returnCode"] == 73
    assert observation["childArgv"]
    assert observation["logExists"] is True


def _assert_s131(perspective: str) -> None:
    observation = observe_launcher_failure(
        repo=ROOT,
        isolation_root=ISOLATION_ROOT,
        child_return_code={
            "primary": 73,
            "adversarial": 74,
            "recovery": 75,
        }[perspective],
    )
    observation["input"] = {
        "childReturnCode": observation.get("childReturnCode"),
        "faultBoundary": {
            "primary": "child_nonzero_normal_return",
            "adversarial": "launcher_terminal_receipt_absent",
            "recovery": "task_history_observer_required",
        }[perspective],
    }
    record_red_node(
        isolation_root=ISOLATION_ROOT,
        case_id="S131",
        perspective=perspective,
        observation=observation,
    )
    node_id = str(oracle_for("S131", perspective)["failureSignature"])
    assert observation["returnCode"] != 0, f"{node_id}:CHILD_FAILURE_NOT_PROPAGATED"
    if perspective == "primary":
        assert observation["walClosed"] is True, f"{node_id}:LAUNCH_WAL_NOT_CLOSED"
    elif perspective == "adversarial":
        assert observation["continuationState"] != "ABSENT", f"{node_id}:PRE_CONTROLLER_CONTINUATION_ABSENT"
    else:
        assert observation["observerReconstructable"] is True, f"{node_id}:TASK_HISTORY_NOT_RECONSTRUCTABLE"


def _make_s131_test(perspective: str):
    def test() -> None:
        _assert_s131(perspective)

    test.__name__ = f"test_s131_{perspective}"
    test.__qualname__ = test.__name__
    return test


for _perspective in ("primary", "adversarial", "recovery"):
    globals()[f"test_s131_{_perspective}"] = _make_s131_test(_perspective)


def _assert_dcp06(perspective: str) -> None:
    node_id = str(oracle_for("DCP06", perspective)["failureSignature"])
    if perspective == "primary":
        observation = observe_launcher_failure(
            repo=ROOT,
            isolation_root=ISOLATION_ROOT,
        )
    else:
        observation = observe_task_history_recovery(ROOT)
        observation.update(
            {
                "input": {
                    "installedTaskAction": observation.get("result"),
                    "scenario": (
                        "caller_supplied_fake_attempt_identity"
                        if perspective == "adversarial"
                        else "task_history_without_launcher_or_attempt_row"
                    ),
                },
            }
        )
    record_red_node(
        isolation_root=ISOLATION_ROOT,
        case_id="DCP06",
        perspective=perspective,
        observation=observation,
    )
    if perspective == "primary":
        assert observation.get("rootOperationId") not in {None, "", "ABSENT"}, f"{node_id}:ROOT_OPERATION_ID_ABSENT"
    elif perspective == "adversarial":
        assert observation.get("launchKey") not in {None, "", "ABSENT"}, f"{node_id}:CALLER_ATTEMPT_ID_NOT_REPLACED_BY_DERIVED_LAUNCH_KEY"
    else:
        assert observation.get("preAttemptStatus") == "failed_before_attempt", f"{node_id}:PRE_ATTEMPT_STATUS_NOT_RECONSTRUCTED"
        assert observation.get("scheduledRecoveryFullAuthorityProvable") is True, (
            f"{node_id}:RECOVERY_AUTHORITY_NOT_PROVABLE"
        )


def _make_dcp06_test(perspective: str):
    def test() -> None:
        _assert_dcp06(perspective)

    test.__name__ = f"test_dcp06_{perspective}"
    test.__qualname__ = test.__name__
    return test


for _perspective in ("primary", "adversarial", "recovery"):
    globals()[f"test_dcp06_{_perspective}"] = _make_dcp06_test(_perspective)
