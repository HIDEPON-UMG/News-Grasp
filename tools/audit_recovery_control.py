from __future__ import annotations

import argparse
import contextlib
import ctypes
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any

from tools.news_grasp_operational_contract import (
    OPERATIONAL_TRUTH_ISSUER,
    finalize_audit_decision,
    select_recovery_branch_from_truth,
    validate_canonical_operational_registry,
    validate_operational_truth_receipt,
)


AUDIT_TERMINALS = {
    "audit_normal_green",
    "audit_recovered_green",
    "audit_major_incident_open",
}
SAME_DAY_PUBLIC_RECOVERY_PRIORITY = "same_day_public_recovery_first"
PUBLIC_GREEN_FOLLOWUP_PRIORITY = "root_cause_after_public_green"
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
CANONICAL_REPO_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_CONTROL_ROOT = Path(__file__).resolve().parents[1]
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
        if decision.get("workPriority") != PUBLIC_GREEN_FOLLOWUP_PRIORITY:
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


def _assign_windows_owned_job(process: subprocess.Popen[bytes]):
    if os.name != "nt":
        return None
    from ctypes import wintypes

    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    JobObjectExtendedLimitInformation = 9

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise ValueError("OWNED_PROCESS_JOB_CREATE_FAILED")
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel32.SetInformationJobObject(
        job,
        JobObjectExtendedLimitInformation,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ) or not kernel32.AssignProcessToJobObject(job, wintypes.HANDLE(process._handle)):
        kernel32.CloseHandle(job)
        process.terminate()
        process.wait(timeout=5)
        raise ValueError("OWNED_PROCESS_JOB_ASSIGNMENT_FAILED")
    return job


def _terminate_owned_process_tree(
    process: subprocess.Popen[bytes], windows_job: object | None
) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        if windows_job:
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
            kernel32.TerminateJobObject.restype = wintypes.BOOL
            if not kernel32.TerminateJobObject(windows_job, 1):
                raise ValueError("OWNED_PROCESS_JOB_TERMINATION_FAILED")
        else:
            process.terminate()
    else:
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            if windows_job:
                from ctypes import wintypes

                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
                kernel32.TerminateJobObject.restype = wintypes.BOOL
                if not kernel32.TerminateJobObject(windows_job, 1):
                    raise ValueError("OWNED_PROCESS_JOB_TERMINATION_FAILED")
            else:
                process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def _resume_windows_owned_process(process: subprocess.Popen[bytes]) -> None:
    if os.name != "nt":
        return
    from ctypes import wintypes

    ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
    ntdll.NtResumeProcess.restype = ctypes.c_long
    status = ntdll.NtResumeProcess(wintypes.HANDLE(process._handle))
    if status != 0:
        raise ValueError("OWNED_PROCESS_RESUME_FAILED")


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
    *, issue_date: str, run_intent: str, run_id: object
) -> dict[str, str]:
    artifact_root = str(CANONICAL_REPO_ROOT.resolve())
    ops_root = str(CANONICAL_RUNNER_STATE_PATH.parent.resolve())
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
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        ) | getattr(subprocess, "CREATE_SUSPENDED", 0x00000004)
    child_env = os.environ.copy()
    child_env["PYTHONUTF8"] = "1"
    child_env["PYTHONIOENCODING"] = "utf-8"
    if env_overrides:
        child_env.update(env_overrides)
    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        creationflags=creationflags,
        env=child_env,
        start_new_session=os.name != "nt",
    )
    windows_job = None
    stdout = bytearray()
    stderr = bytearray()
    exceeded = threading.Event()

    def drain(stream, target: bytearray) -> None:
        reader = getattr(stream, "read1", stream.read)
        while True:
            chunk = reader(64 * 1024)
            if not chunk:
                return
            remaining = MAX_JSON_BYTES + 1 - len(target)
            if remaining > 0:
                target.extend(chunk[:remaining])
            if len(target) > MAX_JSON_BYTES or len(chunk) > remaining:
                exceeded.set()
                return

    try:
        windows_job = _assign_windows_owned_job(process)
        _resume_windows_owned_process(process)
        threads = [
            threading.Thread(target=drain, args=(process.stdout, stdout), daemon=True),
            threading.Thread(target=drain, args=(process.stderr, stderr), daemon=True),
        ]
        for thread in threads:
            thread.start()
        deadline = time.monotonic() + timeout
        timed_out = False
        while process.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                _terminate_owned_process_tree(process, windows_job)
                break
            if exceeded.wait(min(0.05, remaining)):
                _terminate_owned_process_tree(process, windows_job)
                break
        for thread in threads:
            thread.join(timeout=5)
        if exceeded.is_set() or len(stdout) > MAX_JSON_BYTES or len(stderr) > MAX_JSON_BYTES:
            raise ValueError("BOUNDED_SUBPROCESS_OUTPUT_EXCEEDED")
        if timed_out:
            raise ValueError("BOUNDED_SUBPROCESS_TIMEOUT")
        return int(process.returncode or 0), bytes(stdout)
    finally:
        if process.poll() is None:
            _terminate_owned_process_tree(process, windows_job)
        if os.name == "nt" and windows_job:
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            if not kernel32.CloseHandle(windows_job):
                raise ValueError("OWNED_PROCESS_JOB_CLOSE_FAILED")


def _git_bytes(repo_root: Path, *args: str) -> bytes:
    return_code, stdout = _run_bounded(
        [
            "git",
            "-c",
            f"core.hooksPath={os.devnull}",
            "-c",
            "core.fsmonitor=false",
            "-c",
            f"core.attributesFile={os.devnull}",
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
            "-c",
            f"core.hooksPath={os.devnull}",
            "-c",
            "core.fsmonitor=false",
            "-c",
            f"core.attributesFile={os.devnull}",
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
    if (
        GIT_SHA_PATTERN.fullmatch(artifact_head) is None
        or artifact_head != trusted_head
    ):
        raise ValueError("ARTIFACT_EXECUTABLE_TREE_INVALID")
    dirty_executables = _git_text(
        artifact_repo_root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
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
    if dirty_executables:
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
        trusted_head,
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
    artifact_repo_root = _resolve_artifact_repo_root(payload)
    artifact_repo_head = _validate_artifact_executable_tree(artifact_repo_root)
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
        cwd=artifact_repo_root,
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
        repo_root=artifact_repo_root,
        date=issue_date,
        remote="origin",
        branch="main",
        public_base_url="https://hidepon-umg.github.io/News-Grasp/",
        wait_sec=wait_sec,
        poll_sec=poll_sec,
        primary_podcast_state_path=(
            artifact_repo_root / "build" / "youtube-podcast" / "uploads.json"
        ),
        deepdive_podcast_state_path=(
            artifact_repo_root
            / "build"
            / "youtube-podcast-deepdive"
            / "uploads.json"
        ),
        notification_state_path=(
            artifact_repo_root / "build" / "notification" / f"{issue_date}.json"
        ),
        producer_state_path=runner_state_path,
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
    expected_lineage = _completion_lineage(
        issue_date=issue_date,
        run_intent=str(value.get("runIntent") or ""),
        run_id=value.get("runId"),
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
                observed_repo = Path(str(observed_state.get("repo_dir") or "")).resolve()
                if observed_repo != CANONICAL_REPO_ROOT.resolve():
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
                "workPriority": PUBLIC_GREEN_FOLLOWUP_PRIORITY,
                "attemptLedgerWitnessSha256": attempt_witness["receiptSha256"],
                "completionEvidenceSha256": completion["receiptSha256"],
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
                "workPriority": PUBLIC_GREEN_FOLLOWUP_PRIORITY,
                "attemptLedgerWitnessSha256": attempt_witness["receiptSha256"],
                "scheduledFailureReceiptSha256": failure_sha,
                "recoveryAuthorityReceiptSha256": authority["receiptSha256"],
                "recoveryAuthorityLedgerWitnessSha256": witness["receiptSha256"],
                "completionEvidenceSha256": completion["receiptSha256"],
            }))
        if classification == "recoverable" and authority and recovery_status == "not_started":
            return finish(seal_audit_decision({
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


def execute_audit_recovery(payload: object) -> dict[str, Any]:
    """監査判断、production recovery 1回、same-gate再検証、typed terminalを一続きで閉じる。"""
    if not isinstance(payload, dict):
        raise ValueError("AUDIT_RECOVERY_INPUT_INVALID")
    issue_date = _validate_issue_date(payload.get("issueDate"))
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
    authority_path = _contained_file(
        payload.get("recoveryAuthorityPath"),
        root=artifact_repo_root / "build",
        code="RECOVERY_AUTHORITY_INVALID",
    )
    runner_path = CANONICAL_REPO_ROOT / "scripts" / "ops" / "news-grasp-runner.ps1"
    if not runner_path.is_file() or runner_path.is_symlink():
        raise ValueError("RECOVERY_RUNNER_INVALID")
    runner_sha256 = _file_sha256(runner_path)
    canonical_python = CANONICAL_REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    if not canonical_python.is_file() or canonical_python.is_symlink():
        raise ValueError("RECOVERY_RUNTIME_INTERPRETER_INVALID")
    validate_recovery_execution_manifest(
        payload.get("recoveryExecution"),
        issue_date=issue_date,
        authority_receipt_sha256=str(
            decision.get("recoveryAuthorityReceiptSha256") or ""
        ),
        artifact_repo_head=artifact_repo_head,
        runner_sha256=runner_sha256,
    )
    high_cost_workspace = CANONICAL_REPO_ROOT.parent
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
        "-HighCostWorkspaceRoot",
        str(high_cost_workspace),
        "-HighCostBudgetToolPath",
        str(CANONICAL_BROKER_PATH),
        "-ScheduledAuthorityEvidencePath",
        str(authority_path),
    ]
    return_code, _ = _run_bounded(command, cwd=artifact_repo_root, timeout=10800)
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
    write_audit_terminal(final_decision)
    return final_decision


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
    issue_date = _validate_issue_date(decision_value.get("issueDate"))
    if decision_value.get("terminal") == "audit_major_incident_open" and (
        decision_value.get("owner") != "News-Grasp Operations"
        or decision_value.get("nextAction")
        != "resume_same_date_recovery_from_verified_stop_point"
        or not _valid_sha256(decision_value.get("evidenceSha256"))
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
    root = CANONICAL_TERMINAL_ROOT.resolve()
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
            "owner": decision_value.get("owner"),
            "nextAction": decision_value.get("nextAction"),
            "evidenceSha256": decision_value.get("evidenceSha256"),
            "completionEvidenceSha256": decision_value.get(
                "completionEvidenceSha256"
            ),
            "sourceDecision": decision_value.get("sourceDecision") or decision_value,
            "completionEvidence": decision_value.get("completionEvidence"),
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
    execute = sub.add_parser("execute")
    execute.add_argument("--input", type=Path, required=True)
    classify = sub.add_parser("classify-repair")
    classify.add_argument("--input", type=Path, required=True)
    verify_tree = sub.add_parser("verify-artifact-tree")
    verify_tree.add_argument("--artifact-root", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "decide":
        result = decide_audit_recovery(_load(args.input))
        if result.get("terminal"):
            write_audit_terminal(result)
    elif args.command == "execute":
        result = execute_audit_recovery(_load(args.input))
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
