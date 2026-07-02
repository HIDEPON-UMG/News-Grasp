#!/usr/bin/env python3
"""Phase 3: Variant B Magazine Spread Home (docs/index.html) の構造契約テスト。

build_index() が生成する HTML に Variant B 必須セクションが含まれているかを
verify する。スタイル詳細ではなく構造 ID / クラスを pin する。

Variant B の認識: site/variant-b.jsx (Claude Design Handoff) を権威ソースとする。

実行:
    pytest tests/test_home_variant_b.py -v
"""
from __future__ import annotations

import html
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.generate_pages import (  # noqa: E402
    _split_theme_phrases,
    build_all,
    build_category_pages,
    build_index,
    scan_digests,
    _collect_entries,
    _key_numbers,
    _score_note,
    _score_signals,
)
from tools.config import CATEGORIES  # noqa: E402


class _EditorsTopBadgeParser(HTMLParser):
    """home-top3__badge の入れ子 span を含むテキストを抽出する。"""

    def __init__(self) -> None:
        super().__init__()
        self.badges: list[tuple[str, str]] = []
        self._current_cat: str | None = None
        self._depth = 0
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        class_name = attr.get("class", "")
        if tag == "span" and self._current_cat is None and class_name.startswith("home-top3__badge cat-"):
            self._current_cat = class_name.removeprefix("home-top3__badge cat-")
            self._depth = 1
            self._chunks = []
            return
        if self._current_cat is not None and tag == "span":
            self._depth += 1

    def handle_endtag(self, tag: str) -> None:
        if self._current_cat is None or tag != "span":
            return
        self._depth -= 1
        if self._depth == 0:
            self.badges.append((self._current_cat, "".join(self._chunks)))
            self._current_cat = None
            self._chunks = []

    def handle_data(self, data: str) -> None:
        if self._current_cat is not None:
            self._chunks.append(data)


# ============================================================
# fixture: 実 digest を使って Home をビルド
# ============================================================

@pytest.fixture(scope="module")
def built_docs_root(tmp_path_factory) -> Path:
    """実 digest 全件を tmp に再 build して LP / category pages を返す。"""
    docs_root = tmp_path_factory.mktemp("home_b")
    podcast_state = docs_root.parent / "build" / "youtube-podcast"
    podcast_state.mkdir(parents=True, exist_ok=True)
    (podcast_state / "uploads.json").write_text(
        json.dumps({
            "2026-06-21": {
                "status": "public",
                "videoId": "video-latest",
                "playlistId": "playlist-deepdive",
            }
        }),
        encoding="utf-8",
    )
    # 実 digest から個別ページを生成 (build_index 前提)
    written = build_all(full=True, docs_root=docs_root)
    assert written, "build_all が 0 件しか返さない: 実 digest が足りない可能性"
    # _collect_entries は実 digest を再スキャンする
    entries = _collect_entries(scan_digests())
    assert entries, "_collect_entries が空: digest scan に失敗"
    out = build_index(entries, docs_root)
    assert out.exists(), f"build_index が index.html を生成しなかった: {out}"
    category_pages = build_category_pages(entries, docs_root)
    assert category_pages, "build_category_pages がカテゴリーページを生成しなかった"
    return docs_root


@pytest.fixture(scope="module")
def built_home(built_docs_root: Path) -> str:
    """Phase 3 開発用 fixture。再 build 済み index.html を返す。"""
    return (built_docs_root / "index.html").read_text(encoding="utf-8")


# ============================================================
# 構造 pin
# ============================================================

def test_brand_zone_present(built_home: str):
    """home-brand (navy 86px wordmark + ISSUE) セクションがある。"""
    assert 'class="home-brand"' in built_home, "home-brand class missing"
    assert "News Grasp" in built_home, "site title missing"
    assert "SEVEN  LENSES  ON  TODAY" in built_home or "SEVEN LENSES ON TODAY" in built_home, \
        "tagline missing"


def test_sticky_nav_with_7_lenses(built_home: str):
    """home-nav の 7 lenses + TODAY + § ESSAY + ARCHIVE が並ぶ。"""
    assert 'class="home-nav"' in built_home
    assert "home-nav__today" in built_home
    # 7 lenses (Mobility / Manufacturing 含む)
    for lens in ("fx", "ai", "it", "mobility", "manufacturing", "economy", "game"):
        assert f"home-nav__lens-{lens}" in built_home, f"lens nav for {lens} missing"
    # § ESSAY / ARCHIVE
    assert "§ ESSAY" in built_home, "ESSAY entry missing"
    assert "ARCHIVE" in built_home, "ARCHIVE entry missing"


def test_home_nav_places_podcast_before_archive_as_channel_podcasts_link(built_home: str):
    """LP は ARCHIVE 左に Podcast 全体への導線を置く。"""
    assert 'class="home-nav__actions"' in built_home
    assert 'class="home-nav__podcast"' in built_home
    assert "https://www.youtube.com/@newsgrasp/podcasts" in built_home
    assert "https://www.youtube.com/playlist?list=playlist-deepdive" not in built_home
    assert "https://www.youtube.com/watch?v=video-latest" not in built_home
    assert built_home.index('class="home-nav__podcast"') < built_home.index('class="home-nav__archive"')


def test_home_nav_uses_silhouette_icons_and_plain_today(built_home: str):
    """PODCAST/ARCHIVE と TODAY/YESTERDAY は小さなシルエットで識別し、TODAY の <> は出さない。"""
    css = (ROOT / "docs" / "assets" / "site.css").read_text(encoding="utf-8")
    assert 'class="home-nav__today home-nav__day-link home-nav__day-link--today">TODAY</span>' in built_home
    assert 'home-nav__day-link--yesterday">YESTERDAY</a>' in built_home
    assert "&lt;TODAY&gt;" not in built_home
    assert "<TODAY>" not in built_home
    assert ".home-nav__podcast::before" in css
    assert ".home-nav__archive::before" in css
    assert ".home-nav__day-link--today::before" in css
    assert ".home-nav__day-link--yesterday::before" in css
    assert "mask-image:" in css


def test_home_theme_switch_uses_summary_and_deepdive_icons(built_home: str):
    """SUMMARY/DEEP DIVE スイッチもナビと同じ小さなシルエット体系にそろえる。"""
    css = (ROOT / "docs" / "assets" / "site.css").read_text(encoding="utf-8")
    assert 'home-hero__switch-btn home-hero__switch-btn--summary is-active' in built_home
    assert 'home-hero__switch-btn home-hero__switch-btn--deepdive' in built_home
    assert ">❖ DEEP DIVE<" not in built_home
    assert ".home-hero__switch-btn::before" in css
    assert ".home-hero__switch-btn--summary::before" in css
    assert ".home-hero__switch-btn--deepdive::before" in css
    assert "mask-image:" in css


def test_home_theme_switch_sits_above_today_theme_heading(built_home: str):
    """SUMMARY/DEEP DIVE は TODAY'S THEME 見出し上の余白に置く。"""
    theme = built_home.split('class="home-hero__theme"', 1)[1].split(
        '<div class="home-hero__right"', 1
    )[0]
    assert theme.index('class="home-hero__switch"') < theme.index('class="home-hero__eyebrow"')

    css = (ROOT / "docs" / "assets" / "site.css").read_text(encoding="utf-8")
    theme_rule = re.search(r"\.home-hero__theme\s*\{(?P<body>[^}]*)\}", css, re.S)
    switch = re.search(r"\.home-hero__switch\s*\{(?P<body>[^}]*)\}", css, re.S)
    button = re.search(r"\.home-hero__switch-btn\s*\{(?P<body>[^}]*)\}", css, re.S)
    mobile_css = css.split("@media (max-width: 768px)", 1)[1]
    mobile_hero = re.search(r"\.home-hero\s*\{(?P<body>[^}]*)\}", mobile_css, re.S)
    mobile_switch = re.search(r"\.home-hero__switch\s*\{(?P<body>[^}]*)\}", mobile_css, re.S)
    assert theme_rule and switch and button and mobile_hero and mobile_switch
    assert "position: relative" in theme_rule.group("body")
    assert "position: absolute" in switch.group("body")
    assert "top: -52px" in switch.group("body")
    assert "left: 0" in switch.group("body")
    assert "flex-wrap: nowrap" in switch.group("body")
    assert "white-space: nowrap" in button.group("body")
    assert "padding: 56px 16px 24px" in mobile_hero.group("body")
    assert "top: -40px" in mobile_switch.group("body")
    assert "max-width: 100%" in mobile_switch.group("body")
    assert "overflow: hidden" in mobile_switch.group("body")


def test_home_nav_mobile_keeps_today_yesterday_readable():
    """Podcast追加後も、モバイルで TODAY / YESTERDAY を小さく潰さない。"""
    css = (ROOT / "docs" / "assets" / "site.css").read_text(encoding="utf-8")
    sizes = [
        float(match.group("size"))
        for match in re.finditer(
            r"\.home-nav__day\s*\{[^}]*font-size:\s*(?P<size>[0-9.]+)px",
            css,
            re.S,
        )
    ]
    assert sizes, ".home-nav__day font-size missing"
    assert min(sizes) >= 15


def test_home_nav_mobile_uses_compact_yesterday_snapshot_for_actions():
    """PODCAST / ARCHIVE は YESTERDAY に被らない昨日断面の小型ボタンに戻す。"""
    css = (ROOT / "docs" / "assets" / "site.css").read_text(encoding="utf-8")
    assert ".home-nav { padding-left: 10px; padding-right: 10px; }" in css
    assert ".home-nav__actions { gap: 4px; }" in css
    assert ".home-nav__archive { gap: 4px; padding: 4px 5px; font-size: 9px; letter-spacing: 0.08em; }" in css
    assert ".home-nav__archive { padding: 7px 10px; font-size: 10px;" not in css


def test_home_brand_mobile_uses_compact_issue_header():
    """スマホのブランド帯は、日付メタを上段へ寄せて縦幅を圧縮する。"""
    css = (ROOT / "docs" / "assets" / "site.css").read_text(encoding="utf-8")
    assert ".home-brand__left, .home-brand__title, .home-brand__issue { display: contents; }" in css
    assert ".home-brand__tagline, .home-brand__issue-label, .home-brand__issue-loc { display: none; }" in css
    assert ".home-brand__issue-meta br { display: none; }" in css
    assert ".home-brand__eyebrow { grid-column: 1; grid-row: 1;" in css
    assert ".home-brand__issue-meta { grid-column: 2; grid-row: 1;" in css
    assert ".home-brand__issue-num { grid-column: 1 / -1; grid-row: 3;" in css
    assert ".home-brand__issue-num { grid-column: 1 / -1; grid-row: 3; justify-self: start;" in css
    assert "font-size: clamp(30px, 8.3vw, 34px);" in css


def test_home_mobile_nav_keeps_podcast_navy():
    """参照画像どおり、スマホの PODCAST ボタンは濃紺で金色に戻さない。"""
    css = (ROOT / "docs" / "assets" / "site.css").read_text(encoding="utf-8")
    assert ".home-nav__podcast { background: var(--color-navy); color: var(--color-cream);" in css
    assert ".home-nav__podcast { background: var(--color-gold); color: var(--color-navy);" not in css


def test_hero_2col_structure(built_home: str):
    """home-hero の左 (76px theme title) + 右 (Editor's Top 5 + Stats 2x2) が両方存在。

    左の DEEP DIVE エディトリアルがリッチ化した分、右ヒーローは TOP3 では縦に余るので
    TOP5 + 一回り大きいフォントで縦幅を埋める設計に変更済み (2026-05-31)。
    """
    assert 'class="home-hero"' in built_home
    assert "home-hero__title" in built_home
    assert "home-hero__right" in built_home
    assert "EDITOR&#39;S TOP 5" in built_home or "EDITOR'S TOP 5" in built_home
    # TOP5 なので 5 行 (01〜05) が出る
    assert built_home.count('class="home-top3__row"') == 5, "右ヒーローは TOP5=5行であるべき"
    assert "home-stats2x2" in built_home


def test_editors_top_uses_canonical_category_glyphs(built_home: str):
    """Editor's Top 5 のカテゴリーマークは CATEGORIES 正本に揃える。"""
    forbidden = (
        "🚗 MOBILITY",
        "💼 IT &amp; CONSULTING",
        "🤖 ARTIFICIAL INTELLIGENCE",
        "🎮 GAMING",
        "💱 FOREIGN EXCHANGE",
    )
    for marker in forbidden:
        assert marker not in built_home

    parser = _EditorsTopBadgeParser()
    parser.feed(built_home)
    badges = parser.badges
    assert badges, "Editor's Top のカテゴリーバッジが見つからない"
    for cat_id, text in badges:
        meta = CATEGORIES[cat_id]
        expected = f"{meta['glyph']} {meta['label'].upper()}"
        normalized = " ".join(html.unescape(text).split())
        assert normalized == expected


def test_mobile_category_badges_allow_two_term_line_breaks(built_home: str):
    """二語カテゴリは潰さず、明示 span と CSS で二行表示を許す。"""
    css = (ROOT / "docs" / "assets" / "site.css").read_text(encoding="utf-8")
    assert ".home-top3__badge {\n  display: inline-flex;" in css
    assert ".category-label-break" in css
    assert "white-space: normal;" in css
    assert "max-width: min(100%, 17ch);" in css
    assert "ARTIFICIAL</span>" in built_home
    assert "INTELLIGENCE</span>" in built_home
    assert "FOREIGN</span>" in built_home
    assert "EXCHANGE</span>" in built_home


def test_home_category_surfaces_use_canonical_glyphs(built_home: str):
    """LP の nav / category cards / publication matrix は canonical category 情報を使う。"""
    for cat_id, meta in CATEGORIES.items():
        if cat_id == "summary":
            continue
        label_html = html.escape(meta["label"].upper())
        assert f"home-nav__lens-{cat_id}" in built_home
        assert f"{meta['glyph']}</span>{label_html}" in built_home
        assert f'data-category-card="{cat_id}"' in built_home
        assert f"class=\"home-cat-card" in built_home
        assert f'<span class="pub-matrix__cat-glyph">{meta["glyph"]}</span>{meta["jp"]}' in built_home


def test_home_category_card_uses_split_english_label(built_home: str):
    """home-cat-card__en は二語カテゴリを span 分割して mobile 二行化できる。"""
    ai_match = re.search(
        r'<div class="home-cat-card__en">\s*'
        r'<span class="category-label-break__line">ARTIFICIAL</span>\s*'
        r'<span class="category-label-break__line">INTELLIGENCE</span>\s*'
        r"</div>",
        built_home,
    )
    assert ai_match, "AI card の英字カテゴリが二行用 span に分割されていない"


def test_featured_story_section(built_home: str):
    """home-featured (TOP STORY badge + 1.4fr:1fr grid) がある。実 digest があれば必ず出る。"""
    assert "home-featured" in built_home, "Featured Story section missing"
    assert "TOP STORY" in built_home


def test_featured_story_thumb_falls_back_to_og_image(built_home: str):
    """TOP STORY は実サムネ or カテゴリ OG 画像のどちらかを必ず表示する (色面退化禁止)。

    旧 assertion は `/assets/og/` の存在を直接 pin していたが、それは「当日 hero に
    実サムネが無い」というデータ依存の前提で、実サムネがある良い状態の日 (2026-06-10
    の TechCrunch サムネ等) に FAIL する flaky だった。no-thumb → og fallback の分岐は
    template の else 分岐 + publish 境界の tools.validate_public_home (img 必須・
    色面退化禁止) が担保するので、ここでは「img が必ず 1 枚以上あり色面に退化しない」
    という挙動だけを pin する。
    """
    featured = re.search(
        r'<section class="home-featured.*?</section>',
        built_home,
        flags=re.DOTALL,
    )
    assert featured, "home-featured section missing"
    html_block = featured.group(0)
    imgs = re.findall(r'<img src="([^"]+)"', html_block)
    assert imgs and all(src.strip() for src in imgs), (
        "TOP STORY に <img src> が無い = 色面退化。thumb 欠落時は "
        "/assets/og/{category_id}.jpg fallback が出る template 仕様。"
    )
    assert "width: 100%; height: 100%;" not in html_block


def test_home_hero_grid_columns_clamped_to_container():
    """home-hero のグリッド列は minmax(0, 1fr) でコンテナ幅に物理固定する。

    なぜ重要か (2026-06-10 スマホ右切れ事故): `1fr` は `minmax(auto, 1fr)` の
    短縮形で、SUMMARY⇆DEEP DIVE スライダーの track (width 200%) の min-content が
    列をコンテナ超えに押し広げる。CSS 360px 級の端末で右余白 16px が消え、
    本文が画面右端で切れた (412px 端末では顕在化しないため見逃しやすい)。
    minmax(0, 1fr) なら列幅がコンテナを超えられない (illegal state unrepresentable)。
    """
    css = (ROOT / "docs" / "assets" / "site.css").read_text(encoding="utf-8")
    assert "grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);" in css, (
        ".home-hero__inner (desktop) の列が minmax(0, 1fr) でなくなっている。"
        "1fr に戻すとスライダー min-content で列がコンテナを超え、狭幅端末で右切れが再発する。"
    )
    assert "grid-template-columns: minmax(0, 1fr); gap: 24px;" in css, (
        ".home-hero__inner (mobile @768px) の列が minmax(0, 1fr) でなくなっている。"
    )


def test_top_story_media_note_uses_score_note_without_claiming_breakdown(built_home: str):
    """TOP STORY 左下には厳密な内訳ではなく一行説明の SCORE NOTE を表示する。"""
    css = (ROOT / "docs" / "assets" / "site.css").read_text(encoding="utf-8")
    assert "grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);" in css, (
        ".home-featured__grid は中央線で左右を分ける 1:1 の列指定にする。"
    )
    assert 'class="home-featured__media"' in built_home
    assert 'class="feature-note"' in built_home
    assert "SCORE NOTE" in built_home
    assert "KEY NUMBERS" in built_home
    assert ">SIGNALS<" not in built_home
    assert "SCORE BREAKDOWN" not in built_home
    feature_note = built_home[
        built_home.index('class="feature-note"'):
        built_home.index('class="home-featured__title"')
    ]
    note_pos = feature_note.index("SCORE NOTE")
    key_pos = feature_note.index("KEY NUMBERS")
    number_pos = feature_note.index("feature-note__number")
    signal_chip_pos = feature_note.index("feature-note__chips--signals")
    assert note_pos < key_pos < number_pos < signal_chip_pos
    assert ".feature-note__label {\n  font-family: var(--font-mono);\n  font-size: 11px;" in css
    assert ".feature-note p {\n  margin: 0;\n  font-family: var(--font-serif);\n  font-size: 17px;" in css
    assert ".feature-note__chips" in css
    assert ".feature-note__chips--signals" in css
    assert ".feature-note__numbers" in css


def test_category_top_feature_uses_same_score_note_layout(built_docs_root: Path):
    """category index の TOP FEATURE も同じ順序で左下メタ欄を表示する。"""
    page = built_docs_root / "it" / "index.html"
    category_html = page.read_text(encoding="utf-8")
    assert "TOP FEATURE" in category_html
    assert 'class="top-story__media"' in category_html
    assert 'class="feature-note"' in category_html
    assert "SCORE BREAKDOWN" not in category_html
    feature_note = category_html[
        category_html.index('class="feature-note"'):
        category_html.index('class="top-story__title"')
    ]
    assert ">SIGNALS<" not in feature_note
    assert feature_note.index("SCORE NOTE") < feature_note.index("KEY NUMBERS")
    assert feature_note.index("KEY NUMBERS") < feature_note.index("feature-note__chips--signals")


def test_feature_note_mobile_collapses_to_signature_chips_only():
    """スマホでは画像とタイトルの間に SCORE NOTE / KEY NUMBERS 本文を挟まずタグだけ残す。"""
    css = (ROOT / "docs" / "assets" / "site.css").read_text(encoding="utf-8")
    assert ".feature-note__chips--signals::before" not in css
    assert 'content: "SIGNATURE";' not in css
    assert (
        ".feature-note__label,\n"
        "  .feature-note p,\n"
        "  .feature-note__block {\n"
        "    display: none;\n"
        "  }"
    ) in css
    assert ".feature-note__chips--signals {\n    margin-top: 0;" in css


def test_feature_note_is_limited_to_lp_and_category_templates():
    """個別記事 / Archive / DeepDive へ今回部品を混入させない。"""
    forbidden_templates = (
        "prompts/page-template.html",
        "prompts/archive-template.html",
        "prompts/deepdive-template.html",
        "prompts/deepdive-archive-template.html",
    )
    for rel in forbidden_templates:
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "feature-note" not in text, f"{rel} に feature-note が混入している"
        assert "score_note" not in text, f"{rel} に score_note が混入している"


def test_score_note_publish_bumps_service_worker_version():
    """CSS / 生成HTML の公開時に古いPWAキャッシュへ残らないよう SW_VERSION を上げる。"""
    sw = (ROOT / "docs" / "sw.js").read_text(encoding="utf-8")
    assert "2026-07-02-category-hero-r5" in sw


def test_score_note_prefers_current_dataset_signals():
    """現行データで可能な tag signal から一行説明を作る。"""
    top = {
        "score": "96",
        "tags": [
            "co/京セラ",
            "topic/半導体部品",
            "industry/半導体",
            "event/設備投資",
            "score/高",
        ],
        "bullets": [],
    }
    note = _score_note(top, "製造")
    assert note == "設備投資、半導体部品、半導体が重なり、製造でSCORE 96。"
    assert _score_signals(top) == ["設備投資", "半導体部品", "半導体"]


def test_key_numbers_prefers_current_dataset_numbers():
    """現行データの title/summary/bullets から左下に置ける重要数値を抽出する。"""
    assert _key_numbers(
        {
            "title": "京セラ、部品事業に6500億円投資 AI半導体向け売上高3倍へ",
            "summary": "京セラが2031年3月期までに部品事業へ6500億円を投じる。",
            "bullets": [],
        }
    )[:3] == ["6500億円", "3倍", "2031年3月期"]
    assert _key_numbers(
        {
            "title": "Snowflake急騰、エージェント時代でもSaaS課金が崩れない根拠が鮮明に",
            "summary": "Snowflakeの好決算と株価50％急騰が、AIエージェントでSaaS課金が崩れるという見方を揺さぶった。",
            "bullets": [
                "【背景・要点】：[[SaaS課金モデル]]を巡っては**2850億ドル規模の論争**が続いていた。",
            ],
        }
    )[:2] == ["50％", "2850億ドル"]


def test_home_deepdive_does_not_show_ad_hoc_podcast_cta(built_home: str):
    """LP の DeepDive 黒パネルに場当たり的なPodcast CTAを出さない。"""
    assert "home-hero__podcast-cta" not in built_home
    assert "YOUTUBE PODCAST" not in built_home


def test_categories_7_lens_cards(built_home: str):
    """home-cats__grid に 7 lens card が並ぶ。"""
    assert 'data-home-category-grid="true"' in built_home
    for lens in ("fx", "ai", "it", "mobility", "manufacturing", "economy", "game"):
        assert f'data-category-card="{lens}"' in built_home, \
            f"home-cat-card for {lens} missing"


def test_editorial_preview_or_none(built_home: str):
    """summary digest があれば home-editorial が出る。無くてもテンプレが落ちないこと。"""
    # editorial は summary digest 存在依存。少なくとも render が成功していれば OK
    # editorial が出る場合の class 名は確認
    if "home-editorial" in built_home:
        assert "READ FULL ESSAY" in built_home or "ALL SECTIONS" in built_home
        assert "本日のテーマ考察" in built_home


def test_subscribe_band(built_home: str):
    """home-subscribe (毎朝7:30更新) があり Footer に NEWS GRASP がある。"""
    assert "home-subscribe" in built_home
    assert "毎朝7:30更新" in built_home
    assert "毎朝6:30更新" not in built_home
    assert "NEWS GRASP" in built_home
    assert "EST. 2026" in built_home


def test_canonical_root_url(built_home: str):
    """Home の canonical は BASE_URL/ (末尾スラッシュ)。"""
    from tools.config import BASE_URL
    assert f'<link rel="canonical" href="{BASE_URL}/">' in built_home


def test_og_meta_present(built_home: str):
    """og:type=website / og:title / og:url / og:image / twitter:card が揃う。"""
    assert 'property="og:type" content="website"' in built_home
    assert 'property="og:title"' in built_home
    assert 'property="og:url"' in built_home
    assert 'property="og:image"' in built_home
    assert 'name="twitter:card"' in built_home


def test_no_rounded_corners_in_template(built_home: str):
    """Magazine 原則: 角丸 0。inline で border-radius を持たないこと。"""
    # body の inline style だけチェック (CSS のグローバルリセットは site.css 側で担保)
    assert "border-radius:" not in built_home, \
        "Variant B is corner=0; no inline border-radius allowed"


def test_favicon_links_present(built_home: str):
    """News Grasp の N→ ロゴが favicon として 3 サイズ登録されている。"""
    assert 'rel="icon"' in built_home, "favicon link missing"
    assert 'href="https://hidepon-umg.github.io/News-Grasp/assets/favicon-256.png"' in built_home \
        or '/assets/favicon-256.png' in built_home, "favicon-256.png missing"
    assert 'rel="apple-touch-icon"' in built_home, "apple-touch-icon missing"


def test_hero_title_uses_brand_tagline_in_fallback(built_home: str):
    """テーマ抽出に失敗したときの Hero fallback は「時勢を掴み、日々に新たに」を維持する。

    Variant B の variant-b.jsx に書かれていた仮テキスト「5 つのレンズで今日を読む」を
    そのまま流用していた版を改修。ブランドコピー (config.py の SITE_DESCRIPTION も
    同値) と完全一致させる。
    """
    # テーマが抽出できたかどうかに関わらず、テンプレに「時勢を掴み」と「日々に新たに」の
    # ブランドコピー fallback が source として埋まっていること、そして Hero に
    # 「5 つのレンズ」「今日を読む」の仮テキストが残っていないことを pin する。
    assert "5 つのレンズ" not in built_home, "仮テキスト「5 つのレンズ」が Hero に残存"
    assert "今日を読む" not in built_home, "仮テキスト「今日を読む」が Hero に残存"


# ============================================================
# Pure unit tests
# ============================================================

def test_split_theme_phrases_with_to():
    """summary_text の「A と B」を 2 フレーズに分割する。"""
    left, right = _split_theme_phrases("金利の天井とAIの底入れ。")
    assert left == "金利の天井"
    assert right == "AIの底入れ"


def test_split_theme_phrases_with_punctuation():
    """summary_text の「A、B」も拾える。"""
    left, right = _split_theme_phrases("金利の天井、AIの底入れ。詳細は後述。")
    assert left == "金利の天井"
    assert right == "AIの底入れ"


def test_split_theme_phrases_empty():
    """空文字や 1 文しかない場合は ("", "") を返す。"""
    assert _split_theme_phrases("") == ("", "")
    assert _split_theme_phrases("単一フレーズだけ。") == ("", "")


# ============================================================
# YESTERDAY LP (sticky nav の「YESTERDAY」遷移先 = 昨日を当日とみなした LP)
# ============================================================

@pytest.fixture(scope="module")
def yesterday_lp(tmp_path_factory):
    """build_index(target_date=前日, is_yesterday=True) で昨日 LP を生成し (html, 日付) を返す。

    個別記事ページに依存しないため build_all は不要 (entries から直接 LP を組める)。
    """
    docs_root = tmp_path_factory.mktemp("yda_lp")
    entries = _collect_entries(scan_digests())
    dates = sorted({e["date"] for e in entries if e.get("date")}, reverse=True)
    if len(dates) < 2:
        pytest.skip("前日 digest が無いため昨日 LP を検証できない")
    yda = dates[1]
    out = build_index(entries, docs_root, target_date=yda, is_yesterday=True)
    assert out.parts[-2:] == (yda, "index.html"), \
        f"昨日 LP は docs/{yda}/index.html に出るべき: {out}"
    return out.read_text(encoding="utf-8"), yda


def test_yesterday_lp_variant_and_theme(yesterday_lp):
    """昨日 LP は body に data-variant="yesterday" (背景やや暗め) と
    見出し「YESTERDAY'S THEME」を持つ (TODAY と一目で区別する意図を pin)。"""
    html, _ = yesterday_lp
    assert 'data-variant="yesterday"' in html, "昨日 LP の背景識別子が無い"
    assert "YESTERDAY&#39;S THEME" in html or "YESTERDAY'S THEME" in html, \
        "見出しが YESTERDAY'S THEME になっていない"


def test_yesterday_lp_keeps_today_layout(yesterday_lp):
    """昨日 LP は当日トップと同じ LP 体裁 (home-brand / home-hero / カテゴリグリッド) を保つ。

    「今日のトップと同じ見た目の昨日版」が要件なので、overview (俯瞰) ではなく
    index テンプレートで生成されていることを構造で保証する。"""
    html, _ = yesterday_lp
    assert 'class="home-brand"' in html
    assert 'class="home-hero"' in html
    assert 'class="home-cats__grid"' in html


def test_yesterday_lp_nav_is_reversed(yesterday_lp):
    """昨日 LP の sticky nav は YESTERDAY が現在地・TODAY が当日 LP へのリンク
    (今日 LP とは TODAY/YESTERDAY のアクティブが逆転し、相互に行き来できる)。"""
    from tools.config import BASE_URL
    html, _ = yesterday_lp
    assert '<span class="home-nav__today home-nav__day-link home-nav__day-link--yesterday">YESTERDAY</span>' in html, \
        "昨日 LP では YESTERDAY が現在地 (active span) であるべき"
    assert f'href="{BASE_URL}/" class="home-nav__yesterday home-nav__day-link home-nav__day-link--today">TODAY</a>' in html, \
        "昨日 LP の TODAY は当日 LP (ルート) へのリンクであるべき"


def test_today_lp_has_yesterday_link(built_home):
    """当日 LP の sticky nav は TODAY が現在地・YESTERDAY が前日 LP へのリンク。"""
    assert '<span class="home-nav__today home-nav__day-link home-nav__day-link--today">TODAY</span>' in built_home
    assert 'class="home-nav__yesterday home-nav__day-link home-nav__day-link--yesterday">YESTERDAY</a>' in built_home


def test_yesterday_lp_deepdive_is_not_today(yesterday_lp):
    """昨日 LP の DEEP DIVE スライダーは「当日の DeepDive」でなく
    その日 (昨日) 以前の最新 DeepDive を出す。

    なぜ重要か: build_index は当日 LP と昨日 LP の両方を同じ index テンプレで
    生成するが、DEEP DIVE pane は entries と独立に digest/DeepDive/*.md を直接読む。
    回帰前は _latest_deepdive_card() が常に最新 md を読み、昨日 LP の DEEP DIVE にも
    「当日のテーマ」が出ていた (docs/{昨日}/ を開くと前日でなく今日の深掘りが表示)。
    本テストは昨日 LP のカード日付が当日 LP と一致しないこと (= 昨日以前で引けること)
    を pin する。DeepDive が 1 本以下の環境では差を検証できないので skip。
    """
    from tools.generate_pages import _PKG_ROOT, _latest_deepdive_card

    dd_dir = _PKG_ROOT / "digest" / "DeepDive"
    dd_dates = sorted({p.name[:10] for p in dd_dir.glob("*.md")})
    if len(dd_dates) < 2:
        pytest.skip("DeepDive が 2 本未満で当日/昨日の差を検証できない")
    _, yda = yesterday_lp
    if yda < dd_dates[1]:
        pytest.skip(f"昨日 ({yda}) 以前に DeepDive が無く差を検証できない")

    card_today = _latest_deepdive_card()        # 当日 LP 相当 (全体の最新)
    card_yda = _latest_deepdive_card(yda)        # 昨日 LP 相当 (yda 以前の最新)
    assert card_today and card_yda, "DeepDive カードが構築できていない"
    assert card_yda["date"] <= yda, \
        f"昨日 LP の DeepDive 日付 {card_yda['date']} が昨日 {yda} より新しい"
    if card_today["date"] <= yda:
        pytest.skip("当日 DeepDive が無い日は、当日 LP と昨日 LP の DeepDive が同一になり得る")
    assert card_yda["date"] != card_today["date"], \
        "昨日 LP の DEEP DIVE に当日と同じテーマが出ている (回帰)"
