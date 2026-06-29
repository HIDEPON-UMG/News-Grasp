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
POWERSHELL = os.environ.get("NEWS_GRASP_POWERSHELL", "powershell")
OPS_DIR = ROOT / "scripts" / "ops"
RUNNER_PS1 = Path(os.environ.get("NEWS_GRASP_RUNNER", str(OPS_DIR / "news-grasp-runner.ps1")))
WATCHER_PS1 = Path(os.environ.get("NEWS_GRASP_WATCHER", str(OPS_DIR / "watch-news-grasp-runner.ps1")))


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


def _mock_autonomous_policy_invocation(
    failure_kind: str, *, no_publish: bool = False
) -> dict[str, str]:
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
$NoPublish = ($env:NEWS_GRASP_NO_PUBLISH -eq '1')
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
    env["NEWS_GRASP_NO_PUBLISH"] = "1" if no_publish else "0"
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


def _mock_direct_fallback_publish_disabled() -> dict[str, str]:
    script = r"""
function Write-Log {
  param([string]$Text)
  $script:Logs += @($Text)
}
function Exit-Runner {
  param(
    [string]$Status,
    [string]$Message,
    [int]$ExitCode
  )
  $script:ExitStatus = $Status
  $script:ExitMessage = $Message
  $script:ExitCode = $ExitCode
}
$script:Logs = @()
$runner = Get-Content -LiteralPath $env:NEWS_GRASP_RUNNER_PATH -Raw -Encoding UTF8
$NoPublish = $false
$start = $runner.IndexOf('function Invoke-FallbackPublish')
if ($start -lt 0) { Write-Error 'Invoke-FallbackPublish missing'; exit 2 }
$end = $runner.IndexOf('function Invoke-AutonomousCompletionPolicy', $start)
if ($end -lt 0) { Write-Error 'fallback end marker missing'; exit 2 }
Invoke-Expression $runner.Substring($start, $end - $start)
Invoke-FallbackPublish -Reason 'unit-direct-fallback'
[pscustomobject]@{
  exit_status = $script:ExitStatus
  exit_message = $script:ExitMessage
  exit_code = $script:ExitCode
  logs = $script:Logs
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
    subprocess.run(
        ["git", "init"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("test\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "test"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    expected_head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).strip()
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
    payload["_manifest_path"] = str(tmp_path / "data" / "distribution" / "2026-06-23.json")
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


def test_runner_has_bounded_repair_without_normal_fallback_publish() -> None:
    """通常日次の gate 失敗は bounded repair / typed Red で扱い、fallback publish へ逃げない。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    normal_policy = runner.split("function Invoke-AutonomousCompletionPolicy", 1)[1].split("function Write-RecoverOnlyInputManifest", 1)[0]

    assert "Invoke-TargetedRepair" in runner
    assert "tools.gate_attempts" in runner
    assert "Invoke-FallbackPublish" not in normal_policy
    assert "published_fallback_with_notice" not in normal_policy


def test_setup_defines_daily_fix_completion_as_full_activation_path() -> None:
    """修正完了は fallback 保護ではなく、上流契約を満たした Activation Path で判定する。"""
    setup = SETUP_DOC.read_text(encoding="utf-8")

    assert "通常公開完了条件" in setup
    assert "fallback_ok / published_fallback_with_notice は通常公開完了条件ではない" in setup
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


def test_targeted_repair_prompt_patches_existing_artifacts_until_same_gate_passes() -> None:
    """repair は既存 artifact を捨てず、validator failure だけを最小差分で直す。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")

    assert "欠落成果物を再生成" not in runner
    assert "まず既存 artifact を確認" in runner
    assert "既存 artifact を破棄して新規生成しない" in runner
    assert "再利用不能の証拠" in runner
    assert "validation failure" in runner
    assert "最小差分" in runner
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


def test_runner_derives_reporter_categories_from_publish_inventory() -> None:
    """runner は 7 カテゴリ固定ではなく、号日の必須カテゴリだけを fan-out する。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")

    assert "$Categories = @('fx','ai','it','mobility','manufacturing','economy','game')" not in runner
    assert "Get-PublishInventoryArtifacts -Kind 'categories'" in runner
    assert "$Categories = Get-PublishInventoryArtifacts -Kind 'categories'" in runner
    assert "Stage0 harvest summary categories=$($Categories.Count)" in runner
    assert "reporter_artifacts = @($ReporterArtifacts | ForEach-Object { $_.records_file })" in runner
    assert "scheduled_categories = @($Categories)" in runner
    assert "Summary frontmatter categories/tags/sections は scheduled_categories のみ" in runner


def test_runner_contract_mentions_non_target_categories_are_not_fanned_out() -> None:
    """水曜 Game / 土日 Manufacturing・Economy を sub-agent に流さない契約を runner に残す。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")

    assert "scheduled_category_ids(issue)" in runner
    assert "非対象カテゴリを reporter fan-out しない" in runner
    assert "Game は火木土日のみ" in runner
    assert "Manufacturing / Economy は月火水木金のみ" in runner


def test_codex_auth_preflight_runs_before_llm_repair() -> None:
    """LLM repair 前に Codex 認証切れを content failure と分離して止める。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    repair_body = runner.split("function Invoke-TargetedRepair", 1)[1].split("function Snapshot-RepairWorkspace", 1)[0]

    assert "function Test-CodexAuthReadiness" in runner
    assert "codex auth readiness gate start" in repair_body
    assert "blocked_codex_auth" in repair_body
    assert "Invoke-CodexWrapper" in repair_body
    assert repair_body.index("Test-CodexAuthReadiness") < repair_body.index("Invoke-CodexWrapper")


def test_llm_repair_uses_repair_model_policy_not_style_editor() -> None:
    """LLM repair は文体 editor の mini default を流用せず repair role を使う。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    repair_body = runner.split("function Invoke-TargetedRepair", 1)[1].split("function Snapshot-RepairWorkspace", 1)[0]

    assert "Select-RepairModel" in repair_body
    assert "Get-ModelPolicyValue -Role 'editor' -Key 'default'" not in repair_body


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


def test_python_gate_skips_repair_after_final_attempt_failure() -> None:
    """検証されない最終 attempt 後 repair は token と時間の無駄なので禁止する。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    gate_body = runner.split("function Invoke-PythonGateWithRepair", 1)[1].split("function Invoke-AutonomousGate", 1)[0]

    assert "$maxGateAttempts = 5" in gate_body
    assert "if ($attempt -ge $maxGateAttempts)" in gate_body
    assert "final attempt failed; skipping repair" in gate_body
    assert gate_body.index("final attempt failed; skipping repair") < gate_body.index("tools.auto_repair_orchestrator")


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


def test_generation_quality_repair_prompt_guides_audio_script_length_convergence() -> None:
    """音声台本の字数不足 repair は、境界ぎりぎりで再失敗しない目標字数を示す。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    repair_body = runner.split("function Invoke-TargetedRepair", 1)[1].split("function Snapshot-RepairWorkspace", 1)[0]

    assert "audio_script_quality_invalid" in repair_body
    assert "effective_char_count" in repair_body
    assert "2600〜2800" in repair_body


def test_generation_quality_audio_length_uses_deterministic_repair_before_codex() -> None:
    """音声台本の字数不足は runner 個別分岐ではなく registry 経由で決定論的補修する。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    repair_body = runner.split("function Invoke-TargetedRepair", 1)[1].split("function Snapshot-RepairWorkspace", 1)[0]

    assert "Invoke-DeterministicRegistryRepair" in repair_body
    assert "audio-script-length-patch" in runner
    assert "Invoke-DeterministicGenerationRepair" not in runner
    assert repair_body.index("Invoke-DeterministicRegistryRepair") < repair_body.index("codex auth readiness gate start")


def test_runner_has_single_registry_repair_path_without_legacy_deterministic_duplicate() -> None:
    """deterministic repair の正本は registry に一本化し、runner 内の重複経路を残さない。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    repair_body = runner.split("function Invoke-TargetedRepair", 1)[1].split("function Invoke-DeterministicRegistryRepair", 1)[0]

    assert "function Invoke-DeterministicRegistryRepair" in runner
    assert "function Invoke-DeterministicGenerationRepair" not in runner
    assert "tools.repair_audio_script_length" not in runner
    assert repair_body.count("Invoke-DeterministicRegistryRepair") == 1
    assert "Invoke-CodexWrapper" in repair_body
    assert repair_body.index("Invoke-DeterministicRegistryRepair") < repair_body.index("Invoke-CodexWrapper")


def test_runner_invokes_repair_registry_before_llm_worker() -> None:
    """既知内部欠陥は LLM worker 起動前に deterministic repair registry で処理する。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    repair_body = runner.split("function Invoke-TargetedRepair", 1)[1].split("function Snapshot-RepairWorkspace", 1)[0]

    assert "function Invoke-DeterministicRegistryRepair" in runner
    assert "tools.repair_registry" in runner
    assert "blocked_repair_handler_unimplemented" in runner
    assert "Get-RepairDecisionArtifacts" in runner
    assert "Invoke-DeterministicRegistryRepair -GateId $GateId -CapturePath $CapturePath -Artifacts $Artifacts -ClassifyPath $ClassifyPath" in repair_body
    assert "foreach ($artifact in $repairArtifacts)" in runner
    assert repair_body.index("Invoke-DeterministicRegistryRepair") < repair_body.index("Test-RepairWorkerPreflight")
    assert repair_body.index("Invoke-DeterministicRegistryRepair") < repair_body.index("Invoke-CodexWrapper")


def test_runner_blocks_llm_worker_unless_matrix_allows_missing_artifact_generation() -> None:
    """LLM repair worker は coverage matrix が missing artifact 生成を許可した時だけ起動できる。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    repair_body = runner.split("function Invoke-TargetedRepair", 1)[1].split("function New-RepairTransactionId", 1)[0]

    assert "Test-RepairWorkerPreflight -GateId $GateId -Artifacts $Artifacts -RepairTransactionId $RepairTransactionId -RepairDecision $decision" in repair_body
    assert "llm_generate_missing_artifact" in repair_body
    assert "blocked_existing_artifact_llm_recreate" in runner
    assert repair_body.index("llm_generate_missing_artifact") < repair_body.index("codex auth readiness gate start")


def test_repair_patch_existing_policy_is_enforced_after_targeted_repair() -> None:
    """repair は prompt だけでなく、before/after artifact 証跡で patch-existing を強制する。"""
    _assert_runner_powershell_parses()
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    gate_body = runner.split("function Invoke-PythonGateWithRepair", 1)[1].split("function Invoke-AutonomousGate", 1)[0]

    assert "Snapshot-RepairArtifacts -TransactionId $repairTransactionId -Phase 'before'" in gate_body
    assert "Invoke-TargetedRepair -GateId $GateId -Category $Category -CapturePath $capturePath -Artifacts $Artifacts -RepairTransactionId $repairTransactionId -ClassifyPath $classifyPath" in gate_body
    assert "Snapshot-RepairArtifacts -TransactionId $repairTransactionId -Phase 'after'" in gate_body
    assert "Test-RepairPatchExistingPolicy -TransactionId $repairTransactionId -Artifacts $Artifacts" in gate_body
    assert gate_body.index("Test-RepairPatchExistingPolicy") < gate_body.index("Test-RepairArtifactScope")


def test_repair_preflight_blocks_llm_worker_before_existing_artifact_recreate() -> None:
    """既存 artifact がある repair は LLM worker 起動前に止め、下流diff検出へ丸投げしない。"""
    _assert_runner_powershell_parses()
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    repair_body = runner.split("function Invoke-TargetedRepair", 1)[1].split("function Invoke-DeterministicRegistryRepair", 1)[0]

    assert "Test-RepairWorkerPreflight -GateId $GateId -Artifacts $Artifacts -RepairTransactionId $RepairTransactionId" in repair_body
    assert "pre-repair policy denied LLM repair worker" in runner
    assert "blocked_pre_repair_recreate" in runner
    assert repair_body.index("Test-RepairWorkerPreflight") < repair_body.index("codex auth readiness gate start")
    assert repair_body.index("Test-RepairWorkerPreflight") < repair_body.index("Invoke-CodexWrapper")


def test_repair_downstream_guards_are_last_resort_after_upstream_preflight() -> None:
    """下流のdiff/scope検査は最後の砦であり、pre-repair防止より前面に出さない。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    gate_body = runner.split("function Invoke-PythonGateWithRepair", 1)[1].split("function Invoke-AutonomousGate", 1)[0]

    assert "Test-RepairWorkerPreflight" in runner
    assert "Test-RepairPatchExistingPolicy" in runner
    assert "Test-RepairArtifactScope" in runner
    assert "llm_worker_only_when_all_artifacts_missing" in runner
    assert "最後の砦" in runner
    assert gate_body.index("Invoke-TargetedRepair") < gate_body.index("Test-RepairPatchExistingPolicy")
    assert gate_body.index("Test-RepairPatchExistingPolicy") < gate_body.index("Test-RepairArtifactScope")


def test_repair_patch_existing_policy_requires_reuse_blocked_reason_for_rewrite() -> None:
    """既存 artifact の大幅再作成は reuse-blocked.json の typed reason なしでは失敗する。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    helper_body = runner.split("function Test-RepairReuseBlockedReason", 1)[1].split("function Snapshot-RepairWorkspace", 1)[0]
    policy_body = runner.split("function Test-RepairPatchExistingPolicy", 1)[1].split("function Snapshot-RepairWorkspace", 1)[0]
    prompt_body = runner.split("function Invoke-TargetedRepair", 1)[1].split("function Invoke-DeterministicRegistryRepair", 1)[0]

    assert "reuse-blocked.json" in policy_body
    assert "preserved_line_ratio" in policy_body
    assert "without reuse-blocked.json" in policy_body
    assert "missing_artifact" in helper_body
    assert "structure_corrupt" in helper_body
    assert "date_mismatch" in helper_body
    assert "category_mismatch" in helper_body
    assert "provenance_invalid" in helper_body
    assert "reuse-blocked.json に artifact_path と typed reason" in prompt_body


def test_targeted_repair_rejects_changes_outside_artifact_scope() -> None:
    """repair worker が対象 artifact 以外を触ったら同じ gate の再試行へ進ませない。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    gate_body = runner.split("function Invoke-AutonomousGate", 1)[1].split("function Preserve-UnverifiedGeneratedArtifacts", 1)[0]

    assert "Test-RepairArtifactScope" in runner
    assert "Snapshot-RepairWorkspace" in runner
    assert "repair worker changed files outside artifact scope" in runner
    assert "Invoke-PythonGateWithRepair" in gate_body
    assert "if (-not (Test-RepairArtifactScope" in runner


def test_autonomous_gate_classifies_actual_gate_capture() -> None:
    """classify は空 output ではなく、失敗した gate の capture file を読む。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    gate_body = runner.split("function Invoke-PythonGateWithRepair", 1)[1].split("function Invoke-AutonomousGate", 1)[0]

    assert "'tools.auto_repair_orchestrator' 'classify' '--gate-id' $GateId '--output' ''" not in runner
    assert "$classifyPath = Join-Path" in gate_body
    assert "$gateCapturePathForClassify = $capturePath" in gate_body
    assert "Invoke-LoggedCapture -CapturePath $classifyPath" in gate_body
    assert "'tools.auto_repair_orchestrator' 'classify' '--gate-id' $GateId '--output-file' $gateCapturePathForClassify" in gate_body
    assert "'tools.auto_repair_orchestrator' 'classify' '--gate-id' $GateId '--output-file' $capturePath" not in gate_body
    assert "auto repair classify failed" in gate_body
    assert "return $gateRc" in gate_body


def test_runner_blocks_publish_on_batch_slo_violation() -> None:
    """370万token / 2時間超の自走失敗を publish 前の SLO gate で止める。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")

    assert "batch SLO gate start" in runner
    assert "'tools.validate_batch_slo'" in runner
    assert "--max-total-tokens" in runner
    assert "'3000000'" in runner
    assert "--max-window-sec" in runner
    assert "'3600'" in runner
    assert "--since" in runner
    assert "$script:RunnerProcessCreationTime" in runner
    assert "blocked_slo_violation" in runner
    assert runner.index("batch SLO gate start") < runner.index("Daily TTS audio")


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


def test_url_liveness_gate_bypasses_llm_repair_for_quarantine_refill() -> None:
    """URL liveness は targeted LLM repair ではなく既存 quarantine/refill に進める。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    gate_function = runner.split("function Invoke-PythonGateWithRepair", 1)[1].split("function Invoke-AutonomousGate", 1)[0]
    url_block = runner.split("URL liveness gate start", 1)[1].split("record schema gate start", 1)[0]

    assert "[switch] $NoRepair" in gate_function
    assert "repair disabled for this gate; returning rc" in gate_function
    assert "-NoRepair" in url_block
    assert url_block.index("-NoRepair") < url_block.index("URL liveness quarantine start")


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


def test_runner_fallback_publish_is_disabled_before_public_actions() -> None:
    """通常 runner の direct fallback publish は公開操作前に forbidden_fallback で止まる。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    fallback_body = runner.split("function Invoke-FallbackPublish", 1)[1].split("# ===== sentinel", 1)[0]

    assert "function Preserve-UnverifiedGeneratedArtifacts" in runner
    assert "function Resolve-LastGoodDocsRef" in runner
    assert "fallback publish is disabled in the daily runner path" in fallback_body
    assert "Exit-Runner -Status 'forbidden_fallback'" in fallback_body
    assert "Preserve-UnverifiedGeneratedArtifacts" not in fallback_body
    assert "Resolve-LastGoodDocsRef" not in fallback_body
    assert "checkout $lastGoodDocsRef -- 'docs/'" not in fallback_body
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


def test_disabled_runner_fallback_publish_never_sends_web_push() -> None:
    """disabled fallback publish は送信・push・docs restore へ到達しない。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    fallback_body = runner.split("function Invoke-FallbackPublish", 1)[1].split("# ===== sentinel", 1)[0]

    assert "fallback notification skipped: not a normal batch" not in fallback_body
    assert "tools\\send_push.py" not in fallback_body
    assert "fallback send_push" not in fallback_body
    assert "git push origin main" not in fallback_body


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


def test_runner_stage0_uses_auth_doctor_before_podcast_work() -> None:
    """YouTube OAuth/権限不備は高コストな podcast upload 前に typed Yellow/Red へ分離する。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")

    assert "tools.youtube_podcast.auth_doctor" in runner
    assert "youtube auth doctor failed" in runner
    assert "blocked_external_readiness" in runner
    assert runner.index("tools.youtube_podcast.auth_doctor") < runner.index("tools.youtube_podcast.upload_episode")


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


def test_runner_has_no_publish_e2e_mode_distinct_from_no_push() -> None:
    """push直前E2Eは NoPush と別に、commit / publish / upload の副作用も止める。

    なぜ重要か: NoPush は git push と send_push を止めるだけでは足りない。
    git commit、GitHub Releases audio upload、YouTube private prepare が残る状態で
    「本番影響なしのE2E」と呼ぶと、前回と同じ goal 矮小化になる。
    """
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")

    assert "[switch] $NoPublish" in runner
    assert "if ($NoPublish) { $NoPush = $true }" in runner
    assert "NoPublish mode: skipping digest/data git add + commit" in runner
    assert "NoPublish mode: skipping docs git add + commit" in runner
    assert "NoPublish mode: skipping distribution manifest git add + commit" in runner
    assert "tools.tts.publish_audio', $DateStamp, '--dry-run'" in runner
    assert "tools.tts.deepdive_audio', $DateStamp, '--dry-run'" in runner
    assert "tools.youtube_podcast.upload_episode', $DateStamp, '--prepare', '--dry-run'" in runner
    assert "tools.youtube_podcast.upload_episode', $DateStamp, '--kind', 'deepdive', '--prepare', '--dry-run'" in runner
    assert "news-grasp-runner.ps1 PUBLISH DRY RUN OK" in runner
    assert "publish_dry_run_ok" in runner


def test_runner_exposes_resume_from_deepdive_without_reharvest() -> None:
    """失敗後の E2E は Stage0/Reporter を再実行せず、停止点から再開できる。

    なぜ重要か: push直前E2Eのやり直しで重い収集・記者要約を毎回回すと、
    1時間SLOと token 効率の証明そのものを壊す。
    """
    _assert_runner_powershell_parses()
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")

    assert "[ValidateSet('', 'deepdive', 'post-daily-quality', 'post-deepdive')]" in runner
    assert "[string] $ResumeFromStage = ''" in runner
    assert "$ResumeFromPostDailyQuality = $ResumeFromStage -in @('deepdive', 'post-daily-quality')" in runner
    assert "$ResumeAfterDeepDive = $ResumeFromStage -in @('post-deepdive')" in runner
    assert "ResumeFromStage=${ResumeFromStage}: reusing Stage0/Reporter/Editor/daily-quality artifacts; starting at DeepDive" in runner
    assert "ResumeFromStage mode: skipping net reachability wait and git sync" in runner
    assert "ResumeFromStage mode: skipping Stage0/Stage1/Stage1.5/Stage2/Stage3; rechecking summary/daily gates" in runner


def test_runner_can_resume_after_deepdive_without_regenerating_deepdive() -> None:
    """DeepDive artifact が既に使えるなら、DeepDive も再生成せず TTS 以降へ進む。"""
    _assert_runner_powershell_parses()
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    deepdive_block = runner.split("# ===== Stage4: Codex DeepDive", 1)[1].split(
        "# ===== 2.4 generation quality gate", 1
    )[0]

    assert "ResumeFromStage mode: skipping deepdive codex; using existing DeepDive artifact" in deepdive_block
    assert "$ResumeAfterDeepDive" in deepdive_block
    assert "deepdive wrapper invoke START" in deepdive_block


def test_existing_artifact_guard_allows_explicit_resume() -> None:
    """既存 artifact がある日は full rerun を拒否し、明示 resume だけ許可する。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    guard = runner.split("existing daily artifacts detected", 1)[0].split("Assert-RunnerBinaryInSync", 1)[1]

    assert "(-not $ResumeFromPostDailyQuality)" in guard
    assert "Use -ForceFullRerun only after explicit user approval; otherwise resume from existing artifacts." in runner


def test_no_publish_e2e_does_not_mark_publish_complete() -> None:
    """NoPublish E2E は成功しても publish_complete ではなく publish_dry_run_ok にする。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")

    final_block = runner.split("# ===== 6. Web Push", 1)[1]
    final_block = final_block.split("Write-CodexUsageWindowSnapshot -Phase 'end'", 1)[1]
    no_publish_branch = final_block.split("} elseif ($NoPush)", 1)[0]

    assert "news-grasp-runner.ps1 PUBLISH DRY RUN OK" in no_publish_branch
    assert "news-grasp-runner.ps1 OK" not in no_publish_branch
    assert "publish_complete" not in no_publish_branch


def test_no_publish_e2e_never_fallback_publishes_on_quality_hold() -> None:
    """NoPublish E2E は内部 gate 失敗時も fallback publish へ逃げず、内部 block として止まる。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    policy = runner.split("function Invoke-AutonomousCompletionPolicy", 1)[1].split(
        "function Test-DailyArtifactsExist", 1
    )[0]

    assert "Invoke-FallbackPublish" not in policy
    assert "quality_hold" not in policy

    no_publish = _mock_autonomous_policy_invocation("content", no_publish=True)
    assert no_publish["fallback_reason"] in (None, "")
    assert no_publish["exit_status"] == "blocked_internal_quality_gate"
    assert no_publish["exit_code"] == 42


def test_no_publish_autonomous_policy_never_fallbacks_for_any_failure_kind() -> None:
    """NoPublish E2E は全 failure kind で fallback publish に逃げない。"""
    expected_status_by_kind = {
        "content": "blocked_internal_quality_gate",
        "artifact": "blocked_internal_quality_gate",
        "local-tool": "blocked_internal_quality_gate",
        "external": "blocked_external_readiness",
        "publish": "publish_failed",
        "distribution": "distribution_failed",
    }

    for failure_kind, expected_status in expected_status_by_kind.items():
        result = _mock_autonomous_policy_invocation(failure_kind, no_publish=True)

        assert result["fallback_reason"] in (None, ""), failure_kind
        assert result["exit_status"] == expected_status, failure_kind
        assert result["exit_code"] == 42, failure_kind


def test_no_publish_direct_fallback_publish_is_blocked_before_public_actions() -> None:
    """direct fallback publish 呼び出しは NoPublish に限らず公開操作前に止める。"""
    result = _mock_direct_fallback_publish_disabled()

    assert result["exit_status"] == "forbidden_fallback"
    assert result["exit_code"] == 73
    assert "fallback publish is disabled in the daily runner path" in result["exit_message"]
    assert all("fallback publish start" not in line for line in result["logs"])
    assert all("fallback push origin main done" not in line for line in result["logs"])


def test_autonomous_completion_policy_call_sites_are_covered_by_no_publish_contract() -> None:
    """runner 全工程の repair/failure gate を NoPublish 非fallback契約の対象に固定する。"""
    commands = _powershell_command_extents(RUNNER_PS1, "Invoke-AutonomousCompletionPolicy")
    observed: set[tuple[str, str]] = set()
    for command in commands:
        kind = re.search(r"-FailureKind\s+'([^']+)'", command)
        gate = re.search(r"-GateId\s+'([^']+)'", command)
        assert kind, command
        assert gate, command
        observed.add((kind.group(1), gate.group(1)))

    expected = {
        ("distribution", "distribution-manifest"),
        ("content", "newsroom-editor-timeout"),
        ("content", "summary-reflection"),
        ("content", "daily-quality"),
        ("artifact", "generation-normalize"),
        ("content", "generation-quality"),
        ("content", "url-liveness"),
        ("content", "record-schema"),
        ("content", "digest-articles-reconcile"),
        ("content", "ja-callout"),
        ("local-tool", "pytest-static"),
        ("local-tool", "daily-tts"),
        ("local-tool", "deepdive-tts"),
        ("content", "current-deepdive-url"),
        ("local-tool", "generate-pages"),
        ("content", "deepdive-required"),
        ("content", "public-html"),
        ("distribution", "youtube-podcast-auth"),
        ("distribution", "youtube-podcast-prepare"),
        ("distribution", "youtube-podcast-finalize"),
        ("distribution", "deepdive-youtube-podcast-finalize"),
        ("distribution", "podcast-verify"),
        ("distribution", "deepdive-podcast-verify"),
        ("distribution", "podcast-playlist-audit"),
        ("publish", "publish-complete"),
    }
    covered_kinds = {"content", "artifact", "local-tool", "external", "publish", "distribution"}

    assert observed == expected
    assert {kind for kind, _gate in observed} <= covered_kinds


def test_runner_idle_timeout_is_parameterized() -> None:
    """digest / DeepDive の idle timeout は runner パラメータから調整できる。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")

    assert "[int] $IdleTimeoutSec = 900" in runner
    assert "-TimeoutSec $TimeoutSec -IdleTimeoutSec $IdleTimeoutSec" in runner
    assert "-TimeoutSec $DeepDiveTimeoutSec -IdleTimeoutSec $IdleTimeoutSec" in runner


def test_pytest_static_gate_skips_historical_url_liveness_checks() -> None:
    """本番当日 publish 前 pytest は historical URL 生存確認を巻き込まない。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    block = runner.split("pytest gate start", 1)[1].split("if ($pytestGateRc -ne 0)", 1)[0]

    assert "$previousSkipUrlCheck = $env:NEWS_GRASP_SKIP_URL_CHECK" in block
    assert "$env:NEWS_GRASP_SKIP_URL_CHECK = '1'" in block
    assert "Remove-Item Env:\\NEWS_GRASP_SKIP_URL_CHECK" in block


def test_generate_pages_skips_historical_deepdive_url_liveness_after_current_gate() -> None:
    """SSG は過去 DeepDive URL の経年 404 で本日 publish を止めない。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    current_gate = "current DeepDive URL gate start"
    generate_start = "generate_pages.py start"
    deepdive_required = "deepdive required gate start"

    assert runner.index(current_gate) < runner.index(generate_start)
    block = runner.split(generate_start, 1)[1].split(deepdive_required, 1)[0]
    assert "$env:NEWS_GRASP_SKIP_URL_CHECK = '1'" in block
    assert "tools\\generate_pages.py" in block
    assert "tools.validate_deepdive_urls" not in block


def test_runner_writes_machine_readable_state() -> None:
    """runner は foreground 待機に頼らず、終端状態を JSON state に書く。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")

    assert "news-grasp-runner-state.json" in runner
    assert "function Set-RunnerState" in runner
    assert "-Status 'running' -Message 'runner started'" in runner
    assert "-Status 'publish_complete' -Message $Text -ExitCode 0" in runner
    assert "-Status 'ok' -Message $Text -ExitCode 0" not in runner
    assert "-Status 'fallback_ok' -Message $Text -ExitCode 0" not in runner
    assert "-Status 'smoke_ok' -Message $Text -ExitCode 0" in runner
    assert "-Status 'error' -Message $Text -ExitCode 1" in runner


def test_runner_preserves_registry_typed_status_without_unimplemented_rounding() -> None:
    """registry の scope / not-applicable / output violation を handler_unimplemented に丸めない。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    registry_body = runner.split("function Invoke-DeterministicRegistryRepair", 1)[1].split("function New-RepairTransactionId", 1)[0]

    assert "repair_context_scope_mismatch" in runner
    assert "repair_handler_output_scope_violation" in runner
    assert "blocked_deterministic_repair_not_applicable" in runner
    assert "$registryStatus" in registry_body
    assert "Exit-Runner -Status $registryStatus" in registry_body


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


def test_reporter_codex_quota_uses_external_readiness_boundary() -> None:
    """reporter の Codex quota は retry/terminal failure ではなく外部境界で即停止する。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    reporter_body = runner.split("$retryCategories = @($Categories)", 1)[1].split(
        "foreach ($artifactCat in $Categories)",
        1,
    )[0]
    quota_marker = "if ([int]$WaveResult.rc -eq 123)"

    assert quota_marker in runner
    assert "function Test-ReporterCodexQuotaFailure" in runner
    quota_function = runner.split("function Test-ReporterCodexQuotaFailure", 1)[1].split(
        "function Clear-ReporterCategoryArtifacts",
        1,
    )[0]
    quota_block = reporter_body.split("if (Test-ReporterCodexQuotaFailure -WaveResult $waveResult)", 1)[1].split(
        "if ([int]$waveResult.rc -ne 0)",
        1,
    )[0]
    assert "You've hit your usage limit" in quota_function
    assert "purchase more credits" in quota_function
    assert "Stop-ExternalReadiness" in quota_block
    assert "-Kind 'codex_quota'" in quota_block
    assert "-System 'openai_codex'" in quota_block
    assert "blocked_reporter_repeated_failure" not in quota_block
    assert "terminalFailures" not in quota_block


def test_runner_is_repo_managed_and_requires_approved_live_sync() -> None:
    """bin 実行体 drift は勝手に上書きせず、backup + 明示承認 + rollback を要求する。"""
    repo_runner = OPS_DIR / "news-grasp-runner.ps1"
    repo_watcher = OPS_DIR / "watch-news-grasp-runner.ps1"
    runner = repo_runner.read_text(encoding="utf-8-sig")

    assert repo_runner.exists()
    assert repo_watcher.exists()
    forbidden_local_user_path = "C:" + "\\Users\\" + "hide" + "k"
    assert forbidden_local_user_path not in runner
    assert "function Assert-RunnerBinaryInSync" in runner
    assert "function Invoke-RunnerBinarySyncApprovalBlock" in runner
    assert "blocked_runner_sync_approval_required" in runner
    assert "backup + explicit approval + rollback plan" in runner
    assert "Copy-Item -LiteralPath $RepoManagedRunner -Destination $PSCommandPath -Force" not in runner
    assert "NEWS_GRASP_RUNNER_SYNC_REEXEC" not in runner
    assert "runner binary drift repaired; relaunching synced runner" not in runner
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


def test_ops_installer_creates_backup_manifest_and_rollback_hint_before_live_overwrite() -> None:
    """live runner 同期は上書き前に backup / manifest / rollback 証跡を残す。"""
    installer = OPS_DIR / "install-news-grasp-ops.ps1"
    text = installer.read_text(encoding="utf-8-sig")

    assert "backup + explicit approval + rollback" in text
    assert "$BackupDir" in text
    assert "$ManifestPath" in text
    assert "rollback_commands" in text
    assert "Copy-Item -LiteralPath $destination -Destination" in text
    assert "Get-FileHash" in text
    assert "install-manifest.json" in text
    assert text.index("$BackupDir") < text.index("$files = @(")
    assert text.index("$BackupDir") < text.index("Copy-Item -LiteralPath $source -Destination $destination -Force")


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
    assert "'--issue-date', $DateStamp" in block
    assert "'--issue-date' $DateStamp" in block
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


def test_content_gates_block_internal_failures_without_normal_notification() -> None:
    """内容系 gate の未収束は fallback 成功扱いにせず typed internal block で止める。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    assert "function Invoke-AutonomousCompletionPolicy" in runner
    assert "blocked_internal_quality_gate" in runner

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
    """内部品質 gate failure と外部 failure を policy 関数で分離する。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    _assert_runner_powershell_parses()
    policy = runner.split("function Invoke-AutonomousCompletionPolicy", 1)[1].split(
        "function Test-DailyArtifactsExist", 1
    )[0]
    assert "Invoke-FallbackPublish" not in policy
    assert "quality_hold" not in policy
    assert "blocked_internal_quality_gate" in policy
    assert "blocked_external_readiness" in policy
    assert "publish_failed" in policy
    assert "distribution_failed" in policy
    assert "Exit-Runner -Status 'ok'" not in policy

    internal = _mock_autonomous_policy_invocation("content")
    assert internal["fallback_reason"] in (None, "")
    assert internal["exit_status"] == "blocked_internal_quality_gate"
    assert internal["exit_code"] == 42

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

    terminal_body = watcher.split("function Test-TerminalState", 1)[1].split("function", 1)[0]

    assert "fallback_ok" not in terminal_body
    assert "quality_hold" not in terminal_body
    assert "blocked_repair_handler_unimplemented" not in terminal_body
    assert "blocked_slo_progress" not in terminal_body
    assert "blocked_external_readiness" not in terminal_body
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
    assert "podcast playlist audit" in runner
    assert "--audit-playlists" in runner
    assert runner.index("publish verification start") < runner.index("publish verification OK")


def test_runner_publish_verify_uses_wait_window_before_finalize() -> None:
    """push 直後の Deploy Pages / public sentinel 収束待ちは pre-finalize gate に渡す。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")

    block = runner.split("publish verification start", 1)[1].split("youtube podcast finalize start", 1)[0]

    assert "'verify-publish'" in block
    assert "'--wait-sec' $PublishVerifyWaitSec" in block
    assert "'--poll-sec' $PublishVerifyPollSec" in block
    assert "'--require-podcast'" not in block


def test_runner_verifies_publish_complete_manifest_before_success() -> None:
    """publish_complete 前に unified manifest verifier を通す。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")

    assert "verify-publish-complete" in runner
    assert runner.index("deepdive podcast verification OK") < runner.index("verify-publish-complete")
    assert runner.index("podcast playlist audit OK") < runner.index("verify-publish-complete")
    assert runner.index("verify-publish-complete") < runner.index("send_push start")
    assert runner.index("verify-publish-complete") < runner.rindex("news-grasp-runner.ps1 OK")
    block = runner.split("publish-complete manifest verification start", 1)[1].split("send_push start", 1)[0]
    before_block = runner.split("$distributionSummary = Write-DistributionManifest", 1)[1].split(
        "# ===== 5. digest + docs",
        1,
    )[0]
    distribution_body = runner.split("function Write-DistributionManifest", 1)[1].split("function Test-DailyArtifactsExist", 1)[0]
    assert "data\\distribution" in distribution_body
    assert runner.count("$distributionSummary = Write-DistributionManifest") == 1
    assert 'add "data/distribution/$DateStamp.json"' in before_block
    assert 'commit -m "distribution: record publish state for $DateStamp"' in before_block
    assert runner.index("$distributionSummary = Write-DistributionManifest") < runner.index("push origin main start")
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
    publish_tail = runner.split("$distributionSummary = Write-DistributionManifest", 1)[1].split(
        "# ===== 5. digest + docs",
        1,
    )[0]

    assert manifest["date"] == "2026-06-23"
    assert manifest["pre_publish_commit"] == manifest["_expected_head"]
    assert manifest["publish_commit"] == ""
    assert not Path(manifest["_manifest_path"]).read_bytes().startswith(b"\xef\xbb\xbf")
    assert manifest["primary_podcast_state"] == "build/youtube-podcast/uploads.json"
    assert manifest["deepdive_podcast_state"] == "build/youtube-podcast-deepdive/uploads.json"
    assert manifest["latest_audio_state"] == "build/tts/latest_audio.json"
    assert manifest["deepdive_audio_state"] == "build/tts/deepdive/latest_audio.json"
    assert "Write-DistributionManifest" in runner
    assert runner.count("$distributionSummary = Write-DistributionManifest") == 1
    assert 'commit -m "distribution: record publish state for $DateStamp"' in publish_tail
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
