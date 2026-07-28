from __future__ import annotations

from pathlib import Path

import json
from types import SimpleNamespace

import tools.repair_registry as registry_module
from tools.repair_registry import (
    RepairContext,
    RepairResult,
    find_handler,
    repair_with_registry,
)
from tools.generate_pages import parse_articles
from tools.validate_digest_articles_reconcile import reconcile
from tools.validate_daily_quality import validate_summary_emphasis
from tools.validate_generation_quality import _validate_summary


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


def test_registry_rejects_invalid_issue_date_before_handler(tmp_path: Path) -> None:
    result = repair_with_registry(
        RepairContext(
            repo_root=tmp_path,
            issue="../../outside",
            handler_id="summary-emphasis-patch",
            artifacts=[],
        )
    )

    assert result.status == "repair_handler_output_scope_violation"
    assert not result.changed
    assert "invalid issue date" in result.message


def test_registry_cli_returns_nonzero_for_noop(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        registry_module,
        "repair_with_registry",
        lambda _ctx: RepairResult(
            "summary-emphasis-patch",
            "noop",
            False,
            (),
            "no mutation",
        ),
    )

    rc = registry_module.main(
        [
            "repair",
            "--handler-id",
            "summary-emphasis-patch",
            "--repo-root",
            str(tmp_path),
            "--date",
            "2026-07-27",
        ]
    )

    assert rc == 1


def test_registry_cli_refuses_repair_when_completeness_audit_is_not_green(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    monkeypatch.setattr(
        registry_module,
        "_audit_current_repair_system",
        lambda: SimpleNamespace(
            ok=False,
            findings=(
                SimpleNamespace(
                    code="registry_handler_unreachable",
                    detail="dead-handler",
                ),
            ),
        ),
    )

    rc = registry_module.main(
        [
            "repair",
            "--handler-id",
            "summary-emphasis-patch",
            "--repo-root",
            str(tmp_path),
            "--date",
            "2026-07-27",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["status"] == "blocked_repair_system_incomplete"
    assert payload["findings"][0]["code"] == "registry_handler_unreachable"


def test_summary_reflection_patch_passes_same_generation_validator(tmp_path: Path) -> None:
    issue = "2026-07-22"
    rel = f"digest/Summary/{issue}.md"
    summary = tmp_path / rel
    summary.parent.mkdir(parents=True)
    summary.write_text(
        "---\n"
        f"date: {issue}\n"
        'hero_left: "今日の論点"\n'
        'hero_right: "意思決定への示唆"\n'
        "---\n\n"
        "# Summary\n\n"
        "当日のニュースを整理する。\n",
        encoding="utf-8",
    )
    assert any(error.code == "summary_reflection_missing" for error in _validate_summary(tmp_path, rel, issue))

    result = repair_with_registry(
        RepairContext(
            repo_root=tmp_path,
            issue=issue,
            handler_id="summary-reflection-patch",
            artifacts=[rel],
        )
    )

    assert result.status == "repaired"
    assert not _validate_summary(tmp_path, rel, issue)


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


def test_category_emphasis_patch_never_rewrites_thumbnail_markdown(tmp_path: Path) -> None:
    digest = tmp_path / "digest" / "IT-Consulting" / "2026-07-11-IT-Consulting.md"
    digest.parent.mkdir(parents=True)
    thumb = "![thumb](https://example.com/thumb.jpg)"
    digest.write_text(
        f"---\ncategoryId: it\n---\n\n### [80] Test\n\n{thumb}\n\n- plain sentence without markers\n",
        encoding="utf-8",
    )
    result = repair_with_registry(
        RepairContext(
            repo_root=tmp_path,
            issue="2026-07-11",
            handler_id="category-card-emphasis-patch",
            artifacts=["digest/IT-Consulting/2026-07-11-IT-Consulting.md"],
        )
    )
    repaired = digest.read_text(encoding="utf-8")
    assert result.changed
    assert thumb in repaired
    assert "[[![thumb" not in repaired


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


def test_search_audit_metadata_patch_promotes_dropped_reason_summary(tmp_path: Path) -> None:
    issue = "2026-07-22"
    audit = tmp_path / "data" / "search_audit" / issue / "ai.json"
    audit.parent.mkdir(parents=True)
    audit.write_text(
        json.dumps(
            {
                "date": issue,
                "category_id": "ai",
                "queries": ["q1", "q2", "q3"],
                "raw_results_total": 25,
                "candidates_total": 25,
                "selected_total": 4,
                "dropped_count": 20,
                "dropped_reason_summary": "Google News代理URL、読者価値が低い候補、同一テーマ転載を除外した。",
                "coverage_terms_checked": [
                    "OpenAI",
                    "Anthropic",
                    "Google",
                    "Apple",
                    "Microsoft",
                    "Meta",
                    "NVIDIA",
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
            artifacts=[f"data/search_audit/{issue}/ai.json"],
        )
    )

    repaired = json.loads(audit.read_text(encoding="utf-8"))
    assert result.status == "repaired"
    assert repaired["dropped"] == [
        {
            "count": 20,
            "reason": "Google News代理URL、読者価値が低い候補、同一テーマ転載を除外した。",
        }
    ]


def test_search_audit_metadata_patch_promotes_dropped_or_not_selected_reasons(tmp_path: Path) -> None:
    issue = "2026-07-23"
    audit = tmp_path / "data" / "search_audit" / issue / "it.json"
    audit.parent.mkdir(parents=True)
    audit.write_text(
        json.dumps(
            {
                "date": issue,
                "category_id": "it",
                "queries": ["q1", "q2", "q3"],
                "raw_results_total": 25,
                "candidates_total": 25,
                "selected_total": 5,
                "dropped_or_not_selected": [
                    {
                        "title": "公取委、巨大IT対応で組織再編",
                        "reason": "同一論点の正規記事URLを限定検索で確定できず、未解決URLを採用しなかった",
                    },
                    {
                        "title": "東レ子会社が国内4工場のDB刷新",
                        "reason": "確認できた同題材の公開記事が鮮度ゲート外のため採用しなかった",
                    },
                ],
                "coverage_terms_checked": [
                    "Accenture",
                    "BCG",
                    "Deloitte",
                    "McKinsey",
                    "NTT",
                    "PwC",
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
            artifacts=[f"data/search_audit/{issue}/it.json"],
        )
    )

    repaired = json.loads(audit.read_text(encoding="utf-8"))
    assert result.status == "repaired"
    assert repaired["dropped"] == [
        {
            "title": "公取委、巨大IT対応で組織再編",
            "reason": "同一論点の正規記事URLを限定検索で確定できず、未解決URLを採用しなかった",
        },
        {
            "title": "東レ子会社が国内4工場のDB刷新",
            "reason": "確認できた同題材の公開記事が鮮度ゲート外のため採用しなかった",
        },
    ]


def test_search_audit_metadata_patch_syncs_selected_total_from_digest_cards(tmp_path: Path) -> None:
    """final digest で落ちた記事数を search_audit selected_total へ同じ述語で戻す。"""
    issue = "2026-07-04"
    digest = tmp_path / "digest" / "FX" / f"{issue}-FX.md"
    digest.parent.mkdir(parents=True)
    cards = []
    for idx in range(4):
        cards.append(
            f"### [{90 - idx}] FX article {idx + 1}\n\n"
            f"📅 2026-07-04 06:0{idx} · 📰 Example · 🔗 [元記事](https://example.com/2026/07/04/fx-{idx})\n\n"
            f"![thumb](https://example.com/thumb-fx-{idx}.jpg)\n\n"
            "- [[FX]] **policy** __market signal__\n\n"
            "---\n"
        )
    digest.write_text(
        "---\n"
        "title: FX\n"
        f"date: {issue}\n"
        "categoryId: fx\n"
        "---\n\n"
        + "\n".join(cards),
        encoding="utf-8",
    )
    audit = tmp_path / "data" / "search_audit" / issue / "fx.json"
    audit.parent.mkdir(parents=True)
    audit.write_text(
        json.dumps(
            {
                "date": issue,
                "category_id": "fx",
                "queries": ["q1", "q2", "q3"],
                "raw_results_total": 25,
                "candidates_total": 5,
                "selected_total": 5,
                "dropped": [
                    {
                        "title": "stale travel demand item",
                        "reason": "freshness gate: published 2026-07-02, exceeds max-source-age-days 1",
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
            artifacts=[f"data/search_audit/{issue}/fx.json"],
        )
    )

    repaired = json.loads(audit.read_text(encoding="utf-8"))
    assert result.status == "repaired"
    assert result.changed
    assert repaired["selected_total"] == 4


def test_url_quarantine_refill_reorders_stale_top_article(tmp_path: Path) -> None:
    issue = "2026-07-19"
    digest = tmp_path / "digest" / "FX" / f"{issue}-FX.md"
    digest.parent.mkdir(parents=True)
    digest.write_text(
        "---\n"
        "title: FX\n"
        f"date: {issue}\n"
        "categoryId: fx\n"
        "---\n\n"
        "### [01] stale top\n\n"
        "📅 2026-07-17 06:00 · 📰 Example · 🔗 [元記事](https://example.com/old)\n\n"
        "![thumb](https://example.com/old.jpg)\n\n"
        "- [[FX]] **old** __signal__\n\n"
        "---\n\n"
        "### [02] fresh candidate\n\n"
        "📅 2026-07-18 06:00 · 📰 Example · 🔗 [元記事](https://example.com/fresh)\n\n"
        "![thumb](https://example.com/fresh.jpg)\n\n"
        "- [[FX]] **fresh** __signal__\n\n"
        "---\n",
        encoding="utf-8",
    )
    articles = tmp_path / "data" / "articles.jsonl"
    articles.parent.mkdir(parents=True)
    articles.write_text("", encoding="utf-8")

    result = repair_with_registry(
        RepairContext(
            repo_root=tmp_path,
            issue=issue,
            handler_id="url-quarantine-refill",
            artifacts=[f"digest/FX/{issue}-FX.md"],
        )
    )

    repaired = digest.read_text(encoding="utf-8")
    assert result.status == "repaired"
    assert "stale_top_reordered" in result.message
    assert repaired.index("fresh candidate") < repaired.index("stale top")
    assert "\n---\n\n### [01] stale top" in repaired
    assert len(parse_articles(repaired)) == 2


def test_category_card_emphasis_patch_repairs_structured_lane_bullets(tmp_path: Path) -> None:
    issue = "2026-07-19"
    digest = tmp_path / "digest" / "FX" / f"{issue}-FX.md"
    digest.parent.mkdir(parents=True)
    digest.write_text(
        "---\n"
        "title: FX\n"
        f"date: {issue}\n"
        "categoryId: fx\n"
        "---\n\n"
        "### [01] structured lanes\n\n"
        "📅 2026-07-18 06:00 · 📰 Example · 🔗 [元記事](https://example.com/fresh)\n\n"
        "![thumb](https://example.com/fresh.jpg)\n\n"
        "- 【事実・概要】：骨太方針の最終案が判明し、金融政策の具体的手法は[[日銀に委ねる]]と明記。\n"
        "- 【背景・要点】：政府が財政運営と金融政策の距離を示す一文で、利上げ判断の自主性を確認する内容。\n"
        "- 【影響・展望】：市場が政府の関与後退と受け止めれば円金利の上昇圧力は和らぐ可能性がある。骨太方針の __最終文言と円相場の反応__ を追う。\n\n"
        "---\n",
        encoding="utf-8",
    )

    result = repair_with_registry(
        RepairContext(
            repo_root=tmp_path,
            issue=issue,
            handler_id="category-card-emphasis-patch",
            artifacts=[f"digest/FX/{issue}-FX.md"],
        )
    )

    repaired = digest.read_text(encoding="utf-8")
    assert result.status == "repaired"
    assert "**骨太方針" in repaired


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


def test_url_quarantine_refill_handler_uses_digest_source_url_for_missing_thumb(
    tmp_path: Path, monkeypatch
) -> None:
    import tools.repair_registry as registry

    issue = "2026-06-30"
    bad_url = "https://example.com/no-thumb-digest"
    digest = tmp_path / "digest" / "IT-Consulting" / f"{issue}-IT-Consulting.md"
    digest.parent.mkdir(parents=True)
    digest.write_text(
        "# IT\n\n"
        "### [90] no thumb digest\n\n"
        f"📅 {issue} 09:00 · 📰 Example · 🔗 [元記事]({bad_url})\n\n"
        "#cat/it-consulting #score/高\n\n"
        "![thumb](null)\n\n"
        "- [[AI]] **policy** __market signal__\n",
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def fake_refill_category(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "mode": "refilled", "removed": 1, "refilled": 1}

    monkeypatch.setattr(registry, "refill_category", fake_refill_category)

    result = repair_with_registry(
        RepairContext(
            repo_root=tmp_path,
            issue=issue,
            handler_id="url-quarantine-refill",
            artifacts=[f"digest/IT-Consulting/{issue}-IT-Consulting.md"],
        )
    )

    assert result.status == "repaired"
    assert captured["category"] == "it"
    assert captured["bad_urls"] == [bad_url]


def test_url_quarantine_refill_handler_detects_invalid_thumb_direction(
    tmp_path: Path, monkeypatch
) -> None:
    """thumb 欠落だけでなく proxy/self-reference 不正値も quarantine 対象にする。"""
    issue = "2026-07-22"
    bad_url = "https://example.com/story-with-proxy-thumb"
    digest = tmp_path / "digest" / "IT-Consulting" / f"{issue}-IT-Consulting.md"
    digest.parent.mkdir(parents=True)
    digest.write_text(
        "# IT\n\n"
        "### [90] invalid thumb\n\n"
        f"📅 {issue} 09:00 · 📰 Example · 🔗 [元記事]({bad_url})\n\n"
        "![thumb](https://lh3.googleusercontent.com/proxy-image)\n\n"
        "- [[AI]] **policy** __market signal__\n",
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def fake_refill_category(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "mode": "refilled", "removed": 1, "refilled": 1}

    monkeypatch.setattr(registry_module, "refill_category", fake_refill_category)
    result = repair_with_registry(
        RepairContext(
            repo_root=tmp_path,
            issue=issue,
            handler_id="url-quarantine-refill",
            artifacts=[f"digest/IT-Consulting/{issue}-IT-Consulting.md"],
        )
    )

    assert result.status == "repaired"
    assert captured["bad_urls"] == [bad_url]


def test_url_quarantine_refill_handler_detects_unresolved_source_url_direction(
    tmp_path: Path, monkeypatch
) -> None:
    """Google News RSS / landing URL を thumb 問題とは別方向で quarantine する。"""
    issue = "2026-07-22"
    bad_url = "https://news.google.com/rss/articles/example-id"
    digest = tmp_path / "digest" / "IT-Consulting" / f"{issue}-IT-Consulting.md"
    digest.parent.mkdir(parents=True)
    digest.write_text(
        "# IT\n\n"
        "### [90] unresolved source\n\n"
        f"📅 {issue} 09:00 · 📰 Example · 🔗 [元記事]({bad_url})\n\n"
        "![thumb](https://example.com/article.jpg)\n\n"
        "- [[AI]] **policy** __market signal__\n",
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def fake_refill_category(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "mode": "refilled", "removed": 1, "refilled": 1}

    monkeypatch.setattr(registry_module, "refill_category", fake_refill_category)
    result = repair_with_registry(
        RepairContext(
            repo_root=tmp_path,
            issue=issue,
            handler_id="url-quarantine-refill",
            artifacts=[f"digest/IT-Consulting/{issue}-IT-Consulting.md"],
        )
    )

    assert result.status == "repaired"
    assert captured["bad_urls"] == [bad_url]


def test_url_quarantine_refill_handler_consumes_liveness_bad_url_ledger(
    tmp_path: Path, monkeypatch
) -> None:
    """HTTP 404/410 は URL 文字列から再検出せず validator ledger を受け取る。"""
    issue = "2026-07-22"
    bad_url = "https://example.com/live-looking-but-404"
    articles = tmp_path / "data" / "articles.jsonl"
    ledger = tmp_path / "build" / "quarantine" / issue / "bad-urls.json"
    articles.parent.mkdir(parents=True)
    ledger.parent.mkdir(parents=True)
    articles.write_text(
        json.dumps(
            {
                "date": issue,
                "genre": "IT-Consulting",
                "title": "dead",
                "title_ja": "リンク切れ",
                "summary": "404",
                "url": bad_url,
                "thumb": "https://example.com/thumb.jpg",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    ledger.write_text(json.dumps([bad_url], ensure_ascii=False), encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_refill_category(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "mode": "refilled", "removed": 1, "refilled": 1}

    monkeypatch.setattr(registry_module, "refill_category", fake_refill_category)
    result = repair_with_registry(
        RepairContext(
            repo_root=tmp_path,
            issue=issue,
            handler_id="url-quarantine-refill",
            artifacts=[
                "data/articles.jsonl",
                f"build/quarantine/{issue}/bad-urls.json",
            ],
        )
    )

    assert result.status == "repaired"
    assert captured["bad_urls"] == [bad_url]


def test_record_thumb_patch_syncs_missing_thumb_from_digest(tmp_path: Path) -> None:
    issue = "2026-06-30"
    url = "https://example.com/story"
    thumb = "https://example.com/thumb.jpg"
    articles = tmp_path / "data" / "articles.jsonl"
    digest = tmp_path / "digest" / "IT-Consulting" / f"{issue}-IT-Consulting.md"
    articles.parent.mkdir(parents=True)
    digest.parent.mkdir(parents=True)
    articles.write_text(
        json.dumps(
            {
                "date": issue,
                "genre": "IT-Consulting",
                "title": "story",
                "title_ja": "story",
                "url": url,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    digest.write_text(
        "# IT\n\n"
        "### [90] story\n\n"
        f"📅 {issue} 09:00 · 📰 Example · 🔗 [元記事]({url})\n\n"
        f"![thumb]({thumb})\n\n"
        "- [[AI]] **policy** __market signal__\n",
        encoding="utf-8",
    )

    result = repair_with_registry(
        RepairContext(
            repo_root=tmp_path,
            issue=issue,
            handler_id="record-thumb-quarantine-patch",
            artifacts=["data/articles.jsonl", f"digest/IT-Consulting/{issue}-IT-Consulting.md"],
        )
    )

    repaired = json.loads(articles.read_text(encoding="utf-8").strip())
    assert result.status == "repaired"
    assert repaired["thumb"] == thumb


def test_record_thumb_patch_repairs_invalid_thumb_without_broad_refill(tmp_path: Path) -> None:
    """schema 不正 thumb は record scope 内でカテゴリ既定 URL へ正規化する。"""
    from tools.validate_record import validate_jsonl

    issue = "2026-07-22"
    articles = tmp_path / "data" / "articles.jsonl"
    articles.parent.mkdir(parents=True)
    articles.write_text(
        json.dumps(
            {
                "date": issue,
                "genre": "IT-Consulting",
                "title": "story",
                "title_ja": "記事",
                "url": "https://example.com/story",
                "thumb": "broken-thumb",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    assert validate_jsonl(articles, issue_date=issue)

    result = repair_with_registry(
        RepairContext(
            repo_root=tmp_path,
            issue=issue,
            handler_id="record-thumb-quarantine-patch",
            artifacts=["data/articles.jsonl"],
        )
    )

    repaired = json.loads(articles.read_text(encoding="utf-8").strip())
    assert result.status == "repaired"
    assert repaired["thumb"].startswith("https://")
    assert not validate_jsonl(articles, issue_date=issue)


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
    digest = tmp_path / "digest" / "AI" / f"{issue}-AI.md"
    records = tmp_path / "tmp" / "newsroom" / issue / "ai.records.jsonl"
    manifest = tmp_path / "build" / "reporter-artifacts" / issue / "editor-input-manifest.json"
    for path in (articles, digest, records, manifest):
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
        "thumb": "https://example.com/current.jpg",
        "score": 90,
        "tags": ["cat/ai", "score/高"],
    }
    articles.write_text(json.dumps(old, ensure_ascii=False) + "\n", encoding="utf-8")
    digest.write_text(
        "\n".join(
            [
                "---",
                f"date: {issue}",
                "category: AI",
                "---",
                "# AI",
                "",
                "### [90] current",
                "",
                f"📅 {issue} · 📰 Example · 🔗 [元記事]({current['url']})",
                "",
                "#cat/ai #score/高",
                "",
                "![thumb](https://example.com/current.jpg)",
                "",
                "- 【事実・概要】：current",
                "",
            ]
        ),
        encoding="utf-8",
    )
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
            handler_id="digest-articles-digest-only-patch",
            artifacts=[
                f"digest/AI/{issue}-AI.md",
                "data/articles.jsonl",
                f"tmp/newsroom/{issue}/ai.records.jsonl",
            ],
        )
    )

    repaired = articles.read_text(encoding="utf-8")
    assert result.status == "repaired"
    assert result.changed
    assert "appended_current_reporter_records=1" in result.message
    assert "https://example.com/old" in repaired
    assert "https://example.com/current" in repaired


def test_date_evidence_patch_restores_existing_article_from_reporter_records(tmp_path: Path) -> None:
    issue = "2026-06-28"
    articles = tmp_path / "data" / "articles.jsonl"
    records = tmp_path / "tmp" / "newsroom" / issue / "ai.records.jsonl"
    manifest = tmp_path / "build" / "reporter-artifacts" / issue / "editor-input-manifest.json"
    for path in (articles, records, manifest):
        path.parent.mkdir(parents=True, exist_ok=True)
    incomplete = {
        "date": issue,
        "genre": "AI",
        "title": "current",
        "url": "https://example.com/current",
        "source": "Example",
    }
    reporter = {
        **incomplete,
        "published_date": issue,
        "date_evidence_source": "htmldate",
        "seen_at": f"{issue}T06:00:00+09:00",
    }
    articles.write_text(json.dumps(incomplete, ensure_ascii=False) + "\n", encoding="utf-8")
    records.write_text(json.dumps(reporter, ensure_ascii=False) + "\n", encoding="utf-8")
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
            handler_id="date-evidence-source-patch",
            artifacts=["data/articles.jsonl"],
        )
    )

    repaired = json.loads(articles.read_text(encoding="utf-8").strip())
    assert result.status == "repaired"
    assert repaired["published_date"] == issue
    assert repaired["date_evidence_source"] == "htmldate"
    assert repaired["seen_at"] == f"{issue}T06:00:00+09:00"


def test_articles_only_handler_generates_digest_card_and_reconcile_turns_green(tmp_path: Path) -> None:
    """current reporter record から欠落 card を再生成し、同じ gate を Green に戻す。"""
    issue = "2026-07-27"
    digest = tmp_path / "digest" / "AI" / f"{issue}-AI.md"
    articles = tmp_path / "data" / "articles.jsonl"
    records = tmp_path / "tmp" / "newsroom" / issue / "ai.records.jsonl"
    manifest = tmp_path / "build" / "reporter-artifacts" / issue / "editor-input-manifest.json"
    for path in (digest, articles, records, manifest):
        path.parent.mkdir(parents=True, exist_ok=True)

    existing = {
        "date": issue,
        "genre": "AI",
        "title": "既存記事",
        "title_ja": "既存記事",
        "summary": "既存要約",
        "url": "https://example.com/existing",
        "source": "既存媒体",
        "published_date": issue,
        "thumb": "https://example.com/existing.jpg",
        "score": 80,
        "tags": ["cat/ai", "score/中"],
        "bullets": ["【事実・概要】：既存記事。"],
    }
    missing = {
        "date": issue,
        "genre": "AI",
        "title": "Original missing title",
        "title_ja": "欠落していた日本語タイトル",
        "summary": "fixture から自動生成する要約。",
        "url": "https://example.com/missing",
        "source": "Fixture News",
        "published_date": "2026-07-26",
        "time": "09:30",
        "thumb": "https://example.com/missing.jpg",
        "score": 95,
        "tags": ["cat/ai", "topic/repair", "score/高"],
        "bullets": [
            "【事実・概要】：欠落 card を自動生成する。",
            "【背景・要点】：reporter record を正本にする。",
            "【影響・展望】：同じ gate を再検証する。",
        ],
    }
    digest.write_text(
        "\n".join(
            [
                "---",
                f"date: {issue}",
                "category: AI",
                "---",
                "# AI",
                "",
                "### [80] 既存記事",
                "",
                f"📅 {issue} · 📰 既存媒体 · 🔗 [元記事]({existing['url']})",
                "",
                "#cat/ai #score/中",
                "",
                "![thumb](https://example.com/existing.jpg)",
                "",
                "- 【事実・概要】：既存記事。",
                "",
            ]
        ),
        encoding="utf-8",
    )
    articles.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in (existing, missing)) + "\n",
        encoding="utf-8",
    )
    records.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in (existing, missing)) + "\n",
        encoding="utf-8",
    )
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

    before = reconcile(tmp_path / "digest", articles, issue)
    assert [row["url"] for row in before["articles_only"]] == [missing["url"]]

    result = repair_with_registry(
        RepairContext(
            repo_root=tmp_path,
            issue=issue,
            handler_id="digest-card-insert-patch",
            artifacts=[
                f"digest/AI/{issue}-AI.md",
                "data/articles.jsonl",
                f"tmp/newsroom/{issue}/ai.records.jsonl",
            ],
        )
    )

    repaired = digest.read_text(encoding="utf-8")
    assert result.status == "repaired"
    assert result.changed
    assert repaired.index("### [95] 欠落していた日本語タイトル") < repaired.index("### [80] 既存記事")
    assert "Original missing title" not in repaired
    assert "📅 2026-07-26 09:30 · 📰 Fixture News" in repaired
    assert "#cat/ai #topic/repair #score/高" in repaired
    assert "![thumb](https://example.com/missing.jpg)" in repaired
    assert "fixture から自動生成する要約。" in repaired or "欠落 card を自動生成する。" in repaired
    assert reconcile(tmp_path / "digest", articles, issue) == {
        "digest_only": [],
        "articles_only": [],
    }


def test_articles_only_handler_rejects_manifest_path_outside_repo_before_mutation(
    tmp_path: Path,
) -> None:
    """改ざん manifest は repo 外 record を読み込まず、digest を変更しない。"""
    issue = "2026-07-27"
    digest = tmp_path / "digest" / "AI" / f"{issue}-AI.md"
    articles = tmp_path / "data" / "articles.jsonl"
    manifest = (
        tmp_path
        / "build"
        / "reporter-artifacts"
        / issue
        / "editor-input-manifest.json"
    )
    outside = tmp_path.parent / f"{tmp_path.name}-outside.records.jsonl"
    for path in (digest, articles, manifest, outside):
        path.parent.mkdir(parents=True, exist_ok=True)

    record = {
        "date": issue,
        "genre": "AI",
        "title": "Outside record",
        "title_ja": "repo 外 record",
        "summary": "manifest traversal を拒否する。",
        "url": "https://example.com/outside-record",
        "source": "Fixture News",
        "published_date": issue,
        "thumb": "https://example.com/outside-record.jpg",
        "score": 95,
        "tags": ["cat/ai", "topic/security"],
    }
    digest.write_text(
        f"---\ndate: {issue}\ncategory: AI\n---\n# AI\n",
        encoding="utf-8",
    )
    articles.write_text(
        json.dumps(record, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    outside.write_text(
        json.dumps(record, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    manifest.write_text(
        json.dumps(
            {
                "date": issue,
                "scheduled_categories": ["ai"],
                "reporter_artifacts": [outside.as_posix()],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    before = digest.read_bytes()

    result = repair_with_registry(
        RepairContext(
            repo_root=tmp_path,
            issue=issue,
            handler_id="digest-card-insert-patch",
            artifacts=[
                f"digest/AI/{issue}-AI.md",
                "data/articles.jsonl",
                f"build/reporter-artifacts/{issue}/editor-input-manifest.json",
            ],
        )
    )

    assert result.status == "blocked_articles_only_record_incomplete"
    assert not result.changed
    assert "outside allowed reporter scope" in result.message
    assert digest.read_bytes() == before


def test_articles_only_handler_uses_current_articles_when_manifest_is_absent(
    tmp_path: Path,
) -> None:
    issue = "2026-07-27"
    digest = tmp_path / "digest" / "AI" / f"{issue}-AI.md"
    articles = tmp_path / "data" / "articles.jsonl"
    digest.parent.mkdir(parents=True, exist_ok=True)
    articles.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "date": issue,
        "genre": "AI",
        "title": "Articles fallback",
        "title_ja": "current articles から復元",
        "summary": "manifest が無い経路では current articles record を使う。",
        "url": "https://example.com/articles-fallback",
        "source": "Fixture News",
        "published_date": issue,
        "thumb": "https://example.com/articles-fallback.jpg",
        "score": 91,
        "tags": ["cat/ai", "topic/fallback"],
    }
    digest.write_text(
        f"---\ndate: {issue}\ncategory: AI\n---\n# AI\n",
        encoding="utf-8",
    )
    articles.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")

    result = repair_with_registry(
        RepairContext(
            repo_root=tmp_path,
            issue=issue,
            handler_id="digest-card-insert-patch",
            artifacts=[f"digest/AI/{issue}-AI.md", "data/articles.jsonl"],
        )
    )

    assert result.status == "repaired"
    assert "### [91] current articles から復元" in digest.read_text(encoding="utf-8")
    assert reconcile(tmp_path / "digest", articles, issue) == {
        "digest_only": [],
        "articles_only": [],
    }


def test_digest_card_insert_repairs_empty_category_digest_from_current_reporter_record(
    tmp_path: Path,
) -> None:
    issue = "2026-07-29"
    digest = tmp_path / "digest" / "IT-Consulting" / f"{issue}-IT-Consulting.md"
    records = tmp_path / "tmp" / "newsroom" / issue / "it.records.jsonl"
    manifest = tmp_path / "build" / "reporter-artifacts" / issue / "editor-input-manifest.json"
    articles = tmp_path / "data" / "articles.jsonl"
    for path in (digest, records, manifest, articles):
        path.parent.mkdir(parents=True, exist_ok=True)

    record = {
        "date": issue,
        "genre": "IT-Consulting",
        "title": "GMO Cybersecurity launches AI defense base",
        "title_ja": "GMO、AI防御基盤を構築",
        "summary": "record には採用記事があるが、digest card だけが欠落している。",
        "url": "https://example.com/gmo-ai-defense",
        "source": "GMOサイバーセキュリティ",
        "published_date": "2026-07-27",
        "time": "00:00",
        "thumb": "https://example.com/gmo-ai-defense.jpg",
        "score": 92,
        "tags": ["cat/it", "topic/AIセキュリティ", "score/高"],
        "bullets": [
            "【事実・概要】：GMO が AI 防御基盤を構築した。",
            "【背景・要点】：record を正本にして空 digest を決定的に修復する。",
        ],
    }
    digest.write_text(
        "\n".join(
            [
                "---",
                f"date: {issue}",
                "category: IT-Consulting",
                "categoryId: it",
                "---",
                "# IT",
                "",
                "> summary",
                "",
                f"__**← [[2026-07-28|前号]] | [[2026-07-30|翌号]] →**__",
                "",
            ]
        ),
        encoding="utf-8",
    )
    records.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    articles.write_text("", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "date": issue,
                "scheduled_categories": ["it"],
                "reporter_artifacts": [f"tmp/newsroom/{issue}/it.records.jsonl"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = repair_with_registry(
        RepairContext(
            repo_root=tmp_path,
            issue=issue,
            handler_id="digest-card-insert-patch",
            artifacts=[
                f"digest/IT-Consulting/{issue}-IT-Consulting.md",
                f"tmp/newsroom/{issue}/it.records.jsonl",
                f"build/reporter-artifacts/{issue}/editor-input-manifest.json",
            ],
        )
    )

    repaired = digest.read_text(encoding="utf-8")
    assert result.status == "repaired"
    assert "### [92] GMO、AI防御基盤を構築" in repaired
    assert "https://example.com/gmo-ai-defense" in repaired
    assert f"[[2026-07-30|翌号]]" in repaired


def test_digest_only_handler_removes_stale_card_with_authoritative_manifest(tmp_path: Path) -> None:
    """current manifest 外の同日旧 run card は stale と確定して除去する。"""
    issue = "2026-07-27"
    digest = tmp_path / "digest" / "AI" / f"{issue}-AI.md"
    articles = tmp_path / "data" / "articles.jsonl"
    records = tmp_path / "tmp" / "newsroom" / issue / "ai.records.jsonl"
    manifest = tmp_path / "build" / "reporter-artifacts" / issue / "editor-input-manifest.json"
    for path in (digest, articles, records, manifest):
        path.parent.mkdir(parents=True, exist_ok=True)

    current = {
        "date": issue,
        "genre": "AI",
        "title": "current",
        "url": "https://example.com/current",
    }
    stale = {
        "date": issue,
        "genre": "AI",
        "title": "stale",
        "url": "https://example.com/stale",
    }
    digest.write_text(
        "\n".join(
            [
                "---",
                f"date: {issue}",
                "category: AI",
                "---",
                "# AI",
                "",
                "### [90] current",
                "",
                f"📅 {issue} · 📰 Example · 🔗 [元記事]({current['url']})",
                "",
                "- current",
                "",
                "---",
                "",
                "### [70] stale",
                "",
                f"📅 {issue} · 📰 Example · 🔗 [元記事]({stale['url']})",
                "",
                "- stale",
                "",
            ]
        ),
        encoding="utf-8",
    )
    articles.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in (current, stale)) + "\n",
        encoding="utf-8",
    )
    records.write_text(json.dumps(current, ensure_ascii=False) + "\n", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "date": issue,
                "scheduled_categories": ["ai"],
                "reporter_artifacts": [f"tmp/newsroom/{issue}/ai.records.jsonl"],
            }
        ),
        encoding="utf-8",
    )

    result = repair_with_registry(
        RepairContext(
            repo_root=tmp_path,
            issue=issue,
            handler_id="digest-articles-digest-only-patch",
            artifacts=[
                f"digest/AI/{issue}-AI.md",
                "data/articles.jsonl",
                f"tmp/newsroom/{issue}/ai.records.jsonl",
            ],
        )
    )

    repaired = digest.read_text(encoding="utf-8")
    assert result.status == "repaired"
    assert "removed_stale_digest_cards=1" in result.message
    assert current["url"] in repaired
    assert stale["url"] not in repaired
    assert reconcile(tmp_path / "digest", articles, issue) == {
        "digest_only": [],
        "articles_only": [],
    }


def test_digest_only_handler_returns_typed_red_without_current_manifest(tmp_path: Path) -> None:
    """append 漏れと旧 card を判別できない場合は成功扱いしない。"""
    issue = "2026-07-27"
    digest = tmp_path / "digest" / "AI" / f"{issue}-AI.md"
    articles = tmp_path / "data" / "articles.jsonl"
    digest.parent.mkdir(parents=True, exist_ok=True)
    articles.parent.mkdir(parents=True, exist_ok=True)
    digest.write_text(
        "\n".join(
            [
                "---",
                f"date: {issue}",
                "category: AI",
                "---",
                "# AI",
                "",
                "### [70] ambiguous",
                "",
                f"📅 {issue} · 📰 Example · 🔗 [元記事](https://example.com/ambiguous)",
                "",
                "- ambiguous",
                "",
            ]
        ),
        encoding="utf-8",
    )
    articles.write_text("", encoding="utf-8")

    result = repair_with_registry(
        RepairContext(
            repo_root=tmp_path,
            issue=issue,
            handler_id="digest-articles-digest-only-patch",
            artifacts=[f"digest/AI/{issue}-AI.md", "data/articles.jsonl"],
        )
    )

    assert result.status == "blocked_digest_only_ambiguous"
    assert not result.changed
    assert "current reporter manifest" in result.message


def test_digest_only_handler_validates_all_targets_before_articles_append(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """後続 target が不正なら、先行 articles append も行わない。"""
    issue = "2026-07-27"
    articles = tmp_path / "data" / "articles.jsonl"
    records = tmp_path / "tmp" / "newsroom" / issue / "ai.records.jsonl"
    manifest = (
        tmp_path
        / "build"
        / "reporter-artifacts"
        / issue
        / "editor-input-manifest.json"
    )
    for path in (articles, records, manifest):
        path.parent.mkdir(parents=True, exist_ok=True)
    articles.write_text("", encoding="utf-8")
    current = {
        "date": issue,
        "genre": "AI",
        "title": "Current",
        "title_ja": "Current",
        "summary": "Current",
        "url": "https://example.com/current",
        "source": "Example",
        "published": issue,
    }
    records.write_text(
        json.dumps(current, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    manifest.write_text(
        json.dumps(
            {
                "date": issue,
                "scheduled_categories": ["ai"],
                "reporter_artifacts": [
                    f"tmp/newsroom/{issue}/ai.records.jsonl"
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        registry_module,
        "reconcile",
        lambda *_args, **_kwargs: {
            "articles_only": [],
            "digest_only": [
                {
                    "url": current["url"],
                    "evidence": {
                        "target_digest_path": f"digest/AI/{issue}-AI.md"
                    },
                },
                {
                    "url": "https://example.com/stale",
                    "evidence": {
                        "target_digest_path": f"digest/AI/{issue}-missing.md"
                    },
                },
            ],
        },
    )
    before = articles.read_bytes()

    result = repair_with_registry(
        RepairContext(
            repo_root=tmp_path,
            issue=issue,
            handler_id="digest-articles-digest-only-patch",
            artifacts=[
                "data/articles.jsonl",
                f"tmp/newsroom/{issue}/ai.records.jsonl",
                f"build/reporter-artifacts/{issue}/editor-input-manifest.json",
            ],
        )
    )

    assert result.status == "blocked_digest_only_ambiguous"
    assert not result.changed
    assert articles.read_bytes() == before


def test_unrouted_legacy_handlers_are_not_registered() -> None:
    assert find_handler("audio-script-length-patch") is None
    assert find_handler("digest-record-sync-patch") is None
