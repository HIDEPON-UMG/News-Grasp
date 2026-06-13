#!/usr/bin/env python3
"""モデル評価 runner/集計の契約テスト。"""
from __future__ import annotations

import json
from pathlib import Path

from tools.prepare_model_eval_fixture import CANONICAL_GENRES, build_eval_fixture
from tools.run_model_eval import aggregate_scores, build_prompt


def _record(cat: str, idx: int) -> dict:
    return {
        "date": "2026-06-13",
        "genre": cat,
        "title": f"{cat} source title {idx}",
        "title_ja": f"{cat} 日本語タイトル {idx}",
        "url": f"https://example.com/{cat}/{idx}",
        "source": "Example",
        "summary": f"{cat} summary {idx}",
        "bullets": [f"{cat} bullet {idx}"],
    }


def test_build_eval_fixture_uses_canonical_genres_and_title_ja(tmp_path: Path) -> None:
    jsonl = tmp_path / "articles.jsonl"
    rows = []
    for cat in CANONICAL_GENRES:
        rows.extend(_record(cat, i) for i in range(5))
    rows.append(_record("Foreign Exchange", 1))
    rows.append({**_record("AI", 99), "title_ja": ""})
    jsonl.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")

    fixture = build_eval_fixture(jsonl, per_category=3)

    assert len(fixture["items"]) == 21
    assert {item["genre"] for item in fixture["items"]} == set(CANONICAL_GENRES)
    assert all(item["title_ja"] for item in fixture["items"])


def test_model_eval_prompt_contains_variant_and_fixture() -> None:
    fixture = {"version": 1, "items": [_record("AI", 1)]}
    prompt = build_prompt(
        instruction="# Instruction\nReturn JSON.",
        fixture=fixture,
        variant="mini-reporter",
        model="gpt-5.4-mini",
    )
    assert "mini-reporter" in prompt
    assert "gpt-5.4-mini" in prompt
    assert '"items"' in prompt


def test_aggregate_scores_selects_best_cost_adjusted_variant(tmp_path: Path) -> None:
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    for name, naturalness, cost in [
        ("mini", 3, 1.0),
        ("full", 5, 3.3),
        ("mini-editor", 4, 1.6),
    ]:
        (results_dir / f"{name}.json").write_text(json.dumps({
            "model": name,
            "cost_weight": cost,
            "items": [{
                "url": "https://example.com/1",
                "title_ja": "title",
                "summary": "summary",
                "bullets": ["bullet"],
                "self_score": {
                    "fact_retention": 5,
                    "naturalness": naturalness,
                    "news_grasp_style": naturalness,
                    "compression": 4,
                    "emphasis_ready": 4,
                },
            }],
        }), encoding="utf-8")

    report = aggregate_scores(results_dir)

    assert report["recommended_variant"] == "mini-editor"
    assert report["variants"]["mini"]["item_count"] == 1
    assert report["variants"]["full"]["cost_weight"] == 3.3

