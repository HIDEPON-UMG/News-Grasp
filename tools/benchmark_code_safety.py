"""モデル生成Pythonをローカル評価する前の最小信頼境界。"""
from __future__ import annotations

import ast
import ctypes
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Mapping, Sequence


_BANNED_CALLS = {
    "__import__",
    "breakpoint",
    "compile",
    "delattr",
    "eval",
    "exec",
    "getattr",
    "globals",
    "help",
    "input",
    "locals",
    "open",
    "print",
    "setattr",
    "vars",
}
_BANNED_ATTRIBUTE_CALLS = {
    "connect",
    "open",
    "popen",
    "remove",
    "rename",
    "replace",
    "rmdir",
    "run",
    "spawn",
    "system",
    "unlink",
    "urlopen",
    "write_bytes",
    "write_text",
}
_BANNED_NAMES = {
    "builtins",
    "ctypes",
    "importlib",
    "os",
    "pathlib",
    "shutil",
    "socket",
    "subprocess",
    "sys",
}


def validate_benchmark_python(source: str) -> None:
    """依存なし課題に不要なI/O・import・dunder経路を拒否する。"""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ValueError(f"unsafe benchmark code: syntax_error:{exc.msg}") from exc
    problems: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            problems.append("import_forbidden")
        elif isinstance(node, ast.While):
            problems.append("while_forbidden")
        elif isinstance(node, ast.Name):
            if node.id.startswith("__") or node.id in _BANNED_NAMES:
                problems.append(f"name_forbidden:{node.id}")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                problems.append(f"attribute_forbidden:{node.attr}")
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in _BANNED_CALLS:
                problems.append(f"call_forbidden:{node.func.id}")
            elif isinstance(node.func, ast.Attribute) and node.func.attr in _BANNED_ATTRIBUTE_CALLS:
                problems.append(f"call_forbidden:{node.func.attr}")
    if problems:
        detail = ",".join(sorted(set(problems)))
        raise ValueError(f"unsafe benchmark code: {detail}")


def benchmark_subprocess_env(sandbox: Path) -> dict[str, str]:
    """pytest子プロセスへsecretを継承せず、sandbox内の一時領域だけを渡す。"""
    sandbox = sandbox.resolve()
    temp_dir = sandbox / ".tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    env = {
        "HOME": str(sandbox),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONNOUSERSITE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "TEMP": str(temp_dir),
        "TMP": str(temp_dir),
    }
    for name in ("SystemRoot", "WINDIR"):
        value = os.environ.get(name)
        if value:
            env[name] = value
    return env


def _windows_working_set_bytes(process_id: int) -> int | None:
    if os.name != "nt":
        return None

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    process_query_information = 0x0400
    process_vm_read = 0x0010
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
    psapi.GetProcessMemoryInfo.restype = ctypes.c_int
    handle = kernel32.OpenProcess(process_query_information | process_vm_read, False, process_id)
    if not handle:
        return None
    try:
        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        ok = psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
        return int(counters.WorkingSetSize) if ok else None
    finally:
        kernel32.CloseHandle(handle)


def _windows_process_tree_ids(root_process_id: int) -> list[int]:
    if os.name != "nt":
        return [root_process_id]

    class ProcessEntry32(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_ulong),
            ("cntUsage", ctypes.c_ulong),
            ("th32ProcessID", ctypes.c_ulong),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", ctypes.c_ulong),
            ("cntThreads", ctypes.c_ulong),
            ("th32ParentProcessID", ctypes.c_ulong),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", ctypes.c_ulong),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_ulong, ctypes.c_ulong]
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ProcessEntry32)]
    kernel32.Process32FirstW.restype = ctypes.c_int
    kernel32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ProcessEntry32)]
    kernel32.Process32NextW.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot in (None, ctypes.c_void_p(-1).value):
        return [root_process_id]
    parent_by_pid: dict[int, int] = {}
    try:
        entry = ProcessEntry32()
        entry.dwSize = ctypes.sizeof(entry)
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            parent_by_pid[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    process_ids = [root_process_id]
    while True:
        additions = [pid for pid, parent in parent_by_pid.items() if parent in process_ids and pid not in process_ids]
        if not additions:
            return process_ids
        process_ids.extend(additions)


def _process_tree_working_set_bytes(root_process_id: int) -> int | None:
    values = [
        value
        for process_id in _windows_process_tree_ids(root_process_id)
        if (value := _windows_working_set_bytes(process_id)) is not None
    ]
    return sum(values) if values else None


def _terminate_process_tree(proc: subprocess.Popen[bytes]) -> None:
    if os.name != "nt":
        proc.kill()
        return
    process_ids = _windows_process_tree_ids(proc.pid)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    for process_id in reversed(process_ids):
        handle = kernel32.OpenProcess(0x0001, False, process_id)
        if not handle:
            continue
        try:
            kernel32.TerminateProcess(handle, 125)
        finally:
            kernel32.CloseHandle(handle)


def run_limited_benchmark_process(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout_sec: int = 60,
    max_output_bytes: int = 1024 * 1024,
    max_working_set_bytes: int = 512 * 1024 * 1024,
) -> subprocess.CompletedProcess[str]:
    """生成コードを出力・working-set上限付きで実行し、全文をmemoryへ溜めない。"""
    cwd = cwd.resolve()
    temp_dir = cwd / ".tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    reason = ""
    return_code = 0
    with tempfile.TemporaryFile(dir=temp_dir) as output:
        proc = subprocess.Popen(
            list(command),
            cwd=cwd,
            env=dict(env),
            stdout=output,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        deadline = time.monotonic() + timeout_sec
        while proc.poll() is None:
            output.flush()
            output_size = os.fstat(output.fileno()).st_size
            if max_output_bytes > 0 and output_size > max_output_bytes:
                reason = "output limit exceeded"
                return_code = 125
                _terminate_process_tree(proc)
                break
            working_set = _process_tree_working_set_bytes(proc.pid)
            if max_working_set_bytes > 0 and working_set is not None and working_set > max_working_set_bytes:
                reason = "working set limit exceeded"
                return_code = 125
                _terminate_process_tree(proc)
                break
            if time.monotonic() >= deadline:
                reason = "benchmark process timeout"
                return_code = 124
                _terminate_process_tree(proc)
                break
            time.sleep(0.02)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _terminate_process_tree(proc)
            proc.wait(timeout=5)
        output.flush()
        final_output_size = os.fstat(output.fileno()).st_size
        if not reason and max_output_bytes > 0 and final_output_size > max_output_bytes:
            reason = "output limit exceeded"
            return_code = 125
        if not reason:
            return_code = int(proc.returncode or 0)
        output.flush()
        output.seek(0)
        captured = output.read(max(0, max_output_bytes) if max_output_bytes > 0 else -1)
    text = captured.decode("utf-8", errors="replace")
    return subprocess.CompletedProcess(list(command), return_code, text, reason)
