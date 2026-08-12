from __future__ import annotations

import re
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any


NGC_A15_primary_behavior = "NGC_A15_primary_behavior"
NGC_A15_adversarial_boundary = "NGC_A15_adversarial_boundary"
NGC_A15_operational_recovery = "NGC_A15_operational_recovery"
PACKET_SCHEMA_VERSION = "LUNA_EXECUTION_PACKET_V2"
STRICT_PACKET_DISCRIMINATOR = "mutationMode"
PACKET_SET_RELATIVE_PATH = Path("config/news_grasp_luna_packets_v1.json")
TASK_CONSTITUTION_BINDING_SCHEMA = "NEWS_GRASP_OPERATIONAL_IMPROVEMENT_BINDING_V1"
STOP_POLICY = "return_to_sol_before_execution"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
_CLAUSE_RE = re.compile(r"^NGC-C\d{2}$")
_TODO_STATUS_PREFIX_RE = re.compile(r"^[☐◉☑⊘■] ")
_MUTATION_MODES = {"product_mutation", "multi_root_mutation", "verification_only"}
_DERIVED_WRITE_COMMANDS = {
    "python -m tools.news_grasp_constitution generate-active-catalog --repo-root .",
    "python -m tools.news_grasp_constitution generate-projections --repo-root .",
}
_RETURN_TO_SOL_CONDITIONS = {
    "write_set_expansion",
    "new_constitution_principle",
    "unregistered_failure_class",
    "public_semantics_change",
    "unresolved_shared_owner",
    "second_e2e_request",
}
_HUMAN_IMPACT = {
    "noFocusTheft": True,
    "noAutoOpen": True,
    "noUserMonitoring": True,
    "persistentPolling": False,
    "rawProcessTermination": False,
}


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
    baseline_commit: str = ""
    baseline_receipt_path: str = ""
    mutation_mode: str = "legacy_v2"
    target_source_hashes: tuple[tuple[str, str], ...] = ()
    derived_write_set: tuple[str, ...] = ()
    shared_owner_write_set: tuple[str, ...] = ()
    constitution_clauses: tuple[str, ...] = ()
    expected_outputs: tuple[str, ...] = ()
    expected_verification_exit: int = 0
    human_impact: dict[str, bool] | None = None
    return_to_sol_conditions: tuple[str, ...] = ()
    task_constitution_admission_sha256: str = ""


def _tuple(
    payload: dict[str, Any], key: str, *, allow_empty: bool = False
) -> tuple[str, ...]:
    value = payload.get(key)
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ValueError(f"LUNA_PACKET_NONEMPTY_LIST_REQUIRED:{key}")
    result = tuple(str(item) for item in value)
    if len(result) != len(set(result)):
        raise ValueError(f"LUNA_PACKET_DUPLICATE_VALUE:{key}")
    return result


def _relative_paths(
    payload: dict[str, Any], key: str, *, allow_empty: bool = False
) -> tuple[str, ...]:
    paths = _tuple(payload, key, allow_empty=allow_empty)
    for raw in paths:
        path = PurePosixPath(raw.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts or ":" in path.parts[0]:
            raise ValueError(f"LUNA_PACKET_PATH_OUTSIDE_PRODUCT:{key}:{raw}")
        if str(path) in {"", "."}:
            raise ValueError(f"LUNA_PACKET_PATH_EMPTY:{key}")
    return paths


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def todo_definition_set_sha256(entries: list[dict[str, Any]]) -> str:
    """可変statusを除外し、append-only ToDo定義集合だけをhash化する。"""

    if not isinstance(entries, list) or not entries:
        raise ValueError("LUNA_PACKET_TODO_DEFINITION_SET_INVALID")
    definitions: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for expected_sequence, row in enumerate(entries, start=1):
        if not isinstance(row, dict):
            raise ValueError("LUNA_PACKET_TODO_DEFINITION_SET_INVALID")
        todo_id = str(row.get("todoId", ""))
        step = str(row.get("step", ""))
        if (
            row.get("sequence") != expected_sequence
            or not re.fullmatch(r"TODO-\d{3}", todo_id)
            or todo_id in seen_ids
            or _TODO_STATUS_PREFIX_RE.match(step) is None
            or f"[{todo_id}]" not in step
        ):
            raise ValueError("LUNA_PACKET_TODO_DEFINITION_SET_INVALID")
        definition = _TODO_STATUS_PREFIX_RE.sub("", step, count=1)
        if not definition:
            raise ValueError("LUNA_PACKET_TODO_DEFINITION_SET_INVALID")
        seen_ids.add(todo_id)
        definitions.append(
            {
                "sequence": expected_sequence,
                "todoId": todo_id,
                "definition": definition,
            }
        )
    return _canonical_sha256(definitions)


def _admit_task_constitution(
    payload: dict[str, Any], repo_root: Path | None
) -> str:
    expected_sha256 = str(payload.get("taskConstitutionBindingSha256", ""))
    if not _SHA256_RE.fullmatch(expected_sha256):
        raise ValueError("LUNA_PACKET_TASK_CONSTITUTION_BINDING_REQUIRED")
    if repo_root is None:
        raise ValueError("LUNA_PACKET_TASK_CONSTITUTION_REPO_REQUIRED")
    packet_set_path = repo_root / PACKET_SET_RELATIVE_PATH
    if not packet_set_path.is_file():
        raise ValueError("LUNA_PACKET_TASK_CONSTITUTION_BINDING_MISSING")
    packet_set = json.loads(packet_set_path.read_text(encoding="utf-8"))
    binding = packet_set.get("operationalImprovementBinding")
    if (
        not isinstance(binding, dict)
        or binding.get("schemaVersion") != TASK_CONSTITUTION_BINDING_SCHEMA
        or _canonical_sha256(binding) != expected_sha256
    ):
        raise ValueError("LUNA_PACKET_TASK_CONSTITUTION_BINDING_DRIFT")
    if (
        "todoLedgerSha256" in binding
        or "deltaPacketSha256" in binding
        or not _SHA256_RE.fullmatch(str(binding.get("todoDefinitionSetSha256", "")))
    ):
        raise ValueError("LUNA_PACKET_TASK_CONSTITUTION_MUTABLE_PROGRESS_BINDING")

    from tools import news_grasp_constitution as constitution_module
    from tools.news_grasp_operational_contract import admit_task_constitution

    constitution = constitution_module.load_constitution(repo_root)
    skill_binding = constitution_module.load_skill_binding(
        repo_root, verify_shared_sources=False
    )
    graph = constitution_module.load_skill_cross_layer_graph(
        repo_root, constitution, skill_binding
    )
    graph_by_id = {str(row["skillId"]): row for row in graph["skills"]}
    skill_ids = tuple(str(value) for value in binding.get("skillIds", []))
    if not skill_ids or not set(skill_ids) <= set(graph_by_id):
        raise ValueError("LUNA_PACKET_TASK_CONSTITUTION_BINDING_INVALID")
    layer_fields = (
        "purposeIds",
        "clauseIds",
        "flowIds",
        "taskIds",
        "consumerRoutes",
        "stateIds",
        "evidenceIds",
    )
    layers = {
        field_name: list(
            dict.fromkeys(
                str(item)
                for skill_id in skill_ids
                for item in graph_by_id[skill_id][field_name]
            )
        )
        for field_name in layer_fields
    }
    if not set(map(str, payload.get("constitutionClauses", []))) <= set(
        layers["clauseIds"]
    ):
        raise ValueError("LUNA_PACKET_TASK_CONSTITUTION_CLAUSE_MISMATCH")
    request = {
        "schemaVersion": "NEWS_GRASP_TASK_CONSTITUTION_REQUEST_V2",
        "taskId": str(payload["todoId"]),
        "durableGoalId": binding.get("durableGoalId"),
        "todoDefinitionSetSha256": binding.get("todoDefinitionSetSha256"),
        "reviewPolicy": binding.get("reviewPolicy"),
        "reviewAttemptCount": binding.get("reviewAttemptCount"),
        "skillIds": list(skill_ids),
        **layers,
        "requirementIds": list(payload.get("requirementIds", [])),
        "acceptanceIds": list(payload.get("acceptanceIds", [])),
        "writeSet": list(
            dict.fromkeys(
                [
                    *payload.get("writeSet", []),
                    *payload.get("derivedWriteSet", []),
                ]
            )
        ),
        "mutationMode": payload.get("mutationMode"),
        "efficiencyCandidates": binding.get("efficiencyCandidates"),
        "selectedCandidateId": binding.get("selectedCandidateId"),
        "unresolvedDecisionIds": list(payload.get("unresolvedDecisionIds", [])),
    }
    admission = admit_task_constitution(request, repo_root=repo_root)
    return str(admission["requestSha256"])


def _resolved_product_path(root: Path, relative: str) -> Path:
    candidate = root / relative
    resolved_root = root.resolve()
    resolved = candidate.resolve(strict=False)
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise ValueError(f"LUNA_PACKET_PATH_OUTSIDE_PRODUCT:writeSet:{relative}")
    return candidate


def _validate_target_hashes(
    payload: dict[str, Any], write_set: tuple[str, ...], repo_root: Path | None
) -> tuple[tuple[str, str], ...]:
    value = payload.get("targetSourceHashes")
    if not isinstance(value, dict) or set(value) != set(write_set):
        raise ValueError("LUNA_PACKET_TARGET_HASH_SET_MISMATCH")
    rows: list[tuple[str, str]] = []
    for relative in write_set:
        expected = str(value[relative])
        if expected != "ABSENT" and not _SHA256_RE.fullmatch(expected):
            raise ValueError(f"LUNA_PACKET_TARGET_HASH_INVALID:{relative}")
        if repo_root is not None:
            candidate = _resolved_product_path(repo_root, relative)
            if expected == "ABSENT":
                if candidate.exists():
                    raise ValueError(f"LUNA_PACKET_TARGET_HASH_DRIFT:{relative}")
            elif not candidate.is_file() or _file_sha256(candidate) != expected:
                raise ValueError(f"LUNA_PACKET_TARGET_HASH_DRIFT:{relative}")
        rows.append((relative, expected))
    return tuple(rows)


def _validate_derived_writes(
    payload: dict[str, Any],
    *,
    write_set: tuple[str, ...],
    target_hashes: tuple[tuple[str, str], ...],
    repo_root: Path | None,
) -> tuple[str, ...]:
    if "derivedWriteSet" not in payload and "derivedWriteAuthorities" not in payload:
        return ()
    derived = _relative_paths(payload, "derivedWriteSet", allow_empty=True)
    authorities = payload.get("derivedWriteAuthorities", [])
    if not derived:
        if authorities:
            raise ValueError("LUNA_PACKET_DERIVED_WRITE_AUTHORITY_INVALID")
        return ()
    if set(derived) & set(write_set) or not isinstance(authorities, list) or not authorities:
        raise ValueError("LUNA_PACKET_DERIVED_WRITE_AUTHORITY_INVALID")
    expected_hashes = dict(target_hashes)
    observed_outputs: list[str] = []
    for authority in authorities:
        if not isinstance(authority, dict) or set(authority) != {
            "command",
            "generatorPath",
            "generatorSha256",
            "outputPaths",
        }:
            raise ValueError("LUNA_PACKET_DERIVED_WRITE_AUTHORITY_INVALID")
        command = str(authority["command"])
        generator_path = str(authority["generatorPath"])
        generator_sha256 = str(authority["generatorSha256"])
        if (
            command not in _DERIVED_WRITE_COMMANDS
            or generator_path not in write_set
            or expected_hashes.get(generator_path) != generator_sha256
        ):
            raise ValueError("LUNA_PACKET_DERIVED_WRITE_AUTHORITY_INVALID")
        output_paths = _relative_paths(authority, "outputPaths")
        observed_outputs.extend(output_paths)
        if repo_root is not None:
            for relative in output_paths:
                _resolved_product_path(repo_root, relative)
    if len(observed_outputs) != len(set(observed_outputs)) or set(observed_outputs) != set(
        derived
    ):
        raise ValueError("LUNA_PACKET_DERIVED_WRITE_AUTHORITY_INVALID")
    return derived


def _validate_baseline(
    payload: dict[str, Any], repo_root: Path | None
) -> tuple[str, str]:
    commit = str(payload.get("baselineCommit", ""))
    if not _COMMIT_RE.fullmatch(commit):
        raise ValueError("LUNA_PACKET_BASELINE_COMMIT_INVALID")
    receipt_path = str(payload.get("baselineReceiptPath", ""))
    holder = {"paths": [receipt_path]}
    normalized = _relative_paths(holder, "paths")[0]
    if repo_root is not None:
        receipt = _resolved_product_path(repo_root, normalized)
        if not receipt.is_file() or _file_sha256(receipt) != str(
            payload.get("baselineSha256", "")
        ):
            raise ValueError("LUNA_PACKET_BASELINE_RECEIPT_DRIFT")
        value = json.loads(receipt.read_text(encoding="utf-8"))
        if value.get("sourceCommit") != commit:
            raise ValueError("LUNA_PACKET_BASELINE_COMMIT_DRIFT")
    return commit, normalized


def _validate_strict_packet(
    payload: dict[str, Any], *, repo_root: Path | None
) -> dict[str, Any]:
    mutation_mode = str(payload.get("mutationMode", ""))
    if mutation_mode not in _MUTATION_MODES:
        raise ValueError("LUNA_PACKET_MUTATION_MODE_INVALID")
    write_set = _relative_paths(payload, "writeSet", allow_empty=True)
    if mutation_mode == "verification_only" and write_set:
        raise ValueError("LUNA_PACKET_VERIFICATION_WRITE_FORBIDDEN")
    if mutation_mode != "verification_only" and not write_set:
        raise ValueError("LUNA_PACKET_MUTATION_WRITE_SET_REQUIRED")
    target_hashes = _validate_target_hashes(payload, write_set, repo_root)
    derived_write_set = _validate_derived_writes(
        payload,
        write_set=write_set,
        target_hashes=target_hashes,
        repo_root=repo_root,
    )

    baseline_commit, baseline_receipt_path = _validate_baseline(payload, repo_root)
    clauses = _tuple(payload, "constitutionClauses")
    if any(not _CLAUSE_RE.fullmatch(value) for value in clauses):
        raise ValueError("LUNA_PACKET_CONSTITUTION_CLAUSE_INVALID")
    expected_outputs = _tuple(payload, "expectedOutputs")
    expected_exit = payload.get("expectedVerificationExit")
    if type(expected_exit) is not int or expected_exit != 0:
        raise ValueError("LUNA_PACKET_VERIFICATION_EXIT_INVALID")
    human_impact = payload.get("humanImpact")
    if human_impact != _HUMAN_IMPACT:
        raise ValueError("LUNA_PACKET_HUMAN_IMPACT_INVALID")
    return_conditions = _tuple(payload, "returnToSolConditions")
    if set(return_conditions) != _RETURN_TO_SOL_CONDITIONS:
        raise ValueError("LUNA_PACKET_RETURN_TO_SOL_CONDITION_INVALID")
    retry = str(payload.get("causalRetryCondition", ""))
    lowered_retry = retry.lower()
    if (
        not retry
        or "always" in lowered_retry
        or "until green" in lowered_retry
        or (
            "none;" not in lowered_retry
            and "changed" not in lowered_retry
            and "changes" not in lowered_retry
        )
    ):
        raise ValueError("LUNA_PACKET_CAUSAL_RETRY_INVALID")

    shared_owner_write_set: tuple[str, ...] = ()
    if mutation_mode == "multi_root_mutation":
        if payload.get("packetSetSelfMutationPolicy") != (
            "parent_goal_owned_canonical_metadata_excluded_from_target_hash"
        ):
            raise ValueError("LUNA_PACKET_SELF_MUTATION_POLICY_REQUIRED")
        shared_owner_write_set = _relative_paths(payload, "sharedOwnerWriteSet")
        hashes = payload.get("sharedOwnerSourceHashes")
        if not isinstance(hashes, dict) or set(hashes) != set(shared_owner_write_set):
            raise ValueError("LUNA_PACKET_SHARED_OWNER_HASH_SET_MISMATCH")
        if any(not _SHA256_RE.fullmatch(str(value)) for value in hashes.values()):
            raise ValueError("LUNA_PACKET_SHARED_OWNER_HASH_INVALID")
        if not payload.get("sharedOwnerRootId") or not _COMMIT_RE.fullmatch(
            str(payload.get("sharedOwnerBaselineCommit", ""))
        ):
            raise ValueError("LUNA_PACKET_SHARED_OWNER_BASELINE_INVALID")
    elif (
        payload.get("sharedOwnerWriteSet")
        or payload.get("sharedOwnerSourceHashes")
        or payload.get("packetSetSelfMutationPolicy")
    ):
        raise ValueError("LUNA_PACKET_SHARED_OWNER_SCOPE_INVALID")

    return {
        "write_set": write_set,
        "target_hashes": target_hashes,
        "derived_write_set": derived_write_set,
        "baseline_commit": baseline_commit,
        "baseline_receipt_path": baseline_receipt_path,
        "mutation_mode": mutation_mode,
        "shared_owner_write_set": shared_owner_write_set,
        "constitution_clauses": clauses,
        "expected_outputs": expected_outputs,
        "expected_exit": expected_exit,
        "human_impact": dict(human_impact),
        "return_conditions": return_conditions,
    }


def validate_packet(
    payload: dict[str, Any], *, repo_root: Path | str | None = None
) -> LunaTaskPacket:
    schema_version = payload.get("schemaVersion")
    if schema_version != PACKET_SCHEMA_VERSION:
        raise ValueError("LUNA_PACKET_SCHEMA_VERSION_INVALID")
    is_strict_packet = STRICT_PACKET_DISCRIMINATOR in payload
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
    root = Path(repo_root).resolve() if repo_root is not None else None
    strict: dict[str, Any] | None = None
    if is_strict_packet:
        strict = _validate_strict_packet(payload, repo_root=root)
        write_set = strict["write_set"]
    else:
        write_set = _relative_paths(
            payload,
            "writeSet",
            allow_empty=payload.get("mutationMode") == "verification_only",
        )
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
    task_constitution_admission_sha256 = (
        _admit_task_constitution(payload, root)
        if is_strict_packet
        else ""
    )

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
        baseline_commit=strict["baseline_commit"] if strict else "",
        baseline_receipt_path=strict["baseline_receipt_path"] if strict else "",
        mutation_mode=strict["mutation_mode"] if strict else "legacy_v2",
        target_source_hashes=strict["target_hashes"] if strict else (),
        derived_write_set=strict["derived_write_set"] if strict else (),
        shared_owner_write_set=strict["shared_owner_write_set"] if strict else (),
        constitution_clauses=strict["constitution_clauses"] if strict else (),
        expected_outputs=strict["expected_outputs"] if strict else (),
        expected_verification_exit=strict["expected_exit"] if strict else 0,
        human_impact=strict["human_impact"] if strict else None,
        return_to_sol_conditions=strict["return_conditions"] if strict else (),
        task_constitution_admission_sha256=task_constitution_admission_sha256,
    )
