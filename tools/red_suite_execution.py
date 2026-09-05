from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.deepdive_red_suite_coverage import (
    MATRIX,
    ROOT,
    build_requirement_viewpoint_pair_cases,
    validate_red_suite_coverage,
)


SCHEMA = "RED_SUITE_EXECUTION_RECEIPT_V1"
PAIR_TEST_SELECTOR = (
    "tests/test_red_suite_pair_cases.py::"
    "test_requirement_viewpoint_pair_observes_its_own_red"
)
PRODUCTION_DEPENDENCY_PATTERNS = (
    "tools/**/*.py",
    "scripts/ops/**/*.ps1",
    "config/**/*.json",
    "tests/**/*.py",
    "pyproject.toml",
    "pytest.ini",
    "requirements*.txt",
)


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _production_dependency_manifest(root: Path) -> dict[str, str]:
    canonical_root = root.resolve(strict=True)
    paths = {
        path.resolve(strict=True)
        for pattern in PRODUCTION_DEPENDENCY_PATTERNS
        for path in canonical_root.glob(pattern)
        if path.is_file() and "__pycache__" not in path.parts
    }
    return {
        path.relative_to(canonical_root).as_posix(): _file_sha256(path)
        for path in sorted(paths, key=lambda item: item.as_posix())
    }


def _fixture_selectors(coverage: dict[str, Any]) -> list[str]:
    selectors: list[str] = []
    selectors.extend(row["fixture"] for row in coverage["requirements"])
    selectors.extend(
        binding["fixture"]
        for scope in coverage["viewpointScopes"]
        for binding in scope["bindings"]
    )
    selectors.extend(row["fixture"] for row in coverage["routes"])
    if len(selectors) != 60 or len(selectors) != len(set(selectors)):
        raise ValueError("RED_SUITE_EXECUTION_SELECTOR_SET_INVALID")
    return selectors


def _selector_owns_node(selector: str, node_id: str) -> bool:
    return node_id == selector or node_id.startswith(f"{selector}[")


@dataclass(eq=False)
class _Recorder:
    collected: list[str] = field(default_factory=list)
    collection_errors: list[str] = field(default_factory=list)
    outcomes: dict[str, str] = field(default_factory=dict)
    failures: list[dict[str, str]] = field(default_factory=list)

    def pytest_collection_modifyitems(
        self, session: Any, config: Any, items: list[Any]
    ) -> None:
        del session, config
        self.collected = [item.nodeid.replace("\\", "/") for item in items]

    def pytest_collectreport(self, report: Any) -> None:
        if report.failed:
            self.collection_errors.append(str(report.longrepr))

    def pytest_runtest_logreport(self, report: Any) -> None:
        node_id = report.nodeid.replace("\\", "/")
        if report.when == "call":
            self.outcomes[node_id] = report.outcome
        elif report.failed:
            self.failures.append(
                {
                    "nodeId": node_id,
                    "phase": report.when,
                    "detail": str(report.longrepr),
                }
            )


def execute_red_suite(
    *,
    matrix_path: Path = MATRIX,
    root: Path = ROOT,
) -> dict[str, Any]:
    import pytest

    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    coverage_report = validate_red_suite_coverage(matrix, root=root)
    if coverage_report["status"] != "Green":
        raise ValueError("RED_SUITE_COVERAGE_NOT_GREEN")
    coverage = matrix["redSuiteCoverage"]
    selectors = _fixture_selectors(coverage)
    pair_cases = build_requirement_viewpoint_pair_cases(matrix)
    production_dependencies = _production_dependency_manifest(root)
    expected_pair_nodes = {
        f"{PAIR_TEST_SELECTOR}[{case['caseId']}]" for case in pair_cases
    }

    recorder = _Recorder()
    previous_cwd = Path.cwd()
    try:
        os.chdir(root)
        exit_code = int(
            pytest.main(
                [
                    "-q",
                    "-p",
                    "no:cacheprovider",
                    "--disable-warnings",
                    *selectors,
                    PAIR_TEST_SELECTOR,
                ],
                plugins=[recorder],
            )
        )
    finally:
        os.chdir(previous_cwd)

    collected = set(recorder.collected)
    collected_pair_nodes = {
        node_id
        for node_id in collected
        if node_id.startswith(f"{PAIR_TEST_SELECTOR}[")
    }
    missing_selectors = [
        selector
        for selector in selectors
        if not any(_selector_owns_node(selector, node_id) for node_id in collected)
    ]
    unexpected_nodes = sorted(
        node_id
        for node_id in collected
        if node_id not in collected_pair_nodes
        and not any(_selector_owns_node(selector, node_id) for selector in selectors)
    )
    failed_nodes = sorted(
        node_id
        for node_id, outcome in recorder.outcomes.items()
        if outcome != "passed"
    )
    missing_outcomes = sorted(collected - set(recorder.outcomes))
    pair_nodes_match = collected_pair_nodes == expected_pair_nodes
    collected_node_ids = sorted(collected)
    exact_execution_shape = (
        len(collected_node_ids) == 211
        and len(recorder.outcomes) == 211
        and sum(outcome == "passed" for outcome in recorder.outcomes.values()) == 211
    )
    status = "Green" if all(
        (
            exit_code == 0,
            not recorder.collection_errors,
            not recorder.failures,
            not failed_nodes,
            not missing_outcomes,
            not missing_selectors,
            not unexpected_nodes,
            pair_nodes_match,
            exact_execution_shape,
        )
    ) else "Red"
    return {
        "schemaVersion": SCHEMA,
        "status": status,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "matrixPath": str(matrix_path.resolve()),
        "matrixSha256": _file_sha256(matrix_path),
        "coverageSha256": coverage_report["coverageSha256"],
        "fixtureSetSha256": coverage_report["fixtureSetSha256"],
        "fixtureImplementationSetSha256": coverage_report[
            "fixtureImplementationSetSha256"
        ],
        "pairCaseSetSha256": coverage_report["pairCaseSetSha256"],
        "historicalCorpusSha256": coverage_report["historicalCorpusSha256"],
        "pairCaseMode": "traceability_only",
        "producerSha256": _file_sha256(Path(__file__).resolve()),
        "pairTestSha256": _file_sha256(
            root / PAIR_TEST_SELECTOR.split("::", 1)[0]
        ),
        "productionDependencyCount": len(production_dependencies),
        "productionDependencySetSha256": _canonical_sha256(
            production_dependencies
        ),
        "selectorCount": len(selectors),
        "selectorSetSha256": _canonical_sha256(selectors),
        "selectors": selectors,
        "pairCaseCount": len(pair_cases),
        "pairNodeIds": sorted(collected_pair_nodes),
        "collectedNodeCount": len(collected_node_ids),
        "collectedNodeSetSha256": _canonical_sha256(collected_node_ids),
        "collectedNodeIds": collected_node_ids,
        "passedNodeCount": sum(
            outcome == "passed" for outcome in recorder.outcomes.values()
        ),
        "nodeOutcomes": dict(sorted(recorder.outcomes.items())),
        "collectionErrors": recorder.collection_errors,
        "executionFailures": [
            *recorder.failures,
            *({"nodeId": node_id, "phase": "call", "detail": "not passed"}
              for node_id in failed_nodes),
        ],
        "missingOutcomes": missing_outcomes,
        "missingSelectors": missing_selectors,
        "unexpectedNodes": unexpected_nodes,
        "pytestExitCode": exit_code,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", type=Path, default=MATRIX)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    receipt = execute_red_suite(matrix_path=args.matrix, root=args.root)
    payload = json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if receipt["status"] == "Green" else 1


if __name__ == "__main__":
    raise SystemExit(main())
