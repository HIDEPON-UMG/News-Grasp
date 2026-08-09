from __future__ import annotations

import hashlib
import json
import argparse
import ast
from pathlib import Path
import sys
from typing import Any


RECOVERY_BRANCHES = {
    "ResumeFromStage",
    "ScheduledRecoveryFull",
    "minimal_unblocker",
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


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


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


def select_recovery_branch_from_truth(value: object) -> str:
    truth = validate_operational_truth_receipt(value)
    if not truth["stopPointKnown"]:
        return "ScheduledRecoveryFull"
    if truth.get("minimalUnblockerReceiptSha256"):
        if len(str(truth["minimalUnblockerReceiptSha256"])) != 64:
            raise ValueError("MINIMAL_UNBLOCKER_RECEIPT_INVALID")
        return "minimal_unblocker"
    delta = truth["artifactDelta"]
    if delta["exists"] is True and truth.get("stageCheckpointReceiptSha256"):
        if len(str(truth["stageCheckpointReceiptSha256"])) != 64:
            raise ValueError("STAGE_CHECKPOINT_RECEIPT_INVALID")
        return "ResumeFromStage"
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


def transition_operational_state(
    payload: dict[str, Any], decision: dict[str, Any]
) -> dict[str, Any]:
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
    source_hashes: dict[str, str] = {}
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
        source_hashes[route_id] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "status": "Green",
        "reason": "NEWS_GRASP_OPERATIONAL_REGISTRY_CONFORMING",
        "consumerBoundOperationalDesign": {
            field: registry[field] for field in OPERATIONAL_DESIGN_FIELDS
        },
        "routeIds": list(declared),
        "sourceHashes": source_hashes,
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
    parser.add_argument("command", choices=("validate-registry",))
    parser.add_argument("--repo-root", type=Path, default=None)
    args = parser.parse_args(argv)
    result = validate_canonical_operational_registry(args.repo_root)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") == "Green" else 2


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
    result["stateVector"] = {
        "scheduledAttemptStatus": result.get("scheduledAttemptStatus", "unverified"),
        "recoveryAttemptStatus": result.get("recoveryAttemptStatus", "unverified"),
        "productionPublicOutcomeStatus": result.get("publicStatus", "incomplete"),
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
