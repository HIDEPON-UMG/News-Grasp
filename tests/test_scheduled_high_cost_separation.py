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


def _issue_recovery_authority_in_store(store, issue_date: str):
    authority = high_cost.scheduled_news_grasp_authority(issue_date)
    if store.db.execute(
        "SELECT 1 FROM events WHERE request_id=?",
        (f"scheduled-production:{authority.task_identity}:{issue_date}",),
    ).fetchone() is None:
        high_cost.admit_scheduled_news_grasp_operation_in_store(
            store=store,
            issue_date=issue_date,
            operation_kind="scheduled_production",
        )
    failure = high_cost.freeze_scheduled_failure_receipt(
        issue_date=issue_date,
        last_task_result=76,
        runner_state="operation_rejected_high_cost_admission",
        state_sha256="3" * 64,
        log_sha256="4" * 64,
        task_action_sha256="1" * 64,
        runner_sha256="2" * 64,
    )
    store._append_event(
        request_id=f"test-failure:{authority.task_identity}:{issue_date}",
        event_type="scheduled_failure_frozen",
        task_identity=authority.task_identity,
        payload={
            "issueDate": issue_date,
            "failureReceiptSha256": failure["receiptSha256"],
            "source": "test",
        },
    )
    recovery_authority = high_cost.derive_scheduled_recovery_authority_in_store(
        store=store,
        issue_date=issue_date,
        mission_authority=_mission_authority(),
        failure_receipt=failure,
        run_intent="ScheduledRecoveryFull",
        current_task_action_sha256="5" * 64,
        current_runner_sha256="6" * 64,
    )
    return failure, recovery_authority


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

    _, recovery_authority = _issue_recovery_authority_in_store(store, "2026-08-03")
    recovery = admit_scheduled_news_grasp_operation_in_store(
        store=store,
        issue_date="2026-08-03",
        operation_kind="scheduled_recovery",
        authority_evidence=recovery_authority,
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
    _, recovery_authority = _issue_recovery_authority_in_store(store, "2026-08-03")
    recovery = admit_scheduled_news_grasp_operation_in_store(
        store=store,
        issue_date="2026-08-03",
        operation_kind="scheduled_recovery",
        authority_evidence=recovery_authority,
    )
    assert recovery["attemptReservation"]["attemptId"] == "2026-08-03"
    count = store.db.execute(
        "SELECT COUNT(*) FROM events WHERE task_identity=? "
        "AND event_type='scheduled_production_reserved'",
        (recovery["taskIdentity"],),
    ).fetchone()[0]
    assert count == 1
    store.close()


def test_unissued_self_sealed_recovery_authority_cannot_create_ledger_event(
    tmp_path,
) -> None:
    store = high_cost.HighCostControlStore.create_for_test(
        tmp_path / "ledger.sqlite3", high_cost.MemoryAnchor()
    )
    failure, issued = _issue_recovery_authority_in_store(store, "2026-08-03")
    forged = high_cost.derive_scheduled_recovery_authority(
        issue_date="2026-08-03",
        mission_authority=_mission_authority(),
        failure_receipt=failure,
        run_intent="ScheduledRecoveryFull",
        current_task_action_sha256="7" * 64,
        current_runner_sha256="8" * 64,
    )
    assert forged["receiptSha256"] != issued["receiptSha256"]
    before = store.db.execute(
        "SELECT COUNT(*) FROM events WHERE event_type='scheduled_recovery_admitted'"
    ).fetchone()[0]
    with pytest.raises(
        high_cost.ControlError, match="SCHEDULED_RECOVERY_AUTHORITY_LEDGER_INVALID"
    ):
        high_cost.admit_scheduled_news_grasp_operation_in_store(
            store=store,
            issue_date="2026-08-03",
            operation_kind="scheduled_recovery",
            authority_evidence=forged,
        )
    after = store.db.execute(
        "SELECT COUNT(*) FROM events WHERE event_type='scheduled_recovery_admitted'"
    ).fetchone()[0]
    assert after == before
    store.close()


def test_recovery_admission_rejects_failure_hash_substitution_and_replay(tmp_path) -> None:
    store = high_cost.HighCostControlStore.create_for_test(
        tmp_path / "ledger.sqlite3", high_cost.MemoryAnchor()
    )
    _, issued = _issue_recovery_authority_in_store(store, "2026-08-03")
    substituted = dict(issued)
    substituted.pop("receiptSha256")
    substituted["failureReceiptSha256"] = "9" * 64
    substituted = high_cost._sealed(substituted)
    with pytest.raises(
        high_cost.ControlError, match="SCHEDULED_RECOVERY_AUTHORITY_LEDGER_INVALID"
    ):
        high_cost.admit_scheduled_news_grasp_operation_in_store(
            store=store,
            issue_date="2026-08-03",
            operation_kind="scheduled_recovery",
            authority_evidence=substituted,
        )
    high_cost.admit_scheduled_news_grasp_operation_in_store(
        store=store,
        issue_date="2026-08-03",
        operation_kind="scheduled_recovery",
        authority_evidence=issued,
    )
    with pytest.raises(high_cost.ControlError, match="SCHEDULED_RECOVERY_ADMISSION_REPLAY"):
        high_cost.admit_scheduled_news_grasp_operation_in_store(
            store=store,
            issue_date="2026-08-03",
            operation_kind="scheduled_recovery",
            authority_evidence=issued,
        )
    count = store.db.execute(
        "SELECT COUNT(*) FROM events WHERE event_type='scheduled_recovery_admitted'"
    ).fetchone()[0]
    assert count == 1
    store.close()


def _terminal_recovery_state(*, issue_date: str = "2026-08-03") -> dict[str, object]:
    return {
        "status": "blocked_refill_unresolved",
        "message": "gate retry ledger denied repair worker",
        "exit_code": 1,
        "date": issue_date,
        "run_intent": "ScheduledRecoveryFull",
        "run_id": "d" * 32,
        "first_terminal_wins": "first-terminal-wins",
        "phase": "repair",
        "step": "gate retry ledger denied repair worker",
    }


def test_recovery_continuation_reuses_admission_and_remaining_budget(tmp_path) -> None:
    continuation_issuer = _required_symbol(
        "admit_scheduled_recovery_continuation_in_store",
        "RED_SCHEDULED_RECOVERY_CONTINUATION_ISSUER_MISSING",
    )
    store = high_cost.HighCostControlStore.create_for_test(
        tmp_path / "ledger.sqlite3", high_cost.MemoryAnchor()
    )
    _, authority = _issue_recovery_authority_in_store(store, "2026-08-03")
    recovery = high_cost.admit_scheduled_news_grasp_operation_in_store(
        store=store,
        issue_date="2026-08-03",
        operation_kind="scheduled_recovery",
        authority_evidence=authority,
    )

    continuation = continuation_issuer(
        store=store,
        admission=recovery,
        runner_state=_terminal_recovery_state(),
        resume_stage="deepdive",
    )

    assert continuation["schemaVersion"] == "HIGH_COST_SCHEDULED_RECOVERY_CONTINUATION_V1"
    assert continuation["sourceAdmissionReceiptSha256"] == recovery["receiptSha256"]
    assert continuation["sourceRunId"] == "d" * 32
    assert continuation["resumeStage"] == "deepdive"
    assert continuation["allowedModelRoutes"] == ["deepdive"]
    reserved = high_cost.reserve_scheduled_model_call_in_store(
        store=store,
        admission=continuation,
        route="deepdive",
        call_id="continuation-deepdive",
    )
    assert reserved["callCount"] == 1
    store.close()


def test_recovery_continuation_rejects_nonterminal_state_and_route_substitution(
    tmp_path,
) -> None:
    continuation_issuer = _required_symbol(
        "admit_scheduled_recovery_continuation_in_store",
        "RED_SCHEDULED_RECOVERY_CONTINUATION_ISSUER_MISSING",
    )
    store = high_cost.HighCostControlStore.create_for_test(
        tmp_path / "ledger.sqlite3", high_cost.MemoryAnchor()
    )
    _, authority = _issue_recovery_authority_in_store(store, "2026-08-03")
    recovery = high_cost.admit_scheduled_news_grasp_operation_in_store(
        store=store,
        issue_date="2026-08-03",
        operation_kind="scheduled_recovery",
        authority_evidence=authority,
    )
    running = _terminal_recovery_state()
    running.update({"status": "running", "exit_code": -1})
    with pytest.raises(
        high_cost.ControlError, match="SCHEDULED_RECOVERY_CONTINUATION_TERMINAL_REQUIRED"
    ):
        continuation_issuer(
            store=store,
            admission=recovery,
            runner_state=running,
            resume_stage="deepdive",
        )
    continuation = continuation_issuer(
        store=store,
        admission=recovery,
        runner_state=_terminal_recovery_state(),
        resume_stage="deepdive",
    )
    with pytest.raises(
        high_cost.ControlError, match="SCHEDULED_RECOVERY_CONTINUATION_ROUTE_FORBIDDEN"
    ):
        high_cost.reserve_scheduled_model_call_in_store(
            store=store,
            admission=continuation,
            route="reporter:fx",
            call_id="forbidden-reporter",
        )
    assert store.call_count(recovery["taskIdentity"]) == 0
    store.close()


def test_recovery_continuation_is_single_use_per_source_run_and_stage(tmp_path) -> None:
    continuation_issuer = _required_symbol(
        "admit_scheduled_recovery_continuation_in_store",
        "RED_SCHEDULED_RECOVERY_CONTINUATION_ISSUER_MISSING",
    )
    store = high_cost.HighCostControlStore.create_for_test(
        tmp_path / "ledger.sqlite3", high_cost.MemoryAnchor()
    )
    _, authority = _issue_recovery_authority_in_store(store, "2026-08-03")
    recovery = high_cost.admit_scheduled_news_grasp_operation_in_store(
        store=store,
        issue_date="2026-08-03",
        operation_kind="scheduled_recovery",
        authority_evidence=authority,
    )
    state = _terminal_recovery_state()
    continuation_issuer(
        store=store,
        admission=recovery,
        runner_state=state,
        resume_stage="deepdive",
    )
    with pytest.raises(
        high_cost.ControlError, match="SCHEDULED_RECOVERY_CONTINUATION_REPLAY"
    ):
        continuation_issuer(
            store=store,
            admission=recovery,
            runner_state=state,
            resume_stage="deepdive",
        )
    store.close()


def test_recovery_continuation_chains_only_into_generation_quality_repair(
    tmp_path,
) -> None:
    continuation_issuer = _required_symbol(
        "admit_scheduled_recovery_continuation_in_store",
        "RED_SCHEDULED_RECOVERY_CONTINUATION_ISSUER_MISSING",
    )
    store = high_cost.HighCostControlStore.create_for_test(
        tmp_path / "ledger.sqlite3", high_cost.MemoryAnchor()
    )
    _, authority = _issue_recovery_authority_in_store(store, "2026-08-03")
    recovery = high_cost.admit_scheduled_news_grasp_operation_in_store(
        store=store,
        issue_date="2026-08-03",
        operation_kind="scheduled_recovery",
        authority_evidence=authority,
    )
    first = continuation_issuer(
        store=store,
        admission=recovery,
        runner_state=_terminal_recovery_state(),
        resume_stage="deepdive",
    )
    chained_state = _terminal_recovery_state()
    chained_state.update(
        {
            "status": "error",
            "run_id": "e" * 32,
            "message": "generation quality autonomous gate failed",
        }
    )
    chained_state.pop("phase")
    second = continuation_issuer(
        store=store,
        admission=first,
        runner_state=chained_state,
        resume_stage="generation-quality-repair",
    )
    assert second["sourceAdmissionReceiptSha256"] == first["receiptSha256"]
    assert second["allowedModelRoutes"] == ["repair:generation-quality"]
    with pytest.raises(
        high_cost.ControlError,
        match="SCHEDULED_RECOVERY_CONTINUATION_ROUTE_FORBIDDEN",
    ):
        high_cost.reserve_scheduled_model_call_in_store(
            store=store,
            admission=second,
            route="deepdive",
            call_id="forbidden-deepdive-rerun",
        )
    reserved = high_cost.reserve_scheduled_model_call_in_store(
        store=store,
        admission=second,
        route="repair:generation-quality",
        call_id="missing-deepdive-repair",
    )
    assert reserved["callCount"] == 1
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


MISSION_EVENTS = [
    {
        "eventSha256": "021a893039bbfabeebfe366d985bd37a5bd3a99f3c8edb939007ea76e0b6868d",
        "purpose": "audit_recover_publish_report",
    },
    {
        "eventSha256": "6926615fce93fdba64bbd43af82bb3ef71df22e4569f8bd96787f64c2863b03e",
        "purpose": "mandatory_repair_and_report_skills",
    },
    {
        "eventSha256": "81bcd6403a58cd11b51812a0d6be2e201985245f40a83b9dc31ffa585d428017",
        "purpose": "abnormal_stop_recover_fix_publish",
    },
]


def _mission_authority():
    issuer = _required_symbol(
        "issue_news_grasp_audit_mission_authority",
        "RED_AUDIT_MISSION_AUTHORITY_ISSUER_MISSING",
    )
    return issuer(MISSION_EVENTS)


def test_scheduled_production_launch_permit_binds_mission_task_runner_and_nonce() -> None:
    issuer = _required_symbol(
        "issue_scheduled_production_launch_permit",
        "RED_SCHEDULED_LAUNCH_PERMIT_ISSUER_MISSING",
    )
    permit = issuer(
        issue_date="2026-08-02",
        task_action_sha256="1" * 64,
        runner_sha256="2" * 64,
        launch_nonce="bootstrap-20260802-0555",
        mission_authority=_mission_authority(),
    )
    assert permit["schemaVersion"] == "SCHEDULED_PRODUCTION_LAUNCH_PERMIT_V1"
    assert permit["issueDate"] == "2026-08-02"
    assert permit["taskActionSha256"] == "1" * 64
    assert permit["runnerSha256"] == "2" * 64
    assert permit["maxExternalModelCalls"] == 9
    assert permit["maxFullE2EAttempts"] == 0
    assert permit["receiptSha256"]


def test_failure_receipt_freezes_scheduled_failure_without_recovery_overwrite() -> None:
    freezer = _required_symbol(
        "freeze_scheduled_failure_receipt",
        "RED_SCHEDULED_FAILURE_RECEIPT_PRODUCER_MISSING",
    )
    receipt = freezer(
        issue_date="2026-08-02",
        last_task_result=76,
        runner_state="operation_rejected_high_cost_admission",
        state_sha256="3" * 64,
        log_sha256="4" * 64,
        task_action_sha256="1" * 64,
        runner_sha256="2" * 64,
    )
    assert receipt["schemaVersion"] == "SCHEDULED_FAILURE_RECEIPT_V1"
    assert receipt["scheduledAttemptStatus"] == "failed"
    assert "recoveryAttemptStatus" not in receipt


def test_recovery_authority_requires_installed_mission_and_same_date_failure() -> None:
    derive = _required_symbol(
        "derive_scheduled_recovery_authority",
        "RED_SCHEDULED_RECOVERY_AUTHORITY_DERIVER_MISSING",
    )
    freezer = _required_symbol(
        "freeze_scheduled_failure_receipt",
        "RED_SCHEDULED_FAILURE_RECEIPT_PRODUCER_MISSING",
    )
    failure = freezer(
        issue_date="2026-08-02",
        last_task_result=76,
        runner_state="operation_rejected_high_cost_admission",
        state_sha256="3" * 64,
        log_sha256="4" * 64,
        task_action_sha256="1" * 64,
        runner_sha256="2" * 64,
    )
    authority = derive(
        issue_date="2026-08-02",
        mission_authority=_mission_authority(),
        failure_receipt=failure,
        run_intent="ScheduledRecoveryFull",
    )
    assert authority["schemaVersion"] == "SCHEDULED_RECOVERY_AUTHORITY_V1"
    assert authority["failureReceiptSha256"] == failure["receiptSha256"]
    assert authority["maxExternalModelCalls"] == 9
    assert authority["maxFullE2EAttempts"] == 0


def test_recovery_authority_rejects_cross_date_substitution() -> None:
    derive = _required_symbol(
        "derive_scheduled_recovery_authority",
        "RED_SCHEDULED_RECOVERY_AUTHORITY_DERIVER_MISSING",
    )
    freezer = _required_symbol(
        "freeze_scheduled_failure_receipt",
        "RED_SCHEDULED_FAILURE_RECEIPT_PRODUCER_MISSING",
    )
    failure = freezer(
        issue_date="2026-08-01",
        last_task_result=76,
        runner_state="operation_rejected_high_cost_admission",
        state_sha256="3" * 64,
        log_sha256="4" * 64,
        task_action_sha256="1" * 64,
        runner_sha256="2" * 64,
    )
    with pytest.raises(high_cost.ControlError, match="SCHEDULED_RECOVERY_DATE_MISMATCH"):
        derive(
            issue_date="2026-08-02",
            mission_authority=_mission_authority(),
            failure_receipt=failure,
            run_intent="ScheduledRecoveryFull",
        )


def test_legacy_pre_admission_failure_import_is_exact_and_one_time(tmp_path) -> None:
    importer = _required_symbol(
        "import_legacy_pre_admission_failure_in_store",
        "RED_LEGACY_PRE_ADMISSION_IMPORTER_MISSING",
    )
    store = high_cost.HighCostControlStore.create_for_test(
        tmp_path / "ledger.sqlite3", high_cost.MemoryAnchor()
    )
    evidence = {
        "schemaVersion": "OBSERVED_LEGACY_PRE_ADMISSION_FAILURE_V1",
        "issueDate": "2026-08-02",
        "lastTaskResult": 76,
        "runnerState": "operation_rejected_high_cost_admission",
        "stateSha256": "f58e1e59198c675ff3df7394c0a20215f78a2d9d86ea2ac74e3981ba6d0862cd",
        "logSha256": "ffbb7ba276dc2fcb407271bc7f41ce71ec44085146061b8f7b2492aaacdaae12",
        "taskActionSha256": "9904c375a66604a67644db38543b4aec3060da28b781d642289e2e93195e6204",
        "runnerSha256": "d87728c3b56d5e1492780da0a1250f1031d7672efb7211b6bb9c523d2b8f2ee2",
    }
    imported = importer(store=store, evidence=evidence)
    assert imported["schemaVersion"] == "SCHEDULED_FAILURE_RECEIPT_V1"
    with pytest.raises(high_cost.ControlError, match="LEGACY_PRE_ADMISSION_REPLAY"):
        importer(store=store, evidence=evidence)
    store.close()


def test_broker_cli_requires_authority_evidence_for_scheduled_admission() -> None:
    broker = _load_broker_module()
    parser_source = BROKER_PATH.read_text(encoding="utf-8-sig")
    assert "--authority-evidence" in parser_source, "RED_BROKER_AUTHORITY_ARGUMENT_MISSING"
    assert "SCHEDULED_OPERATION_AUTHORITY_REQUIRED" in parser_source


def test_bootstrap_issues_same_date_mission_and_launch_permit() -> None:
    root = Path(__file__).resolve().parents[1]
    bootstrap = (root / "scripts" / "ops" / "news-grasp-bootstrap.ps1").read_text(
        encoding="utf-8-sig"
    )
    assert "issue-news-grasp-audit-mission" in bootstrap
    assert "issue-news-grasp-launch-permit" in bootstrap
    assert "Get-ScheduledTaskActionSha256" in bootstrap
    assert "'--runner-sha256' $runnerSha256" in bootstrap


def test_runner_consumes_typed_scheduled_authority_and_separates_recovery_state() -> None:
    root = Path(__file__).resolve().parents[1]
    runner = (root / "scripts" / "ops" / "news-grasp-runner.ps1").read_text(
        encoding="utf-8-sig"
    )
    assert "[ValidateSet('ScheduledProduction', 'ScheduledRecoveryFull')]" in runner
    assert "ScheduledAuthorityEvidencePath" in runner
    assert "--authority-evidence" in runner
    assert "--expected-task-action-sha256" in runner
    assert "--expected-runner-sha256" in runner
    assert "$operationKind = 'scheduled_recovery'" in runner


def test_runner_resume_uses_broker_issued_recovery_continuation() -> None:
    root = Path(__file__).resolve().parents[1]
    runner = (root / "scripts" / "ops" / "news-grasp-runner.ps1").read_text(
        encoding="utf-8-sig"
    )
    assert "admit-news-grasp-recovery-continuation" in runner
    assert "SCHEDULED_RECOVERY_CONTINUATION_SOURCE_ADMISSION_REQUIRED" in runner
    assert "HIGH_COST_SCHEDULED_RECOVERY_CONTINUATION_V1" in runner
    assert "--runner-state" in runner
    assert "--resume-stage" in runner
    assert "generation-quality-repair" in runner
    assert "generation-quality gate owns missing artifact repair" in runner
    artifact_guards = [
        line
        for line in runner.splitlines()
        if "Test-DailyArtifactsExist -TargetDate $DateStamp" in line
    ]
    assert len(artifact_guards) == 2
    assert all("(-not $ResumeGenerationQualityRepair)" in line for line in artifact_guards)


def test_installer_seals_recurring_audit_mission_authority() -> None:
    root = Path(__file__).resolve().parents[1]
    installer = (root / "scripts" / "ops" / "install-news-grasp-ops.ps1").read_text(
        encoding="utf-8-sig"
    )
    assert "issue-news-grasp-audit-mission" in installer
    assert "audit-mission-authority-v1.json" in installer
    assert "mission_authority = [ordered]@{" in installer
    assert "schema = 'AUDIT_MISSION_AUTHORITY_V1'" in installer


def test_recovery_authority_must_exist_in_canonical_ledger(tmp_path) -> None:
    issuer = _required_symbol(
        "derive_scheduled_recovery_authority_in_store",
        "RED_RECOVERY_AUTHORITY_LEDGER_ISSUER_MISSING",
    )
    validator = _required_symbol(
        "validate_scheduled_recovery_authority_in_store",
        "RED_RECOVERY_AUTHORITY_LEDGER_VALIDATOR_MISSING",
    )
    importer = _required_symbol(
        "import_legacy_pre_admission_failure_in_store",
        "RED_LEGACY_PRE_ADMISSION_IMPORTER_MISSING",
    )
    store = high_cost.HighCostControlStore.create_for_test(
        tmp_path / "ledger.sqlite3", high_cost.MemoryAnchor()
    )
    failure = importer(
        store=store,
        evidence={
            "schemaVersion": "OBSERVED_LEGACY_PRE_ADMISSION_FAILURE_V1",
            "issueDate": "2026-08-02",
            "lastTaskResult": 76,
            "runnerState": "operation_rejected_high_cost_admission",
            "stateSha256": "f58e1e59198c675ff3df7394c0a20215f78a2d9d86ea2ac74e3981ba6d0862cd",
            "logSha256": "ffbb7ba276dc2fcb407271bc7f41ce71ec44085146061b8f7b2492aaacdaae12",
            "taskActionSha256": "9904c375a66604a67644db38543b4aec3060da28b781d642289e2e93195e6204",
            "runnerSha256": "d87728c3b56d5e1492780da0a1250f1031d7672efb7211b6bb9c523d2b8f2ee2",
        },
    )
    authority = issuer(
        store=store,
        issue_date="2026-08-02",
        mission_authority=_mission_authority(),
        failure_receipt=failure,
        run_intent="ScheduledRecoveryFull",
        current_task_action_sha256="5" * 64,
        current_runner_sha256="6" * 64,
    )
    witness = validator(
        store=store,
        authority_evidence=authority,
        issue_date="2026-08-02",
        failure_receipt_sha256=failure["receiptSha256"],
    )
    assert witness["schemaVersion"] == "SCHEDULED_RECOVERY_AUTHORITY_LEDGER_WITNESS_V1"
    forged = dict(authority)
    forged["receiptSha256"] = "f" * 64
    with pytest.raises(high_cost.ControlError, match="SCHEDULED_RECOVERY_AUTHORITY_LEDGER_INVALID"):
        validator(
            store=store,
            authority_evidence=forged,
            issue_date="2026-08-02",
            failure_receipt_sha256=failure["receiptSha256"],
        )
    store.close()


def test_attempt_status_is_derived_from_canonical_ledger_not_caller_text(tmp_path) -> None:
    inspect_attempt = _required_symbol(
        "inspect_scheduled_news_grasp_attempt_in_store",
        "RED_SCHEDULED_ATTEMPT_LEDGER_WITNESS_MISSING",
    )
    importer = _required_symbol(
        "import_legacy_pre_admission_failure_in_store",
        "RED_LEGACY_PRE_ADMISSION_IMPORTER_MISSING",
    )
    store = high_cost.HighCostControlStore.create_for_test(
        tmp_path / "ledger.sqlite3", high_cost.MemoryAnchor()
    )
    failure = importer(
        store=store,
        evidence={
            "schemaVersion": "OBSERVED_LEGACY_PRE_ADMISSION_FAILURE_V1",
            "issueDate": "2026-08-02",
            "lastTaskResult": 76,
            "runnerState": "operation_rejected_high_cost_admission",
            "stateSha256": "f58e1e59198c675ff3df7394c0a20215f78a2d9d86ea2ac74e3981ba6d0862cd",
            "logSha256": "ffbb7ba276dc2fcb407271bc7f41ce71ec44085146061b8f7b2492aaacdaae12",
            "taskActionSha256": "9904c375a66604a67644db38543b4aec3060da28b781d642289e2e93195e6204",
            "runnerSha256": "d87728c3b56d5e1492780da0a1250f1031d7672efb7211b6bb9c523d2b8f2ee2",
        },
    )
    witness = inspect_attempt(store=store, issue_date="2026-08-02")
    assert witness["schemaVersion"] == "SCHEDULED_ATTEMPT_LEDGER_WITNESS_V1"
    assert witness["scheduledAttemptStatus"] == "failed"
    assert witness["failureReceiptSha256"] == failure["receiptSha256"]
    assert witness["scheduledEventSequence"] > 0
    assert witness["scheduledEventHash"]
    authority = high_cost.derive_scheduled_recovery_authority_in_store(
        store=store,
        issue_date="2026-08-02",
        mission_authority=_mission_authority(),
        failure_receipt=failure,
        run_intent="ScheduledRecoveryFull",
        current_task_action_sha256="5" * 64,
        current_runner_sha256="6" * 64,
    )
    high_cost.admit_scheduled_news_grasp_operation_in_store(
        store=store,
        issue_date="2026-08-02",
        operation_kind="scheduled_recovery",
        authority_evidence=authority,
    )
    recovery_witness = inspect_attempt(store=store, issue_date="2026-08-02")
    assert recovery_witness["recoveryAttemptStatus"] == "started"
    assert (
        recovery_witness["recoveryAuthorityReceiptSha256"]
        == authority["receiptSha256"]
    )
    assert recovery_witness["recoveryEventSequence"] > witness["scheduledEventSequence"]
    store.close()


def test_broker_exposes_attempt_ledger_inspection_command() -> None:
    source = BROKER_PATH.read_text(encoding="utf-8-sig")
    assert 'sub.add_parser("inspect-news-grasp-attempt")' in source
    assert "inspect_scheduled_news_grasp_attempt_in_store" in source


def test_broker_exposes_ledger_backed_recovery_authority_validation() -> None:
    parser_source = BROKER_PATH.read_text(encoding="utf-8-sig")
    assert "validate-news-grasp-recovery-authority" in parser_source
    assert "validate_scheduled_recovery_authority_in_store" in parser_source
