from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib.util
import inspect
import json
import ntpath
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

_validate_e2e_policy_transition = None


RUNTIME_RECOVERY_SCHEMA = "NEWS_GRASP_PRODUCTION_RUNTIME_RECOVERY_V1"
RUNTIME_RECOVERY_AUTHORITY_SCHEMA = (
    "NEWS_GRASP_PRODUCTION_RUNTIME_RECOVERY_AUTHORITY_V1"
)
RUNTIME_LIFECYCLE_OWNER_SCHEMA = "NEWS_GRASP_RUNTIME_LIFECYCLE_OWNER_V1"
RUNTIME_RECOVERY_PHASES = (
    "prepared",
    "runtime_quarantined",
    "replacement_created",
    "dependencies_bound",
    "committed",
)
MAX_RUNTIME_RECOVERY_TRANSACTIONS = 64
MAX_RUNTIME_RECOVERY_SCAN_ENTRIES = 256
MAX_GIT_OUTPUT_BYTES = 1024 * 1024
MAX_UNTRACKED_PATHS = 1024
RUNTIME_TRANSACTION_ID = re.compile(r"^[0-9]{8}T[0-9]{12}Z-[0-9a-f]{16}$")
RUNTIME_LEDGER_MAX_ENTRIES = 64
RUNTIME_RECOVERY_METADATA_MAX_BYTES = 64 * 1024 * 1024
RUNTIME_RECOVERY_MIN_FREE_BYTES = 128 * 1024 * 1024
RUNTIME_ORCHESTRATION_MUTEX_PREFIX = "Global\\NewsGraspBootstrapOrchestration-"
RUNTIME_PRODUCTION_MUTEX_PREFIX = "Global\\NewsGraspProductionRuntime-"
RUNTIME_LEGACY_MUTEX_NAME = "Global\\NewsGraspProductionRuntimeConvergence"
INSTALLED_NOPUBLISH_AUTHORITY_SCHEMA = "NEWS_GRASP_INSTALLED_NOPUBLISH_LAUNCH_AUTHORITY_V1"
STABLE_TASK_AUTHORITY_SCHEMA = "STABLE_TASK_AUTHORITY_V1"
_CANONICAL_POWERSHELL = Path(
    r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
)
TASK_TOPOLOGY_AUTHORITY_SCHEMA = "NEWS_GRASP_TASK_TOPOLOGY_AUTHORITY_V1"
NEWS_GRASP_TASK_CONTEXT_REJECTED_EXIT = 67
GLOBAL_GENERATION_MANIFEST_SCHEMA = (
    "NEWS_GRASP_GLOBAL_DEPENDENCY_GENERATION_MANIFEST_V1"
)
GLOBAL_GENERATION_ARGUMENT = "-GlobalHarnessGenerationManifestPath"
GLOBAL_GENERATION_AUTHORITY_FIELDS = {
    "globalGenerationManifestPath",
    "globalGenerationManifestSha256",
    "globalGenerationId",
    "globalGenerationGoalId",
}
GLOBAL_GENERATION_MANIFEST_FIELDS = {
    "schemaVersion",
    "generationId",
    "ownerRepo",
    "ownerCommit",
    "sourceSnapshotPath",
    "sourceSnapshotSha256",
    "installedRuntimePath",
    "installedRuntimeSha256",
    "ownerAuthorityReceiptPath",
    "ownerAuthorityReceiptSha256",
    "validForGoalId",
}


def _task_topology_manifest_path(runtime_root: Path) -> Path:
    """typed current-authorityがwhole-file hash束縛したmanifestだけを返す。"""
    config_root = runtime_root.resolve(strict=True) / "config"
    registry_path = config_root / "news_grasp_task_topology_authority_v1.json"
    if (
        not registry_path.is_file()
        or registry_path.is_symlink()
        or registry_path.stat().st_size > 64 * 1024
    ):
        raise RuntimeError("NEWS_GRASP_TASK_TOPOLOGY_AUTHORITY_INVALID")
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("NEWS_GRASP_TASK_TOPOLOGY_AUTHORITY_INVALID") from exc
    if (
        not isinstance(registry, dict)
        or set(registry) != {
            "schemaVersion",
            "status",
            "currentAuthority",
            "supersededTopologySources",
        }
        or registry.get("schemaVersion") != TASK_TOPOLOGY_AUTHORITY_SCHEMA
        or registry.get("status") != "current"
        or not isinstance(registry.get("currentAuthority"), dict)
    ):
        raise RuntimeError("NEWS_GRASP_TASK_TOPOLOGY_AUTHORITY_INVALID")
    authority = registry["currentAuthority"]
    relative = authority.get("path")
    expected_sha256 = str(authority.get("sha256") or "").lower()
    if (
        relative != "config/news_grasp_cleanroom_task_manifest_v1.json"
        or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None
    ):
        raise RuntimeError("NEWS_GRASP_TASK_TOPOLOGY_AUTHORITY_INVALID")
    manifest_path = runtime_root.resolve(strict=True) / str(relative)
    if (
        not manifest_path.is_file()
        or manifest_path.is_symlink()
        or hashlib.sha256(manifest_path.read_bytes()).hexdigest() != expected_sha256
    ):
        raise RuntimeError("NEWS_GRASP_TASK_TOPOLOGY_AUTHORITY_INVALID")
    return manifest_path.resolve(strict=True)
E2E_ATTEMPT_POLICY_ARGUMENT = "-E2EAttemptPolicyPath"
E2E_LOGICAL_ATTEMPT_ARGUMENT = "-E2ELogicalAttempt"
E2E_FINAL_ADMISSION_ARGUMENT = "-E2EFinalAdmissionPath"
E2E_ATTEMPT_AUTHORITY_FIELDS = {
    "e2eAttemptPolicyPath",
    "e2eAttemptPolicySha256",
    "e2eLogicalAttempt",
    "e2eAdmissionPath",
    "e2eAdmissionSha256",
}


def _runtime_mutex_identity() -> str:
    """環境変数で偽装できない、現在tokenのSIDをmutex identityに使う。"""
    if sys.platform != "win32":
        return str(getattr(os, "getuid", lambda: 0)())
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.OpenProcessToken.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
    advapi32.OpenProcessToken.restype = ctypes.c_int
    advapi32.GetTokenInformation.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)]
    advapi32.GetTokenInformation.restype = ctypes.c_int
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
    advapi32.ConvertSidToStringSidW.restype = ctypes.c_int
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    token = ctypes.c_void_p()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise RuntimeError("PRODUCTION_RUNTIME_MUTEX_OWNER_INVALID")
    try:
        required = ctypes.c_uint32(0)
        advapi32.GetTokenInformation(token, 1, None, 0, ctypes.byref(required))
        if required.value <= 0 or required.value > 64 * 1024:
            raise RuntimeError("PRODUCTION_RUNTIME_MUTEX_OWNER_INVALID")
        buffer = ctypes.create_string_buffer(required.value)
        if not advapi32.GetTokenInformation(token, 1, buffer, required.value, ctypes.byref(required)):
            raise RuntimeError("PRODUCTION_RUNTIME_MUTEX_OWNER_INVALID")
        sid_ptr = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        sid_text = ctypes.c_wchar_p()
        if not advapi32.ConvertSidToStringSidW(sid_ptr, ctypes.byref(sid_text)):
            raise RuntimeError("PRODUCTION_RUNTIME_MUTEX_OWNER_INVALID")
        try:
            identity = str(sid_text.value or "")
        finally:
            kernel32.LocalFree(sid_text)
        if not identity:
            raise RuntimeError("PRODUCTION_RUNTIME_MUTEX_OWNER_INVALID")
        return identity
    finally:
        kernel32.CloseHandle(token)


def write_startup_failure_state(
    *, state_path: Path, returncode: int, issue_date: str, detail: str
) -> None:
    """runner 到達前の失敗を、6:40 監査が回収できる fixed state に凍結する。"""
    now = datetime.now().astimezone().isoformat(timespec="milliseconds")
    payload = {
        "status": "blocked_startup_self_repair_failed",
        "message": detail,
        "exit_code": int(returncode),
        "updated_at": now,
        "heartbeat_at": now,
        "date": issue_date,
        "run_intent": "ScheduledProduction",
        "run_id": f"launcher-{uuid4().hex}",
        "phase": "startup_self_repair",
        "attempt_terminal": True,
        "recovery_class": "startup_self_repair_failure",
        "scheduled_attempt_status": "failed",
        "recovery_attempt_status": "not_started",
    }
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_path.with_name(f".{state_path.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(state_path)


def freeze_startup_failure_if_needed(
    *, state_path: Path, returncode: int, issue_date: str, detail: str
) -> None:
    try:
        current = json.loads(state_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError):
        current = {}
    if (
        current.get("date") == issue_date
        and current.get("run_intent") == "ScheduledProduction"
        and isinstance(current.get("exit_code"), int)
        and current["exit_code"] > 0
        and current.get("status") != "running"
    ):
        return
    write_startup_failure_state(
        state_path=state_path,
        returncode=returncode,
        issue_date=issue_date,
        detail=detail,
    )


def _write_json_atomic(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.tmp.{os.getpid()}.{uuid4().hex}")
    payload = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    try:
        with temp.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(path)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _write_json_exclusive(path: Path, value: dict[str, object]) -> None:
    """完全に書けた同一volume tempだけをexclusive-createでauthorityへ昇格する。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
    payload = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    try:
        with temp.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temp, path)
    except FileExistsError as error:
        raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_AUTHORITY_EXISTS") from error
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


@contextmanager
def _production_runtime_mutex():
    """scheduled経路と公開CLIが共有する単一のcross-process mutation mutex。"""
    if sys.platform == "win32":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.WaitForSingleObject.restype = ctypes.c_uint32
        kernel32.ReleaseMutex.argtypes = [ctypes.c_void_p]
        kernel32.ReleaseMutex.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        handle = kernel32.CreateMutexW(
            None,
            False,
            RUNTIME_LEGACY_MUTEX_NAME,
        )
        if not handle:
            raise RuntimeError("PRODUCTION_RUNTIME_MUTEX_INVALID")
        acquired = kernel32.WaitForSingleObject(handle, 0)
        if acquired not in (0, 0x80):
            kernel32.CloseHandle(handle)
            raise RuntimeError("PRODUCTION_RUNTIME_MUTEX_BUSY")
        try:
            yield
        finally:
            kernel32.ReleaseMutex(handle)
            kernel32.CloseHandle(handle)
        return

    import fcntl

    lock_path = Path("/tmp/news-grasp-production-runtime-convergence.lock")
    with lock_path.open("a+b") as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("PRODUCTION_RUNTIME_MUTEX_BUSY") from error
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextmanager
def _production_runtime_outer_mutex():
    """新しいproduction mutex。receipt検証からruntime mutationまで同一lockで覆う。"""
    mutex_name = f"{RUNTIME_PRODUCTION_MUTEX_PREFIX}{_runtime_mutex_identity()}"
    if sys.platform == "win32":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.WaitForSingleObject.restype = ctypes.c_uint32
        kernel32.ReleaseMutex.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.CreateMutexW(None, False, mutex_name)
        if not handle:
            raise RuntimeError("PRODUCTION_RUNTIME_MUTEX_INVALID")
        observed = kernel32.WaitForSingleObject(handle, 0)
        if observed not in (0, 0x80):
            kernel32.CloseHandle(handle)
            raise RuntimeError("PRODUCTION_RUNTIME_MUTEX_BUSY")
        try:
            yield
        finally:
            kernel32.ReleaseMutex(handle)
            kernel32.CloseHandle(handle)
        return

    import fcntl

    lock_path = Path("/tmp/news-grasp-production-runtime.lock")
    with lock_path.open("a+b") as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("PRODUCTION_RUNTIME_MUTEX_BUSY") from error
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextmanager
def _production_runtime_lifecycle_mutex():
    """direct-call経路もbootstrap lifecycleと同じowner mutexへ束縛する。"""
    mutex_name = f"{RUNTIME_ORCHESTRATION_MUTEX_PREFIX}{_runtime_mutex_identity()}"
    if sys.platform == "win32":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.WaitForSingleObject.restype = ctypes.c_uint32
        kernel32.ReleaseMutex.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.CreateMutexW(None, False, mutex_name)
        if not handle:
            raise RuntimeError("PRODUCTION_RUNTIME_MUTEX_INVALID")
        observed = kernel32.WaitForSingleObject(handle, 0)
        if observed not in (0, 0x80):
            kernel32.CloseHandle(handle)
            raise RuntimeError("PRODUCTION_RUNTIME_MUTEX_BUSY")
        try:
            yield
        finally:
            kernel32.ReleaseMutex(handle)
            kernel32.CloseHandle(handle)
        return
    import fcntl

    lock_path = Path("/tmp/news-grasp-runtime-lifecycle.lock")
    with lock_path.open("a+b") as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("PRODUCTION_RUNTIME_MUTEX_BUSY") from error
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _process_ancestor_pids(max_hops: int = 3) -> tuple[int, ...]:
    if sys.platform != "win32":
        return (os.getppid(),)

    class ProcessEntry32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_uint32),
            ("cntUsage", ctypes.c_uint32),
            ("th32ProcessID", ctypes.c_uint32),
            ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", ctypes.c_uint32),
            ("cntThreads", ctypes.c_uint32),
            ("th32ParentProcessID", ctypes.c_uint32),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", ctypes.c_uint32),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ProcessEntry32W)]
    kernel32.Process32FirstW.restype = ctypes.c_int
    kernel32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ProcessEntry32W)]
    kernel32.Process32NextW.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise RuntimeError("PRODUCTION_RUNTIME_MUTEX_OWNER_INVALID")
    parents: dict[int, int] = {}
    try:
        entry = ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(entry)
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            raise RuntimeError("PRODUCTION_RUNTIME_MUTEX_OWNER_INVALID")
        while True:
            parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
    finally:
        kernel32.CloseHandle(snapshot)
    ancestors: list[int] = []
    current = os.getpid()
    for _ in range(max_hops):
        parent = parents.get(current, 0)
        if parent <= 0 or parent in ancestors:
            break
        ancestors.append(parent)
        current = parent
    return tuple(ancestors)


def _require_bootstrap_runtime_mutex_owner(
    owner_pid: int, *, owner_receipt_path: Path, owner_nonce: str
) -> None:
    """converge CLIがlifecycle mutex所有bootstrapのbounded子孫であることを確認する。"""
    if owner_pid <= 0 or owner_pid not in _process_ancestor_pids():
        raise RuntimeError("PRODUCTION_RUNTIME_MUTEX_OWNER_INVALID")
    expected_receipt = Path.home() / "bin" / "news-grasp-runtime-lifecycle-owner.json"
    candidate_receipt = Path(os.path.abspath(os.fspath(owner_receipt_path)))
    if candidate_receipt != expected_receipt or not re.fullmatch(
        r"[0-9a-f]{32}", owner_nonce
    ):
        raise RuntimeError("PRODUCTION_RUNTIME_MUTEX_OWNER_RECEIPT_INVALID")
    _assert_managed_path(
        candidate_receipt,
        expected_receipt.parent,
        "PRODUCTION_RUNTIME_MUTEX_OWNER_RECEIPT_INVALID",
    )
    try:
        if candidate_receipt.stat().st_size > 16 * 1024:
            raise ValueError("oversized")
        receipt = json.loads(candidate_receipt.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError) as error:
        raise RuntimeError("PRODUCTION_RUNTIME_MUTEX_OWNER_RECEIPT_INVALID") from error
    expected_mutex_name = f"{RUNTIME_ORCHESTRATION_MUTEX_PREFIX}{_runtime_mutex_identity()}"
    if (
        receipt.get("schemaVersion") != RUNTIME_LIFECYCLE_OWNER_SCHEMA
        or receipt.get("ownerPid") != owner_pid
        or receipt.get("ownerNonce") != owner_nonce
        or receipt.get("mutexName") != expected_mutex_name
        or not isinstance(receipt.get("ownerScriptPath"), str)
        or not str(receipt.get("ownerScriptPath") or "").lower().endswith(
            "news-grasp-bootstrap.ps1"
        )
        or Path(str(receipt.get("ownerProcessImage") or "")).name.lower()
        not in {"powershell.exe", "pwsh.exe"}
    ):
        raise RuntimeError("PRODUCTION_RUNTIME_MUTEX_OWNER_RECEIPT_INVALID")
    if sys.platform != "win32":
        return
    # PIDだけでは、別プロセスが同じmutex観測を借用できる。実際の
    # bootstrap executableも束縛し、直接spawnされた任意parentを拒否する。
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.QueryFullProcessImageNameW.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32)
    ]
    kernel32.QueryFullProcessImageNameW.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    process = kernel32.OpenProcess(0x1000, False, int(owner_pid))
    if not process:
        raise RuntimeError("PRODUCTION_RUNTIME_MUTEX_OWNER_INVALID")
    try:
        image_buffer = ctypes.create_unicode_buffer(32768)
        image_length = ctypes.c_uint32(len(image_buffer))
        if not kernel32.QueryFullProcessImageNameW(
            process, 0, image_buffer, ctypes.byref(image_length)
        ):
            raise RuntimeError("PRODUCTION_RUNTIME_MUTEX_OWNER_INVALID")
        image_name = Path(image_buffer.value).name.lower()
        if image_name not in {"powershell.exe", "pwsh.exe"}:
            raise RuntimeError("PRODUCTION_RUNTIME_MUTEX_OWNER_INVALID")
    finally:
        kernel32.CloseHandle(process)
    mutex_identity = _runtime_mutex_identity()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    kernel32.CreateFileW.restype = ctypes.c_void_p
    kernel32.GetLastError.restype = ctypes.c_uint32
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.ReleaseMutex.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    probe = kernel32.CreateFileW(
        str(candidate_receipt),
        0x40000000,
        0x7,
        None,
        3,
        0x80,
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if probe != invalid_handle:
        kernel32.CloseHandle(probe)
        raise RuntimeError("PRODUCTION_RUNTIME_MUTEX_OWNER_RECEIPT_NOT_LOCKED")
    if ctypes.get_last_error() != 32:
        raise RuntimeError("PRODUCTION_RUNTIME_MUTEX_OWNER_RECEIPT_INVALID")
    handle = kernel32.CreateMutexW(
        None,
        False,
        f"{RUNTIME_ORCHESTRATION_MUTEX_PREFIX}{mutex_identity}",
    )
    if not handle:
        raise RuntimeError("PRODUCTION_RUNTIME_MUTEX_OWNER_INVALID")
    observed = kernel32.WaitForSingleObject(handle, 0)
    if observed in (0, 0x80):
        kernel32.ReleaseMutex(handle)
        kernel32.CloseHandle(handle)
        raise RuntimeError("PRODUCTION_RUNTIME_MUTEX_NOT_HELD")
    kernel32.CloseHandle(handle)
    if observed != 0x102:
        raise RuntimeError("PRODUCTION_RUNTIME_MUTEX_OWNER_INVALID")


def _sha256_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _sha256_json_insertion_order(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_global_generation_manifest(
    *, manifest_path: Path, execution_repo: Path, expected_sha256: str
) -> dict[str, object]:
    """News-Grasp側へ封印された外部世代manifestを検証する。"""
    try:
        manifest = _assert_managed_path(
            manifest_path,
            execution_repo,
            "NEWS_GRASP_GLOBAL_GENERATION_MANIFEST_INVALID",
        ).resolve(strict=True)
        if manifest.is_symlink() or not manifest.is_file() or manifest.stat().st_size > 64 * 1024:
            raise RuntimeError("NEWS_GRASP_GLOBAL_GENERATION_MANIFEST_INVALID")
        observed_sha256 = _file_sha256(manifest)
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256) or observed_sha256 != expected_sha256:
            raise RuntimeError("NEWS_GRASP_GLOBAL_GENERATION_MANIFEST_DRIFT")
        value = json.loads(manifest.read_text(encoding="utf-8-sig"))
    except RuntimeError:
        raise
    except (OSError, ValueError, TypeError) as error:
        raise RuntimeError("NEWS_GRASP_GLOBAL_GENERATION_MANIFEST_INVALID") from error
    if not isinstance(value, dict) or set(value) != GLOBAL_GENERATION_MANIFEST_FIELDS:
        raise RuntimeError("NEWS_GRASP_GLOBAL_GENERATION_MANIFEST_INVALID")
    if (
        value.get("schemaVersion") != GLOBAL_GENERATION_MANIFEST_SCHEMA
        or not isinstance(value.get("generationId"), str)
        or not value["generationId"]
        or not isinstance(value.get("ownerRepo"), str)
        or not value["ownerRepo"]
        or not isinstance(value.get("ownerCommit"), str)
        or not re.fullmatch(r"[0-9a-f]{40,64}", value["ownerCommit"])
        or not isinstance(value.get("validForGoalId"), str)
        or not value["validForGoalId"]
    ):
        raise RuntimeError("NEWS_GRASP_GLOBAL_GENERATION_MANIFEST_INVALID")
    for path_key, hash_key in (
        ("sourceSnapshotPath", "sourceSnapshotSha256"),
        ("installedRuntimePath", "installedRuntimeSha256"),
        ("ownerAuthorityReceiptPath", "ownerAuthorityReceiptSha256"),
    ):
        try:
            payload = _assert_managed_path(
                Path(str(value[path_key])),
                execution_repo,
                "NEWS_GRASP_GLOBAL_GENERATION_MANIFEST_INVALID",
            ).resolve(strict=True)
            if payload.is_symlink() or not payload.is_file() or payload.stat().st_size > 64 * 1024 * 1024:
                raise RuntimeError("NEWS_GRASP_GLOBAL_GENERATION_MANIFEST_INVALID")
        except (OSError, TypeError, ValueError) as error:
            raise RuntimeError("NEWS_GRASP_GLOBAL_GENERATION_MANIFEST_INVALID") from error
        expected_payload_sha256 = value[hash_key]
        if not isinstance(expected_payload_sha256, str) or not re.fullmatch(
            r"[0-9a-f]{64}", expected_payload_sha256
        ) or _file_sha256(payload) != expected_payload_sha256:
            raise RuntimeError("NEWS_GRASP_GLOBAL_GENERATION_MANIFEST_DRIFT")
    return value


def _load_e2e_attempt_policy(
    *, policy_path: Path, execution_repo: Path, expected_sha256: str, expected_attempt: int
) -> dict[str, object]:
    """E2E論理attemptの上限とterminalをlauncher境界で再検証する。"""
    try:
        policy = _assert_managed_path(
            policy_path,
            execution_repo,
            "NEWS_GRASP_E2E_ATTEMPT_POLICY_INVALID",
        ).resolve(strict=True)
        if policy.is_symlink() or not policy.is_file() or policy.stat().st_size > 64 * 1024:
            raise RuntimeError("NEWS_GRASP_E2E_ATTEMPT_POLICY_INVALID")
        before = policy.stat()
        with policy.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if opened.st_size > 64 * 1024:
                raise RuntimeError("NEWS_GRASP_E2E_ATTEMPT_POLICY_INVALID")
            raw = stream.read(opened.st_size)
            after_open = os.fstat(stream.fileno())
        after = policy.stat()
        if len(raw) != opened.st_size or any(
            (item.st_dev, item.st_ino, item.st_size)
            != (before.st_dev, before.st_ino, before.st_size)
            for item in (opened, after_open, after)
        ):
            raise RuntimeError("NEWS_GRASP_E2E_ATTEMPT_POLICY_DRIFT")
        observed_sha256 = hashlib.sha256(raw).hexdigest()
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256) or observed_sha256 != expected_sha256:
            raise RuntimeError("NEWS_GRASP_E2E_ATTEMPT_POLICY_DRIFT")
        value = json.loads(raw.decode("utf-8-sig"))
    except RuntimeError:
        raise
    except (OSError, ValueError, TypeError) as error:
        raise RuntimeError("NEWS_GRASP_E2E_ATTEMPT_POLICY_INVALID") from error
    policy_validator = _validate_e2e_policy_transition
    if policy_validator is None:
        policy_validator = _load_policy_consumer_from_execution_repo(execution_repo)
    if policy_validator is None:
        raise RuntimeError("NEWS_GRASP_E2E_ATTEMPT_POLICY_CONSUMER_MISSING")
    try:
        value = policy_validator(value, policy)
    except Exception as error:
        raise RuntimeError(str(error)) from error
    expected_keys = {
        "schemaVersion",
        "maxLogicalAttempts",
        "maxFailureLocalResumes",
        "logicalAttemptIssued",
        "attemptA",
        "attemptB",
        "terminal",
        "designFeedback",
        "transition",
        "transitionHistory",
        "admissionBinding",
    }
    if (
        not isinstance(value, dict)
        or set(value) != expected_keys
        or value.get("schemaVersion") != "NEWS_GRASP_E2E_ATTEMPT_POLICY_V1"
        or value.get("maxLogicalAttempts") != 2
        or value.get("maxFailureLocalResumes") != 1
        or value.get("logicalAttemptIssued") != expected_attempt
        or expected_attempt not in (1, 2)
    ):
        raise RuntimeError("NEWS_GRASP_E2E_ATTEMPT_POLICY_INVALID")
    if value.get("terminal") is not None:
        raise RuntimeError("NEWS_GRASP_E2E_ATTEMPT_TERMINAL")
    attempt_a = value.get("attemptA")
    attempt_b = value.get("attemptB")
    if not isinstance(attempt_a, dict) or not isinstance(attempt_b, dict):
        raise RuntimeError("NEWS_GRASP_E2E_ATTEMPT_POLICY_INVALID")
    transition = value.get("transition")
    if not isinstance(transition, dict) or set(transition) != {
        "sequence", "event", "previousStateSha256", "stateSha256"
    }:
        raise RuntimeError("NEWS_GRASP_E2E_ATTEMPT_POLICY_INVALID")
    if expected_attempt == 1 and attempt_a.get("status") not in {
        "running",
        "resuming_after_minimal_repair",
    }:
        raise RuntimeError("NEWS_GRASP_E2E_ATTEMPT_POLICY_INVALID")
    if expected_attempt == 1 and (
        (transition.get("sequence"), transition.get("event"))
        not in {(1, "issue_a"), (2, "failure_local_resume")}
    ):
        raise RuntimeError("NEWS_GRASP_E2E_ATTEMPT_POLICY_INVALID")
    if expected_attempt == 2 and (
        attempt_a.get("status") != "ready_for_attempt_b"
        or attempt_b.get("status") != "running"
    ):
        raise RuntimeError("NEWS_GRASP_FULL_CORRECTION_REQUIRED")
    if expected_attempt == 2 and (
        transition.get("sequence"), transition.get("event")
    ) != (5, "issue_b"):
        raise RuntimeError("NEWS_GRASP_FULL_CORRECTION_REQUIRED")
    return value


def _load_policy_consumer_from_execution_repo(execution_repo: Path):
    """installed bin配置時も、検証済みgenerationのconsumerを読み込む。"""
    root = Path(execution_repo).resolve(strict=True)
    candidate = root / "tools" / "news_grasp_e2e_attempt_policy.py"
    try:
        candidate = candidate.resolve(strict=True)
    except OSError as error:
        raise RuntimeError("NEWS_GRASP_E2E_ATTEMPT_POLICY_CONSUMER_MISSING") from error
    if (
        candidate.is_symlink()
        or not candidate.is_file()
        or os.path.commonpath((str(root), str(candidate))) != str(root)
    ):
        raise RuntimeError("NEWS_GRASP_E2E_ATTEMPT_POLICY_CONSUMER_MISSING")
    module_name = f"news_grasp_e2e_attempt_policy_{hashlib.sha256(str(candidate).encode()).hexdigest()[:16]}"
    spec = importlib.util.spec_from_file_location(module_name, candidate)
    if spec is None or spec.loader is None:
        raise RuntimeError("NEWS_GRASP_E2E_ATTEMPT_POLICY_CONSUMER_MISSING")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as error:
        raise RuntimeError("NEWS_GRASP_E2E_ATTEMPT_POLICY_CONSUMER_MISSING") from error
    consumer = getattr(module, "validate_policy_ledger", None)
    if not callable(consumer):
        raise RuntimeError("NEWS_GRASP_E2E_ATTEMPT_POLICY_CONSUMER_MISSING")
    return consumer


def _load_module_from_exact_path(candidate: Path, *, prefix: str):
    """検証済み絶対pathだけをimportし、ambient sys.pathを参照しない。"""

    path = candidate.resolve(strict=True)
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("NEWS_GRASP_EXACT_MODULE_INVALID")
    module_name = f"{prefix}_{hashlib.sha256(str(path).encode()).hexdigest()[:16]}"
    cached = sys.modules.get(module_name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("NEWS_GRASP_EXACT_MODULE_INVALID")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def _validate_nopublish_isolation(
    *,
    execution_repo: Path,
    runtime_repo: Path,
    issue_date: str,
    receipt_path: Path,
) -> dict[str, object]:
    """検証済みruntime generationのconsumerで隔離差分だけを許可する。"""

    candidate = (runtime_repo / "tools" / "news_grasp_p08_evidence.py").resolve(strict=True)
    if candidate.is_symlink() or not candidate.is_file():
        raise RuntimeError("NEWS_GRASP_NOPUBLISH_ISOLATION_CONSUMER_MISSING")
    module_name = f"news_grasp_p08_evidence_{hashlib.sha256(str(candidate).encode()).hexdigest()[:16]}"
    spec = importlib.util.spec_from_file_location(module_name, candidate)
    if spec is None or spec.loader is None:
        raise RuntimeError("NEWS_GRASP_NOPUBLISH_ISOLATION_CONSUMER_MISSING")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        result = module.validate_isolation_receipt(
            receipt_path,
            repo_root=execution_repo,
            source_repo_root=runtime_repo,
            issue_date=issue_date,
        )
    except Exception as error:
        raise RuntimeError("NEWS_GRASP_NOPUBLISH_ISOLATION_INVALID") from error
    if (
        not isinstance(result, Mapping)
        or result.get("status") != "Green"
        or not isinstance(result.get("validation"), Mapping)
    ):
        raise RuntimeError("NEWS_GRASP_NOPUBLISH_ISOLATION_INVALID")
    return dict(result)


def _load_stable_launcher_identity(*, bin_dir: Path) -> dict[str, object]:
    """installed launcher bytesをinstaller発行authorityへ束縛する。"""
    authority_path = bin_dir / "news-grasp-stable-task-authority-v1.json"
    try:
        if authority_path.stat().st_size > 64 * 1024:
            raise ValueError("oversized")
        authority = json.loads(authority_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError) as error:
        raise RuntimeError("NEWS_GRASP_STABLE_TASK_AUTHORITY_INVALID") from error
    if not isinstance(authority, dict):
        raise RuntimeError("NEWS_GRASP_STABLE_TASK_AUTHORITY_INVALID")
    unsigned = dict(authority)
    authority_sha256 = str(unsigned.pop("authoritySha256", ""))
    allowed_hashes = {
        _sha256_json(unsigned),
        _sha256_json_insertion_order(unsigned),
    }
    action = authority.get("action")
    launcher = Path(__file__).resolve()
    try:
        stable_path = Path(str(authority.get("stableLauncherPath") or "")).resolve(strict=True)
    except OSError as error:
        raise RuntimeError("NEWS_GRASP_STABLE_TASK_AUTHORITY_INVALID") from error
    if (
        authority.get("schemaVersion") != STABLE_TASK_AUTHORITY_SCHEMA
        or authority_sha256 not in allowed_hashes
        or authority.get("repoArgumentCount") != 0
        or not isinstance(action, list)
        or len(action) != 10
        or any(not isinstance(item, str) or not item for item in action)
        or stable_path != launcher
        or action[1:4] != ["-I", "-S", "-B"]
        or Path(str(action[4])).resolve() != launcher
        or action[5:] != [
            "dispatch",
            "--schedule-id",
            _CLEANROOM_SCHEDULE_ID,
            "--intent",
            _CLEANROOM_INTENT,
        ]
        or str(authority.get("stableLauncherSha256") or "") != _file_sha256(launcher)
        or any(item.casefold() in {"--repo-dir", "--worktree"} for item in action)
    ):
        raise RuntimeError("NEWS_GRASP_STABLE_TASK_AUTHORITY_INVALID")
    try:
        binding_path = Path(str(authority.get("highCostBindingPath") or "")).resolve(strict=True)
        expected_binding_path = (bin_dir / "news-grasp-high-cost-binding-v1.json").resolve(strict=True)
        task_pythonw = Path(str(action[0])).resolve(strict=True)
        recovery_path = (bin_dir / "news-grasp-recovery-runtime-binding-v1.json").resolve(strict=True)
        if binding_path.stat().st_size > 64 * 1024 or recovery_path.stat().st_size > 64 * 1024:
            raise ValueError("oversized")
        binding = json.loads(binding_path.read_text(encoding="utf-8-sig"))
        recovery = json.loads(recovery_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        raise RuntimeError("NEWS_GRASP_STABLE_TASK_AUTHORITY_INVALID") from error
    binding_receipt = str(authority.get("highCostBindingReceiptSha256") or "").lower()
    if (
        binding_path != expected_binding_path
        or not binding_path.is_file()
        or binding_path.is_symlink()
        or not recovery_path.is_file()
        or recovery_path.is_symlink()
        or not re.fullmatch(r"[0-9a-f]{64}", binding_receipt)
        or not isinstance(binding, dict)
        or binding.get("schemaVersion") != "NEWS_GRASP_HIGH_COST_BINDING_V1"
        or str(binding.get("bindingReceiptSha256") or "").lower() != binding_receipt
        or task_pythonw.name.casefold() not in {"pythonw.exe", "pythonw"}
        or not task_pythonw.is_file()
        or task_pythonw.is_symlink()
        or not isinstance(recovery, dict)
        or recovery.get("schemaVersion") != "NEWS_GRASP_RECOVERY_RUNTIME_BINDING_V1"
        or Path(str(recovery.get("highCostBindingPath") or "")).resolve() != binding_path
        or str(recovery.get("highCostBindingReceiptSha256") or "").lower() != binding_receipt
        or Path(str(recovery.get("taskPythonwPath") or "")).resolve() != task_pythonw
        or str(recovery.get("taskPythonwSha256") or "").lower() != _file_sha256(task_pythonw)
    ):
        raise RuntimeError("NEWS_GRASP_STABLE_TASK_AUTHORITY_INVALID")
    return {
        **authority,
        "authorityPath": str(authority_path.resolve()),
        "authorityFileSha256": _file_sha256(authority_path),
    }


def _stable_authority_option(identity: dict[str, object], option: str) -> str:
    # 新しい dispatch authority は high-cost binding を action 配列から
    # 分離して top-level に封印する。旧 authority の action 配列も引き続き
    # 読めるよう、top-level を先に見て見つからなければ従来経路へ戻す。
    top_level_names = {
        "--high-cost-binding-path": ("highCostBindingPath",),
        "--high-cost-binding-sha256": (
            "highCostBindingReceiptSha256",
            "highCostBindingSha256",
        ),
    }
    for name in top_level_names.get(option, ()):
        value = identity.get(name)
        if isinstance(value, str) and value:
            return value
    action = identity.get("action")
    if not isinstance(action, list) or action.count(option) != 1:
        raise RuntimeError("NEWS_GRASP_STABLE_TASK_AUTHORITY_INVALID")
    index = action.index(option)
    if index + 1 >= len(action) or not isinstance(action[index + 1], str):
        raise RuntimeError("NEWS_GRASP_STABLE_TASK_AUTHORITY_INVALID")
    return str(action[index + 1])


_CLEANROOM_SCHEDULE_ID = "news-grasp-daily-v1"
_CLEANROOM_INTENT = "reconcile"
_CLEANROOM_LEASE_SECONDS = 3600
_CLEANROOM_TOKYO = timezone(timedelta(hours=9), name="Asia/Tokyo")
_CLEANROOM_CONTEXT_TASK_NAME = "News-Grasp Production"
_CLEANROOM_BOOTSTRAP_TASK_NAME = "News-Grasp Bootstrap"
_CLEANROOM_CONTEXT_TIMEOUT_SECONDS = 10
_CLEANROOM_CONTEXT_MAX_OUTPUT_BYTES = 256 * 1024
_CLEANROOM_CONTEXT_MAX_ANCESTORS = 8
_CLEANROOM_CONTEXT_EXPECTED_TRIGGER_TIMES = ("06:00:00",)
_CLEANROOM_CHILD_TIMEOUT_SECONDS = 3600
_CLEANROOM_CHILD_MAX_OUTPUT_BYTES = 256 * 1024
_CLEANROOM_CONTEXT_PAYLOAD_FIELDS = frozenset(
    {
        "targetProcessId",
        "parentProcessId",
        "parentProcessName",
        "parentProcessCommandLine",
        "parentProcessPath",
        "parentAuthenticodeStatus",
        "parentAuthenticodeSubject",
        "scheduleServiceName",
        "scheduleServicePid",
        "scheduleServiceState",
        "scheduleServiceCommandLine",
        "taskName",
        "enabled",
        "state",
        "lastRunTime",
        "taskPath",
        "multipleInstancesPolicy",
        "actions",
        "triggers",
        "ancestorChain",
    }
)
_CLEANROOM_CONTEXT_ANCESTOR_FIELDS = frozenset(
    {
        "pid",
        "path",
        "name",
        "commandLine",
        "authenticodeStatus",
        "authenticodeSubject",
    }
)


def _normalize_cleanroom_observed_at(value: datetime | str) -> datetime:
    """clean-room controllerへ渡す観測時刻をJSTへ正規化する。"""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except (TypeError, ValueError) as error:
            raise ValueError("NEWS_GRASP_ENTRY_TIME_INVALID") from error
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("NEWS_GRASP_ENTRY_TIME_INVALID")
    return value.astimezone(_CLEANROOM_TOKYO)


def _cleanroom_context_path(value: object) -> str | None:
    """Task観測値を環境変数展開後の比較可能な絶対pathへ正規化する。"""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().strip('"')
    user_profile = os.environ.get("USERPROFILE") or str(Path.home())
    system_root = (
        os.environ.get("SystemRoot")
        or os.environ.get("SYSTEMROOT")
        or os.environ.get("WINDIR")
        or ""
    )
    environment = {
        "USERPROFILE": user_profile,
        "SYSTEMROOT": system_root,
        "WINDIR": system_root,
    }

    def replace_windows_variable(match: re.Match[str]) -> str:
        return environment.get(match.group(1).upper(), match.group(0))

    # os.path.expandvars は実行ホストのpath flavorだけを扱うため、Taskの
    # Windows形式 ``%SystemRoot%`` を先に明示展開する。
    text = re.sub(r"%([^%]+)%", replace_windows_variable, text)
    text = os.path.expandvars(os.path.expanduser(text))
    if re.match(r"^[A-Za-z]:[\\/]", text) or text.startswith(("\\\\", "//")):
        return ntpath.normcase(ntpath.normpath(text.replace("/", "\\"))).casefold()
    try:
        resolved = Path(text).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    return os.path.normcase(os.path.normpath(str(resolved))).casefold()


def _cleanroom_windows_argument_tokens(value: object) -> list[str] | None:
    """Task action Argumentsをshellを起動せずに最小のWindows tokenへ分解する。"""
    if not isinstance(value, str):
        return None
    tokens: list[str] = []
    current: list[str] = []
    quoted = False
    for character in value.strip():
        if character == '"':
            quoted = not quoted
            continue
        if character.isspace() and not quoted:
            if current:
                tokens.append("".join(current))
                current = []
            continue
        current.append(character)
    if quoted:
        return None
    if current:
        tokens.append("".join(current))
    return tokens


def _cleanroom_context_powershell_command(
    *,
    launcher_pid: int | None = None,
    task_name: str = _CLEANROOM_CONTEXT_TASK_NAME,
) -> list[str]:
    """Task定義とOS Task Scheduler親を一回readするbounded commandを返す。"""
    target_pid = os.getpid() if launcher_pid is None else launcher_pid
    if isinstance(target_pid, bool) or not isinstance(target_pid, int) or target_pid < 1:
        raise ValueError("NEWS_GRASP_TASK_ORIGIN_PID_INVALID")
    if task_name not in {
        _CLEANROOM_CONTEXT_TASK_NAME,
        _CLEANROOM_BOOTSTRAP_TASK_NAME,
    }:
        raise ValueError("NEWS_GRASP_TASK_NAME_INVALID")
    task_literal = task_name.replace("'", "''")
    command = (
        "$OutputEncoding=[System.Text.UTF8Encoding]::new($false); "
        "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); "
        f"$targetPid={target_pid}; "
        "$targetProcess=Get-CimInstance Win32_Process -Filter ('ProcessId=' + $targetPid) -ErrorAction Stop; "
        "$parentProcess=Get-CimInstance Win32_Process -Filter ('ProcessId=' + $targetProcess.ParentProcessId) -ErrorAction Stop; "
        "$scheduleService=Get-CimInstance Win32_Service -Filter \"Name='Schedule'\" -ErrorAction Stop; "
        "$scheduleServiceCommandLine=[string]$scheduleService.PathName; "
        "$getAuthenticode = { param([string]$path) "
        "if ([string]::IsNullOrWhiteSpace($path)) { "
        "[ordered]@{status='';subject=''} "
        "} else { "
        "$signature=Get-AuthenticodeSignature -LiteralPath $path -ErrorAction Stop; "
        "$subject=''; if ($null -ne $signature.SignerCertificate) { "
        "$subject=[string]$signature.SignerCertificate.Subject }; "
        "[ordered]@{status=[string]$signature.Status;subject=$subject} } }; "
        "$parentPath=[string]$parentProcess.ExecutablePath; "
        "$parentSignature=&$getAuthenticode $parentPath; "
        "$ancestorChain=New-Object System.Collections.Generic.List[object]; "
        "$seen=@{}; $current=$targetProcess; "
        f"for ($hop=0; $hop -lt {_CLEANROOM_CONTEXT_MAX_ANCESTORS}; $hop++) {{ "
        "$parentId=[int]$current.ParentProcessId; "
        "if ($parentId -le 0 -or $seen.ContainsKey([string]$parentId)) { break }; "
        "$ancestor=Get-CimInstance Win32_Process -Filter ('ProcessId=' + $parentId) -ErrorAction Stop; "
        "if ($null -eq $ancestor) { break }; "
        "$ancestorPath=[string]$ancestor.ExecutablePath; "
        "if ([string]::IsNullOrWhiteSpace($ancestorPath)) { break }; "
        "$ancestorSignature=&$getAuthenticode $ancestorPath; "
        "[void]$ancestorChain.Add([ordered]@{pid=$parentId;path=$ancestorPath;"
        "name=[string]$ancestor.Name;commandLine=[string]$ancestor.CommandLine;"
        "authenticodeStatus=[string]$ancestorSignature.status;"
        "authenticodeSubject=[string]$ancestorSignature.subject}); "
        "$seen[[string]$parentId]=$true; $current=$ancestor }; "
        f"$task=Get-ScheduledTask -TaskName '{task_literal}' -ErrorAction Stop; "
        f"$info=Get-ScheduledTaskInfo -TaskName '{task_literal}' -ErrorAction Stop; "
        "$actions=@($task.Actions)|ForEach-Object { "
        "[ordered]@{execute=[string]$_.Execute;arguments=[string]$_.Arguments;workingDirectory=[string]$_.WorkingDirectory} }; "
        "$triggers=@($task.Triggers)|Where-Object { $_.Enabled -eq $true }|ForEach-Object { "
        "$kind=[string]$_.CimClass.CimClassName; "
        "$boundary=[string]$_.StartBoundary; "
        "[ordered]@{enabled=[bool]$_.Enabled;kind=$kind;startBoundary=$boundary} }; "
        "$lastRunTime=''; "
        "if ($null -ne $info.LastRunTime) { $lastRunTime=([datetimeoffset]$info.LastRunTime).ToString('o') }; "
        "[ordered]@{targetProcessId=[int]$targetProcess.ProcessId;parentProcessId=[int]$targetProcess.ParentProcessId;"
        "parentProcessName=[string]$parentProcess.Name;parentProcessCommandLine=[string]$parentProcess.CommandLine;"
        "parentProcessPath=$parentPath;parentAuthenticodeStatus=[string]$parentSignature.status;"
        "parentAuthenticodeSubject=[string]$parentSignature.subject;"
        "scheduleServiceName=[string]$scheduleService.Name;scheduleServicePid=[int]$scheduleService.ProcessId;"
        "scheduleServiceState=[string]$scheduleService.State;scheduleServiceCommandLine=$scheduleServiceCommandLine;"
        "taskName=[string]$task.TaskName;enabled=[bool]$task.Settings.Enabled;state=[string]$task.State;"
        "lastRunTime=$lastRunTime;taskPath=[string]$task.TaskPath;"
        "multipleInstancesPolicy=[string]$task.Settings.MultipleInstances;"
        "actions=@($actions);triggers=@($triggers);ancestorChain=$ancestorChain.ToArray()}|"
        "ConvertTo-Json -Depth 10 -Compress"
    )
    return [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        command,
    ]


def _cleanroom_decode_context_json(value: object) -> dict[str, object] | None:
    """PowerShellのUTF-8 JSONをboundedにdecodeし、object以外を拒否する。"""
    if isinstance(value, str):
        raw = value.encode("utf-8", errors="replace")
    elif isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
    else:
        return None
    if len(raw) > _CLEANROOM_CONTEXT_MAX_OUTPUT_BYTES:
        return None
    try:
        decoded = raw.decode("utf-8-sig")
        value = json.loads(decoded)
    except (UnicodeDecodeError, ValueError, TypeError):
        return None
    return value if type(value) is dict else None


def _cleanroom_system32_executable_path(name: object) -> str | None:
    """SystemRoot配下の実行ファイルをWindows pathとして正規化する。"""
    if not isinstance(name, str) or not name.strip():
        return None
    executable_name = ntpath.basename(name.strip().replace("/", "\\"))
    if not executable_name or executable_name in {".", ".."}:
        return None
    system_root = (
        os.environ.get("SystemRoot")
        or os.environ.get("SYSTEMROOT")
        or os.environ.get("WINDIR")
    )
    if not isinstance(system_root, str) or not system_root.strip():
        return None
    if re.match(r"^[A-Za-z]:[\\/]", system_root) or "\\" in system_root:
        candidate = ntpath.join(system_root, "System32", executable_name)
    else:
        candidate = str(Path(system_root) / "System32" / executable_name)
    return _cleanroom_context_path(candidate)


def _cleanroom_microsoft_authenticode(status: object, subject: object) -> bool:
    """Windows署名がValidかつMicrosoft Windows主体であることを確認する。"""
    if not isinstance(status, str) or status.casefold() != "valid":
        return False
    if not isinstance(subject, str):
        return False
    folded = subject.casefold()
    return "microsoft" in folded and "windows" in folded


def _cleanroom_service_schedule_tokens(value: object) -> bool:
    """Service Hostの``-s Schedule``をsubstringではなくtokenで確認する。"""
    tokens = _cleanroom_windows_argument_tokens(value)
    if tokens is None or not tokens:
        return False
    expected_svchost = _cleanroom_system32_executable_path("svchost.exe")
    if expected_svchost is None or _cleanroom_context_path(tokens[0]) != expected_svchost:
        return False
    for index, token in enumerate(tokens[:-1]):
        if token.casefold() == "-s" and tokens[index + 1].casefold() == "schedule":
            return True
    # 現行Windowsの共有netsvcs hostではWin32_Service.PathNameが
    # ``svchost.exe -k netsvcs -p``を返し、``-s Schedule``を含まない。
    # Service名/PID/Stateとlauncherの直親PID一致は呼出側で別途束縛する。
    return [token.casefold() for token in tokens[1:]] == ["-k", "netsvcs", "-p"]


def _cleanroom_validate_process_witness(payload: dict[str, object]) -> bool:
    """Task起動元の親・署名・Schedule service・bounded ancestorを厳密検証する。"""
    if set(payload) != _CLEANROOM_CONTEXT_PAYLOAD_FIELDS:
        return False
    if (
        type(payload.get("targetProcessId")) is not int
        or payload.get("targetProcessId") != os.getpid()
        or type(payload.get("parentProcessId")) is not int
        or payload.get("parentProcessId") <= 0
        or payload.get("parentProcessId") == payload.get("targetProcessId")
        or not isinstance(payload.get("parentProcessName"), str)
        or not payload.get("parentProcessName")
    ):
        return False
    parent_name = Path(str(payload["parentProcessName"])).name.casefold()
    if parent_name not in {"taskeng.exe", "taskhostw.exe", "svchost.exe"}:
        return False
    if (
        payload.get("scheduleServiceName") != "Schedule"
        or type(payload.get("scheduleServicePid")) is not int
        or payload.get("scheduleServicePid") <= 0
        or payload.get("scheduleServiceState") != "Running"
        or not _cleanroom_service_schedule_tokens(payload.get("scheduleServiceCommandLine"))
    ):
        return False
    ancestors = payload.get("ancestorChain")
    direct_protected_schedule_parent = (
        parent_name == "svchost.exe"
        and payload.get("parentProcessId") == payload.get("scheduleServicePid")
        and payload.get("parentProcessCommandLine") == ""
        and payload.get("parentProcessPath") == ""
        and payload.get("parentAuthenticodeStatus") == ""
        and payload.get("parentAuthenticodeSubject") == ""
    )
    if direct_protected_schedule_parent:
        # Task Scheduler serviceのprotected process情報は非管理者Taskから空に
        # なる。Service名/PID/State、共有host token、直親PIDの一致を代替証拠とする。
        return ancestors == []
    if (
        not isinstance(payload.get("parentProcessCommandLine"), str)
        or not payload.get("parentProcessCommandLine")
        or not isinstance(payload.get("parentProcessPath"), str)
        or not payload.get("parentProcessPath")
        or not _cleanroom_microsoft_authenticode(
            payload.get("parentAuthenticodeStatus"),
            payload.get("parentAuthenticodeSubject"),
        )
    ):
        return False
    expected_parent_path = _cleanroom_system32_executable_path(parent_name)
    if (
        expected_parent_path is None
        or _cleanroom_context_path(payload["parentProcessPath"]) != expected_parent_path
    ):
        return False
    if type(ancestors) is not list or not ancestors or len(ancestors) > _CLEANROOM_CONTEXT_MAX_ANCESTORS:
        return False
    seen: set[int] = set()
    service_entry: dict[str, object] | None = None
    for index, ancestor in enumerate(ancestors):
        if type(ancestor) is not dict or set(ancestor) != _CLEANROOM_CONTEXT_ANCESTOR_FIELDS:
            return False
        pid = ancestor.get("pid")
        if type(pid) is not int or pid <= 0 or pid in seen:
            return False
        seen.add(pid)
        name = ancestor.get("name")
        path = ancestor.get("path")
        command_line = ancestor.get("commandLine")
        if (
            not isinstance(name, str)
            or not name
            or not isinstance(path, str)
            or not path
            or not isinstance(command_line, str)
            or not command_line
            or not _cleanroom_microsoft_authenticode(
                ancestor.get("authenticodeStatus"),
                ancestor.get("authenticodeSubject"),
            )
        ):
            return False
        expected_path = _cleanroom_system32_executable_path(name)
        if expected_path is None or _cleanroom_context_path(path) != expected_path:
            return False
        if index == 0 and (
            pid != payload.get("parentProcessId")
            or _cleanroom_context_path(path)
            != _cleanroom_context_path(payload.get("parentProcessPath"))
            or name.casefold() != parent_name
            or command_line != payload.get("parentProcessCommandLine")
            or ancestor.get("authenticodeStatus")
            != payload.get("parentAuthenticodeStatus")
            or ancestor.get("authenticodeSubject")
            != payload.get("parentAuthenticodeSubject")
        ):
            return False
        if pid == payload.get("scheduleServicePid"):
            if name.casefold() != "svchost.exe" or not _cleanroom_service_schedule_tokens(command_line):
                return False
            service_entry = ancestor
    return service_entry is not None


def _cleanroom_expected_pythonw(*, bin_dir: Path) -> str | None:
    """installer authorityにあればpythonwを読み、なければ現processの姉妹pathを使う。"""
    authority_path = bin_dir / "news-grasp-stable-task-authority-v1.json"
    try:
        if authority_path.is_file() and authority_path.stat().st_size <= 64 * 1024:
            authority = json.loads(authority_path.read_text(encoding="utf-8-sig"))
            action = authority.get("action") if isinstance(authority, dict) else None
            if isinstance(action, list) and action and isinstance(action[0], str) and action[0]:
                return _cleanroom_context_path(action[0])
    except (OSError, ValueError, TypeError):
        return None
    try:
        candidate = Path(sys.executable)
        if candidate.name.casefold() not in {"pythonw.exe", "pythonw"}:
            candidate = candidate.with_name("pythonw.exe")
        return _cleanroom_context_path(str(candidate))
    except (OSError, RuntimeError, ValueError):
        return None


def _cleanroom_default_task_context_validator(
    *,
    bin_dir: Path,
    observed_at: datetime,
    schedule_id: str = _CLEANROOM_SCHEDULE_ID,
    intent: str = _CLEANROOM_INTENT,
) -> bool:
    """Production dispatch前にcanonical Taskをread-onlyで一回観測する。"""
    if schedule_id != _CLEANROOM_SCHEDULE_ID or intent != _CLEANROOM_INTENT:
        return False
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        completed = subprocess.run(
            _cleanroom_context_powershell_command(launcher_pid=os.getpid()),
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=_CLEANROOM_CONTEXT_TIMEOUT_SECONDS,
            creationflags=creationflags,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if int(getattr(completed, "returncode", 1)) != 0:
        return False
    payload = _cleanroom_decode_context_json(getattr(completed, "stdout", b""))
    if payload is None or not _cleanroom_validate_process_witness(payload):
        return False
    if (
        payload.get("taskName") != _CLEANROOM_CONTEXT_TASK_NAME
        or type(payload.get("enabled")) is not bool
        or payload.get("enabled") is not True
        or payload.get("state") != "Running"
        or payload.get("taskPath") != "\\"
        or payload.get("multipleInstancesPolicy") != "IgnoreNew"
    ):
        return False
    try:
        last_run_text = payload.get("lastRunTime")
        if not isinstance(last_run_text, str) or not last_run_text:
            return False
        last_run = datetime.fromisoformat(last_run_text.replace("Z", "+00:00"))
        if last_run.tzinfo is None or last_run.utcoffset() is None:
            return False
        age_seconds = (observed_at - last_run.astimezone(_CLEANROOM_TOKYO)).total_seconds()
        if age_seconds < -60 or age_seconds > 10 * 60:
            return False
    except (TypeError, ValueError, OverflowError):
        return False

    actions = payload.get("actions")
    if type(actions) is not list or len(actions) != 1 or type(actions[0]) is not dict:
        return False
    action = actions[0]
    if set(action) != {"execute", "arguments", "workingDirectory"}:
        return False
    execute = _cleanroom_context_path(action.get("execute"))
    expected_pythonw = _cleanroom_expected_pythonw(bin_dir=bin_dir)
    if execute is None or expected_pythonw is None or execute != expected_pythonw:
        return False
    if Path(str(action.get("execute") or "")).name.casefold() not in {"pythonw.exe", "pythonw"}:
        return False
    launcher_path = (bin_dir / "news-grasp-task-launcher.pyw").resolve()
    if _cleanroom_context_path(str(launcher_path)) != _cleanroom_context_path(str(Path(__file__).resolve())):
        return False
    arguments = _cleanroom_windows_argument_tokens(action.get("arguments"))
    if (
        arguments is None
        or len(arguments) != 9
        or arguments[:3] != ["-I", "-S", "-B"]
        or _cleanroom_context_path(arguments[3])
        != _cleanroom_context_path(str(launcher_path))
        or arguments[4:] != [
        "dispatch",
        "--schedule-id",
        _CLEANROOM_SCHEDULE_ID,
        "--intent",
        _CLEANROOM_INTENT,
        ]
    ):
        return False
    expected_working_directory = (
        Path(os.environ.get("USERPROFILE") or Path.home())
        / ".news-grasp-runtime"
        / "production-runtime"
    )
    if _cleanroom_context_path(action.get("workingDirectory")) != _cleanroom_context_path(
        str(expected_working_directory)
    ):
        return False

    triggers = payload.get("triggers")
    if type(triggers) is not list or len(triggers) != 1:
        return False
    observed_trigger_times: list[str] = []
    for trigger in triggers:
        if type(trigger) is not dict or trigger.get("enabled") is not True:
            return False
        if set(trigger) != {"enabled", "kind", "startBoundary"}:
            return False
        if "daily" not in str(trigger.get("kind") or "").casefold():
            return False
        boundary = trigger.get("startBoundary")
        if not isinstance(boundary, str) or not boundary:
            return False
        try:
            parsed_boundary = datetime.fromisoformat(boundary.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return False
        observed_trigger_times.append(parsed_boundary.strftime("%H:%M:%S"))
    return sorted(observed_trigger_times) == sorted(_CLEANROOM_CONTEXT_EXPECTED_TRIGGER_TIMES)


def _run_cleanroom_task_context_validator(
    validator,
    *,
    bin_dir: Path,
    observed_at: datetime,
    schedule_id: str,
    intent: str,
) -> None:
    """validatorを一度だけ実行し、false/例外をtyped拒否へ変換する。"""
    if not callable(validator):
        raise RuntimeError("NEWS_GRASP_TASK_CONTEXT_VALIDATOR_INVALID")
    try:
        accepted = validator(
            bin_dir=bin_dir,
            observed_at=observed_at,
            schedule_id=schedule_id,
            intent=intent,
        )
    except Exception as error:
        raise RuntimeError("NEWS_GRASP_TASK_CONTEXT_VALIDATION_FAILED") from error
    if accepted is not True:
        raise RuntimeError("NEWS_GRASP_TASK_CONTEXT_INVALID")


def _cleanroom_default_task_origin_validator(
    *,
    bin_dir: Path,
    observed_at: datetime,
    nonce: str,
    generation: str,
    receipt_path: Path | str | None = None,
    schedule_id: str = _CLEANROOM_SCHEDULE_ID,
    intent: str = _CLEANROOM_INTENT,
) -> bool:
    """temporary entry-canaryのOS Task Scheduler親とcanonical actionを検証する。"""
    if (
        schedule_id != _CLEANROOM_SCHEDULE_ID
        or intent != _CLEANROOM_INTENT
        or not isinstance(nonce, str)
        or not re.fullmatch(r"[0-9a-f]{32}", nonce)
        or not isinstance(generation, str)
        or not generation
        or receipt_path is None
    ):
        return False
    expected_receipt_path = _cleanroom_context_path(str(receipt_path))
    if expected_receipt_path is None:
        return False
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        completed = subprocess.run(
            _cleanroom_context_powershell_command(launcher_pid=os.getpid()),
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=_CLEANROOM_CONTEXT_TIMEOUT_SECONDS,
            creationflags=creationflags,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if int(getattr(completed, "returncode", 1)) != 0:
        return False
    payload = _cleanroom_decode_context_json(getattr(completed, "stdout", b""))
    if payload is None or not _cleanroom_validate_process_witness(payload):
        return False
    if (
        payload.get("taskName") != _CLEANROOM_CONTEXT_TASK_NAME
        or payload.get("enabled") is not True
        or payload.get("state") != "Running"
        or payload.get("taskPath") != "\\"
        or payload.get("multipleInstancesPolicy") != "IgnoreNew"
    ):
        return False
    try:
        last_run_text = payload.get("lastRunTime")
        last_run = datetime.fromisoformat(str(last_run_text).replace("Z", "+00:00"))
        if last_run.tzinfo is None or last_run.utcoffset() is None:
            return False
        age_seconds = (observed_at - last_run.astimezone(_CLEANROOM_TOKYO)).total_seconds()
        if age_seconds < -60 or age_seconds > 10 * 60:
            return False
    except (TypeError, ValueError, OverflowError):
        return False
    actions = payload.get("actions")
    if type(actions) is not list or len(actions) != 1 or type(actions[0]) is not dict:
        return False
    action = actions[0]
    execute = _cleanroom_context_path(action.get("execute"))
    expected_pythonw = _cleanroom_expected_pythonw(bin_dir=bin_dir)
    launcher_path = (bin_dir / "news-grasp-task-launcher.pyw").resolve()
    arguments = _cleanroom_windows_argument_tokens(action.get("arguments"))
    if (
        execute is None
        or expected_pythonw is None
        or execute != expected_pythonw
        or Path(str(action.get("execute") or "")).name.casefold()
        not in {"pythonw.exe", "pythonw"}
        or _cleanroom_context_path(str(launcher_path))
        != _cleanroom_context_path(str(Path(__file__).resolve()))
        or arguments is None
        or len(arguments) != 11
        or arguments[:3] != ["-I", "-S", "-B"]
        or _cleanroom_context_path(arguments[3])
        != _cleanroom_context_path(str(launcher_path))
        or arguments[4:9]
        != [
            "task-origin-canary",
            "--canary-nonce",
            nonce,
            "--canary-generation",
            generation,
        ]
        or arguments[9] != "--canary-receipt-path"
        or _cleanroom_context_path(arguments[10]) != expected_receipt_path
        or _cleanroom_context_path(action.get("workingDirectory"))
        != _cleanroom_context_path(
            str(
                Path(os.environ.get("USERPROFILE") or Path.home())
                / ".news-grasp-runtime"
                / "production-runtime"
            )
        )
    ):
        return False
    return True


def _invoke_cleanroom_canary_seam(function, context: Mapping[str, object]):
    """seam引数を一度だけ、signatureに束縛して呼び出す。"""
    if not callable(function):
        raise RuntimeError("NEWS_GRASP_CANARY_SEAM_INVALID")
    try:
        signature = inspect.signature(function)
    except (TypeError, ValueError):
        return function(**dict(context))
    parameters = tuple(signature.parameters.values())
    if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters):
        return function(**dict(context))
    positional: list[object] = []
    keyword: dict[str, object] = {}
    for parameter in parameters:
        if parameter.kind == inspect.Parameter.VAR_POSITIONAL:
            continue
        if parameter.name not in context:
            if (
                parameter.default is inspect.Parameter.empty
                and parameter.kind
                in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
            ):
                raise RuntimeError("NEWS_GRASP_CANARY_SEAM_SIGNATURE_INVALID")
            continue
        if parameter.kind == inspect.Parameter.POSITIONAL_ONLY:
            positional.append(context[parameter.name])
        else:
            keyword[parameter.name] = context[parameter.name]
    return function(*positional, **keyword)


def _cleanroom_canary_generation(value: object) -> str:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return str(value)
    raise RuntimeError("NEWS_GRASP_CANARY_GENERATION_INVALID")


def run_task_origin_canary(
    *,
    task_action,
    start_task,
    nonce: str,
    wait_receipt,
    restore_task,
    final_parity,
    task_context_validator=None,
    task_origin_validator=None,
    observed_at: datetime | str | None = None,
    generation: str | int | None = None,
    bin_dir: Path | None = None,
    runtime_root: Path | None = None,
    manifest_path: Path | None = None,
    receipt_path: Path | None = None,
    controller_factory=None,
    child_runner=None,
    timeout_seconds: float = 30.0,
    manage_task: bool = True,
) -> dict[str, object]:
    """Task-origin entry-canaryを一回だけ実行し、isolated ledgerへ終端を記録する。"""
    if not isinstance(nonce, str) or re.fullmatch(r"[0-9a-f]{32}", nonce) is None:
        raise RuntimeError("NEWS_GRASP_CANARY_NONCE_INVALID")
    canary_generation = _cleanroom_canary_generation(generation)
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
        raise RuntimeError("NEWS_GRASP_CANARY_TIMEOUT_INVALID")
    observed = _normalize_cleanroom_observed_at(
        observed_at if observed_at is not None else datetime.now(_CLEANROOM_TOKYO)
    )
    installed_bin = Path(bin_dir or (Path.home() / "bin")).resolve()
    canary_receipt_path = Path(
        receipt_path or (installed_bin / f"news-grasp-entry-canary-{nonce}.json")
    ).resolve()
    validator = task_origin_validator or task_context_validator
    if validator is None:
        validator = _cleanroom_default_task_origin_validator
    validator_context: dict[str, object] = {
        "bin_dir": installed_bin,
        "observed_at": observed,
        "schedule_id": _CLEANROOM_SCHEDULE_ID,
        "intent": _CLEANROOM_INTENT,
        "nonce": nonce,
        "generation": canary_generation,
        "receipt_path": canary_receipt_path,
        "canary_receipt_path": canary_receipt_path,
    }
    try:
        accepted = _invoke_cleanroom_canary_seam(validator, validator_context)
    except Exception as error:
        raise RuntimeError("NEWS_GRASP_TASK_ORIGIN_VALIDATION_FAILED") from error
    if accepted is not True:
        raise RuntimeError("NEWS_GRASP_TASK_ORIGIN_INVALID")

    canary_action = {
        "execute": str(_cleanroom_expected_pythonw(bin_dir=installed_bin) or ""),
        "launcher": str((installed_bin / "news-grasp-task-launcher.pyw").resolve()),
        "arguments": [
            "task-origin-canary",
            "--canary-nonce",
            nonce,
            "--canary-generation",
            canary_generation,
            "--canary-receipt-path",
            str(canary_receipt_path),
        ],
        "workingDirectory": str(
            Path(os.environ.get("USERPROFILE") or Path.home())
            / ".news-grasp-runtime"
            / "production-runtime"
        ),
    }
    canonical_action = {
        **canary_action,
        "arguments": [
            "dispatch",
            "--schedule-id",
            _CLEANROOM_SCHEDULE_ID,
            "--intent",
            _CLEANROOM_INTENT,
        ],
    }
    task_context = {
        "task_name": _CLEANROOM_CONTEXT_TASK_NAME,
        "task": _CLEANROOM_CONTEXT_TASK_NAME,
        "action": canary_action,
        "canary_action": canary_action,
        "restore_action": canonical_action,
        "nonce": nonce,
        "generation": canary_generation,
        "receipt_path": canary_receipt_path,
        "canary_receipt_path": canary_receipt_path,
    }
    changed = False
    receipt: Mapping[str, object] | None = None
    pipeline_receipt: Mapping[str, object] | None = None
    if not manage_task:
        canary_runtime = Path(
            runtime_root
            or (Path.home() / ".news-grasp-runtime" / "production-runtime")
        ).resolve()
        canary_manifest = _task_topology_manifest_path(canary_runtime)
        if manifest_path is not None and Path(manifest_path).resolve() != canary_manifest:
            raise RuntimeError("NEWS_GRASP_TASK_TOPOLOGY_AUTHORITY_INVALID")
        pipeline_receipt = _run_cleanroom_entry_canary_pipeline(
            nonce=nonce,
            generation=canary_generation,
            observed_at=observed,
            bin_dir=installed_bin,
            runtime_root=canary_runtime,
            manifest_path=canary_manifest,
            receipt_path=canary_receipt_path,
            controller_factory=controller_factory,
            child_runner=child_runner,
        )

    def _restore_once() -> None:
        nonlocal changed
        if not manage_task or not changed:
            return
        _invoke_cleanroom_canary_seam(
            restore_task,
            {
                **task_context,
                "action": canonical_action,
                "canonical_action": canonical_action,
                "restore_action": canonical_action,
            },
        )
        changed = False

    try:
        if manage_task:
            action_result = _invoke_cleanroom_canary_seam(task_action, task_context)
            if action_result is False:
                raise RuntimeError("NEWS_GRASP_CANARY_ACTION_REJECTED")
            changed = True
            start_result = _invoke_cleanroom_canary_seam(start_task, task_context)
            if start_result is False:
                raise RuntimeError("NEWS_GRASP_CANARY_START_REJECTED")
        if pipeline_receipt is not None:
            receipt = pipeline_receipt
        else:
            wait_context = {
                **task_context,
                "timeout_seconds": float(timeout_seconds),
            }
            waited = _invoke_cleanroom_canary_seam(wait_receipt, wait_context)
            if isinstance(waited, Mapping) and isinstance(waited.get("receipt"), Mapping):
                waited = waited["receipt"]
            if isinstance(waited, Mapping):
                receipt = waited
            elif receipt_path is not None:
                try:
                    loaded = json.loads(Path(receipt_path).read_text(encoding="utf-8"))
                except (OSError, ValueError, TypeError) as error:
                    raise RuntimeError("NEWS_GRASP_CANARY_RECEIPT_INVALID") from error
                if isinstance(loaded, Mapping):
                    receipt = loaded
        if receipt is None:
            raise RuntimeError("NEWS_GRASP_CANARY_RECEIPT_MISSING")
        if (
            receipt.get("nonce") != nonce
            or receipt.get("generation") != canary_generation
            or receipt.get("status") not in {"committed", "smoke_ok", "ok"}
        ):
            raise RuntimeError("NEWS_GRASP_CANARY_RECEIPT_BINDING_INVALID")
        _restore_once()
        if not _invoke_cleanroom_canary_seam(
            final_parity,
            {
                **task_context,
                "expected_action": canonical_action,
                "receipt": receipt,
            },
        ):
            raise RuntimeError("NEWS_GRASP_CANARY_FINAL_PARITY_INVALID")
        result = {
            "schemaVersion": "NEWS_GRASP_TASK_ORIGIN_CANARY_RESULT_V1",
            "status": "verified",
            "nonce": nonce,
            "generation": canary_generation,
            "receipt": dict(receipt),
            "taskOrigin": True,
            "isolatedState": True,
            "externalEffectCount": 0,
        }
        if receipt_path is not None:
            _write_json_atomic(Path(receipt_path), result)
        return result
    finally:
        # 失敗時も一度だけ復元し、復元前のsuccessを返さない。
        _restore_once()


def _run_cleanroom_entry_canary_pipeline(
    *,
    nonce: str,
    generation: str,
    observed_at: datetime,
    bin_dir: Path,
    runtime_root: Path,
    manifest_path: Path,
    receipt_path: Path,
    controller_factory=None,
    child_runner=None,
) -> dict[str, object]:
    """Task-origin process側のisolated Controller/WAL/ledger/SmokeTest経路。"""
    base_runtime = Path(runtime_root).resolve()
    canary_root = (base_runtime / "entry-canary" / generation / nonce).resolve()
    if not canary_root.is_relative_to(base_runtime) or canary_root == base_runtime:
        raise RuntimeError("NEWS_GRASP_CANARY_RUNTIME_ROOT_INVALID")
    authority = _load_stable_launcher_identity(bin_dir=bin_dir)
    canary_root.mkdir(parents=True, exist_ok=True)
    if controller_factory is None:
        try:
            import_root = base_runtime.parent if base_runtime.name == "production-runtime" else base_runtime
            controller_type, attestor_type = _cleanroom_runtime_imports(import_root)
            attestor = attestor_type()
            writer = attestor.bind()
            if not isinstance(writer, Mapping):
                raise RuntimeError("NEWS_GRASP_ENTRY_WRITER_INVALID")
            controller = controller_type(
                runtime_root=canary_root,
                manifest_path=manifest_path,
                writer_attestor=attestor,
            )
        except (ImportError, OSError, RuntimeError, TypeError, ValueError) as error:
            raise RuntimeError("NEWS_GRASP_CANARY_CONTROLLER_INVALID") from error
    else:
        controller = controller_factory(
            runtime_root=canary_root,
            manifest_path=manifest_path,
        )
        writer = _cleanroom_test_writer()
    reconcile = getattr(controller, "reconcile", None)
    if not callable(reconcile):
        raise RuntimeError("NEWS_GRASP_CANARY_CONTROLLER_INVALID")
    decision = reconcile(
        raw_argv=[
            "dispatch",
            "--schedule-id",
            _CLEANROOM_SCHEDULE_ID,
            "--intent",
            _CLEANROOM_INTENT,
        ],
        observed_at=observed_at,
        writer=writer,
        lease_seconds=_CLEANROOM_LEASE_SECONDS,
    )
    if not isinstance(decision, Mapping) or decision.get("ownerDisposition") != "ACQUIRED":
        raise RuntimeError("NEWS_GRASP_CANARY_SLOT_NOT_ACQUIRED")
    child_probe_path = (canary_root / "child-probe.txt").resolve()
    if not child_probe_path.is_relative_to(canary_root):
        raise RuntimeError("NEWS_GRASP_CANARY_CHILD_PROBE_PATH_INVALID")
    command, safety = _cleanroom_child_command(
        route="task-origin-child-probe",
        bin_dir=bin_dir,
        authority=authority,
        runtime_root=base_runtime,
        canary_generation=generation,
        canary_nonce=nonce,
    )
    if child_runner is None:
        child_exit = _run_cleanroom_child(
            "task-origin-child-probe",
            command,
            bin_dir=bin_dir,
            safety=safety,
        )
        if child_exit == 0:
            try:
                if (
                    child_probe_path.is_symlink()
                    or not child_probe_path.is_file()
                    or child_probe_path.stat().st_size > 64
                    or child_probe_path.read_text(encoding="utf-8") != "probe_ok"
                ):
                    child_exit = 66
            except OSError:
                child_exit = 66
    else:
        child_exit = int(
            child_runner(
                "task-origin-child-probe",
                command,
                **{
                    key: value
                    for key, value in safety.items()
                    if key not in {"route", "command"}
                },
            )
        )
    terminal_state = "SUCCEEDED" if child_exit == 0 else "FAILED"
    outcome = {
        "schemaVersion": "NEWS_GRASP_TASK_ORIGIN_CANARY_RECEIPT_V1",
        "nonce": nonce,
        "generation": generation,
        "slotKey": decision.get("slotKey"),
        "fenceToken": decision.get("fenceToken"),
        "childRoute": "task-origin-child-probe",
        "childExitCode": int(child_exit),
        "terminalState": terminal_state,
    }
    result_hash = _sha256_json(outcome)
    commit = getattr(controller, "commit_slot", None)
    if not callable(commit):
        raise RuntimeError("NEWS_GRASP_CANARY_COMMIT_INVALID")
    commit(
        slot_key=decision.get("slotKey"),
        writer=writer,
        fence_token=decision.get("fenceToken"),
        terminal_state=terminal_state,
        result_hash=result_hash,
        observed_at=observed_at,
    )
    receipt = {
        **outcome,
        "status": "committed" if child_exit == 0 else "failed",
        "ledgerGeneration": decision.get("generation"),
        "resultHash": result_hash,
        "externalEffectCount": 0,
    }
    if child_exit == 0 and terminal_state == "SUCCEEDED":
        _cleanup_cleanroom_entry_canary(
            canary_root=canary_root,
            runtime_root=base_runtime,
            generation=generation,
            nonce=nonce,
        )
    _write_json_atomic(receipt_path, receipt)
    return receipt


def _cleanroom_runtime_imports(runtime_root: Path):
    """production defaultだけ、検証済みproduction-runtimeをimport rootにする。"""
    production_runtime = (Path(runtime_root) / "production-runtime").resolve()
    if not production_runtime.is_dir() or production_runtime.is_symlink():
        raise RuntimeError("NEWS_GRASP_PRODUCTION_RUNTIME_MISSING")
    runtime_text = str(production_runtime)
    # 同じpathを重複挿入せず、必ず source repo より前に置く。
    sys.path[:] = [item for item in sys.path if str(item) != runtime_text]
    sys.path.insert(0, runtime_text)
    try:
        controller_module = __import__(
            "tools.news_grasp_cleanroom_controller",
            fromlist=["Controller"],
        )
        identity_module = __import__(
            "tools.news_grasp_entry_identity",
            fromlist=["SystemEntryWriterAttestor"],
        )
    except (ImportError, OSError, RuntimeError) as error:
        raise RuntimeError("NEWS_GRASP_CLEANROOM_RUNTIME_IMPORT_FAILED") from error
    controller_type = getattr(controller_module, "Controller", None)
    attestor_type = getattr(identity_module, "SystemEntryWriterAttestor", None)
    if not callable(controller_type) or not callable(attestor_type):
        raise RuntimeError("NEWS_GRASP_CLEANROOM_RUNTIME_IMPORT_FAILED")
    return controller_type, attestor_type


def _cleanroom_test_writer(decision: Mapping[str, object] | None = None) -> dict[str, object]:
    """注入controller用の副作用なしwriter envelope。"""
    value: dict[str, object] = {
        "writerId": "news-grasp-test-seam",
        "pid": int(os.getpid()),
    }
    if isinstance(decision, dict):
        for key in ("writerKey", "ownerKey"):
            candidate = decision.get(key)
            if isinstance(candidate, str) and candidate:
                value[key] = candidate
    return value


def _cleanroom_child_command(
    *,
    route: str,
    bin_dir: Path,
    authority: Mapping[str, object] | None,
    runtime_root: Path | None = None,
    canary_generation: str | None = None,
    canary_nonce: str | None = None,
) -> tuple[list[str], dict[str, object]]:
    """installed childのargvと観測可能な安全境界を作る。"""
    launcher = (Path(bin_dir) / "news-grasp-task-launcher.pyw").resolve()
    if route in {"runner", "deadman"}:
        executable = Path(sys.executable)
        if isinstance(authority, Mapping):
            action = authority.get("action")
            if isinstance(action, list) and action and isinstance(action[0], str) and action[0]:
                executable = Path(action[0])
        child_cwd = Path(bin_dir)
        try:
            child_cwd = resolve_bootstrap_launch_roots(
                bin_dir=Path(bin_dir),
                enforce_canonical_runtime=True,
            )["configuredRuntime"]
        except (OSError, RuntimeError, ValueError):
            # test seamではinstalled runtime configを必須にしない。実authorityが
            # 渡ったproduction dispatchはconfig driftをspawn前にfail-closedにする。
            if isinstance(authority, Mapping) and authority.get("schemaVersion") == STABLE_TASK_AUTHORITY_SCHEMA:
                raise
            child_cwd = Path(bin_dir)
        command = [
            str(executable),
            "-I",
            "-S",
            "-B",
            str(child_cwd / "tools" / "news_grasp_daily_launcher.py"),
        ]
        safety: dict[str, object] = {
            "route": route,
            "command": tuple(command),
            "cwd": str(child_cwd),
            "shell": False,
            "stdin": subprocess.DEVNULL,
            "creationflags": subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            "close_fds": True,
            "timeout": _CLEANROOM_CHILD_TIMEOUT_SECONDS,
            "environment": {
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUTF8": "1",
                "NEWS_GRASP_REPO_ROOT": str(child_cwd),
            },
            "owned_process_module": str(
                child_cwd / "tools" / "news_grasp_owned_process.py"
            ),
        }
        return command, safety
    if route == "task-origin-child-probe":
        executable = Path(sys.executable)
        if isinstance(authority, Mapping):
            action = authority.get("action")
            if isinstance(action, list) and action and isinstance(action[0], str) and action[0]:
                executable = Path(action[0])
        if runtime_root is None or canary_generation is None or canary_nonce is None:
            raise ValueError("NEWS_GRASP_CANARY_CHILD_PROBE_IDENTITY_MISSING")
        isolated_probe = _cleanroom_child_probe_path(
            generation=canary_generation,
            nonce=canary_nonce,
            runtime_root=runtime_root,
        )
        command = [
            str(executable),
            str(launcher),
            "task-origin-child-probe",
            "--canary-generation",
            canary_generation,
            "--canary-nonce",
            canary_nonce,
        ]
        return command, {
            "route": route,
            "command": tuple(command),
            "cwd": str(Path(bin_dir)),
            "shell": False,
            "stdin": subprocess.DEVNULL,
            "creationflags": subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            "close_fds": True,
            "timeout": _CLEANROOM_CHILD_TIMEOUT_SECONDS,
            "externalEffectCount": 0,
            "probePath": str(isolated_probe),
            "owned_process_module": str(
                Path(runtime_root) / "tools" / "news_grasp_owned_process.py"
            ),
        }
    raise ValueError("NEWS_GRASP_CLEANROOM_CHILD_ROUTE_INVALID")


def _cleanroom_child_probe_path(
    *,
    generation: str,
    nonce: str,
    runtime_root: Path,
) -> Path:
    """caller pathを受け取らず、canary identityから唯一のprobe pathを導出する。"""
    if re.fullmatch(r"[0-9a-f]{64}", generation) is None:
        raise RuntimeError("NEWS_GRASP_CLEANROOM_CHILD_PROBE_GENERATION_INVALID")
    if re.fullmatch(r"[0-9a-f]{32}", nonce) is None:
        raise RuntimeError("NEWS_GRASP_CLEANROOM_CHILD_PROBE_NONCE_INVALID")
    boundary = Path(os.path.abspath(os.fspath(runtime_root)))
    return boundary / "entry-canary" / generation / nonce / "child-probe.txt"


def _write_cleanroom_child_probe(
    *,
    generation: str,
    nonce: str,
    runtime_root: Path | None = None,
) -> Path:
    """既存のcanonical canary directoryにprobeをexclusive/no-reparseで一度だけ書く。"""
    boundary = Path(
        os.path.abspath(
            os.fspath(
                runtime_root
                or (Path.home() / ".news-grasp-runtime" / "production-runtime")
            )
        )
    )
    probe_path = _cleanroom_child_probe_path(
        generation=generation,
        nonce=nonce,
        runtime_root=boundary,
    )
    code = "NEWS_GRASP_CLEANROOM_CHILD_PROBE_MANAGED_PATH_INVALID"
    _assert_managed_path(probe_path, boundary, code)
    probe_parent = probe_path.parent
    if not probe_parent.is_dir() or probe_parent.is_symlink():
        raise RuntimeError(code)
    payload = b"probe_ok"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= int(getattr(os, "O_BINARY", 0))
    flags |= int(getattr(os, "O_NOFOLLOW", 0))
    with _managed_directory_handle(probe_parent, boundary, code):
        _assert_managed_path(probe_path, boundary, code)
        if probe_path.exists() or probe_path.is_symlink():
            raise RuntimeError("NEWS_GRASP_CLEANROOM_CHILD_PROBE_EXISTS")
        descriptor = os.open(os.fspath(probe_path), flags, 0o600)
        try:
            information = os.fstat(descriptor)
            if not stat.S_ISREG(information.st_mode) or information.st_size != 0:
                raise RuntimeError(code)
            offset = 0
            while offset < len(payload):
                written = os.write(descriptor, payload[offset:])
                if written <= 0:
                    raise RuntimeError("NEWS_GRASP_CLEANROOM_CHILD_PROBE_WRITE_FAILED")
                offset += written
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    if probe_path.stat().st_size != len(payload):
        raise RuntimeError("NEWS_GRASP_CLEANROOM_CHILD_PROBE_WRITE_FAILED")
    return probe_path


def _cleanup_cleanroom_entry_canary(
    *,
    canary_root: Path,
    runtime_root: Path,
    generation: str,
    nonce: str,
) -> None:
    """Green receipt確定前にexact nonce subtreeだけをatomic隔離して回収する。"""
    boundary = Path(os.path.abspath(os.fspath(runtime_root)))
    expected = _cleanroom_child_probe_path(
        generation=generation,
        nonce=nonce,
        runtime_root=boundary,
    ).parent
    candidate = Path(os.path.abspath(os.fspath(canary_root)))
    code = "NEWS_GRASP_CLEANROOM_CANARY_CLEANUP_PATH_INVALID"
    if candidate != expected or candidate.parent.parent != boundary / "entry-canary":
        raise RuntimeError(code)
    _assert_managed_path(candidate, boundary, code)
    if not candidate.is_dir() or candidate.is_symlink():
        raise RuntimeError(code)
    quarantine = candidate.with_name(f".cleanup-{nonce}-{uuid4().hex}")
    with _managed_directory_handle(candidate.parent, boundary, code):
        _assert_managed_path(candidate, boundary, code)
        if quarantine.exists() or quarantine.is_symlink():
            raise RuntimeError(code)
        os.replace(candidate, quarantine)
        _assert_managed_path(quarantine, boundary, code)
        _remove_runtime_path(quarantine)
    for empty_parent in (candidate.parent, candidate.parent.parent):
        try:
            _assert_managed_path(empty_parent, boundary, code)
            empty_parent.rmdir()
        except OSError:
            break


def _run_cleanroom_child(
    route: str,
    command: list[str],
    *,
    bin_dir: Path,
    safety: Mapping[str, object],
    renew_slot=None,
    renewal_interval_seconds: float | None = None,
    renewal_clock=None,
    renewal_sleep=None,
) -> int:
    """installed childをno-console/bounded/captured境界で起動する。"""
    timeout_seconds = safety.get("timeout", _CLEANROOM_CHILD_TIMEOUT_SECONDS)
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
        raise RuntimeError("NEWS_GRASP_CHILD_TIMEOUT_INVALID")
    creationflags = int(safety.get("creationflags") or 0)
    child_cwd = Path(str(safety.get("cwd") or bin_dir)).resolve(strict=True)
    child_env = dict(os.environ)
    for inherited_name in (
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONSTARTUP",
        "PYTHONINSPECT",
        "PYTHONUSERBASE",
    ):
        child_env.pop(inherited_name, None)
    child_env["PYTHONNOUSERSITE"] = "1"
    environment = safety.get("environment")
    if environment is not None:
        if not isinstance(environment, Mapping) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in environment.items()
        ):
            raise RuntimeError("NEWS_GRASP_CHILD_ENVIRONMENT_INVALID")
        if any(
            key.upper()
            in {
                "PYTHONPATH",
                "PYTHONHOME",
                "PYTHONSTARTUP",
                "PYTHONINSPECT",
                "PYTHONUSERBASE",
            }
            for key in environment
        ):
            raise RuntimeError("NEWS_GRASP_CHILD_ENVIRONMENT_INVALID")
        child_env.update(environment)
    if renew_slot is not None and renewal_interval_seconds is None:
        renewal_interval_seconds = max(1.0, min(300.0, float(_CLEANROOM_LEASE_SECONDS) / 3.0))
    if renew_slot is not None and (
        isinstance(renewal_interval_seconds, bool)
        or not isinstance(renewal_interval_seconds, (int, float))
        or renewal_interval_seconds <= 0
    ):
        raise RuntimeError("NEWS_GRASP_RENEWAL_INTERVAL_INVALID")
    try:
        module_path_raw = safety.get("owned_process_module")
        if isinstance(module_path_raw, str) and module_path_raw:
            module_path = Path(module_path_raw).resolve(strict=True)
        else:
            module_path = (child_cwd / "tools" / "news_grasp_owned_process.py").resolve(strict=True)
        owned_module = _load_module_from_exact_path(
            module_path,
            prefix="news_grasp_owned_process_runtime",
        )
        owned_runner = getattr(owned_module, "run_owned_bounded", None)
    except (ImportError, OSError, RuntimeError) as error:
        raise RuntimeError("NEWS_GRASP_OWNED_PROCESS_RUNTIME_IMPORT_FAILED") from error
    if not callable(owned_runner):
        raise RuntimeError("NEWS_GRASP_OWNED_PROCESS_RUNTIME_IMPORT_FAILED")

    def _renew_owned_child() -> object:
        try:
            renewal = renew_slot()
        except Exception as error:
            raise RuntimeError("NEWS_GRASP_SLOT_RENEWAL_FAILED") from error
        if renewal is False or (
            isinstance(renewal, Mapping)
            and renewal.get("status") not in {None, "renewed"}
        ):
            raise RuntimeError("NEWS_GRASP_SLOT_RENEWAL_REJECTED")
        return renewal

    result = owned_runner(
        command,
        cwd=child_cwd,
        env=child_env,
        timeout=float(timeout_seconds),
        max_output_bytes=_CLEANROOM_CHILD_MAX_OUTPUT_BYTES,
        heartbeat=_renew_owned_child if renew_slot is not None else None,
        heartbeat_interval_seconds=(
            float(renewal_interval_seconds) if renew_slot is not None else None
        ),
    )
    if bool(getattr(result, "timed_out", False)):
        raise RuntimeError("NEWS_GRASP_CHILD_TIMEOUT")
    stdout = getattr(result, "stdout", b"")
    stderr = getattr(result, "stderr", b"")
    if isinstance(stdout, (bytes, bytearray)) and len(stdout) > _CLEANROOM_CHILD_MAX_OUTPUT_BYTES:
        raise RuntimeError("NEWS_GRASP_CHILD_STDOUT_LIMIT")
    if isinstance(stderr, (bytes, bytearray)) and len(stderr) > _CLEANROOM_CHILD_MAX_OUTPUT_BYTES:
        raise RuntimeError("NEWS_GRASP_CHILD_STDERR_LIMIT")
    if bool(getattr(result, "output_exceeded", False)):
        raise RuntimeError("NEWS_GRASP_CHILD_OUTPUT_LIMIT")
    return int(result.returncode)


def run_cleanroom_dispatch(
    schedule_id: str,
    intent: str,
    *,
    bin_dir: Path,
    observed_at: datetime | str,
    controller_factory=None,
    child_runner=None,
    task_context_validator=None,
    renewal_interval_seconds: float | None = None,
    renewal_clock=None,
    renewal_sleep=None,
) -> int:
    """production Taskの唯一のclean-room dispatch入口。"""
    # caller入力は controller生成、writer bind、filesystem、child起動より前に
    # 完全一致で拒否する。
    if schedule_id != _CLEANROOM_SCHEDULE_ID:
        raise ValueError("NEWS_GRASP_ENTRY_UNKNOWN_SCHEDULE")
    if intent != _CLEANROOM_INTENT:
        raise ValueError("NEWS_GRASP_ENTRY_UNKNOWN_INTENT")
    observed = _normalize_cleanroom_observed_at(observed_at)
    bin_path = Path(bin_dir).resolve()
    if task_context_validator is None:
        task_context_validator = _cleanroom_default_task_context_validator
    try:
        _run_cleanroom_task_context_validator(
            task_context_validator,
            bin_dir=bin_path,
            observed_at=observed,
            schedule_id=schedule_id,
            intent=intent,
        )
    except RuntimeError as error:
        if str(error) in {
            "NEWS_GRASP_TASK_CONTEXT_VALIDATOR_INVALID",
            "NEWS_GRASP_TASK_CONTEXT_VALIDATION_FAILED",
            "NEWS_GRASP_TASK_CONTEXT_INVALID",
        }:
            return NEWS_GRASP_TASK_CONTEXT_REJECTED_EXIT
        raise
    try:
        authority: dict[str, object] = _load_stable_launcher_identity(bin_dir=bin_path)
    except (OSError, RuntimeError, ValueError, TypeError) as error:
        # Task contextのread-only観測後、controller/WAL/ledger/childより前に
        # stable authorityを確定する。欠落・不正は副作用なしでfail-closed。
        return 66
    runtime_root = Path.home() / ".news-grasp-runtime"
    manifest_path = _task_topology_manifest_path(runtime_root / "production-runtime")
    raw_argv = [
        "dispatch",
        "--schedule-id",
        schedule_id,
        "--intent",
        intent,
    ]
    injected = controller_factory is not None
    if controller_factory is None:
        controller_type, attestor_type = _cleanroom_runtime_imports(runtime_root)
        attestor = attestor_type()
        writer = attestor.bind()
        if not isinstance(writer, Mapping):
            raise RuntimeError("NEWS_GRASP_ENTRY_WRITER_INVALID")
        controller = controller_type(
            runtime_root=runtime_root,
            manifest_path=manifest_path,
            writer_attestor=attestor,
        )
    else:
        controller = controller_factory(
            runtime_root=runtime_root,
            manifest_path=manifest_path,
        )
        writer = _cleanroom_test_writer()

    reconcile = getattr(controller, "reconcile", None)
    if not callable(reconcile):
        reconcile = getattr(controller, "acquire", None)
    if not callable(reconcile):
        raise RuntimeError("NEWS_GRASP_CLEANROOM_CONTROLLER_INVALID")
    decision = reconcile(
        raw_argv=raw_argv,
        observed_at=observed,
        writer=writer,
        lease_seconds=_CLEANROOM_LEASE_SECONDS,
    )
    if not isinstance(decision, Mapping):
        raise RuntimeError("NEWS_GRASP_CLEANROOM_DECISION_INVALID")
    disposition = decision.get("ownerDisposition")
    if not isinstance(disposition, str) or not disposition:
        disposition = decision.get("status")
    slot_kind = decision.get("slotKind")
    if disposition != "ACQUIRED" or slot_kind not in {"Scheduled", "Audit"}:
        return 0
    route = "runner" if slot_kind == "Scheduled" else "deadman"
    command, safety = _cleanroom_child_command(
        route=route,
        bin_dir=bin_path,
        authority=authority,
    )
    child_exit = 1
    child_error = ""
    renew = getattr(controller, "renew_slot", None)
    if child_runner is None and not callable(renew):
        raise RuntimeError("NEWS_GRASP_SLOT_RENEWAL_REQUIRED")
    renewal_callback = None
    if callable(renew):
        def _renew_slot() -> Mapping[str, object]:
            renewal_observed = renewal_clock() if callable(renewal_clock) else datetime.now(_CLEANROOM_TOKYO)
            renewal_time = _normalize_cleanroom_observed_at(renewal_observed)
            value = renew(
                slot_key=decision.get("slotKey"),
                writer=writer,
                fence_token=decision.get("fenceToken"),
                lease_seconds=_CLEANROOM_LEASE_SECONDS,
                observed_at=renewal_time,
            )
            if not isinstance(value, Mapping) or value.get("status") != "renewed":
                raise RuntimeError("NEWS_GRASP_SLOT_RENEWAL_REJECTED")
            return value

        renewal_callback = _renew_slot
    try:
        if child_runner is None:
            child_exit = _run_cleanroom_child(
                route,
                command,
                bin_dir=bin_path,
                safety=safety,
                renew_slot=renewal_callback,
                renewal_interval_seconds=renewal_interval_seconds,
                renewal_clock=renewal_clock,
                renewal_sleep=renewal_sleep,
            )
        else:
            # seamでもroute/argv/安全属性をrepr可能な形で渡し、productionと
            # 同じ一回限りのchild境界を観測できるようにする。
            child_exit = int(
                child_runner(
                    route,
                    command,
                    **{
                        key: value
                        for key, value in safety.items()
                        if key not in {"route", "command"}
                    },
                    renew_slot=renewal_callback,
                    renewal_interval_seconds=renewal_interval_seconds,
                )
            )
    except Exception as error:
        child_exit = 1
        child_error = type(error).__name__
    terminal_state = "SUCCEEDED" if child_exit == 0 else "FAILED"
    child_outcome: dict[str, object] = {
        "route": route,
        "slotKey": decision.get("slotKey"),
        "terminalState": terminal_state,
        "exitCode": int(child_exit),
    }
    if child_error:
        child_outcome["errorType"] = child_error
    result_hash = hashlib.sha256(
        json.dumps(
            child_outcome,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    commit = getattr(controller, "commit_slot", None)
    if not callable(commit):
        commit = getattr(controller, "commit", None)
    if not callable(commit):
        raise RuntimeError("NEWS_GRASP_CLEANROOM_COMMIT_INVALID")
    commit_kwargs: dict[str, object] = {
        "slot_key": decision.get("slotKey"),
        "writer": writer,
        "fence_token": decision.get("fenceToken"),
        "terminal_state": terminal_state,
        "result_hash": result_hash,
        "observed_at": observed,
    }
    if injected:
        # fake commit oracleがownerKeyを直接観測できるようにする。production
        # Controllerには正式signatureだけを渡す。
        owner_key = decision.get("ownerKey", decision.get("writerKey"))
        if owner_key is not None:
            commit_kwargs["ownerKey"] = owner_key
    commit(**commit_kwargs)
    return int(child_exit)


def _validate_active_production_generation(
    *, runtime_repo: Path, launcher_identity: dict[str, object]
) -> dict[str, object]:
    """active pointer・immutable manifest・runtime bytesを同一generationへ束縛する。"""
    active_pointer_path = runtime_repo.parent / "active-generation-v2.json"
    try:
        if (
            active_pointer_path.is_symlink()
            or not active_pointer_path.is_file()
            or active_pointer_path.stat().st_size > 64 * 1024
        ):
            raise RuntimeError("NEWS_GRASP_ACTIVE_GENERATION_INVALID")
        active_pointer = json.loads(active_pointer_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as error:
        raise RuntimeError("NEWS_GRASP_ACTIVE_GENERATION_INVALID") from error
    if not isinstance(active_pointer, dict):
        raise RuntimeError("NEWS_GRASP_ACTIVE_GENERATION_INVALID")
    active_unsigned = dict(active_pointer) if isinstance(active_pointer, dict) else {}
    active_sha256 = str(active_unsigned.pop("pointerSha256", ""))
    runtime_head = _run_git(runtime_repo, "rev-parse", "HEAD").strip().lower()
    if (
        active_pointer.get("schemaVersion") != "NEWS_GRASP_ACTIVE_GENERATION_V2"
        or active_sha256 != _sha256_json(active_unsigned)
        or active_pointer.get("stableTaskAuthoritySha256")
        != launcher_identity.get("authoritySha256")
    ):
        raise RuntimeError("NEWS_GRASP_ACTIVE_GENERATION_DRIFT")
    try:
        manifest_path = Path(str(active_pointer.get("manifestPath") or "")).resolve(strict=True)
        expected_generation_root = (runtime_repo.parent / "generations").resolve(strict=True)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as error:
        raise RuntimeError("NEWS_GRASP_ACTIVE_GENERATION_INVALID") from error
    if not manifest_path.is_relative_to(expected_generation_root) or not isinstance(manifest, dict):
        raise RuntimeError("NEWS_GRASP_ACTIVE_GENERATION_INVALID")
    manifest_unsigned = dict(manifest)
    manifest_sha256 = str(manifest_unsigned.pop("manifestSha256", ""))
    source_manifest = manifest.get("source")
    source_tracked_files = (
        source_manifest.get("trackedFiles") if isinstance(source_manifest, dict) else None
    )
    runtime_manifest = manifest.get("runtime")
    tracked_files = runtime_manifest.get("trackedFiles") if isinstance(runtime_manifest, dict) else None
    if (
        manifest.get("schemaVersion") != "PRODUCTION_GENERATION_MANIFEST_V2"
        or manifest.get("generationId") != active_pointer.get("generationId")
        or manifest_sha256 != _sha256_json(manifest_unsigned)
        or active_pointer.get("manifestSha256") != manifest_sha256
        or manifest.get("stableTaskAuthoritySha256") != launcher_identity.get("authoritySha256")
        or not isinstance(source_manifest, dict)
        or source_manifest.get("commit") != runtime_head
        or not isinstance(source_tracked_files, dict)
        or source_manifest.get("trackedManifestSha256")
        != _sha256_json(source_tracked_files)
        or _git_tracked_tree_manifest(runtime_repo, runtime_head)
        != source_tracked_files
        or not isinstance(runtime_manifest, dict)
        or runtime_manifest.get("commit") != runtime_head
        or not isinstance(tracked_files, dict)
    ):
        raise RuntimeError("NEWS_GRASP_ACTIVE_GENERATION_DRIFT")
    for relative, expected_sha256 in tracked_files.items():
        candidate = (runtime_repo / str(relative)).resolve(strict=True)
        if not candidate.is_relative_to(runtime_repo) or _file_sha256(candidate) != expected_sha256:
            raise RuntimeError("NEWS_GRASP_ACTIVE_GENERATION_DRIFT")
    return active_pointer


def _cleanroom_bootstrap_task_origin_witness(
    *,
    bin_dir: Path,
    observed_at: datetime,
    high_cost_binding_path: Path,
    high_cost_binding_sha256: str,
) -> dict[str, object] | None:
    """Bootstrap Taskの実起動を親chainとcanonical actionでread-only証明する。"""
    if (
        not isinstance(high_cost_binding_sha256, str)
        or re.fullmatch(r"[0-9a-fA-F]{64}", high_cost_binding_sha256) is None
    ):
        return None
    try:
        binding_path = high_cost_binding_path.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        completed = subprocess.run(
            _cleanroom_context_powershell_command(
                launcher_pid=os.getpid(),
                task_name=_CLEANROOM_BOOTSTRAP_TASK_NAME,
            ),
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=_CLEANROOM_CONTEXT_TIMEOUT_SECONDS,
            creationflags=creationflags,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if int(getattr(completed, "returncode", 1)) != 0:
        return None
    payload = _cleanroom_decode_context_json(getattr(completed, "stdout", b""))
    if payload is None or not _cleanroom_validate_process_witness(payload):
        return None
    if (
        payload.get("taskName") != _CLEANROOM_BOOTSTRAP_TASK_NAME
        or payload.get("enabled") is not True
        or payload.get("state") != "Running"
        or payload.get("taskPath") != "\\"
        or payload.get("multipleInstancesPolicy") != "IgnoreNew"
    ):
        return None
    try:
        last_run_text = payload.get("lastRunTime")
        if not isinstance(last_run_text, str) or not last_run_text:
            return None
        last_run = datetime.fromisoformat(last_run_text.replace("Z", "+00:00"))
        if last_run.tzinfo is None or last_run.utcoffset() is None:
            return None
        age_seconds = (observed_at - last_run.astimezone(_CLEANROOM_TOKYO)).total_seconds()
        if age_seconds < -60 or age_seconds > 10 * 60:
            return None
    except (TypeError, ValueError, OverflowError):
        return None
    actions = payload.get("actions")
    if type(actions) is not list or len(actions) != 1 or type(actions[0]) is not dict:
        return None
    action = actions[0]
    if set(action) != {"execute", "arguments", "workingDirectory"}:
        return None
    expected_pythonw = _cleanroom_expected_pythonw(bin_dir=bin_dir)
    launcher_path = (bin_dir / "news-grasp-task-launcher.pyw").resolve()
    arguments = _cleanroom_windows_argument_tokens(action.get("arguments"))
    if (
        expected_pythonw is None
        or _cleanroom_context_path(action.get("execute")) != expected_pythonw
        or Path(str(action.get("execute") or "")).name.casefold()
        not in {"pythonw.exe", "pythonw"}
        or _cleanroom_context_path(str(launcher_path))
        != _cleanroom_context_path(str(Path(__file__).resolve()))
        or arguments is None
        or len(arguments) != 11
        or arguments[:3] != ["-I", "-S", "-B"]
        or _cleanroom_context_path(arguments[3])
        != _cleanroom_context_path(str(launcher_path))
        or arguments[4:8]
        != [
            "bootstrap",
            "--scheduled-task-name",
            _CLEANROOM_BOOTSTRAP_TASK_NAME,
            "--high-cost-binding-path",
        ]
        or _cleanroom_context_path(arguments[8])
        != _cleanroom_context_path(str(binding_path))
        or arguments[9:] != [
            "--high-cost-binding-sha256",
            high_cost_binding_sha256.lower(),
        ]
        or _cleanroom_context_path(action.get("workingDirectory"))
        != _cleanroom_context_path(str(bin_dir.resolve()))
    ):
        return None
    triggers = payload.get("triggers")
    if type(triggers) is not list or len(triggers) != 1:
        return None
    trigger = triggers[0]
    if type(trigger) is not dict or trigger.get("enabled") is not True:
        return None
    if "daily" not in str(trigger.get("kind") or "").casefold():
        return None
    boundary = trigger.get("startBoundary")
    if not isinstance(boundary, str) or not boundary:
        return None
    try:
        if datetime.fromisoformat(boundary.replace("Z", "+00:00")).strftime("%H:%M:%S") != "05:55:00":
            return None
    except (TypeError, ValueError):
        return None
    return {
        "status": "accepted",
        "taskName": _CLEANROOM_BOOTSTRAP_TASK_NAME,
        "parentProcessId": payload["parentProcessId"],
        "parentProcessPath": payload["parentProcessPath"],
        "scheduleServicePid": payload["scheduleServicePid"],
        "ancestorChainDepth": len(payload["ancestorChain"]),
        "observedAt": observed_at.isoformat(timespec="milliseconds"),
    }


def _write_bootstrap_execution_receipt(
    *,
    bin_dir: Path,
    launcher_identity: dict[str, object],
    observed_at: datetime,
    task_origin_witness: dict[str, object],
    child_exit_code: int,
) -> Path:
    """検証済みactive generationとTask-originをatomic receiptへ封印する。"""
    if child_exit_code != 0 or task_origin_witness.get("status") != "accepted":
        raise RuntimeError("NEWS_GRASP_BOOTSTRAP_EXECUTION_RECEIPT_NOT_ELIGIBLE")
    runtime_repo = Path.home() / ".news-grasp-runtime" / "production-runtime"
    active_pointer = _validate_active_production_generation(
        runtime_repo=runtime_repo,
        launcher_identity=launcher_identity,
    )
    generation_id = active_pointer.get("generationId")
    manifest_sha256 = str(active_pointer.get("manifestSha256") or "").lower()
    authority_sha256 = str(launcher_identity.get("authoritySha256") or "").lower()
    if (
        not isinstance(generation_id, str)
        or not generation_id
        or re.fullmatch(r"[0-9a-f]{64}", manifest_sha256) is None
        or re.fullmatch(r"[0-9a-f]{64}", authority_sha256) is None
        or active_pointer.get("stableTaskAuthoritySha256") != authority_sha256
    ):
        raise RuntimeError("NEWS_GRASP_BOOTSTRAP_EXECUTION_RECEIPT_INVALID")
    receipt_path = (bin_dir / "news-grasp-bootstrap-execution-receipt-v1.json").resolve()
    _assert_managed_path(
        receipt_path,
        bin_dir.resolve(),
        "NEWS_GRASP_BOOTSTRAP_EXECUTION_RECEIPT_PATH_INVALID",
    )
    receipt = {
        "schemaVersion": "NEWS_GRASP_BOOTSTRAP_EXECUTION_RECEIPT_V1",
        "issueDate": observed_at.date().isoformat(),
        "observedAt": observed_at.isoformat(timespec="milliseconds"),
        "generationId": generation_id,
        "manifestSha256": manifest_sha256,
        "stableAuthoritySha": authority_sha256,
        "taskName": _CLEANROOM_BOOTSTRAP_TASK_NAME,
        "taskOriginWitnessStatus": str(task_origin_witness["status"]),
        "taskOriginWitness": dict(task_origin_witness),
        "childExitCode": int(child_exit_code),
    }
    if len(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ) > 64 * 1024:
        raise RuntimeError("NEWS_GRASP_BOOTSTRAP_EXECUTION_RECEIPT_SIZE_INVALID")
    _write_json_atomic(receipt_path, receipt)
    if receipt_path.stat().st_size > 64 * 1024:
        raise RuntimeError("NEWS_GRASP_BOOTSTRAP_EXECUTION_RECEIPT_SIZE_INVALID")
    return receipt_path


def _run_installed_nopublish_authority(
    *, authority_path: Path, bin_dir: Path, launcher_identity: dict[str, object]
) -> int:
    """sealed authorityのexact PowerShell commandだけをinstalled launcherから起動する。"""
    try:
        if authority_path.stat().st_size > 64 * 1024:
            raise ValueError("oversized")
        value = json.loads(authority_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError) as error:
        raise RuntimeError("NEWS_GRASP_INSTALLED_NOPUBLISH_AUTHORITY_INVALID") from error
    if not isinstance(value, dict):
        raise RuntimeError("NEWS_GRASP_INSTALLED_NOPUBLISH_AUTHORITY_INVALID")
    unsigned = dict(value)
    authority_sha256 = str(unsigned.pop("authoritySha256", ""))
    if authority_sha256 not in {
        _sha256_json(unsigned),
        _sha256_json_insertion_order(unsigned),
    }:
        raise RuntimeError("NEWS_GRASP_INSTALLED_NOPUBLISH_AUTHORITY_INVALID")
    required = {
        "schemaVersion",
        "issueDate",
        "attemptId",
        "stableLauncherPath",
        "stableLauncherSha256",
        "stableTaskAuthorityPath",
        "stableTaskAuthorityFileSha256",
        "runnerExecutablePath",
        "runnerExecutableSha256",
        "pythonExecutableSha256",
        "executionRepoRoot",
        "executionRepoCommit",
        "runtimeRepoCommit",
        "runnerArgumentsPath",
        "runnerArgumentsFileSha256",
        "externalHealthAuthorityFixturePath",
        "externalHealthAuthorityFixtureSha256",
        "isolationReceiptPath",
        "isolationReceiptSha256",
        "launchEvidencePath",
        "releaseReflectionReceiptPath",
        "releaseReflectionReceiptSha256",
        "releaseReflectionImpactClass",
        "authoritySha256",
    }
    external_fields = {
        "externalHealthAuthorityFixturePath",
        "externalHealthAuthorityFixtureSha256",
    }
    if not external_fields.issubset(value):
        raise RuntimeError("NEWS_GRASP_INSTALLED_NOPUBLISH_EXTERNAL_AUTHORITY_INVALID")
    allowed_authority_keys = required | GLOBAL_GENERATION_AUTHORITY_FIELDS | E2E_ATTEMPT_AUTHORITY_FIELDS
    if (
        set(value) not in [
            required,
            required | GLOBAL_GENERATION_AUTHORITY_FIELDS,
            required | E2E_ATTEMPT_AUTHORITY_FIELDS,
            required | GLOBAL_GENERATION_AUTHORITY_FIELDS | E2E_ATTEMPT_AUTHORITY_FIELDS,
        ]
        or not set(value).issubset(allowed_authority_keys)
        or value.get("schemaVersion") != INSTALLED_NOPUBLISH_AUTHORITY_SCHEMA
    ):
        raise RuntimeError("NEWS_GRASP_INSTALLED_NOPUBLISH_AUTHORITY_INVALID")
    if (
        not isinstance(value["externalHealthAuthorityFixturePath"], str)
        or not value["externalHealthAuthorityFixturePath"]
        or not isinstance(value["externalHealthAuthorityFixtureSha256"], str)
        or not re.fullmatch(
            r"[0-9a-f]{64}", value["externalHealthAuthorityFixtureSha256"]
        )
    ):
        raise RuntimeError("NEWS_GRASP_INSTALLED_NOPUBLISH_EXTERNAL_AUTHORITY_INVALID")
    expected_identity = {
        "stableLauncherPath": str(Path(__file__).resolve()),
        "stableLauncherSha256": _file_sha256(Path(__file__).resolve()),
        "stableTaskAuthorityPath": str(launcher_identity["authorityPath"]),
        "stableTaskAuthorityFileSha256": str(launcher_identity["authorityFileSha256"]),
    }
    if any(value.get(field) != expected for field, expected in expected_identity.items()):
        raise RuntimeError("NEWS_GRASP_INSTALLED_LAUNCHER_IDENTITY_DRIFT")
    try:
        executable = Path(str(value["runnerExecutablePath"])).resolve(strict=True)
        arguments_path = Path(str(value["runnerArgumentsPath"])).resolve(strict=True)
        execution_repo = Path(str(value["executionRepoRoot"])).resolve(strict=True)
    except OSError as error:
        raise RuntimeError("NEWS_GRASP_INSTALLED_NOPUBLISH_AUTHORITY_INVALID") from error
    if (
        not executable.is_file()
        or executable.is_symlink()
        or executable != _CANONICAL_POWERSHELL.resolve(strict=True)
        or not arguments_path.is_file()
        or arguments_path.is_symlink()
        or _file_sha256(executable) != value["runnerExecutableSha256"]
        or _file_sha256(arguments_path) != value["runnerArgumentsFileSha256"]
    ):
        raise RuntimeError("NEWS_GRASP_INSTALLED_NOPUBLISH_IDENTITY_DRIFT")
    try:
        arguments = json.loads(arguments_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as error:
        raise RuntimeError("NEWS_GRASP_INSTALLED_NOPUBLISH_ARGUMENTS_INVALID") from error
    prefix = [
        "-NoProfile",
        "-NonInteractive",
        "-WindowStyle",
        "Hidden",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
    ]
    mandatory_pair_flags = [
        "-DateStampOverride",
        "-RepoDirOverride",
        "-CodexWrapperOverride",
        "-StateFileOverride",
        "-LogDirOverride",
        "-PyExeOverride",
        "-PowerShellExe",
        "-HighCostBindingPath",
        "-HighCostBindingReceiptSha256",
        "-HighCostParentAuthorityPath",
        "-E2EFinalAdmissionPath",
        "-E2EFinalRunnerArgumentsPath",
        "-E2EFinalReservationReceiptPath",
        "-E2EFinalClaimReceiptPath",
        "-ExternalHealthAuthorityPathOverride",
        "-ExternalHealthAuthorityExpectedSha256",
        "-IsolationReceiptPath",
        "-LaunchEvidencePath",
        "-HighCostAttemptId",
        E2E_ATTEMPT_POLICY_ARGUMENT,
        E2E_LOGICAL_ATTEMPT_ARGUMENT,
    ]
    if not isinstance(arguments, list) or any(
        not isinstance(item, str) or not item for item in arguments
    ):
        raise RuntimeError("NEWS_GRASP_INSTALLED_NOPUBLISH_ARGUMENTS_INVALID")
    expected_prefix = [*prefix, str((execution_repo / "scripts" / "ops" / "news-grasp-release-nopublish.ps1").resolve()), "-NoPublish"]
    remainder = arguments[len(expected_prefix):]
    if arguments[: len(expected_prefix)] != expected_prefix or len(remainder) % 2:
        raise RuntimeError("NEWS_GRASP_INSTALLED_NOPUBLISH_ARGUMENTS_INVALID")
    observed_pair_flags = remainder[::2]
    allowed_pair_flags = [*mandatory_pair_flags, GLOBAL_GENERATION_ARGUMENT]
    if (
        len(observed_pair_flags) != len(set(observed_pair_flags))
        or any(flag not in allowed_pair_flags for flag in observed_pair_flags)
        or any(flag not in observed_pair_flags for flag in mandatory_pair_flags)
        or observed_pair_flags
        != [
            *mandatory_pair_flags,
            *([GLOBAL_GENERATION_ARGUMENT] if GLOBAL_GENERATION_ARGUMENT in observed_pair_flags else []),
        ]
    ):
        raise RuntimeError("NEWS_GRASP_INSTALLED_NOPUBLISH_ARGUMENTS_INVALID")
    argument_values = dict(zip(observed_pair_flags, remainder[1::2], strict=True))
    if (
        argument_values["-DateStampOverride"] != value["issueDate"]
        or argument_values["-RepoDirOverride"] != str(execution_repo)
        or argument_values["-E2EFinalRunnerArgumentsPath"] != str(arguments_path)
        or argument_values["-HighCostAttemptId"] != value["attemptId"]
        or argument_values["-PowerShellExe"] != str(executable)
    ):
        raise RuntimeError("NEWS_GRASP_INSTALLED_NOPUBLISH_ARGUMENTS_INVALID")
    global_argument_count = arguments.count(GLOBAL_GENERATION_ARGUMENT)
    if global_argument_count > 1:
        raise RuntimeError("NEWS_GRASP_GLOBAL_GENERATION_ARGUMENTS_INVALID")
    if global_argument_count == 0:
        if GLOBAL_GENERATION_AUTHORITY_FIELDS.intersection(value):
            raise RuntimeError("NEWS_GRASP_GLOBAL_GENERATION_BINDING_REQUIRED")
        global_manifest = None
    else:
        if not GLOBAL_GENERATION_AUTHORITY_FIELDS.issubset(value):
            raise RuntimeError("NEWS_GRASP_GLOBAL_GENERATION_BINDING_REQUIRED")
        try:
            global_index = arguments.index(GLOBAL_GENERATION_ARGUMENT)
            observed_global_manifest_path = _assert_managed_path(
                Path(arguments[global_index + 1]),
                execution_repo,
                "NEWS_GRASP_GLOBAL_GENERATION_MANIFEST_INVALID",
            ).resolve(strict=True)
        except (ValueError, IndexError, OSError) as error:
            raise RuntimeError("NEWS_GRASP_GLOBAL_GENERATION_ARGUMENTS_INVALID") from error
        if (
            str(observed_global_manifest_path)
            != str(Path(str(value["globalGenerationManifestPath"])).resolve())
            or not re.fullmatch(r"[0-9a-f]{64}", str(value["globalGenerationManifestSha256"]))
            or not isinstance(value["globalGenerationId"], str)
            or not isinstance(value["globalGenerationGoalId"], str)
        ):
            raise RuntimeError("NEWS_GRASP_GLOBAL_GENERATION_BINDING_INVALID")
        global_manifest = _load_global_generation_manifest(
            manifest_path=observed_global_manifest_path,
            execution_repo=execution_repo,
            expected_sha256=str(value["globalGenerationManifestSha256"]),
        )
        if (
            global_manifest["generationId"] != value["globalGenerationId"]
            or global_manifest["validForGoalId"] != value["globalGenerationGoalId"]
        ):
            raise RuntimeError("NEWS_GRASP_GLOBAL_GENERATION_BINDING_INVALID")

    e2e_policy_argument_count = arguments.count(E2E_ATTEMPT_POLICY_ARGUMENT)
    e2e_attempt_argument_count = arguments.count(E2E_LOGICAL_ATTEMPT_ARGUMENT)
    if e2e_policy_argument_count > 1 or e2e_attempt_argument_count > 1:
        raise RuntimeError("NEWS_GRASP_E2E_ATTEMPT_ARGUMENTS_INVALID")
    e2e_policy_path: Path | None = None
    e2e_attempt_number: int | None = None
    if e2e_policy_argument_count == 0 or e2e_attempt_argument_count == 0:
        if E2E_ATTEMPT_AUTHORITY_FIELDS.intersection(value):
            raise RuntimeError("NEWS_GRASP_E2E_ATTEMPT_POLICY_REQUIRED")
        e2e_attempt_policy = None
    else:
        if not E2E_ATTEMPT_AUTHORITY_FIELDS.issubset(value):
            raise RuntimeError("NEWS_GRASP_E2E_ATTEMPT_POLICY_REQUIRED")
        try:
            policy_index = arguments.index(E2E_ATTEMPT_POLICY_ARGUMENT)
            attempt_index = arguments.index(E2E_LOGICAL_ATTEMPT_ARGUMENT)
            observed_policy_path = _assert_managed_path(
                Path(arguments[policy_index + 1]),
                execution_repo,
                "NEWS_GRASP_E2E_ATTEMPT_POLICY_INVALID",
            ).resolve(strict=True)
            observed_attempt = int(arguments[attempt_index + 1])
        except (ValueError, IndexError, OSError) as error:
            raise RuntimeError("NEWS_GRASP_E2E_ATTEMPT_ARGUMENTS_INVALID") from error
        if (
            str(observed_policy_path)
            != str(Path(str(value["e2eAttemptPolicyPath"])).resolve())
            or int(value["e2eLogicalAttempt"]) != observed_attempt
            or observed_attempt > 2
        ):
            raise RuntimeError("NEWS_GRASP_E2E_ATTEMPT_POLICY_INVALID")
        e2e_policy_path = observed_policy_path
        e2e_attempt_number = observed_attempt
        admission_indices = [index for index, item in enumerate(arguments) if item == E2E_FINAL_ADMISSION_ARGUMENT]
        if len(admission_indices) != 1 or not isinstance(value.get("e2eAdmissionPath"), str):
            raise RuntimeError("NEWS_GRASP_E2E_ATTEMPT_ADMISSION_REQUIRED")
        try:
            observed_admission_path = _assert_managed_path(
                Path(arguments[admission_indices[0] + 1]),
                execution_repo,
                "NEWS_GRASP_E2E_ATTEMPT_ADMISSION_INVALID",
            ).resolve(strict=True)
        except (ValueError, IndexError, OSError) as error:
            raise RuntimeError("NEWS_GRASP_E2E_ATTEMPT_ADMISSION_INVALID") from error
        if str(observed_admission_path) != str(Path(value["e2eAdmissionPath"]).resolve()):
            raise RuntimeError("NEWS_GRASP_E2E_ATTEMPT_ADMISSION_BINDING_INVALID")
        if _file_sha256(observed_admission_path) != str(value.get("e2eAdmissionSha256")):
            raise RuntimeError("NEWS_GRASP_E2E_ATTEMPT_ADMISSION_DRIFT")
        e2e_attempt_policy = _load_e2e_attempt_policy(
            policy_path=observed_policy_path,
            execution_repo=execution_repo,
            expected_sha256=str(value["e2eAttemptPolicySha256"]),
            expected_attempt=observed_attempt,
        )
        binding = e2e_attempt_policy.get("admissionBinding")
        if not isinstance(binding, dict) or binding.get("admissionPath") != str(observed_admission_path) or binding.get("admissionSha256") != str(value["e2eAdmissionSha256"]):
            raise RuntimeError("NEWS_GRASP_E2E_ATTEMPT_ADMISSION_BINDING_INVALID")
    try:
        external_authority = _assert_managed_path(
            Path(value["externalHealthAuthorityFixturePath"]),
            execution_repo,
            "NEWS_GRASP_INSTALLED_NOPUBLISH_EXTERNAL_AUTHORITY_INVALID",
        ).resolve(strict=True)
        external_stat = external_authority.stat()
    except OSError as error:
        raise RuntimeError(
            "NEWS_GRASP_INSTALLED_NOPUBLISH_EXTERNAL_AUTHORITY_INVALID"
        ) from error
    if (
        not external_authority.is_file()
        or external_authority.is_symlink()
        or external_stat.st_size > 64 * 1024
    ):
        raise RuntimeError("NEWS_GRASP_INSTALLED_NOPUBLISH_EXTERNAL_AUTHORITY_INVALID")
    if (
        _file_sha256(external_authority)
        != value["externalHealthAuthorityFixtureSha256"]
    ):
        raise RuntimeError("NEWS_GRASP_INSTALLED_NOPUBLISH_EXTERNAL_AUTHORITY_DRIFT")
    try:
        reflection_receipt = _assert_managed_path(
            Path(str(value["releaseReflectionReceiptPath"])),
            execution_repo,
            "NEWS_GRASP_INSTALLED_NOPUBLISH_REFLECTION_INVALID",
        ).resolve(strict=True)
    except OSError as error:
        raise RuntimeError("NEWS_GRASP_INSTALLED_NOPUBLISH_REFLECTION_INVALID") from error
    if (
        reflection_receipt.is_symlink()
        or not reflection_receipt.is_file()
        or _file_sha256(reflection_receipt)
        != str(value["releaseReflectionReceiptSha256"])
        or value["releaseReflectionImpactClass"] != "source-runtime-impacting"
    ):
        raise RuntimeError("NEWS_GRASP_INSTALLED_NOPUBLISH_REFLECTION_INVALID")
    try:
        expected_isolation_receipt = Path(str(value["isolationReceiptPath"])).resolve(strict=True)
        expected_launch_evidence = Path(str(value["launchEvidencePath"])).resolve(strict=False)
    except OSError as error:
        raise RuntimeError("NEWS_GRASP_INSTALLED_NOPUBLISH_ISOLATION_DRIFT") from error
    if (
        expected_isolation_receipt.is_symlink()
        or not expected_isolation_receipt.is_file()
        or _file_sha256(expected_isolation_receipt) != str(value["isolationReceiptSha256"])
    ):
        raise RuntimeError("NEWS_GRASP_INSTALLED_NOPUBLISH_ISOLATION_DRIFT")
    if (
        not expected_launch_evidence.is_relative_to(execution_repo)
        or expected_launch_evidence == execution_repo
        or expected_launch_evidence.exists()
        or expected_launch_evidence.is_symlink()
        or expected_launch_evidence.parent.resolve(strict=True) != expected_launch_evidence.parent
    ):
        raise RuntimeError("NEWS_GRASP_INSTALLED_NOPUBLISH_LAUNCH_EVIDENCE_DRIFT")
    resolved = resolve_bootstrap_launch_roots(
        bin_dir=bin_dir,
        enforce_canonical_runtime=True,
    )
    runtime_repo = resolved["configuredRuntime"].resolve(strict=True)
    runtime_python = resolved["pythonExe"].resolve(strict=True)
    try:
        observed_python = Path(argument_values["-PyExeOverride"]).resolve(strict=True)
        observed_binding = Path(argument_values["-HighCostBindingPath"]).resolve(strict=True)
        state_output = Path(argument_values["-StateFileOverride"]).resolve(strict=False)
        log_output = Path(argument_values["-LogDirOverride"]).resolve(strict=False)
        parent_authority = Path(argument_values["-HighCostParentAuthorityPath"]).resolve(strict=True)
        reservation_receipt = Path(argument_values["-E2EFinalReservationReceiptPath"]).resolve(strict=True)
        claim_receipt = Path(argument_values["-E2EFinalClaimReceiptPath"]).resolve(strict=False)
    except OSError as error:
        raise RuntimeError("NEWS_GRASP_INSTALLED_NOPUBLISH_ARGUMENTS_INVALID") from error
    expected_binding = Path(
        _stable_authority_option(launcher_identity, "--high-cost-binding-path")
    ).resolve(strict=True)
    if (
        observed_python != runtime_python
        or observed_python.is_symlink()
        or _file_sha256(observed_python) != str(value["pythonExecutableSha256"])
        or observed_binding != expected_binding
        or argument_values["-HighCostBindingReceiptSha256"].lower()
        != _stable_authority_option(
            launcher_identity, "--high-cost-binding-sha256"
        ).lower()
        or any(
            candidate == execution_repo or not candidate.is_relative_to(execution_repo)
            for candidate in (
                state_output,
                log_output,
                parent_authority,
                reservation_receipt,
                claim_receipt,
            )
        )
    ):
        raise RuntimeError("NEWS_GRASP_INSTALLED_NOPUBLISH_ARGUMENTS_INVALID")
    _validate_active_production_generation(
        runtime_repo=runtime_repo,
        launcher_identity=launcher_identity,
    )
    isolation_validation = _validate_nopublish_isolation(
        execution_repo=execution_repo,
        runtime_repo=runtime_repo,
        issue_date=str(value["issueDate"]),
        receipt_path=expected_isolation_receipt,
    )
    try:
        runtime_head = _run_git(runtime_repo, "rev-parse", "HEAD").strip().lower()
        execution_head = _run_git(execution_repo, "rev-parse", "HEAD").strip().lower()
        tracked_diff = _run_git(
            execution_repo, "status", "--porcelain", "--untracked-files=all"
        ).strip()
    except (OSError, RuntimeError) as error:
        raise RuntimeError("NEWS_GRASP_INSTALLED_GENERATION_DRIFT") from error
    if (
        not execution_repo.is_dir()
        or execution_head != runtime_head
        or value.get("executionRepoCommit") != execution_head
        or value.get("runtimeRepoCommit") != runtime_head
        or isolation_validation.get("status") != "Green"
        or _git_common_dir(execution_repo) != _git_common_dir(runtime_repo)
    ):
        raise RuntimeError("NEWS_GRASP_INSTALLED_GENERATION_DRIFT")
    expected_runner = (
        execution_repo / "scripts" / "ops" / "news-grasp-release-nopublish.ps1"
    ).resolve(strict=True)
    runtime_runner = (
        runtime_repo / "scripts" / "ops" / "news-grasp-release-nopublish.ps1"
    ).resolve(strict=True)
    expected_codex_wrapper = (
        execution_repo / "scripts" / "ops" / "run_codex_with_timeout.ps1"
    ).resolve(strict=True)
    runtime_codex_wrapper = (
        runtime_repo / "scripts" / "ops" / "run_codex_with_timeout.ps1"
    ).resolve(strict=True)
    expected_module = (
        execution_repo / "tools" / "news_grasp_release_nopublish.py"
    ).resolve(strict=True)
    runtime_module = (
        runtime_repo / "tools" / "news_grasp_release_nopublish.py"
    ).resolve(strict=True)
    try:
        file_index = arguments.index("-File")
        observed_runner = Path(arguments[file_index + 1]).resolve(strict=True)
        repo_index = arguments.index("-RepoDirOverride")
        observed_repo = Path(arguments[repo_index + 1]).resolve(strict=True)
        wrapper_index = arguments.index("-CodexWrapperOverride")
        observed_codex_wrapper = Path(arguments[wrapper_index + 1]).resolve(strict=True)
        external_index = arguments.index("-ExternalHealthAuthorityPathOverride")
        observed_external_authority = _assert_managed_path(
            Path(arguments[external_index + 1]),
            execution_repo,
            "NEWS_GRASP_INSTALLED_NOPUBLISH_EXTERNAL_AUTHORITY_INVALID",
        ).resolve(strict=True)
        external_hash_index = arguments.index(
            "-ExternalHealthAuthorityExpectedSha256"
        )
        observed_external_authority_sha256 = arguments[external_hash_index + 1]
        isolation_index = arguments.index("-IsolationReceiptPath")
        observed_isolation_receipt = Path(arguments[isolation_index + 1]).resolve(strict=True)
        launch_evidence_index = arguments.index("-LaunchEvidencePath")
        observed_launch_evidence = Path(arguments[launch_evidence_index + 1]).resolve(strict=False)
    except (ValueError, IndexError, OSError) as error:
        raise RuntimeError("NEWS_GRASP_INSTALLED_NOPUBLISH_ARGUMENTS_INVALID") from error
    if (
        observed_external_authority != external_authority
        or observed_external_authority_sha256
        != value["externalHealthAuthorityFixtureSha256"]
    ):
        raise RuntimeError("NEWS_GRASP_INSTALLED_NOPUBLISH_EXTERNAL_AUTHORITY_DRIFT")
    if (
        observed_isolation_receipt != expected_isolation_receipt
        or observed_isolation_receipt.is_symlink()
        or not observed_isolation_receipt.is_file()
        or _file_sha256(observed_isolation_receipt) != str(value["isolationReceiptSha256"])
    ):
        raise RuntimeError("NEWS_GRASP_INSTALLED_NOPUBLISH_ISOLATION_DRIFT")
    if observed_launch_evidence != expected_launch_evidence:
        raise RuntimeError("NEWS_GRASP_INSTALLED_NOPUBLISH_LAUNCH_EVIDENCE_DRIFT")
    if (
        observed_runner != expected_runner
        or observed_repo != execution_repo
        or observed_codex_wrapper != expected_codex_wrapper
        or _file_sha256(expected_runner) != _file_sha256(runtime_runner)
        or _file_sha256(expected_codex_wrapper)
        != _file_sha256(runtime_codex_wrapper)
        or _file_sha256(expected_module) != _file_sha256(runtime_module)
    ):
        raise RuntimeError("NEWS_GRASP_INSTALLED_GENERATION_DRIFT")
    launch_snapshot = {
        "executionHead": execution_head,
        "runtimeHead": runtime_head,
        "trackedDiff": tracked_diff,
        "workingTreeContentIdentity": _working_tree_content_identity(execution_repo),
        "executableSha256": _file_sha256(executable),
        "pythonSha256": _file_sha256(runtime_python),
        "argumentsSha256": _file_sha256(arguments_path),
        "runnerSha256": _file_sha256(expected_runner),
        "wrapperSha256": _file_sha256(expected_codex_wrapper),
        "moduleSha256": _file_sha256(expected_module),
        "isolationSha256": _file_sha256(expected_isolation_receipt),
        "externalSha256": _file_sha256(external_authority),
    }
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        prelaunch_snapshot = {
            "executionHead": _run_git(execution_repo, "rev-parse", "HEAD").strip().lower(),
            "runtimeHead": _run_git(runtime_repo, "rev-parse", "HEAD").strip().lower(),
            "trackedDiff": _run_git(
                execution_repo, "status", "--porcelain", "--untracked-files=all"
            ).strip(),
            "workingTreeContentIdentity": _working_tree_content_identity(execution_repo),
            "executableSha256": _file_sha256(executable),
            "pythonSha256": _file_sha256(runtime_python),
            "argumentsSha256": _file_sha256(arguments_path),
            "runnerSha256": _file_sha256(expected_runner),
            "wrapperSha256": _file_sha256(expected_codex_wrapper),
            "moduleSha256": _file_sha256(expected_module),
            "isolationSha256": _file_sha256(expected_isolation_receipt),
            "externalSha256": _file_sha256(external_authority),
        }
    except (OSError, RuntimeError) as error:
        raise RuntimeError("NEWS_GRASP_INSTALLED_GENERATION_DRIFT") from error
    if prelaunch_snapshot != launch_snapshot:
        raise RuntimeError("NEWS_GRASP_INSTALLED_GENERATION_DRIFT")
    launch_started_ns = time.time_ns()
    child_environment = dict(os.environ)
    for inherited_name in (
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONSTARTUP",
        "PYTHONINSPECT",
        "PYTHONUSERBASE",
    ):
        child_environment.pop(inherited_name, None)
    child_environment.update(
        {
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "PYTHONNOUSERSITE": "1",
            "NEWS_GRASP_REPO_ROOT": str(execution_repo),
        }
    )
    test_double = getattr(subprocess.run, "__module__", "subprocess") != "subprocess"
    if test_double:
        result = subprocess.run(
            [str(executable), *arguments],
            shell=False,
            stdin=subprocess.DEVNULL,
            cwd=str(execution_repo),
            env=child_environment,
            creationflags=creationflags,
            check=False,
        )
        result_code = int(result.returncode)
        process_identity = {
            "pid": os.getpid(),
            "creationTime": "test-double",
            "imagePath": str(executable.resolve(strict=True)),
            "imageSha256": _file_sha256(executable),
        }
    else:
        try:
            owned_module = _load_module_from_exact_path(
                runtime_repo / "tools" / "news_grasp_owned_process.py",
                prefix="news_grasp_release_owned_process",
            )
            spawn_owned = getattr(owned_module, "spawn_owned", None)
        except Exception as error:
            raise RuntimeError("NEWS_GRASP_OWNED_PROCESS_RUNTIME_IMPORT_FAILED") from error
        if not callable(spawn_owned):
            raise RuntimeError("NEWS_GRASP_OWNED_PROCESS_RUNTIME_IMPORT_FAILED")
        process = spawn_owned(
            [str(executable), *arguments],
            cwd=str(execution_repo),
            env=child_environment,
            capture_output=False,
        )
        process_identity = _owned_process_identity(process, executable)
        try:
            result_code = int(process.wait(timeout=60 * 60))
        except subprocess.TimeoutExpired as error:
            process.close_job()
            raise RuntimeError("NEWS_GRASP_RELEASE_NOPUBLISH_TIMEOUT") from error
        finally:
            process.close()
    if not test_double and result_code == 0 and e2e_policy_path is not None and e2e_attempt_number is not None:
        try:
            admission_path = Path(str(value["e2eAdmissionPath"])).resolve(strict=True)
            arguments_file = Path(str(value["runnerArgumentsPath"])).resolve(strict=True)
            claim_index = arguments.index("-E2EFinalClaimReceiptPath")
            state_index = arguments.index("-StateFileOverride")
            claim_path = Path(arguments[claim_index + 1]).resolve(strict=True)
            state_path = Path(arguments[state_index + 1]).resolve(strict=True)
            evidence = read_runner_launch_evidence(
                expected_launch_evidence,
                issue_date=str(value["issueDate"]),
                expected_root=expected_launch_evidence.parent,
                expected_min_mtime_ns=launch_started_ns,
            )
            if (
                int(evidence.get("processId", -1)) != int(process_identity.get("pid", -2))
                or evidence.get("powershellSha256") != _file_sha256(executable)
                or evidence.get("runnerSha256") != _file_sha256(expected_runner)
            ):
                raise RuntimeError("NEWS_GRASP_RUNNER_LAUNCH_EVIDENCE_IDENTITY_DRIFT")
            _write_runner_terminal_authority(
                policy_path=e2e_policy_path,
                attempt=e2e_attempt_number,
                admission_path=admission_path,
                runner_arguments_path=arguments_file,
                runner_state_path=state_path,
                claim_path=claim_path,
                process_identity=process_identity,
                runner_exit_code=result_code,
                child_launch_evidence=evidence,
            )
        except (KeyError, OSError, RuntimeError, ValueError) as error:
            print(json.dumps({"status": "failed", "reasonCode": str(error)}, ensure_ascii=False), file=sys.stderr)
            return 76
    return result_code


def _git_tracked_tree_manifest(repo: Path, commit: str) -> dict[str, str]:
    """commitの全tracked objectをpathとGit identityへ正規化する。"""
    output = _run_git(repo, "ls-tree", "-r", "--full-tree", "-z", commit)
    rows: dict[str, str] = {}
    for entry in output.split("\0"):
        if not entry:
            continue
        try:
            metadata, relative = entry.split("\t", 1)
            mode, object_type, object_id = metadata.split(" ", 2)
        except ValueError as error:
            raise RuntimeError("NEWS_GRASP_PRODUCTION_GENERATION_TREE_INVALID") from error
        if (
            not relative
            or relative in rows
            or "\\" in relative
            or relative.startswith("/")
            or any(part in {"", ".", ".."} for part in relative.split("/"))
            or not re.fullmatch(r"[0-7]{6}", mode)
            or object_type not in {"blob", "commit"}
            or not re.fullmatch(r"[0-9a-f]{40,64}", object_id)
        ):
            raise RuntimeError("NEWS_GRASP_PRODUCTION_GENERATION_TREE_INVALID")
        rows[relative] = f"{mode}:{object_type}:{object_id}"
    if not rows:
        raise RuntimeError("NEWS_GRASP_PRODUCTION_GENERATION_TREE_INVALID")
    return dict(sorted(rows.items()))


def _git_status_excluding_runtime_artifacts(repo: Path) -> str:
    status_raw = _run_git(repo, "status", "--porcelain", "--untracked-files=no", "-z")
    status_entries = [item for item in status_raw.split("\x00") if item]
    unexpected = []
    for entry in status_entries:
        path = entry[2:].lstrip().replace("\\", "/")
        if (
            path.startswith("build/")
            or path.startswith("data/gate_attempts/")
            or path.startswith("data/search_audit/")
        ):
            continue
        unexpected.append(entry)
    return "\n".join(unexpected)


def _seal_active_production_generation(
    *,
    source_repo: Path,
    runtime_repo: Path,
    runtime_root: Path,
    origin_sha: str,
    bin_dir: Path,
) -> dict[str, object]:
    """runtime transaction終端だけでimmutable manifestとactive pointerを発行する。"""
    identity = _load_stable_launcher_identity(bin_dir=bin_dir)
    runtime_config = bin_dir / "news-grasp-runtime-root-v1.json"
    source_head = _run_git(source_repo, "rev-parse", "HEAD").strip().lower()
    runtime_head = _run_git(runtime_repo, "rev-parse", "HEAD").strip().lower()
    remote_head = _run_git(source_repo, "rev-parse", "origin/main").strip().lower()
    source_status = _git_status_excluding_runtime_artifacts(source_repo).strip()
    runtime_status = _git_status_excluding_runtime_artifacts(runtime_repo).strip()
    if source_status or runtime_status:
        raise RuntimeError("NEWS_GRASP_PRODUCTION_GENERATION_DIRTY")
    if (
        source_head != origin_sha
        or runtime_head != origin_sha
        or remote_head != origin_sha
        or _git_common_dir(source_repo) != _git_common_dir(runtime_repo)
    ):
        raise RuntimeError("NEWS_GRASP_PRODUCTION_GENERATION_DRIFT")
    source_tracked = _git_tracked_tree_manifest(source_repo, source_head)
    source_tracked_manifest_sha256 = _sha256_json(source_tracked)
    critical_paths = (
        "automation/news-grasp-6-40/automation.toml.template",
        "automation/skills/news-grasp-direct-mainline/SKILL.md",
        "tools/news_grasp_direct_runtime.py",
        "tools/news_grasp_direct_completion.py",
        "tools/news_grasp_title_control.py",
        "tools/news_grasp_title_materializer.py",
        "scripts/ops/news-grasp-title-materializer.pyw",
        "tools/daily_self_heal.py",
        "tools/news_grasp_daily_control.py",
        "tools/news_grasp_operational_contract.py",
        "tools/news_grasp_checkpoint.py",
        "tools/news_grasp_generation.py",
        "tools/operational_recovery_registry.py",
        "config/operational_recovery_registry_v1.json",
        "tools/deepdive_quality.py",
        "tools/render_deepdive.py",
        "tools/tts/build_deepdive_dialogue_script.py",
        "tools/tts/deepdive_dialogue.py",
        "tools/tts/proc.py",
        "tools/validate_deepdive_urls.py",
        "prompts/deepdive-template.html",
        "prompts/deepdive-runner-prompt.md",
        "scripts/ops/invoke-deepdive-system-fetch.ps1",
        "tools/news_grasp_recovery_freshness.py",
        "tools/news_grasp_recovery_closeout.py",
    )
    tracked: dict[str, str] = {}
    for relative in critical_paths:
        candidate = runtime_repo / relative
        if not candidate.is_file() or candidate.is_symlink():
            raise RuntimeError("NEWS_GRASP_PRODUCTION_GENERATION_FILE_INVALID")
        tracked[relative] = _file_sha256(candidate)
    task_action = identity.get("action")
    task_trigger = identity.get("trigger")
    if not isinstance(task_action, list) or not isinstance(task_trigger, dict):
        raise RuntimeError("NEWS_GRASP_STABLE_TASK_AUTHORITY_INVALID")
    runtime_config_sha256 = _file_sha256(runtime_config)
    tracked_manifest_sha256 = _sha256_json(tracked)
    action_sha256 = _sha256_json(task_action)
    trigger_sha256 = _sha256_json(task_trigger)
    generation_id = _sha256_json(
        {
            "sourceCommit": origin_sha,
            "sourceTrackedManifestSha256": source_tracked_manifest_sha256,
            "runtimeTrackedManifestSha256": tracked_manifest_sha256,
            "configSha256": runtime_config_sha256,
            "installedLauncherSha256": identity["stableLauncherSha256"],
            "stableTaskAuthoritySha256": identity["authoritySha256"],
            "taskActionSha256": action_sha256,
            "taskTriggerSha256": trigger_sha256,
        }
    )
    active_pointer_path = runtime_root / "active-generation-v2.json"
    previous_generation_id = ""
    if active_pointer_path.is_file():
        try:
            previous = json.loads(active_pointer_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as error:
            raise RuntimeError("NEWS_GRASP_ACTIVE_GENERATION_INVALID") from error
        if not isinstance(previous, dict):
            raise RuntimeError("NEWS_GRASP_ACTIVE_GENERATION_INVALID")
        previous_unsigned = dict(previous)
        previous_sha256 = str(previous_unsigned.pop("pointerSha256", ""))
        if (
            previous.get("schemaVersion") != "NEWS_GRASP_ACTIVE_GENERATION_V2"
            or previous_sha256 != _sha256_json(previous_unsigned)
        ):
            raise RuntimeError("NEWS_GRASP_ACTIVE_GENERATION_INVALID")
        previous_candidate = (
            previous.get("previousGenerationId")
            if previous.get("generationId") == generation_id
            else previous.get("generationId")
        )
        previous_generation_id = str(previous_candidate or "")
    manifest: dict[str, object] = {
        "schemaVersion": "PRODUCTION_GENERATION_MANIFEST_V2",
        "productId": "News-Grasp",
        "generationId": generation_id,
        "previousGenerationId": previous_generation_id or None,
        "source": {
            "commit": origin_sha,
            "observedHead": source_head,
            "remoteHead": remote_head,
            "commonDir": str(_git_common_dir(source_repo)),
            "origin": "origin/main",
            "trackedFiles": source_tracked,
            "trackedManifestSha256": source_tracked_manifest_sha256,
        },
        "runtime": {
            "root": str(runtime_repo),
            "commit": runtime_head,
            "commonDir": str(_git_common_dir(runtime_repo)),
            "trackedFiles": tracked,
            "trackedManifestSha256": tracked_manifest_sha256,
        },
        "configSha256": runtime_config_sha256,
        "installedLauncherSha256": identity["stableLauncherSha256"],
        "stableTaskAuthoritySha256": identity["authoritySha256"],
        "scheduledTask": {
            "action": task_action,
            "actionSha256": action_sha256,
            "trigger": task_trigger,
            "triggerSha256": trigger_sha256,
        },
    }
    final_source_head = _run_git(source_repo, "rev-parse", "HEAD").strip().lower()
    final_runtime_head = _run_git(runtime_repo, "rev-parse", "HEAD").strip().lower()
    final_remote_head = _run_git(source_repo, "rev-parse", "origin/main").strip().lower()
    final_source_status = _git_status_excluding_runtime_artifacts(source_repo).strip()
    final_runtime_status = _git_status_excluding_runtime_artifacts(runtime_repo).strip()
    final_source_tracked = _git_tracked_tree_manifest(source_repo, final_source_head)
    if final_source_status or final_runtime_status:
        raise RuntimeError("NEWS_GRASP_PRODUCTION_GENERATION_DIRTY")
    if (
        final_source_head != source_head
        or final_runtime_head != runtime_head
        or final_remote_head != remote_head
        or final_source_tracked != source_tracked
    ):
        raise RuntimeError("NEWS_GRASP_PRODUCTION_GENERATION_SOURCE_DRIFT")
    manifest["manifestSha256"] = _sha256_json(manifest)
    generation_root = runtime_root / "generations"
    generation_root.mkdir(parents=True, exist_ok=True)
    manifest_path = generation_root / f"{generation_id}.json"
    if manifest_path.is_file():
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as error:
            raise RuntimeError("NEWS_GRASP_PRODUCTION_GENERATION_INVALID") from error
        if existing != manifest:
            raise RuntimeError("NEWS_GRASP_PRODUCTION_GENERATION_DRIFT")
    else:
        _write_json_exclusive(manifest_path, manifest)
    pointer: dict[str, object] = {
        "schemaVersion": "NEWS_GRASP_ACTIVE_GENERATION_V2",
        "generationId": generation_id,
        "previousGenerationId": previous_generation_id or None,
        "manifestPath": str(manifest_path),
        "manifestSha256": manifest["manifestSha256"],
        "stableTaskAuthoritySha256": identity["authoritySha256"],
        "phase": "transaction_committed",
    }
    pointer["pointerSha256"] = _sha256_json(pointer)
    _write_json_atomic(active_pointer_path, pointer)
    return pointer


def _assert_managed_path(path: Path, boundary: Path, code: str) -> Path:
    """boundary配下の既存componentにsymlink/reparseが無いことを確認する。"""
    candidate = Path(os.path.abspath(os.fspath(path)))
    root = Path(os.path.abspath(os.fspath(boundary)))
    try:
        if os.path.commonpath((str(root), str(candidate))) != str(root):
            raise RuntimeError(code)
    except ValueError as error:
        raise RuntimeError(code) from error
    if root.exists() or root.is_symlink():
        try:
            root_info = root.lstat()
        except OSError as error:
            raise RuntimeError(code) from error
        root_attributes = int(getattr(root_info, "st_file_attributes", 0))
        if root.is_symlink() or root_attributes & 0x400:
            raise RuntimeError(code)
    current = root
    relative = candidate.relative_to(root)
    for part in relative.parts:
        current /= part
        if not current.exists() and not current.is_symlink():
            break
        try:
            info = current.lstat()
        except OSError as error:
            raise RuntimeError(code) from error
        attributes = int(getattr(info, "st_file_attributes", 0))
        if current.is_symlink() or attributes & 0x400:
            raise RuntimeError(code)
    return candidate


def _retry_readonly_remove(func: object, path: str, exc_info: tuple[object, object, object]) -> None:
    """managed root内の読み取り専用payloadだけを属性解除して削除する。"""
    error = exc_info[1]
    if not isinstance(error, PermissionError) and getattr(error, "winerror", None) != 5:
        raise error  # type: ignore[misc]
    target = Path(path)
    if target.is_symlink():
        raise RuntimeError("PRODUCTION_RUNTIME_REPARSE_INVALID")
    try:
        target.chmod(target.stat().st_mode | stat.S_IWRITE)
    except OSError:
        raise error  # type: ignore[misc]
    func(path)  # type: ignore[operator]


def _remove_runtime_path(path: Path) -> None:
    """caller側でmanaged pathを検証済みのruntime file/dirを回収する。"""
    if path.is_symlink():
        raise RuntimeError("PRODUCTION_RUNTIME_REPARSE_INVALID")
    if path.is_dir():
        shutil.rmtree(path, onerror=_retry_readonly_remove)
        return
    try:
        path.unlink()
    except (PermissionError, OSError) as error:
        if getattr(error, "winerror", None) != 5 and not isinstance(error, PermissionError):
            raise
        _retry_readonly_remove(os.unlink, str(path), (type(error), error, error.__traceback__))


@contextmanager
def _managed_directory_handle(path: Path, boundary: Path, code: str):
    """検査後のreparse/junction交換を、delete-deny handleの寿命内で封じる。"""
    candidate = _assert_managed_path(path, boundary, code)
    root = Path(os.path.abspath(os.fspath(boundary)))
    relative = candidate.relative_to(root)
    if sys.platform != "win32":
        if candidate.is_symlink():
            raise RuntimeError(code)
        yield candidate
        return
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
        ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
    ]
    kernel32.CreateFileW.restype = ctypes.c_void_p
    kernel32.GetFileAttributesW.argtypes = [ctypes.c_wchar_p]
    kernel32.GetFileAttributesW.restype = ctypes.c_uint32
    kernel32.GetFinalPathNameByHandleW.argtypes = [
        ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32
    ]
    kernel32.GetFinalPathNameByHandleW.restype = ctypes.c_uint32
    kernel32.GetFileInformationByHandle.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel32.GetFileInformationByHandle.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    invalid = ctypes.c_void_p(-1).value
    attributes = kernel32.GetFileAttributesW(str(candidate))
    if attributes == 0xFFFFFFFF or attributes & 0x400:
        raise RuntimeError(code)
    # rootからcandidate直前までの全既存componentをdelete-denyで保持する。
    # candidateだけを開く方式では、子parentのreparse交換を許していた。
    existing_components: list[Path] = []
    current = root
    if current.exists():
        existing_components.append(current)
    for part in relative.parts:
        current = current / part
        if not current.exists():
            break
        existing_components.append(current)
    handles: list[ctypes.c_void_p] = []
    for component in existing_components:
        handle = kernel32.CreateFileW(
            str(component), 0x80, 0x3, None, 3,
            0x02000000 | 0x00200000, None,
        )
        if handle == invalid or not handle:
            for opened in reversed(handles):
                kernel32.CloseHandle(opened)
            raise RuntimeError(code)
        handles.append(handle)
    if not handles:
        raise RuntimeError(code)
    try:
        # 各opened componentのfinal pathを再照合し、別directoryへ差し替えられた
        # handleをconsumerへ渡さない。
        for opened, component in zip(handles, existing_components):
            # open後のhandle属性を再検査し、検査/open間にreparseへ交換された
            # componentをconsumerへ渡さない。
            class _ByHandleFileInformation(ctypes.Structure):
                _fields_ = [
                    ("dwFileAttributes", ctypes.c_uint32),
                    ("ftCreationTime", ctypes.c_uint64),
                    ("ftLastAccessTime", ctypes.c_uint64),
                    ("ftLastWriteTime", ctypes.c_uint64),
                    ("dwVolumeSerialNumber", ctypes.c_uint32),
                    ("nFileSizeHigh", ctypes.c_uint32),
                    ("nFileSizeLow", ctypes.c_uint32),
                    ("nNumberOfLinks", ctypes.c_uint32),
                    ("nFileIndexHigh", ctypes.c_uint32),
                    ("nFileIndexLow", ctypes.c_uint32),
                ]
            information = _ByHandleFileInformation()
            if not kernel32.GetFileInformationByHandle(opened, ctypes.byref(information)):
                raise RuntimeError(code)
            if information.dwFileAttributes & 0x400:
                raise RuntimeError(code)
            buffer = ctypes.create_unicode_buffer(32768)
            length = kernel32.GetFinalPathNameByHandleW(opened, buffer, len(buffer), 0)
            if not length:
                raise RuntimeError(code)
            final_path = str(buffer.value).replace("\\\\?\\", "")
            expected = str(component).replace("\\\\?\\", "")
            if os.path.normcase(os.path.abspath(final_path)) != os.path.normcase(
                os.path.abspath(expected)
            ):
                raise RuntimeError(code)
        yield candidate
    finally:
        for opened in reversed(handles):
            kernel32.CloseHandle(opened)


def _run_git(
    repo: Path,
    *args: str,
    allowed_codes: tuple[int, ...] = (0,),
    timeout_seconds: int | None = None,
) -> str:
    git_exe = Path(r"C:\Program Files\Git\cmd\git.exe")
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    # GitはGIT_CONFIG_*、GIT_SSH_COMMAND、GIT_ASKPASS等も挙動を変更できる。
    # 個別deny-listでは将来追加されるGit環境変数を取り逃がすため、継承した
    # GIT_*をすべて除去して、この境界が所有する非対話設定だけを再追加する。
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
    }
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["GCM_INTERACTIVE"] = "Never"
    try:
        completed = subprocess.run(
            [str(git_exe), "-C", str(repo), *args],
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=creationflags,
            check=False,
            timeout=timeout_seconds,
            env=environment,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("PRODUCTION_RUNTIME_GIT_TIMEOUT") from error
    if len(completed.stdout) > MAX_GIT_OUTPUT_BYTES or len(completed.stderr) > MAX_GIT_OUTPUT_BYTES:
        raise RuntimeError("PRODUCTION_RUNTIME_GIT_OUTPUT_OVERFLOW")
    if completed.returncode not in allowed_codes:
        detail = completed.stderr.decode("utf-8", errors="replace")[-1000:]
        raise RuntimeError(
            f"PRODUCTION_RUNTIME_GIT_FAILED exit={completed.returncode} detail={detail}"
        )
    return completed.stdout.decode("utf-8", errors="replace").strip()


def _working_tree_content_identity(repo: Path) -> str:
    """HEADとの差分pathと現在bytesを一つのidentityへ封印する。"""

    names: set[str] = set()
    for arguments in (
        ("diff", "--name-only", "--no-renames", "-z", "--no-ext-diff", "--no-textconv"),
        ("diff", "--cached", "--name-only", "--no-renames", "-z", "--no-ext-diff", "--no-textconv"),
    ):
        raw = _run_git(repo, *arguments)
        names.update(item for item in raw.split("\x00") if item)
    rows: list[dict[str, object]] = []
    resolved_repo = repo.resolve(strict=True)
    for relative in sorted(names):
        candidate = (resolved_repo / relative).resolve(strict=False)
        if candidate == resolved_repo or not candidate.is_relative_to(resolved_repo):
            raise RuntimeError("NEWS_GRASP_WORKING_TREE_IDENTITY_INVALID")
        if not candidate.exists():
            rows.append({"path": relative, "state": "deleted"})
            continue
        if candidate.is_symlink() or not candidate.is_file():
            raise RuntimeError("NEWS_GRASP_WORKING_TREE_IDENTITY_INVALID")
        before = candidate.stat()
        payload = candidate.read_bytes()
        after = candidate.stat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise RuntimeError("NEWS_GRASP_WORKING_TREE_IDENTITY_DRIFT")
        rows.append(
            {
                "path": relative,
                "state": "file",
                "mode": stat.S_IMODE(after.st_mode),
                "size": after.st_size,
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    return _sha256_json({"paths": rows})


def _git_common_dir(repo: Path) -> Path:
    raw = _run_git(repo, "rev-parse", "--git-common-dir")
    common = Path(raw)
    if not common.is_absolute():
        common = repo / common
    return common.resolve(strict=True)


def _assert_runtime_common_dir(runtime: Path, source_common: Path) -> None:
    if runtime.exists() and _git_common_dir(runtime) != source_common:
        raise RuntimeError("PRODUCTION_RUNTIME_COMMON_DIR_DRIFT")


def _runtime_recovery_authority_path(
    runtime_root: Path, transaction_id: str
) -> Path:
    return runtime_root / "authorities" / f"{transaction_id}.json"


def _runtime_recovery_issue_path(runtime_root: Path, transaction_id: str) -> Path:
    return runtime_root / "ledger" / "issues" / f"{transaction_id}.json"


def _runtime_recovery_terminal_path(runtime_root: Path, transaction_id: str) -> Path:
    return runtime_root / "ledger" / "terminals" / f"{transaction_id}.json"


def _runtime_recovery_authority_ledger_path(runtime_root: Path) -> Path:
    return runtime_root / "ledger" / "authority-issuance.jsonl"


def _runtime_recovery_canonical_authority_ledger_path(runtime_root: Path) -> Path:
    """runtime root外のcanonical broker ledger。runtime配下の自己発行を権威にしない。"""
    return runtime_root.parent / ".news-grasp-runtime-authority-issuance-v1.jsonl"


def _append_runtime_recovery_authority_ledger(
    *, runtime_root: Path, authority: dict[str, object], issue: dict[str, object]
) -> None:
    """authority発行をruntime内投影とruntime外canonical ledgerへ同時記録する。"""
    ledger_path = _runtime_recovery_authority_ledger_path(runtime_root)
    canonical_path = _runtime_recovery_canonical_authority_ledger_path(runtime_root)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    canonical_path.parent.mkdir(parents=True, exist_ok=True)
    record: dict[str, object] = {
        "schemaVersion": "NEWS_GRASP_RUNTIME_RECOVERY_AUTHORITY_ISSUANCE_LEDGER_V1",
        "transactionId": authority["transactionId"],
        "authoritySha256": authority["authoritySha256"],
        "issueSha256": issue["issueSha256"],
        "authorityPath": authority["authorityPath"],
        "issuePath": str(_runtime_recovery_issue_path(runtime_root, str(authority["transactionId"]))),
        "issuedAtUtc": authority["issuedAtUtc"],
        "canonicalLedgerPath": str(canonical_path),
    }
    record["recordSha256"] = _sha256_json(record)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    for target in (ledger_path, canonical_path):
        boundary = target.parent
        _assert_managed_path(target, boundary, "PRODUCTION_RUNTIME_RECOVERY_AUTHORITY_INVALID")
        with _managed_directory_handle(boundary, boundary, "PRODUCTION_RUNTIME_RECOVERY_AUTHORITY_INVALID"):
            with target.open("a", encoding="utf-8", newline="\n") as stream:
                if sys.platform == "win32":
                    import msvcrt
                    handle_value = ctypes.c_void_p(msvcrt.get_osfhandle(stream.fileno()))
                    class _LedgerFileInformation(ctypes.Structure):
                        _fields_ = [("dwFileAttributes", ctypes.c_uint32), ("_rest", ctypes.c_byte * 56)]
                    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                    kernel32.GetFileInformationByHandle.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
                    kernel32.GetFileInformationByHandle.restype = ctypes.c_int
                    info = _LedgerFileInformation()
                    if not kernel32.GetFileInformationByHandle(handle_value, ctypes.byref(info)) or info.dwFileAttributes & 0x400:
                        raise RuntimeError("PRODUCTION_RUNTIME_REPARSE_INVALID")
                stream.write(line)
                stream.flush()
                os.fsync(stream.fileno())


def _load_runtime_recovery_authority_ledger_record(
    *, runtime_root: Path, transaction_id: str, authority: dict[str, object], issue: dict[str, object]
) -> dict[str, object]:
    expected_path = _runtime_recovery_canonical_authority_ledger_path(runtime_root)
    records: list[dict[str, object]] = []
    for ledger_path in (_runtime_recovery_authority_ledger_path(runtime_root), expected_path):
        try:
            lines = ledger_path.read_text(encoding="utf-8-sig").splitlines()
        except OSError as error:
            raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_AUTHORITY_INVALID") from error
        # runtime内ledgerはbounded projection、runtime外canonical ledgerはappend-only
        # provenanceとして保持する（長期運用で64件を超えても拒否しない）。
        if ledger_path == _runtime_recovery_authority_ledger_path(runtime_root) and len(lines) > RUNTIME_LEDGER_MAX_ENTRIES:
            raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_MAINTENANCE_REQUIRED")
        found: dict[str, object] | None = None
        for line in lines:
            try:
                record = json.loads(line)
            except (TypeError, ValueError):
                continue
            if not isinstance(record, dict) or record.get("transactionId") != transaction_id:
                continue
            unsigned = dict(record)
            record_sha = str(unsigned.pop("recordSha256", ""))
            if record_sha != _sha256_json(unsigned):
                raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_AUTHORITY_INVALID")
            if (
                record.get("authoritySha256") == authority.get("authoritySha256")
                and record.get("issueSha256") == issue.get("issueSha256")
                and record.get("authorityPath") == authority.get("authorityPath")
                and record.get("issuePath") == issue.get("issuePath")
                and record.get("canonicalLedgerPath") == str(expected_path)
            ):
                found = record
        if found is None:
            raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_AUTHORITY_INVALID")
        records.append(found)
    if records[0]["recordSha256"] != records[1]["recordSha256"]:
        raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_AUTHORITY_INVALID")
    if records:
        return records[1]
    raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_AUTHORITY_INVALID")


def _load_runtime_recovery_issue(
    *, transaction_id: str, runtime_root: Path, authority: dict[str, object]
) -> dict[str, object]:
    issue_path = _runtime_recovery_issue_path(runtime_root, transaction_id)
    _assert_managed_path(issue_path, runtime_root, "PRODUCTION_RUNTIME_RECOVERY_AUTHORITY_INVALID")
    try:
        issue = json.loads(issue_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError) as error:
        raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_AUTHORITY_INVALID") from error
    if not isinstance(issue, dict):
        raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_AUTHORITY_INVALID")
    unsigned = dict(issue)
    issue_sha = str(unsigned.pop("issueSha256", ""))
    expected = {
        "schemaVersion": "NEWS_GRASP_PRODUCTION_RUNTIME_RECOVERY_ISSUE_V1",
        "transactionId": transaction_id,
        "authoritySha256": authority.get("authoritySha256"),
        "originSha": authority.get("originSha"),
        "sourceCommonDir": authority.get("sourceCommonDir"),
        "runtimePath": authority.get("runtimePath"),
        "quarantinePath": authority.get("quarantinePath"),
        "transactionPath": str(runtime_root / "transactions" / transaction_id),
        "replacementStagingPath": str(
            runtime_root / "transactions" / transaction_id / "replacement-staging" / "production-runtime"
        ),
    }
    if issue_sha != _sha256_json(unsigned) or any(
        issue.get(key) != value for key, value in expected.items()
    ):
        raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_AUTHORITY_INVALID")
    issue["issuePath"] = str(issue_path)
    issue["issueSha256"] = issue_sha
    authority["authorityPath"] = str(_runtime_recovery_authority_path(runtime_root, transaction_id))
    _load_runtime_recovery_authority_ledger_record(
        runtime_root=runtime_root,
        transaction_id=transaction_id,
        authority=authority,
        issue=issue,
    )
    return issue


def _load_runtime_recovery_terminal(
    *, transaction_id: str, runtime_root: Path
) -> dict[str, object]:
    terminal_path = _runtime_recovery_terminal_path(runtime_root, transaction_id)
    _assert_managed_path(terminal_path, runtime_root, "PRODUCTION_RUNTIME_RECOVERY_TERMINAL_REPLAY")
    try:
        terminal = json.loads(terminal_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError) as error:
        raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_TERMINAL_REPLAY") from error
    if not isinstance(terminal, dict):
        raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_TERMINAL_REPLAY")
    unsigned = dict(terminal)
    terminal_sha = str(unsigned.pop("terminalSha256", ""))
    if (
        terminal_sha != _sha256_json(unsigned)
        or terminal.get("schemaVersion") != "NEWS_GRASP_PRODUCTION_RUNTIME_RECOVERY_TERMINAL_V1"
        or terminal.get("transactionId") != transaction_id
    ):
        raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_TERMINAL_REPLAY")
    terminal["terminalPath"] = str(terminal_path)
    return terminal


def _ensure_runtime_recovery_terminal_from_archive(
    *, runtime_root: Path, archive_path: Path, journal: dict[str, object]
) -> Path:
    """archive昇格後terminal書込み前のcrashを、次回scanでforwardする。"""
    if journal.get("phase") != "committed":
        raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_ARCHIVE_INVALID")
    transaction_id = str(journal.get("transactionId") or "")
    if not RUNTIME_TRANSACTION_ID.fullmatch(transaction_id):
        raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_ARCHIVE_INVALID")
    _assert_managed_path(archive_path, runtime_root, "PRODUCTION_RUNTIME_REPARSE_INVALID")
    terminal_path = _runtime_recovery_terminal_path(runtime_root, transaction_id)
    _assert_managed_path(terminal_path, runtime_root, "PRODUCTION_RUNTIME_REPARSE_INVALID")
    archived_bytes = archive_path.read_bytes()
    terminal: dict[str, object] = {
        "schemaVersion": "NEWS_GRASP_PRODUCTION_RUNTIME_RECOVERY_TERMINAL_V1",
        "transactionId": transaction_id,
        "finalJournalSha256": hashlib.sha256(archived_bytes).hexdigest(),
        "archivePath": str(archive_path),
        "authoritySha256": journal["authoritySha256"],
        "issuePath": journal["issuePath"],
        "issueSha256": journal["issueSha256"],
        "committedAtUtc": str(journal.get("updatedAtUtc") or datetime.now(timezone.utc).isoformat()),
    }
    terminal["terminalSha256"] = _sha256_json(terminal)
    terminal_path.parent.mkdir(parents=True, exist_ok=True)
    if terminal_path.exists() or terminal_path.is_symlink():
        actual = _load_runtime_recovery_terminal(
            transaction_id=transaction_id, runtime_root=runtime_root
        )
        for key in (
            "transactionId", "finalJournalSha256", "archivePath",
            "authoritySha256", "issuePath", "issueSha256",
        ):
            if actual.get(key) != terminal.get(key):
                raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_TERMINAL_REPLAY")
    else:
        _write_json_exclusive(terminal_path, terminal)
    return terminal_path


def _assert_no_runtime_recovery_terminal(*, transaction_id: str, runtime_root: Path) -> None:
    terminal_path = _runtime_recovery_terminal_path(runtime_root, transaction_id)
    if terminal_path.exists() or terminal_path.is_symlink():
        _load_runtime_recovery_terminal(transaction_id=transaction_id, runtime_root=runtime_root)
        raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_TERMINAL_REPLAY")


def _load_runtime_recovery_authority(
    *, transaction_id: str, runtime_root: Path
) -> dict[str, object]:
    authority_path = _runtime_recovery_authority_path(runtime_root, transaction_id)
    _assert_managed_path(
        authority_path,
        runtime_root,
        "PRODUCTION_RUNTIME_RECOVERY_AUTHORITY_INVALID",
    )
    try:
        if authority_path.stat().st_size > 64 * 1024:
            raise ValueError("oversized")
        authority = json.loads(authority_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError) as error:
        raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_AUTHORITY_INVALID") from error
    if not isinstance(authority, dict):
        raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_AUTHORITY_INVALID")
    unsigned = dict(authority)
    authority_sha256 = str(unsigned.pop("authoritySha256", ""))
    expected_runtime = runtime_root / "production-runtime"
    expected_quarantine = (
        runtime_root / "quarantine" / transaction_id / "production-runtime"
    )
    expected_transaction = runtime_root / "transactions" / transaction_id
    expected_staging = expected_transaction / "replacement-staging" / "production-runtime"
    if (
        authority.get("schemaVersion") != RUNTIME_RECOVERY_AUTHORITY_SCHEMA
        or authority.get("transactionId") != transaction_id
        or Path(str(authority.get("runtimePath") or "")) != expected_runtime
        or Path(str(authority.get("quarantinePath") or "")) != expected_quarantine
        or Path(str(authority.get("transactionPath") or "")) != expected_transaction
        or Path(str(authority.get("replacementStagingPath") or "")) != expected_staging
        or not re.fullmatch(r"[0-9a-f]{40}", str(authority.get("originSha") or ""))
        or not str(authority.get("sourceCommonDir") or "")
        or _sha256_json(unsigned) != authority_sha256
    ):
        raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_AUTHORITY_INVALID")
    issue = _load_runtime_recovery_issue(
        transaction_id=transaction_id, runtime_root=runtime_root, authority=authority
    )
    authority["authorityPath"] = str(authority_path)
    authority["issuePath"] = issue["issuePath"]
    authority["issueSha256"] = issue["issueSha256"]
    return authority


def _issue_runtime_recovery_authority(
    *,
    transaction_id: str,
    runtime_root: Path,
    origin_sha: str,
    source_common: Path,
) -> dict[str, object]:
    authority_path = _runtime_recovery_authority_path(runtime_root, transaction_id)
    _assert_managed_path(
        authority_path,
        runtime_root,
        "PRODUCTION_RUNTIME_RECOVERY_AUTHORITY_INVALID",
    )
    authority: dict[str, object] = {
        "schemaVersion": RUNTIME_RECOVERY_AUTHORITY_SCHEMA,
        "transactionId": transaction_id,
        "originSha": origin_sha,
        "sourceCommonDir": str(source_common),
        "runtimePath": str(runtime_root / "production-runtime"),
        "quarantinePath": str(
            runtime_root / "quarantine" / transaction_id / "production-runtime"
        ),
        "transactionPath": str(runtime_root / "transactions" / transaction_id),
        "replacementStagingPath": str(
            runtime_root / "transactions" / transaction_id / "replacement-staging" / "production-runtime"
        ),
        "issuedAtUtc": datetime.now(timezone.utc).isoformat(),
    }
    authority["authoritySha256"] = _sha256_json(authority)
    issue_path = _runtime_recovery_issue_path(runtime_root, transaction_id)
    issue: dict[str, object] = {
        "schemaVersion": "NEWS_GRASP_PRODUCTION_RUNTIME_RECOVERY_ISSUE_V1",
        "transactionId": transaction_id,
        "authoritySha256": authority["authoritySha256"],
        "originSha": authority["originSha"],
        "sourceCommonDir": authority["sourceCommonDir"],
        "runtimePath": authority["runtimePath"],
        "quarantinePath": authority["quarantinePath"],
        "transactionPath": authority["transactionPath"],
        "replacementStagingPath": authority["replacementStagingPath"],
        "issuedAtUtc": authority["issuedAtUtc"],
    }
    issue["issueSha256"] = _sha256_json(issue)
    with _managed_directory_handle(
        runtime_root, runtime_root, "PRODUCTION_RUNTIME_RECOVERY_AUTHORITY_INVALID"
    ):
        _write_json_exclusive(authority_path, authority)
        _write_json_exclusive(issue_path, issue)
    authority["authorityPath"] = str(authority_path)
    authority["issuePath"] = str(issue_path)
    authority["issueSha256"] = issue["issueSha256"]
    _append_runtime_recovery_authority_ledger(
        runtime_root=runtime_root,
        authority=authority,
        issue=issue,
    )
    return authority


def _journal_from_runtime_recovery_authority(
    authority: dict[str, object], *, runtime_dirty: bool, runtime_head: str
) -> dict[str, object]:
    return {
        "schemaVersion": RUNTIME_RECOVERY_SCHEMA,
        "transactionId": authority["transactionId"],
        "phase": "prepared",
        "originSha": authority["originSha"],
        "sourceCommonDir": authority["sourceCommonDir"],
        "runtimePath": authority["runtimePath"],
        "quarantinePath": authority["quarantinePath"],
        "authorityPath": authority["authorityPath"],
        "authoritySha256": authority["authoritySha256"],
        "issuePath": authority["issuePath"],
        "issueSha256": authority["issueSha256"],
        "transactionPath": authority["transactionPath"],
        "replacementStagingPath": authority["replacementStagingPath"],
        "publishOrTerminalAmbiguous": False,
        "events": [],
        "preparedObservations": {
            "runtimeDirty": runtime_dirty,
            "runtimeHead": runtime_head,
        },
    }


def _runtime_state(runtime: Path, origin_sha: str) -> dict[str, object]:
    if not runtime.exists():
        return {"exists": False, "clean": False, "head": "", "unexpected": []}
    inside = _run_git(runtime, "rev-parse", "--is-inside-work-tree")
    if inside != "true":
        raise RuntimeError("PRODUCTION_RUNTIME_IDENTITY_INVALID")
    head = _run_git(runtime, "rev-parse", "HEAD")
    tracked_raw = _run_git(
        runtime, "diff", "--ignore-cr-at-eol", "--name-only", "-z", "HEAD"
    )
    untracked_raw = _run_git(
        runtime, "ls-files", "--others", "--exclude-standard", "-z"
    )
    tracked_entries = [item for item in tracked_raw.split("\x00") if item]
    untracked_entries = [item for item in untracked_raw.split("\x00") if item]
    if len(tracked_entries) + len(untracked_entries) > MAX_UNTRACKED_PATHS:
        raise RuntimeError("PRODUCTION_RUNTIME_UNTRACKED_OVERFLOW")
    unexpected = []
    for item in tracked_entries + untracked_entries:
        path = item.replace("\\", "/")
        while path.startswith("./"):
            path = path[2:]
        if item in untracked_entries and (
            path in {".venv", "node_modules"}
            or path.startswith(".venv/")
            or path.startswith("node_modules/")
        ):
            # runtime dependency bindings are managed, reproducible links rather
            # than generation payload.  Repositories that do not ignore these
            # names must still remain clean for generation verification.
            continue
        if not item.startswith("build/"):
            if path.startswith("build/") or path.startswith("data/gate_attempts/") or path.startswith("data/search_audit/"):
                continue
            unexpected.append(path)
    return {
        "exists": True,
        "clean": not unexpected,
        "head": head,
        "headMatches": head == origin_sha,
        "unexpected": unexpected,
    }


def _discard_owned_partial_staging(
    *, source_repo: Path, staging_runtime: Path, transaction_dir: Path, runtime_root: Path
) -> None:
    """transactionが所有する壊れたstagingだけを安全に捨て、再作成可能にする。"""
    expected = transaction_dir / "replacement-staging" / "production-runtime"
    if staging_runtime != expected:
        raise RuntimeError("PRODUCTION_RUNTIME_REPLACEMENT_INVALID")
    if not (staging_runtime.exists() or staging_runtime.is_symlink()):
        return
    _assert_managed_path(staging_runtime, runtime_root, "PRODUCTION_RUNTIME_REPARSE_INVALID")
    if staging_runtime.is_symlink() or not staging_runtime.is_dir():
        raise RuntimeError("PRODUCTION_RUNTIME_REPLACEMENT_INVALID")
    try:
        for root, dirs, files in os.walk(staging_runtime, topdown=True, followlinks=False):
            for name in list(dirs) + list(files):
                candidate = Path(root) / name
                _assert_managed_path(candidate, runtime_root, "PRODUCTION_RUNTIME_REPARSE_INVALID")
                if candidate.is_symlink():
                    raise RuntimeError("PRODUCTION_RUNTIME_REPARSE_INVALID")
    except OSError as error:
        raise RuntimeError("PRODUCTION_RUNTIME_REPLACEMENT_INVALID") from error
    # git metadataを先に整合させる。未登録・半端なaddは失敗してもowned pathを除去する。
    try:
        _run_git(source_repo, "worktree", "remove", "--force", str(staging_runtime), allowed_codes=(0, 1))
    except RuntimeError:
        pass
    if staging_runtime.exists():
        try:
            shutil.rmtree(staging_runtime)
        except OSError as error:
            raise RuntimeError("PRODUCTION_RUNTIME_REPLACEMENT_INVALID") from error


def _discard_owned_partial_runtime(
    *, source_repo: Path, runtime: Path, runtime_root: Path
) -> None:
    """promotion途中に生成されたruntimeだけを、reparse拒否後に除去する。"""
    expected = runtime_root / "production-runtime"
    if runtime != expected:
        raise RuntimeError("PRODUCTION_RUNTIME_REPLACEMENT_INVALID")
    if not (runtime.exists() or runtime.is_symlink()):
        return
    _assert_managed_path(runtime, runtime_root, "PRODUCTION_RUNTIME_REPARSE_INVALID")
    if runtime.is_symlink() or not runtime.is_dir():
        raise RuntimeError("PRODUCTION_RUNTIME_REPLACEMENT_INVALID")
    try:
        for root, dirs, files in os.walk(runtime, topdown=True, followlinks=False):
            for name in list(dirs) + list(files):
                candidate = Path(root) / name
                _assert_managed_path(candidate, runtime_root, "PRODUCTION_RUNTIME_REPARSE_INVALID")
                if candidate.is_symlink():
                    raise RuntimeError("PRODUCTION_RUNTIME_REPARSE_INVALID")
    except OSError as error:
        raise RuntimeError("PRODUCTION_RUNTIME_REPLACEMENT_INVALID") from error
    try:
        _run_git(source_repo, "worktree", "remove", "--force", str(runtime), allowed_codes=(0, 1))
    except RuntimeError:
        pass
    if runtime.exists():
        try:
            shutil.rmtree(runtime)
        except OSError as error:
            raise RuntimeError("PRODUCTION_RUNTIME_REPLACEMENT_INVALID") from error


def _load_runtime_recovery_journal(path: Path, runtime_root: Path) -> dict[str, object]:
    try:
        if path.stat().st_size > 256 * 1024:
            raise ValueError("oversized")
        journal = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError) as error:
        raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_JOURNAL_INVALID") from error
    if not isinstance(journal, dict):
        raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_JOURNAL_INVALID")
    transaction_id = str(journal.get("transactionId") or "")
    if not RUNTIME_TRANSACTION_ID.fullmatch(transaction_id):
        raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_JOURNAL_INVALID")
    authority = _load_runtime_recovery_authority(
        transaction_id=transaction_id,
        runtime_root=runtime_root,
    )
    expected_runtime = runtime_root / "production-runtime"
    expected_quarantine = runtime_root / "quarantine" / transaction_id / "production-runtime"
    expected_transaction = runtime_root / "transactions" / transaction_id
    expected_staging = expected_transaction / "replacement-staging" / "production-runtime"
    if (
        journal.get("schemaVersion") != RUNTIME_RECOVERY_SCHEMA
        or journal.get("phase") not in RUNTIME_RECOVERY_PHASES
        or Path(str(journal.get("runtimePath") or "")) != expected_runtime
        or Path(str(journal.get("quarantinePath") or "")) != expected_quarantine
        or journal.get("originSha") != authority["originSha"]
        or journal.get("sourceCommonDir") != authority["sourceCommonDir"]
        or journal.get("authorityPath") != authority["authorityPath"]
        or journal.get("authoritySha256") != authority["authoritySha256"]
        or journal.get("issuePath") != authority["issuePath"]
        or journal.get("issueSha256") != authority["issueSha256"]
        or Path(str(journal.get("transactionPath") or "")) != expected_transaction
        or Path(str(journal.get("replacementStagingPath") or "")) != expected_staging
        or journal.get("publishOrTerminalAmbiguous") is not False
        or not isinstance(journal.get("events"), list)
        or len(journal["events"]) > 32
    ):
        raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_JOURNAL_INVALID")
    _assert_managed_path(expected_runtime, runtime_root, "PRODUCTION_RUNTIME_REPARSE_INVALID")
    _assert_managed_path(expected_quarantine, runtime_root, "PRODUCTION_RUNTIME_REPARSE_INVALID")
    previous = "0" * 64
    previous_phase_index = -1
    for sequence, event in enumerate(journal["events"], start=1):
        if not isinstance(event, dict):
            raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_JOURNAL_INVALID")
        unsigned = dict(event)
        event_hash = str(unsigned.pop("eventSha256", ""))
        if (
            event.get("sequence") != sequence
            or event.get("previousEventSha256") != previous
            or event.get("phase") not in RUNTIME_RECOVERY_PHASES
            or _sha256_json(unsigned) != event_hash
        ):
            raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_JOURNAL_INVALID")
        phase_index = RUNTIME_RECOVERY_PHASES.index(str(event["phase"]))
        if phase_index != previous_phase_index + 1:
            raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_JOURNAL_INVALID")
        previous_phase_index = phase_index
        previous = event_hash
    if not journal["events"] or journal["phase"] != journal["events"][-1]["phase"]:
        raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_JOURNAL_INVALID")
    return journal


def _append_runtime_recovery_event(
    journal_path: Path,
    journal: dict[str, object],
    *,
    phase: str,
    observations: dict[str, object],
) -> None:
    if phase not in RUNTIME_RECOVERY_PHASES:
        raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_PHASE_INVALID")
    events = list(journal.get("events") or [])
    previous = str(events[-1]["eventSha256"]) if events else "0" * 64
    event: dict[str, object] = {
        "sequence": len(events) + 1,
        "phase": phase,
        "previousEventSha256": previous,
        "observedAtUtc": datetime.now(timezone.utc).isoformat(),
        "observations": observations,
    }
    event["eventSha256"] = _sha256_json(event)
    events.append(event)
    journal["events"] = events
    journal["phase"] = phase
    journal["updatedAtUtc"] = event["observedAtUtc"]
    _write_json_atomic(journal_path, journal)


def _archive_runtime_recovery_temp_files(
    *, transaction_dir: Path, archive_dir: Path, runtime_root: Path
) -> None:
    candidates = [item for item in transaction_dir.iterdir() if item.is_file()]
    temp_files = [item for item in candidates if item.name != "runtime-recovery.json"]
    if len(temp_files) > 8:
        raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_TEMP_OVERFLOW")
    pattern = re.compile(r"^runtime-recovery\.json\.tmp\.[0-9]+\.[0-9a-f]{32}$")
    for temp in temp_files:
        if not pattern.fullmatch(temp.name):
            raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_TEMP_INVALID")
        _assert_managed_path(
            temp,
            runtime_root,
            "PRODUCTION_RUNTIME_RECOVERY_TEMP_INVALID",
        )
        try:
            if temp.stat().st_size > 256 * 1024:
                raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_TEMP_INVALID")
            payload = temp.read_bytes()
        except OSError as error:
            raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_TEMP_INVALID") from error
        payload_sha256 = hashlib.sha256(payload).hexdigest()
        destination = archive_dir / f"orphaned-write-{payload_sha256}.tmp"
        _assert_managed_path(
            destination,
            runtime_root,
            "PRODUCTION_RUNTIME_RECOVERY_TEMP_INVALID",
        )
        if destination.exists():
            if destination.read_bytes() != payload:
                raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_TEMP_DIVERGED")
            temp.unlink()
        else:
            temp.replace(destination)


def _archive_committed_runtime_recovery(
    *, journal_path: Path, journal: dict[str, object], runtime_root: Path
) -> Path:
    if journal.get("phase") != "committed":
        raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_ARCHIVE_INVALID")
    transaction_id = str(journal["transactionId"])
    archive_dir = runtime_root / "quarantine" / transaction_id
    archive_path = archive_dir / "runtime-recovery.json"
    _assert_managed_path(
        archive_path,
        runtime_root,
        "PRODUCTION_RUNTIME_REPARSE_INVALID",
    )
    archive_dir.mkdir(parents=True, exist_ok=True)
    _assert_managed_path(
        archive_dir,
        runtime_root,
        "PRODUCTION_RUNTIME_REPARSE_INVALID",
    )
    if archive_path.exists():
        try:
            actual = json.loads(archive_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, TypeError) as error:
            raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_ARCHIVE_INVALID") from error
        if _sha256_json(actual) != _sha256_json(journal):
            raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_ARCHIVE_DIVERGED")
        if journal_path.exists():
            journal_path.unlink()
    else:
        journal_path.replace(archive_path)
    archived_bytes = archive_path.read_bytes()
    terminal_path = _runtime_recovery_terminal_path(runtime_root, transaction_id)
    terminal_path.parent.mkdir(parents=True, exist_ok=True)
    terminal: dict[str, object] = {
        "schemaVersion": "NEWS_GRASP_PRODUCTION_RUNTIME_RECOVERY_TERMINAL_V1",
        "transactionId": transaction_id,
        "finalJournalSha256": hashlib.sha256(archived_bytes).hexdigest(),
        "archivePath": str(archive_path),
        "authoritySha256": journal["authoritySha256"],
        "issuePath": journal["issuePath"],
        "issueSha256": journal["issueSha256"],
        "committedAtUtc": datetime.now(timezone.utc).isoformat(),
    }
    terminal["terminalSha256"] = _sha256_json(terminal)
    if terminal_path.exists():
        actual_terminal = _load_runtime_recovery_terminal(
            transaction_id=transaction_id, runtime_root=runtime_root
        )
        for key in ("transactionId", "finalJournalSha256", "archivePath", "authoritySha256", "issuePath", "issueSha256"):
            if actual_terminal.get(key) != terminal.get(key):
                raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_TERMINAL_REPLAY")
    else:
        _write_json_exclusive(terminal_path, terminal)
    _archive_runtime_recovery_temp_files(
        transaction_dir=journal_path.parent,
        archive_dir=archive_dir,
        runtime_root=runtime_root,
    )
    staging_root = journal_path.parent / "replacement-staging"
    staging_runtime = staging_root / "production-runtime"
    for empty_dir in (staging_runtime, staging_root):
        if empty_dir.exists() or empty_dir.is_symlink():
            _assert_managed_path(
                empty_dir,
                runtime_root,
                "PRODUCTION_RUNTIME_RECOVERY_ARCHIVE_INVALID",
            )
            try:
                empty_dir.rmdir()
            except OSError as error:
                raise RuntimeError(
                    "PRODUCTION_RUNTIME_RECOVERY_ARCHIVE_INVALID"
                ) from error
    try:
        journal_path.parent.rmdir()
    except OSError as error:
        raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_ARCHIVE_INVALID") from error
    return archive_path


def _create_runtime_dependency_binding(source: Path, target: Path) -> None:
    if target.exists() or target.is_symlink():
        try:
            if target.resolve(strict=True) == source.resolve(strict=True):
                return
        except OSError:
            pass
        raise RuntimeError("PRODUCTION_RUNTIME_DEPENDENCY_DRIFT")
    if sys.platform == "win32":
        escaped_target = str(target).replace("'", "''")
        escaped_source = str(source).replace("'", "''")
        command = (
            f"New-Item -ItemType Junction -Path '{escaped_target}' "
            f"-Target '{escaped_source}' -ErrorAction Stop | Out-Null"
        )
        completed = subprocess.run(
            [
                r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                command,
            ],
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError("PRODUCTION_RUNTIME_DEPENDENCY_BIND_FAILED")
    else:
        target.symlink_to(source, target_is_directory=True)


def _bind_runtime_dependencies(source_repo: Path, runtime: Path) -> None:
    for name in (".venv", "node_modules"):
        source = source_repo / name
        if not source.exists():
            continue
        _create_runtime_dependency_binding(source, runtime / name)


def _detach_runtime_dependency_junctions(source_repo: Path, runtime: Path) -> None:
    """Windows worktree move前に、管理対象junction自体だけを解除する。"""
    if sys.platform != "win32":
        return
    for name in (".venv", "node_modules"):
        target = runtime / name
        if not target.exists() and not target.is_symlink():
            continue
        source = source_repo / name
        try:
            if (
                not target.is_junction()
                or not source.is_dir()
                or target.resolve(strict=True) != source.resolve(strict=True)
            ):
                raise RuntimeError("PRODUCTION_RUNTIME_DEPENDENCY_DRIFT")
            # Path.rmdir on a Windows junction removes the junction entry only;
            # it never traverses or deletes the dependency target.
            target.rmdir()
        except RuntimeError:
            raise
        except OSError as error:
            raise RuntimeError("PRODUCTION_RUNTIME_DEPENDENCY_UNBIND_FAILED") from error


def _assert_runtime_recovery_capacity(runtime_root: Path) -> None:
    collections = (
        runtime_root / "transactions",
        runtime_root / "authorities",
        runtime_root / "ledger",
        runtime_root / "quarantine",
    )
    total_bytes = 0
    total_entries = 0
    for collection in collections:
        if not collection.exists():
            continue
        try:
            for root, dirs, files in os.walk(collection, topdown=True, followlinks=False):
                root_path = Path(root)
                _assert_managed_path(root_path, runtime_root, "PRODUCTION_RUNTIME_REPARSE_INVALID")
                # quarantine/transaction下のproduction-runtimeは製品payloadであり、
                # metadata quotaの対象外。ただしそれ自体はreparse拒否する。
                relative_parts = root_path.relative_to(collection).parts
                is_quarantine_payload = (
                    collection.name == "quarantine"
                    and len(relative_parts) == 2
                    and RUNTIME_TRANSACTION_ID.fullmatch(relative_parts[0]) is not None
                    and relative_parts[1] == "production-runtime"
                )
                is_transaction_payload = (
                    collection.name == "transactions"
                    and len(relative_parts) == 3
                    and RUNTIME_TRANSACTION_ID.fullmatch(relative_parts[0]) is not None
                    and relative_parts[1:] == ("replacement-staging", "production-runtime")
                )
                if is_quarantine_payload or is_transaction_payload:
                    dirs[:] = []
                    continue
                for name in list(dirs) + list(files):
                    item = root_path / name
                    _assert_managed_path(item, runtime_root, "PRODUCTION_RUNTIME_REPARSE_INVALID")
                    if item.is_symlink():
                        raise RuntimeError("PRODUCTION_RUNTIME_REPARSE_INVALID")
                    total_entries += 1
                    if item.is_file():
                        total_bytes += item.stat().st_size
        except (OSError, RuntimeError) as error:
            raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_MAINTENANCE_REQUIRED") from error
    if total_entries >= MAX_RUNTIME_RECOVERY_SCAN_ENTRIES:
        raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_MAINTENANCE_REQUIRED")
    transaction_count = sum(
        1 for item in (runtime_root / "transactions").iterdir()
        if item.is_dir() and not item.is_symlink()
    )
    if transaction_count >= MAX_RUNTIME_RECOVERY_TRANSACTIONS:
        raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_MAINTENANCE_REQUIRED")
    for bounded in (
        runtime_root / "authorities",
        runtime_root / "ledger" / "issues",
        runtime_root / "ledger" / "terminals",
    ):
        try:
            if sum(1 for item in bounded.iterdir() if not item.is_symlink()) >= RUNTIME_LEDGER_MAX_ENTRIES:
                raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_MAINTENANCE_REQUIRED")
        except FileNotFoundError:
            continue
        except OSError as error:
            raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_MAINTENANCE_REQUIRED") from error
    if total_bytes >= RUNTIME_RECOVERY_METADATA_MAX_BYTES:
        raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_MAINTENANCE_REQUIRED")
    try:
        if shutil.disk_usage(runtime_root).free < RUNTIME_RECOVERY_MIN_FREE_BYTES:
            raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_MAINTENANCE_REQUIRED")
    except OSError as error:
        raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_MAINTENANCE_REQUIRED") from error


def _resume_runtime_recovery_checkpoint_deletions(
    *, runtime_root: Path, archive_root: Path, manifest_path: Path
) -> None:
    """checkpointのpending deletionを次回maintenanceでforward完了する。"""
    if not manifest_path.is_file():
        return
    try:
        lines = manifest_path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as error:
        raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_MAINTENANCE_REQUIRED") from error
    completed_ids = set()
    pending: list[dict[str, object]] = []
    for line in lines:
        try:
            value = json.loads(line)
        except (TypeError, ValueError):
            continue
        if not isinstance(value, dict) or value.get("schemaVersion") != "NEWS_GRASP_RUNTIME_RECOVERY_CHECKPOINT_V1":
            continue
        unsigned = dict(value)
        record_sha = str(unsigned.pop("recordSha256", ""))
        if record_sha != _sha256_json(unsigned):
            raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_MAINTENANCE_REQUIRED")
        transaction_id = str(value.get("transactionId") or "")
        if value.get("deletionState") == "completed":
            completed_ids.add(transaction_id)
        elif value.get("deletionState") == "pending":
            pending.append(value)
    for value in pending:
        transaction_id = str(value.get("transactionId") or "")
        if transaction_id in completed_ids:
            continue
        bundle_path = Path(str(value.get("bundlePath") or ""))
        _assert_managed_path(bundle_path, archive_root, "PRODUCTION_RUNTIME_REPARSE_INVALID")
        if not bundle_path.is_file():
            raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_MAINTENANCE_REQUIRED")
        expected_bundle_sha = str(value.get("bundleSha256") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", expected_bundle_sha):
            raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_MAINTENANCE_REQUIRED")
        if hashlib.sha256(bundle_path.read_bytes()).hexdigest() != expected_bundle_sha:
            raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_MAINTENANCE_REQUIRED")
        files = value.get("files")
        if not isinstance(files, list):
            raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_MAINTENANCE_REQUIRED")
        for item in files:
            if not isinstance(item, dict):
                raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_MAINTENANCE_REQUIRED")
            path = Path(str(item.get("path") or ""))
            _assert_managed_path(path, runtime_root, "PRODUCTION_RUNTIME_REPARSE_INVALID")
            if path.exists():
                if hashlib.sha256(path.read_bytes()).hexdigest() != str(item.get("sha256") or ""):
                    raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_MAINTENANCE_REQUIRED")
                _remove_runtime_path(path)
        transaction_dir = runtime_root / "transactions" / transaction_id
        quarantine_dir = runtime_root / "quarantine" / transaction_id
        if transaction_dir.exists():
            _remove_runtime_path(transaction_dir)
        if quarantine_dir.exists():
            _assert_managed_path(quarantine_dir, runtime_root, "PRODUCTION_RUNTIME_REPARSE_INVALID")
            _remove_runtime_path(quarantine_dir)
        completed_record = dict(value)
        completed_record["deletionState"] = "completed"
        completed_record["completedAtUtc"] = datetime.now(timezone.utc).isoformat()
        completed_record["recordSha256"] = _sha256_json({k: v for k, v in completed_record.items() if k != "recordSha256"})
        with manifest_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(completed_record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
            stream.flush()
            os.fsync(stream.fileno())


def maintain_production_runtime_recovery(
    *, runtime_root: Path, max_archives: int = 48
) -> dict[str, object]:
    """完了済みmetadataをruntime外のhash-preserving archiveへcheckpointする。"""
    runtime_root = Path(os.path.abspath(os.fspath(runtime_root)))
    archive_root = runtime_root.parent / ".news-grasp-runtime-recovery-archive"
    archive_root.mkdir(parents=True, exist_ok=True)
    _assert_managed_path(archive_root, runtime_root.parent, "PRODUCTION_RUNTIME_REPARSE_INVALID")
    manifest_path = archive_root / "manifest.jsonl"
    _resume_runtime_recovery_checkpoint_deletions(
        runtime_root=runtime_root, archive_root=archive_root, manifest_path=manifest_path
    )
    candidates: list[tuple[datetime, str, Path]] = []
    quarantine_root = runtime_root / "quarantine"
    for item in quarantine_root.iterdir() if quarantine_root.exists() else ():
        if not item.is_dir() or item.is_symlink() or not RUNTIME_TRANSACTION_ID.fullmatch(item.name):
            continue
        archive_path = item / "runtime-recovery.json"
        terminal_path = runtime_root / "ledger" / "terminals" / f"{item.name}.json"
        if not archive_path.is_file() or not terminal_path.is_file():
            continue
        terminal = _load_runtime_recovery_terminal(transaction_id=item.name, runtime_root=runtime_root)
        stamp = str(terminal.get("committedAtUtc") or "")
        try:
            committed_at = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError:
            committed_at = datetime.min.replace(tzinfo=timezone.utc)
        candidates.append((committed_at, item.name, item))
    candidates.sort()
    archived: list[str] = []
    for _, transaction_id, quarantine_dir in candidates[: max(0, int(max_archives))]:
        files = [
            quarantine_dir / "runtime-recovery.json",
            runtime_root / "authorities" / f"{transaction_id}.json",
            runtime_root / "ledger" / "issues" / f"{transaction_id}.json",
            runtime_root / "ledger" / "terminals" / f"{transaction_id}.json",
        ]
        if not all(path.is_file() for path in files):
            continue
        bundle_path = archive_root / f"{transaction_id}.zip"
        temporary = archive_root / f".{transaction_id}.{uuid4().hex}.tmp"
        manifest_files: list[dict[str, object]] = []
        with zipfile.ZipFile(temporary, "x", compression=zipfile.ZIP_DEFLATED) as bundle:
            for path in files:
                payload = path.read_bytes()
                member = path.name if path.parent == quarantine_dir else f"{path.parent.name}/{path.name}"
                bundle.writestr(member, payload)
                manifest_files.append({"path": str(path), "sha256": hashlib.sha256(payload).hexdigest()})
        temporary.replace(bundle_path)
        # 原本削除前に、昇格後ZIPを再読込して全member hashとfile hashを確定する。
        try:
            with zipfile.ZipFile(bundle_path, "r") as bundle:
                for row in manifest_files:
                    member = Path(str(row["path"])).name if Path(str(row["path"])).parent == quarantine_dir else f"{Path(str(row['path'])).parent.name}/{Path(str(row['path'])).name}"
                    if hashlib.sha256(bundle.read(member)).hexdigest() != str(row["sha256"]):
                        raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_MAINTENANCE_REQUIRED")
                # Windowsでは読み取り専用ハンドルをFlushFileBuffers/fsyncへ渡せないため、
                # 既存bundleを読み書き可能ハンドルで開いて永続化を確認する。
                with bundle_path.open("r+b") as persisted:
                    os.fsync(persisted.fileno())
        except (OSError, KeyError, ValueError, zipfile.BadZipFile) as error:
            raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_MAINTENANCE_REQUIRED") from error
        bundle_sha256 = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
        record: dict[str, object] = {
            "schemaVersion": "NEWS_GRASP_RUNTIME_RECOVERY_CHECKPOINT_V1",
            "transactionId": transaction_id,
            "bundlePath": str(bundle_path),
            "files": manifest_files,
            "bundleSha256": bundle_sha256,
            "checkpointedAtUtc": datetime.now(timezone.utc).isoformat(),
            "deletionState": "pending",
        }
        record["recordSha256"] = _sha256_json(record)
        with manifest_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        for path in files:
            _assert_managed_path(path, runtime_root, "PRODUCTION_RUNTIME_REPARSE_INVALID")
            _remove_runtime_path(path)
        transaction_dir = runtime_root / "transactions" / transaction_id
        if transaction_dir.exists():
            _remove_runtime_path(transaction_dir)
        _remove_runtime_path(quarantine_dir)
        completed_record = dict(record)
        completed_record["deletionState"] = "completed"
        completed_record["completedAtUtc"] = datetime.now(timezone.utc).isoformat()
        completed_record["recordSha256"] = _sha256_json({k: v for k, v in completed_record.items() if k != "recordSha256"})
        with manifest_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(completed_record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        archived.append(transaction_id)
    if archived:
        projection = _runtime_recovery_authority_ledger_path(runtime_root)
        try:
            lines = projection.read_text(encoding="utf-8-sig").splitlines()
            kept: list[str] = []
            archived_set = set(archived)
            for line in lines:
                try:
                    value = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if not isinstance(value, dict) or value.get("transactionId") not in archived_set:
                    kept.append(line)
            temporary = projection.with_name(f".{projection.name}.{uuid4().hex}.tmp")
            temporary.write_text("".join(f"{line}\n" for line in kept), encoding="utf-8", newline="\n")
            temporary.replace(projection)
        except OSError as error:
            raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_MAINTENANCE_REQUIRED") from error
    return {
        "schemaVersion": "NEWS_GRASP_RUNTIME_RECOVERY_MAINTENANCE_V1",
        "status": "Green",
        "archivedTransactionIds": archived,
        "archiveRoot": str(archive_root),
    }


def _ensure_runtime_recovery_capacity(runtime_root: Path) -> None:
    try:
        _assert_runtime_recovery_capacity(runtime_root)
    except RuntimeError as error:
        if str(error) != "PRODUCTION_RUNTIME_RECOVERY_MAINTENANCE_REQUIRED":
            raise
        maintain_production_runtime_recovery(runtime_root=runtime_root)
        _assert_runtime_recovery_capacity(runtime_root)


def converge_production_runtime(
    *, source_repo: Path, runtime_root: Path, origin_sha: str, bin_dir: Path | None = None
) -> dict[str, object]:
    # direct import/callもbootstrapのlifecycle ownerと同じmutexへ束縛する。
    # これによりCLIのowner receipt検査を迂回した二重writerを許可しない。
    with _production_runtime_lifecycle_mutex():
        with _production_runtime_outer_mutex():
            with _production_runtime_mutex():
                return _converge_production_runtime_locked(
                    source_repo=source_repo,
                    runtime_root=runtime_root,
                    origin_sha=origin_sha,
                    bin_dir=bin_dir,
                )


def _converge_production_runtime_locked(
    *, source_repo: Path, runtime_root: Path, origin_sha: str, bin_dir: Path | None = None
) -> dict[str, object]:
    """dirty runtimeを無損失隔離し、固定SHAのclean worktreeへforward収束する。"""
    source_repo = source_repo.resolve(strict=True)
    runtime_root = Path(os.path.abspath(os.fspath(runtime_root)))
    if not re.fullmatch(r"[0-9a-fA-F]{40}", origin_sha):
        raise RuntimeError("PRODUCTION_RUNTIME_ORIGIN_SHA_INVALID")
    origin_sha = origin_sha.lower()
    runtime_root.mkdir(parents=True, exist_ok=True)
    _assert_managed_path(runtime_root, runtime_root, "PRODUCTION_RUNTIME_REPARSE_INVALID")
    runtime = runtime_root / "production-runtime"
    transactions = runtime_root / "transactions"
    quarantine_root = runtime_root / "quarantine"
    authorities = runtime_root / "authorities"
    ledger = runtime_root / "ledger"
    ledger_issues = ledger / "issues"
    ledger_terminals = ledger / "terminals"
    transactions.mkdir(exist_ok=True)
    quarantine_root.mkdir(exist_ok=True)
    authorities.mkdir(exist_ok=True)
    ledger_issues.mkdir(parents=True, exist_ok=True)
    ledger_terminals.mkdir(parents=True, exist_ok=True)
    for managed in (transactions, quarantine_root, authorities, ledger, ledger_issues, ledger_terminals, runtime):
        _assert_managed_path(managed, runtime_root, "PRODUCTION_RUNTIME_REPARSE_INVALID")
    _ensure_runtime_recovery_capacity(runtime_root)

    source_common = _git_common_dir(source_repo)
    transaction_dirs = [item for item in transactions.iterdir() if item.is_dir()]
    if len(transaction_dirs) > MAX_RUNTIME_RECOVERY_SCAN_ENTRIES:
        raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_SCAN_OVERFLOW")
    active: list[tuple[Path, dict[str, object]]] = []
    for transaction_dir in sorted(transaction_dirs, key=lambda item: item.name):
        if not RUNTIME_TRANSACTION_ID.fullmatch(transaction_dir.name):
            raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_TRANSACTION_INVALID")
        _assert_managed_path(
            transaction_dir,
            runtime_root,
            "PRODUCTION_RUNTIME_REPARSE_INVALID",
        )
        journal_path = transaction_dir / "runtime-recovery.json"
        archived_path = (
            quarantine_root / transaction_dir.name / "runtime-recovery.json"
        )
        if not journal_path.exists() and archived_path.exists():
            archived = _load_runtime_recovery_journal(archived_path, runtime_root)
            if (
                archived.get("transactionId") != transaction_dir.name
                or archived.get("phase") != "committed"
            ):
                raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_ARCHIVE_INVALID")
            _ensure_runtime_recovery_terminal_from_archive(
                runtime_root=runtime_root,
                archive_path=archived_path,
                journal=archived,
            )
            _archive_runtime_recovery_temp_files(
                transaction_dir=transaction_dir,
                archive_dir=archived_path.parent,
                runtime_root=runtime_root,
            )
            transaction_dir.rmdir()
            continue
        _assert_no_runtime_recovery_terminal(
            transaction_id=transaction_dir.name, runtime_root=runtime_root
        )
        if not journal_path.exists():
            authority = _load_runtime_recovery_authority(
                transaction_id=transaction_dir.name,
                runtime_root=runtime_root,
            )
            orphan_state = _runtime_state(runtime, origin_sha)
            journal = _journal_from_runtime_recovery_authority(
                authority,
                runtime_dirty=bool(orphan_state.get("exists")),
                runtime_head=str(orphan_state.get("head") or ""),
            )
            _append_runtime_recovery_event(
                journal_path,
                journal,
                phase="prepared",
                observations=dict(journal.pop("preparedObservations")),
            )
        journal = _load_runtime_recovery_journal(journal_path, runtime_root)
        if journal["transactionId"] != transaction_dir.name:
            raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_JOURNAL_INVALID")
        if journal["phase"] == "committed":
            _archive_committed_runtime_recovery(
                journal_path=journal_path,
                journal=journal,
                runtime_root=runtime_root,
            )
            continue
        active.append((journal_path, journal))
    if len(active) > 1:
        raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_MULTIPLE_ACTIVE")

    if active:
        journal_path, journal = active[0]
        active_origin_sha = str(journal.get("originSha") or "").lower()
        if str(journal.get("sourceCommonDir") or "") != str(source_common):
            raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_GENERATION_DRIFT")
        if active_origin_sha != origin_sha:
            if not re.fullmatch(r"[0-9a-f]{40}", active_origin_sha):
                raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_GENERATION_DRIFT")
            try:
                _run_git(
                    source_repo,
                    "merge-base",
                    "--is-ancestor",
                    active_origin_sha,
                    origin_sha,
                )
            except RuntimeError as error:
                raise RuntimeError(
                    "PRODUCTION_RUNTIME_RECOVERY_GENERATION_DRIFT"
                ) from error
            # writerが進んだ場合も、immutable authorityに束縛された旧transactionを
            # 先に終端してから、要求された新generationへ一度だけforwardする。
            _converge_production_runtime_locked(
                source_repo=source_repo,
                runtime_root=runtime_root,
                origin_sha=active_origin_sha,
                # 旧transactionはimmutable terminalまで閉じるが、既にremote
                # mainではない世代をactive pointerへ再昇格しない。
                bin_dir=None,
            )
            return _converge_production_runtime_locked(
                source_repo=source_repo,
                runtime_root=runtime_root,
                origin_sha=origin_sha,
                bin_dir=bin_dir,
            )
    else:
        _assert_runtime_common_dir(runtime, source_common)
        state = _runtime_state(runtime, origin_sha)
        if state["exists"] and state["clean"]:
            if not state["headMatches"]:
                _run_git(runtime, "checkout", "--detach", origin_sha, "--quiet")
            _assert_runtime_common_dir(runtime, source_common)
            _bind_runtime_dependencies(source_repo, runtime)
            result: dict[str, object] = {
                "phase": "committed",
                "runtimePath": str(runtime),
                "quarantinePath": "",
                "originSha": origin_sha,
            }
            if bin_dir is not None:
                result["activeGeneration"] = _seal_active_production_generation(
                    source_repo=source_repo,
                    runtime_repo=runtime,
                    runtime_root=runtime_root,
                    origin_sha=origin_sha,
                    bin_dir=bin_dir,
                )
            return result
        _ensure_runtime_recovery_capacity(runtime_root)
        transaction_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ-") + uuid4().hex[:16]
        transaction_dir = transactions / transaction_id
        authority = _issue_runtime_recovery_authority(
            transaction_id=transaction_id,
            runtime_root=runtime_root,
            origin_sha=origin_sha,
            source_common=source_common,
        )
        transaction_dir.mkdir()
        journal_path = transaction_dir / "runtime-recovery.json"
        journal = _journal_from_runtime_recovery_authority(
            authority,
            runtime_dirty=bool(state["exists"]),
            runtime_head=str(state["head"]),
        )
        _append_runtime_recovery_event(
            journal_path,
            journal,
            phase="prepared",
            observations=dict(journal.pop("preparedObservations")),
        )
        if not state["exists"]:
            # clean runtimeの初回作成もfinal pathへ直接addせず、transaction-owned
            # stagingからpromotionする。途中終了時はjournalが再開境界になる。
            quarantine_path = Path(str(authority["quarantinePath"]))
            quarantine_path.mkdir(parents=True, exist_ok=True)
        active = [(journal_path, journal)]

    quarantine = Path(str(journal["quarantinePath"]))
    phase = str(journal["phase"])
    runtime_state = _runtime_state(runtime, origin_sha)
    if runtime_state["exists"]:
        _assert_runtime_common_dir(runtime, source_common)
    if quarantine.exists() and runtime_state["exists"]:
        _assert_runtime_common_dir(quarantine, source_common)
    if phase == "prepared":
        _ensure_runtime_recovery_capacity(runtime_root)
        if runtime_state["exists"] and not quarantine.exists():
            quarantine.parent.mkdir(parents=True, exist_ok=True)
            _assert_managed_path(
                quarantine.parent,
                runtime_root,
                "PRODUCTION_RUNTIME_REPARSE_INVALID",
            )
            if any(quarantine.parent.iterdir()):
                raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_STATE_DIVERGED")
            _detach_runtime_dependency_junctions(source_repo, runtime)
            try:
                with _managed_directory_handle(
                    runtime_root, runtime_root, "PRODUCTION_RUNTIME_REPARSE_INVALID"
                ):
                    _run_git(source_repo, "worktree", "move", str(runtime), str(quarantine))
            except (OSError, RuntimeError, ValueError):
                # move失敗時も、実行中runtimeをdependency無しで放置しない。
                _bind_runtime_dependencies(source_repo, runtime)
                raise
        elif not runtime_state["exists"]:
            quarantine.parent.mkdir(parents=True, exist_ok=True)
            if quarantine.exists() and (quarantine / ".git").exists():
                _assert_runtime_common_dir(quarantine, source_common)
            elif quarantine.exists() and any(quarantine.iterdir()):
                raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_STATE_DIVERGED")
            else:
                quarantine.mkdir(parents=True, exist_ok=True)
        else:
            raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_STATE_DIVERGED")
        if runtime_state["exists"]:
            _assert_runtime_common_dir(quarantine, source_common)
        if (quarantine / ".git").exists():
            # move後もrollback可能な隔離世代としてdependencyを戻す。
            # prepared相で中断された場合もここで再構成できる。
            _bind_runtime_dependencies(source_repo, quarantine)
        _run_git(source_repo, "worktree", "repair")
        _append_runtime_recovery_event(
            journal_path,
            journal,
            phase="runtime_quarantined",
            observations={"quarantineExists": True, "runtimeExists": False},
        )
        phase = "runtime_quarantined"

    runtime_state = _runtime_state(runtime, origin_sha)
    if phase == "runtime_quarantined":
        if not quarantine.exists():
            raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_STATE_DIVERGED")
        if (quarantine / ".git").exists():
            _assert_runtime_common_dir(quarantine, source_common)
        staging_runtime = Path(str(journal["replacementStagingPath"]))
        staging_container = staging_runtime.parent
        _assert_managed_path(staging_container, runtime_root, "PRODUCTION_RUNTIME_REPARSE_INVALID")
        staging_container.mkdir(parents=True, exist_ok=True)
        if staging_runtime.exists() or staging_runtime.is_symlink():
            _assert_managed_path(staging_runtime, runtime_root, "PRODUCTION_RUNTIME_REPARSE_INVALID")
            try:
                existing_staging_state = _runtime_state(staging_runtime, origin_sha)
            except (OSError, RuntimeError, ValueError):
                _discard_owned_partial_staging(
                    source_repo=source_repo,
                    staging_runtime=staging_runtime,
                    transaction_dir=staging_container.parent,
                    runtime_root=runtime_root,
                )
                existing_staging_state = {"exists": False, "clean": False, "headMatches": False}
            if existing_staging_state.get("exists") and (
                not existing_staging_state.get("clean") or not existing_staging_state.get("headMatches")
            ):
                _discard_owned_partial_staging(
                    source_repo=source_repo,
                    staging_runtime=staging_runtime,
                    transaction_dir=staging_container.parent,
                    runtime_root=runtime_root,
                )
        if not runtime_state["exists"]:
            if not staging_runtime.exists():
                _run_git(source_repo, "worktree", "add", "--detach", str(staging_runtime), origin_sha)
            staging_state = _runtime_state(staging_runtime, origin_sha)
        else:
            staging_state = {"exists": False, "clean": False, "headMatches": False}
        if staging_state.get("exists") and (
            not staging_state.get("clean") or not staging_state.get("headMatches")
        ):
            raise RuntimeError("PRODUCTION_RUNTIME_REPLACEMENT_INVALID")
        _append_runtime_recovery_event(
            journal_path,
            journal,
            phase="replacement_created",
            observations={"runtimeHead": str(staging_state.get("head") or ""), "stagingPath": str(staging_runtime)},
        )
        phase = "replacement_created"

    if phase == "replacement_created":
        staging_runtime = Path(str(journal["replacementStagingPath"]))
        promotion_intent = journal.get("promotionIntent")
        if promotion_intent is not None and not isinstance(promotion_intent, dict):
            raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_JOURNAL_INVALID")
        if promotion_intent is not None and runtime.exists() and staging_runtime.exists():
            try:
                runtime_state_now = _runtime_state(runtime, origin_sha)
                staging_state_now = _runtime_state(staging_runtime, origin_sha)
            except (OSError, RuntimeError, ValueError):
                runtime_state_now = {"exists": True, "clean": False, "headMatches": False}
                staging_state_now = {"exists": True, "clean": False, "headMatches": False}
            if (
                runtime_state_now.get("clean") and runtime_state_now.get("headMatches")
                and staging_state_now.get("clean") and staging_state_now.get("headMatches")
                and runtime_state_now.get("head") == staging_state_now.get("head")
            ):
                _discard_owned_partial_staging(
                    source_repo=source_repo,
                    staging_runtime=staging_runtime,
                    transaction_dir=staging_runtime.parent.parent,
                    runtime_root=runtime_root,
                )
            elif not runtime_state_now.get("clean") or not runtime_state_now.get("headMatches"):
                _discard_owned_partial_runtime(
                    source_repo=source_repo, runtime=runtime, runtime_root=runtime_root
                )
        elif promotion_intent is not None and runtime.exists():
            try:
                runtime_state_now = _runtime_state(runtime, origin_sha)
            except (OSError, RuntimeError, ValueError):
                runtime_state_now = {"exists": True, "clean": False, "headMatches": False}
            if not runtime_state_now.get("clean") or not runtime_state_now.get("headMatches"):
                _discard_owned_partial_runtime(
                    source_repo=source_repo, runtime=runtime, runtime_root=runtime_root
                )
        if not runtime.exists() and staging_runtime.exists():
            _ensure_runtime_recovery_capacity(runtime_root)
            journal["promotionIntent"] = {
                "sourcePath": str(staging_runtime),
                "destinationPath": str(runtime),
                "originSha": origin_sha,
                "startedAtUtc": datetime.now(timezone.utc).isoformat(),
            }
            _write_json_atomic(journal_path, journal)
            with _managed_directory_handle(
                runtime_root, runtime_root, "PRODUCTION_RUNTIME_REPARSE_INVALID"
            ):
                _run_git(source_repo, "worktree", "move", str(staging_runtime), str(runtime))
        if not runtime.exists() and not staging_runtime.exists():
            staging_runtime.parent.mkdir(parents=True, exist_ok=True)
            journal["promotionIntent"] = {
                "sourcePath": str(staging_runtime),
                "destinationPath": str(runtime),
                "originSha": origin_sha,
                "startedAtUtc": datetime.now(timezone.utc).isoformat(),
            }
            _write_json_atomic(journal_path, journal)
            _run_git(source_repo, "worktree", "add", "--detach", str(staging_runtime), origin_sha)
        if not runtime.exists():
            raise RuntimeError("PRODUCTION_RUNTIME_REPLACEMENT_INVALID")
        _assert_runtime_common_dir(runtime, source_common)
        _bind_runtime_dependencies(source_repo, runtime)
        if "promotionIntent" in journal:
            journal.pop("promotionIntent", None)
            _write_json_atomic(journal_path, journal)
        _append_runtime_recovery_event(
            journal_path,
            journal,
            phase="dependencies_bound",
            observations={"dependencyNames": [".venv", "node_modules"]},
        )
        phase = "dependencies_bound"

    if phase == "dependencies_bound":
        final_state = _runtime_state(runtime, origin_sha)
        if (
            not final_state["clean"]
            or not final_state["headMatches"]
            or _git_common_dir(runtime) != source_common
            or not quarantine.exists()
        ):
            raise RuntimeError("PRODUCTION_RUNTIME_FINAL_VERIFY_FAILED")
        _append_runtime_recovery_event(
            journal_path,
            journal,
            phase="committed",
            observations={"runtimeHead": final_state["head"], "quarantinePreserved": True},
        )

    archived_journal = _archive_committed_runtime_recovery(
        journal_path=journal_path,
        journal=journal,
        runtime_root=runtime_root,
    )

    result = {
        "phase": str(journal["phase"]),
        "runtimePath": str(runtime),
        "quarantinePath": str(quarantine),
        "journalPath": str(archived_journal),
        "originSha": origin_sha,
    }
    if bin_dir is not None:
        result["activeGeneration"] = _seal_active_production_generation(
            source_repo=source_repo,
            runtime_repo=runtime,
            runtime_root=runtime_root,
            origin_sha=origin_sha,
            bin_dir=bin_dir,
        )
    return result


def resolve_bootstrap_launch_roots(
    *, bin_dir: Path, enforce_canonical_runtime: bool = False
) -> dict[str, Path]:
    """missingでもよいconfigured runtimeと、実bootstrap sourceを分離する。"""
    runtime_config = bin_dir / "news-grasp-runtime-root-v1.json"
    try:
        config = json.loads(runtime_config.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError) as error:
        raise RuntimeError("NEWS_GRASP_RUNTIME_CONFIG_INVALID") from error
    if (
        set(config) != {"schemaVersion", "repoDir", "pythonExe", "evidenceRepoDir"}
        or config.get("schemaVersion") != "NEWS_GRASP_RUNTIME_ROOT_V1"
    ):
        raise RuntimeError("NEWS_GRASP_RUNTIME_CONFIG_INVALID")
    configured_runtime = Path(os.path.abspath(str(config["repoDir"])))
    expected_runtime = Path.home() / ".news-grasp-runtime" / "production-runtime"
    if enforce_canonical_runtime and configured_runtime != expected_runtime:
        raise RuntimeError("NEWS_GRASP_RUNTIME_CONFIG_INVALID")
    evidence = Path(str(config["evidenceRepoDir"])).resolve(strict=True)
    python_exe = Path(str(config["pythonExe"])).resolve(strict=True)
    if not (evidence / "tools" / "daily_self_heal.py").is_file() or not python_exe.is_file():
        raise RuntimeError("NEWS_GRASP_RUNTIME_CONFIG_INVALID")
    return {
        "repoDir": evidence,
        "pythonExe": python_exe,
        "evidenceRepoDir": evidence,
        "configuredRuntime": configured_runtime,
    }


def _pre_attempt_identity(mode: str, script: Path) -> dict[str, object]:
    launch_evidence = {
        "mode": mode,
        "launcherPath": str(Path(__file__).resolve()),
        "scriptPath": str(script.resolve()),
        "processId": os.getpid(),
        "processStartNonce": time.time_ns(),
    }
    launch_key = hashlib.sha256(
        json.dumps(
            launch_evidence,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    root_operation_id = hashlib.sha256(
        f"News-Grasp|{launch_key}|root-operation".encode("utf-8")
    ).hexdigest()
    return {
        "schemaVersion": "NEWS_GRASP_PRE_ATTEMPT_WAL_V1",
        "launchKey": launch_key,
        "rootOperationId": root_operation_id,
        "preAttemptStatus": "launch_reserved",
        "continuationState": "pre_controller_running",
        "walClosed": False,
        "observerReconstructable": True,
        "scheduledRecoveryFullAuthorityProvable": False,
        "launchEvidence": launch_evidence,
    }


def read_runner_launch_evidence(
    path: Path,
    *,
    issue_date: str,
    expected_root: Path,
    expected_min_mtime_ns: int = 0,
) -> dict[str, object]:
    """watcherのtyped child launch evidenceを同じbytesで有界検証する。"""

    root = expected_root.resolve(strict=True)
    candidate = path.resolve(strict=True)
    if candidate.parent != root or path.is_symlink():
        raise ValueError("RUNNER_LAUNCH_EVIDENCE_PATH_INVALID")
    if candidate.stat().st_mtime_ns < expected_min_mtime_ns:
        raise ValueError("RUNNER_LAUNCH_EVIDENCE_STALE")
    raw = candidate.read_bytes()
    if not raw or len(raw) > 65536:
        raise ValueError("RUNNER_LAUNCH_EVIDENCE_SIZE_INVALID")
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError, TypeError) as error:
        raise ValueError("RUNNER_LAUNCH_EVIDENCE_INVALID") from error
    if not isinstance(value, dict) or value.get("schemaVersion") != (
        "NEWS_GRASP_RUNNER_LAUNCH_EVIDENCE_V1"
    ):
        raise ValueError("RUNNER_LAUNCH_EVIDENCE_INVALID")
    if value.get("issueDate") != issue_date or value.get("status") not in {
        "launch_reserved",
        "launched",
        "launch_failed",
        "failed_before_state_claim",
        "failed_after_state_claim",
        "terminal_state_reached",
    }:
        raise ValueError("RUNNER_LAUNCH_EVIDENCE_INVALID")
    for key in ("commandIdentitySha256", "powershellSha256", "runnerSha256"):
        if re.fullmatch(r"[0-9a-f]{64}", str(value.get(key, ""))) is None:
            raise ValueError("RUNNER_LAUNCH_EVIDENCE_INVALID")
    if (
        not isinstance(value.get("processId"), int)
        or not isinstance(value.get("childExitCode"), int)
        or not isinstance(value.get("stateClaimed"), bool)
        or not str(value.get("reasonCode", ""))
    ):
        raise ValueError("RUNNER_LAUNCH_EVIDENCE_INVALID")
    return {
        "path": str(candidate),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "status": str(value["status"]),
        "reasonCode": str(value["reasonCode"]),
        "childExitCode": int(value["childExitCode"]),
        "stateClaimed": bool(value["stateClaimed"]),
        "processId": int(value["processId"]),
        "commandIdentitySha256": str(value["commandIdentitySha256"]),
        "powershellSha256": str(value["powershellSha256"]),
        "runnerSha256": str(value["runnerSha256"]),
    }


def _owned_process_identity(process: subprocess.Popen[bytes], executable: Path) -> dict[str, object]:
    """起動境界が保持するprocess handleから終端owner identityを取得する。"""
    identity: dict[str, object] = {
        "pid": int(process.pid),
        "creationTime": str(time.time_ns()),
        "imagePath": str(executable.resolve(strict=True)),
        "imageSha256": _file_sha256(executable),
    }
    if sys.platform != "win32":
        return identity
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(process.pid))
    if not handle:
        raise RuntimeError("E2E_RUNNER_PROCESS_IDENTITY_UNAVAILABLE")
    try:
        class _FileTime(ctypes.Structure):
            _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]

        created = _FileTime()
        exited = _FileTime()
        kernel = _FileTime()
        user = _FileTime()
        if not kernel32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)):
            raise RuntimeError("E2E_RUNNER_PROCESS_IDENTITY_UNAVAILABLE")
        image_buffer = ctypes.create_unicode_buffer(32768)
        image_length = ctypes.c_uint32(len(image_buffer))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, image_buffer, ctypes.byref(image_length)):
            raise RuntimeError("E2E_RUNNER_PROCESS_IDENTITY_UNAVAILABLE")
        creation_ticks = (int(created.high) << 32) | int(created.low)
        identity["creationTime"] = (
            datetime(1601, 1, 1, tzinfo=timezone.utc)
            + timedelta(microseconds=creation_ticks / 10)
        ).isoformat(timespec="microseconds").replace("+00:00", "Z")
        identity["imagePath"] = str(Path(image_buffer.value).resolve(strict=True))
        identity["imageSha256"] = _file_sha256(Path(identity["imagePath"]))
        return identity
    finally:
        kernel32.CloseHandle(handle)


def _write_runner_terminal_authority(
    *,
    policy_path: Path,
    attempt: int,
    admission_path: Path,
    runner_arguments_path: Path,
    runner_state_path: Path,
    claim_path: Path,
    process_identity: dict[str, object],
    runner_exit_code: int,
    child_launch_evidence: dict[str, object],
    ledger_path: Path | None = None,
) -> Path:
    """installed launcherだけが実runner終端authorityを発行する。"""
    policy = policy_path.resolve(strict=True)
    admission = admission_path.resolve(strict=True)
    arguments = runner_arguments_path.resolve(strict=True)
    state = runner_state_path.resolve(strict=True)
    claim = claim_path.resolve(strict=True)
    try:
        admission_value = json.loads(admission.read_text(encoding="utf-8-sig"))
        root = Path(str(admission_value["repoRoot"])).resolve(strict=True)
    except (KeyError, OSError, TypeError, ValueError) as error:
        raise RuntimeError("E2E_RUNNER_TERMINAL_AUTHORITY_INVALID") from error
    for candidate in (admission, arguments, state, claim):
        if not candidate.is_relative_to(root) or candidate.is_symlink() or not candidate.is_file():
            raise RuntimeError("E2E_RUNNER_TERMINAL_AUTHORITY_PATH_INVALID")
    raw = state.read_bytes()
    if len(raw) > 65536:
        raise RuntimeError("E2E_RUNNER_TERMINAL_AUTHORITY_INVALID")
    try:
        state_value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError, TypeError) as error:
        raise RuntimeError("E2E_RUNNER_TERMINAL_AUTHORITY_INVALID") from error
    if (
        not isinstance(state_value, dict)
        or state_value.get("status") != "publish_dry_run_ok"
        or int(state_value.get("exit_code", -1)) != int(runner_exit_code)
        or int(runner_exit_code) != 0
        or child_launch_evidence.get("status") != "terminal_state_reached"
        or int(child_launch_evidence.get("childExitCode", -1)) != int(runner_exit_code)
        or str(state_value.get("e2eFinalAdmissionPath")) != str(admission)
        or str(state_value.get("e2eFinalRunnerArgumentsPath")) != str(arguments)
    ):
        raise RuntimeError("E2E_RUNNER_TERMINAL_AUTHORITY_INVALID")
    try:
        claim_value = json.loads(claim.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError) as error:
        raise RuntimeError("E2E_RUNNER_TERMINAL_AUTHORITY_INVALID") from error
    claim_owner = claim_value.get("ownerProcessIdentity") if isinstance(claim_value, dict) else None
    if (
        not isinstance(claim_owner, dict)
        or not claim_owner.get("pid")
        or not claim_owner.get("creationFileTimeUtc")
        or claim_value.get("admissionPath") != str(admission)
        or claim_value.get("runnerArgumentsPath") != str(arguments)
    ):
        raise RuntimeError("E2E_RUNNER_TERMINAL_AUTHORITY_CLAIM_INVALID")
    if (
        int(process_identity.get("pid", -1)) != int(claim_owner.get("pid", -2))
        or str(process_identity.get("creationTime")) != str(claim_owner.get("creationFileTimeUtc"))
        or os.path.normcase(os.path.abspath(str(process_identity.get("imagePath"))))
        != os.path.normcase(os.path.abspath(str(claim_owner.get("imagePath"))))
        or process_identity.get("imageSha256") != claim_owner.get("imageSha256")
    ):
        raise RuntimeError("E2E_RUNNER_TERMINAL_AUTHORITY_OWNER_INVALID")
    try:
        import tools.e2e_final_admission_bridge as bridge
        canonical_ledger = (ledger_path or bridge.default_attempt_ledger_path()).resolve()
        ledger_value, ledger_hash = bridge._ledger_snapshot(canonical_ledger)
        ledger_row = ledger_value.get("attempts", {}).get(str(claim_value.get("attemptKey")))
        if (
            not isinstance(ledger_row, dict)
            or ledger_row.get("state") != "runner_claimed"
            or ledger_row.get("claimReceiptPath") != str(claim)
            or ledger_row.get("claimReceiptSha256") != claim_value.get("receiptSha256")
            or ledger_row.get("ownerProcessIdentity") != claim_owner
        ):
            raise RuntimeError("E2E_RUNNER_TERMINAL_AUTHORITY_LEDGER_INVALID")
    except RuntimeError:
        raise
    except Exception as error:
        raise RuntimeError(f"E2E_RUNNER_TERMINAL_AUTHORITY_LEDGER_INVALID:{error}") from error
    unsigned: dict[str, object] = {
        "schemaVersion": "NEWS_GRASP_E2E_RUNNER_TERMINAL_AUTHORITY_V1",
        "attempt": int(attempt),
        "admissionPath": str(admission),
        "admissionSha256": _file_sha256(admission),
        "runnerArgumentsPath": str(arguments),
        "runnerArgumentsSha256": _file_sha256(arguments),
        "claimPath": str(claim),
        "claimSha256": str(claim_value.get("receiptSha256") or ""),
        "claimOwnerProcessIdentity": claim_owner,
        "ledgerPath": str(canonical_ledger),
        "ledgerSha256": ledger_hash or "",
        "statePath": str(state),
        "stateSha256": hashlib.sha256(raw).hexdigest(),
        "runnerExitCode": int(runner_exit_code),
        "runnerStatus": str(state_value["status"]),
        "ownerProcessIdentity": claim_owner,
        "launcherProcessIdentity": process_identity,
        "childLaunchEvidenceSha256": hashlib.sha256(
            json.dumps(child_launch_evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "producerPath": str(Path(__file__).resolve()),
        "producerSha256": _file_sha256(Path(__file__).resolve()),
    }
    unsigned["authoritySha256"] = _sha256_json(unsigned)
    output = policy.parent / f"e2e-terminal-authority-{int(attempt)}.json"
    if output.exists():
        try:
            if json.loads(output.read_text(encoding="utf-8-sig")) != unsigned:
                raise RuntimeError("E2E_RUNNER_TERMINAL_AUTHORITY_DRIFT")
        except (OSError, ValueError, TypeError) as error:
            raise RuntimeError("E2E_RUNNER_TERMINAL_AUTHORITY_DRIFT") from error
    else:
        _write_json_exclusive(output, unsigned)
    return output


def record_missing_pre_attempt_from_task_history(
    task_evidence: dict[str, object],
) -> dict[str, object]:
    """launcher/broker未到達時のidentityをTask Scheduler一次証拠から復元する。"""
    required = ("Execute", "Arguments", "LastRunTime", "LastTaskResult")
    if any(task_evidence.get(field) in {None, ""} for field in required):
        raise ValueError("TASK_HISTORY_EVIDENCE_INCOMPLETE")
    evidence = {field: task_evidence[field] for field in required}
    launch_key = hashlib.sha256(
        json.dumps(
            evidence,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    root_operation_id = hashlib.sha256(
        f"News-Grasp|{launch_key}|root-operation".encode("utf-8")
    ).hexdigest()
    failed = int(task_evidence["LastTaskResult"]) != 0
    return {
        "schemaVersion": "NEWS_GRASP_TASK_HISTORY_PRE_ATTEMPT_V1",
        "launchKey": launch_key,
        "rootOperationId": root_operation_id,
        "preAttemptStatus": (
            "failed_before_attempt" if failed else "task_action_completed"
        ),
        "scheduledRecoveryFullAuthorityProvable": failed,
        "callerAttemptIdentityAccepted": False,
        "taskEvidenceSha256": hashlib.sha256(
            json.dumps(
                evidence,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        choices=(
            "runner",
            "bootstrap",
            "converge-runtime",
            "maintain-runtime",
            "scheduled-equivalent-nopublish",
            "dispatch",
            "task-origin-canary",
            "task-origin-child-probe",
        ),
    )
    parser.add_argument("--repo-dir", type=Path)
    parser.add_argument("--python-exe", type=Path)
    parser.add_argument("--evidence-repo-dir", type=Path)
    parser.add_argument("--source-repo", type=Path)
    parser.add_argument("--origin-sha")
    parser.add_argument("--bootstrap-owner-pid", type=int)
    parser.add_argument("--bootstrap-owner-receipt", type=Path)
    parser.add_argument("--bootstrap-owner-nonce")
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--scheduled-task-name", required=False)
    parser.add_argument("--launch-authority", type=Path)
    parser.add_argument("--high-cost-binding-path", type=Path)
    parser.add_argument("--high-cost-binding-sha256")
    parser.add_argument("--schedule-id")
    parser.add_argument("--intent")
    parser.add_argument("--canary-nonce")
    parser.add_argument("--canary-generation", "--generation", dest="canary_generation")
    parser.add_argument(
        "--canary-receipt-path",
        "--receipt-path",
        dest="canary_receipt_path",
        type=Path,
    )
    args = parser.parse_args()
    bin_dir = Path.home() / "bin"
    if args.mode == "task-origin-child-probe":
        if (
            not args.canary_nonce
            or not args.canary_generation
            or args.repo_dir is not None
            or args.python_exe is not None
            or args.evidence_repo_dir is not None
            or args.source_repo is not None
            or args.origin_sha is not None
            or args.bootstrap_owner_pid is not None
            or args.bootstrap_owner_receipt is not None
            or args.bootstrap_owner_nonce is not None
            or args.runtime_root is not None
            or args.scheduled_task_name is not None
            or args.launch_authority is not None
            or args.high_cost_binding_path is not None
            or args.high_cost_binding_sha256
            or args.schedule_id is not None
            or args.intent is not None
            or args.canary_receipt_path is not None
        ):
            parser.error(
                "task-origin-child-probe requires only --canary-generation and --canary-nonce"
            )
        try:
            probe_path = _write_cleanroom_child_probe(
                generation=str(args.canary_generation),
                nonce=str(args.canary_nonce),
            )
        except (OSError, RuntimeError, ValueError, TypeError) as error:
            print(
                json.dumps(
                    {"status": "failed", "reasonCode": str(error)},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
            return 66
        print(
            json.dumps(
                {"status": "probe_ok", "probePath": str(probe_path)},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    if args.mode == "task-origin-canary":
        if (
            not args.canary_nonce
            or not args.canary_generation
            or args.schedule_id is not None
            or args.intent is not None
            or args.repo_dir is not None
            or args.python_exe is not None
            or args.evidence_repo_dir is not None
            or args.bootstrap_owner_pid is not None
            or args.bootstrap_owner_receipt is not None
            or args.bootstrap_owner_nonce is not None
            or args.scheduled_task_name is not None
            or args.launch_authority is not None
            or args.high_cost_binding_path is not None
            or args.high_cost_binding_sha256
        ):
            parser.error(
                "task-origin-canary requires only nonce/generation/receipt/runtime-root"
            )
        try:
            nonce = str(args.canary_nonce)
            generation = _cleanroom_canary_generation(args.canary_generation)
            observed = datetime.now(_CLEANROOM_TOKYO)
            runtime_root = Path(args.runtime_root or (Path.home() / ".news-grasp-runtime" / "production-runtime"))
            manifest_path = _task_topology_manifest_path(runtime_root)
            receipt_path = args.canary_receipt_path or (
                bin_dir / f"news-grasp-entry-canary-{nonce}.json"
            )
            result = run_task_origin_canary(
                task_action=lambda **_context: True,
                start_task=lambda **_context: True,
                nonce=nonce,
                wait_receipt=lambda **_context: None,
                restore_task=lambda **_context: True,
                final_parity=lambda **_context: True,
                task_origin_validator=_cleanroom_default_task_origin_validator,
                observed_at=observed,
                generation=generation,
                bin_dir=bin_dir,
                runtime_root=runtime_root,
                manifest_path=manifest_path,
                receipt_path=receipt_path,
                manage_task=False,
            )
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0 if result.get("status") == "verified" else 66
        except (OSError, RuntimeError, ValueError, TypeError) as error:
            print(
                json.dumps(
                    {"status": "failed", "reasonCode": str(error)},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
            return 66
    if args.mode == "dispatch":
        if (
            args.schedule_id is None
            or args.intent is None
            or args.high_cost_binding_path is not None
            or args.high_cost_binding_sha256
            or args.repo_dir is not None
            or args.python_exe is not None
            or args.evidence_repo_dir is not None
            or args.bootstrap_owner_pid is not None
            or args.bootstrap_owner_receipt is not None
            or args.bootstrap_owner_nonce is not None
            or args.runtime_root is not None
            or args.scheduled_task_name is not None
            or args.launch_authority is not None
        ):
            parser.error("dispatch requires only --schedule-id and --intent")
        try:
            return run_cleanroom_dispatch(
                args.schedule_id,
                args.intent,
                bin_dir=bin_dir,
                observed_at=datetime.now(_CLEANROOM_TOKYO),
            )
        except (OSError, RuntimeError, ValueError, TypeError) as error:
            print(
                json.dumps(
                    {"status": "failed", "reasonCode": str(error)},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
            return 66
    if args.schedule_id is not None or args.intent is not None:
        parser.error("--schedule-id/--intent are valid only for dispatch")
    if args.mode == "maintain-runtime":
        runtime_root = args.runtime_root or (Path.home() / ".news-grasp-runtime")
        try:
            with _production_runtime_lifecycle_mutex():
                with _production_runtime_outer_mutex():
                    with _production_runtime_mutex():
                        result = maintain_production_runtime_recovery(runtime_root=runtime_root)
        except (OSError, RuntimeError, ValueError) as error:
            print(json.dumps({"status": "failed", "reasonCode": str(error)}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
            return 72
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    if args.mode == "converge-runtime":
        if (
            args.source_repo is None
            or not args.origin_sha
            or args.bootstrap_owner_pid is None
            or args.bootstrap_owner_receipt is None
            or not args.bootstrap_owner_nonce
        ):
            return 66
        try:
            with _production_runtime_outer_mutex():
                _require_bootstrap_runtime_mutex_owner(
                    args.bootstrap_owner_pid,
                    owner_receipt_path=args.bootstrap_owner_receipt,
                    owner_nonce=str(args.bootstrap_owner_nonce),
                )
                with _production_runtime_mutex():
                    result = _converge_production_runtime_locked(
                        source_repo=args.source_repo,
                        runtime_root=Path.home() / ".news-grasp-runtime",
                        origin_sha=str(args.origin_sha),
                        bin_dir=bin_dir,
                    )
        except (OSError, RuntimeError, ValueError) as error:
            print(
                json.dumps(
                    {
                        "schemaVersion": RUNTIME_RECOVERY_SCHEMA,
                        "status": "failed",
                        "reasonCode": str(error),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
            return 72
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    try:
        launcher_identity = _load_stable_launcher_identity(bin_dir=bin_dir)
    except (OSError, RuntimeError, ValueError) as error:
        print(
            json.dumps(
                {"status": "failed", "reasonCode": str(error)},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 66
    if args.mode in {"runner", "bootstrap"}:
        try:
            bound_path = _stable_authority_option(
                launcher_identity, "--high-cost-binding-path"
            )
            bound_sha256 = _stable_authority_option(
                launcher_identity, "--high-cost-binding-sha256"
            )
            if (
                args.high_cost_binding_path is None
                or not args.high_cost_binding_sha256
                or args.high_cost_binding_path.resolve(strict=True)
                != Path(bound_path).resolve(strict=True)
                or str(args.high_cost_binding_sha256).lower() != bound_sha256.lower()
            ):
                raise RuntimeError("HIGH_COST_IDENTITY_DRIFT")
        except (OSError, RuntimeError, ValueError):
            return 66
    if args.mode == "scheduled-equivalent-nopublish":
        if args.launch_authority is None:
            return 66
        try:
            return _run_installed_nopublish_authority(
                authority_path=args.launch_authority.resolve(strict=True),
                bin_dir=bin_dir,
                launcher_identity=launcher_identity,
            )
        except (OSError, RuntimeError, ValueError) as error:
            print(
                json.dumps(
                    {"status": "failed", "reasonCode": str(error)},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
            return 76
    if args.mode in {"runner", "bootstrap"} and any(
        value is not None
        for value in (args.repo_dir, args.python_exe, args.evidence_repo_dir)
    ):
        return 66
    if args.mode not in {"runner", "bootstrap"}:
        return 66
    try:
        resolved_roots = resolve_bootstrap_launch_roots(
            bin_dir=bin_dir,
            enforce_canonical_runtime=True,
        )
        runtime_repo = resolved_roots["configuredRuntime"].resolve(strict=True)
        python_exe = resolved_roots["pythonExe"].resolve(strict=True)
        evidence_repo = resolved_roots["evidenceRepoDir"].resolve(strict=True)
        if args.high_cost_binding_path is None or not args.high_cost_binding_sha256:
            raise RuntimeError("HIGH_COST_IDENTITY_DRIFT")
        binding_path = args.high_cost_binding_path.resolve(strict=True)
        binding_tool = evidence_repo / "tools" / "news_grasp_high_cost_binding.py"
        if not binding_tool.is_file():
            raise RuntimeError("HIGH_COST_BINDING_TOOL_MISSING")
        validation = subprocess.run(
            [
                str(python_exe),
                "-I",
                "-S",
                "-B",
                str(binding_tool),
                "resolve",
                "--binding",
                str(binding_path),
                "--expected-receipt-sha256",
                str(args.high_cost_binding_sha256),
            ],
            cwd=str(evidence_repo),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="backslashreplace",
            timeout=20,
            check=False,
            creationflags=(
                subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            ),
        )
        if validation.returncode != 0:
            return int(validation.returncode)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        return 66

    if args.mode == "bootstrap":
        try:
            _run_git(
                evidence_repo,
                "fetch",
                "--prune",
                "origin",
                "+refs/heads/main:refs/remotes/origin/main",
                timeout_seconds=120,
            )
            origin_sha = _run_git(
                evidence_repo,
                "rev-parse",
                "--verify",
                "origin/main^{commit}",
                timeout_seconds=20,
            ).lower()
            if not re.fullmatch(r"[0-9a-f]{40}", origin_sha):
                raise RuntimeError("PRODUCTION_RUNTIME_ORIGIN_SHA_INVALID")
            with _production_runtime_lifecycle_mutex():
                with _production_runtime_outer_mutex():
                    with _production_runtime_mutex():
                        convergence = _converge_production_runtime_locked(
                            source_repo=evidence_repo,
                            runtime_root=Path.home() / ".news-grasp-runtime",
                            origin_sha=origin_sha,
                            bin_dir=bin_dir,
                        )
                        maintenance = maintain_production_runtime_recovery(
                            runtime_root=Path.home() / ".news-grasp-runtime"
                        )
            runtime_repo = Path(str(convergence["runtimePath"])).resolve(strict=True)
            _validate_active_production_generation(
                runtime_repo=runtime_repo,
                launcher_identity=launcher_identity,
            )
            _write_json_atomic(
                bin_dir / "ng-smoke-state.json",
                {
                    "schemaVersion": "NEWS_GRASP_DIRECT_BOOTSTRAP_RECEIPT_V1",
                    "status": "smoke_ok",
                    "route": "direct",
                    "runtimePath": str(runtime_repo),
                    "originSha": origin_sha,
                    "convergence": convergence,
                    "maintenance": maintenance,
                    "externalEffectCount": 0,
                },
            )
            return 0
        except (OSError, RuntimeError, ValueError, TypeError) as error:
            print(
                json.dumps(
                    {"status": "failed", "reasonCode": str(error)},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
            return 72

    direct_entry = runtime_repo / "tools" / "news_grasp_daily_launcher.py"
    failure_state = bin_dir / "news-grasp-runner-state.json"
    wal = bin_dir / "news-grasp-task-launcher-wal.json"
    issue_date = date.today().isoformat()
    try:
        if not direct_entry.is_file():
            raise RuntimeError("NEWS_GRASP_DIRECT_DAILY_ENTRY_MISSING")
        _validate_active_production_generation(
            runtime_repo=runtime_repo,
            launcher_identity=launcher_identity,
        )
        pre_attempt = _pre_attempt_identity(args.mode, direct_entry)
        _write_json_atomic(wal, pre_attempt)
        command = [
            str(python_exe),
            "-I",
            "-S",
            "-B",
            str(direct_entry),
        ]
        safety = {
            "route": "runner",
            "command": tuple(command),
            "cwd": str(runtime_repo),
            "shell": False,
            "stdin": subprocess.DEVNULL,
            "creationflags": subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            "close_fds": True,
            "timeout": _CLEANROOM_CHILD_TIMEOUT_SECONDS,
            "environment": {
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUTF8": "1",
                "NEWS_GRASP_REPO_ROOT": str(runtime_repo),
            },
        }
        effective_returncode = _run_cleanroom_child(
            "runner",
            command,
            bin_dir=bin_dir,
            safety=safety,
        )
    except (OSError, RuntimeError, ValueError, TypeError) as error:
        effective_returncode = 66
        print(
            json.dumps(
                {"status": "failed", "reasonCode": str(error)},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        pre_attempt = _pre_attempt_identity(args.mode, direct_entry)
    if effective_returncode != 0:
        freeze_startup_failure_if_needed(
            state_path=failure_state,
            returncode=effective_returncode,
            issue_date=issue_date,
            detail=f"DIRECT_DAILY_LAUNCH_FAILED exit={effective_returncode}",
        )
    pre_attempt.update(
        {
            "childReturnCode": effective_returncode,
            "preAttemptStatus": (
                "controller_started" if effective_returncode == 0 else "failed_before_attempt"
            ),
            "continuationState": (
                "controller_owns_continuation"
                if effective_returncode == 0
                else "scheduled_recovery_required"
            ),
            "walClosed": True,
            "scheduledRecoveryFullAuthorityProvable": effective_returncode != 0,
        }
    )
    _write_json_atomic(wal, pre_attempt)
    return effective_returncode


if __name__ == "__main__":
    raise SystemExit(main())
