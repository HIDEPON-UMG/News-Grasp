from __future__ import annotations

import json
from pathlib import Path

from tools.deepdive_red_suite_coverage import (
    build_requirement_viewpoint_pair_cases,
)
from tools.red_suite_execution import _Recorder, _fixture_selectors


ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "fixtures" / "deepdive_quality" / "tdd_acceptance_matrix.json"


def test_execution_producer_has_exact_selector_and_pair_case_sets() -> None:
    value = json.loads(MATRIX.read_text(encoding="utf-8"))
    selectors = _fixture_selectors(value["redSuiteCoverage"])
    pair_cases = build_requirement_viewpoint_pair_cases(value)
    assert len(selectors) == len(set(selectors)) == 60
    assert len(pair_cases) == 150
    assert len({case["caseId"] for case in pair_cases}) == 150


def test_pair_cases_are_traceability_red_not_behavior_substitutes() -> None:
    value = json.loads(MATRIX.read_text(encoding="utf-8"))
    pair_cases = build_requirement_viewpoint_pair_cases(value)
    assert len(pair_cases) == 150
    assert all(len(case["injectedDefects"]) == 2 for case in pair_cases)
    assert all(len(case["expectedFindings"]) == 2 for case in pair_cases)
    assert len(_fixture_selectors(value["redSuiteCoverage"])) == 60


def test_pytest_execution_recorder_is_hashable_for_plugin_registration() -> None:
    recorder = _Recorder()
    assert isinstance(hash(recorder), int)


def test_execution_recorder_keeps_collection_and_call_outcomes_separate() -> None:
    recorder = _Recorder()

    class Item:
        nodeid = "tests/test_sample.py::test_sample"

    class Report:
        nodeid = Item.nodeid
        when = "call"
        outcome = "passed"
        failed = False

    recorder.pytest_collection_modifyitems(None, None, [Item()])
    recorder.pytest_runtest_logreport(Report())
    assert recorder.collected == [Item.nodeid]
    assert recorder.outcomes == {Item.nodeid: "passed"}
    assert recorder.collection_errors == []
    assert recorder.failures == []
