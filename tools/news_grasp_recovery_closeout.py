"""Public Green後のNews-Grasp recovery closeoutを有界に実行する。"""

from __future__ import annotations

import argparse
import base64
import contextlib
import ctypes
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from collections.abc import Iterator

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import news_grasp_recovery_receipts as receipts
from tools.news_grasp_operational_contract import (
    POST_PUBLIC_CLOSEOUT_BLOCKER,
    require_post_public_green_operation,
)


SCHEMA = "NEWS_GRASP_POST_PUBLIC_CLOSEOUT_V1"
NG_RC_04_FINALIZER_EXACT_ARGS_REPLAY = "NG_RC_04_FINALIZER_EXACT_ARGS_REPLAY"
NG_RC_05_ONE_SHOT_RECEIPT_DRIFT_RESEAL = "NG_RC_05_ONE_SHOT_RECEIPT_DRIFT_RESEAL"
MAX_JSON_BYTES = 1024 * 1024
MAX_RESEAL_TRANSACTION_BYTES = 3 * MAX_JSON_BYTES
ISSUE_DATE_RE = re.compile(r"^20\d{2}-\d{2}-\d{2}$")
RESEAL_TRANSACTION_SCHEMA = "NEWS_GRASP_RECOVERY_RECEIPT_RESEAL_TRANSACTION_V1"
KNOWN_RESEAL_FIELDS = frozenset(
    {
        "receiptSha256",
        "nonce",
        "issuedAt",
        "artifactRoot",
        "opsRoot",
        "productionRuntimeRoot",
        "liveBinRoot",
        "artifactHead",
        "opsHead",
        "runnerStatePath",
        "runnerScriptPath",
        "recoveryBranch",
        "resumeStage",
        "pythonExecutablePath",
        "capabilityReservationPath",
        "receiptResealCount",
        "recoveryAuthorityPath",
        "scheduledFailureReceiptPath",
    }
)


class PostPublicCloseoutError(RuntimeError):
    """closeoutの許可範囲内でGreenにできない。"""


def _system_powershell_executable() -> Path:
    """PATHに依存せずWindows標準PowerShellの実体を固定する。"""

    if os.name != "nt":
        raise PostPublicCloseoutError(
            f"{POST_PUBLIC_CLOSEOUT_BLOCKER}:windows_powershell_unavailable"
        )
    buffer = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetWindowsDirectoryW(buffer, len(buffer))
    if length <= 0 or length >= len(buffer):
        raise PostPublicCloseoutError(
            f"{POST_PUBLIC_CLOSEOUT_BLOCKER}:windows_directory_unavailable"
        )
    windows_root = Path(buffer.value)
    expected = (
        windows_root
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    candidate = expected.resolve(strict=True)
    if os.path.normcase(str(candidate)) != os.path.normcase(str(expected)):
        raise PostPublicCloseoutError(
            f"{POST_PUBLIC_CLOSEOUT_BLOCKER}:powershell_path_invalid"
        )
    cursor = candidate
    boundary = windows_root.resolve(strict=True)
    while True:
        metadata = os.lstat(cursor)
        if cursor.is_symlink() or int(
            getattr(metadata, "st_file_attributes", 0)
        ) & 0x400:
            raise PostPublicCloseoutError(
                f"{POST_PUBLIC_CLOSEOUT_BLOCKER}:powershell_path_invalid"
            )
        if os.path.normcase(str(cursor)) == os.path.normcase(str(boundary)):
            break
        if cursor.parent == cursor:
            raise PostPublicCloseoutError(
                f"{POST_PUBLIC_CLOSEOUT_BLOCKER}:powershell_path_invalid"
            )
        cursor = cursor.parent
    if not candidate.is_file():
        raise PostPublicCloseoutError(
            f"{POST_PUBLIC_CLOSEOUT_BLOCKER}:windows_powershell_unavailable"
        )
    return candidate


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path, *, maximum: int = MAX_JSON_BYTES) -> dict[str, Any]:
    candidate = Path(path).resolve(strict=True)
    if candidate.is_symlink() or candidate.stat().st_size > maximum:
        raise PostPublicCloseoutError(f"{POST_PUBLIC_CLOSEOUT_BLOCKER}:receipt_invalid")
    try:
        value = json.loads(candidate.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PostPublicCloseoutError(
            f"{POST_PUBLIC_CLOSEOUT_BLOCKER}:receipt_invalid"
        ) from error
    if not isinstance(value, dict):
        raise PostPublicCloseoutError(f"{POST_PUBLIC_CLOSEOUT_BLOCKER}:receipt_invalid")
    return value


def _issue_date(value: object) -> str:
    text = str(value or "")
    if ISSUE_DATE_RE.fullmatch(text) is None:
        raise PostPublicCloseoutError(
            f"{POST_PUBLIC_CLOSEOUT_BLOCKER}:issue_date_invalid"
        )
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d")
    except ValueError as error:
        raise PostPublicCloseoutError(
            f"{POST_PUBLIC_CLOSEOUT_BLOCKER}:issue_date_invalid"
        ) from error
    if parsed.strftime("%Y-%m-%d") != text:
        raise PostPublicCloseoutError(
            f"{POST_PUBLIC_CLOSEOUT_BLOCKER}:issue_date_invalid"
        )
    return text


def _contained_output(path: Path, *, root: Path) -> Path:
    boundary = Path(root).resolve(strict=True)
    candidate = Path(os.path.abspath(path))
    if boundary != candidate and boundary not in candidate.parents:
        raise PostPublicCloseoutError(
            f"{POST_PUBLIC_CLOSEOUT_BLOCKER}:output_path_invalid"
        )
    cursor = candidate
    while True:
        if cursor.exists():
            metadata = os.lstat(cursor)
            if cursor.is_symlink() or int(
                getattr(metadata, "st_file_attributes", 0)
            ) & 0x400:
                raise PostPublicCloseoutError(
                    f"{POST_PUBLIC_CLOSEOUT_BLOCKER}:output_path_invalid"
                )
        if os.path.normcase(str(cursor)) == os.path.normcase(str(boundary)):
            break
        parent = cursor.parent
        if parent == cursor:
            raise PostPublicCloseoutError(
                f"{POST_PUBLIC_CLOSEOUT_BLOCKER}:output_path_invalid"
            )
        cursor = parent
    resolved = candidate.resolve(strict=False)
    if boundary != resolved and boundary not in resolved.parents:
        raise PostPublicCloseoutError(
            f"{POST_PUBLIC_CLOSEOUT_BLOCKER}:output_path_invalid"
        )
    return candidate


def _reseal_transaction_path(artifact: Path) -> Path:
    return _contained_output(
        artifact
        / "build"
        / "recovery-authority"
        / "reseal-known-drift.transaction.json",
        root=artifact,
    )


@contextlib.contextmanager
def _exclusive_reseal_lock(artifact: Path, *, timeout: float = 5.0) -> Iterator[None]:
    """process crashでstaleにならないOS lockをboundedに保持する。"""

    lock_path = _contained_output(
        artifact / "build" / "recovery-authority" / "reseal-known-drift.lock",
        root=artifact,
    )
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    deadline = time.monotonic() + timeout
    acquired = False
    try:
        while not acquired:
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError as error:
                if time.monotonic() >= deadline:
                    raise PostPublicCloseoutError(
                        f"{POST_PUBLIC_CLOSEOUT_BLOCKER}:reseal_lock_busy"
                    ) from error
                time.sleep(0.05)
        yield
    finally:
        if acquired:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _rollback_reseal_transaction(
    *,
    transaction_path: Path,
    execution_path: Path,
    finalization_path: Path,
    artifact: Path,
    live: Path,
) -> None:
    transaction = receipts._validate_seal(  # noqa: SLF001
        _read_json(transaction_path, maximum=MAX_RESEAL_TRANSACTION_BYTES),
        schema=RESEAL_TRANSACTION_SCHEMA,
        code="RECOVERY_RECEIPT_RESEAL_TRANSACTION_INVALID",
    )
    if (
        Path(str(transaction.get("executionReceiptPath") or "")).resolve(
            strict=False
        )
        != execution_path
        or Path(str(transaction.get("finalizationReceiptPath") or "")).resolve(
            strict=False
        )
        != finalization_path
        or not isinstance(transaction.get("resealedExecution"), dict)
    ):
        raise PostPublicCloseoutError(
            f"{POST_PUBLIC_CLOSEOUT_BLOCKER}:reseal_transaction_invalid"
        )
    try:
        execution_bytes = base64.b64decode(
            str(transaction["previousExecutionBytesBase64"]), validate=True
        )
        finalization_bytes = base64.b64decode(
            str(transaction["previousFinalizationBytesBase64"]), validate=True
        )
        previous_execution = json.loads(execution_bytes.decode("utf-8-sig"))
        previous_finalization = json.loads(finalization_bytes.decode("utf-8-sig"))
        if (
            hashlib.sha256(execution_bytes).hexdigest()
            != transaction.get("previousExecutionFileSha256")
            or hashlib.sha256(finalization_bytes).hexdigest()
            != transaction.get("previousFinalizationFileSha256")
            or not isinstance(previous_execution, dict)
            or not isinstance(previous_finalization, dict)
        ):
            raise ValueError("receipt byte mismatch")
    except (KeyError, ValueError, UnicodeError, json.JSONDecodeError) as error:
        raise PostPublicCloseoutError(
            f"{POST_PUBLIC_CLOSEOUT_BLOCKER}:reseal_transaction_invalid"
        ) from error
    receipts.write_atomic_bytes(execution_path, execution_bytes, root=artifact)
    receipts.write_atomic_bytes(finalization_path, finalization_bytes, root=artifact)
    receipts.rollback_pending_execution_reseal(
        previous=previous_execution,
        resealed=transaction["resealedExecution"],
        previous_status=transaction.get("previousExecutionStatus"),
        live_bin_root=live,
    )
    transaction_path.unlink(missing_ok=True)


def _execution_body(path: Path) -> dict[str, Any]:
    try:
        return receipts._validate_execution_seal(  # noqa: SLF001 - shared receipt owner
            _read_json(path), code="RECOVERY_EXECUTION_RECEIPT_INVALID"
        )
    except ValueError as error:
        raise PostPublicCloseoutError(
            f"{POST_PUBLIC_CLOSEOUT_BLOCKER}:execution_receipt_invalid"
        ) from error


def _artifact_root_from_receipt(path: Path) -> Path:
    receipt_path = Path(path).resolve(strict=True)
    for parent in receipt_path.parents:
        if parent.name.casefold() == "build":
            return parent.parent.resolve(strict=True)
    raise PostPublicCloseoutError(f"{POST_PUBLIC_CLOSEOUT_BLOCKER}:artifact_root_unknown")


def _resolved_field(value: dict[str, Any], field: str) -> Path:
    try:
        return Path(str(value.get(field) or "")).resolve(strict=True)
    except OSError as error:
        raise PostPublicCloseoutError(
            f"{POST_PUBLIC_CLOSEOUT_BLOCKER}:{field}_invalid"
        ) from error


def _ops_root_from_execution(value: dict[str, Any]) -> Path:
    python_path = _resolved_field(value, "pythonExecutablePath")
    if (
        python_path.parent.name.casefold() == "scripts"
        and python_path.parent.parent.name.casefold() == ".venv"
    ):
        return python_path.parent.parent.parent.resolve(strict=True)
    return _resolved_field(value, "opsRoot")


def _tree_sha256(root: Path) -> str:
    rows: list[dict[str, str]] = []
    if root.is_dir():
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            if path.is_symlink():
                raise PostPublicCloseoutError(
                    f"{POST_PUBLIC_CLOSEOUT_BLOCKER}:public_tree_invalid"
                )
            rows.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "sha256": _file_sha256(path),
                }
            )
    return hashlib.sha256(_canonical(rows)).hexdigest()


def record_closeout_operation(
    *, artifact_root: Path, issue_date: str, operation: str
) -> dict[str, Any]:
    """allowlist判定とunknown blockerの証跡をappend-onlyで残す。"""

    artifact = Path(artifact_root).resolve(strict=True)
    issue_date = _issue_date(issue_date)
    allowed = True
    try:
        normalized = require_post_public_green_operation(operation)
        reason = "allowed"
    except ValueError:
        allowed = False
        normalized = str(operation or "").strip() or "missing"
        reason = POST_PUBLIC_CLOSEOUT_BLOCKER
    body: dict[str, Any] = {
        "schemaVersion": SCHEMA,
        "issueDate": issue_date,
        "operation": normalized,
        "status": "allowed" if allowed else POST_PUBLIC_CLOSEOUT_BLOCKER,
        "reasonCode": reason,
        "observedAt": datetime.now(timezone.utc).isoformat(),
    }
    body["receiptSha256"] = hashlib.sha256(_canonical(body)).hexdigest()
    ledger = _contained_output(
        artifact / "build" / "recovery-closeout" / f"{issue_date}.jsonl",
        root=artifact,
    )
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger = _contained_output(ledger, root=artifact)
    if ledger.exists() and not ledger.is_file():
        raise PostPublicCloseoutError(
            f"{POST_PUBLIC_CLOSEOUT_BLOCKER}:output_path_invalid"
        )
    with ledger.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(body, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    if not allowed:
        raise PostPublicCloseoutError(f"{POST_PUBLIC_CLOSEOUT_BLOCKER}:{normalized}")
    return {**body, "ledgerPath": str(ledger)}


def build_exact_finalizer_command(
    *,
    execution_receipt_path: Path,
    finalization_receipt_path: Path,
    publish_manifest_path: Path,
) -> dict[str, Any]:
    """validated execution receiptだけからfinalizer argvを再現する。"""

    execution_path = Path(execution_receipt_path).resolve(strict=True)
    raw = _execution_body(execution_path)
    artifact = _resolved_field(raw, "artifactRoot")
    ops = _resolved_field(raw, "opsRoot")
    runtime = _resolved_field(raw, "productionRuntimeRoot")
    live = _resolved_field(raw, "liveBinRoot")
    runner_state = Path(str(raw.get("runnerStatePath") or "")).resolve(strict=False)
    runner_script = _resolved_field(raw, "runnerScriptPath")
    try:
        execution = receipts.validate_recovery_execution_receipt(
            receipt_path=execution_path,
            issue_date=str(raw["issueDate"]),
            artifact_root=artifact,
            ops_root=ops,
            production_runtime_root=runtime,
            live_bin_root=live,
            runner_state_path=runner_state,
            runner_script_path=runner_script,
        )
        finalization = receipts.validate_finalization_receipt(
            receipt_path=Path(finalization_receipt_path),
            issue_date=str(raw["issueDate"]),
            artifact_root=artifact,
            ops_root=ops,
            production_runtime_root=runtime,
            live_bin_root=live,
            runner_state_path=runner_state,
            runner_script_path=runner_script,
            require_execution_consumed=False,
        )
        receipts.validate_execution_finalization_chain(
            execution_receipt_path=execution_path,
            execution_receipt=execution,
            finalization_receipt=finalization,
        )
    except (OSError, ValueError) as error:
        raise PostPublicCloseoutError(
            f"{POST_PUBLIC_CLOSEOUT_BLOCKER}:receipt_validation_failed"
        ) from error
    manifest_path = Path(publish_manifest_path).resolve(strict=True)
    if _resolved_field(finalization, "manifestPath") != manifest_path:
        raise PostPublicCloseoutError(
            f"{POST_PUBLIC_CLOSEOUT_BLOCKER}:manifest_path_mismatch"
        )
    command = [
        str(_system_powershell_executable()),
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(runner_script),
        "-RunIntent",
        "ScheduledRecoveryFull",
        "-DateStampOverride",
        str(execution["issueDate"]),
        "-RepoDirOverride",
        str(artifact),
        "-OpsRepoRootOverride",
        str(ops),
        "-PyExeOverride",
        str(execution["pythonExecutablePath"]),
        "-StateFileOverride",
        str(runner_state),
        "-LogDirOverride",
        str(live / "news-grasp-logs"),
        "-HighCostBindingPath",
        str(execution["capabilityReservationPath"]),
        "-HighCostBindingReceiptSha256",
        str(execution["capabilityReservationReceiptSha256"]),
        "-RecoveryRuntimeBindingPath",
        str(live / "news-grasp-recovery-runtime-binding-v1.json"),
        "-ScheduledAuthorityEvidencePath",
        str(execution["recoveryAuthorityPath"]),
        "-RecoveryExecutionReceiptPath",
        str(execution_path),
    ]
    if execution["recoveryBranch"] == "ResumeFromStage":
        command.extend(["-ResumeFromStage", str(execution["resumeStage"])])
    command.extend(
        [
            "-FinalizeVerifiedPublishManifest",
            str(manifest_path),
            "-RecoveryFinalizationReceiptPath",
            str(Path(finalization_receipt_path).resolve(strict=True)),
        ]
    )
    return {
        "schemaVersion": "NEWS_GRASP_EXACT_FINALIZER_COMMAND_V1",
        "status": "Green",
        "issueDate": execution["issueDate"],
        "recoveryBranch": execution["recoveryBranch"],
        "resumeStage": execution["resumeStage"],
        "runnerStatePath": str(runner_state),
        "argv": command,
        "argvSha256": hashlib.sha256(_canonical(command)).hexdigest(),
        "executionReceiptSha256": execution["receiptSha256"],
    }


def _reseal_known_receipt_drift_locked(
    *, execution_receipt_path: Path, finalization_receipt_path: Path
) -> dict[str, Any]:
    """public生成物を変えず既知driftを一括再封印・検証する。"""

    execution_path = Path(execution_receipt_path).resolve(strict=True)
    finalization_path = Path(finalization_receipt_path).resolve(strict=True)
    previous = _execution_body(execution_path)
    artifact = _artifact_root_from_receipt(execution_path)
    live = _resolved_field(previous, "liveBinRoot")
    transaction_path = _reseal_transaction_path(artifact)
    if transaction_path.exists():
        try:
            _rollback_reseal_transaction(
                transaction_path=transaction_path,
                execution_path=execution_path,
                finalization_path=finalization_path,
                artifact=artifact,
                live=live,
            )
        except (OSError, ValueError, RuntimeError) as error:
            raise PostPublicCloseoutError(
                f"{POST_PUBLIC_CLOSEOUT_BLOCKER}:reseal_rollback_failed"
            ) from error
        previous = _execution_body(execution_path)
    previous_execution_bytes = execution_path.read_bytes()
    previous_finalization_bytes = finalization_path.read_bytes()
    ops = _ops_root_from_execution(previous)
    runtime = _resolved_field(previous, "productionRuntimeRoot")
    live = _resolved_field(previous, "liveBinRoot")
    runner = (live / "news-grasp-runner.ps1").resolve(strict=True)
    runner_state = live / "news-grasp-runner-state.json"
    python = _resolved_field(previous, "pythonExecutablePath")
    capability = _resolved_field(previous, "capabilityReservationPath")
    authority_path = _resolved_field(previous, "recoveryAuthorityPath")
    failure_path = _resolved_field(previous, "scheduledFailureReceiptPath")
    authority = _read_json(authority_path)
    failure = _read_json(failure_path)
    issue_date = _issue_date(previous.get("issueDate"))
    try:
        record_closeout_operation(
            artifact_root=artifact,
            issue_date=issue_date,
            operation="receipt_reseal",
        )
        witness = receipts._validate_authority_via_broker(  # noqa: SLF001
            issue_date=issue_date,
            authority_path=authority_path,
            authority=authority,
            failure_receipt_sha256=str(failure.get("receiptSha256") or ""),
        )
        public_tree_before = _tree_sha256(artifact / "docs")
        final_previous = receipts._validate_seal(  # noqa: SLF001
            _read_json(finalization_path),
            schema=receipts.FINALIZATION_SCHEMA,
            code="FINALIZATION_RECEIPT_INVALID",
        )
        if receipts.consumption_status(
            receipt=final_previous, live_bin_root=live, kind="finalization"
        ) is not None:
            raise PostPublicCloseoutError(
                f"{POST_PUBLIC_CLOSEOUT_BLOCKER}:finalization_already_consumed"
            )
        previous_execution_status = receipts.consumption_status(
            receipt=previous, live_bin_root=live, kind="execution"
        )
        if previous_execution_status not in {None, "consumed_pending_operation"}:
            raise PostPublicCloseoutError(
                f"{POST_PUBLIC_CLOSEOUT_BLOCKER}:execution_already_applied"
            )
        if previous.get("receiptResealCount", 0) != 0:
            raise PostPublicCloseoutError(
                f"{POST_PUBLIC_CLOSEOUT_BLOCKER}:receipt_reseal_already_consumed"
            )
        resume_stage = str(previous.get("resumeStage") or "").strip() or None
        recovery_branch = "ResumeFromStage" if resume_stage else "ScheduledRecoveryFull"
        resealed = receipts.create_recovery_execution_receipt(
            issue_date=issue_date,
            artifact_root=artifact,
            ops_root=ops,
            production_runtime_root=runtime,
            live_bin_root=live,
            runner_state_path=runner_state,
            runner_script_path=runner,
            recovery_authority_path=authority_path,
            recovery_authority=authority,
            scheduled_failure_receipt_path=failure_path,
            scheduled_failure_receipt=failure,
            authority_ledger_witness=witness,
            audit_accepted_at=str(previous["auditAcceptedAt"]),
            recovery_branch=recovery_branch,
            resume_stage=resume_stage,
            python_executable_path=python,
            capability_reservation_path=capability,
            capability_reservation_receipt_sha256=str(
                previous["capabilityReservationReceiptSha256"]
            ),
            reserved_max_external_model_calls=int(
                previous["reservedMaxExternalModelCalls"]
            ),
            receipt_reseal_count=1,
        )
        drift_fields = sorted(
            field
            for field in set(previous) | set(resealed)
            if previous.get(field) != resealed.get(field)
        )
        unknown = sorted(set(drift_fields) - KNOWN_RESEAL_FIELDS)
        if unknown:
            raise PostPublicCloseoutError(
                f"{POST_PUBLIC_CLOSEOUT_BLOCKER}:unknown_receipt_drift:{','.join(unknown)}"
            )
        manifest_path = _resolved_field(final_previous, "manifestPath")
        manifest = _read_json(manifest_path)
        producer_path = _resolved_field(final_previous, "producerStatePath")
        finalization = receipts.create_finalization_receipt(
            issue_date=issue_date,
            artifact_root=artifact,
            ops_root=ops,
            production_runtime_root=runtime,
            live_bin_root=live,
            runner_state_path=runner_state,
            runner_script_path=runner,
            manifest_path=manifest_path,
            manifest=manifest,
            recovery_authority_path=authority_path,
            recovery_authority=authority,
            scheduled_failure_receipt_path=failure_path,
            scheduled_failure_receipt=failure,
            authority_ledger_witness=witness,
            execution_receipt_path=execution_path,
            execution_receipt=resealed,
            execution_receipt_file_sha256=hashlib.sha256(
                receipts.json_document_bytes(resealed)
            ).hexdigest(),
            producer_state_path=producer_path,
            producer_state_sha256=_file_sha256(producer_path),
            audit_accepted_at=str(previous["auditAcceptedAt"]),
        )
        transaction = receipts._seal(  # noqa: SLF001
            {
                "schemaVersion": RESEAL_TRANSACTION_SCHEMA,
                "issueDate": issue_date,
                "executionReceiptPath": str(execution_path),
                "finalizationReceiptPath": str(finalization_path),
                "previousExecutionStatus": previous_execution_status,
                "previousExecutionBytesBase64": base64.b64encode(
                    previous_execution_bytes
                ).decode("ascii"),
                "previousExecutionFileSha256": hashlib.sha256(
                    previous_execution_bytes
                ).hexdigest(),
                "previousFinalizationBytesBase64": base64.b64encode(
                    previous_finalization_bytes
                ).decode("ascii"),
                "previousFinalizationFileSha256": hashlib.sha256(
                    previous_finalization_bytes
                ).hexdigest(),
                "resealedExecution": resealed,
            }
        )
        receipts.write_atomic_json(transaction_path, transaction, root=artifact)
        try:
            receipts.write_atomic_json(execution_path, resealed, root=artifact)
            receipts.write_atomic_json(finalization_path, finalization, root=artifact)
            if previous_execution_status is None:
                receipts.consume_or_resume(
                    receipt=resealed, live_bin_root=live, kind="execution"
                )
            else:
                receipts.migrate_pending_execution_receipt(
                    previous=previous, resealed=resealed, live_bin_root=live
                )
            receipts.validate_recovery_execution_receipt(
                receipt_path=execution_path,
                issue_date=issue_date,
                artifact_root=artifact,
                ops_root=ops,
                production_runtime_root=runtime,
                live_bin_root=live,
                runner_state_path=runner_state,
                runner_script_path=runner,
            )
            receipts.validate_finalization_receipt(
                receipt_path=finalization_path,
                issue_date=issue_date,
                artifact_root=artifact,
                ops_root=ops,
                production_runtime_root=runtime,
                live_bin_root=live,
                runner_state_path=runner_state,
                runner_script_path=runner,
            )
            public_tree_after = _tree_sha256(artifact / "docs")
            if public_tree_before != public_tree_after:
                raise PostPublicCloseoutError(
                    f"{POST_PUBLIC_CLOSEOUT_BLOCKER}:public_artifact_mutated"
                )
            transaction_path.unlink(missing_ok=True)
        except (OSError, ValueError, RuntimeError, PostPublicCloseoutError) as error:
            try:
                _rollback_reseal_transaction(
                    transaction_path=transaction_path,
                    execution_path=execution_path,
                    finalization_path=finalization_path,
                    artifact=artifact,
                    live=live,
                )
            except (OSError, ValueError, RuntimeError) as rollback_error:
                raise PostPublicCloseoutError(
                    f"{POST_PUBLIC_CLOSEOUT_BLOCKER}:reseal_rollback_failed"
                ) from rollback_error
            raise error
    except PostPublicCloseoutError:
        raise
    except (OSError, ValueError, RuntimeError) as error:
        raise PostPublicCloseoutError(
            f"{POST_PUBLIC_CLOSEOUT_BLOCKER}:reseal_failed:{type(error).__name__}"
        ) from error
    return {
        "schemaVersion": "NEWS_GRASP_RECOVERY_RECEIPT_RESEAL_V1",
        "status": "Green",
        "issueDate": issue_date,
        "driftFields": drift_fields,
        "executionReceiptSha256": resealed["receiptSha256"],
        "finalizationReceiptSha256": finalization["receiptSha256"],
        "publicArtifactTreeSha256Before": public_tree_before,
        "publicArtifactTreeSha256After": public_tree_after,
        "publicArtifactUnchanged": True,
    }


def reseal_known_receipt_drift(
    *, execution_receipt_path: Path, finalization_receipt_path: Path
) -> dict[str, Any]:
    """同一artifactのone-shot resealをprocess間で直列化する公開入口。"""

    execution_path = Path(execution_receipt_path).resolve(strict=True)
    artifact = _artifact_root_from_receipt(execution_path)
    with _exclusive_reseal_lock(artifact):
        return _reseal_known_receipt_drift_locked(
            execution_receipt_path=execution_path,
            finalization_receipt_path=finalization_receipt_path,
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="News-Grasp bounded public Green closeout")
    sub = parser.add_subparsers(dest="command", required=True)
    exact = sub.add_parser("exact-finalizer-args")
    exact.add_argument("--execution-receipt", type=Path, required=True)
    exact.add_argument("--finalization-receipt", type=Path, required=True)
    exact.add_argument("--publish-manifest", type=Path, required=True)
    reseal = sub.add_parser("reseal-known-drift")
    reseal.add_argument("--execution-receipt", type=Path, required=True)
    reseal.add_argument("--finalization-receipt", type=Path, required=True)
    allow = sub.add_parser("authorize-operation")
    allow.add_argument("--artifact-root", type=Path, required=True)
    allow.add_argument("--date", required=True)
    allow.add_argument("--operation", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "exact-finalizer-args":
            result = build_exact_finalizer_command(
                execution_receipt_path=args.execution_receipt,
                finalization_receipt_path=args.finalization_receipt,
                publish_manifest_path=args.publish_manifest,
            )
        elif args.command == "reseal-known-drift":
            result = reseal_known_receipt_drift(
                execution_receipt_path=args.execution_receipt,
                finalization_receipt_path=args.finalization_receipt,
            )
        else:
            result = record_closeout_operation(
                artifact_root=args.artifact_root,
                issue_date=args.date,
                operation=args.operation,
            )
    except (OSError, ValueError, PostPublicCloseoutError) as error:
        print(str(error), file=sys.stderr)
        return 78
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
