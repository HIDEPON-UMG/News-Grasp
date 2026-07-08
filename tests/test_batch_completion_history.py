from __future__ import annotations

import json
from pathlib import Path

from tools.batch_completion_history import classify_day


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _live_runner_readiness() -> dict:
    return {
        "ok": True,
        "repo_runner": {"sha256": "runner-sha"},
        "live_runner": {"sha256": "runner-sha"},
        "repo_watcher": {"sha256": "watcher-sha"},
        "live_watcher": {"sha256": "watcher-sha"},
        "repo_bootstrap": {"sha256": "bootstrap-sha"},
        "live_bootstrap": {"sha256": "bootstrap-sha"},
        "scheduled_task": {
            "ok": True,
            "state": "Ready",
            "next_run_time": "2026-06-30T06:00:00",
            "number_of_missed_runs": 0,
            "trigger_start_minutes": 360,
            "runner_action_is_production_start": True,
            "targets_live_bootstrap": True,
            "bootstrap_repairs_before_run": True,
            "bootstrap_before_runner": True,
            "bootstrap_trigger_start_minutes": 355,
            "bootstrap_next_run_time": "2026-06-30T05:55:00",
            "bootstrap_number_of_missed_runs": 0,
            "bootstrap_state": "Ready",
            "bootstrap_last_task_result": 0,
            "bootstrap_action_is_smoke_test": True,
            "bootstrap_action_uses_short_timeout": True,
            "bootstrap_action_uses_isolated_state_log": True,
            "bootstrap_targets_live_bootstrap": True,
        },
        "canary": {"ok": True, "status": "smoke_ok"},
    }


def test_batch_completion_history_classifies_complete_day(tmp_path: Path) -> None:
    _write_json(tmp_path / "state" / "news-grasp-runner-state.json", {"date": "2026-06-29", "status": "publish_complete"})
    _write_json(tmp_path / "docs" / "publish-status.json", {"date": "2026-06-29", "result": "published_ok"})
    _write_json(tmp_path / "data" / "distribution" / "2026-06-29.json", {"date": "2026-06-29"})
    _write_json(
        tmp_path / "build" / "publish-complete" / "2026-06-29.json",
        {"date": "2026-06-29", "ok": True, "live_runner_readiness": _live_runner_readiness()},
    )

    assert classify_day(tmp_path, "2026-06-29").status == "complete"


def test_batch_completion_history_classifies_fallback_as_forbidden(tmp_path: Path) -> None:
    _write_json(tmp_path / "state" / "news-grasp-runner-state.json", {"date": "2026-06-29", "status": "fallback_ok"})
    _write_json(
        tmp_path / "docs" / "publish-status.json",
        {"date": "2026-06-29", "result": "published_fallback_with_notice"},
    )

    result = classify_day(tmp_path, "2026-06-29")

    assert result.status == "forbidden_fallback"
    assert "通常日次 fallback 完走扱いは禁止" in result.reason


def test_batch_completion_history_missing_sources_are_unverified_not_complete(tmp_path: Path) -> None:
    _write_json(tmp_path / "state" / "news-grasp-runner-state.json", {"date": "2026-06-29", "status": "running"})

    result = classify_day(tmp_path, "2026-06-29")

    assert result.status == "unverified"


def test_batch_completion_history_uses_publish_complete_manifest(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "build" / "publish-complete" / "2026-06-29.json",
        {"date": "2026-06-29", "ok": True, "live_runner_readiness": _live_runner_readiness()},
    )
    _write_json(tmp_path / "docs" / "publish-status.json", {"date": "2026-06-29", "result": "published_ok"})
    _write_json(tmp_path / "data" / "distribution" / "2026-06-29.json", {"date": "2026-06-29"})

    result = classify_day(tmp_path, "2026-06-29")

    assert result.status == "complete"
    assert "publish-complete manifest" in result.reason


def test_batch_completion_history_flags_missing_live_readiness_as_overclaim(tmp_path: Path) -> None:
    _write_json(tmp_path / "state" / "news-grasp-runner-state.json", {"date": "2026-06-29", "status": "publish_complete"})
    _write_json(tmp_path / "build" / "publish-complete" / "2026-06-29.json", {"date": "2026-06-29", "ok": True})
    _write_json(tmp_path / "docs" / "publish-status.json", {"date": "2026-06-29", "result": "published_ok"})
    _write_json(tmp_path / "data" / "distribution" / "2026-06-29.json", {"date": "2026-06-29"})

    result = classify_day(tmp_path, "2026-06-29")

    assert result.status == "completion_overclaim"
    assert "live ops readiness" in result.reason


def test_batch_completion_history_rejects_thin_live_readiness_manifest(tmp_path: Path) -> None:
    """履歴分類も scheduler 詳細を持たない薄い readiness manifest を complete 扱いしない。"""
    _write_json(tmp_path / "state" / "news-grasp-runner-state.json", {"date": "2026-06-29", "status": "publish_complete"})
    _write_json(tmp_path / "docs" / "publish-status.json", {"date": "2026-06-29", "result": "published_ok"})
    _write_json(tmp_path / "data" / "distribution" / "2026-06-29.json", {"date": "2026-06-29"})
    thin_readiness = _live_runner_readiness()
    thin_readiness["scheduled_task"] = {"targets_live_bootstrap": True}
    _write_json(
        tmp_path / "build" / "publish-complete" / "2026-06-29.json",
        {"date": "2026-06-29", "ok": True, "live_runner_readiness": thin_readiness},
    )

    result = classify_day(tmp_path, "2026-06-29")

    assert result.status == "completion_overclaim"
    assert "live ops readiness" in result.reason


def test_batch_completion_history_rejects_nonproduction_runner_action(tmp_path: Path) -> None:
    """履歴 manifest でも 06:00 task の smoke/status/start-only action を complete 扱いしない。"""
    _write_json(tmp_path / "state" / "news-grasp-runner-state.json", {"date": "2026-06-29", "status": "publish_complete"})
    _write_json(tmp_path / "docs" / "publish-status.json", {"date": "2026-06-29", "result": "published_ok"})
    _write_json(tmp_path / "data" / "distribution" / "2026-06-29.json", {"date": "2026-06-29"})
    readiness = _live_runner_readiness()
    readiness["scheduled_task"]["runner_action_is_production_start"] = False
    _write_json(
        tmp_path / "build" / "publish-complete" / "2026-06-29.json",
        {"date": "2026-06-29", "ok": True, "live_runner_readiness": readiness},
    )

    result = classify_day(tmp_path, "2026-06-29")

    assert result.status == "completion_overclaim"
    assert "live ops readiness" in result.reason


def test_batch_completion_history_rejects_missed_bootstrap_readiness(tmp_path: Path) -> None:
    """direct runner 例外も Bootstrap の missed run と NextRunTime を履歴側で再検証する。"""
    _write_json(tmp_path / "state" / "news-grasp-runner-state.json", {"date": "2026-06-29", "status": "publish_complete"})
    _write_json(tmp_path / "docs" / "publish-status.json", {"date": "2026-06-29", "result": "published_ok"})
    _write_json(tmp_path / "data" / "distribution" / "2026-06-29.json", {"date": "2026-06-29"})
    readiness = _live_runner_readiness()
    readiness["scheduled_task"] = {
        "ok": True,
        "state": "Ready",
        "next_run_time": "2026-06-30T06:00:00",
        "number_of_missed_runs": 0,
        "trigger_start_minutes": 360,
        "targets_live_runner": True,
        "bootstrap_repairs_before_run": True,
        "bootstrap_trigger_start_minutes": 355,
        "bootstrap_next_run_time": "",
        "bootstrap_number_of_missed_runs": 1,
        "bootstrap_state": "Ready",
        "bootstrap_last_task_result": 0,
        "bootstrap_action_is_smoke_test": True,
        "bootstrap_action_uses_short_timeout": True,
        "bootstrap_action_uses_isolated_state_log": True,
        "bootstrap_targets_live_bootstrap": True,
    }
    _write_json(
        tmp_path / "build" / "publish-complete" / "2026-06-29.json",
        {"date": "2026-06-29", "ok": True, "live_runner_readiness": readiness},
    )

    result = classify_day(tmp_path, "2026-06-29")

    assert result.status == "completion_overclaim"
    assert "live ops readiness" in result.reason


def test_batch_completion_history_rejects_direct_runner_interlock_without_reexec(tmp_path: Path) -> None:
    """OC-13 以前の direct_runner_pre_run_interlock=true だけの manifest は complete 扱いしない。"""
    _write_json(tmp_path / "state" / "news-grasp-runner-state.json", {"date": "2026-06-29", "status": "publish_complete"})
    _write_json(tmp_path / "docs" / "publish-status.json", {"date": "2026-06-29", "result": "published_ok"})
    _write_json(tmp_path / "data" / "distribution" / "2026-06-29.json", {"date": "2026-06-29"})
    readiness = _live_runner_readiness()
    readiness["scheduled_task"].update(
        {
            "targets_live_bootstrap": False,
            "targets_live_runner": True,
            "direct_runner_pre_run_interlock": True,
        }
    )
    readiness["scheduled_task"].pop("direct_runner_pre_run_reexec", None)
    _write_json(
        tmp_path / "build" / "publish-complete" / "2026-06-29.json",
        {"date": "2026-06-29", "ok": True, "live_runner_readiness": readiness},
    )

    result = classify_day(tmp_path, "2026-06-29")

    assert result.status == "completion_overclaim"
    assert "live ops readiness" in result.reason


def test_batch_completion_history_flags_live_runner_state_drift(
    tmp_path: Path, monkeypatch
) -> None:
    live_state = tmp_path / "live" / "news-grasp-runner-state.json"
    _write_json(live_state, {"date": "2026-06-29", "status": "publish_failed"})
    _write_json(
        tmp_path / "build" / "publish-complete" / "2026-06-29.json",
        {"date": "2026-06-29", "ok": True, "live_runner_readiness": _live_runner_readiness()},
    )
    _write_json(tmp_path / "docs" / "publish-status.json", {"date": "2026-06-29", "result": "published_ok"})
    _write_json(tmp_path / "data" / "distribution" / "2026-06-29.json", {"date": "2026-06-29"})
    monkeypatch.setenv("NEWS_GRASP_RUNNER_STATE_FILE", str(live_state))

    result = classify_day(tmp_path, "2026-06-29")

    assert result.status == "state_reconciliation_required"
    assert "publish_complete manifest conflicts with runner state publish_failed" in result.reason
    assert str(live_state) in result.evidence
