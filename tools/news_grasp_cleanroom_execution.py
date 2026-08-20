"""S2 clean-room execution plane bound to the S1 slot fence."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid
from typing import Any, Callable, Mapping

from .news_grasp_cleanroom_contracts import (
    CleanroomEntryError,
    _entry_canonical_sha256,
    _managed_runtime_path,
    _validate_entry_time,
)
from .news_grasp_cleanroom_ledger import ControlLedger
from .news_grasp_cleanroom_wal import DurabilityOps, _fsync_real


AUTHORITY_INVALID = "NEWS_GRASP_EXECUTION_AUTHORITY_INVALID"
ADMISSION_DENIED = "NEWS_GRASP_EXECUTION_ADMISSION_DENIED"
ADMISSION_UNAVAILABLE = "NEWS_GRASP_EXECUTION_ADMISSION_UNAVAILABLE"
ADMISSION_INVALID = "NEWS_GRASP_EXECUTION_ADMISSION_INVALID"
CHECKPOINT_CORRUPT = "NEWS_GRASP_EXECUTION_CHECKPOINT_CORRUPT"
CHECKPOINT_MISSING = "NEWS_GRASP_EXECUTION_CHECKPOINT_MISSING"
CHILD_FAILED = "NEWS_GRASP_EXECUTION_CHILD_FAILED"
RESULT_UNKNOWN = "NEWS_GRASP_EXECUTION_RESULT_UNKNOWN"
STALE_FENCE = "NEWS_GRASP_EXECUTION_STALE_FENCE"
DURABILITY_FAILED = "NEWS_GRASP_EXECUTION_DURABILITY_FAILED"

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_ISSUE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SLOT_KEY = re.compile(r"^[^/]+/\d{4}-\d{2}-\d{2}/(Scheduled|Audit)$")
_AUTHORITY_KEYS = frozenset(
    {
        "schemaVersion",
        "authorityId",
        "scheduleId",
        "issueDate",
        "slotKey",
        "generation",
        "ownerKey",
        "fenceToken",
        "maxDispatchAttempts",
        "authoritySha256",
    }
)
_ADMISSION_KEYS = frozenset(
    {
        "schemaVersion",
        "status",
        "authorityId",
        "authoritySha256",
        "idempotencyKey",
        "decisionSha256",
    }
)
_RECEIPT_KEYS = frozenset(
    {
        "schemaVersion",
        "status",
        "idempotencyKey",
        "externalReceiptId",
        "effectHash",
    }
)
_STAGES = ("harvest", "model", "finalize")
_STATES = ("PENDING", "RESERVED", "INTENT_DURABLE", "DISPATCHED", "CONFIRMED", "COMMITTED")
_STATE_RANK = {state: index for index, state in enumerate(_STATES)}


class ExecutionError(Exception):
    """S2 の失敗を公開する typed error。"""

    def __init__(self, reason: str, message: str | None = None, *, stage: str | None = None) -> None:
        self.reason = reason
        self.stage = stage
        super().__init__(message or reason)


class ExternalResultUnknown(Exception):
    """provider が外部結果を確定できないことを示すシグナル。"""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _iso(value: datetime) -> str:
    return _validate_entry_time(value).isoformat()


def _parse_issue_date(value: Any) -> str:
    if not isinstance(value, str) or _ISSUE_DATE.fullmatch(value) is None:
        raise ExecutionError(AUTHORITY_INVALID, "issue date is invalid")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ExecutionError(AUTHORITY_INVALID, "issue date is invalid") from exc
    return value


def _checkpoint_input(stage: str, payload: Mapping[str, Any], outputs: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {"payload": dict(payload)}
    if stage in ("model", "finalize"):
        value["harvest"] = dict(outputs["harvest"])
    if stage == "finalize":
        value["model"] = dict(outputs["model"])
    return value


class ExecutionController:
    """durable admission, staged checkpoints and provider effect state machine."""

    def __init__(
        self,
        runtime_root: Path,
        admission_adapter: Callable[[dict[str, Any]], Mapping[str, Any]],
        provider: Any,
        stage_runner: Callable[[str, dict[str, Any]], Mapping[str, Any]],
        durability_ops: DurabilityOps | None = None,
        boundary_hook: Callable[[str], None] | None = None,
    ) -> None:
        self.runtime_root = Path(runtime_root)
        self.control_root = _managed_runtime_path(self.runtime_root, self.runtime_root / "control")
        self.execution_path = _managed_runtime_path(self.runtime_root, self.control_root / "execution-ledger-v1.sqlite3")
        self.checkpoint_root = _managed_runtime_path(self.runtime_root, self.control_root / "execution-checkpoints")
        self.admission_adapter = admission_adapter
        self.provider = provider
        self.stage_runner = stage_runner
        self.operations = durability_ops or DurabilityOps()
        self.boundary_hook = boundary_hook
        self.busy_timeout_ms = 1000

    def _hook(self, name: str) -> None:
        if self.boundary_hook is not None:
            self.boundary_hook(name)

    def _authority_shape(self, authority: Mapping[str, Any], slot_key: str, issue_date: str) -> dict[str, Any]:
        if not isinstance(authority, Mapping):
            raise ExecutionError(AUTHORITY_INVALID, "authority must be an object")
        value = dict(authority)
        if set(value) != _AUTHORITY_KEYS or value.get("schemaVersion") != "EXECUTION_AUTHORITY_V1":
            raise ExecutionError(AUTHORITY_INVALID, "authority schema is invalid")
        if not isinstance(value.get("authorityId"), str) or not value["authorityId"]:
            raise ExecutionError(AUTHORITY_INVALID, "authority id is invalid")
        if not isinstance(value.get("scheduleId"), str) or not value["scheduleId"]:
            raise ExecutionError(AUTHORITY_INVALID, "authority schedule is invalid")
        _parse_issue_date(value.get("issueDate"))
        if not isinstance(value.get("slotKey"), str) or _SLOT_KEY.fullmatch(value["slotKey"]) is None:
            raise ExecutionError(AUTHORITY_INVALID, "authority slot key is invalid")
        for field in ("generation", "fenceToken", "maxDispatchAttempts"):
            field_value = value.get(field)
            if isinstance(field_value, bool) or not isinstance(field_value, int) or field_value < 1:
                raise ExecutionError(AUTHORITY_INVALID, f"authority {field} is invalid")
        if not isinstance(value.get("ownerKey"), str) or not value["ownerKey"]:
            raise ExecutionError(AUTHORITY_INVALID, "authority owner is invalid")
        authority_hash = value.get("authoritySha256")
        if not isinstance(authority_hash, str) or _HEX64.fullmatch(authority_hash) is None:
            raise ExecutionError(AUTHORITY_INVALID, "authority hash is invalid")
        if authority_hash != _entry_canonical_sha256({key: item for key, item in value.items() if key != "authoritySha256"}):
            raise ExecutionError(AUTHORITY_INVALID, "authority hash drift")
        if value["issueDate"] != issue_date or value["slotKey"] != slot_key:
            raise ExecutionError(AUTHORITY_INVALID, "authority invocation binding is invalid")
        return value

    def _validate_s1_authority(self, authority: Mapping[str, Any], slot_key: str, issue_date: str, observed: datetime) -> None:
        ledger = ControlLedger(self.runtime_root, busy_timeout_ms=self.busy_timeout_ms)
        connection: sqlite3.Connection | None = None
        try:
            if not ledger.ledger_path.exists() or not ledger.generation_path.exists():
                raise ExecutionError(STALE_FENCE, "S1 ledger is absent")
            generation = int(ledger._read_generation_seal()["generation"])
            connection = ledger._connect()
            ledger._verify_connection(connection, generation)
            row = connection.execute(
                "SELECT schedule_id,issue_date,slot_kind,generation,state,owner_key,fence_token,lease_expires_at FROM slots WHERE schedule_id=? AND issue_date=? AND slot_kind=?",
                (authority["scheduleId"], issue_date, slot_key.rsplit("/", 1)[-1]),
            ).fetchone()
            if row is None or row[4] != "ACTIVE":
                raise ExecutionError(STALE_FENCE, "S1 slot is not active")
            if (
                row[0] != authority["scheduleId"]
                or row[1] != authority["issueDate"]
                or f"{row[0]}/{row[1]}/{row[2]}" != authority["slotKey"]
                or int(row[3]) != authority["generation"]
                or row[5] != authority["ownerKey"]
                or int(row[6]) != authority["fenceToken"]
            ):
                raise ExecutionError(STALE_FENCE, "S1 fence does not match authority")
            expiry_text = row[7]
            if not isinstance(expiry_text, str):
                raise ExecutionError(STALE_FENCE, "S1 lease is absent")
            try:
                expiry = datetime.fromisoformat(expiry_text)
            except ValueError as exc:
                raise ExecutionError(STALE_FENCE, "S1 lease is invalid") from exc
            if expiry.tzinfo is None or observed >= expiry:
                raise ExecutionError(STALE_FENCE, "S1 lease is expired")
        except ExecutionError:
            raise
        except (CleanroomEntryError, OSError, sqlite3.Error, TypeError, ValueError) as exc:
            raise ExecutionError(STALE_FENCE, "S1 authority cannot be verified") from exc
        finally:
            if connection is not None:
                connection.close()

    def _connect(self) -> sqlite3.Connection:
        self.control_root.mkdir(parents=True, exist_ok=True)
        try:
            connection = sqlite3.connect(
                self.execution_path,
                timeout=self.busy_timeout_ms / 1000,
                isolation_level=None,
                check_same_thread=False,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
            return connection
        except sqlite3.Error as exc:
            raise ExecutionError(DURABILITY_FAILED, "execution ledger cannot be opened") from exc

    def _durable_sync(self) -> None:
        try:
            with self.execution_path.open("r+b") as stream:
                try:
                    self.operations.fsync(stream.fileno())
                except OSError as exc:
                    if self.operations.fsync is os.fsync and os.name == "nt" and exc.errno == 9:
                        _fsync_real(stream.fileno())
                    else:
                        raise
            self.operations.flush_parent(self.control_root)
        except Exception as exc:
            if isinstance(exc, ExecutionError):
                raise
            raise ExecutionError(DURABILITY_FAILED, str(exc)) from exc

    @contextmanager
    def _transaction(self, connection: sqlite3.Connection):
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield
            connection.commit()
        except ExecutionError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise ExecutionError(DURABILITY_FAILED, "execution transaction failed") from exc
        except Exception:
            connection.rollback()
            raise

    def _schema(self, connection: sqlite3.Connection) -> None:
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS executions (
                    execution_id TEXT PRIMARY KEY,
                    issue_date TEXT NOT NULL,
                    slot_key TEXT NOT NULL,
                    authority_sha256 TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    authority_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    admission_json TEXT,
                    dispatch_attempts INTEGER NOT NULL DEFAULT 0,
                    confirmed_receipt_json TEXT,
                    completed_stages_json TEXT NOT NULL DEFAULT '[]',
                    checkpoints_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(issue_date, slot_key, authority_sha256, payload_sha256)
                );
                CREATE TABLE IF NOT EXISTS transitions (
                    execution_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('PENDING','RESERVED','INTENT_DURABLE','DISPATCHED','CONFIRMED','COMMITTED')),
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(execution_id, sequence),
                    UNIQUE(execution_id, state),
                    FOREIGN KEY(execution_id) REFERENCES executions(execution_id) ON DELETE CASCADE
                );
                """
            )
            self._durable_sync()
        except ExecutionError:
            raise
        except sqlite3.Error as exc:
            raise ExecutionError(DURABILITY_FAILED, "execution schema cannot be created") from exc

    def _row(self, connection: sqlite3.Connection, execution_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM executions WHERE execution_id=?", (execution_id,)).fetchone()
        if row is None:
            raise ExecutionError(DURABILITY_FAILED, "execution identity is missing")
        return row

    def _state(self, connection: sqlite3.Connection, execution_id: str) -> str:
        row = connection.execute("SELECT state FROM transitions WHERE execution_id=? ORDER BY sequence DESC LIMIT 1", (execution_id,)).fetchone()
        if row is None or row[0] not in _STATE_RANK:
            raise ExecutionError(DURABILITY_FAILED, "execution state is invalid")
        return str(row[0])

    def _ensure_execution(
        self,
        connection: sqlite3.Connection,
        *,
        execution_id: str,
        issue_date: str,
        slot_key: str,
        authority: Mapping[str, Any],
        payload: Mapping[str, Any],
        payload_hash: str,
        idempotency_key: str,
        observed: datetime,
    ) -> sqlite3.Row:
        existing = connection.execute("SELECT * FROM executions WHERE execution_id=?", (execution_id,)).fetchone()
        if existing is not None:
            if (
                existing["authority_sha256"] != authority["authoritySha256"]
                or existing["payload_sha256"] != payload_hash
                or existing["idempotency_key"] != idempotency_key
            ):
                raise ExecutionError(AUTHORITY_INVALID, "execution identity drift")
            return existing
        try:
            with self._transaction(connection):
                connection.execute(
                    "INSERT INTO executions(execution_id,issue_date,slot_key,authority_sha256,payload_sha256,idempotency_key,authority_json,payload_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        execution_id,
                        issue_date,
                        slot_key,
                        authority["authoritySha256"],
                        payload_hash,
                        idempotency_key,
                        _canonical_json(dict(authority)),
                        _canonical_json(dict(payload)),
                        _iso(observed),
                        _iso(observed),
                    ),
                )
                connection.execute(
                    "INSERT INTO transitions(execution_id,sequence,state,created_at) VALUES(?,?,?,?)",
                    (execution_id, 0, "PENDING", _iso(observed)),
                )
            self._durable_sync()
        except ExecutionError:
            raise
        except sqlite3.Error as exc:
            raise ExecutionError(DURABILITY_FAILED, "execution identity cannot be persisted") from exc
        return self._row(connection, execution_id)

    def _mutate(self, connection: sqlite3.Connection, callback: Callable[[], Any]) -> Any:
        try:
            with self._transaction(connection):
                result = callback()
            self._durable_sync()
            return result
        except ExecutionError:
            raise
        except sqlite3.Error as exc:
            raise ExecutionError(DURABILITY_FAILED, "execution mutation failed") from exc

    def _transition(
        self,
        connection: sqlite3.Connection,
        execution_id: str,
        target: str,
        observed: datetime,
        *,
        receipt: Mapping[str, Any] | None = None,
        dispatch_attempts: int | None = None,
        boundary: bool = True,
    ) -> None:
        current = self._state(connection, execution_id)
        if _STATE_RANK[current] >= _STATE_RANK[target]:
            return
        if _STATE_RANK[target] != _STATE_RANK[current] + 1:
            raise ExecutionError(DURABILITY_FAILED, "execution transition order is invalid")
        if boundary and target in {"INTENT_DURABLE", "DISPATCHED", "CONFIRMED", "COMMITTED"}:
            self._hook(f"before_{target}")

        def mutate() -> None:
            fields: list[str] = ["updated_at=?"]
            values: list[Any] = [_iso(observed)]
            if dispatch_attempts is not None:
                fields.append("dispatch_attempts=?")
                values.append(dispatch_attempts)
            if receipt is not None:
                fields.append("confirmed_receipt_json=?")
                values.append(_canonical_json(dict(receipt)))
            values.append(execution_id)
            connection.execute(f"UPDATE executions SET {','.join(fields)} WHERE execution_id=?", tuple(values))
            connection.execute(
                "INSERT INTO transitions(execution_id,sequence,state,created_at) VALUES(?,?,?,?)",
                (execution_id, _STATE_RANK[target], target, _iso(observed)),
            )

        self._mutate(connection, mutate)
        if boundary and target in {"INTENT_DURABLE", "DISPATCHED", "CONFIRMED", "COMMITTED"}:
            self._hook(f"after_{target}")

    def _increment_dispatch_attempts(self, connection: sqlite3.Connection, execution_id: str, observed: datetime, attempts: int) -> None:
        self._mutate(
            connection,
            lambda: connection.execute(
                "UPDATE executions SET dispatch_attempts=?,updated_at=? WHERE execution_id=?",
                (attempts, _iso(observed), execution_id),
            ),
        )

    def _admission(self, connection: sqlite3.Connection, row: sqlite3.Row, authority: Mapping[str, Any], payload: Mapping[str, Any], observed: datetime) -> sqlite3.Row:
        if row["admission_json"]:
            try:
                decision = json.loads(row["admission_json"])
            except (TypeError, UnicodeError, json.JSONDecodeError) as exc:
                raise ExecutionError(ADMISSION_INVALID, "persisted admission is invalid") from exc
            if not isinstance(decision, dict):
                raise ExecutionError(ADMISSION_INVALID, "persisted admission is invalid")
            status = decision.get("status")
            if status == "DENIED":
                raise ExecutionError(ADMISSION_DENIED, "high-cost admission denied")
            if status == "UNAVAILABLE":
                raise ExecutionError(ADMISSION_UNAVAILABLE, "high-cost admission unavailable")
            if status == "GRANTED":
                return row
            raise ExecutionError(ADMISSION_INVALID, "persisted admission status is invalid")
        request = {
            "schemaVersion": "HIGH_COST_ADMISSION_REQUEST_V1",
            "executionId": row["execution_id"],
            "authority": dict(authority),
            "idempotencyKey": row["idempotency_key"],
            "issueDate": row["issue_date"],
            "slotKey": row["slot_key"],
            "payload": dict(payload),
        }
        try:
            raw_decision = self.admission_adapter(request)
        except Exception as exc:
            raise ExecutionError(ADMISSION_UNAVAILABLE, "high-cost admission failed") from exc
        if not isinstance(raw_decision, Mapping):
            raise ExecutionError(ADMISSION_INVALID, "admission decision is not an object")
        decision = dict(raw_decision)
        if set(decision) != _ADMISSION_KEYS or decision.get("schemaVersion") != "HIGH_COST_ADMISSION_DECISION_V1":
            raise ExecutionError(ADMISSION_INVALID, "admission decision schema is invalid")
        if decision.get("status") not in {"GRANTED", "DENIED", "UNAVAILABLE"}:
            raise ExecutionError(ADMISSION_INVALID, "admission decision status is invalid")
        if decision.get("authorityId") != authority["authorityId"] or decision.get("authoritySha256") != authority["authoritySha256"] or decision.get("idempotencyKey") != row["idempotency_key"]:
            raise ExecutionError(ADMISSION_INVALID, "admission decision binding is invalid")
        decision_hash = decision.get("decisionSha256")
        if not isinstance(decision_hash, str) or _HEX64.fullmatch(decision_hash) is None or decision_hash != _entry_canonical_sha256({key: item for key, item in decision.items() if key != "decisionSha256"}):
            raise ExecutionError(ADMISSION_INVALID, "admission decision hash is invalid")
        self._mutate(
            connection,
            lambda: connection.execute(
                "UPDATE executions SET admission_json=?,updated_at=? WHERE execution_id=?",
                (_canonical_json(decision), _iso(observed), row["execution_id"]),
            ),
        )
        if decision["status"] == "DENIED":
            raise ExecutionError(ADMISSION_DENIED, "high-cost admission denied")
        if decision["status"] == "UNAVAILABLE":
            raise ExecutionError(ADMISSION_UNAVAILABLE, "high-cost admission unavailable")
        return self._row(connection, row["execution_id"])

    def _checkpoint_path(self, execution_id: str, stage: str) -> Path:
        return _managed_runtime_path(self.runtime_root, self.checkpoint_root / execution_id / f"{stage}.json")

    def _checkpoint_hash(self, value: Mapping[str, Any]) -> str:
        return _entry_canonical_sha256({key: item for key, item in value.items() if key != "checkpointSha256"})

    def _read_checkpoint(self, path: Path, execution_id: str, stage: str, input_payload: Mapping[str, Any]) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ExecutionError(CHECKPOINT_CORRUPT, f"checkpoint is unreadable: {stage}") from exc
        if not isinstance(value, dict) or set(value) != {"schemaVersion", "executionId", "stage", "inputHash", "outputHash", "output", "checkpointSha256"}:
            raise ExecutionError(CHECKPOINT_CORRUPT, f"checkpoint schema is invalid: {stage}")
        if value.get("schemaVersion") != "EXECUTION_CHECKPOINT_V1" or value.get("executionId") != execution_id or value.get("stage") != stage:
            raise ExecutionError(CHECKPOINT_CORRUPT, f"checkpoint binding is invalid: {stage}")
        expected_input_hash = _entry_canonical_sha256(dict(input_payload))
        if value.get("inputHash") != expected_input_hash:
            raise ExecutionError(CHECKPOINT_CORRUPT, f"checkpoint input is invalid: {stage}")
        output = value.get("output")
        if not isinstance(output, dict) or not isinstance(output.get("outputHash"), str) or _HEX64.fullmatch(output["outputHash"]) is None:
            raise ExecutionError(CHECKPOINT_CORRUPT, f"checkpoint output is invalid: {stage}")
        expected_output_hash = _entry_canonical_sha256({"stage": stage, "input": dict(input_payload)})
        if value.get("outputHash") != expected_output_hash or output["outputHash"] != expected_output_hash:
            raise ExecutionError(CHECKPOINT_CORRUPT, f"checkpoint output hash is invalid: {stage}")
        checkpoint_hash = value.get("checkpointSha256")
        if not isinstance(checkpoint_hash, str) or _HEX64.fullmatch(checkpoint_hash) is None or checkpoint_hash != self._checkpoint_hash(value):
            raise ExecutionError(CHECKPOINT_CORRUPT, f"checkpoint hash is invalid: {stage}")
        return value

    def _write_checkpoint(self, path: Path, value: Mapping[str, Any]) -> dict[str, Any]:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            return json.loads(_canonical_json(self._read_checkpoint(path, value["executionId"], value["stage"], value["inputPayload"])))
        public_value = {key: item for key, item in value.items() if key != "inputPayload"}
        public_value = dict(public_value)
        public_value["checkpointSha256"] = self._checkpoint_hash(public_value)
        temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temp.write_text(_canonical_json(public_value), encoding="utf-8")
            with temp.open("r+b") as stream:
                try:
                    self.operations.fsync(stream.fileno())
                except OSError as exc:
                    if self.operations.fsync is os.fsync and os.name == "nt" and exc.errno == 9:
                        _fsync_real(stream.fileno())
                    else:
                        raise
            self.operations.replace(temp, path)
            self.operations.flush_parent(path.parent)
            return public_value
        except ExecutionError:
            raise
        except Exception as exc:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            raise ExecutionError(DURABILITY_FAILED, str(exc)) from exc

    def _checkpoint_binding(self, connection: sqlite3.Connection, row: sqlite3.Row, stage: str, checkpoint: Mapping[str, Any], observed: datetime) -> sqlite3.Row:
        try:
            completed = json.loads(row["completed_stages_json"])
            bindings = json.loads(row["checkpoints_json"])
        except (TypeError, UnicodeError, json.JSONDecodeError) as exc:
            raise ExecutionError(CHECKPOINT_CORRUPT, "checkpoint binding is invalid") from exc
        if not isinstance(completed, list) or not isinstance(bindings, dict):
            raise ExecutionError(CHECKPOINT_CORRUPT, "checkpoint binding is invalid")
        if stage not in completed:
            completed.append(stage)
            expected_prefix = list(_STAGES[: len(completed)])
            if completed != expected_prefix:
                raise ExecutionError(CHECKPOINT_CORRUPT, "completed stages are out of order")
        if bindings.get(stage) not in (None, checkpoint["checkpointSha256"]):
            raise ExecutionError(CHECKPOINT_CORRUPT, "checkpoint binding hash drift")
        bindings[stage] = checkpoint["checkpointSha256"]
        self._mutate(
            connection,
            lambda: connection.execute(
                "UPDATE executions SET completed_stages_json=?,checkpoints_json=?,updated_at=? WHERE execution_id=?",
                (_canonical_json(completed), _canonical_json(bindings), _iso(observed), row["execution_id"]),
            ),
        )
        return self._row(connection, row["execution_id"])

    def _completed(self, connection: sqlite3.Connection, row: sqlite3.Row, payload: Mapping[str, Any]) -> tuple[list[str], dict[str, dict[str, Any]]]:
        try:
            completed = json.loads(row["completed_stages_json"])
            bindings = json.loads(row["checkpoints_json"])
        except (TypeError, UnicodeError, json.JSONDecodeError) as exc:
            raise ExecutionError(CHECKPOINT_CORRUPT, "completed stages are invalid") from exc
        if not isinstance(completed, list) or not isinstance(bindings, dict) or completed != list(_STAGES[: len(completed)]) or any(stage not in _STAGES for stage in completed):
            raise ExecutionError(CHECKPOINT_CORRUPT, "completed stages are invalid")
        outputs: dict[str, dict[str, Any]] = {}
        for stage in completed:
            input_payload = _checkpoint_input(stage, payload, outputs)
            path = self._checkpoint_path(row["execution_id"], stage)
            if not path.exists():
                raise ExecutionError(CHECKPOINT_MISSING, f"checkpoint is missing: {stage}")
            checkpoint = self._read_checkpoint(path, row["execution_id"], stage, input_payload)
            if bindings.get(stage) != checkpoint["checkpointSha256"]:
                raise ExecutionError(CHECKPOINT_CORRUPT, f"checkpoint binding is invalid: {stage}")
            outputs[stage] = dict(checkpoint["output"])
        return completed, outputs

    def _run_stage(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        stage: str,
        input_payload: Mapping[str, Any],
        observed: datetime,
        *,
        persist_checkpoint: bool,
    ) -> tuple[sqlite3.Row, dict[str, Any]]:
        path = self._checkpoint_path(row["execution_id"], stage)
        if path.exists():
            checkpoint = self._read_checkpoint(path, row["execution_id"], stage, input_payload)
            row = self._checkpoint_binding(connection, row, stage, checkpoint, observed)
            return row, dict(checkpoint["output"])
        try:
            output = self.stage_runner(stage, dict(input_payload))
        except Exception as exc:
            raise ExecutionError(CHILD_FAILED, f"stage failed: {stage}", stage=stage) from exc
        if not isinstance(output, Mapping):
            raise ExecutionError(CHILD_FAILED, f"stage output is invalid: {stage}", stage=stage)
        output_value = dict(output)
        expected_output_hash = _entry_canonical_sha256({"stage": stage, "input": dict(input_payload)})
        if output_value.get("outputHash") != expected_output_hash:
            raise ExecutionError(CHILD_FAILED, f"stage output hash is invalid: {stage}", stage=stage)
        if not persist_checkpoint:
            return row, output_value
        checkpoint_value = {
            "schemaVersion": "EXECUTION_CHECKPOINT_V1",
            "executionId": row["execution_id"],
            "stage": stage,
            "inputHash": _entry_canonical_sha256(dict(input_payload)),
            "outputHash": expected_output_hash,
            "output": output_value,
        }
        checkpoint = self._write_checkpoint(path, {**checkpoint_value, "inputPayload": dict(input_payload)})
        row = self._checkpoint_binding(connection, row, stage, checkpoint, observed)
        return row, output_value

    def _receipt(self, value: Any, idempotency_key: str) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise ExecutionError(RESULT_UNKNOWN, "external receipt is invalid")
        receipt = dict(value)
        if set(receipt) != _RECEIPT_KEYS or receipt.get("schemaVersion") != "EXTERNAL_RESULT_RECEIPT_V1" or receipt.get("status") != "CONFIRMED":
            raise ExecutionError(RESULT_UNKNOWN, "external receipt schema is invalid")
        if receipt.get("idempotencyKey") != idempotency_key or not isinstance(receipt.get("externalReceiptId"), str) or not receipt["externalReceiptId"]:
            raise ExecutionError(RESULT_UNKNOWN, "external receipt binding is invalid")
        if not isinstance(receipt.get("effectHash"), str) or _HEX64.fullmatch(receipt["effectHash"]) is None:
            raise ExecutionError(RESULT_UNKNOWN, "external effect hash is invalid")
        return receipt

    def _dispatch_or_query(self, connection: sqlite3.Connection, row: sqlite3.Row, observed: datetime, authority: Mapping[str, Any], payload: Mapping[str, Any]) -> sqlite3.Row:
        state = self._state(connection, row["execution_id"])
        if state == "INTENT_DURABLE":
            attempts = int(row["dispatch_attempts"]) + 1
            if attempts > authority["maxDispatchAttempts"]:
                raise ExecutionError(RESULT_UNKNOWN, "dispatch attempts exhausted")
            self._transition(connection, row["execution_id"], "DISPATCHED", observed, dispatch_attempts=attempts)
            row = self._row(connection, row["execution_id"])
            try:
                receipt = self.provider.dispatch({"idempotencyKey": row["idempotency_key"], "authority": dict(authority), "payload": dict(payload)})
            except ExternalResultUnknown as exc:
                raise ExecutionError(RESULT_UNKNOWN, "external dispatch result is unknown") from exc
            except Exception as exc:
                raise ExecutionError(RESULT_UNKNOWN, "external dispatch result is unknown") from exc
            return self._confirm(connection, row, observed, receipt)
        if state != "DISPATCHED":
            return row
        try:
            outcome = self.provider.query(row["idempotency_key"])
        except ExternalResultUnknown as exc:
            raise ExecutionError(RESULT_UNKNOWN, "external query result is unknown") from exc
        except Exception as exc:
            raise ExecutionError(RESULT_UNKNOWN, "external query result is unknown") from exc
        if not isinstance(outcome, Mapping):
            raise ExecutionError(RESULT_UNKNOWN, "external query result is invalid")
        status = outcome.get("status")
        if status == "PRESENT":
            return self._confirm(connection, row, observed, outcome.get("receipt"))
        if status in {"AMBIGUOUS", "UNAVAILABLE"}:
            raise ExecutionError(RESULT_UNKNOWN, "external query did not resolve the effect")
        if status != "ABSENT":
            raise ExecutionError(RESULT_UNKNOWN, "external query status is invalid")
        attempts = int(row["dispatch_attempts"])
        if attempts >= authority["maxDispatchAttempts"]:
            raise ExecutionError(RESULT_UNKNOWN, "dispatch attempts exhausted")
        attempts += 1
        self._increment_dispatch_attempts(connection, row["execution_id"], observed, attempts)
        row = self._row(connection, row["execution_id"])
        try:
            receipt = self.provider.dispatch({"idempotencyKey": row["idempotency_key"], "authority": dict(authority), "payload": dict(payload)})
        except ExternalResultUnknown as exc:
            raise ExecutionError(RESULT_UNKNOWN, "external dispatch result is unknown") from exc
        except Exception as exc:
            raise ExecutionError(RESULT_UNKNOWN, "external dispatch result is unknown") from exc
        return self._confirm(connection, row, observed, receipt)

    def _confirm(self, connection: sqlite3.Connection, row: sqlite3.Row, observed: datetime, receipt: Any) -> sqlite3.Row:
        value = self._receipt(receipt, row["idempotency_key"])
        self._transition(connection, row["execution_id"], "CONFIRMED", observed, receipt=value)
        return self._row(connection, row["execution_id"])

    def _projection(self, connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        try:
            completed = json.loads(row["completed_stages_json"])
            checkpoint_hashes = json.loads(row["checkpoints_json"])
            receipt = json.loads(row["confirmed_receipt_json"]) if row["confirmed_receipt_json"] else None
        except (TypeError, UnicodeError, json.JSONDecodeError) as exc:
            raise ExecutionError(DURABILITY_FAILED, "terminal projection is invalid") from exc
        return {
            "schemaVersion": "RECONCILE_EXECUTION_RESULT_V1",
            "status": "committed",
            "executionId": row["execution_id"],
            "issueDate": row["issue_date"],
            "slotKey": row["slot_key"],
            "authoritySha256": row["authority_sha256"],
            "idempotencyKey": row["idempotency_key"],
            "externalState": self._state(connection, row["execution_id"]),
            "completedStages": completed,
            "checkpointHashes": checkpoint_hashes,
            "externalReceipt": receipt,
            "dispatchAttempts": int(row["dispatch_attempts"]),
        }

    def execute(
        self,
        slot_key: str,
        issue_date: str,
        authority: Mapping[str, Any],
        payload: Mapping[str, Any],
        observed_at: datetime,
    ) -> dict[str, Any]:
        try:
            observed = _validate_entry_time(observed_at)
        except (CleanroomEntryError, TypeError, ValueError) as exc:
            raise ExecutionError(AUTHORITY_INVALID, "observed_at is invalid") from exc
        if not isinstance(slot_key, str) or _SLOT_KEY.fullmatch(slot_key) is None:
            raise ExecutionError(AUTHORITY_INVALID, "slot key is invalid")
        issue_date = _parse_issue_date(issue_date)
        authority_value = self._authority_shape(authority, slot_key, issue_date)
        self._validate_s1_authority(authority_value, slot_key, issue_date, observed)
        if not isinstance(payload, Mapping):
            raise ExecutionError(AUTHORITY_INVALID, "payload must be an object")
        payload_value = dict(payload)
        try:
            payload_hash = _entry_canonical_sha256(payload_value)
        except Exception as exc:
            raise ExecutionError(AUTHORITY_INVALID, "payload is not canonical JSON") from exc
        identity = {
            "issueDate": issue_date,
            "slotKey": slot_key,
            "authoritySha256": authority_value["authoritySha256"],
            "payloadSha256": payload_hash,
        }
        execution_id = _entry_canonical_sha256(identity)
        idempotency_key = _entry_canonical_sha256({"schemaVersion": "EXECUTION_IDEMPOTENCY_V1", "executionId": execution_id})
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            self._schema(connection)
            row = self._ensure_execution(
                connection,
                execution_id=execution_id,
                issue_date=issue_date,
                slot_key=slot_key,
                authority=authority_value,
                payload=payload_value,
                payload_hash=payload_hash,
                idempotency_key=idempotency_key,
                observed=observed,
            )
            if self._state(connection, execution_id) == "COMMITTED":
                return self._projection(connection, row)
            row = self._admission(connection, row, authority_value, payload_value, observed)
            if self._state(connection, execution_id) == "PENDING":
                self._transition(connection, execution_id, "RESERVED", observed, boundary=False)
                row = self._row(connection, execution_id)
            completed, outputs = self._completed(connection, row, payload_value)
            if "harvest" not in completed:
                harvest_input = _checkpoint_input("harvest", payload_value, outputs)
                row, harvest = self._run_stage(connection, row, "harvest", harvest_input, observed, persist_checkpoint=True)
                outputs["harvest"] = harvest
                completed, outputs = self._completed(connection, row, payload_value)
            model_output: dict[str, Any] | None = outputs.get("model")
            if "model" not in completed:
                model_input = _checkpoint_input("model", payload_value, outputs)
                row, model_output = self._run_stage(connection, row, "model", model_input, observed, persist_checkpoint=False)
            state = self._state(connection, execution_id)
            if state == "RESERVED":
                self._transition(connection, execution_id, "INTENT_DURABLE", observed)
                row = self._row(connection, execution_id)
            row = self._dispatch_or_query(connection, row, observed, authority_value, payload_value)
            state = self._state(connection, execution_id)
            if state != "CONFIRMED":
                raise ExecutionError(DURABILITY_FAILED, "execution did not reach CONFIRMED")
            completed, outputs = self._completed(connection, row, payload_value)
            if "model" not in completed:
                if model_output is None:
                    model_input = _checkpoint_input("model", payload_value, outputs)
                    row, model_output = self._run_stage(connection, row, "model", model_input, observed, persist_checkpoint=False)
                model_input = _checkpoint_input("model", payload_value, outputs)
                checkpoint_value = {
                    "schemaVersion": "EXECUTION_CHECKPOINT_V1",
                    "executionId": row["execution_id"],
                    "stage": "model",
                    "inputHash": _entry_canonical_sha256(dict(model_input)),
                    "outputHash": _entry_canonical_sha256({"stage": "model", "input": dict(model_input)}),
                    "output": model_output,
                }
                checkpoint = self._write_checkpoint(self._checkpoint_path(row["execution_id"], "model"), {**checkpoint_value, "inputPayload": dict(model_input)})
                row = self._checkpoint_binding(connection, row, "model", checkpoint, observed)
                outputs["model"] = dict(model_output)
            completed, outputs = self._completed(connection, row, payload_value)
            if "finalize" not in completed:
                finalize_input = _checkpoint_input("finalize", payload_value, outputs)
                row, _ = self._run_stage(connection, row, "finalize", finalize_input, observed, persist_checkpoint=True)
            self._validate_s1_authority(authority_value, slot_key, issue_date, observed)
            self._transition(connection, execution_id, "COMMITTED", observed)
            return self._projection(connection, self._row(connection, execution_id))
        finally:
            if connection is not None:
                connection.close()
