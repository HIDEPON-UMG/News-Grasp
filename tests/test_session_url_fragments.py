#!/usr/bin/env python3
"""session URL フラグメント方式の契約テスト (2026-06-12 並列 race 解消)。

# 検証する「なぜ重要か」

旧方式は共有ファイル `data/_session_urls.json` への read → union → replace で、
複数サブエージェントが並列で WebSearch/WebFetch を呼ぶと「後勝ちで前の URL が消える」
race を持っていた。フラグメント方式 (発火 1 回 = 新規 1 ファイル) はこの共有可変状態を
消して race を表現不能にする。本テストは以下を locked-in する:

  1. 2 連続発火で 2 ファイルが生成され、相互上書きが起きない (無共有性)
  2. union 読み (audit 側 `_load_session_urls`) が両フラグメントの urls を返す
  3. 破損フラグメント 1 件は warn-skip され、残りは読める
  4. 空 urls のときはフラグメントを生成しない

実行:
  pytest tests/test_session_url_fragments.py -v
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
HOOK_PATH = ROOT / ".codex" / "hooks" / "append_session_urls.py"

from tools.audit_all_article_urls import _load_session_urls  # noqa: E402


def _load_hook_module():
    spec = importlib.util.spec_from_file_location("append_session_urls", str(HOOK_PATH))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


hook = _load_hook_module()

TODAY = date.today().strftime("%Y-%m-%d")


# ── 1. 2 連続発火で 2 ファイル・相互上書きなし (無共有性) ─────────────────────


def test_two_fires_create_two_distinct_fragments(tmp_path: Path):
    """発火 2 回で 2 ファイルが生成され、互いに上書きしない (= race を表現不能化)。"""
    fragments_root = tmp_path / "data" / "_session_urls.d"
    f1 = hook.write_fragment({"https://example.com/a"}, TODAY, fragments_root)
    f2 = hook.write_fragment({"https://example.com/b"}, TODAY, fragments_root)

    assert f1 != f2, "発火ごとに別ファイル名 (uuid) になる契約"
    day_dir = fragments_root / TODAY
    frags = sorted(day_dir.glob("*.json"))
    assert len(frags) == 2, f"2 連続発火で 2 ファイル生成されるはず: {frags}"

    # 1 つ目の内容は 2 つ目の発火で消えない (無共有)
    d1 = json.loads(f1.read_text(encoding="utf-8"))
    d2 = json.loads(f2.read_text(encoding="utf-8"))
    assert d1["urls"] == ["https://example.com/a"]
    assert d2["urls"] == ["https://example.com/b"]


# ── 2. union 読みが両フラグメントの urls を返す ──────────────────────────────


def test_union_read_merges_both_fragments(tmp_path: Path):
    """audit 側の _load_session_urls が当日フラグメント群を union して返す。"""
    fragments_root = tmp_path / "data" / "_session_urls.d"
    hook.write_fragment({"https://example.com/a/"}, TODAY, fragments_root)
    hook.write_fragment({"https://example.com/b#sec"}, TODAY, fragments_root)

    norm, _path, d = _load_session_urls(tmp_path, TODAY)
    assert d == TODAY
    # 正規化 (末尾 / と fragment 除去) されて両方が白リストに入る
    assert "https://example.com/a" in norm
    assert "https://example.com/b" in norm


# ── 3. 破損フラグメント 1 件は warn-skip され、残りは読める ───────────────────


def test_broken_fragment_is_warn_skipped(tmp_path: Path, capsys):
    """破損フラグメント 1 件は skip され、健全な残りは読める (全体を止めない)。"""
    fragments_root = tmp_path / "data" / "_session_urls.d"
    hook.write_fragment({"https://example.com/good"}, TODAY, fragments_root)
    # 壊れた JSON フラグメントを手で 1 件混ぜる
    (fragments_root / TODAY / "broken.json").write_text("{not json", encoding="utf-8")

    norm, _path, d = _load_session_urls(tmp_path, TODAY)
    assert d == TODAY
    assert "https://example.com/good" in norm, "健全フラグメントは読めるはず"
    captured = capsys.readouterr()
    assert "破損" in captured.err or "skip" in captured.err, (
        f"破損フラグメントの WARN が stderr に出るはず: {captured.err}"
    )


# ── 4. 空 urls のときはフラグメントを生成しない (hook の main 経路) ────────────


def test_empty_urls_no_fragment_via_main(tmp_path: Path, monkeypatch):
    """URL を 1 件も抽出できない発火ではフラグメントを作らない。

    hook の main() を URL 抽出 0 件の event で叩き、フラグメントディレクトリが
    生成されないことを確認する (audit ログ痕跡は別テストで担保済み)。
    """
    fragments_root = tmp_path / "data" / "_session_urls.d"
    monkeypatch.setattr(hook, "_FRAGMENTS_ROOT", fragments_root)
    monkeypatch.setattr(hook, "_AUDIT_LOG", tmp_path / "data" / "_session_urls.audit.log")
    # WebSearch だが results 空 = URL 抽出 0 件
    payload = json.dumps({
        "tool_name": "WebSearch",
        "tool_input": {"query": "x"},
        "tool_response": {},
    })
    monkeypatch.setattr("sys.stdin", _FakeStdin(payload))
    rc = hook.main()
    assert rc == 0
    assert not fragments_root.exists(), "URL 0 件ではフラグメントを作らない契約"


class _FakeStdin:
    """main() が読む sys.stdin.read() を差し替えるための最小スタブ。"""

    def __init__(self, data: str):
        self._data = data

    def read(self) -> str:
        return self._data
