from __future__ import annotations

import inspect
import hashlib
import json
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest


REPO = Path(__file__).resolve().parents[1]


def _control():
    try:
        from tools import news_grasp_daily_control
    except ImportError as error:
        pytest.fail(f"PRODUCTION_FAILURE_CONTROLLER_MISSING: {error}")
    return news_grasp_daily_control


def test_production_backend_resolves_legacy_failure_receipt_by_ledger_hash(
    tmp_path: Path,
) -> None:
    """runtime cutover後も旧repoの当日receiptを台帳hashで限定再利用する。"""
    control = _control()
    current = tmp_path / "current"
    legacy = tmp_path / "legacy"
    receipt = control._sealed(
        {
            "schemaVersion": "SCHEDULED_FAILURE_RECEIPT_V1",
            "productId": "News-Grasp",
            "issueDate": "2026-08-09",
            "scheduledAttemptStatus": "failed",
        }
    )
    path = legacy / "build" / "scheduled-failure-receipts" / "2026-08-09-bootstrap-run.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(receipt), encoding="utf-8")

    backend = control.ProductionBackend(repo_root=current, evidence_root=legacy)
    resolved = backend.resolve_failure_receipt(
        "2026-08-09", str(receipt["receiptSha256"])
    )

    assert resolved == path.resolve()


def _audit_plan(*, action: str, branch: str | None = None) -> dict[str, object]:
    value: dict[str, object] = {
        "issueDate": "2026-08-06",
        "action": action,
        "scheduledAttemptStatus": "failed",
        "recoveryAttemptStatus": "not_started",
        "decisionPath": "C:/fixed/decision.json",
        "receiptSha256": "1" * 64,
    }
    if branch is not None:
        value.update(
            {
                "recoveryBranch": branch,
                "scheduledAuthorityEvidencePath": "C:/fixed/authority.json",
            }
        )
    return value


def _same_date_completion(control, *, run_intent: str = "ScheduledProduction") -> dict[str, object]:
    audit = control.audit_recovery_control
    issue_date = "2026-08-06"
    run_id = "run-2026-08-06"
    return audit._sealed(
        {
            "schemaVersion": "SAME_DATE_COMPLETION_EVIDENCE_V1",
            "issuer": audit.VERIFIED_COMPLETION_ISSUER,
            "issueDate": issue_date,
            "publishStatusIssueDate": issue_date,
            "runIntent": run_intent,
            "runId": run_id,
            **audit._completion_lineage(
                issue_date=issue_date,
                run_intent=run_intent,
                run_id=run_id,
            ),
            "checks": {field: True for field in audit.COMPLETION_FIELDS},
            "evidenceSha256": {
                field: hashlib.sha256(field.encode("utf-8")).hexdigest()
                for field in audit.COMPLETION_FIELDS
            },
        }
    )


def test_audit_normal_green_preserves_full_same_date_completion_evidence(monkeypatch) -> None:
    control = _control()
    completion = _same_date_completion(control)
    plan = _audit_plan(action="none")
    plan.update(
        {
            "completion": True,
            "completionEvidenceSha256": completion["receiptSha256"],
            "completionEvidence": completion,
        }
    )
    monkeypatch.setattr(control, "prepare_recovery", lambda **_: plan)
    result = control.execute_audit_0640(
        issue_date="2026-08-06",
        terminal_writer=lambda value: value,
    )
    assert result["terminal"] == "audit_normal_green"
    assert result["completionEvidence"] == completion
    assert result["completionEvidence"]["checks"] == {
        field: True for field in control.audit_recovery_control.COMPLETION_FIELDS
    }


def test_existing_recovery_green_preserves_recovery_terminal_status(monkeypatch) -> None:
    """既存の復旧成功を通常scheduled成功へ上書きしない。"""
    control = _control()
    completion = _same_date_completion(control, run_intent="ScheduledRecoveryFull")
    plan = _audit_plan(action="none")
    plan.update(
        {
            "recoveryAttemptStatus": "succeeded",
            "completion": True,
            "completionEvidenceSha256": completion["receiptSha256"],
            "completionEvidence": completion,
        }
    )
    monkeypatch.setattr(control, "prepare_recovery", lambda **_: plan)

    result = control.execute_audit_0640(
        issue_date="2026-08-06",
        terminal_writer=lambda value: value,
    )

    assert result["terminal"] == "audit_recovered_green"
    assert result["scheduledAttemptStatus"] == "failed"
    assert result["recoveryAttemptStatus"] == "succeeded"
    assert result["reasonCode"] == "SAME_DATE_RECOVERY_COMPLETION_GREEN"
    assert result["sourceDecision"]["recoveryAttemptStatus"] == "succeeded"


def test_audit_terminal_writer_rejects_green_completion_hash_stub(monkeypatch, tmp_path) -> None:
    control = _control()
    audit = control.audit_recovery_control
    monkeypatch.setattr(audit, "CANONICAL_TERMINAL_ROOT", tmp_path)
    stub = {"receiptSha256": "2" * 64}
    terminal = control._audit_green_terminal(
        issue_date="2026-08-06",
        decision=_audit_plan(action="none"),
        completion=stub,
        recovered=False,
    )
    with pytest.raises(ValueError, match="AUDIT_TERMINAL_INVALID"):
        audit.write_audit_terminal(terminal)


def test_audit_minimal_unblocker_must_reverify_and_write_recovered_terminal(monkeypatch) -> None:
    control = _control()
    monkeypatch.setattr(control, "prepare_recovery", lambda **_: _audit_plan(action="launch_minimal_unblocker", branch="minimal_unblocker"))
    monkeypatch.setattr(control.audit_recovery_control, "same_date_completion_green", lambda *_: True)
    terminals: list[dict[str, object]] = []
    result = control.execute_audit_0640(
        issue_date="2026-08-06",
        minimal_executor=lambda _: {"completion": False},
        completion_verifier=lambda *_: {"receiptSha256": "2" * 64},
        terminal_writer=lambda value: terminals.append(value) or value,
    )
    assert result["terminal"] == "audit_recovered_green"
    assert result["completionEvidenceSha256"] == "2" * 64
    assert result["sourceDecision"]["recoveryBranch"] == "minimal_unblocker"
    assert result["completionEvidence"]["receiptSha256"] == "2" * 64
    assert terminals == [result]


def test_audit_resume_branch_is_executed_before_same_date_green(monkeypatch) -> None:
    control = _control()
    plan = _audit_plan(action="launch_recovery", branch="ResumeFromStage")
    plan.update({"resumeStage": "deepdive", "sourceAdmissionPath": "C:/fixed/admission.json"})
    monkeypatch.setattr(control, "prepare_recovery", lambda **_: plan)
    monkeypatch.setattr(control.audit_recovery_control, "same_date_completion_green", lambda *_: True)
    commands: list[list[str]] = []
    result = control.execute_audit_0640(
        issue_date="2026-08-06",
        backend=SimpleNamespace(
            repo_root=REPO,
            runner_path=REPO / "scripts/ops/news-grasp-runner.ps1",
            resolve_high_cost_binding=lambda: {
                "bindingPath": "C:/fixed/news-grasp-high-cost-binding-v1.json",
                "bindingReceiptSha256": "a" * 64,
            },
        ),
        command_runner=lambda command, **_: commands.append(command) or 0,
        completion_verifier=lambda *_: {"receiptSha256": "3" * 64},
        terminal_writer=lambda value: value,
    )
    assert result["terminal"] == "audit_recovered_green"
    assert commands and "-ResumeFromStage" in commands[0]
    assert commands[0][commands[0].index("-ResumeFromStage") + 1] == "deepdive"


def test_audit_recovery_exit_zero_with_incomplete_public_surface_is_major_incident(monkeypatch) -> None:
    control = _control()
    monkeypatch.setattr(control, "prepare_recovery", lambda **_: _audit_plan(action="launch_recovery", branch="ScheduledRecoveryFull"))
    monkeypatch.setattr(control.audit_recovery_control, "same_date_completion_green", lambda *_: False)
    result = control.execute_audit_0640(
        issue_date="2026-08-06",
        backend=SimpleNamespace(
            repo_root=REPO,
            runner_path=REPO / "scripts/ops/news-grasp-runner.ps1",
            resolve_high_cost_binding=lambda: {
                "bindingPath": "C:/fixed/news-grasp-high-cost-binding-v1.json",
                "bindingReceiptSha256": "a" * 64,
            },
        ),
        command_runner=lambda *_args, **_kwargs: 0,
        completion_verifier=lambda *_: {"receiptSha256": "4" * 64},
        terminal_writer=lambda value: value,
    )
    assert result["terminal"] == "audit_major_incident_open"
    assert result["owner"] == "News-Grasp Operations"
    assert result["nextAction"] == "resume_same_date_recovery_from_verified_stop_point"


def test_audit_controller_failure_writes_owned_major_incident(monkeypatch) -> None:
    control = _control()
    monkeypatch.setattr(
        control,
        "prepare_recovery",
        lambda **_: (_ for _ in ()).throw(ValueError("primary evidence invalid")),
    )
    terminals: list[dict[str, object]] = []
    result = control.execute_audit_0640(
        issue_date="2026-08-06",
        terminal_writer=lambda value: terminals.append(value) or value,
    )
    assert result["terminal"] == "audit_major_incident_open"
    assert result["owner"] == "News-Grasp Operations"
    assert result["nextAction"] == "resume_same_date_recovery_from_verified_stop_point"
    assert terminals == [result]


def test_deadman_delegates_to_single_canonical_audit_executor() -> None:
    source = (REPO / "scripts/ops/news-grasp-deadman.ps1").read_text(encoding="utf-8-sig")
    assert "'execute-audit-0640'" in source
    assert "launch_minimal_unblocker" not in source
    assert "Start -RecoveryDecisionPath" not in source
    registry = json.loads(
        (REPO / "config/news_grasp_daily_control_routes.json").read_text(
            encoding="utf-8"
        )
    )
    route = next(
        row for row in registry["routes"] if row["routeId"] == "audit_0640_control"
    )
    assert route["consumerSymbol"] == "execute_audit_0640"
    assert route["productionCallSymbol"] == "execute-audit-0640"


def test_failure_classification_is_derived_from_observed_state_only() -> None:
    control = _control()
    signature = inspect.signature(control.classify_observed_failure)
    assert "payload" not in signature.parameters
    assert "classification" not in signature.parameters
    assert (
        control.classify_observed_failure(
            runner_state={
                "status": "operation_rejected_high_cost_admission",
                "exit_code": 76,
            },
            process_exit_code=76,
            log_text="ERROR: HIGH_COST_OPERATION_ADMISSION_REJECTED exit=1",
        )
        == "recoverable"
    )
    assert (
        control.classify_observed_failure(
            runner_state={
                "status": "blocked_external_readiness",
                "external_readiness": {"kind": "oauth_consent_required"},
            },
            process_exit_code=71,
            log_text="oauth consent required",
        )
        == "incident_required"
    )


def test_codex_auth_block_is_typed_external_deferred_without_recovery_reentry() -> None:
    """Codex認証未準備は再修復へ戻さず、外部依存のdeferredへ分岐する。"""
    control = _control()
    assert (
        control.classify_observed_failure(
            runner_state={
                "status": "blocked_codex_auth",
            },
            process_exit_code=72,
            log_text="codex auth readiness failed before repair:generation-quality",
        )
        == "external_control_plane_unavailable"
    )


def test_recovery_plan_is_bounded_and_preserves_scheduled_failure() -> None:
    control = _control()
    plan = control.build_recovery_plan(
        issue_date="2026-08-05",
        trigger="production_failure",
        classification="recoverable",
        branch="ScheduledRecoveryFull",
        authority_path=Path("C:/evidence/recovery-authority.json"),
        failure_receipt_sha256="a" * 64,
        operational_truth_sha256="b" * 64,
    )
    assert plan["action"] == "launch_recovery"
    assert plan["runIntent"] == "ScheduledRecoveryFull"
    assert plan["maxAutomaticRecoveryAttempts"] == 1
    assert plan["scheduledAttemptStatus"] == "failed"
    assert plan["recoveryAttemptStatus"] == "not_started"
    assert plan["scheduledFailureRetained"] is True
    assert plan["noFocusTheft"] is True
    assert plan["noAutoOpen"] is True
    assert plan["noUserMonitoring"] is True


def test_second_recovery_attempt_becomes_major_incident_not_loop() -> None:
    control = _control()
    plan = control.build_recovery_plan(
        issue_date="2026-08-05",
        trigger="production_failure",
        classification="recoverable",
        branch="ScheduledRecoveryFull",
        authority_path=Path("C:/evidence/recovery-authority.json"),
        failure_receipt_sha256="a" * 64,
        operational_truth_sha256="b" * 64,
        recovery_attempt_number=1,
    )
    assert plan["action"] == "major_incident_continuation"
    assert plan["terminal"] == "production_major_incident_open"
    assert plan["reasonCode"] == "BOUNDED_RECOVERY_ATTEMPT_EXHAUSTED"
    assert plan["completion"] is False


def test_watcher_executes_controller_after_failure_and_rewatches_once() -> None:
    source = (REPO / "scripts" / "ops" / "watch-news-grasp-runner.ps1").read_text(
        encoding="utf-8-sig"
    )
    assert "tools.news_grasp_daily_control" in source, "PRODUCTION_FAILURE_CONTROLLER_MISSING"
    assert "production_failure" in source
    assert "maxAutomaticRecoveryAttempts" in source
    assert "Start-RecoveryFromDecision" in source
    assert source.count("Start-RecoveryFromDecision") == 3


def test_runner_serializes_daily_log_append_and_hash_at_one_boundary() -> None:
    runner = (REPO / "scripts" / "ops" / "news-grasp-runner.ps1").read_text(
        encoding="utf-8-sig"
    )
    assert "function Invoke-WithRunnerLogLock" in runner
    assert "function Add-RunnerLogLine" in runner
    assert "function Get-RunnerLogSha256" in runner
    assert "function New-ScheduledFailureTerminalInput" in runner
    terminalizer = runner[runner.index("function Invoke-ScheduledFailureTerminalizer") :]
    assert "NEWS_GRASP_SCHEDULED_FAILURE_TERMINAL_INPUT_V1" in terminalizer
    assert "--run-id" in terminalizer
    assert "stateEvidenceSha256" in terminalizer
    assert "logEvidenceSha256" in terminalizer
    assert "Get-RunnerLogSha256" not in terminalizer
    assert "Get-FileSha256Hex -Path $StateFile" not in terminalizer
    assert "Add-Content -Path $LogPath" not in runner
    assert "Add-Content -LiteralPath $LogPath" not in runner


def test_bootstrap_startup_failure_terminalizer_passes_run_id() -> None:
    bootstrap = (REPO / "scripts" / "ops" / "news-grasp-bootstrap.ps1").read_text(
        encoding="utf-8-sig"
    )
    terminalizer = bootstrap[
        bootstrap.index("function Record-StartupFailureForAudit") :
        bootstrap.index("$SourceRepoDir = Resolve-NewsGraspRepoDir")
    ]
    assert "record-news-grasp-failure" in terminalizer
    assert "'--run-id' $runId" in terminalizer


def test_deadman_calls_same_controller_for_public_incomplete() -> None:
    source = (REPO / "scripts" / "ops" / "news-grasp-deadman.ps1").read_text(
        encoding="utf-8-sig"
    )
    assert "tools.news_grasp_daily_control" in source, "AUDIT_0640_CONTROLLER_MISSING"
    assert "execute-audit-0640" in source
    assert "Invoke-RecoverOnlyIfStaleDeadPid" not in source
    assert "audit canonical executor" in source


def test_resume_branch_consumes_recovery_authority_not_failed_production_admission() -> None:
    runner = (REPO / "scripts" / "ops" / "news-grasp-runner.ps1").read_text(
        encoding="utf-8-sig"
    )
    watcher = (REPO / "scripts" / "ops" / "watch-news-grasp-runner.ps1").read_text(
        encoding="utf-8-sig"
    )
    assert "[string] $RecoveryDecisionPath" in runner
    assert "validate-decision" in runner
    assert "RECOVERY_DECISION_BRANCH_MISMATCH" in runner
    assert "RecoveryDecisionPath" in watcher
    resume_block = runner[
        runner.index("if ($ResumeFromStage)") : runner.index(
            "if ($HighCostAdmissionPath)", runner.index("if ($ResumeFromStage)")
        )
    ]
    assert "admit-news-grasp-recovery-continuation" not in resume_block
    assert "ScheduledAuthorityEvidencePath" in resume_block
    assert "start-news-grasp-recovery-stage" in runner
    assert "consume-news-grasp-recovery-stage-decision" not in runner
    assert runner.index("start-news-grasp-recovery-stage") > runner.index(
        "} elseif ($ResumeFromPostDailyQuality -or $ResumeAfterDeepDive -or $ResumeGenerationQualityRepair) {"
    )
    assert "sourceAdmissionReceipt" not in resume_block


def test_resume_plan_without_broker_ledger_decision_falls_back_to_full_recovery() -> None:
    control = _control()
    plan = control.build_recovery_plan(
        issue_date="2026-08-05",
        trigger="production_failure",
        classification="recoverable",
        branch="ResumeFromStage",
        authority_path=Path("C:/evidence/recovery-authority.json"),
        failure_receipt_sha256="a" * 64,
        operational_truth_sha256="b" * 64,
        resume_stage="deepdive",
        source_admission_path="C:/evidence/source-production-admission.json",
        source_admission_sha256="c" * 64,
    )
    assert plan["recoveryBranch"] == "ScheduledRecoveryFull"
    assert plan["resumeStage"] is None


def test_resume_plan_binds_broker_ledger_decision() -> None:
    control = _control()
    plan = control.build_recovery_plan(
        issue_date="2026-08-05",
        trigger="production_failure",
        classification="recoverable",
        branch="ResumeFromStage",
        authority_path=Path("C:/evidence/recovery-authority.json"),
        failure_receipt_sha256="a" * 64,
        operational_truth_sha256="b" * 64,
        resume_stage="deepdive",
        source_admission_path="C:/evidence/source-production-admission.json",
        source_admission_sha256="c" * 64,
        broker_stage_decision_path="C:/evidence/stage-decision.json",
        broker_stage_decision_sha256="d" * 64,
        broker_stage_decision_receipt_sha256="e" * 64,
    )
    assert plan["recoveryBranch"] == "ResumeFromStage"
    assert plan["brokerStageDecisionPath"] == "C:/evidence/stage-decision.json"
    assert plan["brokerStageDecisionSha256"] == "d" * 64
    assert plan["brokerStageDecisionReceiptSha256"] == "e" * 64


def test_audit_decision_does_not_accept_caller_repair_classification() -> None:
    from tools import audit_recovery_control

    source = Path(audit_recovery_control.__file__).read_text(encoding="utf-8-sig")
    decision_source = source[source.index("def decide_audit_recovery") :]
    assert 'payload.get("repairDecision")' not in decision_source, (
        "CALLER_REPAIR_CLASSIFICATION_ACCEPTED"
    )
    assert "classify_observed_failure" in decision_source


def test_minimal_unblocker_requires_sealed_public_proof(monkeypatch, tmp_path: Path) -> None:
    from tools import audit_recovery_control

    repo = tmp_path / "repo"
    state_path = tmp_path / "ops" / "state.json"
    state_path.parent.mkdir(parents=True)
    monkeypatch.setattr(audit_recovery_control, "CANONICAL_REPO_ROOT", repo)
    monkeypatch.setattr(audit_recovery_control, "CANONICAL_RUNNER_STATE_PATH", state_path)
    date = "2026-08-05"
    for relative in (
        f"digest/Summary/{date}.md",
        f"digest/DeepDive/{date}-DeepDive.md",
        f"docs/{date}/index.html",
        f"data/distribution/{date}.json",
    ):
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("local-only", encoding="utf-8")
    state_path.write_text(
        json.dumps(
            {
                "date": date,
                "status": "publish_complete",
                "phase": "publish_complete",
                "run_id": "test-run",
                "run_intent": "ScheduledProduction",
            }
        ),
        encoding="utf-8",
    )
    witness = {
        "receiptSha256": "c" * 64,
        "scheduledAttemptStatus": "failed",
    }
    monkeypatch.setattr(
        audit_recovery_control,
        "_verify_public_without_notification",
        lambda **_: None,
        raising=False,
    )
    truth = audit_recovery_control._observe_operational_truth(
        issue_date=date, attempt_witness=witness
    )
    assert "minimalUnblockerReceiptSha256" not in truth, (
        "MINIMAL_UNBLOCKER_PUBLIC_PROOF_MISSING"
    )


def test_operational_truth_separates_admission_file_hash_from_ledger_receipt(
    monkeypatch, tmp_path: Path
) -> None:
    from tools import audit_recovery_control

    issue_date = "2026-08-05"
    repo = tmp_path / "repo"
    state_path = tmp_path / "ops" / "state.json"
    admission_path = repo / "build" / "high-cost-operation-admissions" / issue_date / "source.json"
    artifact_path = repo / "digest" / "Summary" / f"{issue_date}.md"
    admission_path.parent.mkdir(parents=True)
    artifact_path.parent.mkdir(parents=True)
    state_path.parent.mkdir(parents=True)
    artifact_path.write_text("partial artifact", encoding="utf-8")
    admission_body = {
        "schemaVersion": "HIGH_COST_SCHEDULED_OPERATION_ADMISSION_V1",
        "issueDate": issue_date,
        "operationKind": "scheduled_production",
    }
    admission_receipt = hashlib.sha256(
        json.dumps(
            admission_body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    admission = {**admission_body, "receiptSha256": admission_receipt}
    admission_path.write_text(json.dumps(admission), encoding="utf-8")
    admission_file_sha = hashlib.sha256(admission_path.read_bytes()).hexdigest()
    state_path.write_text(
        json.dumps(
            {
                "date": issue_date,
                "status": "failed_generation_quality",
                "phase": "generation-quality-repair",
                "run_id": "a" * 32,
                "run_intent": "ScheduledProduction",
                "resumeStage": "generation-quality-repair",
                "highCostAdmissionPath": str(admission_path),
                "highCostAdmissionSha256": admission_file_sha,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(audit_recovery_control, "CANONICAL_REPO_ROOT", repo)
    monkeypatch.setattr(audit_recovery_control, "CANONICAL_RUNNER_STATE_PATH", state_path)
    truth = audit_recovery_control._observe_operational_truth(
        issue_date=issue_date,
        attempt_witness={
            "receiptSha256": "b" * 64,
            "scheduledAttemptStatus": "failed",
        },
    )
    assert truth["sourceAdmissionSha256"] == admission_receipt
    assert truth["sourceAdmissionFileSha256"] == admission_file_sha
    assert truth["resumeStage"] == "generation-quality-repair"


def test_registry_requires_production_call_edge(tmp_path: Path) -> None:
    from tools.news_grasp_operational_contract import validate_operational_registry

    consumer = tmp_path / "consumer.py"
    caller = tmp_path / "caller.py"
    consumer.write_text("def claimed_consumer():\n    return True\n", encoding="utf-8")
    caller.write_text("# claimed_consumer is intentionally not called\n", encoding="utf-8")
    registry = {
        "schemaVersion": "NEWS_GRASP_DAILY_CONTROL_REGISTRY_V1",
        **{
            field: "x"
            for field in (
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
        },
        "declaredRouteIds": ["route"],
        "consumerRouteIds": ["route"],
        "positiveFixtureRouteIds": ["route"],
        "negativeFixtureRouteIds": ["route"],
        "routes": [
            {
                "routeId": "route",
                "consumerPath": "consumer.py",
                "consumerSymbol": "claimed_consumer",
                "productionCallerPath": "caller.py",
                "productionCallSymbol": "claimed_consumer",
            }
        ],
    }
    result = validate_operational_registry(registry, repo_root=tmp_path)
    assert result["status"] == "Red", "DEAD_CONSUMER_ACCEPTED"
    assert result["reason"] == "NEWS_GRASP_ROUTE_PRODUCTION_EDGE_MISSING"


def test_production_registry_excludes_root_fix_promotion_routes() -> None:
    registry = json.loads(
        (REPO / "config" / "news_grasp_daily_control_routes.json").read_text(
            encoding="utf-8"
        )
    )
    expected_route_ids = [
        "scheduled_runner",
        "producer_lineage",
        "production_self_heal",
        "audit_0640_control",
        "audit_observer",
        "audit_decision",
        "completion_verifier",
    ]
    assert registry["declaredRouteIds"] == expected_route_ids
    assert registry["consumerRouteIds"] == expected_route_ids
    assert registry["positiveFixtureRouteIds"] == expected_route_ids
    assert registry["negativeFixtureRouteIds"] == expected_route_ids
    assert registry["contractTest"] == "tests/test_news_grasp_daily_control.py"
    assert [route["routeId"] for route in registry["routes"]] == expected_route_ids
    assert all(
        not route["consumerPath"].startswith("tools/root_fix_")
        for route in registry["routes"]
    )


def test_installer_rollback_restores_absent_files_and_task_definitions() -> None:
    source = (REPO / "scripts" / "ops" / "install-news-grasp-ops.ps1").read_text(
        encoding="utf-8-sig"
    )
    assert "Export-ScheduledTask" in source
    assert "existed_before" in source
    assert "enabled_before" in source
    assert "Register-ScheduledTask -TaskName $taskName -Xml $xml -Force" in source
    assert "Unregister-ScheduledTask -TaskName $taskName -Confirm:$false" in source
    assert "Remove-NewsGraspVerifiedFile" in source
    assert "$missionAuthorityBackup" in source
    assert "file = 'audit-mission-authority-v1.json'" in source
    assert "destination = $missionAuthorityPath" in source
    assert "Recover-NewsGraspInterruptedInstall" in source
    assert source.index("$script:InstallationCommitted = $false") < source.index(
        ". (Join-Path $PSScriptRoot 'install-news-grasp-ops-guard.ps1')"
    ), "INSTALL_ROLLBACK_LATCH_INITIALIZED_AFTER_GUARD"
    prepared = source.index("Write-NewsGraspInstallJournal -Phase 'prepared'")
    bin_dir_create = source.index("New-Item -ItemType Directory -Force -Path $BinDir")
    first_live_copy = source.index("Write-NewsGraspAtomicFile", prepared)
    assert prepared < bin_dir_create, "INSTALL_JOURNAL_WRITTEN_AFTER_BINDIR_MUTATION"
    assert prepared < first_live_copy, "INSTALL_JOURNAL_WRITTEN_AFTER_LIVE_MUTATION"
    assert "bin_dir_existed_before = $binDirExistedBefore" in source
    assert "DestinationBoundary ([string]$Journal.bin_dir)" in source
    assert "Assert-NewsGraspInstalledState" in source
    verified = source.rindex("Assert-NewsGraspInstalledState")
    committed = source.rindex("$script:InstallationCommitted = $true")
    assert verified < committed, "INSTALL_COMMITTED_BEFORE_RELOAD_VERIFICATION"
    assert "Enable-ScheduledTask -TaskName $RunnerTaskName" in source
    assert "Enable-ScheduledTask -TaskName $BootstrapTaskName" in source
    assert "if (-not $runnerWasEnabled)" not in source
    assert "if (-not $bootstrapWasEnabled)" not in source
    assert "Register-ScheduledTask -TaskName $DeadmanTaskName" in source
    assert "schtasks.exe /Query /TN $DeadmanTaskName" not in source
    assert "if ($actions.Count -ne 1 -or $triggers.Count -ne 1)" in source
    assert "[string]$action.WorkingDirectory" in source
    assert "[bool]$task.Settings.StartWhenAvailable" in source
    assert "[string]$task.Settings.MultipleInstances -ne 'IgnoreNew'" in source
    assert "[string]$trigger.Repetition.Duration" in source
    assert "[bool]$trigger.Repetition.StopAtDurationEnd" in source
    assert "$deadmanRepetition = New-CimInstance" in source
    assert "-ClassName 'MSFT_TaskRepetitionPattern'" in source
    assert "$deadmanTrigger.Repetition = $deadmanRepetition" in source
    assert "$deadmanTrigger.Repetition.Interval =" not in source
    assert "$deadmanTrigger.Repetition.Duration =" not in source
    assert "trap {" in source
    assert "Invoke-NewsGraspInstallRollback" in source


def test_ng_red_01_green_valueerror_green_preserves_public_authority(
    monkeypatch, tmp_path: Path
) -> None:
    control = _control()
    import tools.audit_recovery_control as audit

    state = {
        "date": "2026-08-02",
        "status": "publish_complete",
        "exit_code": 0,
        "run_id": "run-1",
        "run_intent": "ScheduledProduction",
        "completionAuthorityId": "authority-1",
    }
    witness = {
        "scheduledAttemptStatus": "reserved",
        "recoveryAttemptStatus": "not_started",
    }
    backend = SimpleNamespace(
        repo_root=tmp_path,
        load_state=lambda _date: state,
        log_text=lambda _date: "",
        inspect_attempt=lambda _date: witness,
    )
    monkeypatch.setattr(
        audit,
        "_verify_same_date_completion",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("primary verifier boom")),
    )
    monkeypatch.setattr(
        audit,
        "load_completion_authority_receipt",
        lambda _issue_date: {"completionAuthorityId": "authority-1"},
    )

    try:
        result = control.prepare_recovery(
            issue_date="2026-08-02",
            trigger="audit_0640",
            process_exit_code=2,
            backend=backend,
        )
    except Exception as error:  # pragma: no cover - the preimplementation Red path
        pytest.fail(f"PUBLIC_GREEN_REGRESSED_ON_VERIFIER_EXCEPTION: {error}")

    assert result["action"] == "audit_observation_unverified"
    assert result["publicStatus"] == "green"
    assert result["recoveryStarted"] is False
    assert result["terminal"] == "audit_observation_unverified"
    assert result["exitCode"] == 2


def test_ng_red_03_green_valueerror_green_cli_returns_typed_exit_two(monkeypatch, capsys) -> None:
    control = _control()
    monkeypatch.setattr(
        control,
        "execute_audit_0640",
        lambda **_kwargs: {
            "terminal": "audit_observation_unverified",
            "publicStatus": "green",
            "reasonCode": "PRIMARY_VERIFIER_EXCEPTION",
        },
    )

    result = control.main(["execute-audit-0640", "--issue-date", "2026-08-02"])
    capsys.readouterr()
    assert result == 2, "UNVERIFIED_OBSERVATION_NOT_TYPED_EXIT_2"


def test_ng_red_05_audit_monotonic_readiness_red_never_starts_public_recovery() -> None:
    control = _control()
    try:
        result = control.select_audit_recovery_action(
            {
                "verificationStatus": "verified_incomplete",
                "publicCompletionStatus": "green",
                "nextRunReadinessStatus": "red",
                "reasonCode": "RUNNER_READINESS_RED",
                "completionAuthorityId": "authority-1",
            }
        )
    except AttributeError as error:  # pragma: no cover - preimplementation Red path
        pytest.fail(f"READINESS_RED_STARTED_PUBLIC_RECOVERY: {error}")

    assert result["action"] == "readiness_repair"
    assert result["recoveryScope"] == "next_run_readiness"
    assert result["publicStatus"] == "green"
    assert result["publicRecoveryStarted"] is False


def test_ng_red_14_readiness_repair_executes_canonical_installer_once(
    monkeypatch,
) -> None:
    control = _control()
    completion = _same_date_completion(control)
    plan = _audit_plan(action="readiness_repair")
    plan.update(
        {
            "publicStatus": "green",
            "nextRunReadinessStatus": "red",
            "completionAuthorityId": "authority-1",
            "reasonCode": "RUNNER_READINESS_RED",
        }
    )
    monkeypatch.setattr(control, "prepare_recovery", lambda **_: plan)
    commands: list[list[str]] = []
    verifications: list[tuple[str, str]] = []

    def run_command(command: list[str], **_kwargs: object) -> int:
        commands.append(command)
        return 0

    def verify(issue_date: str, run_intent: str) -> dict[str, object]:
        verifications.append((issue_date, run_intent))
        return completion

    result = control.execute_audit_0640(
        issue_date="2026-08-06",
        backend=SimpleNamespace(repo_root=REPO),
        command_runner=run_command,
        completion_verifier=verify,
        terminal_writer=lambda value: value,
    )

    assert len(commands) == 1, "READINESS_REPAIR_NOT_EXECUTED_BY_RUNTIME"
    command = commands[0]
    assert command[0].casefold() == "powershell.exe"
    assert "-NonInteractive" in command
    assert "-File" in command
    assert any(
        value.endswith("scripts\\ops\\install-news-grasp-ops.ps1")
        or value.endswith("scripts/ops/install-news-grasp-ops.ps1")
        for value in command
    ), "READINESS_REPAIR_NOT_EXECUTED_BY_RUNTIME"
    assert verifications == [("2026-08-06", "ScheduledProduction")]
    assert result["terminal"] == "audit_normal_green"
    assert result["publicStatus"] == "green"
    assert result["recoveryStarted"] is False


def test_sec_red_causal_retry_compare_and_consume_is_single_writer(
    monkeypatch, tmp_path: Path
) -> None:
    control = _control()
    current = {
        "sourceSha256": "a" * 64,
        "runtimeSha256": "b" * 64,
        "configSha256": "c" * 64,
        "authoritySha256": "d" * 64,
        "externalEvidenceSha256": "e" * 64,
    }
    barrier = threading.Barrier(2)
    monkeypatch.setattr(control, "_atomic_json", lambda *_args, **_kwargs: barrier.wait(timeout=5))
    results: list[dict[str, object]] = []
    errors: list[Exception] = []

    def run() -> None:
        try:
            results.append(
                control._admit_causal_retry(
                    repo_root=tmp_path,
                    issue_date="2026-08-02",
                    runner_state={},
                    completion=current,
                )
            )
        except Exception as error:  # pragma: no cover - diagnostic capture
            errors.append(error)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert not errors
    assert all(not thread.is_alive() for thread in threads)
    assert sum(result["allowed"] is True for result in results) == 1


def test_sec_red_causal_retry_rejects_state_path_escape(
    monkeypatch, tmp_path: Path
) -> None:
    control = _control()
    repo = tmp_path / "repo"
    outside = tmp_path / "outside.json"
    current = {
        "sourceSha256": "a" * 64,
        "runtimeSha256": "b" * 64,
        "configSha256": "c" * 64,
        "authoritySha256": "d" * 64,
        "externalEvidenceSha256": "e" * 64,
    }
    monkeypatch.setattr(control, "_causal_retry_state_path", lambda *_args: outside)

    with pytest.raises(ValueError, match="CAUSAL_RETRY_STATE_PATH_INVALID"):
        control._admit_causal_retry(
            repo_root=repo,
            issue_date="2026-08-02",
            runner_state={},
            completion=current,
        )
    assert not outside.exists()


def test_sec_red_actual_completion_producer_feeds_causal_retry_gate(
    tmp_path: Path,
) -> None:
    control = _control()
    audit = control.audit_recovery_control
    runner_state_path = tmp_path / "runner-state.json"
    runner_state_path.write_text('{"status":"publish_complete"}', encoding="utf-8")
    completion = audit._typed_public_green_readiness_red(
        issue_date="2026-08-02",
        payload={"verificationWaitSec": 0, "verificationPollSec": 1},
        expected_run_intent="ScheduledProduction",
        runner_state={
            "run_id": "run-1",
            "completionAuthorityId": "authority-1",
        },
        public={
            "ok": True,
            "date": "2026-08-02",
            "publicCompletionStatus": "green",
            "completionAuthorityId": "authority-1",
        },
        readiness={
            "ok": False,
            "reason": "RUNNER_READINESS_RED",
            "failedGateIds": ["runner_readiness"],
        },
        runner_state_path=runner_state_path,
        artifact_repo_root=tmp_path,
    )

    result = control._admit_causal_retry(
        repo_root=tmp_path,
        issue_date="2026-08-02",
        runner_state={},
        completion=completion,
    )

    assert result["allowed"] is True
    assert result["reasonCode"] == "CAUSE_INPUT_CHANGED"
