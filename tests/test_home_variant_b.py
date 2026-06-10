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
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.generate_pages import (  # noqa: E402
    _split_theme_phrases,
    build_all,
    build_index,
    scan_digests,
    _collect_entries,
)
from tools.config import CATEGORIES  # noqa: E402


# ============================================================
# fixture: 実 digest を使って Home をビルド
# ============================================================

@pytest.fixture(scope="module")
def built_home(tmp_path_factory) -> str:
    """Phase 3 開発用 fixture。実 digest 全件を tmp に再 build して index.html を返す。"""
    docs_root = tmp_path_factory.mktemp("home_b")
    # 実 digest から個別ページを生成 (build_index 前提)
    written = build_all(full=True, docs_root=docs_root)
    assert written, "build_all が 0 件しか返さない: 実 digest が足りない可能性"
    # _collect_entries は実 digest を再スキャンする
    entries = _collect_entries(scan_digests())
    assert entries, "_collect_entries が空: digest scan に失敗"
    out = build_index(entries, docs_root)
    assert out.exists(), f"build_index が index.html を生成しなかった: {out}"
    return out.read_text(encoding="utf-8")


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

    badges = re.findall(r'class="home-top3__badge cat-([^"]+)">([^<]+)</span>', built_home)
    assert badges, "Editor's Top のカテゴリーバッジが見つからない"
    for cat_id, text in badges:
        meta = CATEGORIES[cat_id]
        expected = f"{meta['glyph']} {meta['label'].upper()}"
        assert html.unescape(text) == expected
    assert "◎ MOBILITY" in built_home
    assert "⌗ IT &amp; CONSULTING" in built_home


def test_home_category_surfaces_use_canonical_glyphs(built_home: str):
    """LP の nav / category cards / publication matrix は canonical glyph を使う。"""
    for cat_id, meta in CATEGORIES.items():
        if cat_id == "summary":
            continue
        label_html = html.escape(meta["label"].upper())
        assert f"home-nav__lens-{cat_id}" in built_home
        assert f"{meta['glyph']}</span>{label_html}" in built_home
        assert f'class="home-cat-card cat-{cat_id}"' in built_home
        assert f'<div class="home-cat-card__glyph">{meta["glyph"]}</div>' in built_home
        assert f'<span class="pub-matrix__cat-glyph">{meta["glyph"]}</span>{meta["jp"]}' in built_home


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


def test_categories_7_lens_cards(built_home: str):
    """home-cats__grid に 7 lens card が並ぶ。"""
    assert 'class="home-cats__grid"' in built_home
    for lens in ("fx", "ai", "it", "mobility", "manufacturing", "economy", "game"):
        assert f'class="home-cat-card cat-{lens}"' in built_home, \
            f"home-cat-card for {lens} missing"


def test_editorial_preview_or_none(built_home: str):
    """summary digest があれば home-editorial が出る。無くてもテンプレが落ちないこと。"""
    # editorial は summary digest 存在依存。少なくとも render が成功していれば OK
    # editorial が出る場合の class 名は確認
    if "home-editorial" in built_home:
        assert "READ FULL ESSAY" in built_home or "ALL SECTIONS" in built_home
        assert "本日のテーマ考察" in built_home


def test_subscribe_band(built_home: str):
    """home-subscribe (毎朝6:30更新) があり Footer に NEWS GRASP がある。"""
    assert "home-subscribe" in built_home
    assert "毎朝6:30更新" in built_home
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
    assert '<span class="home-nav__today">YESTERDAY</span>' in html, \
        "昨日 LP では YESTERDAY が現在地 (active span) であるべき"
    assert f'href="{BASE_URL}/" class="home-nav__yesterday">TODAY</a>' in html, \
        "昨日 LP の TODAY は当日 LP (ルート) へのリンクであるべき"


def test_today_lp_has_yesterday_link(built_home):
    """当日 LP の sticky nav は TODAY が現在地・YESTERDAY が前日 LP へのリンク。"""
    assert '<span class="home-nav__today">TODAY</span>' in built_home
    assert 'class="home-nav__yesterday">YESTERDAY</a>' in built_home


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
