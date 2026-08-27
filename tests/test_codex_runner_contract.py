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
    assert "CODEX_NONINTERACTIVE_SESSION" in wrapper
    assert "CODEX_OUTPUT_CONTRACT" in wrapper
    assert "artifact-gate" in wrapper


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


def test_runner_pytest_gate_uses_bounded_short_temp_root() -> None:
    """深いdetached worktreeでもWindowsの最大パス長で停止しない。"""
    runner = RUNNER.read_text(encoding="utf-8-sig")

    assert "$PytestBaseTempRoot = Join-Path ([System.IO.Path]::GetTempPath()) 'ng-pytest'" in runner
    assert "$PytestBaseTemp = Join-Path $PytestBaseTempRoot \"$DateStamp-$RunId-$([Guid]::NewGuid().ToString('N'))\"" in runner
    assert "PYTEST_BASETEMP_ROOT_REPARSE_POINT_FORBIDDEN" in runner
    assert "PYTEST_BASETEMP_ROOT_OWNER_INVALID" in runner
    assert "PYTEST_BASETEMP_LEAF_COLLISION" in runner
    assert "Join-Path $RepoDir '.pytest-tmp'" not in runner
    assert "PYTEST_ADDOPTS" in runner
    assert "--basetemp=$PytestBaseTemp" in runner


def test_runner_youtube_podcast_is_required_distribution_gate() -> None:
    """YouTube Podcast は通常公開の必須配信物として扱う。"""
    runner = (ROOT / "scripts" / "ops" / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")

    tts_done = runner.index("tts publish_audio")
    deepdive_tts_script = runner.index("deepdive dialogue script build")
    deepdive_tts_build = runner.index("deepdive dialogue synthesize")
    deepdive_tts_publish = runner.index("deepdive dialogue publish")
    youtube_build = runner.index("youtube podcast build_video")
    deepdive_youtube_build = runner.index("deepdive youtube podcast build_video")
    youtube_prepare = runner.index("youtube podcast prepare")
    deepdive_youtube_prepare = runner.index("deepdive youtube podcast prepare")
    digest_commit = runner.index("2.9 digest/data commit")
    docs_commit = runner.index("4. docs/ commit")
    push_start = runner.index("push origin HEAD:main start")
    publish_verify = runner.index("publish verification start")
    youtube_finalize = runner.index("youtube podcast finalize")
    deepdive_youtube_finalize = runner.index("deepdive youtube podcast finalize")
    podcast_verify = runner.index("podcast verification start")
    deepdive_podcast_verify = runner.index("deepdive podcast verification start")
    send_push = runner.index("send_push start")
    ok_marker = runner.rindex("news-grasp-runner.ps1 OK")

    assert tts_done < deepdive_tts_script < deepdive_tts_build < deepdive_tts_publish < digest_commit
    assert digest_commit < docs_commit < youtube_build < deepdive_youtube_build < youtube_prepare < deepdive_youtube_prepare < push_start
    assert push_start < publish_verify < youtube_finalize < deepdive_youtube_finalize < podcast_verify < deepdive_podcast_verify < send_push < ok_marker
    assert "tools.youtube_podcast.build_video" in runner
    assert "tools.youtube_podcast.upload_episode" in runner
    assert "tools.deepdive_quality" in runner
    assert "'materialize-issue'" in runner
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
    assert "Invoke-AutonomousCompletionPolicy -FailureKind 'distribution'" in youtube_block
    assert "GateId 'youtube-podcast-prepare'" in youtube_block


def test_runner_checks_youtube_oauth_before_podcast_work() -> None:
    """YouTube OAuth 失効は podcast build/upload の直前 gate で止める。"""
    runner = (ROOT / "scripts" / "ops" / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")

    readiness = runner.index("youtube oauth readiness gate start")
    build_video = runner.index("youtube podcast build_video")
    prepare = runner.index("youtube podcast prepare")

    assert "function Test-YouTubePodcastAuthReadiness" in runner
    assert "youtube oauth readiness failed" in runner
    assert readiness < build_video < prepare


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


def test_runner_builds_context_pack_before_deepdive_and_passes_it_to_tts() -> None:
    """DeepDive前処理はStage4起動より前、TTS台本生成には同じpackを渡す。"""
    runner = (ROOT / "scripts" / "ops" / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")

    pack_build = runner.index("tools.deepdive_context_pack")
    deepdive_start = runner.index("deepdive wrapper invoke START")
    tts_build = runner.index("deepdive dialogue script build")
    tts_context_arg = runner.index("--context-pack", tts_build)

    assert pack_build < deepdive_start
    assert tts_build < tts_context_arg
    assert "$DeepDiveContextPack" in runner
    assert "skipping deepdive codex because context pack failed" in runner
    assert "packなしで旧方式に戻さない" not in runner


def test_deepdive_prompts_use_context_pack_instead_of_broad_past_reads() -> None:
    runner_prompt = (ROOT / "prompts" / "deepdive-runner-prompt.md").read_text(encoding="utf-8-sig")
    system_prompt = (ROOT / "prompts" / "deepdive-research-system.md").read_text(encoding="utf-8-sig")
    combined = runner_prompt + "\n" + system_prompt

    assert "build/deepdive-context/{YYYY-MM-DD}.json" in combined
    assert "tools.deepdive_context_pack" in combined
    assert "digest/DeepDive/` 配下の**直近 3 本**の `.md`" not in combined
    assert "直近 3 本の frontmatter" not in combined
