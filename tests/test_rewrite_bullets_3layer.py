#!/usr/bin/env python3
"""カテゴリ digest の3層要約リライト契約テスト。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.rewrite_bullets_3layer import (  # noqa: E402
    ROLE_PREFIXES,
    collect_digest_targets,
    rewrite_markdown_text,
    validate_rewrite,
)


_ARTICLE = """---
title: "News Grasp #20260628 — Artificial Intelligence"
date: 2026-06-28
issue: 20260628
categoryId: ai
---

# AI

---

### [92] Example AI investment

📅 2026-06-28 09:00 · 📰 Example · 🔗 [元記事](https://example.com/news?id=42)

#cat/ai #co/Example #score/高

- [[Example AI]] が**100億円**の追加投資を発表した。
- 背景にはクラウド需要と__GPU調達競争__がある。
- 2026年後半は企業導入のROIが焦点になる。
"""


def test_rewrite_adds_fact_context_outlook_prefixes_without_losing_markers_or_url():
    rewritten, report = rewrite_markdown_text(_ARTICLE)

    assert report.changed_articles == 1
    for prefix in ROLE_PREFIXES:
        assert f"- {prefix}" in rewritten
    for stale_prefix in ("【事実】：", "【背景】：", "【展望】："):
        assert stale_prefix not in rewritten
    assert rewritten.count("- ") == 3
    assert "https://example.com/news?id=42" in rewritten
    assert "[[Example AI]]" in rewritten
    assert "**100億円**" in rewritten
    assert "__GPU調達競争__" in rewritten
    validate_rewrite(_ARTICLE, rewritten)


def test_rewrite_is_idempotent_when_prefixes_exist():
    once, _ = rewrite_markdown_text(_ARTICLE)
    twice, report = rewrite_markdown_text(once)

    assert twice == once
    assert report.changed_articles == 0


def test_rewrite_accepts_legacy_prefixes_but_outputs_current_prefixes():
    legacy = _ARTICLE.replace(
        "- [[Example AI]] が**100億円**の追加投資を発表した。\n"
        "- 背景にはクラウド需要と__GPU調達競争__がある。\n"
        "- 2026年後半は企業導入のROIが焦点になる。",
        "- 【事実】：[[Example AI]] が**100億円**の追加投資を発表した。\n"
        "- 【背景】：背景にはクラウド需要と__GPU調達競争__がある。\n"
        "- 【展望】：2026年後半は企業導入のROIが焦点になる。",
    )

    rewritten, report = rewrite_markdown_text(legacy)

    assert report.changed_articles == 1
    for prefix in ROLE_PREFIXES:
        assert f"- {prefix}" in rewritten
    for stale_prefix in ("【事実】：", "【背景】：", "【展望】："):
        assert stale_prefix not in rewritten
    validate_rewrite(legacy, rewritten)


def test_collect_targets_excludes_summary_directory(tmp_path):
    cat_dir = tmp_path / "digest" / "AI"
    summary_dir = tmp_path / "digest" / "Summary"
    deepdive_dir = tmp_path / "digest" / "DeepDive"
    cat_dir.mkdir(parents=True)
    summary_dir.mkdir(parents=True)
    deepdive_dir.mkdir(parents=True)
    cat_file = cat_dir / "2026-06-28-AI.md"
    summary_file = summary_dir / "2026-06-28-Summary.md"
    deepdive_file = deepdive_dir / "2026-06-28-DeepDive.md"
    cat_file.write_text(_ARTICLE, encoding="utf-8")
    summary_file.write_text(_ARTICLE.replace("categoryId: ai", "categoryId: summary"), encoding="utf-8")
    deepdive_file.write_text(_ARTICLE.replace("categoryId: ai", "categoryId: deepdive"), encoding="utf-8")

    targets = collect_digest_targets(tmp_path / "digest")
    assert targets == [cat_file]


def test_rewrite_normalizes_sparse_or_extra_bullets_to_three_roles():
    sparse = _ARTICLE.replace(
        "- [[Example AI]] が**100億円**の追加投資を発表した。\n"
        "- 背景にはクラウド需要と__GPU調達競争__がある。\n"
        "- 2026年後半は企業導入のROIが焦点になる。",
        "- [[Example AI]] が**100億円**の追加投資を発表した。",
    )
    rewritten, report = rewrite_markdown_text(sparse)

    assert report.total_articles == 1
    assert report.changed_articles == 1
    assert rewritten.count("- ") == 3
    for prefix in ROLE_PREFIXES:
        assert f"- {prefix}" in rewritten
    validate_rewrite(sparse, rewritten)
