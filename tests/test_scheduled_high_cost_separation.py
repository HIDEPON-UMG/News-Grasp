from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest
import tools.harness.high_cost_control_v2 as high_cost


BROKER_PATH = (
    Path(__file__).resolve().parents[2]
    / "tools"
    / "harness"
    / "model_spawn_broker.py"
)


def _load_broker_module():
    spec = importlib.util.spec_from_file_location("scheduled_model_spawn_broker", BROKER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _required_symbol(name: str, failure_signature: str):
    assert hasattr(high_cost, name), failure_signature
    return getattr(high_cost, name)


def test_scheduled_identity_is_date_scoped_and_not_goal_e2e_identity() -> None:
    CanonicalAuthority = high_cost.CanonicalAuthority
    scheduled_news_grasp_authority = _required_symbol(
        "scheduled_news_grasp_authority", "RED_SCHED_IDENTITY_CONSUMER_MISSING"
    )

    scheduled = scheduled_news_grasp_authority("2026-08-03")
    goal = CanonicalAuthority(
        "goal-e2e-task", "thread", "a" * 64, "goal", "単一の最終NoPublish E2E"
    )

    assert scheduled.task_identity != goal.task_identity
    assert scheduled.thread_id == "scheduled:News-Grasp"
    assert scheduled.goal_id == "scheduled-production:2026-08-03"
    assert scheduled_news_grasp_authority("2026-08-03") == scheduled
    assert scheduled_news_grasp_authority("2026-08-04") != scheduled


def test_scheduled_production_reserves_one_non_e2e_attempt(tmp_path) -> None:
    ControlError = high_cost.ControlError
    HighCostControlStore = high_cost.HighCostControlStore
    MemoryAnchor = high_cost.MemoryAnchor
    admit_scheduled_news_grasp_operation_in_store = _required_symbol(
        "admit_scheduled_news_grasp_operation_in_store",
        "RED_SCHED_SINGLE_ATTEMPT_CONSUMER_MISSING",
    )

    store = HighCostControlStore.create_for_test(
        tmp_path / "ledger.sqlite3", MemoryAnchor()
    )
    receipt = admit_scheduled_news_grasp_operation_in_store(
        store=store,
        issue_date="2026-08-03",
        operation_kind="scheduled_production",
    )
    row = store.db.execute(
        "SELECT max_calls,call_count,max_full_e2e_attempts,full_e2e_attempt_count "
        "FROM tasks WHERE task_identity=?",
        (receipt["taskIdentity"],),
    ).fetchone()
    assert tuple(row) == (9, 0, 0, 0)
    with pytest.raises(ControlError, match="HIGH_COST_SCHEDULED_ATTEMPT_REPLAY"):
        admit_scheduled_news_grasp_operation_in_store(
            store=store,
            issue_date="2026-08-03",
            operation_kind="scheduled_production",
        )
    store.close()


def test_recovery_reuses_same_date_identity_attempt_and_remaining_budget(tmp_path) -> None:
    ControlError = high_cost.ControlError
    HighCostControlStore = high_cost.HighCostControlStore
    MemoryAnchor = high_cost.MemoryAnchor
    admit_scheduled_news_grasp_operation_in_store = _required_symbol(
        "admit_scheduled_news_grasp_operation_in_store",
        "RED_SCHED_RECOVERY_ADMISSION_CONSUMER_MISSING",
    )
    reserve_scheduled_model_call_in_store = _required_symbol(
        "reserve_scheduled_model_call_in_store",
        "RED_SCHED_SHARED_BUDGET_CONSUMER_MISSING",
    )

    store = HighCostControlStore.create_for_test(
        tmp_path / "ledger.sqlite3", MemoryAnchor()
    )
    production = admit_scheduled_news_grasp_operation_in_store(
        store=store,
        issue_date="2026-08-03",
        operation_kind="scheduled_production",
    )
    for index in range(5):
        reserve_scheduled_model_call_in_store(
            store=store,
            admission=production,
            route=f"reporter:cat-{index}",
            call_id=f"production-{index}",
        )

    recovery = admit_scheduled_news_grasp_operation_in_store(
        store=store,
        issue_date="2026-08-03",
        operation_kind="scheduled_recovery",
    )
    assert recovery["taskIdentity"] == production["taskIdentity"]
    assert recovery["attemptReservation"] == production["attemptReservation"]
    for index in range(4):
        reserve_scheduled_model_call_in_store(
            store=store,
            admission=recovery,
            route="deepdive",
            call_id=f"recovery-{index}",
        )
    with pytest.raises(ControlError, match="HIGH_COST_CALL_BUDGET_EXHAUSTED"):
        reserve_scheduled_model_call_in_store(
            store=store,
            admission=recovery,
            route="deepdive",
            call_id="tenth-call",
        )
    store.close()


def test_recovery_can_own_the_single_attempt_when_pre_admission_start_failed(
    tmp_path,
) -> None:
    HighCostControlStore = high_cost.HighCostControlStore
    MemoryAnchor = high_cost.MemoryAnchor
    admit_scheduled_news_grasp_operation_in_store = _required_symbol(
        "admit_scheduled_news_grasp_operation_in_store",
        "RED_SCHED_PRE_ADMISSION_RECOVERY_CONSUMER_MISSING",
    )

    store = HighCostControlStore.create_for_test(
        tmp_path / "ledger.sqlite3", MemoryAnchor()
    )
    recovery = admit_scheduled_news_grasp_operation_in_store(
        store=store,
        issue_date="2026-08-03",
        operation_kind="scheduled_recovery",
    )
    assert recovery["attemptReservation"]["attemptId"] == "2026-08-03"
    count = store.db.execute(
        "SELECT COUNT(*) FROM events WHERE task_identity=? "
        "AND event_type='scheduled_production_reserved'",
        (recovery["taskIdentity"],),
    ).fetchone()[0]
    assert count == 1
    store.close()


def test_modified_scheduled_receipt_is_rejected_before_call_reservation(
    tmp_path,
) -> None:
    ControlError = high_cost.ControlError
    HighCostControlStore = high_cost.HighCostControlStore
    MemoryAnchor = high_cost.MemoryAnchor
    admit_scheduled_news_grasp_operation_in_store = _required_symbol(
        "admit_scheduled_news_grasp_operation_in_store",
        "RED_SCHED_RECEIPT_ISSUER_MISSING",
    )
    reserve_scheduled_model_call_in_store = _required_symbol(
        "reserve_scheduled_model_call_in_store",
        "RED_SCHED_RECEIPT_VALIDATOR_MISSING",
    )

    store = HighCostControlStore.create_for_test(
        tmp_path / "ledger.sqlite3", MemoryAnchor()
    )
    receipt = admit_scheduled_news_grasp_operation_in_store(
        store=store,
        issue_date="2026-08-03",
        operation_kind="scheduled_production",
    )
    modified = copy.deepcopy(receipt)
    modified["issueDate"] = "2026-08-04"
    with pytest.raises(ControlError, match="HIGH_COST_SCHEDULED_ADMISSION_INVALID"):
        reserve_scheduled_model_call_in_store(
            store=store,
            admission=modified,
            route="deepdive",
            call_id="forged",
        )
    assert store.call_count(receipt["taskIdentity"]) == 0
    store.close()


def test_scheduled_model_start_requires_same_receipt_reservation(tmp_path) -> None:
    ControlError = high_cost.ControlError
    HighCostControlStore = high_cost.HighCostControlStore
    MemoryAnchor = high_cost.MemoryAnchor
    admit_scheduled_news_grasp_operation_in_store = _required_symbol(
        "admit_scheduled_news_grasp_operation_in_store",
        "RED_SCHED_MODEL_START_ISSUER_MISSING",
    )
    mark_scheduled_model_call_started_in_store = _required_symbol(
        "mark_scheduled_model_call_started_in_store",
        "RED_SCHED_MODEL_START_TRACE_CONSUMER_MISSING",
    )
    reserve_scheduled_model_call_in_store = _required_symbol(
        "reserve_scheduled_model_call_in_store",
        "RED_SCHED_MODEL_START_RESERVATION_CONSUMER_MISSING",
    )

    store = HighCostControlStore.create_for_test(
        tmp_path / "ledger.sqlite3", MemoryAnchor()
    )
    receipt = admit_scheduled_news_grasp_operation_in_store(
        store=store,
        issue_date="2026-08-03",
        operation_kind="scheduled_production",
    )
    with pytest.raises(ControlError, match="HIGH_COST_MODEL_CALL_ADMISSION_REQUIRED"):
        mark_scheduled_model_call_started_in_store(
            store=store,
            admission=receipt,
            route="deepdive",
            call_id="call-1",
        )
    reserve_scheduled_model_call_in_store(
        store=store,
        admission=receipt,
        route="deepdive",
        call_id="call-1",
    )
    started = mark_scheduled_model_call_started_in_store(
        store=store,
        admission=receipt,
        route="deepdive",
        call_id="call-1",
    )
    assert started["processStarted"] is True
    store.close()


@pytest.mark.parametrize(
    ("admission", "expected_kind", "expected_issue_date"),
    [
        (
            {
                "schemaVersion": "HIGH_COST_SCHEDULED_OPERATION_ADMISSION_V1",
                "operationKind": "scheduled_production",
                "issueDate": "2026-08-04",
            },
            "scheduled_production",
            "2026-08-03",
        ),
        (
            {
                "schemaVersion": "HIGH_COST_SCHEDULED_OPERATION_ADMISSION_V1",
                "operationKind": "scheduled_recovery",
                "issueDate": "2026-08-03",
            },
            "scheduled_production",
            "2026-08-03",
        ),
        (
            {
                "schemaVersion": "HIGH_COST_OPERATION_ADMISSION_V2",
                "operationKind": "full_e2e",
            },
            "scheduled_production",
            "2026-08-03",
        ),
    ],
)
def test_execution_rejects_valid_receipt_substitution_before_reservation(
    admission,
    expected_kind,
    expected_issue_date,
) -> None:
    broker = _load_broker_module()

    with pytest.raises(
        broker.ModelSpawnDenied,
        match="HIGH_COST_OPERATION_ADMISSION_IDENTITY_MISMATCH",
    ):
        broker._validate_expected_operation_identity(
            admission,
            expected_operation_kind=expected_kind,
            expected_issue_date=expected_issue_date,
        )


def test_runner_validates_issue_date_before_any_date_scoped_write() -> None:
    runner = (
        Path(__file__).resolve().parents[1] / "scripts" / "ops" / "news-grasp-runner.ps1"
    ).read_text(encoding="utf-8-sig")

    validation = runner.index("NEWS_GRASP_DATE_STAMP_INVALID")
    assert validation < runner.index("$LogPath = Join-Path")
    assert validation < runner.index("build\\high-cost-operation-admissions")


def test_runner_wrapper_and_broker_bind_expected_operation_identity() -> None:
    root = Path(__file__).resolve().parents[1]
    runner = (root / "scripts" / "ops" / "news-grasp-runner.ps1").read_text(
        encoding="utf-8-sig"
    )
    wrapper = (root / "scripts" / "ops" / "run_codex_with_timeout.ps1").read_text(
        encoding="utf-8-sig"
    )

    assert "HighCostExpectedOperationKind" in runner
    assert "HighCostExpectedIssueDate" in runner
    assert "'--expected-operation-kind', $HighCostExpectedOperationKind" in wrapper
    assert "'--expected-issue-date', $HighCostExpectedIssueDate" in wrapper
    assert "Test-Path -LiteralPath $HighCostAdmissionPath -PathType Leaf" in wrapper


def test_sealed_cross_date_receipt_file_is_rejected_without_reservation(tmp_path) -> None:
    store = high_cost.HighCostControlStore.create_for_test(
        tmp_path / "ledger.sqlite3", high_cost.MemoryAnchor()
    )
    receipt = high_cost.admit_scheduled_news_grasp_operation_in_store(
        store=store,
        issue_date="2026-08-03",
        operation_kind="scheduled_production",
    )
    receipt_path = tmp_path / "sealed-admission.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    broker = _load_broker_module()

    with pytest.raises(
        broker.ModelSpawnDenied,
        match="HIGH_COST_OPERATION_ADMISSION_IDENTITY_MISMATCH",
    ):
        broker._load_operation_admission(
            receipt_path,
            expected_operation_kind="scheduled_production",
            expected_issue_date="2026-08-04",
        )

    assert store.call_count(receipt["taskIdentity"]) == 0
    store.close()


def test_scheduled_cli_cannot_mint_another_date_budget() -> None:
    broker = _load_broker_module()

    with pytest.raises(
        broker.ModelSpawnDenied,
        match="HIGH_COST_SCHEDULED_DATE_NOT_CURRENT",
    ):
        broker._validate_scheduled_cli_issue_date(
            "2026-08-04", current_issue_date="2026-08-03"
        )
