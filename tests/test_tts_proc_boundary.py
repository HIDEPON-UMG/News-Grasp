from __future__ import annotations

import subprocess
import sys
from unittest.mock import patch

from tools.tts import proc


def test_quiet_run_adds_create_no_window_on_windows(monkeypatch):
    """Windows では subprocess 境界が黒窓防止 flag を必ず付ける。"""
    monkeypatch.setattr(sys, "platform", "win32")
    completed = subprocess.CompletedProcess(["ffmpeg"], 0, "", "")

    with patch("subprocess.run", return_value=completed) as run:
        result = proc.quiet_run(["ffmpeg", "-version"], check=False)

    assert result is completed
    kwargs = run.call_args.kwargs
    assert kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True
    assert kwargs["encoding"] == "utf-8"
    assert kwargs["errors"] == "replace"


def test_quiet_run_does_not_add_windows_flag_on_non_windows(monkeypatch):
    """非 Windows では Windows 専用 creationflags を渡さない。"""
    monkeypatch.setattr(sys, "platform", "linux")
    completed = subprocess.CompletedProcess(["true"], 0, "", "")

    with patch("subprocess.run", return_value=completed) as run:
        proc.quiet_run(["true"], check=False)

    assert "creationflags" not in run.call_args.kwargs


def test_spawn_detached_adds_create_no_window_on_windows(monkeypatch):
    """エンジン自動起動も同じ境界で CREATE_NO_WINDOW を使う。"""
    monkeypatch.setattr(sys, "platform", "win32")

    with patch("subprocess.Popen") as popen:
        proc.spawn_detached(["AivisSpeech.exe"])

    assert popen.call_args.kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW
