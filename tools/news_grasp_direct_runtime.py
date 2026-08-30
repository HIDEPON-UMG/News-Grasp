"""News-Grasp direct mainline runtime state and dispatcher.

旧 runner を実行 authority に戻さず、Codex automation が direct 本線として
進めた工程を durable に記録する薄い producer である。caller の ``ok`` や
自由形式の completion JSON は authority にせず、現在 stage に対応する
semantic verifier の観測だけで stage 遷移を行う。
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
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
PUBLIC_SCHEMA = "NEWS_GRASP_DIRECT_PUBLIC_VERIFICATION_V1"
AUTOMATION_ID = "news-grasp-6-40"
JST = timezone(timedelta(hours=9), name="Asia/Tokyo")
TITLE_SUCCESS = {"updated", "already_ok"}
TITLE_NONBLOCKING = {"unavailable", "failed", "skipped"}


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
        create: bool = True,
    ) -> None:
        self.state_root = Path(state_root)
        self.db_path = self.state_root / "direct-mainline.sqlite3"
        self.clock = clock or _now_jst
        self.host_generation = host_generation
        self.lease_ttl = lease_ttl
        self.semantic_verifier = semantic_verifier
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
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

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
    return {
        "schemaVersion": RUNTIME_SCHEMA,
        "run_id": row["run_id"],
        "automation_id": row["automation_id"],
        "cwd": row["cwd"],
        "issue_date": row["issue_date"],
        "generation": int(row["generation"]),
        "writer_lease": row["writer_lease"],
        "status": row["status"],
        "current_stage": current_stage,
        "current_stage_index": int(row["current_stage_index"]),
        "next_stage": current_stage,
        "exact_successor": row["exact_successor"] or current_stage,
        "stage_history": history,
        "title_status": row["title_status"],
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
        },
    }


def inspect_run(store: DirectRunStore, *, run_id: str) -> dict[str, Any]:
    with closing(store.connect()) as conn:
        row = store._run_row(conn, run_id)
        return _projection_from_row(store, conn, row)


def start_run(
    store: DirectRunStore,
    *,
    automation_id: str = AUTOMATION_ID,
    cwd: str | Path,
    issue_date: str,
) -> dict[str, Any]:
    issue = _validate_issue_date(issue_date)
    canonical_cwd = _canonical_cwd(cwd)
    now = store.now()
    now_text = _iso(now)
    with closing(store.connect()) as conn:
        latest = store._latest_for_identity(
            conn,
            automation_id=automation_id,
            cwd=canonical_cwd,
            issue_date=issue,
        )
        if latest is not None:
            lease_until = _parse_time(latest["lease_until"])
            if latest["status"] in {"active", "executing"} and lease_until and lease_until > now:
                return _projection_from_row(store, conn, latest)
            if latest["status"] in {"active", "executing"}:
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
                expected_title, exact_successor
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            ),
        )
        row = store._run_row(conn, run_id)
        projection = _projection_from_row(store, conn, row)
        conn.commit()
        return projection


def _expected_title(issue_date: str) -> str:
    from tools.news_grasp_title_control import expected_title

    return expected_title(issue_date)


def _verify_writer(row: sqlite3.Row, writer_lease: str, now: datetime) -> None:
    del now
    if str(row["writer_lease"]) != str(writer_lease):
        raise PermissionError("stale writer lease fenced")
    if row["status"] not in {"active", "executing"}:
        raise RuntimeError("run_not_writable")


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
    if row.get("completion_mode") != "direct_public_v1":
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
        if str(item.get("status") or "").casefold() != "green":
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
        and status in {"external_failure", "quota", "deferred"}
        or "quota" in reason
        or "oauth" in reason
        or "youtube" in surface
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
) -> dict[str, Any]:
    now = (observed_at or store.now()).astimezone(JST)
    now_text = _iso(now)
    with closing(store.connect()) as conn:
        row = store._run_row(conn, run_id)
        _verify_writer(row, writer_lease, now)
        current_index = int(row["current_stage_index"])
        current_stage = _stage_for_index(current_index)
        if not current_stage:
            return _projection_from_row(store, conn, row)
        if stage_id != current_stage:
            raise ValueError("stage order successor violation")
        projection = _projection_from_row(store, conn, row)
        verifier_row = _call_verifier(
            verifier if verifier is not None else store.semantic_verifier,
            stage_id,
            run=projection,
            caller_result=caller_result,
            observed_surface=observed_surface,
        )
        ok = verifier_row.get("ok") is True
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
    )


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
            cwd_values = {
                str(Path(str(item)).expanduser().resolve(strict=True))
                for item in cwds
            }
        except (OSError, TypeError, ValueError):
            failures.append("automation_cwds_invalid")
        else:
            try:
                current_repo = str(Path.cwd().resolve(strict=True))
            except (OSError, RuntimeError, ValueError):
                current_repo = ""
            if current_repo and current_repo not in cwd_values:
                failures.append("automation_cwds_current_repo_missing")
    prompt = str(value.get("prompt") or "")
    required_prompt_parts = (
        "$news-grasp-direct-mainline",
        "YY/MM/DD",
        "title_status",
        "already_ok",
        "post_publish_issue_list",
        "completion_guard.py",
        "direct_public",
        "validate_daily_quality",
        "--require-deepdive",
    )
    for part in required_prompt_parts:
        if part not in prompt:
            failures.append(f"prompt_missing:{part}")
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
    }


def _main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    validate = sub.add_parser("validate-installed")
    validate.add_argument("--path", type=Path, default=None)

    start = sub.add_parser("start")
    start.add_argument("--state-root", type=Path, required=True)
    start.add_argument("--cwd", type=Path, default=Path.cwd())
    start.add_argument(
        "--issue-date",
        default=None,
        help="対象日 YYYY-MM-DD。未指定なら Asia/Tokyo の当日を使う。",
    )
    start.add_argument("--automation-id", default=AUTOMATION_ID)

    inspect = sub.add_parser("inspect")
    inspect.add_argument("--state-root", type=Path, required=True)
    inspect.add_argument("--run-id", required=True)

    verify = sub.add_parser("verify-public")
    verify.add_argument("--state-root", type=Path, required=True)
    verify.add_argument("--run-id", required=True)
    verify.add_argument("--repo-root", type=Path, default=None)
    verify.add_argument("--public-base-url", default="")
    verify.add_argument("--remote", default="origin")
    verify.add_argument("--branch", default="main")
    verify.add_argument("--wait-sec", type=int, default=0)
    verify.add_argument("--poll-sec", type=int, default=30)

    args = parser.parse_args()
    if args.cmd == "validate-installed":
        result = validate_installed_automation_semantics(args.path)
    else:
        store = DirectRunStore(args.state_root)
        if args.cmd == "start":
            result = start_run(
                store,
                automation_id=args.automation_id,
                cwd=args.cwd,
                issue_date=args.issue_date or _now_jst().date().isoformat(),
            )
        elif args.cmd == "inspect":
            result = inspect_run(store, run_id=args.run_id)
        else:
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
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return 0 if result.get("ok", True) is not False else 2


if __name__ == "__main__":
    raise SystemExit(_main())
