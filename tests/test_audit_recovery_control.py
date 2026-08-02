from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path

import pytest


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


def _green_completion(control, run_intent: str) -> dict[str, object]:
    return _seal(
        {
            "schemaVersion": "SAME_DATE_COMPLETION_EVIDENCE_V1",
            "issuer": control.VERIFIED_COMPLETION_ISSUER,
            "issueDate": "2026-08-02",
            "publishStatusIssueDate": "2026-08-02",
            "runIntent": run_intent,
            "runId": "test-run",
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
    incident_root = tmp_path / "incidents"
    monkeypatch.setattr(control, "CANONICAL_TERMINAL_ROOT", incident_root)
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
        }
    )
    terminal = control.write_audit_terminal(decision)
    assert terminal["decisionReceiptSha256"] == decision["receiptSha256"]
    assert (incident_root / "2026-08-02-audit-terminal.json").is_file()
    parser_source = Path(control.__file__).read_text(encoding="utf-8-sig")
    assert "--terminal-root" not in parser_source
    assert "--terminal-output" not in parser_source


def test_actual_completion_verifier_owns_all_required_gates() -> None:
    control = _control("RED_ACTUAL_COMPLETION_VERIFIER_MISSING")
    source = Path(control.__file__).read_text(encoding="utf-8-sig")
    assert '"tools.validate_daily_quality"' in source
    assert '"--require-deepdive"' in source
    assert "verify_publish_complete(" in source
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
        assert "--terminal-output <terminal.json>" not in source
        assert "build/incidents/<issue-date>-audit-terminal.json" in source
        assert "durable ledger" in source
        assert "validate_daily_quality --require-deepdive" in source
        assert "inspect-news-grasp-attempt" in source
        assert "scheduledFailureReceiptPath" in source
        assert "scheduledAttempt.status" not in source
        assert "recoveryAttempt.status" not in source
        assert "runnerStatePath" not in source
