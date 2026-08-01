from __future__ import annotations


ROUTES = ["nopublish", "runner-sequential", "runner-parallel", "deepdive"]


def test_budget_zero_launches_no_fake_process(tmp_path) -> None:
    from tools.harness.model_spawn_broker import simulate_production_routes

    result = simulate_production_routes(ROUTES, budget=0, record_root=tmp_path)
    assert result["realModelLaunches"] == 0
    assert result["fakeModelLaunches"] == 0
    assert result["productionHashesMatched"] is True


def test_budget_one_launches_fake_exactly_once_per_route(tmp_path) -> None:
    from tools.harness.model_spawn_broker import simulate_production_routes

    for route in ROUTES:
        result = simulate_production_routes([route], budget=1, record_root=tmp_path / route)
        assert result["realModelLaunches"] == 0
        assert result["fakeModelLaunches"] == 1
        assert result["reservations"] == 1
        assert result["productionHashesMatched"] is True
