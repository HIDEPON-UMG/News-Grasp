#!/usr/bin/env python3
"""モデル選定と実記事評価 fixture の契約テスト。"""
from __future__ import annotations

import json
from pathlib import Path

from tools.model_policy import (
    DEFAULT_MODEL_POLICY,
    select_repair_model,
    select_newsroom_editor_model,
    should_escalate_reporter,
    should_escalate_repair,
    should_escalate_newsroom_editor,
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


def test_default_model_policy_uses_evaluated_reporter_selection() -> None:
    assert DEFAULT_MODEL_POLICY["reporter"]["default"] == "gpt-5.6-luna"
    assert DEFAULT_MODEL_POLICY["reporter"]["escalate"] == "gpt-5.6-luna"
    assert DEFAULT_MODEL_POLICY["reporter"]["reasoning"] == "max"
    assert DEFAULT_MODEL_POLICY["reporter"]["escalate_reasoning"] == "max"
    assert "fx" not in DEFAULT_MODEL_POLICY["reporter"].get("always_escalate_categories", [])


def test_reporter_escalation_is_condition_based_not_category_based() -> None:
    assert should_escalate_reporter(category="fx", candidate_count=10, english_ratio=0.2, validator_failed=False) is False
    assert should_escalate_reporter(category="ai", candidate_count=2, english_ratio=0.2, validator_failed=False) is True
    assert should_escalate_reporter(category="game", candidate_count=10, english_ratio=0.8, validator_failed=False) is True
    assert should_escalate_reporter(category="mobility", candidate_count=10, english_ratio=0.2, validator_failed=True) is True


def test_editor_policy_adopts_evaluated_editor_without_full_rewrite_by_default() -> None:
    editor = DEFAULT_MODEL_POLICY["editor"]
    assert editor["default"] == "gpt-5.6-luna"
    assert editor["escalate"] == "gpt-5.6-luna"
    assert editor["reasoning"] == "max"
    assert editor["escalate_reasoning"] == "max"
    assert editor["selection_variant"] == "style-editor-56-luna-max"
    assert editor["selection_summary"] == "build/model-comparison-20260715-luna-high-replacement/summary.json"
    assert editor["selection_source"] == "luna_max_scheduled_direct_mainline_2026_08_30"
    assert editor["scope"] == "style_rewrite_only"
    assert editor["mode"] == "selective_rewrite"
    assert editor["rewrite_all"] is False
    assert should_rewrite_with_editor(naturalness_score=3, style_score=5, validator_failed=False) is True
    assert should_rewrite_with_editor(naturalness_score=4, style_score=4, validator_failed=False) is False
    assert should_rewrite_with_editor(naturalness_score=5, style_score=5, validator_failed=True) is True


def test_repair_policy_uses_luna_max_for_llm_repair_worker() -> None:
    """repair は文体 editor ではなく、修復判断用 role のモデルを使う。"""
    repair = DEFAULT_MODEL_POLICY["repair"]
    assert repair["default"] == "gpt-5.6-luna"
    assert repair["escalate"] == "gpt-5.6-luna"
    assert repair["reasoning"] == "max"
    assert repair["escalate_reasoning"] == "max"
    assert repair["scope"] == "llm_repair_worker"
    assert "mini" not in str(repair["default"])
    assert select_repair_model(
        issue_count=1,
        previous_classify_failed=False,
        scope_ambiguous=False,
        missing_artifact_generation=True,
        compound_gate_failure=False,
    ) == "gpt-5.6-luna"


def test_repair_model_escalates_complex_patterns_to_luna_max() -> None:
    """複雑な repair pattern も Luna の max effort へ統一する。"""
    cases = [
        (
            "compound issue ledger",
            dict(
                issue_count=2,
                previous_classify_failed=False,
                scope_ambiguous=False,
                missing_artifact_generation=False,
                compound_gate_failure=False,
            ),
        ),
        (
            "previous classify failure",
            dict(
                issue_count=1,
                previous_classify_failed=True,
                scope_ambiguous=False,
                missing_artifact_generation=False,
                compound_gate_failure=False,
            ),
        ),
        (
            "scope ambiguity",
            dict(
                issue_count=1,
                previous_classify_failed=False,
                scope_ambiguous=True,
                missing_artifact_generation=False,
                compound_gate_failure=False,
            ),
        ),
        (
            "missing artifact generation",
            dict(
                issue_count=1,
                previous_classify_failed=False,
                scope_ambiguous=False,
                missing_artifact_generation=True,
                compound_gate_failure=False,
            ),
        ),
        (
            "compound gate failure",
            dict(
                issue_count=1,
                previous_classify_failed=False,
                scope_ambiguous=False,
                missing_artifact_generation=False,
                compound_gate_failure=True,
            ),
        ),
    ]

    for label, signals in cases:
        assert should_escalate_repair(**signals) is True, label
        assert select_repair_model(**signals) == "gpt-5.6-luna", label


def test_newsroom_editor_policy_uses_luna_max() -> None:
    """編集長生成は Luna の max effort へ統一する。"""
    newsroom_editor = DEFAULT_MODEL_POLICY["newsroom_editor"]
    assert newsroom_editor["selection_summary"] == "build/model-comparison-20260715-luna-high-replacement/summary.json"
    assert newsroom_editor["safety_summary"] == "build/model-eval-5.6/newsroom-append-safety/summary.json"
    assert newsroom_editor["selection_status"] == "selected"
    assert newsroom_editor["default"] == "gpt-5.6-luna"
    assert newsroom_editor["selection_variant"] == "newsroom-editor-56-luna-max"
    assert newsroom_editor["quality_leader_variant"] == "newsroom-editor-56-luna-max"
    assert newsroom_editor["escalate"] == "gpt-5.6-luna"
    assert newsroom_editor["reasoning"] == "max"
    assert newsroom_editor["escalate_reasoning"] == "max"
    assert DEFAULT_MODEL_POLICY["deepdive"]["default"] == "gpt-5.6-sol"
    assert DEFAULT_MODEL_POLICY["deepdive"]["selection_source"] == "weighted_triad_benchmark_2026_07_10"


def test_newsroom_editor_escalation_uses_machine_signals() -> None:
    """編集長の昇格は曖昧な気分ではなく runner が出せる機械シグナルで決める。"""
    assert should_escalate_newsroom_editor(
        gate_fail_count=0,
        dedup_conflict_count=0,
        append_mismatch=False,
        summary_quality_score=5,
        deepdive_theme_count=1,
    ) is False
    assert should_escalate_newsroom_editor(
        gate_fail_count=2,
        dedup_conflict_count=0,
        append_mismatch=False,
        summary_quality_score=5,
        deepdive_theme_count=1,
    ) is True
    assert should_escalate_newsroom_editor(
        gate_fail_count=0,
        dedup_conflict_count=1,
        append_mismatch=False,
        summary_quality_score=5,
        deepdive_theme_count=1,
    ) is True
    assert should_escalate_newsroom_editor(
        gate_fail_count=0,
        dedup_conflict_count=0,
        append_mismatch=True,
        summary_quality_score=5,
        deepdive_theme_count=1,
    ) is True
    assert should_escalate_newsroom_editor(
        gate_fail_count=0,
        dedup_conflict_count=0,
        append_mismatch=False,
        summary_quality_score=3,
        deepdive_theme_count=1,
    ) is True


def test_select_newsroom_editor_model_returns_default_or_quality_leader() -> None:
    assert select_newsroom_editor_model(
        gate_fail_count=0,
        dedup_conflict_count=0,
        append_mismatch=False,
        summary_quality_score=5,
        deepdive_theme_count=1,
    ) == "gpt-5.6-luna"
    assert select_newsroom_editor_model(
        gate_fail_count=1,
        dedup_conflict_count=0,
        append_mismatch=False,
        summary_quality_score=5,
        deepdive_theme_count=1,
    ) == "gpt-5.6-luna"


def test_operational_prompts_match_selected_model_policy() -> None:
    runner_prompt = Path("prompts/runner-prompt.md").read_text(encoding="utf-8-sig")
    newsroom_prompt = Path("prompts/newsroom-editor-system.md").read_text(encoding="utf-8-sig")
    deepdive_prompt = Path("prompts/deepdive-research-system.md").read_text(encoding="utf-8-sig")

    assert "gpt-5.6-luna" in runner_prompt
    assert "gpt-5.6-luna" in runner_prompt
    assert "high" in runner_prompt.casefold()
    assert "gpt-5.6-sol" in newsroom_prompt
    assert "gpt-5.6-sol" in deepdive_prompt
    assert "gpt-5.4-mini" not in runner_prompt
    assert "gpt-5.4" not in runner_prompt
    assert "gpt-5.6-terra" not in runner_prompt
    assert "gpt-5.4" not in newsroom_prompt
    assert "gpt-5.6-terra" not in newsroom_prompt
    assert "gpt-5.5" not in deepdive_prompt


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
