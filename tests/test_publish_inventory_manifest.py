from __future__ import annotations

from datetime import date

from tools.publish_inventory import (
    required_digest_artifacts,
    required_generated_artifacts,
    required_published_artifacts,
    scheduled_category_ids,
)


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
