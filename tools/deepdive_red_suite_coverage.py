from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "fixtures" / "deepdive_quality" / "tdd_acceptance_matrix.json"
ROUTES = ROOT / "config" / "deepdive_quality_routes.json"

REQUIRED_VIEWPOINTS = {
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
VIEWPOINT_ORDER = (
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
)
REQUIRED_REQUIREMENTS = {
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
E2E_REQUIREMENTS = REQUIRED_REQUIREMENTS - {
    "deepdive_url_provenance",
    "deepdive_rendered_public_surface",
    "podcast_reader_value",
}
REQUIRED_VIEWPOINT_SCOPES = {
    "final_e2e",
    "deepdive_url_provenance",
    "deepdive_rendered_public_surface",
    "podcast_reader_value",
}
EXPECTED_REQUIREMENT_SCOPES = {
    requirement_id: (
        "final_e2e" if requirement_id in E2E_REQUIREMENTS else requirement_id
    )
    for requirement_id in REQUIRED_REQUIREMENTS
}
MAX_FIXTURE_BYTES = 1_048_576
MATRIX_TOP_LEVEL_KEYS = {
    "schemaVersion",
    "taskIdentity",
    "coverageRule",
    "redSuiteCoverage",
    "historicalFailureCorpus",
}


def _finding(code: str, detail: str) -> dict[str, str]:
    return {"code": code, "detail": detail}


def build_requirement_viewpoint_pair_cases(
    value: dict[str, Any],
) -> list[dict[str, Any]]:
    """150個のRequirement×同一domain観点Red caseを決定的に展開する。"""
    coverage = value.get("redSuiteCoverage", {})
    requirements = {
        row.get("id"): row for row in coverage.get("requirements", [])
    }
    requirement_scopes = coverage.get("requirementViewpointScopes", {})
    scope_bindings = {
        scope.get("id"): {
            binding.get("viewpoint"): binding
            for binding in scope.get("bindings", [])
        }
        for scope in coverage.get("viewpointScopes", [])
    }
    cases: list[dict[str, Any]] = []
    for requirement_id in sorted(REQUIRED_REQUIREMENTS):
        requirement = requirements.get(requirement_id, {})
        scope_id = requirement_scopes.get(requirement_id, "")
        bindings = scope_bindings.get(scope_id, {})
        for viewpoint in VIEWPOINT_ORDER:
            binding = bindings.get(viewpoint, {})
            case_id = f"{requirement_id}--{viewpoint}"
            cases.append(
                {
                    "caseId": case_id,
                    "requirementId": requirement_id,
                    "viewpoint": viewpoint,
                    "viewpointScope": scope_id,
                    "routeIds": sorted(requirement.get("routeIds", [])),
                    "requirementFixture": requirement.get("fixture", ""),
                    "viewpointFixture": binding.get("fixture", ""),
                    "productionConsumer": requirement.get(
                        "productionConsumer", ""
                    ),
                    "injectedDefects": [
                        {
                            "target": f"requirement:{requirement_id}",
                            "field": "expectedRed",
                            "value": "",
                        },
                        {
                            "target": f"scope:{scope_id}:{viewpoint}",
                            "field": "counterevidence",
                            "value": "",
                        },
                    ],
                    "expectedFindings": [
                        {
                            "code": "missing_requirement_binding",
                            "detail": requirement_id,
                        },
                        {
                            "code": "missing_scope_viewpoint_binding",
                            "detail": f"{scope_id}:{viewpoint}",
                        },
                    ],
                    "expectedRed": (
                        f"{requirement.get('expectedRed', '')} / "
                        f"{binding.get('expectedRed', '')}"
                    ),
                    "counterevidence": (
                        f"{requirement.get('counterevidence', '')} / "
                        f"{binding.get('counterevidence', '')}"
                    ),
                }
            )
    return cases


def _fixture_validation_evidence(
    root: Path, fixture: str
) -> tuple[str | None, str | None, str | None]:
    try:
        relative, function_name = fixture.split("::", 1)
    except ValueError:
        return "fixture_not_executable", None, None
    try:
        canonical_root = root.resolve(strict=True)
        relative_path = Path(relative)
        if relative_path.is_absolute() or relative_path.suffix.casefold() != ".py":
            return "fixture_outside_repo", None, None
        path = (canonical_root / relative_path).resolve(strict=True)
        path.relative_to(canonical_root)
    except ValueError:
        return "fixture_outside_repo", None, None
    except OSError:
        return "fixture_not_executable", None, None
    if not path.is_file():
        return "fixture_not_executable", None, None
    try:
        with path.open("rb") as stream:
            payload = stream.read(MAX_FIXTURE_BYTES + 1)
        if len(payload) > MAX_FIXTURE_BYTES:
            return "fixture_too_large", None, None
        source = payload.decode("utf-8-sig")
    except (OSError, UnicodeError):
        return "fixture_not_executable", None, None
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return "fixture_syntax_invalid", None, None
    local_functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    function = local_functions.get(function_name)
    if function is None:
        return "fixture_not_executable", None, None
    body = list(function.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    has_behavior_observation = any(
        isinstance(node, ast.Assert)
        or (
            isinstance(node, ast.Call)
            and (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "raises"
                or isinstance(node.func, ast.Name)
                and node.func.id == "raises"
            )
        )
        for statement in body
        for node in ast.walk(statement)
    )
    constant_assert_only = bool(body) and all(
        isinstance(statement, ast.Assert)
        and isinstance(statement.test, ast.Constant)
        for statement in body
    )
    return_none_only = bool(body) and all(
        isinstance(statement, ast.Return)
        and (
            statement.value is None
            or (
                isinstance(statement.value, ast.Constant)
                and statement.value.value is None
            )
        )
        for statement in body
    )
    if (
        not body
        or all(isinstance(statement, ast.Pass) for statement in body)
        or constant_assert_only
        or return_none_only
        or not has_behavior_observation
    ):
        return "fixture_implementation_trivial", None, None

    reachable_helpers: set[str] = set()
    pending = [function]
    while pending:
        current = pending.pop()
        for node in ast.walk(current):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in local_functions
                and node.func.id != function_name
                and node.func.id not in reachable_helpers
            ):
                reachable_helpers.add(node.func.id)
                pending.append(local_functions[node.func.id])

    class _SemanticShape(ast.NodeTransformer):
        def visit_Constant(self, node: ast.Constant) -> ast.AST:
            return ast.copy_location(ast.Constant(value="<CONST>"), node)

        def visit_Name(self, node: ast.Name) -> ast.AST:
            if isinstance(node.ctx, ast.Load) and node.id in reachable_helpers:
                return ast.copy_location(
                    ast.Name(id="<LOCAL_HELPER>", ctx=node.ctx), node
                )
            return node

    semantic_shape = _SemanticShape()
    fixture_dump = ast.dump(
        semantic_shape.visit(ast.Module(body=body, type_ignores=[])),
        include_attributes=False,
    )
    helper_dumps = sorted(
        ast.dump(
            semantic_shape.visit(
                ast.Module(
                    body=local_functions[helper_name].body,
                    type_ignores=[],
                )
            ),
            include_attributes=False,
        )
        for helper_name in reachable_helpers
    )
    body_dump = json.dumps(
        {"fixture": fixture_dump, "helperClosure": helper_dumps},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return (
        None,
        hashlib.sha256(payload).hexdigest(),
        hashlib.sha256(body_dump).hexdigest(),
    )


def _fixture_validation_error(root: Path, fixture: str) -> str | None:
    return _fixture_validation_evidence(root, fixture)[0]


def _append_fixture_findings(
    findings: list[dict[str, str]], root: Path, fixture: str
) -> tuple[str | None, str | None]:
    code, source_sha256, implementation_sha256 = _fixture_validation_evidence(
        root, fixture
    )
    if code is None:
        return source_sha256, implementation_sha256
    findings.append(_finding("fixture_not_executable", fixture))
    if code != "fixture_not_executable":
        findings.append(_finding(code, fixture))
    return None, None


def validate_red_suite_coverage(
    value: dict[str, Any],
    *,
    root: Path = ROOT,
    route_registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Requirement×観点×routeの複合証明をfail-closedで検証する。"""
    findings: list[dict[str, str]] = []
    fixture_hashes: dict[str, str] = {}
    fixture_implementation_hashes: dict[str, str] = {}
    fixture_refs: list[str] = []
    coverage = value.get("redSuiteCoverage")
    if not isinstance(coverage, dict):
        return {
            "schemaVersion": "RED_SUITE_COVERAGE_REPORT_V1",
            "status": "Red",
            "findings": [_finding("coverage_missing", "redSuiteCoverageがない")],
            "coverageCellCount": 0,
        }

    if set(value) != MATRIX_TOP_LEVEL_KEYS:
        findings.append(
            _finding("matrix_top_level_shape_invalid", str(sorted(value)))
        )
    if value.get("schemaVersion") != "NEWS_GRASP_DEEPDIVE_TDD_ACCEPTANCE_MATRIX_V2":
        findings.append(_finding("matrix_schema_invalid", "V2 schemaではない"))
    if value.get("coverageRule") != "requirement_viewpoint_route_composite_proof":
        findings.append(_finding("coverage_rule_invalid", "複合証明ruleではない"))
    if coverage.get("schemaVersion") != "RED_SUITE_COVERAGE_V2":
        findings.append(_finding("coverage_schema_invalid", "coverage schemaが不正"))

    required_viewpoints = coverage.get("requiredViewpoints", [])
    if len(required_viewpoints) != len(set(required_viewpoints)):
        findings.append(_finding("duplicate_viewpoint", "観点IDが重複している"))
    if set(required_viewpoints) != REQUIRED_VIEWPOINTS:
        findings.append(_finding("viewpoint_set_mismatch", "必須10観点と一致しない"))

    viewpoint_rows = coverage.get("viewpoints", [])
    viewpoint_ids = [row.get("id") for row in viewpoint_rows]
    if len(viewpoint_ids) != len(set(viewpoint_ids)):
        findings.append(_finding("duplicate_viewpoint", "観点rowが重複している"))
    if set(viewpoint_ids) != REQUIRED_VIEWPOINTS:
        findings.append(_finding("viewpoint_set_mismatch", "観点rowが必須10観点と一致しない"))
    for row in viewpoint_rows:
        if set(row) != {"id"}:
            findings.append(
                _finding("viewpoint_definition_shape_invalid", str(row.get("id")))
            )

    scope_rows = coverage.get("viewpointScopes", [])
    scope_ids = [row.get("id") for row in scope_rows]
    if len(scope_ids) != len(set(scope_ids)):
        findings.append(_finding("duplicate_viewpoint_scope", "scope IDが重複している"))
    if set(scope_ids) != REQUIRED_VIEWPOINT_SCOPES:
        findings.append(
            _finding("viewpoint_scope_set_mismatch", "必須4 domain scopeと一致しない")
        )
    scope_viewpoint_fixtures: dict[str, dict[str, str]] = {}
    for scope in scope_rows:
        scope_id = scope.get("id")
        bindings = scope.get("bindings", [])
        binding_ids = [row.get("viewpoint") for row in bindings]
        if len(binding_ids) != len(set(binding_ids)) or set(binding_ids) != REQUIRED_VIEWPOINTS:
            findings.append(
                _finding("scope_viewpoint_set_mismatch", str(scope_id))
            )
        bound_fixtures: dict[str, str] = {}
        for binding in bindings:
            viewpoint_id = binding.get("viewpoint")
            required_fields = (
                "viewpoint",
                "acceptanceId",
                "fixture",
                "expectedRed",
                "counterevidence",
            )
            if not all(binding.get(field) for field in required_fields):
                findings.append(
                    _finding(
                        "missing_scope_viewpoint_binding",
                        f"{scope_id}:{viewpoint_id}",
                    )
                )
                continue
            fixture = binding["fixture"]
            fixture_refs.append(fixture)
            if "mock" in fixture.lower():
                findings.append(_finding("mock_only_fixture", fixture))
            source_sha256, implementation_sha256 = _append_fixture_findings(
                findings, root, fixture
            )
            if source_sha256:
                fixture_hashes[fixture] = source_sha256
            if implementation_sha256:
                fixture_implementation_hashes[fixture] = implementation_sha256
            bound_fixtures[viewpoint_id] = fixture
        fixtures = list(bound_fixtures.values())
        if len(fixtures) != len(set(fixtures)):
            findings.append(
                _finding("duplicate_scope_viewpoint_fixture", str(scope_id))
            )
        if len(set(fixtures)) == 1 and bindings:
            findings.append(_finding("single_red_implementation", str(scope_id)))
        scope_viewpoint_fixtures[scope_id] = bound_fixtures

    requirement_scopes = coverage.get("requirementViewpointScopes", {})
    if requirement_scopes != EXPECTED_REQUIREMENT_SCOPES:
        findings.append(
            _finding(
                "requirement_viewpoint_scope_mismatch",
                "Requirementとdomain scopeの対応が不正",
            )
        )

    if route_registry is None:
        route_registry = json.loads(ROUTES.read_text(encoding="utf-8"))
    shared_routes = set(route_registry["declaredRoutes"])
    expected_routes = shared_routes | {"final_e2e_wrapper"}
    route_rows = coverage.get("routes", [])
    route_ids = [row.get("id") for row in route_rows]
    if len(route_ids) != len(set(route_ids)):
        findings.append(_finding("duplicate_route", "route IDが重複している"))
    if set(route_ids) != expected_routes:
        findings.append(_finding("route_set_mismatch", "route registryとの集合が一致しない"))
    route_fixtures: dict[str, str] = {}
    for row in route_rows:
        route_id = row.get("id")
        fixture = row.get("fixture")
        if not all(row.get(field) for field in ("id", "scope", "fixture", "productionConsumer")):
            findings.append(_finding("missing_route_binding", str(route_id)))
            continue
        if "mock" in fixture.lower():
            findings.append(_finding("mock_only_fixture", fixture))
        fixture_refs.append(fixture)
        source_sha256, implementation_sha256 = _append_fixture_findings(
            findings, root, fixture
        )
        if source_sha256:
            fixture_hashes[fixture] = source_sha256
        if implementation_sha256:
            fixture_implementation_hashes[fixture] = implementation_sha256
        route_fixtures[route_id] = fixture

    requirements = coverage.get("requirements", [])
    requirement_ids = [row.get("id") for row in requirements]
    if len(requirement_ids) != len(set(requirement_ids)):
        findings.append(_finding("duplicate_requirement", "Requirement IDが重複している"))
    if set(requirement_ids) != REQUIRED_REQUIREMENTS:
        findings.append(_finding("requirement_set_mismatch", "必須Requirement集合と一致しない"))

    cells: list[tuple[str, str, str]] = []
    composite_proofs: set[tuple[str, str, str, str, str, str]] = set()
    requirement_fixtures: list[str] = []
    for requirement in requirements:
        requirement_id = requirement.get("id")
        if "perspectives" in requirement:
            findings.append(
                _finding("legacy_perspectives_forbidden", str(requirement_id))
            )
        expected_requirement_routes = (
            {"final_e2e_wrapper"}
            if requirement_id in E2E_REQUIREMENTS
            else shared_routes
        )
        route_set = set(requirement.get("routeIds", []))
        if route_set != expected_requirement_routes:
            findings.append(_finding("requirement_route_coverage_mismatch", str(requirement_id)))
        required_fields = (
            "id",
            "acceptanceId",
            "fixture",
            "productionConsumer",
            "expectedRed",
            "counterevidence",
        )
        if not all(requirement.get(field) for field in required_fields):
            findings.append(_finding("missing_requirement_binding", str(requirement_id)))
            continue
        requirement_fixture = requirement["fixture"]
        requirement_fixtures.append(requirement_fixture)
        fixture_refs.append(requirement_fixture)
        if "mock" in requirement_fixture.lower():
            findings.append(_finding("mock_only_fixture", requirement_fixture))
        source_sha256, implementation_sha256 = _append_fixture_findings(
            findings, root, requirement_fixture
        )
        if source_sha256:
            fixture_hashes[requirement_fixture] = source_sha256
        if implementation_sha256:
            fixture_implementation_hashes[
                requirement_fixture
            ] = implementation_sha256
        viewpoint_scope = requirement_scopes.get(requirement_id, "")
        bound_viewpoints = scope_viewpoint_fixtures.get(viewpoint_scope, {})
        for viewpoint_id in bound_viewpoints:
            viewpoint_fixture = bound_viewpoints.get(viewpoint_id, "")
            for route_id in route_set:
                cell = (requirement_id, viewpoint_id, route_id)
                cells.append(cell)
                route_fixture = route_fixtures.get(route_id, "")
                composite_proofs.add(
                    (
                        requirement_id,
                        viewpoint_id,
                        route_id,
                        requirement_fixture,
                        viewpoint_fixture,
                        route_fixture,
                    )
                )

    if len(requirement_fixtures) != len(set(requirement_fixtures)):
        findings.append(_finding("duplicate_requirement_fixture", "Requirement fixtureが重複している"))
    if len(fixture_refs) != len(set(fixture_refs)):
        findings.append(
            _finding(
                "duplicate_cross_dimension_fixture",
                "Requirement・viewpoint・route間でfixtureが重複している",
            )
        )
    if len(fixture_refs) != 60 or len(fixture_hashes) != 60:
        findings.append(
            _finding(
                "fixture_count_mismatch",
                f"declared={len(fixture_refs)} validUnique={len(fixture_hashes)} expected=60",
            )
        )
    implementation_values = list(fixture_implementation_hashes.values())
    if (
        len(fixture_implementation_hashes) != 60
        or len(implementation_values) != len(set(implementation_values))
    ):
        findings.append(
            _finding(
                "duplicate_fixture_implementation",
                "60 fixtureの実装本体が独立していない",
            )
        )
    if "final_e2e_discipline" in requirement_ids:
        findings.append(_finding("monolithic_requirement_forbidden", "E2E Requirementが一行へ集約されている"))

    if len(cells) != len(set(cells)):
        findings.append(_finding("duplicate_coverage_cell", "coverage cellが重複している"))
    if len(cells) != 240:
        findings.append(_finding("coverage_cell_count_mismatch", f"actual={len(cells)} expected=240"))
    if len(composite_proofs) != len(cells):
        findings.append(_finding("composite_proof_not_unique", "観点fixtureとroute fixtureの組が一意でない"))

    pair_cases = build_requirement_viewpoint_pair_cases(value)
    pair_case_ids = [case["caseId"] for case in pair_cases]
    if len(pair_cases) != 150 or len(pair_case_ids) != len(set(pair_case_ids)):
        findings.append(
            _finding(
                "pair_case_set_mismatch",
                f"actual={len(pair_cases)} unique={len(set(pair_case_ids))} expected=150",
            )
        )
    pair_required_fields = (
        "caseId",
        "requirementId",
        "viewpoint",
        "viewpointScope",
        "routeIds",
        "requirementFixture",
        "viewpointFixture",
        "productionConsumer",
        "injectedDefects",
        "expectedFindings",
        "expectedRed",
        "counterevidence",
    )
    for case in pair_cases:
        if not all(case.get(field) for field in pair_required_fields):
            findings.append(
                _finding("pair_case_binding_missing", case["caseId"])
            )
    pair_case_canonical = json.dumps(
        pair_cases, ensure_ascii=False, sort_keys=True
    ).encode("utf-8")
    pair_case_set_sha256 = hashlib.sha256(pair_case_canonical).hexdigest()

    historical_corpus = value.get("historicalFailureCorpus")
    if not isinstance(historical_corpus, list):
        findings.append(
            _finding("historical_failure_corpus_invalid", "listではない")
        )
        historical_corpus = []
    historical_canonical = json.dumps(
        historical_corpus, ensure_ascii=False, sort_keys=True
    ).encode("utf-8")
    historical_corpus_sha256 = hashlib.sha256(historical_canonical).hexdigest()
    fixture_set_canonical = json.dumps(
        fixture_hashes, ensure_ascii=False, sort_keys=True
    ).encode("utf-8")
    fixture_set_sha256 = hashlib.sha256(fixture_set_canonical).hexdigest()
    fixture_implementation_canonical = json.dumps(
        fixture_implementation_hashes,
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    fixture_implementation_set_sha256 = hashlib.sha256(
        fixture_implementation_canonical
    ).hexdigest()
    canonical = json.dumps(
        {
            "coverage": coverage,
            "fixtureHashes": fixture_hashes,
            "fixtureImplementationHashes": fixture_implementation_hashes,
            "historicalCorpusSha256": historical_corpus_sha256,
            "pairCaseSetSha256": pair_case_set_sha256,
        },
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return {
        "schemaVersion": "RED_SUITE_COVERAGE_REPORT_V1",
        "status": "Green" if not findings else "Red",
        "findings": findings,
        "requirementCount": len(requirements),
        "viewpointCount": len(REQUIRED_VIEWPOINTS),
        "routeCount": len(route_rows),
        "fixtureCount": len(fixture_hashes),
        "fixtureSetSha256": fixture_set_sha256,
        "fixtureImplementationSetSha256": fixture_implementation_set_sha256,
        "historicalCorpusSha256": historical_corpus_sha256,
        "pairCaseMode": "traceability_only",
        "pairCaseCount": len(pair_cases),
        "pairCaseSetSha256": pair_case_set_sha256,
        "coverageCellCount": len(cells),
        "coverageSha256": hashlib.sha256(canonical).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", type=Path, default=MATRIX)
    args = parser.parse_args()
    value = json.loads(args.matrix.read_text(encoding="utf-8"))
    report = validate_red_suite_coverage(value, root=ROOT)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "Green" else 1


if __name__ == "__main__":
    raise SystemExit(main())
