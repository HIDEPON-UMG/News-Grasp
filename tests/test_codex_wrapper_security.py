from __future__ import annotations

import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WRAPPER = REPO_ROOT / "scripts" / "ops" / "run_codex_with_timeout.ps1"


def _run_wrapper(
    tmp_path: Path,
    codex_exe: Path,
    high_cost_args: list[str],
    env: dict[str, str],
    *extra: str,
) -> subprocess.CompletedProcess[str]:
    prompt = tmp_path / "prompt.txt"
    log = tmp_path / "wrapper.log"
    prompt.write_text("test", encoding="utf-8")
    return subprocess.run(
        [
            "pwsh",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(WRAPPER),
            "-CodexExe",
            str(codex_exe),
            "-PromptFile",
            str(prompt),
            "-LogFile",
            str(log),
            "-WorkingDirectory",
            str(tmp_path),
            "-FlowName",
            "security:test",
            *high_cost_args,
            *extra,
        ],
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
        env=env,
    )


def test_wrapper_rejects_cmd_and_bat_shims(
    tmp_path: Path,
    canonical_model_broker: tuple[list[str], dict[str, str]],
) -> None:
    shim = tmp_path / "codex.cmd"
    shim.write_text("@echo off\r\nexit /b 0\r\n", encoding="ascii")

    high_cost_args, env = canonical_model_broker
    completed = _run_wrapper(tmp_path, shim, high_cost_args, env)

    assert completed.returncode == 125
    assert "unsupported CodexExe extension" in (tmp_path / "wrapper.log").read_text(encoding="utf-8-sig")


def test_wrapper_stops_child_when_captured_output_exceeds_limit(
    tmp_path: Path,
    canonical_model_broker: tuple[list[str], dict[str, str]],
) -> None:
    fake_codex = tmp_path / "fake-codex.ps1"
    fake_codex.write_text(
        "param([Parameter(ValueFromRemainingArguments=$true)][object[]]$Rest)\n"
        "[Console]::Out.Write(('x' * 8192))\n"
        "Start-Sleep -Seconds 10\n",
        encoding="utf-8-sig",
    )

    high_cost_args, env = canonical_model_broker
    completed = _run_wrapper(
        tmp_path,
        fake_codex,
        high_cost_args,
        env,
        "-MaxCapturedOutputBytes",
        "1024",
        "-TimeoutSec",
        "20",
    )

    assert completed.returncode == 125
    assert "OUTPUT LIMIT" in (tmp_path / "wrapper.log").read_text(encoding="utf-8-sig")
