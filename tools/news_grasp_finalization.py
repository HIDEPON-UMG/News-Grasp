"""News-Grasp の候補finalizationとWALを扱う副作用境界。

このモジュールは、publish_completeをlive stateへ適用する前に候補を検証し、
guard結果とreceipt消費を同じWALへ束縛する。SLO/readinessの結果はcommit後の
sidecarであり、public stateのcommit判定へ混ぜない。
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


WAL_SCHEMA = "ATOMIC_FINALIZATION_WAL_V1"
OUTCOME_SCHEMA = "COMPLETION_OUTCOME_ENVELOPE_V2"
_MAX_JSON_BYTES = 1024 * 1024


class FinalizationError(ValueError):
    """WALまたは候補のidentityが検証できない。"""


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path: Path) -> str:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise FinalizationError("FINALIZATION_FILE_UNREADABLE") from error
    return sha256_bytes(raw)


def sha256_value(value: dict[str, Any]) -> str:
    """JSON値identity。receipt/manifestのfile hashとは意図的に分離する。"""

    return sha256_bytes(canonical_bytes(value))


def read_json_snapshot(path: Path, *, root: Path | None = None) -> tuple[dict[str, Any], str]:
    candidate = Path(path)
    if root is not None:
        try:
            candidate.resolve(strict=True).relative_to(Path(root).resolve(strict=True))
        except (OSError, ValueError) as error:
            raise FinalizationError("FINALIZATION_PATH_OUTSIDE_ROOT") from error
    try:
        raw = candidate.read_bytes()
        value = json.loads(raw.decode("utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise FinalizationError("FINALIZATION_JSON_INVALID") from error
    if len(raw) > _MAX_JSON_BYTES or not isinstance(value, dict):
        raise FinalizationError("FINALIZATION_JSON_INVALID")
    return value, sha256_bytes(raw)


def write_atomic_json(path: Path, value: dict[str, Any]) -> str:
    """同一ディレクトリ内でfsync後に置換し、書いたbytesのhashを返す。"""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except OSError as error:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise FinalizationError("FINALIZATION_ATOMIC_WRITE_FAILED") from error
    return sha256_bytes(raw)


def build_candidate_state(
    before_state: dict[str, Any],
    *,
    issue_date: str,
    manifest_path: Path,
    publish_commit: str,
    finalization_receipt_path: Path,
    finalization_receipt_sha256: str,
    scheduled_failure_receipt_path: Path | None = None,
    scheduled_failure_receipt_sha256: str = "",
    done_at: str | None = None,
) -> dict[str, Any]:
    """live stateを変更せず、publish_complete候補を構築する。"""

    if not isinstance(before_state, dict) or before_state.get("date") != issue_date:
        raise FinalizationError("FINALIZATION_STATE_IDENTITY_INVALID")
    if not publish_commit:
        raise FinalizationError("FINALIZATION_PUBLISH_COMMIT_MISSING")
    candidate = dict(before_state)
    candidate.update(
        {
            "status": "publish_complete",
            "message": "verified recovery publish complete",
            "exit_code": 0,
            "updated_at": done_at or datetime.now(timezone.utc).isoformat(),
            "publish_manifest_path": str(Path(manifest_path).resolve()),
            "publish_commit": publish_commit,
            "scheduled_attempt_status": "failed_then_recovered",
            "recovery_attempt_status": "succeeded",
            "recovery_finalization_receipt_path": str(
                Path(finalization_receipt_path).resolve()
            ),
            "recovery_finalization_receipt_sha256": finalization_receipt_sha256,
            "finalization_candidate": True,
        }
    )
    if scheduled_failure_receipt_path is not None:
        candidate["scheduled_failure_receipt_path"] = str(
            Path(scheduled_failure_receipt_path).resolve()
        )
    if scheduled_failure_receipt_sha256:
        candidate["scheduled_failure_receipt_sha256"] = scheduled_failure_receipt_sha256
    return candidate


def _wal_hash(value: dict[str, Any]) -> str:
    body = {key: item for key, item in value.items() if key != "walSha256"}
    return sha256_bytes(canonical_bytes(body))


def _seal_wal(value: dict[str, Any]) -> dict[str, Any]:
    sealed = dict(value)
    sealed["walSha256"] = _wal_hash(sealed)
    return sealed


def _validate_wal(value: dict[str, Any]) -> dict[str, Any]:
    if value.get("schemaVersion") != WAL_SCHEMA:
        raise FinalizationError("FINALIZATION_WAL_SCHEMA_INVALID")
    expected = str(value.get("walSha256") or "")
    if expected != _wal_hash(value):
        raise FinalizationError("FINALIZATION_WAL_HASH_INVALID")
    if value.get("phase") not in {
        "prepared",
        "guard_passed",
        "state_committed",
        "committed",
        "closed",
    }:
        raise FinalizationError("FINALIZATION_WAL_PHASE_INVALID")
    reservation = value.get("receiptReservation")
    journal = value.get("receiptConsumptionJournal")
    if not isinstance(reservation, dict) or not isinstance(journal, dict):
        raise FinalizationError("FINALIZATION_RECEIPT_JOURNAL_INVALID")
    if (
        journal.get("finalizationReceiptSha256")
        != reservation.get("finalizationReceiptSha256")
        or journal.get("executionReceiptSha256")
        != reservation.get("executionReceiptSha256")
    ):
        raise FinalizationError("FINALIZATION_RECEIPT_JOURNAL_INVALID")
    return value


def prepare_wal(
    *,
    wal_path: Path,
    candidate_path: Path,
    state_path: Path,
    before_state: dict[str, Any],
    candidate_state: dict[str, Any],
    manifest_sha256: str,
    finalization_receipt_sha256: str,
    execution_receipt_sha256: str = "",
    receipt_consumption_journal: dict[str, Any] | None = None,
    issue_date: str,
) -> dict[str, Any]:
    """候補stateとWALを作成する。live stateはこの関数では書き換えない。"""

    target = Path(state_path).resolve()
    candidate = Path(candidate_path).resolve()
    wal = Path(wal_path).resolve()
    if target.parent != candidate.parent or wal.parent != target.parent:
        raise FinalizationError("FINALIZATION_PATH_IDENTITY_INVALID")
    before_raw = canonical_bytes(before_state)
    candidate_raw = canonical_bytes(candidate_state)
    write_atomic_json(candidate, candidate_state)
    body = {
        "schemaVersion": WAL_SCHEMA,
        "issueDate": issue_date,
        "statePath": str(target),
        "candidatePath": str(candidate),
        "walPath": str(wal),
        "beforeStateSha256": sha256_bytes(before_raw),
        "candidateStateSha256": sha256_bytes(candidate_raw),
        "afterStateSha256": "",
        "manifestSha256": manifest_sha256,
        "receiptReservation": {
            "finalizationReceiptSha256": finalization_receipt_sha256,
            "executionReceiptSha256": execution_receipt_sha256,
        },
        "receiptConsumptionJournal": dict(receipt_consumption_journal or {
            "status": "consumed_pending_state",
            "finalizationReceiptSha256": finalization_receipt_sha256,
            "executionReceiptSha256": execution_receipt_sha256,
        }),
        "guardDecision": None,
        "phase": "prepared",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "forwardRecovery": "candidate-only-forward-resume",
    }
    sealed = _seal_wal(body)
    write_atomic_json(wal, sealed)
    return sealed


def load_wal(wal_path: Path) -> dict[str, Any]:
    value, _ = read_json_snapshot(Path(wal_path))
    return _validate_wal(value)


def record_guard_decision(
    *, wal_path: Path, decision: dict[str, Any], candidate_path: Path
) -> dict[str, Any]:
    wal = load_wal(wal_path)
    if wal["phase"] != "prepared":
        if wal["phase"] in {"guard_passed", "state_committed", "committed", "closed"}:
            return wal
        raise FinalizationError("FINALIZATION_WAL_PHASE_INVALID")
    candidate_value, _ = read_json_snapshot(Path(candidate_path))
    candidate_sha = sha256_value(candidate_value)
    if candidate_sha != wal.get("candidateStateSha256"):
        raise FinalizationError("FINALIZATION_CANDIDATE_DRIFT")
    if decision.get("ok") is not True:
        raise FinalizationError("FINALIZATION_GUARD_RED")
    updated = dict(wal)
    updated["guardDecision"] = dict(decision)
    updated["phase"] = "guard_passed"
    updated["guardDecisionSha256"] = sha256_bytes(canonical_bytes(decision))
    updated = _seal_wal(updated)
    write_atomic_json(Path(wal_path), updated)
    return updated


def commit_candidate(*, wal_path: Path) -> dict[str, Any]:
    """guard済みcandidateだけをstateへ置換する。rollbackは行わない。"""

    wal = load_wal(wal_path)
    state_path = Path(str(wal["statePath"]))
    candidate_path = Path(str(wal["candidatePath"]))
    if wal["phase"] in {"committed", "closed"}:
        current_value, _ = read_json_snapshot(state_path)
        current_sha = sha256_value(current_value)
        if current_sha != wal.get("afterStateSha256"):
            raise FinalizationError("FINALIZATION_DIVERGENT_STATE")
        return wal
    if wal["phase"] != "guard_passed" or not isinstance(wal.get("guardDecision"), dict):
        raise FinalizationError("FINALIZATION_GUARD_REQUIRED")
    if state_path.exists() and not candidate_path.exists():
        current_value, _ = read_json_snapshot(state_path)
        current_sha = sha256_value(current_value)
        if current_sha == wal.get("candidateStateSha256"):
            updated = dict(wal)
            updated["afterStateSha256"] = current_sha
            updated["phase"] = "committed"
            updated["committedAt"] = datetime.now(timezone.utc).isoformat()
            updated = _seal_wal(updated)
            write_atomic_json(Path(wal_path), updated)
            return updated
        raise FinalizationError("FINALIZATION_DIVERGENT_STATE")
    candidate_value, _ = read_json_snapshot(candidate_path)
    candidate_sha = sha256_value(candidate_value)
    if candidate_sha != wal.get("candidateStateSha256"):
        raise FinalizationError("FINALIZATION_CANDIDATE_DRIFT")
    if state_path.exists():
        current_value, _ = read_json_snapshot(state_path)
        current_sha = sha256_value(current_value)
        if current_sha not in {wal.get("beforeStateSha256"), wal.get("candidateStateSha256")}:  # accept only known state hashes
            raise FinalizationError("FINALIZATION_DIVERGENT_STATE")
    try:
        os.replace(candidate_path, state_path)
    except OSError as error:
        raise FinalizationError("FINALIZATION_STATE_COMMIT_FAILED") from error
    after_value, _ = read_json_snapshot(state_path)
    after_sha = sha256_value(after_value)
    if after_sha != wal.get("candidateStateSha256"):
        raise FinalizationError("FINALIZATION_STATE_COMMIT_HASH_INVALID")
    updated = dict(wal)
    updated["afterStateSha256"] = after_sha
    updated["phase"] = "committed"
    updated["committedAt"] = datetime.now(timezone.utc).isoformat()
    updated = _seal_wal(updated)
    write_atomic_json(Path(wal_path), updated)
    return updated


def close_wal(*, wal_path: Path) -> dict[str, Any]:
    wal = load_wal(wal_path)
    if wal["phase"] == "closed":
        return wal
    if wal["phase"] != "committed":
        raise FinalizationError("FINALIZATION_COMMIT_REQUIRED")
    current_value, _ = read_json_snapshot(Path(str(wal["statePath"])))
    current_sha = sha256_value(current_value)
    if current_sha != wal.get("afterStateSha256"):
        raise FinalizationError("FINALIZATION_DIVERGENT_STATE")
    updated = dict(wal)
    updated["phase"] = "closed"
    updated["closedAt"] = datetime.now(timezone.utc).isoformat()
    updated = _seal_wal(updated)
    write_atomic_json(Path(wal_path), updated)
    return updated


def write_outcome_sidecar(*, path: Path, outcome: dict[str, Any], wal_path: Path) -> dict[str, Any]:
    """commit後のみSLO/readiness outcomeを書き、public stateを触らない。"""

    wal = load_wal(wal_path)
    if wal["phase"] not in {"committed", "closed"}:
        raise FinalizationError("FINALIZATION_COMMIT_REQUIRED")
    value = dict(outcome)
    value.setdefault("schemaVersion", OUTCOME_SCHEMA)
    value.setdefault("publicAuthorityPreserved", True)
    value["finalizationWalSha256"] = wal["walSha256"]
    value["receiptSha256"] = sha256_bytes(canonical_bytes(value))
    write_atomic_json(Path(path), value)
    return value


def recover_wal(*, wal_path: Path) -> dict[str, Any]:
    """同一WAL/candidateでforward resumeし、別WALやrollbackは作らない。"""

    wal = load_wal(wal_path)
    if wal["phase"] == "prepared":
        raise FinalizationError("FINALIZATION_GUARD_REQUIRED")
    if wal["phase"] == "guard_passed":
        return commit_candidate(wal_path=wal_path)
    if wal["phase"] == "state_committed":
        return commit_candidate(wal_path=wal_path)
    if wal["phase"] == "committed":
        return close_wal(wal_path=wal_path)
    return wal
