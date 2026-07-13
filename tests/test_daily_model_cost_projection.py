import json
from pathlib import Path

import pytest

from tools.project_daily_model_costs import project_records


def test_category_projection_allocates_shared_cost_and_reconciles_total() -> None:
    records = [
        {"flow": "reporter:a", "model": "gpt-5.4", "tokens_used": 100, "exit_code": 0},
        {"flow": "reporter:b", "model": "gpt-5.4", "tokens_used": 300, "exit_code": 0},
        {"flow": "newsroom_editor", "model": "gpt-5.4", "tokens_used": 50, "exit_code": 0},
        {"flow": "deepdive", "model": "gpt-5.5", "tokens_used": 20, "exit_code": 0},
    ]
    calibration = {
        "reporter": {"current_unit": 1.0, "candidate_multiplier": 1.0},
        "newsroom_editor": {"current_unit": 2.0, "candidate_multiplier": 1.1},
        "deepdive": {"current_unit": 3.0, "candidate_multiplier": 2.0},
        "repair": {"current_unit": 2.0, "candidate_multiplier": 1.0},
        "style_editor": {"current_unit": 0.5, "candidate_multiplier": 1.5},
    }

    report = project_records(records, calibration)

    assert report["overall"]["current_usd"] == 560.0
    assert report["overall"]["candidate_usd"] == 630.0
    assert sum(row["current_usd"] for row in report["categories"]) == report["overall"]["current_usd"]
    assert sum(row["candidate_usd"] for row in report["categories"]) == report["overall"]["candidate_usd"]
    assert report["categories"][0]["direct_candidate_usd"] == report["categories"][0]["direct_current_usd"]


@pytest.mark.skipif(
    not Path("build/model-eval-5.6/daily-cost-projection.json").exists(),
    reason="model evaluation artifact is generated only by the dedicated benchmark workflow",
)
def test_two_successful_day_projection_reconciles_roles_and_categories() -> None:
    payload = json.loads(Path("build/model-eval-5.6/daily-cost-projection.json").read_text(encoding="utf-8"))
    overall = payload["aggregate"]["overall"]
    assert [row["date"] for row in payload["completion_evidence"]] == ["2026-07-09", "2026-07-10"]
    assert all(row["publish_complete"] for row in payload["completion_evidence"])
    assert overall["tokens"] == 4_424_487
    assert abs(sum(row["current_usd"] for row in payload["aggregate"]["categories"]) - overall["current_usd"]) < 1e-9
    assert abs(sum(row["candidate_usd"] for row in payload["aggregate"]["categories"]) - overall["candidate_usd"]) < 1e-9
    assert abs(sum(row["delta_usd"] for row in payload["aggregate"]["roles"]) - overall["delta_usd"]) < 1e-9
