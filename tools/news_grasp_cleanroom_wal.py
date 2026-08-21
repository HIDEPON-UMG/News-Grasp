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
WAL_RETENTION_LIMIT = "NEWS_GRASP_ENTRY_WAL_RETENTION_LIMIT"
WAL_COMPACTION_AUTHORIZED = "WAL_COMPACTION_AUTHORIZED"
WAL_COMPACTION_COMPLETED = "WAL_COMPACTION_COMPLETED"
MAX_WAL_EVENT_BYTES = 65536
MAX_WAL_ZERO_ENTRIES = 32
MAX_WAL_IMPORTED_ENTRIES = 800
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
    try:
        os.link(source, destination)
    except OSError as exc:
        if getattr(exc, "errno", None) == 17 or getattr(exc, "winerror", None) == 183:
            raise FileExistsError(str(destination)) from exc
        raise
    os.unlink(source)


def _remove_real(path: str | os.PathLike[str]) -> None:
    target = Path(path)
    if target.is_dir():
        target.rmdir()
    else:
        target.unlink()


@dataclass(frozen=True)
class DurabilityOps:
    """filesystem durability operations; injection is negative-test-only."""

    fsync: Callable[[int], Any] = os.fsync
    replace: Callable[[str | os.PathLike[str], str | os.PathLike[str]], Any] = os.replace
    flush_parent: Callable[[Path], Any] = _flush_parent_real
    publish_create_once: Callable[[str | os.PathLike[str], str | os.PathLike[str]], Any] = _publish_create_once_real
    remove: Callable[[str | os.PathLike[str]], Any] = _remove_real


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
        raw = path.read_bytes()
        if len(raw) > MAX_WAL_EVENT_BYTES:
            raise CleanroomEntryError(WAL_RETENTION_LIMIT, f"WAL event exceeds {MAX_WAL_EVENT_BYTES} bytes: {path}")
        value = json.loads(raw.decode("utf-8"))
    except CleanroomEntryError:
        raise
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
        # WAL のファイルツリーを作る前に、eventSha256 を含む完全な event
        # を実際に書き込む canonical UTF-8 bytes へ固定し、上限を検査する。
        # ここで拒否すれば _write_json (mkdir を含む) は一度も実行されず、
        # oversized event が空の WAL tree を残すこともない。
        serialized_event = json.dumps(
            event,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(serialized_event) > MAX_WAL_EVENT_BYTES:
            raise CleanroomEntryError(
                WAL_RETENTION_LIMIT,
                f"WAL event exceeds {MAX_WAL_EVENT_BYTES} bytes",
            )
        path = self._managed(self.wal_root / invocation_id / "0001-initial.json")
        _write_json(path, event, self.operations, INITIAL_WAL_FAILED)
        return event

    def _initial_paths(self) -> list[Path]:
        if not self.wal_root.exists():
            return []
        paths = sorted(self.wal_root.glob("*/0001-initial.json"))
        return [self._managed(path) for path in paths]

    def _all_imported_events(self) -> tuple[dict[str, Any], ...]:
        """cap検査を迂回せず、明示compactionのparity計算用に全イベントを読む。"""
        events: list[dict[str, Any]] = []
        for initial_path in self._initial_paths():
            initial = _read_event(initial_path, event_type="INVOCATION_RECEIVED", phase="INITIAL_DURABLE", sequence=1)
            if initial_path.parent.name != initial["invocationId"]:
                raise CleanroomEntryError(LEDGER_CORRUPT, "WAL directory invocation mismatch")
            imported_path = self._managed(initial_path.with_name("0002-imported.json"))
            if not imported_path.exists():
                continue
            imported = _read_event(imported_path, event_type="INVOCATION_IMPORTED", phase="LEDGER_IMPORTED", sequence=2)
            _validate_imported_parity(initial, imported)
            imported["_initialPath"] = str(initial_path)
            imported["_importedPath"] = str(imported_path)
            events.append(imported)
        events.sort(key=lambda event: (str(event.get("receivedAt", "")), str(event.get("invocationId", ""))))
        return tuple(events)

    def imported_event_records(self) -> tuple[dict[str, Any], ...]:
        """検証済みimportedイベントを返す明示compaction用のread-only API。"""
        return self._all_imported_events()

    def retention_counts(self) -> dict[str, int]:
        """WAL retentionの明示operation用にzero/imported件数を返す。"""
        imported = len(self._all_imported_events())
        zero = 0
        for initial_path in self._initial_paths():
            initial = _read_event(initial_path, event_type="INVOCATION_RECEIVED", phase="INITIAL_DURABLE", sequence=1)
            if not self._managed(initial_path.with_name("0002-imported.json")).exists():
                zero += 1
        return {"zeroEntryCount": zero, "importedEntryCount": imported}

    def _read_compaction_head(self) -> dict[str, Any] | None:
        path = self._managed(self.wal_root / "compaction-head-v1.json")
        if not path.exists():
            return None
        try:
            raw = path.read_bytes()
            if len(raw) > MAX_WAL_EVENT_BYTES:
                raise CleanroomEntryError(WAL_RETENTION_LIMIT, "WAL compaction receipt exceeds size limit")
            value = json.loads(raw.decode("utf-8"))
        except CleanroomEntryError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CleanroomEntryError(WAL_RETENTION_LIMIT, "WAL compaction receipt is unreadable") from exc
        if not isinstance(value, dict) or value.get("schemaVersion") != "WAL_COMPACTION_HEAD_V1":
            raise CleanroomEntryError(WAL_RETENTION_LIMIT, "WAL compaction receipt schema is invalid")
        digest = value.get("selfHash")
        unsigned = {key: item for key, item in value.items() if key != "selfHash"}
        if not isinstance(digest, str) or digest != _entry_canonical_sha256(unsigned):
            raise CleanroomEntryError(WAL_RETENTION_LIMIT, "WAL compaction receipt hash drift")
        previous = value.get("previousReceipt")
        if not isinstance(previous, str) or _HASH_RE.fullmatch(previous) is None:
            raise CleanroomEntryError(WAL_RETENTION_LIMIT, "WAL compaction previous receipt is invalid")
        return value

    def _restore_compaction_quarantine_before_head(self, quarantine_root: Path) -> None:
        """head がまだ durable でない残骸だけを live tree へ戻す。"""
        if not quarantine_root.exists():
            return
        try:
            for target in sorted(quarantine_root.iterdir(), key=lambda item: item.name):
                if not target.is_dir() or _INVOCATION_ID_RE.fullmatch(target.name) is None:
                    raise CleanroomEntryError(WAL_RETENTION_LIMIT, "WAL compaction quarantine is invalid")
                source = self._managed(self.wal_root / target.name)
                if source.exists():
                    raise CleanroomEntryError(WAL_RETENTION_LIMIT, "WAL compaction quarantine/live collision")
                self.operations.replace(target, source)
                self.operations.flush_parent(source.parent)
                self.operations.flush_parent(target.parent)
            if quarantine_root.exists() and not any(quarantine_root.iterdir()):
                self.operations.remove(quarantine_root)
                self.operations.flush_parent(quarantine_root.parent)
        except CleanroomEntryError:
            raise
        except Exception as exc:
            raise CleanroomEntryError(WAL_RETENTION_LIMIT, "WAL compaction quarantine restore failed") from exc

    def _cleanup_compaction_quarantine(self, quarantine_root: Path) -> None:
        """head durable 後の cleanup。失敗時は quarantine を残して再開可能にする。"""
        if not quarantine_root.exists():
            return
        for target in sorted(quarantine_root.iterdir(), key=lambda item: item.name):
            if not target.is_dir() or _INVOCATION_ID_RE.fullmatch(target.name) is None:
                raise CleanroomEntryError(WAL_RETENTION_LIMIT, "WAL compaction quarantine is invalid")
            for child in sorted(target.iterdir(), key=lambda item: item.name):
                if child.is_dir():
                    raise CleanroomEntryError(WAL_RETENTION_LIMIT, "WAL compaction source has unexpected nested directory")
                self.operations.remove(child)
            self.operations.remove(target)
            self.operations.flush_parent(target.parent)
        self.operations.remove(quarantine_root)
        self.operations.flush_parent(quarantine_root.parent)

    def compact_imported(
        self,
        authorization: Mapping[str, Any],
        *,
        observed_at: datetime | None = None,
    ) -> dict[str, Any]:
        """ledgerの明示authorizationに束縛された古いimported WALだけをcompactする。"""
        if not isinstance(authorization, Mapping):
            raise CleanroomEntryError(WAL_RETENTION_LIMIT, "WAL compaction authorization is invalid")
        value = dict(authorization)
        if value.get("schemaVersion") != "WAL_COMPACTION_AUTHORIZATION_V1":
            raise CleanroomEntryError(WAL_RETENTION_LIMIT, "WAL compaction authorization schema is invalid")
        batch = value.get("batch")
        if not isinstance(batch, list) or not 1 <= len(batch) <= MAX_WAL_ZERO_ENTRIES:
            raise CleanroomEntryError(WAL_RETENTION_LIMIT, "WAL compaction batch is invalid")
        batch_digest = value.get("batchDigest")
        expected_batch_digest = _entry_canonical_sha256(batch)
        if batch_digest != expected_batch_digest:
            raise CleanroomEntryError(WAL_RETENTION_LIMIT, "WAL compaction batch hash drift")
        ledger_event_hash = value.get("ledgerEventSha256")
        if not isinstance(ledger_event_hash, str) or _HASH_RE.fullmatch(ledger_event_hash) is None:
            raise CleanroomEntryError(WAL_RETENTION_LIMIT, "WAL compaction ledger parity is unknown")
        previous_head = self._read_compaction_head()
        previous_receipt = value.get("previousReceipt", _ZERO_HASH)
        compaction_id = value.get("authorizationId")
        if not isinstance(compaction_id, str) or _INVOCATION_ID_RE.fullmatch(compaction_id) is None:
            raise CleanroomEntryError(WAL_RETENTION_LIMIT, "WAL compaction authorization id is invalid")

        # A crash after head publication is resumed by the same authorization.
        # The durable head is authoritative; do not move anything back to live
        # and do not create a second receipt for that authorization.
        if previous_head and previous_head.get("compactionId") == compaction_id:
            if (
                previous_head.get("previousReceipt") != previous_receipt
                or previous_head.get("batch") != batch
                or previous_head.get("batchDigest") != expected_batch_digest
                or previous_head.get("ledgerEventSha256") != ledger_event_hash
            ):
                raise CleanroomEntryError(WAL_RETENTION_LIMIT, "WAL compaction durable head authorization drift")
            quarantine_root = self._managed(self.wal_root / ".compaction-quarantine" / compaction_id)
            try:
                self._cleanup_compaction_quarantine(quarantine_root)
            except CleanroomEntryError:
                raise
            except Exception as exc:
                raise CleanroomEntryError(WAL_RETENTION_LIMIT, "WAL compaction cleanup failed") from exc
            return previous_head

        expected_previous = previous_head.get("selfHash", _ZERO_HASH) if previous_head else _ZERO_HASH
        if previous_receipt != expected_previous:
            raise CleanroomEntryError(WAL_RETENTION_LIMIT, "WAL compaction receipt chain is stale")
        try:
            counts = self.retention_counts()
            if counts["zeroEntryCount"] > MAX_WAL_ZERO_ENTRIES:
                raise CleanroomEntryError(WAL_RETENTION_LIMIT, "WAL zero-entry retention limit exceeded")
            all_events = list(self._all_imported_events())
        except CleanroomEntryError:
            raise
        if len(all_events) <= MAX_WAL_IMPORTED_ENTRIES:
            raise CleanroomEntryError(WAL_RETENTION_LIMIT, "WAL imported retention does not require compaction")
        eligible = all_events[:-MAX_WAL_IMPORTED_ENTRIES]
        eligible_keys = {(event["invocationId"], event["eventSha256"]) for event in eligible}
        requested_keys: list[tuple[str, str]] = []
        for item in batch:
            if not isinstance(item, Mapping) or set(item) != {"invocationId", "eventSha256"}:
                raise CleanroomEntryError(WAL_RETENTION_LIMIT, "WAL compaction batch item is invalid")
            invocation_id = item.get("invocationId")
            event_hash = item.get("eventSha256")
            if not isinstance(invocation_id, str) or _INVOCATION_ID_RE.fullmatch(invocation_id) is None or not isinstance(event_hash, str) or _HASH_RE.fullmatch(event_hash) is None:
                raise CleanroomEntryError(WAL_RETENTION_LIMIT, "WAL compaction batch item is invalid")
            requested_keys.append((invocation_id, event_hash))
        if len(set(requested_keys)) != len(requested_keys) or not set(requested_keys) <= eligible_keys:
            raise CleanroomEntryError(WAL_RETENTION_LIMIT, "WAL compaction batch is not an eligible prefix")

        quarantine_root = self._managed(self.wal_root / ".compaction-quarantine" / compaction_id)
        # A process can die between quarantine moves and head publication.
        # Restore that pre-head residue before attempting this authorization
        # again; after a durable head, the branch above never restores it.
        self._restore_compaction_quarantine_before_head(quarantine_root)
        quarantine_root.mkdir(parents=True, exist_ok=True)
        moved: list[Path] = []
        completed_at = (
            _validate_entry_time(observed_at).isoformat()
            if observed_at is not None
            else datetime.now(ZoneInfo("Asia/Tokyo")).isoformat()
        )
        receipt: dict[str, Any] = {
            "schemaVersion": "WAL_COMPACTION_HEAD_V1",
            "compactionId": compaction_id,
            "completedAt": completed_at,
            "previousReceipt": expected_previous,
            "batch": [{"invocationId": item[0], "eventSha256": item[1]} for item in requested_keys],
            "batchDigest": expected_batch_digest,
            "ledgerEventSha256": ledger_event_hash,
        }
        receipt["selfHash"] = _entry_canonical_sha256(receipt)
        head_durable = False

        def restore_moved_before_head() -> None:
            for target in reversed(moved):
                source = self._managed(self.wal_root / target.name)
                if target.exists() and not source.exists():
                    self.operations.replace(target, source)
                    self.operations.flush_parent(source.parent)
            if quarantine_root.exists() and not any(quarantine_root.iterdir()):
                self.operations.remove(quarantine_root)
                self.operations.flush_parent(quarantine_root.parent)

        try:
            for invocation_id, event_hash in requested_keys:
                source = self._managed(self.wal_root / invocation_id)
                if not source.is_dir():
                    raise CleanroomEntryError(WAL_RETENTION_LIMIT, "WAL compaction source directory is missing")
                target = self._managed(quarantine_root / invocation_id)
                self.operations.replace(source, target)
                self.operations.flush_parent(source.parent)
                self.operations.flush_parent(target.parent)
                moved.append(target)
            # The head receipt is the irreversible boundary.  _write_json
            # fsyncs its temp file and parent before we permit any deletion.
            _write_json(self._managed(self.wal_root / "compaction-head-v1.json"), receipt, self.operations, WAL_RETENTION_LIMIT)
            head_durable = True
            self._cleanup_compaction_quarantine(quarantine_root)
        except CleanroomEntryError:
            if not head_durable:
                try:
                    persisted = self._read_compaction_head()
                    head_durable = bool(persisted and persisted.get("selfHash") == receipt["selfHash"])
                except CleanroomEntryError:
                    head_durable = False
            if not head_durable:
                try:
                    restore_moved_before_head()
                except Exception:
                    # Preserve the original typed failure; a subsequent
                    # invocation can inspect the pre-head quarantine.
                    pass
            raise
        except Exception as exc:
            if not head_durable:
                try:
                    persisted = self._read_compaction_head()
                    head_durable = bool(persisted and persisted.get("selfHash") == receipt["selfHash"])
                except CleanroomEntryError:
                    head_durable = False
            if not head_durable:
                try:
                    restore_moved_before_head()
                except Exception:
                    pass
            raise CleanroomEntryError(WAL_RETENTION_LIMIT, "WAL compaction deletion failed") from exc
        return receipt

    # Short alias retained for callers that expose the explicit operation as
    # ``compact`` while keeping all normal dispatch paths untouched.
    compact = compact_imported

    def iter_zero_entries(self) -> tuple[dict[str, Any], ...]:
        zero: list[dict[str, Any]] = []
        imported_count = 0
        for initial_path in self._initial_paths():
            event = _read_event(initial_path, event_type="INVOCATION_RECEIVED", phase="INITIAL_DURABLE", sequence=1)
            if initial_path.parent.name != event["invocationId"]:
                raise CleanroomEntryError(LEDGER_CORRUPT, "WAL directory invocation mismatch")
            imported_path = self._managed(initial_path.with_name("0002-imported.json"))
            if not imported_path.exists():
                zero.append(event)
                if len(zero) > MAX_WAL_ZERO_ENTRIES:
                    raise CleanroomEntryError(WAL_RETENTION_LIMIT, "WAL zero-entry retention limit exceeded")
            else:
                imported_count += 1
                if imported_count > MAX_WAL_IMPORTED_ENTRIES:
                    raise CleanroomEntryError(WAL_RETENTION_LIMIT, "WAL imported-entry retention limit exceeded")
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
