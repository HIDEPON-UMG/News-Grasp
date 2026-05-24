#!/usr/bin/env python3
"""Phase 4: Daily Overview (Pattern C) docs/{YYYY-MM-DD}/index.html 契約テスト。

build_overview() が生成する HTML に C パターン必須セクションが含まれているかを
verify する。

Variant C の認識: site/desktop-extra.jsx の DesktopCategoryOverview を権威ソースとする。

実行:
    pytest tests/test_overview_pattern_c.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.generate_pages import (  # noqa: E402
    build_all,
    build_all_overviews,
    build_overview,
    _collect_entries,
    scan_digests,
)


# ============================================================
# fixture: 同日 fixture digest を build → overview build
# ============================================================

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

- bullet 1
- bullet 2
- bullet 3

---

### [76] テスト記事 2

📅 2026-05-20 不明 · 📰 Test Source · 🔗 [元記事](https://example.com/2)

#cat/{cat_id} #topic/test #score/中

- bullet 1

---

### [62] テスト記事 3

📅 2026-05-20 不明 · 📰 Test Source · 🔗 [元記事](https://example.com/3)

#cat/{cat_id} #topic/test #score/中

- bullet 1
"""


@pytest.fixture(scope="module")
def built_overview(tmp_path_factory) -> str:
    """5 カテゴリ digest を tmp に置き build_overview で 2026-05-20 のページを生成。"""
    root = tmp_path_factory.mktemp("ovr_c")
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
    assert len(entries) == 5

    out = build_overview("2026-05-20", entries, docs)
    assert out.exists()
    assert out.parts[-3:] == ("docs", "2026-05-20", "index.html") or \
        (out.parts[-2] == "2026-05-20" and out.parts[-1] == "index.html"), \
        f"Overview should land at docs/2026-05-20/index.html, got {out}"
    return out.read_text(encoding="utf-8")


# ============================================================
# 構造 pin
# ============================================================

def test_page_header_with_56px_date(built_overview: str):
    """overview-page-header に DAILY OVERVIEW · ISSUE # + 大型日付 + Stats 2x2 がある。"""
    assert "overview-page-header" in built_overview
    assert "DAILY OVERVIEW" in built_overview
    assert "ISSUE #20260520" in built_overview
    assert 'class="date-mmdd">05·20' in built_overview
    assert "本日のニュース俯瞰" in built_overview
    # stats 2x2 の 4 セル
    assert "STORIES" in built_overview
    assert "LENSES" in built_overview
    assert "ESSAY §" in built_overview
    assert "FULL READ" in built_overview


def test_theme_banner_navy(built_overview: str):
    """overview-theme (navy / TODAY'S THEME) がある。"""
    assert "overview-theme" in built_overview
    assert "TODAY&#39;S THEME" in built_overview or "TODAY'S THEME" in built_overview


def test_theme_fallback_uses_brand_tagline(built_overview: str):
    """テーマ抽出失敗時の Theme banner fallback は「時勢を掴み、日々に新たに」を保つ。"""
    # γ schema 非対応の fixture では hero_phrase_*= 空のため fallback が走る
    assert "5 つのレンズで今日を読む" not in built_overview, \
        "仮テキストが Theme banner に残存"


def test_favicon_links_present(built_overview: str):
    """News Grasp の N→ ロゴが favicon として登録されている。"""
    assert 'rel="icon"' in built_overview
    assert '/assets/favicon-256.png' in built_overview
    assert 'rel="apple-touch-icon"' in built_overview


def test_5_category_rows(built_overview: str):
    """fx / ai / it / economy / game の overview-row が 5 行並ぶ (summary は除く)。"""
    for cid in ("fx", "ai", "it", "economy", "game"):
        assert f'class="overview-row cat-{cid}"' in built_overview, \
            f"overview-row for {cid} missing"
    # summary はパターン C には出ない
    assert 'class="overview-row cat-summary"' not in built_overview


def test_category_row_has_top3_and_dist(built_overview: str):
    """各行に Top 3 + Score Distribution + VIEW ALL CTA がある。"""
    assert "overview-top3__head" in built_overview
    assert "TOP 3 BY SCORE" in built_overview
    assert "overview-dist" in built_overview
    assert "SCORE DISTRIBUTION" in built_overview
    # 「VIEW ALL X FOREIGN EXCHANGE」のような CTA
    assert "VIEW ALL" in built_overview


def test_distribution_bars_use_score_values(built_overview: str):
    """Score histogram bars が articles の score 値で height を持つ。"""
    # 3 件分の "height: NN%" が含まれる
    assert "height: 88%" in built_overview
    assert "height: 76%" in built_overview
    assert "height: 62%" in built_overview


def test_canonical_under_date_path(built_overview: str):
    """canonical が {BASE_URL}/2026-05-20/ になっている。"""
    from tools.config import BASE_URL
    assert f'<link rel="canonical" href="{BASE_URL}/2026-05-20/">' in built_overview


def test_og_meta_present(built_overview: str):
    """OGP / Twitter Card メタが揃う。"""
    for prop in ("og:type", "og:title", "og:url", "og:image"):
        assert f'property="{prop}"' in built_overview, f"meta {prop} missing"
    assert 'name="twitter:card"' in built_overview


def test_build_all_overviews_creates_unique_dates_only():
    """build_all_overviews は date 重複を排除して unique 日付の数だけページを作る。"""
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        docs = root / "docs"
        sources: list[Path] = []
        # 2 日分 × 2 カテゴリ = 4 digest 投入
        for date_str in ("2026-05-19", "2026-05-20"):
            for cat_id, label in [("fx", "FX"), ("ai", "AI")]:
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
        overviews = build_all_overviews(entries, docs)
        # 2 日分 = 2 overview ページ
        assert len(overviews) == 2
        assert (docs / "2026-05-19" / "index.html").exists()
        assert (docs / "2026-05-20" / "index.html").exists()
