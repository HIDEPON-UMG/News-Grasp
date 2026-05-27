#!/usr/bin/env python3
"""runner wrapper (~/bin/run_claude_with_timeout.ps1) の日本語 encoding smoke test。

2026-05-23 / 24 / 25 の朝バッチ事故 (claude が「メッセージが文字化けして届いています」
を返し digest 生成 0 件 → メルマガ 3 日停止) の **構造的再発防止**。

bat → PowerShell 5.1 の argv 経路は非 ASCII を CP932 で送る。wrapper 内で
stdin を UTF-8 化しても元データが既に化けて claude が「文字化け」と判定する。
よって prompt は **必ず UTF-8 ファイル経由 (-PromptFile)** で渡す。

本テストは wrapper を実際に叩いて以下を pin する:

1. exit code == 0
2. claude の応答に「文字化け」「encoding」「Shift」「ANSI」「CP932」「mojibake」
   の **どれか 1 語でも含まれていたら FAIL** (= wrapper / prompt 経路が壊れている)
3. **日本語 prompt を投げて、応答にも日本語 (ひらがな or カタカナ or 漢字) が
   含まれる**ことを assertion (= ASCII 応答だけ通ってしまう偽 PASS を防ぐ)
4. ASCII テスト ID (NGSMOKE-OK) が応答に含まれる (= 構造解析の足場)
5. `-Prompt` (string) 経路で非 ASCII を渡したときに exit 126 で拒否される
   (= 壊れた古い経路を物理的に塞ぐ)

実行:
    pytest tests/test_runner_wrapper_smoke.py -v

CI 必須:
    本テストが PASS していない wrapper / runner.bat の変更は commit してはならない。
    safe-commit ゲート 5 に組み込み済み。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

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
    "もう一度",  # claude が再送を求めるときの常套句
]

# 日本語 1 文字でも応答にあれば「日本語が claude に届いて理解された」と判定
_JP_RE = re.compile(r"[぀-ゟ゠-ヿ一-鿿]")

_skip_reason = None
if not WRAPPER.exists():
    _skip_reason = f"wrapper not found: {WRAPPER} (NEWS_GRASP_WRAPPER で上書き可)"
elif not CLAUDE.exists():
    _skip_reason = f"claude.exe not found: {CLAUDE} (NEWS_GRASP_CLAUDE で上書き可)"
elif shutil.which(POWERSHELL) is None:
    _skip_reason = f"powershell not found in PATH (NEWS_GRASP_POWERSHELL で上書き可)"

pytestmark = pytest.mark.skipif(_skip_reason is not None, reason=_skip_reason or "")


# --------- fixture: 日本語 prompt を UTF-8 ファイルで投げて結果取得 ---------

@pytest.fixture
def smoke_run_via_promptfile(tmp_path) -> tuple[int, str]:
    """日本語 prompt を **PromptFile 経由 (UTF-8 BOM 付き)** で 1 件投げる。"""
    log_file = tmp_path / "wrapper_smoke.log"
    prompt_file = tmp_path / "prompt.md"
    prompt = (
        "これは日本語の smoke test です。\n"
        "あなたは以下のフォーマットで **2 行だけ** 返答してください。\n"
        "理由・前置き・絵文字は一切不要です。\n"
        "1 行目: NGSMOKE-OK\n"
        "2 行目: 「了解しました」(かぎ括弧込み)\n"
    )
    # BOM 付き UTF-8 で保存
    prompt_file.write_bytes(b"\xef\xbb\xbf" + prompt.encode("utf-8"))

    result = subprocess.run(
        [
            POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(WRAPPER),
            "-ClaudeExe", str(CLAUDE),
            "-PromptFile", str(prompt_file),
            "-LogFile", str(log_file),
            "-TimeoutSec", "120",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    log_text = log_file.read_text(encoding="utf-8", errors="replace") if log_file.exists() else ""
    return result.returncode, log_text


def test_wrapper_exit_zero(smoke_run_via_promptfile):
    rc, log = smoke_run_via_promptfile
    assert rc == 0, f"wrapper non-zero exit: {rc}\nlog:\n{log[:2000]}"


def test_no_encoding_error_in_response(smoke_run_via_promptfile):
    """claude の応答に「文字化け」「encoding」「Shift」「もう一度」等が出ないこと。"""
    _, log = smoke_run_via_promptfile
    # wrapper 自身のヘッダ行 ([run_claude_with_timeout] ...) は判定対象から除外。
    # pytest tmp_path に test 関数名 "test_no_encoding_error_in_response" が含まれ
    # wrapper ログに echo されて自己参照で encoding を誤検出するため (2026-05-28 修正)。
    response = "\n".join(
        ln for ln in log.splitlines() if "[run_claude_with_timeout]" not in ln
    )
    hit = [tok for tok in ENCODING_ERROR_TOKENS if tok in response]
    assert not hit, (
        f"claude が encoding 問題を検出しました: {hit}\n"
        f"= wrapper.ps1 / runner.bat の prompt 経路が壊れている疑い。\n"
        f"対策: feedback_japanese_env_first_scripting.md ルール 2 / 3 を参照\n"
        f"--- log (先頭 2000 文字) ---\n{log[:2000]}"
    )


def test_response_contains_ngsmoke_ok(smoke_run_via_promptfile):
    """応答に ASCII ID `NGSMOKE-OK` が含まれること (= 応答が届いている保証)。"""
    _, log = smoke_run_via_promptfile
    assert "NGSMOKE-OK" in log, (
        f"応答に NGSMOKE-OK が含まれていません。claude が prompt を理解できていない"
        f"可能性。\n--- log ---\n{log[:2000]}"
    )


def test_response_contains_japanese_characters(smoke_run_via_promptfile):
    """**応答に日本語文字 (ひらがな/カタカナ/漢字) が含まれる**こと。

    旧 smoke は ECHO_OK (ASCII) だけ判定していたため、prompt 経路が壊れていても
    PASS してしまう偽 PASS の温床になっていた。日本語応答必須に強化。
    """
    _, log = smoke_run_via_promptfile
    assert _JP_RE.search(log), (
        f"応答に日本語文字 (ひら/カナ/漢字) が含まれていません。prompt が claude に\n"
        f"日本語として届いていない (= 化けている) 可能性が高い。\n"
        f"--- log ---\n{log[:2000]}"
    )


# --------- 旧 -Prompt 経路の物理的封鎖を確認 ---------

@pytest.fixture
def smoke_run_via_string_prompt_nonascii(tmp_path) -> tuple[int, str]:
    """非 ASCII を含む -Prompt 文字列引数で wrapper を呼ぶ → exit 126 で拒否される想定。"""
    log_file = tmp_path / "wrapper_reject.log"
    result = subprocess.run(
        [
            POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(WRAPPER),
            "-ClaudeExe", str(CLAUDE),
            "-Prompt", "非 ASCII を含む文字列",
            "-LogFile", str(log_file),
            "-TimeoutSec", "30",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    log_text = log_file.read_text(encoding="utf-8", errors="replace") if log_file.exists() else ""
    return result.returncode, log_text


def test_string_prompt_with_nonascii_is_rejected(smoke_run_via_string_prompt_nonascii):
    """壊れた古い経路 (`-Prompt "日本語"`) は exit 126 で **物理的に拒否**される。"""
    rc, log = smoke_run_via_string_prompt_nonascii
    assert rc == 126, (
        f"非 ASCII を含む -Prompt は exit 126 で拒否される想定だが、rc={rc}\n"
        f"wrapper.ps1 のガード処理が壊れている疑い。\n--- log ---\n{log[:1500]}"
    )
