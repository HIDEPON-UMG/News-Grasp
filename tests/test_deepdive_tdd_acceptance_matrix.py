from __future__ import annotations

import ast
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "fixtures" / "deepdive_quality" / "tdd_acceptance_matrix.json"

REQUIRED_RED_VIEWPOINTS = {
    "normal",
    "failure",
    "boundary",
    "substitution",
    "drift",
    "replay",
    "missing",
    "cross_lineage",
    "recovery",
    "human_impact",
}
REQUIRED_RED_REQUIREMENTS = {
    "final_e2e_discipline",
    "deepdive_url_provenance",
    "podcast_reader_value",
}
SHARED_QUALITY_ROUTES = {
    "production_generation",
    "repair_publish",
    "daily_quality",
    "codex_daily_audit",
}


def test_every_acceptance_has_a_unique_executable_fixture() -> None:
    value = json.loads(MATRIX.read_text(encoding="utf-8"))
    assert value["schemaVersion"] == "NEWS_GRASP_DEEPDIVE_TDD_ACCEPTANCE_MATRIX_V2"
    rows = value["rows"]
    assert len(rows) >= 87
    ids = [row["id"] for row in rows]
    fixtures = [row["fixture"] for row in rows]
    assert len(ids) == len(set(ids))
    assert len(fixtures) == len(set(fixtures))
    for row in rows:
        assert set(row) == {
            "id",
            "family",
            "polarity",
            "requirement",
            "failureMode",
            "fixture",
        }
        assert row["polarity"] in {"positive", "negative"}
        relative, function_name = row["fixture"].split("::", 1)
        path = ROOT / relative
        assert path.is_file(), row
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        functions = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert function_name in functions, row


def test_matrix_covers_each_failure_family_and_both_polarities() -> None:
    rows = json.loads(MATRIX.read_text(encoding="utf-8"))["rows"]
    counts = Counter(row["family"] for row in rows)
    assert counts["e2e_admission"] >= 15
    assert counts["e2e_isolation"] >= 9
    assert counts["high_cost_single_attempt"] >= 11
    assert counts["nested_harness_resolution"] >= 3
    assert counts["goal_budget_semantics"] >= 6
    assert counts["compound_gate_repair"] >= 9
    assert counts["reporter_artifact_quality"] >= 2
    assert counts["url_provenance"] >= 10
    assert counts["podcast_value"] >= 9
    assert counts["route_parity"] >= 5
    assert {row["polarity"] for row in rows} == {"positive", "negative"}


def test_red_suite_schema_cannot_be_satisfied_by_legacy_row_count() -> None:
    value = json.loads(MATRIX.read_text(encoding="utf-8"))
    assert value["schemaVersion"] == "NEWS_GRASP_DEEPDIVE_TDD_ACCEPTANCE_MATRIX_V2"
    assert value["coverageRule"] == "requirement_viewpoint_route_composite_proof"


def test_red_suite_declares_the_complete_independent_viewpoint_set() -> None:
    value = json.loads(MATRIX.read_text(encoding="utf-8"))
    actual = set(value.get("redSuiteCoverage", {}).get("requiredViewpoints", []))
    assert actual == REQUIRED_RED_VIEWPOINTS


def test_red_suite_covers_each_user_value_requirement_independently() -> None:
    value = json.loads(MATRIX.read_text(encoding="utf-8"))
    requirements = value.get("redSuiteCoverage", {}).get("requirements", [])
    actual = {item.get("id") for item in requirements}
    assert actual == REQUIRED_RED_REQUIREMENTS


def test_red_suite_binds_every_shared_quality_route() -> None:
    value = json.loads(MATRIX.read_text(encoding="utf-8"))
    routes = value.get("redSuiteCoverage", {}).get("routes", [])
    actual = {item.get("id") for item in routes if item.get("scope") == "shared_quality"}
    assert actual == SHARED_QUALITY_ROUTES


def test_each_requirement_has_all_viewpoints_and_distinct_executable_fixtures() -> None:
    value = json.loads(MATRIX.read_text(encoding="utf-8"))
    requirements = value.get("redSuiteCoverage", {}).get("requirements", [])
    assert requirements
    for requirement in requirements:
        perspectives = requirement.get("perspectives", [])
        assert {item.get("viewpoint") for item in perspectives} == REQUIRED_RED_VIEWPOINTS
        fixtures = [item.get("fixture") for item in perspectives]
        assert None not in fixtures
        assert len(fixtures) == len(set(fixtures)) == len(REQUIRED_RED_VIEWPOINTS)
        for item in perspectives:
            assert item.get("acceptanceId")
            assert item.get("productionConsumer")
            assert item.get("expectedRed")
            assert item.get("counterevidence")
            relative, function_name = item["fixture"].split("::", 1)
            path = ROOT / relative
            assert path.is_file(), item
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            functions = {
                node.name
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            assert function_name in functions, item


def test_red_suite_expands_to_unique_requirement_viewpoint_route_cells() -> None:
    value = json.loads(MATRIX.read_text(encoding="utf-8"))
    coverage = value.get("redSuiteCoverage", {})
    routes = {item.get("id") for item in coverage.get("routes", [])}
    cells: list[tuple[str, str, str]] = []
    for requirement in coverage.get("requirements", []):
        applicable_routes = set(requirement.get("routeIds", []))
        assert applicable_routes
        assert applicable_routes <= routes
        for perspective in requirement.get("perspectives", []):
            for route_id in applicable_routes:
                cells.append((requirement["id"], perspective["viewpoint"], route_id))
    assert len(cells) == 90
    assert len(cells) == len(set(cells))
