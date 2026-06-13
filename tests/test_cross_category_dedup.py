#!/usr/bin/env python3
"""横断 dedup coordinator の契約テスト。"""
from __future__ import annotations

import json
from pathlib import Path

from tools.cross_category_dedup import run_cross_category_dedup


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_cross_category_same_url_is_written_once(tmp_path: Path) -> None:
    """カテゴリ間で同じ URL が出たら LLM 前に 1 件へ統合する。"""
    input_dir = tmp_path / "candidates"
    output_dir = tmp_path / "deduped"
    _write_jsonl(input_dir / "ai.jsonl", [
        {"category": "ai", "title": "Same story", "url": "https://example.com/same?utm_source=x", "score": 90},
        {"category": "ai", "title": "OpenAI launches enterprise agent telemetry", "url": "https://example.com/ai-only", "score": 80},
    ])
    _write_jsonl(input_dir / "it.jsonl", [
        {"category": "it", "title": "Same story follow", "url": "https://example.com/same", "score": 70},
        {"category": "it", "title": "Accenture updates ERP migration playbook", "url": "https://example.com/it-only", "score": 85},
    ])
    articles = tmp_path / "articles.jsonl"
    articles.write_text("", encoding="utf-8")

    result = run_cross_category_dedup(
        input_dir=input_dir,
        output_dir=output_dir,
        articles_jsonl=articles,
        categories=["ai", "it"],
        freshness_gate=False,
        followup_gate=False,
    )

    assert result.passed == 3
    assert result.dropped == 1
    all_rows = _read_jsonl(output_dir / "all.jsonl")
    assert [row["url_norm"] for row in all_rows].count("https://example.com/same") == 1
    assert len(_read_jsonl(output_dir / "ai.jsonl")) == 2
    assert len(_read_jsonl(output_dir / "it.jsonl")) == 1
