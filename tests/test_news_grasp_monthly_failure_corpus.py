from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import pytest

from tools import historical_failure_scenarios as historical


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "constitutional-operations"
MONTHLY_PATH = FIXTURE_ROOT / "monthly-corpus-v1.json"
COMPOUND_PATH = FIXTURE_ROOT / "compound-v1.json"
COMPOUND_TEST_IDS = {
    "same_artifact_repair_plus_residual_red": "test_compound_same_artifact_residual",
    "multi_gate_repair_before_publish_boundary": "test_compound_multigate_before_publish",
    "external_block_plus_local_repair": "test_compound_external_and_local",
    "weekday_inventory_plus_distribution_manifest": "test_compound_inventory_distribution",
    "summary_materialize_missing_plus_downstream_repair_blockers": "test_compound_summary_downstream",
}


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _fail(signature: str, reason: str) -> None:
    pytest.fail(f"{signature}: {reason}", pytrace=False)


def _assert_replay_result(
    *, result: Any, signature: str, replay_id: str, same_lineage_key: str
) -> None:
    if not isinstance(result, dict):
        _fail(signature, "REPLAY_RESULT_NOT_OBJECT")
    expected = {
        "schemaVersion": "NEWS_GRASP_OPERATIONAL_REPLAY_RESULT_V1",
        "replayId": replay_id,
        "sameDailyLineage": True,
        "registeredHandlerOrTypedExternal": True,
        "stateInvariantRetryCount": 0,
        "checkpointModelRerunCount": 0,
        "publicGreenPreserved": True,
        "finiteTerminal": True,
    }
    if same_lineage_key == "sameLineage":
        expected.pop("sameDailyLineage")
        expected["sameLineage"] = True
    for key, expected_value in expected.items():
        if result.get(key) != expected_value:
            _fail(
                signature,
                f"REPLAY_FIELD_MISMATCH:{key}:{result.get(key)!r}!={expected_value!r}",
            )
    if result.get("status") not in {
        "product_complete",
        "external_terminal",
        "major_incident_terminal",
        "user_stopped_terminal",
        "no_progress_terminal",
    }:
        _fail(signature, f"NON_FINITE_STATUS:{result.get('status')!r}")


def _assert_historical(row: dict[str, Any]) -> None:
    issue_date = str(row["issueDate"])
    signature = f"NGC_RED_HIST_{issue_date.replace('-', '')}"
    scenarios = [
        scenario
        for scenario in historical.historical_failure_scenarios()
        if scenario.issue_date == issue_date
    ]
    if not scenarios:
        _fail(signature, f"SCENARIO_MISSING:{row['replayId']}")
    replay = getattr(historical, "replay_operational_failure", None)
    if not callable(replay):
        _fail(signature, "PRODUCTION_REPLAY_CONSUMER_MISSING")
    try:
        result = replay(repo_root=ROOT, fixture=row)
    except Exception as error:  # pragma: no cover - intentional pre-Green boundary
        _fail(signature, f"REPLAY_EXCEPTION:{type(error).__name__}:{error}")
    _assert_replay_result(
        result=result,
        signature=signature,
        replay_id=str(row["replayId"]),
        same_lineage_key="sameDailyLineage",
    )


def _assert_compound(row: dict[str, Any]) -> None:
    fixture_id = str(row["fixtureId"])
    signature = f"NGC_RED_COMPOUND_{fixture_id.upper()}"
    scenarios = {
        scenario.scenario_id: scenario
        for scenario in historical.compound_failure_scenarios()
    }
    if fixture_id not in scenarios:
        _fail(signature, f"SCENARIO_MISSING:{row['replayId']}")
    replay = getattr(historical, "replay_compound_failure", None)
    if not callable(replay):
        _fail(signature, "COMPOUND_REPLAY_CONSUMER_MISSING")
    try:
        result = replay(repo_root=ROOT, fixture=row)
    except Exception as error:  # pragma: no cover - intentional pre-Green boundary
        _fail(signature, f"REPLAY_EXCEPTION:{type(error).__name__}:{error}")
    _assert_replay_result(
        result=result,
        signature=signature,
        replay_id=str(row["replayId"]),
        same_lineage_key="sameLineage",
    )


def _make_historical_test(row: dict[str, Any], node_id: str) -> Callable[[], None]:
    def test_node() -> None:
        _assert_historical(row)

    test_node.__name__ = node_id
    test_node.__qualname__ = node_id
    test_node.__doc__ = f"Replay {row['replayId']} through the production corpus."
    return test_node


def _make_compound_test(row: dict[str, Any], node_id: str) -> Callable[[], None]:
    def test_node() -> None:
        _assert_compound(row)

    test_node.__name__ = node_id
    test_node.__qualname__ = node_id
    test_node.__doc__ = f"Replay compound failure {row['replayId']}."
    return test_node


_MONTHLY = _load(MONTHLY_PATH)
_COMPOUND = _load(COMPOUND_PATH)
assert _MONTHLY["schemaVersion"] == "ONE_MONTH_OPERATIONAL_FAILURE_CORPUS_V1"
assert len(_MONTHLY["rows"]) == 32
assert _COMPOUND["schemaVersion"] == "NEWS_GRASP_COMPOUND_FAILURE_CORPUS_V1"
assert len(_COMPOUND["rows"]) == 5

for _ROW in _MONTHLY["rows"]:
    _NODE_ID = f"test_hist_{str(_ROW['issueDate']).replace('-', '')}"
    globals()[_NODE_ID] = _make_historical_test(_ROW, _NODE_ID)

for _ROW in _COMPOUND["rows"]:
    _NODE_ID = COMPOUND_TEST_IDS[str(_ROW["fixtureId"])]
    globals()[_NODE_ID] = _make_compound_test(_ROW, _NODE_ID)

del _NODE_ID, _ROW
