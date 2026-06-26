from __future__ import annotations

import json
from datetime import date

from tools.publish_inventory import (
    required_digest_artifacts,
    required_distribution_artifacts,
    required_generated_artifacts,
    required_published_artifacts,
    required_published_docs_artifacts,
    required_published_repair_artifacts,
    scheduled_category_ids,
)
from tools.publish_inventory import main as publish_inventory_main


ISSUE = date(2026, 6, 16)


def test_manifest_lists_scheduled_digest_artifacts_for_issue_date() -> None:
    assert scheduled_category_ids(ISSUE) == [
        "fx",
        "ai",
        "it",
        "mobility",
        "manufacturing",
        "economy",
        "game",
    ]
    assert required_digest_artifacts(ISSUE) == [
        "digest/AI/2026-06-16-AI.md",
        "digest/Economy/2026-06-16-Economy.md",
        "digest/FX/2026-06-16-FX.md",
        "digest/Game/2026-06-16-Game.md",
        "digest/IT-Consulting/2026-06-16-IT-Consulting.md",
        "digest/Manufacturing/2026-06-16-Manufacturing.md",
        "digest/Mobility/2026-06-16-Mobility.md",
        "digest/Summary/2026-06-16.md",
        "data/articles.jsonl",
    ]


def test_manifest_lists_published_docs_and_deepdive_artifacts() -> None:
    artifacts = required_published_artifacts(ISSUE)

    assert "docs/2026-06-16/index.html" in artifacts
    assert "docs/2026-06-16/summary/index.html" in artifacts
    assert "docs/fx/2026-06-16/index.html" in artifacts
    assert "docs/it/2026-06-16/index.html" in artifacts
    assert "docs/game/2026-06-16/index.html" in artifacts
    assert "digest/DeepDive/2026-06-16-DeepDive.md" in artifacts
    assert "docs/deepdive/2026-06-16/index.html" in artifacts
    assert "build/tts/latest_audio.json" in artifacts


def test_published_repair_manifest_includes_validation_inputs() -> None:
    """公開必須 gate の repair scope は出力 HTML だけでなく検証入力も含む。"""
    artifacts = required_published_repair_artifacts(ISSUE)

    assert "docs/2026-06-16/index.html" in artifacts
    assert "docs/deepdive/2026-06-16/index.html" in artifacts
    assert "digest/DeepDive/2026-06-16-DeepDive.md" in artifacts
    assert "data/articles.jsonl" in artifacts
    assert "data/search_audit/2026-06-16" in artifacts
    assert "digest/Game/2026-06-16-Game.md" in artifacts


def test_generated_manifest_contains_required_generation_artifacts() -> None:
    artifacts = required_generated_artifacts(ISSUE)

    assert artifacts == [
        "digest/AI/2026-06-16-AI.md",
        "digest/Economy/2026-06-16-Economy.md",
        "digest/FX/2026-06-16-FX.md",
        "digest/Game/2026-06-16-Game.md",
        "digest/IT-Consulting/2026-06-16-IT-Consulting.md",
        "digest/Manufacturing/2026-06-16-Manufacturing.md",
        "digest/Mobility/2026-06-16-Mobility.md",
        "digest/Summary/2026-06-16.md",
        "digest/Summary/2026-06-16-audio-script.md",
        "digest/DeepDive/2026-06-16-DeepDive.md",
        "data/articles.jsonl",
        "data/_status.md",
        "data/search_audit/2026-06-16",
    ]


def test_weekend_manifest_omits_unscheduled_categories() -> None:
    saturday = date(2026, 6, 20)
    artifacts = required_published_artifacts(saturday)

    assert "manufacturing" not in scheduled_category_ids(saturday)
    assert "economy" not in scheduled_category_ids(saturday)
    assert "docs/manufacturing/2026-06-20/index.html" not in artifacts
    assert "docs/economy/2026-06-20/index.html" not in artifacts


def test_generated_manifest_respects_weekend_schedule() -> None:
    saturday = date(2026, 6, 20)
    artifacts = required_generated_artifacts(saturday)

    assert "manufacturing" not in scheduled_category_ids(saturday)
    assert "economy" not in scheduled_category_ids(saturday)
    assert "digest/Manufacturing/2026-06-20-Manufacturing.md" not in artifacts
    assert "digest/Economy/2026-06-20-Economy.md" not in artifacts
    assert "digest/Game/2026-06-20-Game.md" in artifacts
    assert "digest/DeepDive/2026-06-20-DeepDive.md" in artifacts


def test_required_inventory_covers_every_weekday_schedule_pattern() -> None:
    """曜日別成果物セットを全曜日で固定し、未対象カテゴリ混入を防ぐ。"""
    cases = {
        date(2026, 6, 22): ["fx", "ai", "it", "mobility", "manufacturing", "economy"],
        date(2026, 6, 23): ["fx", "ai", "it", "mobility", "manufacturing", "economy", "game"],
        date(2026, 6, 24): ["fx", "ai", "it", "mobility", "manufacturing", "economy"],
        date(2026, 6, 25): ["fx", "ai", "it", "mobility", "manufacturing", "economy", "game"],
        date(2026, 6, 26): ["fx", "ai", "it", "mobility", "manufacturing", "economy"],
        date(2026, 6, 27): ["fx", "ai", "it", "mobility", "game"],
        date(2026, 6, 28): ["fx", "ai", "it", "mobility", "game"],
    }

    for issue, expected_categories in cases.items():
        issue_str = issue.isoformat()
        scheduled = scheduled_category_ids(issue)
        digest = required_digest_artifacts(issue)
        generated = required_generated_artifacts(issue)
        published_docs = required_published_docs_artifacts(issue)
        published = required_published_artifacts(issue)
        published_repair = required_published_repair_artifacts(issue)
        distribution = required_distribution_artifacts(issue)

        assert scheduled == expected_categories, issue_str
        assert len(digest) == len(scheduled) + 2, issue_str
        assert len(generated) == len(scheduled) + 6, issue_str
        assert len(published_docs) == len(scheduled) + 2, issue_str
        assert len(published) == len(scheduled) + 5, issue_str
        assert len(published_repair) == (len(scheduled) * 2) + 15, issue_str
        assert len(distribution) == 7, issue_str
        assert f"data/distribution/{issue_str}.json" in distribution

        for cat_id in expected_categories:
            assert f"docs/{cat_id}/{issue_str}/index.html" in published_docs
        for cat_id in {"economy", "game", "manufacturing"} - set(expected_categories):
            assert f"docs/{cat_id}/{issue_str}/index.html" not in published_docs


def test_distribution_manifest_includes_audio_and_podcast_state() -> None:
    artifacts = required_distribution_artifacts(ISSUE)

    assert "build/tts/latest_audio.json" in artifacts
    assert "build/youtube-podcast/2026-06-16.mp4" in artifacts
    assert "build/youtube-podcast/uploads.json" in artifacts
    assert "build/tts/deepdive/latest_audio.json" in artifacts
    assert "build/youtube-podcast-deepdive/2026-06-16.mp4" in artifacts
    assert "build/youtube-podcast-deepdive/uploads.json" in artifacts
    assert "data/distribution/2026-06-16.json" in artifacts


def test_categories_manifest_exposes_issue_schedule(capsys) -> None:
    rc = publish_inventory_main(["--date", "2026-06-24", "--kind", "categories", "--json"])

    assert rc == 0
    assert json.loads(capsys.readouterr().out) == [
        "fx",
        "ai",
        "it",
        "mobility",
        "manufacturing",
        "economy",
    ]


def test_weekend_categories_manifest_excludes_manufacturing_and_economy(capsys) -> None:
    rc = publish_inventory_main(["--date", "2026-06-20", "--kind", "categories", "--json"])

    assert rc == 0
    assert json.loads(capsys.readouterr().out) == [
        "fx",
        "ai",
        "it",
        "mobility",
        "game",
    ]
