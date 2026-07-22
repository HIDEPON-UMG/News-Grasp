#!/usr/bin/env python3
"""Turn 4 4a/4b/4c category hero contract tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from tools import generate_pages
from tools.config import CATEGORIES

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "prompts" / "category-template.html"
CSS = ROOT / "docs" / "assets" / "site.css"
OG_DIR = ROOT / "docs" / "assets" / "og"


def _jpeg_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    cursor = 2
    sof_markers = {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
    while cursor < len(data):
        if data[cursor] != 0xFF:
            cursor += 1
            continue
        marker = data[cursor + 1]
        cursor += 2
        while marker == 0xFF:
            marker = data[cursor]
            cursor += 1
        if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            continue
        segment_length = int.from_bytes(data[cursor : cursor + 2], "big")
        if marker in sof_markers:
            height = int.from_bytes(data[cursor + 3 : cursor + 5], "big")
            width = int.from_bytes(data[cursor + 5 : cursor + 7], "big")
            return width, height
        cursor += segment_length
    raise AssertionError(f"JPEG size not found: {path}")


def _ctx(category_id: str, pause_notice: dict[str, object] | None = None) -> dict[str, object]:
    meta = CATEGORIES[category_id]
    featured = {
        "date": "2026-07-01",
        "summary_text": (
            "ドル円は高止まりした。米金利の見通しが焦点になった。"
            "日銀の利上げ観測と米利下げ時期が交差し、来週の指標で方向感が変わる。"
        ),
        "top_score": 93,
        "top_title": "テスト記事",
        "top_title_ja": "",
        "top_thumb": "",
        "top_source": "Test",
        "top_date": "2026-07-01 09:00",
        "top_bullets": [],
        "canonical": "https://example.com/article",
    }
    fit = generate_pages.fit_to_sentences(str(featured["summary_text"]), max_chars=31)
    hero = generate_pages.build_category_hero_context(
        category_id=category_id,
        featured=featured,
        entries=[featured] * 64,
        past_7=[featured] * 7,
        nav_categories=[
            {
                "id": cid,
                "name_en": m["label"],
                "name_jp": m["jp"],
                "glyph": m["glyph"],
                "accent": m["accent"],
                "is_active": cid == category_id,
            }
            for cid, m in CATEGORIES.items()
            if cid != "summary"
        ],
        sentence_fit=fit,
        fx_panel=generate_pages.default_fx_hero_panel(),
    )
    return {
        "site_title": "News Grasp",
        "base_url": "https://hidepon-umg.github.io/News-Grasp",
        "canonical": f"https://hidepon-umg.github.io/News-Grasp/{category_id}/",
        "category_id": category_id,
        "category_label": meta["label"],
        "category_jp": meta["jp"],
        "glyph": meta["glyph"],
        "accent": meta["accent"],
        "entries": [featured] * 64,
        "featured": featured,
        "editorial_heading": "",
        "editorial_essay": "",
        "grid_9": [],
        "past_7": [featured] * 7,
        "nav_categories": hero["nav_categories"],
        "pause_notice": pause_notice,
        "hero": hero,
    }


def _render(category_id: str, pause_notice: dict[str, object] | None = None) -> str:
    return generate_pages.render_template(TEMPLATE.read_text(encoding="utf-8"), _ctx(category_id, pause_notice))


def test_fx_hero_uses_turn4a_live_rates_and_sentence_bullets() -> None:
    html = _render("fx")

    assert 'data-hero-contract="turn4-category"' in html
    assert "cat-hero__bg" not in html
    assert "cat-hero__date" not in html
    assert ">LIVE RATES<" in html
    assert 'data-panel="rates"' in html
    assert "USD/JPY" in html
    assert "cat-hero__point-list" in html
    assert "今日の焦点" in html
    assert "cat-hero__focus-title" in html
    assert "テスト記事" in html
    assert "背景" in html
    assert '<strong class="emph-bold">ドル円</strong>は高止まりした。' in html
    assert '<strong class="emph-bold">米金利の見通し</strong>が焦点になった。' in html
    assert "次の視点" not in html
    hero_body = html.split('class="cat-hero__body"', 1)[1].split('class="cat-hero__stats"', 1)[0]
    assert '日銀の利上げ観測' not in hero_body
    assert "…" not in html.split('class="cat-hero__body"', 1)[1].split('class="cat-hero__stats"', 1)[0]
    assert "続きを読む →" in html
    assert ">USD / JPY<" in html
    assert ">TOTAL ENTRIES<" in html
    assert "--hero-bg-image: url('https://hidepon-umg.github.io/News-Grasp/assets/og/fx.jpg');" in html


def test_non_fx_hero_uses_turn4b_signals_and_score_panel() -> None:
    html = _render("ai")

    assert ">SIGNALS<" in html
    assert 'data-panel="lead-signal"' in html
    assert "REPRESENTATIVE SCORE" not in html
    assert "cat-hero__score-value" not in html
    assert "cat-hero__score-unit" not in html
    assert "最重要シグナル" in html
    assert "テスト記事" in html
    assert "Test · 2026-07-01 09:00" in html
    assert '<div class="cat-hero__watermark" aria-hidden="true">◆</div>' in html
    assert "--hero-bg-image: url('https://hidepon-umg.github.io/News-Grasp/assets/og/ai.jpg');" in html


def test_category_focus_title_prefers_summary_section_heading_over_count_sentence() -> None:
    featured = {
        "date": "2026-07-02",
        "summary_text": "IT-Consultingは5件。きょうは、導入前審査と運用再編が共通論点になった。",
        "top_score": 92,
        "top_title": "メタ、AIクラウド外販構想で上昇",
        "top_source": "株探",
        "top_date": "2026-07-01 22:47",
        "canonical": "https://example.com/article",
    }
    fit = generate_pages.fit_to_sentences(str(featured["summary_text"]), max_chars=80)

    hero = generate_pages.build_category_hero_context(
        category_id="it",
        featured=featured,
        entries=[featured],
        past_7=[featured],
        nav_categories=[],
        sentence_fit=fit,
        focus_heading="IT — 導入前審査が入口になる",
        fx_panel=generate_pages.default_fx_hero_panel(),
    )

    assert hero["focus"]["title"] == "導入前審査が入口になる"
    assert not hero["focus"]["title"].endswith("5件。")
    assert hero["visual"]["lead_title"] == "メタ、AIクラウド外販構想で上昇"


def test_category_lead_title_lines_keep_mobile_breaks_at_phrase_boundaries() -> None:
    title = "ソフトバンクG、OpenAIへ1兆6273億円を追加出資　第3弾は10月予定"

    assert generate_pages._category_lead_title_lines(title) == [
        "ソフトバンクG",
        "OpenAIへ1兆6273億円",
        "を追加出資",
        "第3弾は10月予定",
    ]


def test_category_lead_title_lines_keep_short_subject_with_topic_phrase() -> None:
    title = "メタ、AIクラウド外販構想で上昇"

    assert generate_pages._category_lead_title_lines(title) == [
        "メタ、AIクラウド",
        "外販構想で上昇",
    ]


def test_category_lead_title_lines_merge_short_subject_before_quoted_topic() -> None:
    title = "OpenAI、自動レッドチームAI「GPT-Red」発表　攻撃成功率は人間の6倍超"

    lines = generate_pages._category_lead_title_lines(title)

    assert lines == [
        "OpenAI、自動レッドチームAI",
        "「GPT-Red」発表",
        "攻撃成功率は人間の6倍超",
    ]
    assert generate_pages._category_lead_title_quality_errors(title, lines) == []


def test_category_lead_title_lines_merge_short_subject_with_long_budget_phrase() -> None:
    title = "OpenAI、2030年までの計算資源予算を122兆円に引き上げ"

    lines = generate_pages._category_lead_title_lines(title)

    assert lines == [
        "OpenAI、2030年までの",
        "計算資源予算を122兆円に引き上げ",
    ]
    assert generate_pages._category_lead_title_quality_errors(title, lines) == []


def test_category_lead_title_lines_merge_short_middle_fragment() -> None:
    title = "AI開発競争、主戦場は性能から価格へ－OpenAIやメタが新戦略"

    lines = generate_pages._category_lead_title_lines(title)

    assert lines == [
        "AI開発競争主戦場は性能から",
        "価格へ－OpenAIやメタが新戦略",
    ]
    assert generate_pages._category_lead_title_quality_errors(title, lines) == []


def test_category_lead_title_lines_rebalance_particle_before_short_tail() -> None:
    title = "トヨタ、米国製3列EV「ハイランダー」の生産延期"

    lines = generate_pages._category_lead_title_lines(title)

    assert lines == [
        "トヨタ米国製3列EV「ハイランダー」",
        "の生産延期",
    ]
    assert generate_pages._category_lead_title_quality_errors(title, lines) == []


def test_category_lead_title_lines_split_long_symposium_phrase() -> None:
    title = "IPA、仙台で東北サイバーセキュリティシンポジウムを11月開催"

    lines = generate_pages._category_lead_title_lines(title)

    assert lines == [
        "IPA、仙台で",
        "東北サイバーセキュリティ",
        "シンポジウムを11月開催",
    ]
    assert generate_pages._category_lead_title_quality_errors(title, lines) == []


def test_category_lead_title_lines_cover_current_category_hero_titles() -> None:
    cases = {
        "ai": (
            "ソフトバンクG、OpenAIへ1兆6273億円を追加出資　第3弾は10月予定",
            ["ソフトバンクG", "OpenAIへ1兆6273億円", "を追加出資", "第3弾は10月予定"],
        ),
        "economy": (
            "7月値上げ2566品目　円安162円台が家計圧力を増幅",
            ["7月値上げ2566品目", "円安162円台が家計圧力を増幅"],
        ),
        "game": (
            "SIE、2028年からPlayStation新作をダウンロード専売へ",
            ["SIE、2028年から", "PlayStation新作を", "ダウンロード専売へ"],
        ),
        "it": (
            "メタ、AIクラウド外販構想で上昇",
            ["メタ、AIクラウド", "外販構想で上昇"],
        ),
        "manufacturing": (
            "日印、製造投資2兆円 半導体材料工場やAI協業が120件",
            ["日印、製造投資2兆円", "半導体材料工場や", "AI協業が120件"],
        ),
        "mobility": (
            "テスラ韓国法人、補助金継続決定の翌日に主力EVを値上げ",
            ["テスラ韓国法人", "補助金継続決定の翌日に", "主力EVを値上げ"],
        ),
    }

    for category_id, (title, expected) in cases.items():
        lines = generate_pages._category_lead_title_lines(title)
        assert lines == expected, category_id
        assert generate_pages._category_lead_title_quality_errors(title, lines) == []


def test_category_hero_context_rejects_invalid_lead_title_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    featured = {
        "date": "2026-07-02",
        "summary_text": "主要論点を整理した。",
        "top_score": 92,
        "top_title": "長すぎる見出し",
    }

    monkeypatch.setattr(generate_pages, "_category_lead_title_lines", lambda _title: ["孤立", "長すぎる長すぎる長すぎる長すぎる長すぎる"])

    with pytest.raises(ValueError, match="category hero lead title line quality failed"):
        generate_pages.build_category_hero_context(
            category_id="it",
            featured=featured,
            entries=[featured],
            past_7=[featured],
            nav_categories=[],
            sentence_fit=generate_pages.fit_to_sentences(str(featured["summary_text"]), max_chars=80),
            fx_panel=generate_pages.default_fx_hero_panel(),
        )


def test_rest_day_hero_keeps_turn4_theme_and_structured_emphasis() -> None:
    html = _render("game", generate_pages._category_pause_notice("game", "2026-07-01"))

    assert 'data-hero-contract="turn4-category"' in html
    assert 'data-category="game"' in html
    assert "--hero-bg-image: url('https://hidepon-umg.github.io/News-Grasp/assets/og/game.jpg');" in html
    assert "cat-break-notice" not in html
    assert "cat-hero__body cat-hero__body--rest" in html
    assert "REST DAY · 2026-07-01" in html
    assert '<strong class="emph-bold">休載</strong>' in html
    assert "配信状態" in html and "表示内容" in html and "次回更新" in html
    assert '<strong class="emph-bold">ゲーム</strong>' in html
    assert "<strong>下の一覧</strong>" in html
    assert '<span class="emph-und">過去記事として読める順序</span>' in html


def test_turn4_theme_context_exists_for_all_seven_categories() -> None:
    for cid in [c for c in CATEGORIES if c != "summary"]:
        hero = generate_pages.build_category_hero_context(
            category_id=cid,
            featured={"date": "2026-07-01", "summary_text": "主要論点を整理した。"},
            entries=[],
            past_7=[],
            nav_categories=[],
            sentence_fit=generate_pages.fit_to_sentences("主要論点を整理した。"),
            fx_panel=generate_pages.default_fx_hero_panel(),
        )
        theme = hero["theme"]
        assert theme["base"]
        assert theme["dark"]
        assert theme["gradient_from"]
        assert theme["gradient_to"]
        assert theme["accent"]


def test_css_contains_turn4_mobile_stack_and_tab_fade() -> None:
    css = CSS.read_text(encoding="utf-8")

    assert 'cat-hero[data-hero-contract="turn4-category"] {\n  width: 100%;\n  max-width: var(--container-max);' in css
    assert 'cat-hero[data-hero-contract="turn4-category"] {\n  width: 100%;\n  max-width: none;' not in css
    assert 'cat-hero[data-hero-contract="turn4-category"] {\n  max-width: 1160px;' not in css
    assert "margin: 34px auto 0;" not in css
    assert "var(--hero-bg-image, none)" in css
    assert "grid-template-columns: 1.06fr .94fr" in css
    assert ".cat-hero__tab-fade" in css
    assert ".cat-hero__tab-arrow" in css
    tab_scroll_rule = css.split(".cat-hero__tab-scroll {", 1)[1].split("}", 1)[0]
    assert "overflow-x: auto;" in tab_scroll_rule
    assert "-webkit-overflow-scrolling: touch;" in tab_scroll_rule
    assert "scroll-snap-type: x proximity;" in tab_scroll_rule
    assert "scrollbar-width: none;" in tab_scroll_rule
    tab_fade_rule = css.split(".cat-hero__tab-fade {", 1)[1].split("}", 1)[0]
    assert "pointer-events: none;" in tab_fade_rule
    tab_arrow_rule = css.split(".cat-hero__tab-arrow {", 1)[1].split("}", 1)[0]
    assert "pointer-events: none;" in tab_arrow_rule
    assert ".cat-hero__point-list" in css
    assert ".cat-hero__point-key" in css
    focus_label_rule = css.split(".cat-hero__focus-label {", 1)[1].split("}", 1)[0]
    assert "font-size: 15px;" in focus_label_rule
    assert ".cat-hero__focus-title" in css
    focus_rule = css.split(".cat-hero__focus-title {", 1)[1].split("}", 1)[0]
    assert "font-size: 30px;" in focus_rule
    lead_rule = css.split(".cat-hero__lead-title {", 1)[1].split("}", 1)[0]
    assert "font-size: 44px;" in lead_rule
    assert "line-height: 1.16;" in lead_rule
    assert ".cat-hero__lead-title-line" in css
    assert 'content: " ";' in css
    mobile_rule = css.split("@media (max-width: 720px)", 1)[1]
    assert "text-align: left;" in mobile_rule
    assert "word-break: keep-all;" in mobile_rule
    assert "overflow-wrap: normal;" in mobile_rule
    assert "content: none;" in mobile_rule
    mobile_note_rule = mobile_rule.split(".cat-hero__lead .cat-hero__visual-note {", 1)[1].split("}", 1)[0]
    assert "text-align: left;" in mobile_note_rule
    lead_meta_rule = css.split(".cat-hero__lead-meta {", 1)[1].split("}", 1)[0]
    assert "font-size: 13px;" in lead_meta_rule
    lead_note_rule = css.split(".cat-hero__lead .cat-hero__visual-note {", 1)[1].split("}", 1)[0]
    assert "font-size: 15px;" in lead_note_rule
    assert ".cat-hero__point-text .emph-bold" in css
    assert ".cat-hero__body--rest" in css
    assert "box-shadow: inset 4px 0 0 var(--hero-accent);" in css
    assert ".cat-hero__body--rest .emph-bold" in css
    assert ".cat-hero__body--rest .emph-und" in css
    assert ".cat-hero__visual-label" in css
    assert "color: rgba(255, 255, 255, 0.96);" in css
    assert "border-radius: 14px;" not in css
    assert "border-radius: 12px;" not in css
    assert "border-radius: 7px;" not in css
    assert "color: #1a1206;" not in css.split(".cat-hero__visual {", 1)[1].split(".cat-hero__watermark", 1)[0]
    assert "@media (max-width: 720px)" in css
    assert "grid-template-columns: 1fr" in css


def test_mobility_and_manufacturing_hero_background_assets_are_panel_sized() -> None:
    assert _jpeg_size(OG_DIR / "mobility.jpg") == (1120, 587)
    assert _jpeg_size(OG_DIR / "manufacturing.jpg") == (1120, 587)
