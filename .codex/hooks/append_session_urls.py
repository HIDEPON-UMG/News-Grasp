#!/usr/bin/env python3
"""Codex PostToolUse hook: web_search で観測した URL を session fragment に書く。"""
from __future__ import annotations

import json
import re
import sys
import uuid
from datetime import date, datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_FRAGMENTS_ROOT = _REPO_ROOT / "data" / "_session_urls.d"
_AUDIT_LOG = _REPO_ROOT / "data" / "_session_urls.audit.log"
_URL_RE = re.compile(r"https?://[^\s\"<>\)\]\}]+")
_URL_TRAIL_PUNCT = ".,;:!?)」』』”'\""


def _strip_tail(url: str) -> str:
    return url.rstrip(_URL_TRAIL_PUNCT)


def extract_urls_from_event(event: dict) -> set[str]:
    """Codex hook payload から URL を抽出する。"""
    tool_name = str(event.get("tool_name") or "")
    tool_input = event.get("tool_input") or {}
    tool_response = event.get("tool_response")
    urls: set[str] = set()

    if tool_name in {"web_fetch", "fetch"} and isinstance(tool_input, dict):
        url = tool_input.get("url")
        if isinstance(url, str) and url.startswith("http"):
            urls.add(_strip_tail(url))
        return urls

    if tool_name != "web_search":
        return urls

    if isinstance(tool_response, dict):
        results = tool_response.get("results")
        if isinstance(results, list):
            for result in results:
                if isinstance(result, dict):
                    url = result.get("url")
                    if isinstance(url, str) and url.startswith("http"):
                        urls.add(_strip_tail(url))
    if not urls:
        try:
            text = tool_response if isinstance(tool_response, str) else json.dumps(tool_response, ensure_ascii=False)
        except (TypeError, ValueError):
            text = ""
        for match in _URL_RE.finditer(text):
            urls.add(_strip_tail(match.group(0)))
    return urls


def write_fragment(new_urls: set[str], today_str: str, fragments_root: Path) -> Path:
    day_dir = fragments_root / today_str
    day_dir.mkdir(parents=True, exist_ok=True)
    frag = day_dir / f"{uuid.uuid4().hex}.json"
    frag.write_text(
        json.dumps({"date": today_str, "urls": sorted(new_urls)}, ensure_ascii=False),
        encoding="utf-8",
    )
    return frag


def _append_audit(tool_name: str, n_urls: int, note: str = "") -> None:
    try:
        ts = datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
        line = f"{ts} tool={tool_name} urls={n_urls}"
        if note:
            line += f" note={note}"
        _AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def main() -> int:
    try:
        raw = sys.stdin.read()
    except Exception as exc:
        print(f"codex append_session_urls: stdin read failed: {exc}", file=sys.stderr)
        _append_audit("?", 0, note=f"stdin_read_failed:{exc}")
        return 0
    if not raw.strip():
        _append_audit("?", 0, note="empty_stdin")
        return 0
    try:
        event = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"codex append_session_urls: invalid stdin JSON: {exc}", file=sys.stderr)
        _append_audit("?", 0, note=f"invalid_json:{exc}")
        return 0
    if not isinstance(event, dict):
        _append_audit("?", 0, note="event_not_dict")
        return 0

    tool_name = str(event.get("tool_name") or "?")
    try:
        urls = extract_urls_from_event(event)
    except Exception as exc:
        print(f"codex append_session_urls: extract failed: {exc}", file=sys.stderr)
        _append_audit(tool_name, 0, note=f"extract_failed:{exc}")
        return 0
    if not urls:
        _append_audit(tool_name, 0, note="no_urls_extracted")
        return 0
    try:
        write_fragment(urls, date.today().strftime("%Y-%m-%d"), _FRAGMENTS_ROOT)
        _append_audit(tool_name, len(urls))
    except Exception as exc:
        print(f"codex append_session_urls: write failed: {exc}", file=sys.stderr)
        _append_audit(tool_name, len(urls), note=f"write_failed:{exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
