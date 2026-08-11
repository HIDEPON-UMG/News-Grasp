from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import zipfile
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4


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
NEWS_GRASP_TASK_CONTEXT_REJECTED_EXIT = 67


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
    temp.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temp.replace(path)


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
        or len(action) < 3
        or any(not isinstance(item, str) or not item for item in action)
        or stable_path != launcher
        or Path(str(action[1])).resolve() != launcher
        or str(authority.get("stableLauncherSha256") or "") != _file_sha256(launcher)
        or any(item.casefold() in {"--repo-dir", "--worktree"} for item in action)
    ):
        raise RuntimeError("NEWS_GRASP_STABLE_TASK_AUTHORITY_INVALID")
    return {
        **authority,
        "authorityPath": str(authority_path.resolve()),
        "authorityFileSha256": _file_sha256(authority_path),
    }


def _validate_active_production_generation(
    *, runtime_repo: Path, launcher_identity: dict[str, object]
) -> dict[str, object]:
    """active pointer・immutable manifest・runtime bytesを同一generationへ束縛する。"""
    active_pointer_path = runtime_repo.parent / "active-generation-v2.json"
    try:
        active_pointer = json.loads(active_pointer_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as error:
        raise RuntimeError("NEWS_GRASP_ACTIVE_GENERATION_INVALID") from error
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
    runtime_manifest = manifest.get("runtime")
    tracked_files = runtime_manifest.get("trackedFiles") if isinstance(runtime_manifest, dict) else None
    if (
        manifest.get("schemaVersion") != "PRODUCTION_GENERATION_MANIFEST_V2"
        or manifest.get("generationId") != active_pointer.get("generationId")
        or manifest_sha256 != _sha256_json(manifest_unsigned)
        or active_pointer.get("manifestSha256") != manifest_sha256
        or manifest.get("stableTaskAuthoritySha256") != launcher_identity.get("authoritySha256")
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
        "runnerArgumentsPath",
        "runnerArgumentsFileSha256",
        "authoritySha256",
    }
    if set(value) != required or value.get("schemaVersion") != INSTALLED_NOPUBLISH_AUTHORITY_SCHEMA:
        raise RuntimeError("NEWS_GRASP_INSTALLED_NOPUBLISH_AUTHORITY_INVALID")
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
    except OSError as error:
        raise RuntimeError("NEWS_GRASP_INSTALLED_NOPUBLISH_AUTHORITY_INVALID") from error
    if (
        not executable.is_file()
        or executable.is_symlink()
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
    if (
        not isinstance(arguments, list)
        or not arguments
        or any(not isinstance(item, str) or not item for item in arguments)
        or "-NoPublish" not in arguments
        or "-ResumeFromStage" in arguments
    ):
        raise RuntimeError("NEWS_GRASP_INSTALLED_NOPUBLISH_ARGUMENTS_INVALID")
    resolved = resolve_bootstrap_launch_roots(
        bin_dir=bin_dir,
        enforce_canonical_runtime=True,
    )
    runtime_repo = resolved["configuredRuntime"].resolve(strict=True)
    _validate_active_production_generation(
        runtime_repo=runtime_repo,
        launcher_identity=launcher_identity,
    )
    expected_runner = (runtime_repo / "scripts" / "ops" / "news-grasp-runner.ps1").resolve(strict=True)
    try:
        file_index = arguments.index("-File")
        observed_runner = Path(arguments[file_index + 1]).resolve(strict=True)
    except (ValueError, IndexError, OSError) as error:
        raise RuntimeError("NEWS_GRASP_INSTALLED_NOPUBLISH_ARGUMENTS_INVALID") from error
    if observed_runner != expected_runner:
        raise RuntimeError("NEWS_GRASP_INSTALLED_GENERATION_DRIFT")
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    result = subprocess.run(
        [str(executable), *arguments],
        shell=False,
        stdin=subprocess.DEVNULL,
        creationflags=creationflags,
        check=False,
    )
    return int(result.returncode)


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
    critical_paths = (
        "scripts/ops/news-grasp-runner.ps1",
        "tools/daily_self_heal.py",
        "tools/news_grasp_daily_control.py",
        "tools/news_grasp_operational_contract.py",
        "tools/news_grasp_checkpoint.py",
        "tools/news_grasp_generation.py",
        "tools/operational_recovery_registry.py",
        "config/operational_recovery_registry_v1.json",
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
            "observedHead": _run_git(source_repo, "rev-parse", "HEAD").strip().lower(),
            "remoteHead": _run_git(source_repo, "rev-parse", "origin/main").strip().lower(),
            "commonDir": str(_git_common_dir(source_repo)),
            "origin": "origin/main",
        },
        "runtime": {
            "root": str(runtime_repo),
            "commit": _run_git(runtime_repo, "rev-parse", "HEAD").strip().lower(),
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
    if (
        manifest["source"]["commit"] != origin_sha
        or manifest["source"]["remoteHead"] != origin_sha
        or manifest["runtime"]["commit"] != origin_sha
        or manifest["source"]["commonDir"] != manifest["runtime"]["commonDir"]
    ):
        raise RuntimeError("NEWS_GRASP_PRODUCTION_GENERATION_DRIFT")
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


def _run_git(repo: Path, *args: str, allowed_codes: tuple[int, ...] = (0,)) -> str:
    git_exe = Path(r"C:\Program Files\Git\cmd\git.exe")
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    completed = subprocess.run(
        [str(git_exe), "-C", str(repo), *args],
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=creationflags,
        check=False,
    )
    if len(completed.stdout) > MAX_GIT_OUTPUT_BYTES or len(completed.stderr) > MAX_GIT_OUTPUT_BYTES:
        raise RuntimeError("PRODUCTION_RUNTIME_GIT_OUTPUT_OVERFLOW")
    if completed.returncode not in allowed_codes:
        detail = completed.stderr.decode("utf-8", errors="replace")[-1000:]
        raise RuntimeError(
            f"PRODUCTION_RUNTIME_GIT_FAILED exit={completed.returncode} detail={detail}"
        )
    return completed.stdout.decode("utf-8", errors="replace").strip()


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
    diff = subprocess.run(
        [
            r"C:\Program Files\Git\cmd\git.exe",
            "-c",
            "core.hooksPath=NUL",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.attributesFile=NUL",
            "-C",
            str(runtime),
            "diff",
            "--quiet",
            "--no-ext-diff",
            "--ignore-cr-at-eol",
            "HEAD",
            "--",
        ],
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        creationflags=(subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0),
        check=False,
    )
    if diff.returncode not in (0, 1):
        raise RuntimeError("PRODUCTION_RUNTIME_DIFF_FAILED")
    untracked_raw = _run_git(runtime, "ls-files", "--others", "--exclude-standard", "-z")
    untracked = [item.replace("\\", "/") for item in untracked_raw.split("\x00") if item]
    if len(untracked) > MAX_UNTRACKED_PATHS:
        raise RuntimeError("PRODUCTION_RUNTIME_UNTRACKED_OVERFLOW")
    unexpected = [item for item in untracked if not item.startswith("build/")]
    return {
        "exists": True,
        "clean": diff.returncode == 0 and not unexpected,
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


def _bind_runtime_dependencies(source_repo: Path, runtime: Path) -> None:
    for name in (".venv", "node_modules"):
        source = source_repo / name
        target = runtime / name
        if not source.exists():
            continue
        if target.exists() or target.is_symlink():
            try:
                if target.resolve(strict=True) == source.resolve(strict=True):
                    continue
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
            with _managed_directory_handle(
                runtime_root, runtime_root, "PRODUCTION_RUNTIME_REPARSE_INVALID"
            ):
                _run_git(source_repo, "worktree", "move", str(runtime), str(quarantine))
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
        ),
    )
    parser.add_argument("--probe", type=Path)
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
    args = parser.parse_args()
    bin_dir = Path.home() / "bin"
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
    if args.probe:
        args.probe.parent.mkdir(parents=True, exist_ok=True)
        args.probe.write_text("probe_ok", encoding="utf-8")
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
    if args.mode == "runner" and any(
        value is not None
        for value in (args.repo_dir, args.python_exe, args.evidence_repo_dir)
    ):
        return 66
    script = bin_dir / "news-grasp-bootstrap.ps1"
    extra = [
        "-Start", "-UseProductionRuntime", "-ScheduledTaskName", "News-Grasp Runner",
        "-ProductionTaskName", "News-Grasp Production",
    ] if args.mode == "runner" else [
        "-Start", "-UseProductionRuntime", "-ScheduledTaskName", "News-Grasp Bootstrap",
        "-ProductionTaskName", "News-Grasp Production",
        "-SmokeTest",
        "-SkipSourceSync",
        "-PollSeconds", "1", "-TimeoutMinutes", "2",
        "-StateFile", "ng-smoke-state.json", "-LogDir", "ng-smoke-logs",
    ]
    if args.scheduled_task_name:
        extra[extra.index("-ScheduledTaskName") + 1] = args.scheduled_task_name
    runtime_repo = args.repo_dir
    runtime_python = args.python_exe
    runtime_evidence: Path | None = args.evidence_repo_dir
    if runtime_repo is None:
        try:
            resolved_roots = resolve_bootstrap_launch_roots(
                bin_dir=bin_dir,
                enforce_canonical_runtime=True,
            )
        except (OSError, RuntimeError, ValueError):
            return 66
        runtime_repo = (
            resolved_roots["configuredRuntime"]
            if args.mode == "runner"
            else resolved_roots["repoDir"]
        )
        runtime_python = resolved_roots["pythonExe"]
        runtime_evidence = resolved_roots["evidenceRepoDir"]
    if runtime_repo is not None:
        try:
            repo_dir = runtime_repo.resolve(strict=True)
        except OSError:
            return 66
        if args.mode == "runner":
            try:
                _validate_active_production_generation(
                    runtime_repo=repo_dir,
                    launcher_identity=launcher_identity,
                )
            except (OSError, RuntimeError, ValueError):
                return 66
        if not (repo_dir / "tools" / "daily_self_heal.py").is_file():
            return 66
        extra.extend(["-RepoDir", str(repo_dir)])
        if runtime_python is None:
            return 66
        try:
            python_exe = runtime_python.resolve(strict=True)
        except OSError:
            return 66
        if not python_exe.is_file():
            return 66
        extra.extend(["-PythonExe", str(python_exe)])
        if runtime_evidence is None:
            runtime_evidence = repo_dir
        try:
            evidence_repo = runtime_evidence.resolve(strict=True)
        except OSError:
            return 66
        extra.extend(["-EvidenceRepoDir", str(evidence_repo)])
    powershell = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
    issue_date = date.today().isoformat()
    failure_state = bin_dir / (
        "news-grasp-runner-state.json" if args.mode == "runner" else "ng-smoke-state.json"
    )
    if not script.is_file():
        freeze_startup_failure_if_needed(
            state_path=failure_state,
            returncode=66,
            issue_date=issue_date,
            detail="STARTUP_SCRIPT_MISSING",
        )
        return 66
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    log = bin_dir / "news-grasp-task-launcher.log"
    wal = bin_dir / "news-grasp-task-launcher-wal.json"
    pre_attempt = _pre_attempt_identity(args.mode, script)
    _write_json_atomic(wal, pre_attempt)
    with log.open("a", encoding="utf-8", errors="replace") as stream:
        result = subprocess.run(
            [str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script), *extra],
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
            check=False,
        )
    effective_returncode = int(result.returncode)
    context_rejected = (
        args.mode == "bootstrap"
        and effective_returncode == NEWS_GRASP_TASK_CONTEXT_REJECTED_EXIT
    )
    if effective_returncode == 0 and args.mode == "bootstrap":
        state_path = bin_dir / "ng-smoke-state.json"
        try:
            state = json.loads(state_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, TypeError):
            effective_returncode = 73
        else:
            if state.get("status") != "smoke_ok":
                effective_returncode = 73
    if effective_returncode != 0 and not context_rejected:
        freeze_startup_failure_if_needed(
            state_path=failure_state,
            returncode=effective_returncode,
            issue_date=issue_date,
            detail=f"STARTUP_SELF_REPAIR_FAILED exit={effective_returncode}",
        )
    pre_attempt.update(
        {
            "childReturnCode": effective_returncode,
            "preAttemptStatus": (
                "context_rejected"
                if context_rejected
                else (
                    "controller_started"
                    if effective_returncode == 0
                    else "failed_before_attempt"
                )
            ),
            "continuationState": (
                "context_rejected_no_attempt"
                if context_rejected
                else (
                    "controller_owns_continuation"
                    if effective_returncode == 0
                    else "scheduled_recovery_required"
                )
            ),
            "walClosed": True,
            "scheduledRecoveryFullAuthorityProvable": (
                effective_returncode != 0 and not context_rejected
            ),
        }
    )
    _write_json_atomic(wal, pre_attempt)
    return effective_returncode


if __name__ == "__main__":
    raise SystemExit(main())
