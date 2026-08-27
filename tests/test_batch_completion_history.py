from __future__ import annotations

import json
from pathlib import Path

from tools.batch_completion_history import build_weekly_audit, classify_day, parse_log_attempts


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _live_runner_readiness() -> dict:
    return {
        "ok": True,
        "external_control": {
            "status": "ready",
            "modelLaunchCount": 0,
        },
        "repo_runner": {"sha256": "runner-sha"},
        "live_runner": {"sha256": "runner-sha"},
        "repo_watcher": {"sha256": "watcher-sha"},
        "live_watcher": {"sha256": "watcher-sha"},
        "repo_bootstrap": {"sha256": "bootstrap-sha"},
        "live_bootstrap": {"sha256": "bootstrap-sha"},
        "repo_task_launcher": {"sha256": "launcher-sha"},
        "live_task_launcher": {"sha256": "launcher-sha"},
        "scheduled_task": {
            "ok": True,
            "state": "Ready",
            "next_run_time": "2026-06-30T06:00:00",
            "number_of_missed_runs": 0,
            "trigger_start_minutes": 360,
            "runner_action_is_production_start": True,
            "targets_live_task_launcher": True,
            "task_launcher_mode_ok": True,
            "task_launcher_ready": True,
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


def test_batch_completion_history_preserves_failed_schedule_after_recovery(tmp_path: Path) -> None:
    """publish_complete でも scheduled failure は recovered_after_failed_schedule として残す。"""
    _write_json(tmp_path / "state" / "news-grasp-runner-state.json", {"date": "2026-06-29", "status": "publish_complete"})
    _write_json(tmp_path / "docs" / "publish-status.json", {"date": "2026-06-29", "result": "published_ok"})
    _write_json(tmp_path / "data" / "distribution" / "2026-06-29.json", {"date": "2026-06-29"})
    readiness = _live_runner_readiness()
    readiness["last_scheduled_attempt"] = {
        "status": "failed",
        "last_task_result": 72,
        "last_run_time": "2026-06-29T06:00:00",
    }
    readiness["next_run_readiness"] = {"ok": True, "status": "ready"}
    _write_json(
        tmp_path / "build" / "publish-complete" / "2026-06-29.json",
        {
            "date": "2026-06-29",
            "ok": True,
            "public_status": "green",
            "scheduled_attempt_status": "failed_then_recovered",
            "recovery_attempt_status": "succeeded",
            "live_runner_readiness": readiness,
        },
    )

    result = classify_day(tmp_path, "2026-06-29")

    assert result.status == "recovered_after_failed_schedule"
    assert result.scheduled_attempt["status"] == "failed"
    assert result.public_status == "green"


def test_parse_log_attempts_separates_scheduled_and_recovery_ranges(tmp_path: Path) -> None:
    log = tmp_path / "2026-07-31.log"
    log.write_text(
        "\n".join(
            [
                "[2026-07-31 06:00:02.763] news-grasp-runner.ps1 start (smoke=False, recover=False, no_publish=False, resume_from_stage=, pid=10)",
                "[2026-07-31 06:20:00.000] daily-quality gate FAILED category_digest_empty",
                "[2026-07-31 06:53:42.696] news-grasp-runner.ps1 start (run_id=recovery-1, smoke=False, recover=False, no_publish=False, resume_from_stage=deepdive, pid=20)",
                "[2026-07-31 07:24:52.000] publish_complete",
            ]
        ),
        encoding="utf-8",
    )

    attempts = parse_log_attempts(log, "2026-07-31")

    assert attempts[0]["kind"] == "scheduled"
    assert attempts[0]["run_id_status"] == "legacy_missing"
    assert attempts[0]["line_start"] == 1
    assert attempts[0]["line_end"] == 2
    assert "daily-quality" in attempts[0]["terminal_gate"]
    assert attempts[1]["kind"] == "recovery"
    assert attempts[1]["run_id"] == "recovery-1"
    assert attempts[1]["line_start"] == 3
    assert attempts[1]["line_end"] == 4
    assert attempts[1]["terminal_state"] == "publish_complete"


def test_build_weekly_audit_flags_all_days_scheduled_failure(tmp_path: Path) -> None:
    inputs = {
        f"2026-07-{day:02d}": {
            "scheduled_last_task_result": day,
            "six_forty_classification": "異常終了",
            "first_terminal_gate": "daily-quality",
            "recovery_status": "succeeded",
            "public_status": "green",
            "incident_evidence": [],
            "residuals": [],
        }
        for day in range(25, 32)
    }

    result = build_weekly_audit(
        repo_root=tmp_path,
        start_date="2026-07-25",
        end_date="2026-07-31",
        daily_inputs=inputs,
        log_dir=tmp_path / "logs",
        evidence_source="repair-prompt.md",
    )

    assert len(result["days"]) == 7
    assert result["weekly_scheduled_attempt_status"] == "regression"
    assert result["weekly_issue_code"] == "weekly_scheduled_completion_regression"
    assert all(row["scheduled_attempt"]["status"] == "failed" for row in result["days"])
    assert all(row["public_status"] == "green" for row in result["days"])


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
            "targets_live_task_launcher": False,
            "task_launcher_mode_ok": False,
            "task_launcher_ready": False,
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
