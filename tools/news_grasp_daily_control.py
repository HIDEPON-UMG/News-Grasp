from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from tools import audit_recovery_control
from tools import news_grasp_external_control as external_control
from tools import news_grasp_convergence as convergence
from tools import operational_recovery_registry
from tools.news_grasp_operational_contract import (
    evaluate_completion_v3,
    seal_fresh_broker_admission,
    select_recovery_branch_from_truth,
    validate_scheduled_admission_receipt,
)
from tools.news_grasp_owned_process import OwnedProcessError, run_owned_bounded


OWNED_COMMAND_MAX_OUTPUT_BYTES = 4 * 1024 * 1024


def _run_owned_command(
    command: list[str], *, cwd: Path, timeout_seconds: int | float
) -> int:
    """既定の外部commandを生成時からJob Objectへ所属させて実行する。"""

    if timeout_seconds <= 0:
        return 78
    try:
        result = run_owned_bounded(
            command,
            cwd=cwd,
            timeout=timeout_seconds,
            max_output_bytes=OWNED_COMMAND_MAX_OUTPUT_BYTES,
        )
    except OwnedProcessError:
        return 126
    if result.timed_out:
        return 124
    if result.output_exceeded:
        return 125
    return int(result.returncode)


def _recovery_remaining_seconds(issue_date: str) -> int:
    """固定06:40 anchorのhard deadlineまでの残時間を返す。"""

    from tools.news_grasp_recovery_transaction import audit_deadlines

    hard_deadline = datetime.fromisoformat(audit_deadlines(issue_date)["hardDeadlineAt"])
    return max(0, int((hard_deadline - datetime.now().astimezone()).total_seconds()))


SCHEMA = "NEWS_GRASP_DAILY_RECOVERY_PLAN_V1"
COMPLETION_STATE_VECTOR_V3 = "COMPLETION_STATE_VECTOR_V3"
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
    "post-reporter",
    "editor",
}


def build_completion_state_vector_v3(**states: Any) -> dict[str, Any]:
    """daily/audit consumerからV3 state vectorを一つの実装へ束縛する。"""

    result = evaluate_completion_v3(**states)
    if result.get("schemaVersion") != COMPLETION_STATE_VECTOR_V3:
        raise ValueError("COMPLETION_STATE_VECTOR_SCHEMA_DRIFT")
    return result


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


def _local_startup_unblocker_rejection(state: Mapping[str, Any]) -> bool:
    status = str(state.get("status") or "")
    message = str(state.get("message") or "")
    if status in {
        "operation_rejected_high_cost_admission",
        "operation_rejected_high_cost_admission_required",
    }:
        return True
    if status == "blocked_recovery_model_budget" and str(state.get("phase") or "") == "reporter":
        return True
    if status == "blocked_startup_self_repair_failed":
        return True
    return status == "error" and "ARTIFACT_EXECUTABLE_TREE_INVALID" in message


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
        status
        in {
            "failed_shared_broker_generation_drift",
            "external_control_plane_unavailable",
            "blocked_codex_auth",
        }
        or external_kind in {"external_control_plane_unavailable", "shared_broker_generation_drift"}
        or "NEWS_GRASP_SHARED_BROKER_GENERATION_DRIFT" in log_text
    ):
        return "external_control_plane_unavailable"
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


def validate_external_readiness_input(
    payload: dict[str, Any], *, canonical_root: Path | str
) -> dict[str, Any]:
    """product entryからexternal readinessをtyped validatorへ渡す。"""
    try:
        return external_control.validate_external_readiness_input(
            payload, canonical_root=canonical_root
        )
    except external_control.ExternalControlPlaneError as error:
        raise ValueError(str(error)) from error


def probe_external_control_plane() -> dict[str, Any]:
    """固定global authorityだけを読むpure readiness probe。"""
    return external_control.probe_external_readiness()


def decide_operational_convergence(
    *, state: dict[str, Any], previous_event: dict[str, Any] | None
) -> dict[str, Any]:
    return convergence.decide_operational_convergence(
        state=state, previous_event=previous_event
    )


def record_convergence_event(**kwargs: Any) -> dict[str, Any]:
    return convergence.record_convergence_event(**kwargs)


def reverify_convergence(**kwargs: Any) -> dict[str, Any]:
    return convergence.reverify_convergence(**kwargs)


def accept_external_authority(
    *, authority: dict[str, Any], state_path: Path | str
) -> dict[str, Any]:
    """global authorityをproduct-local acceptance ledgerへ一度だけ受理する。"""
    try:
        return external_control.accept_external_authority(
            authority=authority, state_path=state_path
        )
    except external_control.ExternalControlPlaneError as error:
        if str(error) == "EXTERNAL_AUTHORITY_REPLAY":
            return {"accepted": False, "reasonCode": str(error)}
        raise ValueError(str(error)) from error


def build_run_generation_binding(
    *,
    readiness: dict[str, Any],
    product_generation_id: str,
    issue_date: str,
    daily_operation_lineage_id: str,
    checkpoint_id: str,
    runtime_input_manifest_sha256: str = "",
) -> dict[str, Any]:
    try:
        return external_control.build_run_generation_binding(
            readiness=readiness,
            product_generation_id=product_generation_id,
            issue_date=issue_date,
            daily_operation_lineage_id=daily_operation_lineage_id,
            checkpoint_id=checkpoint_id,
            runtime_input_manifest_sha256=runtime_input_manifest_sha256,
        )
    except external_control.ExternalControlPlaneError as error:
        raise ValueError(str(error)) from error


def validate_model_invocation_outcome(
    *,
    return_code: int,
    stdout: str | bytes,
    expected_schema: str,
    stderr: str | bytes = "",
) -> dict[str, Any]:
    return external_control.validate_model_invocation_outcome(
        return_code=return_code,
        stdout=stdout,
        expected_schema=expected_schema,
        stderr=stderr,
    )


def external_reentry_decision(
    *,
    previous_authority_generation: int,
    current_authority_generation: int,
    previous_lineage: str,
    current_lineage: str,
    checkpoint_id: str,
    issue_date: str,
    daily_operation_lineage_id: str,
) -> dict[str, Any]:
    try:
        return external_control.external_reentry_decision(
            previous_authority_generation=previous_authority_generation,
            current_authority_generation=current_authority_generation,
            previous_lineage=previous_lineage,
            current_lineage=current_lineage,
            checkpoint_id=checkpoint_id,
            issue_date=issue_date,
            daily_operation_lineage_id=daily_operation_lineage_id,
        )
    except external_control.ExternalControlPlaneError as error:
        raise ValueError(str(error)) from error


def select_audit_recovery_action(completion: object) -> dict[str, Any]:
    """public Green を保持したまま readiness だけを選択的に修復する。"""
    value = completion if isinstance(completion, dict) else {}
    public_status = str(value.get("publicCompletionStatus") or "")
    readiness_status = str(value.get("nextRunReadinessStatus") or "")
    authority_id = str(value.get("completionAuthorityId") or "")
    if str(value.get("verificationStatus") or "") == "verification_unavailable":
        return {
            "action": "audit_observation_unverified",
            "terminal": "audit_observation_unverified",
            "publicStatus": "green" if authority_id else "unverified",
            "publicRecoveryStarted": False,
            "recoveryStarted": False,
            "exitCode": 2,
            "completionAuthorityId": authority_id,
            "reasonCode": str(value.get("reasonCode") or "VERIFICATION_UNAVAILABLE"),
        }
    if public_status == "green" and readiness_status == "red":
        return {
            "action": "readiness_repair",
            "recoveryScope": "next_run_readiness",
            "publicStatus": "green",
            "publicRecoveryStarted": False,
            "completionAuthorityId": authority_id,
            "reasonCode": str(value.get("reasonCode") or "READINESS_RED"),
        }
    if public_status == "green" and readiness_status in {"green", "unverified"}:
        return {
            "action": "none",
            "recoveryScope": "none",
            "publicStatus": "green",
            "publicRecoveryStarted": False,
            "completionAuthorityId": authority_id,
        }
    return {
        "action": "public_recovery",
        "recoveryScope": "public_completion",
        "publicStatus": "incomplete",
        "publicRecoveryStarted": True,
        "completionAuthorityId": authority_id,
        "reasonCode": str(value.get("reasonCode") or "PUBLIC_COMPLETION_RED"),
    }


def dispatch_registered_readiness_repair(
    *,
    repo_root: Path | str,
    reason_code: str,
    context: Mapping[str, Any],
    executor: Callable[[Mapping[str, Any]], Mapping[str, Any]],
) -> dict[str, Any]:
    """readiness repairをexact registry entry経由だけで実行する。"""

    handlers = operational_recovery_registry.default_handlers()
    handlers["active_generation_reconcile"] = executor
    dispatched = operational_recovery_registry.dispatch(
        repo_root=repo_root,
        reason_code=reason_code,
        context={**dict(context), "reasonCode": reason_code},
        handlers=handlers,
    )
    if dispatched.handler_id != "active_generation_reconcile":
        raise ValueError("READINESS_REPAIR_HANDLER_NOT_REGISTERED")
    result = dict(dispatched.result)
    if result.get("selfDeclaredGreen") is True:
        raise ValueError("READINESS_REPAIR_SELF_DECLARED_GREEN")
    return {
        "schemaVersion": "REGISTERED_READINESS_REPAIR_RESULT_V1",
        "status": dispatched.status,
        "handlerId": dispatched.handler_id,
        "reasonCode": dispatched.reason_code,
        "handlerResult": result,
    }


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
    external_generation_fingerprint: str = "",
    last_external_authority_generation: int = 0,
    last_external_authority_receipt_sha256: str = "",
    checkpoint_id: str = "",
    daily_operation_lineage_id: str = "",
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
    if classification == "external_control_plane_unavailable":
        deferred = external_control.build_external_dependency_deferred(
            issue_date=issue_date,
            daily_operation_lineage_id=daily_operation_lineage_id
            or f"News-Grasp|{issue_date}|scheduled",
            checkpoint_id=checkpoint_id,
            external_generation_fingerprint=external_generation_fingerprint
            or ("0" * 64),
            last_authority_generation=last_external_authority_generation,
            last_authority_receipt_sha256=last_external_authority_receipt_sha256
            or ("0" * 64),
            blocked_stage=resume_stage or "external_control_plane",
        )
        return _sealed(
            {
                **common,
                **deferred,
                "action": "defer_external_control_plane",
                "terminal": "operation_deferred_external_dependency",
                "reasonCode": "EXTERNAL_CONTROL_PLANE_UNAVAILABLE",
                "recoveryAttemptStatus": "deferred",
                "publicStatus": "unchanged",
                "modelLaunchCount": 0,
            }
        )
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
    if branch == "major_incident_fail_closed":
        return _sealed(
            {
                **common,
                "action": "major_incident_continuation",
                "terminal": "production_major_incident_open",
                "reasonCode": "RECOVERY_CHECKPOINT_REQUIRED_FOR_ARTIFACT_DELTA",
                "recoveryBranch": branch,
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
        return _sealed(
            {
                **common,
                "action": "major_incident_continuation",
                "terminal": "production_major_incident_open",
                "reasonCode": "RECOVERY_RESUME_EVIDENCE_INVALID",
                "recoveryBranch": "ResumeFromStage",
                "resumeStage": resume_stage or None,
            }
        )
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
    def __init__(
        self,
        *,
        repo_root: Path | None = None,
        evidence_root: Path | None = None,
    ) -> None:
        self.repo_root = (repo_root or Path(__file__).resolve().parents[1]).resolve()
        configured_evidence = evidence_root or (
            Path(os.environ["NEWS_GRASP_EVIDENCE_REPO_DIR"])
            if os.environ.get("NEWS_GRASP_EVIDENCE_REPO_DIR")
            else None
        )
        self.evidence_root = configured_evidence.resolve() if configured_evidence else None
        self.bin_dir = Path.home() / "bin"
        self.state_path = self.bin_dir / "news-grasp-runner-state.json"
        self.log_dir = self.bin_dir / "news-grasp-logs"
        self.runner_path = self.bin_dir / "news-grasp-runner.ps1"
        self.authority_dir = self.bin_dir / "news-grasp-authority"
        self.mission_path = self.authority_dir / "audit-mission-authority-v1.json"

    def probe_external_control_plane(self) -> dict[str, Any]:
        """固定global authorityのpure probe。product側からglobalを修復しない。"""
        return external_control.probe_external_readiness()

    def resolve_high_cost_binding(self) -> dict[str, Any]:
        return audit_recovery_control.resolve_live_high_cost_binding(self.bin_dir)

    def resolve_failure_receipt(self, issue_date: str, receipt_sha256: str) -> Path:
        issue_date = _validate_date(issue_date)
        if re.fullmatch(r"[0-9a-f]{64}", receipt_sha256) is None:
            raise ValueError("SCHEDULED_FAILURE_RECEIPT_MISSING")
        candidates = [
            self.repo_root
            / "build"
            / "recovery"
            / "authority"
            / f"{issue_date}-scheduled-failure.json"
        ]
        if self.evidence_root is not None:
            legacy_dir = self.evidence_root / "build" / "scheduled-failure-receipts"
            legacy_candidates = sorted(legacy_dir.glob(f"{issue_date}-*.json"))
            if len(legacy_candidates) > 32:
                raise ValueError("SCHEDULED_FAILURE_RECEIPT_AMBIGUOUS")
            candidates.extend(legacy_candidates)
        for path in candidates:
            if not path.is_file() or path.is_symlink():
                continue
            try:
                value = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if not isinstance(value, dict):
                continue
            claimed = str(value.get("receiptSha256") or "")
            body = {key: item for key, item in value.items() if key != "receiptSha256"}
            if (
                value.get("schemaVersion") == "SCHEDULED_FAILURE_RECEIPT_V1"
                and value.get("issueDate") == issue_date
                and claimed == receipt_sha256
                and _sha(body) == claimed
            ):
                return path.resolve()
        raise ValueError("SCHEDULED_FAILURE_RECEIPT_MISSING")

    def _run_broker(self, *args: str) -> dict[str, Any]:
        binding = audit_recovery_control.resolve_live_high_cost_binding(self.bin_dir)
        broker_path = Path(str(binding["brokerInstalledPath"])).resolve(strict=True)
        completed = subprocess.run(
            [audit_recovery_control._canonical_python_executable(), str(broker_path), *args],
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

    def admit_scheduled_recovery(
        self, *, issue_date: str, recovery_authority_path: Path
    ) -> Path:
        try:
            authority = json.loads(
                recovery_authority_path.read_text(encoding="utf-8-sig")
            )
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("HIGH_COST_SCHEDULED_ADMISSION_INVALID") from error
        if not isinstance(authority, dict):
            raise ValueError("HIGH_COST_SCHEDULED_ADMISSION_INVALID")
        expected_authority_sha = str(authority.get("receiptSha256") or "")

        def matching_existing_admission(candidate: Path) -> dict[str, Any] | None:
            if not candidate.is_file() or candidate.is_symlink():
                return None
            try:
                admission = json.loads(candidate.read_text(encoding="utf-8-sig"))
                return validate_scheduled_admission_receipt(
                    admission,
                    expected_operation_kind="scheduled_recovery",
                    expected_issue_date=issue_date,
                    expected_operation_authority_sha256=expected_authority_sha,
                )
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
                # Invalid local/evidence receipts are never copied or reused.
                return None

        path = (
            self.repo_root
            / "build"
            / "high-cost-operation-admissions"
            / issue_date
            / "audit-0640-scheduled_recovery.json"
        )
        admission = matching_existing_admission(path)
        if admission is not None:
            return path.resolve()
        if self.evidence_root is not None and self.evidence_root != self.repo_root:
            evidence_path = (
                self.evidence_root
                / "build"
                / "high-cost-operation-admissions"
                / issue_date
                / "audit-0640-scheduled_recovery.json"
            )
            admission = matching_existing_admission(evidence_path)
            if admission is not None:
                _atomic_json(path, admission)
                return path.resolve()
        admission = self._run_broker(
            "admit",
            "--operation-kind",
            "scheduled_recovery",
            "--attempt-id",
            issue_date,
            "--issue-date",
            issue_date,
            "--authority-evidence",
            str(recovery_authority_path),
            "--expected-task-action-sha256",
            self.task_action_sha256(),
            "--expected-runner-sha256",
            _file_sha(self.runner_path),
        )
        try:
            if "receiptSha256" in admission:
                admission = validate_scheduled_admission_receipt(
                    admission,
                    expected_operation_kind="scheduled_recovery",
                    expected_issue_date=issue_date,
                    expected_operation_authority_sha256=expected_authority_sha,
                )
            else:
                admission = seal_fresh_broker_admission(
                    admission,
                    expected_operation_kind="scheduled_recovery",
                    expected_issue_date=issue_date,
                    expected_operation_authority_sha256=expected_authority_sha,
                )
        except ValueError as error:
            raise ValueError("HIGH_COST_SCHEDULED_ADMISSION_INVALID") from error
        _atomic_json(path, admission)
        return path.resolve()

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


def _causal_retry_state_path(repo_root: Path, issue_date: str) -> Path:
    return (
        repo_root
        / "build"
        / "recovery"
        / "control"
        / f"{issue_date}-causal-retry.json"
    )


def _load_causal_retry_state(
    *, repo_root: Path, issue_date: str, runner_state: dict[str, Any]
) -> dict[str, Any]:
    path = _causal_retry_state_path(repo_root, issue_date)
    if path.exists():
        try:
            payload = audit_recovery_control._read_bounded_bytes(
                path,
                maximum=64 * 1024,
                code="CAUSAL_RETRY_STATE_INVALID",
            )
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError, ValueError) as error:
            raise ValueError("CAUSAL_RETRY_STATE_INVALID") from error
        if not isinstance(value, dict):
            raise ValueError("CAUSAL_RETRY_STATE_INVALID")
        return dict(value)
    for key in ("causalRetryState", "causal_retry_state"):
        value = runner_state.get(key)
        if isinstance(value, dict):
            return dict(value)
    return {}


def _normalize_causal_retry_evidence(value: object) -> dict[str, Any]:
    current = dict(value) if isinstance(value, dict) else {}
    for canonical, alias in audit_recovery_control.CAUSE_INPUT_ALIASES.items():
        selected = str(current.get(canonical) or current.get(alias) or "")
        current[canonical] = selected
        current[alias] = selected
    return current


def _daily_operation_lineage_id(*, issue_date: str, runner_state: dict[str, Any]) -> str:
    """run/session/path変更では変わらない当日scheduled authority由来のlineage。"""
    existing = str(
        runner_state.get("dailyOperationLineageId")
        or runner_state.get("daily_operation_lineage_id")
        or ""
    )
    if existing:
        return existing
    authority = str(
        runner_state.get("scheduledAuthorityId")
        or runner_state.get("scheduled_authority_id")
        or runner_state.get("authorityId")
        or f"News-Grasp|{issue_date}|scheduled"
    )
    return hashlib.sha256(
        json.dumps(
            {
                "schemaVersion": "DAILY_OPERATION_LINEAGE_V1",
                "issueDate": issue_date,
                "scheduledAuthorityId": authority,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _admit_causal_retry(
    *,
    repo_root: Path,
    issue_date: str,
    runner_state: dict[str, Any],
    completion: dict[str, Any],
) -> dict[str, Any]:
    current = _normalize_causal_retry_evidence(completion)
    lineage_id = _daily_operation_lineage_id(
        issue_date=issue_date,
        runner_state=runner_state,
    )
    current["dailyOperationLineageId"] = lineage_id
    expected_root = Path(repo_root) / "build" / "recovery" / "control"
    state_path = _causal_retry_state_path(repo_root, issue_date)
    expected_path = expected_root / f"{issue_date}-causal-retry.json"
    if not audit_recovery_control._same_lexical_path(state_path, expected_path):
        raise ValueError("CAUSAL_RETRY_STATE_PATH_INVALID")
    root = audit_recovery_control._validated_managed_root(
        repo_root=Path(repo_root),
        candidate_root=expected_root,
        relative_parts=("build", "recovery", "control"),
        code="CAUSAL_RETRY_STATE_PATH_INVALID",
    )
    with audit_recovery_control._pinned_directory(
        root,
        anchor=Path(repo_root),
        invalid_code="CAUSAL_RETRY_STATE_PATH_INVALID",
    ):
        with audit_recovery_control._exclusive_file_lock(
            root / ".causal-retry.lock",
            busy_code="CAUSAL_RETRY_BUSY",
            invalid_code="CAUSAL_RETRY_STATE_PATH_INVALID",
            wait_timeout_sec=2.0,
        ):
            root = audit_recovery_control._validated_managed_root(
                repo_root=Path(repo_root),
                candidate_root=expected_root,
                relative_parts=("build", "recovery", "control"),
                code="CAUSAL_RETRY_STATE_PATH_INVALID",
            )
            previous = _load_causal_retry_state(
                repo_root=repo_root,
                issue_date=issue_date,
                runner_state=runner_state,
            )
            if any(
                previous.get(field) or previous.get(alias)
                for field, alias in audit_recovery_control.CAUSE_INPUT_ALIASES.items()
            ):
                previous.setdefault("dailyOperationLineageId", lineage_id)
            decision = audit_recovery_control.causal_retry_gate(previous, current)
            result = {
                "schemaVersion": "CAUSAL_RETRY_RUNTIME_EVENT_V1",
                "allowed": bool(decision.get("allowed")),
                "reasonCode": str(
                    decision.get("reasonCode") or "CAUSAL_RETRY_REJECTED"
                ),
                "causeFingerprint": str(
                    decision.get("causeFingerprint")
                    or audit_recovery_control.cause_fingerprint(current)
                ),
                "previousCauseFingerprint": audit_recovery_control.cause_fingerprint(
                    previous
                ),
                "changedInputs": list(decision.get("changedInputs") or []),
                "retryConsumed": bool(decision.get("retryConsumed")),
                "sourceSha256": current.get("source_sha256", ""),
                "runtimeSha256": current.get("runtime_sha256", ""),
                "configSha256": current.get("config_sha256", ""),
                "authoritySha256": current.get("authority_sha256", ""),
                "externalEvidenceSha256": current.get(
                    "external_evidence_sha256", ""
                ),
                "dailyOperationLineageId": lineage_id,
            }
            if result["allowed"]:
                state = {
                    **current,
                    "schemaVersion": "CAUSAL_RETRY_RUNTIME_STATE_V1",
                    "causeFingerprint": result["causeFingerprint"],
                    "retryConsumed": True,
                }
                audit_recovery_control._atomic_write_bytes(
                    state_path,
                    (
                        json.dumps(state, ensure_ascii=False, sort_keys=True) + "\n"
                    ).encode("utf-8"),
                )
            return result


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
    preloaded_witness: dict[str, Any] | None = None
    preloaded_failure: dict[str, Any] | None = None
    preloaded_failure_path: Path | None = None
    try:
        state = actual.load_state(issue_date)
    except ValueError as error:
        if trigger != "audit_0640":
            raise
        try:
            try:
                actual.reconcile_task_history(issue_date)
            except ValueError as reconcile_error:
                if "SCHEDULED_PRE_ADMISSION_RECONCILE_REPLAY" not in str(
                    reconcile_error
                ):
                    raise
            preloaded_witness = actual.inspect_attempt(issue_date)
            preloaded_failure_path = actual.resolve_failure_receipt(
                issue_date,
                str(preloaded_witness.get("failureReceiptSha256") or ""),
            )
            preloaded_failure = json.loads(
                preloaded_failure_path.read_text(encoding="utf-8-sig")
            )
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as inner:
            raise ValueError("RUNNER_STATE_EVIDENCE_INVALID") from inner
        if (
            preloaded_witness.get("scheduledAttemptStatus") != "failed"
            or preloaded_failure.get("receiptSha256")
            != preloaded_witness.get("failureReceiptSha256")
        ):
            raise ValueError("RUNNER_STATE_EVIDENCE_INVALID") from error
        state = {
            "status": "blocked_startup_self_repair_failed",
            "date": issue_date,
            "run_intent": "ScheduledProduction",
            "phase": "pre_admission",
            "exit_code": process_exit_code,
            "scheduled_failure_receipt_path": str(preloaded_failure_path),
        }
    if hasattr(actual, "probe_external_control_plane"):
        external_readiness = actual.probe_external_control_plane()
        if str(external_readiness.get("status") or "") != "ready":
            lineage = _daily_operation_lineage_id(
                issue_date=issue_date,
                runner_state=state,
            )
            plan = _sealed(
                {
                    "schemaVersion": SCHEMA,
                    "issuer": ISSUER,
                    "issueDate": issue_date,
                    "trigger": trigger,
                    "action": "defer_external_control_plane",
                    "terminal": "operation_deferred_external_dependency",
                    "reasonCode": "EXTERNAL_CONTROL_PLANE_UNAVAILABLE",
                    "scheduledAttemptStatus": str(
                        state.get("scheduledAttemptStatus") or "unknown"
                    ),
                    "recoveryAttemptStatus": "deferred",
                    "publicCompletionStatus": "green"
                    if state.get("publicCompletionStatus") == "green"
                    else "unchanged",
                    "publicStatus": "unchanged",
                    "dailyOperationLineageId": lineage,
                    "externalGenerationFingerprint": str(
                        external_readiness.get("externalGenerationFingerprint") or "0" * 64
                    ),
                    "externalReasonCode": str(
                        external_readiness.get("reasonCode") or "EXTERNAL_CONTROL_PLANE_UNAVAILABLE"
                    ),
                    "modelLaunchCount": 0,
                    "duplicateReportCount": 0,
                    "noFocusTheft": True,
                    "noAutoOpen": True,
                    "noUserMonitoring": True,
                    "completion": False,
                }
            )
            output = actual.repo_root / "build" / "recovery" / "control" / f"{issue_date}-{trigger}.json"
            _atomic_json(output, plan)
            return {**plan, "decisionPath": str(output.resolve())}
    log_text = actual.log_text(issue_date)
    pre_admission_failure: dict[str, Any] | None = None
    pre_admission_failure_path: Path | None = None
    try:
        witness = preloaded_witness or actual.inspect_attempt(issue_date)
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
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
            try:
                authority = audit_recovery_control.load_completion_authority_receipt(
                    issue_date
                )
            except ValueError:
                authority = None
            completion = {
                "verificationStatus": "verification_unavailable",
                "publicCompletionStatus": "green" if authority else "unverified",
                "nextRunReadinessStatus": "unverified",
                "phase": "public_oracle",
                "reasonCode": "PRIMARY_VERIFIER_EXCEPTION",
                "failedGateIds": ["primary_completion_verifier"],
                "completionAuthorityId": str(
                    (authority or {}).get("completionAuthorityId") or ""
                ),
                "exceptionType": type(error).__name__,
            }
        selected = select_audit_recovery_action(completion)
        causal_retry: dict[str, Any] | None = None
        retry_suppressed = False
        if selected.get("action") == "readiness_repair":
            causal_retry = _admit_causal_retry(
                repo_root=actual.repo_root,
                issue_date=issue_date,
                runner_state=state,
                completion=completion,
            )
            retry_suppressed = causal_retry.get("allowed") is not True
            selected = {
                **selected,
                "retrySuppressed": retry_suppressed,
            }
        if selected.get("action") in {"audit_observation_unverified", "readiness_repair"}:
            plan = _sealed(
                {
                    "schemaVersion": SCHEMA,
                    "issuer": ISSUER,
                    "issueDate": issue_date,
                    "trigger": trigger,
                    "scheduledAttemptStatus": (
                        "failed" if witness.get("scheduledAttemptStatus") == "failed" else "succeeded"
                    ),
                    "recoveryAttemptStatus": (
                        "succeeded" if witness.get("recoveryAttemptStatus") == "started" else "not_started"
                    ),
                    "completion": False,
                    "maxAutomaticRecoveryAttempts": 1,
                    "noFocusTheft": True,
                    "noAutoOpen": True,
                    "noUserMonitoring": True,
                    "recoveryStarted": False,
                    **completion,
                    **selected,
                    "causalRetry": causal_retry,
                    "retrySuppressed": retry_suppressed,
                    "causeFingerprint": (
                        causal_retry.get("causeFingerprint")
                        if causal_retry
                        else ""
                    ),
                    "terminal": selected.get("terminal")
                    or "audit_readiness_repair_pending",
                }
            )
            output = actual.repo_root / "build" / "recovery" / "control" / f"{issue_date}-{trigger}.json"
            _atomic_json(output, plan)
            return {**plan, "decisionPath": str(output.resolve())}
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
    observed_exit_code = int(state.get("exit_code") or process_exit_code)
    classification = classify_observed_failure(
        runner_state=state,
        process_exit_code=observed_exit_code,
        log_text=log_text,
    )
    if trigger == "audit_0640" and classification != "incident_required":
        classification = "recoverable"
    elif trigger == "audit_0640" and str(state.get("status") or "") == "publish_complete":
        classification = "recoverable"
    failure_path = actual.repo_root / "build" / "recovery" / "authority" / f"{issue_date}-scheduled-failure.json"
    if preloaded_failure is not None and preloaded_failure_path is not None:
        failure = preloaded_failure
        failure_path = preloaded_failure_path
    elif pre_admission_failure is not None and pre_admission_failure_path is not None:
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
            failure_path = actual.resolve_failure_receipt(
                issue_date,
                str(witness.get("failureReceiptSha256") or ""),
            )
            failure = json.loads(failure_path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("SCHEDULED_FAILURE_RECEIPT_MISSING") from error
    if witness.get("scheduledAttemptStatus") != "failed" or (
        witness.get("failureReceiptSha256") != failure.get("receiptSha256")
    ):
        raise ValueError("SCHEDULED_FAILURE_LEDGER_MISMATCH")
    if witness.get("recoveryAttemptStatus") == "started" and not _local_startup_unblocker_rejection(state):
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
    high_cost_admission_reentry = (
        _local_startup_unblocker_rejection(state)
        and audit_decision.get("reasonCode") == "RECOVERY_STARTED_BUT_COMPLETION_INVALID"
        and audit_decision.get("recoveryBranch") == "ScheduledRecoveryFull"
    )
    if audit_decision.get("action") != "scheduled_recovery" and not high_cost_admission_reentry:
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
            branch = "major_incident_fail_closed"
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
    plan = _sealed(
        {
            **{key: value for key, value in plan.items() if key != "receiptSha256"},
            "scheduledFailureReceiptPath": str(failure_path.resolve()),
            "recoveryAuthorityPath": str(authority_path.resolve()),
        }
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
    if value.get("recoveryBranch") == "ResumeFromStage":
        resume_stage = value.get("resumeStage")
        if not isinstance(resume_stage, str) or resume_stage not in RESUME_STAGES:
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
            "allowedAfterPublicGreen": audit_recovery_control.PUBLIC_GREEN_ALLOWED_OPERATIONS,
            "publicRecoveryStarted": False,
            "recoveryStarted": False,
            "completionEvidenceSha256": completion["receiptSha256"],
            "sourceDecision": decision,
            "completionEvidence": completion,
        }
    )


def _audit_observation_terminal(
    *, issue_date: str, decision: dict[str, Any]
) -> dict[str, Any]:
    return audit_recovery_control.seal_audit_decision(
        {
            "schemaVersion": "AUDIT_RECOVERY_DECISION_V1",
            "issueDate": issue_date,
            "classification": "observation_unverified",
            "action": "none",
            "terminal": "audit_observation_unverified",
            "reasonCode": str(decision.get("reasonCode") or "VERIFICATION_UNAVAILABLE"),
            "scheduledAttemptStatus": str(
                decision.get("scheduledAttemptStatus") or "unverified"
            ),
            "recoveryAttemptStatus": str(
                decision.get("recoveryAttemptStatus") or "not_started"
            ),
            "publicStatus": "green",
            "operationState": "observation_unverified",
            "workPriority": audit_recovery_control.PUBLIC_GREEN_FOLLOWUP_PRIORITY,
            "allowedAfterPublicGreen": audit_recovery_control.PUBLIC_GREEN_ALLOWED_OPERATIONS,
            "exitCode": 2,
            "completionAuthorityId": str(
                decision.get("completionAuthorityId") or ""
            ),
            "sourceSha256": str(decision.get("sourceSha256") or ""),
            "runtimeSha256": str(decision.get("runtimeSha256") or ""),
            "configSha256": str(decision.get("configSha256") or ""),
            "externalEvidenceSha256": str(
                decision.get("externalEvidenceSha256") or ""
            ),
            "evidenceSha256": _sha(
                {
                    "issueDate": issue_date,
                    "reasonCode": decision.get("reasonCode"),
                    "completionAuthorityId": decision.get("completionAuthorityId"),
                }
            ),
            "sourceDecision": decision,
            "completionEvidence": None,
        }
    )


def _execute_audit_0640_owned(
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
    run_command = command_runner or _run_owned_command
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
                recovered=(
                    decision.get("recoveryAttemptStatus") == "succeeded"
                ),
            )
            write_terminal(terminal)
            return terminal
        if action == "audit_observation_unverified":
            terminal = _audit_observation_terminal(
                issue_date=issue_date,
                decision=decision,
            )
            write_terminal(terminal)
            return terminal
        if action == "defer_external_control_plane":
            # 外部Redはaudit terminalをGreenへ偽装せず、公開・checkpointを保持した
            # observationだけを一件追記する。global修復やmodel起動は行わない。
            terminal = _audit_observation_terminal(
                issue_date=issue_date,
                decision={
                    **decision,
                    "reasonCode": "EXTERNAL_CONTROL_PLANE_UNAVAILABLE",
                    "terminal": "operation_deferred_external_dependency",
                },
            )
            write_terminal(terminal)
            return terminal
        if action == "readiness_repair":
            if decision.get("retrySuppressed") is True:
                terminal = _audit_observation_terminal(
                    issue_date=issue_date,
                    decision={
                        **decision,
                        "reasonCode": str(
                            (decision.get("causalRetry") or {}).get("reasonCode")
                            or "CAUSAL_RETRY_SUPPRESSED"
                        ),
                    },
                )
                write_terminal(terminal)
                return terminal
            installer_path = (
                actual.repo_root / "scripts" / "ops" / "install-news-grasp-ops.ps1"
            )
            command = [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(installer_path),
                "-RepoDir",
                str(actual.repo_root),
            ]
            def _execute_registered_reconcile(
                context: Mapping[str, Any],
            ) -> Mapping[str, Any]:
                return_code = int(
                    run_command(
                        command, cwd=actual.repo_root, timeout_seconds=300
                    )
                )
                return {
                    "status": "command_completed",
                    "returnCode": return_code,
                    "mutationCount": 1 if return_code == 0 else 0,
                    "dailyOperationLineageId": context.get(
                        "dailyOperationLineageId"
                    ),
                }

            registered_repair = dispatch_registered_readiness_repair(
                repo_root=actual.repo_root,
                reason_code=str(decision.get("reasonCode") or "READINESS_RED"),
                context={
                    "dailyOperationLineageId": str(
                        decision.get("dailyOperationLineageId") or ""
                    ),
                    "completionAuthorityId": str(
                        decision.get("completionAuthorityId") or ""
                    ),
                    "causeFingerprint": str(
                        decision.get("causeFingerprint") or ""
                    ),
                },
                executor=_execute_registered_reconcile,
            )
            return_code = int(
                registered_repair["handlerResult"].get("returnCode", 1)
            )
            if return_code != 0:
                terminal = _audit_observation_terminal(
                    issue_date=issue_date,
                    decision={
                        **decision,
                        "reasonCode": f"READINESS_REPAIR_EXECUTION_FAILED_{return_code}",
                    },
                )
                write_terminal(terminal)
                return terminal
            completion = verify_completion(issue_date, "ScheduledProduction")
            if not audit_recovery_control.same_date_completion_green(
                issue_date, completion
            ):
                terminal = _audit_observation_terminal(
                    issue_date=issue_date,
                    decision={
                        **decision,
                        "reasonCode": "READINESS_REPAIR_COMPLETION_INVALID",
                    },
                )
                write_terminal(terminal)
                return terminal
            terminal = _audit_green_terminal(
                issue_date=issue_date,
                decision={**decision, "registeredRepair": registered_repair},
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
            high_cost_binding = actual.resolve_high_cost_binding()
            execution_receipt: Path | None = None
            high_cost_admission_path: Path | None = None
            if isinstance(actual, ProductionBackend):
                receipt_payload = {
                    "issueDate": issue_date,
                    "scheduledFailureReceiptPath": str(
                        decision["scheduledFailureReceiptPath"]
                    ),
                    "recoveryAuthorityPath": str(decision["recoveryAuthorityPath"]),
                }
                receipt_authority_decision = audit_recovery_control.decide_audit_recovery(
                    receipt_payload
                )
                if (
                    receipt_authority_decision.get("action") != "scheduled_recovery"
                    and decision.get("recoveryBranch") == "ScheduledRecoveryFull"
                ):
                    authority_value = json.loads(
                        Path(str(decision["scheduledAuthorityEvidencePath"])).read_text(
                            encoding="utf-8-sig"
                        )
                    )
                    receipt_authority_decision = {
                        **receipt_authority_decision,
                        "recoveryAuthorityReceiptSha256": str(
                            authority_value.get("receiptSha256") or ""
                        ),
                    }
                execution_receipt = (
                    audit_recovery_control._issue_recovery_execution_receipt(
                        payload=receipt_payload,
                        decision=receipt_authority_decision,
                        issue_date=issue_date,
                        audit_accepted_at=audit_recovery_control.datetime.now()
                        .astimezone()
                        .isoformat(),
                        artifact_repo_root=actual.repo_root,
                        production_runtime_root=(
                            Path.home()
                            / ".news-grasp-runtime"
                            / "production-runtime"
                        ),
                        live_bin_root=actual.bin_dir,
                        runner_path=actual.runner_path,
                        recovery_branch=str(decision["recoveryBranch"]),
                        resume_stage=(
                            str(decision["resumeStage"])
                            if decision.get("resumeStage")
                            else None
                        ),
                        python_executable_path=Path(
                            audit_recovery_control._canonical_python_executable()
                        ),
                        capability_reservation_path=Path(
                            str(high_cost_binding["bindingPath"])
                        ),
                        capability_reservation_receipt_sha256=str(
                            high_cost_binding["bindingReceiptSha256"]
                        ),
                        reserved_max_external_model_calls=9,
                    )
                )
                if decision.get("recoveryBranch") == "ScheduledRecoveryFull":
                    high_cost_admission_path = actual.admit_scheduled_recovery(
                        issue_date=issue_date,
                        recovery_authority_path=Path(
                            str(decision["scheduledAuthorityEvidencePath"])
                        ),
                    )
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
                "-HighCostBindingPath",
                str(high_cost_binding["bindingPath"]),
                "-HighCostBindingReceiptSha256",
                str(high_cost_binding["bindingReceiptSha256"]),
                "-ScheduledAuthorityEvidencePath",
                str(decision["scheduledAuthorityEvidencePath"]),
                "-RecoveryDecisionPath",
                str(decision["decisionPath"]),
            ]
            if execution_receipt is not None:
                command.extend(
                    ["-RecoveryExecutionReceiptPath", str(execution_receipt)]
                )
            if high_cost_admission_path is not None:
                command.extend(["-HighCostAdmissionPath", str(high_cost_admission_path)])
            if decision.get("recoveryBranch") == "ResumeFromStage":
                command.extend(
                    [
                        "-ResumeFromStage",
                        str(decision["resumeStage"]),
                        "-HighCostAdmissionPath",
                        str(decision["sourceAdmissionPath"]),
                    ]
                )
            return_code = int(
                run_command(
                    command,
                    cwd=actual.repo_root,
                    timeout_seconds=_recovery_remaining_seconds(issue_date),
                )
            )
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
        if decision and decision.get("action") == "readiness_repair":
            terminal = _audit_observation_terminal(
                issue_date=issue_date,
                decision={
                    **decision,
                    "reasonCode": (
                        "READINESS_REPAIR_RUNTIME_FAILED_"
                        + type(error).__name__.upper()
                    ),
                },
            )
        else:
            terminal = _audit_incident_terminal(
                issue_date=issue_date,
                reason_code="AUDIT_EXECUTOR_FAILED_" + type(error).__name__.upper(),
                decision=decision,
            )
        write_terminal(terminal)
        return terminal


def execute_audit_0640(
    *,
    issue_date: str,
    backend: ProductionBackend | None = None,
    command_runner: Any | None = None,
    minimal_executor: Any | None = None,
    completion_verifier: Any | None = None,
    terminal_writer: Any | None = None,
) -> dict[str, Any]:
    """互換adapter。production callerはcanonical transaction ownerへ接続する。"""

    if any(
        value is not None
        for value in (
            backend,
            command_runner,
            minimal_executor,
            completion_verifier,
            terminal_writer,
        )
    ):
        # 明示的dependency injectionは外部副作用を持たないcontract test seam。
        return _execute_audit_0640_owned(
            issue_date=issue_date,
            backend=backend,
            command_runner=command_runner,
            minimal_executor=minimal_executor,
            completion_verifier=completion_verifier,
            terminal_writer=terminal_writer,
        )
    return audit_recovery_control.ensure_audit_0640(
        issue_date=issue_date,
        trigger="daily_compatibility_adapter",
    )


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
    return (
        2
        if result.get("terminal")
        in {"audit_major_incident_open", "audit_observation_unverified"}
        or result.get("action")
        in {"audit_observation_unverified", "readiness_repair"}
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
