"""S4 clean-room recovery plane のsealed Expected Red suite。"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import importlib
import json
from pathlib import Path
import shutil
import sqlite3
from typing import Any
from zoneinfo import ZoneInfo

import pytest


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "news_grasp_cleanroom_s4_cases.json"
S1_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "news_grasp_cleanroom_s1_cases.json"
TOKYO = ZoneInfo("Asia/Tokyo")
RECOVERY_RECORD_COLUMNS = (
    "issue_date",
    "recovery_id",
    "binding_json",
    "binding_sha256",
    "authority_sha256",
    "budget_sha256",
    "attempts_used",
    "phase",
    "execution_receipt_json",
    "execution_receipt_sha256",
    "public_receipt_json",
    "public_receipt_sha256",
    "history_json",
    "history_sha256",
    "result_json",
    "result_sha256",
    "record_sha256",
)


def _sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _at(hour: int, minute: int) -> datetime:
    return datetime(2026, 8, 21, hour, minute, tzinfo=TOKYO)


def _cases() -> dict[str, Any]:
    value = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert value["schemaVersion"] == "NEWS_GRASP_CLEANROOM_S4_CASES_V1"
    assert value["packetId"] == "NG-CLEANROOM-S4-RED-V1"
    assert value["lineages"] == ["Scheduled", "Recovery", "Public", "Readiness"]
    return value


def _s1_runtime(tmp_path: Path, index: int) -> Path:
    root = tmp_path / f"日本語-回復面-{index}"
    root.mkdir(parents=True)
    s1 = json.loads(S1_FIXTURE_PATH.read_text(encoding="utf-8"))
    manifest = {
        "schemaVersion": s1["normative"]["manifest"]["schemaVersion"],
        "scheduleId": s1["normative"]["manifest"]["scheduleId"],
        "tasks": [s1["normative"]["manifest"]["task"]],
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    controller = importlib.import_module("tools.news_grasp_cleanroom_controller").Controller(
        runtime_root=root,
        manifest_path=manifest_path,
    )
    writer = {
        "writerId": f"s4-test-{index}",
        "bootId": "s4-test-boot",
        "pid": 7000 + index,
        "processStartToken": f"s4-process-{index}",
    }
    controller.reconcile(
        raw_argv=s1["normative"]["rawArgv"]["exact"],
        observed_at=_at(6, 1),
        writer=writer,
    )
    audit_writer = {**writer, "writerId": f"s4-audit-{index}", "pid": 8000 + index}
    controller.reconcile(
        raw_argv=s1["normative"]["rawArgv"]["exact"],
        observed_at=_at(6, 41),
        writer=audit_writer,
    )
    ledger = importlib.import_module("tools.news_grasp_cleanroom_ledger").ControlLedger(root)
    ledger.commit_slot(
        slot_key=f'{s1["normative"]["manifest"]["scheduleId"]}/2026-08-21/Scheduled',
        writer=writer,
        fence_token=1,
        terminal_state="FAILED",
        result_hash=_sha({"issueDate": "2026-08-21", "slot": "Scheduled", "state": "FAILED"}),
        observed_at=_at(6, 42),
    )
    return root


def _slot(root: Path, kind: str) -> dict[str, Any]:
    with sqlite3.connect(root / "control" / "control-ledger-v1.sqlite3") as connection:
        row = connection.execute(
            "SELECT schedule_id,issue_date,slot_kind,generation,state,owner_key,fence_token,terminal_state,result_hash "
            "FROM slots WHERE slot_kind=?",
            (kind,),
        ).fetchone()
    if row is None:
        raise AssertionError(f"S1 {kind} slot is missing")
    return {
        "scheduleId": row[0],
        "issueDate": row[1],
        "slotKind": row[2],
        "generation": row[3],
        "state": row[4],
        "ownerKey": row[5],
        "fenceToken": row[6],
        "terminalState": row[7],
        "resultHash": row[8],
    }


def _parent(cases: dict[str, Any], scheduled: dict[str, Any]) -> dict[str, Any]:
    value = {
        "schemaVersion": "RECOVERY_PARENT_V1",
        "lineage": "Scheduled",
        "issueDate": cases["issueDate"],
        "scheduleId": cases["scheduleId"],
        "slotKey": cases["scheduledSlotKey"],
        "terminalState": "FAILED",
        "terminalHash": scheduled["resultHash"] or "s" * 64,
        "generation": scheduled["generation"],
    }
    value["parentSha256"] = _sha(value)
    return value


def _authority(
    cases: dict[str, Any],
    scheduled: dict[str, Any],
    audit: dict[str, Any],
    *,
    authority_id: str | None = None,
) -> dict[str, Any]:
    value = {
        "schemaVersion": "RECOVERY_AUTHORITY_V1",
        "authorityId": authority_id or cases["authority"]["authorityId"],
        "issueDate": cases["issueDate"],
        "scheduledParentTerminalHash": scheduled["resultHash"] or "s" * 64,
        "scheduledGeneration": scheduled["generation"],
        "auditOwnerKey": audit["ownerKey"],
        "auditFenceToken": audit["fenceToken"],
        "maxAttempts": cases["authority"]["maxAttempts"],
    }
    value["authoritySha256"] = _sha(value)
    return value


def _budget(cases: dict[str, Any], authority: dict[str, Any], remaining: int = 1) -> dict[str, Any]:
    value = {
        "schemaVersion": "RECOVERY_BUDGET_V1",
        "authorityId": authority["authorityId"],
        "authoritySha256": authority["authoritySha256"],
        "remainingAttempts": remaining,
    }
    value["budgetSha256"] = _sha(value)
    return value


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _s1_tree_snapshot(root: Path) -> dict[str, dict[str, Any]]:
    ignored = {
        "recovery-ledger-v1.sqlite3",
        "recovery-ledger-v1.sqlite3-wal",
        "recovery-ledger-v1.sqlite3-shm",
    }
    snapshot: dict[str, dict[str, Any]] = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path.name in ignored:
            continue
        payload = path.read_bytes()
        relative = path.relative_to(root).as_posix()
        snapshot[relative] = {
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": payload,
            "mtimeNs": path.stat().st_mtime_ns,
        }
    return snapshot


def _sqlite_json_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"__bytes__": value.hex()}
    return value


def _s1_logical_dump(root: Path) -> list[dict[str, Any]]:
    source_control = root / "control"
    source_path = source_control / "control-ledger-v1.sqlite3"
    if not source_path.exists():
        return []
    # A read-only SQLite connection can still create/update a WAL SHM sidecar
    # when it opens a WAL database.  Clone the closed triplet outside the
    # observed runtime, query the clone, then remove only that test-owned clone.
    clone_root = root.parent / f"{root.name}-logical-observation-clone"
    if clone_root.exists():
        shutil.rmtree(clone_root)
    clone_control = clone_root / "control"
    clone_control.mkdir(parents=True)
    for suffix in ("", "-wal", "-shm"):
        source = source_control / f"control-ledger-v1.sqlite3{suffix}"
        if source.exists():
            shutil.copy2(source, clone_control / source.name)
    clone_path = clone_control / "control-ledger-v1.sqlite3"
    uri = f"file:{clone_path.as_posix()}?mode=ro"
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(uri, uri=True)
        tables = connection.execute(
            "SELECT type,name,sql FROM sqlite_master WHERE type IN ('table','index','trigger','view') ORDER BY type,name"
        ).fetchall()
        dump: list[dict[str, Any]] = []
        for object_type, name, sql in tables:
            item: dict[str, Any] = {"type": object_type, "name": name, "sql": sql}
            if object_type in {"table", "view"} and sql is not None and name not in {"sqlite_sequence"}:
                quoted = '"' + str(name).replace('"', '""') + '"'
                columns = [row[1] for row in connection.execute(f"PRAGMA table_info({quoted})").fetchall()]
                rows = connection.execute(f"SELECT * FROM {quoted}").fetchall()
                item["columns"] = columns
                item["rows"] = [[_sqlite_json_value(value) for value in row] for row in rows]
            dump.append(item)
        return dump
    finally:
        if connection is not None:
            connection.close()
        shutil.rmtree(clone_root, ignore_errors=True)


def _s1_state(root: Path) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    logical = _s1_logical_dump(root)
    return _s1_tree_snapshot(root), logical


def _recovery_triplet_snapshot(root: Path) -> dict[str, tuple[bytes, int] | None]:
    control = root / "control"
    return {
        suffix or "main": (
            (control / f"recovery-ledger-v1.sqlite3{suffix}").read_bytes(),
            (control / f"recovery-ledger-v1.sqlite3{suffix}").stat().st_mtime_ns,
        )
        if (control / f"recovery-ledger-v1.sqlite3{suffix}").exists()
        else None
        for suffix in ("", "-wal", "-shm")
    }


def _recovery_table_inventory(root: Path) -> tuple[bool, int]:
    path = root / "control" / "recovery-ledger-v1.sqlite3"
    if not path.exists():
        return False, 0
    with sqlite3.connect(path) as connection:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='recovery_records'"
        ).fetchone()
        count = (
            connection.execute("SELECT COUNT(*) FROM recovery_records WHERE issue_date=?", ("2026-08-21",)).fetchone()[0]
            if table is not None
            else 0
        )
    return table is not None, int(count)


def _recovery_record(root: Path) -> dict[str, Any]:
    path = root / "control" / "recovery-ledger-v1.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        count = connection.execute("SELECT COUNT(*) FROM recovery_records WHERE issue_date=?", ("2026-08-21",)).fetchone()[0]
        assert count == 1
        row = connection.execute(
            "SELECT " + ",".join(RECOVERY_RECORD_COLUMNS) + " FROM recovery_records WHERE issue_date=?",
            ("2026-08-21",),
        ).fetchone()
    if row is None:
        raise AssertionError("recovery record is missing")
    return {column: row[column] for column in RECOVERY_RECORD_COLUMNS}


def _record_sha(record: dict[str, Any]) -> str:
    return _sha({column: record[column] for column in RECOVERY_RECORD_COLUMNS if column != "record_sha256"})


def _inspection(controller: Any, cases: dict[str, Any]) -> dict[str, Any]:
    value = controller.inspect(cases["issueDate"])
    assert value["schemaVersion"] == "RECOVERY_INSPECTION_V1"
    assert value["issueDate"] == cases["issueDate"]
    return value


def _assert_inspection_phase(
    inspection: dict[str, Any],
    record: dict[str, Any],
    expected: dict[str, Any],
    authority: dict[str, Any],
    budget: dict[str, Any],
) -> None:
    assert inspection["phase"] == expected["phase"] == record["phase"]
    assert inspection["attemptsUsed"] == 1 == record["attempts_used"]
    assert inspection["bindingSha256"] == record["binding_sha256"]
    assert inspection["authoritySha256"] == authority["authoritySha256"] == record["authority_sha256"]
    assert inspection["budgetSha256"] == budget["budgetSha256"] == record["budget_sha256"]
    assert inspection["recordSha256"] == record["record_sha256"] == _record_sha(record)
    for prefix, present in (
        ("execution", expected["execution"]),
        ("public", expected["public"]),
        ("result", expected["result"]),
    ):
        json_key = {"execution": "execution_receipt_json", "public": "public_receipt_json", "result": "result_json"}[prefix]
        hash_key = f"{prefix}_receipt_sha256" if prefix != "result" else "result_sha256"
        assert (record[json_key] is not None) is present
        assert (record[hash_key] is not None) is present
        camel_hash = {
            "execution": "executionReceiptSha256",
            "public": "publicReceiptSha256",
            "result": "resultSha256",
        }[prefix]
        assert inspection[camel_hash] == record[hash_key]


def _mutate_record(root: Path, issue_date: str, mutation: str) -> None:
    path = root / "control" / "recovery-ledger-v1.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT " + ",".join(RECOVERY_RECORD_COLUMNS) + " FROM recovery_records WHERE issue_date=?",
            (issue_date,),
        ).fetchone()
        if row is None:
            raise AssertionError("recovery record is missing")
        record = {column: row[column] for column in RECOVERY_RECORD_COLUMNS}

        def reseal() -> None:
            record["record_sha256"] = _record_sha(record)

        if mutation in {"missing_binding_key", "extra_binding_key"}:
            binding = json.loads(record["binding_json"])
            assert isinstance(binding, dict)
            if mutation == "missing_binding_key":
                binding.pop(next(iter(binding)))
            else:
                binding["unexpectedBindingKey"] = "drift"
            record["binding_json"] = json.dumps(binding, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            record["binding_sha256"] = _sha(binding)
            reseal()
        elif mutation == "unknown_phase":
            record["phase"] = "UNKNOWN_PHASE"
            reseal()
        elif mutation == "attempts_zero":
            record["attempts_used"] = 0
            reseal()
        elif mutation == "attempts_two":
            record["attempts_used"] = 2
            reseal()
        elif mutation == "attempts_bool":
            record["attempts_used"] = "true"
            reseal()
        elif mutation == "recovery_id_drift":
            record["recovery_id"] = "drifted-recovery-id"
            reseal()
        elif mutation == "authority_hash_drift":
            record["authority_sha256"] = "a" * 64
            reseal()
        elif mutation == "budget_hash_drift":
            record["budget_sha256"] = "b" * 64
            reseal()
        elif mutation.startswith("execution_receipt_") or mutation.startswith("public_receipt_"):
            prefix = "execution" if mutation.startswith("execution_") else "public"
            json_key = f"{prefix}_receipt_json"
            hash_key = f"{prefix}_receipt_sha256"
            value = mutation.removeprefix(f"{prefix}_receipt_")
            if value == "syntax":
                record[json_key] = "{not-json"
            else:
                receipt = json.loads(record[json_key])
                assert isinstance(receipt, dict)
                if value == "schema":
                    receipt.pop("schemaVersion", None)
                elif value == "hash":
                    record[hash_key] = "c" * 64
                elif value == "binding":
                    receipt["lineage"] = "public" if prefix == "execution" else "execution"
                if value != "hash":
                    record[json_key] = json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    record[hash_key] = _sha(receipt)
            reseal()
        elif mutation in {"history_syntax", "history_hash", "history_lineage"}:
            if mutation == "history_syntax":
                record["history_json"] = "{not-json"
            elif mutation == "history_hash":
                record["history_sha256"] = "d" * 64
            else:
                history = json.loads(record["history_json"])
                assert isinstance(history, list) and history
                assert isinstance(history[-1], dict)
                history[-1]["lineage"] = "Scheduled"
                record["history_json"] = json.dumps(history, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                record["history_sha256"] = _sha(history)
            reseal()
        elif mutation in {"result_syntax", "result_hash", "result_recovery_id"}:
            if mutation == "result_syntax":
                record["result_json"] = "{not-json"
            elif mutation == "result_hash":
                record["result_sha256"] = "e" * 64
            else:
                result = json.loads(record["result_json"])
                assert isinstance(result, dict)
                result["recoveryId"] = "drifted-recovery-id"
                record["result_json"] = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                record["result_sha256"] = _sha(result)
            reseal()
        else:
            raise AssertionError(f"unknown persisted mutation: {mutation}")
        assignments = ",".join(f"{column}=?" for column in RECOVERY_RECORD_COLUMNS if column != "issue_date")
        connection.execute(
            f"UPDATE recovery_records SET {assignments} WHERE issue_date=?",
            [record[column] for column in RECOVERY_RECORD_COLUMNS if column != "issue_date"] + [issue_date],
        )
        connection.commit()


def _mutate_terminal_legacy(root: Path, issue_date: str, mutation: str) -> None:
    """result.legacyだけをsemanticに変え、result/record sealはproduction同型で再計算する。"""

    path = root / "control" / "recovery-ledger-v1.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT " + ",".join(RECOVERY_RECORD_COLUMNS) + " FROM recovery_records WHERE issue_date=?",
            (issue_date,),
        ).fetchone()
        if row is None:
            raise AssertionError("recovery record is missing")
        record = {column: row[column] for column in RECOVERY_RECORD_COLUMNS}
        result = json.loads(record["result_json"])
        assert isinstance(result, dict)
        legacy = deepcopy(result["legacy"])
        assert isinstance(legacy, dict)
        if mutation == "malformed":
            result["legacy"] = "not-an-object"
        else:
            snapshot = deepcopy(legacy["snapshot"])
            if mutation == "missing_key":
                snapshot.pop("status")
            elif mutation == "extra_key":
                snapshot["unexpected"] = "legacy-drift"
            elif mutation == "unknown_schema":
                snapshot["schemaVersion"] = "LEGACY_UNKNOWN_V9"
            elif mutation == "wrong_issueDate":
                snapshot["issueDate"] = "2026-08-20"
            elif mutation == "wrong_status":
                snapshot["status"] = "GREEN"
            elif mutation == "payload_hash_drift":
                snapshot["payloadSha256"] = "0" * 64
            elif mutation == "bytes_hash_drift":
                legacy["bytesSha256"] = "f" * 64
                result["legacy"] = legacy
                snapshot = None
            else:
                raise AssertionError(f"unknown legacy result mutation: {mutation}")
            if snapshot is not None:
                if mutation in {"wrong_issueDate", "wrong_status"}:
                    snapshot["payloadSha256"] = _sha({key: value for key, value in snapshot.items() if key != "payloadSha256"})
                legacy["snapshot"] = snapshot
                result["legacy"] = legacy
        record["result_json"] = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        record["result_sha256"] = _sha(result)
        record["record_sha256"] = _record_sha(record)
        assignments = ",".join(f"{column}=?" for column in RECOVERY_RECORD_COLUMNS if column != "issue_date")
        connection.execute(
            f"UPDATE recovery_records SET {assignments} WHERE issue_date=?",
            [record[column] for column in RECOVERY_RECORD_COLUMNS if column != "issue_date"] + [issue_date],
        )
        connection.commit()


class Child:
    def __init__(self, name: str, *, terminal: str = "CONFIRMED") -> None:
        self.name = name
        self.terminal = terminal
        self.calls: list[dict[str, Any]] = []

    def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(dict(request))
        return {
            "schemaVersion": f"{self.name.upper()}_RECONCILE_RESULT_V1",
            "status": self.terminal,
            "lineage": self.name,
            "terminalHash": _sha({"name": self.name, "request": request}),
        }


def _controller(module: Any, root: Path, execution: Child, public: Child, legacy: Any = None, **kwargs: Any) -> Any:
    return module.RecoveryController(
        root,
        execution_reconciler=execution,
        public_reconciler=public,
        legacy_reader=legacy,
        **kwargs,
    )


def _audit(controller: Any, cases: dict[str, Any], parent: dict[str, Any], authority: dict[str, Any], budget: dict[str, Any]) -> Any:
    return controller.audit(
        issue_date=cases["issueDate"],
        parent=parent,
        authority=authority,
        budget=budget,
        observed_at=_at(6, 41),
    )


def _legacy_file(root: Path, case: str) -> Path:
    path = root / "legacy-v3.json"
    if case == "valid_v3":
        payload = {"schemaVersion": "LEGACY_RECOVERY_V3", "issueDate": "2026-08-21", "status": "FAILED"}
        payload["payloadSha256"] = _sha({key: value for key, value in payload.items() if key != "payloadSha256"})
        path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    elif case == "unknown_schema":
        path.write_text(json.dumps({"schemaVersion": "LEGACY_UNKNOWN_V9"}), encoding="utf-8")
    elif case == "corrupt_v3":
        path.write_bytes(b"not-json")
    else:
        payload = {"schemaVersion": "LEGACY_RECOVERY_V3", "issueDate": "2026-08-21", "status": "FAILED"}
        if case == "missing_key":
            payload.pop("status")
        elif case == "extra_key":
            payload["unexpected"] = "legacy-drift"
        elif case == "wrong_issueDate":
            payload["issueDate"] = "2026-08-20"
        elif case == "wrong_status":
            payload["status"] = "GREEN"
        elif case == "payload_hash_drift":
            payload["payloadSha256"] = "0" * 64
        else:
            raise AssertionError(f"unknown legacy case: {case}")
        if "payloadSha256" not in payload:
            payload["payloadSha256"] = _sha({key: value for key, value in payload.items() if key != "payloadSha256"})
        path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return path


def test_s4_recovery_authority_matrix(tmp_path: Path) -> None:
    module = importlib.import_module("tools.news_grasp_cleanroom_recovery")
    cases = _cases()
    root = _s1_runtime(tmp_path, 0)
    scheduled = _slot(root, "Scheduled")
    audit = _slot(root, "Audit")
    parent = _parent(cases, scheduled)
    authority = _authority(cases, scheduled, audit)
    valid_budget = _budget(cases, authority)
    execution = Child("execution")
    public = Child("public")
    result = _audit(_controller(module, root, execution, public), cases, parent, authority, valid_budget)
    assert result["schemaVersion"] == "RECOVERY_RECONCILE_RESULT_V1"
    assert len(execution.calls) == 1 and len(public.calls) == 1
    failures = {}
    for index, failure in enumerate(cases["authorityFailures"], start=1):
        failure_root = _s1_runtime(tmp_path, index)
        failure_scheduled = _slot(failure_root, "Scheduled")
        failure_audit = _slot(failure_root, "Audit")
        failure_parent = _parent(cases, failure_scheduled)
        failure_authority = _authority(cases, failure_scheduled, failure_audit)
        failure_budget = _budget(cases, failure_authority)
        if failure == "MALFORMED_AUTHORITY":
            failure_authority.pop("authoritySha256")
        elif failure == "WRONG_PARENT":
            failure_parent["terminalHash"] = "p" * 64
            failure_parent["parentSha256"] = _sha({key: value for key, value in failure_parent.items() if key != "parentSha256"})
        elif failure == "STALE_GENERATION":
            failure_authority["scheduledGeneration"] += 1
            failure_authority["authoritySha256"] = _sha({key: value for key, value in failure_authority.items() if key != "authoritySha256"})
        elif failure == "STALE_OWNER":
            failure_authority["auditOwnerKey"] = "stale-owner"
            failure_authority["authoritySha256"] = _sha({key: value for key, value in failure_authority.items() if key != "authoritySha256"})
        elif failure == "STALE_FENCE":
            failure_authority["auditFenceToken"] += 1
            failure_authority["authoritySha256"] = _sha({key: value for key, value in failure_authority.items() if key != "authoritySha256"})
        elif failure == "EXHAUSTED_BUDGET":
            failure_budget = _budget(cases, failure_authority, remaining=0)
        elif failure == "MISMATCHED_BUDGET":
            failure_budget["authoritySha256"] = "b" * 64
            failure_budget["budgetSha256"] = _sha({key: value for key, value in failure_budget.items() if key != "budgetSha256"})
        else:
            failure_parent["route"] = "unknown"
            failure_parent["parentSha256"] = _sha({key: value for key, value in failure_parent.items() if key != "parentSha256"})
        child_execution = Child("execution")
        child_public = Child("public")
        try:
            _audit(_controller(module, failure_root, child_execution, child_public), cases, failure_parent, failure_authority, failure_budget)
        except Exception as caught:
            failures[failure] = (getattr(caught, "reason", type(caught).__name__), len(child_execution.calls), len(child_public.calls))
        else:
            failures[failure] = ("returned", len(child_execution.calls), len(child_public.calls))
    expected_reasons = {
        "MALFORMED_AUTHORITY": "RECOVERY_AUTHORITY_INVALID",
        "WRONG_PARENT": "RECOVERY_PARENT_INVALID",
        "STALE_GENERATION": "RECOVERY_GENERATION_STALE",
        "STALE_OWNER": "RECOVERY_AUDIT_OWNER_STALE",
        "STALE_FENCE": "RECOVERY_AUDIT_FENCE_STALE",
        "EXHAUSTED_BUDGET": "RECOVERY_BUDGET_EXHAUSTED",
        "MISMATCHED_BUDGET": "RECOVERY_BUDGET_MISMATCH",
        "UNKNOWN_ROUTE": "RECOVERY_ROUTE_UNKNOWN",
    }
    assert [failure for failure in failures if failure not in expected_reasons] == []
    assert all(failures[failure][0] == expected_reasons[failure] for failure in expected_reasons), failures
    assert all(execution_calls == 0 and public_calls == 0 for _, execution_calls, public_calls in failures.values())


def test_s4_recovery_retry_preserves_lineage(tmp_path: Path) -> None:
    module = importlib.import_module("tools.news_grasp_cleanroom_recovery")
    cases = _cases()
    observations: list[dict[str, Any]] = []
    for index, boundary in enumerate(cases["crashBoundaries"], start=100):
        root = _s1_runtime(tmp_path, index)
        scheduled = _slot(root, "Scheduled")
        audit = _slot(root, "Audit")
        parent = _parent(cases, scheduled)
        authority = _authority(cases, scheduled, audit)
        budget = _budget(cases, authority)
        before = _s1_state(root)
        before_repeat = _s1_state(root)
        assert before_repeat == before
        crashed = {"active": True}
        hook_calls: list[str] = []

        def hook(name: str) -> None:
            hook_calls.append(name)
            if crashed["active"] and name == boundary:
                crashed["active"] = False
                raise RuntimeError(f"test-owned recovery crash: {name}")

        first_execution = Child("execution")
        first_public = Child("public")
        first = _controller(module, root, first_execution, first_public, boundary_hook=hook)
        with pytest.raises(RuntimeError) as caught:
            _audit(first, cases, parent, authority, budget)
        assert str(caught.value) == f"test-owned recovery crash: {boundary}"
        assert crashed["active"] is False
        assert boundary in hook_calls
        assert _s1_state(root) == before

        crash_record = _recovery_record(root)
        crash_inspection = _inspection(
            _controller(module, root, Child("inspection-execution"), Child("inspection-public")),
            cases,
        )
        _assert_inspection_phase(
            crash_inspection,
            crash_record,
            cases["phaseExpectations"][boundary],
            authority,
            budget,
        )

        second_execution = Child("execution")
        second_public = Child("public")
        second = _audit(
            _controller(module, root, second_execution, second_public),
            cases,
            parent,
            authority,
            budget,
        )
        terminal_record = _recovery_record(root)
        terminal_inspection = _inspection(
            _controller(module, root, Child("inspection-execution"), Child("inspection-public")),
            cases,
        )
        terminal_expected = cases["phaseExpectations"]["after_recovery_commit"]
        _assert_inspection_phase(terminal_inspection, terminal_record, terminal_expected, authority, budget)
        assert second["attemptsUsed"] == 1
        assert second["recoveryId"] == terminal_record["recovery_id"]
        assert _s1_state(root) == before

        terminal_execution = Child("execution")
        terminal_public = Child("public")
        terminal_retry = _audit(
            _controller(module, root, terminal_execution, terminal_public),
            cases,
            parent,
            authority,
            budget,
        )
        assert second == terminal_retry
        assert _canonical_bytes(second) == _canonical_bytes(terminal_retry)
        assert _recovery_record(root) == terminal_record
        assert _s1_state(root) == before
        assert len(first_execution.calls) + len(second_execution.calls) + len(terminal_execution.calls) == 1
        assert len(first_public.calls) + len(second_public.calls) + len(terminal_public.calls) == 1
        assert second["attemptsUsed"] == 1

        conflicting_authority = _authority(
            cases,
            scheduled,
            audit,
            authority_id=f'{authority["authorityId"]}-conflict',
        )
        conflicting_budget = _budget(cases, conflicting_authority)
        conflict_execution = Child("execution")
        conflict_public = Child("public")
        with pytest.raises(module.RecoveryControlError) as conflict:
            _audit(
                _controller(module, root, conflict_execution, conflict_public),
                cases,
                parent,
                conflicting_authority,
                conflicting_budget,
            )
        assert conflict.value.reason == "RECOVERY_AUTHORITY_CONFLICT"
        assert not conflict_execution.calls and not conflict_public.calls
        assert _recovery_record(root) == terminal_record
        assert _s1_state(root) == before
        observations.append(
            {
                "boundary": boundary,
                "result": second,
                "terminalRetry": terminal_retry,
                "executionCalls": len(first_execution.calls) + len(second_execution.calls) + len(terminal_execution.calls),
                "publicCalls": len(first_public.calls) + len(second_public.calls) + len(terminal_public.calls),
                "before": before,
                "after": _s1_state(root),
            }
        )
    assert all(item["result"] == item["terminalRetry"] for item in observations)
    assert all(_canonical_bytes(item["result"]) == _canonical_bytes(item["terminalRetry"]) for item in observations)
    assert all(item["result"]["recoveryId"] == item["terminalRetry"]["recoveryId"] for item in observations)
    assert len({item["result"]["recoveryId"] for item in observations}) == len(observations)
    assert all(item["executionCalls"] == 1 and item["publicCalls"] == 1 for item in observations)
    assert all(item["before"] == item["after"] for item in observations)
    assert all(item["result"]["attemptsUsed"] == 1 for item in observations)
    assert all(item["result"]["recoveryHistory"][-1]["lineage"] == "Recovery" for item in observations)

    for mutation_index, mutation in enumerate(cases["persistedRecordMutations"], start=1000):
        root = _s1_runtime(tmp_path, mutation_index)
        scheduled = _slot(root, "Scheduled")
        audit = _slot(root, "Audit")
        parent = _parent(cases, scheduled)
        authority = _authority(cases, scheduled, audit)
        budget = _budget(cases, authority)
        before = _s1_state(root)
        assert _s1_state(root) == before
        _audit(
            _controller(module, root, Child("execution"), Child("public")),
            cases,
            parent,
            authority,
            budget,
        )
        _mutate_record(root, cases["issueDate"], mutation)
        retry_execution = Child("execution")
        retry_public = Child("public")
        with pytest.raises(module.RecoveryControlError) as corrupted:
            _audit(
                _controller(module, root, retry_execution, retry_public),
                cases,
                parent,
                authority,
                budget,
            )
        assert corrupted.value.reason == "RECOVERY_LEDGER_CORRUPT"
        assert not retry_execution.calls and not retry_public.calls
        assert _s1_state(root) == before

    for mutation_index, mutation in enumerate(cases["legacyTerminalMutations"], start=1100):
        root = _s1_runtime(tmp_path, mutation_index)
        scheduled = _slot(root, "Scheduled")
        audit = _slot(root, "Audit")
        parent = _parent(cases, scheduled)
        authority = _authority(cases, scheduled, audit)
        budget = _budget(cases, authority)
        legacy_path = _legacy_file(root, "valid_v3")
        legacy_before = (legacy_path.read_bytes(), legacy_path.stat().st_mtime_ns)
        _audit(
            _controller(
                module,
                root,
                Child("execution"),
                Child("public"),
                legacy=module.LegacyReadOnlyAdapter(legacy_path),
            ),
            cases,
            parent,
            authority,
            budget,
        )
        _mutate_terminal_legacy(root, cases["issueDate"], mutation)
        post_mutation_triplet = _recovery_triplet_snapshot(root)
        post_mutation_s1 = _s1_state(root)
        for action in ("audit", "inspect"):
            execution = Child(f"legacy-{mutation}-execution-{action}")
            public = Child(f"legacy-{mutation}-public-{action}")
            controller = _controller(
                module,
                root,
                execution,
                public,
                legacy=module.LegacyReadOnlyAdapter(legacy_path),
            )
            with pytest.raises(module.RecoveryControlError) as caught:
                if action == "audit":
                    _audit(controller, cases, parent, authority, budget)
                else:
                    controller.inspect(cases["issueDate"])
            assert caught.value.reason == "RECOVERY_LEDGER_CORRUPT"
            assert not execution.calls and not public.calls
            assert _recovery_triplet_snapshot(root) == post_mutation_triplet
            assert _s1_state(root) == post_mutation_s1
            assert (legacy_path.read_bytes(), legacy_path.stat().st_mtime_ns) == legacy_before


def test_s4_legacy_writer_zero(tmp_path: Path) -> None:
    module = importlib.import_module("tools.news_grasp_cleanroom_recovery")
    cases = _cases()
    for index, legacy_case in enumerate(cases["legacyCases"], start=200):
        root = _s1_runtime(tmp_path, index)
        scheduled = _slot(root, "Scheduled")
        audit = _slot(root, "Audit")
        parent = _parent(cases, scheduled)
        authority = _authority(cases, scheduled, audit)
        budget = _budget(cases, authority)
        path = _legacy_file(root, legacy_case)
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        legacy = module.LegacyReadOnlyAdapter(path)
        public_methods = sorted(
            name for name in dir(legacy) if not name.startswith("_") and callable(getattr(legacy, name))
        )
        assert public_methods == ["read"]
        execution = Child("execution")
        public = Child("public")
        controller = _controller(module, root, execution, public, legacy=legacy)
        if legacy_case == "valid_v3":
            result = _audit(controller, cases, parent, authority, budget)
            assert result["legacyWriterCount"] == 0
            assert result["legacy"]["bytesSha256"] == hashlib.sha256(before[0]).hexdigest()
            assert result["legacy"]["snapshot"] == json.loads(before[0].decode("utf-8"))
            assert len(execution.calls) == 1 and len(public.calls) == 1
            assert execution.calls[0]["schemaVersion"] == "RECOVERY_CHILD_REQUEST_V1"
            assert public.calls[0]["schemaVersion"] == "RECOVERY_CHILD_REQUEST_V1"
            inspection = _inspection(
                _controller(module, root, Child("inspection-execution"), Child("inspection-public"), legacy=module.LegacyReadOnlyAdapter(path)),
                cases,
            )
            assert inspection["legacyWriterCount"] == 0
            retry_execution = Child("execution")
            retry_public = Child("public")
            retry = _audit(
                _controller(module, root, retry_execution, retry_public, legacy=module.LegacyReadOnlyAdapter(path)),
                cases,
                parent,
                authority,
                budget,
            )
            assert retry == result
            assert not retry_execution.calls and not retry_public.calls
        else:
            s1_before = _s1_state(root)
            recovery_before = _recovery_triplet_snapshot(root)
            with pytest.raises(module.RecoveryControlError) as caught:
                _audit(controller, cases, parent, authority, budget)
            expected_reason = "LEGACY_STATE_UNKNOWN" if legacy_case == "unknown_schema" else "LEGACY_STATE_INVALID"
            assert getattr(caught.value, "reason", None) == expected_reason
            assert not execution.calls and not public.calls
            assert _recovery_triplet_snapshot(root) == recovery_before
            assert _recovery_table_inventory(root) == (False, 0)
            assert _s1_state(root) == s1_before
        assert (path.read_bytes(), path.stat().st_mtime_ns) == before
