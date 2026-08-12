from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Sequence

from tools.news_grasp_owned_process import OwnedProcess, run_owned_bounded, spawn_owned_detached


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
    if sys.platform == "win32":
        result = run_owned_bounded(
            [str(a) for a in args],
            cwd=Path(cwd or ".").resolve(),
            timeout=float(timeout or 24 * 60 * 60),
            max_output_bytes=16 * 1024 * 1024,
        )
        stdout = result.stdout.decode("utf-8", errors="replace")
        stderr = result.stderr.decode("utf-8", errors="replace")
        completed = subprocess.CompletedProcess(
            [str(a) for a in args], result.returncode, stdout, stderr
        )
        if result.timed_out:
            raise subprocess.TimeoutExpired(args, timeout, output=stdout, stderr=stderr)
        if result.output_exceeded:
            raise RuntimeError("TTS_SUBPROCESS_OUTPUT_EXCEEDED")
        if check and result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode, args, output=stdout, stderr=stderr
            )
        return completed
    return subprocess.run([str(a) for a in args], **kwargs)


def spawn_detached(args: Sequence[str | Path], *, cwd: str | Path | None = None) -> OwnedProcess:
    """AivisSpeech エンジン起動用。失敗時は呼び出し側で非致命扱いにする。"""
    return spawn_owned_detached(
        [str(a) for a in args], cwd=Path(cwd or ".").resolve()
    )
