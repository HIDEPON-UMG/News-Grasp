from __future__ import annotations

from pathlib import Path
import json

from tests.helpers.current_audit_adapter import observe_current_audit_decision
from tests.helpers.current_broker_child_adapter import observe_current_broker_children
from tests.helpers.current_broker_daily_baseline_adapter import (
    observe_daily_budget_baseline,
)
from tests.helpers.current_completion_consumer_adapter import (
    observe_current_completion_consumer,
)
from tests.helpers.current_hook_adapter import observe_current_hook
from tests.helpers.current_import_adapter import (
    observe_historical_failure,
    observe_operational_principle,
)
from tests.helpers.current_runner_adapter import (
    observe_current_runner_failure,
    observe_parallel_runner_failures,
)
from tests.helpers.current_scheduled_reentry_adapter import observe_scheduled_reentry
from tests.helpers.historical_goal_replay_adapter import (
    observe_historical_goal_replay,
)
from tests.helpers.red_matrix_registry import load_red_matrix
from tests.helpers.red_node_evidence import oracle_for, record_red_node


ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "tests" / "fixtures" / "autonomous_operations" / "red-matrix-v5.md"
ISOLATION_ROOT = ROOT.parent


def test_red_registry_is_exact_before_any_consumer_execution() -> None:
    nodes, baselines = load_red_matrix(MATRIX)

    assert len(nodes) == 81
    assert len({node.case_id for node in nodes}) == 27
    assert len({node.fixture_node for node in nodes}) == 81
    assert [baseline.risk for baseline in baselines] == ["DCP03 daily budget root"]


def test_current_audit_adapter_observes_real_reserved_incomplete_output() -> None:
    observation = observe_current_audit_decision(
        repo=ROOT,
        isolation_root=ISOLATION_ROOT,
        mode="reserved_incomplete",
    )

    assert observation["returnCode"] == 0
    assert observation["consumerSources"][0]["symbol"] == "main.decide"
    assert observation["brokerArgv"]
    result = observation["result"]
    assert isinstance(result, dict)
    assert result["publicStatus"] == "incomplete"
    assert result["terminal"] == "audit_major_incident_open"
    assert result["selectedWorkOrder"] == "major_incident_continuation"


_DEFAULT_MODES = {
    "primary": "reserved_incomplete",
    "adversarial": "human_invalid",
    "recovery": "failed_recovery_invalid",
}

_AUDIT_CASE_MODES = {
    "G01": _DEFAULT_MODES,
    "G02": _DEFAULT_MODES,
    "G03": _DEFAULT_MODES,
    "G04": _DEFAULT_MODES,
    "G05": {
        "primary": "broker_inspect_nonzero",
        "adversarial": "broker_inspect_malformed",
        "recovery": "failed_recovery_invalid",
    },
    "G06": {
        "primary": "failed_recoverable",
        "adversarial": "failed_recoverable",
        "recovery": "failed_recoverable",
    },
    "G07": {
        "primary": "failed_recoverable",
        "adversarial": "failed_recoverable",
        "recovery": "failed_recovery_invalid",
    },
    "G08": _DEFAULT_MODES,
    "G09": _DEFAULT_MODES,
}


def _payload_extra(case_id: str, perspective: str) -> dict[str, object]:
    perspective_stimulus: dict[str, object] = {
        "redCaseId": case_id,
        "evidencePerspective": perspective,
    }
    if perspective == "primary":
        perspective_stimulus.update(
            {
                "observedStageClass": "pre_generation_or_normal_attempt",
                "artifactDeltaClass": "none_or_expected",
            }
        )
    elif perspective == "adversarial":
        perspective_stimulus.update(
            {
                "observedStageClass": "substitution_or_human_impact_boundary",
                "artifactDeltaClass": "partial_or_drifted",
            }
        )
    else:
        perspective_stimulus.update(
            {
                "observedStageClass": "failed_attempt_recovery_boundary",
                "artifactDeltaClass": "recovery_rebuild_or_resume",
            }
        )
    if case_id != "S121":
        if case_id == "G06":
            perspective_stimulus["requestedRecoveryBranch"] = {
                "primary": "ScheduledRecoveryFull",
                "adversarial": "ResumeFromStage",
                "recovery": "minimal_unblocker",
            }[perspective]
            perspective_stimulus["stopPointEvidenceClass"] = {
                "primary": "runner_not_reached",
                "adversarial": "partial_artifact_checkpoint",
                "recovery": "typed_minimal_unblocker",
            }[perspective]
        if case_id == "G07":
            perspective_stimulus["competingWorkOrder"] = {
                "primary": "incident_report",
                "adversarial": "harness_mutation",
                "recovery": "major_incident_continuation",
            }[perspective]
        if case_id == "G08":
            perspective_stimulus["plannedMutation"] = {
                "primary": "incident_report_only",
                "adversarial": "harness",
                "recovery": "report",
            }[perspective]
        return perspective_stimulus
    if perspective == "primary":
        perspective_stimulus.update({
            "requestedRunIntent": "ScheduledProduction",
            "stopPointKnown": False,
        })
    elif perspective == "adversarial":
        perspective_stimulus.update({
            "requestedRunIntent": "ScheduledProduction",
            "sameDateAttemptExists": True,
            "replayedNonce": True,
        })
    else:
        perspective_stimulus.update({
            "requestedRunIntent": "ScheduledProduction",
            "requestedBranch": "ScheduledRecoveryFull",
            "stopPointKnown": True,
        })
    return perspective_stimulus


def _assert_target_oracle(
    *, case_id: str, perspective: str, observation: dict[str, object]
) -> None:
    node_id = str(oracle_for(case_id, perspective)["failureSignature"])
    result = observation.get("result")
    assert isinstance(result, dict), f"{node_id}:RESULT_NOT_OBJECT"
    if case_id == "G01":
        if perspective == "primary":
            assert result.get("outcomeTarget") == "same_day_public_outcome", f"{node_id}:OUTCOME_TARGET_MISSING"
        elif perspective == "adversarial":
            assert result.get("selectedWorkOrder") == "major_incident_continuation", f"{node_id}:INCIDENT_CONTINUATION_MISSING"
        else:
            assert result.get("outcomeTarget") == "same_day_public_outcome" and result.get("completion") is False, f"{node_id}:RECOVERY_OUTCOME_TARGET_NOT_RETAINED"
    elif case_id == "G02":
        if perspective == "primary":
            assert isinstance(result.get("stopPointKnown"), bool), f"{node_id}:STOP_POINT_STATE_MISSING"
        elif perspective == "adversarial":
            assert result.get("artifactDelta") is not None, f"{node_id}:ARTIFACT_DELTA_MISSING"
        else:
            assert result.get("nextAction") is not None, f"{node_id}:NEXT_ACTION_MISSING"
    elif case_id == "G03":
        field = {"primary": "sourceIssueId", "adversarial": "repairWorkOrder", "recovery": "reverifyGate"}[perspective]
        assert result.get(field), f"{node_id}:{field}_MISSING"
    elif case_id == "G04":
        if perspective == "primary":
            assert result.get("continuationAction"), f"{node_id}:CONTINUATION_ACTION_MISSING"
        elif perspective == "adversarial":
            assert result.get("completion") is False, f"{node_id}:INCOMPLETE_MUST_NOT_BE_TERMINAL"
        else:
            assert result.get("continuationAction") == "major_incident_continuation", f"{node_id}:MAJOR_INCIDENT_CONTINUATION_MISSING"
    elif case_id == "G05":
        if perspective == "primary":
            assert result.get("authorityResult") in {"authorized", "bounded_continuation"}, f"{node_id}:TYPED_AUTHORITY_RESULT_MISSING"
        elif perspective == "adversarial":
            assert result.get("callerReceiptAccepted") is False, f"{node_id}:CALLER_RECEIPT_REJECTION_MISSING"
        else:
            assert result.get("boundedAlternative"), f"{node_id}:BOUNDED_CONTINUATION_MISSING"
    elif case_id == "G06":
        if perspective == "primary":
            assert result.get("recoveryBranch") == "ScheduledRecoveryFull", f"{node_id}:SCHEDULED_RECOVERY_FULL_NOT_SELECTED"
        elif perspective == "adversarial":
            assert result.get("recoveryBranch") == "ScheduledRecoveryFull", f"{node_id}:CALLER_BRANCH_INJECTION_CHANGED_SELECTION"
        else:
            proof = result.get("stopPointProofSha256")
            assert isinstance(proof, str) and len(proof) == 64, f"{node_id}:STOP_POINT_PROOF_MISSING"
    elif case_id == "G07":
        if perspective == "primary":
            assert result.get("priorityClass") == "same_day_public_recovery", f"{node_id}:RECOVERY_PRIORITY_MISSING"
        elif perspective == "adversarial":
            assert result.get("selectedWorkOrder") == "recovery", f"{node_id}:RECOVERY_WORK_ORDER_MISSING"
        else:
            assert result.get("selectedWorkOrder") == "major_incident_continuation", f"{node_id}:MAJOR_INCIDENT_WORK_ORDER_MISSING"
    elif case_id == "G08":
        if perspective == "primary":
            assert result.get("valueBearingWork") is True, f"{node_id}:PUBLIC_OUTCOME_WORK_NOT_SELECTED"
            assert result.get("selectedWorkOrder") == "major_incident_continuation", f"{node_id}:REPORT_DISPLACED_PUBLIC_OUTCOME"
        elif perspective == "adversarial":
            assert result.get("budgetExtensionGranted") is False, f"{node_id}:BUDGET_EXTENSION_NOT_DENIED"
            assert result.get("priorityClass") == "same_day_public_recovery", f"{node_id}:HARNESS_DISPLACED_RECOVERY"
        else:
            assert result.get("mutationCapability") is False, f"{node_id}:REPORT_MUTATION_CAPABILITY_NOT_REVOKED"
            assert result.get("selectedWorkOrder") == "major_incident_continuation", f"{node_id}:REPORT_BECAME_TERMINAL"
    elif case_id == "G09":
        if perspective == "primary":
            assert result.get("independentEvidenceHashes"), f"{node_id}:INDEPENDENT_EVIDENCE_MISSING"
        elif perspective == "adversarial":
            assert result.get("claimDerivation"), f"{node_id}:CLAIM_DERIVATION_MISSING"
        else:
            assert result.get("reportState") == "incomplete", f"{node_id}:REPORT_STATE_NOT_DERIVED"
    else:
        raise AssertionError(f"{node_id}:AUDIT_CASE_UNROUTED")


def _make_audit_red_test(case_id: str, perspective: str, mode: str):
    def test() -> None:
        observation = observe_current_audit_decision(
            repo=ROOT,
            isolation_root=ISOLATION_ROOT,
            mode=mode,
            payload_extra=_payload_extra(case_id, perspective),
        )
        record_red_node(
            isolation_root=ISOLATION_ROOT,
            case_id=case_id,
            perspective=perspective,
            observation=observation,
        )
        _assert_target_oracle(
            case_id=case_id,
            perspective=perspective,
            observation=observation,
        )

    test.__name__ = f"test_{case_id.lower()}_{perspective}"
    test.__qualname__ = test.__name__
    return test


for _case_id, _perspective_modes in _AUDIT_CASE_MODES.items():
    for _perspective, _mode in _perspective_modes.items():
        globals()[f"test_{_case_id.lower()}_{_perspective}"] = _make_audit_red_test(
            _case_id, _perspective, _mode
        )


def _make_s121_test(perspective: str):
    def test() -> None:
        observation = observe_scheduled_reentry(
            workspace_harness=ISOLATION_ROOT / "workspace-harness",
            isolation_root=ISOLATION_ROOT,
            perspective=perspective,
        )
        record_red_node(
            isolation_root=ISOLATION_ROOT,
            case_id="S121",
            perspective=perspective,
            observation=observation,
        )
        node_id = str(oracle_for("S121", perspective)["failureSignature"])
        result = observation["result"]
        if perspective == "primary":
            assert observation["returnCode"] == 2, f"{node_id}:SCHEDULED_REENTRY_NOT_REJECTED"
            assert "HIGH_COST_SCHEDULED_ATTEMPT_REPLAY" in result["reason"]
        elif perspective == "adversarial":
            assert observation["returnCode"] == 2, f"{node_id}:NONCE_REPLAY_NOT_REJECTED"
            assert "SCHEDULED_OPERATION_AUTHORITY_REPLAY" in result["reason"]
        else:
            assert result["selectedAuthority"] == "scheduled_recovery", f"{node_id}:RECOVERY_AUTHORITY_NOT_SELECTED"
            assert result["runIntent"] == "ScheduledRecoveryFull"

    test.__name__ = f"test_s121_{perspective}"
    test.__qualname__ = test.__name__
    return test


for _s121_perspective in ("primary", "adversarial", "recovery"):
    globals()[f"test_s121_{_s121_perspective}"] = _make_s121_test(
        _s121_perspective
    )


_HOOK_CASES = {"G11", "S122", "S123"}


def test_current_hook_adapter_observes_real_hook_outputs() -> None:
    expectations = {
        "G11": 0,
        "S122": 2,
        "S123": 2,
    }
    for case_id, expected_return_code in expectations.items():
        observation = observe_current_hook(
            case_id=case_id,
            perspective="primary",
            isolation_root=ISOLATION_ROOT,
        )
        assert observation["returnCode"] == expected_return_code, observation
        assert len(str(observation["consumerSha256"])) == 64
        assert len(str(observation["inputSha256"])) == 64


def _assert_hook_target_oracle(
    *, case_id: str, perspective: str, observation: dict[str, object]
) -> None:
    node_id = str(oracle_for(case_id, perspective)["failureSignature"])
    result = observation.get("result")
    assert isinstance(result, dict), f"{node_id}:HOOK_RESULT_NOT_OBJECT"
    if case_id == "G11":
        if perspective == "primary":
            assert result.get("parentRequirementRetention") is True, f"{node_id}:PARENT_REQUIREMENT_RETENTION_UNENFORCED"
        elif perspective == "adversarial":
            assert result.get("workOrderRecalculated") is True, f"{node_id}:WORK_ORDER_NOT_RECALCULATED"
        else:
            assert result.get("latestActualUserEventBound") is True, f"{node_id}:LATEST_USER_EVENT_NOT_BOUND"
    elif case_id == "S122":
        if perspective == "primary":
            assert observation.get("returnCode") == 2, f"{node_id}:FOREIGN_OVERLAP_NOT_REJECTED"
        elif perspective == "adversarial":
            assert result.get("reason") == "FOREIGN_OVERLAP_ADMISSION_INVALID", f"{node_id}:FOREIGN_OVERLAP_REASON_MISSING"
        else:
            assert result.get("mutationCapability") is False, f"{node_id}:MUTATION_CAPABILITY_NOT_REVOKED"
    elif case_id == "S123":
        if perspective == "primary":
            assert observation.get("returnCode") == 2, f"{node_id}:ADVERSARIAL_REVIEW_ORDER_NOT_REJECTED"
        elif perspective == "adversarial":
            assert result.get("reason") == "ADVERSARIAL_REVIEW_ORDER_INVALID", f"{node_id}:ADVERSARIAL_REVIEW_REASON_MISSING"
        else:
            assert result.get("mutationCapability") is False, f"{node_id}:REVIEW_MUTATION_CAPABILITY_NOT_REVOKED"
    else:
        raise AssertionError(f"{node_id}:HOOK_CASE_UNROUTED")


def _make_hook_red_test(case_id: str, perspective: str):
    def test() -> None:
        observation = observe_current_hook(
            case_id=case_id,
            perspective=perspective,
            isolation_root=ISOLATION_ROOT,
        )
        record_red_node(
            isolation_root=ISOLATION_ROOT,
            case_id=case_id,
            perspective=perspective,
            observation=observation,
        )
        _assert_hook_target_oracle(
            case_id=case_id,
            perspective=perspective,
            observation=observation,
        )

    test.__name__ = f"test_{case_id.lower()}_{perspective}"
    test.__qualname__ = test.__name__
    return test


for _hook_case_id in sorted(_HOOK_CASES):
    for _hook_perspective in ("primary", "adversarial", "recovery"):
        globals()[
            f"test_{_hook_case_id.lower()}_{_hook_perspective}"
        ] = _make_hook_red_test(_hook_case_id, _hook_perspective)


def test_current_import_adapters_reach_real_consumers() -> None:
    historical = observe_historical_failure(repo=ROOT, perspective="primary")
    principle = observe_operational_principle(
        case_id="G12", perspective="primary"
    )
    route = observe_operational_principle(
        case_id="S124", perspective="baseline"
    )
    assert historical["returnCode"] == 0
    assert historical["result"]["valid"] is True
    assert principle["returnCode"] == 0
    assert principle["result"]["status"] == "Green"
    assert route["returnCode"] == 0
    assert route["result"]["status"] == "Green"


def _assert_import_target_oracle(
    *, case_id: str, perspective: str, observation: dict[str, object]
) -> None:
    node_id = str(oracle_for(case_id, perspective)["failureSignature"])
    result = observation.get("result")
    assert isinstance(result, dict), f"{node_id}:IMPORT_RESULT_NOT_OBJECT"
    if case_id == "G10":
        assert observation.get("returnCode") == 0, (
            f"{node_id}:HISTORICAL_CONSUMER_NOT_REACHED"
        )
        assert result.get("valid") is True, (
            f"{node_id}:HISTORICAL_BASELINE_NOT_VALID"
        )
        closure = result.get("operationalClosure")
        field = {"primary": "consumerPatchHash", "adversarial": "negativeFixtureHash", "recovery": "liveEvidenceHash"}[perspective]
        assert isinstance(closure, dict) and closure.get(field), f"{node_id}:CLOSURE_FIELD_MISSING:{field}"
    elif case_id == "G12":
        assert observation.get("returnCode") == 0
        assert result.get("status") == "Green"
        bindings = result.get("consumerBoundOperationalDesign")
        fields = {"primary": ("owner", "trigger"), "adversarial": ("entryGate", "executionPath"), "recovery": ("recovery", "maintenance")}[perspective]
        assert isinstance(bindings, dict) and all(bindings.get(field) for field in fields), f"{node_id}:BOUND_FIELDS_MISSING:{fields}"
    elif case_id == "S124":
        if perspective == "primary":
            assert observation.get("returnCode") == 2, f"{node_id}:POSITIVE_FIXTURE_GAP_NOT_REJECTED"
        elif perspective == "adversarial":
            assert result.get("reason") == "NEWS_GRASP_ROUTE_SET_NOT_EXACT", f"{node_id}:ROUTE_PARITY_REASON_MISSING"
        else:
            assert result.get("status") != "Green", f"{node_id}:ROUTE_DRIFT_ACCEPTED_GREEN"
            assert result.get("reason") == "NEWS_GRASP_ROUTE_CONSUMER_SYMBOL_MISSING"
    else:
        raise AssertionError(f"{node_id}:IMPORT_CASE_UNROUTED")


def _make_import_red_test(case_id: str, perspective: str):
    def test() -> None:
        if case_id == "G10":
            observation = observe_historical_failure(
                repo=ROOT, perspective=perspective
            )
        else:
            observation = observe_operational_principle(
                case_id=case_id,
                perspective=perspective,
            )
        record_red_node(
            isolation_root=ISOLATION_ROOT,
            case_id=case_id,
            perspective=perspective,
            observation=observation,
        )
        _assert_import_target_oracle(
            case_id=case_id,
            perspective=perspective,
            observation=observation,
        )

    test.__name__ = f"test_{case_id.lower()}_{perspective}"
    test.__qualname__ = test.__name__
    return test


for _import_case_id in ("G10", "G12", "S124"):
    for _import_perspective in ("primary", "adversarial", "recovery"):
        globals()[
            f"test_{_import_case_id.lower()}_{_import_perspective}"
        ] = _make_import_red_test(_import_case_id, _import_perspective)


def test_goal_replay_adapter_reads_official_capture() -> None:
    observation = observe_historical_goal_replay(
        case_id="S125", perspective="primary"
    )
    assert observation["returnCode"] == 0
    assert len(str(observation["sourceSha256"])) == 64
    assert observation["captureLine"] > 0
    assert observation["captureLine"] > observation["latestRequirementLine"]


def _assert_goal_replay_target_oracle(
    *, case_id: str, perspective: str, observation: dict[str, object]
) -> None:
    node_id = str(oracle_for(case_id, perspective)["failureSignature"])
    result = observation.get("result")
    assert isinstance(result, dict), f"{node_id}:GOAL_REPLAY_RESULT_NOT_OBJECT"
    goal = result.get("goal")
    assert isinstance(goal, dict), f"{node_id}:GOAL_OBJECT_MISSING"
    if case_id == "S125":
        if perspective == "primary":
            assert result.get("productionPillar") is True, f"{node_id}:PRODUCTION_PILLAR_MISSING"
        elif perspective == "adversarial":
            assert result.get("auditPillar") is True, f"{node_id}:AUDIT_PILLAR_MISSING"
        else:
            assert result.get("pillars") == ["production_self_heal", "audit_recovery_priority"], f"{node_id}:DURABLE_GOAL_PILLARS_INCOMPLETE"
    elif case_id == "S126":
        if perspective == "primary":
            binding = goal.get("requirementBinding")
            assert isinstance(binding, dict), f"{node_id}:GOAL_REQUIREMENT_BINDING_MISSING"
            assert binding.get("actualUserRecordSha256") == observation.get("latestRequirementRecordSha256"), f"{node_id}:GOAL_REQUIREMENT_RECORD_UNBOUND"
            assert binding.get("objectiveSha256") == goal.get("objectiveHash"), f"{node_id}:GOAL_OBJECTIVE_HASH_UNBOUND"
        elif perspective == "adversarial":
            assert observation.get("captureLine", 0) > observation.get("latestRequirementLine", 0), f"{node_id}:OLD_GOAL_CAPTURE_PRECEDES_REQUIREMENT"
        else:
            assert result.get("twoPillarCompleteness") is True, f"{node_id}:LATEST_GOAL_NOT_TWO_PILLAR"
    elif case_id == "S127":
        if perspective == "primary":
            assert result.get("twoPillarCompleteness") is True, f"{node_id}:TWO_PILLAR_COMPLETENESS_FALSE"
        elif perspective == "adversarial":
            assert result.get("productionPillar") is True, f"{node_id}:PRODUCTION_SELF_HEAL_NOT_OPERATIONAL"
        else:
            assert result.get("auditPillar") is True and result.get("productionPillar") is True, f"{node_id}:AUDIT_GREEN_MASKS_PRODUCTION_GAP"
    else:
        raise AssertionError(f"{node_id}:GOAL_CASE_UNROUTED")


def _make_goal_replay_red_test(case_id: str, perspective: str):
    def test() -> None:
        observation = observe_historical_goal_replay(
            case_id=case_id,
            perspective=perspective,
        )
        record_red_node(
            isolation_root=ISOLATION_ROOT,
            case_id=case_id,
            perspective=perspective,
            observation=observation,
        )
        _assert_goal_replay_target_oracle(
            case_id=case_id,
            perspective=perspective,
            observation=observation,
        )

    test.__name__ = f"test_{case_id.lower()}_{perspective}"
    test.__qualname__ = test.__name__
    return test


for _goal_case_id in ("S125", "S126", "S127"):
    for _goal_perspective in ("primary", "adversarial", "recovery"):
        globals()[
            f"test_{_goal_case_id.lower()}_{_goal_perspective}"
        ] = _make_goal_replay_red_test(_goal_case_id, _goal_perspective)


def test_parallel_runner_adapter_reaches_two_actual_processes() -> None:
    observation = observe_parallel_runner_failures(
        repo=ROOT,
        isolation_root=ISOLATION_ROOT,
    )
    assert observation["returnCodes"] == [76, 76], observation
    assert observation["state"]


def test_dcp03_preserved_green_baseline_is_executed() -> None:
    observation = observe_daily_budget_baseline(
        workspace_harness=ISOLATION_ROOT / "workspace-harness",
        isolation_root=ISOLATION_ROOT,
    )
    assert observation["returnCode"] == 0
    assert observation["sameDateIdentityStable"] is True
    assert observation["otherDateIdentityDistinct"] is True
    assert observation["scheduledReplayRejected"] is True
    assert observation["rootOverrideRejected"] is True
    assert observation["callCountAfter"] - observation["callCountBefore"] == 2
    assert observation["taskIdentityAfter"] == observation["taskIdentityBefore"]
    assert observation["maxCallsAfter"] == observation["maxCallsBefore"] == 9
    assert observation["taskStateAfter"] == observation["taskStateBefore"]
    target = ISOLATION_ROOT / "artifacts" / "dcp03-preserved-green-observation.json"
    target.write_text(
        json.dumps(observation, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _dcp_audit_observation(
    *, case_id: str, perspective: str
) -> dict[str, object]:
    if case_id == "DCP01" and perspective == "primary":
        return observe_current_runner_failure(
            repo=ROOT,
            isolation_root=ISOLATION_ROOT,
            mode="reject_exit_1",
        )
    if case_id == "DCP01" and perspective == "adversarial":
        return observe_parallel_runner_failures(
            repo=ROOT,
            isolation_root=ISOLATION_ROOT,
        )
    if case_id == "DCP01" and perspective == "recovery":
        return observe_current_audit_decision(
            repo=ROOT,
            isolation_root=ISOLATION_ROOT,
            mode="broker_inspect_nonzero",
        )
    if case_id == "DCP02" and perspective == "primary":
        return observe_current_runner_failure(
            repo=ROOT,
            isolation_root=ISOLATION_ROOT,
            mode="reject_exit_1",
        )
    if case_id == "DCP02" and perspective == "adversarial":
        return observe_current_runner_failure(
            repo=ROOT,
            isolation_root=ISOLATION_ROOT,
            mode="reject_exit_1",
            seed_status="publish_complete",
        )
    if case_id == "DCP02" and perspective == "recovery":
        return observe_current_audit_decision(
            repo=ROOT,
            isolation_root=ISOLATION_ROOT,
            mode="failed_recovery_invalid",
        )
    raise AssertionError(f"{case_id}:{perspective}:DCP_ADAPTER_UNROUTED")


def _assert_dcp_target_oracle(
    *, case_id: str, perspective: str, observation: dict[str, object]
) -> None:
    node_id = str(oracle_for(case_id, perspective)["failureSignature"])
    if case_id == "DCP01":
        if perspective == "primary":
            fields = observation.get("controlEventFields")
            assert isinstance(fields, dict), f"{node_id}:CONTROL_FIELDS_NOT_OBJECT"
            for field in ("eventSequence", "previousEventHash", "dailyRootId"):
                assert fields.get(field) != "ABSENT", (
                    f"{node_id}:IMMUTABLE_EVENT_FIELD_ABSENT:{field}"
                )
        elif perspective == "adversarial":
            assert observation.get("returnCodes") == [76, 76]
            assert observation.get("retainedFailureEventCount") == 2, (
                f"{node_id}:PARALLEL_FAILURE_EVENTS_NOT_RETAINED"
            )
            assert observation.get("authoritativeFailureReceiptCount") == 2, (
                f"{node_id}:PARALLEL_FAILURE_RECEIPTS_NOT_RETAINED"
            )
            receipt_run_ids = observation.get("failureReceiptRunIds")
            assert isinstance(receipt_run_ids, list) and len(set(receipt_run_ids)) == 2, (
                f"{node_id}:FAILURE_RECEIPTS_NOT_BOUND_TO_UNIQUE_RUNS"
            )
            assert observation.get("terminalizerFailureCount") == 0, (
                f"{node_id}:FAILURE_TERMINALIZER_NOT_AUTHORITATIVE"
            )
            assert observation.get("controlTransitionPredicate") != "ABSENT", (
                f"{node_id}:CONTROL_TRANSITION_PREDICATE_ABSENT"
            )
        else:
            result = observation.get("result")
            assert isinstance(result, dict), f"{node_id}:AUDIT_RESULT_NOT_OBJECT"
            assert result.get("controlChainValid") is False, (
                f"{node_id}:HEAD_ONLY_CONTROL_STATE_NOT_REJECTED"
            )
            assert result.get("completion") is False, (
                f"{node_id}:HEAD_ONLY_EVIDENCE_COMPLETED"
            )
    elif case_id == "DCP02":
        if perspective in {"primary", "adversarial"}:
            vector = observation.get("stateVector")
            assert isinstance(vector, dict), f"{node_id}:STATE_VECTOR_NOT_OBJECT"
            assert vector.get("scheduledAttemptStatus") == "failed", (
                f"{node_id}:SCHEDULED_ATTEMPT_VECTOR_ABSENT"
            )
            assert vector.get("productionPublicOutcomeStatus") in {
                "unknown",
                "incomplete",
            }, f"{node_id}:PUBLIC_NAMESPACE_NOT_SEPARATED"
            if perspective == "adversarial":
                assert observation.get("state", {}).get("priorStatusRetained") is True, (
                    f"{node_id}:PRIOR_PUBLIC_MEANING_ERASED"
                )
        else:
            result = observation.get("result")
            assert isinstance(result, dict), f"{node_id}:AUDIT_RESULT_NOT_OBJECT"
            vector = result.get("stateVector")
            assert isinstance(vector, dict), f"{node_id}:AUDIT_STATE_VECTOR_ABSENT"
            assert vector == {
                "scheduledAttemptStatus": "failed",
                "recoveryAttemptStatus": "not_started",
                "productionPublicOutcomeStatus": "incomplete",
            }, f"{node_id}:AUDIT_STATE_VECTOR_NOT_MATERIALIZED"
            assert result.get("completion") is False
    else:
        raise AssertionError(f"{node_id}:DCP_CASE_UNROUTED")


def _make_dcp_red_test(case_id: str, perspective: str):
    def test() -> None:
        observation = _dcp_audit_observation(
            case_id=case_id,
            perspective=perspective,
        )
        record_red_node(
            isolation_root=ISOLATION_ROOT,
            case_id=case_id,
            perspective=perspective,
            observation=observation,
        )
        _assert_dcp_target_oracle(
            case_id=case_id,
            perspective=perspective,
            observation=observation,
        )

    test.__name__ = f"test_{case_id.lower()}_{perspective}"
    test.__qualname__ = test.__name__
    return test


for _dcp_case_id in ("DCP01", "DCP02"):
    for _dcp_perspective in ("primary", "adversarial", "recovery"):
        globals()[
            f"test_{_dcp_case_id.lower()}_{_dcp_perspective}"
        ] = _make_dcp_red_test(_dcp_case_id, _dcp_perspective)


def _make_dcp04_red_test(perspective: str):
    def test() -> None:
        observation = observe_current_completion_consumer(
            repo=ROOT,
            isolation_root=ISOLATION_ROOT,
            perspective=perspective,
        )
        record_red_node(
            isolation_root=ISOLATION_ROOT,
            case_id="DCP04",
            perspective=perspective,
            observation=observation,
        )
        node_id = str(oracle_for("DCP04", perspective)["failureSignature"])
        assert observation["producerCallCount"] == 1, (
            f"{node_id}:ACTUAL_PRODUCER_CALL_COUNT_INVALID"
        )
        assert observation["readinessConsumerCallCount"] == 1, (
            f"{node_id}:ACTUAL_READINESS_CONSUMER_NOT_CALLED"
        )
        assert observation["qualityConsumer"] == (
            "actual_subprocess_tools.validate_daily_quality"
        )
        assert observation["deepdiveConsumer"] == (
            "actual_deepdive_quality.audit_issue"
        )
        assert observation["gitLocalConsumer"] == (
            "actual_git_rev_parse_archive_tree_ancestor"
        )
        manifest = observation["manifest"]
        assert isinstance(manifest, dict)
        if perspective == "primary":
            assert observation["accepted"] is True, (
                f"{node_id}:CURRENT_COMPLETION_BASELINE_NOT_REACHED"
            )
            for field in (
                "artifactRoot",
                "opsRoot",
                "dailyRootId",
                "rootOperationId",
                "producerOperationId",
                "verifierOperationId",
            ):
                assert manifest.get(field), (
                    f"{node_id}:COMPLETION_LINEAGE_FIELD_ABSENT:{field}"
                )
        else:
            assert observation["accepted"] is False, (
                f"{node_id}:ROOT_OR_INTENT_SUBSTITUTION_ACCEPTED"
            )

    test.__name__ = f"test_dcp04_{perspective}"
    test.__qualname__ = test.__name__
    return test


def _make_dcp05_red_test(perspective: str):
    def test() -> None:
        observation = observe_current_broker_children(
            workspace_harness=ISOLATION_ROOT / "workspace-harness",
            isolation_root=ISOLATION_ROOT,
            perspective=perspective,
        )
        record_red_node(
            isolation_root=ISOLATION_ROOT,
            case_id="DCP05",
            perspective=perspective,
            observation=observation,
        )
        node_id = str(oracle_for("DCP05", perspective)["failureSignature"])
        assert observation["events"], f"{node_id}:CURRENT_LEDGER_NOT_EXERCISED"
        if perspective == "primary":
            actual_processes = [
                item
                for item in observation["launched"]
                if isinstance(item, dict) and "returnCode" in item
            ]
            assert [item["returnCode"] for item in actual_processes] == [0, 0]
            assert len(set(observation["childGrantIds"])) == 2, (
                f"{node_id}:CHILD_GRANT_ID_ABSENT_OR_NOT_DISTINCT"
            )
            assert len(set(observation["processLaunchTokens"])) == 2, (
                f"{node_id}:PROCESS_LAUNCH_TOKEN_ABSENT_OR_NOT_DISTINCT"
            )
        elif perspective == "adversarial":
            predicate = observation["childStatePredicate"]
            assert isinstance(predicate, dict)
            assert all(predicate["derivedFromApis"].values()), (
                f"{node_id}:CHILD_STATE_APIS_ABSENT"
            )
            adversarial = observation["launched"][-1]
            assert adversarial["duplicatePayloadReached"] is False, (
                f"{node_id}:DUPLICATE_CHILD_REACHED_PROCESS"
            )
            api_results = adversarial["adversarialApiResults"]
            assert api_results["wrongState"].get("error") == (
                "CHILD_AUTHORITY_WRONG_STATE"
            ), f"{node_id}:WRONG_STATE_NOT_REJECTED"
            assert api_results["tokenSwap"].get("error") == (
                "CHILD_PROCESS_LAUNCH_TOKEN_MISMATCH"
            ), f"{node_id}:TOKEN_SWAP_NOT_REJECTED"
        else:
            traces = [
                item
                for item in observation["launched"]
                if isinstance(item, dict) and "boundary" in item
            ]
            assert len(traces) == 4, f"{node_id}:CRASH_BOUNDARY_TRACE_INCOMPLETE"
            assert [item.get("brokerProcessReturnCode") for item in traces] == [
                91,
                92,
                93,
                94,
            ], f"{node_id}:CRASH_BOUNDARY_NOT_ACTUALLY_REACHED"
            assert all(
                isinstance(item.get("reconcile"), dict)
                and item["reconcile"].get("available") is True
                for item in traces
            ), (
                f"{node_id}:CRASH_REOPEN_RECONCILE_API_MISSING"
            )
            race = observation["launched"][-1]
            assert race.get("casWinnerCount") == 1, (
                f"{node_id}:RECONCILE_CAS_WINNER_COUNT_INVALID"
            )
            assert race.get("lateLoserPayloadNotReached") is True, (
                f"{node_id}:LATE_LOSER_REACHED_PAYLOAD"
            )
            outcomes = {
                item["boundary"]: item["reconcile"].get("outcome")
                for item in traces
            }
            assert outcomes == observation["recoveryOracle"]["canonicalOutcomes"], (
                f"{node_id}:CANONICAL_RECONCILE_OUTCOMES_INVALID"
            )
            assert all(
                not bool(item["reconcile"].get("autoReissued"))
                for item in traces
                if item["boundary"] == "payload_started_before_completion"
            ), f"{node_id}:PAYLOAD_STARTED_CHILD_AUTO_REISSUED"

    test.__name__ = f"test_dcp05_{perspective}"
    test.__qualname__ = test.__name__
    return test


for _dcp_perspective in ("primary", "adversarial", "recovery"):
    globals()[f"test_dcp04_{_dcp_perspective}"] = _make_dcp04_red_test(
        _dcp_perspective
    )
    globals()[f"test_dcp05_{_dcp_perspective}"] = _make_dcp05_red_test(
        _dcp_perspective
    )
