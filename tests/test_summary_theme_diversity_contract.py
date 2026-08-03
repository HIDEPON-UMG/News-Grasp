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
    assert "{hero_left} と {hero_right}" not in prompt


def test_summary_prompts_require_one_concrete_news_headline_not_abstract_pair() -> None:
    for prompt_path in (EDITOR_PROMPT, ROUTINE_PROMPT):
        prompt = _read(prompt_path)
        for needle in [
            "単一の主役ニュース",
            "主体・出来事・動作",
            "複数の独立ニュース",
            "hero_headline",
        ]:
            assert needle in prompt


def test_home_templates_do_not_force_contrast_joiner_between_hero_fragments() -> None:
    index = _read(ROOT / "prompts" / "index-template.html")
    overview = _read(ROOT / "prompts" / "overview-template.html")

    assert "</span> と <br/>" not in index
    assert "</span>と<span" not in overview
    assert "{{ hero_phrase_left }}と{{ hero_phrase_right }}" not in index


def test_summary_renderer_does_not_force_contrast_joiner_between_hero_fragments() -> None:
    renderer = _read(ROOT / "tools" / "generate_pages.py")

    assert 'f"{left}と{right}"' not in renderer
    for date in ("2026-08-02", "2026-08-03"):
        rendered = _read(ROOT / "docs" / date / "summary" / "index.html")
        assert "、と" not in rendered


def test_august_2_and_3_summary_titles_are_concrete_contiguous_news_headlines() -> None:
    august_2 = _read(SUMMARY_DIR / "2026-08-02.md")
    title_2 = _frontmatter_value(august_2, "title").split("—", 1)[-1].strip(" '\"")
    assert title_2 == (
        _frontmatter_value(august_2, "hero_left").strip(" '\"")
        + _frontmatter_value(august_2, "hero_right").strip(" '\"")
    )

    august_3 = _read(SUMMARY_DIR / "2026-08-03.md")
    title_3 = _frontmatter_value(august_3, "title").split("—", 1)[-1].strip(" '\"")
    headline_3 = _frontmatter_value(august_3, "hero_headline").strip(" '\"")
    assert title_3 == headline_3 == "日米が円買い協調介入、ドル円は一時155円台前半へ"
