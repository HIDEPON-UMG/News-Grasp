#!/usr/bin/env python3
"""Summary テーマ見出しの連続同型化を防ぐ編集長プロンプト契約テスト。"""
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
EDITOR_PROMPT = ROOT / "prompts" / "newsroom-editor-system.md"
ROUTINE_PROMPT = ROOT / "prompts" / "routine-system.md"
SUMMARY_DIR = ROOT / "digest" / "Summary"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def _frontmatter_value(text: str, key: str) -> str:
    match = re.search(rf"^{re.escape(key)}:\s*[\"']?(.+?)[\"']?\s*$", text, re.MULTILINE)
    assert match, f"{key} が見つからない"
    return match.group(1)


def test_recent_summary_titles_expose_repetition_pattern() -> None:
    """2026-06-20〜23 の実例から、同型化しやすい骨格を固定する。"""
    dates = ["2026-06-23", "2026-06-22", "2026-06-21", "2026-06-20"]
    summaries = [_read(SUMMARY_DIR / f"{date}.md") for date in dates]
    titles = [_frontmatter_value(text, "title") for text in summaries]
    hero_pairs = [
        (_frontmatter_value(text, "hero_left"), _frontmatter_value(text, "hero_right"))
        for text in summaries
    ]

    assert sum(" と " in title or "と" in title.split("—", 1)[-1] for title in titles) >= 3
    flat_hero = " / ".join(part for pair in hero_pairs for part in pair)
    for repeated in ["現場実装", "制御境界"]:
        assert flat_hero.count(repeated) >= 2, f"{repeated} の反復リスクを fixture で捉える"


def test_newsroom_editor_prompt_requires_three_day_theme_diversity_gate() -> None:
    prompt = _read(EDITOR_PROMPT)
    for needle in [
        "直近3日",
        "digest/Summary/{前日}.md",
        "タイトルパターン帳",
        "候補を最低3本",
        "異なる型",
        "A と B",
        "現場実装",
        "制御境界",
        "採用禁止",
    ]:
        assert needle in prompt


def test_routine_prompt_keeps_summary_theme_diversity_contract() -> None:
    prompt = _read(ROUTINE_PROMPT)
    for needle in [
        "直近3日",
        "タイトルパターン帳",
        "候補を最低3本",
        "同じ骨格",
        "採用禁止",
    ]:
        assert needle in prompt
