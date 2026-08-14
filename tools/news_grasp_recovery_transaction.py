"""Issue-date単位のNews-Grasp recovery single-flight transaction。"""

from __future__ import annotations

import contextlib
import ctypes
import argparse
from datetime import date, datetime, time as clock_time, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
import sys
from typing import Any, Iterator, Mapping
import uuid

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import news_grasp_verified_storage as verified_storage


SCHEMA = "AUDIT_RECOVERY_TRANSACTION_V2"
READINESS_SNAPSHOT_SCHEMA = "SCHEDULED_READINESS_SNAPSHOT_V2"
LAUNCH_PERMIT_SCHEMA = "SCHEDULED_PRODUCTION_LAUNCH_PERMIT_V2"
ACTUAL_LAUNCH_IDENTITY_SCHEMA = "NEWS_GRASP_ACTUAL_LAUNCH_IDENTITY_V1"
JST = timezone(timedelta(hours=9), name="JST")
MAX_BYTES = 1024 * 1024
MAX_IDENTITY_BYTES = 32 * 1024 * 1024
MAX_JOURNAL_EVENTS = 256
SHA256 = frozenset("0123456789abcdef")
TERMINAL_PHASES = {"terminal_green", "terminal_degraded", "terminal_major_incident"}


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _seal(body: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(body)
    result["receiptSha256"] = hashlib.sha256(_canonical(result)).hexdigest()
    return result


def _valid_sha(value: object, *, length: int = 64) -> bool:
    text = str(value or "")
    return len(text) == length and set(text) <= SHA256


def _aware(value: object, *, code: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(code) from error
    if parsed.tzinfo is None:
        raise ValueError(code)
    return parsed.astimezone(JST)


def _anchor(issue_date: str) -> datetime:
    try:
        day = date.fromisoformat(issue_date)
    except ValueError as error:
        raise ValueError("AUDIT_RECOVERY_TRANSACTION_INVALID") from error
    return datetime.combine(day, clock_time(6, 40), tzinfo=JST)


def _is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _read_stable_file(path: Path, *, max_bytes: int, code: str) -> bytes:
    """Read a regular file through one descriptor and reject replacement races."""

    candidate = Path(os.path.abspath(path))
    try:
        before = candidate.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or _is_link_or_reparse(candidate)
            or int(getattr(before, "st_nlink", 1)) != 1
            or before.st_size > max_bytes
        ):
            raise ValueError(code)
        descriptor = os.open(
            candidate,
            os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            opened = os.fstat(descriptor)
            if (
                (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                or opened.st_size > max_bytes
            ):
                raise ValueError(code)
            chunks: list[bytes] = []
            remaining = max_bytes + 1
            while remaining > 0:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
            after_handle = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        after = candidate.lstat()
    except (OSError, ValueError) as error:
        raise ValueError(code) from error
    if (
        len(raw) > max_bytes
        or (after_handle.st_dev, after_handle.st_ino, after_handle.st_size)
        != (before.st_dev, before.st_ino, before.st_size)
        or (after.st_dev, after.st_ino, after.st_size)
        != (before.st_dev, before.st_ino, before.st_size)
        or after.st_mtime_ns != before.st_mtime_ns
        or _is_link_or_reparse(candidate)
    ):
        raise ValueError(code)
    return raw


def _managed_root(repo_root: Path, *, create: bool = True) -> Path:
    return verified_storage.validated_managed_root(
        repo_root=repo_root,
        relative_parts=("build", "recovery", "transactions"),
        create=create,
        code="AUDIT_RECOVERY_TRANSACTION_ROOT_INVALID",
    )


@contextlib.contextmanager
def _lock(root: Path, *, repo_root: Path) -> Iterator[None]:
    path = root / ".issue-date-transaction.lock"
    with verified_storage.pinned_directory(
        root,
        anchor=Path(os.path.abspath(repo_root)),
        code="AUDIT_RECOVERY_TRANSACTION_ROOT_INVALID",
    ):
        if _is_link_or_reparse(path):
            raise ValueError("AUDIT_RECOVERY_TRANSACTION_ROOT_INVALID")
        flags = (
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(path, flags, 0o600)
        opened = os.fstat(descriptor)
        path_item = os.lstat(path)
        if (
            (opened.st_dev, opened.st_ino) != (path_item.st_dev, path_item.st_ino)
            or _is_link_or_reparse(path)
            or int(getattr(opened, "st_nlink", 1)) != 1
        ):
            os.close(descriptor)
            raise ValueError("AUDIT_RECOVERY_TRANSACTION_ROOT_INVALID")
        stream = os.fdopen(descriptor, "r+b")
        locked = False
        try:
            if stream.seek(0, os.SEEK_END) == 0:
                stream.write(b"\0")
                stream.flush()
                os.fsync(stream.fileno())
            stream.seek(0)
            deadline = time.monotonic() + 5
            while True:
                try:
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                    break
                except OSError as error:
                    if time.monotonic() >= deadline:
                        raise ValueError("AUDIT_RECOVERY_TRANSACTION_BUSY") from error
                    time.sleep(0.01)
                    stream.seek(0)
            yield
        finally:
            if locked:
                stream.seek(0)
                try:
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
            stream.close()


def _atomic_write(path: Path, value: Mapping[str, Any], *, root: Path) -> None:
    verified_storage.atomic_write_json(
        path,
        value,
        root=root,
        code="AUDIT_RECOVERY_TRANSACTION_ROOT_INVALID",
    )


def _read(path: Path, *, issue_date: str, root: Path) -> dict[str, Any]:
    value = verified_storage.read_json(
        path,
        root=root,
        max_bytes=MAX_BYTES,
        code="AUDIT_RECOVERY_TRANSACTION_INVALID",
    )
    body = {key: item for key, item in value.items() if key != "receiptSha256"}
    if (
        body.get("schemaVersion") != SCHEMA
        or body.get("issueDate") != issue_date
        or value.get("receiptSha256") != hashlib.sha256(_canonical(body)).hexdigest()
        or not isinstance(body.get("fencingToken"), int)
        or int(body["fencingToken"]) <= 0
        or not isinstance(body.get("journal"), list)
        or len(body["journal"]) > MAX_JOURNAL_EVENTS
    ):
        raise ValueError("AUDIT_RECOVERY_TRANSACTION_INVALID")
    return value


def _process_creation_token(pid: int) -> str:
    if pid <= 0:
        return ""
    if os.name != "nt":
        try:
            return Path(f"/proc/{pid}/stat").read_text(encoding="ascii").split()[21]
        except (OSError, IndexError):
            return ""
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.GetProcessTimes.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return ""
    try:
        created = ctypes.c_ulonglong()
        exited = ctypes.c_ulonglong()
        kernel = ctypes.c_ulonglong()
        user = ctypes.c_ulonglong()
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return ""
        return f"{created.value:016x}"
    finally:
        kernel32.CloseHandle(handle)


def _owner_alive(transaction: Mapping[str, Any]) -> bool:
    pid = int(transaction.get("ownerProcessId") or 0)
    expected = str(transaction.get("ownerProcessCreationToken") or "")
    return bool(expected) and _process_creation_token(pid) == expected


def transaction_path(repo_root: Path, issue_date: str) -> Path:
    _anchor(issue_date)
    return _managed_root(repo_root) / f"{issue_date}-audit-recovery-transaction-v2.json"


def validate_transaction_reference(
    *, repo_root: Path, issue_date: str, path: Path
) -> dict[str, Any]:
    expected = transaction_path(repo_root, issue_date)
    candidate = Path(path).resolve(strict=True)
    if candidate != expected.resolve(strict=True):
        raise ValueError("AUDIT_RECOVERY_TRANSACTION_REFERENCE_INVALID")
    return _read(candidate, issue_date=issue_date, root=_managed_root(repo_root))


def begin_owned_operation(
    *,
    repo_root: Path,
    issue_date: str,
    owner_receipt: Mapping[str, Any],
    operation_kind: str,
    cause_receipt_sha256: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """current fencing ownerだけに一回限りの外部mutation開始権を発行する。"""

    if (
        not re.fullmatch(r"[a-z][a-z0-9_]{2,63}", operation_kind)
        or not _valid_sha(cause_receipt_sha256)
    ):
        raise ValueError("AUDIT_RECOVERY_OWNED_OPERATION_INVALID")
    owner_body = {
        key: item for key, item in owner_receipt.items() if key != "receiptSha256"
    }
    if (
        owner_body.get("schemaVersion") != SCHEMA
        or owner_receipt.get("issueDate") != issue_date
        or owner_receipt.get("receiptSha256")
        != hashlib.sha256(_canonical(owner_body)).hexdigest()
    ):
        raise ValueError("AUDIT_RECOVERY_TRANSACTION_INVALID")
    observed = (now or datetime.now(timezone.utc)).astimezone(JST)
    root = _managed_root(repo_root)
    path = root / f"{issue_date}-audit-recovery-transaction-v2.json"
    with _lock(root, repo_root=repo_root):
        current = _read(path, issue_date=issue_date, root=root)
        if (
            current.get("phase") in TERMINAL_PHASES
            or current.get("transactionId") != owner_receipt.get("transactionId")
            or current.get("fencingToken") != owner_receipt.get("fencingToken")
            or current.get("ownerId") != owner_receipt.get("ownerId")
        ):
            raise ValueError("AUDIT_RECOVERY_FENCING_TOKEN_STALE")
        operation_id = hashlib.sha256(
            (
                f"{current['transactionId']}|{current['fencingToken']}|"
                f"{operation_kind}|{cause_receipt_sha256}"
            ).encode("utf-8")
        ).hexdigest()
        if any(
            event.get("event") == "owned_operation_started"
            and event.get("operationId") == operation_id
            for event in current["journal"]
        ):
            raise ValueError("AUDIT_RECOVERY_OWNED_OPERATION_REPLAY_REJECTED")
        journal = list(current["journal"])
        if len(journal) >= MAX_JOURNAL_EVENTS:
            raise ValueError("AUDIT_RECOVERY_JOURNAL_EXHAUSTED")
        journal.append(
            {
                "sequence": len(journal) + 1,
                "event": "owned_operation_started",
                "operationId": operation_id,
                "operationKind": operation_kind,
                "causeReceiptSha256": cause_receipt_sha256,
                "fencingToken": current["fencingToken"],
                "observedAt": observed.isoformat(),
            }
        )
        updated = _seal(
            {
                **{key: item for key, item in current.items() if key != "receiptSha256"},
                "updatedAt": observed.isoformat(),
                "journal": journal,
            }
        )
        _atomic_write(path, updated, root=root)
    return _seal(
        {
            "schemaVersion": "AUDIT_RECOVERY_OWNED_OPERATION_RECEIPT_V1",
            "issueDate": issue_date,
            "transactionId": updated["transactionId"],
            "fencingToken": updated["fencingToken"],
            "ownerId": updated["ownerId"],
            "operationId": operation_id,
            "operationKind": operation_kind,
            "causeReceiptSha256": cause_receipt_sha256,
            "transactionReceiptSha256": updated["receiptSha256"],
            "startedAt": observed.isoformat(),
            "singleUse": True,
        }
    )


def _owned_operation_id(
    transaction: Mapping[str, Any],
    *,
    operation_kind: str,
    cause_receipt_sha256: str,
) -> str:
    return hashlib.sha256(
        (
            f"{transaction['transactionId']}|{transaction['fencingToken']}|"
            f"{operation_kind}|{cause_receipt_sha256}"
        ).encode("utf-8")
    ).hexdigest()


def resume_owned_operation(
    *,
    repo_root: Path,
    issue_date: str,
    owner_receipt: Mapping[str, Any],
    operation_kind: str,
    cause_receipt_sha256: str,
) -> dict[str, Any]:
    """Return a durable operation state without repeating its external effect."""

    if (
        not re.fullmatch(r"[a-z][a-z0-9_]{2,63}", operation_kind)
        or not _valid_sha(cause_receipt_sha256)
    ):
        raise ValueError("AUDIT_RECOVERY_OWNED_OPERATION_INVALID")
    root = _managed_root(repo_root)
    path = root / f"{issue_date}-audit-recovery-transaction-v2.json"
    with _lock(root, repo_root=repo_root):
        current = _read(path, issue_date=issue_date, root=root)
        if (
            current.get("transactionId") != owner_receipt.get("transactionId")
            or current.get("fencingToken") != owner_receipt.get("fencingToken")
            or current.get("ownerId") != owner_receipt.get("ownerId")
        ):
            raise ValueError("AUDIT_RECOVERY_FENCING_TOKEN_STALE")
        operation_id = _owned_operation_id(
            current,
            operation_kind=operation_kind,
            cause_receipt_sha256=cause_receipt_sha256,
        )
        events = [
            event
            for event in current["journal"]
            if event.get("operationId") == operation_id
        ]
        if not any(event.get("event") == "owned_operation_started" for event in events):
            raise ValueError("AUDIT_RECOVERY_OWNED_OPERATION_NOT_STARTED")
        terminal = next(
            (
                event
                for event in reversed(events)
                if event.get("event")
                in {
                    "owned_operation_completed",
                    "owned_operation_failed",
                    "owned_operation_outcome_unknown",
                }
            ),
            None,
        )
        state = (
            str(terminal["event"]).removeprefix("owned_operation_")
            if terminal
            else "started_unresolved"
        )
        return _seal(
            {
                "schemaVersion": "AUDIT_RECOVERY_OWNED_OPERATION_RESUME_V1",
                "issueDate": issue_date,
                "transactionId": current["transactionId"],
                "fencingToken": current["fencingToken"],
                "ownerId": current["ownerId"],
                "operationId": operation_id,
                "operationKind": operation_kind,
                "causeReceiptSha256": cause_receipt_sha256,
                "operationState": state,
                "outcomeReceiptSha256": (
                    str((terminal or {}).get("outcomeReceiptSha256") or "")
                ),
                "externalEffectMayHaveOccurred": state
                in {"started_unresolved", "outcome_unknown"},
            }
        )


def complete_owned_operation(
    *,
    repo_root: Path,
    issue_date: str,
    owner_receipt: Mapping[str, Any],
    operation_receipt: Mapping[str, Any],
    outcome_status: str,
    outcome_receipt_sha256: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Commit an external operation outcome once; exact repeats are idempotent."""

    if outcome_status not in {"completed", "failed", "outcome_unknown"} or not _valid_sha(
        outcome_receipt_sha256
    ):
        raise ValueError("AUDIT_RECOVERY_OWNED_OPERATION_OUTCOME_INVALID")
    receipt_body = {
        key: item for key, item in operation_receipt.items() if key != "receiptSha256"
    }
    if (
        receipt_body.get("schemaVersion")
        not in {
            "AUDIT_RECOVERY_OWNED_OPERATION_RECEIPT_V1",
            "AUDIT_RECOVERY_OWNED_OPERATION_RESUME_V1",
        }
        or operation_receipt.get("receiptSha256")
        != hashlib.sha256(_canonical(receipt_body)).hexdigest()
        or receipt_body.get("issueDate") != issue_date
    ):
        raise ValueError("AUDIT_RECOVERY_OWNED_OPERATION_INVALID")
    observed = (now or datetime.now(timezone.utc)).astimezone(JST)
    root = _managed_root(repo_root)
    path = root / f"{issue_date}-audit-recovery-transaction-v2.json"
    with _lock(root, repo_root=repo_root):
        current = _read(path, issue_date=issue_date, root=root)
        if (
            current.get("transactionId") != owner_receipt.get("transactionId")
            or current.get("fencingToken") != owner_receipt.get("fencingToken")
            or current.get("ownerId") != owner_receipt.get("ownerId")
            or receipt_body.get("transactionId") != current.get("transactionId")
            or receipt_body.get("fencingToken") != current.get("fencingToken")
            or receipt_body.get("ownerId") != current.get("ownerId")
        ):
            raise ValueError("AUDIT_RECOVERY_FENCING_TOKEN_STALE")
        operation_id = str(receipt_body.get("operationId") or "")
        events = [
            event
            for event in current["journal"]
            if event.get("operationId") == operation_id
        ]
        if not any(event.get("event") == "owned_operation_started" for event in events):
            raise ValueError("AUDIT_RECOVERY_OWNED_OPERATION_NOT_STARTED")
        expected_event = f"owned_operation_{outcome_status}"
        prior_terminal = next(
            (
                event
                for event in reversed(events)
                if event.get("event")
                in {
                    "owned_operation_completed",
                    "owned_operation_failed",
                    "owned_operation_outcome_unknown",
                }
            ),
            None,
        )
        if prior_terminal:
            if (
                prior_terminal.get("event") != expected_event
                or prior_terminal.get("outcomeReceiptSha256")
                != outcome_receipt_sha256
            ):
                raise ValueError("AUDIT_RECOVERY_OWNED_OPERATION_OUTCOME_CONFLICT")
            return current
        journal = list(current["journal"])
        if len(journal) >= MAX_JOURNAL_EVENTS:
            raise ValueError("AUDIT_RECOVERY_JOURNAL_EXHAUSTED")
        journal.append(
            {
                "sequence": len(journal) + 1,
                "event": expected_event,
                "operationId": operation_id,
                "operationKind": receipt_body.get("operationKind"),
                "outcomeReceiptSha256": outcome_receipt_sha256,
                "fencingToken": current["fencingToken"],
                "observedAt": observed.isoformat(),
            }
        )
        updated = _seal(
            {
                **{key: item for key, item in current.items() if key != "receiptSha256"},
                "updatedAt": observed.isoformat(),
                "journal": journal,
            }
        )
        _atomic_write(path, updated, root=root)
        return updated


def acquire_or_attach(
    *,
    repo_root: Path,
    issue_date: str,
    trigger: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """active ownerは一つだけにし、他triggerはattach receiptを返す。"""

    if trigger not in {"deadman", "automation", "watcher", "daily_adapter", "direct_cli"}:
        raise ValueError("AUDIT_RECOVERY_TRIGGER_INVALID")
    observed = (now or datetime.now(timezone.utc)).astimezone(JST)
    anchor = _anchor(issue_date)
    root = _managed_root(repo_root)
    path = root / f"{issue_date}-audit-recovery-transaction-v2.json"
    with _lock(root, repo_root=repo_root):
        existing = _read(path, issue_date=issue_date, root=root) if path.exists() else None
        if existing and existing.get("phase") in TERMINAL_PHASES:
            return {
                "mode": "terminal_projection",
                "processExitCode": int(existing.get("processExitCode") or 0),
                "transaction": existing,
            }
        if existing and existing.get("phase") not in {"failed", "stale"}:
            lease = _aware(existing.get("leaseExpiresAt"), code="AUDIT_RECOVERY_TRANSACTION_INVALID")
            if lease > observed or _owner_alive(existing):
                return {"mode": "attached", "processExitCode": 3, "transaction": existing}
        fencing = int((existing or {}).get("fencingToken") or 0) + 1
        pid = os.getpid()
        creation = _process_creation_token(pid)
        if not creation:
            raise ValueError("AUDIT_RECOVERY_OWNER_IDENTITY_INVALID")
        transaction_id = hashlib.sha256(
            f"News-Grasp|{issue_date}|audit-recovery".encode("utf-8")
        ).hexdigest()
        prior_journal = list((existing or {}).get("journal") or [])
        if len(prior_journal) >= MAX_JOURNAL_EVENTS:
            raise ValueError("AUDIT_RECOVERY_JOURNAL_EXHAUSTED")
        event = {
            "sequence": len(prior_journal) + 1,
            "event": "owner_acquired",
            "trigger": trigger,
            "fencingToken": fencing,
            "observedAt": observed.isoformat(),
            "previousReceiptSha256": (existing or {}).get("receiptSha256"),
        }
        transaction = _seal(
            {
                "schemaVersion": SCHEMA,
                "issueDate": issue_date,
                "transactionId": transaction_id,
                "fencingToken": fencing,
                "ownerId": uuid.uuid4().hex,
                "ownerProcessId": pid,
                "ownerProcessCreationToken": creation,
                "phase": "owned_preflight",
                "transactionStartedAt": str(
                    (existing or {}).get("transactionStartedAt") or observed.isoformat()
                ),
                "auditSloAnchor": anchor.isoformat(),
                "preflightDeadline": (
                    (observed if observed < anchor else anchor)
                    + timedelta(minutes=5)
                ).isoformat(),
                "leaseExpiresAt": (observed + timedelta(minutes=5)).isoformat(),
                "hardDeadline": (anchor + timedelta(minutes=90)).isoformat(),
                "updatedAt": observed.isoformat(),
                "journal": [*prior_journal, event],
                "processExitCode": None,
                "terminalProjection": None,
            }
        )
        _atomic_write(path, transaction, root=root)
        return {"mode": "owner", "processExitCode": None, "transaction": transaction}


def finalize(
    *,
    repo_root: Path,
    issue_date: str,
    owner_receipt: Mapping[str, Any],
    terminal_projection: Mapping[str, Any],
    process_exit_code: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """fencing tokenがcurrentのownerだけがterminalを確定する。"""

    observed = (now or datetime.now(timezone.utc)).astimezone(JST)
    root = _managed_root(repo_root)
    path = root / f"{issue_date}-audit-recovery-transaction-v2.json"
    with _lock(root, repo_root=repo_root):
        current = _read(path, issue_date=issue_date, root=root)
        if (
            current.get("transactionId") != owner_receipt.get("transactionId")
            or current.get("fencingToken") != owner_receipt.get("fencingToken")
            or current.get("ownerId") != owner_receipt.get("ownerId")
        ):
            raise ValueError("AUDIT_RECOVERY_FENCING_TOKEN_STALE")
        terminal = str(terminal_projection.get("terminal") or "")
        if terminal in {"audit_normal_green", "audit_recovered_green"} and process_exit_code == 0:
            phase = "terminal_green"
        elif terminal == "audit_major_incident_open":
            phase = "terminal_major_incident"
        else:
            phase = "terminal_degraded"
        journal = list(current["journal"])
        if len(journal) >= MAX_JOURNAL_EVENTS:
            raise ValueError("AUDIT_RECOVERY_JOURNAL_EXHAUSTED")
        journal.append(
            {
                "sequence": len(journal) + 1,
                "event": "terminal_committed",
                "terminal": terminal,
                "fencingToken": current["fencingToken"],
                "observedAt": observed.isoformat(),
            }
        )
        result = _seal(
            {
                **{key: item for key, item in current.items() if key != "receiptSha256"},
                "phase": phase,
                "leaseExpiresAt": observed.isoformat(),
                "updatedAt": observed.isoformat(),
                "journal": journal,
                "processExitCode": int(process_exit_code),
                "terminalProjection": dict(terminal_projection),
            }
        )
        _atomic_write(path, result, root=root)
        return result


def build_readiness_snapshot_v2(
    *, issue_date: str, observations: Mapping[str, Any], observed_at: str
) -> dict[str, Any]:
    """05:55観測を非消費・非authorityの再利用可能snapshotへ固定する。"""

    observed = _aware(observed_at, code="SCHEDULED_READINESS_SNAPSHOT_INVALID")
    if not observations or not all(str(value or "") for value in observations.values()):
        raise ValueError("SCHEDULED_READINESS_SNAPSHOT_INVALID")
    return _seal(
        {
            "schemaVersion": READINESS_SNAPSHOT_SCHEMA,
            "issueDate": issue_date,
            "observedAt": observed.isoformat(),
            "observations": dict(observations),
            "reusable": True,
            "consumable": False,
            "authority": False,
        }
    )


def issue_launch_permit_v2(
    *,
    issue_date: str,
    readiness_snapshot: Mapping[str, Any],
    task_action_sha256: str,
    runner_sha256: str,
    launch_nonce: str,
    broker_authority: Mapping[str, Any],
    mission_authority_v2: Mapping[str, Any] | None = None,
    mission_authority_v2_path: str | None = None,
    mission_authority_v2_file_sha256: str | None = None,
) -> dict[str, Any]:
    """snapshotを参照するがsingle-use authorityはpermit側だけに保持する。"""

    body = {
        key: item for key, item in readiness_snapshot.items() if key != "receiptSha256"
    }
    snapshot_sha = str(readiness_snapshot.get("receiptSha256") or "")
    broker_body = {
        key: item for key, item in broker_authority.items() if key != "receiptSha256"
    }
    broker_sha = str(broker_authority.get("receiptSha256") or "")
    if (
        body.get("schemaVersion") != READINESS_SNAPSHOT_SCHEMA
        or body.get("issueDate") != issue_date
        or body.get("authority") is not False
        or body.get("consumable") is not False
        or snapshot_sha != hashlib.sha256(_canonical(body)).hexdigest()
        or not _valid_sha(snapshot_sha)
        or not _valid_sha(task_action_sha256)
        or not _valid_sha(runner_sha256)
        or not launch_nonce
        or broker_body.get("schemaVersion") != "SCHEDULED_PRODUCTION_LAUNCH_PERMIT_V1"
        or broker_body.get("issueDate") != issue_date
        or broker_body.get("taskActionSha256") != task_action_sha256
        or broker_body.get("runnerSha256") != runner_sha256
        or broker_sha != hashlib.sha256(_canonical(broker_body)).hexdigest()
        or not _valid_sha(broker_sha)
    ):
        raise ValueError("SCHEDULED_PRODUCTION_LAUNCH_PERMIT_V2_INVALID")
    mission_fields: dict[str, Any] = {}
    if mission_authority_v2 is None:
        raise ValueError("AUDIT_MISSION_AUTHORITY_V2_REQUIRED")
    if mission_authority_v2 is not None:
        mission = dict(mission_authority_v2)
        mission_body = {key: item for key, item in mission.items() if key != "receiptSha256"}
        mission_source = mission_body.get("sourceAuthority")
        if (
            mission_body.get("schemaVersion") != "AUDIT_MISSION_AUTHORITY_V2"
            or mission_body.get("auditDecisionSchemaVersion") != "AUDIT_RECOVERY_DECISION_V2"
            or mission_body.get("terminalEnum")
            != [
                "audit_normal_green",
                "audit_recovered_green",
                "audit_observation_unverified",
                "audit_major_incident_open",
            ]
            or not isinstance(mission_source, dict)
            or mission_source.get("schemaVersion") != "AUDIT_MISSION_AUTHORITY_V1"
            or mission_body.get("sourceAuthorityReceiptSha256")
            != mission_source.get("receiptSha256")
            or mission.get("receiptSha256") != hashlib.sha256(_canonical(mission_body)).hexdigest()
            or not _valid_sha(mission.get("receiptSha256"))
            or not _valid_sha(mission_body.get("sourceAuthorityReceiptSha256"))
            or (
                mission_authority_v2_file_sha256 is not None
                and not _valid_sha(mission_authority_v2_file_sha256)
            )
        ):
            raise ValueError("AUDIT_MISSION_AUTHORITY_V2_INVALID")
        mission_fields = {
            "missionAuthorityV2Path": mission_authority_v2_path or None,
            "missionAuthorityV2Sha256": mission["receiptSha256"],
            "missionAuthorityV2FileSha256": mission_authority_v2_file_sha256 or None,
            "missionAuthoritySourceV1Sha256": mission_body[
                "sourceAuthorityReceiptSha256"
            ],
        }
    return _seal(
        {
            "schemaVersion": LAUNCH_PERMIT_SCHEMA,
            "issueDate": issue_date,
            "readinessSnapshotSha256": snapshot_sha,
            "taskActionSha256": task_action_sha256,
            "runnerSha256": runner_sha256,
            "launchNonce": launch_nonce,
            "brokerAuthoritySha256": broker_sha,
            "brokerAuthority": dict(broker_authority),
            **mission_fields,
            "singleUse": True,
            "maxFullE2EAttempts": 0,
        }
    )


def extract_broker_authority(
    permit: object,
    *,
    issue_date: str,
    task_action_sha256: str,
    runner_sha256: str,
    mission_authority_v2_path: Path | None = None,
) -> dict[str, Any]:
    """V2 wrapperとsnapshot bindingを検証してbroker V1 authorityだけを返す。"""

    code = "SCHEDULED_PRODUCTION_LAUNCH_PERMIT_V2_INVALID"
    if not isinstance(permit, dict):
        raise ValueError(code)
    body = {key: item for key, item in permit.items() if key != "receiptSha256"}
    broker = body.get("brokerAuthority")
    if (
        body.get("schemaVersion") != LAUNCH_PERMIT_SCHEMA
        or body.get("issueDate") != issue_date
        or body.get("taskActionSha256") != task_action_sha256
        or body.get("runnerSha256") != runner_sha256
        or body.get("singleUse") is not True
        or not _valid_sha(body.get("readinessSnapshotSha256"))
        or permit.get("receiptSha256") != hashlib.sha256(_canonical(body)).hexdigest()
        or not isinstance(broker, dict)
        or body.get("brokerAuthoritySha256") != broker.get("receiptSha256")
    ):
        raise ValueError(code)
    if mission_authority_v2_path is not None:
        candidate = Path(mission_authority_v2_path)
        try:
            raw = _read_stable_file(
                candidate,
                max_bytes=MAX_BYTES,
                code="AUDIT_MISSION_AUTHORITY_V2_INVALID",
            )
            mission = json.loads(raw.decode("utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("AUDIT_MISSION_AUTHORITY_V2_INVALID") from error
        mission_body = mission if isinstance(mission, dict) else {}
        if (
            candidate.resolve() != Path(str(body["missionAuthorityV2Path"])).resolve()
            or hashlib.sha256(raw).hexdigest() != body["missionAuthorityV2FileSha256"]
            or mission_body.get("receiptSha256") != body["missionAuthorityV2Sha256"]
        ):
            raise ValueError("AUDIT_MISSION_AUTHORITY_V2_INVALID")
    if (
        not _valid_sha(body.get("missionAuthorityV2Sha256"))
        or not _valid_sha(body.get("missionAuthorityV2FileSha256"))
        or not _valid_sha(body.get("missionAuthoritySourceV1Sha256"))
        or not body.get("missionAuthorityV2Path")
    ):
        raise ValueError(code)
    broker_body = {key: item for key, item in broker.items() if key != "receiptSha256"}
    if (
        broker_body.get("schemaVersion") != "SCHEDULED_PRODUCTION_LAUNCH_PERMIT_V1"
        or broker_body.get("issueDate") != issue_date
        or broker_body.get("taskActionSha256") != task_action_sha256
        or broker_body.get("runnerSha256") != runner_sha256
        or broker.get("receiptSha256") != hashlib.sha256(_canonical(broker_body)).hexdigest()
    ):
        raise ValueError(code)
    return dict(broker)


def _load_json(path: Path, *, code: str) -> dict[str, Any]:
    try:
        value = json.loads(
            _read_stable_file(Path(path), max_bytes=MAX_BYTES, code=code).decode(
                "utf-8-sig"
            )
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError(code) from error
    if not isinstance(value, dict):
        raise ValueError(code)
    return value


def _regular_file_identity(path: Path, *, code: str) -> dict[str, str]:
    candidate = Path(os.path.abspath(path))
    try:
        raw = _read_stable_file(
            candidate, max_bytes=MAX_IDENTITY_BYTES, code=code
        )
    except (OSError, ValueError) as error:
        raise ValueError(code) from error
    if not raw:
        raise ValueError(code)
    return {
        "path": str(candidate.resolve(strict=True)),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _regular_root(path: Path, *, code: str) -> str:
    candidate = Path(os.path.abspath(path))
    if not candidate.is_dir() or _is_link_or_reparse(candidate):
        raise ValueError(code)
    return str(candidate.resolve(strict=True))


def issue_actual_launch_identity_v1(
    *,
    output: Path,
    issue_date: str,
    run_id: str,
    run_intent: str,
    process_id: int,
    runner: Path,
    artifact_root: Path,
    ops_root: Path,
    python: Path,
    high_cost_binding_receipt: Path,
    expected_high_cost_binding_receipt_sha256: str,
    launch_authority: Path,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """実06:00 processと検証済みauthority/bindingの同一identityを物理化する。"""

    code = "NEWS_GRASP_ACTUAL_LAUNCH_IDENTITY_INVALID"
    _anchor(issue_date)
    if (
        run_intent != "ScheduledProduction"
        or not re.fullmatch(r"[0-9a-f]{32}", run_id)
        or process_id <= 0
        or not _valid_sha(expected_high_cost_binding_receipt_sha256)
    ):
        raise ValueError(code)
    artifact = Path(_regular_root(artifact_root, code=code))
    ops = Path(_regular_root(ops_root, code=code))
    runner_identity = _regular_file_identity(runner, code=code)
    python_identity = _regular_file_identity(python, code=code)
    binding_identity = _regular_file_identity(high_cost_binding_receipt, code=code)
    authority_identity = _regular_file_identity(launch_authority, code=code)
    binding = _load_json(Path(binding_identity["path"]), code=code)
    authority = _load_json(Path(authority_identity["path"]), code=code)
    authority_body = {
        key: item for key, item in authority.items() if key != "receiptSha256"
    }
    if (
        binding.get("schemaVersion") != "NEWS_GRASP_HIGH_COST_BINDING_V1"
        or binding.get("bindingReceiptSha256")
        != expected_high_cost_binding_receipt_sha256
        or authority_body.get("schemaVersion") != LAUNCH_PERMIT_SCHEMA
        or authority_body.get("issueDate") != issue_date
        or authority_body.get("runnerSha256") != runner_identity["sha256"]
        or authority.get("receiptSha256")
        != hashlib.sha256(_canonical(authority_body)).hexdigest()
        or not _valid_sha(authority_body.get("taskActionSha256"))
        or not _valid_sha(authority_body.get("readinessSnapshotSha256"))
    ):
        raise ValueError(code)
    creation_token = _process_creation_token(process_id)
    if not creation_token:
        raise ValueError(code)
    expected_output = (
        artifact
        / "build"
        / "recovery"
        / "launch-identities"
        / issue_date
        / f"{run_id}.json"
    )
    candidate_output = Path(os.path.abspath(output))
    if candidate_output != expected_output or candidate_output.exists():
        raise ValueError(code)
    verified_storage.validated_managed_root(
        repo_root=artifact,
        relative_parts=("build", "recovery", "launch-identities", issue_date),
        create=True,
        code=code,
    )
    observed = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    result = _seal(
        {
            "schemaVersion": ACTUAL_LAUNCH_IDENTITY_SCHEMA,
            "issueDate": issue_date,
            "runId": run_id,
            "runIntent": run_intent,
            "process": {
                "processId": process_id,
                "creationToken": creation_token,
            },
            "roots": {
                "artifactRoot": str(artifact),
                "opsRoot": str(ops),
            },
            "runner": runner_identity,
            "python": python_identity,
            "highCostBinding": {
                **binding_identity,
                "bindingReceiptSha256": expected_high_cost_binding_receipt_sha256,
            },
            "launchAuthority": {
                **authority_identity,
                "receiptSha256": authority["receiptSha256"],
            },
            "receiptPath": str(candidate_output),
            "observedAt": observed.isoformat(),
            "mutationScope": "identity_receipt_only",
            "externalCallCount": 0,
            "scheduledTaskMutationCount": 0,
        }
    )
    verified_storage.atomic_write_json(
        candidate_output, result, root=artifact, code=code
    )
    return result


def validate_actual_launch_identity_v1(
    value: object,
    *,
    receipt_path: Path,
    issue_date: str,
    run_id: str,
    process_id: int,
    artifact_root: Path,
    current_task_action_sha256: str,
    require_process_alive: bool = True,
) -> dict[str, Any]:
    """発行済みidentityをcurrent bytes・PID creation tokenまで再検証する。"""

    code = "NEWS_GRASP_ACTUAL_LAUNCH_IDENTITY_INVALID"
    artifact = Path(_regular_root(artifact_root, code=code))
    expected_path = (
        artifact
        / "build"
        / "recovery"
        / "launch-identities"
        / issue_date
        / f"{run_id}.json"
    )
    actual_path = Path(os.path.abspath(receipt_path))
    if (
        actual_path != expected_path
        or not actual_path.is_file()
        or _is_link_or_reparse(actual_path)
        or not isinstance(value, dict)
    ):
        raise ValueError(code)
    body = {key: item for key, item in value.items() if key != "receiptSha256"}
    process = body.get("process")
    roots = body.get("roots")
    runner = body.get("runner")
    python = body.get("python")
    binding_identity = body.get("highCostBinding")
    authority_identity = body.get("launchAuthority")
    if (
        body.get("schemaVersion") != ACTUAL_LAUNCH_IDENTITY_SCHEMA
        or body.get("issueDate") != issue_date
        or body.get("runId") != run_id
        or body.get("runIntent") != "ScheduledProduction"
        or value.get("receiptSha256") != hashlib.sha256(_canonical(body)).hexdigest()
        or body.get("receiptPath") != str(actual_path)
        or not isinstance(process, dict)
        or int(process.get("processId") or 0) != process_id
        or not str(process.get("creationToken") or "")
        or not isinstance(roots, dict)
        or Path(str(roots.get("artifactRoot") or "")) != artifact
        or not isinstance(runner, dict)
        or not isinstance(python, dict)
        or not isinstance(binding_identity, dict)
        or not isinstance(authority_identity, dict)
        or not _valid_sha(current_task_action_sha256)
    ):
        raise ValueError(code)
    current_runner = _regular_file_identity(Path(str(runner.get("path") or "")), code=code)
    current_python = _regular_file_identity(Path(str(python.get("path") or "")), code=code)
    current_binding = _regular_file_identity(
        Path(str(binding_identity.get("path") or "")), code=code
    )
    current_authority = _regular_file_identity(
        Path(str(authority_identity.get("path") or "")), code=code
    )
    binding = _load_json(Path(current_binding["path"]), code=code)
    authority = _load_json(Path(current_authority["path"]), code=code)
    authority_body = {
        key: item for key, item in authority.items() if key != "receiptSha256"
    }
    if (
        current_runner != runner
        or current_python != python
        or current_binding["path"] != binding_identity.get("path")
        or current_binding["sha256"] != binding_identity.get("sha256")
        or current_authority["path"] != authority_identity.get("path")
        or current_authority["sha256"] != authority_identity.get("sha256")
        or binding.get("schemaVersion") != "NEWS_GRASP_HIGH_COST_BINDING_V1"
        or binding.get("bindingReceiptSha256")
        != binding_identity.get("bindingReceiptSha256")
        or authority_body.get("schemaVersion") != LAUNCH_PERMIT_SCHEMA
        or authority_body.get("issueDate") != issue_date
        or authority_body.get("runnerSha256") != runner.get("sha256")
        or authority_body.get("taskActionSha256") != current_task_action_sha256
        or authority.get("receiptSha256")
        != hashlib.sha256(_canonical(authority_body)).hexdigest()
        or authority.get("receiptSha256") != authority_identity.get("receiptSha256")
    ):
        raise ValueError(code)
    current_token = _process_creation_token(process_id)
    expected_token = str(process.get("creationToken") or "")
    if current_token and current_token != expected_token:
        raise ValueError(code)
    process_alive = current_token == expected_token
    if require_process_alive and not process_alive:
        raise ValueError(code)
    return {**dict(value), "processAlive": process_alive}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    snapshot = sub.add_parser("build-readiness-snapshot-v2")
    snapshot.add_argument("--issue-date", required=True)
    snapshot.add_argument("--observed-at", required=True)
    snapshot.add_argument("--observations", type=Path, required=True)
    wrap = sub.add_parser("wrap-launch-permit-v2")
    wrap.add_argument("--issue-date", required=True)
    wrap.add_argument("--snapshot", type=Path, required=True)
    wrap.add_argument("--broker-authority", type=Path, required=True)
    wrap.add_argument("--task-action-sha256", required=True)
    wrap.add_argument("--runner-sha256", required=True)
    wrap.add_argument("--launch-nonce", required=True)
    wrap.add_argument("--mission-authority-v2", type=Path)
    extract = sub.add_parser("extract-broker-authority")
    extract.add_argument("--permit", type=Path, required=True)
    extract.add_argument("--issue-date", required=True)
    extract.add_argument("--task-action-sha256", required=True)
    extract.add_argument("--runner-sha256", required=True)
    extract.add_argument("--mission-authority-v2", type=Path)
    identity = sub.add_parser("issue-launch-identity")
    identity.add_argument("--output", type=Path, required=True)
    identity.add_argument("--issue-date", required=True)
    identity.add_argument("--run-id", required=True)
    identity.add_argument("--run-intent", required=True)
    identity.add_argument("--process-id", type=int, required=True)
    identity.add_argument("--runner", type=Path, required=True)
    identity.add_argument("--artifact-root", type=Path, required=True)
    identity.add_argument("--ops-root", type=Path, required=True)
    identity.add_argument("--python", type=Path, required=True)
    identity.add_argument("--high-cost-binding-receipt", type=Path, required=True)
    identity.add_argument(
        "--expected-high-cost-binding-receipt-sha256", required=True
    )
    identity.add_argument("--launch-authority", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "build-readiness-snapshot-v2":
        observations = _load_json(
            args.observations, code="SCHEDULED_READINESS_SNAPSHOT_INVALID"
        )
        result = build_readiness_snapshot_v2(
            issue_date=args.issue_date,
            observations=observations,
            observed_at=args.observed_at,
        )
    elif args.command == "wrap-launch-permit-v2":
        mission = None
        mission_file_sha = None
        mission_path = None
        if args.mission_authority_v2:
            mission_path = args.mission_authority_v2
            mission = _load_json(
                mission_path, code="AUDIT_MISSION_AUTHORITY_V2_INVALID"
            )
            mission_file_sha = hashlib.sha256(
                _read_stable_file(
                    mission_path,
                    max_bytes=MAX_BYTES,
                    code="AUDIT_MISSION_AUTHORITY_V2_INVALID",
                )
            ).hexdigest()
        result = issue_launch_permit_v2(
            issue_date=args.issue_date,
            readiness_snapshot=_load_json(
                args.snapshot, code="SCHEDULED_READINESS_SNAPSHOT_INVALID"
            ),
            task_action_sha256=args.task_action_sha256,
            runner_sha256=args.runner_sha256,
            launch_nonce=args.launch_nonce,
            broker_authority=_load_json(
                args.broker_authority,
                code="SCHEDULED_PRODUCTION_LAUNCH_PERMIT_V2_INVALID",
            ),
            mission_authority_v2=mission,
            mission_authority_v2_path=(str(mission_path.resolve()) if mission_path else None),
            mission_authority_v2_file_sha256=mission_file_sha,
        )
    elif args.command == "extract-broker-authority":
        result = extract_broker_authority(
            _load_json(
                args.permit,
                code="SCHEDULED_PRODUCTION_LAUNCH_PERMIT_V2_INVALID",
            ),
            issue_date=args.issue_date,
            task_action_sha256=args.task_action_sha256,
            runner_sha256=args.runner_sha256,
            mission_authority_v2_path=args.mission_authority_v2,
        )
    else:
        result = issue_actual_launch_identity_v1(
            output=args.output,
            issue_date=args.issue_date,
            run_id=args.run_id,
            run_intent=args.run_intent,
            process_id=args.process_id,
            runner=args.runner,
            artifact_root=args.artifact_root,
            ops_root=args.ops_root,
            python=args.python,
            high_cost_binding_receipt=args.high_cost_binding_receipt,
            expected_high_cost_binding_receipt_sha256=(
                args.expected_high_cost_binding_receipt_sha256
            ),
            launch_authority=args.launch_authority,
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2) from None
