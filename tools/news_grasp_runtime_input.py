"""product generationにboundしたimmutable runtime input snapshot。"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping


SCHEMA = "RUNTIME_INPUT_SNAPSHOT_V1"
WAL_SCHEMA = "RUNTIME_INPUT_WAL_V1"


class RuntimeInputError(ValueError):
    """runtime inputのgeneration、sequence、WAL違反。"""


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeInputError("RUNTIME_INPUT_MANIFEST_INVALID") from error
    if not isinstance(value, dict):
        raise RuntimeInputError("RUNTIME_INPUT_MANIFEST_INVALID")
    return value


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(_canonical(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class RuntimeInputStore:
    """issueDate/inputKindごとのproduct-local single-writer store。"""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _dir(self, input_kind: str, issue_date: str) -> Path:
        if not input_kind or "/" in input_kind or "\\" in input_kind or ".." in input_kind:
            raise RuntimeInputError("RUNTIME_INPUT_KIND_INVALID")
        return self.root / issue_date / input_kind

    def _pointer(self, input_kind: str, issue_date: str) -> Path:
        return self._dir(input_kind, issue_date) / "current.json"

    def _load_current(self, input_kind: str, issue_date: str) -> dict[str, Any] | None:
        pointer = self._pointer(input_kind, issue_date)
        if not pointer.exists():
            return None
        value = _read_json(pointer)
        if value.get("schemaVersion") != SCHEMA:
            raise RuntimeInputError("RUNTIME_INPUT_POINTER_INVALID")
        body = dict(value)
        receipt = body.pop("manifestSha256", None)
        if receipt != _sha(body):
            raise RuntimeInputError("RUNTIME_INPUT_MANIFEST_INVALID")
        return value

    def commit(self, **kwargs: Any) -> dict[str, Any]:
        """single-writer lock下で一度だけcommitする。"""
        input_kind = str(kwargs.get("input_kind") or "")
        issue_date = str(kwargs.get("issue_date") or "")
        lock_path = self._dir(input_kind, issue_date) / ".writer.lock"
        directory = lock_path.parent
        directory.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as error:
            raise RuntimeInputError("RUNTIME_INPUT_WRITER_BUSY") from error
        try:
            os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
            os.close(descriptor)
            return self._commit_unlocked(**kwargs)
        finally:
            lock_path.unlink(missing_ok=True)

    def _commit_unlocked(
        self,
        *,
        input_kind: str,
        issue_date: str,
        product_generation_id: str,
        producer_id: str,
        producer_operation_id: str,
        payload: Mapping[str, Any],
        schema_id: str,
        oracle_id: str,
        sequence: int | None = None,
        source_status: str = "api",
    ) -> dict[str, Any]:
        if not product_generation_id or not producer_id or not producer_operation_id:
            raise RuntimeInputError("RUNTIME_INPUT_ID_INVALID")
        if source_status not in {"api", "last_verified", "static_typed_fallback"}:
            raise RuntimeInputError("RUNTIME_INPUT_SOURCE_STATUS_INVALID")
        directory = self._dir(input_kind, issue_date)
        current = self._load_current(input_kind, issue_date)
        if current and current.get("producerOperationId") == producer_operation_id:
            if current.get("productGenerationId") != product_generation_id or current.get("payloadSha256") != _sha(payload):
                raise RuntimeInputError("RUNTIME_INPUT_OPERATION_REPLAY_TAMPERED")
            return current
        previous_sequence = int(current.get("sequence", 0)) if current else 0
        expected_sequence = previous_sequence + 1
        actual_sequence = expected_sequence if sequence is None else int(sequence)
        if actual_sequence != expected_sequence:
            raise RuntimeInputError("RUNTIME_INPUT_SEQUENCE_INVALID")
        if current and current.get("productGenerationId") != product_generation_id:
            raise RuntimeInputError("RUNTIME_INPUT_FOREIGN_GENERATION")
        previous_manifest_sha = str(current.get("manifestSha256") or "") if current else ""
        operation_id = f"{issue_date}|{input_kind}|{producer_operation_id}"
        payload_sha = _sha(payload)
        body: dict[str, Any] = {
            "schemaVersion": SCHEMA,
            "productGenerationId": product_generation_id,
            "inputKind": input_kind,
            "issueDate": issue_date,
            "snapshotId": _sha({"operation": operation_id, "payload": payload_sha}),
            "producerId": producer_id,
            "producerOperationId": producer_operation_id,
            "sequence": actual_sequence,
            "pointerGeneration": actual_sequence,
            "payloadSha256": payload_sha,
            "schemaSha256": _sha(schema_id),
            "oracleId": oracle_id,
            "sourceStatus": source_status,
            "previousSnapshotId": current.get("snapshotId") if current else None,
            "previousManifestSha256": previous_manifest_sha,
        }
        payload_path = directory / f"{body['snapshotId']}.payload.json"
        body["payloadPath"] = str(payload_path)
        body["manifestSha256"] = _sha(body)
        tx = directory / f".tx-{producer_operation_id}"
        tx.mkdir(parents=True, exist_ok=False)
        wal = {"schemaVersion": WAL_SCHEMA, "operationId": operation_id, "phase": "prepared", "manifestSha256": body["manifestSha256"]}
        committed = False
        try:
            _atomic_json(tx / "wal.json", wal)
            _atomic_json(tx / "payload.json", dict(payload))
            wal["phase"] = "payload_committed"
            _atomic_json(tx / "wal.json", wal)
            _atomic_json(payload_path, dict(payload))
            _atomic_json(tx / "manifest.json", body)
            wal["phase"] = "manifest_committed"
            _atomic_json(tx / "wal.json", wal)
            _atomic_json(self._pointer(input_kind, issue_date), body)
            wal["phase"] = "pointer_committed"
            _atomic_json(tx / "wal.json", wal)
            committed = True
            return body
        finally:
            if committed and tx.exists():
                for child in tx.iterdir():
                    child.unlink(missing_ok=True)
                tx.rmdir()

    def read_current(
        self, *, input_kind: str, issue_date: str, product_generation_id: str
    ) -> dict[str, Any]:
        value = self._load_current(input_kind, issue_date)
        if value is None:
            raise RuntimeInputError("RUNTIME_INPUT_POINTER_MISSING")
        if value.get("productGenerationId") != product_generation_id:
            raise RuntimeInputError("RUNTIME_INPUT_FOREIGN_GENERATION")
        return value

    def read_payload(self, manifest: Mapping[str, Any]) -> dict[str, Any]:
        path = Path(str(manifest.get("payloadPath") or ""))
        if not path.is_file() or path.is_symlink():
            raise RuntimeInputError("RUNTIME_INPUT_PAYLOAD_MISSING")
        payload = _read_json(path)
        if _sha(payload) != str(manifest.get("payloadSha256") or ""):
            raise RuntimeInputError("RUNTIME_INPUT_PAYLOAD_INVALID")
        return payload

    def recover(self, *, input_kind: str, issue_date: str, product_generation_id: str) -> dict[str, Any]:
        """orphan transactionは可視pointerへ昇格せずquarantineへbounded移動する。"""
        directory = self._dir(input_kind, issue_date)
        quarantine = self.root / "quarantine" / issue_date / input_kind
        moved = 0
        for tx in sorted(directory.glob(".tx-*")):
            if not tx.is_dir():
                continue
            manifest = tx / "manifest.json"
            if manifest.is_file():
                value = _read_json(manifest)
                if value.get("productGenerationId") != product_generation_id:
                    raise RuntimeInputError("RUNTIME_INPUT_FOREIGN_GENERATION")
                current = self._load_current(input_kind, issue_date)
                if current is None or int(value.get("sequence", 0)) > int(current.get("sequence", 0)):
                    _atomic_json(self._pointer(input_kind, issue_date), value)
                    for child in tx.iterdir():
                        child.unlink(missing_ok=True)
                    tx.rmdir()
                    continue
            quarantine.mkdir(parents=True, exist_ok=True)
            target = quarantine / tx.name
            tx.replace(target)
            moved += 1
        return {"status": "recovered", "quarantinedTransactions": moved}
