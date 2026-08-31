from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import tools.news_grasp_title_materializer as title_materializer
from tools.news_grasp_title_materializer import (
    TitleMaterializationError,
    materialize_title,
)
from tools.sync_news_grasp_codex_automation import _render_installed


ISSUE_DATE = "2026-08-31"


def _write_fixture(tmp_path: Path, *, name: str) -> tuple[Path, Path, Path]:
    repo = tmp_path / "News-Grasp"
    template = repo / "automation" / "news-grasp-6-40" / "automation.toml.template"
    installed = tmp_path / "installed" / "automation.toml"
    db = tmp_path / "codex-dev.db"
    template.parent.mkdir(parents=True)
    installed.parent.mkdir(parents=True)
    template.write_text(
        'name = "News-Grasp 臨時本線日次バッチ 6:00 記事作成・公開"\n',
        encoding="utf-8",
    )
    installed.write_text(
        "\n".join(
            [
                'version = 1',
                'id = "news-grasp-6-40"',
                'kind = "cron"',
                f"name = {json.dumps(name, ensure_ascii=False)}",
                'status = "ACTIVE"',
                'rrule = "RRULE:FREQ=DAILY;BYHOUR=6;BYMINUTE=0;BYSECOND=0"',
                'model = "gpt-5.6-luna"',
                'reasoning_effort = "xhigh"',
                'execution_environment = "local"',
                'prompt = "unchanged"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    conn = sqlite3.connect(db)
    conn.execute(
        "create table automations (id text primary key, name text not null, prompt text not null, "
        "status text not null, next_run_at integer, last_run_at integer, cwds text not null, "
        "rrule text not null, model text, reasoning_effort text, created_at integer not null, "
        "updated_at integer not null, target_type text, project_id text)"
    )
    conn.execute(
        "insert into automations values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "news-grasp-6-40",
            name,
            "prompt unchanged",
            "ACTIVE",
            100,
            90,
            "[\"C:/News-Grasp\"]",
            "RRULE:FREQ=DAILY;BYHOUR=6;BYMINUTE=0;BYSECOND=0",
            "gpt-5.6-luna",
            "xhigh",
            80,
            100,
            "project",
            "project-1",
        ),
    )
    conn.commit()
    conn.close()
    return template, installed, db


def test_materializer_updates_only_names_and_binds_issue_date(tmp_path: Path) -> None:
    template, installed, db = _write_fixture(
        tmp_path, name="News-Grasp 臨時本線日次バッチ 6:00 記事作成・公開"
    )
    receipt_path = template.parents[2] / "build" / "direct-mainline" / "receipt.json"

    result = materialize_title(
        issue_date=ISSUE_DATE,
        template_path=template,
        installed_path=installed,
        app_db_path=db,
        receipt_path=receipt_path,
    )

    expected = "26/08/31 News-Grasp 臨時本線日次バッチ 6:00 記事作成・公開"
    assert result["ok"] is True
    assert result["materialized_title"] == expected
    assert result["installed_changed"] is True
    assert result["app_db_changed"] is True
    assert f'name = {json.dumps(expected, ensure_ascii=False)}' in installed.read_text(
        encoding="utf-8"
    )
    conn = sqlite3.connect(db)
    row = conn.execute(
        "select name, prompt, rrule, model, reasoning_effort, updated_at from automations "
        "where id = ?",
        ("news-grasp-6-40",),
    ).fetchone()
    conn.close()
    assert row[:5] == (expected, "prompt unchanged", "RRULE:FREQ=DAILY;BYHOUR=6;BYMINUTE=0;BYSECOND=0", "gpt-5.6-luna", "xhigh")
    assert row[5] >= 100
    assert json.loads(receipt_path.read_text(encoding="utf-8"))["ok"] is True


def test_cli_rejects_receipt_paths_outside_fixed_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    template, installed, db = _write_fixture(tmp_path, name="stale")
    repo = template.parents[2]
    monkeypatch.setattr(title_materializer, "_default_installed", lambda: installed)
    monkeypatch.setattr(title_materializer, "_default_app_db", lambda: db)

    for receipt_arg in (
        str(tmp_path / "absolute.json"),
        "build/direct-mainline/../receipt.json",
        "build/other/receipt.json",
    ):
        assert title_materializer.main(
            ["--repo-root", str(repo), "--issue-date", ISSUE_DATE, "--receipt", receipt_arg]
        ) == 2
        result = json.loads(capsys.readouterr().out)
        assert result["reasonCode"] == "RECEIPT_PATH_INVALID"


def test_cli_default_receipt_is_written_under_fixed_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    template, installed, db = _write_fixture(tmp_path, name="stale")
    repo = template.parents[2]
    monkeypatch.setattr(title_materializer, "_default_installed", lambda: installed)
    monkeypatch.setattr(title_materializer, "_default_app_db", lambda: db)

    assert title_materializer.main(
        ["--repo-root", str(repo), "--issue-date", ISSUE_DATE]
    ) == 0

    result = json.loads(capsys.readouterr().out)
    receipt = repo / "build" / "direct-mainline" / "title-materialization.json"
    assert result["ok"] is True
    assert receipt.is_file()
    assert json.loads(receipt.read_text(encoding="utf-8"))["ok"] is True


@pytest.mark.parametrize("unsafe_component", ["leaf", "parent"])
def test_materializer_rejects_reparse_receipt_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unsafe_component: str,
) -> None:
    template, installed, db = _write_fixture(tmp_path, name="stale")
    receipt = template.parents[2] / "build" / "direct-mainline" / "receipt.json"
    receipt.parent.mkdir(parents=True)
    if unsafe_component == "leaf":
        receipt.write_text("old\n", encoding="utf-8")
        unsafe_path = receipt
    else:
        unsafe_path = receipt.parent
    monkeypatch.setattr(
        title_materializer,
        "_is_reparse_point",
        lambda path, *_args, **_kwargs: Path(path) == unsafe_path,
    )

    with pytest.raises(TitleMaterializationError, match="RECEIPT_PATH_INVALID"):
        materialize_title(
            issue_date=ISSUE_DATE,
            template_path=template,
            installed_path=installed,
            app_db_path=db,
            receipt_path=receipt,
        )
    if unsafe_component == "leaf":
        assert receipt.read_text(encoding="utf-8") == "old\n"


def test_materializer_rejects_reparse_installed_ancestor_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    template, installed, db = _write_fixture(tmp_path, name="stale")
    unsafe_parent = installed.parent
    monkeypatch.setattr(
        title_materializer,
        "_is_reparse_point",
        lambda path, *_args, **_kwargs: Path(path) == unsafe_parent,
        raising=False,
    )

    with pytest.raises(TitleMaterializationError, match="INSTALLED_PARENT_UNSAFE_PATH"):
        materialize_title(
            issue_date=ISSUE_DATE,
            template_path=template,
            installed_path=installed,
            app_db_path=db,
        )


def test_materializer_rejects_reparse_app_db_leaf_before_connect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    template, installed, db = _write_fixture(tmp_path, name="stale")
    monkeypatch.setattr(
        title_materializer,
        "_is_reparse_point",
        lambda path, *_args, **_kwargs: Path(path) == db,
        raising=False,
    )

    with pytest.raises(TitleMaterializationError, match="APP_DB_UNSAFE_PATH"):
        materialize_title(
            issue_date=ISSUE_DATE,
            template_path=template,
            installed_path=installed,
            app_db_path=db,
        )


def test_materializer_is_idempotent_when_both_surfaces_are_current(tmp_path: Path) -> None:
    expected = "26/08/31 News-Grasp 臨時本線日次バッチ 6:00 記事作成・公開"
    template, installed, db = _write_fixture(tmp_path, name=expected)

    result = materialize_title(
        issue_date=ISSUE_DATE,
        template_path=template,
        installed_path=installed,
        app_db_path=db,
    )

    assert result["ok"] is True
    assert result["installed_changed"] is False
    assert result["app_db_changed"] is False


def test_materializer_derives_each_run_title_from_issue_date(tmp_path: Path) -> None:
    template, installed, db = _write_fixture(
        tmp_path, name="News-Grasp 臨時本線日次バッチ 6:00 記事作成・公開"
    )

    result = materialize_title(
        issue_date="2026-09-01",
        template_path=template,
        installed_path=installed,
        app_db_path=db,
    )

    assert result["materialized_title"] == (
        "26/09/01 News-Grasp 臨時本線日次バッチ 6:00 記事作成・公開"
    )


def test_full_sync_does_not_erase_a_materialized_name(tmp_path: Path) -> None:
    expected = "26/08/31 News-Grasp 臨時本線日次バッチ 6:00 記事作成・公開"
    template, installed, _db = _write_fixture(tmp_path, name=expected)

    rendered = _render_installed(
        template_path=template,
        installed_path=installed,
        repo_root=template.parents[2],
        project_target={"type": "project", "project_id": "project-1"},
        now_ms=200,
    )

    assert f'name = {json.dumps(expected, ensure_ascii=False)}' in rendered


def test_materializer_rejects_a_noncanonical_template_name(tmp_path: Path) -> None:
    template, installed, db = _write_fixture(tmp_path, name="stale")
    template.write_text('name = "News-Grasp title {{date}}"\n', encoding="utf-8")

    with pytest.raises(TitleMaterializationError, match="TEMPLATE_NAME_NOT_CANONICAL"):
        materialize_title(
            issue_date=ISSUE_DATE,
            template_path=template,
            installed_path=installed,
            app_db_path=db,
        )


def test_scheduled_entrypoint_is_a_hidden_title_only_preflight() -> None:
    root = Path(__file__).parents[1]
    entrypoint = root / "scripts" / "ops" / "news-grasp-title-materializer.pyw"
    installer = root / "scripts" / "ops" / "install-news-grasp-title-materializer.ps1"
    entrypoint_text = entrypoint.read_text(encoding="utf-8-sig")
    installer_text = installer.read_text(encoding="utf-8-sig")

    assert "news_grasp_title_materializer" in entrypoint_text
    assert "-Daily -At" in installer_text
    assert "AddMinutes(59)" in installer_text
    assert "pythonw.exe" in installer_text
    assert "MultipleInstances IgnoreNew" in installer_text
    assert "-Hidden" in installer_text
    assert "-StartWhenAvailable" in installer_text
    assert "News-Grasp Production" not in installer_text
    assert "news-grasp-runner" not in installer_text
