"""S1 clean-room admission suite.

The fixture is a mechanically copied projection of the accepted V2/V3 impact
receipts.  The tests deliberately import the real S1 production symbols only
when a node executes; there is no fallback implementation or traceback
normalization.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib
import json
from pathlib import Path
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pytest


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "news_grasp_cleanroom_s1_cases.json"
ACTIVE_TZ = ZoneInfo("Asia/Tokyo")
EXPECTED_TOP_LEVEL_NODES = (
    "test_s1_manifest_single_entry",
    "test_s1_unknown_manifest_zero_effect",
    "test_s1_terminal_slot_idempotent",
    "test_s1_wal_precedes_parse",
    "test_s1_wal_fsync_failure_zero_effect",
    "test_s1_zero_entry_recovery",
    "test_s1_three_writer_single_owner",
    "test_s1_stale_fence_cannot_commit",
    "test_s1_ledger_corruption_recovery",
    "test_s1_time_window_primary",
    "test_s1_clock_rollback_and_dst_fail_closed",
    "test_s1_missed_scheduled_persists_then_audits",
    "test_edge_writer_crash_primary",
    "test_edge_writer_crash_adversarial",
    "test_edge_writer_crash_recovery",
)


class OwnedCrash(RuntimeError):
    """Boundary seam exception owned by this test suite."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_fixture() -> dict[str, Any]:
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert data["schemaVersion"] == "NEWS_GRASP_CLEANROOM_S1_CASES_V1"
    assert data["packetId"] == "NG-CLEANROOM-S1-RED-V1"
    assert data["baselineCommit"] == "76b5309d74887c86c863bf20a1b188c9f7d6fda7"
    assert tuple(data["topLevelTestNodes"]) == EXPECTED_TOP_LEVEL_NODES
    assert len(data["topLevelTestNodes"]) == len(set(data["topLevelTestNodes"])) == 15

    root = FIXTURE_PATH.parents[2]
    expected_source_artifacts = {
        "config/news_grasp_cleanroom_control_s0_v2.json": "2b76d2b0aa73d22a43e0279c93cea3eecf3dff0874da6384c6abb74ed61d3a91",
        "config/news_grasp_cleanroom_s1_impact_receipt_v1.json": "8127c93297ce8d82528654751a4996c4ea085d91d888be651ea64ac5c3dd685c",
        "config/news_grasp_cleanroom_s1_impact_receipt_v2.json": "fdd3f3bcc8b83c458a8df5f5a7a9767767871c098adfe9feedebd502a11850ef",
        "config/news_grasp_cleanroom_s1_impact_receipt_v3.json": "6002f61ca6f7249e99db0b791aae5020883ff11e3f5aa4fb56f8f054846f1a08",
        "config/news_grasp_cleanroom_s1_impact_review_v3.json": "a484fc5a0c8486f096fbc19f340d9988067c9536517ce4ea437b0ddd11bb1087",
        "tools/news_grasp_cleanroom_contracts.py": "b5e0904636085228f2e59a66c8cc4551ae78ce0db18f247fcc9d3b690668c6e2",
    }
    assert data["sourceArtifacts"] == expected_source_artifacts
    immutable_source_artifacts = {
        relative_path: expected_hash
        for relative_path, expected_hash in expected_source_artifacts.items()
        if relative_path != "tools/news_grasp_cleanroom_contracts.py"
    }
    for relative_path, expected_hash in immutable_source_artifacts.items():
        assert _sha256(root / relative_path) == expected_hash

    v2 = json.loads(
        (root / "config/news_grasp_cleanroom_s1_impact_receipt_v2.json").read_text(encoding="utf-8")
    )
    v3 = json.loads(
        (root / "config/news_grasp_cleanroom_s1_impact_receipt_v3.json").read_text(encoding="utf-8")
    )
    normative = data["normative"]
    assert normative["manifest"] == v2["exactSchemas"]["manifest"]
    assert normative["rawArgv"] == v2["exactSchemas"]["rawArgv"]
    assert normative["writer"] == v2["exactSchemas"]["writer"]
    assert normative["time"] == v2["exactSchemas"]["time"]
    assert normative["lease"] == v2["exactSchemas"]["lease"]
    assert normative["pathLayout"] == v2["exactSchemas"]["pathLayout"]
    assert normative["reasonBindings"] == v2["reasonBindings"]
    assert normative["decisionTable"] == v2["decisionTable"]
    assert normative["faultRecoveryOracle"] == v2["faultRecoveryOracle"]
    assert normative["recoveryFaultOracle"] == v2["recoveryFaultOracle"]
    assert normative["dispatchProjectionSchema"] == v3["dispatchProjectionSchema"]
    assert normative["slotDecisionResultMatrix"] == v3["slotDecisionResultMatrix"]
    assert normative["dispatchResultValueMatrix"] == v3["dispatchResultValueMatrix"]
    assert normative["recoveryBoundaryHooks"] == v3["recoveryBoundaryHooks"]
    assert normative["recoveryHookExceptionPolicy"] == v3["recoveryHookExceptionPolicy"]
    assert normative["correctedRecoverWrapperSignature"] == v3["correctedRecoverWrapperSignature"]

    dispatch_cases = data["dispatchCases"]
    assert dispatch_cases == v3["dispatchResultValueMatrix"]
    dispatch_ids = [case["caseId"] for case in dispatch_cases]
    assert len(dispatch_ids) == len(set(dispatch_ids)) == 13
    assert data["dispatchCommitBoundaries"] == v2["faultRecoveryOracle"]
    boundary_names = [row["boundary"] for row in data["dispatchCommitBoundaries"]]
    assert len(boundary_names) == len(set(boundary_names)) == 9
    assert data["recoveryCases"] == v3["recoveryBoundaryHooks"]
    recovery_names = [row["hook"] for row in data["recoveryCases"]]
    assert len(recovery_names) == len(set(recovery_names)) == 5

    invalid_inputs = data["invalidInputs"]
    invalid_ids = [case["caseId"] for case in invalid_inputs]
    assert len(invalid_ids) == len(set(invalid_ids)) == 17
    assert all(isinstance(case["expectedReason"], str) and case["expectedReason"] for case in invalid_inputs)
    expected_reasons = {row["reason"] for row in v2["reasonBindings"] if row["reason"] is not None}
    assert {case["expectedReason"] for case in invalid_inputs} == expected_reasons
    return data


def _production() -> dict[str, Any]:
    """Load only the real S1 symbols; missing symbols must fail with traceback."""
    contracts = importlib.import_module("tools.news_grasp_cleanroom_contracts")
    wal = importlib.import_module("tools.news_grasp_cleanroom_wal")
    ledger = importlib.import_module("tools.news_grasp_cleanroom_ledger")
    controller = importlib.import_module("tools.news_grasp_cleanroom_controller")
    dispatch_module = importlib.import_module("tools.news_grasp_cleanroom_dispatch")
    return {
        "error": contracts.CleanroomEntryError,
        "validate_manifest": contracts.validate_manifest,
        "reconcile_slot": contracts.reconcile_slot,
        "DurabilityOps": wal.DurabilityOps,
        "DurableWal": wal.DurableWal,
        "ControlLedger": ledger.ControlLedger,
        "Controller": controller.Controller,
        "dispatch": dispatch_module.dispatch,
        "commit_slot": dispatch_module.commit_slot,
        "inspect_control_state": dispatch_module.inspect_control_state,
        "recover_ledger": dispatch_module.recover_ledger,
    }


def _manifest(data: dict[str, Any]) -> dict[str, Any]:
    schema = data["normative"]["manifest"]
    return {
        "schemaVersion": schema["schemaVersion"],
        "scheduleId": schema["scheduleId"],
        "tasks": [deepcopy(schema["task"])],
    }


def _write_manifest(tmp_path: Path, data: dict[str, Any]) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(_manifest(data), ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return path


def _writer(index: int) -> dict[str, Any]:
    return {
        "writerId": f"s1-test-{index}",
        "bootId": "s1-test-boot",
        "pid": 1000 + index,
        "processStartToken": f"s1-process-{index}",
    }


def _at(hour: int, minute: int, second: int = 0, *, day: int = 21) -> datetime:
    return datetime(2026, 8, day, hour, minute, second, tzinfo=ACTIVE_TZ)


def _controller(production: dict[str, Any], runtime_root: Path, manifest_path: Path, **kwargs: Any) -> Any:
    return production["Controller"](runtime_root=runtime_root, manifest_path=manifest_path, **kwargs)


def _expect_reason(error_type: type[Exception], expected: str, operation: Callable[[], Any]) -> None:
    try:
        operation()
    except error_type as exc:
        if getattr(exc, "reason", None) != expected:
            raise
    else:
        pytest.fail(f"expected production reason {expected}")


def _assert_real_sqlite(runtime_root: Path) -> None:
    ledger_path = runtime_root / "control" / "control-ledger-v1.sqlite3"
    assert ledger_path.read_bytes()[:16] == b"SQLite format 3\x00"
    with sqlite3.connect(ledger_path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2


def _result_hash(schedule_id: str, issue_date: str, terminal_state: str) -> str:
    payload = json.dumps(
        {"scheduleId": schedule_id, "issueDate": issue_date, "terminalState": terminal_state},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def test_s1_manifest_single_entry() -> None:
    data = _load_fixture()
    production = _production()
    manifest = _manifest(data)
    assert production["validate_manifest"](manifest) == manifest
    invalid = deepcopy(manifest)
    invalid["tasks"][0]["triggers"].append(deepcopy(invalid["tasks"][0]["triggers"][0]))
    _expect_reason(
        production["error"],
        "NEWS_GRASP_ENTRY_MANIFEST_INVALID",
        lambda: production["validate_manifest"](invalid),
    )


def test_s1_unknown_manifest_zero_effect(tmp_path: Path) -> None:
    data = _load_fixture()
    production = _production()
    manifest_path = _write_manifest(tmp_path, data)
    runtime_root = tmp_path / "runtime"
    common = {
        "runtime_root": runtime_root,
        "manifest_path": manifest_path,
        "observed_at": _at(6, 1),
        "writer": _writer(1),
    }
    invalid_calls = (
        (["dispatch"], "NEWS_GRASP_ENTRY_ARGS_INVALID"),
        (["dispatch", "--schedule-id", "news-grasp-daily-v1", "--intent", "publish"], "NEWS_GRASP_ENTRY_UNKNOWN_INTENT"),
        (["dispatch", "--schedule-id", "other", "--intent", "reconcile"], "NEWS_GRASP_ENTRY_UNKNOWN_SCHEDULE"),
    )
    for raw_argv, reason in invalid_calls:
        _expect_reason(
            production["error"],
            reason,
            lambda raw_argv=raw_argv: production["dispatch"](raw_argv=raw_argv, **common),
        )
    assert not (runtime_root / "control" / "control-ledger-v1.sqlite3").exists()


def test_s1_terminal_slot_idempotent(tmp_path: Path) -> None:
    data = _load_fixture()
    production = _production()
    manifest_path = _write_manifest(tmp_path, data)
    runtime_root = tmp_path / "runtime"
    writer = _writer(1)
    controller = _controller(production, runtime_root, manifest_path)
    decision = controller.reconcile(
        raw_argv=data["normative"]["rawArgv"]["exact"],
        observed_at=_at(6, 1),
        writer=writer,
        lease_seconds=120,
    )
    committed = controller.commit_slot(
        slot_key=decision["slotKey"],
        writer=writer,
        fence_token=decision["fenceToken"],
        terminal_state="SUCCEEDED",
        result_hash="a" * 64,
        observed_at=_at(6, 2),
    )
    assert committed["status"] == "committed"
    replay = controller.commit_slot(
        slot_key=decision["slotKey"],
        writer=writer,
        fence_token=decision["fenceToken"],
        terminal_state="SUCCEEDED",
        result_hash="a" * 64,
        observed_at=_at(6, 2),
    )
    assert replay["status"] == "noop"
    _expect_reason(
        production["error"],
        "NEWS_GRASP_ENTRY_TERMINAL_CONFLICT",
        lambda: controller.commit_slot(
            slot_key=decision["slotKey"],
            writer=writer,
            fence_token=decision["fenceToken"],
            terminal_state="FAILED",
            result_hash="b" * 64,
            observed_at=_at(6, 2),
        ),
    )


def test_s1_wal_precedes_parse(tmp_path: Path) -> None:
    data = _load_fixture()
    production = _production()
    manifest_path = _write_manifest(tmp_path, data)
    runtime_root = tmp_path / "runtime"
    _expect_reason(
        production["error"],
        "NEWS_GRASP_ENTRY_ARGS_INVALID",
        lambda: production["dispatch"](
            raw_argv=["dispatch"],
            runtime_root=runtime_root,
            manifest_path=manifest_path,
            observed_at=_at(6, 1),
            writer=_writer(1),
        ),
    )
    initial_wal = list((runtime_root / "control" / "wal").glob("*/0001-initial.json"))
    assert len(initial_wal) == 1
    assert json.loads(initial_wal[0].read_text(encoding="utf-8"))["phase"] == "INITIAL_DURABLE"
    assert not (runtime_root / "control" / "control-ledger-v1.sqlite3").exists()


def test_s1_wal_fsync_failure_zero_effect(tmp_path: Path) -> None:
    data = _load_fixture()
    production = _production()
    manifest_path = _write_manifest(tmp_path, data)

    def fail_once(*_: Any, **__: Any) -> None:
        raise OSError("test-owned durability failure")

    for operation in ("fsync", "replace", "flush_parent"):
        runtime_root = tmp_path / operation
        durability_ops = production["DurabilityOps"](**{operation: fail_once})
        _expect_reason(
            production["error"],
            "NEWS_GRASP_ENTRY_INITIAL_WAL_FAILED",
            lambda runtime_root=runtime_root, durability_ops=durability_ops: production["dispatch"](
                raw_argv=data["normative"]["rawArgv"]["exact"],
                runtime_root=runtime_root,
                manifest_path=manifest_path,
                observed_at=_at(6, 1),
                writer=_writer(1),
                durability_ops=durability_ops,
            ),
        )
        assert not (runtime_root / "control" / "control-ledger-v1.sqlite3").exists()


def test_s1_zero_entry_recovery(tmp_path: Path) -> None:
    data = _load_fixture()
    production = _production()
    manifest_path = _write_manifest(tmp_path, data)
    runtime_root = tmp_path / "runtime"

    def crash_after_initial(hook: str) -> None:
        if hook == "after_initial_wal_fsync":
            raise OwnedCrash(hook)

    controller = _controller(production, runtime_root, manifest_path, boundary_hook=crash_after_initial)
    with pytest.raises(OwnedCrash):
        controller.reconcile(
            raw_argv=data["normative"]["rawArgv"]["exact"],
            observed_at=_at(6, 1),
            writer=_writer(1),
        )
    del controller
    reopened = _controller(production, runtime_root, manifest_path)
    result = reopened.reconcile(
        raw_argv=data["normative"]["rawArgv"]["exact"],
        observed_at=_at(6, 1, 1),
        writer=_writer(1),
    )
    assert result["ownerDisposition"] == "ACQUIRED"
    state = reopened.inspect_control_state()
    assert len(state["invocations"]) == 2
    _assert_real_sqlite(runtime_root)


def test_s1_three_writer_single_owner(tmp_path: Path) -> None:
    data = _load_fixture()
    production = _production()
    manifest_path = _write_manifest(tmp_path, data)
    runtime_root = tmp_path / "runtime"

    def run(index: int) -> dict[str, Any]:
        instance = _controller(production, runtime_root, manifest_path)
        return instance.reconcile(
            raw_argv=data["normative"]["rawArgv"]["exact"],
            observed_at=_at(6, 1),
            writer=_writer(index),
        )

    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(run, (1, 2, 3)))
    assert sum(result["ownerDisposition"] == "ACQUIRED" for result in results) == 1
    assert sum(result["ownerDisposition"] == "ATTACHED" for result in results) == 2
    assert len({result["ownerKey"] for result in results}) == 1
    assert len({result["fenceToken"] for result in results}) == 1
    _assert_real_sqlite(runtime_root)


def test_s1_stale_fence_cannot_commit(tmp_path: Path) -> None:
    data = _load_fixture()
    production = _production()
    manifest_path = _write_manifest(tmp_path, data)
    runtime_root = tmp_path / "runtime"
    first = _controller(production, runtime_root, manifest_path)
    old_writer = _writer(1)
    old = first.reconcile(
        raw_argv=data["normative"]["rawArgv"]["exact"],
        observed_at=_at(6, 1),
        writer=old_writer,
        lease_seconds=1,
    )
    takeover = _controller(production, runtime_root, manifest_path).reconcile(
        raw_argv=data["normative"]["rawArgv"]["exact"],
        observed_at=_at(6, 1, 2),
        writer=_writer(2),
        lease_seconds=120,
    )
    assert takeover["fenceToken"] == old["fenceToken"] + 1
    _expect_reason(
        production["error"],
        "NEWS_GRASP_ENTRY_STALE_FENCE",
        lambda: first.commit_slot(
            slot_key=old["slotKey"],
            writer=old_writer,
            fence_token=old["fenceToken"],
            terminal_state="SUCCEEDED",
            result_hash="a" * 64,
            observed_at=_at(6, 1, 3),
        ),
    )


def test_s1_ledger_corruption_recovery(tmp_path: Path) -> None:
    data = _load_fixture()
    production = _production()
    manifest_path = _write_manifest(tmp_path, data)
    runtime_root = tmp_path / "runtime"
    controller = _controller(production, runtime_root, manifest_path)
    controller.reconcile(
        raw_argv=data["normative"]["rawArgv"]["exact"],
        observed_at=_at(6, 1),
        writer=_writer(1),
    )
    del controller
    ledger_path = runtime_root / "control" / "control-ledger-v1.sqlite3"
    _assert_real_sqlite(runtime_root)
    corrupted = bytearray(ledger_path.read_bytes())
    corrupted[:16] = b"not a sqlite file"
    ledger_path.write_bytes(corrupted)
    reopened = _controller(production, runtime_root, manifest_path)
    recovery = reopened.recover_ledger(observed_at=_at(6, 2))
    assert recovery["status"] == "recovered"
    assert recovery["newGeneration"] == recovery["oldGeneration"] + 1
    del reopened
    recovered = _controller(production, runtime_root, manifest_path)
    recovered.inspect_control_state()
    _assert_real_sqlite(runtime_root)


def test_s1_time_window_primary() -> None:
    data = _load_fixture()
    production = _production()
    manifest = _manifest(data)
    expected = {
        5: data["normative"]["slotDecisionResultMatrix"][0]["result"],
        6: data["normative"]["slotDecisionResultMatrix"][1]["result"],
        7: data["normative"]["slotDecisionResultMatrix"][2]["result"],
    }
    for hour, result_oracle in expected.items():
        observed = _at(hour, 59, 59) if hour == 5 else _at(hour, 0)
        result = production["reconcile_slot"](
            manifest=manifest,
            observed_at=observed,
            last_observed_at=None,
            scheduled_state="ABSENT",
        )
        assert result["schemaVersion"] == result_oracle["schemaVersion"]
        assert result["decision"] == result_oracle["decision"]
        expected_scheduled_state = result_oracle["scheduledState"]
        if expected_scheduled_state == "same as scheduledPreState":
            expected_scheduled_state = "ABSENT"
        assert result["scheduledState"] == expected_scheduled_state
        assert result["externalEffectCount"] == 0
    exact_0640 = production["reconcile_slot"](
        manifest=manifest,
        observed_at=_at(6, 40),
        last_observed_at=None,
        scheduled_state="ABSENT",
    )
    assert exact_0640["decision"] == "MISSED_SCHEDULED_AND_ENSURE_AUDIT"


def test_s1_clock_rollback_and_dst_fail_closed() -> None:
    data = _load_fixture()
    production = _production()
    manifest = _manifest(data)
    invalid_times = (
        datetime(2026, 8, 21, 6, 1),
        datetime(2026, 8, 21, 6, 1, tzinfo=timezone.utc),
        datetime(2026, 8, 21, 6, 1, tzinfo=ACTIVE_TZ, fold=1),
    )
    for invalid_time in invalid_times:
        _expect_reason(
            production["error"],
            "NEWS_GRASP_ENTRY_TIME_INVALID",
            lambda invalid_time=invalid_time: production["reconcile_slot"](
                manifest=manifest,
                observed_at=invalid_time,
                last_observed_at=None,
                scheduled_state="ABSENT",
            ),
        )
    _expect_reason(
        production["error"],
        "NEWS_GRASP_ENTRY_CLOCK_ROLLBACK",
        lambda: production["reconcile_slot"](
            manifest=manifest,
            observed_at=_at(6, 0),
            last_observed_at=_at(6, 1),
            scheduled_state="ABSENT",
        ),
    )


def test_s1_missed_scheduled_persists_then_audits(tmp_path: Path) -> None:
    data = _load_fixture()
    production = _production()
    manifest_path = _write_manifest(tmp_path, data)
    runtime_root = tmp_path / "runtime"
    controller = _controller(production, runtime_root, manifest_path)
    result = controller.reconcile(
        raw_argv=data["normative"]["rawArgv"]["exact"],
        observed_at=_at(6, 40),
        writer=_writer(1),
    )
    assert result["decision"] == "MISSED_SCHEDULED_AND_ENSURE_AUDIT"
    assert result["scheduledState"] == "MISSED_SCHEDULED"
    state = controller.inspect_control_state()
    missed = [
        row
        for row in state["slots"]
        if row["slotKind"] == "Scheduled" and row["terminalState"] == "MISSED_SCHEDULED"
    ]
    assert len(missed) == 1
    row = missed[0]
    assert row["ownerKey"] == "system:reconcile"
    assert row["fenceToken"] == 1
    assert row["resultHash"] == _result_hash("news-grasp-daily-v1", row["issueDate"], "MISSED_SCHEDULED")
    _assert_real_sqlite(runtime_root)


def test_edge_writer_crash_primary(tmp_path: Path) -> None:
    data = _load_fixture()
    production = _production()
    manifest_path = _write_manifest(tmp_path, data)
    for index, boundary_row in enumerate(data["dispatchCommitBoundaries"], start=1):
        runtime_root = tmp_path / f"boundary-{index}"
        hook_name = boundary_row["boundary"]

        def boundary_hook(hook: str, expected=hook_name) -> None:
            if hook == expected:
                raise OwnedCrash(hook)

        controller = _controller(production, runtime_root, manifest_path, boundary_hook=boundary_hook)
        with pytest.raises(OwnedCrash):
            controller.reconcile(
                raw_argv=data["normative"]["rawArgv"]["exact"],
                observed_at=_at(6, 1),
                writer=_writer(index),
            )
        del controller
        reopened = _controller(production, runtime_root, manifest_path)
        reopened.reconcile(
            raw_argv=data["normative"]["rawArgv"]["exact"],
            observed_at=_at(6, 1, 1),
            writer=_writer(index),
        )
        _assert_real_sqlite(runtime_root)


def test_edge_writer_crash_adversarial(tmp_path: Path) -> None:
    data = _load_fixture()
    production = _production()
    manifest_path = _write_manifest(tmp_path, data)
    for index, boundary_row in enumerate(data["dispatchCommitBoundaries"], start=1):
        runtime_root = tmp_path / f"adversarial-{index}"
        hook_name = boundary_row["boundary"]

        def boundary_hook(hook: str, expected=hook_name) -> None:
            if hook == expected:
                raise OwnedCrash(hook)

        stale = _controller(production, runtime_root, manifest_path, boundary_hook=boundary_hook)
        with pytest.raises(OwnedCrash):
            stale.reconcile(
                raw_argv=data["normative"]["rawArgv"]["exact"],
                observed_at=_at(6, 1),
                writer=_writer(index),
                lease_seconds=1,
            )
        del stale
        current = _controller(production, runtime_root, manifest_path)
        acquired = current.reconcile(
            raw_argv=data["normative"]["rawArgv"]["exact"],
            observed_at=_at(6, 1, 2),
            writer=_writer(index + 100),
        )
        if acquired["ownerDisposition"] == "ACQUIRED":
            _expect_reason(
                production["error"],
                "NEWS_GRASP_ENTRY_STALE_FENCE",
                lambda: current.commit_slot(
                    slot_key=acquired["slotKey"],
                    writer=_writer(index),
                    fence_token=max(1, acquired["fenceToken"] - 1),
                    terminal_state="SUCCEEDED",
                    result_hash="a" * 64,
                    observed_at=_at(6, 1, 3),
                ),
            )


def test_edge_writer_crash_recovery(tmp_path: Path) -> None:
    data = _load_fixture()
    production = _production()
    manifest_path = _write_manifest(tmp_path, data)
    for index, hook_row in enumerate(data["recoveryCases"], start=1):
        runtime_root = tmp_path / f"recovery-{index}"
        initial = _controller(production, runtime_root, manifest_path)
        initial.reconcile(
            raw_argv=data["normative"]["rawArgv"]["exact"],
            observed_at=_at(6, 1),
            writer=_writer(index),
        )
        del initial
        ledger_path = runtime_root / "control" / "control-ledger-v1.sqlite3"
        corrupted = bytearray(ledger_path.read_bytes())
        corrupted[:16] = b"not a sqlite file"
        ledger_path.write_bytes(corrupted)
        hook_name = hook_row["hook"]

        def boundary_hook(hook: str, expected=hook_name) -> None:
            if hook == expected:
                raise OwnedCrash(hook)

        crashed = _controller(production, runtime_root, manifest_path, boundary_hook=boundary_hook)
        with pytest.raises(OwnedCrash):
            crashed.recover_ledger(observed_at=_at(6, 2))
        del crashed
        resumed = _controller(production, runtime_root, manifest_path)
        recovery = resumed.recover_ledger(observed_at=_at(6, 2, 1))
        assert recovery["status"] == "recovered"
        assert recovery["newGeneration"] == recovery["oldGeneration"] + 1
        terminal = resumed.recover_ledger(observed_at=_at(6, 2, 2))
        assert terminal["status"] == "RECOVERY_NOT_REQUIRED"
        _assert_real_sqlite(runtime_root)
