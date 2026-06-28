#!/usr/bin/env python3
"""記事カード3行要約レーンUIの契約テスト。"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CSS_PATH = ROOT / "docs" / "assets" / "site.css"

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


def _assert_layer_lanes(html: str) -> None:
    for role in ("fact", "context", "outlook"):
        assert f'data-role="{role}"' in html
    assert "summary-lanes__spine" in html
    assert "summary-lane__avatar-col" in html
    assert "summary-lane__avatar" in html
    assert "summary-lane__icon" in html
    assert "summary-lane__short" in html
    assert "summary-lane__marker" in html
    assert "summary-lane__label" not in html
    for short in ("FACT", "CONTEXT", "OUTLOOK"):
        assert short in html
    for marker in ("【事実・概要】", "【背景・要点】", "【影響・展望】"):
        assert marker in html
    for stale_marker in ("【事実】", "【背景】", "【展望】"):
        assert stale_marker not in html
    for persona in ("記者", "解説者", "予測者"):
        assert persona not in html


def test_page_cards_render_three_layer_lanes_and_keep_card_shell(tmp_path):
    html = _build(tmp_path)["page"]

    _assert_layer_lanes(html)
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
    _assert_layer_lanes(html)


def test_summary_lane_avatar_colors_have_defined_visible_tokens():
    css = CSS_PATH.read_text(encoding="utf-8")

    assert "--summary-accent-mid:" in css
    assert "var(--summary-accent-mid)" in css
    assert "--summary-accent-mid: color-mix(in srgb, var(--summary-accent) 55%, var(--color-surface));" in css
    assert 'body[data-category="fx"] .summary-lanes' in css
    for fx_stop in ("#8A6408", "#B8860B", "#CFAD59", "#E1CC99"):
        assert fx_stop in css
    defined_summary_tokens = set(re.findall(r"(--summary-[a-z-]+)\s*:", css))
    used_summary_tokens = set(re.findall(r"var\((--summary-[a-z-]+)\)", css))
    assert used_summary_tokens <= defined_summary_tokens
    assert ".summary-lane--context .summary-lane__avatar" in css
    assert ".summary-lane--outlook .summary-lane__avatar" in css
    context_start = css.index(".summary-lane--context .summary-lane__avatar")
    context_block = css[context_start:css.index("}", context_start)]
    assert "linear-gradient(135deg, var(--summary-accent), var(--summary-accent-mid))" in context_block
    for role in ("fact", "context", "outlook"):
        selector = f".summary-lane--{role} .summary-lane__avatar"
        start = css.index(selector)
        block = css[start:css.index("}", start)]
        assert "background:" in block
        assert "transparent" not in block
