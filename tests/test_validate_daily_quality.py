#!/usr/bin/env python3
"""tools.validate_daily_quality の契約テスト。"""
from __future__ import annotations

import json
from pathlib import Path

from datetime import date

from tools.validate_daily_quality import (
    main,
    validate_daily_quality,
    validate_card_emphasis_coverage,
    validate_digest_article_counts,
    validate_digest_article_thumbnail_coverage,
    validate_digest_style_quality,
    validate_dedup_annotation_present,
    validate_deepdive_presence,
    validate_deepdive_relations_layout,
    validate_issue_schedule,
)


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


def _write_summary_with_reflection(root: Path, *, lead: str, section_body: str) -> None:
    summary_dir = root / "digest" / "Summary"
    summary_dir.mkdir(parents=True, exist_ok=True)
    (summary_dir / "2026-06-08.md").write_text(
        "---\n"
        "title: Summary\n"
        "date: 2026-06-08\n"
        "category: Daily Summary\n"
        "hero_left: プラットフォーム再編\n"
        "hero_right: 市場へ波及\n"
        "---\n\n"
        "# Summary\n\n"
        "## § 本日のテーマ考察\n\n"
        "*政策と市場の接点*\n\n"
        f"> {lead}\n\n"
        "### §01 総論 — 実装力を見る日\n\n"
        f"{section_body}\n",
        encoding="utf-8",
    )


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
            f"![thumb](https://example.com/thumb-{i}.jpg)\n\n"
            "- [[test]] **test** __test__\n\n"
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
            f"![thumb](https://example.com/thumb-{cat_id}-{i}.jpg)\n\n"
            "- [[test]] **test** __test__\n\n"
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


def _write_category_digest_for_issue(
    root: Path,
    *,
    issue: str,
    cat_id: str,
    folder: str,
) -> None:
    cat_dir = root / "digest" / folder
    cat_dir.mkdir(parents=True, exist_ok=True)
    (cat_dir / f"{issue}-{folder}.md").write_text(
        "---\n"
        f"title: {folder}\n"
        f"date: {issue}\n"
        f"categoryId: {cat_id}\n"
        "---\n\n"
        f"### [90] {cat_id} article\n\n"
        f"📅 {issue} 06:00 · 📰 Example · 🔗 [元記事](https://example.com/{issue}/{cat_id})\n\n"
        "![thumb](https://example.com/thumb.jpg)\n\n"
        "- [[test]] **test** __test__\n\n"
        "---\n",
        encoding="utf-8",
    )


def _write_wednesday_summary(
    root: Path,
    *,
    categories: list[str],
    body: str = "",
) -> None:
    summary_dir = root / "digest" / "Summary"
    summary_dir.mkdir(parents=True, exist_ok=True)
    category_lines = "\n".join(f"  - {cat_id}" for cat_id in categories)
    tag_lines = "\n".join(f"  - cat/{cat_id}" for cat_id in categories)
    (summary_dir / "2026-06-24.md").write_text(
        "---\n"
        "title: Summary\n"
        "date: 2026-06-24\n"
        "weekday: 水曜日\n"
        "category: Daily Summary\n"
        "categoryId: summary\n"
        "categories:\n"
        f"{category_lines}\n"
        "tags:\n"
        f"{tag_lines}\n"
        "---\n\n"
        "# Summary\n\n"
        f"{body}\n",
        encoding="utf-8",
    )


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


def test_daily_quality_rejects_all_null_thumbnails_for_issue(tmp_path: Path) -> None:
    """当日号の全記事が thumb=null なら、全カード fallback 表示になるため公開前に落とす。"""
    _write_summary(tmp_path)
    url = "https://example.com/2026/06/08/fresh-news"
    _write_category(tmp_path, url)
    _write_jsonl(tmp_path, url, extra={"title_ja": "Freshness test article", "thumb": None})

    errs = validate_daily_quality(
        issue_date="2026-06-08",
        digest_root=tmp_path / "digest",
        jsonl_path=tmp_path / "data" / "articles.jsonl",
    )

    joined = "\n".join(errs)
    assert "thumb が全件 null" in joined
    assert "公開ページが全件 fallback サムネになります" in joined


def test_digest_article_thumbnail_coverage_rejects_empty_card_thumb(tmp_path: Path) -> None:
    """digest カードの thumb が空なら、記事ページ生成前に公開 gate で落とす。"""
    cat_dir = tmp_path / "digest" / "AI"
    cat_dir.mkdir(parents=True)
    (cat_dir / "2026-06-08-AI.md").write_text(
        "---\n"
        "title: AI\n"
        "date: 2026-06-08\n"
        "categoryId: ai\n"
        "---\n\n"
        "### [90] Missing thumbnail article\n\n"
        "📅 2026-06-08 06:00 · 📰 Example · 🔗 [元記事](https://example.com/2026/06/08/fresh-news)\n\n"
        "![thumb](null)\n\n"
        "- [[test]] **test** __test__\n\n"
        "---\n",
        encoding="utf-8",
    )

    errs = validate_digest_article_thumbnail_coverage(tmp_path / "digest", date(2026, 6, 8))

    joined = "\n".join(errs)
    assert "thumb が空です" in joined
    assert "カテゴリ fallback サムネになります" in joined


def test_daily_quality_rejects_google_news_rss_urls_for_issue(tmp_path: Path) -> None:
    """当日 record の url が Google News RSS のままなら、元記事 OGP 取得漏れとして落とす。"""
    _write_summary(tmp_path)
    url = "https://news.google.com/rss/articles/CBMiExample?oc=5"
    _write_category(tmp_path, url)
    _write_jsonl(
        tmp_path,
        url,
        extra={
            "title_ja": "Freshness test article",
            "thumb": "https://example.com/thumb.jpg",
        },
    )

    errs = validate_daily_quality(
        issue_date="2026-06-08",
        digest_root=tmp_path / "digest",
        jsonl_path=tmp_path / "data" / "articles.jsonl",
    )

    joined = "\n".join(errs)
    assert "Google News RSS URL のままです" in joined
    assert "元記事 URL へ解決してから公開してください" in joined


def test_daily_quality_rejects_google_news_proxy_thumbnails_for_issue(tmp_path: Path) -> None:
    """Google News 代理画像の lh3.googleusercontent.com サムネは公開前に落とす。"""
    _write_summary(tmp_path)
    url = "https://www.axios.com/2026/06/17/fed-warsh-interest-rates"
    _write_category(tmp_path, url)
    _write_jsonl(
        tmp_path,
        url,
        extra={
            "title_ja": "Freshness test article",
            "thumb": "https://lh3.googleusercontent.com/J6_proxy=s0-w300-rw",
        },
    )

    errs = validate_daily_quality(
        issue_date="2026-06-08",
        digest_root=tmp_path / "digest",
        jsonl_path=tmp_path / "data" / "articles.jsonl",
    )

    joined = "\n".join(errs)
    assert "Google News 代理サムネ" in joined
    assert "元記事 OGP 画像またはカテゴリ既定画像へ差し替えてください" in joined


def test_daily_quality_rejects_homepage_rounded_urls_for_issue(tmp_path: Path) -> None:
    """Google News 解決失敗で媒体トップ URL に丸まった record は公開前に落とす。"""
    _write_summary(tmp_path)
    url = "https://www.nikkei.com/"
    _write_category(tmp_path, url)
    _write_jsonl(
        tmp_path,
        url,
        extra={
            "title_ja": "Freshness test article",
            "thumb": "https://example.com/thumb.jpg",
        },
    )

    errs = validate_daily_quality(
        issue_date="2026-06-08",
        digest_root=tmp_path / "digest",
        jsonl_path=tmp_path / "data" / "articles.jsonl",
    )

    joined = "\n".join(errs)
    assert "媒体トップまたはカテゴリトップに丸まった URL" in joined
    assert "元記事単位の URL へ解決してから公開してください" in joined


def test_daily_quality_rejects_summary_reflection_without_three_tier_emphasis(tmp_path: Path) -> None:
    """Summary 考察があるのに太字・下線・マーカーが不足していれば落とす。"""
    _write_summary_with_reflection(
        tmp_path,
        lead="政策イベントと企業実装が同じ日に並んだ。",
        section_body="市場は発表ではなく実装能力を見ている。",
    )
    _write_category(tmp_path, "https://example.com/2026/06/08/fresh-news")
    _write_jsonl(tmp_path, "https://example.com/2026/06/08/fresh-news")

    errs = validate_daily_quality(
        issue_date="2026-06-08",
        digest_root=tmp_path / "digest",
        jsonl_path=tmp_path / "data" / "articles.jsonl",
    )

    joined = "\n".join(errs)
    assert "reflection lead lacks required emphasis" in joined
    assert "reflection section §01 lacks required emphasis" in joined
    assert "[[ ]] marker" in joined
    assert "** ** bold" in joined
    assert "__ __ underline" in joined


def test_daily_quality_accepts_summary_reflection_with_three_tier_emphasis(tmp_path: Path) -> None:
    """Summary 考察 lead / § 本文が 3 階層強調を含めば通す。"""
    _write_summary_with_reflection(
        tmp_path,
        lead="[[政策イベント]] と **企業実装** が同じ日に並び、__運用力の差__ が見えた。",
        section_body="[[AI導入]] は **発表数** ではなく、__継続運用できる体制__ で評価される。",
    )
    _write_category(tmp_path, "https://example.com/2026/06/08/fresh-news")
    _write_jsonl(tmp_path, "https://example.com/2026/06/08/fresh-news")

    assert validate_daily_quality(
        issue_date="2026-06-08",
        digest_root=tmp_path / "digest",
        jsonl_path=tmp_path / "data" / "articles.jsonl",
    ) == []


def test_card_emphasis_coverage_rejects_plain_bullets(tmp_path: Path) -> None:
    """カテゴリカード本文の 3 階層強調漏れを編集長補完対象として検出する。"""
    cat_dir = tmp_path / "digest" / "AI"
    cat_dir.mkdir(parents=True)
    (cat_dir / "2026-06-08-AI.md").write_text(
        "---\n"
        "title: AI\n"
        "date: 2026-06-08\n"
        "categoryId: ai\n"
        "---\n\n"
        "### [90] Freshness test article\n\n"
        "📅 2026-06-08 06:00 · 📰 Example · 🔗 [元記事](https://example.com/2026/06/08/fresh-news)\n\n"
        "- plain bullet\n\n"
        "---\n",
        encoding="utf-8",
    )

    errs = validate_card_emphasis_coverage(tmp_path / "digest", date(2026, 6, 8))

    joined = "\n".join(errs)
    assert "card #01 lacks required emphasis" in joined
    assert "[[ ]] marker" in joined
    assert "** ** bold" in joined
    assert "__ __ underline" in joined


def test_card_emphasis_coverage_accepts_three_tier_bullets(tmp_path: Path) -> None:
    """カード本文が 3 階層強調を含めば追加エラーを出さない。"""
    cat_dir = tmp_path / "digest" / "AI"
    cat_dir.mkdir(parents=True)
    (cat_dir / "2026-06-08-AI.md").write_text(
        "---\n"
        "title: AI\n"
        "date: 2026-06-08\n"
        "categoryId: ai\n"
        "---\n\n"
        "### [90] Freshness test article\n\n"
        "📅 2026-06-08 06:00 · 📰 Example · 🔗 [元記事](https://example.com/2026/06/08/fresh-news)\n\n"
        "- [[政策イベント]] と **企業実装** が並び、__運用力の差__ が見えた。\n\n"
        "---\n",
        encoding="utf-8",
    )

    assert validate_card_emphasis_coverage(tmp_path / "digest", date(2026, 6, 8)) == []


def test_digest_style_quality_rejects_translationese_and_repetition(tmp_path: Path) -> None:
    """翻訳調・文末反復・冗長接続句・未翻訳 title_ja を記事単位で検出する。"""
    cat_dir = tmp_path / "digest" / "AI"
    cat_dir.mkdir(parents=True)
    (cat_dir / "2026-06-08-AI.md").write_text(
        "---\n"
        "title: AI\n"
        "date: 2026-06-08\n"
        "categoryId: ai\n"
        "---\n\n"
        "### [90] OpenAI Microsoft\n\n"
        "- [[企業]] は **新製品** を発表した。\n"
        "- 一方で、また、さらに、加えて、投資家は様子見となった。\n"
        "- [[市場]] が **同じ文末** となった。\n"
        "- [[政策]] も **同じ文末** となった。\n"
        "- [[需給]] も **同じ文末** となった。\n"
        "---\n",
        encoding="utf-8",
    )

    joined = "\n".join(validate_digest_style_quality(tmp_path / "digest", date(2026, 6, 8)))

    assert "title_ja appears untranslated" in joined
    assert "repetitive sentence endings" in joined
    assert "redundant connectors" in joined


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


def test_daily_quality_rejects_scheduled_category_gap(tmp_path: Path) -> None:
    """配信対象カテゴリの digest 欠落は公開必須 inventory 欠落として落とす。"""
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
    assert "unscheduled category digest present" not in joined


def test_issue_schedule_rejects_unscheduled_summary_category_on_wednesday(tmp_path: Path) -> None:
    """水曜 Summary が Game を参照したら、記者 fan-out が正しくても公開前に落とす。"""
    for cat_id, folder in [
        ("fx", "FX"),
        ("ai", "AI"),
        ("it", "IT-Consulting"),
        ("mobility", "Mobility"),
        ("manufacturing", "Manufacturing"),
        ("economy", "Economy"),
    ]:
        _write_category_digest_for_issue(
            tmp_path,
            issue="2026-06-24",
            cat_id=cat_id,
            folder=folder,
        )
    _write_wednesday_summary(
        tmp_path,
        categories=["fx", "ai", "it", "mobility", "manufacturing", "economy", "game"],
        body="### §08 ゲーム — 発売より、その後の回し方\n\nGame は料金改定が主役でした。\n",
    )

    joined = "\n".join(validate_issue_schedule(tmp_path / "digest", date(2026, 6, 24)))

    assert "unscheduled summary category" in joined
    assert "game" in joined
    assert "2026-06-24" in joined


def test_issue_schedule_allows_stale_unscheduled_digest_when_summary_excludes_it(tmp_path: Path) -> None:
    """非対象カテゴリ artifact が残っていても、Summary が参照しなければ missing/failure にしない。"""
    for cat_id, folder in [
        ("fx", "FX"),
        ("ai", "AI"),
        ("it", "IT-Consulting"),
        ("mobility", "Mobility"),
        ("manufacturing", "Manufacturing"),
        ("economy", "Economy"),
        ("game", "Game"),
    ]:
        _write_category_digest_for_issue(
            tmp_path,
            issue="2026-06-24",
            cat_id=cat_id,
            folder=folder,
        )
    _write_wednesday_summary(
        tmp_path,
        categories=["fx", "ai", "it", "mobility", "manufacturing", "economy"],
        body="### §06 製造 — 量産の入口\n\nManufacturing を扱います。\n",
    )

    assert validate_issue_schedule(tmp_path / "digest", date(2026, 6, 24)) == []


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


def test_daily_quality_allows_shortfall_without_quality_reason(tmp_path: Path) -> None:
    """5件未満カテゴリだけでは号全体を fallback させない。"""
    _write_summary(tmp_path)
    _write_category(tmp_path, "https://example.com/2026/06/08/fresh-news", count=4)
    _write_jsonl(tmp_path, "https://example.com/2026/06/08/fresh-news")

    errs = validate_daily_quality(
        issue_date="2026-06-08",
        digest_root=tmp_path / "digest",
        jsonl_path=tmp_path / "data" / "articles.jsonl",
    )

    joined = "\n".join(errs)
    assert "has 4 article(s); target is 5" not in joined
    assert "quality_shortfall_reason" not in joined


def test_daily_quality_rejects_zero_article_category_digest(tmp_path: Path) -> None:
    """カテゴリ digest が存在しても記事カード 0 件なら公開物として成立していないため落とす。"""
    _write_summary(tmp_path)
    cat_dir = tmp_path / "digest" / "AI"
    cat_dir.mkdir(parents=True)
    (cat_dir / "2026-06-08-AI.md").write_text(
        "---\n"
        "title: AI\n"
        "date: 2026-06-08\n"
        "categoryId: ai\n"
        "---\n\n"
        "# AI\n\n"
        "> [!summary]\n"
        "> 記事カードがない要約だけの digest。\n",
        encoding="utf-8",
    )
    _write_jsonl(tmp_path, "https://example.com/2026/06/08/fresh-news")

    errs = validate_daily_quality(
        issue_date="2026-06-08",
        digest_root=tmp_path / "digest",
        jsonl_path=tmp_path / "data" / "articles.jsonl",
    )

    joined = "\n".join(errs)
    assert "has 0 article(s)" in joined
    assert "category digest is not an article page" in joined


def test_daily_quality_rejects_news_grasp_self_reference_thumbnail(tmp_path: Path) -> None:
    """articles.jsonl の thumb が News-Grasp 自己参照なら記事固有サムネではないため落とす。"""
    _write_summary(tmp_path)
    url = "https://example.com/2026/06/08/fresh-news"
    _write_category(tmp_path, url)
    _write_jsonl(
        tmp_path,
        url,
        extra={
            "title_ja": "Freshness test article",
            "thumb": "https://hidepon-umg.github.io/News-Grasp/assets/og/ai.jpg",
        },
    )

    errs = validate_daily_quality(
        issue_date="2026-06-08",
        digest_root=tmp_path / "digest",
        jsonl_path=tmp_path / "data" / "articles.jsonl",
    )

    joined = "\n".join(errs)
    assert "News-Grasp 自己参照 thumb" in joined


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


# ── dedup 強化版の鮮度注釈刻印検証 (2026-06-11) ──────────────────────────────


def _write_jsonl_records(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )


def test_dedup_annotation_warns_when_all_records_unannotated() -> None:
    """当日レコード全件に date_evidence_source が無ければ警告する (fatal でない)。"""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        jsonl = Path(td) / "articles.jsonl"
        _write_jsonl_records(jsonl, [
            {"date": "2026-06-11", "title": "A", "url": "https://example.com/a"},
            {"date": "2026-06-11", "title": "B", "url": "https://example.com/b"},
        ])
        warns = validate_dedup_annotation_present(jsonl, date(2026, 6, 11))
        assert len(warns) == 1, f"全件注釈なしは警告 1 件: {warns}"
        assert "date_evidence_source" in warns[0]


def test_dedup_annotation_no_warn_when_one_record_annotated() -> None:
    """当日レコードのうち 1 件でも注釈があれば警告しない (warn-pass で残り注釈なしは許容)。"""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        jsonl = Path(td) / "articles.jsonl"
        _write_jsonl_records(jsonl, [
            {"date": "2026-06-11", "title": "A", "url": "https://example.com/a",
             "date_evidence_source": "url-path", "published_date": "2026-06-11"},
            {"date": "2026-06-11", "title": "B", "url": "https://example.com/b"},
        ])
        assert validate_dedup_annotation_present(jsonl, date(2026, 6, 11)) == []


def test_dedup_annotation_not_applied_before_start_date() -> None:
    """注釈導入日 (2026-06-11) より前の号には刻印検証を適用しない。"""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        jsonl = Path(td) / "articles.jsonl"
        _write_jsonl_records(jsonl, [
            {"date": "2026-06-10", "title": "A", "url": "https://example.com/a"},
        ])
        assert validate_dedup_annotation_present(jsonl, date(2026, 6, 10)) == []


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


def test_daily_quality_ignores_non_scheduled_game_digest_on_wednesday(tmp_path: Path) -> None:
    """水曜の Game artifact は非対象なので記事数 gate の失敗にしない。"""
    issue = date(2026, 6, 24)
    game = tmp_path / "digest" / "Game" / "2026-06-24-Game.md"
    game.parent.mkdir(parents=True)
    game.write_text(
        "---\n"
        "title: Game\n"
        "date: 2026-06-24\n"
        "categoryId: game\n"
        "---\n\n"
        "# Game\n",
        encoding="utf-8",
    )

    assert validate_digest_article_counts(tmp_path / "digest", issue) == []


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


def test_deepdive_presence_rejects_missing_issue_deepdive(tmp_path: Path) -> None:
    """当日 DeepDive md/html が無い号を publish 完了扱いにしない。"""
    errs = validate_deepdive_presence(
        digest_root=tmp_path / "digest",
        docs_root=tmp_path / "docs",
        issue=date(2026, 6, 8),
    )

    joined = "\n".join(errs)
    assert "DeepDive digest が存在しません" in joined
    assert "DeepDive HTML が存在しません" in joined


def test_daily_quality_cli_can_require_deepdive(tmp_path: Path, capsys) -> None:
    """publish 前 gate は --require-deepdive で当日 DeepDive 欠落を exit 1 にする。"""
    _write_summary(tmp_path)
    url = "https://example.com/2026/06/08/fresh-news"
    _write_category(tmp_path, url)
    _write_jsonl(tmp_path, url)

    rc = main([
        "--date", "2026-06-08",
        "--digest-root", str(tmp_path / "digest"),
        "--jsonl", str(tmp_path / "data" / "articles.jsonl"),
        "--docs-root", str(tmp_path / "docs"),
        "--require-deepdive",
    ])

    captured = capsys.readouterr()
    assert rc == 1
    assert "DeepDive digest が存在しません" in captured.err


def test_deepdive_relations_layout_rejects_explicit_crossing(tmp_path: Path) -> None:
    """明示座標つき関係図は、避けられる線交差を daily gate で落とす。"""
    dd_dir = tmp_path / "digest" / "DeepDive"
    dd_dir.mkdir(parents=True)
    (dd_dir / "2026-06-08-DeepDive.md").write_text(
        "---\ntitle: test\ndate: 2026-06-08\n---\n\n"
        "```relations\n"
        "{\n"
        '  "title": "交差検出",\n'
        '  "nodes": [\n'
        '    {"id": "a", "label": "A", "x": 100, "y": 100},\n'
        '    {"id": "b", "label": "B", "x": 500, "y": 100},\n'
        '    {"id": "c", "label": "C", "x": 100, "y": 500},\n'
        '    {"id": "d", "label": "D", "x": 500, "y": 500}\n'
        "  ],\n"
        '  "edges": [\n'
        '    {"from": "a", "to": "d", "label": "ad", "kind": "供給"},\n'
        '    {"from": "c", "to": "b", "label": "cb", "kind": "供給"}\n'
        "  ]\n"
        "}\n"
        "```\n",
        encoding="utf-8",
    )

    errs = validate_deepdive_relations_layout(
        digest_root=tmp_path / "digest",
        issue=date(2026, 6, 8),
    )

    assert "線交差" in "\n".join(errs)


def test_deepdive_relations_layout_rejects_row_group_mismatch(tmp_path: Path) -> None:
    """rowGroups で同役割指定したノードは、同じ y 行でなければ daily gate が落とす。"""
    dd_dir = tmp_path / "digest" / "DeepDive"
    dd_dir.mkdir(parents=True)
    (dd_dir / "2026-06-08-DeepDive.md").write_text(
        "---\ntitle: test\ndate: 2026-06-08\n---\n\n"
        "```relations\n"
        "{\n"
        '  "title": "行揃え検出",\n'
        '  "rowGroups": [["boj", "fed"]],\n'
        '  "nodes": [\n'
        '    {"id": "boj", "label": "日銀", "x": 100, "y": 200},\n'
        '    {"id": "fed", "label": "Fed", "x": 500, "y": 260},\n'
        '    {"id": "market", "label": "市場", "x": 300, "y": 500}\n'
        "  ],\n"
        '  "edges": [\n'
        '    {"from": "boj", "to": "market", "label": "円", "kind": "規制"},\n'
        '    {"from": "fed", "to": "market", "label": "ドル", "kind": "供給"}\n'
        "  ]\n"
        "}\n"
        "```\n",
        encoding="utf-8",
    )

    errs = validate_deepdive_relations_layout(
        digest_root=tmp_path / "digest",
        issue=date(2026, 6, 8),
    )

    assert "同じ行" in "\n".join(errs)
