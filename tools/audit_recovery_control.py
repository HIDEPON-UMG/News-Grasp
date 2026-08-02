from __future__ import annotations

import argparse
import contextlib
import ctypes
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any


AUDIT_TERMINALS = {
    "audit_normal_green",
    "audit_recovered_green",
    "audit_major_incident_open",
}
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
CANONICAL_REPO_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_TERMINAL_ROOT = CANONICAL_REPO_ROOT / "build" / "incidents"
CANONICAL_BROKER_PATH = Path.home() / "bin" / "ai-model-spawn-broker.py"
CANONICAL_RUNNER_STATE_PATH = Path.home() / "bin" / "news-grasp-runner-state.json"


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
    if not isinstance(decision, dict) or decision.get("schemaVersion") != "AUDIT_RECOVERY_DECISION_V1":
        raise ValueError("AUDIT_DECISION_RECEIPT_INVALID")
    terminal = decision.get("terminal")
    if terminal is not None and terminal not in AUDIT_TERMINALS:
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


def _run_bounded(command: list[str], *, cwd: Path, timeout: int) -> tuple[int, bytes]:
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        completed = subprocess.run(
            command,
            cwd=cwd,
            stdout=stdout_file,
            stderr=stderr_file,
            timeout=timeout,
            check=False,
            shell=False,
            creationflags=creationflags,
        )
        stdout_file.seek(0)
        stdout = stdout_file.read(MAX_JSON_BYTES + 1)
        if len(stdout) > MAX_JSON_BYTES:
            raise ValueError("BOUNDED_SUBPROCESS_OUTPUT_EXCEEDED")
        return completed.returncode, stdout


def _inspect_attempt_via_broker(*, issue_date: str) -> dict[str, Any]:
    if not CANONICAL_BROKER_PATH.is_file():
        raise ValueError("SCHEDULED_ATTEMPT_BROKER_UNAVAILABLE")
    return_code, stdout = _run_bounded(
        [
            sys.executable,
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
    scheduled: object, *, issue_date: str
) -> dict[str, Any]:
    if not isinstance(scheduled, dict) or scheduled.get("status") != "failed":
        raise ValueError("SCHEDULED_ATTEMPT_EVIDENCE_INVALID")
    failure_path = _contained_file(
        scheduled.get("failureReceiptPath"),
        root=CANONICAL_REPO_ROOT / "build",
        code="SCHEDULED_ATTEMPT_EVIDENCE_INVALID",
    )
    failure = _validate_sealed(
        _load(failure_path, expected_root=CANONICAL_REPO_ROOT / "build"),
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
    *, issue_date: str, authority_path_value: object, failure_receipt_sha256: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    authority_path = _contained_file(
        authority_path_value,
        root=CANONICAL_REPO_ROOT / "build",
        code="RECOVERY_AUTHORITY_INVALID",
    )
    if not CANONICAL_BROKER_PATH.is_file():
        raise ValueError("RECOVERY_AUTHORITY_BROKER_UNAVAILABLE")
    return_code, stdout = _run_bounded(
        [
            sys.executable,
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
        _load(authority_path, expected_root=CANONICAL_REPO_ROOT / "build"),
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
    wait_sec = int(payload.get("verificationWaitSec", 0))
    poll_sec = int(payload.get("verificationPollSec", 10))
    if wait_sec < 0 or wait_sec > 600 or poll_sec < 1 or poll_sec > 60:
        raise ValueError("COMPLETION_VERIFICATION_BUDGET_INVALID")
    quality_return_code, quality_stdout = _run_bounded(
        [
            sys.executable,
            "-m",
            "tools.validate_daily_quality",
            "--date",
            issue_date,
            "--require-deepdive",
            "--json",
        ],
        cwd=CANONICAL_REPO_ROOT,
        timeout=180,
    )
    if quality_return_code != 0:
        return None
    try:
        quality = json.loads(quality_stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    from tools.daily_self_heal import verify_publish_complete

    publish = verify_publish_complete(
        repo_root=CANONICAL_REPO_ROOT,
        date=issue_date,
        remote="origin",
        branch="main",
        public_base_url="https://hidepon-umg.github.io/News-Grasp/",
        wait_sec=wait_sec,
        poll_sec=poll_sec,
        notification_state_path=(
            CANONICAL_REPO_ROOT / "build" / "notification" / f"{issue_date}.json"
        ),
    )
    if publish.get("ok") is not True or publish.get("date") != issue_date:
        return None
    evidence_seed = {
        "quality": quality,
        "publish": publish,
        "runnerStateSha256": _file_sha256(runner_state_path),
    }
    return _sealed(
        {
            "schemaVersion": "SAME_DATE_COMPLETION_EVIDENCE_V1",
            "issuer": VERIFIED_COMPLETION_ISSUER,
            "issueDate": issue_date,
            "publishStatusIssueDate": issue_date,
            "runIntent": expected_run_intent,
            "runId": runner_state.get("run_id"),
            "checks": {field: True for field in COMPLETION_FIELDS},
            "evidenceSha256": {
                field: hashlib.sha256(
                    _canonical({"field": field, **evidence_seed})
                ).hexdigest()
                for field in COMPLETION_FIELDS
            },
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


def validate_recovery_execution_manifest(manifest: object) -> dict[str, Any]:
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
        "schemaVersion": "AUDIT_RECOVERY_DECISION_V1",
        "issueDate": issue_date,
        "classification": "incident_required",
        "action": "escalate_major_incident",
        "terminal": "audit_major_incident_open",
        "reasonCode": reason_code,
        "scheduledAttemptStatus": scheduled_status,
        "recoveryAttemptStatus": recovery_status,
        "publicStatus": "incomplete",
        "operationState": "incident_open",
    })


@contextlib.contextmanager
def _locked_directory(path: Path):
    path.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("AUDIT_TERMINAL_OUTPUT_INVALID")
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            yield
        finally:
            os.close(descriptor)
        return
    kernel32 = ctypes.windll.kernel32
    kernel32.GetFileAttributesW.restype = ctypes.c_uint32
    kernel32.CreateFileW.restype = ctypes.c_void_p
    attributes = kernel32.GetFileAttributesW(str(path))
    if attributes == 0xFFFFFFFF or attributes & 0x400:
        raise ValueError("AUDIT_TERMINAL_OUTPUT_INVALID")
    handle = kernel32.CreateFileW(
        str(path),
        0x0001,
        0x0001 | 0x0002,
        None,
        3,
        0x02000000 | 0x00200000,
        None,
    )
    if handle in (0, ctypes.c_void_p(-1).value):
        raise ValueError("AUDIT_TERMINAL_OUTPUT_INVALID")
    try:
        yield
    finally:
        kernel32.CloseHandle(handle)


def decide_audit_recovery(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("AUDIT_RECOVERY_INPUT_INVALID")
    issue_date = _validate_issue_date(payload.get("issueDate"))
    repair = payload.get("repairDecision")
    human = payload.get("humanImpact")
    if isinstance(human, dict) and any(
        human.get(field) is not True
        for field in ("noFocusTheft", "noUserMonitoring", "noAutoOpen")
    ):
        return _incident(
            issue_date=issue_date,
            scheduled_status="unverified",
            recovery_status="unverified",
            reason_code="HUMAN_IMPACT_CONTRACT_INVALID",
        )
    try:
        attempt_witness = _inspect_attempt_via_broker(issue_date=issue_date)
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError):
        return _incident(
            issue_date=issue_date,
            scheduled_status="unverified",
            recovery_status="unverified",
            reason_code="SCHEDULED_ATTEMPT_LEDGER_INVALID",
        )
    ledger_scheduled_status = str(attempt_witness["scheduledAttemptStatus"])
    recovery_status = str(attempt_witness["recoveryAttemptStatus"])
    classification = str((repair or {}).get("classification") or "incident_required")

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
            return seal_audit_decision({
                "schemaVersion": "AUDIT_RECOVERY_DECISION_V1",
                "issueDate": issue_date,
                "classification": "normal",
                "action": "none",
                "terminal": "audit_normal_green",
                "reasonCode": "SAME_DATE_COMPLETION_GREEN",
                "scheduledAttemptStatus": "succeeded",
                "recoveryAttemptStatus": "not_started",
                "publicStatus": "green",
                "operationState": "complete",
                "attemptLedgerWitnessSha256": attempt_witness["receiptSha256"],
                "completionEvidenceSha256": completion["receiptSha256"],
            })
        return _incident(
            issue_date=issue_date,
            scheduled_status="reserved",
            recovery_status=recovery_status,
            reason_code="SAME_DATE_COMPLETION_EVIDENCE_INVALID",
        )

    if ledger_scheduled_status == "failed":
        try:
            failure = _validate_scheduled_failure_path(
                {
                    "status": "failed",
                    "failureReceiptPath": payload.get("scheduledFailureReceiptPath"),
                },
                issue_date=issue_date,
            )
            failure_sha = str(failure["receiptSha256"])
            if failure_sha != attempt_witness.get("failureReceiptSha256"):
                raise ValueError("SCHEDULED_ATTEMPT_LEDGER_INVALID")
            authority, witness = _validate_recovery_authority_via_broker(
                issue_date=issue_date,
                authority_path_value=payload.get("recoveryAuthorityPath"),
                failure_receipt_sha256=failure_sha,
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
            return seal_audit_decision({
                "schemaVersion": "AUDIT_RECOVERY_DECISION_V1",
                "issueDate": issue_date,
                "classification": "recoverable",
                "action": "none",
                "terminal": "audit_recovered_green",
                "reasonCode": "RECOVERY_AND_SAME_DATE_COMPLETION_GREEN",
                "scheduledAttemptStatus": "failed",
                "recoveryAttemptStatus": "succeeded",
                "publicStatus": "green",
                "operationState": "complete",
                "attemptLedgerWitnessSha256": attempt_witness["receiptSha256"],
                "scheduledFailureReceiptSha256": failure_sha,
                "recoveryAuthorityReceiptSha256": authority["receiptSha256"],
                "recoveryAuthorityLedgerWitnessSha256": witness["receiptSha256"],
                "completionEvidenceSha256": completion["receiptSha256"],
            })
        if classification == "recoverable" and authority and recovery_status == "not_started":
            return seal_audit_decision({
                "schemaVersion": "AUDIT_RECOVERY_DECISION_V1",
                "issueDate": issue_date,
                "classification": "recoverable",
                "action": "scheduled_recovery",
                "terminal": None,
                "reasonCode": "TYPED_RECOVERY_AUTHORITY_READY",
                "scheduledAttemptStatus": "failed",
                "recoveryAttemptStatus": recovery_status,
                "publicStatus": "incomplete",
                "operationState": "recovery_required",
                "attemptLedgerWitnessSha256": attempt_witness["receiptSha256"],
                "recoveryAuthorityReceiptSha256": authority["receiptSha256"],
                "recoveryAuthorityLedgerWitnessSha256": witness["receiptSha256"],
            })
        if recovery_status == "started":
            return _incident(
                issue_date=issue_date,
                scheduled_status="failed",
                recovery_status="started",
                reason_code="RECOVERY_STARTED_BUT_COMPLETION_INVALID",
            )
        return _incident(
            issue_date=issue_date,
            scheduled_status="failed",
            recovery_status=recovery_status,
            reason_code="RECOVERY_AUTHORITY_INVALID",
        )

    return _incident(
        issue_date=issue_date,
        scheduled_status=ledger_scheduled_status,
        recovery_status=recovery_status,
        reason_code="REPAIR_CLASS_INCIDENT_REQUIRED",
    )


def write_audit_terminal(decision: object) -> dict[str, Any]:
    try:
        decision_value = _validate_sealed(
            decision,
            schema_version="AUDIT_RECOVERY_DECISION_V1",
            code="AUDIT_DECISION_RECEIPT_INVALID",
        )
    except ValueError as error:
        raise ValueError("AUDIT_DECISION_RECEIPT_INVALID") from error
    if (
        decision_value.get("issuer") != DECISION_ISSUER
        or decision_value.get("terminal") not in AUDIT_TERMINALS
    ):
        raise ValueError("AUDIT_TERMINAL_INVALID")
    root = CANONICAL_TERMINAL_ROOT.resolve()
    issue_date = _validate_issue_date(decision_value.get("issueDate"))
    target = root / f"{issue_date}-audit-terminal.json"
    if target.exists() and target.is_symlink():
        raise ValueError("AUDIT_TERMINAL_OUTPUT_INVALID")
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
        }
    )
    with _locked_directory(root):
        if target.parent.resolve() != root:
            raise ValueError("AUDIT_TERMINAL_OUTPUT_INVALID")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(terminal, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            if target.parent.resolve() != root:
                raise ValueError("AUDIT_TERMINAL_OUTPUT_INVALID")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return terminal


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


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    decide = sub.add_parser("decide")
    decide.add_argument("--input", type=Path, required=True)
    classify = sub.add_parser("classify-repair")
    classify.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "decide":
        result = decide_audit_recovery(_load(args.input))
        if result.get("terminal"):
            write_audit_terminal(result)
    elif args.command == "classify-repair":
        result = {"classification": classify_repair_payload(_load(args.input))}
    else:
        raise ValueError("AUDIT_RECOVERY_COMMAND_INVALID")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        code = str(error).splitlines()[0] or "AUDIT_RECOVERY_FAILED"
        print(code, file=sys.stderr)
        raise SystemExit(2) from None
