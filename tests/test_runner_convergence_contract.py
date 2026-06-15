#!/usr/bin/env python3
"""日次 runner の責務分離と fallback publish 契約。"""
from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
RUNNER_PROMPT = ROOT / "prompts" / "runner-prompt.md"
ROUTINE_SYSTEM = ROOT / "prompts" / "routine-system.md"
RUNNER_PS1 = Path(os.environ.get("NEWS_GRASP_RUNNER", str(Path.home() / "bin" / "news-grasp-runner.ps1")))
WATCHER_PS1 = Path(os.environ.get("NEWS_GRASP_WATCHER", str(Path.home() / "bin" / "watch-news-grasp-runner.ps1")))


def test_claude_prompt_does_not_delegate_commit_to_claude() -> None:
    """Claude は生成専用で、commit/push/docs は runner 所有に固定する。"""
    prompt = RUNNER_PROMPT.read_text(encoding="utf-8")
    routine = ROUTINE_SYSTEM.read_text(encoding="utf-8")

    assert "commit まで" not in prompt
    assert "git commit / git push / docs 生成 / publish gate 実行は絶対に行わない" in prompt
    assert "生成した digest / data/articles.jsonl / data/archive / data/_status.md を保存したら停止する" in routine
    step6 = routine.split("### ステップ 6:", 1)[1].split("### ステップ 7:", 1)[0]
    assert "commit -m" not in step6
    assert "git -c user.name" not in step6


def test_runner_has_bounded_repair_and_fallback_publish() -> None:
    """gate 失敗後の戻り先が無制限 loop ではなく bounded repair + fallback であること。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")

    assert "Invoke-TargetedRepair" in runner
    assert "tools.gate_attempts" in runner
    assert "Invoke-FallbackPublish" in runner
    assert "published_fallback_with_notice" in runner
    assert "tools.validate_availability" in runner


def test_fallback_publish_restores_unverified_generated_artifacts() -> None:
    """fallback は公開 notice だけを残し、未検証 digest/data 差分を作業ツリーに残さない。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    fallback_body = runner.split("function Invoke-FallbackPublish", 1)[1].split("# ===== sentinel", 1)[0]

    assert "function Restore-UnverifiedGeneratedArtifacts" in runner
    assert "function Resolve-LastGoodDocsRef" in runner
    assert "Restore-UnverifiedGeneratedArtifacts" in fallback_body
    assert "Resolve-LastGoodDocsRef" in fallback_body
    assert "checkout $lastGoodDocsRef -- 'docs/'" in fallback_body
    assert "data/articles.jsonl" in runner
    assert "digest/" in runner


def test_daily_runner_timeout_is_80_minutes() -> None:
    """日次 digest 本体の wall-clock timeout は 80 分、idle 既定は 15 分に固定する。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")

    assert "$TimeoutSec = 4800" in runner
    assert "[int] $IdleTimeoutSec = 900" in runner


def test_runner_exposes_no_push_dry_run_switch() -> None:
    """NoPush では生成後 gate までは通し、git push と send_push を実行しない。

    なぜ重要か: Newsroom 切替後の慣らし運転で、本番公開や購読通知を出さずに
    gate と生成物だけ確認できる入口を runner に持たせるため。
    """
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")

    assert "[switch] $NoPush" in runner
    assert "NoPush mode: skipping git push origin main" in runner
    assert "NoPush mode: skipping send_push" in runner


def test_runner_idle_timeout_is_parameterized() -> None:
    """digest / DeepDive の idle timeout は runner パラメータから調整できる。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")

    assert "[int] $IdleTimeoutSec = 900" in runner
    assert "-TimeoutSec $TimeoutSec -IdleTimeoutSec $IdleTimeoutSec" in runner
    assert "-TimeoutSec $DeepDiveTimeoutSec -IdleTimeoutSec $IdleTimeoutSec" in runner


def test_runner_writes_machine_readable_state() -> None:
    """runner は foreground 待機に頼らず、終端状態を JSON state に書く。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")

    assert "news-grasp-runner-state.json" in runner
    assert "function Set-RunnerState" in runner
    assert "-Status 'running' -Message 'runner started'" in runner
    assert "-Status 'ok' -Message $Text -ExitCode 0" in runner
    assert "-Status 'fallback_ok' -Message $Text -ExitCode 0" in runner
    assert "-Status 'smoke_ok' -Message $Text -ExitCode 0" in runner
    assert "-Status 'error' -Message $Text -ExitCode 1" in runner


def test_runner_watcher_uses_hidden_start_and_terminal_state_polling() -> None:
    """watcher は runner を hidden 起動し、state/log の終端状態で完了判定する。"""
    watcher = WATCHER_PS1.read_text(encoding="utf-8-sig")

    assert "[switch] $StartOnly" in watcher
    assert "[switch] $Status" in watcher
    assert "[int] $StaleMinutes = 15" in watcher
    assert "[int] $TimeoutMinutes = 120" in watcher
    assert "Start-Process -FilePath 'powershell'" in watcher
    assert "-WindowStyle Hidden" in watcher
    assert "@('ok', 'fallback_ok', 'smoke_ok')" in watcher
    assert "runner process exited without ok marker" in watcher
    assert "log has not changed for" in watcher
    assert "watch timeout after" in watcher


def test_runner_record_gate_passes_issue_date() -> None:
    """record schema gate は号日整合 (--issue-date) を渡して当日号の date ズレを弾く。

    2026-06-11 に子プロセスが articles.jsonl の date (= 号日) を記事公開日と誤解釈して
    21 件誤記した class of bugs を、push 前 gate で機械検査するための契約。runner が
    `tools.validate_record` 呼び出しに `--issue-date` を必ず渡すことを locked-in する。
    """
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    assert "--issue-date" in runner, (
        "record schema gate が --issue-date を渡していない (号日整合チェックが効かない)"
    )


def test_runner_quarantines_bad_urls_before_fallback_publish() -> None:
    """URL gate 失敗は号全体 fallback 直行ではなく、記事単位隔離を先に試す。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    block = runner.split("URL liveness gate start", 1)[1].split("record schema gate start", 1)[0]

    assert "--quarantine-articles" in block
    assert "--apply" in block
    assert "URL liveness quarantine start" in block
    assert "URL liveness gate recheck after quarantine" in block
    assert block.index("URL liveness quarantine start") < block.index("Invoke-FallbackPublish")
    assert "search_audit_updated" in (ROOT / "tools" / "audit_all_article_urls.py").read_text(encoding="utf-8")


def test_content_gates_do_not_publish_fallback_notice() -> None:
    """内容系 gate の未収束は fallback notice 公開ではなく、通常公開を止める。

    なぜ重要か: publish-always 化の過渡期でも、内容系 gate の失敗で旧号 fallback notice を
    publish すると「本日分が存在するのに品質確認中へ落ちる」事故が再発する。
    """
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    assert "function Stop-ContentGateWithoutFallback" in runner

    content_gate_markers = [
        ("summary reflection gate start", "daily quality gate start"),
        ("daily quality gate start", "Stage4: Codex DeepDive"),
        ("record schema gate start", "digest/articles reconcile gate start"),
        ("digest/articles reconcile gate start", "ja-callout gate start"),
        ("ja-callout gate start", "pytest gate start"),
    ]
    for start, end in content_gate_markers:
        block = runner.split(start, 1)[1].split(end, 1)[0]
        assert "Stop-ContentGateWithoutFallback" in block
        assert "Invoke-FallbackPublish" not in block


def test_runner_requires_deepdive_after_pages_generation() -> None:
    """通常公開前に当日 DeepDive md/html の存在を gate する。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    assert "deepdive required gate start" in runner
    assert "--require-deepdive" in runner
    assert "--docs-root" in runner
    assert runner.index("generate_pages.py done") < runner.index("deepdive required gate start")
    assert runner.index("deepdive required gate start") < runner.index("public HTML gate start")
