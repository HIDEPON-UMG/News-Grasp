from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from tools import audit_recovery_control
from tools import news_grasp_recovery_receipts
from tools import news_grasp_recovery_transaction
from tools import news_grasp_finalization
from tools import news_grasp_control_plane
from tools import news_grasp_external_control as external_control
from tools import news_grasp_convergence as convergence
from tools.news_grasp_operational_contract import (
    RECOVERY_RESUME_STAGES,
    evaluate_completion_v3,
    select_recovery_branch_from_truth,
)


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
RESUME_STAGES = set(RECOVERY_RESUME_STAGES)


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


def _remaining_deadline_seconds(
    value: object,
    *,
    observed_at: datetime | None = None,
    invalid_code: str = "AUDIT_RECOVERY_DEADLINE_INVALID",
    expired_code: str = "AUDIT_RECOVERY_HARD_DEADLINE_EXPIRED",
) -> int:
    """共有deadlineから親・観測・childが使う同一の残時間を算出する。"""
    try:
        deadline = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(invalid_code) from error
    if deadline.tzinfo is None:
        raise ValueError(invalid_code)
    observed = observed_at or datetime.now(timezone.utc)
    if observed.tzinfo is None:
        raise ValueError(invalid_code)
    remaining = int(
        (
            deadline.astimezone(timezone.utc)
            - observed.astimezone(timezone.utc)
        ).total_seconds()
    )
    if remaining <= 0:
        raise ValueError(expired_code)
    return remaining


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
    """public Green後はterminalを閉じ、readiness debtを別取引へ搬送する。"""
    value = completion if isinstance(completion, dict) else {}
    public_status = str(value.get("publicCompletionStatus") or "")
    readiness_status = str(value.get("nextRunReadinessStatus") or "")
    authority_id = str(value.get("completionAuthorityId") or "")
    if (
        str(value.get("verificationStatus") or "") == "slo_failed"
        or str(value.get("reasonCode") or "") == "PUBLIC_GREEN_SLO_FAILED"
    ):
        return {
            "action": "major_incident",
            "terminal": "audit_major_incident_open",
            "publicStatus": "green",
            "publicRecoveryStarted": False,
            "recoveryStarted": False,
            "exitCode": 2,
            "completionAuthorityId": authority_id,
            "reasonCode": "PUBLIC_GREEN_SLO_FAILED",
            "nextAction": "investigate_slo_without_public_republication",
        }
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
            "action": "none",
            "recoveryScope": "readiness_debt_out_of_band",
            "publicStatus": "green",
            "publicRecoveryStarted": False,
            "completionAuthorityId": authority_id,
            "reasonCode": str(value.get("reasonCode") or "READINESS_RED"),
            "readinessDebt": True,
            "exitCode": 2,
            "nextAction": "reconcile_readiness_in_separate_transaction",
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


def build_recovery_plan(
    *,
    issue_date: str,
    trigger: str,
    classification: str,
    branch: str,
    authority_path: Path,
    failure_receipt_sha256: str,
    operational_truth_sha256: str,
    operational_truth_path: str = "",
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
        "operationalTruthPath": operational_truth_path or None,
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
        raise ValueError("RECOVERY_RESUME_EVIDENCE_INVALID")
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
        # brokerへ渡す効果authorityはV1のまま更新可能にし、product側の
        # current authority V2を同じパスへ上書きしない。
        self.mission_path = self.authority_dir / "broker-audit-mission-authority-v1.json"

    def probe_external_control_plane(self) -> dict[str, Any]:
        """固定global authorityのpure probe。product側からglobalを修復しない。"""
        return external_control.probe_external_readiness()

    def resolve_high_cost_binding(self) -> dict[str, Any]:
        return audit_recovery_control.resolve_live_high_cost_binding(self.bin_dir)

    def capture_readiness_observation(
        self, *, issue_date: str, completion_authority_id: str
    ) -> dict[str, Any]:
        from tools import daily_self_heal

        observation = daily_self_heal.verify_live_runner_readiness(
            repo_root=self.repo_root,
            ops_repo_root=self.repo_root,
            date=issue_date,
        )
        return news_grasp_finalization.record_readiness_observation_v1(
            repo_root=self.repo_root,
            issue_date=issue_date,
            completion_authority_id=completion_authority_id,
            observation=observation,
        )

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
            [sys.executable, str(broker_path), *args],
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

    def observe_owned_runner(
        self, issue_date: str, state: Mapping[str, Any]
    ) -> dict[str, Any]:
        """実runnerをidentity receiptとheartbeatで限定観測する。"""

        from tools import news_grasp_recovery_transaction

        common = {
            "schemaVersion": "NEWS_GRASP_OWNED_RUNNER_OBSERVATION_V1",
            "issueDate": issue_date,
            "runId": str(state.get("run_id") or ""),
            "processId": int(state.get("pid") or 0),
            "healthy": False,
            "ownedProcessAlive": False,
            "ownershipEvidenceValid": False,
        }
        if (
            state.get("status") != "running"
            or state.get("run_intent") != "ScheduledProduction"
            or re.fullmatch(r"[0-9a-f]{32}", common["runId"]) is None
            or common["processId"] <= 0
        ):
            return _sealed({**common, "reasonCode": "OWNED_RUNNER_STATE_NOT_RUNNING"})
        expected = (
            self.repo_root
            / "build"
            / "recovery"
            / "launch-identities"
            / issue_date
            / f"{common['runId']}.json"
        ).resolve()
        try:
            receipt_path = Path(
                str(state.get("actualLaunchIdentityReceiptPath") or "")
            ).resolve(strict=True)
            if receipt_path != expected or receipt_path.is_symlink():
                raise ValueError("NEWS_GRASP_ACTUAL_LAUNCH_IDENTITY_INVALID")
            receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
            validated = news_grasp_recovery_transaction.validate_actual_launch_identity_v1(
                receipt,
                receipt_path=receipt_path,
                issue_date=issue_date,
                run_id=common["runId"],
                process_id=common["processId"],
                artifact_root=self.repo_root,
                current_task_action_sha256=self.task_action_sha256(),
                require_process_alive=False,
            )
            if (
                state.get("actualLaunchIdentityReceiptSha256")
                != validated.get("receiptSha256")
                or Path(str(state.get("runner_path") or "")).resolve(strict=True)
                != Path(str(validated["runner"]["path"])).resolve(strict=True)
            ):
                raise ValueError("NEWS_GRASP_ACTUAL_LAUNCH_IDENTITY_INVALID")
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            return _sealed({**common, "reasonCode": "OWNED_RUNNER_IDENTITY_UNVERIFIED"})
        alive = validated.get("processAlive") is True
        verified = {**common, "ownedProcessAlive": alive, "ownershipEvidenceValid": True}
        if not alive:
            return _sealed({**verified, "reasonCode": "OWNED_RUNNER_EXITED"})
        try:
            heartbeat = datetime.fromisoformat(
                str(state.get("heartbeat_at") or "").replace("Z", "+00:00")
            )
        except ValueError:
            return _sealed({**verified, "reasonCode": "OWNED_RUNNER_HEARTBEAT_INVALID"})
        if heartbeat.tzinfo is None:
            return _sealed({**verified, "reasonCode": "OWNED_RUNNER_HEARTBEAT_INVALID"})
        age = (datetime.now(timezone.utc) - heartbeat.astimezone(timezone.utc)).total_seconds()
        if age < -60:
            return _sealed({**verified, "reasonCode": "OWNED_RUNNER_HEARTBEAT_FUTURE"})
        if age > 15 * 60:
            return _sealed(
                {
                    **verified,
                    "heartbeatAgeSeconds": int(age),
                    "reasonCode": "OWNED_RUNNER_HEARTBEAT_STALE",
                }
            )
        return _sealed(
            {
                **verified,
                "healthy": True,
                "heartbeatAgeSeconds": max(0, int(age)),
                "reasonCode": "OWNED_RUNNER_HEALTHY",
            }
        )

    @staticmethod
    def wait_for_owned_runner_change(seconds: float) -> None:
        time.sleep(max(0.0, seconds))

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
        permit_path = self.authority_dir / f"{issue_date}-launch-permit-v1.json"
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

    def reserve_recovery_capability(
        self, *, issue_date: str, recovery_authority_path: Path
    ) -> tuple[dict[str, Any], Path]:
        """runner前に最大必要量のscheduled recovery admissionを一回予約する。"""

        output = (
            self.repo_root
            / "build"
            / "recovery"
            / "capability-reservations"
            / f"{issue_date}-scheduled-recovery.json"
        )
        if output.is_file() and not output.is_symlink():
            try:
                existing = json.loads(output.read_text(encoding="utf-8-sig"))
                body = {
                    key: item for key, item in existing.items() if key != "receiptSha256"
                }
                if (
                    existing.get("schemaVersion")
                    == "HIGH_COST_SCHEDULED_OPERATION_ADMISSION_V1"
                    and existing.get("issueDate") == issue_date
                    and existing.get("operationKind") == "scheduled_recovery"
                    and existing.get("receiptSha256") == _sha(body)
                ):
                    return existing, output.resolve()
            except (OSError, UnicodeError, json.JSONDecodeError):
                pass
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
        if (
            admission.get("schemaVersion")
            != "HIGH_COST_SCHEDULED_OPERATION_ADMISSION_V1"
            or admission.get("issueDate") != issue_date
            or admission.get("operationKind") != "scheduled_recovery"
        ):
            raise ValueError("RECOVERY_CAPABILITY_RESERVATION_INVALID")
        _atomic_json(output, admission)
        return admission, output.resolve()

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
    state = actual.load_state(issue_date)
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
    if trigger == "audit_0640" and str(state.get("status") or "") == "running":
        observation = (
            actual.observe_owned_runner(issue_date, state)
            if hasattr(actual, "observe_owned_runner")
            else {
                "healthy": False,
                "ownedProcessAlive": False,
                "ownershipEvidenceValid": False,
                "reasonCode": "OWNED_RUNNER_OBSERVER_UNAVAILABLE",
            }
        )
        if observation.get("healthy") is True or observation.get("ownedProcessAlive") is True:
            plan = _sealed(
                {
                    "schemaVersion": SCHEMA,
                    "issuer": ISSUER,
                    "issueDate": issue_date,
                    "trigger": trigger,
                    "action": "observe_existing_runner",
                    "terminal": None,
                    "reasonCode": str(observation.get("reasonCode") or "OWNED_RUNNER_OBSERVATION"),
                    "scheduledAttemptStatus": "running",
                    "recoveryAttemptStatus": "not_started",
                    "publicStatus": "pending",
                    "publicRecoveryStarted": False,
                    "recoveryStarted": False,
                    "completion": False,
                    "ownedRunnerObservation": observation,
                    "noFocusTheft": True,
                    "noAutoOpen": True,
                    "noUserMonitoring": True,
                }
            )
            output = actual.repo_root / "build" / "recovery" / "control" / f"{issue_date}-{trigger}.json"
            _atomic_json(output, plan)
            return {**plan, "decisionPath": str(output.resolve())}
        if (
            observation.get("ownershipEvidenceValid") is not True
            and observation.get("reasonCode") != "OWNED_RUNNER_EXITED"
        ):
            plan = _sealed(
                {
                    "schemaVersion": SCHEMA,
                    "issuer": ISSUER,
                    "issueDate": issue_date,
                    "trigger": trigger,
                    "action": "major_incident",
                    "terminal": "audit_major_incident_open",
                    "reasonCode": str(observation.get("reasonCode") or "OWNED_RUNNER_IDENTITY_UNVERIFIED"),
                    "scheduledAttemptStatus": "unverified",
                    "recoveryAttemptStatus": "not_started",
                    "publicStatus": "incomplete",
                    "publicRecoveryStarted": False,
                    "recoveryStarted": False,
                    "completion": False,
                    "ownedRunnerObservation": observation,
                    "nextAction": "verify_owned_runner_without_parallel_launch",
                    "noFocusTheft": True,
                    "noAutoOpen": True,
                    "noUserMonitoring": True,
                }
            )
            output = actual.repo_root / "build" / "recovery" / "control" / f"{issue_date}-{trigger}.json"
            _atomic_json(output, plan)
            return {**plan, "decisionPath": str(output.resolve())}
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
        if selected.get("action") in {
            "audit_observation_unverified",
            "major_incident",
        }:
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
                    or "audit_observation_unverified",
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
                    "readinessDebt": selected.get("readinessDebt") is True,
                    "exitCode": int(selected.get("exitCode") or 0),
                    "nextAction": str(selected.get("nextAction") or ""),
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
    truth_path = (
        actual.repo_root
        / "build"
        / "recovery"
        / "evidence"
        / f"{issue_date}-operational-truth.json"
    )
    _atomic_json(truth_path, truth)
    branch = select_recovery_branch_from_truth(truth)
    resume_stage = str(truth.get("resumeStage") or "")
    source_admission_path = str(state.get("highCostAdmissionPath") or "")
    broker_stage_decision_path = ""
    broker_stage_decision_sha256 = ""
    broker_stage_decision_receipt_sha256 = ""
    if branch == "ResumeFromStage":
        source_path = Path(source_admission_path)
        if not source_path.is_file():
            raise ValueError("RECOVERY_CAPABILITY_RESERVATION_REQUIRED")
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
        operational_truth_path=str(truth_path.resolve()),
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


def _execute_minimal_unblocker_owned(
    path: Path,
    *,
    transaction_receipt: Mapping[str, Any],
    repo_root: Path,
) -> dict[str, Any]:
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
    notification = repo_root / "build" / "notification" / f"{issue_date}.json"
    operation_kind = "minimal_notification_unblocker"

    def proven_notification() -> dict[str, Any] | None:
        if not notification.is_file():
            return None
        try:
            value = json.loads(notification.read_text(encoding="utf-8-sig"))
            from tools import daily_self_heal

            if not isinstance(value, dict) or not daily_self_heal._notification_delivery_proven(
                value, issue_date
            ):
                return None
            return value
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            return None

    def result_for(operation_receipt: Mapping[str, Any]) -> dict[str, Any]:
        return _sealed(
            {
                "schemaVersion": "NEWS_GRASP_MINIMAL_UNBLOCKER_RESULT_V1",
                "issuer": ISSUER,
                "issueDate": issue_date,
                "decisionReceiptSha256": decision["receiptSha256"],
                "ownedOperationReceiptSha256": operation_receipt["receiptSha256"],
                "notificationStateSha256": _file_sha(notification),
                "scheduledAttemptStatus": "failed",
                "recoveryAttemptStatus": "succeeded",
                "publicStatus": "pending_same_date_completion_reverification",
                "completion": False,
            }
        )

    try:
        operation_receipt = news_grasp_recovery_transaction.begin_owned_operation(
            repo_root=repo_root,
            issue_date=issue_date,
            owner_receipt=transaction_receipt,
            operation_kind=operation_kind,
            cause_receipt_sha256=decision["receiptSha256"],
        )
    except ValueError as error:
        if str(error) != "AUDIT_RECOVERY_OWNED_OPERATION_REPLAY_REJECTED":
            raise
        operation_receipt = news_grasp_recovery_transaction.resume_owned_operation(
            repo_root=repo_root,
            issue_date=issue_date,
            owner_receipt=transaction_receipt,
            operation_kind=operation_kind,
            cause_receipt_sha256=decision["receiptSha256"],
        )
        state = str(operation_receipt.get("operationState") or "")
        existing_notification = proven_notification()
        if state == "completed" and existing_notification is not None:
            if operation_receipt.get("outcomeReceiptSha256") != _file_sha(notification):
                raise ValueError("MINIMAL_UNBLOCKER_NOTIFICATION_RECEIPT_DRIFT")
            return result_for(operation_receipt)
        if state == "started_unresolved" and existing_notification is not None:
            news_grasp_recovery_transaction.complete_owned_operation(
                repo_root=repo_root,
                issue_date=issue_date,
                owner_receipt=transaction_receipt,
                operation_receipt=operation_receipt,
                outcome_status="completed",
                outcome_receipt_sha256=_file_sha(notification),
            )
            return result_for(operation_receipt)
        if state == "started_unresolved":
            news_grasp_recovery_transaction.complete_owned_operation(
                repo_root=repo_root,
                issue_date=issue_date,
                owner_receipt=transaction_receipt,
                operation_receipt=operation_receipt,
                outcome_status="outcome_unknown",
                outcome_receipt_sha256=decision["receiptSha256"],
            )
        raise ValueError("MINIMAL_UNBLOCKER_NOTIFICATION_OUTCOME_UNKNOWN")

    completed = subprocess.run(
        [
            sys.executable,
            str(repo_root / "tools" / "send_push.py"),
            "--record-state",
            str(notification),
            "--issue-date",
            issue_date,
        ],
        cwd=repo_root,
        capture_output=True,
        check=False,
        timeout=120,
        creationflags=(
            getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        ),
    )
    notification_state = proven_notification()
    if completed.returncode != 0 or notification_state is None:
        news_grasp_recovery_transaction.complete_owned_operation(
            repo_root=repo_root,
            issue_date=issue_date,
            owner_receipt=transaction_receipt,
            operation_receipt=operation_receipt,
            outcome_status="outcome_unknown",
            outcome_receipt_sha256=decision["receiptSha256"],
        )
        raise ValueError("MINIMAL_UNBLOCKER_NOTIFICATION_FAILED")
    news_grasp_recovery_transaction.complete_owned_operation(
        repo_root=repo_root,
        issue_date=issue_date,
        owner_receipt=transaction_receipt,
        operation_receipt=operation_receipt,
        outcome_status="completed",
        outcome_receipt_sha256=_file_sha(notification),
    )
    return result_for(operation_receipt)


def _audit_incident_terminal(
    *, issue_date: str, reason_code: str, decision: dict[str, Any] | None = None
) -> dict[str, Any]:
    evidence = str((decision or {}).get("receiptSha256") or "")
    if re.fullmatch(r"[0-9a-f]{64}", evidence) is None:
        evidence = _sha({"issueDate": issue_date, "reasonCode": reason_code})
    requested_public_status = str((decision or {}).get("publicStatus") or "incomplete")
    public_status = requested_public_status if requested_public_status in {"green", "incomplete"} else "incomplete"
    public_green = public_status == "green"
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
            "publicStatus": public_status,
            "operationState": "incident_open",
            "workPriority": (
                audit_recovery_control.PUBLIC_GREEN_FOLLOWUP_PRIORITY
                if public_green
                else audit_recovery_control.SAME_DAY_PUBLIC_RECOVERY_PRIORITY
            ),
            **(
                {"allowedAfterPublicGreen": audit_recovery_control.PUBLIC_GREEN_ALLOWED_OPERATIONS}
                if public_green
                else {
                    "allowedBeforePublicGreen": audit_recovery_control.ALLOWED_BEFORE_PUBLIC_GREEN,
                    "forbiddenBeforePublicGreen": audit_recovery_control.FORBIDDEN_BEFORE_PUBLIC_GREEN,
                }
            ),
            "owner": "News-Grasp Operations",
            "nextAction": str(
                (decision or {}).get("nextAction")
                or "resume_same_date_recovery_from_verified_stop_point"
            ),
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
            "readinessDebt": decision.get("readinessDebt") is True,
            "exitCode": 2 if decision.get("readinessDebt") is True else 0,
            "nextAction": str(decision.get("nextAction") or ""),
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


def _validate_full_recovery_policy(repo_root: Path) -> dict[str, Any]:
    path = repo_root / "config" / "news_grasp_full_recovery_policy_v2.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("FULL_RECOVERY_POLICY_INVALID") from error
    if not isinstance(value, dict):
        raise ValueError("FULL_RECOVERY_POLICY_INVALID")
    body = {key: item for key, item in value.items() if key != "receiptSha256"}
    if (
        body.get("schemaVersion") != "NEWS_GRASP_FULL_RECOVERY_POLICY_V2"
        or body.get("productId") != "News-Grasp"
        or body.get("authorityScope") != "ScheduledRecoveryFull"
        or body.get("sourceStatus") != "UserConfirmed"
        or body.get("brokerReceiptRequired") is not True
        or body.get("artifactSnapshotRequired") is not True
        or body.get("fullAllowedOnlyBeforeRunnerWithoutArtifactDelta") is not True
        or body.get("unknownRoute") != "audit_major_incident_open"
        or body.get("maxFullE2EAttempts") != 0
        or value.get("receiptSha256") != _sha(body)
    ):
        raise ValueError("FULL_RECOVERY_POLICY_INVALID")
    return value


def _ensure_control_plane_preflight_once(
    *,
    actual: ProductionBackend,
    issue_date: str,
    payload: Mapping[str, Any],
    decision: Mapping[str, Any],
    production_runtime_root: Path,
    runtime_binding: Mapping[str, Any],
    preflight_deadline: datetime,
) -> dict[str, Any]:
    """root/Python/live bindingをrunner前に検査し、allowlist repairは一回だけ行う。"""

    high_cost_binding = actual.resolve_high_cost_binding()

    def verify() -> dict[str, Any]:
        return news_grasp_control_plane.verify_control_plane(
            artifact_root=actual.repo_root,
            ops_root=actual.repo_root,
            production_runtime_root=production_runtime_root,
            live_bin_root=actual.bin_dir,
            issue_date=issue_date,
            run_intent="ScheduledRecoveryFull",
            high_cost_binding_path=Path(str(high_cost_binding["bindingPath"])),
            high_cost_binding_receipt_sha256=str(
                high_cost_binding["bindingReceiptSha256"]
            ),
        )

    observed = verify()
    if observed.get("ok") is True:
        return observed
    if observed.get("reasonCode") not in {
        "PRODUCTION_RUNTIME_DRIFT",
        "LIVE_BIN_DRIFT",
    }:
        raise ValueError(str(observed.get("reasonCode") or "RECOVERY_RUNTIME_BINDING_INVALID"))
    repair_authority_path = audit_recovery_control._issue_control_plane_repair_receipt(
        payload=dict(payload),
        decision=dict(decision),
        issue_date=issue_date,
        artifact_repo_root=actual.repo_root,
        production_runtime_root=production_runtime_root,
        live_bin_root=actual.bin_dir,
        preflight=observed,
    )
    bootstrap_path = actual.bin_dir / "news-grasp-bootstrap.ps1"
    python_path = Path(str(runtime_binding.get("pythonExe") or ""))
    if (
        not bootstrap_path.is_file()
        or bootstrap_path.is_symlink()
        or not python_path.is_file()
        or python_path.is_symlink()
    ):
        raise ValueError("CONTROL_PLANE_REPAIR_ENTRYPOINT_INVALID")
    remaining = int(
        (
            preflight_deadline.astimezone(timezone.utc)
            - datetime.now(timezone.utc)
        ).total_seconds()
    )
    if remaining <= 0:
        raise ValueError("RECOVERY_PREFLIGHT_DEADLINE_EXCEEDED")
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(bootstrap_path),
            "-UseProductionRuntime",
            "-ControlPlaneRepairOnly",
            "-RecoverOnly",
            "-ControlPlaneRepairAuthorityPath",
            str(repair_authority_path),
            "-ControlPlaneRepairArtifactRoot",
            str(actual.repo_root),
            "-RepoDir",
            str(actual.repo_root),
            "-EvidenceRepoDir",
            str(actual.repo_root),
            "-PythonExe",
            str(python_path),
            "-BinDir",
            str(actual.bin_dir),
            "-DateStamp",
            issue_date,
            "-HighCostBindingPath",
            str(high_cost_binding["bindingPath"]),
            "-HighCostBindingReceiptSha256",
            str(high_cost_binding["bindingReceiptSha256"]),
        ],
        cwd=actual.repo_root,
        capture_output=True,
        check=False,
        timeout=min(300, remaining),
        creationflags=(
            getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        ),
    )
    if completed.returncode != 0:
        raise ValueError("RECOVERY_CONTROL_PLANE_RECONCILE_FAILED")
    reprobe = verify()
    if reprobe.get("ok") is not True:
        raise ValueError("RECOVERY_RUNTIME_BINDING_INVALID")
    return reprobe


def issue_recovery_execution_receipt_v2(
    *,
    actual: ProductionBackend,
    issue_date: str,
    decision: Mapping[str, Any],
    transaction_receipt: Mapping[str, Any] | None,
) -> tuple[Path, Path]:
    """5分preflightの確定結果をrunnerが一回だけ消費するV2 receiptへsealする。"""

    if (
        not isinstance(transaction_receipt, Mapping)
        or transaction_receipt.get("schemaVersion")
        != "AUDIT_RECOVERY_TRANSACTION_V2"
        or transaction_receipt.get("issueDate") != issue_date
        or transaction_receipt.get("phase") != "owned_preflight"
    ):
        raise ValueError("AUDIT_RECOVERY_TRANSACTION_REQUIRED")
    try:
        preflight_deadline = datetime.fromisoformat(
            str(transaction_receipt.get("preflightDeadline") or "").replace(
                "Z", "+00:00"
            )
        )
    except ValueError as error:
        raise ValueError("RECOVERY_PREFLIGHT_DEADLINE_INVALID") from error
    if (
        preflight_deadline.tzinfo is None
        or datetime.now(timezone.utc) > preflight_deadline.astimezone(timezone.utc)
    ):
        raise ValueError("RECOVERY_PREFLIGHT_DEADLINE_EXCEEDED")
    branch = str(decision.get("recoveryBranch") or "")
    resume_stage = str(decision.get("resumeStage") or "")
    truth_path = Path(str(decision.get("operationalTruthPath") or ""))
    if not truth_path.is_file() or truth_path.is_symlink():
        raise ValueError("RECOVERY_OPERATIONAL_TRUTH_INVALID")
    try:
        truth = json.loads(truth_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("RECOVERY_OPERATIONAL_TRUTH_INVALID") from error
    if not isinstance(truth, dict):
        raise ValueError("RECOVERY_OPERATIONAL_TRUTH_INVALID")
    truth_body = {
        key: item for key, item in truth.items() if key != "receiptSha256"
    }
    if (
        truth.get("receiptSha256") != decision.get("operationalTruthReceiptSha256")
        or truth.get("receiptSha256") != _sha(truth_body)
    ):
        raise ValueError("RECOVERY_OPERATIONAL_TRUTH_INVALID")
    authority_path = Path(str(decision.get("scheduledAuthorityEvidencePath") or ""))
    if not authority_path.is_file() or authority_path.is_symlink():
        raise ValueError("RECOVERY_AUTHORITY_INVALID")
    authority = json.loads(authority_path.read_text(encoding="utf-8-sig"))
    failure_path = actual.resolve_failure_receipt(
        issue_date, str(decision.get("failureReceiptSha256") or "")
    )
    evidence_payload = {
        "scheduledFailureReceiptPath": str(failure_path),
        "recoveryAuthorityPath": str(authority_path),
    }
    evidence_decision = {
        "recoveryAuthorityReceiptSha256": authority.get("receiptSha256")
    }
    (
        validated_failure_path,
        failure,
        validated_authority_path,
        validated_authority,
        authority_witness,
    ) = audit_recovery_control._load_recovery_evidence_for_receipt(
        payload=evidence_payload,
        decision=evidence_decision,
        issue_date=issue_date,
        artifact_repo_root=actual.repo_root,
    )
    runtime_binding_path = actual.bin_dir / "news-grasp-recovery-runtime-binding-v1.json"
    try:
        runtime_binding = json.loads(
            runtime_binding_path.read_text(encoding="utf-8-sig")
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("RECOVERY_RUNTIME_BINDING_INVALID") from error
    if not isinstance(runtime_binding, dict):
        raise ValueError("RECOVERY_RUNTIME_BINDING_INVALID")
    production_runtime_root = Path(
        str(runtime_binding.get("productionRuntimeRoot") or "")
    )
    python_path = Path(str(runtime_binding.get("pythonExe") or ""))
    high_cost_binding_path = Path(
        str(runtime_binding.get("highCostBindingPath") or "")
    )
    if not all(
        path.is_file() and not path.is_symlink()
        for path in (runtime_binding_path, python_path, high_cost_binding_path)
    ) or not production_runtime_root.is_dir():
        raise ValueError("RECOVERY_RUNTIME_BINDING_INVALID")
    _ensure_control_plane_preflight_once(
        actual=actual,
        issue_date=issue_date,
        payload=evidence_payload,
        decision=evidence_decision,
        production_runtime_root=production_runtime_root,
        runtime_binding=runtime_binding,
        preflight_deadline=preflight_deadline,
    )
    runtime_binding = json.loads(
        runtime_binding_path.read_text(encoding="utf-8-sig")
    )
    production_runtime_root = Path(
        str(runtime_binding.get("productionRuntimeRoot") or "")
    )
    python_path = Path(str(runtime_binding.get("pythonExe") or ""))
    high_cost_binding_path = Path(
        str(runtime_binding.get("highCostBindingPath") or "")
    )
    if not all(
        path.is_file() and not path.is_symlink()
        for path in (runtime_binding_path, python_path, high_cost_binding_path)
    ) or not production_runtime_root.is_dir():
        raise ValueError("RECOVERY_RUNTIME_BINDING_INVALID")
    if datetime.now(timezone.utc) > preflight_deadline.astimezone(timezone.utc):
        raise ValueError("RECOVERY_PREFLIGHT_DEADLINE_EXCEEDED")
    if branch == "ScheduledRecoveryFull":
        _validate_full_recovery_policy(actual.repo_root)
        if (
            (truth.get("artifactDelta") or {}).get("exists") is not False
            or truth.get("scheduledAttemptReachedRunner") is not False
        ):
            raise ValueError("RECOVERY_FULL_BRANCH_FORBIDDEN")
        reservation, reservation_path = actual.reserve_recovery_capability(
            issue_date=issue_date,
            recovery_authority_path=validated_authority_path,
        )
    elif branch == "ResumeFromStage":
        if (
            re.fullmatch(
                r"[0-9a-f]{64}",
                str(truth.get("stageCheckpointReceiptSha256") or ""),
            )
            is None
            or truth.get("resumeStage") != resume_stage
        ):
            raise ValueError("RECOVERY_CHECKPOINT_INVALID")
        reservation_path = Path(str(decision.get("brokerStageDecisionPath") or ""))
        if (
            not reservation_path.is_file()
            or reservation_path.is_symlink()
            or _file_sha(reservation_path)
            != decision.get("brokerStageDecisionSha256")
        ):
            raise ValueError("RECOVERY_CAPABILITY_RESERVATION_INVALID")
        reservation = json.loads(reservation_path.read_text(encoding="utf-8-sig"))
    else:
        raise ValueError("RECOVERY_BRANCH_INVALID")
    output = (
        actual.repo_root
        / "build"
        / "recovery-authority"
        / f"{issue_date}-execution-receipt-v2.json"
    )
    receipt = news_grasp_recovery_receipts.create_recovery_execution_receipt_v2(
        transaction=dict(transaction_receipt),
        branch=branch,
        resume_from_stage=resume_stage,
        capability_reservation_path=reservation_path,
        capability_reservation_sha256=str(reservation.get("receiptSha256") or ""),
        python_path=python_path,
        python_sha256=_file_sha(python_path),
        production_runtime_binding_sha256=_file_sha(runtime_binding_path),
        live_binding_sha256=_file_sha(high_cost_binding_path),
        operational_truth_path=truth_path,
        operational_truth=truth,
        issue_date=issue_date,
        artifact_root=actual.repo_root,
        ops_root=actual.repo_root,
        production_runtime_root=production_runtime_root,
        live_bin_root=actual.bin_dir,
        runner_state_path=actual.state_path,
        runner_script_path=actual.runner_path,
        recovery_authority_path=validated_authority_path,
        recovery_authority=validated_authority,
        scheduled_failure_receipt_path=validated_failure_path,
        scheduled_failure_receipt=failure,
        authority_ledger_witness=authority_witness,
        audit_accepted_at=str(transaction_receipt.get("updatedAt")),
    )
    news_grasp_recovery_receipts.write_atomic_json(
        output, receipt, root=actual.repo_root
    )
    return output.resolve(), reservation_path.resolve()


def _execute_audit_0640_owned(
    *,
    issue_date: str,
    backend: ProductionBackend | None = None,
    command_runner: Any | None = None,
    minimal_executor: Any | None = None,
    completion_verifier: Any | None = None,
    terminal_writer: Any | None = None,
    transaction_receipt: Mapping[str, Any] | None = None,
    execution_receipt_issuer: Any | None = None,
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
    execute_minimal = minimal_executor or _execute_minimal_unblocker_owned

    def run_with_shared_deadline(command: list[str], **kwargs: Any) -> int:
        from tools.news_grasp_owned_process import run_owned_bounded

        if not isinstance(transaction_receipt, Mapping):
            raise ValueError("AUDIT_RECOVERY_TRANSACTION_REQUIRED")
        remaining = _remaining_deadline_seconds(
            transaction_receipt.get("hardDeadline")
        )
        result = run_owned_bounded(
            command,
            cwd=kwargs.get("cwd", actual.repo_root),
            timeout=min(90 * 60, remaining),
            max_output_bytes=4 * 1024 * 1024,
            env=kwargs.get("env"),
        )
        if result.output_exceeded:
            raise ValueError("RECOVERY_OWNED_PROCESS_OUTPUT_EXCEEDED")
        if result.timed_out:
            return 124
        return int(result.returncode)

    run_command = command_runner or run_with_shared_deadline
    decision: dict[str, Any] | None = None
    try:
        while True:
            decision = prepare_recovery(
                issue_date=issue_date,
                trigger="audit_0640",
                process_exit_code=1,
                backend=actual,
            )
            action = str(decision.get("action") or "")
            if action != "observe_existing_runner":
                break
            if not isinstance(transaction_receipt, Mapping):
                raise ValueError("AUDIT_RECOVERY_TRANSACTION_REQUIRED")
            try:
                remaining = _remaining_deadline_seconds(
                    transaction_receipt.get("hardDeadline")
                )
            except ValueError as error:
                if str(error) != "AUDIT_RECOVERY_HARD_DEADLINE_EXPIRED":
                    raise
                terminal = _audit_incident_terminal(
                    issue_date=issue_date,
                    reason_code="OWNED_RUNNER_HARD_DEADLINE_EXCEEDED",
                    decision={
                        **decision,
                        "publicStatus": "incomplete",
                        "nextAction": "watcher_closes_verified_owned_job_then_investigate",
                    },
                )
                write_terminal(terminal)
                return terminal
            wait = getattr(actual, "wait_for_owned_runner_change", None)
            if not callable(wait):
                raise ValueError("OWNED_RUNNER_BOUNDED_OBSERVER_UNAVAILABLE")
            wait(min(10.0, remaining))
        if action == "major_incident":
            terminal = _audit_incident_terminal(
                issue_date=issue_date,
                reason_code=str(decision.get("reasonCode") or "AUDIT_MAJOR_INCIDENT_REQUIRED"),
                decision=decision,
            )
            write_terminal(terminal)
            return terminal
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
            # V1互換入力はfail-closedで受けるが、installerや再検証を実行しない。
            # V2 producerはaction=none + readinessDebt=trueだけを発行する。
            terminal = _audit_observation_terminal(
                issue_date=issue_date,
                decision={
                    **decision,
                    "reasonCode": "READINESS_DEBT_OUT_OF_BAND_REQUIRED",
                    "nextAction": "reconcile_readiness_in_separate_transaction",
                },
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
            execute_minimal(
                Path(str(decision["decisionPath"])),
                transaction_receipt=transaction_receipt,
                repo_root=actual.repo_root,
            )
        elif action == "launch_recovery":
            issue_execution_receipt = (
                execution_receipt_issuer or issue_recovery_execution_receipt_v2
            )
            execution_receipt_path, _capability_reservation_path = (
                issue_execution_receipt(
                    actual=actual,
                    issue_date=issue_date,
                    decision=decision,
                    transaction_receipt=transaction_receipt,
                )
            )
            high_cost_binding = actual.resolve_high_cost_binding()
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
                "-RecoveryExecutionReceiptPath",
                str(execution_receipt_path),
            ]
            if decision.get("recoveryBranch") == "ResumeFromStage":
                command.extend(
                    [
                        "-ResumeFromStage",
                        str(decision["resumeStage"]),
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
    transaction_receipt: Mapping[str, Any] | None = None,
    execution_receipt_issuer: Any | None = None,
) -> dict[str, Any]:
    """互換adapter。runner ownershipはcanonical ensure transactionに委譲する。"""

    actual = backend or ProductionBackend()
    return audit_recovery_control.ensure_0640(
        issue_date=issue_date,
        trigger="daily_adapter",
        repo_root=actual.repo_root,
        executor=lambda *, issue_date, transaction_receipt: _execute_audit_0640_owned(
            issue_date=issue_date,
            backend=actual,
            command_runner=command_runner,
            minimal_executor=minimal_executor,
            completion_verifier=completion_verifier,
            terminal_writer=terminal_writer,
            transaction_receipt=transaction_receipt,
            execution_receipt_issuer=execution_receipt_issuer,
        ),
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
        or int(result.get("exitCode") or result.get("processExitCode") or 0) == 2
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
