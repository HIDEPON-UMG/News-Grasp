#!/usr/bin/env python3
"""Codex web_search 用 session URL hook adapter の契約テスト。"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK_PATH = ROOT / ".codex" / "hooks" / "append_session_urls.py"
HOOKS_JSON = ROOT / ".codex" / "hooks.json"


def _load_hook_module():
    spec = importlib.util.spec_from_file_location("codex_append_session_urls", str(HOOK_PATH))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_codex_hooks_json_matches_native_web_search() -> None:
    data = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
    entries = data["hooks"]["PostToolUse"]
    assert any(entry["matcher"] == "^web_search$" for entry in entries)
    command = entries[0]["hooks"][0]
    assert "commandWindows" in command
    assert "C:\\Users\\" not in command["commandWindows"]
    assert "CLAUDE_PROJECT_DIR" not in json.dumps(data)


def test_extract_codex_web_search_urls() -> None:
    hook = _load_hook_module()
    ev = {
        "tool_name": "web_search",
        "tool_response": {
            "results": [
                {"url": "https://example.com/a", "title": "A"},
                {"url": "https://example.com/b", "title": "B"},
            ]
        },
    }
    assert hook.extract_urls_from_event(ev) == {"https://example.com/a", "https://example.com/b"}


def test_extract_codex_web_search_urls_from_camel_case_payload() -> None:
    hook = _load_hook_module()
    ev = {
        "toolName": "WebSearch",
        "toolResponse": {
            "results": [
                {"url": "https://example.com/camel"},
            ]
        },
    }
    assert hook.extract_urls_from_event(ev) == {"https://example.com/camel"}


def test_codex_cli_event_writes_fragment(tmp_path: Path) -> None:
    hooks_dir = tmp_path / ".codex" / "hooks"
    hooks_dir.mkdir(parents=True)
    dst = hooks_dir / "append_session_urls.py"
    dst.write_text(HOOK_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    payload = {
        "tool_name": "web_search",
        "tool_response": {"results": [{"url": "https://example.com/codex"}]},
    }

    result = subprocess.run(
        [sys.executable, str(dst)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )

    today = date.today().strftime("%Y-%m-%d")
    frag_dir = tmp_path / "data" / "_session_urls.d" / today
    urls = []
    for frag in frag_dir.glob("*.json"):
        urls.extend(json.loads(frag.read_text(encoding="utf-8")).get("urls", []))
    assert result.returncode == 0
    assert urls == ["https://example.com/codex"]
