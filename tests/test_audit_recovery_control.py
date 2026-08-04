from __future__ import annotations

import hashlib
import importlib
import json
import os
import subprocess
import sys
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


def test_green_public_surface_releases_root_cause_work_only_after_recovery(
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
    assert decision["workPriority"] == "root_cause_after_public_green"


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
    assert '"verify-publish-complete"' in source
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


def test_bounded_subprocess_owns_windows_descendants() -> None:
    control = _control("RED_PROCESS_TREE_OWNERSHIP_MISSING")
    source = Path(control.__file__).read_text(encoding="utf-8-sig")
    assert "JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE" in source
    assert "AssignProcessToJobObject" in source
    assert "TerminateJobObject" in source
    assert "CREATE_SUSPENDED" in source
    assert "NtResumeProcess" in source


def test_windows_cleanup_without_job_terminates_process_without_posix_killpg(
    monkeypatch,
) -> None:
    control = _control("RED_WINDOWS_JOB_CREATE_FAILURE_LEAK")

    class FakeProcess:
        pid = 424242

        def __init__(self) -> None:
            self.returncode = None
            self.terminated = False

        def poll(self):
            return self.returncode

        def terminate(self) -> None:
            self.terminated = True
            self.returncode = 1

        def kill(self) -> None:
            self.returncode = 1

        def wait(self, timeout=None):
            return self.returncode

    process = FakeProcess()
    monkeypatch.setattr(control.os, "name", "nt")
    monkeypatch.setattr(
        control.os,
        "killpg",
        lambda *_args: pytest.fail("Windows cleanup must not call os.killpg"),
        raising=False,
    )

    control._terminate_owned_process_tree(process, None)
    assert process.terminated is True


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
    assert "$replacementBackup" in installer


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
    monkeypatch.setattr(control, "_resolve_artifact_repo_root", lambda _payload: repo)
    monkeypatch.setattr(control, "_contained_file", lambda *args, **kwargs: authority)
    monkeypatch.setattr(control, "_git_text", lambda *args, **kwargs: "b" * 40)
    monkeypatch.setattr(control, "_file_sha256", lambda _path: "c" * 64)
    monkeypatch.setattr(
        control, "_validate_artifact_executable_tree", lambda _root: "b" * 40
    )
    monkeypatch.setattr(
        control,
        "_run_bounded",
        lambda command, **kwargs: (commands.append(command) or 0, b""),
    )
    monkeypatch.setattr(control, "write_audit_terminal", lambda value: terminals.append(value))

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


def test_product_constitution_makes_same_day_public_recovery_preemptive() -> None:
    root = Path(__file__).resolve().parents[1]
    for path in (root / "AGENTS.md", root / "CLAUDE.md", root / "docs" / "spec.md"):
        source = path.read_text(encoding="utf-8-sig")
        assert "same_day_public_recovery_first" in source
        assert "incident_report_polish" in source
        assert "root_cause_hardening" in source
