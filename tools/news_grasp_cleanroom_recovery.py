"""S4 clean-room recovery plane。"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
from typing import Any, Callable, Mapping

from .news_grasp_cleanroom_contracts import (
    _entry_canonical_sha256,
    _managed_runtime_path,
    _validate_entry_time,
)


RECOVERY_AUTHORITY_INVALID = "RECOVERY_AUTHORITY_INVALID"
RECOVERY_PARENT_INVALID = "RECOVERY_PARENT_INVALID"
RECOVERY_GENERATION_STALE = "RECOVERY_GENERATION_STALE"
RECOVERY_AUDIT_OWNER_STALE = "RECOVERY_AUDIT_OWNER_STALE"
RECOVERY_AUDIT_FENCE_STALE = "RECOVERY_AUDIT_FENCE_STALE"
RECOVERY_BUDGET_EXHAUSTED = "RECOVERY_BUDGET_EXHAUSTED"
RECOVERY_BUDGET_MISMATCH = "RECOVERY_BUDGET_MISMATCH"
RECOVERY_ROUTE_UNKNOWN = "RECOVERY_ROUTE_UNKNOWN"
RECOVERY_AUTHORITY_CONFLICT = "RECOVERY_AUTHORITY_CONFLICT"
RECOVERY_CHILD_RECEIPT_INVALID = "RECOVERY_CHILD_RECEIPT_INVALID"
RECOVERY_LEDGER_CORRUPT = "RECOVERY_LEDGER_CORRUPT"
LEGACY_STATE_INVALID = "LEGACY_STATE_INVALID"
LEGACY_STATE_UNKNOWN = "LEGACY_STATE_UNKNOWN"

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_ISSUE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_RECORD_COLUMNS = (
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
_PHASES = (
    "ATTEMPT_DURABLE",
    "EXECUTION_RESULT_DURABLE",
    "EXECUTION_COMMITTED",
    "PUBLIC_RESULT_DURABLE",
    "PUBLIC_COMMITTED",
    "RESULT_DURABLE",
    "COMMITTED",
)
_BINDING_KEYS = frozenset(
    {"schemaVersion", "issueDate", "parentSha256", "authoritySha256", "budgetSha256", "recoveryId"}
)
_PARENT_KEYS = frozenset(
    {
        "schemaVersion",
        "lineage",
        "issueDate",
        "scheduleId",
        "slotKey",
        "terminalState",
        "terminalHash",
        "generation",
        "parentSha256",
    }
)
_AUTHORITY_KEYS = frozenset(
    {
        "schemaVersion",
        "authorityId",
        "issueDate",
        "scheduledParentTerminalHash",
        "scheduledGeneration",
        "auditOwnerKey",
        "auditFenceToken",
        "maxAttempts",
        "authoritySha256",
    }
)
_BUDGET_KEYS = frozenset(
    {"schemaVersion", "authorityId", "authoritySha256", "remainingAttempts", "budgetSha256"}
)
_RECEIPT_KEYS = frozenset({"schemaVersion", "status", "lineage", "terminalHash"})
_LEGACY_KEYS = frozenset({"schemaVersion", "issueDate", "status", "payloadSha256"})


class RecoveryControlError(Exception):
    """S4 の typed failure。"""

    def __init__(self, reason: str, message: str | None = None) -> None:
        self.reason = reason
        super().__init__(message or reason)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(value: Any) -> str:
    return _entry_canonical_sha256(value)


def _parse_object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, str):
        raise RecoveryControlError(RECOVERY_LEDGER_CORRUPT, f"{field} is not JSON text")
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise RecoveryControlError(RECOVERY_LEDGER_CORRUPT, f"{field} is not valid JSON") from exc
    if not isinstance(parsed, dict) or value != _canonical(parsed):
        raise RecoveryControlError(RECOVERY_LEDGER_CORRUPT, f"{field} is not canonical")
    return parsed


def _parse_any(value: Any, field: str) -> Any:
    if not isinstance(value, str):
        raise RecoveryControlError(RECOVERY_LEDGER_CORRUPT, f"{field} is not JSON text")
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise RecoveryControlError(RECOVERY_LEDGER_CORRUPT, f"{field} is not valid JSON") from exc
    if value != _canonical(parsed):
        raise RecoveryControlError(RECOVERY_LEDGER_CORRUPT, f"{field} is not canonical")
    return parsed


def _issue(value: Any) -> str:
    if not isinstance(value, str) or _ISSUE_DATE.fullmatch(value) is None:
        raise RecoveryControlError(RECOVERY_AUTHORITY_INVALID, "issueDate is invalid")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise RecoveryControlError(RECOVERY_AUTHORITY_INVALID, "issueDate is invalid") from exc
    return value


def _aware_time(value: Any) -> datetime:
    if not isinstance(value, datetime):
        raise RecoveryControlError(RECOVERY_AUTHORITY_INVALID, "observedAt is invalid")
    try:
        return _validate_entry_time(value)
    except Exception as exc:
        raise RecoveryControlError(RECOVERY_AUTHORITY_INVALID, "observedAt is invalid") from exc


def _nonempty_string(value: Any, reason: str, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise RecoveryControlError(reason, f"{label} is invalid")
    return value


def _hash_field(value: Any, reason: str, label: str) -> str:
    if not isinstance(value, str) or _HEX64.fullmatch(value) is None:
        raise RecoveryControlError(reason, f"{label} is invalid")
    return value


class LegacyReadOnlyAdapter:
    """旧 V3 JSON の read 専用 adapter。"""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def read(self) -> dict[str, Any]:
        try:
            raw = self._path.read_bytes()
        except (OSError, ValueError) as exc:
            raise RecoveryControlError(LEGACY_STATE_INVALID, "legacy state cannot be read") from exc
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            raise RecoveryControlError(LEGACY_STATE_INVALID, "legacy state is malformed") from exc
        if not isinstance(parsed, dict):
            raise RecoveryControlError(LEGACY_STATE_INVALID, "legacy state is not an object")
        if parsed.get("schemaVersion") != "LEGACY_RECOVERY_V3":
            raise RecoveryControlError(LEGACY_STATE_UNKNOWN, "legacy schema is unknown")
        if set(parsed) != _LEGACY_KEYS:
            raise RecoveryControlError(LEGACY_STATE_INVALID, "legacy keys are invalid")
        if not isinstance(parsed.get("issueDate"), str) or _ISSUE_DATE.fullmatch(parsed["issueDate"]) is None:
            raise RecoveryControlError(LEGACY_STATE_INVALID, "legacy issueDate is invalid")
        if parsed.get("status") != "FAILED":
            raise RecoveryControlError(LEGACY_STATE_INVALID, "legacy status is invalid")
        payload_hash = parsed.get("payloadSha256")
        if not isinstance(payload_hash, str) or _HEX64.fullmatch(payload_hash) is None:
            raise RecoveryControlError(LEGACY_STATE_INVALID, "legacy payload hash is invalid")
        unsigned = {key: value for key, value in parsed.items() if key != "payloadSha256"}
        if payload_hash != _sha(unsigned):
            raise RecoveryControlError(LEGACY_STATE_INVALID, "legacy payload hash drift")
        return {
            "snapshot": parsed,
            "bytesSha256": hashlib.sha256(raw).hexdigest(),
        }


class RecoveryController:
    """S1 の authority を読み取り、S2/S3 の recovery を一度だけ収束させる。"""

    def __init__(
        self,
        runtime_root: Path,
        execution_reconciler: Callable[[dict[str, Any]], Mapping[str, Any]],
        public_reconciler: Callable[[dict[str, Any]], Mapping[str, Any]],
        legacy_reader: LegacyReadOnlyAdapter | None = None,
        boundary_hook: Callable[[str], None] | None = None,
    ) -> None:
        self.runtime_root = Path(runtime_root)
        self.control_root = _managed_runtime_path(self.runtime_root, self.runtime_root / "control")
        self.ledger_path = _managed_runtime_path(self.runtime_root, self.control_root / "recovery-ledger-v1.sqlite3")
        self.execution_reconciler = execution_reconciler
        self.public_reconciler = public_reconciler
        self.legacy_reader = legacy_reader
        self.boundary_hook = boundary_hook
        self.busy_timeout_ms = 1000

    def _hook(self, name: str) -> None:
        if self.boundary_hook is not None:
            self.boundary_hook(name)

    def _read_s1(self, issue_date: str, parent: Mapping[str, Any]) -> dict[str, Any]:
        path = _managed_runtime_path(self.runtime_root, self.control_root / "control-ledger-v1.sqlite3")
        if not path.exists():
            raise RecoveryControlError(RECOVERY_PARENT_INVALID, "S1 ledger is absent")
        uri = f"file:{path.as_posix()}?mode=ro"
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(uri, uri=True)
            connection.row_factory = sqlite3.Row
            scheduled = connection.execute(
                "SELECT * FROM slots WHERE schedule_id=? AND issue_date=? AND slot_kind='Scheduled'",
                (parent["scheduleId"], issue_date),
            ).fetchone()
            audit = connection.execute(
                "SELECT * FROM slots WHERE schedule_id=? AND issue_date=? AND slot_kind='Audit'",
                (parent["scheduleId"], issue_date),
            ).fetchone()
        except (sqlite3.Error, OSError) as exc:
            raise RecoveryControlError(RECOVERY_PARENT_INVALID, "S1 authority state cannot be read") from exc
        finally:
            if connection is not None:
                connection.close()
        if scheduled is None or audit is None:
            raise RecoveryControlError(RECOVERY_PARENT_INVALID, "S1 parent or audit slot is absent")
        if (
            scheduled["state"] != "TERMINAL"
            or scheduled["terminal_state"] != "FAILED"
            or scheduled["result_hash"] != parent["terminalHash"]
            or scheduled["generation"] != parent["generation"]
        ):
            raise RecoveryControlError(RECOVERY_PARENT_INVALID, "S1 Scheduled parent conflicts")
        return {"scheduled": dict(scheduled), "audit": dict(audit)}

    def _validate_parent(self, issue_date: str, parent: Any) -> dict[str, Any]:
        if isinstance(parent, Mapping) and "route" in parent:
            raise RecoveryControlError(RECOVERY_ROUTE_UNKNOWN, "recovery route is unknown")
        if not isinstance(parent, Mapping) or set(parent) != _PARENT_KEYS:
            raise RecoveryControlError(RECOVERY_PARENT_INVALID, "parent shape is invalid")
        value = dict(parent)
        if value["schemaVersion"] != "RECOVERY_PARENT_V1" or value["lineage"] != "Scheduled":
            raise RecoveryControlError(RECOVERY_PARENT_INVALID, "parent schema or lineage is invalid")
        if value["issueDate"] != issue_date or value["terminalState"] != "FAILED":
            raise RecoveryControlError(RECOVERY_PARENT_INVALID, "parent state is invalid")
        for key in ("scheduleId", "slotKey"):
            _nonempty_string(value.get(key), RECOVERY_PARENT_INVALID, key)
        if not isinstance(value["generation"], int) or isinstance(value["generation"], bool) or value["generation"] < 1:
            raise RecoveryControlError(RECOVERY_PARENT_INVALID, "parent generation is invalid")
        _hash_field(value.get("terminalHash"), RECOVERY_PARENT_INVALID, "parent terminalHash")
        _hash_field(value.get("parentSha256"), RECOVERY_PARENT_INVALID, "parentSha256")
        if value["parentSha256"] != _sha({key: item for key, item in value.items() if key != "parentSha256"}):
            raise RecoveryControlError(RECOVERY_PARENT_INVALID, "parentSha256 drift")
        expected_slot = f"{value['scheduleId']}/{issue_date}/Scheduled"
        if value["slotKey"] != expected_slot:
            raise RecoveryControlError(RECOVERY_PARENT_INVALID, "parent slot key is invalid")
        return value

    def _validate_authority(self, issue_date: str, parent: Mapping[str, Any], s1: Mapping[str, Any], authority: Any) -> dict[str, Any]:
        if not isinstance(authority, Mapping) or set(authority) != _AUTHORITY_KEYS:
            raise RecoveryControlError(RECOVERY_AUTHORITY_INVALID, "authority shape is invalid")
        value = dict(authority)
        if value["schemaVersion"] != "RECOVERY_AUTHORITY_V1":
            raise RecoveryControlError(RECOVERY_AUTHORITY_INVALID, "authority schema is invalid")
        _nonempty_string(value.get("authorityId"), RECOVERY_AUTHORITY_INVALID, "authorityId")
        if value["issueDate"] != issue_date:
            raise RecoveryControlError(RECOVERY_AUTHORITY_INVALID, "authority issueDate is invalid")
        _hash_field(value.get("scheduledParentTerminalHash"), RECOVERY_AUTHORITY_INVALID, "scheduledParentTerminalHash")
        if not isinstance(value["scheduledGeneration"], int) or isinstance(value["scheduledGeneration"], bool) or value["scheduledGeneration"] < 1:
            raise RecoveryControlError(RECOVERY_AUTHORITY_INVALID, "scheduledGeneration is invalid")
        _nonempty_string(value.get("auditOwnerKey"), RECOVERY_AUTHORITY_INVALID, "auditOwnerKey")
        if not isinstance(value["auditFenceToken"], int) or isinstance(value["auditFenceToken"], bool) or value["auditFenceToken"] < 1:
            raise RecoveryControlError(RECOVERY_AUTHORITY_INVALID, "auditFenceToken is invalid")
        if value["maxAttempts"] != 1:
            raise RecoveryControlError(RECOVERY_AUTHORITY_INVALID, "maxAttempts is invalid")
        _hash_field(value.get("authoritySha256"), RECOVERY_AUTHORITY_INVALID, "authoritySha256")
        if value["authoritySha256"] != _sha({key: item for key, item in value.items() if key != "authoritySha256"}):
            raise RecoveryControlError(RECOVERY_AUTHORITY_INVALID, "authoritySha256 drift")
        if value["scheduledParentTerminalHash"] != parent["terminalHash"]:
            raise RecoveryControlError(RECOVERY_PARENT_INVALID, "authority parent hash conflicts")
        if value["scheduledGeneration"] != s1["scheduled"]["generation"]:
            raise RecoveryControlError(RECOVERY_GENERATION_STALE, "scheduled generation is stale")
        if value["auditOwnerKey"] != s1["audit"]["owner_key"]:
            raise RecoveryControlError(RECOVERY_AUDIT_OWNER_STALE, "audit owner is stale")
        if value["auditFenceToken"] != s1["audit"]["fence_token"]:
            raise RecoveryControlError(RECOVERY_AUDIT_FENCE_STALE, "audit fence is stale")
        return value

    def _validate_budget(self, authority: Mapping[str, Any], budget: Any) -> dict[str, Any]:
        if not isinstance(budget, Mapping) or set(budget) != _BUDGET_KEYS:
            raise RecoveryControlError(RECOVERY_BUDGET_MISMATCH, "budget shape is invalid")
        value = dict(budget)
        if value["schemaVersion"] != "RECOVERY_BUDGET_V1":
            raise RecoveryControlError(RECOVERY_BUDGET_MISMATCH, "budget schema is invalid")
        if value["authorityId"] != authority["authorityId"] or value["authoritySha256"] != authority["authoritySha256"]:
            raise RecoveryControlError(RECOVERY_BUDGET_MISMATCH, "budget authority binding is invalid")
        if not isinstance(value["remainingAttempts"], int) or isinstance(value["remainingAttempts"], bool) or value["remainingAttempts"] not in {0, 1}:
            raise RecoveryControlError(RECOVERY_BUDGET_MISMATCH, "remainingAttempts is invalid")
        _hash_field(value.get("budgetSha256"), RECOVERY_BUDGET_MISMATCH, "budgetSha256")
        if value["budgetSha256"] != _sha({key: item for key, item in value.items() if key != "budgetSha256"}):
            raise RecoveryControlError(RECOVERY_BUDGET_MISMATCH, "budgetSha256 drift")
        if value["remainingAttempts"] == 0:
            raise RecoveryControlError(RECOVERY_BUDGET_EXHAUSTED, "recovery budget is exhausted")
        return value

    def _binding(self, issue_date: str, parent: Mapping[str, Any], authority: Mapping[str, Any], budget: Mapping[str, Any]) -> dict[str, Any]:
        recovery_id = _sha(
            {
                "issueDate": issue_date,
                "parentSha256": parent["parentSha256"],
                "authoritySha256": authority["authoritySha256"],
            }
        )
        return {
            "schemaVersion": "RECOVERY_BINDING_V1",
            "issueDate": issue_date,
            "parentSha256": parent["parentSha256"],
            "authoritySha256": authority["authoritySha256"],
            "budgetSha256": budget["budgetSha256"],
            "recoveryId": recovery_id,
        }

    def _connect_rw(self) -> sqlite3.Connection:
        self.control_root.mkdir(parents=True, exist_ok=True)
        try:
            connection = sqlite3.connect(
                self.ledger_path,
                timeout=self.busy_timeout_ms / 1000,
                isolation_level=None,
                check_same_thread=False,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
            self._schema(connection)
            return connection
        except RecoveryControlError:
            raise
        except sqlite3.Error as exc:
            raise RecoveryControlError(RECOVERY_LEDGER_CORRUPT, "recovery ledger cannot be opened") from exc

    def _schema(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS recovery_records (
                issue_date TEXT PRIMARY KEY,
                recovery_id TEXT NOT NULL,
                binding_json TEXT NOT NULL,
                binding_sha256 TEXT NOT NULL,
                authority_sha256 TEXT NOT NULL,
                budget_sha256 TEXT NOT NULL,
                attempts_used INTEGER NOT NULL,
                phase TEXT NOT NULL,
                execution_receipt_json TEXT,
                execution_receipt_sha256 TEXT,
                public_receipt_json TEXT,
                public_receipt_sha256 TEXT,
                history_json TEXT NOT NULL,
                history_sha256 TEXT NOT NULL,
                result_json TEXT,
                result_sha256 TEXT,
                record_sha256 TEXT NOT NULL
            )
            """
        )
        columns = [row[1] for row in connection.execute("PRAGMA table_info(recovery_records)").fetchall()]
        if tuple(columns) != _RECORD_COLUMNS:
            raise RecoveryControlError(RECOVERY_LEDGER_CORRUPT, "recovery ledger columns are invalid")

    @contextmanager
    def _transaction(self, connection: sqlite3.Connection):
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield
            connection.commit()
        except RecoveryControlError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise RecoveryControlError(RECOVERY_LEDGER_CORRUPT, "recovery ledger transaction failed") from exc
        except Exception:
            connection.rollback()
            raise

    def _record_sha(self, record: Mapping[str, Any]) -> str:
        return _sha({column: record[column] for column in _RECORD_COLUMNS if column != "record_sha256"})

    def _row_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return {column: row[column] for column in _RECORD_COLUMNS}

    def _validate_receipt(
        self,
        raw: Any,
        digest: Any,
        *,
        schema: str,
        lineage: str,
    ) -> dict[str, Any] | None:
        if raw is None or digest is None:
            if raw is not None or digest is not None:
                raise RecoveryControlError(RECOVERY_LEDGER_CORRUPT, "receipt/hash pairing is invalid")
            return None
        value = _parse_object(raw, f"{lineage} receipt")
        if set(value) != _RECEIPT_KEYS or value.get("schemaVersion") != schema or value.get("status") != "CONFIRMED" or value.get("lineage") != lineage:
            raise RecoveryControlError(RECOVERY_LEDGER_CORRUPT, f"{lineage} receipt shape is invalid")
        if not isinstance(value.get("terminalHash"), str) or not value["terminalHash"]:
            raise RecoveryControlError(RECOVERY_LEDGER_CORRUPT, f"{lineage} terminal hash is invalid")
        _hash_field(digest, RECOVERY_LEDGER_CORRUPT, f"{lineage} receipt hash")
        if digest != _sha(value):
            raise RecoveryControlError(RECOVERY_LEDGER_CORRUPT, f"{lineage} receipt hash drift")
        return value

    def _validate_record(self, record: Mapping[str, Any]) -> dict[str, Any]:
        if set(record) != set(_RECORD_COLUMNS):
            raise RecoveryControlError(RECOVERY_LEDGER_CORRUPT, "recovery record columns are invalid")
        _issue(record.get("issue_date"))
        _nonempty_string(record.get("recovery_id"), RECOVERY_LEDGER_CORRUPT, "recovery_id")
        _hash_field(record.get("record_sha256"), RECOVERY_LEDGER_CORRUPT, "record_sha256")
        if record["record_sha256"] != self._record_sha(record):
            raise RecoveryControlError(RECOVERY_LEDGER_CORRUPT, "record seal drift")
        if not isinstance(record["attempts_used"], int) or isinstance(record["attempts_used"], bool) or record["attempts_used"] != 1:
            raise RecoveryControlError(RECOVERY_LEDGER_CORRUPT, "attempts_used is invalid")
        if record["phase"] not in _PHASES:
            raise RecoveryControlError(RECOVERY_LEDGER_CORRUPT, "phase is unknown")
        binding = _parse_object(record["binding_json"], "binding")
        if set(binding) != _BINDING_KEYS or binding.get("schemaVersion") != "RECOVERY_BINDING_V1":
            raise RecoveryControlError(RECOVERY_LEDGER_CORRUPT, "binding shape is invalid")
        for key in ("issueDate", "parentSha256", "authoritySha256", "budgetSha256", "recoveryId"):
            _nonempty_string(binding.get(key), RECOVERY_LEDGER_CORRUPT, f"binding.{key}")
        for key in ("parentSha256", "authoritySha256", "budgetSha256", "recoveryId"):
            _hash_field(binding[key], RECOVERY_LEDGER_CORRUPT, f"binding.{key}")
        _hash_field(record["binding_sha256"], RECOVERY_LEDGER_CORRUPT, "binding_sha256")
        if record["binding_sha256"] != _sha(binding):
            raise RecoveryControlError(RECOVERY_LEDGER_CORRUPT, "binding hash drift")
        if binding["issueDate"] != record["issue_date"] or binding["recoveryId"] != record["recovery_id"]:
            raise RecoveryControlError(RECOVERY_LEDGER_CORRUPT, "binding identity drift")
        expected_recovery_id = _sha(
            {
                "issueDate": binding["issueDate"],
                "parentSha256": binding["parentSha256"],
                "authoritySha256": binding["authoritySha256"],
            }
        )
        if binding["recoveryId"] != expected_recovery_id:
            raise RecoveryControlError(RECOVERY_LEDGER_CORRUPT, "recoveryId drift")
        if record["authority_sha256"] != binding["authoritySha256"] or record["budget_sha256"] != binding["budgetSha256"]:
            raise RecoveryControlError(RECOVERY_LEDGER_CORRUPT, "record authority/budget binding drift")
        execution = self._validate_receipt(
            record["execution_receipt_json"],
            record["execution_receipt_sha256"],
            schema="EXECUTION_RECONCILE_RESULT_V1",
            lineage="execution",
        )
        public = self._validate_receipt(
            record["public_receipt_json"],
            record["public_receipt_sha256"],
            schema="PUBLIC_RECONCILE_RESULT_V1",
            lineage="public",
        )
        history = _parse_any(record["history_json"], "history")
        if not isinstance(history, list):
            raise RecoveryControlError(RECOVERY_LEDGER_CORRUPT, "history is not a list")
        _hash_field(record["history_sha256"], RECOVERY_LEDGER_CORRUPT, "history_sha256")
        if record["history_sha256"] != _sha(history):
            raise RecoveryControlError(RECOVERY_LEDGER_CORRUPT, "history hash drift")
        result: dict[str, Any] | None = None
        if record["result_json"] is None or record["result_sha256"] is None:
            if record["result_json"] is not None or record["result_sha256"] is not None:
                raise RecoveryControlError(RECOVERY_LEDGER_CORRUPT, "result/hash pairing is invalid")
        else:
            result = _parse_object(record["result_json"], "result")
            _hash_field(record["result_sha256"], RECOVERY_LEDGER_CORRUPT, "result_sha256")
            if record["result_sha256"] != _sha(result):
                raise RecoveryControlError(RECOVERY_LEDGER_CORRUPT, "result hash drift")
            self._validate_result(result, record, execution, public, history)
        phase = record["phase"]
        expected = {
            "ATTEMPT_DURABLE": (False, False, False),
            "EXECUTION_RESULT_DURABLE": (True, False, False),
            "EXECUTION_COMMITTED": (True, False, False),
            "PUBLIC_RESULT_DURABLE": (True, True, False),
            "PUBLIC_COMMITTED": (True, True, False),
            "RESULT_DURABLE": (True, True, True),
            "COMMITTED": (True, True, True),
        }[phase]
        if (execution is not None, public is not None, result is not None) != expected:
            raise RecoveryControlError(RECOVERY_LEDGER_CORRUPT, "phase receipt set is invalid")
        if bool(history) != expected[2]:
            raise RecoveryControlError(RECOVERY_LEDGER_CORRUPT, "phase history set is invalid")
        if history:
            if len(history) != 1 or not isinstance(history[-1], dict):
                raise RecoveryControlError(RECOVERY_LEDGER_CORRUPT, "history event set is invalid")
            event = history[-1]
            expected_event = {
                "lineage": "Recovery",
                "recoveryId": record["recovery_id"],
                "executionReceiptSha256": record["execution_receipt_sha256"],
                "publicReceiptSha256": record["public_receipt_sha256"],
            }
            if event != expected_event:
                raise RecoveryControlError(RECOVERY_LEDGER_CORRUPT, "history event binding is invalid")
        return {
            "record": dict(record),
            "binding": binding,
            "execution": execution,
            "public": public,
            "history": history,
            "result": result,
        }

    def _validate_result(
        self,
        result: Mapping[str, Any],
        record: Mapping[str, Any],
        execution: Mapping[str, Any] | None,
        public: Mapping[str, Any] | None,
        history: Any,
    ) -> None:
        keys = {"schemaVersion", "issueDate", "recoveryId", "status", "attemptsUsed", "execution", "public", "recoveryHistory", "legacy", "legacyWriterCount"}
        if set(result) != keys or result.get("schemaVersion") != "RECOVERY_RECONCILE_RESULT_V1" or result.get("issueDate") != record["issue_date"] or result.get("recoveryId") != record["recovery_id"] or result.get("status") != "CONFIRMED" or result.get("attemptsUsed") != 1 or result.get("legacyWriterCount") != 0:
            raise RecoveryControlError(RECOVERY_LEDGER_CORRUPT, "result semantics are invalid")
        self._validate_legacy_result(result.get("legacy"), record["issue_date"])
        if result.get("execution") != execution or result.get("public") != public or result.get("recoveryHistory") != history:
            raise RecoveryControlError(RECOVERY_LEDGER_CORRUPT, "result receipt/history binding is invalid")

    def _insert_record(self, connection: sqlite3.Connection, issue_date: str, binding: Mapping[str, Any], authority: Mapping[str, Any], budget: Mapping[str, Any]) -> dict[str, Any]:
        history: list[Any] = []
        record: dict[str, Any] = {
            "issue_date": issue_date,
            "recovery_id": binding["recoveryId"],
            "binding_json": _canonical(binding),
            "binding_sha256": _sha(binding),
            "authority_sha256": authority["authoritySha256"],
            "budget_sha256": budget["budgetSha256"],
            "attempts_used": 1,
            "phase": "ATTEMPT_DURABLE",
            "execution_receipt_json": None,
            "execution_receipt_sha256": None,
            "public_receipt_json": None,
            "public_receipt_sha256": None,
            "history_json": _canonical(history),
            "history_sha256": _sha(history),
            "result_json": None,
            "result_sha256": None,
            "record_sha256": "",
        }
        record["record_sha256"] = self._record_sha(record)
        try:
            connection.execute(
                "INSERT INTO recovery_records (" + ",".join(_RECORD_COLUMNS) + ") VALUES (" + ",".join("?" for _ in _RECORD_COLUMNS) + ")",
                [record[column] for column in _RECORD_COLUMNS],
            )
        except sqlite3.Error as exc:
            raise RecoveryControlError(RECOVERY_LEDGER_CORRUPT, "recovery record cannot be inserted") from exc
        return record

    def _write_record(self, connection: sqlite3.Connection, record: Mapping[str, Any]) -> None:
        value = dict(record)
        value["record_sha256"] = self._record_sha(value)
        if isinstance(record, dict):
            record["record_sha256"] = value["record_sha256"]
        assignments = ",".join(f"{column}=?" for column in _RECORD_COLUMNS if column != "issue_date")
        connection.execute(
            f"UPDATE recovery_records SET {assignments} WHERE issue_date=?",
            [value[column] for column in _RECORD_COLUMNS if column != "issue_date"] + [value["issue_date"]],
        )

    def _persist_phase(self, connection: sqlite3.Connection, record: dict[str, Any], phase: str) -> dict[str, Any]:
        record["phase"] = phase
        self._write_record(connection, record)
        return record

    def _persist_receipt(self, connection: sqlite3.Connection, record: dict[str, Any], prefix: str, receipt: Mapping[str, Any]) -> dict[str, Any]:
        record[f"{prefix}_receipt_json"] = _canonical(receipt)
        record[f"{prefix}_receipt_sha256"] = _sha(receipt)
        record["phase"] = "EXECUTION_RESULT_DURABLE" if prefix == "execution" else "PUBLIC_RESULT_DURABLE"
        self._write_record(connection, record)
        return record

    def _child_request(self, binding: Mapping[str, Any], prior_hash: str | None = None) -> dict[str, Any]:
        request: dict[str, Any] = {
            "schemaVersion": "RECOVERY_CHILD_REQUEST_V1",
            "issueDate": binding["issueDate"],
            "recoveryId": binding["recoveryId"],
            "parentSha256": binding["parentSha256"],
            "authoritySha256": binding["authoritySha256"],
            "budgetSha256": binding["budgetSha256"],
            "attemptNumber": 1,
        }
        if prior_hash is not None:
            request["priorReceipt"] = {"lineage": "execution", "receiptSha256": prior_hash}
        return request

    def _call_child(self, child: Callable[[dict[str, Any]], Mapping[str, Any]], request: dict[str, Any], *, lineage: str) -> dict[str, Any]:
        value = child(request)
        if not isinstance(value, Mapping):
            raise RecoveryControlError(RECOVERY_CHILD_RECEIPT_INVALID, f"{lineage} child result is not an object")
        receipt = dict(value)
        schema = "EXECUTION_RECONCILE_RESULT_V1" if lineage == "execution" else "PUBLIC_RECONCILE_RESULT_V1"
        if set(receipt) != _RECEIPT_KEYS or receipt.get("schemaVersion") != schema or receipt.get("status") != "CONFIRMED" or receipt.get("lineage") != lineage or not isinstance(receipt.get("terminalHash"), str) or not receipt["terminalHash"]:
            raise RecoveryControlError(RECOVERY_CHILD_RECEIPT_INVALID, f"{lineage} child receipt is invalid")
        return receipt

    def _validate_legacy_result(self, value: Any, issue_date: str, *, error_reason: str = RECOVERY_LEDGER_CORRUPT) -> dict[str, Any] | None:
        if value is None:
            return None
        if not isinstance(value, Mapping) or set(value) != {"snapshot", "bytesSha256"}:
            raise RecoveryControlError(error_reason, "legacy result shape is invalid")
        snapshot = value.get("snapshot")
        if not isinstance(snapshot, Mapping):
            raise RecoveryControlError(error_reason, "legacy snapshot is invalid")
        snapshot_value = dict(snapshot)
        if snapshot_value.get("schemaVersion") != "LEGACY_RECOVERY_V3":
            raise RecoveryControlError(error_reason, "legacy snapshot schema is invalid")
        if set(snapshot_value) != _LEGACY_KEYS:
            raise RecoveryControlError(error_reason, "legacy snapshot keys are invalid")
        if snapshot_value.get("issueDate") != issue_date or snapshot_value.get("status") != "FAILED":
            raise RecoveryControlError(error_reason, "legacy snapshot binding is invalid")
        payload_hash = snapshot_value.get("payloadSha256")
        if not isinstance(payload_hash, str) or _HEX64.fullmatch(payload_hash) is None:
            raise RecoveryControlError(error_reason, "legacy payload hash is invalid")
        if payload_hash != _sha({key: item for key, item in snapshot_value.items() if key != "payloadSha256"}):
            raise RecoveryControlError(error_reason, "legacy payload hash drift")
        bytes_hash = value.get("bytesSha256")
        if not isinstance(bytes_hash, str) or _HEX64.fullmatch(bytes_hash) is None:
            raise RecoveryControlError(error_reason, "legacy bytes hash is invalid")
        return {"snapshot": snapshot_value, "bytesSha256": bytes_hash}

    def _validate_live_legacy(self, result: Mapping[str, Any], legacy_value: dict[str, Any] | None) -> None:
        if result.get("legacy") != legacy_value:
            raise RecoveryControlError(RECOVERY_LEDGER_CORRUPT, "legacy result drift")

    def _read_legacy(self, issue_date: str) -> dict[str, Any] | None:
        if self.legacy_reader is None:
            return None
        reader = getattr(self.legacy_reader, "read", None)
        if not callable(reader):
            raise RecoveryControlError(LEGACY_STATE_INVALID, "legacy reader is not read-only")
        value = reader()
        return self._validate_legacy_result(value, issue_date, error_reason=LEGACY_STATE_INVALID)

    @contextmanager
    def _ledger_read_copy(self) -> Any:
        with tempfile.TemporaryDirectory(prefix="news_grasp_s4_ledger_") as temporary_root:
            copy_root = Path(temporary_root)
            copy_path = copy_root / self.ledger_path.name
            for suffix in ("", "-wal", "-shm"):
                source = Path(f"{self.ledger_path}{suffix}")
                if source.exists():
                    shutil.copyfile(source, Path(f"{copy_path}{suffix}"))
            yield copy_path

    def _preflight_existing_record(self, issue_date: str, legacy_value: dict[str, Any] | None) -> None:
        if not self.ledger_path.exists():
            return
        with self._ledger_read_copy() as copy_path:
            uri = f"file:{copy_path.as_posix()}?mode=ro"
            connection: sqlite3.Connection | None = None
            try:
                connection = sqlite3.connect(uri, uri=True)
                connection.row_factory = sqlite3.Row
                columns = [row[1] for row in connection.execute("PRAGMA table_info(recovery_records)").fetchall()]
                if not columns:
                    return
                if tuple(columns) != _RECORD_COLUMNS:
                    raise RecoveryControlError(RECOVERY_LEDGER_CORRUPT, "recovery ledger columns are invalid")
                row = connection.execute("SELECT " + ",".join(_RECORD_COLUMNS) + " FROM recovery_records WHERE issue_date=?", (issue_date,)).fetchone()
                if row is not None:
                    validated = self._validate_record(self._row_dict(row))
                    if self.legacy_reader is not None and validated["result"] is not None:
                        self._validate_live_legacy(validated["result"], legacy_value)
            except RecoveryControlError:
                raise
            except (sqlite3.Error, OSError) as exc:
                raise RecoveryControlError(RECOVERY_LEDGER_CORRUPT, "recovery ledger cannot be inspected") from exc
            finally:
                if connection is not None:
                    connection.close()

    def _load_rw_record(self, connection: sqlite3.Connection, issue_date: str) -> dict[str, Any] | None:
        row = connection.execute("SELECT " + ",".join(_RECORD_COLUMNS) + " FROM recovery_records WHERE issue_date=?", (issue_date,)).fetchone()
        if row is None:
            return None
        return self._validate_record(self._row_dict(row))

    def _stored_result(self, validated: Mapping[str, Any]) -> dict[str, Any]:
        result = validated.get("result")
        if not isinstance(result, dict):
            raise RecoveryControlError(RECOVERY_LEDGER_CORRUPT, "terminal result is absent")
        return json.loads(_canonical(result))

    def inspect(self, issue_date: str) -> dict[str, Any]:
        issue = _issue(issue_date)
        if not self.ledger_path.exists():
            return {
                "schemaVersion": "RECOVERY_INSPECTION_V1",
                "issueDate": issue,
                "phase": "ABSENT",
                "attemptsUsed": 0,
                "bindingSha256": None,
                "authoritySha256": None,
                "budgetSha256": None,
                "executionReceiptSha256": None,
                "publicReceiptSha256": None,
                "resultSha256": None,
                "recordSha256": None,
                "legacyWriterCount": 0,
            }
        with self._ledger_read_copy() as copy_path:
            uri = f"file:{copy_path.as_posix()}?mode=ro"
            connection: sqlite3.Connection | None = None
            try:
                connection = sqlite3.connect(uri, uri=True)
                connection.row_factory = sqlite3.Row
                columns = [row[1] for row in connection.execute("PRAGMA table_info(recovery_records)").fetchall()]
                if tuple(columns) != _RECORD_COLUMNS:
                    raise RecoveryControlError(RECOVERY_LEDGER_CORRUPT, "recovery ledger columns are invalid")
                row = connection.execute("SELECT " + ",".join(_RECORD_COLUMNS) + " FROM recovery_records WHERE issue_date=?", (issue,)).fetchone()
                if row is None:
                    raise RecoveryControlError(RECOVERY_LEDGER_CORRUPT, "recovery record is missing")
                validated = self._validate_record(self._row_dict(row))
                if self.legacy_reader is not None and validated["result"] is not None:
                    self._validate_live_legacy(validated["result"], self._read_legacy(issue))
                record = validated["record"]
                return {
                    "schemaVersion": "RECOVERY_INSPECTION_V1",
                    "issueDate": issue,
                    "phase": record["phase"],
                    "attemptsUsed": record["attempts_used"],
                    "bindingSha256": record["binding_sha256"],
                    "authoritySha256": record["authority_sha256"],
                    "budgetSha256": record["budget_sha256"],
                    "executionReceiptSha256": record["execution_receipt_sha256"],
                    "publicReceiptSha256": record["public_receipt_sha256"],
                    "resultSha256": record["result_sha256"],
                    "recordSha256": record["record_sha256"],
                    "legacyWriterCount": 0,
                }
            except RecoveryControlError:
                raise
            except (sqlite3.Error, OSError) as exc:
                raise RecoveryControlError(RECOVERY_LEDGER_CORRUPT, "recovery ledger cannot be inspected") from exc
            finally:
                if connection is not None:
                    connection.close()

    def audit(
        self,
        issue_date: str,
        parent: Mapping[str, Any],
        authority: Mapping[str, Any],
        budget: Mapping[str, Any],
        observed_at: datetime,
    ) -> dict[str, Any]:
        issue = _issue(issue_date)
        _aware_time(observed_at)
        parent_value = self._validate_parent(issue, parent)
        s1 = self._read_s1(issue, parent_value)
        authority_value = self._validate_authority(issue, parent_value, s1, authority)
        budget_value = self._validate_budget(authority_value, budget)
        binding = self._binding(issue, parent_value, authority_value, budget_value)
        legacy_value = self._read_legacy(issue)
        self._preflight_existing_record(issue, legacy_value)
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect_rw()
            validated = self._load_rw_record(connection, issue)
            if validated is not None:
                if self.legacy_reader is not None and validated["result"] is not None:
                    self._validate_live_legacy(validated["result"], legacy_value)
                stored_binding = validated["binding"]
                if stored_binding["recoveryId"] != binding["recoveryId"] or stored_binding["authoritySha256"] != binding["authoritySha256"] or stored_binding["parentSha256"] != binding["parentSha256"]:
                    raise RecoveryControlError(RECOVERY_AUTHORITY_CONFLICT, "recovery authority conflicts with durable record")
                record = validated["record"]
            else:
                with self._transaction(connection):
                    record = self._insert_record(connection, issue, binding, authority_value, budget_value)
                validated = self._validate_record(record)

            if validated["result"] is not None and record["phase"] == "COMMITTED":
                return self._stored_result(validated)

            binding_value = validated["binding"]
            execution = validated["execution"]
            public = validated["public"]
            history = validated["history"]
            record = dict(validated["record"])
            if execution is None:
                self._hook("before_execution_receipt")
                request = self._child_request(binding_value)
                execution = self._call_child(self.execution_reconciler, request, lineage="execution")
                with self._transaction(connection):
                    self._persist_receipt(connection, record, "execution", execution)
                validated = self._validate_record(record)
                record = dict(validated["record"])
                execution = validated["execution"]
                self._hook("after_execution_receipt")
            if record["phase"] == "EXECUTION_RESULT_DURABLE":
                with self._transaction(connection):
                    self._persist_phase(connection, record, "EXECUTION_COMMITTED")
                validated = self._validate_record(record)
                record = dict(validated["record"])
                execution = validated["execution"]
            if public is None:
                self._hook("before_public_receipt")
                request = self._child_request(binding_value, record["execution_receipt_sha256"])
                public = self._call_child(self.public_reconciler, request, lineage="public")
                with self._transaction(connection):
                    self._persist_receipt(connection, record, "public", public)
                validated = self._validate_record(record)
                record = dict(validated["record"])
                public = validated["public"]
                self._hook("after_public_receipt")
            if record["phase"] == "PUBLIC_RESULT_DURABLE":
                with self._transaction(connection):
                    self._persist_phase(connection, record, "PUBLIC_COMMITTED")
                validated = self._validate_record(record)
                record = dict(validated["record"])
                execution = validated["execution"]
                public = validated["public"]
            if record["phase"] == "PUBLIC_COMMITTED":
                history = [
                    {
                        "lineage": "Recovery",
                        "recoveryId": record["recovery_id"],
                        "executionReceiptSha256": record["execution_receipt_sha256"],
                        "publicReceiptSha256": record["public_receipt_sha256"],
                    }
                ]
                result = {
                    "schemaVersion": "RECOVERY_RECONCILE_RESULT_V1",
                    "issueDate": issue,
                    "recoveryId": record["recovery_id"],
                    "status": "CONFIRMED",
                    "attemptsUsed": 1,
                    "execution": execution,
                    "public": public,
                    "recoveryHistory": history,
                    "legacy": legacy_value,
                    "legacyWriterCount": 0,
                }
                with self._transaction(connection):
                    record["history_json"] = _canonical(history)
                    record["history_sha256"] = _sha(history)
                    record["result_json"] = _canonical(result)
                    record["result_sha256"] = _sha(result)
                    self._persist_phase(connection, record, "RESULT_DURABLE")
                validated = self._validate_record(record)
                record = dict(validated["record"])
                result = validated["result"]
                self._hook("before_recovery_commit")
            if record["phase"] == "RESULT_DURABLE":
                with self._transaction(connection):
                    self._persist_phase(connection, record, "COMMITTED")
                validated = self._validate_record(record)
                result = validated["result"]
                self._hook("after_recovery_commit")
            if result is None:
                validated = self._validate_record(record)
                result = validated["result"]
            if not isinstance(result, dict):
                raise RecoveryControlError(RECOVERY_LEDGER_CORRUPT, "recovery result is absent")
            return json.loads(_canonical(result))
        finally:
            if connection is not None:
                connection.close()
