from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tools import sync_news_grasp_codex_automation as syncer


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "automation" / "news-grasp-6-40" / "automation.toml.template"


@pytest.fixture(autouse=True)
def _allow_isolated_sync_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEWS_GRASP_ALLOW_TEST_SYNC_PATHS", "1")


def _database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        """
        create table automations (
            id text primary key,
            name text not null,
            prompt text not null,
            status text not null default 'ACTIVE',
            next_run_at integer,
            last_run_at integer,
            cwds text not null default '[]',
            rrule text not null,
            model text,
            reasoning_effort text,
            created_at integer not null,
            updated_at integer not null,
            target_type text,
            project_id text,
            notification_policy text
        )
        """
    )
    values = (
        "fixture name",
        "fixture prompt",
        "ACTIVE",
        None,
        None,
        "[]",
        "RRULE:FREQ=DAILY;BYHOUR=6;BYMINUTE=0;BYSECOND=0",
        "gpt-5.6-luna",
        "medium",
        10,
        20,
        "project",
        "fixture-project",
        "failed_runs_only",
    )
    conn.execute(
        """
        insert into automations(
            id,name,prompt,status,next_run_at,last_run_at,cwds,rrule,model,
            reasoning_effort,created_at,updated_at,target_type,project_id,
            notification_policy
        ) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (syncer.AUTOMATION_ID, *values),
    )
    conn.execute(
        """
        insert into automations(
            id,name,prompt,status,next_run_at,last_run_at,cwds,rrule,model,
            reasoning_effort,created_at,updated_at,target_type,project_id,
            notification_policy
        ) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        ("unrelated-automation", *values),
    )
    conn.commit()
    conn.close()


def _promote(path: Path) -> dict[str, object]:
    target = syncer._new_app_db_promotion_target(path)
    result = syncer.sync_app_db(
        repo_root=ROOT,
        template_path=TEMPLATE,
        app_db_path=path,
        project_target={"type": "project", "project_id": "fixture-project"},
        dry_run=False,
        allow_custom_app_db=True,
        promotion_target=target,
    )
    assert result["ok"] is True, result["failures"]
    assert target["status"] == "promoted"
    return target


def _prompt(path: Path, automation_id: str) -> str:
    conn = sqlite3.connect(path)
    value = conn.execute("select prompt from automations where id = ?", (automation_id,)).fetchone()[0]
    conn.close()
    return str(value)


def test_app_db_rollback_restores_only_target_row_and_preserves_optional_fields(tmp_path: Path) -> None:
    database = tmp_path / "news-grasp-sync-fixture" / "codex-dev.db"
    _database(database)
    target = _promote(database)
    conn = sqlite3.connect(database)
    conn.execute("update automations set prompt = ? where id = ?", ("parallel app update", "unrelated-automation"))
    conn.commit()
    conn.close()

    receipt = syncer._rollback_app_db_target(target)

    assert receipt["ok"] is True
    assert receipt["cas"]["status"] == "restored"
    assert _prompt(database, syncer.AUTOMATION_ID) == "fixture prompt"
    assert _prompt(database, "unrelated-automation") == "parallel app update"
    conn = sqlite3.connect(database)
    policy = conn.execute(
        "select notification_policy from automations where id = ?", (syncer.AUTOMATION_ID,)
    ).fetchone()[0]
    conn.close()
    assert policy == "failed_runs_only"


def test_app_db_rollback_cas_mismatch_preserves_newer_target_row(tmp_path: Path) -> None:
    database = tmp_path / "news-grasp-sync-fixture" / "codex-dev.db"
    _database(database)
    target = _promote(database)
    conn = sqlite3.connect(database)
    conn.execute(
        "update automations set prompt = ? where id = ?",
        ("newer operator update", syncer.AUTOMATION_ID),
    )
    conn.commit()
    conn.close()

    receipt = syncer._rollback_app_db_target(target)

    assert receipt["ok"] is False
    assert receipt["failures"] == ["app_db_rollback_postimage_mismatch"]
    assert receipt["cas"]["status"] == "mismatch"
    assert _prompt(database, syncer.AUTOMATION_ID) == "newer operator update"


def test_app_db_schema_drift_is_rejected_without_row_mutation(tmp_path: Path) -> None:
    database = tmp_path / "news-grasp-sync-fixture" / "codex-dev.db"
    _database(database)
    before = database.read_bytes()
    conn = sqlite3.connect(database)
    conn.execute("alter table automations add column future_field text")
    conn.commit()
    original_prompt = _prompt(database, syncer.AUTOMATION_ID)
    conn.close()

    target = syncer._new_app_db_promotion_target(database)
    result = syncer.sync_app_db(
        repo_root=ROOT,
        template_path=TEMPLATE,
        app_db_path=database,
        project_target={"type": "project", "project_id": "fixture-project"},
        dry_run=False,
        allow_custom_app_db=True,
        promotion_target=target,
    )

    assert result["ok"] is False
    assert any(str(item).startswith("app_db_schema_drift:") for item in result["failures"]), result
    assert _prompt(database, syncer.AUTOMATION_ID) == original_prompt
    assert database.read_bytes() != before  # fixtureのALTERだけは存在する。
