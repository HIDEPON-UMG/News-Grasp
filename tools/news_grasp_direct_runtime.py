"""News-Grasp direct mainline runtime state and dispatcher.

旧 runner を実行 authority に戻さず、Codex automation が direct 本線として
進めた工程を durable に記録する薄い producer である。caller の ``ok`` や
自由形式の completion JSON は authority にせず、現在 stage に対応する
semantic verifier の観測だけで stage 遷移を行う。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tomllib
import uuid
from contextlib import closing
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ``python -m tools.news_grasp_direct_runtime daily`` で起動した場合も、
# 日次producerがimportするcanonical moduleと同じmodule instanceを共有する。
# これが無いと ``__main__`` とpackage名で二重loadされ、DirectRunStoreの型identityが
# 分裂して同一process・同一writerという日次契約を破る。
if __name__ == "__main__" and __spec__ is not None:
    sys.modules[__spec__.name] = sys.modules[__name__]


DIRECT_STAGES = (
    "title_control",
    "issue_inventory",
    "category_collection",
    "evidence_dedup_freshness",
    "category_digest",
    "reporter_validation",
    "articles_jsonl",
    "summary",
    "daily_audio",
    "deepdive_article",
    "deepdive_quality",
    "html_docs",
    "daily_quality",
    "youtube_podcasts",
    "playlist",
    "notification",
    "distribution",
    "publish_status",
    "commit_push",
    "pages_verify",
    "public_completion",
)

PUBLIC_SURFACES = (
    "web",
    "daily_audio",
    "deepdive_article",
    "deepdive_audio",
    "youtube_daily",
    "youtube_deepdive",
    "playlist",
    "notification",
    "distribution",
    "publish_status",
    "remote_commit",
    "pages",
)

RUNTIME_SCHEMA = "NEWS_GRASP_DIRECT_RUNTIME_V1"
RUNTIME_SCHEMA_V2 = "NEWS_GRASP_DIRECT_RUNTIME_V2"
MAINLINE_RECEIPT_SCHEMA = "NEWS_GRASP_DIRECT_MAINLINE_RECEIPT_V1"
PUBLIC_SCHEMA = "NEWS_GRASP_DIRECT_PUBLIC_VERIFICATION_V1"
PUBLIC_SCHEMA_V2 = "NEWS_GRASP_DIRECT_PUBLIC_VERIFICATION_V2"
CHILD_RESULT_SCHEMA = "NEWS_GRASP_CHILD_RESULT_V1"
APPLIED_RECEIPT_SCHEMA = "NEWS_GRASP_APPLIED_STAGE_RECEIPT_V1"
PREDICATE_CLAIM_SCHEMA = "NEWS_GRASP_PREDICATE_CLAIM_V1"
CONSUMER_PUBLIC_VERIFICATION_RECEIPT_SCHEMA = "NEWS_GRASP_CONSUMER_PUBLIC_VERIFICATION_RECEIPT_V1"
RUN_INTENT = "scheduled_production_direct"
AUTOMATION_ID = "news-grasp-6-40"
JST = timezone(timedelta(hours=9), name="Asia/Tokyo")
TITLE_SUCCESS = {"updated", "already_ok"}
TITLE_NONBLOCKING = {"unavailable", "failed", "skipped"}
MAX_CLI_EVIDENCE_BYTES = 1024 * 1024
_REGISTERED_CONSUMER_CAPABILITY = object()
TIMING_EVENT_KINDS = (
    "internal_processing",
    "queue",
    "external_wait",
    "retry",
    "handoff",
    "user_wait",
    "failure",
    "unmeasured",
)
DAILY_OPERATION_ORDER = (
    "static_check",
    "scoped_contract_unit",
    "current_issue_integration",
    "external_publication",
    "consumer_public_verification",
    "atomic_completion",
)
DAILY_SEQUENCE_SCHEMA = "NEWS_GRASP_DAILY_SEQUENCE_RECEIPT_V2"


def _strict_json_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """重複keyを受け入れないJSON object decoder。"""

    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("child_result_duplicate_key")
        result[key] = value
    return result


def _snake_key(key: Any) -> str:
    value = str(key)
    explicit = {
        "schemaVersion": "schema_version",
        "inputHash": "input_hash",
        "stageId": "stage_id",
        "operationId": "operation_id",
        "writerLease": "writer_lease",
        "runId": "run_id",
        "createdAt": "created_at",
        "completedAt": "completed_at",
        "sourceIdentity": "source_identity",
        "generationId": "generation_id",
        "predicateId": "predicate_id",
    }
    if value in explicit:
        return explicit[value]
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    return value.replace("-", "_").casefold()


def _canonicalize_child_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            canonical_key = _snake_key(key)
            if canonical_key in result:
                raise ValueError("child_result_canonical_key_collision")
            result[canonical_key] = _canonicalize_child_value(item)
        return result
    if isinstance(value, list):
        return [_canonicalize_child_value(item) for item in value]
    return value


def _invalid_child_result(reason: str, *, raw: Any = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schemaVersion": CHILD_RESULT_SCHEMA,
        "ok": False,
        "status": "invalid_child_result",
        "reason_code": reason,
        "reasonCode": reason,
    }
    if raw is not None:
        result["raw_type"] = type(raw).__name__
    return result


def parse_child_result(
    raw: bytes | str,
    *,
    expected_schema: str = CHILD_RESULT_SCHEMA,
    expected_input_hash: str,
) -> dict[str, Any]:
    """childのUTF-8一行JSONをmutation前に正規化・検証する。

    不正入力は例外でDBへ到達させず、``ok=False`` の観測結果として返す。
    有効な結果はcamelCase互換入力をcanonical snake_caseへ変換する。
    """

    try:
        if isinstance(raw, bytes):
            text = raw.decode("utf-8", errors="strict")
        elif isinstance(raw, str):
            text = raw
        else:
            return _invalid_child_result("child_result_transport_type", raw=raw)
        if text.startswith("\ufeff"):
            return _invalid_child_result("child_result_bom_forbidden", raw=raw)
        if "\r" in text or "\n" in text:
            return _invalid_child_result("child_result_not_one_line", raw=raw)
        # JSON transportは一行に限定するが、JSON文法上の先頭/末尾空白は
        # payloadの一部ではないためcanonical decode前に除去する。
        text = text.strip()
        if not text:
            return _invalid_child_result("child_result_json_invalid", raw=raw)
        decoder = json.JSONDecoder(object_pairs_hook=_strict_json_object_pairs)
        value, end = decoder.raw_decode(text)
        if text[end:].strip():
            return _invalid_child_result("child_result_trailing_data", raw=raw)
        canonical = _canonicalize_child_value(value)
        if not isinstance(canonical, Mapping):
            return _invalid_child_result("child_result_object_required", raw=raw)
        schema = canonical.get("schema_version")
        input_hash = canonical.get("input_hash")
        if schema != expected_schema:
            return _invalid_child_result("child_result_schema_mismatch", raw=raw)
        if input_hash != expected_input_hash:
            return _invalid_child_result("child_result_input_hash_mismatch", raw=raw)
        if "ok" in canonical and canonical.get("ok") is not True:
            return _invalid_child_result("child_result_ok_false", raw=raw)
        status = str(canonical.get("status") or "").casefold()
        if status in {"", "red", "failed", "blocked", "error", "invalid", "not_executed"}:
            return _invalid_child_result("child_result_status_red", raw=raw)
        output_hash = str(canonical.get("output_hash") or "").strip()
        if output_hash:
            without_hash = dict(canonical)
            without_hash.pop("output_hash", None)
            actual_hash = hashlib.sha256(
                _json_dump(without_hash).encode("utf-8")
            ).hexdigest()
            if output_hash != actual_hash:
                return _invalid_child_result("child_result_output_hash_mismatch", raw=raw)
        return {
            "schemaVersion": CHILD_RESULT_SCHEMA,
            "ok": True,
            "status": "verified",
            "schema_version": str(schema),
            "input_hash": str(input_hash),
            "child_result": dict(canonical),
        }
    except ValueError as exc:
        reason = str(exc) or "child_result_json_invalid"
        if reason.startswith("child_result_"):
            return _invalid_child_result(reason, raw=raw)
        return _invalid_child_result("child_result_json_invalid", raw=raw)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return _invalid_child_result("child_result_json_invalid", raw=raw)


def slo_dispatch(*, elapsed_seconds: int | float) -> dict[str, Any]:
    """45/75/90分境界の次動作を一意に投影する。"""

    if isinstance(elapsed_seconds, bool):
        raise ValueError("elapsed_seconds_invalid")
    try:
        seconds = float(elapsed_seconds)
    except (TypeError, ValueError) as exc:
        raise ValueError("elapsed_seconds_invalid") from exc
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("elapsed_seconds_invalid")
    # 45分を越えてからではなく、45分到達時点でmethod_changeへ切り替える。
    # 75分・90分も同様に境界時刻を含めてdispatcherへ渡す。ただし既存の
    # ``time_band`` は運用レポートの互換性のためhigh_cost_frozenを維持する。
    if seconds < 45 * 60:
        band = "target"
        dispatch = "target"
    elif seconds < 75 * 60:
        band = "method_change"
        dispatch = "method_change"
    elif seconds < 90 * 60:
        band = "high_cost_frozen"
        dispatch = "scope_reduce"
    else:
        band = "slo_debt"
        dispatch = "deadline_revision"
    return {
        "schemaVersion": "NEWS_GRASP_SLO_DISPATCH_V1",
        "ok": True,
        "elapsed_seconds": seconds,
        "elapsed_minutes": seconds / 60.0,
        "time_band": band,
        "dispatch": dispatch,
        # 旧consumer向けの読み取り専用projection。実制御の正本は
        # deadline_revisionで、retryと新generationは引き続き禁止する。
        "compatibility_action": "slo_debt_continue_public" if dispatch == "deadline_revision" else None,
        "required_inventory_preserved": True,
        "consumer_verifier_preserved": True,
    }


def _close_open_timing_event(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    ended_at: str,
    evidence: Mapping[str, Any] | None = None,
) -> int | None:
    """同一runの最後の未完了区間を指定時刻で閉じる。"""

    row = conn.execute(
        """
        SELECT event_id,started_at,evidence_json
        FROM timing_events
        WHERE run_id=? AND ended_at=''
        ORDER BY event_id DESC LIMIT 1
        """,
        (run_id,),
    ).fetchone()
    if row is None:
        return None
    started = _parse_time(str(row[1]))
    ended = _parse_time(ended_at)
    if started is None or ended is None or ended < started:
        raise ValueError("timing_interval_invalid")
    original = _json_load(str(row[2]), {})
    merged = dict(original) if isinstance(original, Mapping) else {}
    if evidence:
        merged.update(dict(evidence))
    conn.execute(
        """
        UPDATE timing_events
        SET ended_at=?,elapsed_seconds=?,evidence_json=?
        WHERE event_id=? AND run_id=? AND ended_at=''
        """,
        (
            ended_at,
            max(0.0, (ended - started).total_seconds()),
            _json_dump(merged),
            int(row[0]),
            run_id,
        ),
    )
    return int(row[0])


def _append_timing_event_in_tx(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    event_kind: str,
    started_at: str,
    ended_at: str = "",
    evidence: Mapping[str, Any] | None = None,
) -> int:
    """前区間を閉じてから次区間を一つだけ開始する。"""

    if event_kind not in TIMING_EVENT_KINDS:
        raise ValueError("timing_event_kind_invalid")
    started = _parse_time(started_at)
    ended = _parse_time(ended_at) if ended_at else None
    if started is None or (ended is not None and ended < started):
        raise ValueError("timing_interval_invalid")
    _close_open_timing_event(conn, run_id=run_id, ended_at=started_at)
    elapsed = (ended - started).total_seconds() if ended is not None else None
    cursor = conn.execute(
        """
        INSERT INTO timing_events(
            run_id,event_kind,started_at,ended_at,elapsed_seconds,evidence_json
        ) VALUES(?,?,?,?,?,?)
        """,
        (
            run_id,
            event_kind,
            started_at,
            ended_at,
            elapsed,
            _json_dump(dict(evidence or {})),
        ),
    )
    return int(cursor.lastrowid)


def _reject_reparse_chain(path: str | Path, *, reason: str) -> None:
    """raw path componentのjunction/symlinkをresolve前後の境界で拒否する。"""

    absolute = Path(os.path.abspath(os.fspath(path)))
    for current in reversed((absolute, *absolute.parents)):
        if str(current) == current.anchor:
            continue
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            continue
        attributes = int(getattr(info, "st_file_attributes", 0))
        if stat.S_ISLNK(info.st_mode) or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise ValueError(reason)


def _now_jst() -> datetime:
    return datetime.now(JST)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=JST)
    return value.astimezone(JST).isoformat()


def _parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=JST)
    return parsed.astimezone(JST)


def _json_load(value: str, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _canonical_cwd(cwd: str | Path) -> str:
    return str(Path(cwd).expanduser().resolve())


def _validate_issue_date(issue_date: str) -> str:
    value = str(issue_date).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
        raise ValueError("issue_date_invalid")
    return value


def _opaque_id(prefix: str, issue_date: str, generation: int) -> str:
    return f"{prefix}-{issue_date}-{generation}-{uuid.uuid4().hex}"


def _call_generation(source: Callable[[], int] | int | None) -> int:
    if callable(source):
        value = source()
    elif source is None:
        value = 1
    else:
        value = source
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return 1
    return value


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    for method_name in ("as_dict", "to_dict"):
        method = getattr(value, method_name, None)
        if callable(method):
            projected = method()
            if isinstance(projected, Mapping):
                return dict(projected)
    return {"ok": False, "status": "invalid_verifier_result", "value": repr(value)}


def _row_value(row: sqlite3.Row, key: str, default: Any = None) -> Any:
    """旧V1 DBの未移行列をread-only projectionで安全に扱う。"""

    try:
        return row[key]
    except (IndexError, KeyError):
        return default


def _require_fencing_token(store: "DirectRunStore", fencing_token: int | None) -> int | None:
    """production mutationではleaseとfencing tokenを常に同時提示させる。"""

    if store.test_only_allow_semantic_verifier:
        return fencing_token
    if fencing_token is None:
        raise PermissionError("fencing_token_required")
    try:
        value = int(fencing_token)
    except (TypeError, ValueError) as exc:
        raise PermissionError("fencing_token_invalid") from exc
    if value <= 0:
        raise PermissionError("fencing_token_invalid")
    return value


class DirectRunStore:
    """SQLite backed state store for one direct News-Grasp issue run."""

    def __init__(
        self,
        state_root: str | Path,
        *,
        clock: Callable[[], datetime] | None = None,
        host_generation: Callable[[], int] | int | None = None,
        lease_ttl: timedelta = timedelta(minutes=10),
        semantic_verifier: Any = None,
        test_only_allow_semantic_verifier: bool = False,
        create: bool = True,
    ) -> None:
        self.state_root = Path(state_root)
        self.db_path = self.state_root / "direct-mainline.sqlite3"
        self.clock = clock or _now_jst
        self.host_generation = host_generation
        self.lease_ttl = lease_ttl
        if semantic_verifier is not None and test_only_allow_semantic_verifier is not True:
            raise PermissionError("semantic_verifier_injection_test_only")
        self.semantic_verifier = semantic_verifier
        self.test_only_allow_semantic_verifier = test_only_allow_semantic_verifier is True
        self._production_db_identity: tuple[int, int] | None = None
        self._schema_ready = False
        self._schema_migration_receipt: dict[str, Any] = {}
        if create:
            self.state_root.mkdir(parents=True, exist_ok=True)
            # 既存DBはconstructorでALTER/terminalizeしない。新規DBだけをここで
            # 初期化し、既存V1はstart_runの明示migration境界へ送る。
            self._init_db(allow_migration=not self.db_path.exists())
        else:
            if not self.state_root.is_dir():
                raise FileNotFoundError("direct_state_root_missing")
            if not self.db_path.is_file():
                raise FileNotFoundError("direct_state_db_missing")
            self._schema_ready = self._schema_complete()

    def now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None:
            value = value.replace(tzinfo=JST)
        return value.astimezone(JST)

    def connect(self) -> sqlite3.Connection:
        expected = self._production_db_identity
        if expected is not None:
            _reject_reparse_chain(self.state_root, reason="production_runtime_state_root_reparse_forbidden")
            observed = self._database_identity()
            if observed != expected:
                raise PermissionError("production_runtime_db_identity_changed")
        conn = sqlite3.connect(str(self.db_path))
        if expected is not None and self._database_identity() != expected:
            conn.close()
            raise PermissionError("production_runtime_db_identity_changed")
        conn.row_factory = sqlite3.Row
        return conn

    def _database_identity(self) -> tuple[int, int]:
        _reject_reparse_chain(self.db_path, reason="production_runtime_db_reparse_forbidden")
        info = os.lstat(self.db_path)
        attributes = int(getattr(info, "st_file_attributes", 0))
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise ValueError("production_runtime_db_invalid")
        return (info.st_dev, info.st_ino)

    def bind_production_runtime(self) -> None:
        if self.test_only_allow_semantic_verifier:
            return
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        if not local_app_data:
            raise EnvironmentError("localappdata_missing")
        requested = Path(os.path.abspath(os.fspath(self.state_root)))
        canonical = Path(os.path.abspath(os.fspath(Path(local_app_data) / "News-Grasp" / "direct-mainline")))
        if os.path.normcase(str(requested)) != os.path.normcase(str(canonical)):
            raise ValueError("production_runtime_state_root_not_canonical")
        _reject_reparse_chain(canonical, reason="production_runtime_state_root_reparse_forbidden")
        identity = self._database_identity()
        if self._production_db_identity is not None and self._production_db_identity != identity:
            raise PermissionError("production_runtime_db_identity_changed")
        self._production_db_identity = identity

    def _schema_complete(self) -> bool:
        required_columns = {
            "runtime_schema",
            "run_intent",
            "scheduler_trigger_at",
            "manifest_reservation_id",
            "fencing_token",
            "completion_elapsed_seconds",
            "completion_elapsed_at",
            "start_seal_json",
            "publish_seal_json",
            "external_started_at",
        }
        required_tables = {
            "stages",
            "runtime_migrations",
            "runtime_checkpoints",
            "runtime_manifest_rebindings",
            "timing_events",
            "applied_receipts",
            "predicate_claims",
            "daily_operation_receipts",
            "daily_operation_claims",
            "external_outbox",
            "notification_ledger",
            "runtime_migration_journal",
        }
        try:
            with closing(sqlite3.connect(str(self.db_path))) as conn:
                columns = {
                    str(row[1])
                    for row in conn.execute("PRAGMA table_info(runs)").fetchall()
                }
                tables = {
                    str(row[0])
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                index_sql = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='index' AND name='runs_active_identity_uq'"
                ).fetchone()
                sql = str(index_sql[0] if index_sql else "")
                outbox_columns = {
                    str(row[1])
                    for row in conn.execute("PRAGMA table_info(external_outbox)").fetchall()
                }
                outbox_index = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='index' AND name='external_outbox_run_logical_uq'"
                ).fetchone()
                outbox_index_sql = str(outbox_index[0] if outbox_index else "")
                return (
                    required_columns <= columns
                    and required_tables <= tables
                    and "run_intent" in sql
                    and "cwd" not in sql
                    and "logical_operation_id" in outbox_columns
                    and "run_id" in outbox_index_sql
                    and "logical_operation_id" in outbox_index_sql
                )
        except sqlite3.DatabaseError:
            return False

    def _recover_pending_schema_migration(self) -> dict[str, Any] | None:
        """中断journalを一次証拠からfinalizeまたはbackupへrollbackする。"""

        if not self.db_path.is_file():
            return None
        try:
            with closing(sqlite3.connect(f"file:{self.db_path.as_posix()}?mode=ro", uri=True)) as conn:
                rows = conn.execute(
                    "SELECT journal_id,db_path,from_schema,to_schema,backup_path,status "
                    "FROM runtime_migration_journal WHERE status='started' ORDER BY started_at"
                ).fetchall()
        except sqlite3.OperationalError:
            return None
        if not rows:
            return None
        if len(rows) != 1:
            raise RuntimeError("runtime_schema_migration_journal_ambiguous")
        journal_id, db_path, from_schema, to_schema, backup_raw, _status = rows[0]
        if (
            os.path.normcase(os.path.abspath(str(db_path)))
            != os.path.normcase(os.path.abspath(str(self.db_path)))
            or str(from_schema) != "NEWS_GRASP_DIRECT_RUNTIME_V1"
            or str(to_schema) != RUNTIME_SCHEMA_V2
        ):
            raise RuntimeError("runtime_schema_migration_journal_identity_invalid")
        backup_path = Path(str(backup_raw))
        if not str(backup_raw).strip():
            raise RuntimeError("runtime_schema_migration_incomplete:backup_missing")
        canonical_parent = self.state_root.resolve(strict=False)
        try:
            backup_path.resolve(strict=False).relative_to(canonical_parent)
        except ValueError as exc:
            raise RuntimeError("runtime_schema_migration_backup_outside_state_root") from exc
        if not backup_path.name.startswith(f"{self.db_path.name}.pre-daily-v2-"):
            raise RuntimeError("runtime_schema_migration_backup_identity_invalid")

        if self._schema_complete():
            # DDL/receipt commit後、journal finalize前だけがこの分岐へ来る。
            # 既存receiptがあれば再発行せず、それを同じjournalへ束縛する。
            with closing(self.connect()) as conn:
                receipt_row = conn.execute(
                    """
                    SELECT receipt_json FROM runtime_migrations
                    WHERE run_id='__runtime_schema__' AND from_schema=? AND to_schema=?
                    ORDER BY id DESC LIMIT 1
                    """,
                    (str(from_schema), str(to_schema)),
                ).fetchone()
            receipt = _json_load(str(receipt_row[0]), {}) if receipt_row is not None else {}
            if (
                not isinstance(receipt, Mapping)
                or receipt.get("schemaVersion") != "NEWS_GRASP_RUNTIME_SCHEMA_MIGRATION_V1"
                or receipt.get("toSchema") != RUNTIME_SCHEMA_V2
                or str(receipt.get("backupPath") or "") != str(backup_path)
            ):
                receipt = self._persist_schema_migration_receipt(
                    from_schema=str(from_schema),
                    status="migrated",
                    backup_path=str(backup_path),
                )
            completed_at = _iso(self.now())
            with closing(self.connect()) as conn:
                conn.execute("BEGIN IMMEDIATE")
                changed = conn.execute(
                    """
                    UPDATE runtime_migration_journal
                    SET status='completed',receipt_json=?,completed_at=?
                    WHERE journal_id=? AND status='started'
                    """,
                    (_json_dump(dict(receipt)), completed_at, str(journal_id)),
                ).rowcount
                if changed != 1:
                    conn.rollback()
                    raise RuntimeError("runtime_schema_migration_finalize_cas_conflict")
                conn.commit()
            self._schema_ready = True
            self._schema_migration_receipt = dict(receipt)
            return {
                "status": "completed_journal_recovered",
                "migration_receipt": dict(receipt),
            }

        # DDLが未完了なら、pre-migration backupのintegrityを確認して同じ
        # database inodeへSQLite backup APIで戻す。backup欠落・破損時は現stateを
        # 一切変更せずtyped Redにする。
        if not backup_path.is_file():
            raise RuntimeError("runtime_schema_migration_backup_missing")
        try:
            _reject_reparse_chain(
                backup_path,
                reason="runtime_schema_migration_backup_reparse_forbidden",
            )
            with closing(sqlite3.connect(str(backup_path))) as backup:
                if str(backup.execute("PRAGMA integrity_check").fetchone()[0]).casefold() != "ok":
                    raise RuntimeError("runtime_schema_migration_backup_integrity_red")
                with closing(sqlite3.connect(str(self.db_path))) as destination:
                    backup.backup(destination)
        except sqlite3.DatabaseError as exc:
            raise RuntimeError("runtime_schema_migration_backup_restore_red") from exc
        self._schema_ready = False
        # 旧実装のbackupはjournal insert後に採られている場合がある。その場合も
        # rollback済みを明記し、次のmigration attemptを妨げない。
        try:
            with closing(self.connect()) as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    """
                    UPDATE runtime_migration_journal
                    SET status='rolled_back_recovered',completed_at=?,receipt_json=?
                    WHERE status='started'
                    """,
                    (
                        _iso(self.now()),
                        _json_dump(
                            {
                                "schemaVersion": "NEWS_GRASP_RUNTIME_SCHEMA_MIGRATION_RECOVERY_V1",
                                "status": "rolled_back_recovered",
                                "backupPath": str(backup_path),
                            }
                        ),
                    ),
                )
                conn.commit()
        except sqlite3.OperationalError:
            pass
        return {"status": "rolled_back_for_retry", "backup_path": str(backup_path)}

    def ensure_runtime_schema(self) -> dict[str, Any]:
        """read-only検査→SQLite backup→明示migrationの順でV2 schemaを準備する。"""

        self._recover_pending_schema_migration()

        if self._schema_ready and self._schema_complete():
            with closing(self.connect()) as conn:
                conn.execute("BEGIN IMMEDIATE")
                self._terminalize_completed_identity_writers(conn)
                latest = conn.execute(
                    """
                    SELECT receipt_json FROM runtime_migrations
                    WHERE run_id='__runtime_schema__'
                    ORDER BY id DESC LIMIT 1
                    """
                ).fetchone()
                conn.commit()
            if latest is not None:
                loaded = _json_load(str(latest[0]), {})
                if isinstance(loaded, dict):
                    self._schema_migration_receipt = loaded
            notification_evidence = (
                self._schema_migration_receipt.get("notificationLedgerMigration")
                if isinstance(self._schema_migration_receipt, Mapping)
                else None
            )
            if (
                not self._schema_migration_receipt
                or not isinstance(notification_evidence, Mapping)
                or notification_evidence.get("table") != "notification_ledger"
                or notification_evidence.get("schemaVersion")
                != "NEWS_GRASP_NOTIFICATION_LEDGER_V2"
            ):
                self._schema_migration_receipt = self._persist_schema_migration_receipt(
                    from_schema="",
                    status="already_initialized",
                    backup_path="",
                )
            return {
                "schemaVersion": "NEWS_GRASP_RUNTIME_SCHEMA_MIGRATION_V1",
                "ok": True,
                "status": "already_migrated",
                "migrated": False,
                "migration_receipt": dict(self._schema_migration_receipt),
            }
        if not self.db_path.exists():
            self._init_db(allow_migration=True)
            self._schema_ready = True
            result = {
                "schemaVersion": "NEWS_GRASP_RUNTIME_SCHEMA_MIGRATION_V1",
                "ok": True,
                "status": "initialized",
                "migrated": True,
                "backup_path": "",
            }
            self._schema_migration_receipt = self._persist_schema_migration_receipt(
                from_schema="",
                status="initialized",
                backup_path="",
            )
            result["migration_receipt"] = dict(self._schema_migration_receipt)
            return result
        # 変更前はquery-onlyでintegrityとbytesを確認する。
        with closing(sqlite3.connect(f"file:{self.db_path.as_posix()}?mode=ro", uri=True)) as source:
            source.execute("PRAGMA query_only=ON")
            integrity = str(source.execute("PRAGMA integrity_check").fetchone()[0])
            if integrity.casefold() != "ok":
                raise RuntimeError("runtime_schema_preflight_integrity_red")
        backup_path = self.db_path.with_name(
            f"{self.db_path.name}.pre-daily-v2-{self.now().strftime('%Y%m%dT%H%M%S%f%z')}-{uuid.uuid4().hex}.bak"
        )
        journal_id = f"schema-journal-{uuid.uuid4().hex}"
        journal_started = _iso(self.now())
        # backupはjournal/DDLより先に採る。これによりDDL途中終了時も、backupは
        # 必ず純粋なpre-migration bytesであり、有限rollbackが可能になる。
        with closing(sqlite3.connect(str(self.db_path))) as source, closing(sqlite3.connect(str(backup_path))) as backup:
            source.backup(backup)
            if str(backup.execute("PRAGMA integrity_check").fetchone()[0]).casefold() != "ok":
                backup_path.unlink(missing_ok=True)
                raise RuntimeError("runtime_schema_backup_integrity_red")
        # DDL前にrecoverable journalを記録する。途中終了したjournalは次回の
        # preflightでbackup rollbackまたはreceipt finalizeへ必ず収束させる。
        with closing(sqlite3.connect(str(self.db_path))) as journal_conn:
            journal_conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runtime_migration_journal (
                    journal_id TEXT PRIMARY KEY,
                    db_path TEXT NOT NULL,
                    from_schema TEXT NOT NULL,
                    to_schema TEXT NOT NULL,
                    backup_path TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    receipt_json TEXT NOT NULL DEFAULT '{}',
                    started_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL DEFAULT ''
                )
                """
            )
            journal_conn.execute(
                """
                INSERT INTO runtime_migration_journal(
                    journal_id,db_path,from_schema,to_schema,backup_path,status,started_at
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    journal_id,
                    str(self.db_path),
                    "NEWS_GRASP_DIRECT_RUNTIME_V1",
                    RUNTIME_SCHEMA_V2,
                    str(backup_path),
                    "started",
                    journal_started,
                ),
            )
            journal_conn.commit()
        self._init_db(allow_migration=True)
        self._schema_ready = True
        self._schema_migration_receipt = self._persist_schema_migration_receipt(
            from_schema="NEWS_GRASP_DIRECT_RUNTIME_V1",
            status="migrated",
            backup_path=str(backup_path),
        )
        with closing(self.connect()) as journal_conn:
            journal_conn.execute(
                """
                UPDATE runtime_migration_journal
                SET status='completed',receipt_json=?,completed_at=?
                WHERE journal_id=? AND status='started'
                """,
                (_json_dump(self._schema_migration_receipt), _iso(self.now()), journal_id),
            )
            journal_conn.commit()
        result = {
            "schemaVersion": "NEWS_GRASP_RUNTIME_SCHEMA_MIGRATION_V1",
            "ok": True,
            "status": "migrated",
            "migrated": True,
            "backup_path": str(backup_path),
            "migration_receipt": dict(self._schema_migration_receipt),
        }
        return result

    def _persist_schema_migration_receipt(
        self,
        *,
        from_schema: str,
        status: str,
        backup_path: str,
    ) -> dict[str, Any]:
        """schema migrationをappend-only台帳へ記録し、ID/hashを返す。"""

        now_text = _iso(self.now())
        notification_status = (
            "migrated" if status == "migrated" else "initialized"
        )
        body = {
            "schemaVersion": "NEWS_GRASP_RUNTIME_SCHEMA_MIGRATION_V1",
            "status": status,
            "fromSchema": from_schema,
            "toSchema": RUNTIME_SCHEMA_V2,
            "backupPath": backup_path,
            "migratedAt": now_text,
            "notificationLedgerMigration": {
                "schemaVersion": "NEWS_GRASP_NOTIFICATION_LEDGER_V2",
                "status": notification_status,
                "table": "notification_ledger",
            },
        }
        migration_id = f"schema-{uuid.uuid4().hex}"
        migration_hash = hashlib.sha256(
            _json_dump({**body, "migrationId": migration_id}).encode("utf-8")
        ).hexdigest()
        receipt = {
            **body,
            "migrationId": migration_id,
            "migrationHash": migration_hash,
        }
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO runtime_migrations(
                    run_id,from_schema,to_schema,receipt_json,migrated_at
                ) VALUES(?,?,?,?,?)
                """,
                (
                    "__runtime_schema__",
                    from_schema,
                    RUNTIME_SCHEMA_V2,
                    _json_dump(receipt),
                    now_text,
                ),
            )
            conn.commit()
        return receipt

    def _terminalize_completed_identity_writers(self, conn: sqlite3.Connection) -> None:
        """completed済みidentityに残るwriterを外部副作用に応じて安全に閉じる。"""

        completed_identity_active_rows = conn.execute(
            """
            SELECT active.run_id, active.external_started_at
            FROM runs AS active
            WHERE active.status IN ('active','executing','finalizing')
              AND EXISTS (
                SELECT 1 FROM runs AS completed
                WHERE completed.automation_id = active.automation_id
                  AND completed.issue_date = active.issue_date
                  AND COALESCE(completed.run_intent, '') = COALESCE(active.run_intent, '')
                  AND completed.status IN ('completed','complete','green')
              )
            """
        ).fetchall()
        for stale in completed_identity_active_rows:
            side_effect_rows = conn.execute(
                "SELECT status FROM external_outbox WHERE run_id=?",
                (stale[0],),
            ).fetchall()
            side_effect_statuses = {str(item[0]).casefold() for item in side_effect_rows}
            has_unknown_delivery = bool(
                side_effect_statuses
                & {"unknown_delivery", "unknown_unobtainable", "unobtainable"}
            )
            has_started_effect = bool(str(stale[1] or "")) or bool(
                side_effect_statuses
                & {"started", "sent", "acknowledged", "completed"}
            )
            terminal_status = (
                "blocked_external_delivery_unknown"
                if has_unknown_delivery
                else "superseded_after_external_start"
                if has_started_effect
                else "stale_writer_rejected"
            )
            conn.execute(
                "UPDATE runs SET status=?, updated_at=? WHERE run_id=?",
                (terminal_status, _iso(self.now()), stale[0]),
            )

    def _init_db(self, *, allow_migration: bool = True) -> None:
        if self.db_path.exists() and not allow_migration:
            self._schema_ready = self._schema_complete()
            return
        with closing(self.connect()) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    automation_id TEXT NOT NULL,
                    cwd TEXT NOT NULL,
                    issue_date TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    writer_lease TEXT NOT NULL,
                    status TEXT NOT NULL,
                    current_stage_index INTEGER NOT NULL,
                    started_at TEXT NOT NULL,
                    lease_until TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL DEFAULT '',
                    title_status TEXT NOT NULL DEFAULT '',
                    actual_title TEXT NOT NULL DEFAULT '',
                    expected_title TEXT NOT NULL DEFAULT '',
                    post_publish_issue_list TEXT NOT NULL DEFAULT '[]',
                    surface_failures TEXT NOT NULL DEFAULT '[]',
                    exact_successor TEXT NOT NULL DEFAULT 'title_control'
                )
                """
            )
            existing_columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(runs)").fetchall()
            }
            additions = {
                "runtime_schema": "TEXT NOT NULL DEFAULT 'NEWS_GRASP_DIRECT_RUNTIME_V1'",
                "run_intent": "TEXT NOT NULL DEFAULT ''",
                "manifest_id": "TEXT NOT NULL DEFAULT ''",
                "manifest_reservation_id": "TEXT NOT NULL DEFAULT ''",
                "migration_receipt_json": "TEXT NOT NULL DEFAULT '{}'",
                "observation_receipt_json": "TEXT NOT NULL DEFAULT '{}'",
                "typed_issues_json": "TEXT NOT NULL DEFAULT '[]'",
                "finalization_nonce": "TEXT NOT NULL DEFAULT ''",
                "scheduler_trigger_at": "TEXT NOT NULL DEFAULT ''",
                "fencing_token": "INTEGER NOT NULL DEFAULT 0",
                "completion_elapsed_seconds": "REAL",
                "completion_elapsed_at": "TEXT NOT NULL DEFAULT ''",
                "start_seal_json": "TEXT NOT NULL DEFAULT '{}'",
                "publish_seal_json": "TEXT NOT NULL DEFAULT '{}'",
                "external_started_at": "TEXT NOT NULL DEFAULT ''",
            }
            for name, declaration in additions.items():
                if name not in existing_columns:
                    conn.execute(f"ALTER TABLE runs ADD COLUMN {name} {declaration}")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS external_outbox (
                    operation_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    logical_operation_id TEXT NOT NULL DEFAULT '',
                    side_effect_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    started_at TEXT NOT NULL DEFAULT '',
                    completed_at TEXT NOT NULL DEFAULT '',
                    idempotency_key TEXT NOT NULL DEFAULT '',
                    fencing_token INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT '',
                    provider_ack_status TEXT NOT NULL DEFAULT '',
                    output_hash TEXT NOT NULL DEFAULT ''
                )
                """
            )
            existing_outbox_columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(external_outbox)").fetchall()
            }
            outbox_additions = {
                "logical_operation_id": "TEXT NOT NULL DEFAULT ''",
                "idempotency_key": "TEXT NOT NULL DEFAULT ''",
                "fencing_token": "INTEGER NOT NULL DEFAULT 0",
                "updated_at": "TEXT NOT NULL DEFAULT ''",
                "provider_ack_status": "TEXT NOT NULL DEFAULT ''",
                "output_hash": "TEXT NOT NULL DEFAULT ''",
                "provider_receipt_json": "TEXT NOT NULL DEFAULT '{}'",
                "provider_receipt_hash": "TEXT NOT NULL DEFAULT ''",
                "provider_output_hash": "TEXT NOT NULL DEFAULT ''",
            }
            for name, declaration in outbox_additions.items():
                if name not in existing_outbox_columns:
                    conn.execute(f"ALTER TABLE external_outbox ADD COLUMN {name} {declaration}")
            # 旧schemaのoperation_idは物理PKとして保持し、logical IDを
            # run scopeへ分離する。table rebuildなしのadditive migration。
            conn.execute(
                "UPDATE external_outbox SET logical_operation_id=operation_id "
                "WHERE logical_operation_id=''"
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS external_outbox_run_logical_uq "
                "ON external_outbox(run_id,logical_operation_id)"
            )
            # 45分本線のsingle-flight identityはcwdに依存しない。既存indexを
            # 明示的に再生成し、古いruntimeのcwd単位制約を残さない。
            conn.execute("DROP INDEX IF EXISTS runs_active_identity_uq")
            active_duplicates = conn.execute(
                """
                SELECT automation_id, issue_date, COALESCE(run_intent, '') AS run_intent
                FROM runs
                WHERE status IN ('active','executing','finalizing')
                GROUP BY automation_id, issue_date, COALESCE(run_intent, '')
                HAVING COUNT(*) > 1
                """
            ).fetchall()
            for duplicate in active_duplicates:
                duplicate_rows = conn.execute(
                    """
                    SELECT run_id, external_started_at
                    FROM runs
                    WHERE automation_id = ? AND issue_date = ?
                      AND COALESCE(run_intent, '') = ?
                      AND status IN ('active','executing','finalizing')
                    ORDER BY generation DESC, run_id DESC
                    """,
                    (duplicate[0], duplicate[1], duplicate[2]),
                ).fetchall()
                for stale in duplicate_rows[1:]:
                    side_effect_rows = conn.execute(
                        """
                        SELECT status FROM external_outbox
                        WHERE run_id=?
                        """,
                        (stale[0],),
                    ).fetchall()
                    side_effect_statuses = {str(item[0]).casefold() for item in side_effect_rows}
                    has_unknown_delivery = bool(
                        side_effect_statuses & {
                            "unknown_delivery",
                            "unknown_unobtainable",
                            "unobtainable",
                        }
                    )
                    has_started_effect = bool(str(stale[1] or "")) or bool(
                        side_effect_statuses & {
                            "started",
                            "sent",
                            "acknowledged",
                            "completed",
                        }
                    )
                    terminal_status = (
                        "blocked_external_delivery_unknown"
                        if has_unknown_delivery
                        else "superseded_after_external_start"
                        if has_started_effect
                        else "stale_writer_rejected"
                    )
                    conn.execute(
                        "UPDATE runs SET status=?, updated_at=? WHERE run_id=?",
                        (terminal_status, _iso(self.now()), stale[0]),
                    )
            self._terminalize_completed_identity_writers(conn)
            conn.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS runs_active_identity_uq
                   ON runs(automation_id,issue_date,run_intent)
                   WHERE status IN ('active','executing','finalizing')"""
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS stages (
                    run_id TEXT NOT NULL,
                    stage_index INTEGER NOT NULL,
                    stage_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    evidence_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY (run_id, stage_index)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runtime_migrations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    from_schema TEXT NOT NULL,
                    to_schema TEXT NOT NULL,
                    receipt_json TEXT NOT NULL,
                    migrated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runtime_checkpoints (
                    run_id TEXT NOT NULL,
                    checkpoint_minute INTEGER NOT NULL,
                    elapsed_minutes REAL NOT NULL,
                    recorded_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, checkpoint_minute)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runtime_manifest_rebindings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    previous_manifest_id TEXT NOT NULL,
                    manifest_id TEXT NOT NULL,
                    observation_receipt_json TEXT NOT NULL,
                    receipt_json TEXT NOT NULL,
                    rebound_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS timing_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    event_kind TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT NOT NULL DEFAULT '',
                    elapsed_seconds REAL,
                    evidence_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS applied_receipts (
                    operation_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    stage_id TEXT NOT NULL,
                    input_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    receipt_json TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS predicate_claims (
                    generation_id TEXT NOT NULL,
                    predicate_id TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    source_identity TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    claimed_at TEXT NOT NULL,
                    PRIMARY KEY (generation_id, predicate_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_operation_receipts (
                    run_id TEXT NOT NULL,
                    operation_id TEXT NOT NULL,
                    operation_index INTEGER NOT NULL,
                    input_hash TEXT NOT NULL,
                    handler_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    receipt_json TEXT NOT NULL,
                    applied_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, operation_id),
                    UNIQUE (run_id, operation_index)
                )
                """
            )
            existing_daily_columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(daily_operation_receipts)").fetchall()
            }
            daily_additions = {
                "fencing_token": "INTEGER NOT NULL DEFAULT 0",
                "receipt_hash": "TEXT NOT NULL DEFAULT ''",
            }
            for name, declaration in daily_additions.items():
                if name not in existing_daily_columns:
                    conn.execute(f"ALTER TABLE daily_operation_receipts ADD COLUMN {name} {declaration}")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_operation_claims (
                    run_id TEXT NOT NULL,
                    operation_id TEXT NOT NULL,
                    operation_index INTEGER NOT NULL,
                    input_hash TEXT NOT NULL,
                    handler_id TEXT NOT NULL,
                    fencing_token INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    claimed_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (run_id, operation_id),
                    UNIQUE (run_id, operation_index)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS notification_ledger (
                    idempotency_key TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    issue_date TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    provider_ack_status TEXT NOT NULL DEFAULT '',
                    sent_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runtime_migration_journal (
                    journal_id TEXT PRIMARY KEY,
                    db_path TEXT NOT NULL,
                    from_schema TEXT NOT NULL,
                    to_schema TEXT NOT NULL,
                    backup_path TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    receipt_json TEXT NOT NULL DEFAULT '{}',
                    started_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute(
                "DROP INDEX IF EXISTS idx_runs_identity"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_runs_identity "
                "ON runs (automation_id, issue_date, run_intent, generation)"
            )
            conn.commit()
        self._schema_ready = True


    def _latest_for_identity(
        self,
        conn: sqlite3.Connection,
        *,
        automation_id: str,
        cwd: str | None = None,
        issue_date: str,
        run_intent: str | None = None,
    ) -> sqlite3.Row | None:
        del cwd  # identityにcwdを含めない。保存値は監査表示用に保持する。
        intent = str(run_intent or "")
        return conn.execute(
            """
            SELECT * FROM runs
            WHERE automation_id = ? AND issue_date = ?
              AND COALESCE(run_intent, '') = ?
            ORDER BY generation DESC
            LIMIT 1
            """,
            (automation_id, issue_date, intent),
        ).fetchone()

    def _run_row(self, conn: sqlite3.Connection, run_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise ValueError("run_not_found")
        return row

    @staticmethod
    def _external_side_effect_state(conn: sqlite3.Connection, run_id: str) -> str:
        """outbox/送信receiptを含む外部副作用の現在分類を返す。"""

        try:
            rows = conn.execute(
                "SELECT status FROM external_outbox WHERE run_id=?",
                (run_id,),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        statuses = {str(item[0]).casefold() for item in rows}
        if statuses & {"unknown_delivery", "unknown_unobtainable", "unobtainable", "unknown"}:
            return "unknown_delivery"
        if statuses & {"started", "sent", "acknowledged", "completed", "success"}:
            return "started"
        return "none"


class PredicateLedger:
    """SQLite版のgeneration+predicate単一消費台帳。"""

    def __init__(self, store: DirectRunStore) -> None:
        if not isinstance(store, DirectRunStore):
            raise TypeError("predicate_ledger_store_invalid")
        self.store = store

    def claim_once(
        self,
        *,
        generation_id: str,
        predicate_id: str,
        owner: str,
        source_identity: str,
        evidence: Mapping[str, Any],
    ) -> dict[str, Any]:
        values = (generation_id, predicate_id, owner, source_identity)
        if any(not isinstance(item, str) or not item.strip() for item in values):
            raise ValueError("predicate_claim_binding_invalid")
        if not isinstance(evidence, Mapping):
            raise ValueError("predicate_claim_evidence_invalid")
        self.store.ensure_runtime_schema()
        now_text = _iso(self.store.now())
        with closing(self.store.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                """
                SELECT owner FROM predicate_claims
                WHERE generation_id=? AND predicate_id=?
                """,
                (generation_id, predicate_id),
            ).fetchone()
            if existing is not None:
                conn.rollback()
                if str(existing[0]) != owner:
                    raise PermissionError("predicate_owner_mismatch")
                raise RuntimeError("predicate_already_consumed")
            conn.execute(
                """
                INSERT INTO predicate_claims(
                    generation_id,predicate_id,owner,source_identity,evidence_json,claimed_at
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    generation_id,
                    predicate_id,
                    owner,
                    source_identity,
                    _json_dump(dict(evidence)),
                    now_text,
                ),
            )
            receipt = {
                "schemaVersion": PREDICATE_CLAIM_SCHEMA,
                "ok": True,
                "status": "claimed",
                "generation_id": generation_id,
                "predicate_id": predicate_id,
                "owner": owner,
                "source_identity": source_identity,
                "evidence": dict(evidence),
                "claimed_at": now_text,
            }
            conn.commit()
            return receipt


def record_timing_event(
    store: DirectRunStore,
    *,
    run_id: str,
    writer_lease: str,
    event_kind: str,
    started_at: str | datetime,
    ended_at: str | datetime | None = None,
    elapsed_seconds: int | float | None = None,
    evidence: Mapping[str, Any] | None = None,
    fencing_token: int | None = None,
) -> dict[str, Any]:
    """分類済みの実行時間イベントをappendする。"""

    if event_kind not in TIMING_EVENT_KINDS:
        raise ValueError("timing_event_kind_invalid")
    _require_fencing_token(store, fencing_token)
    start_value = _iso(started_at if isinstance(started_at, datetime) else (_parse_time(started_at) or datetime.fromisoformat(started_at)))
    end_value = ""
    if ended_at is not None:
        end_value = _iso(ended_at if isinstance(ended_at, datetime) else (_parse_time(ended_at) or datetime.fromisoformat(ended_at)))
    if elapsed_seconds is not None:
        if isinstance(elapsed_seconds, bool):
            raise ValueError("timing_elapsed_invalid")
        try:
            elapsed_value = float(elapsed_seconds)
        except (TypeError, ValueError) as exc:
            raise ValueError("timing_elapsed_invalid") from exc
        if not math.isfinite(elapsed_value) or elapsed_value < 0:
            raise ValueError("timing_elapsed_invalid")
    else:
        elapsed_value = None
    store.ensure_runtime_schema()
    with closing(store.connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = store._run_row(conn, run_id)
        _verify_writer(row, writer_lease, store.now(), fencing_token=fencing_token)
        event_id = _append_timing_event_in_tx(
            conn,
            run_id=run_id,
            event_kind=event_kind,
            started_at=start_value,
            ended_at=end_value,
            evidence=evidence,
        )
        conn.commit()
    return {
        "schemaVersion": "NEWS_GRASP_TIMING_EVENT_V1",
        "ok": True,
        "status": "recorded",
        "event_id": event_id,
        "run_id": run_id,
        "event_kind": event_kind,
        "started_at": start_value,
        "ended_at": end_value,
        "elapsed_seconds": elapsed_value,
        "evidence": dict(evidence or {}),
    }


def freeze_completion_elapsed(
    store: DirectRunStore,
    *,
    run_id: str,
    writer_lease: str,
    elapsed_seconds: int | float,
    fencing_token: int | None = None,
) -> dict[str, Any]:
    """completion elapsedを最初の値へ一度だけfreezeする。"""

    # productionではcompletion elapsedのauthorityはfinalize_public_completion
    # だけである。任意callerが先に値を書けると、scheduler T0からの実測を
    # 上書きして45分SLOと完了証跡を偽装できるため、DBへ触れる前に拒否する。
    if not store.test_only_allow_semantic_verifier:
        raise PermissionError("completion_elapsed_finalizer_only")
    if isinstance(elapsed_seconds, bool):
        raise ValueError("elapsed_seconds_invalid")
    _require_fencing_token(store, fencing_token)
    try:
        requested = float(elapsed_seconds)
    except (TypeError, ValueError) as exc:
        raise ValueError("elapsed_seconds_invalid") from exc
    if not math.isfinite(requested) or requested < 0:
        raise ValueError("elapsed_seconds_invalid")
    store.ensure_runtime_schema()
    now_text = _iso(store.now())
    with closing(store.connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = store._run_row(conn, run_id)
        _verify_writer(row, writer_lease, store.now(), fencing_token=fencing_token)
        current = row["completion_elapsed_seconds"]
        if current is None:
            conn.execute(
                """
                UPDATE runs
                SET completion_elapsed_seconds=?, completion_elapsed_at=?, updated_at=?
                WHERE run_id=? AND writer_lease=? AND completion_elapsed_seconds IS NULL
                """,
                (requested, now_text, now_text, run_id, writer_lease),
            )
            current = requested
        else:
            current = float(current)
        conn.commit()
    return {
        "schemaVersion": "NEWS_GRASP_COMPLETION_ELAPSED_V1",
        "ok": True,
        "status": "frozen",
        "run_id": run_id,
        "elapsed_seconds": current,
        "frozen": True,
    }


def admit_daily_operation(
    store: DirectRunStore,
    *,
    run_id: str,
    writer_lease: str,
    operation_id: str,
    fencing_token: int | None = None,
) -> dict[str, Any]:
    """Daily operationを実行前にSLO dispatcherへ通す。

    45/75/90分の分岐を表示だけにせずadmissionへ接続する。ただし六つの
    operationはすべて公開必須であるため、method_change/scope_reduceでも
    operation自体は削らず、高コスト診断・再試行の禁止だけをreceiptへ残す。
    """

    if operation_id not in DAILY_OPERATION_ORDER:
        raise ValueError("daily_operation_unknown")
    _require_fencing_token(store, fencing_token)
    store.ensure_runtime_schema()
    now = store.now()
    now_text = _iso(now)
    with closing(store.connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = store._run_row(conn, run_id)
        _verify_writer(row, writer_lease, now, allowed_statuses={"active", "executing", "finalizing"}, fencing_token=fencing_token)
        lease_until = _iso(now + store.lease_ttl)
        renewed = conn.execute(
            "UPDATE runs SET lease_until=?,updated_at=? "
            "WHERE run_id=? AND writer_lease=? AND fencing_token=? AND lease_until=?",
            (
                lease_until, now_text, run_id, writer_lease,
                int(fencing_token or 0), str(row["lease_until"]),
            ),
        ).rowcount
        if renewed != 1:
            conn.rollback()
            raise PermissionError("daily_operation_lease_renew_cas_conflict")
        t0 = _parse_time(str(_row_value(row, "scheduler_trigger_at", ""))) or _parse_time(str(row["started_at"])) or now
        elapsed_seconds = max(0.0, (now - t0).total_seconds())
        dispatch = slo_dispatch(elapsed_seconds=elapsed_seconds)
        for checkpoint in (45, 75, 90):
            if elapsed_seconds >= checkpoint * 60:
                conn.execute(
                    "INSERT OR IGNORE INTO runtime_checkpoints(run_id,checkpoint_minute,elapsed_minutes,recorded_at) VALUES(?,?,?,?)",
                    (run_id, checkpoint, elapsed_seconds / 60.0, now_text),
                )
        conn.commit()
    return {
        "schemaVersion": "NEWS_GRASP_DAILY_OPERATION_ADMISSION_V1",
        "ok": True,
        "status": "admitted",
        "run_id": run_id,
        "operation_id": operation_id,
        "elapsed_seconds": elapsed_seconds,
        "elapsed_minutes": elapsed_seconds / 60.0,
        "dispatch": dispatch["dispatch"],
        "time_band": dispatch["time_band"],
        "method_change": dispatch["dispatch"] == "method_change",
        "scope_reduce": dispatch["dispatch"] == "scope_reduce",
        "deadline_revision": dispatch["dispatch"] == "deadline_revision",
        "high_cost_capability_frozen": dispatch["dispatch"] in {"scope_reduce", "deadline_revision"},
        "retry_allowed": dispatch["dispatch"] not in {"deadline_revision"},
        "new_generation_allowed": dispatch["dispatch"] not in {"deadline_revision"},
        "required_inventory_preserved": True,
        "consumer_verifier_preserved": True,
    }


def renew_daily_writer_lease(
    store: DirectRunStore,
    *,
    run_id: str,
    writer_lease: str,
    fencing_token: int,
) -> dict[str, Any]:
    """長時間operation中の生存ownerだけがleaseをCAS更新する。"""

    _require_fencing_token(store, fencing_token)
    now = store.now()
    now_text = _iso(now)
    with closing(store.connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = store._run_row(conn, run_id)
        _verify_writer(
            row,
            writer_lease,
            now,
            allowed_statuses={"active", "executing", "finalizing"},
            fencing_token=fencing_token,
        )
        lease_until = _iso(now + store.lease_ttl)
        changed = conn.execute(
            "UPDATE runs SET lease_until=?,updated_at=? "
            "WHERE run_id=? AND writer_lease=? AND fencing_token=? AND lease_until=?",
            (
                lease_until, now_text, run_id, writer_lease,
                int(fencing_token), str(row["lease_until"]),
            ),
        ).rowcount
        if changed != 1:
            conn.rollback()
            raise PermissionError("daily_writer_heartbeat_cas_conflict")
        conn.commit()
    return {
        "schemaVersion": "NEWS_GRASP_DAILY_WRITER_HEARTBEAT_V1",
        "ok": True,
        "status": "renewed",
        "run_id": run_id,
        "lease_until": lease_until,
    }


def _canonical_child_mapping(
    child_result: Mapping[str, Any],
    *,
    expected_schema: str,
    expected_input_hash: str,
    expected_stage_id: str | None = None,
) -> dict[str, Any]:
    canonical = _canonicalize_child_value(child_result)
    if not isinstance(canonical, Mapping):
        raise ValueError("child_result_object_required")
    if canonical.get("schema_version") != expected_schema:
        raise ValueError("child_result_schema_mismatch")
    if canonical.get("input_hash") != expected_input_hash:
        raise ValueError("child_result_input_hash_mismatch")
    if "ok" in canonical and canonical.get("ok") is not True:
        raise ValueError("child_result_ok_false")
    status = str(canonical.get("status") or "").casefold()
    if status in {"", "red", "failed", "blocked", "error", "invalid", "not_executed"}:
        raise ValueError("child_result_status_red")
    if expected_stage_id:
        observed_stage = str(canonical.get("stage_id") or "")
        if observed_stage and observed_stage != expected_stage_id:
            raise ValueError("child_result_stage_mismatch")
    output_hash = str(canonical.get("output_hash") or "").strip()
    if output_hash:
        without_hash = dict(canonical)
        without_hash.pop("output_hash", None)
        actual_hash = hashlib.sha256(_json_dump(without_hash).encode("utf-8")).hexdigest()
        if output_hash != actual_hash:
            raise ValueError("child_result_output_hash_mismatch")
    return dict(canonical)


def apply_stage_result_atomic(
    store: DirectRunStore,
    *,
    run_id: str,
    writer_lease: str,
    stage_id: str,
    child_result: Mapping[str, Any] | bytes | str,
    expected_input_hash: str,
    operation_id: str | None = None,
    expected_schema: str = CHILD_RESULT_SCHEMA,
    fencing_token: int | None = None,
) -> dict[str, Any]:
    """child結果とstage state、applied receiptを一つのtransactionで適用する。"""

    if isinstance(child_result, Mapping) and not store.test_only_allow_semantic_verifier:
        raise ValueError("child_result_mapping_test_only")
    if not isinstance(expected_input_hash, str) or not expected_input_hash:
        raise ValueError("child_result_input_hash_expected_missing")
    _require_fencing_token(store, fencing_token)
    if isinstance(child_result, Mapping):
        canonical = _canonical_child_mapping(
            child_result,
            expected_schema=expected_schema,
            expected_input_hash=expected_input_hash,
            expected_stage_id=stage_id,
        )
    else:
        parsed = parse_child_result(
            child_result,
            expected_schema=expected_schema,
            expected_input_hash=expected_input_hash,
        )
        if parsed.get("ok") is not True:
            raise ValueError(str(parsed.get("reason_code") or "child_result_invalid"))
        canonical = dict(parsed["child_result"])
    if not isinstance(stage_id, str) or stage_id not in DIRECT_STAGES:
        raise ValueError("stage_id_invalid")
    store.ensure_runtime_schema()
    effective_operation_id = str(operation_id or f"{run_id}:{stage_id}:{expected_input_hash}")
    if not effective_operation_id.strip():
        raise ValueError("operation_id_invalid")
    payload = {
        "run_id": run_id,
        "stage_id": stage_id,
        "input_hash": expected_input_hash,
        "child_result": canonical,
    }
    payload_json = _json_dump(payload)
    now_text = _iso(store.now())
    with closing(store.connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT payload_json,receipt_json,run_id FROM applied_receipts WHERE operation_id=?",
            (effective_operation_id,),
        ).fetchone()
        if existing is not None:
            stored_payload = str(existing[0])
            if str(existing[2]) == run_id and stored_payload == payload_json:
                receipt = _json_load(str(existing[1]), {})
                conn.rollback()
                if not isinstance(receipt, dict):
                    raise RuntimeError("applied_receipt_invalid")
                return receipt
            conn.rollback()
            raise RuntimeError("idempotency_conflict")
        row = store._run_row(conn, run_id)
        _verify_writer(row, writer_lease, store.now(), fencing_token=fencing_token)
        stage_index = DIRECT_STAGES.index(stage_id)
        current_index = int(row["current_stage_index"])
        # stateの現在successorだけを一回で適用する。先行・先読みstageの
        # receiptを保存して後から穴埋めする経路は out-of-order 完了を隠すため禁止。
        if stage_index != current_index:
            conn.rollback()
            raise RuntimeError("stage order successor violation")
        stage_status = str(canonical.get("status") or "verified")
        if stage_index == current_index:
            existing_stage = conn.execute(
                "SELECT 1 FROM stages WHERE run_id=? AND stage_index=?",
                (run_id, stage_index),
            ).fetchone()
            if existing_stage is not None:
                conn.rollback()
                raise RuntimeError("stage_already_applied")
            conn.execute(
                """
                INSERT INTO stages(
                    run_id,stage_index,stage_id,status,started_at,completed_at,evidence_json
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (run_id, stage_index, stage_id, stage_status, now_text, now_text, _json_dump(canonical)),
            )
        if stage_index == current_index:
            next_index = min(current_index + 1, len(DIRECT_STAGES))
            next_stage = _stage_for_index(next_index)
            conn.execute(
                "UPDATE runs SET current_stage_index=?, exact_successor=?, updated_at=? WHERE run_id=?",
                (next_index, next_stage, now_text, run_id),
            )
        if canonical.get("external_started") is True or canonical.get("external_operation_id"):
            conn.execute(
                "UPDATE runs SET external_started_at=COALESCE(NULLIF(external_started_at,''),?), updated_at=? WHERE run_id=?",
                (now_text, now_text, run_id),
            )
        receipt = {
            "schemaVersion": APPLIED_RECEIPT_SCHEMA,
            "ok": True,
            "status": "applied",
            "operation_id": effective_operation_id,
            "operationId": effective_operation_id,
            "run_id": run_id,
            "stage_id": stage_id,
            "stageId": stage_id,
            "input_hash": expected_input_hash,
            "inputHash": expected_input_hash,
            "child_result": canonical,
            "applied_at": now_text,
            "state": {
                "current_stage_index": int(next_index if stage_index == current_index else current_index),
                "current_stage": _stage_for_index(next_index if stage_index == current_index else current_index),
            },
        }
        conn.execute(
            """
            INSERT INTO applied_receipts(
                operation_id,run_id,stage_id,input_hash,payload_json,receipt_json,applied_at
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (
                effective_operation_id,
                run_id,
                stage_id,
                expected_input_hash,
                payload_json,
                _json_dump(receipt),
                now_text,
            ),
        )
        conn.commit()
        return receipt


def get_applied_receipt(
    store: DirectRunStore,
    *,
    run_id: str,
    operation_id: str,
) -> dict[str, Any] | None:
    """同一operation_idの適用receiptだけをretry authorityとして返す。"""

    with closing(store.connect()) as conn:
        row = conn.execute(
            "SELECT run_id,receipt_json FROM applied_receipts WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
    if row is None:
        return None
    if str(row[0]) != run_id:
        raise ValueError("applied_receipt_run_mismatch")
    receipt = _json_load(str(row[1]), {})
    if not isinstance(receipt, dict):
        raise RuntimeError("applied_receipt_invalid")
    return receipt


def get_active_run(
    store: DirectRunStore,
    *,
    automation_id: str = AUTOMATION_ID,
    issue_date: str,
    run_intent: str = RUN_INTENT,
    include_writer: bool = False,
) -> dict[str, Any] | None:
    """canonical identityのactive runを観測投影として取得する。

    leaseを必要とする内部writerだけが``include_writer=True``を明示する。
    """

    issue = _validate_issue_date(issue_date)
    # V1 DBを読み取る前に明示migrationを完了させる。これをcaller側の
    # get_active_run順序へ任せると、旧schemaのactive rowを誤ってwinner扱いする。
    store.ensure_runtime_schema()
    with closing(store.connect()) as conn:
        row = store._latest_for_identity(
            conn,
            automation_id=automation_id,
            issue_date=issue,
            run_intent=str(run_intent or ""),
        )
        if row is None or row["status"] not in {"active", "executing", "finalizing"}:
            return None
        projection = _projection_from_row(store, conn, row)
        if not include_writer:
            projection.pop("writer_lease", None)
            projection.pop("fencing_token", None)
            start_seal = projection.get("start_seal")
            if isinstance(start_seal, Mapping):
                projection["start_seal"] = {
                    key: value
                    for key, value in start_seal.items()
                    if key not in {"fencingToken", "writerLease"}
                }
        return projection


def claim_daily_operation(
    store: DirectRunStore,
    *,
    run_id: str,
    writer_lease: str,
    operation_id: str,
    input_hash: str,
    handler_id: str,
    fencing_token: int | None = None,
) -> dict[str, Any]:
    """handler起動前にDaily operationを一度だけclaimする。

    claimは``BEGIN IMMEDIATE``内で行い、同じrun/operationを並列workerが
    二重実行できないようにする。既存claimは再実行許可ではなく、callerが
    既存receiptをattachすべき状態として返す。
    """

    if operation_id not in DAILY_OPERATION_ORDER:
        raise ValueError("daily_operation_unknown")
    if not isinstance(input_hash, str) or not input_hash.strip():
        raise ValueError("daily_operation_input_hash_invalid")
    if not isinstance(handler_id, str) or not handler_id.strip():
        raise ValueError("daily_operation_handler_id_invalid")
    _require_fencing_token(store, fencing_token)
    store.ensure_runtime_schema()
    operation_index = DAILY_OPERATION_ORDER.index(operation_id)
    now_text = _iso(store.now())
    with closing(store.connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = store._run_row(conn, run_id)
        _verify_writer(row, writer_lease, store.now(), fencing_token=fencing_token)
        existing_receipt = conn.execute(
            "SELECT receipt_json FROM daily_operation_receipts WHERE run_id=? AND operation_id=?",
            (run_id, operation_id),
        ).fetchone()
        if existing_receipt is not None:
            receipt = _json_load(str(existing_receipt[0]), {})
            if (
                not isinstance(receipt, Mapping)
                or str(receipt.get("input_hash") or "") != input_hash
                or str(receipt.get("handler_id") or "") != handler_id
            ):
                conn.rollback()
                raise RuntimeError("daily_operation_receipt_binding_conflict")
            conn.rollback()
            return {
                "schemaVersion": "NEWS_GRASP_DAILY_OPERATION_CLAIM_V1",
                "ok": True,
                "status": "completed",
                "run_id": run_id,
                "operation_id": operation_id,
                "input_hash": input_hash,
                "handler_id": handler_id,
                "fencing_token": int(_row_value(row, "fencing_token", 0) or 0),
                "receipt": receipt if isinstance(receipt, Mapping) else {},
                "attach_only": True,
            }
        prior_rows = conn.execute(
            "SELECT operation_index FROM daily_operation_receipts WHERE run_id=? ORDER BY operation_index",
            (run_id,),
        ).fetchall()
        prior_indices = [int(item[0]) for item in prior_rows]
        if prior_indices != list(range(operation_index)):
            conn.rollback()
            raise RuntimeError("daily_operation_order_violation")
        existing = conn.execute(
            "SELECT input_hash,handler_id,fencing_token,status,claimed_at FROM daily_operation_claims WHERE run_id=? AND operation_id=?",
            (run_id, operation_id),
        ).fetchone()
        if existing is not None:
            if str(existing[0]) != input_hash or str(existing[1]) != handler_id:
                conn.rollback()
                raise RuntimeError("daily_operation_claim_idempotency_conflict")
            existing_status = str(existing[3] or "")
            token = int(fencing_token if fencing_token is not None else _row_value(row, "fencing_token", 0) or 0)
            if existing_status == "recoverable" and int(existing[2] or 0) == token:
                timing_event_id = _append_timing_event_in_tx(
                    conn,
                    run_id=run_id,
                    event_kind="retry",
                    started_at=now_text,
                    evidence={
                        "phase": "daily_operation",
                        "operation_id": operation_id,
                        "operation_index": operation_index,
                        "event": "operation_resume",
                    },
                )
                changed = conn.execute(
                    "UPDATE daily_operation_claims SET status='claimed',claimed_at=?,completed_at='' "
                    "WHERE run_id=? AND operation_id=? AND input_hash=? AND handler_id=? "
                    "AND fencing_token=? AND status='recoverable'",
                    (now_text, run_id, operation_id, input_hash, handler_id, token),
                ).rowcount
                if changed != 1:
                    conn.rollback()
                    raise PermissionError("daily_operation_claim_resume_cas_conflict")
                conn.commit()
                return {
                    "schemaVersion": "NEWS_GRASP_DAILY_OPERATION_CLAIM_V1",
                    "ok": True,
                    "status": "claimed",
                    "run_id": run_id,
                    "operation_id": operation_id,
                    "input_hash": input_hash,
                    "handler_id": handler_id,
                    "fencing_token": token,
                    "claimed_at": now_text,
                    "timing_event_id": timing_event_id,
                    "attach_only": False,
                    "resumed": True,
                }
            conn.rollback()
            return {
                "schemaVersion": "NEWS_GRASP_DAILY_OPERATION_CLAIM_V1",
                "ok": False,
                "status": "already_claimed",
                "run_id": run_id,
                "operation_id": operation_id,
                "input_hash": input_hash,
                "handler_id": handler_id,
                "fencing_token": int(existing[2] or 0),
                "claimed_at": str(existing[4] or ""),
                "attach_only": True,
            }
        token = int(fencing_token if fencing_token is not None else _row_value(row, "fencing_token", 0) or 0)
        if token <= 0:
            conn.rollback()
            raise PermissionError("fencing_token_required")
        timing_event_id = _append_timing_event_in_tx(
            conn,
            run_id=run_id,
            event_kind="internal_processing",
            started_at=now_text,
            evidence={
                "phase": "daily_operation",
                "operation_id": operation_id,
                "operation_index": operation_index,
                "event": "operation_start",
            },
        )
        conn.execute(
            """
            INSERT INTO daily_operation_claims(
                run_id,operation_id,operation_index,input_hash,handler_id,
                fencing_token,status,claimed_at
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (run_id, operation_id, operation_index, input_hash, handler_id, token, "claimed", now_text),
        )
        conn.commit()
    return {
        "schemaVersion": "NEWS_GRASP_DAILY_OPERATION_CLAIM_V1",
        "ok": True,
        "status": "claimed",
        "run_id": run_id,
        "operation_id": operation_id,
        "input_hash": input_hash,
        "handler_id": handler_id,
        "fencing_token": token,
        "claimed_at": now_text,
        "timing_event_id": timing_event_id,
        "attach_only": False,
    }


def release_daily_operation_claim(
    store: DirectRunStore,
    *,
    run_id: str,
    writer_lease: str,
    operation_id: str,
    input_hash: str,
    handler_id: str,
    reason: str,
    fencing_token: int | None = None,
) -> dict[str, Any]:
    """Redになったclaimを同一writerの有限recoveryだけへ戻す。"""

    if operation_id not in DAILY_OPERATION_ORDER:
        raise ValueError("daily_operation_unknown")
    if not reason.strip():
        raise ValueError("daily_operation_recovery_reason_missing")
    _require_fencing_token(store, fencing_token)
    store.ensure_runtime_schema()
    now_text = _iso(store.now())
    with closing(store.connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = store._run_row(conn, run_id)
        _verify_writer(row, writer_lease, store.now(), fencing_token=fencing_token)
        token = int(fencing_token if fencing_token is not None else _row_value(row, "fencing_token", 0) or 0)
        changed = conn.execute(
            "UPDATE daily_operation_claims SET status='recoverable',completed_at=? "
            "WHERE run_id=? AND operation_id=? AND input_hash=? AND handler_id=? "
            "AND fencing_token=? AND status='claimed'",
            (now_text, run_id, operation_id, input_hash, handler_id, token),
        ).rowcount
        if changed != 1:
            conn.rollback()
            raise PermissionError("daily_operation_claim_release_cas_conflict")
        timing_event_id = _close_open_timing_event(
            conn,
            run_id=run_id,
            ended_at=now_text,
            evidence={
                "phase": "daily_operation",
                "operation_id": operation_id,
                "event": "operation_red",
                "status": "recoverable",
                "reason": reason,
            },
        )
        conn.commit()
    return {
        "schemaVersion": "NEWS_GRASP_DAILY_OPERATION_CLAIM_RELEASE_V1",
        "ok": True,
        "status": "recoverable",
        "run_id": run_id,
        "operation_id": operation_id,
        "fencing_token": token,
        "timing_event_id": timing_event_id,
        "reason": reason,
    }


def apply_daily_operation_atomic(
    store: DirectRunStore,
    *,
    run_id: str,
    writer_lease: str,
    operation_id: str,
    input_hash: str,
    handler_id: str,
    producer_receipt: Mapping[str, Any],
    fencing_token: int | None = None,
) -> dict[str, Any]:
    """Daily operationのproducer receiptと進捗を同一SQLite transactionへ適用する。"""

    if operation_id not in DAILY_OPERATION_ORDER:
        raise ValueError("daily_operation_unknown")
    if not isinstance(input_hash, str) or not input_hash.strip():
        raise ValueError("daily_operation_input_hash_invalid")
    if not isinstance(handler_id, str) or not handler_id.strip():
        raise ValueError("daily_operation_handler_id_invalid")
    if not isinstance(producer_receipt, Mapping):
        raise ValueError("daily_operation_producer_receipt_missing")
    if producer_receipt.get("ok") is not True:
        raise ValueError("daily_operation_producer_receipt_red")
    producer_status = str(
        producer_receipt.get("status") or producer_receipt.get("result") or ""
    ).casefold()
    if producer_status in {"", "red", "failed", "blocked", "missing", "not_executed"}:
        raise ValueError("daily_operation_producer_receipt_invalid")
    _require_fencing_token(store, fencing_token)
    store.ensure_runtime_schema()
    operation_index = DAILY_OPERATION_ORDER.index(operation_id)
    payload = {
        "run_id": run_id,
        "operation_id": operation_id,
        "operation_index": operation_index,
        "input_hash": input_hash,
        "handler_id": handler_id,
        "producer_receipt": dict(producer_receipt),
    }
    payload_json = _json_dump(payload)
    now_text = _iso(store.now())
    with closing(store.connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            """
            SELECT payload_json,receipt_json
            FROM daily_operation_receipts
            WHERE run_id=? AND operation_id=?
            """,
            (run_id, operation_id),
        ).fetchone()
        if existing is not None:
            if str(existing[0]) == payload_json:
                receipt = _json_load(str(existing[1]), {})
                conn.rollback()
                if not isinstance(receipt, dict):
                    raise RuntimeError("daily_operation_receipt_invalid")
                return receipt
            conn.rollback()
            raise RuntimeError("idempotency_conflict")
        row = store._run_row(conn, run_id)
        _verify_writer(row, writer_lease, store.now(), fencing_token=fencing_token)
        token = int(fencing_token if fencing_token is not None else _row_value(row, "fencing_token", 0) or 0)
        if token <= 0:
            conn.rollback()
            raise PermissionError("fencing_token_required")
        claim_row = conn.execute(
            """
            SELECT input_hash,handler_id,fencing_token,status
            FROM daily_operation_claims
            WHERE run_id=? AND operation_id=?
            """,
            (run_id, operation_id),
        ).fetchone()
        if claim_row is None:
            conn.rollback()
            raise PermissionError("daily_operation_claim_required")
        if (
            str(claim_row[0]) != input_hash
            or str(claim_row[1]) != handler_id
            or int(claim_row[2] or 0) != token
            or str(claim_row[3]) != "claimed"
        ):
            conn.rollback()
            raise PermissionError("daily_operation_claim_fenced")
        prior_rows = conn.execute(
            """
            SELECT operation_id,operation_index
            FROM daily_operation_receipts WHERE run_id=? ORDER BY operation_index
            """,
            (run_id,),
        ).fetchall()
        prior_indices = [int(item[1]) for item in prior_rows]
        expected_prior = list(range(operation_index))
        if prior_indices != expected_prior:
            conn.rollback()
            raise RuntimeError("daily_operation_order_violation")
        receipt = {
            "schemaVersion": "NEWS_GRASP_DAILY_OPERATION_RECEIPT_V1",
            "ok": True,
            "status": "completed",
            "run_id": run_id,
            "operation_id": operation_id,
            "operation_index": operation_index,
            "input_hash": input_hash,
            "handler_id": handler_id,
            "producer_receipt": dict(producer_receipt),
            "applied_at": now_text,
            "sequence": {
                "completed_before": [item[0] for item in prior_rows],
                "next_operation": (
                    DAILY_OPERATION_ORDER[operation_index + 1]
                    if operation_index + 1 < len(DAILY_OPERATION_ORDER)
                    else ""
                ),
            },
        }
        receipt_hash = hashlib.sha256(_json_dump(receipt).encode("utf-8")).hexdigest()
        closed_timing_event_id = _close_open_timing_event(
            conn,
            run_id=run_id,
            ended_at=now_text,
            evidence={
                "phase": "daily_operation",
                "operation_id": operation_id,
                "event": "operation_end",
                "status": "completed",
            },
        )
        conn.execute(
            """
            INSERT INTO daily_operation_receipts(
                run_id,operation_id,operation_index,input_hash,handler_id,
                payload_json,receipt_json,applied_at,fencing_token,receipt_hash
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                run_id,
                operation_id,
                operation_index,
                input_hash,
                handler_id,
                payload_json,
                _json_dump(receipt),
                now_text,
                token,
                receipt_hash,
            ),
        )
        changed_claim = conn.execute(
            """
            UPDATE daily_operation_claims
            SET status='completed',completed_at=?
            WHERE run_id=? AND operation_id=? AND input_hash=? AND handler_id=?
              AND fencing_token=? AND status='claimed'
            """,
            (now_text, run_id, operation_id, input_hash, handler_id, token),
        ).rowcount
        if changed_claim != 1:
            conn.rollback()
            raise PermissionError("daily_operation_claim_cas_conflict")
        if producer_receipt.get("external_started") is True:
            conn.execute(
                "UPDATE runs SET external_started_at=COALESCE(NULLIF(external_started_at,''),?), updated_at=? WHERE run_id=? AND writer_lease=? AND fencing_token=?",
                (now_text, now_text, run_id, writer_lease, token),
            )
        if operation_id == DAILY_OPERATION_ORDER[-1]:
            # operation receiptの適用は完了判定ではない。public verifierを
            # 通した既存唯一finalizerだけがstatus=completedへ遷移させる。
            changed_run = conn.execute(
                "UPDATE runs SET status='finalizing', exact_successor='public_completion', updated_at=? WHERE run_id=? AND writer_lease=? AND fencing_token=? AND status IN ('active','executing','finalizing')",
                (now_text, run_id, writer_lease, token),
            ).rowcount
            if changed_run != 1:
                conn.rollback()
                raise PermissionError("daily_operation_run_cas_conflict")
        else:
            changed_run = conn.execute(
                "UPDATE runs SET updated_at=? WHERE run_id=? AND writer_lease=? AND fencing_token=?",
                (now_text, run_id, writer_lease, token),
            ).rowcount
            if changed_run != 1:
                conn.rollback()
                raise PermissionError("daily_operation_run_cas_conflict")
        conn.commit()
        receipt["receipt_hash"] = receipt_hash
        receipt["timing_event_id"] = closed_timing_event_id
        return receipt


def get_daily_operation_receipt(
    store: DirectRunStore,
    *,
    run_id: str,
    operation_id: str,
) -> dict[str, Any] | None:
    """Daily operationの既存receiptを返す。"""

    with closing(store.connect()) as conn:
        row = conn.execute(
            """
            SELECT receipt_json FROM daily_operation_receipts
            WHERE run_id=? AND operation_id=?
            """,
            (run_id, operation_id),
        ).fetchone()
    if row is None:
        return None
    receipt = _json_load(str(row[0]), {})
    if not isinstance(receipt, dict):
        raise RuntimeError("daily_operation_receipt_invalid")
    return receipt


def inspect_external_outbox(
    store: DirectRunStore,
    *,
    run_id: str,
) -> list[dict[str, Any]]:
    """consumer verifierへ渡すimmutable external receipt projectionを返す。"""

    store.ensure_runtime_schema()
    with closing(store.connect()) as conn:
        run = store._run_row(conn, run_id)
        rows = conn.execute(
            """
            SELECT logical_operation_id,side_effect_id,status,idempotency_key,
                   fencing_token,provider_ack_status,output_hash,
                   provider_receipt_json,provider_receipt_hash,provider_output_hash,
                   started_at,completed_at
            FROM external_outbox WHERE run_id=? ORDER BY rowid
            """,
            (run_id,),
        ).fetchall()
    projection: list[dict[str, Any]] = []
    for row in rows:
        receipt = _json_load(str(row[7] or "{}"), {})
        payload_identity = str(
            receipt.get("payload_identity") or receipt.get("payloadIdentity") or ""
        ).casefold() if isinstance(receipt, Mapping) else ""
        projection.append(
            {
                "run_id": run_id,
                "issue_date": str(run["issue_date"] or ""),
                "run_intent": str(_row_value(run, "run_intent", "") or ""),
                "operation_id": str(row[0] or ""),
                "side_effect_id": str(row[1] or ""),
                "status": str(row[2] or ""),
                "idempotency_key": str(row[3] or ""),
                "payload_identity": payload_identity,
                "fencing_token": int(row[4] or 0),
                "provider_ack_status": str(row[5] or ""),
                "payload_hash": str(row[6] or ""),
                "provider_receipt": dict(receipt) if isinstance(receipt, Mapping) else {},
                "provider_receipt_hash": str(row[8] or ""),
                "provider_output_hash": str(row[9] or ""),
                "started_at": str(row[10] or ""),
                "completed_at": str(row[11] or ""),
            }
        )
    return projection


def record_external_outbox(
    store: DirectRunStore,
    *,
    run_id: str,
    writer_lease: str,
    operation_id: str,
    side_effect_id: str,
    status: str,
    payload: Mapping[str, Any] | None = None,
    idempotency_key: str | None = None,
    fencing_token: int | None = None,
) -> dict[str, Any]:
    """許可済みexternal side effectをoutboxへ一度だけ記録する。"""

    allowed_statuses = {
        "reserved",
        "started",
        "sent",
        "acknowledged",
        "completed",
        "unknown_delivery",
        "unknown_unobtainable",
    }
    if status not in allowed_statuses:
        raise ValueError("external_outbox_status_invalid")
    if status != "reserved":
        raise ValueError("external_outbox_initial_status_must_be_reserved")
    if not operation_id.strip() or not side_effect_id.strip():
        raise ValueError("external_outbox_identity_invalid")
    _require_fencing_token(store, fencing_token)
    store.ensure_runtime_schema()
    payload_value = dict(payload or {})
    payload_json = _json_dump(payload_value)
    now_text = _iso(store.now())
    with closing(store.connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = store._run_row(conn, run_id)
        _verify_writer(row, writer_lease, store.now(), fencing_token=fencing_token)
        start_seal = _json_load(_row_value(row, "start_seal_json", "{}"), {})
        allowed_ids = {
            str(item)
            for item in (start_seal.get("allowedSideEffectIds") or [])
            if isinstance(item, str)
        }
        if allowed_ids and side_effect_id not in allowed_ids:
            conn.rollback()
            raise PermissionError("external_side_effect_not_allowed")
        existing = conn.execute(
            "SELECT side_effect_id,status,payload_json FROM external_outbox "
            "WHERE run_id=? AND logical_operation_id=?",
            (run_id, operation_id),
        ).fetchone()
        if existing is not None:
            if str(existing[0]) == side_effect_id and str(existing[2]) == payload_json:
                result = {
                    "schemaVersion": "NEWS_GRASP_EXTERNAL_OUTBOX_RECEIPT_V1",
                    "ok": True,
                    "status": str(existing[1]),
                    "operation_id": operation_id,
                    "side_effect_id": side_effect_id,
                    "idempotent": True,
                }
                conn.rollback()
                return result
            conn.rollback()
            raise RuntimeError("idempotency_conflict")
        token = int(fencing_token if fencing_token is not None else _row_value(row, "fencing_token", 0) or 0)
        key = str(idempotency_key or f"{run_id}:{operation_id}:{side_effect_id}").strip()
        if not key:
            conn.rollback()
            raise ValueError("external_idempotency_key_invalid")
        started_at = ""
        completed_at = now_text if status in {"acknowledged", "completed"} else ""
        output_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        physical_operation_id = hashlib.sha256(
            f"{run_id}\0{operation_id}".encode("utf-8")
        ).hexdigest()
        conn.execute(
            """
            INSERT INTO external_outbox(
                operation_id,run_id,logical_operation_id,side_effect_id,status,payload_json,started_at,completed_at,
                idempotency_key,fencing_token,updated_at,output_hash
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (physical_operation_id, run_id, operation_id, side_effect_id, status, payload_json, started_at, completed_at,
             key, token, now_text, output_hash),
        )
        result = {
            "schemaVersion": "NEWS_GRASP_EXTERNAL_OUTBOX_RECEIPT_V1",
            "ok": True,
            "status": status,
            "operation_id": operation_id,
            "side_effect_id": side_effect_id,
            "payload": payload_value,
            "started_at": started_at,
            "completed_at": completed_at,
            "idempotency_key": key,
            "fencing_binding_hash": fencing_binding_hash(
                run_id=run_id,
                generation=int(row["generation"]),
                writer_lease=writer_lease,
                fencing_token=token,
            ),
            "output_hash": output_hash,
            "idempotent": False,
        }
        conn.commit()
        return result


def transition_external_outbox(
    store: DirectRunStore,
    *,
    run_id: str,
    writer_lease: str,
    operation_id: str,
    expected_status: str,
    next_status: str,
    provider_ack_status: str = "",
    fencing_token: int | None = None,
) -> dict[str, Any]:
    """sealed outboxの単調CAS遷移。送信処理自体はこの関数の外で一度だけ行う。"""

    transitions = {
        "reserved": {"started"},
        "started": {"sent", "unknown_delivery", "unknown_unobtainable"},
        "sent": {"acknowledged", "completed", "unknown_delivery", "unknown_unobtainable"},
        "acknowledged": {"completed"},
        "completed": set(),
        "unknown_delivery": set(),
        "unknown_unobtainable": set(),
    }
    if next_status not in transitions.get(expected_status, set()):
        raise ValueError("external_outbox_transition_invalid")
    _require_fencing_token(store, fencing_token)
    store.ensure_runtime_schema()
    now_text = _iso(store.now())
    with closing(store.connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = store._run_row(conn, run_id)
        _verify_writer(row, writer_lease, store.now(), fencing_token=fencing_token)
        current = conn.execute(
            "SELECT side_effect_id,status,idempotency_key,fencing_token,payload_json,output_hash "
            "FROM external_outbox WHERE run_id=? AND logical_operation_id=?",
            (run_id, operation_id),
        ).fetchone()
        if current is None:
            conn.rollback()
            raise ValueError("external_outbox_missing")
        if str(current[1]) != expected_status:
            conn.rollback()
            if str(current[1]) == next_status:
                return {
                    "schemaVersion": "NEWS_GRASP_EXTERNAL_OUTBOX_RECEIPT_V1",
                    "ok": True,
                    "status": next_status,
                    "operation_id": operation_id,
                    "side_effect_id": str(current[0]),
                    "idempotency_key": str(current[2]),
                    "idempotent": True,
                }
            raise PermissionError("external_outbox_cas_conflict")
        token = int(fencing_token if fencing_token is not None else current[3] or 0)
        if token != int(current[3] or 0):
            conn.rollback()
            raise PermissionError("external_outbox_fencing_conflict")
        started_at = now_text if next_status == "started" else ""
        completed_at = now_text if next_status in {"completed", "acknowledged"} else ""
        changed = conn.execute(
            """
            UPDATE external_outbox
            SET status=?,started_at=CASE WHEN ?!='' THEN ? ELSE started_at END,
                completed_at=CASE WHEN ?!='' THEN ? ELSE completed_at END,
                provider_ack_status=?,updated_at=?
            WHERE run_id=? AND logical_operation_id=? AND status=? AND fencing_token=?
            """,
            (next_status, started_at, started_at, completed_at, completed_at,
             str(provider_ack_status or ""), now_text, run_id, operation_id,
             expected_status, token),
        ).rowcount
        if changed != 1:
            conn.rollback()
            raise PermissionError("external_outbox_cas_conflict")
        conn.execute(
            "UPDATE runs SET external_started_at=COALESCE(NULLIF(external_started_at,''),?),updated_at=? WHERE run_id=?",
            (now_text, now_text, run_id),
        )
        conn.commit()
    return {
        "schemaVersion": "NEWS_GRASP_EXTERNAL_OUTBOX_RECEIPT_V1",
        "ok": True,
        "status": next_status,
        "operation_id": operation_id,
        "side_effect_id": str(current[0]),
        "idempotency_key": str(current[2]),
        "provider_ack_status": str(provider_ack_status or ""),
        "idempotent": False,
    }


def start_external_outbox_atomic(
    store: DirectRunStore,
    *,
    run_id: str,
    writer_lease: str,
    operation_id: str,
    event_kind: str,
    started_at: str | datetime,
    evidence: Mapping[str, Any],
    fencing_token: int | None = None,
) -> dict[str, Any]:
    """outbox started CASと外部call開始timingを同一transactionで確定する。"""

    if event_kind not in TIMING_EVENT_KINDS:
        raise ValueError("timing_event_kind_invalid")
    if not isinstance(evidence, Mapping):
        raise ValueError("timing_evidence_invalid")
    _require_fencing_token(store, fencing_token)
    start_value = _iso(
        started_at
        if isinstance(started_at, datetime)
        else (_parse_time(started_at) or datetime.fromisoformat(started_at))
    )
    store.ensure_runtime_schema()
    with closing(store.connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = store._run_row(conn, run_id)
        _verify_writer(row, writer_lease, store.now(), fencing_token=fencing_token)
        current = conn.execute(
            "SELECT side_effect_id,status,idempotency_key,fencing_token "
            "FROM external_outbox WHERE run_id=? AND logical_operation_id=?",
            (run_id, operation_id),
        ).fetchone()
        if current is None:
            conn.rollback()
            raise ValueError("external_outbox_missing")
        token = int(fencing_token if fencing_token is not None else current[3] or 0)
        if token != int(current[3] or 0):
            conn.rollback()
            raise PermissionError("external_outbox_fencing_conflict")
        if str(current[1]) != "reserved":
            conn.rollback()
            raise PermissionError("external_outbox_cas_conflict")
        changed = conn.execute(
            """
            UPDATE external_outbox
            SET status='started',started_at=?,updated_at=?
            WHERE run_id=? AND logical_operation_id=?
              AND status='reserved' AND fencing_token=?
            """,
            (start_value, start_value, run_id, operation_id, token),
        ).rowcount
        if changed != 1:
            conn.rollback()
            raise PermissionError("external_outbox_cas_conflict")
        event_id = _append_timing_event_in_tx(
            conn,
            run_id=run_id,
            event_kind=event_kind,
            started_at=start_value,
            evidence=evidence,
        )
        conn.execute(
            "UPDATE runs SET external_started_at=COALESCE(NULLIF(external_started_at,''),?),updated_at=? WHERE run_id=?",
            (start_value, start_value, run_id),
        )
        conn.commit()
    return {
        "schemaVersion": "NEWS_GRASP_EXTERNAL_OUTBOX_START_RECEIPT_V1",
        "ok": True,
        "status": "started",
        "operation_id": operation_id,
        "side_effect_id": str(current[0]),
        "idempotency_key": str(current[2]),
        "timing_event_id": event_id,
        "started_at": start_value,
    }


def complete_external_outbox_atomic(
    store: DirectRunStore,
    *,
    run_id: str,
    writer_lease: str,
    operation_id: str,
    provider_receipt: Mapping[str, Any],
    provider_ack_status: str,
    reconciled: bool = False,
    fencing_token: int | None = None,
) -> dict[str, Any]:
    """validated provider receiptとcompleted遷移を同一transactionへ保存する。"""

    if not isinstance(provider_receipt, Mapping):
        raise ValueError("external_provider_receipt_invalid")
    receipt_value = dict(provider_receipt)
    receipt_json = _json_dump(receipt_value)
    receipt_hash = hashlib.sha256(receipt_json.encode("utf-8")).hexdigest()
    provider_output_hash = str(
        _mapping_first(receipt_value, "output_hash", "outputHash", default="") or ""
    ).casefold()
    if re.fullmatch(r"[0-9a-f]{64}", provider_output_hash) is None:
        raise ValueError("external_provider_output_hash_invalid")
    _require_fencing_token(store, fencing_token)
    store.ensure_runtime_schema()
    now_text = _iso(store.now())
    with closing(store.connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = store._run_row(conn, run_id)
        _verify_writer(row, writer_lease, store.now(), fencing_token=fencing_token)
        current = conn.execute(
            "SELECT side_effect_id,status,idempotency_key,fencing_token,provider_receipt_json,provider_receipt_hash,provider_output_hash "
            "FROM external_outbox WHERE run_id=? AND logical_operation_id=?",
            (run_id, operation_id),
        ).fetchone()
        if current is None:
            conn.rollback()
            raise ValueError("external_outbox_missing")
        token = int(fencing_token if fencing_token is not None else current[3] or 0)
        if token != int(current[3] or 0):
            conn.rollback()
            raise PermissionError("external_outbox_fencing_conflict")
        if str(current[1]) == "completed":
            if (
                str(current[4] or "{}") == receipt_json
                and str(current[5] or "") == receipt_hash
                and str(current[6] or "") == provider_output_hash
            ):
                conn.rollback()
                return {
                    "schemaVersion": "NEWS_GRASP_EXTERNAL_OUTBOX_RECEIPT_V2",
                    "ok": True,
                    "status": "completed",
                    "operation_id": operation_id,
                    "side_effect_id": str(current[0]),
                    "idempotency_key": str(current[2]),
                    "provider_receipt_hash": receipt_hash,
                    "provider_output_hash": provider_output_hash,
                    "idempotent": True,
                }
            conn.rollback()
            raise RuntimeError("external_provider_receipt_idempotency_conflict")
        allowed_current = {"started", "unknown_delivery", "unknown_unobtainable"} if reconciled else {"started"}
        if str(current[1]) not in allowed_current:
            conn.rollback()
            raise PermissionError("external_outbox_cas_conflict")
        status_placeholders = ",".join("?" for _ in sorted(allowed_current))
        changed = conn.execute(
            f"""
            UPDATE external_outbox
            SET status='completed',completed_at=?,provider_ack_status=?,updated_at=?,
                provider_receipt_json=?,provider_receipt_hash=?,provider_output_hash=?
            WHERE run_id=? AND logical_operation_id=? AND status IN ({status_placeholders}) AND fencing_token=?
            """,
            (
                now_text,
                str(provider_ack_status or ""),
                now_text,
                receipt_json,
                receipt_hash,
                provider_output_hash,
                run_id,
                operation_id,
                *sorted(allowed_current),
                token,
            ),
        ).rowcount
        if changed != 1:
            conn.rollback()
            raise PermissionError("external_outbox_cas_conflict")
        conn.execute(
            "UPDATE runs SET external_started_at=COALESCE(NULLIF(external_started_at,''),?),updated_at=? WHERE run_id=?",
            (now_text, now_text, run_id),
        )
        conn.commit()
    return {
        "schemaVersion": "NEWS_GRASP_EXTERNAL_OUTBOX_RECEIPT_V2",
        "ok": True,
        "status": "completed",
        "operation_id": operation_id,
        "side_effect_id": str(current[0]),
        "idempotency_key": str(current[2]),
        "provider_ack_status": str(provider_ack_status or ""),
        "provider_receipt_hash": receipt_hash,
        "provider_output_hash": provider_output_hash,
        "idempotent": False,
    }


def seal_publish(
    store: DirectRunStore,
    *,
    run_id: str,
    writer_lease: str,
    release_commit_sha: str,
    exact_write_set: Sequence[str],
    file_hashes: Mapping[str, str],
    manifest_id: str,
    bundle_id: str,
    external_operation_ids: Sequence[str] = (),
    external_input_hashes: Mapping[str, str] | None = None,
    fencing_token: int | None = None,
) -> dict[str, Any]:
    """外部公開直前のpublish sealを同一runへ固定する。"""

    _require_fencing_token(store, fencing_token)
    if not isinstance(exact_write_set, Sequence) or isinstance(exact_write_set, (str, bytes, bytearray)) or not exact_write_set:
        raise ValueError("publish_seal_write_set_invalid")
    write_set = [str(item) for item in exact_write_set]
    if len(set(write_set)) != len(write_set) or any(not item or Path(item).is_absolute() or ".." in Path(item).parts for item in write_set):
        raise ValueError("publish_seal_write_set_invalid")
    if not store.test_only_allow_semantic_verifier and "docs/index.html" not in write_set:
        raise ValueError("publish_seal_home_write_required")
    if not isinstance(file_hashes, Mapping):
        raise ValueError("publish_seal_file_hashes_invalid")
    if not re.fullmatch(r"[0-9a-f]{40}", str(release_commit_sha or "").casefold()):
        raise ValueError("publish_seal_release_commit_invalid")
    if set(str(key) for key in file_hashes) != set(write_set) or any(
        re.fullmatch(r"[0-9a-f]{64}", str(value or "").casefold()) is None
        for value in file_hashes.values()
    ):
        raise ValueError("publish_seal_file_hashes_invalid")
    external_inputs = {
        str(key): str(value).casefold()
        for key, value in dict(external_input_hashes or {}).items()
    }
    if any(
        not key or Path(key).is_absolute() or ".." in Path(key).parts
        or re.fullmatch(r"[0-9a-f]{64}", value) is None
        for key, value in external_inputs.items()
    ):
        raise ValueError("publish_seal_external_input_hashes_invalid")
    manifest_value = str(manifest_id or "").strip().casefold()
    if not re.fullmatch(r"[0-9a-f]{64}", manifest_value) or not str(bundle_id or "").strip():
        raise ValueError("publish_seal_binding_invalid")
    seal = {
        "schemaVersion": "NEWS_GRASP_PUBLISH_SEAL_V1",
        "runId": run_id,
        "fencingToken": int(fencing_token or 0),
        "releaseCommitSha": release_commit_sha,
        "exactWriteSet": write_set,
        "fileHashes": dict(file_hashes),
        "manifestId": manifest_value,
        "bundleId": bundle_id,
        "externalOperationIds": list(external_operation_ids),
        "externalInputHashes": external_inputs,
    }
    seal_json = _json_dump(seal)
    now_text = _iso(store.now())
    store.ensure_runtime_schema()
    with closing(store.connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = store._run_row(conn, run_id)
        _verify_writer(row, writer_lease, store.now(), fencing_token=fencing_token)
        if not store.test_only_allow_semantic_verifier:
            issue_date = str(row["issue_date"])
            required_external_inputs = {
                f"build/tts/{issue_date}.mp3",
                f"build/tts/deepdive/{issue_date}.mp3",
                f"build/youtube-podcast/{issue_date}.mp4",
                f"build/youtube-podcast-deepdive/{issue_date}.mp4",
            }
            if set(external_inputs) != required_external_inputs:
                conn.rollback()
                raise ValueError("publish_seal_external_input_set_invalid")
        seal["issueDate"] = str(row["issue_date"] or "")
        seal["runIntent"] = str(_row_value(row, "run_intent", "") or "")
        seal["fencingToken"] = int(
            fencing_token
            if fencing_token is not None
            else _row_value(row, "fencing_token", 0) or 0
        )
        if str(_row_value(row, "external_started_at", "") or "") or store._external_side_effect_state(conn, run_id) != "none":
            conn.rollback()
            raise PermissionError("superseded_after_external_start")
        row_manifest = str(_row_value(row, "manifest_id", "") or "").strip().casefold()
        reservation_id = str(_row_value(row, "manifest_reservation_id", "") or "").strip().casefold()
        if not store.test_only_allow_semantic_verifier:
            if not re.fullmatch(r"[0-9a-f]{64}", reservation_id):
                conn.rollback()
                raise PermissionError("manifest_reservation_missing")
            # reservationはrun開始時にseal済みであり、公開直前に差し替え
            # られない。publish seal自身にも同じ値を持たせることで、
            # DB列・start seal・publish sealの三者を同一identityへ束縛する。
        seal["manifestReservationId"] = reservation_id
        seal_json = _json_dump(seal)
        start_seal = _json_load(_row_value(row, "start_seal_json", "{}"), {})
        start_reservation_id = str(
            _mapping_first(start_seal, "manifestReservationId", "manifest_reservation_id", default="")
            or ""
        ).strip().casefold()
        if not store.test_only_allow_semantic_verifier and start_reservation_id != reservation_id:
            conn.rollback()
            raise PermissionError("manifest_reservation_binding_conflict")
        allowed_ids = {
            str(item)
            for item in (start_seal.get("allowedSideEffectIds") or [])
            if isinstance(item, str)
        }
        if allowed_ids and any(str(item) not in allowed_ids for item in external_operation_ids):
            conn.rollback()
            raise PermissionError("external_side_effect_not_allowed")
        existing = str(_row_value(row, "publish_seal_json", "{}") or "{}")
        if existing not in {"", "{}"}:
            if row_manifest == manifest_value and existing == seal_json:
                conn.rollback()
                return _json_load(existing, seal)
            conn.rollback()
            raise RuntimeError("publish_seal_idempotency_conflict")
        if row_manifest and row_manifest != manifest_value:
            conn.rollback()
            raise RuntimeError("publish_seal_idempotency_conflict")
        if not store.test_only_allow_semantic_verifier and row_manifest:
            # productionのactual manifestはpublish seal以外から設定されては
            # ならない。既存値が残る不整合はrebindせず拒否する。
            conn.rollback()
            raise RuntimeError("publish_seal_idempotency_conflict")
        if store.test_only_allow_semantic_verifier and row_manifest:
            token = int(fencing_token if fencing_token is not None else _row_value(row, "fencing_token", 0) or 0)
            changed = conn.execute(
                "UPDATE runs SET publish_seal_json=?, updated_at=? WHERE run_id=? AND writer_lease=? AND fencing_token=? AND manifest_id=? AND publish_seal_json='{}'",
                (seal_json, now_text, run_id, writer_lease, token, row_manifest),
            ).rowcount
        else:
            # productionの二段目seal。manifest列へのCAS設定とseal JSONの
            # 書込みを同一BEGIN IMMEDIATEへ置き、片側だけのbindを許さない。
            token = int(fencing_token if fencing_token is not None else _row_value(row, "fencing_token", 0) or 0)
            changed = conn.execute(
                "UPDATE runs SET manifest_id=?, publish_seal_json=?, updated_at=? WHERE run_id=? AND writer_lease=? AND fencing_token=? AND manifest_id='' AND publish_seal_json='{}'",
                (manifest_value, seal_json, now_text, run_id, writer_lease, token),
            ).rowcount
        if changed != 1:
            conn.rollback()
            raise PermissionError("publish_seal_cas_conflict")
        conn.commit()
    return seal


record_publish_seal = seal_publish


def relocate_runtime_state_v1(
    *,
    source_state_root: str | Path,
    source_repo_root: str | Path,
    target_state_root: str | Path,
    run_id: str,
    writer_lease: str,
    new_writer_lease: str,
    recovery_authority: str,
) -> dict[str, Any]:
    """worktree内V1 DBを変更せず、外部single state rootへ一貫snapshotする。"""
    raw_source_repo = Path(os.path.abspath(os.fspath(source_repo_root)))
    raw_source_root = Path(os.path.abspath(os.fspath(source_state_root)))
    raw_target_root = Path(os.path.abspath(os.fspath(target_state_root)))
    _reject_reparse_chain(raw_source_repo, reason="runtime_source_reparse_forbidden")
    _reject_reparse_chain(raw_source_root, reason="runtime_source_reparse_forbidden")
    _reject_reparse_chain(raw_target_root, reason="runtime_target_reparse_forbidden")
    source_repo = Path(source_repo_root).resolve(strict=True)
    source_root = Path(source_state_root).resolve(strict=True)
    target_root = Path(target_state_root).resolve(strict=False)
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if not local_app_data:
        raise EnvironmentError("localappdata_missing")
    raw_canonical_target = Path(os.path.abspath(os.fspath(Path(local_app_data) / "News-Grasp" / "direct-mainline")))
    _reject_reparse_chain(raw_canonical_target, reason="runtime_target_reparse_forbidden")
    canonical_target = raw_canonical_target.resolve(strict=False)
    if os.path.normcase(str(raw_target_root)) != os.path.normcase(str(raw_canonical_target)):
        raise ValueError("runtime_target_not_canonical_external_root")
    if not source_root.is_relative_to(source_repo):
        raise ValueError("runtime_source_not_bound_to_repo")
    if target_root == source_repo or target_root.is_relative_to(source_repo):
        raise ValueError("runtime_target_inside_source_repo")
    if recovery_authority != "same_run_append_only_migration":
        raise PermissionError("runtime_relocation_recovery_authority_invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", new_writer_lease) or new_writer_lease == writer_lease:
        raise ValueError("runtime_relocation_new_writer_lease_invalid")
    source_db = source_root / "direct-mainline.sqlite3"
    if not source_db.is_file() or source_db.is_symlink():
        raise ValueError("runtime_source_db_invalid")
    if target_root == source_root or target_root.is_relative_to(source_root):
        raise ValueError("runtime_target_must_be_external")
    target_db = target_root / "direct-mainline.sqlite3"
    target_root.mkdir(parents=True, exist_ok=True)
    _reject_reparse_chain(raw_target_root, reason="runtime_target_reparse_forbidden")
    target_owned = False
    recovery_status = "completed"
    try:
        _reject_reparse_chain(raw_source_root, reason="runtime_source_reparse_forbidden")
        with closing(sqlite3.connect(f"file:{source_db.as_posix()}?mode=ro", uri=True)) as source:
            source.execute("PRAGMA query_only=ON")
            source.execute("BEGIN")
            row = source.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                raise PermissionError("runtime_relocation_run_missing")
            columns = [str(item[1]) for item in source.execute("PRAGMA table_info(runs)").fetchall()]
            bound = dict(zip(columns, row, strict=True))
            if str(bound.get("writer_lease")) != writer_lease or str(bound.get("status")) not in {"active", "executing"}:
                raise PermissionError("runtime_relocation_writer_fenced")
            source_lease_until = _parse_time(str(bound.get("lease_until") or ""))
            if source_lease_until is None:
                raise PermissionError("runtime_relocation_source_lease_invalid")
            if source_lease_until > _now_jst():
                raise PermissionError("runtime_relocation_source_lease_active")
            newer = source.execute("SELECT COUNT(*) FROM runs WHERE automation_id=? AND cwd=? AND issue_date=? AND generation>?", (bound["automation_id"], bound["cwd"], bound["issue_date"], bound["generation"])).fetchone()[0]
            if int(newer) != 0:
                raise PermissionError("runtime_relocation_newer_generation_exists")
            stage_rows = source.execute("SELECT * FROM stages WHERE run_id=? ORDER BY stage_index", (run_id,)).fetchall()
            stage_digest = hashlib.sha256(_json_dump([list(item) for item in stage_rows]).encode("utf-8")).hexdigest()
            recovery_registry = raw_canonical_target.parent / "runtime-recovery.sqlite3"
            _reject_reparse_chain(recovery_registry, reason="runtime_target_reparse_forbidden")
            recovery_nonce = uuid.uuid4().hex
            with closing(sqlite3.connect(recovery_registry)) as registry:
                registry.execute("CREATE TABLE IF NOT EXISTS recoveries(run_id TEXT PRIMARY KEY, source_stage_sha256 TEXT NOT NULL, recovery_nonce TEXT NOT NULL, status TEXT NOT NULL, claimed_at TEXT NOT NULL, completed_at TEXT NOT NULL DEFAULT '')")
                registry.commit()
                if target_db.exists() or target_db.is_symlink():
                    _reject_reparse_chain(target_db, reason="runtime_target_reparse_forbidden")
                    with closing(sqlite3.connect(target_db)) as existing:
                        if existing.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                            raise FileExistsError("runtime_target_existing_integrity_red")
                        copied = existing.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
                        copied_stages = existing.execute("SELECT * FROM stages WHERE run_id=? ORDER BY stage_index", (run_id,)).fetchall()
                    if copied is None:
                        raise FileExistsError("runtime_target_existing_run_unbound")
                    copied_bound = dict(zip(columns, copied, strict=True))
                    copied_stage_digest = hashlib.sha256(_json_dump([list(item) for item in copied_stages]).encode("utf-8")).hexdigest()
                    immutable_columns_match = all(
                        copied_bound.get(name) == bound.get(name)
                        for name in columns
                        if name not in {"writer_lease", "lease_until"}
                    )
                    recovery_row = registry.execute(
                        "SELECT source_stage_sha256,recovery_nonce,status FROM recoveries WHERE run_id=?",
                        (run_id,),
                    ).fetchone()
                    recovery_matches = recovery_row is None or str(recovery_row[0]) == stage_digest
                    if not immutable_columns_match or copied_stage_digest != stage_digest or not recovery_matches:
                        raise FileExistsError("runtime_target_existing_binding_red")
                    if str(copied_bound.get("writer_lease")) == new_writer_lease:
                        registry.execute("BEGIN IMMEDIATE")
                        if recovery_row is None:
                            registry.execute(
                                "INSERT INTO recoveries(run_id,source_stage_sha256,recovery_nonce,status,claimed_at,completed_at) VALUES(?,?,?,?,?,?)",
                                (run_id, stage_digest, recovery_nonce, "completed_adopted", _iso(_now_jst()), _iso(_now_jst())),
                            )
                            recovery_status = "completed_adopted"
                        elif str(recovery_row[2]) == "claimed":
                            changed = registry.execute(
                                "UPDATE recoveries SET status='completed_adopted',completed_at=? WHERE run_id=? AND source_stage_sha256=? AND status='claimed'",
                                (_iso(_now_jst()), run_id, stage_digest),
                            ).rowcount
                            if changed != 1:
                                registry.rollback()
                                raise RuntimeError("runtime_relocation_orphan_adoption_cas_red")
                            recovery_status = "completed_adopted"
                        elif str(recovery_row[2]) not in {"completed", "completed_adopted"}:
                            registry.rollback()
                            raise RuntimeError("runtime_relocation_recovery_status_red")
                        registry.commit()
                        renewed_until = str(copied_bound.get("lease_until") or "")
                        source.rollback()
                        continue_relocation = False
                    elif str(copied_bound.get("writer_lease")) == writer_lease and recovery_row is None:
                        target_db.unlink()
                        continue_relocation = True
                    else:
                        raise FileExistsError("runtime_target_existing_lease_unbound")
                else:
                    continue_relocation = True
                if not continue_relocation:
                    pass
                else:
                    registry.execute("BEGIN IMMEDIATE")
                    try:
                        registry.execute(
                            "INSERT INTO recoveries(run_id,source_stage_sha256,recovery_nonce,status,claimed_at) VALUES(?,?,?,?,?)",
                            (run_id, stage_digest, recovery_nonce, "claimed", _iso(_now_jst())),
                        )
                    except sqlite3.IntegrityError as exc:
                        registry.rollback()
                        raise PermissionError("runtime_relocation_recovery_replay") from exc
                    _reject_reparse_chain(raw_target_root, reason="runtime_target_reparse_forbidden")
                    target_owned = True
                    with closing(sqlite3.connect(target_db)) as target:
                        source.backup(target)
                    source.rollback()
                    with closing(sqlite3.connect(target_db)) as target:
                        if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                            raise RuntimeError("runtime_relocation_integrity_red")
                        copied = target.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
                        copied_stages = target.execute("SELECT * FROM stages WHERE run_id=? ORDER BY stage_index", (run_id,)).fetchall()
                        copied_stage_digest = hashlib.sha256(_json_dump([list(item) for item in copied_stages]).encode("utf-8")).hexdigest()
                        if copied is None or tuple(copied) != tuple(row) or copied_stage_digest != stage_digest:
                            raise RuntimeError("runtime_relocation_binding_red")
                        renewed_until = _iso(_now_jst() + timedelta(hours=2))
                        changed = target.execute("UPDATE runs SET writer_lease=?, lease_until=? WHERE run_id=? AND writer_lease=? AND status IN ('active','executing')", (new_writer_lease, renewed_until, run_id, writer_lease)).rowcount
                        if changed != 1:
                            raise RuntimeError("runtime_relocation_target_renewal_red")
                        target.commit()
                    changed = registry.execute(
                        "UPDATE recoveries SET status='completed',completed_at=? WHERE run_id=? AND recovery_nonce=? AND status='claimed'",
                        (_iso(_now_jst()), run_id, recovery_nonce),
                    ).rowcount
                    if changed != 1:
                        raise RuntimeError("runtime_relocation_recovery_registry_red")
                    registry.commit()
    except Exception:
        if target_owned:
            target_db.unlink(missing_ok=True)
        try:
            target_root.rmdir()
        except OSError:
            pass
        raise
    return {
        "schemaVersion": "NEWS_GRASP_RUNTIME_RELOCATION_RECEIPT_V1",
        "ok": True,
        "status": "verified",
        "runId": run_id,
        "sourcePolicy": "read_only_immutable",
        "sourceStateRoot": str(source_root),
        "targetStateRoot": str(target_root),
        "targetDb": str(target_db),
        "recoveryAuthority": recovery_authority,
        "sourceStageHistorySha256": stage_digest,
        "sourceLeaseUntil": _iso(source_lease_until),
        "sourceLeaseExpiredAtRecovery": source_lease_until <= _now_jst(),
        "targetLeaseRenewedUntil": renewed_until,
        "recoveryStatus": recovery_status,
    }


def _stage_for_index(index: int) -> str:
    if 0 <= index < len(DIRECT_STAGES):
        return DIRECT_STAGES[index]
    return ""


def _projection_from_row(store: DirectRunStore, conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    stage_rows = conn.execute(
        "SELECT * FROM stages WHERE run_id = ? ORDER BY stage_index ASC",
        (row["run_id"],),
    ).fetchall()
    history = [
        {
            "stage": item["stage_id"],
            "status": item["status"],
            "started_at": item["started_at"],
            "completed_at": item["completed_at"],
            "evidence": _json_load(item["evidence_json"], {}),
        }
        for item in stage_rows
    ]
    # 所要時間の起点はworker再起動やgenerationではなくscheduler T0。
    # 旧rowにtriggerがない場合だけstarted_atへ限定的にfallbackし、観測不能を
    # 現在時刻から再計算して過去のelapsedを水増ししない。
    started_at = (
        _parse_time(str(_row_value(row, "scheduler_trigger_at", "")))
        or _parse_time(row["started_at"])
        or store.now()
    )
    frozen_elapsed = _row_value(row, "completion_elapsed_seconds")
    if frozen_elapsed is None:
        elapsed_seconds = max(0.0, (store.now() - started_at).total_seconds())
    else:
        elapsed_seconds = max(0.0, float(frozen_elapsed))
    elapsed = elapsed_seconds / 60.0
    slo = slo_dispatch(elapsed_seconds=elapsed_seconds)
    current_stage = _stage_for_index(int(row["current_stage_index"]))
    title_status = str(row["title_status"] or "")
    title_completion = (
        "fulfilled" if title_status in TITLE_SUCCESS
        else "deferred" if title_status in TITLE_NONBLOCKING
        else "unverified"
    )
    checkpoint_rows = conn.execute(
        "SELECT checkpoint_minute,elapsed_minutes,recorded_at FROM runtime_checkpoints WHERE run_id=? ORDER BY checkpoint_minute",
        (row["run_id"],),
    ).fetchall()
    rebind_rows = conn.execute(
        "SELECT previous_manifest_id,manifest_id,receipt_json,rebound_at FROM runtime_manifest_rebindings WHERE run_id=? ORDER BY id",
        (row["run_id"],),
    ).fetchall()
    try:
        timing_rows = conn.execute(
            """
            SELECT event_kind,started_at,ended_at,elapsed_seconds,evidence_json
            FROM timing_events WHERE run_id=? ORDER BY event_id
            """,
            (row["run_id"],),
        ).fetchall()
    except sqlite3.OperationalError:
        timing_rows = []
    try:
        daily_rows = conn.execute(
            """
            SELECT operation_id,operation_index,input_hash,handler_id,receipt_json,applied_at,fencing_token,receipt_hash
            FROM daily_operation_receipts WHERE run_id=? ORDER BY operation_index
            """,
            (row["run_id"],),
        ).fetchall()
    except sqlite3.OperationalError:
        daily_rows = []
    return {
        "schemaVersion": row["runtime_schema"] or RUNTIME_SCHEMA,
        "legacySchemaVersion": RUNTIME_SCHEMA,
        "mainline_receipt_schema": MAINLINE_RECEIPT_SCHEMA,
        "run_id": row["run_id"],
        "automation_id": row["automation_id"],
        "cwd": row["cwd"],
        "issue_date": row["issue_date"],
        "run_intent": row["run_intent"],
        "manifest_id": row["manifest_id"],
        "manifest_reservation_id": _row_value(row, "manifest_reservation_id", "") or "",
        "migration_receipt": _json_load(row["migration_receipt_json"], {}),
        "observation_receipt": _json_load(row["observation_receipt_json"], {}),
        "manifest_rebindings": [
            {
                "previousManifestId": item["previous_manifest_id"],
                "manifestId": item["manifest_id"],
                "receipt": _json_load(item["receipt_json"], {}),
                "reboundAt": item["rebound_at"],
            }
            for item in rebind_rows
        ],
        "typed_issues": _json_load(row["typed_issues_json"], []),
        "generation": int(row["generation"]),
        "fencing_token": int(_row_value(row, "fencing_token", 0) or 0),
        "writer_lease": row["writer_lease"],
        "status": row["status"],
        "current_stage": current_stage,
        "current_stage_index": int(row["current_stage_index"]),
        "next_stage": current_stage,
        "exact_successor": row["exact_successor"] or current_stage,
        "stage_history": history,
        "stage_count": len(DIRECT_STAGES),
        "title_status": title_status,
        "title_completion": title_completion,
        "actual_title": row["actual_title"],
        "expected_title": row["expected_title"],
        "post_publish_issue_list": _json_load(row["post_publish_issue_list"], []),
        "surface_failures": _json_load(row["surface_failures"], []),
        "started_at": row["started_at"],
        "scheduler_trigger_at": _row_value(row, "scheduler_trigger_at", "") or "",
        "updated_at": row["updated_at"],
        "completed_at": row["completed_at"],
        "completion_elapsed_seconds": (
            float(_row_value(row, "completion_elapsed_seconds"))
            if _row_value(row, "completion_elapsed_seconds") is not None
            else None
        ),
        "completion_elapsed_at": _row_value(row, "completion_elapsed_at", "") or "",
        "start_seal": _json_load(_row_value(row, "start_seal_json", "{}"), {}),
        "publish_seal": _json_load(_row_value(row, "publish_seal_json", "{}"), {}),
        "timing_events": [
            {
                "event_kind": item["event_kind"],
                "started_at": item["started_at"],
                "ended_at": item["ended_at"],
                "elapsed_seconds": (
                    float(item["elapsed_seconds"])
                    if item["elapsed_seconds"] is not None
                    else None
                ),
                "evidence": _json_load(item["evidence_json"], {}),
            }
            for item in timing_rows
        ],
        "daily_operations": [
            {
                "operation_id": item["operation_id"],
                "operation_index": int(item["operation_index"]),
                "input_hash": item["input_hash"],
                "handler_id": item["handler_id"],
                "receipt": _json_load(item["receipt_json"], {}),
                "applied_at": item["applied_at"],
                "fencing_token": int(item["fencing_token"] or 0),
                "receipt_hash": item["receipt_hash"] or "",
            }
            for item in daily_rows
        ],
        "slo": {
            "elapsed_minutes": elapsed,
            "elapsed_seconds": elapsed_seconds,
            "target_minutes": 45,
            "optional_high_cost_freeze_minutes": 75,
            "slo_minutes": 90,
            "target_met": elapsed <= 45,
            "optional_high_cost_frozen": elapsed >= 75,
            "slo_met": elapsed <= 90,
            "slo_debt": elapsed > 90,
            "time_band": slo["time_band"],
            "dispatch": slo["dispatch"],
            "continue_public_successors": True,
            "checkpoints": [
                {"minute": int(item["checkpoint_minute"]), "elapsed_minutes": float(item["elapsed_minutes"]), "recorded_at": item["recorded_at"]}
                for item in checkpoint_rows
            ],
        },
    }


def inspect_run(store: DirectRunStore, *, run_id: str) -> dict[str, Any]:
    with closing(store.connect()) as conn:
        row = store._run_row(conn, run_id)
        projection = _projection_from_row(store, conn, row)
        projection.pop("writer_lease", None)
        return projection


def _same_run_release_commit_at_head(
    cwd: str,
    *,
    source_baseline: str,
    issue_date: str,
    run_id: str,
    expected_release_sha: str = "",
) -> bool:
    """current integrationのcommit後crashだけを同run continuation候補にする。"""

    if not re.fullmatch(r"[0-9a-f]{40}", source_baseline):
        return False
    env = dict(os.environ)
    env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})

    def probe(*args: str) -> str | None:
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=30,
            check=False,
            shell=False,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
            env=env,
        )
        return completed.stdout.strip() if completed.returncode == 0 else None

    head = str(probe("rev-parse", "HEAD") or "").casefold()
    if not re.fullmatch(r"[0-9a-f]{40}", head) or head == source_baseline:
        return False
    if expected_release_sha and head != expected_release_sha.casefold():
        return False
    parent = str(probe("rev-parse", f"{head}^") or "").casefold()
    message = str(probe("log", "-1", "--format=%B", head) or "")
    expected_message = f"Publish {issue_date} News-Grasp daily release [{run_id}]"
    return parent == source_baseline and message == expected_message


def start_run(
    store: DirectRunStore,
    *,
    automation_id: str = AUTOMATION_ID,
    cwd: str | Path,
    issue_date: str,
    run_intent: str | None = None,
    manifest_id: str = "",
    manifest_reservation_id: str = "",
    observation_receipt: Mapping[str, Any] | None = None,
    scheduler_trigger_at: str | None = None,
    source_baseline: str = "",
    runtime_generation: str = "",
    remote_base_sha: str = "",
    allowed_side_effect_ids: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    schema_receipt = store.ensure_runtime_schema()
    issue = _validate_issue_date(issue_date)
    canonical_cwd = _canonical_cwd(cwd)
    now = store.now()
    now_text = _iso(now)
    if run_intent is not None and not str(run_intent).strip():
        raise ValueError("run_intent_invalid")
    if run_intent is None and not store.test_only_allow_semantic_verifier:
        raise ValueError("run_intent_required")
    identity_run_intent = str(run_intent or "")
    if not store.test_only_allow_semantic_verifier and not str(scheduler_trigger_at or "").strip():
        raise ValueError("scheduler_trigger_at_required")
    trigger_at = str(scheduler_trigger_at or now_text)
    if _parse_time(trigger_at) is None:
        raise ValueError("scheduler_trigger_at_invalid")
    supplied_manifest_id = str(manifest_id or "").strip().casefold()
    supplied_manifest_reservation_id = str(manifest_reservation_id or "").strip().casefold()
    if supplied_manifest_reservation_id and not re.fullmatch(r"[0-9a-f]{64}", supplied_manifest_reservation_id):
        raise ValueError("manifest_reservation_id_invalid")
    if not store.test_only_allow_semantic_verifier:
        if not re.fullmatch(r"[0-9a-f]{40}", str(source_baseline or "").casefold()):
            raise ValueError("source_baseline_required")
        if not re.fullmatch(r"[0-9a-f]{40}", str(remote_base_sha or "").casefold()):
            raise ValueError("remote_base_sha_required")
        # manifestは公開直前のsealまで存在してはならない。開始時に完成
        # manifestを受け取ると、開始identityと公開identityの二段階束縛が
        # 崩れ、後段のrebind逃げ道になるためfail-closedする。
        if supplied_manifest_id:
            raise ValueError("manifest_id_must_be_unsealed_at_start")
        if not re.fullmatch(r"[0-9a-f]{64}", supplied_manifest_reservation_id):
            raise ValueError("manifest_reservation_id_required")
        if not str(runtime_generation or "").strip():
            raise ValueError("runtime_generation_required")
        side_effect_ids = [str(item).strip() for item in (allowed_side_effect_ids or ())]
        if not side_effect_ids or len(set(side_effect_ids)) != len(side_effect_ids):
            raise ValueError("allowed_side_effect_ids_required")
    else:
        side_effect_ids = [str(item).strip() for item in (allowed_side_effect_ids or ()) if str(item).strip()]
        # test_onlyの既存fixtureはmanifest_idだけを渡して開始するため、
        # その互換性は維持する。productionでは上記の未seal規則を緩めない。
        if not supplied_manifest_reservation_id and re.fullmatch(r"[0-9a-f]{64}", supplied_manifest_id):
            supplied_manifest_reservation_id = supplied_manifest_id
    with closing(store.connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        latest = store._latest_for_identity(
            conn,
            automation_id=automation_id,
            cwd=canonical_cwd,
            issue_date=issue,
            run_intent=identity_run_intent,
        )
        # 同じ自然Daily identityを一度completedへ確定した後は、generationを
        # 増やして再実行してはならない。公開済みbundleを書き換えるcontent
        # handlerやduplicate external sendへ到達する前に、この最上流transaction
        # でfail-closedする。明示的な訂正公開は別run intentと別authorityを持つ
        # 新releaseであり、scheduled_production_directの続きにはしない。
        completed_identity = conn.execute(
            """
            SELECT * FROM runs
            WHERE automation_id=? AND issue_date=?
              AND COALESCE(run_intent,'')=?
              AND status IN ('completed','complete','green')
            ORDER BY generation DESC
            LIMIT 1
            """,
            (automation_id, issue, identity_run_intent),
        ).fetchone()
        if completed_identity is not None:
            projection = _projection_from_row(store, conn, completed_identity)
            projection.update(
                {
                    "ok": False,
                    "status": "blocked",
                    "single_flight": "completed_reexecution_rejected",
                    "failures": ["same_issue_completed_reexecution_forbidden"],
                    "exact_successor": "next_natural_issue_date_or_explicit_new_release",
                }
            )
            projection.pop("writer_lease", None)
            projection.pop("fencing_token", None)
            start_seal_projection = projection.get("start_seal")
            if isinstance(start_seal_projection, Mapping):
                projection["start_seal"] = {
                    key: value
                    for key, value in start_seal_projection.items()
                    if key not in {"fencingToken", "writerLease"}
                }
            conn.rollback()
            return projection
        if latest is not None:
            lease_until = _parse_time(latest["lease_until"])
            if latest["status"] in {"active", "executing", "finalizing"} and lease_until and lease_until > now:
                projection = _projection_from_row(store, conn, latest)
                projection["status"] = "attached"
                projection["single_flight"] = "attached"
                projection["attached_to_run_id"] = latest["run_id"]
                # attachは観測専用。active writerのleaseを別workerへ返さない。
                projection.pop("writer_lease", None)
                projection.pop("fencing_token", None)
                start_seal_projection = projection.get("start_seal")
                if isinstance(start_seal_projection, Mapping):
                    projection["start_seal"] = {
                        key: value
                        for key, value in start_seal_projection.items()
                        if key not in {"fencingToken", "writerLease"}
                    }
                conn.rollback()
                return projection
            if latest["status"] in {"active", "executing", "finalizing"}:
                external_state = store._external_side_effect_state(conn, latest["run_id"])
                if latest["status"] == "finalizing" and str(latest["finalization_nonce"] or ""):
                    admission_receipt = _json_load(
                        str(latest["observation_receipt_json"] or "{}"),
                        {},
                    )
                    stored_seal = _json_load(
                        _row_value(latest, "start_seal_json", "{}"),
                        {},
                    )
                    publish_seal = _json_load(
                        _row_value(latest, "publish_seal_json", "{}"),
                        {},
                    )
                    daily_receipt_rows = conn.execute(
                        """
                        SELECT operation_id,operation_index,input_hash,handler_id,payload_json,receipt_json,
                               applied_at,fencing_token,receipt_hash
                        FROM daily_operation_receipts WHERE run_id=? ORDER BY operation_index
                        """,
                        (latest["run_id"],),
                    ).fetchall()
                    daily_indices = [int(item[1]) for item in daily_receipt_rows]
                    daily_receipt_digest = hashlib.sha256(
                        _json_dump([list(item) for item in daily_receipt_rows]).encode("utf-8")
                    ).hexdigest()
                    consumer_receipt_hash = ""
                    if len(daily_receipt_rows) > DAILY_OPERATION_ORDER.index("consumer_public_verification"):
                        consumer_operation = _json_load(
                            str(
                                daily_receipt_rows[
                                    DAILY_OPERATION_ORDER.index("consumer_public_verification")
                                ][5]
                            ),
                            {},
                        )
                        consumer_receipt = _unwrap_consumer_public_receipt(
                            consumer_operation.get("producer_receipt")
                            if isinstance(consumer_operation, Mapping)
                            else None
                        )
                        if consumer_receipt is None and isinstance(consumer_operation, Mapping):
                            consumer_receipt = _unwrap_consumer_public_receipt(
                                consumer_operation.get("producerReceipt")
                            )
                        if consumer_receipt is not None:
                            consumer_receipt_hash = _consumer_receipt_hash(consumer_receipt)
                    finalizer_recovery_valid = (
                        isinstance(admission_receipt, Mapping)
                        and admission_receipt.get("schemaVersion")
                        == "NEWS_GRASP_DAILY_FINALIZER_ADMISSION_V1"
                        and str(admission_receipt.get("runId") or "")
                        == str(latest["run_id"])
                        and str(admission_receipt.get("nonce") or "")
                        == str(latest["finalization_nonce"] or "")
                        and str(admission_receipt.get("manifestId") or "")
                        == str(latest["manifest_id"] or "")
                        and str(admission_receipt.get("admissionUpdatedAt") or "")
                        == str(latest["updated_at"] or "")
                        and str(admission_receipt.get("dailyReceiptDigest") or "")
                        == daily_receipt_digest
                        and str(admission_receipt.get("consumerReceiptHash") or "")
                        == consumer_receipt_hash
                        and bool(consumer_receipt_hash)
                        and daily_indices == list(range(len(DAILY_OPERATION_ORDER)))
                        and stored_seal.get("runtimeGeneration") == str(runtime_generation)
                        and stored_seal.get("allowedSideEffectIds") == side_effect_ids
                        and publish_seal.get("schemaVersion") == "NEWS_GRASP_PUBLISH_SEAL_V1"
                        and publish_seal.get("runId") == latest["run_id"]
                        and int(publish_seal.get("fencingToken") or 0)
                        == int(_row_value(latest, "fencing_token", 0) or 0)
                        and external_state in {"started", "unknown_delivery"}
                    )
                    if finalizer_recovery_valid:
                        renewed_until = _iso(now + store.lease_ttl)
                        changed = conn.execute(
                            "UPDATE runs SET lease_until=? WHERE run_id=? AND writer_lease=? "
                            "AND fencing_token=? AND lease_until=? AND status='finalizing' "
                            "AND finalization_nonce=? AND updated_at=?",
                            (
                                renewed_until,
                                latest["run_id"],
                                latest["writer_lease"],
                                int(_row_value(latest, "fencing_token", 0) or 0),
                                latest["lease_until"],
                                latest["finalization_nonce"],
                                latest["updated_at"],
                            ),
                        ).rowcount
                        if changed != 1:
                            conn.rollback()
                            raise PermissionError("finalizer_continuation_lease_cas_conflict")
                        _append_timing_event_in_tx(
                            conn,
                            run_id=str(latest["run_id"]),
                            event_kind="handoff",
                            started_at=str(latest["lease_until"]),
                            ended_at=now_text,
                            evidence={
                                "phase": "atomic_completion",
                                "event": "finalizer_receipt_resume",
                                "external_state": external_state,
                            },
                        )
                        recovered = store._run_row(conn, str(latest["run_id"]))
                        projection = _projection_from_row(store, conn, recovered)
                        projection.update(
                            {
                                "status": "finalizing",
                                "single_flight": "recovered_finalizer_receipt",
                                "continuation_recovery": True,
                            }
                        )
                        conn.commit()
                        return projection
                    projection = _projection_from_row(store, conn, latest)
                    projection.update(
                        {
                            "status": "blocked",
                            "single_flight": "blocked",
                            "failures": ["finalizer_recovery_evidence_invalid"],
                        }
                    )
                    projection.pop("writer_lease", None)
                    projection.pop("fencing_token", None)
                    conn.rollback()
                    return projection
                # current_issue_integrationはrelease commit/publish sealをDaily
                # receiptより先に作る。ownerがその狭い窓で停止した場合だけ、
                # exact commitまたはexact sealを一次証拠として同じrunへ戻す。
                # 新generationへ進めると旧run ID入りcommitを再利用できず、
                # duplicate publicationの誘因になるためである。
                stored_seal = _json_load(
                    _row_value(latest, "start_seal_json", "{}"),
                    {},
                )
                publish_seal = _json_load(
                    _row_value(latest, "publish_seal_json", "{}"),
                    {},
                )
                current_claim = conn.execute(
                    "SELECT input_hash,handler_id,fencing_token,status FROM daily_operation_claims "
                    "WHERE run_id=? AND operation_id='current_issue_integration'",
                    (latest["run_id"],),
                ).fetchone()
                current_receipt = conn.execute(
                    "SELECT 1 FROM daily_operation_receipts "
                    "WHERE run_id=? AND operation_id='current_issue_integration'",
                    (latest["run_id"],),
                ).fetchone()
                current_prior_indices = [
                    int(item[0])
                    for item in conn.execute(
                        "SELECT operation_index FROM daily_operation_receipts "
                        "WHERE run_id=? ORDER BY operation_index",
                        (latest["run_id"],),
                    ).fetchall()
                ]
                route_binding_matches = (
                    stored_seal.get("runtimeGeneration") == str(runtime_generation)
                    and stored_seal.get("allowedSideEffectIds") == side_effect_ids
                )
                publish_seal_matches = (
                    publish_seal.get("schemaVersion") == "NEWS_GRASP_PUBLISH_SEAL_V1"
                    and publish_seal.get("runId") == latest["run_id"]
                    and int(publish_seal.get("fencingToken") or 0)
                    == int(_row_value(latest, "fencing_token", 0) or 0)
                )
                commit_matches = _same_run_release_commit_at_head(
                    canonical_cwd,
                    source_baseline=str(stored_seal.get("sourceBaseline") or "").casefold(),
                    issue_date=issue,
                    run_id=str(latest["run_id"]),
                    expected_release_sha=(
                        str(publish_seal.get("releaseCommitSha") or "")
                        if publish_seal.get("schemaVersion") == "NEWS_GRASP_PUBLISH_SEAL_V1"
                        else ""
                    ),
                )
                can_recover_current_issue = (
                    latest["status"] in {"active", "executing"}
                    and external_state == "none"
                    and not str(latest["external_started_at"] or "")
                    and route_binding_matches
                    and current_claim is not None
                    and str(current_claim[3] or "") in {"claimed", "recoverable"}
                    and int(current_claim[2] or 0)
                    == int(_row_value(latest, "fencing_token", 0) or 0)
                    and current_receipt is None
                    and current_prior_indices
                    == list(range(DAILY_OPERATION_ORDER.index("current_issue_integration")))
                    and commit_matches
                    and (
                        publish_seal.get("schemaVersion")
                        != "NEWS_GRASP_PUBLISH_SEAL_V1"
                        or publish_seal_matches
                    )
                )
                if can_recover_current_issue:
                    recovery_lease = _opaque_id(
                        "lease-current-recovery",
                        issue,
                        int(latest["generation"]),
                    )
                    renewed_until = _iso(now + store.lease_ttl)
                    changed = conn.execute(
                        "UPDATE runs SET writer_lease=?,lease_until=?,status='active',updated_at=?,"
                        "exact_successor='current_issue_integration' "
                        "WHERE run_id=? AND writer_lease=? AND lease_until=? "
                        "AND status IN ('active','executing')",
                        (
                            recovery_lease,
                            renewed_until,
                            now_text,
                            latest["run_id"],
                            latest["writer_lease"],
                            latest["lease_until"],
                        ),
                    ).rowcount
                    if changed != 1:
                        conn.rollback()
                        raise PermissionError("current_issue_continuation_lease_cas_conflict")
                    conn.execute(
                        "UPDATE daily_operation_claims SET status='recoverable',completed_at=? "
                        "WHERE run_id=? AND operation_id='current_issue_integration' "
                        "AND status IN ('claimed','recoverable')",
                        (now_text, latest["run_id"]),
                    )
                    _append_timing_event_in_tx(
                        conn,
                        run_id=str(latest["run_id"]),
                        event_kind="handoff",
                        started_at=str(latest["lease_until"]),
                        ended_at=now_text,
                        evidence={
                            "phase": "current_issue_integration",
                            "event": "expired_owner_continuation",
                            "release_commit_reusable": commit_matches,
                            "publish_seal_reusable": publish_seal_matches,
                        },
                    )
                    recovered = store._run_row(conn, str(latest["run_id"]))
                    projection = _projection_from_row(store, conn, recovered)
                    projection.update(
                        {
                            "status": "active",
                            "single_flight": "recovered_after_expired_owner",
                            "continuation_recovery": True,
                        }
                    )
                    conn.commit()
                    return projection
                if str(latest["external_started_at"] or "") or external_state != "none":
                    # owner lease失効後のexternal_publicationだけは、同じrunへ
                    # continuation capabilityを再発行してreconcileさせる。生存中
                    # leaseのsteal、他operationの再実行、新generation化はしない。
                    stored_seal = _json_load(_row_value(latest, "start_seal_json", "{}"), {})
                    # push後のremote HEADやmanifest reservationをcaller側で
                    # 再計算しない。immutable start/publish sealをauthorityにし、
                    # restart callerはruntime generationとroute集合だけを証明する。
                    publish_seal = _json_load(
                        _row_value(latest, "publish_seal_json", "{}"),
                        {},
                    )
                    binding_matches = (
                        stored_seal.get("runtimeGeneration") == str(runtime_generation)
                        and stored_seal.get("allowedSideEffectIds") == side_effect_ids
                        and publish_seal.get("schemaVersion") == "NEWS_GRASP_PUBLISH_SEAL_V1"
                        and publish_seal.get("runId") == latest["run_id"]
                        and int(publish_seal.get("fencingToken") or 0)
                        == int(_row_value(latest, "fencing_token", 0) or 0)
                    )
                    external_claim = conn.execute(
                        "SELECT input_hash,handler_id,fencing_token,status FROM daily_operation_claims "
                        "WHERE run_id=? AND operation_id='external_publication'",
                        (latest["run_id"],),
                    ).fetchone()
                    external_receipt = conn.execute(
                        "SELECT 1 FROM daily_operation_receipts "
                        "WHERE run_id=? AND operation_id='external_publication'",
                        (latest["run_id"],),
                    ).fetchone()
                    prior_indices = [
                        int(item[0])
                        for item in conn.execute(
                            "SELECT operation_index FROM daily_operation_receipts "
                            "WHERE run_id=? ORDER BY operation_index",
                            (latest["run_id"],),
                        ).fetchall()
                    ]
                    can_recover_external = (
                        latest["status"] in {"active", "executing"}
                        and binding_matches
                        and external_claim is not None
                        and str(external_claim[3] or "") in {"claimed", "recoverable"}
                        and int(external_claim[2] or 0) == int(_row_value(latest, "fencing_token", 0) or 0)
                        and external_receipt is None
                        and prior_indices == list(range(DAILY_OPERATION_ORDER.index("external_publication")))
                    )
                    if can_recover_external:
                        recovery_lease = _opaque_id(
                            "lease-recovery",
                            issue,
                            int(latest["generation"]),
                        )
                        renewed_until = _iso(now + store.lease_ttl)
                        changed = conn.execute(
                            "UPDATE runs SET writer_lease=?,lease_until=?,status='active',updated_at=?,"
                            "exact_successor='external_publication' "
                            "WHERE run_id=? AND writer_lease=? AND lease_until=? "
                            "AND status IN ('active','executing')",
                            (
                                recovery_lease,
                                renewed_until,
                                now_text,
                                latest["run_id"],
                                latest["writer_lease"],
                                latest["lease_until"],
                            ),
                        ).rowcount
                        if changed != 1:
                            conn.rollback()
                            raise PermissionError("external_continuation_lease_cas_conflict")
                        conn.execute(
                            "UPDATE daily_operation_claims SET status='recoverable',completed_at=? "
                            "WHERE run_id=? AND operation_id='external_publication' "
                            "AND status IN ('claimed','recoverable')",
                            (now_text, latest["run_id"]),
                        )
                        _append_timing_event_in_tx(
                            conn,
                            run_id=str(latest["run_id"]),
                            event_kind="handoff",
                            started_at=str(latest["lease_until"]),
                            ended_at=now_text,
                            evidence={
                                "phase": "external_publication",
                                "event": "expired_owner_continuation",
                                "external_state": external_state,
                            },
                        )
                        recovered = store._run_row(conn, str(latest["run_id"]))
                        projection = _projection_from_row(store, conn, recovered)
                        projection.update(
                            {
                                "status": "active",
                                "single_flight": "recovered_after_expired_owner",
                                "continuation_recovery": True,
                            }
                        )
                        conn.commit()
                        return projection
                    projection = _projection_from_row(store, conn, latest)
                    failure = (
                        "unknown_delivery_requires_attach"
                        if external_state == "unknown_delivery"
                        else "superseded_after_external_start"
                    )
                    projection.update(
                        {
                            "status": "blocked",
                            "single_flight": "blocked",
                            "failures": [failure],
                        }
                    )
                    projection.pop("writer_lease", None)
                    conn.rollback()
                    return projection
                conn.execute(
                    "UPDATE runs SET status = ?, updated_at = ? WHERE run_id = ?",
                    ("stale_writer_rejected", now_text, latest["run_id"]),
                )
        prior_generation = int(latest["generation"]) if latest is not None else 0
        generation = max(prior_generation + 1, _call_generation(store.host_generation))
        fence_row = conn.execute(
            """
            SELECT COALESCE(MAX(fencing_token), 0)
            FROM runs
            WHERE automation_id=? AND issue_date=? AND COALESCE(run_intent,'')=?
            """,
            (automation_id, issue, identity_run_intent),
        ).fetchone()
        fencing_token = int(fence_row[0] or 0) + 1
        run_id = _opaque_id("direct", issue, generation)
        writer_lease = _opaque_id("lease", issue, generation)
        lease_until = _iso(now + store.lease_ttl)
        expected_title = _expected_title(issue)
        start_seal = {
            "schemaVersion": "NEWS_GRASP_START_SEAL_V2",
            "runId": run_id,
            "automationId": automation_id,
            "issueDate": issue,
            "runIntent": identity_run_intent,
            "generation": generation,
            "fencingToken": fencing_token,
            "schedulerTriggerAt": trigger_at,
            "cwd": canonical_cwd,
            "manifestReservationId": supplied_manifest_reservation_id,
            "sourceBaseline": str(source_baseline),
            "runtimeGeneration": str(runtime_generation or (RUNTIME_SCHEMA_V2 if run_intent else RUNTIME_SCHEMA)),
            "remoteBaseSha": str(remote_base_sha),
            "allowedSideEffectIds": side_effect_ids,
            "runtimeSchemaMigrationId": str(
                (schema_receipt.get("migration_receipt") or {}).get("migrationId") or ""
            ),
            "runtimeSchemaMigrationHash": str(
                (schema_receipt.get("migration_receipt") or {}).get("migrationHash") or ""
            ),
        }
        conn.execute(
            """
            INSERT INTO runs (
                run_id, automation_id, cwd, issue_date, generation, writer_lease,
                status, current_stage_index, started_at, lease_until, updated_at,
                expected_title, exact_successor, runtime_schema, run_intent,
                manifest_id, manifest_reservation_id, observation_receipt_json, scheduler_trigger_at,
                fencing_token, start_seal_json, migration_receipt_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                automation_id,
                canonical_cwd,
                issue,
                generation,
                writer_lease,
                "active",
                0,
                now_text,
                lease_until,
                now_text,
                expected_title,
                DIRECT_STAGES[0],
                RUNTIME_SCHEMA_V2 if run_intent else RUNTIME_SCHEMA,
                identity_run_intent,
                supplied_manifest_id if store.test_only_allow_semantic_verifier else "",
                supplied_manifest_reservation_id,
                _json_dump(dict(observation_receipt or {})),
                trigger_at,
                fencing_token,
                _json_dump(start_seal),
                _json_dump(schema_receipt.get("migration_receipt") or {}),
            ),
        )
        trigger_value = _parse_time(trigger_at)
        if trigger_value is None:
            conn.rollback()
            raise ValueError("scheduler_trigger_at_invalid")
        # T0はschedulerのtrigger時刻で固定する。triggerから実際のworker
        # admissionまでをqueue、admission後をinternal_processingとして
        # 最初から区間分離し、後段でwall-clockを推測しない。
        if trigger_value <= now:
            _append_timing_event_in_tx(
                conn,
                run_id=run_id,
                event_kind="queue",
                started_at=_iso(trigger_value),
                ended_at=now_text,
                evidence={"scheduler_trigger_at": _iso(trigger_value), "t0": _iso(trigger_value)},
            )
        else:
            # 未来のtriggerは時計補正・テストfixtureでは許すが、負の時間を
            # 作らず、admissionをtrigger時刻から開始した扱いに固定する。
            _append_timing_event_in_tx(
                conn,
                run_id=run_id,
                event_kind="queue",
                started_at=now_text,
                ended_at=now_text,
                evidence={"scheduler_trigger_at": _iso(trigger_value), "t0": _iso(trigger_value), "clock_skew": True},
            )
        _append_timing_event_in_tx(
            conn,
            run_id=run_id,
            event_kind="internal_processing",
            started_at=now_text,
            evidence={"phase": "run_admission", "scheduler_trigger_at": _iso(trigger_value), "t0": _iso(trigger_value)},
        )
        row = store._run_row(conn, run_id)
        projection = _projection_from_row(store, conn, row)
        conn.commit()
        return projection


def _expected_title(issue_date: str) -> str:
    from tools.news_grasp_title_control import expected_title

    return expected_title(issue_date)


def _verify_writer(
    row: sqlite3.Row,
    writer_lease: str,
    now: datetime,
    *,
    allowed_statuses: set[str] | None = None,
    fencing_token: int | None = None,
) -> None:
    if str(row["writer_lease"]) != str(writer_lease):
        raise PermissionError("stale writer lease fenced")
    if fencing_token is not None:
        try:
            observed_token = int(fencing_token)
        except (TypeError, ValueError) as exc:
            raise PermissionError("fencing_token_invalid") from exc
        if observed_token <= 0 or observed_token != int(_row_value(row, "fencing_token", 0) or 0):
            raise PermissionError("fencing_token_fenced")
    if row["status"] not in (allowed_statuses or {"active", "executing"}):
        raise RuntimeError("run_not_writable")
    # 期限切れ後も同じtokenかつactiveのrunはresume可能。別writerがstart_runを
    # 取得した場合は旧runがstale_writer_rejectedになるため上のstatus gateで拒否する。


def _call_verifier(
    verifier: Any,
    stage_id: str,
    *,
    run: Mapping[str, Any],
    caller_result: Mapping[str, Any] | None,
    observed_surface: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if verifier is None:
        return {"ok": False, "status": "semantic_verifier_missing"}
    try:
        if hasattr(verifier, "verify") and callable(verifier.verify):
            return _as_mapping(
                verifier.verify(
                    stage_id,
                    run=run,
                    caller_result=caller_result or {},
                    observed_surface=observed_surface or {},
                )
            )
        if callable(verifier):
            return _as_mapping(
                verifier(
                    stage_id,
                    run=run,
                    caller_result=caller_result or {},
                    observed_surface=observed_surface or {},
                )
            )
    except TypeError:
        if callable(verifier):
            return _as_mapping(verifier(stage_id, observed_surface or {}))
    except Exception as exc:  # noqa: BLE001 - verifier failure is stage Red data.
        return {"ok": False, "status": "semantic_verifier_error", "reason": str(exc)}
    return {"ok": False, "status": "semantic_verifier_invalid"}


def _append_unique(items: list[Any], value: Any) -> list[Any]:
    if value not in items:
        items.append(value)
    return items


def _title_failure(row: Mapping[str, Any], issue_date: str) -> str:
    from tools.news_grasp_title_control import validate_title

    title_status = str(row.get("title_status") or "")
    actual_title = str(row.get("actual_title") or "")
    if title_status in TITLE_SUCCESS:
        if validate_title(actual_title, issue_date).get("ok") is not True:
            return "title exact semantic violation"
        return ""
    if title_status in TITLE_NONBLOCKING:
        issues = row.get("post_publish_issue_list")
        if not isinstance(issues, list) or not any("title" in str(item).casefold() for item in issues):
            return "title failure issue missing"
        return ""
    return "title status invalid"


def _public_failures(row: Mapping[str, Any], issue_date: str) -> list[str]:
    failures: list[str] = []
    if row.get("completion_mode") not in {"direct_public_v1", "direct_public_v2"}:
        failures.append("completion_mode_invalid")
    if row.get("issue_date") != issue_date:
        failures.append("issue_date_mismatch")
    surfaces = row.get("public_surfaces")
    if not isinstance(surfaces, Mapping):
        failures.append("public_surfaces_missing")
        return failures
    for name in PUBLIC_SURFACES:
        item = surfaces.get(name)
        if not isinstance(item, Mapping):
            failures.append(f"public_surface_missing:{name}")
            continue
        if item.get("issue_date") != issue_date:
            failures.append(f"public_surface_issue_date_mismatch:{name}")
        if item.get("semantic_ok") is not True:
            failures.append(f"public_surface_semantic_red:{name}")
        if str(item.get("status") or "").casefold() not in {"green", "verified", "verified_with_warnings"}:
            failures.append(f"public_surface_not_green:{name}")
    return failures


def _surface_scoped(row: Mapping[str, Any], stage_id: str) -> bool:
    if row.get("surface_scoped") is True:
        return True
    status = str(row.get("status") or "").casefold()
    surface = str(row.get("surface") or "").casefold()
    reason = str(row.get("reason") or "").casefold()
    return (
        stage_id in {"youtube_podcasts", "playlist", "notification"}
        and (
            status in {"external_failure", "quota", "deferred"}
            or "quota" in reason
            or "oauth" in reason
            or "youtube" in surface
        )
    )


def _advance_core(
    store: DirectRunStore,
    *,
    run_id: str,
    stage_id: str,
    writer_lease: str,
    caller_result: Mapping[str, Any] | None = None,
    observed_surface: Mapping[str, Any] | None = None,
    verifier: Any = None,
    observed_at: datetime | None = None,
    _registered_consumer: Any = None,
    fencing_token: int | None = None,
) -> dict[str, Any]:
    if verifier is not None and not store.test_only_allow_semantic_verifier and _registered_consumer is not _REGISTERED_CONSUMER_CAPABILITY:
        raise PermissionError("semantic_verifier_injection_test_only")
    now = (observed_at or store.now()).astimezone(JST)
    now_text = _iso(now)
    with closing(store.connect()) as conn:
        row = store._run_row(conn, run_id)
        _require_fencing_token(store, fencing_token)
        _verify_writer(row, writer_lease, now, fencing_token=fencing_token)
        started_at = (
            _parse_time(str(_row_value(row, "scheduler_trigger_at", "")))
            or _parse_time(str(row["started_at"]))
            or now
        )
        elapsed_minutes = max(0.0, (now - started_at).total_seconds() / 60.0)
        for checkpoint in (45, 75, 90):
            if elapsed_minutes >= checkpoint:
                conn.execute(
                    "INSERT OR IGNORE INTO runtime_checkpoints (run_id,checkpoint_minute,elapsed_minutes,recorded_at) VALUES (?,?,?,?)",
                    (run_id, checkpoint, elapsed_minutes, now_text),
                )
        lease_until = _iso(now + store.lease_ttl)
        renewed = conn.execute(
            "UPDATE runs SET lease_until = ? WHERE run_id = ? AND writer_lease = ? AND lease_until = ?",
            (lease_until, run_id, writer_lease, row["lease_until"]),
        ).rowcount
        if renewed != 1:
            raise PermissionError("writer_lease_cas_conflict")
        row = store._run_row(conn, run_id)
        current_index = int(row["current_stage_index"])
        current_stage = _stage_for_index(current_index)
        if not current_stage:
            return _projection_from_row(store, conn, row)
        if stage_id != current_stage:
            raise ValueError("stage order successor violation")
        if stage_id == "public_completion":
            raise PermissionError("public_completion_requires_atomic_finalizer")
        _append_timing_event_in_tx(
            conn,
            run_id=run_id,
            event_kind="internal_processing",
            started_at=now_text,
            evidence={"phase": "legacy_stage", "stage_id": stage_id, "event": "stage_start"},
        )
        projection = _projection_from_row(store, conn, row)
        verifier_row = _call_verifier(
            verifier if verifier is not None else store.semantic_verifier,
            stage_id,
            run=projection,
            caller_result=caller_result,
            observed_surface=observed_surface,
        )
        ok = verifier_row.get("ok") is True
        stage_status = str(verifier_row.get("status") or "green").casefold()
        stage_warnings = list(verifier_row.get("post_publish_issue_list") or [])
        if stage_status == "verified_with_warnings":
            typed_warnings_ok = bool(stage_warnings) and all(
                isinstance(item, Mapping)
                and str(item.get("status") or "").casefold() == "warning"
                and bool(item.get("surface"))
                and bool(item.get("reasonCode"))
                and bool(item.get("evidenceRef"))
                for item in stage_warnings
            )
            if not typed_warnings_ok:
                verifier_row = {**verifier_row, "ok": False, "status": "red", "failures": ["verified_with_warnings_without_typed_issue"]}
                ok = False
        if stage_id == "title_control":
            title_failure = _title_failure(verifier_row, str(row["issue_date"]))
            if title_failure:
                raise ValueError(title_failure)
        if stage_id == "public_completion" and ok:
            public_failures = _public_failures(verifier_row, str(row["issue_date"]))
            if public_failures:
                verifier_row = {
                    **verifier_row,
                    "ok": False,
                    "status": "red",
                    "failures": public_failures,
                }
                ok = False
        issues = _json_load(row["post_publish_issue_list"], [])
        surface_failures = _json_load(row["surface_failures"], [])
        if ok and stage_status == "verified_with_warnings":
            for item in stage_warnings:
                issues = _append_unique(issues, dict(item))
            conn.execute("UPDATE runs SET post_publish_issue_list=?,updated_at=? WHERE run_id=?", (_json_dump(issues), now_text, run_id))
        if stage_id == "title_control":
            status = str(verifier_row.get("title_status") or "")
            for item in verifier_row.get("post_publish_issue_list") or []:
                issues = _append_unique(issues, item)
            conn.execute(
                """
                UPDATE runs
                SET title_status = ?, actual_title = ?, post_publish_issue_list = ?,
                    updated_at = ?
                WHERE run_id = ?
                """,
                (
                    status,
                    str(verifier_row.get("actual_title") or ""),
                    _json_dump(issues),
                    now_text,
                    run_id,
                ),
            )
        if not ok:
            _close_open_timing_event(
                conn,
                run_id=run_id,
                ended_at=now_text,
                evidence={"phase": "legacy_stage", "stage_id": stage_id, "event": "stage_failure"},
            )
            if _surface_scoped(verifier_row, stage_id):
                surface_failures = _append_unique(surface_failures, dict(verifier_row))
                next_index = min(current_index + 1, len(DIRECT_STAGES) - 1)
                next_stage = _stage_for_index(next_index)
                conn.execute(
                    """
                    INSERT OR REPLACE INTO stages (
                        run_id, stage_index, stage_id, status, started_at,
                        completed_at, evidence_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        current_index,
                        stage_id,
                        "deferred",
                        now_text,
                        now_text,
                        _json_dump(verifier_row),
                    ),
                )
                conn.execute(
                    """
                    UPDATE runs
                    SET current_stage_index = ?, surface_failures = ?,
                        exact_successor = ?, updated_at = ?
                    WHERE run_id = ?
                    """,
                    (next_index, _json_dump(surface_failures), next_stage, now_text, run_id),
                )
                projection = {
                    **_projection_from_row(store, conn, store._run_row(conn, run_id)),
                    "stage": stage_id,
                    "completed_stage": stage_id,
                    "status": "deferred",
                    "next_stage": next_stage,
                    "successor": next_stage,
                    "failures": [str(verifier_row.get("reason") or "surface_scoped_failure")],
                }
                conn.commit()
                return projection
            conn.execute(
                "UPDATE runs SET exact_successor = ?, updated_at = ? WHERE run_id = ?",
                (current_stage, now_text, run_id),
            )
            projection = {
                **_projection_from_row(store, conn, store._run_row(conn, run_id)),
                "stage": stage_id,
                "status": "red",
                "next_stage": current_stage,
                "successor": current_stage,
                "failures": list(verifier_row.get("failures") or [str(verifier_row.get("status") or "semantic_red")]),
            }
            conn.commit()
            return projection
        _close_open_timing_event(
            conn,
            run_id=run_id,
            ended_at=now_text,
            evidence={"phase": "legacy_stage", "stage_id": stage_id, "event": "stage_end"},
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO stages (
                run_id, stage_index, stage_id, status, started_at, completed_at,
                evidence_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                current_index,
                stage_id,
                str(verifier_row.get("status") or "green"),
                now_text,
                now_text,
                _json_dump(verifier_row),
            ),
        )
        if stage_id == "public_completion":
            if surface_failures:
                conn.execute(
                    "UPDATE runs SET exact_successor = ?, updated_at = ? WHERE run_id = ?",
                    ("public_completion", now_text, run_id),
                )
                projection = {
                    **_projection_from_row(store, conn, store._run_row(conn, run_id)),
                    "stage": stage_id,
                    "status": "red",
                    "next_stage": "public_completion",
                    "successor": "public_completion",
                    "failures": surface_failures,
                }
                conn.commit()
                return projection
            conn.execute(
                """
                UPDATE runs
                SET status = ?, current_stage_index = ?, exact_successor = ?,
                    completed_at = ?, updated_at = ?
                WHERE run_id = ?
                """,
                ("completed", len(DIRECT_STAGES), "", now_text, now_text, run_id),
            )
            projection = {
                **_projection_from_row(store, conn, store._run_row(conn, run_id)),
                "stage": stage_id,
                "completed_stage": stage_id,
                "status": "completed",
                "next_stage": "",
                "successor": "",
            }
            conn.commit()
            return projection
        next_index = current_index + 1
        next_stage = _stage_for_index(next_index)
        conn.execute(
            """
            UPDATE runs
            SET current_stage_index = ?, exact_successor = ?, updated_at = ?
            WHERE run_id = ?
            """,
            (next_index, next_stage, now_text, run_id),
        )
        projection = {
            **_projection_from_row(store, conn, store._run_row(conn, run_id)),
            "stage": stage_id,
            "completed_stage": stage_id,
            "status": str(verifier_row.get("status") or "green"),
            "next_stage": next_stage,
            "successor": next_stage,
        }
        conn.commit()
        return projection


def advance_stage(
    store: DirectRunStore,
    *,
    run_id: str,
    stage_id: str,
    writer_lease: str,
    caller_result: Mapping[str, Any] | None = None,
    evidence: Mapping[str, Any] | None = None,
    semantic_oracle: Any = None,
    semantic_verifier: Any = None,
    observed_at: datetime | None = None,
    fencing_token: int | None = None,
) -> dict[str, Any]:
    verifier = semantic_verifier if semantic_verifier is not None else semantic_oracle
    return _advance_core(
        store,
        run_id=run_id,
        stage_id=stage_id,
        writer_lease=writer_lease,
        caller_result=caller_result,
        observed_surface=evidence,
        verifier=verifier,
        observed_at=observed_at,
        fencing_token=fencing_token,
    )


def run_exact_successor(
    store: DirectRunStore,
    *,
    run_id: str,
    writer_lease: str,
    caller_result: Mapping[str, Any] | None = None,
    observed_surface: Mapping[str, Any] | None = None,
    semantic_verifier: Any = None,
    observed_at: datetime | None = None,
    stage_id: str | None = None,
    handlers: Mapping[str, Callable[..., Mapping[str, Any]]] | None = None,
    _registered_consumer: Any = None,
    fencing_token: int | None = None,
) -> dict[str, Any]:
    state = inspect_run(store, run_id=run_id)
    current_stage = str(state.get("current_stage") or "")
    if not current_stage:
        return state
    requested = stage_id or current_stage
    if requested != current_stage:
        raise ValueError("stage order successor violation")
    handler_row: Mapping[str, Any] | None = None
    if handlers is not None:
        handler = handlers.get(current_stage)
        if handler is None:
            return {
                **state,
                "status": "missing_handler",
                "stage": current_stage,
                "next_stage": current_stage,
                "successor": current_stage,
                "advanced": False,
            }
        handler_row = handler(
            store=store,
            run=state,
            stage_id=current_stage,
            observed_at=observed_at or store.now(),
        )
    return _advance_core(
        store,
        run_id=run_id,
        stage_id=current_stage,
        writer_lease=writer_lease,
        caller_result=caller_result,
        observed_surface=handler_row if handler_row is not None else observed_surface,
        verifier=semantic_verifier,
        observed_at=observed_at,
        _registered_consumer=_registered_consumer,
        fencing_token=fencing_token,
    )


def migrate_run_v1_to_v2(
    store: DirectRunStore,
    *,
    run_id: str,
    run_intent: str = RUN_INTENT,
    manifest_id: str,
    observation_receipt: Mapping[str, Any],
    writer_lease: str,
) -> dict[str, Any]:
    """stage historyを変更せず、V1 runへV2 fieldsとappend-only receiptを追加する。"""
    if not re.fullmatch(r"[0-9a-f]{64}", manifest_id):
        raise ValueError("manifest_id_invalid")
    if observation_receipt.get("schemaVersion") != "NEWS_GRASP_RUN_OBSERVATION_V1":
        raise ValueError("observation_receipt_invalid")
    if not re.fullmatch(r"[0-9a-f]{40}", str(observation_receipt.get("sourceHead") or "")):
        raise ValueError("observation_source_head_invalid")
    if not isinstance(observation_receipt.get("exactWriteSet"), list) or not observation_receipt.get("exactWriteSet"):
        raise ValueError("observation_exact_write_set_invalid")
    now = store.now()
    with closing(store.connect()) as conn:
        columns = {str(item[1]) for item in conn.execute("PRAGMA table_info(runs)").fetchall()}
        raw = conn.execute("SELECT run_id,issue_date,cwd,writer_lease,status,lease_until FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if raw is None:
            raise KeyError(run_id)
        if str(raw[3]) != writer_lease or str(raw[4]) not in {"active", "executing"} or (_parse_time(str(raw[5])) or now) < now:
            raise PermissionError("stale writer lease fenced")
        required_binding = {
            "runId": run_id,
            "issueDate": str(raw[1]),
            "runIntent": run_intent,
        }
        if any(str(observation_receipt.get(key) or "") != value for key, value in required_binding.items()):
            raise ValueError("observation_receipt_binding_invalid")
        source_cwd = Path(str(raw[2])).resolve(strict=False)
        observed_cwd_value = str(observation_receipt.get("cwd") or "")
        if not observed_cwd_value or observation_receipt.get("dirty") is not False:
            raise ValueError("observation_clean_cwd_required")
        observed_cwd_raw = Path(os.path.abspath(observed_cwd_value))
        _reject_reparse_chain(observed_cwd_raw, reason="observation_cwd_reparse_forbidden")
        target_cwd = observed_cwd_raw.resolve(strict=True)
        if os.path.normcase(str(observed_cwd_raw)) != os.path.normcase(observed_cwd_value):
            raise ValueError("observation_cwd_not_canonical")
        if str(observation_receipt.get("manifestId") or "") != manifest_id:
            raise ValueError("observation_manifest_id_mismatch")
        runtime_state = observation_receipt.get("runtimeState")
        if (
            not isinstance(runtime_state, Mapping)
            or os.path.normcase(str(Path(str(runtime_state.get("root") or "")).resolve(strict=False)))
            != os.path.normcase(str(store.state_root.resolve(strict=False)))
            or runtime_state.get("dbExists") is not True
        ):
            raise ValueError("observation_runtime_state_binding_invalid")
        from tools.news_grasp_publish_contract import load_manifest, verify_manifest

        canonical_manifest = load_manifest(target_cwd, required_binding["issueDate"])
        if (
            canonical_manifest.get("manifestId") != manifest_id
            or canonical_manifest.get("runId") != run_id
            or canonical_manifest.get("runIntent") != run_intent
            or list(observation_receipt.get("exactWriteSet") or []) != list(canonical_manifest.get("exactWriteSet") or [])
            or verify_manifest(canonical_manifest, repo_root=target_cwd, require_files=True).get("ok") is not True
        ):
            raise ValueError("observation_manifest_binding_invalid")
        if "runtime_schema" in columns:
            existing = store._run_row(conn, run_id)
        else:
            existing = None
        if existing is not None and str(existing["runtime_schema"] or "") == RUNTIME_SCHEMA_V2:
            if (
                str(existing["run_intent"] or "") == run_intent
                and str(existing["manifest_id"] or "") == manifest_id
                and Path(str(existing["cwd"])).resolve(strict=False) == target_cwd
                and _json_load(existing["observation_receipt_json"], {})
                == dict(observation_receipt)
            ):
                return inspect_run(store, run_id=run_id)
            raise ValueError("runtime_v2_binding_drift")
    backup = store.db_path.with_name(
        f"{store.db_path.name}.pre-v2-{now.strftime('%Y%m%dT%H%M%S%f%z')}.bak"
    )
    with closing(store.connect()) as source, closing(sqlite3.connect(backup)) as destination:
        locked = source.execute("SELECT writer_lease,status,lease_until FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if locked is None or str(locked[0]) != writer_lease or str(locked[1]) not in {"active", "executing"}:
            raise PermissionError("stale writer lease fenced")
        source.backup(destination)
        if str(destination.execute("PRAGMA integrity_check").fetchone()[0]).casefold() != "ok":
            backup.unlink(missing_ok=True)
            raise RuntimeError("migration_backup_integrity_red")
        after_backup = source.execute("SELECT writer_lease,status,lease_until FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if after_backup is None or tuple(after_backup) != tuple(locked):
            backup.unlink(missing_ok=True)
            raise PermissionError("migration_backup_source_changed")
    store._init_db()  # noqa: SLF001 - backup precedes schema mutation.
    with closing(store.connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = store._run_row(conn, run_id)
        _verify_writer(row, writer_lease, now)
        prior_schema = str(row["runtime_schema"] or RUNTIME_SCHEMA)
        receipt = {
            "schemaVersion": "NEWS_GRASP_DIRECT_RUNTIME_MIGRATION_RECEIPT_V1",
            "runId": run_id,
            "fromSchema": prior_schema,
            "toSchema": RUNTIME_SCHEMA_V2,
            "runIntent": run_intent,
            "manifestId": manifest_id,
            "stageHistoryPreserved": True,
            "sourceCwd": str(source_cwd),
            "targetCwd": str(target_cwd),
            "backupPath": str(backup),
            "migratedAt": _iso(now),
        }
        prior_failures = _json_load(row["surface_failures"], [])
        typed_issues = _json_load(row["typed_issues_json"], [])
        for failure in prior_failures:
            item = dict(failure) if isinstance(failure, Mapping) else {"reasonCode": str(failure)}
            item.setdefault("surface", str(item.get("surface") or "public_completion"))
            item.setdefault("status", "blocked")
            item.setdefault("evidenceRef", "pre_v2_surface_failure")
            item["minimalSuccessor"] = "public_completion"
            typed_issues = _append_unique(typed_issues, item)
        conn.execute(
            """
            UPDATE runs
            SET runtime_schema = ?, run_intent = ?, manifest_id = ?, cwd = ?,
                migration_receipt_json = ?, observation_receipt_json = ?,
                typed_issues_json = ?, status = ?, exact_successor = ?, updated_at = ?
            WHERE run_id = ?
            """,
            (
                RUNTIME_SCHEMA_V2,
                run_intent,
                manifest_id,
                str(target_cwd),
                _json_dump(receipt),
                _json_dump(dict(observation_receipt)),
                _json_dump(typed_issues),
                "active" if int(row["current_stage_index"]) < len(DIRECT_STAGES) else row["status"],
                "public_completion" if int(row["current_stage_index"]) == len(DIRECT_STAGES) - 1 else row["exact_successor"],
                _iso(now),
                run_id,
            ),
        )
        conn.execute(
            "INSERT INTO runtime_migrations (run_id,from_schema,to_schema,receipt_json,migrated_at) VALUES (?,?,?,?,?)",
            (run_id, prior_schema, RUNTIME_SCHEMA_V2, _json_dump(receipt), _iso(now)),
        )
        conn.commit()
    return inspect_run(store, run_id=run_id)


def rebind_runtime_manifest(
    store: DirectRunStore,
    *,
    run_id: str,
    previous_manifest_id: str,
    manifest_id: str,
    repo_root: str | Path,
    writer_lease: str,
) -> dict[str, Any]:
    """V2 runのmanifestをcleanな再観測へCASでappend-only再束縛する。"""
    if not re.fullmatch(r"[0-9a-f]{64}", previous_manifest_id):
        raise ValueError("previous_manifest_id_invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", manifest_id):
        raise ValueError("manifest_id_invalid")
    if previous_manifest_id == manifest_id:
        raise ValueError("manifest_rebinding_identity_unchanged")
    now = store.now()
    with closing(store.connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = store._run_row(conn, run_id)
        _verify_writer(row, writer_lease, now)
        if str(row["runtime_schema"] or "") != RUNTIME_SCHEMA_V2:
            raise ValueError("runtime_v2_required")
        if str(row["manifest_id"] or "") != previous_manifest_id:
            raise ValueError("runtime_previous_manifest_binding_mismatch")
        if int(row["current_stage_index"]) != len(DIRECT_STAGES) - 1 or str(row["exact_successor"] or "") != "public_completion":
            raise ValueError("runtime_public_completion_successor_required")
        required_binding = {
            "runId": run_id,
            "issueDate": str(row["issue_date"]),
            "runIntent": str(row["run_intent"]),
        }
        observed_cwd_raw = Path(os.path.abspath(repo_root))
        _reject_reparse_chain(observed_cwd_raw, reason="observation_cwd_reparse_forbidden")
        observed_cwd = observed_cwd_raw.resolve(strict=True)
        bound_cwd = Path(str(row["cwd"])).resolve(strict=True)
        if os.path.normcase(str(observed_cwd)) != os.path.normcase(str(bound_cwd)):
            raise ValueError("observation_cwd_binding_mismatch")
        from tools.news_grasp_execution_receipt import capture_observation
        from tools.news_grasp_direct_completion import _up_to_date_observation
        from tools.news_grasp_publish_contract import load_manifest, manifest_path, verify_manifest

        canonical_manifest = load_manifest(observed_cwd, required_binding["issueDate"])
        if (
            canonical_manifest.get("manifestId") != manifest_id
            or canonical_manifest.get("runId") != run_id
            or canonical_manifest.get("runIntent") != required_binding["runIntent"]
            or verify_manifest(canonical_manifest, repo_root=observed_cwd, require_files=True).get("ok") is not True
        ):
            raise ValueError("observation_manifest_binding_invalid")
        observation_receipt = capture_observation(
            repo_root=observed_cwd,
            purpose="runtime-manifest-rebinding",
            run_id=run_id,
            run_intent=required_binding["runIntent"],
            issue_date=required_binding["issueDate"],
            manifest_path=manifest_path(observed_cwd, required_binding["issueDate"]),
            runtime_state_root=store.state_root,
        )
        remote_observation = _up_to_date_observation(observed_cwd, "origin", "main")
        if (
            observation_receipt.get("schemaVersion") != "NEWS_GRASP_RUN_OBSERVATION_V1"
            or observation_receipt.get("dirty") is not False
            or observation_receipt.get("manifestId") != manifest_id
            or list(observation_receipt.get("exactWriteSet") or []) != list(canonical_manifest.get("exactWriteSet") or [])
            or remote_observation.get("ok") is not True
            or observation_receipt.get("sourceHead") != remote_observation.get("head")
            or remote_observation.get("head") != remote_observation.get("remoteHead")
        ):
            raise ValueError("consumer_owned_manifest_observation_red")
        now_text = _iso(now)
        receipt = {
            "schemaVersion": "NEWS_GRASP_DIRECT_RUNTIME_MANIFEST_REBINDING_RECEIPT_V1",
            "runId": run_id,
            "issueDate": required_binding["issueDate"],
            "runIntent": required_binding["runIntent"],
            "previousManifestId": previous_manifest_id,
            "manifestId": manifest_id,
            "sourceHead": str(observation_receipt.get("sourceHead") or ""),
            "remoteHead": str(remote_observation.get("remoteHead") or ""),
            "stageHistoryPreserved": True,
            "minimalSuccessor": "public_completion",
            "reboundAt": now_text,
        }
        updated = conn.execute(
            """UPDATE runs
               SET manifest_id=?, observation_receipt_json=?, lease_until=?, updated_at=?
               WHERE run_id=? AND writer_lease=? AND status IN ('active','executing')
                 AND current_stage_index=? AND exact_successor='public_completion'
                 AND manifest_id=? AND updated_at=?""",
            (
                manifest_id,
                _json_dump(dict(observation_receipt)),
                _iso(now + store.lease_ttl),
                now_text,
                run_id,
                writer_lease,
                len(DIRECT_STAGES) - 1,
                previous_manifest_id,
                row["updated_at"],
            ),
        ).rowcount
        if updated != 1:
            raise PermissionError("manifest_rebinding_cas_conflict")
        conn.execute(
            """INSERT INTO runtime_manifest_rebindings
               (run_id,previous_manifest_id,manifest_id,observation_receipt_json,receipt_json,rebound_at)
               VALUES (?,?,?,?,?,?)""",
            (run_id, previous_manifest_id, manifest_id, _json_dump(dict(observation_receipt)), _json_dump(receipt), now_text),
        )
        conn.commit()
    return inspect_run(store, run_id=run_id)


def record_runtime_checkpoint(
    store: DirectRunStore,
    *,
    run_id: str,
    checkpoint_minute: int,
    elapsed_minutes: float,
) -> dict[str, Any]:
    """45/75/90 checkpointをexactly-onceで記録する。"""
    if checkpoint_minute not in {45, 75, 90}:
        raise ValueError("checkpoint_minute_invalid")
    with closing(store.connect()) as conn:
        changed = conn.execute(
            "INSERT OR IGNORE INTO runtime_checkpoints (run_id,checkpoint_minute,elapsed_minutes,recorded_at) VALUES (?,?,?,?)",
            (run_id, checkpoint_minute, float(elapsed_minutes), _iso(store.now())),
        ).rowcount
        conn.commit()
    return {
        "ok": True,
        "recorded": changed == 1,
        "checkpointMinute": checkpoint_minute,
        "optionalHighCostFrozen": elapsed_minutes >= 75,
        "sloDebt": elapsed_minutes > 90,
        "continuePublicCriticalSuccessor": True,
    }


def _require_registered_public_context(
    store: DirectRunStore,
    *,
    semantic_verifier: Any,
    repo_root: str | Path | None,
    public_base_url: str | None,
    remote: str,
    branch: str,
    wait_sec: int,
    poll_sec: int,
) -> None:
    if store.test_only_allow_semantic_verifier:
        return
    if semantic_verifier is not None:
        raise PermissionError("semantic_verifier_injection_test_only")
    if repo_root is None or not public_base_url:
        raise ValueError("registered_public_verifier_context_required")
    expected_url = "https://hidepon-umg.github.io/News-Grasp/"
    if str(public_base_url).rstrip("/") + "/" != expected_url:
        raise ValueError("public_base_url_not_canonical")
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if not local_app_data:
        raise EnvironmentError("localappdata_missing")
    _validate_public_operation(remote=remote, branch=branch, wait_sec=wait_sec, poll_sec=poll_sec)
    store.bind_production_runtime()


def _validate_public_operation(*, remote: str, branch: str, wait_sec: int, poll_sec: int) -> None:
    if remote != "origin" or branch != "main":
        raise ValueError("public_git_target_not_canonical")
    if isinstance(wait_sec, bool) or isinstance(poll_sec, bool):
        raise ValueError("public_probe_timing_invalid")
    if wait_sec != 0 and not 5 <= wait_sec <= 900:
        raise ValueError("public_probe_wait_out_of_policy")
    if not 5 <= poll_sec <= 120:
        raise ValueError("public_probe_poll_out_of_policy")
    if wait_sec > 0 and poll_sec > wait_sec:
        raise ValueError("public_probe_poll_exceeds_wait")


def probe_public_completion(
    store: DirectRunStore,
    *,
    run_id: str,
    semantic_verifier: Any = None,
    repo_root: str | Path | None = None,
    public_base_url: str | None = None,
    remote: str = "origin",
    branch: str = "main",
    wait_sec: int = 0,
    poll_sec: int = 30,
    observation_nonce: str | None = None,
) -> dict[str, Any]:
    """工程0〜19完了を前提に、20を閉じる前のfresh public predicateを評価する。"""
    _require_registered_public_context(
        store,
        semantic_verifier=semantic_verifier,
        repo_root=repo_root,
        public_base_url=public_base_url,
        remote=remote,
        branch=branch,
        wait_sec=wait_sec,
        poll_sec=poll_sec,
    )
    state = inspect_run(store, run_id=run_id)
    failures: list[str] = []
    daily_receipts = list(state.get("daily_operations") or [])
    stage_history = list(state.get("stage_history") or [])
    current_stage = state.get("current_stage")
    if daily_receipts:
        expected_daily = [
            (index, operation_id)
            for index, operation_id in enumerate(DAILY_OPERATION_ORDER)
        ]
        observed_daily = [
            (int(item.get("operation_index")) if item.get("operation_index") is not None else -1, str(item.get("operation_id") or ""))
            for item in daily_receipts
            if isinstance(item, Mapping)
        ]
        if observed_daily != expected_daily or any(
            not isinstance(item, Mapping)
            or not isinstance(item.get("receipt"), Mapping)
            or item["receipt"].get("ok") is not True
            or str(item["receipt"].get("status") or "").casefold() != "completed"
            for item in daily_receipts
        ):
            failures.append("daily_operation_history_incomplete")
        if state.get("status") not in {"active", "executing", "finalizing"}:
            failures.append("daily_public_completion_status_invalid")
        if str(state.get("exact_successor") or "") != "public_completion":
            failures.append("daily_public_completion_successor_invalid")
    elif current_stage == "public_completion":
        if len(stage_history) != len(DIRECT_STAGES) - 1:
            failures.append("pre_public_stage_history_incomplete")
    elif state.get("status") in {"complete", "completed", "green"}:
        if len(stage_history) != len(DIRECT_STAGES):
            failures.append("stage_history_incomplete")
    else:
        failures.append("exact_successor_not_public_completion")
    verifier = semantic_verifier if semantic_verifier is not None else store.semantic_verifier
    if repo_root is not None and public_base_url:
        from tools.news_grasp_direct_completion import verify_direct_public_completion

        verifier_row = verify_direct_public_completion(
            repo_root=Path(repo_root),
            issue_date=str(state.get("issue_date") or ""),
            public_base_url=public_base_url,
            remote=remote,
            branch=branch,
            wait_sec=wait_sec,
            poll_sec=poll_sec,
            run_id=run_id,
            run_intent=str(state.get("run_intent") or RUN_INTENT),
            manifest_id=str(state.get("manifest_id") or ""),
        )
    elif verifier is not None:
        verifier_row = _call_verifier(
            verifier,
            "public_completion",
            run=state,
            caller_result={},
            observed_surface={},
        )
    else:
        verifier_row = {"ok": False, "status": "public_verifier_missing"}
    if verifier_row.get("ok") is not True:
        failures.extend(list(verifier_row.get("failures") or [str(verifier_row.get("status") or "public_semantic_red")]))
    else:
        failures.extend(_public_failures(verifier_row, str(state.get("issue_date") or "")))
    resolved_nonce = str(observation_nonce or uuid.uuid4().hex)
    return {
        "schemaVersion": PUBLIC_SCHEMA_V2,
        "ok": not failures,
        "status": "verified" if not failures else "blocked",
        "completion_mode": "direct_public_v2",
        "run_id": run_id,
        "runIntent": state.get("run_intent") if "run_intent" in state else RUN_INTENT,
        "manifestId": state.get("manifest_id") or "",
        "issue_date": state.get("issue_date"),
        "failures": failures,
        "public_surfaces": verifier_row.get("public_surfaces"),
        "verifier": verifier_row,
        "exact_successor": "public_completion",
        "freshnessBinding": {
            "runId": run_id,
            "generation": state.get("generation"),
            "updatedAt": state.get("updated_at"),
            "manifestId": state.get("manifest_id") or "",
            "issueDate": state.get("issue_date"),
            "runIntent": state.get("run_intent") if "run_intent" in state else RUN_INTENT,
            "observationNonce": resolved_nonce,
            "observedAt": _iso(store.now()),
        },
    }


def _mapping_first(value: Any, *keys: str, default: Any = None) -> Any:
    if not isinstance(value, Mapping):
        return default
    for key in keys:
        if key in value:
            return value[key]
    return default


def _unwrap_consumer_public_receipt(value: Any) -> dict[str, Any] | None:
    """consumer operation/producer receiptを同じtyped shapeへ解決する。"""

    if not isinstance(value, Mapping):
        return None
    for key in ("producer_receipt", "producerReceipt"):
        nested = value.get(key)
        if isinstance(nested, Mapping):
            return dict(nested)
    return dict(value)


def _consumer_receipt_hash(receipt: Mapping[str, Any]) -> str:
    return hashlib.sha256(_json_dump(dict(receipt)).encode("utf-8")).hexdigest()


def fencing_binding_hash(
    *,
    run_id: str,
    generation: int,
    writer_lease: str,
    fencing_token: int,
) -> str:
    """raw fencing capabilityをreceiptへ出さず同一writerへ束縛する。"""

    if not run_id or not writer_lease or int(generation) <= 0 or int(fencing_token) <= 0:
        raise ValueError("fencing_binding_identity_invalid")
    return hashlib.sha256(
        f"{run_id}\0{int(generation)}\0{writer_lease}\0{int(fencing_token)}".encode("utf-8")
    ).hexdigest()


def _daily_public_observation_receipt(
    *,
    store: DirectRunStore,
    row: sqlite3.Row,
    daily_rows: Sequence[sqlite3.Row],
    public_observation_receipt: Mapping[str, Any] | None,
    admitted_at: datetime,
) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    """Daily consumer receiptを検証し、観測・binding・hashを返す。

    この関数はnetwork verifierを呼ばない。公開観測のauthorityは、
    operation index 4へatomicに保存されたconsumer receiptだけである。
    """

    expected_rows = [
        (index, operation_id)
        for index, operation_id in enumerate(DAILY_OPERATION_ORDER)
    ]
    if len(daily_rows) != len(expected_rows):
        raise PermissionError("finalizer_daily_operation_count_red")
    observed_rows: list[tuple[int, str]] = []
    parsed_operations: list[dict[str, Any]] = []
    for item in daily_rows:
        raw_operation = _json_load(str(item[5]), None)
        if not isinstance(raw_operation, dict):
            raise PermissionError("finalizer_daily_operation_receipt_invalid")
        try:
            operation_index = int(item[1])
        except (TypeError, ValueError) as exc:
            raise PermissionError("finalizer_daily_operation_index_invalid") from exc
        # index 0は有効値であり、truthinessで-1へ落としてはいけない。
        observed_rows.append((operation_index, str(item[0])))
        parsed_operations.append(raw_operation)
        saved_hash = str(item[8] or "").strip().casefold()
        computed_hash = _consumer_receipt_hash(raw_operation)
        if not re.fullmatch(r"[0-9a-f]{64}", saved_hash) or saved_hash != computed_hash:
            raise PermissionError("finalizer_daily_operation_receipt_hash_mismatch")
        try:
            raw_operation_index = int(raw_operation.get("operation_index", -1))
        except (TypeError, ValueError) as exc:
            raise PermissionError("finalizer_daily_operation_binding_red") from exc
        if (
            raw_operation.get("ok") is not True
            or str(raw_operation.get("status") or "").casefold() != "completed"
            or str(raw_operation.get("run_id") or raw_operation.get("runId") or "") != str(row["run_id"])
            or str(raw_operation.get("operation_id") or raw_operation.get("operationId") or "") != str(item[0])
            or raw_operation_index != operation_index
            or str(raw_operation.get("input_hash") or raw_operation.get("inputHash") or "") != str(item[2])
            or str(raw_operation.get("handler_id") or raw_operation.get("handlerId") or "") != str(item[3])
        ):
            raise PermissionError("finalizer_daily_operation_receipt_binding_red")
    if observed_rows != expected_rows:
        raise PermissionError("finalizer_daily_operation_history_red")

    consumer_operation = parsed_operations[DAILY_OPERATION_ORDER.index("consumer_public_verification")]
    stored_consumer = _unwrap_consumer_public_receipt(consumer_operation.get("producer_receipt"))
    if stored_consumer is None:
        stored_consumer = _unwrap_consumer_public_receipt(consumer_operation.get("producerReceipt"))
    supplied_consumer = _unwrap_consumer_public_receipt(public_observation_receipt)
    if stored_consumer is None and supplied_consumer is None:
        raise PermissionError("finalizer_consumer_public_receipt_missing")
    if stored_consumer is not None and supplied_consumer is not None:
        if _json_dump(stored_consumer) != _json_dump(supplied_consumer):
            raise PermissionError("finalizer_consumer_public_receipt_binding_conflict")
    consumer = stored_consumer or supplied_consumer
    if consumer is None:
        raise PermissionError("finalizer_consumer_public_receipt_missing")
    if (
        consumer.get("schemaVersion") != CONSUMER_PUBLIC_VERIFICATION_RECEIPT_SCHEMA
        or consumer.get("ok") is not True
        or str(consumer.get("status") or "").casefold() != "verified"
    ):
        raise PermissionError("finalizer_consumer_public_receipt_invalid")
    observation = consumer.get("observation")
    if not isinstance(observation, Mapping) or observation.get("ok") is not True:
        raise PermissionError("finalizer_consumer_public_observation_invalid")
    binding = _mapping_first(consumer, "freshnessBinding", "freshness_binding")
    if not isinstance(binding, Mapping):
        binding = _mapping_first(observation, "freshnessBinding", "freshness_binding")
    if not isinstance(binding, Mapping):
        raise PermissionError("finalizer_consumer_freshness_binding_missing")
    binding = dict(binding)
    expected_run_id = str(_mapping_first(binding, "runId", "run_id", default="") or "")
    expected_issue_date = str(
        _mapping_first(binding, "issueDate", "issue_date", "date", default="") or ""
    )
    expected_run_intent = str(_mapping_first(binding, "runIntent", "run_intent", default="") or "")
    expected_manifest = str(_mapping_first(binding, "manifestId", "manifest_id", default="") or "").casefold()
    expected_generation = _mapping_first(binding, "generation", "generationId", "generation_id")
    expected_fencing_hash = str(
        _mapping_first(binding, "fencingBindingHash", "fencing_binding_hash", default="") or ""
    ).casefold()
    expected_updated = str(_mapping_first(binding, "updatedAt", "updated_at", default="") or "")
    observed_at_text = str(_mapping_first(binding, "observedAt", "observed_at", default="") or "")
    observed_at = _parse_time(observed_at_text)
    observation_nonce = str(
        _mapping_first(
            binding,
            "observationNonce",
            "observation_nonce",
            "observationToken",
            "observation_token",
            "nonce",
            "token",
            default="",
        )
        or _mapping_first(
            observation,
            "observationNonce",
            "observation_nonce",
            "observationToken",
            "observation_token",
            "nonce",
            "token",
            default="",
        )
        or consumer.get("observation_token")
        or consumer.get("observationToken")
        or ""
    ).strip()
    if not observation_nonce:
        raise PermissionError("finalizer_consumer_observation_nonce_missing")
    try:
        generation_matches = expected_generation is not None and int(expected_generation) == int(row["generation"])
        fencing_matches = expected_fencing_hash == fencing_binding_hash(
            run_id=str(row["run_id"]),
            generation=int(row["generation"]),
            writer_lease=str(row["writer_lease"]),
            fencing_token=int(_row_value(row, "fencing_token", 0) or 0),
        )
    except (TypeError, ValueError) as exc:
        raise PermissionError("finalizer_consumer_freshness_binding_invalid") from exc
    consumer_index = DAILY_OPERATION_ORDER.index("consumer_public_verification")
    previous_applied_at = str(daily_rows[consumer_index - 1][6] or "")
    consumer_applied_at = _parse_time(str(daily_rows[consumer_index][6] or ""))
    expected_updated_at = _parse_time(expected_updated)
    if (
        expected_run_id != str(row["run_id"])
        or expected_issue_date != str(row["issue_date"])
        or expected_run_intent != str(row["run_intent"] or "")
        or expected_manifest != str(row["manifest_id"] or "").casefold()
        or not expected_manifest
        or not generation_matches
        or not fencing_matches
        # consumer観測はexternal_publication適用後、consumer自身のreceipt適用前に行う。
        # そのため、後続二receiptでも進むrun.updated_atではなく、安定した直前operation
        # のapplied_atへ束縛する。
        or expected_updated != previous_applied_at
        or expected_updated_at is None
        or observed_at is None
        or consumer_applied_at is None
        or observed_at < expected_updated_at
        or observed_at > consumer_applied_at
        or observed_at > admitted_at
        or (admitted_at - observed_at).total_seconds() > 15 * 60
    ):
        raise PermissionError("finalizer_consumer_freshness_binding_invalid")

    atomic_operation = parsed_operations[DAILY_OPERATION_ORDER.index("atomic_completion")]
    atomic_producer = _unwrap_consumer_public_receipt(atomic_operation.get("producer_receipt"))
    if atomic_producer is None:
        atomic_producer = _unwrap_consumer_public_receipt(atomic_operation.get("producerReceipt"))
    if atomic_producer is not None:
        declared_hash = _mapping_first(
            atomic_producer,
            "consumer_receipt_hash",
            "consumerReceiptHash",
            "public_observation_receipt_hash",
            "publicObservationReceiptHash",
        )
        if declared_hash is not None and str(declared_hash).strip().casefold() != _consumer_receipt_hash(consumer):
            raise PermissionError("finalizer_atomic_consumer_receipt_hash_mismatch")
    return consumer, binding, _consumer_receipt_hash(consumer), expected_updated


def _finalize_daily_public_completion(
    store: DirectRunStore,
    *,
    run_id: str,
    writer_lease: str,
    public_observation_receipt: Mapping[str, Any] | None,
    semantic_verifier: Any = None,
    fencing_token: int | None = None,
) -> dict[str, Any]:
    """Daily六operationの保存済み公開観測だけを一度consumeして完了する。"""

    # Daily finalizerはconsumer operationが既に取得したfresh observationを
    # consumeするだけであり、ここからnetwork/semantic verifierを呼ばない。
    if not store.test_only_allow_semantic_verifier and semantic_verifier is not None:
        raise PermissionError("semantic_verifier_injection_test_only")
    _require_fencing_token(store, fencing_token)
    if fencing_token is None and not store.test_only_allow_semantic_verifier:
        raise PermissionError("fencing_token_required")
    store.ensure_runtime_schema()
    if not store.test_only_allow_semantic_verifier:
        # public URLの有無に依存せず、canonical runtime DB identityだけを
        # 確認する。公開観測自体は先行consumer operationの責務である。
        store.bind_production_runtime()
    finalizer_nonce = uuid.uuid4().hex
    admitted_row: sqlite3.Row | None = None
    admitted_stage_digest = ""
    admitted_updated_at = ""
    admitted_consumer_receipt: dict[str, Any] = {}
    admitted_consumer_hash = ""
    admission_updated_at = ""
    admission_at = store.now()
    with closing(store.connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = store._run_row(conn, run_id)
        now = store.now()
        _verify_writer(
            row,
            writer_lease,
            now,
            allowed_statuses={"active", "executing", "finalizing"},
            fencing_token=fencing_token,
        )
        daily_rows = conn.execute(
            """
            SELECT operation_id,operation_index,input_hash,handler_id,payload_json,receipt_json,
                   applied_at,fencing_token,receipt_hash
            FROM daily_operation_receipts WHERE run_id=? ORDER BY operation_index
            """,
            (run_id,),
        ).fetchall()
        admitted_stage_digest = hashlib.sha256(
            _json_dump([list(item) for item in daily_rows]).encode("utf-8")
        ).hexdigest()
        persisted_admission = _json_load(str(row["observation_receipt_json"] or "{}"), {})
        if str(row["status"] or "") == "finalizing" and str(row["finalization_nonce"] or ""):
            # admission commit後のprocess crashは、保存済みconsumer receiptを
            # 再観測せず同じnonce/hashでfinal transactionだけresumeする。
            if (
                not isinstance(persisted_admission, Mapping)
                or persisted_admission.get("schemaVersion")
                != "NEWS_GRASP_DAILY_FINALIZER_ADMISSION_V1"
                or str(persisted_admission.get("runId") or "") != run_id
                or str(persisted_admission.get("nonce") or "")
                != str(row["finalization_nonce"] or "")
                or str(persisted_admission.get("dailyReceiptDigest") or "")
                != admitted_stage_digest
                or str(persisted_admission.get("manifestId") or "")
                != str(row["manifest_id"] or "")
                or str(persisted_admission.get("admissionUpdatedAt") or "")
                != str(row["updated_at"] or "")
            ):
                conn.rollback()
                raise PermissionError("finalizer_resume_admission_receipt_invalid")
            resumed_at = _parse_time(str(persisted_admission.get("admittedAt") or ""))
            if resumed_at is None:
                conn.rollback()
                raise PermissionError("finalizer_resume_admitted_at_invalid")
            admitted_consumer_receipt, _binding, admitted_consumer_hash, admitted_updated_at = _daily_public_observation_receipt(
                store=store,
                row=row,
                daily_rows=daily_rows,
                public_observation_receipt=public_observation_receipt,
                admitted_at=resumed_at,
            )
            if admitted_consumer_hash != str(persisted_admission.get("consumerReceiptHash") or ""):
                conn.rollback()
                raise PermissionError("finalizer_resume_consumer_receipt_hash_mismatch")
            finalizer_nonce = str(row["finalization_nonce"] or "")
            admission_updated_at = str(row["updated_at"] or "")
            admission_at = resumed_at
            admitted_row = row
            conn.rollback()
        else:
            # row.updated_atはfinalizer admissionが書き換える前の値を保持する。
            # consumer freshness bindingはこの値へ束縛し、admission後の新しい
            # updated_atへ誤って再束縛しない。
            admitted_consumer_receipt, _binding, admitted_consumer_hash, admitted_updated_at = _daily_public_observation_receipt(
                store=store,
                row=row,
                daily_rows=daily_rows,
                public_observation_receipt=public_observation_receipt,
                admitted_at=now,
            )
            admitted_row = row
            now_text = _iso(now)
            admission_updated_at = now_text
            admission_at = now
            lease_until = _iso(now + store.lease_ttl)
            admission_receipt = {
                "schemaVersion": "NEWS_GRASP_DAILY_FINALIZER_ADMISSION_V1",
                "runId": run_id,
                "nonce": finalizer_nonce,
                "manifestId": str(row["manifest_id"] or ""),
                "dailyReceiptDigest": admitted_stage_digest,
                "consumerReceiptHash": admitted_consumer_hash,
                "consumerBindingUpdatedAt": admitted_updated_at,
                "admittedAt": now_text,
                "admissionUpdatedAt": now_text,
            }
            changed = conn.execute(
                """
                UPDATE runs SET status='finalizing', finalization_nonce=?, lease_until=?, updated_at=?,
                       observation_receipt_json=?
                WHERE run_id=? AND writer_lease=? AND fencing_token=?
                  AND status IN ('active','executing','finalizing')
                  AND finalization_nonce=''
                  AND updated_at=?
                """,
                (
                    finalizer_nonce,
                    lease_until,
                    now_text,
                    _json_dump(admission_receipt),
                    run_id,
                    writer_lease,
                    int(fencing_token if fencing_token is not None else _row_value(row, "fencing_token", 0) or 0),
                    row["updated_at"],
                ),
            ).rowcount
            if changed != 1:
                conn.rollback()
                raise PermissionError("finalizer_admission_cas_conflict")
            conn.commit()

    def restore_after_red() -> None:
        with closing(store.connect()) as restore:
            restore.execute("BEGIN IMMEDIATE")
            token = int(fencing_token if fencing_token is not None else _row_value(admitted_row, "fencing_token", 0) or 0)
            restored = restore.execute(
                """
                UPDATE runs SET status='active',finalization_nonce='',exact_successor='public_completion',
                       observation_receipt_json='{}',updated_at=?
                WHERE run_id=? AND writer_lease=? AND fencing_token=? AND status='finalizing' AND finalization_nonce=?
                """,
                (_iso(store.now()), run_id, writer_lease, token, finalizer_nonce),
            ).rowcount
            if restored == 1:
                restore.commit()
            else:
                restore.rollback()

    try:
        with closing(store.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = store._run_row(conn, run_id)
            now = store.now()
            _verify_writer(
                row,
                writer_lease,
                now,
                allowed_statuses={"finalizing"},
                fencing_token=fencing_token,
            )
            current_daily_rows = conn.execute(
                """
                SELECT operation_id,operation_index,input_hash,handler_id,payload_json,receipt_json,
                       applied_at,fencing_token,receipt_hash
                FROM daily_operation_receipts WHERE run_id=? ORDER BY operation_index
                """,
                (run_id,),
            ).fetchall()
            current_digest = hashlib.sha256(
                _json_dump([list(item) for item in current_daily_rows]).encode("utf-8")
            ).hexdigest()
            # 再度network verifierを実行せず、保存行のhash/bindingだけを
            # admission時のsnapshotに照合する。15分freshnessもfinal時点で
            # 再計算するが、意味predicate自体は再評価しない。
            current_consumer, binding, current_consumer_hash, _ = _daily_public_observation_receipt(
                store=store,
                row=admitted_row,
                daily_rows=current_daily_rows,
                public_observation_receipt=admitted_consumer_receipt,
                admitted_at=admission_at,
            )
            if (
                current_digest != admitted_stage_digest
                or current_consumer_hash != admitted_consumer_hash
                or str(row["updated_at"]) != admission_updated_at
                or str(row["finalization_nonce"] or "") != finalizer_nonce
                or str(row["exact_successor"] or "") != "public_completion"
                or str(row["manifest_id"] or "") != str(admitted_row["manifest_id"] or "")
            ):
                conn.rollback()
                raise PermissionError("finalizer_freshness_cas_conflict")
            # _daily_public_observation_receipt checked binding.updatedAt against
            # admitted_row. The current run row is intentionally newer because
            # the admission transaction refreshed updated_at.
            prior = _json_load(row["surface_failures"], [])
            typed = _json_load(row["typed_issues_json"], [])
            post_publish_issues = _json_load(row["post_publish_issue_list"], [])
            if prior:
                typed = _append_unique(
                    typed,
                    {
                        "surface": "public_completion",
                        "reasonCode": "prior_surface_failures_resolved_by_stored_consumer_observation",
                        "status": "verified",
                        "evidenceRef": CONSUMER_PUBLIC_VERIFICATION_RECEIPT_SCHEMA,
                        "priorFailures": prior,
                    },
                )
            for issue in current_consumer.get("post_publish_issue_list") or []:
                if isinstance(issue, Mapping):
                    post_publish_issues = _append_unique(post_publish_issues, dict(issue))
            now_text = _iso(now)
            completion_start = _parse_time(str(_row_value(row, "scheduler_trigger_at", "")))
            if completion_start is None:
                completion_start = _parse_time(str(row["started_at"])) or now
            completion_elapsed = max(0.0, (now - completion_start).total_seconds())
            _close_open_timing_event(
                conn,
                run_id=run_id,
                ended_at=now_text,
                evidence={"phase": "public_completion", "event": "completion", "nonce": finalizer_nonce},
            )
            consumed_observation = {
                "schemaVersion": CONSUMER_PUBLIC_VERIFICATION_RECEIPT_SCHEMA,
                "receipt": current_consumer,
                "receiptHash": current_consumer_hash,
                "source": "consumer_public_verification_receipt",
                "consumedAt": now_text,
            }
            changed = conn.execute(
                """
                UPDATE runs SET surface_failures='[]',typed_issues_json=?,post_publish_issue_list=?,
                   observation_receipt_json=?,status='completed',current_stage_index=?,exact_successor='',
                   finalization_nonce='',completed_at=?,completion_elapsed_seconds=COALESCE(completion_elapsed_seconds,?),
                   completion_elapsed_at=COALESCE(NULLIF(completion_elapsed_at,''),?),updated_at=?
                WHERE run_id=? AND writer_lease=? AND fencing_token=? AND status='finalizing'
                  AND finalization_nonce=? AND updated_at=? AND manifest_id=?
                """,
                (
                    _json_dump(typed),
                    _json_dump(post_publish_issues),
                    _json_dump(consumed_observation),
                    len(DIRECT_STAGES),
                    now_text,
                    completion_elapsed,
                    now_text,
                    now_text,
                    run_id,
                    writer_lease,
                    int(fencing_token if fencing_token is not None else _row_value(row, "fencing_token", 0) or 0),
                    finalizer_nonce,
                    row["updated_at"],
                    row["manifest_id"],
                ),
            ).rowcount
            if changed != 1:
                conn.rollback()
                raise PermissionError("finalizer_atomic_cas_conflict")
            result = _projection_from_row(store, conn, store._run_row(conn, run_id))
            result.pop("writer_lease", None)
            conn.commit()
    except Exception:
        restore_after_red()
        raise
    return {
        **result,
        "ok": True,
        "publicProbe": admitted_consumer_receipt,
        "public_probe_source": "consumer_public_verification_receipt",
        "completion_elapsed_seconds": result.get("completion_elapsed_seconds"),
        "freshnessBinding": binding,
    }


def finalize_public_completion(
    store: DirectRunStore,
    *,
    run_id: str,
    writer_lease: str,
    semantic_verifier: Any = None,
    repo_root: str | Path | None = None,
    public_base_url: str | None = None,
    remote: str = "origin",
    branch: str = "main",
    wait_sec: int = 0,
    poll_sec: int = 30,
    exact_successor: str,
    fencing_token: int | None = None,
    public_observation_receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Dailyは保存済みconsumer観測、legacyは一回のfresh probeで工程20を閉じる。"""
    # Daily経路ではconsumer_public_verificationが唯一のnetwork/semantic
    # verifier実行主体であり、finalizerはそのreceiptを一回だけconsumeする。
    # 先にoperation存在だけをreadし、legacy direct stageには従来probeを残す。
    if not store.test_only_allow_semantic_verifier:
        store.bind_production_runtime()
    store.ensure_runtime_schema()
    with closing(store.connect()) as daily_probe_conn:
        store._run_row(daily_probe_conn, run_id)
        has_daily_receipt = daily_probe_conn.execute(
            "SELECT 1 FROM daily_operation_receipts WHERE run_id=? LIMIT 1",
            (run_id,),
        ).fetchone() is not None
    if has_daily_receipt:
        return _finalize_daily_public_completion(
            store,
            run_id=run_id,
            writer_lease=writer_lease,
            public_observation_receipt=public_observation_receipt,
            semantic_verifier=semantic_verifier,
            fencing_token=fencing_token,
        )
    if public_observation_receipt is not None:
        raise ValueError("public_observation_receipt_daily_only")
    _require_registered_public_context(
        store,
        semantic_verifier=semantic_verifier,
        repo_root=repo_root,
        public_base_url=public_base_url,
        remote=remote,
        branch=branch,
        wait_sec=wait_sec,
        poll_sec=poll_sec,
    )
    _require_fencing_token(store, fencing_token)
    if exact_successor != "public_completion":
        raise ValueError("exact_successor_invalid")
    nonce = uuid.uuid4().hex
    admitted_stage_digest = ""
    admitted_stage_warnings: list[dict[str, Any]] = []
    finalizing_updated_at = ""
    with closing(store.connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = store._run_row(conn, run_id)
        now = store.now()
        _verify_writer(row, writer_lease, now, allowed_statuses={"active", "executing", "finalizing"}, fencing_token=fencing_token)
        daily_rows = conn.execute(
            """
            SELECT operation_id,operation_index,input_hash,handler_id,payload_json,receipt_json,
                   applied_at,fencing_token,receipt_hash
            FROM daily_operation_receipts WHERE run_id=? ORDER BY operation_index
            """,
            (run_id,),
        ).fetchall()
        daily_mode = bool(daily_rows)
        if daily_mode:
            expected_daily = [(index, operation_id) for index, operation_id in enumerate(DAILY_OPERATION_ORDER)]
            observed_daily = [(int(item[1]), str(item[0])) for item in daily_rows]
            if (
                observed_daily != expected_daily
                or any(str(_json_load(str(item[5]), {}).get("status") or "").casefold() != "completed" for item in daily_rows)
                or any(_json_load(str(item[5]), {}).get("ok") is not True for item in daily_rows)
                or str(row["exact_successor"] or "") != "public_completion"
                or str(row["status"] or "") not in {"active", "executing", "finalizing"}
            ):
                conn.rollback()
                raise PermissionError("finalizer_daily_operation_admission_red")
            if any(int(item[7] or 0) != int(_row_value(row, "fencing_token", 0) or 0) for item in daily_rows):
                conn.rollback()
                raise PermissionError("finalizer_daily_fencing_admission_red")
            admitted_stage_digest = hashlib.sha256(
                _json_dump([list(item) for item in daily_rows]).encode("utf-8")
            ).hexdigest()
        elif (
            int(row["current_stage_index"]) != len(DIRECT_STAGES) - 1
            or str(row["exact_successor"] or "") != "public_completion"
            or not re.fullmatch(r"[0-9a-f]{64}", str(row["manifest_id"] or ""))
        ):
            conn.rollback()
            raise PermissionError("finalizer_admission_red")
        stages = conn.execute("SELECT stage_index,stage_id,status,evidence_json FROM stages WHERE run_id=? ORDER BY stage_index", (run_id,)).fetchall()
        if not daily_mode:
            expected = [(index, stage_id) for index, stage_id in enumerate(DIRECT_STAGES[:-1])]
            observed = [(int(item[0]), str(item[1])) for item in stages]
            stage_evidence = [_json_load(str(item[3]), None) for item in stages]
            for item, evidence_row in zip(stages, stage_evidence, strict=True):
                if str(item[2]).casefold() != "verified_with_warnings":
                    continue
                warnings = list(evidence_row.get("post_publish_issue_list") or []) if isinstance(evidence_row, Mapping) else []
                if not warnings or any(
                    not isinstance(warning, Mapping)
                    or str(warning.get("status") or "").casefold() != "warning"
                    or not warning.get("surface")
                    or not warning.get("reasonCode")
                    or not warning.get("evidenceRef")
                    for warning in warnings
                ):
                    conn.rollback()
                    raise PermissionError("finalizer_stage_warning_admission_red")
                admitted_stage_warnings.extend(dict(warning) for warning in warnings)
            if (
                observed != expected
                or any(str(item[2]).casefold() not in {"green", "verified", "verified_with_warnings"} for item in stages)
                or any(not isinstance(item, Mapping) or not item for item in stage_evidence)
            ):
                conn.rollback()
                raise PermissionError("finalizer_stage_history_admission_red")
            admitted_stage_digest = hashlib.sha256(_json_dump([list(item) for item in stages]).encode("utf-8")).hexdigest()
        now_text = _iso(now)
        finalizing_updated_at = now_text
        lease_until = _iso(now + store.lease_ttl)
        changed = conn.execute(
            """UPDATE runs SET status='finalizing', finalization_nonce=?, lease_until=?, updated_at=?
               WHERE run_id=? AND writer_lease=? AND fencing_token=? AND status=? AND updated_at=?""",
            (nonce, lease_until, now_text, run_id, writer_lease, int(fencing_token if fencing_token is not None else _row_value(row, "fencing_token", 0) or 0), row["status"], row["updated_at"]),
        ).rowcount
        if changed != 1:
            conn.rollback()
            raise PermissionError("finalizer_admission_cas_conflict")
        conn.commit()

    def restore_after_red() -> None:
        with closing(store.connect()) as restore:
            restore.execute("BEGIN IMMEDIATE")
            restore_token = int(fencing_token or 0)
            if restore_token <= 0:
                token_row = restore.execute("SELECT fencing_token FROM runs WHERE run_id=?", (run_id,)).fetchone()
                restore_token = int(token_row[0] or 0) if token_row is not None else 0
            restored = restore.execute(
                """UPDATE runs SET status='active', finalization_nonce='', exact_successor='public_completion', updated_at=?
                   WHERE run_id=? AND writer_lease=? AND fencing_token=? AND status='finalizing' AND finalization_nonce=?""",
                (_iso(store.now()), run_id, writer_lease, restore_token, nonce),
            ).rowcount
            if restored == 1:
                restore.commit()
            else:
                restore.rollback()

    try:
        # verifierは同一predicateを二度観測しない。waitを伴うこの一回の
        # fresh probeだけを公開consumerのauthorityとしてfinal transactionへ
        # 束縛する。
        fresh_probe = probe_public_completion(
            store,
            run_id=run_id,
            semantic_verifier=semantic_verifier,
            repo_root=repo_root,
            public_base_url=public_base_url,
            remote=remote,
            branch=branch,
            wait_sec=wait_sec,
            poll_sec=poll_sec,
            observation_nonce=nonce,
        )
        if fresh_probe.get("ok") is not True:
            restore_after_red()
            return {**inspect_run(store, run_id=run_id), "ok": False, "status": "needs_successor", "failures": fresh_probe["failures"], "exact_successor": "public_completion"}
    except Exception:
        restore_after_red()
        raise
    try:
        with closing(store.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = store._run_row(conn, run_id)
            now = store.now()
            _verify_writer(row, writer_lease, now, allowed_statuses={"finalizing"}, fencing_token=fencing_token)
            binding = fresh_probe.get("freshnessBinding") or {}
            current_daily_rows = conn.execute(
                """
                SELECT operation_id,operation_index,input_hash,handler_id,payload_json,receipt_json,
                       applied_at,fencing_token,receipt_hash
                FROM daily_operation_receipts WHERE run_id=? ORDER BY operation_index
                """,
                (run_id,),
            ).fetchall()
            current_daily_mode = bool(current_daily_rows)
            current_bind_rows = current_daily_rows if current_daily_mode else conn.execute(
                "SELECT stage_index,stage_id,status,evidence_json FROM stages WHERE run_id=? ORDER BY stage_index",
                (run_id,),
            ).fetchall()
            current_stage_digest = hashlib.sha256(_json_dump([list(item) for item in current_bind_rows]).encode("utf-8")).hexdigest()
            expected_manifest = str(binding.get("manifestId") or binding.get("manifest_id") or "")
            expected_run_id = str(binding.get("runId") or binding.get("run_id") or "")
            expected_run_intent = str(binding.get("runIntent") or binding.get("run_intent") or "")
            expected_updated = str(binding.get("updatedAt") or binding.get("updated_at") or "")
            expected_issue_date = str(binding.get("issueDate") or binding.get("issue_date") or "")
            expected_generation = binding.get("generation")
            expected_observation_nonce = str(
                binding.get("observationNonce")
                or binding.get("observation_nonce")
                or ""
            )
            observed_at = _parse_time(
                str(binding.get("observedAt") or binding.get("observed_at") or "")
            )
            binding_ok = (
                expected_manifest == str(row["manifest_id"] or "")
                and bool(expected_manifest)
                and expected_run_id == run_id
                and expected_run_intent == str(row["run_intent"] or "")
                and expected_issue_date == str(row["issue_date"] or "")
                and expected_generation is not None
                and int(row["generation"]) == int(expected_generation)
                # legacy probeはfinalizing遷移後のrun snapshotへ束縛する。
                # 現在行も同じ遷移時刻から変化していないことを照合する。
                and expected_updated == finalizing_updated_at
                and str(row["updated_at"] or "") == finalizing_updated_at
                and expected_observation_nonce == nonce
                and observed_at is not None
            )
            if (
                str(row["finalization_nonce"] or "") != nonce
                or current_stage_digest != admitted_stage_digest
                or not binding_ok
                or (not current_daily_mode and int(row["current_stage_index"]) != len(DIRECT_STAGES) - 1)
                or str(row["exact_successor"] or "") != "public_completion"
            ):
                conn.rollback()
                raise PermissionError("finalizer_freshness_cas_conflict")
            prior = _json_load(row["surface_failures"], [])
            typed = _json_load(row["typed_issues_json"], [])
            post_publish_issues = _json_load(row["post_publish_issue_list"], [])
            for warning in admitted_stage_warnings:
                post_publish_issues = _append_unique(post_publish_issues, warning)
            if prior:
                typed = _append_unique(typed, {"surface": "public_completion", "reasonCode": "prior_surface_failures_resolved_by_fresh_probe", "status": "verified", "evidenceRef": PUBLIC_SCHEMA_V2, "priorFailures": prior})
            for issue in fresh_probe.get("verifier", {}).get("post_publish_issue_list") or []:
                if isinstance(issue, Mapping):
                    post_publish_issues = _append_unique(post_publish_issues, dict(issue))
            now_text = _iso(now)
            evidence = {**fresh_probe.get("verifier", {}), "freshnessBinding": binding}
            if not current_daily_mode:
                conn.execute(
                    "INSERT OR REPLACE INTO stages (run_id,stage_index,stage_id,status,started_at,completed_at,evidence_json) VALUES (?,?,?,?,?,?,?)",
                    (run_id, len(DIRECT_STAGES) - 1, "public_completion", "verified", now_text, now_text, _json_dump(evidence)),
                )
            completion_start = _parse_time(str(_row_value(row, "scheduler_trigger_at", ""))) or _parse_time(str(row["started_at"])) or now
            completion_elapsed = max(0.0, (now - completion_start).total_seconds())
            _close_open_timing_event(
                conn,
                run_id=run_id,
                ended_at=now_text,
                evidence={"phase": "public_completion", "event": "completion", "nonce": nonce},
            )
            changed = conn.execute(
                """UPDATE runs SET surface_failures='[]', typed_issues_json=?, post_publish_issue_list=?, status='completed',
                   current_stage_index=?, exact_successor='', finalization_nonce='', completed_at=?,
                   completion_elapsed_seconds=COALESCE(completion_elapsed_seconds,?), completion_elapsed_at=COALESCE(NULLIF(completion_elapsed_at,''),?), updated_at=?
                   WHERE run_id=? AND writer_lease=? AND fencing_token=? AND status='finalizing' AND finalization_nonce=?
                     AND updated_at=? AND manifest_id=?""",
                (_json_dump(typed), _json_dump(post_publish_issues), len(DIRECT_STAGES), now_text, completion_elapsed, now_text, now_text, run_id, writer_lease, int(fencing_token if fencing_token is not None else _row_value(row, "fencing_token", 0) or 0), nonce, row["updated_at"], row["manifest_id"]),
            ).rowcount
            if changed != 1:
                conn.rollback()
                raise PermissionError("finalizer_atomic_cas_conflict")
            if not current_daily_mode:
                final_stages = conn.execute("SELECT stage_index,stage_id FROM stages WHERE run_id=? ORDER BY stage_index", (run_id,)).fetchall()
                if [(int(item[0]), str(item[1])) for item in final_stages] != [(index, stage_id) for index, stage_id in enumerate(DIRECT_STAGES)]:
                    conn.rollback()
                    raise PermissionError("finalizer_stage_history_postcondition_red")
            result = _projection_from_row(store, conn, store._run_row(conn, run_id))
            result.pop("writer_lease", None)
            conn.commit()
    except Exception:
        restore_after_red()
        raise
    return {**result, "ok": True, "publicProbe": fresh_probe}


def verify_public_completion(
    store: DirectRunStore,
    *,
    run_id: str,
    semantic_verifier: Any = None,
    completion_receipt: Mapping[str, Any] | None = None,
    repo_root: str | Path | None = None,
    public_base_url: str | None = None,
    remote: str = "origin",
    branch: str = "main",
    wait_sec: int = 0,
    poll_sec: int = 30,
) -> dict[str, Any]:
    del completion_receipt
    if store.test_only_allow_semantic_verifier:
        pass
    elif public_base_url:
        _require_registered_public_context(
            store,
            semantic_verifier=semantic_verifier,
            repo_root=repo_root,
            public_base_url=public_base_url,
            remote=remote,
            branch=branch,
            wait_sec=wait_sec,
            poll_sec=poll_sec,
        )
    else:
        if semantic_verifier is not None:
            raise PermissionError("semantic_verifier_injection_test_only")
        store.bind_production_runtime()
    state = inspect_run(store, run_id=run_id)
    failures: list[str] = []
    daily_receipts = list(state.get("daily_operations") or [])
    if daily_receipts:
        expected_daily = [
            (index, operation_id)
            for index, operation_id in enumerate(DAILY_OPERATION_ORDER)
        ]
        observed_daily = [
            (int(item.get("operation_index")) if item.get("operation_index") is not None else -1, str(item.get("operation_id") or ""))
            for item in daily_receipts
            if isinstance(item, Mapping)
        ]
        if observed_daily != expected_daily or any(
            not isinstance(item, Mapping)
            or item.get("receipt", {}).get("ok") is not True
            or item.get("receipt", {}).get("status") != "completed"
            for item in daily_receipts
        ):
            failures.append("daily_operation_history_incomplete")
    elif len(state.get("stage_history") or []) != len(DIRECT_STAGES):
        failures.append("stage_history_incomplete")
    if state.get("status") not in {"complete", "completed", "green"}:
        failures.append("run_not_completed")
    surface_failures = list(state.get("surface_failures") or [])
    if surface_failures:
        failures.extend(
            str(item.get("surface") or item.get("reason") or item)
            if isinstance(item, Mapping)
            else str(item)
            for item in surface_failures
        )
    public_stage = None
    for item in state.get("stage_history") or []:
        if isinstance(item, Mapping) and item.get("stage") == "public_completion":
            evidence = item.get("evidence")
            if isinstance(evidence, Mapping):
                public_stage = dict(evidence)
            break
    verifier = semantic_verifier if semantic_verifier is not None else store.semantic_verifier
    if repo_root is not None and public_base_url:
        from tools.news_grasp_direct_completion import verify_direct_public_completion

        verifier_row = verify_direct_public_completion(
            repo_root=Path(repo_root),
            issue_date=str(state.get("issue_date") or ""),
            public_base_url=public_base_url,
            remote=remote,
            branch=branch,
            wait_sec=wait_sec,
            poll_sec=poll_sec,
        )
    elif (
        state.get("status") in {"complete", "completed", "green"}
        and public_stage is not None
        and not surface_failures
        and verifier is None
    ):
        verifier_row = public_stage
    elif verifier is not None:
        verifier_row = _call_verifier(
            verifier,
            "public_completion",
            run=state,
            caller_result={},
            observed_surface={},
        )
    else:
        verifier_row = {"ok": False, "status": "public_verifier_missing"}
    if verifier_row.get("ok") is not True:
        failures.extend(list(verifier_row.get("failures") or [str(verifier_row.get("status") or "public_semantic_red")]))
    else:
        failures.extend(_public_failures(verifier_row, str(state.get("issue_date") or "")))
    return {
        "schemaVersion": PUBLIC_SCHEMA,
        "ok": not failures,
        "completion_mode": "direct_public_v1",
        "issue_date": state.get("issue_date"),
        "run_id": run_id,
        "state_root": str(store.state_root),
        "status": "green" if not failures else "red",
        "failures": failures,
        "stage_history": state.get("stage_history"),
        "title_status": state.get("title_status"),
        "actual_title": state.get("actual_title"),
        "post_publish_issue_list": state.get("post_publish_issue_list"),
        "slo": state.get("slo"),
    }


def validate_installed_automation_semantics(path: str | Path | None = None) -> dict[str, Any]:
    config_path = (
        Path(path)
        if path is not None
        else Path.home() / ".codex" / "automations" / AUTOMATION_ID / "automation.toml"
    )
    failures: list[str] = []
    try:
        value = tomllib.loads(config_path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {"schemaVersion": "NEWS_GRASP_DIRECT_AUTOMATION_CONFIG_V1", "ok": False, "failures": ["installed_config_missing"], "path": str(config_path)}
    except (UnicodeError, tomllib.TOMLDecodeError) as exc:
        return {"schemaVersion": "NEWS_GRASP_DIRECT_AUTOMATION_CONFIG_V1", "ok": False, "failures": [f"installed_config_invalid:{exc}"], "path": str(config_path)}
    python_path = Path(r"C:\Users\hidek\AppData\Local\Programs\Python\Python312\python.exe")
    python_sha256 = ""
    if not python_path.is_file():
        failures.append("python312_executable_missing")
    else:
        try:
            digest = hashlib.sha256()
            with python_path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            python_sha256 = digest.hexdigest()
            expected_hash = os.environ.get("NEWS_GRASP_PYTHON312_SHA256", "").strip().casefold()
            if expected_hash and python_sha256 != expected_hash:
                failures.append("python312_executable_hash_mismatch")
        except OSError as exc:
            failures.append(f"python312_executable_unreadable:{type(exc).__name__}")
    if value.get("id") != AUTOMATION_ID:
        failures.append("automation_id_invalid")
    if value.get("status") != "ACTIVE":
        failures.append("automation_inactive")
    if value.get("kind") != "cron":
        failures.append("automation_kind_invalid")
    if str(value.get("model") or "").casefold() != "gpt-5.6-luna":
        failures.append("automation_model_not_luna")
    if value.get("reasoning_effort") != "max":
        failures.append("automation_reasoning_not_max")
    if str(value.get("rrule") or "").upper() != "RRULE:FREQ=DAILY;BYHOUR=6;BYMINUTE=0;BYSECOND=0":
        failures.append("automation_schedule_not_0600")
    created_at = value.get("created_at")
    updated_at = value.get("updated_at")
    if not isinstance(created_at, int) or isinstance(created_at, bool) or created_at <= 0:
        failures.append("automation_app_schema_created_at_invalid")
    if not isinstance(updated_at, int) or isinstance(updated_at, bool) or updated_at <= 0:
        failures.append("automation_app_schema_updated_at_invalid")
    if (
        isinstance(created_at, int)
        and not isinstance(created_at, bool)
        and isinstance(updated_at, int)
        and not isinstance(updated_at, bool)
        and updated_at < created_at
    ):
        failures.append("automation_app_schema_timestamp_order_invalid")
    cwds = value.get("cwds")
    if not isinstance(cwds, list) or not cwds:
        failures.append("automation_cwds_missing")
    else:
        try:
            cwd_values = [
                Path(str(item)).expanduser().resolve(strict=True)
                for item in cwds
            ]
        except (OSError, TypeError, ValueError):
            failures.append("automation_cwds_invalid")
        else:
            try:
                current_repo = Path.cwd().resolve(strict=True)
            except (OSError, RuntimeError, ValueError):
                current_repo = None
            same_project = False
            if current_repo is not None:
                try:
                    from tools import sync_news_grasp_codex_automation as automation_sync

                    same_project = any(
                        automation_sync._same_path(item, current_repo)  # noqa: SLF001
                        or automation_sync._same_git_repository(item, current_repo)  # noqa: SLF001
                        for item in cwd_values
                    )
                except (ImportError, OSError, ValueError):
                    same_project = current_repo in cwd_values
            if current_repo is not None and not same_project:
                failures.append("automation_cwds_current_repo_missing")
    prompt = str(value.get("prompt") or "")
    required_prompt_parts = (
        "$news-grasp-direct-mainline",
        "YY/MM/DD",
        "title_status",
        "title_status=already_ok",
        "already_ok",
        "post_publish_issue_list",
        "direct completion guard",
        "static_check",
        "scoped_contract_unit",
        "current_issue_integration",
        "external_publication",
        "consumer_public_verification",
        "atomic_completion",
        "protected_release_reexecution_forbidden",
    )
    for part in required_prompt_parts:
        if part not in prompt:
            failures.append(f"prompt_missing:{part}")
    completion_phrase = "完全な品質で記事公開するまで完了してはならない"
    if prompt.count(completion_phrase) != 3:
        failures.append("prompt_completion_phrase_count_invalid")
    if "[automation]" in prompt or "prompt =" in prompt:
        failures.append("prompt_toml_replacement_detected")
    if "public incomplete のまま最終応答しないでください" in prompt:
        failures.append("prompt_external_blocker_boundary_ambiguous")
    if "最初に `python -m tools.news_grasp_direct_runtime start" in prompt:
        failures.append("prompt_title_runtime_order_ambiguous")
    app_db_result = None
    snapshot_results: list[dict[str, Any]] = []
    if path is None:
        try:
            from tools import sync_news_grasp_codex_automation as automation_sync

            app_db_result = automation_sync.validate_app_db_semantics(
                Path.home() / ".codex" / "sqlite" / "codex-dev.db",
                repo_root=Path.cwd(),
            )
            for snapshot in automation_sync._snapshot_targets(Path.cwd(), None):  # noqa: SLF001
                snapshot_results.append(
                    automation_sync.validate_semantics(snapshot, repo_root=Path.cwd())
                )
        except Exception as exc:
            app_db_result = {
                "ok": False,
                "failures": [f"app_db_validation_unavailable:{exc}"],
            }
        if app_db_result.get("ok") is not True:
            for failure in app_db_result.get("failures") or ["app_db_invalid"]:
                failures.append(f"app_db:{failure}")
        for snapshot_result in snapshot_results:
            if snapshot_result.get("ok") is not True:
                for failure in snapshot_result.get("failures") or ["snapshot_invalid"]:
                    failures.append(f"snapshot:{failure}")
    return {
        "schemaVersion": "NEWS_GRASP_DIRECT_AUTOMATION_CONFIG_V1",
        "ok": not failures,
        "path": str(config_path),
        "failures": failures,
        "model": value.get("model"),
        "reasoning_effort": value.get("reasoning_effort"),
        "rrule": value.get("rrule"),
        "created_at": created_at,
        "updated_at": updated_at,
        "app_db": app_db_result,
        "snapshots": snapshot_results,
        "python_executable": str(python_path),
        "python_sha256": python_sha256,
        "prompt_completion_phrase_count": prompt.count(completion_phrase),
    }


def _load_cli_mapping(*, evidence_json: str | None, evidence_file: Path | None) -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    if evidence_file is not None:
        if not evidence_file.is_file():
            raise ValueError("evidence_file_missing")
        if evidence_file.stat().st_size > MAX_CLI_EVIDENCE_BYTES:
            raise ValueError("evidence_file_too_large")
        loaded = json.loads(evidence_file.read_text(encoding="utf-8-sig"))
        if not isinstance(loaded, Mapping):
            raise ValueError("evidence_file_not_object")
        evidence.update(dict(loaded))
    if evidence_json:
        loaded = json.loads(evidence_json)
        if not isinstance(loaded, Mapping):
            raise ValueError("evidence_json_not_object")
        evidence.update(dict(loaded))
    return evidence


def _emit_cli(result: Mapping[str, Any]) -> None:
    """machine-readable stdoutをBOMなし・UTF-8・一行JSONに固定する。"""

    text = json.dumps(dict(result), ensure_ascii=False, separators=(",", ":"))
    if "\r" in text or "\n" in text:
        raise ValueError("cli_result_multiline_forbidden")
    sys.stdout.write(text + "\n")


_REGISTERED_STAGE_CONSUMERS = {
    "title_control": "news_grasp_title_control.validate_title",
    "issue_inventory": "publish_inventory.scheduled_category_ids",
    "category_collection": "validate_daily_quality.validate_jsonl_source_freshness",
    "evidence_dedup_freshness": "validate_daily_quality.validate_dedup_annotation_present",
    "category_digest": "validate_daily_quality.validate_issue_schedule",
    "reporter_validation": "validate_generation_quality.validate_generation_quality",
    "articles_jsonl": "validate_daily_quality.validate_jsonl_source_freshness",
    "summary": "validate_daily_quality.validate_summary_hero",
    "daily_audio": "news_grasp_direct_completion._audio_projection",
    "deepdive_article": "deepdive_quality.audit_issue",
    "deepdive_quality": "deepdive_quality.audit_issue",
    "html_docs": "news_grasp_direct_completion._required_docs",
    "daily_quality": "news_grasp_direct_completion._daily_quality",
    "youtube_podcasts": "daily_self_heal.verify_podcast",
    "playlist": "news_grasp_direct_completion._podcast_rows",
    "notification": "news_grasp_direct_completion._notification",
    "distribution": "news_grasp_direct_completion._required_distribution",
    "publish_status": "news_grasp_direct_completion._publish_status",
    "commit_push": "news_grasp_direct_completion._up_to_date_observation",
    "pages_verify": "news_grasp_direct_completion._pages_workflow_observation",
    "public_completion": "news_grasp_direct_completion.verify_direct_public_completion",
}


def _registered_stage_verifier(
    stage_id: str,
    *,
    run: Mapping[str, Any],
    evidence: Mapping[str, Any],
    repo_root: Path | None,
    public_base_url: str | None,
    remote: str,
    branch: str,
    wait_sec: int,
    poll_sec: int,
) -> dict[str, Any]:
    """callerのokを捨て、固定stage→consumer mappingから観測を再生成する。"""
    _validate_public_operation(remote=remote, branch=branch, wait_sec=wait_sec, poll_sec=poll_sec)
    consumer_id = _REGISTERED_STAGE_CONSUMERS.get(stage_id)
    if stage_id == "title_control":
        from tools.news_grasp_title_control import validate_title

        issue_date = str(run.get("issue_date") or "")
        status = str(evidence.get("title_status") or "")
        actual = str(evidence.get("actual_title") or "")
        failures: list[str] = []
        valid = validate_title(actual, issue_date).get("ok") is True
        if status in TITLE_SUCCESS and not valid:
            failures.append("title_exact_semantic_red")
        elif status not in TITLE_SUCCESS | TITLE_NONBLOCKING:
            failures.append("title_status_invalid")
        observation = {"title_status": status, "actual_title": actual, "post_publish_issue_list": list(evidence.get("post_publish_issue_list") or [])}
        return {"ok": not failures, "status": "green" if not failures else "red", "consumerId": consumer_id, "issue_date": issue_date, "failures": failures, "observation": observation, **observation}
    if consumer_id is None or repo_root is None:
        return {"ok": False, "status": "registered_stage_consumer_missing", "failures": [f"registered_stage_consumer_missing:{stage_id}"]}
    issue_date = str(run.get("issue_date") or "")
    run_id = str(run.get("run_id") or "")
    run_intent = str(run.get("run_intent") or RUN_INTENT)
    try:
        from datetime import date
        from tools import deepdive_quality, validate_daily_quality, validate_generation_quality
        from tools.news_grasp_direct_completion import (
            _audio_projection, _daily_quality, _notification, _pages_workflow_observation,
            _podcast_rows, _publish_status, _required_distribution, _required_docs,
            _up_to_date_observation, resolve_trusted_repo_root, verify_direct_public_completion,
        )
        from tools.news_grasp_title_control import validate_title
        from tools.publish_inventory import scheduled_category_ids

        root = resolve_trusted_repo_root(repo_root)
        issue = date.fromisoformat(issue_date)
        failures: list[str] = []
        observation: Any = None
        if stage_id == "title_control":
            status = str(evidence.get("title_status") or "")
            actual = str(evidence.get("actual_title") or "")
            valid = validate_title(actual, issue_date).get("ok") is True
            if status in TITLE_SUCCESS and not valid:
                failures.append("title_exact_semantic_red")
            elif status not in TITLE_SUCCESS | TITLE_NONBLOCKING:
                failures.append("title_status_invalid")
            observation = {"title_status": status, "actual_title": actual, "post_publish_issue_list": list(evidence.get("post_publish_issue_list") or [])}
        elif stage_id == "issue_inventory":
            categories = scheduled_category_ids(issue_date)
            observation = {"scheduledCategoryIds": categories}
            if not categories:
                failures.append("scheduled_category_inventory_empty")
        elif stage_id in {"category_collection", "articles_jsonl"}:
            failures.extend(str(item) for item in validate_daily_quality.validate_jsonl_source_freshness(root / "data" / "articles.jsonl", issue))
        elif stage_id == "evidence_dedup_freshness":
            failures.extend(str(item) for item in validate_daily_quality.validate_dedup_annotation_present(root / "data" / "articles.jsonl", issue))
            failures.extend(str(item) for item in validate_daily_quality.validate_jsonl_source_freshness(root / "data" / "articles.jsonl", issue))
        elif stage_id == "category_digest":
            failures.extend(str(item) for item in validate_daily_quality.validate_issue_schedule(root / "digest", issue))
            failures.extend(str(item) for item in validate_daily_quality.validate_digest_article_counts(root / "digest", issue))
        elif stage_id == "reporter_validation":
            generated = validate_generation_quality.validate_generation_quality(root, issue_date)
            failures.extend(item.code for item in generated.errors)
            observation = {"exitCode": generated.exit_code, "warningCount": len(generated.warnings)}
        elif stage_id == "summary":
            # hero / reflection の正本は frontmatter を持つ digest Markdown。
            # docs 側は生成済み公開HTMLであり、validate_daily_quality の
            # frontmatter 検証へ渡すと常に欠落扱いになる。
            summary = root / "digest" / "Summary" / f"{issue_date}.md"
            failures.extend(validate_daily_quality.validate_summary_hero(summary))
            failures.extend(validate_daily_quality.validate_summary_emphasis(summary))
        elif stage_id == "daily_audio":
            observation = _audio_projection(root, issue_date, audio_type="daily", run_id=run_id, run_intent=run_intent)
            if observation.get("ok") is not True:
                failures.extend(observation.get("reasonCodes") or ["daily_audio_projection_red"])
        elif stage_id in {"deepdive_article", "deepdive_quality"}:
            observation = deepdive_quality.audit_issue(
                repo_root=root,
                issue_date=issue_date,
                include_corpus=False,
                require_rendered_public=stage_id == "deepdive_quality",
                route="production_generation",
            )
            if observation.get("status") != "Green" or observation.get("issueCodes") or observation.get("issues"):
                failures.extend(observation.get("issueCodes") or observation.get("issues") or ["deepdive_quality_red"])
        elif stage_id == "html_docs":
            observation = _required_docs(root, issue_date)
            if observation.get("ok") is not True:
                failures.extend(observation.get("missing") or observation.get("security_errors") or ["html_docs_red"])
        elif stage_id == "daily_quality":
            observation = _daily_quality(root, issue_date)
            if observation.get("ok") is not True:
                failures.append("daily_quality_red")
        elif stage_id == "youtube_podcasts":
            from tools.daily_self_heal import verify_podcast
            daily = verify_podcast(date=issue_date, state_path=root / "build" / "youtube-podcast" / "uploads.json", wait_sec=wait_sec, poll_sec=poll_sec, expected_title=f"News-Grasp Daily News Briefing {issue_date}")
            deep = verify_podcast(date=issue_date, state_path=root / "build" / "youtube-podcast-deepdive" / "uploads.json", wait_sec=wait_sec, poll_sec=poll_sec, expected_title=f"News-Grasp DeepDive Dialogue {issue_date}")
            observation = {"daily": daily, "deepdive": deep}
            if daily.get("ok") is not True or deep.get("ok") is not True:
                failures.append("youtube_podcasts_red")
        elif stage_id == "playlist":
            observation = _podcast_rows(root, issue_date, wait_sec=wait_sec, poll_sec=poll_sec, run_id=run_id, run_intent=run_intent).get("playlist", {})
            if observation.get("ok") is not True:
                failures.append("playlist_red")
        elif stage_id == "notification":
            observation = _notification(root, issue_date, run_id=run_id, run_intent=run_intent)
            if observation.get("ok") is not True:
                failures.extend(observation.get("failures") or ["notification_red"])
        elif stage_id == "distribution":
            from tools.news_grasp_publish_contract import load_manifest
            observation = _required_distribution(root, issue_date, manifest=load_manifest(root, issue_date), run_id=run_id, run_intent=run_intent)
            if observation.get("ok") is not True:
                failures.extend(observation.get("missing") or observation.get("failures") or ["distribution_red"])
        elif stage_id == "publish_status":
            observation = _publish_status(root, issue_date)
            if observation.get("ok") is not True:
                failures.append("publish_status_red")
        elif stage_id == "commit_push":
            observation = _up_to_date_observation(root, remote, branch)
            if observation.get("ok") is not True:
                failures.append("remote_commit_red")
        elif stage_id == "pages_verify":
            from tools.news_grasp_publish_contract import load_manifest
            remote_row = _up_to_date_observation(root, remote, branch)
            manifest = load_manifest(root, issue_date)
            observation = _pages_workflow_observation(remote_head=str(remote_row.get("remoteHead") or ""), manifest_id=str(manifest.get("manifestId") or ""), issue_date=issue_date)
            if remote_row.get("ok") is not True or observation.get("ok") is not True:
                failures.append("pages_verify_red")
        else:
            if not public_base_url:
                failures.append("public_base_url_missing")
            else:
                observation = verify_direct_public_completion(repo_root=root, issue_date=issue_date, public_base_url=public_base_url, remote=remote, branch=branch, wait_sec=wait_sec, poll_sec=poll_sec, run_id=run_id, run_intent=run_intent, manifest_id=str(run.get("manifest_id") or ""), cache_bust=True)
                if observation.get("ok") is not True:
                    failures.extend(observation.get("failures") or ["public_completion_red"])
        return {
            "ok": not failures,
            "status": "green" if not failures else "red",
            "consumerId": consumer_id,
            "issue_date": issue_date,
            "failures": sorted(set(str(item) for item in failures)),
            "observation": observation,
            **(observation if stage_id == "title_control" and isinstance(observation, Mapping) else {}),
        }
    except Exception as exc:  # noqa: BLE001 - registered consumer failure is typed Red.
        return {"ok": False, "status": "registered_consumer_error", "consumerId": consumer_id, "failures": [f"registered_consumer_error:{stage_id}:{exc}"]}


def _cli_stage_verifier(
    stage_id: str,
    *,
    run: Mapping[str, Any],
    evidence: Mapping[str, Any],
    repo_root: Path | None,
    public_base_url: str | None,
    remote: str,
    branch: str,
    wait_sec: int,
    poll_sec: int,
) -> dict[str, Any]:
    return _registered_stage_verifier(
        stage_id,
        run=run,
        evidence=evidence,
        repo_root=repo_root,
        public_base_url=public_base_url,
        remote=remote,
        branch=branch,
        wait_sec=wait_sec,
        poll_sec=poll_sec,
    )


def _canonical_daily_state_root() -> Path:
    """Windows Known Folderから唯一のproduction state rootを解決する。"""

    if os.name != "nt":
        raise OSError("daily_windows_known_folder_required")
    import ctypes

    class _Guid(ctypes.Structure):
        _fields_ = [
            ("data1", ctypes.c_uint32),
            ("data2", ctypes.c_uint16),
            ("data3", ctypes.c_uint16),
            ("data4", ctypes.c_ubyte * 8),
        ]

    folder_id = _Guid.from_buffer_copy(
        uuid.UUID("f1b32785-6fba-4fcf-9d55-7b8e7f157091").bytes_le
    )
    output = ctypes.c_wchar_p()
    status = ctypes.windll.shell32.SHGetKnownFolderPath(
        ctypes.byref(folder_id),
        0,
        None,
        ctypes.byref(output),
    )
    if status != 0 or not output.value:
        raise OSError(f"daily_known_folder_unavailable:{status}")
    try:
        return (Path(output.value) / "News-Grasp" / "direct-mainline").resolve(
            strict=False
        )
    finally:
        ctypes.windll.ole32.CoTaskMemFree(output)


def _contains_writer_capability(value: Any) -> bool:
    """machine receiptへwriter capabilityを投影しない。"""

    if isinstance(value, Mapping):
        for key, nested in value.items():
            canonical_key = str(key).replace("_", "").casefold()
            if canonical_key in {
                "writerlease",
                "fencingtoken",
                "continuationcapability",
            }:
                return True
            if _contains_writer_capability(nested):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(_contains_writer_capability(item) for item in value)
    return False


def _daily_red(reason: str, **extra: Any) -> dict[str, Any]:
    return {
        "schemaVersion": DAILY_SEQUENCE_SCHEMA,
        "ok": False,
        "status": "red",
        "failures": [reason],
        "humanImpact": {
            "noFocusTheft": True,
            "noAutoOpen": True,
            "noUserMonitoring": True,
        },
        **extra,
    }


def run_daily_mainline(
    *,
    repo_root: Path,
    state_root: Path,
    issue_date: str,
    scheduler_trigger_at: str,
) -> dict[str, Any]:
    """Daily六operationをdirect runtime process内で順序実行する。"""

    from tools import news_grasp_daily_gate as daily

    protected_failure = daily.protected_release_failure(
        repo_root=repo_root,
        issue_date=issue_date,
        require_contract_integrity=True,
    )
    if protected_failure:
        return _daily_red(
            protected_failure,
            issue_date=issue_date,
            protected_release=daily.PROTECTED_RELEASE,
            protected_release_policy=daily.PROTECTED_RELEASE_POLICY,
            exact_successor="explicit_new_release_authority_required",
        )
    identity = daily.resolve_daily_identity_context(
        repo_root=repo_root,
        issue_date=issue_date,
    )
    if identity.get("ok") is not True:
        return _daily_red("daily_identity_preflight_red", identity=identity)
    store = DirectRunStore(state_root)
    receipts = daily.run_daily_sequence(
        store=store,
        cwd=repo_root,
        issue_date=issue_date,
        run_intent=RUN_INTENT,
        automation_id=AUTOMATION_ID,
        scheduler_trigger_at=scheduler_trigger_at,
        manifest_id=str(identity.get("manifest_id") or ""),
        manifest_reservation_id=str(identity.get("manifest_reservation_id") or ""),
        source_baseline=str(identity.get("source_baseline") or ""),
        runtime_generation=RUNTIME_SCHEMA_V2,
        remote_base_sha=str(identity.get("remote_base_sha") or ""),
        allowed_side_effect_ids=list(identity.get("allowed_side_effect_ids") or ()),
    )
    final = receipts[-1] if receipts else {}
    result = {
        "schemaVersion": DAILY_SEQUENCE_SCHEMA,
        "ok": (
            len(receipts) == len(daily.DAILY_OPERATIONS)
            and final.get("ok") is True
            and final.get("status") == "completed"
        ),
        "status": final.get("status") or "red",
        "run_id": str((receipts[0] if receipts else {}).get("run_id") or ""),
        "operation_count": len(receipts),
        "operation_receipts": receipts,
        "failures": list(final.get("failures") or ()),
        "humanImpact": {
            "noFocusTheft": True,
            "noAutoOpen": True,
            "noUserMonitoring": True,
        },
    }
    if _contains_writer_capability(result):
        return _daily_red("daily_writer_capability_projection_violation")
    return result


def _run_daily_cli() -> dict[str, Any]:
    from tools import news_grasp_daily_gate as daily

    if os.path.normcase(os.path.abspath(sys.executable)) != os.path.normcase(
        os.path.abspath(daily.DAILY_PYTHON)
    ):
        return _daily_red(
            "fixed_python_3_12_required",
            expected_python=daily.DAILY_PYTHON,
            observed_python=sys.executable,
        )
    issue_date = (
        os.environ.get("NEWS_GRASP_ISSUE_DATE", "").strip()
        or daily._issue_date_default()
    )
    repo_root = Path(os.environ.get("NEWS_GRASP_REPO_ROOT", str(Path.cwd())))
    scheduler_trigger_at = (
        os.environ.get("NEWS_GRASP_SCHEDULER_TRIGGER_AT", "").strip()
        or f"{issue_date}T06:00:00+09:00"
    )
    try:
        return run_daily_mainline(
            repo_root=repo_root,
            state_root=_canonical_daily_state_root(),
            issue_date=issue_date,
            scheduler_trigger_at=scheduler_trigger_at,
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        return _daily_red(f"daily_sequence_error:{type(exc).__name__}:{exc}")


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("daily")
    validate = sub.add_parser("validate-installed")
    validate.add_argument("--path", type=Path, default=None)

    start = sub.add_parser("start")
    start.add_argument("--state-root", type=Path, required=True)
    start.add_argument("--cwd", type=Path, default=Path.cwd())
    start.add_argument(
        "--installed-config",
        type=Path,
        default=None,
        help="Codex App installed automation TOML。未指定なら実installed定義とApp DBを検査する。",
    )
    start.add_argument(
        "--issue-date",
        default=None,
        help="対象日 YYYY-MM-DD。未指定なら Asia/Tokyo の当日を使う。",
    )
    start.add_argument("--automation-id", default=AUTOMATION_ID)
    start.add_argument("--run-intent", default=RUN_INTENT)
    start.add_argument("--manifest-id", default="")
    start.add_argument("--manifest-reservation-id", default="")
    start.add_argument("--scheduler-trigger-at", default=None)
    start.add_argument("--source-baseline", default="")
    start.add_argument("--runtime-generation", default=RUNTIME_SCHEMA_V2)
    start.add_argument("--remote-base-sha", default="")
    start.add_argument("--allowed-side-effect-id", action="append", default=[])

    inspect = sub.add_parser("inspect")
    inspect.add_argument("--state-root", type=Path, required=True)
    inspect.add_argument("--run-id", required=True)

    relocate = sub.add_parser("relocate-state")
    relocate.add_argument("--source-state-root", type=Path, required=True)
    relocate.add_argument("--source-repo-root", type=Path, required=True)
    relocate.add_argument("--state-root", type=Path, required=True)
    relocate.add_argument("--run-id", required=True)
    relocate.add_argument("--writer-lease", required=True)
    relocate.add_argument("--new-writer-lease", required=True)
    relocate.add_argument("--recovery-authority", required=True)

    migrate = sub.add_parser("migrate-v2")
    migrate.add_argument("--state-root", type=Path, required=True)
    migrate.add_argument("--run-id", required=True)
    migrate.add_argument("--manifest-id", required=True)
    migrate.add_argument("--observation-file", type=Path, required=True)
    migrate.add_argument("--run-intent", default=RUN_INTENT)
    migrate.add_argument("--writer-lease", required=True)

    rebind = sub.add_parser("rebind-manifest")
    rebind.add_argument("--state-root", type=Path, required=True)
    rebind.add_argument("--run-id", required=True)
    rebind.add_argument("--previous-manifest-id", required=True)
    rebind.add_argument("--manifest-id", required=True)
    rebind.add_argument("--repo-root", type=Path, required=True)
    rebind.add_argument("--writer-lease", required=True)

    advance = sub.add_parser("advance")
    advance.add_argument("--state-root", type=Path, required=True)
    advance.add_argument("--run-id", required=True)
    advance.add_argument("--writer-lease", required=True)
    advance.add_argument("--evidence-json", default=None)
    advance.add_argument("--evidence-file", type=Path, default=None)
    advance.add_argument("--repo-root", type=Path, default=None)
    advance.add_argument("--public-base-url", default="")
    advance.add_argument("--remote", default="origin")
    advance.add_argument("--branch", default="main")
    advance.add_argument("--wait-sec", type=int, default=0)
    advance.add_argument("--poll-sec", type=int, default=30)
    advance.add_argument("--fencing-token", type=int, default=None)

    verify = sub.add_parser("verify-public")
    verify.add_argument("--state-root", type=Path, required=True)
    verify.add_argument("--run-id", required=True)
    verify.add_argument("--repo-root", type=Path, default=None)
    verify.add_argument("--public-base-url", default="")
    verify.add_argument("--remote", default="origin")
    verify.add_argument("--branch", default="main")
    verify.add_argument("--wait-sec", type=int, default=0)
    verify.add_argument("--poll-sec", type=int, default=30)

    probe = sub.add_parser("probe-public")
    probe.add_argument("--state-root", type=Path, required=True)
    probe.add_argument("--run-id", required=True)
    probe.add_argument("--repo-root", type=Path, required=True)
    probe.add_argument("--public-base-url", required=True)
    probe.add_argument("--remote", default="origin")
    probe.add_argument("--branch", default="main")
    probe.add_argument("--wait-sec", type=int, default=0)
    probe.add_argument("--poll-sec", type=int, default=30)
    probe.add_argument("--cache-bust", action="store_true")

    finalize = sub.add_parser("finalize-public")
    finalize.add_argument("--state-root", type=Path, required=True)
    finalize.add_argument("--run-id", required=True)
    finalize.add_argument("--manifest", type=Path, required=True)
    finalize.add_argument("--repo-root", type=Path, required=True)
    finalize.add_argument("--public-base-url", required=True)
    finalize.add_argument("--exact-successor", required=True)
    finalize.add_argument("--writer-lease", required=True)
    finalize.add_argument("--remote", default="origin")
    finalize.add_argument("--branch", default="main")
    finalize.add_argument("--wait-sec", type=int, default=0)
    finalize.add_argument("--poll-sec", type=int, default=30)
    finalize.add_argument("--fencing-token", type=int, default=None)

    args = parser.parse_args(argv)
    if args.cmd == "daily":
        result = _run_daily_cli()
    elif args.cmd == "validate-installed":
        result = validate_installed_automation_semantics(args.path)
    elif args.cmd == "relocate-state":
        result = relocate_runtime_state_v1(
            source_state_root=args.source_state_root,
            source_repo_root=args.source_repo_root,
            target_state_root=args.state_root,
            run_id=args.run_id,
            writer_lease=args.writer_lease,
            new_writer_lease=args.new_writer_lease,
            recovery_authority=args.recovery_authority,
        )
    else:
        store = DirectRunStore(args.state_root, create=args.cmd != "migrate-v2")
        if args.cmd == "start":
            if (
                args.installed_config is not None
                and os.environ.get("NEWS_GRASP_DIRECT_RUNTIME_ALLOW_TEST_INSTALLED_CONFIG") != "1"
            ):
                issue_date = args.issue_date or _now_jst().date().isoformat()
                result = {
                    "schemaVersion": RUNTIME_SCHEMA,
                    "ok": False,
                    "status": "installed_config_override_forbidden",
                    "automation_id": args.automation_id,
                    "cwd": str(Path(args.cwd).resolve()),
                    "issue_date": issue_date,
                    "failures": ["installed_config_override_test_only"],
                    "exact_successor": "use live installed automation without --installed-config",
                    "post_publish_issue_list": [
                        "--installed-config is test-only and cannot replace live automation authority"
                    ],
                }
                _emit_cli(result)
                return 2
            # runtime startはautomationを自動修復しない。source/installed/App DBの
            # parityをpromotion工程で閉じ、ここはRedのまま停止する。
            config_result = validate_installed_automation_semantics(args.installed_config)
            if config_result.get("ok") is not True:
                issue_date = args.issue_date or _now_jst().date().isoformat()
                result = {
                    "schemaVersion": RUNTIME_SCHEMA,
                    "ok": False,
                    "status": "automation_config_red",
                    "automation_id": args.automation_id,
                    "cwd": str(Path(args.cwd).resolve()),
                    "issue_date": issue_date,
                    "failures": list(config_result.get("failures") or ["automation_config_red"]),
                    "config": config_result,
                    "exact_successor": (
                        "python -m tools.sync_news_grasp_codex_automation --promote "
                        "--write-snapshot --write-skill --write-app-db"
                    ),
                    "post_publish_issue_list": [
                        "automation_config_red: live automation must be gpt-5.6-luna/max with direct-mainline prompt before production start"
                    ],
                }
                _emit_cli(result)
                return 2
            result = start_run(
                store,
                automation_id=args.automation_id,
                cwd=args.cwd,
                issue_date=args.issue_date or _now_jst().date().isoformat(),
                run_intent=args.run_intent,
                manifest_id=args.manifest_id,
                manifest_reservation_id=args.manifest_reservation_id,
                scheduler_trigger_at=args.scheduler_trigger_at,
                source_baseline=args.source_baseline,
                runtime_generation=args.runtime_generation,
                remote_base_sha=args.remote_base_sha,
                allowed_side_effect_ids=args.allowed_side_effect_id,
            )
        elif args.cmd == "inspect":
            result = inspect_run(store, run_id=args.run_id)
        elif args.cmd == "migrate-v2":
            observation = json.loads(
                args.observation_file.read_text(encoding="utf-8-sig")
            )
            if not isinstance(observation, Mapping):
                raise ValueError("observation_receipt_invalid")
            result = migrate_run_v1_to_v2(
                store,
                run_id=args.run_id,
                run_intent=args.run_intent,
                manifest_id=args.manifest_id,
                observation_receipt=observation,
                writer_lease=args.writer_lease,
            )
        elif args.cmd == "rebind-manifest":
            result = rebind_runtime_manifest(
                store,
                run_id=args.run_id,
                previous_manifest_id=args.previous_manifest_id,
                manifest_id=args.manifest_id,
                repo_root=args.repo_root,
                writer_lease=args.writer_lease,
            )
        elif args.cmd == "advance":
            evidence = _load_cli_mapping(
                evidence_json=args.evidence_json,
                evidence_file=args.evidence_file,
            )
            current = inspect_run(store, run_id=args.run_id)
            if current.get("current_stage") == "public_completion":
                result = {**current, "ok": False, "status": "blocked", "failures": ["public_completion_requires_atomic_finalizer"], "exact_successor": "finalize-public"}
            else:
                result = run_exact_successor(
                    store,
                    run_id=args.run_id,
                    writer_lease=args.writer_lease,
                    fencing_token=args.fencing_token,
                    semantic_verifier=lambda stage_id, **kwargs: _cli_stage_verifier(
                        stage_id,
                        run=kwargs.get("run") or {},
                        evidence=evidence,
                        repo_root=args.repo_root,
                        public_base_url=args.public_base_url or None,
                        remote=args.remote,
                        branch=args.branch,
                        wait_sec=args.wait_sec,
                        poll_sec=args.poll_sec,
                    ),
                    _registered_consumer=_REGISTERED_CONSUMER_CAPABILITY,
                )
        elif args.cmd == "verify-public":
            result = verify_public_completion(
                store,
                run_id=args.run_id,
                repo_root=args.repo_root,
                public_base_url=args.public_base_url or None,
                remote=args.remote,
                branch=args.branch,
                wait_sec=args.wait_sec,
                poll_sec=args.poll_sec,
            )
        elif args.cmd == "probe-public":
            result = probe_public_completion(
                store,
                run_id=args.run_id,
                repo_root=args.repo_root,
                public_base_url=args.public_base_url,
                remote=args.remote,
                branch=args.branch,
                wait_sec=args.wait_sec,
                poll_sec=args.poll_sec,
            )
        else:
            manifest_value = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
            state = inspect_run(store, run_id=args.run_id)
            if not isinstance(manifest_value, Mapping) or manifest_value.get("manifestId") != state.get("manifest_id"):
                result = {"ok": False, "status": "blocked", "failures": ["finalizer_manifest_binding_mismatch"], "exact_successor": "public_completion"}
            else:
                result = finalize_public_completion(
                    store,
                    run_id=args.run_id,
                    writer_lease=args.writer_lease,
                    repo_root=args.repo_root,
                    public_base_url=args.public_base_url,
                    remote=args.remote,
                    branch=args.branch,
                    wait_sec=args.wait_sec,
                    poll_sec=args.poll_sec,
                    exact_successor=args.exact_successor,
                    fencing_token=args.fencing_token,
                )
    _emit_cli(result)
    if args.cmd == "daily":
        return (
            0
            if result.get("ok") is True and result.get("status") == "completed"
            else 1
        )
    if result.get("ok", True) is not False:
        return 0
    status = str(result.get("status") or "").casefold()
    failures = " ".join(str(item) for item in result.get("failures") or [])
    if "lease" in status or "lease" in failures:
        return 4
    if status in {"deferred", "blocked_external", "required_external_blocked"}:
        return 3
    if status in {"environment_missing", "automation_config_red", "installed_config_override_forbidden"}:
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(_main())
