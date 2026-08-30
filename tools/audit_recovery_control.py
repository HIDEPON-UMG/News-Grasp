from __future__ import annotations

import argparse
import contextlib
import ctypes
import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.news_grasp_operational_contract import (
    OPERATIONAL_TRUTH_ISSUER,
    POST_PUBLIC_CLOSEOUT_BLOCKER,
    POST_PUBLIC_GREEN_ALLOWED_OPERATIONS,
    finalize_audit_decision,
    select_recovery_branch_from_truth,
    validate_canonical_operational_registry,
    validate_completion_authority_receipt,
    validate_operational_truth_receipt,
)
from tools.news_grasp_owned_process import run_owned_bounded
from tools import (
    news_grasp_control_plane,
    news_grasp_recovery_closeout,
    news_grasp_recovery_freshness,
    news_grasp_recovery_receipts,
    news_grasp_high_cost_binding,
    verify_public_surface as news_grasp_verify_public_surface,
)
from tools.news_grasp_recovery_transaction import (
    RecoveryTransactionStore,
    TRANSACTION_SCHEMA,
)


AUDIT_TERMINALS = {
    "audit_normal_green",
    "audit_recovered_green",
    "audit_observation_unverified",
    "audit_major_incident_open",
}
AUDIT_DECISION_SCHEMA = "AUDIT_RECOVERY_DECISION_V2"
LEGACY_AUDIT_DECISION_SCHEMA = "AUDIT_RECOVERY_DECISION_V1"
SAME_DAY_PUBLIC_RECOVERY_PRIORITY = "same_day_public_recovery_first"
PUBLIC_GREEN_FOLLOWUP_PRIORITY = "direct_public_closeout_only"
PUBLIC_GREEN_ALLOWED_OPERATIONS = POST_PUBLIC_GREEN_ALLOWED_OPERATIONS
ALLOWED_BEFORE_PUBLIC_GREEN = [
    "scheduled_recovery",
    "minimal_recovery_unblocker",
    "escalate_major_incident",
]
FORBIDDEN_BEFORE_PUBLIC_GREEN = [
    "incident_report_polish",
    "root_cause_hardening",
    "unrelated_cleanup",
]
COMPLETION_FIELDS = (
    "quality",
    "distributionManifest",
    "publishStatus",
    "publicSurface",
    "primaryPodcast",
    "deepDivePodcast",
    "notification",
    "runnerState",
)
MAX_JSON_BYTES = 1024 * 1024
DECISION_ISSUER = "tools.audit_recovery_control"
VERIFIED_COMPLETION_ISSUER = "tools.audit_recovery_control.actual_verifiers"
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
GIT_SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
AUDIT_EVENT_HISTORY_LIMIT = 48
MAX_AUDIT_EVENT_BYTES = 64 * 1024
MAX_AUDIT_HISTORY_BYTES = AUDIT_EVENT_HISTORY_LIMIT * MAX_AUDIT_EVENT_BYTES
AUDIT_RETENTION_DAYS = 31
AUDIT_LIFECYCLE_OWNER = "News-Grasp Operations"
CAUSE_INPUT_FIELDS = (
    "source_sha256",
    "runtime_sha256",
    "config_sha256",
    "authority_sha256",
    "external_evidence_sha256",
)
CAUSE_INPUT_ALIASES = {
    "source_sha256": "sourceSha256",
    "runtime_sha256": "runtimeSha256",
    "config_sha256": "configSha256",
    "authority_sha256": "authoritySha256",
    "external_evidence_sha256": "externalEvidenceSha256",
}
CANONICAL_REPO_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_LIVE_BIN_ROOT = Path.home() / "bin"
CANONICAL_CONTROL_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_TERMINAL_ROOT = CANONICAL_REPO_ROOT / "build" / "incidents"
CANONICAL_BROKER_PATH = Path.home() / "bin" / "ai-model-spawn-broker.py"
CANONICAL_RUNNER_STATE_PATH = Path.home() / "bin" / "news-grasp-runner-state.json"
CANONICAL_AUTOMATION_COMPLETION_GUARD_PATH = (
    Path.home() / ".codex" / "automations" / "news-grasp-6-40" / "completion_guard.py"
)
GIT_HARDENED_CONFIG_ARGS = (
    "-c",
    f"core.hooksPath={os.devnull}",
    "-c",
    "core.fsmonitor=false",
    "-c",
    f"core.attributesFile={os.devnull}",
    "-c",
    "core.autocrlf=true",
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sealed(value: dict[str, Any]) -> dict[str, Any]:
    body = dict(value)
    body["receiptSha256"] = hashlib.sha256(_canonical(body)).hexdigest()
    return body


def _valid_sha256(value: object) -> bool:
    return SHA256_PATTERN.fullmatch(str(value or "")) is not None


def resolve_live_high_cost_binding(live_bin_root: Path) -> dict[str, Any]:
    """installed recovery bindingからGlobal capabilityを再解決する。"""

    live_root = live_bin_root.resolve(strict=True)
    runtime_binding_path = live_root / "news-grasp-recovery-runtime-binding-v1.json"
    if (
        not runtime_binding_path.is_file()
        or runtime_binding_path.is_symlink()
        or runtime_binding_path.stat().st_size > MAX_JSON_BYTES
    ):
        raise ValueError("HIGH_COST_WORKSPACE_BINDING_MISSING")
    try:
        runtime_binding = json.loads(
            runtime_binding_path.read_text(encoding="utf-8-sig")
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("HIGH_COST_WORKSPACE_BINDING_MISSING") from error
    if runtime_binding.get("schemaVersion") != "NEWS_GRASP_RECOVERY_RUNTIME_BINDING_V1":
        raise ValueError("HIGH_COST_WORKSPACE_BINDING_MISSING")
    binding_path = Path(str(runtime_binding.get("highCostBindingPath") or ""))
    try:
        binding_path = binding_path.resolve(strict=True)
    except OSError as error:
        raise ValueError("HIGH_COST_WORKSPACE_BINDING_MISSING") from error
    if binding_path.parent != live_root or binding_path.name != "news-grasp-high-cost-binding-v1.json":
        raise ValueError("HIGH_COST_IDENTITY_DRIFT")
    expected_file_sha = str(runtime_binding.get("highCostBindingFileSha256") or "")
    if not _valid_sha256(expected_file_sha) or _file_sha256(binding_path) != expected_file_sha:
        raise ValueError("HIGH_COST_IDENTITY_DRIFT")
    receipt = str(runtime_binding.get("highCostBindingReceiptSha256") or "")
    try:
        return news_grasp_high_cost_binding.resolve_binding(
            binding_path=binding_path,
            expected_receipt_sha256=receipt,
        )
    except news_grasp_high_cost_binding.HighCostBindingError as error:
        raise ValueError(error.reason) from error


def _cause_inputs(value: object) -> dict[str, str]:
    source = value if isinstance(value, dict) else {}
    return {
        field: str(source.get(field) or source.get(CAUSE_INPUT_ALIASES[field]) or "")
        for field in CAUSE_INPUT_FIELDS
    }


def cause_fingerprint(value: object) -> str:
    """原因へ作用するhashだけから安定fingerprintを作る。"""
    return hashlib.sha256(_canonical(_cause_inputs(value))).hexdigest()


def causal_retry_gate(previous: object, current: object) -> dict[str, Any]:
    """同じ原因は再試行せず、原因入力の変化後だけ一回許可する。"""
    previous_value = previous if isinstance(previous, dict) else {}
    current_value = current if isinstance(current, dict) else {}
    current_inputs = _cause_inputs(current_value)
    previous_inputs = _cause_inputs(previous_value)
    if not all(_valid_sha256(current_inputs[field]) for field in CAUSE_INPUT_FIELDS):
        raise ValueError("CAUSAL_RETRY_EVIDENCE_INVALID")
    if previous_value and not all(
        _valid_sha256(previous_inputs[field]) for field in CAUSE_INPUT_FIELDS
    ):
        raise ValueError("CAUSAL_RETRY_EVIDENCE_INVALID")
    previous_fingerprint = cause_fingerprint(previous_value)
    current_fingerprint = cause_fingerprint(current_value)
    changed = [
        field
        for field in CAUSE_INPUT_FIELDS
        if previous_inputs[field] != current_inputs[field]
    ]
    if not changed or previous_fingerprint == current_fingerprint:
        return {
            "allowed": False,
            "reasonCode": (
                "CAUSAL_RETRY_ALREADY_CONSUMED"
                if previous_value.get("retryConsumed") is True
                else "SAME_CAUSE_UNCHANGED"
            ),
            "causeFingerprint": current_fingerprint,
            "changedInputs": [],
        }
    return {
        "allowed": True,
        "reasonCode": "CAUSE_INPUT_CHANGED",
        "causeFingerprint": current_fingerprint,
        "changedInputs": changed,
        "retryConsumed": True,
    }


def _validate_sealed(value: object, *, schema_version: str, code: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(code)
    body = dict(value)
    receipt_sha = body.pop("receiptSha256", None)
    if body.get("schemaVersion") != schema_version:
        raise ValueError(code)
    if not _valid_sha256(receipt_sha) or receipt_sha != hashlib.sha256(_canonical(body)).hexdigest():
        raise ValueError(code)
    return dict(value)


def seal_audit_decision(decision: object) -> dict[str, Any]:
    if not isinstance(decision, dict) or decision.get("schemaVersion") not in {
        LEGACY_AUDIT_DECISION_SCHEMA,
        AUDIT_DECISION_SCHEMA,
    }:
        raise ValueError("AUDIT_DECISION_RECEIPT_INVALID")
    terminal = decision.get("terminal")
    if terminal is not None and terminal not in AUDIT_TERMINALS:
        raise ValueError("AUDIT_DECISION_RECEIPT_INVALID")
    public_status = decision.get("publicStatus")
    if public_status == "incomplete":
        if (
            decision.get("workPriority") != SAME_DAY_PUBLIC_RECOVERY_PRIORITY
            or decision.get("allowedBeforePublicGreen")
            != ALLOWED_BEFORE_PUBLIC_GREEN
            or decision.get("forbiddenBeforePublicGreen")
            != FORBIDDEN_BEFORE_PUBLIC_GREEN
            or decision.get("action")
            not in {"scheduled_recovery", "escalate_major_incident"}
        ):
            raise ValueError("AUDIT_DECISION_RECEIPT_INVALID")
    elif public_status == "green":
        if (
            decision.get("workPriority") != PUBLIC_GREEN_FOLLOWUP_PRIORITY
            or tuple(decision.get("allowedAfterPublicGreen") or ())
            != PUBLIC_GREEN_ALLOWED_OPERATIONS
        ):
            raise ValueError("AUDIT_DECISION_RECEIPT_INVALID")
    else:
        raise ValueError("AUDIT_DECISION_RECEIPT_INVALID")
    body = dict(decision)
    body["issuer"] = DECISION_ISSUER
    body.pop("receiptSha256", None)
    return _sealed(body)


def _validate_recovery_authority(
    value: object, *, issue_date: str, failure_receipt_sha256: str
) -> dict[str, Any]:
    authority = _validate_sealed(
        value,
        schema_version="SCHEDULED_RECOVERY_AUTHORITY_V1",
        code="RECOVERY_AUTHORITY_INVALID",
    )
    required_hashes = (
        "missionAuthoritySha256",
        "failureReceiptSha256",
        "taskActionSha256",
        "runnerSha256",
        "failedTaskActionSha256",
        "failedRunnerSha256",
    )
    if (
        authority.get("productId") != "News-Grasp"
        or authority.get("issueDate") != issue_date
        or authority.get("operationKind") != "scheduled_recovery"
        or authority.get("runIntent") != "ScheduledRecoveryFull"
        or authority.get("failureReceiptSha256") != failure_receipt_sha256
        or authority.get("maxExternalModelCalls") != 9
        or authority.get("maxFullE2EAttempts") != 0
        or any(authority.get(field) is not True for field in ("noFocusTheft", "noUserMonitoring", "noAutoOpen"))
        or any(not _valid_sha256(authority.get(field)) for field in required_hashes)
    ):
        raise ValueError("RECOVERY_AUTHORITY_INVALID")
    return authority


def _validate_issue_date(value: object) -> str:
    text = str(value or "")
    try:
        parsed = date.fromisoformat(text)
    except ValueError as error:
        raise ValueError("AUDIT_RECOVERY_DATE_INVALID") from error
    if parsed.isoformat() != text:
        raise ValueError("AUDIT_RECOVERY_DATE_INVALID")
    return text


def _contained_file(path_value: object, *, root: Path, code: str) -> Path:
    path = Path(str(path_value or "")).resolve()
    resolved_root = root.resolve()
    if resolved_root not in path.parents or not path.is_file() or path.is_symlink():
        raise ValueError(code)
    return path


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_python_executable() -> str:
    # resolver enforces binding.pythonExeSha256, pythonTrustAnchor and the
    # RECOVERY_PYTHON_IDENTITY_INVALID fail-closed boundary before launch.
    from tools.e2e_final_admission_bridge import resolve_production_runtime_python

    return str(
        resolve_production_runtime_python(live_bin_root=CANONICAL_LIVE_BIN_ROOT)
    )


def _typed_completion_hashes(
    *, issue_date: str, payload: dict[str, Any], evidence: object
) -> dict[str, str]:
    runtime = Path(sys.executable)
    try:
        runtime_sha = (
            _file_sha256(runtime)
            if runtime.is_file() and not runtime.is_symlink()
            else hashlib.sha256(str(runtime).encode("utf-8")).hexdigest()
        )
    except OSError:
        runtime_sha = hashlib.sha256(str(runtime).encode("utf-8")).hexdigest()
    config_sha = hashlib.sha256(
        _canonical(
            {
                "issueDate": issue_date,
                "verificationWaitSec": payload.get("verificationWaitSec", 0),
                "verificationPollSec": payload.get("verificationPollSec", 10),
                "expectedRunIntent": payload.get("expectedRunIntent", ""),
            }
        )
    ).hexdigest()
    authority_claims: list[str] = []

    def collect_authority_claims(value: object) -> None:
        if isinstance(value, dict):
            for key, item in sorted(value.items()):
                if key in {
                    "completionAuthorityId",
                    "completion_authority_id",
                    "authorityReceiptSha256",
                } and item:
                    authority_claims.append(f"{key}={item}")
                elif isinstance(item, (dict, list, tuple)):
                    collect_authority_claims(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                collect_authority_claims(item)

    collect_authority_claims(evidence)
    external_evidence_sha = hashlib.sha256(_canonical(evidence)).hexdigest()
    return {
        "sourceSha256": _file_sha256(Path(__file__)),
        "runtimeSha256": runtime_sha,
        "configSha256": config_sha,
        "authoritySha256": hashlib.sha256(
            _canonical(
                {
                    "issueDate": issue_date,
                    "authorityClaims": authority_claims,
                }
            )
        ).hexdigest(),
        "externalEvidenceSha256": external_evidence_sha,
        "evidenceHashSha256": external_evidence_sha,
    }


def _typed_public_green_readiness_red(
    *,
    issue_date: str,
    payload: dict[str, Any],
    expected_run_intent: str,
    runner_state: dict[str, Any],
    public: dict[str, Any],
    readiness: dict[str, Any],
    runner_state_path: Path,
    artifact_repo_root: Path,
) -> dict[str, Any]:
    lineage = _completion_lineage(
        issue_date=issue_date,
        run_intent=expected_run_intent,
        run_id=runner_state.get("run_id"),
        artifact_root=artifact_repo_root,
        ops_root=CANONICAL_REPO_ROOT,
    )
    evidence_seed = {
        "public": public,
        "readiness": readiness,
        "runnerStateSha256": _file_sha256(runner_state_path),
    }
    typed_hashes = _typed_completion_hashes(
        issue_date=issue_date, payload=payload, evidence=evidence_seed
    )
    authority_id = str(
        runner_state.get("completionAuthorityId")
        or public.get("completion_authority_id")
        or public.get("completionAuthorityId")
        or ""
    )
    return _sealed(
        {
            "schemaVersion": "SAME_DATE_COMPLETION_EVIDENCE_V1",
            "issuer": VERIFIED_COMPLETION_ISSUER,
            "issueDate": issue_date,
            "publishStatusIssueDate": issue_date,
            "runIntent": expected_run_intent,
            "runId": runner_state.get("run_id"),
            **lineage,
            "checks": {field: False for field in COMPLETION_FIELDS},
            "evidenceSha256": {
                field: hashlib.sha256(
                    _canonical({"field": field, **evidence_seed})
                ).hexdigest()
                for field in COMPLETION_FIELDS
            },
            "verificationStatus": "verified_incomplete",
            "publicCompletionStatus": "green",
            "nextRunReadinessStatus": "red",
            "phase": "readiness",
            "reasonCode": str(readiness.get("reason") or "READINESS_RED"),
            "failedGateIds": list(readiness.get("failedGateIds") or [
                str(readiness.get("reason") or "next_run_readiness")
            ]),
            "completionAuthorityId": authority_id,
            **typed_hashes,
        }
    )


def _verify_public_without_notification(*, issue_date: str) -> dict[str, Any] | None:
    """notification だけを除外し、それ以外の同日公開完了面を実 verifier で検証する。"""
    from tools.daily_self_heal import verify_publish_complete

    result = verify_publish_complete(
        repo_root=CANONICAL_REPO_ROOT,
        date=issue_date,
        remote="origin",
        branch="main",
        public_base_url="https://hidepon-umg.github.io/News-Grasp/",
        wait_sec=0,
        poll_sec=10,
        notification_state_path=None,
        producer_state_path=CANONICAL_RUNNER_STATE_PATH,
    )
    if result.get("ok") is not True or result.get("date") != issue_date:
        return None
    return _sealed(
        {
            "schemaVersion": "SAME_DATE_PUBLIC_WITHOUT_NOTIFICATION_V1",
            "issuer": VERIFIED_COMPLETION_ISSUER,
            "issueDate": issue_date,
            "publishManifestSha256": hashlib.sha256(_canonical(result)).hexdigest(),
            "publishCommit": result.get("publish_commit")
            or result.get("publishCommit"),
            "publicStatus": "green_without_notification",
        }
    )


def _observe_operational_truth(
    *, issue_date: str, attempt_witness: dict[str, Any]
) -> dict[str, Any]:
    artifact_paths = (
        CANONICAL_REPO_ROOT / "digest" / "Summary" / f"{issue_date}.md",
        CANONICAL_REPO_ROOT / "digest" / "DeepDive" / f"{issue_date}-DeepDive.md",
        CANONICAL_REPO_ROOT / "docs" / issue_date / "index.html",
        CANONICAL_REPO_ROOT / "data" / "distribution" / f"{issue_date}.json",
        CANONICAL_REPO_ROOT / "build" / "notification" / f"{issue_date}.json",
    )
    rows: list[dict[str, Any]] = []
    for path in artifact_paths:
        exists = path.is_file() and not path.is_symlink()
        rows.append(
            {
                "path": str(path.resolve()),
                "exists": exists,
                "sha256": _file_sha256(path) if exists else None,
            }
        )
    artifact_manifest_sha256 = hashlib.sha256(_canonical(rows)).hexdigest()

    runner_state: dict[str, Any] = {}
    runner_state_sha256: str | None = None
    if CANONICAL_RUNNER_STATE_PATH.is_file() and not CANONICAL_RUNNER_STATE_PATH.is_symlink():
        try:
            loaded = _load(
                CANONICAL_RUNNER_STATE_PATH,
                expected_root=CANONICAL_RUNNER_STATE_PATH.parent,
            )
            if isinstance(loaded, dict) and loaded.get("date") == issue_date:
                runner_state = loaded
                runner_state_sha256 = _file_sha256(CANONICAL_RUNNER_STATE_PATH)
        except (OSError, ValueError, json.JSONDecodeError):
            runner_state = {}

    reached_runner = bool(
        runner_state
        and runner_state.get("run_id")
        and runner_state.get("run_intent")
    )
    scheduled_status = str(attempt_witness.get("scheduledAttemptStatus") or "")
    stop_point_known = scheduled_status in {"reserved", "failed"}
    stop_point = (
        str(runner_state.get("phase") or runner_state.get("status") or "runner_reached")
        if reached_runner
        else "before_runner"
    )

    body: dict[str, Any] = {
        "schemaVersion": "NEWS_GRASP_OPERATIONAL_TRUTH_V1",
        "issuer": OPERATIONAL_TRUTH_ISSUER,
        "issueDate": issue_date,
        "attemptLedgerWitnessSha256": attempt_witness["receiptSha256"],
        "stopPointKnown": stop_point_known,
        "stopPoint": stop_point,
        "scheduledAttemptReachedRunner": reached_runner,
        "artifactDelta": {
            "exists": any(row["exists"] for row in rows),
            "manifestSha256": artifact_manifest_sha256,
            "presentCount": sum(1 for row in rows if row["exists"]),
            "requiredCount": len(rows),
        },
    }
    if runner_state_sha256 is not None:
        body["runnerStateSha256"] = runner_state_sha256
    resume_stage = str(runner_state.get("resumeStage") or "")
    source_admission = runner_state.get("highCostAdmissionPath")
    source_admission_file_sha = str(
        runner_state.get("highCostAdmissionSha256") or ""
    )
    source_admission_receipt_sha = ""
    checkpoint_source_valid = False
    if resume_stage in {
        "deepdive",
        "post-daily-quality",
        "post-deepdive",
        "generation-quality-repair",
    } and source_admission:
        try:
            source_admission_path = _contained_file(
                source_admission,
                root=CANONICAL_REPO_ROOT / "build",
                code="STAGE_CHECKPOINT_SOURCE_ADMISSION_INVALID",
            )
            source_admission_value = json.loads(
                source_admission_path.read_text(encoding="utf-8-sig")
            )
            source_admission_body = {
                key: value
                for key, value in source_admission_value.items()
                if key != "receiptSha256"
            }
            source_admission_receipt_sha = str(
                source_admission_value.get("receiptSha256") or ""
            )
            checkpoint_source_valid = (
                _valid_sha256(source_admission_file_sha)
                and _file_sha256(source_admission_path)
                == source_admission_file_sha
                and _valid_sha256(source_admission_receipt_sha)
                and source_admission_receipt_sha
                == hashlib.sha256(_canonical(source_admission_body)).hexdigest()
                and source_admission_body.get("schemaVersion")
                == "HIGH_COST_SCHEDULED_OPERATION_ADMISSION_V1"
                and source_admission_body.get("issueDate") == issue_date
                and source_admission_body.get("operationKind")
                == "scheduled_production"
            )
        except (OSError, ValueError, json.JSONDecodeError):
            checkpoint_source_valid = False
    if reached_runner and body["artifactDelta"]["exists"] and checkpoint_source_valid:
        body["resumeStage"] = resume_stage
        body["sourceAdmissionSha256"] = source_admission_receipt_sha
        body["sourceAdmissionFileSha256"] = source_admission_file_sha
        body["stageCheckpointReceiptSha256"] = hashlib.sha256(
            _canonical(
                {
                    "issueDate": issue_date,
                    "stopPoint": stop_point,
                    "resumeStage": resume_stage,
                    "runnerStateSha256": runner_state_sha256,
                    "sourceAdmissionSha256": source_admission_receipt_sha,
                    "sourceAdmissionFileSha256": source_admission_file_sha,
                    "artifactManifestSha256": artifact_manifest_sha256,
                    "attemptLedgerWitnessSha256": attempt_witness["receiptSha256"],
                }
            )
        ).hexdigest()
    missing_rows = [row for row in rows if row["exists"] is False]
    notification_path = (
        CANONICAL_REPO_ROOT
        / "build"
        / "notification"
        / f"{issue_date}.json"
    ).resolve()
    if (
        reached_runner
        and len(missing_rows) == 1
        and Path(missing_rows[0]["path"]).resolve() == notification_path
        and str(runner_state.get("phase") or runner_state.get("status") or "")
        in {"publish_complete", "notification_pending", "completed"}
    ):
        public_proof = _verify_public_without_notification(issue_date=issue_date)
        if public_proof is not None:
            body["minimalUnblockerPublicProofSha256"] = public_proof[
                "receiptSha256"
            ]
            body["minimalUnblockerReceiptSha256"] = hashlib.sha256(
                _canonical(
                    {
                        "schemaVersion": "NEWS_GRASP_MINIMAL_UNBLOCKER_EVIDENCE_V1",
                        "issueDate": issue_date,
                        "missingArtifact": missing_rows[0]["path"],
                        "runnerStateSha256": runner_state_sha256,
                        "artifactManifestSha256": artifact_manifest_sha256,
                        "attemptLedgerWitnessSha256": attempt_witness["receiptSha256"],
                        "publicProofSha256": public_proof["receiptSha256"],
                        "allowedOperation": "rebuild_notification_from_published_manifest",
                    }
                )
            ).hexdigest()
    return _sealed(body)


def _completion_lineage(
    *,
    issue_date: str,
    run_intent: str,
    run_id: object,
    artifact_root: object | None = None,
    ops_root: object | None = None,
) -> dict[str, str]:
    artifact_root = str(
        Path(str(artifact_root)).resolve()
        if artifact_root
        else CANONICAL_REPO_ROOT.resolve()
    )
    ops_root = str(
        Path(str(ops_root)).resolve()
        if ops_root
        else CANONICAL_RUNNER_STATE_PATH.parent.resolve()
    )
    daily_root_id = hashlib.sha256(
        f"News-Grasp|{issue_date}|{artifact_root.casefold()}|{ops_root.casefold()}".encode(
            "utf-8"
        )
    ).hexdigest()
    root_operation_id = hashlib.sha256(
        f"{daily_root_id}|{str(run_id or '')}|root-operation".encode("utf-8")
    ).hexdigest()
    producer_operation_id = hashlib.sha256(
        f"{root_operation_id}|producer|{run_intent}".encode("utf-8")
    ).hexdigest()
    lineage_receipt_sha256 = hashlib.sha256(
        (
            "NEWS_GRASP_PRODUCER_LINEAGE_V1|"
            f"{issue_date}|{artifact_root.casefold()}|{ops_root.casefold()}|"
            f"{daily_root_id}|{root_operation_id}|{producer_operation_id}|"
            f"{run_intent}|{str(run_id or '')}"
        ).encode("utf-8")
    ).hexdigest()
    return {
        "artifactRoot": artifact_root,
        "opsRoot": ops_root,
        "dailyRootId": daily_root_id,
        "rootOperationId": root_operation_id,
        "producerDailyRootId": daily_root_id,
        "producerRootOperationId": root_operation_id,
        "producerRunIntent": run_intent,
        "verifierRunIntent": run_intent,
        "producerOperationId": producer_operation_id,
        "lineageReceiptSha256": lineage_receipt_sha256,
        "verifierOperationId": hashlib.sha256(
            f"{root_operation_id}|verifier|{run_intent}".encode("utf-8")
        ).hexdigest(),
    }


def _run_bounded(
    command: list[str],
    *,
    cwd: Path,
    timeout: int,
    env_overrides: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    child_env = os.environ.copy()
    child_env["PYTHONUTF8"] = "1"
    child_env["PYTHONIOENCODING"] = "utf-8"
    if env_overrides:
        child_env.update(env_overrides)
    result = run_owned_bounded(
        command,
        cwd=cwd,
        timeout=timeout,
        max_output_bytes=MAX_JSON_BYTES,
        env=child_env,
    )
    if result.output_exceeded:
        raise ValueError("BOUNDED_SUBPROCESS_OUTPUT_EXCEEDED")
    if result.timed_out:
        raise ValueError("BOUNDED_SUBPROCESS_TIMEOUT")
    return result.returncode, result.stdout


def _git_bytes(repo_root: Path, *args: str) -> bytes:
    return_code, stdout = _run_bounded(
        [
            "git",
            *GIT_HARDENED_CONFIG_ARGS,
            "-C",
            str(repo_root),
            *args,
        ],
        cwd=repo_root,
        timeout=30,
        env_overrides={
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_ATTR_NOSYSTEM": "1",
        },
    )
    if return_code != 0:
        raise ValueError("ARTIFACT_REPO_IDENTITY_INVALID")
    return stdout


def _git_text(repo_root: Path, *args: str) -> str:
    try:
        return _git_bytes(repo_root, *args).decode("utf-8").strip()
    except UnicodeDecodeError as error:
        raise ValueError("ARTIFACT_REPO_IDENTITY_INVALID") from error


def _resolve_artifact_repo_root(payload: dict[str, Any]) -> Path:
    if not payload.get("artifactRepoRoot") and not payload.get("opsRepoRoot"):
        if not CANONICAL_REPO_ROOT.is_dir() or CANONICAL_REPO_ROOT.is_symlink():
            raise ValueError("ARTIFACT_REPO_IDENTITY_INVALID")
        return CANONICAL_REPO_ROOT.resolve()
    candidate = Path(
        str(payload.get("artifactRepoRoot") or CANONICAL_REPO_ROOT)
    ).resolve()
    if not candidate.is_dir() or candidate.is_symlink():
        raise ValueError("ARTIFACT_REPO_IDENTITY_INVALID")
    top_level = Path(_git_text(candidate, "rev-parse", "--show-toplevel")).resolve()
    if top_level != candidate:
        raise ValueError("ARTIFACT_REPO_IDENTITY_INVALID")
    canonical_origin = _git_text(CANONICAL_REPO_ROOT, "remote", "get-url", "origin")
    candidate_origin = _git_text(candidate, "remote", "get-url", "origin")
    if not canonical_origin or candidate_origin != canonical_origin:
        raise ValueError("ARTIFACT_REPO_IDENTITY_INVALID")
    canonical_common_dir = Path(
        _git_text(
            CANONICAL_REPO_ROOT,
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        )
    ).resolve()
    candidate_common_dir = Path(
        _git_text(
            candidate,
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        )
    ).resolve()
    if candidate_common_dir != canonical_common_dir:
        raise ValueError("ARTIFACT_REPO_IDENTITY_INVALID")
    ops_root = Path(
        str(payload.get("opsRepoRoot") or CANONICAL_REPO_ROOT)
    ).resolve()
    if ops_root != CANONICAL_REPO_ROOT.resolve():
        raise ValueError("OPS_REPO_IDENTITY_INVALID")
    return candidate


def _validate_artifact_executable_tree(artifact_repo_root: Path) -> str:
    """実行対象codeはtrusted remote HEADと一致するclean treeに限定する。"""
    for startup_name in ("sitecustomize.py", "usercustomize.py"):
        startup_path = artifact_repo_root / startup_name
        if startup_path.exists() or startup_path.is_symlink():
            raise ValueError("ARTIFACT_EXECUTABLE_TREE_INVALID")
    filter_return_code, filter_stdout = _run_bounded(
        [
            "git",
            *GIT_HARDENED_CONFIG_ARGS,
            "-C",
            str(artifact_repo_root),
            "config",
            "--includes",
            "--local",
            "--name-only",
            "--get-regexp",
            r"^filter\..*\.(clean|smudge|process)$",
        ],
        cwd=artifact_repo_root,
        timeout=30,
        env_overrides={
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_ATTR_NOSYSTEM": "1",
        },
    )
    if filter_return_code not in {0, 1} or filter_stdout.strip():
        raise ValueError("ARTIFACT_EXECUTABLE_TREE_INVALID")
    common_dir = Path(
        _git_text(
            artifact_repo_root,
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        )
    ).resolve()
    info_attributes = common_dir / "info" / "attributes"
    if info_attributes.exists() or info_attributes.is_symlink():
        raise ValueError("ARTIFACT_EXECUTABLE_TREE_INVALID")
    artifact_head = _git_text(artifact_repo_root, "rev-parse", "HEAD")
    trusted_head = _git_text(
        CANONICAL_REPO_ROOT, "rev-parse", "refs/remotes/origin/main"
    )
    if GIT_SHA_PATTERN.fullmatch(artifact_head) is None:
        raise ValueError("ARTIFACT_EXECUTABLE_TREE_INVALID")
    if artifact_head != trusted_head:
        merge_base = _git_text(
            artifact_repo_root, "merge-base", artifact_head, trusted_head
        )
        if merge_base != trusted_head:
            raise ValueError("ARTIFACT_EXECUTABLE_TREE_INVALID")
    untracked_executables = _git_text(
        artifact_repo_root,
        "ls-files",
        "--others",
        "--exclude-standard",
        "--",
        "scripts",
        "tools",
        "schemas",
        "prompts",
        "pyproject.toml",
        "pytest.ini",
        "requirements.txt",
        "requirements-dev.txt",
        ".gitattributes",
    )
    if untracked_executables:
        raise ValueError("ARTIFACT_EXECUTABLE_TREE_INVALID")
    index_tags = _git_text(
        artifact_repo_root,
        "ls-files",
        "-v",
        "--",
        "scripts",
        "tools",
        "schemas",
        "prompts",
        "pyproject.toml",
        "pytest.ini",
        "requirements.txt",
        "requirements-dev.txt",
        ".gitattributes",
    )
    if any(not line.startswith("H ") for line in index_tags.splitlines() if line):
        raise ValueError("ARTIFACT_EXECUTABLE_TREE_INVALID")
    tree = _git_bytes(
        CANONICAL_REPO_ROOT,
        "ls-tree",
        "-r",
        "-z",
        "--full-tree",
        artifact_head,
        "--",
        "scripts",
        "tools",
        "schemas",
        "prompts",
        "pyproject.toml",
        "pytest.ini",
        "requirements.txt",
        "requirements-dev.txt",
        ".gitattributes",
    )
    total_bytes = 0
    entries = [entry for entry in tree.split(b"\0") if entry]
    if not entries or len(entries) > 5000:
        raise ValueError("ARTIFACT_EXECUTABLE_TREE_INVALID")
    tree_paths: set[str] = set()
    for entry in entries:
        try:
            metadata, raw_relative = entry.split(b"\t", 1)
            mode, object_type, expected_blob = metadata.decode("ascii").split(" ")
            relative = raw_relative.decode("utf-8")
        except (ValueError, UnicodeDecodeError) as error:
            raise ValueError("ARTIFACT_EXECUTABLE_TREE_INVALID") from error
        if object_type != "blob" or mode not in {"100644", "100755"}:
            raise ValueError("ARTIFACT_EXECUTABLE_TREE_INVALID")
        tree_paths.add(relative)
        candidate = artifact_repo_root.joinpath(*Path(relative).parts)
        if not candidate.is_file() or candidate.is_symlink():
            raise ValueError("ARTIFACT_EXECUTABLE_TREE_INVALID")
        data = candidate.read_bytes()
        total_bytes += len(data)
        if len(data) > 16 * 1024 * 1024 or total_bytes > 512 * 1024 * 1024:
            raise ValueError("ARTIFACT_EXECUTABLE_TREE_INVALID")
        hash_return_code, hash_stdout = _run_bounded(
            [
                "git",
                *GIT_HARDENED_CONFIG_ARGS,
                "-C",
                str(artifact_repo_root),
                "hash-object",
                "--path",
                relative,
                str(candidate),
            ],
            cwd=artifact_repo_root,
            timeout=30,
            env_overrides={"GIT_NO_REPLACE_OBJECTS": "1"},
        )
        if hash_return_code != 0:
            raise ValueError("ARTIFACT_EXECUTABLE_TREE_INVALID")
        actual_blob = hash_stdout.decode("utf-8").strip()
        if actual_blob != expected_blob:
            raise ValueError("ARTIFACT_EXECUTABLE_TREE_INVALID")
    worktree_attributes = artifact_repo_root / ".gitattributes"
    if (
        (worktree_attributes.exists() or worktree_attributes.is_symlink())
        and ".gitattributes" not in tree_paths
    ):
        raise ValueError("ARTIFACT_EXECUTABLE_TREE_INVALID")
    return artifact_head


def _inspect_attempt_via_broker(*, issue_date: str) -> dict[str, Any]:
    if not CANONICAL_BROKER_PATH.is_file():
        raise ValueError("SCHEDULED_ATTEMPT_BROKER_UNAVAILABLE")
    return_code, stdout = _run_bounded(
        [
            _canonical_python_executable(),
            str(CANONICAL_BROKER_PATH),
            "inspect-news-grasp-attempt",
            "--issue-date",
            issue_date,
        ],
        cwd=CANONICAL_REPO_ROOT,
        timeout=30,
    )
    if return_code != 0:
        raise ValueError("SCHEDULED_ATTEMPT_LEDGER_INVALID")
    try:
        witness = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("SCHEDULED_ATTEMPT_LEDGER_INVALID") from error
    witness = _validate_sealed(
        witness,
        schema_version="SCHEDULED_ATTEMPT_LEDGER_WITNESS_V1",
        code="SCHEDULED_ATTEMPT_LEDGER_INVALID",
    )
    if (
        witness.get("productId") != "News-Grasp"
        or witness.get("issueDate") != issue_date
        or witness.get("scheduledAttemptStatus") not in {"reserved", "failed"}
        or witness.get("recoveryAttemptStatus") not in {"not_started", "started"}
        or not isinstance(witness.get("scheduledEventSequence"), int)
        or int(witness.get("scheduledEventSequence")) <= 0
        or not _valid_sha256(witness.get("scheduledEventHash"))
    ):
        raise ValueError("SCHEDULED_ATTEMPT_LEDGER_INVALID")
    if witness["scheduledAttemptStatus"] == "failed" and (
        not _valid_sha256(witness.get("failureReceiptSha256"))
        or not isinstance(witness.get("failureEventSequence"), int)
        or int(witness.get("failureEventSequence")) <= 0
        or not _valid_sha256(witness.get("failureEventHash"))
    ):
        raise ValueError("SCHEDULED_ATTEMPT_LEDGER_INVALID")
    if witness["recoveryAttemptStatus"] == "started" and (
        not _valid_sha256(witness.get("recoveryAuthorityReceiptSha256"))
        or not isinstance(witness.get("recoveryEventSequence"), int)
        or int(witness.get("recoveryEventSequence")) <= 0
        or not _valid_sha256(witness.get("recoveryEventHash"))
    ):
        raise ValueError("SCHEDULED_ATTEMPT_LEDGER_INVALID")
    return witness


def _validate_scheduled_failure_path(
    scheduled: object, *, issue_date: str, evidence_repo_root: Path | None = None
) -> dict[str, Any]:
    if not isinstance(scheduled, dict) or scheduled.get("status") != "failed":
        raise ValueError("SCHEDULED_ATTEMPT_EVIDENCE_INVALID")
    evidence_build_root = (evidence_repo_root or CANONICAL_REPO_ROOT) / "build"
    failure_path = _contained_file(
        scheduled.get("failureReceiptPath"),
        root=evidence_build_root,
        code="SCHEDULED_ATTEMPT_EVIDENCE_INVALID",
    )
    failure = _validate_sealed(
        _load(failure_path, expected_root=evidence_build_root),
        schema_version="SCHEDULED_FAILURE_RECEIPT_V1",
        code="SCHEDULED_ATTEMPT_EVIDENCE_INVALID",
    )
    if (
        failure.get("issueDate") != issue_date
        or failure.get("scheduledAttemptStatus") != "failed"
    ):
        raise ValueError("SCHEDULED_ATTEMPT_EVIDENCE_INVALID")
    return failure


def _validate_recovery_authority_via_broker(
    *, issue_date: str, authority_path_value: object, failure_receipt_sha256: str,
    evidence_repo_root: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    evidence_build_root = (evidence_repo_root or CANONICAL_REPO_ROOT) / "build"
    authority_path = _contained_file(
        authority_path_value,
        root=evidence_build_root,
        code="RECOVERY_AUTHORITY_INVALID",
    )
    if not CANONICAL_BROKER_PATH.is_file():
        raise ValueError("RECOVERY_AUTHORITY_BROKER_UNAVAILABLE")
    return_code, stdout = _run_bounded(
        [
            _canonical_python_executable(),
            str(CANONICAL_BROKER_PATH),
            "validate-news-grasp-recovery-authority",
            "--issue-date",
            issue_date,
            "--authority-evidence",
            str(authority_path),
            "--failure-receipt-sha256",
            failure_receipt_sha256,
        ],
        cwd=CANONICAL_REPO_ROOT,
        timeout=30,
    )
    if return_code != 0:
        raise ValueError("RECOVERY_AUTHORITY_LEDGER_INVALID")
    try:
        witness = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("RECOVERY_AUTHORITY_LEDGER_INVALID") from error
    witness = _validate_sealed(
        witness,
        schema_version="SCHEDULED_RECOVERY_AUTHORITY_LEDGER_WITNESS_V1",
        code="RECOVERY_AUTHORITY_LEDGER_INVALID",
    )
    authority = _validate_recovery_authority(
        _load(authority_path, expected_root=evidence_build_root),
        issue_date=issue_date,
        failure_receipt_sha256=failure_receipt_sha256,
    )
    if (
        witness.get("issueDate") != issue_date
        or witness.get("failureReceiptSha256") != failure_receipt_sha256
        or witness.get("authorityReceiptSha256") != authority.get("receiptSha256")
        or not isinstance(witness.get("ledgerEventSequence"), int)
        or int(witness.get("ledgerEventSequence")) <= 0
        or not _valid_sha256(witness.get("ledgerEventHash"))
    ):
        raise ValueError("RECOVERY_AUTHORITY_LEDGER_INVALID")
    return authority, witness


def _verify_same_date_completion(
    *, issue_date: str, payload: dict[str, Any], expected_run_intent: str
) -> dict[str, Any] | None:
    runner_state_path = CANONICAL_RUNNER_STATE_PATH
    if not runner_state_path.is_file() or runner_state_path.is_symlink():
        raise ValueError("RUNNER_STATE_EVIDENCE_INVALID")
    runner_state = _load(
        runner_state_path, expected_root=CANONICAL_RUNNER_STATE_PATH.parent
    )
    if (
        runner_state.get("date") != issue_date
        or runner_state.get("status") != "publish_complete"
        or runner_state.get("exit_code") != 0
        or runner_state.get("run_intent") != expected_run_intent
    ):
        return None
    artifact_repo_root = _resolve_artifact_repo_root(
        {
            "artifactRepoRoot": str(runner_state.get("artifactRoot") or ""),
            "opsRepoRoot": str(CANONICAL_REPO_ROOT),
        }
    )
    _validate_artifact_executable_tree(CANONICAL_REPO_ROOT)
    wait_sec = int(payload.get("verificationWaitSec", 0))
    poll_sec = int(payload.get("verificationPollSec", 10))
    if wait_sec < 0 or wait_sec > 600 or poll_sec < 1 or poll_sec > 60:
        raise ValueError("COMPLETION_VERIFICATION_BUDGET_INVALID")
    quality_return_code, quality_stdout = _run_bounded(
        [
            sys.executable,
            "-P",
            "-m",
            "tools.validate_daily_quality",
            "--date",
            issue_date,
            "--require-deepdive",
            "--json",
        ],
        cwd=artifact_repo_root,
        timeout=180,
        env_overrides={
            "PYTHONPATH": str(CANONICAL_REPO_ROOT),
            "PYTHONNOUSERSITE": "1",
        },
    )
    if quality_return_code != 0:
        return None
    try:
        quality = json.loads(quality_stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    from tools import daily_self_heal
    from tools.daily_self_heal import verify_publish_complete

    publish_kwargs = {
        "repo_root": artifact_repo_root,
        "ops_repo_root": CANONICAL_REPO_ROOT,
        "date": issue_date,
        "remote": "origin",
        "branch": "main",
        "public_base_url": "https://hidepon-umg.github.io/News-Grasp/",
        "wait_sec": wait_sec,
        "poll_sec": poll_sec,
        "primary_podcast_state_path": (
            artifact_repo_root / "build" / "youtube-podcast" / "uploads.json"
        ),
        "deepdive_podcast_state_path": (
            artifact_repo_root
            / "build"
            / "youtube-podcast-deepdive"
            / "uploads.json"
        ),
        "notification_state_path": (
            artifact_repo_root / "build" / "notification" / f"{issue_date}.json"
        ),
        "producer_state_path": runner_state_path,
    }
    primary_exception: Exception | None = None
    preserved_authority: dict[str, Any] | None = None
    try:
        publish = verify_publish_complete(**publish_kwargs)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        primary_exception = error
        try:
            publish = daily_self_heal.verify_public_completion(**publish_kwargs)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as public_error:
            try:
                preserved_authority = load_completion_authority_receipt(issue_date)
            except ValueError:
                preserved_authority = None
            publish = {
                "ok": False,
                "date": issue_date,
                "public_status": "green" if preserved_authority else "unverified",
                "publicCompletionStatus": (
                    "green" if preserved_authority else "unverified"
                ),
                "verification_unavailable": True,
                "reason": "PUBLIC_ORACLE_EXCEPTION",
                "exceptionType": type(public_error).__name__,
            }
    if primary_exception is not None:
        lineage = _completion_lineage(
            issue_date=issue_date,
            run_intent=expected_run_intent,
            run_id=runner_state.get("run_id"),
            artifact_root=artifact_repo_root,
            ops_root=CANONICAL_REPO_ROOT,
        )
        evidence_seed = {
            "quality": quality,
            "publish": publish,
            "runnerStateSha256": _file_sha256(runner_state_path),
            "primaryException": type(primary_exception).__name__,
        }
        typed_hashes = _typed_completion_hashes(
            issue_date=issue_date, payload=payload, evidence=evidence_seed
        )
        fresh_public_green = (
            publish.get("ok") is True
            and publish.get("date") == issue_date
            and str(
                publish.get("publicCompletionStatus")
                or publish.get("public_status")
                or ""
            )
            == "green"
        )
        selected_authority_id = (
            (
                publish.get("completion_authority_id")
                or publish.get("completionAuthorityId")
            )
            if fresh_public_green
            else (preserved_authority or {}).get("completionAuthorityId")
        )
        authority_id = str(selected_authority_id or "")
        public_completion_status = (
            "green" if fresh_public_green or preserved_authority else "unverified"
        )
        return _sealed(
            {
                "schemaVersion": "SAME_DATE_COMPLETION_EVIDENCE_V1",
                "issuer": VERIFIED_COMPLETION_ISSUER,
                "issueDate": issue_date,
                "publishStatusIssueDate": issue_date,
                "runIntent": expected_run_intent,
                "runId": runner_state.get("run_id"),
                **lineage,
                "checks": {field: False for field in COMPLETION_FIELDS},
                "evidenceSha256": {
                    field: hashlib.sha256(
                        _canonical({"field": field, **evidence_seed})
                    ).hexdigest()
                    for field in COMPLETION_FIELDS
                },
                "verificationStatus": "verification_unavailable",
                "publicCompletionStatus": str(
                    public_completion_status
                ),
                "nextRunReadinessStatus": "unverified",
                "phase": "public_oracle",
                "reasonCode": "PRIMARY_VERIFIER_EXCEPTION",
                "failedGateIds": ["primary_completion_verifier"],
                "completionAuthorityId": authority_id,
                **typed_hashes,
            }
        )

    if publish.get("ok") is not True or publish.get("date") != issue_date:
        nested_public = publish.get("publish")
        readiness = publish.get("live_runner_readiness")
        if (
            isinstance(nested_public, dict)
            and nested_public.get("ok") is True
            and isinstance(readiness, dict)
            and readiness.get("ok") is not True
        ):
            return _typed_public_green_readiness_red(
                issue_date=issue_date,
                payload=payload,
                expected_run_intent=expected_run_intent,
                runner_state=runner_state,
                public=nested_public,
                readiness=readiness,
                runner_state_path=runner_state_path,
                artifact_repo_root=artifact_repo_root,
            )
        return None
    completion_guard: dict[str, Any] = {}
    if expected_run_intent == "ScheduledRecoveryFull":
        finalization_receipt_path = str(
            runner_state.get("recovery_finalization_receipt_path") or ""
        )
        if finalization_receipt_path:
            from tools import news_grasp_completion_guard

            try:
                completion_guard, validated_finalization = (
                    news_grasp_completion_guard.evaluate_finalization_receipt(
                        Path(finalization_receipt_path),
                        artifact_root=artifact_repo_root,
                        ops_root=CANONICAL_REPO_ROOT,
                        production_runtime_root=(
                            Path.home() / ".news-grasp-runtime" / "production-runtime"
                        ),
                        live_bin_root=CANONICAL_RUNNER_STATE_PATH.parent,
                        runner_state_path=CANONICAL_RUNNER_STATE_PATH,
                        runner_script_path=(
                            CANONICAL_REPO_ROOT
                            / "scripts"
                            / "ops"
                            / "news-grasp-runner.ps1"
                        ),
                    )
                )
            except (OSError, ValueError, RuntimeError):
                return None
            if (
                completion_guard.get("ok") is not True
                or completion_guard.get("issueDate") != issue_date
                or validated_finalization.get("receiptSha256")
                != runner_state.get("recovery_finalization_receipt_sha256")
            ):
                return None
        else:
            legacy_guard_path = (
                artifact_repo_root
                / "build"
                / "publish-complete"
                / f"{issue_date}.automation-guard.json"
            )
            try:
                legacy_guard = _load(
                    legacy_guard_path,
                    expected_root=artifact_repo_root / "build",
                )
            except (OSError, ValueError, json.JSONDecodeError):
                return None
            if (
                legacy_guard.get("schemaVersion")
                != "NEWS_GRASP_640_COMPLETION_GUARD_V1"
                or legacy_guard.get("ok") is not True
                or legacy_guard.get("issueDate") != issue_date
                or legacy_guard.get("scheduled_attempt_status")
                != "failed_then_recovered"
                or legacy_guard.get("recovery_attempt_status") != "succeeded"
                or legacy_guard.get("public_status") != "green"
                or legacy_guard.get("runner_status") != "publish_complete"
            ):
                return None
            completion_guard = legacy_guard
    evidence_seed = {
        "quality": quality,
        "publish": publish,
        "completionGuard": completion_guard,
        "runnerStateSha256": _file_sha256(runner_state_path),
    }
    typed_hashes = _typed_completion_hashes(
        issue_date=issue_date, payload=payload, evidence=evidence_seed
    )
    completion_authority_id = str(
        runner_state.get("completionAuthorityId")
        or publish.get("completion_authority_id")
        or publish.get("completionAuthorityId")
        or ""
    )
    return _sealed(
        {
            "schemaVersion": "SAME_DATE_COMPLETION_EVIDENCE_V1",
            "issuer": VERIFIED_COMPLETION_ISSUER,
            "issueDate": issue_date,
            "publishStatusIssueDate": issue_date,
            "runIntent": expected_run_intent,
            "runId": runner_state.get("run_id"),
            "artifactRoot": publish["artifactRoot"],
            "opsRoot": publish["opsRoot"],
            "dailyRootId": publish["dailyRootId"],
            "rootOperationId": publish["rootOperationId"],
            "producerDailyRootId": publish["dailyRootId"],
            "producerRootOperationId": publish["rootOperationId"],
            "producerRunIntent": publish["producerRunIntent"],
            "producerOperationId": publish["producerOperationId"],
            "lineageReceiptSha256": publish["lineageReceiptSha256"],
            "verifierRunIntent": expected_run_intent,
            "verifierOperationId": hashlib.sha256(
                (
                    f"{publish['rootOperationId']}|verifier|"
                    f"{expected_run_intent}"
                ).encode("utf-8")
            ).hexdigest(),
            "checks": {field: True for field in COMPLETION_FIELDS},
            "evidenceSha256": {
                field: hashlib.sha256(
                    _canonical({"field": field, **evidence_seed})
                ).hexdigest()
                for field in COMPLETION_FIELDS
            },
            "verificationStatus": "verified_green",
            "publicCompletionStatus": "green",
            "nextRunReadinessStatus": "green",
            "phase": "public_and_readiness",
            "reasonCode": "VERIFIED_GREEN",
            "failedGateIds": [],
            "completionAuthorityId": completion_authority_id,
            **typed_hashes,
        }
    )


def same_date_completion_green(issue_date: str, completion: object) -> bool:
    try:
        value = _validate_sealed(
            completion,
            schema_version="SAME_DATE_COMPLETION_EVIDENCE_V1",
            code="SAME_DATE_COMPLETION_EVIDENCE_INVALID",
        )
    except ValueError:
        return False
    if value.get("issuer") != VERIFIED_COMPLETION_ISSUER:
        return False
    if value.get("issueDate") != issue_date or value.get("publishStatusIssueDate") != issue_date:
        return False
    if value.get("verificationStatus") not in {None, "verified_green"}:
        return False
    if value.get("publicCompletionStatus") not in {None, "green"}:
        return False
    if value.get("nextRunReadinessStatus") not in {None, "green"}:
        return False
    expected_lineage = _completion_lineage(
        issue_date=issue_date,
        run_intent=str(value.get("runIntent") or ""),
        run_id=value.get("runId"),
        artifact_root=value.get("artifactRoot"),
        ops_root=value.get("opsRoot"),
    )
    for field in (
        "artifactRoot",
        "opsRoot",
        "dailyRootId",
        "rootOperationId",
        "producerDailyRootId",
        "producerRootOperationId",
        "producerRunIntent",
        "verifierRunIntent",
        "producerOperationId",
        "lineageReceiptSha256",
        "verifierOperationId",
    ):
        if value.get(field) != expected_lineage[field]:
            return False
    checks = value.get("checks")
    evidence_sha256 = value.get("evidenceSha256")
    if not isinstance(checks, dict) or not isinstance(evidence_sha256, dict):
        return False
    return all(
        checks.get(field) is True and _valid_sha256(evidence_sha256.get(field))
        for field in COMPLETION_FIELDS
    )


def classify_repair_payload(payload: object) -> str:
    if not isinstance(payload, dict):
        return "incident_required"
    repair_class = str(payload.get("repair_class") or "")
    failure_status = str(payload.get("failure_status") or "")
    if repair_class in {
        "deterministic_handler",
        "llm_generate_missing_artifact",
        "llm_rewrite_existing_artifact",
    } and failure_status not in {
        "blocked_unknown_repair_class",
        "blocked_external_readiness",
    }:
        return "recoverable"
    return "incident_required"


def validate_recovery_execution_manifest(
    manifest: object,
    *,
    issue_date: str | None = None,
    authority_receipt_sha256: str | None = None,
    artifact_repo_head: str | None = None,
    runner_sha256: str | None = None,
) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        raise ValueError("HUMAN_IMPACT_CONTRACT_INVALID")
    if (
        manifest.get("runIntent") != "ScheduledRecoveryFull"
        or manifest.get("maxExternalModelCalls") != 9
        or manifest.get("maxFullE2EAttempts") != 0
        or manifest.get("noFocusTheft") is not True
        or manifest.get("noUserMonitoring") is not True
        or manifest.get("noAutoOpen") is not True
    ):
        raise ValueError("HUMAN_IMPACT_CONTRACT_INVALID")
    if issue_date is not None and (
        manifest.get("issueDate") != issue_date
        or manifest.get("recoveryAuthorityReceiptSha256")
        != authority_receipt_sha256
        or manifest.get("artifactRepoHead") != artifact_repo_head
        or manifest.get("runnerSha256") != runner_sha256
        or not _valid_sha256(manifest.get("recoveryAuthorityReceiptSha256"))
        or GIT_SHA_PATTERN.fullmatch(str(manifest.get("artifactRepoHead") or ""))
        is None
        or not _valid_sha256(manifest.get("runnerSha256"))
    ):
        raise ValueError("RECOVERY_EXECUTION_BINDING_INVALID")
    return dict(manifest)


def select_recovery_run_intent(
    *, issue_date: str, artifacts: dict[str, bool]
) -> str:
    if not issue_date or not artifacts or not all(artifacts.values()):
        return "ScheduledRecoveryFull"
    return "RecoverOnly"


def _incident(
    *,
    issue_date: str,
    scheduled_status: str,
    recovery_status: str,
    reason_code: str,
) -> dict[str, Any]:
    return seal_audit_decision({
        "schemaVersion": AUDIT_DECISION_SCHEMA,
        "issueDate": issue_date,
        "classification": "incident_required",
        "action": "escalate_major_incident",
        "terminal": "audit_major_incident_open",
        "reasonCode": reason_code,
        "scheduledAttemptStatus": scheduled_status,
        "recoveryAttemptStatus": recovery_status,
        "publicStatus": "incomplete",
        "operationState": "incident_open",
        "workPriority": SAME_DAY_PUBLIC_RECOVERY_PRIORITY,
        "allowedBeforePublicGreen": ALLOWED_BEFORE_PUBLIC_GREEN,
        "forbiddenBeforePublicGreen": FORBIDDEN_BEFORE_PUBLIC_GREEN,
        "owner": "News-Grasp Operations",
        "nextAction": "resume_same_date_recovery_from_verified_stop_point",
        "evidenceSha256": hashlib.sha256(
            _canonical(
                {
                    "issueDate": issue_date,
                    "scheduledAttemptStatus": scheduled_status,
                    "recoveryAttemptStatus": recovery_status,
                    "reasonCode": reason_code,
                }
            )
        ).hexdigest(),
        "sourceDecision": {
            "issueDate": issue_date,
            "scheduledAttemptStatus": scheduled_status,
            "recoveryAttemptStatus": recovery_status,
            "reasonCode": reason_code,
        },
        "completionEvidence": None,
    })


def _lexical_absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _same_lexical_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.fspath(_lexical_absolute(left))) == os.path.normcase(
        os.fspath(_lexical_absolute(right))
    )


def _is_reparse_or_symlink(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(metadata.st_mode):
        return True
    attributes = getattr(metadata, "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _validated_managed_root(
    *,
    repo_root: Path,
    candidate_root: Path,
    relative_parts: tuple[str, ...],
    code: str,
    create: bool = True,
) -> Path:
    repo = _lexical_absolute(repo_root)
    expected = repo.joinpath(*relative_parts)
    if not _same_lexical_path(candidate_root, expected):
        raise ValueError(code)
    if not repo.is_dir() or _is_reparse_or_symlink(repo):
        raise ValueError(code)
    current = repo
    for part in relative_parts:
        current = current / part
        if current.exists():
            if not current.is_dir() or _is_reparse_or_symlink(current):
                raise ValueError(code)
        elif create:
            current.mkdir(exist_ok=True)
            if not current.is_dir() or _is_reparse_or_symlink(current):
                raise ValueError(code)
        else:
            return expected
    try:
        resolved_repo = repo.resolve(strict=True)
        resolved_expected = expected.resolve(strict=True)
    except OSError as error:
        raise ValueError(code) from error
    if resolved_expected != expected or resolved_repo not in resolved_expected.parents:
        raise ValueError(code)
    return expected


def _validated_terminal_root(*, create: bool = True) -> Path:
    return _validated_managed_root(
        repo_root=CANONICAL_REPO_ROOT,
        candidate_root=CANONICAL_TERMINAL_ROOT,
        relative_parts=("build", "incidents"),
        code="AUDIT_TERMINAL_OUTPUT_INVALID",
        create=create,
    )


@contextlib.contextmanager
def _pinned_directory(
    path: Path, *, invalid_code: str, anchor: Path | None = None
):
    """anchorからmanaged rootまでの全component差替えを封止する。"""
    root = _lexical_absolute(path)
    anchor_path = _lexical_absolute(anchor or path)
    try:
        relative = root.relative_to(anchor_path)
    except ValueError as error:
        raise ValueError(invalid_code) from error
    components = [anchor_path]
    current = anchor_path
    for part in relative.parts:
        current = current / part
        components.append(current)
    if any(
        not component.is_dir() or _is_reparse_or_symlink(component)
        for component in components
    ):
        raise ValueError(invalid_code)

    if os.name != "nt":
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(
            os, "O_NOFOLLOW", 0
        )
        descriptors: list[int] = []
        try:
            for component in components:
                descriptors.append(os.open(component, flags))
            yield
        except OSError as error:
            raise ValueError(invalid_code) from error
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)
        return

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateFileW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    kernel32.CreateFileW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handles: list[int] = []
    try:
        for component in components:
            directory_handle = kernel32.CreateFileW(
                str(component),
                0x0080,
                0x0001 | 0x0002,
                None,
                3,
                0x02000000 | 0x00200000,
                None,
            )
            if directory_handle in (0, ctypes.c_void_p(-1).value):
                raise ValueError(invalid_code)
            handles.append(directory_handle)
            pin_path = component / ".managed-root.pin"
            pin_handle = kernel32.CreateFileW(
                str(pin_path),
                0x80000000 | 0x40000000,
                0x0001 | 0x0002,
                None,
                4,
                0x00000002 | 0x00200000,
                None,
            )
            if pin_handle in (0, ctypes.c_void_p(-1).value):
                raise ValueError(invalid_code)
            handles.append(pin_handle)
            if _is_reparse_or_symlink(component) or _is_reparse_or_symlink(
                pin_path
            ):
                raise ValueError(invalid_code)
        yield
    finally:
        for handle in reversed(handles):
            kernel32.CloseHandle(handle)


@contextlib.contextmanager
def _exclusive_file_lock(
    lock_path: Path,
    *,
    busy_code: str,
    invalid_code: str,
    wait_timeout_sec: float = 0.0,
):
    if lock_path.exists() and _is_reparse_or_symlink(lock_path):
        raise ValueError(invalid_code)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    stream = lock_path.open("a+b")
    locked = False
    try:
        if _is_reparse_or_symlink(lock_path) or not lock_path.is_file():
            raise ValueError(invalid_code)
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
            os.fsync(stream.fileno())
        stream.seek(0)
        deadline = time.monotonic() + max(0.0, wait_timeout_sec)
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(
                        stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB
                    )
                locked = True
                break
            except OSError as error:
                if time.monotonic() >= deadline:
                    raise ValueError(busy_code) from error
                time.sleep(0.01)
                stream.seek(0)
        yield
    finally:
        if locked:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        stream.close()


@contextlib.contextmanager
def _locked_directory(path: Path):
    root = _validated_terminal_root()
    if not _same_lexical_path(path, root):
        raise ValueError("AUDIT_TERMINAL_OUTPUT_INVALID")
    with _pinned_directory(
        root,
        anchor=CANONICAL_REPO_ROOT,
        invalid_code="AUDIT_TERMINAL_OUTPUT_INVALID",
    ):
        with _exclusive_file_lock(
            root / ".audit-terminal.lock",
            busy_code="AUDIT_TERMINAL_BUSY",
            invalid_code="AUDIT_TERMINAL_OUTPUT_INVALID",
        ):
            _validated_terminal_root()
            yield


def decide_audit_recovery(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("AUDIT_RECOVERY_INPUT_INVALID")
    issue_date = _validate_issue_date(payload.get("issueDate"))
    operational_truth: dict[str, Any] | None = None

    def finish(decision: dict[str, Any]) -> dict[str, Any]:
        decision_body = {
            key: value for key, value in decision.items() if key != "receiptSha256"
        }
        if operational_truth is not None:
            truth = validate_operational_truth_receipt(operational_truth)
            decision_body.update(
                {
                    "operationalTruthReceiptSha256": operational_truth[
                        "receiptSha256"
                    ],
                    "stopPointKnown": truth["stopPointKnown"],
                    "scheduledAttemptReachedRunner": truth[
                        "scheduledAttemptReachedRunner"
                    ],
                    "artifactDelta": (
                        "partial" if truth["artifactDelta"]["exists"] else "none"
                    ),
                    "recoveryBranch": select_recovery_branch_from_truth(
                        operational_truth
                    ),
                }
            )
            if decision_body["recoveryBranch"] == "ResumeFromStage":
                decision_body["resumeStage"] = str(truth.get("resumeStage") or "")
        observed_decision = seal_audit_decision(decision_body)
        external_payload = {
            key: value
            for key, value in payload.items()
            if key != "_verifiedOperationalTruth"
        }
        enriched = finalize_audit_decision(external_payload, observed_decision)
        return seal_audit_decision(enriched)

    registry = validate_canonical_operational_registry(CANONICAL_CONTROL_ROOT)
    if registry.get("status") != "Green":
        return finish(_incident(
            issue_date=issue_date,
            scheduled_status="unverified",
            recovery_status="unverified",
            reason_code=str(
                registry.get("reason") or "NEWS_GRASP_OPERATIONAL_REGISTRY_INVALID"
            ),
        ))

    human = payload.get("humanImpact")
    if isinstance(human, dict) and any(
        human.get(field) is not True
        for field in ("noFocusTheft", "noUserMonitoring", "noAutoOpen")
    ):
        return finish(_incident(
            issue_date=issue_date,
            scheduled_status="unverified",
            recovery_status="unverified",
            reason_code="HUMAN_IMPACT_CONTRACT_INVALID",
        ))
    try:
        attempt_witness = _inspect_attempt_via_broker(issue_date=issue_date)
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError):
        return finish(_incident(
            issue_date=issue_date,
            scheduled_status="unverified",
            recovery_status="unverified",
            reason_code="SCHEDULED_ATTEMPT_LEDGER_INVALID",
        ))
    operational_truth = _observe_operational_truth(
        issue_date=issue_date,
        attempt_witness=attempt_witness,
    )
    ledger_scheduled_status = str(attempt_witness["scheduledAttemptStatus"])
    recovery_status = str(attempt_witness["recoveryAttemptStatus"])
    classification = "normal" if ledger_scheduled_status == "reserved" else "recoverable"
    if ledger_scheduled_status == "failed":
        from tools.news_grasp_daily_control import classify_observed_failure

        observed_state: dict[str, Any] = {}
        observed_log = ""
        try:
            if CANONICAL_RUNNER_STATE_PATH.is_file():
                observed_state = _load(
                    CANONICAL_RUNNER_STATE_PATH,
                    expected_root=CANONICAL_RUNNER_STATE_PATH.parent,
                )
                if str(observed_state.get("date") or "") not in {"", issue_date}:
                    observed_state = {}
                observed_repo_raw = str(observed_state.get("repo_dir") or "").strip()
                if observed_repo_raw and Path(observed_repo_raw).resolve() != CANONICAL_REPO_ROOT.resolve():
                    observed_state = {}
            log_path = observed_state.get("log_path")
            if log_path:
                observed_log_path = _contained_file(
                    log_path,
                    root=CANONICAL_RUNNER_STATE_PATH.parent,
                    code="RUNNER_LOG_EVIDENCE_INVALID",
                )
                observed_log = observed_log_path.read_text(
                    encoding="utf-8-sig", errors="replace"
                )
        except (OSError, ValueError, json.JSONDecodeError):
            observed_state = {}
            observed_log = ""
        if observed_state:
            classification = classify_observed_failure(
                runner_state=observed_state,
                process_exit_code=int(observed_state.get("exit_code") or 1),
                log_text=observed_log,
            )
            if classification == "external_control_plane_unavailable":
                try:
                    from tools import news_grasp_external_control

                    readiness = news_grasp_external_control.probe_external_readiness()
                except Exception:
                    readiness = {}
                if isinstance(readiness, dict) and readiness.get("status") == "ready":
                    classification = "recoverable"

    if ledger_scheduled_status == "reserved" and classification == "normal":
        try:
            completion = _verify_same_date_completion(
                issue_date=issue_date,
                payload=payload,
                expected_run_intent="ScheduledProduction",
            )
        except (ValueError, OSError, RuntimeError, subprocess.SubprocessError):
            completion = None
        if same_date_completion_green(issue_date, completion):
            return finish(seal_audit_decision({
                "schemaVersion": AUDIT_DECISION_SCHEMA,
                "issueDate": issue_date,
                "classification": "normal",
                "action": "none",
                "terminal": "audit_normal_green",
                "reasonCode": "SAME_DATE_COMPLETION_GREEN",
                "scheduledAttemptStatus": "succeeded",
                "recoveryAttemptStatus": "not_started",
                "publicStatus": "green",
                "operationState": "complete",
                "workPriority": PUBLIC_GREEN_FOLLOWUP_PRIORITY,
                "allowedAfterPublicGreen": PUBLIC_GREEN_ALLOWED_OPERATIONS,
                "attemptLedgerWitnessSha256": attempt_witness["receiptSha256"],
                "completionEvidenceSha256": completion["receiptSha256"],
                "completionEvidence": completion,
            }))
        return finish(_incident(
            issue_date=issue_date,
            scheduled_status="reserved",
            recovery_status=recovery_status,
            reason_code="SAME_DATE_COMPLETION_EVIDENCE_INVALID",
        ))

    if ledger_scheduled_status == "failed":
        try:
            artifact_repo_root = _resolve_artifact_repo_root(payload)
            failure = _validate_scheduled_failure_path(
                {
                    "status": "failed",
                    "failureReceiptPath": payload.get("scheduledFailureReceiptPath"),
                },
                issue_date=issue_date,
                evidence_repo_root=artifact_repo_root,
            )
            failure_sha = str(failure["receiptSha256"])
            if failure_sha != attempt_witness.get("failureReceiptSha256"):
                raise ValueError("SCHEDULED_ATTEMPT_LEDGER_INVALID")
            authority, witness = _validate_recovery_authority_via_broker(
                issue_date=issue_date,
                authority_path_value=payload.get("recoveryAuthorityPath"),
                failure_receipt_sha256=failure_sha,
                evidence_repo_root=artifact_repo_root,
            )
            if authority.get("receiptSha256") != attempt_witness.get(
                "recoveryAuthorityReceiptSha256"
            ) and recovery_status == "started":
                raise ValueError("RECOVERY_ATTEMPT_LINEAGE_INVALID")
            completion = None
            if recovery_status == "started":
                completion = _verify_same_date_completion(
                    issue_date=issue_date,
                    payload=payload,
                    expected_run_intent="ScheduledRecoveryFull",
                )
        except (ValueError, OSError, RuntimeError, subprocess.SubprocessError):
            failure_sha = ""
            authority = {}
            witness = {}
            completion = None
        if same_date_completion_green(issue_date, completion):
            return finish(seal_audit_decision({
                "schemaVersion": AUDIT_DECISION_SCHEMA,
                "issueDate": issue_date,
                "classification": "recoverable",
                "action": "none",
                "terminal": "audit_recovered_green",
                "reasonCode": "RECOVERY_AND_SAME_DATE_COMPLETION_GREEN",
                "scheduledAttemptStatus": "failed",
                "recoveryAttemptStatus": "succeeded",
                "publicStatus": "green",
                "operationState": "complete",
                "workPriority": PUBLIC_GREEN_FOLLOWUP_PRIORITY,
                "allowedAfterPublicGreen": PUBLIC_GREEN_ALLOWED_OPERATIONS,
                "attemptLedgerWitnessSha256": attempt_witness["receiptSha256"],
                "scheduledFailureReceiptSha256": failure_sha,
                "recoveryAuthorityReceiptSha256": authority["receiptSha256"],
                "recoveryAuthorityLedgerWitnessSha256": witness["receiptSha256"],
                "completionEvidenceSha256": completion["receiptSha256"],
                "completionEvidence": completion,
            }))
        if classification == "recoverable" and authority and recovery_status == "not_started":
            return finish(seal_audit_decision({
                "schemaVersion": AUDIT_DECISION_SCHEMA,
                "issueDate": issue_date,
                "classification": "recoverable",
                "action": "scheduled_recovery",
                "terminal": None,
                "reasonCode": "TYPED_RECOVERY_AUTHORITY_READY",
                "scheduledAttemptStatus": "failed",
                "recoveryAttemptStatus": recovery_status,
                "publicStatus": "incomplete",
                "operationState": "recovery_required",
                "workPriority": SAME_DAY_PUBLIC_RECOVERY_PRIORITY,
                "allowedBeforePublicGreen": ALLOWED_BEFORE_PUBLIC_GREEN,
                "forbiddenBeforePublicGreen": FORBIDDEN_BEFORE_PUBLIC_GREEN,
                "attemptLedgerWitnessSha256": attempt_witness["receiptSha256"],
                "recoveryAuthorityReceiptSha256": authority["receiptSha256"],
                "recoveryAuthorityLedgerWitnessSha256": witness["receiptSha256"],
            }))
        if recovery_status == "started":
            return finish(_incident(
                issue_date=issue_date,
                scheduled_status="failed",
                recovery_status="started",
                reason_code="RECOVERY_STARTED_BUT_COMPLETION_INVALID",
            ))
        return finish(_incident(
            issue_date=issue_date,
            scheduled_status="failed",
            recovery_status=recovery_status,
            reason_code="RECOVERY_AUTHORITY_INVALID",
        ))

    return finish(_incident(
        issue_date=issue_date,
        scheduled_status=ledger_scheduled_status,
        recovery_status=recovery_status,
        reason_code="REPAIR_CLASS_INCIDENT_REQUIRED",
    ))


def _is_typed_recovery_manifest_candidate(
    path: Path, issue_date: str, *, artifact_root: Path
) -> bool:
    lexical_root = _lexical_absolute(artifact_root)
    lexical_path = _lexical_absolute(path)
    if not _same_lexical_path(lexical_path.parents[2], lexical_root):
        return False
    cursor = lexical_path
    while True:
        if _is_reparse_or_symlink(cursor):
            return False
        if _same_lexical_path(cursor, lexical_root):
            break
        parent = cursor.parent
        if parent == cursor:
            return False
        cursor = parent
    if not lexical_path.is_file():
        return False
    try:
        if lexical_path.stat().st_size > MAX_JSON_BYTES:
            return False
        value = json.loads(lexical_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(value, dict):
        return False
    return _is_typed_recovery_manifest_value(value, issue_date)


def _has_verified_public_green_proof(
    *, artifact_root: Path, issue_date: str
) -> bool:
    """固定proofの完全な公開bundleだけをpost-Green境界として扱う。"""
    proof_path = (
        artifact_root
        / "build"
        / "recovery"
        / "proofs"
        / f"{issue_date}-public-surface-final.json"
    )
    try:
        value = _load(proof_path, expected_root=artifact_root / "build")
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    publish = value.get("publish_complete_manifest")
    nested_publish = publish.get("publish") if isinstance(publish, dict) else None
    distribution = (
        publish.get("distribution_artifacts") if isinstance(publish, dict) else None
    )
    podcasts = publish.get("podcasts") if isinstance(publish, dict) else None
    notification = publish.get("notification") if isinstance(publish, dict) else None
    return bool(
        value.get("schema_version") == 1
        and value.get("issue_date") == issue_date
        and value.get("overall_status") == "green"
        and value.get("errors") == []
        and isinstance(publish, dict)
        and publish.get("ok") is True
        and publish.get("public_status") == "green"
        and publish.get("scheduled_attempt_status") == "failed_then_recovered"
        and publish.get("recovery_attempt_status") == "succeeded"
        and isinstance(nested_publish, dict)
        and nested_publish.get("ok") is True
        and isinstance(distribution, dict)
        and distribution.get("missing") == []
        and isinstance(notification, dict)
        and notification.get("ok") is True
        and isinstance(podcasts, dict)
        and isinstance(podcasts.get("primary"), dict)
        and podcasts["primary"].get("ok") is True
        and isinstance(podcasts.get("deepdive"), dict)
        and podcasts["deepdive"].get("ok") is True
    )


def _is_typed_recovery_manifest_value(value: object, issue_date: str) -> bool:
    if not isinstance(value, dict):
        return False
    publish_commit = str(value.get("publish_commit") or "")
    publish = value.get("publish")
    distribution = value.get("distribution_artifacts")
    podcasts = value.get("podcasts")
    readiness = value.get("live_runner_readiness")
    next_run = readiness.get("next_run_readiness") if isinstance(readiness, dict) else None
    return bool(
        value.get("schemaVersion") == "NEWS_GRASP_PUBLISH_COMPLETE_V2"
        and value.get("date") == issue_date
        and value.get("ok") is True
        and value.get("public_status") == "green"
        and value.get("scheduled_attempt_status") == "failed_then_recovered"
        and value.get("recovery_attempt_status") == "succeeded"
        and all(
            GIT_SHA_PATTERN.fullmatch(str(value.get(field) or ""))
            for field in ("source_commit", "artifact_commit", "publish_commit")
        )
        and GIT_SHA_PATTERN.fullmatch(publish_commit)
        and isinstance(publish, dict)
        and publish.get("ok") is True
        and publish.get("deploy_head") == publish_commit
        and isinstance(distribution, dict)
        and not distribution.get("missing")
        and isinstance(value.get("notification"), dict)
        and value["notification"].get("ok") is True
        and isinstance(podcasts, dict)
        and all(
            isinstance(podcasts.get(kind), dict)
            and podcasts[kind].get("ok") is True
            for kind in ("primary", "deepdive")
        )
        and isinstance(readiness, dict)
        and readiness.get("ok") is True
        and isinstance(next_run, dict)
        and next_run.get("ok") is True
    )


def _load_recovery_evidence_for_receipt(
    *,
    payload: dict[str, Any],
    decision: dict[str, Any],
    issue_date: str,
    artifact_repo_root: Path,
) -> tuple[Path, dict[str, Any], Path, dict[str, Any], dict[str, Any]]:
    failure_path = _contained_file(
        payload.get("scheduledFailureReceiptPath"),
        root=artifact_repo_root / "build",
        code="SCHEDULED_ATTEMPT_EVIDENCE_INVALID",
    )
    failure = _validate_scheduled_failure_path(
        {"status": "failed", "failureReceiptPath": str(failure_path)},
        issue_date=issue_date,
        evidence_repo_root=artifact_repo_root,
    )
    authority_path = _contained_file(
        payload.get("recoveryAuthorityPath"),
        root=artifact_repo_root / "build",
        code="RECOVERY_AUTHORITY_INVALID",
    )
    authority, authority_witness = _validate_recovery_authority_via_broker(
        issue_date=issue_date,
        authority_path_value=str(authority_path),
        failure_receipt_sha256=str(failure["receiptSha256"]),
        evidence_repo_root=artifact_repo_root,
    )
    if authority.get("receiptSha256") != decision.get("recoveryAuthorityReceiptSha256"):
        raise ValueError("RECOVERY_AUTHORITY_IDENTITY_DRIFT")
    return failure_path, failure, authority_path, authority, authority_witness


def _issue_control_plane_repair_receipt(
    *,
    payload: dict[str, Any],
    decision: dict[str, Any],
    issue_date: str,
    artifact_repo_root: Path,
    production_runtime_root: Path,
    live_bin_root: Path,
    preflight: dict[str, Any],
) -> Path:
    _, _, authority_path, authority, authority_witness = _load_recovery_evidence_for_receipt(
        payload=payload,
        decision=decision,
        issue_date=issue_date,
        artifact_repo_root=artifact_repo_root,
    )
    receipt_path = (
        artifact_repo_root
        / "build"
        / "control-plane"
        / f"{issue_date}-bounded-repair-authority.json"
    )
    if receipt_path.is_file() and not receipt_path.is_symlink():
        try:
            news_grasp_recovery_receipts.validate_control_plane_repair_receipt(
                receipt_path=receipt_path,
                issue_date=issue_date,
                artifact_root=artifact_repo_root,
                ops_root=CANONICAL_REPO_ROOT,
                production_runtime_root=production_runtime_root,
                live_bin_root=live_bin_root,
                current_preflight=preflight,
            )
            return receipt_path
        except (OSError, ValueError):
            pass
    receipt = news_grasp_recovery_receipts.create_control_plane_repair_receipt(
        issue_date=issue_date,
        artifact_root=artifact_repo_root,
        ops_root=CANONICAL_REPO_ROOT,
        production_runtime_root=production_runtime_root,
        live_bin_root=live_bin_root,
        preflight=preflight,
        recovery_authority_path=authority_path,
        recovery_authority=authority,
        authority_ledger_witness=authority_witness,
    )
    news_grasp_recovery_receipts.write_atomic_json(
        receipt_path, receipt, root=artifact_repo_root
    )
    return receipt_path


def _issue_recovery_execution_receipt(
    *,
    payload: dict[str, Any],
    decision: dict[str, Any],
    issue_date: str,
    audit_accepted_at: str,
    artifact_repo_root: Path,
    production_runtime_root: Path,
    live_bin_root: Path,
    runner_path: Path,
    recovery_branch: str = "ScheduledRecoveryFull",
    resume_stage: str | None = None,
    python_executable_path: Path | None = None,
    capability_reservation_path: Path | None = None,
    capability_reservation_receipt_sha256: str | None = None,
    reserved_max_external_model_calls: int = 0,
) -> Path:
    failure_path, failure, authority_path, authority, authority_witness = _load_recovery_evidence_for_receipt(
        payload=payload,
        decision=decision,
        issue_date=issue_date,
        artifact_repo_root=artifact_repo_root,
    )
    receipt_path = (
        artifact_repo_root
        / "build"
        / "recovery-authority"
        / f"{issue_date}-execution-receipt.json"
    )
    if receipt_path.is_file() and not receipt_path.is_symlink():
        try:
            existing = news_grasp_recovery_receipts.validate_recovery_execution_receipt(
                receipt_path=receipt_path,
                issue_date=issue_date,
                artifact_root=artifact_repo_root,
                ops_root=production_runtime_root,
                production_runtime_root=production_runtime_root,
                live_bin_root=live_bin_root,
                runner_state_path=CANONICAL_RUNNER_STATE_PATH,
                runner_script_path=runner_path,
            )
            if (
                existing.get("schemaVersion")
                == news_grasp_recovery_receipts.EXECUTION_SCHEMA
                and existing.get("recoveryBranch") == recovery_branch
                and (existing.get("resumeStage") or None) == (resume_stage or None)
            ):
                return receipt_path
        except (OSError, ValueError):
            pass
    receipt = news_grasp_recovery_receipts.create_recovery_execution_receipt(
        issue_date=issue_date,
        artifact_root=artifact_repo_root,
        ops_root=production_runtime_root,
        production_runtime_root=production_runtime_root,
        live_bin_root=live_bin_root,
        runner_state_path=CANONICAL_RUNNER_STATE_PATH,
        runner_script_path=runner_path,
        recovery_authority_path=authority_path,
        recovery_authority=authority,
        scheduled_failure_receipt_path=failure_path,
        scheduled_failure_receipt=failure,
        authority_ledger_witness=authority_witness,
        audit_accepted_at=audit_accepted_at,
        recovery_branch=recovery_branch,
        resume_stage=resume_stage,
        python_executable_path=(
            python_executable_path
            or (CANONICAL_REPO_ROOT / ".venv" / "Scripts" / "python.exe")
        ),
        capability_reservation_path=(
            capability_reservation_path or authority_path
        ),
        capability_reservation_receipt_sha256=(
            capability_reservation_receipt_sha256
            or str(authority.get("receiptSha256") or "")
        ),
        reserved_max_external_model_calls=reserved_max_external_model_calls,
    )
    news_grasp_recovery_receipts.write_atomic_json(
        receipt_path, receipt, root=artifact_repo_root
    )
    return receipt_path


def _fresh_reverify_publish_manifest(
    *,
    payload: dict[str, Any],
    issue_date: str,
    artifact_repo_root: Path,
    manifest_path: Path,
) -> tuple[dict[str, Any], Path, str]:
    """Public Greenで確定済みのmanifest bytesをcloseout receiptへ束縛する。"""

    state_path = CANONICAL_RUNNER_STATE_PATH
    state = _load(state_path, expected_root=state_path.parent)
    news_grasp_recovery_receipts.validate_producer_lineage(
        producer_state=state,
        issue_date=issue_date,
        artifact_root=artifact_repo_root,
        ops_root=Path(str(state.get("opsRoot") or "")),
    )
    fresh = _load(manifest_path, expected_root=artifact_repo_root / "build")
    if not _is_typed_recovery_manifest_value(fresh, issue_date):
        raise ValueError("POST_PUBLIC_GREEN_MANIFEST_INVALID")
    producer_snapshot = (
        artifact_repo_root
        / "build"
        / "recovery-authority"
        / f"{issue_date}-producer-state.json"
    )
    news_grasp_recovery_receipts.write_atomic_json(
        producer_snapshot, state, root=artifact_repo_root
    )
    producer_state_sha256 = _file_sha256(producer_snapshot)
    return fresh, producer_snapshot, producer_state_sha256


def _fresh_reverify_and_issue_finalization(
    *,
    payload: dict[str, Any],
    decision: dict[str, Any],
    issue_date: str,
    audit_accepted_at: str,
    artifact_repo_root: Path,
    production_runtime_root: Path,
    live_bin_root: Path,
    runner_path: Path,
    manifest_path: Path,
    execution_receipt_path: Path,
) -> Path:
    fresh, producer_snapshot, producer_state_sha256 = _fresh_reverify_publish_manifest(
        payload=payload,
        issue_date=issue_date,
        artifact_repo_root=artifact_repo_root,
        manifest_path=manifest_path,
    )
    failure_path, failure, authority_path, authority, authority_witness = _load_recovery_evidence_for_receipt(
        payload=payload,
        decision=decision,
        issue_date=issue_date,
        artifact_repo_root=artifact_repo_root,
    )
    receipt = news_grasp_recovery_receipts.create_finalization_receipt(
        issue_date=issue_date,
        artifact_root=artifact_repo_root,
        ops_root=production_runtime_root,
        production_runtime_root=production_runtime_root,
        live_bin_root=live_bin_root,
        runner_state_path=CANONICAL_RUNNER_STATE_PATH,
        runner_script_path=runner_path,
        manifest_path=manifest_path,
        manifest=fresh,
        recovery_authority_path=authority_path,
        recovery_authority=authority,
        scheduled_failure_receipt_path=failure_path,
        scheduled_failure_receipt=failure,
        authority_ledger_witness=authority_witness,
        execution_receipt_path=execution_receipt_path,
        execution_receipt=_load(
            execution_receipt_path, expected_root=artifact_repo_root / "build"
        ),
        producer_state_path=producer_snapshot,
        producer_state_sha256=producer_state_sha256,
        audit_accepted_at=audit_accepted_at,
    )
    receipt_path = manifest_path.with_name(f"{issue_date}.finalization-receipt.json")
    news_grasp_recovery_receipts.write_atomic_json(
        receipt_path, receipt, root=artifact_repo_root
    )
    return receipt_path


def _verify_post_public_closeout_surface(
    *, payload: dict[str, Any], issue_date: str, artifact_repo_root: Path
) -> dict[str, Any]:
    """finalizer後にpublic surfaceを同一日付・同一artifactで再検証する。"""

    wait_sec = int(payload.get("verificationWaitSec", 0))
    poll_sec = int(payload.get("verificationPollSec", 10))
    if wait_sec < 0 or wait_sec > 600 or poll_sec < 1 or poll_sec > 60:
        raise ValueError("COMPLETION_VERIFICATION_BUDGET_INVALID")
    return news_grasp_verify_public_surface.verify_public_surface(
        date=issue_date,
        repo_root=artifact_repo_root,
        ops_repo_root=CANONICAL_REPO_ROOT,
        notification_state_path=(
            artifact_repo_root / "build" / "notification" / f"{issue_date}.json"
        ),
        producer_state_path=CANONICAL_RUNNER_STATE_PATH,
        remote="origin",
        branch="main",
        public_base_url="https://hidepon-umg.github.io/News-Grasp/",
        wait_sec=wait_sec,
        poll_sec=poll_sec,
    )


def _run_post_public_automation_completion_guard(
    *,
    issue_date: str,
    artifact_repo_root: Path,
    execution_receipt_path: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    """installed automation guardをreceipt由来identityで一回だけ実行する。"""

    execution = news_grasp_recovery_closeout._execution_body(  # noqa: SLF001
        execution_receipt_path
    )
    source_guard = _contained_file(
        artifact_repo_root
        / "automation"
        / "news-grasp-6-40"
        / "completion_guard.py",
        root=artifact_repo_root,
        code="AUTOMATION_COMPLETION_GUARD_INVALID",
    )
    installed_guard = CANONICAL_AUTOMATION_COMPLETION_GUARD_PATH.resolve(
        strict=True
    )
    if (
        installed_guard.is_symlink()
        or not installed_guard.is_file()
        or _file_sha256(installed_guard) != _file_sha256(source_guard)
    ):
        raise ValueError("AUTOMATION_COMPLETION_GUARD_DRIFT")
    python_executable = news_grasp_recovery_closeout._resolved_field(  # noqa: SLF001
        execution, "pythonExecutablePath"
    )
    ops_root = news_grasp_recovery_closeout._resolved_field(  # noqa: SLF001
        execution, "opsRoot"
    )
    runner_state = Path(str(execution.get("runnerStatePath") or "")).resolve(
        strict=True
    )
    news_grasp_recovery_closeout.record_closeout_operation(
        artifact_root=artifact_repo_root,
        issue_date=issue_date,
        operation="completion_guard",
    )
    return_code, stdout = _run_bounded(
        [
            str(python_executable),
            "-I",
            "-B",
            str(installed_guard),
            "--issue-date",
            issue_date,
            "--manifest",
            str(manifest_path),
            "--runner-state",
            str(runner_state),
            "--ops-root",
            str(ops_root),
        ],
        cwd=artifact_repo_root,
        timeout=120,
    )
    try:
        result = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("AUTOMATION_COMPLETION_GUARD_INVALID") from error
    if (
        return_code != 0
        or not isinstance(result, dict)
        or result.get("schemaVersion") != "NEWS_GRASP_640_COMPLETION_GUARD_V1"
        or result.get("ok") is not True
        or result.get("issueDate") != issue_date
        or result.get("scheduled_attempt_status") != "failed_then_recovered"
        or result.get("recovery_attempt_status") != "succeeded"
        or result.get("public_status") != "green"
        or result.get("runner_status") != "publish_complete"
        or result.get("run_intent") != "ScheduledRecoveryFull"
    ):
        raise ValueError("AUTOMATION_COMPLETION_GUARD_RED")
    return result


def _completion_evidence_from_closeout(
    *,
    issue_date: str,
    artifact_repo_root: Path,
    execution_receipt_path: Path,
    closeout_surface: dict[str, Any],
    completion_guard: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    """allowed verifierとapplied runner stateからsame-date evidenceを封印する。"""

    state = _load(
        CANONICAL_RUNNER_STATE_PATH,
        expected_root=CANONICAL_RUNNER_STATE_PATH.parent,
    )
    execution = news_grasp_recovery_closeout._execution_body(  # noqa: SLF001
        execution_receipt_path
    )
    if (
        state.get("status") != "publish_complete"
        or state.get("date") != issue_date
        or state.get("run_intent") != "ScheduledRecoveryFull"
        or state.get("scheduled_attempt_status") != "failed_then_recovered"
        or state.get("recovery_attempt_status") != "succeeded"
        or completion_guard.get("ok") is not True
        or completion_guard.get("issueDate") != issue_date
        or completion_guard.get("runner_status") != "publish_complete"
        or closeout_surface.get("public_status") != "green"
        or closeout_surface.get("scheduled_attempt_status")
        != "failed_then_recovered"
        or closeout_surface.get("recovery_attempt_status") != "succeeded"
        or Path(str(state.get("artifactRoot") or "")).resolve(strict=True)
        != artifact_repo_root
        or Path(str(state.get("opsRoot") or "")).resolve(strict=True)
        != Path(str(execution.get("opsRoot") or "")).resolve(strict=True)
    ):
        raise ValueError("FINALIZATION_STATE_BINDING_INVALID")
    lineage = _completion_lineage(
        issue_date=issue_date,
        run_intent="ScheduledRecoveryFull",
        run_id=state.get("run_id"),
        artifact_root=artifact_repo_root,
        ops_root=execution["opsRoot"],
    )
    for field in (
        "dailyRootId",
        "rootOperationId",
        "producerOperationId",
        "producerRunIntent",
        "lineageReceiptSha256",
    ):
        if state.get(field) != lineage[field]:
            raise ValueError("FINALIZATION_STATE_BINDING_INVALID")
    evidence_seed = {
        "completionGuard": completion_guard,
        "publicSurface": closeout_surface,
        "runnerStateSha256": _file_sha256(CANONICAL_RUNNER_STATE_PATH),
        "executionReceiptSha256": execution["receiptSha256"],
    }
    typed_hashes = _typed_completion_hashes(
        issue_date=issue_date,
        payload={**payload, "expectedRunIntent": "ScheduledRecoveryFull"},
        evidence=evidence_seed,
    )
    completion = _sealed(
        {
            "schemaVersion": "SAME_DATE_COMPLETION_EVIDENCE_V1",
            "issuer": VERIFIED_COMPLETION_ISSUER,
            "issueDate": issue_date,
            "publishStatusIssueDate": issue_date,
            "runIntent": "ScheduledRecoveryFull",
            "runId": state.get("run_id"),
            **lineage,
            "checks": {field: True for field in COMPLETION_FIELDS},
            "evidenceSha256": {
                field: hashlib.sha256(
                    _canonical({"field": field, **evidence_seed})
                ).hexdigest()
                for field in COMPLETION_FIELDS
            },
            "verificationStatus": "verified_green",
            "publicCompletionStatus": "green",
            "nextRunReadinessStatus": "green",
            "phase": "post_public_closeout",
            "reasonCode": "VERIFIED_GREEN",
            "failedGateIds": [],
            "completionAuthorityId": str(
                state.get("completionAuthorityId") or ""
            ),
            **typed_hashes,
        }
    )
    if not same_date_completion_green(issue_date, completion):
        raise ValueError("SAME_DATE_COMPLETION_EVIDENCE_INVALID")
    return completion


def _execute_existing_public_green_closeout(
    *,
    payload: dict[str, Any],
    decision: dict[str, Any],
    issue_date: str,
    audit_accepted_at: str,
    artifact_repo_root: Path,
    production_runtime_root: Path,
    live_bin_root: Path,
    runner_path: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    """Public Green後を既存receipt由来の有限closeoutだけで閉じる。"""

    execution_receipt = (
        artifact_repo_root
        / "build"
        / "recovery-authority"
        / f"{issue_date}-execution-receipt.json"
    )
    finalization_receipt = manifest_path.with_name(
        f"{issue_date}.finalization-receipt.json"
    )
    public_tree_before: str | None = None
    try:
        public_tree_before = news_grasp_recovery_closeout._tree_sha256(  # noqa: SLF001
            artifact_repo_root / "docs"
        )
        if not finalization_receipt.is_file() or finalization_receipt.is_symlink():
            raise news_grasp_recovery_closeout.PostPublicCloseoutError(
                f"{POST_PUBLIC_CLOSEOUT_BLOCKER}:finalization_receipt_missing"
            )

        reseal_attempted = False
        try:
            command_receipt = news_grasp_recovery_closeout.build_exact_finalizer_command(
                execution_receipt_path=execution_receipt,
                finalization_receipt_path=finalization_receipt,
                publish_manifest_path=manifest_path,
            )
        except news_grasp_recovery_closeout.PostPublicCloseoutError:
            # 既知driftだけをtransactional helperへ一括委譲する。未知drift、
            # applied ledger、authority driftはhelper内でfail-closedになる。
            reseal_attempted = True
            news_grasp_recovery_closeout.reseal_known_receipt_drift(
                execution_receipt_path=execution_receipt,
                finalization_receipt_path=finalization_receipt,
            )
            command_receipt = news_grasp_recovery_closeout.build_exact_finalizer_command(
                execution_receipt_path=execution_receipt,
                finalization_receipt_path=finalization_receipt,
                publish_manifest_path=manifest_path,
            )

        news_grasp_recovery_closeout.record_closeout_operation(
            artifact_root=artifact_repo_root,
            issue_date=issue_date,
            operation="finalizer_exact_args_replay",
        )
        return_code, _ = _run_bounded(
            list(command_receipt["argv"]), cwd=artifact_repo_root, timeout=5400
        )
        if return_code != 0:
            raise RuntimeError(f"FINALIZER_EXIT_{return_code}")

        completion_guard = _run_post_public_automation_completion_guard(
            issue_date=issue_date,
            artifact_repo_root=artifact_repo_root,
            execution_receipt_path=execution_receipt,
            manifest_path=manifest_path,
        )
        closeout_surface = _verify_post_public_closeout_surface(
            payload=payload,
            issue_date=issue_date,
            artifact_repo_root=artifact_repo_root,
        )
        if (
            closeout_surface.get("ok") is not True
            or closeout_surface.get("overall_status") != "green"
        ):
            raise RuntimeError("VERIFY_PUBLIC_SURFACE_RED")
        public_tree_after = news_grasp_recovery_closeout._tree_sha256(  # noqa: SLF001
            artifact_repo_root / "docs"
        )
        if public_tree_after != public_tree_before:
            raise RuntimeError("PUBLIC_ARTIFACT_MUTATED_DURING_CLOSEOUT")

        completion = _completion_evidence_from_closeout(
            issue_date=issue_date,
            artifact_repo_root=artifact_repo_root,
            execution_receipt_path=execution_receipt,
            closeout_surface=closeout_surface,
            completion_guard=completion_guard,
            payload=payload,
        )
        news_grasp_recovery_closeout.record_closeout_operation(
            artifact_root=artifact_repo_root,
            issue_date=issue_date,
            operation="final_report",
        )
        final_decision = seal_audit_decision(
            {
                "schemaVersion": AUDIT_DECISION_SCHEMA,
                "issueDate": issue_date,
                "classification": "recoverable",
                "action": "none",
                "terminal": "audit_recovered_green",
                "reasonCode": "POST_PUBLIC_GREEN_CLOSEOUT_COMPLETE",
                "scheduledAttemptStatus": "failed",
                "recoveryAttemptStatus": "succeeded",
                "publicStatus": "green",
                "operationState": "complete",
                "workPriority": PUBLIC_GREEN_FOLLOWUP_PRIORITY,
                "allowedAfterPublicGreen": PUBLIC_GREEN_ALLOWED_OPERATIONS,
                "completionEvidenceSha256": completion["receiptSha256"],
                "completionEvidence": completion,
                "postPublicCloseout": {
                    "status": "Green",
                    "exactArgsSha256": command_receipt.get("argvSha256"),
                    "receiptResealAttempts": 1 if reseal_attempted else 0,
                    "publicArtifactTreeSha256Before": public_tree_before,
                    "publicArtifactTreeSha256After": public_tree_after,
                    "publicArtifactUnchanged": True,
                },
            }
        )
        write_audit_terminal(final_decision)
        return final_decision
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        incident = _incident(
            issue_date=issue_date,
            scheduled_status="failed",
            recovery_status=str(decision.get("recoveryAttemptStatus") or "succeeded"),
            reason_code=POST_PUBLIC_CLOSEOUT_BLOCKER,
        )
        incident["closeoutError"] = str(error) or type(error).__name__
        incident["receiptResealAttempts"] = int(
            "reseal_attempted" in locals() and reseal_attempted
        )
        if public_tree_before is not None:
            incident["publicArtifactTreeSha256Before"] = public_tree_before
        write_audit_terminal(incident)
        return incident


def _post_public_closeout_blocker(
    *, issue_date: str, decision: Mapping[str, Any] | None, error: BaseException
) -> dict[str, Any]:
    """Public Green後の失敗を、復旧再生成へ戻さずtyped terminalへ固定する。"""

    incident = _incident(
        issue_date=issue_date,
        scheduled_status="failed",
        recovery_status=str(
            (decision or {}).get("recoveryAttemptStatus") or "succeeded"
        ),
        reason_code=POST_PUBLIC_CLOSEOUT_BLOCKER,
    )
    body = {
        key: value for key, value in incident.items() if key != "receiptSha256"
    }
    body.update(
        {
            "closeoutError": str(error) or type(error).__name__,
            "closeoutOperation": "execute_public_green_closeout_owned",
            "sourceDecision": dict(decision or {}),
        }
    )
    blocker = _sealed(body)
    try:
        write_audit_terminal(blocker)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        # terminal writer側の既知replay/busyは呼出側で同じtyped blockerを扱う。
        # ここで生成や別経路へフォールバックしない。
        pass
    return blocker


def execute_public_green_closeout_owned(
    issue_date: str, decision: Mapping[str, Any]
) -> dict[str, Any]:
    """Canonical ensure-0640からpost-public-Green finalizerへ一回だけ接続する。

    この入口は公開済みartifactを再生成しない。決定に束縛されたfailure/authority
    とHumanImpactだけを受け取り、実行対象のroot、runner、HEAD、SHAはcanonical
    sourceから再解決して既存 ``execute_audit_recovery`` へ渡す。
    """

    try:
        normalized_date = _validate_issue_date(issue_date)
    except (TypeError, ValueError) as error:
        # 不正日付はaudit terminalへ安全に封印できないため、入力境界で拒否する。
        raise ValueError("AUDIT_RECOVERY_DATE_INVALID") from error

    if not isinstance(decision, Mapping):
        return _post_public_closeout_blocker(
            issue_date=normalized_date,
            decision=None,
            error=ValueError("POST_PUBLIC_CLOSEOUT_DECISION_INVALID"),
        )

    decision_value = dict(decision)
    action = str(decision_value.get("action") or "").strip()
    if action not in {"launch_recovery", "scheduled_recovery"}:
        return _post_public_closeout_blocker(
            issue_date=normalized_date,
            decision=decision_value,
            error=ValueError("POST_PUBLIC_CLOSEOUT_ACTION_INVALID"),
        )

    try:
        artifact_root = _resolve_artifact_repo_root(
            {
                "artifactRepoRoot": decision_value.get("artifactRepoRoot"),
                "opsRepoRoot": str(CANONICAL_REPO_ROOT),
            }
        )
        artifact_head = _validate_artifact_executable_tree(artifact_root)
        production_runtime_root = (
            Path.home() / ".news-grasp-runtime" / "production-runtime"
        )
        recovery_freshness = (
            news_grasp_recovery_freshness.verify_recovery_freshness(
                worktree_root=artifact_root,
                runtime_root=production_runtime_root.parent,
            )
        )
        if recovery_freshness.get("ok") is not True:
            raise ValueError(
                "RECOVERY_DEEPDIVE_RUNTIME_FRESHNESS_MISMATCH:"
                f"{recovery_freshness.get('detailCode') or 'unverified'}"
            )

        live_bin_root = CANONICAL_LIVE_BIN_ROOT.resolve(strict=True)
        if live_bin_root.is_symlink() or not live_bin_root.is_dir():
            raise ValueError("RECOVERY_RUNNER_INVALID")
        runner_path = live_bin_root / "news-grasp-runner.ps1"
        if runner_path.is_symlink() or not runner_path.is_file():
            raise ValueError("RECOVERY_RUNNER_INVALID")
        runner_sha256 = _file_sha256(runner_path)
        if not _valid_sha256(runner_sha256):
            raise ValueError("RECOVERY_RUNNER_INVALID")

        execution_path = (
            artifact_root
            / "build"
            / "recovery-authority"
            / f"{normalized_date}-execution-receipt.json"
        )
        execution = news_grasp_recovery_closeout._execution_body(  # noqa: SLF001
            execution_path
        )
        if (
            execution.get("issueDate") != normalized_date
            or Path(str(execution.get("artifactRoot") or "")).resolve(strict=True)
            != artifact_root
        ):
            raise ValueError("RECOVERY_EXECUTION_RECEIPT_INVALID")
        failure_path = _contained_file(
            execution.get("scheduledFailureReceiptPath"),
            root=artifact_root / "build",
            code="SCHEDULED_ATTEMPT_EVIDENCE_INVALID",
        )
        authority_path = _contained_file(
            execution.get("recoveryAuthorityPath"),
            root=artifact_root / "build",
            code="RECOVERY_AUTHORITY_INVALID",
        )
        authority_sha256 = str(execution.get("recoveryAuthorityReceiptSha256") or "")
        if not _valid_sha256(authority_sha256):
            raise ValueError("RECOVERY_AUTHORITY_INVALID")

        human_source = decision_value.get("humanImpact")
        if not isinstance(human_source, Mapping):
            human_source = decision_value
        human = {
            field: human_source.get(field) is True
            for field in ("noFocusTheft", "noUserMonitoring", "noAutoOpen")
        }
        if not all(human.values()):
            raise ValueError("HUMAN_IMPACT_CONTRACT_INVALID")

        payload: dict[str, Any] = {
            "issueDate": normalized_date,
            "artifactRepoRoot": str(artifact_root),
            "opsRepoRoot": str(CANONICAL_REPO_ROOT.resolve(strict=True)),
            "scheduledFailureReceiptPath": str(failure_path),
            "recoveryAuthorityPath": str(authority_path),
            "humanImpact": human,
            "verificationWaitSec": 0,
            "verificationPollSec": 10,
            "recoveryExecution": {
                "issueDate": normalized_date,
                "runIntent": "ScheduledRecoveryFull",
                "maxExternalModelCalls": int(
                    execution.get("reservedMaxExternalModelCalls") or 0
                ),
                "maxFullE2EAttempts": 0,
                **human,
                "recoveryAuthorityReceiptSha256": authority_sha256,
                "artifactRepoHead": artifact_head,
                "runnerSha256": runner_sha256,
            },
        }
        return _execute_existing_public_green_closeout(
            payload=payload,
            decision=decision_value,
            issue_date=normalized_date,
            audit_accepted_at=datetime.now().astimezone().isoformat(),
            artifact_repo_root=artifact_root,
            production_runtime_root=production_runtime_root,
            live_bin_root=live_bin_root,
            runner_path=runner_path,
            manifest_path=(
                artifact_root
                / "build"
                / "publish-complete"
                / f"{normalized_date}.json"
            ),
        )
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        return _post_public_closeout_blocker(
            issue_date=normalized_date,
            decision=decision_value,
            error=error,
        )


def execute_audit_recovery(payload: object) -> dict[str, Any]:
    """監査判断、production recovery 1回、same-gate再検証、typed terminalを一続きで閉じる。"""
    if not isinstance(payload, dict):
        raise ValueError("AUDIT_RECOVERY_INPUT_INVALID")
    if any(
        field in payload
        for field in (
            "auditAcceptedAt",
            "productionRuntimeRoot",
            "liveBinRoot",
            "recoveryPythonExe",
        )
    ):
        raise ValueError("AUDIT_PRODUCTION_IDENTITY_OVERRIDE_FORBIDDEN")
    issue_date = _validate_issue_date(payload.get("issueDate"))
    audit_accepted_at = datetime.now().astimezone().isoformat()
    decision = decide_audit_recovery(payload)
    if decision.get("terminal"):
        write_audit_terminal(decision)
        return decision
    if decision.get("action") != "scheduled_recovery":
        incident = _incident(
            issue_date=issue_date,
            scheduled_status=str(decision.get("scheduledAttemptStatus") or "unverified"),
            recovery_status=str(decision.get("recoveryAttemptStatus") or "unverified"),
            reason_code="AUDIT_RECOVERY_ACTION_INVALID",
        )
        write_audit_terminal(incident)
        return incident

    artifact_repo_root = _resolve_artifact_repo_root(payload)
    artifact_repo_head = _validate_artifact_executable_tree(artifact_repo_root)
    live_bin_root = CANONICAL_LIVE_BIN_ROOT
    runner_path = live_bin_root / "news-grasp-runner.ps1"
    if not runner_path.is_file() or runner_path.is_symlink():
        raise ValueError("RECOVERY_RUNNER_INVALID")
    runner_sha256 = _file_sha256(runner_path)
    production_runtime_root = Path.home() / ".news-grasp-runtime" / "production-runtime"
    publish_complete_manifest = (
        artifact_repo_root / "build" / "publish-complete" / f"{issue_date}.json"
    )
    typed_manifest_candidate = _is_typed_recovery_manifest_candidate(
        publish_complete_manifest,
        issue_date,
        artifact_root=artifact_repo_root,
    )
    public_green_reached = typed_manifest_candidate or _has_verified_public_green_proof(
        artifact_root=artifact_repo_root,
        issue_date=issue_date,
    )
    recovery_freshness = news_grasp_recovery_freshness.verify_recovery_freshness(
        worktree_root=artifact_repo_root,
        runtime_root=production_runtime_root.parent,
    )
    if recovery_freshness.get("ok") is not True:
        incident = _incident(
            issue_date=issue_date,
            scheduled_status="failed",
            recovery_status="not_started",
            reason_code="RECOVERY_DEEPDIVE_RUNTIME_FRESHNESS_MISMATCH",
        )
        incident["recoveryFreshness"] = recovery_freshness
        write_audit_terminal(incident)
        return incident
    if public_green_reached:
        return _execute_existing_public_green_closeout(
            payload=payload,
            decision=decision,
            issue_date=issue_date,
            audit_accepted_at=audit_accepted_at,
            artifact_repo_root=artifact_repo_root,
            production_runtime_root=production_runtime_root,
            live_bin_root=live_bin_root,
            runner_path=runner_path,
            manifest_path=publish_complete_manifest,
        )

    authority_path = _contained_file(
        payload.get("recoveryAuthorityPath"),
        root=artifact_repo_root / "build",
        code="RECOVERY_AUTHORITY_INVALID",
    )

    canonical_python = (CANONICAL_REPO_ROOT / ".venv" / "Scripts" / "python.exe").resolve()
    if not canonical_python.is_file() or canonical_python.is_symlink():
        raise ValueError("RECOVERY_RUNTIME_INTERPRETER_INVALID")
    try:
        high_cost_binding = resolve_live_high_cost_binding(live_bin_root)
    except (OSError, ValueError) as error:
        incident = _incident(
            issue_date=issue_date,
            scheduled_status="failed",
            recovery_status="not_started",
            reason_code=str(error),
        )
        write_audit_terminal(incident)
        return incident
    validate_recovery_execution_manifest(
        payload.get("recoveryExecution"),
        issue_date=issue_date,
        authority_receipt_sha256=str(
            decision.get("recoveryAuthorityReceiptSha256") or ""
        ),
        artifact_repo_head=artifact_repo_head,
        runner_sha256=runner_sha256,
    )
    control_plane = news_grasp_control_plane.verify_control_plane(
        artifact_root=artifact_repo_root,
        ops_root=CANONICAL_REPO_ROOT,
        production_runtime_root=production_runtime_root,
        live_bin_root=live_bin_root,
        issue_date=issue_date,
        run_intent="ScheduledRecoveryFull",
        high_cost_binding_path=Path(str(high_cost_binding["bindingPath"])),
        high_cost_binding_receipt_sha256=str(
            high_cost_binding["bindingReceiptSha256"]
        ),
    )
    initial_control_plane = control_plane
    if (
        not control_plane.get("ok")
        and not public_green_reached
        and control_plane.get("reasonCode")
        in {"PRODUCTION_RUNTIME_DRIFT", "LIVE_BIN_DRIFT"}
    ):
        bootstrap_path = live_bin_root / "news-grasp-bootstrap.ps1"
        if not bootstrap_path.is_file() or bootstrap_path.is_symlink():
            raise ValueError("CONTROL_PLANE_REPAIR_ENTRYPOINT_INVALID")
        repair_authority_path = _issue_control_plane_repair_receipt(
            payload=payload,
            decision=decision,
            issue_date=issue_date,
            artifact_repo_root=artifact_repo_root,
            production_runtime_root=production_runtime_root,
            live_bin_root=live_bin_root,
            preflight=control_plane,
        )
        repair_command = [
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
            str(artifact_repo_root),
            "-RepoDir",
            str(CANONICAL_REPO_ROOT),
            "-EvidenceRepoDir",
            str(CANONICAL_REPO_ROOT),
            "-PythonExe",
            str(canonical_python),
            "-BinDir",
            str(live_bin_root),
            "-DateStamp",
            issue_date,
            "-HighCostBindingPath",
            str(high_cost_binding["bindingPath"]),
            "-HighCostBindingReceiptSha256",
            str(high_cost_binding["bindingReceiptSha256"]),
        ]
        repair_return_code, _ = _run_bounded(
            repair_command,
            cwd=CANONICAL_REPO_ROOT,
            timeout=900,
        )
        if repair_return_code == 0:
            control_plane = news_grasp_control_plane.verify_control_plane(
                artifact_root=artifact_repo_root,
                ops_root=CANONICAL_REPO_ROOT,
                production_runtime_root=production_runtime_root,
                live_bin_root=live_bin_root,
                issue_date=issue_date,
                run_intent="ScheduledRecoveryFull",
                high_cost_binding_path=Path(str(high_cost_binding["bindingPath"])),
                high_cost_binding_receipt_sha256=str(
                    high_cost_binding["bindingReceiptSha256"]
                ),
            )
        else:
            control_plane = {
                **control_plane,
                "reasonCode": f"CONTROL_PLANE_BOUNDED_REPAIR_FAILED_{repair_return_code}",
                "repairReturnCode": repair_return_code,
            }
    if not control_plane.get("ok") and not public_green_reached:
        incident = _incident(
            issue_date=issue_date,
            scheduled_status="failed",
            recovery_status=str(
                decision.get("recoveryAttemptStatus") or "not_started"
            ),
            reason_code=str(
                control_plane.get("reasonCode")
                or "NEWS_GRASP_CONTROL_PLANE_PREFLIGHT_FAILED"
            ),
        )
        incident["initialControlPlanePreflight"] = initial_control_plane
        incident["controlPlanePreflight"] = control_plane
        write_audit_terminal(incident)
        return incident
    decision_recovery_branch = str(
        decision.get("recoveryBranch") or "ScheduledRecoveryFull"
    )
    decision_resume_stage = str(decision.get("resumeStage") or "").strip() or None
    if decision_recovery_branch != "ResumeFromStage":
        decision_recovery_branch = "ScheduledRecoveryFull"
        decision_resume_stage = None
    try:
        execution_receipt = _issue_recovery_execution_receipt(
            payload=payload,
            decision=decision,
            issue_date=issue_date,
            audit_accepted_at=audit_accepted_at,
            artifact_repo_root=artifact_repo_root,
            production_runtime_root=production_runtime_root,
            live_bin_root=live_bin_root,
            runner_path=runner_path,
            recovery_branch=decision_recovery_branch,
            resume_stage=decision_resume_stage,
            python_executable_path=canonical_python,
            capability_reservation_path=Path(str(high_cost_binding["bindingPath"])),
            capability_reservation_receipt_sha256=str(
                high_cost_binding["bindingReceiptSha256"]
            ),
            reserved_max_external_model_calls=int(
                (payload.get("recoveryExecution") or {}).get(
                    "maxExternalModelCalls", 0
                )
            ),
        )
    except (OSError, ValueError, RuntimeError) as error:
        incident = _incident(
            issue_date=issue_date,
            scheduled_status="failed",
            recovery_status=str(decision.get("recoveryAttemptStatus") or "not_started"),
            reason_code=f"RECOVERY_EXECUTION_RECEIPT_FAILED_{type(error).__name__}",
        )
        write_audit_terminal(incident)
        return incident
    # child processを作る前に同じreceiptを再検証し、canonical ledgerへ予約する。
    # invalid/mismatched receiptはauthorityを合成せず、process start=0で終了する。
    try:
        execution_value = news_grasp_recovery_receipts.validate_recovery_execution_receipt(
            receipt_path=execution_receipt,
            issue_date=issue_date,
            artifact_root=artifact_repo_root,
            ops_root=production_runtime_root,
            production_runtime_root=production_runtime_root,
            live_bin_root=live_bin_root,
            runner_state_path=CANONICAL_RUNNER_STATE_PATH,
            runner_script_path=runner_path,
        )
        status = news_grasp_recovery_receipts.consumption_status(
            receipt=execution_value,
            live_bin_root=live_bin_root,
            kind="execution",
        )
        if status is None:
            news_grasp_recovery_receipts.consume_or_resume(
                receipt=execution_value,
                live_bin_root=live_bin_root,
                kind="execution",
            )
        elif status != "consumed_pending_operation":
            raise ValueError("RECOVERY_EXECUTION_RECEIPT_ALREADY_APPLIED")
    except (OSError, ValueError, RuntimeError) as error:
        incident = _incident(
            issue_date=issue_date,
            scheduled_status="failed",
            recovery_status="not_started",
            reason_code=f"RECOVERY_EXECUTION_RECEIPT_INVALID_{type(error).__name__}",
        )
        write_audit_terminal(incident)
        return incident
    state_path = Path.home() / "bin" / "news-grasp-runner-state.json"
    log_dir = Path.home() / "bin" / "news-grasp-logs"
    command = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(runner_path),
        "-RunIntent",
        "ScheduledRecoveryFull",
        "-DateStampOverride",
        issue_date,
        "-RepoDirOverride",
        str(artifact_repo_root),
        "-OpsRepoRootOverride",
        str(CANONICAL_REPO_ROOT),
        "-PyExeOverride",
        str(canonical_python),
        "-StateFileOverride",
        str(state_path),
        "-LogDirOverride",
        str(log_dir),
        "-HighCostBindingPath",
        str(high_cost_binding["bindingPath"]),
        "-HighCostBindingReceiptSha256",
        str(high_cost_binding["bindingReceiptSha256"]),
        "-ScheduledAuthorityEvidencePath",
        str(authority_path),
        "-RecoveryExecutionReceiptPath",
        str(execution_receipt),
    ]
    if decision_recovery_branch == "ResumeFromStage":
        command.extend(["-ResumeFromStage", str(decision_resume_stage)])
    return_code, _ = _run_bounded(command, cwd=artifact_repo_root, timeout=5400)
    if return_code != 0:
        incident = _incident(
            issue_date=issue_date,
            scheduled_status="failed",
            recovery_status="started",
            reason_code=f"RECOVERY_EXECUTION_FAILED_{return_code}",
        )
        write_audit_terminal(incident)
        return incident

    final_decision = decide_audit_recovery(payload)
    if final_decision.get("terminal") != "audit_recovered_green":
        final_decision = _incident(
            issue_date=issue_date,
            scheduled_status="failed",
            recovery_status="started",
            reason_code="RECOVERY_COMPLETION_INVALID",
        )
    else:
        try:
            news_grasp_recovery_closeout.record_closeout_operation(
                artifact_root=artifact_repo_root,
                issue_date=issue_date,
                operation="final_report",
            )
        except (OSError, ValueError, RuntimeError) as error:
            final_decision = _incident(
                issue_date=issue_date,
                scheduled_status="failed",
                recovery_status="succeeded",
                reason_code=POST_PUBLIC_CLOSEOUT_BLOCKER,
            )
            final_decision["closeoutError"] = str(error)
    write_audit_terminal(final_decision)
    return final_decision


def _audit_event_history_path(root: Path, issue_date: str) -> Path:
    return root / f"{issue_date}-audit-events.jsonl"


def _completion_authority_path(root: Path, issue_date: str) -> Path:
    return root / f"{issue_date}-completion-authority.json"


def _audit_transaction_path(root: Path, issue_date: str) -> Path:
    return root / f".{issue_date}-audit-terminal-transaction.json"


def _read_bounded_bytes(
    path: Path, *, maximum: int, code: str, limit_code: str | None = None
) -> bytes:
    if path.is_symlink() or _is_reparse_or_symlink(path) or not path.is_file():
        raise ValueError(code)
    try:
        with path.open("rb") as stream:
            payload = stream.read(maximum + 1)
    except OSError as error:
        raise ValueError(code) from error
    if len(payload) > maximum:
        raise ValueError(limit_code or code)
    return payload


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    if path.exists() and _is_reparse_or_symlink(path):
        raise ValueError("AUDIT_TERMINAL_OUTPUT_INVALID")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _load_terminal_projection(root: Path, issue_date: str) -> dict[str, Any]:
    path = root / f"{issue_date}-audit-terminal.json"
    try:
        value = json.loads(
            _read_bounded_bytes(
                path,
                maximum=MAX_JSON_BYTES,
                code="AUDIT_TERMINAL_PROJECTION_INVALID",
            ).decode("utf-8")
        )
        terminal = _validate_sealed(
            value,
            schema_version="AUDIT_TERMINAL_V1",
            code="AUDIT_TERMINAL_PROJECTION_INVALID",
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("AUDIT_TERMINAL_PROJECTION_INVALID") from error
    if terminal.get("issuer") != DECISION_ISSUER or terminal.get(
        "issueDate"
    ) != issue_date:
        raise ValueError("AUDIT_TERMINAL_PROJECTION_INVALID")
    return terminal


def _validate_completion_authority_anchor(
    *, root: Path, issue_date: str, authority: dict[str, Any]
) -> None:
    """managed-rootを信頼境界とし、caller由来receipt単体のGreen化を拒否する。"""
    events = validate_audit_observation_history(
        _audit_event_history_path(root, issue_date)
    )
    if not events:
        raise ValueError("AUDIT_COMPLETION_AUTHORITY_INVALID")
    terminal = _load_terminal_projection(root, issue_date)
    if (
        terminal.get("eventSequence") != events[-1].get("sequence")
        or terminal.get("eventHash") != events[-1].get("eventHash")
    ):
        raise ValueError("AUDIT_COMPLETION_AUTHORITY_INVALID")
    authority_id = authority.get("completionAuthorityId")
    decision_sha = authority.get("decisionReceiptSha256")
    first_terminal = authority.get("firstVerifiedTerminal")
    if not any(
        event.get("completionAuthorityId") == authority_id
        and event.get("decisionReceiptSha256") == decision_sha
        and event.get("result") == first_terminal
        for event in events
    ):
        raise ValueError("AUDIT_COMPLETION_AUTHORITY_INVALID")


def load_completion_authority_receipt(issue_date: str) -> dict[str, Any] | None:
    """正規managed-rootのevent chainへanchorされたauthorityだけを返す。"""
    issue_date = _validate_issue_date(issue_date)
    root = _validated_terminal_root(create=False)
    path = _completion_authority_path(root, issue_date)
    if not path.exists():
        return None
    with _pinned_directory(
        root,
        anchor=CANONICAL_REPO_ROOT,
        invalid_code="AUDIT_COMPLETION_AUTHORITY_INVALID",
    ):
        _validated_terminal_root(create=False)
        try:
            payload = _read_bounded_bytes(
                path,
                maximum=MAX_JSON_BYTES,
                code="AUDIT_COMPLETION_AUTHORITY_INVALID",
            )
            value = json.loads(payload.decode("utf-8"))
            authority = validate_completion_authority_receipt(
                value, issue_date=issue_date
            )
            _validate_completion_authority_anchor(
                root=root, issue_date=issue_date, authority=authority
            )
            return authority
        except (UnicodeError, json.JSONDecodeError, ValueError) as error:
            raise ValueError("AUDIT_COMPLETION_AUTHORITY_INVALID") from error


def prune_audit_artifacts(
    root: Path, *, issue_date: str, owner: str = AUDIT_LIFECYCLE_OWNER
) -> list[str]:
    """正規ownerだけがissue date基準の31日超artifactを整理する。"""
    if owner != AUDIT_LIFECYCLE_OWNER:
        raise ValueError("AUDIT_RETENTION_OWNER_INVALID")
    current = _validate_issue_date(issue_date)
    cutoff = date.fromisoformat(current) - timedelta(days=AUDIT_RETENTION_DAYS)
    canonical_root = _validated_terminal_root(create=False)
    if not _same_lexical_path(root, canonical_root):
        raise ValueError("AUDIT_TERMINAL_OUTPUT_INVALID")
    root = canonical_root
    if not root.exists():
        return []
    removed: list[str] = []
    for pattern in (
        "*-audit-events.jsonl",
        "*-audit-terminal.json",
        "*-completion-authority.json",
    ):
        for path in sorted(root.glob(pattern)):
            prefix = path.name[:10]
            try:
                artifact_date = date.fromisoformat(prefix)
            except ValueError:
                continue
            if artifact_date >= cutoff:
                continue
            if path.is_symlink() or not path.is_file():
                raise ValueError("AUDIT_RETENTION_ARTIFACT_INVALID")
            path.unlink()
            removed.append(path.name)
    return removed


def _completion_authority_receipt(
    *, decision: dict[str, Any], terminal: dict[str, Any]
) -> dict[str, Any] | None:
    completion = decision.get("completionEvidence")
    if not isinstance(completion, dict):
        return None
    authority_id = str(
        decision.get("completionAuthorityId")
        or completion.get("completionAuthorityId")
        or completion.get("completion_authority_id")
        or ""
    )
    if not authority_id:
        return None
    return _sealed(
        {
            "schemaVersion": "COMPLETION_AUTHORITY_V2",
            "issuer": DECISION_ISSUER,
            "issueDate": decision.get("issueDate"),
            "completionAuthorityId": authority_id,
            "completionEvidenceSha256": decision.get("completionEvidenceSha256"),
            "completionEvidence": completion,
            "firstVerifiedTerminal": terminal.get("terminal"),
            "decisionReceiptSha256": decision.get("receiptSha256"),
        }
    )


def _ensure_immutable_completion_authority(
    *, root: Path, issue_date: str, decision: dict[str, Any], terminal: dict[str, Any]
) -> Path | None:
    receipt = _completion_authority_receipt(decision=decision, terminal=terminal)
    if receipt is None:
        return None
    path = _completion_authority_path(root, issue_date)
    if path.exists():
        try:
            existing = json.loads(
                _read_bounded_bytes(
                    path,
                    maximum=MAX_JSON_BYTES,
                    code="AUDIT_COMPLETION_AUTHORITY_INVALID",
                ).decode("utf-8")
            )
            existing = validate_completion_authority_receipt(
                existing, issue_date=issue_date
            )
        except (UnicodeError, json.JSONDecodeError, ValueError) as error:
            raise ValueError("AUDIT_COMPLETION_AUTHORITY_INVALID") from error
        if (
            existing.get("completionAuthorityId")
            != receipt.get("completionAuthorityId")
        ):
            raise ValueError("AUDIT_EVENT_CROSS_LINEAGE")
        return path
    validate_completion_authority_receipt(receipt, issue_date=issue_date)
    _atomic_write_bytes(
        path,
        (json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    return path


def _validate_audit_observation_history_bytes(payload: bytes) -> list[dict[str, Any]]:
    encoded_lines = payload.splitlines()
    if any(len(line) > MAX_AUDIT_EVENT_BYTES for line in encoded_lines):
        raise ValueError("AUDIT_EVENT_HISTORY_LIMIT_EXCEEDED")
    try:
        text = payload.decode("utf-8")
    except UnicodeError as error:
        raise ValueError("AUDIT_EVENT_HISTORY_INVALID") from error
    lines = text.splitlines()
    if len(lines) > AUDIT_EVENT_HISTORY_LIMIT:
        raise ValueError("AUDIT_EVENT_HISTORY_LIMIT_EXCEEDED")
    events: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    authority_id = ""
    for expected_sequence, line in enumerate(lines, start=1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError("AUDIT_EVENT_HISTORY_INVALID") from error
        if not isinstance(event, dict):
            raise ValueError("AUDIT_EVENT_HISTORY_INVALID")
        event_id = str(event.get("eventId") or "")
        if not event_id or event_id in seen_ids:
            raise ValueError("AUDIT_EVENT_REPLAY")
        if event.get("schemaVersion") != "AUDIT_OBSERVATION_EVENT_V1":
            raise ValueError("AUDIT_EVENT_HISTORY_INVALID")
        if event.get("sequence") != expected_sequence:
            raise ValueError("AUDIT_EVENT_REPLAY")
        previous_hash = str(event.get("previousEventHash") or "")
        expected_previous = "0" * 64 if expected_sequence == 1 else str(events[-1]["eventHash"])
        if previous_hash != expected_previous:
            raise ValueError("AUDIT_EVENT_REPLAY")
        current_authority = str(event.get("completionAuthorityId") or "")
        if current_authority and current_authority != "unverified" and not authority_id:
            authority_id = current_authority
        elif (
            current_authority
            and current_authority != "unverified"
            and authority_id
            and current_authority != authority_id
        ):
            raise ValueError("AUDIT_EVENT_CROSS_LINEAGE")
        event_hash = str(event.get("eventHash") or "")
        body = {key: value for key, value in event.items() if key != "eventHash"}
        if not _valid_sha256(event_hash) or hashlib.sha256(_canonical(body)).hexdigest() != event_hash:
            raise ValueError("AUDIT_EVENT_HISTORY_INVALID")
        seen_ids.add(event_id)
        events.append(event)
    return events


def validate_audit_observation_history(path: Path) -> list[dict[str, Any]]:
    """event historyをdecode前のbyte上限と単調hash chainで検証する。"""
    if not path.exists():
        return []
    payload = _read_bounded_bytes(
        path,
        maximum=MAX_AUDIT_HISTORY_BYTES,
        code="AUDIT_EVENT_HISTORY_INVALID",
        limit_code="AUDIT_EVENT_HISTORY_LIMIT_EXCEEDED",
    )
    return _validate_audit_observation_history_bytes(payload)


def _build_audit_observation_event(
    *, decision: dict[str, Any], previous: dict[str, Any] | None, sequence: int
) -> dict[str, Any]:
    previous_hash = str(previous.get("eventHash") if previous else "0" * 64)
    previous_authority = str(previous.get("completionAuthorityId") if previous else "")
    completion = decision.get("completionEvidence")
    completion_authority = str(decision.get("completionAuthorityId") or "")
    if not completion_authority and isinstance(completion, dict):
        completion_authority = str(completion.get("completionAuthorityId") or "")
    if (
        previous_authority
        and previous_authority != "unverified"
        and completion_authority
        and completion_authority != "unverified"
        and previous_authority != completion_authority
    ):
        raise ValueError("AUDIT_EVENT_CROSS_LINEAGE")
    completion_authority = completion_authority or previous_authority or "unverified"
    event_body = {
        "schemaVersion": "AUDIT_OBSERVATION_EVENT_V1",
        "eventId": hashlib.sha256(
            _canonical(
                {
                    "issueDate": decision.get("issueDate"),
                    "sequence": sequence,
                    "decisionReceiptSha256": decision.get("receiptSha256"),
                }
            )
        ).hexdigest(),
        "issueDate": decision.get("issueDate"),
        "sequence": sequence,
        "previousEventHash": previous_hash,
        "completionAuthorityId": completion_authority,
        "causeFingerprint": str(
            decision.get("causeFingerprint") or cause_fingerprint(decision)
        ),
        "action": decision.get("action"),
        "result": decision.get("terminal"),
        "observedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "decisionReceiptSha256": decision.get("receiptSha256"),
        "sourceSha256": str(decision.get("sourceSha256") or ""),
        "runtimeSha256": str(decision.get("runtimeSha256") or ""),
        "configSha256": str(decision.get("configSha256") or ""),
        "externalEvidenceSha256": str(decision.get("externalEvidenceSha256") or ""),
    }
    return {
        **event_body,
        "eventHash": hashlib.sha256(_canonical(event_body)).hexdigest(),
    }


def _audit_history_bytes(events: list[dict[str, Any]]) -> bytes:
    encoded_events = [
        json.dumps(event, ensure_ascii=False, sort_keys=True).encode("utf-8")
        for event in events
    ]
    if any(len(event) > MAX_AUDIT_EVENT_BYTES for event in encoded_events):
        raise ValueError("AUDIT_EVENT_HISTORY_LIMIT_EXCEEDED")
    payload = b"".join(event + b"\n" for event in encoded_events)
    if len(payload) > MAX_AUDIT_HISTORY_BYTES:
        raise ValueError("AUDIT_EVENT_HISTORY_LIMIT_EXCEEDED")
    return payload


def _json_document_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _audit_transaction_receipt(
    *, issue_date: str, history_payload: bytes, terminal: dict[str, Any]
) -> dict[str, Any]:
    return _sealed(
        {
            "schemaVersion": "AUDIT_TERMINAL_TRANSACTION_V1",
            "issuer": DECISION_ISSUER,
            "issueDate": issue_date,
            "historyJsonl": history_payload.decode("utf-8"),
            "historySha256": hashlib.sha256(history_payload).hexdigest(),
            "terminal": terminal,
            "terminalReceiptSha256": terminal.get("receiptSha256"),
        }
    )


def _validate_audit_transaction(
    value: object, *, issue_date: str
) -> tuple[bytes, dict[str, Any]]:
    try:
        transaction = _validate_sealed(
            value,
            schema_version="AUDIT_TERMINAL_TRANSACTION_V1",
            code="AUDIT_TERMINAL_TRANSACTION_INVALID",
        )
        history_text = transaction.get("historyJsonl")
        terminal = _validate_sealed(
            transaction.get("terminal"),
            schema_version="AUDIT_TERMINAL_V1",
            code="AUDIT_TERMINAL_TRANSACTION_INVALID",
        )
        if not isinstance(history_text, str):
            raise ValueError("AUDIT_TERMINAL_TRANSACTION_INVALID")
        history_payload = history_text.encode("utf-8")
        events = _validate_audit_observation_history_bytes(history_payload)
        if (
            transaction.get("issuer") != DECISION_ISSUER
            or transaction.get("issueDate") != issue_date
            or transaction.get("historySha256")
            != hashlib.sha256(history_payload).hexdigest()
            or transaction.get("terminalReceiptSha256")
            != terminal.get("receiptSha256")
            or terminal.get("issueDate") != issue_date
            or not events
            or terminal.get("eventSequence") != events[-1].get("sequence")
            or terminal.get("eventHash") != events[-1].get("eventHash")
        ):
            raise ValueError("AUDIT_TERMINAL_TRANSACTION_INVALID")
    except (UnicodeError, ValueError) as error:
        raise ValueError("AUDIT_TERMINAL_TRANSACTION_INVALID") from error
    return history_payload, terminal


def _recover_audit_transaction(root: Path, issue_date: str) -> None:
    journal = _audit_transaction_path(root, issue_date)
    if not journal.exists():
        return
    try:
        value = json.loads(
            _read_bounded_bytes(
                journal,
                maximum=MAX_AUDIT_HISTORY_BYTES + MAX_JSON_BYTES,
                code="AUDIT_TERMINAL_TRANSACTION_INVALID",
            ).decode("utf-8")
        )
        history_payload, terminal = _validate_audit_transaction(
            value, issue_date=issue_date
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("AUDIT_TERMINAL_TRANSACTION_INVALID") from error
    history_path = _audit_event_history_path(root, issue_date)
    journal_events = _validate_audit_observation_history_bytes(history_payload)
    live_events = validate_audit_observation_history(history_path)

    def is_prefix(
        prefix: list[dict[str, Any]], full: list[dict[str, Any]]
    ) -> bool:
        return len(prefix) <= len(full) and all(
            left.get("eventHash") == right.get("eventHash")
            for left, right in zip(prefix, full)
        )

    if live_events and is_prefix(journal_events, live_events):
        if len(live_events) > len(journal_events):
            projection = _load_terminal_projection(root, issue_date)
            if (
                projection.get("eventSequence") != live_events[-1].get("sequence")
                or projection.get("eventHash") != live_events[-1].get("eventHash")
            ):
                raise ValueError("AUDIT_TERMINAL_TRANSACTION_INVALID")
            journal.unlink()
            return
    elif live_events and not is_prefix(live_events, journal_events):
        raise ValueError("AUDIT_TERMINAL_TRANSACTION_INVALID")

    _atomic_write_bytes(history_path, history_payload)
    _atomic_write_bytes(
        root / f"{issue_date}-audit-terminal.json",
        _json_document_bytes(terminal),
    )
    journal.unlink()


def write_audit_terminal(decision: object) -> dict[str, Any]:
    try:
        if not isinstance(decision, dict) or decision.get("schemaVersion") not in {
            LEGACY_AUDIT_DECISION_SCHEMA,
            AUDIT_DECISION_SCHEMA,
        }:
            raise ValueError("AUDIT_DECISION_RECEIPT_INVALID")
        decision_value = _validate_sealed(
            decision,
            schema_version=str(decision["schemaVersion"]),
            code="AUDIT_DECISION_RECEIPT_INVALID",
        )
    except ValueError as error:
        raise ValueError("AUDIT_DECISION_RECEIPT_INVALID") from error
    if (
        decision_value.get("issuer") != DECISION_ISSUER
        or decision_value.get("terminal") not in AUDIT_TERMINALS
    ):
        raise ValueError("AUDIT_TERMINAL_INVALID")
    issue_date = _validate_issue_date(decision_value.get("issueDate"))
    if decision_value.get("terminal") == "audit_major_incident_open" and (
        decision_value.get("owner") != "News-Grasp Operations"
        or decision_value.get("nextAction")
        != "resume_same_date_recovery_from_verified_stop_point"
        or not _valid_sha256(decision_value.get("evidenceSha256"))
    ):
        raise ValueError("AUDIT_TERMINAL_INVALID")
    if decision_value.get("terminal") == "audit_observation_unverified" and (
        decision_value.get("publicStatus") not in {"green", "unverified"}
        or (
            decision_value.get("publicStatus") == "green"
            and not str(decision_value.get("completionAuthorityId") or "")
        )
        or not str(decision_value.get("reasonCode") or "")
    ):
        raise ValueError("AUDIT_TERMINAL_INVALID")
    if decision_value.get("terminal") in {
        "audit_normal_green",
        "audit_recovered_green",
    } and (
        not isinstance(decision_value.get("completionEvidence"), dict)
        or not _valid_sha256(decision_value.get("completionEvidenceSha256"))
        or decision_value["completionEvidence"].get("receiptSha256")
        != decision_value.get("completionEvidenceSha256")
        or not same_date_completion_green(
            issue_date, decision_value.get("completionEvidence")
        )
    ):
        raise ValueError("AUDIT_TERMINAL_INVALID")
    root = _validated_terminal_root()
    target = root / f"{issue_date}-audit-terminal.json"
    history_path = _audit_event_history_path(root, issue_date)
    if target.exists() and target.is_symlink():
        raise ValueError("AUDIT_TERMINAL_OUTPUT_INVALID")
    with _locked_directory(root):
        root = _validated_terminal_root()
        if not _same_lexical_path(target.parent, root) or not _same_lexical_path(
            history_path.parent, root
        ):
            raise ValueError("AUDIT_TERMINAL_OUTPUT_INVALID")
        _recover_audit_transaction(root, issue_date)
        events = validate_audit_observation_history(history_path)
        if len(events) >= AUDIT_EVENT_HISTORY_LIMIT:
            raise ValueError("AUDIT_EVENT_HISTORY_LIMIT_EXCEEDED")
        if any(
            event.get("decisionReceiptSha256") == decision_value["receiptSha256"]
            for event in events
        ):
            raise ValueError("AUDIT_EVENT_REPLAY")
        prune_audit_artifacts(
            root,
            issue_date=issue_date,
            owner=AUDIT_LIFECYCLE_OWNER,
        )
        event = _build_audit_observation_event(
            decision=decision_value,
            previous=events[-1] if events else None,
            sequence=len(events) + 1,
        )
        terminal = _sealed(
            {
                "schemaVersion": "AUDIT_TERMINAL_V1",
                "issuer": DECISION_ISSUER,
                "decisionReceiptSha256": decision_value["receiptSha256"],
                "issueDate": decision_value.get("issueDate"),
                "terminal": decision_value.get("terminal"),
                "scheduledAttemptStatus": decision_value.get("scheduledAttemptStatus"),
                "recoveryAttemptStatus": decision_value.get("recoveryAttemptStatus"),
                "publicStatus": decision_value.get("publicStatus"),
                "reasonCode": decision_value.get("reasonCode"),
                "owner": decision_value.get("owner"),
                "nextAction": decision_value.get("nextAction"),
                "evidenceSha256": decision_value.get("evidenceSha256"),
                "completionEvidenceSha256": decision_value.get(
                    "completionEvidenceSha256"
                ),
                "sourceDecision": decision_value.get("sourceDecision") or decision_value,
                "completionEvidence": decision_value.get("completionEvidence"),
                "eventId": event["eventId"],
                "eventSequence": event["sequence"],
                "previousEventHash": event["previousEventHash"],
                "eventHash": event["eventHash"],
                "completionAuthorityId": event["completionAuthorityId"],
                "causeFingerprint": event["causeFingerprint"],
                "observedAt": event["observedAt"],
            }
        )
        _ensure_immutable_completion_authority(
            root=root,
            issue_date=issue_date,
            decision=decision_value,
            terminal=terminal,
        )
        history_payload = _audit_history_bytes([*events, event])
        transaction = _audit_transaction_receipt(
            issue_date=issue_date,
            history_payload=history_payload,
            terminal=terminal,
        )
        journal = _audit_transaction_path(root, issue_date)
        _atomic_write_bytes(journal, _json_document_bytes(transaction))
        try:
            _atomic_write_bytes(history_path, history_payload)
            _atomic_write_bytes(target, _json_document_bytes(terminal))
        except Exception:
            raise
        else:
            journal.unlink()
    return terminal


def append_observation(decision: object) -> dict[str, Any]:
    """audit observationを既存のimmutable terminal transactionへ追記する。

    呼出側が固定terminalを直接上書きする経路を持たないよう、公開する追加入口は
    ``write_audit_terminal``へ集約する。履歴検証、replay拒否、lineage検証、WALは
    既存consumerが一元的に担当する。
    """
    terminal = write_audit_terminal(decision)
    return {
        "status": "green",
        "terminal": terminal,
        "eventSequence": terminal.get("eventSequence"),
        "eventHash": terminal.get("eventHash"),
        "completionAuthorityId": terminal.get("completionAuthorityId"),
    }


def _opened_path(descriptor: int, fallback: Path) -> Path:
    if os.name != "nt":
        return Path(os.path.realpath(fallback))
    import msvcrt

    handle = msvcrt.get_osfhandle(descriptor)
    buffer = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetFinalPathNameByHandleW(
        ctypes.c_void_p(handle), buffer, len(buffer), 0
    )
    if length <= 0 or length >= len(buffer):
        raise ValueError("AUDIT_RECOVERY_INPUT_INVALID")
    value = buffer.value
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return Path(value)


def _load(path: Path, *, expected_root: Path | None = None) -> dict[str, Any]:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ValueError("AUDIT_RECOVERY_INPUT_INVALID") from error
    try:
        if expected_root is not None:
            opened_path = _opened_path(descriptor, path).resolve()
            resolved_root = expected_root.resolve()
            if resolved_root not in opened_path.parents:
                raise ValueError("AUDIT_RECOVERY_INPUT_INVALID")
        with os.fdopen(descriptor, "rb") as stream:
            raw = stream.read(MAX_JSON_BYTES + 1)
    except (OSError, ValueError) as error:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise ValueError("AUDIT_RECOVERY_INPUT_INVALID") from error
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError("AUDIT_RECOVERY_INPUT_INVALID")
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("AUDIT_RECOVERY_INPUT_INVALID") from error
    if not isinstance(value, dict):
        raise ValueError("AUDIT_RECOVERY_INPUT_INVALID")
    return value


def _default_recovery_transaction_root() -> Path:
    override = os.environ.get("NEWS_GRASP_RECOVERY_TRANSACTION_ROOT", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".news-grasp-runtime" / "audit-recovery" / "transactions"


def ensure_audit_0640(
    *,
    issue_date: str,
    trigger: str = "automation_0640",
    transaction_root: Path | None = None,
    executor: Any | None = None,
    heartbeat_interval_seconds: float = 60,
) -> dict[str, Any]:
    """全triggerを一つのissue-date transactionへacquire-or-attachする。"""

    if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", issue_date) is None:
        raise ValueError("ISSUE_DATE_INVALID")
    if trigger not in {
        "deadman_0640",
        "automation_0640",
        "watcher_failure",
        "daily_compatibility_adapter",
        "direct_cli",
    }:
        raise ValueError("AUDIT_RECOVERY_TRIGGER_INVALID")
    store = RecoveryTransactionStore(
        transaction_root or _default_recovery_transaction_root()
    )
    owner_id = f"{trigger}:{os.getpid()}:{threading.get_ident()}"
    acquired = store.acquire(
        issue_date=issue_date,
        trigger=trigger,
        owner_id=owner_id,
        owner_pid=os.getpid(),
    )
    acquisition_status = str(acquired.get("status") or "")
    if acquisition_status == "terminal_projection":
        terminal = dict(acquired.get("terminal") or {})
        terminal.update(
            {
                "transactionStatus": acquisition_status,
                "transactionId": acquired.get("transactionId"),
                "fencingToken": acquired.get("fencingToken"),
                "processExitCode": acquired.get("processExitCode", 0),
            }
        )
        return terminal
    if acquisition_status in {"attached", "attached_pending", "attached_owner_alive"}:
        return {
            "schemaVersion": TRANSACTION_SCHEMA,
            "issueDate": issue_date,
            "transactionStatus": acquisition_status,
            "transactionId": acquired.get("transactionId"),
            "fencingToken": acquired.get("fencingToken"),
            "terminal": "audit_observation_unverified",
            "reasonCode": "AUDIT_RECOVERY_TRANSACTION_ATTACHED",
            "processExitCode": 3,
        }
    if acquisition_status not in {"acquired", "recovered_stale_owner"}:
        raise ValueError("AUDIT_RECOVERY_TRANSACTION_ACQUIRE_INVALID")

    fencing_token = int(acquired["fencingToken"])
    stop_heartbeat = threading.Event()
    heartbeat_lost = threading.Event()

    def _heartbeat() -> None:
        interval = max(0.01, float(heartbeat_interval_seconds))
        while not stop_heartbeat.wait(interval):
            try:
                store.renew(
                    issue_date=issue_date,
                    owner_id=owner_id,
                    fencing_token=fencing_token,
                )
            except ValueError as error:
                if str(error) == "AUDIT_RECOVERY_TRANSACTION_BUSY":
                    continue
                heartbeat_lost.set()
                return

    heartbeat = threading.Thread(
        target=_heartbeat,
        name=f"news-grasp-recovery-heartbeat-{issue_date}",
        daemon=True,
    )
    heartbeat.start()
    try:
        if executor is None:
            from tools import news_grasp_daily_control

            execute = news_grasp_daily_control._execute_audit_0640_owned
        else:
            execute = executor
        result = dict(execute(issue_date=issue_date))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        result = {
            "terminal": "audit_major_incident_open",
            "reasonCode": "AUDIT_RECOVERY_OWNER_FAILED_" + type(error).__name__.upper(),
            "issueDate": issue_date,
            "exitCode": 2,
        }
    finally:
        stop_heartbeat.set()
        heartbeat.join(timeout=2)

    terminal_name = str(result.get("terminal") or "")
    if heartbeat_lost.is_set():
        result = {
            "terminal": "audit_major_incident_open",
            "reasonCode": "AUDIT_RECOVERY_OWNER_LEASE_LOST",
            "issueDate": issue_date,
            "exitCode": 2,
        }
        terminal_name = result["terminal"]
    if terminal_name not in AUDIT_TERMINALS:
        result = {
            "terminal": "audit_major_incident_open",
            "reasonCode": "AUDIT_RECOVERY_OWNER_TERMINAL_INVALID",
            "issueDate": issue_date,
            "exitCode": 2,
        }
    exit_code = int(
        result.get("exitCode")
        if result.get("exitCode") is not None
        else 2
        if result["terminal"]
        in {"audit_major_incident_open", "audit_observation_unverified"}
        else 0
    )
    result["exitCode"] = exit_code
    completed = store.complete(
        issue_date=issue_date,
        owner_id=owner_id,
        fencing_token=fencing_token,
        terminal=result,
    )
    result.update(
        {
            "transactionSchemaVersion": TRANSACTION_SCHEMA,
            "transactionStatus": "terminal",
            "transactionId": completed.get("transactionId"),
            "fencingToken": fencing_token,
            "transactionReceiptSha256": completed.get("receiptSha256"),
            "processExitCode": exit_code,
        }
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    decide = sub.add_parser("decide")
    decide.add_argument("--input", type=Path, required=True)
    execute = sub.add_parser("execute")
    execute.add_argument("--input", type=Path, required=True)
    classify = sub.add_parser("classify-repair")
    classify.add_argument("--input", type=Path, required=True)
    verify_tree = sub.add_parser("verify-artifact-tree")
    verify_tree.add_argument("--artifact-root", type=Path, required=True)
    ensure = sub.add_parser("ensure-0640")
    ensure.add_argument("--issue-date", required=True)
    ensure.add_argument(
        "--trigger",
        choices=(
            "deadman_0640",
            "automation_0640",
            "watcher_failure",
            "daily_compatibility_adapter",
            "direct_cli",
        ),
        default="automation_0640",
    )
    args = parser.parse_args()
    if args.command == "decide":
        result = decide_audit_recovery(_load(args.input))
        if result.get("terminal"):
            write_audit_terminal(result)
    elif args.command == "execute":
        untrusted = _load(args.input)
        result = {
            "schemaVersion": TRANSACTION_SCHEMA,
            "issueDate": str(untrusted.get("issueDate") or ""),
            "terminal": "audit_major_incident_open",
            "reasonCode": "AUDIT_RECOVERY_DIRECT_EXECUTE_REQUIRES_CANONICAL_ENSURE",
            "nextAction": "invoke_ensure_0640",
            "processExitCode": 2,
        }
    elif args.command == "classify-repair":
        result = {"classification": classify_repair_payload(_load(args.input))}
    elif args.command == "verify-artifact-tree":
        artifact_root = args.artifact_root.resolve()
        if not artifact_root.is_dir() or artifact_root.is_symlink():
            raise ValueError("ARTIFACT_REPO_IDENTITY_INVALID")
        result = {
            "schemaVersion": "ARTIFACT_EXECUTABLE_TREE_VERIFICATION_V1",
            "artifactRepoHead": _validate_artifact_executable_tree(artifact_root),
            "status": "trusted_tree_bytes_match",
        }
    elif args.command == "ensure-0640":
        result = ensure_audit_0640(issue_date=args.issue_date, trigger=args.trigger)
    else:
        raise ValueError("AUDIT_RECOVERY_COMMAND_INVALID")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return int(result.get("processExitCode") or 0)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        code = str(error).splitlines()[0] or "AUDIT_RECOVERY_FAILED"
        print(code, file=sys.stderr)
        raise SystemExit(2) from None
