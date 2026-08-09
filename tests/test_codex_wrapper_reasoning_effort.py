from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


WRAPPER = Path("scripts/ops/run_codex_with_timeout.ps1")
RUNNER = Path("scripts/ops/news-grasp-runner.ps1")
INSTALLER = Path("scripts/ops/install-news-grasp-ops.ps1")


def test_repo_managed_wrapper_passes_model_and_high_effort(
    tmp_path: Path,
    canonical_model_broker: tuple[list[str], dict[str, str]],
) -> None:
    assert WRAPPER.exists()
    capture = tmp_path / "args.txt"
    fake = tmp_path / "fake-codex.ps1"
    fake.write_text(
        "$args -join ' ' | Set-Content -LiteralPath $env:CAPTURE -Encoding utf8\nexit 0\n",
        encoding="utf-8-sig",
    )
    prompt = tmp_path / "prompt.md"
    prompt.write_text("test", encoding="utf-8")
    log = tmp_path / "wrapper.log"
    usage = tmp_path / "usage.jsonl"
    high_cost_args, env = canonical_model_broker
    env["CAPTURE"] = str(capture)

    completed = subprocess.run(
        [
            "pwsh", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(WRAPPER),
            "-CodexExe", str(fake), "-PromptFile", str(prompt), "-LogFile", str(log),
            "-TimeoutSec", "10", "-IdleTimeoutSec", "0", "-HeartbeatSec", "0",
            "-WorkingDirectory", str(tmp_path), "-Model", "gpt-5.6-luna",
            "-ReasoningEffort", "high", "-FlowName", "test", "-UsageLog", str(usage),
            *high_cost_args,
        ],
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr + completed.stdout
    args = capture.read_text(encoding="utf-8-sig", errors="replace")
    assert "--model gpt-5.6-luna" in args
    assert "model_reasoning_effort" in args
    assert "high" in args
    record = json.loads(usage.read_text(encoding="utf-8-sig").splitlines()[0])
    assert record["model"] == "gpt-5.6-luna"
    assert record["reasoning_effort"] == "high"


def test_runner_passes_reasoning_effort_for_every_codex_role() -> None:
    source = RUNNER.read_text(encoding="utf-8-sig")

    assert "[string] $ReasoningEffort = ''" in source
    assert "$codexArgs['ReasoningEffort'] = $ReasoningEffort" in source
    assert "ReporterReasoningEffort" in source
    assert "RepairReasoningEffort" in source
    assert "NewsroomEditorReasoningEffort" in source
    assert "DeepDiveReasoningEffort" in source
    assert source.count("-ReasoningEffort") >= 4


def test_installer_syncs_wrapper_with_backup_and_rollback() -> None:
    source = INSTALLER.read_text(encoding="utf-8-sig")

    assert source.count("run_codex_with_timeout.ps1") >= 1
    assert "$backup = Join-Path $BackupDir $file" in source
    assert "install-news-grasp-ops-guard.ps1" in source
    assert "rollback_commands" in source
