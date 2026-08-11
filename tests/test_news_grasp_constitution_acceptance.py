from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any, Callable

import pytest


ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = (
    ROOT
    / "tests"
    / "fixtures"
    / "constitutional-operations"
    / "acceptance-matrix-v1.json"
)
PERSPECTIVE_SUFFIX = {
    "primary_behavior": "PRIMARY",
    "adversarial_boundary": "BOUNDARY",
    "operational_recovery": "RECOVERY",
}


def _matrix() -> dict[str, Any]:
    return json.loads(MATRIX_PATH.read_text(encoding="utf-8"))


def _fail(signature: str, reason: str) -> None:
    pytest.fail(f"{signature}: {reason}", pytrace=False)


def _assert_acceptance(row: dict[str, Any], perspective: str) -> None:
    acceptance_id = str(row["acceptanceId"])
    suffix = PERSPECTIVE_SUFFIX[perspective]
    signature = f"NGC_RED_{acceptance_id}_{suffix}"
    route = ROOT / str(row["productionRoute"])
    marker = str(row["consumerMarker"])

    if not route.is_file():
        _fail(signature, f"PRODUCTION_ROUTE_MISSING:{row['productionRoute']}")
    route_text = route.read_text(encoding="utf-8-sig")
    if marker not in route_text:
        _fail(signature, f"CONSUMER_MARKER_MISSING:{marker}")

    try:
        consumer = importlib.import_module("tools.news_grasp_constitution")
    except (ImportError, ModuleNotFoundError) as error:
        _fail(signature, f"CONSTITUTION_CONSUMER_MISSING:{error}")

    evaluator = getattr(consumer, "evaluate_acceptance", None)
    if not callable(evaluator):
        _fail(signature, "ACCEPTANCE_EVALUATOR_MISSING")

    try:
        result = evaluator(
            repo_root=ROOT,
            acceptance_id=acceptance_id,
            perspective=perspective,
        )
    except Exception as error:  # pragma: no cover - intentional pre-Green boundary
        _fail(signature, f"EVALUATOR_EXCEPTION:{type(error).__name__}:{error}")

    if not isinstance(result, dict):
        _fail(signature, "RESULT_NOT_OBJECT")
    expected = {
        "schemaVersion": "NEWS_GRASP_CONSTITUTION_ACCEPTANCE_RESULT_V1",
        "acceptanceId": acceptance_id,
        "requirementId": str(row["requirementId"]),
        "perspective": perspective,
        "productionRoute": str(row["productionRoute"]),
        "consumerMarker": marker,
        "status": "Green",
    }
    for key, expected_value in expected.items():
        if result.get(key) != expected_value:
            _fail(
                signature,
                f"RESULT_FIELD_MISMATCH:{key}:{result.get(key)!r}!={expected_value!r}",
            )
    evidence = result.get("evidence")
    if not isinstance(evidence, dict):
        _fail(signature, "EVIDENCE_NOT_OBJECT")
    if evidence.get("sourceBound") is not True:
        _fail(signature, "SOURCE_NOT_BOUND")
    if evidence.get("traceBound") is not True:
        _fail(signature, "TRACE_NOT_BOUND")
    if evidence.get("consumerObserved") is not True:
        _fail(signature, "CONSUMER_NOT_OBSERVED")
    if evidence.get("oracleSatisfied") is not True:
        _fail(signature, "ORACLE_NOT_SATISFIED")


def _make_test(
    row: dict[str, Any], perspective: str, node_id: str
) -> Callable[[], None]:
    def test_node() -> None:
        _assert_acceptance(row, perspective)

    test_node.__name__ = node_id
    test_node.__qualname__ = node_id
    test_node.__doc__ = (
        f"{row['acceptanceId']} {perspective} through {row['productionRoute']}."
    )
    return test_node


_VALUE = _matrix()
assert _VALUE["schemaVersion"] == "NEWS_GRASP_CONSTITUTION_ACCEPTANCE_MATRIX_V1"
assert len(_VALUE["rows"]) == 17
for _ROW in _VALUE["rows"]:
    assert len(_ROW["perspectives"]) == 3
    assert len(_ROW["redNodeIds"]) == 3
    for _PERSPECTIVE, _NODE_ID in zip(
        _ROW["perspectives"], _ROW["redNodeIds"], strict=True
    ):
        globals()[_NODE_ID] = _make_test(_ROW, _PERSPECTIVE, _NODE_ID)

del _NODE_ID, _PERSPECTIVE, _ROW
