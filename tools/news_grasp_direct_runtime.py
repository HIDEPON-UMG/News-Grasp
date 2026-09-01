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
import os
import re
import shutil
import sqlite3
import stat
import sys
import tomllib
import uuid
from contextlib import closing
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


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
RUN_INTENT = "scheduled_production_direct"
AUTOMATION_ID = "news-grasp-6-40"
JST = timezone(timedelta(hours=9), name="Asia/Tokyo")
TITLE_SUCCESS = {"updated", "already_ok"}
TITLE_NONBLOCKING = {"unavailable", "failed", "skipped"}
MAX_CLI_EVIDENCE_BYTES = 1024 * 1024
_REGISTERED_CONSUMER_CAPABILITY = object()


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
        if create:
            self.state_root.mkdir(parents=True, exist_ok=True)
            self._init_db()
        else:
            if not self.state_root.is_dir():
                raise FileNotFoundError("direct_state_root_missing")
            if not self.db_path.is_file():
                raise FileNotFoundError("direct_state_db_missing")

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

    def _init_db(self) -> None:
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
                "migration_receipt_json": "TEXT NOT NULL DEFAULT '{}'",
                "observation_receipt_json": "TEXT NOT NULL DEFAULT '{}'",
                "typed_issues_json": "TEXT NOT NULL DEFAULT '[]'",
                "finalization_nonce": "TEXT NOT NULL DEFAULT ''",
            }
            for name, declaration in additions.items():
                if name not in existing_columns:
                    conn.execute(f"ALTER TABLE runs ADD COLUMN {name} {declaration}")
            conn.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS runs_active_identity_uq
                   ON runs(automation_id,cwd,issue_date)
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
                "CREATE INDEX IF NOT EXISTS idx_runs_identity "
                "ON runs (automation_id, cwd, issue_date, generation)"
            )
            conn.commit()


    def _latest_for_identity(
        self,
        conn: sqlite3.Connection,
        *,
        automation_id: str,
        cwd: str,
        issue_date: str,
    ) -> sqlite3.Row | None:
        return conn.execute(
            """
            SELECT * FROM runs
            WHERE automation_id = ? AND cwd = ? AND issue_date = ?
            ORDER BY generation DESC
            LIMIT 1
            """,
            (automation_id, cwd, issue_date),
        ).fetchone()

    def _run_row(self, conn: sqlite3.Connection, run_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise ValueError("run_not_found")
        return row


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
    started_at = _parse_time(row["started_at"]) or store.now()
    elapsed = max(0.0, (store.now() - started_at).total_seconds() / 60.0)
    if elapsed <= 45:
        time_band = "target"
    elif elapsed < 75:
        time_band = "closeout"
    elif elapsed <= 90:
        time_band = "public_critical_only"
    else:
        time_band = "slo_debt_continue_public"
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
        "migration_receipt": _json_load(row["migration_receipt_json"], {}),
        "observation_receipt": _json_load(row["observation_receipt_json"], {}),
        "typed_issues": _json_load(row["typed_issues_json"], []),
        "generation": int(row["generation"]),
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
        "updated_at": row["updated_at"],
        "completed_at": row["completed_at"],
        "slo": {
            "elapsed_minutes": elapsed,
            "target_minutes": 45,
            "optional_high_cost_freeze_minutes": 75,
            "slo_minutes": 90,
            "target_met": elapsed <= 45,
            "optional_high_cost_frozen": elapsed >= 75,
            "slo_met": elapsed <= 90,
            "slo_debt": elapsed > 90,
            "time_band": time_band,
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


def start_run(
    store: DirectRunStore,
    *,
    automation_id: str = AUTOMATION_ID,
    cwd: str | Path,
    issue_date: str,
    run_intent: str | None = None,
    manifest_id: str = "",
    observation_receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    issue = _validate_issue_date(issue_date)
    canonical_cwd = _canonical_cwd(cwd)
    now = store.now()
    now_text = _iso(now)
    with closing(store.connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        latest = store._latest_for_identity(
            conn,
            automation_id=automation_id,
            cwd=canonical_cwd,
            issue_date=issue,
        )
        if latest is not None:
            lease_until = _parse_time(latest["lease_until"])
            if latest["status"] in {"active", "executing", "finalizing"} and lease_until and lease_until > now:
                projection = _projection_from_row(store, conn, latest)
                projection.pop("writer_lease", None)
                conn.rollback()
                return projection
            if latest["status"] in {"active", "executing", "finalizing"}:
                conn.execute(
                    "UPDATE runs SET status = ?, updated_at = ? WHERE run_id = ?",
                    ("stale_writer_rejected", now_text, latest["run_id"]),
                )
        prior_generation = int(latest["generation"]) if latest is not None else 0
        generation = max(prior_generation + 1, _call_generation(store.host_generation))
        run_id = _opaque_id("direct", issue, generation)
        writer_lease = _opaque_id("lease", issue, generation)
        lease_until = _iso(now + store.lease_ttl)
        expected_title = _expected_title(issue)
        conn.execute(
            """
            INSERT INTO runs (
                run_id, automation_id, cwd, issue_date, generation, writer_lease,
                status, current_stage_index, started_at, lease_until, updated_at,
                expected_title, exact_successor, runtime_schema, run_intent,
                manifest_id, observation_receipt_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                str(run_intent or ""),
                str(manifest_id),
                _json_dump(dict(observation_receipt or {})),
            ),
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
) -> None:
    if str(row["writer_lease"]) != str(writer_lease):
        raise PermissionError("stale writer lease fenced")
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
) -> dict[str, Any]:
    if verifier is not None and not store.test_only_allow_semantic_verifier and _registered_consumer is not _REGISTERED_CONSUMER_CAPABILITY:
        raise PermissionError("semantic_verifier_injection_test_only")
    now = (observed_at or store.now()).astimezone(JST)
    now_text = _iso(now)
    with closing(store.connect()) as conn:
        row = store._run_row(conn, run_id)
        _verify_writer(row, writer_lease, now)
        started_at = _parse_time(str(row["started_at"])) or now
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
            "cwd": str(Path(str(raw[2])).resolve()),
        }
        if any(str(observation_receipt.get(key) or "") != value for key, value in required_binding.items()):
            raise ValueError("observation_receipt_binding_invalid")
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

        canonical_manifest = load_manifest(required_binding["cwd"], required_binding["issueDate"])
        if (
            canonical_manifest.get("manifestId") != manifest_id
            or canonical_manifest.get("runId") != run_id
            or canonical_manifest.get("runIntent") != run_intent
            or list(observation_receipt.get("exactWriteSet") or []) != list(canonical_manifest.get("exactWriteSet") or [])
            or verify_manifest(canonical_manifest, repo_root=required_binding["cwd"], require_files=True).get("ok") is not True
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
            SET runtime_schema = ?, run_intent = ?, manifest_id = ?,
                migration_receipt_json = ?, observation_receipt_json = ?,
                typed_issues_json = ?, status = ?, exact_successor = ?, updated_at = ?
            WHERE run_id = ?
            """,
            (
                RUNTIME_SCHEMA_V2,
                run_intent,
                manifest_id,
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
    stage_history = list(state.get("stage_history") or [])
    current_stage = state.get("current_stage")
    if current_stage == "public_completion":
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
    return {
        "schemaVersion": PUBLIC_SCHEMA_V2,
        "ok": not failures,
        "status": "verified" if not failures else "blocked",
        "completion_mode": "direct_public_v2",
        "run_id": run_id,
        "runIntent": state.get("run_intent") or RUN_INTENT,
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
            "observedAt": _iso(store.now()),
        },
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
) -> dict[str, Any]:
    """fresh probe Green後だけsurface failureをtyped issueへ移して工程20を閉じる。"""
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
    if exact_successor != "public_completion":
        raise ValueError("exact_successor_invalid")
    nonce = uuid.uuid4().hex
    admitted_stage_digest = ""
    admitted_stage_warnings: list[dict[str, Any]] = []
    with closing(store.connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = store._run_row(conn, run_id)
        now = store.now()
        _verify_writer(row, writer_lease, now, allowed_statuses={"active", "executing", "finalizing"})
        if (
            int(row["current_stage_index"]) != len(DIRECT_STAGES) - 1
            or str(row["exact_successor"] or "") != "public_completion"
            or not re.fullmatch(r"[0-9a-f]{64}", str(row["manifest_id"] or ""))
        ):
            conn.rollback()
            raise PermissionError("finalizer_admission_red")
        stages = conn.execute("SELECT stage_index,stage_id,status,evidence_json FROM stages WHERE run_id=? ORDER BY stage_index", (run_id,)).fetchall()
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
        lease_until = _iso(now + store.lease_ttl)
        changed = conn.execute(
            """UPDATE runs SET status='finalizing', finalization_nonce=?, lease_until=?, updated_at=?
               WHERE run_id=? AND writer_lease=? AND status=? AND updated_at=?""",
            (nonce, lease_until, now_text, run_id, writer_lease, row["status"], row["updated_at"]),
        ).rowcount
        if changed != 1:
            conn.rollback()
            raise PermissionError("finalizer_admission_cas_conflict")
        conn.commit()

    def restore_after_red() -> None:
        with closing(store.connect()) as restore:
            restore.execute("BEGIN IMMEDIATE")
            restored = restore.execute(
                """UPDATE runs SET status='active', finalization_nonce='', exact_successor='public_completion', updated_at=?
                   WHERE run_id=? AND writer_lease=? AND status='finalizing' AND finalization_nonce=?""",
                (_iso(store.now()), run_id, writer_lease, nonce),
            ).rowcount
            if restored == 1:
                restore.commit()
            else:
                restore.rollback()

    try:
        probe = probe_public_completion(
            store,
            run_id=run_id,
            semantic_verifier=semantic_verifier,
            repo_root=repo_root,
            public_base_url=public_base_url,
            remote=remote,
            branch=branch,
            wait_sec=wait_sec,
            poll_sec=poll_sec,
        )
        if probe.get("ok") is not True:
            restore_after_red()
            return {**inspect_run(store, run_id=run_id), "ok": False, "status": "needs_successor", "failures": probe["failures"], "exact_successor": "public_completion"}
        fresh_probe = probe_public_completion(
            store,
            run_id=run_id,
            semantic_verifier=semantic_verifier,
            repo_root=repo_root,
            public_base_url=public_base_url,
            remote=remote,
            branch=branch,
            wait_sec=0,
            poll_sec=poll_sec,
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
            _verify_writer(row, writer_lease, now, allowed_statuses={"finalizing"})
            binding = fresh_probe.get("freshnessBinding") or {}
            current_stages = conn.execute("SELECT stage_index,stage_id,status,evidence_json FROM stages WHERE run_id=? ORDER BY stage_index", (run_id,)).fetchall()
            current_stage_digest = hashlib.sha256(_json_dump([list(item) for item in current_stages]).encode("utf-8")).hexdigest()
            if (
                str(row["finalization_nonce"] or "") != nonce
                or current_stage_digest != admitted_stage_digest
                or int(row["current_stage_index"]) != len(DIRECT_STAGES) - 1
                or str(row["exact_successor"] or "") != "public_completion"
                or str(row["manifest_id"] or "") != str(binding.get("manifestId") or "")
                or int(row["generation"]) != int(binding.get("generation") or -1)
                or str(row["updated_at"] or "") != str(binding.get("updatedAt") or "")
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
            conn.execute(
                "INSERT OR REPLACE INTO stages (run_id,stage_index,stage_id,status,started_at,completed_at,evidence_json) VALUES (?,?,?,?,?,?,?)",
                (run_id, len(DIRECT_STAGES) - 1, "public_completion", "verified", now_text, now_text, _json_dump(evidence)),
            )
            changed = conn.execute(
                """UPDATE runs SET surface_failures='[]', typed_issues_json=?, post_publish_issue_list=?, status='completed',
                   current_stage_index=?, exact_successor='', finalization_nonce='', completed_at=?, updated_at=?
                   WHERE run_id=? AND writer_lease=? AND status='finalizing' AND finalization_nonce=?
                     AND current_stage_index=? AND updated_at=? AND manifest_id=?""",
                (_json_dump(typed), _json_dump(post_publish_issues), len(DIRECT_STAGES), now_text, now_text, run_id, writer_lease, nonce, len(DIRECT_STAGES) - 1, row["updated_at"], row["manifest_id"]),
            ).rowcount
            if changed != 1:
                conn.rollback()
                raise PermissionError("finalizer_atomic_cas_conflict")
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
    if len(state.get("stage_history") or []) != len(DIRECT_STAGES):
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
        "news_grasp_title_materializer",
        "title_status",
        "title_status=already_ok",
        "already_ok",
        "post_publish_issue_list",
        "direct completion guard",
        "completion_guard.py",
        "direct_public",
        "validate_daily_quality",
        "--require-deepdive",
    )
    for part in required_prompt_parts:
        if part not in prompt:
            failures.append(f"prompt_missing:{part}")
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
    }


def _repair_installed_automation_config_once(*, cwd: Path) -> dict[str, Any]:
    """live installed automation を repo template へ一度だけ同期する。

    `--installed-config` で渡されたテスト用・手動検査用pathは対象にしない。
    06:00 direct 本線の開始前に、App/UI 側で medium や旧promptへ戻った
    live store だけを operation-local に修復するための薄い successor である。
    """

    try:
        from tools import sync_news_grasp_codex_automation as automation_sync

        canonical_repo = automation_sync._assert_trusted_repo_root(  # noqa: SLF001
            automation_sync._default_repo_root()  # noqa: SLF001
        )
        requested_cwd = cwd.resolve(strict=True)
        if requested_cwd != canonical_repo:
            return {
                "schemaVersion": "NEWS_GRASP_CODEX_AUTOMATION_SYNC_V1",
                "ok": False,
                "failures": ["automation_config_repair_cwd_not_canonical_news_grasp_repo"],
                "cwd": str(requested_cwd),
                "expected_cwd": str(canonical_repo),
            }
        return automation_sync.sync(
            repo_root=canonical_repo,
            write_snapshot=True,
            write_skill=True,
            write_app_db=True,
            dry_run=False,
        )
    except Exception as exc:  # pragma: no cover - 例外型は呼出環境依存
        return {
            "schemaVersion": "NEWS_GRASP_CODEX_AUTOMATION_SYNC_V1",
            "ok": False,
            "failures": [f"automation_config_repair_unavailable:{exc}"],
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
            summary = root / "docs" / issue_date / "summary" / "index.html"
            failures.extend(validate_daily_quality.validate_summary_hero(summary))
            failures.extend(validate_daily_quality.validate_summary_emphasis(summary))
        elif stage_id == "daily_audio":
            observation = _audio_projection(root, issue_date, audio_type="daily", run_id=run_id, run_intent=run_intent)
            if observation.get("ok") is not True:
                failures.extend(observation.get("reasonCodes") or ["daily_audio_projection_red"])
        elif stage_id in {"deepdive_article", "deepdive_quality"}:
            observation = deepdive_quality.audit_issue(repo_root=root, issue_date=issue_date, require_rendered_public=stage_id == "deepdive_quality", route="production_generation")
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


def _main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
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
    start.add_argument("--run-intent", default="")
    start.add_argument("--manifest-id", default="")

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

    args = parser.parse_args()
    if args.cmd == "validate-installed":
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
                sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
                return 2
            config_result = validate_installed_automation_semantics(args.installed_config)
            config_repair = None
            if config_result.get("ok") is not True and args.installed_config is None:
                config_repair = _repair_installed_automation_config_once(
                    cwd=Path(args.cwd),
                )
                config_result = validate_installed_automation_semantics(None)
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
                    "config_repair": config_repair,
                    "exact_successor": (
                        "python -m tools.sync_news_grasp_codex_automation "
                        "--write-snapshot --write-skill --write-app-db"
                    ),
                    "post_publish_issue_list": [
                        "automation_config_red: live automation must be gpt-5.6-luna/max with direct-mainline prompt before production start"
                    ],
                }
                sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
                return 2
            result = start_run(
                store,
                automation_id=args.automation_id,
                cwd=args.cwd,
                issue_date=args.issue_date or _now_jst().date().isoformat(),
                run_intent=args.run_intent,
                manifest_id=args.manifest_id,
            )
            if config_repair is not None:
                result["config_repair"] = config_repair
                result["post_publish_issue_list"] = list(
                    result.get("post_publish_issue_list") or []
                ) + ["automation_config_repaired_before_stage_start"]
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
                )
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
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
