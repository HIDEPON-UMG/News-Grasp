from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from tools import audit_recovery_control
from tools.news_grasp_operational_contract import select_recovery_branch_from_truth


SCHEMA = "NEWS_GRASP_DAILY_RECOVERY_PLAN_V1"
ISSUER = "tools.news_grasp_daily_control.actual_state_controller"
RECOVERABLE_STATUSES = {
    "error",
    "failed",
    "publish_failed",
    "distribution_failed",
    "blocked_gate_timeout",
    "blocked_reporter_timeout",
    "blocked_reporter_repeated_failure",
    "operation_rejected_high_cost_admission",
    "operation_rejected_high_cost_admission_required",
    "watchdog_stale_timeout",
    "watchdog_wall_timeout",
}
INCIDENT_STATUSES = {
    "blocked_external_readiness",
    "watchdog_state_corrupt",
    "watchdog_stale_unconfirmed",
    "blocked_runner_state_corrupt",
}
RESUME_STAGES = {
    "deepdive",
    "post-daily-quality",
    "post-deepdive",
    "generation-quality-repair",
}


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sealed(body: dict[str, Any]) -> dict[str, Any]:
    value = dict(body)
    value["receiptSha256"] = _sha(value)
    return value


def _validate_date(value: str) -> str:
    if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", value) is None:
        raise ValueError("ISSUE_DATE_INVALID")
    return value


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def classify_observed_failure(
    *, runner_state: dict[str, Any], process_exit_code: int, log_text: str
) -> str:
    """未署名 caller label ではなく一次状態だけから復旧可能性を分類する。"""
    status = str(runner_state.get("status") or "").strip()
    external = runner_state.get("external_readiness")
    external_kind = (
        str(external.get("kind") or "") if isinstance(external, dict) else ""
    )
    if (
        status in INCIDENT_STATUSES
        or external_kind in {"oauth_consent_required", "youtube_quota_or_permission"}
        or "oauth consent required" in log_text.casefold()
    ):
        return "incident_required"
    if (
        status in RECOVERABLE_STATUSES
        or status.startswith("operation_rejected_high_cost")
        or status.startswith("failed_")
        or int(process_exit_code) != 0
    ):
        return "recoverable"
    return "incident_required"


def build_recovery_plan(
    *,
    issue_date: str,
    trigger: str,
    classification: str,
    branch: str,
    authority_path: Path,
    failure_receipt_sha256: str,
    operational_truth_sha256: str,
    recovery_attempt_number: int = 0,
    resume_stage: str = "",
    source_admission_path: str = "",
    source_admission_sha256: str = "",
    broker_stage_decision_path: str = "",
    broker_stage_decision_sha256: str = "",
    broker_stage_decision_receipt_sha256: str = "",
    minimal_public_proof_sha256: str = "",
) -> dict[str, Any]:
    issue_date = _validate_date(issue_date)
    common: dict[str, Any] = {
        "schemaVersion": SCHEMA,
        "issuer": ISSUER,
        "issueDate": issue_date,
        "trigger": trigger,
        "scheduledAttemptStatus": "failed",
        "recoveryAttemptStatus": "not_started",
        "publicStatus": "incomplete",
        "scheduledFailureRetained": True,
        "failureReceiptSha256": failure_receipt_sha256,
        "operationalTruthReceiptSha256": operational_truth_sha256,
        "maxAutomaticRecoveryAttempts": 1,
        "recoveryAttemptNumber": int(recovery_attempt_number),
        "noFocusTheft": True,
        "noAutoOpen": True,
        "noUserMonitoring": True,
        "completion": False,
    }
    if classification != "recoverable":
        return _sealed(
            {
                **common,
                "action": "major_incident_continuation",
                "terminal": "production_major_incident_open",
                "reasonCode": "OBSERVED_FAILURE_NOT_AUTOMATICALLY_RECOVERABLE",
            }
        )
    if int(recovery_attempt_number) >= 1:
        return _sealed(
            {
                **common,
                "action": "major_incident_continuation",
                "terminal": "production_major_incident_open",
                "reasonCode": "BOUNDED_RECOVERY_ATTEMPT_EXHAUSTED",
            }
        )
    if branch not in {"ScheduledRecoveryFull", "ResumeFromStage", "minimal_unblocker"}:
        raise ValueError("RECOVERY_BRANCH_INVALID")
    if branch == "ResumeFromStage" and (
        resume_stage not in RESUME_STAGES
        or not source_admission_path
        or not re.fullmatch(r"[0-9a-f]{64}", source_admission_sha256)
        or not broker_stage_decision_path
        or not re.fullmatch(r"[0-9a-f]{64}", broker_stage_decision_sha256)
        or not re.fullmatch(
            r"[0-9a-f]{64}", broker_stage_decision_receipt_sha256
        )
    ):
        branch = "ScheduledRecoveryFull"
        resume_stage = ""
        source_admission_path = ""
        source_admission_sha256 = ""
        broker_stage_decision_path = ""
        broker_stage_decision_sha256 = ""
        broker_stage_decision_receipt_sha256 = ""
    action = "launch_minimal_unblocker" if branch == "minimal_unblocker" else "launch_recovery"
    return _sealed(
        {
            **common,
            "action": action,
            "terminal": None,
            "reasonCode": "TYPED_RECOVERY_AUTHORITY_READY",
            "runIntent": "ScheduledRecoveryFull",
            "recoveryBranch": branch,
            "resumeStage": resume_stage or None,
            "sourceAdmissionPath": source_admission_path or None,
            "sourceAdmissionSha256": source_admission_sha256 or None,
            "brokerStageDecisionPath": broker_stage_decision_path or None,
            "brokerStageDecisionSha256": broker_stage_decision_sha256 or None,
            "brokerStageDecisionReceiptSha256": (
                broker_stage_decision_receipt_sha256 or None
            ),
            "minimalPublicProofSha256": minimal_public_proof_sha256 or None,
            "scheduledAuthorityEvidencePath": str(authority_path.resolve()),
        }
    )


class ProductionBackend:
    def __init__(self, *, repo_root: Path | None = None) -> None:
        self.repo_root = (repo_root or Path(__file__).resolve().parents[1]).resolve()
        self.bin_dir = Path.home() / "bin"
        self.state_path = self.bin_dir / "news-grasp-runner-state.json"
        self.log_dir = self.bin_dir / "news-grasp-logs"
        self.runner_path = self.bin_dir / "news-grasp-runner.ps1"
        self.broker_path = self.bin_dir / "ai-model-spawn-broker.py"
        self.authority_dir = self.bin_dir / "news-grasp-authority"
        self.mission_path = self.authority_dir / "audit-mission-authority-v1.json"

    def _run_broker(self, *args: str) -> dict[str, Any]:
        if not self.broker_path.is_file():
            raise ValueError("RECOVERY_AUTHORITY_BROKER_UNAVAILABLE")
        completed = subprocess.run(
            [sys.executable, str(self.broker_path), *args],
            cwd=self.repo_root,
            capture_output=True,
            check=False,
            timeout=30,
            creationflags=(
                getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            ),
        )
        if completed.returncode != 0:
            raise ValueError(
                (completed.stderr or completed.stdout).decode("utf-8", errors="replace").strip()
                or "BROKER_OPERATION_FAILED"
            )
        try:
            value = json.loads(completed.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("BROKER_OUTPUT_INVALID") from error
        if not isinstance(value, dict):
            raise ValueError("BROKER_OUTPUT_INVALID")
        return value

    def load_state(self, issue_date: str) -> dict[str, Any]:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("RUNNER_STATE_EVIDENCE_INVALID") from error
        if not isinstance(value, dict) or value.get("date") != issue_date:
            raise ValueError("RUNNER_STATE_EVIDENCE_INVALID")
        return value

    def log_text(self, issue_date: str) -> str:
        path = self.log_dir / f"{issue_date}.log"
        try:
            return path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            return ""

    def task_action_sha256(self) -> str:
        script = (
            "$t=Get-ScheduledTask -TaskName 'News-Grasp Production' -ErrorAction Stop;"
            "$s=(@($t.Actions)|%{([string]$_.Execute+' '+[string]$_.Arguments).Trim()}) -join ' ; ';"
            "$b=[Text.Encoding]::UTF8.GetBytes($s.Trim().ToLowerInvariant());"
            "$h=[Security.Cryptography.SHA256]::Create();"
            "try{([BitConverter]::ToString($h.ComputeHash($b))-replace '-','').ToLowerInvariant()}finally{$h.Dispose()}"
        )
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            check=False,
            timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        value = completed.stdout.decode("utf-8", errors="replace").strip()
        if completed.returncode != 0 or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError("TASK_ACTION_EVIDENCE_INVALID")
        return value

    def inspect_attempt(self, issue_date: str) -> dict[str, Any]:
        return self._run_broker("inspect-news-grasp-attempt", "--issue-date", issue_date)

    def reconcile_task_history(self, issue_date: str) -> dict[str, Any]:
        return self._run_broker(
            "reconcile-news-grasp-scheduled-pre-admission-failure",
            "--issue-date",
            issue_date,
        )

    def record_pre_admission_process_failure(
        self, *, issue_date: str, process_exit_code: int
    ) -> tuple[dict[str, Any], Path]:
        state = self.load_state(issue_date)
        log_path = self.log_dir / f"{issue_date}.log"
        permit_path = self.authority_dir / f"{issue_date}-launch-permit.json"
        if not log_path.is_file() or not permit_path.is_file():
            raise ValueError("SCHEDULED_PRE_ADMISSION_PRIMARY_EVIDENCE_MISSING")
        failure = self._run_broker(
            "record-news-grasp-pre-admission-process-failure",
            "--issue-date",
            issue_date,
            "--launch-permit",
            str(permit_path),
            "--runner-state-file",
            str(self.state_path),
            "--runner-log",
            str(log_path),
            "--process-exit-code",
            str(int(state.get("exit_code") or process_exit_code)),
        )
        path = self.repo_root / "build" / "recovery" / "authority" / f"{issue_date}-scheduled-failure.json"
        _atomic_json(path, failure)
        return failure, path

    def record_failure(
        self, *, issue_date: str, state: dict[str, Any], process_exit_code: int
    ) -> tuple[dict[str, Any], Path]:
        log_path = self.log_dir / f"{issue_date}.log"
        if not self.state_path.is_file() or not log_path.is_file() or not self.runner_path.is_file():
            raise ValueError("SCHEDULED_FAILURE_PRIMARY_EVIDENCE_MISSING")
        exit_code = int(state.get("exit_code") or process_exit_code)
        if exit_code == 0:
            raise ValueError("SCHEDULED_FAILURE_PRIMARY_EVIDENCE_INVALID")
        failure = self._run_broker(
            "record-news-grasp-failure",
            "--issue-date",
            issue_date,
            "--last-task-result",
            str(exit_code),
            "--runner-state",
            str(state.get("status") or "unknown"),
            "--state-sha256",
            _file_sha(self.state_path),
            "--log-sha256",
            _file_sha(log_path),
            "--task-action-sha256",
            self.task_action_sha256(),
            "--runner-sha256",
            _file_sha(self.runner_path),
            "--failure-stage",
            re.sub(r"[^a-z0-9_:-]", "_", str(state.get("phase") or state.get("status") or "unknown").casefold())[:64],
        )
        path = self.repo_root / "build" / "recovery" / "authority" / f"{issue_date}-scheduled-failure.json"
        _atomic_json(path, failure)
        return failure, path

    def derive_authority(
        self, *, issue_date: str, failure_path: Path
    ) -> tuple[dict[str, Any], Path]:
        authority = self._run_broker(
            "derive-news-grasp-recovery-authority",
            "--issue-date",
            issue_date,
            "--mission-authority",
            str(self.mission_path),
            "--failure-receipt",
            str(failure_path),
            "--run-intent",
            "ScheduledRecoveryFull",
            "--current-task-action-sha256",
            self.task_action_sha256(),
            "--current-runner-sha256",
            _file_sha(self.runner_path),
        )
        path = self.repo_root / "build" / "recovery" / "authority" / f"{issue_date}-recovery-authority.json"
        _atomic_json(path, authority)
        return authority, path

    def issue_stage_decision(
        self,
        *,
        issue_date: str,
        failure_path: Path,
        operational_truth_path: Path,
        source_production_admission_path: Path,
        recovery_authority_path: Path,
        resume_stage: str,
    ) -> tuple[dict[str, Any], Path]:
        decision = self._run_broker(
            "issue-news-grasp-recovery-stage-decision",
            "--issue-date",
            issue_date,
            "--failure-receipt",
            str(failure_path),
            "--operational-truth",
            str(operational_truth_path),
            "--source-production-admission",
            str(source_production_admission_path),
            "--recovery-authority",
            str(recovery_authority_path),
            "--resume-stage",
            resume_stage,
        )
        path = (
            self.repo_root
            / "build"
            / "recovery"
            / "authority"
            / f"{issue_date}-stage-decision.json"
        )
        _atomic_json(path, decision)
        return decision, path


def prepare_recovery(
    *,
    issue_date: str,
    trigger: str,
    process_exit_code: int,
    recovery_attempt_number: int = 0,
    backend: ProductionBackend | None = None,
) -> dict[str, Any]:
    issue_date = _validate_date(issue_date)
    if trigger not in {"production_failure", "audit_0640"}:
        raise ValueError("RECOVERY_TRIGGER_INVALID")
    actual = backend or ProductionBackend()
    state = actual.load_state(issue_date)
    log_text = actual.log_text(issue_date)
    pre_admission_failure: dict[str, Any] | None = None
    pre_admission_failure_path: Path | None = None
    try:
        witness = actual.inspect_attempt(issue_date)
    except ValueError:
        if trigger == "production_failure":
            pre_admission_failure, pre_admission_failure_path = (
                actual.record_pre_admission_process_failure(
                    issue_date=issue_date,
                    process_exit_code=process_exit_code,
                )
            )
        else:
            actual.reconcile_task_history(issue_date)
        witness = actual.inspect_attempt(issue_date)
    if trigger == "audit_0640" and str(state.get("status") or "") == "publish_complete":
        expected_intent = (
            "ScheduledRecoveryFull"
            if witness.get("recoveryAttemptStatus") == "started"
            else "ScheduledProduction"
        )
        try:
            completion = audit_recovery_control._verify_same_date_completion(
                issue_date=issue_date,
                payload={"verificationWaitSec": 0, "verificationPollSec": 10},
                expected_run_intent=expected_intent,
            )
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
            completion = None
        if audit_recovery_control.same_date_completion_green(issue_date, completion):
            plan = _sealed(
                {
                    "schemaVersion": SCHEMA,
                    "issuer": ISSUER,
                    "issueDate": issue_date,
                    "trigger": trigger,
                    "action": "none",
                    "terminal": "audit_same_date_public_green",
                    "scheduledAttemptStatus": (
                        "failed" if witness.get("scheduledAttemptStatus") == "failed" else "succeeded"
                    ),
                    "recoveryAttemptStatus": (
                        "succeeded" if witness.get("recoveryAttemptStatus") == "started" else "not_started"
                    ),
                    "publicStatus": "green",
                    "completion": True,
                    "completionEvidenceSha256": completion["receiptSha256"],
                    "completionEvidence": completion,
                    "maxAutomaticRecoveryAttempts": 1,
                    "noFocusTheft": True,
                    "noAutoOpen": True,
                    "noUserMonitoring": True,
                }
            )
            output = actual.repo_root / "build" / "recovery" / "control" / f"{issue_date}-{trigger}.json"
            _atomic_json(output, plan)
            return {**plan, "decisionPath": str(output.resolve())}
    classification = classify_observed_failure(
        runner_state=state,
        process_exit_code=process_exit_code,
        log_text=log_text,
    )
    if trigger == "audit_0640" and classification != "incident_required":
        classification = "recoverable"
    elif trigger == "audit_0640" and str(state.get("status") or "") == "publish_complete":
        classification = "recoverable"
    failure_path = actual.repo_root / "build" / "recovery" / "authority" / f"{issue_date}-scheduled-failure.json"
    if pre_admission_failure is not None and pre_admission_failure_path is not None:
        failure = pre_admission_failure
        failure_path = pre_admission_failure_path
    elif witness.get("scheduledAttemptStatus") == "reserved":
        failure, failure_path = actual.record_failure(
            issue_date=issue_date,
            state=state,
            process_exit_code=(
                process_exit_code
                if int(state.get("exit_code") or process_exit_code) != 0
                else 1
            ),
        )
        witness = actual.inspect_attempt(issue_date)
    else:
        try:
            failure = json.loads(failure_path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("SCHEDULED_FAILURE_RECEIPT_MISSING") from error
    if witness.get("scheduledAttemptStatus") != "failed" or (
        witness.get("failureReceiptSha256") != failure.get("receiptSha256")
    ):
        raise ValueError("SCHEDULED_FAILURE_LEDGER_MISMATCH")
    if witness.get("recoveryAttemptStatus") == "started":
        recovery_attempt_number = max(1, int(recovery_attempt_number))
    _, authority_path = actual.derive_authority(
        issue_date=issue_date, failure_path=failure_path
    )
    audit_decision = audit_recovery_control.decide_audit_recovery(
        {
            "issueDate": issue_date,
            "scheduledFailureReceiptPath": str(failure_path),
            "recoveryAuthorityPath": str(authority_path),
            "humanImpact": {
                "noFocusTheft": True,
                "noUserMonitoring": True,
                "noAutoOpen": True,
            },
        }
    )
    if audit_decision.get("action") != "scheduled_recovery":
        classification = "incident_required"
    truth = audit_recovery_control._observe_operational_truth(
        issue_date=issue_date, attempt_witness=witness
    )
    branch = select_recovery_branch_from_truth(truth)
    resume_stage = str(truth.get("resumeStage") or "")
    source_admission_path = str(state.get("highCostAdmissionPath") or "")
    broker_stage_decision_path = ""
    broker_stage_decision_sha256 = ""
    broker_stage_decision_receipt_sha256 = ""
    if branch == "ResumeFromStage":
        truth_path = (
            actual.repo_root
            / "build"
            / "recovery"
            / "evidence"
            / f"{issue_date}-operational-truth.json"
        )
        _atomic_json(truth_path, truth)
        source_path = Path(source_admission_path)
        if not source_path.is_file():
            branch = "ScheduledRecoveryFull"
            resume_stage = ""
            source_admission_path = ""
        else:
            stage_decision, stage_decision_path = actual.issue_stage_decision(
                issue_date=issue_date,
                failure_path=failure_path,
                operational_truth_path=truth_path,
                source_production_admission_path=source_path,
                recovery_authority_path=authority_path,
                resume_stage=resume_stage,
            )
            broker_stage_decision_path = str(stage_decision_path.resolve())
            broker_stage_decision_sha256 = _file_sha(stage_decision_path)
            broker_stage_decision_receipt_sha256 = str(
                stage_decision["receiptSha256"]
            )
    plan = build_recovery_plan(
        issue_date=issue_date,
        trigger=trigger,
        classification=classification,
        branch=branch,
        authority_path=authority_path,
        failure_receipt_sha256=str(failure["receiptSha256"]),
        operational_truth_sha256=str(truth["receiptSha256"]),
        recovery_attempt_number=recovery_attempt_number,
        resume_stage=resume_stage,
        source_admission_path=source_admission_path,
        source_admission_sha256=str(truth.get("sourceAdmissionSha256") or ""),
        broker_stage_decision_path=broker_stage_decision_path,
        broker_stage_decision_sha256=broker_stage_decision_sha256,
        broker_stage_decision_receipt_sha256=(
            broker_stage_decision_receipt_sha256
        ),
        minimal_public_proof_sha256=str(
            truth.get("minimalUnblockerPublicProofSha256") or ""
        ),
    )
    output = actual.repo_root / "build" / "recovery" / "control" / f"{issue_date}-{trigger}.json"
    _atomic_json(output, plan)
    return {**plan, "decisionPath": str(output.resolve())}


def validate_decision(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("RECOVERY_DECISION_INVALID") from error
    if not isinstance(value, dict):
        raise ValueError("RECOVERY_DECISION_INVALID")
    receipt = value.get("receiptSha256")
    body = {key: item for key, item in value.items() if key != "receiptSha256"}
    if (
        value.get("schemaVersion") != SCHEMA
        or value.get("issuer") != ISSUER
        or receipt != _sha(body)
        or value.get("maxAutomaticRecoveryAttempts") != 1
        or value.get("noFocusTheft") is not True
        or value.get("noAutoOpen") is not True
        or value.get("noUserMonitoring") is not True
    ):
        raise ValueError("RECOVERY_DECISION_INVALID")
    return {**value, "decisionPath": str(path.resolve())}


def execute_minimal_unblocker(path: Path) -> dict[str, Any]:
    decision = validate_decision(path)
    if (
        decision.get("action") != "launch_minimal_unblocker"
        or decision.get("recoveryBranch") != "minimal_unblocker"
        or re.fullmatch(
            r"[0-9a-f]{64}", str(decision.get("minimalPublicProofSha256") or "")
        )
        is None
    ):
        raise ValueError("MINIMAL_UNBLOCKER_DECISION_INVALID")
    issue_date = _validate_date(str(decision.get("issueDate") or ""))
    proof = audit_recovery_control._verify_public_without_notification(
        issue_date=issue_date
    )
    if (
        proof is None
        or proof.get("receiptSha256") != decision["minimalPublicProofSha256"]
    ):
        raise ValueError("MINIMAL_UNBLOCKER_PUBLIC_PROOF_DRIFT")
    repo_root = Path(__file__).resolve().parents[1]
    notification = repo_root / "build" / "notification" / f"{issue_date}.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(repo_root / "tools" / "send_push.py"),
            "--record-state",
            str(notification),
        ],
        cwd=repo_root,
        capture_output=True,
        check=False,
        timeout=120,
        creationflags=(
            getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        ),
    )
    if completed.returncode != 0 or not notification.is_file():
        raise ValueError("MINIMAL_UNBLOCKER_NOTIFICATION_FAILED")
    return _sealed(
        {
            "schemaVersion": "NEWS_GRASP_MINIMAL_UNBLOCKER_RESULT_V1",
            "issuer": ISSUER,
            "issueDate": issue_date,
            "decisionReceiptSha256": decision["receiptSha256"],
            "notificationStateSha256": _file_sha(notification),
            "scheduledAttemptStatus": "failed",
            "recoveryAttemptStatus": "succeeded",
            "publicStatus": "pending_same_date_completion_reverification",
            "completion": False,
        }
    )


def _audit_incident_terminal(
    *, issue_date: str, reason_code: str, decision: dict[str, Any] | None = None
) -> dict[str, Any]:
    evidence = str((decision or {}).get("receiptSha256") or "")
    if re.fullmatch(r"[0-9a-f]{64}", evidence) is None:
        evidence = _sha({"issueDate": issue_date, "reasonCode": reason_code})
    return audit_recovery_control.seal_audit_decision(
        {
            "schemaVersion": "AUDIT_RECOVERY_DECISION_V1",
            "issueDate": issue_date,
            "classification": "incident_required",
            "action": "escalate_major_incident",
            "terminal": "audit_major_incident_open",
            "reasonCode": reason_code,
            "scheduledAttemptStatus": str(
                (decision or {}).get("scheduledAttemptStatus") or "unverified"
            ),
            "recoveryAttemptStatus": str(
                (decision or {}).get("recoveryAttemptStatus") or "unverified"
            ),
            "publicStatus": "incomplete",
            "operationState": "incident_open",
            "workPriority": audit_recovery_control.SAME_DAY_PUBLIC_RECOVERY_PRIORITY,
            "allowedBeforePublicGreen": audit_recovery_control.ALLOWED_BEFORE_PUBLIC_GREEN,
            "forbiddenBeforePublicGreen": audit_recovery_control.FORBIDDEN_BEFORE_PUBLIC_GREEN,
            "owner": "News-Grasp Operations",
            "nextAction": "resume_same_date_recovery_from_verified_stop_point",
            "evidenceSha256": evidence,
            "sourceDecision": decision,
            "completionEvidence": None,
        }
    )


def _audit_green_terminal(
    *,
    issue_date: str,
    decision: dict[str, Any],
    completion: dict[str, Any],
    recovered: bool,
) -> dict[str, Any]:
    return audit_recovery_control.seal_audit_decision(
        {
            "schemaVersion": "AUDIT_RECOVERY_DECISION_V1",
            "issueDate": issue_date,
            "classification": "green",
            "action": "none",
            "terminal": "audit_recovered_green" if recovered else "audit_normal_green",
            "reasonCode": (
                "SAME_DATE_RECOVERY_COMPLETION_GREEN"
                if recovered
                else "SAME_DATE_SCHEDULED_COMPLETION_GREEN"
            ),
            "scheduledAttemptStatus": str(
                decision.get("scheduledAttemptStatus") or "unverified"
            ),
            "recoveryAttemptStatus": "succeeded" if recovered else "not_started",
            "publicStatus": "green",
            "operationState": "complete",
            "workPriority": audit_recovery_control.PUBLIC_GREEN_FOLLOWUP_PRIORITY,
            "completionEvidenceSha256": completion["receiptSha256"],
            "sourceDecision": decision,
            "completionEvidence": completion,
        }
    )


def execute_audit_0640(
    *,
    issue_date: str,
    backend: ProductionBackend | None = None,
    command_runner: Any | None = None,
    minimal_executor: Any | None = None,
    completion_verifier: Any | None = None,
    terminal_writer: Any | None = None,
) -> dict[str, Any]:
    """stop-point判断、選択branch、same-date再検証、typed terminalを単一路で閉じる。"""
    issue_date = _validate_date(issue_date)
    actual = backend or ProductionBackend()
    write_terminal = terminal_writer or audit_recovery_control.write_audit_terminal
    verify_completion = completion_verifier or (
        lambda value, intent: audit_recovery_control._verify_same_date_completion(
            issue_date=value,
            payload={"verificationWaitSec": 300, "verificationPollSec": 10},
            expected_run_intent=intent,
        )
    )
    execute_minimal = minimal_executor or execute_minimal_unblocker
    run_command = command_runner or (
        lambda command, **kwargs: subprocess.run(
            command,
            **kwargs,
            capture_output=True,
            check=False,
            timeout=10800,
            creationflags=(
                getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            ),
        ).returncode
    )
    decision: dict[str, Any] | None = None
    try:
        decision = prepare_recovery(
            issue_date=issue_date,
            trigger="audit_0640",
            process_exit_code=1,
            backend=actual,
        )
        action = str(decision.get("action") or "")
        if action == "none" and decision.get("completion") is True:
            completion = decision.get("completionEvidence")
            if (
                not audit_recovery_control.same_date_completion_green(
                    issue_date, completion
                )
                or not isinstance(completion, dict)
                or completion.get("receiptSha256")
                != decision.get("completionEvidenceSha256")
            ):
                raise ValueError("SAME_DATE_COMPLETION_EVIDENCE_INVALID")
            terminal = _audit_green_terminal(
                issue_date=issue_date,
                decision=decision,
                completion=completion,
                recovered=False,
            )
            write_terminal(terminal)
            return terminal
        if action == "major_incident_continuation":
            terminal = _audit_incident_terminal(
                issue_date=issue_date,
                reason_code=str(decision.get("reasonCode") or "RECOVERY_NOT_AVAILABLE"),
                decision=decision,
            )
            write_terminal(terminal)
            return terminal

        expected_intent = "ScheduledProduction"
        if action == "launch_minimal_unblocker":
            execute_minimal(Path(str(decision["decisionPath"])))
        elif action == "launch_recovery":
            command = [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(actual.runner_path),
                "-RunIntent",
                "ScheduledRecoveryFull",
                "-DateStampOverride",
                issue_date,
                "-RepoDirOverride",
                str(actual.repo_root),
                "-HighCostBudgetToolPath",
                str(actual.broker_path),
                "-ScheduledAuthorityEvidencePath",
                str(decision["scheduledAuthorityEvidencePath"]),
                "-RecoveryDecisionPath",
                str(decision["decisionPath"]),
            ]
            if decision.get("recoveryBranch") == "ResumeFromStage":
                command.extend(
                    [
                        "-ResumeFromStage",
                        str(decision["resumeStage"]),
                        "-HighCostAdmissionPath",
                        str(decision["sourceAdmissionPath"]),
                    ]
                )
            return_code = int(run_command(command, cwd=actual.repo_root))
            if return_code != 0:
                terminal = _audit_incident_terminal(
                    issue_date=issue_date,
                    reason_code=f"RECOVERY_EXECUTION_FAILED_{return_code}",
                    decision=decision,
                )
                write_terminal(terminal)
                return terminal
            expected_intent = "ScheduledRecoveryFull"
        else:
            terminal = _audit_incident_terminal(
                issue_date=issue_date,
                reason_code="AUDIT_RECOVERY_ACTION_INVALID",
                decision=decision,
            )
            write_terminal(terminal)
            return terminal

        completion = verify_completion(issue_date, expected_intent)
        if not audit_recovery_control.same_date_completion_green(issue_date, completion):
            terminal = _audit_incident_terminal(
                issue_date=issue_date,
                reason_code="RECOVERY_COMPLETION_INVALID",
                decision=decision,
            )
            write_terminal(terminal)
            return terminal
        terminal = _audit_green_terminal(
            issue_date=issue_date,
            decision=decision,
            completion=completion,
            recovered=True,
        )
        write_terminal(terminal)
        return terminal
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        terminal = _audit_incident_terminal(
            issue_date=issue_date,
            reason_code="AUDIT_EXECUTOR_FAILED_" + type(error).__name__.upper(),
            decision=decision,
        )
        write_terminal(terminal)
        return terminal


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--issue-date", required=True)
    prepare.add_argument(
        "--trigger", choices=("production_failure", "audit_0640"), required=True
    )
    prepare.add_argument("--process-exit-code", type=int, default=1)
    prepare.add_argument("--recovery-attempt-number", type=int, default=0)
    validate = sub.add_parser("validate-decision")
    validate.add_argument("--path", type=Path, required=True)
    minimal = sub.add_parser("execute-minimal-unblocker")
    minimal.add_argument("--path", type=Path, required=True)
    execute_audit = sub.add_parser("execute-audit-0640")
    execute_audit.add_argument("--issue-date", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare_recovery(
                issue_date=args.issue_date,
                trigger=args.trigger,
                process_exit_code=args.process_exit_code,
                recovery_attempt_number=args.recovery_attempt_number,
            )
        elif args.command == "validate-decision":
            result = validate_decision(args.path)
        elif args.command == "execute-minimal-unblocker":
            result = execute_minimal_unblocker(args.path)
        else:
            result = execute_audit_0640(issue_date=args.issue_date)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 2 if result.get("terminal") == "audit_major_incident_open" else 0


if __name__ == "__main__":
    raise SystemExit(main())
