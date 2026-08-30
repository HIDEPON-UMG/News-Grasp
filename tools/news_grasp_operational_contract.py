from __future__ import annotations

import json
import argparse
import ast
import hashlib
import re
from dataclasses import asdict, dataclass
from pathlib import Path
import sys
from typing import Any, Callable, Mapping


RECOVERY_BRANCHES = {
    "ResumeFromStage",
    "ScheduledRecoveryFull",
    "minimal_unblocker",
    "major_incident_fail_closed",
}
OPERATIONAL_DESIGN_FIELDS = (
    "owner",
    "trigger",
    "actor",
    "entryGate",
    "executionPath",
    "states",
    "statePredicate",
    "outcomes",
    "evidence",
    "recovery",
    "maintenance",
    "contractTest",
    "operationalCost",
)
OPERATIONAL_TRUTH_ISSUER = "tools.audit_recovery_control.actual_observer"
COMPLETION_AUTHORITY_ISSUER = "tools.audit_recovery_control"
VERIFIED_COMPLETION_ISSUER = "tools.audit_recovery_control.actual_verifiers"
COMPLETION_FIELDS = (
    "quality",
    "distributionManifest",
    "publishStatus",
    "publicSurface",
    "primaryPodcast",
    "deepDivePodcast",
    "notification",
    "runnerState",
)
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
ROOT = Path(__file__).resolve().parents[1]
OPERATION_CONSUMPTION_ROOT: Path | None = None
OPERATION_DECISION_GRAPH_RELATIVE_PATH = Path(
    "config/news_grasp_operation_decision_graph_v1.json"
)
OPERATION_DECISION_GRAPH_SCHEMA_VERSION = "NEWS_GRASP_OPERATION_DECISION_GRAPH_V1"
OPERATION_DECISION_SCHEMA_VERSION = "NEWS_GRASP_OPERATION_DECISION_V1"
EXPECTED_OPERATION_TERMINALS = frozenset(
    {
        "success",
        "recovery_completed",
        "operation_deferred",
        "user_stopped",
        "major_incident",
        "design_escape",
        "no_progress",
    }
)
POST_PUBLIC_GREEN_ALLOWED_OPERATIONS = (
    "finalizer_exact_args_replay",
    "receipt_reseal",
    "completion_guard",
    "verify_public_surface",
    "final_report",
)
POST_PUBLIC_CLOSEOUT_BLOCKER = "post_public_closeout_blocker"
NG_RC_06_POST_PUBLIC_GREEN_CLOSEOUT = "NG_RC_06_POST_PUBLIC_GREEN_CLOSEOUT"


def require_post_public_green_operation(operation: str) -> str:
    """Public Green後の未知・再生成operationをsingle ownerでfail-closeする。"""

    normalized = str(operation or "").strip()
    if normalized not in POST_PUBLIC_GREEN_ALLOWED_OPERATIONS:
        raise ValueError(f"{POST_PUBLIC_CLOSEOUT_BLOCKER}:{normalized or 'missing'}")
    return normalized


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


# Scheduled high-cost admission receipts are product-local authority.  Keep the
# schema contracts here (rather than trusting the global broker's shape) so all
# callers validate the same canonical bytes before copying or launching work.
SCHEDULED_ADMISSION_SCHEMAS: dict[str, frozenset[str]] = {
    "HIGH_COST_SCHEDULED_OPERATION_ADMISSION_V1": frozenset(
        {
            "attemptReservation",
            "authorityKind",
            "issueDate",
            "latestActualUserEventHash",
            "maxExternalModelCalls",
            "operationAuthoritySha256",
            "operationKind",
            "productId",
            "receiptSha256",
            "schemaVersion",
            "taskIdentity",
            "taskState",
        }
    ),
    "HIGH_COST_SCHEDULED_RECOVERY_CONTINUATION_V1": frozenset(
        {
            "allowedModelRoutes",
            "attemptReservation",
            "authorityKind",
            "continuationEventSequence",
            "issueDate",
            "latestActualUserEventHash",
            "maxExternalModelCalls",
            "operationAuthoritySha256",
            "operationKind",
            "productId",
            "receiptSha256",
            "resumeStage",
            "schemaVersion",
            "sourceAdmissionReceiptSha256",
            "sourceRunId",
            "sourceRunnerStateSha256",
            "sourceTerminalStatus",
            "taskIdentity",
            "taskState",
        }
    ),
    "HIGH_COST_SCHEDULED_INCIDENT_REPAIR_V1": frozenset(
        {
            "allowedArtifactHashes",
            "allowedModelRoutes",
            "attemptReservation",
            "authorityKind",
            "incidentBudgetEventSequence",
            "issueDate",
            "latestActualUserEventHash",
            "maxExternalModelCalls",
            "operationAuthoritySha256",
            "operationKind",
            "productId",
            "receiptSha256",
            "schemaVersion",
            "sourceAdmissionReceiptSha256",
            "sourceRunId",
            "sourceRunnerStateSha256",
            "sourceTerminalStatus",
            "taskIdentity",
            "taskState",
        }
    ),
}
SCHEDULED_ADMISSION_BASE_SCHEMAS = frozenset(
    {
        "HIGH_COST_SCHEDULED_OPERATION_ADMISSION_V1",
        "HIGH_COST_SCHEDULED_RECOVERY_CONTINUATION_V1",
        "HIGH_COST_SCHEDULED_INCIDENT_REPAIR_V1",
    }
)
SCHEDULED_ADMISSION_MAX_MODEL_CALLS = 64
SCHEDULED_ADMISSION_MAX_EVENT_SEQUENCE = 1_000_000
SCHEDULED_ADMISSION_MAX_TEXT = 256
SCHEDULED_ADMISSION_RESUME_STAGES = frozenset(
    {
        "deepdive",
        "post-daily-quality",
        "post-deepdive",
        "generation-quality-repair",
        "post-reporter",
        "editor",
    }
)
SCHEDULED_ADMISSION_RESERVATION_KEYS = frozenset(
    {"attemptId", "eventSequence", "idempotent"}
)


def _scheduled_admission_invalid() -> ValueError:
    return ValueError("HIGH_COST_SCHEDULED_ADMISSION_INVALID")


def _scheduled_admission_hex(value: object, *, length: int = 64) -> bool:
    return isinstance(value, str) and re.fullmatch(rf"[0-9a-f]{{{length}}}", value) is not None


def _scheduled_admission_text(value: object, *, maximum: int = SCHEDULED_ADMISSION_MAX_TEXT) -> bool:
    return isinstance(value, str) and bool(value) and len(value) <= maximum


def _validate_scheduled_admission_body(
    value: object,
    *,
    expected_operation_kind: str,
    expected_issue_date: str,
    expected_operation_authority_sha256: str,
    require_receipt: bool,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _scheduled_admission_invalid()
    schema = value.get("schemaVersion")
    expected_keys = SCHEDULED_ADMISSION_SCHEMAS.get(str(schema))
    if not require_receipt and expected_keys is not None:
        expected_keys = expected_keys - {"receiptSha256"}
    if expected_keys is None or set(value) != expected_keys:
        raise _scheduled_admission_invalid()
    if (
        value.get("productId") != "News-Grasp"
        or value.get("authorityKind") != "scheduled_news_grasp"
        or value.get("operationKind") != expected_operation_kind
        or value.get("issueDate") != expected_issue_date
        or not isinstance(expected_issue_date, str)
        or re.fullmatch(r"20\d{2}-\d{2}-\d{2}", expected_issue_date) is None
        or value.get("operationAuthoritySha256") != expected_operation_authority_sha256
        or not _scheduled_admission_hex(expected_operation_authority_sha256)
        or value.get("taskState") != "running"
        or not _scheduled_admission_hex(value.get("taskIdentity"))
        or not _scheduled_admission_hex(value.get("latestActualUserEventHash"))
    ):
        raise _scheduled_admission_invalid()
    max_calls = value.get("maxExternalModelCalls")
    if (
        isinstance(max_calls, bool)
        or not isinstance(max_calls, int)
        or max_calls <= 0
        or max_calls > SCHEDULED_ADMISSION_MAX_MODEL_CALLS
    ):
        raise _scheduled_admission_invalid()
    reservation = value.get("attemptReservation")
    if (
        not isinstance(reservation, dict)
        or set(reservation) != SCHEDULED_ADMISSION_RESERVATION_KEYS
        or not _scheduled_admission_text(reservation.get("attemptId"))
        or isinstance(reservation.get("eventSequence"), bool)
        or not isinstance(reservation.get("eventSequence"), int)
        or reservation["eventSequence"] <= 0
        or reservation["eventSequence"] > SCHEDULED_ADMISSION_MAX_EVENT_SEQUENCE
        or reservation.get("idempotent") is not False
    ):
        raise _scheduled_admission_invalid()

    if schema == "HIGH_COST_SCHEDULED_RECOVERY_CONTINUATION_V1":
        routes = value.get("allowedModelRoutes")
        sequence = value.get("continuationEventSequence")
        if (
            not isinstance(routes, list)
            or len(routes) > 32
            or any(not _scheduled_admission_text(route, maximum=128) for route in routes)
            or len(set(routes)) != len(routes)
            or isinstance(sequence, bool)
            or not isinstance(sequence, int)
            or sequence <= 0
            or sequence > SCHEDULED_ADMISSION_MAX_EVENT_SEQUENCE
            or value.get("resumeStage") not in SCHEDULED_ADMISSION_RESUME_STAGES
        ):
            raise _scheduled_admission_invalid()
    elif schema == "HIGH_COST_SCHEDULED_INCIDENT_REPAIR_V1":
        routes = value.get("allowedModelRoutes")
        artifacts = value.get("allowedArtifactHashes")
        sequence = value.get("incidentBudgetEventSequence")
        if (
            not isinstance(routes, list)
            or not routes
            or len(routes) > 32
            or any(not _scheduled_admission_text(route, maximum=128) for route in routes)
            or len(set(routes)) != len(routes)
            or not isinstance(artifacts, dict)
            or not artifacts
            or len(artifacts) > 256
            or any(
                not _scheduled_admission_text(path, maximum=512)
                or not _scheduled_admission_hex(digest)
                for path, digest in artifacts.items()
            )
            or isinstance(sequence, bool)
            or not isinstance(sequence, int)
            or sequence <= 0
            or sequence > SCHEDULED_ADMISSION_MAX_EVENT_SEQUENCE
        ):
            raise _scheduled_admission_invalid()

    if schema != "HIGH_COST_SCHEDULED_OPERATION_ADMISSION_V1":
        if (
            not _scheduled_admission_hex(value.get("sourceAdmissionReceiptSha256"))
            or not _scheduled_admission_hex(value.get("sourceRunnerStateSha256"))
            or not _scheduled_admission_hex(value.get("sourceRunId"), length=32)
            or not _scheduled_admission_text(value.get("sourceTerminalStatus"))
        ):
            raise _scheduled_admission_invalid()

    if require_receipt:
        receipt = value.get("receiptSha256")
        body = {key: item for key, item in value.items() if key != "receiptSha256"}
        if not _scheduled_admission_hex(receipt) or receipt != _sha(body):
            raise _scheduled_admission_invalid()
    return dict(value)


def validate_scheduled_admission_receipt(
    value: object,
    *,
    expected_operation_kind: str,
    expected_issue_date: str,
    expected_operation_authority_sha256: str,
) -> dict[str, Any]:
    """scheduled/recovery/incidentのsealed receiptSha256をexact schemaで検証する。"""

    return _validate_scheduled_admission_body(
        value,
        expected_operation_kind=expected_operation_kind,
        expected_issue_date=expected_issue_date,
        expected_operation_authority_sha256=expected_operation_authority_sha256,
        require_receipt=True,
    )


def seal_fresh_broker_admission(
    value: object,
    *,
    expected_operation_kind: str,
    expected_issue_date: str,
    expected_operation_authority_sha256: str,
) -> dict[str, Any]:
    """fresh brokerの未seal bodyを一度だけcanonical JSON hashで封印する。"""

    if not isinstance(value, dict) or "receiptSha256" in value:
        raise _scheduled_admission_invalid()
    _validate_scheduled_admission_body(
        value,
        expected_operation_kind=expected_operation_kind,
        expected_issue_date=expected_issue_date,
        expected_operation_authority_sha256=expected_operation_authority_sha256,
        require_receipt=False,
    )
    sealed = dict(value)
    sealed["receiptSha256"] = _sha(value)
    return validate_scheduled_admission_receipt(
        sealed,
        expected_operation_kind=expected_operation_kind,
        expected_issue_date=expected_issue_date,
        expected_operation_authority_sha256=expected_operation_authority_sha256,
    )


def validate_operational_truth_receipt(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("OPERATIONAL_TRUTH_RECEIPT_INVALID")
    body = {key: item for key, item in value.items() if key != "receiptSha256"}
    if (
        body.get("schemaVersion") != "NEWS_GRASP_OPERATIONAL_TRUTH_V1"
        or body.get("issuer") != OPERATIONAL_TRUTH_ISSUER
        or value.get("receiptSha256") != _sha(body)
        or not isinstance(body.get("stopPointKnown"), bool)
        or not isinstance(body.get("scheduledAttemptReachedRunner"), bool)
        or not isinstance(body.get("artifactDelta"), dict)
    ):
        raise ValueError("OPERATIONAL_TRUTH_RECEIPT_INVALID")
    delta = body["artifactDelta"]
    if (
        not isinstance(delta.get("exists"), bool)
        or not isinstance(delta.get("manifestSha256"), str)
        or len(delta["manifestSha256"]) != 64
    ):
        raise ValueError("OPERATIONAL_TRUTH_RECEIPT_INVALID")
    return body


def validate_completion_authority_receipt(
    value: object, *, issue_date: str
) -> dict[str, Any]:
    """immutable public Green authorityをseal・日付・lineageまで検証する。"""
    if not isinstance(value, dict):
        raise ValueError("AUDIT_COMPLETION_AUTHORITY_INVALID")
    authority_body = {
        key: item for key, item in value.items() if key != "receiptSha256"
    }
    completion = authority_body.get("completionEvidence")
    if not isinstance(completion, dict):
        raise ValueError("AUDIT_COMPLETION_AUTHORITY_INVALID")
    completion_body = {
        key: item for key, item in completion.items() if key != "receiptSha256"
    }
    checks = completion_body.get("checks")
    evidence = completion_body.get("evidenceSha256")
    if (
        authority_body.get("schemaVersion")
        not in {"COMPLETION_AUTHORITY_V1", "COMPLETION_AUTHORITY_V2"}
        or authority_body.get("issuer") != COMPLETION_AUTHORITY_ISSUER
        or authority_body.get("issueDate") != issue_date
        or value.get("receiptSha256") != _sha(authority_body)
        or not str(authority_body.get("completionAuthorityId") or "")
        or authority_body.get("completionEvidenceSha256")
        != completion.get("receiptSha256")
        or SHA256_PATTERN.fullmatch(
            str(authority_body.get("completionEvidenceSha256") or "")
        )
        is None
        or SHA256_PATTERN.fullmatch(
            str(authority_body.get("decisionReceiptSha256") or "")
        )
        is None
        or authority_body.get("firstVerifiedTerminal")
        not in {"audit_normal_green", "audit_recovered_green"}
        or completion_body.get("schemaVersion")
        != "SAME_DATE_COMPLETION_EVIDENCE_V1"
        or completion_body.get("issuer") != VERIFIED_COMPLETION_ISSUER
        or completion_body.get("issueDate") != issue_date
        or completion_body.get("publishStatusIssueDate") != issue_date
        or completion.get("receiptSha256") != _sha(completion_body)
        or completion_body.get("verificationStatus") not in {None, "verified_green"}
        or completion_body.get("publicCompletionStatus") not in {None, "green"}
        or (
            authority_body.get("schemaVersion") == "COMPLETION_AUTHORITY_V1"
            and completion_body.get("nextRunReadinessStatus") not in {None, "green"}
        )
        or not isinstance(checks, dict)
        or not isinstance(evidence, dict)
        or not all(
            checks.get(field) is True
            and SHA256_PATTERN.fullmatch(str(evidence.get(field) or "")) is not None
            for field in COMPLETION_FIELDS
        )
    ):
        raise ValueError("AUDIT_COMPLETION_AUTHORITY_INVALID")

    artifact_root = str(completion_body.get("artifactRoot") or "")
    ops_root = str(completion_body.get("opsRoot") or "")
    run_intent = str(completion_body.get("runIntent") or "")
    run_id = str(completion_body.get("runId") or "")
    if not artifact_root or not ops_root or not run_intent:
        raise ValueError("AUDIT_COMPLETION_AUTHORITY_INVALID")
    daily_root_id = hashlib.sha256(
        f"News-Grasp|{issue_date}|{artifact_root.casefold()}|{ops_root.casefold()}".encode(
            "utf-8"
        )
    ).hexdigest()
    root_operation_id = hashlib.sha256(
        f"{daily_root_id}|{run_id}|root-operation".encode("utf-8")
    ).hexdigest()
    producer_operation_id = hashlib.sha256(
        f"{root_operation_id}|producer|{run_intent}".encode("utf-8")
    ).hexdigest()
    lineage_receipt_sha256 = hashlib.sha256(
        (
            "NEWS_GRASP_PRODUCER_LINEAGE_V1|"
            f"{issue_date}|{artifact_root.casefold()}|{ops_root.casefold()}|"
            f"{daily_root_id}|{root_operation_id}|{producer_operation_id}|"
            f"{run_intent}|{run_id}"
        ).encode("utf-8")
    ).hexdigest()
    expected_lineage = {
        "dailyRootId": daily_root_id,
        "rootOperationId": root_operation_id,
        "producerDailyRootId": daily_root_id,
        "producerRootOperationId": root_operation_id,
        "producerRunIntent": run_intent,
        "verifierRunIntent": run_intent,
        "producerOperationId": producer_operation_id,
        "lineageReceiptSha256": lineage_receipt_sha256,
        "verifierOperationId": hashlib.sha256(
            f"{root_operation_id}|verifier|{run_intent}".encode("utf-8")
        ).hexdigest(),
    }
    if any(
        completion_body.get(field) != expected
        for field, expected in expected_lineage.items()
    ):
        raise ValueError("AUDIT_COMPLETION_AUTHORITY_INVALID")
    return dict(value)


def select_recovery_branch_from_truth(value: object) -> str:
    truth = validate_operational_truth_receipt(value)
    delta = truth["artifactDelta"]
    if not truth["stopPointKnown"]:
        if truth["scheduledAttemptReachedRunner"] or delta["exists"] is True:
            return "major_incident_fail_closed"
        return "ScheduledRecoveryFull"
    if truth.get("minimalUnblockerReceiptSha256"):
        if len(str(truth["minimalUnblockerReceiptSha256"])) != 64:
            raise ValueError("MINIMAL_UNBLOCKER_RECEIPT_INVALID")
        return "minimal_unblocker"
    if delta["exists"] is True and truth.get("stageCheckpointReceiptSha256"):
        if len(str(truth["stageCheckpointReceiptSha256"])) != 64:
            raise ValueError("STAGE_CHECKPOINT_RECEIPT_INVALID")
        return "ResumeFromStage"
    if delta["exists"] is True:
        return "major_incident_fail_closed"
    return "ScheduledRecoveryFull"


def bind_outcome_target(
    payload: dict[str, Any], decision: dict[str, Any]
) -> dict[str, Any]:
    result = dict(decision)
    incomplete = result.get("publicStatus") != "green"
    result["outcomeTarget"] = "same_day_public_outcome"
    result["completion"] = not incomplete
    if incomplete and not result.get("selectedWorkOrder"):
        result["selectedWorkOrder"] = (
            "major_incident_continuation"
            if result.get("terminal") == "audit_major_incident_open"
            else "public_outcome_first"
        )
    return result


def map_quality_issue_to_work_order(
    payload: dict[str, Any], decision: dict[str, Any]
) -> dict[str, Any]:
    result = dict(decision)
    result["sourceIssueId"] = str(result.get("reasonCode") or "UNKNOWN")
    result["repairWorkOrder"] = str(
        result.get("selectedWorkOrder") or "public_outcome_first"
    )
    result["reverifyGate"] = "same_date_same_intent_public_completion"
    return result


def validate_operation_decision_graph(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("OPERATION_DECISION_GRAPH_INVALID")
    if (
        value.get("schemaVersion") != OPERATION_DECISION_GRAPH_SCHEMA_VERSION
        or value.get("productId") != "News-Grasp"
        or value.get("initialState") != "admitted"
    ):
        raise ValueError("OPERATION_DECISION_GRAPH_INVALID")
    states = value.get("states")
    transitions = value.get("transitions")
    terminal_ids = value.get("terminalStateIds")
    if not all(isinstance(rows, list) and rows for rows in (states, transitions, terminal_ids)):
        raise ValueError("OPERATION_DECISION_GRAPH_INVALID")
    state_ids = [str(row.get("stateId", "")) for row in states]
    if len(state_ids) != len(set(state_ids)) or not all(state_ids):
        raise ValueError("OPERATION_DECISION_GRAPH_STATE_INVALID")
    state_terminal = {
        str(row["stateId"]): row.get("terminal") is True
        for row in states
    }
    terminal_set = set(map(str, terminal_ids))
    if terminal_set != EXPECTED_OPERATION_TERMINALS or terminal_set != {
        state_id for state_id, terminal in state_terminal.items() if terminal
    }:
        raise ValueError("OPERATION_DECISION_GRAPH_TERMINAL_SET_INVALID")

    transition_index: dict[tuple[str, str], str] = {}
    outgoing: dict[str, set[str]] = {state_id: set() for state_id in state_ids}
    for row in transitions:
        source = str(row.get("from", ""))
        event = str(row.get("event", ""))
        target = str(row.get("to", ""))
        if source not in state_terminal or target not in state_terminal or not event:
            raise ValueError("OPERATION_DECISION_GRAPH_TRANSITION_INVALID")
        if source == target:
            raise ValueError("OPERATION_DECISION_GRAPH_CYCLE_INVALID")
        if state_terminal[source]:
            raise ValueError("OPERATION_DECISION_GRAPH_TERMINAL_OUTBOUND_INVALID")
        key = (source, event)
        if key in transition_index:
            raise ValueError("OPERATION_DECISION_GRAPH_EVENT_DUPLICATE")
        transition_index[key] = target
        outgoing[source].add(target)

    nonterminal_ids = {
        state_id for state_id, terminal in state_terminal.items() if not terminal
    }
    for state_id in nonterminal_ids:
        if not outgoing[state_id] or (state_id, "user_stop") not in transition_index:
            raise ValueError("OPERATION_DECISION_GRAPH_NONTERMINAL_INCOMPLETE")
        if (state_id, "*") not in transition_index:
            raise ValueError("OPERATION_DECISION_GRAPH_UNKNOWN_ROUTE_MISSING")
    forbidden_event_parts = ("todo", "goal", "pending")
    if any(
        any(part in event.casefold() for part in forbidden_event_parts)
        for _, event in transition_index
    ):
        raise ValueError("OPERATION_DECISION_GRAPH_IMPLICIT_REENTRY_FORBIDDEN")

    visiting: set[str] = set()
    visited: set[str] = set()

    def assert_acyclic(state_id: str) -> None:
        if state_id in visited or state_terminal[state_id]:
            return
        if state_id in visiting:
            raise ValueError("OPERATION_DECISION_GRAPH_CYCLE_INVALID")
        visiting.add(state_id)
        for target in outgoing[state_id]:
            assert_acyclic(target)
        visiting.remove(state_id)
        visited.add(state_id)

    assert_acyclic(str(value["initialState"]))
    if visited != nonterminal_ids:
        raise ValueError("OPERATION_DECISION_GRAPH_ORPHAN_STATE")

    depth_cache: dict[str, int] = {}

    def max_depth(state_id: str) -> int:
        if state_terminal[state_id]:
            return 0
        if state_id not in depth_cache:
            depth_cache[state_id] = 1 + max(
                max_depth(target) for target in outgoing[state_id]
            )
        return depth_cache[state_id]

    actual_max_depth = max_depth(str(value["initialState"]))
    if value.get("maxTransitionDepth") != actual_max_depth:
        raise ValueError("OPERATION_DECISION_GRAPH_DEPTH_INVALID")
    depth_from_initial = {str(value["initialState"]): 0}
    frontier = [str(value["initialState"])]
    while frontier:
        source = frontier.pop(0)
        for target in outgoing[source]:
            candidate_depth = depth_from_initial[source] + 1
            if target not in depth_from_initial or candidate_depth < depth_from_initial[target]:
                depth_from_initial[target] = candidate_depth
                frontier.append(target)
    if set(depth_from_initial) != set(state_ids):
        raise ValueError("OPERATION_DECISION_GRAPH_ORPHAN_STATE")
    return {
        **value,
        "transitionIndex": transition_index,
        "stateTerminal": state_terminal,
        "actualMaxTransitionDepth": actual_max_depth,
        "stateDepthFromInitial": depth_from_initial,
        "graphSha256": _sha(value),
    }


def load_operation_decision_graph(
    repo_root: Path | str = ROOT,
) -> dict[str, Any]:
    root = Path(repo_root).resolve(strict=True)
    path = (root / OPERATION_DECISION_GRAPH_RELATIVE_PATH).resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file() or path.is_symlink():
        raise ValueError("OPERATION_DECISION_GRAPH_PATH_INVALID")
    return validate_operation_decision_graph(
        json.loads(path.read_text(encoding="utf-8"))
    )


def _operation_decision_result(
    *,
    graph: dict[str, Any],
    state_id: str,
    event: str,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    terminal = bool(graph["stateTerminal"][state_id])
    result = {
        "schemaVersion": OPERATION_DECISION_SCHEMA_VERSION,
        "operationState": state_id,
        "terminal": terminal,
        "terminalClass": state_id if terminal else None,
        "operationEvent": event,
        "transitionCount": graph["stateDepthFromInitial"][state_id],
        "maxTransitionDepth": graph["actualMaxTransitionDepth"],
        "decisionGraphSha256": graph["graphSha256"],
        "mutationCount": 0,
    }
    if extra:
        result.update(dict(extra))
    return result


def _transition_from_operation_event(
    payload: Mapping[str, Any],
    decision: Mapping[str, Any],
) -> dict[str, Any]:
    graph = load_operation_decision_graph(ROOT)
    current = str(payload.get("operationState") or graph["initialState"])
    event = str(payload.get("operationEvent") or "")
    if current not in graph["stateTerminal"] or not event:
        raise ValueError("OPERATION_DECISION_INPUT_INVALID")
    if current == "operation_deferred" and event == "fresh_external_authority":
        lineage_id = str(payload.get("dailyOperationLineageId") or "")
        previous_hash = str(payload.get("previousExternalAuthoritySha256") or "")
        current_hash = str(payload.get("externalAuthoritySha256") or "")
        if (
            not lineage_id
            or SHA256_PATTERN.fullmatch(previous_hash) is None
            or SHA256_PATTERN.fullmatch(current_hash) is None
            or previous_hash == current_hash
        ):
            raise ValueError("OPERATION_DECISION_EXTERNAL_AUTHORITY_NOT_FRESH")
        from tools.news_grasp_execution_governance import consume_once

        consumption = consume_once(
            repo_root=ROOT,
            ledger_root=OPERATION_CONSUMPTION_ROOT,
            kind="external_authority_reentry",
            key_parts=(lineage_id, previous_hash, current_hash),
        )
        if consumption["consumed"] is not True:
            raise ValueError("OPERATION_DECISION_REENTRY_ALREADY_CONSUMED")
        return _operation_decision_result(
            graph=graph,
            state_id="admitted",
            event=event,
            extra={
                **decision,
                "dailyOperationLineageId": lineage_id,
                "externalAuthoritySha256": current_hash,
                "reentryConsumed": True,
                "reentryReason": "fresh_external_authority_same_lineage",
                "consumptionKeySha256": consumption["consumptionKeySha256"],
            },
        )
    if graph["stateTerminal"][current]:
        return _operation_decision_result(
            graph=graph,
            state_id=current,
            event=event,
            extra={**decision, "terminalImmutable": True},
        )
    target = graph["transitionIndex"].get((current, event))
    if target is None:
        target = graph["transitionIndex"][(current, "*")]
    return _operation_decision_result(
        graph=graph,
        state_id=target,
        event=event,
        extra=decision,
    )


def transition_operational_state(
    payload: dict[str, Any], decision: dict[str, Any]
) -> dict[str, Any]:
    if payload.get("operationEvent") is not None:
        return _transition_from_operation_event(payload, decision)
    result = dict(decision)
    if result.get("publicStatus") != "green":
        result["completion"] = False
        result["continuationAction"] = (
            "major_incident_continuation"
            if result.get("terminal") == "audit_major_incident_open"
            else "continue_same_day_public_recovery"
        )
    return result


def derive_report_state(
    payload: dict[str, Any], decision: dict[str, Any]
) -> dict[str, Any]:
    result = dict(decision)
    hashes = [
        str(result[key])
        for key in sorted(result)
        if key.endswith("Sha256") and isinstance(result.get(key), str)
    ]
    result["independentEvidenceHashes"] = hashes
    result["claimDerivation"] = {
        "issueDate": result.get("issueDate"),
        "runIntent": result.get("runIntent")
        or ("ScheduledRecoveryFull" if result.get("recoveryAttemptStatus") == "started" else "ScheduledProduction"),
        "publicStatus": result.get("publicStatus"),
        "evidenceCount": len(hashes),
    }
    result["reportState"] = (
        "complete" if result.get("completion") is True else "incomplete"
    )
    return result


def validate_operational_registry(
    registry: object, *, repo_root: Path
) -> dict[str, Any]:
    if not isinstance(registry, dict) or registry.get("schemaVersion") != (
        "NEWS_GRASP_DAILY_CONTROL_REGISTRY_V1"
    ):
        return {
            "status": "Red",
            "reason": "NEWS_GRASP_OPERATIONAL_REGISTRY_INVALID",
        }
    missing_design = [
        field for field in OPERATIONAL_DESIGN_FIELDS if not registry.get(field)
    ]
    declared = registry.get("declaredRouteIds")
    consumer = registry.get("consumerRouteIds")
    positive = registry.get("positiveFixtureRouteIds")
    negative = registry.get("negativeFixtureRouteIds")
    routes = registry.get("routes")
    if (
        missing_design
        or not all(isinstance(value, list) for value in (declared, consumer, positive, negative, routes))
        or len(set(declared or [])) != len(declared or [])
        or set(declared or []) != set(consumer or [])
        or set(declared or []) != set(positive or [])
        or set(declared or []) != set(negative or [])
    ):
        return {
            "status": "Red",
            "reason": "NEWS_GRASP_ROUTE_SET_NOT_EXACT",
            "missingOperationalDesignFields": missing_design,
        }
    route_map = {
        str(row.get("routeId")): row for row in routes if isinstance(row, dict)
    }
    if set(route_map) != set(declared):
        return {"status": "Red", "reason": "NEWS_GRASP_ROUTE_SET_NOT_EXACT"}
    source_observations: dict[str, dict[str, int | str]] = {}
    for route_id in declared:
        row = route_map[route_id]
        path = (repo_root / str(row.get("consumerPath") or "")).resolve()
        if repo_root.resolve() not in path.parents or not path.is_file():
            return {
                "status": "Red",
                "reason": "NEWS_GRASP_ROUTE_CONSUMER_MISSING",
                "routeId": route_id,
            }
        source = path.read_text(encoding="utf-8-sig", errors="replace")
        symbol = str(row.get("consumerSymbol") or "")
        if not symbol or symbol not in source:
            return {
                "status": "Red",
                "reason": "NEWS_GRASP_ROUTE_CONSUMER_SYMBOL_MISSING",
                "routeId": route_id,
            }
        caller = (repo_root / str(row.get("productionCallerPath") or "")).resolve()
        call_symbol = str(row.get("productionCallSymbol") or "")
        if (
            repo_root.resolve() not in caller.parents
            or not caller.is_file()
            or caller.is_symlink()
            or not call_symbol
        ):
            return {
                "status": "Red",
                "reason": "NEWS_GRASP_ROUTE_PRODUCTION_EDGE_MISSING",
                "routeId": route_id,
            }
        caller_source = caller.read_text(encoding="utf-8-sig", errors="replace")
        edge_found = False
        if caller.suffix.casefold() == ".py":
            try:
                tree = ast.parse(caller_source)
            except SyntaxError:
                tree = None
            if tree is not None:
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    function = node.func
                    name = (
                        function.id
                        if isinstance(function, ast.Name)
                        else function.attr
                        if isinstance(function, ast.Attribute)
                        else ""
                    )
                    if name == call_symbol:
                        edge_found = True
                        break
        else:
            executable_lines = [
                line.split("#", 1)[0]
                for line in caller_source.splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            ]
            edge_found = any(
                call_symbol in line and not line.lstrip().startswith(("function ", "param("))
                for line in executable_lines
            )
        if not edge_found:
            return {
                "status": "Red",
                "reason": "NEWS_GRASP_ROUTE_PRODUCTION_EDGE_MISSING",
                "routeId": route_id,
            }
        source_observations[route_id] = {
            "consumerPath": str(row.get("consumerPath") or ""),
            "status": "present",
            "bytes": path.stat().st_size,
        }
    return {
        "status": "Green",
        "reason": "NEWS_GRASP_OPERATIONAL_REGISTRY_CONFORMING",
        "consumerBoundOperationalDesign": {
            field: registry[field] for field in OPERATIONAL_DESIGN_FIELDS
        },
        "routeIds": list(declared),
        "sourceObservations": source_observations,
    }


def validate_canonical_operational_registry(
    repo_root: Path | None = None,
) -> dict[str, Any]:
    root = (repo_root or Path(__file__).resolve().parents[1]).resolve()
    path = root / "config" / "news_grasp_daily_control_routes.json"
    try:
        registry = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {
            "status": "Red",
            "reason": "NEWS_GRASP_OPERATIONAL_REGISTRY_INVALID",
        }
    return validate_operational_registry(registry, repo_root=root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", choices=("validate-registry", "validate-scheduled-admission")
    )
    parser.add_argument("--repo-root", type=Path, default=None)
    parser.add_argument("--path", type=Path, default=None)
    parser.add_argument("--expected-operation-kind", default=None)
    parser.add_argument("--expected-issue-date", default=None)
    parser.add_argument("--expected-operation-authority-sha256", default=None)
    args = parser.parse_args(argv)
    if args.command == "validate-registry":
        result = validate_canonical_operational_registry(args.repo_root)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result.get("status") == "Green" else 2
    try:
        if (
            args.path is None
            or not args.expected_operation_kind
            or not args.expected_issue_date
            or not args.expected_operation_authority_sha256
        ):
            raise _scheduled_admission_invalid()
        raw = args.path.resolve(strict=True).read_text(encoding="utf-8-sig")
        value = json.loads(raw)
        result = validate_scheduled_admission_receipt(
            value,
            expected_operation_kind=args.expected_operation_kind,
            expected_issue_date=args.expected_issue_date,
            expected_operation_authority_sha256=args.expected_operation_authority_sha256,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        print(str(error) or "HIGH_COST_SCHEDULED_ADMISSION_INVALID", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())


def validate_pillar_completeness(objective: str) -> dict[str, Any]:
    production = all(word in objective for word in ("06:00", "自己修復", "完走"))
    audit = all(word in objective for word in ("06:40", "復旧", "優先"))
    pillars = []
    if production:
        pillars.append("production_self_heal")
    if audit:
        pillars.append("audit_recovery_priority")
    return {
        "pillars": pillars,
        "productionPillar": production,
        "auditPillar": audit,
        "twoPillarCompleteness": production and audit,
    }


def finalize_audit_decision(
    payload: dict[str, Any], sealed_decision: dict[str, Any]
) -> dict[str, Any]:
    result = {
        key: value
        for key, value in sealed_decision.items()
        if key != "receiptSha256"
    }
    scheduled = str(result.get("scheduledAttemptStatus") or "unverified")
    result["stopPointKnown"] = bool(result.get("stopPointKnown", False))
    result["scheduledAttemptReachedRunner"] = bool(
        result.get("scheduledAttemptReachedRunner", False)
    )
    result["artifactDelta"] = str(result.get("artifactDelta") or "none")
    existing_next_action = str(result.get("nextAction") or "")
    if result.get("publicStatus") == "green":
        result["nextAction"] = "verify_public_completion"
    elif (
        result.get("terminal") == "audit_major_incident_open"
        and existing_next_action
    ):
        result["nextAction"] = existing_next_action
    else:
        result["nextAction"] = "recover_same_day_public_outcome"
    result["controlChainValid"] = bool(
        result.get("operationalTruthReceiptSha256")
    )
    public_status = str(
        result.get("publicStatus")
        or result.get("publicCompletionStatus")
        or "incomplete"
    )
    audit_status = str(result.get("auditObservationStatus") or "")
    if not audit_status:
        terminal = str(result.get("terminal") or "")
        audit_status = (
            "green"
            if terminal in {"audit_normal_green", "audit_recovered_green"}
            else "unverified"
            if terminal == "audit_observation_unverified"
            else "red"
            if terminal == "audit_major_incident_open"
            else str(result.get("verificationStatus") or "unverified")
        )
    completion = result.get("completionEvidence")
    completion_value = completion if isinstance(completion, dict) else {}
    readiness_status = str(
        result.get("nextRunReadinessStatus")
        or completion_value.get("nextRunReadinessStatus")
        or "unverified"
    )
    operational_status = str(result.get("operationalStatus") or "")
    if not operational_status:
        operational_status = (
            "recovery_required"
            if public_status != "green"
            else "green"
            if audit_status == "green" and readiness_status == "green"
            else "degraded"
        )
    result["stateVector"] = {
        "scheduledAttemptStatus": result.get("scheduledAttemptStatus", "unverified"),
        "recoveryAttemptStatus": result.get("recoveryAttemptStatus", "unverified"),
        "publicStatus": public_status,
        "auditObservationStatus": audit_status,
        "nextRunReadinessStatus": readiness_status,
        "operationalStatus": operational_status,
    }
    result = bind_outcome_target(payload, result)
    result = map_quality_issue_to_work_order(payload, result)
    result = transition_operational_state(payload, result)
    branch = str(result.get("recoveryBranch") or "ScheduledRecoveryFull")
    if branch not in RECOVERY_BRANCHES:
        branch = "ScheduledRecoveryFull"
    result["recoveryBranch"] = branch
    result["stopPointProofSha256"] = _sha({
        "issueDate": result.get("issueDate"),
        "scheduledAttemptStatus": scheduled,
        "stopPointKnown": result["stopPointKnown"],
        "artifactDelta": result["artifactDelta"],
        "branch": branch,
    })
    result["priorityClass"] = "same_day_public_recovery"
    if result.get("publicStatus") != "green":
        result["selectedWorkOrder"] = (
            "major_incident_continuation"
            if result.get("terminal") == "audit_major_incident_open"
            and result.get("classification") == "recoverable"
            else "recovery"
            if result.get("action") == "scheduled_recovery"
            else result.get("selectedWorkOrder", "public_outcome_first")
        )
    result["valueBearingWork"] = result.get("selectedWorkOrder") in {
        "recovery",
        "public_outcome_first",
        "major_incident_continuation",
    }
    result["budgetExtensionGranted"] = False
    result["mutationCapability"] = result.get("action") == "scheduled_recovery"
    result["authorityResult"] = (
        "authorized"
        if result.get("action") == "scheduled_recovery"
        else "bounded_continuation"
    )
    result["callerReceiptAccepted"] = result.get("reasonCode") not in {
        "SCHEDULED_ATTEMPT_LEDGER_INVALID",
        "RECOVERY_AUTHORITY_INVALID",
    }
    result["boundedAlternative"] = (
        None
        if result["callerReceiptAccepted"]
        else "major_incident_continuation"
    )
    result = derive_report_state(payload, result)
    return result


@dataclass(frozen=True)
class CompletionStateVectorV2:
    """公開、readiness、audit、scheduled、recoveryを交換不能なfieldで保持する。"""

    scheduledAttemptStatus: str
    recoveryAttemptStatus: str
    publicCompletionStatus: str
    nextRunReadinessStatus: str
    auditObservationStatus: str
    operationalStatus: str


@dataclass(frozen=True)
class CompletionStateVectorV3:
    """公開/readinessにexternal/constitutionを加えた交換不能なstate vector。"""

    scheduledAttemptStatus: str
    recoveryAttemptStatus: str
    publicCompletionStatus: str
    nextRunReadinessStatus: str
    auditObservationStatus: str
    externalDependencyStatus: str
    constitutionStatus: str
    operationalStatus: str


COMPLETION_STATE_VECTOR_V3 = "COMPLETION_STATE_VECTOR_V3"


def evaluate_completion(
    *,
    scheduled_attempt: Mapping[str, Any],
    recovery_attempt: Mapping[str, Any],
    public_receipt: Mapping[str, Any],
    readiness_probe: Mapping[str, Any],
    audit_observation: Mapping[str, Any],
) -> dict[str, Any]:
    """既存state vectorへpublic/readinessの非後退述語を投影する。"""
    public_status = str(public_receipt.get("status") or "unverified")
    authority = public_receipt.get("authorityId")
    if public_status == "verified_regression":
        public_completion = "red"
    elif public_status == "verified_green" and isinstance(authority, str) and authority:
        public_completion = "green"
    elif public_receipt.get("previousVerifiedGreen") and isinstance(authority, str) and authority:
        public_completion = "green"
    else:
        public_completion = "unverified"
    readiness_status = str(readiness_probe.get("status") or "unverified")
    operational_status = "green" if public_completion == "green" and readiness_status == "green" else "degraded"
    state_vector = CompletionStateVectorV2(
        scheduledAttemptStatus=str(scheduled_attempt.get("status") or "unverified"),
        recoveryAttemptStatus=str(recovery_attempt.get("status") or "unverified"),
        publicCompletionStatus=public_completion,
        nextRunReadinessStatus=readiness_status,
        auditObservationStatus=str(audit_observation.get("status") or "unverified"),
        operationalStatus=operational_status,
    )
    return {
        "schemaVersion": "COMPLETION_STATE_VECTOR_V2",
        **asdict(state_vector),
        "stateVector": asdict(state_vector),
        "completionAuthorityId": authority,
        "causeFingerprint": audit_observation.get("causeFingerprint"),
    }


def evaluate_completion_v3(
    *,
    scheduled_attempt: Mapping[str, Any],
    recovery_attempt: Mapping[str, Any],
    public_receipt: Mapping[str, Any],
    readiness_probe: Mapping[str, Any],
    audit_observation: Mapping[str, Any],
    external_dependency: Mapping[str, Any],
    constitution_admission: Mapping[str, Any],
) -> dict[str, Any]:
    """public Greenをreadiness/external観測で後退させず、総合状態だけを分離する。"""

    base = evaluate_completion(
        scheduled_attempt=scheduled_attempt,
        recovery_attempt=recovery_attempt,
        public_receipt=public_receipt,
        readiness_probe=readiness_probe,
        audit_observation=audit_observation,
    )
    public_status = str(base["publicCompletionStatus"])
    readiness_status = str(base["nextRunReadinessStatus"])
    external_status = str(external_dependency.get("status") or "unverified")
    constitution_status = str(constitution_admission.get("status") or "unverified")
    if public_status == "red":
        operational_status = "red"
    elif (
        public_status == "green"
        and readiness_status == "green"
        and external_status in {"ready", "not_required"}
        and constitution_status == "green"
    ):
        operational_status = "green"
    else:
        operational_status = "degraded"
    vector = CompletionStateVectorV3(
        scheduledAttemptStatus=str(base["scheduledAttemptStatus"]),
        recoveryAttemptStatus=str(base["recoveryAttemptStatus"]),
        publicCompletionStatus=public_status,
        nextRunReadinessStatus=readiness_status,
        auditObservationStatus=str(base["auditObservationStatus"]),
        externalDependencyStatus=external_status,
        constitutionStatus=constitution_status,
        operationalStatus=operational_status,
    )
    return {
        "schemaVersion": COMPLETION_STATE_VECTOR_V3,
        **asdict(vector),
        "stateVector": asdict(vector),
        "completionAuthorityId": base.get("completionAuthorityId"),
        "causeFingerprint": base.get("causeFingerprint"),
        "externalEvidenceHash": external_dependency.get("evidenceHash"),
        "constitutionHash": constitution_admission.get("constitutionHash"),
    }


def probe_readiness(*, root: Path | str, expected_paths: list[str], generation_id: str) -> dict[str, Any]:
    """副作用なしで固定rootのreadinessを観測する。"""
    base = Path(root).resolve()
    checked: list[Path] = []
    for relative in expected_paths:
        candidate = Path(relative)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError("NG_READINESS_ROOT_INVALID")
        resolved = (base / candidate).resolve()
        if base not in resolved.parents and resolved != base:
            raise ValueError("NG_READINESS_ROOT_INVALID")
        checked.append(resolved)
    missing = [
        relative
        for relative, resolved in zip(expected_paths, checked)
        if not resolved.is_file() or resolved.is_symlink()
    ]
    return {
        "schemaVersion": "READINESS_PROBE_RESULT_V1",
        "status": "green" if not missing else "red",
        "generationId": generation_id,
        "missingPaths": missing,
        "mutationCount": 0,
    }


def repair_readiness(
    *,
    authority: Mapping[str, Any],
    reason_code: str,
    handler: Callable[[Mapping[str, Any]], Mapping[str, Any]],
) -> Mapping[str, Any]:
    """typed authorityと登録handlerの結果だけを返す。"""
    if not all(authority.get(key) for key in ("authorityId", "generationId", "causeFingerprint")):
        raise ValueError("NG_READINESS_AUTHORITY_INVALID")
    result = handler({"reasonCode": reason_code, "authority": dict(authority)})
    if not isinstance(result, Mapping) or result.get("selfDeclaredGreen") is True:
        raise ValueError("NG_READINESS_SELF_DECLARED_GREEN")
    return {"status": "repair_completed", "handlerResult": dict(result), "mutationCount": result.get("mutationCount", 0)}


def verify_repaired_readiness(*, root: Path | str, expected_paths: list[str], generation_id: str) -> dict[str, Any]:
    result = probe_readiness(root=root, expected_paths=expected_paths, generation_id=generation_id)
    if result["status"] != "green":
        raise ValueError("NG_READINESS_REPAIR_NOT_VERIFIED")
    return result


def evaluate_bundle(bundle: Mapping[str, Any]) -> dict[str, Any]:
    """公開必須bundleを一度だけ判定し、既存artifactの再生成を抑止する。

    DeepDive記事・対談・音声は別artifactであるため、いずれか一つでも
    欠けている場合はGreenにしない。既存artifact hashが入力と一致する場合は
    ``reuseExisting`` を返し、モデル経路へ戻さない。
    """
    if not isinstance(bundle, Mapping):
        raise ValueError("NG_BUNDLE_INVALID")
    aliases = {
        "summary": ("summary",),
        "deepdiveArticle": ("deepdiveArticle", "deepdive", "article"),
        "deepdiveDialogue": ("deepdiveDialogue", "dialogue"),
        "deepdiveAudio": ("deepdiveAudio", "audio"),
    }
    missing: list[str] = []
    selected: dict[str, Any] = {}
    for canonical, names in aliases.items():
        value = next((bundle.get(name) for name in names if bundle.get(name) is not None), None)
        if value in (None, "", {}, [], ()):  # 空fixtureをGreenへ倒さない。
            missing.append(canonical)
        else:
            selected[canonical] = value
    if missing:
        return {
            "schemaVersion": "PUBLIC_BUNDLE_VERIFICATION_V1",
            "status": "incomplete",
            "missing": missing,
            "modelCalls": 0,
            "reuseExisting": False,
        }
    expected = bundle.get("existingArtifactHashes")
    actual = bundle.get("artifactHashes")
    reuse = isinstance(expected, Mapping) and isinstance(actual, Mapping) and dict(expected) == dict(actual)
    return {
        "schemaVersion": "PUBLIC_BUNDLE_VERIFICATION_V1",
        "status": "green",
        "missing": [],
        "bundle": selected,
        "modelCalls": 0 if reuse else int(bundle.get("modelCalls", 0) or 0),
        "reuseExisting": reuse,
    }
# NEWS_GRASP_TASK_CONSTITUTION_ADMISSION_V1
NEWS_GRASP_TASK_CONSTITUTION_ADMISSION_V1 = "NEWS_GRASP_TASK_CONSTITUTION_ADMISSION_V1"
NGC_A02_primary_behavior = "NGC_A02_primary_behavior"
NGC_A02_adversarial_boundary = "NGC_A02_adversarial_boundary"
NGC_A02_operational_recovery = "NGC_A02_operational_recovery"
dailyOperationLineageId = "dailyOperationLineageId"


def derive_daily_operation_lineage(issue_date: str, scheduled_authority: str) -> str:
    return hashlib.sha256(f"{issue_date}|{scheduled_authority}".encode("utf-8")).hexdigest()


def _task_string_tuple(payload: dict[str, Any], key: str) -> tuple[str, ...]:
    value = payload.get(key)
    if not isinstance(value, list) or not value:
        raise ValueError(f"NEWS_GRASP_TASK_FIELD_INVALID:{key}")
    result = tuple(str(item) for item in value)
    if len(result) != len(set(result)) or any(not item for item in result):
        raise ValueError(f"NEWS_GRASP_TASK_FIELD_INVALID:{key}")
    return result


def admit_task_constitution(
    payload: dict[str, Any], *, repo_root: Path | str = ROOT
) -> dict[str, Any]:
    if (
        not isinstance(payload, dict)
        or payload.get("schemaVersion") != "NEWS_GRASP_TASK_CONSTITUTION_REQUEST_V2"
    ):
        raise ValueError("NEWS_GRASP_TASK_CONSTITUTION_SCHEMA_INVALID")
    unresolved_value = payload.get("unresolvedDecisionIds")
    if not isinstance(unresolved_value, list):
        raise ValueError("NEWS_GRASP_TASK_UNRESOLVED_DECISIONS")
    unresolved = tuple(str(value) for value in unresolved_value)
    if unresolved:
        raise ValueError("NEWS_GRASP_TASK_UNRESOLVED_DECISIONS")
    if not re.fullmatch(r"TODO-\d{3}", str(payload.get("taskId", ""))):
        raise ValueError("NEWS_GRASP_TASK_ID_INVALID")
    if not re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        str(payload.get("durableGoalId", "")),
    ):
        raise ValueError("NEWS_GRASP_TASK_DURABLE_GOAL_INVALID")
    mutable_progress_fields = {"todoLedgerSha256", "deltaPacketSha256"}
    if mutable_progress_fields & set(payload):
        raise ValueError("NEWS_GRASP_TASK_MUTABLE_PROGRESS_BINDING_FORBIDDEN")
    if not SHA256_PATTERN.fullmatch(
        str(payload.get("todoDefinitionSetSha256", ""))
    ):
        raise ValueError(
            "NEWS_GRASP_TASK_DURABLE_STATE_INVALID:todoDefinitionSetSha256"
        )
    review_policy = str(payload.get("reviewPolicy", ""))
    review_attempt_count = payload.get("reviewAttemptCount")
    if (
        review_policy
        not in {"required_finite_series", "review_series_closed", "no_additional_review"}
        or type(review_attempt_count) is not int
        or review_attempt_count < 0
        or (
            review_policy in {"review_series_closed", "no_additional_review"}
            and review_attempt_count != 0
        )
    ):
        raise ValueError("NEWS_GRASP_TASK_REVIEW_POLICY_INVALID")

    root = Path(repo_root).resolve()
    from tools import news_grasp_constitution as constitution_module

    constitution = constitution_module.load_constitution(root)
    binding = constitution_module.load_skill_binding(root, verify_shared_sources=False)
    graph = constitution_module.load_skill_cross_layer_graph(root, constitution, binding)
    graph_by_id = {str(row["skillId"]): row for row in graph["skills"]}
    skill_ids = _task_string_tuple(payload, "skillIds")
    if not set(skill_ids) <= set(graph_by_id):
        raise ValueError("NEWS_GRASP_TASK_SKILL_UNKNOWN")

    layer_fields = (
        "purposeIds",
        "clauseIds",
        "flowIds",
        "taskIds",
        "consumerRoutes",
        "stateIds",
        "evidenceIds",
    )
    layers: dict[str, tuple[str, ...]] = {}
    for field_name in layer_fields:
        actual = _task_string_tuple(payload, field_name)
        expected = tuple(
            dict.fromkeys(
                str(item)
                for skill_id in skill_ids
                for item in graph_by_id[skill_id][field_name]
            )
        )
        if actual != expected:
            raise ValueError("NEWS_GRASP_TASK_LAYER_BINDING_MISMATCH")
        layers[field_name] = actual

    requirement_ids = _task_string_tuple(payload, "requirementIds")
    acceptance_ids = _task_string_tuple(payload, "acceptanceIds")
    if (
        len(requirement_ids) != len(acceptance_ids)
        or any(not re.fullmatch(r"R(?:0[1-9]|1[0-7])", value) for value in requirement_ids)
        or any(not re.fullmatch(r"A(?:0[1-9]|1[0-7])", value) for value in acceptance_ids)
        or any(requirement[1:] != acceptance[1:] for requirement, acceptance in zip(requirement_ids, acceptance_ids))
    ):
        raise ValueError("NEWS_GRASP_TASK_REQUIREMENT_ACCEPTANCE_INVALID")
    write_set_value = payload.get("writeSet")
    if not isinstance(write_set_value, list):
        raise ValueError("NEWS_GRASP_TASK_WRITE_SET_REQUIRED")
    write_set = tuple(str(value) for value in write_set_value)
    if (
        len(write_set) != len(set(write_set))
        or any(not value for value in write_set)
        or (
            not write_set
            and payload.get("mutationMode") != "verification_only"
        )
    ):
        raise ValueError("NEWS_GRASP_TASK_WRITE_SET_REQUIRED")

    candidates = payload.get("efficiencyCandidates")
    if not isinstance(candidates, list) or len(candidates) < 2:
        raise ValueError("NEWS_GRASP_TASK_EFFICIENCY_CANDIDATES_REQUIRED")
    resource_fields = {
        "modelCalls",
        "toolCalls",
        "expectedRetries",
        "broadRegressions",
        "e2eAttempts",
        "humanOperations",
        "wallClockMinutes",
    }
    costs: dict[str, float] = {}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ValueError("NEWS_GRASP_TASK_EFFICIENCY_CANDIDATE_INVALID")
        candidate_id = str(candidate.get("candidateId", ""))
        cost = candidate.get("expectedTotalResource")
        vector = candidate.get("resourceVector")
        if (
            not candidate_id
            or candidate_id in costs
            or candidate.get("goalFidelity") is not True
            or candidate.get("safetyComplete") is not True
            or isinstance(cost, bool)
            or not isinstance(cost, (int, float))
            or cost < 0
            or not isinstance(vector, dict)
            or set(vector) != resource_fields
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value < 0
                for value in vector.values()
            )
        ):
            raise ValueError("NEWS_GRASP_TASK_EFFICIENCY_CANDIDATE_INVALID")
        costs[candidate_id] = float(cost)
    selected_candidate_id = str(payload.get("selectedCandidateId", ""))
    if (
        selected_candidate_id not in costs
        or costs[selected_candidate_id] != min(costs.values())
    ):
        raise ValueError("NEWS_GRASP_TASK_EFFICIENCY_SELECTION_INVALID")

    return {
        "schemaVersion": NEWS_GRASP_TASK_CONSTITUTION_ADMISSION_V1,
        "taskId": str(payload["taskId"]),
        "requestSha256": hashlib.sha256(_canonical(payload)).hexdigest(),
        "durableGoalId": str(payload["durableGoalId"]),
        "skillIds": skill_ids,
        **layers,
        "requirementIds": requirement_ids,
        "acceptanceIds": acceptance_ids,
        "writeSet": write_set,
        "selectedCandidateId": selected_candidate_id,
        "reviewPolicy": review_policy,
        "unresolvedDecisionIds": (),
    }


def evaluate_execution_governance(payload: dict[str, Any]) -> dict[str, Any]:
    """News-Grasp product-local execution governanceの単一入口。"""

    from tools.news_grasp_execution_governance import evaluate

    return evaluate(payload, repo_root=Path(__file__).resolve().parents[1])
