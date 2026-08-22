"""S1 clean-room dispatch orchestration。"""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence
from zoneinfo import ZoneInfo

from .news_grasp_cleanroom_contracts import (
    CleanroomEntryError,
    ENTRY_ARGS_INVALID,
    ENTRY_LEASE_INVALID,
    ENTRY_MANIFEST_INVALID,
    ENTRY_TIME_INVALID,
    ENTRY_WRITER_INVALID,
    ENTRY_UNKNOWN_INTENT,
    ENTRY_UNKNOWN_SCHEDULE,
    _ENTRY_RAW_ARGV,
    _ENTRY_SCHEDULE_ID,
    _validate_busy_timeout,
    _validate_entry_time,
    _validate_entry_writer,
    validate_manifest,
)
from .news_grasp_cleanroom_ledger import ControlLedger
from .news_grasp_cleanroom_wal import DurableWal, DurabilityOps, WAL_FINALIZE_FAILED
from .news_grasp_entry_identity import EntryWriterAttestor, SystemEntryWriterAttestor


class _Clock(Protocol):
    def __call__(self) -> datetime: ...


def _default_clock() -> datetime:
    return datetime.now(ZoneInfo("Asia/Tokyo"))


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CleanroomEntryError(ENTRY_MANIFEST_INVALID, "manifest cannot be read") from exc
    try:
        return validate_manifest(value)
    except CleanroomEntryError:
        raise
    except Exception as exc:
        raise CleanroomEntryError(ENTRY_MANIFEST_INVALID, "manifest is invalid") from exc


def _validate_lease(lease_seconds: Any) -> int:
    if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or not 1 <= lease_seconds <= 3600:
        raise CleanroomEntryError(ENTRY_LEASE_INVALID, "lease_seconds must be an integer from 1 through 3600")
    return lease_seconds


def _parse_argv(raw_argv: Sequence[str]) -> None:
    if isinstance(raw_argv, (str, bytes)):
        raise CleanroomEntryError(ENTRY_ARGS_INVALID, "raw argv must be a sequence of tokens")
    try:
        tokens = list(raw_argv)
    except (TypeError, ValueError) as exc:
        raise CleanroomEntryError(ENTRY_ARGS_INVALID, "raw argv is not iterable") from exc
    if len(tokens) != len(_ENTRY_RAW_ARGV) or tokens[0:2] != ["dispatch", "--schedule-id"] or tokens[3] != "--intent":
        raise CleanroomEntryError(ENTRY_ARGS_INVALID, "raw argv shape is invalid")
    if any(not isinstance(token, str) for token in tokens):
        raise CleanroomEntryError(ENTRY_ARGS_INVALID, "raw argv tokens must be strings")
    if tokens[2] != _ENTRY_SCHEDULE_ID:
        raise CleanroomEntryError(ENTRY_UNKNOWN_SCHEDULE, "schedule id is unknown")
    if tokens[4] != "reconcile":
        raise CleanroomEntryError(ENTRY_UNKNOWN_INTENT, "intent is unknown")


class Controller:
    """WAL → parse/manifest → SQLite decision → WAL marker の単一経路。"""

    def __init__(
        self,
        *,
        runtime_root: Path,
        manifest_path: Path,
        durability_ops: DurabilityOps | None = None,
        boundary_hook: Callable[[str], None] | None = None,
        busy_timeout_ms: int = 1000,
        writer_attestor: EntryWriterAttestor | None = None,
        clock: _Clock | Any | None = None,
    ) -> None:
        self.busy_timeout_ms = _validate_busy_timeout(busy_timeout_ms)
        self.runtime_root = Path(runtime_root)
        self.manifest_path = Path(manifest_path)
        self.durability_ops = durability_ops
        self.boundary_hook = boundary_hook
        self.writer_attestor = writer_attestor or SystemEntryWriterAttestor()
        # An explicit attestor is the deterministic test seam; callers that
        # omit both dependencies use the real OS clock in production.
        self.clock = clock if clock is not None else (
            None if writer_attestor is not None and not isinstance(self.writer_attestor, SystemEntryWriterAttestor) else _default_clock
        )

    def _ledger(self) -> ControlLedger:
        return ControlLedger(
            self.runtime_root,
            busy_timeout_ms=self.busy_timeout_ms,
            boundary_hook=self.boundary_hook,
            writer_attestor=self.writer_attestor,
            clock=self.clock,
        )

    def _attest_writer(self, writer: Mapping[str, Any]) -> None:
        try:
            valid = bool(self.writer_attestor.validate(writer))
        except Exception:
            valid = False
        if not valid:
            raise CleanroomEntryError(ENTRY_WRITER_INVALID, "writer identity is not current process")

    def reconcile(
        self,
        *,
        raw_argv: Sequence[str],
        observed_at: datetime,
        writer: Mapping[str, Any],
        lease_seconds: int = 120,
    ) -> dict[str, Any]:
        # The writer envelope is the sole semantic validation before an
        # invocation identity can be durably recorded.
        if not isinstance(observed_at, datetime):
            raise CleanroomEntryError(ENTRY_TIME_INVALID, "observed_at must be a datetime")
        _validate_entry_writer(writer)
        self._attest_writer(writer)
        wal = DurableWal(self.runtime_root, durability_ops=self.durability_ops)
        initial_event = wal.record_initial(raw_argv=raw_argv, received_at=observed_at, writer=writer)
        if self.boundary_hook is not None:
            self.boundary_hook("after_initial_wal_fsync")

        _parse_argv(raw_argv)
        manifest = _read_manifest(self.manifest_path)
        lease = _validate_lease(lease_seconds)
        observed = _validate_entry_time(observed_at)

        ledger = self._ledger()
        zero_entries = wal.iter_zero_entries()
        prior_zero_entries = tuple(
            event for event in zero_entries
            if event.get("invocationId") != initial_event["invocationId"]
        )
        ledger.import_zero_entries(prior_zero_entries, observed_at=observed)
        result = ledger.reconcile(
            invocation_event=initial_event,
            manifest=manifest,
            writer=writer,
            lease_seconds=lease,
            observed_at=observed,
        )
        # This is deliberately after the SQLite commit boundary.  A failure
        # leaves the invocation/slot durable and is retried idempotently.
        try:
            for event in prior_zero_entries:
                wal.mark_imported(event, imported_at=observed)
            wal.mark_imported(initial_event, imported_at=observed)
        except CleanroomEntryError:
            raise
        except Exception as exc:
            raise CleanroomEntryError(WAL_FINALIZE_FAILED, str(exc)) from exc
        return result

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
        return self._ledger().commit_slot(
            slot_key=slot_key,
            writer=writer,
            fence_token=fence_token,
            terminal_state=terminal_state,
            result_hash=result_hash,
            observed_at=observed_at,
        )

    def renew_slot(
        self,
        *,
        slot_key: str,
        writer: Mapping[str, Any],
        fence_token: int,
        lease_seconds: int = 120,
        observed_at: datetime,
    ) -> dict[str, Any]:
        """同一owner/fenceのACTIVE slotだけをboundedに延長する。"""
        lease = _validate_lease(lease_seconds)
        return self._ledger().renew_slot(
            slot_key=slot_key,
            writer=writer,
            fence_token=fence_token,
            lease_seconds=lease,
            observed_at=observed_at,
        )

    def inspect_control_state(self) -> dict[str, Any]:
        DurableWal(self.runtime_root, durability_ops=self.durability_ops).verify()
        return self._ledger().inspect()

    def recover_ledger(self, *, observed_at: datetime) -> dict[str, Any]:
        return ControlLedger(
            self.runtime_root,
            busy_timeout_ms=self.busy_timeout_ms,
            boundary_hook=self.boundary_hook,
            writer_attestor=self.writer_attestor,
            clock=self.clock,
        ).recover(observed_at=observed_at, durability_ops=self.durability_ops)
