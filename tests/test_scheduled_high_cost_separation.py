from __future__ import annotations

import copy
import ctypes
import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader

import pytest
from tools import harness as workspace_harness
import tools.harness.high_cost_control_v2 as high_cost


BROKER_PATH = (
    workspace_harness.resolve_workspace_harness_path()
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
        run_id="scheduled-high-cost-separation",
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


def test_recovery_reclaims_only_unleased_reporter_reservations(tmp_path) -> None:
    store = high_cost.HighCostControlStore.create_for_test(
        tmp_path / "ledger.sqlite3", high_cost.MemoryAnchor()
    )
    _, recovery_authority = _issue_recovery_authority_in_store(store, "2026-08-03")
    recovery = high_cost.admit_scheduled_news_grasp_operation_in_store(
        store=store,
        issue_date="2026-08-03",
        operation_kind="scheduled_recovery",
        authority_evidence=recovery_authority,
    )
    source_run_id = "a" * 32
    leased = high_cost.reserve_scheduled_model_call_in_store(
        store=store,
        admission=recovery,
        route="reporter:ai",
        call_id=f"{source_run_id}:reporter:ai:1",
    )
    high_cost.issue_model_process_lease_in_store(
        store=store,
        reservation=leased,
        api="pytest",
    )
    row = store.db.execute(
        "SELECT authorization_id,max_calls,admission_digest FROM tasks WHERE task_identity=?",
        (recovery["taskIdentity"],),
    ).fetchone()
    store.reserve_call(
        {
            "authorizationId": row["authorization_id"],
            "taskIdentity": recovery["taskIdentity"],
            "maxExternalModelCalls": row["max_calls"],
            "admissionDigest": row["admission_digest"],
        },
        call_id=f"{source_run_id}:reporter:fx:1",
        request_id=f"model:{recovery['taskIdentity']}:reporter:fx:{source_run_id}:reporter:fx:1",
    )

    result = high_cost.reclaim_unleased_scheduled_recovery_reservations_in_store(
        store=store,
        admission=recovery,
        runner_state={
            "date": "2026-08-03",
            "run_intent": "ScheduledRecoveryFull",
            "run_id": source_run_id,
            "status": "error",
            "first_terminal_wins": "first-terminal-wins",
        },
    )
    row = store.db.execute(
        "SELECT call_count FROM tasks WHERE task_identity=?",
        (recovery["taskIdentity"],),
    ).fetchone()

    assert result["reclaimedCallCount"] == 1
    assert row["call_count"] == 1
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


def _startup_failed_recovery_state(*, issue_date: str = "2026-08-03") -> dict[str, object]:
    return {
        "status": "blocked_startup_self_repair_failed",
        "message": "pre-run bootstrap failed exit=1",
        "exit_code": 72,
        "date": issue_date,
        "run_intent": "ScheduledRecoveryFull",
        "run_id": "e" * 32,
        "first_terminal_wins": "first-terminal-wins",
        "phase": "startup",
        "step": "bootstrap_interlock",
    }


def test_startup_failed_recovery_can_reenter_same_attempt_once_without_budget_reset(
    tmp_path,
) -> None:
    reconcile = _required_symbol(
        "reconcile_scheduled_recovery_startup_failure_in_store",
        "RED_RECOVERY_STARTUP_REENTRY_RECONCILER_MISSING",
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

    permit = reconcile(
        store=store,
        admission=recovery,
        runner_state=_startup_failed_recovery_state(),
    )
    assert permit["schemaVersion"] == "SCHEDULED_RECOVERY_STARTUP_REENTRY_PERMIT_V1"
    assert permit["maxAdditionalModelCalls"] == 0
    restarted = high_cost.admit_scheduled_news_grasp_operation_in_store(
        store=store,
        issue_date="2026-08-03",
        operation_kind="scheduled_recovery",
        authority_evidence=authority,
    )
    assert restarted == recovery
    assert store.call_count(recovery["taskIdentity"]) == 0
    with pytest.raises(high_cost.ControlError, match="SCHEDULED_RECOVERY_ADMISSION_REPLAY"):
        high_cost.admit_scheduled_news_grasp_operation_in_store(
            store=store,
            issue_date="2026-08-03",
            operation_kind="scheduled_recovery",
            authority_evidence=authority,
        )
    store.close()


def test_startup_reentry_rejects_nonstartup_failure_and_any_reserved_model_call(
    tmp_path,
) -> None:
    reconcile = _required_symbol(
        "reconcile_scheduled_recovery_startup_failure_in_store",
        "RED_RECOVERY_STARTUP_REENTRY_RECONCILER_MISSING",
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
    wrong_state = _startup_failed_recovery_state()
    wrong_state["status"] = "failed_daily_quality"
    with pytest.raises(
        high_cost.ControlError, match="SCHEDULED_RECOVERY_STARTUP_FAILURE_REQUIRED"
    ):
        reconcile(store=store, admission=recovery, runner_state=wrong_state)

    high_cost.reserve_scheduled_model_call_in_store(
        store=store,
        admission=recovery,
        route="reporter:ai",
        call_id="already-consumed",
    )
    with pytest.raises(
        high_cost.ControlError, match="SCHEDULED_RECOVERY_STARTUP_REENTRY_BUDGET_DIRTY"
    ):
        reconcile(
            store=store,
            admission=recovery,
            runner_state=_startup_failed_recovery_state(),
        )
    store.close()


def test_broker_exposes_typed_recovery_startup_reconcile_command() -> None:
    source = BROKER_PATH.read_text(encoding="utf-8")
    assert "reconcile-news-grasp-recovery-startup" in source, (
        "RED_RECOVERY_STARTUP_RECONCILE_COMMAND_MISSING"
    )


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


def test_post_reporter_recovery_continuation_allows_only_remaining_generation_routes(tmp_path) -> None:
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
        resume_stage="post-reporter",
    )

    assert continuation["resumeStage"] == "post-reporter"
    assert continuation["allowedModelRoutes"] == [
        "newsroom_editor",
        "deepdive",
        "repair:daily-quality",
    ]
    high_cost.reserve_scheduled_model_call_in_store(
        store=store,
        admission=continuation,
        route="newsroom_editor",
        call_id="continuation-editor",
    )
    with pytest.raises(
        high_cost.ControlError, match="SCHEDULED_RECOVERY_CONTINUATION_ROUTE_FORBIDDEN"
    ):
        high_cost.reserve_scheduled_model_call_in_store(
            store=store,
            admission=continuation,
            route="reporter:ai",
            call_id="forbidden-reporter",
        )
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
    assert second["allowedModelRoutes"] == [
        "repair:daily-quality",
        "repair:generation-quality",
    ]
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


def test_incident_publication_repair_adds_one_scoped_call_after_exhaustion(
    tmp_path,
) -> None:
    issuer = _required_symbol(
        "admit_news_grasp_incident_publication_repair_in_store",
        "RED_NEWS_GRASP_INCIDENT_REPAIR_ISSUER_MISSING",
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
    for index in range(9):
        high_cost.reserve_scheduled_model_call_in_store(
            store=store,
            admission=recovery,
            route="deepdive",
            call_id=f"used-{index}",
        )
    artifacts = {
        "digest/DeepDive/2026-08-03-DeepDive.md": "1" * 64,
        "digest/Summary/2026-08-03-audio-script.md": "2" * 64,
    }
    incident = issuer(
        store=store,
        admission=recovery,
        runner_state=_terminal_recovery_state(),
        artifact_hashes=artifacts,
    )
    assert incident["schemaVersion"] == "HIGH_COST_SCHEDULED_INCIDENT_REPAIR_V1"
    assert incident["maxExternalModelCalls"] == 10
    assert incident["allowedModelRoutes"] == ["repair:incident-publication"]
    with pytest.raises(
        high_cost.ControlError,
        match="SCHEDULED_RECOVERY_CONTINUATION_ROUTE_FORBIDDEN",
    ):
        high_cost.reserve_scheduled_model_call_in_store(
            store=store,
            admission=incident,
            route="deepdive",
            call_id="forbidden-rerun",
        )
    final_call = high_cost.reserve_scheduled_model_call_in_store(
        store=store,
        admission=incident,
        route="repair:incident-publication",
        call_id="incident-repair",
    )
    assert final_call["callCount"] == 10
    zero_model_continuation = high_cost.admit_scheduled_recovery_continuation_in_store(
        store=store,
        admission=incident,
        runner_state=_terminal_recovery_state(),
        resume_stage="post-deepdive",
    )
    assert zero_model_continuation["maxExternalModelCalls"] == 10
    assert zero_model_continuation["allowedModelRoutes"] == []
    with pytest.raises(high_cost.ControlError, match="HIGH_COST_CALL_BUDGET_EXHAUSTED"):
        high_cost.reserve_scheduled_model_call_in_store(
            store=store,
            admission=incident,
            route="repair:incident-publication",
            call_id="incident-repair-replay-bypass",
        )
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


def test_direct_title_control_validates_issue_date_before_receipt_write(tmp_path) -> None:
    from tools.news_grasp_title_control import TitleControlError, record_title_status

    output = tmp_path / "title-status.json"
    with pytest.raises(TitleControlError, match="TITLE_ISSUE_DATE_INVALID"):
        record_title_status(
            issue_date="2026-8-3",
            status="failed",
            actual_title="",
            reason="fixture",
            post_publish_issue_list=[],
            output_path=output,
        )
    assert not output.exists()


def test_direct_mainline_keeps_broker_operation_identity_without_legacy_runner() -> None:
    root = Path(__file__).resolve().parents[1]
    wrapper = (root / "scripts" / "ops" / "run_codex_with_timeout.ps1").read_text(
        encoding="utf-8-sig"
    )

    assert not (root / "scripts" / "ops" / "news-grasp-runner.ps1").exists()
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
            expected_route="scheduled-production-test",
            expected_command_sha256="a" * 64,
            expected_call_id="scheduled-cross-date-test",
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
        run_id="freeze-scheduled-failure",
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
        run_id="recovery-authority-same-date",
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
        run_id="recovery-authority-cross-date",
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


def test_bootstrap_reserves_and_freezes_failure_before_production_runtime_can_abort() -> None:
    """remote fetch/worktree failureも同日attempt ledgerの外へ脱落させない。"""
    root = Path(__file__).resolve().parents[1]
    bootstrap = (root / "scripts" / "ops" / "news-grasp-bootstrap.ps1").read_text(
        encoding="utf-8-sig"
    )
    resolve_runtime = bootstrap.index("Resolve-ProductionRuntimeRepo -SourceRepoDir")
    preliminary_permit = bootstrap.index("Write-PreliminaryLaunchPermit")
    assert preliminary_permit < resolve_runtime
    assert "function Record-StartupFailureForAudit" in bootstrap
    terminalizer = bootstrap.split("function Record-StartupFailureForAudit", 1)[1].split(
        "function Resolve-ProductionRuntimeRepo", 1
    )[0]
    assert "'admit'" in terminalizer
    assert "'record-news-grasp-failure'" in terminalizer
    assert "scheduled-failure-receipts" in terminalizer
    assert "blocked_startup_self_repair_failed" in terminalizer


def test_bootstrap_authenticates_task_context_serializes_runtime_and_bounds_fetch() -> None:
    """任意手動起動、並行worktree更新、対話git hangをscheduled attemptへ入れない。"""
    root = Path(__file__).resolve().parents[1]
    bootstrap = (root / "scripts" / "ops" / "news-grasp-bootstrap.ps1").read_text(
        encoding="utf-8-sig"
    )
    launcher = (root / "scripts" / "ops" / "news-grasp-task-launcher.pyw").read_text(
        encoding="utf-8-sig"
    )
    assert "ScheduledTaskName" in bootstrap
    assert "Assert-ScheduledTaskLaunchContext" in bootstrap
    assert "Get-ScheduledTaskInfo" in bootstrap
    assert "News-Grasp Bootstrap" in bootstrap
    assert "News-Grasp Runner" in bootstrap
    assert "news-grasp-task-launcher.pyw" in bootstrap
    assert "Global\\NewsGraspBootstrapOrchestration" in bootstrap
    assert "WaitOne(0)" in bootstrap
    assert "AbandonedMutexException" in bootstrap
    assert "finally" in bootstrap.split("$runtimeMutex =", 1)[1]
    assert "GIT_TERMINAL_PROMPT" in bootstrap
    assert "WaitForExit(60000)" in bootstrap
    assert '_CLEANROOM_CONTEXT_TASK_NAME = "News-Grasp Production"' in launcher
    assert '"dispatch"' in launcher
    assert '"--schedule-id"' in launcher
    assert '"--intent"' in launcher


def test_local_authority_and_smoke_artifacts_are_never_commit_candidates() -> None:
    ignore = (Path(__file__).resolve().parents[1] / ".gitignore").read_text(
        encoding="utf-8-sig"
    )
    for entry in (
        "scripts/ops/news-grasp-authority/",
        "scripts/ops/ng-smoke-logs/",
        "scripts/ops/ng-smoke-state.json",
        "build/audit-recovery/",
        "build/high-cost-operation-admissions/",
    ):
        assert entry in ignore


def test_scheduled_launcher_enters_clean_production_runtime_and_smoke_is_fail_closed() -> None:
    root = Path(__file__).resolve().parents[1]
    launcher = (root / "scripts" / "ops" / "news-grasp-task-launcher.pyw").read_text(
        encoding="utf-8-sig"
    )
    bootstrap = (root / "scripts" / "ops" / "news-grasp-bootstrap.ps1").read_text(
        encoding="utf-8-sig"
    )
    assert 'def run_cleanroom_dispatch(' in launcher
    assert 'def _cleanroom_default_task_context_validator(' in launcher
    assert '"owned_process_module"' in launcher
    assert '"-I"' in launcher and '"-S"' in launcher and '"-B"' in launcher
    assert "[switch] $UseProductionRuntime" in bootstrap
    assert "Resolve-ProductionRuntimeRepo" in bootstrap
    assert "production-runtime" in launcher
    assert '"worktree", "add", "--detach"' in launcher
    assert "origin/main" in bootstrap
    assert "converge-runtime" in bootstrap
    assert "PRODUCTION_RUNTIME_RECOVERY_V1" in launcher
    assert bootstrap.count("'news-grasp-task-launcher.pyw'") >= 2


def test_production_runtime_dirty_gate_separates_source_drift_from_runtime_state() -> None:
    launcher = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "ops"
        / "news-grasp-task-launcher.pyw"
    ).read_text(encoding="utf-8-sig")
    resolver = launcher.split("def _runtime_state", 1)[1].split(
        "def _load_runtime_recovery_journal", 1
    )[0]

    assert "status --porcelain=v1 --untracked-files=normal" not in resolver
    assert '"diff",' in resolver
    assert '"--ignore-cr-at-eol"' in resolver
    assert '"ls-files", "--others", "--exclude-standard", "-z"' in resolver
    assert 'not item.startswith("build/")' in resolver
    assert "MAX_UNTRACKED_PATHS" in resolver


def _load_task_launcher_module():
    launcher_path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "ops"
        / "news-grasp-task-launcher.pyw"
    )
    loader = SourceFileLoader("news_grasp_task_launcher_runtime_test", str(launcher_path))
    spec = spec_from_loader(loader.name, loader)
    assert spec is not None
    module = module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_runtime_recovery_removes_readonly_managed_path(tmp_path: Path) -> None:
    """managed quarantineの読み取り専用payloadも正規maintenanceで回収できる。"""
    launcher = _load_task_launcher_module()
    target = tmp_path / "readonly-payload.txt"
    target.write_text("quarantined\n", encoding="utf-8")
    target.chmod(stat.S_IREAD)

    launcher._remove_runtime_path(target)

    assert not target.exists()


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        [r"C:\Program Files\Git\cmd\git.exe", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


def _runtime_fixture(tmp_path: Path) -> tuple[object, Path, Path, str]:
    launcher = _load_task_launcher_module()
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init")
    _git(source, "config", "user.email", "test@example.invalid")
    _git(source, "config", "user.name", "News Grasp Test")
    (source / "tools").mkdir()
    (source / "tools" / "daily_self_heal.py").write_text("# fixture\n", encoding="utf-8")
    (source / "tracked.txt").write_text("clean\n", encoding="utf-8")
    _git(source, "add", ".")
    _git(source, "commit", "-m", "fixture")
    origin_sha = _git(source, "rev-parse", "HEAD")
    runtime_root = tmp_path / ".news-grasp-runtime"
    runtime_root.mkdir()
    _git(source, "worktree", "add", "--detach", str(runtime_root / "production-runtime"), origin_sha)
    return launcher, source, runtime_root, origin_sha


def test_dirty_production_runtime_is_quarantined_and_replaced_without_data_loss(
    tmp_path: Path,
) -> None:
    launcher, source, runtime_root, origin_sha = _runtime_fixture(tmp_path)
    runtime = runtime_root / "production-runtime"
    (runtime / "tracked.txt").write_text("dirty preserved\n", encoding="utf-8")

    result = launcher.converge_production_runtime(
        source_repo=source,
        runtime_root=runtime_root,
        origin_sha=origin_sha,
    )

    quarantine = Path(result["quarantinePath"])
    assert result["phase"] == "committed"
    assert (quarantine / "tracked.txt").read_text(encoding="utf-8") == "dirty preserved\n"
    assert _git(runtime, "rev-parse", "HEAD") == origin_sha
    assert _git(runtime, "diff", "--name-only") == ""


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junction contract")
def test_dirty_runtime_dependency_junctions_are_quarantined_with_bindings(
    tmp_path: Path,
) -> None:
    """依存junction付きdirty runtimeも隔離後にactive/quarantineの復旧性を保つ。"""
    launcher, source, runtime_root, origin_sha = _runtime_fixture(tmp_path)
    runtime = runtime_root / "production-runtime"
    for name in (".venv", "node_modules"):
        source_dependency = source / name
        source_dependency.mkdir()
        (source_dependency / "marker.txt").write_text(
            f"{name} source\n", encoding="utf-8"
        )
        link = runtime / name
        completed = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(source_dependency)],
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
            check=False,
        )
        if completed.returncode != 0:
            pytest.skip(f"junction fixture unavailable: {completed.stderr.strip()}")
    (runtime / "tracked.txt").write_text("dirty junction runtime\n", encoding="utf-8")

    result = launcher.converge_production_runtime(
        source_repo=source,
        runtime_root=runtime_root,
        origin_sha=origin_sha,
    )

    quarantine = Path(result["quarantinePath"])
    assert result["phase"] == "committed"
    assert (quarantine / "tracked.txt").read_text(encoding="utf-8") == (
        "dirty junction runtime\n"
    )
    assert _git(runtime, "rev-parse", "HEAD") == origin_sha
    assert _git(runtime, "diff", "--name-only") == ""
    for name in (".venv", "node_modules"):
        assert (runtime / name / "marker.txt").read_text(encoding="utf-8") == (
            f"{name} source\n"
        )
        assert (quarantine / name / "marker.txt").read_text(encoding="utf-8") == (
            f"{name} source\n"
        )
        assert (runtime / name).resolve(strict=True) == (source / name).resolve(
            strict=True
        )
        assert (quarantine / name).resolve(strict=True) == (source / name).resolve(
            strict=True
        )


def test_runtime_recovery_forwards_after_move_before_phase_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher, source, runtime_root, origin_sha = _runtime_fixture(tmp_path)
    runtime = runtime_root / "production-runtime"
    (runtime / "tracked.txt").write_text("dirty crash fixture\n", encoding="utf-8")
    real_append = launcher._append_runtime_recovery_event
    injected = {"raised": False}

    def crash_once(*args, **kwargs):
        if kwargs.get("phase") == "runtime_quarantined" and not injected["raised"]:
            injected["raised"] = True
            raise RuntimeError("INJECTED_AFTER_MOVE_BEFORE_PHASE")
        return real_append(*args, **kwargs)

    monkeypatch.setattr(launcher, "_append_runtime_recovery_event", crash_once)
    with pytest.raises(RuntimeError, match="INJECTED_AFTER_MOVE_BEFORE_PHASE"):
        launcher.converge_production_runtime(
            source_repo=source,
            runtime_root=runtime_root,
            origin_sha=origin_sha,
        )
    monkeypatch.setattr(launcher, "_append_runtime_recovery_event", real_append)

    recovered = launcher.converge_production_runtime(
        source_repo=source,
        runtime_root=runtime_root,
        origin_sha=origin_sha,
    )
    assert recovered["phase"] == "committed"
    assert _git(runtime, "rev-parse", "HEAD") == origin_sha
    assert (Path(recovered["quarantinePath"]) / "tracked.txt").read_text(
        encoding="utf-8"
    ) == "dirty crash fixture\n"


def test_runtime_recovery_forwards_after_replacement_before_phase_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher, source, runtime_root, origin_sha = _runtime_fixture(tmp_path)
    runtime = runtime_root / "production-runtime"
    (runtime / "tracked.txt").write_text("dirty replacement fixture\n", encoding="utf-8")
    real_append = launcher._append_runtime_recovery_event
    injected = {"raised": False}

    def crash_once(*args, **kwargs):
        if kwargs.get("phase") == "replacement_created" and not injected["raised"]:
            injected["raised"] = True
            raise RuntimeError("INJECTED_AFTER_ADD_BEFORE_PHASE")
        return real_append(*args, **kwargs)

    monkeypatch.setattr(launcher, "_append_runtime_recovery_event", crash_once)
    with pytest.raises(RuntimeError, match="INJECTED_AFTER_ADD_BEFORE_PHASE"):
        launcher.converge_production_runtime(
            source_repo=source,
            runtime_root=runtime_root,
            origin_sha=origin_sha,
        )
    monkeypatch.setattr(launcher, "_append_runtime_recovery_event", real_append)

    recovered = launcher.converge_production_runtime(
        source_repo=source,
        runtime_root=runtime_root,
        origin_sha=origin_sha,
    )
    assert recovered["phase"] == "committed"
    assert _git(runtime, "rev-parse", "HEAD") == origin_sha
    assert (Path(recovered["quarantinePath"]) / "tracked.txt").read_text(
        encoding="utf-8"
    ) == "dirty replacement fixture\n"


def test_runtime_recovery_finishes_ancestor_transaction_before_new_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """writer更新時は旧transactionを終端してから新generationへ一回だけ進む。"""
    launcher, source, runtime_root, origin_sha = _runtime_fixture(tmp_path)
    runtime = runtime_root / "production-runtime"
    (runtime / "tracked.txt").write_text("dirty ancestor fixture\n", encoding="utf-8")
    real_append = launcher._append_runtime_recovery_event
    injected = {"raised": False}

    def crash_after_replacement(*args, **kwargs):
        result = real_append(*args, **kwargs)
        if kwargs.get("phase") == "replacement_created" and not injected["raised"]:
            injected["raised"] = True
            raise RuntimeError("INJECTED_AFTER_REPLACEMENT_RECORDED")
        return result

    monkeypatch.setattr(launcher, "_append_runtime_recovery_event", crash_after_replacement)
    with pytest.raises(RuntimeError, match="INJECTED_AFTER_REPLACEMENT_RECORDED"):
        launcher.converge_production_runtime(
            source_repo=source,
            runtime_root=runtime_root,
            origin_sha=origin_sha,
        )
    monkeypatch.setattr(launcher, "_append_runtime_recovery_event", real_append)

    (source / "tracked.txt").write_text("next generation\n", encoding="utf-8")
    _git(source, "add", "tracked.txt")
    _git(source, "commit", "-m", "next generation")
    next_sha = _git(source, "rev-parse", "HEAD")
    sealed_origins: list[str] = []

    def record_active_generation(**kwargs):
        sealed_origins.append(str(kwargs["origin_sha"]))
        return {"generationId": str(kwargs["origin_sha"])}

    monkeypatch.setattr(
        launcher, "_seal_active_production_generation", record_active_generation
    )

    recovered = launcher.converge_production_runtime(
        source_repo=source,
        runtime_root=runtime_root,
        origin_sha=next_sha,
        bin_dir=tmp_path / "bin",
    )

    assert recovered["phase"] == "committed"
    assert recovered["originSha"] == next_sha
    assert _git(runtime, "rev-parse", "HEAD") == next_sha
    assert (runtime / "tracked.txt").read_text(encoding="utf-8") == "next generation\n"
    assert not list((runtime_root / "transactions").iterdir())
    assert list((runtime_root / "ledger" / "terminals").glob("*.json"))
    assert sealed_origins == [next_sha]


def test_runtime_recovery_rejects_divergent_generation_during_active_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """active authorityと非ancestorのgenerationへはforwardしない。"""
    launcher, source, runtime_root, origin_sha = _runtime_fixture(tmp_path)
    runtime = runtime_root / "production-runtime"
    (runtime / "tracked.txt").write_text("dirty divergent fixture\n", encoding="utf-8")
    real_append = launcher._append_runtime_recovery_event
    injected = {"raised": False}

    def crash_after_replacement(*args, **kwargs):
        result = real_append(*args, **kwargs)
        if kwargs.get("phase") == "replacement_created" and not injected["raised"]:
            injected["raised"] = True
            raise RuntimeError("INJECTED_DIVERGENT_REPLACEMENT_RECORDED")
        return result

    monkeypatch.setattr(launcher, "_append_runtime_recovery_event", crash_after_replacement)
    with pytest.raises(RuntimeError, match="INJECTED_DIVERGENT_REPLACEMENT_RECORDED"):
        launcher.converge_production_runtime(
            source_repo=source,
            runtime_root=runtime_root,
            origin_sha=origin_sha,
        )
    monkeypatch.setattr(launcher, "_append_runtime_recovery_event", real_append)
    tree_sha = _git(source, "rev-parse", f"{origin_sha}^{{tree}}")
    divergent_sha = _git(source, "commit-tree", tree_sha, "-m", "divergent generation")

    with pytest.raises(RuntimeError, match="PRODUCTION_RUNTIME_RECOVERY_GENERATION_DRIFT"):
        launcher.converge_production_runtime(
            source_repo=source,
            runtime_root=runtime_root,
            origin_sha=divergent_sha,
        )

    assert not runtime.exists()
    assert len(list((runtime_root / "transactions").iterdir())) == 1


def test_runtime_recovery_forwards_after_quarantine_parent_only_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher, source, runtime_root, origin_sha = _runtime_fixture(tmp_path)
    runtime = runtime_root / "production-runtime"
    (runtime / "tracked.txt").write_text("dirty parent fixture\n", encoding="utf-8")
    real_run_git = launcher._run_git
    injected = {"raised": False}

    def crash_before_move(repo, *args, **kwargs):
        if args[:2] == ("worktree", "move") and not injected["raised"]:
            injected["raised"] = True
            raise RuntimeError("INJECTED_AFTER_QUARANTINE_PARENT")
        return real_run_git(repo, *args, **kwargs)

    monkeypatch.setattr(launcher, "_run_git", crash_before_move)
    with pytest.raises(RuntimeError, match="INJECTED_AFTER_QUARANTINE_PARENT"):
        launcher.converge_production_runtime(
            source_repo=source,
            runtime_root=runtime_root,
            origin_sha=origin_sha,
        )
    monkeypatch.setattr(launcher, "_run_git", real_run_git)

    recovered = launcher.converge_production_runtime(
        source_repo=source,
        runtime_root=runtime_root,
        origin_sha=origin_sha,
    )
    assert recovered["phase"] == "committed"
    assert (Path(recovered["quarantinePath"]) / "tracked.txt").read_text(
        encoding="utf-8"
    ) == "dirty parent fixture\n"


def test_runtime_recovery_handles_orphan_transaction_before_journal_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher, source, runtime_root, origin_sha = _runtime_fixture(tmp_path)
    runtime = runtime_root / "production-runtime"
    (runtime / "tracked.txt").write_text("dirty orphan fixture\n", encoding="utf-8")
    real_append = launcher._append_runtime_recovery_event
    injected = {"raised": False}

    def crash_before_journal(*args, **kwargs):
        if kwargs.get("phase") == "prepared" and not injected["raised"]:
            injected["raised"] = True
            raise RuntimeError("INJECTED_BEFORE_JOURNAL_PUBLISH")
        return real_append(*args, **kwargs)

    monkeypatch.setattr(launcher, "_append_runtime_recovery_event", crash_before_journal)
    with pytest.raises(RuntimeError, match="INJECTED_BEFORE_JOURNAL_PUBLISH"):
        launcher.converge_production_runtime(
            source_repo=source,
            runtime_root=runtime_root,
            origin_sha=origin_sha,
        )
    monkeypatch.setattr(launcher, "_append_runtime_recovery_event", real_append)

    recovered = launcher.converge_production_runtime(
        source_repo=source,
        runtime_root=runtime_root,
        origin_sha=origin_sha,
    )
    assert recovered["phase"] == "committed"
    assert (Path(recovered["quarantinePath"]) / "tracked.txt").read_text(
        encoding="utf-8"
    ) == "dirty orphan fixture\n"


def test_foreign_runtime_common_dir_is_rejected_before_checkout_or_dependency_binding(
    tmp_path: Path,
) -> None:
    launcher, source, runtime_root, origin_sha = _runtime_fixture(tmp_path)
    runtime = runtime_root / "production-runtime"
    _git(source, "worktree", "remove", "--force", str(runtime))
    _git(tmp_path, "clone", "--no-local", str(source), str(runtime))
    _git(runtime, "config", "user.email", "test@example.invalid")
    _git(runtime, "config", "user.name", "Foreign Runtime")
    (runtime / "tracked.txt").write_text("foreign generation\n", encoding="utf-8")
    _git(runtime, "add", "tracked.txt")
    _git(runtime, "commit", "-m", "foreign")
    foreign_head = _git(runtime, "rev-parse", "HEAD")
    (source / ".venv").mkdir()

    with pytest.raises(RuntimeError, match="PRODUCTION_RUNTIME_COMMON_DIR_DRIFT"):
        launcher.converge_production_runtime(
            source_repo=source,
            runtime_root=runtime_root,
            origin_sha=origin_sha,
        )

    assert _git(runtime, "rev-parse", "HEAD") == foreign_head
    assert not (runtime / ".venv").exists()


def _lock_runtime_owner_receipt_for_current_process(
    home: Path, *, nonce: str
) -> tuple[Path, int]:
    mutex_identity = _load_task_launcher_module()._runtime_mutex_identity()
    receipt_path = home / "bin" / "news-grasp-runtime-lifecycle-owner.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(
            {
                "schemaVersion": "NEWS_GRASP_RUNTIME_LIFECYCLE_OWNER_V1",
                "ownerPid": os.getpid(),
                "ownerNonce": nonce,
                "mutexName": (
                    f"Global\\NewsGraspBootstrapOrchestration-{mutex_identity}"
                ),
                "ownerScriptPath": "C:\\fixture\\news-grasp-bootstrap.ps1",
                "ownerProcessImage": "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe",
                "issuedAtUtc": "2026-08-10T03:00:00+00:00",
            },
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = ctypes.c_void_p
    handle = kernel32.CreateFileW(
        str(receipt_path), 0xC0000000, 0x1, None, 2, 0x80, None
    )
    assert handle != ctypes.c_void_p(-1).value
    written = ctypes.c_uint32()
    buffer = ctypes.create_string_buffer(payload)
    assert kernel32.WriteFile(
        handle, buffer, len(payload), ctypes.byref(written), None
    )
    assert written.value == len(payload)
    assert kernel32.FlushFileBuffers(handle)
    return receipt_path, int(handle)


def _start_runtime_lifecycle_owner(
    home: Path, *, nonce: str
) -> tuple[subprocess.Popen[str], Path, int]:
    mutex_identity = _load_task_launcher_module()._runtime_mutex_identity()
    receipt_path = home / "bin" / "news-grasp-runtime-lifecycle-owner.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    mutex_name = f"Global\\NewsGraspBootstrapOrchestration-{mutex_identity}"
    owner_code = r'''
import ctypes, json, os, sys
receipt_path, nonce, mutex_name = sys.argv[1:4]
payload = (json.dumps({
    "schemaVersion": "NEWS_GRASP_RUNTIME_LIFECYCLE_OWNER_V1",
    "ownerPid": os.getpid(),
    "ownerNonce": nonce,
    "mutexName": mutex_name,
    "ownerScriptPath": r"C:\fixture\news-grasp-bootstrap.ps1",
    "ownerProcessImage": r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
    "issuedAtUtc": "2026-08-10T03:00:00+00:00",
}, separators=(",", ":")) + "\n").encode("utf-8")
k = ctypes.WinDLL("kernel32", use_last_error=True)
k.CreateFileW.restype = ctypes.c_void_p
file_handle = k.CreateFileW(receipt_path, 0xC0000000, 0x1, None, 2, 0x80, None)
if file_handle == ctypes.c_void_p(-1).value:
    raise SystemExit(91)
written = ctypes.c_uint32()
buffer = ctypes.create_string_buffer(payload)
if not k.WriteFile(file_handle, buffer, len(payload), ctypes.byref(written), None):
    raise SystemExit(92)
k.FlushFileBuffers(file_handle)
mutex_handle = k.CreateMutexW(None, False, mutex_name)
if k.WaitForSingleObject(mutex_handle, 0) not in (0, 0x80):
    raise SystemExit(93)
print(json.dumps({"pid": os.getpid()}), flush=True)
sys.stdin.readline()
k.ReleaseMutex(mutex_handle)
k.CloseHandle(mutex_handle)
k.CloseHandle(file_handle)
'''
    owner = subprocess.Popen(
        [sys.executable, "-c", owner_code, str(receipt_path), nonce, mutex_name],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert owner.stdout is not None
    ready = json.loads(owner.stdout.readline())
    return owner, receipt_path, int(ready["pid"])


def test_direct_converge_runtime_requires_same_runtime_mutex_across_processes(
    tmp_path: Path,
) -> None:
    if sys.platform != "win32":
        pytest.skip("Windows named mutex contract")
    _, source, _, origin_sha = _runtime_fixture(tmp_path)
    launcher_path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "ops"
        / "news-grasp-task-launcher.pyw"
    )
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    runtime_handle = kernel32.CreateMutexW(
        None, False, "Global\\NewsGraspProductionRuntimeConvergence"
    )
    mutex_identity = _load_task_launcher_module()._runtime_mutex_identity()
    bootstrap_handle = kernel32.CreateMutexW(
        None, False, f"Global\\NewsGraspBootstrapOrchestration-{mutex_identity}"
    )
    assert runtime_handle and bootstrap_handle
    assert kernel32.WaitForSingleObject(runtime_handle, 0) in (0, 0x80)
    assert kernel32.WaitForSingleObject(bootstrap_handle, 0) in (0, 0x80)
    isolated_home = tmp_path / "isolated-home"
    isolated_home.mkdir()
    owner_nonce = "1" * 32
    owner_receipt, owner_receipt_handle = (
        _lock_runtime_owner_receipt_for_current_process(
            isolated_home,
            nonce=owner_nonce,
        )
    )
    env = dict(os.environ)
    env["USERPROFILE"] = str(isolated_home)
    env["HOME"] = str(isolated_home)
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(launcher_path),
                "converge-runtime",
                "--source-repo",
                str(source),
                "--origin-sha",
                origin_sha,
                "--bootstrap-owner-pid",
                str(os.getpid()),
                "--bootstrap-owner-receipt",
                str(owner_receipt),
                "--bootstrap-owner-nonce",
                owner_nonce,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            check=False,
            timeout=20,
        )
    finally:
        kernel32.CloseHandle(owner_receipt_handle)
        kernel32.ReleaseMutex(bootstrap_handle)
        kernel32.CloseHandle(bootstrap_handle)
        kernel32.ReleaseMutex(runtime_handle)
        kernel32.CloseHandle(runtime_handle)
    assert completed.returncode == 72
    assert "PRODUCTION_RUNTIME_MUTEX_OWNER_INVALID" in completed.stderr


def test_direct_converge_runtime_is_blocked_while_bootstrap_runtime_lifecycle_mutex_is_held(
    tmp_path: Path,
) -> None:
    if sys.platform != "win32":
        pytest.skip("Windows named mutex contract")
    _, source, _, origin_sha = _runtime_fixture(tmp_path)
    launcher_path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "ops"
        / "news-grasp-task-launcher.pyw"
    )
    isolated_home = tmp_path / "direct-home"
    isolated_home.mkdir()
    owner_nonce = "2" * 32
    owner, owner_receipt, owner_pid = _start_runtime_lifecycle_owner(
        isolated_home,
        nonce=owner_nonce,
    )
    env = dict(os.environ)
    env["USERPROFILE"] = str(isolated_home)
    env["HOME"] = str(isolated_home)
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(launcher_path),
                "converge-runtime",
                "--source-repo",
                str(source),
                "--origin-sha",
                origin_sha,
                "--bootstrap-owner-pid",
                str(owner_pid),
                "--bootstrap-owner-receipt",
                str(owner_receipt),
                "--bootstrap-owner-nonce",
                owner_nonce,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            check=False,
            timeout=20,
        )
    finally:
        owner.communicate(input="\n", timeout=10)
    assert completed.returncode == 72
    assert "PRODUCTION_RUNTIME_MUTEX_OWNER_INVALID" in completed.stderr
    assert not (isolated_home / ".news-grasp-runtime" / "production-runtime").exists()


def test_direct_converge_cannot_borrow_unrelated_bootstrap_mutex_holder(
    tmp_path: Path,
) -> None:
    if sys.platform != "win32":
        pytest.skip("Windows named mutex contract")
    _, source, _, origin_sha = _runtime_fixture(tmp_path)
    launcher_path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "ops"
        / "news-grasp-task-launcher.pyw"
    )
    isolated_home = tmp_path / "borrowed-home"
    isolated_home.mkdir()
    owner_nonce = "3" * 32
    owner, owner_receipt, _ = _start_runtime_lifecycle_owner(
        isolated_home,
        nonce=owner_nonce,
    )
    env = dict(os.environ)
    env["USERPROFILE"] = str(isolated_home)
    env["HOME"] = str(isolated_home)
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(launcher_path),
                "converge-runtime",
                "--source-repo",
                str(source),
                "--origin-sha",
                origin_sha,
                "--bootstrap-owner-pid",
                str(os.getpid()),
                "--bootstrap-owner-receipt",
                str(owner_receipt),
                "--bootstrap-owner-nonce",
                owner_nonce,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            check=False,
            timeout=20,
        )
    finally:
        owner.communicate(input="\n", timeout=10)
    assert completed.returncode == 72
    assert "PRODUCTION_RUNTIME_MUTEX_OWNER_RECEIPT_INVALID" in completed.stderr
    assert not (isolated_home / ".news-grasp-runtime" / "production-runtime").exists()


def test_runtime_mutex_owner_rejects_process_beyond_bounded_ancestry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    launcher = _load_task_launcher_module()
    monkeypatch.setattr(launcher, "_process_ancestor_pids", lambda max_hops=3: (11, 22, 33))

    with pytest.raises(RuntimeError, match="PRODUCTION_RUNTIME_MUTEX_OWNER_INVALID"):
        launcher._require_bootstrap_runtime_mutex_owner(
            44,
            owner_receipt_path=tmp_path / "unused.json",
            owner_nonce="4" * 32,
        )


def test_runtime_mutex_owner_snapshot_failure_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if sys.platform != "win32":
        pytest.skip("Windows process snapshot contract")
    launcher = _load_task_launcher_module()

    class _FailedSnapshot:
        def __init__(self) -> None:
            self.argtypes = None
            self.restype = None

        def __call__(self, *_args):
            return ctypes.c_void_p(-1).value

    class _UnusedApi:
        def __init__(self) -> None:
            self.argtypes = None
            self.restype = None

        def __call__(self, *_args):
            raise AssertionError("snapshot失敗後にprocess列挙してはならない")

    class _Kernel32:
        CreateToolhelp32Snapshot = _FailedSnapshot()
        Process32FirstW = _UnusedApi()
        Process32NextW = _UnusedApi()
        CloseHandle = _UnusedApi()

    monkeypatch.setattr(launcher.ctypes, "WinDLL", lambda *_args, **_kwargs: _Kernel32())
    with pytest.raises(RuntimeError, match="PRODUCTION_RUNTIME_MUTEX_OWNER_INVALID"):
        launcher._process_ancestor_pids()


def test_runtime_recovery_archives_atomic_write_temp_after_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher, source, runtime_root, origin_sha = _runtime_fixture(tmp_path)
    runtime = runtime_root / "production-runtime"
    (runtime / "tracked.txt").write_text("dirty temp fixture\n", encoding="utf-8")
    real_append = launcher._append_runtime_recovery_event
    injected = {"raised": False}

    def crash_after_temp(*args, **kwargs):
        if kwargs.get("phase") == "committed" and not injected["raised"]:
            injected["raised"] = True
            journal_path = Path(args[0])
            orphan = journal_path.with_name(
                f"{journal_path.name}.tmp.123.{'a' * 32}"
            )
            orphan.write_text('{"partial":true}\n', encoding="utf-8")
            raise RuntimeError("INJECTED_AFTER_TEMP_BEFORE_REPLACE")
        return real_append(*args, **kwargs)

    monkeypatch.setattr(launcher, "_append_runtime_recovery_event", crash_after_temp)
    with pytest.raises(RuntimeError, match="INJECTED_AFTER_TEMP_BEFORE_REPLACE"):
        launcher.converge_production_runtime(
            source_repo=source,
            runtime_root=runtime_root,
            origin_sha=origin_sha,
        )
    monkeypatch.setattr(launcher, "_append_runtime_recovery_event", real_append)

    recovered = launcher.converge_production_runtime(
        source_repo=source,
        runtime_root=runtime_root,
        origin_sha=origin_sha,
    )
    archive_dir = Path(recovered["journalPath"]).parent
    assert recovered["phase"] == "committed"
    assert len(list(archive_dir.glob("orphaned-write-*.tmp"))) == 1


def test_runtime_recovery_rejects_self_consistent_unanchored_journal(
    tmp_path: Path,
) -> None:
    launcher, source, runtime_root, origin_sha = _runtime_fixture(tmp_path)
    runtime = runtime_root / "production-runtime"
    (runtime / "tracked.txt").write_text("dirty unanchored fixture\n", encoding="utf-8")
    transaction_id = "20260810T120000000000Z-0123456789abcdef"
    transaction_dir = runtime_root / "transactions" / transaction_id
    transaction_dir.mkdir(parents=True)
    event = {
        "sequence": 1,
        "phase": "prepared",
        "previousEventSha256": "0" * 64,
        "observedAtUtc": "2026-08-10T03:00:00+00:00",
        "observations": {"runtimeDirty": True, "runtimeHead": origin_sha},
    }
    event["eventSha256"] = launcher._sha256_json(event)
    journal = {
        "schemaVersion": launcher.RUNTIME_RECOVERY_SCHEMA,
        "transactionId": transaction_id,
        "phase": "prepared",
        "originSha": origin_sha,
        "sourceCommonDir": str(launcher._git_common_dir(source)),
        "runtimePath": str(runtime),
        "quarantinePath": str(
            runtime_root / "quarantine" / transaction_id / "production-runtime"
        ),
        "publishOrTerminalAmbiguous": False,
        "events": [event],
    }
    (transaction_dir / "runtime-recovery.json").write_text(
        json.dumps(journal), encoding="utf-8"
    )

    with pytest.raises(RuntimeError, match="PRODUCTION_RUNTIME_RECOVERY_AUTHORITY_INVALID"):
        launcher.converge_production_runtime(
            source_repo=source,
            runtime_root=runtime_root,
            origin_sha=origin_sha,
        )
    assert (runtime / "tracked.txt").read_text(encoding="utf-8") == (
        "dirty unanchored fixture\n"
    )


def _write_anchored_committed_runtime_transaction(
    launcher: object,
    *,
    source: Path,
    runtime_root: Path,
    origin_sha: str,
    index: int,
) -> None:
    transaction_id = f"20260810T{index:012d}Z-{index:016x}"
    runtime = runtime_root / "production-runtime"
    quarantine = runtime_root / "quarantine" / transaction_id / "production-runtime"
    authority_path = runtime_root / "authorities" / f"{transaction_id}.json"
    authority_path.parent.mkdir(parents=True, exist_ok=True)
    authority = {
        "schemaVersion": "NEWS_GRASP_PRODUCTION_RUNTIME_RECOVERY_AUTHORITY_V1",
        "transactionId": transaction_id,
        "originSha": origin_sha,
        "sourceCommonDir": str(launcher._git_common_dir(source)),
        "runtimePath": str(runtime),
        "quarantinePath": str(quarantine),
        "transactionPath": str(runtime_root / "transactions" / transaction_id),
        "replacementStagingPath": str(
            runtime_root
            / "transactions"
            / transaction_id
            / "replacement-staging"
            / "production-runtime"
        ),
        "issuedAtUtc": "2026-08-10T03:00:00+00:00",
    }
    authority["authoritySha256"] = launcher._sha256_json(authority)
    authority_path.write_text(json.dumps(authority), encoding="utf-8")
    issue_path = runtime_root / "ledger" / "issues" / f"{transaction_id}.json"
    issue_path.parent.mkdir(parents=True, exist_ok=True)
    issue = {
        "schemaVersion": "NEWS_GRASP_PRODUCTION_RUNTIME_RECOVERY_ISSUE_V1",
        "transactionId": transaction_id,
        "authoritySha256": authority["authoritySha256"],
        "originSha": authority["originSha"],
        "sourceCommonDir": authority["sourceCommonDir"],
        "runtimePath": authority["runtimePath"],
        "quarantinePath": authority["quarantinePath"],
        "transactionPath": authority["transactionPath"],
        "replacementStagingPath": authority["replacementStagingPath"],
        "issuedAtUtc": authority["issuedAtUtc"],
    }
    issue["issueSha256"] = launcher._sha256_json(issue)
    issue_path.write_text(json.dumps(issue), encoding="utf-8")
    authority_for_ledger = dict(authority)
    authority_for_ledger["authorityPath"] = str(authority_path)
    issue_for_ledger = dict(issue)
    issue_for_ledger["issuePath"] = str(issue_path)
    launcher._append_runtime_recovery_authority_ledger(
        runtime_root=runtime_root,
        authority=authority_for_ledger,
        issue=issue_for_ledger,
    )
    events: list[dict[str, object]] = []
    previous = "0" * 64
    for sequence, phase in enumerate(launcher.RUNTIME_RECOVERY_PHASES, start=1):
        event: dict[str, object] = {
            "sequence": sequence,
            "phase": phase,
            "previousEventSha256": previous,
            "observedAtUtc": "2026-08-10T03:00:00+00:00",
            "observations": {},
        }
        event["eventSha256"] = launcher._sha256_json(event)
        previous = str(event["eventSha256"])
        events.append(event)
    transaction_dir = runtime_root / "transactions" / transaction_id
    transaction_dir.mkdir(parents=True)
    journal = {
        "schemaVersion": launcher.RUNTIME_RECOVERY_SCHEMA,
        "transactionId": transaction_id,
        "phase": "committed",
        "originSha": origin_sha,
        "sourceCommonDir": str(launcher._git_common_dir(source)),
        "runtimePath": str(runtime),
        "quarantinePath": str(quarantine),
        "authorityPath": str(authority_path),
        "authoritySha256": authority["authoritySha256"],
        "issuePath": str(issue_path),
        "issueSha256": issue["issueSha256"],
        "transactionPath": authority["transactionPath"],
        "replacementStagingPath": authority["replacementStagingPath"],
        "publishOrTerminalAmbiguous": False,
        "events": events,
    }
    archive_dir = quarantine.parent
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / "runtime-recovery.json"
    archive_bytes = json.dumps(journal).encode("utf-8")
    archive_path.write_bytes(archive_bytes)
    terminal_path = runtime_root / "ledger" / "terminals" / f"{transaction_id}.json"
    terminal_path.parent.mkdir(parents=True, exist_ok=True)
    terminal = {
        "schemaVersion": "NEWS_GRASP_PRODUCTION_RUNTIME_RECOVERY_TERMINAL_V1",
        "transactionId": transaction_id,
        "finalJournalSha256": hashlib.sha256(archive_bytes).hexdigest(),
        "archivePath": str(archive_path),
        "authoritySha256": authority["authoritySha256"],
        "issuePath": str(issue_path),
        "issueSha256": issue["issueSha256"],
        "committedAtUtc": "2026-08-10T03:00:00+00:00",
    }
    terminal["terminalSha256"] = launcher._sha256_json(terminal)
    terminal_path.write_text(json.dumps(terminal), encoding="utf-8")


def test_runtime_recovery_requires_maintenance_before_64_committed_turnovers(
    tmp_path: Path,
) -> None:
    launcher, source, runtime_root, origin_sha = _runtime_fixture(tmp_path)
    for index in range(65):
        _write_anchored_committed_runtime_transaction(
            launcher,
            source=source,
            runtime_root=runtime_root,
            origin_sha=origin_sha,
            index=index,
        )

    recovered = launcher.converge_production_runtime(
        source_repo=source,
        runtime_root=runtime_root,
        origin_sha=origin_sha,
    )
    assert recovered["phase"] == "committed"
    assert len(list((runtime_root / "transactions").iterdir())) == 0
    archive_root = runtime_root.parent / ".news-grasp-runtime-recovery-archive"
    assert len(list(archive_root.glob("*.zip"))) == 48
    assert (archive_root / "manifest.jsonl").is_file()


def test_runtime_recovery_capacity_excludes_transaction_owned_product_payload(
    tmp_path: Path,
) -> None:
    """replacement worktree本体をmetadata quotaへ誤算入してpromotionを止めない。"""
    launcher = _load_task_launcher_module()
    runtime_root = tmp_path / ".news-grasp-runtime"
    for relative in ("transactions", "authorities", "ledger/issues", "ledger/terminals", "quarantine"):
        (runtime_root / relative).mkdir(parents=True, exist_ok=True)
    transaction_id = "20260812T010203040506Z-0123456789abcdef"
    payload = (
        runtime_root
        / "transactions"
        / transaction_id
        / "replacement-staging"
        / "production-runtime"
    )
    payload.mkdir(parents=True)
    for index in range(launcher.MAX_RUNTIME_RECOVERY_SCAN_ENTRIES + 8):
        (payload / f"tracked-{index:04d}.txt").write_text("payload\n", encoding="utf-8")

    launcher._assert_runtime_recovery_capacity(runtime_root)


def test_clean_runtime_is_created_via_transaction_owned_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher, source, runtime_root, origin_sha = _runtime_fixture(tmp_path)
    runtime = runtime_root / "production-runtime"
    _git(source, "worktree", "remove", "--force", str(runtime))
    calls: list[tuple[str, ...]] = []
    real_run_git = launcher._run_git

    def recording_run_git(repo: Path, *args: str, **kwargs: object) -> str:
        if args[:3] == ("worktree", "add", "--detach"):
            calls.append(tuple(args))
        return real_run_git(repo, *args, **kwargs)

    monkeypatch.setattr(launcher, "_run_git", recording_run_git)
    result = launcher.converge_production_runtime(
        source_repo=source, runtime_root=runtime_root, origin_sha=origin_sha
    )
    assert result["phase"] == "committed"
    assert runtime.exists()
    assert calls
    assert Path(calls[0][-2]) != runtime


def test_runtime_recovery_rejects_forged_journal_path_before_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher, source, runtime_root, origin_sha = _runtime_fixture(tmp_path)
    runtime = runtime_root / "production-runtime"
    (runtime / "tracked.txt").write_text("dirty forged fixture\n", encoding="utf-8")
    real_append = launcher._append_runtime_recovery_event

    def crash_after_prepared(*args, **kwargs):
        real_append(*args, **kwargs)
        if kwargs.get("phase") == "prepared":
            raise RuntimeError("INJECTED_AFTER_PREPARED")

    monkeypatch.setattr(launcher, "_append_runtime_recovery_event", crash_after_prepared)
    with pytest.raises(RuntimeError, match="INJECTED_AFTER_PREPARED"):
        launcher.converge_production_runtime(
            source_repo=source,
            runtime_root=runtime_root,
            origin_sha=origin_sha,
        )
    monkeypatch.setattr(launcher, "_append_runtime_recovery_event", real_append)
    journal_path = next((runtime_root / "transactions").glob("*/runtime-recovery.json"))
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    outside = tmp_path / "outside"
    journal["quarantinePath"] = str(outside)
    journal_path.write_text(json.dumps(journal), encoding="utf-8")

    with pytest.raises(RuntimeError, match="PRODUCTION_RUNTIME_RECOVERY_JOURNAL_INVALID"):
        launcher.converge_production_runtime(
            source_repo=source,
            runtime_root=runtime_root,
            origin_sha=origin_sha,
        )
    assert not outside.exists()
    assert (runtime / "tracked.txt").read_text(encoding="utf-8") == "dirty forged fixture\n"


def test_launcher_uses_evidence_source_when_configured_runtime_is_missing(
    tmp_path: Path,
) -> None:
    launcher = _load_task_launcher_module()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    evidence = tmp_path / "evidence"
    (evidence / "tools").mkdir(parents=True)
    (evidence / "tools" / "daily_self_heal.py").write_text("# fixture\n", encoding="utf-8")
    python_exe = tmp_path / "python.exe"
    python_exe.write_bytes(b"fixture")
    configured_runtime = tmp_path / ".news-grasp-runtime" / "production-runtime"
    config = {
        "schemaVersion": "NEWS_GRASP_RUNTIME_ROOT_V1",
        "repoDir": str(configured_runtime),
        "pythonExe": str(python_exe),
        "evidenceRepoDir": str(evidence),
    }
    (bin_dir / "news-grasp-runtime-root-v1.json").write_text(
        json.dumps(config), encoding="utf-8"
    )

    resolved = launcher.resolve_bootstrap_launch_roots(bin_dir=bin_dir)
    assert resolved["repoDir"] == evidence.resolve()
    assert resolved["configuredRuntime"] == configured_runtime.absolute()
    assert not configured_runtime.exists()


def test_runtime_recovery_and_installer_phase_contract_is_forward_only() -> None:
    root = Path(__file__).resolve().parents[1]
    launcher = (root / "scripts" / "ops" / "news-grasp-task-launcher.pyw").read_text(
        encoding="utf-8-sig"
    )
    installer = (root / "scripts" / "ops" / "install-news-grasp-ops.ps1").read_text(
        encoding="utf-8-sig"
    )
    bootstrap = (root / "scripts" / "ops" / "news-grasp-bootstrap.ps1").read_text(
        encoding="utf-8-sig"
    )
    for phase in (
        "prepared",
        "runtime_quarantined",
        "replacement_created",
        "dependencies_bound",
        "committed",
    ):
        assert phase in launcher
    assert "NEWS_GRASP_PRODUCTION_RUNTIME_RECOVERY_V1" in launcher
    assert "MAX_RUNTIME_RECOVERY_TRANSACTIONS = 64" in launcher
    assert "--bootstrap-owner-pid" in bootstrap
    assert "--bootstrap-owner-receipt" in bootstrap
    assert "--bootstrap-owner-nonce" in bootstrap
    assert "([string]$PID)" in bootstrap
    assert "PRODUCTION_RUNTIME_MUTEX_OWNER_INVALID" in launcher
    assert "NEWS_GRASP_RUNTIME_LIFECYCLE_OWNER_V1" in bootstrap
    assert "[System.IO.FileShare]::Read" in bootstrap
    assert bootstrap.index("$runtimeOwnerReceiptStream.Write") < bootstrap.index(
        "$runtimeMutex = New-Object System.Threading.Mutex"
    )
    assert "tasks_converged" in installer
    assert "verified" in installer
    recovery = installer.split("function Recover-NewsGraspInterruptedInstall", 1)[1].split(
        "function Invoke-NewsGraspInstallRollback", 1
    )[0]
    assert "tasks_registered" not in recovery


def test_production_runtime_resolver_never_leaks_git_output_into_repo_path() -> None:
    """git の stdout/stderr を関数の RepoDir 戻り値へ混入させない。"""
    bootstrap = (
        Path(__file__).resolve().parents[1] / "scripts" / "ops" / "news-grasp-bootstrap.ps1"
    ).read_text(encoding="utf-8-sig")
    resolver = bootstrap.split("function Resolve-ProductionRuntimeRepo", 1)[1].split(
        "function Get-FileSha256Hex", 1
    )[0]

    assert "$convergenceJson = (& $PythonExe" in resolver
    assert "$convergenceJson | ConvertFrom-Json" in resolver
    assert "PRODUCTION_RUNTIME_CONVERGENCE_RESULT_INVALID" in resolver
    assert "return $runtimeRepo" in resolver


def test_production_runtime_self_repair_evidence_is_written_outside_clean_worktree() -> None:
    """自己修復backup/manifestがproduction runtime自身をdirtyにしない。"""
    bootstrap = (
        Path(__file__).resolve().parents[1] / "scripts" / "ops" / "news-grasp-bootstrap.ps1"
    ).read_text(encoding="utf-8-sig")
    evidence_block = bootstrap.split("$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'", 1)[1].split(
        "$manifestFiles = @()", 1
    )[0]

    assert "$repairEvidenceRoot = if ($UseProductionRuntime)" in evidence_block
    assert "Join-Path $BinDir 'news-grasp-runtime-backups'" in evidence_block
    assert "Join-Path $RepoDir 'build\\live-bootstrap-self-repair'" in evidence_block
    assert "$backupDir = Join-Path $repairEvidenceRoot $timestamp" in evidence_block


def test_direct_identity_uses_issue_scoped_completion_and_detached_push_is_exact() -> None:
    root = Path(__file__).resolve().parents[1]
    assert not (root / "scripts" / "ops" / "news-grasp-runner.ps1").exists()
    skill = (root / "automation/skills/news-grasp-direct-mainline/SKILL.md").read_text(
        encoding="utf-8"
    )
    runtime = (root / "tools/news_grasp_direct_runtime.py").read_text(
        encoding="utf-8"
    )
    assert "automation_id + issue_date + run_intent" in skill
    assert "`origin/main`へfast-forward push" in skill
    assert "ON runs(automation_id,issue_date,run_intent)" in runtime
    assert "ON runs(automation_id,cwd,issue_date" not in runtime


def test_scheduled_high_cost_failure_is_operation_local_for_direct_recovery() -> None:
    root = Path(__file__).resolve().parents[1]
    assert not (root / "scripts" / "ops" / "news-grasp-runner.ps1").exists()
    decision = high_cost.scheduled_operation_failure_disposition(
        issue_date="2026-08-30",
        operation="reporter_model_call",
        reason_code="HIGH_COST_OPERATION_ADMISSION_REJECTED",
        model_launch_count=0,
        exact_successor="reuse_fresh_reporter_artifact",
    )
    assert decision["operation_status"] == "red"
    assert decision["task_terminal"] is False
    assert decision["task_wide_stop"] is False
    assert decision["exact_successor"] == "reuse_fresh_reporter_artifact"


def test_task_launcher_freezes_pre_runner_failure_in_fixed_state(tmp_path: Path) -> None:
    """fetch/worktree/bootstrap が runner 前に落ちても監査が回収できる typed state を残す。"""
    root = Path(__file__).resolve().parents[1]
    launcher_path = root / "scripts" / "ops" / "news-grasp-task-launcher.pyw"
    loader = SourceFileLoader("news_grasp_task_launcher_test", str(launcher_path))
    spec = spec_from_loader(loader.name, loader)
    assert spec is not None
    module = module_from_spec(spec)
    loader.exec_module(module)

    state_path = tmp_path / "news-grasp-runner-state.json"
    module.write_startup_failure_state(
        state_path=state_path,
        returncode=72,
        issue_date="2026-08-03",
        detail="PRODUCTION_RUNTIME_FETCH_FAILED",
    )

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["status"] == "blocked_startup_self_repair_failed"
    assert state["exit_code"] == 72
    assert state["date"] == "2026-08-03"
    assert state["run_intent"] == "ScheduledProduction"
    assert state["attempt_terminal"] is True
    assert state["recovery_class"] == "startup_self_repair_failure"


def test_direct_completion_consumes_typed_run_intent_without_runner_state() -> None:
    root = Path(__file__).resolve().parents[1]
    assert not (root / "scripts" / "ops" / "news-grasp-runner.ps1").exists()
    completion = (root / "tools/news_grasp_completion_guard.py").read_text(
        encoding="utf-8"
    )
    direct = completion.split("def evaluate_direct_public", 1)[1].split(
        "def _canonical_bytes", 1
    )[0]
    assert 'receipt.get("run_intent") != "ScheduledProductionDirect"' in direct
    assert "runner_state" not in direct
    assert 'receipt.get("readiness")' not in direct
    assert "receipt.get('readiness')" not in direct


def test_direct_resume_uses_exact_public_successor_without_runner_continuation() -> None:
    root = Path(__file__).resolve().parents[1]
    assert not (root / "scripts" / "ops" / "news-grasp-runner.ps1").exists()
    skill = (root / "automation/skills/news-grasp-direct-mainline/SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "実行可能なexact public successorを継続" in skill
    assert "quality Redは該当artifactだけを修復" in skill
    assert "旧 runner、NoPublish、fallbackへの切替えは行わない" in skill


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


def test_current_scheduled_failure_is_atomically_recorded_for_recovery(tmp_path) -> None:
    recorder = _required_symbol(
        "record_scheduled_news_grasp_failure_in_store",
        "RED_SCHEDULED_FAILURE_TERMINALIZER_MISSING",
    )
    store = high_cost.HighCostControlStore.create_for_test(
        tmp_path / "ledger.sqlite3", high_cost.MemoryAnchor()
    )
    high_cost.admit_scheduled_news_grasp_operation_in_store(
        store=store,
        issue_date="2026-08-03",
        operation_kind="scheduled_production",
    )

    failure = recorder(
        store=store,
        issue_date="2026-08-03",
        run_id="atomic-failure-record",
        last_task_result=72,
        runner_state="blocked_startup_self_repair_failed",
        state_sha256="3" * 64,
        log_sha256="4" * 64,
        task_action_sha256="1" * 64,
        runner_sha256="2" * 64,
        failure_stage="startup_self_repair",
    )

    witness = high_cost.inspect_scheduled_news_grasp_attempt_in_store(
        store=store,
        issue_date="2026-08-03",
    )
    assert failure["schemaVersion"] == "SCHEDULED_FAILURE_RECEIPT_V1"
    assert witness["scheduledAttemptStatus"] == "failed"
    assert witness["failureReceiptSha256"] == failure["receiptSha256"]
    event = store.db.execute(
        "SELECT request_id,payload_json FROM events WHERE event_type='scheduled_failure_frozen'"
    ).fetchone()
    assert event["request_id"].endswith(":2026-08-03:atomic-failure-record")
    assert json.loads(event["payload_json"])["runId"] == "atomic-failure-record"
    assert json.loads(event["payload_json"])["failureStage"] == "startup_self_repair"
    store.close()


def test_current_scheduled_failure_terminalizer_rejects_replay_and_missing_reservation(
    tmp_path,
) -> None:
    recorder = _required_symbol(
        "record_scheduled_news_grasp_failure_in_store",
        "RED_SCHEDULED_FAILURE_TERMINALIZER_MISSING",
    )
    store = high_cost.HighCostControlStore.create_for_test(
        tmp_path / "ledger.sqlite3", high_cost.MemoryAnchor()
    )
    kwargs = {
        "store": store,
        "issue_date": "2026-08-03",
        "run_id": "terminalizer-replay",
        "last_task_result": 72,
        "runner_state": "blocked_startup_self_repair_failed",
        "state_sha256": "3" * 64,
        "log_sha256": "4" * 64,
        "task_action_sha256": "1" * 64,
        "runner_sha256": "2" * 64,
        "failure_stage": "startup_self_repair",
    }
    with pytest.raises(
        high_cost.ControlError, match="SCHEDULED_ATTEMPT_LEDGER_EVENT_MISSING"
    ):
        recorder(**kwargs)

    high_cost.admit_scheduled_news_grasp_operation_in_store(
        store=store,
        issue_date="2026-08-03",
        operation_kind="scheduled_production",
    )
    recorder(**kwargs)
    with pytest.raises(
        high_cost.ControlError, match="SCHEDULED_FAILURE_TERMINAL_REPLAY"
    ):
        recorder(**kwargs)
    store.close()


def test_broker_exposes_atomic_scheduled_failure_terminalizer() -> None:
    source = BROKER_PATH.read_text(encoding="utf-8-sig")
    assert 'sub.add_parser("record-news-grasp-failure")' in source
    assert "record_scheduled_news_grasp_failure_in_store" in source


def test_broker_exposes_attempt_ledger_inspection_command() -> None:
    source = BROKER_PATH.read_text(encoding="utf-8-sig")
    assert 'sub.add_parser("inspect-news-grasp-attempt")' in source
    assert "inspect_scheduled_news_grasp_attempt_in_store" in source


def test_broker_exposes_ledger_backed_recovery_authority_validation() -> None:
    parser_source = BROKER_PATH.read_text(encoding="utf-8-sig")
    assert "validate-news-grasp-recovery-authority" in parser_source
    assert "validate_scheduled_recovery_authority_in_store" in parser_source


def test_runtime_recovery_rejects_third_party_outer_mutex_bypass_before_mutation(
    tmp_path: Path,
) -> None:
    if sys.platform != "win32":
        pytest.skip("Windows named mutex contract")
    launcher, source, runtime_root, origin_sha = _runtime_fixture(tmp_path)
    runtime = runtime_root / "production-runtime"
    before = (runtime / "tracked.txt").read_bytes()
    mutex_identity = _load_task_launcher_module()._runtime_mutex_identity()
    mutex_name = f"Global\\NewsGraspProductionRuntime-{mutex_identity}"
    holder_code = r'''
import ctypes, sys
k = ctypes.WinDLL("kernel32", use_last_error=True)
h = k.CreateMutexW(None, False, sys.argv[1])
if not h or k.WaitForSingleObject(h, 0) not in (0, 0x80):
    raise SystemExit(91)
print("ready", flush=True)
sys.stdin.readline()
k.ReleaseMutex(h)
k.CloseHandle(h)
'''
    holder = subprocess.Popen(
        [sys.executable, "-c", holder_code, mutex_name],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert holder.stdout is not None
    assert holder.stdout.readline().strip() == "ready"
    try:
        with pytest.raises(RuntimeError, match="PRODUCTION_RUNTIME_MUTEX_BUSY"):
            launcher.converge_production_runtime(
                source_repo=source, runtime_root=runtime_root, origin_sha=origin_sha
            )
    finally:
        assert holder.stdin is not None
        holder.stdin.write("stop\n")
        holder.stdin.flush()
        holder.wait(timeout=10)
    assert (runtime / "tracked.txt").read_bytes() == before


def test_runtime_recovery_rejects_self_consistent_forged_authority_and_prepared_journal_before_mutation(
    tmp_path: Path,
) -> None:
    launcher, source, runtime_root, origin_sha = _runtime_fixture(tmp_path)
    runtime = runtime_root / "production-runtime"
    (runtime / "tracked.txt").write_text("forged authority preserved\n", encoding="utf-8")
    transaction_id = "20260810T120000000000Z-abcdef0123456789"
    transaction_dir = runtime_root / "transactions" / transaction_id
    transaction_dir.mkdir(parents=True)
    authority_path = runtime_root / "authorities" / f"{transaction_id}.json"
    authority_path.parent.mkdir(parents=True, exist_ok=True)
    authority = {
        "schemaVersion": launcher.RUNTIME_RECOVERY_AUTHORITY_SCHEMA,
        "transactionId": transaction_id,
        "originSha": origin_sha,
        "sourceCommonDir": str(launcher._git_common_dir(source)),
        "runtimePath": str(runtime),
        "quarantinePath": str(runtime_root / "quarantine" / transaction_id / "production-runtime"),
        "transactionPath": str(transaction_dir),
        "replacementStagingPath": str(transaction_dir / "replacement-staging" / "production-runtime"),
        "issuedAtUtc": "2026-08-10T03:00:00+00:00",
    }
    authority["authoritySha256"] = launcher._sha256_json(authority)
    authority_path.write_text(json.dumps(authority), encoding="utf-8")
    event = {
        "sequence": 1,
        "phase": "prepared",
        "previousEventSha256": "0" * 64,
        "observedAtUtc": "2026-08-10T03:00:00+00:00",
        "observations": {"runtimeDirty": True},
    }
    event["eventSha256"] = launcher._sha256_json(event)
    journal = {
        "schemaVersion": launcher.RUNTIME_RECOVERY_SCHEMA,
        "transactionId": transaction_id,
        "phase": "prepared",
        "originSha": origin_sha,
        "sourceCommonDir": authority["sourceCommonDir"],
        "runtimePath": authority["runtimePath"],
        "quarantinePath": authority["quarantinePath"],
        "authorityPath": str(authority_path),
        "authoritySha256": authority["authoritySha256"],
        "issuePath": str(runtime_root / "ledger" / "issues" / f"{transaction_id}.json"),
        "issueSha256": "0" * 64,
        "transactionPath": authority["transactionPath"],
        "replacementStagingPath": authority["replacementStagingPath"],
        "publishOrTerminalAmbiguous": False,
        "events": [event],
    }
    (transaction_dir / "runtime-recovery.json").write_text(json.dumps(journal), encoding="utf-8")
    with pytest.raises(RuntimeError, match="PRODUCTION_RUNTIME_RECOVERY_AUTHORITY_INVALID"):
        launcher.converge_production_runtime(
            source_repo=source, runtime_root=runtime_root, origin_sha=origin_sha
        )
    assert (runtime / "tracked.txt").read_text(encoding="utf-8") == "forged authority preserved\n"


def test_runtime_recovery_rejects_terminal_transaction_replay_before_mutation(
    tmp_path: Path,
) -> None:
    launcher, source, runtime_root, origin_sha = _runtime_fixture(tmp_path)
    runtime = runtime_root / "production-runtime"
    (runtime / "tracked.txt").write_text("terminal replay preserved\n", encoding="utf-8")
    _write_anchored_committed_runtime_transaction(
        launcher, source=source, runtime_root=runtime_root, origin_sha=origin_sha, index=999
    )
    transaction_id = "20260810T000000000999Z-00000000000003e7"
    archive_path = runtime_root / "quarantine" / transaction_id / "runtime-recovery.json"
    transaction_dir = runtime_root / "transactions" / transaction_id
    transaction_dir.mkdir(parents=True, exist_ok=True)
    archive_path.replace(transaction_dir / "runtime-recovery.json")
    with pytest.raises(RuntimeError, match="PRODUCTION_RUNTIME_RECOVERY_TERMINAL_REPLAY"):
        launcher.converge_production_runtime(
            source_repo=source, runtime_root=runtime_root, origin_sha=origin_sha
        )
    assert (runtime / "tracked.txt").read_text(encoding="utf-8") == "terminal replay preserved\n"


def test_runtime_recovery_forwards_archive_to_missing_terminal_after_crash(
    tmp_path: Path,
) -> None:
    launcher, source, runtime_root, origin_sha = _runtime_fixture(tmp_path)
    _write_anchored_committed_runtime_transaction(
        launcher, source=source, runtime_root=runtime_root, origin_sha=origin_sha, index=1000
    )
    transaction_id = "20260810T000000001000Z-00000000000003e8"
    terminal_path = runtime_root / "ledger" / "terminals" / f"{transaction_id}.json"
    terminal_path.unlink()
    recovered = launcher.converge_production_runtime(
        source_repo=source, runtime_root=runtime_root, origin_sha=origin_sha
    )
    assert recovered["phase"] == "committed"
    assert terminal_path.is_file()
    assert not (runtime_root / "transactions" / transaction_id).exists()


def test_runtime_recovery_requires_runtime_external_canonical_authority_ledger(
    tmp_path: Path,
) -> None:
    launcher, source, runtime_root, origin_sha = _runtime_fixture(tmp_path)
    transaction_id = "20260810T120000000000Z-0123456789abcdee"
    launcher._issue_runtime_recovery_authority(
        transaction_id=transaction_id,
        runtime_root=runtime_root,
        origin_sha=origin_sha,
        source_common=launcher._git_common_dir(source),
    )
    canonical = launcher._runtime_recovery_canonical_authority_ledger_path(runtime_root)
    canonical.unlink()
    (runtime_root / "transactions" / transaction_id).mkdir(parents=True)
    with pytest.raises(RuntimeError, match="PRODUCTION_RUNTIME_RECOVERY_AUTHORITY_INVALID"):
        launcher.converge_production_runtime(
            source_repo=source, runtime_root=runtime_root, origin_sha=origin_sha
        )


def test_runtime_recovery_rejects_parent_junction_swap_without_external_write_or_move(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    parent = tmp_path / "managed"
    try:
        parent.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable")
    module = _load_task_launcher_module()
    with pytest.raises(RuntimeError, match="PRODUCTION_RUNTIME_REPARSE_INVALID"):
        with module._managed_directory_handle(
            parent / "child", tmp_path, "PRODUCTION_RUNTIME_REPARSE_INVALID"
        ):
            pass


def test_runtime_recovery_forwards_after_partial_replacement_worktree_add_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher, source, runtime_root, origin_sha = _runtime_fixture(tmp_path)
    runtime = runtime_root / "production-runtime"
    (runtime / "tracked.txt").write_text("partial add preserved\n", encoding="utf-8")
    real_run_git = launcher._run_git
    injected = {"raised": False}

    def crash_after_add(repo, *args, **kwargs):
        result = real_run_git(repo, *args, **kwargs)
        if args[:3] == ("worktree", "add", "--detach") and not injected["raised"]:
            injected["raised"] = True
            raise RuntimeError("INJECTED_AFTER_PARTIAL_REPLACEMENT_ADD")
        return result

    monkeypatch.setattr(launcher, "_run_git", crash_after_add)
    with pytest.raises(RuntimeError, match="INJECTED_AFTER_PARTIAL_REPLACEMENT_ADD"):
        launcher.converge_production_runtime(
            source_repo=source, runtime_root=runtime_root, origin_sha=origin_sha
        )
    monkeypatch.setattr(launcher, "_run_git", real_run_git)
    recovered = launcher.converge_production_runtime(
        source_repo=source, runtime_root=runtime_root, origin_sha=origin_sha
    )
    assert recovered["phase"] == "committed"
    assert _git(runtime, "rev-parse", "HEAD") == origin_sha
    assert (Path(recovered["quarantinePath"]) / "tracked.txt").read_text(encoding="utf-8") == "partial add preserved\n"


def test_runtime_recovery_forwards_after_partial_promotion_move_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher, source, runtime_root, origin_sha = _runtime_fixture(tmp_path)
    runtime = runtime_root / "production-runtime"
    (runtime / "tracked.txt").write_text("partial move preserved\n", encoding="utf-8")
    real_run_git = launcher._run_git
    injected = {"raised": False}

    def crash_after_move(repo, *args, **kwargs):
        result = real_run_git(repo, *args, **kwargs)
        if args[:2] == ("worktree", "move") and not injected["raised"]:
            injected["raised"] = True
            raise RuntimeError("INJECTED_AFTER_PARTIAL_PROMOTION_MOVE")
        return result

    monkeypatch.setattr(launcher, "_run_git", crash_after_move)
    with pytest.raises(RuntimeError, match="INJECTED_AFTER_PARTIAL_PROMOTION_MOVE"):
        launcher.converge_production_runtime(
            source_repo=source, runtime_root=runtime_root, origin_sha=origin_sha
        )
    monkeypatch.setattr(launcher, "_run_git", real_run_git)
    recovered = launcher.converge_production_runtime(
        source_repo=source, runtime_root=runtime_root, origin_sha=origin_sha
    )
    assert recovered["phase"] == "committed"
    assert _git(runtime, "rev-parse", "HEAD") == origin_sha
    assert (Path(recovered["quarantinePath"]) / "tracked.txt").read_text(encoding="utf-8") == "partial move preserved\n"


def test_runtime_recovery_capacity_admission_precedes_runtime_move_and_bounds_metadata(
    tmp_path: Path,
) -> None:
    launcher, source, runtime_root, origin_sha = _runtime_fixture(tmp_path)
    runtime = runtime_root / "production-runtime"
    (runtime / "tracked.txt").write_text("capacity preserved\n", encoding="utf-8")
    issue_dir = runtime_root / "ledger" / "issues"
    issue_dir.mkdir(parents=True, exist_ok=True)
    for index in range(launcher.RUNTIME_LEDGER_MAX_ENTRIES):
        (issue_dir / f"capacity-{index:02d}.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="PRODUCTION_RUNTIME_RECOVERY_MAINTENANCE_REQUIRED"):
        launcher.converge_production_runtime(
            source_repo=source, runtime_root=runtime_root, origin_sha=origin_sha
        )
    assert (runtime / "tracked.txt").read_text(encoding="utf-8") == "capacity preserved\n"


def test_parallel_hotfix_daily_audit_startup_ledger_fallback_uses_canonical_python_and_ops_root() -> None:
    """startup失敗はbroker ledger/failure receiptへ戻り、canonical実行面だけを使う。"""
    import inspect
    from tools import audit_recovery_control, news_grasp_daily_control

    daily_source = inspect.getsource(news_grasp_daily_control.prepare_recovery)
    audit_source = inspect.getsource(audit_recovery_control.execute_audit_recovery)
    assert "blocked_startup_self_repair_failed" in daily_source
    assert "failureReceiptSha256" in daily_source
    assert "canonical_python" in audit_source
    assert "production_runtime_root" in audit_source
    assert "ops_root" in audit_source
