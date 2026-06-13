#!/usr/bin/env python3
"""News-Grasp の Codex モデル選定ポリシー。"""
from __future__ import annotations

DEFAULT_MODEL_POLICY: dict[str, dict[str, object]] = {
    "reporter": {
        "default": "gpt-5.4-mini",
        "escalate": "gpt-5.4",
        "reasoning": "low",
        "escalate_reasoning": "medium",
        "always_escalate_categories": [],
        "min_candidate_count": 3,
        "english_ratio_threshold": 0.7,
    },
    "editor": {
        "default": "gpt-5.4-mini",
        "escalate": "gpt-5.4",
        "mode": "selective_rewrite",
        "rewrite_all": False,
        "min_naturalness_score": 4,
        "min_style_score": 4,
        "reasoning": "medium",
    },
    "deepdive": {
        "default": "gpt-5.5",
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
    """記者モデルを gpt-5.4 へ昇格するかをカテゴリ固定ではなく条件で判定する。"""
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
