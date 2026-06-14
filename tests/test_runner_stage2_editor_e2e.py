#!/usr/bin/env python3
"""news-grasp-runner.ps1 Stage2 -> editor 初動の部分 E2E。

7 カテゴリ reporter の並列 fan-out が完走し、editor が 7 成果物の manifest を
読めるところまでを fake Codex wrapper で決定論的に検証する。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

RUNNER = Path(os.environ.get("NEWS_GRASP_RUNNER", str(Path.home() / "bin" / "news-grasp-runner.ps1")))
POWERSHELL = os.environ.get("NEWS_GRASP_POWERSHELL", "powershell")
ROOT = Path(__file__).resolve().parent.parent
ISSUE = "2026-06-14"
CATEGORIES = ("fx", "ai", "it", "mobility", "manufacturing", "economy", "game")


def _copy_minimal_repo(dst: Path) -> None:
    """runner Stage2/3 smoke に必要な最小 repo 構造を作る。"""
    (dst / ".git").mkdir(parents=True)
    for rel in [
        "schemas",
        "prompts",
    ]:
        shutil.copytree(ROOT / rel, dst / rel)
    (dst / "tools").mkdir()
    for name in [
        "model_policy.py",
        "validate_record.py",
        "verify_reporter_output.py",
    ]:
        shutil.copy2(ROOT / "tools" / name, dst / "tools" / name)
    (dst / "data").mkdir()
    (dst / "data" / "articles.jsonl").write_text("", encoding="utf-8")
    dedup_dir = dst / "build" / "deduped-candidates"
    dedup_dir.mkdir(parents=True)
    for cat in CATEGORIES:
        (dedup_dir / f"{cat}.jsonl").write_text(
            f'{{"category":"{cat}","title":"candidate {cat}"}}\n',
            encoding="utf-8",
        )


def _fake_codex_wrapper(path: Path) -> None:
    """reporter 成果物を書き、editor で manifest 7 件を検査する wrapper。"""
    path.write_text(
        r'''
param(
    [string] $CodexExe,
    [string] $PromptFile,
    [string] $LogFile,
    [int] $TimeoutSec,
    [int] $IdleTimeoutSec,
    [string] $WorkingDirectory,
    [string] $OutputSchema = '',
    [string] $OutputLastMessage = '',
    [string] $Model = '',
    [string] $FlowName = 'unknown',
    [string] $UsageLog = ''
)

$ErrorActionPreference = 'Stop'
$date = $env:NEWS_GRASP_E2E_DATE
$trace = $env:NEWS_GRASP_E2E_TRACE
$sentinel = $env:NEWS_GRASP_E2E_SENTINEL
$genreMap = @{
    fx = 'FX'
    ai = 'AI'
    it = 'IT-Consulting'
    mobility = 'Mobility'
    manufacturing = 'Manufacturing'
    economy = 'Economy'
    game = 'Game'
}

function Write-JsonFile {
    param([string]$Path, [object]$Value)
    $dir = Split-Path -Parent $Path
    if ($dir) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $Value | ConvertTo-Json -Depth 8 | Set-Content -Path $Path -Encoding UTF8
}

Add-Content -Path $trace -Value "wrapper START $FlowName"
if ($UsageLog) {
    $usageDir = Split-Path -Parent $UsageLog
    if ($usageDir) { New-Item -ItemType Directory -Path $usageDir -Force | Out-Null }
    Add-Content -Path $UsageLog -Value ('{"flow":"' + $FlowName + '","tokens_used":1}') -Encoding UTF8
}

if ($FlowName.StartsWith('reporter:')) {
    $cat = $FlowName.Substring('reporter:'.Length)
    Start-Sleep -Milliseconds 300
    $genre = $genreMap[$cat]
    $recordsDir = Join-Path $WorkingDirectory "tmp\newsroom\$date"
    $auditDir = Join-Path $WorkingDirectory "data\search_audit\$date"
    $digestDir = Join-Path $WorkingDirectory "digest\$genre"
    New-Item -ItemType Directory -Path $recordsDir,$auditDir,$digestDir -Force | Out-Null
    $recordsPath = Join-Path $recordsDir "$cat.records.jsonl"
    $digestPath = Join-Path $digestDir "$date-$genre.md"
    $auditPath = Join-Path $auditDir "$cat.json"

    $lines = @()
    $cards = @("---", "title: Smoke $genre", "date: $date", "category: $genre", "categoryId: $cat", "---", "")
    for ($i = 1; $i -le 5; $i++) {
        $rec = [ordered]@{
            date = $date
            seen_at = "${date}T06:00:00+09:00"
            genre = $genre
            title = "Smoke $cat story $i"
            title_ja = "Smoke $cat story $i ja"
            url = "https://example.com/$cat/story-$i"
            thumb = "https://example.com/$cat/thumb-$i.png"
        }
        $lines += ($rec | ConvertTo-Json -Compress)
        $cards += "### [88] Smoke $cat story $i"
        $cards += ""
        $cards += "![thumb](https://example.com/$cat/thumb-$i.png)"
        $cards += ""
        $cards += "- smoke bullet $i"
        $cards += ""
    }
    Set-Content -Path $recordsPath -Value $lines -Encoding UTF8
    Set-Content -Path $digestPath -Value ($cards -join "`n") -Encoding UTF8
    Write-JsonFile -Path $auditPath -Value ([ordered]@{
        date = $date
        category_id = $cat
        queries = @("smoke")
        raw_results_total = 5
        candidates_total = 5
        selected_total = 5
    })
    if ($OutputLastMessage) {
        Write-JsonFile -Path $OutputLastMessage -Value ([ordered]@{
            category = $cat
            issue_date = $date
            records_file = "tmp/newsroom/$date/$cat.records.jsonl"
            digest_file = "digest/$genre/$date-$genre.md"
            search_audit = "data/search_audit/$date/$cat.json"
            selected_count = 5
            titles = @("Smoke $cat story 1")
            quality_shortfall_reasons = @()
        })
    }
    Add-Content -Path $trace -Value "wrapper END $FlowName"
    exit 0
}

if ($FlowName -eq 'newsroom_editor') {
    $prompt = Get-Content -LiteralPath $PromptFile -Raw -Encoding UTF8
    if ($prompt -notmatch 'manifest は (.+?) にある') {
        throw "editor prompt did not expose manifest path"
    }
    $manifestPath = $Matches[1].Trim()
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if (@($manifest.reporter_artifacts).Count -ne 7) {
        throw "reporter_artifacts count was $(@($manifest.reporter_artifacts).Count)"
    }
    if (@($manifest.reporter_artifact_details).Count -ne 7) {
        throw "reporter_artifact_details count was $(@($manifest.reporter_artifact_details).Count)"
    }
    foreach ($rel in @($manifest.reporter_artifacts)) {
        $full = Join-Path $WorkingDirectory $rel
        if (-not (Test-Path $full)) {
            throw "missing reporter artifact: $rel"
        }
    }
    Write-JsonFile -Path $sentinel -Value ([ordered]@{
        editor_started = $true
        reporter_artifacts = @($manifest.reporter_artifacts)
        source_policy = $manifest.source_policy
    })
    if ($OutputLastMessage) {
        Write-JsonFile -Path $OutputLastMessage -Value ([ordered]@{
            issue_date = $date
            inputs = [ordered]@{
                reporter_artifacts = @($manifest.reporter_artifacts)
                dedup_file = [string]$manifest.dedup_file
                source_policy = [string]$manifest.source_policy
            }
            append_records = @([ordered]@{
                date = $date
                genre = "Summary"
                title = "Smoke editor summary"
                title_ja = "Smoke editor summary ja"
                url = "https://example.com/summary"
                source = "Example"
                summary = "Smoke summary"
                bullets = @("Smoke bullet")
            })
            summary_markdown = "Smoke summary"
        })
    }
    Add-Content -Path $trace -Value "wrapper END $FlowName"
    exit 0
}

throw "unexpected FlowName: $FlowName"
'''.lstrip(),
        encoding="utf-8-sig",
    )


def test_stage2_parallel_reporters_finish_and_editor_reads_all_artifacts(tmp_path: Path) -> None:
    """7 reporter 完走後、editor が manifest 経由で 7 成果物を読める。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    _copy_minimal_repo(repo)
    wrapper = tmp_path / "fake_codex_wrapper.ps1"
    trace = tmp_path / "trace.log"
    sentinel = tmp_path / "editor-sentinel.json"
    _fake_codex_wrapper(wrapper)

    env = os.environ.copy()
    env["NEWS_GRASP_E2E_DATE"] = ISSUE
    env["NEWS_GRASP_E2E_TRACE"] = str(trace)
    env["NEWS_GRASP_E2E_SENTINEL"] = str(sentinel)
    result = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(RUNNER),
            "-Stage2EditorSmokeOnly",
            "-StopAfterEditorStart",
            "-NoPush",
            "-RepoDirOverride",
            str(repo),
            "-CodexWrapperOverride",
            str(wrapper),
            "-CodexExeOverride",
            str(tmp_path / "fake_codex.cmd"),
            "-PyExeOverride",
            sys.executable,
            "-DateStampOverride",
            ISSUE,
            "-IdleTimeoutSec",
            "30",
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    trace_text = trace.read_text(encoding="utf-8")
    assert trace_text.count("wrapper START reporter:") == 7
    assert trace_text.count("wrapper END reporter:") == 7
    assert "wrapper START newsroom_editor" in trace_text
    first_parent_end = result.stdout.index("reporter job END")
    assert result.stdout[:first_parent_end].count("reporter job START") == 7

    sentinel_text = sentinel.read_text(encoding="utf-8")
    assert '"editor_started":  true' in sentinel_text or '"editor_started": true' in sentinel_text
    assert sentinel_text.count(".records.jsonl") == 7
    manifest = repo / "build" / "reporter-artifacts" / ISSUE / "editor-input-manifest.json"
    assert manifest.exists()
    assert (repo / "build" / "codex-last-message.txt").exists()
    assert not (ROOT / "build" / "codex-last-message.txt").exists()
