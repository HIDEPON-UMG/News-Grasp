#!/usr/bin/env python3
"""News-Grasp の Codex モデル選定ポリシー。"""
from __future__ import annotations

LUNA_MODEL = "gpt-5.6-luna"
LUNA_REASONING = "high"

DEFAULT_MODEL_POLICY: dict[str, dict[str, object]] = {
    "reporter": {
        "default": LUNA_MODEL,
        "escalate": LUNA_MODEL,
        "selection_variant": "reporter-56-luna-high",
        "selection_combo": "luna-high-unified",
        "selection_summary": "build/model-comparison-20260715-luna-high-replacement/summary.json",
        "selection_source": "luna_high_replacement_benchmark_2026_07_15",
        "reasoning": LUNA_REASONING,
        "escalate_reasoning": LUNA_REASONING,
        "always_escalate_categories": [],
        "min_candidate_count": 3,
        "english_ratio_threshold": 0.7,
    },
    "editor": {
        "default": LUNA_MODEL,
        "escalate": LUNA_MODEL,
        "selection_variant": "style-editor-56-luna-high",
        "selection_summary": "build/model-comparison-20260715-luna-high-replacement/summary.json",
        "selection_source": "luna_high_replacement_benchmark_2026_07_15",
        "previous_selection_source": "full__mini-editor",
        "scope": "style_rewrite_only",
        "mode": "selective_rewrite",
        "rewrite_all": False,
        "min_naturalness_score": 4,
        "min_style_score": 4,
        "reasoning": LUNA_REASONING,
        "escalate_reasoning": LUNA_REASONING,
    },
    "repair": {
        "default": LUNA_MODEL,
        "escalate": LUNA_MODEL,
        "selection_source": "model_retirement_luna_high_policy_2026_07_16",
        "scope": "llm_repair_worker",
        "reasoning": LUNA_REASONING,
        "escalate_reasoning": LUNA_REASONING,
        "escalation_thresholds": {
            "issue_count_above": 1,
        },
        "escalate_when": [
            "compound issue ledger requires repair judgment",
            "previous classify failed before repair",
            "scope ambiguity or mismatch is detected",
            "missing artifact generation is required",
            "compound gate failure requires bounded recovery",
        ],
    },
    "newsroom_editor": {
        "default": LUNA_MODEL,
        "escalate": LUNA_MODEL,
        "selection_status": "selected",
        "selection_variant": "newsroom-editor-56-luna-high",
        "quality_leader_variant": "newsroom-editor-56-luna-high",
        "selection_summary": "build/model-comparison-20260715-luna-high-replacement/summary.json",
        "safety_summary": "build/model-eval-5.6/newsroom-append-safety/summary.json",
        "selection_source": "luna_high_replacement_benchmark_2026_07_15",
        "previous_selection_source": "tts_script_quality_override_2026_07_02",
        "candidate_variants": [
            "newsroom-editor-56-luna-high",
        ],
        "reasoning": LUNA_REASONING,
        "escalate_reasoning": LUNA_REASONING,
        "escalation_thresholds": {
            "gate_fail_count": 1,
            "dedup_conflict_count": 1,
            "summary_quality_score_below": 4,
            "deepdive_theme_count_above": 1,
        },
        "escalate_when": [
            "reporter gate failures require repair judgment",
            "cross-category dedup conflicts require article/card surgery",
            "Summary planning quality falls below floor",
            "append card/record/count mismatch is detected",
            "multiple DeepDive theme candidates require prioritization",
        ],
    },
    "deepdive": {
        "default": "gpt-5.6-sol",
        "selection_summary": "build/model-eval-5.6/deepdive-triad-judge/summary.json",
        "selection_source": "weighted_triad_benchmark_2026_07_10",
        "reasoning": "high",
    },
}


def should_escalate_reporter(
    *,
    category: str,
    candidate_count: int,
    english_ratio: float,
    validator_failed: bool,
) -> bool:
    """記者モデルの昇格要否をカテゴリ固定ではなく条件で判定する。"""
    reporter = DEFAULT_MODEL_POLICY["reporter"]
    always = set(reporter.get("always_escalate_categories", []))
    if category.casefold() in {str(v).casefold() for v in always}:
        return True
    if validator_failed:
        return True
    if candidate_count < int(reporter["min_candidate_count"]):
        return True
    if english_ratio >= float(reporter["english_ratio_threshold"]):
        return True
    return False


def should_rewrite_with_editor(
    *,
    naturalness_score: int,
    style_score: int,
    validator_failed: bool,
) -> bool:
    """編集長文体調整を全件ではなく低品質・検証失敗記事だけに限定する。"""
    editor = DEFAULT_MODEL_POLICY["editor"]
    if validator_failed:
        return True
    if bool(editor.get("rewrite_all", False)):
        return True
    if naturalness_score < int(editor["min_naturalness_score"]):
        return True
    if style_score < int(editor["min_style_score"]):
        return True
    return False


def should_escalate_repair(
    *,
    issue_count: int,
    previous_classify_failed: bool,
    scope_ambiguous: bool,
    missing_artifact_generation: bool,
    compound_gate_failure: bool,
) -> bool:
    """repair worker は文体 editor ではなく修復判断用 role で昇格要否を判定する。"""
    repair = DEFAULT_MODEL_POLICY["repair"]
    thresholds = repair["escalation_thresholds"]
    if issue_count > int(thresholds["issue_count_above"]):
        return True
    if previous_classify_failed:
        return True
    if scope_ambiguous:
        return True
    if missing_artifact_generation:
        return True
    if compound_gate_failure:
        return True
    return False


def select_repair_model(
    *,
    issue_count: int,
    previous_classify_failed: bool,
    scope_ambiguous: bool,
    missing_artifact_generation: bool,
    compound_gate_failure: bool,
) -> str:
    """LLM repair worker 用モデルを返す。mini editor default は使わない。"""
    policy = DEFAULT_MODEL_POLICY["repair"]
    if should_escalate_repair(
        issue_count=issue_count,
        previous_classify_failed=previous_classify_failed,
        scope_ambiguous=scope_ambiguous,
        missing_artifact_generation=missing_artifact_generation,
        compound_gate_failure=compound_gate_failure,
    ):
        return str(policy["escalate"])
    return str(policy["default"])


def should_escalate_newsroom_editor(
    *,
    gate_fail_count: int,
    dedup_conflict_count: int,
    append_mismatch: bool,
    summary_quality_score: int,
    deepdive_theme_count: int,
) -> bool:
    """編集長モデルを quality leader へ昇格するか機械シグナルで判定する。"""
    policy = DEFAULT_MODEL_POLICY["newsroom_editor"]
    thresholds = policy["escalation_thresholds"]
    if gate_fail_count >= int(thresholds["gate_fail_count"]):
        return True
    if dedup_conflict_count >= int(thresholds["dedup_conflict_count"]):
        return True
    if append_mismatch:
        return True
    if summary_quality_score < int(thresholds["summary_quality_score_below"]):
        return True
    if deepdive_theme_count > int(thresholds["deepdive_theme_count_above"]):
        return True
    return False


def select_newsroom_editor_model(
    *,
    gate_fail_count: int,
    dedup_conflict_count: int,
    append_mismatch: bool,
    summary_quality_score: int,
    deepdive_theme_count: int,
) -> str:
    """通常日は既定モデル、重い編集判断日は昇格モデルを返す。"""
    policy = DEFAULT_MODEL_POLICY["newsroom_editor"]
    if should_escalate_newsroom_editor(
        gate_fail_count=gate_fail_count,
        dedup_conflict_count=dedup_conflict_count,
        append_mismatch=append_mismatch,
        summary_quality_score=summary_quality_score,
        deepdive_theme_count=deepdive_theme_count,
    ):
        return str(policy["escalate"])
    return str(policy["default"])
