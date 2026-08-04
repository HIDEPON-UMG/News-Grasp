#!/usr/bin/env python3
"""runner wrapper (~/bin/run_codex_with_timeout.ps1) の契約テスト。"""
from __future__ import annotations

import os
import json
import subprocess
import sys
from pathlib import Path

import pytest

WRAPPER = Path(os.environ.get(
    "NEWS_GRASP_CODEX_WRAPPER",
    r"C:\Users\hidek\bin\run_codex_with_timeout.ps1",
))
POWERSHELL = os.environ.get("NEWS_GRASP_POWERSHELL", "powershell")
ROOT = Path(__file__).resolve().parent.parent
REPO_WRAPPER = ROOT / "scripts" / "ops" / "run_codex_with_timeout.ps1"

pytestmark = pytest.mark.skipif(not WRAPPER.exists(), reason=f"wrapper not found: {WRAPPER}")


def _fake_codex(tmp_path: Path) -> Path:
    fake = tmp_path / "fake_codex.ps1"
    fake.write_text(
        "Write-Output ('ARGV:' + ($args -join ' '))\n"
        "Write-Output '{\"type\":\"result\",\"is_error\":false}'\n",
        encoding="utf-8-sig",
    )
    return fake


def _fake_codex_with_usage(tmp_path: Path) -> Path:
    fake = tmp_path / "fake_codex_usage.ps1"
    fake.write_text(
        "Write-Output ('ARGV:' + ($args -join ' '))\n"
        "Write-Output 'tokens used'\n"
        "Write-Output '12,345'\n",
        encoding="utf-8-sig",
    )
    return fake


def _fake_codex_usage_limit(tmp_path: Path) -> Path:
    fake = tmp_path / "fake_codex_usage_limit.ps1"
    fake.write_text(
        "Write-Output \"ERROR: You've hit your usage limit.\"\n"
        "Write-Output 'Visit https://chatgpt.com/codex/settings/usage to purchase more credits or try again at 5:36 AM.'\n"
        "exit 1\n",
        encoding="utf-8-sig",
    )
    return fake


def _fake_codex_success_with_quoted_quota_terms(tmp_path: Path) -> Path:
    fake = tmp_path / "fake_codex_success_with_quoted_quota_terms.ps1"
    fake.write_text(
        "Write-Output '調査メモ: purchase more credits という文言を誤検知してはならない。'\n"
        "Write-Output 'tokens used'\n"
        "Write-Output '123'\n"
        "exit 0\n",
        encoding="utf-8-sig",
    )
    return fake


def _fake_codex_with_delayed_exit(tmp_path: Path, sentinel: Path) -> Path:
    fake = tmp_path / "fake_codex_delayed.ps1"
    fake.write_text(
        "$sentinel = [Environment]::GetEnvironmentVariable('CODEX_FAKE_SUCCESS_SENTINEL', 'Process')\n"
        "if (-not $sentinel) { Write-Error 'CODEX_FAKE_SUCCESS_SENTINEL missing'; exit 99 }\n"
        "$childCode = \"Start-Sleep -Seconds 2; [System.IO.File]::WriteAllText(''${sentinel}'', ''mutated'', [System.Text.UTF8Encoding]::new(`$false))\"\n"
        "Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile', '-Command', $childCode) -WindowStyle Hidden | Out-Null\n"
        "Write-Output '{\"type\":\"result\",\"is_error\":false}'\n"
        "[System.IO.File]::WriteAllText($sentinel, 'green', [System.Text.UTF8Encoding]::new($false))\n"
        "Start-Sleep -Seconds 20\n"
        "exit 0\n",
        encoding="utf-8",
    )
    return fake


def _success_probe_script(tmp_path: Path, sentinel: Path) -> Path:
    probe = tmp_path / "success_probe.ps1"
    probe.write_text(
        "param([string]$Sentinel)\n"
        "if (Test-Path -LiteralPath $Sentinel) { exit 0 }\n"
        "exit 1\n",
        encoding="utf-8",
    )
    return probe


def _fake_codex_capture_ps1(tmp_path: Path) -> Path:
    fake = tmp_path / "fake_codex_capture.ps1"
    fake.write_text(
        "$capturePath = $env:CODEX_FAKE_CAPTURE_JSON\n"
        "if (-not $capturePath) { Write-Error 'CODEX_FAKE_CAPTURE_JSON missing'; exit 99 }\n"
        "$stdinBytesStream = [Console]::OpenStandardInput()\n"
        "$stdinMemory = [System.IO.MemoryStream]::new()\n"
        "$stdinBytesStream.CopyTo($stdinMemory)\n"
        "$stdinText = [System.Text.Encoding]::UTF8.GetString($stdinMemory.ToArray())\n"
        "$data = [ordered]@{\n"
        "  argv = @($args)\n"
        "  stdin = $stdinText\n"
        "  env = [ordered]@{\n"
        "    PYTHONIOENCODING = $env:PYTHONIOENCODING\n"
        "    PYTHONUTF8 = $env:PYTHONUTF8\n"
        "    CODEX_NONINTERACTIVE_SESSION = $env:CODEX_NONINTERACTIVE_SESSION\n"
        "    CODEX_OUTPUT_CONTRACT = $env:CODEX_OUTPUT_CONTRACT\n"
        "  }\n"
        "}\n"
        "$json = $data | ConvertTo-Json -Depth 5\n"
        "[System.IO.File]::WriteAllText($capturePath, $json, [System.Text.UTF8Encoding]::new($false))\n"
        "Write-Output '{\"type\":\"result\",\"is_error\":false}'\n"
        "exit 0\n",
        encoding="utf-8",
    )
    return fake


def _canonical_test_broker(tmp_path: Path) -> tuple[list[str], dict[str, str]]:
    """本番と同じ必須broker境界を、外部modelなしで通す隔離fixtureを返す。"""
    # USERPROFILEそのものを隔離rootにして、broker正本照合とログ秘匿を同時に再演する。
    profile = tmp_path
    broker = profile / "bin" / "ai-model-spawn-broker.py"
    workspace = tmp_path / "workspace"
    registry = workspace / "docs" / "harness" / "high_cost_model_routes_v1.json"
    admission = tmp_path / "scheduled-operation-admission.json"
    broker.parent.mkdir(parents=True, exist_ok=True)
    registry.parent.mkdir(parents=True, exist_ok=True)
    broker.write_text(
        "import subprocess\n"
        "import sys\n"
        "args = sys.argv[1:]\n"
        "if not args or args[0] != 'exec':\n"
        "    raise SystemExit(97)\n"
        "try:\n"
        "    executable = args[args.index('--executable') + 1]\n"
        "    separator = args.index('--')\n"
        "except (ValueError, IndexError):\n"
        "    raise SystemExit(98)\n"
        "raise SystemExit(subprocess.run([executable, *args[separator + 1:]]).returncode)\n",
        encoding="utf-8",
    )
    registry.write_text("{}\n", encoding="utf-8")
    admission.write_text("{}\n", encoding="utf-8")
    env = os.environ.copy()
    env["USERPROFILE"] = str(profile)
    args = [
        "-HighCostWorkspaceRoot", str(workspace),
        "-HighCostBudgetToolPath", str(broker),
        "-HighCostPythonExe", sys.executable,
        "-HighCostCallId", f"test-{tmp_path.name}",
        "-HighCostAdmissionPath", str(admission),
        "-HighCostExpectedOperationKind", "full_e2e",
    ]
    return args, env


def test_codex_wrapper_uses_prompt_file_working_directory_and_schema(tmp_path: Path) -> None:
    prompt_file = tmp_path / "prompt.md"
    log_file = tmp_path / "wrapper.log"
    prompt_file.write_text("日本語 smoke test\n", encoding="utf-8")
    high_cost_args, env = _canonical_test_broker(tmp_path)
    result = subprocess.run(
        [
            POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(WRAPPER),
            "-CodexExe", str(_fake_codex(tmp_path)),
            "-PromptFile", str(prompt_file),
            "-LogFile", str(log_file),
            "-TimeoutSec", "30",
            "-IdleTimeoutSec", "30",
            "-WorkingDirectory", str(ROOT),
            "-OutputSchema", str(ROOT / "schemas" / "model_eval_output.schema.json"),
            "-OutputLastMessage", str(tmp_path / "last-message.txt"),
            "-Model", "test-model",
            "-FlowName", "reporter:test",
            *high_cost_args,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        env=env,
    )

    log = log_file.read_text(encoding="utf-8", errors="replace")
    assert result.returncode == 0, result.stderr + log
    assert "exec" in log
    assert "--output-schema" in log
    assert "--output-last-message" in log or " -o " in log
    assert "-C" in log
    assert "--search" not in log
    assert "test-model" in log
    assert str(tmp_path) not in log
    assert str(ROOT) not in log


def test_codex_wrapper_sets_noninteractive_artifact_gate_env_and_stdin_prompt(tmp_path: Path) -> None:
    """非対話 runner の正本は env/stdin/exit code で、人間向け完了報告に依存しない。"""
    prompt_file = tmp_path / "prompt.md"
    log_file = tmp_path / "wrapper.log"
    capture_file = tmp_path / "capture.json"
    prompt_text = "日本語の artifact gate prompt\n長大本文を argv に載せない\n"
    prompt_file.write_text(prompt_text, encoding="utf-8")
    high_cost_args, env = _canonical_test_broker(tmp_path)
    env["CODEX_FAKE_CAPTURE_JSON"] = str(capture_file)

    result = subprocess.run(
        [
            POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(WRAPPER),
            "-CodexExe", str(_fake_codex_capture_ps1(tmp_path)),
            "-PromptFile", str(prompt_file),
            "-LogFile", str(log_file),
            "-TimeoutSec", "30",
            "-IdleTimeoutSec", "30",
            "-WorkingDirectory", str(ROOT),
            "-Model", "test-model",
            "-FlowName", "reporter:test",
            *high_cost_args,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        env=env,
    )

    log = log_file.read_text(encoding="utf-8", errors="replace")
    assert result.returncode == 0, result.stderr + log
    capture = json.loads(capture_file.read_text(encoding="utf-8-sig"))
    argv_text = " ".join(capture["argv"])
    assert capture["stdin"].replace("\r\n", "\n") == prompt_text
    assert "日本語の artifact gate prompt" not in argv_text, "prompt leaked into argv"
    assert capture["env"]["CODEX_NONINTERACTIVE_SESSION"] == "1", "artifact-gate env missing"
    assert capture["env"]["CODEX_OUTPUT_CONTRACT"] == "artifact-gate", "artifact-gate env missing"
    assert capture["env"]["PYTHONUTF8"] == "1"
    assert capture["env"]["PYTHONIOENCODING"] == "utf-8:backslashreplace"


def test_codex_wrapper_has_no_legacy_agent_parameters() -> None:
    text = WRAPPER.read_text(encoding="utf-8-sig")
    assert "ClaudeExe" not in text
    assert "run_claude" not in text


def test_codex_wrapper_quotes_argument_list_for_paths_with_spaces() -> None:
    text = WRAPPER.read_text(encoding="utf-8-sig")
    assert "ConvertTo-ProcessArgumentString" in text
    assert "$effectiveArgString = ConvertTo-ProcessArgumentString" in text
    assert "CreateSuspendedAssignedProcess($filePath, $effectiveArgString" in text
    assert "-ArgumentList $effectiveArgs" not in text


def test_codex_wrapper_writes_flow_usage_jsonl(tmp_path: Path) -> None:
    prompt_file = tmp_path / "prompt.md"
    log_file = tmp_path / "wrapper.log"
    usage_log = tmp_path / "usage.jsonl"
    prompt_file.write_text("usage smoke\n", encoding="utf-8")
    high_cost_args, env = _canonical_test_broker(tmp_path)

    result = subprocess.run(
        [
            POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(WRAPPER),
            "-CodexExe", str(_fake_codex_with_usage(tmp_path)),
            "-PromptFile", str(prompt_file),
            "-LogFile", str(log_file),
            "-TimeoutSec", "30",
            "-IdleTimeoutSec", "30",
            "-WorkingDirectory", str(ROOT),
            "-FlowName", "reporter:ai",
            "-UsageLog", str(usage_log),
            *high_cost_args,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    text = usage_log.read_text(encoding="utf-8")
    assert '"flow":"reporter:ai"' in text
    assert '"tokens_used":12345' in text
    assert str(tmp_path) not in text
    assert str(ROOT) not in text


def test_codex_wrapper_maps_usage_limit_to_typed_external_rc(tmp_path: Path) -> None:
    """Codex quota は内部 reporter 失敗ではなく、外部 readiness の rc=123 に正規化する。"""
    prompt_file = tmp_path / "prompt.md"
    log_file = tmp_path / "wrapper.log"
    usage_log = tmp_path / "usage.jsonl"
    prompt_file.write_text("quota smoke\n", encoding="utf-8")
    high_cost_args, env = _canonical_test_broker(tmp_path)

    result = subprocess.run(
        [
            POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(WRAPPER),
            "-CodexExe", str(_fake_codex_usage_limit(tmp_path)),
            "-PromptFile", str(prompt_file),
            "-LogFile", str(log_file),
            "-TimeoutSec", "30",
            "-IdleTimeoutSec", "30",
            "-WorkingDirectory", str(ROOT),
            "-FlowName", "reporter:ai",
            "-UsageLog", str(usage_log),
            *high_cost_args,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        env=env,
    )

    log = log_file.read_text(encoding="utf-8", errors="replace")
    usage = usage_log.read_text(encoding="utf-8", errors="replace")
    assert result.returncode == 123, result.stderr + log
    assert "codex quota detected" in log
    assert '"exit_code":123' in usage


def test_codex_wrapper_never_overrides_success_rc_from_quoted_quota_terms(tmp_path: Path) -> None:
    """成功出力中の説明・prompt・memory引用を実quotaへ読み替えない。"""
    prompt_file = tmp_path / "prompt.md"
    log_file = tmp_path / "wrapper.log"
    usage_log = tmp_path / "usage.jsonl"
    prompt_file.write_text("quoted quota terms smoke\n", encoding="utf-8")
    high_cost_args, env = _canonical_test_broker(tmp_path)

    result = subprocess.run(
        [
            POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(REPO_WRAPPER),
            "-CodexExe", str(_fake_codex_success_with_quoted_quota_terms(tmp_path)),
            "-PromptFile", str(prompt_file),
            "-LogFile", str(log_file),
            "-TimeoutSec", "30",
            "-IdleTimeoutSec", "30",
            "-WorkingDirectory", str(ROOT),
            "-FlowName", "reporter:it",
            "-UsageLog", str(usage_log),
            *high_cost_args,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        env=env,
    )

    assert result.returncode == 0, log_file.read_text(encoding="utf-8", errors="replace")
    assert '"exit_code":0' in usage_log.read_text(encoding="utf-8")


def test_codex_wrapper_success_probe_returns_after_artifact_gate_green(tmp_path: Path) -> None:
    """成果物ゲートがGreenなら、長引くcodex子プロセスを待たずにrc=0で制御を戻す。"""
    prompt_file = tmp_path / "prompt.md"
    log_file = tmp_path / "wrapper.log"
    usage_log = tmp_path / "usage.jsonl"
    sentinel = tmp_path / "artifact-green.txt"
    prompt_file.write_text("success probe smoke\n", encoding="utf-8")
    probe = _success_probe_script(tmp_path, sentinel)
    high_cost_args, env = _canonical_test_broker(tmp_path)
    env["CODEX_FAKE_SUCCESS_SENTINEL"] = str(sentinel)

    result = subprocess.run(
        [
            POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(WRAPPER),
            "-CodexExe", str(_fake_codex_with_delayed_exit(tmp_path, sentinel)),
            "-PromptFile", str(prompt_file),
            "-LogFile", str(log_file),
            "-TimeoutSec", "30",
            "-IdleTimeoutSec", "30",
            "-WorkingDirectory", str(ROOT),
            "-FlowName", "newsroom_editor",
            "-UsageLog", str(usage_log),
            "-SuccessProbeCommand", f'powershell -NoProfile -ExecutionPolicy Bypass -File "{probe}" -Sentinel "{sentinel}"',
            "-SuccessProbeIntervalSec", "1",
            "-SuccessProbeMinElapsedSec", "1",
            *high_cost_args,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        env=env,
    )

    log = log_file.read_text(encoding="utf-8", errors="replace")
    assert result.returncode == 0, result.stderr + log
    assert "success probe passed" in log
    assert "process tree confirmed stopped" in log
    assert '"flow":"newsroom_editor"' in usage_log.read_text(encoding="utf-8", errors="replace")
    assert '"exit_code":0' in usage_log.read_text(encoding="utf-8", errors="replace")
    import time

    time.sleep(3)
    assert sentinel.read_text(encoding="utf-8") == "green"
