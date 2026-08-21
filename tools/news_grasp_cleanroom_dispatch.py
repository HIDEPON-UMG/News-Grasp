"""S1 の公開 dispatch wrapper。"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import hashlib
import json
import sys
from typing import Any, Callable, Mapping, Sequence
from zoneinfo import ZoneInfo

from .news_grasp_cleanroom_controller import Controller
from .news_grasp_cleanroom_contracts import (
    CleanroomEntryError,
    ENTRY_ARGS_INVALID,
    ENTRY_TIME_INVALID,
    ENTRY_WRITER_INVALID,
    _validate_entry_time,
)
from .news_grasp_cleanroom_wal import DurabilityOps
from .news_grasp_entry_identity import EntryWriterAttestor, SystemEntryWriterAttestor


ENTRY_FAULT_AFTER_INITIAL_WAL = "NEWS_GRASP_ENTRY_FAULT_AFTER_INITIAL_WAL"
ENTRY_CLI_FAILURE = "NEWS_GRASP_ENTRY_CLI_FAILURE"
_TOKYO = ZoneInfo("Asia/Tokyo")
_CLI_OPTIONS = (
    "--schedule-id",
    "--intent",
    "--runtime-root",
    "--manifest-path",
    "--observed-at",
    "--writer-json",
)
_DISPATCH_DECISION_KEYS = frozenset(
    {
        "schemaVersion",
        "status",
        "decision",
        "issueDate",
        "scheduleId",
        "slotKind",
        "slotKey",
        "slotState",
        "slotTerminalState",
        "generation",
        "ownerDisposition",
        "ownerKey",
        "fenceToken",
        "leaseExpiresAt",
        "scheduledState",
        "externalEffectCount",
        "invocationId",
        "walEventSha256",
    }
)
_STABLE_DECISION_KEYS = (
    "schemaVersion",
    "decision",
    "issueDate",
    "scheduleId",
    "slotKind",
    "slotKey",
    "slotState",
    "slotTerminalState",
    "generation",
    "ownerKey",
    "fenceToken",
    "leaseExpiresAt",
    "externalEffectCount",
)


def dispatch(
    *,
    raw_argv: Sequence[str],
    runtime_root: Path,
    manifest_path: Path,
    observed_at: datetime,
    writer: Mapping[str, Any],
    lease_seconds: int = 120,
    durability_ops: DurabilityOps | None = None,
    boundary_hook: Callable[[str], None] | None = None,
    busy_timeout_ms: int = 1000,
    writer_attestor: EntryWriterAttestor | None = None,
    clock: Callable[[], datetime] | Any | None = None,
) -> dict[str, Any]:
    return Controller(
        runtime_root=runtime_root,
        manifest_path=manifest_path,
        durability_ops=durability_ops,
        boundary_hook=boundary_hook,
        busy_timeout_ms=busy_timeout_ms,
        writer_attestor=writer_attestor,
        clock=clock,
    ).reconcile(raw_argv=raw_argv, observed_at=observed_at, writer=writer, lease_seconds=lease_seconds)


def commit_slot(
    *,
    runtime_root: Path,
    manifest_path: Path,
    slot_key: str,
    writer: Mapping[str, Any],
    fence_token: int,
    terminal_state: str,
    result_hash: str,
    observed_at: datetime,
    writer_attestor: EntryWriterAttestor | None = None,
    clock: Callable[[], datetime] | Any | None = None,
) -> dict[str, Any]:
    return Controller(runtime_root=runtime_root, manifest_path=manifest_path, writer_attestor=writer_attestor, clock=clock).commit_slot(
        slot_key=slot_key,
        writer=writer,
        fence_token=fence_token,
        terminal_state=terminal_state,
        result_hash=result_hash,
        observed_at=observed_at,
    )


def inspect_control_state(*, runtime_root: Path, manifest_path: Path) -> dict[str, Any]:
    return Controller(runtime_root=runtime_root, manifest_path=manifest_path).inspect_control_state()


def recover_ledger(
    *,
    runtime_root: Path,
    manifest_path: Path,
    observed_at: datetime,
    durability_ops: DurabilityOps | None = None,
    boundary_hook: Callable[[str], None] | None = None,
    busy_timeout_ms: int = 1000,
    writer_attestor: EntryWriterAttestor | None = None,
    clock: Callable[[], datetime] | Any | None = None,
) -> dict[str, Any]:
    return Controller(
        runtime_root=runtime_root,
        manifest_path=manifest_path,
        durability_ops=durability_ops,
        boundary_hook=boundary_hook,
        busy_timeout_ms=busy_timeout_ms,
        writer_attestor=writer_attestor,
        clock=clock,
    ).recover_ledger(observed_at=observed_at)


def _cli_fail(reason: str) -> None:
    raise CleanroomEntryError(reason, reason)


def _parse_cli(argv: Sequence[str]) -> tuple[list[str], Path, Path, datetime, dict[str, Any], bool]:
    """CLI引数を副作用なく厳密に解釈する。"""
    if isinstance(argv, (str, bytes)):
        _cli_fail(ENTRY_ARGS_INVALID)
    try:
        tokens = list(argv)
    except (TypeError, ValueError):
        _cli_fail(ENTRY_ARGS_INVALID)
    if not tokens or tokens[0] != "dispatch":
        _cli_fail(ENTRY_ARGS_INVALID)
    values: dict[str, str] = {}
    fault = False
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token == "--fault-after-initial-wal":
            if fault:
                _cli_fail(ENTRY_ARGS_INVALID)
            fault = True
            index += 1
            continue
        if token not in _CLI_OPTIONS or token in values or index + 1 >= len(tokens):
            _cli_fail(ENTRY_ARGS_INVALID)
        value = tokens[index + 1]
        if not isinstance(value, str) or not value or value.startswith("--"):
            _cli_fail(ENTRY_ARGS_INVALID)
        values[token] = value
        index += 2
    if set(values) != set(_CLI_OPTIONS):
        _cli_fail(ENTRY_ARGS_INVALID)
    schedule_id = values["--schedule-id"]
    intent = values["--intent"]
    raw_argv = ["dispatch", "--schedule-id", schedule_id, "--intent", intent]
    runtime_root = Path(values["--runtime-root"])
    manifest_path = Path(values["--manifest-path"])
    writer_path = Path(values["--writer-json"])
    if not runtime_root.is_absolute() or not manifest_path.is_absolute() or not writer_path.is_absolute():
        _cli_fail(ENTRY_ARGS_INVALID)
    if not runtime_root.exists() or not runtime_root.is_dir() or not writer_path.is_file():
        _cli_fail(ENTRY_ARGS_INVALID)
    try:
        observed = datetime.fromisoformat(values["--observed-at"])
    except (TypeError, ValueError):
        _cli_fail(ENTRY_TIME_INVALID)
    if observed.tzinfo is None or observed.utcoffset() != timedelta(hours=9):
        _cli_fail(ENTRY_TIME_INVALID)
    observed = observed.astimezone(_TOKYO)
    try:
        _validate_entry_time(observed)
    except CleanroomEntryError:
        raise
    try:
        writer_value = json.loads(writer_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        _cli_fail(ENTRY_WRITER_INVALID)
    if not isinstance(writer_value, dict):
        _cli_fail(ENTRY_WRITER_INVALID)
    return raw_argv, runtime_root, manifest_path, observed, writer_value, fault


def _fault_after_initial_wal(name: str) -> None:
    if name == "after_initial_wal_fsync":
        raise CleanroomEntryError(ENTRY_FAULT_AFTER_INITIAL_WAL, ENTRY_FAULT_AFTER_INITIAL_WAL)


def _stable_decision(result: Mapping[str, Any]) -> dict[str, Any]:
    """再起動で変化する invocation/WAL/遷移投影を公開結果から除く。"""
    if set(result) != _DISPATCH_DECISION_KEYS or result.get("schemaVersion") != "DISPATCH_DECISION_V1":
        _cli_fail(ENTRY_CLI_FAILURE)
    return {key: result[key] for key in _STABLE_DECISION_KEYS}


def main(argv: Sequence[str] | None = None) -> int:
    """production CLI boundary; stdout/stderr are intentionally single-line."""
    try:
        parsed = _parse_cli(sys.argv[1:] if argv is None else argv)
        raw_argv, runtime_root, manifest_path, observed, supplied_writer, fault = parsed
        attestor = SystemEntryWriterAttestor()
        writer = attestor.bind(supplied_writer)
        if writer is None:
            raise CleanroomEntryError(ENTRY_WRITER_INVALID, ENTRY_WRITER_INVALID)
        result = dispatch(
            raw_argv=raw_argv,
            runtime_root=runtime_root,
            manifest_path=manifest_path,
            observed_at=observed,
            writer=writer,
            boundary_hook=_fault_after_initial_wal if fault else None,
            writer_attestor=attestor,
        )
        decision = _stable_decision(result)
        decision_bytes = json.dumps(
            decision, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        envelope = {
            "schemaVersion": "RECONCILE_RESULT_V1",
            "status": "accepted",
            "decision": decision,
            "decisionSha256": hashlib.sha256(decision_bytes).hexdigest(),
        }
        sys.stdout.write(json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        return 0
    except CleanroomEntryError as exc:
        sys.stderr.write(f"{exc.reason}\n")
        return 1
    except Exception:
        sys.stderr.write(f"{ENTRY_CLI_FAILURE}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
