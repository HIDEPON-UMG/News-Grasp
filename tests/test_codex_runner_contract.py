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


def test_runner_pytest_gate_uses_repo_local_basetemp() -> None:
    """pytest の一時ディレクトリ権限で日次公開を止めない。"""
    runner = RUNNER.read_text(encoding="utf-8-sig")

    assert "$PytestBaseTemp = Join-Path $RepoDir '.pytest-tmp'" in runner
    assert "PYTEST_ADDOPTS" in runner
    assert "--basetemp=$PytestBaseTemp" in runner


def test_runner_youtube_podcast_is_required_distribution_gate() -> None:
    """YouTube Podcast は通常公開の必須配信物として扱う。"""
    runner = (ROOT / "scripts" / "ops" / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")

    tts_done = runner.index("tts publish_audio")
    deepdive_tts_build = runner.index("deepdive dialogue synthesize")
    deepdive_tts_publish = runner.index("deepdive dialogue publish")
    youtube_build = runner.index("youtube podcast build_video")
    deepdive_youtube_build = runner.index("deepdive youtube podcast build_video")
    youtube_prepare = runner.index("youtube podcast prepare")
    deepdive_youtube_prepare = runner.index("deepdive youtube podcast prepare")
    digest_commit = runner.index("2.9 digest/data commit")
    docs_commit = runner.index("4. docs/ commit")
    push_start = runner.index("push origin main start")
    publish_verify = runner.index("publish verification start")
    youtube_finalize = runner.index("youtube podcast finalize")
    deepdive_youtube_finalize = runner.index("deepdive youtube podcast finalize")
    podcast_verify = runner.index("podcast verification start")
    deepdive_podcast_verify = runner.index("deepdive podcast verification start")
    send_push = runner.index("send_push start")
    ok_marker = runner.rindex("news-grasp-runner.ps1 OK")

    assert tts_done < deepdive_tts_build < deepdive_tts_publish < digest_commit
    assert digest_commit < docs_commit < youtube_build < deepdive_youtube_build < youtube_prepare < deepdive_youtube_prepare < push_start
    assert push_start < publish_verify < youtube_finalize < deepdive_youtube_finalize < podcast_verify < deepdive_podcast_verify < send_push < ok_marker
    assert "tools.youtube_podcast.build_video" in runner
    assert "tools.youtube_podcast.upload_episode" in runner
    assert "tools.tts.deepdive_dialogue" in runner
    assert "tools.tts.deepdive_audio" in runner
    assert "'--kind', 'deepdive'" in runner
    assert "--prepare" in runner
    assert "--finalize" in runner
    assert "distribution_failed" in runner
    assert "youtube_podcast_failed" not in runner
    repair_window = runner[tts_done:docs_commit]
    assert "tools.youtube_podcast.upload_episode" not in repair_window
    youtube_block = runner[youtube_build:push_start]
    assert "exit 1" in youtube_block


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
