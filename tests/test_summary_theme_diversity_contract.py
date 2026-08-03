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
            "一つのニュース見出し",
            "主体・出来事・動作",
            "抽象語二句の対比",
            "連続する前半・後半",
            "hero_left + hero_right",
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
    for date in ("2026-08-02", "2026-08-03"):
        summary = _read(SUMMARY_DIR / f"{date}.md")
        title = _frontmatter_value(summary, "title").split("—", 1)[-1].strip(" '\"")
        left = _frontmatter_value(summary, "hero_left").strip(" '\"")
        right = _frontmatter_value(summary, "hero_right").strip(" '\"")

        assert title == left + right
        assert any(anchor in title for anchor in ("AI", "円", "介入", "企業", "クラウド", "EV"))
        assert any(action in title for action in ("拡大", "値下げ", "迫る", "開始", "決定", "導入"))
