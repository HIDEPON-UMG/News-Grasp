#!/usr/bin/env python3
"""tools.daily_self_heal の契約テスト。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import tools.daily_self_heal as dsh
from tools.daily_self_heal import (
    classify_phase0,
    compare_files,
    emit_alert,
    evaluate_deadman,
    normalize_failure_signature,
    verify_publish,
)


def test_phase0_prioritizes_bin_drift_before_content_repair() -> None:
    """bin drift がある日は content repair へ進む前に同期不備を主因にする。"""
    snapshot = {
        "scheduler": {"exists": True, "last_result": 1},
        "logs": {"runner_invoked": True},
        "repo_bin": {"synced": False},
        "content": {"gate_failed": True, "gate_id": "daily-quality"},
    }

    assert classify_phase0(snapshot)["root_cause"] == "bin_drift"


def test_phase0_detects_no_run_before_pages_or_content() -> None:
    """runner が未発火なら Pages や content gate の話に進まない。"""
    snapshot = {
        "scheduler": {"exists": True, "last_run_missing": True},
        "pages": {"public_sentinel_ok": False},
        "content": {"gate_failed": True, "gate_id": "url-liveness"},
    }

    assert classify_phase0(snapshot)["root_cause"] == "no_run_detected"


def test_phase0_accepts_live_snapshot_aliases_and_failed_task_result() -> None:
    """PowerShell 収集値のキー名でも LastTaskResult 失敗を主因にできる。"""
    snapshot = {
        "scheduled_task": {"exists": True, "last_task_result": 1},
        "runner": {"status": "failed", "date": "2026-06-15"},
        "bin": {"synced": True},
    }

    assert classify_phase0(snapshot)["root_cause"] == "runner_failed"


def test_phase0_detects_yesterday_running_state_as_stale() -> None:
    """当日でない running state は成功扱いせず stale として復旧対象にする。"""
    snapshot = {
        "expected_date": "2026-06-15",
        "scheduler": {"exists": True, "last_result": 1},
        "state": {"status": "running", "date": "2026-06-14"},
        "logs": {"runner_invoked": True},
    }

    assert classify_phase0(snapshot)["root_cause"] == "stale_runner"


def test_deadman_alerts_for_stale_and_deduplicates(tmp_path: Path) -> None:
    """stale / fallback_ok 等は Web Push 以外の alert log に一度だけ残す。"""
    decision = evaluate_deadman(
        state={"status": "stale", "date": "2026-06-15"},
        now=datetime(2026, 6, 15, 12, tzinfo=timezone.utc),
        expected_date="2026-06-15",
        max_ok_age_hours=27,
    )
    assert decision["alert"] is True
    assert decision["reason"] == "stale"

    log = tmp_path / "alerts.jsonl"
    marker = tmp_path / "marker.json"
    first = emit_alert({"date": "2026-06-15", **decision}, alert_log=log, marker_path=marker)
    second = emit_alert({"date": "2026-06-15", **decision}, alert_log=log, marker_path=marker)

    assert first["sent"] is True
    assert second["duplicate"] is True
    assert len(log.read_text(encoding="utf-8").splitlines()) == 1


def test_deadman_accepts_fresh_today_ok() -> None:
    now = datetime(2026, 6, 15, 12, tzinfo=timezone.utc)
    decision = evaluate_deadman(
        state={
            "status": "ok",
            "date": "2026-06-15",
            "updated_at": (now - timedelta(hours=1)).isoformat(),
        },
        now=now,
        expected_date="2026-06-15",
        max_ok_age_hours=27,
    )

    assert decision["alert"] is False


def test_failure_signature_normalizes_url_to_host() -> None:
    assert normalize_failure_signature(
        gate_id="URL-Liveness",
        error_code="Date-Fatal",
        artifact_identity="AI",
        url_or_category="https://Example.COM/news/123",
    ) == "url-liveness|date-fatal|ai|example.com"


def test_compare_files_detects_drift(tmp_path: Path) -> None:
    repo = tmp_path / "repo.ps1"
    live = tmp_path / "live.ps1"
    repo.write_text("runner-v1", encoding="utf-8")
    live.write_text("runner-v2", encoding="utf-8")

    result = compare_files(repo, live)

    assert result["synced"] is False
    assert result["repo_sha256"] != result["live_sha256"]


def test_deadman_cli_payload_is_json_serializable(tmp_path: Path) -> None:
    """alert record は外部通知やログ保存へそのまま渡せる JSON 形に固定する。"""
    record = {
        "date": "2026-06-15",
        "alert": True,
        "reason": "fallback_ok",
        "status": "fallback_ok",
    }
    result = emit_alert(record, alert_log=tmp_path / "a.jsonl", marker_path=tmp_path / "m.json")

    json.dumps(result)


def test_verify_publish_requires_remote_head_and_public_status(monkeypatch, tmp_path: Path) -> None:
    """ok は remote HEAD と公開 publish-status の両方が揃った後だけ。"""
    calls: list[list[str]] = []

    def fake_git(repo_root: Path, args: list[str]) -> str:
        calls.append(args)
        if args == ["rev-parse", "HEAD"]:
            return "abc123"
        if args == ["ls-remote", "origin", "refs/heads/main"]:
            return "abc123\trefs/heads/main"
        raise AssertionError(args)

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return json.dumps({"result": "published_ok", "date": "2026-06-15"}).encode("utf-8")

    monkeypatch.setattr(dsh, "_git_output", fake_git)
    monkeypatch.setattr(dsh.urllib.request, "urlopen", lambda *a, **k: FakeResponse())

    result = verify_publish(
        repo_root=tmp_path,
        date="2026-06-15",
        remote="origin",
        branch="main",
        public_base_url="https://example.com/News-Grasp/",
        wait_sec=0,
        poll_sec=1,
    )

    assert result["ok"] is True
    assert calls == [["rev-parse", "HEAD"], ["ls-remote", "origin", "refs/heads/main"]]


def test_verify_publish_rejects_public_status_mismatch(monkeypatch, tmp_path: Path) -> None:
    def fake_git(_repo: Path, args: list[str]) -> str:
        if args == ["rev-parse", "HEAD"]:
            return "abc123"
        if args == ["ls-remote", "origin", "refs/heads/main"]:
            return "abc123\trefs/heads/main"
        raise AssertionError(args)

    monkeypatch.setattr(dsh, "_git_output", fake_git)

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return json.dumps({"result": "published_fallback_with_notice", "date": "2026-06-15"}).encode("utf-8")

    monkeypatch.setattr(dsh.urllib.request, "urlopen", lambda *a, **k: FakeResponse())

    result = verify_publish(
        repo_root=tmp_path,
        date="2026-06-15",
        remote="origin",
        branch="main",
        public_base_url="https://example.com/News-Grasp/",
        wait_sec=0,
        poll_sec=1,
    )

    assert result["ok"] is False
    assert result["reason"] == "public_sentinel_missing"
