"""S1 の durable WAL primitives。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import uuid
from typing import Any, Callable, Mapping, Sequence

from .news_grasp_cleanroom_contracts import (
    CleanroomEntryError,
    _entry_canonical_sha256,
    _writer_owner_key,
)


INITIAL_WAL_FAILED = "NEWS_GRASP_ENTRY_INITIAL_WAL_FAILED"
WAL_FINALIZE_FAILED = "NEWS_GRASP_ENTRY_WAL_FINALIZE_FAILED"
LEDGER_CORRUPT = "NEWS_GRASP_ENTRY_LEDGER_CORRUPT"
_ZERO_HASH = "0" * 64


def _fsync_real(fd: int) -> None:
    """Windows Python の fsync(2) 不実装環境でも FlushFileBuffers を使う。"""
    try:
        os.fsync(fd)
        return
    except OSError as exc:
        if os.name != "nt" or exc.errno != 9:
            raise
    import ctypes
    import msvcrt

    handle = msvcrt.get_osfhandle(fd)
    if handle == -1 or not ctypes.windll.kernel32.FlushFileBuffers(handle):
        raise OSError("FlushFileBuffers failed")


def _flush_parent_real(directory: Path) -> None:
    """親 directory の durable flush を OS 境界で実行する。"""
    directory = Path(directory)
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        create_file.restype = wintypes.HANDLE
        handle = create_file(str(directory), 0xC0000000, 0x00000007, None, 3, 0x02000000, None)
        if handle == wintypes.HANDLE(-1).value:
            raise OSError(ctypes.get_last_error(), "CreateFileW directory flush failed")
        try:
            if not kernel32.FlushFileBuffers(handle):
                raise OSError(ctypes.get_last_error(), "FlushFileBuffers failed")
        finally:
            kernel32.CloseHandle(handle)
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@dataclass(frozen=True)
class DurabilityOps:
    """filesystem durability operations; injection is negative-test-only."""

    fsync: Callable[[int], Any] = os.fsync
    replace: Callable[[str | os.PathLike[str], str | os.PathLike[str]], Any] = os.replace
    flush_parent: Callable[[Path], Any] = _flush_parent_real


def _event_hash(event: Mapping[str, Any]) -> str:
    unsigned = {key: value for key, value in event.items() if key != "eventSha256"}
    return _entry_canonical_sha256(unsigned)


def _write_json(path: Path, payload: Mapping[str, Any], operations: DurabilityOps, reason: str) -> None:
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


def _read_event(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CleanroomEntryError(LEDGER_CORRUPT, f"invalid WAL JSON: {path}") from exc
    if not isinstance(value, dict) or value.get("schemaVersion") != "WAL_EVENT_V1":
        raise CleanroomEntryError(LEDGER_CORRUPT, f"invalid WAL event: {path}")
    if value.get("eventSha256") != _event_hash(value):
        raise CleanroomEntryError(LEDGER_CORRUPT, f"WAL event hash drift: {path}")
    return value


class DurableWal:
    """initial/imported WAL の durable append-only facade。"""

    def __init__(self, runtime_root: Path, *, durability_ops: DurabilityOps | None = None):
        self.runtime_root = Path(runtime_root)
        self.control_root = self.runtime_root / "control"
        self.wal_root = self.control_root / "wal"
        self.operations = durability_ops or DurabilityOps()

    def record_initial(
        self,
        *,
        raw_argv: Sequence[str],
        received_at: datetime,
        writer: Mapping[str, Any],
    ) -> dict[str, Any]:
        invocation_id = uuid.uuid4().hex
        writer_value = dict(writer)
        _writer_owner_key(writer_value)
        raw_argv_value = list(raw_argv)
        event: dict[str, Any] = {
            "schemaVersion": "WAL_EVENT_V1",
            "eventType": "INVOCATION_RECEIVED",
            "phase": "INITIAL_DURABLE",
            "invocationId": invocation_id,
            "sequence": 1,
            "receivedAt": received_at.isoformat(),
            "rawArgv": raw_argv_value,
            "rawArgvSha256": _entry_canonical_sha256(raw_argv_value),
            "writer": writer_value,
            "previousEventSha256": _ZERO_HASH,
        }
        event["eventSha256"] = _event_hash(event)
        path = self.wal_root / invocation_id / "0001-initial.json"
        _write_json(path, event, self.operations, INITIAL_WAL_FAILED)
        return event

    def _initial_paths(self) -> list[Path]:
        if not self.wal_root.exists():
            return []
        return sorted(self.wal_root.glob("*/0001-initial.json"))

    def iter_zero_entries(self) -> tuple[dict[str, Any], ...]:
        zero: list[dict[str, Any]] = []
        for initial_path in self._initial_paths():
            event = _read_event(initial_path)
            imported_path = initial_path.with_name("0002-imported.json")
            if not imported_path.exists():
                zero.append(event)
        return tuple(zero)

    def mark_imported(self, initial_event: Mapping[str, Any], *, imported_at: datetime) -> dict[str, Any]:
        initial = dict(initial_event)
        invocation_id = initial.get("invocationId")
        if not isinstance(invocation_id, str) or not invocation_id:
            raise CleanroomEntryError(LEDGER_CORRUPT, "initial WAL invocationId is invalid")
        imported_path = self.wal_root / invocation_id / "0002-imported.json"
        if imported_path.exists():
            existing = _read_event(imported_path)
            if existing.get("previousEventSha256") != initial.get("eventSha256"):
                raise CleanroomEntryError(LEDGER_CORRUPT, "imported WAL predecessor drift")
            return existing
        event: dict[str, Any] = {
            "schemaVersion": "WAL_EVENT_V1",
            "eventType": "INVOCATION_IMPORTED",
            "phase": "LEDGER_IMPORTED",
            "invocationId": invocation_id,
            "sequence": 2,
            "receivedAt": imported_at.isoformat(),
            "rawArgv": initial.get("rawArgv"),
            "rawArgvSha256": initial.get("rawArgvSha256"),
            "writer": initial.get("writer"),
            "previousEventSha256": initial.get("eventSha256"),
        }
        event["eventSha256"] = _event_hash(event)
        try:
            _write_json(imported_path, event, self.operations, WAL_FINALIZE_FAILED)
        except CleanroomEntryError as exc:
            # Another controller may have published the same immutable marker
            # between the existence check and os.replace on Windows.
            if exc.reason == WAL_FINALIZE_FAILED and imported_path.exists():
                existing = _read_event(imported_path)
                if existing.get("previousEventSha256") == initial.get("eventSha256"):
                    return existing
            raise
        return event

    def verify(self) -> dict[str, Any]:
        for initial_path in self._initial_paths():
            initial = _read_event(initial_path)
            imported_path = initial_path.with_name("0002-imported.json")
            if imported_path.exists():
                imported = _read_event(imported_path)
                if imported.get("invocationId") != initial.get("invocationId") or imported.get("sequence") != 2:
                    raise CleanroomEntryError(LEDGER_CORRUPT, "WAL sequence or invocation mismatch")
                if imported.get("previousEventSha256") != initial.get("eventSha256"):
                    raise CleanroomEntryError(LEDGER_CORRUPT, "WAL event chain mismatch")
        return {"status": "verified", "zeroEntryCount": len(self.iter_zero_entries())}
