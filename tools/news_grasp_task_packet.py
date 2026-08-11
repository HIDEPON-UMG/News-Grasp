from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any


NGC_A15_primary_behavior = "NGC_A15_primary_behavior"
NGC_A15_adversarial_boundary = "NGC_A15_adversarial_boundary"
NGC_A15_operational_recovery = "NGC_A15_operational_recovery"
PACKET_SCHEMA_VERSION = "LUNA_EXECUTION_PACKET_V2"
STOP_POLICY = "return_to_sol_before_execution"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")


@dataclass(frozen=True)
class LunaTaskPacket:
    packet_id: str
    todo_id: str
    dependency_ids: tuple[str, ...]
    write_set: tuple[str, ...]
    baseline_sha256: str
    requirement_ids: tuple[str, ...]
    acceptance_ids: tuple[str, ...]
    red_node_ids: tuple[str, ...]
    command: str
    expected_failure_signature: str
    artifact_paths: tuple[str, ...]
    local_verification: tuple[str, ...]
    causal_retry_condition: str
    rollback: str
    snapshot: str
    delivery: str
    stop_policy: str
    unresolvedDecisionIds: tuple[str, ...]


def _tuple(payload: dict[str, Any], key: str) -> tuple[str, ...]:
    value = payload.get(key)
    if not isinstance(value, list) or not value:
        raise ValueError(f"LUNA_PACKET_NONEMPTY_LIST_REQUIRED:{key}")
    result = tuple(str(item) for item in value)
    if len(result) != len(set(result)):
        raise ValueError(f"LUNA_PACKET_DUPLICATE_VALUE:{key}")
    return result


def _relative_paths(payload: dict[str, Any], key: str) -> tuple[str, ...]:
    paths = _tuple(payload, key)
    for raw in paths:
        path = PurePosixPath(raw.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts or ":" in path.parts[0]:
            raise ValueError(f"LUNA_PACKET_PATH_OUTSIDE_PRODUCT:{key}:{raw}")
        if str(path) in {"", "."}:
            raise ValueError(f"LUNA_PACKET_PATH_EMPTY:{key}")
    return paths


def validate_packet(payload: dict[str, Any]) -> LunaTaskPacket:
    if payload.get("schemaVersion") != PACKET_SCHEMA_VERSION:
        raise ValueError("LUNA_PACKET_SCHEMA_VERSION_INVALID")
    unresolved = tuple(str(value) for value in payload.get("unresolvedDecisionIds", []))
    if unresolved:
        raise ValueError("LUNA_PACKET_UNRESOLVED_DECISION")
    required = (
        "packetId",
        "todoId",
        "baselineSha256",
        "command",
        "expectedFailureSignature",
        "causalRetryCondition",
        "rollback",
        "snapshot",
        "delivery",
        "stopPolicy",
    )
    missing = [key for key in required if not payload.get(key)]
    if missing:
        raise ValueError("LUNA_PACKET_REQUIRED_FIELD:" + ",".join(missing))
    packet_id = str(payload["packetId"])
    todo_id = str(payload["todoId"])
    if not _ID_RE.fullmatch(packet_id) or not re.fullmatch(r"TODO-\d{3}", todo_id):
        raise ValueError("LUNA_PACKET_ID_INVALID")
    baseline_sha256 = str(payload["baselineSha256"])
    if not _SHA256_RE.fullmatch(baseline_sha256):
        raise ValueError("LUNA_PACKET_BASELINE_HASH_INVALID")
    if payload["stopPolicy"] != STOP_POLICY:
        raise ValueError("LUNA_PACKET_STOP_POLICY_INVALID")
    if payload.get("returnToSolBeforeExecution") is not True:
        raise ValueError("LUNA_PACKET_RETURN_TO_SOL_FLAG_REQUIRED")

    dependency_ids = tuple(str(value) for value in payload.get("dependencyIds", []))
    if any(not re.fullmatch(r"TODO-\d{3}", value) for value in dependency_ids):
        raise ValueError("LUNA_PACKET_DEPENDENCY_ID_INVALID")
    write_set = _relative_paths(payload, "writeSet")
    artifact_paths = _relative_paths(payload, "artifactPaths")
    requirement_ids = _tuple(payload, "requirementIds")
    acceptance_ids = _tuple(payload, "acceptanceIds")
    red_node_ids = _tuple(payload, "redNodeIds")
    local_verification = _tuple(payload, "localVerification")
    if any(not re.fullmatch(r"R\d{2}", value) for value in requirement_ids):
        raise ValueError("LUNA_PACKET_REQUIREMENT_ID_INVALID")
    if any(not re.fullmatch(r"A\d{2}", value) for value in acceptance_ids):
        raise ValueError("LUNA_PACKET_ACCEPTANCE_ID_INVALID")
    if any(not value.startswith("test_") for value in red_node_ids):
        raise ValueError("LUNA_PACKET_RED_NODE_ID_INVALID")

    return LunaTaskPacket(
        packet_id=packet_id,
        todo_id=todo_id,
        dependency_ids=dependency_ids,
        write_set=write_set,
        baseline_sha256=baseline_sha256,
        requirement_ids=requirement_ids,
        acceptance_ids=acceptance_ids,
        red_node_ids=red_node_ids,
        command=str(payload["command"]),
        expected_failure_signature=str(payload["expectedFailureSignature"]),
        artifact_paths=artifact_paths,
        local_verification=local_verification,
        causal_retry_condition=str(payload["causalRetryCondition"]),
        rollback=str(payload["rollback"]),
        snapshot=str(payload["snapshot"]),
        delivery=str(payload["delivery"]),
        stop_policy=str(payload["stopPolicy"]),
        unresolvedDecisionIds=unresolved,
    )
