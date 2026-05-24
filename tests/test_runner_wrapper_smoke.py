#!/usr/bin/env python3
"""runner wrapper (~/bin/run_claude_with_timeout.ps1) の日本語 encoding smoke test。

2026-05-23 / 24 / 25 の朝バッチ事故 (claude が「メッセージが文字化けして届いています」
を返し digest 生成 0 件 → メルマガ 3 日停止) の **構造的再発防止**。

PowerShell 5.1 (`powershell.exe`) は外部プロセスへの非 ASCII argv をデフォルトで
CP932 (ANSI) で送るため、UTF-8 を期待する claude.exe で文字化けする。本テストは
wrapper を実際に叩いて以下を pin する:

1. exit code == 0
2. claude の応答 (stdout) に「文字化け」「encoding」「Shift」「ANSI」「CP932」
   の **どれか 1 語でも含まれていたら FAIL** (= claude が encoding 問題を検出した
   = wrapper が壊れている)
3. 短い ECHO_OK タスクを与えて、本当に応答が返ってきていることも確認

実行:
    pytest tests/test_runner_wrapper_smoke.py -v

CI 必須:
    本テストが PASS していない wrapper / runner.bat の変更は commit してはならない。
    safe-commit ゲート 5 に組み込み済み (`scripts/smoke_runner.py` 経由でも可)。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# wrapper / claude のパス
WRAPPER = Path(os.environ.get(
    "NEWS_GRASP_WRAPPER",
    r"C:\Users\hidek\bin\run_claude_with_timeout.ps1",
))
CLAUDE = Path(os.environ.get(
    "NEWS_GRASP_CLAUDE",
    r"C:\Users\hidek\.local\bin\claude.exe",
))
POWERSHELL = os.environ.get("NEWS_GRASP_POWERSHELL", "powershell")

# 「encoding 問題」を示す語 (claude が検出すると返してくる典型語)
# 1 語でも応答に出たら wrapper が壊れている = FAIL
ENCODING_ERROR_TOKENS = [
    "文字化け",
    "文字エンコー",
    "encoding",
    "Encoding",
    "Shift-JIS",
    "Shift_JIS",
    "CP932",
    "ANSI",
    "mojibake",
]

_skip_reason = None
if not WRAPPER.exists():
    _skip_reason = f"wrapper not found: {WRAPPER} (NEWS_GRASP_WRAPPER で上書き可)"
elif not CLAUDE.exists():
    _skip_reason = f"claude.exe not found: {CLAUDE} (NEWS_GRASP_CLAUDE で上書き可)"
elif shutil.which(POWERSHELL) is None:
    _skip_reason = f"powershell not found in PATH (NEWS_GRASP_POWERSHELL で上書き可)"

pytestmark = pytest.mark.skipif(_skip_reason is not None, reason=_skip_reason or "")


@pytest.fixture
def smoke_run(tmp_path) -> tuple[int, str]:
    """日本語 prompt を 1 件投げて (exit_code, log_text) を返す。"""
    log_file = tmp_path / "wrapper_smoke.log"
    prompt = (
        "日本語のテストです。返事は ECHO_OK の 7 文字だけ返してください。"
        "理由・前置き・絵文字は禁止。"
    )
    result = subprocess.run(
        [
            POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(WRAPPER),
            "-ClaudeExe", str(CLAUDE),
            "-Prompt", prompt,
            "-LogFile", str(log_file),
            "-TimeoutSec", "120",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    log_text = ""
    if log_file.exists():
        log_text = log_file.read_text(encoding="utf-8", errors="replace")
    return result.returncode, log_text


def test_wrapper_exit_zero(smoke_run):
    """wrapper 正常終了 (timeout / start failure ではない)。"""
    rc, log = smoke_run
    assert rc == 0, f"wrapper non-zero exit: {rc}\nlog:\n{log[:2000]}"


def test_no_encoding_error_in_response(smoke_run):
    """claude の応答に「文字化け」「encoding」「Shift」等が **出てこない**こと。

    出てきた瞬間 = wrapper が CP932 で argv を送ってしまった証拠。
    """
    _, log = smoke_run
    hit = [tok for tok in ENCODING_ERROR_TOKENS if tok in log]
    assert not hit, (
        f"claude が encoding 問題を検出しました: {hit}\n"
        f"= wrapper.ps1 が非 ASCII argv を CP932 で送っている疑い。\n"
        f"対策: feedback_japanese_env_first_scripting.md ルール 2 を参照\n"
        f"--- log (先頭 2000 文字) ---\n{log[:2000]}"
    )


def test_response_contains_echo_ok(smoke_run):
    """claude が本当に応答を返しているか (空応答ではない)。"""
    _, log = smoke_run
    assert "ECHO_OK" in log, (
        f"応答に ECHO_OK が含まれていません。claude が日本語 prompt を理解できて"
        f"いない可能性。\n--- log (先頭 2000 文字) ---\n{log[:2000]}"
    )
