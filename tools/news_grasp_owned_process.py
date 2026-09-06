"""News-GraspのWindows所有process境界。生成時Job所属とJob close終了を強制する。"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable, Mapping, Sequence


class OwnedProcessError(RuntimeError):
    """所有process境界の契約違反。"""


@dataclass(frozen=True)
class OwnedRunResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    timed_out: bool = False
    output_exceeded: bool = False


if os.name == "nt":
    from ctypes import wintypes

    CREATE_SUSPENDED = 0x00000004
    CREATE_NO_WINDOW = 0x08000000
    CREATE_UNICODE_ENVIRONMENT = 0x00000400
    EXTENDED_STARTUPINFO_PRESENT = 0x00080000
    STARTF_USESTDHANDLES = 0x00000100
    PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
    PROC_THREAD_ATTRIBUTE_JOB_LIST = 0x0002000D
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
    HANDLE_FLAG_INHERIT = 0x00000001
    WAIT_OBJECT_0 = 0x00000000
    WAIT_TIMEOUT = 0x00000102
    STILL_ACTIVE = 259
    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    FILE_SHARE_READ = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    OPEN_EXISTING = 3

    class SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", wintypes.LPVOID),
            ("bInheritHandle", wintypes.BOOL),
        ]

    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD),
            ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD),
            ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD),
            ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.POINTER(ctypes.c_ubyte)),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]

    class STARTUPINFOEXW(ctypes.Structure):
        _fields_ = [("StartupInfo", STARTUPINFOW), ("lpAttributeList", wintypes.LPVOID)]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD),
        ]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.CreateFileW.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CreatePipe.argtypes = [
        ctypes.POINTER(wintypes.HANDLE),
        ctypes.POINTER(wintypes.HANDLE),
        ctypes.POINTER(SECURITY_ATTRIBUTES),
        wintypes.DWORD,
    ]
    _kernel32.SetHandleInformation.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    _kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
    _kernel32.ResumeThread.restype = wintypes.DWORD
    _kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _kernel32.WaitForSingleObject.restype = wintypes.DWORD
    _kernel32.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    _kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    _kernel32.InitializeProcThreadAttributeList.argtypes = [
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    _kernel32.UpdateProcThreadAttribute.argtypes = [
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.c_size_t,
        wintypes.LPVOID,
        ctypes.c_size_t,
        wintypes.LPVOID,
        wintypes.LPVOID,
    ]
    _kernel32.CreateProcessW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.BOOL,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPCWSTR,
        ctypes.POINTER(STARTUPINFOEXW),
        ctypes.POINTER(PROCESS_INFORMATION),
    ]


def _raise_last_error(code: str) -> None:
    raise OwnedProcessError(f"{code}:{ctypes.get_last_error()}")


def _close_handle(handle: int | None) -> None:
    if os.name == "nt" and handle:
        _kernel32.CloseHandle(wintypes.HANDLE(handle))


class OwnedProcess:
    """生成時にJobへ所属したprocessと、その唯一の終了authority。"""

    def __init__(
        self,
        *,
        process_handle: int,
        job_handle: int,
        pid: int,
        stdout: BinaryIO | None,
        stderr: BinaryIO | None,
        args: tuple[str, ...],
    ) -> None:
        self._process_handle = process_handle
        self._job_handle = job_handle
        self.pid = pid
        self.stdout = stdout
        self.stderr = stderr
        self.args = args
        self.returncode: int | None = None

    def poll(self) -> int | None:
        if self.returncode is not None:
            return self.returncode
        code = wintypes.DWORD()
        if not _kernel32.GetExitCodeProcess(
            wintypes.HANDLE(self._process_handle), ctypes.byref(code)
        ):
            _raise_last_error("OWNED_PROCESS_EXIT_QUERY_FAILED")
        if code.value == STILL_ACTIVE:
            return None
        self.returncode = int(code.value)
        return self.returncode

    def wait(self, timeout: int | float | None = None) -> int:
        milliseconds = 0xFFFFFFFF if timeout is None else max(0, int(timeout * 1000))
        result = _kernel32.WaitForSingleObject(
            wintypes.HANDLE(self._process_handle), milliseconds
        )
        if result == WAIT_TIMEOUT:
            raise subprocess.TimeoutExpired(self.args, timeout)
        if result != WAIT_OBJECT_0:
            _raise_last_error("OWNED_PROCESS_WAIT_FAILED")
        return int(self.poll() or 0)

    def close_job(self) -> None:
        if self._job_handle:
            if not _kernel32.CloseHandle(wintypes.HANDLE(self._job_handle)):
                _raise_last_error("OWNED_PROCESS_JOB_CLOSE_FAILED")
            self._job_handle = 0

    def close(self) -> None:
        self.close_job()
        for stream in (self.stdout, self.stderr):
            if stream is not None and not stream.closed:
                stream.close()
        if self._process_handle:
            _close_handle(self._process_handle)
            self._process_handle = 0


def _resolve_executable(value: str | os.PathLike[str]) -> str:
    raw = os.fspath(value)
    candidate = Path(raw)
    try:
        if candidate.is_file():
            return str(candidate.resolve(strict=True))
    except OSError:
        # Microsoft Store Python の App Execution Alias は実行可能でも
        # Path.resolve(strict=True) が WinError 1920を返す。現在processと
        # byte-for-byte同じargv[0]に限り、任意aliasへ広げず許可する。
        if os.path.normcase(os.path.abspath(raw)) == os.path.normcase(
            os.path.abspath(sys.executable)
        ):
            return raw
    found = shutil.which(raw)
    if not found:
        raise OwnedProcessError("OWNED_PROCESS_EXECUTABLE_INVALID")
    try:
        return str(Path(found).resolve(strict=True))
    except OSError:
        if os.path.normcase(os.path.abspath(found)) == os.path.normcase(
            os.path.abspath(sys.executable)
        ):
            return found
        raise OwnedProcessError("OWNED_PROCESS_EXECUTABLE_INVALID")


def _is_reparse_point(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def _create_pipe() -> tuple[int, int]:
    read_handle = wintypes.HANDLE()
    write_handle = wintypes.HANDLE()
    attributes = SECURITY_ATTRIBUTES(
        ctypes.sizeof(SECURITY_ATTRIBUTES), None, True
    )
    if not _kernel32.CreatePipe(
        ctypes.byref(read_handle), ctypes.byref(write_handle), ctypes.byref(attributes), 0
    ):
        _raise_last_error("OWNED_PROCESS_PIPE_CREATE_FAILED")
    if not _kernel32.SetHandleInformation(read_handle, HANDLE_FLAG_INHERIT, 0):
        _close_handle(int(read_handle.value))
        _close_handle(int(write_handle.value))
        _raise_last_error("OWNED_PROCESS_PIPE_INHERITANCE_FAILED")
    return int(read_handle.value), int(write_handle.value)


def _open_null(access: int) -> int:
    attributes = SECURITY_ATTRIBUTES(
        ctypes.sizeof(SECURITY_ATTRIBUTES), None, True
    )
    handle = _kernel32.CreateFileW(
        "NUL",
        access,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        ctypes.byref(attributes),
        OPEN_EXISTING,
        0,
        None,
    )
    if handle in {0, -1}:
        _raise_last_error("OWNED_PROCESS_NULL_OPEN_FAILED")
    return int(handle or 0)


def _open_stdin_file(path: str | os.PathLike[str]) -> int:
    candidate = Path(path)
    if not candidate.is_file() or _is_reparse_point(candidate):
        raise OwnedProcessError("OWNED_PROCESS_STDIN_PATH_INVALID")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise OwnedProcessError("OWNED_PROCESS_STDIN_PATH_INVALID") from exc
    if not resolved.is_file() or _is_reparse_point(resolved):
        raise OwnedProcessError("OWNED_PROCESS_STDIN_PATH_INVALID")
    attributes = SECURITY_ATTRIBUTES(
        ctypes.sizeof(SECURITY_ATTRIBUTES), None, True
    )
    handle = _kernel32.CreateFileW(
        str(resolved),
        GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        ctypes.byref(attributes),
        OPEN_EXISTING,
        0,
        None,
    )
    if handle in {0, -1}:
        _raise_last_error("OWNED_PROCESS_STDIN_OPEN_FAILED")
    return int(handle or 0)


def _as_binary_stream(handle: int) -> BinaryIO:
    import msvcrt

    descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    return os.fdopen(descriptor, "rb", buffering=0)


def spawn_owned(
    command: Sequence[str | os.PathLike[str]],
    *,
    cwd: str | os.PathLike[str],
    env: Mapping[str, str] | None = None,
    capture_output: bool,
    stdin_path: str | os.PathLike[str] | None = None,
) -> OwnedProcess:
    """processをWindows Job所属として原子的に生成する。"""

    if os.name != "nt":
        raise OwnedProcessError("OWNED_PROCESS_WINDOWS_REQUIRED")
    if not command:
        raise OwnedProcessError("OWNED_PROCESS_COMMAND_EMPTY")
    argv = tuple(os.fspath(value) for value in command)
    executable = _resolve_executable(argv[0])
    working_directory = str(Path(cwd).resolve(strict=True))
    child_env = dict(os.environ if env is None else env)
    env_block = ctypes.create_unicode_buffer(
        "\0".join(f"{key}={value}" for key, value in sorted(child_env.items())) + "\0\0"
    )
    job = int(_kernel32.CreateJobObjectW(None, None) or 0)
    if not job:
        _raise_last_error("OWNED_PROCESS_JOB_CREATE_FAILED")
    process_info = PROCESS_INFORMATION()
    attribute_list = None
    handle_array = None
    job_array = None
    stdin_handle = 0
    stdout_read = stdout_write = stderr_read = stderr_write = 0
    try:
        limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not _kernel32.SetInformationJobObject(
            wintypes.HANDLE(job),
            JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            _raise_last_error("OWNED_PROCESS_JOB_CONFIGURATION_FAILED")
        stdin_handle = (
            _open_stdin_file(stdin_path)
            if stdin_path is not None
            else _open_null(GENERIC_READ)
        )
        if capture_output:
            stdout_read, stdout_write = _create_pipe()
            stderr_read, stderr_write = _create_pipe()
        else:
            stdout_write = _open_null(GENERIC_WRITE)
            stderr_write = _open_null(GENERIC_WRITE)

        attribute_size = ctypes.c_size_t()
        _kernel32.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(attribute_size))
        attribute_list = ctypes.create_string_buffer(attribute_size.value)
        if not _kernel32.InitializeProcThreadAttributeList(
            attribute_list, 2, 0, ctypes.byref(attribute_size)
        ):
            _raise_last_error("OWNED_PROCESS_ATTRIBUTE_INIT_FAILED")
        handle_array = (wintypes.HANDLE * 3)(
            stdin_handle, stdout_write, stderr_write
        )
        if not _kernel32.UpdateProcThreadAttribute(
            attribute_list,
            0,
            PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
            handle_array,
            ctypes.sizeof(handle_array),
            None,
            None,
        ):
            _raise_last_error("OWNED_PROCESS_HANDLE_ATTRIBUTE_FAILED")
        job_array = (wintypes.HANDLE * 1)(job)
        if not _kernel32.UpdateProcThreadAttribute(
            attribute_list,
            0,
            PROC_THREAD_ATTRIBUTE_JOB_LIST,
            job_array,
            ctypes.sizeof(job_array),
            None,
            None,
        ):
            _raise_last_error("OWNED_PROCESS_JOB_ATTRIBUTE_FAILED")

        startup = STARTUPINFOEXW()
        startup.StartupInfo.cb = ctypes.sizeof(startup)
        startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES
        startup.StartupInfo.hStdInput = wintypes.HANDLE(stdin_handle)
        startup.StartupInfo.hStdOutput = wintypes.HANDLE(stdout_write)
        startup.StartupInfo.hStdError = wintypes.HANDLE(stderr_write)
        startup.lpAttributeList = ctypes.cast(attribute_list, wintypes.LPVOID)
        command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
        flags = (
            CREATE_SUSPENDED
            | CREATE_NO_WINDOW
            | CREATE_UNICODE_ENVIRONMENT
            | EXTENDED_STARTUPINFO_PRESENT
        )
        if not _kernel32.CreateProcessW(
            executable,
            command_line,
            None,
            None,
            True,
            flags,
            ctypes.cast(env_block, wintypes.LPVOID),
            working_directory,
            ctypes.byref(startup),
            ctypes.byref(process_info),
        ):
            _raise_last_error("OWNED_PROCESS_CREATE_FAILED")
        if _kernel32.ResumeThread(process_info.hThread) == 0xFFFFFFFF:
            _raise_last_error("OWNED_PROCESS_RESUME_FAILED")
        _close_handle(int(process_info.hThread))
        process_info.hThread = None
        _close_handle(stdin_handle)
        stdin_handle = 0
        _close_handle(stdout_write)
        stdout_write = 0
        _close_handle(stderr_write)
        stderr_write = 0
        stdout_stream = _as_binary_stream(stdout_read) if capture_output else None
        stdout_read = 0
        stderr_stream = _as_binary_stream(stderr_read) if capture_output else None
        stderr_read = 0
        result = OwnedProcess(
            process_handle=int(process_info.hProcess),
            job_handle=job,
            pid=int(process_info.dwProcessId),
            stdout=stdout_stream,
            stderr=stderr_stream,
            args=argv,
        )
        process_info.hProcess = None
        job = 0
        return result
    finally:
        if attribute_list is not None:
            _kernel32.DeleteProcThreadAttributeList(attribute_list)
        for handle in (
            int(process_info.hThread or 0),
            int(process_info.hProcess or 0),
            stdin_handle,
            stdout_read,
            stdout_write,
            stderr_read,
            stderr_write,
        ):
            _close_handle(handle)
        _close_handle(job)


def run_owned_bounded(
    command: Sequence[str | os.PathLike[str]],
    *,
    cwd: str | os.PathLike[str],
    timeout: int | float | None,
    max_output_bytes: int,
    env: Mapping[str, str] | None = None,
    heartbeat: Callable[[], object] | None = None,
    heartbeat_interval_seconds: int | float | None = None,
    stdin_path: str | os.PathLike[str] | None = None,
    stdout_sink: BinaryIO | None = None,
    stderr_sink: BinaryIO | None = None,
) -> OwnedRunResult:
    """所有Job内でbounded commandを実行し、超過時はJob closeだけで回収する。

    heartbeatはchildの生存中だけ一定間隔で呼び出す。例外は隠蔽せず、
    finallyのJob closeで所有process treeを回収した後にcallerへ返す。
    """

    if heartbeat is None:
        heartbeat_interval = None
    else:
        if (
            isinstance(heartbeat_interval_seconds, bool)
            or not isinstance(heartbeat_interval_seconds, (int, float))
            or heartbeat_interval_seconds <= 0
        ):
            raise OwnedProcessError("OWNED_PROCESS_HEARTBEAT_INTERVAL_INVALID")
        heartbeat_interval = float(heartbeat_interval_seconds)

    process = spawn_owned(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        stdin_path=stdin_path,
    )
    stdout = bytearray()
    stderr = bytearray()
    exceeded = threading.Event()
    sink_errors: list[BaseException] = []
    sink_error_lock = threading.Lock()

    def drain(
        stream: BinaryIO | None,
        target: bytearray,
        sink: BinaryIO | None,
    ) -> None:
        if stream is None:
            return
        try:
            reader = getattr(stream, "read1", stream.read)
            while True:
                chunk = reader(64 * 1024)
                if not chunk:
                    return
                if sink is not None:
                    sink.write(chunk)
                    sink.flush()
                remaining = max_output_bytes + 1 - len(target)
                if remaining > 0:
                    target.extend(chunk[:remaining])
                if len(target) > max_output_bytes or len(chunk) > remaining:
                    exceeded.set()
                    return
        except BaseException as exc:  # noqa: BLE001 - sink failure crosses the owner boundary.
            with sink_error_lock:
                sink_errors.append(exc)
            exceeded.set()

    threads = [
        threading.Thread(target=drain, args=(process.stdout, stdout, stdout_sink), daemon=True),
        threading.Thread(target=drain, args=(process.stderr, stderr, stderr_sink), daemon=True),
    ]
    for thread in threads:
        thread.start()
    timed_out = False
    started = time.monotonic()
    deadline = None if timeout is None else started + float(timeout)
    next_heartbeat = (
        started + heartbeat_interval if heartbeat_interval is not None else None
    )
    try:
        while process.poll() is None:
            if exceeded.is_set():
                process.close_job()
                break
            now = time.monotonic()
            remaining = None if deadline is None else deadline - now
            if remaining is not None and remaining <= 0:
                timed_out = True
                process.close_job()
                break
            if (
                heartbeat is not None
                and next_heartbeat is not None
                and now >= next_heartbeat
            ):
                heartbeat()
                next_heartbeat = now + float(heartbeat_interval or 0)
            wait_seconds = 0.05 if remaining is None else min(0.05, remaining)
            if next_heartbeat is not None:
                wait_seconds = min(wait_seconds, max(0.001, next_heartbeat - now))
            try:
                process.wait(timeout=wait_seconds)
            except subprocess.TimeoutExpired:
                continue
        for thread in threads:
            thread.join(timeout=5)
        if sink_errors:
            raise OwnedProcessError("OWNED_PROCESS_SINK_WRITE_FAILED") from sink_errors[0]
        return OwnedRunResult(
            returncode=int(process.poll() or 0),
            stdout=bytes(stdout),
            stderr=bytes(stderr),
            timed_out=timed_out,
            output_exceeded=(
                exceeded.is_set()
                or len(stdout) > max_output_bytes
                or len(stderr) > max_output_bytes
            ),
        )
    finally:
        process.close()


def spawn_owned_detached(
    command: Sequence[str | os.PathLike[str]],
    *,
    cwd: str | os.PathLike[str],
    env: Mapping[str, str] | None = None,
) -> OwnedProcess:
    """長寿命processを生成時Job所属かつhiddenで起動する。"""

    return spawn_owned(command, cwd=cwd, env=env, capture_output=False)
