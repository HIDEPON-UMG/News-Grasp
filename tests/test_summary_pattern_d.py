#!/usr/bin/env python3
"""Phase 5: Editorial Summary (Pattern D) docs/{date}/summary/index.html 契約テスト。

build_summary() が生成する HTML に D パターン必須セクションが含まれているかを
verify する。γ schema が無い digest でも fallback で 7 sections + 3 takeaways が
必ず描画されることを pin する。

Variant D の認識: site/desktop-extra.jsx の DesktopSummaryOnly を権威ソースとする。

実行:
    pytest tests/test_summary_pattern_d.py -v
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CSS_PATH = ROOT / "docs" / "assets" / "site.css"
SUMMARY_TEMPLATE = ROOT / "prompts" / "summary-template.html"

from tools.generate_pages import (  # noqa: E402
    _section_label_to_cid,
    build_all,
    build_all_summaries,
    build_summary,
    parse_essay_sections,
    _collect_entries,
    _SUMMARY_SECTION_COLORS,
)
from tools.config import CATEGORIES  # noqa: E402


def _last_css_block(css: str, selector: str) -> str:
    start = css.rindex(selector)
    return css[start:css.index("}", start)]


DIGEST_TEMPLATE = """---
title: "News Grasp #20260520 — {label}"
date: 2026-05-20
issue: 20260520
weekday: 水
category: {label}
categoryId: {cat_id}
---

# {LABEL}

> [!summary]
> 統合テスト用サマリ。{cat_id} カテゴリの本文サマリを 1 行で記述。

---

### [88] テスト記事 1

📅 2026-05-20 不明 · 📰 Test Source · 🔗 [元記事](https://example.com/1)

#cat/{cat_id} #topic/test #score/高

- 【事実・概要】：FACT_SENTINEL は配信網と認証が先に立つ。
- 【背景・要点】：CONTEXT_SENTINEL は制度参加と運用設計が焦点になる。
- 【影響・展望】：OUTLOOK_SENTINEL は安全性と運用体制が案件獲得を左右する。

---

### [76] テスト記事 2

📅 2026-05-20 不明 · 📰 Test Source · 🔗 [元記事](https://example.com/2)

#cat/{cat_id} #topic/test #score/中

- 【事実・概要】：SECOND_FACT_SENTINEL

---

### [62] テスト記事 3

📅 2026-05-20 不明 · 📰 Test Source · 🔗 [元記事](https://example.com/3)

#cat/{cat_id} #topic/test #score/中

- 【事実・概要】：THIRD_FACT_SENTINEL
"""


@pytest.fixture(scope="module")
def built_summary(tmp_path_factory) -> str:
    """5 カテゴリ digest を tmp に置き build_summary で 2026-05-20 の summary を生成。"""
    root = tmp_path_factory.mktemp("sum_d")
    docs = root / "docs"
    sources: list[Path] = []
    for cat_id, label in [("fx", "FX"), ("ai", "AI"), ("it", "IT-Consulting"),
                          ("economy", "Economy"), ("game", "Game")]:
        digest_dir = root / "digest" / label.upper()
        digest_dir.mkdir(parents=True, exist_ok=True)
        path = digest_dir / f"2026-05-20-{label}.md"
        path.write_text(
            DIGEST_TEMPLATE.format(label=label, LABEL=label.upper(), cat_id=cat_id),
            encoding="utf-8",
        )
        sources.append(path)

    build_all(full=True, docs_root=docs, digests=sources)
    entries = _collect_entries(sources)

    out = build_summary("2026-05-20", entries, docs)
    assert out.exists()
    # docs/2026-05-20/summary/index.html
    parts = out.parts[-3:]
    assert parts == ("2026-05-20", "summary", "index.html"), \
        f"Summary should land at docs/2026-05-20/summary/index.html, got {out}"
    return out.read_text(encoding="utf-8")


# ============================================================
# 構造 pin
# ============================================================

def test_dc_masthead_replaces_legacy_brand_zone(built_summary: str):
    """カテゴリー別と同じ brand-zone + lens-nav を使い、旧 summary 専用スイッチは残さない。"""
    assert 'class="brand-zone brand-zone--summary"' in built_summary
    masthead = built_summary.split('<nav class="lens-nav lens-nav--summary">', 1)[0]
    assert 'class="lens-nav lens-nav--summary"' in built_summary
    assert 'class="lens-pill lens-pill--essay is-active"' in built_summary
    assert 'class="brand-search brand-search--summary"' in masthead
    assert 'id="summary-search"' in masthead
    assert "brand-zone__summary-mark" not in masthead
    assert "SECTION" not in masthead
    assert "本日のテーマ考察" not in masthead
    assert "News Grasp" in built_summary
    assert "LENSES" in built_summary
    assert "ESSAY" in built_summary
    assert "summary-masthead" not in built_summary
    assert "summary-mode-pill" not in built_summary


def test_hero_dark_navy(built_summary: str):
    """DC正本の hero に theme / at-a-glance / flow がある。"""
    assert 'class="summary-hero"' in built_summary
    assert "EDITORIAL SUMMARY" in built_summary
    assert "本日のテーマ考察 / EDITORIAL SUMMARY" not in built_summary
    assert "本日のテーマ考察" in built_summary
    assert "AT A GLANCE" in built_summary
    assert "制度・標準" in built_summary
    assert "供給・販路" in built_summary
    assert "実装・拡張" in built_summary


def test_dc_category_sections_render_categories_without_legacy_outline(built_summary: str):
    """DC正本どおり、総論/明日へを独立させ、カテゴリセクションを全件出す。"""
    assert "§ 00" in built_summary
    assert "§ 08" in built_summary
    for i, cid in enumerate([c for c in CATEGORIES if c != "summary"], start=1):
        assert f"§{i:02d}" in built_summary, f"§{i:02d} missing"
        assert f'data-category-id="{cid}"' in built_summary
    assert "summary-sections__grid" not in built_summary
    assert "FULL ESSAY OUTLINE" not in built_summary


def test_summary_essay_sections_use_dc_lane_cards(built_summary: str):
    """ESSAYカテゴリは DC正本の FACT/CONTEXT/OUTLOOK 3レーンを使う。"""
    assert "summary-lanes--essay" not in built_summary
    assert "summary-sec__bullets" not in built_summary
    assert "summary-lane-card" in built_summary
    assert "summary-lane-card__badge" in built_summary
    assert "summary-lane-card__short" in built_summary
    assert "summary-lane-card__marker" in built_summary
    for role in ("fact", "context", "outlook"):
        assert f'data-role="{role}"' in built_summary
    for short in ("FACT", "CONTEXT", "OUTLOOK"):
        assert short in built_summary
    for marker in ("事実・概要", "背景・要点", "影響・展望"):
        assert marker in built_summary
    for stale_marker in ("【事実】", "【背景】", "【展望】", "【事実・概要】", "【背景・要点】", "【影響・展望】"):
        assert stale_marker not in built_summary
    for persona in ("記者", "解説者", "予測者"):
        assert persona not in built_summary
    assert "summary-lane__label" not in built_summary


def test_essay_redesign_renders_analysis_board_shell(built_summary: str):
    """ESSAY redesign は Hero 以降を分析ボードとして構造化する。"""
    for required in (
        'class="summary-glance"',
        'class="summary-conclusions"',
        'class="summary-synthesis"',
        'class="summary-tomorrow"',
        'class="summary-category-sections"',
        "WATCH",
        "SIGNAL",
        "IMPLICATION",
    ):
        assert required in built_summary


def test_essay_redesign_uses_canonical_category_glyphs(built_summary: str):
    """カテゴリの見様見真似アイコンを使わず CATEGORIES の正規 glyph を出す。"""
    expected = {
        "fx": "¥",
        "ai": "◆",
        "it": "⌗",
        "mobility": "◎",
        "manufacturing": "⬢",
        "economy": "■",
        "game": "▶",
    }
    for cid, glyph in expected.items():
        assert f'data-category-id="{cid}"' in built_summary
        assert f'data-category-glyph="{glyph}"' in built_summary


def test_essay_redesign_maps_english_section_labels_to_canonical_categories():
    """英字の ESSAY 見出しも正規カテゴリ ID に解決する。"""
    expected = {
        "FX — 161円台後半": "fx",
        "Mobility — ルール先行": "mobility",
        "Manufacturing — 資本投下": "manufacturing",
        "Economy — 成長率": "economy",
        "Game — IP支援": "game",
    }
    for heading, cid in expected.items():
        assert _section_label_to_cid(heading) == cid


def test_essay_redesign_keeps_canonical_lane_icons_and_readable_labels(built_summary: str):
    """FACT/CONTEXT/OUTLOOK は正規 SVG と読めるラベルサイズ契約を持つ。"""
    for icon in (
        '<circle cx="10.5" cy="10.5" r="6.5"',
        '<line x1="15.5" y1="15.5" x2="21" y2="21"',
        '<rect x="6" y="5" width="5" height="14"',
        '<rect x="13" y="5" width="5" height="14"',
        '<polyline points="4 16 9 11 13 14 20 7"',
        '<polyline points="15 7 20 7 20 12"',
    ):
        assert icon in built_summary
    css = CSS_PATH.read_text(encoding="utf-8")
    assert ".summary-lane-card__short" in css
    assert "font-size: 8px" not in css[css.index(".summary-lane-card__short"):
                                      css.index("}", css.index(".summary-lane-card__short"))]
    short_block = _last_css_block(css, ".summary-lane-card__short")
    assert re.search(r"font-size:\s*(?:15|16)px", short_block), short_block


def test_essay_redesign_uses_category_marker_chips_and_larger_watch_labels(built_summary: str):
    """マーカーは濃色ベタにせず、カテゴリ色の淡色背景+カテゴリ文字にする。"""
    css = CSS_PATH.read_text(encoding="utf-8")
    assert 'class="summary-conclusion__chip"' in built_summary
    assert re.search(r'<article class="summary-conclusion"[^>]+data-category-id="(?:fx|ai|it|mobility|manufacturing|economy|game)"', built_summary)
    assert any(glyph in built_summary for glyph in ("¥", "◆", "⌗", "◎", "⬢", "■", "▶"))
    conclusion_chip_start = css.rindex(".summary-conclusion__chip {\n")
    conclusion_chip_block = css[conclusion_chip_start:css.index("}", conclusion_chip_start)]
    assert "background: color-mix(in srgb, var(--c) 16%, var(--color-surface))" in conclusion_chip_block
    assert "color: color-mix(in srgb, var(--c) 86%, var(--color-navy))" in conclusion_chip_block
    lane_head_start = css.rindex(".summary-lane-card__head {\n  display")
    lane_head_block = css[lane_head_start:css.index("}", lane_head_start)]
    assert "border-left: 4px solid var(--c)" in lane_head_block
    assert "background: color-mix(in srgb, var(--c) 8%, var(--color-surface))" in lane_head_block
    marker_start = css.index(".summary-lane-card__marker {\n  display")
    marker_block = css[marker_start:css.index("}", marker_start)]
    assert "background: color-mix(in srgb, var(--c) 14%, var(--color-surface))" in marker_block
    assert "color: color-mix(in srgb, var(--c) 84%, var(--color-navy))" in marker_block
    assert "color: #fff" not in marker_block
    assert "background: var(--c)" not in marker_block
    assert '.summary-lane-card[data-role="context"] .summary-lane-card__badge' in css
    assert '.summary-lane-card[data-role="outlook"] .summary-lane-card__badge' in css
    emph_block = _last_css_block(css, ".summary-lane-card p .emph-bold")
    assert "background: color-mix(in srgb, var(--c) 18%, transparent)" in emph_block
    assert "color: color-mix(in srgb, var(--c) 82%, var(--color-navy))" in emph_block
    assert 'class="summary-tomorrow__cat"' in built_summary
    assert re.search(r'<span class="summary-tomorrow__cat">\s*<span class="summary-tomorrow__glyph"[^>]*>.*?</span>\s*<span>[A-Z &]+ / ', built_summary, re.S)
    tomorrow_cat_start = css.rindex(".summary-tomorrow__cat {\n")
    tomorrow_cat_block = css[tomorrow_cat_start:css.index("}", tomorrow_cat_start)]
    assert "background: var(--c)" in tomorrow_cat_block
    assert "padding: 5px 12px 5px 10px" in tomorrow_cat_block
    watch_block = _last_css_block(css, ".summary-tomorrow__cells div > span")
    assert re.search(r"font-size:\s*(?:14|15|16)px", watch_block), watch_block
    desktop_css = css[:css.index("@media (max-width: 520px)")]
    watch_body_block = _last_css_block(desktop_css, ".summary-tomorrow__cells p")
    assert re.search(r"font-size:\s*20px", watch_body_block), watch_body_block
    watch_texts = re.findall(r"<div><span>WATCH 見る</span><p>(.*?)</p></div>", built_summary, re.S)
    assert watch_texts
    assert not any(
        re.match(r"\s*(?:FX|AI|IT|Mobility|Manufacturing|Economy|Game)\s*[—–\-ー―:：/]+", text)
        for text in watch_texts
    ), watch_texts
    assert not re.search(r"\.summary-tomorrow__cells\s+span\s*\{", css)
    inline_span_block = _last_css_block(css, ".summary-tomorrow__cells p span")
    assert "display: inline" in inline_span_block
    assert "font-size: inherit" in inline_span_block
    assert "color: inherit" in inline_span_block
    mobile_css = css[css.index("@media (max-width: 520px)"):]
    assert ".summary-tomorrow__cells p" in mobile_css
    assert re.search(r"\.summary-tomorrow__cells p\s*\{[^}]*font-size:\s*16\.5px", mobile_css, re.S), mobile_css


def test_summary_header_search_matches_category_page_position_contract(built_summary: str):
    """ESSAY の検索窓はカテゴリーページ同様、logo の外側で header 中央列に置く。"""
    template = SUMMARY_TEMPLATE.read_text(encoding="utf-8")
    label_pos = template.index('<label class="brand-search brand-search--summary">')
    nav_pos = template.index('<nav class="brand-zone__nav">')
    logo_close = template.index("</div>\n    <label", template.index('<div class="brand-zone__logo">'))
    assert logo_close < label_pos < nav_pos
    assert re.search(
        r'<div class="brand-zone__logo">.*?</div>\s*<label class="brand-search brand-search--summary">',
        built_summary,
        re.S,
    )
    css = CSS_PATH.read_text(encoding="utf-8")
    desktop_css = css[:css.index("@media (max-width: 520px)")]
    summary_search_start = desktop_css.rindex(".brand-zone--summary .brand-search {\n")
    summary_search_block = desktop_css[summary_search_start:desktop_css.index("}", summary_search_start)]
    assert "flex: 1 1 auto" in summary_search_block
    assert "max-width: 440px" in summary_search_block


def test_essay_redesign_keeps_lane_texts_bound_to_their_roles(built_summary: str):
    """記事 bullet の role prefix を本文に残さず、各 lane へ正しい順で流し込む。"""
    fact_match = re.search(
        r'<section class="summary-lane-card" data-role="fact".*?</section>',
        built_summary,
        re.S,
    )
    context_match = re.search(
        r'<section class="summary-lane-card" data-role="context".*?</section>',
        built_summary,
        re.S,
    )
    outlook_match = re.search(
        r'<section class="summary-lane-card" data-role="outlook".*?</section>',
        built_summary,
        re.S,
    )
    assert fact_match and context_match and outlook_match
    fact_html = fact_match.group(0)
    context_html = context_match.group(0)
    outlook_html = outlook_match.group(0)
    assert "FACT_SENTINEL" in fact_html
    assert "CONTEXT_SENTINEL" in context_html
    assert "OUTLOOK_SENTINEL" in outlook_html
    assert "FACT_SENTINEL" not in context_html
    assert "CONTEXT_SENTINEL" not in outlook_html
    assert "OUTLOOK_SENTINEL" not in fact_html
    for role_prefix in ("【事実・概要】", "【背景・要点】", "【影響・展望】"):
        assert role_prefix not in fact_html
        assert role_prefix not in context_html
        assert role_prefix not in outlook_html


def test_essay_redesign_expands_hero_text_and_aligns_glance_top(built_summary: str):
    """本日のテーマ考察本文を右へ伸ばし、AT A GLANCE の上端を本文ブロックと揃える。"""
    css = CSS_PATH.read_text(encoding="utf-8")
    inner_start = css.index(".summary-hero__inner {\n  display: grid;")
    inner_block = css[inner_start:css.index("}", inner_start)]
    assert "grid-template-columns" in inner_block
    assert "minmax(0, 1fr)" in inner_block
    assert "minmax(320px, 380px)" in inner_block
    lead_start = css.index(".summary-hero__lead {\n  max-width: 68ch;")
    lead_block = css[lead_start:css.index("}", lead_start)]
    assert "max-width: 68ch" in lead_block
    glance_start = css.index(".summary-glance {\n  grid-column: 2;")
    glance_block = css[glance_start:css.index("}", glance_start)]
    assert "margin-top: 56px" in glance_block
    kicker_block = _last_css_block(css, ".summary-glance__kicker")
    assert re.search(r"font-size:\s*(?:14|15|16)px", kicker_block), kicker_block


def test_section_accent_colors_used(built_summary: str):
    """各§の accent color (固定 7 色) が inline style に出ている。"""
    for color in _SUMMARY_SECTION_COLORS:
        assert color in built_summary, f"section color {color} missing"


def test_3_takeaways_always_rendered(built_summary: str):
    """TODAY'S 3 CONCLUSIONS に必ず 3 件出る。"""
    assert "TODAY'S 3 CONCLUSIONS" in built_summary
    assert "今日の 3 つの結論" not in built_summary
    assert "summary-takeaways" not in built_summary
    assert "summary-conclusion__n" in built_summary
    # 番号 01 / 02 / 03 が出る
    assert ">01<" in built_summary
    assert ">02<" in built_summary
    assert ">03<" in built_summary


def test_cta_band(built_summary: str):
    """CTA band: 全 N 本のニュースを読む + 過去の考察を見る。"""
    assert "summary-cta" in built_summary
    assert "本のニュースを読む" in built_summary
    assert "過去の考察を見る" in built_summary


def test_summary_digest_not_counted_as_news_source(tmp_path: Path):
    """Summary digest 内の [NN] 見出しは、日別ニュース総数に混ぜない。"""
    docs = tmp_path / "docs"
    sources: list[Path] = []
    for cat_id, label in [("fx", "FX"), ("ai", "AI")]:
        digest_dir = tmp_path / "digest" / label
        digest_dir.mkdir(parents=True, exist_ok=True)
        path = digest_dir / f"2026-05-20-{label}.md"
        path.write_text(
            DIGEST_TEMPLATE.format(label=label, LABEL=label.upper(), cat_id=cat_id),
            encoding="utf-8",
        )
        sources.append(path)

    summary_dir = tmp_path / "digest" / "Summary"
    summary_dir.mkdir(parents=True, exist_ok=True)
    summary = summary_dir / "2026-05-20.md"
    summary.write_text(
        _SUMMARY_DIGEST_WITH_ESSAY
        + "\n\n**本日の主要記事**（2 件）\n"
        + "- [88] [再掲されてはいけない summary 内リンク](../AI/2026-05-20-AI.md#x)\n"
        + "- [76] [これも記事総数に含めない](../FX/2026-05-20-FX.md#y)\n",
        encoding="utf-8",
    )
    sources.append(summary)

    entries = _collect_entries(sources)
    summary_entry = next(e for e in entries if e["category_id"] == "summary")
    assert summary_entry["articles_count"] == 0

    out = build_summary("2026-05-20", entries, docs)
    html = out.read_text(encoding="utf-8")
    assert "全6本のニュースを読む" in html
    assert "全8本のニュースを読む" not in html


def test_canonical_under_date_summary(built_summary: str):
    """canonical = {BASE_URL}/2026-05-20/summary/"""
    from tools.config import BASE_URL
    assert f'<link rel="canonical" href="{BASE_URL}/2026-05-20/summary/">' in built_summary


def test_og_meta_present(built_summary: str):
    """og:type=article + 標準 og 群 + twitter card。"""
    assert 'property="og:type" content="article"' in built_summary
    for prop in ("og:title", "og:url", "og:image"):
        assert f'property="{prop}"' in built_summary
    assert 'name="twitter:card"' in built_summary


def test_favicon_links_present(built_summary: str):
    """News Grasp の N→ ロゴが favicon として登録されている。"""
    assert 'rel="icon"' in built_summary
    assert '/assets/favicon-256.png' in built_summary
    assert 'rel="apple-touch-icon"' in built_summary


def test_pull_quote_hidden_in_fallback(built_summary: str):
    """DC正本では pull quote セクション自体を使わない。"""
    assert 'class="summary-pull"' not in built_summary


def test_summary_public_html_contains_no_raw_emphasis_markers(tmp_path):
    """summary page では [[ ]] / ** ** / __ __ を public HTML に残さない。"""
    root = tmp_path
    docs = root / "docs"
    sources: list[Path] = []
    for cat_id, label in [("fx", "FX"), ("ai", "AI"), ("it", "IT-Consulting")]:
        digest_dir = root / "digest" / label.upper()
        digest_dir.mkdir(parents=True, exist_ok=True)
        p = digest_dir / f"2026-05-20-{label}.md"
        p.write_text(
            DIGEST_TEMPLATE.format(label=label, LABEL=label.upper(), cat_id=cat_id),
            encoding="utf-8",
        )
        sources.append(p)

    sum_dir = root / "digest" / "Summary"
    sum_dir.mkdir(parents=True, exist_ok=True)
    sum_path = sum_dir / "2026-05-20.md"
    sum_path.write_text(_SUMMARY_DIGEST_WITH_ESSAY, encoding="utf-8")
    sources.append(sum_path)

    build_all(full=True, docs_root=docs, digests=sources)
    entries = _collect_entries(sources)
    out = build_summary("2026-05-20", entries, docs, digest_sources=sources)
    html_text = out.read_text(encoding="utf-8")

    assert "[[" not in html_text
    assert "]]" not in html_text
    assert "**" not in html_text
    assert not re.search(r"(?<![A-Za-z0-9])__(?![A-Za-z0-9])", html_text)
    assert '&lt;strong class="emph-bold"&gt;' not in html_text
    assert "&lt;strong&gt;" not in html_text
    assert '&lt;span class="emph-und"&gt;' not in html_text


# ============================================================
# §01 全文表示 (2026-05-26 ユーザー要望)
# ============================================================

_SUMMARY_DIGEST_WITH_ESSAY = """---
title: "News Grasp #20260520 — 時勢を掴み、日々に新たに。"
date: 2026-05-20
issue: 20260520
weekday: 水
categoryId: summary
---

# News Grasp #20260520

> [!summary]
> ヒーロー導入のサマリ文。一行で本日のテーマを語る。

## § 本日のテーマ考察

### §01 総論 — 金利の壁と AI の自律が交差した一日

本日の 5 分野を貫く構造は「**金利上昇圧力と AI 投資の続行**」という矛盾した二軸の共存だ。__バブルだから崩れる__ と「稼げるから続く」の綱引きは今週の FOMC 議事録・PCE で一度答えが出る。

### §02 為替 — タカ派議事録がドル高の「第二波」を呼ぶ

[[FOMC]] の 4 票反対と「緩和バイアス削除」が、ドル円の 7 連騰を演出した。

### §07 明日への示唆 — 今週は「数字が相場を決める週」

5/28 ・ 5/30 ・ 5/29 の指標が並ぶ。__観察の週__ であり、行動を決める情報収集の週だ。

### KEY TAKEAWAYS

- 為替: PCE が 3.5% 超で 160 円突破
"""


def test_parse_essay_sections_extracts_section_bodies():
    """parse_essay_sections() が `### §NN ...` を {heading, body} 辞書で返す。"""
    sections = parse_essay_sections(_SUMMARY_DIGEST_WITH_ESSAY)
    assert 1 in sections
    assert "総論" in sections[1]["heading"]
    assert "金利上昇圧力" in sections[1]["body"]
    # body は次の `### §` 前で切れる (§02 の文言を含まない)
    assert "ドル円の 7 連騰" not in sections[1]["body"]
    # § 07 まで届く
    assert 7 in sections
    assert "観察の週" in sections[7]["body"]
    # KEY TAKEAWAYS ブロックは含めない
    assert "KEY TAKEAWAYS" not in sections[7]["body"]
    assert "160 円突破" not in sections[7]["body"]


def test_parse_essay_sections_stops_before_h2_key_takeaways():
    """`## KEY TAKEAWAYS` でも最後の ESSAY 本文へ構造化ブロックを混ぜない。"""
    digest = _SUMMARY_DIGEST_WITH_ESSAY.replace("### KEY TAKEAWAYS", "## KEY TAKEAWAYS")
    digest = digest.replace(
        "- 為替: PCE が 3.5% 超で 160 円突破",
        "- n: 1 / color: #B8860B / **[FX・Economy]** PCE が 3.5% 超で 160 円突破\n"
        "\n"
        "> [!link] Related Issues\n"
        "> - 2026-06-21 — 前回号",
    )

    sections = parse_essay_sections(digest)

    assert "観察の週" in sections[7]["body"]
    assert "KEY TAKEAWAYS" not in sections[7]["body"]
    assert "color:" not in sections[7]["body"]
    assert "Related Issues" not in sections[7]["body"]
    assert "前回号" not in sections[7]["body"]


def test_build_summary_renders_section1_full_body_when_essay_present(tmp_path):
    """digest_sources に summary digest を渡すと §01 本文が HTML に出る (全文表示)。"""
    root = tmp_path
    docs = root / "docs"
    sources: list[Path] = []
    # 5 カテゴリ digest (entries 生成用)
    for cat_id, label in [("fx", "FX"), ("ai", "AI"), ("it", "IT-Consulting"),
                          ("economy", "Economy"), ("game", "Game")]:
        digest_dir = root / "digest" / label.upper()
        digest_dir.mkdir(parents=True, exist_ok=True)
        p = digest_dir / f"2026-05-20-{label}.md"
        p.write_text(
            DIGEST_TEMPLATE.format(label=label, LABEL=label.upper(), cat_id=cat_id),
            encoding="utf-8",
        )
        sources.append(p)
    # summary digest (§01-§07 構造を持つ)
    sum_dir = root / "digest" / "Summary"
    sum_dir.mkdir(parents=True, exist_ok=True)
    sum_path = sum_dir / "2026-05-20.md"
    sum_path.write_text(_SUMMARY_DIGEST_WITH_ESSAY, encoding="utf-8")
    sources.append(sum_path)

    build_all(full=True, docs_root=docs, digests=sources)
    entries = _collect_entries(sources)

    out = build_summary("2026-05-20", entries, docs, digest_sources=sources)
    html_text = out.read_text(encoding="utf-8")

    # §01 本文 (digest md 由来) が HTML に含まれる
    assert "金利上昇圧力" in html_text, "§01 本文 (essay sections) が HTML に注入されていない"
    # 強調 emph の bold タグも render_emph で出る
    assert "金利上昇圧力と AI 投資の続行" in html_text
    # §01 の「詳細を読む」リンク (自己参照) は出ない
    assert "→ §01 総論 詳細を読む" not in html_text
    # §07 の「詳細を読む」も自己参照なので出ない
    assert "→ §07 明日へ 詳細を読む" not in html_text


def test_build_all_summaries_unique_dates():
    """build_all_summaries は unique date の数だけ summary ページを作る。"""
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        docs = root / "docs"
        sources: list[Path] = []
        for date_str in ("2026-05-19", "2026-05-20"):
            for cat_id, label in [("fx", "FX")]:
                digest_dir = root / "digest" / label.upper()
                digest_dir.mkdir(parents=True, exist_ok=True)
                p = digest_dir / f"{date_str}-{label}.md"
                content = DIGEST_TEMPLATE.format(label=label, LABEL=label.upper(), cat_id=cat_id)
                content = content.replace("2026-05-20", date_str).replace(
                    "20260520", date_str.replace("-", "")
                )
                p.write_text(content, encoding="utf-8")
                sources.append(p)
        build_all(full=True, docs_root=docs, digests=sources)
        entries = _collect_entries(sources)
        summaries = build_all_summaries(entries, docs)
        assert len(summaries) == 2
        assert (docs / "2026-05-19" / "summary" / "index.html").exists()
        assert (docs / "2026-05-20" / "summary" / "index.html").exists()
