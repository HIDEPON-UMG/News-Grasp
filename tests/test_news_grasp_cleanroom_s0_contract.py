"""S0 clean-room admission の sealed Red suite。

S0で実行するのは三つの actual test nodeだけである。45 requirement-viewpoint
nodeと12 internal-edge node、57 planned unionはfixture catalogとして保持し、
将来sliceのproduction behaviorをここへ実装しない。
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib
import json
from pathlib import Path
from typing import Any, Callable

import pytest


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "news_grasp_cleanroom_s0_cases.json"
ACTIVE_SLICE = "S0"
BASELINE_COMMIT = "bf04d4e05b71027ee3cf1ccacc9938e37e5456d8"
ACTUAL_TEST_NODES = (
    "test_s0_role_collision_rejected",
    "test_s0_trace_gap_duplicate_orphan_rejected",
    "test_s0_stale_seal_rejected",
)
REQUIRED_REASON_KEYS = (
    "NEWS_GRASP_S0_ROLE_COLLISION",
    "NEWS_GRASP_S0_WRITE_LEASE_OVERLAP",
    "NEWS_GRASP_S0_TRACE_GAP",
    "NEWS_GRASP_S0_TRACE_DUPLICATE",
    "NEWS_GRASP_S0_TRACE_ORPHAN",
    "NEWS_GRASP_S0_UNKNOWN_NODE",
    "NEWS_GRASP_S0_LEASE_SEAL_STALE",
    "NEWS_GRASP_S0_BASELINE_DRIFT",
)


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _load_fixture() -> dict[str, Any]:
    """fixtureの正本性をproduction importより先に検証する。"""
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert data["schemaVersion"] == "NEWS_GRASP_CLEANROOM_S0_CASES_V1"
    assert data["activeSlice"] == ACTIVE_SLICE
    assert data["baselineCommit"] == BASELINE_COMMIT
    assert tuple(data["actualTestNodes"]) == ACTUAL_TEST_NODES
    assert len(data["actualTestNodes"]) == 3
    assert len(set(data["actualTestNodes"])) == 3
    assert data["sourceArtifact"]["sha256"] == "2b76d2b0aa73d22a43e0279c93cea3eecf3dff0874da6384c6abb74ed61d3a91"
    assert data["impactReceipt"]["sha256"] == "7647135ca8ff015f12e576cde348f29aa531583d0362425e60dbafa836cab5a2"
    assert data["impactReceipt"]["decision"] == "accepted"
    assert data["impactReceipt"]["blockerCount"] == 0
    source = json.loads(
        (FIXTURE_PATH.parents[2] / "config" / "news_grasp_cleanroom_control_s0_v2.json").read_text(
            encoding="utf-8"
        )
    )
    expected_role_contexts = [
        {"role": "test_luna", "canonicalContext": "/root/playlist_repair_red"},
        {"role": "implementation_luna", "canonicalContext": "/root/implementation_luna_canary"},
        {"role": "sol_codex_reviewer", "canonicalContext": "/root/s0_independent_review"},
    ]
    assert data["baseAdmission"]["roleContexts"] == expected_role_contexts
    leases = data["baseAdmission"]["writeLeases"]
    assert len(leases) == 3
    assert leases == source["agentRoleAttestation"]["leases"]
    for lease in leases:
        lease_without_hash = {
            key: value for key, value in lease.items() if key != "leaseSha256"
        }
        assert _canonical_sha256(lease_without_hash) == lease["leaseSha256"]
    lease_id_seal_map = [
        {"leaseId": lease["leaseId"], "leaseSha256": lease["leaseSha256"]}
        for lease in leases
    ]
    assert _canonical_sha256(lease_id_seal_map) == (
        "9d9ae6873a83fdd85926535b48af970f67b3811fe3b5728688076c5bc7f490b7"
    )
    assert data["baseAdmission"]["leaseSeal"] == source["agentRoleAttestation"]["leaseSealReceipt"]

    catalog = data["catalog"]
    source_requirements = [
        {key: row[key] for key in ("id", "slice", "acceptance", "itSuite")}
        for row in source["requirements"]
    ]
    assert catalog["requirements"] == source_requirements
    assert catalog["requirementViewpointTrace"] == source["requirementViewpointTrace"]
    assert catalog["internalEdgeTrace"] == source["internalEdgeTrace"]
    requirements = catalog["requirements"]
    assert len(requirements) == 15
    requirement_ids = [row["id"] for row in requirements]
    assert requirement_ids == [f"NG-A-R{index:02d}" for index in range(1, 16)]
    assert len(set(requirement_ids)) == 15

    viewpoint_trace = catalog["requirementViewpointTrace"]
    assert len(viewpoint_trace) == 15
    assert {row["requirementId"] for row in viewpoint_trace} == set(requirement_ids)
    viewpoint_nodes = [
        row[key]
        for row in viewpoint_trace
        for key in ("primary_behavior", "adversarial_boundary", "operational_recovery")
    ]
    assert len(viewpoint_nodes) == 45
    assert len(set(viewpoint_nodes)) == 45

    edge_trace = catalog["internalEdgeTrace"]
    assert len(edge_trace) == 12
    edge_nodes = [row["plannedNode"] for row in edge_trace]
    assert len(set(edge_nodes)) == 12
    planned_union = set(viewpoint_nodes) | set(edge_nodes)
    assert len(planned_union) == 57
    assert catalog["plannedNodeCatalog"] == sorted(planned_union)
    assert len(catalog["plannedNodeCatalog"]) == 57

    case_ids = [case["id"] for case in data["cases"]]
    assert case_ids == [
        "valid_base",
        "role_context_collision",
        "write_overlap_alias",
        "trace_missing",
        "trace_duplicate",
        "trace_orphan",
        "trace_unknown",
        "lease_seal_stale",
        "baseline_commit_drift",
        "file_seal_drift",
    ]
    assert set(REQUIRED_REASON_KEYS) == {
        case["expectedReason"]
        for case in data["cases"]
        if "expectedReason" in case
    }
    return data


def _build_contract(data: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    contract = deepcopy(data["baseAdmission"])
    for key, value in data["catalog"].items():
        contract[key] = deepcopy(value)
    for mutation in case["mutations"]:
        operation = mutation["op"]
        path = mutation["path"]
        parent: Any = contract
        for token in path[:-1]:
            parent = parent[token]
        leaf = path[-1]
        if operation == "replace":
            parent[leaf] = deepcopy(mutation["value"])
        elif operation == "remove":
            if isinstance(parent, list):
                parent.pop(leaf)
            else:
                parent.pop(leaf)
        elif operation == "duplicate":
            assert isinstance(parent, list)
            parent.insert(leaf + 1, deepcopy(parent[leaf]))
        elif operation == "append":
            assert isinstance(parent, list)
            parent.append(deepcopy(mutation["value"]))
        else:
            raise AssertionError(f"unknown fixture mutation: {operation}")
    return contract


def _cases_by_id(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {case["id"]: case for case in data["cases"]}


def _load_production_validator() -> tuple[Callable[..., Any], type[Exception]]:
    try:
        module = importlib.import_module("tools.news_grasp_cleanroom_contracts")
    except ModuleNotFoundError as exc:
        if exc.name == "tools.news_grasp_cleanroom_contracts":
            pytest.fail("NGC_S0_EXPECTED_RED_PRODUCTION_VALIDATOR_ABSENT", pytrace=False)
        raise
    validator = getattr(module, "validate_s0_admission", None)
    error_type = getattr(module, "CleanroomContractError", None)
    assert callable(validator), "S0 production validator API is missing"
    assert isinstance(error_type, type) and issubclass(error_type, Exception)
    return validator, error_type


def _validate(
    validator: Callable[..., Any],
    contract: dict[str, Any],
    *,
    actual_test_nodes: tuple[str, ...] = ACTUAL_TEST_NODES,
) -> dict[str, Any]:
    result = validator(
        contract,
        active_slice=ACTIVE_SLICE,
        actual_test_nodes=actual_test_nodes,
        baseline_commit=BASELINE_COMMIT,
    )
    assert isinstance(result, dict)
    return result


def _assert_valid_s0_result(result: dict[str, Any]) -> None:
    expected = {
        "schemaVersion": "NEWS_GRASP_CLEANROOM_S0_ADMISSION_RESULT_V1",
        "status": "accepted",
        "requirementCount": 15,
        "plannedNodeCount": 57,
        "actualNodeCount": 3,
        "roleCollisionCount": 0,
        "writeLeaseIntersectionCount": 0,
        "traceGapCount": 0,
        "staleSealCount": 0,
    }
    for key, value in expected.items():
        assert result.get(key) == value, f"valid S0 result drift at {key}: {result!r}"


def _assert_reason(
    validator: Callable[..., Any],
    error_type: type[Exception],
    contract: dict[str, Any],
    expected_reason: str,
    *,
    actual_test_nodes: tuple[str, ...] = ACTUAL_TEST_NODES,
) -> None:
    with pytest.raises(error_type) as caught:
        _validate(validator, contract, actual_test_nodes=actual_test_nodes)
    assert getattr(caught.value, "reason", None) == expected_reason


def test_s0_role_collision_rejected() -> None:
    data = _load_fixture()
    validator, error_type = _load_production_validator()
    cases = _cases_by_id(data)
    _assert_valid_s0_result(_validate(validator, _build_contract(data, cases["valid_base"])))
    _assert_reason(
        validator,
        error_type,
        _build_contract(data, cases["role_context_collision"]),
        "NEWS_GRASP_S0_ROLE_COLLISION",
    )
    _assert_reason(
        validator,
        error_type,
        _build_contract(data, cases["write_overlap_alias"]),
        "NEWS_GRASP_S0_WRITE_LEASE_OVERLAP",
    )


def test_s0_trace_gap_duplicate_orphan_rejected() -> None:
    data = _load_fixture()
    validator, error_type = _load_production_validator()
    cases = _cases_by_id(data)
    for case_id in ("trace_missing", "trace_duplicate", "trace_orphan", "trace_unknown"):
        case = cases[case_id]
        _assert_reason(
            validator,
            error_type,
            _build_contract(data, case),
            case["expectedReason"],
            actual_test_nodes=tuple(case.get("actualTestNodes", data["actualTestNodes"])),
        )


def test_s0_stale_seal_rejected() -> None:
    data = _load_fixture()
    validator, error_type = _load_production_validator()
    cases = _cases_by_id(data)
    for case_id in ("lease_seal_stale", "baseline_commit_drift", "file_seal_drift"):
        case = cases[case_id]
        _assert_reason(validator, error_type, _build_contract(data, case), case["expectedReason"])
