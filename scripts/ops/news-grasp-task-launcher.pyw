from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import subprocess
import sys
import time
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
RUNTIME_ORCHESTRATION_MUTEX_PREFIX = "Global\\NewsGraspBootstrapOrchestration-"
RUNTIME_PRODUCTION_MUTEX_PREFIX = "Global\\NewsGraspProductionRuntime-"
RUNTIME_LEGACY_MUTEX_NAME = "Global\\NewsGraspProductionRuntimeConvergence"


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
    mutex_name = f"{RUNTIME_PRODUCTION_MUTEX_PREFIX}{os.environ.get('USERNAME', '')}"
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
    expected_mutex_name = (
        f"{RUNTIME_ORCHESTRATION_MUTEX_PREFIX}{os.environ.get('USERNAME', '')}"
    )
    if (
        receipt.get("schemaVersion") != RUNTIME_LIFECYCLE_OWNER_SCHEMA
        or receipt.get("ownerPid") != owner_pid
        or receipt.get("ownerNonce") != owner_nonce
        or receipt.get("mutexName") != expected_mutex_name
    ):
        raise RuntimeError("PRODUCTION_RUNTIME_MUTEX_OWNER_RECEIPT_INVALID")
    if sys.platform != "win32":
        return
    username = str(os.environ.get("USERNAME") or "")
    if not username:
        raise RuntimeError("PRODUCTION_RUNTIME_MUTEX_OWNER_INVALID")
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
        f"{RUNTIME_ORCHESTRATION_MUTEX_PREFIX}{username}",
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


@contextmanager
def _managed_directory_handle(path: Path, boundary: Path, code: str):
    """検査後のreparse/junction交換を、delete-deny handleの寿命内で封じる。"""
    candidate = _assert_managed_path(path, boundary, code)
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
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    invalid = ctypes.c_void_p(-1).value
    attributes = kernel32.GetFileAttributesW(str(candidate))
    if attributes == 0xFFFFFFFF or attributes & 0x400:
        raise RuntimeError(code)
    handle = kernel32.CreateFileW(
        str(candidate),
        0x80,
        0x3,
        None,
        3,
        0x02000000 | 0x00200000,
        None,
    )
    if handle == invalid or not handle:
        raise RuntimeError(code)
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        length = kernel32.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
        if not length:
            raise RuntimeError(code)
        final_path = str(buffer.value).replace("\\\\?\\", "")
        expected = str(candidate).replace("\\\\?\\", "")
        if os.path.normcase(os.path.abspath(final_path)) != os.path.normcase(
            os.path.abspath(expected)
        ):
            raise RuntimeError(code)
        yield candidate
    finally:
        kernel32.CloseHandle(handle)


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
        runtime_root / "ledger" / "issues",
        runtime_root / "ledger" / "terminals",
    )
    for collection in collections:
        try:
            count = sum(1 for _ in collection.iterdir()) if collection.exists() else 0
        except OSError as error:
            raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_MAINTENANCE_REQUIRED") from error
        if count >= RUNTIME_LEDGER_MAX_ENTRIES:
            raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_MAINTENANCE_REQUIRED")


def converge_production_runtime(
    *, source_repo: Path, runtime_root: Path, origin_sha: str
) -> dict[str, object]:
    with _production_runtime_outer_mutex():
        with _production_runtime_mutex():
            return _converge_production_runtime_locked(
                source_repo=source_repo,
                runtime_root=runtime_root,
                origin_sha=origin_sha,
            )


def _converge_production_runtime_locked(
    *, source_repo: Path, runtime_root: Path, origin_sha: str
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
            terminal_path = _runtime_recovery_terminal_path(
                runtime_root, transaction_dir.name
            )
            if terminal_path.exists() or terminal_path.is_symlink():
                terminal = _load_runtime_recovery_terminal(
                    transaction_id=transaction_dir.name, runtime_root=runtime_root
                )
                if (
                    terminal.get("archivePath") != str(archived_path)
                    or terminal.get("finalJournalSha256")
                    != hashlib.sha256(archived_path.read_bytes()).hexdigest()
                    or terminal.get("authoritySha256") != archived.get("authoritySha256")
                    or terminal.get("issueSha256") != archived.get("issueSha256")
                ):
                    raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_TERMINAL_REPLAY")
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
        if (
            str(journal.get("originSha") or "").lower() != origin_sha
            or str(journal.get("sourceCommonDir") or "") != str(source_common)
        ):
            raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_GENERATION_DRIFT")
    else:
        _assert_runtime_common_dir(runtime, source_common)
        state = _runtime_state(runtime, origin_sha)
        if state["exists"] and state["clean"]:
            if not state["headMatches"]:
                _run_git(runtime, "checkout", "--detach", origin_sha, "--quiet")
            _assert_runtime_common_dir(runtime, source_common)
            _bind_runtime_dependencies(source_repo, runtime)
            return {"phase": "committed", "runtimePath": str(runtime), "quarantinePath": ""}
        if not state["exists"]:
            _run_git(source_repo, "worktree", "add", "--detach", str(runtime), origin_sha)
            _assert_runtime_common_dir(runtime, source_common)
            _bind_runtime_dependencies(source_repo, runtime)
            return {"phase": "committed", "runtimePath": str(runtime), "quarantinePath": ""}
        _assert_runtime_recovery_capacity(runtime_root)
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
            runtime_dirty=True,
            runtime_head=str(state["head"]),
        )
        _append_runtime_recovery_event(
            journal_path,
            journal,
            phase="prepared",
            observations=dict(journal.pop("preparedObservations")),
        )

    quarantine = Path(str(journal["quarantinePath"]))
    phase = str(journal["phase"])
    runtime_state = _runtime_state(runtime, origin_sha)
    if runtime_state["exists"]:
        _assert_runtime_common_dir(runtime, source_common)
    if quarantine.exists():
        _assert_runtime_common_dir(quarantine, source_common)
    if phase == "prepared":
        _assert_runtime_recovery_capacity(runtime_root)
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
        elif runtime_state["exists"] or not quarantine.exists():
            raise RuntimeError("PRODUCTION_RUNTIME_RECOVERY_STATE_DIVERGED")
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
        _assert_runtime_common_dir(quarantine, source_common)
        staging_runtime = Path(str(journal["replacementStagingPath"]))
        staging_container = staging_runtime.parent
        _assert_managed_path(staging_container, runtime_root, "PRODUCTION_RUNTIME_REPARSE_INVALID")
        staging_container.mkdir(parents=True, exist_ok=True)
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
        if not runtime.exists() and staging_runtime.exists():
            _assert_runtime_recovery_capacity(runtime_root)
            with _managed_directory_handle(
                runtime_root, runtime_root, "PRODUCTION_RUNTIME_REPARSE_INVALID"
            ):
                _run_git(source_repo, "worktree", "move", str(staging_runtime), str(runtime))
        if not runtime.exists():
            raise RuntimeError("PRODUCTION_RUNTIME_REPLACEMENT_INVALID")
        _assert_runtime_common_dir(runtime, source_common)
        _bind_runtime_dependencies(source_repo, runtime)
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

    return {
        "phase": str(journal["phase"]),
        "runtimePath": str(runtime),
        "quarantinePath": str(quarantine),
        "journalPath": str(archived_journal),
        "originSha": origin_sha,
    }


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
    parser.add_argument("mode", choices=("runner", "bootstrap", "converge-runtime"))
    parser.add_argument("--probe", type=Path)
    parser.add_argument("--repo-dir", type=Path)
    parser.add_argument("--python-exe", type=Path)
    parser.add_argument("--evidence-repo-dir", type=Path)
    parser.add_argument("--source-repo", type=Path)
    parser.add_argument("--origin-sha")
    parser.add_argument("--bootstrap-owner-pid", type=int)
    parser.add_argument("--bootstrap-owner-receipt", type=Path)
    parser.add_argument("--bootstrap-owner-nonce")
    parser.add_argument("--scheduled-task-name", required=False)
    args = parser.parse_args()
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
    bin_dir = Path.home() / "bin"
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
        runtime_repo = resolved_roots["repoDir"]
        runtime_python = resolved_roots["pythonExe"]
        runtime_evidence = resolved_roots["evidenceRepoDir"]
    if runtime_repo is not None:
        try:
            repo_dir = runtime_repo.resolve(strict=True)
        except OSError:
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
    if effective_returncode == 0 and args.mode == "bootstrap":
        state_path = bin_dir / "ng-smoke-state.json"
        try:
            state = json.loads(state_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, TypeError):
            effective_returncode = 73
        else:
            if state.get("status") != "smoke_ok":
                effective_returncode = 73
    if effective_returncode != 0:
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
                "controller_started"
                if effective_returncode == 0
                else "failed_before_attempt"
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
