#!/usr/bin/env python3
"""Daily runner diagnosis, alerting, and publish verification helpers."""
from __future__ import annotations

import argparse
import ast
from functools import wraps
import hashlib
import io
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlencode, urljoin, urlparse

if __package__ in {None, ""}:
    # Recovery verification runs as ``python -I <absolute-script>`` so an
    # ambient PYTHONPATH/sitecustomize cannot replace trusted ops modules.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import deepdive_quality
from tools.publish_inventory import (
    CATEGORY_PATHS,
    required_distribution_artifacts,
    required_published_docs_artifacts,
    scheduled_category_ids,
)


ALERT_STATUSES = {
    "content_failed",
    "exhausted",
    "failed",
    "fallback_ok",
    "no_run_detected",
    "publish_failed",
    "stale",
}

RUNNER_START_MINUTES = 6 * 60
BOOTSTRAP_START_MINUTES = 5 * 60 + 55


def _current_jst_date() -> str:
    """次回production readinessを検証する現在のJST日付を返す。"""
    return datetime.now(timezone(timedelta(hours=9))).date().isoformat()


def _jst_timezone():
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo("Asia/Tokyo")
    except Exception:
        return timezone(timedelta(hours=9))


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def readiness_freshness_snapshot(
    *,
    generation_id: str,
    descriptor_path: Path | None = None,
    task_definition: str = "",
    deadman_path: Path | None = None,
    valid_until: str = "",
) -> dict[str, object]:
    """readiness proofをgenerationと運用面hashへ束縛する。"""

    def _safe_sha(path: Path | None) -> str:
        if path is None or not path.is_file() or path.is_symlink():
            return ""
        try:
            return sha256_file(path)
        except OSError:
            return ""

    descriptor = descriptor_path.resolve() if descriptor_path else None
    deadman = deadman_path.resolve() if deadman_path else None
    return {
        "schemaVersion": "NEXT_RUN_READINESS_V1",
        "generationId": str(generation_id or ""),
        "descriptorPath": str(descriptor) if descriptor else "",
        "descriptorSha256": _safe_sha(descriptor),
        "taskDefinitionSha256": hashlib.sha256(
            str(task_definition or "").encode("utf-8")
        ).hexdigest(),
        "deadmanPath": str(deadman) if deadman else "",
        "deadmanIdentitySha256": _safe_sha(deadman),
        "validUntil": str(valid_until or ""),
    }


def verify_readiness_freshness(
    proof: dict[str, object],
    *,
    generation_id: str,
    descriptor_path: Path | None = None,
    task_definition: str = "",
    deadman_path: Path | None = None,
) -> dict[str, object]:
    """保存済みGreen proofをcurrent generation/descriptor/task/deadmanへ再束縛する。"""

    if not isinstance(proof, dict) or proof.get("schemaVersion") != "NEXT_RUN_READINESS_V1":
        return {"ok": False, "status": "unverified", "reasonCode": "readiness_proof_missing"}
    expected = readiness_freshness_snapshot(
        generation_id=generation_id,
        descriptor_path=descriptor_path,
        task_definition=task_definition,
        deadman_path=deadman_path,
        valid_until=str(proof.get("validUntil") or ""),
    )
    keys = (
        "generationId",
        "descriptorSha256",
        "taskDefinitionSha256",
        "deadmanIdentitySha256",
    )
    if any(str(proof.get(key) or "") != str(expected.get(key) or "") for key in keys):
        return {
            "ok": False,
            "status": "stale",
            "reasonCode": "readiness_proof_stale",
            "expected": expected,
            "observed": {key: proof.get(key) for key in keys},
        }
    return {"ok": True, "status": "ready", "freshness": expected}


@dataclass(frozen=True)
class CompletionVerificationResultV1:
    """公開完了、次回 readiness、audit 観測を混同しない内部結果。"""

    status: str
    verificationStatus: str
    publicCompletionStatus: str
    nextRunReadinessStatus: str
    phase: str
    reasonCode: str
    failedGateIds: tuple[str, ...]
    sourceSha256: str
    runtimeSha256: str
    configSha256: str
    evidenceSha256: str
    publicAuthority: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        authority = dict(self.publicAuthority)
        return {
            "schemaVersion": "COMPLETION_VERIFICATION_RESULT_V1",
            "status": self.status,
            "verificationStatus": self.verificationStatus,
            "publicCompletionStatus": self.publicCompletionStatus,
            "nextRunReadinessStatus": self.nextRunReadinessStatus,
            "phase": self.phase,
            "reasonCode": self.reasonCode,
            "failedGateIds": list(self.failedGateIds),
            "sourceSha256": self.sourceSha256,
            "runtimeSha256": self.runtimeSha256,
            "configSha256": self.configSha256,
            "evidenceSha256": self.evidenceSha256,
            "publicAuthority": authority,
            "completionAuthorityId": str(
                authority.get("completionAuthorityId")
                or authority.get("id")
                or ""
            ),
        }


def _completion_runtime_sha256() -> str:
    runtime = Path(sys.executable)
    if runtime.is_file() and not runtime.is_symlink():
        try:
            return sha256_file(runtime)
        except OSError:
            pass
    return hashlib.sha256(str(runtime).encode("utf-8")).hexdigest()


def _completion_config_sha256(
    *,
    repo_root: Path,
    ops_repo_root: Path | None,
    date: str,
    remote: str,
    branch: str,
    public_base_url: str,
    wait_sec: int,
    poll_sec: int,
) -> str:
    body = {
        "repoRoot": str(repo_root.resolve()),
        "opsRepoRoot": str((ops_repo_root or repo_root).resolve()),
        "date": date,
        "remote": remote,
        "branch": branch,
        "publicBaseUrl": public_base_url,
        "waitSec": wait_sec,
        "pollSec": poll_sec,
    }
    return hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _completion_result(
    *,
    repo_root: Path,
    ops_repo_root: Path | None,
    date: str,
    remote: str,
    branch: str,
    public_base_url: str,
    wait_sec: int,
    poll_sec: int,
    public: dict[str, object],
    readiness: dict[str, object],
    phase: str,
    reason_code: str,
    verification_status: str | None = None,
    public_authority: dict[str, object] | None = None,
) -> dict[str, object]:
    public_green = public.get("ok") is True or (
        public.get("public_status") == "green" and public_authority
    )
    readiness_ok = readiness.get("ok") is True
    readiness_unavailable = bool(readiness.get("verification_unavailable"))
    public_status = (
        "green"
        if public_green
        else "unverified"
        if public.get("verification_unavailable")
        else "incomplete"
    )
    readiness_status = (
        "green" if readiness_ok else "unverified" if readiness_unavailable else "red"
    )
    if verification_status is None:
        if public.get("verification_unavailable") or readiness_unavailable:
            verification_status = "verification_unavailable"
        elif public_green and readiness_ok:
            verification_status = "verified_green"
        else:
            verification_status = "verified_incomplete"
    failed = []
    for source in (public, readiness):
        values = source.get("failedGateIds")
        if isinstance(values, (list, tuple)):
            failed.extend(str(value) for value in values if str(value))
    if not failed and verification_status != "verified_green" and reason_code:
        failed.append(reason_code)
    authority = dict(public_authority or {})
    authority.setdefault(
        "completionAuthorityId",
        str(public.get("completion_authority_id") or public.get("completionAuthorityId") or ""),
    )
    public_evidence = {
        "public": public,
        "authority": authority,
        "date": date,
    }
    evidence_sha = hashlib.sha256(
        json.dumps(
            {"public": public_evidence, "readiness": readiness, "phase": phase},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    result = CompletionVerificationResultV1(
        status=verification_status,
        verificationStatus=verification_status,
        publicCompletionStatus=public_status,
        nextRunReadinessStatus=readiness_status,
        phase=phase,
        reasonCode=reason_code,
        failedGateIds=tuple(dict.fromkeys(failed)),
        sourceSha256=sha256_file(Path(__file__)),
        runtimeSha256=_completion_runtime_sha256(),
        configSha256=_completion_config_sha256(
            repo_root=repo_root,
            ops_repo_root=ops_repo_root,
            date=date,
            remote=remote,
            branch=branch,
            public_base_url=public_base_url,
            wait_sec=wait_sec,
            poll_sec=poll_sec,
        ),
        evidenceSha256=evidence_sha,
        publicAuthority=authority,
    )
    return {
        **result.to_dict(),
        "ok": verification_status == "verified_green",
        "date": date,
        "public_status": public_status,
        "readiness_status": readiness_status,
        "public": public,
        "readiness": readiness,
    }


def completion_cli_exit_code(result: object) -> int:
    """typed completionのaggregate Greenだけを成功終了コードへ投影する。"""
    value = result if isinstance(result, dict) else {}
    status = str(value.get("status") or "")
    aggregate_green = (
        status == "verified_green"
        and value.get("ok") is True
        and value.get("publicCompletionStatus") == "green"
        and value.get("nextRunReadinessStatus") == "green"
    )
    return 0 if aggregate_green else 2


def compare_files(repo_path: Path, live_path: Path) -> dict:
    repo_exists = repo_path.exists()
    live_exists = live_path.exists()
    repo_sha = sha256_file(repo_path) if repo_exists else None
    live_sha = sha256_file(live_path) if live_exists else None
    return {
        "repo_path": str(repo_path),
        "live_path": str(live_path),
        "repo_exists": repo_exists,
        "live_exists": live_exists,
        "repo_sha256": repo_sha,
        "live_sha256": live_sha,
        "synced": bool(repo_exists and live_exists and repo_sha == live_sha),
    }


def _default_live_runner_path() -> Path:
    return Path.home() / "bin" / "news-grasp-runner.ps1"


def _default_live_watcher_path() -> Path:
    return Path.home() / "bin" / "watch-news-grasp-runner.ps1"


def _default_live_bootstrap_path() -> Path:
    return Path.home() / "bin" / "news-grasp-bootstrap.ps1"


def _default_live_task_launcher_path() -> Path:
    return Path.home() / "bin" / "news-grasp-task-launcher.pyw"


def _command_path_text(value: Path | str) -> str:
    return str(value).strip().strip('"').replace("/", "\\").lower()


def _task_action_records(details: dict) -> list[dict[str, str]]:
    actions = details.get("actions")
    if isinstance(actions, dict):
        actions = [actions]
    if not isinstance(actions, list):
        return []
    records: list[dict[str, str]] = []
    for action in actions:
        if not isinstance(action, dict):
            return []
        execute = str(action.get("execute") or "")
        arguments = str(action.get("arguments") or "")
        if not execute:
            return []
        records.append({"execute": execute, "arguments": arguments})
    return records


def _windows_action_arguments(arguments: str) -> list[str]:
    try:
        values = shlex.split(arguments, posix=False)
    except ValueError:
        return []
    normalized: list[str] = []
    for value in values:
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if not value or '"' in value or "'" in value:
            return []
        normalized.append(value)
    return normalized


def _ancestor_identities(path: Path) -> tuple[tuple[str, int, int, int], ...]:
    """leaf readの前後で全ancestorの差替えとreparse化を検知する。"""
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    identities: list[tuple[str, int, int, int]] = []
    current = path.parent
    while True:
        metadata = os.lstat(current)
        attributes = int(getattr(metadata, "st_file_attributes", 0))
        if attributes & reparse_flag:
            raise ValueError("ancestor reparse point")
        identities.append((os.path.normcase(str(current)), metadata.st_dev, metadata.st_ino, attributes))
        if current.parent == current:
            break
        current = current.parent
    return tuple(identities)


def _canonical_file_bytes(path: Path, *, expected: Path, max_bytes: int) -> bytes:
    try:
        candidate = Path(os.path.abspath(path))
        expected_path = Path(os.path.abspath(expected))
        if os.path.normcase(str(candidate)) != os.path.normcase(str(expected_path)):
            raise ValueError("path mismatch")
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        ancestors_before = _ancestor_identities(candidate)
        before = os.lstat(candidate)
        file_attributes = int(getattr(before, "st_file_attributes", 0))
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or file_attributes & reparse_flag
            or before.st_nlink != 1
        ):
            raise ValueError("non-regular file")
        if before.st_size <= 0 or before.st_size > max_bytes:
            raise ValueError("bounded size invalid")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(candidate, flags)
        try:
            opened = os.fstat(descriptor)
            if (
                opened.st_dev != before.st_dev
                or opened.st_ino != before.st_ino
                or opened.st_nlink != 1
                or opened.st_size != before.st_size
            ):
                raise ValueError("file identity drift")
            chunks: list[bytes] = []
            remaining = max_bytes + 1
            while remaining > 0:
                chunk = os.read(descriptor, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
            after_handle = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        after_path = os.lstat(candidate)
        ancestors_after = _ancestor_identities(candidate)
        if (
            len(raw) != before.st_size
            or after_handle.st_dev != before.st_dev
            or after_handle.st_ino != before.st_ino
            or after_handle.st_size != before.st_size
            or after_handle.st_mtime_ns != before.st_mtime_ns
            or after_path.st_dev != before.st_dev
            or after_path.st_ino != before.st_ino
            or after_path.st_nlink != 1
            or after_path.st_size != before.st_size
            or after_path.st_mtime_ns != before.st_mtime_ns
            or ancestors_after != ancestors_before
        ):
            raise ValueError("file changed during read")
        return raw
    except (OSError, ValueError) as error:
        raise ValueError("high_cost_binding_authority_invalid") from error


def _canonical_live_json(path: Path, *, expected: Path, max_bytes: int = 65536) -> tuple[dict, bytes]:
    try:
        raw = _canonical_file_bytes(path, expected=expected, max_bytes=max_bytes)
        value = json.loads(raw.decode("utf-8-sig"))
        if not isinstance(value, dict):
            raise ValueError("JSON object required")
        return value, raw
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("high_cost_binding_authority_invalid") from error


def _authenticode_identity(path: Path, *, ops_repo_root: Path) -> dict[str, str]:
    """Windows trust storeで実ファイルの署名を検証し、JSON自己申告と分離する。"""
    helper = (
        ops_repo_root.resolve(strict=True)
        / "scripts"
        / "ops"
        / "get-news-grasp-authenticode-identity.ps1"
    )
    if not helper.is_file() or helper.is_symlink():
        raise ValueError("authenticode helper invalid")
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        completed = subprocess.run(
            [
                r"C:\Program Files\PowerShell\7\pwsh.exe",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(helper),
                "-TargetPath",
                str(path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
            creationflags=creationflags,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError("authenticode verification unavailable") from error
    if completed.returncode != 0:
        raise ValueError("authenticode verification failed")
    value = json.loads(completed.stdout.lstrip("\ufeff").strip())
    if not isinstance(value, dict):
        raise ValueError("authenticode identity invalid")
    return {str(key): str(item or "") for key, item in value.items()}


def _safe_ops_git_output(ops_repo_root: Path, args: list[str]) -> str:
    """検証対象repoのhook/fsmonitor/attributesを実行せずboundedにGitを読む。"""
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        completed = subprocess.run(
            [
                "git",
                "-c",
                "core.hooksPath=NUL",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.attributesFile=NUL",
                "-C",
                str(ops_repo_root),
                *args,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
            creationflags=creationflags,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError("safe ops git unavailable") from error
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout).strip())
    return completed.stdout.strip()


def _trusted_ops_generation(ops_repo_root: Path) -> dict[str, str]:
    """cleanなcanonical ops generationをlive自己申告から独立して導出する。"""
    root = ops_repo_root.resolve(strict=True)
    head = _safe_ops_git_output(root, ["rev-parse", "HEAD"]).lower()
    remote = _safe_ops_git_output(root, ["remote", "get-url", "origin"])
    dirty_raw = _safe_ops_git_output(root, ["status", "--porcelain", "--untracked-files=all"])
    dirty = "\n".join(
        line
        for line in dirty_raw.splitlines()
        if line
        and not line[2:].lstrip().startswith("data/gate_attempts/")
        and not line[2:].lstrip().startswith("data/search_audit/")
    )
    ignored_raw = _safe_ops_git_output(
        root, ["ls-files", "--others", "--ignored", "--exclude-standard"]
    )
    ignored = "\n".join(
        line
        for line in ignored_raw.splitlines()
        if line
        and line != ".managed-root.pin"
        and not line.startswith(".pytest_cache/")
        and not line.startswith("build/")
        and not line.startswith("tmp/")
        and not line.startswith("data/search_audit/")
        and "__pycache__/" not in line
        and not line.endswith(".pyc")
    )
    daily_self_heal = (root / "tools" / "daily_self_heal.py").resolve(strict=True)
    if dirty or ignored or not re.fullmatch(r"[0-9a-f]{40}", head):
        raise ValueError("ops generation invalid")
    return {
        "root": str(root),
        "head": head,
        "remote": remote,
        "daily_self_heal_path": str(daily_self_heal),
        "daily_self_heal_sha256": sha256_file(daily_self_heal),
    }


def _validate_live_high_cost_binding_files(
    *,
    live_bin_root: Path,
    binding_path: Path,
    binding_receipt_sha256: str,
    ops_repo_root: Path | None = None,
) -> dict:
    try:
        live_bin = live_bin_root.resolve(strict=True)
        expected_binding = live_bin / "news-grasp-high-cost-binding-v1.json"
        expected_recovery = live_bin / "news-grasp-recovery-runtime-binding-v1.json"
        binding, binding_raw = _canonical_live_json(binding_path, expected=expected_binding)
        recovery, _ = _canonical_live_json(expected_recovery, expected=expected_recovery)
        receipt = binding_receipt_sha256.lower()
        binding_file_sha256 = hashlib.sha256(binding_raw).hexdigest()
        python_exe = Path(str(recovery.get("pythonExe") or ""))
        task_pythonw = Path(str(recovery.get("taskPythonwPath") or ""))
        expected_pythonw = python_exe.with_name("pythonw.exe")
        python_raw = _canonical_file_bytes(
            python_exe,
            expected=python_exe,
            max_bytes=64 * 1024 * 1024,
        )
        pythonw_raw = _canonical_file_bytes(
            task_pythonw,
            expected=expected_pythonw,
            max_bytes=64 * 1024 * 1024,
        )
        python_sha256 = hashlib.sha256(python_raw).hexdigest()
        pythonw_sha256 = hashlib.sha256(pythonw_raw).hexdigest()
        trusted_ops = _trusted_ops_generation(ops_repo_root) if ops_repo_root is not None else None
        if ops_repo_root is None:
            raise ValueError("ops generation required")
        python_signature = _authenticode_identity(
            python_exe, ops_repo_root=ops_repo_root
        )
        pythonw_signature = _authenticode_identity(
            task_pythonw, ops_repo_root=ops_repo_root
        )
        trusted_remote = "https://github.com/HIDEPON-UMG/News-Grasp.git"
        if (
            not re.fullmatch(r"[0-9a-f]{64}", receipt)
            or binding.get("schemaVersion") != "NEWS_GRASP_HIGH_COST_BINDING_V1"
            or str(binding.get("bindingReceiptSha256") or "").lower() != receipt
            or recovery.get("schemaVersion") != "NEWS_GRASP_RECOVERY_RUNTIME_BINDING_V1"
            or os.path.normcase(str(Path(str(recovery.get("highCostBindingPath") or "")).resolve(strict=True)))
            != os.path.normcase(str(expected_binding.resolve(strict=True)))
            or str(recovery.get("highCostBindingReceiptSha256") or "").lower() != receipt
            or str(recovery.get("highCostBindingFileSha256") or "").lower() != binding_file_sha256
            or python_exe.name.lower() != "python.exe"
            or str(recovery.get("pythonExeSha256") or "").lower() != python_sha256
            or str(recovery.get("taskPythonwSha256") or "").lower() != pythonw_sha256
            or recovery.get("pythonTrustAnchor") != "authenticode:python-software-foundation"
            or not str(recovery.get("pythonSignerSubject") or "").startswith(
                "CN=Python Software Foundation, O=Python Software Foundation,"
            )
            or not re.fullmatch(
                r"[0-9a-f]{40}",
                str(recovery.get("pythonSignerThumbprint") or "").lower(),
            )
            or python_signature.get("status") != "Valid"
            or python_signature.get("subject") != recovery.get("pythonSignerSubject")
            or python_signature.get("thumbprint", "").lower()
            != str(recovery.get("pythonSignerThumbprint") or "").lower()
            or recovery.get("pythonwTrustAnchor") != "authenticode:python-software-foundation"
            or pythonw_signature.get("status") != "Valid"
            or pythonw_signature.get("subject") != recovery.get("pythonwSignerSubject")
            or pythonw_signature.get("thumbprint", "").lower()
            != str(recovery.get("pythonwSignerThumbprint") or "").lower()
            or not str(pythonw_signature.get("subject") or "").startswith(
                "CN=Python Software Foundation, O=Python Software Foundation,"
            )
            or pythonw_signature.get("thumbprint", "").lower()
            != python_signature.get("thumbprint", "").lower()
            or (
                trusted_ops is not None
                and (
                    str(recovery.get("opsHead") or "").lower() != trusted_ops["head"]
                    or str(recovery.get("trustedRemote") or "") != trusted_remote
                    or trusted_ops["remote"].removesuffix(".git") != trusted_remote.removesuffix(".git")
                    or str(recovery.get("dailySelfHealSha256") or "").lower()
                    != trusted_ops["daily_self_heal_sha256"]
                )
            )
        ):
            raise ValueError("binding identity mismatch")
        return {
            "ok": True,
            "reason": "",
            "binding_path": str(expected_binding.resolve(strict=True)),
            "binding_receipt_sha256": receipt,
            "binding_file_sha256": binding_file_sha256,
            "task_pythonw_path": str(expected_pythonw),
            "task_pythonw_sha256": pythonw_sha256,
        }
    except (OSError, RuntimeError, ValueError):
        return {"ok": False, "reason": "high_cost_binding_authority_invalid"}


def _validate_live_high_cost_binding_authority(
    *,
    task_details: dict,
    bootstrap_details: dict,
    live_task_launcher_path: Path,
    task_name: str,
    bootstrap_task_name: str,
    ops_repo_root: Path,
) -> dict:
    runner_actions = _task_action_records(task_details)
    bootstrap_actions = _task_action_records(bootstrap_details)
    if len(runner_actions) != 1 or len(bootstrap_actions) != 1:
        return {"ok": False, "reason": "high_cost_binding_action_invalid"}
    live_bin = live_task_launcher_path.resolve(strict=True).parent
    expected_authority = live_bin / "news-grasp-stable-task-authority-v1.json"
    try:
        authority, _ = _canonical_live_json(expected_authority, expected=expected_authority)
        if authority.get("schemaVersion") != "STABLE_TASK_AUTHORITY_V1":
            raise ValueError("authority schema mismatch")
        declared_authority_sha256 = str(authority.get("authoritySha256") or "").lower()
        authority_body = dict(authority)
        authority_body.pop("authoritySha256", None)
        calculated_authority_sha256 = hashlib.sha256(
            json.dumps(
                authority_body,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if (
            not re.fullmatch(r"[0-9a-f]{64}", declared_authority_sha256)
            or declared_authority_sha256 != calculated_authority_sha256
        ):
            raise ValueError("authority hash mismatch")
        authority_action = authority.get("action")
        if not isinstance(authority_action, list) or not all(isinstance(item, str) for item in authority_action):
            raise ValueError("authority action invalid")
        runner_action = runner_actions[0]
        runner_argv = [runner_action["execute"], *_windows_action_arguments(runner_action["arguments"])]
        if runner_argv != authority_action and runner_argv[1:] == authority_action[1:]:
            runner_argv = [authority_action[0], *runner_argv[1:]]
        if runner_argv != authority_action:
            raise ValueError("runner action mismatch")
        if len(authority_action) != 9:
            raise ValueError("runner action length invalid")
        expected_binding_path = live_bin / "news-grasp-high-cost-binding-v1.json"
        expected_runner = [
            authority_action[0],
            str(live_task_launcher_path.resolve(strict=True)),
            "runner",
            "--scheduled-task-name",
            task_name,
            "--high-cost-binding-path",
            str(expected_binding_path.resolve(strict=True)),
            "--high-cost-binding-sha256",
            authority_action[8],
        ]
        if authority_action != expected_runner:
            raise ValueError("runner authority mismatch")
        bootstrap_action = bootstrap_actions[0]
        bootstrap_argv = [
            bootstrap_action["execute"],
            *_windows_action_arguments(bootstrap_action["arguments"]),
        ]
        expected_bootstrap = [
            authority_action[0],
            str(live_task_launcher_path.resolve(strict=True)),
            "bootstrap",
            "--scheduled-task-name",
            bootstrap_task_name,
            "--high-cost-binding-path",
            str(expected_binding_path.resolve(strict=True)),
            "--high-cost-binding-sha256",
            authority_action[8],
        ]
        if bootstrap_argv != expected_bootstrap and bootstrap_argv[1:] == expected_bootstrap[1:]:
            bootstrap_argv = [expected_bootstrap[0], *bootstrap_argv[1:]]
        if bootstrap_argv != expected_bootstrap:
            if bootstrap_argv[1:] == expected_bootstrap[1:]:
                bootstrap_argv = [expected_bootstrap[0], *bootstrap_argv[1:]]
        if bootstrap_argv != expected_bootstrap:
            raise ValueError("bootstrap action mismatch")
        files = _validate_live_high_cost_binding_files(
            live_bin_root=live_bin,
            binding_path=expected_binding_path,
            binding_receipt_sha256=authority_action[8],
            ops_repo_root=ops_repo_root,
        )
        if not files.get("ok"):
            return files
        if os.path.normcase(authority_action[0]) != os.path.normcase(
            str(files.get("task_pythonw_path") or "")
        ):
            raise ValueError("task pythonw authority mismatch")
        return files
    except (OSError, ValueError):
        return {"ok": False, "reason": "high_cost_binding_action_invalid"}


def _safe_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _time_minutes_from_text(value: object) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    match = re.search(r"(?:T|\s)(\d{1,2}):(\d{2})(?::\d{2})?\s*([AP]M)?", text, re.IGNORECASE)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2))
    marker = (match.group(3) or "").upper()
    if marker == "PM" and hour < 12:
        hour += 12
    elif marker == "AM" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return hour * 60 + minute


def _next_run_time_matches(details: dict, expected_minutes: int) -> bool:
    return _time_minutes_from_text(details.get("next_run_time")) == expected_minutes


def _missed_runs_zero(details: dict) -> bool:
    return _safe_int(details.get("number_of_missed_runs")) == 0


def _action_has_switch(action_summary: str, switch: str) -> bool:
    return bool(re.search(rf"(?i)(?:^|\s){re.escape(switch)}(?:\s|$)", action_summary))


def _action_option_value(action_summary: str, option: str) -> str:
    match = re.search(
        rf"(?i)(?:^|\s){re.escape(option)}\s+(?:\"([^\"]+)\"|'([^']+)'|(\S+))",
        action_summary,
    )
    if not match:
        return ""
    return next((part for part in match.groups() if part), "")


def _action_option_int(action_summary: str, option: str) -> int | None:
    return _safe_int(_action_option_value(action_summary, option))


def _is_isolated_smoke_path(value: str, *, kind: str) -> bool:
    text = _command_path_text(value)
    if not text:
        return False
    if kind == "state":
        return "smoke" in text and not text.endswith("\\news-grasp-runner-state.json")
    return "smoke" in text and "news-grasp-logs" not in text


def _task_launcher_source_contract(path: Path) -> dict:
    try:
        source = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return {
            "ok": False,
            "reason": "task_launcher_unreadable",
            "missing_tokens": ["readable_source"],
            "modes": [],
            "missing_modes": [],
        }
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {
            "ok": False,
            "reason": "task_launcher_contract_invalid",
            "missing_tokens": ["valid_python_ast"],
            "modes": [],
            "missing_modes": [],
        }

    def _string_sequence(node: ast.AST) -> list[str]:
        if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return []
        return [
            str(item.value)
            for item in node.elts
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
        ]

    def _is_add_mode_argument(node: ast.Call) -> bool:
        if not node.args or not isinstance(node.args[0], ast.Constant) or node.args[0].value != "mode":
            return False
        function = node.func
        return bool(
            (isinstance(function, ast.Attribute) and function.attr == "add_argument")
            or (isinstance(function, ast.Name) and function.id == "add_argument")
        )

    def _is_runner_mode_test(node: ast.AST) -> bool:
        if not isinstance(node, ast.Compare) or len(node.ops) != 1 or not isinstance(node.ops[0], ast.Eq):
            return False
        if len(node.comparators) != 1:
            return False
        left = node.left
        right = node.comparators[0]
        return bool(
            isinstance(left, ast.Attribute)
            and isinstance(left.value, ast.Name)
            and left.value.id == "args"
            and left.attr == "mode"
            and isinstance(right, ast.Constant)
            and right.value == "runner"
        )

    mode_choices: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_add_mode_argument(node):
            continue
        choices_node = next((keyword.value for keyword in node.keywords if keyword.arg == "choices"), None)
        if choices_node is not None:
            mode_choices = _string_sequence(choices_node)
        break

    required_modes = {
        "runner",
        "bootstrap",
        "converge-runtime",
        "maintain-runtime",
        "scheduled-equivalent-nopublish",
    }
    missing_modes = sorted(required_modes - set(mode_choices))
    missing: list[str] = []
    if missing_modes:
        missing.append("mode_choices:" + "+".join(missing_modes))

    has_bootstrap_script = any(
        isinstance(node, ast.Constant) and node.value == "news-grasp-bootstrap.ps1"
        for node in ast.walk(tree)
    )
    if not has_bootstrap_script:
        missing.append("bootstrap_script:news-grasp-bootstrap.ps1")

    has_no_window = any(
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "subprocess"
        and node.attr == "CREATE_NO_WINDOW"
        for node in ast.walk(tree)
    )
    if not has_no_window:
        missing.append("subprocess.CREATE_NO_WINDOW")

    runner_args: list[str] = []
    bootstrap_args: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not any(
            isinstance(target, ast.Name) and target.id == "extra"
            for target in node.targets
        ):
            continue
        if not isinstance(node.value, ast.IfExp) or not _is_runner_mode_test(node.value.test):
            continue
        runner_args = _string_sequence(node.value.body)
        bootstrap_args = _string_sequence(node.value.orelse)
        break

    def _option_value(arguments: list[str], option: str) -> str:
        try:
            index = arguments.index(option)
        except ValueError:
            return ""
        return arguments[index + 1] if index + 1 < len(arguments) else ""

    required_runner_args = [
        "-Start",
        "-UseProductionRuntime",
        "-ScheduledTaskName",
        "News-Grasp Runner",
    ]
    required_bootstrap_args = [
        "-Start",
        "-UseProductionRuntime",
        "-ScheduledTaskName",
        "News-Grasp Bootstrap",
        "-SmokeTest",
        "-SkipSourceSync",
    ]
    if not all(item in runner_args for item in required_runner_args):
        missing.append("runner_args:UseProductionRuntime+ScheduledTaskName")
    if not all(item in bootstrap_args for item in required_bootstrap_args):
        missing.append("bootstrap_args:UseProductionRuntime+ScheduledTaskName")
    expected_bootstrap_options = {
        "-PollSeconds": "1",
        "-TimeoutMinutes": "2",
        "-StateFile": "ng-smoke-state.json",
        "-LogDir": "ng-smoke-logs",
    }
    for option, expected in expected_bootstrap_options.items():
        if _option_value(bootstrap_args, option) != expected:
            missing.append(f"bootstrap_args:{option}={expected}")
    return {
        "ok": not missing,
        "reason": "" if not missing else "task_launcher_contract_invalid",
        "missing_tokens": missing,
        "modes": sorted(mode_choices),
        "missing_modes": missing_modes,
        "timeout_minutes": _safe_int(_option_value(bootstrap_args, "-TimeoutMinutes")),
        "state_file": _option_value(bootstrap_args, "-StateFile"),
        "log_dir": _option_value(bootstrap_args, "-LogDir"),
    }


def _task_launcher_action_mode(action_summary: str, *, launcher_path_text: str, mode: str) -> bool:
    action_text = _command_path_text(action_summary)
    return bool(
        launcher_path_text
        and launcher_path_text in action_text
        and re.search(rf"(?i)(?:^|\s){re.escape(mode)}(?:\s|$)", action_summary)
    )


def _bootstrap_action_smoke_contract(
    action_summary: str,
    *,
    bootstrap_path_text: str,
    watcher_text: str,
    launcher_path_text: str = "",
    launcher_contract: dict | None = None,
) -> dict:
    action_text = _command_path_text(action_summary)
    timeout_minutes = _action_option_int(action_summary, "-TimeoutMinutes")
    state_file = _action_option_value(action_summary, "-StateFile")
    log_dir = _action_option_value(action_summary, "-LogDir")
    targets_live_bootstrap = bool(bootstrap_path_text in action_text)
    targets_live_watcher = bool(watcher_text in action_text)
    targets_live_task_launcher = bool(launcher_path_text and launcher_path_text in action_text)
    launcher_mode_ok = _task_launcher_action_mode(
        action_summary,
        launcher_path_text=launcher_path_text,
        mode="bootstrap",
    )
    launcher_ok = bool(targets_live_task_launcher and launcher_mode_ok and (launcher_contract or {}).get("ok"))
    if launcher_ok:
        targets_live_bootstrap = True
        timeout_minutes = _safe_int((launcher_contract or {}).get("timeout_minutes"))
        state_file = str((launcher_contract or {}).get("state_file") or "")
        log_dir = str((launcher_contract or {}).get("log_dir") or "")
    return {
        "targets_live_bootstrap": targets_live_bootstrap,
        "targets_live_watcher": targets_live_watcher,
        "targets_live_task_launcher": targets_live_task_launcher,
        "task_launcher_mode_ok": launcher_mode_ok,
        "is_smoke_test": _action_has_switch(action_summary, "-SmokeTest") or launcher_ok,
        "uses_short_timeout": isinstance(timeout_minutes, int) and timeout_minutes <= 2,
        "uses_isolated_state_log": _is_isolated_smoke_path(state_file, kind="state")
        and _is_isolated_smoke_path(log_dir, kind="log"),
        "state_file": state_file,
        "log_dir": log_dir,
        "timeout_minutes": timeout_minutes,
    }


def _runner_action_start_contract(
    action_summary: str,
    *,
    targets_live_watcher: bool,
    targets_live_bootstrap: bool,
    targets_live_runner: bool,
    targets_live_task_launcher: bool = False,
) -> dict:
    forbidden_switches = [
        "-SmokeTest",
        "-SkipSourceSync",
        "-Status",
        "-StartOnly",
        "-PreflightOnly",
        "-RecoverOnly",
        "-NoPublish",
        "-NoPush",
        "-Stage2EditorSmokeOnly",
        "-StopAfterEditorStart",
        "-StopBeforeDeepDive",
        "-ResumeFromStage",
    ]
    found_forbidden = [switch for switch in forbidden_switches if _action_has_switch(action_summary, switch)]
    requires_start = bool((targets_live_watcher or targets_live_bootstrap) and not targets_live_task_launcher)
    has_start = _action_has_switch(action_summary, "-Start")
    targets_known_entrypoint = bool(
        targets_live_watcher or targets_live_bootstrap or targets_live_runner or targets_live_task_launcher
    )
    return {
        "is_production_start": bool(
            targets_known_entrypoint
            and not found_forbidden
            and ((not requires_start) or has_start)
        ),
        "requires_start": requires_start,
        "has_start": has_start,
        "forbidden_switches": found_forbidden,
    }


def _runner_has_pre_run_bootstrap_interlock(live_runner_path: Path) -> bool:
    try:
        text = live_runner_path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return False
    try:
        marker_body = text.split("function Test-PreRunBootstrapSmokeMarker", 1)[1].split(
            "function Assert-PreRunBootstrapInterlock",
            1,
        )[0]
        interlock_body = text.split("function Assert-PreRunBootstrapInterlock", 1)[1].split(
            "function Convert-JsonStringArrayToStringList",
            1,
        )[0]
        reexec_body = text.split("function Invoke-SyncedRunnerReexec", 1)[1].split(
            "function Assert-RunnerBinaryInSync",
            1,
        )[0]
        sync_body = text.split("function Assert-RunnerBinaryInSync", 1)[1].split(
            "function Invoke-Logged",
            1,
        )[0]
        start_block = text.split("# ===== sentinel: 起動できた事実 =====", 1)[1].split(
            "$IsE2EOrDryRun",
            1,
        )[0]
    except IndexError:
        return False
    required_marker_body = (
        "$BootstrapSmokeEarliestMinutes",
        "$BootstrapSmokeFreshnessMinutes",
        "updated_at",
        "LastWriteTime",
        "TotalMinutes",
    )
    required_interlock_body = (
        "Start-Process",
        "$bootstrapArgs",
        "-SmokeTest",
        "-PollSeconds",
        "1",
        "-TimeoutMinutes",
        "2",
        "-StateFile",
        "$BootstrapSmokeStateFile",
        "-LogDir",
        "$BootstrapSmokeLogDir",
        "blocked_startup_self_repair_failed",
    )
    required_reexec_body = (
        "NEWS_GRASP_RUNNER_SYNC_REEXEC",
        "Get-RunnerScriptArguments",
        "Start-Process",
        "-Wait",
        "runner binary drift repaired; relaunching synced runner",
        "exit $exitCode",
    )
    required_sync_body = (
        "Test-NormalDailyPublishRun",
        "Assert-PreRunBootstrapInterlock -ForceRepair",
        "Invoke-SyncedRunnerReexec",
        "Invoke-RunnerBinarySyncApprovalBlock",
        "blocked_startup_self_repair_failed",
    )
    return bool(
        all(marker in text for marker in ("ng-smoke-state.json", "ng-smoke-logs", "function Test-NormalDailyPublishRun"))
        and all(marker in marker_body for marker in required_marker_body)
        and all(marker in interlock_body for marker in required_interlock_body)
        and all(marker in reexec_body for marker in required_reexec_body)
        and all(marker in sync_body for marker in required_sync_body)
        and sync_body.index("Assert-PreRunBootstrapInterlock -ForceRepair") < sync_body.index("Invoke-SyncedRunnerReexec")
        and sync_body.index("Test-NormalDailyPublishRun") < sync_body.index("Invoke-RunnerBinarySyncApprovalBlock")
        and "Assert-PreRunBootstrapInterlock" in start_block
        and "Assert-RunnerBinaryInSync" in start_block
        and start_block.index("Assert-PreRunBootstrapInterlock") < start_block.index("Assert-RunnerBinaryInSync")
    )


def _legacy_direct_clean_runtime_contract(live_runner_path: Path, live_bootstrap_path: Path) -> bool:
    """旧direct Task actionがclean production runtimeへ必ず移譲する契約を検証する。"""
    try:
        runner = live_runner_path.read_text(encoding="utf-8-sig", errors="replace")
        bootstrap = live_bootstrap_path.read_text(encoding="utf-8-sig", errors="replace")
        trampoline = runner.split("function Invoke-LegacyScheduledProductionTrampoline", 1)[1].split(
            "Invoke-LegacyScheduledProductionTrampoline", 1
        )[0]
        runner_prefix = runner.split("$RepoDir   = Resolve-NewsGraspRepoDir", 1)[0]
        scheduled_context = bootstrap.split("function Assert-ScheduledTaskLaunchContext", 1)[1].split(
            "function Invoke-BoundedGitFetch", 1
        )[0]
    except (OSError, IndexError):
        return False
    runner_tokens = (
        "Get-ScheduledTask -TaskName 'News-Grasp Runner'",
        "Get-ScheduledTaskInfo -TaskName 'News-Grasp Runner'",
        "news-grasp-runner\\.ps1",
        "-UseProductionRuntime",
        "-ScheduledTaskName",
        "-LegacyDirectEntrypoint",
        "exit $exitCode",
    )
    bootstrap_tokens = (
        "[switch] $LegacyDirectEntrypoint",
        "[bool] $AllowLegacyDirectEntrypoint",
        "news-grasp-runner\\.ps1",
        "-AllowLegacyDirectEntrypoint ([bool]$LegacyDirectEntrypoint)",
    )
    return bool(
        all(token in trampoline for token in runner_tokens)
        and "-SmokeTest" not in trampoline
        and "Invoke-LegacyScheduledProductionTrampoline" in runner_prefix
        and all(token in bootstrap for token in bootstrap_tokens)
        and "$AllowLegacyDirectEntrypoint" in scheduled_context
    )


def live_runner_readiness_manifest_ok(readiness: dict) -> bool:
    """publish-complete 履歴から再利用できる live ops readiness の正本判定。"""
    if not isinstance(readiness, dict) or not readiness.get("ok"):
        return False
    repo_runner = readiness.get("repo_runner") if isinstance(readiness.get("repo_runner"), dict) else {}
    live_runner = readiness.get("live_runner") if isinstance(readiness.get("live_runner"), dict) else {}
    repo_watcher = readiness.get("repo_watcher") if isinstance(readiness.get("repo_watcher"), dict) else {}
    live_watcher = readiness.get("live_watcher") if isinstance(readiness.get("live_watcher"), dict) else {}
    repo_bootstrap = readiness.get("repo_bootstrap") if isinstance(readiness.get("repo_bootstrap"), dict) else {}
    live_bootstrap = readiness.get("live_bootstrap") if isinstance(readiness.get("live_bootstrap"), dict) else {}
    repo_task_launcher = (
        readiness.get("repo_task_launcher") if isinstance(readiness.get("repo_task_launcher"), dict) else {}
    )
    live_task_launcher = (
        readiness.get("live_task_launcher") if isinstance(readiness.get("live_task_launcher"), dict) else {}
    )
    scheduled_task = readiness.get("scheduled_task") if isinstance(readiness.get("scheduled_task"), dict) else {}
    canary = readiness.get("canary") if isinstance(readiness.get("canary"), dict) else {}
    repo_sha = str(repo_runner.get("sha256") or "")
    live_sha = str(live_runner.get("sha256") or "")
    repo_watcher_sha = str(repo_watcher.get("sha256") or "")
    live_watcher_sha = str(live_watcher.get("sha256") or "")
    repo_bootstrap_sha = str(repo_bootstrap.get("sha256") or "")
    live_bootstrap_sha = str(live_bootstrap.get("sha256") or "")
    repo_task_launcher_sha = str(repo_task_launcher.get("sha256") or "")
    live_task_launcher_sha = str(live_task_launcher.get("sha256") or "")
    runner_schedule_ok = bool(
        scheduled_task.get("ok") is True
        and str(scheduled_task.get("state") or "") in {"Ready", "Running"}
        and _safe_int(scheduled_task.get("trigger_start_minutes")) == RUNNER_START_MINUTES
        and _time_minutes_from_text(scheduled_task.get("next_run_time")) == RUNNER_START_MINUTES
        and _safe_int(scheduled_task.get("number_of_missed_runs")) == 0
    )
    bootstrap_contract_ok = bool(
        scheduled_task.get("bootstrap_targets_live_bootstrap") is True
        and scheduled_task.get("bootstrap_action_is_smoke_test") is True
        and scheduled_task.get("bootstrap_action_uses_short_timeout") is True
        and scheduled_task.get("bootstrap_action_uses_isolated_state_log") is True
        and str(scheduled_task.get("bootstrap_state") or "") in {"Ready", "Running"}
        and scheduled_task.get("bootstrap_last_task_result") == 0
        and _safe_int(scheduled_task.get("bootstrap_trigger_start_minutes")) == BOOTSTRAP_START_MINUTES
        and _time_minutes_from_text(scheduled_task.get("bootstrap_next_run_time")) == BOOTSTRAP_START_MINUTES
        and _safe_int(scheduled_task.get("bootstrap_number_of_missed_runs")) == 0
        and scheduled_task.get("bootstrap_before_runner") is True
        and scheduled_task.get("bootstrap_repairs_before_run") is True
    )
    launcher_runner_target_ok = bool(
        scheduled_task.get("runner_action_is_production_start") is True
        and bootstrap_contract_ok
        and scheduled_task.get("targets_live_task_launcher") is True
        and scheduled_task.get("task_launcher_mode_ok") is True
        and scheduled_task.get("task_launcher_ready") is True
    )
    legacy_runner_target_ok = bool(
        scheduled_task.get("runner_action_is_production_start") is True
        and bootstrap_contract_ok
        and scheduled_task.get("targets_live_runner") is True
        and scheduled_task.get("legacy_direct_clean_runtime_trampoline") is True
        and scheduled_task.get("bootstrap_targets_live_task_launcher") is True
        and scheduled_task.get("bootstrap_task_launcher_mode_ok") is True
        and scheduled_task.get("task_launcher_ready") is True
    )
    runner_target_ok = launcher_runner_target_ok or legacy_runner_target_ok
    return bool(
        repo_sha
        and live_sha
        and repo_sha == live_sha
        and repo_watcher_sha
        and live_watcher_sha
        and repo_watcher_sha == live_watcher_sha
        and repo_bootstrap_sha
        and live_bootstrap_sha
        and repo_bootstrap_sha == live_bootstrap_sha
        and repo_task_launcher_sha
        and live_task_launcher_sha
        and repo_task_launcher_sha == live_task_launcher_sha
        and runner_schedule_ok
        and runner_target_ok
        and canary.get("ok") is True
        and str(canary.get("status") or "") == "smoke_ok"
    )


def _scheduled_task_action_summary(
    *,
    task_name: str = "News-Grasp Production",
    powershell_exe: str = "pwsh",
) -> str:
    safe_task_name = task_name.replace("'", "''")
    command = (
        "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); "
        "$OutputEncoding=[System.Text.UTF8Encoding]::new($false); "
        f"$task=Get-ScheduledTask -TaskName '{safe_task_name}' -ErrorAction Stop; "
        "(@($task.Actions) | ForEach-Object { "
        "(([string]$_.Execute + ' ' + [string]$_.Arguments).Trim()) "
        "}) -join ' ; '"
    )
    try:
        proc = subprocess.run(
            [powershell_exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],
            capture_output=True,
            text=False,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"unavailable: {exc}"
    stdout = _decode_scheduled_task_output(proc.stdout)
    stderr = _decode_scheduled_task_output(proc.stderr)
    if proc.returncode != 0:
        detail = (stderr or stdout).strip()
        return f"unavailable: {detail or f'rc={proc.returncode}'}"
    return stdout.strip()


def _decode_scheduled_task_output(data: bytes | str | None) -> str:
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    for encoding in ("utf-8-sig", "utf-8", "cp932", "mbcs"):
        try:
            return data.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", errors="replace")


def _scheduled_task_details(
    *,
    task_name: str = "News-Grasp Production",
    powershell_exe: str = "pwsh",
) -> dict:
    safe_task_name = task_name.replace("'", "''")
    command = (
        "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); "
        "$OutputEncoding=[System.Text.UTF8Encoding]::new($false); "
        f"$task=Get-ScheduledTask -TaskName '{safe_task_name}' -ErrorAction Stop; "
        f"$info=Get-ScheduledTaskInfo -TaskName '{safe_task_name}' -ErrorAction Stop; "
        "$actions=(@($task.Actions) | ForEach-Object { "
        "(([string]$_.Execute + ' ' + [string]$_.Arguments).Trim()) "
        "}) -join ' ; '; "
        "$actionRecords=@($task.Actions) | ForEach-Object { "
        "[ordered]@{ execute=[string]$_.Execute; arguments=[string]$_.Arguments } "
        "}; "
        "$triggers=@($task.Triggers) | ForEach-Object { "
        "[ordered]@{ start_boundary=[string]$_.StartBoundary; enabled=[bool]$_.Enabled } "
        "}; "
        "[ordered]@{ "
        "ok=$true; "
        "task_name=[string]$task.TaskName; "
        "state=[string]$task.State; "
        "action_summary=$actions; "
        "actions=$actionRecords; "
        "triggers=$triggers; "
        "last_run_time=[string]$info.LastRunTime; "
        "last_task_result=[int]$info.LastTaskResult; "
        "next_run_time=[string]$info.NextRunTime; "
        "number_of_missed_runs=[int]$info.NumberOfMissedRuns "
        "} | ConvertTo-Json -Depth 8"
    )
    try:
        proc = subprocess.run(
            [powershell_exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],
            capture_output=True,
            text=False,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "reason": f"unavailable: {exc}", "action_summary": f"unavailable: {exc}"}
    stdout = _decode_scheduled_task_output(proc.stdout)
    stderr = _decode_scheduled_task_output(proc.stderr)
    if proc.returncode != 0:
        detail = (stderr or stdout).strip()
        reason = f"unavailable: {detail or f'rc={proc.returncode}'}"
        return {"ok": False, "reason": reason, "action_summary": reason}
    try:
        payload = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return {"ok": False, "reason": "scheduled_task_json_invalid", "action_summary": stdout.strip()}
    return payload if isinstance(payload, dict) else {"ok": False, "reason": "scheduled_task_json_not_object"}


def _trigger_start_minutes(details: dict) -> int | None:
    triggers = details.get("triggers")
    if isinstance(triggers, dict):
        triggers = [triggers]
    if not isinstance(triggers, list):
        return None
    minutes: list[int] = []
    for trigger in triggers:
        if not isinstance(trigger, dict):
            continue
        if trigger.get("enabled") is False:
            continue
        boundary = str(trigger.get("start_boundary") or "")
        match = re.search(r"T(\d{2}):(\d{2})(?::\d{2})?", boundary)
        if match:
            minutes.append(int(match.group(1)) * 60 + int(match.group(2)))
    return min(minutes) if minutes else None


def _run_live_startup_canary(
    *,
    repo_root: Path,
    ops_repo_root: Path | None = None,
    startup_path: Path,
    date: str,
    live_runner_path: Path | None = None,
    high_cost_binding_path: Path | None = None,
    high_cost_binding_receipt_sha256: str = "",
    timeout_sec: int = 60,
    powershell_exe: str = "powershell.exe",
) -> dict:
    repo_root = repo_root.resolve()
    resolved_ops_root = ops_repo_root.resolve() if ops_repo_root is not None else None
    canary_root = repo_root / "build" / "live-runner-canary" / date
    log_dir = canary_root / "logs"
    state_file = canary_root / "state.json"
    log_file = log_dir / f"{date}.log"
    canary_root.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    if state_file.exists():
        state_file.unlink()
    if log_file.exists():
        log_file.unlink()
    command = [
        powershell_exe,
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(startup_path),
        "-Start",
        "-SmokeTest",
        "-SkipSourceSync",
        "-PollSeconds",
        "1",
        "-StaleMinutes",
        "2",
        "-TimeoutMinutes",
        "2",
        "-DateStamp",
        date,
        "-LogDir",
        str(log_dir),
        "-StateFile",
        str(state_file),
        "-PythonExe",
        sys.executable,
    ]
    if live_runner_path is not None:
        command += ["-RunnerPath", str(live_runner_path), "-BinDir", str(live_runner_path.parent)]
    if resolved_ops_root is not None:
        command += [
            "-UseProductionRuntime",
            "-RepoDir",
            str(resolved_ops_root),
            "-EvidenceRepoDir",
            str(resolved_ops_root),
        ]
    else:
        command += ["-RepoDir", str(repo_root)]
    if bool(high_cost_binding_path) != bool(high_cost_binding_receipt_sha256):
        return {
            "ok": False,
            "reason": "canary_binding_incomplete",
            "state_file": str(state_file),
            "log_file": str(log_file),
        }
    if high_cost_binding_path is not None:
        binding_authority = _validate_live_high_cost_binding_files(
            live_bin_root=startup_path.resolve(strict=True).parent,
            binding_path=high_cost_binding_path,
            binding_receipt_sha256=high_cost_binding_receipt_sha256,
            ops_repo_root=resolved_ops_root,
        )
        if not binding_authority.get("ok"):
            return {
                "ok": False,
                "reason": "canary_binding_authority_invalid",
                "state_file": str(state_file),
                "log_file": str(log_file),
            }
        command += [
            "-HighCostBindingPath",
            str(high_cost_binding_path),
            "-HighCostBindingReceiptSha256",
            high_cost_binding_receipt_sha256,
        ]
    try:
        canary_env = os.environ.copy()
        canary_env["PYTHONDONTWRITEBYTECODE"] = "1"
        proc = subprocess.run(
            command,
            cwd=repo_root,
            env=canary_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "reason": "canary_timeout",
            "state_file": str(state_file),
            "log_file": str(log_file),
            "timeout_sec": timeout_sec,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "ok": False,
            "reason": "canary_launch_failed",
            "state_file": str(state_file),
            "log_file": str(log_file),
            "detail": str(exc),
        }
    state: dict = {}
    if state_file.exists():
        try:
            loaded = json.loads(state_file.read_text(encoding="utf-8-sig"))
            if isinstance(loaded, dict):
                state = loaded
        except (json.JSONDecodeError, OSError, UnicodeDecodeError, ValueError):
            state = {}
    log_text = ""
    if log_file.exists():
        try:
            log_text = log_file.read_text(encoding="utf-8-sig", errors="replace")
        except (OSError, UnicodeDecodeError):
            log_text = ""
    status = str(state.get("status") or "")
    log_smoke_ok = "news-grasp-runner.ps1 SMOKE OK" in log_text
    stderr_tail = proc.stderr[-2000:]
    if proc.returncode != 0:
        reason = "canary_failed"
    elif "CommandNotFoundException" in proc.stderr or "Get-FileHash" in proc.stderr:
        reason = "canary_stderr_error"
    elif status != "smoke_ok":
        reason = "canary_state_not_smoke_ok"
    elif not log_smoke_ok:
        reason = "canary_log_missing_smoke_ok"
    else:
        reason = ""
    return {
        "ok": reason == "",
        "reason": reason,
        "returncode": proc.returncode,
        "status": status,
        "state_file": str(state_file),
        "log_file": str(log_file),
        "log_smoke_ok": log_smoke_ok,
        "stdout": proc.stdout[-2000:],
        "stderr": stderr_tail,
    }


def verify_live_runner_readiness(
    *,
    repo_root: Path,
    ops_repo_root: Path | None = None,
    date: str,
    live_runner_path: Path | None = None,
    live_watcher_path: Path | None = None,
    live_bootstrap_path: Path | None = None,
    live_task_launcher_path: Path | None = None,
    task_name: str = "News-Grasp Production",
    bootstrap_task_name: str = "News-Grasp Bootstrap",
    run_canary: bool = True,
    canary_timeout_sec: int = 60,
    powershell_exe: str = "pwsh",
) -> dict:
    repo_root = repo_root.resolve()
    ops_repo_root = (ops_repo_root or repo_root).resolve()
    live_runner_path = live_runner_path or _default_live_runner_path()
    live_watcher_path = live_watcher_path or _default_live_watcher_path()
    live_bootstrap_path = live_bootstrap_path or _default_live_bootstrap_path()
    live_task_launcher_path = live_task_launcher_path or _default_live_task_launcher_path()
    repo_runner = ops_repo_root / "scripts" / "ops" / "news-grasp-runner.ps1"
    repo_watcher = ops_repo_root / "scripts" / "ops" / "watch-news-grasp-runner.ps1"
    repo_bootstrap = ops_repo_root / "scripts" / "ops" / "news-grasp-bootstrap.ps1"
    repo_task_launcher = ops_repo_root / "scripts" / "ops" / "news-grasp-task-launcher.pyw"
    runner_checksum = compare_files(repo_runner, live_runner_path)
    watcher_checksum = compare_files(repo_watcher, live_watcher_path)
    bootstrap_checksum = compare_files(repo_bootstrap, live_bootstrap_path)
    task_launcher_checksum = compare_files(repo_task_launcher, live_task_launcher_path)
    task_launcher_contract = _task_launcher_source_contract(live_task_launcher_path)
    result = {
        "ok": False,
        "reason": "",
        "status": "not_ready",
        "date": date,
        "artifact_repo_root": str(repo_root),
        "ops_repo_root": str(ops_repo_root),
        "repo_runner": {
            "path": str(repo_runner),
            "exists": runner_checksum["repo_exists"],
            "sha256": runner_checksum["repo_sha256"],
        },
        "live_runner": {
            "path": str(live_runner_path),
            "exists": runner_checksum["live_exists"],
            "sha256": runner_checksum["live_sha256"],
        },
        "repo_watcher": {
            "path": str(repo_watcher),
            "exists": watcher_checksum["repo_exists"],
            "sha256": watcher_checksum["repo_sha256"],
        },
        "live_watcher": {
            "path": str(live_watcher_path),
            "exists": watcher_checksum["live_exists"],
            "sha256": watcher_checksum["live_sha256"],
        },
        "repo_bootstrap": {
            "path": str(repo_bootstrap),
            "exists": bootstrap_checksum["repo_exists"],
            "sha256": bootstrap_checksum["repo_sha256"],
        },
        "live_bootstrap": {
            "path": str(live_bootstrap_path),
            "exists": bootstrap_checksum["live_exists"],
            "sha256": bootstrap_checksum["live_sha256"],
        },
        "repo_task_launcher": {
            "path": str(repo_task_launcher),
            "exists": task_launcher_checksum["repo_exists"],
            "sha256": task_launcher_checksum["repo_sha256"],
        },
        "live_task_launcher": {
            "path": str(live_task_launcher_path),
            "exists": task_launcher_checksum["live_exists"],
            "sha256": task_launcher_checksum["live_sha256"],
            "contract": task_launcher_contract,
        },
        "scheduled_task": {},
        "next_run_readiness": {"ok": False, "status": "not_ready"},
        "last_scheduled_attempt": {"status": "unknown", "last_task_result": None, "last_run_time": ""},
        "canary": {},
    }
    if not runner_checksum["repo_exists"]:
        return {**result, "reason": "repo_runner_missing"}
    if not runner_checksum["live_exists"]:
        return {**result, "reason": "live_runner_missing"}
    if not watcher_checksum["repo_exists"]:
        return {**result, "reason": "repo_watcher_missing"}
    if not watcher_checksum["live_exists"]:
        return {**result, "reason": "live_watcher_missing"}
    if not bootstrap_checksum["repo_exists"]:
        return {**result, "reason": "repo_bootstrap_missing"}
    if not bootstrap_checksum["live_exists"]:
        return {**result, "reason": "live_bootstrap_missing"}
    if not runner_checksum["synced"]:
        return {**result, "reason": "live_runner_hash_mismatch"}
    if not watcher_checksum["synced"]:
        return {**result, "reason": "live_watcher_hash_mismatch"}
    if not bootstrap_checksum["synced"]:
        return {**result, "reason": "live_bootstrap_hash_mismatch"}

    task_details = _scheduled_task_details(task_name=task_name, powershell_exe=powershell_exe)
    last_task_result = task_details.get("last_task_result")
    if last_task_result == 0:
        last_scheduled_status = "succeeded"
    elif last_task_result is None:
        last_scheduled_status = "unknown"
    else:
        last_scheduled_status = "failed"
    result["last_scheduled_attempt"] = {
        "status": last_scheduled_status,
        "last_task_result": last_task_result,
        "last_run_time": task_details.get("last_run_time") or "",
    }
    action_summary = str(task_details.get("action_summary") or "")
    # stable launcher の authority と同じく、generation pointer は
    # production-runtime の親 (.news-grasp-runtime) に置かれる。
    active_generation_path = Path.home() / ".news-grasp-runtime" / "active-generation-v2.json"
    generation_id = ""
    if active_generation_path.is_file() and not active_generation_path.is_symlink():
        try:
            active_generation = json.loads(active_generation_path.read_text(encoding="utf-8-sig"))
            if isinstance(active_generation, dict):
                generation_id = str(active_generation.get("generationId") or "")
        except (OSError, UnicodeError, json.JSONDecodeError):
            generation_id = ""
    action_text = _command_path_text(action_summary)
    watcher_text = _command_path_text(live_watcher_path)
    runner_text = _command_path_text(live_runner_path)
    bootstrap_path_text = _command_path_text(live_bootstrap_path)
    task_launcher_path_text = _command_path_text(live_task_launcher_path)
    runner_targets_watcher = bool(action_summary and not action_summary.startswith("unavailable:") and watcher_text in action_text)
    runner_targets_runner = bool(action_summary and not action_summary.startswith("unavailable:") and runner_text in action_text)
    runner_targets_bootstrap = bool(
        action_summary and not action_summary.startswith("unavailable:") and bootstrap_path_text in action_text
    )
    runner_targets_task_launcher = bool(
        action_summary and not action_summary.startswith("unavailable:") and task_launcher_path_text in action_text
    )
    runner_task_launcher_mode_ok = _task_launcher_action_mode(
        action_summary,
        launcher_path_text=task_launcher_path_text,
        mode="runner",
    )
    task_launcher_ready = bool(task_launcher_checksum["synced"] and task_launcher_contract.get("ok"))
    runner_action_contract = _runner_action_start_contract(
        action_summary,
        targets_live_watcher=runner_targets_watcher,
        targets_live_bootstrap=runner_targets_bootstrap,
        targets_live_runner=runner_targets_runner,
        targets_live_task_launcher=bool(
            runner_targets_task_launcher and runner_task_launcher_mode_ok and task_launcher_ready
        ),
    )
    direct_runner_pre_run_interlock = _runner_has_pre_run_bootstrap_interlock(live_runner_path)
    direct_runner_pre_run_reexec = direct_runner_pre_run_interlock
    legacy_direct_clean_runtime_trampoline = _legacy_direct_clean_runtime_contract(
        live_runner_path, live_bootstrap_path
    )
    bootstrap_details = _scheduled_task_details(task_name=bootstrap_task_name, powershell_exe=powershell_exe)
    bootstrap_summary = str(bootstrap_details.get("action_summary") or "")
    bootstrap_text = _command_path_text(bootstrap_summary)
    bootstrap_action_contract = _bootstrap_action_smoke_contract(
        bootstrap_summary,
        bootstrap_path_text=bootstrap_path_text,
        watcher_text=watcher_text,
        launcher_path_text=task_launcher_path_text,
        launcher_contract=task_launcher_contract if task_launcher_checksum["synced"] else {"ok": False},
    )
    bootstrap_targets_watcher = bool(
        bootstrap_summary
        and not bootstrap_summary.startswith("unavailable:")
        and (watcher_text in bootstrap_text or bootstrap_path_text in bootstrap_text)
    )
    bootstrap_targets_task_launcher = bool(
        bootstrap_summary
        and not bootstrap_summary.startswith("unavailable:")
        and task_launcher_path_text in bootstrap_text
    )
    binding_authority = _validate_live_high_cost_binding_authority(
        task_details=task_details,
        bootstrap_details=bootstrap_details,
        live_task_launcher_path=live_task_launcher_path,
        task_name=task_name,
        bootstrap_task_name=bootstrap_task_name,
        ops_repo_root=ops_repo_root,
    )
    runner_state_ok = str(task_details.get("state") or "") in {"Ready", "Running"}
    bootstrap_state_ok = str(bootstrap_details.get("state") or "") in {"Ready", "Running"}
    bootstrap_last_result_ok = bootstrap_details.get("last_task_result") == 0
    runner_start = _trigger_start_minutes(task_details)
    bootstrap_start = _trigger_start_minutes(bootstrap_details)
    runner_trigger_ok = runner_start == RUNNER_START_MINUTES
    runner_next_run_ok = _next_run_time_matches(task_details, RUNNER_START_MINUTES)
    runner_missed_runs_ok = _missed_runs_zero(task_details)
    bootstrap_trigger_ok = bootstrap_start == BOOTSTRAP_START_MINUTES
    bootstrap_next_run_ok = _next_run_time_matches(bootstrap_details, BOOTSTRAP_START_MINUTES)
    bootstrap_missed_runs_ok = _missed_runs_zero(bootstrap_details)
    bootstrap_smoke_contract_ok = bool(
        bootstrap_action_contract["targets_live_bootstrap"]
        and bootstrap_action_contract["is_smoke_test"]
        and bootstrap_action_contract["uses_short_timeout"]
        and bootstrap_action_contract["uses_isolated_state_log"]
    )
    bootstrap_before_runner = (
        isinstance(bootstrap_start, int)
        and isinstance(runner_start, int)
        and bootstrap_start < runner_start
    )
    bootstrap_definition_ok = bool(
        bootstrap_smoke_contract_ok
        and bootstrap_state_ok
        and bootstrap_trigger_ok
        and bootstrap_next_run_ok
        and bootstrap_missed_runs_ok
        and bootstrap_before_runner
    )
    bootstrap_pre_run_ok = bool(bootstrap_definition_ok and bootstrap_last_result_ok)
    # missed run は過去の実行観測でありTask定義ではない。definitionへ混ぜると、
    # まさに復旧すべき日にScheduledRecoveryFull自身を遮断してしまう。
    runner_schedule_ok = bool(
        runner_state_ok
        and runner_trigger_ok
        and runner_next_run_ok
    )
    launcher_runner_ready = bool(
        runner_targets_task_launcher and runner_task_launcher_mode_ok and task_launcher_ready
    )
    legacy_direct_runner_ready = bool(
        runner_targets_runner
        and legacy_direct_clean_runtime_trampoline
        and bootstrap_targets_task_launcher
        and bootstrap_action_contract["task_launcher_mode_ok"]
        and task_launcher_ready
    )
    task_definition_ok = bool(
        runner_schedule_ok
        and runner_action_contract["is_production_start"]
        and bootstrap_definition_ok
        and binding_authority.get("ok") is True
        and (launcher_runner_ready or legacy_direct_runner_ready)
    )
    task_ok = bool(
        task_definition_ok
        and runner_missed_runs_ok
        and bootstrap_last_result_ok
    )
    result["scheduled_task"] = {
        "ok": task_ok,
        "definition_ok": task_definition_ok,
        "task_name": task_name,
        "action_summary": action_summary,
        "state": task_details.get("state"),
        "next_run_time": task_details.get("next_run_time"),
        "last_run_time": task_details.get("last_run_time"),
        "last_task_result": task_details.get("last_task_result"),
        "number_of_missed_runs": task_details.get("number_of_missed_runs"),
        "trigger_start_minutes": runner_start,
        "trigger_is_daily_0600": runner_trigger_ok,
        "next_run_time_is_0600": runner_next_run_ok,
        "number_of_missed_runs_ok": runner_missed_runs_ok,
        "runner_action_is_production_start": runner_action_contract["is_production_start"],
        "runner_action_requires_start": runner_action_contract["requires_start"],
        "runner_action_has_start": runner_action_contract["has_start"],
        "runner_action_forbidden_switches": runner_action_contract["forbidden_switches"],
        "targets_live_watcher": runner_targets_watcher,
        "targets_live_runner": runner_targets_runner,
        "targets_live_bootstrap": runner_targets_bootstrap,
        "targets_live_task_launcher": runner_targets_task_launcher,
        "task_launcher_mode_ok": runner_task_launcher_mode_ok,
        "task_launcher_ready": task_launcher_ready,
        "high_cost_binding_action_ok": binding_authority.get("ok") is True,
        "high_cost_binding_path": binding_authority.get("binding_path", ""),
        "high_cost_binding_receipt_sha256": binding_authority.get(
            "binding_receipt_sha256", ""
        ),
        "high_cost_binding_file_sha256": binding_authority.get("binding_file_sha256", ""),
        "direct_runner_pre_run_interlock": direct_runner_pre_run_interlock,
        "direct_runner_pre_run_reexec": direct_runner_pre_run_reexec,
        "legacy_direct_clean_runtime_trampoline": legacy_direct_clean_runtime_trampoline,
        "bootstrap_task_name": bootstrap_task_name,
        "bootstrap_action_summary": bootstrap_summary,
        "bootstrap_state": bootstrap_details.get("state"),
        "bootstrap_next_run_time": bootstrap_details.get("next_run_time"),
        "bootstrap_last_run_time": bootstrap_details.get("last_run_time"),
        "bootstrap_last_task_result": bootstrap_details.get("last_task_result"),
        "bootstrap_number_of_missed_runs": bootstrap_details.get("number_of_missed_runs"),
        "bootstrap_trigger_start_minutes": bootstrap_start,
        "bootstrap_trigger_is_0555": bootstrap_trigger_ok,
        "bootstrap_next_run_time_is_0555": bootstrap_next_run_ok,
        "bootstrap_number_of_missed_runs_ok": bootstrap_missed_runs_ok,
        "bootstrap_targets_watcher_or_bootstrap": bootstrap_targets_watcher,
        "bootstrap_targets_live_bootstrap": bootstrap_action_contract["targets_live_bootstrap"],
        "bootstrap_targets_live_watcher": bootstrap_action_contract["targets_live_watcher"],
        "bootstrap_targets_live_task_launcher": bootstrap_targets_task_launcher,
        "bootstrap_task_launcher_mode_ok": bootstrap_action_contract["task_launcher_mode_ok"],
        "bootstrap_action_is_smoke_test": bootstrap_action_contract["is_smoke_test"],
        "bootstrap_action_uses_short_timeout": bootstrap_action_contract["uses_short_timeout"],
        "bootstrap_action_uses_isolated_state_log": bootstrap_action_contract["uses_isolated_state_log"],
        "bootstrap_action_state_file": bootstrap_action_contract["state_file"],
        "bootstrap_action_log_dir": bootstrap_action_contract["log_dir"],
        "bootstrap_action_timeout_minutes": bootstrap_action_contract["timeout_minutes"],
        "bootstrap_before_runner": bootstrap_before_runner,
        "bootstrap_definition_ok": bootstrap_definition_ok,
        "bootstrap_last_observation_ok": bootstrap_last_result_ok,
        "bootstrap_repairs_before_run": bootstrap_pre_run_ok,
    }
    if not task_ok:
        if not task_details.get("ok"):
            reason = "scheduled_task_unavailable"
        elif not runner_state_ok:
            reason = "scheduled_task_disabled"
        elif not runner_trigger_ok:
            reason = "scheduled_task_not_0600"
        elif not runner_next_run_ok:
            reason = "scheduled_task_next_run_missing"
        elif not runner_missed_runs_ok:
            reason = "scheduled_task_missed_runs"
        elif not (runner_targets_watcher or runner_targets_bootstrap or runner_targets_runner or runner_targets_task_launcher):
            reason = "scheduled_task_target_mismatch"
        elif not runner_action_contract["is_production_start"]:
            reason = "scheduled_task_action_not_production_start"
        elif runner_targets_task_launcher and not task_launcher_checksum["synced"]:
            reason = "task_launcher_hash_mismatch"
        elif runner_targets_task_launcher and not task_launcher_contract.get("ok"):
            reason = "task_launcher_contract_invalid"
        elif runner_targets_task_launcher and not runner_task_launcher_mode_ok:
            reason = "task_launcher_runner_mode_invalid"
        elif bootstrap_targets_task_launcher and not task_launcher_checksum["synced"]:
            reason = "bootstrap_task_launcher_hash_mismatch"
        elif bootstrap_targets_task_launcher and not task_launcher_contract.get("ok"):
            reason = (
                "scheduled_task_launcher_required"
                if not runner_targets_task_launcher
                else "bootstrap_task_launcher_contract_invalid"
            )
        elif bootstrap_targets_task_launcher and not bootstrap_action_contract["task_launcher_mode_ok"]:
            reason = (
                "scheduled_task_launcher_required"
                if not runner_targets_task_launcher
                else "bootstrap_task_launcher_mode_invalid"
            )
        elif runner_targets_runner and not direct_runner_pre_run_interlock:
            reason = "direct_runner_pre_run_interlock_missing"
        elif runner_targets_runner and not direct_runner_pre_run_reexec:
            reason = "direct_runner_pre_run_reexec_missing"
        elif runner_targets_runner and not bootstrap_action_contract["targets_live_bootstrap"]:
            reason = "bootstrap_task_target_mismatch"
        elif runner_targets_runner and not bootstrap_smoke_contract_ok:
            reason = "bootstrap_task_smoke_contract_invalid"
        elif runner_targets_runner and not bootstrap_state_ok:
            reason = "bootstrap_task_disabled"
        elif runner_targets_runner and not bootstrap_trigger_ok:
            reason = "bootstrap_task_not_0555"
        elif runner_targets_runner and not bootstrap_next_run_ok:
            reason = "bootstrap_task_next_run_missing"
        elif runner_targets_runner and not bootstrap_missed_runs_ok:
            reason = "bootstrap_task_missed_runs"
        elif bootstrap_definition_ok and not bootstrap_last_result_ok:
            reason = "bootstrap_task_last_result_not_ok"
        elif runner_targets_runner and not bootstrap_before_runner:
            reason = "bootstrap_task_not_before_runner"
        elif not runner_targets_task_launcher:
            reason = "scheduled_task_launcher_required"
        elif not binding_authority.get("ok"):
            reason = str(binding_authority.get("reason") or "high_cost_binding_action_invalid")
        else:
            reason = "scheduled_task_target_mismatch"
        result["next_run_readiness"] = {
            "ok": False,
            "status": "not_ready",
            "reasonCode": reason,
            "taskDefinitionStatus": "ready" if task_definition_ok else "not_ready",
            "bootstrapObservationStatus": "green" if bootstrap_last_result_ok else "red",
        }
        return {**result, "reason": reason}

    if run_canary:
        canary = _run_live_startup_canary(
            repo_root=repo_root,
            ops_repo_root=ops_repo_root,
            startup_path=live_bootstrap_path
            if (
                runner_targets_bootstrap
                or runner_targets_task_launcher
                or (runner_targets_runner and bootstrap_action_contract["targets_live_bootstrap"])
            )
            else live_watcher_path,
            live_runner_path=live_runner_path,
            high_cost_binding_path=Path(str(binding_authority["binding_path"])),
            high_cost_binding_receipt_sha256=str(
                binding_authority["binding_receipt_sha256"]
            ),
            date=date,
            timeout_sec=canary_timeout_sec,
            powershell_exe=powershell_exe,
        )
        result["canary"] = canary
        if not canary.get("ok"):
            return {**result, "reason": str(canary.get("reason") or "canary_failed")}
    else:
        result["canary"] = {"ok": True, "skipped": True}
    ready_status = "ready" if last_scheduled_status == "succeeded" else f"ready_with_{last_scheduled_status}_last_schedule"
    result["next_run_readiness"] = {
        "ok": True,
        "status": "ready",
        "next_run_time": task_details.get("next_run_time"),
        "number_of_missed_runs": task_details.get("number_of_missed_runs"),
        "canary_status": result["canary"].get("status", "skipped"),
    }
    descriptor_path = Path.home() / ".codex" / "state" / "high-cost-operation" / "capability-v1.json"
    deadman_path = ops_repo_root / "scripts" / "ops" / "news-grasp-deadman.ps1"
    if generation_id or descriptor_path.is_file() or deadman_path.is_file():
        freshness = readiness_freshness_snapshot(
            generation_id=generation_id,
            descriptor_path=descriptor_path,
            task_definition=action_summary,
            deadman_path=deadman_path,
        )
        result["readinessFreshness"] = freshness
        result["next_run_readiness"]["freshness"] = freshness
    return {**result, "ok": True, "reason": "", "status": ready_status}


def normalize_failure_signature(
    *, gate_id: str, error_code: str, artifact_identity: str = "", url_or_category: str = ""
) -> str:
    host_or_category = url_or_category.strip().lower()
    if "://" in host_or_category:
        host_or_category = urlparse(host_or_category).netloc.lower()
    parts = [
        gate_id.strip().lower(),
        error_code.strip().lower(),
        artifact_identity.strip().lower(),
        host_or_category,
    ]
    return "|".join(p or "-" for p in parts)


def classify_phase0(snapshot: dict) -> dict:
    scheduler = snapshot.get("scheduler") or snapshot.get("scheduled_task") or {}
    state = snapshot.get("state") or snapshot.get("runner") or {}
    repo_bin = snapshot.get("repo_bin") or snapshot.get("bin") or {}
    git = snapshot.get("git") or {}
    pages = snapshot.get("pages") or {}
    logs = snapshot.get("logs") or {}
    content = snapshot.get("content") or {}
    expected_date = snapshot.get("expected_date")
    last_result = scheduler.get("last_result", scheduler.get("last_task_result"))

    if not scheduler.get("exists", True):
        return {"root_cause": "scheduled_task_missing", "layer": "scheduler"}
    if scheduler.get("last_run_missing") or scheduler.get("days_since_last_run", 0) >= 1:
        return {"root_cause": "no_run_detected", "layer": "scheduler"}
    if not logs.get("runner_invoked", True):
        return {"root_cause": "runner_not_started", "layer": "runner"}
    if repo_bin and repo_bin.get("synced") is False:
        return {"root_cause": "bin_drift", "layer": "runner_sync"}
    if state.get("status") == "running" and (
        state.get("process_alive") is False
        or (expected_date and state.get("date") and state.get("date") != expected_date)
    ):
        return {"root_cause": "stale_runner", "layer": "watcher"}
    if git.get("dirty_required_files"):
        return {"root_cause": "uncommitted_required_changes", "layer": "git"}
    if git.get("local_head") and git.get("remote_head") and git["local_head"] != git["remote_head"]:
        return {"root_cause": "push_not_reflected", "layer": "git"}
    if git.get("push_failed"):
        return {"root_cause": "push_failed", "layer": "git"}
    if pages.get("deployment_success") is False or pages.get("public_sentinel_ok") is False:
        return {"root_cause": "pages_not_reflected", "layer": "pages"}
    if content.get("gate_failed"):
        return {
            "root_cause": "content_gate_failed",
            "layer": "content",
            "gate_id": content.get("gate_id", ""),
        }
    if last_result not in (None, 0):
        return {"root_cause": "runner_failed", "layer": "runner"}
    return {"root_cause": "no_issue_detected", "layer": "none"}


def evaluate_deadman(
    *,
    state: dict | None,
    now: datetime,
    expected_date: str,
    max_ok_age_hours: int,
) -> dict:
    state = state or {}
    status = str(state.get("status") or "no_run_detected")
    updated = _parse_dt(state.get("updated_at"))
    state_date = str(state.get("date") or "")

    if status in ALERT_STATUSES:
        return {"alert": True, "reason": status, "status": status}
    if status != "ok":
        return {"alert": True, "reason": "no_ok_state", "status": status}
    if state_date != expected_date:
        return {"alert": True, "reason": "ok_not_for_expected_date", "status": status}
    if updated is None:
        return {"alert": True, "reason": "ok_without_timestamp", "status": status}
    if now - updated > timedelta(hours=max_ok_age_hours):
        return {"alert": True, "reason": "ok_too_old", "status": status}
    return {"alert": False, "reason": "", "status": status}


def emit_alert(record: dict, *, alert_log: Path, marker_path: Path, webhook_url: str = "") -> dict:
    alert_log.parent.mkdir(parents=True, exist_ok=True)
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    key = f"{record.get('date','')}|{record.get('reason','')}|{record.get('status','')}"
    if marker_path.exists():
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            marker = {}
        if marker.get("key") == key:
            return {"sent": False, "duplicate": True, "key": key}

    payload = {**record, "key": key, "alerted_at": datetime.now(timezone.utc).isoformat()}
    with alert_log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    marker_path.write_text(json.dumps({"key": key}, ensure_ascii=False, indent=2), encoding="utf-8")

    if webhook_url:
        data = json.dumps({"text": f"News-Grasp daily alert: {key}"}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as res:  # noqa: S310 - operator configured URL
            res.read()
    return {"sent": True, "duplicate": False, "key": key}


def _git_output(repo_root: Path, args: list[str]) -> str:
    cp = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if cp.returncode != 0:
        raise RuntimeError((cp.stderr or cp.stdout).strip())
    return cp.stdout.strip()


def _is_git_worktree(repo_root: Path) -> bool:
    return (repo_root / ".git").exists()


def _git_tree_has_path(repo_root: Path, commit: str, rel_path: str) -> bool | None:
    if not _is_git_worktree(repo_root):
        return None
    try:
        _git_output(repo_root, ["cat-file", "-e", f"{commit}:{rel_path}"])
    except RuntimeError:
        return False
    return True


def _commit_is_ancestor(repo_root: Path, ancestor: str, descendant: str) -> bool:
    if ancestor == descendant:
        return True
    if not _is_git_worktree(repo_root):
        return False
    try:
        _git_output(repo_root, ["merge-base", "--is-ancestor", ancestor, descendant])
    except RuntimeError:
        return False
    return True


def _latest_audio_for_publish(repo_root: Path, date: str) -> dict[str, str] | None:
    latest_path = repo_root / "build" / "tts" / "latest_audio.json"
    if not latest_path.exists():
        return None
    try:
        data = json.loads(latest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if data.get("latest_audio_date") != date:
        return None
    url = str(data.get("latest_audio_url") or "")
    if not url:
        return {"latest_audio_date": date, "latest_audio_url": ""}
    return {"latest_audio_date": date, "latest_audio_url": url}


def _fetch_text(url: str) -> str:
    with urllib.request.urlopen(url, timeout=20) as res:  # noqa: S310 - fixed public URL from runner config
        return res.read().decode("utf-8-sig", errors="replace")


def _extract_sw_version(text: str) -> str:
    match = re.search(r"SW_VERSION\s*=\s*['\"]([^'\"]+)['\"]", text)
    return match.group(1).strip() if match else ""


def verify_public_sw_version(
    *, repo_root: Path, public_base_url: str, source_head: str | None = None
) -> dict:
    local_sw = repo_root / "docs" / "sw.js"
    if source_head:
        try:
            expected_sw = _git_output(
                repo_root, ["show", f"{source_head}:docs/sw.js"]
            )
        except RuntimeError as exc:
            return {
                "ok": False,
                "reason": "source_sw_unavailable",
                "detail": str(exc),
                "source_head": source_head,
            }
    else:
        if not local_sw.exists():
            return {"ok": False, "reason": "local_sw_missing", "path": str(local_sw)}
        try:
            expected_sw = local_sw.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as exc:
            return {"ok": False, "reason": "local_sw_unreadable", "detail": str(exc), "path": str(local_sw)}
    local_version = _extract_sw_version(expected_sw)
    if not local_version:
        return {"ok": False, "reason": "local_sw_version_missing", "path": str(local_sw)}

    public_sw_url = urljoin(public_base_url.rstrip("/") + "/", "sw.js")
    try:
        public_version = _extract_sw_version(_fetch_text(public_sw_url))
    except (urllib.error.URLError, TimeoutError, UnicodeDecodeError) as exc:
        return {
            "ok": False,
            "reason": "public_sw_fetch_failed",
            "detail": str(exc),
            "url": public_sw_url,
            "local_sw_version": local_version,
        }
    if not public_version:
        return {
            "ok": False,
            "reason": "public_sw_version_missing",
            "url": public_sw_url,
            "local_sw_version": local_version,
        }
    if public_version != local_version:
        return {
            "ok": False,
            "reason": "sw_version_mismatch",
            "url": public_sw_url,
            "local_sw_version": local_version,
            "public_sw_version": public_version,
        }
    return {
        "ok": True,
        "reason": "",
        "url": public_sw_url,
        "local_sw_version": local_version,
        "public_sw_version": public_version,
        "source_head": source_head or "",
    }


def _url_head_ok(url: str) -> bool:
    req = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(req, timeout=20) as res:  # noqa: S310 - fixed public URL from runner config
        return int(getattr(res, "status", 200)) == 200


def verify_public_audio(*, repo_root: Path, date: str, public_base_url: str) -> dict:
    latest = _latest_audio_for_publish(repo_root, date)
    if latest is None:
        return {"checked": False, "ok": True, "reason": "no_audio_for_date"}
    audio_url = latest.get("latest_audio_url", "")
    if not audio_url:
        return {"checked": True, "ok": False, "reason": "audio_url_missing", "latest_audio_date": date}
    try:
        if not _url_head_ok(audio_url):
            return {"checked": True, "ok": False, "reason": "audio_url_not_200", "latest_audio_url": audio_url}
        base = public_base_url.rstrip("/") + "/"
        pages = {
            "home": base,
            "summary": urljoin(base, f"{date}/summary/"),
        }
        missing_from = [
            name
            for name, url in pages.items()
            if audio_url not in _fetch_text(url)
        ]
    except (urllib.error.URLError, TimeoutError, UnicodeDecodeError) as exc:
        return {
            "checked": True,
            "ok": False,
            "reason": "public_audio_verification_failed",
            "detail": str(exc),
            "latest_audio_url": audio_url,
        }
    if missing_from:
        return {
            "checked": True,
            "ok": False,
            "reason": "public_audio_missing",
            "missing_from": missing_from,
            "latest_audio_url": audio_url,
        }
    return {"checked": True, "ok": True, "latest_audio_url": audio_url}


def _canonical_public_text_sha256(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def verify_historical_public_archive(
    *, repo_root: Path, date: str, public_base_url: str
) -> dict:
    """最新号を維持したまま、過去号3面と同日音声2本の公開一致を検証する。"""

    base = public_base_url.rstrip("/") + "/"
    surfaces = {
        "daily": (repo_root / "docs" / date / "index.html", urljoin(base, f"{date}/")),
        "summary": (
            repo_root / "docs" / date / "summary" / "index.html",
            urljoin(base, f"{date}/summary/"),
        ),
        "deepdive": (
            repo_root / "docs" / "deepdive" / date / "index.html",
            urljoin(base, f"deepdive/{date}/"),
        ),
    }
    pages: dict[str, dict] = {}
    missing_local = [name for name, (path, _url) in surfaces.items() if not path.exists()]
    if missing_local:
        return {
            "ok": False,
            "reason": "historical_archive_local_missing",
            "missing": missing_local,
        }
    try:
        for name, (path, url) in surfaces.items():
            local_text = path.read_text(encoding="utf-8-sig")
            public_text = _fetch_text(url)
            local_sha256 = _canonical_public_text_sha256(local_text)
            public_sha256 = _canonical_public_text_sha256(public_text)
            pages[name] = {
                "url": url,
                "local_sha256": local_sha256,
                "public_sha256": public_sha256,
                "public_text": public_text,
            }
    except (OSError, urllib.error.URLError, TimeoutError, UnicodeDecodeError) as exc:
        return {
            "ok": False,
            "reason": "historical_archive_fetch_failed",
            "detail": str(exc),
            "pages": pages,
        }
    mismatched = [
        name
        for name, row in pages.items()
        if row["local_sha256"] != row["public_sha256"]
    ]
    if mismatched:
        return {
            "ok": False,
            "reason": "historical_archive_mismatch",
            "mismatched": mismatched,
            "pages": pages,
        }

    audio_specs = {
        "daily": ("summary", "audio-daily"),
        "deepdive": ("deepdive", "audio-deepdive"),
    }
    audio_urls: dict[str, str] = {}
    for name, (surface, release_tag) in audio_specs.items():
        pattern = re.compile(
            rf"https://github\.com/HIDEPON-UMG/News-Grasp/releases/download/"
            rf"{release_tag}/{re.escape(date)}\.mp3(?:\?[^\"'<>\s]+)?"
        )
        match = pattern.search(str(pages[surface]["public_text"]))
        if match is None:
            return {
                "ok": False,
                "reason": "historical_archive_audio_missing",
                "audio_kind": name,
                "pages": pages,
            }
        audio_urls[name] = match.group(0)
    try:
        unavailable = [name for name, url in audio_urls.items() if not _url_head_ok(url)]
    except (urllib.error.URLError, TimeoutError) as exc:
        return {
            "ok": False,
            "reason": "historical_archive_audio_fetch_failed",
            "detail": str(exc),
            "audio_urls": audio_urls,
            "pages": pages,
        }
    if unavailable:
        return {
            "ok": False,
            "reason": "historical_archive_audio_not_200",
            "unavailable": unavailable,
            "audio_urls": audio_urls,
            "pages": pages,
        }
    for row in pages.values():
        row.pop("public_text", None)
    return {
        "ok": True,
        "reason": "",
        "pages": pages,
        "audio": {"checked": True, "ok": True, "urls": audio_urls},
    }


def _load_podcast_row(state_path: Path, date: str) -> dict:
    if not state_path.exists():
        return {}
    try:
        data = json.loads(state_path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"_state_error": "podcast_state_corrupt"}
    row = data.get(date)
    return row if isinstance(row, dict) else {}


def _fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=20) as res:  # noqa: S310 - fixed public URL
        return json.loads(res.read().decode("utf-8-sig"))


def _title_from_watch_html(html: str, expected: str) -> str:
    match = re.search(r"<title>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    if match:
        title = re.sub(r"\s+", " ", match.group(1)).strip()
        suffix = " - YouTube"
        if title.endswith(suffix):
            title = title[: -len(suffix)].strip()
        if title:
            return title
    if expected in html:
        return expected
    return ""


def verify_podcast(
    *,
    date: str,
    state_path: Path,
    wait_sec: int = 0,
    poll_sec: int = 30,
    expected_title: str | None = None,
) -> dict:
    expected = expected_title or f"News-Grasp Daily News Briefing {date}"
    deadline = time.monotonic() + max(0, wait_sec)
    last: dict = {}
    while True:
        row = _load_podcast_row(state_path, date)
        if row.get("_state_error"):
            return {"ok": False, "reason": row["_state_error"], "state": str(state_path)}
        video_id = str(row.get("videoId") or "")
        playlist_id = str(row.get("playlistId") or "")
        status = str(row.get("status") or row.get("privacyStatus") or "")
        if not video_id:
            return {"ok": False, "reason": "public_podcast_missing", "state": str(state_path)}
        if status and status != "public":
            last = {"ok": False, "reason": "podcast_pending", "videoId": video_id, "status": status}
        else:
            try:
                watch_url = f"https://www.youtube.com/watch?v={quote(video_id)}"
                oembed_url = f"https://www.youtube.com/oembed?url={quote(watch_url, safe='')}&format=json"
                verification = "oembed_watch_playlist"
                try:
                    oembed = _fetch_json(oembed_url)
                    actual_title = str(oembed.get("title") or "")
                    if actual_title != expected:
                        return {
                            "ok": False,
                            "reason": "podcast_title_mismatch",
                            "videoId": video_id,
                            "expected_title": expected,
                            "actual_title": actual_title,
                        }
                except urllib.error.HTTPError as exc:
                    if exc.code != 401:
                        raise
                    actual_title = ""
                    verification = "watch_playlist_fallback"
                watch_html = _fetch_text(watch_url)
                if not actual_title:
                    actual_title = _title_from_watch_html(watch_html, expected)
                if expected not in watch_html and video_id not in watch_html:
                    last = {"ok": False, "reason": "podcast_watch_missing", "videoId": video_id}
                elif actual_title and actual_title != expected:
                    return {
                        "ok": False,
                        "reason": "podcast_title_mismatch",
                        "videoId": video_id,
                        "expected_title": expected,
                        "actual_title": actual_title,
                    }
                elif playlist_id:
                    playlist_url = f"https://www.youtube.com/playlist?list={quote(playlist_id)}"
                    playlist_html = _fetch_text(playlist_url)
                    if video_id not in playlist_html:
                        last = {
                            "ok": False,
                            "reason": "podcast_playlist_missing",
                            "videoId": video_id,
                            "playlistId": playlist_id,
                        }
                    else:
                        primary_playlist_id = str(row.get("primaryPodcastPlaylistId") or "")
                        if primary_playlist_id and primary_playlist_id != playlist_id:
                            primary_playlist_url = f"https://www.youtube.com/playlist?list={quote(primary_playlist_id)}"
                            primary_playlist_html = _fetch_text(primary_playlist_url)
                            if video_id not in primary_playlist_html:
                                last = {
                                    "ok": False,
                                    "reason": "primary_podcast_playlist_missing",
                                    "videoId": video_id,
                                    "playlistId": primary_playlist_id,
                                }
                                if time.monotonic() >= deadline:
                                    return last
                                time.sleep(max(1, poll_sec))
                                continue
                        return {
                            "ok": True,
                            "reason": "",
                            "videoId": video_id,
                            "playlistId": playlist_id,
                            "primaryPodcastPlaylistId": primary_playlist_id,
                            "title": actual_title,
                            "verification": verification,
                        }
                else:
                    return {
                        "ok": False,
                        "reason": "podcast_playlist_missing",
                        "videoId": video_id,
                        "playlistId": "",
                    }
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                last = {"ok": False, "reason": "podcast_pending", "videoId": video_id, "detail": str(exc)}
        if time.monotonic() >= deadline:
            return last or {"ok": False, "reason": "public_podcast_missing", "state": str(state_path)}
        time.sleep(max(1, poll_sec))


def _repo_slug_from_remote_url(remote_url: str) -> tuple[str, str] | None:
    value = remote_url.strip().rstrip("/")
    if value.endswith(".git"):
        value = value[:-4]
    match = re.fullmatch(r"https://github\.com/([^/\s]+)/([^/\s]+)", value)
    if not match:
        match = re.fullmatch(r"git@github\.com:([^/\s]+)/([^/\s]+)", value)
    if not match:
        return None
    owner, repo = match.group(1), match.group(2)
    if not owner or not repo:
        return None
    return owner, repo


def _gh_auth_token() -> str:
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        token = os.environ.get(name, "").strip()
        if token:
            return token
    try:
        proc = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def _github_headers(token: str = "") -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2026-03-10",
        "User-Agent": "News-Grasp-PublishVerifier/1.0",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _github_api_json(url: str) -> dict:
    token = os.environ.get("GITHUB_TOKEN", "").strip() or os.environ.get("GH_TOKEN", "").strip()
    tried_auth = bool(token)
    try:
        req = urllib.request.Request(url, headers=_github_headers(token))
        with urllib.request.urlopen(req, timeout=10) as res:  # noqa: S310 - fixed GitHub API URL derived from origin
            payload = json.loads(res.read().decode("utf-8-sig"))
    except urllib.error.HTTPError as exc:
        if exc.code not in {401, 403, 404} or tried_auth:
            raise
        token = _gh_auth_token()
        if not token:
            raise
        req = urllib.request.Request(url, headers=_github_headers(token))
        with urllib.request.urlopen(req, timeout=10) as res:  # noqa: S310 - fixed GitHub API URL derived from origin
            payload = json.loads(res.read().decode("utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("response_not_object")
    return payload


def _github_api_post(url: str, payload: dict) -> int:
    token = os.environ.get("GITHUB_TOKEN", "").strip() or os.environ.get("GH_TOKEN", "").strip()
    tried_auth = bool(token)
    data = json.dumps(payload).encode("utf-8")
    headers = {**_github_headers(token), "Content-Type": "application/json"}
    try:
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10) as res:  # noqa: S310 - fixed GitHub API URL derived from origin
            return int(getattr(res, "status", 204))
    except urllib.error.HTTPError as exc:
        if exc.code not in {401, 403, 404} or tried_auth:
            raise
        token = _gh_auth_token()
        if not token:
            raise
        headers = {**_github_headers(token), "Content-Type": "application/json"}
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10) as res:  # noqa: S310 - fixed GitHub API URL derived from origin
            return int(getattr(res, "status", 204))


def _verify_workflow_pages_status(*, owner: str, repo: str, branch: str, expected_commit: str, latest_detail: str = "") -> dict:
    url = f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}/pages"
    try:
        pages = _github_api_json(url)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        return {"ok": False, "reason": "pages_build_unavailable", "url": url, "detail": str(exc), "latest_detail": latest_detail}
    status = str(pages.get("status") or "")
    build_type = str(pages.get("build_type") or "")
    source = pages.get("source") if isinstance(pages.get("source"), dict) else {}
    source_branch = str(source.get("branch") or "")
    source_path = str(source.get("path") or "")
    if build_type != "workflow":
        return {
            "ok": False,
            "reason": "pages_build_unavailable",
            "url": url,
            "status": status,
            "build_type": build_type,
            "detail": "pages_build_latest_unavailable_for_non_workflow",
            "latest_detail": latest_detail,
        }
    if status != "built":
        return {
            "ok": False,
            "reason": "pages_build_not_built",
            "url": url,
            "status": status,
            "build_type": build_type,
            "source": source,
            "latest_detail": latest_detail,
        }
    if source_branch and source_branch != branch:
        return {
            "ok": False,
            "reason": "pages_build_commit_mismatch",
            "url": url,
            "status": status,
            "build_type": build_type,
            "source": source,
            "expected_branch": branch,
        }
    return {
        "ok": True,
        "reason": "",
        "status": status,
        "commit": expected_commit,
        "url": url,
        "build_type": build_type,
        "source_branch": source_branch,
        "source_path": source_path,
        "latest_detail": latest_detail,
    }


def verify_pages_build(repo_root: Path, remote: str, expected_commit: str, branch: str = "main") -> dict:
    """GitHub Pages latest build が対象 commit で built であることを検証する。"""
    try:
        remote_url = _git_output(repo_root, ["config", "--get", f"remote.{remote}.url"])
    except Exception as exc:
        return {"ok": False, "reason": "pages_remote_unparseable", "detail": str(exc)}
    slug = _repo_slug_from_remote_url(remote_url)
    if slug is None:
        return {"ok": False, "reason": "pages_remote_unparseable", "remote_url": remote_url}
    owner, repo = slug
    url = f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}/pages/builds/latest"
    try:
        build = _github_api_json(url)
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            return {"ok": False, "reason": "pages_build_unavailable", "url": url, "detail": str(exc)}
        return _verify_workflow_pages_status(
            owner=owner,
            repo=repo,
            branch=branch,
            expected_commit=expected_commit,
            latest_detail=str(exc),
        )
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        return {"ok": False, "reason": "pages_build_unavailable", "url": url, "detail": str(exc)}
    if not isinstance(build, dict):
        return {"ok": False, "reason": "pages_build_unavailable", "url": url, "detail": "response_not_object"}
    status = str(build.get("status") or "")
    commit = str(build.get("commit") or "")
    if status != "built":
        return {"ok": False, "reason": "pages_build_not_built", "status": status, "commit": commit, "url": url}
    if commit != expected_commit:
        workflow_pages = _verify_workflow_pages_status(
            owner=owner,
            repo=repo,
            branch=branch,
            expected_commit=expected_commit,
            latest_detail=f"latest_commit_mismatch:{commit}",
        )
        if workflow_pages["ok"]:
            return {**workflow_pages, "latest_build": {"status": status, "commit": commit, "url": url}}
        return {"ok": False, "reason": "pages_build_commit_mismatch", "status": status, "commit": commit, "expected_commit": expected_commit, "url": url, "workflow_pages": workflow_pages}
    return {"ok": True, "reason": "", "status": status, "commit": commit, "url": url}


def verify_deploy_workflow(repo_root: Path, remote: str, branch: str, expected_commit: str) -> dict:
    """Deploy Pages workflow が対象 commit で success したことを検証する。"""
    workflow_file = "deploy-pages.yml"
    workflow_path = repo_root / ".github" / "workflows" / workflow_file
    if not workflow_path.exists():
        return {"ok": False, "reason": "deploy_workflow_unavailable", "detail": f"workflow_missing:{workflow_file}"}
    try:
        remote_url = _git_output(repo_root, ["config", "--get", f"remote.{remote}.url"])
    except Exception as exc:
        return {"ok": False, "reason": "deploy_workflow_unavailable", "detail": str(exc)}
    slug = _repo_slug_from_remote_url(remote_url)
    if slug is None:
        return {"ok": False, "reason": "deploy_workflow_unavailable", "remote_url": remote_url}
    owner, repo = slug
    query = urlencode({"branch": branch, "head_sha": expected_commit, "per_page": 10})
    url = (
        f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}/actions/workflows/"
        f"{quote(workflow_file, safe='')}/runs?{query}"
    )
    try:
        payload = _github_api_json(url)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        return {"ok": False, "reason": "deploy_workflow_unavailable", "url": url, "detail": str(exc)}
    if not isinstance(payload, dict) or not isinstance(payload.get("workflow_runs"), list):
        return {"ok": False, "reason": "deploy_workflow_unavailable", "url": url, "detail": "workflow_runs_not_list"}
    runs = payload["workflow_runs"]
    if not runs:
        return {"ok": False, "reason": "deploy_workflow_not_success", "url": url, "workflow_file": workflow_file}
    matching_runs = [run for run in runs if isinstance(run, dict) and str(run.get("head_sha") or "") == expected_commit]
    if not matching_runs:
        first_head = ""
        for run in runs:
            if isinstance(run, dict):
                first_head = str(run.get("head_sha") or "")
                break
        return {
            "ok": False,
            "reason": "deploy_workflow_commit_mismatch",
            "url": url,
            "head_sha": first_head,
            "expected_commit": expected_commit,
            "workflow_file": workflow_file,
        }
    run = matching_runs[0]
    status = str(run.get("status") or "")
    conclusion = str(run.get("conclusion") or "")
    if status != "completed" or conclusion != "success":
        result = {
            "ok": False,
            "reason": "deploy_workflow_not_success",
            "status": status,
            "conclusion": conclusion,
            "head_sha": str(run.get("head_sha") or ""),
            "run_id": run.get("id", ""),
            "html_url": run.get("html_url", ""),
            "url": url,
            "workflow_file": workflow_file,
        }
        recovery = _deploy_workflow_fresh_dispatch_recovery(result=result, branch=branch, remote=remote)
        if recovery is not None:
            result["recovery"] = recovery
        return result
    return {
        "ok": True,
        "reason": "",
        "status": status,
        "conclusion": conclusion,
        "head_sha": str(run.get("head_sha") or ""),
        "event": str(run.get("event") or ""),
        "run_id": run.get("id", ""),
        "html_url": run.get("html_url", ""),
        "url": url,
        "workflow_file": workflow_file,
    }


def verify_deploy_workflow_covering_deploy_head(
    *, repo_root: Path, remote: str, branch: str, source_head: str, deploy_relevant_head: str
) -> dict:
    """同一docs treeを実際にdeployした祖先push tipを検証する。"""
    workflow_file = "deploy-pages.yml"
    if not (repo_root / ".github" / "workflows" / workflow_file).exists():
        return {
            "ok": False,
            "reason": "deploy_workflow_unavailable",
            "detail": f"workflow_missing:{workflow_file}",
        }
    try:
        remote_url = _git_output(repo_root, ["config", "--get", f"remote.{remote}.url"])
        ancestors = set(_git_output(repo_root, ["rev-list", source_head]).splitlines())
    except Exception as exc:
        return {"ok": False, "reason": "deploy_workflow_unavailable", "detail": str(exc)}
    slug = _repo_slug_from_remote_url(remote_url)
    if slug is None:
        return {"ok": False, "reason": "deploy_workflow_unavailable", "remote_url": remote_url}
    owner, repo = slug
    query = urlencode({"branch": branch, "per_page": 50})
    url = (
        f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}/actions/workflows/"
        f"{quote(workflow_file, safe='')}/runs?{query}"
    )
    try:
        payload = _github_api_json(url)
    except (
        urllib.error.HTTPError,
        urllib.error.URLError,
        TimeoutError,
        json.JSONDecodeError,
        UnicodeDecodeError,
        ValueError,
    ) as exc:
        return {"ok": False, "reason": "deploy_workflow_unavailable", "url": url, "detail": str(exc)}
    runs = payload.get("workflow_runs") if isinstance(payload, dict) else None
    if not isinstance(runs, list):
        return {
            "ok": False,
            "reason": "deploy_workflow_unavailable",
            "url": url,
            "detail": "workflow_runs_not_list",
        }
    for run in runs:
        if not isinstance(run, dict):
            continue
        candidate_head = str(run.get("head_sha") or "")
        if candidate_head not in ancestors:
            continue
        resolution = resolve_deploy_head(repo_root=repo_root, source_head=candidate_head)
        if not resolution.get("ok") or resolution.get("deploy_head") != deploy_relevant_head:
            continue
        status = str(run.get("status") or "")
        conclusion = str(run.get("conclusion") or "")
        return {
            "ok": status == "completed" and conclusion == "success",
            "reason": "" if status == "completed" and conclusion == "success" else "deploy_workflow_not_success",
            "status": status,
            "conclusion": conclusion,
            "head_sha": candidate_head,
            "covered_deploy_head": deploy_relevant_head,
            "event": str(run.get("event") or ""),
            "run_id": run.get("id", ""),
            "html_url": run.get("html_url", ""),
            "url": url,
            "workflow_file": workflow_file,
            "coverage_resolution": resolution,
        }
    return {
        "ok": False,
        "reason": "deploy_workflow_not_success",
        "url": url,
        "workflow_file": workflow_file,
        "covered_deploy_head": deploy_relevant_head,
    }


def wait_for_deploy_workflow_covering_deploy_head(
    *,
    repo_root: Path,
    remote: str,
    branch: str,
    source_head: str,
    deploy_relevant_head: str,
    deadline: float,
    poll_sec: int,
) -> dict:
    while True:
        result = verify_deploy_workflow_covering_deploy_head(
            repo_root=repo_root,
            remote=remote,
            branch=branch,
            source_head=source_head,
            deploy_relevant_head=deploy_relevant_head,
        )
        if result.get("ok") or not _is_retryable_deploy_workflow(result):
            return result
        if time.monotonic() >= deadline:
            return {**result, "detail": "deploy_workflow_wait_timeout"}
        time.sleep(max(1, poll_sec))


def _deploy_workflow_fresh_dispatch_recovery(*, result: dict, branch: str, remote: str) -> dict | None:
    if result.get("ok") or result.get("reason") != "deploy_workflow_not_success":
        return None
    status = str(result.get("status") or "")
    conclusion = str(result.get("conclusion") or "")
    if status != "completed" or not conclusion or conclusion == "success":
        return None
    return {
        "action": "workflow_dispatch",
        "workflow_file": "deploy-pages.yml",
        "branch": branch,
        "remote": remote,
        "reason": "completed_failure",
        "command": [
            "python",
            "-m",
            "tools.daily_self_heal",
            "dispatch-deploy-workflow",
            "--repo-root",
            ".",
            "--remote",
            remote,
            "--branch",
            branch,
        ],
    }


def dispatch_deploy_workflow_if_failed(repo_root: Path, remote: str, branch: str) -> dict:
    """同一 HEAD の Deploy Pages が completed/failure のときだけ fresh workflow dispatch する。"""
    workflow_file = "deploy-pages.yml"
    try:
        expected_commit = _git_output(repo_root, ["rev-parse", "HEAD"])
    except Exception as exc:
        return {"ok": False, "reason": "deploy_workflow_dispatch_unavailable", "detail": str(exc)}
    current = verify_deploy_workflow(
        repo_root=repo_root,
        remote=remote,
        branch=branch,
        expected_commit=expected_commit,
    )
    recovery = _deploy_workflow_fresh_dispatch_recovery(result=current, branch=branch, remote=remote)
    if recovery is None:
        return {
            "ok": False,
            "reason": "deploy_workflow_dispatch_not_applicable",
            "deploy_workflow": current,
        }
    try:
        remote_url = _git_output(repo_root, ["config", "--get", f"remote.{remote}.url"])
    except Exception as exc:
        return {"ok": False, "reason": "deploy_workflow_dispatch_unavailable", "detail": str(exc)}
    slug = _repo_slug_from_remote_url(remote_url)
    if slug is None:
        return {"ok": False, "reason": "deploy_workflow_dispatch_unavailable", "remote_url": remote_url}
    owner, repo = slug
    url = (
        f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}/actions/workflows/"
        f"{quote(workflow_file, safe='')}/dispatches"
    )
    try:
        status = _github_api_post(url, {"ref": branch})
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, UnicodeEncodeError, ValueError) as exc:
        return {
            "ok": False,
            "reason": "deploy_workflow_dispatch_failed",
            "url": url,
            "detail": str(exc),
            "deploy_workflow": current,
        }
    return {
        "ok": True,
        "reason": "",
        "action": "workflow_dispatch",
        "workflow_file": workflow_file,
        "branch": branch,
        "status": status,
        "expected_commit": expected_commit,
        "url": url,
        "deploy_workflow": current,
    }


def _is_retryable_deploy_workflow(result: dict) -> bool:
    if result.get("ok"):
        return False
    if result.get("reason") != "deploy_workflow_not_success":
        return False
    status = str(result.get("status") or "")
    conclusion = str(result.get("conclusion") or "")
    if status == "completed" and conclusion and conclusion != "success":
        return False
    return status in {"", "queued", "requested", "waiting", "pending", "in_progress"}


def wait_for_deploy_workflow(
    *,
    repo_root: Path,
    remote: str,
    branch: str,
    expected_commit: str,
    deadline: float,
    poll_sec: int,
) -> dict:
    """Deploy Pages workflow の transient pending だけを同一 deadline 内で待つ。"""
    while True:
        result = verify_deploy_workflow(
            repo_root=repo_root,
            remote=remote,
            branch=branch,
            expected_commit=expected_commit,
        )
        if result.get("ok") or not _is_retryable_deploy_workflow(result):
            return result
        if time.monotonic() >= deadline:
            return {**result, "detail": "deploy_workflow_wait_timeout"}
        time.sleep(max(1, poll_sec))


_DEPLOY_RELEVANT_PATHS = ("docs", ".github/workflows/deploy-pages.yml")
_ISSUE_PUBLIC_PATHS = (
    "docs/index.html",
    "docs/publish-status.json",
)


def resolve_issue_public_tree_equivalence(
    *, repo_root: Path, artifact_head: str, remote_head: str, date: str
) -> dict:
    """後続commitが対象日付の公開面を変えていないことをtree objectで証明する。"""
    paths: dict[str, dict[str, str]] = {}
    issue_paths = list(_ISSUE_PUBLIC_PATHS)
    issue_paths.extend(required_published_docs_artifacts(date))
    issue_paths.append(f"docs/deepdive/{date}/index.html")
    issue_paths.extend(
        f"docs/{CATEGORY_PATHS[cat_id]['docs_segment']}/index.html"
        for cat_id in scheduled_category_ids(date)
    )
    for path in dict.fromkeys(issue_paths):
        try:
            artifact_tree = _git_output(
                repo_root, ["rev-parse", f"{artifact_head}:{path}"]
            )
            remote_tree = _git_output(
                repo_root, ["rev-parse", f"{remote_head}:{path}"]
            )
        except RuntimeError as exc:
            return {
                "ok": False,
                "reason": "issue_public_tree_unavailable",
                "detail": str(exc),
                "path": path,
                "paths": paths,
            }
        paths[path] = {
            "artifactTree": artifact_tree,
            "remoteTree": remote_tree,
        }
        if not artifact_tree or artifact_tree != remote_tree:
            return {
                "ok": False,
                "reason": "issue_public_tree_changed",
                "path": path,
                "paths": paths,
            }
    return {"ok": True, "reason": "", "paths": paths}


def resolve_deploy_head(*, repo_root: Path, source_head: str) -> dict:
    """制御コード HEAD から、Pages が実際に公開する直近 commit を解決する。"""
    try:
        deploy_head = _git_output(
            repo_root,
            ["rev-list", "-1", source_head, "--", *_DEPLOY_RELEVANT_PATHS],
        )
    except RuntimeError as exc:
        return {
            "ok": False,
            "reason": "deploy_head_unavailable",
            "detail": str(exc),
            "source_head": source_head,
            "deploy_head": "",
            "deploy_relevant_paths": list(_DEPLOY_RELEVANT_PATHS),
        }
    if not deploy_head:
        return {
            "ok": False,
            "reason": "deploy_relevant_commit_missing",
            "source_head": source_head,
            "deploy_head": "",
            "deploy_relevant_paths": list(_DEPLOY_RELEVANT_PATHS),
        }
    return {
        "ok": True,
        "reason": "",
        "source_head": source_head,
        "deploy_head": deploy_head,
        "resolution": (
            "source_head_is_deploy_relevant"
            if deploy_head == source_head
            else "latest_deploy_relevant_ancestor"
        ),
        "deploy_relevant_paths": list(_DEPLOY_RELEVANT_PATHS),
    }


def verify_publish(
    *,
    repo_root: Path,
    date: str,
    remote: str,
    branch: str,
    public_base_url: str,
    wait_sec: int,
    poll_sec: int,
    require_podcast: bool = False,
    podcast_state_path: Path | None = None,
) -> dict:
    artifact_head = _git_output(repo_root, ["rev-parse", "HEAD"])
    remote_head = _git_output(repo_root, ["ls-remote", remote, f"refs/heads/{branch}"]).split()[0]
    source_head = artifact_head
    issue_tree_resolution: dict = {
        "ok": True,
        "reason": "artifact_head_is_remote_head",
        "paths": {},
    }
    if artifact_head != remote_head:
        if not _commit_is_ancestor(repo_root, artifact_head, remote_head):
            return {
                "ok": False,
                "reason": "remote_head_mismatch",
                "artifact_head": artifact_head,
                "local_head": artifact_head,
                "remote_head": remote_head,
            }
        remote_resolution = resolve_deploy_head(
            repo_root=repo_root, source_head=remote_head
        )
        if not remote_resolution.get("ok"):
            return {
                "ok": False,
                "reason": "artifact_publish_head_stale",
                "artifact_head": artifact_head,
                "local_head": artifact_head,
                "remote_head": remote_head,
                "deploy_head_resolution": remote_resolution,
            }
        if remote_resolution.get("deploy_head") != artifact_head:
            issue_tree_resolution = resolve_issue_public_tree_equivalence(
                repo_root=repo_root,
                artifact_head=artifact_head,
                remote_head=remote_head,
                date=date,
            )
            if not issue_tree_resolution.get("ok"):
                return {
                    "ok": False,
                    "reason": "artifact_publish_head_stale",
                    "artifact_head": artifact_head,
                    "local_head": artifact_head,
                    "remote_head": remote_head,
                    "deploy_head_resolution": remote_resolution,
                    "issue_public_tree_resolution": issue_tree_resolution,
                }
        else:
            issue_tree_resolution = {
                "ok": True,
                "reason": "deploy_tree_unchanged",
                "paths": {},
            }
        source_head = remote_head
    if _is_git_worktree(repo_root):
        deploy_resolution = resolve_deploy_head(
            repo_root=repo_root, source_head=source_head
        )
        if not deploy_resolution["ok"]:
            return {
                "ok": False,
                "reason": deploy_resolution["reason"],
                "artifact_head": artifact_head,
                "local_head": source_head,
                "remote_head": remote_head,
                "deploy_head_resolution": deploy_resolution,
            }
    else:
        # unit fixture など git metadata を持たない呼出元は従来契約を保つ。
        deploy_resolution = {
            "ok": True,
            "reason": "",
            "source_head": source_head,
            "deploy_head": source_head,
            "resolution": "source_head_without_git_metadata",
            "deploy_relevant_paths": list(_DEPLOY_RELEVANT_PATHS),
        }
    deploy_relevant_head = str(deploy_resolution["deploy_head"])
    deadline = time.monotonic() + max(0, wait_sec)
    # 複数 commit を 1 push した場合、workflow の head_sha は docs を変更した途中 commit
    # ではなく push の最終 HEAD になる。まず source HEAD を実測し、workflow が存在しない
    # tools-only push の場合だけ直近 deploy-relevant ancestor へフォールバックする。
    source_workflow = verify_deploy_workflow(
        repo_root=repo_root,
        remote=remote,
        branch=branch,
        expected_commit=source_head,
    )
    deploy_head = source_head
    if source_workflow.get("ok"):
        deploy_workflow = source_workflow
    elif _is_retryable_deploy_workflow(source_workflow) and str(source_workflow.get("status") or ""):
        if time.monotonic() < deadline:
            time.sleep(max(1, poll_sec))
        deploy_workflow = wait_for_deploy_workflow(
            repo_root=repo_root,
            remote=remote,
            branch=branch,
            expected_commit=source_head,
            deadline=deadline,
            poll_sec=poll_sec,
        )
    elif not _is_retryable_deploy_workflow(source_workflow):
        deploy_workflow = source_workflow
    elif deploy_relevant_head != source_head:
        deploy_workflow = wait_for_deploy_workflow_covering_deploy_head(
            repo_root=repo_root,
            remote=remote,
            branch=branch,
            source_head=source_head,
            deploy_relevant_head=deploy_relevant_head,
            deadline=deadline,
            poll_sec=poll_sec,
        )
        if deploy_workflow.get("ok"):
            deploy_head = str(deploy_workflow["head_sha"])
    else:
        deploy_workflow = wait_for_deploy_workflow(
            repo_root=repo_root,
            remote=remote,
            branch=branch,
            expected_commit=source_head,
            deadline=deadline,
            poll_sec=poll_sec,
        )
    head_state = {
        "artifact_head": artifact_head,
        "local_head": source_head,
        "remote_head": remote_head,
        "deploy_head": deploy_head,
        "deploy_relevant_head": deploy_relevant_head,
        "deploy_head_resolution": deploy_resolution,
        "issue_public_tree_unchanged": issue_tree_resolution.get("ok") is True,
        "issue_public_tree_resolution": issue_tree_resolution,
    }
    if not deploy_workflow["ok"]:
        return {
            "ok": False,
            "reason": deploy_workflow["reason"],
            **head_state,
            "deploy_workflow": deploy_workflow,
        }
    pages = verify_pages_build(repo_root=repo_root, remote=remote, expected_commit=deploy_head, branch=branch)
    if not pages["ok"]:
        return {
            "ok": False,
            "reason": pages["reason"],
            **head_state,
            "deploy_workflow": deploy_workflow,
            "pages": pages,
        }

    status_url = urljoin(public_base_url.rstrip("/") + "/", "publish-status.json")
    last_error = ""
    while True:
        try:
            with urllib.request.urlopen(status_url, timeout=20) as res:  # noqa: S310 - fixed public URL from runner config
                status = json.loads(res.read().decode("utf-8-sig"))
            status_date = str(status.get("date") or "")
            status_mode = ""
            historical_archive: dict = {}
            if status.get("result") == "published_ok" and status_date == date:
                status_mode = "current_issue"
            elif (
                status.get("result") == "published_ok"
                and re.fullmatch(r"\d{4}-\d{2}-\d{2}", status_date)
                and status_date > date
            ):
                historical_archive = verify_historical_public_archive(
                    repo_root=repo_root,
                    date=date,
                    public_base_url=public_base_url,
                )
                if historical_archive.get("ok"):
                    status_mode = "historical_archive"
                else:
                    last_error = (
                        f"{historical_archive.get('reason')}: "
                        f"{historical_archive!r}"
                    )
            if status_mode:
                pwa = verify_public_sw_version(
                    repo_root=repo_root,
                    public_base_url=public_base_url,
                    source_head=(source_head if source_head != artifact_head else None),
                )
                if not pwa["ok"]:
                    return {
                        "ok": False,
                        "reason": pwa["reason"],
                        **head_state,
                        "url": status_url,
                        "deploy_workflow": deploy_workflow,
                        "pages": pages,
                        "pwa": pwa,
                    }
                audio = (
                    dict(historical_archive["audio"])
                    if status_mode == "historical_archive"
                    else verify_public_audio(
                        repo_root=repo_root,
                        date=date,
                        public_base_url=public_base_url,
                    )
                )
                if audio["ok"]:
                    podcast = {"checked": False, "ok": True, "reason": "podcast_not_required"}
                    if require_podcast:
                        podcast = verify_podcast(
                            date=date,
                            state_path=podcast_state_path or repo_root / "build" / "youtube-podcast" / "uploads.json",
                            wait_sec=wait_sec,
                            poll_sec=poll_sec,
                        )
                        if not podcast["ok"]:
                            return {
                                "ok": False,
                                "reason": podcast["reason"],
                                **head_state,
                                "url": status_url,
                                "deploy_workflow": deploy_workflow,
                                "pages": pages,
                                "pwa": pwa,
                                "audio": audio,
                                "podcast": podcast,
                            }
                    confirmed_remote_head = _git_output(
                        repo_root,
                        ["ls-remote", remote, f"refs/heads/{branch}"],
                    ).split()[0]
                    if confirmed_remote_head != remote_head:
                        return {
                            "ok": False,
                            "reason": "remote_head_changed_during_verify",
                            **head_state,
                            "confirmed_remote_head": confirmed_remote_head,
                            "url": status_url,
                            "deploy_workflow": deploy_workflow,
                            "pages": pages,
                            "pwa": pwa,
                            "audio": audio,
                            "podcast": podcast,
                        }
                    return {
                        "ok": True,
                        "reason": "",
                        **head_state,
                        "url": status_url,
                        "status_mode": status_mode,
                        "public_status_date": status_date,
                        "historical_archive": historical_archive,
                        "deploy_workflow": deploy_workflow,
                        "pages": pages,
                        "pwa": pwa,
                        "audio": audio,
                        "podcast": podcast,
                    }
                return {
                    "ok": False,
                    "reason": audio["reason"],
                    **head_state,
                    "url": status_url,
                    "status_mode": status_mode,
                    "public_status_date": status_date,
                    "historical_archive": historical_archive,
                    "deploy_workflow": deploy_workflow,
                    "pages": pages,
                    "pwa": pwa,
                    "audio": audio,
                }
            if not last_error:
                last_error = f"publish-status mismatch: {status!r}"
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            last_error = str(exc)
        if time.monotonic() >= deadline:
            return {
                "ok": False,
                "reason": "public_sentinel_missing",
                "detail": last_error,
                **head_state,
                "url": status_url,
                "deploy_workflow": deploy_workflow,
                "pages": pages,
            }
        time.sleep(max(1, poll_sec))


def _distribution_artifact_manifest(repo_root: Path, date: str) -> dict:
    required = required_distribution_artifacts(date)
    missing = [rel for rel in required if not (repo_root / rel).exists()]
    manifest_rel = f"data/distribution/{date}.json"
    manifest_path = repo_root / manifest_rel
    manifest: dict = {}
    manifest_errors: list[str] = []
    manifest_reason = ""
    if manifest_path.exists():
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            loaded = {}
            manifest_errors.append(f"invalid_json:{exc}")
        if not isinstance(loaded, dict):
            manifest_errors.append("manifest_not_object")
            loaded = {}
        manifest = loaded
        required_text_fields = (
            "date",
            "primary_podcast_state",
            "deepdive_podcast_state",
            "latest_audio_state",
            "deepdive_audio_state",
            "generated_at",
        )
        for field in required_text_fields:
            value = str(manifest.get(field) or "").strip()
            if not value:
                manifest_errors.append(f"missing_field:{field}")
            elif field.endswith("_state") and Path(value).is_absolute():
                manifest_errors.append(f"absolute_path:{field}")
        if manifest_errors:
            manifest_reason = "distribution_manifest_invalid"
        elif str(manifest.get("date")) != date:
            manifest_reason = "distribution_manifest_mismatch"
        else:
            pre_publish_commit = str(manifest.get("pre_publish_commit") or "").strip()
            if not re.fullmatch(r"[0-9a-fA-F]{7,40}", pre_publish_commit):
                manifest_reason = "distribution_manifest_commit_missing"
            else:
                publish_commit = str(manifest.get("publish_commit") or "").strip()
                resolution = str(manifest.get("publish_commit_resolution") or "").strip()
                same_publish_contract = str(manifest.get("same_publish_contract") or "").strip()
                if not publish_commit and (
                    resolution != "post_push_verify"
                    or same_publish_contract != "pre_publish_commit_must_equal_verified_publish_commit"
                ):
                    manifest_reason = "distribution_manifest_publish_commit_resolution_missing"
    return {
        "required": required,
        "missing": missing,
        "manifest_path": manifest_rel,
        "manifest": manifest,
        "manifest_errors": manifest_errors,
        "manifest_reason": manifest_reason,
    }


def _canonical_quality_text_sha256(payload: bytes) -> str:
    text = payload.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _verify_deepdive_quality_head_binding(
    *,
    repo_root: Path,
    audit: dict,
) -> dict:
    """品質engineが読んだ全fileを現在のHEAD bytesへ束縛する。"""

    repo = Path(repo_root).resolve()
    raw_files = list(audit.get("auditedFiles") or [])
    if not raw_files:
        return {"ok": False, "reason": "deepdive_quality_audited_paths_missing", "paths": []}
    evidence_by_path: dict[str, dict] = {}
    try:
        for row in raw_files:
            if not isinstance(row, dict):
                raise ValueError("audit row is not an object")
            resolved = Path(str(row.get("path") or "")).resolve(strict=True)
            relative = resolved.relative_to(repo).as_posix()
            if relative in evidence_by_path:
                raise ValueError("duplicate audit path")
            evidence_by_path[relative] = row
    except (OSError, ValueError, TypeError):
        return {"ok": False, "reason": "deepdive_quality_path_invalid", "paths": []}
    relative_paths = sorted(evidence_by_path)
    if not _is_git_worktree(repo):
        return {
            "ok": False,
            "reason": "deepdive_quality_head_unavailable",
            "paths": relative_paths,
        }
    try:
        head = _git_output(repo, ["rev-parse", "HEAD"])
    except RuntimeError:
        return {
            "ok": False,
            "reason": "deepdive_quality_head_unavailable",
            "paths": relative_paths,
        }
    archive = subprocess.run(
        ["git", "-C", str(repo), "archive", "--format=tar", head, "--", *relative_paths],
        capture_output=True,
        check=False,
    )
    if archive.returncode != 0:
        return {
            "ok": False,
            "reason": "deepdive_quality_source_untracked",
            "head": head,
            "paths": relative_paths,
        }
    archived_hashes: dict[str, str] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:") as stream:
            for member in stream.getmembers():
                if not member.isfile() or member.name not in evidence_by_path:
                    continue
                extracted = stream.extractfile(member)
                if extracted is None:
                    raise tarfile.TarError("archive member body missing")
                archived_hashes[member.name] = _canonical_quality_text_sha256(
                    extracted.read()
                )
    except (tarfile.TarError, UnicodeError, OSError):
        return {
            "ok": False,
            "reason": "deepdive_quality_head_check_failed",
            "head": head,
            "paths": relative_paths,
        }
    if set(archived_hashes) != set(relative_paths):
        return {
            "ok": False,
            "reason": "deepdive_quality_source_untracked",
            "head": head,
            "paths": relative_paths,
        }
    mismatched = [
        path
        for path in relative_paths
        if str(evidence_by_path[path].get("canonicalTextSha256") or "")
        != archived_hashes[path]
    ]
    if mismatched:
        return {
            "ok": False,
            "reason": "deepdive_quality_head_blob_mismatch",
            "head": head,
            "paths": relative_paths,
            "mismatched_paths": mismatched,
        }
    return {"ok": True, "reason": "", "head": head, "paths": relative_paths}


def _producer_lineage_expected(
    *, repo_root: Path, ops_root: Path, date: str, run_intent: str, run_id: str
) -> dict[str, str]:
    artifact_root = str(repo_root.resolve())
    canonical_ops_root = str(ops_root.resolve())
    daily_root_id = hashlib.sha256(
        (
            f"News-Grasp|{date}|{artifact_root.casefold()}|"
            f"{canonical_ops_root.casefold()}"
        ).encode("utf-8")
    ).hexdigest()
    root_operation_id = hashlib.sha256(
        f"{daily_root_id}|{run_id}|root-operation".encode("utf-8")
    ).hexdigest()
    producer_operation_id = hashlib.sha256(
        f"{root_operation_id}|producer|{run_intent}".encode("utf-8")
    ).hexdigest()
    receipt_sha256 = hashlib.sha256(
        (
            "NEWS_GRASP_PRODUCER_LINEAGE_V1|"
            f"{date}|{artifact_root.casefold()}|{canonical_ops_root.casefold()}|"
            f"{daily_root_id}|{root_operation_id}|{producer_operation_id}|"
            f"{run_intent}|{run_id}"
        ).encode("utf-8")
    ).hexdigest()
    return {
        "artifactRoot": artifact_root,
        "opsRoot": canonical_ops_root,
        "dailyRootId": daily_root_id,
        "rootOperationId": root_operation_id,
        "producerOperationId": producer_operation_id,
        "producerRunIntent": run_intent,
        "lineageReceiptSha256": receipt_sha256,
    }


def _load_producer_lineage(
    *, repo_root: Path, ops_root: Path, state_path: Path, date: str
) -> dict[str, str] | None:
    if not state_path.is_file() or state_path.is_symlink():
        return None
    try:
        state = json.loads(state_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(state, dict) or state.get("date") != date:
        return None
    run_intent = str(state.get("run_intent") or "")
    run_id = str(state.get("run_id") or "")
    if not run_intent or not run_id:
        return None
    expected = _producer_lineage_expected(
        repo_root=repo_root,
        ops_root=ops_root,
        date=date,
        run_intent=run_intent,
        run_id=run_id,
    )
    if any(state.get(field) != value for field, value in expected.items()):
        return None
    return expected


def _typed_completion_manifest(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        return _typed_manifest_result(function(*args, **kwargs))

    return wrapped


@_typed_completion_manifest
def verify_publish_complete(
    *,
    repo_root: Path,
    ops_repo_root: Path | None = None,
    date: str,
    remote: str,
    branch: str,
    public_base_url: str,
    wait_sec: int,
    poll_sec: int,
    primary_podcast_state_path: Path | None = None,
    deepdive_podcast_state_path: Path | None = None,
    notification_state_path: Path | None = None,
    producer_state_path: Path | None = None,
    include_readiness: bool = True,
) -> dict:
    """公開完了を remote/public/audio/podcast/local inventory の同一 manifest として検証する。"""
    readiness_date = _current_jst_date()
    distribution = _distribution_artifact_manifest(repo_root, date)
    base = {
        "schemaVersion": "NEWS_GRASP_PUBLISH_COMPLETE_V2",
        "ok": False,
        "reason": "",
        "public_status": "red",
        "scheduled_attempt_status": "unknown",
        "recovery_attempt_status": "not_verified",
        "date": date,
        "readiness_date": readiness_date,
        "distribution_artifacts": distribution,
    }
    if distribution["missing"]:
        return {**base, "reason": "distribution_artifact_missing"}
    if distribution.get("manifest_reason"):
        return {**base, "reason": distribution["manifest_reason"]}
    state_path = producer_state_path or (
        Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        / "News-Grasp"
        / "ops"
        / "news-grasp-runner-state.json"
    )
    producer_lineage = _load_producer_lineage(
        repo_root=repo_root,
        ops_root=(ops_repo_root or repo_root).resolve(),
        state_path=state_path,
        date=date,
    )
    if producer_lineage is None:
        return {**base, "reason": "producer_lineage_invalid"}

    shared_quality = deepdive_quality.audit_issue(
        repo_root=repo_root,
        issue_date=date,
        require_rendered_public=True,
    )
    base["deepdive_shared_quality"] = shared_quality
    if shared_quality.get("status") != "Green":
        issue_codes = [
            str(code)
            for code in shared_quality.get("issueCodes", [])
            if str(code)
        ]
        return {
            **base,
            "reason": (
                issue_codes[0]
                if issue_codes
                else "deepdive_shared_quality_invalid"
            ),
        }
    quality_head_binding = _verify_deepdive_quality_head_binding(
        repo_root=repo_root,
        audit=shared_quality,
    )
    base["deepdive_quality_head_binding"] = quality_head_binding
    if not quality_head_binding.get("ok"):
        return {
            **base,
            "reason": str(
                quality_head_binding.get("reason")
                or "deepdive_quality_source_dirty"
            ),
        }

    primary_state = primary_podcast_state_path or repo_root / "build" / "youtube-podcast" / "uploads.json"
    deepdive_state = deepdive_podcast_state_path or repo_root / "build" / "youtube-podcast-deepdive" / "uploads.json"
    publish = verify_publish(
        repo_root=repo_root,
        date=date,
        remote=remote,
        branch=branch,
        public_base_url=public_base_url,
        wait_sec=wait_sec,
        poll_sec=poll_sec,
        require_podcast=True,
        podcast_state_path=primary_state,
    )
    manifest = {
        **base,
        **producer_lineage,
        "publish": publish,
        "local_head": publish.get("local_head", ""),
        "remote_head": publish.get("remote_head", ""),
        "publish_status_url": publish.get("url", ""),
        "pwa": publish.get("pwa", {}),
        "audio": publish.get("audio", {}),
        "podcasts": {
            "primary": {"date": date, **dict(publish.get("podcast") or {})},
            "deepdive": {},
        },
        "distribution_manifest": distribution.get("manifest", {}),
    }
    if not publish.get("ok"):
        return {**manifest, "reason": str(publish.get("reason") or "publish_sentinel_failed")}

    local_head = str(publish.get("local_head") or "")
    remote_head = str(publish.get("remote_head") or "")
    artifact_head = str(publish.get("artifact_head") or local_head)
    deploy_head = str(publish.get("deploy_head") or local_head)
    if not local_head or local_head != remote_head:
        return {**manifest, "reason": "publish_commit_mismatch"}
    manifest["source_commit"] = local_head
    manifest["artifact_commit"] = artifact_head
    manifest["deploy_head"] = deploy_head
    if str(quality_head_binding.get("head") or "") != artifact_head:
        return {**manifest, "reason": "deepdive_quality_head_mismatch"}
    manifest_rel = str(distribution.get("manifest_path") or f"data/distribution/{date}.json")
    manifest_in_head = _git_tree_has_path(repo_root, local_head, manifest_rel)
    if manifest_in_head is False:
        return {**manifest, "reason": "distribution_manifest_remote_missing"}
    manifest["distribution_manifest_in_head"] = manifest_in_head
    distribution_manifest = dict(distribution.get("manifest") or {})
    pre_publish_commit = str(distribution_manifest.get("pre_publish_commit") or "").strip()
    publish_commit = str(distribution_manifest.get("publish_commit") or "").strip()
    if not _commit_is_ancestor(repo_root, pre_publish_commit, local_head):
        return {**manifest, "reason": "distribution_manifest_commit_mismatch"}
    if publish_commit and not _commit_is_ancestor(repo_root, publish_commit, local_head):
        return {**manifest, "reason": "distribution_manifest_commit_mismatch"}

    deepdive = verify_podcast(
        date=date,
        state_path=deepdive_state,
        wait_sec=wait_sec,
        poll_sec=poll_sec,
        expected_title=f"News-Grasp DeepDive Dialogue {date}",
    )
    manifest["podcasts"]["deepdive"] = {"date": date, **deepdive}
    if not deepdive.get("ok"):
        return {**manifest, "reason": "deepdive_podcast_missing"}

    if notification_state_path is not None:
        notification = _load_notification_state(
            notification_state_path, date, repo_root=repo_root
        )
        manifest["notification"] = notification.get("state", {})
        if notification.get("reason"):
            return {**manifest, "reason": notification["reason"], "notification": notification}

    if not include_readiness:
        return {
            **manifest,
            "ok": True,
            "reason": "",
            "public_status": "green",
            "publicCompletionStatus": "green",
            "nextRunReadinessStatus": "unverified",
            "publicAuthority": {
                "completionAuthorityId": str(
                    manifest.get("completion_authority_id")
                    or manifest.get("completionAuthorityId")
                    or ""
                )
            },
        }

    live_readiness = verify_live_runner_readiness(
        repo_root=repo_root,
        ops_repo_root=ops_repo_root,
        date=readiness_date,
    )
    manifest["live_runner_readiness"] = live_readiness
    if not live_readiness.get("ok"):
        return {**manifest, "reason": str(live_readiness.get("reason") or "live_runner_readiness_failed")}

    last_scheduled = dict(live_readiness.get("last_scheduled_attempt") or {})
    last_scheduled_status = str(last_scheduled.get("status") or "unknown")
    if last_scheduled_status == "succeeded":
        scheduled_attempt_status = "succeeded"
        recovery_attempt_status = "not_required"
    elif last_scheduled_status == "failed":
        scheduled_attempt_status = "failed_then_recovered"
        recovery_attempt_status = "succeeded"
    else:
        scheduled_attempt_status = "unknown_then_recovered"
        recovery_attempt_status = "succeeded"

    return {
        **manifest,
        "ok": True,
        "reason": "",
        "verified_at": datetime.now(timezone(timedelta(hours=9))).isoformat(),
        "public_status": "green",
        "scheduled_attempt_status": scheduled_attempt_status,
        "recovery_attempt_status": recovery_attempt_status,
        "source_commit": local_head,
        "publish_commit": deploy_head,
        "same_publish": {
            "date": date,
            "local_head": local_head,
            "remote_head": remote_head,
            "source_head": local_head,
            "deploy_head": deploy_head,
            "publish_commit": deploy_head,
            "distribution_date": str(distribution_manifest.get("date") or ""),
            "distribution_pre_publish_commit": pre_publish_commit,
            "distribution_publish_commit_resolution": str(
                distribution_manifest.get("publish_commit_resolution") or ""
            ),
            "distribution_same_publish_contract": str(
                distribution_manifest.get("same_publish_contract") or ""
            ),
        },
    }


def _typed_manifest_result(result: dict) -> dict:
    """legacy manifestへCompletionVerificationResultV1の主要typed stateを付与する。"""
    if str(result.get("status") or "") in {
        "verified_green",
        "verified_incomplete",
        "verification_unavailable",
    }:
        return result
    nested_public = result.get("publish")
    public = nested_public if isinstance(nested_public, dict) else result
    readiness = result.get("live_runner_readiness")
    readiness_value = readiness if isinstance(readiness, dict) else {}
    public_green = public.get("ok") is True or (
        public.get("public_status") == "green"
        and bool(
            public.get("completion_authority_id")
            or public.get("completionAuthorityId")
            or result.get("completion_authority_id")
            or result.get("completionAuthorityId")
        )
    )
    readiness_ok = readiness_value.get("ok") is True or (
        not readiness_value and result.get("nextRunReadinessStatus") == "green"
    )
    unavailable = bool(
        result.get("verification_unavailable")
        or public.get("verification_unavailable")
        or readiness_value.get("verification_unavailable")
    )
    public_status = "green" if public_green else "incomplete"
    readiness_status = (
        str(result.get("nextRunReadinessStatus") or "unverified")
        if not readiness_value
        else "green"
        if readiness_ok
        else "unverified"
        if unavailable
        else "red"
    )
    status = (
        "verification_unavailable"
        if unavailable
        else "verified_green"
        if public_green and readiness_ok
        else "verified_incomplete"
    )
    failed = []
    for source in (result, public, readiness_value):
        values = source.get("failedGateIds")
        if isinstance(values, (list, tuple)):
            failed.extend(str(value) for value in values if str(value))
    reason = str(
        result.get("reason")
        or public.get("reason")
        or readiness_value.get("reason")
        or ""
    )
    if not failed and status != "verified_green" and reason:
        failed.append(reason)
    return {
        **result,
        "status": status,
        "verificationStatus": status,
        "publicCompletionStatus": public_status,
        "nextRunReadinessStatus": readiness_status,
        "phase": (
            "verification_unavailable"
            if unavailable
            else "public_and_readiness"
            if readiness_value
            else "public"
        ),
        "reasonCode": reason,
        "failedGateIds": list(dict.fromkeys(failed)),
        "public_status": public_status,
        "readiness_status": readiness_status,
    }


def verify_public_completion(
    *,
    repo_root: Path,
    ops_repo_root: Path | None = None,
    date: str,
    remote: str,
    branch: str,
    public_base_url: str,
    wait_sec: int,
    poll_sec: int,
    primary_podcast_state_path: Path | None = None,
    deepdive_podcast_state_path: Path | None = None,
    notification_state_path: Path | None = None,
    producer_state_path: Path | None = None,
) -> dict:
    """次回 readiness を実行せず、同日公開面だけを一回検証する。"""
    return verify_publish_complete(
        repo_root=repo_root,
        ops_repo_root=ops_repo_root,
        date=date,
        remote=remote,
        branch=branch,
        public_base_url=public_base_url,
        wait_sec=wait_sec,
        poll_sec=poll_sec,
        primary_podcast_state_path=primary_podcast_state_path,
        deepdive_podcast_state_path=deepdive_podcast_state_path,
        notification_state_path=notification_state_path,
        producer_state_path=producer_state_path,
        include_readiness=False,
    )


def evaluate_completion(
    *,
    repo_root: Path,
    ops_repo_root: Path | None = None,
    date: str,
    remote: str,
    branch: str,
    public_base_url: str,
    wait_sec: int,
    poll_sec: int,
    primary_podcast_state_path: Path | None = None,
    deepdive_podcast_state_path: Path | None = None,
    notification_state_path: Path | None = None,
    producer_state_path: Path | None = None,
    previous_public_authority: dict[str, object] | None = None,
) -> dict[str, object]:
    """公開 authority と次回 readiness を型付き結果へ分離して返す。"""
    validated_previous_authority: dict[str, object] | None = None
    if previous_public_authority is not None:
        try:
            from tools import audit_recovery_control

            canonical_authority = (
                audit_recovery_control.load_completion_authority_receipt(date)
            )
            if (
                canonical_authority is not None
                and canonical_authority.get("receiptSha256")
                == previous_public_authority.get("receiptSha256")
            ):
                validated_previous_authority = canonical_authority
        except (OSError, ValueError):
            validated_previous_authority = None
    public: dict[str, object]
    try:
        public = dict(
            verify_public_completion(
                repo_root=repo_root,
                ops_repo_root=ops_repo_root,
                date=date,
                remote=remote,
                branch=branch,
                public_base_url=public_base_url,
                wait_sec=wait_sec,
                poll_sec=poll_sec,
                primary_podcast_state_path=primary_podcast_state_path,
                deepdive_podcast_state_path=deepdive_podcast_state_path,
                notification_state_path=notification_state_path,
                producer_state_path=producer_state_path,
            )
        )
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        public = {
            "ok": bool(validated_previous_authority),
            "public_status": (
                "green" if validated_previous_authority else "unverified"
            ),
            "publicCompletionStatus": (
                "green" if validated_previous_authority else "unverified"
            ),
            "verification_unavailable": True,
            "reason": "PRIMARY_PUBLIC_ORACLE_EXCEPTION",
            "exceptionType": type(error).__name__,
        }
    try:
        readiness = dict(
            verify_live_runner_readiness(
                repo_root=repo_root,
                ops_repo_root=ops_repo_root,
                date=_current_jst_date(),
            )
        )
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        readiness = {
            "ok": False,
            "verification_unavailable": True,
            "reason": "READINESS_VERIFIER_EXCEPTION",
            "exceptionType": type(error).__name__,
        }
    authority = validated_previous_authority
    if authority is None and isinstance(public.get("publicAuthority"), dict):
        authority = dict(public["publicAuthority"])
    return _completion_result(
        repo_root=repo_root,
        ops_repo_root=ops_repo_root,
        date=date,
        remote=remote,
        branch=branch,
        public_base_url=public_base_url,
        wait_sec=wait_sec,
        poll_sec=poll_sec,
        public=public,
        readiness=readiness,
        phase=(
            "public_and_readiness"
            if public.get("ok") is True and readiness.get("ok") is True
            else "readiness"
            if public.get("ok") is True
            else "public"
        ),
        reason_code=str(
            readiness.get("reason")
            if public.get("ok") is True and readiness.get("ok") is not True
            else public.get("reason") or "VERIFIED_GREEN"
        ),
        public_authority=authority,
    )


def complete_readiness_repair(
    completion: dict[str, object], readiness: dict[str, object]
) -> dict[str, object]:
    """readiness だけを修復し、公開 authority を再生成・巻き戻ししない。"""
    result = dict(completion)
    if (
        result.get("publicCompletionStatus") != "green"
        or readiness.get("ok") is not True
    ):
        return result
    result.update(
        {
            "schemaVersion": "COMPLETION_VERIFICATION_RESULT_V1",
            "verificationStatus": "verified_green",
            "nextRunReadinessStatus": "green",
            "phase": "readiness_repair",
            "reasonCode": "READINESS_REPAIR_GREEN",
            "failedGateIds": [],
            "publicRecoveryStarted": False,
        }
    )
    result["evidenceSha256"] = hashlib.sha256(
        json.dumps(
            {"completion": result, "readiness": readiness},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    result["ok"] = True
    return result


_KNOWN_NOTIFICATION_STATUSES = {
    "sent",
    "already_sent",
    "send_failed",
    "no_subscribers",
    "dry_run",
    "skipped_fallback",
    "skipped_not_normal",
    "config_error",
    "external_error",
    "partial_failure",
    "delivery_ledger_invalid",
    "delivery_ledger_conflict",
}


def _load_notification_state(
    path: Path, date: str, *, repo_root: Path | None = None
) -> dict:
    if not path.exists():
        return {"path": str(path), "state": {}, "reason": "notification_state_missing"}
    try:
        expected_state = (
            Path(repo_root) / "build" / "notification" / f"{date}.json"
            if repo_root is not None
            else Path(path)
        )
        state_raw = _canonical_file_bytes(
            Path(path), expected=expected_state, max_bytes=1024 * 1024
        )
        payload = json.loads(state_raw.decode("utf-8-sig"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError, ValueError) as exc:
        return {"path": str(path), "state": {}, "reason": "notification_state_invalid", "detail": str(exc)}
    if not isinstance(payload, dict):
        return {"path": str(path), "state": {}, "reason": "notification_state_invalid", "detail": "not_object"}
    status = str(payload.get("status") or "")
    if status not in _KNOWN_NOTIFICATION_STATUSES:
        return {
            "path": str(path),
            "state": payload,
            "reason": "notification_state_invalid",
            "detail": f"unknown_status:{status}",
        }
    payload_date = str(payload.get("date") or "")
    if payload_date and payload_date != date:
        return {
            "path": str(path),
            "state": payload,
            "reason": "notification_state_mismatch",
            "detail": f"date:{payload_date}",
        }
    if payload.get("ok") is not True:
        return {
            "path": str(path),
            "state": payload,
            "reason": "notification_delivery_unverified",
            "detail": f"status:{status}",
        }
    if status not in {"sent", "already_sent", "no_subscribers"}:
        return {
            "path": str(path),
            "state": payload,
            "reason": "notification_delivery_unverified",
            "detail": f"status:{status}",
        }
    source = str(payload.get("source") or "")
    subscription_count = payload.get("subscription_count")
    sent_count = payload.get("sent_count")
    payload_sha = str(payload.get("payload_sha256") or "")
    audience_sha = str(payload.get("audience_set_sha256") or "")
    producer_sha = str(payload.get("producer_sha256") or "")
    producer_run_id = str(payload.get("producer_run_id") or "")
    try:
        recorded_at = datetime.fromisoformat(
            str(payload.get("recorded_at") or "").replace("Z", "+00:00")
        )
    except ValueError:
        recorded_at = datetime.min
    if (
        source not in {"worker", "file"}
        or not isinstance(subscription_count, int)
        or not isinstance(sent_count, int)
        or subscription_count < 0
        or sent_count < 0
        or re.fullmatch(r"[0-9a-f]{64}", payload_sha) is None
        or re.fullmatch(r"[0-9a-f]{64}", audience_sha) is None
        or re.fullmatch(r"[0-9a-f]{64}", producer_sha) is None
        or payload.get("producer") != "tools.send_push"
        or re.fullmatch(r"[0-9a-f]{32}", producer_run_id) is None
        or recorded_at.tzinfo is None
    ):
        return {
            "path": str(path),
            "state": payload,
            "reason": "notification_semantics_invalid",
        }
    try:
        recorded_date = recorded_at.astimezone(_jst_timezone()).date().isoformat()
    except (ValueError, OSError):
        recorded_date = ""
    producer_path = Path(__file__).with_name("send_push.py")
    if (
        recorded_date != date
        or hashlib.sha256(producer_path.read_bytes()).hexdigest() != producer_sha
    ):
        return {
            "path": str(path),
            "state": payload,
            "reason": "notification_lineage_invalid",
        }
    if status in {"sent", "already_sent"}:
        receipt = payload.get("deliveryReceipt")
        receipt_sha = str(payload.get("deliveryReceiptSha256") or "")
        expected_schema = "NEWS_GRASP_NOTIFICATION_DELIVERY_RECEIPT_V1"
    elif status == "no_subscribers":
        receipt = payload.get("audienceResolutionReceipt")
        receipt_sha = str(payload.get("audienceResolutionReceiptSha256") or "")
        expected_schema = "NEWS_GRASP_NOTIFICATION_AUDIENCE_RESOLUTION_V1"
    else:
        raise AssertionError("unreachable notification status")
    if not isinstance(receipt, dict):
        return {
            "path": str(path),
            "state": payload,
            "reason": "notification_receipt_missing",
        }
    body = {key: item for key, item in receipt.items() if key != "receiptSha256"}
    expected_sha = hashlib.sha256(
        json.dumps(
            body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    if (
        receipt.get("schemaVersion") != expected_schema
        or receipt.get("date") != date
        or receipt.get("receiptSha256") != expected_sha
        or receipt_sha != expected_sha
    ):
        return {
            "path": str(path),
            "state": payload,
            "reason": "notification_receipt_invalid",
        }
    common_invalid = (
        receipt.get("source") != source
        or receipt.get("subscriptionCount") != subscription_count
        or receipt.get("audienceSetSha256") != audience_sha
        or receipt.get("producer") != "tools.send_push"
        or receipt.get("producerSha256") != producer_sha
        or receipt.get("producerRunId") != producer_run_id
    )
    prior_receipt: dict | None = None
    prior_raw = b""
    if status == "sent":
        semantic_invalid = (
            subscription_count <= 0
            or sent_count != subscription_count
            or receipt.get("sentCount") != sent_count
            or receipt.get("payloadSha256") != payload_sha
        )
    elif status == "already_sent":
        expected_prior_path = Path(
            os.path.abspath(path.with_name(f"{date}.delivery.json"))
        )
        observed_prior_path = Path(
            str(receipt.get("priorDeliveryReceiptPath") or "")
        )
        try:
            prior_raw = _canonical_file_bytes(
                observed_prior_path,
                expected=expected_prior_path,
                max_bytes=65536,
            )
            prior_receipt = json.loads(prior_raw.decode("utf-8-sig"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError, OSError):
            prior_receipt = None
            prior_raw = b""
        if isinstance(prior_receipt, dict):
            prior_body = {
                key: item
                for key, item in prior_receipt.items()
                if key != "receiptSha256"
            }
            prior_self_sha = hashlib.sha256(
                json.dumps(
                    prior_body,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
        else:
            prior_self_sha = ""
        semantic_invalid = (
            subscription_count <= 0
            or sent_count != subscription_count
            or receipt.get("sentCount") != sent_count
            or receipt.get("payloadSha256") != payload_sha
            or re.fullmatch(
                r"[0-9a-f]{64}", str(receipt.get("priorDeliveryReceiptSha256") or "")
            )
            is None
            or not isinstance(prior_receipt, dict)
            or prior_receipt.get("schemaVersion")
            != "NEWS_GRASP_NOTIFICATION_DELIVERY_RECEIPT_V1"
            or prior_receipt.get("receiptSha256") != prior_self_sha
            or receipt.get("priorDeliveryReceiptSha256") != prior_self_sha
            or receipt.get("priorDeliveryReceiptFileSha256")
            != hashlib.sha256(prior_raw).hexdigest()
            or prior_receipt.get("date") != date
            or prior_receipt.get("source") != source
            or prior_receipt.get("subscriptionCount") != subscription_count
            or prior_receipt.get("sentCount") != sent_count
            or prior_receipt.get("payloadSha256") != payload_sha
            or prior_receipt.get("audienceSetSha256") != audience_sha
            or prior_receipt.get("producer") != "tools.send_push"
            or prior_receipt.get("producerSha256") != producer_sha
            or re.fullmatch(
                r"[0-9a-f]{32}", str(prior_receipt.get("producerRunId") or "")
            )
            is None
        )
    else:
        semantic_invalid = (
            subscription_count != 0
            or sent_count != 0
            or receipt.get("subscriptionCount") != 0
        )
    if common_invalid or semantic_invalid:
        return {
            "path": str(path),
            "state": payload,
            "reason": "notification_semantics_invalid",
        }
    ledger_suffix = "delivery" if status in {"sent", "already_sent"} else "audience"
    expected_ledger_path = Path(
        os.path.abspath(Path(path).with_name(f"{date}.{ledger_suffix}.json"))
    )
    observed_ledger_path = Path(str(payload.get("evidenceLedgerPath") or ""))
    try:
        ledger_raw = _canonical_file_bytes(
            observed_ledger_path,
            expected=expected_ledger_path,
            max_bytes=65536,
        )
        ledger_receipt = json.loads(ledger_raw.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError, OSError):
        ledger_raw = b""
        ledger_receipt = None
    if isinstance(ledger_receipt, dict):
        ledger_body = {
            key: item for key, item in ledger_receipt.items() if key != "receiptSha256"
        }
        ledger_self_sha = hashlib.sha256(
            json.dumps(
                ledger_body,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
    else:
        ledger_self_sha = ""
    expected_ledger_receipt = prior_receipt if status == "already_sent" else receipt
    if (
        not isinstance(ledger_receipt, dict)
        or ledger_receipt != expected_ledger_receipt
        or ledger_receipt.get("receiptSha256") != ledger_self_sha
        or payload.get("evidenceLedgerReceiptSha256") != ledger_self_sha
        or payload.get("evidenceLedgerFileSha256")
        != hashlib.sha256(ledger_raw).hexdigest()
        or ledger_receipt.get("date") != date
        or ledger_receipt.get("source") != source
        or ledger_receipt.get("subscriptionCount") != subscription_count
        or ledger_receipt.get("audienceSetSha256") != audience_sha
        or ledger_receipt.get("producer") != "tools.send_push"
        or ledger_receipt.get("producerSha256") != producer_sha
        or re.fullmatch(
            r"[0-9a-f]{32}", str(ledger_receipt.get("producerRunId") or "")
        )
        is None
        or (
            status in {"sent", "already_sent"}
            and (
                ledger_receipt.get("sentCount") != sent_count
                or ledger_receipt.get("payloadSha256") != payload_sha
            )
        )
    ):
        return {
            "path": str(path),
            "state": payload,
            "reason": "notification_evidence_ledger_invalid",
        }
    return {"path": str(path), "state": payload, "reason": ""}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="News-Grasp daily self-healing helpers")
    sub = parser.add_subparsers(dest="cmd", required=True)

    checksum = sub.add_parser("checksum")
    checksum.add_argument("--repo-path", type=Path, required=True)
    checksum.add_argument("--live-path", type=Path, required=True)

    phase0 = sub.add_parser("phase0")
    phase0.add_argument("--snapshot-json", type=Path, required=True)

    deadman = sub.add_parser("deadman")
    deadman.add_argument("--state-file", type=Path, required=True)
    deadman.add_argument("--date", required=True)
    deadman.add_argument("--max-ok-age-hours", type=int, default=27)
    deadman.add_argument("--alert-log", type=Path, required=True)
    deadman.add_argument("--marker", type=Path, required=True)
    deadman.add_argument("--webhook-env", default="NEWS_GRASP_ALERT_WEBHOOK_URL")

    verify = sub.add_parser("verify-publish")
    verify.add_argument("--repo-root", type=Path, required=True)
    verify.add_argument("--date", required=True)
    verify.add_argument("--remote", default="origin")
    verify.add_argument("--branch", default="main")
    verify.add_argument("--public-base-url", default="https://hidepon-umg.github.io/News-Grasp/")
    verify.add_argument("--wait-sec", type=int, default=600)
    verify.add_argument("--poll-sec", type=int, default=30)
    verify.add_argument("--require-podcast", action="store_true")
    verify.add_argument("--podcast-state", type=Path, default=None)

    dispatch = sub.add_parser("dispatch-deploy-workflow")
    dispatch.add_argument("--repo-root", type=Path, required=True)
    dispatch.add_argument("--remote", default="origin")
    dispatch.add_argument("--branch", default="main")

    wait_deploy = sub.add_parser("wait-deploy-workflow")
    wait_deploy.add_argument("--repo-root", type=Path, required=True)
    wait_deploy.add_argument("--remote", default="origin")
    wait_deploy.add_argument("--branch", default="main")
    wait_deploy.add_argument("--wait-sec", type=int, default=600)
    wait_deploy.add_argument("--poll-sec", type=int, default=30)

    podcast = sub.add_parser("verify-podcast")
    podcast.add_argument("--date", required=True)
    podcast.add_argument("--state", type=Path, default=Path("build") / "youtube-podcast" / "uploads.json")
    podcast.add_argument("--wait-sec", type=int, default=1200)
    podcast.add_argument("--poll-sec", type=int, default=30)
    podcast.add_argument("--expected-title", default=None)

    complete = sub.add_parser("verify-publish-complete")
    complete.add_argument("--repo-root", type=Path, required=True)
    complete.add_argument("--ops-repo-root", type=Path, default=None)
    complete.add_argument("--date", required=True)
    complete.add_argument("--remote", default="origin")
    complete.add_argument("--branch", default="main")
    complete.add_argument("--public-base-url", default="https://hidepon-umg.github.io/News-Grasp/")
    complete.add_argument("--wait-sec", type=int, default=600)
    complete.add_argument("--poll-sec", type=int, default=30)
    complete.add_argument("--primary-podcast-state", type=Path, default=None)
    complete.add_argument("--deepdive-podcast-state", type=Path, default=None)
    complete.add_argument("--notification-state", type=Path, default=None)
    complete.add_argument("--producer-state", type=Path, default=None)
    complete.add_argument("--output", type=Path, default=None)

    live_ready = sub.add_parser("verify-live-runner-readiness")
    live_ready.add_argument("--repo-root", type=Path, required=True)
    live_ready.add_argument("--ops-repo-root", type=Path, default=None)
    live_ready.add_argument("--date", required=True)
    live_ready.add_argument("--live-runner", type=Path, default=None)
    live_ready.add_argument("--live-watcher", type=Path, default=None)
    live_ready.add_argument("--live-bootstrap", type=Path, default=None)
    live_ready.add_argument("--task-name", default="News-Grasp Production")
    live_ready.add_argument("--bootstrap-task-name", default="News-Grasp Bootstrap")
    live_ready.add_argument("--skip-canary", action="store_true")
    live_ready.add_argument("--canary-timeout-sec", type=int, default=60)
    live_ready.add_argument("--powershell-exe", default="pwsh")
    live_ready.add_argument("--output", type=Path, default=None)

    args = parser.parse_args(argv)
    if args.cmd == "checksum":
        result = compare_files(args.repo_path, args.live_path)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["synced"] else 1
    if args.cmd == "phase0":
        snapshot = json.loads(args.snapshot_json.read_text(encoding="utf-8"))
        print(json.dumps(classify_phase0(snapshot), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "deadman":
        state = json.loads(args.state_file.read_text(encoding="utf-8")) if args.state_file.exists() else {}
        decision = evaluate_deadman(
            state=state,
            now=datetime.now(timezone.utc),
            expected_date=args.date,
            max_ok_age_hours=args.max_ok_age_hours,
        )
        if decision["alert"]:
            result = emit_alert(
                {"date": args.date, **decision},
                alert_log=args.alert_log,
                marker_path=args.marker,
                webhook_url=os.environ.get(args.webhook_env, ""),
            )
            print(json.dumps({**decision, **result}, ensure_ascii=False, indent=2))
            return 2
        print(json.dumps(decision, ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "verify-publish":
        result = verify_publish(
            repo_root=args.repo_root,
            date=args.date,
            remote=args.remote,
            branch=args.branch,
            public_base_url=args.public_base_url,
            wait_sec=args.wait_sec,
            poll_sec=args.poll_sec,
            require_podcast=args.require_podcast,
            podcast_state_path=args.podcast_state,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1
    if args.cmd == "dispatch-deploy-workflow":
        result = dispatch_deploy_workflow_if_failed(
            repo_root=args.repo_root,
            remote=args.remote,
            branch=args.branch,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1
    if args.cmd == "wait-deploy-workflow":
        expected_commit = _git_output(args.repo_root, ["rev-parse", "HEAD"])
        result = wait_for_deploy_workflow(
            repo_root=args.repo_root,
            remote=args.remote,
            branch=args.branch,
            expected_commit=expected_commit,
            deadline=time.monotonic() + max(0, args.wait_sec),
            poll_sec=args.poll_sec,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1
    if args.cmd == "verify-podcast":
        result = verify_podcast(
            date=args.date,
            state_path=args.state,
            wait_sec=args.wait_sec,
            poll_sec=args.poll_sec,
            expected_title=args.expected_title,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1
    if args.cmd == "verify-publish-complete":
        result = verify_publish_complete(
            repo_root=args.repo_root,
            ops_repo_root=args.ops_repo_root,
            date=args.date,
            remote=args.remote,
            branch=args.branch,
            public_base_url=args.public_base_url,
            wait_sec=args.wait_sec,
            poll_sec=args.poll_sec,
            primary_podcast_state_path=args.primary_podcast_state,
            deepdive_podcast_state_path=args.deepdive_podcast_state,
            notification_state_path=args.notification_state,
            producer_state_path=args.producer_state,
        )
        text = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text + "\n", encoding="utf-8")
        print(text)
        return completion_cli_exit_code(result)
    if args.cmd == "verify-live-runner-readiness":
        result = verify_live_runner_readiness(
            repo_root=args.repo_root,
            ops_repo_root=args.ops_repo_root,
            date=args.date,
            live_runner_path=args.live_runner,
            live_watcher_path=args.live_watcher,
            live_bootstrap_path=args.live_bootstrap,
            task_name=args.task_name,
            bootstrap_task_name=args.bootstrap_task_name,
            run_canary=not args.skip_canary,
            canary_timeout_sec=args.canary_timeout_sec,
            powershell_exe=args.powershell_exe,
        )
        text = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text + "\n", encoding="utf-8")
        print(text)
        return 0 if result["ok"] else 1
    raise AssertionError(args.cmd)


if __name__ == "__main__":
    sys.exit(main())
