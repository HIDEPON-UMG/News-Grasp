"""本体開始前後を区別する製品内の有限・追記専用journal。"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import stat
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EVENTS = frozenset({
    "wrapper_started", "authority_ready", "reservation_ready", "claim_started",
    "claim_ready", "witness_ready", "module_loaded", "module_entered", "module_started",
    "terminal", "preentry_failed",
})
MAX_EVENTS_PER_ISSUE = 256
MAX_EVENT_BYTES = 4096


def _safe_path(path: Path) -> Path:
    candidate = Path(os.path.abspath(path))
    for part in (candidate, *candidate.parents):
        if part.exists() or part.is_symlink():
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise RuntimeError("NEWS_GRASP_PREENTRY_REPARSE_REJECTED")
    return candidate


class PreentryJournal:
    def __init__(self, path: Path):
        self.path = _safe_path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS events (sequence INTEGER PRIMARY KEY AUTOINCREMENT, issue_date TEXT NOT NULL, session_id TEXT NOT NULL, phase TEXT NOT NULL, observed_at TEXT NOT NULL, detail TEXT NOT NULL, UNIQUE(issue_date,session_id,phase))")
            db.execute("CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT,'append_only'); END")
            db.execute("CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT,'append_only'); END")

    @contextmanager
    def _connect(self):
        _safe_path(self.path)
        db = sqlite3.connect(self.path, timeout=5)
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA journal_mode=DELETE")
        db.execute("PRAGMA max_page_count=16384")
        try:
            with db:
                yield db
        finally:
            db.close()

    def append(self, issue_date: str, session_id: str, phase: str, detail: dict[str, Any]) -> int:
        if phase not in EVENTS or not session_id or len(session_id) > 128:
            raise RuntimeError("NEWS_GRASP_PREENTRY_EVENT_INVALID")
        datetime.strptime(issue_date, "%Y-%m-%d")
        raw = json.dumps(detail, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(raw.encode("utf-8")) > MAX_EVENT_BYTES:
            raise RuntimeError("NEWS_GRASP_PREENTRY_EVENT_TOO_LARGE")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT sequence,detail FROM events WHERE issue_date=? AND session_id=? AND phase=?", (issue_date,session_id,phase)).fetchone()
            if existing:
                if existing[1] != raw:
                    raise RuntimeError("NEWS_GRASP_PREENTRY_REPLAY_DRIFT")
                return int(existing[0])
            count = db.execute("SELECT COUNT(*) FROM events WHERE issue_date=?", (issue_date,)).fetchone()[0]
            if count >= MAX_EVENTS_PER_ISSUE:
                raise RuntimeError("NEWS_GRASP_PREENTRY_JOURNAL_FULL")
            if phase == "module_started":
                entered = db.execute("SELECT detail FROM events WHERE issue_date=? AND session_id=? AND phase='module_entered'", (issue_date,session_id)).fetchone()
                if not entered or json.loads(entered[0]) != detail:
                    raise RuntimeError("NEWS_GRASP_PREENTRY_START_WITHOUT_OBSERVATION")
                if db.execute("SELECT 1 FROM events WHERE issue_date=? AND phase='module_started'", (issue_date,)).fetchone():
                    raise RuntimeError("NEWS_GRASP_E2E_BODY_ALREADY_STARTED")
            cursor = db.execute("INSERT INTO events(issue_date,session_id,phase,observed_at,detail) VALUES(?,?,?,?,?)", (issue_date,session_id,phase,datetime.now(timezone.utc).isoformat(),raw))
            return int(cursor.lastrowid)

    def events(self, issue_date: str, session_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT sequence,session_id,phase,observed_at,detail FROM events WHERE issue_date=?"
        params = [issue_date]
        if session_id is not None:
            query += " AND session_id=?"
            params.append(session_id)
        with self._connect() as db:
            return [{"sequence":r[0],"sessionId":r[1],"phase":r[2],"observedAt":r[3],"detail":json.loads(r[4])} for r in db.execute(query+" ORDER BY sequence",params)]


def environment_journal() -> tuple[PreentryJournal, str, str] | None:
    names = ("NEWS_GRASP_PREENTRY_JOURNAL", "NEWS_GRASP_PREENTRY_ISSUE", "NEWS_GRASP_PREENTRY_SESSION")
    values = [os.environ.get(name, "") for name in names]
    if not any(values):
        return None
    if not all(values):
        raise RuntimeError("NEWS_GRASP_PREENTRY_CONTEXT_MISSING")
    return PreentryJournal(Path(values[0])), values[1], values[2]


def record_environment_event(phase: str, detail: dict[str, Any]) -> int:
    context = environment_journal()
    if context is None:
        raise RuntimeError("NEWS_GRASP_PREENTRY_CONTEXT_MISSING")
    journal, issue_date, session_id = context
    return journal.append(issue_date, session_id, phase, detail)


def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=sorted((EVENTS - {"module_loaded", "module_entered", "module_started"}) | {"failure"}))
    parser.add_argument("--detail", default="{}")
    parser.add_argument("--exit-code", type=int)
    parser.add_argument("--reason-code", default="")
    args = parser.parse_args()
    detail = json.loads(args.detail)
    if not isinstance(detail, dict):
        raise RuntimeError("NEWS_GRASP_PREENTRY_EVENT_INVALID")
    if args.exit_code is not None:
        detail["exitCode"] = args.exit_code
    if args.reason_code:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{5,127}", args.reason_code):
            raise RuntimeError("NEWS_GRASP_PREENTRY_EVENT_INVALID")
        detail["reasonCode"] = args.reason_code
    phase = args.phase
    if phase == "failure":
        context = environment_journal()
        if context is None:
            raise RuntimeError("NEWS_GRASP_PREENTRY_CONTEXT_MISSING")
        journal, issue_date, session_id = context
        phase = "terminal" if any(row["phase"] == "module_started" for row in journal.events(issue_date, session_id)) else "preentry_failed"
    print(json.dumps({"sequence": record_environment_event(phase, detail)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
