from __future__ import annotations

import hashlib
import importlib.util
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Any


def _load_control(workspace_harness: Path) -> tuple[ModuleType, Path]:
    source = workspace_harness / "tools" / "harness" / "high_cost_control_v2.py"
    spec = importlib.util.spec_from_file_location(
        "isolated_daily_baseline_high_cost_control_v2", source
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("DAILY_BASELINE_CONTROL_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module, source


def observe_daily_budget_baseline(
    *, workspace_harness: Path, isolation_root: Path
) -> dict[str, Any]:
    control, source = _load_control(workspace_harness)
    runtime_parent = isolation_root / "artifacts" / "red-runtime"
    runtime_parent.mkdir(parents=True, exist_ok=True)
    runtime_root = Path(
        tempfile.mkdtemp(prefix="dcp03-", dir=str(runtime_parent))
    )
    store = control.HighCostControlStore.create_for_test(
        runtime_root / "ledger.sqlite3", control.MemoryAnchor()
    )
    authority_first = control.scheduled_news_grasp_authority("2026-08-05")
    authority_second = control.scheduled_news_grasp_authority("2026-08-05")
    other_day = control.scheduled_news_grasp_authority("2026-08-06")
    replay_error = ""
    root_override_error = ""
    try:
        store.ensure_production_task(
            authority=authority_first,
            max_calls=9,
            max_full_e2e_attempts=0,
            request_id=f"issue:{authority_first.task_identity}",
        )
        scheduled = store.reserve_scheduled_production_attempt(
            authority=authority_first,
            issue_date="2026-08-05",
            allow_existing=False,
        )
        try:
            store.reserve_scheduled_production_attempt(
                authority=authority_second,
                issue_date="2026-08-05",
                allow_existing=False,
            )
        except Exception as error:
            replay_error = str(error)
        before = store.db.execute(
            "SELECT task_identity,max_calls,call_count,state FROM tasks WHERE task_identity=?",
            (authority_first.task_identity,),
        ).fetchone()
        calls: list[dict[str, Any]] = []
        for index in range(2):
            call_id = f"dcp03-call-{index}"
            reservation = store.reserve_production_call(
                authority=authority_first,
                route="daily:summary",
                call_id=call_id,
            )
            started = control._mark_model_call_started_in_store(
                store=store,
                authority=authority_first,
                route="daily:summary",
                call_id=call_id,
            )
            calls.append({"reservation": reservation, "started": started})
        after = store.db.execute(
            "SELECT task_identity,max_calls,call_count,state FROM tasks WHERE task_identity=?",
            (authority_first.task_identity,),
        ).fetchone()
        try:
            authority_first.assert_no_caller_override(
                workspace_root=runtime_root / "substituted-root",
                thread_id=authority_first.thread_id,
                actual_event_hash=authority_first.actual_event_hash,
            )
        except Exception as error:
            root_override_error = str(error)
        return {
            "schemaVersion": "CURRENT_BROKER_DAILY_BASELINE_OBSERVATION_V1",
            "returnCode": 0,
            "consumerSha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "input": {
                "issueDate": "2026-08-05",
                "productId": "News-Grasp",
                "callIds": ["dcp03-call-0", "dcp03-call-1"],
            },
            "sameDateIdentityStable": (
                authority_first.task_identity == authority_second.task_identity
            ),
            "otherDateIdentityDistinct": (
                authority_first.task_identity != other_day.task_identity
            ),
            "scheduledReservation": scheduled,
            "scheduledReplayRejected": (
                "HIGH_COST_SCHEDULED_ATTEMPT_REPLAY" in replay_error
            ),
            "scheduledReplayError": replay_error,
            "rootOverrideRejected": (
                "HIGH_COST_CANONICAL_IDENTITY_MISMATCH" in root_override_error
            ),
            "rootOverrideError": root_override_error,
            "taskIdentityBefore": str(before["task_identity"]),
            "taskIdentityAfter": str(after["task_identity"]),
            "maxCallsBefore": int(before["max_calls"]),
            "maxCallsAfter": int(after["max_calls"]),
            "callCountBefore": int(before["call_count"]),
            "callCountAfter": int(after["call_count"]),
            "taskStateBefore": str(before["state"]),
            "taskStateAfter": str(after["state"]),
            "calls": calls,
        }
    finally:
        store.close()
