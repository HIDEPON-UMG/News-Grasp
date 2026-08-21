#!/usr/bin/env python3
"""日次 runner の責務分離と fallback publish 契約。"""
from __future__ import annotations

import os
import json
import hashlib
import inspect
import re
import runpy
import shutil
import subprocess
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
RUNNER_PROMPT = ROOT / "prompts" / "runner-prompt.md"
ROUTINE_SYSTEM = ROOT / "prompts" / "routine-system.md"
DEEPDIVE_PROMPT = ROOT / "prompts" / "deepdive-runner-prompt.md"
SETUP_DOC = ROOT / "SETUP.md"
POWERSHELL = os.environ.get("NEWS_GRASP_POWERSHELL", "powershell")
OPS_DIR = ROOT / "scripts" / "ops"
RUNNER_PS1 = Path(os.environ.get("NEWS_GRASP_RUNNER", str(OPS_DIR / "news-grasp-runner.ps1")))
SCHEDULED_EQUIVALENT_PS1 = OPS_DIR / "invoke-scheduled-equivalent-nopublish.ps1"
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
    assert "python / py / uv / repo-local runtime の直書きは禁止" in runner
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


def test_codex_doctor_mcp_failure_does_not_block_auth_ready_repair() -> None:
    """codex doctor の MCP 設定警告を ChatGPT auth 失効と誤分類しない。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    auth_body = runner.split("function Test-CodexAuthReadiness", 1)[1].split(
        "function Test-YouTubePodcastAuthReadiness", 1
    )[0]

    assert "codex doctor non-auth failure ignored" in auth_body
    assert "mcp" in auth_body.lower()
    assert "auth is configured" in auth_body
    assert "stored ChatGPT tokens" in auth_body
    assert "codex auth readiness failed: codex doctor rc=$doctorRc" not in auth_body


def test_git_add_retries_transient_index_lock_before_publish_commits() -> None:
    """公開前 git add は transient index.lock で即 fatal にせず bounded retry する。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")

    assert "function Invoke-GitAddWithIndexLockRetry" in runner
    assert "git add $Label retry after rc=128" in runner
    assert "stale empty index.lock removed before retry" in runner
    assert "$lock.Length -eq 0" in runner
    assert "$lockAge.TotalSeconds -ge 60" in runner
    assert "Invoke-GitAddWithIndexLockRetry -Label 'digest/data' -Pathspecs @('digest/', 'data/')" in runner
    assert "Invoke-GitAddWithIndexLockRetry -Label 'docs' -Pathspecs @('docs/')" in runner
    assert "& $GitExe -C $RepoDir add 'digest/' 'data/'" not in runner
    assert "& $GitExe -C $RepoDir add 'docs/'" not in runner


def test_llm_repair_uses_repair_model_policy_not_style_editor() -> None:
    """LLM repair は文体 editor の mini default を流用せず repair role を使う。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    repair_body = runner.split("function Invoke-TargetedRepair", 1)[1].split("function Snapshot-RepairWorkspace", 1)[0]

    assert "Select-RepairModel" in repair_body
    assert "Get-ModelPolicyValue -Role 'editor' -Key 'default'" not in repair_body


def test_runner_never_passes_long_prompt_or_html_via_native_argument() -> None:
    """長大 prompt/report/html 本文は file/stdin 境界に閉じ、native argv へ載せない。"""
    runner_path = OPS_DIR / "news-grasp-runner.ps1"
    wrapper_path = OPS_DIR / "run_codex_with_timeout.ps1"
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
        + [
            re.sub(r"\s+", " ", stmt).strip()
            for stmt in _powershell_command_extents(wrapper_path, "CreateSuspendedAssignedProcess")
        ]
    )
    assert argument_boundaries
    for statement in argument_boundaries:
        for var_name in body_vars:
            assert not _contains_powershell_variable(statement, var_name), (
                f"long prompt must be file/stdin, not native argv: {statement}"
            )

    native_launch = " ".join(
        _normalized_powershell_statements(wrapper, "CreateSuspendedAssignedProcess")
    )
    assert "$stdinFile" in native_launch
    assert "$stdoutFile" in native_launch
    assert "$stderrFile" in native_launch
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


def test_gate_convergence_is_deadline_and_repair_ledger_bound_not_fixed_attempt_bound() -> None:
    """異なる typed issue の収束を固定5回で打ち切らず、deadline と repair ledger で止める。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    gate_body = runner.split("function Invoke-PythonGateWithRepair", 1)[1].split("function Invoke-AutonomousGate", 1)[0]

    assert "$maxGateAttempts = 5" not in gate_body
    assert "for ($attempt = 1; ; $attempt++)" in gate_body
    assert "final attempt failed; skipping repair" not in gate_body
    assert "tools.auto_repair_orchestrator" in gate_body
    assert "tools.gate_attempts" in runner


def test_typed_repair_decision_is_written_before_retry_ledger_denial_returns() -> None:
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    repair_body = runner.split("function Invoke-TargetedRepair", 1)[1].split(
        "function Invoke-PythonGateWithRepair", 1
    )[0]

    decision_marker = "$decision = Read-RepairDecision"
    retry_denied_marker = "gate retry ledger denied repair worker"
    assert decision_marker in repair_body
    assert retry_denied_marker in repair_body
    assert repair_body.index(decision_marker) < repair_body.index(retry_denied_marker)
    assert "Set-TypedRepairTerminalState" in repair_body


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


def test_generation_quality_audio_length_uses_typed_llm_rewrite_not_legacy_handler() -> None:
    """音声台本の品質不足を到達不能な旧 deterministic handler へ戻さない。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    repair_body = runner.split("function Invoke-TargetedRepair", 1)[1].split("function Snapshot-RepairWorkspace", 1)[0]

    assert "audio_script_quality_invalid" in repair_body
    assert "audio-script-length-patch" not in runner
    assert "Invoke-DeterministicGenerationRepair" not in runner
    assert "Invoke-CodexWrapper" in repair_body


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


def test_runner_scopes_each_compound_repair_to_primary_issue_artifacts() -> None:
    """ledger全体のselected_artifactsを単一issueのrepair scopeへ混ぜない。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    helper = runner.split("function Get-RepairDecisionArtifacts", 1)[1].split(
        "function Invoke-DeterministicRegistryRepair", 1
    )[0]

    selected_artifacts = helper.index("'selected_artifacts'")
    artifact_paths = helper.index("'artifact_paths'")
    primary_return = helper.index("return $selected.ToArray()")
    assert selected_artifacts < artifact_paths < primary_return


def test_parallel_hotfix_runner_optional_fields_three_admission_schemas_and_artifact_priority_dedupe() -> None:
    """optional decision fields、3 admission schema、selected-first fallbackを束縛する。"""
    fixture = json.loads(
        (ROOT / "tests" / "fixtures" / "news_grasp_cleanroom_parallel_hotfix_cases.json").read_text(
            encoding="utf-8"
        )
    )
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    helper = runner.split("function Get-RepairDecisionArtifacts", 1)[1].split(
        "function Invoke-DeterministicRegistryRepair", 1
    )[0]
    assert all(schema in runner for schema in fixture["admissionSchemas"])
    assert "HIGH_COST_SCHEDULED_UNKNOWN_V1" not in runner
    assert helper.index("'selected_artifacts'") < helper.index("'artifact_paths'")
    assert "FallbackArtifacts" in helper

    script = r"""
$runner = Get-Content -LiteralPath $env:NEWS_GRASP_RUNNER_PATH -Raw -Encoding UTF8
$start = $runner.IndexOf('function Get-RepairDecisionArtifacts')
$end = $runner.IndexOf('function Invoke-DeterministicRegistryRepair', $start)
if ($start -lt 0 -or $end -lt 0) { exit 2 }
Invoke-Expression $runner.Substring($start, $end - $start)
$decision = [pscustomobject]@{ selected_artifacts=@('one','two','one'); artifact_paths=@('two','three') }
$selected = @(Get-RepairDecisionArtifacts -RepairDecision $decision -FallbackArtifacts @('fallback','one'))
$fallback = @(Get-RepairDecisionArtifacts -RepairDecision ([pscustomobject]@{}) -FallbackArtifacts @('fallback','one'))
[pscustomobject]@{ selected=$selected; fallback=$fallback } | ConvertTo-Json -Compress
"""
    env = os.environ.copy()
    env["NEWS_GRASP_RUNNER_PATH"] = str(RUNNER_PS1)
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
    value = json.loads(result.stdout)
    assert value["selected"] == ["one", "two", "three"]
    assert value["fallback"] == []
    terminal_body = runner.split("function Set-TypedRepairTerminalState", 1)[1].split(
        "function Invoke-TargetedRepair", 1
    )[0]
    for field in ("external_kind", "external_system", "reason"):
        assert f"$Decision.PSObject.Properties.Name -contains '{field}'" in terminal_body


def test_runner_does_not_report_registry_noop_as_repair_success() -> None:
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")

    assert "$registryStatus -eq 'noop'" in runner
    assert "registry noop; same-gate reverify required" in runner
    assert "deterministic registry repair produced no mutation" in runner


def test_runner_blocks_llm_worker_unless_matrix_allows_missing_artifact_generation() -> None:
    """LLM repair worker は coverage matrix が missing artifact 生成を許可した時だけ起動できる。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    repair_body = runner.split("function Invoke-TargetedRepair", 1)[1].split("function New-RepairTransactionId", 1)[0]

    assert "$llmRepairArtifacts = @(Get-RepairDecisionArtifacts -RepairDecision $decision -FallbackArtifacts $Artifacts)" in repair_body
    assert "Test-RepairWorkerPreflight -GateId $GateId -Artifacts $llmRepairArtifacts -RepairTransactionId $RepairTransactionId -RepairDecision $decision" in repair_body
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

    assert "$llmRepairArtifacts = @(Get-RepairDecisionArtifacts -RepairDecision $decision -FallbackArtifacts $Artifacts)" in repair_body
    assert "Test-RepairWorkerPreflight -GateId $GateId -Artifacts $llmRepairArtifacts -RepairTransactionId $RepairTransactionId" in repair_body
    assert "pre-repair policy denied LLM repair worker" in runner
    assert "blocked_pre_repair_recreate" in runner
    assert repair_body.index("Test-RepairWorkerPreflight") < repair_body.index("codex auth readiness gate start")
    assert repair_body.index("Test-RepairWorkerPreflight") < repair_body.index("Invoke-CodexWrapper")


def test_repair_preflight_allows_matrix_owned_existing_artifact_rewrite() -> None:
    """明示 handler 付き rewrite class は既存 artifact だけを bounded repair できる。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    target = runner.split("function Invoke-TargetedRepair", 1)[1].split("function Invoke-DeterministicRegistryRepair", 1)[0]
    preflight = runner.split("function Test-RepairWorkerPreflight", 1)[1].split("function Test-RepairPatchExistingPolicy", 1)[0]

    assert "llm_rewrite_existing_artifact" in target
    assert "$rewriteExistingAllowed" in preflight
    assert "$missing.Count -eq 0" in preflight
    assert "matrix_owned_existing_artifact_rewrite" in preflight


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


def test_send_push_requires_publish_verification_for_normal_or_recovery() -> None:
    """通知は通常/recoveryとも公開反映確認後だけ実行し、NoPushでは送らない。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    notify_gate = runner.split("function Should-SendNormalBatchNotification", 1)[1].split("# ===== sentinel", 1)[0]
    send_block = runner.split("# ===== 6. Web Push", 1)[1].split("Write-CodexUsageWindowSnapshot -Phase 'end'", 1)[0]

    assert "$NormalPublishVerified = $false" in runner
    assert "$NormalPublishVerified = $true" in runner
    assert "$NormalPublishVerified" in notify_gate
    assert "-not $NoPush" in notify_gate
    assert "-not $RecoverOnly" not in notify_gate
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
    assert "NoPush mode: skipping git push origin HEAD:main" in runner
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

    assert "[ValidateSet('', 'post-reporter', 'editor', 'deepdive', 'post-daily-quality', 'post-deepdive', 'generation-quality-repair')]" in runner
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


def test_no_publish_e2e_forbids_force_full_rerun_after_artifacts_exist() -> None:
    """修正後の NoPublish/E2E は full rerun へ戻らず、既存 artifact から resume させる。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    guard = runner.split("Assert-RunnerBinaryInSync", 1)[1].split("Write-CodexUsageWindowSnapshot -Phase 'start'", 1)[0]

    assert "$IsE2EOrDryRun = $NoPublish -or $NoPush -or $StopBeforeDeepDive" in guard
    assert "E2E full rerun forbidden after existing artifacts" in guard
    assert "Use -ResumeFromStage deepdive, post-daily-quality, post-deepdive, or generation-quality-repair" in guard
    assert guard.index("E2E full rerun forbidden after existing artifacts") < guard.index("existing daily artifacts detected")
    assert "-not $ForceFullRerun" not in guard.split("E2E full rerun forbidden after existing artifacts", 1)[0]


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
            ("content", "newsroom-editor-contract"),
            ("content", "newsroom-editor-preview"),
            ("content", "newsroom-editor-transaction-recovery"),
        ("content", "newsroom-editor-workspace"),
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
        ("content", "deepdive-shared-quality"),
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
        ("publish", "publish-verify"),
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
    current_gate = "current DeepDive provenance capture start"
    generate_start = "generate_pages.py start"
    deepdive_required = "deepdive required gate start"

    assert runner.index(current_gate) < runner.index(generate_start)
    block = runner.split(generate_start, 1)[1].split(deepdive_required, 1)[0]
    assert "$env:NEWS_GRASP_SKIP_URL_CHECK = '1'" in block
    assert "tools\\generate_pages.py" in block
    assert "tools.validate_deepdive_urls" not in block


def test_current_deepdive_url_gate_cannot_inherit_skip_environment() -> None:
    """本番復帰時のURL gateは親プロセスのskip設定を継承してはならない。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    start = runner.index('Write-Log "current DeepDive provenance capture start')
    end = runner.index("Write-Log 'current DeepDive provenance capture OK'", start)
    block = runner[start:end]

    assert "tools.deepdive_quality" in block
    assert "'capture'" in block
    assert "NEWS_GRASP_SKIP_URL_CHECK" not in block


def test_runner_requires_deepdive_dialogue_value_gate_before_synthesis() -> None:
    """全復帰経路で価値台帳を通さず音声・公開へ進めない。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    build = "deepdive dialogue script build"
    value_gate = "deepdive shared quality gate"
    synth = "deepdive dialogue synthesize"
    assert runner.index(build) < runner.index(value_gate) < runner.index(synth)
    assert "tools.deepdive_quality" in runner[runner.index(value_gate):runner.index(synth)]
    assert "audit-issue" in runner[runner.index(value_gate):runner.index(synth)]


def test_runner_rejects_bad_deepdive_urls_before_dialogue_synthesis() -> None:
    """不良URLへ音声合成・公開リソースを投入しない。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    url_gate = 'Write-Log "current DeepDive provenance capture start'
    build = "deepdive dialogue script build"
    synth = "deepdive dialogue synthesize"
    publish = "deepdive dialogue publish"
    assert runner.index(url_gate) < runner.index(build) < runner.index(synth) < runner.index(publish)


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
    assert "$previousStatus -eq 'error'" in state_body
    assert "Get-RunnerStateProperty" in runner
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
    assert "job_states=" in reporter_body
    assert "REPORTER_JOB_START_FAILED" in reporter_body
    assert "if ($Stage2EditorSmokeOnly) { 1 } else { 5 }" in reporter_body
    assert "finally" in reporter_body
    assert "Get-Job -Id $ownedJob.Id" in reporter_body
    assert "reporter job CLEANUP" in reporter_body
    assert "Stop-Job -Job $liveOwnedJob" in reporter_body
    assert "Receive-Job -Id $liveOwnedJob.Id -ErrorAction SilentlyContinue" in reporter_body
    assert "Remove-Job -Job $liveOwnedJob" in reporter_body
    assert "Update-RunnerProgress -Phase 'reporter'" in reporter_body
    assert "active_jobs" in reporter_body
    assert "Append-ReporterWrapperLog" in reporter_body
    assert "wrapper_log_offsets" in reporter_body
    assert "Stop-Job -Job $job -Force" in reporter_body


def test_reporter_wave_preserves_binding_resolver_identity_and_job_exceptions() -> None:
    """本番reporter waveはresolver identityを落とさず、Start-Job例外を空rcに潰さない。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    reporter_body = runner.split("function Invoke-ReporterWave", 1)[1].split(
        "# ===== Stage3", 1
    )[0]
    assert "-HighCostBindingResolverPath $HighCostBindingResolverPath" in reporter_body
    assert "                '',\n                $HighCostBindingResolverSha256" not in reporter_body
    assert "wrapper_invocation_exception" in reporter_body
    assert "if ($received.Count -eq 0)" in reporter_body
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


def test_reporter_quota_rc_does_not_override_verified_green_artifact() -> None:
    """記者 artifact 検証が Green なら wrapper の quota rc は公開生成を止めない。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    reporter_body = runner.split("$retryCategories = @($Categories)", 1)[1].split(
        "foreach ($artifactCat in $Categories)",
        1,
    )[0]
    success_marker = "if ($verifyReporterRc -eq 0) {"
    quota_failure_marker = "} elseif (Test-ReporterCodexQuotaFailure -WaveResult $waveResult) {"
    green_warning = "artifact verification Green category=$catName attempt=$attempt"

    assert success_marker in reporter_body
    assert quota_failure_marker in reporter_body
    assert green_warning in reporter_body
    assert reporter_body.index(success_marker) < reporter_body.index(green_warning)
    assert reporter_body.index(green_warning) < reporter_body.index(quota_failure_marker)


def test_post_reporter_resume_reuses_verified_reporter_artifacts_without_refanout() -> None:
    """post-reporter resume は reporter を再消費せず、既存 artifact 検証から editor へ進む。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    reporter_body = runner.split("$retryCategories = @($Categories)", 1)[1].split(
        "foreach ($artifactCat in $Categories)",
        1,
    )[0]

    assert "$ResumeAfterReporter = $ResumeFromStage -in @('post-reporter', 'editor')" in runner
    assert "(-not $ResumeAfterReporter)" in runner
    assert "ResumeFromStage=${ResumeFromStage}: skipping Stage0/Stage1/Stage1.5" in runner
    assert "if ($ResumeAfterReporter) {" in reporter_body
    assert "skipping reporter fan-out; verifying existing reporter artifacts" in reporter_body
    assert "$retryCategories = @()" in reporter_body
    assert "HIGH_COST_SCHEDULED_RECOVERY_CONTINUATION_V1" in runner
    assert "SCHEDULED_RECOVERY_CONTINUATION_SOURCE_ADMISSION_INVALID" in runner


def test_recovery_continuation_admission_covers_deepdive_resume_boundary() -> None:
    """DeepDive resume も broker-issued continuation admission で再開できる。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    admission_block = runner.split("$stageDecisionReceipt = ''", 1)[1].split(
        "if (-not $ScheduledAuthorityEvidencePath)",
        1,
    )[0]
    resume_block = runner.split("scheduled recovery stage start boundary", 1)[1].split(
        "if ($ResumeGenerationQualityRepair)",
        1,
    )[0]

    assert "if ($HighCostAdmissionPath)" in admission_block
    assert "[string]$continuationAdmission.resumeStage -ne $ResumeFromStage" in admission_block
    assert "$script:UsesHighCostContinuationAdmission = $true" in admission_block
    assert "scheduled recovery stage start boundary satisfied by HIGH_COST_SCHEDULED_RECOVERY_CONTINUATION_V1" in resume_block
    assert "start-news-grasp-recovery-stage" in resume_block


def test_recovery_continuation_admission_bypasses_stale_deadline_gate() -> None:
    """Broker-issued continuation must not be blocked by the original 06:40 fixed cutoff."""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    deadline_block = runner.split("function Assert-RecoveryOperationDeadline", 1)[1].split(
        "function Acquire-RecoveryHighCostBudget",
        1,
    )[0]
    receipt_block = runner.split(
        "$script:RecoveryHardDeadline = [DateTimeOffset]::Parse",
        1,
    )[1].split("if ($FinalizeVerifiedPublishManifest)", 1)[0]

    assert "$script:UsesHighCostContinuationAdmission -or" in deadline_block
    assert "($ResumeFromStage -and $HighCostAdmissionPath) -or" in deadline_block
    assert "(-not $script:UsesHighCostContinuationAdmission)" in receipt_block
    assert "(-not ($ResumeFromStage -and $HighCostAdmissionPath))" in receipt_block
    assert receipt_block.count("(-not ($ResumeFromStage -and $HighCostAdmissionPath))") >= 2


def test_scheduled_recovery_resume_never_converts_pytest_failure_to_success() -> None:
    """同日公開復旧でもpytest failureを成功へ書き換えず、品質劣化を拒否する。"""
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    block = runner.split("if ($pytestGateRc -ne 0)", 1)[1].split("Write-Log 'pytest gate OK'", 1)[0]

    assert "$pytestGateRc = 0" not in block
    assert "Invoke-AutonomousCompletionPolicy" in block


def test_runner_is_repo_managed_and_requires_approved_live_sync_outside_normal_daily() -> None:
    """手動・検証系の bin 実行体 drift は backup + 明示承認 + rollback を要求する。"""
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
    assert "daily_self_heal.py" in text
    assert "'-I' '-S' '-B' $DailySelfHealPath" in text
    assert "deadman" in text
    assert "news-grasp-alerts" in text
    assert "Invoke-Audit0640Control" in text
    assert "audit_recovery_control.py" in text
    assert "'-I' '-S' '-B' $AuditControlPath" in text
    assert "ensure-0640" in text
    assert "audit controller remains authoritative" in text
    assert "exit (Invoke-Audit0640Control)" in text
    assert "$terminalJson" in text
    assert "$executorExitCode -notin @(0, 2, 3)" in text
    assert "Invoke-RecoverOnlyIfStaleDeadPid" not in text
    assert "-RecoveryDecisionPath" not in text
    assert "watch-news-grasp-runner.ps1" not in text
    assert "-RecoverOnly" not in text


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
    assert 'parser.add_argument("--repo-dir", type=Path)' in launcher_text
    assert '["-RepoDir", str(repo_dir)]' in launcher_text
    assert '$deadmanArgs = "`"$deadmanLauncherPath`""' in installer_text
    assert 'news-grasp-runtime-root-v1.json' in launcher_text
    assert '{"schemaVersion", "repoDir", "pythonExe", "evidenceRepoDir"}' in launcher_text
    assert '["-PythonExe", str(python_exe)]' in launcher_text
    deadman_text = (OPS_DIR / "news-grasp-deadman.ps1").read_text(encoding="utf-8-sig")
    assert "$OutputEncoding = [System.Text.Encoding]::UTF8" in deadman_text
    assert "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8" in deadman_text


def test_runner_and_bootstrap_tasks_use_pythonw_no_console_launcher() -> None:
    launcher = OPS_DIR / "news-grasp-task-launcher.pyw"
    installer = OPS_DIR / "install-news-grasp-ops.ps1"
    assert launcher.exists()
    launcher_text = launcher.read_text(encoding="utf-8")
    installer_text = installer.read_text(encoding="utf-8-sig")
    assert 'script = bin_dir / "news-grasp-bootstrap.ps1"' in launcher_text
    assert '"news-grasp-runner.ps1" if args.mode == "runner"' not in launcher_text
    assert "subprocess.CREATE_NO_WINDOW" in launcher_text
    assert "stdin=subprocess.DEVNULL" in launcher_text
    assert "news-grasp-task-launcher.pyw" in installer_text
    assert 'parser.add_argument("--repo-dir", type=Path)' in launcher_text
    assert 'extra.extend(["-RepoDir", str(repo_dir)])' in launcher_text
    assert '$runnerArgs = "`"$taskLauncherPath`" runner --scheduled-task-name `"$RunnerTaskName`" --high-cost-binding-path' in installer_text
    assert '$bootstrapArgs = "`"$taskLauncherPath`" bootstrap --scheduled-task-name `"$BootstrapTaskName`" --high-cost-binding-path' in installer_text
    assert '--repo-dir `"$RepoDir`"' not in installer_text
    assert 'news-grasp-runtime-root-v1.json' in launcher_text
    assert '{"schemaVersion", "repoDir", "pythonExe", "evidenceRepoDir"}' in launcher_text
    assert 'extra.extend(["-PythonExe", str(python_exe)])' in launcher_text
    assert "New-ScheduledTaskAction -Execute 'powershell.exe'" not in installer_text
    assert '/TR "powershell.exe ' not in installer_text
    assert "schtasks.exe /Create /TN $BootstrapTaskName" not in installer_text
    assert "Invoke-NewsGraspInstallRollback" in installer_text
    assert "existed_before" in installer_text
    assert "Export-ScheduledTask" in installer_text
    assert "Register-ScheduledTask -TaskName $taskName -Xml $xml -Force" in installer_text
    assert "execute = $pythonw" in installer_text
    assert "[Console]::OutputEncoding" in installer_text
    assert "Register-ScheduledTask -TaskName $RunnerTaskName -Action $runnerAction -Trigger $runnerTrigger -Settings $runnerSettings" in installer_text
    assert "Enable-ScheduledTask -TaskName $RunnerTaskName" in installer_text
    assert "if (-not $runnerRegistered) {" in installer_text
    assert 'throw "failed to converge $RunnerTaskName action:' in installer_text


def test_editor_retry_prefers_current_attempt_fallback_preview() -> None:
    """retry 成功時は stale な前回 preview より当該 attempt の last-message を検証する。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    body = runner.split("function Sync-EditorOutputPreview", 1)[1].split(
        "function Read-RepairDecision", 1
    )[0]

    fallback_branch = "if ($FallbackPath -and (Test-Path -LiteralPath $FallbackPath))"
    preview_missing_branch = "elseif (-not (Test-Path -LiteralPath $sourcePath))"
    assert fallback_branch in body
    assert preview_missing_branch in body
    assert body.index(fallback_branch) < body.index(preview_missing_branch)


def test_editor_materialization_uses_one_production_boundary_and_recovers_before_model() -> None:
    """production runner は旧直書きを廃止し、WAL 回復後に単一 materializer を使う。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    body = runner.split("function Sync-EditorOutputPreview", 1)[1].split(
        "function Read-RepairDecision", 1
    )[0]
    editor_flow = runner.split("$gateAttemptDir =", 1)[0]

    assert "& $PyExe '-I' $canonicalMaterializer" in body
    assert "ConvertFrom-Json" not in body
    assert "[System.IO.File]::WriteAllText" not in body
    assert "Add-JsonlRecordsIfMissing" not in body
    assert "--recover-only" in editor_flow
    assert "EDITOR_OUTPUT_TRANSACTION_RECOVERY_REQUIRED" in editor_flow
    assert "tools.validate_editor_output_preview' $editorOutputPreview '--date'" not in runner
    assert editor_flow.rfind("--recover-only") < editor_flow.rfind("$gateAttemptDir =") or "$gateAttemptDir =" not in editor_flow
    assert "Python312\\python.exe" in runner
    assert "$env:PYTHONSAFEPATH = '1'" in runner
    assert "$env:PYTHONNOUSERSITE = '1'" in runner
    assert "$env:PYTHONPATH = $RepoDir" in runner
    assert "$env:GIT_CONFIG_GLOBAL = 'NUL'" not in runner
    assert "$GitSafeArgs = @(" in runner
    assert "& $GitExe -C" not in runner
    assert "& $GitExe @GitSafeArgs -C" in runner
    assert "function Test-ArtifactExecutableTreeIntegrity" in runner
    assert "verify-artifact-tree" in runner
    assert "ARTIFACT_EXECUTABLE_TREE_INVALID after reporter fan-out" in runner
    assert "& $PyExe '-I' $auditControl" in runner
    wrapper = runner.split("function Invoke-CodexWrapper", 1)[1].split(
        "function ConvertTo-JsonlLine", 1
    )[0]
    assert "if ($wrapperRc -eq 0 -and" not in wrapper
    assert "return 125" not in wrapper.split("if (-not $wrapperOk)", 1)[1].split(
        "Test-ArtifactExecutableTreeIntegrity", 1
    )[0]
    assert "$wrapperRc = 125" in wrapper
    reporter = runner.split(
        "$waveResults = Invoke-ReporterWave -Attempt $attempt -WaveCategories $retryCategories",
        1,
    )[1]
    verify = reporter.index("Test-ArtifactExecutableTreeIntegrity")
    artifact_validator = reporter.index("tools.verify_reporter_output")
    assert verify < artifact_validator


def test_editor_materializer_uses_isolated_canonical_script() -> None:
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    body = runner.split("function Sync-EditorOutputPreview", 1)[1].split(
        "function Read-RepairDecision", 1
    )[0]
    startup = runner.split("$gateAttemptDir =", 1)[0]
    assert "$canonicalMaterializer = Join-Path $OpsRepoRoot 'tools\\materialize_editor_output.py'" in runner
    assert "& $PyExe '-I' $canonicalMaterializer" in body
    assert "& $PyExe '-I' $canonicalMaterializer" in startup
    assert "'-m' 'tools.materialize_editor_output'" not in runner


def test_editor_attempt_snapshot_rejects_recursive_or_outside_repo_targets() -> None:
    """editor rollback は許可済みfileだけを扱い、repo外・directory・改変manifestを拒否する。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    snapshot = runner.split("function New-EditorAttemptSnapshot", 1)[1].split(
        "$MaxAgentAttempts = 3", 1
    )[0]

    assert "function Resolve-EditorArtifactPath" in runner
    assert "[System.IO.Path]::IsPathRooted($RelativePath)" in runner
    assert "[System.IO.Path]::GetFullPath" in runner
    assert "[System.IO.FileAttributes]::ReparsePoint" in runner
    assert "EDITOR_SNAPSHOT_PATH_INVALID" in runner
    assert "EDITOR_SNAPSHOT_DIRECTORY_FORBIDDEN" in snapshot
    assert "snapshot_sha256" in snapshot
    assert "[System.IO.Path]::GetTempPath()" in snapshot
    assert "[Guid]::NewGuid().ToString('N')" in snapshot
    assert "manifest_sha256" in snapshot
    assert "manifest.sha256" not in snapshot
    assert "EDITOR_SNAPSHOT_MANIFEST_TAMPERED" in snapshot
    assert "function Remove-EditorAttemptSnapshot" in snapshot
    assert "Remove-Item -LiteralPath $snapshotDir -Recurse" not in snapshot
    assert "Remove-Item -LiteralPath $destination -Recurse -Force" not in snapshot
    assert "Copy-Item -LiteralPath $source -Destination $snapshotPath -Recurse -Force" not in snapshot


def test_scheduled_tasks_bind_to_stable_pythonw_not_recovery_worktree() -> None:
    """一時worktreeからinstallしてもScheduled Taskは安定したPythonへ束縛する。"""
    installer_text = (OPS_DIR / "install-news-grasp-ops.ps1").read_text(encoding="utf-8-sig")

    assert "[string] $TaskPythonwPath = ''" in installer_text
    assert "function Resolve-NewsGraspTaskPythonw" in installer_text
    assert "$env:NEWS_GRASP_TASK_PYTHONW" in installer_text
    assert "AppData\\Local\\Programs\\Python\\Python312\\pythonw.exe" in installer_text
    assert "$TaskPythonwPath = Resolve-NewsGraspTaskPythonw" in installer_text
    assert "$pythonw = $TaskPythonwPath" in installer_text
    assert "task_pythonw_path = $TaskPythonwPath" in installer_text


def test_nopublish_installed_python_uses_runtime_binding_or_workspace_boundary() -> None:
    """正規runtime bindingのsystem Pythonだけをworkspace外で許可する。"""
    wrapper = (OPS_DIR / "invoke-scheduled-equivalent-nopublish.ps1").read_text(
        encoding="utf-8-sig"
    )
    marker = "-Label 'installed launcher Python'"
    assert marker in wrapper
    call = next(line for line in wrapper.splitlines() if marker in line)
    assert "-Boundary $installedPythonBoundary" in call
    assert "$installedRuntimeBinding.schemaVersion" in wrapper
    assert "$installedRuntimeBinding.pythonExeSha256" in wrapper
    assert "$installedRuntimeBinding.pythonTrustAnchor" in wrapper
    assert "$installedRuntimeBinding.pythonSignerThumbprint" in wrapper
    assert "Get-AuthenticodeSignature" in wrapper


def test_runner_has_typed_verified_publish_finalize_path() -> None:
    """公開済み recovery は全生成を再実行せず、同一 manifest から typed terminal state に収束する。"""
    text = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    assert "FinalizeVerifiedPublishManifest" in text
    assert "failed_then_recovered" in text
    assert "recovery_attempt_status" in text
    assert "FINALIZATION_MANIFEST_NOT_GREEN" in text
    assert "Set-RunnerState -Status 'publish_complete'" in text
    assert "OpsRepoRootOverride" in text
    assert "--ops-repo-root" in text


def test_ops_installer_creates_backup_manifest_and_rollback_hint_before_live_overwrite() -> None:
    """live runner 同期は上書き前に backup / manifest / rollback 証跡を残す。"""
    installer = OPS_DIR / "install-news-grasp-ops.ps1"
    text = installer.read_text(encoding="utf-8-sig")
    launcher_text = (OPS_DIR / "news-grasp-task-launcher.pyw").read_text(encoding="utf-8")
    bootstrap_text = (OPS_DIR / "news-grasp-bootstrap.ps1").read_text(encoding="utf-8-sig")

    assert "backup + explicit approval + rollback" in text
    assert "$BackupDir" in text
    assert "$ManifestPath" in text
    assert "rollback_commands" in text
    assert "Read-NewsGraspVerifiedFile" in text
    assert "Write-NewsGraspAtomicFile" in text
    assert ".Sha256" in text
    assert "install-manifest.json" in text
    assert "news-grasp-bootstrap.ps1" in text
    assert "news-grasp-lineage.ps1" in text
    assert "news-grasp-task-launcher.pyw" in text
    assert "news-grasp-runtime-root-v1.json" in text
    assert "NEWS_GRASP_RUNTIME_ROOT_V1" in text
    assert "generated:runtime-root" in text
    assert "pythonExe = $runtimePythonPath" in text
    assert "evidenceRepoDir = $runtimeEvidenceRepoDir" in text
    assert "[string] $PythonExe = ''" in bootstrap_text
    assert "'-PyExeOverride', $PythonExe" in bootstrap_text
    watcher_text = (OPS_DIR / "watch-news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    for dependency in (
        "run_codex_with_timeout.ps1",
        "news-grasp-lineage.ps1",
        "news-grasp-task-launcher.pyw",
    ):
        assert dependency in bootstrap_text
        assert dependency in watcher_text
    assert "News-Grasp Bootstrap" in text
    assert "register_failed_bootstrap_required" in text
    for argument in ('"-SmokeTest"', '"-PollSeconds"', '"-TimeoutMinutes"'):
        assert argument in launcher_text
    assert '"-StateFile", "ng-smoke-state.json", "-LogDir", "ng-smoke-logs"' in launcher_text
    assert text.index("$BackupDir") < text.index("$files = @(")
    assert text.index("$BackupDir") < text.index("$afterHash = Write-NewsGraspAtomicFile")
    assert "Register-ScheduledTask" in text
    assert "watch-news-grasp-runner.ps1" in text
    assert "-Start" in text
    assert "[string] $RunnerTaskName = 'News-Grasp Production'" in text


def test_ops_installer_preserves_pre_mutation_error_without_rollback_masking(tmp_path: Path) -> None:
    """変更開始前の失敗はrollbackへ入らず、元の診断をそのまま返す。"""
    installer = OPS_DIR / "install-news-grasp-ops.ps1"
    missing_repo = tmp_path / "missing-repo"
    bin_dir = tmp_path / "bin"

    completed = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(installer),
            "-RepoDir",
            str(missing_repo),
            "-BinDir",
            str(bin_dir),
            "-SkipTaskRegistration",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
    )

    diagnostic = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert "NEWS_GRASP_REPO_PATH_NOT_FOUND" in diagnostic
    assert "Invoke-NewsGraspInstallRollback" not in diagnostic
    assert not bin_dir.exists()


def test_ops_installer_initializes_journal_authority_before_mutation_and_trap() -> None:
    """authority作成前の失敗でもrollback journalが未定義変数で壊れない。"""
    text = (OPS_DIR / "install-news-grasp-ops.ps1").read_text(encoding="utf-8-sig")
    initialization = "$missionAuthorityPath = ''"
    prepared = "Write-NewsGraspInstallJournal -Phase 'prepared'"
    mutation_started = "$script:InstallationMutationStarted = $true"
    first_live_write = "$afterHash = Write-NewsGraspAtomicFile"
    recovery = text.split("function Recover-NewsGraspInterruptedInstall", 1)[1].split(
        "function Invoke-NewsGraspInstallRollback", 1
    )[0]

    assert initialization in text, "NGI_RED_INSTALLER_PRE_AUTHORITY_ROLLBACK_UNBOUND"
    assert text.index(initialization) < text.index("trap {")
    assert text.index(initialization) < text.index(mutation_started)
    assert text.index(prepared) < text.index(mutation_started) < text.index(first_live_write)
    missing_journal = recovery.split(
        "if (-not (Test-Path -LiteralPath $journalPath -PathType Leaf)) {", 1
    )[1].split("        try {", 1)[0]
    assert "continue" in missing_journal, "NGI_RED_INSTALLER_PREJOURNAL_ORPHAN_BLOCKS_RECOVERY"
    assert "NEWS_GRASP_INSTALL_JOURNAL_INGEST_MISSING" not in missing_journal


def test_ops_installer_task_specs_are_shape_complete_under_strict_mode() -> None:
    """全task specはrepetition有無にかかわらず同一property shapeを持つ。"""
    text = (OPS_DIR / "install-news-grasp-ops.ps1").read_text(encoding="utf-8-sig")
    expected_block = text.split("$expected = @(", 1)[1].split("    )", 1)[0]
    task_specs = [line for line in expected_block.splitlines() if "[ordered]@{" in line]

    assert len(task_specs) == 3
    for spec in task_specs:
        assert "interval =" in spec, spec
        assert "duration =" in spec, spec


def test_ops_installer_task_rollback_fails_closed_before_rolled_back_receipt() -> None:
    """task復元失敗を非終端errorとして扱い、rolled_backへ誤記録しない。"""
    text = (OPS_DIR / "install-news-grasp-ops.ps1").read_text(encoding="utf-8-sig")
    rollback_blocks = [
        text.split("function Invoke-NewsGraspRollbackJournal", 1)[1].split(
            "function Recover-NewsGraspInterruptedInstall", 1
        )[0],
        text.split("function Invoke-NewsGraspInstallRollback", 1)[1].split(
            "function Write-NewsGraspInstallJournal", 1
        )[0],
    ]

    for block in rollback_blocks:
        for command in (
            "Register-ScheduledTask",
            "Enable-ScheduledTask",
            "Disable-ScheduledTask",
            "Unregister-ScheduledTask",
        ):
            matching = [line for line in block.splitlines() if command in line]
            assert matching
            assert all("-ErrorAction Stop" in line for line in matching), matching
    assert "$Journal | Add-Member -NotePropertyName 'rolled_back_at'" in text
    assert "$Journal.rolled_back_at =" not in text


def test_ops_installer_skips_privileged_task_restore_when_snapshot_is_unchanged() -> None:
    """task XML と enabled state が同一なら、非管理者復旧はtask mutationを呼ばない。"""
    text = (OPS_DIR / "install-news-grasp-ops.ps1").read_text(encoding="utf-8-sig")
    for marker in (
        "function Invoke-NewsGraspRollbackJournal",
        "function Invoke-NewsGraspInstallRollback",
    ):
        block = text.split(marker, 1)[1]
        assert "$taskNeedsRestore = $true" in block
        assert "$taskNeedsRestore = $false" in block
        assert "if (-not $taskNeedsRestore) { continue }" in block


def test_install_guard_limits_reparse_check_to_the_trusted_managed_root() -> None:
    """OneDrive祖先は許容し、repo配下に現れるreparseだけを拒否対象にする。"""
    guard = OPS_DIR / "install-news-grasp-ops-guard.ps1"
    backup_root = ROOT / "build" / "live-runner-backups"
    command = (
        f". '{guard}'; "
        f"Assert-NewsGraspNoReparsePath -Path '{backup_root}' -Boundary '{ROOT}'"
    )

    completed = subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_ops_installer_disables_legacy_task_after_canonical_convergence() -> None:
    """旧taskはrollback可能なsnapshotを保持し、正規3taskの収束後に無効化する。"""
    text = (OPS_DIR / "install-news-grasp-ops.ps1").read_text(encoding="utf-8-sig")

    assert "[string] $LegacyRunnerTaskName = 'News-Grasp Runner'" in text
    assert "@($RunnerTaskName, $BootstrapTaskName, $DeadmanTaskName, $LegacyRunnerTaskName)" in text
    assert "Disable-ScheduledTask -TaskName $LegacyRunnerTaskName -ErrorAction Stop" in text
    assert "legacy task remains enabled" in text
    deadman_registration = text.index(
        "Register-ScheduledTask -TaskName $DeadmanTaskName"
    )
    legacy_disable = text.index(
        "Disable-ScheduledTask -TaskName $LegacyRunnerTaskName -ErrorAction Stop"
    )
    tasks_converged = text.index("Write-NewsGraspInstallJournal -Phase 'tasks_converged'")
    assert deadman_registration < legacy_disable < tasks_converged


def test_interrupted_install_rejects_forged_journal_paths_and_task_names_before_mutation(
    tmp_path: Path,
) -> None:
    """復旧journalは任意file/taskを一件も変更する前に全体を拒否する。"""
    guard = OPS_DIR / "install-news-grasp-ops-guard.ps1"
    backup_root = tmp_path / "backups"
    transaction_dir = backup_root / "20260809-120000"
    transaction_dir.mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("unchanged", encoding="utf-8")
    journal_path = transaction_dir / "install-manifest.json"
    journal = {
        "schemaVersion": "NEWS_GRASP_OPS_INSTALL_JOURNAL_V1",
        "transaction_id": "20260809-120000",
        "phase": "files_installed",
        "updated_at": "2026-08-09T12:00:00+09:00",
        "repo_dir": str(tmp_path / "repo"),
        "bin_dir": str(bin_dir),
        "task_pythonw_path": str(tmp_path / "pythonw.exe"),
        "bin_dir_existed_before": True,
        "backup_dir": str(transaction_dir),
        "files": [
            {
                "file": "news-grasp-runner.ps1",
                "source": "source",
                "destination": str(outside),
                "backup": "",
                "before_sha256": "",
                "source_sha256": "",
                "after_sha256": "",
            }
        ],
        "rollback_commands": ["Invoke-NewsGraspInstallRollback"],
        "mission_authority": {
            "path": "",
            "sha256": "",
            "schema": "AUDIT_MISSION_AUTHORITY_V1",
        },
        "scheduled_tasks": [],
        "task_snapshots": [
            {
                "task_name": "Arbitrary Privileged Task",
                "existed_before": False,
                "enabled_before": False,
                "xml_backup": "",
            }
        ],
    }
    journal_path.write_text(json.dumps(journal), encoding="utf-8")
    command = (
        f". '{guard}'; "
        f"$journal=Get-Content -LiteralPath '{journal_path}' -Raw -Encoding UTF8 | ConvertFrom-Json; "
        "Assert-NewsGraspRecoveryJournal "
        f"-JournalPath '{journal_path}' -Journal $journal "
        f"-ExpectedBackupRoot '{backup_root}' -ExpectedRepoDir '{tmp_path / 'repo'}' "
        f"-ExpectedBinDir '{bin_dir}' "
        "-ExpectedTaskNames @('News-Grasp Production','News-Grasp Bootstrap','News-Grasp Deadman')"
    )

    completed = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert completed.returncode != 0
    assert "NEWS_GRASP_INSTALL_JOURNAL_" in completed.stderr
    assert outside.read_text(encoding="utf-8") == "unchanged"


def test_install_guard_limits_reparse_walk_to_each_trusted_root() -> None:
    """OneDrive等の正規親を拒否せず、trusted root外とroot内reparseだけを拒否する。"""
    guard = (OPS_DIR / "install-news-grasp-ops-guard.ps1").read_text(encoding="utf-8-sig")
    assert "[string] $Boundary" in guard
    assert "NEWS_GRASP_INSTALL_JOURNAL_BOUNDARY_INVALID" in guard
    assert "if (Test-NewsGraspSamePath -Left $cursor -Right $trustedBoundary) { break }" in guard
    assert "Assert-NewsGraspNoReparsePath -Path $JournalPath -Boundary $ExpectedBackupRoot" in guard


def test_watcher_repairs_live_ops_before_runner_start() -> None:
    """6:00 入口の watcher は repo 管理 ops へ自己修復してから runner を起動する。"""
    watcher = WATCHER_PS1.read_text(encoding="utf-8-sig")

    assert "function Repair-LiveOpsFromRepo" in watcher
    assert "news-grasp-bootstrap.ps1" in watcher
    assert "news-grasp-runner.ps1" in watcher
    assert "watch-news-grasp-runner.ps1" in watcher
    assert "news-grasp-deadman.ps1" in watcher
    assert "auto-repair-manifest.json" in watcher
    assert "Copy-Item -LiteralPath $source -Destination $destination -Force" in watcher
    assert "before_sha256" in watcher
    assert "after_sha256" in watcher
    assert "Repair-LiveOpsFromRepo" in watcher.split("$proc = Start-RunnerProcess", 1)[0]


def test_bootstrap_smoke_uses_isolated_state_and_backed_up_self_repair() -> None:
    """05:55 bootstrap smoke は本番 state/log を汚染せず、live overwrite 証跡を残す。"""
    bootstrap = OPS_DIR / "news-grasp-bootstrap.ps1"
    text = bootstrap.read_text(encoding="utf-8-sig")

    assert bootstrap.exists()
    assert "build\\bootstrap-task-smoke\\state.json" in text
    assert "build\\bootstrap-task-smoke\\logs" in text
    assert "build\\live-bootstrap-self-repair" in text
    assert "auto-repair-manifest.json" in text
    assert "Copy-Item -LiteralPath $destination -Destination $backup -Force" in text
    assert "before_sha256" in text
    assert "after_sha256" in text
    assert "if ($SmokeTest)" in text
    smoke_block = text.split("if ($SmokeTest)", 1)[1].split("foreach ($file", 1)[0]
    assert "$StateFile = Join-Path $RepoDir 'build\\bootstrap-task-smoke\\state.json'" in smoke_block
    assert "$LogDir = Join-Path $RepoDir 'build\\bootstrap-task-smoke\\logs'" in smoke_block
    assert "[System.IO.Path]::IsPathRooted($StateFile)" in text
    assert "$StateFile = Join-Path $BinDir $StateFile" in text
    assert "$LogDir = Join-Path $BinDir $LogDir" in text


def test_runner_watcher_uses_hidden_start_and_event_driven_terminal_state() -> None:
    """watcher はrunnerをhidden起動し、event/deadlineで終端判定する。"""
    watcher = WATCHER_PS1.read_text(encoding="utf-8-sig")

    assert "[switch] $StartOnly" in watcher
    assert "[switch] $Status" in watcher
    assert "[int] $StaleMinutes = 15" in watcher
    assert "[int] $TimeoutMinutes = 120" in watcher
    assert "Start-Process -FilePath 'powershell'" not in watcher
    assert "CreateSuspendedJobProcess" in watcher
    assert "CREATE_NO_WINDOW" in watcher
    assert "FindFirstChangeNotification" in watcher
    assert "FindNextChangeNotification" in watcher


def test_start_only_keeps_owned_job_watcher_alive_until_terminal() -> None:
    """StartOnlyがJob handleを閉じる前に親を終了させ、startedだけを残さない。"""
    watcher = WATCHER_PS1.read_text(encoding="utf-8-sig")
    start_only = watcher.split("if ($PSCmdlet.ParameterSetName -eq 'StartOnly')", 1)[1]
    assert "Write-StartedJson -Process $proc" in start_only
    assert "exit 0" not in start_only.split("Watch-Runner -Process $proc", 1)[0]
    assert "Watch-Runner -Process $proc" in start_only
    assert "[System.IO.FileSystemWatcher]" not in watcher
    assert "[System.Threading.WaitHandle]::WaitAny" in watcher
    assert "Start-Sleep -Seconds $PollSeconds" not in watcher
    assert "DateStampOverride = $DateStamp" in watcher
    assert "LogDirOverride = $LogDir" in watcher
    assert "StateFileOverride = $StateFile" in watcher
    assert "@boundRunnerParameters" in watcher
    assert "`$global:LASTEXITCODE = `$null" in watcher
    assert "@('publish_complete', 'smoke_ok')" in watcher
    assert "@('ok', 'smoke_ok')" not in watcher
    assert "fallback_ok" not in watcher.split("function Test-TerminalState", 1)[1].split("function", 1)[0]
    assert "runner process exited without publish_complete marker" in watcher
    assert "log has not changed for" in watcher
    assert "watch timeout after" in watcher


def test_direct_runner_has_pre_run_bootstrap_interlock_before_generation() -> None:
    """06:00 direct runner 残存時も、本番生成へ進む前に bootstrap smoke interlock を通す。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    interlock_body = runner.split("function Assert-PreRunBootstrapInterlock", 1)[1].split(
        "function Convert-JsonStringArrayToStringList",
        1,
    )[0]

    assert "function Assert-PreRunBootstrapInterlock" in runner
    assert "ng-smoke-state.json" in runner
    assert "ng-smoke-logs" in runner
    assert "$BootstrapSmokeEarliestMinutes = 5 * 60 + 55" in runner
    assert "$BootstrapSmokeFreshnessMinutes = 15" in runner
    assert "updated_at" in runner.split("function Test-PreRunBootstrapSmokeMarker", 1)[1].split(
        "function Assert-PreRunBootstrapInterlock",
        1,
    )[0]
    assert "LastWriteTime" in runner.split("function Test-PreRunBootstrapSmokeMarker", 1)[1].split(
        "function Assert-PreRunBootstrapInterlock",
        1,
    )[0]
    assert "blocked_startup_self_repair_failed" in runner
    assert "-SmokeTest" in runner.split("function Assert-PreRunBootstrapInterlock", 1)[1].split(
        "# ===== sentinel: 起動できた事実 =====",
        1,
    )[0]
    assert "'-PythonExe'" in interlock_body
    assert "$PyExe" in interlock_body
    assert runner.index("Assert-PreRunBootstrapInterlock") < runner.index("Assert-RunnerBinaryInSync")
    start_block = runner.split("# ===== sentinel: 起動できた事実 =====", 1)[1].split(
        "$IsE2EOrDryRun",
        1,
    )[0]
    assert "Assert-PreRunBootstrapInterlock" in start_block
    assert start_block.index("Assert-PreRunBootstrapInterlock") < start_block.index("Assert-RunnerBinaryInSync")


def test_direct_runner_reexecutes_synced_runner_after_bootstrap_repairs_hash_drift() -> None:
    """fresh marker 後の repo/live drift は異常終了ではなく bootstrap repair + 同期済み runner 再起動へ収束する。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")

    assert "NEWS_GRASP_RUNNER_SYNC_REEXEC" in runner
    assert "function Invoke-SyncedRunnerReexec" in runner
    assert "runner binary drift repaired; relaunching synced runner" in runner
    assert "function Test-NormalDailyPublishRun" in runner

    sync_body = runner.split("function Assert-RunnerBinaryInSync", 1)[1].split(
        "# ===== sentinel: 起動できた事実 =====",
        1,
    )[0]
    reexec_body = runner.split("function Invoke-SyncedRunnerReexec", 1)[1].split(
        "function Assert-RunnerBinaryInSync",
        1,
    )[0]

    assert "Test-NormalDailyPublishRun" in sync_body
    assert "Assert-PreRunBootstrapInterlock -ForceRepair" in sync_body
    assert "Invoke-SyncedRunnerReexec" in sync_body
    assert sync_body.index("Assert-PreRunBootstrapInterlock -ForceRepair") < sync_body.index(
        "Invoke-SyncedRunnerReexec"
    )
    assert sync_body.index("Test-NormalDailyPublishRun") < sync_body.index("Invoke-RunnerBinarySyncApprovalBlock")

    assert "Start-Process" in reexec_body
    assert "-Wait" in reexec_body
    assert "$env:NEWS_GRASP_RUNNER_SYNC_REEXEC = '1'" in reexec_body
    assert "exit $exitCode" in reexec_body


def test_legacy_direct_scheduled_action_is_a_tombstone_not_a_second_production_route() -> None:
    """旧 Runner task は二重productionを起動せず、typed tombstoneだけを残す。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    bootstrap = (OPS_DIR / "news-grasp-bootstrap.ps1").read_text(encoding="utf-8-sig")

    assert "function Invoke-LegacyScheduledProductionTrampoline" in runner
    assert "Invoke-LegacyScheduledProductionTrampoline" in runner.split(
        "$RepoDir   = Resolve-NewsGraspRepoDir", 1
    )[0]
    trampoline = runner.split("function Invoke-LegacyScheduledProductionTrampoline", 1)[1].split(
        "Invoke-LegacyScheduledProductionTrampoline", 1
    )[0]
    assert "Get-ScheduledTask -TaskName 'News-Grasp Runner'" in trampoline
    assert "Get-ScheduledTaskInfo -TaskName 'News-Grasp Runner'" in trampoline
    assert "news-grasp-runner\\.ps1" in trampoline
    assert "NEWS_GRASP_LEGACY_TASK_TOMBSTONE_V1" in trampoline
    assert 'canonical_task_name = "News-Grasp Production"' in trampoline
    assert 'scheduled_attempt_status = "not_started_legacy_tombstone"' in trampoline
    assert "production_started = $false" in trampoline
    assert "-UseProductionRuntime" not in trampoline
    assert "exit 0" in trampoline

    assert "[switch] $LegacyDirectEntrypoint" in bootstrap
    scheduled_context = bootstrap.split("function Assert-ScheduledTaskLaunchContext", 1)[1].split(
        "function Invoke-BoundedGitFetch", 1
    )[0]
    assert "[bool] $AllowLegacyDirectEntrypoint" in scheduled_context
    assert "news-grasp-runner\\.ps1" in scheduled_context
    assert "$AllowLegacyDirectEntrypoint" in scheduled_context
    assert "-AllowLegacyDirectEntrypoint ([bool]$LegacyDirectEntrypoint)" in bootstrap


def test_runner_smoke_test_writes_terminal_smoke_ok_state() -> None:
    """実起動 canary はログだけでなく state に smoke_ok を残す。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    smoke_block = runner.rsplit("if ($SmokeTest)", 1)[1].split("if ($RecoverOnly)", 1)[0]

    assert "news-grasp-runner.ps1 SMOKE OK" in smoke_block
    assert "Exit-Runner -Status 'smoke_ok'" in smoke_block
    assert "exit 0" not in smoke_block


def test_runner_sha256_helper_has_dotnet_fallback() -> None:
    """隔離PowerShellでもautoloadに頼らずrunner drift guardをfail-closedにする。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    canonical_hash_body = runner.split("function Get-NewsGraspFileSha256Hex", 1)[1].split(
        "function Invoke-LegacyScheduledProductionTrampoline", 1
    )[0]
    hash_body = runner.split("function Get-FileSha256Hex", 1)[1].split("function Get-ScheduledTaskActionSummary", 1)[0]

    assert "[Security.Cryptography.SHA256]::Create()" in canonical_hash_body
    assert "[BitConverter]::ToString" in canonical_hash_body
    assert "[IO.FileShare]::Read" in canonical_hash_body
    assert "Get-NewsGraspFileSha256Hex -Path $Path" in hash_body
    assert "Get-FileHash" not in runner


def test_watcher_closes_only_verified_owned_job_and_writes_typed_watchdog_state() -> None:
    """watcherは照合済みrunnerの所有Jobだけを閉じ、生PID killを使わない。"""
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
    assert "Stop-Process" not in watcher
    assert "PROC_THREAD_ATTRIBUTE_JOB_LIST" in watcher
    assert "[NewsGraspRunnerJob]::CloseOwnedJob($handle)" in watcher
    assert watcher.index("Test-RunnerProcessIdentity") < watcher.index("[NewsGraspRunnerJob]::CloseOwnedJob($handle)")
    assert "heartbeat_at" in watcher
    assert "stale_seconds" in watcher


def test_watcher_ignores_previous_run_state_until_new_process_claims_identity() -> None:
    """起動直後に前runの古いstate/logを新runのstale判定へ流用しない。"""
    watcher = WATCHER_PS1.read_text(encoding="utf-8-sig")

    assert "function Test-StateBelongsToRunnerProcess" in watcher
    watch_body = watcher.split("function Watch-Runner", 1)[1].split(
        "if ($PSCmdlet.ParameterSetName -eq 'Status')", 1
    )[0]
    assert "$stateBoundToProcess = Test-StateBelongsToRunnerProcess" in watch_body
    assert "if (-not $stateBoundToProcess)" in watch_body
    assert "continue" in watch_body.split("if (-not $stateBoundToProcess)", 1)[1].split(
        "if (Test-TerminalState", 1
    )[0]
    assert watch_body.index("if (-not $stateBoundToProcess)") < watch_body.index(
        "if (Test-TerminalState"
    )
    assert watch_body.index("if (-not $stateBoundToProcess)") < watch_body.index(
        "$staleSeconds = Get-StaleSeconds"
    )


def test_watcher_failure_before_state_claim_writes_typed_launch_evidence(
    tmp_path: Path,
) -> None:
    """runnerがstate claim前にexit 2でも実consumerが原因証拠を残す。"""

    bin_dir = tmp_path / "bin"
    log_dir = tmp_path / "logs"
    state_file = tmp_path / "state.json"
    binding_path = tmp_path / "runner-bound-arguments.json"
    bin_dir.mkdir()
    for name in (
        "run_codex_with_timeout.ps1",
        "news-grasp-bootstrap.ps1",
        "news-grasp-runner.ps1",
        "news-grasp-lineage.ps1",
        "watch-news-grasp-runner.ps1",
        "news-grasp-deadman.ps1",
        "news-grasp-deadman-launcher.pyw",
        "news-grasp-task-launcher.pyw",
    ):
        shutil.copy2(OPS_DIR / name, bin_dir / name)
    failing_runner = tmp_path / "typed-failing-runner.ps1"
    failing_runner.write_text(
        """param(
[switch] $SmokeTest,
[switch] $SkipSourceSync,
[switch] $RecoverOnly,
[string] $DateStampOverride,
[string] $LogDirOverride,
[string] $StateFileOverride,
[string] $RepoDirOverride,
[string] $PyExeOverride,
[string] $HighCostAdmissionPath,
[string] $HighCostBudgetToolPath,
[string] $HighCostWorkspaceRoot,
[string] $RunIntent,
[string] $ScheduledAuthorityEvidencePath,
[string] $ResumeFromStage,
[string] $RecoveryDecisionPath
)
$binding = [ordered]@{
    smokeTest = [bool]$SmokeTest
    skipSourceSync = [bool]$SkipSourceSync
    dateStampOverride = [string]$DateStampOverride
    logDirOverride = [string]$LogDirOverride
    stateFileOverride = [string]$StateFileOverride
    repoDirOverride = [string]$RepoDirOverride
}
[IO.File]::WriteAllText(
    $env:NEWS_GRASP_TEST_BINDING_PATH,
    ($binding | ConvertTo-Json -Compress),
    [Text.UTF8Encoding]::new($false)
)
if (
    $SmokeTest -and $SkipSourceSync -and
    $DateStampOverride -eq '2026-08-12' -and
    $LogDirOverride -and $StateFileOverride -and $RepoDirOverride
) { exit 2 }
exit 77
""",
        encoding="utf-8-sig",
    )

    completed = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(WATCHER_PS1),
            "-Start",
            "-SmokeTest",
            "-SkipSourceSync",
            "-TimeoutMinutes",
            "1",
            "-RunnerPath",
            str(failing_runner),
            "-StateFile",
            str(state_file),
            "-LogDir",
            str(log_dir),
            "-DateStamp",
            "2026-08-12",
            "-RepoDir",
            str(ROOT),
            "-BinDir",
            str(bin_dir),
            "-PyExeOverride",
            sys.executable,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
        env={**os.environ, "NEWS_GRASP_TEST_BINDING_PATH": str(binding_path)},
    )

    assert binding_path.is_file(), "NGI_RED_WATCHER_ARGUMENT_SPLAT_INVALID"
    binding = json.loads(binding_path.read_text(encoding="utf-8"))
    assert binding == {
        "smokeTest": True,
        "skipSourceSync": True,
        "dateStampOverride": "2026-08-12",
        "logDirOverride": str(log_dir),
        "stateFileOverride": str(state_file),
        "repoDirOverride": str(ROOT),
    }, "NGI_RED_WATCHER_ARGUMENT_SPLAT_INVALID"
    assert completed.returncode == 1, (
        "NGI_RED_RUNNER_LAUNCH_EVIDENCE_MISSING\n"
        + completed.stdout
        + completed.stderr
    )
    evidence_path = log_dir / "runner-launch-evidence-2026-08-12.json"
    assert evidence_path.is_file(), "NGI_RED_RUNNER_LAUNCH_EVIDENCE_MISSING"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8-sig"))
    assert evidence == {
        **evidence,
        "schemaVersion": "NEWS_GRASP_RUNNER_LAUNCH_EVIDENCE_V1",
        "status": "failed_before_state_claim",
        "reasonCode": "RUNNER_EXITED_BEFORE_STATE_CLAIM",
        "childExitCode": 2,
        "stateClaimed": False,
    }, "NGI_RED_WATCHER_CHILD_EXIT_CODE_COLLAPSED"
    assert evidence["processId"] > 0
    assert len(evidence["processCreationTime"]) >= 20
    assert len(evidence["commandIdentitySha256"]) == 64
    assert len(evidence["powershellSha256"]) == 64
    assert len(evidence["runnerSha256"]) == 64
    assert Path(evidence["workingDirectory"]).resolve() == ROOT.resolve()
    launcher = runpy.run_path(str(OPS_DIR / "news-grasp-task-launcher.pyw"))
    summary = launcher["read_runner_launch_evidence"](
        evidence_path,
        issue_date="2026-08-12",
        expected_root=log_dir,
    )
    assert summary["status"] == "failed_before_state_claim"
    assert summary["reasonCode"] == "RUNNER_EXITED_BEFORE_STATE_CLAIM"
    assert summary["childExitCode"] == 2
    assert len(summary["sha256"]) == 64


def test_watcher_terminal_state_does_not_crash_powershell_host(
    tmp_path: Path,
) -> None:
    """A terminal child state must leave the watcher host with exit code zero."""

    bin_dir = tmp_path / "bin"
    log_dir = tmp_path / "logs"
    state_file = tmp_path / "state.json"
    bin_dir.mkdir()
    for name in (
        "run_codex_with_timeout.ps1",
        "news-grasp-bootstrap.ps1",
        "news-grasp-runner.ps1",
        "news-grasp-lineage.ps1",
        "watch-news-grasp-runner.ps1",
        "news-grasp-deadman.ps1",
        "news-grasp-deadman-launcher.pyw",
        "news-grasp-task-launcher.pyw",
    ):
        shutil.copy2(OPS_DIR / name, bin_dir / name)
    successful_runner = tmp_path / "typed-successful-runner.ps1"
    successful_runner.write_text(
        """param(
[switch] $SmokeTest,
[switch] $SkipSourceSync,
[switch] $RecoverOnly,
[string] $DateStampOverride,
[string] $LogDirOverride,
[string] $StateFileOverride,
[string] $RepoDirOverride,
[string] $PyExeOverride,
[string] $HighCostAdmissionPath,
[string] $HighCostBudgetToolPath,
[string] $HighCostWorkspaceRoot,
[string] $RunIntent,
[string] $ScheduledAuthorityEvidencePath,
[string] $ResumeFromStage,
[string] $RecoveryDecisionPath
)
$now = (Get-Date).ToString('yyyy-MM-ddTHH:mm:ss.fffK')
$payload = [ordered]@{
    status = 'smoke_ok'
    message = 'typed fixture completed'
    exit_code = 0
    updated_at = $now
    heartbeat_at = $now
    run_id = 'typed-success'
    pid = [int]$PID
    repo_dir = [string]$RepoDirOverride
    runner_path = [string]$PSCommandPath
    process_creation_time = [Diagnostics.Process]::GetCurrentProcess().StartTime.ToString('o')
    command_line_fingerprint = 'fixture'
}
[IO.File]::WriteAllText(
    $StateFileOverride,
    (($payload | ConvertTo-Json -Depth 6) + [Environment]::NewLine),
    [Text.UTF8Encoding]::new($false)
)
Start-Sleep -Milliseconds 300
exit 0
""",
        encoding="utf-8-sig",
    )

    completed = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(WATCHER_PS1),
            "-Start",
            "-SmokeTest",
            "-SkipSourceSync",
            "-TimeoutMinutes",
            "1",
            "-RunnerPath",
            str(successful_runner),
            "-StateFile",
            str(state_file),
            "-LogDir",
            str(log_dir),
            "-DateStamp",
            "2026-08-12",
            "-RepoDir",
            str(ROOT),
            "-BinDir",
            str(bin_dir),
            "-PyExeOverride",
            sys.executable,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )

    assert state_file.is_file(), "NGI_RED_WATCHER_TERMINAL_STATE_MISSING"
    state = json.loads(state_file.read_text(encoding="utf-8-sig"))
    assert state["status"] == "smoke_ok"
    assert completed.returncode == 0, (
        "NGI_RED_WATCHER_CALLBACK_HOST_RC2\n"
        + completed.stdout
        + completed.stderr
    )
    evidence_path = log_dir / "runner-launch-evidence-2026-08-12.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8-sig"))
    assert evidence["status"] == "terminal_state_reached"
    assert evidence["reasonCode"] == "RUNNER_TERMINAL_STATE_REACHED"
    assert evidence["childExitCode"] == 0
    assert evidence["stateClaimed"] is True


def test_runner_and_watcher_retry_transient_state_reads_before_declaring_corrupt() -> None:
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    watcher = WATCHER_PS1.read_text(encoding="utf-8-sig")
    runner_read = runner.split("function Read-RunnerStateOrNull", 1)[1].split(
        "function Invoke-WithRunnerStateLock", 1
    )[0]
    watcher_read = watcher.split("function Read-State", 1)[1].split("function Write-StateAtomic", 1)[0]

    for body in (runner_read, watcher_read):
        assert "for ($attempt = 1; $attempt -le 3; $attempt++)" in body
        assert "Start-Sleep -Milliseconds 100" in body
        assert body.index("for ($attempt = 1; $attempt -le 3; $attempt++)") < body.index("corrupt_backup")


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


def test_runner_summary_reflection_gate_is_date_bound() -> None:
    """Summary reflection は latest fallback ではなく対象日を検証する。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    probe_block = runner.split("wrapper invoke START (agent=codex, role=newsroom_editor", 1)[1].split(
        "wrapper invoke END (agent=codex, role=newsroom_editor",
        1,
    )[0]
    gate_block = runner.split("summary reflection gate start", 1)[1].split("daily quality gate start", 1)[0]

    assert "SuccessProbeCommand" not in probe_block
    assert "'tools.validate_summary_reflection', '--date', $DateStamp" in gate_block
    assert "validate_summary_reflection --latest" not in runner


def test_runner_daily_quality_gate_uses_structured_json_output() -> None:
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    gate_block = runner.split("daily quality gate start", 1)[1].split("Stage4: Codex DeepDive", 1)[0]
    assert "'tools.validate_daily_quality', '--date', $DateStamp, '--json'" in gate_block


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


def test_runner_tts_publish_external_failure_preserves_typed_boundary() -> None:
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    block = runner.split("Daily TTS audio (fatal", 1)[1].split("2.9 digest/data commit", 1)[0]

    assert "'tools.tts.publish_audio', $DateStamp, '--json'" in block
    assert "if ($ttsRc -eq 71 -and $ttsStep.Name -eq 'tts publish_audio')" in block
    assert "Stop-ExternalReadiness" in block
    assert "-Kind 'github_release_upload_transient'" in block
    assert "-System 'github-release'" in block
    assert "-ExternalStatus 'service_unavailable'" in block


def test_runner_deepdive_tts_publish_external_failure_preserves_typed_boundary() -> None:
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    block = runner.split("DeepDive dialogue audio (fatal", 1)[1].split("2.9 digest/data commit", 1)[0]

    assert "'tools.tts.deepdive_audio', $DateStamp, '--json'" in block
    assert "if ($deepDiveTtsRc -eq 71 -and $deepDiveTtsStep.Name -eq 'deepdive dialogue publish')" in block
    assert "Stop-ExternalReadiness" in block
    assert "-Kind 'github_release_upload_transient'" in block
    assert "-System 'github-release'" in block
    assert "-ExternalStatus 'service_unavailable'" in block


def test_installer_skip_task_registration_does_not_require_scheduler_convergence() -> None:
    installer = (OPS_DIR / "install-news-grasp-ops.ps1").read_text(encoding="utf-8-sig")

    assert "if ((-not $SkipTaskRegistration) -and (-not $runnerRegistered))" in installer


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


def test_runner_fresh_dispatches_failed_deploy_workflow_before_publish_fail() -> None:
    """Deploy Pages completed/failure は publish_failed 確定前に fresh workflow dispatch を 1 回試す。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")

    block = runner.split("publish verification start", 1)[1].split("youtube podcast finalize start", 1)[0]

    assert "'verify-publish'" in block
    assert "'dispatch-deploy-workflow'" in block
    assert "'wait-deploy-workflow'" in block
    assert block.index("'verify-publish'") < block.index("'dispatch-deploy-workflow'")
    assert block.index("'dispatch-deploy-workflow'") < block.index("'wait-deploy-workflow'")
    assert block.index("'wait-deploy-workflow'") < block.rindex("'verify-publish'")
    assert "gh run rerun" not in block
    assert "rerun --failed" not in block
    assert "Set-RunnerState -Status 'publish_failed'" not in block
    assert "Invoke-AutonomousCompletionPolicy" in block
    assert "-GateId 'publish-verify'" in block


def test_runner_verifies_publish_complete_manifest_before_success() -> None:
    """publish_complete 前に unified manifest verifier を通す。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")

    assert "verify-publish-complete" in runner
    assert runner.index("deepdive podcast verification OK") < runner.index("verify-publish-complete")
    assert runner.index("podcast playlist audit OK") < runner.index("verify-publish-complete")
    assert runner.index("send_push start") < runner.index("verify-publish-complete")
    assert runner.index("verify-publish-complete") < runner.rindex("news-grasp-runner.ps1 OK")
    block = runner.split("publish-complete manifest verification start", 1)[1].split("news-grasp-runner.ps1 OK", 1)[0]
    before_block = runner.split("$distributionSummary = Write-DistributionManifest", 1)[1].split(
        "# ===== 5. digest + docs",
        1,
    )[0]
    distribution_body = runner.split("function Write-DistributionManifest", 1)[1].split("function Test-DailyArtifactsExist", 1)[0]
    assert "data\\distribution" in distribution_body
    assert runner.count("$distributionSummary = Write-DistributionManifest") == 1
    assert 'add "data/distribution/$DateStamp.json"' in before_block
    assert 'commit -m "distribution: record publish state for $DateStamp"' in before_block
    assert runner.index("$distributionSummary = Write-DistributionManifest") < runner.index("push origin HEAD:main start")
    assert "$DateStamp.json" in distribution_body
    assert "build\\publish-complete\\$DateStamp.json" in block
    assert "build\\notification\\$DateStamp.json" in runner
    assert "'--notification-state'" in block
    assert "'--producer-state'" in block
    assert "$StateFile" in block
    assert "$StateFilePath" not in block
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
    assert manifest["publish_commit_resolution"] == "post_push_verify"
    assert manifest["same_publish_contract"] == "pre_publish_commit_must_equal_verified_publish_commit"
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


def test_runner_start_log_binds_attempt_to_run_id() -> None:
    """scheduled/recovery の log range を一意に切れるよう start marker に run_id を残す。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")

    start_line = next(line for line in runner.splitlines() if "news-grasp-runner.ps1 start" in line)
    assert "run_id=$RunId" in start_line


def test_scheduled_equivalent_nopublish_uses_same_runner_with_isolated_state() -> None:
    """最終E2Eはinstalled stable launcherから同じrunnerをNoPublishで通す。"""
    source = SCHEDULED_EQUIVALENT_PS1.read_text(encoding="utf-8-sig")

    assert "news-grasp-runner.ps1" in source
    assert "-NoPublish" in source
    assert "-DateStampOverride" in source
    assert "-RepoDirOverride" in source
    assert "-StateFileOverride" in source
    assert "-LogDirOverride" in source
    assert "-PyExeOverride" in source
    assert "-CodexWrapperOverride" in source
    assert "scripts\\ops\\run_codex_with_timeout.ps1" in source
    assert "'-WindowStyle', 'Hidden'" in source
    assert "-NonInteractive" in source
    assert "scheduled_entrypoint_mode = 'installed_stable_launcher'" in source
    assert "NEWS_GRASP_INSTALLED_NOPUBLISH_LAUNCH_AUTHORITY_V1" in source
    assert "& $installedTaskPythonPath @installedLauncherArguments" in source
    assert "& $PowerShellExe @runnerArguments" not in source
    assert "expected_terminal_state = 'publish_dry_run_ok'" in source
    assert "elapsed_seconds = $elapsedSeconds" in source
    assert "duration_slo_limit_seconds = $durationSloLimitSeconds" in source
    assert "duration_slo_met = $durationSloMet" in source
    assert "$durationSloLimitSeconds = 3600" in source
    assert "$durationSloMet" in source.split("ok =", 1)[1]
    assert "Start-Process" not in source


def test_installed_nopublish_runner_revalidates_external_authority_hash() -> None:
    """launcherが封印したfixture hashをrunner実consumerがprobe直前にも再検証する。"""
    wrapper = SCHEDULED_EQUIVALENT_PS1.read_text(encoding="utf-8-sig")
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")

    assert "'-ExternalHealthAuthorityExpectedSha256'" in wrapper
    assert "[string] $ExternalHealthAuthorityExpectedSha256" in runner
    assert "EXTERNAL_AUTHORITY_FIXTURE_HASH_DRIFT" in runner
    assert "Get-NewsGraspFileSha256Hex -Path $script:ExternalHealthAuthorityPath" in runner


def test_runner_does_not_shadow_external_authority_hash_parameter() -> None:
    """E2E fixture hash引数をscript-scope初期化で空文字へ上書きしない。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")

    assert "$script:ExternalHealthAuthorityExpectedSha256 = ''" not in runner


def test_installed_nopublish_launcher_accepts_only_same_generation_isolation() -> None:
    """隔離runnerはactive runtimeと同一commit/common-dir/hashの場合だけ許可する。"""
    launcher = (OPS_DIR / "news-grasp-task-launcher.pyw").read_text(
        encoding="utf-8-sig"
    )

    for field in (
        '"executionRepoRoot"',
        '"executionRepoCommit"',
        '"runtimeRepoCommit"',
    ):
        assert field in launcher
    assert "_git_common_dir(execution_repo) != _git_common_dir(runtime_repo)" in launcher
    assert '"status", "--porcelain", "--untracked-files=no"' in launcher
    assert "observed_runner != expected_runner" in launcher
    assert "_file_sha256(expected_runner) != _file_sha256(runtime_runner)" in launcher
    assert "observed_repo != execution_repo" in launcher
    assert "observed_codex_wrapper != expected_codex_wrapper" in launcher
    assert "_file_sha256(expected_codex_wrapper)" in launcher
    assert "_file_sha256(runtime_codex_wrapper)" in launcher


def _installed_nopublish_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    namespace = runpy.run_path(str(OPS_DIR / "news-grasp-task-launcher.pyw"))
    runtime_repo = tmp_path / "runtime"
    execution_repo = tmp_path / "isolation"
    bin_dir = tmp_path / "bin"
    for root in (runtime_repo, execution_repo):
        (root / "scripts" / "ops").mkdir(parents=True)
        (root / "scripts" / "ops" / "news-grasp-runner.ps1").write_text(
            "runner\n", encoding="utf-8"
        )
        (root / "scripts" / "ops" / "run_codex_with_timeout.ps1").write_text(
            "wrapper\n", encoding="utf-8"
        )
    bin_dir.mkdir()
    executable = tmp_path / "powershell.exe"
    executable.write_bytes(b"fixture executable")
    arguments_path = execution_repo / "build" / "runner-arguments.json"
    arguments_path.parent.mkdir(parents=True)
    external_authority_path = execution_repo / "build" / "external-health-authority-v1.json"
    external_authority_path.write_text('{"status":"ready"}\n', encoding="utf-8")
    file_hash = "b" * 64
    arguments = [
        "-File",
        str(execution_repo / "scripts" / "ops" / "news-grasp-runner.ps1"),
        "-NoPublish",
        "-RepoDirOverride",
        str(execution_repo),
        "-CodexWrapperOverride",
        str(execution_repo / "scripts" / "ops" / "run_codex_with_timeout.ps1"),
        "-ExternalHealthAuthorityPathOverride",
        str(external_authority_path),
        "-ExternalHealthAuthorityExpectedSha256",
        file_hash,
    ]
    arguments_path.write_text(json.dumps(arguments), encoding="utf-8")
    commit = "a" * 40
    launcher_path = (OPS_DIR / "news-grasp-task-launcher.pyw").resolve()
    launcher_identity = {
        "authorityPath": str(bin_dir / "stable-authority.json"),
        "authorityFileSha256": file_hash,
    }
    unsigned = {
        "schemaVersion": "NEWS_GRASP_INSTALLED_NOPUBLISH_LAUNCH_AUTHORITY_V1",
        "issueDate": "2026-08-12",
        "attemptId": "nopublish:2026-08-12",
        "stableLauncherPath": str(launcher_path),
        "stableLauncherSha256": file_hash,
        "stableTaskAuthorityPath": launcher_identity["authorityPath"],
        "stableTaskAuthorityFileSha256": file_hash,
        "runnerExecutablePath": str(executable),
        "runnerExecutableSha256": file_hash,
        "executionRepoRoot": str(execution_repo),
        "executionRepoCommit": commit,
        "runtimeRepoCommit": commit,
        "runnerArgumentsPath": str(arguments_path),
        "runnerArgumentsFileSha256": file_hash,
        "externalHealthAuthorityFixturePath": str(external_authority_path),
        "externalHealthAuthorityFixtureSha256": file_hash,
    }
    authority = {**unsigned, "authoritySha256": namespace["_sha256_json"](unsigned)}
    authority_path = execution_repo / "build" / "launch-authority.json"
    authority_path.write_text(json.dumps(authority), encoding="utf-8")

    monkeypatch.setitem(
        namespace["_run_installed_nopublish_authority"].__globals__,
        "resolve_bootstrap_launch_roots",
        lambda **_kwargs: {"configuredRuntime": runtime_repo},
    )
    monkeypatch.setitem(
        namespace["_run_installed_nopublish_authority"].__globals__,
        "_validate_active_production_generation",
        lambda **_kwargs: {},
    )
    monkeypatch.setitem(
        namespace["_run_installed_nopublish_authority"].__globals__,
        "_file_sha256",
        lambda _path: file_hash,
    )
    monkeypatch.setitem(
        namespace["_run_installed_nopublish_authority"].__globals__,
        "_git_common_dir",
        lambda _path: tmp_path / "common",
    )

    def fake_git(_repo: Path, *args: str, **_kwargs) -> str:
        return commit if args[:2] == ("rev-parse", "HEAD") else ""

    monkeypatch.setitem(
        namespace["_run_installed_nopublish_authority"].__globals__,
        "_run_git",
        fake_git,
    )
    monkeypatch.setattr(
        namespace["subprocess"],
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0),
    )
    return namespace, authority_path, bin_dir, launcher_identity


def test_installed_nopublish_launcher_runs_same_generation_isolation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    namespace, authority_path, bin_dir, launcher_identity = _installed_nopublish_fixture(
        tmp_path, monkeypatch
    )

    observed = namespace["_run_installed_nopublish_authority"](
        authority_path=authority_path,
        bin_dir=bin_dir,
        launcher_identity=launcher_identity,
    )

    assert observed == 0


def test_installed_launcher_resolves_policy_consumer_from_execution_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """bin配置後も実行generation側のpolicy consumerを解決できる。"""
    namespace = runpy.run_path(str(OPS_DIR / "news-grasp-task-launcher.pyw"))
    execution_repo = tmp_path / "execution"
    policy_module = execution_repo / "tools" / "news_grasp_e2e_attempt_policy.py"
    policy_module.parent.mkdir(parents=True)
    policy_module.write_text(
        (ROOT / "tools" / "news_grasp_e2e_attempt_policy.py").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    monkeypatch.setitem(namespace, "_validate_e2e_policy_transition", None)

    consumer = namespace["_load_policy_consumer_from_execution_repo"](
        execution_repo
    )

    assert callable(consumer)


def test_installed_nopublish_launcher_rejects_cross_generation_isolation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    namespace, authority_path, bin_dir, launcher_identity = _installed_nopublish_fixture(
        tmp_path, monkeypatch
    )
    value = json.loads(authority_path.read_text(encoding="utf-8"))
    value["executionRepoCommit"] = "c" * 40
    unsigned = dict(value)
    unsigned.pop("authoritySha256")
    value["authoritySha256"] = namespace["_sha256_json"](unsigned)
    authority_path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(RuntimeError, match="NEWS_GRASP_INSTALLED_GENERATION_DRIFT"):
        namespace["_run_installed_nopublish_authority"](
            authority_path=authority_path,
            bin_dir=bin_dir,
            launcher_identity=launcher_identity,
        )


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
def test_runner_scopes_gate_retry_budget_to_current_run_intent() -> None:
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    assert "gate-attempt-archives" in runner
    assert "reset gate attempt ledger for run_id=" in runner
    assert 'news-grasp-repair-classify-$GateId-$DateStamp-$RunId-attempt$attempt.json' in runner


def test_runner_does_not_surface_retry_budget_as_repair_cause() -> None:
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")

    assert "repair worker denied by retry budget" not in runner
    assert "gate retry limit reached before repair worker" not in runner
    assert "gate retry ledger denied repair worker" in runner
    assert "preserving typed gate classification" in runner


def test_runner_executes_compound_repair_plan_before_single_gate_reverify() -> None:
    """複合gateを先頭issueだけで修復する旧経路へ戻さない。"""
    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")

    assert "repair_steps" in runner
    assert "repair-plan" in runner
    assert "--plan-file" in runner
    assert "compound deterministic repair OK" in runner


def _run_install_guard(
    command: str,
    *,
    env: dict[str, str] | None = None,
    powershell_executable: str = POWERSHELL,
) -> subprocess.CompletedProcess[str]:
    guard = OPS_DIR / "install-news-grasp-ops-guard.ps1"
    script = (
        "$ErrorActionPreference='Stop'; "
        f"try {{ . '{guard}'; {command} }} catch {{ "
        "[Console]::Error.WriteLine($_.Exception.ToString()); exit 1 }"
    )
    return subprocess.run(
        [powershell_executable, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
        env=env,
    )


def test_ops_installer_rejects_noncanonical_source_before_recovery_or_mutation() -> None:
    """runtime-root正本または同一common-dirのclean remote-main generationだけを許可する。"""
    installer = (OPS_DIR / "install-news-grasp-ops.ps1").read_text(encoding="utf-8-sig")
    guard = (OPS_DIR / "install-news-grasp-ops-guard.ps1").read_text(encoding="utf-8-sig")

    assert "function Assert-NewsGraspCanonicalInstallSource" in guard
    assert "NEWS_GRASP_NONCANONICAL_INSTALL_SOURCE" in guard
    assert "[string]$runtimeRoot.repoDir" in guard
    assert "function Test-NewsGraspPromotableInstallSource" in guard
    assert "rev-parse --show-toplevel" in guard
    assert "refs/remotes/origin/main" in guard
    assert "ls-files -v" in guard
    assert "-c core.quotepath=false -C $CandidateRepoDir ls-files -v" in guard
    assert "ArgumentList.Add('hash-object')" not in guard
    assert "'hash-object', '--no-filters', '--'" in guard
    assert "ConvertTo-NewsGraspWindowsProcessArgument" in guard
    assert "'GIT_CONFIG_GLOBAL' = 'NUL'" in guard
    assert "'GIT_ATTR_NOSYSTEM' = '1'" in guard
    assert "'GIT_NO_REPLACE_OBJECTS' = '1'" in guard
    assert "'GIT_CONFIG_NOSYSTEM' = '1'" in guard
    assert "diff --quiet HEAD --" not in guard
    assert "diff --cached --quiet --no-ext-diff --no-textconv --" in guard
    assert "[int] $MaxEntries = 16384" in guard
    assert "[string]$runtimeRoot.evidenceRepoDir" in guard
    assert "[string] $EvidenceRepoDir" in installer
    assert "NEWS_GRASP_EVIDENCE_REPO_REQUIRED" in installer
    assert "NEWS_GRASP_EVIDENCE_REPO_SELF_REFERENCE_FORBIDDEN" in installer
    assert "Read-NewsGraspVerifiedFile" in installer
    assert "existingRuntimeRootSnapshot.Sha256" in installer
    assert "runtimeRootAuthoritySha" in installer
    assert "Get-Content -LiteralPath $existingRuntimeRootPath -Raw" not in installer
    assert "[int64] $MaxBytes" in guard
    assert "-MaxBytes 65536" in guard
    assert "-MaxBytes 65536" in installer
    evidence_validation = installer.index("$runtimeEvidenceRepoDir = if ($EvidenceRepoDir)")
    mutation_start = installer.index("$script:InstallationMutationStarted = $true")
    assert evidence_validation < mutation_start
    assert "Test-NewsGraspUnsafeTraversalReparsePoint -Item $buildItem" in guard
    main = installer.split("$RepoDir = Resolve-NewsGraspRepoDir", 1)[1]
    authority_call = main.index("Assert-NewsGraspCanonicalInstallSource")
    assert authority_call < main.index("Recover-NewsGraspInterruptedInstall")
    assert authority_call < main.index("$script:InstallationMutationStarted = $true")
    pre_mutation = main.split("$script:InstallationMutationStarted = $true", 1)[0]
    assert "Read-NewsGraspVerifiedFile" in pre_mutation
    assert "$sourceSnapshots[$file]" in pre_mutation


def test_ops_installer_allows_clean_evidence_generation_transition() -> None:
    """旧runtime rootから、同一candidate generationのclean evidenceへ遷移できる。"""
    installer = (OPS_DIR / "install-news-grasp-ops.ps1").read_text(encoding="utf-8-sig")
    transition = "Test-NewsGraspPromotableInstallSource `\n            -CurrentRepoDir $runtimeEvidenceRepoDir `\n            -CandidateRepoDir $RepoDir"
    assert transition in installer
    assert "NEWS_GRASP_EVIDENCE_REPO_GENERATION_DRIFT" in installer


def test_ops_installer_preflights_shared_broker_generation_before_mutation() -> None:
    """共有broker probeは診断用に残すが、決定論的promotionの入口へ結合しない。"""
    installer = (OPS_DIR / "install-news-grasp-ops.ps1").read_text(encoding="utf-8-sig")
    assert "function Assert-NewsGraspSharedBrokerGeneration" in installer
    assert "NEWS_GRASP_SHARED_BROKER_GENERATION_DRIFT" in installer
    executable = installer.split("$TaskPythonwPath = Resolve-NewsGraspTaskPythonw", 1)[1]
    executable = executable.split("$ops = Join-Path $RepoDir 'scripts\\ops'", 1)[0]
    assert "Assert-NewsGraspSharedBrokerGeneration" not in executable
    assert "Assert-NewsGraspExternalControlPlaneReady" not in executable

    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    assert "tools\\news_grasp_external_control.py" in runner
    assert "external_control_plane_unavailable" in runner


def test_installer_resolves_workspace_harness_from_external_worktree_git_common_dir(
    tmp_path: Path,
) -> None:
    """repo外worktreeでもGit common-dirからworkspace-global harness正本を解決する。"""
    workspace_root = tmp_path / "ProjectFolders"
    canonical_repo = workspace_root / "News-Grasp"
    external_worktree = tmp_path / "ng-0640-redesign-v1"
    (workspace_root / "tools" / "harness").mkdir(parents=True)
    (workspace_root / "docs" / "harness").mkdir(parents=True)
    (workspace_root / "tools" / "harness" / "task_model_routing.py").write_text(
        "# fixture\n", encoding="utf-8"
    )
    (workspace_root / "docs" / "harness" / "high_cost_model_routes_v1.json").write_text(
        "{}\n", encoding="utf-8"
    )
    canonical_repo.mkdir()
    subprocess.run(["git", "init", str(canonical_repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(canonical_repo), "config", "user.email", "fixture@example.invalid"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(canonical_repo), "config", "user.name", "News-Grasp fixture"],
        check=True,
        capture_output=True,
    )
    (canonical_repo / "tracked.txt").write_text("fixture\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(canonical_repo), "add", "tracked.txt"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(canonical_repo), "commit", "-m", "fixture"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(canonical_repo), "worktree", "add", str(external_worktree), "HEAD"],
        check=True,
        capture_output=True,
    )

    installer = (OPS_DIR / "install-news-grasp-ops.ps1").read_text(encoding="utf-8-sig")
    resolver_source = "function Resolve-NewsGraspWorkspaceHarnessRoot" + installer.split(
        "function Resolve-NewsGraspWorkspaceHarnessRoot", 1
    )[1].split("function Assert-NewsGraspSharedBrokerGeneration", 1)[0]
    resolver_path = tmp_path / "workspace-root-resolver.ps1"
    resolver_path.write_text(resolver_source, encoding="utf-8")
    guard = OPS_DIR / "install-news-grasp-ops-guard.ps1"
    script = (
        "$ErrorActionPreference='Stop'; "
        f". '{guard}'; . '{resolver_path}'; "
        f"Resolve-NewsGraspWorkspaceHarnessRoot -StartPath '{external_worktree}'"
    )
    completed = subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert Path(completed.stdout.strip()).resolve() == workspace_root.resolve()


def test_bootstrap_archives_stale_same_day_startup_canary_before_preflight() -> None:
    """Expected Red: generation切替後の旧canary rootを次のcanary admissionへ混入させない。"""
    bootstrap = (OPS_DIR / "news-grasp-bootstrap.ps1").read_text(
        encoding="utf-8-sig"
    )

    assert "function Archive-StaleStartupCanaryState" in bootstrap
    archive_call = (
        "Archive-StaleStartupCanaryState -StateFile $StateFile "
        "-ExpectedRoot $RepoDir"
    )
    archive = bootstrap.index(archive_call)
    control_plane = bootstrap.index("$controlPlaneArgs = @(", archive)
    assert archive < control_plane
    assert "STARTUP_CANARY_STATE_ARCHIVE_V1" in bootstrap
    assert "StateFileSha256" in bootstrap


def test_install_guard_dynamically_rejects_noncanonical_runtime_root_source(tmp_path: Path) -> None:
    canonical_repo = tmp_path / "canonical"
    legacy_repo = tmp_path / "legacy"
    bin_dir = tmp_path / "bin"
    canonical_repo.mkdir()
    legacy_repo.mkdir()
    bin_dir.mkdir()
    sentinel = tmp_path / "sentinel.txt"
    sentinel.write_text("unchanged", encoding="utf-8")
    runtime_root = {
        "schemaVersion": "NEWS_GRASP_RUNTIME_ROOT_V1",
        "repoDir": str(canonical_repo),
        "pythonExe": str(tmp_path / "python.exe"),
        "evidenceRepoDir": str(canonical_repo),
    }
    (bin_dir / "news-grasp-runtime-root-v1.json").write_text(
        json.dumps(runtime_root), encoding="utf-8"
    )

    completed = _run_install_guard(
        "Assert-NewsGraspCanonicalInstallSource "
        f"-ResolvedRepoDir '{legacy_repo}' -RequestedBinDir '{bin_dir}' "
        f"-CanonicalBinDir '{bin_dir}' -TrustedBoundary '{tmp_path}' "
        "-ManagedTaskNames @()"
    )

    assert completed.returncode != 0
    assert "NEWS_GRASP_NONCANONICAL_INSTALL_SOURCE" in completed.stderr
    assert sentinel.read_text(encoding="utf-8") == "unchanged"


def _install_source_generation_fixture(
    tmp_path: Path, *, include_tracked_build: bool = False
) -> tuple[Path, Path, Path]:
    git = r"C:\Program Files\Git\cmd\git.exe"
    canonical_repo = tmp_path / "canonical"
    promoted_repo = tmp_path / "promoted"
    bin_dir = tmp_path / "bin"
    canonical_repo.mkdir()
    bin_dir.mkdir()
    subprocess.run([git, "-C", str(canonical_repo), "init", "-b", "main"], check=True)
    subprocess.run([git, "-C", str(canonical_repo), "config", "core.autocrlf", "false"], check=True)
    subprocess.run([git, "-C", str(canonical_repo), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run([git, "-C", str(canonical_repo), "config", "user.name", "News Grasp Test"], check=True)
    (canonical_repo / "tracked.txt").write_text("clean\n", encoding="utf-8")
    (canonical_repo / ".gitignore").write_text(".venv/\n", encoding="utf-8")
    if include_tracked_build:
        (canonical_repo / "build").mkdir()
        (canonical_repo / "build" / "tracked-artifact.json").write_text(
            "{\"generation\":\"clean\"}\n", encoding="utf-8"
        )
    subprocess.run([git, "-C", str(canonical_repo), "add", "."], check=True)
    subprocess.run([git, "-C", str(canonical_repo), "commit", "-m", "fixture"], check=True)
    subprocess.run(
        [git, "-C", str(canonical_repo), "update-ref", "refs/remotes/origin/main", "HEAD"],
        check=True,
    )
    subprocess.run(
        [git, "-C", str(canonical_repo), "worktree", "add", "--detach", str(promoted_repo), "HEAD"],
        check=True,
    )
    runtime_root = {
        "schemaVersion": "NEWS_GRASP_RUNTIME_ROOT_V1",
        "repoDir": str(canonical_repo),
        "pythonExe": str(tmp_path / "python.exe"),
        "evidenceRepoDir": str(canonical_repo),
    }
    (bin_dir / "news-grasp-runtime-root-v1.json").write_text(
        json.dumps(runtime_root), encoding="utf-8"
    )
    return canonical_repo, promoted_repo, bin_dir


def test_install_guard_accepts_clean_origin_main_sibling_worktree_promotion(tmp_path: Path) -> None:
    _, promoted_repo, bin_dir = _install_source_generation_fixture(tmp_path)
    (promoted_repo / "build" / "evidence").mkdir(parents=True)

    completed = _run_install_guard(
        "Assert-NewsGraspCanonicalInstallSource "
        f"-ResolvedRepoDir '{promoted_repo}' -RequestedBinDir '{bin_dir}' "
        f"-CanonicalBinDir '{bin_dir}' -TrustedBoundary '{tmp_path}' "
        "-ManagedTaskNames @() | Out-Null"
    )

    assert completed.returncode == 0, completed.stderr


def test_install_guard_rejects_sibling_worktree_with_head_drift(tmp_path: Path) -> None:
    _, promoted_repo, bin_dir = _install_source_generation_fixture(tmp_path)
    git = r"C:\Program Files\Git\cmd\git.exe"
    (promoted_repo / "tracked.txt").write_text("next generation\n", encoding="utf-8")
    subprocess.run([git, "-C", str(promoted_repo), "add", "tracked.txt"], check=True)
    subprocess.run([git, "-C", str(promoted_repo), "commit", "-m", "drift"], check=True)

    completed = _run_install_guard(
        "Assert-NewsGraspCanonicalInstallSource "
        f"-ResolvedRepoDir '{promoted_repo}' -RequestedBinDir '{bin_dir}' "
        f"-CanonicalBinDir '{bin_dir}' -TrustedBoundary '{tmp_path}' "
        "-ManagedTaskNames @() | Out-Null"
    )

    assert completed.returncode != 0
    assert "NEWS_GRASP_NONCANONICAL_INSTALL_SOURCE" in completed.stderr


def test_install_guard_rejects_sibling_worktree_with_tracked_dirty_state(tmp_path: Path) -> None:
    _, promoted_repo, bin_dir = _install_source_generation_fixture(tmp_path)
    (promoted_repo / "tracked.txt").write_text("dirty\n", encoding="utf-8")

    completed = _run_install_guard(
        "Assert-NewsGraspCanonicalInstallSource "
        f"-ResolvedRepoDir '{promoted_repo}' -RequestedBinDir '{bin_dir}' "
        f"-CanonicalBinDir '{bin_dir}' -TrustedBoundary '{tmp_path}' "
        "-ManagedTaskNames @() | Out-Null"
    )

    assert completed.returncode != 0
    assert "NEWS_GRASP_NONCANONICAL_INSTALL_SOURCE" in completed.stderr


def test_install_guard_rejects_nested_directory_inside_promotable_worktree(tmp_path: Path) -> None:
    _, promoted_repo, bin_dir = _install_source_generation_fixture(tmp_path)
    nested = promoted_repo / "build" / "evil"
    nested.mkdir(parents=True)

    completed = _run_install_guard(
        "Assert-NewsGraspCanonicalInstallSource "
        f"-ResolvedRepoDir '{nested}' -RequestedBinDir '{bin_dir}' "
        f"-CanonicalBinDir '{bin_dir}' -TrustedBoundary '{tmp_path}' "
        "-ManagedTaskNames @() | Out-Null"
    )

    assert completed.returncode != 0
    assert "NEWS_GRASP_NONCANONICAL_INSTALL_SOURCE" in completed.stderr


def test_install_guard_rejects_reparse_build_root_in_promotable_worktree(tmp_path: Path) -> None:
    _, promoted_repo, bin_dir = _install_source_generation_fixture(tmp_path)
    outside = tmp_path / "outside-build"
    outside.mkdir()
    build_root = promoted_repo / "build"
    junction_result = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"New-Item -ItemType Junction -Path '{build_root}' -Target '{outside}' | Out-Null",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
    )
    if junction_result.returncode != 0:
        pytest.skip(f"junction fixture unavailable: {junction_result.stderr}")

    completed = _run_install_guard(
        "Assert-NewsGraspCanonicalInstallSource "
        f"-ResolvedRepoDir '{promoted_repo}' -RequestedBinDir '{bin_dir}' "
        f"-CanonicalBinDir '{bin_dir}' -TrustedBoundary '{tmp_path}' "
        "-ManagedTaskNames @() | Out-Null"
    )

    assert completed.returncode != 0
    assert "NEWS_GRASP_NONCANONICAL_INSTALL_SOURCE" in completed.stderr


def test_install_guard_rejects_nested_reparse_under_allowed_build_tree(tmp_path: Path) -> None:
    _, promoted_repo, bin_dir = _install_source_generation_fixture(tmp_path)
    outside = tmp_path / "outside-backups"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("unchanged", encoding="utf-8")
    nested_reparse = promoted_repo / "build" / "live-runner-backups"
    nested_reparse.parent.mkdir(parents=True)
    junction_result = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"New-Item -ItemType Junction -Path '{nested_reparse}' -Target '{outside}' | Out-Null",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
    )
    if junction_result.returncode != 0:
        pytest.skip(f"junction fixture unavailable: {junction_result.stderr}")

    completed = _run_install_guard(
        "Assert-NewsGraspCanonicalInstallSource "
        f"-ResolvedRepoDir '{promoted_repo}' -RequestedBinDir '{bin_dir}' "
        f"-CanonicalBinDir '{bin_dir}' -TrustedBoundary '{tmp_path}' "
        "-ManagedTaskNames @() | Out-Null"
    )

    assert completed.returncode != 0
    assert "NEWS_GRASP_NONCANONICAL_INSTALL_SOURCE" in completed.stderr
    assert sentinel.read_text(encoding="utf-8") == "unchanged"


def test_install_guard_rejects_ignored_executable_payload_outside_build(tmp_path: Path) -> None:
    _, promoted_repo, bin_dir = _install_source_generation_fixture(tmp_path)
    fake_python = promoted_repo / ".venv" / "Scripts" / "python.exe"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_bytes(b"untrusted executable")

    completed = _run_install_guard(
        "Assert-NewsGraspCanonicalInstallSource "
        f"-ResolvedRepoDir '{promoted_repo}' -RequestedBinDir '{bin_dir}' "
        f"-CanonicalBinDir '{bin_dir}' -TrustedBoundary '{tmp_path}' "
        "-ManagedTaskNames @() | Out-Null"
    )

    assert completed.returncode != 0
    assert "NEWS_GRASP_NONCANONICAL_INSTALL_SOURCE" in completed.stderr


def test_install_guard_revalidates_generation_after_runtime_root_path_migration(tmp_path: Path) -> None:
    _, promoted_repo, bin_dir = _install_source_generation_fixture(tmp_path)
    runtime_root_path = bin_dir / "news-grasp-runtime-root-v1.json"
    runtime_root = json.loads(runtime_root_path.read_text(encoding="utf-8"))
    runtime_root["repoDir"] = str(promoted_repo)
    runtime_root_path.write_text(json.dumps(runtime_root), encoding="utf-8")
    (promoted_repo / "tracked.txt").write_text("drift after migration\n", encoding="utf-8")

    completed = _run_install_guard(
        "Assert-NewsGraspCanonicalInstallSource "
        f"-ResolvedRepoDir '{promoted_repo}' -RequestedBinDir '{bin_dir}' "
        f"-CanonicalBinDir '{bin_dir}' -TrustedBoundary '{tmp_path}' "
        "-ManagedTaskNames @() | Out-Null"
    )

    assert completed.returncode != 0
    assert "NEWS_GRASP_NONCANONICAL_INSTALL_SOURCE" in completed.stderr


def test_install_guard_rejects_assume_unchanged_tracked_payload(tmp_path: Path) -> None:
    _, promoted_repo, bin_dir = _install_source_generation_fixture(tmp_path)
    git = r"C:\Program Files\Git\cmd\git.exe"
    subprocess.run(
        [git, "-C", str(promoted_repo), "update-index", "--assume-unchanged", "tracked.txt"],
        check=True,
    )
    (promoted_repo / "tracked.txt").write_text("hidden drift\n", encoding="utf-8")

    completed = _run_install_guard(
        "Assert-NewsGraspCanonicalInstallSource "
        f"-ResolvedRepoDir '{promoted_repo}' -RequestedBinDir '{bin_dir}' "
        f"-CanonicalBinDir '{bin_dir}' -TrustedBoundary '{tmp_path}' "
        "-ManagedTaskNames @() | Out-Null"
    )

    assert completed.returncode != 0
    assert "NEWS_GRASP_NONCANONICAL_INSTALL_SOURCE" in completed.stderr


def test_install_guard_rejects_working_payload_hidden_by_inherited_global_clean_filter(
    tmp_path: Path,
) -> None:
    _, promoted_repo, bin_dir = _install_source_generation_fixture(tmp_path)
    git = r"C:\Program Files\Git\cmd\git.exe"
    poisoned_home = tmp_path / "poisoned-home"
    poisoned_home.mkdir()
    attributes_file = poisoned_home / "global-attributes"
    attributes_file.write_text("tracked.txt filter=restore-head\n", encoding="utf-8")
    filter_script = poisoned_home / "restore_head.py"
    filter_sentinel = poisoned_home / "filter-executed.txt"
    filter_script.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        f"Path({str(filter_sentinel)!r}).write_text('executed', encoding='utf-8')\n"
        "sys.stdin.buffer.read()\n"
        "sys.stdout.buffer.write(b'clean\\n')\n",
        encoding="utf-8",
    )
    poisoned_env = os.environ.copy()
    poisoned_env["HOME"] = str(poisoned_home)
    poisoned_env["XDG_CONFIG_HOME"] = str(poisoned_home / "xdg")
    filter_command = f'"{sys.executable}" "{filter_script}"'
    subprocess.run(
        [git, "config", "--global", "core.attributesFile", str(attributes_file)],
        check=True,
        env=poisoned_env,
    )
    subprocess.run(
        [git, "config", "--global", "filter.restore-head.clean", filter_command],
        check=True,
        env=poisoned_env,
    )
    (promoted_repo / "tracked.txt").write_text("installer payload drift\n", encoding="utf-8")

    completed = _run_install_guard(
        "Assert-NewsGraspCanonicalInstallSource "
        f"-ResolvedRepoDir '{promoted_repo}' -RequestedBinDir '{bin_dir}' "
        f"-CanonicalBinDir '{bin_dir}' -TrustedBoundary '{tmp_path}' "
        "-ManagedTaskNames @() | Out-Null",
        env=poisoned_env,
    )

    assert completed.returncode != 0
    assert "NEWS_GRASP_NONCANONICAL_INSTALL_SOURCE" in completed.stderr
    assert not filter_sentinel.exists()


def test_install_guard_rejects_staged_payload_hidden_by_repo_local_external_diff(
    tmp_path: Path,
) -> None:
    _, promoted_repo, bin_dir = _install_source_generation_fixture(tmp_path)
    git = r"C:\Program Files\Git\cmd\git.exe"
    diff_sentinel = tmp_path / "external-diff-executed.txt"
    diff_script = tmp_path / "external_diff.py"
    diff_script.write_text(
        "from pathlib import Path\n"
        f"Path({str(diff_sentinel)!r}).write_text('executed', encoding='utf-8')\n",
        encoding="utf-8",
    )
    diff_command = f'"{sys.executable}" "{diff_script}"'
    subprocess.run(
        [git, "-C", str(promoted_repo), "config", "diff.external", diff_command],
        check=True,
    )
    subprocess.run(
        [git, "-C", str(promoted_repo), "config", "diff.trustExitCode", "true"],
        check=True,
    )
    (promoted_repo / "tracked.txt").write_text("staged payload drift\n", encoding="utf-8")
    subprocess.run([git, "-C", str(promoted_repo), "add", "tracked.txt"], check=True)

    completed = _run_install_guard(
        "Assert-NewsGraspCanonicalInstallSource "
        f"-ResolvedRepoDir '{promoted_repo}' -RequestedBinDir '{bin_dir}' "
        f"-CanonicalBinDir '{bin_dir}' -TrustedBoundary '{tmp_path}' "
        "-ManagedTaskNames @() | Out-Null"
    )

    assert completed.returncode != 0
    assert "NEWS_GRASP_NONCANONICAL_INSTALL_SOURCE" in completed.stderr
    assert not diff_sentinel.exists()


def test_install_guard_rejects_staged_payload_hidden_by_replace_ref(tmp_path: Path) -> None:
    _, promoted_repo, bin_dir = _install_source_generation_fixture(tmp_path)
    git = r"C:\Program Files\Git\cmd\git.exe"
    head = subprocess.check_output(
        [git, "-C", str(promoted_repo), "rev-parse", "HEAD"],
        text=True,
        encoding="utf-8",
    ).strip()
    (promoted_repo / "tracked.txt").write_text("replace-ref payload drift\n", encoding="utf-8")
    subprocess.run([git, "-C", str(promoted_repo), "add", "tracked.txt"], check=True)
    tree = subprocess.check_output(
        [git, "-C", str(promoted_repo), "write-tree"],
        text=True,
        encoding="utf-8",
    ).strip()
    replacement_commit = subprocess.check_output(
        [git, "-C", str(promoted_repo), "commit-tree", tree, "-m", "replacement"],
        text=True,
        encoding="utf-8",
    ).strip()
    subprocess.run(
        [git, "-C", str(promoted_repo), "update-ref", f"refs/replace/{head}", replacement_commit],
        check=True,
    )

    completed = _run_install_guard(
        "Assert-NewsGraspCanonicalInstallSource "
        f"-ResolvedRepoDir '{promoted_repo}' -RequestedBinDir '{bin_dir}' "
        f"-CanonicalBinDir '{bin_dir}' -TrustedBoundary '{tmp_path}' "
        "-ManagedTaskNames @() | Out-Null"
    )

    assert completed.returncode != 0
    assert "NEWS_GRASP_NONCANONICAL_INSTALL_SOURCE" in completed.stderr


def test_install_guard_rejects_unrelated_repo_recreated_at_canonical_runtime_path(
    tmp_path: Path,
) -> None:
    canonical_repo, promoted_repo, bin_dir = _install_source_generation_fixture(tmp_path)
    git = r"C:\Program Files\Git\cmd\git.exe"
    runtime_root_path = bin_dir / "news-grasp-runtime-root-v1.json"
    runtime_root = json.loads(runtime_root_path.read_text(encoding="utf-8"))
    runtime_root["repoDir"] = str(promoted_repo)
    runtime_root_path.write_text(json.dumps(runtime_root), encoding="utf-8")
    subprocess.run(
        [git, "-C", str(canonical_repo), "worktree", "remove", "--force", str(promoted_repo)],
        check=True,
    )
    promoted_repo.mkdir()
    subprocess.run([git, "-C", str(promoted_repo), "init", "-b", "main"], check=True)
    subprocess.run([git, "-C", str(promoted_repo), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run([git, "-C", str(promoted_repo), "config", "user.name", "Unrelated Repo"], check=True)
    (promoted_repo / "tracked.txt").write_text("unrelated payload\n", encoding="utf-8")
    subprocess.run([git, "-C", str(promoted_repo), "add", "."], check=True)
    subprocess.run([git, "-C", str(promoted_repo), "commit", "-m", "unrelated"], check=True)
    subprocess.run(
        [git, "-C", str(promoted_repo), "update-ref", "refs/remotes/origin/main", "HEAD"],
        check=True,
    )

    completed = _run_install_guard(
        "Assert-NewsGraspCanonicalInstallSource "
        f"-ResolvedRepoDir '{promoted_repo}' -RequestedBinDir '{bin_dir}' "
        f"-CanonicalBinDir '{bin_dir}' -TrustedBoundary '{tmp_path}' "
        "-ManagedTaskNames @() | Out-Null"
    )

    assert completed.returncode != 0
    assert "NEWS_GRASP_NONCANONICAL_INSTALL_SOURCE" in completed.stderr


def test_install_guard_rejects_tracked_bytes_hidden_by_stat_cache(tmp_path: Path) -> None:
    _, promoted_repo, bin_dir = _install_source_generation_fixture(tmp_path)
    tracked = promoted_repo / "tracked.txt"
    original_stat = tracked.stat()
    tracked.write_bytes(b"drift\n")
    os.utime(tracked, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))

    completed = _run_install_guard(
        "Assert-NewsGraspCanonicalInstallSource "
        f"-ResolvedRepoDir '{promoted_repo}' -RequestedBinDir '{bin_dir}' "
        f"-CanonicalBinDir '{bin_dir}' -TrustedBoundary '{tmp_path}' "
        "-ManagedTaskNames @() | Out-Null"
    )

    assert completed.returncode != 0
    assert "NEWS_GRASP_NONCANONICAL_INSTALL_SOURCE" in completed.stderr


def test_install_guard_rejects_tracked_build_bytes_hidden_by_missing_working_diff(
    tmp_path: Path,
) -> None:
    _, promoted_repo, bin_dir = _install_source_generation_fixture(
        tmp_path, include_tracked_build=True
    )
    tracked_build = promoted_repo / "build" / "tracked-artifact.json"
    tracked_build.write_text("{\"generation\":\"drift\"}\n", encoding="utf-8")

    completed = _run_install_guard(
        "Assert-NewsGraspCanonicalInstallSource "
        f"-ResolvedRepoDir '{promoted_repo}' -RequestedBinDir '{bin_dir}' "
        f"-CanonicalBinDir '{bin_dir}' -TrustedBoundary '{tmp_path}' "
        "-ManagedTaskNames @() | Out-Null"
    )

    assert completed.returncode != 0
    assert "NEWS_GRASP_NONCANONICAL_INSTALL_SOURCE" in completed.stderr


def test_install_guard_bounds_promotable_source_tree_scan(tmp_path: Path) -> None:
    canonical_repo, promoted_repo, _ = _install_source_generation_fixture(tmp_path)
    build_root = promoted_repo / "build"
    build_root.mkdir()
    for index in range(4):
        (build_root / f"artifact-{index}.json").write_text("{}", encoding="utf-8")

    completed = _run_install_guard(
        "$result = Test-NewsGraspPromotableInstallSource "
        f"-CurrentRepoDir '{canonical_repo}' -CandidateRepoDir '{promoted_repo}' "
        f"-TrustedBoundary '{tmp_path}' -MaxEntries 3; "
        "if ($result) { throw 'PROMOTABLE_SOURCE_SCAN_LIMIT_NOT_ENFORCED' }"
    )

    assert completed.returncode == 0, completed.stderr


def test_install_guard_accepts_clean_generation_with_non_ascii_tracked_path(
    tmp_path: Path,
) -> None:
    """日本語Windowsのtracked pathをquotepath既定値で誤拒否しない。"""
    canonical_repo, promoted_repo, _ = _install_source_generation_fixture(tmp_path)
    git = r"C:\Program Files\Git\cmd\git.exe"
    localized = canonical_repo / "docs" / "NewsGrasp仕様.txt"
    localized.parent.mkdir()
    localized.write_text("clean\n", encoding="utf-8")
    subprocess.run([git, "-C", str(canonical_repo), "add", str(localized)], check=True)
    commit = subprocess.run(
        [git, "-C", str(canonical_repo), "commit", "-m", "localized tracked path"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert commit.returncode == 0
    head = subprocess.run(
        [git, "-C", str(canonical_repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()
    subprocess.run(
        [git, "-C", str(canonical_repo), "update-ref", "refs/remotes/origin/main", head],
        check=True,
    )
    subprocess.run([git, "-C", str(promoted_repo), "reset", "--hard", head], check=True)

    completed = _run_install_guard(
        "$result = Test-NewsGraspPromotableInstallSource "
        f"-CurrentRepoDir '{canonical_repo}' -CandidateRepoDir '{promoted_repo}' "
        f"-TrustedBoundary '{tmp_path}'; if (-not $result) {{ "
        "throw 'NON_ASCII_TRACKED_PATH_FALSE_NEGATIVE' }"
    )

    assert completed.returncode == 0, completed.stderr


def test_verified_source_reader_rejects_hardlinked_authority_file(tmp_path: Path) -> None:
    """source/hash/read は同一 handle へ束縛し、複数 hard-link の authority file を拒否する。"""
    source = tmp_path / "source.ps1"
    alias = tmp_path / "alias.ps1"
    source.write_bytes(b"trusted")
    os.link(source, alias)

    completed = _run_install_guard(
        "Read-NewsGraspVerifiedFile "
        f"-Path '{source}' -TrustedBoundary '{tmp_path}' -RequireSingleLink"
    )

    assert completed.returncode != 0
    assert "NEWS_GRASP_VERIFIED_SOURCE_HARDLINK_FORBIDDEN" in completed.stderr


def test_verified_source_reader_and_atomic_writer_reject_symlink_entries(tmp_path: Path) -> None:
    target = tmp_path / "target.ps1"
    source_link = tmp_path / "source-link.ps1"
    destination_link = tmp_path / "destination-link.ps1"
    target.write_bytes(b"unchanged")
    try:
        os.symlink(target, source_link)
        os.symlink(target, destination_link)
    except OSError as exc:
        target_dir = tmp_path / "junction-target"
        junction = tmp_path / "junction"
        target_dir.mkdir()
        target = target_dir / "target.ps1"
        target.write_bytes(b"unchanged")
        junction_result = subprocess.run(
            [
                POWERSHELL,
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                f"New-Item -ItemType Junction -Path '{junction}' -Target '{target_dir}' | Out-Null",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=30,
        )
        if junction_result.returncode != 0:
            pytest.skip(f"reparse fixture unavailable: {exc}; {junction_result.stderr}")
        source_link = junction / "target.ps1"
        destination_link = junction / "destination.ps1"

    source_result = _run_install_guard(
        "Read-NewsGraspVerifiedFile "
        f"-Path '{source_link}' -TrustedBoundary '{tmp_path}' -RequireSingleLink"
    )
    destination_result = _run_install_guard(
        "Write-NewsGraspAtomicFile "
        f"-Path '{destination_link}' -TrustedBoundary '{tmp_path}' "
        "-Bytes ([Text.Encoding]::UTF8.GetBytes('new'))"
    )

    assert source_result.returncode != 0
    assert "REPARSE_POINT_FORBIDDEN" in source_result.stderr
    assert destination_result.returncode != 0
    assert "REPARSE_POINT_FORBIDDEN" in destination_result.stderr
    assert target.read_bytes() == b"unchanged"


def test_atomic_install_replaces_hardlink_entry_without_mutating_sibling(tmp_path: Path) -> None:
    """live destination が hard-link でも entry を原子的に置換し、兄弟linkの内容を壊さない。"""
    destination = tmp_path / "runner.ps1"
    sibling = tmp_path / "runner-sibling.ps1"
    destination.write_bytes(b"old")
    os.link(destination, sibling)
    payload = "[Text.Encoding]::UTF8.GetBytes('new')"

    completed = _run_install_guard(
        "Write-NewsGraspAtomicFile "
        f"-Path '{destination}' -TrustedBoundary '{tmp_path}' -Bytes ({payload})"
    )

    assert completed.returncode == 0, completed.stderr
    assert destination.read_bytes() == b"new"
    assert sibling.read_bytes() == b"old"


def test_atomic_install_failure_preserves_old_destination(tmp_path: Path) -> None:
    """commit 境界が失敗しても旧 live file は truncate されず、temp も残らない。"""
    destination = tmp_path / "runner.ps1"
    destination.write_bytes(b"old")
    command = (
        f"$held=[IO.File]::Open('{destination}',[IO.FileMode]::Open,"
        "[IO.FileAccess]::Read,[IO.FileShare]::Read); "
        "try { Write-NewsGraspAtomicFile "
        f"-Path '{destination}' -TrustedBoundary '{tmp_path}' "
        "-Bytes ([Text.Encoding]::UTF8.GetBytes('new')) } finally { $held.Dispose() }"
    )

    completed = _run_install_guard(command)

    assert completed.returncode != 0
    assert "NEWS_GRASP_ATOMIC_COMMIT_FAILED" in completed.stderr
    assert destination.read_bytes() == b"old"
    assert not list(tmp_path.glob(".news-grasp-install-*.tmp"))


def test_atomic_install_supports_long_destination_with_longer_temp_name(tmp_path: Path) -> None:
    """destinationはMAX_PATH未満でもtemp名で超過するbackupを同じatomic境界で扱う。"""
    temp_name = ".news-grasp-install-" + ("0" * 32) + ".tmp"
    fixture_root = ROOT / "build" / "live-runner-backups"
    suffix = Path("news-grasp-assets") / "skills" / "news-grasp-e2e-discipline" / "agents"
    minimum = fixture_root / "t" / suffix / temp_name
    padding_length = 260 - len(str(minimum))
    assert padding_length > 0
    transaction = "t" * (padding_length + 1)
    transaction_root = fixture_root / transaction
    parent = transaction_root / suffix
    fixture_root.mkdir(parents=True, exist_ok=True)
    destination = parent / "receipt.json"
    projected_temp = parent / temp_name
    assert len(str(destination)) < 260
    assert len(str(projected_temp)) == 260

    try:
        completed = _run_install_guard(
            f"New-Item -ItemType Directory -Force -Path '{parent}' | Out-Null; "
            "Write-NewsGraspAtomicFile "
            f"-Path '{destination}' -TrustedBoundary '{fixture_root}' "
            "-Bytes ([Text.Encoding]::UTF8.GetBytes('long-path-green'))",
            powershell_executable="pwsh",
        )

        assert completed.returncode == 0, (
            "NGI_RED_INSTALLER_LONG_PATH_ATOMIC_TEMP_CREATE_FAILED\n"
            + completed.stdout
            + completed.stderr
        )
        assert destination.read_bytes() == b"long-path-green"
        assert not list(parent.glob(".news-grasp-install-*.tmp"))
    finally:
        shutil.rmtree(transaction_root, ignore_errors=True)


def test_atomic_temp_create_failure_preserves_win32_cause_code() -> None:
    """外部一時失敗を同一shape retryせず分類できるようWin32 causeを保持する。"""
    boundary = (OPS_DIR / "install-news-grasp-verified-file-boundary.ps1").read_text(
        encoding="utf-8-sig"
    )
    create_failure = boundary.split("if (temporaryHandle.IsInvalid)", 1)[1].split(
        "try", 1
    )[0]

    assert "Marshal.GetLastWin32Error()" in create_failure
    assert "NEWS_GRASP_ATOMIC_TEMP_CREATE_FAILED:" in create_failure, (
        "NGI_RED_INSTALLER_ATOMIC_CAUSE_CODE_DROPPED"
    )


def test_verified_rollback_restore_and_delete_do_not_mutate_hardlink_sibling(tmp_path: Path) -> None:
    backup = tmp_path / "backup.ps1"
    destination = tmp_path / "runner.ps1"
    sibling = tmp_path / "runner-sibling.ps1"
    backup.write_bytes(b"backup")
    destination.write_bytes(b"old")
    os.link(destination, sibling)

    restored = _run_install_guard(
        "Restore-NewsGraspVerifiedFile "
        f"-BackupPath '{backup}' -DestinationPath '{destination}' "
        f"-BackupBoundary '{tmp_path}' -DestinationBoundary '{tmp_path}'"
    )

    assert restored.returncode == 0, restored.stderr
    assert destination.read_bytes() == b"backup"
    assert sibling.read_bytes() == b"old"

    removed = _run_install_guard(
        f"Remove-NewsGraspVerifiedFile -Path '{destination}' -TrustedBoundary '{tmp_path}'"
    )

    assert removed.returncode == 0, removed.stderr
    assert not destination.exists()
    assert sibling.read_bytes() == b"old"


def test_install_and_rollback_use_one_verified_atomic_file_boundary() -> None:
    """forward/recovery/rollback の file mutation を同じ verified boundary に集約する。"""
    installer = (OPS_DIR / "install-news-grasp-ops.ps1").read_text(encoding="utf-8-sig")
    boundary = (OPS_DIR / "install-news-grasp-verified-file-boundary.ps1").read_text(
        encoding="utf-8-sig"
    )
    recovery = installer.split("function Invoke-NewsGraspRollbackJournal", 1)[1].split(
        "function Recover-NewsGraspInterruptedInstall", 1
    )[0]
    rollback = installer.split("function Invoke-NewsGraspInstallRollback", 1)[1].split(
        "function Write-NewsGraspInstallJournal", 1
    )[0]
    install = installer.split("foreach ($file in $files) {", 2)[2].split(
        "$runtimePythonPath", 1
    )[0]

    for block in (recovery, rollback):
        assert "Restore-NewsGraspVerifiedFile" in block
        assert "Remove-NewsGraspVerifiedFile" in block
        assert "Copy-Item -LiteralPath ([string]$row.backup)" not in block
        assert "Remove-Item -LiteralPath ([string]$row.destination)" not in block
    assert "Read-NewsGraspVerifiedFile" in install
    assert "Write-NewsGraspAtomicFile" in install
    assert "Copy-Item -LiteralPath $source -Destination $destination -Force" not in install
    assert "NtSetInformationFile" in boundary
    assert "FILE_RENAME_INFORMATION_CLASS = 10" in boundary
    assert "GetFinalPath(temporaryHandle)" in boundary
    assert boundary.index("renamed = true") < boundary.index(
        "GetFinalPath(temporaryHandle)", boundary.index("renamed = true")
    )
    assert "if (!renamed)" in boundary
    assert "if (!committed)" not in boundary
    assert "ReplaceFileW" not in boundary
    assert "MoveFileExW" not in boundary


def test_recovery_journal_ingestion_is_handle_bound_and_complete_set_required() -> None:
    installer = (OPS_DIR / "install-news-grasp-ops.ps1").read_text(encoding="utf-8-sig")
    guard = (OPS_DIR / "install-news-grasp-ops-guard.ps1").read_text(encoding="utf-8-sig")
    recovery = installer.split("function Recover-NewsGraspInterruptedInstall", 1)[1].split(
        "function Invoke-NewsGraspInstallRollback", 1
    )[0]

    assert "Read-NewsGraspVerifiedFile" in recovery
    assert "-RequireSingleLink" in recovery
    assert "Get-Content -LiteralPath $journal" not in recovery
    assert "NEWS_GRASP_INSTALL_JOURNAL_FILE_SET_INVALID" in guard
    assert "$seenFiles.Count -ne $allowedFiles.Count" in guard
    assert "NEWS_GRASP_INSTALL_JOURNAL_LIVE_STATE_DRIFT" in guard


def test_recovery_journal_missing_managed_rows_cannot_delete_live_file(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    ops = repo / "scripts" / "ops"
    ops.mkdir(parents=True)
    source = ops / "news-grasp-runner.ps1"
    source.write_bytes(b"canonical")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    destination = bin_dir / "news-grasp-runner.ps1"
    destination.write_bytes(b"canonical")
    backup_root = tmp_path / "backups"
    transaction_dir = backup_root / "20260809-130000"
    transaction_dir.mkdir(parents=True)
    journal_path = transaction_dir / "install-manifest.json"
    journal = {
        "schemaVersion": "NEWS_GRASP_OPS_INSTALL_JOURNAL_V1",
        "transaction_id": "20260809-130000",
        "phase": "files_installed",
        "updated_at": "2026-08-09T13:00:00+09:00",
        "repo_dir": str(repo),
        "bin_dir": str(bin_dir),
        "task_pythonw_path": str(tmp_path / "pythonw.exe"),
        "bin_dir_existed_before": True,
        "backup_dir": str(transaction_dir),
        "files": [
            {
                "file": "news-grasp-runner.ps1",
                "source": str(source),
                "destination": str(destination),
                "backup": "",
                "before_sha256": "",
                "source_sha256": source_hash,
                "after_sha256": source_hash,
            }
        ],
        "rollback_commands": ["Invoke-NewsGraspInstallRollback"],
        "mission_authority": {
            "path": "",
            "sha256": "",
            "schema": "AUDIT_MISSION_AUTHORITY_V1",
        },
        "scheduled_tasks": [],
        "task_snapshots": [],
    }
    journal_path.write_text(json.dumps(journal), encoding="utf-8")
    command = (
        f"$journal=Get-Content -LiteralPath '{journal_path}' -Raw -Encoding UTF8 | ConvertFrom-Json; "
        "Assert-NewsGraspRecoveryJournal "
        f"-JournalPath '{journal_path}' -Journal $journal "
        f"-ExpectedBackupRoot '{backup_root}' -ExpectedRepoDir '{repo}' "
        f"-ExpectedBinDir '{bin_dir}' -ExpectedTaskNames @('News-Grasp Production')"
    )

    completed = _run_install_guard(command)

    assert completed.returncode != 0
    assert "NEWS_GRASP_INSTALL_JOURNAL_FILE_SET_INVALID" in completed.stderr
    assert destination.read_bytes() == b"canonical"


def test_task_xml_backup_hash_is_checked_again_immediately_before_privileged_restore(
    tmp_path: Path,
) -> None:
    installer = (OPS_DIR / "install-news-grasp-ops.ps1").read_text(encoding="utf-8-sig")
    guard = (OPS_DIR / "install-news-grasp-ops-guard.ps1").read_text(encoding="utf-8-sig")
    xml_path = tmp_path / "task.xml"
    original = "<Task><Actions /></Task>".encode("utf-16-le")
    xml_path.write_bytes(original)
    expected_hash = hashlib.sha256(original).hexdigest()
    xml_path.write_bytes("<Task><Exec>attacker</Exec></Task>".encode("utf-16-le"))

    completed = _run_install_guard(
        "Read-NewsGraspVerifiedTaskXml "
        f"-Path '{xml_path}' -TrustedBoundary '{tmp_path}' "
        f"-ExpectedSha256 '{expected_hash}'"
    )

    assert completed.returncode != 0
    assert "NEWS_GRASP_INSTALL_JOURNAL_TASK_XML_DRIFT" in completed.stderr
    assert "xml_backup_sha256" in installer
    assert "xml_backup_sha256" in guard
    for marker in (
        "function Invoke-NewsGraspRollbackJournal",
        "function Invoke-NewsGraspInstallRollback",
    ):
        rollback = installer.split(marker, 1)[1]
        assert "Read-NewsGraspVerifiedTaskXml" in rollback
        assert "-ExpectedSha256 ([string]$snapshot.xml_backup_sha256)" in rollback


def test_nopublish_parent_authority_input_survives_script_scope_projection_reset() -> None:
    runner = (OPS_DIR / "news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    assert "$incomingHighCostParentAuthorityPath = [string]$HighCostParentAuthorityPath" in runner
    assert "if (-not $incomingHighCostParentAuthorityPath)" in runner
    assert "GetFullPath($incomingHighCostParentAuthorityPath)" in runner


def test_bootstrap_runtime_convergence_failure_is_preserved_as_typed_evidence() -> None:
    """runtime forward recovery の失敗理由を exit 72 へ潰さない。"""
    bootstrap = (OPS_DIR / "news-grasp-bootstrap.ps1").read_text(encoding="utf-8-sig")
    runtime_catch = bootstrap.split("Resolve-ProductionRuntimeRepo -SourceRepoDir", 1)[1].split(
        "$opsDir = Join-Path $RepoDir 'scripts\\ops'", 1
    )[0]

    assert "Write-BootstrapFailureObservation" in runtime_catch
    assert "-Phase 'runtime_convergence'" in runtime_catch
    assert "-ReasonCode 'PRODUCTION_RUNTIME_CONVERGENCE_FAILED'" in runtime_catch
    assert "-Detail $_.Exception.Message" in runtime_catch
    assert "exit 72" in runtime_catch


def test_rejected_direct_bootstrap_context_does_not_claim_scheduled_failure() -> None:
    """task context を偽装したdirect起動はscheduled attempt lineageを消費しない。"""
    bootstrap = (OPS_DIR / "news-grasp-bootstrap.ps1").read_text(encoding="utf-8-sig")
    launcher = (OPS_DIR / "news-grasp-task-launcher.pyw").read_text(encoding="utf-8-sig")

    assert "NEWS_GRASP_TASK_CONTEXT_REJECTED_EXIT = 67" in launcher
    assert "SCHEDULED_TASK_CONTEXT_REJECTED_EXIT" in bootstrap
    assert "exit $SCHEDULED_TASK_CONTEXT_REJECTED_EXIT" in bootstrap
    assert "context_rejected =" in launcher
    assert "and not context_rejected" in launcher
    assert '"context_rejected_no_attempt"' in launcher
    assert '"scheduledRecoveryFullAuthorityProvable": (' in launcher
    assert "effective_returncode != 0 and not context_rejected" in launcher


def test_sec_runner_validates_scheduled_admission_with_product_local_canonical_validator_before_copy() -> None:
    """既存admissionの再利用/搬送前にproduct-local canonical validatorを通す。"""

    runner = RUNNER_PS1.read_text(encoding="utf-8-sig")
    validator_marker = "validate-scheduled-admission"
    assert validator_marker in runner
    validator_index = runner.index(validator_marker)
    copy_index = runner.index("WriteAllText($admissionReceipt")
    assert validator_index < copy_index


def test_sec_scheduled_admission_validator_is_closed_schema_and_receipt_sealed() -> None:
    """canonical helperはclosed schemaとreceiptSha256を同時に検証する。"""

    contracts = __import__("tools.news_grasp_operational_contract", fromlist=["*"])
    validator = getattr(contracts, "validate_scheduled_admission_receipt", None)
    assert callable(validator)
    source = inspect.getsource(validator)
    body_validator = getattr(contracts, "_validate_scheduled_admission_body", None)
    assert callable(body_validator)
    body_source = inspect.getsource(body_validator)
    invalid_source = inspect.getsource(contracts._scheduled_admission_invalid)
    assert "_validate_scheduled_admission_body" in source
    assert "receiptSha256" in body_source
    assert "schemaVersion" in body_source
    assert "_scheduled_admission_invalid" in body_source
    assert "HIGH_COST_SCHEDULED_ADMISSION_INVALID" in invalid_source
