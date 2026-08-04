from __future__ import annotations

import subprocess
import time
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


def test_wrapper_job_object_kills_grandchild_when_wrapper_is_terminated(
    tmp_path: Path,
    canonical_model_broker: tuple[list[str], dict[str, str]],
) -> None:
    fake_codex = tmp_path / "fake-codex-grandchild.ps1"
    started = tmp_path / "started.txt"
    sentinel = tmp_path / "grandchild-survived.txt"
    fake_codex.write_text(
        "$child = \"Start-Sleep -Seconds 2; [IO.File]::WriteAllText(''"
        + str(sentinel).replace("'", "''")
        + "'', ''survived'')\"\n"
        "Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile','-Command',$child) -WindowStyle Hidden | Out-Null\n"
        f"[IO.File]::WriteAllText('{str(started).replace("'", "''")}', 'started')\n"
        "Start-Sleep -Seconds 30\n",
        encoding="utf-8-sig",
    )
    prompt = tmp_path / "prompt.txt"
    log = tmp_path / "wrapper.log"
    prompt.write_text("test", encoding="utf-8")
    high_cost_args, env = canonical_model_broker
    process = subprocess.Popen(
        [
            "pwsh",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(WRAPPER),
            "-CodexExe",
            str(fake_codex),
            "-PromptFile",
            str(prompt),
            "-LogFile",
            str(log),
            "-WorkingDirectory",
            str(tmp_path),
            "-FlowName",
            "reporter:owned-job-test",
            *high_cost_args,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    try:
        deadline = time.monotonic() + 15
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert started.exists(), log.read_text(encoding="utf-8-sig", errors="replace")
        process.terminate()
        process.wait(timeout=10)
        time.sleep(3)
        assert not sentinel.exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_wrapper_owns_model_tree_with_kill_on_job_close() -> None:
    source = WRAPPER.read_text(encoding="utf-8-sig")
    assert "JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE" in source
    assert "AssignProcessToJobObject" in source
    assert "CREATE_SUSPENDED" in source
    assert "ResumeThread" in source
    assign = source.index("CreateSuspendedAssignedProcess")
    model_start = source.index("$proc = Get-Process", assign)
    assert assign < model_start
    assert "[NewsGraspOwnedJob]::CloseOwnedJob($ownedJobHandle)" in source


def test_wrapper_kills_grandchild_after_normal_model_exit_in_calling_host(
    tmp_path: Path,
    canonical_model_broker: tuple[list[str], dict[str, str]],
) -> None:
    fake_codex = tmp_path / "fake-codex-normal-grandchild.ps1"
    sentinel = tmp_path / "normal-grandchild-survived.txt"
    fake_codex.write_text(
        "$child = \"Start-Sleep -Seconds 2; [IO.File]::WriteAllText(''"
        + str(sentinel).replace("'", "''")
        + "'', ''survived'')\"\n"
        "Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile','-Command',$child) -WindowStyle Hidden | Out-Null\n"
        "exit 0\n",
        encoding="utf-8-sig",
    )
    prompt = tmp_path / "prompt.txt"
    log = tmp_path / "wrapper.log"
    caller = tmp_path / "caller.ps1"
    prompt.write_text("test", encoding="utf-8")
    high_cost_args, env = canonical_model_broker
    quoted_args = ",\n".join("    '" + arg.replace("'", "''") + "'" for arg in high_cost_args)
    caller.write_text(
        "$wrapperArgs = @(\n"
        f"    '-CodexExe', '{str(fake_codex).replace("'", "''")}',\n"
        f"    '-PromptFile', '{str(prompt).replace("'", "''")}',\n"
        f"    '-LogFile', '{str(log).replace("'", "''")}',\n"
        f"    '-WorkingDirectory', '{str(tmp_path).replace("'", "''")}',\n"
        "    '-FlowName', 'security:normal-exit-test',\n"
        f"{quoted_args}\n"
        ")\n"
        f"& '{str(WRAPPER).replace("'", "''")}' @wrapperArgs\n"
        "$wrapperRc = $LASTEXITCODE\n"
        "Start-Sleep -Seconds 3\n"
        f"if (Test-Path -LiteralPath '{str(sentinel).replace("'", "''")}') {{ exit 99 }}\n"
        "exit $wrapperRc\n",
        encoding="utf-8-sig",
    )

    completed = subprocess.run(
        ["pwsh", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(caller)],
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
        env=env,
    )

    assert completed.returncode == 0, log.read_text(encoding="utf-8-sig", errors="replace")
    assert not sentinel.exists()
