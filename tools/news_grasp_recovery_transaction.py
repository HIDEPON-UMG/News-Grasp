"""06:40 recovery を issue-date 単位で一意に所有する transaction。

Deadman、automation、watcher、互換 CLI はこの store の acquire-or-attach を
経由する。永続 fencing token と期限付き lease により、同日 runner の二重起動と
stale owner の ABA 完了を拒否する。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import uuid
import ctypes
import ctypes.wintypes
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


TRANSACTION_SCHEMA_V2 = "AUDIT_RECOVERY_TRANSACTION_V2"
TRANSACTION_SCHEMA = "AUDIT_RECOVERY_TRANSACTION_V3"
MISSION_TERMINALS = {
    "closed_reader_green",
    "closed_reader_incomplete_external_blocker",
    "closed_reader_unverified_budget_exhausted",
    "closed_control_plane_unavailable",
}
MISSION_PHASES = (
    "observed",
    "envelope_validated",
    "recovery_admitted",
    "recovery_running",
    "reader_verified",
    "finalization_prepared",
    "finalization_committed",
    "closed",
)
JST = ZoneInfo("Asia/Tokyo")
ISSUE_DATE_RE = re.compile(r"^20\d{2}-\d{2}-\d{2}$")
LEASE_MINUTES = 5
MAX_JOURNAL_EVENTS = 64
MAX_TRANSACTION_BYTES = 1024 * 1024


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _seal(value: dict[str, Any]) -> dict[str, Any]:
    result = dict(value)
    result.pop("receiptSha256", None)
    result["receiptSha256"] = hashlib.sha256(_canonical(result)).hexdigest()
    return result


def _parse_issue_date(value: str) -> date:
    if ISSUE_DATE_RE.fullmatch(value) is None:
        raise ValueError("ISSUE_DATE_INVALID")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError("ISSUE_DATE_INVALID") from error
    if parsed.isoformat() != value:
        raise ValueError("ISSUE_DATE_INVALID")
    return parsed


def _aware(value: datetime | None) -> datetime:
    result = value or datetime.now(timezone.utc)
    if result.tzinfo is None:
        raise ValueError("AUDIT_RECOVERY_CLOCK_INVALID")
    return result


def _clock(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise ValueError("AUDIT_RECOVERY_CLOCK_INVALID") from error
    if parsed.tzinfo is None:
        raise ValueError("AUDIT_RECOVERY_CLOCK_INVALID")
    return parsed


def audit_deadlines(issue_date: str) -> dict[str, str]:
    """caller clock ではなく対象日 06:40 JST から全 deadline を導出する。"""

    parsed = _parse_issue_date(issue_date)
    anchor = datetime(parsed.year, parsed.month, parsed.day, 6, 40, tzinfo=JST)
    return {
        "auditSloAnchor": anchor.isoformat(),
        "preflightDeadlineAt": (anchor + timedelta(minutes=5)).isoformat(),
        "targetCloseoutReserveAt": (anchor + timedelta(minutes=45)).isoformat(),
        "targetDeadlineAt": (anchor + timedelta(minutes=60)).isoformat(),
        "highCostCutoffAt": (anchor + timedelta(minutes=75)).isoformat(),
        "hardDeadlineAt": (anchor + timedelta(minutes=90)).isoformat(),
    }


def _opened_path(descriptor: int, fallback: Path) -> Path:
    if os.name != "nt":
        return Path(os.path.realpath(fallback))
    import msvcrt

    handle = msvcrt.get_osfhandle(descriptor)
    buffer = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetFinalPathNameByHandleW(
        ctypes.c_void_p(handle), buffer, len(buffer), 0
    )
    if length <= 0 or length >= len(buffer):
        raise OSError("opened path unavailable")
    value = buffer.value
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return Path(value)


def _read_regular_bytes(path: Path, *, root: Path) -> bytes | None:
    """transactionを同一handleから読み、reparse/hardlink/path driftを拒否する。"""

    try:
        before = os.lstat(path)
    except FileNotFoundError:
        return None
    attributes = int(getattr(before, "st_file_attributes", 0))
    if (
        not stat.S_ISREG(before.st_mode)
        or stat.S_ISLNK(before.st_mode)
        or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        or before.st_nlink != 1
        or before.st_size > MAX_TRANSACTION_BYTES
    ):
        raise ValueError("AUDIT_RECOVERY_TRANSACTION_INVALID")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            opened_path = _opened_path(descriptor, path).resolve(strict=True)
            boundary = root.resolve(strict=True)
            if (
                opened_path.parent != boundary
                or (opened.st_dev, opened.st_ino, opened.st_size, opened.st_nlink)
                != (before.st_dev, before.st_ino, before.st_size, 1)
            ):
                raise OSError("transaction identity drift")
            chunks: list[bytes] = []
            remaining = MAX_TRANSACTION_BYTES + 1
            while remaining > 0:
                chunk = os.read(descriptor, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        raw = b"".join(chunks)
        if (
            len(raw) != before.st_size
            or len(raw) > MAX_TRANSACTION_BYTES
            or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        ):
            raise OSError("transaction changed during read")
        return raw
    except OSError as error:
        raise ValueError("AUDIT_RECOVERY_TRANSACTION_INVALID") from error


class RecoveryTransactionStore:
    """Filesystem-backed acquire-or-attach store with bounded crash journal."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _prepare_root(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        metadata = os.lstat(self.root)
        attributes = int(getattr(metadata, "st_file_attributes", 0))
        if (
            self.root.is_symlink()
            or not stat.S_ISDIR(metadata.st_mode)
            or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        ):
            raise ValueError("AUDIT_RECOVERY_TRANSACTION_ROOT_INVALID")
        return self.root.resolve()

    def _transaction_path(self, issue_date: str) -> Path:
        _parse_issue_date(issue_date)
        return self._prepare_root() / f"{issue_date}.json"

    def _guard_path(self, issue_date: str) -> Path:
        return self._prepare_root() / f".{issue_date}.lock"

    @contextmanager
    def _guard(self, issue_date: str):
        """クラッシュ時にOSが自動解放する非blocking file lock。"""

        path = self._guard_path(issue_date)
        if path.exists() and (path.is_symlink() or not path.is_file()):
            raise ValueError("AUDIT_RECOVERY_TRANSACTION_GUARD_INVALID")
        flags = (
            os.O_CREAT
            | os.O_RDWR
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(path, flags, 0o600)
        except OSError as error:
            raise ValueError("AUDIT_RECOVERY_TRANSACTION_GUARD_INVALID") from error
        stream = os.fdopen(descriptor, "r+b", buffering=0)
        locked = False
        try:
            metadata = os.lstat(path)
            opened = os.fstat(stream.fileno())
            attributes = int(getattr(metadata, "st_file_attributes", 0))
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or attributes
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                or metadata.st_nlink != 1
                or (opened.st_dev, opened.st_ino, opened.st_nlink)
                != (metadata.st_dev, metadata.st_ino, 1)
            ):
                raise ValueError("AUDIT_RECOVERY_TRANSACTION_GUARD_INVALID")
            if os.name == "nt":
                import msvcrt

                handle = msvcrt.get_osfhandle(stream.fileno())
                buffer = ctypes.create_unicode_buffer(32768)
                length = ctypes.windll.kernel32.GetFinalPathNameByHandleW(
                    ctypes.c_void_p(handle), buffer, len(buffer), 0
                )
                opened_path = buffer.value
                if opened_path.startswith("\\\\?\\"):
                    opened_path = opened_path[4:]
                if (
                    length <= 0
                    or length >= len(buffer)
                    or os.path.normcase(os.path.abspath(opened_path))
                    != os.path.normcase(os.path.abspath(path))
                ):
                    raise ValueError("AUDIT_RECOVERY_TRANSACTION_GUARD_INVALID")
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"\0")
                stream.flush()
                os.fsync(stream.fileno())
            stream.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except OSError as error:
                raise ValueError("AUDIT_RECOVERY_TRANSACTION_BUSY") from error
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

    def _read(self, issue_date: str) -> dict[str, Any] | None:
        path = self._transaction_path(issue_date)
        raw = _read_regular_bytes(path, root=self._prepare_root())
        if raw is None:
            return None
        try:
            value = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("AUDIT_RECOVERY_TRANSACTION_INVALID") from error
        if not isinstance(value, dict) or value.get("schemaVersion") not in {
            TRANSACTION_SCHEMA,
            TRANSACTION_SCHEMA_V2,
        }:
            raise ValueError("AUDIT_RECOVERY_TRANSACTION_INVALID")
        expected = str(value.get("receiptSha256") or "")
        if _seal(value).get("receiptSha256") != expected:
            raise ValueError("AUDIT_RECOVERY_TRANSACTION_INVALID")
        if value.get("issueDate") != issue_date:
            raise ValueError("AUDIT_RECOVERY_TRANSACTION_INVALID")
        return value

    @staticmethod
    def _compatibility_terminal(transaction: dict[str, Any]) -> dict[str, Any]:
        projection = transaction.get("terminalProjection")
        if isinstance(projection, dict):
            return dict(projection)
        legacy = transaction.get("terminal")
        return dict(legacy) if isinstance(legacy, dict) else {}

    def _write(self, issue_date: str, value: dict[str, Any]) -> dict[str, Any]:
        path = self._transaction_path(issue_date)
        payload = dict(value)
        payload["schemaVersion"] = TRANSACTION_SCHEMA
        sealed = _seal(payload)
        encoded = (
            json.dumps(sealed, ensure_ascii=False, sort_keys=True) + "\n"
        ).encode("utf-8")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            if path.exists():
                _read_regular_bytes(path, root=self._prepare_root())
            os.replace(temporary, path)
            persisted = _read_regular_bytes(path, root=self._prepare_root())
            if persisted != encoded:
                raise ValueError("AUDIT_RECOVERY_TRANSACTION_INVALID")
            return sealed
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _process_identity(pid: int) -> str:
        """PID再利用を区別できるprocess creation identityを返す。"""

        if pid <= 0:
            return ""
        if os.name != "nt":
            try:
                os.kill(pid, 0)
            except OSError:
                return ""
            return f"pid:{pid}"
        process_query_limited_information = 0x1000
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.argtypes = [
            ctypes.wintypes.DWORD,
            ctypes.wintypes.BOOL,
            ctypes.wintypes.DWORD,
        ]
        kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE
        kernel32.GetProcessTimes.argtypes = [
            ctypes.wintypes.HANDLE,
            ctypes.POINTER(ctypes.wintypes.FILETIME),
            ctypes.POINTER(ctypes.wintypes.FILETIME),
            ctypes.POINTER(ctypes.wintypes.FILETIME),
            ctypes.POINTER(ctypes.wintypes.FILETIME),
        ]
        kernel32.GetProcessTimes.restype = ctypes.wintypes.BOOL
        kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
        kernel32.CloseHandle.restype = ctypes.wintypes.BOOL
        handle = kernel32.OpenProcess(
            process_query_limited_information, False, pid
        )
        if not handle:
            return ""
        created = ctypes.wintypes.FILETIME()
        exited = ctypes.wintypes.FILETIME()
        kernel = ctypes.wintypes.FILETIME()
        user = ctypes.wintypes.FILETIME()
        try:
            if not kernel32.GetProcessTimes(
                handle,
                ctypes.byref(created),
                ctypes.byref(exited),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                return ""
            ticks = (int(created.dwHighDateTime) << 32) | int(created.dwLowDateTime)
            return f"win-filetime:{ticks}"
        finally:
            kernel32.CloseHandle(handle)

    @classmethod
    def _owner_is_alive(cls, transaction: dict[str, Any]) -> bool:
        try:
            pid = int(transaction.get("ownerPid") or 0)
        except (TypeError, ValueError):
            return False
        expected = str(transaction.get("ownerProcessIdentity") or "")
        return bool(expected) and cls._process_identity(pid) == expected

    @staticmethod
    def _append_event(
        transaction: dict[str, Any], *, event: str, now: datetime, details: dict[str, Any]
    ) -> None:
        journal = transaction.setdefault("phaseJournal", [])
        if not isinstance(journal, list) or len(journal) >= MAX_JOURNAL_EVENTS:
            raise ValueError("AUDIT_RECOVERY_JOURNAL_LIMIT_EXCEEDED")
        journal.append({"event": event, "at": now.isoformat(), **details})

    def acquire(
        self,
        *,
        issue_date: str,
        trigger: str,
        owner_id: str,
        owner_pid: int | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        _parse_issue_date(issue_date)
        if not trigger.strip() or not owner_id.strip():
            raise ValueError("AUDIT_RECOVERY_OWNER_INVALID")
        observed_at = _aware(now)
        owner_pid_value = int(owner_pid or 0)
        owner_process_identity = (
            self._process_identity(owner_pid_value) if owner_pid_value else ""
        )
        if owner_pid_value and not owner_process_identity:
            raise ValueError("AUDIT_RECOVERY_OWNER_PROCESS_INVALID")
        try:
            with self._guard(issue_date):
                current = self._read(issue_date)
                if current and current.get("status") == "terminal":
                    response = dict(current)
                    response["status"] = "terminal_projection"
                    response["terminal"] = self._compatibility_terminal(current)
                    response["missionTerminal"] = str(
                        current.get("missionTerminal") or ""
                    )
                    response["processExitCode"] = int(
                        (response["terminal"] or {}).get("exitCode", 0)
                    )
                    return response
                if current and current.get("status") == "active":
                    lease_expires = _clock(str(current.get("leaseExpiresAt") or ""))
                    if observed_at < lease_expires or self._owner_is_alive(current):
                        response = dict(current)
                        response["status"] = (
                            "attached_owner_alive"
                            if observed_at >= lease_expires
                            else "attached"
                        )
                        response["processExitCode"] = 3
                        return response
                    fencing_token = int(current.get("fencingToken") or 0) + 1
                    transaction = dict(current)
                    transaction.update(
                        {
                            "status": "active",
                            "ownerId": owner_id,
                            "ownerTrigger": trigger,
                            "ownerPid": owner_pid_value,
                            "ownerProcessIdentity": owner_process_identity,
                            "fencingToken": fencing_token,
                            "leaseExpiresAt": (
                                observed_at + timedelta(minutes=LEASE_MINUTES)
                            ).isoformat(),
                            "updatedAt": observed_at.isoformat(),
                            "missionState": str(current.get("missionState") or "observed"),
                            "missionPhase": str(current.get("missionPhase") or "observed"),
                        }
                    )
                    self._append_event(
                        transaction,
                        event="stale_owner_recovered",
                        now=observed_at,
                        details={"ownerId": owner_id, "fencingToken": fencing_token},
                    )
                    written = self._write(issue_date, transaction)
                    response = dict(written)
                    response["status"] = "recovered_stale_owner"
                    response["processExitCode"] = 0
                    return response

                transaction = {
                    "schemaVersion": TRANSACTION_SCHEMA,
                    "issueDate": issue_date,
                    "transactionId": uuid.uuid4().hex,
                    "status": "active",
                    "ownerId": owner_id,
                    "ownerTrigger": trigger,
                    "ownerPid": owner_pid_value,
                    "ownerProcessIdentity": owner_process_identity,
                    "fencingToken": 1,
                    "transactionStartedAt": observed_at.isoformat(),
                    "leaseExpiresAt": (
                        observed_at + timedelta(minutes=LEASE_MINUTES)
                    ).isoformat(),
                    "updatedAt": observed_at.isoformat(),
                    **audit_deadlines(issue_date),
                    "phaseJournal": [],
                    "missionState": "observed",
                    "missionPhase": "observed",
                    "missionTerminal": None,
                    "terminalProjection": None,
                    "operationBinding": None,
                    "observationEvents": [],
                }
                self._append_event(
                    transaction,
                    event="owner_acquired",
                    now=observed_at,
                    details={"ownerId": owner_id, "fencingToken": 1, "trigger": trigger},
                )
                written = self._write(issue_date, transaction)
                response = dict(written)
                response["status"] = "acquired"
                response["processExitCode"] = 0
                return response
        except ValueError as error:
            if str(error) != "AUDIT_RECOVERY_TRANSACTION_BUSY":
                raise
            current = self._read(issue_date)
            if current is None:
                return {
                    "schemaVersion": TRANSACTION_SCHEMA,
                    "issueDate": issue_date,
                    "status": "attached_pending",
                    "processExitCode": 3,
                }
            response = dict(current)
            response["status"] = (
                "terminal_projection"
                if current.get("status") == "terminal"
                else "attached"
            )
            response["processExitCode"] = (
                int((current.get("terminal") or {}).get("exitCode", 0))
                if current.get("status") == "terminal"
                else 3
            )
            return response

    def complete(
        self,
        *,
        issue_date: str,
        owner_id: str,
        fencing_token: int,
        terminal: dict[str, Any],
        now: datetime | None = None,
    ) -> dict[str, Any]:
        observed_at = _aware(now)
        with self._guard(issue_date):
            current = self._read(issue_date)
            if current is None or current.get("status") != "active":
                raise ValueError("AUDIT_RECOVERY_TRANSACTION_NOT_ACTIVE")
            if (
                current.get("ownerId") != owner_id
                or int(current.get("fencingToken") or 0) != int(fencing_token)
            ):
                raise ValueError("AUDIT_RECOVERY_FENCING_TOKEN_STALE")
            compatibility_terminal = str(terminal.get("terminal") or "")
            if compatibility_terminal not in {
                "audit_normal_green",
                "audit_recovered_green",
                "audit_observation_unverified",
                "audit_major_incident_open",
            }:
                raise ValueError("AUDIT_RECOVERY_TERMINAL_INVALID")
            mission_terminal = {
                "audit_normal_green": "closed_reader_green",
                "audit_recovered_green": "closed_reader_green",
                "audit_observation_unverified": "closed_reader_unverified_budget_exhausted",
                "audit_major_incident_open": "closed_reader_incomplete_external_blocker",
            }[compatibility_terminal]
            current["status"] = "terminal"
            current["missionState"] = "closed"
            current["missionPhase"] = "closed"
            current["missionTerminal"] = mission_terminal
            current["terminalProjection"] = dict(terminal)
            current.pop("terminal", None)
            current["updatedAt"] = observed_at.isoformat()
            current["leaseExpiresAt"] = observed_at.isoformat()
            if compatibility_terminal == "audit_major_incident_open":
                self._append_event(
                    current,
                    event="audit_observation",
                    now=observed_at,
                    details={
                        "schemaVersion": "AuditObservationEventV1",
                        "compatibilityTerminal": compatibility_terminal,
                        "missionTerminal": mission_terminal,
                        "ownerId": owner_id,
                        "fencingToken": fencing_token,
                    },
                )
            self._append_event(
                current,
                event="terminal_committed",
                now=observed_at,
                details={
                    "ownerId": owner_id,
                    "fencingToken": fencing_token,
                    "terminal": mission_terminal,
                    "compatibilityTerminal": compatibility_terminal,
                },
            )
            written = self._write(issue_date, current)
            response = dict(written)
            response["processExitCode"] = int(terminal.get("exitCode", 0))
            return response

    def advance_phase(
        self,
        *,
        issue_date: str,
        owner_id: str,
        fencing_token: int,
        phase: str,
        details: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """同一fencing tokenでmission phaseを単調に進める。"""

        if phase not in MISSION_PHASES:
            raise ValueError("AUDIT_RECOVERY_MISSION_PHASE_INVALID")
        observed_at = _aware(now)
        with self._guard(issue_date):
            current = self._read(issue_date)
            if current is None or current.get("status") != "active":
                raise ValueError("AUDIT_RECOVERY_TRANSACTION_NOT_ACTIVE")
            if (
                current.get("ownerId") != owner_id
                or int(current.get("fencingToken") or 0) != int(fencing_token)
            ):
                raise ValueError("AUDIT_RECOVERY_FENCING_TOKEN_STALE")
            current_index = MISSION_PHASES.index(
                str(current.get("missionPhase") or "observed")
            )
            next_index = MISSION_PHASES.index(phase)
            if next_index < current_index:
                raise ValueError("AUDIT_RECOVERY_MISSION_PHASE_REGRESSION")
            current["missionPhase"] = phase
            current["missionState"] = (
                "closed" if phase == "closed" else "active"
            )
            current["updatedAt"] = observed_at.isoformat()
            if next_index > current_index:
                self._append_event(
                    current,
                    event="mission_phase_advanced",
                    now=observed_at,
                    details={
                        "phase": phase,
                        "ownerId": owner_id,
                        "fencingToken": fencing_token,
                        **dict(details or {}),
                    },
                )
            return self._write(issue_date, current)

    def bind_operation(
        self,
        *,
        issue_date: str,
        owner_id: str,
        fencing_token: int,
        binding: dict[str, Any],
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """child/finalizer/public authority identityをtransaction journalへ束縛する。"""

        required = (
            "childExecutableSha256",
            "childArgvSha256",
            "childCwdSha256",
            "executionReceiptSha256",
        )
        if any(not str(binding.get(key) or "") for key in required):
            raise ValueError("AUDIT_RECOVERY_OPERATION_BINDING_INVALID")
        observed_at = _aware(now)
        with self._guard(issue_date):
            current = self._read(issue_date)
            if current is None or current.get("status") != "active":
                raise ValueError("AUDIT_RECOVERY_TRANSACTION_NOT_ACTIVE")
            if (
                current.get("ownerId") != owner_id
                or int(current.get("fencingToken") or 0) != int(fencing_token)
            ):
                raise ValueError("AUDIT_RECOVERY_TRANSACTION_FENCING_TOKEN_STALE")
            existing = current.get("operationBinding")
            if existing is not None and existing != binding:
                raise ValueError("AUDIT_RECOVERY_OPERATION_BINDING_DRIFT")
            current["operationBinding"] = dict(binding)
            self._append_event(
                current,
                event="operation_bound",
                now=observed_at,
                details={
                    "ownerId": owner_id,
                    "fencingToken": fencing_token,
                    "bindingSha256": hashlib.sha256(_canonical(binding)).hexdigest(),
                },
            )
            current["updatedAt"] = observed_at.isoformat()
            return self._write(issue_date, current)

    def observe(
        self,
        *,
        issue_date: str,
        owner_id: str,
        fencing_token: int,
        observation: dict[str, Any],
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """major incidentをterminal化せずappend-only observationとして記録する。"""

        if not observation.get("reasonCode"):
            raise ValueError("AUDIT_RECOVERY_OBSERVATION_INVALID")
        observed_at = _aware(now)
        with self._guard(issue_date):
            current = self._read(issue_date)
            if current is None or current.get("status") != "active":
                raise ValueError("AUDIT_RECOVERY_TRANSACTION_NOT_ACTIVE")
            if (
                current.get("ownerId") != owner_id
                or int(current.get("fencingToken") or 0) != int(fencing_token)
            ):
                raise ValueError("AUDIT_RECOVERY_TRANSACTION_FENCING_TOKEN_STALE")
            events = current.setdefault("observationEvents", [])
            event = {
                "schemaVersion": "AuditObservationEventV1",
                "observedAt": observed_at.isoformat(),
                "fencingToken": fencing_token,
                **dict(observation),
            }
            events.append(event)
            if len(events) > MAX_JOURNAL_EVENTS:
                raise ValueError("AUDIT_RECOVERY_JOURNAL_LIMIT_EXCEEDED")
            current["updatedAt"] = observed_at.isoformat()
            return self._write(issue_date, current)

    def renew(
        self,
        *,
        issue_date: str,
        owner_id: str,
        fencing_token: int,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """長時間runner観測中にleaseだけを更新し、journalを水増ししない。"""

        observed_at = _aware(now)
        with self._guard(issue_date):
            current = self._read(issue_date)
            if current is None or current.get("status") != "active":
                raise ValueError("AUDIT_RECOVERY_TRANSACTION_NOT_ACTIVE")
            if (
                current.get("ownerId") != owner_id
                or int(current.get("fencingToken") or 0) != int(fencing_token)
            ):
                raise ValueError("AUDIT_RECOVERY_FENCING_TOKEN_STALE")
            current["lastHeartbeatAt"] = observed_at.isoformat()
            current["leaseExpiresAt"] = (
                observed_at + timedelta(minutes=LEASE_MINUTES)
            ).isoformat()
            current["updatedAt"] = observed_at.isoformat()
            return self._write(issue_date, current)
