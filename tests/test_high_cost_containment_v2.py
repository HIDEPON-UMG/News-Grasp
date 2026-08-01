from __future__ import annotations

import pytest


FINAL_E2E_OBJECTIVE = (
    "News-Graspは全上流Green後にだけ単一の最終production-equivalent "
    "NoPublish E2Eを実行する。重複探索・E2E連発・無駄な外部model起動を禁止する。"
)


def test_complaint_race_defers_call_and_runs_local_path(tmp_path) -> None:
    from tools.harness.high_cost_control_v2 import HighCostControlStore, MemoryAnchor

    store = HighCostControlStore.create_for_test(tmp_path / "ledger.sqlite3", MemoryAnchor())
    admission = store.issue_for_test(task_identity="task-1", max_calls=1, request_id="issue-1")
    marker = tmp_path / "local-result.txt"
    receipt = store.contain_and_continue(
        task_identity="task-1",
        reason="usage_complaint",
        local_operation=lambda: marker.write_text("submitted", encoding="utf-8"),
    )
    assert receipt["highCostOperationState"] == "operation_deferred"
    assert receipt["taskState"] == "running"
    assert receipt["localResultObserved"] is True
    assert marker.read_text(encoding="utf-8") == "submitted"
    assert store.try_reserve_call(admission, call_id="late-call", request_id="late") is None
    store.close()


def test_japanese_goal_allows_exactly_one_final_nopublish_e2e() -> None:
    from tools.harness.high_cost_control_v2 import _objective_full_e2e_limit

    assert _objective_full_e2e_limit(FINAL_E2E_OBJECTIVE) == 1


def test_news_grasp_final_e2e_goal_gets_minimum_normal_path_model_budget() -> None:
    from tools.harness.high_cost_control_v2 import _objective_external_model_limit

    assert _objective_external_model_limit(FINAL_E2E_OBJECTIVE) == 9


def test_ambiguous_japanese_goal_does_not_gain_external_model_budget() -> None:
    from tools.harness.high_cost_control_v2 import _objective_external_model_limit

    assert _objective_external_model_limit("単一の最終E2Eを実行する") == 0


def test_unconsumed_zero_limits_can_promote_from_same_goal_semantics(tmp_path) -> None:
    from tools.harness.high_cost_control_v2 import (
        CanonicalAuthority,
        HighCostControlStore,
        MemoryAnchor,
    )

    authority = CanonicalAuthority(
        "task-1", "thread-1", "a" * 64, "goal-1", FINAL_E2E_OBJECTIVE
    )
    store = HighCostControlStore.create_for_test(
        tmp_path / "ledger.sqlite3", MemoryAnchor()
    )
    store.ensure_production_task(
        authority=authority,
        max_calls=0,
        max_full_e2e_attempts=0,
        request_id="issue:task-1",
    )

    store.ensure_production_task(
        authority=authority,
        max_calls=9,
        max_full_e2e_attempts=1,
        request_id="issue:task-1",
    )

    row = store.db.execute(
        "SELECT max_calls,max_full_e2e_attempts,call_count,full_e2e_attempt_count "
        "FROM tasks WHERE task_identity='task-1'"
    ).fetchone()
    assert tuple(row) == (9, 1, 0, 0)
    event = store.db.execute(
        "SELECT event_type FROM events WHERE task_identity='task-1' ORDER BY sequence DESC"
    ).fetchone()
    assert event["event_type"] == "limits_promoted_from_goal_semantics"
    store.close()


def test_consumed_or_nonzero_limits_cannot_be_promoted(tmp_path) -> None:
    from tools.harness.high_cost_control_v2 import (
        CanonicalAuthority,
        ControlError,
        HighCostControlStore,
        MemoryAnchor,
    )

    authority = CanonicalAuthority(
        "task-1", "thread-1", "a" * 64, "goal-1", FINAL_E2E_OBJECTIVE
    )
    store = HighCostControlStore.create_for_test(
        tmp_path / "ledger.sqlite3", MemoryAnchor()
    )
    store.ensure_production_task(
        authority=authority,
        max_calls=0,
        max_full_e2e_attempts=0,
        request_id="issue:task-1",
    )
    store.db.execute("UPDATE tasks SET call_count=1 WHERE task_identity='task-1'")
    store.db.commit()

    with pytest.raises(ControlError, match="HIGH_COST_ISSUED_LIMIT_MISMATCH"):
        store.ensure_production_task(
            authority=authority,
            max_calls=9,
            max_full_e2e_attempts=1,
            request_id="issue:task-1",
        )
    store.close()


def test_promoted_limits_cannot_be_changed_again(tmp_path) -> None:
    from tools.harness.high_cost_control_v2 import (
        CanonicalAuthority,
        ControlError,
        HighCostControlStore,
        MemoryAnchor,
    )

    authority = CanonicalAuthority(
        "task-1", "thread-1", "a" * 64, "goal-1", FINAL_E2E_OBJECTIVE
    )
    store = HighCostControlStore.create_for_test(
        tmp_path / "ledger.sqlite3", MemoryAnchor()
    )
    store.ensure_production_task(
        authority=authority,
        max_calls=0,
        max_full_e2e_attempts=0,
        request_id="issue:task-1",
    )
    store.ensure_production_task(
        authority=authority,
        max_calls=9,
        max_full_e2e_attempts=1,
        request_id="issue:task-1",
    )

    with pytest.raises(ControlError, match="HIGH_COST_ISSUED_LIMIT_MISMATCH"):
        store.ensure_production_task(
            authority=authority,
            max_calls=8,
            max_full_e2e_attempts=1,
            request_id="issue:task-1",
        )
    store.close()
