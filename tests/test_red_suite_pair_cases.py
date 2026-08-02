from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tools.deepdive_red_suite_coverage import (
    build_requirement_viewpoint_pair_cases,
    validate_red_suite_coverage,
)


ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "fixtures" / "deepdive_quality" / "tdd_acceptance_matrix.json"
MATRIX_VALUE = json.loads(MATRIX.read_text(encoding="utf-8"))
PAIR_CASES = build_requirement_viewpoint_pair_cases(MATRIX_VALUE)


def _case_id(case: dict[str, object]) -> str:
    return str(case["caseId"])


@pytest.mark.parametrize("case", PAIR_CASES, ids=_case_id)
def test_requirement_viewpoint_pair_observes_its_own_red(
    case: dict[str, object],
) -> None:
    """各Requirement×観点を別caseとして壊し、対象detailのRedを観測する。"""
    value = copy.deepcopy(MATRIX_VALUE)
    coverage = value["redSuiteCoverage"]
    requirement = next(
        row
        for row in coverage["requirements"]
        if row["id"] == case["requirementId"]
    )
    requirement["expectedRed"] = ""
    scope = next(
        row
        for row in coverage["viewpointScopes"]
        if row["id"] == case["viewpointScope"]
    )
    binding = next(
        row
        for row in scope["bindings"]
        if row["viewpoint"] == case["viewpoint"]
    )
    binding["counterevidence"] = ""

    report = validate_red_suite_coverage(value, root=ROOT)
    observed = {
        (finding["code"], finding["detail"])
        for finding in report["findings"]
    }
    expected = {
        (finding["code"], finding["detail"])
        for finding in case["expectedFindings"]
    }
    assert report["status"] == "Red"
    assert expected <= observed


def test_pair_case_registry_is_exact_and_individually_addressable() -> None:
    assert len(PAIR_CASES) == 150
    case_ids = [case["caseId"] for case in PAIR_CASES]
    assert len(case_ids) == len(set(case_ids)) == 150
    assert all(len(case["injectedDefects"]) == 2 for case in PAIR_CASES)
    assert all(len(case["expectedFindings"]) == 2 for case in PAIR_CASES)
