#!/usr/bin/env python3
"""tools.validate_daily_quality の契約テスト。"""
from __future__ import annotations

import json
from pathlib import Path

from tools.validate_daily_quality import main, validate_daily_quality


def _write_summary(root: Path, *, hero: bool = True, weekday: str | None = None) -> None:
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
    if weekday:
        frontmatter += f"weekday: {weekday}\n"
    frontmatter += "---\n\n# Summary\n"
    (summary_dir / "2026-06-08.md").write_text(frontmatter, encoding="utf-8")


def _write_category(
    root: Path,
    url: str,
    *,
    count: int = 5,
    quality_shortfall_reason: str | None = None,
) -> None:
    cat_dir = root / "digest" / "AI"
    cat_dir.mkdir(parents=True)
    articles = []
    for i in range(count):
        articles.append(
            f"### [{90 - i}] Freshness test article {i + 1}\n\n"
            f"📅 2026-06-08 06:0{i} · 📰 Example · 🔗 [元記事]({url})\n\n"
            "- test\n\n"
            "---\n"
        )
    frontmatter = (
        "---\n"
        "title: AI\n"
        "date: 2026-06-08\n"
        "categoryId: ai\n"
    )
    if quality_shortfall_reason:
        frontmatter += f"quality_shortfall_reason: {quality_shortfall_reason}\n"
    frontmatter += "---\n\n"
    (cat_dir / "2026-06-08-AI.md").write_text(
        frontmatter + "\n".join(articles),
        encoding="utf-8",
    )


def _write_category_digest(root: Path, cat_id: str, folder: str, *, count: int = 5) -> None:
    cat_dir = root / "digest" / folder
    cat_dir.mkdir(parents=True, exist_ok=True)
    articles = []
    for i in range(count):
        articles.append(
            f"### [{90 - i}] {cat_id} article {i + 1}\n\n"
            f"📅 2026-06-08 06:0{i} · 📰 Example · 🔗 [元記事](https://example.com/2026/06/08/{cat_id}-{i})\n\n"
            "- test\n\n"
            "---\n"
        )
    (cat_dir / f"2026-06-08-{folder}.md").write_text(
        "---\n"
        f"title: {folder}\n"
        "date: 2026-06-08\n"
        f"categoryId: {cat_id}\n"
        "---\n\n"
        + "\n".join(articles),
        encoding="utf-8",
    )


def _write_monday_scheduled_digests(root: Path) -> None:
    for cat_id, folder in [
        ("fx", "FX"),
        ("ai", "AI"),
        ("it", "IT-Consulting"),
        ("mobility", "Mobility"),
        ("manufacturing", "Manufacturing"),
        ("economy", "Economy"),
    ]:
        _write_category_digest(root, cat_id, folder)


def _write_jsonl(root: Path, url: str, *, extra: dict | None = None) -> None:
    data_dir = root / "data"
    data_dir.mkdir()
    record = {
        "date": "2026-06-08",
        "genre": "AI",
        "title": "Freshness test article",
        "url": url,
    }
    if extra:
        record.update(extra)
    (data_dir / "articles.jsonl").write_text(
        json.dumps(record, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _write_search_audit(
    root: Path,
    *,
    selected_total: int,
    candidates_total: int = 6,
    raw_results_total: int = 12,
    coverage_terms_checked: list[str] | None = None,
    dropped: list[dict] | None = None,
) -> None:
    audit_dir = root / "data" / "search_audit" / "2026-06-08"
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit = {
        "date": "2026-06-08",
        "category_id": "ai",
        "queries": [
            "AI news June 8 2026",
            "OpenAI Anthropic Google Apple Microsoft AI June 8 2026",
            "site:techcrunch.com AI June 8 2026",
        ],
        "raw_results_total": raw_results_total,
        "candidates_total": candidates_total,
        "selected_total": selected_total,
        "coverage_terms_checked": coverage_terms_checked or [
            "OpenAI",
            "Anthropic",
            "Google",
            "Apple",
            "Microsoft",
            "Meta",
            "NVIDIA",
        ],
        "dropped": dropped if dropped is not None else [
            {"title": "Low-newsworthiness candidate", "reason": "新材料が薄いため除外"}
        ],
    }
    (audit_dir / "ai.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2),
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


def test_daily_quality_rejects_weekday_mismatch(tmp_path: Path) -> None:
    """Summary の曜日が date と矛盾したら、配信対象カテゴリ以前に落とす。"""
    _write_summary(tmp_path, weekday="日曜日")
    _write_monday_scheduled_digests(tmp_path)
    _write_jsonl(tmp_path, "https://example.com/2026/06/08/fresh-news")

    errs = validate_daily_quality(
        issue_date="2026-06-08",
        digest_root=tmp_path / "digest",
        jsonl_path=tmp_path / "data" / "articles.jsonl",
    )

    joined = "\n".join(errs)
    assert "weekday=日曜日" in joined
    assert "月曜日" in joined


def test_daily_quality_rejects_scheduled_category_gap_and_extra(tmp_path: Path) -> None:
    """月曜に必須の製造・経済欠落と、月曜非対象の Game 混入を同時に落とす。"""
    _write_summary(tmp_path, weekday="月曜日")
    for cat_id, folder in [
        ("fx", "FX"),
        ("ai", "AI"),
        ("it", "IT-Consulting"),
        ("mobility", "Mobility"),
        ("game", "Game"),
    ]:
        _write_category_digest(tmp_path, cat_id, folder)
    _write_jsonl(tmp_path, "https://example.com/2026/06/08/fresh-news")

    errs = validate_daily_quality(
        issue_date="2026-06-08",
        digest_root=tmp_path / "digest",
        jsonl_path=tmp_path / "data" / "articles.jsonl",
    )

    joined = "\n".join(errs)
    assert "scheduled category digest missing" in joined
    assert "manufacturing" in joined
    assert "economy" in joined
    assert "unscheduled category digest present" in joined
    assert "game" in joined


def test_daily_quality_rejects_stale_url_date_in_digest_and_jsonl(tmp_path: Path) -> None:
    """URL パス日付が号日より古ければ、digest と jsonl の両方で落とす。"""
    _write_summary(tmp_path)
    stale_url = "https://example.com/2026/06/06/stale-news"
    _write_category(tmp_path, stale_url)
    _write_jsonl(tmp_path, stale_url)

    errs = validate_daily_quality(
        issue_date="2026-06-08",
        digest_root=tmp_path / "digest",
        jsonl_path=tmp_path / "data" / "articles.jsonl",
    )

    joined = "\n".join(errs)
    assert "source URL date 2026-06-06" in joined
    assert "digest" in joined
    assert "articles.jsonl" in joined


def test_daily_quality_rejects_stale_top_article_meta_date(tmp_path: Path) -> None:
    """URL 日付なしでも、カテゴリ TOP のメタ日付が古ければ落とす。"""
    _write_summary(tmp_path)
    fresh_url = "https://example.com/no-date/high-score-old-topic"
    _write_category(tmp_path, fresh_url)
    ai_md = tmp_path / "digest" / "AI" / "2026-06-08-AI.md"
    ai_md.write_text(
        ai_md.read_text(encoding="utf-8").replace(
            "📅 2026-06-08 06:00",
            "📅 2026-06-01 09:00",
            1,
        ),
        encoding="utf-8",
    )
    _write_jsonl(tmp_path, fresh_url)

    errs = validate_daily_quality(
        issue_date="2026-06-08",
        digest_root=tmp_path / "digest",
        jsonl_path=tmp_path / "data" / "articles.jsonl",
    )

    joined = "\n".join(errs)
    assert "top article date 2026-06-01" in joined
    assert "TOP STORY" in joined


def test_daily_quality_accepts_issue_day_previous_day_or_unknown_url_date(tmp_path: Path) -> None:
    """当日・前日 URL と日付が取れない URL は通す。"""
    _write_summary(tmp_path)
    _write_category(tmp_path, "https://example.com/no-date/fresh-topic")
    _write_jsonl(tmp_path, "https://example.com/2026/06/07/us-time-news")

    assert validate_daily_quality(
        issue_date="2026-06-08",
        digest_root=tmp_path / "digest",
        jsonl_path=tmp_path / "data" / "articles.jsonl",
    ) == []


def test_daily_quality_rejects_stale_matched_followup(tmp_path: Path) -> None:
    """URL 日付なしの記事でも、古い matched_with への未レビュー follow-up は落とす。"""
    _write_summary(tmp_path)
    _write_category(tmp_path, "https://example.com/no-date/followup-topic")
    _write_jsonl(
        tmp_path,
        "https://example.com/no-date/followup-topic",
        extra={
            "is_followup": True,
            "matched_with": "https://example.com/2026/05/20/original-topic",
        },
    )

    errs = validate_daily_quality(
        issue_date="2026-06-08",
        digest_root=tmp_path / "digest",
        jsonl_path=tmp_path / "data" / "articles.jsonl",
    )

    joined = "\n".join(errs)
    assert "follow-up matched_with URL date 2026-05-20" in joined
    assert "followup_review_note" in joined
    assert "articles.jsonl" in joined


def test_daily_quality_accepts_reviewed_stale_matched_followup(tmp_path: Path) -> None:
    """古い matched_with でも、新材料レビュー済みの続報は通す。"""
    _write_summary(tmp_path)
    _write_category(tmp_path, "https://example.com/no-date/followup-topic")
    _write_jsonl(
        tmp_path,
        "https://example.com/no-date/followup-topic",
        extra={
            "is_followup": True,
            "matched_with": "https://example.com/2026/05/20/original-topic",
            "followup_review_note": "地域が異なる新規展開であり旧記事の再掲ではない",
        },
    )

    assert validate_daily_quality(
        issue_date="2026-06-08",
        digest_root=tmp_path / "digest",
        jsonl_path=tmp_path / "data" / "articles.jsonl",
    ) == []


def test_daily_quality_rejects_shortfall_without_quality_reason(tmp_path: Path) -> None:
    """5件未満でも可だが、低品質記事を避けた理由が無い不足は落とす。"""
    _write_summary(tmp_path)
    _write_category(tmp_path, "https://example.com/2026/06/08/fresh-news", count=4)
    _write_jsonl(tmp_path, "https://example.com/2026/06/08/fresh-news")

    errs = validate_daily_quality(
        issue_date="2026-06-08",
        digest_root=tmp_path / "digest",
        jsonl_path=tmp_path / "data" / "articles.jsonl",
    )

    joined = "\n".join(errs)
    assert "has 4 article(s); target is 5" in joined
    assert "quality_shortfall_reason" in joined
    assert "2026-06-08-AI.md" in joined


def test_daily_quality_accepts_shortfall_with_quality_reason(tmp_path: Path) -> None:
    """ニュース性の低い記事を避けた明示理由と検索監査ログがあれば、5件未満でも通す。"""
    _write_summary(tmp_path)
    _write_category(
        tmp_path,
        "https://example.com/2026/06/08/fresh-news",
        count=3,
        quality_shortfall_reason="当日候補のうち新材料がある記事のみ採用",
    )
    _write_jsonl(tmp_path, "https://example.com/2026/06/08/fresh-news")
    _write_search_audit(tmp_path, selected_total=3)

    assert validate_daily_quality(
        issue_date="2026-06-08",
        digest_root=tmp_path / "digest",
        jsonl_path=tmp_path / "data" / "articles.jsonl",
        audit_root=tmp_path / "data" / "search_audit",
    ) == []


def test_daily_quality_rejects_shortfall_without_search_audit(tmp_path: Path) -> None:
    """5件未満のカテゴリは、品質不足理由だけでなく検索監査ログも必須。"""
    _write_summary(tmp_path)
    _write_category(
        tmp_path,
        "https://example.com/2026/06/08/fresh-news",
        count=3,
        quality_shortfall_reason="当日候補のうち新材料がある記事のみ採用",
    )
    _write_jsonl(tmp_path, "https://example.com/2026/06/08/fresh-news")

    errs = validate_daily_quality(
        issue_date="2026-06-08",
        digest_root=tmp_path / "digest",
        jsonl_path=tmp_path / "data" / "articles.jsonl",
        audit_root=tmp_path / "data" / "search_audit",
    )

    joined = "\n".join(errs)
    assert "search audit missing" in joined
    assert "data" in joined and "search_audit" in joined


def test_daily_quality_rejects_search_audit_missing_ai_coverage_terms(tmp_path: Path) -> None:
    """AI短縮号では主要AI企業を検索確認していない監査ログを落とす。"""
    _write_summary(tmp_path)
    _write_category(
        tmp_path,
        "https://example.com/2026/06/08/fresh-news",
        count=3,
        quality_shortfall_reason="当日候補のうち新材料がある記事のみ採用",
    )
    _write_jsonl(tmp_path, "https://example.com/2026/06/08/fresh-news")
    _write_search_audit(
        tmp_path,
        selected_total=3,
        coverage_terms_checked=["Google", "Apple"],
    )

    errs = validate_daily_quality(
        issue_date="2026-06-08",
        digest_root=tmp_path / "digest",
        jsonl_path=tmp_path / "data" / "articles.jsonl",
        audit_root=tmp_path / "data" / "search_audit",
    )

    joined = "\n".join(errs)
    assert "coverage_terms_checked missing required terms" in joined
    assert "OpenAI" in joined


def test_daily_quality_rejects_thin_search_audit(tmp_path: Path) -> None:
    """候補数や検索結果数が薄い監査ログは、収集漏れリスクとして落とす。"""
    _write_summary(tmp_path)
    _write_category(
        tmp_path,
        "https://example.com/2026/06/08/fresh-news",
        count=3,
        quality_shortfall_reason="当日候補のうち新材料がある記事のみ採用",
    )
    _write_jsonl(tmp_path, "https://example.com/2026/06/08/fresh-news")
    _write_search_audit(tmp_path, selected_total=3, candidates_total=3, raw_results_total=4)

    errs = validate_daily_quality(
        issue_date="2026-06-08",
        digest_root=tmp_path / "digest",
        jsonl_path=tmp_path / "data" / "articles.jsonl",
        audit_root=tmp_path / "data" / "search_audit",
    )

    joined = "\n".join(errs)
    assert "raw_results_total=4" in joined
    assert "candidates_total=3" in joined


def test_daily_quality_cli_returns_nonzero_for_stale_url(tmp_path: Path, capsys) -> None:
    """runner から呼ぶ CLI は stale URL を stderr ERROR と exit 1 で返す。"""
    _write_summary(tmp_path)
    stale_url = "https://example.com/2026/06/06/stale-news"
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
    assert "2026-06-06" in captured.err
