"""direct 本線の収束・回復契約テスト。

旧 runner PowerShell の収束仕様は tombstone 化済みである。ここでは
direct runtime が exact successor と surface-scoped defer を持つことだけを
実行可能な consumer 経由で検査する。
"""
from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parent.parent
ISSUE_DATE = "2026-08-30"


class _Verifier:
    def __init__(self, failures: dict[str, dict[str, Any]] | None = None) -> None:
        self.failures = failures or {}

    def verify(self, stage_id: str, **_: Any) -> dict[str, Any]:
        if stage_id in self.failures:
            return self.failures[stage_id]
        if stage_id == "title_control":
            return {
                "ok": True,
                "status": "green",
                "issue_date": ISSUE_DATE,
                "title_status": "already_ok",
                "actual_title": "26/08/30 News-Grasp 臨時本線日次バッチ 6:00 記事作成・公開",
                "post_publish_issue_list": [],
            }
        return {"ok": True, "status": "green", "issue_date": ISSUE_DATE}


def _start(tmp_path: Path, *, verifier: Any | None = None) -> tuple[Any, Any, dict[str, Any]]:
    api = importlib.import_module("tools.news_grasp_direct_runtime")
    store = api.DirectRunStore(tmp_path / "direct-mainline", semantic_verifier=verifier or _Verifier())
    run = api.start_run(store, cwd=ROOT, issue_date=ISSUE_DATE)
    return api, store, run


def test_direct_runtime_keeps_exact_successor_after_stage_red(tmp_path: Path) -> None:
    api, store, run = _start(
        tmp_path,
        verifier=_Verifier(
            {
                "issue_inventory": {
                    "ok": False,
                    "status": "red",
                    "failures": ["inventory_missing"],
                }
            }
        ),
    )
    ok_title = {
        "ok": True,
        "status": "green",
        "issue_date": ISSUE_DATE,
        "title_status": "already_ok",
        "actual_title": "26/08/30 News-Grasp 臨時本線日次バッチ 6:00 記事作成・公開",
        "post_publish_issue_list": [],
    }

    first = api.run_exact_successor(
        store,
        run_id=run["run_id"],
        writer_lease=run["writer_lease"],
        observed_surface=ok_title,
    )
    second = api.run_exact_successor(
        store,
        run_id=run["run_id"],
        writer_lease=run["writer_lease"],
    )

    assert first["completed_stage"] == "title_control"
    assert second["status"] == "red"
    assert second["exact_successor"] == "issue_inventory"
    assert "inventory_missing" in second["failures"]


def test_direct_runtime_defers_external_surface_without_task_wide_stop(tmp_path: Path) -> None:
    api, store, run = _start(
        tmp_path,
        verifier=_Verifier(
            {
                "youtube_podcasts": {
                    "ok": False,
                    "status": "deferred",
                    "reason": "youtube_quota_exceeded",
                    "surface": "youtube_daily",
                    "surface_scoped": True,
                }
            }
        ),
    )

    state = run
    for _ in range(api.DIRECT_STAGES.index("youtube_podcasts")):
        evidence = {"ok": True, "status": "green", "issue_date": ISSUE_DATE}
        if state["current_stage"] == "title_control":
            evidence.update(
                {
                    "title_status": "already_ok",
                    "actual_title": "26/08/30 News-Grasp 臨時本線日次バッチ 6:00 記事作成・公開",
                    "post_publish_issue_list": [],
                }
            )
        state = api.run_exact_successor(
            store,
            run_id=run["run_id"],
            writer_lease=run["writer_lease"],
            observed_surface=evidence,
        )

    deferred = api.run_exact_successor(
        store,
        run_id=run["run_id"],
        writer_lease=run["writer_lease"],
    )

    assert deferred["status"] == "deferred"
    assert deferred["completed_stage"] == "youtube_podcasts"
    assert deferred["current_stage"] == "playlist"
    assert deferred["exact_successor"] == "playlist"
    assert deferred["surface_failures"][0]["reason"] == "youtube_quota_exceeded"


def test_direct_runtime_rejects_out_of_order_stage(tmp_path: Path) -> None:
    api, store, run = _start(tmp_path)

    with pytest.raises(ValueError, match="stage order successor violation"):
        api.advance_stage(
            store,
            run_id=run["run_id"],
            writer_lease=run["writer_lease"],
            stage_id="daily_quality",
            evidence={"ok": True, "status": "green", "issue_date": ISSUE_DATE},
            semantic_verifier=_Verifier(),
        )


def test_legacy_runner_file_remains_absent() -> None:
    assert not (ROOT / "scripts" / "ops" / "news-grasp-runner.ps1").exists()
    assert not (Path.home() / "bin" / "news-grasp-runner.ps1").exists()
