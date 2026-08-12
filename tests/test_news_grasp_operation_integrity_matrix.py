from __future__ import annotations

import importlib.util
import hashlib
import json
import os
from pathlib import Path
import sys
from types import ModuleType
from typing import Any, Callable

import pytest


ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = (
    ROOT
    / "tests"
    / "fixtures"
    / "constitutional-operations"
    / "operation-integrity-matrix-v1.json"
)
PERSPECTIVE_SUFFIX = {
    "primary_behavior": "PRIMARY",
    "adversarial_boundary": "BOUNDARY",
    "operational_recovery": "RECOVERY",
}
PERSPECTIVE_NODE_SUFFIX = {
    "primary_behavior": "primary",
    "adversarial_boundary": "boundary",
    "operational_recovery": "recovery",
}


def _matrix() -> dict[str, Any]:
    return json.loads(MATRIX_PATH.read_text(encoding="utf-8"))


def _consumer_root() -> Path:
    value = os.environ.get("NEWS_GRASP_INTEGRITY_CONSUMER_ROOT", "")
    return Path(value).resolve() if value else ROOT.resolve()


def _load_consumer(root: Path) -> ModuleType:
    source = root / "tools" / "news_grasp_constitution.py"
    module_name = f"news_grasp_constitution_integrity_{abs(hash(str(root)))}"
    spec = importlib.util.spec_from_file_location(module_name, source)
    if spec is None or spec.loader is None:
        raise AssertionError(f"CONSUMER_LOAD_SPEC_INVALID:{source}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _fail(signature: str, reason: str) -> None:
    pytest.fail(f"{signature}: {reason}", pytrace=False)


def _assert_integrity(row: dict[str, Any], perspective: str) -> None:
    evaluation_id = str(row["evaluationId"])
    signature = f"NGI_RED_{evaluation_id.replace('-', '')}_{PERSPECTIVE_SUFFIX[perspective]}"
    consumer_root = _consumer_root()
    integration = _matrix()["integrationConsumer"]
    route = consumer_root / str(integration["productionRoute"])
    if not route.is_file():
        _fail(signature, f"PRODUCTION_ROUTE_MISSING:{integration['productionRoute']}")
    if str(integration["consumerMarker"]) not in route.read_text(encoding="utf-8-sig"):
        _fail(signature, f"PRODUCTION_CONSUMER_MARKER_MISSING:{integration['consumerMarker']}")
    observed_route = consumer_root / str(row["productionRoute"])
    if not observed_route.is_file():
        _fail(signature, f"OBSERVED_ROUTE_MISSING:{row['productionRoute']}")
    if str(row["consumerMarker"]) not in observed_route.read_text(encoding="utf-8-sig"):
        _fail(signature, f"OBSERVED_CONSUMER_MARKER_MISSING:{row['consumerMarker']}")

    consumer = _load_consumer(consumer_root)
    evaluator = getattr(consumer, "evaluate_acceptance")
    try:
        result = evaluator(
            repo_root=consumer_root,
            acceptance_id=evaluation_id,
            perspective=perspective,
        )
    except Exception as error:
        _fail(
            signature,
            f"CONSUMER_REJECTED:{type(error).__name__}:{error}",
        )

    expected = {
        "schemaVersion": "NEWS_GRASP_OPERATION_INTEGRITY_RESULT_V1",
        "evaluationId": evaluation_id,
        "perspective": perspective,
        "productionRoute": str(integration["productionRoute"]),
        "consumerSymbol": str(integration["consumerSymbol"]),
        "observedRoute": str(row["productionRoute"]),
        "observedConsumerSymbol": str(row["consumerSymbol"]),
        "stateOwner": str(row["stateOwner"]),
        "status": "Green",
    }
    if not isinstance(result, dict):
        _fail(signature, "CONSUMER_RESULT_NOT_OBJECT")
    for key, expected_value in expected.items():
        if result.get(key) != expected_value:
            _fail(
                signature,
                f"CONSUMER_RESULT_FIELD_MISMATCH:{key}:{result.get(key)!r}!={expected_value!r}",
            )
    if result.get("constitutionClauses") != row["constitutionClauses"]:
        _fail(signature, "CONSTITUTION_BINDING_MISMATCH")
    evidence = result.get("evidence")
    if not isinstance(evidence, dict):
        _fail(signature, "EVIDENCE_NOT_OBJECT")
    for field in (
        "sourceBound",
        "consumerObserved",
        "observedConsumerInvoked",
        "oracleSatisfied",
        "generationBound",
        "selfDeclaredGreenRejected",
    ):
        if evidence.get(field) is not True:
            _fail(signature, f"EVIDENCE_PREDICATE_RED:{field}")
    observed_source_sha256 = hashlib.sha256(observed_route.read_bytes()).hexdigest()
    if evidence.get("observedSourceSha256") != observed_source_sha256:
        _fail(signature, "OBSERVED_SOURCE_HASH_MISMATCH")
    if result.get("oracle") != row["oracles"][perspective]:
        _fail(signature, "ORACLE_BINDING_MISMATCH")


def _make_test(
    row: dict[str, Any], perspective: str, node_id: str
) -> Callable[[], None]:
    def test_node() -> None:
        _assert_integrity(row, perspective)

    test_node.__name__ = node_id
    test_node.__qualname__ = node_id
    test_node.__doc__ = (
        f"{row['evaluationId']} {perspective} through integration consumer, observing "
        f"{row['productionRoute']}::{row['consumerSymbol']}."
    )
    return test_node


_VALUE = _matrix()
assert _VALUE["schemaVersion"] == "NEWS_GRASP_OPERATION_INTEGRITY_MATRIX_V1"
assert len(_VALUE["rows"]) == 14
assert _VALUE["perspectives"] == list(PERSPECTIVE_SUFFIX)
for _ROW in _VALUE["rows"]:
    for _PERSPECTIVE in _VALUE["perspectives"]:
        _NODE_ID = (
            f"test_{_ROW['evaluationId'].lower().replace('-', '')}_"
            f"{PERSPECTIVE_NODE_SUFFIX[_PERSPECTIVE]}"
        )
        globals()[_NODE_ID] = _make_test(_ROW, _PERSPECTIVE, _NODE_ID)

del _NODE_ID, _PERSPECTIVE, _ROW
