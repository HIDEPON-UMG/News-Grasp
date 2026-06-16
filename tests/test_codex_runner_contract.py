#!/usr/bin/env python3
"""Codex runner 移行の静的契約テスト。"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CODEX_WRAPPER = Path(os.environ.get("NEWS_GRASP_CODEX_WRAPPER", str(Path.home() / "bin" / "run_codex_with_timeout.ps1")))
RUNNER = Path(os.environ.get("NEWS_GRASP_RUNNER", str(Path.home() / "bin" / "news-grasp-runner.ps1")))
BACKFILL_MOBILITY = ROOT / "build" / "run_backfill_mobility.ps1"


def test_codex_timeout_wrapper_uses_codex_exec_schema_and_last_message() -> None:
    wrapper = CODEX_WRAPPER.read_text(encoding="utf-8-sig")

    assert "codex" in wrapper.lower()
    assert "exec" in wrapper
    assert "--output-schema" in wrapper
    assert "--output-last-message" in wrapper or "-o" in wrapper
    assert "--search" not in wrapper
    assert "IdleTimeoutSec" in wrapper
    assert "WorkingDirectory" in wrapper


def test_runner_exposes_codex_mode_without_claude_print() -> None:
    runner = RUNNER.read_text(encoding="utf-8-sig")

    assert "run_codex_with_timeout.ps1" in runner
    assert "codex exec" in runner.lower() or "-CodexExe" in runner
    assert "claude --print" not in runner.lower()


def test_runner_uses_direct_codex_exe_not_preflight_capturing_wrapper() -> None:
    """日次 runner は stdout/stderr を捕捉する codex.ps1 経由で idle 監視を壊さない。"""
    runner = RUNNER.read_text(encoding="utf-8-sig")

    assert "function Resolve-CodexCliExe" in runner
    assert "$CodexExe  = Resolve-CodexCliExe -Override $CodexExeOverride" in runner
    assert "codex_exec_preflight_wrapper.ps1" not in runner
    assert "Join-Path $env:USERPROFILE 'bin\\codex.ps1'" not in runner


def test_mobility_backfill_does_not_keep_claude_print_path() -> None:
    """完了済み一時backfillからClaude CLI課金経路を残さない。"""
    if not BACKFILL_MOBILITY.exists():
        return

    script = BACKFILL_MOBILITY.read_text(encoding="utf-8-sig").lower()

    assert "claude --print" not in script
    assert "claude -p" not in script


def test_codex_mode_routes_repair_and_deepdive_to_codex_wrapper() -> None:
    """Codex移行後に補修/DeepDiveだけClaudeへ戻らないことをpinする。"""
    runner = RUNNER.read_text(encoding="utf-8-sig")

    assert "function Invoke-CodexWrapper" in runner
    assert "repair wrapper invoke START (agent=codex" in runner
    assert "deepdive wrapper invoke START (agent=codex" in runner
    assert "& $Wrapper -ClaudeExe $ClaudeExe -PromptFile $repairPrompt" not in runner
    assert "& $Wrapper -ClaudeExe $ClaudeExe -PromptFile $DeepDivePromptFile" not in runner
