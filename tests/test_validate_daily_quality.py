#!/usr/bin/env python3
"""tools.validate_daily_quality の契約テスト。"""
from __future__ import annotations

import json
from pathlib import Path

from tools.validate_daily_quality import main, validate_daily_quality


def _write_summary(root: Path, *, hero: bool = True) -> None:
    summary_dir = root / "digest" / "Summary"
    summary_dir.mkdir(parents=True)
    frontmatter = (
        "---\n"
        "title: Summary\n"
        "date: 2026-06-08\n"
        "category: Daily Summary\n"
    )
    if hero:
        frontmatter += "hero_left: プラットフォーム再編\nhero_right: 市場へ波及\n"
    frontmatter += "---\n\n# Summary\n"
    (summary_dir / "2026-06-08.md").write_text(frontmatter, encoding="utf-8")


def _write_category(root: Path, url: str) -> None:
    cat_dir = root / "digest" / "AI"
    cat_dir.mkdir(parents=True)
    (cat_dir / "2026-06-08-AI.md").write_text(
        "---\n"
        "title: AI\n"
        "date: 2026-06-08\n"
        "categoryId: ai\n"
        "---\n\n"
        "### [90] Freshness test article\n\n"
        f"📅 2026-06-08 06:00 · 📰 Example · 🔗 [元記事]({url})\n\n"
        "- test\n\n"
        "---\n",
        encoding="utf-8",
    )


def _write_jsonl(root: Path, url: str) -> None:
    data_dir = root / "data"
    data_dir.mkdir()
    record = {
        "date": "2026-06-08",
        "genre": "AI",
        "title": "Freshness test article",
        "url": url,
    }
    (data_dir / "articles.jsonl").write_text(
        json.dumps(record, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def test_daily_quality_rejects_missing_summary_hero(tmp_path: Path) -> None:
    """hero_left / hero_right が無い Summary は LP fallback 防止のため落とす。"""
    _write_summary(tmp_path, hero=False)
    url = "https://example.com/2026/06/08/fresh-news"
    _write_category(tmp_path, url)
    _write_jsonl(tmp_path, url)

    errs = validate_daily_quality(
        issue_date="2026-06-08",
        digest_root=tmp_path / "digest",
        jsonl_path=tmp_path / "data" / "articles.jsonl",
    )

    assert any("hero_left / hero_right" in e for e in errs)


def test_daily_quality_rejects_stale_url_date_in_digest_and_jsonl(tmp_path: Path) -> None:
    """URL パス日付が号日より古ければ、digest と jsonl の両方で落とす。"""
    _write_summary(tmp_path)
    stale_url = "https://example.com/2026/06/07/stale-news"
    _write_category(tmp_path, stale_url)
    _write_jsonl(tmp_path, stale_url)

    errs = validate_daily_quality(
        issue_date="2026-06-08",
        digest_root=tmp_path / "digest",
        jsonl_path=tmp_path / "data" / "articles.jsonl",
    )

    joined = "\n".join(errs)
    assert "source URL date 2026-06-07" in joined
    assert "digest" in joined
    assert "articles.jsonl" in joined


def test_daily_quality_accepts_issue_day_or_unknown_url_date(tmp_path: Path) -> None:
    """当日 URL と日付が取れない URL は偽陽性防止のため通す。"""
    _write_summary(tmp_path)
    _write_category(tmp_path, "https://example.com/no-date/fresh-topic")
    _write_jsonl(tmp_path, "https://example.com/2026/06/08/fresh-news")

    assert validate_daily_quality(
        issue_date="2026-06-08",
        digest_root=tmp_path / "digest",
        jsonl_path=tmp_path / "data" / "articles.jsonl",
    ) == []


def test_daily_quality_cli_returns_nonzero_for_stale_url(tmp_path: Path, capsys) -> None:
    """runner から呼ぶ CLI は stale URL を stderr ERROR と exit 1 で返す。"""
    _write_summary(tmp_path)
    stale_url = "https://example.com/2026/06/07/stale-news"
    _write_category(tmp_path, stale_url)
    _write_jsonl(tmp_path, stale_url)

    rc = main([
        "--date", "2026-06-08",
        "--digest-root", str(tmp_path / "digest"),
        "--jsonl", str(tmp_path / "data" / "articles.jsonl"),
    ])

    captured = capsys.readouterr()
    assert rc == 1
    assert "ERROR:" in captured.err
    assert "2026-06-07" in captured.err
