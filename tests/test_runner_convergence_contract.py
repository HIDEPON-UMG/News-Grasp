#!/usr/bin/env python3
"""日次 runner の責務分離と fallback publish 契約。"""
from __future__ import annotations

import os
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
RUNNER_PROMPT = ROOT / "prompts" / "runner-prompt.md"
ROUTINE_SYSTEM = ROOT / "prompts" / "routine-system.md"
DEEPDIVE_PROMPT = ROOT / "prompts" / "deepdive-runner-prompt.md"
SETUP_DOC = ROOT / "SETUP.md"
RUNNER_PS1 = Path(os.environ.get("NEWS_GRASP_RUNNER", str(Path.home() / "bin" / "news-grasp-runner.ps1")))
WATCHER_PS1 = Path(os.environ.get("NEWS_GRASP_WATCHER", str(Path.home() / "bin" / "watch-news-grasp-runner.ps1")))
POWERSHELL = os.environ.get("NEWS_GRASP_POWERSHELL", "powershell")
OPS_DIR = ROOT / "scripts" / "ops"


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


def test_deepdive_prompt_does_not_delegate_git_to_agent() -> None:
    """DeepDive agent も生成専用で、git 操作は runner 側にだけ置く。"""
    prompt = DEEPDIVE_PROMPT.read_text(encoding="utf-8")

    assert "git -c user.name" not in prompt
    assert "add → commit" not in prompt
    assert "commit まで実行" not in prompt
    assert "git add / git commit / git push は絶対に実行しない" in prompt
    assert "runner が DeepDive / data/_status.md の commit と publish を一元管理" in prompt


def test_runner_has_bounded_repair_and_fallback_publish() -> None:
    """gate 失敗後の戻り先が無制限 loop ではなく bounded repair + fallback であること。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")

    assert "Invoke-TargetedRepair" in runner
    assert "tools.gate_attempts" in runner
    assert "Invoke-FallbackPublish" in runner
    assert "published_fallback_with_notice" in runner
    assert "tools.validate_availability" in runner


def test_setup_defines_daily_fix_completion_as_full_activation_path() -> None:
    """修正完了は fallback 保護ではなく、上流契約を満たした Activation Path で判定する。"""
    setup = SETUP_DOC.read_text(encoding="utf-8")

    assert "通常公開完了条件" in setup
    assert "fallback_ok は復旧完了ではなく本線保護" in setup
    assert "上流契約で防げる漏れを高コスト E2E に委ねない" in setup
    assert "E2E は省略せず必要な統合検証として残す" in setup
    assert "E2E を設計漏れのバグ発見機として濫用しない" in setup
    assert "E2E が見つけた前提漏れは runner / watcher / prompt / publish の責務境界" in setup
    assert "live runner と repo runner の checksum 一致" in setup
    assert "Task Scheduler が指す live runner" in setup
    assert "docs/YYYY-MM-DD/index.html" in setup
    assert "docs/publish-status.json の published_ok" in setup
    assert "公開 URL の sentinel" in setup


def test_runner_refuses_full_rerun_when_daily_artifacts_exist() -> None:
    """既存成果物がある日付で、明示 force なしに頭から回す経路を禁止する。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")

    assert "ForceFullRerun" in runner
    assert "Test-DailyArtifactsExist" in runner
    assert "existing daily artifacts detected; refusing full rerun" in runner
    assert "Use -ForceFullRerun only after explicit user approval" in runner


def test_targeted_repair_prompt_is_bounded_to_runner_owned_tools() -> None:
    """repair agent が bare python/uv/git/広域検索へ逃げず、runner の境界内だけで直す。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")

    assert "検証コマンドは必ず次の Python 実行体だけを使う" in runner
    assert "python / py / uv / .venv\\Scripts\\python.exe の直書きは禁止" in runner
    assert "git add / git commit / git push / git checkout / git reset は絶対に実行しない" in runner
    assert "rg / Get-ChildItem -Recurse / 広域 Select-String は禁止" in runner
    assert "runner_python:" in runner


def test_recover_only_does_not_disable_targeted_repair() -> None:
    """RecoverOnly でも欠落 inventory を fatal 終了だけにせず bounded repair へ進む。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    repair_body = runner.split("function Invoke-TargetedRepair", 1)[1].split("function Invoke-PythonGateWithRepair", 1)[0]

    assert "repair worker skipped: RecoverOnly mode" not in repair_body
    assert "if ($RecoverOnly)" not in repair_body
    assert "tools.gate_attempts" in repair_body
    assert "Invoke-CodexWrapper" in repair_body


def test_targeted_repair_prompt_regenerates_until_same_gate_passes() -> None:
    """欠落時の repair は fatal で終わらず、成果物再生成→同一 gate 再検証を要求する。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")

    assert "欠落成果物を再生成" in runner
    assert "同じ gate を再実行" in runner
    assert "PASS するまで" in runner
    assert "bounded retry" in runner


def test_inventory_repair_artifacts_cover_required_digest_and_docs() -> None:
    """repair prompt に欠落 inventory の実ファイルが渡るよう artifact scope を固定する。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")

    assert "Get-PublishInventoryArtifacts" in runner
    assert "tools.publish_inventory" in runner
    assert "$DailyDigestArtifacts = Get-PublishInventoryArtifacts -Kind 'digest'" in runner
    assert "$PublishedDocsArtifacts = Get-PublishInventoryArtifacts -Kind 'published'" in runner
    for rel in [
        "digest/AI/",
        "digest/Economy/",
        "digest/FX/",
        "digest/Game/",
        "digest/IT-Consulting/",
        "digest/Manufacturing/",
        "digest/Mobility/",
        "digest/Summary/",
        "data/articles.jsonl",
    ]:
        assert rel in runner or rel in (ROOT / "tools" / "publish_inventory.py").read_text(encoding="utf-8")
    assert "-GateId 'daily-quality'" in runner
    assert "-Artifacts $DailyDigestArtifacts" in runner

    for rel in [
        "docs/{date}/index.html",
        "docs/{date}/summary/index.html",
        "docs/{cat_id}/{date}/index.html",
        "digest/DeepDive/{date}-DeepDive.md",
        "docs/deepdive/{date}/index.html",
    ]:
        assert rel in (ROOT / "tools" / "publish_inventory.py").read_text(encoding="utf-8")
    assert "-GateId 'deepdive-required'" in runner
    assert "-Artifacts $PublishedDocsArtifacts" in runner


def test_runner_runs_generation_quality_before_url_and_record_gates() -> None:
    """生成物品質 gate は URL / record gate より前に normalize 済み artifact を検査する。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")

    assert "$GeneratedArtifacts = Get-PublishInventoryArtifacts -Kind 'generated'" in runner
    assert runner.index("generation artifact normalize start") < runner.index("generation quality gate start")
    assert runner.index("generation quality gate start") < runner.index("URL liveness gate start")
    assert runner.index("generation quality gate start") < runner.index("record schema gate start")


def test_generation_quality_gate_uses_bounded_repair() -> None:
    """generation-quality は独自 loop でなく既存 bounded repair gate に乗せる。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    block = runner.split("generation quality gate start", 1)[1].split("URL liveness gate start", 1)[0]

    assert "Invoke-PythonGateWithRepair" in block
    assert "-GateId 'generation-quality'" in block
    assert "-Category 'generated'" in block
    assert "-Artifacts $GeneratedArtifacts" in block
    assert "tools.validate_generation_quality" in block


def test_generation_quality_repair_failure_sets_content_repair_failed() -> None:
    """生成品質 repair が収束しない場合は publish 系 state と混ぜない。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    block = runner.split("generation quality gate start", 1)[1].split("URL liveness gate start", 1)[0]

    assert "content_repair_failed" in block
    assert "generation quality repair failed" in block
    assert "Invoke-FallbackPublish" not in block
    assert "send_push" not in block


def test_generation_quality_repair_prompt_is_item_scoped() -> None:
    """repair prompt は error JSON と artifact scope を見せ、無関係 artifact へ広げない。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    repair_body = runner.split("function Invoke-TargetedRepair", 1)[1].split("function Invoke-PythonGateWithRepair", 1)[0]

    assert "gate_id: $GateId" in repair_body
    assert "失敗ログ" in repair_body
    assert "対象 artifact 以外" in repair_body
    assert "full rerun" in repair_body
    assert "publish 実行は禁止" in repair_body


def test_targeted_repair_rejects_changes_outside_artifact_scope() -> None:
    """repair worker が対象 artifact 以外を触ったら同じ gate の再試行へ進ませない。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    gate_body = runner.split("function Invoke-PythonGateWithRepair", 1)[1].split("function Restore-UnverifiedGeneratedArtifacts", 1)[0]

    assert "Test-RepairArtifactScope" in runner
    assert "Snapshot-RepairWorkspace" in runner
    assert "repair worker changed files outside artifact scope" in runner
    assert "if (-not (Test-RepairArtifactScope" in gate_body


def test_generation_quality_runs_after_external_readiness_precheck() -> None:
    """外部 readiness 不足は生成品質 failure と混ぜず、generation-quality 前に止める。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")

    assert "generation external readiness gate start" in runner
    assert "Test-GenerationExternalReadiness" in runner
    assert runner.index("generation external readiness gate start") < runner.index("generation quality gate start")
    readiness_block = runner.split("generation external readiness gate start", 1)[1].split("generation artifact normalize start", 1)[0]
    assert "blocked_external_readiness" in readiness_block
    assert "content_repair_failed" not in readiness_block


def test_preflight_only_writes_terminal_state() -> None:
    """PreflightOnly 成功は running のままにせず preflight_ok を state に残す。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    preflight_block = runner.split("PreflightOnly mode: skipping codex / git pull / push / generate_pages", 1)[1].split("# ===== 0.5", 1)[0]

    exit_runner_body = runner.split("function Exit-Runner", 1)[1].split("function Write-Log", 1)[0]
    assert "Set-RunnerState -Status $Status -Message $Message -ExitCode $ExitCode" in exit_runner_body
    assert "Exit-Runner -Status 'preflight_ok'" in preflight_block


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


def test_fallback_publish_never_sends_web_push() -> None:
    """fallback publish は公開本体の保護だけで、購読通知へは到達させない。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    fallback_body = runner.split("function Invoke-FallbackPublish", 1)[1].split("# ===== sentinel", 1)[0]

    assert "fallback notification skipped: not a normal batch" in fallback_body
    assert "tools\\send_push.py" not in fallback_body
    assert "fallback send_push" not in fallback_body


def test_send_push_requires_normal_batch_publish_verification() -> None:
    """通知は通常バッチの公開反映確認が通った後だけ実行する。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    notify_gate = runner.split("function Should-SendNormalBatchNotification", 1)[1].split("# ===== sentinel", 1)[0]
    send_block = runner.split("# ===== 6. Web Push", 1)[1].split("Write-CodexUsageWindowSnapshot -Phase 'end'", 1)[0]

    assert "$NormalPublishVerified = $false" in runner
    assert "$NormalPublishVerified = $true" in runner
    assert "$NormalPublishVerified" in notify_gate
    assert "-not $NoPush" in notify_gate
    assert "-not $RecoverOnly" in notify_gate
    assert "Should-SendNormalBatchNotification" in send_block
    assert "RecoverOnly mode: skipping send_push (not a normal batch)" in send_block
    assert runner.index("publish verification OK") < runner.index("send_push start")


def test_external_readiness_failures_write_blocked_state() -> None:
    """外部 readiness 不足は warn skip ではなく終端 state を残して止める。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    net_wait_block = runner.split("net reachability wait start", 1)[1].split("# ===== 1. git fetch", 1)[0]

    assert "function Stop-ExternalReadiness" in runner
    assert "blocked_external_readiness" in runner
    assert "Stop-ExternalReadiness" in net_wait_block
    assert "WARN: net_wait.py not found" not in net_wait_block


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


def test_runner_is_repo_managed_and_checks_live_checksum() -> None:
    """bin 実行体 drift は手動 install 待ちにせず自己同期して再起動する。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    repo_runner = OPS_DIR / "news-grasp-runner.ps1"
    repo_watcher = OPS_DIR / "watch-news-grasp-runner.ps1"

    assert repo_runner.exists()
    assert repo_watcher.exists()
    forbidden_local_user_path = "C:" + "\\Users\\" + "hide" + "k"
    assert forbidden_local_user_path not in runner
    assert "function Assert-RunnerBinaryInSync" in runner
    assert "function Invoke-RunnerBinarySelfUpdate" in runner
    assert "NEWS_GRASP_RUNNER_SYNC_REEXEC" in runner
    assert "runner binary drift repaired; relaunching synced runner" in runner
    assert "scripts\\ops\\news-grasp-runner.ps1" in runner
    assert "runner binary drift" in runner
    assert "Run scripts/ops/install-news-grasp-ops.ps1 before scheduled execution" not in runner


def test_runner_only_marks_ok_after_publish_verification() -> None:
    """ok marker は push 後の公開反映確認より後にしか書けない。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")

    assert "tools.daily_self_heal' 'verify-publish'" in runner
    assert runner.index("publish verification start") < runner.index("send_push start")
    assert runner.index("publish verification start") < runner.rindex("news-grasp-runner.ps1 OK")


def test_deadman_wrapper_exists_and_uses_non_webpush_alert_log() -> None:
    """Web Push 以外の dead-man alert 経路を repo 管理下に置く。"""
    script = OPS_DIR / "news-grasp-deadman.ps1"

    assert script.exists()
    text = script.read_text(encoding="utf-8-sig")
    forbidden_local_user_path = "C:" + "\\Users\\" + "hide" + "k"
    assert forbidden_local_user_path not in text
    assert "tools.daily_self_heal" in text
    assert "deadman" in text
    assert "news-grasp-alerts" in text
    assert "$exitCode -eq 2" in text
    assert "exit 0" in text
    assert "Invoke-RecoverOnlyIfStaleDeadPid" in text
    assert "watch-news-grasp-runner.ps1" in text
    assert "-StartOnly" in text
    assert "-RecoverOnly" in text


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


def test_watcher_status_reports_stale_when_running_pid_is_dead(tmp_path: Path) -> None:
    """Status 表示は stale running を「まだ実行中」と誤表示しない。"""
    state_file = tmp_path / "state.json"
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log_path = log_dir / "2026-06-15.log"
    log_path.write_text("stale\n", encoding="utf-8")
    state_file.write_text(
        json.dumps(
            {
                "status": "running",
                "message": "runner started",
                "exit_code": -1,
                "updated_at": "2026-06-15T20:07:40.343+09:00",
                "date": "2026-06-15",
                "pid": 999999,
                "repo_dir": str(tmp_path / "repo"),
                "log_path": str(log_path),
                "started_at": "2026-06-15T20:07:40.343+09:00",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(WATCHER_PS1),
            "-Status",
            "-StateFile",
            str(state_file),
            "-LogDir",
            str(log_dir),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "stale"
    assert payload["process_alive"] is False
    assert "process is not alive" in payload["message"]


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


def test_runner_tts_is_additive_and_non_fatal_before_pages_generation() -> None:
    """TTS 生成・公開は additive として扱い、通常号 publish 本線を止めない。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    assert "Daily TTS audio (non-fatal" in runner
    assert "tools.tts.build_script" in runner
    assert "tools.tts.synthesize_daily" in runner
    assert "tools.tts.publish_audio" in runner
    assert runner.index("pytest gate OK") < runner.index("Daily TTS audio (non-fatal")
    assert runner.index("Daily TTS audio (non-fatal") < runner.index("2.9 digest/data commit")
    block = runner.split("Daily TTS audio (non-fatal", 1)[1].split("2.9 digest/data commit", 1)[0]
    assert "WARN:" in block
    assert "non-fatal" in block
    assert "Invoke-FallbackPublish" not in block
    assert "Stop-ContentGateWithoutFallback" not in block


def test_runner_tts_does_not_send_normal_notification() -> None:
    """TTS 失敗・成功だけで通常通知を送らず、通知は通常 publish verified 後に限定する。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    tts_block = runner.split("Daily TTS audio (non-fatal", 1)[1].split("2.9 digest/data commit", 1)[0]
    send_push_index = runner.index("send_push start")
    assert "send_push" not in tts_block
    assert "Should-SendNormalBatchNotification" in runner
    assert runner.index("publish verification start") < send_push_index


def test_runner_preflight_checks_workspace_write_readiness_before_generation() -> None:
    """OneDrive/file lock/disk 問題は生成後ではなく開始前に検出する。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")

    assert "function Test-WorkspaceWriteReadiness" in runner
    assert "workspace write readiness gate start" in runner
    assert "workspace write readiness gate OK" in runner
    assert "blocked_external_readiness" in runner
    assert runner.index("workspace write readiness gate start") < runner.index("Stage0: deterministic candidate harvest")
    preflight_block = runner.split("if ($PreflightOnly)", 1)[1].split("if ($SmokeTest)", 1)[0]
    assert "Test-WorkspaceWriteReadiness" in preflight_block


def test_runner_checks_publish_external_readiness_before_expensive_generation() -> None:
    """git remote / push auth の明白な失敗は LLM 実行前に blocked_external_readiness へ分離する。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")

    assert "function Test-PublishExternalReadiness" in runner
    assert "publish external readiness gate start" in runner
    assert "git ls-remote origin main" in runner
    assert "git push --dry-run origin HEAD:main" in runner
    assert runner.index("publish external readiness gate start") < runner.index("Stage0: deterministic candidate harvest")


def test_runner_stage0_harvest_uses_last_good_candidate_fallback() -> None:
    """一時的な収集元ブロックは last-good 候補で bounded fallback し、無ければ外部readiness停止に分ける。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")

    assert "CandidateLastGoodDir" in runner
    assert "Stage0 harvest fallback from last-good" in runner
    assert "Stage0 harvest no last-good candidates" in runner
    assert "Stop-ExternalReadiness" in runner
    assert "Copy-Item -LiteralPath $outPath -Destination $lastGoodPath -Force" in runner
