"""News-Grasp clean-room S0 admission の production validator。"""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Iterable


CONTRACT_SCHEMA = "NEWS_GRASP_CLEANROOM_S0_ADMISSION_V1"
RESULT_SCHEMA = "NEWS_GRASP_CLEANROOM_S0_ADMISSION_RESULT_V1"
CONTRACT_INVALID = "NEWS_GRASP_S0_CONTRACT_INVALID"
ROLE_COLLISION = "NEWS_GRASP_S0_ROLE_COLLISION"
WRITE_LEASE_OVERLAP = "NEWS_GRASP_S0_WRITE_LEASE_OVERLAP"
TRACE_GAP = "NEWS_GRASP_S0_TRACE_GAP"
TRACE_DUPLICATE = "NEWS_GRASP_S0_TRACE_DUPLICATE"
TRACE_ORPHAN = "NEWS_GRASP_S0_TRACE_ORPHAN"
UNKNOWN_NODE = "NEWS_GRASP_S0_UNKNOWN_NODE"
LEASE_SEAL_STALE = "NEWS_GRASP_S0_LEASE_SEAL_STALE"
BASELINE_DRIFT = "NEWS_GRASP_S0_BASELINE_DRIFT"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_TOP_LEVEL_KEYS = frozenset(
    {
        "schemaVersion",
        "slice",
        "roleContexts",
        "writeLeases",
        "leaseSeal",
        "baselineSeal",
        "fileSeal",
        "requirements",
        "requirementViewpointTrace",
        "internalEdgeTrace",
        "plannedNodeCatalog",
    }
)
_EXPECTED_ROLE_CONTEXTS = {
    "test_luna": "/root/playlist_repair_red",
    "implementation_luna": "/root/implementation_luna_canary",
    "sol_codex_reviewer": "/root/s0_independent_review",
}
_EXPECTED_LEASES = {
    "TEST-LEASE-S0-V2": {
        "leaseId": "TEST-LEASE-S0-V2",
        "role": "test_luna",
        "canonicalContext": "/root/playlist_repair_red",
        "worktree": "<PROJECT_FOLDERS>/_worktrees/News-Grasp-cleanroom-test-s0-h1",
        "writes": ["tests/test_news_grasp_cleanroom_s0_contract.py", "tests/fixtures/news_grasp_cleanroom_s0_cases.json"],
    },
    "IMPL-LEASE-P0-V1": {
        "leaseId": "IMPL-LEASE-P0-V1",
        "role": "implementation_luna",
        "canonicalContext": "/root/implementation_luna_canary",
        "writes": ["tools/youtube_podcast/upload_episode.py"],
        "state": "completed",
    },
    "REVIEW-LEASE-S0-V2": {
        "leaseId": "REVIEW-LEASE-S0-V2",
        "role": "sol_codex_reviewer",
        "canonicalContext": "/root/s0_independent_review",
        "writes": [],
    },
}
_KNOWN_WRITE_PATHS = frozenset(
    path.casefold()
    for lease in _EXPECTED_LEASES.values()
    for path in lease["writes"]
)
_SEALED_CONFIG_PATHS = {
    "config/news_grasp_cleanroom_control_s0_v2.json": "controlSha256",
    "config/news_grasp_cleanroom_s0_impact_review_v2.json": "impactSha256",
}


class CleanroomContractError(Exception):
    """S0 admission の fail-closed な typed error。"""

    def __init__(self, reason: str, message: str | None = None) -> None:
        self.reason = reason
        super().__init__(message or reason)


def _invalid(message: str) -> None:
    raise CleanroomContractError(CONTRACT_INVALID, message)


def _canonical_sha256(value: Any) -> str:
    try:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        _invalid(f"canonical JSON is not serializable: {exc}")
    return sha256(payload).hexdigest()


def _require_dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _invalid(f"{label} must be an object")
    return value


def _require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        _invalid(f"{label} must be an array")
    return value


def _normalize_relative_path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        _invalid(f"{label} must be a relative path")
    raw = value.replace("\\", "/")
    if raw.startswith("/") or raw.startswith("//") or re.match(r"^[a-zA-Z]:", raw):
        _invalid(f"{label} must not be absolute")
    parts: list[str] = []
    for segment in raw.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            _invalid(f"{label} must not traverse a parent")
        parts.append(segment.casefold())
    if not parts:
        _invalid(f"{label} must not be empty")
    return "/".join(parts)


def _validate_shape(
    contract: Any,
    *,
    active_slice: Any,
    actual_test_nodes: Iterable[str],
    baseline_commit: Any,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    if not isinstance(contract, dict):
        _invalid("contract must be an object")
    if set(contract) != _TOP_LEVEL_KEYS:
        _invalid("contract has unknown or missing top-level fields")
    if contract.get("schemaVersion") != CONTRACT_SCHEMA:
        _invalid("contract schemaVersion is invalid")
    if not isinstance(active_slice, str) or active_slice != "S0":
        _invalid("active_slice must be S0")
    if contract.get("slice") != active_slice:
        _invalid("contract slice does not match active_slice")
    if not isinstance(baseline_commit, str) or not _COMMIT_RE.fullmatch(baseline_commit):
        _invalid("baseline_commit must be a 40-character lowercase SHA-1")
    if not isinstance(actual_test_nodes, Iterable) or isinstance(actual_test_nodes, (str, bytes, bytearray)):
        _invalid("actual_test_nodes must be an iterable of node names")
    try:
        actual_nodes = tuple(actual_test_nodes)
    except TypeError as exc:
        _invalid(f"actual_test_nodes is not iterable: {exc}")
    if any(not isinstance(node, str) or not node for node in actual_nodes):
        _invalid("actual_test_nodes contains a non-string node")
    for key in ("roleContexts", "writeLeases", "requirements", "requirementViewpointTrace", "internalEdgeTrace", "plannedNodeCatalog"):
        _require_list(contract.get(key), key)
    for key in ("leaseSeal", "baselineSeal", "fileSeal"):
        _require_dict(contract.get(key), key)
    return contract, actual_nodes


def _validate_roles(contract: dict[str, Any]) -> None:
    rows = contract["roleContexts"]
    seen_roles: set[str] = set()
    seen_contexts: set[str] = set()
    observed: dict[str, str] = {}
    for index, row_value in enumerate(rows):
        row = _require_dict(row_value, f"roleContexts[{index}]")
        if set(row) != {"role", "canonicalContext"}:
            _invalid(f"roleContexts[{index}] fields are invalid")
        role = row.get("role")
        context = row.get("canonicalContext")
        if not isinstance(role, str) or not role or not isinstance(context, str) or not context:
            _invalid(f"roleContexts[{index}] contains an invalid value")
        if role in seen_roles or context in seen_contexts:
            raise CleanroomContractError(ROLE_COLLISION, "role or canonicalContext is duplicated")
        seen_roles.add(role)
        seen_contexts.add(context)
        observed[role] = context
    if observed != _EXPECTED_ROLE_CONTEXTS:
        raise CleanroomContractError(ROLE_COLLISION, "role/canonicalContext binding is not canonical")


def _validate_write_leases(contract: dict[str, Any]) -> None:
    rows = contract["writeLeases"]
    seen_paths: dict[str, str] = {}
    for index, row_value in enumerate(rows):
        row = _require_dict(row_value, f"writeLeases[{index}]")
        writes = _require_list(row.get("writes"), f"writeLeases[{index}].writes")
        lease_id = row.get("leaseId")
        if not isinstance(lease_id, str) or not lease_id:
            _invalid(f"writeLeases[{index}].leaseId is invalid")
        for path_index, raw_path in enumerate(writes):
            normalized = _normalize_relative_path(raw_path, f"writeLeases[{index}].writes[{path_index}]")
            if normalized not in _KNOWN_WRITE_PATHS:
                _invalid(f"unknown write path: {raw_path}")
            previous = seen_paths.get(normalized)
            if previous is not None:
                raise CleanroomContractError(
                    WRITE_LEASE_OVERLAP,
                    f"write path {normalized!r} overlaps leases {previous!r} and {lease_id!r}",
                )
            seen_paths[normalized] = lease_id


def _validate_requirements(
    contract: dict[str, Any],
) -> tuple[list[str], dict[str, str], dict[str, tuple[str, ...]]]:
    rows = contract["requirements"]
    if len(rows) != 15:
        _invalid("requirements count is invalid")
    requirement_ids: list[str] = []
    slices: dict[str, str] = {}
    test_nodes: dict[str, tuple[str, ...]] = {}
    for index, row_value in enumerate(rows):
        row = _require_dict(row_value, f"requirements[{index}]")
        if set(row) != {"id", "slice", "acceptance", "itSuite", "testNodes"}:
            _invalid(f"requirements[{index}] fields are invalid")
        requirement_id = row.get("id")
        if not isinstance(requirement_id, str) or not requirement_id:
            _invalid(f"requirements[{index}].id is invalid")
        if requirement_id in slices:
            _invalid("requirements contain a duplicate id")
        for field in ("slice", "acceptance", "itSuite"):
            if not isinstance(row.get(field), str) or not row[field]:
                _invalid(f"requirements[{index}].{field} is invalid")
        nodes = row.get("testNodes")
        if not isinstance(nodes, list) or len(nodes) != 3:
            _invalid(f"requirements[{index}].testNodes must contain exactly three nodes")
        if any(not isinstance(node, str) or not node for node in nodes):
            _invalid(f"requirements[{index}].testNodes contains an invalid node")
        if len(set(nodes)) != len(nodes):
            _invalid(f"requirements[{index}].testNodes contains a duplicate node")
        requirement_ids.append(requirement_id)
        slices[requirement_id] = row["slice"]
        test_nodes[requirement_id] = tuple(nodes)
    expected_ids = [f"NG-A-R{index:02d}" for index in range(1, 16)]
    if requirement_ids != expected_ids:
        _invalid("requirements catalog is not canonical")
    return requirement_ids, slices, test_nodes


def _validate_traces(
    contract: dict[str, Any],
    requirement_ids: list[str],
    requirement_slices: dict[str, str],
) -> set[str]:
    requirement_id_set = set(requirement_ids)
    viewpoint_rows = contract["requirementViewpointTrace"]
    edge_rows = contract["internalEdgeTrace"]
    planned_catalog = contract["plannedNodeCatalog"]
    if len(planned_catalog) != 57 or any(not isinstance(node, str) or not node for node in planned_catalog):
        _invalid("plannedNodeCatalog has an invalid shape")
    if planned_catalog != sorted(planned_catalog) or len(set(planned_catalog)) != len(planned_catalog):
        raise CleanroomContractError(TRACE_DUPLICATE, "plannedNodeCatalog is not sorted and unique")
    planned_nodes = set(planned_catalog)

    viewpoint_counts: dict[str, int] = {requirement_id: 0 for requirement_id in requirement_ids}
    viewpoint_nodes: list[str] = []
    viewpoint_orphans: list[str] = []
    for index, row_value in enumerate(viewpoint_rows):
        row = _require_dict(row_value, f"requirementViewpointTrace[{index}]")
        keys = {"requirementId", "primary_behavior", "adversarial_boundary", "operational_recovery"}
        if set(row) != keys:
            _invalid(f"requirementViewpointTrace[{index}] fields are invalid")
        requirement_id = row.get("requirementId")
        if not isinstance(requirement_id, str) or not requirement_id:
            _invalid(f"requirementViewpointTrace[{index}].requirementId is invalid")
        if requirement_id in viewpoint_counts:
            viewpoint_counts[requirement_id] += 1
        else:
            viewpoint_orphans.append(requirement_id)
        for field in ("primary_behavior", "adversarial_boundary", "operational_recovery"):
            node = row.get(field)
            if not isinstance(node, str) or not node:
                _invalid(f"requirementViewpointTrace[{index}].{field} is invalid")
            viewpoint_nodes.append(node)

    missing_requirements = [requirement_id for requirement_id, count in viewpoint_counts.items() if count == 0]
    if missing_requirements:
        raise CleanroomContractError(TRACE_GAP, "requirement viewpoint trace is missing")

    edge_nodes: list[str] = []
    edge_orphans: list[str] = []
    for index, row_value in enumerate(edge_rows):
        row = _require_dict(row_value, f"internalEdgeTrace[{index}]")
        keys = {"edgeId", "requirementId", "acceptance", "viewpoint", "plannedNode"}
        if set(row) != keys:
            _invalid(f"internalEdgeTrace[{index}] fields are invalid")
        requirement_id = row.get("requirementId")
        if not isinstance(requirement_id, str) or not requirement_id:
            _invalid(f"internalEdgeTrace[{index}].requirementId is invalid")
        if requirement_id in requirement_id_set:
            pass
        else:
            edge_orphans.append(requirement_id)
        for field in ("edgeId", "acceptance", "viewpoint", "plannedNode"):
            if not isinstance(row.get(field), str) or not row[field]:
                _invalid(f"internalEdgeTrace[{index}].{field} is invalid")
        edge_nodes.append(row["plannedNode"])

    all_nodes = viewpoint_nodes + edge_nodes
    node_counts: dict[str, int] = {}
    for node in all_nodes:
        node_counts[node] = node_counts.get(node, 0) + 1
    duplicate_requirements = any(count > 1 for count in viewpoint_counts.values())
    duplicate_nodes = any(count > 1 for count in node_counts.values())
    duplicate_edge_ids = len({row.get("edgeId") for row in edge_rows if isinstance(row, dict)}) != len(edge_rows)
    if duplicate_requirements or duplicate_nodes or duplicate_edge_ids:
        raise CleanroomContractError(TRACE_DUPLICATE, "requirement trace or planned node is duplicated")

    if viewpoint_orphans or edge_orphans:
        raise CleanroomContractError(TRACE_ORPHAN, "trace row refers to a missing requirement")
    unknown_trace_nodes = set(all_nodes) - planned_nodes
    if unknown_trace_nodes:
        raise CleanroomContractError(TRACE_ORPHAN, "trace row refers to an unknown planned node")
    missing_planned_nodes = planned_nodes - set(all_nodes)
    if missing_planned_nodes:
        raise CleanroomContractError(TRACE_GAP, "planned node catalog is not covered by trace")

    active_nodes: set[str] = set()
    for row_value in viewpoint_rows:
        row = row_value
        if requirement_slices[row["requirementId"]] == "S0":
            active_nodes.update(row[field] for field in ("primary_behavior", "adversarial_boundary", "operational_recovery"))
    for row_value in edge_rows:
        row = row_value
        if row["requirementId"] in requirement_slices and requirement_slices[row["requirementId"]] == "S0":
            active_nodes.add(row["plannedNode"])
    return active_nodes


def _validate_actual_nodes(actual_nodes: tuple[str, ...], expected_nodes: tuple[str, ...]) -> None:
    expected_set = set(expected_nodes)
    unknown = [node for node in actual_nodes if node not in expected_set]
    if unknown:
        raise CleanroomContractError(UNKNOWN_NODE, "actual test node is outside the active slice")
    if len(actual_nodes) != len(set(actual_nodes)):
        raise CleanroomContractError(TRACE_DUPLICATE, "actual test node is duplicated")
    if actual_nodes != expected_nodes:
        raise CleanroomContractError(TRACE_GAP, "actual test node set is incomplete or out of order")


def _validate_lease_seal(contract: dict[str, Any]) -> None:
    rows = contract["writeLeases"]
    if len(rows) != len(_EXPECTED_LEASES):
        raise CleanroomContractError(LEASE_SEAL_STALE, "write lease row count is stale")
    observed_ids = [row.get("leaseId") for row in rows if isinstance(row, dict)]
    if len(observed_ids) != len(set(observed_ids)) or set(observed_ids) != set(_EXPECTED_LEASES):
        raise CleanroomContractError(LEASE_SEAL_STALE, "write lease ids are stale")
    for row_value in rows:
        if not isinstance(row_value, dict):
            raise CleanroomContractError(LEASE_SEAL_STALE, "write lease row is stale")
        lease_id = row_value.get("leaseId")
        expected = _EXPECTED_LEASES.get(lease_id)
        if expected is None:
            raise CleanroomContractError(LEASE_SEAL_STALE, "unknown write lease id")
        if set(row_value) != set(expected) | {"leaseSha256"}:
            raise CleanroomContractError(LEASE_SEAL_STALE, "write lease fields are stale")
        if any(row_value.get(key) != value for key, value in expected.items()):
            raise CleanroomContractError(LEASE_SEAL_STALE, "write lease binding is stale")
        lease_hash = row_value.get("leaseSha256")
        if not isinstance(lease_hash, str) or not _SHA256_RE.fullmatch(lease_hash):
            raise CleanroomContractError(LEASE_SEAL_STALE, "write lease hash is stale")
        unsigned = {key: value for key, value in row_value.items() if key != "leaseSha256"}
        if _canonical_sha256(unsigned) != lease_hash:
            raise CleanroomContractError(LEASE_SEAL_STALE, "write lease hash does not match the row")

    lease_seal = contract["leaseSeal"]
    if set(lease_seal) != {
        "schemaVersion",
        "leaseCount",
        "leaseIdSealMapSha256",
        "unknownLeaseCount",
        "duplicateLeaseIdCount",
        "sealMismatchCount",
        "status",
    }:
        raise CleanroomContractError(LEASE_SEAL_STALE, "lease seal fields are stale")
    if lease_seal.get("schemaVersion") != "AGENT_ROLE_LEASE_SEAL_RECEIPT_V1":
        raise CleanroomContractError(LEASE_SEAL_STALE, "lease seal schema is stale")
    if lease_seal.get("leaseCount") != len(rows):
        raise CleanroomContractError(LEASE_SEAL_STALE, "lease seal count is stale")
    lease_id_seal_map = [
        {"leaseId": row["leaseId"], "leaseSha256": row["leaseSha256"]}
        for row in rows
    ]
    if lease_seal.get("leaseIdSealMapSha256") != _canonical_sha256(lease_id_seal_map):
        raise CleanroomContractError(LEASE_SEAL_STALE, "lease id seal map is stale")
    if lease_seal.get("unknownLeaseCount") != 0 or lease_seal.get("duplicateLeaseIdCount") != 0 or lease_seal.get("sealMismatchCount") != 0:
        raise CleanroomContractError(LEASE_SEAL_STALE, "lease seal counters are stale")
    if lease_seal.get("status") != "green":
        raise CleanroomContractError(LEASE_SEAL_STALE, "lease seal status is stale")


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_baseline(contract: dict[str, Any], baseline_commit: str) -> dict[str, Any]:
    baseline = contract["baselineSeal"]
    if set(baseline) != {"generation", "commit", "remoteHead"}:
        raise CleanroomContractError(BASELINE_DRIFT, "baseline seal fields are stale")
    if baseline.get("generation") != "H1" or baseline.get("commit") != baseline_commit or baseline.get("remoteHead") != baseline_commit:
        raise CleanroomContractError(BASELINE_DRIFT, "baseline commit or generation drifted")

    file_seal = contract["fileSeal"]
    if set(file_seal) != {"controlPath", "controlSha256", "impactPath", "impactSha256"}:
        raise CleanroomContractError(BASELINE_DRIFT, "file seal fields are stale")
    root = Path(__file__).resolve().parents[1]
    control_path: Path | None = None
    for path_field, hash_field in (("controlPath", "controlSha256"), ("impactPath", "impactSha256")):
        normalized = _normalize_relative_path(file_seal.get(path_field), f"fileSeal.{path_field}")
        if normalized not in _SEALED_CONFIG_PATHS or _SEALED_CONFIG_PATHS[normalized] != hash_field:
            _invalid(f"unknown sealed file path: {file_seal.get(path_field)!r}")
        expected_hash = file_seal.get(hash_field)
        if not isinstance(expected_hash, str) or not _SHA256_RE.fullmatch(expected_hash):
            raise CleanroomContractError(BASELINE_DRIFT, f"file seal hash is stale: {hash_field}")
        actual_path = root / normalized
        try:
            actual_hash = _sha256_file(actual_path)
        except (OSError, ValueError) as exc:
            raise CleanroomContractError(BASELINE_DRIFT, f"sealed file cannot be read: {normalized}") from exc
        if actual_hash != expected_hash:
            raise CleanroomContractError(BASELINE_DRIFT, f"sealed file hash drifted: {normalized}")
        if hash_field == "controlSha256":
            control_path = actual_path
    if control_path is None:
        raise CleanroomContractError(BASELINE_DRIFT, "sealed control path is missing")
    try:
        with control_path.open("r", encoding="utf-8") as stream:
            control = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CleanroomContractError(BASELINE_DRIFT, "sealed control cannot be loaded") from exc
    if not isinstance(control, dict):
        raise CleanroomContractError(BASELINE_DRIFT, "sealed control is not an object")
    return control


def _validate_semantic_seal(contract: dict[str, Any], control: dict[str, Any]) -> None:
    exact_subtrees = ("requirements", "requirementViewpointTrace", "internalEdgeTrace")
    for key in exact_subtrees:
        if contract.get(key) != control.get(key):
            raise CleanroomContractError(BASELINE_DRIFT, f"sealed control subtree drifted: {key}")
    try:
        sealed_viewpoint_nodes = [
            row[field]
            for row in control["requirementViewpointTrace"]
            for field in ("primary_behavior", "adversarial_boundary", "operational_recovery")
        ]
        sealed_edge_nodes = [row["plannedNode"] for row in control["internalEdgeTrace"]]
        sealed_catalog = sorted(set(sealed_viewpoint_nodes) | set(sealed_edge_nodes))
    except (KeyError, TypeError) as exc:
        raise CleanroomContractError(BASELINE_DRIFT, "sealed control trace mapping is invalid") from exc
    if contract.get("plannedNodeCatalog") != sealed_catalog:
        raise CleanroomContractError(BASELINE_DRIFT, "planned node catalog is not sealed to the control")


def _sealed_active_test_nodes(control: dict[str, Any], active_slice: str) -> tuple[str, ...]:
    try:
        requirements = control["requirements"]
        rows = [row for row in requirements if row["slice"] == active_slice]
        expected = tuple(node for row in rows for node in row["testNodes"])
    except (KeyError, TypeError) as exc:
        raise CleanroomContractError(BASELINE_DRIFT, "sealed requirement testNodes are invalid") from exc
    if not expected:
        raise CleanroomContractError(BASELINE_DRIFT, "sealed active slice has no testNodes")
    return expected


def validate_s0_admission(
    contract: dict,
    *,
    active_slice: str,
    actual_test_nodes: Iterable[str],
    baseline_commit: str,
) -> dict:
    """S0 admission contract を順序付きに検証し、Green projection を返す。"""
    contract_value, actual_nodes = _validate_shape(
        contract,
        active_slice=active_slice,
        actual_test_nodes=actual_test_nodes,
        baseline_commit=baseline_commit,
    )
    _validate_roles(contract_value)
    _validate_write_leases(contract_value)
    requirement_ids, requirement_slices, _test_nodes = _validate_requirements(contract_value)
    _validate_traces(contract_value, requirement_ids, requirement_slices)
    _validate_lease_seal(contract_value)
    control = _validate_baseline(contract_value, baseline_commit)
    _validate_semantic_seal(contract_value, control)
    expected_nodes = _sealed_active_test_nodes(control, active_slice)
    _validate_actual_nodes(actual_nodes, expected_nodes)
    return {
        "schemaVersion": RESULT_SCHEMA,
        "status": "accepted",
        "requirementCount": len(requirement_ids),
        "plannedNodeCount": len(contract_value["plannedNodeCatalog"]),
        "actualNodeCount": len(actual_nodes),
        "roleCollisionCount": 0,
        "writeLeaseIntersectionCount": 0,
        "traceGapCount": 0,
        "staleSealCount": 0,
    }
