from __future__ import annotations

import hashlib
import json
import argparse
import ast
import re
from dataclasses import asdict, dataclass
from pathlib import Path
import sys
from typing import Any, Callable, Mapping


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
        authority_body.get("schemaVersion") != "COMPLETION_AUTHORITY_V1"
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
        or completion_body.get("nextRunReadinessStatus") not in {None, "green"}
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
