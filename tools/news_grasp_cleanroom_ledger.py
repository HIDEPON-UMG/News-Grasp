"""S1 SQLite control ledger と resumable recovery。"""

from __future__ import annotations

from datetime import datetime, timedelta
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

from .news_grasp_cleanroom_contracts import (
    CleanroomEntryError,
    _entry_canonical_sha256,
    _ENTRY_SCHEDULE_ID,
    ENTRY_CLOCK_ROLLBACK,
    _managed_runtime_path,
    _validate_busy_timeout,
    _validate_entry_time,
    _validate_entry_writer,
    _writer_owner_key,
    reconcile_slot,
)
from .news_grasp_cleanroom_wal import DurabilityOps, _fsync_real


LEDGER_BUSY = "NEWS_GRASP_ENTRY_LEDGER_BUSY"
LEDGER_CORRUPT = "NEWS_GRASP_ENTRY_LEDGER_CORRUPT"
STALE_FENCE = "NEWS_GRASP_ENTRY_STALE_FENCE"
TERMINAL_CONFLICT = "NEWS_GRASP_ENTRY_TERMINAL_CONFLICT"
COMMIT_INVALID = "NEWS_GRASP_ENTRY_COMMIT_INVALID"
RECOVERY_FAILED = "NEWS_GRASP_ENTRY_LEDGER_RECOVERY_FAILED"
RECOVERY_NOT_REQUIRED = "NEWS_GRASP_ENTRY_RECOVERY_NOT_REQUIRED"
_ZERO_HASH = "0" * 64
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_RECOVERY_ID = re.compile(r"^[0-9a-f]{32}$")
_RECOVERY_PHASES = {"PREPARED", "QUARANTINED", "SEALED", "LEDGER_CREATED", "COMMITTED"}
_RECOVERY_KEYS = frozenset({"schemaVersion", "recoveryId", "oldGeneration", "newGeneration", "quarantineRelativePath", "phase", "updatedAt", "journalSha256"})
_SLOT_KEY = re.compile(r"^news-grasp-daily-v1/(\d{4}-\d{2}-\d{2})/(Scheduled|Audit)$")
_TERMINAL_STATES = {"SUCCEEDED", "FAILED", "MISSED_SCHEDULED"}
_BOOTSTRAP_LOCK_NAME = "bootstrap-v1.lock"


def _iso(value: datetime) -> str:
    return _validate_entry_time(value).isoformat()


def _durable_write(path: Path, payload: Mapping[str, Any], operations: DurabilityOps, reason: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        with temp.open("r+b") as stream:
            try:
                operations.fsync(stream.fileno())
            except OSError as exc:
                if operations.fsync is os.fsync and os.name == "nt" and exc.errno == 9:
                    _fsync_real(stream.fileno())
                else:
                    raise
        operations.replace(temp, path)
        with path.open("r+b") as stream:
            try:
                operations.fsync(stream.fileno())
            except OSError as exc:
                if operations.fsync is os.fsync and os.name == "nt" and exc.errno == 9:
                    _fsync_real(stream.fileno())
                else:
                    raise
        operations.flush_parent(path.parent)
    except Exception as exc:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        if isinstance(exc, CleanroomEntryError):
            raise
        raise CleanroomEntryError(reason, str(exc)) from exc


def _event_hash(payload: Mapping[str, Any]) -> str:
    return _entry_canonical_sha256(payload)


class ControlLedger:
    """BEGIN IMMEDIATE と event hash chain を共有する制御台帳。"""

    def __init__(
        self,
        runtime_root: Path,
        *,
        busy_timeout_ms: int = 1000,
        boundary_hook: Callable[[str], None] | None = None,
    ):
        self.busy_timeout_ms = _validate_busy_timeout(busy_timeout_ms)
        self.runtime_root = Path(runtime_root)
        self.control_root = _managed_runtime_path(self.runtime_root, self.runtime_root / "control")
        self.ledger_path = _managed_runtime_path(self.runtime_root, self.control_root / "control-ledger-v1.sqlite3")
        self.generation_path = _managed_runtime_path(self.runtime_root, self.control_root / "generation-seal-v1.json")
        self.recovery_path = _managed_runtime_path(self.runtime_root, self.control_root / "recovery-journal-v1.json")
        self.bootstrap_lock_path = _managed_runtime_path(self.runtime_root, self.control_root / _BOOTSTRAP_LOCK_NAME)
        self.boundary_hook = boundary_hook

    def _managed(self, path: Path) -> Path:
        return _managed_runtime_path(self.runtime_root, path)

    def _hook(self, name: str) -> None:
        if self.boundary_hook is not None:
            self.boundary_hook(name)

    def _read_generation_seal(self) -> dict[str, Any]:
        try:
            seal = json.loads(self.generation_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CleanroomEntryError(LEDGER_CORRUPT, "generation seal is unreadable") from exc
        if not isinstance(seal, dict) or seal.get("schemaVersion") != "CONTROL_LEDGER_GENERATION_SEAL_V1":
            raise CleanroomEntryError(LEDGER_CORRUPT, "generation seal schema is invalid")
        unsigned = {key: value for key, value in seal.items() if key != "sealSha256"}
        if seal.get("sealSha256") != _entry_canonical_sha256(unsigned):
            raise CleanroomEntryError(LEDGER_CORRUPT, "generation seal hash drift")
        if isinstance(seal.get("generation"), bool) or not isinstance(seal.get("generation"), int) or seal["generation"] < 1:
            raise CleanroomEntryError(LEDGER_CORRUPT, "generation seal value is invalid")
        return seal

    def _write_generation_seal(self, generation: int, previous: str, created_at: datetime, operations: DurabilityOps) -> dict[str, Any]:
        seal: dict[str, Any] = {
            "schemaVersion": "CONTROL_LEDGER_GENERATION_SEAL_V1",
            "generation": generation,
            "ledgerRelativePath": "control/control-ledger-v1.sqlite3",
            "previousSealSha256": previous,
            "createdAt": _iso(created_at),
        }
        seal["sealSha256"] = _entry_canonical_sha256(seal)
        _durable_write(self.generation_path, seal, operations, RECOVERY_FAILED)
        return seal

    def _connect(self) -> sqlite3.Connection:
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                self.ledger_path,
                timeout=max(0.001, self.busy_timeout_ms / 1000),
                isolation_level=None,
                check_same_thread=False,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute(f"PRAGMA busy_timeout={int(self.busy_timeout_ms)}")
            return connection
        except sqlite3.Error as exc:
            if connection is not None:
                try:
                    connection.close()
                except sqlite3.Error:
                    pass
            raise CleanroomEntryError(LEDGER_CORRUPT, "SQLite ledger cannot be opened") from exc

    @contextmanager
    def _bootstrap_lock(self):
        """プロセス間で初期generation/SQLite生成を一度だけ行う。"""
        self.control_root.mkdir(parents=True, exist_ok=True)
        stream = self.bootstrap_lock_path.open("a+b")
        locked = False
        deadline = time.monotonic() + max(0, self.busy_timeout_ms) / 1000
        try:
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"\x00")
                stream.flush()
            while True:
                stream.seek(0)
                try:
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                    break
                except (OSError, BlockingIOError):
                    if time.monotonic() >= deadline:
                        raise CleanroomEntryError(LEDGER_BUSY, "bootstrap lock is busy")
                    time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
            yield
        finally:
            if locked:
                try:
                    stream.seek(0)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
            stream.close()

    def _materialized_state_hash(self, connection: sqlite3.Connection) -> str:
        invocations = [
            tuple(row)
            for row in connection.execute(
                "SELECT invocation_id,received_at,raw_argv_sha256,writer_key,wal_event_sha256,imported_at,status FROM invocations ORDER BY invocation_id"
            ).fetchall()
        ]
        slots = [
            tuple(row)
            for row in connection.execute(
                "SELECT schedule_id,issue_date,slot_kind,generation,state,owner_key,fence_token,lease_expires_at,terminal_state,result_hash,updated_at FROM slots ORDER BY schedule_id,issue_date,slot_kind"
            ).fetchall()
        ]
        metadata_row = connection.execute("SELECT value FROM metadata WHERE key='lastObservedAt'").fetchone()
        last_observed_at = metadata_row[0] if metadata_row is not None else ""
        return _entry_canonical_sha256({"invocations": invocations, "slots": slots, "lastObservedAt": last_observed_at})

    def _update_materialized_state(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            "UPDATE metadata SET value=? WHERE key='materializedStateSha256'",
            (self._materialized_state_hash(connection),),
        )

    def _read_last_observed_from_connection(self, connection: sqlite3.Connection) -> datetime | None:
        value = connection.execute("SELECT value FROM metadata WHERE key='lastObservedAt'").fetchone()
        if value is None or not value[0]:
            return None
        try:
            persisted = datetime.fromisoformat(value[0])
            if persisted.tzinfo is None or persisted.utcoffset() != timedelta(hours=9) or persisted.fold != 0:
                raise ValueError("persisted lastObservedAt timezone is invalid")
            return _validate_entry_time(persisted.astimezone(ZoneInfo("Asia/Tokyo")))
        except (TypeError, ValueError, CleanroomEntryError) as exc:
            raise CleanroomEntryError(LEDGER_CORRUPT, "lastObservedAt is invalid") from exc

    def _validate_recovery_journal(self, journal: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(journal, dict) or set(journal) != _RECOVERY_KEYS:
            raise CleanroomEntryError(RECOVERY_FAILED, "recovery journal keys are invalid")
        if journal.get("schemaVersion") != "CONTROL_LEDGER_RECOVERY_JOURNAL_V1":
            raise CleanroomEntryError(RECOVERY_FAILED, "recovery journal schema is invalid")
        if not isinstance(journal.get("recoveryId"), str) or _RECOVERY_ID.fullmatch(journal["recoveryId"]) is None:
            raise CleanroomEntryError(RECOVERY_FAILED, "recovery id is invalid")
        old_generation = journal.get("oldGeneration")
        new_generation = journal.get("newGeneration")
        if isinstance(old_generation, bool) or not isinstance(old_generation, int) or old_generation < 1:
            raise CleanroomEntryError(RECOVERY_FAILED, "old generation is invalid")
        if isinstance(new_generation, bool) or not isinstance(new_generation, int) or new_generation != old_generation + 1:
            raise CleanroomEntryError(RECOVERY_FAILED, "new generation is invalid")
        expected_quarantine = f"control/quarantine/g{old_generation:08d}-to-g{new_generation:08d}-{journal['recoveryId']}"
        if journal.get("quarantineRelativePath") != expected_quarantine:
            raise CleanroomEntryError(RECOVERY_FAILED, "quarantine path is invalid")
        if not isinstance(journal.get("phase"), str) or journal["phase"] not in _RECOVERY_PHASES:
            raise CleanroomEntryError(RECOVERY_FAILED, "recovery phase is invalid")
        if not isinstance(journal.get("updatedAt"), str):
            raise CleanroomEntryError(RECOVERY_FAILED, "recovery updatedAt is invalid")
        try:
            updated_at = datetime.fromisoformat(journal["updatedAt"])
            if updated_at.tzinfo is None or updated_at.utcoffset() != timedelta(hours=9) or updated_at.fold != 0:
                raise ValueError("recovery updatedAt timezone is invalid")
            _validate_entry_time(updated_at.astimezone(ZoneInfo("Asia/Tokyo")))
        except (TypeError, ValueError, CleanroomEntryError) as exc:
            raise CleanroomEntryError(RECOVERY_FAILED, "recovery updatedAt is invalid") from exc
        journal_hash = journal.get("journalSha256")
        if not isinstance(journal_hash, str) or not _HEX64.fullmatch(journal_hash) or journal_hash != _entry_canonical_sha256({key: value for key, value in journal.items() if key != "journalSha256"}):
            raise CleanroomEntryError(RECOVERY_FAILED, "recovery journal hash is invalid")
        return dict(journal)

    def _create_schema(self, connection: sqlite3.Connection, generation: int) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS invocations (
                invocation_id TEXT PRIMARY KEY,
                received_at TEXT NOT NULL,
                raw_argv_sha256 TEXT NOT NULL,
                writer_key TEXT NOT NULL,
                wal_event_sha256 TEXT NOT NULL UNIQUE,
                imported_at TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('CURRENT','RECOVERED_ZERO_ENTRY'))
            );
            CREATE TABLE IF NOT EXISTS slots (
                schedule_id TEXT NOT NULL,
                issue_date TEXT NOT NULL,
                slot_kind TEXT NOT NULL CHECK(slot_kind IN ('Scheduled','Audit')),
                generation INTEGER NOT NULL CHECK(generation >= 1),
                state TEXT NOT NULL CHECK(state IN ('ACTIVE','TERMINAL')),
                owner_key TEXT NOT NULL,
                fence_token INTEGER NOT NULL CHECK(fence_token >= 1),
                lease_expires_at TEXT,
                terminal_state TEXT CHECK(terminal_state IN ('SUCCEEDED','FAILED','MISSED_SCHEDULED')),
                result_hash TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(schedule_id, issue_date, slot_kind)
            );
            CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER PRIMARY KEY,
                generation INTEGER NOT NULL CHECK(generation >= 1),
                event_type TEXT NOT NULL,
                slot_key TEXT,
                payload_json TEXT NOT NULL,
                previous_event_sha256 TEXT NOT NULL,
                event_sha256 TEXT NOT NULL UNIQUE
            );
            """
        )
        existing = dict(connection.execute("SELECT key,value FROM metadata").fetchall())
        values = {
            "schemaVersion": "CONTROL_LEDGER_SQLITE_V1",
            "generation": str(generation),
            "lastObservedAt": existing.get("lastObservedAt", ""),
            "eventChainHead": existing.get("eventChainHead", _ZERO_HASH),
            "materializedStateSha256": existing.get("materializedStateSha256", ""),
        }
        for key, value in values.items():
            connection.execute("INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        if not existing.get("materializedStateSha256"):
            self._update_materialized_state(connection)

    def _ensure_initialized(self, observed_at: datetime) -> int:
        with self._bootstrap_lock():
            if not self.generation_path.exists():
                self._write_generation_seal(1, _ZERO_HASH, observed_at, DurabilityOps())
            seal = self._read_generation_seal()
            if not self.ledger_path.exists():
                connection = sqlite3.connect(self.ledger_path, isolation_level=None)
                try:
                    self._create_schema(connection, int(seal["generation"]))
                finally:
                    connection.close()
            return int(seal["generation"])

    def _verify_connection(self, connection: sqlite3.Connection, generation: int | None = None) -> int:
        try:
            metadata = dict(connection.execute("SELECT key,value FROM metadata").fetchall())
            if metadata.get("schemaVersion") != "CONTROL_LEDGER_SQLITE_V1":
                raise ValueError("metadata schema")
            current_generation = int(metadata.get("generation", "0"))
            if current_generation < 1 or generation is not None and current_generation != generation:
                raise ValueError("metadata generation")
            if not _HEX64.fullmatch(metadata.get("eventChainHead", "")):
                raise ValueError("metadata event head")
            rows = connection.execute("SELECT sequence,generation,event_type,slot_key,payload_json,previous_event_sha256,event_sha256 FROM events ORDER BY sequence").fetchall()
            previous = _ZERO_HASH
            for expected_sequence, row in enumerate(rows, start=1):
                if row["sequence"] != expected_sequence or row["generation"] != current_generation or row["previous_event_sha256"] != previous:
                    raise ValueError("event chain sequence")
                payload = json.loads(row["payload_json"])
                unsigned = {
                    "sequence": row["sequence"],
                    "generation": row["generation"],
                    "eventType": row["event_type"],
                    "slotKey": row["slot_key"],
                    "payload": payload,
                    "previousEventSha256": row["previous_event_sha256"],
                }
                digest = _event_hash(unsigned)
                if digest != row["event_sha256"]:
                    raise ValueError("event hash")
                previous = digest
            if metadata["eventChainHead"] != previous:
                raise ValueError("event head")
            materialized = metadata.get("materializedStateSha256", "")
            if not _HEX64.fullmatch(materialized) or materialized != self._materialized_state_hash(connection):
                raise ValueError("materialized state")
            return current_generation
        except (sqlite3.Error, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise CleanroomEntryError(LEDGER_CORRUPT, "SQLite ledger integrity verification failed") from exc

    def _append_event(self, connection: sqlite3.Connection, generation: int, event_type: str, slot_key: str | None, payload: Mapping[str, Any]) -> str:
        row = connection.execute("SELECT COALESCE(MAX(sequence),0) FROM events").fetchone()
        sequence = int(row[0]) + 1
        previous = connection.execute("SELECT value FROM metadata WHERE key='eventChainHead'").fetchone()[0]
        unsigned = {
            "sequence": sequence,
            "generation": generation,
            "eventType": event_type,
            "slotKey": slot_key,
            "payload": dict(payload),
            "previousEventSha256": previous,
        }
        digest = _event_hash(unsigned)
        connection.execute(
            "INSERT INTO events(sequence,generation,event_type,slot_key,payload_json,previous_event_sha256,event_sha256) VALUES(?,?,?,?,?,?,?)",
            (sequence, generation, event_type, slot_key, json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")), previous, digest),
        )
        connection.execute("UPDATE metadata SET value=? WHERE key='eventChainHead'", (digest,))
        return digest

    def _import_invocation(self, connection: sqlite3.Connection, invocation_event: Mapping[str, Any], observed_at: datetime, status: str = "CURRENT") -> bool:
        invocation_id = str(invocation_event["invocationId"])
        existing = connection.execute("SELECT invocation_id FROM invocations WHERE invocation_id=?", (invocation_id,)).fetchone()
        if existing is not None:
            return False
        received_at = str(invocation_event["receivedAt"])
        writer_key = _writer_owner_key(invocation_event["writer"])
        connection.execute(
            "INSERT INTO invocations(invocation_id,received_at,raw_argv_sha256,writer_key,wal_event_sha256,imported_at,status) VALUES(?,?,?,?,?,?,?)",
            (invocation_id, received_at, invocation_event["rawArgvSha256"], writer_key, invocation_event["eventSha256"], _iso(observed_at), status),
        )
        self._append_event(connection, int(connection.execute("SELECT value FROM metadata WHERE key='generation'").fetchone()[0]), "INVOCATION_IMPORTED", None, dict(invocation_event))
        return True

    def import_zero_entries(self, zero_entries: tuple[Mapping[str, Any], ...], *, observed_at: datetime) -> None:
        if not zero_entries:
            return
        observed = _validate_entry_time(observed_at)
        generation = self._ensure_initialized(observed)
        connection = self._connect()
        try:
            try:
                connection.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc:
                raise CleanroomEntryError(LEDGER_BUSY, "SQLite ledger is busy") from exc
            self._verify_connection(connection, generation)
            persisted = self._read_last_observed_from_connection(connection)
            if persisted is not None and observed < persisted:
                raise CleanroomEntryError(ENTRY_CLOCK_ROLLBACK, "observed_at precedes persisted lastObservedAt")
            for event in zero_entries:
                self._import_invocation(connection, event, observed, status="RECOVERED_ZERO_ENTRY")
            connection.execute("UPDATE metadata SET value=? WHERE key='lastObservedAt'", (_iso(observed),))
            self._update_materialized_state(connection)
            connection.commit()
        except CleanroomEntryError:
            connection.rollback()
            raise
        except sqlite3.OperationalError as exc:
            connection.rollback()
            raise CleanroomEntryError(LEDGER_BUSY, "SQLite ledger is busy") from exc
        finally:
            connection.close()

    def last_observed_at(self) -> datetime | None:
        """現在の lastObservedAt を読み取り、未作成台帳では None を返す。"""
        if not self.ledger_path.exists():
            return None
        generation = int(self._read_generation_seal()["generation"])
        connection = self._connect()
        try:
            self._verify_connection(connection, generation)
            value = connection.execute("SELECT value FROM metadata WHERE key='lastObservedAt'").fetchone()
            if value is None or not value[0]:
                return None
            try:
                persisted = datetime.fromisoformat(value[0])
                if persisted.tzinfo is None or persisted.utcoffset() != timedelta(hours=9) or persisted.fold != 0:
                    raise ValueError("persisted lastObservedAt timezone is invalid")
                return _validate_entry_time(persisted.astimezone(ZoneInfo("Asia/Tokyo")))
            except (TypeError, ValueError) as exc:
                raise CleanroomEntryError(LEDGER_CORRUPT, "lastObservedAt is invalid") from exc
        finally:
            connection.close()

    def scheduled_state(self, issue_date: str) -> str:
        """対象 issue date の Scheduled slot 状態を投影する。"""
        if not isinstance(issue_date, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", issue_date) is None:
            raise CleanroomEntryError(LEDGER_CORRUPT, "issue date is invalid")
        if not self.ledger_path.exists():
            return "ABSENT"
        generation = int(self._read_generation_seal()["generation"])
        connection = self._connect()
        try:
            self._verify_connection(connection, generation)
            row = connection.execute(
                "SELECT state,terminal_state FROM slots WHERE schedule_id=? AND issue_date=? AND slot_kind='Scheduled'",
                (_ENTRY_SCHEDULE_ID, issue_date),
            ).fetchone()
            if row is None:
                return "ABSENT"
            if row["state"] == "ACTIVE":
                return "ACTIVE"
            return str(row["terminal_state"] or "TERMINAL")
        finally:
            connection.close()

    def _slot_row(self, connection: sqlite3.Connection, slot_key: str) -> sqlite3.Row | None:
        match = _SLOT_KEY.fullmatch(slot_key)
        if match is None:
            return None
        issue_date, kind = match.groups()
        return connection.execute("SELECT * FROM slots WHERE schedule_id=? AND issue_date=? AND slot_kind=?", (_ENTRY_SCHEDULE_ID, issue_date, kind)).fetchone()

    def _decision_from_connection(
        self,
        connection: sqlite3.Connection,
        *,
        manifest: Mapping[str, Any],
        observed_at: datetime,
    ) -> dict[str, Any]:
        metadata_row = connection.execute("SELECT value FROM metadata WHERE key='lastObservedAt'").fetchone()
        persisted_value = metadata_row[0] if metadata_row is not None else ""
        if not persisted_value:
            last_observed_at = None
        else:
            try:
                persisted = datetime.fromisoformat(persisted_value)
                if persisted.tzinfo is None or persisted.utcoffset() != timedelta(hours=9) or persisted.fold != 0:
                    raise ValueError("persisted lastObservedAt timezone is invalid")
                last_observed_at = _validate_entry_time(persisted.astimezone(ZoneInfo("Asia/Tokyo")))
            except (TypeError, ValueError) as exc:
                raise CleanroomEntryError(LEDGER_CORRUPT, "lastObservedAt is invalid") from exc
        issue_date = observed_at.date().isoformat()
        scheduled_row = connection.execute(
            "SELECT state,terminal_state FROM slots WHERE schedule_id=? AND issue_date=? AND slot_kind='Scheduled'",
            (_ENTRY_SCHEDULE_ID, issue_date),
        ).fetchone()
        if scheduled_row is None:
            scheduled_state = "ABSENT"
        elif scheduled_row["state"] == "ACTIVE":
            scheduled_state = "ACTIVE"
        else:
            scheduled_state = str(scheduled_row["terminal_state"] or "TERMINAL")
        return reconcile_slot(
            manifest=manifest,
            observed_at=observed_at,
            last_observed_at=last_observed_at,
            scheduled_state=scheduled_state,
        )

    def _acquire_or_attach(self, connection: sqlite3.Connection, slot_key: str, writer: Mapping[str, Any], lease_seconds: int, observed_at: datetime) -> tuple[sqlite3.Row | dict[str, Any], str, bool]:
        match = _SLOT_KEY.fullmatch(slot_key)
        if match is None:
            raise CleanroomEntryError(COMMIT_INVALID, "slot key is invalid")
        issue_date, slot_kind = match.groups()
        owner_key = _writer_owner_key(writer)
        row = self._slot_row(connection, slot_key)
        if row is None:
            expiry = observed_at + timedelta(seconds=lease_seconds)
            connection.execute(
                "INSERT INTO slots(schedule_id,issue_date,slot_kind,generation,state,owner_key,fence_token,lease_expires_at,terminal_state,result_hash,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (_ENTRY_SCHEDULE_ID, issue_date, slot_kind, int(connection.execute("SELECT value FROM metadata WHERE key='generation'").fetchone()[0]), "ACTIVE", owner_key, 1, expiry.isoformat(), None, None, _iso(observed_at)),
            )
            self._hook("after_slot_insert")
            return {"ownerKey": owner_key, "fenceToken": 1, "leaseExpiresAt": expiry.isoformat(), "slotState": "ACTIVE", "slotTerminalState": None}, "ACQUIRED", True
        if row["state"] == "TERMINAL":
            return {"ownerKey": row["owner_key"], "fenceToken": row["fence_token"], "leaseExpiresAt": None, "slotState": "TERMINAL", "slotTerminalState": row["terminal_state"]}, "TERMINAL_NOOP", False
        expiry_text = row["lease_expires_at"]
        expiry = datetime.fromisoformat(expiry_text) if expiry_text else observed_at
        if observed_at >= expiry:
            new_fence = int(row["fence_token"]) + 1
            new_expiry = observed_at + timedelta(seconds=lease_seconds)
            connection.execute("UPDATE slots SET owner_key=?,fence_token=?,lease_expires_at=?,updated_at=? WHERE schedule_id=? AND issue_date=? AND slot_kind=?", (owner_key, new_fence, new_expiry.isoformat(), _iso(observed_at), _ENTRY_SCHEDULE_ID, issue_date, slot_kind))
            self._hook("after_lease_update")
            return {"ownerKey": owner_key, "fenceToken": new_fence, "leaseExpiresAt": new_expiry.isoformat(), "slotState": "ACTIVE", "slotTerminalState": None}, "ACQUIRED", True
        return {"ownerKey": row["owner_key"], "fenceToken": row["fence_token"], "leaseExpiresAt": row["lease_expires_at"], "slotState": "ACTIVE", "slotTerminalState": None}, "ATTACHED", False

    def reconcile(
        self,
        *,
        invocation_event: Mapping[str, Any],
        manifest: Mapping[str, Any],
        writer: Mapping[str, Any],
        lease_seconds: int,
        observed_at: datetime,
    ) -> dict[str, Any]:
        generation = self._ensure_initialized(observed_at)
        connection = self._connect()
        committed = False
        try:
            try:
                connection.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc:
                raise CleanroomEntryError(LEDGER_BUSY, "SQLite ledger is busy") from exc
            self._hook("after_ledger_begin")
            self._verify_connection(connection, generation)
            decision = self._decision_from_connection(connection, manifest=manifest, observed_at=observed_at)
            imported = self._import_invocation(connection, invocation_event, observed_at)
            if imported:
                self._hook("after_invocation_import")
            decision_name = decision["decision"]
            scheduled_state = decision["scheduledState"]
            target_slot_kind: str | None = None
            slot_key: str | None = None
            owner_projection: dict[str, Any] = {"ownerKey": None, "fenceToken": None, "leaseExpiresAt": None, "slotState": None, "slotTerminalState": None}
            owner_disposition = "NONE"
            accepted = False
            issue_date = decision["issueDate"]
            if decision_name != "NOT_DUE":
                target_slot_kind = "Scheduled" if decision_name == "ENSURE_SCHEDULED" else "Audit"
                slot_key = f"{decision['scheduleId']}/{issue_date}/{target_slot_kind}"
                if decision_name == "MISSED_SCHEDULED_AND_ENSURE_AUDIT" and self._slot_row(connection, f"{decision['scheduleId']}/{issue_date}/Scheduled") is None:
                    missed_hash = _entry_canonical_sha256({"scheduleId": decision["scheduleId"], "issueDate": issue_date, "terminalState": "MISSED_SCHEDULED"})
                    connection.execute("INSERT INTO slots(schedule_id,issue_date,slot_kind,generation,state,owner_key,fence_token,lease_expires_at,terminal_state,result_hash,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (decision["scheduleId"], issue_date, "Scheduled", generation, "TERMINAL", "system:reconcile", 1, None, "MISSED_SCHEDULED", missed_hash, _iso(observed_at)))
                    self._hook("after_slot_insert")
                    accepted = True
                owner_projection, owner_disposition, owner_changed = self._acquire_or_attach(connection, slot_key, writer, lease_seconds, observed_at)
                accepted = accepted or owner_changed
                self._append_event(connection, generation, "SLOT_DECISION", slot_key, {"decision": decision_name, "ownerDisposition": owner_disposition, "ownerKey": owner_projection["ownerKey"], "fenceToken": owner_projection["fenceToken"], "scheduledState": scheduled_state})
            connection.execute("UPDATE metadata SET value=? WHERE key='lastObservedAt'", (_iso(observed_at),))
            self._update_materialized_state(connection)
            self._hook("before_ledger_commit")
            connection.commit()
            committed = True
            self._hook("after_ledger_commit")
            return {
                "schemaVersion": "DISPATCH_DECISION_V1",
                "status": "accepted" if accepted else "noop",
                "decision": decision_name,
                "issueDate": issue_date,
                "scheduleId": decision["scheduleId"],
                "slotKind": target_slot_kind,
                "slotKey": slot_key,
                "slotState": owner_projection["slotState"],
                "slotTerminalState": owner_projection["slotTerminalState"],
                "generation": generation,
                "ownerDisposition": owner_disposition,
                "ownerKey": owner_projection["ownerKey"],
                "fenceToken": owner_projection["fenceToken"],
                "leaseExpiresAt": owner_projection["leaseExpiresAt"],
                "scheduledState": scheduled_state,
                "externalEffectCount": 0,
                "invocationId": invocation_event["invocationId"],
                "walEventSha256": invocation_event["eventSha256"],
            }
        except CleanroomEntryError:
            if not committed:
                connection.rollback()
            raise
        except sqlite3.OperationalError as exc:
            if not committed:
                connection.rollback()
            raise CleanroomEntryError(LEDGER_BUSY, "SQLite ledger operation is busy") from exc
        except Exception:
            if not committed:
                connection.rollback()
            raise
        finally:
            connection.close()

    def commit_slot(
        self,
        *,
        slot_key: str,
        writer: Mapping[str, Any],
        fence_token: int,
        terminal_state: str,
        result_hash: str,
        observed_at: datetime,
    ) -> dict[str, Any]:
        observed = _validate_entry_time(observed_at)
        try:
            writer_value, owner_key = _validate_entry_writer(writer)
        except CleanroomEntryError:
            raise CleanroomEntryError(COMMIT_INVALID, "writer is invalid for terminal commit")
        if not isinstance(slot_key, str) or _SLOT_KEY.fullmatch(slot_key) is None or isinstance(fence_token, bool) or not isinstance(fence_token, int) or fence_token < 1 or terminal_state not in {"SUCCEEDED", "FAILED"} or not isinstance(result_hash, str) or not _HEX64.fullmatch(result_hash):
            raise CleanroomEntryError(COMMIT_INVALID, "terminal commit payload is invalid")
        generation = self._ensure_initialized(observed)
        connection = self._connect()
        committed = False
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._verify_connection(connection, generation)
            row = self._slot_row(connection, slot_key)
            if row is None:
                raise CleanroomEntryError(COMMIT_INVALID, "slot does not exist")
            if row["state"] == "TERMINAL":
                if row["terminal_state"] == terminal_state and row["result_hash"] == result_hash:
                    return {"schemaVersion": "SLOT_COMMIT_RESULT_V1", "status": "noop", "slotKey": slot_key, "generation": row["generation"], "fenceToken": row["fence_token"], "terminalState": row["terminal_state"], "resultHash": row["result_hash"], "externalEffectCount": 0}
                raise CleanroomEntryError(TERMINAL_CONFLICT, "terminal payload conflicts")
            if row["owner_key"] != owner_key or row["fence_token"] != fence_token:
                raise CleanroomEntryError(STALE_FENCE, "writer or fence is stale")
            self._hook("before_terminal_commit")
            connection.execute("UPDATE slots SET state='TERMINAL',lease_expires_at=NULL,terminal_state=?,result_hash=?,updated_at=? WHERE schedule_id=? AND issue_date=? AND slot_kind=?", (terminal_state, result_hash, _iso(observed), _ENTRY_SCHEDULE_ID, slot_key.split("/")[1], slot_key.split("/")[2]))
            self._append_event(connection, generation, "SLOT_TERMINAL", slot_key, {"terminalState": terminal_state, "resultHash": result_hash, "ownerKey": owner_key, "fenceToken": fence_token})
            self._update_materialized_state(connection)
            connection.commit()
            committed = True
            self._hook("after_terminal_commit")
            return {"schemaVersion": "SLOT_COMMIT_RESULT_V1", "status": "committed", "slotKey": slot_key, "generation": generation, "fenceToken": fence_token, "terminalState": terminal_state, "resultHash": result_hash, "externalEffectCount": 0}
        except CleanroomEntryError:
            if not committed:
                connection.rollback()
            raise
        except sqlite3.OperationalError as exc:
            if not committed:
                connection.rollback()
            raise CleanroomEntryError(LEDGER_BUSY, "SQLite ledger operation is busy") from exc
        finally:
            connection.close()

    def inspect(self) -> dict[str, Any]:
        generation = self._read_generation_seal()["generation"] if self.generation_path.exists() else 1
        connection = self._connect()
        try:
            generation = self._verify_connection(connection, int(generation))
            invocations = [dict(row) for row in connection.execute("SELECT invocation_id,received_at,raw_argv_sha256,writer_key,wal_event_sha256,imported_at,status FROM invocations ORDER BY invocation_id")]
            slots = [dict(row) for row in connection.execute("SELECT schedule_id,issue_date,slot_kind,generation,state,owner_key,fence_token,lease_expires_at,terminal_state,result_hash,updated_at FROM slots ORDER BY schedule_id,issue_date,slot_kind")]
            metadata = dict(connection.execute("SELECT key,value FROM metadata").fetchall())
            return {"schemaVersion": "CONTROL_STATE_SNAPSHOT_V1", "generation": generation, "lastObservedAt": metadata.get("lastObservedAt", ""), "eventChainHead": metadata.get("eventChainHead", _ZERO_HASH), "invocations": [{"invocationId": row.pop("invocation_id"), "receivedAt": row.pop("received_at"), "rawArgvSha256": row.pop("raw_argv_sha256"), "writerKey": row.pop("writer_key"), "walEventSha256": row.pop("wal_event_sha256"), "importedAt": row.pop("imported_at"), "status": row.pop("status")} for row in invocations], "slots": [{"scheduleId": row.pop("schedule_id"), "issueDate": row.pop("issue_date"), "slotKind": row.pop("slot_kind"), "generation": row.pop("generation"), "state": row.pop("state"), "ownerKey": row.pop("owner_key"), "fenceToken": row.pop("fence_token"), "leaseExpiresAt": row.pop("lease_expires_at"), "terminalState": row.pop("terminal_state"), "resultHash": row.pop("result_hash"), "updatedAt": row.pop("updated_at")} for row in slots], "zeroEntryWal": [], "integrityStatus": "green"}
        finally:
            connection.close()

    def verify(self) -> None:
        generation = int(self._read_generation_seal()["generation"])
        connection = self._connect()
        try:
            self._verify_connection(connection, generation)
        finally:
            connection.close()

    def _recovery_genesis_matches(self, recovery_id: str, old_generation: int, new_generation: int) -> bool:
        if not self.ledger_path.exists():
            return False
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            self._verify_connection(connection, new_generation)
            rows = connection.execute(
                "SELECT sequence,generation,event_type,slot_key,payload_json,previous_event_sha256,event_sha256 FROM events ORDER BY sequence"
            ).fetchall()
            if len(rows) != 1:
                return False
            row = rows[0]
            payload = json.loads(row["payload_json"])
            expected_payload = {"recoveryId": recovery_id, "oldGeneration": old_generation, "newGeneration": new_generation}
            return (
                row["sequence"] == 1
                and row["generation"] == new_generation
                and row["event_type"] == "LEDGER_RECOVERED"
                and row["slot_key"] is None
                and payload == expected_payload
                and row["previous_event_sha256"] == _ZERO_HASH
            )
        except (CleanroomEntryError, OSError, sqlite3.Error, TypeError, ValueError, json.JSONDecodeError):
            return False
        finally:
            if connection is not None:
                connection.close()

    def _quarantine_partial_ledger(self, quarantine_path: Path, operations: DurabilityOps) -> None:
        token = uuid.uuid4().hex
        for source in (
            self.ledger_path,
            self.ledger_path.with_name(self.ledger_path.name + "-wal"),
            self.ledger_path.with_name(self.ledger_path.name + "-shm"),
        ):
            source = self._managed(source)
            if not source.exists():
                continue
            target = self._managed(quarantine_path / f"partial-{token}-{source.name}")
            operations.replace(source, target)
            operations.flush_parent(source.parent)
            operations.flush_parent(target.parent)

    def recover(self, *, observed_at: datetime, durability_ops: DurabilityOps | None = None) -> dict[str, Any]:
        observed = _validate_entry_time(observed_at)
        operations = durability_ops or DurabilityOps()
        hook_failure: Exception | None = None

        def recovery_hook(name: str) -> None:
            nonlocal hook_failure
            try:
                self._hook(name)
            except Exception as exc:
                hook_failure = exc
                raise

        healthy = False
        try:
            self.verify()
        except CleanroomEntryError as exc:
            if exc.reason != LEDGER_CORRUPT:
                raise
        else:
            healthy = True
        seal = self._read_generation_seal()
        journal: dict[str, Any]
        if self.recovery_path.exists():
            try:
                journal = self._validate_recovery_journal(json.loads(self.recovery_path.read_text(encoding="utf-8")))
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError, CleanroomEntryError) as exc:
                if isinstance(exc, CleanroomEntryError) and exc.reason == RECOVERY_FAILED:
                    raise
                raise CleanroomEntryError(RECOVERY_FAILED, "recovery journal is invalid") from exc
            if journal.get("phase") == "COMMITTED" and healthy:
                return {
                    "schemaVersion": "CONTROL_LEDGER_RECOVERY_V1",
                    "status": "RECOVERY_NOT_REQUIRED",
                    "oldGeneration": journal["oldGeneration"],
                    "newGeneration": journal["newGeneration"],
                    "recoveryId": journal["recoveryId"],
                    "quarantinePath": journal["quarantineRelativePath"],
                    "resumed": True,
                    "externalEffectCount": 0,
                }
            if journal.get("phase") == "COMMITTED":
                history_path = self._managed(self.control_root / "recovery-history" / f"{journal['recoveryId']}.json")
                history_path.parent.mkdir(parents=True, exist_ok=True)
                if history_path.exists():
                    try:
                        history = json.loads(history_path.read_text(encoding="utf-8"))
                    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                        raise CleanroomEntryError(RECOVERY_FAILED, "recovery history is invalid") from exc
                    if history != journal:
                        raise CleanroomEntryError(RECOVERY_FAILED, "recovery history conflicts")
                else:
                    _durable_write(history_path, journal, operations, RECOVERY_FAILED)
                old_generation = int(seal["generation"])
                recovery_id = uuid.uuid4().hex
                new_generation = old_generation + 1
                quarantine = f"control/quarantine/g{old_generation:08d}-to-g{new_generation:08d}-{recovery_id}"
                journal = {
                    "schemaVersion": "CONTROL_LEDGER_RECOVERY_JOURNAL_V1",
                    "recoveryId": recovery_id,
                    "oldGeneration": old_generation,
                    "newGeneration": new_generation,
                    "quarantineRelativePath": quarantine,
                    "phase": "PREPARED",
                    "updatedAt": _iso(observed),
                }
                journal["journalSha256"] = _entry_canonical_sha256(journal)
                journal = self._validate_recovery_journal(journal)
                _durable_write(self.recovery_path, journal, operations, RECOVERY_FAILED)
                recovery_hook("after_recovery_journal_prepared")
        else:
            if healthy:
                raise CleanroomEntryError(RECOVERY_NOT_REQUIRED, "ledger is healthy")
            self.control_root.mkdir(parents=True, exist_ok=True)
            old_generation = int(seal["generation"])
            recovery_id = uuid.uuid4().hex
            new_generation = old_generation + 1
            quarantine = f"control/quarantine/g{old_generation:08d}-to-g{new_generation:08d}-{recovery_id}"
            journal = {"schemaVersion": "CONTROL_LEDGER_RECOVERY_JOURNAL_V1", "recoveryId": recovery_id, "oldGeneration": old_generation, "newGeneration": new_generation, "quarantineRelativePath": quarantine, "phase": "PREPARED", "updatedAt": _iso(observed)}
            journal["journalSha256"] = _entry_canonical_sha256(journal)
            journal = self._validate_recovery_journal(journal)
            _durable_write(self.recovery_path, journal, operations, RECOVERY_FAILED)
            recovery_hook("after_recovery_journal_prepared")
        old_generation = int(journal["oldGeneration"])
        recovery_id = journal["recoveryId"]
        new_generation = int(journal["newGeneration"])
        quarantine_path = self._managed(self.runtime_root / journal["quarantineRelativePath"])
        phase = journal["phase"]
        try:
            if phase == "PREPARED":
                quarantine_path.mkdir(parents=True, exist_ok=True)
                for source in (self.ledger_path, self.ledger_path.with_name(self.ledger_path.name + "-wal"), self.ledger_path.with_name(self.ledger_path.name + "-shm")):
                    source = self._managed(source)
                    if source.exists():
                        target = self._managed(quarantine_path / source.name)
                        operations.replace(source, target)
                        operations.flush_parent(source.parent)
                        operations.flush_parent(target.parent)
                journal["phase"] = "QUARANTINED"
                journal["updatedAt"] = _iso(observed)
                journal["journalSha256"] = _entry_canonical_sha256({key: value for key, value in journal.items() if key != "journalSha256"})
                _durable_write(self.recovery_path, journal, operations, RECOVERY_FAILED)
                recovery_hook("after_recovery_quarantined")
                phase = "QUARANTINED"
            if phase == "QUARANTINED":
                current_seal = self._read_generation_seal()
                if current_seal["generation"] == new_generation:
                    previous_seal = current_seal.get("previousSealSha256")
                    if not isinstance(previous_seal, str) or not _HEX64.fullmatch(previous_seal) or previous_seal == current_seal.get("sealSha256"):
                        raise CleanroomEntryError(RECOVERY_FAILED, "recovery seal linkage is invalid")
                    seal = current_seal
                elif current_seal["generation"] == old_generation:
                    seal = self._write_generation_seal(new_generation, current_seal["sealSha256"], observed, operations)
                else:
                    raise CleanroomEntryError(RECOVERY_FAILED, "recovery generation is not resumable")
                journal["phase"] = "SEALED"
                journal["updatedAt"] = _iso(observed)
                journal["journalSha256"] = _entry_canonical_sha256({key: value for key, value in journal.items() if key != "journalSha256"})
                _durable_write(self.recovery_path, journal, operations, RECOVERY_FAILED)
                recovery_hook("after_recovery_generation_sealed")
                phase = "SEALED"
            if phase == "SEALED":
                if not self._recovery_genesis_matches(recovery_id, old_generation, new_generation):
                    self._quarantine_partial_ledger(quarantine_path, operations)
                    connection = sqlite3.connect(self.ledger_path, isolation_level=None)
                    try:
                        self._create_schema(connection, new_generation)
                        event = {"sequence": 1, "generation": new_generation, "eventType": "LEDGER_RECOVERED", "slotKey": None, "payload": {"recoveryId": recovery_id, "oldGeneration": old_generation, "newGeneration": new_generation}, "previousEventSha256": _ZERO_HASH}
                        digest = _event_hash(event)
                        connection.execute("INSERT INTO events(sequence,generation,event_type,slot_key,payload_json,previous_event_sha256,event_sha256) VALUES(?,?,?,?,?,?,?)", (1, new_generation, "LEDGER_RECOVERED", None, json.dumps(event["payload"], sort_keys=True, separators=(",", ":")), _ZERO_HASH, digest))
                        connection.execute("UPDATE metadata SET value=? WHERE key='eventChainHead'", (digest,))
                        self._update_materialized_state(connection)
                        connection.execute("PRAGMA journal_mode=WAL")
                        connection.execute("PRAGMA synchronous=FULL")
                        connection.execute("PRAGMA wal_checkpoint(FULL)")
                    finally:
                        connection.close()
                self.verify()
                journal["phase"] = "LEDGER_CREATED"
                journal["updatedAt"] = _iso(observed)
                journal["journalSha256"] = _entry_canonical_sha256({key: value for key, value in journal.items() if key != "journalSha256"})
                _durable_write(self.recovery_path, journal, operations, RECOVERY_FAILED)
                recovery_hook("after_recovery_ledger_created")
                phase = "LEDGER_CREATED"
            if phase == "LEDGER_CREATED":
                journal["phase"] = "COMMITTED"
                journal["updatedAt"] = _iso(observed)
                journal["journalSha256"] = _entry_canonical_sha256({key: value for key, value in journal.items() if key != "journalSha256"})
                _durable_write(self.recovery_path, journal, operations, RECOVERY_FAILED)
                recovery_hook("after_recovery_committed")
            return {"schemaVersion": "CONTROL_LEDGER_RECOVERY_V1", "status": "recovered", "oldGeneration": old_generation, "newGeneration": new_generation, "recoveryId": recovery_id, "quarantinePath": journal["quarantineRelativePath"], "resumed": phase != "PREPARED", "externalEffectCount": 0}
        except CleanroomEntryError:
            raise
        except Exception as exc:
            if exc is hook_failure:
                raise
            raise CleanroomEntryError(RECOVERY_FAILED, str(exc)) from exc
