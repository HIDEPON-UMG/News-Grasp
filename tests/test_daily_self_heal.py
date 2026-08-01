#!/usr/bin/env python3
"""tools.daily_self_heal の契約テスト。"""
from __future__ import annotations

import json
import subprocess
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


def _write_local_sw(repo_root: Path, version: str = "expected-version") -> None:
    docs = repo_root / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "sw.js").write_text(f"const SW_VERSION = '{version}';\n", encoding="utf-8")


def _mock_pages_build_success(monkeypatch, commit: str = "abc123") -> None:
    monkeypatch.setattr(
        dsh,
        "verify_pages_build",
        lambda **_kwargs: {"ok": True, "reason": "", "status": "built", "commit": commit, "url": "pages"},
    )


def _mock_deploy_workflow_success(monkeypatch, commit: str = "abc123") -> None:
    monkeypatch.setattr(
        dsh,
        "verify_deploy_workflow",
        lambda **_kwargs: {
            "ok": True,
            "reason": "",
            "status": "completed",
            "conclusion": "success",
            "head_sha": commit,
            "run_id": 123,
            "url": "workflow",
        },
    )


def _write_deploy_workflow(repo_root: Path) -> None:
    workflow_dir = repo_root / ".github" / "workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    (workflow_dir / "deploy-pages.yml").write_text("name: Deploy Pages\n", encoding="utf-8")


def _runner_with_pre_run_interlock_source() -> str:
    return """
$BootstrapSmokeStateFile = 'ng-smoke-state.json'
$BootstrapSmokeLogDir = 'ng-smoke-logs'
$BootstrapSmokeEarliestMinutes = 5 * 60 + 55
$BootstrapSmokeFreshnessMinutes = 15
function Test-NormalDailyPublishRun { return $true }
function Test-PreRunBootstrapSmokeMarker {
    $state.updated_at | Out-Null
    $item.LastWriteTime | Out-Null
    $BootstrapSmokeEarliestMinutes | Out-Null
    $BootstrapSmokeFreshnessMinutes | Out-Null
    $now = Get-Date
    ($now - $state.updated_at).TotalMinutes | Out-Null
}
function Assert-PreRunBootstrapInterlock {
    $bootstrapArgs = @('-SmokeTest', '-PollSeconds', '1', '-TimeoutMinutes', '2', '-StateFile', $BootstrapSmokeStateFile, '-LogDir', $BootstrapSmokeLogDir)
    Start-Process -FilePath 'powershell' -ArgumentList $bootstrapArgs
    blocked_startup_self_repair_failed
}
function Convert-JsonStringArrayToStringList {}
function Invoke-SyncedRunnerReexec {
    $env:NEWS_GRASP_RUNNER_SYNC_REEXEC = '1'
    $runnerArgs = Get-RunnerScriptArguments
    Write-Log 'runner binary drift repaired; relaunching synced runner'
    $proc = Start-Process -FilePath 'powershell' -ArgumentList $runnerArgs -Wait
    $exitCode = [int]$proc.ExitCode
    exit $exitCode
}
function Assert-RunnerBinaryInSync {
    if (Test-NormalDailyPublishRun) {
        Assert-PreRunBootstrapInterlock -ForceRepair
        Invoke-SyncedRunnerReexec
    }
    Invoke-RunnerBinarySyncApprovalBlock
    blocked_startup_self_repair_failed
}
function Invoke-Logged {}
# ===== sentinel: 起動できた事実 =====
Assert-PreRunBootstrapInterlock
Assert-RunnerBinaryInSync
$IsE2EOrDryRun = $false
"""


def _live_runner_readiness_ok() -> dict:
    return {
        "ok": True,
        "reason": "",
        "repo_runner": {"exists": True, "sha256": "runner-sha"},
        "live_runner": {"exists": True, "sha256": "runner-sha"},
        "scheduled_task": {"ok": True, "task_name": "News-Grasp Runner", "targets_live_runner": True},
        "next_run_readiness": {"ok": True, "status": "ready"},
        "last_scheduled_attempt": {
            "status": "failed",
            "last_task_result": 72,
            "last_run_time": "2026-06-20T06:00:00",
        },
        "canary": {"ok": True, "status": "smoke_ok", "returncode": 0},
    }


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
    _write_local_sw(tmp_path)
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
    monkeypatch.setattr(dsh, "_fetch_text", lambda _url: "const SW_VERSION = 'expected-version';\n")
    _mock_deploy_workflow_success(monkeypatch)
    _mock_pages_build_success(monkeypatch)

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


def test_repo_slug_from_remote_url_accepts_github_https_and_ssh() -> None:
    """GitHub Pages API の owner/repo は origin URL から一意に導く。"""
    assert dsh._repo_slug_from_remote_url("https://github.com/HIDEPON-UMG/News-Grasp.git") == (
        "HIDEPON-UMG",
        "News-Grasp",
    )
    assert dsh._repo_slug_from_remote_url("https://github.com/HIDEPON-UMG/News-Grasp/") == (
        "HIDEPON-UMG",
        "News-Grasp",
    )
    assert dsh._repo_slug_from_remote_url("git@github.com:HIDEPON-UMG/News-Grasp.git") == (
        "HIDEPON-UMG",
        "News-Grasp",
    )
    assert dsh._repo_slug_from_remote_url("https://example.com/HIDEPON-UMG/News-Grasp.git") is None


def test_verify_deploy_workflow_uses_workflow_file_api_contract_and_timeout(monkeypatch, tmp_path: Path) -> None:
    """Deploy Pages workflow sentinel は workflow file 正本と同じ branch/head_sha で確認する。"""
    _write_deploy_workflow(tmp_path)
    head = "a" * 40

    def fake_git(_repo: Path, args: list[str]) -> str:
        if args == ["config", "--get", "remote.origin.url"]:
            return "https://github.com/HIDEPON-UMG/News-Grasp.git"
        raise AssertionError(args)

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return json.dumps(
                {
                    "workflow_runs": [
                        {
                            "id": 123,
                            "head_sha": head,
                            "status": "completed",
                            "conclusion": "success",
                            "html_url": "https://github.example/run/123",
                        }
                    ]
                }
            ).encode("utf-8")

    seen: list[dict[str, str | int]] = []

    def fake_urlopen(req, *args, **kwargs):
        headers = dict(req.header_items())
        seen.append(
            {
                "url": req.full_url,
                "accept": headers.get("Accept"),
                "version": headers.get("X-github-api-version"),
                "user_agent": headers.get("User-agent"),
                "timeout": kwargs.get("timeout"),
            }
        )
        return FakeResponse()

    monkeypatch.setattr(dsh, "_git_output", fake_git)
    monkeypatch.setattr(dsh.urllib.request, "urlopen", fake_urlopen)

    result = dsh.verify_deploy_workflow(repo_root=tmp_path, remote="origin", branch="main", expected_commit=head)

    assert result["ok"] is True
    assert result["run_id"] == 123
    assert seen == [
        {
            "url": (
                "https://api.github.com/repos/HIDEPON-UMG/News-Grasp/actions/workflows/"
                f"deploy-pages.yml/runs?branch=main&head_sha={head}&per_page=10"
            ),
            "accept": "application/vnd.github+json",
            "version": "2026-03-10",
            "user_agent": "News-Grasp-PublishVerifier/1.0",
            "timeout": 10,
        }
    ]


def test_verify_deploy_workflow_accepts_manual_dispatch_for_same_head(monkeypatch, tmp_path: Path) -> None:
    """code-only push 後の手動 Deploy Pages も同じ head の公開証跡として扱う。"""
    _write_deploy_workflow(tmp_path)
    head = "a" * 40

    def fake_git(_repo: Path, args: list[str]) -> str:
        if args == ["config", "--get", "remote.origin.url"]:
            return "https://github.com/HIDEPON-UMG/News-Grasp.git"
        raise AssertionError(args)

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return json.dumps(
                {
                    "workflow_runs": [
                        {
                            "id": 456,
                            "event": "workflow_dispatch",
                            "head_sha": head,
                            "status": "completed",
                            "conclusion": "success",
                            "html_url": "https://github.example/run/456",
                        }
                    ]
                }
            ).encode("utf-8")

    monkeypatch.setattr(dsh, "_git_output", fake_git)
    monkeypatch.setattr(dsh.urllib.request, "urlopen", lambda *_args, **_kwargs: FakeResponse())

    result = dsh.verify_deploy_workflow(repo_root=tmp_path, remote="origin", branch="main", expected_commit=head)

    assert result["ok"] is True
    assert result["run_id"] == 456
    assert result["event"] == "workflow_dispatch"


def test_verify_deploy_workflow_recommends_fresh_dispatch_for_completed_failure(monkeypatch, tmp_path: Path) -> None:
    """completed/failure の Deploy Pages は rerun ではなく fresh workflow dispatch で復旧する。"""
    _write_deploy_workflow(tmp_path)
    head = "a" * 40

    def fake_git(_repo: Path, args: list[str]) -> str:
        if args == ["config", "--get", "remote.origin.url"]:
            return "https://github.com/HIDEPON-UMG/News-Grasp.git"
        raise AssertionError(args)

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self) -> bytes:
            return json.dumps(
                {
                    "workflow_runs": [
                        {
                            "id": 789,
                            "head_sha": head,
                            "status": "completed",
                            "conclusion": "failure",
                            "html_url": "https://github.example/run/789",
                        }
                    ]
                }
            ).encode("utf-8")

    monkeypatch.setattr(dsh, "_git_output", fake_git)
    monkeypatch.setattr(dsh.urllib.request, "urlopen", lambda *_args, **_kwargs: FakeResponse())

    result = dsh.verify_deploy_workflow(repo_root=tmp_path, remote="origin", branch="main", expected_commit=head)

    assert result["ok"] is False
    assert result["reason"] == "deploy_workflow_not_success"
    assert result["status"] == "completed"
    assert result["conclusion"] == "failure"
    assert result["recovery"]["action"] == "workflow_dispatch"
    assert result["recovery"]["workflow_file"] == "deploy-pages.yml"
    assert result["recovery"]["branch"] == "main"
    assert "rerun" not in " ".join(result["recovery"]["command"])


def test_dispatch_deploy_workflow_if_failed_posts_workflow_dispatch(monkeypatch, tmp_path: Path) -> None:
    """fresh dispatch command は同一 HEAD failure を確認して workflow dispatch endpoint へ POST する。"""
    head = "a" * 40
    seen: dict[str, object] = {}

    def fake_git(_repo: Path, args: list[str]) -> str:
        if args == ["rev-parse", "HEAD"]:
            return head
        if args == ["config", "--get", "remote.origin.url"]:
            return "https://github.com/HIDEPON-UMG/News-Grasp.git"
        raise AssertionError(args)

    monkeypatch.setattr(dsh, "_git_output", fake_git)
    monkeypatch.setattr(
        dsh,
        "verify_deploy_workflow",
        lambda **_kwargs: {
            "ok": False,
            "reason": "deploy_workflow_not_success",
            "status": "completed",
            "conclusion": "failure",
            "head_sha": head,
            "recovery": {"action": "workflow_dispatch"},
        },
    )

    def fake_post(url: str, payload: dict) -> int:
        seen["url"] = url
        seen["payload"] = payload
        return 204

    monkeypatch.setattr(dsh, "_github_api_post", fake_post)

    result = dsh.dispatch_deploy_workflow_if_failed(tmp_path, remote="origin", branch="main")

    assert result["ok"] is True
    assert result["action"] == "workflow_dispatch"
    assert seen == {
        "url": "https://api.github.com/repos/HIDEPON-UMG/News-Grasp/actions/workflows/deploy-pages.yml/dispatches",
        "payload": {"ref": "main"},
    }


def test_verify_deploy_workflow_normalizes_api_errors_and_bad_payloads(monkeypatch, tmp_path: Path) -> None:
    """Actions API 失敗や壊れた payload は deploy_workflow_unavailable に正規化する。"""
    _write_deploy_workflow(tmp_path)
    head = "a" * 40

    def fake_git(_repo: Path, args: list[str]) -> str:
        if args == ["config", "--get", "remote.origin.url"]:
            return "https://github.com/HIDEPON-UMG/News-Grasp.git"
        raise AssertionError(args)

    class BadPayloadResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return json.dumps({"workflow_runs": {}}).encode("utf-8")

    monkeypatch.setattr(dsh, "_git_output", fake_git)
    monkeypatch.setattr(dsh.urllib.request, "urlopen", lambda *a, **k: BadPayloadResponse())

    bad_payload = dsh.verify_deploy_workflow(repo_root=tmp_path, remote="origin", branch="main", expected_commit=head)
    assert bad_payload["ok"] is False
    assert bad_payload["reason"] == "deploy_workflow_unavailable"

    def fake_urlopen(req, *args, **kwargs):
        raise dsh.urllib.error.HTTPError(req.full_url, 503, "Service Unavailable", {}, None)

    monkeypatch.setattr(dsh.urllib.request, "urlopen", fake_urlopen)
    api_error = dsh.verify_deploy_workflow(repo_root=tmp_path, remote="origin", branch="main", expected_commit=head)
    assert api_error["ok"] is False
    assert api_error["reason"] == "deploy_workflow_unavailable"


def test_verify_publish_requires_deploy_pages_workflow_success(monkeypatch, tmp_path: Path) -> None:
    """Deploy Pages workflow が未完了なら Pages build や public sentinel へ進まない。"""
    _write_local_sw(tmp_path)
    _write_deploy_workflow(tmp_path)
    head = "a" * 40

    def fake_git(_repo: Path, args: list[str]) -> str:
        if args == ["rev-parse", "HEAD"]:
            return head
        if args == ["ls-remote", "origin", "refs/heads/main"]:
            return f"{head}\trefs/heads/main"
        if args == ["config", "--get", "remote.origin.url"]:
            return "https://github.com/HIDEPON-UMG/News-Grasp.git"
        raise AssertionError(args)

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return json.dumps(
                {"workflow_runs": [{"id": 123, "head_sha": head, "status": "in_progress", "conclusion": None}]}
            ).encode("utf-8")

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

    assert result["ok"] is False
    assert result["reason"] == "deploy_workflow_not_success"
    assert result["deploy_workflow"]["status"] == "in_progress"


def test_verify_publish_rejects_deploy_pages_workflow_commit_mismatch(monkeypatch, tmp_path: Path) -> None:
    """Deploy Pages workflow run が別 commit なら publish_complete にしない。"""
    _write_local_sw(tmp_path)
    _write_deploy_workflow(tmp_path)
    head = "a" * 40

    def fake_git(_repo: Path, args: list[str]) -> str:
        if args == ["rev-parse", "HEAD"]:
            return head
        if args == ["ls-remote", "origin", "refs/heads/main"]:
            return f"{head}\trefs/heads/main"
        if args == ["config", "--get", "remote.origin.url"]:
            return "https://github.com/HIDEPON-UMG/News-Grasp.git"
        raise AssertionError(args)

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return json.dumps(
                {
                    "workflow_runs": [
                        {"id": 123, "head_sha": "b" * 40, "status": "completed", "conclusion": "success"}
                    ]
                }
            ).encode("utf-8")

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

    assert result["ok"] is False
    assert result["reason"] == "deploy_workflow_commit_mismatch"
    assert result["deploy_workflow"]["head_sha"] == "b" * 40


def test_verify_pages_build_uses_github_api_contract_and_timeout(monkeypatch, tmp_path: Path) -> None:
    """GitHub Pages build sentinel は固定 endpoint / API version / timeout で確認する。"""
    head = "a" * 40

    def fake_git(_repo: Path, args: list[str]) -> str:
        if args == ["config", "--get", "remote.origin.url"]:
            return "https://github.com/HIDEPON-UMG/News-Grasp.git"
        raise AssertionError(args)

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return json.dumps({"status": "built", "commit": head}).encode("utf-8")

    seen: list[dict[str, str | int]] = []

    def fake_urlopen(req, *args, **kwargs):
        headers = dict(req.header_items())
        seen.append(
            {
                "url": req.full_url,
                "accept": headers.get("Accept"),
                "version": headers.get("X-github-api-version"),
                "user_agent": headers.get("User-agent"),
                "timeout": kwargs.get("timeout"),
            }
        )
        return FakeResponse()

    monkeypatch.setattr(dsh, "_git_output", fake_git)
    monkeypatch.setattr(dsh.urllib.request, "urlopen", fake_urlopen)

    result = dsh.verify_pages_build(repo_root=tmp_path, remote="origin", expected_commit=head)

    assert result["ok"] is True
    assert seen == [
        {
            "url": "https://api.github.com/repos/HIDEPON-UMG/News-Grasp/pages/builds/latest",
            "accept": "application/vnd.github+json",
            "version": "2026-03-10",
            "user_agent": "News-Grasp-PublishVerifier/1.0",
            "timeout": 10,
        }
    ]


def test_verify_pages_build_normalizes_api_errors(monkeypatch, tmp_path: Path) -> None:
    """Pages API が失敗した場合は publish_complete に進まず typed reason へ正規化する。"""
    head = "a" * 40

    def fake_git(_repo: Path, args: list[str]) -> str:
        if args == ["config", "--get", "remote.origin.url"]:
            return "https://github.com/HIDEPON-UMG/News-Grasp.git"
        raise AssertionError(args)

    def fake_urlopen(req, *args, **kwargs):
        raise dsh.urllib.error.HTTPError(req.full_url, 503, "Service Unavailable", {}, None)

    monkeypatch.setattr(dsh, "_git_output", fake_git)
    monkeypatch.setattr(dsh.urllib.request, "urlopen", fake_urlopen)

    result = dsh.verify_pages_build(repo_root=tmp_path, remote="origin", expected_commit=head)

    assert result["ok"] is False
    assert result["reason"] == "pages_build_unavailable"
    assert result["url"] == "https://api.github.com/repos/HIDEPON-UMG/News-Grasp/pages/builds/latest"


def test_verify_pages_build_falls_back_to_workflow_pages_status_when_latest_unavailable(monkeypatch, tmp_path: Path) -> None:
    """workflow Pages では pages/builds/latest が 404 でも Pages status を正本 fallback にできる。"""
    head = "a" * 40

    def fake_git(_repo: Path, args: list[str]) -> str:
        if args == ["config", "--get", "remote.origin.url"]:
            return "https://github.com/HIDEPON-UMG/News-Grasp.git"
        raise AssertionError(args)

    class FakePagesResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            payload = {
                "status": "built",
                "build_type": "workflow",
                "source": {"branch": "main", "path": "/docs"},
            }
            return json.dumps(payload).encode("utf-8")

    seen: list[dict[str, str | None]] = []

    def fake_urlopen(req, *args, **kwargs):
        headers = dict(req.header_items())
        seen.append({"url": req.full_url, "authorization": headers.get("Authorization")})
        if req.full_url.endswith("/pages/builds/latest"):
            raise dsh.urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
        if req.full_url.endswith("/pages") and headers.get("Authorization") == "Bearer gh-test-token":
            return FakePagesResponse()
        raise dsh.urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(dsh, "_gh_auth_token", lambda: "gh-test-token")
    monkeypatch.setattr(dsh, "_git_output", fake_git)
    monkeypatch.setattr(dsh.urllib.request, "urlopen", fake_urlopen)

    result = dsh.verify_pages_build(repo_root=tmp_path, remote="origin", expected_commit=head)

    assert result["ok"] is True
    assert result["url"] == "https://api.github.com/repos/HIDEPON-UMG/News-Grasp/pages"
    assert result["commit"] == head
    assert result["build_type"] == "workflow"
    assert result["source_branch"] == "main"
    assert result["source_path"] == "/docs"
    assert any(call["authorization"] == "Bearer gh-test-token" for call in seen)


def test_verify_pages_build_accepts_workflow_pages_status_when_latest_build_is_stale(monkeypatch, tmp_path: Path) -> None:
    """workflow Pages の latest build が古い commit を返す場合は Pages status + branch を使う。"""
    head = "a" * 40
    stale = "b" * 40

    def fake_git(_repo: Path, args: list[str]) -> str:
        if args == ["config", "--get", "remote.origin.url"]:
            return "git@github.com:HIDEPON-UMG/News-Grasp.git"
        raise AssertionError(args)

    class FakeResponse:
        def __init__(self, payload: dict):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return json.dumps(self.payload).encode("utf-8")

    def fake_urlopen(req, *args, **kwargs):
        if req.full_url.endswith("/pages/builds/latest"):
            return FakeResponse({"status": "built", "commit": stale})
        if req.full_url.endswith("/pages"):
            return FakeResponse(
                {
                    "status": "built",
                    "build_type": "workflow",
                    "source": {"branch": "main", "path": "/docs"},
                }
            )
        raise AssertionError(req.full_url)

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(dsh, "_git_output", fake_git)
    monkeypatch.setattr(dsh.urllib.request, "urlopen", fake_urlopen)

    result = dsh.verify_pages_build(repo_root=tmp_path, remote="origin", expected_commit=head)

    assert result["ok"] is True
    assert result["commit"] == head
    assert result["latest_build"]["commit"] == stale
    assert result["latest_detail"] == f"latest_commit_mismatch:{stale}"


def test_verify_publish_requires_pages_build_for_remote_head(monkeypatch, tmp_path: Path) -> None:
    """remote HEAD だけでなく、同じ commit の GitHub Pages build が built であることを要求する。"""
    _write_local_sw(tmp_path)
    head = "a" * 40

    def fake_git(_repo: Path, args: list[str]) -> str:
        if args == ["rev-parse", "HEAD"]:
            return head
        if args == ["ls-remote", "origin", "refs/heads/main"]:
            return f"{head}\trefs/heads/main"
        if args == ["config", "--get", "remote.origin.url"]:
            return "https://github.com/HIDEPON-UMG/News-Grasp.git"
        raise AssertionError(args)

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return json.dumps({"status": "queued", "commit": head}).encode("utf-8")

    seen: list[str] = []

    def fake_urlopen(req, *args, **kwargs):
        url = getattr(req, "full_url", str(req))
        seen.append(url)
        assert url == "https://api.github.com/repos/HIDEPON-UMG/News-Grasp/pages/builds/latest"
        return FakeResponse()

    monkeypatch.setattr(dsh, "_git_output", fake_git)
    monkeypatch.setattr(dsh.urllib.request, "urlopen", fake_urlopen)
    _mock_deploy_workflow_success(monkeypatch, commit=head)

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
    assert result["reason"] == "pages_build_not_built"
    assert result["pages"]["status"] == "queued"
    assert seen == ["https://api.github.com/repos/HIDEPON-UMG/News-Grasp/pages/builds/latest"]


def test_verify_publish_rejects_pages_build_commit_mismatch(monkeypatch, tmp_path: Path) -> None:
    """Pages build が built でも対象 commit でなければ publish_complete にしない。"""
    _write_local_sw(tmp_path)
    head = "a" * 40

    def fake_git(_repo: Path, args: list[str]) -> str:
        if args == ["rev-parse", "HEAD"]:
            return head
        if args == ["ls-remote", "origin", "refs/heads/main"]:
            return f"{head}\trefs/heads/main"
        if args == ["config", "--get", "remote.origin.url"]:
            return "git@github.com:HIDEPON-UMG/News-Grasp.git"
        raise AssertionError(args)

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return json.dumps({"status": "built", "commit": "b" * 40}).encode("utf-8")

    monkeypatch.setattr(dsh, "_git_output", fake_git)
    monkeypatch.setattr(dsh.urllib.request, "urlopen", lambda *a, **k: FakeResponse())
    _mock_deploy_workflow_success(monkeypatch, commit=head)

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
    assert result["reason"] == "pages_build_commit_mismatch"
    assert result["pages"]["commit"] == "b" * 40


def test_verify_publish_rejects_stale_public_sw_version(monkeypatch, tmp_path: Path) -> None:
    """public sw.js の SW_VERSION がローカル期待版と違えば publish_complete にしない。"""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "sw.js").write_text("const SW_VERSION = 'expected-version';\n", encoding="utf-8")

    def fake_git(_repo: Path, args: list[str]) -> str:
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

    fetched: list[str] = []

    def fake_fetch_text(url: str) -> str:
        fetched.append(url)
        if url == "https://example.com/News-Grasp/sw.js":
            return "const SW_VERSION = 'stale-version';\n"
        raise AssertionError(url)

    monkeypatch.setattr(dsh, "_git_output", fake_git)
    monkeypatch.setattr(dsh.urllib.request, "urlopen", lambda *a, **k: FakeResponse())
    monkeypatch.setattr(dsh, "_fetch_text", fake_fetch_text)
    _mock_deploy_workflow_success(monkeypatch)
    _mock_pages_build_success(monkeypatch)

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
    assert result["reason"] == "sw_version_mismatch"
    assert result["pwa"]["local_sw_version"] == "expected-version"
    assert result["pwa"]["public_sw_version"] == "stale-version"
    assert fetched == ["https://example.com/News-Grasp/sw.js"]


def test_verify_publish_checks_public_audio_when_latest_audio_exists(monkeypatch, tmp_path: Path) -> None:
    """当日音声がある日は Release だけでなく Home/summary の audio URL 反映まで確認する。"""
    _write_local_sw(tmp_path)
    audio_url = "https://github.com/HIDEPON-UMG/News-Grasp/releases/download/audio-daily/2026-06-16.mp3?v=abc123"
    latest = tmp_path / "build" / "tts" / "latest_audio.json"
    latest.parent.mkdir(parents=True)
    latest.write_text(
        json.dumps({"latest_audio_date": "2026-06-16", "latest_audio_url": audio_url}),
        encoding="utf-8",
    )

    def fake_git(_repo: Path, args: list[str]) -> str:
        if args == ["rev-parse", "HEAD"]:
            return "abc123"
        if args == ["ls-remote", "origin", "refs/heads/main"]:
            return "abc123\trefs/heads/main"
        raise AssertionError(args)

    seen: list[tuple[str, str]] = []

    class FakeResponse:
        def __init__(self, body: str = "", status: int = 200):
            self._body = body
            self.status = status

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return self._body.encode("utf-8")

    def fake_urlopen(req, *args, **kwargs):
        url = getattr(req, "full_url", str(req))
        method = getattr(req, "get_method", lambda: "GET")()
        seen.append((method, url))
        if url.endswith("publish-status.json"):
            return FakeResponse(json.dumps({"result": "published_ok", "date": "2026-06-16"}))
        if url == "https://example.com/News-Grasp/sw.js":
            return FakeResponse("const SW_VERSION = 'expected-version';\n")
        if url == audio_url:
            return FakeResponse("", status=200)
        if url == "https://example.com/News-Grasp/":
            return FakeResponse(f'<audio preload="none" controls src="{audio_url}"></audio>')
        if url == "https://example.com/News-Grasp/2026-06-16/summary/":
            return FakeResponse(f'<audio preload="none" controls src="{audio_url}"></audio>')
        raise AssertionError(url)

    monkeypatch.setattr(dsh, "_git_output", fake_git)
    monkeypatch.setattr(dsh.urllib.request, "urlopen", fake_urlopen)
    _mock_deploy_workflow_success(monkeypatch)
    _mock_pages_build_success(monkeypatch)

    result = verify_publish(
        repo_root=tmp_path,
        date="2026-06-16",
        remote="origin",
        branch="main",
        public_base_url="https://example.com/News-Grasp/",
        wait_sec=0,
        poll_sec=1,
    )

    assert result["ok"] is True
    assert result["audio"]["ok"] is True
    assert ("HEAD", audio_url) in seen
    assert ("GET", "https://example.com/News-Grasp/") in seen
    assert ("GET", "https://example.com/News-Grasp/2026-06-16/summary/") in seen


def test_verify_podcast_accepts_public_video_and_playlist(monkeypatch, tmp_path: Path) -> None:
    """Podcast は state、watch/oEmbed、playlist の全反映で OK にする。"""
    state = tmp_path / "build" / "youtube-podcast" / "uploads.json"
    state.parent.mkdir(parents=True)
    state.write_text(
        json.dumps(
            {
                "2026-06-20": {
                    "status": "public",
                    "videoId": "video-1",
                    "playlistId": "playlist-1",
                    "playlistItemId": "item-1",
                    "mp4_sha256": "abc",
                }
            }
        ),
        encoding="utf-8",
    )

    class FakeResponse:
        status = 200

        def __init__(self, body: str):
            self._body = body

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return self._body.encode("utf-8")

    def fake_urlopen(req, *args, **kwargs):
        url = getattr(req, "full_url", str(req))
        if "oembed" in url:
            return FakeResponse(json.dumps({"title": "News-Grasp Daily News Briefing 2026-06-20"}))
        if "watch?v=video-1" in url:
            return FakeResponse("<html>News-Grasp Daily News Briefing 2026-06-20</html>")
        if "playlist?list=playlist-1" in url:
            return FakeResponse("<html>video-1</html>")
        raise AssertionError(url)

    monkeypatch.setattr(dsh.urllib.request, "urlopen", fake_urlopen)

    result = dsh.verify_podcast(date="2026-06-20", state_path=state, wait_sec=0, poll_sec=1)

    assert result["ok"] is True
    assert result["videoId"] == "video-1"
    assert result["playlistId"] == "playlist-1"


def test_verify_podcast_requires_primary_podcast_playlist_when_recorded(monkeypatch, tmp_path: Path) -> None:
    """DeepDive 対談の二重所属記録がある場合は News-Grasp Podcast 本体も確認する。"""
    state = tmp_path / "uploads.json"
    state.write_text(
        json.dumps(
            {
                "2026-06-21": {
                    "status": "public",
                    "videoId": "deepdive-video-1",
                    "playlistId": "playlist-deepdive",
                    "primaryPodcastPlaylistId": "playlist-primary",
                }
            }
        ),
        encoding="utf-8",
    )

    class FakeResponse:
        status = 200

        def __init__(self, body: str):
            self._body = body

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return self._body.encode("utf-8")

    def fake_urlopen(req, *args, **kwargs):
        url = getattr(req, "full_url", str(req))
        if "oembed" in url:
            return FakeResponse(json.dumps({"title": "News-Grasp DeepDive Dialogue 2026-06-21"}))
        if "watch?v=deepdive-video-1" in url:
            return FakeResponse("<html>News-Grasp DeepDive Dialogue 2026-06-21</html>")
        if "playlist?list=playlist-deepdive" in url:
            return FakeResponse("<html>deepdive-video-1</html>")
        if "playlist?list=playlist-primary" in url:
            return FakeResponse("<html>daily-video-only</html>")
        raise AssertionError(url)

    monkeypatch.setattr(dsh.urllib.request, "urlopen", fake_urlopen)

    result = dsh.verify_podcast(
        date="2026-06-21",
        state_path=state,
        wait_sec=0,
        poll_sec=1,
        expected_title="News-Grasp DeepDive Dialogue 2026-06-21",
    )

    assert result["ok"] is False
    assert result["reason"] == "primary_podcast_playlist_missing"
    assert result["playlistId"] == "playlist-primary"


def test_verify_podcast_falls_back_when_oembed_is_unauthorized(monkeypatch, tmp_path: Path) -> None:
    """oEmbed が 401 でも watch / playlist HTML で公開実体を確認できれば OK。"""
    state = tmp_path / "uploads.json"
    state.write_text(
        json.dumps({"2026-06-21": {"status": "public", "videoId": "video-1", "playlistId": "playlist-1"}}),
        encoding="utf-8",
    )

    class FakeResponse:
        status = 200

        def __init__(self, body: str):
            self._body = body

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return self._body.encode("utf-8")

    def fake_urlopen(req, *args, **kwargs):
        url = getattr(req, "full_url", str(req))
        if "oembed" in url:
            raise dsh.urllib.error.HTTPError(url, 401, "Unauthorized", hdrs=None, fp=None)
        if "watch?v=video-1" in url:
            return FakeResponse("<title>News-Grasp Daily News Briefing 2026-06-21 - YouTube</title>video-1")
        if "playlist?list=playlist-1" in url:
            return FakeResponse("<html>News-Grasp Daily News Briefing 2026-06-21 video-1</html>")
        raise AssertionError(url)

    monkeypatch.setattr(dsh.urllib.request, "urlopen", fake_urlopen)

    result = dsh.verify_podcast(date="2026-06-21", state_path=state, wait_sec=0, poll_sec=1)

    assert result["ok"] is True
    assert result["reason"] == ""
    assert result["videoId"] == "video-1"
    assert result["title"] == "News-Grasp Daily News Briefing 2026-06-21"
    assert result["verification"] == "watch_playlist_fallback"


def test_verify_podcast_rejects_title_mismatch(monkeypatch, tmp_path: Path) -> None:
    state = tmp_path / "uploads.json"
    state.write_text(
        json.dumps({"2026-06-20": {"status": "public", "videoId": "video-1", "playlistId": "playlist-1"}}),
        encoding="utf-8",
    )

    class FakeResponse:
        status = 200

        def __init__(self, body: str):
            self._body = body

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return self._body.encode("utf-8")

    monkeypatch.setattr(
        dsh.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: FakeResponse(json.dumps({"title": "News-Grasp Daily News Briefing 2026-06-19"})),
    )

    result = dsh.verify_podcast(date="2026-06-20", state_path=state, wait_sec=0, poll_sec=1)

    assert result["ok"] is False
    assert result["reason"] == "podcast_title_mismatch"


def test_verify_publish_can_require_podcast(monkeypatch, tmp_path: Path) -> None:
    """通常公開は Web/audio に加えて Podcast gate も要求できる。"""
    _write_local_sw(tmp_path)

    def fake_git(_repo: Path, args: list[str]) -> str:
        if args == ["rev-parse", "HEAD"]:
            return "abc123"
        if args == ["ls-remote", "origin", "refs/heads/main"]:
            return "abc123\trefs/heads/main"
        raise AssertionError(args)

    class FakeResponse:
        status = 200

        def __init__(self, body: str = ""):
            self._body = body

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return self._body.encode("utf-8")

    monkeypatch.setattr(dsh, "_git_output", fake_git)
    monkeypatch.setattr(
        dsh.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: FakeResponse(json.dumps({"result": "published_ok", "date": "2026-06-20"})),
    )
    monkeypatch.setattr(dsh, "_fetch_text", lambda _url: "const SW_VERSION = 'expected-version';\n")
    monkeypatch.setattr(dsh, "verify_public_audio", lambda **_kwargs: {"checked": False, "ok": True})
    monkeypatch.setattr(dsh, "verify_podcast", lambda **_kwargs: {"ok": False, "reason": "public_podcast_missing"})
    _mock_deploy_workflow_success(monkeypatch)
    _mock_pages_build_success(monkeypatch)

    result = verify_publish(
        repo_root=tmp_path,
        date="2026-06-20",
        remote="origin",
        branch="main",
        public_base_url="https://example.com/News-Grasp/",
        wait_sec=0,
        poll_sec=1,
        require_podcast=True,
        podcast_state_path=tmp_path / "uploads.json",
    )

    assert result["ok"] is False
    assert result["reason"] == "public_podcast_missing"


def test_verify_publish_does_not_require_podcast_by_default(monkeypatch, tmp_path: Path) -> None:
    """pre-finalize の publish gate は Web/audio までを確認し、Podcast は complete gate に残す。"""
    _write_local_sw(tmp_path)

    def fake_git(_repo: Path, args: list[str]) -> str:
        if args == ["rev-parse", "HEAD"]:
            return "abc123"
        if args == ["ls-remote", "origin", "refs/heads/main"]:
            return "abc123\trefs/heads/main"
        raise AssertionError(args)

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return json.dumps({"result": "published_ok", "date": "2026-06-20"}).encode("utf-8")

    monkeypatch.setattr(dsh, "_git_output", fake_git)
    monkeypatch.setattr(dsh.urllib.request, "urlopen", lambda *_args, **_kwargs: FakeResponse())
    monkeypatch.setattr(dsh, "_fetch_text", lambda _url: "const SW_VERSION = 'expected-version';\n")
    monkeypatch.setattr(dsh, "verify_public_audio", lambda **_kwargs: {"checked": False, "ok": True})
    monkeypatch.setattr(dsh, "verify_podcast", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("podcast must be skipped")))
    _mock_deploy_workflow_success(monkeypatch)
    _mock_pages_build_success(monkeypatch)

    result = verify_publish(
        repo_root=tmp_path,
        date="2026-06-20",
        remote="origin",
        branch="main",
        public_base_url="https://example.com/News-Grasp/",
        wait_sec=0,
        poll_sec=1,
    )

    assert result["ok"] is True
    assert result["podcast"]["reason"] == "podcast_not_required"


def test_verify_publish_waits_for_deploy_workflow_success(monkeypatch, tmp_path: Path) -> None:
    """push 直後の Deploy workflow in_progress は wait window 内で success まで待つ。"""
    _write_local_sw(tmp_path)
    deploy_calls: list[str] = []
    sleeps: list[int] = []

    def fake_git(_repo: Path, args: list[str]) -> str:
        if args == ["rev-parse", "HEAD"]:
            return "abc123"
        if args == ["ls-remote", "origin", "refs/heads/main"]:
            return "abc123\trefs/heads/main"
        raise AssertionError(args)

    def fake_deploy(**_kwargs):
        deploy_calls.append("call")
        if len(deploy_calls) == 1:
            return {
                "ok": False,
                "reason": "deploy_workflow_not_success",
                "status": "in_progress",
                "conclusion": "",
                "head_sha": "abc123",
            }
        return {
            "ok": True,
            "reason": "",
            "status": "completed",
            "conclusion": "success",
            "head_sha": "abc123",
        }

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return json.dumps({"result": "published_ok", "date": "2026-06-20"}).encode("utf-8")

    monkeypatch.setattr(dsh, "_git_output", fake_git)
    monkeypatch.setattr(dsh, "verify_deploy_workflow", fake_deploy)
    monkeypatch.setattr(dsh, "verify_pages_build", lambda **_kwargs: {"ok": True, "reason": "", "status": "built"})
    monkeypatch.setattr(dsh.urllib.request, "urlopen", lambda *_args, **_kwargs: FakeResponse())
    monkeypatch.setattr(dsh, "_fetch_text", lambda _url: "const SW_VERSION = 'expected-version';\n")
    monkeypatch.setattr(dsh, "verify_public_audio", lambda **_kwargs: {"checked": False, "ok": True})
    monkeypatch.setattr(dsh.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(dsh.time, "monotonic", lambda: 0.0)

    result = verify_publish(
        repo_root=tmp_path,
        date="2026-06-20",
        remote="origin",
        branch="main",
        public_base_url="https://example.com/News-Grasp/",
        wait_sec=30,
        poll_sec=7,
    )

    assert result["ok"] is True
    assert len(deploy_calls) == 2
    assert sleeps == [7]
    assert result["deploy_workflow"]["status"] == "completed"


def test_verify_publish_does_not_wait_on_deploy_commit_mismatch(monkeypatch, tmp_path: Path) -> None:
    """別 commit の Deploy workflow は待機で隠さず即 typed mismatch にする。"""
    _write_local_sw(tmp_path)
    deploy_calls: list[str] = []

    def fake_git(_repo: Path, args: list[str]) -> str:
        if args == ["rev-parse", "HEAD"]:
            return "abc123"
        if args == ["ls-remote", "origin", "refs/heads/main"]:
            return "abc123\trefs/heads/main"
        raise AssertionError(args)

    def fake_deploy(**_kwargs):
        deploy_calls.append("call")
        return {
            "ok": False,
            "reason": "deploy_workflow_commit_mismatch",
            "head_sha": "def456",
            "expected_commit": "abc123",
        }

    monkeypatch.setattr(dsh, "_git_output", fake_git)
    monkeypatch.setattr(dsh, "verify_deploy_workflow", fake_deploy)
    monkeypatch.setattr(dsh, "verify_pages_build", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("pages must not run")))
    monkeypatch.setattr(dsh.time, "sleep", lambda _seconds: (_ for _ in ()).throw(AssertionError("must not wait")))

    result = verify_publish(
        repo_root=tmp_path,
        date="2026-06-20",
        remote="origin",
        branch="main",
        public_base_url="https://example.com/News-Grasp/",
        wait_sec=30,
        poll_sec=7,
    )

    assert result["ok"] is False
    assert result["reason"] == "deploy_workflow_commit_mismatch"
    assert len(deploy_calls) == 1


def test_verify_publish_rejects_public_audio_url_missing_from_summary(monkeypatch, tmp_path: Path) -> None:
    """summary が旧音声URLのままなら publish 完了扱いにしない。"""
    _write_local_sw(tmp_path)
    audio_url = "https://github.com/HIDEPON-UMG/News-Grasp/releases/download/audio-daily/2026-06-16.mp3?v=newhash"
    latest = tmp_path / "build" / "tts" / "latest_audio.json"
    latest.parent.mkdir(parents=True)
    latest.write_text(
        json.dumps({"latest_audio_date": "2026-06-16", "latest_audio_url": audio_url}),
        encoding="utf-8",
    )

    def fake_git(_repo: Path, args: list[str]) -> str:
        if args == ["rev-parse", "HEAD"]:
            return "abc123"
        if args == ["ls-remote", "origin", "refs/heads/main"]:
            return "abc123\trefs/heads/main"
        raise AssertionError(args)

    class FakeResponse:
        status = 200

        def __init__(self, body: str = ""):
            self._body = body

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return self._body.encode("utf-8")

    def fake_urlopen(req, *args, **kwargs):
        url = getattr(req, "full_url", str(req))
        if url.endswith("publish-status.json"):
            return FakeResponse(json.dumps({"result": "published_ok", "date": "2026-06-16"}))
        if url == "https://example.com/News-Grasp/sw.js":
            return FakeResponse("const SW_VERSION = 'expected-version';\n")
        if url == audio_url:
            return FakeResponse("")
        if url == "https://example.com/News-Grasp/":
            return FakeResponse(f'<audio preload="none" controls src="{audio_url}"></audio>')
        if url == "https://example.com/News-Grasp/2026-06-16/summary/":
            return FakeResponse('<audio preload="none" controls src="old.mp3?v=oldhash"></audio>')
        raise AssertionError(url)

    monkeypatch.setattr(dsh, "_git_output", fake_git)
    monkeypatch.setattr(dsh.urllib.request, "urlopen", fake_urlopen)
    _mock_deploy_workflow_success(monkeypatch)
    _mock_pages_build_success(monkeypatch)

    result = verify_publish(
        repo_root=tmp_path,
        date="2026-06-16",
        remote="origin",
        branch="main",
        public_base_url="https://example.com/News-Grasp/",
        wait_sec=0,
        poll_sec=1,
    )

    assert result["ok"] is False
    assert result["reason"] == "public_audio_missing"
    assert result["audio"]["missing_from"] == ["summary"]


def test_verify_publish_rejects_public_status_mismatch(monkeypatch, tmp_path: Path) -> None:
    def fake_git(_repo: Path, args: list[str]) -> str:
        if args == ["rev-parse", "HEAD"]:
            return "abc123"
        if args == ["ls-remote", "origin", "refs/heads/main"]:
            return "abc123\trefs/heads/main"
        raise AssertionError(args)

    monkeypatch.setattr(dsh, "_git_output", fake_git)
    _mock_deploy_workflow_success(monkeypatch)
    _mock_pages_build_success(monkeypatch)

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


PUBLISH_COMMIT = "a" * 40


def _write_publish_complete_inventory(
    repo_root: Path,
    date: str = "2026-06-20",
    *,
    distribution_manifest: dict | str | None = None,
) -> None:
    _write_local_sw(repo_root)
    (repo_root / "build" / "tts").mkdir(parents=True, exist_ok=True)
    (repo_root / "build" / "tts" / "latest_audio.json").write_text(
        json.dumps(
            {
                "latest_audio_date": date,
                "latest_audio_url": f"https://example.com/audio/{date}.mp3",
            }
        ),
        encoding="utf-8",
    )
    (repo_root / "build" / "tts" / "deepdive").mkdir(parents=True, exist_ok=True)
    (repo_root / "build" / "tts" / "deepdive" / "latest_audio.json").write_text(
        json.dumps(
            {
                "latest_audio_date": date,
                "latest_audio_url": f"https://example.com/audio/{date}-deepdive.mp3",
            }
        ),
        encoding="utf-8",
    )
    primary = repo_root / "build" / "youtube-podcast"
    primary.mkdir(parents=True, exist_ok=True)
    (primary / f"{date}.mp4").write_bytes(b"primary")
    (primary / "uploads.json").write_text(
        json.dumps({date: {"status": "public", "videoId": "primary-video", "playlistId": "primary-list"}}),
        encoding="utf-8",
    )
    deepdive = repo_root / "build" / "youtube-podcast-deepdive"
    deepdive.mkdir(parents=True, exist_ok=True)
    (deepdive / f"{date}.mp4").write_bytes(b"deepdive")
    (deepdive / "uploads.json").write_text(
        json.dumps({date: {"status": "public", "videoId": "deepdive-video", "playlistId": "deepdive-list"}}),
        encoding="utf-8",
    )
    dist = repo_root / "data" / "distribution"
    dist.mkdir(parents=True, exist_ok=True)
    if distribution_manifest is None:
        distribution_manifest = {
            "date": date,
            "pre_publish_commit": PUBLISH_COMMIT,
            "publish_commit": "",
            "publish_commit_resolution": "post_push_verify",
            "same_publish_contract": "pre_publish_commit_must_equal_verified_publish_commit",
            "primary_podcast_state": "build/youtube-podcast/uploads.json",
            "deepdive_podcast_state": "build/youtube-podcast-deepdive/uploads.json",
            "latest_audio_state": "build/tts/latest_audio.json",
            "deepdive_audio_state": "build/tts/deepdive/latest_audio.json",
            "generated_at": "2026-06-20T00:00:00+09:00",
        }
    content = distribution_manifest if isinstance(distribution_manifest, str) else json.dumps(distribution_manifest)
    (dist / f"{date}.json").write_text(content, encoding="utf-8")


def test_verify_publish_complete_requires_distribution_inventory(monkeypatch, tmp_path: Path) -> None:
    """publish_complete は local distribution inventory が揃わない限り成立しない。"""
    _write_publish_complete_inventory(tmp_path)
    (tmp_path / "build" / "youtube-podcast-deepdive" / "uploads.json").unlink()
    monkeypatch.setattr(
        dsh,
        "verify_publish",
        lambda **_kwargs: {"ok": True, "local_head": PUBLISH_COMMIT, "remote_head": PUBLISH_COMMIT, "url": "status"},
    )

    result = dsh.verify_publish_complete(
        repo_root=tmp_path,
        date="2026-06-20",
        remote="origin",
        branch="main",
        public_base_url="https://example.com/News-Grasp/",
        wait_sec=0,
        poll_sec=1,
    )

    assert result["ok"] is False
    assert result["reason"] == "distribution_artifact_missing"
    assert "build/youtube-podcast-deepdive/uploads.json" in result["distribution_artifacts"]["missing"]


def test_verify_live_runner_readiness_requires_hash_task_and_canary(monkeypatch, tmp_path: Path) -> None:
    """live runner readiness は repo/live ops hash、watcher task target、実起動 canary をまとめて見る。"""
    repo_runner = tmp_path / "scripts" / "ops" / "news-grasp-runner.ps1"
    repo_watcher = tmp_path / "scripts" / "ops" / "watch-news-grasp-runner.ps1"
    repo_bootstrap = tmp_path / "scripts" / "ops" / "news-grasp-bootstrap.ps1"
    live_runner = tmp_path / "bin" / "news-grasp-runner.ps1"
    live_watcher = tmp_path / "bin" / "watch-news-grasp-runner.ps1"
    live_bootstrap = tmp_path / "bin" / "news-grasp-bootstrap.ps1"
    repo_runner.parent.mkdir(parents=True)
    live_runner.parent.mkdir(parents=True)
    runner_with_interlock = _runner_with_pre_run_interlock_source()
    repo_runner.write_text(runner_with_interlock, encoding="utf-8")
    repo_watcher.write_text("watcher", encoding="utf-8")
    repo_bootstrap.write_text("bootstrap", encoding="utf-8")
    live_runner.write_text(runner_with_interlock, encoding="utf-8")
    live_watcher.write_text("watcher", encoding="utf-8")
    live_bootstrap.write_text("bootstrap", encoding="utf-8")
    def fake_task_details(**kwargs):
        if kwargs.get("task_name") == "News-Grasp Bootstrap":
            return {
                "ok": True,
                "state": "Ready",
                "action_summary": (
                    f'powershell.exe -File "{live_bootstrap}" -Start -SmokeTest '
                    "-PollSeconds 1 -TimeoutMinutes 2 -StateFile ng-smoke-state.json -LogDir ng-smoke-logs"
                ),
                "triggers": [{"enabled": True, "start_boundary": "2026-06-20T05:55:00"}],
                "last_task_result": 0,
                "next_run_time": "2026-06-21T05:55:00",
                "number_of_missed_runs": 0,
            }
        return {
            "ok": True,
            "state": "Ready",
            "action_summary": f'powershell.exe -File "{live_bootstrap}" -Start',
            "triggers": [{"enabled": True, "start_boundary": "2026-06-20T06:00:00"}],
            "last_task_result": 0,
            "next_run_time": "2026-06-21T06:00:00",
            "number_of_missed_runs": 0,
        }

    monkeypatch.setattr(dsh, "_scheduled_task_details", fake_task_details)
    monkeypatch.setattr(dsh, "_run_live_startup_canary", lambda **_kwargs: {"ok": True, "status": "smoke_ok"})

    result = dsh.verify_live_runner_readiness(
        repo_root=tmp_path,
        live_runner_path=live_runner,
        live_watcher_path=live_watcher,
        live_bootstrap_path=live_bootstrap,
        date="2026-06-20",
        run_canary=True,
    )

    assert result["ok"] is True
    assert result["repo_runner"]["sha256"] == result["live_runner"]["sha256"]
    assert result["repo_watcher"]["sha256"] == result["live_watcher"]["sha256"]
    assert result["repo_bootstrap"]["sha256"] == result["live_bootstrap"]["sha256"]
    assert result["scheduled_task"]["targets_live_bootstrap"] is True
    assert result["scheduled_task"]["runner_action_is_production_start"] is True
    assert result["scheduled_task"]["direct_runner_pre_run_reexec"] is True
    assert result["canary"]["status"] == "smoke_ok"


def test_verify_live_runner_readiness_rejects_scheduler_target_drift(monkeypatch, tmp_path: Path) -> None:
    """Scheduled Task が watcher bootstrap 以外を指す日は next-run ready ではない。"""
    repo_runner = tmp_path / "scripts" / "ops" / "news-grasp-runner.ps1"
    repo_watcher = tmp_path / "scripts" / "ops" / "watch-news-grasp-runner.ps1"
    repo_bootstrap = tmp_path / "scripts" / "ops" / "news-grasp-bootstrap.ps1"
    live_runner = tmp_path / "bin" / "news-grasp-runner.ps1"
    live_watcher = tmp_path / "bin" / "watch-news-grasp-runner.ps1"
    live_bootstrap = tmp_path / "bin" / "news-grasp-bootstrap.ps1"
    repo_runner.parent.mkdir(parents=True)
    live_runner.parent.mkdir(parents=True)
    runner_with_interlock = _runner_with_pre_run_interlock_source()
    repo_runner.write_text(runner_with_interlock, encoding="utf-8")
    repo_watcher.write_text("watcher", encoding="utf-8")
    repo_bootstrap.write_text("bootstrap", encoding="utf-8")
    live_runner.write_text(runner_with_interlock, encoding="utf-8")
    live_watcher.write_text("watcher", encoding="utf-8")
    live_bootstrap.write_text("bootstrap", encoding="utf-8")
    monkeypatch.setattr(
        dsh,
        "_scheduled_task_details",
        lambda **_kwargs: {
            "ok": True,
            "state": "Ready",
            "action_summary": "powershell.exe -File C:\\old\\runner.ps1",
            "triggers": [{"enabled": True, "start_boundary": "2026-06-20T06:00:00"}],
            "last_task_result": 0,
            "next_run_time": "2026-06-21T06:00:00",
            "number_of_missed_runs": 0,
        },
    )
    monkeypatch.setattr(dsh, "_run_live_startup_canary", lambda **_kwargs: {"ok": True, "status": "smoke_ok"})

    result = dsh.verify_live_runner_readiness(
        repo_root=tmp_path,
        live_runner_path=live_runner,
        live_watcher_path=live_watcher,
        live_bootstrap_path=live_bootstrap,
        date="2026-06-20",
        run_canary=True,
    )

    assert result["ok"] is False
    assert result["reason"] == "scheduled_task_target_mismatch"


def test_verify_live_runner_readiness_rejects_runner_without_0600_next_run(monkeypatch, tmp_path: Path) -> None:
    """Runner task は target だけでなく 06:00 trigger と次回実行予定を必須にする。"""
    repo_runner = tmp_path / "scripts" / "ops" / "news-grasp-runner.ps1"
    repo_watcher = tmp_path / "scripts" / "ops" / "watch-news-grasp-runner.ps1"
    repo_bootstrap = tmp_path / "scripts" / "ops" / "news-grasp-bootstrap.ps1"
    live_runner = tmp_path / "bin" / "news-grasp-runner.ps1"
    live_watcher = tmp_path / "bin" / "watch-news-grasp-runner.ps1"
    live_bootstrap = tmp_path / "bin" / "news-grasp-bootstrap.ps1"
    repo_runner.parent.mkdir(parents=True)
    live_runner.parent.mkdir(parents=True)
    runner_with_interlock = _runner_with_pre_run_interlock_source()
    for path, content in (
        (repo_runner, runner_with_interlock),
        (live_runner, runner_with_interlock),
        (repo_watcher, "watcher"),
        (live_watcher, "watcher"),
        (repo_bootstrap, "bootstrap"),
        (live_bootstrap, "bootstrap"),
    ):
        path.write_text(content, encoding="utf-8")

    monkeypatch.setattr(
        dsh,
        "_scheduled_task_details",
        lambda **_kwargs: {
            "ok": True,
            "state": "Ready",
            "action_summary": f'powershell.exe -File "{live_bootstrap}" -Start',
            "triggers": [{"enabled": True, "start_boundary": "2026-06-20T06:05:00"}],
            "last_task_result": 0,
            "next_run_time": "",
            "number_of_missed_runs": 0,
        },
    )
    monkeypatch.setattr(dsh, "_run_live_startup_canary", lambda **_kwargs: {"ok": True, "status": "smoke_ok"})

    result = dsh.verify_live_runner_readiness(
        repo_root=tmp_path,
        live_runner_path=live_runner,
        live_watcher_path=live_watcher,
        live_bootstrap_path=live_bootstrap,
        date="2026-06-20",
    )

    assert result["ok"] is False
    assert result["reason"] == "scheduled_task_not_0600"


def test_verify_live_runner_readiness_rejects_nonproduction_runner_task_action(
    monkeypatch, tmp_path: Path
) -> None:
    """06:00 task が bootstrap/watcher を指していても smoke/status/start-only action は本番起動ではない。"""
    repo_runner = tmp_path / "scripts" / "ops" / "news-grasp-runner.ps1"
    repo_watcher = tmp_path / "scripts" / "ops" / "watch-news-grasp-runner.ps1"
    repo_bootstrap = tmp_path / "scripts" / "ops" / "news-grasp-bootstrap.ps1"
    live_runner = tmp_path / "bin" / "news-grasp-runner.ps1"
    live_watcher = tmp_path / "bin" / "watch-news-grasp-runner.ps1"
    live_bootstrap = tmp_path / "bin" / "news-grasp-bootstrap.ps1"
    repo_runner.parent.mkdir(parents=True)
    live_runner.parent.mkdir(parents=True)
    runner_with_interlock = _runner_with_pre_run_interlock_source()
    for path, content in (
        (repo_runner, runner_with_interlock),
        (live_runner, runner_with_interlock),
        (repo_watcher, "watcher"),
        (live_watcher, "watcher"),
        (repo_bootstrap, "bootstrap"),
        (live_bootstrap, "bootstrap"),
    ):
        path.write_text(content, encoding="utf-8")

    def fake_task_details(**kwargs):
        if kwargs.get("task_name") == "News-Grasp Bootstrap":
            return {
                "ok": True,
                "state": "Ready",
                "action_summary": (
                    f'powershell.exe -File "{live_bootstrap}" -Start -SmokeTest '
                    "-PollSeconds 1 -TimeoutMinutes 2 -StateFile ng-smoke-state.json -LogDir ng-smoke-logs"
                ),
                "triggers": [{"enabled": True, "start_boundary": "2026-06-20T05:55:00"}],
                "last_task_result": 0,
                "next_run_time": "2026-06-21T05:55:00",
                "number_of_missed_runs": 0,
            }
        return {
            "ok": True,
            "state": "Ready",
            "action_summary": f'powershell.exe -File "{live_bootstrap}" -Start -SmokeTest',
            "triggers": [{"enabled": True, "start_boundary": "2026-06-20T06:00:00"}],
            "last_task_result": 0,
            "next_run_time": "2026-06-21T06:00:00",
            "number_of_missed_runs": 0,
        }

    monkeypatch.setattr(dsh, "_scheduled_task_details", fake_task_details)
    monkeypatch.setattr(dsh, "_run_live_startup_canary", lambda **_kwargs: {"ok": True, "status": "smoke_ok"})

    result = dsh.verify_live_runner_readiness(
        repo_root=tmp_path,
        live_runner_path=live_runner,
        live_watcher_path=live_watcher,
        live_bootstrap_path=live_bootstrap,
        date="2026-06-20",
    )

    assert result["ok"] is False
    assert result["reason"] == "scheduled_task_action_not_production_start"


def test_verify_live_runner_readiness_rejects_thin_direct_interlock_marker(
    monkeypatch, tmp_path: Path
) -> None:
    """direct runner interlock は文字列だけでなく、呼び出し順序と bootstrap args contract まで見る。"""
    repo_runner = tmp_path / "scripts" / "ops" / "news-grasp-runner.ps1"
    repo_watcher = tmp_path / "scripts" / "ops" / "watch-news-grasp-runner.ps1"
    repo_bootstrap = tmp_path / "scripts" / "ops" / "news-grasp-bootstrap.ps1"
    live_runner = tmp_path / "bin" / "news-grasp-runner.ps1"
    live_watcher = tmp_path / "bin" / "watch-news-grasp-runner.ps1"
    live_bootstrap = tmp_path / "bin" / "news-grasp-bootstrap.ps1"
    repo_runner.parent.mkdir(parents=True)
    live_runner.parent.mkdir(parents=True)
    thin_interlock = "function Assert-PreRunBootstrapInterlock { 'ng-smoke-state.json'; 'ng-smoke-logs'; blocked_startup_self_repair_failed }"
    for path, content in (
        (repo_runner, thin_interlock),
        (live_runner, thin_interlock),
        (repo_watcher, "watcher"),
        (live_watcher, "watcher"),
        (repo_bootstrap, "bootstrap"),
        (live_bootstrap, "bootstrap"),
    ):
        path.write_text(content, encoding="utf-8")

    def fake_task_details(**kwargs):
        if kwargs.get("task_name") == "News-Grasp Bootstrap":
            return {
                "ok": True,
                "state": "Ready",
                "action_summary": (
                    f'powershell.exe -File "{live_bootstrap}" -Start -SmokeTest '
                    "-PollSeconds 1 -TimeoutMinutes 2 -StateFile ng-smoke-state.json -LogDir ng-smoke-logs"
                ),
                "triggers": [{"enabled": True, "start_boundary": "2026-06-20T05:55:00"}],
                "last_task_result": 0,
                "next_run_time": "2026-06-21T05:55:00",
                "number_of_missed_runs": 0,
            }
        return {
            "ok": True,
            "state": "Ready",
            "action_summary": f'powershell.exe -File "{live_runner}"',
            "triggers": [{"enabled": True, "start_boundary": "2026-06-20T06:00:00"}],
            "last_task_result": 72,
            "next_run_time": "2026-06-21T06:00:00",
            "number_of_missed_runs": 0,
        }

    monkeypatch.setattr(dsh, "_scheduled_task_details", fake_task_details)
    monkeypatch.setattr(dsh, "_run_live_startup_canary", lambda **_kwargs: {"ok": True, "status": "smoke_ok"})

    result = dsh.verify_live_runner_readiness(
        repo_root=tmp_path,
        live_runner_path=live_runner,
        live_watcher_path=live_watcher,
        live_bootstrap_path=live_bootstrap,
        date="2026-06-20",
    )

    assert result["ok"] is False
    assert result["reason"] == "direct_runner_pre_run_interlock_missing"


def test_verify_live_runner_readiness_rejects_direct_interlock_without_reexec(
    monkeypatch, tmp_path: Path
) -> None:
    """direct runner は marker/interlock だけでなく、drift repair 後の synced reexec まで必須にする。"""
    repo_runner = tmp_path / "scripts" / "ops" / "news-grasp-runner.ps1"
    repo_watcher = tmp_path / "scripts" / "ops" / "watch-news-grasp-runner.ps1"
    repo_bootstrap = tmp_path / "scripts" / "ops" / "news-grasp-bootstrap.ps1"
    live_runner = tmp_path / "bin" / "news-grasp-runner.ps1"
    live_watcher = tmp_path / "bin" / "watch-news-grasp-runner.ps1"
    live_bootstrap = tmp_path / "bin" / "news-grasp-bootstrap.ps1"
    repo_runner.parent.mkdir(parents=True)
    live_runner.parent.mkdir(parents=True)
    interlock_without_reexec = """
$BootstrapSmokeStateFile = 'ng-smoke-state.json'
$BootstrapSmokeLogDir = 'ng-smoke-logs'
$BootstrapSmokeEarliestMinutes = 5 * 60 + 55
$BootstrapSmokeFreshnessMinutes = 15
function Test-NormalDailyPublishRun { return $true }
function Test-PreRunBootstrapSmokeMarker {
    $state.updated_at | Out-Null
    $item.LastWriteTime | Out-Null
    $BootstrapSmokeEarliestMinutes | Out-Null
    $BootstrapSmokeFreshnessMinutes | Out-Null
    $now = Get-Date
    ($now - $state.updated_at).TotalMinutes | Out-Null
}
function Assert-PreRunBootstrapInterlock {
    $bootstrapArgs = @('-SmokeTest', '-PollSeconds', '1', '-TimeoutMinutes', '2', '-StateFile', $BootstrapSmokeStateFile, '-LogDir', $BootstrapSmokeLogDir)
    Start-Process -FilePath 'powershell' -ArgumentList $bootstrapArgs
    blocked_startup_self_repair_failed
}
function Convert-JsonStringArrayToStringList {}
function Assert-RunnerBinaryInSync {
    if (Test-NormalDailyPublishRun) {
        Assert-PreRunBootstrapInterlock -ForceRepair
    }
    Invoke-RunnerBinarySyncApprovalBlock
}
function Invoke-Logged {}
# ===== sentinel: 起動できた事実 =====
Assert-PreRunBootstrapInterlock
Assert-RunnerBinaryInSync
$IsE2EOrDryRun = $false
"""
    for path, content in (
        (repo_runner, interlock_without_reexec),
        (live_runner, interlock_without_reexec),
        (repo_watcher, "watcher"),
        (live_watcher, "watcher"),
        (repo_bootstrap, "bootstrap"),
        (live_bootstrap, "bootstrap"),
    ):
        path.write_text(content, encoding="utf-8")

    def fake_task_details(**kwargs):
        if kwargs.get("task_name") == "News-Grasp Bootstrap":
            return {
                "ok": True,
                "state": "Ready",
                "action_summary": (
                    f'powershell.exe -File "{live_bootstrap}" -Start -SmokeTest '
                    "-PollSeconds 1 -TimeoutMinutes 2 -StateFile ng-smoke-state.json -LogDir ng-smoke-logs"
                ),
                "triggers": [{"enabled": True, "start_boundary": "2026-06-20T05:55:00"}],
                "last_task_result": 0,
                "next_run_time": "2026-06-21T05:55:00",
                "number_of_missed_runs": 0,
            }
        return {
            "ok": True,
            "state": "Ready",
            "action_summary": f'powershell.exe -File "{live_runner}"',
            "triggers": [{"enabled": True, "start_boundary": "2026-06-20T06:00:00"}],
            "last_task_result": 72,
            "next_run_time": "2026-06-21T06:00:00",
            "number_of_missed_runs": 0,
        }

    monkeypatch.setattr(dsh, "_scheduled_task_details", fake_task_details)
    monkeypatch.setattr(dsh, "_run_live_startup_canary", lambda **_kwargs: {"ok": True, "status": "smoke_ok"})

    result = dsh.verify_live_runner_readiness(
        repo_root=tmp_path,
        live_runner_path=live_runner,
        live_watcher_path=live_watcher,
        live_bootstrap_path=live_bootstrap,
        date="2026-06-20",
    )

    assert result["ok"] is False
    assert result["reason"] == "direct_runner_pre_run_interlock_missing"


def test_verify_live_runner_readiness_accepts_bootstrap_before_direct_runner(monkeypatch, tmp_path: Path) -> None:
    """既存 runner task を変更できない環境でも、事前 bootstrap が watcher を指せば self-heal ready とする。"""
    repo_runner = tmp_path / "scripts" / "ops" / "news-grasp-runner.ps1"
    repo_watcher = tmp_path / "scripts" / "ops" / "watch-news-grasp-runner.ps1"
    repo_bootstrap = tmp_path / "scripts" / "ops" / "news-grasp-bootstrap.ps1"
    live_runner = tmp_path / "bin" / "news-grasp-runner.ps1"
    live_watcher = tmp_path / "bin" / "watch-news-grasp-runner.ps1"
    live_bootstrap = tmp_path / "bin" / "news-grasp-bootstrap.ps1"
    repo_runner.parent.mkdir(parents=True)
    live_runner.parent.mkdir(parents=True)
    runner_with_interlock = _runner_with_pre_run_interlock_source()
    repo_runner.write_text(runner_with_interlock, encoding="utf-8")
    repo_watcher.write_text("watcher", encoding="utf-8")
    repo_bootstrap.write_text("bootstrap", encoding="utf-8")
    live_runner.write_text(runner_with_interlock, encoding="utf-8")
    live_watcher.write_text("watcher", encoding="utf-8")
    live_bootstrap.write_text("bootstrap", encoding="utf-8")

    def fake_task_details(**kwargs):
        if kwargs.get("task_name") == "News-Grasp Bootstrap":
            return {
                "ok": True,
                "state": "Ready",
                "action_summary": (
                    f'powershell.exe -File "{live_bootstrap}" -Start -SmokeTest '
                    "-PollSeconds 1 -TimeoutMinutes 2 -StateFile ng-smoke-state.json -LogDir ng-smoke-logs"
                ),
                "triggers": [{"enabled": True, "start_boundary": "2026-06-20T05:55:00"}],
                "last_task_result": 0,
                "next_run_time": "2026-06-21T05:55:00",
                "last_run_time": "2026-06-20T05:55:00",
                "number_of_missed_runs": 0,
            }
        return {
            "ok": True,
            "state": "Ready",
            "action_summary": f'powershell.exe -File "{live_runner}"',
            "triggers": [{"enabled": True, "start_boundary": "2026-06-20T06:00:00"}],
            "last_task_result": 72,
            "next_run_time": "2026-06-21T06:00:00",
            "number_of_missed_runs": 0,
        }

    monkeypatch.setattr(dsh, "_scheduled_task_details", fake_task_details)
    monkeypatch.setattr(dsh, "_run_live_startup_canary", lambda **_kwargs: {"ok": True, "status": "smoke_ok"})

    result = dsh.verify_live_runner_readiness(
        repo_root=tmp_path,
        live_runner_path=live_runner,
        live_watcher_path=live_watcher,
        live_bootstrap_path=live_bootstrap,
        date="2026-06-20",
        run_canary=True,
    )

    assert result["ok"] is True
    assert result["scheduled_task"]["targets_live_runner"] is True
    assert result["scheduled_task"]["direct_runner_pre_run_interlock"] is True
    assert result["scheduled_task"]["direct_runner_pre_run_reexec"] is True
    assert result["scheduled_task"]["bootstrap_repairs_before_run"] is True
    assert result["status"] == "ready_with_failed_last_schedule"
    assert result["next_run_readiness"]["ok"] is True
    assert result["last_scheduled_attempt"]["status"] == "failed"
    assert result["last_scheduled_attempt"]["last_task_result"] == 72


def test_verify_live_runner_readiness_rejects_bootstrap_last_result_failure(monkeypatch, tmp_path: Path) -> None:
    """bootstrap task が未成功なら、Action が正しくても next-run self-heal ready ではない。"""
    repo_runner = tmp_path / "scripts" / "ops" / "news-grasp-runner.ps1"
    repo_watcher = tmp_path / "scripts" / "ops" / "watch-news-grasp-runner.ps1"
    repo_bootstrap = tmp_path / "scripts" / "ops" / "news-grasp-bootstrap.ps1"
    live_runner = tmp_path / "bin" / "news-grasp-runner.ps1"
    live_watcher = tmp_path / "bin" / "watch-news-grasp-runner.ps1"
    live_bootstrap = tmp_path / "bin" / "news-grasp-bootstrap.ps1"
    repo_runner.parent.mkdir(parents=True)
    live_runner.parent.mkdir(parents=True)
    runner_with_interlock = _runner_with_pre_run_interlock_source()
    for path, content in (
        (repo_runner, runner_with_interlock),
        (live_runner, runner_with_interlock),
        (repo_watcher, "watcher"),
        (live_watcher, "watcher"),
        (repo_bootstrap, "bootstrap"),
        (live_bootstrap, "bootstrap"),
    ):
        path.write_text(content, encoding="utf-8")

    def fake_task_details(**kwargs):
        if kwargs.get("task_name") == "News-Grasp Bootstrap":
            return {
                "ok": True,
                "state": "Ready",
                "action_summary": (
                    f'powershell.exe -File "{live_bootstrap}" -Start -SmokeTest '
                    "-PollSeconds 1 -TimeoutMinutes 2 -StateFile ng-smoke-state.json -LogDir ng-smoke-logs"
                ),
                "triggers": [{"enabled": True, "start_boundary": "2026-06-20T05:55:00"}],
                "last_task_result": 267011,
                "next_run_time": "2026-06-21T05:55:00",
                "number_of_missed_runs": 0,
            }
        return {
            "ok": True,
            "state": "Ready",
            "action_summary": f'powershell.exe -File "{live_runner}"',
            "triggers": [{"enabled": True, "start_boundary": "2026-06-20T06:00:00"}],
            "last_task_result": 72,
            "next_run_time": "2026-06-21T06:00:00",
            "number_of_missed_runs": 0,
        }

    monkeypatch.setattr(dsh, "_scheduled_task_details", fake_task_details)
    monkeypatch.setattr(dsh, "_run_live_startup_canary", lambda **_kwargs: {"ok": True, "status": "smoke_ok"})

    result = dsh.verify_live_runner_readiness(
        repo_root=tmp_path,
        live_runner_path=live_runner,
        live_watcher_path=live_watcher,
        live_bootstrap_path=live_bootstrap,
        date="2026-06-20",
    )

    assert result["ok"] is False
    assert result["reason"] == "bootstrap_task_last_result_not_ok"


def test_verify_live_runner_readiness_rejects_bootstrap_after_runner(monkeypatch, tmp_path: Path) -> None:
    """bootstrap task が 06:00 runner の後なら、初手自己修復の証明にはならない。"""
    repo_runner = tmp_path / "scripts" / "ops" / "news-grasp-runner.ps1"
    repo_watcher = tmp_path / "scripts" / "ops" / "watch-news-grasp-runner.ps1"
    repo_bootstrap = tmp_path / "scripts" / "ops" / "news-grasp-bootstrap.ps1"
    live_runner = tmp_path / "bin" / "news-grasp-runner.ps1"
    live_watcher = tmp_path / "bin" / "watch-news-grasp-runner.ps1"
    live_bootstrap = tmp_path / "bin" / "news-grasp-bootstrap.ps1"
    repo_runner.parent.mkdir(parents=True)
    live_runner.parent.mkdir(parents=True)
    runner_with_interlock = _runner_with_pre_run_interlock_source()
    for path, content in (
        (repo_runner, runner_with_interlock),
        (live_runner, runner_with_interlock),
        (repo_watcher, "watcher"),
        (live_watcher, "watcher"),
        (repo_bootstrap, "bootstrap"),
        (live_bootstrap, "bootstrap"),
    ):
        path.write_text(content, encoding="utf-8")

    def fake_task_details(**kwargs):
        if kwargs.get("task_name") == "News-Grasp Bootstrap":
            return {
                "ok": True,
                "state": "Ready",
                "action_summary": (
                    f'powershell.exe -File "{live_bootstrap}" -Start -SmokeTest '
                    "-PollSeconds 1 -TimeoutMinutes 2 -StateFile ng-smoke-state.json -LogDir ng-smoke-logs"
                ),
                "triggers": [{"enabled": True, "start_boundary": "2026-06-20T06:05:00"}],
                "last_task_result": 0,
                "next_run_time": "2026-06-21T06:05:00",
                "number_of_missed_runs": 0,
            }
        return {
            "ok": True,
            "state": "Ready",
            "action_summary": f'powershell.exe -File "{live_runner}"',
            "triggers": [{"enabled": True, "start_boundary": "2026-06-20T06:00:00"}],
            "last_task_result": 72,
            "next_run_time": "2026-06-21T06:00:00",
            "number_of_missed_runs": 0,
        }

    monkeypatch.setattr(dsh, "_scheduled_task_details", fake_task_details)
    monkeypatch.setattr(dsh, "_run_live_startup_canary", lambda **_kwargs: {"ok": True, "status": "smoke_ok"})

    result = dsh.verify_live_runner_readiness(
        repo_root=tmp_path,
        live_runner_path=live_runner,
        live_watcher_path=live_watcher,
        live_bootstrap_path=live_bootstrap,
        date="2026-06-20",
    )

    assert result["ok"] is False
    assert result["reason"] == "bootstrap_task_not_0555"


def test_verify_live_runner_readiness_rejects_bootstrap_missed_or_unscheduled(monkeypatch, tmp_path: Path) -> None:
    """事前 bootstrap は missed run なし、次回 05:55 予約ありでなければ self-heal 証明にならない。"""
    repo_runner = tmp_path / "scripts" / "ops" / "news-grasp-runner.ps1"
    repo_watcher = tmp_path / "scripts" / "ops" / "watch-news-grasp-runner.ps1"
    repo_bootstrap = tmp_path / "scripts" / "ops" / "news-grasp-bootstrap.ps1"
    live_runner = tmp_path / "bin" / "news-grasp-runner.ps1"
    live_watcher = tmp_path / "bin" / "watch-news-grasp-runner.ps1"
    live_bootstrap = tmp_path / "bin" / "news-grasp-bootstrap.ps1"
    repo_runner.parent.mkdir(parents=True)
    live_runner.parent.mkdir(parents=True)
    runner_with_interlock = _runner_with_pre_run_interlock_source()
    for path, content in (
        (repo_runner, runner_with_interlock),
        (live_runner, runner_with_interlock),
        (repo_watcher, "watcher"),
        (live_watcher, "watcher"),
        (repo_bootstrap, "bootstrap"),
        (live_bootstrap, "bootstrap"),
    ):
        path.write_text(content, encoding="utf-8")

    def fake_task_details(**kwargs):
        if kwargs.get("task_name") == "News-Grasp Bootstrap":
            return {
                "ok": True,
                "state": "Ready",
                "action_summary": (
                    f'powershell.exe -File "{live_bootstrap}" -Start -SmokeTest '
                    "-PollSeconds 1 -TimeoutMinutes 2 -StateFile ng-smoke-state.json -LogDir ng-smoke-logs"
                ),
                "triggers": [{"enabled": True, "start_boundary": "2026-06-20T05:55:00"}],
                "last_task_result": 0,
                "next_run_time": "",
                "number_of_missed_runs": 1,
            }
        return {
            "ok": True,
            "state": "Ready",
            "action_summary": f'powershell.exe -File "{live_runner}"',
            "triggers": [{"enabled": True, "start_boundary": "2026-06-20T06:00:00"}],
            "last_task_result": 72,
            "next_run_time": "2026-06-21T06:00:00",
            "number_of_missed_runs": 0,
        }

    monkeypatch.setattr(dsh, "_scheduled_task_details", fake_task_details)
    monkeypatch.setattr(dsh, "_run_live_startup_canary", lambda **_kwargs: {"ok": True, "status": "smoke_ok"})

    result = dsh.verify_live_runner_readiness(
        repo_root=tmp_path,
        live_runner_path=live_runner,
        live_watcher_path=live_watcher,
        live_bootstrap_path=live_bootstrap,
        date="2026-06-20",
    )

    assert result["ok"] is False
    assert result["reason"] == "bootstrap_task_next_run_missing"


def test_verify_live_runner_readiness_rejects_bootstrap_without_isolated_smoke_action(
    monkeypatch, tmp_path: Path
) -> None:
    """Bootstrap task は -SmokeTest、短い timeout、隔離 state/log を Action に明示する。"""
    repo_runner = tmp_path / "scripts" / "ops" / "news-grasp-runner.ps1"
    repo_watcher = tmp_path / "scripts" / "ops" / "watch-news-grasp-runner.ps1"
    repo_bootstrap = tmp_path / "scripts" / "ops" / "news-grasp-bootstrap.ps1"
    live_runner = tmp_path / "bin" / "news-grasp-runner.ps1"
    live_watcher = tmp_path / "bin" / "watch-news-grasp-runner.ps1"
    live_bootstrap = tmp_path / "bin" / "news-grasp-bootstrap.ps1"
    repo_runner.parent.mkdir(parents=True)
    live_runner.parent.mkdir(parents=True)
    runner_with_interlock = _runner_with_pre_run_interlock_source()
    for path, content in (
        (repo_runner, runner_with_interlock),
        (live_runner, runner_with_interlock),
        (repo_watcher, "watcher"),
        (live_watcher, "watcher"),
        (repo_bootstrap, "bootstrap"),
        (live_bootstrap, "bootstrap"),
    ):
        path.write_text(content, encoding="utf-8")

    def fake_task_details(**kwargs):
        if kwargs.get("task_name") == "News-Grasp Bootstrap":
            return {
                "ok": True,
                "state": "Ready",
                "action_summary": f'powershell.exe -File "{live_bootstrap}" -Start',
                "triggers": [{"enabled": True, "start_boundary": "2026-06-20T05:55:00"}],
                "last_task_result": 0,
                "next_run_time": "2026-06-21T05:55:00",
                "number_of_missed_runs": 0,
            }
        return {
            "ok": True,
            "state": "Ready",
            "action_summary": f'powershell.exe -File "{live_runner}"',
            "triggers": [{"enabled": True, "start_boundary": "2026-06-20T06:00:00"}],
            "last_task_result": 72,
            "next_run_time": "2026-06-21T06:00:00",
            "number_of_missed_runs": 0,
        }

    monkeypatch.setattr(dsh, "_scheduled_task_details", fake_task_details)
    monkeypatch.setattr(dsh, "_run_live_startup_canary", lambda **_kwargs: {"ok": True, "status": "smoke_ok"})

    result = dsh.verify_live_runner_readiness(
        repo_root=tmp_path,
        live_runner_path=live_runner,
        live_watcher_path=live_watcher,
        live_bootstrap_path=live_bootstrap,
        date="2026-06-20",
    )

    assert result["ok"] is False
    assert result["reason"] == "bootstrap_task_smoke_contract_invalid"


def test_live_runner_canary_rejects_command_not_found_stderr(monkeypatch, tmp_path: Path) -> None:
    """canary は exit 0 でも PowerShell の致命 stderr を Green にしない。"""
    live_runner = tmp_path / "bin" / "news-grasp-runner.ps1"
    live_runner.parent.mkdir(parents=True)
    live_runner.write_text("runner", encoding="utf-8")

    class Proc:
        returncode = 0
        stdout = "news-grasp-runner.ps1 SMOKE OK\n"
        stderr = "Get-FileHash : The term 'Get-FileHash' is not recognized\nCommandNotFoundException\n"

    def fake_run(command, **_kwargs):
        state_file = Path(command[command.index("-StateFile") + 1])
        log_dir = Path(command[command.index("-LogDir") + 1])
        state_file.parent.mkdir(parents=True, exist_ok=True)
        log_dir.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps({"status": "smoke_ok"}), encoding="utf-8")
        (log_dir / "2026-06-20.log").write_text("news-grasp-runner.ps1 SMOKE OK\n", encoding="utf-8")
        return Proc()

    monkeypatch.setattr(dsh.subprocess, "run", fake_run)

    result = dsh._run_live_startup_canary(
        repo_root=tmp_path,
        startup_path=live_runner,
        date="2026-06-20",
    )

    assert result["ok"] is False
    assert result["reason"] == "canary_stderr_error"


def test_live_startup_canary_removes_stale_log_before_run(monkeypatch, tmp_path: Path) -> None:
    """canary は同一日付の古い log に汚染されず、今回の state/log だけで判定する。"""
    startup = tmp_path / "bin" / "news-grasp-bootstrap.ps1"
    startup.parent.mkdir(parents=True)
    startup.write_text("bootstrap", encoding="utf-8")
    stale_log = tmp_path / "build" / "live-runner-canary" / "2026-06-20" / "logs" / "2026-06-20.log"
    stale_log.parent.mkdir(parents=True, exist_ok=True)
    stale_log.write_text("old log without smoke\n", encoding="utf-8")

    class Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, **_kwargs):
        state_file = Path(command[command.index("-StateFile") + 1])
        log_dir = Path(command[command.index("-LogDir") + 1])
        assert not (log_dir / "2026-06-20.log").exists()
        state_file.parent.mkdir(parents=True, exist_ok=True)
        log_dir.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps({"status": "smoke_ok"}), encoding="utf-8")
        (log_dir / "2026-06-20.log").write_text("news-grasp-runner.ps1 SMOKE OK\n", encoding="utf-8")
        return Proc()

    monkeypatch.setattr(dsh.subprocess, "run", fake_run)

    result = dsh._run_live_startup_canary(
        repo_root=tmp_path,
        startup_path=startup,
        date="2026-06-20",
    )

    assert result["ok"] is True


def test_verify_publish_complete_requires_live_runner_readiness(monkeypatch, tmp_path: Path) -> None:
    """public/distribution が揃っても live readiness が無ければ daily 完了にしない。"""
    _write_publish_complete_inventory(tmp_path)
    monkeypatch.setattr(
        dsh,
        "verify_publish",
        lambda **_kwargs: {
            "ok": True,
            "local_head": PUBLISH_COMMIT,
            "remote_head": PUBLISH_COMMIT,
            "url": "https://example.com/News-Grasp/publish-status.json",
            "pwa": {"ok": True},
            "audio": {"ok": True},
            "podcast": {"ok": True, "videoId": "primary-video"},
        },
    )
    monkeypatch.setattr(
        dsh,
        "verify_podcast",
        lambda **_kwargs: {"ok": True, "videoId": "deepdive-video", "title": "News-Grasp DeepDive Dialogue 2026-06-20"},
    )
    monkeypatch.setattr(
        dsh,
        "verify_live_runner_readiness",
        lambda **_kwargs: {"ok": False, "reason": "live_runner_hash_mismatch"},
    )

    result = dsh.verify_publish_complete(
        repo_root=tmp_path,
        date="2026-06-20",
        remote="origin",
        branch="main",
        public_base_url="https://example.com/News-Grasp/",
        wait_sec=0,
        poll_sec=1,
    )

    assert result["ok"] is False
    assert result["reason"] == "live_runner_hash_mismatch"
    assert result["live_runner_readiness"]["ok"] is False


def test_verify_publish_complete_rejects_invalid_distribution_manifest(monkeypatch, tmp_path: Path) -> None:
    """distribution manifest は存在だけでなく JSON/schema を満たす必要がある。"""
    _write_publish_complete_inventory(tmp_path, distribution_manifest="{not-json")
    monkeypatch.setattr(
        dsh,
        "verify_publish",
        lambda **_kwargs: {"ok": True, "local_head": PUBLISH_COMMIT, "remote_head": PUBLISH_COMMIT, "url": "status"},
    )

    result = dsh.verify_publish_complete(
        repo_root=tmp_path,
        date="2026-06-20",
        remote="origin",
        branch="main",
        public_base_url="https://example.com/News-Grasp/",
        wait_sec=0,
        poll_sec=1,
    )

    assert result["ok"] is False
    assert result["reason"] == "distribution_manifest_invalid"


def test_verify_publish_complete_rejects_distribution_date_mismatch(monkeypatch, tmp_path: Path) -> None:
    """distribution manifest の日付が対象日と違う場合は same-publish にしない。"""
    _write_publish_complete_inventory(
        tmp_path,
        distribution_manifest={
            "date": "2026-06-19",
            "pre_publish_commit": PUBLISH_COMMIT,
            "primary_podcast_state": "build/youtube-podcast/uploads.json",
            "deepdive_podcast_state": "build/youtube-podcast-deepdive/uploads.json",
            "latest_audio_state": "build/tts/latest_audio.json",
            "deepdive_audio_state": "build/tts/deepdive/latest_audio.json",
            "generated_at": "2026-06-20T00:00:00+09:00",
        },
    )
    monkeypatch.setattr(
        dsh,
        "verify_publish",
        lambda **_kwargs: {"ok": True, "local_head": PUBLISH_COMMIT, "remote_head": PUBLISH_COMMIT, "url": "status"},
    )

    result = dsh.verify_publish_complete(
        repo_root=tmp_path,
        date="2026-06-20",
        remote="origin",
        branch="main",
        public_base_url="https://example.com/News-Grasp/",
        wait_sec=0,
        poll_sec=1,
    )

    assert result["ok"] is False
    assert result["reason"] == "distribution_manifest_mismatch"


def test_verify_publish_complete_requires_distribution_commit_anchor(monkeypatch, tmp_path: Path) -> None:
    """date だけの distribution manifest は同一 publish 証明として不十分。"""
    _write_publish_complete_inventory(
        tmp_path,
        distribution_manifest={
            "date": "2026-06-20",
            "primary_podcast_state": "build/youtube-podcast/uploads.json",
            "deepdive_podcast_state": "build/youtube-podcast-deepdive/uploads.json",
            "latest_audio_state": "build/tts/latest_audio.json",
            "deepdive_audio_state": "build/tts/deepdive/latest_audio.json",
            "generated_at": "2026-06-20T00:00:00+09:00",
        },
    )
    monkeypatch.setattr(
        dsh,
        "verify_publish",
        lambda **_kwargs: {"ok": True, "local_head": PUBLISH_COMMIT, "remote_head": PUBLISH_COMMIT, "url": "status"},
    )

    result = dsh.verify_publish_complete(
        repo_root=tmp_path,
        date="2026-06-20",
        remote="origin",
        branch="main",
        public_base_url="https://example.com/News-Grasp/",
        wait_sec=0,
        poll_sec=1,
    )

    assert result["ok"] is False
    assert result["reason"] == "distribution_manifest_commit_missing"


def test_verify_publish_complete_rejects_empty_publish_commit_without_resolution(monkeypatch, tmp_path: Path) -> None:
    """pre-push manifest の空 publish_commit は post-push same-publish 契約なしでは受理しない。"""
    _write_publish_complete_inventory(
        tmp_path,
        distribution_manifest={
            "date": "2026-06-20",
            "pre_publish_commit": PUBLISH_COMMIT,
            "publish_commit": "",
            "primary_podcast_state": "build/youtube-podcast/uploads.json",
            "deepdive_podcast_state": "build/youtube-podcast-deepdive/uploads.json",
            "latest_audio_state": "build/tts/latest_audio.json",
            "deepdive_audio_state": "build/tts/deepdive/latest_audio.json",
            "generated_at": "2026-06-20T00:00:00+09:00",
        },
    )

    result = dsh.verify_publish_complete(
        repo_root=tmp_path,
        date="2026-06-20",
        remote="origin",
        branch="main",
        public_base_url="https://example.com/News-Grasp/",
        wait_sec=0,
        poll_sec=1,
    )

    assert result["ok"] is False
    assert result["reason"] == "distribution_manifest_publish_commit_resolution_missing"


def test_verify_publish_complete_rejects_distribution_commit_mismatch(monkeypatch, tmp_path: Path) -> None:
    """distribution manifest の commit anchor が公開検証 commit と違えば完了にしない。"""
    _write_publish_complete_inventory(
        tmp_path,
        distribution_manifest={
            "date": "2026-06-20",
            "pre_publish_commit": "b" * 40,
            "publish_commit": "",
            "publish_commit_resolution": "post_push_verify",
            "same_publish_contract": "pre_publish_commit_must_equal_verified_publish_commit",
            "primary_podcast_state": "build/youtube-podcast/uploads.json",
            "deepdive_podcast_state": "build/youtube-podcast-deepdive/uploads.json",
            "latest_audio_state": "build/tts/latest_audio.json",
            "deepdive_audio_state": "build/tts/deepdive/latest_audio.json",
            "generated_at": "2026-06-20T00:00:00+09:00",
        },
    )
    monkeypatch.setattr(
        dsh,
        "verify_publish",
        lambda **_kwargs: {"ok": True, "local_head": PUBLISH_COMMIT, "remote_head": PUBLISH_COMMIT, "url": "status"},
    )

    result = dsh.verify_publish_complete(
        repo_root=tmp_path,
        date="2026-06-20",
        remote="origin",
        branch="main",
        public_base_url="https://example.com/News-Grasp/",
        wait_sec=0,
        poll_sec=1,
    )

    assert result["ok"] is False
    assert result["reason"] == "distribution_manifest_commit_mismatch"


def test_verify_publish_complete_rejects_manifest_missing_from_head_tree(monkeypatch, tmp_path: Path) -> None:
    """local に手書き manifest があっても、publish HEAD の tree に無ければ完了にしない。"""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=tmp_path, check=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True, encoding="utf-8").strip()

    _write_publish_complete_inventory(
        tmp_path,
        distribution_manifest={
            "date": "2026-06-20",
            "pre_publish_commit": head,
            "publish_commit": "",
            "publish_commit_resolution": "post_push_verify",
            "same_publish_contract": "pre_publish_commit_must_equal_verified_publish_commit",
            "primary_podcast_state": "build/youtube-podcast/uploads.json",
            "deepdive_podcast_state": "build/youtube-podcast-deepdive/uploads.json",
            "latest_audio_state": "build/tts/latest_audio.json",
            "deepdive_audio_state": "build/tts/deepdive/latest_audio.json",
            "generated_at": "2026-06-20T00:00:00+09:00",
        },
    )
    monkeypatch.setattr(
        dsh,
        "verify_publish",
        lambda **_kwargs: {"ok": True, "local_head": head, "remote_head": head, "url": "status"},
    )

    result = dsh.verify_publish_complete(
        repo_root=tmp_path,
        date="2026-06-20",
        remote="origin",
        branch="main",
        public_base_url="https://example.com/News-Grasp/",
        wait_sec=0,
        poll_sec=1,
    )

    assert result["ok"] is False
    assert result["reason"] == "distribution_manifest_remote_missing"


def test_verify_publish_complete_rejects_optional_publish_commit_conflict(monkeypatch, tmp_path: Path) -> None:
    """optional publish_commit が入っている場合も公開検証 commit と矛盾できない。"""
    _write_publish_complete_inventory(
        tmp_path,
        distribution_manifest={
            "date": "2026-06-20",
            "pre_publish_commit": PUBLISH_COMMIT,
            "publish_commit": "b" * 40,
            "primary_podcast_state": "build/youtube-podcast/uploads.json",
            "deepdive_podcast_state": "build/youtube-podcast-deepdive/uploads.json",
            "latest_audio_state": "build/tts/latest_audio.json",
            "deepdive_audio_state": "build/tts/deepdive/latest_audio.json",
            "generated_at": "2026-06-20T00:00:00+09:00",
        },
    )
    monkeypatch.setattr(
        dsh,
        "verify_publish",
        lambda **_kwargs: {"ok": True, "local_head": PUBLISH_COMMIT, "remote_head": PUBLISH_COMMIT, "url": "status"},
    )

    result = dsh.verify_publish_complete(
        repo_root=tmp_path,
        date="2026-06-20",
        remote="origin",
        branch="main",
        public_base_url="https://example.com/News-Grasp/",
        wait_sec=0,
        poll_sec=1,
    )

    assert result["ok"] is False
    assert result["reason"] == "distribution_manifest_commit_mismatch"


def test_verify_publish_complete_requires_deepdive_podcast(monkeypatch, tmp_path: Path) -> None:
    """DeepDive podcast が public 化されない限り publish_complete にしない。"""
    _write_publish_complete_inventory(tmp_path)
    monkeypatch.setattr(
        dsh,
        "verify_publish",
        lambda **_kwargs: {
            "ok": True,
            "local_head": PUBLISH_COMMIT,
            "remote_head": PUBLISH_COMMIT,
            "url": "https://example.com/News-Grasp/publish-status.json",
            "pwa": {"ok": True},
            "audio": {"ok": True},
            "podcast": {"ok": True, "videoId": "primary-video"},
        },
    )
    monkeypatch.setattr(dsh, "verify_podcast", lambda **_kwargs: {"ok": False, "reason": "public_podcast_missing"})

    result = dsh.verify_publish_complete(
        repo_root=tmp_path,
        date="2026-06-20",
        remote="origin",
        branch="main",
        public_base_url="https://example.com/News-Grasp/",
        wait_sec=0,
        poll_sec=1,
    )

    assert result["ok"] is False
    assert result["reason"] == "deepdive_podcast_missing"
    assert result["podcasts"]["deepdive"]["reason"] == "public_podcast_missing"


def test_verify_publish_complete_requires_notification_state_when_requested(
    monkeypatch, tmp_path: Path
) -> None:
    """runner が notification state を指定した場合、欠落を completion proof にしない。"""
    _write_publish_complete_inventory(tmp_path)
    monkeypatch.setattr(
        dsh,
        "verify_publish",
        lambda **_kwargs: {
            "ok": True,
            "local_head": PUBLISH_COMMIT,
            "remote_head": PUBLISH_COMMIT,
            "url": "https://example.com/News-Grasp/publish-status.json",
            "pwa": {"ok": True},
            "audio": {"ok": True},
            "podcast": {"ok": True, "videoId": "primary-video"},
        },
    )
    monkeypatch.setattr(
        dsh,
        "verify_podcast",
        lambda **_kwargs: {"ok": True, "videoId": "deepdive-video", "title": "News-Grasp DeepDive Dialogue 2026-06-20"},
    )

    result = dsh.verify_publish_complete(
        repo_root=tmp_path,
        date="2026-06-20",
        remote="origin",
        branch="main",
        public_base_url="https://example.com/News-Grasp/",
        wait_sec=0,
        poll_sec=1,
        notification_state_path=tmp_path / "build" / "notification" / "2026-06-20.json",
    )

    assert result["ok"] is False
    assert result["reason"] == "notification_state_missing"


def test_verify_publish_complete_records_notification_state(monkeypatch, tmp_path: Path) -> None:
    """notification 結果を publish-complete manifest に含める。"""
    _write_publish_complete_inventory(tmp_path)
    notification = tmp_path / "build" / "notification" / "2026-06-20.json"
    notification.parent.mkdir(parents=True)
    notification.write_text(
        json.dumps(
            {
                "status": "no_subscribers",
                "ok": True,
                "date": "2026-06-20",
                "subscription_count": 0,
                "sent_count": 0,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        dsh,
        "verify_publish",
        lambda **_kwargs: {
            "ok": True,
            "local_head": PUBLISH_COMMIT,
            "remote_head": PUBLISH_COMMIT,
            "url": "https://example.com/News-Grasp/publish-status.json",
            "pwa": {"ok": True},
            "audio": {"ok": True},
            "podcast": {"ok": True, "videoId": "primary-video"},
        },
    )
    monkeypatch.setattr(
        dsh,
        "verify_podcast",
        lambda **_kwargs: {"ok": True, "videoId": "deepdive-video", "title": "News-Grasp DeepDive Dialogue 2026-06-20"},
    )
    monkeypatch.setattr(dsh, "verify_live_runner_readiness", lambda **_kwargs: _live_runner_readiness_ok())

    result = dsh.verify_publish_complete(
        repo_root=tmp_path,
        date="2026-06-20",
        remote="origin",
        branch="main",
        public_base_url="https://example.com/News-Grasp/",
        wait_sec=0,
        poll_sec=1,
        notification_state_path=notification,
    )

    assert result["ok"] is True
    assert result["notification"]["status"] == "no_subscribers"
    assert result["live_runner_readiness"]["ok"] is True
    assert result["public_status"] == "green"
    assert result["scheduled_attempt_status"] == "failed_then_recovered"
    assert result["recovery_attempt_status"] == "succeeded"


def test_verify_publish_complete_cli_outputs_manifest(monkeypatch, tmp_path: Path, capsys) -> None:
    """CLI は既定 stdout、明示 `--output` のみ local manifest を書く。"""
    _write_publish_complete_inventory(tmp_path)
    output = tmp_path / "build" / "publish-complete" / "2026-06-20.json"
    monkeypatch.setattr(
        dsh,
        "verify_publish",
        lambda **_kwargs: {
            "ok": True,
            "local_head": PUBLISH_COMMIT,
            "remote_head": PUBLISH_COMMIT,
            "url": "https://example.com/News-Grasp/publish-status.json",
            "pwa": {"ok": True, "local_sw_version": "expected-version", "public_sw_version": "expected-version"},
            "audio": {"ok": True, "latest_audio_url": "https://example.com/audio/2026-06-20.mp3"},
            "podcast": {"ok": True, "videoId": "primary-video"},
        },
    )
    monkeypatch.setattr(
        dsh,
        "verify_podcast",
        lambda **_kwargs: {"ok": True, "videoId": "deepdive-video", "title": "News-Grasp DeepDive Dialogue 2026-06-20"},
    )
    monkeypatch.setattr(dsh, "verify_live_runner_readiness", lambda **_kwargs: _live_runner_readiness_ok())

    rc = dsh.main(
        [
            "verify-publish-complete",
            "--repo-root",
            str(tmp_path),
            "--date",
            "2026-06-20",
            "--public-base-url",
            "https://example.com/News-Grasp/",
            "--wait-sec",
            "0",
            "--poll-sec",
            "1",
            "--output",
            str(output),
        ]
    )

    captured = capsys.readouterr()
    stdout_manifest = json.loads(captured.out)
    file_manifest = json.loads(output.read_text(encoding="utf-8"))
    assert rc == 0
    assert stdout_manifest["ok"] is True
    assert stdout_manifest["publish_commit"] == PUBLISH_COMMIT
    assert stdout_manifest["same_publish"]["distribution_pre_publish_commit"] == PUBLISH_COMMIT
    assert stdout_manifest == file_manifest
