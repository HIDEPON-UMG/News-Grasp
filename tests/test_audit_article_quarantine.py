#!/usr/bin/env python3
"""URL gate の per-article drop 契約テスト。"""
from __future__ import annotations

import json
from pathlib import Path

from tools.audit_all_article_urls import drop_article_urls


def _write_jsonl(path: Path) -> None:
    rows = [
        {"date": "2026-06-13", "title": "keep", "url": "https://example.com/keep", "thumb": None, "title_ja": "keep"},
        {"date": "2026-06-13", "title": "drop", "url": "https://example.com/drop", "thumb": None, "title_ja": "drop"},
    ]
    path.parent.mkdir(parents=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def _write_digest(path: Path) -> None:
    path.parent.mkdir(parents=True)
    path.write_text(
        "---\ntitle: AI\ndate: 2026-06-13\ncategoryId: ai\n---\n\n"
        "### [90] keep\n\n"
        "📅 2026-06-13 06:00 · 📰 Example · 🔗 [元記事](https://example.com/keep)\n\n"
        "- [[keep]] **keep** __keep__\n\n"
        "---\n"
        "### [80] drop\n\n"
        "📅 2026-06-13 06:01 · 📰 Example · 🔗 [元記事](https://example.com/drop)\n\n"
        "- [[drop]] **drop** __drop__\n\n"
        "---\n",
        encoding="utf-8",
    )


def test_drop_article_urls_removes_only_bad_url(tmp_path: Path) -> None:
    jsonl = tmp_path / "data" / "articles.jsonl"
    digest = tmp_path / "digest" / "AI" / "2026-06-13-AI.md"
    _write_jsonl(jsonl)
    _write_digest(digest)

    result = drop_article_urls(
        repo_root=tmp_path,
        urls={"https://example.com/drop"},
        issue_date="2026-06-13",
        apply=True,
    )

    assert result.jsonl_dropped == 1
    assert result.digest_cards_dropped == 1
    assert "https://example.com/keep" in jsonl.read_text(encoding="utf-8")
    assert "https://example.com/drop" not in jsonl.read_text(encoding="utf-8")
    digest_text = digest.read_text(encoding="utf-8")
    assert "https://example.com/keep" in digest_text
    assert "https://example.com/drop" not in digest_text


def test_drop_article_urls_syncs_search_audit_selected_total(tmp_path: Path) -> None:
    """quarantine 後に search_audit の selected_total も生存カード数へ同期する。"""
    jsonl = tmp_path / "data" / "articles.jsonl"
    digest = tmp_path / "digest" / "AI" / "2026-06-13-AI.md"
    audit = tmp_path / "data" / "search_audit" / "2026-06-13" / "ai.json"
    _write_jsonl(jsonl)
    _write_digest(digest)
    audit.parent.mkdir(parents=True)
    audit.write_text(
        json.dumps(
            {
                "date": "2026-06-13",
                "category_id": "ai",
                "queries": ["q1", "q2", "q3"],
                "raw_results_total": 20,
                "candidates_total": 2,
                "selected_total": 2,
                "coverage_terms_checked": ["OpenAI", "Anthropic", "Google", "Apple"],
                "dropped": [{"title": "x", "url": "https://example.com/x", "reason": "fixture"}],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    result = drop_article_urls(
        repo_root=tmp_path,
        urls={"https://example.com/drop"},
        issue_date="2026-06-13",
        apply=True,
    )

    assert result.search_audit_updated == 1
    updated = json.loads(audit.read_text(encoding="utf-8"))
    assert updated["selected_total"] == 1
