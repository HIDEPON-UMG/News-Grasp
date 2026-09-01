"""履歴修復候補をdirty原本からread-only inventoryし、日次authorityと隔離する。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


SCHEMA = "NEWS_GRASP_HISTORY_ISOLATION_V1"
PERIOD_START = "2026-06-21"
PERIOD_END = "2026-08-31"


def _reject_reparse_chain(path: str | Path, *, reason: str) -> None:
    absolute = Path(os.path.abspath(os.fspath(path)))
    for current in reversed((absolute, *absolute.parents)):
        if str(current) == current.anchor or (not current.exists() and not current.is_symlink()):
            continue
        info = os.lstat(current)
        attributes = int(getattr(info, "st_file_attributes", 0))
        if stat.S_ISLNK(info.st_mode) or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise ValueError(reason)


def _write_external_json_once(*, source_root: Path, output: str | Path, value: dict[str, Any]) -> Path:
    """source外のregular pathへno-follow・no-overwriteでatomic publishする。"""
    raw_target = Path(os.path.abspath(os.fspath(output)))
    _reject_reparse_chain(raw_target, reason="history_output_reparse_forbidden")
    source = source_root.resolve(strict=True)
    resolved_target = raw_target.resolve(strict=False)
    if resolved_target == source or resolved_target.is_relative_to(source):
        raise ValueError("history_inventory_output_inside_source_forbidden")
    raw_target.parent.mkdir(parents=True, exist_ok=True)
    _reject_reparse_chain(raw_target, reason="history_output_reparse_forbidden")
    if os.path.lexists(raw_target):
        raise FileExistsError("history_output_exists_no_overwrite")
    parent_before = os.lstat(raw_target.parent)
    payload = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    handle, temp_name = tempfile.mkstemp(prefix=raw_target.name + ".", suffix=".tmp", dir=raw_target.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        _reject_reparse_chain(raw_target, reason="history_output_reparse_forbidden")
        parent_after = os.lstat(raw_target.parent)
        if (parent_before.st_dev, parent_before.st_ino) != (parent_after.st_dev, parent_after.st_ino):
            raise RuntimeError("history_output_parent_identity_changed")
        os.link(temp_name, raw_target, follow_symlinks=False)
    finally:
        Path(temp_name).unlink(missing_ok=True)
    return raw_target


def _read_repo_json_bounded(repo_root: Path, path: str | Path) -> dict[str, Any]:
    raw = Path(os.path.abspath(os.fspath(path)))
    _reject_reparse_chain(raw, reason="history_manifest_reparse_forbidden")
    root = repo_root.resolve(strict=True)
    resolved = raw.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise ValueError("history_manifest_outside_repo")
    before = os.lstat(raw)
    if not stat.S_ISREG(before.st_mode) or before.st_size > 1_048_576:
        raise ValueError("history_manifest_file_invalid")
    descriptor = os.open(raw, os.O_RDONLY | int(getattr(os, "O_BINARY", 0)) | int(getattr(os, "O_NOFOLLOW", 0)))
    try:
        data = os.read(descriptor, 1_048_577)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
    if identity(before) != identity(after) or len(data) != after.st_size or len(data) > 1_048_576:
        raise ValueError("history_manifest_identity_changed")
    value = json.loads(data.decode("utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("daily_manifest_invalid")
    return value


def _run_git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=str(root), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60, check=False,
        shell=False, creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
    )


def _safe_hash(root: Path, path: Path) -> tuple[str, int]:
    relative = path.relative_to(root)
    current = root
    for part in relative.parts:
        current = current / part
        if not os.path.lexists(current):
            return "missing", 0
        component = os.lstat(current)
        component_attributes = int(getattr(component, "st_file_attributes", 0))
        if stat.S_ISLNK(component.st_mode) or component_attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            return "unsafe", int(component.st_size)
    before = os.lstat(path)
    attributes = int(getattr(before, "st_file_attributes", 0))
    if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode) or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
        return "unsafe", int(before.st_size)
    flags = os.O_RDONLY | int(getattr(os, "O_BINARY", 0)) | int(getattr(os, "O_NOFOLLOW", 0))
    fd = os.open(path, flags)
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns):
            return "unsafe", int(before.st_size)
        digest = hashlib.sha256()
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(fd)
        if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns):
            return "unsafe", int(after.st_size)
        return digest.hexdigest(), int(after.st_size)
    finally:
        os.close(fd)


def inventory_dirty_checkout(source_root: str | Path) -> dict[str, Any]:
    """tracked/untracked path、hash、baselineを原本へ書かずに封印する。"""
    root = Path(source_root).resolve(strict=True)
    status = _run_git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    head = _run_git(root, "rev-parse", "HEAD")
    remote = _run_git(root, "ls-remote", "origin", "refs/heads/main")
    if status.returncode != 0 or head.returncode != 0:
        raise RuntimeError("history_inventory_git_observation_failed")
    rows: list[dict[str, Any]] = []
    records = status.stdout.split("\0")
    skip_original = False
    for line in records:
        if skip_original:
            skip_original = False
            continue
        if len(line) < 4:
            continue
        code = line[:2]
        raw = line[3:]
        relative = raw.replace("\\", "/")
        if "R" in code or "C" in code:
            skip_original = True
        parts = relative.split("/")
        if (
            not relative
            or relative.startswith("/")
            or re.match(r"^[A-Za-z]:", relative)
            or any(part in {"", ".", ".."} for part in parts)
        ):
            raise ValueError("history_inventory_path_escape")
        normalized = PurePosixPath(relative).as_posix()
        candidate = root.joinpath(*PurePosixPath(normalized).parts)
        digest, size = ("missing", 0)
        if os.path.lexists(candidate):
            digest, size = _safe_hash(root, candidate)
        rows.append({"status": code, "path": normalized, "sha256": digest, "size": size})
    remote_head = remote.stdout.split()[0] if remote.returncode == 0 and remote.stdout.split() else "unverified"
    return {
        "schemaVersion": SCHEMA,
        "sourceRoot": str(root),
        "sourcePolicy": "read_only_immutable",
        "baselineHead": head.stdout.strip(),
        "remoteHead": remote_head,
        "period": {"start": PERIOD_START, "end": PERIOD_END},
        "pathCount": len(rows),
        "paths": rows,
    }


def write_inventory_outside_source(source_root: str | Path, output: str | Path) -> Path:
    """inventoryはdirty原本の外だけへ保存する。"""
    source = Path(source_root).resolve(strict=True)
    raw_target = Path(os.path.abspath(os.fspath(output)))
    _reject_reparse_chain(raw_target, reason="history_output_reparse_forbidden")
    resolved_target = raw_target.resolve(strict=False)
    if resolved_target == source or resolved_target.is_relative_to(source):
        raise ValueError("history_inventory_output_inside_source_forbidden")
    return _write_external_json_once(source_root=source, output=output, value=inventory_dirty_checkout(source))


def period_dates(start: str = PERIOD_START, end: str = PERIOD_END) -> list[str]:
    first = date.fromisoformat(start)
    last = date.fromisoformat(end)
    if first > last:
        raise ValueError("history_period_invalid")
    rows: list[str] = []
    current = first
    while current <= last:
        rows.append(current.isoformat())
        current += timedelta(days=1)
    return rows


def audit_history_date(repo_root: str | Path, issue_date: str) -> dict[str, Any]:
    """repair_publish routeのGreenだけをpromotion候補にする。"""
    if issue_date not in period_dates():
        return {"date": issue_date, "status": "quarantine", "reasonCodes": ["history_date_out_of_scope"]}
    root = Path(repo_root).resolve()
    try:
        from tools import deepdive_quality
        result = deepdive_quality.audit_issue(repo_root=root, issue_date=issue_date, require_rendered_public=True, route="repair_publish")
    except Exception as exc:  # noqa: BLE001 - typed quarantine data.
        return {"date": issue_date, "status": "quarantine", "reasonCodes": [f"history_audit_error:{exc}"]}
    reasons = list(result.get("issueCodes") or []) + list(result.get("issues") or [])
    status_value = "verified" if result.get("status") == "Green" and not reasons else "quarantine"
    return {"date": issue_date, "status": status_value, "route": "repair_publish", "reasonCodes": reasons}


def aggregate_history_audit(rows: Iterable[dict[str, Any]], *, daily_manifest_ok: bool) -> dict[str, Any]:
    """history quarantineと当日authorityを型分離する。"""
    promoted = [dict(row) for row in rows if row.get("status") == "verified"]
    quarantined = [dict(row) for row in rows if row.get("status") != "verified"]
    return {"schemaVersion": SCHEMA, "dailyAuthority": "verified" if daily_manifest_ok else "blocked", "promoted": promoted, "quarantine": quarantined}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    inventory = sub.add_parser("inventory-original")
    inventory.add_argument("--source-root", type=Path, required=True)
    inventory.add_argument("--output", type=Path, required=True)
    audit = sub.add_parser("audit-period")
    audit.add_argument("--repo-root", type=Path, default=Path.cwd())
    audit.add_argument("--output", type=Path, required=True)
    audit.add_argument("--daily-manifest", type=Path, required=True)
    return parser


def _main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.cmd == "inventory-original":
        path = write_inventory_outside_source(args.source_root, args.output)
        result = {"ok": True, "path": str(path)}
    else:
        from tools.news_grasp_publish_contract import verify_manifest

        manifest = _read_repo_json_bounded(Path(args.repo_root), args.daily_manifest)
        daily = verify_manifest(manifest, repo_root=args.repo_root, require_files=True)
        rows = [audit_history_date(args.repo_root, value) for value in period_dates()]
        result = aggregate_history_audit(rows, daily_manifest_ok=daily.get("ok") is True)
        _write_external_json_once(source_root=Path(args.repo_root), output=args.output, value=result)
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
