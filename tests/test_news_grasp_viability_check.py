from __future__ import annotations

from pathlib import Path

from tools import news_grasp_viability_check as viability


ROOT = Path(__file__).resolve().parents[1]


def test_viability_check_has_closed_condition_set() -> None:
    assert viability.CONDITION_IDS == (
        "entry_control_plane",
        "input_inventory",
        "model_route_authority",
        "artifact_generation_contract",
        "quality_repair_routing",
        "dry_public_boundary",
        "production_completion_authority",
        "bounded_slo_control",
        "post_publish_issue_boundary",
        "external_dependency_boundary",
    )


def test_viability_check_returns_green_for_repo_local_contracts() -> None:
    result = viability.evaluate(
        repo_root=ROOT,
        issue_date="2026-08-29",
        installed_automation=None,
        check_live_tasks=False,
        run_red_suite_coverage=False,
    )

    assert result["schemaVersion"] == "NEWS_GRASP_COMPLETION_VIABILITY_V1"
    assert result["viability"] == "viability_green"
    assert [row["conditionId"] for row in result["rows"]] == list(
        viability.CONDITION_IDS
    )
    assert {row["status"] for row in result["rows"]} == {"green"}


def test_viability_check_is_bound_to_direct_assets_not_legacy_runner() -> None:
    source = (ROOT / "tools" / "news_grasp_viability_check.py").read_text(encoding="utf-8")
    assert "news-grasp-direct-mainline" in source
    assert "tools/news_grasp_completion_guard.py" in source
    assert 'runner = _read(' not in source


def test_viability_check_demotes_live_duplicate_scheduler_to_entry_red(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        viability,
        "_live_task_conflicts",
        lambda: ("red", ["duplicate scheduler enabled: News-Grasp Production"]),
    )

    result = viability.evaluate(
        repo_root=ROOT,
        issue_date="2026-08-29",
        installed_automation=None,
        check_live_tasks=True,
        run_red_suite_coverage=False,
    )

    entry = result["rows"][0]
    assert result["viability"] == "viability_red"
    assert entry["conditionId"] == "entry_control_plane"
    assert entry["status"] == "red"
    assert entry["failureDestination"] == "fix_now"
    assert entry["reason"] == "duplicate scheduled task conflict"


def test_viability_check_demotes_red_suite_failure_to_quality_red(monkeypatch) -> None:
    monkeypatch.setattr(
        viability,
        "_run_json",
        lambda *_args, **_kwargs: (
            1,
            {"status": "Red", "findings": [{"code": "x"}]},
            '{"status":"Red"}',
        ),
    )

    result = viability.evaluate(
        repo_root=ROOT,
        issue_date="2026-08-29",
        installed_automation=None,
        check_live_tasks=False,
        run_red_suite_coverage=True,
    )

    row = next(
        item for item in result["rows"] if item["conditionId"] == "quality_repair_routing"
    )
    assert result["viability"] == "viability_red"
    assert row["status"] == "red"
    assert row["failureDestination"] == "fix_now"
