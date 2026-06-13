#!/usr/bin/env python3
"""モデル選定と実記事評価 fixture の契約テスト。"""
from __future__ import annotations

import json
from pathlib import Path

from tools.model_policy import (
    DEFAULT_MODEL_POLICY,
    should_escalate_reporter,
    should_rewrite_with_editor,
)
from tools.prepare_model_eval_fixture import build_eval_fixture


def _record(cat: str, idx: int) -> dict:
    return {
        "date": "2026-06-13",
        "genre": cat,
        "title": f"{cat} title {idx}",
        "title_ja": f"{cat} title {idx}",
        "url": f"https://example.com/{cat}/{idx}",
        "source": "Example",
        "summary": "summary",
        "thumb": None,
    }


def test_default_model_policy_uses_mini_for_reporters() -> None:
    assert DEFAULT_MODEL_POLICY["reporter"]["default"] == "gpt-5.4-mini"
    assert DEFAULT_MODEL_POLICY["reporter"]["escalate"] == "gpt-5.4"
    assert "fx" not in DEFAULT_MODEL_POLICY["reporter"].get("always_escalate_categories", [])


def test_reporter_escalation_is_condition_based_not_category_based() -> None:
    assert should_escalate_reporter(category="fx", candidate_count=10, english_ratio=0.2, validator_failed=False) is False
    assert should_escalate_reporter(category="ai", candidate_count=2, english_ratio=0.2, validator_failed=False) is True
    assert should_escalate_reporter(category="game", candidate_count=10, english_ratio=0.8, validator_failed=False) is True
    assert should_escalate_reporter(category="mobility", candidate_count=10, english_ratio=0.2, validator_failed=True) is True


def test_editor_policy_adopts_mini_editor_without_full_rewrite_by_default() -> None:
    editor = DEFAULT_MODEL_POLICY["editor"]
    assert editor["default"] == "gpt-5.4-mini"
    assert editor["mode"] == "selective_rewrite"
    assert editor["rewrite_all"] is False
    assert should_rewrite_with_editor(naturalness_score=3, style_score=5, validator_failed=False) is True
    assert should_rewrite_with_editor(naturalness_score=4, style_score=4, validator_failed=False) is False
    assert should_rewrite_with_editor(naturalness_score=5, style_score=5, validator_failed=True) is True


def test_build_eval_fixture_samples_three_per_category(tmp_path: Path) -> None:
    jsonl = tmp_path / "data" / "articles.jsonl"
    jsonl.parent.mkdir()
    rows = []
    for cat in ["FX", "AI", "IT-Consulting", "Mobility", "Game", "Manufacturing", "Economy"]:
        rows.extend(_record(cat, i) for i in range(5))
    jsonl.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")

    fixture = build_eval_fixture(jsonl, per_category=3)

    assert len(fixture["items"]) == 21
    counts: dict[str, int] = {}
    for item in fixture["items"]:
        counts[item["genre"]] = counts.get(item["genre"], 0) + 1
    assert set(counts.values()) == {3}
