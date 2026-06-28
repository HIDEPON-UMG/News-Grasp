#!/usr/bin/env python3
"""記事カード3行要約レーンUIの契約テスト。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.generate_pages import (  # noqa: E402
    _collect_entries,
    build_all,
    build_category_pages,
    build_index,
)


_DIGEST = """---
title: "News Grasp #20260628 — Foreign Exchange"
date: 2026-06-28
issue: 20260628
weekday: 日
categoryId: fx
accent: "#B8860B"
glyph: "¥"
---

# ¥ FX — Foreign Exchange

> [!summary]
> テスト用サマリ本文。

---

### [91] テストトップ記事

🇯🇵 検証用和訳タイトル

📅 2026-06-28 07:30 · 📰 Test Source · 🔗 [元記事](https://example.com/top)

#cat/fx #topic/test #score/高

![thumb](https://example.com/top.jpg)

- [[FACT_SENTINEL]] が**100億円**の投資を発表した。
- 背景には__制度変更__と市場参加者の入れ替わりがある。
- 次は採用企業の投資対効果が焦点になる。

---

### [82] テスト追加記事

📅 2026-06-28 08:30 · 📰 Test Source · 🔗 [元記事](https://example.com/more)

#cat/fx #topic/test #score/中

![thumb](https://example.com/more.jpg)

- 追加記事の事実行。
- 追加記事の背景行。
- 追加記事の展望行。
"""


def _build(tmp_path: Path) -> dict[str, str]:
    digest_dir = tmp_path / "digest" / "FX"
    digest_dir.mkdir(parents=True)
    digest = digest_dir / "2026-06-28-FX.md"
    digest.write_text(_DIGEST, encoding="utf-8")

    docs = tmp_path / "docs"
    sources = [digest]
    build_all(full=True, docs_root=docs, digests=sources)
    entries = _collect_entries(sources)
    build_index(entries, docs)
    build_category_pages(entries, docs, digests=sources)
    return {
        "page": (docs / "fx" / "2026-06-28" / "index.html").read_text(encoding="utf-8"),
        "home": (docs / "index.html").read_text(encoding="utf-8"),
        "category": (docs / "fx" / "index.html").read_text(encoding="utf-8"),
    }


def _assert_lane_roles(html: str) -> None:
    for role in ("fact", "context", "outlook"):
        assert f'data-role="{role}"' in html
    assert html.count("summary-lane__avatar") >= 3
    assert "summary-lanes__spine" in html
    assert "summary-lane__icon" in html


def test_page_cards_render_three_persona_lanes_and_keep_card_shell(tmp_path):
    html = _build(tmp_path)["page"]

    _assert_lane_roles(html)
    assert "top-story__thumb-wrap" in html
    assert "more-card__thumb-wrap" in html
    assert "top-story__bullets" not in html
    assert "more-card__bullets" not in html


def test_category_more_cards_keep_outlook_lane(tmp_path):
    html = _build(tmp_path)["category"]

    assert "more-card" in html
    assert 'data-role="outlook"' in html
    assert "追加記事の展望行" in html


def test_home_featured_story_uses_same_lane_component(tmp_path):
    html = _build(tmp_path)["home"]

    assert "home-featured" in html
    _assert_lane_roles(html)
