"""実行プロセスの News-Grasp entry writer identity を検証する。"""

from __future__ import annotations

from collections.abc import Mapping
import os
from typing import Protocol, Any


class EntryWriterAttestor(Protocol):
    """writer envelope が現在の entry process に束縛されているかを検証する。"""

    def validate(self, writer: Mapping[str, Any]) -> bool:
        """writer の OS 実測 identity を検証する。"""


def _filetime_token(value: Any) -> str:
    """FILETIME/LARGE_INTEGER を比較可能な canonical decimal token にする。"""
    try:
        integer = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("invalid OS time token") from exc
    if integer < 0:
        raise ValueError("invalid OS time token")
    return str(integer)


def _windows_boot_token() -> str:
    """NtQuerySystemInformation(SystemTimeOfDayInformation).BootTime。"""
    if os.name != "nt":
        raise OSError("Windows boot token is unavailable")
    import ctypes
    from ctypes import wintypes

    class _SystemTimeOfDayInformation(ctypes.Structure):
        _fields_ = (
            ("BootTime", ctypes.c_longlong),
            ("CurrentTime", ctypes.c_longlong),
            ("TimeZoneBias", ctypes.c_longlong),
            ("TimeZoneId", wintypes.ULONG),
            ("Reserved", wintypes.ULONG),
            ("BootTimeBias", ctypes.c_longlong),
            ("SleepTime", ctypes.c_longlong),
        )

    ntdll = ctypes.WinDLL("ntdll")
    query = ntdll.NtQuerySystemInformation
    query.argtypes = [wintypes.ULONG, wintypes.LPVOID, wintypes.ULONG, ctypes.POINTER(wintypes.ULONG)]
    query.restype = wintypes.LONG
    info = _SystemTimeOfDayInformation()
    returned = wintypes.ULONG()
    # SystemTimeOfDayInformation is information class 3.
    status = query(3, ctypes.byref(info), ctypes.sizeof(info), ctypes.byref(returned))
    if status < 0:
        raise OSError(f"NtQuerySystemInformation failed: 0x{status & 0xFFFFFFFF:08x}")
    return _filetime_token(info.BootTime)


def _windows_process_start_token() -> str:
    """GetCurrentProcess/GetProcessTimes の creation FILETIME。"""
    if os.name != "nt":
        raise OSError("Windows process token is unavailable")
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_current_process = kernel32.GetCurrentProcess
    get_current_process.argtypes = []
    get_current_process.restype = wintypes.HANDLE
    get_process_times = kernel32.GetProcessTimes
    get_process_times.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    get_process_times.restype = wintypes.BOOL
    creation = wintypes.FILETIME()
    exit_time = wintypes.FILETIME()
    kernel_time = wintypes.FILETIME()
    user_time = wintypes.FILETIME()
    if not get_process_times(
        get_current_process(),
        ctypes.byref(creation),
        ctypes.byref(exit_time),
        ctypes.byref(kernel_time),
        ctypes.byref(user_time),
    ):
        raise OSError(ctypes.get_last_error(), "GetProcessTimes failed")
    value = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
    return _filetime_token(value)


class SystemEntryWriterAttestor:
    """OS の実測 identity だけを信頼する production attestor。"""

    def __init__(self, *, pid: int | None = None) -> None:
        self.pid = int(os.getpid() if pid is None else pid)

    def identity(self) -> dict[str, Any] | None:
        """現在 process の writer identity を返す。取得不能なら None。"""
        try:
            if os.name != "nt":
                return None
            return {
                "pid": self.pid,
                "bootId": _windows_boot_token(),
                "processStartToken": _windows_process_start_token(),
            }
        except (OSError, RuntimeError, TypeError, ValueError):
            return None

    def bind(self, writer: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
        """OS identity を writer envelope に束縛する。"""
        identity = self.identity()
        if identity is None:
            return None
        writer_id = "news-grasp-entry"
        if isinstance(writer, Mapping) and isinstance(writer.get("writerId"), str) and writer["writerId"]:
            writer_id = writer["writerId"]
        return {"writerId": writer_id, **identity}

    current_writer = bind

    def validate(self, writer: Mapping[str, Any]) -> bool:
        """writer の pid/bootId/processStartToken が OS 実測値と完全一致するか検証する。"""
        identity = self.identity()
        if identity is None or not isinstance(writer, Mapping):
            return False
        return (
            writer.get("pid") == identity["pid"]
            and writer.get("bootId") == identity["bootId"]
            and writer.get("processStartToken") == identity["processStartToken"]
        )
