#!/usr/bin/env python3
"""direct 本線移行後の旧 runner tombstone 契約テスト。"""
from __future__ import annotations

import importlib
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
AUTOMATION_TEMPLATE = ROOT / "automation" / "news-grasp-6-40" / "automation.toml.template"
DIRECT_SKILL = ROOT / "automation" / "skills" / "news-grasp-direct-mainline" / "SKILL.md"
BACKFILL_MOBILITY = ROOT / "build" / "run_backfill_mobility.ps1"


def test_legacy_runner_entrypoints_remain_tombstoned() -> None:
    """旧 runner 実体を復活させず、direct runtime を正規入口にする。"""

    forbidden = [
        ROOT / "scripts" / "ops" / "news-grasp-runner.ps1",
        Path.home() / "bin" / "news-grasp-runner.ps1",
    ]

    assert not [str(path) for path in forbidden if path.exists()]
    assert (ROOT / "tools" / "news_grasp_direct_runtime.py").is_file()
    assert (ROOT / "tools" / "news_grasp_direct_completion.py").is_file()


def test_daily_automation_points_to_direct_mainline_without_runner_command() -> None:
    """06:00 automation prompt は direct skill/runtime を使い、runner実行へ戻らない。"""

    text = AUTOMATION_TEMPLATE.read_text(encoding="utf-8-sig")

    assert "$news-grasp-direct-mainline" in text
    assert text.count("Python312\\\\python.exe -m tools.news_grasp_direct_runtime daily") == 1
    for operation in (
        "static_check",
        "scoped_contract_unit",
        "current_issue_integration",
        "external_publication",
        "consumer_public_verification",
        "atomic_completion",
    ):
        assert operation in text
        assert f"tools.news_grasp_daily_gate {operation}" not in text
    assert "python -m tools.news_grasp_direct_runtime start" not in text
    assert "direct completion guard" in text
    assert "news-grasp-runner.ps1" not in text
    assert "news_grasp_runner.py" not in text
    assert "NoPublish、URL 200 単独" in text


def test_direct_mainline_stage_order_covers_required_public_surfaces() -> None:
    """direct runtime の 21工程が公開critical pathを順序付きで保持する。"""

    api = importlib.import_module("tools.news_grasp_direct_runtime")

    assert api.DIRECT_STAGES == (
        "title_control",
        "issue_inventory",
        "category_collection",
        "evidence_dedup_freshness",
        "category_digest",
        "reporter_validation",
        "articles_jsonl",
        "summary",
        "daily_audio",
        "deepdive_article",
        "deepdive_quality",
        "html_docs",
        "daily_quality",
        "youtube_podcasts",
        "playlist",
        "notification",
        "distribution",
        "publish_status",
        "commit_push",
        "pages_verify",
        "public_completion",
    )
    assert set(api.PUBLIC_SURFACES) >= {
        "web",
        "daily_audio",
        "deepdive_article",
        "deepdive_audio",
        "youtube_daily",
        "youtube_deepdive",
        "playlist",
        "notification",
        "distribution",
        "publish_status",
        "remote_commit",
        "pages",
    }


def test_direct_skill_documents_quality_and_recovery_boundaries() -> None:
    """skill本文は title / quality / public-only completion / exact successor を要求する。"""

    text = DIRECT_SKILL.read_text(encoding="utf-8-sig")

    for marker in [
        "Daily 六phase",
        "static_check",
        "current_issue_integration",
        "consumer_public_verification",
        "atomic_completion",
        "frontmatter付きMarkdown",
        "current issue",
        "unknown_unobtainable",
        "callerの`ok=true`だけではrunをcompletedにしない",
        "public incompleteかつexact successorがある状態で終了しない",
    ]:
        assert marker in text
    assert "runner state、readiness、durable goal、URL 200単独" in text


def test_mobility_backfill_does_not_keep_claude_print_path() -> None:
    """完了済み一時backfillからClaude CLI課金経路を残さない。"""

    if not BACKFILL_MOBILITY.exists():
        return

    script = BACKFILL_MOBILITY.read_text(encoding="utf-8-sig").lower()

    assert "claude --print" not in script
    assert "claude -p" not in script
