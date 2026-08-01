from __future__ import annotations

import copy
import inspect
import json
from pathlib import Path

from tools import deepdive_red_suite_coverage
from tools.deepdive_red_suite_coverage import validate_red_suite_coverage


ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "fixtures" / "deepdive_quality" / "tdd_acceptance_matrix.json"


def _value() -> dict:
    return json.loads(MATRIX.read_text(encoding="utf-8"))


def _codes(report: dict) -> set[str]:
    return {finding["code"] for finding in report["findings"]}


def test_complete_red_suite_has_ninety_composite_coverage_cells() -> None:
    report = validate_red_suite_coverage(_value(), root=ROOT)
    assert report["status"] == "Green", report
    assert report["requirementCount"] == 3
    assert report["viewpointCount"] == 10
    assert report["coverageCellCount"] == 90


def test_missing_viewpoint_is_rejected_as_requirement_definition_gap() -> None:
    value = _value()
    value["redSuiteCoverage"]["requirements"][1]["perspectives"].pop()
    report = validate_red_suite_coverage(value, root=ROOT)
    assert "missing_viewpoints" in _codes(report)
    assert "coverage_cell_count_mismatch" in _codes(report)


def test_one_fixture_cannot_substitute_for_all_red_viewpoints() -> None:
    value = _value()
    perspectives = value["redSuiteCoverage"]["requirements"][1]["perspectives"]
    fixture = perspectives[0]["fixture"]
    for perspective in perspectives:
        perspective["fixture"] = fixture
    report = validate_red_suite_coverage(value, root=ROOT)
    assert "duplicate_perspective_fixture" in _codes(report)
    assert "single_red_implementation" in _codes(report)


def test_route_omission_cannot_hide_behind_shared_engine_claim() -> None:
    value = _value()
    value["redSuiteCoverage"]["requirements"][2]["routeIds"].remove(
        "codex_daily_audit"
    )
    report = validate_red_suite_coverage(value, root=ROOT)
    assert "requirement_route_coverage_mismatch" in _codes(report)
    assert "coverage_cell_count_mismatch" in _codes(report)


def test_expected_red_and_counterevidence_are_mandatory_bindings() -> None:
    value = _value()
    perspective = value["redSuiteCoverage"]["requirements"][0]["perspectives"][0]
    perspective["expectedRed"] = ""
    perspective["counterevidence"] = ""
    report = validate_red_suite_coverage(value, root=ROOT)
    assert "missing_perspective_binding" in _codes(report)


def test_mock_only_fixture_is_rejected_even_when_named_uniquely() -> None:
    value = _value()
    perspective = value["redSuiteCoverage"]["requirements"][0]["perspectives"][0]
    perspective["fixture"] = "tests/mocks/test_fake.py::test_fake"
    report = validate_red_suite_coverage(value, root=ROOT)
    assert "mock_only_fixture" in _codes(report)
    assert "fixture_not_executable" in _codes(report)


def test_unknown_requirement_cannot_expand_the_closed_world_silently() -> None:
    value = _value()
    unknown = copy.deepcopy(value["redSuiteCoverage"]["requirements"][0])
    unknown["id"] = "unknown_requirement"
    value["redSuiteCoverage"]["requirements"].append(unknown)
    report = validate_red_suite_coverage(value, root=ROOT)
    assert "requirement_set_mismatch" in _codes(report)
    assert "coverage_cell_count_mismatch" in _codes(report)


def test_fixture_path_cannot_escape_the_repository(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("def test_outside():\n    pass\n", encoding="utf-8")
    value = _value()
    value["redSuiteCoverage"]["requirements"][0]["perspectives"][0][
        "fixture"
    ] = "../outside.py::test_outside"
    report = validate_red_suite_coverage(value, root=repo)
    assert "fixture_outside_repo" in _codes(report)


def test_invalid_python_fixture_is_typed_instead_of_crashing(tmp_path: Path) -> None:
    broken = tmp_path / "broken.py"
    broken.write_text("def broken(:\n", encoding="utf-8")
    value = _value()
    value["redSuiteCoverage"]["requirements"][0]["perspectives"][0][
        "fixture"
    ] = "broken.py::test_broken"
    report = validate_red_suite_coverage(value, root=tmp_path)
    assert "fixture_syntax_invalid" in _codes(report)


def test_oversized_fixture_is_rejected_before_ast_parse(tmp_path: Path) -> None:
    oversized = tmp_path / "oversized.py"
    oversized.write_text(" " * 1_048_577, encoding="utf-8")
    value = _value()
    value["redSuiteCoverage"]["requirements"][0]["perspectives"][0][
        "fixture"
    ] = "oversized.py::test_oversized"
    report = validate_red_suite_coverage(value, root=tmp_path)
    assert "fixture_too_large" in _codes(report)


def test_fixture_size_limit_uses_one_bounded_stream_read() -> None:
    source = inspect.getsource(deepdive_red_suite_coverage._fixture_validation_error)
    assert '.open("rb")' in source
    assert ".read(MAX_FIXTURE_BYTES + 1)" in source
    assert ".stat()" not in source
    assert ".read_bytes()" not in source
