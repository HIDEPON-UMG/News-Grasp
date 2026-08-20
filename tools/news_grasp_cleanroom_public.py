"""S3 clean-room public publication and notification plane."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Callable, Mapping

from .news_grasp_cleanroom_contracts import (
    _entry_canonical_sha256,
    _managed_runtime_path,
    _validate_entry_time,
)


PUBLIC_INVENTORY_INVALID = "PUBLIC_INVENTORY_INVALID"
PUBLIC_INCOMPLETE = "PUBLIC_INCOMPLETE"
MANUAL_RECONCILIATION_REQUIRED = "MANUAL_RECONCILIATION_REQUIRED"
PUBLIC_LINEAGE_CONFLICT = "PUBLIC_LINEAGE_CONFLICT"
PUBLIC_RECEIPT_INVALID = "PUBLIC_RECEIPT_INVALID"
PUBLIC_NOTIFICATION_INVALID = "PUBLIC_NOTIFICATION_INVALID"
PUBLIC_LEDGER_CORRUPT = "PUBLIC_LEDGER_CORRUPT"

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_ISSUE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_INVENTORY_KEYS = frozenset(
    {
        "schemaVersion",
        "issueDate",
        "requiredSurfaceIds",
        "eligibleNotRequiredSurfaceIds",
        "surfaces",
        "inventorySha256",
    }
)
_SURFACE_KEYS = frozenset({"surfaceId", "status", "artifactSha256"})
_SURFACE_STATUSES = {"PENDING", "CONFIRMED", "NOT_REQUIRED", "FAILED", "UNKNOWN"}
_LINEAGES = ("Scheduled", "Recovery", "Public", "Readiness")
_SURFACE_RECEIPT_KEYS = frozenset(
    {"schemaVersion", "idempotencyKey", "surfaceId", "status", "terminalHash"}
)
_NOTIFICATION_RECEIPT_KEYS = frozenset(
    {"schemaVersion", "idempotencyKey", "status", "terminalHash"}
)


class PublicControlError(Exception):
    """S3 の typed failure。"""

    def __init__(self, reason: str, message: str | None = None) -> None:
        self.reason = reason
        super().__init__(message or reason)


class PublishResultUnknown(Exception):
    """publisher/notifier が外部結果を確定できないシグナル。"""


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return _entry_canonical_sha256(value)


def _iso(value: datetime) -> str:
    return _validate_entry_time(value).isoformat()


def _issue_date(value: Any) -> str:
    if not isinstance(value, str) or _ISSUE_DATE.fullmatch(value) is None:
        raise PublicControlError(PUBLIC_INVENTORY_INVALID, "issue date is invalid")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise PublicControlError(PUBLIC_INVENTORY_INVALID, "issue date is invalid") from exc
    return value


def _terminal_value(lineage: str, value: Any) -> tuple[dict[str, Any], str, str]:
    if isinstance(value, Mapping):
        state_value = dict(value)
        if not isinstance(state_value.get("state"), str) or not state_value["state"]:
            raise PublicControlError(PUBLIC_LINEAGE_CONFLICT, f"{lineage} state is invalid")
        terminal_hash = state_value.get("terminalHash")
        if terminal_hash is None:
            terminal_hash = _hash({"lineage": lineage, "state": state_value})
        elif not isinstance(terminal_hash, str) or not terminal_hash:
            raise PublicControlError(PUBLIC_LINEAGE_CONFLICT, f"{lineage} terminal hash is invalid")
        return state_value, state_value["state"], terminal_hash
    if isinstance(value, str) and value:
        state_value = {"state": value}
        return state_value, value, _hash({"lineage": lineage, "state": value})
    raise PublicControlError(PUBLIC_LINEAGE_CONFLICT, f"{lineage} state is invalid")


class PublicController:
    """inventory を検証し、surface と通知を durable に収束させる。"""

    def __init__(
        self,
        runtime_root: Path,
        publisher: Any,
        notifier: Any,
        boundary_hook: Callable[[str], None] | None = None,
    ) -> None:
        self.runtime_root = Path(runtime_root)
        self.control_root = _managed_runtime_path(self.runtime_root, self.runtime_root / "control")
        self.database_path = _managed_runtime_path(self.runtime_root, self.control_root / "public-ledger-v1.sqlite3")
        self.publisher = publisher
        self.notifier = notifier
        self.boundary_hook = boundary_hook
        self.busy_timeout_ms = 1000

    def _hook(self, name: str) -> None:
        if self.boundary_hook is not None:
            self.boundary_hook(name)

    def _connect(self) -> sqlite3.Connection:
        self.control_root.mkdir(parents=True, exist_ok=True)
        try:
            connection = sqlite3.connect(
                self.database_path,
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
            raise PublicControlError(PUBLIC_LEDGER_CORRUPT, "public ledger cannot be opened") from exc

    @contextmanager
    def _transaction(self, connection: sqlite3.Connection):
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield
            connection.commit()
        except PublicControlError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise PublicControlError(PUBLIC_LEDGER_CORRUPT, "public ledger transaction failed") from exc

    def _schema(self, connection: sqlite3.Connection) -> None:
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS lineages (
                    issue_date TEXT NOT NULL,
                    lineage TEXT NOT NULL,
                    state TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    terminal_hash TEXT NOT NULL,
                    lineage_hash TEXT NOT NULL,
                    PRIMARY KEY(issue_date, lineage)
                );
                CREATE TABLE IF NOT EXISTS surfaces (
                    issue_date TEXT NOT NULL,
                    surface_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    artifact_sha256 TEXT NOT NULL,
                    terminal_hash TEXT,
                    receipt_json TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    attempt_disposition TEXT NOT NULL,
                    PRIMARY KEY(issue_date, surface_id),
                    UNIQUE(issue_date, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS notifications (
                    issue_date TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    receipt_json TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    attempt_disposition TEXT NOT NULL
                );
                """
            )
        except sqlite3.Error as exc:
            raise PublicControlError(PUBLIC_LEDGER_CORRUPT, "public ledger schema is invalid") from exc

    def _validate_inventory(self, issue_date: str, inventory: Mapping[str, Any]) -> tuple[list[str], list[str], dict[str, dict[str, Any]]]:
        if not isinstance(inventory, Mapping):
            raise PublicControlError(PUBLIC_INVENTORY_INVALID, "inventory must be an object")
        value = dict(inventory)
        if set(value) != _INVENTORY_KEYS or value.get("schemaVersion") != "PUBLIC_SURFACE_INVENTORY_V1":
            raise PublicControlError(PUBLIC_INVENTORY_INVALID, "inventory schema is invalid")
        if value.get("issueDate") != issue_date:
            raise PublicControlError(PUBLIC_INVENTORY_INVALID, "inventory issue date is invalid")
        required = value.get("requiredSurfaceIds")
        eligible = value.get("eligibleNotRequiredSurfaceIds")
        surfaces = value.get("surfaces")
        if not isinstance(required, list) or not required or any(not isinstance(item, str) or not item for item in required) or len(set(required)) != len(required):
            raise PublicControlError(PUBLIC_INVENTORY_INVALID, "required surface ids are invalid")
        if not isinstance(eligible, list) or any(not isinstance(item, str) or not item for item in eligible) or len(set(eligible)) != len(eligible) or not set(eligible).issubset(required):
            raise PublicControlError(PUBLIC_INVENTORY_INVALID, "eligible surface ids are invalid")
        inventory_hash = value.get("inventorySha256")
        if not isinstance(inventory_hash, str) or _HEX64.fullmatch(inventory_hash) is None or inventory_hash != _hash({key: item for key, item in value.items() if key != "inventorySha256"}):
            raise PublicControlError(PUBLIC_INVENTORY_INVALID, "inventory hash is invalid")
        if not isinstance(surfaces, list):
            raise PublicControlError(PUBLIC_INVENTORY_INVALID, "surfaces are invalid")
        rows: dict[str, dict[str, Any]] = {}
        for surface in surfaces:
            if not isinstance(surface, Mapping) or set(surface) != _SURFACE_KEYS:
                raise PublicControlError(PUBLIC_INVENTORY_INVALID, "surface row is invalid")
            row = dict(surface)
            surface_id = row.get("surfaceId")
            if not isinstance(surface_id, str) or not surface_id or surface_id not in required or surface_id in rows:
                raise PublicControlError(PUBLIC_INVENTORY_INVALID, "surface id is invalid")
            if row.get("status") not in _SURFACE_STATUSES:
                raise PublicControlError(PUBLIC_INVENTORY_INVALID, "surface status is invalid")
            artifact_hash = row.get("artifactSha256")
            if not isinstance(artifact_hash, str) or _HEX64.fullmatch(artifact_hash) is None:
                raise PublicControlError(PUBLIC_INVENTORY_INVALID, "surface artifact hash is invalid")
            rows[surface_id] = row
        missing = [surface_id for surface_id in required if surface_id not in rows]
        if missing:
            raise PublicControlError(PUBLIC_INCOMPLETE, "required surface is missing")
        for surface_id in required:
            status = rows[surface_id]["status"]
            if status == "FAILED":
                raise PublicControlError(PUBLIC_INCOMPLETE, "required surface failed")
            if status == "UNKNOWN":
                raise PublicControlError(MANUAL_RECONCILIATION_REQUIRED, "required surface is unknown")
            if status == "NOT_REQUIRED" and surface_id not in eligible:
                raise PublicControlError(PUBLIC_INCOMPLETE, "surface is not eligible for NOT_REQUIRED")
        return list(required), list(eligible), rows

    def _ensure_lineage(self, connection: sqlite3.Connection, issue_date: str, lineage: str, source: Any) -> sqlite3.Row:
        state_json, state, terminal_hash = _terminal_value(lineage, source)
        lineage_hash = _hash(state_json)
        existing = connection.execute("SELECT * FROM lineages WHERE issue_date=? AND lineage=?", (issue_date, lineage)).fetchone()
        if existing is not None:
            if existing["state_json"] != _canonical(state_json) or existing["terminal_hash"] != terminal_hash or existing["lineage_hash"] != lineage_hash:
                raise PublicControlError(PUBLIC_LINEAGE_CONFLICT, f"{lineage} lineage conflicts")
            return existing
        try:
            connection.execute(
                "INSERT INTO lineages(issue_date,lineage,state,state_json,terminal_hash,lineage_hash) VALUES(?,?,?,?,?,?)",
                (issue_date, lineage, state, _canonical(state_json), terminal_hash, lineage_hash),
            )
        except sqlite3.Error as exc:
            raise PublicControlError(PUBLIC_LINEAGE_CONFLICT, f"{lineage} lineage cannot be inserted") from exc
        return connection.execute("SELECT * FROM lineages WHERE issue_date=? AND lineage=?", (issue_date, lineage)).fetchone()

    def _surface_key(self, issue_date: str, surface_id: str, artifact_hash: str) -> str:
        return _hash({"issueDate": issue_date, "surfaceId": surface_id, "artifactSha256": artifact_hash})

    def _surface_terminal_hash(self, issue_date: str, surface_id: str, artifact_hash: str, state: str) -> str:
        return _hash({"issueDate": issue_date, "surfaceId": surface_id, "artifactSha256": artifact_hash, "state": state})

    def _ensure_surface(self, connection: sqlite3.Connection, issue_date: str, row: Mapping[str, Any]) -> sqlite3.Row:
        surface_id = row["surfaceId"]
        artifact_hash = row["artifactSha256"]
        idempotency_key = self._surface_key(issue_date, surface_id, artifact_hash)
        existing = connection.execute("SELECT * FROM surfaces WHERE issue_date=? AND surface_id=?", (issue_date, surface_id)).fetchone()
        if existing is not None:
            if existing["artifact_sha256"] != artifact_hash or existing["idempotency_key"] != idempotency_key:
                raise PublicControlError(PUBLIC_LEDGER_CORRUPT, "surface identity conflicts")
            return existing
        status = row["status"]
        terminal_hash = self._surface_terminal_hash(issue_date, surface_id, artifact_hash, status) if status in {"CONFIRMED", "NOT_REQUIRED"} else None
        disposition = "TERMINAL" if terminal_hash is not None else "INTENT_DURABLE"
        connection.execute(
            "INSERT INTO surfaces(issue_date,surface_id,state,idempotency_key,artifact_sha256,terminal_hash,receipt_json,attempt_count,attempt_disposition) VALUES(?,?,?,?,?,?,?,?,?)",
            (issue_date, surface_id, status if terminal_hash is not None else "PENDING", idempotency_key, artifact_hash, terminal_hash, None, 0, disposition),
        )
        return connection.execute("SELECT * FROM surfaces WHERE issue_date=? AND surface_id=?", (issue_date, surface_id)).fetchone()

    def _surface_receipt(self, value: Any, surface_id: str, idempotency_key: str) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise PublicControlError(PUBLIC_RECEIPT_INVALID, "surface receipt is not an object")
        receipt = dict(value)
        if set(receipt) != _SURFACE_RECEIPT_KEYS or receipt.get("schemaVersion") != "PUBLIC_SURFACE_RECEIPT_V1" or receipt.get("status") != "CONFIRMED":
            raise PublicControlError(PUBLIC_RECEIPT_INVALID, "surface receipt schema is invalid")
        if receipt.get("surfaceId") != surface_id or receipt.get("idempotencyKey") != idempotency_key:
            raise PublicControlError(PUBLIC_RECEIPT_INVALID, "surface receipt binding is invalid")
        if not isinstance(receipt.get("terminalHash"), str) or _HEX64.fullmatch(receipt["terminalHash"]) is None:
            raise PublicControlError(PUBLIC_RECEIPT_INVALID, "surface terminal hash is invalid")
        return receipt

    def _confirm_surface(self, connection: sqlite3.Connection, row: sqlite3.Row, receipt: Mapping[str, Any]) -> sqlite3.Row:
        value = self._surface_receipt(receipt, row["surface_id"], row["idempotency_key"])
        connection.execute(
            "UPDATE surfaces SET state='CONFIRMED',terminal_hash=?,receipt_json=?,attempt_disposition='TERMINAL' WHERE issue_date=? AND surface_id=?",
            (value["terminalHash"], _canonical(value), row["issue_date"], row["surface_id"]),
        )
        return connection.execute("SELECT * FROM surfaces WHERE issue_date=? AND surface_id=?", (row["issue_date"], row["surface_id"])).fetchone()

    def _prepare_surface_attempt(self, connection: sqlite3.Connection, row: sqlite3.Row) -> sqlite3.Row:
        if row["attempt_disposition"] == "QUERY_REQUIRED":
            return row
        connection.execute(
            "UPDATE surfaces SET attempt_count=attempt_count+1,attempt_disposition='QUERY_REQUIRED' WHERE issue_date=? AND surface_id=?",
            (row["issue_date"], row["surface_id"]),
        )
        return connection.execute("SELECT * FROM surfaces WHERE issue_date=? AND surface_id=?", (row["issue_date"], row["surface_id"])).fetchone()

    def _publish_surface(self, connection: sqlite3.Connection, row: sqlite3.Row) -> sqlite3.Row:
        if row["state"] in {"CONFIRMED", "NOT_REQUIRED"}:
            return row
        if row["attempt_disposition"] == "QUERY_REQUIRED":
            try:
                queried = self.publisher.query(row["idempotency_key"])
            except PublishResultUnknown:
                raise
            except Exception as exc:
                raise PublishResultUnknown("surface query result is unknown") from exc
            if queried is None:
                raise PublishResultUnknown("surface query did not find a receipt")
            with self._transaction(connection):
                return self._confirm_surface(connection, row, queried)
        row = self._prepare_surface_attempt(connection, row)
        self._hook("before_surface_publish")
        try:
            receipt = self.publisher.publish(
                {
                    "issueDate": row["issue_date"],
                    "surfaceId": row["surface_id"],
                    "artifactSha256": row["artifact_sha256"],
                    "idempotencyKey": row["idempotency_key"],
                }
            )
        except PublishResultUnknown:
            raise
        except Exception as exc:
            raise PublicControlError(PUBLIC_RECEIPT_INVALID, "surface publish failed") from exc
        with self._transaction(connection):
            confirmed = self._confirm_surface(connection, row, receipt)
        self._hook("after_surface_confirmed")
        return confirmed

    def _public_terminal_hash(self, issue_date: str, surface_rows: list[sqlite3.Row]) -> str:
        return _hash(
            {
                "lineage": "Public",
                "issueDate": issue_date,
                "surfaces": [
                    {"surfaceId": row["surface_id"], "state": row["state"], "terminalHash": row["terminal_hash"]}
                    for row in surface_rows
                ],
            }
        )

    def _notification_receipt(self, value: Any, idempotency_key: str) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise PublicControlError(PUBLIC_NOTIFICATION_INVALID, "notification receipt is not an object")
        receipt = dict(value)
        if set(receipt) != _NOTIFICATION_RECEIPT_KEYS or receipt.get("schemaVersion") != "PUBLIC_NOTIFICATION_RECEIPT_V1" or receipt.get("status") != "CONFIRMED":
            raise PublicControlError(PUBLIC_NOTIFICATION_INVALID, "notification receipt schema is invalid")
        if receipt.get("idempotencyKey") != idempotency_key or not isinstance(receipt.get("terminalHash"), str) or _HEX64.fullmatch(receipt["terminalHash"]) is None:
            raise PublicControlError(PUBLIC_NOTIFICATION_INVALID, "notification receipt binding is invalid")
        return receipt

    def _notify(self, connection: sqlite3.Connection, issue_date: str, public_hash: str) -> sqlite3.Row:
        key = _hash({"issueDate": issue_date, "publicTerminalHash": public_hash})
        row = connection.execute("SELECT * FROM notifications WHERE issue_date=?", (issue_date,)).fetchone()
        if row is None:
            connection.execute(
                "INSERT INTO notifications(issue_date,state,idempotency_key,receipt_json,attempt_count,attempt_disposition) VALUES(?,?,?,?,?,?)",
                (issue_date, "PENDING", key, None, 1, "INTENT_DURABLE"),
            )
            row = connection.execute("SELECT * FROM notifications WHERE issue_date=?", (issue_date,)).fetchone()
        elif row["idempotency_key"] != key:
            raise PublicControlError(PUBLIC_LEDGER_CORRUPT, "notification identity conflicts")
        if row["state"] == "CONFIRMED":
            return row
        if row["attempt_disposition"] == "QUERY_REQUIRED":
            try:
                queried = self.notifier.query(row["idempotency_key"])
            except PublishResultUnknown:
                raise
            except Exception as exc:
                raise PublishResultUnknown("notification query result is unknown") from exc
            if queried is None:
                raise PublishResultUnknown("notification query did not find a receipt")
            receipt = self._notification_receipt(queried, row["idempotency_key"])
        else:
            self._hook("before_notification")
            try:
                queried = self.notifier.notify({"issueDate": issue_date, "idempotencyKey": row["idempotency_key"], "publicTerminalHash": public_hash})
            except PublishResultUnknown:
                with self._transaction(connection):
                    connection.execute(
                        "UPDATE notifications SET attempt_disposition='QUERY_REQUIRED' WHERE issue_date=?",
                        (issue_date,),
                    )
                raise
            except Exception as exc:
                raise PublicControlError(PUBLIC_NOTIFICATION_INVALID, "notification failed") from exc
            receipt = self._notification_receipt(queried, row["idempotency_key"])
        with self._transaction(connection):
            connection.execute(
                "UPDATE notifications SET state='CONFIRMED',receipt_json=?,attempt_disposition='TERMINAL' WHERE issue_date=?",
                (_canonical(receipt), issue_date),
            )
        self._hook("after_notification_confirmed")
        return connection.execute("SELECT * FROM notifications WHERE issue_date=?", (issue_date,)).fetchone()

    def _result(self, connection: sqlite3.Connection, issue_date: str, required: list[str]) -> dict[str, Any]:
        surfaces = [connection.execute("SELECT * FROM surfaces WHERE issue_date=? AND surface_id=?", (issue_date, surface_id)).fetchone() for surface_id in required]
        lineages = [connection.execute("SELECT * FROM lineages WHERE issue_date=? AND lineage=?", (issue_date, lineage)).fetchone() for lineage in _LINEAGES]
        notification = connection.execute("SELECT * FROM notifications WHERE issue_date=?", (issue_date,)).fetchone()
        if any(row is None for row in surfaces) or any(row is None for row in lineages) or notification is None:
            raise PublicControlError(PUBLIC_LEDGER_CORRUPT, "public terminal projection is incomplete")
        return {
            "schemaVersion": "PUBLIC_RECONCILE_RESULT_V1",
            "issueDate": issue_date,
            "publicState": next(row["state"] for row in lineages if row["lineage"] == "Public"),
            "requiredSurfaceIds": list(required),
            "surfaceStates": {row["surface_id"]: row["state"] for row in surfaces},
            "lineages": [
                {"lineage": row["lineage"], "state": row["state"], "terminalHash": row["terminal_hash"], "lineageHash": row["lineage_hash"]}
                for row in lineages
            ],
            "notificationState": notification["state"],
            "terminalHashes": {row["lineage"]: row["terminal_hash"] for row in lineages},
        }

    def reconcile(
        self,
        issue_date: str,
        scheduled_state: Any,
        recovery_state: Any,
        readiness_state: Any,
        inventory: Mapping[str, Any],
        observed_at: datetime,
    ) -> dict[str, Any]:
        issue = _issue_date(issue_date)
        try:
            _validate_entry_time(observed_at)
        except Exception as exc:
            raise PublicControlError(PUBLIC_INVENTORY_INVALID, "observed_at is invalid") from exc
        required, eligible, surface_values = self._validate_inventory(issue, inventory)
        del eligible
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            self._schema(connection)
            with self._transaction(connection):
                self._ensure_lineage(connection, issue, "Scheduled", scheduled_state)
                self._ensure_lineage(connection, issue, "Recovery", recovery_state)
                self._ensure_lineage(connection, issue, "Readiness", readiness_state)
                for surface_id in required:
                    self._ensure_surface(connection, issue, surface_values[surface_id])
            for surface_id in required:
                row = connection.execute("SELECT * FROM surfaces WHERE issue_date=? AND surface_id=?", (issue, surface_id)).fetchone()
                if row is None:
                    raise PublicControlError(PUBLIC_LEDGER_CORRUPT, "surface row is missing")
                if row["state"] not in {"CONFIRMED", "NOT_REQUIRED"}:
                    try:
                        row = self._publish_surface(connection, row)
                    except PublishResultUnknown:
                        raise
            surface_rows = [connection.execute("SELECT * FROM surfaces WHERE issue_date=? AND surface_id=?", (issue, surface_id)).fetchone() for surface_id in required]
            if any(row is None or row["state"] not in {"CONFIRMED", "NOT_REQUIRED"} for row in surface_rows):
                raise PublicControlError(PUBLIC_INCOMPLETE, "public surfaces are incomplete")
            public_hash = self._public_terminal_hash(issue, [row for row in surface_rows if row is not None])
            public_state = {
                "state": "GREEN",
                "surfaceStates": {row["surface_id"]: row["state"] for row in surface_rows if row is not None},
                "terminalHash": public_hash,
            }
            with self._transaction(connection):
                self._ensure_lineage(connection, issue, "Public", public_state)
            self._notify(connection, issue, public_hash)
            return self._result(connection, issue, required)
        finally:
            if connection is not None:
                connection.close()


def render_scheduled_first_report(projection: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(projection, Mapping):
        raise PublicControlError(PUBLIC_LINEAGE_CONFLICT, "lineage projection is invalid")
    lineages = []
    overall_green = True
    for lineage in _LINEAGES:
        value = projection.get(lineage)
        if not isinstance(value, Mapping):
            overall_green = False
            value = {"state": "UNKNOWN"}
        state = value.get("state")
        if state not in {"GREEN", "CONFIRMED"}:
            overall_green = False
        lineages.append({"lineage": lineage, **dict(value)})
    return {
        "schemaVersion": "PUBLIC_SCHEDULED_FIRST_REPORT_V1",
        "overallState": "GREEN" if overall_green else "NOT_GREEN",
        "lineages": lineages,
    }
