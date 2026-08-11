from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.historical_failure_scenarios import historical_failure_scenarios

ROOT = Path(__file__).resolve().parents[1]
CORPUS = json.loads((ROOT / "tests/fixtures/constitutional-operations/compound-v1.json").read_text(encoding="utf-8"))
ACTUAL = historical_failure_scenarios()


def _run_compound(row: dict) -> None:
    matches = [item for item in ACTUAL if getattr(item, "compound_id", None) == row["fixtureId"]]
    if not matches:
        pytest.fail(f"NGC_RED_COMPOUND_{row['fixtureId']}_SCENARIO_MISSING")
    if getattr(matches[0], "finite_terminal", None) is not True:
        pytest.fail(f"NGC_RED_COMPOUND_{row['fixtureId']}_FINITE_TERMINAL_MISSING")


for _row in CORPUS["rows"]:
    _name = "test_compound_" + _row["fixtureId"].replace("-", "_")

    def _test(row=_row):
        _run_compound(row)

    _test.__name__ = _name
    globals()[_name] = _test
