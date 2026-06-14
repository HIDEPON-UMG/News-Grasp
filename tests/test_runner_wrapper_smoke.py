#!/usr/bin/env python3
"""runner wrapper (~/bin/run_codex_with_timeout.ps1) の契約テスト。"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

WRAPPER = Path(os.environ.get(
    "NEWS_GRASP_CODEX_WRAPPER",
    r"C:\Users\hidek\bin\run_codex_with_timeout.ps1",
))
POWERSHELL = os.environ.get("NEWS_GRASP_POWERSHELL", "powershell")
ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(not WRAPPER.exists(), reason=f"wrapper not found: {WRAPPER}")


def _fake_codex(tmp_path: Path) -> Path:
    fake = tmp_path / "fake_codex.cmd"
    fake.write_text(
        "@echo off\r\n"
        "echo ARGV:%*\r\n"
        "echo {\"type\":\"result\",\"is_error\":false}\r\n",
        encoding="cp932",
    )
    return fake


def _fake_codex_with_usage(tmp_path: Path) -> Path:
    fake = tmp_path / "fake_codex_usage.cmd"
    fake.write_text(
        "@echo off\r\n"
        "echo ARGV:%*\r\n"
        "echo tokens used\r\n"
        "echo 12,345\r\n",
        encoding="cp932",
    )
    return fake


def test_codex_wrapper_uses_prompt_file_working_directory_and_schema(tmp_path: Path) -> None:
    prompt_file = tmp_path / "prompt.md"
    log_file = tmp_path / "wrapper.log"
    prompt_file.write_text("日本語 smoke test\n", encoding="utf-8")
    result = subprocess.run(
        [
            POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(WRAPPER),
            "-CodexExe", str(_fake_codex(tmp_path)),
            "-PromptFile", str(prompt_file),
            "-LogFile", str(log_file),
            "-TimeoutSec", "30",
            "-IdleTimeoutSec", "30",
            "-WorkingDirectory", str(ROOT),
            "-OutputSchema", str(ROOT / "schemas" / "model_eval_output.schema.json"),
            "-OutputLastMessage", str(tmp_path / "last-message.txt"),
            "-Model", "test-model",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )

    log = log_file.read_text(encoding="utf-8", errors="replace")
    assert result.returncode == 0, result.stderr + log
    assert "exec" in log
    assert "--output-schema" in log
    assert "--output-last-message" in log or " -o " in log
    assert "-C" in log
    assert "--search" not in log
    assert "test-model" in log


def test_codex_wrapper_has_no_legacy_agent_parameters() -> None:
    text = WRAPPER.read_text(encoding="utf-8-sig")
    assert "ClaudeExe" not in text
    assert "run_claude" not in text


def test_codex_wrapper_quotes_argument_list_for_paths_with_spaces() -> None:
    text = WRAPPER.read_text(encoding="utf-8-sig")
    assert "ConvertTo-ProcessArgumentString" in text
    assert "$effectiveArgString = ConvertTo-ProcessArgumentString" in text
    assert "-ArgumentList $effectiveArgString" in text
    assert "-ArgumentList $effectiveArgs" not in text


def test_codex_wrapper_writes_flow_usage_jsonl(tmp_path: Path) -> None:
    prompt_file = tmp_path / "prompt.md"
    log_file = tmp_path / "wrapper.log"
    usage_log = tmp_path / "usage.jsonl"
    prompt_file.write_text("usage smoke\n", encoding="utf-8")

    result = subprocess.run(
        [
            POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(WRAPPER),
            "-CodexExe", str(_fake_codex_with_usage(tmp_path)),
            "-PromptFile", str(prompt_file),
            "-LogFile", str(log_file),
            "-TimeoutSec", "30",
            "-IdleTimeoutSec", "30",
            "-WorkingDirectory", str(ROOT),
            "-FlowName", "reporter:ai",
            "-UsageLog", str(usage_log),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    text = usage_log.read_text(encoding="utf-8")
    assert '"flow":"reporter:ai"' in text
    assert '"tokens_used":12345' in text
