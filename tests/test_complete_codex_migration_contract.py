#!/usr/bin/env python3
"""News-Grasp 完全 Codex 移行の Acceptance Matrix 契約テスト。"""
from __future__ import annotations

import os
import re
from pathlib import Path

from tools.run_model_eval import VARIANTS

ROOT = Path(__file__).resolve().parent.parent
RUNNER = Path(os.environ.get("NEWS_GRASP_RUNNER", str(Path.home() / "bin" / "news-grasp-runner.ps1")))


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def test_runner_has_no_claude_execution_path_and_uses_model_policy() -> None:
    runner = _read(RUNNER)

    forbidden = [
        "UseClaude",
        "ClaudeExe",
        "run_claude_with_timeout.ps1",
        "-ClaudeExe",
        "agent=claude",
        "sonnet",
        "opus",
    ]
    for needle in forbidden:
        assert needle not in runner
    assert "run_codex_with_timeout.ps1" in runner
    assert "model_policy.py" in runner
    assert "gpt-5.4-mini" not in runner
    assert "gpt-5.4'" not in runner


def test_runner_main_codex_call_uses_newsroom_editor_model_policy() -> None:
    """runner-prompt/newsroom-editor-system を読む単一 Codex 呼び出しは編集長モデルを使う。"""
    runner = _read(RUNNER)
    stage = runner.split("$MaxAgentAttempts = 3", 1)[1].split("if ($agentRc -eq 124)", 1)[0]
    assert "Get-ModelPolicyValue -Role 'newsroom_editor' -Key 'default'" in stage
    assert "Get-ModelPolicyValue -Role 'reporter' -Key 'default'" not in stage
    assert "$NewsroomEditorModel" in stage


def test_runner_physically_fans_out_reporters_before_editor_integration() -> None:
    """Stage2 はコメント上の「相当」ではなくカテゴリ別 Codex 成果物を生成する。"""
    runner = _read(RUNNER)
    stage = runner.split("Stage2 reporter fan-out", 1)[1].split("Stage4: Codex DeepDive", 1)[0]

    assert "prompts\\newsroom-reporter-system.md" in stage
    assert "schemas\\reporter_fanout_return.schema.json" in stage
    assert "schemas\\reporter_records.schema.json" in stage
    assert "$ReporterArtifactDir" in stage
    assert "$ReporterModel = Get-ModelPolicyValue -Role 'reporter' -Key 'default'" in stage
    assert "role=reporter" in stage
    assert "tools.verify_reporter_output" in stage
    assert "$MaxParallelReporterJobs = 7" in runner
    assert "Start-Job" in stage
    assert "Wait-Job" in stage
    assert "Receive-Job" in stage
    assert "reporter job START" in stage
    assert "reporter job END" in stage
    assert "foreach ($cat in $Categories)" not in stage


def test_runner_retries_only_failed_reporters_with_signature_gate() -> None:
    """Stage2 retry は失敗カテゴリだけに限定し、同一 failure signature の焼き直しを止める。"""
    runner = _read(RUNNER)
    stage = runner.split("Stage2 reporter fan-out", 1)[1].split("Stage4: Codex DeepDive", 1)[0]

    assert "$ReporterMaxAttempts = 3" in stage
    assert "Get-ReporterFailureSignature" in stage
    assert "Clear-ReporterCategoryArtifacts" in stage
    assert "same failure signature" in stage
    assert "$failedCategories" in stage
    assert "$retryCategories" in stage
    assert "Invoke-ReporterWave -Attempt $attempt -WaveCategories $retryCategories" in stage


def test_runner_editor_uses_reporter_artifacts_as_explicit_input() -> None:
    """Stage3 編集長は再収集せず、Stage2 reporter artifact と Stage1 dedup を入力にする。"""
    runner = _read(RUNNER)
    stage = runner.split("Stage2 reporter fan-out", 1)[1].split("Stage4: Codex DeepDive", 1)[0]

    assert "schemas\\editor_summary.schema.json" in stage
    assert "$EditorInputManifest" in stage
    assert "reporter_artifacts" in stage
    assert "dedup_file" in stage
    assert "source_policy = 'no_recollection'" in stage
    assert "Invoke-CodexWrapper -PromptFile $EditorPromptFile" in stage


def test_runner_has_no_codex_preflight_before_full_e2e() -> None:
    """Full E2E 前に schema/prompt/manifest 不整合を Codex 実行なしで落とす。"""
    runner = _read(RUNNER)

    assert "[switch] $PreflightOnly" in runner
    assert "tools.newsroom_preflight" in runner
    assert "PreflightOnly mode: skipping codex / git pull / push / generate_pages" in runner


def test_runner_executes_stage0_stage1_before_any_codex_wrapper() -> None:
    runner = _read(RUNNER)

    stage0 = runner.index("harvest_candidates.py")
    stage1 = runner.index("cross_category_dedup.py")
    stage2 = runner.index("Stage2 reporter fan-out")
    assert stage0 < stage1 < stage2
    assert "MaxParallelReporterJobs = 7" in runner
    assert "Stage2 reporter fan-out" in runner


def test_runner_stage0_stdout_writer_is_windows_powershell_51_compatible() -> None:
    runner = _read(RUNNER)

    helper = runner.split("function Invoke-PythonStdoutFileUtf8", 1)[1].split("function ", 1)[0]
    assert "ArgumentList" not in helper
    assert "RedirectStandardOutput = $true" in helper
    assert "[System.IO.File]::WriteAllText($StdoutPath" in helper
    assert "UTF8Encoding" in helper


def test_runner_invokes_codex_wrapper_with_named_parameter_splatting() -> None:
    runner = _read(RUNNER)

    wrapper = runner.split("function Invoke-CodexWrapper", 1)[1].split("function ", 1)[0]
    assert "$codexArgs = @{" in wrapper
    assert "'CodexExe' = $CodexExe" in wrapper
    assert "'PromptFile' = $PromptFile" in wrapper
    assert "'TimeoutSec' = $TimeoutSec" in wrapper
    assert "& $CodexWrapper @codexArgs" in wrapper
    assert "'OutputSchema' = $OutputSchema" in wrapper
    assert "'OutputLastMessage' = $OutputLastMessage" in wrapper
    assert "'FlowName' = $FlowName" in wrapper
    assert "'UsageLog' = $CodexUsageLog" in wrapper
    assert "$wrapperOk = $?" in wrapper
    assert "return 125" in wrapper
    assert "'OutputSchema' = $CodexOutputSchema" not in wrapper
    assert "'-CodexExe', $CodexExe" not in wrapper


def test_runner_records_codex_usage_by_flow() -> None:
    runner = _read(RUNNER)
    assert "$CodexUsageLog = Join-Path $RepoDir \"build\\codex-usage\\$DateStamp.jsonl\"" in runner
    for flow in [
        '-FlowName "reporter:$Category"',
        "-FlowName 'newsroom_editor'",
        "-FlowName 'deepdive'",
        '-FlowName "repair:$GateId"',
    ]:
        assert flow in runner


def test_active_prompts_use_codex_terms_and_style_guide() -> None:
    prompt_paths = [
        ROOT / "prompts" / "newsroom-editor-system.md",
        ROOT / "prompts" / "newsroom-reporter-system.md",
        ROOT / "prompts" / "deepdive-runner-prompt.md",
        ROOT / "prompts" / "deepdive-research-system.md",
        ROOT / "prompts" / "model-eval-reporter.md",
        ROOT / "prompts" / "model-eval-editor-rewrite.md",
    ]
    forbidden_patterns = [
        r"\bTask\b",
        r"ng-reporter",
        r"ng-deepdive",
        r"Sonnet",
        r"Opus",
        r"claude --print",
        r"\.claude",
    ]
    for path in prompt_paths:
        text = _read(path)
        assert "prompts/style-guide.md" in text, path
        for pattern in forbidden_patterns:
            assert not re.search(pattern, text), f"{path}: {pattern}"


def test_model_eval_includes_gpt55_editor_variant_before_policy_finalization() -> None:
    assert "mini-editor-55" in VARIANTS
    assert VARIANTS["mini-editor-55"]["model"] == "gpt-5.5"
    assert "mini-editor-54" in VARIANTS
    assert VARIANTS["mini-editor-54"]["model"] == "gpt-5.4"


def test_style_guide_exists_and_is_shared_by_prompts() -> None:
    style = ROOT / "prompts" / "style-guide.md"
    text = _read(style)
    for needle in [
        "翻訳調禁止",
        "文末反復禁止",
        "冗長な接続句",
        "数字",
        "title_ja",
        "何が動き、なぜ重要で、次に何を見るか",
    ]:
        assert needle in text
