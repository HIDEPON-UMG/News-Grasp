"""Artifact checkpointとdaily-lineage causal retry。"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


class NewsGraspCheckpointError(RuntimeError):
    """checkpoint/retry ledger違反。"""


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def derive_daily_operation_lineage(*, issue_date: str, scheduled_authority_id: str) -> str:
    """当日最初のscheduled authorityから一度だけlineageを導出する。"""
    if not issue_date or not scheduled_authority_id:
        raise NewsGraspCheckpointError("NG_DAILY_LINEAGE_INVALID")
    return hashlib.sha256(
        _canonical(
            {
                "schemaVersion": "DAILY_OPERATION_LINEAGE_V1",
                "issueDate": issue_date,
                "scheduledAuthorityId": scheduled_authority_id,
            }
        )
    ).hexdigest()


@dataclass(frozen=True)
class ArtifactCheckpointV1:
    issueDate: str
    dailyOperationLineageId: str
    stage: str
    artifactKey: str
    inputHashes: Mapping[str, str]
    outputHash: str
    schema: str
    oracleId: str
    producerRouteId: str
    nextDeterministicStep: str
    causeFingerprint: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": "ARTIFACT_CHECKPOINT_V1",
            "issueDate": self.issueDate,
            "dailyOperationLineageId": self.dailyOperationLineageId,
            "stage": self.stage,
            "artifactKey": self.artifactKey,
            "inputHashes": dict(self.inputHashes),
            "outputHash": self.outputHash,
            "schema": self.schema,
            "oracleId": self.oracleId,
            "producerRouteId": self.producerRouteId,
            "nextDeterministicStep": self.nextDeterministicStep,
            "causeFingerprint": self.causeFingerprint,
        }


def _validate_retry_key(key: str) -> tuple[str, str, str, str, str]:
    parts = tuple(str(key).split("|"))
    if len(parts) != 5 or any(not part or "|" in part for part in parts):
        raise NewsGraspCheckpointError("NG_RETRY_KEY_INVALID")
    return parts  # type: ignore[return-value]


def cause_fingerprint(
    *,
    issue_date: str,
    daily_operation_lineage_id: str,
    artifact_key: str,
    stage_id: str,
    producer_route_id: str,
    failure_class: str,
    reason_code: str,
    cause_input_mask: list[str],
    input_hashes: Mapping[str, str],
) -> str:
    selected = {key: input_hashes[key] for key in sorted(cause_input_mask) if key in input_hashes}
    body = {
        "issueDate": issue_date,
        "dailyOperationLineageId": daily_operation_lineage_id,
        "artifactKey": artifact_key,
        "stageId": stage_id,
        "producerRouteId": producer_route_id,
        "failureClass": failure_class,
        "reasonCode": reason_code,
        "causeInputMask": sorted(cause_input_mask),
        "selectedInputHashes": selected,
    }
    return hashlib.sha256(_canonical(body)).hexdigest()


def create_checkpoint(
    *,
    issue_date: str,
    daily_operation_lineage_id: str,
    stage: str,
    artifact_key: str,
    input_hashes: Mapping[str, str],
    output_hash: str,
    schema: str,
    oracle_id: str,
    producer_route_id: str,
    next_deterministic_step: str,
    cause_fingerprint_value: str,
    output_path: Path | str,
) -> dict[str, Any]:
    if not daily_operation_lineage_id or not issue_date or not artifact_key:
        raise NewsGraspCheckpointError("NG_CHECKPOINT_ID_INVALID")
    value: dict[str, Any] = {
        "schemaVersion": "ARTIFACT_CHECKPOINT_V1",
        "issueDate": issue_date,
        "dailyOperationLineageId": daily_operation_lineage_id,
        "stage": stage,
        "artifactKey": artifact_key,
        "inputHashes": dict(input_hashes),
        "outputHash": output_hash,
        "schema": schema,
        "oracleId": oracle_id,
        "producerRouteId": producer_route_id,
        "nextDeterministicStep": next_deterministic_step,
        "causeFingerprint": cause_fingerprint_value,
    }
    value["checkpointSha256"] = hashlib.sha256(_canonical(value)).hexdigest()
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    return value


def validate_checkpoint(checkpoint: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "schemaVersion", "issueDate", "dailyOperationLineageId", "stage", "artifactKey", "inputHashes",
        "outputHash", "schema", "oracleId", "producerRouteId", "nextDeterministicStep", "causeFingerprint", "checkpointSha256",
    }
    if not required.issubset(checkpoint) or checkpoint.get("schemaVersion") != "ARTIFACT_CHECKPOINT_V1":
        raise NewsGraspCheckpointError("NG_CHECKPOINT_INVALID")
    body = dict(checkpoint)
    expected = body.pop("checkpointSha256")
    if expected != hashlib.sha256(_canonical(body)).hexdigest():
        raise NewsGraspCheckpointError("NG_CHECKPOINT_INVALID")
    return {"status": "valid", "checkpointSha256": expected, "nextDeterministicStep": checkpoint["nextDeterministicStep"]}


def resume_stage(*, checkpoint: Mapping[str, Any] | None, wrapper_result: Mapping[str, Any]) -> dict[str, Any]:
    if checkpoint is None:
        return {"status": "producer_required", "modelCalls": 1, "nextStep": "stage_start"}
    validation = validate_checkpoint(checkpoint)
    if wrapper_result.get("checkpointAlreadyMaterialized") is True and wrapper_result.get("exitCode") in (126, "timeout", "hang"):
        return {"status": "continue_deterministic", "modelCalls": 0, "nextStep": validation["nextDeterministicStep"]}
    return {"status": "producer_required", "modelCalls": 1, "nextStep": checkpoint["stage"]}


class RetryLedger:
    """同一fingerprintは再試行せず、因果hash変更だけを一回許可する。"""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("{}\n", encoding="utf-8")

    def _load(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise NewsGraspCheckpointError("NG_RETRY_LEDGER_INVALID") from exc
        return value if isinstance(value, dict) else {}

    def _atomic_write(self, value: Mapping[str, Any]) -> None:
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
                mode="w",
                encoding="utf-8",
                newline="\n",
            ) as stream:
                temporary = Path(stream.name)
                json.dump(value, stream, ensure_ascii=False, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _lock(self) -> Path:
        lock = self.path.with_name(f".{self.path.name}.lock")
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            try:
                age = time.time() - lock.stat().st_mtime
            except OSError:
                age = 0
            if age > 300:
                lock.unlink(missing_ok=True)
                return self._lock()
            raise NewsGraspCheckpointError("NG_RETRY_LEDGER_BUSY") from exc
        try:
            os.write(descriptor, f"{os.getpid()}|{time.time()}".encode("ascii"))
        finally:
            os.close(descriptor)
        return lock

    def admit_retry(self, *, key: str, fingerprint: str, cause_hash: str) -> dict[str, Any]:
        _validate_retry_key(key)
        lock = self._lock()
        try:
            ledger = self._load()
            previous = ledger.get(key)
            if previous is None:
                ledger[key] = {"fingerprint": fingerprint, "causeHash": cause_hash, "attempt": 0}
                self._atomic_write(ledger)
                return {"retry": 0, "reason": "first_observation"}
            if previous.get("fingerprint") == fingerprint:
                return {"retry": 0, "reason": "same_cause_fingerprint"}
            if previous.get("causeHash") == cause_hash or previous.get("attempt") >= 1:
                return {"retry": 0, "reason": "cause_not_changed_or_budget_consumed"}
            ledger[key] = {"fingerprint": fingerprint, "causeHash": cause_hash, "attempt": 1}
            self._atomic_write(ledger)
            return {"retry": 1, "reason": "causal_input_changed"}
        finally:
            lock.unlink(missing_ok=True)


def retry_ledger(path: Path | str) -> RetryLedger:
    """登録済みretry ledger consumerのfactory。"""
    return RetryLedger(path)
