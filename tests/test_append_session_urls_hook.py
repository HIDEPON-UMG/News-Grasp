#!/usr/bin/env python3
"""案②-Lite 案③: PostToolUse hook (`.claude/hooks/append_session_urls.py`) 契約テスト。

# 検証する「なぜ重要か」

LLM の規律破り (= prompts で「session ファイル書け」と命じても破る) を構造的に封じる
ため、Claude Code ハーネスが WebSearch / WebFetch 後に本 hook を発火させ、ハーネス層
が直接 session 白リストに URL を append する設計。2026-06-12 にフラグメント方式
(発火 1 回 = `data/_session_urls.d/{date}/{uuid}.json` を 1 ファイル新規作成) へ移行し、
並列発火の後勝ち消失 race を構造的に消した。本テストは以下を locked-in:

  1. WebFetch event の `tool_input.url` が拾われてフラグメントに入る
  2. WebSearch event の `tool_response.results[].url` が拾われてフラグメントに入る
  3. WebSearch event の results 不明形式でも JSON dump → 正規表現フォールバックで URL を拾う
  4. write_fragment が発火ごとに別ファイル (uuid) を新規作成し、相互上書きしない
  5. URL 0 件ではフラグメントを作らない
  6. 不正な stdin (空 / 壊れた JSON) でも例外で落ちず exit 0

実行:
  pytest tests/test_append_session_urls_hook.py -v
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import date
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


# ── write_fragment の挙動 (フラグメント方式) ──────────────────────────────────


def test_write_fragment_creates_file(tmp_path: Path):
    """発火 1 回でフラグメント 1 ファイルが当日ディレクトリ配下に作られる。"""
    fragments_root = tmp_path / "data" / "_session_urls.d"
    today = date.today().strftime("%Y-%m-%d")
    frag = hook.write_fragment({"https://example.com/a"}, today, fragments_root)
    assert frag.parent == fragments_root / today
    saved = json.loads(frag.read_text(encoding="utf-8"))
    assert saved == {"date": today, "urls": ["https://example.com/a"]}


def test_write_fragment_sorts_urls(tmp_path: Path):
    """フラグメント内の urls は sorted で書かれる (照合の決定性確保)。"""
    fragments_root = tmp_path / "data" / "_session_urls.d"
    today = date.today().strftime("%Y-%m-%d")
    frag = hook.write_fragment(
        {"https://example.com/b", "https://example.com/a"}, today, fragments_root
    )
    saved = json.loads(frag.read_text(encoding="utf-8"))
    assert saved["urls"] == ["https://example.com/a", "https://example.com/b"]


def test_write_fragment_two_fires_no_overwrite(tmp_path: Path):
    """2 連続発火で別ファイルが作られ、相互上書きしない (= 並列 race を表現不能化)。"""
    fragments_root = tmp_path / "data" / "_session_urls.d"
    today = date.today().strftime("%Y-%m-%d")
    f1 = hook.write_fragment({"https://example.com/a"}, today, fragments_root)
    f2 = hook.write_fragment({"https://example.com/b"}, today, fragments_root)
    assert f1 != f2
    assert json.loads(f1.read_text(encoding="utf-8"))["urls"] == ["https://example.com/a"]
    assert json.loads(f2.read_text(encoding="utf-8"))["urls"] == ["https://example.com/b"]
    assert len(list((fragments_root / today).glob("*.json"))) == 2


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


def _read_fragment_urls(tmp_repo: Path) -> list[str]:
    """当日フラグメントディレクトリの全 *.json を union した urls を sorted で返す。"""
    today = date.today().strftime("%Y-%m-%d")
    frag_dir = tmp_repo / "data" / "_session_urls.d" / today
    urls: set[str] = set()
    for frag in frag_dir.glob("*.json"):
        urls |= set(json.loads(frag.read_text(encoding="utf-8")).get("urls", []))
    return sorted(urls)


def _fragment_dir_exists(tmp_repo: Path) -> bool:
    today = date.today().strftime("%Y-%m-%d")
    return (tmp_repo / "data" / "_session_urls.d" / today).exists()


def test_cli_webfetch_event_appends_to_session(tmp_path: Path):
    r = _run_hook(
        {"tool_name": "WebFetch",
         "tool_input": {"url": "https://example.com/cli-fetch"},
         "tool_response": {}},
        tmp_path,
    )
    assert r.returncode == 0, f"hook should exit 0.\nstderr:\n{r.stderr}"
    assert _fragment_dir_exists(tmp_path), "当日フラグメントディレクトリが作られるはず"
    assert _read_fragment_urls(tmp_path) == ["https://example.com/cli-fetch"]


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
    assert _read_fragment_urls(tmp_path) == ["https://example.com/s1", "https://example.com/s2"]


def test_cli_empty_stdin_exit_zero(tmp_path: Path):
    r = _run_hook("", tmp_path)
    assert r.returncode == 0
    assert not _fragment_dir_exists(tmp_path)


def test_cli_broken_json_stdin_exit_zero(tmp_path: Path):
    r = _run_hook("{not json", tmp_path)
    assert r.returncode == 0, (
        f"壊れた JSON でも hook は exit 0 で落ちない契約 (claude を止めないため)\n"
        f"stderr:\n{r.stderr}"
    )
    assert not _fragment_dir_exists(tmp_path)


def test_cli_writes_audit_log_on_fire(tmp_path: Path):
    """hook 発火時は data/_session_urls.audit.log に痕跡が書かれる契約。

    2026-06-05 朝バッチで hook が一度も発火しなかった事故の証拠取り用。
    audit log の更新有無で「hook が呼ばれたか」「呼ばれたが URL を抽出できなかったか」
    の区別がつく必要がある。
    """
    r = _run_hook(
        {"tool_name": "WebSearch",
         "tool_input": {"query": "x"},
         "tool_response": {"results": [{"url": "https://example.com/audit"}]}},
        tmp_path,
    )
    assert r.returncode == 0
    audit = tmp_path / "data" / "_session_urls.audit.log"
    assert audit.exists(), "発火痕跡 audit log が書かれるはず"
    text = audit.read_text(encoding="utf-8")
    assert "tool=WebSearch" in text
    assert "urls=1" in text


def test_cli_writes_audit_log_on_empty_extract(tmp_path: Path):
    """URL を抽出できなかった発火でも audit log に痕跡が残る契約 (= 沈黙発火を可視化)。"""
    r = _run_hook(
        {"tool_name": "WebSearch",
         "tool_input": {"query": "x"},
         "tool_response": {}},  # 結果空 = 抽出 0 件
        tmp_path,
    )
    assert r.returncode == 0
    audit = tmp_path / "data" / "_session_urls.audit.log"
    assert audit.exists(), "URL 0 件でも発火痕跡は残る契約"
    text = audit.read_text(encoding="utf-8")
    assert "urls=0" in text or "no_urls_extracted" in text


def test_cli_unrelated_tool_does_not_create_file(tmp_path: Path):
    r = _run_hook(
        {"tool_name": "Bash",
         "tool_input": {"command": "echo hi"},
         "tool_response": {"stdout": "hi"}},
        tmp_path,
    )
    assert r.returncode == 0
    assert not _fragment_dir_exists(tmp_path), (
        "対象外ツールではフラグメントを作らない契約"
    )
