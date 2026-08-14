"""News-Grasp recovery の bounded repair / finalizer authority receipt。

Global HighCost authority は複製しない。既に検証済みの recovery authority と、
News-Grasp が所有する root・artifact・clock を改ざん検知可能な一回限りの入力へ束縛する。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import uuid

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    # Recovery entrypoints run with ``python -I <absolute-script>``.  Isolated
    # mode intentionally ignores ambient PYTHONPATH, so bind imports to the
    # verified ops repository that owns this script.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import news_grasp_verified_storage as verified_storage


MAX_JSON_BYTES = 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REPAIR_SCHEMA = "NEWS_GRASP_CONTROL_PLANE_REPAIR_RECEIPT_V1"
EXECUTION_SCHEMA = "NEWS_GRASP_RECOVERY_EXECUTION_RECEIPT_V1"
EXECUTION_SCHEMA_V2 = "NEWS_GRASP_RECOVERY_EXECUTION_RECEIPT_V2"
FINALIZATION_SCHEMA_V1 = "NEWS_GRASP_RECOVERY_FINALIZATION_RECEIPT_V1"
FINALIZATION_SCHEMA = "NEWS_GRASP_RECOVERY_FINALIZATION_RECEIPT_V2"
ALLOWED_REPAIR_REASONS = {"PRODUCTION_RUNTIME_DRIFT", "LIVE_BIN_DRIFT"}
FUTURE_TOLERANCE = timedelta(minutes=5)
RECEIPT_MAX_AGE = timedelta(hours=2)
CANONICAL_BROKER_PATH = Path.home() / "bin" / "ai-model-spawn-broker.py"
CONSUMPTION_LEDGER_NAME = "news-grasp-recovery-consumption-v1.sqlite3"
CONSUMPTION_TABLE = "receipt_consumptions_v2"


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_head(root: Path) -> str:
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            creationflags=creationflags,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    head = completed.stdout.strip().lower()
    return head if completed.returncode == 0 and GIT_SHA_RE.fullmatch(head) else ""


def _parse_clock(value: object, *, code: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(code) from error
    if parsed.tzinfo is None:
        raise ValueError(code)
    return parsed


def _resolved(path: Path) -> Path:
    return Path(path).resolve(strict=True)


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(_resolved(left))) == os.path.normcase(str(_resolved(right)))


def _same_lexical_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(Path(left).resolve(strict=False))) == os.path.normcase(
        str(Path(right).resolve(strict=False))
    )


def _contained_regular_file(path: Path, *, root: Path, code: str) -> Path:
    lexical_candidate = Path(os.path.abspath(path))
    lexical_boundary = Path(os.path.abspath(root))
    if lexical_boundary not in lexical_candidate.parents:
        raise ValueError(code)
    cursor = lexical_candidate
    while True:
        if cursor.is_symlink():
            raise ValueError(code)
        try:
            if bool(cursor.stat().st_file_attributes & 0x400):
                raise ValueError(code)
        except AttributeError:
            pass
        if os.path.normcase(str(cursor)) == os.path.normcase(str(lexical_boundary)):
            break
        parent = cursor.parent
        if parent == cursor:
            raise ValueError(code)
        cursor = parent
    candidate = _resolved(path)
    boundary = _resolved(root)
    if boundary not in candidate.parents or not candidate.is_file() or candidate.is_symlink():
        raise ValueError(code)
    if candidate.stat().st_size > MAX_JSON_BYTES:
        raise ValueError(code)
    return candidate


def _read_json(path: Path, *, root: Path, code: str) -> dict[str, Any]:
    candidate = _contained_regular_file(path, root=root, code=code)
    raw = candidate.read_bytes()
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError(code)
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(code) from error
    if not isinstance(value, dict):
        raise ValueError(code)
    return value


def _seal(body: dict[str, Any]) -> dict[str, Any]:
    value = dict(body)
    value["receiptSha256"] = hashlib.sha256(canonical_bytes(value)).hexdigest()
    return value


def _validate_seal(value: dict[str, Any], *, schema: str, code: str) -> dict[str, Any]:
    body = dict(value)
    receipt_sha = str(body.pop("receiptSha256", ""))
    if body.get("schemaVersion") != schema or not SHA256_RE.fullmatch(receipt_sha):
        raise ValueError(code)
    if hashlib.sha256(canonical_bytes(body)).hexdigest() != receipt_sha:
        raise ValueError(code)
    return value


def _validate_execution_seal(value: dict[str, Any], *, code: str) -> dict[str, Any]:
    schema = str(value.get("schemaVersion") or "")
    if schema == EXECUTION_SCHEMA_V2:
        return _validate_seal(value, schema=EXECUTION_SCHEMA_V2, code=code)
    return _validate_seal(value, schema=EXECUTION_SCHEMA, code=code)


def _validate_embedded_receipt(
    value: dict[str, Any], *, issue_date: str, allowed_schemas: set[str], code: str
) -> None:
    body = dict(value)
    receipt_sha = str(body.pop("receiptSha256", ""))
    if (
        body.get("schemaVersion") not in allowed_schemas
        or body.get("issueDate") != issue_date
        or not SHA256_RE.fullmatch(receipt_sha)
        or hashlib.sha256(canonical_bytes(body)).hexdigest() != receipt_sha
    ):
        raise ValueError(code)


def _validate_authority_via_broker(
    *,
    issue_date: str,
    authority_path: Path,
    authority: dict[str, Any],
    failure_receipt_sha256: str,
) -> dict[str, Any]:
    """Global production ledgerをauthorityの正本として再検証する。"""
    if not CANONICAL_BROKER_PATH.is_file() or CANONICAL_BROKER_PATH.is_symlink():
        raise ValueError("RECOVERY_AUTHORITY_BROKER_UNAVAILABLE")
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    completed = subprocess.run(
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
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        creationflags=creationflags,
    )
    if completed.returncode != 0:
        raise ValueError("RECOVERY_AUTHORITY_LEDGER_INVALID")
    try:
        witness = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ValueError("RECOVERY_AUTHORITY_LEDGER_INVALID") from error
    witness = _validate_seal(
        witness,
        schema="SCHEDULED_RECOVERY_AUTHORITY_LEDGER_WITNESS_V1",
        code="RECOVERY_AUTHORITY_LEDGER_INVALID",
    )
    if (
        witness.get("issueDate") != issue_date
        or witness.get("failureReceiptSha256") != failure_receipt_sha256
        or witness.get("authorityReceiptSha256") != authority.get("receiptSha256")
        or not isinstance(witness.get("ledgerEventSequence"), int)
        or int(witness["ledgerEventSequence"]) <= 0
        or not SHA256_RE.fullmatch(str(witness.get("ledgerEventHash") or ""))
    ):
        raise ValueError("RECOVERY_AUTHORITY_LEDGER_INVALID")
    return witness


def _broker_binding(witness: dict[str, Any]) -> dict[str, Any]:
    return {
        "authorityLedgerWitnessSha256": witness["receiptSha256"],
        "authorityLedgerEventSequence": witness["ledgerEventSequence"],
        "authorityLedgerEventHash": witness["ledgerEventHash"],
    }


def _validated_broker_witness(
    witness: dict[str, Any], *, issue_date: str, authority_sha: str, failure_sha: str
) -> dict[str, Any]:
    value = _validate_seal(
        witness,
        schema="SCHEDULED_RECOVERY_AUTHORITY_LEDGER_WITNESS_V1",
        code="RECOVERY_AUTHORITY_LEDGER_INVALID",
    )
    if (
        value.get("issueDate") != issue_date
        or value.get("authorityReceiptSha256") != authority_sha
        or value.get("failureReceiptSha256") != failure_sha
        or not isinstance(value.get("ledgerEventSequence"), int)
        or int(value["ledgerEventSequence"]) <= 0
        or not SHA256_RE.fullmatch(str(value.get("ledgerEventHash") or ""))
    ):
        raise ValueError("RECOVERY_AUTHORITY_LEDGER_INVALID")
    return value


def _assert_broker_binding(
    receipt: dict[str, Any], witness: dict[str, Any], *, code: str
) -> None:
    if any(receipt.get(field) != value for field, value in _broker_binding(witness).items()):
        raise ValueError(code)


def _assert_fresh(issued_at: object, *, code: str) -> None:
    issued = _parse_clock(issued_at, code=code)
    now = datetime.now(timezone.utc)
    if issued > now + FUTURE_TOLERANCE or now - issued > RECEIPT_MAX_AGE:
        raise ValueError(code)


def _consumption_ledger(live_bin_root: Path) -> Path:
    live = _resolved(live_bin_root)
    ledger = live / CONSUMPTION_LEDGER_NAME
    if ledger.exists() and (ledger.is_symlink() or not ledger.is_file()):
        raise ValueError("RECOVERY_RECEIPT_CONSUMPTION_INVALID")
    return ledger


def _receipt_identity(receipt: dict[str, Any], *, kind: str) -> tuple[str, str, str]:
    receipt_sha = str(receipt.get("receiptSha256") or "")
    nonce = str(receipt.get("nonce") or "")
    issue_date = str(receipt.get("issueDate") or "")
    if (
        not SHA256_RE.fullmatch(receipt_sha)
        or not re.fullmatch(r"[0-9a-f]{32}", nonce)
        or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", issue_date)
    ):
        raise ValueError("RECOVERY_RECEIPT_CONSUMPTION_INVALID")
    return receipt_sha, nonce, issue_date


def _semantic_consumption_key(receipt: dict[str, Any], *, kind: str) -> str:
    if kind == "control_plane_repair":
        fields = (
            "issueDate",
            "recoveryAuthorityReceiptSha256",
            "driftWitnessSha256",
            "artifactRoot",
            "opsRoot",
        )
    elif kind == "execution":
        legacy_fields = (
            "issueDate",
            "recoveryAuthorityReceiptSha256",
            "scheduledFailureReceiptSha256",
            "artifactRoot",
            "opsRoot",
        )
        identity_fields = (
            # 実行identityが更新されたbounded recoveryは、同一authorityでも
            # stale receiptのsemantic keyへ衝突させず、現行runtimeへ再束縛する。
            "artifactHead",
            "opsHead",
            "runnerSha256",
        )
        fields = legacy_fields + identity_fields if all(receipt.get(field) for field in identity_fields) else legacy_fields
    elif kind == "finalization":
        fields = ("issueDate", "executionReceiptSha256")
    else:
        raise ValueError("RECOVERY_RECEIPT_CONSUMPTION_INVALID")
    values = {field: str(receipt.get(field) or "") for field in fields}
    # Old fixture receipts without semantic fields remain protected by their
    # receipt identity; production schemas always fill every field above.
    if any(not value for value in values.values()):
        values = {"receiptSha256": str(receipt.get("receiptSha256") or "")}
    return hashlib.sha256(canonical_bytes({"kind": kind, **values})).hexdigest()


def _ensure_consumption_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        f"CREATE TABLE IF NOT EXISTS {CONSUMPTION_TABLE} ("
        "receipt_sha256 TEXT PRIMARY KEY, nonce TEXT NOT NULL UNIQUE, "
        "kind TEXT NOT NULL, issue_date TEXT NOT NULL, semantic_key TEXT NOT NULL UNIQUE, "
        "parent_execution_sha256 TEXT UNIQUE, status TEXT NOT NULL, "
        "consumed_at TEXT NOT NULL, state_applied_at TEXT)"
    )


def consume_once(
    *, receipt: dict[str, Any], live_bin_root: Path, kind: str
) -> dict[str, Any]:
    """pathに依存しないcanonical SQLite ledgerでreceipt identityを一度だけ消費する。"""
    receipt_sha, nonce, issue_date = _receipt_identity(receipt, kind=kind)
    semantic_key = _semantic_consumption_key(receipt, kind=kind)
    ledger = _consumption_ledger(live_bin_root)
    connection = sqlite3.connect(str(ledger), timeout=30, isolation_level=None)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        _ensure_consumption_table(connection)
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            f"INSERT INTO {CONSUMPTION_TABLE} VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                receipt_sha,
                nonce,
                kind,
                issue_date,
                semantic_key,
                str(receipt.get("executionReceiptSha256") or "") or None,
                "consumed_pending_state" if kind == "finalization" else "consumed",
                datetime.now(timezone.utc).isoformat(),
                None,
            ),
        )
        connection.execute("COMMIT")
    except sqlite3.IntegrityError as error:
        try:
            connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise ValueError("RECOVERY_RECEIPT_ALREADY_CONSUMED") from error
    finally:
        connection.close()
    return {
        "schemaVersion": "NEWS_GRASP_RECOVERY_RECEIPT_CONSUMPTION_V1",
        "receiptSha256": receipt_sha,
        "nonce": nonce,
        "kind": kind,
        "issueDate": issue_date,
        "ledgerPath": str(ledger),
    }


def consume_or_resume(
    *, receipt: dict[str, Any], live_bin_root: Path, kind: str
) -> dict[str, Any]:
    """canonical receiptのexact retryだけをpending operationの再開として受理する。"""
    receipt_sha, nonce, issue_date = _receipt_identity(receipt, kind=kind)
    semantic_key = _semantic_consumption_key(receipt, kind=kind)
    ledger = _consumption_ledger(live_bin_root)
    connection = sqlite3.connect(str(ledger), timeout=30, isolation_level=None)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        _ensure_consumption_table(connection)
        connection.execute("BEGIN IMMEDIATE")
        semantic = connection.execute(
            f"SELECT receipt_sha256, nonce, kind, issue_date, status FROM {CONSUMPTION_TABLE} "
            "WHERE semantic_key = ?",
            (semantic_key,),
        ).fetchone()
        expected = (receipt_sha, nonce, kind, issue_date)
        if semantic is None:
            now = datetime.now(timezone.utc).isoformat()
            connection.execute(
                f"INSERT INTO {CONSUMPTION_TABLE} VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    receipt_sha,
                    nonce,
                    kind,
                    issue_date,
                    semantic_key,
                    None,
                    "consumed_pending_operation",
                    now,
                    None,
                ),
            )
            status = "consumed_pending_operation"
        elif semantic[:4] == expected and semantic[4] == "consumed_pending_operation":
            status = "consumed_pending_operation"
        else:
            raise ValueError("RECOVERY_RECEIPT_ALREADY_CONSUMED")
        connection.execute("COMMIT")
    except (sqlite3.Error, ValueError) as error:
        try:
            connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise ValueError("RECOVERY_RECEIPT_ALREADY_CONSUMED") from error
    finally:
        connection.close()
    return {
        "schemaVersion": "NEWS_GRASP_RECOVERY_RECEIPT_CONSUMPTION_V1",
        "receiptSha256": receipt_sha,
        "nonce": nonce,
        "kind": kind,
        "issueDate": issue_date,
        "status": status,
        "ledgerPath": str(ledger),
    }


def consume_finalization_chain(
    *, finalization: dict[str, Any], execution: dict[str, Any], live_bin_root: Path
) -> dict[str, Any]:
    """execution+finalizationをsemantic one-shotかつcrash再開可能に消費する。"""
    execution_sha, execution_nonce, execution_date = _receipt_identity(
        execution, kind="execution"
    )
    final_sha, final_nonce, final_date = _receipt_identity(
        finalization, kind="finalization"
    )
    if str(finalization.get("executionReceiptSha256") or "") != execution_sha:
        raise ValueError("RECOVERY_EXECUTION_CONSUMPTION_INVALID")
    execution_semantic = _semantic_consumption_key(execution, kind="execution")
    final_semantic = _semantic_consumption_key(finalization, kind="finalization")
    ledger = _consumption_ledger(live_bin_root)
    connection = sqlite3.connect(str(ledger), timeout=30, isolation_level=None)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        _ensure_consumption_table(connection)
        connection.execute("BEGIN IMMEDIATE")
        existing_execution = connection.execute(
            f"SELECT nonce, kind, issue_date, semantic_key FROM {CONSUMPTION_TABLE} "
            "WHERE receipt_sha256 = ?",
            (execution_sha,),
        ).fetchone()
        expected_execution = (
            execution_nonce,
            "execution",
            execution_date,
            execution_semantic,
        )
        now = datetime.now(timezone.utc).isoformat()
        if existing_execution is None:
            connection.execute(
                f"INSERT INTO {CONSUMPTION_TABLE} VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    execution_sha,
                    execution_nonce,
                    "execution",
                    execution_date,
                    execution_semantic,
                    None,
                    "consumed_pending_operation",
                    now,
                    None,
                ),
            )
        elif existing_execution != expected_execution:
            raise ValueError("RECOVERY_EXECUTION_CONSUMPTION_INVALID")
        existing_final = connection.execute(
            f"SELECT nonce, kind, issue_date, semantic_key, parent_execution_sha256, status "
            f"FROM {CONSUMPTION_TABLE} WHERE receipt_sha256 = ?",
            (final_sha,),
        ).fetchone()
        expected_final_prefix = (
            final_nonce,
            "finalization",
            final_date,
            final_semantic,
            execution_sha,
        )
        if existing_final is None:
            connection.execute(
                f"INSERT INTO {CONSUMPTION_TABLE} VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    final_sha,
                    final_nonce,
                    "finalization",
                    final_date,
                    final_semantic,
                    execution_sha,
                    "consumed_pending_state",
                    now,
                    None,
                ),
            )
            final_status = "consumed_pending_state"
        elif existing_final[:5] == expected_final_prefix and existing_final[5] in {
            "consumed_pending_state",
            "state_applied",
        }:
            final_status = str(existing_final[5])
        else:
            raise ValueError("RECOVERY_RECEIPT_ALREADY_CONSUMED")
        connection.execute("COMMIT")
    except (sqlite3.IntegrityError, ValueError) as error:
        try:
            connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise ValueError("RECOVERY_RECEIPT_ALREADY_CONSUMED") from error
    finally:
        connection.close()
    return {
        "schemaVersion": "NEWS_GRASP_RECOVERY_RECEIPT_CONSUMPTION_V1",
        "receiptSha256": finalization["receiptSha256"],
        "executionReceiptSha256": execution["receiptSha256"],
        "kind": "finalization",
        "issueDate": finalization["issueDate"],
        "status": final_status,
        "ledgerPath": str(ledger),
    }


def mark_finalization_state_applied(
    *, receipt: dict[str, Any], live_bin_root: Path
) -> dict[str, Any]:
    receipt_sha, nonce, issue_date = _receipt_identity(receipt, kind="finalization")
    ledger = _consumption_ledger(live_bin_root)
    connection = sqlite3.connect(str(ledger), timeout=30, isolation_level=None)
    try:
        connection.execute("PRAGMA synchronous=FULL")
        _ensure_consumption_table(connection)
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            f"SELECT nonce, kind, issue_date, status FROM {CONSUMPTION_TABLE} "
            "WHERE receipt_sha256 = ?",
            (receipt_sha,),
        ).fetchone()
        if row is None or row[:3] != (nonce, "finalization", issue_date):
            raise ValueError("RECOVERY_FINALIZATION_STATE_JOURNAL_INVALID")
        if row[3] == "consumed_pending_state":
            connection.execute(
                f"UPDATE {CONSUMPTION_TABLE} SET status = ?, state_applied_at = ? "
                "WHERE receipt_sha256 = ?",
                ("state_applied", datetime.now(timezone.utc).isoformat(), receipt_sha),
            )
        elif row[3] != "state_applied":
            raise ValueError("RECOVERY_FINALIZATION_STATE_JOURNAL_INVALID")
        connection.execute("COMMIT")
    except (sqlite3.Error, ValueError) as error:
        try:
            connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise ValueError("RECOVERY_FINALIZATION_STATE_JOURNAL_INVALID") from error
    finally:
        connection.close()
    return {
        "schemaVersion": "NEWS_GRASP_RECOVERY_FINALIZATION_STATE_JOURNAL_V1",
        "receiptSha256": receipt_sha,
        "status": "state_applied",
        "ledgerPath": str(ledger),
    }


def mark_operation_applied(
    *, receipt: dict[str, Any], live_bin_root: Path, kind: str
) -> dict[str, Any]:
    if kind not in {"control_plane_repair", "execution"}:
        raise ValueError("RECOVERY_OPERATION_JOURNAL_INVALID")
    receipt_sha, nonce, issue_date = _receipt_identity(receipt, kind=kind)
    ledger = _consumption_ledger(live_bin_root)
    connection = sqlite3.connect(str(ledger), timeout=30, isolation_level=None)
    try:
        connection.execute("PRAGMA synchronous=FULL")
        _ensure_consumption_table(connection)
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            f"SELECT nonce, kind, issue_date, status FROM {CONSUMPTION_TABLE} "
            "WHERE receipt_sha256 = ?",
            (receipt_sha,),
        ).fetchone()
        if row is None or row[:3] != (nonce, kind, issue_date):
            raise ValueError("RECOVERY_OPERATION_JOURNAL_INVALID")
        if row[3] == "consumed_pending_operation":
            connection.execute(
                f"UPDATE {CONSUMPTION_TABLE} SET status = ?, state_applied_at = ? "
                "WHERE receipt_sha256 = ?",
                ("operation_applied", datetime.now(timezone.utc).isoformat(), receipt_sha),
            )
        elif row[3] != "operation_applied":
            raise ValueError("RECOVERY_OPERATION_JOURNAL_INVALID")
        connection.execute("COMMIT")
    except (sqlite3.Error, ValueError) as error:
        try:
            connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise ValueError("RECOVERY_OPERATION_JOURNAL_INVALID") from error
    finally:
        connection.close()
    return {
        "schemaVersion": "NEWS_GRASP_RECOVERY_OPERATION_JOURNAL_V1",
        "receiptSha256": receipt_sha,
        "kind": kind,
        "status": "operation_applied",
        "ledgerPath": str(ledger),
    }


def is_consumed(*, receipt: dict[str, Any], live_bin_root: Path, kind: str) -> bool:
    ledger = _consumption_ledger(live_bin_root)
    if not ledger.is_file() or ledger.is_symlink():
        return False
    connection = sqlite3.connect(f"file:{ledger}?mode=ro", uri=True, timeout=10)
    try:
        _ensure_consumption_table(connection)
        row = connection.execute(
            f"SELECT nonce, kind, issue_date FROM {CONSUMPTION_TABLE} WHERE receipt_sha256 = ?",
            (str(receipt.get("receiptSha256") or ""),),
        ).fetchone()
    except sqlite3.Error:
        return False
    finally:
        connection.close()
    return row == (
        str(receipt.get("nonce") or ""),
        kind,
        str(receipt.get("issueDate") or ""),
    )


def pending_finalization_for_execution(
    *, execution_receipt_sha256: str, live_bin_root: Path
) -> str:
    ledger = _consumption_ledger(live_bin_root)
    if not ledger.is_file() or ledger.is_symlink():
        return ""
    connection = sqlite3.connect(str(ledger), timeout=5)
    try:
        _ensure_consumption_table(connection)
        row = connection.execute(
            f"SELECT receipt_sha256 FROM {CONSUMPTION_TABLE} "
            "WHERE parent_execution_sha256 = ? AND kind = 'finalization' "
            "AND status = 'consumed_pending_state'",
            (execution_receipt_sha256,),
        ).fetchone()
    finally:
        connection.close()
    return str(row[0]) if row else ""


def finalization_state_applied(*, receipt: dict[str, Any], live_bin_root: Path) -> bool:
    ledger = _consumption_ledger(live_bin_root)
    if not ledger.is_file() or ledger.is_symlink():
        return False
    connection = sqlite3.connect(str(ledger), timeout=5)
    try:
        _ensure_consumption_table(connection)
        row = connection.execute(
            f"SELECT nonce, kind, issue_date, status FROM {CONSUMPTION_TABLE} "
            "WHERE receipt_sha256 = ?",
            (str(receipt.get("receiptSha256") or ""),),
        ).fetchone()
    finally:
        connection.close()
    return row == (
        str(receipt.get("nonce") or ""),
        "finalization",
        str(receipt.get("issueDate") or ""),
        "state_applied",
    )


def _drift_witness(preflight: dict[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, str]] = []
    for row in preflight.get("managedFiles") or []:
        if not isinstance(row, dict):
            continue
        normalized = {"name": str(row.get("name") or "")}
        for role in ("ops", "runtime", "live"):
            observation = row.get(role)
            normalized[f"{role}Sha256"] = str(
                observation.get("sha256") if isinstance(observation, dict) else ""
            )
        rows.append(normalized)
    return {
        "reasonCode": str(preflight.get("reasonCode") or ""),
        "managedFiles": rows,
    }


def _assert_safe_output_path(path: Path, *, root: Path, code: str) -> tuple[Path, Path]:
    candidate = Path(os.path.abspath(path))
    boundary = Path(os.path.abspath(root))
    if candidate == boundary or boundary not in candidate.parents:
        raise ValueError(code)
    cursor = candidate.parent
    while True:
        if cursor.exists():
            if cursor.is_symlink() or not cursor.is_dir():
                raise ValueError(code)
            try:
                if bool(cursor.stat().st_file_attributes & 0x400):
                    raise ValueError(code)
            except AttributeError:
                pass
        if os.path.normcase(str(cursor)) == os.path.normcase(str(boundary)):
            break
        parent = cursor.parent
        if parent == cursor:
            raise ValueError(code)
        cursor = parent
    return candidate, boundary


def write_atomic_json(
    path: Path, value: dict[str, Any], *, root: Path | None = None
) -> None:
    boundary = root if root is not None else path.parent
    path, _ = _assert_safe_output_path(
        path, root=boundary, code="RECOVERY_RECEIPT_OUTPUT_INVALID"
    )
    relative_parent = path.parent.relative_to(Path(os.path.abspath(boundary)))
    verified_storage.validated_managed_root(
        repo_root=boundary,
        relative_parts=tuple(relative_parent.parts),
        create=True,
        code="RECOVERY_RECEIPT_OUTPUT_INVALID",
    )
    verified_storage.atomic_write_json(
        path,
        value,
        root=boundary,
        code="RECOVERY_RECEIPT_OUTPUT_INVALID",
    )


def validate_producer_lineage(
    *,
    producer_state: dict[str, Any],
    issue_date: str,
    artifact_root: Path,
    ops_root: Path,
    expected_run_intent: str = "ScheduledRecoveryFull",
) -> dict[str, str]:
    """auditと通常runnerが共有するproducer lineageの唯一のvalidator。"""
    from tools import daily_self_heal

    artifact = _resolved(artifact_root)
    ops = _resolved(ops_root)
    run_id = str(producer_state.get("run_id") or "")
    if expected_run_intent not in {"ScheduledProduction", "ScheduledRecoveryFull"}:
        raise ValueError("FINALIZATION_PRODUCER_LINEAGE_INVALID")
    run_intent = expected_run_intent
    expected = daily_self_heal._producer_lineage_expected(
        repo_root=artifact,
        ops_root=ops,
        date=issue_date,
        run_intent=run_intent,
        run_id=run_id,
    )
    try:
        repo_matches = _same_path(
            Path(str(producer_state.get("repo_dir") or "")), artifact
        )
    except (OSError, RuntimeError):
        repo_matches = False
    if (
        producer_state.get("date") != issue_date
        or not run_id
        or producer_state.get("run_intent") != run_intent
        or not repo_matches
        or any(producer_state.get(field) != value for field, value in expected.items())
    ):
        raise ValueError("FINALIZATION_PRODUCER_LINEAGE_INVALID")
    return expected


def create_control_plane_repair_receipt(
    *,
    issue_date: str,
    artifact_root: Path,
    ops_root: Path,
    production_runtime_root: Path,
    live_bin_root: Path,
    preflight: dict[str, Any],
    recovery_authority_path: Path,
    recovery_authority: dict[str, Any],
    authority_ledger_witness: dict[str, Any],
) -> dict[str, Any]:
    reason = str(preflight.get("reasonCode") or "")
    if reason not in ALLOWED_REPAIR_REASONS:
        raise ValueError("CONTROL_PLANE_REPAIR_REASON_INVALID")
    _validate_embedded_receipt(
        recovery_authority,
        issue_date=issue_date,
        allowed_schemas={"SCHEDULED_RECOVERY_AUTHORITY_V1"},
        code="CONTROL_PLANE_REPAIR_AUTHORITY_INVALID",
    )
    authority_path = _contained_regular_file(
        recovery_authority_path,
        root=_resolved(artifact_root) / "build",
        code="CONTROL_PLANE_REPAIR_AUTHORITY_INVALID",
    )
    failure_sha = str(recovery_authority.get("failureReceiptSha256") or "")
    if not SHA256_RE.fullmatch(failure_sha):
        raise ValueError("CONTROL_PLANE_REPAIR_AUTHORITY_INVALID")
    authority_ledger_witness = _validated_broker_witness(
        authority_ledger_witness,
        issue_date=issue_date,
        authority_sha=str(recovery_authority["receiptSha256"]),
        failure_sha=failure_sha,
    )
    witness = _drift_witness(preflight)
    now = datetime.now(timezone.utc)
    return _seal(
        {
            "schemaVersion": REPAIR_SCHEMA,
            "issueDate": issue_date,
            "artifactRoot": str(_resolved(artifact_root)),
            "opsRoot": str(_resolved(ops_root)),
            "productionRuntimeRoot": str(_resolved(production_runtime_root)),
            "liveBinRoot": str(_resolved(live_bin_root)),
            "opsHead": _git_head(_resolved(ops_root)),
            "reasonCode": reason,
            "driftWitnessSha256": hashlib.sha256(canonical_bytes(witness)).hexdigest(),
            "recoveryAuthorityPath": str(authority_path),
            "recoveryAuthorityFileSha256": file_sha256(authority_path),
            "recoveryAuthorityReceiptSha256": recovery_authority["receiptSha256"],
            "failureReceiptSha256": failure_sha,
            **_broker_binding(authority_ledger_witness),
            "issuedAt": now.isoformat(),
            "nonce": uuid.uuid4().hex,
        }
    )


def validate_control_plane_repair_receipt(
    *,
    receipt_path: Path,
    issue_date: str,
    artifact_root: Path,
    ops_root: Path,
    production_runtime_root: Path,
    live_bin_root: Path,
    current_preflight: dict[str, Any],
) -> dict[str, Any]:
    value = _validate_seal(
        _read_json(
            receipt_path,
            root=_resolved(artifact_root) / "build",
            code="CONTROL_PLANE_REPAIR_RECEIPT_INVALID",
        ),
        schema=REPAIR_SCHEMA,
        code="CONTROL_PLANE_REPAIR_RECEIPT_INVALID",
    )
    expected_roots = {
        "artifactRoot": artifact_root,
        "opsRoot": ops_root,
        "productionRuntimeRoot": production_runtime_root,
        "liveBinRoot": live_bin_root,
    }
    if value.get("issueDate") != issue_date:
        raise ValueError("CONTROL_PLANE_REPAIR_IDENTITY_DRIFT")
    for field, expected in expected_roots.items():
        try:
            matches = _same_path(Path(str(value.get(field) or "")), expected)
        except (OSError, RuntimeError):
            matches = False
        if not matches:
            raise ValueError("CONTROL_PLANE_REPAIR_IDENTITY_DRIFT")
    if value.get("opsHead") != _git_head(_resolved(ops_root)):
        raise ValueError("CONTROL_PLANE_REPAIR_IDENTITY_DRIFT")
    if str(current_preflight.get("reasonCode") or "") not in ALLOWED_REPAIR_REASONS:
        raise ValueError("CONTROL_PLANE_REPAIR_WITNESS_DRIFT")
    witness_sha = hashlib.sha256(canonical_bytes(_drift_witness(current_preflight))).hexdigest()
    if witness_sha != value.get("driftWitnessSha256"):
        raise ValueError("CONTROL_PLANE_REPAIR_WITNESS_DRIFT")
    authority_path = _contained_regular_file(
        Path(str(value.get("recoveryAuthorityPath") or "")),
        root=_resolved(artifact_root) / "build",
        code="CONTROL_PLANE_REPAIR_AUTHORITY_INVALID",
    )
    authority = _read_json(
        authority_path,
        root=_resolved(artifact_root) / "build",
        code="CONTROL_PLANE_REPAIR_AUTHORITY_INVALID",
    )
    _validate_embedded_receipt(
        authority,
        issue_date=issue_date,
        allowed_schemas={"SCHEDULED_RECOVERY_AUTHORITY_V1"},
        code="CONTROL_PLANE_REPAIR_AUTHORITY_INVALID",
    )
    if (
        file_sha256(authority_path) != value.get("recoveryAuthorityFileSha256")
        or authority.get("receiptSha256") != value.get("recoveryAuthorityReceiptSha256")
    ):
        raise ValueError("CONTROL_PLANE_REPAIR_AUTHORITY_INVALID")
    witness = _validate_authority_via_broker(
        issue_date=issue_date,
        authority_path=authority_path,
        authority=authority,
        failure_receipt_sha256=str(value.get("failureReceiptSha256") or ""),
    )
    _assert_broker_binding(value, witness, code="CONTROL_PLANE_REPAIR_AUTHORITY_INVALID")
    _assert_fresh(value.get("issuedAt"), code="CONTROL_PLANE_REPAIR_CLOCK_INVALID")
    return value


def create_recovery_execution_receipt(
    *,
    issue_date: str,
    artifact_root: Path,
    ops_root: Path,
    production_runtime_root: Path,
    live_bin_root: Path,
    runner_state_path: Path,
    runner_script_path: Path,
    recovery_authority_path: Path,
    recovery_authority: dict[str, Any],
    scheduled_failure_receipt_path: Path,
    scheduled_failure_receipt: dict[str, Any],
    authority_ledger_witness: dict[str, Any],
    audit_accepted_at: str,
) -> dict[str, Any]:
    artifact = _resolved(artifact_root)
    ops = _resolved(ops_root)
    runtime = _resolved(production_runtime_root)
    live = _resolved(live_bin_root)
    _validate_embedded_receipt(
        recovery_authority,
        issue_date=issue_date,
        allowed_schemas={"SCHEDULED_RECOVERY_AUTHORITY_V1"},
        code="RECOVERY_EXECUTION_AUTHORITY_INVALID",
    )
    _validate_embedded_receipt(
        scheduled_failure_receipt,
        issue_date=issue_date,
        allowed_schemas={"SCHEDULED_FAILURE_RECEIPT_V1", "SCHEDULED_FAILURE_RECEIPT_V2"},
        code="RECOVERY_EXECUTION_FAILURE_INVALID",
    )
    scheduled_status = str(
        scheduled_failure_receipt.get("scheduledAttemptStatus")
        or scheduled_failure_receipt.get("scheduled_attempt_status")
        or ""
    )
    if scheduled_status != "failed":
        raise ValueError("RECOVERY_EXECUTION_FAILURE_INVALID")
    failure_receipt_sha = str(scheduled_failure_receipt.get("receiptSha256") or "")
    if recovery_authority.get("failureReceiptSha256") != failure_receipt_sha:
        raise ValueError("RECOVERY_EXECUTION_AUTHORITY_INVALID")
    authority_ledger_witness = _validated_broker_witness(
        authority_ledger_witness,
        issue_date=issue_date,
        authority_sha=str(recovery_authority["receiptSha256"]),
        failure_sha=failure_receipt_sha,
    )
    authority_file = _contained_regular_file(
        recovery_authority_path,
        root=artifact / "build",
        code="RECOVERY_EXECUTION_AUTHORITY_INVALID",
    )
    failure_file = _contained_regular_file(
        scheduled_failure_receipt_path,
        root=artifact / "build",
        code="RECOVERY_EXECUTION_FAILURE_INVALID",
    )
    runner_file = _contained_regular_file(
        runner_script_path, root=live, code="RECOVERY_EXECUTION_RUNNER_INVALID"
    )
    expected_state = live / "news-grasp-runner-state.json"
    if not _same_lexical_path(runner_state_path, expected_state):
        raise ValueError("RECOVERY_EXECUTION_STATE_INVALID")
    t0 = _parse_clock(audit_accepted_at, code="RECOVERY_EXECUTION_CLOCK_INVALID")
    issued = datetime.now(timezone.utc)
    if t0 > issued + FUTURE_TOLERANCE:
        raise ValueError("RECOVERY_EXECUTION_CLOCK_INVALID")
    return _seal(
        {
            "schemaVersion": EXECUTION_SCHEMA,
            "issueDate": issue_date,
            "artifactRoot": str(artifact),
            "opsRoot": str(ops),
            "productionRuntimeRoot": str(runtime),
            "liveBinRoot": str(live),
            "artifactHead": _git_head(artifact),
            "opsHead": _git_head(ops),
            "runnerStatePath": str(expected_state),
            "runnerScriptPath": str(runner_file),
            "runnerSha256": file_sha256(runner_file),
            "recoveryAuthorityPath": str(authority_file),
            "recoveryAuthorityFileSha256": file_sha256(authority_file),
            "recoveryAuthorityReceiptSha256": recovery_authority["receiptSha256"],
            "scheduledFailureReceiptPath": str(failure_file),
            "scheduledFailureReceiptFileSha256": file_sha256(failure_file),
            "scheduledFailureReceiptSha256": scheduled_failure_receipt["receiptSha256"],
            **_broker_binding(authority_ledger_witness),
            "auditAcceptedAt": audit_accepted_at,
            "issuedAt": issued.isoformat(),
            "nonce": uuid.uuid4().hex,
        }
    )


def create_recovery_execution_receipt_v2(
    *,
    transaction: dict[str, Any],
    branch: str,
    resume_from_stage: str,
    capability_reservation_path: Path,
    capability_reservation_sha256: str,
    python_path: Path,
    python_sha256: str,
    production_runtime_binding_sha256: str,
    live_binding_sha256: str,
    operational_truth_path: Path,
    operational_truth: dict[str, Any],
    **legacy_arguments: Any,
) -> dict[str, Any]:
    """既存authority/lineage bindingを保ったままV2 admission fieldsを追加する。"""

    legacy = create_recovery_execution_receipt(**legacy_arguments)
    issue_date = str(legacy["issueDate"])
    anchor = datetime.fromisoformat(f"{issue_date}T06:40:00+09:00")
    body = {
        **{key: item for key, item in legacy.items() if key != "receiptSha256"},
        "schemaVersion": EXECUTION_SCHEMA_V2,
        "transactionId": str(transaction.get("transactionId") or ""),
        "fencingToken": transaction.get("fencingToken"),
        "branch": branch,
        "resumeFromStage": resume_from_stage or None,
        "roots": {
            "artifactRepoRoot": legacy["artifactRoot"],
            "opsRepoRoot": legacy["opsRoot"],
            "productionRuntimeRoot": legacy["productionRuntimeRoot"],
            "liveBinRoot": legacy["liveBinRoot"],
        },
        "rootHashes": {
            "artifactRepoHead": legacy["artifactHead"],
            "opsRepoHead": legacy["opsHead"],
            "productionRuntimeBindingSha256": production_runtime_binding_sha256,
            "liveBindingSha256": live_binding_sha256,
        },
        "python": {"path": str(_resolved(python_path)), "sha256": python_sha256},
        "capabilityReservation": {
            "receiptPath": str(_resolved(capability_reservation_path)),
            "receiptSha256": capability_reservation_sha256,
        },
        "checkpointReceiptSha256": (
            operational_truth.get("stageCheckpointReceiptSha256")
            if branch == "ResumeFromStage"
            else None
        ),
        "operationalTruthPath": str(_resolved(operational_truth_path)),
        "operationalTruthReceiptSha256": operational_truth.get("receiptSha256"),
        "artifactSnapshot": (
            {
                "operationalTruthPath": str(_resolved(operational_truth_path)),
                "operationalTruthReceiptSha256": operational_truth.get(
                    "receiptSha256"
                ),
                "existingArtifactCount": int(
                    (operational_truth.get("artifactDelta") or {}).get(
                        "presentCount", 0
                    )
                ),
                "quarantineRequired": False,
            }
            if branch == "ScheduledRecoveryFull"
            else None
        ),
        "deadline": {
            "auditSloAnchor": anchor.isoformat(),
            "targetDeadline": (anchor + timedelta(minutes=60)).isoformat(),
            "highCostCutoff": (anchor + timedelta(minutes=75)).isoformat(),
            "hardDeadline": (anchor + timedelta(minutes=90)).isoformat(),
            "postGreenMinutes": 15,
        },
        "singleUse": True,
        "issuedAt": str(transaction.get("updatedAt") or legacy["issuedAt"]),
    }
    result = _seal(body)
    validate_recovery_execution_receipt_v2(result, issue_date=issue_date)
    return result


def validate_recovery_execution_receipt(
    *,
    receipt_path: Path,
    issue_date: str,
    artifact_root: Path,
    ops_root: Path,
    production_runtime_root: Path,
    live_bin_root: Path,
    runner_state_path: Path,
    runner_script_path: Path,
) -> dict[str, Any]:
    artifact = _resolved(artifact_root)
    ops = _resolved(ops_root)
    runtime = _resolved(production_runtime_root)
    live = _resolved(live_bin_root)
    value = _validate_execution_seal(
        _read_json(
            receipt_path,
            root=artifact / "build",
            code="RECOVERY_EXECUTION_RECEIPT_INVALID",
        ),
        code="RECOVERY_EXECUTION_RECEIPT_INVALID",
    )
    if value.get("schemaVersion") == EXECUTION_SCHEMA_V2:
        validate_recovery_execution_receipt_v2(value, issue_date=issue_date)
    if value.get("issueDate") != issue_date:
        raise ValueError("RECOVERY_EXECUTION_IDENTITY_DRIFT")
    for field, expected in (
        ("artifactRoot", artifact),
        ("opsRoot", ops),
        ("productionRuntimeRoot", runtime),
        ("liveBinRoot", live),
        ("runnerScriptPath", runner_script_path),
    ):
        try:
            matches = _same_path(Path(str(value.get(field) or "")), Path(expected))
        except (OSError, RuntimeError):
            matches = False
        if not matches:
            raise ValueError("RECOVERY_EXECUTION_IDENTITY_DRIFT")
    if not _same_lexical_path(
        Path(str(value.get("runnerStatePath") or "")), runner_state_path
    ):
        raise ValueError("RECOVERY_EXECUTION_IDENTITY_DRIFT")
    if value.get("artifactHead") != _git_head(artifact) or value.get("opsHead") != _git_head(ops):
        raise ValueError("RECOVERY_EXECUTION_IDENTITY_DRIFT")
    if file_sha256(_resolved(runner_script_path)) != value.get("runnerSha256"):
        raise ValueError("RECOVERY_EXECUTION_RUNNER_DRIFT")
    for prefix, schemas, code in (
        ("recoveryAuthority", {"SCHEDULED_RECOVERY_AUTHORITY_V1"}, "RECOVERY_EXECUTION_AUTHORITY_INVALID"),
        ("scheduledFailureReceipt", {"SCHEDULED_FAILURE_RECEIPT_V1", "SCHEDULED_FAILURE_RECEIPT_V2"}, "RECOVERY_EXECUTION_FAILURE_INVALID"),
    ):
        evidence_path = _contained_regular_file(
            Path(str(value.get(f"{prefix}Path") or "")),
            root=artifact / "build",
            code=code,
        )
        evidence = _read_json(evidence_path, root=artifact / "build", code=code)
        _validate_embedded_receipt(
            evidence, issue_date=issue_date, allowed_schemas=schemas, code=code
        )
        file_field = f"{prefix}FileSha256"
        receipt_field = (
            "recoveryAuthorityReceiptSha256"
            if prefix == "recoveryAuthority"
            else "scheduledFailureReceiptSha256"
        )
        if (
            file_sha256(evidence_path) != value.get(file_field)
            or evidence.get("receiptSha256") != value.get(receipt_field)
        ):
            raise ValueError(code)
    authority_path = Path(str(value.get("recoveryAuthorityPath") or ""))
    authority = _read_json(
        authority_path, root=artifact / "build", code="RECOVERY_EXECUTION_AUTHORITY_INVALID"
    )
    witness = _validate_authority_via_broker(
        issue_date=issue_date,
        authority_path=authority_path,
        authority=authority,
        failure_receipt_sha256=str(value.get("scheduledFailureReceiptSha256") or ""),
    )
    _assert_broker_binding(value, witness, code="RECOVERY_EXECUTION_AUTHORITY_INVALID")
    if value.get("schemaVersion") == EXECUTION_SCHEMA_V2:
        roots = value["roots"]
        if any(
            not _same_path(Path(str(roots[field])), expected)
            for field, expected in (
                ("artifactRepoRoot", artifact),
                ("opsRepoRoot", ops),
                ("productionRuntimeRoot", runtime),
                ("liveBinRoot", live),
            )
        ):
            raise ValueError("RECOVERY_EXECUTION_IDENTITY_DRIFT")
        python_path = _resolved(Path(str(value["python"]["path"])))
        if file_sha256(python_path) != value["python"]["sha256"]:
            raise ValueError("RECOVERY_EXECUTION_PYTHON_DRIFT")
        runtime_binding_path = live / "news-grasp-recovery-runtime-binding-v1.json"
        runtime_binding = _read_json(
            runtime_binding_path,
            root=live,
            code="RECOVERY_EXECUTION_BINDING_DRIFT",
        )
        high_cost_binding_path = _contained_regular_file(
            Path(str(runtime_binding.get("highCostBindingPath") or "")),
            root=live,
            code="RECOVERY_EXECUTION_BINDING_DRIFT",
        )
        if (
            file_sha256(runtime_binding_path)
            != value["rootHashes"]["productionRuntimeBindingSha256"]
            or file_sha256(high_cost_binding_path)
            != value["rootHashes"]["liveBindingSha256"]
        ):
            raise ValueError("RECOVERY_EXECUTION_BINDING_DRIFT")
        reservation_path = _contained_regular_file(
            Path(str(value["capabilityReservation"]["receiptPath"])),
            root=artifact / "build",
            code="RECOVERY_CAPABILITY_RESERVATION_INVALID",
        )
        reservation = _read_json(
            reservation_path,
            root=artifact / "build",
            code="RECOVERY_CAPABILITY_RESERVATION_INVALID",
        )
        reservation_body = {
            key: item for key, item in reservation.items() if key != "receiptSha256"
        }
        if (
            reservation.get("receiptSha256")
            != value["capabilityReservation"]["receiptSha256"]
            or reservation.get("receiptSha256")
            != hashlib.sha256(canonical_bytes(reservation_body)).hexdigest()
        ):
            raise ValueError("RECOVERY_CAPABILITY_RESERVATION_INVALID")
        truth_path = _contained_regular_file(
            Path(str(value.get("operationalTruthPath") or "")),
            root=artifact / "build",
            code="RECOVERY_OPERATIONAL_TRUTH_INVALID",
        )
        truth = _read_json(
            truth_path,
            root=artifact / "build",
            code="RECOVERY_OPERATIONAL_TRUTH_INVALID",
        )
        truth_body = {
            key: item for key, item in truth.items() if key != "receiptSha256"
        }
        if (
            truth.get("receiptSha256")
            != value.get("operationalTruthReceiptSha256")
            or truth.get("receiptSha256")
            != hashlib.sha256(canonical_bytes(truth_body)).hexdigest()
        ):
            raise ValueError("RECOVERY_OPERATIONAL_TRUTH_INVALID")
        if value.get("branch") == "ResumeFromStage" and (
            truth.get("stageCheckpointReceiptSha256")
            != value.get("checkpointReceiptSha256")
            or truth.get("resumeStage") != value.get("resumeFromStage")
        ):
            raise ValueError("RECOVERY_CHECKPOINT_INVALID")
        if value.get("branch") == "ScheduledRecoveryFull" and (
            (truth.get("artifactDelta") or {}).get("exists") is not False
            or truth.get("scheduledAttemptReachedRunner") is not False
        ):
            raise ValueError("RECOVERY_FULL_BRANCH_FORBIDDEN")
    t0 = _parse_clock(value.get("auditAcceptedAt"), code="RECOVERY_EXECUTION_CLOCK_INVALID")
    issued = _parse_clock(value.get("issuedAt"), code="RECOVERY_EXECUTION_CLOCK_INVALID")
    if not (t0 <= issued <= datetime.now(timezone.utc) + FUTURE_TOLERANCE):
        raise ValueError("RECOVERY_EXECUTION_CLOCK_INVALID")
    _assert_fresh(value.get("issuedAt"), code="RECOVERY_EXECUTION_CLOCK_INVALID")
    return value


def validate_recovery_execution_receipt_v2(
    value: object, *, issue_date: str
) -> dict[str, Any]:
    """branch・root・Python・capability・deadlineを一枚に固定するpure validator。"""

    code = "RECOVERY_EXECUTION_RECEIPT_V2_INVALID"
    if not isinstance(value, dict):
        raise ValueError(code)
    try:
        receipt = _validate_seal(value, schema=EXECUTION_SCHEMA_V2, code=code)
    except ValueError as error:
        raise ValueError(code) from error
    roots = receipt.get("roots")
    root_hashes = receipt.get("rootHashes")
    python = receipt.get("python")
    reservation = receipt.get("capabilityReservation")
    artifact_snapshot = receipt.get("artifactSnapshot")
    deadline = receipt.get("deadline")
    branch = str(receipt.get("branch") or "")
    resume_stage = str(receipt.get("resumeFromStage") or "")
    if (
        receipt.get("issueDate") != issue_date
        or not str(receipt.get("transactionId") or "")
        or not isinstance(receipt.get("fencingToken"), int)
        or int(receipt["fencingToken"]) <= 0
        or branch
        not in {
            "ResumeFromStage",
            "ScheduledRecoveryFull",
            "minimal_unblocker",
            "registered_reconcile",
        }
        or (branch == "ResumeFromStage" and not resume_stage)
        or (
            branch == "ResumeFromStage"
            and not SHA256_RE.fullmatch(
                str(receipt.get("checkpointReceiptSha256") or "")
            )
        )
        or (branch == "ScheduledRecoveryFull" and bool(resume_stage))
        or (
            branch == "ScheduledRecoveryFull"
            and (
                not isinstance(artifact_snapshot, dict)
                or set(artifact_snapshot)
                != {
                    "operationalTruthPath",
                    "operationalTruthReceiptSha256",
                    "existingArtifactCount",
                    "quarantineRequired",
                }
                or artifact_snapshot.get("existingArtifactCount") != 0
                or artifact_snapshot.get("quarantineRequired") is not False
                or not SHA256_RE.fullmatch(
                    str(artifact_snapshot.get("operationalTruthReceiptSha256") or "")
                )
            )
        )
        or receipt.get("singleUse") is not True
        or not str(receipt.get("nonce") or "")
        or not str(receipt.get("operationalTruthPath") or "")
        or not SHA256_RE.fullmatch(
            str(receipt.get("operationalTruthReceiptSha256") or "")
        )
        or not isinstance(roots, dict)
        or set(roots)
        != {
            "artifactRepoRoot",
            "opsRepoRoot",
            "productionRuntimeRoot",
            "liveBinRoot",
        }
        or not all(str(roots.get(field) or "") for field in roots)
        or not isinstance(root_hashes, dict)
        or set(root_hashes)
        != {
            "artifactRepoHead",
            "opsRepoHead",
            "productionRuntimeBindingSha256",
            "liveBindingSha256",
        }
        or not GIT_SHA_RE.fullmatch(str(root_hashes.get("artifactRepoHead") or ""))
        or not GIT_SHA_RE.fullmatch(str(root_hashes.get("opsRepoHead") or ""))
        or not SHA256_RE.fullmatch(
            str(root_hashes.get("productionRuntimeBindingSha256") or "")
        )
        or not SHA256_RE.fullmatch(str(root_hashes.get("liveBindingSha256") or ""))
        or not isinstance(python, dict)
        or set(python) != {"path", "sha256"}
        or not str(python.get("path") or "")
        or not SHA256_RE.fullmatch(str(python.get("sha256") or ""))
        or not isinstance(reservation, dict)
        or set(reservation) != {"receiptPath", "receiptSha256"}
        or not str(reservation.get("receiptPath") or "")
        or not SHA256_RE.fullmatch(str(reservation.get("receiptSha256") or ""))
        or not isinstance(deadline, dict)
        or set(deadline)
        != {
            "auditSloAnchor",
            "targetDeadline",
            "highCostCutoff",
            "hardDeadline",
            "postGreenMinutes",
        }
        or deadline.get("postGreenMinutes") != 15
    ):
        raise ValueError(code)
    try:
        anchor = _parse_clock(deadline["auditSloAnchor"], code=code)
        target = _parse_clock(deadline["targetDeadline"], code=code)
        cutoff = _parse_clock(deadline["highCostCutoff"], code=code)
        hard = _parse_clock(deadline["hardDeadline"], code=code)
        issued = _parse_clock(receipt.get("issuedAt"), code=code)
    except ValueError as error:
        raise ValueError(code) from error
    expected_anchor = datetime.fromisoformat(f"{issue_date}T06:40:00+09:00")
    if (
        anchor != expected_anchor
        or target != anchor + timedelta(minutes=60)
        or cutoff != anchor + timedelta(minutes=75)
        or hard != anchor + timedelta(minutes=90)
        or issued > hard
    ):
        raise ValueError(code)
    return dict(receipt)


def create_finalization_receipt(
    *,
    issue_date: str,
    artifact_root: Path,
    ops_root: Path,
    production_runtime_root: Path,
    live_bin_root: Path,
    runner_state_path: Path,
    runner_script_path: Path,
    manifest_path: Path,
    manifest: dict[str, Any],
    recovery_authority_path: Path,
    recovery_authority: dict[str, Any],
    scheduled_failure_receipt_path: Path,
    scheduled_failure_receipt: dict[str, Any],
    authority_ledger_witness: dict[str, Any],
    execution_receipt_path: Path,
    execution_receipt: dict[str, Any],
    producer_state_path: Path,
    producer_state_sha256: str,
    audit_accepted_at: str,
) -> dict[str, Any]:
    required_manifest = (
        manifest.get("schemaVersion") == "NEWS_GRASP_PUBLISH_COMPLETE_V2"
        and manifest.get("date") == issue_date
        and manifest.get("ok") is True
        and manifest.get("public_status") == "green"
        and manifest.get("scheduled_attempt_status") == "failed_then_recovered"
        and manifest.get("recovery_attempt_status") == "succeeded"
        and all(
            GIT_SHA_RE.fullmatch(str(manifest.get(field) or ""))
            for field in ("source_commit", "artifact_commit", "publish_commit")
        )
    )
    if not required_manifest:
        raise ValueError("FINALIZATION_MANIFEST_INVALID")
    _validate_embedded_receipt(
        recovery_authority,
        issue_date=issue_date,
        allowed_schemas={"SCHEDULED_RECOVERY_AUTHORITY_V1"},
        code="FINALIZATION_RECOVERY_AUTHORITY_INVALID",
    )
    _validate_embedded_receipt(
        scheduled_failure_receipt,
        issue_date=issue_date,
        allowed_schemas={"SCHEDULED_FAILURE_RECEIPT_V1", "SCHEDULED_FAILURE_RECEIPT_V2"},
        code="FINALIZATION_SCHEDULED_FAILURE_INVALID",
    )
    scheduled_status = str(
        scheduled_failure_receipt.get("scheduledAttemptStatus")
        or scheduled_failure_receipt.get("scheduled_attempt_status")
        or ""
    )
    if scheduled_status != "failed":
        raise ValueError("FINALIZATION_SCHEDULED_FAILURE_INVALID")
    authority_ledger_witness = _validated_broker_witness(
        authority_ledger_witness,
        issue_date=issue_date,
        authority_sha=str(recovery_authority["receiptSha256"]),
        failure_sha=str(scheduled_failure_receipt["receiptSha256"]),
    )
    artifact = _resolved(artifact_root)
    ops = _resolved(ops_root)
    runtime = _resolved(production_runtime_root)
    live = _resolved(live_bin_root)
    manifest_file = _contained_regular_file(
        manifest_path, root=artifact / "build", code="FINALIZATION_MANIFEST_INVALID"
    )
    authority_file = _contained_regular_file(
        recovery_authority_path,
        root=artifact / "build",
        code="FINALIZATION_RECOVERY_AUTHORITY_INVALID",
    )
    failure_file = _contained_regular_file(
        scheduled_failure_receipt_path,
        root=artifact / "build",
        code="FINALIZATION_SCHEDULED_FAILURE_INVALID",
    )
    runner_file = _contained_regular_file(
        runner_script_path, root=live, code="FINALIZATION_RUNNER_INVALID"
    )
    expected_state = live / "news-grasp-runner-state.json"
    if os.path.normcase(str(Path(runner_state_path).resolve())) != os.path.normcase(
        str(expected_state.resolve())
    ):
        raise ValueError("FINALIZATION_RUNNER_STATE_INVALID")
    execution_file = _contained_regular_file(
        execution_receipt_path,
        root=artifact / "build",
        code="FINALIZATION_EXECUTION_RECEIPT_INVALID",
    )
    execution = _validate_execution_seal(
        execution_receipt, code="FINALIZATION_EXECUTION_RECEIPT_INVALID"
    )
    if (
        execution.get("issueDate") != issue_date
        or execution.get("recoveryAuthorityReceiptSha256")
        != recovery_authority.get("receiptSha256")
        or execution.get("scheduledFailureReceiptSha256")
        != scheduled_failure_receipt.get("receiptSha256")
        or execution.get("artifactRoot") != str(artifact)
        or execution.get("opsRoot") != str(ops)
        or execution.get("productionRuntimeRoot") != str(runtime)
        or execution.get("liveBinRoot") != str(live)
        or any(
            execution.get(field) != value
            for field, value in _broker_binding(authority_ledger_witness).items()
        )
    ):
        raise ValueError("FINALIZATION_EXECUTION_RECEIPT_INVALID")
    producer_file = _contained_regular_file(
        producer_state_path,
        root=artifact / "build",
        code="FINALIZATION_PRODUCER_STATE_INVALID",
    )
    if file_sha256(producer_file) != producer_state_sha256:
        raise ValueError("FINALIZATION_PRODUCER_STATE_INVALID")
    producer_state = _read_json(
        producer_file,
        root=artifact / "build",
        code="FINALIZATION_PRODUCER_STATE_INVALID",
    )
    validate_producer_lineage(
        producer_state=producer_state,
        issue_date=issue_date,
        artifact_root=artifact,
        ops_root=ops,
    )
    generation_id = str(
        manifest.get("runId")
        or producer_state.get("run_id")
        or producer_state.get("runId")
        or ""
    )
    if not generation_id:
        raise ValueError("FINALIZATION_GENERATION_ID_INVALID")
    result_path = _contained_regular_file(
        Path(str(manifest.get("commonFinalizationResultPath") or "")),
        root=artifact / "build",
        code="FINALIZATION_COMMON_RESULT_INVALID",
    )
    result = _read_json(
        result_path, root=artifact / "build", code="FINALIZATION_COMMON_RESULT_INVALID"
    )
    result_body = {key: item for key, item in result.items() if key != "receiptSha256"}
    result_authority = result.get("completionAuthority")
    result_lineage = (
        result_authority.get("producerLineage")
        if isinstance(result_authority, dict)
        else None
    )
    if (
        result.get("schemaVersion") != "NEWS_GRASP_COMMON_FINALIZATION_RESULT_V1"
        or result.get("issueDate") != issue_date
        or result.get("publicStatus") != "green"
        or result.get("receiptSha256")
        != hashlib.sha256(canonical_bytes(result_body)).hexdigest()
        or not isinstance(result_lineage, dict)
        or result_lineage.get("generationId") != generation_id
        or result_lineage.get("publishCommit") != manifest["publish_commit"]
    ):
        raise ValueError("FINALIZATION_COMMON_RESULT_INVALID")
    t0 = _parse_clock(audit_accepted_at, code="FINALIZATION_CLOCK_INVALID")
    tgreen_text = str(manifest.get("verified_at") or "")
    tgreen = _parse_clock(tgreen_text, code="FINALIZATION_CLOCK_INVALID")
    issued = datetime.now(timezone.utc)
    if not (t0 <= tgreen <= issued + FUTURE_TOLERANCE):
        raise ValueError("FINALIZATION_CLOCK_INVALID")
    return _seal(
        {
            "schemaVersion": FINALIZATION_SCHEMA,
            "issueDate": issue_date,
            "artifactRoot": str(artifact),
            "opsRoot": str(ops),
            "productionRuntimeRoot": str(runtime),
            "liveBinRoot": str(live),
            "artifactHead": _git_head(artifact),
            "opsHead": _git_head(ops),
            "runnerStatePath": str(expected_state),
            "runnerScriptPath": str(runner_file),
            "runnerSha256": file_sha256(runner_file),
            "manifestPath": str(manifest_file),
            "manifestSha256": file_sha256(manifest_file),
            "recoveryAuthorityPath": str(authority_file),
            "recoveryAuthorityFileSha256": file_sha256(authority_file),
            "recoveryAuthorityReceiptSha256": recovery_authority["receiptSha256"],
            "scheduledFailureReceiptPath": str(failure_file),
            "scheduledFailureReceiptFileSha256": file_sha256(failure_file),
            "scheduledFailureReceiptSha256": scheduled_failure_receipt["receiptSha256"],
            **_broker_binding(authority_ledger_witness),
            "executionReceiptPath": str(execution_file),
            "executionReceiptFileSha256": file_sha256(execution_file),
            "executionReceiptSha256": execution["receiptSha256"],
            "executionReceiptNonce": execution["nonce"],
            "producerStatePath": str(producer_file),
            "producerStateSha256": producer_state_sha256,
            "scheduledAttemptStatus": "failed_then_recovered",
            "recoveryAttemptStatus": "succeeded",
            "publicStatus": "green",
            "sourceCommit": manifest["source_commit"],
            "artifactCommit": manifest["artifact_commit"],
            "publishCommit": manifest["publish_commit"],
            "generationId": generation_id,
            "commonFinalizationResultPath": str(result_path),
            "commonFinalizationResultFileSha256": file_sha256(result_path),
            "commonFinalizationResultReceiptSha256": result["receiptSha256"],
            "auditAcceptedAt": audit_accepted_at,
            "publicGreenAt": tgreen_text,
            "completionGuardOutputPath": str(
                artifact / "build" / "publish-complete" / f"{issue_date}.automation-guard.json"
            ),
            "issuedAt": issued.isoformat(),
            "nonce": uuid.uuid4().hex,
        }
    )


def validate_finalization_receipt(
    *,
    receipt_path: Path,
    issue_date: str,
    artifact_root: Path,
    ops_root: Path,
    production_runtime_root: Path,
    live_bin_root: Path,
    runner_state_path: Path,
    runner_script_path: Path,
    require_execution_consumed: bool = True,
) -> dict[str, Any]:
    artifact = _resolved(artifact_root)
    ops = _resolved(ops_root)
    runtime = _resolved(production_runtime_root)
    live = _resolved(live_bin_root)
    raw_receipt = _read_json(
        receipt_path,
        root=artifact / "build",
        code="FINALIZATION_RECEIPT_INVALID",
    )
    if raw_receipt.get("schemaVersion") == FINALIZATION_SCHEMA_V1:
        raise ValueError("FINALIZATION_RECEIPT_LEGACY_READ_ONLY")
    value = _validate_seal(
        raw_receipt,
        schema=FINALIZATION_SCHEMA,
        code="FINALIZATION_RECEIPT_INVALID",
    )
    if value.get("issueDate") != issue_date:
        raise ValueError("FINALIZATION_IDENTITY_DRIFT")
    exact_paths = {
        "artifactRoot": artifact,
        "opsRoot": ops,
        "productionRuntimeRoot": runtime,
        "liveBinRoot": live,
        "runnerScriptPath": Path(runner_script_path),
    }
    for field, expected in exact_paths.items():
        try:
            matches = _same_path(Path(str(value.get(field) or "")), expected)
        except (OSError, RuntimeError):
            matches = False
        if not matches:
            raise ValueError("FINALIZATION_IDENTITY_DRIFT")
    if not _same_lexical_path(
        Path(str(value.get("runnerStatePath") or "")), Path(runner_state_path)
    ):
        raise ValueError("FINALIZATION_IDENTITY_DRIFT")
    if not _same_lexical_path(live, Path(runner_state_path).parent):
        raise ValueError("FINALIZATION_IDENTITY_DRIFT")
    if value.get("artifactHead") != _git_head(artifact) or value.get("opsHead") != _git_head(ops):
        raise ValueError("FINALIZATION_IDENTITY_DRIFT")
    if file_sha256(_resolved(runner_script_path)) != value.get("runnerSha256"):
        raise ValueError("FINALIZATION_RUNNER_DRIFT")
    manifest_path = _contained_regular_file(
        Path(str(value.get("manifestPath") or "")),
        root=artifact / "build",
        code="FINALIZATION_MANIFEST_INVALID",
    )
    if file_sha256(manifest_path) != value.get("manifestSha256"):
        raise ValueError("FINALIZATION_MANIFEST_DRIFT")
    manifest = _read_json(
        manifest_path, root=artifact / "build", code="FINALIZATION_MANIFEST_INVALID"
    )
    for source, target in (
        ("source_commit", "sourceCommit"),
        ("artifact_commit", "artifactCommit"),
        ("publish_commit", "publishCommit"),
    ):
        if manifest.get(source) != value.get(target):
            raise ValueError("FINALIZATION_MANIFEST_DRIFT")
    if (
        manifest.get("schemaVersion") != "NEWS_GRASP_PUBLISH_COMPLETE_V2"
        or manifest.get("date") != issue_date
        or manifest.get("ok") is not True
        or manifest.get("public_status") != "green"
        or manifest.get("scheduled_attempt_status") != "failed_then_recovered"
        or manifest.get("recovery_attempt_status") != "succeeded"
    ):
        raise ValueError("FINALIZATION_MANIFEST_INVALID")
    generation_id = str(value.get("generationId") or "")
    if not generation_id or manifest.get("runId") not in {None, generation_id}:
        raise ValueError("FINALIZATION_IDENTITY_DRIFT")
    result_path = _contained_regular_file(
        Path(str(value.get("commonFinalizationResultPath") or "")),
        root=artifact / "build",
        code="FINALIZATION_COMMON_RESULT_INVALID",
    )
    result = _read_json(
        result_path, root=artifact / "build", code="FINALIZATION_COMMON_RESULT_INVALID"
    )
    result_body = {key: item for key, item in result.items() if key != "receiptSha256"}
    result_authority = result.get("completionAuthority")
    result_lineage = (
        result_authority.get("producerLineage")
        if isinstance(result_authority, dict)
        else None
    )
    if (
        result.get("schemaVersion") != "NEWS_GRASP_COMMON_FINALIZATION_RESULT_V1"
        or result.get("issueDate") != issue_date
        or result.get("publicStatus") != "green"
        or result.get("receiptSha256")
        != hashlib.sha256(canonical_bytes(result_body)).hexdigest()
        or file_sha256(result_path) != value.get("commonFinalizationResultFileSha256")
        or result.get("receiptSha256")
        != value.get("commonFinalizationResultReceiptSha256")
        or not isinstance(result_lineage, dict)
        or result_lineage.get("generationId") != generation_id
        or result_lineage.get("publishCommit") != value.get("publishCommit")
    ):
        raise ValueError("FINALIZATION_COMMON_RESULT_INVALID")
    for prefix, schemas, code in (
        (
            "recoveryAuthority",
            {"SCHEDULED_RECOVERY_AUTHORITY_V1"},
            "FINALIZATION_RECOVERY_AUTHORITY_INVALID",
        ),
        (
            "scheduledFailureReceipt",
            {"SCHEDULED_FAILURE_RECEIPT_V1", "SCHEDULED_FAILURE_RECEIPT_V2"},
            "FINALIZATION_SCHEDULED_FAILURE_INVALID",
        ),
    ):
        evidence_path = _contained_regular_file(
            Path(str(value.get(f"{prefix}Path") or "")),
            root=artifact / "build",
            code=code,
        )
        evidence = _read_json(evidence_path, root=artifact / "build", code=code)
        _validate_embedded_receipt(
            evidence, issue_date=issue_date, allowed_schemas=schemas, code=code
        )
        file_field = f"{prefix}FileSha256"
        receipt_field = (
            "recoveryAuthorityReceiptSha256"
            if prefix == "recoveryAuthority"
            else "scheduledFailureReceiptSha256"
        )
        if (
            file_sha256(evidence_path) != value.get(file_field)
            or evidence.get("receiptSha256") != value.get(receipt_field)
        ):
            raise ValueError(code)
    authority_path = Path(str(value.get("recoveryAuthorityPath") or ""))
    authority = _read_json(
        authority_path, root=artifact / "build", code="FINALIZATION_RECOVERY_AUTHORITY_INVALID"
    )
    witness = _validate_authority_via_broker(
        issue_date=issue_date,
        authority_path=authority_path,
        authority=authority,
        failure_receipt_sha256=str(value.get("scheduledFailureReceiptSha256") or ""),
    )
    _assert_broker_binding(value, witness, code="FINALIZATION_RECOVERY_AUTHORITY_INVALID")
    execution_path = _contained_regular_file(
        Path(str(value.get("executionReceiptPath") or "")),
        root=artifact / "build",
        code="FINALIZATION_EXECUTION_RECEIPT_INVALID",
    )
    execution = validate_recovery_execution_receipt(
        receipt_path=execution_path,
        issue_date=issue_date,
        artifact_root=artifact,
        ops_root=ops,
        production_runtime_root=Path(str(value["productionRuntimeRoot"])),
        live_bin_root=Path(str(value["liveBinRoot"])),
        runner_state_path=runner_state_path,
        runner_script_path=runner_script_path,
    )
    if (
        file_sha256(execution_path) != value.get("executionReceiptFileSha256")
        or execution.get("receiptSha256") != value.get("executionReceiptSha256")
        or execution.get("nonce") != value.get("executionReceiptNonce")
        or (
            require_execution_consumed
            and not is_consumed(
                receipt=execution,
                live_bin_root=Path(str(value["liveBinRoot"])),
                kind="execution",
            )
        )
    ):
        raise ValueError("FINALIZATION_EXECUTION_RECEIPT_INVALID")
    producer_path = _contained_regular_file(
        Path(str(value.get("producerStatePath") or "")),
        root=artifact / "build",
        code="FINALIZATION_PRODUCER_STATE_INVALID",
    )
    if file_sha256(producer_path) != value.get("producerStateSha256"):
        raise ValueError("FINALIZATION_PRODUCER_STATE_INVALID")
    producer_state = _read_json(
        producer_path,
        root=artifact / "build",
        code="FINALIZATION_PRODUCER_STATE_INVALID",
    )
    validate_producer_lineage(
        producer_state=producer_state,
        issue_date=issue_date,
        artifact_root=artifact,
        ops_root=ops,
    )
    t0 = _parse_clock(value.get("auditAcceptedAt"), code="FINALIZATION_CLOCK_INVALID")
    tgreen = _parse_clock(value.get("publicGreenAt"), code="FINALIZATION_CLOCK_INVALID")
    issued = _parse_clock(value.get("issuedAt"), code="FINALIZATION_CLOCK_INVALID")
    now = datetime.now(timezone.utc)
    if not (t0 <= tgreen <= issued <= now + FUTURE_TOLERANCE):
        raise ValueError("FINALIZATION_CLOCK_INVALID")
    _assert_fresh(value.get("issuedAt"), code="FINALIZATION_CLOCK_INVALID")
    expected_guard = artifact / "build" / "publish-complete" / f"{issue_date}.automation-guard.json"
    if os.path.normcase(str(Path(str(value.get("completionGuardOutputPath") or "")).resolve())) != os.path.normcase(str(expected_guard.resolve())):
        raise ValueError("FINALIZATION_IDENTITY_DRIFT")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="News-Grasp recovery receipt validator")
    sub = parser.add_subparsers(dest="command", required=True)
    repair = sub.add_parser("validate-control-plane-repair")
    consume_repair = sub.add_parser("consume-control-plane-repair")
    mark_repair = sub.add_parser("mark-control-plane-repair-applied")
    execution = sub.add_parser("validate-execution")
    consume_execution = sub.add_parser("consume-execution")
    mark_execution = sub.add_parser("mark-execution-applied")
    final = sub.add_parser("validate-finalization")
    consume_final = sub.add_parser("consume-finalization")
    mark_final = sub.add_parser("mark-finalization-state-applied")
    issue = sub.add_parser("issue-finalization")
    for item in (
        repair,
        consume_repair,
        mark_repair,
        execution,
        consume_execution,
        mark_execution,
        final,
        consume_final,
        mark_final,
        issue,
    ):
        item.add_argument("--receipt", type=Path, required=True)
        item.add_argument("--issue-date", required=True)
        item.add_argument("--artifact-root", type=Path, required=True)
        item.add_argument("--ops-root", type=Path, required=True)
    for item in (repair, consume_repair, mark_repair):
        item.add_argument("--production-runtime-root", type=Path, required=True)
        item.add_argument("--live-bin-root", type=Path, required=True)
    for item in (execution, consume_execution, mark_execution, final, consume_final, mark_final, issue):
        item.add_argument("--runner-state", type=Path, required=True)
        item.add_argument("--runner-script", type=Path, required=True)
        item.add_argument("--production-runtime-root", type=Path, required=True)
        item.add_argument("--live-bin-root", type=Path, required=True)
    issue.add_argument("--manifest", type=Path, required=True)
    issue.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command in {"validate-control-plane-repair", "consume-control-plane-repair", "mark-control-plane-repair-applied"}:
        if args.command == "mark-control-plane-repair-applied":
            value = _validate_seal(
                _read_json(
                    args.receipt,
                    root=_resolved(args.artifact_root) / "build",
                    code="CONTROL_PLANE_REPAIR_RECEIPT_INVALID",
                ),
                schema=REPAIR_SCHEMA,
                code="CONTROL_PLANE_REPAIR_RECEIPT_INVALID",
            )
            value = mark_operation_applied(
                receipt=value,
                live_bin_root=args.live_bin_root,
                kind="control_plane_repair",
            )
            print(json.dumps(value, ensure_ascii=False, sort_keys=True))
            return 0
        from tools.news_grasp_control_plane import verify_control_plane

        preflight = verify_control_plane(
            artifact_root=args.artifact_root,
            ops_root=args.ops_root,
            production_runtime_root=args.production_runtime_root,
            live_bin_root=args.live_bin_root,
            issue_date=args.issue_date,
            run_intent="ScheduledRecoveryFull",
        )
        value = validate_control_plane_repair_receipt(
            receipt_path=args.receipt,
            issue_date=args.issue_date,
            artifact_root=args.artifact_root,
            ops_root=args.ops_root,
            production_runtime_root=args.production_runtime_root,
            live_bin_root=args.live_bin_root,
            current_preflight=preflight,
        )
        if args.command == "consume-control-plane-repair":
            value = consume_or_resume(
                receipt=value,
                live_bin_root=args.live_bin_root,
                kind="control_plane_repair",
            )
    elif args.command in {"validate-execution", "consume-execution", "mark-execution-applied"}:
        value = validate_recovery_execution_receipt(
            receipt_path=args.receipt,
            issue_date=args.issue_date,
            artifact_root=args.artifact_root,
            ops_root=args.ops_root,
            production_runtime_root=args.production_runtime_root,
            live_bin_root=args.live_bin_root,
            runner_state_path=args.runner_state,
            runner_script_path=args.runner_script,
        )
        if args.command == "consume-execution":
            value = consume_or_resume(
                receipt=value,
                live_bin_root=Path(str(value["liveBinRoot"])),
                kind="execution",
            )
        elif args.command == "mark-execution-applied":
            value = mark_operation_applied(
                receipt=value,
                live_bin_root=args.live_bin_root,
                kind="execution",
            )
    elif args.command in {
        "validate-finalization",
        "consume-finalization",
        "mark-finalization-state-applied",
    }:
        value = validate_finalization_receipt(
            receipt_path=args.receipt,
            issue_date=args.issue_date,
            artifact_root=args.artifact_root,
            ops_root=args.ops_root,
            production_runtime_root=args.production_runtime_root,
            live_bin_root=args.live_bin_root,
            runner_state_path=args.runner_state,
            runner_script_path=args.runner_script,
            require_execution_consumed=args.command == "mark-finalization-state-applied",
        )
        if args.command == "consume-finalization":
            validated_finalization = value
            execution = _validate_execution_seal(
                _read_json(
                    Path(str(value["executionReceiptPath"])),
                    root=_resolved(args.artifact_root) / "build",
                    code="FINALIZATION_EXECUTION_RECEIPT_INVALID",
                ),
                code="FINALIZATION_EXECUTION_RECEIPT_INVALID",
            )
            consumed = consume_finalization_chain(
                finalization=value,
                execution=execution,
                live_bin_root=args.live_bin_root,
            )
            value = {
                **consumed,
                "schemaVersion": "NEWS_GRASP_FINALIZATION_CONSUMPTION_V2",
                "finalizationReceiptSha256": validated_finalization["receiptSha256"],
                "finalizationReceiptFileSha256": file_sha256(args.receipt),
                "commonFinalizationResultPath": validated_finalization[
                    "commonFinalizationResultPath"
                ],
                "commonFinalizationResultFileSha256": validated_finalization[
                    "commonFinalizationResultFileSha256"
                ],
                "commonFinalizationResultReceiptSha256": validated_finalization[
                    "commonFinalizationResultReceiptSha256"
                ],
                "generationId": validated_finalization["generationId"],
                "publishCommit": validated_finalization["publishCommit"],
            }
        elif args.command == "mark-finalization-state-applied":
            state = _read_json(
                args.runner_state,
                root=_resolved(args.live_bin_root),
                code="RECOVERY_FINALIZATION_STATE_JOURNAL_INVALID",
            )
            if (
                state.get("date") != args.issue_date
                or state.get("status") != "publish_complete"
                or state.get("exit_code") != 0
                or state.get("recovery_finalization_receipt_sha256")
                != value.get("receiptSha256")
                or not _same_lexical_path(
                    Path(str(state.get("recovery_finalization_receipt_path") or "")),
                    args.receipt,
                )
            ):
                raise ValueError("RECOVERY_FINALIZATION_STATE_JOURNAL_INVALID")
            value = mark_finalization_state_applied(
                receipt=value,
                live_bin_root=args.live_bin_root,
            )
    else:
        execution = validate_recovery_execution_receipt(
            receipt_path=args.receipt,
            issue_date=args.issue_date,
            artifact_root=args.artifact_root,
            ops_root=args.ops_root,
            production_runtime_root=args.production_runtime_root,
            live_bin_root=args.live_bin_root,
            runner_state_path=args.runner_state,
            runner_script_path=args.runner_script,
        )
        artifact = _resolved(args.artifact_root)
        expected_output = (
            artifact
            / "build"
            / "publish-complete"
            / f"{args.issue_date}.finalization-receipt.json"
        )
        if not _same_lexical_path(args.output, expected_output):
            raise ValueError("FINALIZATION_RECEIPT_OUTPUT_INVALID")
        pending_sha = pending_finalization_for_execution(
            execution_receipt_sha256=str(execution["receiptSha256"]),
            live_bin_root=args.live_bin_root,
        )
        if pending_sha:
            existing = validate_finalization_receipt(
                receipt_path=expected_output,
                issue_date=args.issue_date,
                artifact_root=artifact,
                ops_root=args.ops_root,
                production_runtime_root=args.production_runtime_root,
                live_bin_root=args.live_bin_root,
                runner_state_path=args.runner_state,
                runner_script_path=args.runner_script,
                require_execution_consumed=True,
            )
            if existing.get("receiptSha256") != pending_sha:
                raise ValueError("FINALIZATION_PENDING_RECEIPT_INVALID")
            value = existing
            print(json.dumps(value, ensure_ascii=False, sort_keys=True))
            return 0
        manifest_path = _contained_regular_file(
            args.manifest,
            root=artifact / "build",
            code="FINALIZATION_MANIFEST_INVALID",
        )
        manifest = _read_json(
            manifest_path,
            root=artifact / "build",
            code="FINALIZATION_MANIFEST_INVALID",
        )
        authority_path = Path(str(execution["recoveryAuthorityPath"]))
        failure_path = Path(str(execution["scheduledFailureReceiptPath"]))
        authority = _read_json(
            authority_path,
            root=artifact / "build",
            code="FINALIZATION_RECOVERY_AUTHORITY_INVALID",
        )
        failure = _read_json(
            failure_path,
            root=artifact / "build",
            code="FINALIZATION_SCHEDULED_FAILURE_INVALID",
        )
        witness = _validate_authority_via_broker(
            issue_date=args.issue_date,
            authority_path=authority_path,
            authority=authority,
            failure_receipt_sha256=str(failure["receiptSha256"]),
        )
        producer_value = _read_json(
            args.runner_state,
            root=_resolved(args.runner_state).parent,
            code="FINALIZATION_PRODUCER_STATE_INVALID",
        )
        producer_snapshot = (
            artifact
            / "build"
            / "recovery-authority"
            / f"{args.issue_date}-producer-state.json"
        )
        validate_producer_lineage(
            producer_state=producer_value,
            issue_date=args.issue_date,
            artifact_root=artifact,
            ops_root=args.ops_root,
        )
        write_atomic_json(producer_snapshot, producer_value, root=artifact)
        value = create_finalization_receipt(
            issue_date=args.issue_date,
            artifact_root=artifact,
            ops_root=args.ops_root,
            production_runtime_root=Path(str(execution["productionRuntimeRoot"])),
            live_bin_root=Path(str(execution["liveBinRoot"])),
            runner_state_path=args.runner_state,
            runner_script_path=args.runner_script,
            manifest_path=manifest_path,
            manifest=manifest,
            recovery_authority_path=authority_path,
            recovery_authority=authority,
            scheduled_failure_receipt_path=failure_path,
            scheduled_failure_receipt=failure,
            authority_ledger_witness=witness,
            execution_receipt_path=args.receipt,
            execution_receipt=execution,
            producer_state_path=producer_snapshot,
            producer_state_sha256=file_sha256(producer_snapshot),
            audit_accepted_at=str(execution["auditAcceptedAt"]),
        )
        write_atomic_json(expected_output, value, root=artifact)
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
