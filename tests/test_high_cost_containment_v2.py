from __future__ import annotations


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
