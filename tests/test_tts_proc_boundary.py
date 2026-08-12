from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from tools.news_grasp_owned_process import OwnedRunResult
from tools.tts import proc


def test_quiet_run_adds_create_no_window_on_windows(monkeypatch):
    """Windows では黒窓防止を所有process境界へ集約する。"""
    monkeypatch.setattr(sys, "platform", "win32")
    owned = OwnedRunResult(0, b"ok", b"")

    with patch.object(proc, "run_owned_bounded", return_value=owned) as run:
        with patch("subprocess.run") as raw_run:
            result = proc.quiet_run(["ffmpeg", "-version"], check=False)

    raw_run.assert_not_called()
    run.assert_called_once_with(
        ["ffmpeg", "-version"],
        cwd=Path(".").resolve(),
        timeout=float(24 * 60 * 60),
        max_output_bytes=16 * 1024 * 1024,
    )
    assert result.args == ["ffmpeg", "-version"]
    assert result.returncode == 0
    assert result.stdout == "ok"
    assert result.stderr == ""


def test_quiet_run_does_not_add_windows_flag_on_non_windows(monkeypatch):
    """非 Windows では Windows 専用 creationflags を渡さない。"""
    monkeypatch.setattr(sys, "platform", "linux")
    completed = subprocess.CompletedProcess(["true"], 0, "", "")

    with patch("subprocess.run", return_value=completed) as run:
        proc.quiet_run(["true"], check=False)

    assert "creationflags" not in run.call_args.kwargs


def test_spawn_detached_adds_create_no_window_on_windows(monkeypatch):
    """エンジン自動起動も所有process境界だけを使う。"""
    monkeypatch.setattr(sys, "platform", "win32")

    with patch.object(proc, "spawn_owned_detached") as spawn:
        with patch("subprocess.Popen") as raw_popen:
            result = proc.spawn_detached(["AivisSpeech.exe"])

    raw_popen.assert_not_called()
    assert result is spawn.return_value
    spawn.assert_called_once_with(
        ["AivisSpeech.exe"], cwd=Path(".").resolve()
    )
