from __future__ import annotations

from tools.run_newsroom_append_safety_benchmark import SCENARIOS, score_output


def test_append_safety_fixture_covers_five_failure_boundaries() -> None:
    assert len(SCENARIOS) == 5
    assert {scenario["expected"]["action"] for scenario in SCENARIOS} == {"append", "abort"}
    assert all(scenario["expected"]["preserve_existing"] is True for scenario in SCENARIOS)


def test_append_safety_score_requires_exact_action_ids_count_and_preservation() -> None:
    perfect = {
        "model": "candidate",
        "decisions": [
            {
                "scenario_id": scenario["scenario_id"],
                **scenario["expected"],
                "rationale": "境界条件に従う。",
            }
            for scenario in SCENARIOS
        ],
    }
    score = score_output(perfect)
    assert score["passed"] is True
    assert score["passed_scenarios"] == 5
    assert score["fatal_issues"] == []

    broken = {**perfect, "decisions": [dict(item) for item in perfect["decisions"]]}
    broken["decisions"][0]["append_ids"] = ["wrong-id"]
    score = score_output(broken)
    assert score["passed"] is False
    assert score["passed_scenarios"] == 4
    assert any("append_ids" in issue for issue in score["fatal_issues"])
