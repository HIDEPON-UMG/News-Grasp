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


def test_complete_red_suite_has_two_hundred_forty_composite_coverage_cells() -> None:
    report = validate_red_suite_coverage(_value(), root=ROOT)
    assert report["status"] == "Green", report
    assert report["requirementCount"] == 15
    assert report["viewpointCount"] == 10
    assert report["coverageCellCount"] == 240
    assert report["fixtureCount"] == 60
    assert len(report["fixtureSetSha256"]) == 64
    assert len(report["fixtureImplementationSetSha256"]) == 64
    assert len(report["historicalCorpusSha256"]) == 64
    assert report["pairCaseCount"] == 150
    assert report["pairCaseMode"] == "traceability_only"
    assert len(report["pairCaseSetSha256"]) == 64


def test_missing_viewpoint_is_rejected_as_requirement_definition_gap() -> None:
    value = _value()
    value["redSuiteCoverage"]["viewpointScopes"][0]["bindings"].pop()
    report = validate_red_suite_coverage(value, root=ROOT)
    assert "scope_viewpoint_set_mismatch" in _codes(report)
    assert "coverage_cell_count_mismatch" in _codes(report)


def test_one_fixture_cannot_substitute_for_all_red_viewpoints() -> None:
    value = _value()
    bindings = value["redSuiteCoverage"]["viewpointScopes"][0]["bindings"]
    fixture = bindings[0]["fixture"]
    for binding in bindings:
        binding["fixture"] = fixture
    report = validate_red_suite_coverage(value, root=ROOT)
    assert "duplicate_scope_viewpoint_fixture" in _codes(report)
    assert "single_red_implementation" in _codes(report)


def test_one_requirement_fixture_cannot_substitute_for_another_requirement() -> None:
    value = _value()
    requirements = value["redSuiteCoverage"]["requirements"]
    requirements[1]["fixture"] = requirements[0]["fixture"]
    report = validate_red_suite_coverage(value, root=ROOT)
    assert "duplicate_requirement_fixture" in _codes(report)


def test_fixture_cannot_substitute_across_requirement_viewpoint_and_route() -> None:
    value = _value()
    coverage = value["redSuiteCoverage"]
    coverage["viewpointScopes"][0]["bindings"][0]["fixture"] = coverage[
        "routes"
    ][0]["fixture"]
    report = validate_red_suite_coverage(value, root=ROOT)
    assert "duplicate_cross_dimension_fixture" in _codes(report)
    assert "fixture_count_mismatch" in _codes(report)


def test_route_omission_cannot_hide_behind_shared_engine_claim() -> None:
    value = _value()
    requirement = next(
        item
        for item in value["redSuiteCoverage"]["requirements"]
        if item["id"] == "podcast_reader_value"
    )
    requirement["routeIds"].remove("codex_daily_audit")
    report = validate_red_suite_coverage(value, root=ROOT)
    assert "requirement_route_coverage_mismatch" in _codes(report)
    assert "coverage_cell_count_mismatch" in _codes(report)


def test_expected_red_and_counterevidence_are_mandatory_bindings() -> None:
    value = _value()
    requirement = value["redSuiteCoverage"]["requirements"][0]
    requirement["expectedRed"] = ""
    requirement["counterevidence"] = ""
    report = validate_red_suite_coverage(value, root=ROOT)
    assert "missing_requirement_binding" in _codes(report)


def test_viewpoint_expected_red_and_counterevidence_are_mandatory_bindings() -> None:
    value = _value()
    binding = value["redSuiteCoverage"]["viewpointScopes"][0]["bindings"][0]
    binding["expectedRed"] = ""
    binding["counterevidence"] = ""
    report = validate_red_suite_coverage(value, root=ROOT)
    assert "missing_scope_viewpoint_binding" in _codes(report)


def test_viewpoint_fixture_cannot_be_substituted_across_content_domains() -> None:
    value = _value()
    scopes = value["redSuiteCoverage"]["viewpointScopes"]
    scopes[1]["bindings"][0]["fixture"] = scopes[0]["bindings"][0]["fixture"]
    report = validate_red_suite_coverage(value, root=ROOT)
    assert "duplicate_cross_dimension_fixture" in _codes(report)
    assert "fixture_count_mismatch" in _codes(report)


def test_requirement_cannot_be_rebound_to_another_domain_viewpoint_scope() -> None:
    value = _value()
    value["redSuiteCoverage"]["requirementViewpointScopes"][
        "deepdive_url_provenance"
    ] = "podcast_reader_value"
    report = validate_red_suite_coverage(value, root=ROOT)
    assert "requirement_viewpoint_scope_mismatch" in _codes(report)


def test_mock_only_fixture_is_rejected_even_when_named_uniquely() -> None:
    value = _value()
    requirement = value["redSuiteCoverage"]["requirements"][0]
    requirement["fixture"] = "tests/mocks/test_fake.py::test_fake"
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
    assert "fixture_count_mismatch" in _codes(report)


def test_legacy_nested_perspectives_cannot_create_a_second_coverage_authority() -> None:
    value = _value()
    value["redSuiteCoverage"]["requirements"][0]["perspectives"] = []
    report = validate_red_suite_coverage(value, root=ROOT)
    assert "legacy_perspectives_forbidden" in _codes(report)


def test_legacy_top_level_rows_cannot_create_a_second_coverage_authority() -> None:
    value = _value()
    value["rows"] = value.pop("historicalFailureCorpus")
    report = validate_red_suite_coverage(value, root=ROOT)
    assert "matrix_top_level_shape_invalid" in _codes(report)


def test_monolithic_e2e_alias_cannot_replace_an_explicit_requirement() -> None:
    value = _value()
    requirement = value["redSuiteCoverage"]["requirements"][0]
    requirement["id"] = "final_e2e_discipline"
    report = validate_red_suite_coverage(value, root=ROOT)
    assert "monolithic_requirement_forbidden" in _codes(report)
    assert "requirement_set_mismatch" in _codes(report)


def test_fixture_path_cannot_escape_the_repository(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("def test_outside():\n    pass\n", encoding="utf-8")
    value = _value()
    value["redSuiteCoverage"]["requirements"][0]["fixture"] = (
        "../outside.py::test_outside"
    )
    report = validate_red_suite_coverage(value, root=repo)
    assert "fixture_outside_repo" in _codes(report)


def test_invalid_python_fixture_is_typed_instead_of_crashing(tmp_path: Path) -> None:
    broken = tmp_path / "broken.py"
    broken.write_text("def broken(:\n", encoding="utf-8")
    value = _value()
    value["redSuiteCoverage"]["requirements"][0]["fixture"] = (
        "broken.py::test_broken"
    )
    report = validate_red_suite_coverage(value, root=tmp_path)
    assert "fixture_syntax_invalid" in _codes(report)


def test_pass_only_fixture_cannot_count_as_independent_red_implementation(
    tmp_path: Path,
) -> None:
    trivial = tmp_path / "trivial.py"
    trivial.write_text("def test_trivial():\n    pass\n", encoding="utf-8")
    value = _value()
    value["redSuiteCoverage"]["requirements"][0]["fixture"] = (
        "trivial.py::test_trivial"
    )
    report = validate_red_suite_coverage(value, root=tmp_path)
    assert "fixture_implementation_trivial" in _codes(report)


def test_docstring_return_and_constant_assert_cannot_count_as_red(
    tmp_path: Path,
) -> None:
    trivial = tmp_path / "trivial_variants.py"
    trivial.write_text(
        "def test_docstring():\n    \"\"\"説明だけ。\"\"\"\n\n"
        "def test_return():\n    return None\n\n"
        "def test_constant():\n    assert True\n",
        encoding="utf-8",
    )
    for function_name in ("test_docstring", "test_return", "test_constant"):
        value = _value()
        value["redSuiteCoverage"]["requirements"][0]["fixture"] = (
            f"trivial_variants.py::{function_name}"
        )
        report = validate_red_suite_coverage(value, root=tmp_path)
        assert "fixture_implementation_trivial" in _codes(report)


def test_distinct_function_names_cannot_hide_identical_implementations(
    tmp_path: Path,
) -> None:
    duplicate = tmp_path / "duplicate.py"
    duplicate.write_text(
        "def test_one():\n    assert True\n\n"
        "def test_two():\n    assert True\n",
        encoding="utf-8",
    )
    value = _value()
    requirements = value["redSuiteCoverage"]["requirements"]
    requirements[0]["fixture"] = "duplicate.py::test_one"
    requirements[1]["fixture"] = "duplicate.py::test_two"
    report = validate_red_suite_coverage(value, root=tmp_path)
    assert "duplicate_fixture_implementation" in _codes(report)


def test_different_constants_cannot_hide_one_helper_implementation(
    tmp_path: Path,
) -> None:
    duplicate = tmp_path / "helper_wrappers.py"
    duplicate.write_text(
        "def observe_one(value):\n    return bool(value)\n\n"
        "def observe_two(value):\n    return bool(value)\n\n"
        "def test_one():\n    assert observe_one('one')\n\n"
        "def test_two():\n    assert observe_two('two')\n",
        encoding="utf-8",
    )
    value = _value()
    requirements = value["redSuiteCoverage"]["requirements"]
    requirements[0]["fixture"] = "helper_wrappers.py::test_one"
    requirements[1]["fixture"] = "helper_wrappers.py::test_two"
    report = validate_red_suite_coverage(value, root=tmp_path)
    assert "duplicate_fixture_implementation" in _codes(report)


def test_oversized_fixture_is_rejected_before_ast_parse(tmp_path: Path) -> None:
    oversized = tmp_path / "oversized.py"
    oversized.write_text(" " * 1_048_577, encoding="utf-8")
    value = _value()
    value["redSuiteCoverage"]["requirements"][0]["fixture"] = (
        "oversized.py::test_oversized"
    )
    report = validate_red_suite_coverage(value, root=tmp_path)
    assert "fixture_too_large" in _codes(report)


def test_fixture_size_limit_uses_one_bounded_stream_read() -> None:
    source = inspect.getsource(
        deepdive_red_suite_coverage._fixture_validation_evidence
    )
    assert '.open("rb")' in source
    assert ".read(MAX_FIXTURE_BYTES + 1)" in source
    assert ".stat()" not in source
    assert ".read_bytes()" not in source
