from __future__ import annotations

from datetime import datetime
import hashlib
import importlib.util
import inspect
import json
import os
from pathlib import Path
import re
import subprocess

import pytest

from tools import (
    audit_recovery_control,
    daily_self_heal,
    news_grasp_daily_control,
    news_grasp_deterministic_builders,
    news_grasp_finalization,
    news_grasp_mission_authority,
    news_grasp_operational_contract,
    news_grasp_recovery_receipts,
    news_grasp_recovery_transaction,
    verify_public_surface,
)


ROOT = Path(__file__).resolve().parents[1]
REPLAY = ROOT / "tests" / "fixtures" / "recovery" / "2026-08-14-control-plane-replay.json"
V3_FIELDS = {
    "scheduledAttemptStatus",
    "recoveryAttemptStatus",
    "publicCompletionStatus",
    "nextRunReadinessStatus",
    "auditObservationStatus",
    "externalDependencyStatus",
    "constitutionStatus",
    "operationalStatus",
}


def _truth(*, delta: bool, reached: bool, checkpoint: str = "") -> dict[str, object]:
    body: dict[str, object] = {
        "schemaVersion": "NEWS_GRASP_OPERATIONAL_TRUTH_V1",
        "issuer": news_grasp_operational_contract.OPERATIONAL_TRUTH_ISSUER,
        "issueDate": "2026-08-14",
        "stopPointKnown": True,
        "scheduledAttemptReachedRunner": reached,
        "artifactDelta": {"exists": delta, "manifestSha256": "a" * 64},
    }
    if checkpoint:
        body["stageCheckpointReceiptSha256"] = checkpoint
    body["receiptSha256"] = news_grasp_operational_contract._sha(body)
    return body


def _seal(value: dict[str, object]) -> dict[str, object]:
    body = dict(value)
    body["receiptSha256"] = hashlib.sha256(
        json.dumps(
            body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    return body


def _current_mission_authority() -> dict[str, object]:
    legacy = _seal(dict(news_grasp_mission_authority.LEGACY_EXPECTED_BODY))
    return news_grasp_mission_authority.wrap_legacy_authority(legacy)


def test_artifact_delta_without_checkpoint_never_falls_back_to_full() -> None:
    with pytest.raises(ValueError, match="RECOVERY_CHECKPOINT_REQUIRED"):
        news_grasp_operational_contract.select_recovery_branch_from_truth(
            _truth(delta=True, reached=True)
        )


def test_full_recovery_is_only_for_no_delta_before_runner() -> None:
    assert (
        news_grasp_operational_contract.select_recovery_branch_from_truth(
            _truth(delta=False, reached=False)
        )
        == "ScheduledRecoveryFull"
    )
    with pytest.raises(ValueError, match="RECOVERY_BRANCH_UNKNOWN"):
        news_grasp_operational_contract.select_recovery_branch_from_truth(
            _truth(delta=False, reached=True)
        )


def test_v3_state_vector_field_set_remains_immutable() -> None:
    result = news_grasp_operational_contract.evaluate_completion_v3(
        scheduled_attempt={"status": "failed"},
        recovery_attempt={"status": "succeeded"},
        public_receipt={"status": "verified_green", "authorityId": "auth"},
        readiness_probe={"status": "red"},
        audit_observation={"status": "green"},
        external_dependency={"status": "not_required"},
        constitution_admission={"status": "green"},
    )
    assert set(result["stateVector"]) == V3_FIELDS
    assert result["publicCompletionStatus"] == "green"
    assert result["operationalStatus"] == "degraded"


def test_slo_uses_fixed_0640_anchor_and_exact_boundaries() -> None:
    evaluate = getattr(news_grasp_operational_contract, "evaluate_recovery_slo_v2", None)
    assert callable(evaluate), "evaluate_recovery_slo_v2 is required"
    t0 = "2026-08-14T06:40:00+09:00"
    target = evaluate(
        issue_date="2026-08-14",
        transaction_started_at="2026-08-14T06:20:00+09:00",
        public_green_at="2026-08-14T07:25:00+09:00",
        done_at="2026-08-14T07:40:00+09:00",
        actual_recovery_operation_count=1,
    )
    assert target["auditSloAnchor"] == t0
    assert target["targetMet"] is True
    assert target["repairBudgetMet"] is False
    budget = evaluate(
        issue_date="2026-08-14",
        transaction_started_at="2026-08-14T06:20:00+09:00",
        public_green_at="2026-08-14T07:55:00+09:00",
        done_at="2026-08-14T08:10:00+09:00",
        actual_recovery_operation_count=1,
    )
    assert budget["targetMet"] is False
    assert budget["repairBudgetMet"] is True


def test_recovery_free_61_to_90_minutes_is_not_budget_success() -> None:
    evaluate = getattr(news_grasp_operational_contract, "evaluate_recovery_slo_v2", None)
    assert callable(evaluate), "evaluate_recovery_slo_v2 is required"
    result = evaluate(
        issue_date="2026-08-14",
        transaction_started_at="2026-08-14T06:40:00+09:00",
        public_green_at="2026-08-14T07:50:00+09:00",
        done_at="2026-08-14T08:00:00+09:00",
        actual_recovery_operation_count=0,
    )
    assert result["targetMet"] is False
    assert result["repairBudgetMet"] is False
    assert result["status"] == "public_green_slo_failed"


def test_pre_audit_green_is_not_applicable_to_audit_slo() -> None:
    evaluate = getattr(news_grasp_operational_contract, "evaluate_recovery_slo_v2", None)
    assert callable(evaluate), "evaluate_recovery_slo_v2 is required"
    result = evaluate(
        issue_date="2026-08-14",
        transaction_started_at="2026-08-14T06:10:00+09:00",
        public_green_at="2026-08-14T06:30:00+09:00",
        done_at="2026-08-14T06:35:00+09:00",
        actual_recovery_operation_count=1,
    )
    assert result["status"] == "not_applicable_pre_audit_green"


@pytest.mark.parametrize(
    ("observed_at", "operation_class", "allowed", "reason"),
    (
        ("2026-08-14T07:24:59+09:00", "new_high_cost", True, "admitted"),
        (
            "2026-08-14T07:25:00+09:00",
            "new_high_cost",
            False,
            "target_closeout_reserve_active",
        ),
        ("2026-08-14T07:39:59+09:00", "closeout", True, "admitted"),
        ("2026-08-14T07:40:00+09:00", "closeout", True, "admitted"),
        (
            "2026-08-14T07:54:59+09:00",
            "sealed_high_cost_continuation",
            True,
            "admitted",
        ),
        (
            "2026-08-14T07:55:00+09:00",
            "sealed_high_cost_continuation",
            False,
            "high_cost_cutoff_exceeded",
        ),
        ("2026-08-14T08:09:59+09:00", "closeout", True, "admitted"),
        (
            "2026-08-14T08:10:00+09:00",
            "closeout",
            False,
            "hard_deadline_exceeded",
        ),
    ),
)
def test_deadline_policy_enforces_45_60_75_90_operation_boundaries(
    observed_at: str,
    operation_class: str,
    allowed: bool,
    reason: str,
) -> None:
    policy = getattr(news_grasp_operational_contract, "recovery_deadline_policy_v2", None)
    assert callable(policy), "recovery_deadline_policy_v2 is required"
    result = policy(
        "2026-08-14",
        datetime.fromisoformat(observed_at),
        operation_class=operation_class,
    )
    assert result["operationAllowed"] is allowed
    assert result["reason"] == reason


def test_recovery_cli_has_no_alternate_terminal_or_notification_owner() -> None:
    audit_source = (ROOT / "tools/audit_recovery_control.py").read_text(
        encoding="utf-8-sig"
    )
    daily_source = (ROOT / "tools/news_grasp_daily_control.py").read_text(
        encoding="utf-8-sig"
    )
    decide_branch = audit_source.split('if args.command == "decide":', 1)[1].split(
        'elif args.command == "execute":', 1
    )[0]
    assert "write_audit_terminal" not in decide_branch
    assert 'sub.add_parser("execute-minimal-unblocker")' not in daily_source
    assert "begin_owned_operation(" in daily_source
    assert "AUDIT_RECOVERY_OWNED_OPERATION_REPLAY_REJECTED" in inspect.getsource(
        news_grasp_recovery_transaction.begin_owned_operation
    )


def test_owned_operation_receipt_rejects_same_fencing_replay(tmp_path: Path) -> None:
    first = news_grasp_recovery_transaction.acquire_or_attach(
        repo_root=tmp_path,
        issue_date="2026-08-14",
        trigger="automation",
        now=datetime.fromisoformat("2026-08-14T06:40:00+09:00"),
    )["transaction"]
    receipt = news_grasp_recovery_transaction.begin_owned_operation(
        repo_root=tmp_path,
        issue_date="2026-08-14",
        owner_receipt=first,
        operation_kind="minimal_notification_unblocker",
        cause_receipt_sha256="a" * 64,
        now=datetime.fromisoformat("2026-08-14T06:41:00+09:00"),
    )
    assert receipt["singleUse"] is True
    with pytest.raises(
        ValueError, match="AUDIT_RECOVERY_OWNED_OPERATION_REPLAY_REJECTED"
    ):
        news_grasp_recovery_transaction.begin_owned_operation(
            repo_root=tmp_path,
            issue_date="2026-08-14",
            owner_receipt=first,
            operation_kind="minimal_notification_unblocker",
            cause_receipt_sha256="a" * 64,
            now=datetime.fromisoformat("2026-08-14T06:42:00+09:00"),
        )


@pytest.mark.parametrize(
    ("run_intent", "scheduled_status", "recovery_status", "expected_terminal"),
    (
        ("ScheduledProduction", "succeeded", "not_required", "audit_normal_green"),
        (
            "ScheduledRecoveryFull",
            "failed_then_recovered",
            "succeeded",
            "audit_recovered_green",
        ),
    ),
)
def test_actual_common_finalizer_attach_preserves_normal_and_recovery_intent(
    monkeypatch,
    tmp_path: Path,
    run_intent: str,
    scheduled_status: str,
    recovery_status: str,
    expected_terminal: str,
) -> None:
    issue_date = "2026-08-14"
    artifact = tmp_path / "artifact"
    ops = tmp_path / "ops"
    live = tmp_path / "live"
    artifact.mkdir()
    ops.mkdir()
    live.mkdir()
    subprocess.run(["git", "init", str(artifact)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(artifact), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(artifact), "config", "user.name", "News Grasp Test"],
        check=True,
    )
    marker = artifact / "README.md"
    marker.write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(artifact), "add", "README.md"], check=True)
    subprocess.run(
        ["git", "-C", str(artifact), "commit", "-m", "fixture"],
        check=True,
        capture_output=True,
    )
    head = subprocess.run(
        ["git", "-C", str(artifact), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    run_id = f"fixture-{run_intent.lower()}"
    lineage = daily_self_heal._producer_lineage_expected(
        repo_root=artifact,
        ops_root=ops,
        date=issue_date,
        run_intent=run_intent,
        run_id=run_id,
    )
    state_path = live / "news-grasp-runner-state.json"
    state_path.write_text(
        json.dumps(
            {
                "date": issue_date,
                "status": "publish_complete",
                "exit_code": 0,
                "run_intent": run_intent,
                "run_id": run_id,
                "repo_dir": str(artifact),
                "artifactRoot": str(artifact),
                **lineage,
            }
        ),
        encoding="utf-8",
    )

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = datetime.fromisoformat("2026-08-14T07:00:00+09:00")
            return value.astimezone(tz) if tz is not None else value.replace(tzinfo=None)

    monkeypatch.setattr(audit_recovery_control, "CANONICAL_REPO_ROOT", ops)
    monkeypatch.setattr(
        audit_recovery_control, "CANONICAL_RUNNER_STATE_PATH", state_path
    )
    monkeypatch.setattr(audit_recovery_control, "datetime", FixedDateTime)
    monkeypatch.setattr(
        audit_recovery_control, "_resolve_artifact_repo_root", lambda _: artifact
    )

    public_observation = {
        "ok": True,
        "date": issue_date,
        "public_status": "green",
        "verified_at": "2026-08-14T06:50:00+09:00",
        "local_head": head,
        "deploy_head": head,
        "artifact_commit": head,
        "publish": {"ok": True, "deploy_head": head},
        "distribution_artifacts": {"missing": []},
        "distribution_manifest": {"date": issue_date},
        "notification": {"ok": True},
        "podcasts": {"primary": {"ok": True}, "deepdive": {"ok": True}},
        "deepdive_shared_quality": {"status": "Green"},
        "pwa": {"ok": True},
        "audio": {"ok": True},
    }
    monkeypatch.setattr(
        daily_self_heal,
        "verify_public_completion",
        lambda **_: dict(public_observation),
    )
    monkeypatch.setattr(
        daily_self_heal,
        "verify_live_runner_readiness",
        lambda **_: {"ok": True},
    )
    monkeypatch.setattr(
        news_grasp_finalization,
        "observe_remote_publish_head",
        lambda **_: {
            "ok": True,
            "publishCommit": head,
            "observationKind": "fixture_remote_head",
        },
    )

    completion = audit_recovery_control._verify_same_date_completion(
        issue_date=issue_date,
        payload={
            "verificationWaitSec": 0,
            "verificationPollSec": 10,
            "transactionStartedAt": "2026-08-14T06:40:00+09:00",
        },
        expected_run_intent=run_intent,
    )
    assert audit_recovery_control.same_date_completion_green(
        issue_date, completion
    )
    legacy = json.loads(
        (artifact / "build" / "publish-complete" / f"{issue_date}.json").read_text(
            encoding="utf-8-sig"
        )
    )
    assert legacy["scheduled_attempt_status"] == scheduled_status
    assert legacy["recovery_attempt_status"] == recovery_status
    terminal = news_grasp_daily_control._audit_green_terminal(
        issue_date=issue_date,
        decision={
            "scheduledAttemptStatus": scheduled_status,
            "recoveryAttemptStatus": recovery_status,
            "publicStatus": "green",
            "receiptSha256": "a" * 64,
        },
        completion=completion,
        recovered=run_intent == "ScheduledRecoveryFull",
    )
    assert terminal["terminal"] == expected_terminal


def test_execution_receipt_v2_binds_branch_roots_python_and_deadline() -> None:
    validator = getattr(news_grasp_recovery_receipts, "validate_recovery_execution_receipt_v2", None)
    assert callable(validator), "RECOVERY_EXECUTION_RECEIPT_V2 validator is required"
    receipt = _seal(
        {
            "schemaVersion": "NEWS_GRASP_RECOVERY_EXECUTION_RECEIPT_V2",
            "issueDate": "2026-08-14",
            "transactionId": "tx-20260814",
            "fencingToken": 3,
            "branch": "ResumeFromStage",
            "resumeFromStage": "summary_audio",
            "checkpointReceiptSha256": "f" * 64,
            "artifactSnapshot": None,
            "operationalTruthPath": "C:/artifact/build/recovery/truth.json",
            "operationalTruthReceiptSha256": "0" * 64,
            "roots": {
                "artifactRepoRoot": "C:/artifact",
                "opsRepoRoot": "C:/ops",
                "productionRuntimeRoot": "C:/runtime",
                "liveBinRoot": "C:/live",
            },
            "rootHashes": {
                "artifactRepoHead": "1" * 40,
                "opsRepoHead": "2" * 40,
                "productionRuntimeBindingSha256": "3" * 64,
                "liveBindingSha256": "4" * 64,
            },
            "python": {"path": "C:/Python/python.exe", "sha256": "5" * 64},
            "capabilityReservation": {
                "receiptPath": "C:/artifact/build/recovery/reservation.json",
                "receiptSha256": "6" * 64,
            },
            "deadline": {
                "auditSloAnchor": "2026-08-14T06:40:00+09:00",
                "targetDeadline": "2026-08-14T07:40:00+09:00",
                "highCostCutoff": "2026-08-14T07:55:00+09:00",
                "hardDeadline": "2026-08-14T08:10:00+09:00",
                "postGreenMinutes": 15,
            },
            "singleUse": True,
            "issuedAt": "2026-08-14T06:42:00+09:00",
            "nonce": "receipt-v2-positive",
        }
    )
    assert validator(receipt, issue_date="2026-08-14")["branch"] == "ResumeFromStage"
    tampered = dict(receipt)
    tampered["branch"] = "ScheduledRecoveryFull"
    with pytest.raises(ValueError, match="RECOVERY_EXECUTION_RECEIPT_V2_INVALID"):
        validator(tampered, issue_date="2026-08-14")


def test_actual_launch_identity_binds_live_process_runtime_and_authority(
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "artifact"
    ops_root = artifact_root / "ops"
    artifact_root.mkdir()
    ops_root.mkdir()
    runner = ops_root / "news-grasp-runner.ps1"
    python = ops_root / "python.exe"
    runner.write_bytes(b"runner-v2")
    python.write_bytes(b"python-v2")
    runner_sha = hashlib.sha256(runner.read_bytes()).hexdigest()
    binding_sha = "b" * 64
    binding = ops_root / "news-grasp-high-cost-binding-v1.json"
    binding.write_text(
        json.dumps(
            {
                "schemaVersion": "NEWS_GRASP_HIGH_COST_BINDING_V1",
                "bindingReceiptSha256": binding_sha,
            }
        ),
        encoding="utf-8",
    )
    snapshot = news_grasp_recovery_transaction.build_readiness_snapshot_v2(
        issue_date="2026-08-14",
        observations={"runnerSha256": runner_sha},
        observed_at="2026-08-14T05:55:00+09:00",
    )
    broker = _seal(
        {
            "schemaVersion": "SCHEDULED_PRODUCTION_LAUNCH_PERMIT_V1",
            "issueDate": "2026-08-14",
            "taskActionSha256": "a" * 64,
            "runnerSha256": runner_sha,
        }
    )
    authority = news_grasp_recovery_transaction.issue_launch_permit_v2(
        issue_date="2026-08-14",
        readiness_snapshot=snapshot,
        task_action_sha256="a" * 64,
        runner_sha256=runner_sha,
        launch_nonce="fixture-launch",
        broker_authority=broker,
        mission_authority_v2=_current_mission_authority(),
        mission_authority_v2_path="C:/bin/news-grasp-authority/audit-mission-authority-v2.json",
        mission_authority_v2_file_sha256="5" * 64,
    )
    authority_path = ops_root / "launch-authority.json"
    authority_path.write_text(json.dumps(authority), encoding="utf-8")
    run_id = "c" * 32
    output = (
        artifact_root
        / "build"
        / "recovery"
        / "launch-identities"
        / "2026-08-14"
        / f"{run_id}.json"
    )
    result = news_grasp_recovery_transaction.issue_actual_launch_identity_v1(
        output=output,
        issue_date="2026-08-14",
        run_id=run_id,
        run_intent="ScheduledProduction",
        process_id=os.getpid(),
        runner=runner,
        artifact_root=artifact_root,
        ops_root=ops_root,
        python=python,
        high_cost_binding_receipt=binding,
        expected_high_cost_binding_receipt_sha256=binding_sha,
        launch_authority=authority_path,
    )
    assert result["schemaVersion"] == "NEWS_GRASP_ACTUAL_LAUNCH_IDENTITY_V1"
    assert result["process"]["processId"] == os.getpid()
    assert result["runner"]["sha256"] == runner_sha
    assert result["launchAuthority"]["receiptSha256"] == authority["receiptSha256"]
    assert result["externalCallCount"] == 0
    assert output.is_file()
    validated = news_grasp_recovery_transaction.validate_actual_launch_identity_v1(
        result,
        receipt_path=output,
        issue_date="2026-08-14",
        run_id=run_id,
        process_id=os.getpid(),
        artifact_root=artifact_root,
        current_task_action_sha256="a" * 64,
    )
    assert validated["processAlive"] is True
    runner.write_bytes(b"runner-tampered")
    with pytest.raises(ValueError, match="NEWS_GRASP_ACTUAL_LAUNCH_IDENTITY_INVALID"):
        news_grasp_recovery_transaction.validate_actual_launch_identity_v1(
            result,
            receipt_path=output,
            issue_date="2026-08-14",
            run_id=run_id,
            process_id=os.getpid(),
            artifact_root=artifact_root,
            current_task_action_sha256="a" * 64,
        )


def test_actual_launch_identity_rejects_tampered_authority(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifact"
    artifact_root.mkdir()
    runner = artifact_root / "runner.ps1"
    python = artifact_root / "python.exe"
    binding = artifact_root / "binding.json"
    authority = artifact_root / "authority.json"
    runner.write_bytes(b"runner")
    python.write_bytes(b"python")
    binding.write_text(
        json.dumps(
            {
                "schemaVersion": "NEWS_GRASP_HIGH_COST_BINDING_V1",
                "bindingReceiptSha256": "b" * 64,
            }
        ),
        encoding="utf-8",
    )
    authority.write_text(
        json.dumps(
            {
                "schemaVersion": "SCHEDULED_PRODUCTION_LAUNCH_PERMIT_V2",
                "issueDate": "2026-08-14",
                "runnerSha256": hashlib.sha256(runner.read_bytes()).hexdigest(),
                "taskActionSha256": "a" * 64,
                "readinessSnapshotSha256": "c" * 64,
                "receiptSha256": "d" * 64,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="NEWS_GRASP_ACTUAL_LAUNCH_IDENTITY_INVALID"):
        news_grasp_recovery_transaction.issue_actual_launch_identity_v1(
            output=(
                artifact_root
                / "build/recovery/launch-identities/2026-08-14/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee.json"
            ),
            issue_date="2026-08-14",
            run_id="e" * 32,
            run_intent="ScheduledProduction",
            process_id=os.getpid(),
            runner=runner,
            artifact_root=artifact_root,
            ops_root=artifact_root,
            python=python,
            high_cost_binding_receipt=binding,
            expected_high_cost_binding_receipt_sha256="b" * 64,
            launch_authority=authority,
        )


def test_runner_enforces_closeout_reserve_and_issues_launch_identity() -> None:
    runner = (ROOT / "scripts" / "ops" / "news-grasp-runner.ps1").read_text(
        encoding="utf-8-sig"
    )
    assert "$TimeoutSec = 2700" in runner
    assert "SCHEDULED_PRODUCTION_TARGET_CLOSEOUT_RESERVE_ACTIVE" in runner
    assert "$script:ScheduledProductionHighCostDeadlineAt" in runner
    assert "RECOVERY_TARGET_CLOSEOUT_RESERVE_ACTIVE" in runner
    assert "RECOVERY_HIGH_COST_CUTOFF_EXCEEDED" in runner
    assert "-OperationClass 'NewHighCost'" in runner
    assert "-OperationClass 'Closeout'" in runner
    assert "deadline.targetDeadline" in runner
    assert ".AddMinutes(-$recoveryPostGreenMinutes)" in runner
    assert "issue-launch-identity" in runner
    assert "NEWS_GRASP_ACTUAL_LAUNCH_IDENTITY_V1" in runner
    assert runner.index("issue-launch-identity") < runner.index(
        "'admit' '--operation-kind' $operationKind"
    )


def test_resume_stage_set_is_single_canonical_set_with_producer_checkpoints() -> None:
    runner = (ROOT / "scripts" / "ops" / "news-grasp-runner.ps1").read_text(
        encoding="utf-8-sig"
    )
    expected = {
        "post-reporter",
        "post-daily-quality",
        "post-deepdive",
        "generation-quality-repair",
    }
    assert set(news_grasp_operational_contract.RECOVERY_RESUME_STAGES) == expected
    for stage in expected:
        assert f"-ResumeStageCheckpoint '{stage}'" in runner
    resume_validate_sets = re.findall(
        r"\[ValidateSet\(([^\]]+)\)\]\s*\r?\n\s*\[string\] \$(?:ResumeFromStage|ResumeStageCheckpoint)",
        runner,
    )
    assert len(resume_validate_sets) == 3
    for values in resume_validate_sets:
        actual = {value for value in re.findall(r"'([^']*)'", values) if value}
        assert actual == expected


def test_public_terminal_preserves_authority_but_watcher_propagates_exit_two() -> None:
    runner = (ROOT / "scripts" / "ops" / "news-grasp-runner.ps1").read_text(
        encoding="utf-8-sig"
    )
    watcher = (
        ROOT / "scripts" / "ops" / "watch-news-grasp-runner.ps1"
    ).read_text(encoding="utf-8-sig")
    assert runner.index("news-grasp-runner.ps1 PUBLIC GREEN") < runner.index(
        "COMMON_FINALIZATION_DEGRADED"
    )
    assert runner.index("COMMON_FINALIZATION_DEGRADED") < runner.rindex(
        "news-grasp-runner.ps1 OK"
    )
    assert "RUNNER_PUBLIC_TERMINAL_WITH_DEGRADED_AUTOMATION" in watcher
    assert "RUNNER_TERMINAL_PROCESS_EXIT_TIMEOUT" in watcher
    assert "$script:WatchExitCode = $terminalProcessExitCode" in watcher
    assert "$script:WatchExitCode = 0" not in watcher[
        watcher.index("if (Test-TerminalState -State $state)") : watcher.index(
            "if ($state -and [string]$state.status -eq 'error'"
        )
    ]
    assert "$finalRunnerState = Read-State" in watcher
    assert "(-not (Test-TerminalState -State $finalRunnerState))" not in watcher
    assert "$script:WatchExitCode = Invoke-CanonicalRecoveryEnsure" in watcher


def test_public_authority_v2_does_not_require_readiness_green() -> None:
    validator = getattr(news_grasp_operational_contract, "validate_completion_authority_v2", None)
    assert callable(validator), "COMPLETION_AUTHORITY_V2 validator is required"
    lineage = {
        "generationId": "generation-20260814",
        "publishCommit": "7" * 40,
        "producerOperationId": "8" * 64,
    }
    checks = {
        field: True
        for field in news_grasp_operational_contract.PUBLIC_COMPLETION_FIELDS
    }
    manifest = _seal(
        {
            "schemaVersion": "NEWS_GRASP_PUBLIC_COMPLETION_MANIFEST_V2",
            "issueDate": "2026-08-14",
            "profile": "public-only-v3",
            "publicStatus": "green",
            "checks": checks,
            "evidenceSha256": {field: "9" * 64 for field in checks},
            "producerLineage": lineage,
        }
    )
    authority = _seal(
        {
            "schemaVersion": "COMPLETION_AUTHORITY_V2",
            "issuer": news_grasp_operational_contract.COMPLETION_AUTHORITY_ISSUER,
            "issueDate": "2026-08-14",
            "completionAuthorityId": "authority-20260814",
            "publicManifestSha256": manifest["receiptSha256"],
            "publicManifest": manifest,
            "producerLineage": lineage,
            "firstVerifiedTerminal": "audit_recovered_green",
            "decisionReceiptSha256": "a" * 64,
        }
    )
    assert validator(authority, issue_date="2026-08-14")["completionAuthorityId"]
    assert "nextRunReadinessStatus" not in manifest
    broken = json.loads(json.dumps(authority))
    broken["publicManifest"]["checks"]["notification"] = False
    broken = _seal({key: value for key, value in broken.items() if key != "receiptSha256"})
    with pytest.raises(ValueError, match="AUDIT_COMPLETION_AUTHORITY_V2_INVALID"):
        validator(broken, issue_date="2026-08-14")


def test_mission_authority_terminal_schema_includes_observation_unverified() -> None:
    source = inspect.getsource(news_grasp_mission_authority)
    assert "audit_observation_unverified" in source
    assert "AUDIT_RECOVERY_DECISION_V2" in source


def test_outcome_sidecar_preserves_exact_v3_and_separates_readiness_debt() -> None:
    state = news_grasp_operational_contract.evaluate_completion_v3(
        scheduled_attempt={"status": "failed"},
        recovery_attempt={"status": "succeeded"},
        public_receipt={"status": "verified_green", "authorityId": "auth"},
        readiness_probe={"status": "red"},
        audit_observation={"status": "green"},
        external_dependency={"status": "not_required"},
        constitution_admission={"status": "green"},
    )
    slo = news_grasp_operational_contract.evaluate_recovery_slo_v2(
        issue_date="2026-08-14",
        transaction_started_at="2026-08-14T06:40:00+09:00",
        public_green_at="2026-08-14T07:25:00+09:00",
        done_at="2026-08-14T07:40:00+09:00",
        actual_recovery_operation_count=1,
    )
    outcome = news_grasp_operational_contract.build_completion_outcome_envelope_v1(
        completion_state_vector_v3=state,
        completion_authority_sha256="e" * 64,
        slo=slo,
        automation_outcome="audit_recovered_green",
        readiness_debt={"status": "red", "reason": "scheduled_task_missed_runs"},
        generated_at="2026-08-14T07:40:00+09:00",
    )
    assert set(state["stateVector"]) == V3_FIELDS
    assert outcome["readinessDebt"]["status"] == "red"
    assert outcome["automationOutcome"] == "audit_recovered_green"
    assert "stateVector" not in outcome


def test_new_audit_decisions_are_v2_and_accept_all_four_terminals() -> None:
    for terminal in sorted(audit_recovery_control.AUDIT_TERMINALS):
        public_green = terminal != "audit_major_incident_open"
        body: dict[str, object] = {
            "schemaVersion": "AUDIT_RECOVERY_DECISION_V1",
            "issueDate": "2026-08-14",
            "terminal": terminal,
            "publicStatus": "green" if public_green else "incomplete",
            "workPriority": (
                audit_recovery_control.PUBLIC_GREEN_FOLLOWUP_PRIORITY
                if public_green
                else audit_recovery_control.SAME_DAY_PUBLIC_RECOVERY_PRIORITY
            ),
            "action": "none" if public_green else "escalate_major_incident",
        }
        if public_green:
            body["allowedAfterPublicGreen"] = list(
                audit_recovery_control.PUBLIC_GREEN_ALLOWED_OPERATIONS
            )
        else:
            body["allowedBeforePublicGreen"] = audit_recovery_control.ALLOWED_BEFORE_PUBLIC_GREEN
            body["forbiddenBeforePublicGreen"] = audit_recovery_control.FORBIDDEN_BEFORE_PUBLIC_GREEN
        sealed = audit_recovery_control.seal_audit_decision(body)
        assert sealed["schemaVersion"] == "AUDIT_RECOVERY_DECISION_V2"


def test_public_surface_verifier_consumes_manifest_without_recursive_publish_verifier() -> None:
    pure = getattr(verify_public_surface, "verify_sealed_public_manifest", None)
    assert callable(pure), "pure sealed-manifest public oracle is required"
    checks = {
        field: True
        for field in news_grasp_operational_contract.PUBLIC_COMPLETION_FIELDS
    }
    manifest = _seal(
        {
            "schemaVersion": "NEWS_GRASP_PUBLIC_COMPLETION_MANIFEST_V2",
            "issueDate": "2026-08-14",
            "profile": "public-only-v3",
            "publicStatus": "green",
            "checks": checks,
            "evidenceSha256": {field: "b" * 64 for field in checks},
            "producerLineage": {
                "generationId": "generation-20260814",
                "publishCommit": "c" * 40,
                "producerOperationId": "d" * 64,
            },
        }
    )
    assert pure(manifest, issue_date="2026-08-14")["ok"] is True
    manifest["publicStatus"] = "red"
    with pytest.raises(ValueError, match="PUBLIC_COMPLETION_MANIFEST_INVALID"):
        pure(manifest, issue_date="2026-08-14")


def test_audio_script_builder_materializes_idempotent_file(tmp_path: Path) -> None:
    materialize = getattr(news_grasp_deterministic_builders, "materialize_summary_audio_script", None)
    assert callable(materialize), "audio script materializer is required"
    target = tmp_path / "digest" / "Audio" / "2026-08-14.md"
    summary = {
        "issueDate": "2026-08-14",
        "title": "制御面を一本化する",
        "sections": ["公開品質を保つ。", "同じ入力なら同じscriptを生成する。"],
    }
    first = materialize(summary, target)
    second = materialize(summary, target)
    assert target.is_file()
    assert first["outputSha256"] == second["outputSha256"]
    assert first["sourceHash"] == second["sourceHash"]
    assert second["changed"] is False
    assert "文字数" not in target.read_text(encoding="utf-8")


def test_distribution_builder_materializes_sealed_pre_verifier_artifact(
    tmp_path: Path,
) -> None:
    materialize = getattr(
        news_grasp_deterministic_builders,
        "materialize_distribution_manifest_v2",
        None,
    )
    assert callable(materialize), "distribution manifest materializer is required"
    artifacts = {
        "summaryAudio": {"path": "build/tts/latest_audio.json", "sha256": "1" * 64},
        "summaryPodcast": {
            "path": "build/youtube-podcast/uploads.json",
            "sha256": "2" * 64,
        },
        "deepdiveAudio": {
            "path": "build/tts/deepdive/latest_audio.json",
            "sha256": "3" * 64,
        },
        "deepdivePodcast": {
            "path": "build/youtube-podcast-deepdive/uploads.json",
            "sha256": "4" * 64,
        },
    }
    target = tmp_path / "data" / "distribution" / "2026-08-14.json"
    first = materialize(
        issue_date="2026-08-14",
        generation_id="generation-20260814",
        pre_publish_commit="a" * 40,
        artifacts=artifacts,
        target=target,
    )
    second = materialize(
        issue_date="2026-08-14",
        generation_id="generation-20260814",
        pre_publish_commit="a" * 40,
        artifacts=artifacts,
        target=target,
    )
    persisted = json.loads(target.read_text(encoding="utf-8"))
    assert persisted["schemaVersion"] == "NEWS_GRASP_DISTRIBUTION_MANIFEST_V2"
    assert persisted["stage"] == "pre-verifier"
    assert persisted["receiptSha256"] == first["receiptSha256"]
    assert second["changed"] is False
    assert persisted["artifacts"]["deepdivePodcast"]["sha256"] == "4" * 64


@pytest.mark.parametrize("status", ["skipped_not_normal", "skipped_fallback", "dry_run"])
def test_notification_outcome_rejects_undelivered_success_status(status: str) -> None:
    build = getattr(
        news_grasp_deterministic_builders,
        "build_notification_outcome_v2",
        None,
    )
    assert callable(build), "notification outcome V2 builder is required"
    outcome = build(
        issue_date="2026-08-14",
        status=status,
        source="runner",
        subscription_count=0,
        sent_count=0,
    )
    assert outcome["ok"] is False
    assert "deliveryReceipt" not in outcome
    assert "audienceResolutionReceipt" not in outcome


def test_notification_outcome_seals_delivery_or_zero_audience_receipt() -> None:
    build = news_grasp_deterministic_builders.build_notification_outcome_v2
    sent = build(
        issue_date="2026-08-14",
        status="sent",
        source="worker",
        subscription_count=2,
        sent_count=2,
        evidence={"payloadSha256": "5" * 64, "audienceSha256": "6" * 64},
    )
    zero = build(
        issue_date="2026-08-14",
        status="no_subscribers",
        source="worker",
        subscription_count=0,
        sent_count=0,
        evidence={"audienceSha256": "7" * 64},
    )
    assert sent["ok"] is True
    assert sent["deliveryReceipt"]["receiptSha256"]
    assert zero["ok"] is True
    assert zero["audienceResolutionReceipt"]["resolvedAudienceCount"] == 0
    assert zero["audienceResolutionReceipt"]["receiptSha256"]


def test_runner_materializes_audio_before_quality_and_never_marks_skip_green() -> None:
    runner = (ROOT / "scripts" / "ops" / "news-grasp-runner.ps1").read_text(
        encoding="utf-8-sig"
    )
    audio_call = "materialize-summary-audio"
    assert runner.index(audio_call) < runner.index("daily quality gate start")
    assert "materialize-distribution" in runner
    notification = runner.split("# ===== 6. Web Push 通知", 1)[1].split(
        "publish-complete manifest verification start", 1
    )[0]
    assert "'--issue-date' $DateStamp" in notification
    assert "status = 'skipped_not_normal'" in notification
    skipped = notification.split("status = 'skipped_not_normal'", 1)[1].split("}", 1)[0]
    assert "ok = $false" in skipped
    assert "non-fatal" not in notification


def test_20260814_replay_maps_every_repeat_to_an_upstream_stop() -> None:
    replay = json.loads(REPLAY.read_text(encoding="utf-8"))
    classifier = getattr(audit_recovery_control, "classify_recovery_preflight_stop_v2", None)
    assert callable(classifier), "2026-08-14 replay classifier is required"
    for event in replay["events"]:
        assert classifier(event["reasonCode"]) == event["expectedStop"]


def test_deadman_and_daily_controller_have_no_direct_runner_owner() -> None:
    deadman = (ROOT / "scripts" / "ops" / "news-grasp-deadman.ps1").read_text(
        encoding="utf-8-sig"
    )
    daily = (ROOT / "tools" / "news_grasp_daily_control.py").read_text(encoding="utf-8")
    assert "tools.audit_recovery_control' 'ensure-0640'" in deadman
    execute_block = daily[daily.index("def execute_audit_0640(") : daily.index("def main(")]
    assert "news-grasp-runner.ps1" not in execute_block
    assert "-RecoveryDecisionPath" not in execute_block


def test_issue_date_transaction_acquires_attaches_and_reuses_terminal(tmp_path: Path) -> None:
    first = news_grasp_recovery_transaction.acquire_or_attach(
        repo_root=tmp_path,
        issue_date="2026-08-14",
        trigger="deadman",
        now=datetime.fromisoformat("2026-08-14T06:40:00+09:00"),
    )
    assert first["mode"] == "owner"
    attached = news_grasp_recovery_transaction.acquire_or_attach(
        repo_root=tmp_path,
        issue_date="2026-08-14",
        trigger="automation",
        now=datetime.fromisoformat("2026-08-14T06:40:01+09:00"),
    )
    assert attached["mode"] == "attached"
    assert attached["processExitCode"] == 3
    terminal = {"terminal": "audit_recovered_green", "issueDate": "2026-08-14"}
    committed = news_grasp_recovery_transaction.finalize(
        repo_root=tmp_path,
        issue_date="2026-08-14",
        owner_receipt=first["transaction"],
        terminal_projection=terminal,
        process_exit_code=0,
        now=datetime.fromisoformat("2026-08-14T07:20:00+09:00"),
    )
    assert committed["phase"] == "terminal_green"
    projected = news_grasp_recovery_transaction.acquire_or_attach(
        repo_root=tmp_path,
        issue_date="2026-08-14",
        trigger="watcher",
        now=datetime.fromisoformat("2026-08-14T07:21:00+09:00"),
    )
    assert projected["mode"] == "terminal_projection"
    assert projected["transaction"]["terminalProjection"] == terminal


def test_stale_owner_advances_fencing_and_rejects_aba_finalize(tmp_path: Path) -> None:
    first = news_grasp_recovery_transaction.acquire_or_attach(
        repo_root=tmp_path,
        issue_date="2026-08-14",
        trigger="deadman",
        now=datetime.fromisoformat("2026-08-14T06:40:00+09:00"),
    )
    path = news_grasp_recovery_transaction.transaction_path(tmp_path, "2026-08-14")
    stale = {
        key: value
        for key, value in first["transaction"].items()
        if key != "receiptSha256"
    }
    stale["ownerProcessId"] = 999999
    stale["ownerProcessCreationToken"] = "dead-owner"
    stale["leaseExpiresAt"] = "2026-08-14T06:44:59+09:00"
    path.write_text(
        json.dumps(news_grasp_recovery_transaction._seal(stale)), encoding="utf-8"
    )
    second = news_grasp_recovery_transaction.acquire_or_attach(
        repo_root=tmp_path,
        issue_date="2026-08-14",
        trigger="automation",
        now=datetime.fromisoformat("2026-08-14T06:45:00+09:00"),
    )
    assert second["mode"] == "owner"
    assert second["transaction"]["fencingToken"] == 2
    with pytest.raises(ValueError, match="AUDIT_RECOVERY_FENCING_TOKEN_STALE"):
        news_grasp_recovery_transaction.finalize(
            repo_root=tmp_path,
            issue_date="2026-08-14",
            owner_receipt=first["transaction"],
            terminal_projection={"terminal": "audit_recovered_green"},
            process_exit_code=0,
        )


def test_0555_snapshot_is_reusable_evidence_but_0600_permit_is_authority() -> None:
    snapshot = news_grasp_recovery_transaction.build_readiness_snapshot_v2(
        issue_date="2026-08-14",
        observations={"runtimeBindingSha256": "1" * 64, "pythonSha256": "2" * 64},
        observed_at="2026-08-14T05:55:00+09:00",
    )
    broker = _seal(
        {
            "schemaVersion": "SCHEDULED_PRODUCTION_LAUNCH_PERMIT_V1",
            "issueDate": "2026-08-14",
            "taskActionSha256": "3" * 64,
            "runnerSha256": "4" * 64,
            "launchNonce": "broker-launch-20260814",
        }
    )
    permit = news_grasp_recovery_transaction.issue_launch_permit_v2(
        issue_date="2026-08-14",
        readiness_snapshot=snapshot,
        task_action_sha256="3" * 64,
        runner_sha256="4" * 64,
        launch_nonce="launch-20260814",
        broker_authority=broker,
        mission_authority_v2=_current_mission_authority(),
        mission_authority_v2_path="C:/bin/news-grasp-authority/audit-mission-authority-v2.json",
        mission_authority_v2_file_sha256="5" * 64,
    )
    assert snapshot["authority"] is False
    assert snapshot["consumable"] is False
    assert permit["singleUse"] is True
    assert permit["readinessSnapshotSha256"] == snapshot["receiptSha256"]
    assert news_grasp_recovery_transaction.extract_broker_authority(
        permit,
        issue_date="2026-08-14",
        task_action_sha256="3" * 64,
        runner_sha256="4" * 64,
    ) == broker


def test_launch_permit_v2_binds_current_mission_authority_without_overwriting_broker_v1() -> None:
    snapshot = news_grasp_recovery_transaction.build_readiness_snapshot_v2(
        issue_date="2026-08-14",
        observations={"runtimeBindingSha256": "1" * 64},
        observed_at="2026-08-14T05:55:00+09:00",
    )
    broker = _seal(
        {
            "schemaVersion": "SCHEDULED_PRODUCTION_LAUNCH_PERMIT_V1",
            "issueDate": "2026-08-14",
            "taskActionSha256": "3" * 64,
            "runnerSha256": "4" * 64,
        }
    )
    legacy = _seal(dict(news_grasp_mission_authority.LEGACY_EXPECTED_BODY))
    current = news_grasp_mission_authority.wrap_legacy_authority(legacy)
    permit = news_grasp_recovery_transaction.issue_launch_permit_v2(
        issue_date="2026-08-14",
        readiness_snapshot=snapshot,
        task_action_sha256="3" * 64,
        runner_sha256="4" * 64,
        launch_nonce="launch-v2-mission",
        broker_authority=broker,
        mission_authority_v2=current,
        mission_authority_v2_path="C:/bin/news-grasp-authority/audit-mission-authority-v2.json",
        mission_authority_v2_file_sha256="5" * 64,
    )
    assert permit["missionAuthorityV2Sha256"] == current["receiptSha256"]
    assert permit["missionAuthoritySourceV1Sha256"] == legacy["receiptSha256"]
    assert news_grasp_recovery_transaction.extract_broker_authority(
        permit,
        issue_date="2026-08-14",
        task_action_sha256="3" * 64,
        runner_sha256="4" * 64,
    ) == broker


def test_bootstrap_materializes_snapshot_before_v2_permit_and_runner_unwraps_it() -> None:
    bootstrap = (ROOT / "scripts" / "ops" / "news-grasp-bootstrap.ps1").read_text(
        encoding="utf-8-sig"
    )
    runner = (ROOT / "scripts" / "ops" / "news-grasp-runner.ps1").read_text(
        encoding="utf-8-sig"
    )
    final_authority = bootstrap[bootstrap.rindex("$broker = if") :]
    assert final_authority.index("build-readiness-snapshot-v2") < final_authority.index(
        "wrap-launch-permit-v2"
    )
    assert final_authority.index("wrap-launch-permit-v2") < final_authority.index(
        "& powershell.exe @args"
    )
    assert "broker-audit-mission-authority-v1.json" in final_authority
    assert "audit-mission-authority-v2.json" in final_authority
    assert "--mission-authority-v2" in final_authority
    admission = runner[runner.index("$admissionDir =") : runner.index("# ===== 外部制御面pure readiness")]
    assert admission.index("extract-broker-authority") < admission.index(
        "'admit' '--operation-kind'"
    )
    assert "$brokerAuthorityEvidencePath" in admission


def test_recovery_preflight_rejects_expired_five_minute_deadline_before_io() -> None:
    with pytest.raises(ValueError, match="RECOVERY_PREFLIGHT_DEADLINE_EXCEEDED"):
        __import__(
            "tools.news_grasp_daily_control", fromlist=["issue_recovery_execution_receipt_v2"]
        ).issue_recovery_execution_receipt_v2(
            actual=object(),
            issue_date="2026-08-14",
            decision={},
            transaction_receipt={
                "schemaVersion": "AUDIT_RECOVERY_TRANSACTION_V2",
                "issueDate": "2026-08-14",
                "phase": "owned_preflight",
                "preflightDeadline": "2026-08-14T06:45:00+09:00",
            },
        )


def test_recovery_parent_and_runner_share_the_ninety_minute_deadline(
    tmp_path: Path,
) -> None:
    receipt = tmp_path / "build" / "recovery" / "execution.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(
        json.dumps(
            {"deadline": {"hardDeadline": "2026-08-14T08:10:00+09:00"}}
        ),
        encoding="utf-8",
    )
    daily_control = __import__("tools.news_grasp_daily_control", fromlist=["_remaining_deadline_seconds"])
    deadline = json.loads(receipt.read_text(encoding="utf-8"))["deadline"]["hardDeadline"]
    assert daily_control._remaining_deadline_seconds(
        deadline,
        observed_at=datetime.fromisoformat("2026-08-14T08:00:00+09:00"),
    ) == 600
    with pytest.raises(ValueError, match="AUDIT_RECOVERY_HARD_DEADLINE_EXPIRED"):
        daily_control._remaining_deadline_seconds(
            deadline,
            observed_at=datetime.fromisoformat("2026-08-14T08:10:00+09:00"),
        )

    runner = (ROOT / "scripts" / "ops" / "news-grasp-runner.ps1").read_text(
        encoding="utf-8-sig"
    )
    assert "Get-NewsGraspRecoveryBoundedTimeoutSec" in runner
    assert "RECOVERY_HIGH_COST_CUTOFF_EXCEEDED" in runner
    assert "deadline.hardDeadline" in runner
    assert "deadline.highCostCutoff" in runner
    assert "'--wait-sec' $PodcastVerifyWaitSec" in runner
    assert "'--wait-sec' $DeepDivePodcastVerifyWaitSec" in runner
    assert "timeout=10800" not in inspect.getsource(
        audit_recovery_control.execute_audit_recovery
    )


def test_full_recovery_policy_is_user_confirmed_and_fail_closed() -> None:
    from tools import news_grasp_daily_control

    policy = news_grasp_daily_control._validate_full_recovery_policy(ROOT)
    assert policy["sourceStatus"] == "UserConfirmed"
    assert policy["brokerReceiptRequired"] is True
    assert policy["artifactSnapshotRequired"] is True
    assert policy["unknownRoute"] == "audit_major_incident_open"


def test_versioned_automation_assets_bind_canonical_prompt_and_installed_guard() -> None:
    manifest = json.loads(
        (ROOT / "config" / "news_grasp_automation_assets_v2.json").read_text(
            encoding="utf-8"
        )
    )
    assets = {row["assetId"]: row for row in manifest["assets"]}
    assert assets["audit-recovery-prompt-v2"]["sourcePath"] == (
        "automation/prompts/news-grasp-0640-v2.md"
    )
    assert assets["common-finalization-guard-v2"]["sourcePath"] == (
        "automation/guards/news-grasp-finalization-guard-v2.py"
    )
    prompt = (ROOT / assets["audit-recovery-prompt-v2"]["sourcePath"]).read_text(
        encoding="utf-8-sig"
    )
    assert "audit_recovery_control ensure-0640" in prompt
    assert "--trigger automation" in prompt
    assert "codex_automation" not in prompt
    assert "runner、daily controller、verifier、finalizerを直接起動しない" in prompt

    runner = (ROOT / "scripts" / "ops" / "news-grasp-runner.ps1").read_text(
        encoding="utf-8-sig"
    )
    guard_start = runner.index("function Invoke-NewsGraspCompletionGuard")
    guard_block = runner[
        guard_start : runner.index("function New-NewsGraspFinalizationReceipt", guard_start)
    ]
    assert "$RecoveryRuntimeBinding.CompletionGuardToolPath" in guard_block
    assert "tools.news_grasp_completion_guard" not in guard_block
    assert "news-grasp-finalization-guard-v2.py" in runner

    installer = (ROOT / "scripts" / "ops" / "install-news-grasp-ops.ps1").read_text(
        encoding="utf-8-sig"
    )
    assert "common-finalization-guard-v2" in installer
    assert "$completionGuardToolPath = Join-Path $assetInstallRoot" in installer
    assert "completionGuardToolPath = $completionGuardToolPath" in installer


def test_installed_guard_preserves_public_green_when_only_readiness_is_debt(
    tmp_path: Path,
) -> None:
    evidence = {
        field: {"ok": True, "field": field}
        for field in news_grasp_operational_contract.PUBLIC_COMPLETION_FIELDS
    }
    manifest = news_grasp_finalization.build_public_manifest_v2(
        issue_date="2026-08-14",
        generation_id="generation-20260814",
        publish_commit="a" * 40,
        producer_operation_id="b" * 64,
        evidence=evidence,
    )
    result = news_grasp_finalization.finalize_common(
        repo_root=tmp_path,
        public_manifest=manifest,
        run_intent="ScheduledRecoveryFull",
        transaction_started_at="2026-08-14T06:40:00+09:00",
        public_green_at="2026-08-14T07:10:00+09:00",
        done_at="2026-08-14T07:20:00+09:00",
        readiness={"ok": False, "reason": "scheduled_task_missed_runs"},
        actual_recovery_operation_count=1,
    )
    result_path = news_grasp_finalization.common_finalization_path(
        tmp_path,
        issue_date="2026-08-14",
        generation_id="generation-20260814",
        publish_commit="a" * 40,
    )
    guard_path = ROOT / "automation" / "guards" / "news-grasp-finalization-guard-v2.py"
    spec = importlib.util.spec_from_file_location("news_grasp_installed_guard_v2", guard_path)
    assert spec is not None and spec.loader is not None
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    guarded = guard.evaluate(result_path)
    assert result["publicStatus"] == "green"
    assert result["guardOk"] is True
    assert result["exitCode"] == 2
    assert guarded["ok"] is True
    assert guarded["automationExitCode"] == 2
    assert guarded["readinessStatus"] == "red"

    tampered = json.loads(result_path.read_text(encoding="utf-8"))
    tampered["completionAuthority"]["issueDate"] = "2026-08-13"
    tampered_body = {
        key: value for key, value in tampered.items() if key != "receiptSha256"
    }
    tampered["receiptSha256"] = hashlib.sha256(
        json.dumps(
            tampered_body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    result_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValueError, match="COMPLETION_AUTHORITY_V2_INVALID"):
        guard.evaluate(result_path)


def test_watcher_and_parent_command_are_capped_by_the_shared_ninety_minute_budget() -> None:
    watcher = (ROOT / "scripts" / "ops" / "watch-news-grasp-runner.ps1").read_text(
        encoding="utf-8-sig"
    )
    bootstrap = (ROOT / "scripts" / "ops" / "news-grasp-bootstrap.ps1").read_text(
        encoding="utf-8-sig"
    )
    daily = inspect.getsource(
        __import__("tools.news_grasp_daily_control", fromlist=["_execute_audit_0640_owned"])
        ._execute_audit_0640_owned
    )
    assert "[int] $TimeoutMinutes = 90" in watcher
    assert "[int] $TimeoutMinutes = 90" in bootstrap
    assert "timeout=10800" not in daily
    assert "hardDeadline" in daily
    assert "timeout=min(90 * 60, remaining)" in daily


def test_product_spec_contains_the_approved_whole_flow_and_gate_contract() -> None:
    spec = (ROOT / "docs" / "spec.md").read_text(encoding="utf-8-sig")
    section = spec[spec.index("## 2026-08-14 日次運用全体最適化") :]
    assert "flowchart TD" in section
    assert "sequenceDiagram" in section
    assert "Canonical ensure-0640" in section
    assert "同時producer一つ、成功manifest一つ" in section
    assert "public authorityと別receipt/debt" in section
    assert "Full E2E、public incident report" in section


def test_mission_authority_v1_is_read_only_and_wrapper_v2_is_current() -> None:
    legacy_body = dict(news_grasp_mission_authority.LEGACY_EXPECTED_BODY)
    legacy = _seal(legacy_body)
    assert (
        news_grasp_mission_authority.validate_mission_authority(legacy)[
            "authorityVersion"
        ]
        == "legacy_read_only"
    )
    current = news_grasp_mission_authority.wrap_legacy_authority(legacy)
    validated = news_grasp_mission_authority.validate_mission_authority(current)
    assert validated["authorityVersion"] == "current"
    assert current["sourceAuthorityReceiptSha256"] == legacy["receiptSha256"]

    installer = (ROOT / "scripts" / "ops" / "install-news-grasp-ops.ps1").read_text(
        encoding="utf-8-sig"
    )
    assert "[string]$missionValidation.authorityVersion -ceq 'current'" in installer
    assert "$missionSchema -ne 'AUDIT_MISSION_AUTHORITY_V2'" in installer
    assert "wrap-legacy" in installer


def test_unresolved_notification_resume_never_resends(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    decision = _seal(
        {
            "schemaVersion": news_grasp_daily_control.SCHEMA,
            "issuer": news_grasp_daily_control.ISSUER,
            "issueDate": "2026-08-14",
            "action": "launch_minimal_unblocker",
            "recoveryBranch": "minimal_unblocker",
            "minimalPublicProofSha256": "a" * 64,
            "maxAutomaticRecoveryAttempts": 1,
            "noFocusTheft": True,
            "noAutoOpen": True,
            "noUserMonitoring": True,
        }
    )
    decision_path = tmp_path / "decision.json"
    decision_path.write_text(json.dumps(decision), encoding="utf-8")
    monkeypatch.setattr(
        audit_recovery_control,
        "_verify_public_without_notification",
        lambda **_: {"receiptSha256": "a" * 64},
    )
    monkeypatch.setattr(
        news_grasp_recovery_transaction,
        "begin_owned_operation",
        lambda **_: (_ for _ in ()).throw(
            ValueError("AUDIT_RECOVERY_OWNED_OPERATION_REPLAY_REJECTED")
        ),
    )
    monkeypatch.setattr(
        news_grasp_recovery_transaction,
        "resume_owned_operation",
        lambda **_: {
            "operationState": "started_unresolved",
            "receiptSha256": "b" * 64,
        },
    )
    completed: list[str] = []
    monkeypatch.setattr(
        news_grasp_recovery_transaction,
        "complete_owned_operation",
        lambda **kwargs: completed.append(str(kwargs["outcome_status"])) or {},
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("unresolved delivery must not be resent"),
    )
    with pytest.raises(
        ValueError, match="MINIMAL_UNBLOCKER_NOTIFICATION_OUTCOME_UNKNOWN"
    ):
        news_grasp_daily_control._execute_minimal_unblocker_owned(
            decision_path,
            transaction_receipt={"receiptSha256": "c" * 64},
            repo_root=tmp_path,
        )
    assert completed == ["outcome_unknown"]


def test_installer_dry_run_validates_package_without_filesystem_or_task_mutation(
    tmp_path: Path,
) -> None:
    untouched_bin = tmp_path / "not-created-bin"
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "scripts" / "ops" / "install-news-grasp-ops.ps1"),
            "-RepoDir",
            str(ROOT),
            "-BinDir",
            str(untouched_bin),
            "-DryRun",
        ],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        timeout=30,
        creationflags=(
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            if __import__("os").name == "nt"
            else 0
        ),
    )
    assert completed.returncode == 0, completed.stderr
    value = json.loads(completed.stdout.strip().splitlines()[-1])
    assert value["schemaVersion"] == "NEWS_GRASP_INSTALLER_DRY_RUN_V1"
    assert value["ok"] is True
    assert value["mutationCount"] == 0
    assert value["taskMutationCount"] == 0
    assert value["externalMutationCount"] == 0
    assert value["packageFileCount"] >= 10
    assert untouched_bin.exists() is False
