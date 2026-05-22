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

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.generate_pages import (  # noqa: E402
    build_all,
    build_all_summaries,
    build_summary,
    _collect_entries,
    _SUMMARY_SECTION_TAGS,
    _SUMMARY_SECTION_COLORS,
)


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

def test_mode_pill_navigates_to_digest(built_summary: str):
    """NEWS DIGEST / § EDITORIAL SUMMARY pill があり、§ EDITORIAL SUMMARY が active。"""
    assert "summary-mode-pill" in built_summary
    assert "NEWS DIGEST" in built_summary
    assert "§ EDITORIAL SUMMARY" in built_summary
    assert 'class="summary-mode-pill__on"' in built_summary


def test_hero_dark_navy(built_summary: str):
    """summary-hero (dark navy) に EDITORIAL DIGEST · ISSUE # + 56px 大見出しがある。"""
    assert 'class="summary-hero"' in built_summary
    assert "EDITORIAL DIGEST" in built_summary
    assert "ISSUE #20260520" in built_summary
    assert "本日のテーマ考察" in built_summary
    # 4 stats
    assert "SECTIONS" in built_summary
    assert "MIN READ" in built_summary
    assert "TAKEAWAYS" in built_summary
    assert "SOURCES" in built_summary
    # 200px § sigil
    assert 'class="summary-hero__sigil">§' in built_summary


def test_7_sections_always_rendered(built_summary: str):
    """γ schema 無しでも fallback で 7 sections (§01-§07) が必ず出る。"""
    for i in range(1, 8):
        assert f"§{i:02d}" in built_summary, f"§{i:02d} missing"
    # 固定 tag
    for tag in _SUMMARY_SECTION_TAGS:
        assert f'>{tag}<' in built_summary, f"section tag {tag!r} missing"


def test_section_accent_colors_used(built_summary: str):
    """各§の accent color (固定 7 色) が inline style に出ている。"""
    for color in _SUMMARY_SECTION_COLORS:
        assert color in built_summary, f"section color {color} missing"


def test_3_takeaways_always_rendered(built_summary: str):
    """KEY TAKEAWAYS に必ず 3 件出る。"""
    assert "KEY TAKEAWAYS" in built_summary
    assert "今日の 3 つの結論" in built_summary
    assert "summary-take__n" in built_summary
    # 番号 01 / 02 / 03 が出る
    assert ">01<" in built_summary
    assert ">02<" in built_summary
    assert ">03<" in built_summary


def test_cta_band(built_summary: str):
    """CTA band: 全 N 本のニュースを読む + 過去の考察を見る。"""
    assert "summary-cta" in built_summary
    assert "本のニュースを読む" in built_summary
    assert "過去の考察を見る" in built_summary


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


def test_pull_quote_hidden_in_fallback(built_summary: str):
    """γ schema 未対応の fallback では pull quote セクションは出ない。"""
    # pull_quote.text が空のときは class="summary-pull" のセクションごと出ないこと
    assert 'class="summary-pull"' not in built_summary, \
        "Pull quote should be hidden when γ pull_quote is empty (fallback)"


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
