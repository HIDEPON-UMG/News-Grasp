#!/usr/bin/env python3
"""カテゴリ索引ページ (/{cat}/) の grid fallback が強調記法を描画する契約テスト。

背景 (2026-06-04 / Manufacturing 初回 backfill で露見):
    build_category_pages は、digest が 1 日分しか無い新設カテゴリでは grid を
    _articles_as_grid_entries で埋める。旧実装はここで articles.jsonl の **raw summary**
    を summary_text にそのまま渡し top_bullets を空にしていたため、category-template の
    more-card は ``{{ e.summary_text|truncate }}`` 経路 (render_emph 無し) に落ち、
    `**bold**` や `[[wikilink]]` が **生 markdown のまま** 1 行だけ表示されていた
    (3 箇条も出ず強調もされない)。Mobility 等の複数日付カテゴリは別経路のため無事だった。

検証する意図 (= この class of bugs を 1 テストで封じる):
    - 単一日付・複数記事の新設カテゴリでも、grid (非 featured 記事) が
      整形済み bullets (<strong class="emph-bold"> / <span class="emph-und">) で描画される
    - 生マーカー ``[[`` / ``**`` / ``__`` が索引ページに 1 つも残らない (回帰防止の核)
    - grid に featured 以外の記事が並ぶ (1 記事だけに退化しない)
    - 純関数 _articles_as_grid_entries が top_bullets を埋め summary_text を空にする

実行:
    pytest tests/test_category_grid_fallback_emphasis.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.generate_pages import (  # noqa: E402
    build_all,
    build_category_pages,
    _collect_entries,
    _articles_as_grid_entries,
)

# 単一日付・3 記事。各 bullet に [[ ]] / ** ** / __ __ を仕込み、sentinel で
# 「整形されたか」「生マーカーが残らないか」を文字列一致で検証する。
_DIGEST = """---
title: "News Grasp #20260603 — Manufacturing"
date: 2026-06-03
issue: 20260603
weekday: 水
category: Manufacturing
categoryId: manufacturing
accent: "#5A6B7B"
glyph: "⬢"
edition: Morning Edition
---

# ⬢ 製造 — Manufacturing

> [!summary]
> 単一日付カテゴリのサマリ。マーカー無しの素テキスト。

---

### [90] フィーチャー記事タイトル SENT_FEAT

📅 2026-06-03 07:30 · 📰 Feat Source · 🔗 [元記事](https://example.com/feat)

#cat/manufacturing #co/Feat #score/高

- [[SENT_FEATWIKI]] が**SENT_FEATBOLD**を発表。__SENT_FEATUND__が含意
- 2 本目の bullet も普通に出る
- 3 本目の bullet

---

### [80] グリッド記事二 SENT_G2TITLE

📅 2026-06-03 08:00 · 📰 G2 Source · 🔗 [元記事](https://example.com/g2)

#cat/manufacturing #co/G2 #score/中

- 台湾の[[SENT_G2WIKI]] が**SENT_G2BOLD**で先行し__SENT_G2UND__が要点
- G2 の 2 本目
- G2 の 3 本目

---

### [70] グリッド記事三 SENT_G3TITLE

📅 2026-06-03 08:30 · 📰 G3 Source · 🔗 [元記事](https://example.com/g3)

#cat/manufacturing #co/G3 #score/中

- [[SENT_G3WIKI]] と**SENT_G3BOLD**、__SENT_G3UND__で締める
- G3 の 2 本目
- G3 の 3 本目

---
"""

_SUNDAY_AI_DIGEST = """---
title: "News Grasp #20260607 — Artificial Intelligence"
date: 2026-06-07
issue: 20260607
weekday: 日
category: Artificial Intelligence
categoryId: ai
accent: "#2D5BB8"
glyph: "◆"
edition: Morning Edition
---

# ◆ AI — Artificial Intelligence

> [!summary]
> 日曜の AI サマリ。

---

### [90] 日曜 AI 記事 SENT_AI_SUN

📅 2026-06-07 07:30 · 📰 AI Source · 🔗 [元記事](https://example.com/ai-sun)

#cat/ai #co/AI #score/高

- AI の bullet
- 2 本目
- 3 本目

---
"""


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    root = tmp_path_factory.mktemp("cat_grid_fallback")
    docs = root / "docs"
    d = root / "digest" / "Manufacturing"
    d.mkdir(parents=True, exist_ok=True)
    src = d / "2026-06-03-Manufacturing.md"
    src.write_text(_DIGEST, encoding="utf-8")
    sources = [src]
    # digests=sources を渡すことで grid fallback が tmp digest を走査する (実リポ非依存)。
    build_all(full=True, docs_root=docs, digests=sources)
    entries = _collect_entries(sources)
    build_category_pages(entries, docs, digests=sources)
    return (docs / "manufacturing" / "index.html").read_text(encoding="utf-8")


def test_grid_renders_emphasis_not_raw_markdown(built):
    """grid の非 featured 記事が整形済み (emph-bold / emph-und) で出る。"""
    # 非 featured (grid) 記事の装飾が HTML 変換されている
    assert "SENT_G2WIKI" in built and "SENT_G2BOLD" in built and "SENT_G2UND" in built
    assert "SENT_G3WIKI" in built
    assert "emph-bold" in built          # [[ ]] マーカー
    assert "emph-und" in built           # __ __ 下線
    assert "<strong>SENT_G2BOLD</strong>" in built  # ** ** 太字


def test_no_raw_markers_anywhere(built):
    """生マーカー付き記事テキストが索引ページに残らない (回帰防止の核)。

    CSS は BEM (block__element) で `__` を使うため全文走査はできない。記事本文に
    仕込んだ sentinel が *生マーカー付き* で出ていないこと (= 変換漏れが無いこと) を見る。
    """
    for sent in ("SENT_FEATWIKI", "SENT_G2WIKI", "SENT_G3WIKI"):
        assert f"[[{sent}]]" not in built
    for sent in ("SENT_FEATBOLD", "SENT_G2BOLD", "SENT_G3BOLD"):
        assert f"**{sent}**" not in built
    for sent in ("SENT_FEATUND", "SENT_G2UND", "SENT_G3UND"):
        assert f"__{sent}__" not in built


def test_grid_has_multiple_nonfeatured_articles(built):
    """grid に featured 以外の記事が複数並ぶ (1 記事へ退化しない)。"""
    assert "SENT_G2TITLE" in built
    assert "SENT_G3TITLE" in built
    assert 'class="more-card"' in built


def test_articles_as_grid_entries_fills_bullets():
    """純関数: digests 注入で top_bullets を整形済みで埋め、summary_text を空にする。"""
    # _DIGEST を直書きした一時ファイルで検証 (実リポ非依存)
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "2026-06-03-Manufacturing.md"
        p.write_text(_DIGEST, encoding="utf-8")
        rows = _articles_as_grid_entries(
            "manufacturing", "2026-06-03",
            skip_url="https://example.com/feat", digests=[p],
        )
    assert len(rows) == 2  # featured を除く 2 記事
    for r in rows:
        assert r["top_bullets"], "bullets が空 (= raw summary fallback に退化)"
        assert r["summary_text"] == ""
        joined = " ".join(r["top_bullets"])
        assert "[[" not in joined and "**" not in joined and "__" not in joined
    # score 降順 (G2=80 が先頭)
    assert rows[0]["top_score"] == 80 and rows[1]["top_score"] == 70


def test_category_rest_day_notice_appears_on_unscheduled_today(tmp_path: Path):
    """本日休載カテゴリは、カテゴリトップのヒーローで休載を明示する。"""
    docs = tmp_path / "docs"
    d_man = tmp_path / "digest" / "Manufacturing"
    d_ai = tmp_path / "digest" / "Artificial Intelligence"
    d_man.mkdir(parents=True)
    d_ai.mkdir(parents=True)
    man = d_man / "2026-06-05-Manufacturing.md"
    ai = d_ai / "2026-06-07-Artificial-Intelligence.md"
    man.write_text(_DIGEST.replace("2026-06-03", "2026-06-05"), encoding="utf-8")
    ai.write_text(_SUNDAY_AI_DIGEST, encoding="utf-8")

    sources = [man, ai]
    entries = _collect_entries(sources)
    build_category_pages(entries, docs, digests=sources)

    manufacturing_html = (docs / "manufacturing" / "index.html").read_text(encoding="utf-8")
    ai_html = (docs / "ai" / "index.html").read_text(encoding="utf-8")
    assert "本日は休載です。" in manufacturing_html
    assert "このカテゴリは本日の配信対象外です" in manufacturing_html
    assert "SENT_FEAT" in manufacturing_html
    assert "本日は休載です。" not in ai_html
