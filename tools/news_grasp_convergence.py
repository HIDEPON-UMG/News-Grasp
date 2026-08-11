"""News-Grasp product-local convergence decision/event consumer。"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

SCHEMA = "OPERATIONAL_CONVERGENCE_DECISION_V1"
EVENT_SCHEMA = "OPERATIONAL_CONVERGENCE_EVENT_V1"


class ConvergenceError(ValueError):
    """convergence event・action契約違反。"""


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def cause_fingerprint(state: Mapping[str, Any]) -> str:
    selected = {key: state.get(key) for key in ("source", "runtime", "task", "external", "input", "public", "readiness")}
    return _sha(selected)


def decide_operational_convergence(
    *, state: Mapping[str, Any], previous_event: Mapping[str, Any] | None
) -> dict[str, Any]:
    """未達の最上流predicateからregistered actionを一つだけ選ぶ。"""
    fingerprint = cause_fingerprint(state)
    if previous_event and previous_event.get("causeFingerprint") == fingerprint:
        body = {
            "schemaVersion": SCHEMA,
            "observedStateHash": _sha(state),
            "nextActionId": "terminal_projection",
            "reasonCode": "CONVERGENCE_SAME_CAUSE_NO_DUPLICATE",
            "causeFingerprint": fingerprint,
            "retryEligible": False,
            "actionClass": "terminal_projection",
            "submissionState": "terminal",
        }
        body["receiptSha256"] = _sha(body)
        return body
    if state.get("external") not in {None, "ready", "green"}:
        action, reason, action_class = "defer_external_control_plane", "EXTERNAL_CONTROL_PLANE_UNAVAILABLE", "read_only_reverify"
    elif state.get("source") not in {None, "green", "clean"}:
        action, reason, action_class = "active_generation_reconcile", "GENERATION_DRIFT", "mutation"
    elif state.get("runtime") not in {None, "green", "fresh"}:
        action, reason, action_class = "previous_generation_restore", "RUNTIME_GENERATION_DRIFT", "mutation"
    elif state.get("input") not in {None, "green", "bound"}:
        action, reason, action_class = "checkpoint_continuation", "RUNTIME_INPUT_DRIFT", "mutation"
    elif state.get("readiness") not in {None, "green", "ready"}:
        action, reason, action_class = "active_generation_reconcile", "READINESS_RED", "mutation"
    elif state.get("public") not in {None, "green", "verified"}:
        action, reason, action_class = "checkpoint_continuation", "PUBLIC_COMPLETION_RED", "mutation"
    else:
        action, reason, action_class = "terminal_projection", "CONVERGENCE_GREEN", "terminal_projection"
    if action_class == "mutation":
        from tools import operational_recovery_registry as recovery_registry

        registered = recovery_registry.resolve_handler_id(
            repo_root=Path(__file__).resolve().parents[1],
            reason_code=reason,
        )
        if registered != action:
            raise ConvergenceError("CONVERGENCE_REGISTERED_HANDLER_DRIFT")
    body = {
        "schemaVersion": SCHEMA,
        "observedStateHash": _sha(state),
        "nextActionId": action,
        "reasonCode": reason,
        "causeFingerprint": fingerprint,
        "retryEligible": action_class == "mutation",
        "actionClass": action_class,
        "submissionState": "not_started",
        "correlationId": _sha({"state": _sha(state), "cause": fingerprint})[:32],
        "expectedPredicateId": f"{action}:converged",
        "observationSourceId": "news-grasp-convergence-v1",
        "reverifyCount": 0,
        "maxReverifyCount": 1 if action_class == "mutation" else 2,
        "deadlineAt": None,
    }
    body["receiptSha256"] = _sha(body)
    return body


def _read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise ConvergenceError("CONVERGENCE_EVENT_READ_FAILED") from error
    if len(lines) > 256:
        raise ConvergenceError("CONVERGENCE_EVENT_HISTORY_OVERSIZE")
    events: list[dict[str, Any]] = []
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ConvergenceError("CONVERGENCE_EVENT_INVALID") from error
        if not isinstance(value, dict):
            raise ConvergenceError("CONVERGENCE_EVENT_INVALID")
        events.append(value)
    return events


def record_convergence_event(
    *,
    path: Path | str,
    state_hash: str,
    cause_fingerprint: str,
    action_id: str,
    action_class: str,
    result: str,
    correlation_id: str = "",
    progress_evidence_hash: str = "",
) -> dict[str, Any]:
    """same causeのmutation/model/reportを再発行せず、eventだけ追記する。"""
    target = Path(path)
    events = _read_events(target)
    for event in reversed(events):
        if event.get("causeFingerprint") == cause_fingerprint and event.get("actionClass") in {"mutation", "model", "report"}:
            return {"accepted": False, "reasonCode": "CONVERGENCE_SAME_CAUSE_NO_DUPLICATE", "eventId": event.get("eventId")}
        if event.get("actionClass") == "terminal_projection":
            break
    previous_hash = str(events[-1].get("eventSha256") or "") if events else ""
    body: dict[str, Any] = {
        "schemaVersion": EVENT_SCHEMA,
        "eventId": _sha({"state": state_hash, "cause": cause_fingerprint, "sequence": len(events) + 1})[:32],
        "sequence": len(events) + 1,
        "previousEventHash": previous_hash,
        "stateHash": state_hash,
        "causeFingerprint": cause_fingerprint,
        "actionId": action_id,
        "actionClass": action_class,
        "result": result,
        "correlationId": correlation_id,
        "progressEvidenceHash": progress_evidence_hash,
        "observedAt": _now(),
    }
    body["eventSha256"] = _sha(body)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(body, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return {"accepted": True, **body}


def reverify_convergence(
    *,
    submission_state: str,
    observed_effect: bool,
    deadline_expired: bool,
    progress_evidence_hash: str,
) -> dict[str, Any]:
    if submission_state not in {"submitted", "accepted", "pending_external"}:
        raise ConvergenceError("CONVERGENCE_SUBMISSION_STATE_INVALID")
    if deadline_expired:
        return {"submissionState": "terminal", "terminal": True, "reasonCode": "CONVERGENCE_DEADLINE_EXPIRED"}
    if observed_effect:
        return {"submissionState": "effect_observed", "terminal": False, "progressEvidenceHash": progress_evidence_hash}
    return {"submissionState": "pending_external", "terminal": False, "progressEvidenceHash": progress_evidence_hash}
