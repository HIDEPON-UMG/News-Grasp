#!/usr/bin/env python3
"""articles.jsonl 追記境界 (`tools/append_after_dedup.py`) の契約テスト。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import append_after_dedup  # type: ignore  # noqa: E402


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def test_append_boundary_runs_dedup_before_writing(tmp_path: Path) -> None:
    """同一 URL は境界スクリプトで drop され、articles.jsonl に追記されない。"""
    jsonl = tmp_path / "articles.jsonl"
    existing = {
        "date": "2026-06-05",
        "seen_at": "2026-06-05T06:00:00+09:00",
        "genre": "AI",
        "title": "Existing CNBC item",
        "url": "https://www.cnbc.com/2026/06/01/microsoft-and-google-take-on-anthropic-and-openai-in-ai-coding-models.html",
    }
    jsonl.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")

    candidate = {
        "date": "2026-06-07",
        "genre": "AI",
        "title": "Microsoft and Google take on Anthropic and OpenAI in AI coding models",
        "url": "https://www.cnbc.com/2026/06/01/microsoft-and-google-take-on-anthropic-and-openai-in-ai-coding-models.html?utm_source=x",
        "score": 91,
    }
    passed, dropped = append_after_dedup.filter_records(
        [candidate],
        _read_jsonl(jsonl),
        window_hours=24.0,
        title_threshold=0.42,
        followup_gate=True,
        freshness_gate=False,
        max_source_age_days=7,
    )
    append_after_dedup.append_records(jsonl, passed)

    rows = _read_jsonl(jsonl)
    assert len(passed) == 0
    assert len(dropped) == 1
    assert "url match" in dropped[0]["dedup_reason"]
    assert len(rows) == 1


def test_append_boundary_drops_stale_source_date(tmp_path: Path) -> None:
    """日付付き URL の発行日が古い候補は append 前に drop される。"""
    jsonl = tmp_path / "articles.jsonl"
    jsonl.write_text("", encoding="utf-8")
    candidate = {
        "date": "2026-06-07",
        "genre": "AI",
        "title": "Old article should not be a today item",
        "url": "https://www.cnbc.com/2026/02/17/old-ai-news.html",
        "score": 80,
    }
    passed, dropped = append_after_dedup.filter_records(
        [candidate],
        [],
        window_hours=24.0,
        title_threshold=0.42,
        followup_gate=True,
        freshness_gate=True,
        max_source_age_days=7,
    )
    append_after_dedup.append_records(jsonl, passed)

    assert passed == []
    assert len(dropped) == 1
    assert "freshness gate" in dropped[0]["dedup_reason"]
    assert jsonl.read_text(encoding="utf-8") == ""
