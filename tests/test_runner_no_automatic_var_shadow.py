#!/usr/bin/env python3
"""news-grasp-runner.ps1 が PowerShell 自動変数 `$args` を shadow しないことを pin する契約テスト。

2026-06-10 の実害 (構造的再発防止 / [[feedback_check_design_principles]] Lv3-4):

daily-quality gate 失敗 → `Invoke-TargetedRepair` が retry 予算チェック python を
呼ぶとき、ローカル配列を **`$args`** に代入して `Invoke-Logged { & $PyExe @args }`
で splat していた。`$args` は PowerShell の自動変数で、scriptblock を
`Invoke-Logged` 内の `& $Block` で実行すると `@args` は **scriptblock 自身の空の
automatic $args** に解決される。結果 `& $PyExe` がスクリプト無指定で起動し、
Python 3.13 の対話 REPL (`_pyrepl`) が立ち上がる。Task Scheduler 配下 (非 TTY) では
console 寸法取得が WinError 6/123 で失敗し、例外リトライ無限ループに陥って runner
全体がハング (当日ログに 27000 行超の traceback、CPU 0% で 30 分以上停止)。

修正は変数名を `$gateAttemptArgs` 等の非自動変数へリネームすること
(`Invoke-PythonGateWithRepair` の `@PythonArgs` と同じ正常な closure 捕捉経路)。

本テストは runner ソースを静的検査し、scriptblock に splat される python 引数配列が
自動変数 `$args` を使って再導入されたら FAIL する。PSScriptAnalyzer の
PSAvoidAssignmentToAutomaticVariable を、この 1 ファイル・この 1 class of bug に
絞って CI/pytest gate へ常設化したもの。

実行:
    pytest tests/test_runner_no_automatic_var_shadow.py -v
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

RUNNER = Path(os.environ.get(
    "NEWS_GRASP_RUNNER",
    r"C:\Users\hidek\bin\news-grasp-runner.ps1",
))

_skip_reason = None
if not RUNNER.exists():
    _skip_reason = f"runner not found: {RUNNER} (NEWS_GRASP_RUNNER で上書き可)"

pytestmark = pytest.mark.skipif(_skip_reason is not None, reason=_skip_reason or "")

# `$args = ...` / `$args += ...` の代入 (= 自動変数 shadow)。
# 行頭/空白に続く `$args` への代入のみを対象にし、`foreach ($x in $args)` のような
# 正当な読み取りは拾わない。
_ASSIGN_ARGS_RE = re.compile(r"^\s*\$args\s*(=|\+=)", re.MULTILINE)


def _strip_ps_comments(src: str) -> str:
    """PowerShell の行コメント (`#` 以降) を除去する。

    本テストの危険パターン (`$args =` / `& $PyExe @args`) を *説明する* コメント
    自体を誤検知しないため。`& $PyExe @args` のような実害パターンは文字列リテラル
    内に現れないので、各行の最初の `#` 以降を落とす素朴な方式で十分。
    """
    out = []
    for line in src.splitlines():
        idx = line.find("#")
        out.append(line if idx < 0 else line[:idx])
    return "\n".join(out)


def _read_runner() -> str:
    # runner は UTF-8 BOM 付き (enforce_script_encoding.ps1 が付与)。
    # コメント行は除去して、規約を説明するコメント自身を拾わないようにする。
    return _strip_ps_comments(RUNNER.read_text(encoding="utf-8-sig", errors="replace"))


def test_runner_does_not_assign_to_automatic_args():
    """runner は自動変数 `$args` へ代入しない (REPL 無限ループ hang の class を封じる)。"""
    src = _read_runner()
    hits = [m.group(0).strip() for m in _ASSIGN_ARGS_RE.finditer(src)]
    assert not hits, (
        "news-grasp-runner.ps1 が PowerShell 自動変数 $args へ代入しています: "
        f"{hits}\n"
        "scriptblock 経由 (Invoke-Logged { & $PyExe @args }) で splat すると空配列に\n"
        "化けて bare python = 対話 REPL 無限ループ hang を起こす (2026-06-10 実害)。\n"
        "$gateAttemptArgs 等の非自動変数名へリネームしてください。"
    )


def test_runner_commits_digest_sources_before_push():
    """runner が digest/ と data/ を stage する step を持つこと。

    2026-06-09 改定で Claude セッションは commit 禁止になり (routine-system.md
    ステップ 6「commit / push は ps1 が代行」)、commit 責務は runner へ移った。
    しかし runner は docs/ しか git add しておらず、digest md / articles.jsonl が
    永久に未コミット = push しても本日号ソースが remote に残らない片手落ちだった
    (2026-06-10 発覚)。本テストは「digest/data を stage する行」が runner から
    消えたら FAIL する。
    """
    src = _read_runner()
    assert re.search(r"add\s+'digest/'\s+'data/'", src), (
        "news-grasp-runner.ps1 に digest/ + data/ を stage する step がありません。\n"
        "claude は commit しない設計 (routine-system.md ステップ 6) のため、runner が\n"
        "gate 通過後に digest/data を commit しないと本日号ソースが remote に出ない。"
    )


def test_runner_no_scriptblock_splat_of_args():
    """scriptblock 内で python を `@args` で splat 起動していないこと (二重の安全網)。"""
    src = _read_runner()
    bad = re.findall(r"&\s*\$PyExe\s+@args\b", src)
    assert not bad, (
        "news-grasp-runner.ps1 が scriptblock 内で `& $PyExe @args` を使っています。\n"
        "@args は自動変数 shadow で空に化け bare python REPL hang を招く。"
        "非自動変数名で splat してください。"
    )
