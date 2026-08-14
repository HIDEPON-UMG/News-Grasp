"""News-Grasp公開manifestのsingle producerとnormal/recovery共通finalizer。"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import shutil
import sys
import tempfile
import threading
import types
from typing import Any, Callable, Mapping

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.news_grasp_operational_contract import (
    COMPLETION_AUTHORITY_V2,
    COMPLETION_AUTHORITY_ISSUER,
    PUBLIC_COMPLETION_FIELDS,
    build_completion_outcome_envelope_v1,
    evaluate_completion_v3,
    evaluate_recovery_slo_v2,
    validate_completion_authority_v2,
)
from tools.verify_public_surface import verify_sealed_public_manifest
from tools import news_grasp_verified_storage as verified_storage


SHA256_RE = re.compile(r"[0-9a-f]{64}")
COMMIT_RE = re.compile(r"[0-9a-f]{40}")
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _seal(value: Mapping[str, Any]) -> dict[str, Any]:
    body = dict(value)
    body["receiptSha256"] = _sha(value)
    return body


def _atomic_json(path: Path, value: Mapping[str, Any], *, root: Path) -> None:
    verified_storage.atomic_write_json(
        path, value, root=root, code="FINALIZATION_OUTPUT_INVALID"
    )


def _load(path: Path, *, root: Path) -> dict[str, Any]:
    return verified_storage.read_json(
        path,
        root=root,
        max_bytes=4 * 1024 * 1024,
        code="FINALIZATION_ARTIFACT_INVALID",
    )


def _lineage_key(issue_date: str, generation_id: str, publish_commit: str) -> str:
    if (
        not re.fullmatch(r"\d{4}-\d{2}-\d{2}", issue_date)
        or not generation_id.strip()
        or COMMIT_RE.fullmatch(publish_commit) is None
    ):
        raise ValueError("MANIFEST_LINEAGE_INVALID")
    return _sha(
        {
            "issueDate": issue_date,
            "generationId": generation_id,
            "publishCommit": publish_commit,
        }
    )


@contextmanager
def _lineage_lock(path: Path, *, repo_root: Path):
    key = os.path.normcase(str(path.resolve()))
    with _LOCKS_GUARD:
        local_lock = _LOCKS.setdefault(key, threading.Lock())
    with local_lock:
        with verified_storage.pinned_directory(
            path.parent,
            anchor=repo_root,
            code="FINALIZATION_OUTPUT_INVALID",
        ):
            flags = (
                os.O_RDWR
                | os.O_CREAT
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            descriptor = os.open(path, flags, 0o600)
            opened = os.fstat(descriptor)
            path_item = os.lstat(path)
            if (
                (opened.st_dev, opened.st_ino)
                != (path_item.st_dev, path_item.st_ino)
                or _is_link_or_reparse(path)
                or int(getattr(opened, "st_nlink", 1)) != 1
            ):
                os.close(descriptor)
                raise ValueError("FINALIZATION_OUTPUT_INVALID")
            stream = os.fdopen(descriptor, "r+b")
            try:
                if os.name == "nt":
                    import msvcrt

                    stream.seek(0)
                    if stream.tell() == stream.seek(0, os.SEEK_END) == 0:
                        stream.write(b"0")
                        stream.flush()
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                yield
            finally:
                try:
                    stream.seek(0)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                finally:
                    stream.close()


def build_public_manifest_v2(
    *,
    issue_date: str,
    generation_id: str,
    publish_commit: str,
    producer_operation_id: str,
    evidence: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    if (
        set(evidence) != set(PUBLIC_COMPLETION_FIELDS)
        or not all(item.get("ok") is True for item in evidence.values())
        or COMMIT_RE.fullmatch(publish_commit) is None
        or SHA256_RE.fullmatch(producer_operation_id) is None
    ):
        raise ValueError("PUBLIC_COMPLETION_EVIDENCE_INVALID")
    return _seal(
        {
            "schemaVersion": "NEWS_GRASP_PUBLIC_COMPLETION_MANIFEST_V2",
            "issueDate": issue_date,
            "profile": "public-only-v3",
            "publicStatus": "green",
            "checks": {field: True for field in PUBLIC_COMPLETION_FIELDS},
            "evidenceSha256": {
                field: _sha(evidence[field]) for field in PUBLIC_COMPLETION_FIELDS
            },
            "producerLineage": {
                "generationId": generation_id,
                "publishCommit": publish_commit,
                "producerOperationId": producer_operation_id,
            },
        }
    )


def _manifest_paths(repo_root: Path, key: str) -> dict[str, Path]:
    repo = Path(os.path.abspath(repo_root))
    root = verified_storage.validated_managed_root(
        repo_root=repo,
        relative_parts=("build", "publish-complete", "coordinator"),
        create=True,
        code="FINALIZATION_OUTPUT_INVALID",
    )
    for child in ("locks", "journals", "manifests", "observations", "finalizations"):
        verified_storage.validated_managed_root(
            repo_root=repo,
            relative_parts=("build", "publish-complete", "coordinator", child),
            create=True,
            code="FINALIZATION_OUTPUT_INVALID",
        )
    return {
        "lock": root / "locks" / f"{key}.lock",
        "journal": root / "journals" / f"{key}.json",
        "manifest": root / "manifests" / f"{key}.public-v3.json",
        "legacy": root / "observations" / f"{key}.legacy-public.json",
        "observation": root / "observations" / f"{key}.remote-observation.json",
        "finalization": root / "finalizations" / f"{key}.json",
    }


def _seal_legacy_observation(
    value: Mapping[str, Any],
    *,
    issue_date: str,
    generation_id: str,
    publish_commit: str,
    public_manifest_sha256: str,
) -> dict[str, Any]:
    body = {key: item for key, item in value.items() if key != "receiptSha256"}
    body.update(
        {
            "schemaVersion": "NEWS_GRASP_LEGACY_PUBLIC_OBSERVATION_V1",
            "issueDate": issue_date,
            "generationId": generation_id,
            "runId": generation_id,
            "publish_commit": publish_commit,
            "publicManifestSha256": public_manifest_sha256,
        }
    )
    try:
        observed = datetime.fromisoformat(
            str(body.get("verified_at") or "").replace("Z", "+00:00")
        )
    except ValueError as error:
        raise ValueError("LEGACY_PUBLIC_OBSERVATION_INVALID") from error
    if observed.tzinfo is None:
        raise ValueError("LEGACY_PUBLIC_OBSERVATION_INVALID")
    body["verified_at"] = observed.astimezone(timezone.utc).isoformat()
    return _seal(body)


def _validate_legacy_observation(
    value: object,
    *,
    issue_date: str,
    generation_id: str,
    publish_commit: str,
    public_manifest_sha256: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("LEGACY_PUBLIC_OBSERVATION_INVALID")
    body = {key: item for key, item in value.items() if key != "receiptSha256"}
    if (
        body.get("schemaVersion") != "NEWS_GRASP_LEGACY_PUBLIC_OBSERVATION_V1"
        or body.get("issueDate") != issue_date
        or body.get("generationId") != generation_id
        or body.get("runId") != generation_id
        or body.get("publish_commit") != publish_commit
        or body.get("publicManifestSha256") != public_manifest_sha256
        or value.get("receiptSha256") != _sha(body)
    ):
        raise ValueError("LEGACY_PUBLIC_OBSERVATION_INVALID")
    try:
        observed = datetime.fromisoformat(
            str(body.get("verified_at") or "").replace("Z", "+00:00")
        )
    except ValueError as error:
        raise ValueError("LEGACY_PUBLIC_OBSERVATION_INVALID") from error
    if observed.tzinfo is None:
        raise ValueError("LEGACY_PUBLIC_OBSERVATION_INVALID")
    return dict(value)


def get_or_produce_manifest(
    *,
    repo_root: Path,
    issue_date: str,
    generation_id: str,
    publish_commit: str,
    cause_hash: str,
    producer: Callable[[], Mapping[str, Any]],
    observer: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """lineageごとのproducerを一つにし、失敗後のforward retryを一回に制限する。"""

    if SHA256_RE.fullmatch(cause_hash) is None:
        raise ValueError("MANIFEST_CAUSE_HASH_INVALID")
    key = _lineage_key(issue_date, generation_id, publish_commit)
    paths = _manifest_paths(repo_root, key)
    with _lineage_lock(paths["lock"], repo_root=Path(repo_root)):
        if paths["manifest"].is_file():
            manifest = _load(paths["manifest"], root=Path(repo_root))
            verify_sealed_public_manifest(manifest, issue_date=issue_date)
            observed = (
                dict(observer(manifest))
                if observer is not None
                else {
                    "ok": True,
                    "observationKind": "sealed_manifest_only",
                    "publishCommit": publish_commit,
                }
            )
            if (
                observed.get("ok") is not True
                or observed.get("publishCommit") != publish_commit
            ):
                raise RuntimeError("REMOTE_OBSERVATION_RED")
            observation = _seal(
                {
                    "schemaVersion": "NEWS_GRASP_REMOTE_OBSERVATION_RECEIPT_V1",
                    "issueDate": issue_date,
                    "generationId": generation_id,
                    "publishCommit": publish_commit,
                    "publicManifestSha256": manifest["receiptSha256"],
                    "causeHash": cause_hash,
                    "observedAt": datetime.now(timezone.utc).isoformat(),
                    "manifestRegenerated": False,
                    "observationKind": str(
                        observed.get("observationKind") or "remote_head"
                    ),
                    "observationEvidenceSha256": _sha(observed),
                }
            )
            _atomic_json(paths["observation"], observation, root=Path(repo_root))
            result = {
                "status": "existing_success_observed",
                "manifest": manifest,
                "journalPath": str(paths["journal"]),
                "manifestPath": str(paths["manifest"]),
                "observationPath": str(paths["observation"]),
            }
            if paths["legacy"].is_file():
                result["legacyObservation"] = _validate_legacy_observation(
                    _load(paths["legacy"], root=Path(repo_root)),
                    issue_date=issue_date,
                    generation_id=generation_id,
                    publish_commit=publish_commit,
                    public_manifest_sha256=manifest["receiptSha256"],
                )
                result["legacyObservationPath"] = str(paths["legacy"])
            return result

        journal = (
            _load(paths["journal"], root=Path(repo_root))
            if paths["journal"].is_file()
            else {}
        )
        invocation_count = int(journal.get("producerInvocationCount") or 0)
        previous_cause = str(journal.get("causeHash") or "")
        if journal.get("status") == "retry_exhausted" or invocation_count >= 2:
            raise RuntimeError("MANIFEST_FORWARD_RETRY_EXHAUSTED")
        if invocation_count and previous_cause != cause_hash:
            raise RuntimeError("MANIFEST_CAUSE_DRIFT")
        invocation_count += 1
        forward_retry_count = max(0, invocation_count - 1)
        running = _seal(
            {
                "schemaVersion": "NEWS_GRASP_MANIFEST_PRODUCER_JOURNAL_V1",
                "issueDate": issue_date,
                "generationId": generation_id,
                "publishCommit": publish_commit,
                "causeHash": cause_hash,
                "status": "producer_started",
                "producerInvocationCount": invocation_count,
                "forwardRetryCount": forward_retry_count,
            }
        )
        _atomic_json(paths["journal"], running, root=Path(repo_root))
        try:
            produced = dict(producer())
            public_manifest = dict(produced.get("publicManifest") or produced)
            verify_sealed_public_manifest(public_manifest, issue_date=issue_date)
            lineage = public_manifest.get("producerLineage") or {}
            if (
                lineage.get("generationId") != generation_id
                or lineage.get("publishCommit") != publish_commit
            ):
                raise ValueError("PUBLIC_COMPLETION_LINEAGE_INVALID")
            _atomic_json(paths["manifest"], public_manifest, root=Path(repo_root))
            legacy = produced.get("legacyObservation")
            if isinstance(legacy, Mapping):
                legacy = _seal_legacy_observation(
                    legacy,
                    issue_date=issue_date,
                    generation_id=generation_id,
                    publish_commit=publish_commit,
                    public_manifest_sha256=public_manifest["receiptSha256"],
                )
                _atomic_json(paths["legacy"], legacy, root=Path(repo_root))
            succeeded = _seal(
                {
                    **{key: value for key, value in running.items() if key != "receiptSha256"},
                    "status": "succeeded",
                    "publicManifestSha256": public_manifest["receiptSha256"],
                }
            )
            _atomic_json(paths["journal"], succeeded, root=Path(repo_root))
        except Exception as exc:
            failed = _seal(
                {
                    **{key: value for key, value in running.items() if key != "receiptSha256"},
                    "status": "retry_exhausted" if invocation_count >= 2 else "failed",
                    "failureType": type(exc).__name__,
                    "failureFingerprint": _sha(
                        {"causeHash": cause_hash, "type": type(exc).__name__, "message": str(exc)}
                    ),
                }
            )
            _atomic_json(paths["journal"], failed, root=Path(repo_root))
            if invocation_count >= 2:
                raise RuntimeError("MANIFEST_FORWARD_RETRY_EXHAUSTED") from exc
            raise RuntimeError("MANIFEST_PRODUCER_FAILED") from exc
        result = {
            "status": "produced",
            "manifest": public_manifest,
            "journalPath": str(paths["journal"]),
            "manifestPath": str(paths["manifest"]),
            "observationPath": "",
        }
        if isinstance(legacy, Mapping):
            result["legacyObservation"] = dict(legacy)
            result["legacyObservationPath"] = str(paths["legacy"])
        return result


def finalize_common(
    *,
    repo_root: Path,
    public_manifest: Mapping[str, Any],
    run_intent: str,
    transaction_started_at: str,
    public_green_at: str,
    done_at: str,
    readiness: Mapping[str, Any],
    actual_recovery_operation_count: int,
) -> dict[str, Any]:
    manifest = dict(public_manifest)
    verify_sealed_public_manifest(
        manifest, issue_date=str(public_manifest.get("issueDate") or "")
    )
    issue_date = str(manifest["issueDate"])
    lineage = dict(manifest["producerLineage"])
    green_terminal = (
        "audit_recovered_green"
        if actual_recovery_operation_count > 0
        else "audit_normal_green"
    )
    decision = _seal(
        {
            "schemaVersion": "NEWS_GRASP_COMMON_FINALIZATION_DECISION_V1",
            "issueDate": issue_date,
            "runIntent": run_intent,
            "publicManifestSha256": manifest["receiptSha256"],
            "greenTerminal": green_terminal,
        }
    )
    authority_id = _sha(
        {
            "issueDate": issue_date,
            "manifest": manifest["receiptSha256"],
            "lineage": lineage,
        }
    )
    authority = _seal(
        {
            "schemaVersion": COMPLETION_AUTHORITY_V2,
            "issuer": COMPLETION_AUTHORITY_ISSUER,
            "issueDate": issue_date,
            "completionAuthorityId": authority_id,
            "publicManifestSha256": manifest["receiptSha256"],
            "publicManifest": manifest,
            "producerLineage": lineage,
            "firstVerifiedTerminal": green_terminal,
            "decisionReceiptSha256": decision["receiptSha256"],
        }
    )
    validate_completion_authority_v2(authority, issue_date=issue_date)
    readiness_green = readiness.get("ok") is True
    scheduled_status = "failed" if actual_recovery_operation_count else "succeeded"
    recovery_status = "succeeded" if actual_recovery_operation_count else "not_required"
    state = evaluate_completion_v3(
        scheduled_attempt={"status": scheduled_status},
        recovery_attempt={"status": recovery_status},
        public_receipt={
            "status": "verified_green",
            "authorityId": authority_id,
        },
        readiness_probe={"status": "green" if readiness_green else "red"},
        audit_observation={"status": "verified"},
        external_dependency={"status": "not_required"},
        constitution_admission={"status": "green"},
    )
    slo = evaluate_recovery_slo_v2(
        issue_date=issue_date,
        transaction_started_at=transaction_started_at,
        public_green_at=public_green_at,
        done_at=done_at,
        actual_recovery_operation_count=actual_recovery_operation_count,
    )
    slo_failed = slo["status"] == "public_green_slo_failed"
    terminal = "audit_major_incident_open" if slo_failed else green_terminal
    readiness_debt = (
        {}
        if readiness_green
        else _seal(
            {
                "schemaVersion": "NEWS_GRASP_READINESS_DEBT_V1",
                "issueDate": issue_date,
                "reason": str(readiness.get("reason") or "readiness_red"),
                "observationSha256": _sha(readiness),
            }
        )
    )
    outcome = build_completion_outcome_envelope_v1(
        completion_state_vector_v3=state,
        completion_authority_sha256=authority["receiptSha256"],
        slo=slo,
        automation_outcome=terminal,
        readiness_debt=readiness_debt,
        generated_at=done_at,
    )
    result = _seal(
        {
            "schemaVersion": "NEWS_GRASP_COMMON_FINALIZATION_RESULT_V1",
            "issueDate": issue_date,
            "terminal": terminal,
            "guardOk": not slo_failed,
            "exitCode": 2 if slo_failed or readiness_debt else 0,
            "publicStatus": "green",
            "completionAuthority": authority,
            "stateVector": state["stateVector"],
            "completionStateVectorV3": state,
            "outcomeEnvelope": outcome,
        }
    )
    key = _lineage_key(issue_date, lineage["generationId"], lineage["publishCommit"])
    path = _manifest_paths(Path(repo_root), key)["finalization"]
    with _lineage_lock(path.with_suffix(".lock"), repo_root=Path(repo_root)):
        if path.is_file():
            return load_common_finalization(
                repo_root,
                issue_date=issue_date,
                generation_id=lineage["generationId"],
                publish_commit=lineage["publishCommit"],
            )
        _atomic_json(path, result, root=Path(repo_root))
    return result


def common_finalization_path(
    repo_root: Path, *, issue_date: str, generation_id: str, publish_commit: str
) -> Path:
    key = _lineage_key(issue_date, generation_id, publish_commit)
    return _manifest_paths(Path(repo_root), key)["finalization"]


def load_common_finalization(
    repo_root: Path, *, issue_date: str, generation_id: str, publish_commit: str
) -> dict[str, Any]:
    result = _load(
        common_finalization_path(
            repo_root,
            issue_date=issue_date,
            generation_id=generation_id,
            publish_commit=publish_commit,
        ),
        root=Path(repo_root),
    )
    body = {key: value for key, value in result.items() if key != "receiptSha256"}
    authority = result.get("completionAuthority")
    state = result.get("completionStateVectorV3")
    outcome = result.get("outcomeEnvelope")
    state_fields = {
        "scheduledAttemptStatus",
        "recoveryAttemptStatus",
        "publicCompletionStatus",
        "nextRunReadinessStatus",
        "auditObservationStatus",
        "externalDependencyStatus",
        "constitutionStatus",
        "operationalStatus",
    }
    try:
        validated_authority = validate_completion_authority_v2(
            authority, issue_date=issue_date
        )
    except ValueError as error:
        raise ValueError("COMMON_FINALIZATION_RESULT_INVALID") from error
    authority_lineage = validated_authority.get("producerLineage")
    outcome_body = (
        {key: value for key, value in outcome.items() if key != "receiptSha256"}
        if isinstance(outcome, dict)
        else {}
    )
    readiness_debt = outcome_body.get("readinessDebt")
    slo = outcome_body.get("slo") if isinstance(outcome_body.get("slo"), dict) else {}
    readiness_red = isinstance(state, dict) and state.get("nextRunReadinessStatus") == "red"
    slo_failed = slo.get("status") == "public_green_slo_failed"
    if (
        result.get("schemaVersion") != "NEWS_GRASP_COMMON_FINALIZATION_RESULT_V1"
        or result.get("issueDate") != issue_date
        or result.get("receiptSha256") != _sha(body)
        or result.get("publicStatus") != "green"
        or not isinstance(authority_lineage, dict)
        or authority_lineage.get("generationId") != generation_id
        or authority_lineage.get("publishCommit") != publish_commit
        or not isinstance(state, dict)
        or state.get("schemaVersion") != "COMPLETION_STATE_VECTOR_V3"
        or not isinstance(state.get("stateVector"), dict)
        or set(state["stateVector"]) != state_fields
        or any(state.get(field) != state["stateVector"].get(field) for field in state_fields)
        or result.get("stateVector") != state.get("stateVector")
        or state.get("publicCompletionStatus") != "green"
        or state.get("completionAuthorityId")
        != validated_authority.get("completionAuthorityId")
        or not isinstance(outcome, dict)
        or outcome_body.get("schemaVersion") != "COMPLETION_OUTCOME_ENVELOPE_V1"
        or outcome.get("receiptSha256") != _sha(outcome_body)
        or outcome_body.get("completionStateVectorV3Sha256") != _sha(state)
        or outcome_body.get("completionAuthoritySha256")
        != validated_authority.get("receiptSha256")
        or outcome_body.get("automationOutcome") != result.get("terminal")
        or result.get("terminal")
        not in {"audit_normal_green", "audit_recovered_green", "audit_major_incident_open"}
        or result.get("guardOk") is not (not slo_failed)
        or int(result.get("exitCode", -1))
        != (2 if slo_failed or bool(readiness_debt) else 0)
        or readiness_red != bool(readiness_debt)
    ):
        raise ValueError("COMMON_FINALIZATION_RESULT_INVALID")
    return result


def record_readiness_observation_v1(
    *,
    repo_root: Path,
    issue_date: str,
    completion_authority_id: str,
    observation: Mapping[str, Any],
    observed_at: str | None = None,
) -> dict[str, Any]:
    """immutable public authorityとは別lineageでfresh readiness観測を追記する。"""

    if not completion_authority_id or not isinstance(observation, Mapping):
        raise ValueError("READINESS_OBSERVATION_INVALID")
    observed = datetime.fromisoformat(
        str(observed_at or datetime.now(timezone.utc).isoformat()).replace("Z", "+00:00")
    )
    if observed.tzinfo is None:
        raise ValueError("READINESS_OBSERVATION_INVALID")
    receipt = _seal(
        {
            "schemaVersion": "NEWS_GRASP_READINESS_OBSERVATION_V1",
            "issueDate": issue_date,
            "completionAuthorityId": completion_authority_id,
            "status": "green" if observation.get("ok") is True else "red",
            "observationSha256": _sha(observation),
            "observation": dict(observation),
            "observedAt": observed.astimezone(timezone.utc).isoformat(),
        }
    )
    root = verified_storage.validated_managed_root(
        repo_root=Path(repo_root),
        relative_parts=("build", "recovery", "readiness-observations", issue_date),
        create=True,
        code="READINESS_OBSERVATION_INVALID",
    )
    path = root / f"{receipt['receiptSha256']}.json"
    if path.is_file():
        existing = _load(path, root=Path(repo_root))
        if existing != receipt:
            raise ValueError("READINESS_OBSERVATION_REPLAY")
    else:
        _atomic_json(path, receipt, root=Path(repo_root))
    return {**receipt, "receiptPath": str(path)}


def readiness_observation_green(value: object, *, issue_date: str) -> bool:
    if not isinstance(value, dict):
        return False
    body = {key: item for key, item in value.items() if key not in {"receiptSha256", "receiptPath"}}
    return bool(
        body.get("schemaVersion") == "NEWS_GRASP_READINESS_OBSERVATION_V1"
        and body.get("issueDate") == issue_date
        and body.get("status") == "green"
        and isinstance(body.get("observation"), dict)
        and body["observation"].get("ok") is True
        and body.get("observationSha256") == _sha(body["observation"])
        and value.get("receiptSha256") == _sha(body)
    )


def _legacy_evidence(legacy: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    publish = dict(legacy.get("publish") or {})
    podcasts = dict(legacy.get("podcasts") or {})
    evidence: dict[str, Mapping[str, Any]] = {
        "quality": dict(legacy.get("deepdive_shared_quality") or {}),
        "distributionManifest": {
            "ok": not bool((legacy.get("distribution_artifacts") or {}).get("missing")),
            "value": legacy.get("distribution_manifest"),
        },
        "publishStatus": {"ok": publish.get("ok") is True, "value": publish},
        "publicSurface": {
            "ok": publish.get("ok") is True,
            "pwa": legacy.get("pwa"),
            "audio": legacy.get("audio"),
        },
        "primaryPodcast": dict(podcasts.get("primary") or {}),
        "deepDivePodcast": dict(podcasts.get("deepdive") or {}),
        "notification": dict(legacy.get("notification") or {}),
    }
    if evidence["quality"].get("status") == "Green":
        evidence["quality"] = {**evidence["quality"], "ok": True}
    return evidence


def _git_output(repo_root: Path, *args: str) -> str:
    if any(
        "\x00" in str(item)
        or (str(item).startswith("-") and str(item) != "--")
        for item in args
    ):
        raise ValueError("FINALIZATION_GIT_ARGUMENT_INVALID")
    git_candidate = shutil.which("git")
    if not git_candidate:
        raise ValueError("FINALIZATION_GIT_OBSERVATION_FAILED")
    git_exe = Path(git_candidate).resolve(strict=True)
    if git_exe.name.casefold() not in {"git", "git.exe"} or _is_link_or_reparse(git_exe):
        raise ValueError("FINALIZATION_GIT_OBSERVATION_FAILED")
    creation_flags = (
        getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    )
    result = subprocess.run(
        [str(git_exe), *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
        creationflags=creation_flags,
    )
    if result.returncode != 0:
        raise ValueError("FINALIZATION_GIT_OBSERVATION_FAILED")
    return result.stdout.strip()


def _git_head(repo_root: Path) -> str:
    return _git_output(repo_root, "rev-parse", "HEAD").lower()


def observe_remote_publish_head(
    *, repo_root: Path, remote: str, branch: str, expected_commit: str
) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", remote):
        raise ValueError("FINALIZATION_GIT_REMOTE_INVALID")
    if (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", branch)
        or ".." in branch
        or branch.endswith(("/", "."))
    ):
        raise ValueError("FINALIZATION_GIT_BRANCH_INVALID")
    if COMMIT_RE.fullmatch(expected_commit or "") is None:
        raise ValueError("FINALIZATION_GIT_COMMIT_INVALID")
    output = _git_output(
        repo_root, "ls-remote", "--", remote, f"refs/heads/{branch}"
    )
    fields = output.split()
    remote_head = fields[0].lower() if fields else ""
    return {
        "ok": remote_head == expected_commit,
        "observationKind": "remote_head",
        "publishCommit": remote_head,
        "remote": remote,
        "branch": branch,
    }


def no_side_effect_loaded_smoke(
    repo_root: Path, *, installed_asset_root: Path | None = None
) -> dict[str, Any]:
    """新control planeのsource bytesとrouteを、write/networkなしでload検証する。"""

    root = Path(os.path.abspath(repo_root))
    if not root.is_dir() or _is_link_or_reparse(root):
        raise ValueError("NEWS_GRASP_LOADED_SMOKE_ROOT_INVALID")
    asset_root = (
        Path(os.path.abspath(installed_asset_root))
        if installed_asset_root is not None
        else root
    )
    if not asset_root.is_dir() or _is_link_or_reparse(asset_root):
        raise ValueError("NEWS_GRASP_LOADED_SMOKE_ROOT_INVALID")
    installed_assets = installed_asset_root is not None
    required = {
        "assetManifest": root / "config" / "news_grasp_automation_assets_v2.json",
        "dailyRoutes": root / "config" / "news_grasp_daily_control_routes.json",
        "fullRecoveryPolicy": root / "config" / "news_grasp_full_recovery_policy_v2.json",
        "automationPrompt": asset_root
        / ("prompts" if installed_assets else "automation/prompts")
        / "news-grasp-0640-v2.md",
        "finalizationGuard": asset_root
        / ("guards" if installed_assets else "automation/guards")
        / "news-grasp-finalization-guard-v2.py",
        "operationalContract": root / "tools" / "news_grasp_operational_contract.py",
        "recoveryTransaction": root / "tools" / "news_grasp_recovery_transaction.py",
        "finalizationCoordinator": Path(__file__),
    }
    assets = _load(required["assetManifest"], root=root)
    routes = _load(required["dailyRoutes"], root=root)
    policy = _load(required["fullRecoveryPolicy"], root=root)
    declared_sources = {
        str(item.get("installPath")): str(item.get("sourcePath"))
        for item in assets.get("assets", [])
        if isinstance(item, Mapping)
    }
    identities: dict[str, dict[str, str]] = {}
    guard_raw: bytes | None = None
    for name, path in required.items():
        candidate = Path(os.path.abspath(path))
        if not candidate.is_file() or _is_link_or_reparse(candidate):
            raise ValueError("NEWS_GRASP_LOADED_SMOKE_ARTIFACT_INVALID")
        if candidate not in {required["automationPrompt"], required["finalizationGuard"]}:
            try:
                candidate.relative_to(root)
            except ValueError as error:
                raise ValueError("NEWS_GRASP_LOADED_SMOKE_ARTIFACT_INVALID") from error
        else:
            try:
                candidate.relative_to(asset_root)
            except ValueError as error:
                raise ValueError("NEWS_GRASP_LOADED_SMOKE_ARTIFACT_INVALID") from error
        try:
            raw = verified_storage.read_bytes(
                candidate,
                root=asset_root
                if name in {"automationPrompt", "finalizationGuard"}
                else root,
                max_bytes=16 * 1024 * 1024,
                code="NEWS_GRASP_LOADED_SMOKE_ARTIFACT_INVALID",
            )
        except (OSError, ValueError) as error:
            raise ValueError("NEWS_GRASP_LOADED_SMOKE_ARTIFACT_INVALID") from error
        if installed_assets and name in {"automationPrompt", "finalizationGuard"}:
            install_path = str(candidate.relative_to(asset_root)).replace("\\", "/")
            source_relative = declared_sources.get(install_path)
            if not source_relative:
                raise ValueError("NEWS_GRASP_LOADED_SMOKE_ASSET_BINDING_INVALID")
            try:
                source_raw = verified_storage.read_bytes(
                    root / source_relative,
                    root=root,
                    max_bytes=16 * 1024 * 1024,
                    code="NEWS_GRASP_LOADED_SMOKE_ASSET_BINDING_INVALID",
                )
            except (OSError, ValueError) as error:
                raise ValueError("NEWS_GRASP_LOADED_SMOKE_ASSET_BINDING_INVALID") from error
            if hashlib.sha256(raw).digest() != hashlib.sha256(source_raw).digest():
                raise ValueError("NEWS_GRASP_LOADED_SMOKE_ASSET_DRIFT")
        if candidate.suffix.casefold() == ".py":
            compile(raw, str(candidate), "exec")
        if name == "finalizationGuard":
            guard_raw = raw
        identities[name] = {
            "path": str(candidate),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    from tools import news_grasp_operational_contract as loaded_operational_contract
    from tools import news_grasp_recovery_transaction as loaded_recovery_transaction

    if guard_raw is None:
        raise ValueError("NEWS_GRASP_LOADED_SMOKE_CONTRACT_INVALID")
    loaded_guard = types.ModuleType("news_grasp_loaded_finalization_guard_v2")
    loaded_guard.__file__ = str(required["finalizationGuard"])
    exec(compile(guard_raw, str(required["finalizationGuard"]), "exec"), loaded_guard.__dict__)
    asset_bindings = {
        str(item.get("assetId")): str(item.get("sourcePath"))
        for item in assets.get("assets", [])
        if isinstance(item, Mapping)
    }
    route_bindings = {
        str(item.get("routeId")): (
            str(item.get("consumerPath")), str(item.get("consumerSymbol"))
        )
        for item in routes.get("routes", [])
        if isinstance(item, Mapping)
    }
    if (
        assets.get("schemaVersion") != "NEWS_GRASP_AUTOMATION_ASSET_MANIFEST_V2"
        or asset_bindings.get("audit-recovery-prompt-v2")
        != "automation/prompts/news-grasp-0640-v2.md"
        or asset_bindings.get("common-finalization-guard-v2")
        != "automation/guards/news-grasp-finalization-guard-v2.py"
        or route_bindings.get("common_finalizer")
        != ("tools/news_grasp_finalization.py", "coordinate_publish")
        or policy.get("schemaVersion") != "NEWS_GRASP_FULL_RECOVERY_POLICY_V2"
        or policy.get("sourceStatus") != "UserConfirmed"
        or policy.get("fullAllowedOnlyBeforeRunnerWithoutArtifactDelta") is not True
        or policy.get("maxFullE2EAttempts") != 0
        or Path(str(loaded_operational_contract.__file__)).resolve()
        != required["operationalContract"].resolve()
        or Path(str(loaded_recovery_transaction.__file__)).resolve()
        != required["recoveryTransaction"].resolve()
        or not callable(loaded_recovery_transaction.validate_actual_launch_identity_v1)
        or not callable(loaded_guard.evaluate)
        or not callable(coordinate_publish)
    ):
        raise ValueError("NEWS_GRASP_LOADED_SMOKE_CONTRACT_INVALID")
    return {
        "schemaVersion": "NEWS_GRASP_NO_SIDE_EFFECT_LOADED_SMOKE_V1",
        "ok": True,
        "repoRoot": str(root),
        "moduleIdentity": identities,
        "loadedContracts": {
            "completionAuthority": COMPLETION_AUTHORITY_V2,
            "publicCompletionFieldCount": len(PUBLIC_COMPLETION_FIELDS),
            "commonFinalizer": "coordinate_publish",
            "actualLaunchIdentity": "NEWS_GRASP_ACTUAL_LAUNCH_IDENTITY_V1",
            "operationalContractModule": str(
                Path(str(loaded_operational_contract.__file__)).resolve()
            ),
            "recoveryTransactionModule": str(
                Path(str(loaded_recovery_transaction.__file__)).resolve()
            ),
            "finalizationGuardEntrypoint": "evaluate",
        },
        "mutationCount": 0,
        "externalCallCount": 0,
        "scheduledTaskObservationCount": 0,
        "scheduledTaskMutationCount": 0,
    }


def coordinate_publish(args: argparse.Namespace) -> dict[str, Any]:
    from tools import daily_self_heal

    repo_root = args.repo_root.resolve()
    publish_commit = _git_head(repo_root)
    producer_operation_id = _sha(
        {"issueDate": args.date, "generationId": args.run_id, "runIntent": args.run_intent}
    )
    legacy_output = repo_root / "build" / "publish-complete" / f"{args.date}.json"

    def producer() -> Mapping[str, Any]:
        legacy = daily_self_heal.verify_public_completion(
            repo_root=repo_root,
            ops_repo_root=args.ops_repo_root,
            date=args.date,
            remote=args.remote,
            branch=args.branch,
            public_base_url=args.public_base_url,
            wait_sec=args.wait_sec,
            poll_sec=args.poll_sec,
            notification_state_path=args.notification_state,
            producer_state_path=args.producer_state,
        )
        if legacy.get("ok") is not True or legacy.get("public_status") != "green":
            raise ValueError(str(legacy.get("reason") or "PUBLIC_VERIFICATION_RED"))
        observed_commit = str(legacy.get("deploy_head") or legacy.get("local_head") or "").lower()
        if observed_commit != publish_commit:
            raise ValueError("PUBLIC_VERIFICATION_COMMIT_DRIFT")
        verified_at = str(legacy.get("verified_at") or datetime.now(timezone.utc).isoformat())
        legacy = {
            **legacy,
            "verified_at": verified_at,
            "scheduled_attempt_status": (
                "failed_then_recovered"
                if args.run_intent == "ScheduledRecoveryFull"
                else "succeeded"
            ),
            "recovery_attempt_status": (
                "succeeded"
                if args.run_intent == "ScheduledRecoveryFull"
                else "not_required"
            ),
            "source_commit": str(legacy.get("local_head") or publish_commit),
            "artifact_commit": str(legacy.get("artifact_commit") or publish_commit),
            "publish_commit": publish_commit,
        }
        public = build_public_manifest_v2(
            issue_date=args.date,
            generation_id=args.run_id,
            publish_commit=publish_commit,
            producer_operation_id=producer_operation_id,
            evidence=_legacy_evidence(legacy),
        )
        return {"publicManifest": public, "legacyObservation": legacy}

    cause_hash = _sha(
        {
            "issueDate": args.date,
            "generationId": args.run_id,
            "publishCommit": publish_commit,
            "notificationState": str(args.notification_state),
        }
    )
    produced = get_or_produce_manifest(
        repo_root=repo_root,
        issue_date=args.date,
        generation_id=args.run_id,
        publish_commit=publish_commit,
        cause_hash=cause_hash,
        producer=producer,
        observer=lambda _manifest: observe_remote_publish_head(
            repo_root=repo_root,
            remote=args.remote,
            branch=args.branch,
            expected_commit=publish_commit,
        ),
    )
    legacy = produced.get("legacyObservation")
    if not isinstance(legacy, Mapping):
        raise ValueError("LEGACY_PUBLIC_OBSERVATION_MISSING")
    _atomic_json(legacy_output, legacy, root=repo_root)
    try:
        readiness = daily_self_heal.verify_live_runner_readiness(
            repo_root=repo_root,
            ops_repo_root=args.ops_repo_root,
            date=args.date,
        )
    except Exception as exc:
        readiness = {
            "ok": False,
            "reason": "readiness_observation_unavailable",
            "exceptionType": type(exc).__name__,
        }
    green_at = str(legacy.get("verified_at") or datetime.now(timezone.utc).isoformat())
    done_at = datetime.now(timezone.utc).isoformat()
    common = finalize_common(
        repo_root=repo_root,
        public_manifest=produced["manifest"],
        run_intent=args.run_intent,
        transaction_started_at=args.transaction_started_at,
        public_green_at=green_at,
        done_at=done_at,
        readiness=readiness,
        actual_recovery_operation_count=args.actual_recovery_operation_count,
    )
    finalization_path = common_finalization_path(
        repo_root,
        issue_date=args.date,
        generation_id=args.run_id,
        publish_commit=publish_commit,
    )
    legacy = {
        **dict(legacy),
        "runId": args.run_id,
        "publicManifestPath": produced["manifestPath"],
        "commonFinalizationResultPath": str(finalization_path),
        "commonFinalizationReceiptSha256": common["receiptSha256"],
    }
    _atomic_json(legacy_output, legacy, root=repo_root)
    return {
        "schemaVersion": "NEWS_GRASP_COORDINATED_PUBLISH_RESULT_V1",
        "legacyManifestPath": str(legacy_output),
        "publicManifestPath": produced["manifestPath"],
        "publishCommit": publish_commit,
        "manifestLeaseStatus": produced["status"],
        "commonFinalization": common,
        "commonFinalizationResultPath": str(finalization_path),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="News-Grasp common finalization coordinator")
    sub = parser.add_subparsers(dest="command", required=True)
    coordinate = sub.add_parser("coordinate-publish")
    coordinate.add_argument("--repo-root", type=Path, required=True)
    coordinate.add_argument("--ops-repo-root", type=Path, required=True)
    coordinate.add_argument("--date", required=True)
    coordinate.add_argument("--run-id", required=True)
    coordinate.add_argument("--run-intent", choices=("ScheduledProduction", "ScheduledRecoveryFull"), required=True)
    coordinate.add_argument("--transaction-started-at", required=True)
    coordinate.add_argument("--actual-recovery-operation-count", type=int, required=True)
    coordinate.add_argument("--remote", default="origin")
    coordinate.add_argument("--branch", default="main")
    coordinate.add_argument("--public-base-url", required=True)
    coordinate.add_argument("--wait-sec", type=int, default=0)
    coordinate.add_argument("--poll-sec", type=int, default=30)
    coordinate.add_argument("--notification-state", type=Path, required=True)
    coordinate.add_argument("--producer-state", type=Path, required=True)
    load_result = sub.add_parser("load-result")
    load_result.add_argument("--repo-root", type=Path, required=True)
    load_result.add_argument("--date", required=True)
    load_result.add_argument("--generation-id", required=True)
    load_result.add_argument("--publish-commit", required=True)
    smoke = sub.add_parser("smoke-loaded")
    smoke.add_argument("--repo-root", type=Path, required=True)
    smoke.add_argument("--installed-asset-root", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "smoke-loaded":
            result = no_side_effect_loaded_smoke(
                args.repo_root, installed_asset_root=args.installed_asset_root
            )
        elif args.command == "load-result":
            result = load_common_finalization(
                args.repo_root,
                issue_date=args.date,
                generation_id=args.generation_id,
                publish_commit=args.publish_commit,
            )
        else:
            result = coordinate_publish(args)
    except Exception as exc:
        print(json.dumps({"ok": False, "reason": str(exc)}, ensure_ascii=False))
        return 2
    # smoke-loaded は日本語pathをASCII escapeし、Windows redirected stdoutの
    # locale差(CP932/UTF-8)から独立した機械可読receiptにする。
    print(
        json.dumps(
            result,
            ensure_ascii=args.command == "smoke-loaded",
            sort_keys=True,
        )
    )
    if args.command == "coordinate-publish":
        return int(result["commonFinalization"]["exitCode"])
    if args.command == "load-result":
        return int(result["exitCode"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
