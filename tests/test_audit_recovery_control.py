from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_high_cost_binding(monkeypatch, tmp_path: Path) -> None:
    """audit unit fixturesはGlobal runtimeではなく明示binding adapterへ束縛する。"""

    control = _control("isolated high-cost binding fixture")
    binding_path = tmp_path / "news-grasp-high-cost-binding-v1.json"
    binding_path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        control,
        "resolve_live_high_cost_binding",
        lambda _root: {
            "bindingPath": str(binding_path),
            "bindingReceiptSha256": "a" * 64,
            "brokerInstalledPath": str(tmp_path / "ai-model-spawn-broker.py"),
        },
    )


def _control(failure_signature: str):
    try:
        return importlib.import_module("tools.audit_recovery_control")
    except ModuleNotFoundError as error:
        pytest.fail(
            f"{failure_signature}: canonical audit recovery consumer is missing: {error}"
        )


def _seal(value: dict[str, object]) -> dict[str, object]:
    body = dict(value)
    encoded = json.dumps(
        body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    body["receiptSha256"] = hashlib.sha256(encoded).hexdigest()
    return body


def _admit_test_execution_receipt(monkeypatch, control) -> None:
    """execute系unit fixtureへ、P03のpre-child receipt admissionを束縛する。"""

    value = {
        "schemaVersion": "RECOVERY_EXECUTION_RECEIPT_V2",
        "receiptSha256": "e" * 64,
        "nonce": "test-execution-nonce",
        "issueDate": "2026-08-13",
    }
    monkeypatch.setattr(
        control.news_grasp_recovery_receipts,
        "validate_recovery_execution_receipt",
        lambda **_kwargs: value,
    )
    monkeypatch.setattr(
        control.news_grasp_recovery_receipts,
        "consumption_status",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        control.news_grasp_recovery_receipts,
        "consume_or_resume",
        lambda **_kwargs: {"status": "consumed_pending_operation"},
    )
    monkeypatch.setattr(
        control.news_grasp_recovery_freshness,
        "verify_recovery_freshness",
        lambda **_kwargs: {
            "schemaVersion": "NEWS_GRASP_RECOVERY_FRESHNESS_V1",
            "ok": True,
            "status": "fresh",
            "reasonCode": "RECOVERY_DEEPDIVE_RUNTIME_FRESHNESS_GREEN",
        },
    )
    monkeypatch.setattr(
        control.news_grasp_recovery_closeout,
        "record_closeout_operation",
        lambda **_kwargs: {"status": "allowed"},
    )
    monkeypatch.setattr(
        control,
        "_run_post_public_automation_completion_guard",
        lambda **_kwargs: {
            "schemaVersion": "NEWS_GRASP_640_COMPLETION_GUARD_V1",
            "ok": True,
            "issueDate": "2026-08-13",
            "scheduled_attempt_status": "failed_then_recovered",
            "recovery_attempt_status": "succeeded",
            "public_status": "green",
            "runner_status": "publish_complete",
            "run_intent": "ScheduledRecoveryFull",
        },
    )
    monkeypatch.setattr(
        control.news_grasp_recovery_closeout,
        "build_exact_finalizer_command",
        lambda **kwargs: {
            "argv": [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(control.CANONICAL_LIVE_BIN_ROOT / "news-grasp-runner.ps1"),
                "-FinalizeVerifiedPublishManifest",
                str(kwargs["publish_manifest_path"]),
                "-RecoveryFinalizationReceiptPath",
                str(kwargs["finalization_receipt_path"]),
            ]
        },
    )

    def completion_from_closeout(**kwargs):
        """post-Green completion seamへ、同日・同rootのsealed evidenceを返す。"""

        issue_date = str(kwargs["issue_date"])
        artifact_root = Path(str(kwargs["artifact_repo_root"])).resolve()
        ops_root = Path(control.CANONICAL_RUNNER_STATE_PATH).parent.resolve()
        lineage = control._completion_lineage(
            issue_date=issue_date,
            run_intent="ScheduledRecoveryFull",
            run_id="test-closeout-run",
            artifact_root=artifact_root,
            ops_root=ops_root,
        )
        completion = _seal(
            {
                "schemaVersion": "SAME_DATE_COMPLETION_EVIDENCE_V1",
                "issuer": control.VERIFIED_COMPLETION_ISSUER,
                "issueDate": issue_date,
                "publishStatusIssueDate": issue_date,
                "runIntent": "ScheduledRecoveryFull",
                "runId": "test-closeout-run",
                **lineage,
                "checks": {field: True for field in control.COMPLETION_FIELDS},
                "evidenceSha256": {
                    field: "9" * 64 for field in control.COMPLETION_FIELDS
                },
                "verificationStatus": "verified_green",
                "publicCompletionStatus": "green",
                "nextRunReadinessStatus": "green",
                "phase": "post_public_closeout",
                "reasonCode": "VERIFIED_GREEN",
                "failedGateIds": [],
            }
        )
        assert control.same_date_completion_green(issue_date, completion)
        return completion

    monkeypatch.setattr(
        control, "_completion_evidence_from_closeout", completion_from_closeout
    )


def _green_completion(control, run_intent: str) -> dict[str, object]:
    lineage = control._completion_lineage(
        issue_date="2026-08-02",
        run_intent=run_intent,
        run_id="test-run",
    )
    return _seal(
        {
            "schemaVersion": "SAME_DATE_COMPLETION_EVIDENCE_V1",
            "issuer": control.VERIFIED_COMPLETION_ISSUER,
            "issueDate": "2026-08-02",
            "publishStatusIssueDate": "2026-08-02",
            "runIntent": run_intent,
            "runId": "test-run",
            **lineage,
            "checks": {field: True for field in control.COMPLETION_FIELDS},
            "evidenceSha256": {
                field: "9" * 64 for field in control.COMPLETION_FIELDS
            },
        }
    )


def _attempt_witness(
    *, scheduled_status: str, recovery_status: str, failure_sha: str = ""
) -> dict[str, object]:
    value: dict[str, object] = {
        "receiptSha256": "f" * 64,
        "scheduledAttemptStatus": scheduled_status,
        "recoveryAttemptStatus": recovery_status,
        "scheduledEventSequence": 1,
        "scheduledEventHash": "a" * 64,
    }
    if failure_sha:
        value.update(
            {
                "failureReceiptSha256": failure_sha,
                "failureEventSequence": 2,
                "failureEventHash": "b" * 64,
            }
        )
    return value


def test_p03_invalid_execution_receipt_is_fail_closed_before_child_start() -> None:
    root = Path(__file__).resolve().parents[1]
    runner = (root / "scripts" / "ops" / "news-grasp-runner.ps1").read_text(
        encoding="utf-8-sig"
    )
    controller = Path(__file__).resolve().parents[1] / "tools" / "audit_recovery_control.py"
    controller_text = controller.read_text(encoding="utf-8")
    assert "RECOVERY_EXECUTION_RECEIPT_INVALID_FAIL_CLOSED" in runner
    assert "RECOVERY_EXECUTION_RECEIPT_INVALID_CONTINUING_WITH_BOUND_AUTHORITY" not in runner
    assert "_admit_test_execution_receipt" not in runner
    assert controller_text.index("validate_recovery_execution_receipt(") < controller_text.index(
        "_run_bounded(command"
    )
    assert "consume_or_resume(" in controller_text


def _failure_context(control, monkeypatch, tmp_path: Path):
    repo = tmp_path / "repo"
    evidence_dir = repo / "build" / "recovery" / "authority"
    evidence_dir.mkdir(parents=True)
    monkeypatch.setattr(control, "CANONICAL_REPO_ROOT", repo)
    failure = _seal(
        {
            "schemaVersion": "SCHEDULED_FAILURE_RECEIPT_V1",
            "issueDate": "2026-08-02",
            "scheduledAttemptStatus": "failed",
            "lastTaskResult": 76,
            "runnerState": "operation_rejected_high_cost_admission",
            "stateSha256": "1" * 64,
            "logSha256": "2" * 64,
            "taskActionSha256": "3" * 64,
            "runnerSha256": "4" * 64,
        }
    )
    failure_path = evidence_dir / "scheduled-failure.json"
    failure_path.write_text(json.dumps(failure), encoding="utf-8")
    authority = _seal(
        {
            "schemaVersion": "SCHEDULED_RECOVERY_AUTHORITY_V1",
            "productId": "News-Grasp",
            "issueDate": "2026-08-02",
            "operationKind": "scheduled_recovery",
            "runIntent": "ScheduledRecoveryFull",
            "missionAuthoritySha256": "5" * 64,
            "failureReceiptSha256": failure["receiptSha256"],
            "taskActionSha256": "6" * 64,
            "runnerSha256": "7" * 64,
            "failedTaskActionSha256": "3" * 64,
            "failedRunnerSha256": "4" * 64,
            "maxExternalModelCalls": 9,
            "maxFullE2EAttempts": 0,
            "noFocusTheft": True,
            "noUserMonitoring": True,
            "noAutoOpen": True,
        }
    )
    authority_path = evidence_dir / "recovery-authority.json"
    authority_path.write_text(json.dumps(authority), encoding="utf-8")
    witness = _seal(
        {
            "schemaVersion": "SCHEDULED_RECOVERY_AUTHORITY_LEDGER_WITNESS_V1",
            "issueDate": "2026-08-02",
            "failureReceiptSha256": failure["receiptSha256"],
            "authorityReceiptSha256": authority["receiptSha256"],
            "ledgerEventSequence": 3,
            "ledgerEventHash": "8" * 64,
        }
    )
    monkeypatch.setattr(
        control,
        "_validate_recovery_authority_via_broker",
        lambda **_: (authority, witness),
    )
    monkeypatch.setattr(
        control,
        "_inspect_attempt_via_broker",
        lambda **_: _attempt_witness(
            scheduled_status="failed",
            recovery_status="not_started",
            failure_sha=str(failure["receiptSha256"]),
        ),
    )
    return failure_path, authority_path


def _terminal_test_root(control, monkeypatch, tmp_path: Path, name: str) -> Path:
    repo = tmp_path / name
    repo.mkdir()
    root = repo / "build" / "incidents"
    monkeypatch.setattr(control, "CANONICAL_REPO_ROOT", repo)
    monkeypatch.setattr(control, "CANONICAL_TERMINAL_ROOT", root)
    return root


def test_audit_normal_requires_actual_same_date_public_green(monkeypatch) -> None:
    control = _control("RED_AUDIT_NORMAL_CONSUMER_MISSING")
    monkeypatch.setattr(
        control,
        "_verify_same_date_completion",
        lambda **_: _green_completion(control, "ScheduledProduction"),
    )
    monkeypatch.setattr(
        control,
        "_inspect_attempt_via_broker",
        lambda **_: _attempt_witness(
            scheduled_status="reserved", recovery_status="not_started"
        ),
    )
    decision = control.decide_audit_recovery(
        {
            "issueDate": "2026-08-02",
            "repairDecision": {"classification": "normal"},
        }
    )
    assert decision["terminal"] == "audit_normal_green"
    assert decision["completionEvidenceSha256"]
    assert decision["completionEvidence"]["receiptSha256"] == decision["completionEvidenceSha256"]


def test_caller_boolean_or_url_200_cannot_produce_green(monkeypatch) -> None:
    control = _control("RED_STALE_PUBLIC_GREEN_REJECTOR_MISSING")
    monkeypatch.setattr(control, "_verify_same_date_completion", lambda **_: None)
    monkeypatch.setattr(
        control,
        "_inspect_attempt_via_broker",
        lambda **_: _attempt_witness(
            scheduled_status="reserved", recovery_status="not_started"
        ),
    )
    decision = control.decide_audit_recovery(
        {
            "issueDate": "2026-08-02",
            "repairDecision": {"classification": "normal"},
            "completion": {
                "quality": True,
                "publishStatusIssueDate": "2026-08-01",
                "httpStatus": 200,
            },
        }
    )
    assert decision["terminal"] == "audit_major_incident_open"
    assert decision["reasonCode"] == "SAME_DATE_COMPLETION_EVIDENCE_INVALID"


def test_recoverable_failure_uses_ledger_backed_authority_not_deferred(
    monkeypatch, tmp_path: Path
) -> None:
    control = _control("RED_RECOVERY_DECISION_CONSUMER_MISSING")
    failure_path, authority_path = _failure_context(control, monkeypatch, tmp_path)
    decision = control.decide_audit_recovery(
        {
            "issueDate": "2026-08-02",
            "scheduledFailureReceiptPath": str(failure_path),
            "repairDecision": {"classification": "recoverable"},
            "recoveryAuthorityPath": str(authority_path),
        }
    )
    assert decision["action"] == "scheduled_recovery"
    assert decision["terminal"] is None
    assert decision["recoveryAuthorityLedgerWitnessSha256"]


def test_startup_terminal_state_without_repo_dir_can_enter_scheduled_recovery(
    monkeypatch, tmp_path: Path
) -> None:
    control = _control("STARTUP_TERMINAL_STATE_REPO_DIR_REQUIRED_FALSE_RED")
    failure_path, authority_path = _failure_context(control, monkeypatch, tmp_path)
    state_path = tmp_path / "runner-state.json"
    state_path.write_text(
        json.dumps(
            {
                "status": "blocked_startup_self_repair_failed",
                "date": "2026-08-02",
                "run_intent": "ScheduledProduction",
                "run_id": "startup-run",
                "phase": "startup_self_repair",
                "exit_code": 72,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(control, "CANONICAL_RUNNER_STATE_PATH", state_path)
    monkeypatch.setattr(control, "_verify_same_date_completion", lambda **_: None)

    decision = control.decide_audit_recovery(
        {
            "issueDate": "2026-08-02",
            "scheduledFailureReceiptPath": str(failure_path),
            "recoveryAuthorityPath": str(authority_path),
        }
    )

    assert decision["action"] == "scheduled_recovery"
    assert decision["terminal"] is None
    assert decision["recoveryBranch"] == "ScheduledRecoveryFull"


def test_external_readiness_reopens_typed_recovery(monkeypatch, tmp_path: Path) -> None:
    control = _control("RED_EXTERNAL_READINESS_RECOVERY_REOPEN_MISSING")
    repo = tmp_path / "repo"
    runner_dir = repo / "build" / "runner"
    runner_dir.mkdir(parents=True)
    state_path = runner_dir / "runner-state.json"
    log_path = runner_dir / "runner.log"
    state_path.write_text(
        json.dumps(
            {
                "repo_dir": str(repo),
                "log_path": str(log_path),
                "exit_code": 126,
            }
        ),
        encoding="utf-8",
    )
    log_path.write_text("external control plane unavailable", encoding="utf-8")
    monkeypatch.setattr(control, "CANONICAL_REPO_ROOT", repo)
    monkeypatch.setattr(control, "CANONICAL_RUNNER_STATE_PATH", state_path)
    monkeypatch.setattr(
        control,
        "validate_canonical_operational_registry",
        lambda _root: {"status": "Green"},
    )
    monkeypatch.setattr(control, "_observe_operational_truth", lambda **_: None)
    monkeypatch.setattr(
        control,
        "_inspect_attempt_via_broker",
        lambda **_: _attempt_witness(
            scheduled_status="failed",
            recovery_status="not_started",
            failure_sha="e" * 64,
        ),
    )
    from tools import news_grasp_daily_control, news_grasp_external_control

    monkeypatch.setattr(
        news_grasp_daily_control,
        "classify_observed_failure",
        lambda **_: "external_control_plane_unavailable",
    )
    monkeypatch.setattr(
        news_grasp_external_control,
        "probe_external_readiness",
        lambda: {"status": "ready"},
    )
    monkeypatch.setattr(control, "_resolve_artifact_repo_root", lambda _payload: repo)
    monkeypatch.setattr(
        control,
        "_validate_scheduled_failure_path",
        lambda *_, **__: {"receiptSha256": "e" * 64},
    )
    monkeypatch.setattr(
        control,
        "_validate_recovery_authority_via_broker",
        lambda **_: (
            {"receiptSha256": "a" * 64},
            {"receiptSha256": "b" * 64},
        ),
    )

    decision = control.decide_audit_recovery(
        {
            "issueDate": "2026-08-02",
            "scheduledFailureReceiptPath": str(runner_dir / "failure.json"),
            "recoveryAuthorityPath": str(runner_dir / "authority.json"),
        }
    )

    assert decision["classification"] == "recoverable"
    assert decision["action"] == "scheduled_recovery"
    assert decision["reasonCode"] == "TYPED_RECOVERY_AUTHORITY_READY"


def test_incomplete_public_surface_seals_same_day_recovery_as_first_priority(
    monkeypatch, tmp_path: Path
) -> None:
    control = _control("RED_SAME_DAY_PUBLIC_RECOVERY_PRIORITY_MISSING")
    failure_path, authority_path = _failure_context(control, monkeypatch, tmp_path)
    decision = control.decide_audit_recovery(
        {
            "issueDate": "2026-08-02",
            "scheduledFailureReceiptPath": str(failure_path),
            "repairDecision": {"classification": "recoverable"},
            "recoveryAuthorityPath": str(authority_path),
        }
    )
    assert decision["publicStatus"] == "incomplete"
    assert decision["workPriority"] == "same_day_public_recovery_first"
    assert decision["allowedBeforePublicGreen"] == [
        "scheduled_recovery",
        "minimal_recovery_unblocker",
        "escalate_major_incident",
    ]
    assert decision["forbiddenBeforePublicGreen"] == [
        "incident_report_polish",
        "root_cause_hardening",
        "unrelated_cleanup",
    ]


def test_sealer_rejects_incomplete_public_decision_without_recovery_priority() -> None:
    control = _control("RED_PRIORITY_SEAL_FAIL_CLOSED_MISSING")
    with pytest.raises(ValueError, match="AUDIT_DECISION_RECEIPT_INVALID"):
        control.seal_audit_decision(
            {
                "schemaVersion": "AUDIT_RECOVERY_DECISION_V1",
                "issueDate": "2026-08-02",
                "classification": "incident_required",
                "action": "escalate_major_incident",
                "terminal": "audit_major_incident_open",
                "reasonCode": "TEST",
                "scheduledAttemptStatus": "failed",
                "recoveryAttemptStatus": "not_started",
                "publicStatus": "incomplete",
                "operationState": "incident_open",
            }
        )


def test_green_public_surface_releases_only_runner_finalization_after_recovery(
    monkeypatch, tmp_path: Path
) -> None:
    control = _control("RED_ROOT_CAUSE_AFTER_PUBLIC_GREEN_MISSING")
    failure_path, authority_path = _failure_context(control, monkeypatch, tmp_path)
    failure_value = json.loads(failure_path.read_text(encoding="utf-8"))
    authority_value = json.loads(authority_path.read_text(encoding="utf-8"))
    monkeypatch.setattr(
        control,
        "_inspect_attempt_via_broker",
        lambda **_: {
            **_attempt_witness(
                scheduled_status="failed",
                recovery_status="started",
                failure_sha=str(failure_value["receiptSha256"]),
            ),
            "recoveryAuthorityReceiptSha256": authority_value["receiptSha256"],
            "recoveryEventSequence": 4,
            "recoveryEventHash": "e" * 64,
        },
    )
    monkeypatch.setattr(
        control,
        "_verify_same_date_completion",
        lambda **_: _green_completion(control, "ScheduledRecoveryFull"),
    )
    decision = control.decide_audit_recovery(
        {
            "issueDate": "2026-08-02",
            "scheduledFailureReceiptPath": str(failure_path),
            "repairDecision": {"classification": "recoverable"},
            "recoveryAuthorityPath": str(authority_path),
        }
    )
    assert decision["publicStatus"] == "green"
    assert decision["workPriority"] == "runner_finalization_only"
    assert decision["allowedAfterPublicGreen"] == (
        "finalizer_exact_args_replay",
        "receipt_reseal",
        "completion_guard",
        "verify_public_surface",
        "final_report",
    )


def test_missing_or_unvalidated_authority_is_major_incident(
    monkeypatch, tmp_path: Path
) -> None:
    control = _control("RED_RECOVERY_AUTHORITY_VALIDATOR_MISSING")
    failure_path, _ = _failure_context(control, monkeypatch, tmp_path)
    monkeypatch.setattr(
        control,
        "_validate_recovery_authority_via_broker",
        lambda **_: (_ for _ in ()).throw(ValueError("forged")),
    )
    decision = control.decide_audit_recovery(
        {
            "issueDate": "2026-08-02",
            "scheduledFailureReceiptPath": str(failure_path),
            "repairDecision": {"classification": "recoverable"},
            "recoveryAuthorityPath": str(tmp_path / "forged.json"),
        }
    )
    assert decision["terminal"] == "audit_major_incident_open"
    assert decision["reasonCode"] == "RECOVERY_AUTHORITY_INVALID"


def test_unknown_repair_class_is_major_incident_not_silent_deferred(monkeypatch) -> None:
    control = _control("RED_UNKNOWN_REPAIR_ESCALATION_MISSING")
    monkeypatch.setattr(
        control,
        "_inspect_attempt_via_broker",
        lambda **_: _attempt_witness(
            scheduled_status="failed",
            recovery_status="not_started",
            failure_sha="d" * 64,
        ),
    )
    decision = control.decide_audit_recovery(
        {
            "issueDate": "2026-08-02",
            "repairDecision": {"classification": "incident_required"},
        }
    )
    assert decision["terminal"] == "audit_major_incident_open"
    assert decision["action"] == "escalate_major_incident"


def test_failed_schedule_recovered_keeps_failed_history(
    monkeypatch, tmp_path: Path
) -> None:
    control = _control("RED_FAILURE_LINEAGE_PRESERVATION_MISSING")
    failure_path, authority_path = _failure_context(control, monkeypatch, tmp_path)
    failure_value = json.loads(failure_path.read_text(encoding="utf-8"))
    monkeypatch.setattr(
        control,
        "_inspect_attempt_via_broker",
        lambda **_: {
            **_attempt_witness(
                scheduled_status="failed",
                recovery_status="started",
                failure_sha=str(failure_value["receiptSha256"]),
            ),
            "recoveryAuthorityReceiptSha256": json.loads(
                authority_path.read_text(encoding="utf-8")
            )["receiptSha256"],
            "recoveryEventSequence": 4,
            "recoveryEventHash": "e" * 64,
        },
    )
    monkeypatch.setattr(
        control,
        "_verify_same_date_completion",
        lambda **_: _green_completion(control, "ScheduledRecoveryFull"),
    )
    decision = control.decide_audit_recovery(
        {
            "issueDate": "2026-08-02",
            "scheduledFailureReceiptPath": str(failure_path),
            "repairDecision": {"classification": "recoverable"},
            "recoveryAuthorityPath": str(authority_path),
        }
    )
    assert decision["terminal"] == "audit_recovered_green"
    assert decision["scheduledAttemptStatus"] == "failed"
    assert decision["recoveryAttemptStatus"] == "succeeded"


def test_terminal_writer_has_internal_root_and_only_three_terminals(
    monkeypatch, tmp_path: Path
) -> None:
    control = _control("RED_TYPED_TERMINAL_WRITER_MISSING")
    incident_root = _terminal_test_root(
        control, monkeypatch, tmp_path, "terminal-writer-repo"
    )
    invalid = _seal(
        {
            "schemaVersion": "AUDIT_RECOVERY_DECISION_V1",
            "issuer": control.DECISION_ISSUER,
            "terminal": "operation_deferred",
            "issueDate": "2026-08-02",
        }
    )
    with pytest.raises(ValueError, match="AUDIT_TERMINAL_INVALID"):
        control.write_audit_terminal(invalid)
    decision = control.seal_audit_decision(
        {
            "schemaVersion": "AUDIT_RECOVERY_DECISION_V1",
            "issueDate": "2026-08-02",
            "classification": "incident_required",
            "action": "escalate_major_incident",
            "terminal": "audit_major_incident_open",
            "reasonCode": "TEST_INCIDENT",
            "scheduledAttemptStatus": "failed",
            "recoveryAttemptStatus": "not_started",
            "publicStatus": "incomplete",
            "operationState": "incident_open",
            "workPriority": "same_day_public_recovery_first",
            "allowedBeforePublicGreen": [
                "scheduled_recovery",
                "minimal_recovery_unblocker",
                "escalate_major_incident",
            ],
            "forbiddenBeforePublicGreen": [
                "incident_report_polish",
                "root_cause_hardening",
                "unrelated_cleanup",
            ],
            "owner": "News-Grasp Operations",
            "nextAction": "resume_same_date_recovery_from_verified_stop_point",
            "evidenceSha256": "a" * 64,
        }
    )
    terminal = control.write_audit_terminal(decision)
    assert terminal["decisionReceiptSha256"] == decision["receiptSha256"]
    assert terminal["owner"] == "News-Grasp Operations"
    assert terminal["nextAction"] == "resume_same_date_recovery_from_verified_stop_point"
    assert terminal["evidenceSha256"] == "a" * 64
    assert (incident_root / "2026-08-02-audit-terminal.json").is_file()
    parser_source = Path(control.__file__).read_text(encoding="utf-8-sig")
    assert "--terminal-root" not in parser_source
    assert "--terminal-output" not in parser_source


def test_actual_completion_verifier_owns_all_required_gates() -> None:
    control = _control("RED_ACTUAL_COMPLETION_VERIFIER_MISSING")
    source = Path(control.__file__).read_text(encoding="utf-8-sig")
    assert '"tools.validate_daily_quality"' in source
    assert '"--require-deepdive"' in source
    assert "from tools.daily_self_heal import verify_publish_complete" in source
    assert "publish = verify_publish_complete(" in source
    assert 'child_env["PYTHONUTF8"] = "1"' in source
    assert 'child_env["PYTHONIOENCODING"] = "utf-8"' in source
    assert 'runner_state.get("run_intent") != expected_run_intent' in source
    assert 'runner_state.get("status") != "publish_complete"' in source


def test_recovery_execution_manifest_requires_bounded_human_impact() -> None:
    control = _control("RED_HUMAN_IMPACT_GATE_MISSING")
    with pytest.raises(ValueError, match="HUMAN_IMPACT_CONTRACT_INVALID"):
        control.validate_recovery_execution_manifest(
            {
                "issueDate": "2026-08-02",
                "runIntent": "ScheduledRecoveryFull",
                "maxExternalModelCalls": 9,
                "maxFullE2EAttempts": 0,
                "noFocusTheft": False,
                "noUserMonitoring": True,
                "noAutoOpen": True,
            }
        )


def test_recovery_execution_manifest_rejects_cross_date_and_substitution() -> None:
    control = _control("RED_RECOVERY_EXECUTION_BINDING_MISSING")
    manifest = {
        "issueDate": "2026-08-03",
        "runIntent": "ScheduledRecoveryFull",
        "maxExternalModelCalls": 9,
        "maxFullE2EAttempts": 0,
        "noFocusTheft": True,
        "noUserMonitoring": True,
        "noAutoOpen": True,
        "recoveryAuthorityReceiptSha256": "a" * 64,
        "artifactRepoHead": "b" * 40,
        "runnerSha256": "c" * 64,
    }
    with pytest.raises(ValueError, match="RECOVERY_EXECUTION_BINDING_INVALID"):
        control.validate_recovery_execution_manifest(
            manifest,
            issue_date="2026-08-02",
            authority_receipt_sha256="a" * 64,
            artifact_repo_head="b" * 40,
            runner_sha256="c" * 64,
        )


def test_invalid_issue_date_is_rejected_before_decision() -> None:
    control = _control("RED_ISSUE_DATE_VALIDATOR_MISSING")
    with pytest.raises(ValueError, match="AUDIT_RECOVERY_DATE_INVALID"):
        control.decide_audit_recovery({"issueDate": "2026-8-2"})


def test_json_loader_uses_single_bounded_handle(tmp_path: Path) -> None:
    control = _control("RED_BOUNDED_SINGLE_HANDLE_LOADER_MISSING")
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"{" + b" " * control.MAX_JSON_BYTES + b"}")
    with pytest.raises(ValueError, match="AUDIT_RECOVERY_INPUT_INVALID"):
        control._load(oversized)


def test_bounded_subprocess_rejects_stderr_over_budget(tmp_path: Path) -> None:
    control = _control("RED_STDERR_BUDGET_MISSING")
    with pytest.raises(ValueError, match="BOUNDED_SUBPROCESS_OUTPUT_EXCEEDED"):
        control._run_bounded(
            [
                sys.executable,
                "-c",
                "import sys; sys.stderr.buffer.write(b'x' * (1024 * 1024 + 1))",
            ],
            cwd=tmp_path,
            timeout=30,
        )


def test_bounded_subprocess_uses_creation_time_owned_job() -> None:
    control = _control("RED_PROCESS_TREE_OWNERSHIP_MISSING")
    source = Path(control.__file__).read_text(encoding="utf-8-sig")
    boundary = (Path(control.__file__).parent / "news_grasp_owned_process.py").read_text(
        encoding="utf-8-sig"
    )
    assert "run_owned_bounded" in source
    assert "PROC_THREAD_ATTRIBUTE_JOB_LIST" in boundary
    assert "JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE" in boundary
    assert "CREATE_SUSPENDED" in boundary
    assert "TerminateProcess" not in boundary
    assert "AssignProcessToJobObject" not in boundary


def test_windows_owned_job_boundary_rejects_missing_executable(tmp_path: Path) -> None:
    from tools.news_grasp_owned_process import OwnedProcessError, run_owned_bounded

    with pytest.raises(OwnedProcessError, match="OWNED_PROCESS_EXECUTABLE_INVALID"):
        run_owned_bounded(
            ["definitely-missing-news-grasp-executable.exe"],
            cwd=tmp_path,
            timeout=1,
            max_output_bytes=1024,
        )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object contract")
def test_output_overflow_terminates_owned_grandchild(tmp_path: Path) -> None:
    control = _control("RED_OWNED_GRANDCHILD_SURVIVAL")
    sentinel = tmp_path / "grandchild-survived.txt"
    child = (
        "import time; from pathlib import Path; "
        f"time.sleep(2); Path({str(sentinel)!r}).write_text('alive')"
    )
    parent = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable,'-c',{child!r}]); "
        "sys.stderr.buffer.write(b'x' * (1024 * 1024 + 1)); "
        "sys.stderr.flush(); time.sleep(30)"
    )
    with pytest.raises(ValueError, match="BOUNDED_SUBPROCESS_OUTPUT_EXCEEDED"):
        control._run_bounded(
            [sys.executable, "-c", parent], cwd=tmp_path, timeout=30
        )
    import time

    time.sleep(3)
    assert not sentinel.exists()


def test_runner_exposes_full_generation_scheduled_recovery_intent() -> None:
    runner = (
        Path(__file__).resolve().parents[1] / "scripts" / "ops" / "news-grasp-runner.ps1"
    ).read_text(encoding="utf-8-sig")
    assert "ScheduledRecoveryFull" in runner
    assert "$RunIntent -eq 'ScheduledRecoveryFull'" in runner
    assert "run_intent = $RunIntent" in runner


def test_recover_only_is_not_the_all_artifacts_missing_recovery_path() -> None:
    control = _control("RED_RECOVER_ONLY_GUARD_MISSING")
    decision = control.select_recovery_run_intent(
        issue_date="2026-08-02",
        artifacts={
            "summary": False,
            "deepDive": False,
            "docs": False,
            "distribution": False,
            "notification": False,
        },
    )
    assert decision == "ScheduledRecoveryFull"


def test_bootstrap_nonce_and_authority_writes_are_fresh_and_atomic() -> None:
    bootstrap = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "ops"
        / "news-grasp-bootstrap.ps1"
    ).read_text(encoding="utf-8-sig")
    assert "[Guid]::NewGuid().ToString('N')" in bootstrap
    assert '"bootstrap-$DateStamp-$taskActionSha256-$runnerSha256"' not in bootstrap
    assert "Write-AtomicUtf8Text -Path $missionPath" in bootstrap
    assert "Write-AtomicUtf8Text -Path $launchPermitPath" in bootstrap
    assert "[IO.File]::Replace($temporary, $Path, $null" not in bootstrap
    assert "$replacementBackup" in bootstrap
    installer = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "ops"
        / "install-news-grasp-ops.ps1"
    ).read_text(encoding="utf-8-sig")
    assert "[IO.File]::Replace($temporary, $Path, $null" not in installer
    assert "Write-NewsGraspAtomicFile" in installer
    guard = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "ops"
        / "install-news-grasp-ops-guard.ps1"
    ).read_text(encoding="utf-8-sig")
    assert "[NewsGraspVerifiedFileBoundary]::WriteAtomic($Path, $Bytes)" in guard
    boundary = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "ops"
        / "install-news-grasp-verified-file-boundary.ps1"
    ).read_text(encoding="utf-8-sig")
    assert "public static string WriteAtomic(string path, byte[] bytes)" in boundary
    assert "FlushFileBuffers(temporaryHandle)" in boundary
    assert "RenameByHandle(temporaryHandle, destination)" in boundary
    assert "MarkDelete(temporaryHandle)" in boundary
    assert "NEWS_GRASP_ATOMIC_POSTCOMMIT_HASH_MISMATCH" in boundary


def test_audit_cli_cannot_self_mint_completion_or_trust_plain_attempt_status() -> None:
    control = _control("RED_AUDIT_SELF_MINTING_SURFACE_PRESENT")
    source = Path(control.__file__).read_text(encoding="utf-8-sig")
    assert 'sub.add_parser("check-completion")' not in source
    assert 'payload.get("scheduledAttempt")' not in source
    assert 'payload.get("recoveryAttempt")' not in source
    assert "_inspect_attempt_via_broker" in source


def test_automation_and_repair_skill_use_executable_fixed_terminal_contract() -> None:
    home = Path.home()
    automation = (
        home / ".codex" / "automations" / "news-grasp-6-40" / "automation.toml"
    ).read_text(encoding="utf-8-sig")
    skill = (
        home / ".codex" / "skills" / "news-grasp-repair-method" / "SKILL.md"
    ).read_text(encoding="utf-8-sig")
    for source in (automation, skill):
        assert "python -m tools.audit_recovery_control decide --input <audit-input.json>" in source
        assert "python -m tools.audit_recovery_control execute --input <audit-input.json>" in source
        assert "audit agent は runner を直接起動しない" in source
        assert "artifactRepoRoot" in source
        assert "opsRepoRoot" in source
        assert "recoveryExecution" in source
        assert "recoveryAuthorityReceiptSha256" in source
        assert "artifactRepoHead" in source
        assert "runnerSha256" in source
        assert "artifact repo内のrunnerを実行しない" in source
        assert "--terminal-output <terminal.json>" not in source
        assert "build/incidents/<issue-date>-audit-terminal.json" in source
        assert "durable ledger" in source
        assert "validate_daily_quality --require-deepdive" in source
        assert "inspect-news-grasp-attempt" in source
        assert "scheduledFailureReceiptPath" in source
        assert "scheduledAttempt.status" not in source
        assert "recoveryAttempt.status" not in source
        assert "runnerStatePath" not in source
        assert "production_scheduled_run" in source
        assert "production_recovery_run" in source
        assert "audit_run" in source
        assert "artifact_repo_root" in source
        assert "ops_repo_root" in source
        assert "FinalizeVerifiedPublishManifest" in source
        assert "audit_major_incident_open" in source
        assert "same_day_public_recovery_first" in source
        assert "incident_report_polish" in source
        assert "root_cause_hardening" in source
        assert "attempt_terminal" in source
        assert "scheduled_failure_receipt_path" in source
        assert "audit_major_incident_open" in source


def test_artifact_executable_tree_rejects_dirty_same_origin_substitution(
    monkeypatch, tmp_path: Path
) -> None:
    control = _control("RED_ARTIFACT_EXECUTABLE_SUBSTITUTION_MISSING")
    artifact = tmp_path / "same-origin-forgery"
    artifact.mkdir()

    def git_text(root: Path, *args: str) -> str:
        if args == ("rev-parse", "HEAD"):
            return "a" * 40
        if args == ("rev-parse", "refs/remotes/origin/main"):
            return "a" * 40
        if args and args[0] == "status":
            return "?? tools/generate_pages.py"
        if args == ("rev-parse", "--path-format=absolute", "--git-common-dir"):
            return str(root / ".git")
        raise AssertionError((root, args))

    monkeypatch.setattr(control, "_git_text", git_text)
    with pytest.raises(ValueError, match="ARTIFACT_EXECUTABLE_TREE_INVALID"):
        control._validate_artifact_executable_tree(artifact)


def test_artifact_repo_rejects_unregistered_same_origin_clone(
    monkeypatch, tmp_path: Path
) -> None:
    control = _control("RED_UNREGISTERED_CLONE_ACCEPTED")
    canonical = tmp_path / "canonical"
    candidate = tmp_path / "candidate"
    canonical.mkdir()
    candidate.mkdir()
    monkeypatch.setattr(control, "CANONICAL_REPO_ROOT", canonical)

    def git_text(root: Path, *args: str) -> str:
        if args == ("rev-parse", "--show-toplevel"):
            return str(root)
        if args == ("remote", "get-url", "origin"):
            return "https://example.invalid/news-grasp.git"
        if args == ("rev-parse", "--path-format=absolute", "--git-common-dir"):
            return str(root / ".git")
        raise AssertionError((root, args))

    monkeypatch.setattr(control, "_git_text", git_text)
    with pytest.raises(ValueError, match="ARTIFACT_REPO_IDENTITY_INVALID"):
        control._resolve_artifact_repo_root(
            {"artifactRepoRoot": str(candidate), "opsRepoRoot": str(canonical)}
        )


def test_same_date_completion_separates_artifact_data_from_trusted_ops_code(
    monkeypatch, tmp_path: Path
) -> None:
    """復旧artifactのbuild証跡は保持しつつ、検証codeはcleanな現行ops treeだけを使う。"""
    control = _control("RED_ARTIFACT_AND_OPS_ROOT_CONFLATED")
    from tools import daily_self_heal

    artifact = tmp_path / "artifact"
    ops = tmp_path / "ops"
    state_path = tmp_path / "bin" / "news-grasp-runner-state.json"
    artifact.mkdir()
    ops.mkdir()
    guard_path = artifact / "build" / "publish-complete" / "2026-08-09.automation-guard.json"
    guard_path.parent.mkdir(parents=True)
    guard_path.write_text(
        json.dumps(
            {
                "schemaVersion": "NEWS_GRASP_640_COMPLETION_GUARD_V1",
                "ok": True,
                "issueDate": "2026-08-09",
                "scheduled_attempt_status": "failed_then_recovered",
                "recovery_attempt_status": "succeeded",
                "public_status": "green",
                "runner_status": "publish_complete",
            }
        ),
        encoding="utf-8",
    )
    state_path.parent.mkdir()
    state_path.write_text(
        json.dumps(
            {
                "date": "2026-08-09",
                "status": "publish_complete",
                "exit_code": 0,
                "run_intent": "ScheduledRecoveryFull",
                "run_id": "a" * 32,
                "artifactRoot": str(artifact),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(control, "CANONICAL_REPO_ROOT", ops)
    monkeypatch.setattr(control, "CANONICAL_RUNNER_STATE_PATH", state_path)

    resolved_payloads: list[dict[str, object]] = []
    validated_roots: list[Path] = []
    publish_calls: list[dict[str, object]] = []

    def resolve(payload: dict[str, object]) -> Path:
        resolved_payloads.append(payload)
        return artifact

    def validate(root: Path) -> str:
        validated_roots.append(root)
        return "b" * 40

    def run_bounded(command, *, cwd, timeout, env_overrides=None):
        assert command[1:3] == ["-P", "-m"]
        assert cwd == artifact
        assert env_overrides == {
            "PYTHONPATH": str(ops),
            "PYTHONNOUSERSITE": "1",
        }
        return 0, b'{"ok": true}'

    def verify_publish_complete(**kwargs):
        publish_calls.append(kwargs)
        lineage = control._completion_lineage(
            issue_date="2026-08-09",
            run_intent="ScheduledRecoveryFull",
            run_id="a" * 32,
            artifact_root=artifact,
            ops_root=ops,
        )
        return {
            "ok": True,
            "date": "2026-08-09",
            **lineage,
        }

    monkeypatch.setattr(control, "_resolve_artifact_repo_root", resolve)
    monkeypatch.setattr(control, "_validate_artifact_executable_tree", validate)
    monkeypatch.setattr(control, "_run_bounded", run_bounded)
    monkeypatch.setattr(daily_self_heal, "verify_publish_complete", verify_publish_complete)

    completion = control._verify_same_date_completion(
        issue_date="2026-08-09",
        payload={"verificationWaitSec": 0, "verificationPollSec": 10},
        expected_run_intent="ScheduledRecoveryFull",
    )

    assert completion is not None
    assert control.same_date_completion_green("2026-08-09", completion) is True
    assert resolved_payloads == [
        {"artifactRepoRoot": str(artifact), "opsRepoRoot": str(ops)}
    ]
    assert validated_roots == [ops]
    assert publish_calls[0]["repo_root"] == artifact
    assert publish_calls[0]["ops_repo_root"] == ops


def test_artifact_tree_rejects_assume_unchanged_byte_substitution(
    monkeypatch, tmp_path: Path
) -> None:
    control = _control("RED_ASSUME_UNCHANGED_SUBSTITUTION_ACCEPTED")
    canonical = tmp_path / "canonical"
    candidate = tmp_path / "candidate"
    subprocess.run(["git", "init", str(canonical)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(canonical), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(canonical), "config", "user.name", "News Grasp Test"],
        check=True,
    )
    tool = canonical / "tools" / "generate_pages.py"
    tool.parent.mkdir()
    tool.write_text("print('trusted')\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(canonical), "add", "tools/generate_pages.py"], check=True)
    subprocess.run(["git", "-C", str(canonical), "commit", "-m", "fixture"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(canonical), "update-ref", "refs/remotes/origin/main", "HEAD"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(canonical), "worktree", "add", str(candidate), "HEAD"],
        check=True,
        capture_output=True,
    )
    candidate_tool = candidate / "tools" / "generate_pages.py"
    candidate_tool.write_text("print('substituted')\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(candidate), "update-index", "--assume-unchanged", "tools/generate_pages.py"],
        check=True,
    )
    monkeypatch.setattr(control, "CANONICAL_REPO_ROOT", canonical)

    with pytest.raises(ValueError, match="ARTIFACT_EXECUTABLE_TREE_INVALID"):
        control._validate_artifact_executable_tree(candidate)


def test_artifact_tree_rejects_python_startup_customization(
    monkeypatch, tmp_path: Path
) -> None:
    control = _control("RED_PYTHON_STARTUP_CUSTOMIZATION_ACCEPTED")
    artifact = tmp_path / "artifact"
    trusted = b"print('trusted')\n"
    tool = artifact / "tools" / "generate_pages.py"
    tool.parent.mkdir(parents=True)
    tool.write_bytes(trusted)
    (artifact / "sitecustomize.py").write_text("raise SystemExit(99)\n", encoding="utf-8")
    blob = hashlib.sha1(f"blob {len(trusted)}\0".encode("ascii") + trusted).hexdigest()

    def git_text(_root: Path, *args: str) -> str:
        if args in {
            ("rev-parse", "HEAD"),
            ("rev-parse", "refs/remotes/origin/main"),
        }:
            return "a" * 40
        if args and args[0] == "status":
            return ""
        if args and args[0] == "ls-files":
            return "H tools/generate_pages.py"
        if args == ("rev-parse", "--path-format=absolute", "--git-common-dir"):
            return str(artifact / ".git")
        raise AssertionError(args)

    tree = f"100644 blob {blob}\ttools/generate_pages.py\0".encode("utf-8")
    monkeypatch.setattr(control, "_git_text", git_text)
    monkeypatch.setattr(control, "_git_bytes", lambda *_args, **_kwargs: tree)
    monkeypatch.setattr(control, "CANONICAL_REPO_ROOT", artifact)

    with pytest.raises(ValueError, match="ARTIFACT_EXECUTABLE_TREE_INVALID"):
        control._validate_artifact_executable_tree(artifact)


def test_isolated_verifier_does_not_execute_artifact_sitecustomize(tmp_path: Path) -> None:
    control = _control("RED_VERIFIER_SITECUSTOMIZE_EXECUTED")
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    sentinel = tmp_path / "sitecustomize-executed.txt"
    (artifact / "sitecustomize.py").write_text(
        f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('executed')\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(artifact)
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            str(Path(control.__file__).resolve()),
            "verify-artifact-tree",
            "--artifact-root",
            str(artifact),
        ],
        cwd=artifact,
        env=env,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert not sentinel.exists()


def test_artifact_tree_rejects_gitattributes_clean_filter_without_execution(
    monkeypatch, tmp_path: Path
) -> None:
    control = _control("RED_GIT_CLEAN_FILTER_EXECUTED")
    artifact = tmp_path / "artifact"
    trusted = b"print('trusted')\n"
    tool = artifact / "tools" / "generate_pages.py"
    tool.parent.mkdir(parents=True)
    tool.write_bytes(trusted)
    sentinel = tmp_path / "filter-executed.txt"
    (artifact / ".gitattributes").write_text("* filter=evil\n", encoding="utf-8")
    blob = hashlib.sha1(f"blob {len(trusted)}\0".encode("ascii") + trusted).hexdigest()

    def git_text(_root: Path, *args: str) -> str:
        if args in {
            ("rev-parse", "HEAD"),
            ("rev-parse", "refs/remotes/origin/main"),
        }:
            return "a" * 40
        if args == ("rev-parse", "--path-format=absolute", "--git-common-dir"):
            return str(artifact / ".git")
        if args and args[0] == "status":
            return ""
        if args and args[0] == "ls-files":
            return "H tools/generate_pages.py"
        raise AssertionError(args)

    tree = f"100644 blob {blob}\ttools/generate_pages.py\0".encode("utf-8")
    monkeypatch.setattr(control, "_git_text", git_text)
    monkeypatch.setattr(control, "_git_bytes", lambda *_args, **_kwargs: tree)
    monkeypatch.setattr(control, "CANONICAL_REPO_ROOT", artifact)

    with pytest.raises(ValueError, match="ARTIFACT_EXECUTABLE_TREE_INVALID"):
        control._validate_artifact_executable_tree(artifact)
    assert not sentinel.exists()


def test_artifact_tree_rejects_command_bearing_local_filter_config(
    monkeypatch, tmp_path: Path
) -> None:
    control = _control("RED_LOCAL_FILTER_COMMAND_ACCEPTED")
    artifact = tmp_path / "artifact"
    subprocess.run(["git", "init", str(artifact)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(artifact), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(artifact), "config", "user.name", "News Grasp Test"], check=True)
    tool = artifact / "tools" / "generate_pages.py"
    tool.parent.mkdir()
    tool.write_text("print('trusted')\n", encoding="utf-8")
    (artifact / ".gitattributes").write_text("* filter=evil\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(artifact), "add", "tools/generate_pages.py", ".gitattributes"],
        check=True,
    )
    subprocess.run(["git", "-C", str(artifact), "commit", "-m", "fixture"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(artifact), "update-ref", "refs/remotes/origin/main", "HEAD"],
        check=True,
    )
    sentinel = tmp_path / "filter-executed.txt"
    filter_command = (
        "powershell.exe -NoProfile -Command \"[IO.File]::WriteAllText('"
        + str(sentinel).replace("'", "''")
        + "','executed'); $input\""
    )
    subprocess.run(["git", "-C", str(artifact), "config", "filter.evil.clean", filter_command], check=True)
    monkeypatch.setattr(control, "CANONICAL_REPO_ROOT", artifact)

    with pytest.raises(ValueError, match="ARTIFACT_EXECUTABLE_TREE_INVALID"):
        control._validate_artifact_executable_tree(artifact)
    assert not sentinel.exists()


def test_artifact_tree_rejects_filter_from_included_local_config_without_execution(
    monkeypatch, tmp_path: Path
) -> None:
    control = _control("RED_INCLUDED_LOCAL_FILTER_COMMAND_ACCEPTED")
    artifact = tmp_path / "artifact"
    subprocess.run(["git", "init", str(artifact)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(artifact), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(artifact), "config", "user.name", "News Grasp Test"], check=True)
    tool = artifact / "tools" / "generate_pages.py"
    tool.parent.mkdir()
    tool.write_text("print('trusted')\n", encoding="utf-8")
    (artifact / ".gitattributes").write_text("* filter=evil\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(artifact), "add", "tools/generate_pages.py", ".gitattributes"],
        check=True,
    )
    subprocess.run(["git", "-C", str(artifact), "commit", "-m", "fixture"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(artifact), "update-ref", "refs/remotes/origin/main", "HEAD"],
        check=True,
    )
    sentinel = tmp_path / "included-filter-executed.txt"
    included = tmp_path / "included-filter.config"
    filter_command = (
        "powershell.exe -NoProfile -Command \"[IO.File]::WriteAllText('"
        + str(sentinel).replace("'", "''")
        + "','executed'); $input\""
    )
    included.write_text(f"[filter \"evil\"]\n\tclean = {filter_command}\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(artifact), "config", "include.path", str(included)],
        check=True,
    )
    monkeypatch.setattr(control, "CANONICAL_REPO_ROOT", artifact)

    with pytest.raises(ValueError, match="ARTIFACT_EXECUTABLE_TREE_INVALID"):
        control._validate_artifact_executable_tree(artifact)
    assert not sentinel.exists()
    source = Path(control.__file__).read_text(encoding="utf-8")
    filter_query = source[source.index('"config",') : source.index('r"^filter\\..*\\.(clean|smudge|process)$"')]
    assert '"--includes",' in filter_query


def test_execute_runs_one_typed_recovery_then_writes_recovered_terminal(
    monkeypatch, tmp_path: Path
) -> None:
    control = _control("RED_AUDIT_EXECUTOR_MISSING")
    repo = tmp_path / "recovery-repo"
    runner = repo / "scripts" / "ops" / "news-grasp-runner.ps1"
    runner.parent.mkdir(parents=True)
    runner.write_text("# test runner\n", encoding="utf-8")
    canonical_repo = tmp_path / "canonical-repo"
    canonical_runner = canonical_repo / "scripts" / "ops" / "news-grasp-runner.ps1"
    canonical_runner.parent.mkdir(parents=True)
    canonical_runner.write_text("# canonical test runner\n", encoding="utf-8")
    python = canonical_repo / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"test-python")
    authority = tmp_path / "authority.json"
    authority.write_text("{}", encoding="utf-8")
    decisions = iter(
        [
            {
                "action": "scheduled_recovery",
                "terminal": None,
                "scheduledAttemptStatus": "failed",
                "recoveryAttemptStatus": "not_started",
                "recoveryAuthorityReceiptSha256": "a" * 64,
            },
            {
                "action": "none",
                "terminal": "audit_recovered_green",
                "scheduledAttemptStatus": "failed",
                "recoveryAttemptStatus": "succeeded",
            },
        ]
    )
    commands: list[list[str]] = []
    terminals: list[dict[str, object]] = []
    monkeypatch.setattr(control, "decide_audit_recovery", lambda _payload: next(decisions))
    monkeypatch.setattr(control, "CANONICAL_REPO_ROOT", canonical_repo)
    monkeypatch.setattr(control, "CANONICAL_LIVE_BIN_ROOT", canonical_runner.parent)
    monkeypatch.setattr(control, "_resolve_artifact_repo_root", lambda _payload: repo)
    monkeypatch.setattr(control, "_contained_file", lambda *args, **kwargs: authority)
    monkeypatch.setattr(control, "_git_text", lambda *args, **kwargs: "b" * 40)
    monkeypatch.setattr(control, "_file_sha256", lambda _path: "c" * 64)
    monkeypatch.setattr(
        control, "_validate_artifact_executable_tree", lambda _root: "b" * 40
    )
    monkeypatch.setattr(
        control.news_grasp_control_plane,
        "verify_control_plane",
        lambda **_kwargs: {
            "schemaVersion": "NEWS_GRASP_CONTROL_PLANE_PREFLIGHT_V1",
            "ok": True,
            "status": "ready",
            "reasonCode": "CONTROL_PLANE_READY",
        },
    )
    monkeypatch.setattr(
        control,
        "_run_bounded",
        lambda command, **kwargs: (commands.append(command) or 0, b""),
    )
    monkeypatch.setattr(control, "write_audit_terminal", lambda value: terminals.append(value))
    monkeypatch.setattr(
        control,
        "_issue_recovery_execution_receipt",
        lambda **_kwargs: repo / "build" / "recovery-execution.json",
    )
    _admit_test_execution_receipt(monkeypatch, control)

    result = control.execute_audit_recovery(
        {
            "issueDate": "2026-08-02",
            "recoveryAuthorityPath": str(authority),
            "recoveryExecution": {
                "issueDate": "2026-08-02",
                "runIntent": "ScheduledRecoveryFull",
                "maxExternalModelCalls": 9,
                "maxFullE2EAttempts": 0,
                "noFocusTheft": True,
                "noUserMonitoring": True,
                "noAutoOpen": True,
                "recoveryAuthorityReceiptSha256": "a" * 64,
                "artifactRepoHead": "b" * 40,
                "runnerSha256": "c" * 64,
            },
        }
    )

    assert result["terminal"] == "audit_recovered_green"
    assert len(commands) == 1
    assert "ScheduledRecoveryFull" in commands[0]
    assert "-ScheduledAuthorityEvidencePath" in commands[0]
    assert "-AuditAcceptedAtOverride" not in commands[0]
    assert "-PyExeOverride" in commands[0]
    python_arg = Path(commands[0][commands[0].index("-PyExeOverride") + 1]).resolve()
    assert python_arg == (
        control.CANONICAL_REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    ).resolve()
    runner_arg = Path(commands[0][commands[0].index("-File") + 1]).resolve()
    assert runner_arg == (
        control.CANONICAL_REPO_ROOT / "scripts" / "ops" / "news-grasp-runner.ps1"
    ).resolve()
    assert runner_arg != runner.resolve()
    assert terminals == [result]


def test_execute_uses_typed_finalizer_when_public_manifest_is_already_green(
    monkeypatch, tmp_path: Path
) -> None:
    control = _control("POST_GREEN_FULL_RECOVERY_REENTRY")
    artifact = tmp_path / "artifact"
    manifest = artifact / "build" / "publish-complete" / "2026-08-13.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "schemaVersion": "NEWS_GRASP_PUBLISH_COMPLETE_V2",
                "date": "2026-08-13",
                "ok": True,
                "public_status": "green",
                "scheduled_attempt_status": "failed_then_recovered",
                "recovery_attempt_status": "succeeded",
                "source_commit": "a" * 40,
                "artifact_commit": "b" * 40,
                "publish_commit": "c" * 40,
                "publish": {"ok": True, "deploy_head": "c" * 40},
                "distribution_artifacts": {"missing": []},
                "notification": {"ok": True},
                "podcasts": {
                    "primary": {"ok": True},
                    "deepdive": {"ok": True},
                },
                "live_runner_readiness": {
                    "ok": True,
                    "next_run_readiness": {"ok": True},
                },
            }
        ),
        encoding="utf-8",
    )
    canonical = tmp_path / "canonical"
    runner = canonical / "scripts" / "ops" / "news-grasp-runner.ps1"
    runner.parent.mkdir(parents=True)
    runner.write_text("# runner", encoding="utf-8")
    python = canonical / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"python")
    authority = artifact / "build" / "authority.json"
    authority.write_text("{}", encoding="utf-8")
    decisions = iter(
        [
            {
                "action": "scheduled_recovery",
                "terminal": None,
                "scheduledAttemptStatus": "failed",
                "recoveryAttemptStatus": "not_started",
                "recoveryAuthorityReceiptSha256": "a" * 64,
            },
        ]
    )
    commands: list[list[str]] = []
    surface_calls: list[str] = []
    monkeypatch.setattr(control, "CANONICAL_REPO_ROOT", canonical)
    monkeypatch.setattr(control, "CANONICAL_LIVE_BIN_ROOT", runner.parent)
    monkeypatch.setattr(control, "decide_audit_recovery", lambda _payload: next(decisions))
    monkeypatch.setattr(control, "_resolve_artifact_repo_root", lambda _payload: artifact)
    monkeypatch.setattr(control, "_validate_artifact_executable_tree", lambda _root: "b" * 40)
    monkeypatch.setattr(control, "_contained_file", lambda *args, **kwargs: authority)
    monkeypatch.setattr(control, "_file_sha256", lambda _path: "c" * 64)
    monkeypatch.setattr(
        control, "validate_recovery_execution_manifest", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        control.news_grasp_control_plane,
        "verify_control_plane",
        lambda **_kwargs: {
            "ok": False,
            "status": "not_ready",
            "reasonCode": "RUNNER_STATE_ROOT_DRIFT",
        },
    )
    monkeypatch.setattr(
        control,
        "_run_bounded",
        lambda command, **kwargs: (commands.append(command) or 0, b""),
    )
    monkeypatch.setattr(
        control,
        "_verify_post_public_closeout_surface",
        lambda **kwargs: (
            surface_calls.append(str(kwargs["issue_date"]))
            or {"ok": True, "overall_status": "green"}
        ),
    )
    finalization_receipt = manifest.with_name("2026-08-13.finalization-receipt.json")
    finalization_receipt.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        control,
        "_fresh_reverify_and_issue_finalization",
        lambda **_kwargs: finalization_receipt,
    )
    monkeypatch.setattr(control, "write_audit_terminal", lambda _value: None)
    monkeypatch.setattr(
        control,
        "_issue_recovery_execution_receipt",
        lambda **_kwargs: artifact / "build" / "recovery-execution.json",
    )
    _admit_test_execution_receipt(monkeypatch, control)

    result = control.execute_audit_recovery(
        {
            "issueDate": "2026-08-13",
            "recoveryAuthorityPath": str(authority),
            "recoveryExecution": {},
        }
    )

    assert result["terminal"] == "audit_recovered_green"
    assert len(commands) == 1
    assert "-FinalizeVerifiedPublishManifest" in commands[0]
    assert commands[0][commands[0].index("-FinalizeVerifiedPublishManifest") + 1] == str(
        manifest
    )
    assert commands[0][commands[0].index("-RecoveryFinalizationReceiptPath") + 1] == str(
        finalization_receipt
    )
    assert surface_calls == ["2026-08-13"]


def _public_green_exact_args_fixture(monkeypatch, tmp_path: Path):
    """公開Green後のcloseoutだけを通すための実行境界fixture。"""

    control = _control("POST_PUBLIC_GREEN_EXACT_ARGS_REPLAY")
    issue_date = "2026-08-27"
    artifact = tmp_path / "artifact"
    (artifact / "docs").mkdir(parents=True)
    (artifact / "docs" / "index.html").write_text(
        "<!doctype html><title>stable public artifact</title>", encoding="utf-8"
    )
    manifest = artifact / "build" / "publish-complete" / f"{issue_date}.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "schemaVersion": "NEWS_GRASP_PUBLISH_COMPLETE_V2",
                "date": issue_date,
                "ok": True,
                "public_status": "green",
                "scheduled_attempt_status": "failed_then_recovered",
                "recovery_attempt_status": "succeeded",
                "source_commit": "a" * 40,
                "artifact_commit": "b" * 40,
                "publish_commit": "c" * 40,
                "publish": {"ok": True, "deploy_head": "c" * 40},
                "distribution_artifacts": {"missing": []},
                "notification": {"ok": True},
                "podcasts": {
                    "primary": {"ok": True},
                    "deepdive": {"ok": True},
                },
                "live_runner_readiness": {
                    "ok": True,
                    "next_run_readiness": {"ok": True},
                },
            }
        ),
        encoding="utf-8",
    )
    recovery_authority_dir = artifact / "build" / "recovery-authority"
    recovery_authority_dir.mkdir(parents=True)
    execution_receipt = recovery_authority_dir / f"{issue_date}-execution-receipt.json"
    execution_receipt.write_text("{}\n", encoding="utf-8")
    finalization_receipt = manifest.with_name(f"{issue_date}.finalization-receipt.json")
    finalization_receipt.write_text("{}\n", encoding="utf-8")

    canonical = tmp_path / "canonical"
    runner = canonical / "scripts" / "ops" / "news-grasp-runner.ps1"
    runner.parent.mkdir(parents=True)
    runner.write_text("# finalizer fixture\n", encoding="utf-8")
    python = canonical / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"bound-python")
    authority = artifact / "build" / "authority.json"
    authority.write_text("{}\n", encoding="utf-8")

    expected_argv = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(runner),
        "-RunIntent",
        "ScheduledRecoveryFull",
        "-DateStampOverride",
        issue_date,
        "-RepoDirOverride",
        str(artifact),
        "-OpsRepoRootOverride",
        str(canonical),
        "-PyExeOverride",
        str(python),
        "-StateFileOverride",
        str(canonical / "runner-state.json"),
        "-LogDirOverride",
        str(canonical / "news-grasp-logs"),
        "-ResumeFromStage",
        "generation-quality-repair",
        "-FinalizeVerifiedPublishManifest",
        str(manifest),
        "-RecoveryFinalizationReceiptPath",
        str(finalization_receipt),
    ]
    decisions = iter(
        [
            {
                "action": "scheduled_recovery",
                "terminal": None,
                "scheduledAttemptStatus": "failed",
                "recoveryAttemptStatus": "succeeded",
                "recoveryBranch": "ResumeFromStage",
                "resumeStage": "generation-quality-repair",
                "recoveryAuthorityReceiptSha256": "a" * 64,
            },
        ]
    )
    calls: dict[str, list[object]] = {
        "build": [],
        "reseal": [],
        "run": [],
        "operations": [],
        "terminals": [],
    }

    monkeypatch.setattr(control, "CANONICAL_REPO_ROOT", canonical)
    monkeypatch.setattr(control, "CANONICAL_LIVE_BIN_ROOT", runner.parent)
    monkeypatch.setattr(control, "decide_audit_recovery", lambda _payload: next(decisions))
    monkeypatch.setattr(control, "_resolve_artifact_repo_root", lambda _payload: artifact)
    monkeypatch.setattr(control, "_validate_artifact_executable_tree", lambda _root: "b" * 40)
    monkeypatch.setattr(control, "_contained_file", lambda *args, **kwargs: authority)
    monkeypatch.setattr(control, "_file_sha256", lambda _path: "c" * 64)
    monkeypatch.setattr(control, "write_audit_terminal", lambda value: calls["terminals"].append(value))
    monkeypatch.setattr(
        control.news_grasp_recovery_closeout,
        "record_closeout_operation",
        lambda **kwargs: calls["operations"].append(kwargs) or {"status": "allowed"},
    )
    monkeypatch.setattr(
        control,
        "_run_bounded",
        lambda command, **kwargs: (calls["run"].append(command) or 0, b""),
    )
    monkeypatch.setattr(
        control,
        "_verify_post_public_closeout_surface",
        lambda **kwargs: {"ok": True, "overall_status": "green"},
    )
    _admit_test_execution_receipt(monkeypatch, control)

    def unexpected_control_plane(**_kwargs):
        pytest.fail("public Green closeout must not rediscover control-plane state")

    def admitted_freshness(**_kwargs):
        return {
            "ok": True,
            "status": "green",
            "reasonCode": "RECOVERY_DEEPDIVE_RUNTIME_FRESHNESS_GREEN",
        }

    def unexpected_receipt(**_kwargs):
        pytest.fail("public Green closeout must not issue a new execution receipt")

    def unexpected_binding(*_args, **_kwargs):
        pytest.fail("public Green closeout must not resolve high-cost binding")

    monkeypatch.setattr(
        control.news_grasp_control_plane, "verify_control_plane", unexpected_control_plane
    )
    monkeypatch.setattr(
        control.news_grasp_recovery_freshness,
        "verify_recovery_freshness",
        admitted_freshness,
    )
    monkeypatch.setattr(control, "_issue_recovery_execution_receipt", unexpected_receipt)
    monkeypatch.setattr(control, "resolve_live_high_cost_binding", unexpected_binding)
    monkeypatch.setattr(control, "_fresh_reverify_and_issue_finalization", unexpected_receipt)

    payload = {
        "issueDate": issue_date,
        "recoveryAuthorityPath": str(authority),
        "recoveryExecution": {},
    }
    return {
        "control": control,
        "payload": payload,
        "issue_date": issue_date,
        "artifact": artifact,
        "canonical": canonical,
        "runner": runner,
        "manifest": manifest,
        "execution_receipt": execution_receipt,
        "finalization_receipt": finalization_receipt,
        "expected_argv": expected_argv,
        "calls": calls,
    }


def test_public_green_closeout_reseals_known_drift_then_replays_exact_args_once(
    monkeypatch, tmp_path: Path
) -> None:
    """known driftは一括再封印後にreceipt由来argvを一度だけ実行する。"""

    fixture = _public_green_exact_args_fixture(monkeypatch, tmp_path)
    control = fixture["control"]
    closeout = control.news_grasp_recovery_closeout
    calls = fixture["calls"]

    def build(**kwargs):
        calls["build"].append(kwargs)
        if len(calls["build"]) == 1:
            raise closeout.PostPublicCloseoutError(
                "post_public_closeout_blocker:known_receipt_drift"
            )
        return {
            "status": "Green",
            "argv": fixture["expected_argv"],
            "argvSha256": "d" * 64,
        }

    def reseal(**kwargs):
        calls["reseal"].append(kwargs)
        return {"status": "Green", "receiptResealed": True}

    monkeypatch.setattr(closeout, "build_exact_finalizer_command", build)
    monkeypatch.setattr(closeout, "reseal_known_receipt_drift", reseal)

    result = control.execute_audit_recovery(fixture["payload"])

    assert result["terminal"] == "audit_recovered_green"
    assert result["scheduledAttemptStatus"] == "failed"
    assert result["recoveryAttemptStatus"] == "succeeded"
    assert result["postPublicCloseout"]["receiptResealAttempts"] == 1
    assert result["postPublicCloseout"]["publicArtifactUnchanged"] is True
    assert control.same_date_completion_green(
        fixture["issue_date"], result["completionEvidence"]
    )
    assert len(result["completionEvidence"]["receiptSha256"]) == 64
    assert len(calls["build"]) == 2
    assert len(calls["reseal"]) == 1
    assert calls["run"] == [fixture["expected_argv"]]
    assert calls["build"][0]["execution_receipt_path"] == fixture["execution_receipt"]
    assert calls["build"][1]["finalization_receipt_path"] == fixture["finalization_receipt"]
    assert calls["reseal"][0]["execution_receipt_path"] == fixture["execution_receipt"]


def test_public_green_closeout_unknown_drift_is_typed_blocker_without_runner_child(
    monkeypatch, tmp_path: Path
) -> None:
    """unknown driftは一回のresealでpost_public_closeout_blockerに固定する。"""

    fixture = _public_green_exact_args_fixture(monkeypatch, tmp_path)
    control = fixture["control"]
    closeout = control.news_grasp_recovery_closeout
    calls = fixture["calls"]

    def build(**kwargs):
        calls["build"].append(kwargs)
        raise closeout.PostPublicCloseoutError(
            "post_public_closeout_blocker:unknown_receipt_drift"
        )

    def reseal(**kwargs):
        calls["reseal"].append(kwargs)
        raise closeout.PostPublicCloseoutError(
            "post_public_closeout_blocker:unknown_receipt_drift"
        )

    monkeypatch.setattr(closeout, "build_exact_finalizer_command", build)
    monkeypatch.setattr(closeout, "reseal_known_receipt_drift", reseal)

    result = control.execute_audit_recovery(fixture["payload"])

    assert result["terminal"] == "audit_major_incident_open"
    assert result["reasonCode"] == "post_public_closeout_blocker"
    assert result["scheduledAttemptStatus"] == "failed"
    assert result["recoveryAttemptStatus"] == "succeeded"
    assert result["receiptResealAttempts"] == 1
    assert len(calls["build"]) == 1
    assert len(calls["reseal"]) == 1
    assert calls["run"] == []
    assert calls["terminals"] == [result]


def test_execute_repairs_control_plane_once_then_reverifies_before_runner(
    monkeypatch, tmp_path: Path
) -> None:
    control = _control("CONTROL_PLANE_REPAIR_PATH_MISSING")
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    canonical = tmp_path / "canonical"
    runner = canonical / "scripts" / "ops" / "news-grasp-runner.ps1"
    bootstrap = canonical / "scripts" / "ops" / "news-grasp-bootstrap.ps1"
    runner.parent.mkdir(parents=True)
    runner.write_text("# runner", encoding="utf-8")
    bootstrap.write_text("# bootstrap", encoding="utf-8")
    python = canonical / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"python")
    authority = artifact / "build" / "authority.json"
    authority.parent.mkdir()
    authority.write_text("{}", encoding="utf-8")
    decisions = iter(
        [
            {
                "action": "scheduled_recovery",
                "terminal": None,
                "scheduledAttemptStatus": "failed",
                "recoveryAttemptStatus": "not_started",
                "recoveryAuthorityReceiptSha256": "a" * 64,
            },
            {"terminal": "audit_recovered_green"},
        ]
    )
    preflights = iter(
        [
            {"ok": False, "reasonCode": "PRODUCTION_RUNTIME_DRIFT"},
            {"ok": True, "reasonCode": "CONTROL_PLANE_READY"},
        ]
    )
    commands: list[list[str]] = []
    monkeypatch.setattr(control, "CANONICAL_REPO_ROOT", canonical)
    monkeypatch.setattr(control, "CANONICAL_LIVE_BIN_ROOT", runner.parent)
    monkeypatch.setattr(control, "decide_audit_recovery", lambda _payload: next(decisions))
    monkeypatch.setattr(control, "_resolve_artifact_repo_root", lambda _payload: artifact)
    monkeypatch.setattr(control, "_validate_artifact_executable_tree", lambda _root: "b" * 40)
    monkeypatch.setattr(control, "_contained_file", lambda *args, **kwargs: authority)
    monkeypatch.setattr(control, "_file_sha256", lambda _path: "c" * 64)
    monkeypatch.setattr(
        control.news_grasp_control_plane,
        "verify_control_plane",
        lambda **_kwargs: next(preflights),
    )
    monkeypatch.setattr(
        control, "validate_recovery_execution_manifest", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        control,
        "_run_bounded",
        lambda command, **kwargs: (commands.append(command) or 0, b""),
    )
    repair_receipt = artifact / "build" / "control-plane" / "repair.json"
    monkeypatch.setattr(
        control, "_issue_control_plane_repair_receipt", lambda **_kwargs: repair_receipt
    )
    monkeypatch.setattr(control, "write_audit_terminal", lambda _value: None)
    monkeypatch.setattr(
        control,
        "_issue_recovery_execution_receipt",
        lambda **_kwargs: artifact / "build" / "recovery-execution.json",
    )
    _admit_test_execution_receipt(monkeypatch, control)

    result = control.execute_audit_recovery(
        {
            "issueDate": "2026-08-13",
            "recoveryAuthorityPath": str(authority),
            "recoveryExecution": {},
        }
    )

    assert result["terminal"] == "audit_recovered_green"
    assert len(commands) == 2
    assert "-ControlPlaneRepairOnly" in commands[0]
    assert "ScheduledRecoveryFull" in commands[1]


def test_execute_stops_after_one_control_plane_repair_if_drift_remains(
    monkeypatch, tmp_path: Path
) -> None:
    control = _control("CONTROL_PLANE_REPAIR_UNBOUNDED")
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    canonical = tmp_path / "canonical"
    runner = canonical / "scripts" / "ops" / "news-grasp-runner.ps1"
    bootstrap = canonical / "scripts" / "ops" / "news-grasp-bootstrap.ps1"
    runner.parent.mkdir(parents=True)
    runner.write_text("# runner", encoding="utf-8")
    bootstrap.write_text("# bootstrap", encoding="utf-8")
    python = canonical / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"python")
    authority = artifact / "build" / "authority.json"
    authority.parent.mkdir()
    authority.write_text("{}", encoding="utf-8")
    commands: list[list[str]] = []
    terminals: list[dict[str, object]] = []
    monkeypatch.setattr(
        control,
        "decide_audit_recovery",
        lambda _payload: {
            "action": "scheduled_recovery",
            "terminal": None,
            "scheduledAttemptStatus": "failed",
            "recoveryAttemptStatus": "not_started",
            "recoveryAuthorityReceiptSha256": "a" * 64,
        },
    )
    monkeypatch.setattr(control, "CANONICAL_REPO_ROOT", canonical)
    monkeypatch.setattr(control, "CANONICAL_LIVE_BIN_ROOT", runner.parent)
    monkeypatch.setattr(control, "_resolve_artifact_repo_root", lambda _payload: artifact)
    monkeypatch.setattr(control, "_validate_artifact_executable_tree", lambda _root: "b" * 40)
    monkeypatch.setattr(control, "_contained_file", lambda *args, **kwargs: authority)
    monkeypatch.setattr(control, "_file_sha256", lambda _path: "c" * 64)
    monkeypatch.setattr(
        control, "validate_recovery_execution_manifest", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        control.news_grasp_recovery_freshness,
        "verify_recovery_freshness",
        lambda **_kwargs: {"ok": True, "status": "green"},
    )
    monkeypatch.setattr(
        control.news_grasp_control_plane,
        "verify_control_plane",
        lambda **_kwargs: {"ok": False, "reasonCode": "LIVE_BIN_DRIFT"},
    )
    monkeypatch.setattr(
        control,
        "_run_bounded",
        lambda command, **kwargs: (commands.append(command) or 0, b""),
    )
    repair_receipt = artifact / "build" / "control-plane" / "repair.json"
    monkeypatch.setattr(
        control, "_issue_control_plane_repair_receipt", lambda **_kwargs: repair_receipt
    )
    monkeypatch.setattr(control, "write_audit_terminal", lambda value: terminals.append(value))

    result = control.execute_audit_recovery(
        {
            "issueDate": "2026-08-13",
            "recoveryAuthorityPath": str(authority),
            "recoveryExecution": {},
        }
    )

    assert result["terminal"] == "audit_major_incident_open"
    assert result["reasonCode"] == "LIVE_BIN_DRIFT"
    assert len(commands) == 1
    assert "-ControlPlaneRepairOnly" in commands[0]
    assert terminals == [result]


def test_post_green_runtime_drift_never_reenters_broad_repair(
    monkeypatch, tmp_path: Path
) -> None:
    control = _control("POST_GREEN_BROAD_REPAIR_FORBIDDEN")
    artifact = tmp_path / "artifact"
    manifest = artifact / "build" / "publish-complete" / "2026-08-13.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "schemaVersion": "NEWS_GRASP_PUBLISH_COMPLETE_V2",
                "date": "2026-08-13",
                "ok": True,
                "public_status": "green",
                "scheduled_attempt_status": "failed_then_recovered",
                "recovery_attempt_status": "succeeded",
                "source_commit": "a" * 40,
                "artifact_commit": "b" * 40,
                "publish_commit": "c" * 40,
                "publish": {"ok": True, "deploy_head": "c" * 40},
                "distribution_artifacts": {"missing": []},
                "notification": {"ok": True},
                "podcasts": {"primary": {"ok": True}, "deepdive": {"ok": True}},
                "live_runner_readiness": {
                    "ok": True,
                    "next_run_readiness": {"ok": True},
                },
            }
        ),
        encoding="utf-8",
    )
    canonical = tmp_path / "canonical"
    runner = canonical / "scripts" / "ops" / "news-grasp-runner.ps1"
    runner.parent.mkdir(parents=True)
    runner.write_text("# runner", encoding="utf-8")
    python = canonical / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"python")
    commands: list[list[str]] = []
    surface_calls: list[str] = []
    decisions = iter(
        [
            {
                "action": "scheduled_recovery",
                "terminal": None,
                "scheduledAttemptStatus": "failed",
                "recoveryAttemptStatus": "not_started",
                "recoveryAuthorityReceiptSha256": "a" * 64,
            },
        ]
    )
    monkeypatch.setattr(control, "decide_audit_recovery", lambda _payload: next(decisions))
    monkeypatch.setattr(control, "CANONICAL_REPO_ROOT", canonical)
    monkeypatch.setattr(control, "CANONICAL_LIVE_BIN_ROOT", runner.parent)
    monkeypatch.setattr(control, "_resolve_artifact_repo_root", lambda _p: artifact)
    monkeypatch.setattr(control, "_validate_artifact_executable_tree", lambda _r: "b" * 40)
    monkeypatch.setattr(control, "_contained_file", lambda *args, **kwargs: manifest)
    monkeypatch.setattr(control, "_file_sha256", lambda _p: "c" * 64)
    monkeypatch.setattr(control, "validate_recovery_execution_manifest", lambda *a, **k: {})
    monkeypatch.setattr(
        control.news_grasp_control_plane,
        "verify_control_plane",
        lambda **_k: {"ok": False, "reasonCode": "LIVE_BIN_DRIFT"},
    )
    monkeypatch.setattr(
        control, "_run_bounded", lambda command, **_k: (commands.append(command) or 0, b"")
    )
    monkeypatch.setattr(
        control,
        "_verify_post_public_closeout_surface",
        lambda **kwargs: (
            surface_calls.append(str(kwargs["issue_date"]))
            or {"ok": True, "overall_status": "green"}
        ),
    )
    finalization_receipt = manifest.with_name("2026-08-13.finalization-receipt.json")
    finalization_receipt.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        control,
        "_fresh_reverify_and_issue_finalization",
        lambda **_kwargs: finalization_receipt,
    )
    monkeypatch.setattr(
        control,
        "_issue_recovery_execution_receipt",
        lambda **_kwargs: artifact / "build" / "recovery-execution.json",
    )
    monkeypatch.setattr(control, "write_audit_terminal", lambda _v: None)
    _admit_test_execution_receipt(monkeypatch, control)

    result = control.execute_audit_recovery(
        {
            "issueDate": "2026-08-13",
            "recoveryAuthorityPath": str(manifest),
            "recoveryExecution": {},
        }
    )

    assert result["terminal"] == "audit_recovered_green"
    assert len(commands) == 1
    assert "-ControlPlaneRepairOnly" not in commands[0]
    assert "-FinalizeVerifiedPublishManifest" in commands[0]
    assert surface_calls == ["2026-08-13"]


def test_owned_public_green_freshness_red_never_spawns_finalizer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """RC-03: public Green closeoutもfreshness Redならchildを0回で止める。"""

    control = _control("POST_GREEN_FRESHNESS_PRESPAWN")
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    commands: list[list[str]] = []
    terminals: list[dict] = []
    monkeypatch.setattr(control, "_resolve_artifact_repo_root", lambda _p: artifact)
    monkeypatch.setattr(
        control, "_validate_artifact_executable_tree", lambda _r: "a" * 40
    )
    monkeypatch.setattr(
        control.news_grasp_recovery_freshness,
        "verify_recovery_freshness",
        lambda **_kwargs: {
            "ok": False,
            "status": "red",
            "detailCode": "SOURCE_RUNTIME_CRITICAL_SHA_MISMATCH",
        },
    )
    monkeypatch.setattr(
        control,
        "_run_bounded",
        lambda command, **_kwargs: (commands.append(command) or 0, b""),
    )
    monkeypatch.setattr(
        control, "write_audit_terminal", lambda value: terminals.append(value)
    )

    result = control.execute_public_green_closeout_owned(
        "2026-08-13",
        {
            "action": "launch_recovery",
            "artifactRepoRoot": str(artifact),
            "recoveryAttemptStatus": "succeeded",
            "humanImpact": {
                "noFocusTheft": True,
                "noUserMonitoring": True,
                "noAutoOpen": True,
            },
        },
    )

    assert result["reasonCode"] == control.POST_PUBLIC_CLOSEOUT_BLOCKER
    assert "RECOVERY_DEEPDIVE_RUNTIME_FRESHNESS_MISMATCH" in result["closeoutError"]
    assert commands == []
    assert terminals == [result]


def test_public_surface_proof_enforces_complete_bundle_before_post_green(
    tmp_path: Path,
) -> None:
    control = _control("PUBLIC_GREEN_PROOF_BOUNDARY")
    artifact = tmp_path / "artifact"
    proof = (
        artifact
        / "build"
        / "recovery"
        / "proofs"
        / "2026-08-13-public-surface-final.json"
    )
    proof.parent.mkdir(parents=True)
    complete = {
        "schema_version": 1,
        "issue_date": "2026-08-13",
        "overall_status": "green",
        "errors": [],
        "publish_complete_manifest": {
            "ok": True,
            "public_status": "green",
            "scheduled_attempt_status": "failed_then_recovered",
            "recovery_attempt_status": "succeeded",
            "publish": {"ok": True},
            "distribution_artifacts": {"missing": []},
            "notification": {"ok": True},
            "podcasts": {"primary": {"ok": True}, "deepdive": {"ok": True}},
        },
    }
    proof.write_text(json.dumps(complete), encoding="utf-8")

    assert control._has_verified_public_green_proof(
        artifact_root=artifact, issue_date="2026-08-13"
    )
    complete["publish_complete_manifest"]["podcasts"]["deepdive"]["ok"] = False
    proof.write_text(json.dumps(complete), encoding="utf-8")
    assert not control._has_verified_public_green_proof(
        artifact_root=artifact, issue_date="2026-08-13"
    )


@pytest.mark.parametrize(
    "field",
    ["auditAcceptedAt", "productionRuntimeRoot", "liveBinRoot", "recoveryPythonExe"],
)
def test_execute_rejects_caller_override_of_production_identity(field: str) -> None:
    control = _control("PRODUCTION_IDENTITY_OVERRIDE_FORBIDDEN")
    with pytest.raises(ValueError, match="AUDIT_PRODUCTION_IDENTITY_OVERRIDE_FORBIDDEN"):
        control.execute_audit_recovery(
            {"issueDate": "2026-08-13", field: "forged"}
        )


def test_product_constitution_makes_same_day_public_recovery_preemptive() -> None:
    root = Path(__file__).resolve().parents[1]
    for path in (root / "AGENTS.md", root / "CLAUDE.md", root / "docs" / "spec.md"):
        source = path.read_text(encoding="utf-8-sig")
        assert "same_day_public_recovery_first" in source


def test_finalization_reverification_rejects_mismatched_producer_lineage(
    monkeypatch, tmp_path: Path
) -> None:
    control = _control("FINALIZATION_PRODUCER_LINEAGE_NOT_FABRICATED")
    artifact = tmp_path / "artifact"
    ops = tmp_path / "ops"
    live = tmp_path / "live"
    artifact.mkdir()
    ops.mkdir()
    live.mkdir()
    state_path = live / "news-grasp-runner-state.json"
    state_path.write_text(
        json.dumps(
            {
                "date": "2026-08-13",
                "run_intent": "ScheduledRecoveryFull",
                "run_id": "producer-run",
                "repo_dir": str(artifact),
                "artifactRoot": str(artifact),
                "opsRoot": str(live),
                "dailyRootId": "forged",
                "rootOperationId": "forged",
                "producerOperationId": "forged",
                "producerRunIntent": "ScheduledRecoveryFull",
                "lineageReceiptSha256": "f" * 64,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(control, "CANONICAL_REPO_ROOT", ops)
    monkeypatch.setattr(control, "CANONICAL_RUNNER_STATE_PATH", state_path)

    with pytest.raises(ValueError, match="FINALIZATION_PRODUCER_LINEAGE_INVALID"):
        control._fresh_reverify_publish_manifest(
            payload={},
            issue_date="2026-08-13",
            artifact_repo_root=artifact,
            manifest_path=artifact / "build" / "publish-complete" / "2026-08-13.json",
        )
        assert "incident_report_polish" in source
        assert "root_cause_hardening" in source


def test_artifact_executable_tree_accepts_windows_crlf_but_rejects_content_drift(
    monkeypatch, tmp_path: Path
) -> None:
    control = _control("RED_WINDOWS_CRLF_FALSE_DRIFT")
    repo = tmp_path / "artifact-repo"
    repo.mkdir()

    def git(*args: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        return completed.stdout.strip()

    git("init")
    git("config", "user.name", "News-Grasp Contract Test")
    git("config", "user.email", "contract-test@example.invalid")
    tracked = repo / "tools" / "sample.py"
    tracked.parent.mkdir()
    tracked.write_bytes(b"print('stable')\n")
    git("add", "tools/sample.py")
    git("commit", "-m", "fixture")
    head = git("rev-parse", "HEAD")
    git("update-ref", "refs/remotes/origin/main", head)
    monkeypatch.setattr(control, "CANONICAL_REPO_ROOT", repo)

    tracked.write_bytes(b"print('stable')\r\n")
    assert control._validate_artifact_executable_tree(repo) == head

    tracked.write_bytes(b"print('changed')\r\n")
    with pytest.raises(ValueError, match="ARTIFACT_EXECUTABLE_TREE_INVALID"):
        control._validate_artifact_executable_tree(repo)


def test_ng_red_02_typed_completion_exception_is_not_collapsed_to_none(
    monkeypatch, tmp_path: Path
) -> None:
    control = _control("VERIFICATION_EXCEPTION_COLLAPSED_TO_NONE")
    import tools.daily_self_heal as daily_self_heal

    repo = tmp_path / "artifact-repo"
    repo.mkdir()
    ops = tmp_path / "ops"
    ops.mkdir()
    state_path = ops / "news-grasp-runner-state.json"
    state_path.write_text(
        json.dumps(
            {
                "date": "2026-08-02",
                "status": "publish_complete",
                "exit_code": 0,
                "run_intent": "ScheduledProduction",
                "run_id": "run-1",
                "artifactRoot": str(repo),
                "completionAuthorityId": "authority-1",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(control, "CANONICAL_REPO_ROOT", repo)
    monkeypatch.setattr(control, "CANONICAL_RUNNER_STATE_PATH", state_path)
    monkeypatch.setattr(control, "_resolve_artifact_repo_root", lambda _payload: repo)
    monkeypatch.setattr(control, "_validate_artifact_executable_tree", lambda _root: "a" * 40)
    monkeypatch.setattr(control, "_run_bounded", lambda *_args, **_kwargs: (0, b'{"status":"Green"}'))
    monkeypatch.setattr(
        daily_self_heal,
        "verify_publish_complete",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("primary verifier boom")),
    )
    monkeypatch.setattr(
        daily_self_heal,
        "verify_public_completion",
        lambda **_kwargs: {
            "ok": True,
            "date": "2026-08-02",
            "public_status": "green",
            "publicCompletionStatus": "green",
            "publish_commit": "a" * 40,
            "completion_authority_id": "authority-1",
        },
        raising=False,
    )

    try:
        result = control._verify_same_date_completion(
            issue_date="2026-08-02",
            payload={"verificationWaitSec": 0, "verificationPollSec": 1},
            expected_run_intent="ScheduledProduction",
        )
    except Exception as error:  # pragma: no cover - the preimplementation Red path
        pytest.fail(f"VERIFICATION_EXCEPTION_COLLAPSED_TO_NONE: {error}")

    assert isinstance(result, dict), "VERIFICATION_EXCEPTION_COLLAPSED_TO_NONE"
    assert result["verificationStatus"] == "verification_unavailable"
    assert result["publicCompletionStatus"] == "green"
    assert result["reasonCode"] == "PRIMARY_VERIFIER_EXCEPTION"
    assert result["failedGateIds"]


def _ng_red_incident(control, reason: str, authority_id: str = "authority-1") -> dict:
    return control.seal_audit_decision(
        {
            "schemaVersion": "AUDIT_RECOVERY_DECISION_V1",
            "issueDate": "2026-08-02",
            "classification": "incident_required",
            "action": "escalate_major_incident",
            "terminal": "audit_major_incident_open",
            "reasonCode": reason,
            "scheduledAttemptStatus": "failed",
            "recoveryAttemptStatus": "not_started",
            "publicStatus": "incomplete",
            "operationState": "incident_open",
            "workPriority": "same_day_public_recovery_first",
            "allowedBeforePublicGreen": [
                "scheduled_recovery",
                "minimal_recovery_unblocker",
                "escalate_major_incident",
            ],
            "forbiddenBeforePublicGreen": [
                "incident_report_polish",
                "root_cause_hardening",
                "unrelated_cleanup",
            ],
            "owner": "News-Grasp Operations",
            "nextAction": "resume_same_date_recovery_from_verified_stop_point",
            "evidenceSha256": "a" * 64,
            "completionAuthorityId": authority_id,
        }
    )


def test_ng_red_07_audit_monotonic_history_is_not_overwritten(
    monkeypatch, tmp_path: Path
) -> None:
    control = _control("AUDIT_EVENT_HISTORY_OVERWRITTEN")
    incident_root = _terminal_test_root(
        control, monkeypatch, tmp_path, "history-repo"
    )

    first = control.write_audit_terminal(_ng_red_incident(control, "FIRST"))
    second = control.write_audit_terminal(_ng_red_incident(control, "SECOND"))
    events_path = incident_root / "2026-08-02-audit-events.jsonl"

    assert events_path.is_file(), "AUDIT_EVENT_HISTORY_OVERWRITTEN"
    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    assert len(events) == 2, "AUDIT_EVENT_HISTORY_OVERWRITTEN"
    assert [event["sequence"] for event in events] == [1, 2]
    assert events[1]["previousEventHash"] == events[0]["eventHash"]
    assert first["eventId"] != second["eventId"]


def test_ng_red_08_audit_monotonic_history_rejects_replay_and_cross_lineage(
    monkeypatch, tmp_path: Path
) -> None:
    control = _control("AUDIT_EVENT_REPLAY_OR_CROSS_LINEAGE_ACCEPTED")
    incident_root = _terminal_test_root(
        control, monkeypatch, tmp_path, "replay-repo"
    )
    control.write_audit_terminal(_ng_red_incident(control, "FIRST", "authority-1"))
    events_path = incident_root / "2026-08-02-audit-events.jsonl"
    events_path.write_text(
        events_path.read_text(encoding="utf-8")
        + events_path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    try:
        control.validate_audit_observation_history(events_path)
    except ValueError as error:
        assert str(error) in {
            "AUDIT_EVENT_REPLAY",
            "AUDIT_EVENT_CROSS_LINEAGE",
        }
    except AttributeError as error:  # pragma: no cover - preimplementation Red path
        pytest.fail(f"AUDIT_EVENT_REPLAY_OR_CROSS_LINEAGE_ACCEPTED: {error}")
    else:  # pragma: no cover - preimplementation Red path
        pytest.fail("AUDIT_EVENT_REPLAY_OR_CROSS_LINEAGE_ACCEPTED")


def test_ng_red_15_completion_state_vector_preserves_six_independent_states() -> None:
    from tools import news_grasp_operational_contract as operational

    result = operational.finalize_audit_decision(
        {},
        {
            "issueDate": "2026-08-10",
            "scheduledAttemptStatus": "failed",
            "recoveryAttemptStatus": "succeeded",
            "publicStatus": "green",
            "auditObservationStatus": "unverified",
            "nextRunReadinessStatus": "red",
            "operationalStatus": "degraded",
            "terminal": "audit_observation_unverified",
            "action": "none",
            "reasonCode": "RUNNER_READINESS_RED",
        },
    )

    assert result["stateVector"] == {
        "scheduledAttemptStatus": "failed",
        "recoveryAttemptStatus": "succeeded",
        "publicStatus": "green",
        "auditObservationStatus": "unverified",
        "nextRunReadinessStatus": "red",
        "operationalStatus": "degraded",
    }, "COMPLETION_STATE_VECTOR_COLLAPSED"


def _ng_red_green(control, reason: str, authority_id: str = "authority-1") -> dict:
    completion = _green_completion(control, "ScheduledProduction")
    return control.seal_audit_decision(
        {
            "schemaVersion": "AUDIT_RECOVERY_DECISION_V1",
            "issueDate": "2026-08-02",
            "classification": "normal",
            "action": "none",
            "terminal": "audit_normal_green",
            "reasonCode": reason,
            "scheduledAttemptStatus": "succeeded",
            "recoveryAttemptStatus": "not_started",
            "publicStatus": "green",
            "operationState": "normal_green",
            "workPriority": "runner_finalization_only",
            "allowedAfterPublicGreen": control.PUBLIC_GREEN_ALLOWED_OPERATIONS,
            "owner": "News-Grasp Operations",
            "completionAuthorityId": authority_id,
            "completionEvidenceSha256": completion["receiptSha256"],
            "completionEvidence": completion,
        }
    )


def test_ng_red_17_authority_is_immutable_and_history_rejects_49th_event(
    monkeypatch, tmp_path: Path
) -> None:
    control = _control("AUDIT_AUTHORITY_OR_HISTORY_LIFECYCLE_INCOMPLETE")
    authority_root = _terminal_test_root(
        control, monkeypatch, tmp_path, "authority-repo"
    )

    control.write_audit_terminal(_ng_red_green(control, "FIRST_GREEN"))
    authority_path = authority_root / "2026-08-02-completion-authority.json"
    assert authority_path.is_file(), "AUDIT_AUTHORITY_OR_HISTORY_LIFECYCLE_INCOMPLETE"
    authority_bytes = authority_path.read_bytes()

    control.write_audit_terminal(_ng_red_green(control, "SECOND_GREEN"))
    assert authority_path.read_bytes() == authority_bytes, (
        "AUDIT_AUTHORITY_OR_HISTORY_LIFECYCLE_INCOMPLETE"
    )

    history_root = _terminal_test_root(
        control, monkeypatch, tmp_path, "history-limit-repo"
    )
    for index in range(48):
        control.write_audit_terminal(_ng_red_incident(control, f"EVENT-{index}"))
    history_path = history_root / "2026-08-02-audit-events.jsonl"
    before = history_path.read_bytes()
    with pytest.raises(
        ValueError, match="AUDIT_EVENT_HISTORY_LIMIT_EXCEEDED"
    ):
        control.write_audit_terminal(_ng_red_incident(control, "EVENT-49"))
    assert history_path.read_bytes() == before


def test_ng_red_18_unverified_sentinel_allows_first_verified_lineage_only(
    monkeypatch, tmp_path: Path
) -> None:
    control = _control("AUDIT_AUTHORITY_OR_HISTORY_LIFECYCLE_INCOMPLETE")
    root = _terminal_test_root(
        control, monkeypatch, tmp_path, "sentinel-repo"
    )

    control.write_audit_terminal(_ng_red_incident(control, "UNVERIFIED", "unverified"))
    control.write_audit_terminal(_ng_red_green(control, "FIRST_VERIFIED", "authority-1"))
    events_path = root / "2026-08-02-audit-events.jsonl"
    events = control.validate_audit_observation_history(events_path)
    assert [event["completionAuthorityId"] for event in events] == [
        "unverified",
        "authority-1",
    ]

    with pytest.raises(ValueError, match="AUDIT_EVENT_CROSS_LINEAGE"):
        control.write_audit_terminal(_ng_red_green(control, "SECOND_VERIFIED", "authority-2"))


def test_sec_red_authority_id_alone_cannot_preserve_public_green(
    monkeypatch, tmp_path: Path
) -> None:
    control = _control("FORGED_COMPLETION_AUTHORITY_ACCEPTED")
    import tools.daily_self_heal as daily_self_heal

    repo = tmp_path / "repo"
    repo.mkdir()
    state_path = repo / "runner-state.json"
    state_path.write_text(
        json.dumps(
            {
                "date": "2026-08-02",
                "status": "publish_complete",
                "exit_code": 0,
                "run_intent": "ScheduledProduction",
                "run_id": "run-1",
                "artifactRoot": str(repo),
                "completionAuthorityId": "forged-authority",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(control, "CANONICAL_REPO_ROOT", repo)
    monkeypatch.setattr(
        control, "CANONICAL_TERMINAL_ROOT", repo / "build" / "incidents"
    )
    monkeypatch.setattr(control, "CANONICAL_RUNNER_STATE_PATH", state_path)
    monkeypatch.setattr(control, "_resolve_artifact_repo_root", lambda _payload: repo)
    monkeypatch.setattr(control, "_validate_artifact_executable_tree", lambda _root: "a" * 40)
    monkeypatch.setattr(control, "_run_bounded", lambda *_args, **_kwargs: (0, b'{"status":"Green"}'))
    monkeypatch.setattr(
        daily_self_heal,
        "verify_publish_complete",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("primary verifier boom")),
    )
    monkeypatch.setattr(
        daily_self_heal,
        "verify_public_completion",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("public verifier boom")),
        raising=False,
    )

    result = control._verify_same_date_completion(
        issue_date="2026-08-02",
        payload={"verificationWaitSec": 0, "verificationPollSec": 1},
        expected_run_intent="ScheduledProduction",
    )

    assert isinstance(result, dict)
    assert result["publicCompletionStatus"] == "unverified"
    assert result["completionAuthorityId"] == ""


def test_sec_red_completion_authority_rejects_date_and_hash_mismatch(
    monkeypatch, tmp_path: Path
) -> None:
    control = _control("STALE_COMPLETION_AUTHORITY_ACCEPTED")
    repo = tmp_path / "repo"
    root = repo / "build" / "incidents"
    root.mkdir(parents=True)
    monkeypatch.setattr(control, "CANONICAL_REPO_ROOT", repo)
    monkeypatch.setattr(control, "CANONICAL_TERMINAL_ROOT", root)
    completion = _green_completion(control, "ScheduledProduction")
    authority = _seal(
        {
            "schemaVersion": "COMPLETION_AUTHORITY_V1",
            "issuer": control.DECISION_ISSUER,
            "issueDate": "2026-08-01",
            "completionAuthorityId": "authority-1",
            "completionEvidenceSha256": "0" * 64,
            "completionEvidence": completion,
            "firstVerifiedTerminal": "audit_normal_green",
            "decisionReceiptSha256": "1" * 64,
        }
    )
    (root / "2026-08-02-completion-authority.json").write_text(
        json.dumps(authority), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="AUDIT_COMPLETION_AUTHORITY_INVALID"):
        control.load_completion_authority_receipt("2026-08-02")


def test_sec_red_audit_terminal_lock_is_exclusive(monkeypatch, tmp_path: Path) -> None:
    control = _control("AUDIT_TERMINAL_LOCK_NOT_EXCLUSIVE")
    repo = tmp_path / "repo"
    repo.mkdir()
    root = repo / "build" / "incidents"
    monkeypatch.setattr(control, "CANONICAL_REPO_ROOT", repo)
    monkeypatch.setattr(control, "CANONICAL_TERMINAL_ROOT", root)
    entered = threading.Event()
    release = threading.Event()

    def hold_lock() -> None:
        with control._locked_directory(control._validated_terminal_root()):
            entered.set()
            release.wait(timeout=5)

    thread = threading.Thread(target=hold_lock)
    thread.start()
    assert entered.wait(timeout=5)
    try:
        with pytest.raises(ValueError, match="AUDIT_TERMINAL_BUSY"):
            with control._locked_directory(control._validated_terminal_root()):
                pass
    finally:
        release.set()
        thread.join(timeout=5)
    assert not thread.is_alive()


def test_sec_red_audit_terminal_wal_recovers_split_write(
    monkeypatch, tmp_path: Path
) -> None:
    control = _control("AUDIT_TERMINAL_SPLIT_WRITE_NOT_RECOVERED")
    repo = tmp_path / "repo"
    repo.mkdir()
    root = repo / "build" / "incidents"
    monkeypatch.setattr(control, "CANONICAL_REPO_ROOT", repo)
    monkeypatch.setattr(control, "CANONICAL_TERMINAL_ROOT", root)
    real_atomic = control._atomic_write_bytes
    call_count = 0

    def fail_projection(path: Path, payload: bytes) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 3:
            raise OSError("simulated projection crash")
        real_atomic(path, payload)

    monkeypatch.setattr(control, "_atomic_write_bytes", fail_projection)
    with pytest.raises(OSError, match="simulated projection crash"):
        control.write_audit_terminal(_ng_red_incident(control, "FIRST"))
    monkeypatch.setattr(control, "_atomic_write_bytes", real_atomic)

    second = control.write_audit_terminal(_ng_red_incident(control, "SECOND"))
    events = control.validate_audit_observation_history(
        root / "2026-08-02-audit-events.jsonl"
    )
    assert [event["sequence"] for event in events] == [1, 2]
    assert second["eventHash"] == events[-1]["eventHash"]
    assert not (root / ".2026-08-02-audit-terminal-transaction.json").exists()


def test_sec_red_audit_terminal_rejects_output_root_escape(
    monkeypatch, tmp_path: Path
) -> None:
    control = _control("AUDIT_TERMINAL_OUTPUT_ROOT_ESCAPE")
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside"
    monkeypatch.setattr(control, "CANONICAL_REPO_ROOT", repo)
    monkeypatch.setattr(control, "CANONICAL_TERMINAL_ROOT", outside)

    with pytest.raises(ValueError, match="AUDIT_TERMINAL_OUTPUT_INVALID"):
        control.write_audit_terminal(_ng_red_incident(control, "ESCAPE"))
    assert not outside.exists() or not list(outside.glob("*.json*"))


def test_sec_red_audit_history_is_bounded_before_decode(
    monkeypatch, tmp_path: Path
) -> None:
    control = _control("AUDIT_HISTORY_UNBOUNDED_READ")
    repo = tmp_path / "repo"
    root = repo / "build" / "incidents"
    root.mkdir(parents=True)
    monkeypatch.setattr(control, "CANONICAL_REPO_ROOT", repo)
    monkeypatch.setattr(control, "CANONICAL_TERMINAL_ROOT", root)
    history = root / "2026-08-02-audit-events.jsonl"
    history.write_bytes(b"x" * (control.MAX_AUDIT_HISTORY_BYTES + 1))

    with pytest.raises(ValueError, match="AUDIT_EVENT_HISTORY_LIMIT_EXCEEDED"):
        control.validate_audit_observation_history(history)


def test_sec_red_single_audit_event_is_bounded_before_decode(tmp_path: Path) -> None:
    control = _control("AUDIT_EVENT_UNBOUNDED_READ")
    history = tmp_path / "2026-08-02-audit-events.jsonl"
    history.write_bytes(b"x" * (control.MAX_AUDIT_EVENT_BYTES + 1) + b"\n")

    with pytest.raises(ValueError, match="AUDIT_EVENT_HISTORY_LIMIT_EXCEEDED"):
        control.validate_audit_observation_history(history)


def test_sec_green_valid_immutable_authority_preserves_public_green(
    monkeypatch, tmp_path: Path
) -> None:
    control = _control("VALID_COMPLETION_AUTHORITY_NOT_REUSED")
    import tools.daily_self_heal as daily_self_heal

    repo = tmp_path / "repo"
    repo.mkdir()
    root = repo / "build" / "incidents"
    monkeypatch.setattr(control, "CANONICAL_REPO_ROOT", repo)
    monkeypatch.setattr(control, "CANONICAL_TERMINAL_ROOT", root)
    control.write_audit_terminal(_ng_red_green(control, "VERIFIED_GREEN"))

    state_path = repo / "runner-state.json"
    state_path.write_text(
        json.dumps(
            {
                "date": "2026-08-02",
                "status": "publish_complete",
                "exit_code": 0,
                "run_intent": "ScheduledProduction",
                "run_id": "run-1",
                "artifactRoot": str(repo),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(control, "CANONICAL_RUNNER_STATE_PATH", state_path)
    monkeypatch.setattr(control, "_resolve_artifact_repo_root", lambda _payload: repo)
    monkeypatch.setattr(control, "_validate_artifact_executable_tree", lambda _root: "a" * 40)
    monkeypatch.setattr(control, "_run_bounded", lambda *_args, **_kwargs: (0, b'{"status":"Green"}'))
    monkeypatch.setattr(
        daily_self_heal,
        "verify_publish_complete",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("primary verifier boom")),
    )
    monkeypatch.setattr(
        daily_self_heal,
        "verify_public_completion",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("public verifier boom")),
        raising=False,
    )

    result = control._verify_same_date_completion(
        issue_date="2026-08-02",
        payload={"verificationWaitSec": 0, "verificationPollSec": 1},
        expected_run_intent="ScheduledProduction",
    )

    assert isinstance(result, dict)
    assert result["publicCompletionStatus"] == "green"
    assert result["completionAuthorityId"] == "authority-1"


def test_sec_red_audit_terminal_rejects_reparse_component(
    monkeypatch, tmp_path: Path
) -> None:
    control = _control("AUDIT_TERMINAL_REPARSE_COMPONENT_ACCEPTED")
    repo = tmp_path / "repo"
    build = repo / "build"
    build.mkdir(parents=True)
    root = build / "incidents"
    original = control._is_reparse_or_symlink
    monkeypatch.setattr(control, "CANONICAL_REPO_ROOT", repo)
    monkeypatch.setattr(control, "CANONICAL_TERMINAL_ROOT", root)
    monkeypatch.setattr(
        control,
        "_is_reparse_or_symlink",
        lambda path: path == build or original(path),
    )

    with pytest.raises(ValueError, match="AUDIT_TERMINAL_OUTPUT_INVALID"):
        control.write_audit_terminal(_ng_red_incident(control, "REPARSE"))


def test_sec_red_self_sealed_authority_without_event_chain_is_rejected(
    monkeypatch, tmp_path: Path
) -> None:
    control = _control("UNANCHORED_COMPLETION_AUTHORITY_ACCEPTED")
    repo = tmp_path / "repo"
    root = repo / "build" / "incidents"
    root.mkdir(parents=True)
    monkeypatch.setattr(control, "CANONICAL_REPO_ROOT", repo)
    monkeypatch.setattr(control, "CANONICAL_TERMINAL_ROOT", root)
    completion = _green_completion(control, "ScheduledProduction")
    authority = _seal(
        {
            "schemaVersion": "COMPLETION_AUTHORITY_V1",
            "issuer": control.DECISION_ISSUER,
            "issueDate": "2026-08-02",
            "completionAuthorityId": "forged-authority",
            "completionEvidenceSha256": completion["receiptSha256"],
            "completionEvidence": completion,
            "firstVerifiedTerminal": "audit_normal_green",
            "decisionReceiptSha256": "1" * 64,
        }
    )
    (root / "2026-08-02-completion-authority.json").write_text(
        json.dumps(authority), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="AUDIT_COMPLETION_AUTHORITY_INVALID"):
        control.load_completion_authority_receipt("2026-08-02")


def test_sec_red_completion_producer_emits_all_causal_hashes() -> None:
    control = _control("COMPLETION_CAUSAL_HASHES_MISSING")
    hashes = control._typed_completion_hashes(
        issue_date="2026-08-02",
        payload={"verificationWaitSec": 0, "verificationPollSec": 1},
        evidence={"public": {"ok": True}},
    )

    for field in (
        "sourceSha256",
        "runtimeSha256",
        "configSha256",
        "authoritySha256",
        "externalEvidenceSha256",
    ):
        assert control._valid_sha256(hashes.get(field)), field


def test_sec_red_stale_wal_cannot_roll_back_newer_history(
    monkeypatch, tmp_path: Path
) -> None:
    control = _control("STALE_AUDIT_WAL_ROLLBACK")
    root = _terminal_test_root(control, monkeypatch, tmp_path, "wal-stale-repo")
    first = control.write_audit_terminal(_ng_red_incident(control, "FIRST"))
    history_path = root / "2026-08-02-audit-events.jsonl"
    first_history = history_path.read_bytes()
    control.write_audit_terminal(_ng_red_incident(control, "SECOND"))
    stale = control._audit_transaction_receipt(
        issue_date="2026-08-02",
        history_payload=first_history,
        terminal=first,
    )
    control._atomic_write_bytes(
        control._audit_transaction_path(root, "2026-08-02"),
        control._json_document_bytes(stale),
    )

    control.write_audit_terminal(_ng_red_incident(control, "THIRD"))
    events = control.validate_audit_observation_history(history_path)
    assert [event["sequence"] for event in events] == [1, 2, 3]
    assert [event["result"] for event in events] == [
        "audit_major_incident_open",
        "audit_major_incident_open",
        "audit_major_incident_open",
    ]


def test_sec_red_authority_must_match_committed_green_event(
    monkeypatch, tmp_path: Path
) -> None:
    control = _control("AUTHORITY_EVENT_CHAIN_MISMATCH_ACCEPTED")
    root = _terminal_test_root(control, monkeypatch, tmp_path, "authority-chain-repo")
    control.write_audit_terminal(_ng_red_green(control, "GREEN"))
    path = root / "2026-08-02-completion-authority.json"
    authority = json.loads(path.read_text(encoding="utf-8"))
    authority["decisionReceiptSha256"] = "f" * 64
    authority.pop("receiptSha256")
    authority = _seal(authority)
    path.write_text(json.dumps(authority), encoding="utf-8")

    with pytest.raises(ValueError, match="AUDIT_COMPLETION_AUTHORITY_INVALID"):
        control.load_completion_authority_receipt("2026-08-02")


def test_sec_red_divergent_wal_is_rejected_without_history_mutation(
    monkeypatch, tmp_path: Path
) -> None:
    control = _control("DIVERGENT_AUDIT_WAL_ACCEPTED")
    live_root = _terminal_test_root(control, monkeypatch, tmp_path, "wal-live-repo")
    control.write_audit_terminal(_ng_red_incident(control, "LIVE"))
    live_history_path = live_root / "2026-08-02-audit-events.jsonl"
    live_before = live_history_path.read_bytes()

    other_root = _terminal_test_root(control, monkeypatch, tmp_path, "wal-other-repo")
    other_terminal = control.write_audit_terminal(
        _ng_red_incident(control, "OTHER")
    )
    other_history = (
        other_root / "2026-08-02-audit-events.jsonl"
    ).read_bytes()
    divergent = control._audit_transaction_receipt(
        issue_date="2026-08-02",
        history_payload=other_history,
        terminal=other_terminal,
    )

    live_repo = live_root.parents[1]
    monkeypatch.setattr(control, "CANONICAL_REPO_ROOT", live_repo)
    monkeypatch.setattr(control, "CANONICAL_TERMINAL_ROOT", live_root)
    control._atomic_write_bytes(
        control._audit_transaction_path(live_root, "2026-08-02"),
        control._json_document_bytes(divergent),
    )

    with pytest.raises(ValueError, match="AUDIT_TERMINAL_TRANSACTION_INVALID"):
        control.write_audit_terminal(_ng_red_incident(control, "NEXT"))
    assert live_history_path.read_bytes() == live_before


@pytest.mark.skipif(os.name != "nt", reason="Windows directory pin contract")
def test_sec_red_windows_directory_pin_blocks_root_swap(
    monkeypatch, tmp_path: Path
) -> None:
    control = _control("AUDIT_TERMINAL_ROOT_SWAP_ACCEPTED")
    root = _terminal_test_root(control, monkeypatch, tmp_path, "pin-repo")
    root.mkdir(parents=True)
    moved = root.with_name("incidents-moved")

    with control._pinned_directory(
        root, invalid_code="AUDIT_TERMINAL_OUTPUT_INVALID"
    ):
        with pytest.raises(OSError):
            root.rename(moved)
    assert root.is_dir()
    assert not moved.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows ancestor pin contract")
def test_sec_red_windows_directory_pin_blocks_ancestor_swap(
    monkeypatch, tmp_path: Path
) -> None:
    control = _control("AUDIT_TERMINAL_ANCESTOR_SWAP_ACCEPTED")
    root = _terminal_test_root(control, monkeypatch, tmp_path, "ancestor-pin-repo")
    root.mkdir(parents=True)
    repo = root.parents[1]
    build = repo / "build"
    moved = repo / "build-moved"

    with control._pinned_directory(
        root,
        anchor=repo,
        invalid_code="AUDIT_TERMINAL_OUTPUT_INVALID",
    ):
        with pytest.raises(OSError):
            build.rename(moved)
    assert build.is_dir()
    assert not moved.exists()


def test_sec_red_audit_lock_excludes_independent_process(
    monkeypatch, tmp_path: Path
) -> None:
    control = _control("AUDIT_TERMINAL_PROCESS_LOCK_NOT_EXCLUSIVE")
    repo = tmp_path / "repo"
    repo.mkdir()
    root = repo / "build" / "incidents"
    ready = repo / "child-ready"
    monkeypatch.setattr(control, "CANONICAL_REPO_ROOT", repo)
    monkeypatch.setattr(control, "CANONICAL_TERMINAL_ROOT", root)
    script = """
import sys
from pathlib import Path
from tools import audit_recovery_control as control
repo = Path(sys.argv[1])
ready = Path(sys.argv[2])
control.CANONICAL_REPO_ROOT = repo
control.CANONICAL_TERMINAL_ROOT = repo / 'build' / 'incidents'
with control._locked_directory(control._validated_terminal_root()):
    ready.write_text('ready', encoding='utf-8')
    sys.stdin.readline()
"""
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(repo), str(ready)],
        cwd=Path(control.__file__).resolve().parents[1],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        creationflags=creationflags,
    )
    try:
        for _ in range(100):
            if ready.exists() or process.poll() is not None:
                break
            time.sleep(0.05)
        assert ready.exists(), process.stderr.read() if process.poll() is not None else ""
        with pytest.raises(ValueError, match="AUDIT_TERMINAL_BUSY"):
            with control._locked_directory(control._validated_terminal_root()):
                pass
    finally:
        if process.stdin is not None:
            process.stdin.write("\n")
            process.stdin.flush()
        process.wait(timeout=5)
    assert process.returncode == 0


def test_sec_audit_broker_requires_exact_bound_python_and_trust_anchor() -> None:
    """audit brokerは.venv/PATH fallbackではなくbindingのpython hash/anchorだけを使う。"""

    control = _control("RECOVERY_PYTHON_IDENTITY_INVALID")
    executable_source = inspect.getsource(control._canonical_python_executable)
    broker_source = inspect.getsource(control._inspect_attempt_via_broker)
    assert "pythonExeSha256" in executable_source or "pythonExeSha256" in broker_source
    assert "pythonTrustAnchor" in executable_source or "pythonTrustAnchor" in broker_source
    assert "return \"python\"" not in executable_source
    assert "Path.home() / \"AppData\"" not in executable_source
    assert "RECOVERY_PYTHON_IDENTITY_INVALID" in executable_source or "RECOVERY_PYTHON_IDENTITY_INVALID" in broker_source


def test_canonical_python_executable_delegates_to_live_runtime_resolver(
    monkeypatch, tmp_path: Path
) -> None:
    """監査brokerは独自fallbackでなくlive binding resolverの結果だけを使う。"""

    control = _control("RECOVERY_PYTHON_IDENTITY_INVALID")
    from tools import e2e_final_admission_bridge as bridge

    bound_python = tmp_path / "live-bin" / "python.exe"
    bound_python.parent.mkdir(parents=True)
    bound_python.write_bytes(b"bound-python")
    calls: list[dict[str, object]] = []

    def fake_resolver(**kwargs: object) -> Path:
        calls.append(kwargs)
        return bound_python.resolve()

    monkeypatch.setattr(bridge, "resolve_production_runtime_python", fake_resolver)
    resolved = control._canonical_python_executable()

    assert Path(resolved).resolve() == bound_python.resolve()
    assert calls


def _public_green_closeout_decision(issue_date: str) -> dict[str, object]:
    """public Green済み・closeout未完了のcanonical decision fixture。"""

    return {
        "schemaVersion": "AUDIT_RECOVERY_DECISION_V2",
        "issueDate": issue_date,
        "action": "launch_recovery",
        "publicStatus": "green",
        "publicCompletionStatus": "green",
        "closeoutStatus": "incomplete",
        "closeoutComplete": False,
        "scheduledAttemptStatus": "failed",
        "recoveryAttemptStatus": "succeeded",
        "recoveryBranch": "ResumeFromStage",
        "resumeStage": "generation-quality-repair",
        "receiptSha256": "a" * 64,
    }


def _public_green_terminal(issue_date: str) -> dict[str, object]:
    return {
        "schemaVersion": "AUDIT_RECOVERY_DECISION_V2",
        "issueDate": issue_date,
        "action": "none",
        "terminal": "audit_recovered_green",
        "reasonCode": "POST_PUBLIC_GREEN_CLOSEOUT_COMPLETE",
        "scheduledAttemptStatus": "failed",
        "recoveryAttemptStatus": "succeeded",
        "publicStatus": "green",
        "exitCode": 0,
    }


def _install_default_public_green_owner_seams(
    monkeypatch, control, decision: dict[str, object], calls: list[tuple[str, dict[str, object]]]
) -> None:
    """default ensure ownerが実daily ownerのcloseout境界へ入るfixture。"""

    from tools import news_grasp_daily_control

    monkeypatch.setattr(
        news_grasp_daily_control,
        "prepare_recovery",
        lambda **_: dict(decision),
    )
    monkeypatch.setattr(
        news_grasp_daily_control,
        "_has_verified_public_green_for_closeout",
        lambda **_: True,
    )

    def fake_closeout(issue_date: str, value: dict[str, object]) -> dict[str, object]:
        calls.append((issue_date, dict(value)))
        return _public_green_terminal(issue_date)

    monkeypatch.setattr(control, "execute_public_green_closeout_owned", fake_closeout)


def test_ensure_audit_0640_default_owner_delegates_public_green_closeout_once(
    monkeypatch, tmp_path: Path
) -> None:
    """canonical ensureのdefault ownerはpublic Green closeoutを一度だけ委譲する。"""

    control = _control("PUBLIC_GREEN_CLOSEOUT_DEFAULT_OWNER_MISSING")
    issue_date = "2026-08-27"
    decision = _public_green_closeout_decision(issue_date)
    calls: list[tuple[str, dict[str, object]]] = []
    _install_default_public_green_owner_seams(monkeypatch, control, decision, calls)

    result = control.ensure_audit_0640(
        issue_date=issue_date,
        trigger="automation_0640",
        transaction_root=tmp_path,
    )

    from tools import news_grasp_daily_control

    expected_artifact_root = news_grasp_daily_control._resolve_public_green_artifact_root(
        issue_date=issue_date,
        backend=news_grasp_daily_control.ProductionBackend(),
    )
    assert len(calls) == 1
    assert calls[0][0] == issue_date
    assert decision.get("artifactRepoRoot") is None
    assert calls[0][1] == {
        **decision,
        "artifactRepoRoot": str(expected_artifact_root),
    }
    assert result["transactionStatus"] == "terminal"
    assert result["terminal"] == "audit_recovered_green"
    assert result["scheduledAttemptStatus"] == "failed"
    assert result["recoveryAttemptStatus"] == "succeeded"


def test_direct_cli_execute_remains_canonical_ensure_only(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    """direct CLI executeはowner/closeoutを起動せずcanonical ensureへの遷移だけを返す。"""

    control = _control("AUDIT_RECOVERY_DIRECT_EXECUTE_REQUIRES_CANONICAL_ENSURE")
    input_path = tmp_path / "direct-execute.json"
    input_path.write_text(
        json.dumps({"issueDate": "2026-08-27", "trigger": "direct_cli"}),
        encoding="utf-8",
    )
    terminals: list[dict[str, object]] = []
    calls: list[str] = []
    monkeypatch.setattr(
        control,
        "write_audit_terminal",
        lambda value: terminals.append(dict(value)) or value,
    )
    monkeypatch.setattr(
        control,
        "ensure_audit_0640",
        lambda **_: calls.append("ensure") or pytest.fail("direct CLI invoked owner"),
    )
    monkeypatch.setattr(
        control,
        "execute_public_green_closeout_owned",
        lambda *_args, **_kwargs: pytest.fail("direct CLI invoked closeout"),
    )

    monkeypatch.setattr(
        sys,
        "argv",
        ["audit_recovery_control.py", "execute", "--input", str(input_path)],
    )
    process_exit_code = control.main()

    assert process_exit_code == 2
    assert calls == []
    assert terminals == []
    output = json.loads(capsys.readouterr().out)
    assert output["reasonCode"] == (
        "AUDIT_RECOVERY_DIRECT_EXECUTE_REQUIRES_CANONICAL_ENSURE"
    )
    assert output["nextAction"] == "invoke_ensure_0640"


def test_terminal_projection_replay_does_not_reexecute_public_green_closeout(
    monkeypatch, tmp_path: Path
) -> None:
    """同一transactionのterminal projection/replayではcloseout consumerを再実行しない。"""

    control = _control("PUBLIC_GREEN_CLOSEOUT_TERMINAL_REPLAY")
    issue_date = "2026-08-27"
    decision = _public_green_closeout_decision(issue_date)
    calls: list[tuple[str, dict[str, object]]] = []
    _install_default_public_green_owner_seams(monkeypatch, control, decision, calls)

    first = control.ensure_audit_0640(
        issue_date=issue_date,
        trigger="automation_0640",
        transaction_root=tmp_path,
    )
    second = control.ensure_audit_0640(
        issue_date=issue_date,
        trigger="automation_0640",
        transaction_root=tmp_path,
    )

    assert first["transactionStatus"] == "terminal"
    assert second["transactionStatus"] == "terminal_projection"
    assert second["terminal"] == "audit_recovered_green"
    assert second["reasonCode"] == "POST_PUBLIC_GREEN_CLOSEOUT_COMPLETE"
    from tools import news_grasp_daily_control

    expected_artifact_root = news_grasp_daily_control._resolve_public_green_artifact_root(
        issue_date=issue_date,
        backend=news_grasp_daily_control.ProductionBackend(),
    )
    assert len(calls) == 1
    assert calls[0][0] == issue_date
    assert decision.get("artifactRepoRoot") is None
    assert calls[0][1] == {
        **decision,
        "artifactRepoRoot": str(expected_artifact_root),
    }
