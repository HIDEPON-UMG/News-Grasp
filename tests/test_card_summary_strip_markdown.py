#!/usr/bin/env python3
"""home カード / overview 行のカテゴリ summary が生 markdown を出さない契約テスト。

背景 (2026-06-04 調査):
    build_index (home の lens カード) と build_overview (daily overview の行) は、
    カテゴリ digest の ``> [!summary]`` callout 本文 (= summary_text) を card の
    ``summary`` に渡す。テンプレ側 (index-template:269 / overview-template:127) は
    これを ``{{ ... | truncate }}`` で出すだけで **render_emph を通さない**。よって
    callout に ``**bold**`` や ``[[wikilink]]`` が混じると home/overview に生マーカーが
    そのまま表示される (カテゴリ索引 grid で起きた生 markdown 事故と同じ class)。
    現状 callout は慣習上プレーンなので不発だが構造的に封じられていなかったため、
    build_index / build_overview 側で strip_inline してから card に渡すよう恒久封鎖した。

検証する意図 (= この class of bugs を 1 テストで封じる):
    - callout に装飾を仕込んでも home カード / overview 行の summary が素テキスト化され、
      生マーカー (**…** / [[…]] / __…__) が 1 つも残らない
    - 素テキスト (sentinel) 自体はちゃんと残る (= summary を空にして握りつぶしていない)

実行:
    pytest tests/test_card_summary_strip_markdown.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.generate_pages import (  # noqa: E402
    build_all,
    build_index,
    build_overview,
    _collect_entries,
)

# callout に **bold** / [[wikilink]] / __underline__ を仕込んだカテゴリ digest。
_DIGEST = """---
title: "News Grasp #20260520 — {label}"
date: 2026-05-20
issue: 20260520
weekday: 火
categoryId: {cat_id}
accent: "#5A6B7B"
glyph: "⬢"
---

# {LABEL}

> [!summary]
> 要約に**SENT_BOLD**と[[SENT_WIKI]]と__SENT_UND__を含む素テキスト化検証用 callout。

---

### [88] {label} テスト記事

📅 2026-05-20 07:30 · 📰 Test Source · 🔗 [元記事](https://example.com/{cat_id})

#cat/{cat_id} #topic/test #score/高

- [[記事内]] の**強調**は__保持__される (bullets は safe 経路)
- 2 本目
- 3 本目
"""

_RAW_FORMS = ("**SENT_BOLD**", "[[SENT_WIKI]]", "__SENT_UND__")
_STRIPPED = ("SENT_BOLD", "SENT_WIKI", "SENT_UND")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    root = tmp_path_factory.mktemp("card_summary_strip")
    docs = root / "docs"
    sources: list[Path] = []
    # manufacturing は callout に装飾あり / fx はプレーン (overview に複数行出すため)。
    for cat_id, label in [("manufacturing", "Manufacturing"), ("fx", "FX")]:
        d = root / "digest" / label.upper()
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"2026-05-20-{label}.md"
        p.write_text(_DIGEST.format(label=label, LABEL=label.upper(), cat_id=cat_id),
                     encoding="utf-8")
        sources.append(p)
    build_all(full=True, docs_root=docs, digests=sources)
    entries = _collect_entries(sources)
    build_index(entries, docs)
    build_overview("2026-05-20", entries, docs)
    return {
        "home": (docs / "index.html").read_text(encoding="utf-8"),
        "overview": (docs / "2026-05-20" / "index.html").read_text(encoding="utf-8"),
    }


def test_home_card_summary_has_no_raw_markers(built):
    """home の lens カード summary に生マーカーが残らず、素テキストは残る。"""
    home = built["home"]
    for raw in _RAW_FORMS:
        assert raw not in home, f"home カードに生マーカー {raw} が残存"
    # 素テキスト化された sentinel は残っている (summary を空にしていない)
    assert "SENT_BOLD" in home and "SENT_WIKI" in home


def test_overview_row_summary_has_no_raw_markers(built):
    """overview 行 summary に生マーカーが残らず、素テキストは残る。"""
    ovr = built["overview"]
    for raw in _RAW_FORMS:
        assert raw not in ovr, f"overview 行に生マーカー {raw} が残存"
    assert "SENT_BOLD" in ovr and "SENT_WIKI" in ovr
