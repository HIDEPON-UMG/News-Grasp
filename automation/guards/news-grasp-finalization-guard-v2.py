"""Installed News-Grasp common-finalization guard V2 (read-only)."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat


MAX_BYTES = 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
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


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _validate_seal(value: object, *, code: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(code)
    body = {key: item for key, item in value.items() if key != "receiptSha256"}
    if value.get("receiptSha256") != hashlib.sha256(_canonical(body)).hexdigest():
        raise ValueError(code)
    return value


def _is_reparse(path: Path) -> bool:
    try:
        item = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(item.st_mode) or bool(
        int(getattr(item, "st_file_attributes", 0)) & 0x400
    )


def _contained(path: Path, root: Path, *, code: str) -> Path:
    candidate = Path(os.path.abspath(os.fspath(path)))
    boundary = Path(os.path.abspath(os.fspath(root)))
    try:
        candidate.relative_to(boundary)
    except ValueError as error:
        raise ValueError(code) from error
    cursor = boundary
    parts = candidate.relative_to(boundary).parts
    for part in parts:
        cursor = cursor / part
        if _is_reparse(cursor):
            raise ValueError(code)
    if not candidate.is_file() or _is_reparse(candidate):
        raise ValueError(code)
    return candidate


def _read_stable(path: Path, *, max_bytes: int, code: str) -> bytes:
    candidate = Path(os.path.abspath(path))
    try:
        before = candidate.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or _is_reparse(candidate)
            or int(getattr(before, "st_nlink", 1)) != 1
            or before.st_size > max_bytes
        ):
            raise ValueError(code)
        descriptor = os.open(
            candidate,
            os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            opened = os.fstat(descriptor)
            if (
                (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                or opened.st_size > max_bytes
            ):
                raise ValueError(code)
            chunks: list[bytes] = []
            remaining = max_bytes + 1
            while remaining > 0:
                chunk = os.read(descriptor, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
            after_handle = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        after = candidate.lstat()
    except (OSError, ValueError) as error:
        raise ValueError(code) from error
    if (
        len(raw) > max_bytes
        or (after_handle.st_dev, after_handle.st_ino, after_handle.st_size)
        != (before.st_dev, before.st_ino, before.st_size)
        or (after.st_dev, after.st_ino, after.st_size)
        != (before.st_dev, before.st_ino, before.st_size)
        or after.st_mtime_ns != before.st_mtime_ns
        or _is_reparse(candidate)
    ):
        raise ValueError(code)
    return raw


def _read_json(path: Path, *, root: Path, code: str) -> tuple[dict[str, object], str]:
    candidate = _contained(path, root, code=code)
    raw = _read_stable(candidate, max_bytes=MAX_BYTES, code=code)
    value = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(code)
    return value, hashlib.sha256(raw).hexdigest()


def evaluate(
    path: Path,
    *,
    finalization_receipt_path: Path | None = None,
    artifact_root: Path | None = None,
    expected_issue_date: str | None = None,
    expected_generation_id: str | None = None,
    expected_publish_commit: str | None = None,
    expected_finalization_receipt_sha256: str | None = None,
    expected_finalization_receipt_file_sha256: str | None = None,
    expected_result_sha256: str | None = None,
) -> dict[str, object]:
    result_path = Path(path)
    raw = _read_stable(result_path, max_bytes=MAX_BYTES, code="COMMON_FINALIZATION_RESULT_INVALID")
    result_file_sha256 = hashlib.sha256(raw).hexdigest()
    value = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("COMMON_FINALIZATION_RESULT_INVALID")
    value = _validate_seal(value, code="COMMON_FINALIZATION_RESULT_INVALID")
    body = {key: item for key, item in value.items() if key != "receiptSha256"}
    authority = _validate_seal(
        body.get("completionAuthority"), code="COMPLETION_AUTHORITY_V2_INVALID"
    )
    public_manifest = _validate_seal(
        authority.get("publicManifest"), code="PUBLIC_COMPLETION_MANIFEST_V2_INVALID"
    )
    outcome = _validate_seal(
        body.get("outcomeEnvelope"), code="COMPLETION_OUTCOME_ENVELOPE_V1_INVALID"
    )
    completion_v3 = body.get("completionStateVectorV3")
    state = body.get("stateVector")
    slo = outcome.get("slo") if isinstance(outcome, dict) else None
    readiness_debt = outcome.get("readinessDebt") if isinstance(outcome, dict) else None
    if (
        body.get("schemaVersion") != "NEWS_GRASP_COMMON_FINALIZATION_RESULT_V1"
        or body.get("publicStatus") != "green"
        or authority.get("schemaVersion") != "COMPLETION_AUTHORITY_V2"
        or not SHA256_RE.fullmatch(str(authority.get("receiptSha256") or ""))
        or authority.get("issueDate") != body.get("issueDate")
        or authority.get("publicManifestSha256") != public_manifest.get("receiptSha256")
        or public_manifest.get("schemaVersion")
        != "NEWS_GRASP_PUBLIC_COMPLETION_MANIFEST_V2"
        or public_manifest.get("issueDate") != body.get("issueDate")
        or not isinstance(state, dict)
        or set(state) != V3_FIELDS
        or state.get("publicCompletionStatus") != "green"
        or not isinstance(completion_v3, dict)
        or completion_v3.get("schemaVersion") != "COMPLETION_STATE_VECTOR_V3"
        or completion_v3.get("stateVector") != state
        or outcome.get("schemaVersion") != "COMPLETION_OUTCOME_ENVELOPE_V1"
        or outcome.get("completionAuthoritySha256") != authority.get("receiptSha256")
        or outcome.get("completionStateVectorV3Sha256")
        != hashlib.sha256(_canonical(completion_v3)).hexdigest()
        or not isinstance(slo, dict)
        or slo.get("schemaVersion") != "RECOVERY_SLO_ENVELOPE_V2"
        or not isinstance(readiness_debt, dict)
        or outcome.get("automationOutcome") != body.get("terminal")
        or body.get("exitCode") not in {0, 2}
    ):
        raise ValueError("COMMON_FINALIZATION_RESULT_INVALID")
    slo_failed = slo.get("status") == "public_green_slo_failed"
    has_readiness_debt = bool(readiness_debt)
    if (
        body.get("guardOk") is (not slo_failed)
        and body.get("exitCode") == (2 if slo_failed or has_readiness_debt else 0)
        and (not slo_failed or body.get("terminal") == "audit_major_incident_open")
    ) is not True:
        raise ValueError("COMMON_FINALIZATION_OUTCOME_INCONSISTENT")
    receipt_sha256 = None
    receipt_file_sha256 = None
    if finalization_receipt_path is not None:
        if artifact_root is None:
            raise ValueError("FINALIZATION_RECEIPT_ROOT_INVALID")
        receipt, receipt_file_sha256 = _read_json(
            finalization_receipt_path,
            root=Path(artifact_root).resolve(strict=True) / "build",
            code="FINALIZATION_RECEIPT_INVALID",
        )
        receipt = _validate_seal(receipt, code="FINALIZATION_RECEIPT_INVALID")
        receipt_sha256 = str(receipt.get("receiptSha256") or "")
        if (
            receipt.get("schemaVersion")
            != "NEWS_GRASP_RECOVERY_FINALIZATION_RECEIPT_V2"
            or not SHA256_RE.fullmatch(receipt_sha256)
            or receipt.get("issueDate") != expected_issue_date
            or receipt.get("generationId") != expected_generation_id
            or receipt.get("publishCommit") != expected_publish_commit
            or receipt.get("commonFinalizationResultReceiptSha256")
            != value.get("receiptSha256")
            or receipt.get("commonFinalizationResultFileSha256") != result_file_sha256
            or os.path.normcase(
                os.path.abspath(str(receipt.get("commonFinalizationResultPath") or ""))
            )
            != os.path.normcase(os.path.abspath(os.fspath(result_path)))
        ):
            raise ValueError("FINALIZATION_RECEIPT_RESULT_BINDING_INVALID")
        lineage = authority.get("producerLineage")
        if (
            not isinstance(lineage, dict)
            or lineage.get("generationId") != receipt.get("generationId")
            or lineage.get("publishCommit") != receipt.get("publishCommit")
        ):
            raise ValueError("FINALIZATION_RECEIPT_LINEAGE_INVALID")
        if expected_result_sha256 and value.get("receiptSha256") != expected_result_sha256:
            raise ValueError("FINALIZATION_RESULT_SHA256_MISMATCH")
        if expected_finalization_receipt_sha256 and receipt_sha256 != expected_finalization_receipt_sha256:
            raise ValueError("FINALIZATION_RECEIPT_SHA256_MISMATCH")
        if expected_finalization_receipt_file_sha256 and receipt_file_sha256 != expected_finalization_receipt_file_sha256:
            raise ValueError("FINALIZATION_RECEIPT_FILE_SHA256_MISMATCH")
    lineage = authority.get("producerLineage")
    return {
        "schemaVersion": "NEWS_GRASP_INSTALLED_FINALIZATION_GUARD_V2",
        "ok": body.get("guardOk") is True,
        "issueDate": body.get("issueDate"),
        "publicStatus": "green",
        "terminal": body.get("terminal"),
        "automationExitCode": body.get("exitCode"),
        "readinessStatus": state.get("nextRunReadinessStatus"),
        "commonFinalizationReceiptSha256": value["receiptSha256"],
        "finalizationReceiptSha256": receipt_sha256,
        "finalizationReceiptFileSha256": receipt_file_sha256,
        "commonFinalizationResultFileSha256": result_file_sha256,
        "generationId": lineage.get("generationId") if isinstance(lineage, dict) else None,
        "publishCommit": lineage.get("publishCommit") if isinstance(lineage, dict) else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument("--expected-issue-date")
    parser.add_argument("--expected-generation-id")
    parser.add_argument("--expected-publish-commit")
    parser.add_argument("--expected-finalization-receipt-sha256")
    parser.add_argument("--expected-finalization-receipt-file-sha256")
    parser.add_argument("--expected-result-sha256")
    args = parser.parse_args()
    try:
        result = evaluate(
            args.result.resolve(strict=True),
            finalization_receipt_path=args.receipt,
            artifact_root=args.artifact_root,
            expected_issue_date=args.expected_issue_date,
            expected_generation_id=args.expected_generation_id,
            expected_publish_commit=args.expected_publish_commit,
            expected_finalization_receipt_sha256=args.expected_finalization_receipt_sha256,
            expected_finalization_receipt_file_sha256=args.expected_finalization_receipt_file_sha256,
            expected_result_sha256=args.expected_result_sha256,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        print(json.dumps({"ok": False, "reason": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
