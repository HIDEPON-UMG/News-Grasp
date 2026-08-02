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
    "e2e_purpose",
    "e2e_non_purpose",
    "e2e_layer_model",
    "e2e_readiness_admission",
    "e2e_attempt_identity",
    "e2e_checkpoint_boundary",
    "e2e_exploration_separation",
    "e2e_resource_budget",
    "e2e_side_effect_boundary",
    "e2e_stop_and_failure",
    "e2e_evidence_contract",
    "e2e_completion_boundary",
    "deepdive_url_provenance",
    "deepdive_rendered_public_surface",
    "podcast_reader_value",
}
SHARED_QUALITY_ROUTES = {
    "production_generation",
    "repair_publish",
    "daily_quality",
    "codex_daily_audit",
}
REQUIRED_VIEWPOINT_SCOPES = {
    "final_e2e",
    "deepdive_url_provenance",
    "deepdive_rendered_public_surface",
    "podcast_reader_value",
}


def test_every_acceptance_has_a_unique_executable_fixture() -> None:
    value = json.loads(MATRIX.read_text(encoding="utf-8"))
    assert value["schemaVersion"] == "NEWS_GRASP_DEEPDIVE_TDD_ACCEPTANCE_MATRIX_V2"
    rows = value["historicalFailureCorpus"]
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
    rows = json.loads(MATRIX.read_text(encoding="utf-8"))[
        "historicalFailureCorpus"
    ]
    counts = Counter(row["family"] for row in rows)
    assert counts["e2e_admission"] >= 15
    assert counts["e2e_isolation"] >= 9
    assert counts["high_cost_single_attempt"] >= 11
    assert counts["nested_harness_resolution"] >= 3
    assert counts["goal_budget_semantics"] >= 6
    assert counts["compound_gate_repair"] >= 9
    assert counts["reporter_artifact_quality"] >= 2
    assert counts["url_provenance"] >= 10
    assert counts["rendered_public_surface"] >= 10
    assert counts["podcast_value"] >= 9
    assert counts["route_parity"] >= 5
    assert {row["polarity"] for row in rows} == {"positive", "negative"}


def test_red_suite_schema_cannot_be_satisfied_by_legacy_row_count() -> None:
    value = json.loads(MATRIX.read_text(encoding="utf-8"))
    assert value["schemaVersion"] == "NEWS_GRASP_DEEPDIVE_TDD_ACCEPTANCE_MATRIX_V2"
    assert value["coverageRule"] == "requirement_viewpoint_route_composite_proof"


def test_red_suite_declares_the_complete_independent_viewpoint_set() -> None:
    value = json.loads(MATRIX.read_text(encoding="utf-8"))
    coverage = value.get("redSuiteCoverage", {})
    assert coverage.get("schemaVersion") == "RED_SUITE_COVERAGE_V2"
    actual = {item.get("id") for item in coverage.get("viewpoints", [])}
    assert actual == REQUIRED_RED_VIEWPOINTS


def test_each_domain_has_its_own_complete_viewpoint_fixture_set() -> None:
    value = json.loads(MATRIX.read_text(encoding="utf-8"))
    coverage = value["redSuiteCoverage"]
    scopes = coverage.get("viewpointScopes", [])
    assert {item.get("id") for item in scopes} == REQUIRED_VIEWPOINT_SCOPES
    all_fixtures: list[str] = []
    for scope in scopes:
        bindings = scope.get("bindings", [])
        assert {item.get("viewpoint") for item in bindings} == REQUIRED_RED_VIEWPOINTS
        fixtures = [item.get("fixture") for item in bindings]
        assert len(fixtures) == len(set(fixtures)) == 10
        all_fixtures.extend(fixtures)
        for binding in bindings:
            for field in ("acceptanceId", "fixture", "expectedRed", "counterevidence"):
                assert binding.get(field), (scope["id"], field, binding)
    assert len(all_fixtures) == len(set(all_fixtures)) == 40


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


def test_requirements_and_viewpoints_have_independent_executable_fixtures() -> None:
    value = json.loads(MATRIX.read_text(encoding="utf-8"))
    coverage = value.get("redSuiteCoverage", {})
    requirements = coverage.get("requirements", [])
    assert requirements
    requirement_fixtures = [item.get("fixture") for item in requirements]
    assert len(requirement_fixtures) == len(set(requirement_fixtures)) == 15
    for requirement in requirements:
        for field in (
            "acceptanceId",
            "fixture",
            "productionConsumer",
            "expectedRed",
            "counterevidence",
        ):
            assert requirement.get(field), (field, requirement)
    scope_bindings = [
        binding
        for scope in coverage.get("viewpointScopes", [])
        for binding in scope.get("bindings", [])
    ]
    fixtures = [item.get("fixture") for item in scope_bindings]
    assert len(fixtures) == len(set(fixtures)) == 40
    route_fixtures = [item.get("fixture") for item in coverage.get("routes", [])]
    all_fixtures = [*requirement_fixtures, *fixtures, *route_fixtures]
    assert len(all_fixtures) == len(set(all_fixtures)) == 60
    for item in [*requirements, *scope_bindings]:
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
    viewpoints = {item.get("id") for item in coverage.get("viewpoints", [])}
    assert viewpoints == REQUIRED_RED_VIEWPOINTS
    scope_bindings = {
        scope["id"]: {item["viewpoint"] for item in scope["bindings"]}
        for scope in coverage.get("viewpointScopes", [])
    }
    requirement_scopes = coverage.get("requirementViewpointScopes", {})
    assert set(requirement_scopes) == REQUIRED_RED_REQUIREMENTS
    cells: list[tuple[str, str, str]] = []
    for requirement in coverage.get("requirements", []):
        applicable_routes = set(requirement.get("routeIds", []))
        viewpoint_scope = requirement_scopes.get(requirement["id"])
        assert viewpoint_scope in REQUIRED_VIEWPOINT_SCOPES
        assert scope_bindings[viewpoint_scope] == REQUIRED_RED_VIEWPOINTS
        assert applicable_routes
        assert applicable_routes <= routes
        for viewpoint in scope_bindings[viewpoint_scope]:
            for route_id in applicable_routes:
                cells.append((requirement["id"], viewpoint, route_id))
    assert len(cells) == 240
    assert len(cells) == len(set(cells))


def test_monolithic_e2e_requirement_alias_is_forbidden() -> None:
    value = json.loads(MATRIX.read_text(encoding="utf-8"))
    ids = {item.get("id") for item in value["redSuiteCoverage"]["requirements"]}
    assert "final_e2e_discipline" not in ids
