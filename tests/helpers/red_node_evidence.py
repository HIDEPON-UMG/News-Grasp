from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


PERSPECTIVES = ("primary", "adversarial", "recovery")

CASE_ORACLE_PREDICATES: dict[str, tuple[str, str, str]] = {
    "G01": (
        "outcomeTarget_is_same_day_public_outcome",
        "public_incomplete_selects_public_outcome_first",
        "recovery_continuation_retains_same_day_outcome_target",
    ),
    "G02": (
        "stopPointKnown_is_typed_boolean",
        "artifactDelta_is_reconstructed_from_primary_evidence",
        "nextAction_is_derived_after_stop_point_and_delta",
    ),
    "G03": (
        "sourceIssueId_is_exact",
        "repairWorkOrder_is_typed",
        "reverifyGate_is_bound_to_source_issue",
    ),
    "G04": (
        "public_incomplete_emits_continuationAction",
        "human_impact_invalid_cannot_be_terminal_complete",
        "recovery_invalid_emits_major_incident_continuation",
    ),
    "G05": (
        "broker_nonzero_maps_to_typed_authority_result",
        "malformed_broker_receipt_is_rejected_as_caller_evidence",
        "authority_hash_mismatch_emits_bounded_continuation",
    ),
    "G06": (
        "runner_before_generation_selects_ScheduledRecoveryFull",
        "partial_artifact_delta_selects_ResumeFromStage",
        "selected_branch_binds_stopPointProofSha256",
    ),
    "G07": (
        "same_day_public_recovery_has_highest_priorityClass",
        "internal_work_is_not_selected_before_recovery",
        "invalid_authority_selects_major_incident_continuation",
    ),
    "G08": (
        "incident_report_request_cannot_displace_public_outcome_work",
        "local_green_harness_work_gets_no_budget_extension",
        "no_artifact_delta_report_work_gets_no_mutation_capability",
    ),
    "G09": (
        "report_state_requires_independent_evidence_hashes",
        "claim_derivation_is_machine_reconstructable",
        "recovery_not_started_keeps_reportState_incomplete",
    ),
    "G10": (
        "historical_closure_requires_consumerPatchHash",
        "historical_closure_requires_negative_and_Green_evidence",
        "recurrence_requires_fresh_liveEvidenceHash",
    ),
    "G11": (
        "interaction_delta_retains_parent_requirements",
        "interaction_delta_recalculates_work_order",
        "interaction_delta_binds_latest_actual_user_event",
    ),
    "G12": (
        "operational_design_binds_owner_and_trigger",
        "operational_design_binds_entryGate_and_executionPath",
        "operational_design_binds_recovery_and_maintenance",
    ),
    "S121": (
        "failed_same_day_attempt_forbids_fresh_scheduled_reentry",
        "replayed_nonce_is_rejected_before_runner",
        "ScheduledRecoveryFull_is_selected_only_by_recovery_authority",
    ),
    "S122": (
        "foreign_overlap_is_rejected",
        "foreign_diff_drift_has_typed_rejection_reason",
        "stale_foreign_receipt_revokes_mutation_capability",
    ),
    "S123": (
        "completion_only_review_order_is_rejected",
        "stale_plan_review_receipt_has_typed_reason",
        "REVISE_finding_blocks_mutation_capability",
    ),
    "S124": (
        "missing_positive_route_fixture_is_rejected",
        "missing_negative_route_fixture_has_exact_reason",
        "route_drift_cannot_return_Green",
    ),
    "S125": (
        "goal_contains_production_self_heal_pillar",
        "goal_contains_audit_recovery_priority_pillar",
        "goal_contains_both_independent_pillars",
    ),
    "S126": (
        "goal_objective_hash_matches_latest_requirement",
        "older_self_report_goal_cannot_replace_latest_requirement",
        "goal_capture_occurs_after_two_pillar_requirement",
    ),
    "S127": (
        "goal_twoPillarCompleteness_is_true",
        "production_pillar_is_not_authority_only",
        "audit_green_cannot_mask_unverified_production_outcome",
    ),
    "S128": (
        "broker_rejection_commits_failure_receipt",
        "invalid_admission_json_emits_typed_continuation",
        "missing_authority_retains_scheduled_failure_history",
    ),
    "S129": (
        "installed_task_starts_versioned_launcher",
        "legacy_trampoline_is_not_lineage_authority",
        "fresh_repo_launcher_hash_cannot_mask_installed_direct_runner",
    ),
    "S131": (
        "child_nonzero_closes_launch_WAL",
        "launcher_crash_emits_pre_controller_continuation",
        "task_history_observer_reconstructs_failed_launch",
    ),
    "DCP01": (
        "runner_failure_appends_immutable_control_event",
        "parallel_failures_retain_both_valid_events_and_reject_forgery",
        "head_only_state_cannot_prove_public_completion",
    ),
    "DCP02": (
        "scheduled_attempt_and_public_outcome_are_separate_namespaces",
        "later_failure_cannot_erase_prior_public_meaning",
        "audit_materializes_scheduled_recovery_public_state_vector",
    ),
    "DCP04": (
        "completion_binds_immutable_daily_root_and_operation_ids",
        "root_or_intent_substitution_is_rejected",
        "cross_daily_root_recovery_completion_is_rejected",
    ),
    "DCP05": (
        "each_child_has_distinct_grant_and_launch_token",
        "duplicate_or_wrong_state_child_is_rejected_before_payload",
        "crash_reopen_reconcile_has_one_CAS_winner_and_exact_outcome",
    ),
    "DCP06": (
        "launcher_creates_pre_attempt_identity_before_broker_attempt",
        "caller_supplied_attempt_identity_is_ignored",
        "task_history_reconstructs_pre_attempt_and_recovery_authority",
    ),
}

CONSUMER_ROUTES: dict[str, str] = {
    **{case: "tools.audit_recovery_control.decide" for case in ("G01", "G02", "G03", "G04", "G05", "G06", "G07", "G08", "G09", "S121")},
    "G11": "tools.root_fix_goal_lineage.validate_goal_lineage",
    "S122": "tools.root_fix_promotion_control.validate_overlap_manifest",
    "S123": "tools.root_fix_adversarial_review_gate.validate_adversarial_review",
    "G10": "tools.historical_failure_scenarios.validate_historical_evidence",
    "G12": "workspace.task_lifecycle_control.evaluate_operational_principle",
    "S124": "workspace.task_lifecycle_control.evaluate_operational_principle",
    **{case: "official_goal_capture_replay" for case in ("S125", "S126", "S127")},
    "S128": "scripts.ops.news-grasp-runner.ps1",
    "S129": "Windows.TaskScheduler.News-Grasp Runner.Action",
    "S131": "scripts.ops.news-grasp-task-launcher.main",
    "DCP01": "runner_or_audit_control_state_consumer",
    "DCP02": "runner_or_audit_state_vector_consumer",
    "DCP04": "daily_self_heal.verify_publish_complete+audit_recovery_control.same_date_completion_green",
    "DCP05": "high_cost_control_v2+model_spawn_broker.run_model_subprocess",
    "DCP06": "launcher_or_task_history_pre_attempt_consumer",
}


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _hash_text(value: object) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


_LABEL_ONLY_KEYS = {
    "caseId",
    "perspective",
    "redCaseId",
    "evidencePerspective",
    "failureSignature",
}


def semantic_stimulus(value: object) -> object:
    """case/観点ラベルを除き、consumerへ渡した意味入力だけを残す。"""
    if isinstance(value, dict):
        return {
            str(key): semantic_stimulus(item)
            for key, item in value.items()
            if str(key) not in _LABEL_ONLY_KEYS
        }
    if isinstance(value, list):
        return [semantic_stimulus(item) for item in value]
    return value


def _consumer_sources(observation: dict[str, Any]) -> list[dict[str, str]]:
    declared = observation.get("consumerSources")
    if declared is None:
        candidates = (
            (observation.get("consumerPath"), observation.get("consumerSymbol")),
            (observation.get("runnerPath"), "news-grasp-runner.ps1"),
            (observation.get("launcherPath"), "main"),
        )
        declared = [
            {"path": str(path), "symbol": str(symbol or "module")}
            for path, symbol in candidates
            if path
        ]
    if not isinstance(declared, list) or not declared:
        raise ValueError("RED_NODE_CONSUMER_SOURCE_REQUIRED")
    result: list[dict[str, str]] = []
    for item in declared:
        if not isinstance(item, dict):
            raise ValueError("RED_NODE_CONSUMER_SOURCE_INVALID")
        source = Path(str(item.get("path") or ""))
        symbol = str(item.get("symbol") or "")
        if not source.is_file() or not symbol:
            raise ValueError(
                f"RED_NODE_CONSUMER_SOURCE_INVALID:{source}:{symbol}"
            )
        result.append(
            {
                "path": str(source.resolve()),
                "symbol": symbol,
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            }
        )
    return result


def _input_artifact(
    *,
    isolation_root: Path,
    case_id: str,
    perspective: str,
    observation: dict[str, Any],
) -> tuple[Path, object, str]:
    declared_path = observation.get("inputArtifactPath")
    if declared_path:
        path = Path(str(declared_path))
        if not path.is_file():
            raise ValueError(f"RED_NODE_INPUT_ARTIFACT_MISSING:{path}")
        raw = path.read_bytes()
        try:
            value = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"RED_NODE_INPUT_ARTIFACT_NOT_JSON:{path}") from error
    else:
        if "input" not in observation:
            raise ValueError("RED_NODE_EXPLICIT_INPUT_REQUIRED")
        value = observation["input"]
        path = (
            isolation_root
            / "artifacts"
            / "red-inputs"
            / f"{case_id.lower()}-{perspective}.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(
            value, ensure_ascii=False, sort_keys=True, indent=2
        ).encode("utf-8") + b"\n"
        path.write_bytes(raw)
    return path.resolve(), value, hashlib.sha256(raw).hexdigest()


def oracle_for(case_id: str, perspective: str) -> dict[str, Any]:
    predicates = CASE_ORACLE_PREDICATES[case_id]
    index = PERSPECTIVES.index(perspective)
    predicate = predicates[index]
    return {
        "schemaVersion": "NEWS_GRASP_RED_ORACLE_V1",
        "caseId": case_id,
        "perspective": perspective,
        "predicate": predicate,
        "expectedBeforeImplementation": "predicate_false",
        "expectedAfterImplementation": "predicate_true",
        "failureSignature": f"{case_id}:{perspective}:{predicate}",
    }


def record_red_node(
    *,
    isolation_root: Path,
    case_id: str,
    perspective: str,
    observation: dict[str, Any],
) -> dict[str, Any]:
    oracle = oracle_for(case_id, perspective)
    input_path, input_value, input_sha = _input_artifact(
        isolation_root=isolation_root,
        case_id=case_id,
        perspective=perspective,
        observation=observation,
    )
    sources = _consumer_sources(observation)
    consumer_source_sha = _sha(sources)
    stdout_sha = str(observation.get("stdoutSha256") or _hash_text(observation.get("stdout")))
    stderr_sha = str(observation.get("stderrSha256") or _hash_text(observation.get("stderr")))
    observation_path = (
        isolation_root
        / "artifacts"
        / "red-observations"
        / f"{case_id.lower()}-{perspective}.json"
    )
    observation_path.parent.mkdir(parents=True, exist_ok=True)
    observation_bytes = json.dumps(
        observation, ensure_ascii=False, sort_keys=True, indent=2
    ).encode("utf-8") + b"\n"
    observation_path.write_bytes(observation_bytes)
    actual_state = observation
    receipt = {
        "schemaVersion": "NEWS_GRASP_RED_NODE_EVIDENCE_V2",
        "caseId": case_id,
        "perspective": perspective,
        "consumerRoute": CONSUMER_ROUTES[case_id],
        "consumerSources": sources,
        "currentConsumerSourceSha256": consumer_source_sha,
        "inputArtifactPath": str(input_path),
        "inputArtifactSha256": input_sha,
        "semanticStimulusSha256": _sha(semantic_stimulus(input_value)),
        "returnCode": int(observation.get("returnCode", 0)),
        "stdoutSha256": stdout_sha,
        "stderrSha256": stderr_sha,
        "actualStateSha256": _sha(actual_state),
        "actualStateArtifactPath": str(observation_path),
        "actualStateArtifactFileSha256": hashlib.sha256(observation_bytes).hexdigest(),
        "oracle": oracle,
        "oracleSha256": _sha(oracle),
        "assertionBinding": "junit_actual_failed_assert_expression",
    }
    target = (
        isolation_root
        / "artifacts"
        / "red-nodes"
        / f"{case_id.lower()}-{perspective}.json"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return receipt
