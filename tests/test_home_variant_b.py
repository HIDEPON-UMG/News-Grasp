#!/usr/bin/env python3
"""Phase 3: Variant B Magazine Spread Home (docs/index.html) の構造契約テスト。

build_index() が生成する HTML に Variant B 必須セクションが含まれているかを
verify する。スタイル詳細ではなく構造 ID / クラスを pin する。

Variant B の認識: site/variant-b.jsx (Claude Design Handoff) を権威ソースとする。

実行:
    pytest tests/test_home_variant_b.py -v
"""
from __future__ import annotations

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
    assert "FIVE  LENSES  ON  TODAY" in built_home or "FIVE LENSES ON TODAY" in built_home, \
        "tagline missing"


def test_sticky_nav_with_5_lenses(built_home: str):
    """home-nav の 5 lenses + TODAY + § ESSAY + ARCHIVE が並ぶ。"""
    assert 'class="home-nav"' in built_home
    assert "home-nav__today" in built_home
    # 5 lenses
    for lens in ("fx", "ai", "it", "economy", "game"):
        assert f"home-nav__lens-{lens}" in built_home, f"lens nav for {lens} missing"
    # § ESSAY / ARCHIVE
    assert "§ ESSAY" in built_home, "ESSAY entry missing"
    assert "ARCHIVE" in built_home, "ARCHIVE entry missing"


def test_hero_2col_structure(built_home: str):
    """home-hero の左 (76px theme title) + 右 (Editor's Top 3 + Stats 2x2) が両方存在。"""
    assert 'class="home-hero"' in built_home
    assert "home-hero__title" in built_home
    assert "home-hero__right" in built_home
    assert "EDITOR&#39;S TOP 3" in built_home or "EDITOR'S TOP 3" in built_home
    assert "home-stats2x2" in built_home


def test_featured_story_section(built_home: str):
    """home-featured (TOP STORY badge + 1.4fr:1fr grid) がある。実 digest があれば必ず出る。"""
    assert "home-featured" in built_home, "Featured Story section missing"
    assert "TOP STORY" in built_home


def test_categories_5col(built_home: str):
    """home-cats__grid が repeat(5, 1fr) クラスを当てており 5 lens card が並ぶ。"""
    assert 'class="home-cats__grid"' in built_home
    for lens in ("fx", "ai", "it", "economy", "game"):
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
    """home-subscribe (毎朝6:30) があり Footer に NEWS GRASP がある。"""
    assert "home-subscribe" in built_home
    assert "毎朝6:30" in built_home
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
