from __future__ import annotations

from pathlib import Path

import json

from tools.repair_registry import (
    RepairContext,
    find_handler,
    repair_with_registry,
)
from tools.validate_daily_quality import validate_summary_emphasis


def test_registry_exposes_summary_emphasis_patch_metadata() -> None:
    handler = find_handler("summary-emphasis-patch")

    assert handler is not None
    assert handler.handler_id == "summary-emphasis-patch"
    assert handler.kind == "deterministic"
    assert handler.verify_gate == "daily-quality"
    assert "digest/Summary/{date}.md" in handler.allowed_artifacts


def test_missing_handler_returns_typed_unimplemented_status(tmp_path: Path) -> None:
    result = repair_with_registry(
        RepairContext(
            repo_root=tmp_path,
            issue="2026-06-25",
            handler_id="does-not-exist",
            artifacts=[],
        )
    )

    assert not result.changed
    assert result.status == "blocked_repair_handler_unimplemented"


def test_summary_emphasis_patch_updates_existing_summary_only(tmp_path: Path) -> None:
    summary = tmp_path / "digest" / "Summary" / "2026-06-25.md"
    other = tmp_path / "digest" / "AI" / "2026-06-25-AI.md"
    summary.parent.mkdir(parents=True)
    other.parent.mkdir(parents=True)
    summary.write_text("# Summary\n\n### AI\n\n市場の変化を整理する。\n", encoding="utf-8")
    other.write_text("# AI\n\nこのファイルは触らない。\n", encoding="utf-8")

    result = repair_with_registry(
        RepairContext(
            repo_root=tmp_path,
            issue="2026-06-25",
            handler_id="summary-emphasis-patch",
            artifacts=["digest/Summary/2026-06-25.md"],
        )
    )

    assert result.changed
    assert result.status == "repaired"
    repaired = summary.read_text(encoding="utf-8")
    assert "**市場の変化**" in repaired
    assert "__**市場の変化**（[[市場の変化]]）を整理する。__" in repaired
    assert other.read_text(encoding="utf-8") == "# AI\n\nこのファイルは触らない。\n"


def test_summary_emphasis_patch_is_idempotent(tmp_path: Path) -> None:
    summary = tmp_path / "digest" / "Summary" / "2026-06-25.md"
    summary.parent.mkdir(parents=True)
    summary.write_text("# Summary\n\n### AI\n\n市場の変化を整理する。\n", encoding="utf-8")
    ctx = RepairContext(
        repo_root=tmp_path,
        issue="2026-06-25",
        handler_id="summary-emphasis-patch",
        artifacts=["digest/Summary/2026-06-25.md"],
    )

    first = repair_with_registry(ctx)
    second = repair_with_registry(ctx)

    assert first.status == "repaired"
    assert second.status in {"noop", "not_applicable"}
    assert summary.read_text(encoding="utf-8").count("**市場の変化**") == 1


def test_summary_emphasis_patch_preserves_frontmatter_and_repairs_reflection(tmp_path: Path) -> None:
    issue = "2026-06-25"
    summary = tmp_path / "digest" / "Summary" / f"{issue}.md"
    summary.parent.mkdir(parents=True)
    summary.write_text(
        "---\n"
        "title: Summary\n"
        f"date: {issue}\n"
        "category: Daily Summary\n"
        "hero_left: プラットフォーム再編\n"
        "hero_right: 市場へ波及\n"
        "---\n\n"
        "# Summary\n\n"
        "## § 本日のテーマ考察\n\n"
        "> [[政策イベント]] と __企業実装__ が同じ日に並んだ。\n\n"
        "### §01 総論 — 実装力を見る日\n\n"
        "[[AI導入]] は __継続運用できる体制__ で評価される。\n",
        encoding="utf-8",
    )

    result = repair_with_registry(
        RepairContext(
            repo_root=tmp_path,
            issue=issue,
            handler_id="summary-emphasis-patch",
            artifacts=[f"digest/Summary/{issue}.md"],
        )
    )

    repaired = summary.read_text(encoding="utf-8")
    assert result.status == "repaired"
    assert "title: Summary" in repaired
    assert "**title: Summary**" not in repaired
    assert validate_summary_emphasis(summary) == []


def test_summary_emphasis_patch_repairs_missing_underline_when_bold_exists(tmp_path: Path) -> None:
    issue = "2026-06-25"
    summary = tmp_path / "digest" / "Summary" / f"{issue}.md"
    summary.parent.mkdir(parents=True)
    summary.write_text(
        "# Summary\n\n"
        "## § 本日のテーマ考察\n\n"
        "> [[政策イベント]] は **企業実装** と並んだが、継続運用の視点も必要だ。\n\n"
        "### §01 総論 — 実装力を見る日\n\n"
        "[[AI導入]] は **継続運用できる体制** で評価される。\n",
        encoding="utf-8",
    )

    result = repair_with_registry(
        RepairContext(
            repo_root=tmp_path,
            issue=issue,
            handler_id="summary-emphasis-patch",
            artifacts=[f"digest/Summary/{issue}.md"],
        )
    )

    repaired = summary.read_text(encoding="utf-8")
    assert result.status == "repaired"
    assert "__" in repaired
    assert validate_summary_emphasis(summary) == []


def test_summary_emphasis_patch_repairs_missing_wikilink_when_bold_and_underline_exist(tmp_path: Path) -> None:
    issue = "2026-06-30"
    summary = tmp_path / "digest" / "Summary" / f"{issue}.md"
    summary.parent.mkdir(parents=True)
    summary.write_text(
        "# Summary\n\n"
        "## § 本日のテーマ考察\n\n"
        "> [[政策イベント]] は **企業実装** と __運用体制__ を同時に動かした。\n\n"
        "### §03 IT — Jiraと観測基盤が運用をつなぐ\n\n"
        "__**Copilot for Jira の一般提供、Kiro 連携の可観測性、Amazon MSK のエージェントスキル**"
        "が揃い、開発、運用、委託先リスクが一本につながりました。__\n",
        encoding="utf-8",
    )

    result = repair_with_registry(
        RepairContext(
            repo_root=tmp_path,
            issue=issue,
            handler_id="summary-emphasis-patch",
            artifacts=[f"digest/Summary/{issue}.md"],
        )
    )

    repaired = summary.read_text(encoding="utf-8")
    assert result.status == "repaired"
    assert "[[Copilot for Jira の一般提供、Kiro 連携の可観測性、Amazon MSK のエージェントスキル]]" in repaired
    assert validate_summary_emphasis(summary) == []


def test_registry_blocks_handler_scope_violation(tmp_path: Path) -> None:
    summary = tmp_path / "digest" / "Summary" / "2026-06-25.md"
    summary.parent.mkdir(parents=True)
    summary.write_text("# Summary\n\n### AI\n\n市場の変化を整理する。\n", encoding="utf-8")

    result = repair_with_registry(
        RepairContext(
            repo_root=tmp_path,
            issue="2026-06-25",
            handler_id="summary-emphasis-patch",
            artifacts=["docs/index.html"],
        )
    )

    assert result.status == "repair_context_scope_mismatch"
    assert not result.changed
    assert "**市場の変化**" not in summary.read_text(encoding="utf-8")


def test_summary_emphasis_patch_ignores_unrelated_gate_artifacts(tmp_path: Path) -> None:
    issue = "2026-06-25"
    summary = tmp_path / "digest" / "Summary" / f"{issue}.md"
    other = tmp_path / "digest" / "AI" / f"{issue}-AI.md"
    summary.parent.mkdir(parents=True)
    other.parent.mkdir(parents=True)
    summary.write_text("# Summary\n\n### AI\n\n市場の変化を整理する。\n", encoding="utf-8")
    other.write_text("# AI\n\nこのファイルは触らない。\n", encoding="utf-8")

    result = repair_with_registry(
        RepairContext(
            repo_root=tmp_path,
            issue=issue,
            handler_id="summary-emphasis-patch",
            artifacts=[
                f"digest/Summary/{issue}.md",
                f"digest/AI/{issue}-AI.md",
                "data/articles.jsonl",
                f"data/search_audit/{issue}/mobility.json",
            ],
        )
    )

    assert result.status == "repaired"
    assert result.artifacts == (f"digest/Summary/{issue}.md",)
    repaired = summary.read_text(encoding="utf-8")
    assert "**市場の変化**" in repaired
    assert "__**市場の変化**（[[市場の変化]]）を整理する。__" in repaired
    assert other.read_text(encoding="utf-8") == "# AI\n\nこのファイルは触らない。\n"


def test_registry_reports_output_scope_violation_separately(tmp_path: Path, monkeypatch) -> None:
    import tools.repair_registry as registry

    def bad_repair(ctx: RepairContext) -> registry.RepairResult:
        return registry.RepairResult(ctx.handler_id, "repaired", True, ("docs/index.html",))

    handler = registry.RepairHandler(
        handler_id="bad-output-scope-test",
        kind="deterministic",
        allowed_artifacts=("digest/Summary/{date}.md",),
        verify_gate="daily-quality",
        repair=bad_repair,
    )
    monkeypatch.setitem(registry.REGISTRY, handler.handler_id, handler)

    result = repair_with_registry(
        RepairContext(
            repo_root=tmp_path,
            issue="2026-06-25",
            handler_id=handler.handler_id,
            artifacts=["digest/Summary/2026-06-25.md"],
        )
    )

    assert result.status == "repair_handler_output_scope_violation"
    assert not result.changed


def test_registry_maps_handler_not_applicable_to_typed_block(tmp_path: Path, monkeypatch) -> None:
    import tools.repair_registry as registry

    def no_match(ctx: RepairContext) -> registry.RepairResult:
        return registry.RepairResult(ctx.handler_id, registry.NOT_APPLICABLE_STATUS, False)

    handler = registry.RepairHandler(
        handler_id="not-applicable-test",
        kind="deterministic",
        allowed_artifacts=("digest/Summary/{date}.md",),
        verify_gate="daily-quality",
        repair=no_match,
    )
    monkeypatch.setitem(registry.REGISTRY, handler.handler_id, handler)

    result = repair_with_registry(
        RepairContext(
            repo_root=tmp_path,
            issue="2026-06-25",
            handler_id=handler.handler_id,
            artifacts=["digest/Summary/2026-06-25.md"],
        )
    )

    assert result.status == "blocked_deterministic_repair_not_applicable"
    assert not result.changed


def test_search_audit_metadata_patch_promotes_dropped_examples_and_terms(tmp_path: Path) -> None:
    issue = "2026-06-25"
    audit = tmp_path / "data" / "search_audit" / issue / "mobility.json"
    audit.parent.mkdir(parents=True)
    audit.write_text(
        json.dumps(
            {
                "date": issue,
                "category_id": "mobility",
                "queries": ["q1", "q2", "q3"],
                "raw_results_total": 25,
                "candidates_total": 25,
                "selected_total": 4,
                "dropped_examples": [
                    {
                        "title": "old candidate",
                        "resolved_url": "https://example.com/old",
                        "published_date": "2026-06-01",
                        "reason": "actual_source_age_gt_1d",
                    }
                ],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    result = repair_with_registry(
        RepairContext(
            repo_root=tmp_path,
            issue=issue,
            handler_id="search-audit-metadata-patch",
            artifacts=[f"data/search_audit/{issue}/mobility.json"],
        )
    )

    repaired = json.loads(audit.read_text(encoding="utf-8"))
    assert result.status == "repaired"
    assert repaired["dropped"] == repaired["dropped_examples"]
    assert {"BYD", "Tesla", "Toyota", "Uber", "Waymo"}.issubset(set(repaired["coverage_terms_checked"]))


def test_url_quarantine_refill_handler_repairs_stale_followup_from_registry(tmp_path: Path) -> None:
    issue = "2026-06-28"
    stale_url = "https://example.com/no-date/followup-topic"
    reserve_url = "https://example.com/fresh-reserve"
    digest = tmp_path / "digest" / "AI" / f"{issue}-AI.md"
    records = tmp_path / "tmp" / "newsroom" / issue / "ai.records.jsonl"
    articles = tmp_path / "data" / "articles.jsonl"
    audit = tmp_path / "data" / "search_audit" / issue / "ai.json"
    candidate_dir = tmp_path / "build" / "deduped-candidates"
    for path in (digest, records, articles, audit, candidate_dir / "ai_candidates.jsonl"):
        path.parent.mkdir(parents=True, exist_ok=True)

    rows = [
        {
            "date": issue,
            "genre": "AI",
            "title": f"title {idx}",
            "title_ja": f"title {idx}",
            "summary": f"summary {idx}",
            "url": f"https://example.com/fresh-{idx}",
            "thumb": "https://example.com/thumb.jpg",
            "source": "Example",
            "published": issue,
            "date_evidence_source": "rss_pubDate",
        }
        for idx in range(1, 5)
    ]
    stale = {
        "date": issue,
        "genre": "AI",
        "title": "stale followup",
        "title_ja": "stale followup",
        "summary": "old matched source",
        "url": stale_url,
        "thumb": "https://example.com/thumb.jpg",
        "source": "Example",
        "published": issue,
        "date_evidence_source": "rss_pubDate",
        "is_followup": True,
        "matched_with": "https://example.com/2026/05/20/original-topic",
    }
    all_rows = rows + [stale]
    digest.write_text(
        "# AI\n"
        + "\n---\n".join(
            f"### [7{idx}] {row['title']}\n\n{issue} · 🔗 [元記事]({row['url']})"
            for idx, row in enumerate(all_rows, start=1)
        )
        + "\n",
        encoding="utf-8",
    )
    records.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in all_rows), encoding="utf-8")
    articles.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in all_rows), encoding="utf-8")
    audit.write_text(
        json.dumps({"category_id": "ai", "date": issue, "selected_total": 5, "dropped": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    reserve = {
        "category": "ai",
        "pubDate": f"{issue}T09:35:00+00:00",
        "source": "Reserve",
        "title": "fresh reserve",
        "url": reserve_url,
        "thumb": "https://example.com/reserve-thumb.jpg",
    }
    (candidate_dir / "ai_candidates.jsonl").write_text(json.dumps(reserve, ensure_ascii=False) + "\n", encoding="utf-8")

    result = repair_with_registry(
        RepairContext(
            repo_root=tmp_path,
            issue=issue,
            handler_id="url-quarantine-refill",
            artifacts=[f"digest/Summary/{issue}.md", "data/articles.jsonl", f"data/search_audit/{issue}"],
        )
    )

    assert result.status == "repaired"
    assert result.changed
    assert "autonomous_recovery: url_quarantine_refill" in result.message
    assert stale_url not in records.read_text(encoding="utf-8")
    assert stale_url not in articles.read_text(encoding="utf-8")
    assert stale_url not in digest.read_text(encoding="utf-8")
    assert reserve_url in records.read_text(encoding="utf-8")
    assert reserve_url in articles.read_text(encoding="utf-8")
    assert reserve_url in digest.read_text(encoding="utf-8")


def test_url_quarantine_refill_handler_repairs_missing_thumb_record(tmp_path: Path) -> None:
    issue = "2026-06-30"
    bad_url = "https://example.com/no-thumb"
    reserve_url = "https://example.com/thumb-reserve"
    digest = tmp_path / "digest" / "IT-Consulting" / f"{issue}-IT-Consulting.md"
    records = tmp_path / "tmp" / "newsroom" / issue / "it.records.jsonl"
    articles = tmp_path / "data" / "articles.jsonl"
    audit = tmp_path / "data" / "search_audit" / issue / "it.json"
    candidate_dir = tmp_path / "build" / "deduped-candidates"
    for path in (digest, records, articles, audit, candidate_dir / "it_candidates.jsonl"):
        path.parent.mkdir(parents=True, exist_ok=True)

    good_rows = [
        {
            "date": issue,
            "genre": "IT-Consulting",
            "title": f"good {idx}",
            "title_ja": f"good {idx}",
            "summary": f"summary {idx}",
            "url": f"https://example.com/good-{idx}",
            "thumb": "https://example.com/thumb.jpg",
            "source": "Example",
            "published": issue,
            "date_evidence_source": "rss_pubDate",
        }
        for idx in range(1, 5)
    ]
    bad = {
        "date": issue,
        "genre": "IT-Consulting",
        "title": "no thumb",
        "title_ja": "no thumb",
        "summary": "missing thumbnail",
        "url": bad_url,
        "thumb": None,
        "source": "Example",
        "published": issue,
        "date_evidence_source": "rss_pubDate",
    }
    all_rows = [*good_rows, bad]
    digest.write_text(
        "# IT\n"
        + "\n---\n".join(
            f"### [7{idx}] {row['title']}\n\n{issue} · 🔗 [元記事]({row['url']})\n\n![thumb]({row['thumb'] if row['thumb'] else 'null'})"
            for idx, row in enumerate(all_rows, start=1)
        )
        + "\n",
        encoding="utf-8",
    )
    payload = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in all_rows)
    records.write_text(payload, encoding="utf-8")
    articles.write_text(payload, encoding="utf-8")
    audit.write_text(
        json.dumps({"category_id": "it", "date": issue, "selected_total": 5, "dropped": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    reserve = {
        "category": "it",
        "pubDate": f"{issue}T09:35:00+00:00",
        "source": "Reserve",
        "title": "thumb reserve",
        "url": reserve_url,
        "thumb": "https://example.com/reserve-thumb.jpg",
    }
    (candidate_dir / "it_candidates.jsonl").write_text(json.dumps(reserve, ensure_ascii=False) + "\n", encoding="utf-8")

    result = repair_with_registry(
        RepairContext(
            repo_root=tmp_path,
            issue=issue,
            handler_id="url-quarantine-refill",
            artifacts=[f"digest/IT-Consulting/{issue}-IT-Consulting.md", "data/articles.jsonl", f"data/search_audit/{issue}"],
        )
    )

    assert result.status == "repaired"
    assert bad_url not in records.read_text(encoding="utf-8")
    assert bad_url not in articles.read_text(encoding="utf-8")
    assert bad_url not in digest.read_text(encoding="utf-8")
    assert reserve_url in records.read_text(encoding="utf-8")
    assert reserve_url in articles.read_text(encoding="utf-8")
    assert reserve_url in digest.read_text(encoding="utf-8")


def test_url_quarantine_refill_handler_reorders_stale_top_article(tmp_path: Path) -> None:
    issue = "2026-06-28"
    digest = tmp_path / "digest" / "IT-Consulting" / f"{issue}-IT-Consulting.md"
    articles = tmp_path / "data" / "articles.jsonl"
    audit = tmp_path / "data" / "search_audit" / issue / "it.json"
    records = tmp_path / "tmp" / "newsroom" / issue / "it.records.jsonl"
    for path in (digest, articles, audit, records):
        path.parent.mkdir(parents=True, exist_ok=True)
    stale = {
        "date": issue,
        "genre": "IT-Consulting",
        "title": "stale top",
        "title_ja": "stale top",
        "summary": "old item",
        "url": "https://example.com/no-date/stale-top",
        "thumb": "https://example.com/thumb.jpg",
        "source": "Example",
        "published": "2026-06-26",
        "date_evidence_source": "body-text",
    }
    fresh = {
        "date": issue,
        "genre": "IT-Consulting",
        "title": "fresh second",
        "title_ja": "fresh second",
        "summary": "fresh item",
        "url": "https://example.com/no-date/fresh-second",
        "thumb": "https://example.com/thumb.jpg",
        "source": "Example",
        "published": "2026-06-27",
        "date_evidence_source": "body-text",
    }
    articles.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in [stale, fresh]), encoding="utf-8")
    records.write_text(articles.read_text(encoding="utf-8"), encoding="utf-8")
    audit.write_text(json.dumps({"category_id": "it", "date": issue, "selected_total": 2}, ensure_ascii=False), encoding="utf-8")
    digest.write_text(
        "---\ncategoryId: it\n---\n\n"
        "# IT\n\n"
        "### [91] stale top\n\n"
        "📅 2026-06-26 00:00 · 📰 Example · 🔗 [元記事](https://example.com/no-date/stale-top)\n\n"
        "---\n\n"
        "### [88] fresh second\n\n"
        "📅 2026-06-27 09:00 · 📰 Example · 🔗 [元記事](https://example.com/no-date/fresh-second)\n\n"
        "← [[2026-06-27-IT-Consulting|前号 IT-Consulting]]\n",
        encoding="utf-8",
    )

    result = repair_with_registry(
        RepairContext(
            repo_root=tmp_path,
            issue=issue,
            handler_id="url-quarantine-refill",
            artifacts=[f"digest/Summary/{issue}.md", "data/articles.jsonl", f"data/search_audit/{issue}"],
        )
    )

    repaired = digest.read_text(encoding="utf-8")
    assert result.status == "repaired"
    assert "stale_top_reordered" in result.message
    assert repaired.index("fresh second") < repaired.index("stale top")


def test_digest_articles_reconcile_handler_appends_current_reporter_records(tmp_path: Path) -> None:
    issue = "2026-06-28"
    articles = tmp_path / "data" / "articles.jsonl"
    records = tmp_path / "tmp" / "newsroom" / issue / "ai.records.jsonl"
    manifest = tmp_path / "build" / "reporter-artifacts" / issue / "editor-input-manifest.json"
    for path in (articles, records, manifest):
        path.parent.mkdir(parents=True, exist_ok=True)

    old = {
        "date": "2026-06-27",
        "genre": "AI",
        "title": "old",
        "title_ja": "old",
        "summary": "old",
        "url": "https://example.com/old",
        "source": "Example",
        "published": "2026-06-27",
        "date_evidence_source": "rss_pubDate",
    }
    current = {
        "date": issue,
        "genre": "AI",
        "title": "current",
        "title_ja": "current",
        "summary": "current",
        "url": "https://example.com/current",
        "source": "Example",
        "published": issue,
        "date_evidence_source": "rss_pubDate",
    }
    articles.write_text(json.dumps(old, ensure_ascii=False) + "\n", encoding="utf-8")
    records.write_text(json.dumps(current, ensure_ascii=False) + "\n", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "date": issue,
                "scheduled_categories": ["ai"],
                "reporter_artifacts": [f"tmp/newsroom/{issue}/ai.records.jsonl"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = repair_with_registry(
        RepairContext(
            repo_root=tmp_path,
            issue=issue,
            handler_id="digest-articles-reconcile-patch",
            artifacts=["digest", "data/articles.jsonl", "data/_status.md"],
        )
    )

    repaired = articles.read_text(encoding="utf-8")
    assert result.status == "repaired"
    assert result.changed
    assert "appended_current_reporter_records=1" in result.message
    assert "https://example.com/old" in repaired
    assert "https://example.com/current" in repaired


def test_audio_script_length_patch_blocks_outline_missing_repeated_closing(tmp_path: Path) -> None:
    issue = "2026-06-28"
    summary_dir = tmp_path / "digest" / "Summary"
    summary_dir.mkdir(parents=True)
    history_tail = "ありがとうございました。\nニュースグラスプでした。\n今日はここまでです。\n"
    (summary_dir / "2026-06-27-audio-script.md").write_text(history_tail, encoding="utf-8")
    body = (
        "6月28日の朝のニュースです。\n"
        "AI、FX、Game、IT、Mobilityを順に見ます。\n"
        + "\n".join(
            f"今日の論点{i}は、認証と運用と説明責任を同じ順番で確認することです。"
            for i in range(70)
        )
        + "\n今日の観点・考察として、広げる前に守る条件をそろえることが重要です。\n"
        + history_tail
    )
    (summary_dir / f"{issue}-audio-script.md").write_text(body, encoding="utf-8")

    result = repair_with_registry(
        RepairContext(
            repo_root=tmp_path,
            issue=issue,
            handler_id="audio-script-length-patch",
            artifacts=[f"digest/Summary/{issue}-audio-script.md"],
        )
    )

    assert result.status == "blocked_deterministic_repair_not_applicable"
    assert not result.changed


def test_audio_script_length_patch_blocks_outline_missing_short_script(tmp_path: Path) -> None:
    issue = "2026-07-02"
    summary_dir = tmp_path / "digest" / "Summary"
    summary_dir.mkdir(parents=True)
    body = (
        "7月2日の朝のニュースをお伝えします。ニュース グラスプ、7月2日号です。\n"
        "FX、AI、IT、Mobility、Manufacturing、Economy、Gameを順に見ます。\n"
        "FXでは円安と政策発言の受け止めを確認します。\n"
        "AIでは投資と配布面の競争を確認します。\n"
        "ITでは導入前後の審査と監視を確認します。\n"
        "Mobilityでは安全標準と運行条件を確認します。\n"
        "Manufacturingでは量産拠点と供給網を確認します。\n"
        "Economyでは物価と金利の重さを確認します。\n"
        "Gameでは販路と安全設計を確認します。\n"
        "今日の観点・考察として、条件を先にそろえることが重要です。\n"
    )
    (summary_dir / f"{issue}-audio-script.md").write_text(body, encoding="utf-8")

    result = repair_with_registry(
        RepairContext(
            repo_root=tmp_path,
            issue=issue,
            handler_id="audio-script-length-patch",
            artifacts=[f"digest/Summary/{issue}-audio-script.md"],
        )
    )

    assert result.status == "blocked_deterministic_repair_not_applicable"
    assert not result.changed
