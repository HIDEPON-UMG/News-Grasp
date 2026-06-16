from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Sequence


def _windows_creationflags() -> int:
    if sys.platform != "win32":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def quiet_run(
    args: Sequence[str | Path],
    *,
    timeout: int | float | None = None,
    cwd: str | Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """コンソールアプリ実行を 1 箇所に集約し、Windows の黒窓点滅を防ぐ。"""
    kwargs = {
        "timeout": timeout,
        "cwd": cwd,
        "check": check,
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    flags = _windows_creationflags()
    if flags:
        kwargs["creationflags"] = flags
    return subprocess.run([str(a) for a in args], **kwargs)


def spawn_detached(args: Sequence[str | Path], *, cwd: str | Path | None = None) -> subprocess.Popen:
    """AivisSpeech エンジン起動用。失敗時は呼び出し側で非致命扱いにする。"""
    kwargs = {
        "cwd": cwd,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
        "close_fds": True,
    }
    flags = _windows_creationflags()
    if flags:
        kwargs["creationflags"] = flags
    return subprocess.Popen([str(a) for a in args], **kwargs)
