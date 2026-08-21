"""S1 clean-room admission suite.

The fixture is a mechanically copied projection of the accepted V2/V3 impact
receipts.  The tests deliberately import the real S1 production symbols only
when a node executes; there is no fallback implementation or traceback
normalization.
"""

from __future__ import annotations

from contextlib import closing
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import inspect
import importlib
import json
import os
from pathlib import Path
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock
from typing import Any, Callable, Mapping
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

    def test_dispatch(**kwargs: Any) -> Any:
        kwargs.setdefault("writer_attestor", _FakeWriterAttestor(valid=True))
        return dispatch_module.dispatch(**kwargs)

    return {
        "error": contracts.CleanroomEntryError,
        "validate_manifest": contracts.validate_manifest,
        "reconcile_slot": contracts.reconcile_slot,
        "DurabilityOps": wal.DurabilityOps,
        "DurableWal": wal.DurableWal,
        "ControlLedger": ledger.ControlLedger,
        "Controller": controller.Controller,
        "dispatch": test_dispatch,
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
    # Production default is OS-backed attestation; all existing deterministic
    # fixture writers must explicitly use the test-owned attestor seam.
    if (
        "writer_attestor" in inspect.signature(production["Controller"].__init__).parameters
        and "writer_attestor" not in kwargs
    ):
        kwargs["writer_attestor"] = _FakeWriterAttestor(valid=True)
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
    with closing(sqlite3.connect(ledger_path)) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2


def _tree_snapshot(root: Path) -> dict[str, tuple[str, bytes | None]]:
    snapshot: dict[str, tuple[str, bytes | None]] = {}
    root = Path(root)
    if not root.exists():
        return snapshot
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            snapshot[relative] = ("symlink", os.readlink(path).encode("utf-8"))
        elif path.is_dir():
            snapshot[relative] = ("directory", None)
        else:
            snapshot[relative] = ("file", path.read_bytes())
    return snapshot


def _result_hash(schedule_id: str, issue_date: str, terminal_state: str) -> str:
    payload = json.dumps(
        {"scheduleId": schedule_id, "issueDate": issue_date, "terminalState": terminal_state},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _require_wal_compaction_api(production: dict[str, Any]) -> None:
    for owner, name in (
        (production["DurableWal"], "retention_counts"),
        (production["DurableWal"], "imported_event_records"),
        (production["DurableWal"], "compact_imported"),
        (production["ControlLedger"], "authorize_wal_compaction"),
        (production["ControlLedger"], "complete_wal_compaction"),
    ):
        if not hasattr(owner, name):
            pytest.fail(f"{owner.__name__}.{name} compaction API seam is missing")


def _seed_compaction_runtime(
    production: dict[str, Any],
    data: dict[str, Any],
    runtime_root: Path,
    *,
    imported_count: int = 832,
) -> tuple[Any, Any, tuple[dict[str, Any], ...], datetime]:
    """832件のimported WALと対応するSQLite invocationを、明示操作用に用意する。"""

    wal = production["DurableWal"](runtime_root)
    ledger = production["ControlLedger"](runtime_root)
    base = _at(6, 1)
    imported: list[dict[str, Any]] = []
    for index in range(imported_count):
        observed = base + timedelta(seconds=index)
        initial = wal.record_initial(
            raw_argv=data["normative"]["rawArgv"]["exact"],
            received_at=observed,
            writer=_writer(1000 + index),
        )
        imported.append(wal.mark_imported(initial, imported_at=observed))
    ledger.import_zero_entries(
        tuple(imported),
        observed_at=base + timedelta(seconds=imported_count),
    )
    return wal, ledger, tuple(imported), base


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


class _FakeWriterAttestor:
    """OS実測の代わりに、writer attestationの境界だけを注入する。"""

    def __init__(self, valid: bool) -> None:
        self.valid = valid
        self.calls: list[dict[str, Any]] = []

    def validate(self, writer: Mapping[str, Any]) -> bool:
        self.calls.append(dict(writer))
        return self.valid


def test_sec_s1_writer_attestation_rejects_fake_identity_before_wal(tmp_path: Path) -> None:
    """fake pid/boot/processStartTokenはWAL初期イベントより前に拒否する。"""

    data = _load_fixture()
    production = _production()
    manifest_path = _write_manifest(tmp_path, data)
    runtime_root = tmp_path / "runtime"
    controller_type = importlib.import_module("tools.news_grasp_cleanroom_controller").Controller
    if "writer_attestor" not in inspect.signature(controller_type.__init__).parameters:
        pytest.fail("writer_attestor constructor seam is missing")
    attestor = _FakeWriterAttestor(valid=False)
    controller = controller_type(
        runtime_root=runtime_root,
        manifest_path=manifest_path,
        writer_attestor=attestor,
    )
    _expect_reason(
        production["error"],
        "NEWS_GRASP_ENTRY_WRITER_INVALID",
        lambda: controller.reconcile(
            raw_argv=data["normative"]["rawArgv"]["exact"],
            observed_at=_at(6, 1),
            writer=_writer(101),
        ),
    )
    assert len(attestor.calls) == 1
    assert not (runtime_root / "control" / "wal").exists()


def test_sec_s1_expired_lease_rejects_terminal_commit_without_takeover(tmp_path: Path) -> None:
    """takeoverなしでもlease expiry後のterminal commitはSTALE_FENCEになる。"""

    data = _load_fixture()
    production = _production()
    manifest_path = _write_manifest(tmp_path, data)
    runtime_root = tmp_path / "runtime"
    writer = _writer(102)
    controller = _controller(production, runtime_root, manifest_path)
    acquired = controller.reconcile(
        raw_argv=data["normative"]["rawArgv"]["exact"],
        observed_at=_at(6, 1),
        writer=writer,
        lease_seconds=1,
    )
    _expect_reason(
        production["error"],
        "NEWS_GRASP_ENTRY_STALE_FENCE",
        lambda: controller.commit_slot(
            slot_key=acquired["slotKey"],
            writer=writer,
            fence_token=acquired["fenceToken"],
            terminal_state="SUCCEEDED",
            result_hash="a" * 64,
            observed_at=_at(6, 1, 2),
        ),
    )


def test_sec_s1_wal_event_and_retention_limits_fail_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """WALのevent bytes/zero entry上限を超えた履歴走査はtyped拒否する。"""

    data = _load_fixture()
    production = _production()
    wal_module = importlib.import_module("tools.news_grasp_cleanroom_wal")
    for name in ("MAX_WAL_EVENT_BYTES", "MAX_WAL_ZERO_ENTRIES", "MAX_WAL_IMPORTED_ENTRIES"):
        if not hasattr(wal_module, name):
            pytest.fail(f"{name} retention seam is missing")

    event_root = tmp_path / "event"
    event_wal = production["DurableWal"](event_root)
    initial = event_wal.record_initial(
        raw_argv=data["normative"]["rawArgv"]["exact"],
        received_at=_at(6, 1),
        writer=_writer(103),
    )
    initial_path = event_root / "control" / "wal" / initial["invocationId"] / "0001-initial.json"
    monkeypatch.setattr(wal_module, "MAX_WAL_EVENT_BYTES", 1)
    monkeypatch.setattr(wal_module, "MAX_WAL_ZERO_ENTRIES", 32)
    initial_path.write_bytes(initial_path.read_bytes() + b" " * 128)
    _expect_reason(
        production["error"],
        "NEWS_GRASP_ENTRY_WAL_RETENTION_LIMIT",
        lambda: event_wal.iter_zero_entries(),
    )

    count_root = tmp_path / "count"
    count_wal = production["DurableWal"](count_root)
    count_wal.record_initial(
        raw_argv=data["normative"]["rawArgv"]["exact"],
        received_at=_at(6, 1),
        writer=_writer(104),
    )
    monkeypatch.setattr(wal_module, "MAX_WAL_EVENT_BYTES", 65536)
    monkeypatch.setattr(wal_module, "MAX_WAL_ZERO_ENTRIES", 0)
    _expect_reason(
        production["error"],
        "NEWS_GRASP_ENTRY_WAL_RETENTION_LIMIT",
        lambda: count_wal.iter_zero_entries(),
    )


def test_sec_s1_wal_compaction_requires_exact_sqlite_parity(tmp_path: Path) -> None:
    """WAL/SQLiteの一件でも改竄・欠落したcompaction指定は、削除前に拒否する。"""

    data = _load_fixture()
    production = _production()
    _require_wal_compaction_api(production)
    runtime_root = tmp_path / "runtime"
    wal, ledger, imported, base = _seed_compaction_runtime(production, data, runtime_root)
    assert wal.retention_counts() == {"zeroEntryCount": 0, "importedEntryCount": 832}
    assert len(wal.imported_event_records()) == 832

    wal_root = runtime_root / "control" / "wal"
    before_authorization = _tree_snapshot(wal_root)
    requested_hashes = [event["eventSha256"] for event in imported[:32]]
    tampered_hashes = list(requested_hashes)
    tampered_hashes[0] = "f" * 64
    _expect_reason(
        production["error"],
        "NEWS_GRASP_ENTRY_WAL_RETENTION_LIMIT",
        lambda: ledger.authorize_wal_compaction(
            wal=wal,
            wal_event_hashes=tampered_hashes,
            batch_size=32,
            observed_at=base + timedelta(seconds=833),
        ),
    )
    assert _tree_snapshot(wal_root) == before_authorization

    authorization = ledger.authorize_wal_compaction(
        wal=wal,
        wal_event_hashes=requested_hashes,
        batch_size=32,
        observed_at=base + timedelta(seconds=833),
    )
    before_invalid_receipts = _tree_snapshot(wal_root)

    forged_hash = deepcopy(authorization)
    forged_hash["batch"][0]["eventSha256"] = "e" * 64
    _expect_reason(
        production["error"],
        "NEWS_GRASP_ENTRY_WAL_RETENTION_LIMIT",
        lambda: wal.compact_imported(forged_hash, observed_at=base + timedelta(seconds=834)),
    )
    assert _tree_snapshot(wal_root) == before_invalid_receipts

    omitted_hash = deepcopy(authorization)
    omitted_hash["batch"] = omitted_hash["batch"][1:]
    _expect_reason(
        production["error"],
        "NEWS_GRASP_ENTRY_WAL_RETENTION_LIMIT",
        lambda: wal.compact_imported(omitted_hash, observed_at=base + timedelta(seconds=834)),
    )
    assert _tree_snapshot(wal_root) == before_invalid_receipts
    assert wal.retention_counts() == {"zeroEntryCount": 0, "importedEntryCount": 832}
    assert not (wal_root / "compaction-head-v1.json").exists()


def test_sec_s1_wal_compaction_preserves_zero_and_emits_chained_receipt(tmp_path: Path) -> None:
    """明示authorize→compact→completeだけが32件を削除し、receipt chainを残す。"""

    data = _load_fixture()
    production = _production()
    _require_wal_compaction_api(production)
    runtime_root = tmp_path / "runtime"
    wal, ledger, imported, base = _seed_compaction_runtime(production, data, runtime_root)
    wal_root = runtime_root / "control" / "wal"
    assert wal.retention_counts() == {"zeroEntryCount": 0, "importedEntryCount": 832}
    assert not (wal_root / "compaction-head-v1.json").exists()

    zero_event = wal.record_initial(
        raw_argv=data["normative"]["rawArgv"]["exact"],
        received_at=base + timedelta(seconds=832),
        writer=_writer(9999),
    )
    zero_path = wal_root / zero_event["invocationId"] / "0001-initial.json"
    zero_bytes = zero_path.read_bytes()
    assert wal.retention_counts() == {"zeroEntryCount": 1, "importedEntryCount": 832}

    requested_hashes = [event["eventSha256"] for event in wal.imported_event_records()[:32]]
    authorization = ledger.authorize_wal_compaction(
        wal=wal,
        wal_event_hashes=requested_hashes,
        batch_size=32,
        observed_at=base + timedelta(seconds=833),
    )
    assert authorization["schemaVersion"] == "WAL_COMPACTION_AUTHORIZATION_V1"
    assert len(authorization["batch"]) <= 32
    assert authorization["batchDigest"] == importlib.import_module(
        "tools.news_grasp_cleanroom_contracts"
    )._entry_canonical_sha256(authorization["batch"])

    receipt = wal.compact_imported(authorization, observed_at=base + timedelta(seconds=834))
    completion = ledger.complete_wal_compaction(receipt, observed_at=base + timedelta(seconds=835))
    assert completion["schemaVersion"] == "WAL_COMPACTION_COMPLETION_V1"
    assert completion["status"] == "completed"
    assert completion["receiptSha256"] == receipt["selfHash"]
    assert completion["ledgerEventSha256"]

    contracts = importlib.import_module("tools.news_grasp_cleanroom_contracts")
    head_path = runtime_root / "control" / "wal" / "compaction-head-v1.json"
    assert head_path.exists()
    assert receipt == json.loads(head_path.read_text(encoding="utf-8"))
    assert head_path.read_bytes() == json.dumps(
        receipt,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert receipt["schemaVersion"] == "WAL_COMPACTION_HEAD_V1"
    assert receipt["previousReceipt"] == "0" * 64
    assert receipt["batch"] == authorization["batch"]
    assert len(receipt["batch"]) <= 32
    assert receipt["batchDigest"] == contracts._entry_canonical_sha256(receipt["batch"])
    assert receipt["ledgerEventSha256"] == authorization["ledgerEventSha256"]
    assert receipt["selfHash"] == contracts._entry_canonical_sha256(
        {key: value for key, value in receipt.items() if key != "selfHash"}
    )
    assert not [path for path in wal_root.rglob("*.tmp")]

    counts = wal.retention_counts()
    assert counts == {"zeroEntryCount": 1, "importedEntryCount": 800}
    assert counts["importedEntryCount"] <= 800
    assert zero_path.exists()
    assert zero_path.read_bytes() == zero_bytes
    for batch_item in receipt["batch"]:
        assert not (wal_root / batch_item["invocationId"]).exists()

    ledger.verify()
    ledger_path = runtime_root / "control" / "control-ledger-v1.sqlite3"
    with closing(sqlite3.connect(ledger_path)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT sequence,generation,event_type,slot_key,payload_json,previous_event_sha256,event_sha256 "
            "FROM events WHERE event_type IN (?,?) ORDER BY sequence",
            ("WAL_COMPACTION_AUTHORIZED", "WAL_COMPACTION_COMPLETED"),
        ).fetchall()
    assert [row["event_type"] for row in rows] == [
        "WAL_COMPACTION_AUTHORIZED",
        "WAL_COMPACTION_COMPLETED",
    ]
    authorized_row, completed_row = rows
    authorized_payload = json.loads(authorized_row["payload_json"])
    completed_payload = json.loads(completed_row["payload_json"])
    assert authorized_row["event_sha256"] == authorization["ledgerEventSha256"]
    assert authorized_payload["batch"] == authorization["batch"]
    assert authorized_payload["batchDigest"] == authorization["batchDigest"]
    assert completed_row["previous_event_sha256"] == authorized_row["event_sha256"]
    assert completed_payload["receiptSha256"] == receipt["selfHash"]
    assert completed_payload["batchDigest"] == receipt["batchDigest"]
    assert completed_payload["ledgerAuthorizationEventSha256"] == authorized_row["event_sha256"]
    assert completed_row["event_sha256"] == completion["ledgerEventSha256"]


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
    fresh_reconcile_boundaries = {
        "after_initial_wal_fsync",
        "after_ledger_begin",
        "after_invocation_import",
        "after_slot_insert",
        "before_ledger_commit",
        "after_ledger_commit",
    }
    terminal_commit_boundaries = {"before_terminal_commit", "after_terminal_commit"}
    assert fresh_reconcile_boundaries | {"after_lease_update"} | terminal_commit_boundaries == {
        row["boundary"] for row in data["dispatchCommitBoundaries"]
    }
    for index, boundary_row in enumerate(data["dispatchCommitBoundaries"], start=1):
        runtime_root = tmp_path / f"boundary-{index}"
        hook_name = boundary_row["boundary"]

        def boundary_hook(hook: str, expected=hook_name) -> None:
            if hook == expected:
                raise OwnedCrash(hook)

        if hook_name in fresh_reconcile_boundaries:
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
            continue

        if hook_name == "after_lease_update":
            initial = _controller(production, runtime_root, manifest_path)
            acquired = initial.reconcile(
                raw_argv=data["normative"]["rawArgv"]["exact"],
                observed_at=_at(6, 1),
                writer=_writer(index),
                lease_seconds=1,
            )
            assert acquired["ownerDisposition"] == "ACQUIRED"
            assert acquired["slotKind"] == "Scheduled"
            del initial

            crashed = _controller(production, runtime_root, manifest_path, boundary_hook=boundary_hook)
            with pytest.raises(OwnedCrash):
                crashed.reconcile(
                    raw_argv=data["normative"]["rawArgv"]["exact"],
                    observed_at=_at(6, 1, 2),
                    writer=_writer(index + 100),
                    lease_seconds=120,
                )
            del crashed

            pre_update = _controller(production, runtime_root, manifest_path).inspect_control_state()
            scheduled_rows = [
                row
                for row in pre_update["slots"]
                if row["slotKind"] == "Scheduled" and row["state"] == "ACTIVE"
            ]
            assert len(scheduled_rows) == 1
            assert scheduled_rows[0]["ownerKey"] == acquired["ownerKey"]
            assert scheduled_rows[0]["fenceToken"] == acquired["fenceToken"]

            reopened = _controller(production, runtime_root, manifest_path).reconcile(
                raw_argv=data["normative"]["rawArgv"]["exact"],
                observed_at=_at(6, 1, 3),
                writer=_writer(index + 100),
                lease_seconds=120,
            )
            assert reopened["ownerDisposition"] == "ACQUIRED"
            assert reopened["ownerKey"] != acquired["ownerKey"]
            assert reopened["fenceToken"] == acquired["fenceToken"] + 1
            _assert_real_sqlite(runtime_root)
            continue

        if hook_name in terminal_commit_boundaries:
            owner = _writer(index)
            initial = _controller(production, runtime_root, manifest_path)
            acquired = initial.reconcile(
                raw_argv=data["normative"]["rawArgv"]["exact"],
                observed_at=_at(6, 1),
                writer=owner,
            )
            assert acquired["slotKind"] == "Scheduled"
            assert acquired["ownerDisposition"] == "ACQUIRED"
            terminal_state = "SUCCEEDED"
            result_hash = "a" * 64
            del initial

            crashed = _controller(production, runtime_root, manifest_path, boundary_hook=boundary_hook)
            with pytest.raises(OwnedCrash):
                crashed.commit_slot(
                    slot_key=acquired["slotKey"],
                    writer=owner,
                    fence_token=acquired["fenceToken"],
                    terminal_state=terminal_state,
                    result_hash=result_hash,
                    observed_at=_at(6, 1, 1),
                )
            del crashed

            reopened = _controller(production, runtime_root, manifest_path)
            if hook_name == "before_terminal_commit":
                retry = reopened.commit_slot(
                    slot_key=acquired["slotKey"],
                    writer=owner,
                    fence_token=acquired["fenceToken"],
                    terminal_state=terminal_state,
                    result_hash=result_hash,
                    observed_at=_at(6, 1, 2),
                )
                assert retry["status"] == "committed"
            else:
                replay = reopened.commit_slot(
                    slot_key=acquired["slotKey"],
                    writer=owner,
                    fence_token=acquired["fenceToken"],
                    terminal_state=terminal_state,
                    result_hash=result_hash,
                    observed_at=_at(6, 1, 2),
                )
                assert replay["status"] == "noop"
                _expect_reason(
                    production["error"],
                    "NEWS_GRASP_ENTRY_TERMINAL_CONFLICT",
                    lambda: reopened.commit_slot(
                        slot_key=acquired["slotKey"],
                        writer=owner,
                        fence_token=acquired["fenceToken"],
                        terminal_state="FAILED",
                        result_hash="b" * 64,
                        observed_at=_at(6, 1, 2),
                    ),
                )
            _assert_real_sqlite(runtime_root)
            continue

        pytest.fail(f"unhandled dispatch/commit boundary: {hook_name}")


def test_edge_writer_crash_adversarial(tmp_path: Path) -> None:
    data = _load_fixture()
    production = _production()
    manifest_path = _write_manifest(tmp_path, data)
    fresh_reconcile_boundaries = {
        "after_initial_wal_fsync",
        "after_ledger_begin",
        "after_invocation_import",
        "after_slot_insert",
        "before_ledger_commit",
        "after_ledger_commit",
    }
    terminal_commit_boundaries = {"before_terminal_commit", "after_terminal_commit"}
    assert fresh_reconcile_boundaries | {"after_lease_update"} | terminal_commit_boundaries == {
        row["boundary"] for row in data["dispatchCommitBoundaries"]
    }
    for index, boundary_row in enumerate(data["dispatchCommitBoundaries"], start=1):
        runtime_root = tmp_path / f"adversarial-{index}"
        hook_name = boundary_row["boundary"]

        def boundary_hook(hook: str, expected=hook_name) -> None:
            if hook == expected:
                raise OwnedCrash(hook)

        if hook_name in fresh_reconcile_boundaries:
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
                lease_seconds=120,
            )
            assert acquired["ownerDisposition"] == "ACQUIRED"
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
            _assert_real_sqlite(runtime_root)
            continue

        if hook_name == "after_lease_update":
            owner = _writer(index)
            initial = _controller(production, runtime_root, manifest_path)
            acquired = initial.reconcile(
                raw_argv=data["normative"]["rawArgv"]["exact"],
                observed_at=_at(6, 1),
                writer=owner,
                lease_seconds=1,
            )
            assert acquired["ownerDisposition"] == "ACQUIRED"
            del initial

            crashed = _controller(production, runtime_root, manifest_path, boundary_hook=boundary_hook)
            with pytest.raises(OwnedCrash):
                crashed.reconcile(
                    raw_argv=data["normative"]["rawArgv"]["exact"],
                    observed_at=_at(6, 1, 2),
                    writer=_writer(index + 100),
                    lease_seconds=120,
                )
            del crashed

            pre_update = _controller(production, runtime_root, manifest_path).inspect_control_state()
            scheduled_rows = [
                row
                for row in pre_update["slots"]
                if row["slotKind"] == "Scheduled" and row["state"] == "ACTIVE"
            ]
            assert len(scheduled_rows) == 1
            assert scheduled_rows[0]["ownerKey"] == acquired["ownerKey"]
            assert scheduled_rows[0]["fenceToken"] == acquired["fenceToken"]

            current = _controller(production, runtime_root, manifest_path)
            takeover = current.reconcile(
                raw_argv=data["normative"]["rawArgv"]["exact"],
                observed_at=_at(6, 1, 3),
                writer=_writer(index + 100),
                lease_seconds=120,
            )
            assert takeover["ownerDisposition"] == "ACQUIRED"
            assert takeover["fenceToken"] == acquired["fenceToken"] + 1
            _expect_reason(
                production["error"],
                "NEWS_GRASP_ENTRY_STALE_FENCE",
                lambda: current.commit_slot(
                    slot_key=acquired["slotKey"],
                    writer=owner,
                    fence_token=acquired["fenceToken"],
                    terminal_state="SUCCEEDED",
                    result_hash="a" * 64,
                    observed_at=_at(6, 1, 4),
                ),
            )
            _assert_real_sqlite(runtime_root)
            continue

        if hook_name in terminal_commit_boundaries:
            owner = _writer(index)
            initial = _controller(production, runtime_root, manifest_path)
            acquired = initial.reconcile(
                raw_argv=data["normative"]["rawArgv"]["exact"],
                observed_at=_at(6, 1),
                writer=owner,
            )
            assert acquired["slotKind"] == "Scheduled"
            assert acquired["ownerDisposition"] == "ACQUIRED"
            del initial

            crashed = _controller(production, runtime_root, manifest_path, boundary_hook=boundary_hook)
            with pytest.raises(OwnedCrash):
                crashed.commit_slot(
                    slot_key=acquired["slotKey"],
                    writer=owner,
                    fence_token=acquired["fenceToken"],
                    terminal_state="SUCCEEDED",
                    result_hash="a" * 64,
                    observed_at=_at(6, 1, 1),
                )
            del crashed

            current = _controller(production, runtime_root, manifest_path)
            if hook_name == "before_terminal_commit":
                _expect_reason(
                    production["error"],
                    "NEWS_GRASP_ENTRY_STALE_FENCE",
                    lambda: current.commit_slot(
                        slot_key=acquired["slotKey"],
                        writer=_writer(index + 100),
                        fence_token=acquired["fenceToken"],
                        terminal_state="SUCCEEDED",
                        result_hash="a" * 64,
                        observed_at=_at(6, 1, 2),
                    ),
                )
                retry = current.commit_slot(
                    slot_key=acquired["slotKey"],
                    writer=owner,
                    fence_token=acquired["fenceToken"],
                    terminal_state="SUCCEEDED",
                    result_hash="a" * 64,
                    observed_at=_at(6, 1, 3),
                )
                assert retry["status"] == "committed"
            else:
                replay = current.commit_slot(
                    slot_key=acquired["slotKey"],
                    writer=owner,
                    fence_token=acquired["fenceToken"],
                    terminal_state="SUCCEEDED",
                    result_hash="a" * 64,
                    observed_at=_at(6, 1, 2),
                )
                assert replay["status"] == "noop"
                _expect_reason(
                    production["error"],
                    "NEWS_GRASP_ENTRY_TERMINAL_CONFLICT",
                    lambda: current.commit_slot(
                        slot_key=acquired["slotKey"],
                        writer=owner,
                        fence_token=acquired["fenceToken"],
                        terminal_state="FAILED",
                        result_hash="b" * 64,
                        observed_at=_at(6, 1, 2),
                    ),
                )
            _assert_real_sqlite(runtime_root)
            continue

        pytest.fail(f"unhandled dispatch/commit boundary: {hook_name}")


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
        if hook_row["hook"] == "after_recovery_committed":
            assert recovery["status"] == "RECOVERY_NOT_REQUIRED"
        else:
            assert recovery["status"] == "recovered"
            assert recovery["newGeneration"] == recovery["oldGeneration"] + 1
        terminal = resumed.recover_ledger(observed_at=_at(6, 2, 2))
        assert terminal["status"] == "RECOVERY_NOT_REQUIRED"
        _assert_real_sqlite(runtime_root)


def test_s1_recovery_journal_self_consistent_forgery_fails_closed(tmp_path: Path) -> None:
    data = _load_fixture()
    production = _production()
    contracts = importlib.import_module("tools.news_grasp_cleanroom_contracts")
    mutations = (
        ("missing_key", lambda journal: journal.pop("updatedAt")),
        ("extra_key", lambda journal: journal.update({"forged": "extra"})),
        ("unknown_phase", lambda journal: journal.update({"phase": "UNKNOWN"})),
        ("bool_generation", lambda journal: journal.update({"oldGeneration": True})),
        ("nonconsecutive_generation", lambda journal: journal.update({"newGeneration": 4})),
        ("invalid_recovery_id", lambda journal: journal.update({"recoveryId": "!"})),
        ("traversal_quarantine", lambda journal: journal.update({"quarantineRelativePath": "../outside/traversal"})),
        ("absolute_quarantine", lambda journal: journal.update({"quarantineRelativePath": ""})),
    )
    for index, (case_name, mutate) in enumerate(mutations, start=1):
        case_root = tmp_path / f"forgery-{index}-{case_name}"
        runtime_root = case_root / "runtime"
        outside_root = case_root / "outside"
        outside_root.mkdir(parents=True)
        (outside_root / "sentinel.txt").write_text("outside sentinel", encoding="utf-8")
        manifest_path = _write_manifest(case_root, data)
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
        first = _controller(production, runtime_root, manifest_path).recover_ledger(observed_at=_at(6, 2))
        assert first["status"] == "recovered"
        recovery_journal = runtime_root / "control" / "recovery-journal-v1.json"
        journal = json.loads(recovery_journal.read_text(encoding="utf-8"))
        mutate(journal)
        if case_name == "absolute_quarantine":
            journal["quarantineRelativePath"] = str(outside_root / "absolute")
        journal["journalSha256"] = contracts._entry_canonical_sha256(
            {key: value for key, value in journal.items() if key != "journalSha256"}
        )
        recovery_journal.write_text(
            json.dumps(journal, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        corrupted = bytearray(ledger_path.read_bytes())
        corrupted[:16] = b"not a sqlite file"
        ledger_path.write_bytes(corrupted)
        managed_before = ledger_path.read_bytes()
        outside_before = _tree_snapshot(outside_root)
        reopened = _controller(production, runtime_root, manifest_path)
        _expect_reason(
            production["error"],
            "NEWS_GRASP_ENTRY_LEDGER_RECOVERY_FAILED",
            lambda: reopened.recover_ledger(observed_at=_at(6, 3)),
        )
        assert ledger_path.exists()
        assert ledger_path.read_bytes() == managed_before
        assert _tree_snapshot(outside_root) == outside_before


def test_s1_busy_timeout_invalid_matrix_is_typed_before_write(tmp_path: Path) -> None:
    data = _load_fixture()
    production = _production()
    dispatch_manifest = _write_manifest(tmp_path, data)
    invalid_values = (
        ("true", True),
        ("zero", 0),
        ("negative", -1),
        ("float", 1.5),
        ("string", "1000"),
        ("none", None),
    )
    for value_name, value in invalid_values:
        route_root = tmp_path / f"busy-dispatch-{value_name}"
        _expect_reason(
            production["error"],
            "NEWS_GRASP_ENTRY_BUSY_TIMEOUT_INVALID",
            lambda value=value, route_root=route_root: production["dispatch"](
                raw_argv=data["normative"]["rawArgv"]["exact"],
                runtime_root=route_root,
                manifest_path=dispatch_manifest,
                observed_at=_at(6, 1),
                writer=_writer(1),
                busy_timeout_ms=value,
            ),
        )
        assert not route_root.exists()

        route_root = tmp_path / f"busy-recover-{value_name}"
        _expect_reason(
            production["error"],
            "NEWS_GRASP_ENTRY_BUSY_TIMEOUT_INVALID",
            lambda value=value, route_root=route_root: production["recover_ledger"](
                runtime_root=route_root,
                manifest_path=tmp_path / f"manifest-recover-{value_name}.json",
                observed_at=_at(6, 2),
                busy_timeout_ms=value,
            ),
        )
        assert not route_root.exists()

        route_root = tmp_path / f"busy-controller-{value_name}"
        _expect_reason(
            production["error"],
            "NEWS_GRASP_ENTRY_BUSY_TIMEOUT_INVALID",
            lambda value=value, route_root=route_root: production["Controller"](
                runtime_root=route_root,
                manifest_path=tmp_path / f"manifest-controller-{value_name}.json",
                busy_timeout_ms=value,
            ),
        )
        assert not route_root.exists()

        route_root = tmp_path / f"busy-ledger-{value_name}"
        _expect_reason(
            production["error"],
            "NEWS_GRASP_ENTRY_BUSY_TIMEOUT_INVALID",
            lambda value=value, route_root=route_root: production["ControlLedger"](
                route_root,
                busy_timeout_ms=value,
            ),
        )
        assert not route_root.exists()

    for value in (1, 60000):
        controller = production["Controller"](
            runtime_root=tmp_path / f"accepted-controller-{value}",
            manifest_path=tmp_path / f"accepted-controller-{value}.json",
            busy_timeout_ms=value,
        )
        ledger = production["ControlLedger"](
            tmp_path / f"accepted-ledger-{value}",
            busy_timeout_ms=value,
        )
        assert controller.busy_timeout_ms == value
        assert ledger.busy_timeout_ms == value


def test_s1_zero_entry_import_cannot_erase_clock_rollback(tmp_path: Path) -> None:
    data = _load_fixture()
    production = _production()
    manifest_path = _write_manifest(tmp_path, data)
    runtime_root = tmp_path / "runtime"
    controller = _controller(production, runtime_root, manifest_path)
    controller.reconcile(
        raw_argv=data["normative"]["rawArgv"]["exact"],
        observed_at=_at(7, 0),
        writer=_writer(1),
    )
    before = controller.inspect_control_state()
    wal = production["DurableWal"](runtime_root)
    prior = wal.record_initial(
        raw_argv=data["normative"]["rawArgv"]["exact"],
        received_at=_at(6, 59),
        writer=_writer(2),
    )
    prior_path = runtime_root / "control" / "wal" / prior["invocationId"]
    _expect_reason(
        production["error"],
        "NEWS_GRASP_ENTRY_CLOCK_ROLLBACK",
        lambda: controller.reconcile(
            raw_argv=data["normative"]["rawArgv"]["exact"],
            observed_at=_at(6, 59),
            writer=_writer(3),
        ),
    )
    after = controller.inspect_control_state()
    assert after["lastObservedAt"] == _at(7, 0).isoformat()
    assert after["invocations"] == before["invocations"]
    assert after["slots"] == before["slots"]
    assert after["eventChainHead"] == before["eventChainHead"]
    assert not (prior_path / "0002-imported.json").exists()


def test_s1_wal_exact_schema_directory_and_imported_chain(tmp_path: Path) -> None:
    data = _load_fixture()
    production = _production()
    contracts = importlib.import_module("tools.news_grasp_cleanroom_contracts")
    cases = (
        ("extra_key", lambda event: event.update({"unexpected": "value"}), False),
        ("missing_key", lambda event: event.pop("writer"), False),
        ("wrong_type", lambda event: event.update({"sequence": "1"}), False),
        ("wrong_event_type", lambda event: event.update({"eventType": "FORGED"}), False),
        ("wrong_phase", lambda event: event.update({"phase": "FORGED"}), False),
        ("wrong_sequence", lambda event: event.update({"sequence": 2}), False),
        ("raw_argv_hash_mismatch", lambda event: event.update({"rawArgv": ["forged"]}), False),
        ("directory_invocation_mismatch", lambda event: event.update({"invocationId": "f" * 32}), False),
        ("imported_parity_mismatch", lambda event: event.update({"rawArgv": ["forged"]}), True),
    )
    for index, (case_name, mutate, imported) in enumerate(cases, start=1):
        runtime_root = tmp_path / f"wal-schema-{index}-{case_name}"
        wal = production["DurableWal"](runtime_root)
        initial = wal.record_initial(
            raw_argv=data["normative"]["rawArgv"]["exact"],
            received_at=_at(6, 1),
            writer=_writer(index),
        )
        if imported:
            wal.mark_imported(initial, imported_at=_at(6, 2))
            target = runtime_root / "control" / "wal" / initial["invocationId"] / "0002-imported.json"
        else:
            target = runtime_root / "control" / "wal" / initial["invocationId"] / "0001-initial.json"
        event = json.loads(target.read_text(encoding="utf-8"))
        mutate(event)
        event["eventSha256"] = contracts._entry_canonical_sha256(
            {key: value for key, value in event.items() if key != "eventSha256"}
        )
        target.write_text(
            json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        _expect_reason(
            production["error"],
            "NEWS_GRASP_ENTRY_LEDGER_CORRUPT",
            lambda wal=wal: wal.verify(),
        )


def test_s1_recovery_transition_gaps_resume_same_identity(tmp_path: Path) -> None:
    data = _load_fixture()
    production = _production()
    for index, target_phase in enumerate(("SEALED", "LEDGER_CREATED"), start=1):
        case_root = tmp_path / f"transition-{index}-{target_phase.lower()}"
        case_root.mkdir(parents=True)
        runtime_root = case_root / "runtime"
        manifest_path = _write_manifest(case_root, data)
        initial = _controller(production, runtime_root, manifest_path)
        initial.reconcile(
            raw_argv=data["normative"]["rawArgv"]["exact"],
            observed_at=_at(6, 1),
            writer=_writer(index),
        )
        del initial
        generation_path = runtime_root / "control" / "generation-seal-v1.json"
        old_seal = json.loads(generation_path.read_text(encoding="utf-8"))
        ledger_path = runtime_root / "control" / "control-ledger-v1.sqlite3"
        corrupted = bytearray(ledger_path.read_bytes())
        corrupted[:16] = b"not a sqlite file"
        ledger_path.write_bytes(corrupted)
        failure_state = {"failed": False}

        def fail_at_phase(source: str | os.PathLike[str], destination: str | os.PathLike[str], expected=target_phase) -> None:
            if Path(destination).name == "recovery-journal-v1.json" and not failure_state["failed"]:
                payload = json.loads(Path(source).read_text(encoding="utf-8"))
                if payload.get("phase") == expected:
                    failure_state["failed"] = True
                    raise OSError(f"test-owned replace failure at {expected}")
            os.replace(source, destination)

        operations = production["DurabilityOps"](replace=fail_at_phase)
        _expect_reason(
            production["error"],
            "NEWS_GRASP_ENTRY_LEDGER_RECOVERY_FAILED",
            lambda operations=operations: production["recover_ledger"](
                runtime_root=runtime_root,
                manifest_path=manifest_path,
                observed_at=_at(6, 2),
                durability_ops=operations,
            ),
        )
        assert failure_state["failed"]
        recovery_journal = runtime_root / "control" / "recovery-journal-v1.json"
        failed_journal = json.loads(recovery_journal.read_text(encoding="utf-8"))
        recovery_id = failed_journal["recoveryId"]
        new_generation = failed_journal["newGeneration"]
        resumed = _controller(production, runtime_root, manifest_path).recover_ledger(observed_at=_at(6, 3))
        assert resumed["status"] == "recovered"
        assert resumed["recoveryId"] == recovery_id
        assert resumed["newGeneration"] == new_generation == 2
        seal = json.loads(generation_path.read_text(encoding="utf-8"))
        assert seal["generation"] == 2
        assert seal["previousSealSha256"] == old_seal["sealSha256"]
        with closing(sqlite3.connect(ledger_path)) as connection:
            genesis_count = connection.execute(
                "SELECT COUNT(*) FROM events WHERE event_type='LEDGER_RECOVERED'"
            ).fetchone()[0]
        assert genesis_count == 1
        committed = json.loads(recovery_journal.read_text(encoding="utf-8"))
        assert committed["phase"] == "COMMITTED"
        assert committed["recoveryId"] == recovery_id
        _controller(production, runtime_root, manifest_path).inspect_control_state()
        _assert_real_sqlite(runtime_root)


def test_s1_wal_managed_root_containment(tmp_path: Path) -> None:
    data = _load_fixture()
    production = _production()
    contracts = importlib.import_module("tools.news_grasp_cleanroom_contracts")

    direct_root = tmp_path / "containment-direct"
    direct_outside = tmp_path / "containment-direct-outside"
    direct_outside.mkdir()
    (direct_outside / "sentinel.txt").write_text("outside sentinel", encoding="utf-8")
    direct_wal = production["DurableWal"](direct_root)
    direct_initial = direct_wal.record_initial(
        raw_argv=data["normative"]["rawArgv"]["exact"],
        received_at=_at(6, 1),
        writer=_writer(1),
    )
    forged = dict(direct_initial)
    forged["invocationId"] = "../../../containment-direct-outside"
    forged["eventSha256"] = contracts._entry_canonical_sha256(
        {key: value for key, value in forged.items() if key != "eventSha256"}
    )
    direct_before = _tree_snapshot(direct_outside)
    _expect_reason(
        production["error"],
        "NEWS_GRASP_ENTRY_LEDGER_CORRUPT",
        lambda: direct_wal.mark_imported(forged, imported_at=_at(6, 2)),
    )
    assert _tree_snapshot(direct_outside) == direct_before

    forged_root = tmp_path / "containment-forged"
    forged_outside = tmp_path / "containment-forged-outside"
    forged_outside.mkdir()
    (forged_outside / "sentinel.txt").write_text("outside sentinel", encoding="utf-8")
    forged_wal = production["DurableWal"](forged_root)
    forged_initial = forged_wal.record_initial(
        raw_argv=data["normative"]["rawArgv"]["exact"],
        received_at=_at(6, 1),
        writer=_writer(2),
    )
    forged_event = dict(forged_initial)
    forged_event["invocationId"] = "../../../containment-forged-outside"
    forged_event["eventSha256"] = contracts._entry_canonical_sha256(
        {key: value for key, value in forged_event.items() if key != "eventSha256"}
    )
    forged_directory = forged_root / "control" / "wal" / "forged-directory"
    forged_directory.mkdir(parents=True)
    (forged_directory / "0001-initial.json").write_text(
        json.dumps(forged_event, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    forged_before = _tree_snapshot(forged_outside)
    _expect_reason(
        production["error"],
        "NEWS_GRASP_ENTRY_LEDGER_CORRUPT",
        lambda: forged_wal.iter_zero_entries(),
    )
    assert _tree_snapshot(forged_outside) == forged_before

    symlink_root = tmp_path / "containment-symlink"
    symlink_outside = tmp_path / "containment-symlink-outside"
    symlink_outside.mkdir()
    (symlink_outside / "sentinel.txt").write_text("outside sentinel", encoding="utf-8")
    (symlink_root / "control").mkdir(parents=True)
    wal_link = symlink_root / "control" / "wal"
    try:
        wal_link.symlink_to(symlink_outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        symlink_created = False
    else:
        symlink_created = True
    if symlink_created:
        manifest_path = _write_manifest(tmp_path, data)
        symlink_before = _tree_snapshot(symlink_outside)
        _expect_reason(
            production["error"],
            "NEWS_GRASP_ENTRY_LEDGER_CORRUPT",
            lambda: production["dispatch"](
                raw_argv=data["normative"]["rawArgv"]["exact"],
                runtime_root=symlink_root,
                manifest_path=manifest_path,
                observed_at=_at(6, 1),
                writer=_writer(3),
            ),
        )
        assert _tree_snapshot(symlink_outside) == symlink_before


def test_s1_materialized_state_drift_is_corrupt(tmp_path: Path) -> None:
    data = _load_fixture()
    production = _production()
    mutations = (
        ("slot_owner", "UPDATE slots SET owner_key=?", ("forged-owner",)),
        ("slot_fence", "UPDATE slots SET fence_token=?", (2,)),
        ("invocation_status", "UPDATE invocations SET status=?", ("RECOVERED_ZERO_ENTRY",)),
        ("last_observed_at", "UPDATE metadata SET value=? WHERE key='lastObservedAt'", (_at(6, 2).isoformat(),)),
    )
    for index, (case_name, statement, parameters) in enumerate(mutations, start=1):
        runtime_root = tmp_path / f"materialized-{index}-{case_name}"
        manifest_root = tmp_path / f"materialized-manifest-{index}"
        manifest_root.mkdir(parents=True)
        manifest_path = _write_manifest(manifest_root, data)
        controller = _controller(production, runtime_root, manifest_path)
        controller.reconcile(
            raw_argv=data["normative"]["rawArgv"]["exact"],
            observed_at=_at(6, 1),
            writer=_writer(index),
        )
        del controller
        ledger_path = runtime_root / "control" / "control-ledger-v1.sqlite3"
        with closing(sqlite3.connect(ledger_path)) as connection:
            events_before = connection.execute(
                "SELECT sequence,generation,event_type,slot_key,payload_json,previous_event_sha256,event_sha256 FROM events ORDER BY sequence"
            ).fetchall()
            connection.execute(statement, parameters)
            connection.commit()
            events_after = connection.execute(
                "SELECT sequence,generation,event_type,slot_key,payload_json,previous_event_sha256,event_sha256 FROM events ORDER BY sequence"
            ).fetchall()
        assert events_after == events_before
        reopened = _controller(production, runtime_root, manifest_path)
        _expect_reason(
            production["error"],
            "NEWS_GRASP_ENTRY_LEDGER_CORRUPT",
            lambda: reopened.inspect_control_state(),
        )


def test_s1_public_invalid_type_matrix_is_total(tmp_path: Path) -> None:
    data = _load_fixture()
    production = _production()
    manifest_path = _write_manifest(tmp_path, data)
    for index, raw_argv in enumerate((None, 42), start=1):
        runtime_root = tmp_path / f"invalid-raw-argv-{index}"
        _expect_reason(
            production["error"],
            "NEWS_GRASP_ENTRY_ARGS_INVALID",
            lambda raw_argv=raw_argv, runtime_root=runtime_root: production["dispatch"](
                raw_argv=raw_argv,
                runtime_root=runtime_root,
                manifest_path=manifest_path,
                observed_at=_at(6, 1),
                writer=_writer(index),
            ),
        )
        assert not runtime_root.exists()

    for index, observed_at in enumerate((None, object()), start=1):
        runtime_root = tmp_path / f"invalid-observed-at-{index}"
        _expect_reason(
            production["error"],
            "NEWS_GRASP_ENTRY_TIME_INVALID",
            lambda observed_at=observed_at, runtime_root=runtime_root: production["dispatch"](
                raw_argv=data["normative"]["rawArgv"]["exact"],
                runtime_root=runtime_root,
                manifest_path=manifest_path,
                observed_at=observed_at,
                writer=_writer(index + 10),
            ),
        )
        assert not runtime_root.exists()

    _expect_reason(
        production["error"],
        "NEWS_GRASP_ENTRY_MANIFEST_INVALID",
        lambda: production["reconcile_slot"](
            manifest=_manifest(data),
            observed_at=_at(6, 1),
            last_observed_at=None,
            scheduled_state=[],
        ),
    )
    commit_root = tmp_path / "invalid-commit-slot"
    _expect_reason(
        production["error"],
        "NEWS_GRASP_ENTRY_COMMIT_INVALID",
        lambda: production["commit_slot"](
            runtime_root=commit_root,
            manifest_path=manifest_path,
            slot_key=[],
            writer=_writer(20),
            fence_token=1,
            terminal_state="SUCCEEDED",
            result_hash="a" * 64,
            observed_at=_at(6, 1),
        ),
    )
    assert not commit_root.exists()

    imported_root = tmp_path / "invalid-imported-at"
    wal = production["DurableWal"](imported_root)
    initial = wal.record_initial(
        raw_argv=data["normative"]["rawArgv"]["exact"],
        received_at=_at(6, 1),
        writer=_writer(21),
    )
    initial_path = imported_root / "control" / "wal" / initial["invocationId"] / "0001-initial.json"
    initial_before = initial_path.read_bytes()
    _expect_reason(
        production["error"],
        "NEWS_GRASP_ENTRY_TIME_INVALID",
        lambda: wal.mark_imported(initial, imported_at=None),
    )
    assert initial_path.read_bytes() == initial_before
    assert not initial_path.with_name("0002-imported.json").exists()


def test_s1_reconcile_revalidates_scheduled_state_inside_transaction(tmp_path: Path) -> None:
    data = _load_fixture()
    production = _production()
    manifest_path = _write_manifest(tmp_path, data)
    runtime_root = tmp_path / "runtime"
    ledger_module = importlib.import_module("tools.news_grasp_cleanroom_ledger")
    original_import_zero_entries = ledger_module.ControlLedger.import_zero_entries
    trigger_lock = Lock()
    triggered = False
    competing_result: list[dict[str, Any]] = []

    def synchronized_import_zero_entries(
        ledger: Any,
        zero_entries: tuple[dict[str, Any], ...],
        *,
        observed_at: datetime,
    ) -> None:
        nonlocal triggered
        with trigger_lock:
            should_trigger = not triggered
            triggered = True
        if should_trigger:
            competing = _controller(production, runtime_root, manifest_path)
            competing_result.append(
                competing.reconcile(
                    raw_argv=data["normative"]["rawArgv"]["exact"],
                    observed_at=_at(6, 39),
                    writer=_writer(2),
                )
            )
            del competing
        original_import_zero_entries(ledger, zero_entries, observed_at=observed_at)

    ledger_module.ControlLedger.import_zero_entries = synchronized_import_zero_entries
    try:
        outer = _controller(production, runtime_root, manifest_path)
        result = outer.reconcile(
            raw_argv=data["normative"]["rawArgv"]["exact"],
            observed_at=_at(6, 40),
            writer=_writer(1),
        )
    finally:
        ledger_module.ControlLedger.import_zero_entries = original_import_zero_entries

    assert len(competing_result) == 1
    assert competing_result[0]["decision"] == "ENSURE_SCHEDULED"
    assert result["decision"] == "ENSURE_AUDIT_OBSERVING_SCHEDULED"
    assert result["scheduledState"] == "ACTIVE"
    assert result["slotKind"] == "Audit"
    state = outer.inspect_control_state()
    scheduled = [
        row
        for row in state["slots"]
        if row["slotKind"] == "Scheduled" and row["state"] == "ACTIVE"
    ]
    missed = [
        row
        for row in state["slots"]
        if row["slotKind"] == "Scheduled" and row["terminalState"] == "MISSED_SCHEDULED"
    ]
    assert len(scheduled) == 1
    assert scheduled[0]["terminalState"] is None
    assert not missed
    _assert_real_sqlite(runtime_root)


def test_s1_recovery_after_committed_journal_repairs_new_corruption(tmp_path: Path) -> None:
    data = _load_fixture()
    production = _production()
    manifest_path = _write_manifest(tmp_path, data)
    runtime_root = tmp_path / "runtime"
    initial = _controller(production, runtime_root, manifest_path)
    initial.reconcile(
        raw_argv=data["normative"]["rawArgv"]["exact"],
        observed_at=_at(6, 1),
        writer=_writer(1),
    )
    del initial

    ledger_path = runtime_root / "control" / "control-ledger-v1.sqlite3"
    corrupted = bytearray(ledger_path.read_bytes())
    corrupted[:16] = b"not a sqlite file"
    ledger_path.write_bytes(corrupted)

    first_controller = _controller(production, runtime_root, manifest_path)
    first = first_controller.recover_ledger(observed_at=_at(6, 2))
    assert first["status"] == "recovered"
    assert first["oldGeneration"] == 1
    assert first["newGeneration"] == 2
    del first_controller
    recovery_journal = runtime_root / "control" / "recovery-journal-v1.json"
    prior_journal = json.loads(recovery_journal.read_text(encoding="utf-8"))
    assert prior_journal["phase"] == "COMMITTED"

    reopened = _controller(production, runtime_root, manifest_path)
    reopened.inspect_control_state()
    del reopened
    corrupted = bytearray(ledger_path.read_bytes())
    corrupted[:16] = b"not a sqlite file"
    ledger_path.write_bytes(corrupted)

    second_controller = _controller(production, runtime_root, manifest_path)
    second = second_controller.recover_ledger(observed_at=_at(6, 3))
    assert second["status"] == "recovered"
    assert second["recoveryId"] != first["recoveryId"]
    assert second["oldGeneration"] == 2
    assert second["newGeneration"] == 3
    assert second["quarantinePath"] != first["quarantinePath"]
    state = second_controller.inspect_control_state()
    assert state["generation"] == 3
    assert state["integrityStatus"] == "green"
    history_path = runtime_root / "control" / "recovery-history" / f"{first['recoveryId']}.json"
    assert history_path.exists()
    assert json.loads(history_path.read_text(encoding="utf-8")) == prior_journal
    _assert_real_sqlite(runtime_root)


def test_s1_recover_wrapper_exposes_and_forwards_busy_timeout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    production = _production()
    dispatch_module = importlib.import_module("tools.news_grasp_cleanroom_dispatch")
    parameter = inspect.signature(production["recover_ledger"]).parameters.get("busy_timeout_ms")
    assert parameter is not None
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default == 1000
    captured: dict[str, Any] = {}

    class CapturingController:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        def recover_ledger(self, *, observed_at: datetime) -> dict[str, Any]:
            captured["observed_at"] = observed_at
            return {"status": "captured"}

    monkeypatch.setattr(dispatch_module, "Controller", CapturingController)
    observed = _at(6, 2)
    result = dispatch_module.recover_ledger(
        runtime_root=tmp_path / "runtime",
        manifest_path=tmp_path / "manifest.json",
        observed_at=observed,
        busy_timeout_ms=17,
    )
    assert result == {"status": "captured"}
    assert captured["busy_timeout_ms"] == 17
    assert captured["observed_at"] is observed


def test_s1_atomic_publish_never_reopens_public_final_for_write(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    data = _load_fixture()
    production = _production()
    manifest_path = _write_manifest(tmp_path, data)
    runtime_root = tmp_path / "runtime"
    forbidden_reopens: list[Path] = []
    original_open = Path.open

    def guarded_open(path: Path, mode: str = "r", *args: Any, **kwargs: Any):
        candidate = Path(path)
        if (
            mode == "r+b"
            and candidate.exists()
            and candidate.suffix == ".json"
            and not candidate.name.startswith(".")
            and candidate.is_relative_to(runtime_root / "control")
        ):
            forbidden_reopens.append(candidate)
            raise PermissionError(f"simulated sharing denial for {candidate}")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    controller = _controller(production, runtime_root, manifest_path)
    controller.reconcile(
        raw_argv=data["normative"]["rawArgv"]["exact"],
        observed_at=_at(6, 1),
        writer=_writer(1),
    )
    assert production["DurableWal"](runtime_root).verify()["status"] == "verified"
    state = controller.inspect_control_state()
    assert state["integrityStatus"] == "green"
    assert forbidden_reopens == []


def test_s1_generation_seal_exact_schema_fails_closed(tmp_path: Path) -> None:
    data = _load_fixture()
    production = _production()
    contracts = importlib.import_module("tools.news_grasp_cleanroom_contracts")
    mutations = (
        ("extra_key", lambda seal: seal.update({"unexpected": "value"})),
        ("missing_ledger_relative_path", lambda seal: seal.pop("ledgerRelativePath")),
        ("wrong_ledger_relative_path", lambda seal: seal.update({"ledgerRelativePath": "control/other.sqlite3"})),
        ("bool_generation", lambda seal: seal.update({"generation": True})),
        ("non_hex_previous", lambda seal: seal.update({"previousSealSha256": "z" * 64})),
        ("generation_one_nonzero_previous", lambda seal: seal.update({"previousSealSha256": "1" * 64})),
        ("invalid_created_at", lambda seal: seal.update({"createdAt": "not-an-iso-timestamp"})),
    )
    for index, (case_name, mutate) in enumerate(mutations, start=1):
        case_root = tmp_path / f"generation-seal-{index}-{case_name}"
        case_root.mkdir(parents=True)
        manifest_path = _write_manifest(case_root, data)
        runtime_root = case_root / "runtime"
        controller = _controller(production, runtime_root, manifest_path)
        controller.reconcile(
            raw_argv=data["normative"]["rawArgv"]["exact"],
            observed_at=_at(6, 1),
            writer=_writer(index),
        )
        generation_path = runtime_root / "control" / "generation-seal-v1.json"
        seal = json.loads(generation_path.read_text(encoding="utf-8"))
        mutate(seal)
        seal["sealSha256"] = contracts._entry_canonical_sha256(
            {key: value for key, value in seal.items() if key != "sealSha256"}
        )
        generation_path.write_text(
            json.dumps(seal, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        _expect_reason(
            production["error"],
            "NEWS_GRASP_ENTRY_LEDGER_CORRUPT",
            lambda runtime_root=runtime_root: production["ControlLedger"](runtime_root).verify(),
        )


def test_s1_sqlite_exact_schema_fails_closed(tmp_path: Path) -> None:
    data = _load_fixture()
    production = _production()
    mutations = (
        ("unexpected_table", lambda connection: connection.execute("CREATE TABLE unexpected_user_table (value TEXT)")),
        (
            "metadata_without_constraints",
            lambda connection: (
                connection.execute("ALTER TABLE metadata RENAME TO metadata_original"),
                connection.execute("CREATE TABLE metadata (key TEXT, value TEXT)"),
                connection.executemany(
                    "INSERT INTO metadata(key,value) VALUES(?,?)",
                    connection.execute("SELECT key,value FROM metadata_original ORDER BY key").fetchall(),
                ),
                connection.execute("DROP TABLE metadata_original"),
            ),
        ),
        ("invocations_unexpected_column", lambda connection: connection.execute("ALTER TABLE invocations ADD COLUMN forged TEXT")),
        ("slots_unexpected_index", lambda connection: connection.execute("CREATE INDEX forged_slots_index ON slots(issue_date)")),
        ("events_unexpected_column", lambda connection: connection.execute("ALTER TABLE events ADD COLUMN forged TEXT")),
    )

    def materialized_snapshot(connection: sqlite3.Connection) -> dict[str, list[tuple[Any, ...]]]:
        return {
            "metadata": connection.execute("SELECT key,value FROM metadata ORDER BY key").fetchall(),
            "invocations": connection.execute(
                "SELECT invocation_id,received_at,raw_argv_sha256,writer_key,wal_event_sha256,imported_at,status FROM invocations ORDER BY invocation_id"
            ).fetchall(),
            "slots": connection.execute(
                "SELECT schedule_id,issue_date,slot_kind,generation,state,owner_key,fence_token,lease_expires_at,terminal_state,result_hash,updated_at FROM slots ORDER BY schedule_id,issue_date,slot_kind"
            ).fetchall(),
            "events": connection.execute(
                "SELECT sequence,generation,event_type,slot_key,payload_json,previous_event_sha256,event_sha256 FROM events ORDER BY sequence"
            ).fetchall(),
        }

    for index, (case_name, mutate) in enumerate(mutations, start=1):
        case_root = tmp_path / f"sqlite-schema-{index}-{case_name}"
        case_root.mkdir(parents=True)
        manifest_path = _write_manifest(case_root, data)
        runtime_root = case_root / "runtime"
        _controller(production, runtime_root, manifest_path).reconcile(
            raw_argv=data["normative"]["rawArgv"]["exact"],
            observed_at=_at(6, 1),
            writer=_writer(index),
        )
        ledger_path = runtime_root / "control" / "control-ledger-v1.sqlite3"
        with closing(sqlite3.connect(ledger_path)) as connection:
            before = materialized_snapshot(connection)
            mutate(connection)
            connection.commit()
            assert materialized_snapshot(connection) == before
        _expect_reason(
            production["error"],
            "NEWS_GRASP_ENTRY_LEDGER_CORRUPT",
            lambda runtime_root=runtime_root: production["ControlLedger"](runtime_root).verify(),
        )


def test_s1_imported_marker_concurrent_publish_is_immutable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    data = _load_fixture()
    production = _production()
    wal_module = importlib.import_module("tools.news_grasp_cleanroom_wal")
    runtime_root = tmp_path / "runtime"
    initial_wal = production["DurableWal"](runtime_root)
    initial = initial_wal.record_initial(
        raw_argv=data["normative"]["rawArgv"]["exact"],
        received_at=_at(6, 1),
        writer=_writer(1),
    )
    original_write_json = wal_module._write_json
    publish_barrier = Barrier(2)
    replace_barrier = Barrier(2)
    replace_lock = Lock()

    def controlled_replace(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
        if Path(destination).name == "0002-imported.json":
            replace_barrier.wait(timeout=10)
            with replace_lock:
                os.replace(source, destination)
            return
        os.replace(source, destination)

    def barrier_write(path: Path, payload: dict[str, Any], operations: Any, reason: str) -> None:
        if Path(path).name == "0002-imported.json":
            publish_barrier.wait(timeout=10)
        original_write_json(path, payload, operations, reason)

    operations = production["DurabilityOps"](replace=controlled_replace)
    wal = production["DurableWal"](runtime_root, durability_ops=operations)
    monkeypatch.setattr(wal_module, "_write_json", barrier_write)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (
            executor.submit(wal.mark_imported, initial, imported_at=_at(6, 2)),
            executor.submit(wal.mark_imported, initial, imported_at=_at(6, 2, 1)),
        )
        returned = [future.result(timeout=10) for future in futures]

    imported_path = runtime_root / "control" / "wal" / initial["invocationId"] / "0002-imported.json"
    final_bytes = imported_path.read_bytes()
    final_event = json.loads(final_bytes.decode("utf-8"))
    assert len({event["eventSha256"] for event in returned}) == 1
    for event in returned:
        assert json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") == final_bytes
    assert wal.verify()["status"] == "verified"
    assert not [path for path in (runtime_root / "control" / "wal").rglob("*") if path.is_file() and path.suffix == ".tmp"]
    assert final_event["eventSha256"] == returned[0]["eventSha256"]


def test_s1_concurrent_recovery_has_single_authority(tmp_path: Path) -> None:
    data = _load_fixture()
    production = _production()
    manifest_path = _write_manifest(tmp_path, data)
    runtime_root = tmp_path / "runtime"
    _controller(production, runtime_root, manifest_path).reconcile(
        raw_argv=data["normative"]["rawArgv"]["exact"],
        observed_at=_at(6, 1),
        writer=_writer(1),
    )
    ledger_path = runtime_root / "control" / "control-ledger-v1.sqlite3"
    corrupted = bytearray(ledger_path.read_bytes())
    corrupted[:16] = b"not a sqlite file"
    ledger_path.write_bytes(corrupted)

    prepared = Event()
    release = Event()
    second_attempted = Event()

    def first_hook(name: str) -> None:
        if name == "after_recovery_journal_prepared":
            prepared.set()
            assert release.wait(timeout=10)

    def first_worker() -> dict[str, Any]:
        return _controller(production, runtime_root, manifest_path, boundary_hook=first_hook).recover_ledger(observed_at=_at(6, 2))

    def second_worker() -> dict[str, Any]:
        second_attempted.set()
        return _controller(production, runtime_root, manifest_path).recover_ledger(observed_at=_at(6, 2, 1))

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(first_worker)
        assert prepared.wait(timeout=10)
        second_future = executor.submit(second_worker)
        assert second_attempted.wait(timeout=10)
        release.set()
        outcomes: list[tuple[str, Any]] = []
        for future in (first_future, second_future):
            try:
                outcomes.append(("ok", future.result(timeout=10)))
            except Exception as exc:
                outcomes.append(("error", exc))

    assert all(kind == "ok" for kind, _ in outcomes)
    results = [value for kind, value in outcomes if kind == "ok"]
    assert sorted(result["status"] for result in results) == ["RECOVERY_NOT_REQUIRED", "recovered"]
    assert len({result["recoveryId"] for result in results}) == 1
    assert len({result["newGeneration"] for result in results}) == 1
    recovery_journal = json.loads((runtime_root / "control" / "recovery-journal-v1.json").read_text(encoding="utf-8"))
    assert recovery_journal["phase"] == "COMMITTED"
    assert recovery_journal["recoveryId"] == results[0]["recoveryId"]
    generation = json.loads((runtime_root / "control" / "generation-seal-v1.json").read_text(encoding="utf-8"))
    assert generation["generation"] == 2
    with closing(sqlite3.connect(ledger_path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM events WHERE event_type='LEDGER_RECOVERED'").fetchone()[0] == 1
    assert _controller(production, runtime_root, manifest_path).inspect_control_state()["integrityStatus"] == "green"
    _assert_real_sqlite(runtime_root)


def test_s1_imported_marker_parent_flush_failure_is_not_race_success(tmp_path: Path) -> None:
    data = _load_fixture()
    production = _production()
    contracts = importlib.import_module("tools.news_grasp_cleanroom_contracts")
    runtime_root = tmp_path / "runtime"
    initial_wal = production["DurableWal"](runtime_root)
    initial = initial_wal.record_initial(
        raw_argv=data["normative"]["rawArgv"]["exact"],
        received_at=_at(6, 1),
        writer=_writer(1),
    )

    def fail_flush_parent(path: Path) -> None:
        raise OSError(f"test-owned parent flush failure: {path}")

    failing_operations = production["DurabilityOps"](flush_parent=fail_flush_parent)
    failing_wal = production["DurableWal"](runtime_root, durability_ops=failing_operations)
    imported_path = runtime_root / "control" / "wal" / initial["invocationId"] / "0002-imported.json"
    _expect_reason(
        production["error"],
        "NEWS_GRASP_ENTRY_WAL_FINALIZE_FAILED",
        lambda: failing_wal.mark_imported(initial, imported_at=_at(6, 2)),
    )

    assert imported_path.exists()
    final_event = json.loads(imported_path.read_text(encoding="utf-8"))
    assert set(final_event) == {
        "schemaVersion",
        "eventType",
        "phase",
        "invocationId",
        "sequence",
        "receivedAt",
        "rawArgv",
        "rawArgvSha256",
        "writer",
        "previousEventSha256",
        "eventSha256",
    }
    assert final_event["schemaVersion"] == "WAL_EVENT_V1"
    assert final_event["eventType"] == "INVOCATION_IMPORTED"
    assert final_event["phase"] == "LEDGER_IMPORTED"
    assert final_event["invocationId"] == initial["invocationId"]
    assert final_event["sequence"] == 2
    assert final_event["rawArgv"] == initial["rawArgv"]
    assert final_event["rawArgvSha256"] == initial["rawArgvSha256"]
    assert final_event["writer"] == initial["writer"]
    assert final_event["previousEventSha256"] == initial["eventSha256"]
    assert final_event["eventSha256"] == contracts._entry_canonical_sha256(
        {key: value for key, value in final_event.items() if key != "eventSha256"}
    )
    assert not [path for path in imported_path.parent.iterdir() if path.is_file() and path.suffix == ".tmp"]

    normal_wal = production["DurableWal"](runtime_root)
    retry = normal_wal.mark_imported(initial, imported_at=_at(6, 3))
    assert retry == final_event
    assert normal_wal.verify()["status"] == "verified"
