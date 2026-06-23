#!/usr/bin/env python3
"""日次 runner の責務分離と fallback publish 契約。"""
from __future__ import annotations

import os
import json
import re
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


def _normalized_powershell_statements(text: str, marker: str) -> list[str]:
    lines = text.splitlines()
    statements: list[str] = []
    for index, line in enumerate(lines):
        if marker not in line:
            continue
        collected = [line.rstrip()]
        balance = line.count("(") - line.count(")")
        cursor = index + 1
        while cursor < len(lines):
            previous_continues = collected[-1].rstrip().endswith("`")
            next_line = lines[cursor].rstrip()
            next_stripped = next_line.strip()
            if not previous_continues and balance <= 0 and not next_stripped.startswith("-"):
                break
            collected.append(next_line)
            balance += next_line.count("(") - next_line.count(")")
            cursor += 1
        normalized = " ".join(part.rstrip("`").strip() for part in collected)
        statements.append(re.sub(r"\s+", " ", normalized).strip())
    return statements


def _contains_powershell_variable(statement: str, variable_name: str) -> bool:
    return bool(re.search(re.escape(variable_name) + r"(?![A-Za-z0-9_])", statement, re.IGNORECASE))


def _powershell_command_extents(path: Path, command_name: str) -> list[str]:
    """PowerShell Parser で実コマンド AST を抽出し、静的 scan の対象を構文単位へ寄せる。"""
    script = r"""
$Path = $env:NEWS_GRASP_AST_PATH
$CommandName = $env:NEWS_GRASP_AST_COMMAND
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$tokens, [ref]$errors)
if ($errors.Count -gt 0) {
  $errors | ForEach-Object { Write-Error $_.Message }
  exit 2
}
$commands = $ast.FindAll({
  param($node)
  $node -is [System.Management.Automation.Language.CommandAst] -and
    $node.GetCommandName() -eq $CommandName
}, $true)
@($commands | ForEach-Object { $_.Extent.Text }) | ConvertTo-Json -Compress
"""
    env = os.environ.copy()
    env["NEWS_GRASP_AST_PATH"] = str(path)
    env["NEWS_GRASP_AST_COMMAND"] = command_name
    result = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout or "[]")
    if isinstance(data, str):
        return [data]
    return [str(item) for item in data]


def _assert_runner_powershell_parses() -> None:
    script = r"""
$Path = $env:NEWS_GRASP_AST_PATH
$tokens = $null
$errors = $null
[void][System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$tokens, [ref]$errors)
if ($errors.Count -gt 0) {
  $errors | ForEach-Object { Write-Error $_.Message }
  exit 2
}
"""
    env = os.environ.copy()
    env["NEWS_GRASP_AST_PATH"] = str(OPS_DIR / "news-grasp-runner.ps1")
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        env=env,
    )
    assert result.returncode == 0, result.stderr


def _mock_autonomous_policy_invocation(failure_kind: str) -> dict[str, str]:
    script = r"""
function Invoke-FallbackPublish {
  param([string]$Reason)
  $script:FallbackReason = $Reason
}
function Write-Log {
  param([string]$Text)
  $script:LastLog = $Text
}
function Exit-Runner {
  param(
    [string]$Status,
    [string]$Message,
    [int]$ExitCode,
    [string]$ExternalKind = '',
    [string]$ExternalSystem = '',
    [string]$ExternalStatus = '',
    [string]$ExternalStderr = '',
    [string]$ExternalDetail = ''
  )
  $script:ExitStatus = $Status
  $script:ExitMessage = $Message
  $script:ExitCode = $ExitCode
  $script:ExternalKind = $ExternalKind
  $script:ExternalSystem = $ExternalSystem
  $script:ExternalStatus = $ExternalStatus
  $script:ExternalStderr = $ExternalStderr
  $script:ExternalDetail = $ExternalDetail
}
$runner = Get-Content -LiteralPath $env:NEWS_GRASP_RUNNER_PATH -Raw -Encoding UTF8
$start = $runner.IndexOf('function Invoke-AutonomousCompletionPolicy')
if ($start -lt 0) { Write-Error 'Invoke-AutonomousCompletionPolicy missing'; exit 2 }
$end = $runner.IndexOf('function Test-DailyArtifactsExist', $start)
if ($end -lt 0) { Write-Error 'policy end marker missing'; exit 2 }
Invoke-Expression $runner.Substring($start, $end - $start)
Invoke-AutonomousCompletionPolicy -FailureKind $env:NEWS_GRASP_FAILURE_KIND -GateId 'unit-gate' -Reason 'unit-test' -ExitCode 42
[pscustomobject]@{
  fallback_reason = $script:FallbackReason
  exit_status = $script:ExitStatus
  exit_message = $script:ExitMessage
  exit_code = $script:ExitCode
  external_kind = $script:ExternalKind
  external_system = $script:ExternalSystem
  external_status = $script:ExternalStatus
  external_stderr = $script:ExternalStderr
  external_detail = $script:ExternalDetail
} | ConvertTo-Json -Compress
"""
    env = os.environ.copy()
    env["NEWS_GRASP_RUNNER_PATH"] = str(OPS_DIR / "news-grasp-runner.ps1")
    env["NEWS_GRASP_FAILURE_KIND"] = failure_kind
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _mock_external_readiness_block() -> dict:
    script = r"""
function Write-Log {
  param([string]$Text)
  $script:LastLog = $Text
}
function Exit-Runner {
  param(
    [string]$Status,
    [string]$Message,
    [int]$ExitCode,
    [string]$ExternalKind = '',
    [string]$ExternalSystem = '',
    [string]$ExternalStatus = '',
    [string]$ExternalStderr = '',
    [string]$ExternalDetail = ''
  )
  [pscustomobject]@{
    status = $Status
    message = $Message
    exit_code = $ExitCode
    external_readiness = [ordered]@{
      kind = $ExternalKind
      system = $ExternalSystem
      status = $ExternalStatus
      stderr = $ExternalStderr
      detail = $ExternalDetail
    }
  } | ConvertTo-Json -Compress
}
$runner = Get-Content -LiteralPath $env:NEWS_GRASP_RUNNER_PATH -Raw -Encoding UTF8
$start = $runner.IndexOf('function Stop-ExternalReadiness')
if ($start -lt 0) { Write-Error 'Stop-ExternalReadiness missing'; exit 2 }
$end = $runner.IndexOf('function Test-WorkspaceWriteReadiness', $start)
if ($end -lt 0) { Write-Error 'Stop-ExternalReadiness end marker missing'; exit 2 }
Invoke-Expression $runner.Substring($start, $end - $start)
Stop-ExternalReadiness -Kind 'git_push_auth' -System 'github' -Reason 'push dry-run failed' -ExitCode 71 -ExternalStatus 'rc=1' -ExternalStderr 'fatal auth' -ExternalDetail 'origin HEAD:main'
"""
    env = os.environ.copy()
    env["NEWS_GRASP_RUNNER_PATH"] = str(OPS_DIR / "news-grasp-runner.ps1")
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _mock_recoveronly_manifest(tmp_path: Path) -> dict:
    script = r"""
$RepoDir = $env:NEWS_GRASP_REPO_DIR
$DateStamp = '2026-06-23'
$StateFile = Join-Path $RepoDir 'state.json'
$GitExe = 'git'
function Write-Log { param([string]$Text) }
function Get-PublishInventoryArtifacts {
  param([string]$Kind)
  return @('digest/Summary/2026-06-23.md', 'data/articles.jsonl')
}
$runner = Get-Content -LiteralPath $env:NEWS_GRASP_RUNNER_PATH -Raw -Encoding UTF8
$start = $runner.IndexOf('function Write-RecoverOnlyInputManifest')
if ($start -lt 0) { Write-Error 'Write-RecoverOnlyInputManifest missing'; exit 2 }
    $end = $runner.IndexOf('function Test-DailyArtifactsExist', $start)
if ($end -lt 0) { Write-Error 'Write-RecoverOnlyInputManifest end marker missing'; exit 2 }
Invoke-Expression $runner.Substring($start, $end - $start)
$path = Write-RecoverOnlyInputManifest
Get-Content -LiteralPath $path -Raw -Encoding UTF8
"""
    (tmp_path / "digest" / "Summary").mkdir(parents=True)
    (tmp_path / "digest" / "Summary" / "2026-06-23.md").write_text("summary", encoding="utf-8")
    env = os.environ.copy()
    env["NEWS_GRASP_RUNNER_PATH"] = str(OPS_DIR / "news-grasp-runner.ps1")
    env["NEWS_GRASP_REPO_DIR"] = str(tmp_path)
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _mock_distribution_manifest(tmp_path: Path) -> dict:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("test\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "test"], cwd=tmp_path, check=True, capture_output=True, text=True)
    expected_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True).strip()
    script = r"""
$RepoDir = $env:NEWS_GRASP_REPO_DIR
$DateStamp = '2026-06-23'
$GitExe = 'git'
function Write-Log { param([string]$Text) }
$runner = Get-Content -LiteralPath $env:NEWS_GRASP_RUNNER_PATH -Raw -Encoding UTF8
$start = $runner.IndexOf('function Write-DistributionManifest')
if ($start -lt 0) { Write-Error 'Write-DistributionManifest missing'; exit 2 }
$end = $runner.IndexOf('function Test-DailyArtifactsExist', $start)
if ($end -lt 0) { Write-Error 'Write-DistributionManifest end marker missing'; exit 2 }
Invoke-Expression $runner.Substring($start, $end - $start)
$path = Write-DistributionManifest
Get-Content -LiteralPath $path -Raw -Encoding UTF8
"""
    env = os.environ.copy()
    env["NEWS_GRASP_RUNNER_PATH"] = str(OPS_DIR / "news-grasp-runner.ps1")
    env["NEWS_GRASP_REPO_DIR"] = str(tmp_path)
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    payload["_expected_head"] = expected_head
    return payload


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
    assert "$PublishedRepairArtifacts = Get-PublishInventoryArtifacts -Kind 'published-repair'" in runner
    assert "-Artifacts $PublishedRepairArtifacts" in runner


def test_codex_auth_preflight_runs_before_llm_repair() -> None:
    """LLM repair 前に Codex 認証切れを content failure と分離して止める。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    repair_body = runner.split("function Invoke-TargetedRepair", 1)[1].split("function Snapshot-RepairWorkspace", 1)[0]

    assert "function Test-CodexAuthReadiness" in runner
    assert "codex auth readiness gate start" in repair_body
    assert "blocked_codex_auth" in repair_body
    assert "Invoke-CodexWrapper" in repair_body
    assert repair_body.index("Test-CodexAuthReadiness") < repair_body.index("Invoke-CodexWrapper")


def test_runner_never_passes_long_prompt_or_html_via_native_argument() -> None:
    """長大 prompt/report/html 本文は file/stdin 境界に閉じ、native argv へ載せない。"""
    runner_path = OPS_DIR / "news-grasp-runner.ps1"
    wrapper_path = Path.home() / "bin" / "run_codex_with_timeout.ps1"
    runner = runner_path.read_text(encoding="utf-8-sig")
    wrapper = wrapper_path.read_text(encoding="utf-8-sig")

    codex_calls = [
        re.sub(r"\s+", " ", stmt).strip()
        for stmt in _powershell_command_extents(runner_path, "Invoke-CodexWrapper")
        if not stmt.startswith("function Invoke-CodexWrapper")
    ]
    assert codex_calls
    body_vars = [
        "$prompt",
        "$promptText",
        "$PromptBody",
        "$reporterPrompt",
        "$failureText",
        "$DateHeader",
        "$html",
        "$reportHtml",
    ]
    for call in codex_calls:
        assert re.search(r"(?:^|\s)-PromptFile(?:\s|$)", call), (
            f"long prompt must be file/stdin, not native argv: {call}"
        )
        assert not re.search(r"(?:^|\s)-Prompt(?!File)(?:\s|$)", call), (
            f"long prompt must be file/stdin, not native argv: {call}"
        )
        for var_name in body_vars:
            assert not _contains_powershell_variable(call, var_name), (
                f"long prompt must be file/stdin, not native argv: {call}"
            )

    argument_boundaries = (
        _normalized_powershell_statements(runner, "-ArgumentList")
        + [re.sub(r"\s+", " ", stmt).strip() for stmt in _powershell_command_extents(wrapper_path, "Start-Process")]
    )
    assert argument_boundaries
    for statement in argument_boundaries:
        for var_name in body_vars:
            assert not _contains_powershell_variable(statement, var_name), (
                f"long prompt must be file/stdin, not native argv: {statement}"
            )

    start_process = " ".join(_normalized_powershell_statements(wrapper, "Start-Process"))
    assert "-RedirectStandardInput $stdinFile" in start_process
    assert "-ArgumentList $effectiveArgString" in start_process
    assert "--prompt" not in wrapper
    assert "$argList += $promptText" not in wrapper


def test_runner_runs_generation_quality_before_url_and_record_gates() -> None:
    """生成物品質 gate は URL / record gate より前に normalize 済み artifact を検査する。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")

    assert "$GeneratedArtifacts = Get-PublishInventoryArtifacts -Kind 'generated'" in runner
    assert runner.index("generation artifact normalize start") < runner.index("generation quality gate start")
    assert runner.index("generation quality gate start") < runner.index("URL liveness gate start")
    assert runner.index("generation quality gate start") < runner.index("record schema gate start")


def test_generation_quality_gate_uses_autonomous_gate() -> None:
    """generation-quality は分類つき自走 gate に乗せる。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    block = runner.split("generation quality gate start", 1)[1].split("URL liveness gate start", 1)[0]

    assert "Invoke-AutonomousGate" in block
    assert "-GateId 'generation-quality'" in block
    assert "-Category 'generated'" in block
    assert "-Artifacts $GeneratedArtifacts" in block
    assert "tools.validate_generation_quality" in block


def test_generation_quality_repair_failure_sets_typed_repair_status() -> None:
    """生成品質 repair が収束しない場合は旧 content_repair_failed に直行しない。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    block = runner.split("generation quality gate start", 1)[1].split("URL liveness gate start", 1)[0]

    assert "Invoke-AutonomousCompletionPolicy" in block
    assert "-FailureKind 'content'" in block
    assert "-GateId 'generation-quality'" in block
    assert "generation quality autonomous gate failed" in block
    assert "content_repair_failed" not in block
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
    gate_body = runner.split("function Invoke-AutonomousGate", 1)[1].split("function Preserve-UnverifiedGeneratedArtifacts", 1)[0]

    assert "Test-RepairArtifactScope" in runner
    assert "Snapshot-RepairWorkspace" in runner
    assert "repair worker changed files outside artifact scope" in runner
    assert "Invoke-PythonGateWithRepair" in gate_body
    assert "if (-not (Test-RepairArtifactScope" in runner


def test_generation_quality_runs_after_external_readiness_precheck() -> None:
    """外部 readiness 不足は生成品質 failure と混ぜず、generation-quality 前に止める。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")

    assert "generation external readiness gate start" in runner
    assert "Test-GenerationExternalReadiness" in runner
    assert runner.index("generation external readiness gate start") < runner.index("generation quality gate start")
    readiness_block = runner.split("generation external readiness gate start", 1)[1].split("generation artifact normalize start", 1)[0]
    assert "Stop-ExternalReadiness" in readiness_block
    assert "-Kind 'generation_input_missing'" in readiness_block
    assert "content_repair_failed" not in readiness_block


def test_url_liveness_refill_expands_json_categories_before_native_call() -> None:
    """PowerShell 5.1 の JSON 配列差を吸収し、category を 1 件ずつ native command に渡す。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    refill_block = runner.split("URL liveness refill start", 1)[1].split("URL liveness gate recheck after quarantine", 1)[0]

    assert "Convert-JsonStringArrayToStringList" in runner
    assert "$refillCategories = Convert-JsonStringArrayToStringList -JsonText $refillCategoriesJson" in refill_block
    assert "@($refillCategoriesJson | ConvertFrom-Json)" not in refill_block
    assert "foreach ($nestedItem in $item)" in runner
    assert "refill category contains whitespace" in refill_block
    assert "'--category' $refillCat" in refill_block


def test_runner_url_liveness_does_not_require_interactive_session_whitelist() -> None:
    """非対話 runner は session hook 台帳不在だけで公開を止めず、URL 物理 gate を本線にする。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    url_block = runner.split("URL liveness gate start", 1)[1].split("record schema gate start", 1)[0]

    assert "--match-session" in url_block
    assert "--require-session" not in url_block
    assert "data/_session_urls.json" in url_block
    assert "data/_session_urls.d" in url_block


def test_preflight_only_writes_terminal_state() -> None:
    """PreflightOnly 成功は running のままにせず preflight_ok を state に残す。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    preflight_block = runner.split("PreflightOnly mode: skipping codex / git pull / push / generate_pages", 1)[1].split("# ===== 0.5", 1)[0]

    exit_runner_body = runner.split("function Exit-Runner", 1)[1].split("function Write-Log", 1)[0]
    assert "Set-RunnerState -Status $Status -Message $Message -ExitCode $ExitCode" in exit_runner_body
    assert "Exit-Runner -Status 'preflight_ok'" in preflight_block


def test_fallback_publish_quarantines_unverified_generated_artifacts() -> None:
    """fallback は復旧可能な当日 artifact を削除せず quarantine に保存する。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    fallback_body = runner.split("function Invoke-FallbackPublish", 1)[1].split("# ===== sentinel", 1)[0]

    assert "function Preserve-UnverifiedGeneratedArtifacts" in runner
    assert "function Resolve-LastGoodDocsRef" in runner
    assert "Preserve-UnverifiedGeneratedArtifacts" in fallback_body
    assert "Resolve-LastGoodDocsRef" in fallback_body
    assert "checkout $lastGoodDocsRef -- 'docs/'" in fallback_body
    assert "build\\quarantine\\$DateStamp" in runner
    assert "Copy-Item" in runner
    assert "Remove-Item -LiteralPath $full -Recurse -Force" not in runner
    assert "data/articles.jsonl" in runner
    assert "digest/" in runner


def test_repair_scope_allows_runner_state_and_ignores_temp_outputs() -> None:
    """repair scope は runner 管理 state と pytest 一時生成物を artifact 違反にしない。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    scope_body = runner.split("function Test-RepairArtifactScope", 1)[1].split("function Test-GenerationExternalReadiness", 1)[0]

    assert "Test-RepairStatusPathAllowed" in runner
    assert "data/gate_attempts/$DateStamp.json" in runner
    assert ".pytest-tmp/" in runner
    assert "build/codex-usage/" in runner
    assert "runner-owned state" in runner
    assert "Test-RepairStatusPathAllowed -Path $path" in scope_body


def test_published_repair_inventory_json_array_flattens_for_windows_powershell() -> None:
    """PowerShell 5.1 でも JSON 配列 inventory を artifact path 配列として扱う。"""
    script = r"""
$runner = Get-Content -LiteralPath $env:NEWS_GRASP_RUNNER_PATH -Raw -Encoding UTF8
$convertStart = $runner.IndexOf('function Convert-PublishInventoryJson')
$convertEnd = $runner.IndexOf('function Get-PublishInventoryArtifacts', $convertStart)
if ($convertStart -lt 0 -or $convertEnd -lt 0) { Write-Error 'Convert-PublishInventoryJson block missing'; exit 2 }
$scopeStart = $runner.IndexOf('function Test-RepairStatusPathAllowed')
$scopeEnd = $runner.IndexOf('function Test-RepairArtifactScope', $scopeStart)
if ($scopeStart -lt 0 -or $scopeEnd -lt 0) { Write-Error 'Test-RepairStatusPathAllowed block missing'; exit 2 }
$script:DateStamp = '2026-06-23'
Invoke-Expression $runner.Substring($convertStart, $convertEnd - $convertStart)
Invoke-Expression $runner.Substring($scopeStart, $scopeEnd - $scopeStart)
$json = '["data/search_audit/2026-06-23","docs/index.html"]'
$items = @(Convert-PublishInventoryJson -Json @($json))
$allowed = @($items | ForEach-Object { ([string]$_).Trim().Replace('\', '/') } | Where-Object { $_ })
$childAllowed = Test-RepairStatusPathAllowed -Path 'data/search_audit/2026-06-23/it.json' -AllowedArtifacts $allowed
[pscustomobject]@{
  count = $items.Count
  first = [string]$items[0]
  second = [string]$items[1]
  child_allowed = $childAllowed
} | ConvertTo-Json -Compress
"""
    env = os.environ.copy()
    env["NEWS_GRASP_RUNNER_PATH"] = str(OPS_DIR / "news-grasp-runner.ps1")
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads(result.stdout)
    assert data == {
        "count": 2,
        "first": "data/search_audit/2026-06-23",
        "second": "docs/index.html",
        "child_allowed": True,
    }


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
    assert "-Status 'publish_complete' -Message $Text -ExitCode 0" in runner
    assert "-Status 'ok' -Message $Text -ExitCode 0" not in runner
    assert "-Status 'fallback_ok' -Message $Text -ExitCode 0" in runner
    assert "-Status 'smoke_ok' -Message $Text -ExitCode 0" in runner
    assert "-Status 'error' -Message $Text -ExitCode 1" in runner


def test_runner_typed_terminal_state_can_replace_generic_error() -> None:
    """ERROR ログの副作用で typed terminal state を generic error に潰さない。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    state_body = runner.split("function Set-RunnerState", 1)[1].split("function Exit-Runner", 1)[0]

    assert "typed terminal state must replace generic error" in state_body
    assert "$prev.status -eq 'error'" in state_body
    for status in ["blocked_external_readiness", "publish_failed", "distribution_failed"]:
        assert status in state_body


def test_runner_state_is_progress_aware_and_terminal_first_wins() -> None:
    """長時間工程は heartbeat を残し、terminal state は後続更新で上書きしない。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    state_body = runner.split("function Set-RunnerState", 1)[1].split("function Exit-Runner", 1)[0]

    assert "$RunId = [guid]::NewGuid().ToString('N')" in runner
    assert "command_line_fingerprint" in state_body
    assert "process_creation_time" in state_body
    assert "heartbeat_at" in state_body
    assert "deadline_at" in state_body
    assert "phase" in state_body
    assert "Invoke-WithRunnerStateLock" in runner
    assert "Local\\NewsGraspRunnerState-" in runner
    assert "[System.IO.File]::Replace" in runner
    assert "Test-TerminalRunnerStatus" in runner
    assert "first-terminal-wins" in runner
    assert "blocked_runner_state_lock_timeout" in runner
    assert "blocked_runner_state_corrupt" in runner


def test_runner_progress_updates_long_running_phases() -> None:
    """reporter / gate / repair / publish / podcast の長時間工程は進捗 state を更新する。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")

    assert "function Update-RunnerProgress" in runner
    assert "Update-RunnerProgress -Phase 'reporter'" in runner
    assert "Update-RunnerProgress -Phase 'gate'" in runner
    assert "Update-RunnerProgress -Phase 'repair'" in runner
    assert "Update-RunnerProgress -Phase 'publish-verify'" in runner
    assert "Update-RunnerProgress -Phase 'podcast-verify'" in runner
    assert "active_jobs" in runner
    assert "GateDeadlineSec" in runner
    assert "blocked_gate_timeout" in runner


def test_reporter_wave_uses_supervisor_loop_instead_of_blind_wait_job() -> None:
    """reporter wave は親runnerが沈黙しないよう job を監視し続ける。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    reporter_body = runner.split("function Invoke-ReporterWave", 1)[1].split("$retryCategories = @($Categories)", 1)[0]

    assert "Wait-Job -Job $jobs | Out-Null" not in reporter_body
    assert "Wait-Job -Job @($jobs | Where-Object { $_.State -eq 'Running' }) -Any" not in reporter_body
    assert "ReporterPollSeconds" in reporter_body
    assert "ReporterHeartbeatSeconds" in reporter_body
    assert "Update-RunnerProgress -Phase 'reporter'" in reporter_body
    assert "active_jobs" in reporter_body
    assert "Append-ReporterWrapperLog" in reporter_body
    assert "wrapper_log_offsets" in reporter_body
    assert "Stop-Job -Job $job -Force" in reporter_body
    assert "Remove-Job -Job $job -Force" in reporter_body
    assert "blocked_reporter_timeout" in reporter_body
    assert "blocked_reporter_repeated_failure" in runner


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


def test_runner_only_marks_publish_complete_after_publish_verification() -> None:
    """publish_complete marker は publish + podcast verification より後にしか書けない。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")

    assert "tools.daily_self_heal' 'verify-publish'" in runner
    assert "tools.daily_self_heal' 'verify-podcast'" in runner
    assert "publish_complete" in runner
    assert runner.index("publish verification start") < runner.index("send_push start")
    assert runner.index("podcast verification start") < runner.index("send_push start")
    assert runner.index("publish verification start") < runner.rindex("news-grasp-runner.ps1 OK")
    assert runner.index("podcast verification start") < runner.rindex("news-grasp-runner.ps1 OK")


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


def test_deadman_task_launcher_uses_pythonw_and_create_no_window() -> None:
    """Deadman の毎時 task は console を出さない launcher 経由に固定する。"""
    launcher = OPS_DIR / "news-grasp-deadman-launcher.pyw"
    installer = OPS_DIR / "install-news-grasp-ops.ps1"

    assert launcher.exists()
    launcher_text = launcher.read_text(encoding="utf-8")
    installer_text = installer.read_text(encoding="utf-8-sig")

    assert "subprocess.CREATE_NO_WINDOW" in launcher_text
    assert "stdout=subprocess.DEVNULL" in launcher_text
    assert "stderr=subprocess.DEVNULL" in launcher_text
    assert "subprocess.run(" in launcher_text
    assert "news-grasp-deadman.ps1" in launcher_text
    assert "news-grasp-deadman-launcher.pyw" in installer_text


def test_runner_watcher_uses_hidden_start_and_terminal_state_polling() -> None:
    """watcher は runner を hidden 起動し、state/log の終端状態で完了判定する。"""
    watcher = WATCHER_PS1.read_text(encoding="utf-8-sig")

    assert "[switch] $StartOnly" in watcher
    assert "[switch] $Status" in watcher
    assert "[int] $StaleMinutes = 15" in watcher
    assert "[int] $TimeoutMinutes = 120" in watcher
    assert "Start-Process -FilePath 'powershell'" in watcher
    assert "-WindowStyle Hidden" in watcher
    assert "@('publish_complete', 'smoke_ok')" in watcher
    assert "@('ok', 'smoke_ok')" not in watcher
    assert "fallback_ok" not in watcher.split("function Test-TerminalState", 1)[1].split("function", 1)[0]
    assert "runner process exited without publish_complete marker" in watcher
    assert "log has not changed for" in watcher
    assert "watch timeout after" in watcher


def test_watcher_kills_only_verified_runner_and_writes_typed_watchdog_state() -> None:
    """watcher は照合済み runner だけを止め、照合不能・state破損では kill しない。"""
    watcher = WATCHER_PS1.read_text(encoding="utf-8-sig")

    assert "function Write-WatchdogState" in watcher
    assert "function Test-RunnerProcessIdentity" in watcher
    assert "Get-CimInstance Win32_Process" in watcher
    assert "command_line_fingerprint" in watcher
    assert "process_creation_time" in watcher
    assert "run_id" in watcher
    assert "watchdog_stale_timeout" in watcher
    assert "watchdog_wall_timeout" in watcher
    assert "watchdog_stale_unconfirmed" in watcher
    assert "watchdog_state_corrupt" in watcher
    assert "Stop-Process -Id ([int]$State.pid) -Force" in watcher
    assert watcher.index("Test-RunnerProcessIdentity") < watcher.index("Stop-Process -Id ([int]$State.pid) -Force")
    assert "heartbeat_at" in watcher
    assert "stale_seconds" in watcher


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


def test_runner_quarantines_and_refills_bad_urls_before_typed_failure() -> None:
    """URL gate 失敗は隔離だけで終わらず、カテゴリ補充と再検証へ進む。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    block = runner.split("URL liveness gate start", 1)[1].split("record schema gate start", 1)[0]

    assert "--quarantine-articles" in block
    assert "--apply" in block
    assert "URL liveness quarantine start" in block
    assert "tools.refill_category_after_quarantine" in block
    assert "URL liveness gate recheck after quarantine" in block
    assert "Invoke-AutonomousCompletionPolicy" in block
    assert "-GateId 'url-liveness'" in block
    assert "Stop-ContentGateWithoutFallback -GateId 'url-liveness'" not in block
    assert "search_audit_updated" in (ROOT / "tools" / "audit_all_article_urls.py").read_text(encoding="utf-8")


def test_runner_refill_categories_follow_canonical_config() -> None:
    """URL liveness refill はカテゴリ直書きではなく refill tool の正本一覧を使う。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    block = runner.split("URL liveness refill start", 1)[1].split("URL liveness refill OK", 1)[0]

    assert "--list-categories" in block
    assert "ConvertFrom-Json" in block
    assert "$refillCategories = @('fx','ai','it','mobility','manufacturing','economy','game')" not in runner
    assert "URL liveness refill category list failed" in block
    assert "URL liveness refill category list parse failed" in block


def test_content_gates_use_quality_hold_fallback_without_normal_notification() -> None:
    """内容系 gate の未収束は手動復旧に落とさず、品質保留 fallback で自走完了する。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    assert "function Invoke-AutonomousCompletionPolicy" in runner
    assert "quality_hold" in runner

    content_gate_markers = [
        ("summary reflection gate start", "daily quality gate start"),
        ("daily quality gate start", "Stage4: Codex DeepDive"),
        ("generation quality gate start", "URL liveness gate start"),
        ("URL liveness gate start", "record schema gate start"),
        ("record schema gate start", "digest/articles reconcile gate start"),
        ("digest/articles reconcile gate start", "ja-callout gate start"),
        ("ja-callout gate start", "pytest gate start"),
        ("pytest gate start", "Daily TTS audio"),
        ("generate_pages.py start", "deepdive required gate start"),
        ("deepdive required gate start", "public HTML gate start"),
        ("public HTML gate start", "availability gate start"),
    ]
    for start, end in content_gate_markers:
        block = runner.split(start, 1)[1].split(end, 1)[0]
        assert (
            "Invoke-AutonomousGate" in block
            or "blocked_refill_unresolved" in block
            or "blocked_repair_budget_exhausted" in block
            or "Invoke-AutonomousCompletionPolicy" in block
            or "TTS is required for normal publish" in block
        )
        assert "send_push" not in block
        assert "Should-SendNormalBatchNotification" not in block
    assert "function Stop-ContentGateWithoutFallback" not in runner
    assert "Set-RunnerState -Status 'content_repair_failed'" not in runner


def test_runner_non_external_failures_do_not_require_manual_recoveronly() -> None:
    """非外部障害は `RECOVER:` や手動 RecoverOnly 案内で終わらせない。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    manual_markers = [
        "RECOVER:",
        "publish manually",
        "manual resolve required",
        "run: powershell -NoProfile -ExecutionPolicy Bypass -File $PSCommandPath -RecoverOnly",
    ]
    non_external_ranges = [
        ("function Stop-ContentGateWithoutFallback", "function Test-DailyArtifactsExist"),
        ("wrapper invoke START (agent=codex, role=newsroom_editor", "summary reflection gate start"),
        ("generation artifact normalize start", "generation quality gate start"),
        ("Daily TTS audio (fatal", "2.9 digest/data commit"),
        ("generate_pages.py start", "deepdive required gate start"),
    ]
    for start, end in non_external_ranges:
        if start not in runner:
            continue
        block = runner.split(start, 1)[1].split(end, 1)[0]
        for marker in manual_markers:
            assert marker not in block, (
                f"non-external failures must not require manual RecoverOnly: {marker}"
            )


def test_runner_autonomous_completion_policy_separates_internal_and_external_failures() -> None:
    """内部品質保留 fallback と外部 failure を policy 関数で分離する。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    _assert_runner_powershell_parses()
    policy = runner.split("function Invoke-AutonomousCompletionPolicy", 1)[1].split(
        "function Test-DailyArtifactsExist", 1
    )[0]
    assert "Invoke-FallbackPublish" in policy
    assert "quality_hold" in policy
    assert "blocked_external_readiness" in policy
    assert "publish_failed" in policy
    assert "distribution_failed" in policy
    assert "Exit-Runner -Status 'ok'" not in policy

    internal = _mock_autonomous_policy_invocation("content")
    assert internal["fallback_reason"] == "quality_hold:unit-gate"
    assert internal["exit_status"] in (None, "")

    external = _mock_autonomous_policy_invocation("external")
    assert external["fallback_reason"] in (None, "")
    assert external["exit_status"] == "blocked_external_readiness"
    assert external["exit_code"] == 42
    assert external["external_kind"] == "unit-gate"
    assert external["external_system"] == "external"
    assert external["external_status"] == "rc=42"
    assert external["external_detail"] == "unit-test"


def test_external_readiness_state_carries_typed_evidence() -> None:
    """blocked_external_readiness は machine-readable external evidence を持つ。"""
    payload = _mock_external_readiness_block()

    assert payload["status"] == "blocked_external_readiness"
    assert payload["exit_code"] == 71
    assert payload["external_readiness"]["kind"] == "git_push_auth"
    assert payload["external_readiness"]["system"] == "github"
    assert payload["external_readiness"]["status"] == "rc=1"
    assert payload["external_readiness"]["stderr"] == "fatal auth"
    assert payload["external_readiness"]["detail"] == "origin HEAD:main"


def test_blocked_external_readiness_uses_boundary_only() -> None:
    """外部停止は直接 Set-RunnerState せず Stop-ExternalReadiness 境界へ集約する。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    direct = [
        line.strip()
        for line in runner.splitlines()
        if "Set-RunnerState -Status 'blocked_external_readiness'" in line
    ]

    assert not direct, (
        "blocked_external_readiness must use external readiness boundary: "
        + "; ".join(direct)
    )


def test_publish_external_readiness_returns_structured_evidence() -> None:
    """publish readiness は bool だけでなく kind/system/status/stderr を返す。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    body = runner.split("function Test-PublishExternalReadiness", 1)[1].split(
        "function Should-SendNormalBatchNotification", 1
    )[0]

    assert "New-ExternalReadinessResult" in body
    assert "-Kind 'github_remote'" in body
    assert "-Kind 'git_push_auth'" in body
    assert "-System 'github'" in body
    assert ".ok" in runner.split("publish external readiness gate start", 1)[1].split("publish external readiness gate OK", 1)[0]
    assert "-ExternalKind" in runner
    assert "-ExternalSystem" in runner


def test_generation_readiness_uses_external_boundary_with_missing_paths() -> None:
    """generation readiness も missing paths を typed evidence として外部境界に渡す。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    block = runner.split("generation external readiness gate start", 1)[1].split(
        "generation external readiness gate OK", 1
    )[0]

    assert "Stop-ExternalReadiness" in block
    assert "-Kind 'generation_input_missing'" in block
    assert "-System 'local_artifact_inventory'" in block
    assert "-ExternalDetail" in block
    assert "Set-RunnerState -Status 'blocked_external_readiness'" not in block


def test_runner_distribution_failures_use_autonomous_policy() -> None:
    """distribution_failed は各呼出箇所で直書きせず policy 境界へ集約する。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    direct_distribution_writes = [
        line.strip()
        for line in runner.splitlines()
        if "Set-RunnerState -Status 'distribution_failed'" in line
    ]

    assert not direct_distribution_writes, (
        "distribution failures must use autonomous completion policy: "
        + "; ".join(direct_distribution_writes)
    )


def test_runner_preflight_passes_issue_date_to_newsroom_preflight() -> None:
    """date-sensitive inventory drift は実行対象日で preflight する。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    block = runner.split("PreflightOnly mode: skipping codex / git pull / push / generate_pages", 1)[1].split(
        "publish external readiness gate start", 1
    )[0]

    assert "tools.newsroom_preflight" in block
    assert "'--date'" in block
    assert "$DateStamp" in block


def test_recoveronly_writes_input_manifest_before_reuse(tmp_path: Path) -> None:
    """RecoverOnly は再利用する入力を machine-readable manifest に残す。"""
    manifest = _mock_recoveronly_manifest(tmp_path)

    assert manifest["date"] == "2026-06-23"
    assert manifest["mode"] == "RecoverOnly"
    assert "digest/Summary/2026-06-23.md" in manifest["required_artifacts"]
    assert "data/articles.jsonl" in manifest["missing_artifacts"]
    assert manifest["repo_head"]
    assert manifest["state_file"].endswith("state.json")
    assert manifest["created_at"]


def test_runner_requires_deepdive_after_pages_generation() -> None:
    """通常公開前に当日 DeepDive md/html の存在を gate する。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    assert "deepdive required gate start" in runner
    assert "--require-deepdive" in runner
    assert "--docs-root" in runner
    assert runner.index("generate_pages.py done") < runner.index("deepdive required gate start")
    assert runner.index("deepdive required gate start") < runner.index("public HTML gate start")


def test_runner_tts_is_required_before_pages_generation() -> None:
    """TTS 生成・公開は通常公開必須として扱い、失敗時は publish 本線へ進めない。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    assert "Daily TTS audio (fatal" in runner
    assert "tools.tts.build_script" in runner
    assert "tools.tts.synthesize_daily" in runner
    assert "tools.tts.publish_audio" in runner
    assert runner.index("pytest gate OK") < runner.index("Daily TTS audio (fatal")
    assert runner.index("Daily TTS audio (fatal") < runner.index("2.9 digest/data commit")
    block = runner.split("Daily TTS audio (fatal", 1)[1].split("2.9 digest/data commit", 1)[0]
    assert "TTS is required for normal publish" in block
    assert "Invoke-AutonomousCompletionPolicy" in block
    assert "-FailureKind 'local-tool'" in block
    assert "-GateId 'daily-tts'" in block
    assert "non-fatal" not in block
    assert "Stop-ContentGateWithoutFallback" not in block


def test_runner_tts_does_not_send_normal_notification() -> None:
    """TTS 失敗・成功だけで通常通知を送らず、通知は通常 publish verified 後に限定する。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    tts_block = runner.split("Daily TTS audio (fatal", 1)[1].split("2.9 digest/data commit", 1)[0]
    send_push_index = runner.index("send_push start")
    assert "send_push" not in tts_block
    assert "Should-SendNormalBatchNotification" in runner
    assert runner.index("publish verification start") < send_push_index


def test_watcher_does_not_treat_fallback_as_normal_terminal_success() -> None:
    """fallback_ok は公開済み旧号保護であり、通常バッチ完走として watcher を閉じない。"""
    watcher = WATCHER_PS1.read_text(encoding="utf-8-sig")

    assert "fallback_ok" not in watcher.split("function Test-TerminalState", 1)[1].split("function", 1)[0]
    assert "@('publish_complete', 'smoke_ok')" in watcher
    assert "@('ok', 'smoke_ok')" not in watcher


def test_runner_publish_verification_includes_public_audio_sentinel() -> None:
    """push 完了判定は publish-status / audio / podcast 反映まで含む。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")

    assert "public audio sentinel" in runner
    assert "public podcast sentinel" in runner
    assert "tools.daily_self_heal" in runner
    assert "verify-publish" in runner
    assert "verify-podcast" in runner
    assert runner.index("publish verification start") < runner.index("publish verification OK")


def test_runner_verifies_publish_complete_manifest_before_success() -> None:
    """publish_complete 前に unified manifest verifier を通す。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")

    assert "verify-publish-complete" in runner
    assert runner.index("deepdive podcast verification OK") < runner.index("verify-publish-complete")
    assert runner.index("verify-publish-complete") < runner.index("send_push start")
    assert runner.index("verify-publish-complete") < runner.rindex("news-grasp-runner.ps1 OK")
    block = runner.split("publish-complete manifest verification start", 1)[1].split("send_push start", 1)[0]
    before_block = runner.split("deepdive podcast verification OK", 1)[1].split("publish-complete manifest verification start", 1)[0]
    distribution_body = runner.split("function Write-DistributionManifest", 1)[1].split("function Test-DailyArtifactsExist", 1)[0]
    assert "data\\distribution" in distribution_body
    assert "$distributionSummary = Write-DistributionManifest" in before_block
    assert "$DateStamp.json" in distribution_body
    assert "build\\publish-complete\\$DateStamp.json" in block
    assert "Invoke-AutonomousCompletionPolicy" in block
    assert "-FailureKind 'publish'" in block
    assert "-GateId 'publish-complete'" in block


def test_runner_publish_complete_state_carries_manifest() -> None:
    """terminal state は publish manifest path と commit を持つ。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    state_body = runner.split("function Set-RunnerState", 1)[1].split("function Update-RunnerProgress", 1)[0]
    write_log_body = runner.split("function Write-Log", 1)[1].split("function Invoke-Logged", 1)[0]

    assert "PublishManifestPath" in state_body
    assert "PublishCommit" in state_body
    assert "publish_manifest_path" in state_body
    assert "publish_commit" in state_body
    assert "$script:PublishCompleteManifestPath" in write_log_body
    assert "$script:PublishCompleteCommit" in write_log_body


def test_runner_writes_distribution_manifest_with_commit_anchor(tmp_path: Path) -> None:
    """publish_complete 前の distribution manifest は local HEAD の commit anchor を持つ。"""
    manifest = _mock_distribution_manifest(tmp_path)
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    publish_tail = runner.split("deepdive podcast verification OK", 1)[1].split(
        "publish-complete manifest verification start",
        1,
    )[0]

    assert manifest["date"] == "2026-06-23"
    assert manifest["pre_publish_commit"] == manifest["_expected_head"]
    assert manifest["publish_commit"] == ""
    assert manifest["primary_podcast_state"].endswith("build\\youtube-podcast\\uploads.json")
    assert "Write-DistributionManifest" in runner
    assert "$distributionSummary = Write-DistributionManifest" in publish_tail
    assert "pre_publish_commit" in runner.split("function Write-DistributionManifest", 1)[1].split(
        "function Test-DailyArtifactsExist",
        1,
    )[0]


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
