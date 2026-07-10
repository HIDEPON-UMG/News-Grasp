#!/usr/bin/env python3
"""news-grasp-runner.ps1 Stage2 -> editor 初動の部分 E2E。

当日必須カテゴリだけの reporter 並列 fan-out が完走し、editor が schedule 由来の
成果物 manifest を読めるところまでを fake Codex wrapper で決定論的に検証する。
"""
from __future__ import annotations

import os
import json
import shutil
import subprocess
import sys
from pathlib import Path

from tools.publish_inventory import scheduled_category_ids

ROOT = Path(__file__).resolve().parent.parent
RUNNER = Path(os.environ.get("NEWS_GRASP_RUNNER", str(ROOT / "scripts" / "ops" / "news-grasp-runner.ps1")))
POWERSHELL = os.environ.get("NEWS_GRASP_POWERSHELL", "powershell")
ISSUE = "2026-06-14"
CATEGORIES = tuple(scheduled_category_ids(ISSUE))


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
        "publish_inventory.py",
        "url_quality.py",
        "validate_record.py",
        "verify_reporter_output.py",
    ]:
        shutil.copy2(ROOT / "tools" / name, dst / "tools" / name)
    # semantic details are covered by test_validate_editor_output_preview.py;
    # this isolated runner fixture only proves the pre-materialization call boundary.
    (dst / "tools" / "validate_editor_output_preview.py").write_text(
        "import argparse\nfrom pathlib import Path\n"
        "p=argparse.ArgumentParser(); p.add_argument('preview', type=Path); p.add_argument('--date', required=True); a=p.parse_args(); raise SystemExit(0 if a.preview.exists() else 1)\n",
        encoding="utf-8",
    )
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
    """reporter 成果物を書き、editor で schedule 由来 manifest 件数を検査する wrapper。"""
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
$expectedReporterArtifacts = [int]$env:NEWS_GRASP_E2E_EXPECTED_REPORTERS
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

function Add-TraceLine {
    param([string]$Value)
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Value + [Environment]::NewLine)
    for ($attempt = 0; $attempt -lt 50; $attempt++) {
        try {
            $stream = [System.IO.File]::Open($trace, [System.IO.FileMode]::Append, [System.IO.FileAccess]::Write, [System.IO.FileShare]::Read)
            try {
                $stream.Write($bytes, 0, $bytes.Length)
            } finally {
                $stream.Dispose()
            }
            return
        } catch {
            Start-Sleep -Milliseconds 20
        }
    }
    throw "failed to append trace line: $Value"
}

Add-TraceLine "wrapper START $FlowName"
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
            published_date = $date
            date_evidence_source = "fixture"
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
    Add-TraceLine "wrapper END $FlowName"
    exit 0
}

if ($FlowName -eq 'newsroom_editor') {
    $prompt = Get-Content -LiteralPath $PromptFile -Raw -Encoding UTF8
    if ($prompt -notmatch 'manifest は (.+?) にある') {
        throw "editor prompt did not expose manifest path"
    }
    $manifestPath = $Matches[1].Trim()
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if (@($manifest.reporter_artifacts).Count -ne $expectedReporterArtifacts) {
        throw "reporter_artifacts count was $(@($manifest.reporter_artifacts).Count)"
    }
    if (@($manifest.reporter_artifact_details).Count -ne $expectedReporterArtifacts) {
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
            summary_markdown = "## § 本日のテーマ考察`n`n> $(('主要カテゴリを横断し、企業戦略と技術投資の接点を整理する。' * 8))`n`n### §01 FX — 為替市場の政策変化を追う`n`n- 【事実・概要】：市場変化。`n- 【背景・要点】：政策背景。`n- 【影響・展望】：判断材料。`n`n### §02 AI — 企業AI投資の責任を問う`n`n- 【事実・概要】：導入拡大。`n- 【背景・要点】：契約責任。`n- 【影響・展望】：監査強化。`n`n### §03 IT — 実装と運用責任を整理する`n`n- 【事実・概要】：実装進展。`n- 【背景・要点】：運用設計。`n- 【影響・展望】：責任分担。`n`n### §04 Mobility — 移動サービスの安全を測る`n`n- 【事実・概要】：都市展開。`n- 【背景・要点】：安全運用。`n- 【影響・展望】：規制対応。`n`n### §05 Game — 利用接点と継続性を考える`n`n- 【事実・概要】：接点拡大。`n- 【背景・要点】：利用導線。`n- 【影響・展望】：継続評価。"
        })
    }
    Add-TraceLine "wrapper END $FlowName"
    exit 0
}

throw "unexpected FlowName: $FlowName"
'''.lstrip(),
        encoding="utf-8-sig",
    )


def test_stage2_parallel_reporters_finish_and_editor_reads_all_artifacts(tmp_path: Path) -> None:
    """当日必須 reporter 完走後、editor が schedule 由来 manifest を読める。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    _copy_minimal_repo(repo)
    wrapper = tmp_path / "fake_codex_wrapper.ps1"
    trace = tmp_path / "trace.log"
    sentinel = tmp_path / "editor-sentinel.json"
    log_dir = tmp_path / "runner-logs"
    state_file = tmp_path / "runner-state.json"
    _fake_codex_wrapper(wrapper)

    env = os.environ.copy()
    env["NEWS_GRASP_E2E_DATE"] = ISSUE
    env["NEWS_GRASP_E2E_TRACE"] = str(trace)
    env["NEWS_GRASP_E2E_SENTINEL"] = str(sentinel)
    env["NEWS_GRASP_E2E_EXPECTED_REPORTERS"] = str(len(CATEGORIES))
    root_last_message = ROOT / "build" / "codex-last-message.txt"
    root_last_message_backup = tmp_path / "root-codex-last-message.txt"
    if root_last_message.exists():
        shutil.move(str(root_last_message), str(root_last_message_backup))
    root_last_message_written_during_test = False
    try:
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
                "-LogDirOverride",
                str(log_dir),
                "-StateFileOverride",
                str(state_file),
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
        root_last_message_written_during_test = root_last_message.exists()
    finally:
        if root_last_message_backup.exists():
            root_last_message.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(root_last_message_backup), str(root_last_message))

    assert result.returncode == 0, result.stdout + result.stderr
    trace_text = trace.read_text(encoding="utf-8")
    expected_count = len(CATEGORIES)
    assert trace_text.count("wrapper START reporter:") == expected_count
    assert trace_text.count("wrapper END reporter:") == expected_count
    assert "wrapper START newsroom_editor" in trace_text
    first_parent_end = result.stdout.index("reporter job END")
    assert result.stdout[:first_parent_end].count("reporter job START") == expected_count

    sentinel_text = sentinel.read_text(encoding="utf-8")
    assert '"editor_started":  true' in sentinel_text or '"editor_started": true' in sentinel_text
    assert sentinel_text.count(".records.jsonl") == expected_count
    manifest = repo / "build" / "reporter-artifacts" / ISSUE / "editor-input-manifest.json"
    assert manifest.exists()
    assert (repo / "build" / "codex-last-message.txt").exists()
    assert "## § 本日のテーマ考察" in (repo / "digest" / "Summary" / f"{ISSUE}.md").read_text(encoding="utf-8-sig")
    articles = (repo / "data" / "articles.jsonl").read_text(encoding="utf-8")
    assert '"genre":"Summary"' in articles
    assert not root_last_message_written_during_test
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["status"] == "smoke_ok"
    assert state["repo_dir"] == str(repo)
    assert state["log_path"] == str(log_dir / f"{ISSUE}.log")


def test_runner_validates_editor_preview_before_materialization() -> None:
    """Semantic Red の editor JSON を Summary/articles へ反映してはならない。"""
    runner = RUNNER.read_text(encoding="utf-8-sig")
    success_block = runner.split("if ($agentRc -eq 0)", 1)[1].split("if ($agentRc -eq 124)", 1)[0]

    validate = "tools.validate_editor_output_preview"
    sync = "Sync-EditorOutputPreview -PreviewPath $editorOutputPreview -FallbackPath $CodexLastMessage\n"
    assert validate in success_block
    assert success_block.index(validate) < success_block.index(sync)
    assert "editor preview semantic validation failed" in success_block
