#!/usr/bin/env python3
"""案②-Lite 案③: PostToolUse hook (`.claude/hooks/append_session_urls.py`) 契約テスト。

# 検証する「なぜ重要か」

LLM の規律破り (= prompts で「session ファイル書け」と命じても破る) を構造的に封じる
ため、Claude Code ハーネスが WebSearch / WebFetch 後に本 hook を発火させ、ハーネス層
が直接 `data/_session_urls.json` に URL を append する設計。本テストは以下を locked-in:

  1. WebFetch event の `tool_input.url` が拾われて session に入る
  2. WebSearch event の `tool_response.results[].url` が拾われて session に入る
  3. WebSearch event の results 不明形式でも JSON dump → 正規表現フォールバックで URL を拾う
  4. 既存 session (当日 date) に union で append される (重複排除・sorted)
  5. 既存 session の date が他日 → 当日 fresh で上書き (= 古い URL を残さない)
  6. session ファイル不在 → 当日 date で新規作成
  7. 不正な stdin (空 / 壊れた JSON) でも例外で落ちず exit 0

実行:
  pytest tests/test_append_session_urls_hook.py -v
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK_PATH = ROOT / ".claude" / "hooks" / "append_session_urls.py"


# Hook モジュールを動的読込 (`.claude/hooks/` は通常 PYTHONPATH に乗らないため)
def _load_hook_module():
    spec = importlib.util.spec_from_file_location("append_session_urls", str(HOOK_PATH))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


hook = _load_hook_module()


# ── 純粋関数の単体テスト (URL 抽出ロジック) ───────────────────────────────────


def test_extract_webfetch_url():
    ev = {
        "tool_name": "WebFetch",
        "tool_input": {"url": "https://example.com/article"},
        "tool_response": {"content": "..."},
    }
    urls = hook.extract_urls_from_event(ev)
    assert urls == {"https://example.com/article"}


def test_extract_webfetch_strips_trailing_punctuation():
    ev = {
        "tool_name": "WebFetch",
        "tool_input": {"url": "https://example.com/a."},
        "tool_response": {},
    }
    urls = hook.extract_urls_from_event(ev)
    assert urls == {"https://example.com/a"}


def test_extract_websearch_results_array():
    ev = {
        "tool_name": "WebSearch",
        "tool_input": {"query": "openai"},
        "tool_response": {
            "results": [
                {"url": "https://example.com/a", "title": "A"},
                {"url": "https://example.com/b", "title": "B"},
                {"title": "C"},  # url キー欠落は無視
            ]
        },
    }
    urls = hook.extract_urls_from_event(ev)
    assert urls == {"https://example.com/a", "https://example.com/b"}


def test_extract_websearch_fallback_regex_from_dict_dump():
    """results 配列が無い未知形式でも JSON dump から URL を拾える。"""
    ev = {
        "tool_name": "WebSearch",
        "tool_input": {"query": "x"},
        "tool_response": {
            "summary": "See https://example.com/a and https://example.com/b for details",
            "metadata": {"source_url": "https://example.com/c"},
        },
    }
    urls = hook.extract_urls_from_event(ev)
    assert urls == {"https://example.com/a", "https://example.com/b", "https://example.com/c"}


def test_extract_unrelated_tool_returns_empty():
    """対象外ツール (Bash 等) では URL を採用しない。"""
    ev = {
        "tool_name": "Bash",
        "tool_input": {"command": "curl https://example.com"},
        "tool_response": {"stdout": "fetched https://example.com"},
    }
    urls = hook.extract_urls_from_event(ev)
    assert urls == set()


# ── merge_into_session_file の挙動 ────────────────────────────────────────────


def test_merge_creates_new_file_when_missing(tmp_path: Path):
    p = tmp_path / "data" / "_session_urls.json"
    today = date.today().strftime("%Y-%m-%d")
    payload = hook.merge_into_session_file({"https://example.com/a"}, today, p)
    assert payload == {"date": today, "urls": ["https://example.com/a"]}
    saved = json.loads(p.read_text(encoding="utf-8"))
    assert saved == payload


def test_merge_unions_when_same_date(tmp_path: Path):
    p = tmp_path / "_session_urls.json"
    today = date.today().strftime("%Y-%m-%d")
    p.write_text(
        json.dumps({"date": today, "urls": ["https://example.com/old"]}),
        encoding="utf-8",
    )
    hook.merge_into_session_file({"https://example.com/new"}, today, p)
    saved = json.loads(p.read_text(encoding="utf-8"))
    assert saved["date"] == today
    assert saved["urls"] == sorted(["https://example.com/old", "https://example.com/new"])


def test_merge_overwrites_when_date_differs(tmp_path: Path):
    """日付が変わったら古い URL は捨てる (前日の白リストを残さない契約)。"""
    p = tmp_path / "_session_urls.json"
    today = date.today().strftime("%Y-%m-%d")
    yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    p.write_text(
        json.dumps({"date": yesterday, "urls": ["https://example.com/old-day"]}),
        encoding="utf-8",
    )
    hook.merge_into_session_file({"https://example.com/today"}, today, p)
    saved = json.loads(p.read_text(encoding="utf-8"))
    assert saved["date"] == today
    assert saved["urls"] == ["https://example.com/today"], (
        "date が変わったら前日の URL を捨てる契約"
    )


def test_merge_dedupes(tmp_path: Path):
    p = tmp_path / "_session_urls.json"
    today = date.today().strftime("%Y-%m-%d")
    p.write_text(
        json.dumps({"date": today, "urls": ["https://example.com/a"]}),
        encoding="utf-8",
    )
    hook.merge_into_session_file({"https://example.com/a", "https://example.com/b"}, today, p)
    saved = json.loads(p.read_text(encoding="utf-8"))
    assert saved["urls"] == ["https://example.com/a", "https://example.com/b"]


def test_merge_carries_over_note_field(tmp_path: Path):
    """`_note` (運用説明) は date が変わっても carry over される契約。

    リポ初期サンプルの `_note` 説明文が朝バッチの初回 hook 発火で消えると、
    将来チーム共有時に「このファイルは何か」が読めなくなる。だから保持する。
    """
    p = tmp_path / "_session_urls.json"
    yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    today = date.today().strftime("%Y-%m-%d")
    p.write_text(
        json.dumps({
            "_note": "運用説明テキスト",
            "date": yesterday,
            "urls": ["https://example.com/old"],
        }),
        encoding="utf-8",
    )
    payload = hook.merge_into_session_file({"https://example.com/new"}, today, p)
    assert payload["_note"] == "運用説明テキスト"
    assert payload["date"] == today
    assert payload["urls"] == ["https://example.com/new"], (
        "date が変わったら urls は新規分のみ (= 古い URL は持ち越さない)"
    )
    saved = json.loads(p.read_text(encoding="utf-8"))
    assert saved["_note"] == "運用説明テキスト"


def test_merge_recovers_from_broken_json(tmp_path: Path):
    p = tmp_path / "_session_urls.json"
    p.write_text("{not json", encoding="utf-8")
    today = date.today().strftime("%Y-%m-%d")
    hook.merge_into_session_file({"https://example.com/x"}, today, p)
    saved = json.loads(p.read_text(encoding="utf-8"))
    assert saved == {"date": today, "urls": ["https://example.com/x"]}


# ── CLI 統合テスト (stdin → 副作用) ──────────────────────────────────────────


def _run_hook(stdin_payload: str | dict, tmp_repo: Path) -> subprocess.CompletedProcess:
    """hook を子プロセスで起動し stdin に payload を渡す。

    `_REPO_ROOT` は hook の `__file__` 位置から計算されるので、本テストでは
    hook スクリプトを `tmp_repo/.claude/hooks/` に**コピー**して repo root を
    tmp に切り替える。
    """
    hooks_dir = tmp_repo / ".claude" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    dst = hooks_dir / "append_session_urls.py"
    dst.write_text(HOOK_PATH.read_text(encoding="utf-8"), encoding="utf-8")

    if isinstance(stdin_payload, dict):
        stdin_payload = json.dumps(stdin_payload, ensure_ascii=False)
    return subprocess.run(
        [sys.executable, str(dst)],
        input=stdin_payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )


def test_cli_webfetch_event_appends_to_session(tmp_path: Path):
    r = _run_hook(
        {"tool_name": "WebFetch",
         "tool_input": {"url": "https://example.com/cli-fetch"},
         "tool_response": {}},
        tmp_path,
    )
    assert r.returncode == 0, f"hook should exit 0.\nstderr:\n{r.stderr}"
    p = tmp_path / "data" / "_session_urls.json"
    assert p.exists(), "session ファイルが新規作成されるはず"
    saved = json.loads(p.read_text(encoding="utf-8"))
    assert saved["urls"] == ["https://example.com/cli-fetch"]


def test_cli_websearch_event_appends_to_session(tmp_path: Path):
    r = _run_hook(
        {"tool_name": "WebSearch",
         "tool_input": {"query": "openai"},
         "tool_response": {"results": [
             {"url": "https://example.com/s1", "title": "S1"},
             {"url": "https://example.com/s2", "title": "S2"},
         ]}},
        tmp_path,
    )
    assert r.returncode == 0, f"hook should exit 0.\nstderr:\n{r.stderr}"
    p = tmp_path / "data" / "_session_urls.json"
    saved = json.loads(p.read_text(encoding="utf-8"))
    assert saved["urls"] == ["https://example.com/s1", "https://example.com/s2"]


def test_cli_empty_stdin_exit_zero(tmp_path: Path):
    r = _run_hook("", tmp_path)
    assert r.returncode == 0
    assert not (tmp_path / "data" / "_session_urls.json").exists()


def test_cli_broken_json_stdin_exit_zero(tmp_path: Path):
    r = _run_hook("{not json", tmp_path)
    assert r.returncode == 0, (
        f"壊れた JSON でも hook は exit 0 で落ちない契約 (claude を止めないため)\n"
        f"stderr:\n{r.stderr}"
    )
    assert not (tmp_path / "data" / "_session_urls.json").exists()


def test_cli_unrelated_tool_does_not_create_file(tmp_path: Path):
    r = _run_hook(
        {"tool_name": "Bash",
         "tool_input": {"command": "echo hi"},
         "tool_response": {"stdout": "hi"}},
        tmp_path,
    )
    assert r.returncode == 0
    assert not (tmp_path / "data" / "_session_urls.json").exists(), (
        "対象外ツールでは session ファイルを作らない契約"
    )
