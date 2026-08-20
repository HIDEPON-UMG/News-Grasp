"""S1 の durable WAL primitives。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import re
import uuid
from typing import Any, Callable, Mapping, Sequence
from zoneinfo import ZoneInfo

from .news_grasp_cleanroom_contracts import (
    CleanroomEntryError,
    ENTRY_ARGS_INVALID,
    ENTRY_TIME_INVALID,
    _entry_canonical_sha256,
    _managed_runtime_path,
    _validate_entry_time,
    _validate_entry_writer,
    _writer_owner_key,
)


INITIAL_WAL_FAILED = "NEWS_GRASP_ENTRY_INITIAL_WAL_FAILED"
WAL_FINALIZE_FAILED = "NEWS_GRASP_ENTRY_WAL_FINALIZE_FAILED"
LEDGER_CORRUPT = "NEWS_GRASP_ENTRY_LEDGER_CORRUPT"
_ZERO_HASH = "0" * 64
_INVOCATION_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_WAL_KEYS = frozenset(
    {
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
)


class _CreateOnceCollision(Exception):
    """create-once 公開先の既存衝突だけを内部で伝播する。"""


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


def _publish_create_once_real(
    source: str | os.PathLike[str], destination: str | os.PathLike[str]
) -> None:
    """既存の公開先を置換せず、同一ディレクトリへ一度だけ公開する。"""
    if os.name == "nt":
        os.rename(source, destination)
        return
    try:
        os.link(source, destination)
    except OSError as exc:
        if getattr(exc, "errno", None) == 17:
            raise FileExistsError(str(destination)) from exc
        raise
    os.unlink(source)


@dataclass(frozen=True)
class DurabilityOps:
    """filesystem durability operations; injection is negative-test-only."""

    fsync: Callable[[int], Any] = os.fsync
    replace: Callable[[str | os.PathLike[str], str | os.PathLike[str]], Any] = os.replace
    flush_parent: Callable[[Path], Any] = _flush_parent_real
    publish_create_once: Callable[[str | os.PathLike[str], str | os.PathLike[str]], Any] = _publish_create_once_real


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
        if path.name == "0002-imported.json":
            try:
                operations.publish_create_once(temp, path)
            except FileExistsError as exc:
                raise _CreateOnceCollision from exc
        else:
            operations.replace(temp, path)
        operations.flush_parent(path.parent)
    except _CreateOnceCollision:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    except Exception as exc:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        if isinstance(exc, CleanroomEntryError):
            raise
        raise CleanroomEntryError(reason, str(exc)) from exc


def _read_event(path: Path, *, event_type: str, phase: str, sequence: int) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CleanroomEntryError(LEDGER_CORRUPT, f"invalid WAL JSON: {path}") from exc
    if not isinstance(value, dict) or set(value) != _WAL_KEYS or value.get("schemaVersion") != "WAL_EVENT_V1":
        raise CleanroomEntryError(LEDGER_CORRUPT, f"invalid WAL event: {path}")
    if value.get("eventType") != event_type or value.get("phase") != phase:
        raise CleanroomEntryError(LEDGER_CORRUPT, f"invalid WAL event state: {path}")
    if isinstance(value.get("sequence"), bool) or not isinstance(value.get("sequence"), int) or value["sequence"] != sequence:
        raise CleanroomEntryError(LEDGER_CORRUPT, f"invalid WAL event sequence: {path}")
    if not isinstance(value.get("invocationId"), str) or _INVOCATION_ID_RE.fullmatch(value["invocationId"]) is None:
        raise CleanroomEntryError(LEDGER_CORRUPT, f"invalid WAL invocation id: {path}")
    if not isinstance(value.get("receivedAt"), str):
        raise CleanroomEntryError(LEDGER_CORRUPT, f"invalid WAL receivedAt: {path}")
    try:
        received_at = datetime.fromisoformat(value["receivedAt"])
        if received_at.tzinfo is None or received_at.utcoffset() != timedelta(hours=9) or received_at.fold != 0:
            raise ValueError("WAL receivedAt timezone is invalid")
        _validate_entry_time(received_at.astimezone(ZoneInfo("Asia/Tokyo")))
    except (TypeError, ValueError, CleanroomEntryError) as exc:
        raise CleanroomEntryError(LEDGER_CORRUPT, f"invalid WAL receivedAt: {path}") from exc
    if not isinstance(value.get("rawArgv"), list):
        raise CleanroomEntryError(LEDGER_CORRUPT, f"invalid WAL raw argv: {path}")
    try:
        raw_hash = _entry_canonical_sha256(value["rawArgv"])
    except CleanroomEntryError as exc:
        raise CleanroomEntryError(LEDGER_CORRUPT, f"invalid WAL raw argv: {path}") from exc
    if not isinstance(value.get("rawArgvSha256"), str) or _HASH_RE.fullmatch(value["rawArgvSha256"]) is None or value["rawArgvSha256"] != raw_hash:
        raise CleanroomEntryError(LEDGER_CORRUPT, f"invalid WAL raw argv hash: {path}")
    if not isinstance(value.get("writer"), dict):
        raise CleanroomEntryError(LEDGER_CORRUPT, f"invalid WAL writer: {path}")
    try:
        _validate_entry_writer(value["writer"])
    except CleanroomEntryError as exc:
        raise CleanroomEntryError(LEDGER_CORRUPT, f"invalid WAL writer: {path}") from exc
    if not isinstance(value.get("previousEventSha256"), str) or _HASH_RE.fullmatch(value["previousEventSha256"]) is None:
        raise CleanroomEntryError(LEDGER_CORRUPT, f"invalid WAL predecessor: {path}")
    if event_type == "INVOCATION_RECEIVED" and value["previousEventSha256"] != _ZERO_HASH:
        raise CleanroomEntryError(LEDGER_CORRUPT, f"invalid WAL initial predecessor: {path}")
    if not isinstance(value.get("eventSha256"), str) or _HASH_RE.fullmatch(value["eventSha256"]) is None:
        raise CleanroomEntryError(LEDGER_CORRUPT, f"invalid WAL event hash: {path}")
    if value.get("eventSha256") != _event_hash(value):
        raise CleanroomEntryError(LEDGER_CORRUPT, f"WAL event hash drift: {path}")
    return value


def _validate_imported_parity(initial: Mapping[str, Any], imported: Mapping[str, Any]) -> dict[str, Any]:
    if (
        imported.get("invocationId") != initial.get("invocationId")
        or imported.get("rawArgv") != initial.get("rawArgv")
        or imported.get("rawArgvSha256") != initial.get("rawArgvSha256")
        or imported.get("writer") != initial.get("writer")
        or imported.get("previousEventSha256") != initial.get("eventSha256")
    ):
        raise CleanroomEntryError(LEDGER_CORRUPT, "WAL imported parity mismatch")
    return dict(imported)


class DurableWal:
    """initial/imported WAL の durable append-only facade。"""

    def __init__(self, runtime_root: Path, *, durability_ops: DurabilityOps | None = None):
        self.runtime_root = Path(runtime_root)
        self.control_root = _managed_runtime_path(self.runtime_root, self.runtime_root / "control")
        self.wal_root = _managed_runtime_path(self.runtime_root, self.control_root / "wal")
        self.operations = durability_ops or DurabilityOps()

    def _managed(self, path: Path) -> Path:
        return _managed_runtime_path(self.runtime_root, path)

    def record_initial(
        self,
        *,
        raw_argv: Sequence[str],
        received_at: datetime,
        writer: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(received_at, datetime):
            raise CleanroomEntryError(ENTRY_TIME_INVALID, "received_at must be a datetime")
        if isinstance(raw_argv, (str, bytes)):
            raise CleanroomEntryError(ENTRY_ARGS_INVALID, "raw argv must be a non-string iterable")
        try:
            raw_argv_value = list(raw_argv)
            json.dumps(raw_argv_value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise CleanroomEntryError(ENTRY_ARGS_INVALID, "raw argv must be JSON serializable") from exc
        invocation_id = uuid.uuid4().hex
        try:
            writer_value = dict(writer)
            _writer_owner_key(writer_value)
        except (TypeError, ValueError) as exc:
            raise CleanroomEntryError(ENTRY_ARGS_INVALID, "writer is invalid") from exc
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
        path = self._managed(self.wal_root / invocation_id / "0001-initial.json")
        _write_json(path, event, self.operations, INITIAL_WAL_FAILED)
        return event

    def _initial_paths(self) -> list[Path]:
        if not self.wal_root.exists():
            return []
        paths = sorted(self.wal_root.glob("*/0001-initial.json"))
        return [self._managed(path) for path in paths]

    def iter_zero_entries(self) -> tuple[dict[str, Any], ...]:
        zero: list[dict[str, Any]] = []
        for initial_path in self._initial_paths():
            event = _read_event(initial_path, event_type="INVOCATION_RECEIVED", phase="INITIAL_DURABLE", sequence=1)
            if initial_path.parent.name != event["invocationId"]:
                raise CleanroomEntryError(LEDGER_CORRUPT, "WAL directory invocation mismatch")
            imported_path = self._managed(initial_path.with_name("0002-imported.json"))
            if not imported_path.exists():
                zero.append(event)
            else:
                imported = _read_event(imported_path, event_type="INVOCATION_IMPORTED", phase="LEDGER_IMPORTED", sequence=2)
                _validate_imported_parity(event, imported)
        return tuple(zero)

    def mark_imported(self, initial_event: Mapping[str, Any], *, imported_at: datetime) -> dict[str, Any]:
        imported_observed = _validate_entry_time(imported_at)
        initial = dict(initial_event)
        invocation_id = initial.get("invocationId")
        if not isinstance(invocation_id, str) or _INVOCATION_ID_RE.fullmatch(invocation_id) is None:
            raise CleanroomEntryError(LEDGER_CORRUPT, "initial WAL invocationId is invalid")
        initial_path = self._managed(self.wal_root / invocation_id / "0001-initial.json")
        imported_path = self._managed(initial_path.with_name("0002-imported.json"))
        initial = _read_event(initial_path, event_type="INVOCATION_RECEIVED", phase="INITIAL_DURABLE", sequence=1)
        if initial_path.parent.name != invocation_id:
            raise CleanroomEntryError(LEDGER_CORRUPT, "initial WAL directory mismatch")
        if imported_path.exists():
            existing = _read_event(imported_path, event_type="INVOCATION_IMPORTED", phase="LEDGER_IMPORTED", sequence=2)
            return _validate_imported_parity(initial, existing)
        event: dict[str, Any] = {
            "schemaVersion": "WAL_EVENT_V1",
            "eventType": "INVOCATION_IMPORTED",
            "phase": "LEDGER_IMPORTED",
            "invocationId": invocation_id,
            "sequence": 2,
            "receivedAt": imported_observed.isoformat(),
            "rawArgv": initial.get("rawArgv"),
            "rawArgvSha256": initial.get("rawArgvSha256"),
            "writer": initial.get("writer"),
            "previousEventSha256": initial.get("eventSha256"),
        }
        event["eventSha256"] = _event_hash(event)
        try:
            _write_json(imported_path, event, self.operations, WAL_FINALIZE_FAILED)
        except _CreateOnceCollision:
            existing = _read_event(imported_path, event_type="INVOCATION_IMPORTED", phase="LEDGER_IMPORTED", sequence=2)
            return _validate_imported_parity(initial, existing)
        return event

    def verify(self) -> dict[str, Any]:
        for initial_path in self._initial_paths():
            initial = _read_event(initial_path, event_type="INVOCATION_RECEIVED", phase="INITIAL_DURABLE", sequence=1)
            if initial_path.parent.name != initial.get("invocationId"):
                raise CleanroomEntryError(LEDGER_CORRUPT, "WAL directory invocation mismatch")
            imported_path = self._managed(initial_path.with_name("0002-imported.json"))
            if imported_path.exists():
                imported = _read_event(imported_path, event_type="INVOCATION_IMPORTED", phase="LEDGER_IMPORTED", sequence=2)
                _validate_imported_parity(initial, imported)
        return {"status": "verified", "zeroEntryCount": len(self.iter_zero_entries())}
