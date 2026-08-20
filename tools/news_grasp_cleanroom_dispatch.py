"""S1 の公開 dispatch wrapper。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .news_grasp_cleanroom_controller import Controller
from .news_grasp_cleanroom_wal import DurabilityOps


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
) -> dict[str, Any]:
    return Controller(
        runtime_root=runtime_root,
        manifest_path=manifest_path,
        durability_ops=durability_ops,
        boundary_hook=boundary_hook,
        busy_timeout_ms=busy_timeout_ms,
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
) -> dict[str, Any]:
    return Controller(runtime_root=runtime_root, manifest_path=manifest_path).commit_slot(
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
) -> dict[str, Any]:
    return Controller(
        runtime_root=runtime_root,
        manifest_path=manifest_path,
        durability_ops=durability_ops,
        boundary_hook=boundary_hook,
        busy_timeout_ms=busy_timeout_ms,
    ).recover_ledger(observed_at=observed_at)
