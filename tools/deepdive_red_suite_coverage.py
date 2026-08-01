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
REQUIRED_REQUIREMENTS = {
    "final_e2e_discipline",
    "deepdive_url_provenance",
    "podcast_reader_value",
}
MAX_FIXTURE_BYTES = 1_048_576


def _finding(code: str, detail: str) -> dict[str, str]:
    return {"code": code, "detail": detail}


def _fixture_validation_error(root: Path, fixture: str) -> str | None:
    try:
        relative, function_name = fixture.split("::", 1)
    except ValueError:
        return "fixture_not_executable"
    try:
        canonical_root = root.resolve(strict=True)
        relative_path = Path(relative)
        if relative_path.is_absolute() or relative_path.suffix.casefold() != ".py":
            return "fixture_outside_repo"
        path = (canonical_root / relative_path).resolve(strict=True)
        path.relative_to(canonical_root)
    except ValueError:
        return "fixture_outside_repo"
    except OSError:
        return "fixture_not_executable"
    if not path.is_file():
        return "fixture_not_executable"
    try:
        with path.open("rb") as stream:
            payload = stream.read(MAX_FIXTURE_BYTES + 1)
        if len(payload) > MAX_FIXTURE_BYTES:
            return "fixture_too_large"
        source = payload.decode("utf-8-sig")
    except (OSError, UnicodeError):
        return "fixture_not_executable"
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return "fixture_syntax_invalid"
    exists = any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function_name
        for node in tree.body
    )
    return None if exists else "fixture_not_executable"


def _append_fixture_findings(
    findings: list[dict[str, str]], root: Path, fixture: str
) -> None:
    code = _fixture_validation_error(root, fixture)
    if code is None:
        return
    findings.append(_finding("fixture_not_executable", fixture))
    if code != "fixture_not_executable":
        findings.append(_finding(code, fixture))


def validate_red_suite_coverage(
    value: dict[str, Any],
    *,
    root: Path = ROOT,
    route_registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Requirement×観点×routeの複合証明をfail-closedで検証する。"""
    findings: list[dict[str, str]] = []
    coverage = value.get("redSuiteCoverage")
    if not isinstance(coverage, dict):
        return {
            "schemaVersion": "RED_SUITE_COVERAGE_REPORT_V1",
            "status": "Red",
            "findings": [_finding("coverage_missing", "redSuiteCoverageがない")],
            "coverageCellCount": 0,
        }

    if value.get("schemaVersion") != "NEWS_GRASP_DEEPDIVE_TDD_ACCEPTANCE_MATRIX_V2":
        findings.append(_finding("matrix_schema_invalid", "V2 schemaではない"))
    if value.get("coverageRule") != "requirement_viewpoint_route_composite_proof":
        findings.append(_finding("coverage_rule_invalid", "複合証明ruleではない"))
    if coverage.get("schemaVersion") != "RED_SUITE_COVERAGE_V1":
        findings.append(_finding("coverage_schema_invalid", "coverage schemaが不正"))

    viewpoints = coverage.get("requiredViewpoints", [])
    if len(viewpoints) != len(set(viewpoints)):
        findings.append(_finding("duplicate_viewpoint", "観点IDが重複している"))
    if set(viewpoints) != REQUIRED_VIEWPOINTS:
        findings.append(_finding("viewpoint_set_mismatch", "必須10観点と一致しない"))

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
        _append_fixture_findings(findings, root, fixture)
        route_fixtures[route_id] = fixture

    requirements = coverage.get("requirements", [])
    requirement_ids = [row.get("id") for row in requirements]
    if len(requirement_ids) != len(set(requirement_ids)):
        findings.append(_finding("duplicate_requirement", "Requirement IDが重複している"))
    if set(requirement_ids) != REQUIRED_REQUIREMENTS:
        findings.append(_finding("requirement_set_mismatch", "必須Requirement集合と一致しない"))

    cells: list[tuple[str, str, str]] = []
    composite_proofs: set[tuple[str, str, str, str]] = set()
    for requirement in requirements:
        requirement_id = requirement.get("id")
        expected_requirement_routes = (
            {"final_e2e_wrapper"}
            if requirement_id == "final_e2e_discipline"
            else shared_routes
        )
        route_set = set(requirement.get("routeIds", []))
        if route_set != expected_requirement_routes:
            findings.append(_finding("requirement_route_coverage_mismatch", str(requirement_id)))

        perspectives = requirement.get("perspectives", [])
        perspective_ids = [row.get("viewpoint") for row in perspectives]
        missing = REQUIRED_VIEWPOINTS - set(perspective_ids)
        extra = set(perspective_ids) - REQUIRED_VIEWPOINTS
        if missing:
            findings.append(_finding("missing_viewpoints", f"{requirement_id}:{sorted(missing)}"))
        if extra:
            findings.append(_finding("unknown_viewpoints", f"{requirement_id}:{sorted(extra)}"))
        if len(perspective_ids) != len(set(perspective_ids)):
            findings.append(_finding("duplicate_requirement_viewpoint", str(requirement_id)))

        fixtures = [row.get("fixture") for row in perspectives]
        if len(fixtures) != len(set(fixtures)):
            findings.append(_finding("duplicate_perspective_fixture", str(requirement_id)))
        if len(set(fixtures)) == 1 and perspectives:
            findings.append(_finding("single_red_implementation", str(requirement_id)))

        for perspective in perspectives:
            viewpoint = perspective.get("viewpoint")
            required_fields = (
                "viewpoint",
                "acceptanceId",
                "fixture",
                "productionConsumer",
                "expectedRed",
                "counterevidence",
            )
            if not all(perspective.get(field) for field in required_fields):
                findings.append(
                    _finding("missing_perspective_binding", f"{requirement_id}:{viewpoint}")
                )
                continue
            fixture = perspective["fixture"]
            if "mock" in fixture.lower():
                findings.append(_finding("mock_only_fixture", fixture))
            _append_fixture_findings(findings, root, fixture)
            for route_id in route_set:
                cell = (requirement_id, viewpoint, route_id)
                cells.append(cell)
                route_fixture = route_fixtures.get(route_id, "")
                composite_proofs.add((requirement_id, viewpoint, fixture, route_fixture))

    if len(cells) != len(set(cells)):
        findings.append(_finding("duplicate_coverage_cell", "coverage cellが重複している"))
    if len(cells) != 90:
        findings.append(_finding("coverage_cell_count_mismatch", f"actual={len(cells)} expected=90"))
    if len(composite_proofs) != len(cells):
        findings.append(_finding("composite_proof_not_unique", "観点fixtureとroute fixtureの組が一意でない"))

    canonical = json.dumps(coverage, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return {
        "schemaVersion": "RED_SUITE_COVERAGE_REPORT_V1",
        "status": "Green" if not findings else "Red",
        "findings": findings,
        "requirementCount": len(requirements),
        "viewpointCount": len(REQUIRED_VIEWPOINTS),
        "routeCount": len(route_rows),
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
