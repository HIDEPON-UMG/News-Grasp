#!/usr/bin/env python3
"""tools.daily_self_heal の契約テスト。"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import tools.daily_self_heal as dsh
import tools.deepdive_quality as ddq
from tools.daily_self_heal import (
    classify_phase0,
    compare_files,
    emit_alert,
    evaluate_deadman,
    normalize_failure_signature,
    verify_publish,
)


def test_local_windows_principal_accepts_scheduler_short_name_only_for_local_domain(
    monkeypatch,
) -> None:
    monkeypatch.setenv("COMPUTERNAME", "HIDEKI-AI-PRO")
    assert dsh._same_local_windows_principal("hideki", r"HIDEKI-AI-PRO\hideki")
    assert dsh._same_local_windows_principal(r"HIDEKI-AI-PRO\hideki", "hideki")
    assert not dsh._same_local_windows_principal("hideki", r"FOREIGN\hideki")
    assert not dsh._same_local_windows_principal("other", r"HIDEKI-AI-PRO\hideki")


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


def _commit_fixture(repo_root: Path, message: str, *relative_paths: str) -> str:
    for relative_path in relative_paths:
        subprocess.run(
            ["git", "add", "--", relative_path],
            cwd=repo_root,
            check=True,
            capture_output=True,
        )
    subprocess.run(
        ["git", "commit", "-q", "-m", message],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        text=True,
        encoding="utf-8",
    ).strip()


def _init_deploy_history(repo_root: Path) -> tuple[str, str]:
    subprocess.run(["git", "init", "-q"], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "News-Grasp Test"],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )
    _write_local_sw(repo_root)
    _write_deploy_workflow(repo_root)
    deploy_head = _commit_fixture(
        repo_root,
        "deploy fixture",
        "docs/sw.js",
        ".github/workflows/deploy-pages.yml",
    )
    tool = repo_root / "tools" / "control.py"
    tool.parent.mkdir(parents=True, exist_ok=True)
    tool.write_text("CONTROL = True\n", encoding="utf-8")
    source_head = _commit_fixture(repo_root, "control-only fixture", "tools/control.py")
    return deploy_head, source_head


def _init_issue_scoped_deploy_history(
    repo_root: Path, *, mutate_current_issue: bool, mutate_category_landing: bool = False
) -> tuple[str, str]:
    subprocess.run(["git", "init", "-q"], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "News-Grasp Test"],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )
    _write_local_sw(repo_root, "artifact-version")
    _write_deploy_workflow(repo_root)
    issue_paths = dsh.required_published_docs_artifacts("2026-06-15")
    issue_paths.append("docs/deepdive/2026-06-15/index.html")
    issue_paths.extend(
        f"docs/{dsh.CATEGORY_PATHS[cat_id]['docs_segment']}/index.html"
        for cat_id in dsh.scheduled_category_ids("2026-06-15")
    )
    for relative_path in issue_paths:
        issue_file = repo_root / relative_path
        issue_file.parent.mkdir(parents=True, exist_ok=True)
        issue_file.write_text(f"current issue: {relative_path}\n", encoding="utf-8")
    (repo_root / "docs" / "index.html").write_text("current home\n", encoding="utf-8")
    (repo_root / "docs" / "publish-status.json").write_text(
        '{"result":"published_ok","date":"2026-06-15"}\n',
        encoding="utf-8",
    )
    artifact_head = _commit_fixture(
        repo_root,
        "issue artifact fixture",
        "docs/sw.js",
        ".github/workflows/deploy-pages.yml",
        *issue_paths,
        "docs/index.html",
        "docs/publish-status.json",
    )
    _write_local_sw(repo_root, "remote-version")
    if mutate_category_landing:
        changed_path = "docs/fx/index.html"
        (repo_root / changed_path).write_text(
            "mutated current issue category landing\n", encoding="utf-8"
        )
    elif mutate_current_issue:
        changed_path = "docs/fx/2026-06-15/index.html"
        (repo_root / changed_path).write_text(
            "mutated current issue category\n", encoding="utf-8"
        )
    else:
        historical = repo_root / "docs" / "2026-06-14" / "summary" / "index.html"
        historical.parent.mkdir(parents=True)
        historical.write_text("historical correction\n", encoding="utf-8")
        changed_path = "docs/2026-06-14/summary/index.html"
    remote_head = _commit_fixture(
        repo_root,
        "later public correction",
        "docs/sw.js",
        changed_path,
    )
    subprocess.run(
        ["git", "switch", "--detach", artifact_head],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )
    return artifact_head, remote_head


def _verify_issue_scoped_publish_fixture(
    monkeypatch,
    repo_root: Path,
    *,
    mutate_current_issue: bool,
    mutate_category_landing: bool = False,
) -> tuple[dict, str, str]:
    artifact_head, remote_head = _init_issue_scoped_deploy_history(
        repo_root,
        mutate_current_issue=mutate_current_issue,
        mutate_category_landing=mutate_category_landing,
    )
    real_git_output = dsh._git_output

    def fake_git(root: Path, args: list[str]) -> str:
        if args == ["ls-remote", "origin", "refs/heads/main"]:
            return f"{remote_head}\trefs/heads/main"
        return real_git_output(root, args)

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return json.dumps(
                {"result": "published_ok", "date": "2026-06-15"}
            ).encode("utf-8")

    monkeypatch.setattr(dsh, "_git_output", fake_git)
    monkeypatch.setattr(
        dsh,
        "verify_deploy_workflow",
        lambda **_kwargs: {
            "ok": True,
            "reason": "",
            "status": "completed",
            "conclusion": "success",
            "head_sha": remote_head,
        },
    )
    monkeypatch.setattr(
        dsh,
        "verify_pages_build",
        lambda **kwargs: {
            "ok": True,
            "reason": "",
            "status": "built",
            "commit": kwargs["expected_commit"],
        },
    )
    monkeypatch.setattr(
        dsh.urllib.request, "urlopen", lambda *_args, **_kwargs: FakeResponse()
    )
    monkeypatch.setattr(
        dsh, "_fetch_text", lambda _url: "const SW_VERSION = 'remote-version';\n"
    )
    monkeypatch.setattr(
        dsh, "verify_public_audio", lambda **_kwargs: {"checked": False, "ok": True}
    )
    result = verify_publish(
        repo_root=repo_root,
        date="2026-06-15",
        remote="origin",
        branch="main",
        public_base_url="https://example.com/News-Grasp/",
        wait_sec=0,
        poll_sec=1,
    )
    return result, artifact_head, remote_head


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


def _canonical_live_runner_readiness_manifest() -> dict:
    action = {
        "entryModule": "tools.news_grasp_cleanroom_dispatch",
        "argv": [
            "dispatch",
            "--schedule-id",
            "news-grasp-daily-v1",
            "--intent",
            "reconcile",
        ],
        "workingDirectoryToken": "<RUNTIME_ROOT>",
    }
    triggers = [
        {
            "triggerId": "scheduled-0600",
            "kind": "daily",
            "localTime": "06:00:00",
            "timeZone": "Asia/Tokyo",
        },
    ]
    authority = {
        "schemaVersion": "STABLE_TASK_AUTHORITY_V1",
        "taskName": "News-Grasp Production",
        "taskPath": "\\",
        "multipleInstancesPolicy": "IgnoreNew",
        "action": deepcopy(action),
        "triggers": deepcopy(triggers),
        "workingDirectoryToken": "<RUNTIME_ROOT>",
    }
    authority["authoritySha256"] = hashlib.sha256(
        json.dumps(authority, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    digest = "b" * 64
    return {
        "ok": True,
        "reason": "",
        "repo_runner": {"exists": True, "sha256": digest},
        "live_runner": {"exists": True, "sha256": digest},
        "repo_watcher": {"exists": True, "sha256": digest},
        "live_watcher": {"exists": True, "sha256": digest},
        "repo_bootstrap": {"exists": True, "sha256": digest},
        "live_bootstrap": {"exists": True, "sha256": digest},
        "repo_task_launcher": {"exists": True, "sha256": digest},
        "live_task_launcher": {"exists": True, "sha256": digest},
        "scheduled_task": {
            "ok": True,
            "taskName": "News-Grasp Production",
            "taskPath": "\\",
            "multipleInstancesPolicy": "IgnoreNew",
            "action": deepcopy(action),
            "triggers": deepcopy(triggers),
            "stableAuthority": deepcopy(authority),
            "authority": deepcopy(authority),
        },
        "stable_authority": deepcopy(authority),
        "external_control": {
            "schemaVersion": "EXTERNAL_CONTROL_PLANE_READINESS_V1",
            "status": "ready",
            "reasonCode": "",
            "modelLaunchCount": 0,
            "receiptSha256": "c" * 64,
        },
        "next_run_readiness": {"ok": True, "status": "ready"},
        "last_scheduled_attempt": {
            "status": "failed",
            "last_task_result": 72,
            "last_run_time": "2026-06-20T06:00:00",
        },
        "canary": {"ok": True, "status": "smoke_ok", "returncode": 0},
    }


def _live_runner_readiness_ok() -> dict:
    return _canonical_live_runner_readiness_manifest()


def test_live_runner_readiness_manifest_ok_accepts_canonical_dispatch_and_rejects_legacy_runner() -> None:
    """readiness consumer は canonical Production dispatch だけを Green にする。"""
    consumer = getattr(dsh, "live_runner_readiness_manifest_ok", None)
    assert callable(consumer), "live readiness consumer is missing"

    canonical = _canonical_live_runner_readiness_manifest()
    assert consumer(canonical) is True

    legacy = deepcopy(canonical)
    legacy_task = legacy["scheduled_task"]
    legacy_task["taskName"] = "News-Grasp Runner"
    legacy_task["multipleInstancesPolicy"] = "IgnoreNew"
    legacy_task["action"] = {
        "execute": r"C:\Python312\pythonw.exe",
        "arguments": "news-grasp-task-launcher.pyw runner",
    }
    legacy_task["triggers"] = [
        {
            "triggerId": "scheduled-0600",
            "kind": "daily",
            "localTime": "06:00:00",
            "timeZone": "Asia/Tokyo",
        }
    ]
    legacy_authority = {
        "schemaVersion": "STABLE_TASK_AUTHORITY_V1",
        "taskName": "News-Grasp Runner",
        "taskPath": "\\",
        "multipleInstancesPolicy": "IgnoreNew",
        "action": deepcopy(legacy_task["action"]),
        "triggers": deepcopy(legacy_task["triggers"]),
    }
    legacy_task["stableAuthority"] = deepcopy(legacy_authority)
    legacy_task["authority"] = deepcopy(legacy_authority)
    legacy["stable_authority"] = deepcopy(legacy_authority)
    assert consumer(legacy) is False

    control_drift = deepcopy(canonical)
    control_drift["external_control"] = {
        "schemaVersion": "EXTERNAL_CONTROL_PLANE_READINESS_V1",
        "status": "unavailable",
        "reasonCode": "installed_source_drift",
        "modelLaunchCount": 0,
    }
    assert consumer(control_drift) is False


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


def _deadman_topology_fixture(tmp_path: Path) -> tuple[dict[str, object], Path, Path]:
    """DeadmanのTaskPath/Action/Principal/triggerを全項目持つ純粋fixture。"""
    pythonw = tmp_path / "Python312" / "pythonw.exe"
    launcher = tmp_path / "bin" / "news-grasp-deadman-launcher.pyw"
    pythonw.parent.mkdir(parents=True)
    launcher.parent.mkdir(parents=True)
    pythonw.write_bytes(b"pythonw fixture")
    launcher.write_text("# deadman launcher fixture\n", encoding="utf-8")
    current_user = r"CONTOSO\hidek"
    row = {
        "task_name": "News-Grasp Deadman",
        "task_path": "\\",
        "enabled": True,
        "state": "Ready",
        "multiple_instances_policy": "IgnoreNew",
        "execution_time_limit": "PT1H45M",
        "principal_user_id": current_user,
        "principal_logon_type": "Interactive",
        "principal_run_level": "Limited",
        "actions": [
            {
                "execute": str(pythonw),
                "arguments": subprocess.list2cmdline([str(launcher)]),
                "workingDirectory": str(launcher.parent),
            }
        ],
        "triggers": [
            {
                "enabled": True,
                "trigger_type": "MSFT_TaskDailyTrigger",
                "days_interval": 1,
                "start_boundary": "2026-08-22T06:40:00",
                "repetition_interval": "PT1H",
                "repetition_duration": "P1D",
                "stop_at_duration_end": False,
            }
        ],
    }
    return {"current_user_id": current_user, "task_topology": [row]}, pythonw, launcher


def test_deadman_topology_requires_exact_path_action_and_principal(tmp_path: Path) -> None:
    """Deadmanはcanonical pythonw、単一launcher argv、TaskPath/Principalを同時に検査する。"""
    details, pythonw, launcher = _deadman_topology_fixture(tmp_path)
    contract = dsh._deadman_topology_contract(
        details,
        live_deadman_launcher_path=launcher,
        expected_pythonw_path=pythonw,
    )
    assert contract == {"ok": True, "reason": ""}

    invalid_cases: list[tuple[str, object]] = []
    wrong_path = deepcopy(details)
    wrong_path["task_topology"][0]["task_path"] = "\\News-Grasp"
    invalid_cases.append(("task_path", wrong_path))
    wrong_executable = deepcopy(details)
    wrong_executable["task_topology"][0]["actions"][0]["execute"] = "pythonw.exe"
    invalid_cases.append(("relative_pythonw", wrong_executable))
    extra_argv = deepcopy(details)
    extra_argv["task_topology"][0]["actions"][0]["arguments"] = subprocess.list2cmdline(
        [str(launcher), "--unexpected"]
    )
    invalid_cases.append(("extra_launcher_argv", extra_argv))
    wrong_user = deepcopy(details)
    wrong_user["task_topology"][0]["principal_user_id"] = r"CONTOSO\other"
    invalid_cases.append(("principal_user", wrong_user))
    wrong_logon = deepcopy(details)
    wrong_logon["task_topology"][0]["principal_logon_type"] = "Password"
    invalid_cases.append(("principal_logon", wrong_logon))
    wrong_level = deepcopy(details)
    wrong_level["task_topology"][0]["principal_run_level"] = "Highest"
    invalid_cases.append(("principal_level", wrong_level))
    wrong_repetition = deepcopy(details)
    wrong_repetition["task_topology"][0]["triggers"][0]["repetition_interval"] = "PT2H"
    invalid_cases.append(("repetition_interval", wrong_repetition))
    unknown_enabled = deepcopy(details)
    unknown_enabled["task_topology"][0]["enabled"] = None
    invalid_cases.append(("unknown_enabled", unknown_enabled))

    for _label, candidate in invalid_cases:
        result = dsh._deadman_topology_contract(
            candidate,
            live_deadman_launcher_path=launcher,
            expected_pythonw_path=pythonw,
        )
        assert result["ok"] is False
        assert result["reason"] == "deadman_task_definition_invalid"


@pytest.mark.parametrize("enabled", [True, None, "unknown"])
def test_disabled_legacy_tasks_require_absence_or_explicit_disabled(
    enabled: object,
) -> None:
    """Pull/legacy Runnerのenabled/unknown観測はRed、disabledまたは不在だけを許可する。"""
    for task_name in ("News-Grasp Pull", "News-Grasp Runner"):
        result = dsh._disabled_legacy_topology_contract(
            {
                "task_topology": [
                    {
                        "task_name": task_name,
                        "task_path": "\\",
                        "enabled": enabled,
                    }
                ]
            }
        )
        assert result["ok"] is False
        assert result["reason"] == "legacy_task_remains_enabled"


def test_disabled_legacy_tasks_accept_absent_or_disabled_root_tasks() -> None:
    assert dsh._disabled_legacy_topology_contract({"task_topology": []}) == {
        "ok": True,
        "reason": "",
    }
    assert dsh._disabled_legacy_topology_contract(
        {
            "task_topology": [
                {"task_name": "News-Grasp Pull", "task_path": "\\", "enabled": False},
                {"task_name": "News-Grasp Runner", "task_path": "\\", "enabled": False},
            ]
        }
    ) == {"ok": True, "reason": ""}


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
    assert calls == [
        ["rev-parse", "HEAD"],
        ["ls-remote", "origin", "refs/heads/main"],
        ["ls-remote", "origin", "refs/heads/main"],
    ]


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


def test_resolve_deploy_head_uses_latest_deploy_relevant_ancestor(tmp_path: Path) -> None:
    """tools-only HEAD は直近の docs deploy commit を公開正本として解決する。"""
    deploy_head, source_head = _init_deploy_history(tmp_path)

    result = dsh.resolve_deploy_head(repo_root=tmp_path, source_head=source_head)

    assert result == {
        "ok": True,
        "reason": "",
        "source_head": source_head,
        "deploy_head": deploy_head,
        "resolution": "latest_deploy_relevant_ancestor",
        "deploy_relevant_paths": ["docs", ".github/workflows/deploy-pages.yml"],
    }


def test_resolve_deploy_head_keeps_head_when_head_changes_docs(tmp_path: Path) -> None:
    """現在 HEAD が docs を変更した場合は過去の成功 workflow へ逃がさない。"""
    _deploy_head, _source_head = _init_deploy_history(tmp_path)
    sw = tmp_path / "docs" / "sw.js"
    sw.write_text("const SW_VERSION = 'next-version';\n", encoding="utf-8")
    current_head = _commit_fixture(tmp_path, "current deploy fixture", "docs/sw.js")

    result = dsh.resolve_deploy_head(repo_root=tmp_path, source_head=current_head)

    assert result["ok"] is True
    assert result["source_head"] == current_head
    assert result["deploy_head"] == current_head
    assert result["resolution"] == "source_head_is_deploy_relevant"


def test_deploy_workflow_accepts_successful_ancestor_push_covering_same_docs(
    monkeypatch, tmp_path: Path
) -> None:
    """docs commit後のpush tipで成功したworkflowを、後続code-only HEADから検証できる。"""
    deploy_head, workflow_head = _init_deploy_history(tmp_path)
    later = tmp_path / "tools" / "later.py"
    later.write_text("LATER = True\n", encoding="utf-8")
    source_head = _commit_fixture(tmp_path, "later control fixture", "tools/later.py")
    subprocess.run(
        ["git", "config", "remote.origin.url", "https://github.com/HIDEPON-UMG/News-Grasp.git"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    monkeypatch.setattr(
        dsh,
        "_github_api_json",
        lambda _url: {
            "workflow_runs": [
                {
                    "id": 123,
                    "head_sha": workflow_head,
                    "status": "completed",
                    "conclusion": "success",
                    "event": "push",
                    "html_url": "https://example.invalid/run/123",
                }
            ]
        },
    )

    result = dsh.verify_deploy_workflow_covering_deploy_head(
        repo_root=tmp_path,
        remote="origin",
        branch="main",
        source_head=source_head,
        deploy_relevant_head=deploy_head,
    )

    assert result["ok"] is True
    assert result["head_sha"] == workflow_head
    assert result["covered_deploy_head"] == deploy_head


def test_deploy_workflow_rejects_ancestor_run_before_later_docs_change(
    monkeypatch, tmp_path: Path
) -> None:
    """祖先workflow成功後にdocsが変わった場合、過去deployへ逃がさない。"""
    _old_deploy_head, workflow_head = _init_deploy_history(tmp_path)
    sw = tmp_path / "docs" / "sw.js"
    sw.write_text("const SW_VERSION = 'later-docs';\n", encoding="utf-8")
    source_head = _commit_fixture(tmp_path, "later docs fixture", "docs/sw.js")
    deploy_relevant_head = dsh.resolve_deploy_head(
        repo_root=tmp_path, source_head=source_head
    )["deploy_head"]
    subprocess.run(
        ["git", "config", "remote.origin.url", "https://github.com/HIDEPON-UMG/News-Grasp.git"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    monkeypatch.setattr(
        dsh,
        "_github_api_json",
        lambda _url: {
            "workflow_runs": [
                {
                    "id": 123,
                    "head_sha": "f" * 40,
                    "status": "completed",
                    "conclusion": "success",
                },
                {
                    "id": 124,
                    "head_sha": workflow_head,
                    "status": "completed",
                    "conclusion": "success",
                },
            ]
        },
    )

    result = dsh.verify_deploy_workflow_covering_deploy_head(
        repo_root=tmp_path,
        remote="origin",
        branch="main",
        source_head=source_head,
        deploy_relevant_head=deploy_relevant_head,
    )

    assert result["ok"] is False
    assert result["reason"] == "deploy_workflow_not_success"


def test_verify_publish_prefers_successful_push_head_over_deploy_ancestor(monkeypatch, tmp_path: Path) -> None:
    """同一 push の最終 HEAD で Deploy Pages が成功した場合は途中 docs commit を待たない。"""
    deploy_head, source_head = _init_deploy_history(tmp_path)
    real_git_output = dsh._git_output
    seen: dict[str, str] = {}

    def fake_git(repo_root: Path, args: list[str]) -> str:
        if args == ["ls-remote", "origin", "refs/heads/main"]:
            return f"{source_head}\trefs/heads/main"
        return real_git_output(repo_root, args)

    def fake_deploy(**kwargs):
        seen["workflow"] = kwargs["expected_commit"]
        return {
            "ok": True,
            "reason": "",
            "status": "completed",
            "conclusion": "success",
            "head_sha": kwargs["expected_commit"],
        }

    def fake_pages(**kwargs):
        seen["pages"] = kwargs["expected_commit"]
        return {"ok": True, "reason": "", "status": "built", "commit": kwargs["expected_commit"]}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return json.dumps({"result": "published_ok", "date": "2026-06-15"}).encode("utf-8")

    monkeypatch.setattr(dsh, "_git_output", fake_git)
    monkeypatch.setattr(dsh, "verify_deploy_workflow", fake_deploy)
    monkeypatch.setattr(dsh, "verify_pages_build", fake_pages)
    monkeypatch.setattr(dsh.urllib.request, "urlopen", lambda *_args, **_kwargs: FakeResponse())
    monkeypatch.setattr(dsh, "_fetch_text", lambda _url: "const SW_VERSION = 'expected-version';\n")
    monkeypatch.setattr(dsh, "verify_public_audio", lambda **_kwargs: {"checked": False, "ok": True})

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
    assert result["local_head"] == source_head
    assert result["remote_head"] == source_head
    assert result["deploy_head"] == source_head
    assert result["deploy_relevant_head"] == deploy_head
    assert seen == {"workflow": source_head, "pages": source_head}


def test_verify_publish_accepts_clean_artifact_head_before_control_only_descendant(
    monkeypatch, tmp_path: Path
) -> None:
    """公開artifact commit後のcontrol-only更新は、公開済み成果をRedへ戻さない。"""
    deploy_head, source_head = _init_deploy_history(tmp_path)
    subprocess.run(
        ["git", "switch", "--detach", deploy_head],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    real_git_output = dsh._git_output

    def fake_git(repo_root: Path, args: list[str]) -> str:
        if args == ["ls-remote", "origin", "refs/heads/main"]:
            return f"{source_head}\trefs/heads/main"
        return real_git_output(repo_root, args)

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return json.dumps(
                {"result": "published_ok", "date": "2026-06-15"}
            ).encode("utf-8")

    monkeypatch.setattr(dsh, "_git_output", fake_git)
    monkeypatch.setattr(
        dsh,
        "verify_deploy_workflow",
        lambda **_kwargs: {
            "ok": False,
            "reason": "deploy_workflow_not_success",
            "status": "",
        },
    )
    monkeypatch.setattr(
        dsh,
        "wait_for_deploy_workflow_covering_deploy_head",
        lambda **_kwargs: {
            "ok": True,
            "reason": "",
            "status": "completed",
            "conclusion": "success",
            "head_sha": deploy_head,
        },
    )
    monkeypatch.setattr(
        dsh,
        "verify_pages_build",
        lambda **kwargs: {
            "ok": True,
            "reason": "",
            "status": "built",
            "commit": kwargs["expected_commit"],
        },
    )
    monkeypatch.setattr(
        dsh.urllib.request, "urlopen", lambda *_args, **_kwargs: FakeResponse()
    )
    monkeypatch.setattr(
        dsh, "_fetch_text", lambda _url: "const SW_VERSION = 'expected-version';\n"
    )
    monkeypatch.setattr(
        dsh, "verify_public_audio", lambda **_kwargs: {"checked": False, "ok": True}
    )

    result = verify_publish(
        repo_root=tmp_path,
        date="2026-06-15",
        remote="origin",
        branch="main",
        public_base_url="https://example.com/News-Grasp/",
        wait_sec=0,
        poll_sec=1,
    )

    assert result["ok"] is True, result.get("reason")
    assert result["artifact_head"] == deploy_head
    assert result["local_head"] == source_head
    assert result["remote_head"] == source_head
    assert result["deploy_relevant_head"] == deploy_head


def test_verify_publish_accepts_later_public_change_outside_current_issue(
    monkeypatch, tmp_path: Path
) -> None:
    """別日付の公開修正は、対象日付の公開完了をRedへ戻さない。"""
    result, artifact_head, remote_head = _verify_issue_scoped_publish_fixture(
        monkeypatch, tmp_path, mutate_current_issue=False
    )

    assert result["ok"] is True, result.get("reason")
    assert result["artifact_head"] == artifact_head
    assert result["local_head"] == remote_head
    assert result["remote_head"] == remote_head
    assert result["issue_public_tree_unchanged"] is True
    assert result["pwa"]["public_sw_version"] == "remote-version"


def test_verify_publish_rejects_later_change_to_current_issue_category(
    monkeypatch, tmp_path: Path
) -> None:
    """対象日付の公開artifactが変わった場合は、旧artifact完了を受理しない。"""
    result, artifact_head, remote_head = _verify_issue_scoped_publish_fixture(
        monkeypatch, tmp_path, mutate_current_issue=True
    )

    assert result["ok"] is False
    assert result["reason"] == "artifact_publish_head_stale"
    assert result["artifact_head"] == artifact_head
    assert result["remote_head"] == remote_head


def test_verify_publish_rejects_later_change_to_current_issue_category_landing(
    monkeypatch, tmp_path: Path
) -> None:
    """対象号をfeatured表示するカテゴリlandingの後続改変も拒否する。"""
    result, artifact_head, remote_head = _verify_issue_scoped_publish_fixture(
        monkeypatch,
        tmp_path,
        mutate_current_issue=False,
        mutate_category_landing=True,
    )

    assert result["ok"] is False
    assert result["reason"] == "artifact_publish_head_stale"
    assert result["artifact_head"] == artifact_head
    assert result["remote_head"] == remote_head


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


def _write_historical_public_archive(tmp_path: Path, date: str) -> dict[str, str]:
    daily_audio = (
        "https://github.com/HIDEPON-UMG/News-Grasp/releases/download/"
        f"audio-daily/{date}.mp3?v=dailyhash"
    )
    deepdive_audio = (
        "https://github.com/HIDEPON-UMG/News-Grasp/releases/download/"
        f"audio-deepdive/{date}.mp3?v=deephash"
    )
    pages = {
        f"{date}/": f"<html><title>{date}</title></html>",
        f"{date}/summary/": f'<html><audio src="{daily_audio}"></audio></html>',
        f"deepdive/{date}/": f'<html><audio src="{deepdive_audio}"></audio></html>',
    }
    for suffix, body in pages.items():
        target = tmp_path / "docs" / suffix / "index.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return {**pages, "daily_audio": daily_audio, "deepdive_audio": deepdive_audio}


def test_verify_publish_accepts_historical_archive_after_newer_daily_publish(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """後日復旧は最新日のroot sentinelを巻き戻さず、対象日archiveをHEAD一致で証明する。"""
    date = "2026-08-07"
    archive = _write_historical_public_archive(tmp_path, date)
    monkeypatch.setattr(dsh, "_git_output", lambda _repo, args: "abc123\trefs/heads/main" if args[0] == "ls-remote" else "abc123")
    _mock_deploy_workflow_success(monkeypatch)
    _mock_pages_build_success(monkeypatch)
    monkeypatch.setattr(dsh, "verify_public_sw_version", lambda **_kwargs: {"ok": True})

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
            return FakeResponse(json.dumps({"result": "published_ok", "date": "2026-08-09"}))
        if url in {archive["daily_audio"], archive["deepdive_audio"]}:
            return FakeResponse()
        base = "https://example.com/News-Grasp/"
        suffix = url.removeprefix(base)
        if suffix in archive:
            return FakeResponse(archive[suffix])
        raise AssertionError(url)

    monkeypatch.setattr(dsh.urllib.request, "urlopen", fake_urlopen)
    result = verify_publish(
        repo_root=tmp_path,
        date=date,
        remote="origin",
        branch="main",
        public_base_url="https://example.com/News-Grasp/",
        wait_sec=0,
        poll_sec=1,
    )

    assert result["ok"] is True
    assert result["status_mode"] == "historical_archive"
    assert result["historical_archive"]["audio"]["ok"] is True


def test_verify_publish_rejects_historical_archive_content_drift(monkeypatch, tmp_path: Path) -> None:
    date = "2026-08-07"
    archive = _write_historical_public_archive(tmp_path, date)
    monkeypatch.setattr(dsh, "_git_output", lambda _repo, args: "abc123\trefs/heads/main" if args[0] == "ls-remote" else "abc123")
    _mock_deploy_workflow_success(monkeypatch)
    _mock_pages_build_success(monkeypatch)

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
            return FakeResponse(json.dumps({"result": "published_ok", "date": "2026-08-09"}))
        suffix = url.removeprefix("https://example.com/News-Grasp/")
        if suffix == f"{date}/summary/":
            return FakeResponse("<html>stale summary</html>")
        if suffix in archive:
            return FakeResponse(archive[suffix])
        raise AssertionError(url)

    monkeypatch.setattr(dsh.urllib.request, "urlopen", fake_urlopen)
    result = verify_publish(
        repo_root=tmp_path,
        date=date,
        remote="origin",
        branch="main",
        public_base_url="https://example.com/News-Grasp/",
        wait_sec=0,
        poll_sec=1,
    )

    assert result["ok"] is False
    assert result["reason"] == "public_sentinel_missing"
    assert "historical_archive_mismatch" in result["detail"]


def test_verify_publish_rejects_historical_archive_missing_surface(monkeypatch, tmp_path: Path) -> None:
    date = "2026-08-07"
    archive = _write_historical_public_archive(tmp_path, date)
    monkeypatch.setattr(dsh, "_git_output", lambda _repo, args: "abc123\trefs/heads/main" if args[0] == "ls-remote" else "abc123")
    _mock_deploy_workflow_success(monkeypatch)
    _mock_pages_build_success(monkeypatch)

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
            return FakeResponse(json.dumps({"result": "published_ok", "date": "2026-08-09"}))
        suffix = url.removeprefix("https://example.com/News-Grasp/")
        if suffix == f"deepdive/{date}/":
            raise dsh.urllib.error.URLError("not found")
        if suffix in archive:
            return FakeResponse(archive[suffix])
        raise AssertionError(url)

    monkeypatch.setattr(dsh.urllib.request, "urlopen", fake_urlopen)
    result = verify_publish(
        repo_root=tmp_path,
        date=date,
        remote="origin",
        branch="main",
        public_base_url="https://example.com/News-Grasp/",
        wait_sec=0,
        poll_sec=1,
    )

    assert result["ok"] is False
    assert result["reason"] == "public_sentinel_missing"
    assert "historical_archive_fetch_failed" in result["detail"]


PUBLISH_COMMIT = "a" * 40
_REAL_VERIFY_DEEPDIVE_QUALITY_HEAD_BINDING = (
    dsh._verify_deepdive_quality_head_binding
)


@pytest.fixture(autouse=True)
def _isolate_publish_complete_shared_quality(monkeypatch, tmp_path: Path) -> None:
    """publish verifier固有テストは共有品質engineを明示fixtureで隔離する。"""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(
        dsh,
        "deepdive_quality",
        SimpleNamespace(
            audit_issue=lambda **_kwargs: {
                "status": "Green",
                "issueCodes": [],
                "issues": [],
            }
        ),
        raising=False,
    )
    monkeypatch.setattr(
        dsh,
        "_verify_deepdive_quality_head_binding",
        lambda **_kwargs: {
            "ok": True,
            "reason": "",
            "head": PUBLISH_COMMIT,
            "paths": [],
        },
        raising=False,
    )


def _write_publish_complete_inventory(
    repo_root: Path,
    date: str = "2026-06-20",
    *,
    distribution_manifest: dict | str | None = None,
) -> None:
    state_path = repo_root / "News-Grasp" / "ops" / "news-grasp-runner-state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    run_id = "fixture-run"
    run_intent = "ScheduledProduction"
    lineage = dsh._producer_lineage_expected(
        repo_root=repo_root,
        ops_root=repo_root,
        date=date,
        run_intent=run_intent,
        run_id=run_id,
    )
    state_path.write_text(
        json.dumps(
            {
                "date": date,
                "status": "publish_complete",
                "exit_code": 0,
                "run_id": run_id,
                "run_intent": run_intent,
                **lineage,
            }
        ),
        encoding="utf-8",
    )
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


def test_verify_publish_complete_rejects_shared_deepdive_quality_red_before_public_probe(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """公開完了は単日Greenで日跨ぎ反復を代用せず、共有品質Redを先に返す。"""
    _write_publish_complete_inventory(tmp_path)
    public_probe_calls: list[dict] = []
    monkeypatch.setattr(
        dsh.deepdive_quality,
        "audit_issue",
        lambda **_kwargs: {
            "status": "Red",
            "issueCodes": ["deepdive_dialogue_value_invalid"],
            "issues": ["CORPUS: 日跨ぎ台本類似度超過"],
        },
    )
    monkeypatch.setattr(
        dsh,
        "verify_publish",
        lambda **kwargs: public_probe_calls.append(kwargs) or {"ok": True},
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
    assert result["reason"] == "deepdive_dialogue_value_invalid"
    assert result["deepdive_shared_quality"]["status"] == "Red"
    assert public_probe_calls == []


def test_verify_publish_complete_rejects_uncommitted_quality_substitution_before_public_probe(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """working treeだけGreenにした内容を公開commitの品質証明へ代用できない。"""
    _write_publish_complete_inventory(tmp_path)
    public_probe_calls: list[dict] = []
    monkeypatch.setattr(
        dsh,
        "_verify_deepdive_quality_head_binding",
        lambda **_kwargs: {
            "ok": False,
            "reason": "deepdive_quality_source_dirty",
            "paths": ["digest/DeepDive/2026-06-20-DeepDive-dialogue.md"],
        },
    )
    monkeypatch.setattr(
        dsh,
        "verify_publish",
        lambda **kwargs: public_probe_calls.append(kwargs) or {"ok": True},
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
    assert result["reason"] == "deepdive_quality_source_dirty"
    assert result["deepdive_quality_head_binding"]["ok"] is False
    assert public_probe_calls == []


def test_deepdive_quality_head_binding_uses_real_git_bytes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    target = repo / "digest" / "DeepDive" / "2026-06-20-DeepDive.md"
    target.parent.mkdir(parents=True)
    target.write_text("committed quality bytes\n", encoding="utf-8")
    for args in (
        ["git", "init", "-q", str(repo)],
        ["git", "-C", str(repo), "config", "user.email", "test@example.invalid"],
        ["git", "-C", str(repo), "config", "user.name", "News-Grasp Test"],
        ["git", "-C", str(repo), "add", "--", target.relative_to(repo).as_posix()],
        ["git", "-C", str(repo), "commit", "-q", "-m", "fixture"],
    ):
        subprocess.run(args, check=True, capture_output=True)
    committed_evidence = ddq._audit_file_evidence(target, target.read_bytes())
    audit = {"auditedFiles": [committed_evidence]}

    green = _REAL_VERIFY_DEEPDIVE_QUALITY_HEAD_BINDING(
        repo_root=repo,
        audit=audit,
    )
    assert green["ok"] is True

    target.write_text("uncommitted substitution\n", encoding="utf-8")
    substituted_audit = {
        "auditedFiles": [ddq._audit_file_evidence(target, target.read_bytes())]
    }
    red = _REAL_VERIFY_DEEPDIVE_QUALITY_HEAD_BINDING(
        repo_root=repo,
        audit=substituted_audit,
    )
    assert red["ok"] is False
    assert red["reason"] == "deepdive_quality_head_blob_mismatch"


def test_verify_publish_complete_rejects_quality_head_publish_head_drift(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _write_publish_complete_inventory(tmp_path)
    monkeypatch.setattr(
        dsh,
        "_verify_deepdive_quality_head_binding",
        lambda **_kwargs: {
            "ok": True,
            "reason": "",
            "head": "b" * 40,
            "paths": ["digest/DeepDive/2026-06-20-DeepDive.md"],
        },
    )
    monkeypatch.setattr(
        dsh,
        "verify_publish",
        lambda **_kwargs: {
            "ok": True,
            "local_head": PUBLISH_COMMIT,
            "remote_head": PUBLISH_COMMIT,
            "url": "status",
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
    assert result["reason"] == "deepdive_quality_head_mismatch"


def test_verify_live_runner_readiness_rejects_direct_bootstrap_without_task_launcher(
    monkeypatch, tmp_path: Path
) -> None:
    """06:00 は clean production runtime を選ぶ pythonw launcher 以外を Green にしない。"""
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
        live_task_launcher_path=live_runner.parent / "news-grasp-task-launcher.pyw",
        date="2026-06-20",
        run_canary=True,
    )

    assert result["ok"] is False
    assert result["reason"] == "scheduled_task_launcher_required"
    assert result["repo_runner"]["sha256"] == result["live_runner"]["sha256"]
    assert result["repo_watcher"]["sha256"] == result["live_watcher"]["sha256"]
    assert result["repo_bootstrap"]["sha256"] == result["live_bootstrap"]["sha256"]
    assert result["scheduled_task"]["targets_live_bootstrap"] is True
    assert result["scheduled_task"]["runner_action_is_production_start"] is True


def _write_live_binding_authority_fixture(
    live_bin: Path,
    launcher: Path,
    *,
    task_name: str = "News-Grasp Production",
    bootstrap_task_name: str = "News-Grasp Bootstrap",
) -> tuple[dict[str, object], dict[str, object], Path, str]:
    receipt = "b" * 64
    binding = live_bin / "news-grasp-high-cost-binding-v1.json"
    binding.write_text(
        json.dumps(
            {
                "schemaVersion": "NEWS_GRASP_HIGH_COST_BINDING_V1",
                "bindingReceiptSha256": receipt,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    python_exe = live_bin / "python.exe"
    pythonw_exe = live_bin / "pythonw.exe"
    python_exe.write_bytes(b"python-fixture")
    pythonw_exe.write_bytes(b"pythonw-fixture")
    recovery = {
        "schemaVersion": "NEWS_GRASP_RECOVERY_RUNTIME_BINDING_V1",
        "highCostBindingPath": str(binding.resolve()),
        "highCostBindingReceiptSha256": receipt,
        "highCostBindingFileSha256": hashlib.sha256(binding.read_bytes()).hexdigest(),
        "pythonExe": str(python_exe.resolve()),
        "pythonExeSha256": hashlib.sha256(python_exe.read_bytes()).hexdigest(),
        "taskPythonwPath": str(pythonw_exe.resolve()),
        "taskPythonwSha256": hashlib.sha256(pythonw_exe.read_bytes()).hexdigest(),
        "pythonTrustAnchor": "authenticode:python-software-foundation",
        "pythonSignerSubject": "CN=Python Software Foundation, O=Python Software Foundation, fixture",
        "pythonSignerThumbprint": "d" * 40,
        "pythonwTrustAnchor": "authenticode:python-software-foundation",
        "pythonwSignerSubject": "CN=Python Software Foundation, O=Python Software Foundation, fixture",
        "pythonwSignerThumbprint": "d" * 40,
        "opsRepoRoot": str(live_bin.parent.resolve()),
        "opsHead": "a" * 40,
        "trustedRemote": "https://github.com/HIDEPON-UMG/News-Grasp.git",
        "dailySelfHealPath": str((live_bin.parent / "tools" / "daily_self_heal.py").resolve()),
        "dailySelfHealSha256": "c" * 64,
    }
    (live_bin / "news-grasp-recovery-runtime-binding-v1.json").write_text(
        json.dumps(recovery) + "\n", encoding="utf-8"
    )
    executable = str(pythonw_exe.resolve())
    runner_action = [
        executable,
        str(launcher.resolve()),
        "runner",
        "--scheduled-task-name",
        task_name,
        "--high-cost-binding-path",
        str(binding.resolve()),
        "--high-cost-binding-sha256",
        receipt,
    ]
    bootstrap_action = [
        executable,
        str(launcher.resolve()),
        "bootstrap",
        "--scheduled-task-name",
        bootstrap_task_name,
        "--high-cost-binding-path",
        str(binding.resolve()),
        "--high-cost-binding-sha256",
        receipt,
    ]
    authority = {
        "schemaVersion": "STABLE_TASK_AUTHORITY_V1",
        "action": runner_action,
    }
    authority["authoritySha256"] = hashlib.sha256(
        json.dumps(authority, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    (live_bin / "news-grasp-stable-task-authority-v1.json").write_text(
        json.dumps(authority, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    def details(action: list[str]) -> dict[str, object]:
        arguments = subprocess.list2cmdline(action[1:])
        return {
            "action_summary": f"{action[0]} {arguments}",
            "actions": [{"execute": action[0], "arguments": arguments}],
        }

    return details(runner_action), details(bootstrap_action), binding, receipt


@pytest.mark.parametrize(
    ("deadman_enabled", "bootstrap_last_result", "expected_ok", "expected_reason"),
    [
        (True, 0, True, ""),
        (True, 1, False, "bootstrap_task_last_result_not_ok"),
        (False, 0, False, "deadman_task_definition_invalid"),
    ],
)
def test_verify_live_runner_readiness_accepts_pythonw_task_launcher_contract(
    monkeypatch,
    tmp_path: Path,
    deadman_enabled: bool,
    bootstrap_last_result: int,
    expected_ok: bool,
    expected_reason: str,
) -> None:
    """pythonw task launcher の mode 契約を検証し、正規の 06:00/05:55 action を受理する。"""
    repo_ops = tmp_path / "scripts" / "ops"
    live_bin = tmp_path / "bin"
    repo_ops.mkdir(parents=True)
    live_bin.mkdir(parents=True)
    # Bind the readiness fixture to a deterministic current issue-date
    # generation instead of the shared user's active pointer.
    runtime_root = tmp_path / ".news-grasp-runtime"
    runtime_root.mkdir(parents=True)
    (runtime_root / "active-generation-v2.json").write_text(
        json.dumps(
            {
                "schemaVersion": "NEWS_GRASP_ACTIVE_GENERATION_V2",
                "generationId": "fixture-generation-20260822",
                "issuedAtUtc": "2026-08-21T20:50:00+00:00",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    # Keep pathlib's real class; only redirect this module's classmethod home
    # lookup so the active-generation and Bootstrap receipt are deterministic.
    monkeypatch.setattr(dsh.Path, "home", classmethod(lambda _cls: tmp_path))
    runner_with_interlock = _runner_with_pre_run_interlock_source()
    launcher_source = """
parser.add_argument(
    "mode",
    choices=(
        "dispatch",
        "runner",
        "bootstrap",
        "converge-runtime",
        "maintain-runtime",
        "scheduled-equivalent-nopublish",
    ),
)
parser.add_argument("--schedule-id")
parser.add_argument("--intent")
script = bin_dir / "news-grasp-bootstrap.ps1"
extra = [
    "-Start", "-UseProductionRuntime", "-ScheduledTaskName", "News-Grasp Runner",
] if args.mode == "runner" else [
    "-Start", "-UseProductionRuntime", "-ScheduledTaskName", "News-Grasp Bootstrap",
    "-SmokeTest", "-SkipSourceSync", "-PollSeconds", "1", "-TimeoutMinutes", "2",
    "-StateFile", "ng-smoke-state.json", "-LogDir", "ng-smoke-logs",
]
creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
"""
    for name, source in {
        "news-grasp-runner.ps1": runner_with_interlock,
        "watch-news-grasp-runner.ps1": "watcher",
        "news-grasp-bootstrap.ps1": "bootstrap",
        "news-grasp-task-launcher.pyw": launcher_source,
    }.items():
        (repo_ops / name).write_text(source, encoding="utf-8")
        (live_bin / name).write_text(source, encoding="utf-8")

    live_launcher = live_bin / "news-grasp-task-launcher.pyw"
    _legacy_runner_action, _legacy_bootstrap_action, live_binding, binding_receipt_sha256 = (
        _write_live_binding_authority_fixture(live_bin, live_launcher)
    )
    executable = str((live_bin / "pythonw.exe").resolve())
    launcher_path = str(live_launcher.resolve())
    binding_path = str(live_binding.resolve())
    production_runtime = str((Path.home() / ".news-grasp-runtime" / "production-runtime").resolve())
    canonical_runner_argv = [
        executable,
        launcher_path,
        "dispatch",
        "--schedule-id",
        "news-grasp-daily-v1",
        "--intent",
        "reconcile",
    ]
    canonical_bootstrap_argv = [
        executable,
        launcher_path,
        "bootstrap",
        "--scheduled-task-name",
        "News-Grasp Bootstrap",
        "--high-cost-binding-path",
        binding_path,
        "--high-cost-binding-sha256",
        binding_receipt_sha256,
    ]

    def action_details(action: list[str], *, working_directory: str = "") -> dict[str, object]:
        arguments = subprocess.list2cmdline(action[1:])
        return {
            "action_summary": f"{action[0]} {arguments}",
            "actions": [
                {
                    "execute": action[0],
                    "arguments": arguments,
                    "workingDirectory": working_directory,
                }
            ],
        }

    runner_action = action_details(canonical_runner_argv, working_directory=production_runtime)
    bootstrap_action = action_details(canonical_bootstrap_argv)
    deadman_launcher = live_bin / "news-grasp-deadman-launcher.pyw"
    deadman_launcher.write_text("# deadman launcher fixture\n", encoding="utf-8")
    current_user_id = r"CONTOSO\hidek"
    runner_action["current_user_id"] = current_user_id
    runner_action["task_topology"] = [
        {
            "task_name": "News-Grasp Deadman",
            "task_path": "\\",
            "enabled": deadman_enabled,
            "state": "Ready",
            "multiple_instances_policy": "IgnoreNew",
            "execution_time_limit": "PT1H45M",
            "principal_user_id": current_user_id,
            "principal_logon_type": "Interactive",
            "principal_run_level": "Limited",
            "actions": [
                {
                    "execute": executable,
                    "arguments": subprocess.list2cmdline([str(deadman_launcher.resolve())]),
                    "workingDirectory": str(live_bin.resolve()),
                }
            ],
            "triggers": [
                {
                    "enabled": True,
                    "trigger_type": "MSFT_TaskDailyTrigger",
                    "days_interval": 1,
                    "start_boundary": "2026-08-22T06:40:00",
                    "repetition_interval": "PT1H",
                    "repetition_duration": "P1D",
                    "stop_at_duration_end": False,
                }
            ],
        },
        {
            "task_name": "News-Grasp Pull",
            "task_path": "\\",
            "enabled": False,
            "state": "Ready",
        },
        {
            "task_name": "News-Grasp Runner",
            "task_path": "\\",
            "enabled": False,
            "state": "Ready",
        },
    ]
    cleanroom_triggers = [
        {
            "triggerId": "scheduled-0600",
            "kind": "daily",
            "localTime": "06:00:00",
            "timeZone": "Asia/Tokyo",
        },
    ]
    cleanroom_task_triggers = [
        {
            "enabled": True,
            "trigger_type": "MSFT_TaskDailyTrigger",
            "days_interval": 1,
            "start_boundary": "2026-08-22T06:00:00",
        }
    ]
    authority = {
        "schemaVersion": "STABLE_TASK_AUTHORITY_V1",
        "taskName": "News-Grasp Production",
        "taskPath": "\\",
        "multipleInstancesPolicy": "IgnoreNew",
        "principal": {
            "userId": current_user_id,
            "logonType": "Interactive",
            "runLevel": "Limited",
        },
        "action": canonical_runner_argv,
        "manifestAction": {
            "entryModule": "tools.news_grasp_cleanroom_dispatch",
            "argv": [
                "dispatch",
                "--schedule-id",
                "news-grasp-daily-v1",
                "--intent",
                "reconcile",
            ],
            "workingDirectoryToken": "<RUNTIME_ROOT>",
        },
        "triggers": [
            {
                "triggerId": "scheduled-0600",
                "kind": "daily",
                "localTime": "06:00:00",
                "timeZone": "Asia/Tokyo",
            },
        ],
        "workingDirectoryToken": "<RUNTIME_ROOT>",
        "highCostBindingPath": binding_path,
        "highCostBindingReceiptSha256": binding_receipt_sha256,
        "generationId": "fixture-generation-20260822",
        "generationTimestamp": "2026-08-22T05:50:00+09:00",
    }
    authority["authoritySha256"] = hashlib.sha256(
        json.dumps(authority, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    authority_path = live_bin / "news-grasp-stable-task-authority-v1.json"
    authority_path.write_text(
        json.dumps(authority, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    installed_manifest_sha256 = "c" * 64
    (live_bin / "news-grasp-bootstrap-execution-receipt-v1.json").write_text(
        json.dumps(
            {
                "schemaVersion": "NEWS_GRASP_BOOTSTRAP_EXECUTION_RECEIPT_V1",
                "status": "succeeded",
                "issueDate": "2026-08-22",
                "observedAt": "2026-08-22T05:55:00+09:00",
                "generationId": "fixture-generation-20260822",
                "manifestSha256": installed_manifest_sha256,
                "stableAuthoritySha": authority["authoritySha256"],
                "stableAuthorityFileSha256": hashlib.sha256(
                    authority_path.read_bytes()
                ).hexdigest(),
                "taskName": "News-Grasp Bootstrap",
                "originWitness": {
                    "taskName": "News-Grasp Bootstrap",
                    "source": "scheduled-task",
                    "mode": "bootstrap",
                },
                "childExitCode": 0,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    runner_action.update(
        {
            "ok": True,
            "enabled": True,
            "task_name": "News-Grasp Production",
            "task_path": "\\",
            "multiple_instances_policy": "IgnoreNew",
            "current_user_id": current_user_id,
            "principal_user_id": current_user_id,
            "principal_logon_type": "Interactive",
            "principal_run_level": "Limited",
            "state": "Ready",
            "triggers": cleanroom_task_triggers,
            "last_task_result": 0,
            "next_run_time": "2026-08-22T06:00:00",
            "number_of_missed_runs": 0,
        }
    )
    bootstrap_action.update(
        {
            "ok": True,
            "enabled": True,
            "task_name": "News-Grasp Bootstrap",
            "task_path": "\\",
            "multiple_instances_policy": "IgnoreNew",
            "current_user_id": current_user_id,
            "principal_user_id": current_user_id,
            "principal_logon_type": "Interactive",
            "principal_run_level": "Limited",
            "state": "Ready",
            "triggers": [
                {
                    "enabled": True,
                    "trigger_type": "MSFT_TaskDailyTrigger",
                    "days_interval": 1,
                    "start_boundary": "2026-08-22T05:55:00",
                }
            ],
            "last_task_result": bootstrap_last_result,
            "next_run_time": "2026-08-22T05:55:00",
            "last_run_time": "2026-08-22T05:55:00+09:00",
            "lastRunTime": "2026-08-22T05:55:00+09:00",
            "issue_date": "2026-08-22",
            "issueDate": "2026-08-22",
            "generation_id": "fixture-generation-20260822",
            "generationId": "fixture-generation-20260822",
            "installed_generation_id": "fixture-generation-20260822",
            "installedGenerationId": "fixture-generation-20260822",
            "installed_generation_timestamp": "2026-08-22T05:50:00+09:00",
            "installedGenerationTimestamp": "2026-08-22T05:50:00+09:00",
            "installed_manifest_sha256": installed_manifest_sha256,
            "installedManifestSha256": installed_manifest_sha256,
            "number_of_missed_runs": 0,
        }
    )
    monkeypatch.setattr(
        dsh,
        "_authenticode_identity",
        lambda _path, **_kwargs: {
            "status": "Valid",
            "subject": "CN=Python Software Foundation, O=Python Software Foundation, fixture",
            "thumbprint": "d" * 40,
        },
    )
    monkeypatch.setattr(
        dsh,
        "_trusted_ops_generation",
        lambda _root: {
            "root": str(tmp_path.resolve()),
            "head": "a" * 40,
            "remote": "https://github.com/HIDEPON-UMG/News-Grasp.git",
            "daily_self_heal_path": str((tmp_path / "tools" / "daily_self_heal.py").resolve()),
            "daily_self_heal_sha256": "c" * 64,
        },
    )

    def fake_task_details(**kwargs):
        return dict(
            bootstrap_action
            if kwargs.get("task_name") == "News-Grasp Bootstrap"
            else runner_action
        )

    monkeypatch.setattr(dsh, "_scheduled_task_details", fake_task_details)
    monkeypatch.setattr(
        dsh,
        "_probe_external_control_plane_readiness",
        lambda: {
            "schemaVersion": "EXTERNAL_CONTROL_PLANE_READINESS_V1",
            "status": "ready",
            "reasonCode": "",
            "modelLaunchCount": 0,
            "receiptSha256": "c" * 64,
        },
    )
    captured_canary: dict[str, object] = {}

    def fake_canary(**kwargs):
        captured_canary.update(kwargs)
        return {"ok": True, "status": "smoke_ok"}

    monkeypatch.setattr(dsh, "_run_live_startup_canary", fake_canary)

    result = dsh.verify_live_runner_readiness(
        repo_root=tmp_path,
        live_runner_path=live_bin / "news-grasp-runner.ps1",
        live_watcher_path=live_bin / "watch-news-grasp-runner.ps1",
        live_bootstrap_path=live_bin / "news-grasp-bootstrap.ps1",
        live_task_launcher_path=live_launcher,
        date="2026-08-22",
        run_canary=True,
    )

    assert result["ok"] is expected_ok
    assert result["reason"] == expected_reason
    assert result["scheduled_task"]["definition_ok"] is deadman_enabled
    assert result["scheduled_task"]["bootstrap_last_task_result"] == bootstrap_last_result
    assert result["scheduled_task"]["targets_live_task_launcher"] is deadman_enabled
    assert result["scheduled_task"]["task_launcher_mode_ok"] is deadman_enabled
    assert result["scheduled_task"]["bootstrap_definition_ok"] is True
    assert result["scheduled_task"]["high_cost_binding_action_ok"] is True
    assert result["external_control"]["status"] == "ready"
    assert captured_canary == {}
    if expected_ok:
        assert result["canary"]["ok"] is True
        assert result["canary"]["status"] == "task_origin_smoke_ok"
        assert result["canary"]["generationId"] == "fixture-generation-20260822"


def test_bootstrap_observation_accepts_fresh_manual_task_origin_after_install() -> None:
    """05:55以外の手動Task起動も、同一世代かつ実LastRunTime一致ならfreshとする。"""
    manifest_sha256 = "b" * 64
    authority_sha256 = "c" * 64
    ok, reason = dsh._bootstrap_observation_gate(
        bootstrap_details={
            "last_task_result": 0,
            "last_run_time": "2026-08-22T21:55:37+09:00",
            "installed_generation_id": "generation-live",
            "installed_manifest_sha256": manifest_sha256,
        },
        authority={"authoritySha256": authority_sha256},
        execution_receipt={
            "schemaVersion": "NEWS_GRASP_BOOTSTRAP_EXECUTION_RECEIPT_V1",
            "issueDate": "2026-08-22",
            "observedAt": "2026-08-22T21:55:50+09:00",
            "generationId": "generation-live",
            "manifestSha256": manifest_sha256,
            "stableAuthoritySha": authority_sha256,
            "taskName": "News-Grasp Bootstrap",
            "taskOriginWitnessStatus": "accepted",
            "taskOriginWitness": {"taskName": "News-Grasp Bootstrap"},
            "childExitCode": 0,
        },
        issue_date="2026-08-22",
        installed_generation_timestamp="2026-08-22T21:55:40+09:00",
    )
    assert ok is True
    assert reason == ""


def test_task_launcher_contract_accepts_current_registered_multimode_launcher() -> None:
    """現在のstable launcherを、整形済みsource文字列ではなくAST mode契約で受理する。"""
    launcher = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "ops"
        / "news-grasp-task-launcher.pyw"
    )

    result = dsh._task_launcher_source_contract(launcher)

    assert result["ok"] is True
    assert set(result["modes"]) >= {
        "dispatch",
        "runner",
        "bootstrap",
        "converge-runtime",
        "maintain-runtime",
        "scheduled-equivalent-nopublish",
    }


def test_task_launcher_contract_rejects_mode_decoy_without_bootstrap(tmp_path: Path) -> None:
    """コメント相当の文字列decoyがあっても、AST choicesにbootstrapがなければ拒否する。"""
    launcher = tmp_path / "news-grasp-task-launcher.pyw"
    launcher.write_text(
        '''
mode_decoy = 'choices=("runner","bootstrap","converge-runtime","maintain-runtime","scheduled-equivalent-nopublish")'
parser.add_argument(
    "mode",
    choices=("runner", "converge-runtime", "maintain-runtime", "scheduled-equivalent-nopublish"),
)
script = bin_dir / "news-grasp-bootstrap.ps1"
extra = [
    "-Start", "-UseProductionRuntime", "-ScheduledTaskName", "News-Grasp Runner",
] if args.mode == "runner" else [
    "-Start", "-UseProductionRuntime", "-ScheduledTaskName", "News-Grasp Bootstrap",
    "-SmokeTest", "-SkipSourceSync", "-PollSeconds", "1", "-TimeoutMinutes", "2",
    "-StateFile", "ng-smoke-state.json", "-LogDir", "ng-smoke-logs",
]
creationflags = subprocess.CREATE_NO_WINDOW
''',
        encoding="utf-8",
    )

    result = dsh._task_launcher_source_contract(launcher)

    assert result["ok"] is False
    assert result["reason"] == "task_launcher_contract_invalid"
    assert "bootstrap" in result["missing_modes"]


def test_task_launcher_contract_rejects_mode_decoy_without_dispatch(tmp_path: Path) -> None:
    """canonical production task は launcher の dispatch mode を必須にする。"""
    launcher = tmp_path / "news-grasp-task-launcher.pyw"
    launcher.write_text(
        '''
parser.add_argument(
    "mode",
    choices=("runner", "bootstrap", "converge-runtime", "maintain-runtime", "scheduled-equivalent-nopublish"),
)
script = bin_dir / "news-grasp-bootstrap.ps1"
extra = [
    "-Start", "-UseProductionRuntime", "-ScheduledTaskName", "News-Grasp Runner",
] if args.mode == "runner" else [
    "-Start", "-UseProductionRuntime", "-ScheduledTaskName", "News-Grasp Bootstrap",
    "-SmokeTest", "-SkipSourceSync", "-PollSeconds", "1", "-TimeoutMinutes", "2",
    "-StateFile", "ng-smoke-state.json", "-LogDir", "ng-smoke-logs",
]
creationflags = subprocess.CREATE_NO_WINDOW
''',
        encoding="utf-8",
    )

    result = dsh._task_launcher_source_contract(launcher)

    assert result["ok"] is False
    assert result["reason"] == "task_launcher_contract_invalid"
    assert "dispatch" in result["missing_modes"]


def test_verify_live_runner_readiness_rejects_legacy_tombstone_as_canonical_success(
    monkeypatch, tmp_path: Path
) -> None:
    """legacy Runnerのexit 0をcanonical Production成功へ読み替えない。"""
    source_ops = Path(__file__).resolve().parents[1] / "scripts" / "ops"
    repo_ops = tmp_path / "scripts" / "ops"
    live_bin = tmp_path / "bin"
    repo_ops.mkdir(parents=True)
    live_bin.mkdir(parents=True)
    for name in (
        "watch-news-grasp-runner.ps1",
        "news-grasp-bootstrap.ps1",
        "news-grasp-task-launcher.pyw",
    ):
        source = (source_ops / name).read_text(encoding="utf-8-sig")
        (repo_ops / name).write_text(source, encoding="utf-8")
        (live_bin / name).write_text(source, encoding="utf-8")

    tombstone = "# legacy News-Grasp runner removed; direct mainline is authoritative\n"
    (repo_ops / "news-grasp-runner.ps1").write_text(tombstone, encoding="utf-8")
    (live_bin / "news-grasp-runner.ps1").write_text(tombstone, encoding="utf-8")

    live_runner = live_bin / "news-grasp-runner.ps1"
    live_launcher = live_bin / "news-grasp-task-launcher.pyw"

    def fake_task_details(**kwargs):
        bootstrap = kwargs.get("task_name") == "News-Grasp Bootstrap"
        start = "05:55:00" if bootstrap else "06:00:00"
        action = (
            f'pythonw.exe "{live_launcher}" bootstrap'
            if bootstrap
            else f'powershell.exe -File "{live_runner}"'
        )
        return {
            "ok": True,
            "state": "Ready",
            "action_summary": action,
            "triggers": [{"enabled": True, "start_boundary": f"2026-06-20T{start}"}],
            "last_task_result": 0 if bootstrap else 72,
            "last_run_time": f"2026-06-20T{start}",
            "next_run_time": f"2026-06-21T{start}",
            "number_of_missed_runs": 0,
        }

    monkeypatch.setattr(dsh, "_scheduled_task_details", fake_task_details)
    monkeypatch.setattr(dsh, "_run_live_startup_canary", lambda **_kwargs: {"ok": True, "status": "smoke_ok"})

    result = dsh.verify_live_runner_readiness(
        repo_root=tmp_path,
        live_runner_path=live_runner,
        live_watcher_path=live_bin / "watch-news-grasp-runner.ps1",
        live_bootstrap_path=live_bin / "news-grasp-bootstrap.ps1",
        live_task_launcher_path=live_launcher,
        date="2026-06-20",
        run_canary=True,
    )

    assert result["ok"] is False
    assert result["reason"] == "direct_runner_pre_run_interlock_missing"
    assert result["scheduled_task"]["legacy_direct_clean_runtime_trampoline"] is False
    assert result["scheduled_task"]["targets_live_runner"] is True
    assert result["scheduled_task"]["bootstrap_targets_live_task_launcher"] is True


def test_task_launcher_contract_requires_clean_runtime_on_both_scheduled_modes(
    tmp_path: Path,
) -> None:
    """旧launcherに単語があるだけでなく、runner/bootstrap両引数列のclean runtimeを必須にする。"""
    launcher = tmp_path / "news-grasp-task-launcher.pyw"
    launcher.write_text(
        '''
parser.add_argument("mode", choices=("runner", "bootstrap"))
script = bin_dir / "news-grasp-bootstrap.ps1"
extra = ["-Start"] if args.mode == "runner" else [
    "-Start", "-SmokeTest", "-PollSeconds", "1", "-TimeoutMinutes", "2",
    "-StateFile", "ng-smoke-state.json", "-LogDir", "ng-smoke-logs",
]
creationflags = subprocess.CREATE_NO_WINDOW
''',
        encoding="utf-8",
    )

    result = dsh._task_launcher_source_contract(launcher)

    assert result["ok"] is False
    assert result["reason"] == "task_launcher_contract_invalid"
    assert any("UseProductionRuntime" in token for token in result["missing_tokens"])


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


def test_verify_live_runner_readiness_rejects_bootstrap_before_direct_runner(monkeypatch, tmp_path: Path) -> None:
    """事前bootstrapがGreenでも06:00 direct runnerをclean-runtime routeにしない。"""
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

    assert result["ok"] is False
    assert result["reason"] == "scheduled_task_launcher_required"
    assert result["scheduled_task"]["targets_live_runner"] is True
    assert result["scheduled_task"]["direct_runner_pre_run_interlock"] is True
    assert result["scheduled_task"]["direct_runner_pre_run_reexec"] is True
    # LastTaskResult=0だけでは新しいexecution receipt契約を満たさない。
    assert result["scheduled_task"]["bootstrap_repairs_before_run"] is False
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
        assert "-SkipSourceSync" in command
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


def test_live_startup_canary_propagates_explicit_high_cost_binding(monkeypatch, tmp_path: Path) -> None:
    """production canary は Task Action と同じ binding identity を bootstrap へ渡す。"""
    startup = tmp_path / "bin" / "news-grasp-bootstrap.ps1"
    binding = startup.parent / "news-grasp-high-cost-binding-v1.json"
    startup.parent.mkdir(parents=True)
    startup.write_text("bootstrap", encoding="utf-8")
    launcher = startup.parent / "news-grasp-task-launcher.pyw"
    launcher.write_text("launcher", encoding="utf-8")
    _, _, binding, receipt_sha256 = _write_live_binding_authority_fixture(
        startup.parent, launcher
    )
    monkeypatch.setattr(
        dsh,
        "_authenticode_identity",
        lambda _path, **_kwargs: {
            "status": "Valid",
            "subject": "CN=Python Software Foundation, O=Python Software Foundation, fixture",
            "thumbprint": "d" * 40,
        },
    )
    monkeypatch.setattr(
        dsh,
        "_trusted_ops_generation",
        lambda _root: {
            "root": str(tmp_path.resolve()),
            "head": "a" * 40,
            "remote": "https://github.com/HIDEPON-UMG/News-Grasp.git",
            "daily_self_heal_path": str((tmp_path / "tools" / "daily_self_heal.py").resolve()),
            "daily_self_heal_sha256": "c" * 64,
        },
    )

    class Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, **kwargs):
        assert command[command.index("-HighCostBindingPath") + 1] == str(binding)
        assert command[command.index("-HighCostBindingReceiptSha256") + 1] == receipt_sha256
        assert kwargs["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
        state_file = Path(command[command.index("-StateFile") + 1])
        log_dir = Path(command[command.index("-LogDir") + 1])
        state_file.write_text(json.dumps({"status": "smoke_ok"}), encoding="utf-8")
        (log_dir / "2026-06-20.log").write_text(
            "news-grasp-runner.ps1 SMOKE OK\n", encoding="utf-8"
        )
        return Proc()

    monkeypatch.setattr(dsh.subprocess, "run", fake_run)

    result = dsh._run_live_startup_canary(
        repo_root=tmp_path,
        startup_path=startup,
        date="2026-06-20",
        high_cost_binding_path=binding,
        high_cost_binding_receipt_sha256=receipt_sha256,
        ops_repo_root=tmp_path,
    )

    assert result["ok"] is True


def test_live_startup_canary_rejects_ops_provenance_drift_before_launch(
    monkeypatch, tmp_path: Path
) -> None:
    """canary単体でもrecovery bindingとcanonical ops generationのずれを拒否する。"""
    startup = tmp_path / "bin" / "news-grasp-bootstrap.ps1"
    startup.parent.mkdir(parents=True)
    startup.write_text("bootstrap", encoding="utf-8")
    launcher = startup.parent / "news-grasp-task-launcher.pyw"
    launcher.write_text("launcher", encoding="utf-8")
    _, _, binding, receipt_sha256 = _write_live_binding_authority_fixture(
        startup.parent, launcher
    )
    monkeypatch.setattr(
        dsh,
        "_authenticode_identity",
        lambda _path, **_kwargs: {
            "status": "Valid",
            "subject": "CN=Python Software Foundation, O=Python Software Foundation, fixture",
            "thumbprint": "d" * 40,
        },
    )
    monkeypatch.setattr(
        dsh,
        "_trusted_ops_generation",
        lambda _root: {
            "root": str(tmp_path.resolve()),
            "head": "e" * 40,
            "remote": "https://github.com/HIDEPON-UMG/News-Grasp.git",
            "daily_self_heal_path": str((tmp_path / "tools" / "daily_self_heal.py").resolve()),
            "daily_self_heal_sha256": "c" * 64,
        },
    )
    launched = False

    def fake_run(*_args, **_kwargs):
        nonlocal launched
        launched = True
        raise AssertionError("canary must fail before launch")

    monkeypatch.setattr(dsh.subprocess, "run", fake_run)
    result = dsh._run_live_startup_canary(
        repo_root=tmp_path,
        ops_repo_root=tmp_path,
        startup_path=startup,
        date="2026-06-20",
        high_cost_binding_path=binding,
        high_cost_binding_receipt_sha256=receipt_sha256,
    )

    assert result["ok"] is False
    assert result["reason"] == "canary_binding_authority_invalid"
    assert launched is False


def test_live_startup_canary_rejects_partial_high_cost_binding_before_launch(
    monkeypatch, tmp_path: Path
) -> None:
    """binding path/hash の片側だけでは subprocess を開始しない。"""
    startup = tmp_path / "bin" / "news-grasp-bootstrap.ps1"
    startup.parent.mkdir(parents=True)
    startup.write_text("bootstrap", encoding="utf-8")
    launched = False

    def fake_run(*_args, **_kwargs):
        nonlocal launched
        launched = True
        raise AssertionError("subprocess must not start")

    monkeypatch.setattr(dsh.subprocess, "run", fake_run)
    result = dsh._run_live_startup_canary(
        repo_root=tmp_path,
        startup_path=startup,
        date="2026-06-20",
        high_cost_binding_path=startup.parent / "binding.json",
    )

    assert result["ok"] is False
    assert result["reason"] == "canary_binding_incomplete"
    assert launched is False


@pytest.mark.parametrize(
    ("mutation", "expected_reason"),
    [
        ("missing_binding", "high_cost_binding_action_invalid"),
        ("duplicate_option", "high_cost_binding_action_invalid"),
        ("multiple_actions", "high_cost_binding_action_invalid"),
        ("out_of_bin", "high_cost_binding_action_invalid"),
        ("malformed_receipt", "high_cost_binding_authority_invalid"),
        ("recovery_mismatch", "high_cost_binding_authority_invalid"),
        ("ops_git_failure", "high_cost_binding_authority_invalid"),
    ],
)
def test_live_high_cost_binding_authority_fails_closed(
    monkeypatch, tmp_path: Path, mutation: str, expected_reason: str
) -> None:
    """Task文字列だけをauthorityにせず、stable/live binding三者照合でfail-closedにする。"""
    live_bin = tmp_path / "bin"
    live_bin.mkdir()
    launcher = live_bin / "news-grasp-task-launcher.pyw"
    launcher.write_text("launcher", encoding="utf-8")
    runner, bootstrap, binding, _ = _write_live_binding_authority_fixture(
        live_bin, launcher
    )
    monkeypatch.setattr(
        dsh,
        "_authenticode_identity",
        lambda _path, **_kwargs: {
            "status": "Valid",
            "subject": "CN=Python Software Foundation, O=Python Software Foundation, fixture",
            "thumbprint": "d" * 40,
        },
    )
    monkeypatch.setattr(
        dsh,
        "_trusted_ops_generation",
        lambda _root: {
            "root": str(tmp_path.resolve()),
            "head": "a" * 40,
            "remote": "https://github.com/HIDEPON-UMG/News-Grasp.git",
            "daily_self_heal_path": str((tmp_path / "tools" / "daily_self_heal.py").resolve()),
            "daily_self_heal_sha256": "c" * 64,
        },
    )
    authority_path = live_bin / "news-grasp-stable-task-authority-v1.json"
    authority = json.loads(authority_path.read_text(encoding="utf-8"))

    def reseal_authority() -> None:
        authority.pop("authoritySha256", None)
        authority["authoritySha256"] = hashlib.sha256(
            json.dumps(
                authority, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        authority_path.write_text(
            json.dumps(authority, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    def bind_task_actions_to_authority() -> None:
        runner_action = list(authority["action"])
        bootstrap_action = list(runner_action)
        bootstrap_action[2] = "bootstrap"
        bootstrap_action[4] = "News-Grasp Bootstrap"
        for details, action in ((runner, runner_action), (bootstrap, bootstrap_action)):
            details["actions"] = [
                {
                    "execute": action[0],
                    "arguments": subprocess.list2cmdline(action[1:]),
                }
            ]

    if mutation == "missing_binding":
        for details in (runner, bootstrap):
            values = dsh._windows_action_arguments(details["actions"][0]["arguments"])
            values = values[:4]
            details["actions"][0]["arguments"] = subprocess.list2cmdline(values)
    elif mutation == "duplicate_option":
        runner["actions"][0]["arguments"] += " --high-cost-binding-sha256 " + "d" * 64
    elif mutation == "multiple_actions":
        runner["actions"].append(dict(runner["actions"][0]))
    elif mutation == "out_of_bin":
        authority["action"][0] = str((tmp_path / "evil.exe").resolve())
        bind_task_actions_to_authority()
        reseal_authority()
    elif mutation == "malformed_receipt":
        authority["action"][8] = "not-a-sha"
        bind_task_actions_to_authority()
        reseal_authority()
    elif mutation == "recovery_mismatch":
        recovery_path = live_bin / "news-grasp-recovery-runtime-binding-v1.json"
        recovery = json.loads(recovery_path.read_text(encoding="utf-8"))
        recovery["highCostBindingFileSha256"] = "0" * 64
        recovery_path.write_text(json.dumps(recovery) + "\n", encoding="utf-8")
    elif mutation == "ops_git_failure":
        def fail_ops_generation(_root):
            raise RuntimeError("bounded git failure")

        monkeypatch.setattr(dsh, "_trusted_ops_generation", fail_ops_generation)

    result = dsh._validate_live_high_cost_binding_authority(
        task_details=runner,
        bootstrap_details=bootstrap,
        live_task_launcher_path=launcher,
        task_name="News-Grasp Production",
        bootstrap_task_name="News-Grasp Bootstrap",
        ops_repo_root=tmp_path,
    )

    assert result["ok"] is False
    assert result["reason"] == expected_reason
    assert binding.is_file()


def test_safe_ops_git_disables_repo_controlled_execution_and_has_timeout(
    monkeypatch, tmp_path: Path
) -> None:
    """operational recovery: trusted ops判定はrepoのhook/fsmonitorを起動しない。"""
    captured: dict[str, object] = {}

    class Proc:
        returncode = 0
        stdout = "ok\n"
        stderr = ""

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return Proc()

    monkeypatch.setattr(dsh.subprocess, "run", fake_run)
    assert dsh._safe_ops_git_output(tmp_path, ["status", "--porcelain"]) == "ok"
    command = captured["command"]
    assert "core.hooksPath=NUL" in command
    assert "core.fsmonitor=false" in command
    assert "core.attributesFile=NUL" in command
    assert captured["timeout"] == 15
    assert captured["check"] is False


def test_trusted_ops_generation_allows_ignored_runtime_artifacts(
    monkeypatch, tmp_path: Path
) -> None:
    """復旧artifact同居rootでも、tracked source dirtyでなければops generationを検証できる。"""
    daily_self_heal = tmp_path / "tools" / "daily_self_heal.py"
    daily_self_heal.parent.mkdir(parents=True)
    daily_self_heal.write_text("fixture", encoding="utf-8")

    outputs = {
        ("rev-parse", "HEAD"): "a" * 40,
        ("remote", "get-url", "origin"): "https://github.com/HIDEPON-UMG/News-Grasp.git",
        ("status", "--porcelain", "--untracked-files=all"): "",
        (
            "ls-files",
            "--others",
            "--ignored",
            "--exclude-standard",
        ): "\n".join(
            [
                ".managed-root.pin",
                "build/tts/latest_audio.json",
                "build/youtube-podcast/uploads.json",
                "data/search_audit/2026-08-16/ai.json",
                "tools/__pycache__/daily_self_heal.cpython-312.pyc",
            ]
        ),
    }

    monkeypatch.setattr(
        dsh,
        "_safe_ops_git_output",
        lambda _root, args: outputs[tuple(args)],
    )

    result = dsh._trusted_ops_generation(tmp_path)

    assert result["head"] == "a" * 40
    assert result["daily_self_heal_sha256"] == dsh.sha256_file(daily_self_heal)


def test_trusted_ops_generation_rejects_tracked_dirty_with_runtime_artifacts(
    monkeypatch, tmp_path: Path
) -> None:
    """artifact許容はtracked source dirtyのfail-closedを緩めない。"""
    daily_self_heal = tmp_path / "tools" / "daily_self_heal.py"
    daily_self_heal.parent.mkdir(parents=True)
    daily_self_heal.write_text("fixture", encoding="utf-8")

    outputs = {
        ("rev-parse", "HEAD"): "a" * 40,
        ("remote", "get-url", "origin"): "https://github.com/HIDEPON-UMG/News-Grasp.git",
        ("status", "--porcelain", "--untracked-files=all"): " M tools/daily_self_heal.py",
        (
            "ls-files",
            "--others",
            "--ignored",
            "--exclude-standard",
        ): "build/tts/latest_audio.json",
    }

    monkeypatch.setattr(
        dsh,
        "_safe_ops_git_output",
        lambda _root, args: outputs[tuple(args)],
    )

    with pytest.raises(ValueError, match="ops generation invalid"):
        dsh._trusted_ops_generation(tmp_path)


def test_authenticode_timeout_is_typed_failure(monkeypatch, tmp_path: Path) -> None:
    """operational recovery: trust store停滞はtracebackでなくtyped authority failureにする。"""
    executable = tmp_path / "python.exe"
    executable.write_bytes(b"fixture")
    helper = tmp_path / "scripts" / "ops" / "get-news-grasp-authenticode-identity.ps1"
    helper.parent.mkdir(parents=True)
    helper.write_text("param([string]$TargetPath)", encoding="utf-8")

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="powershell.exe", timeout=15)

    monkeypatch.setattr(dsh.subprocess, "run", timeout)
    with pytest.raises(ValueError, match="authenticode verification unavailable"):
        dsh._authenticode_identity(executable, ops_repo_root=tmp_path)


def test_authenticode_identity_uses_tracked_powershell_helper(
    monkeypatch, tmp_path: Path
) -> None:
    """primary: Unicode pathを追跡済みhelperの-File引数へ束縛する。"""
    executable = tmp_path / "python.exe"
    executable.write_bytes(b"fixture")
    helper = tmp_path / "scripts" / "ops" / "get-news-grasp-authenticode-identity.ps1"
    helper.parent.mkdir(parents=True)
    helper.write_text("param([string]$TargetPath)", encoding="utf-8")
    captured: dict[str, object] = {}

    class Proc:
        returncode = 0
        stdout = json.dumps(
            {
                "status": "Valid",
                "subject": "CN=Python Software Foundation, O=Python Software Foundation, fixture",
                "thumbprint": "d" * 40,
            }
        )
        stderr = ""

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return Proc()

    monkeypatch.setattr(dsh.subprocess, "run", fake_run)
    identity = dsh._authenticode_identity(executable, ops_repo_root=tmp_path)

    command = captured["command"]
    assert command[0] == r"C:\Program Files\PowerShell\7\pwsh.exe"
    assert "-File" in command
    assert command[command.index("-File") + 1] == str(helper)
    assert command[command.index("-TargetPath") + 1] == str(executable)
    assert identity["status"] == "Valid"


def test_live_high_cost_binding_authority_rejects_coordinated_recovery_rewrite(
    monkeypatch, tmp_path: Path
) -> None:
    """adversarial: Task/authority/recoveryを同時再sealしてもunsigned executableはGreenにしない。"""
    live_bin = tmp_path / "bin"
    live_bin.mkdir()
    launcher = live_bin / "news-grasp-task-launcher.pyw"
    launcher.write_text("launcher", encoding="utf-8")
    runner, bootstrap, _, _ = _write_live_binding_authority_fixture(live_bin, launcher)
    recovery_path = live_bin / "news-grasp-recovery-runtime-binding-v1.json"
    recovery = json.loads(recovery_path.read_text(encoding="utf-8"))
    rogue_dir = tmp_path / "rogue"
    rogue_dir.mkdir()
    rogue_python = rogue_dir / "python.exe"
    rogue_pythonw = rogue_dir / "pythonw.exe"
    rogue_python.write_bytes(b"rogue-python")
    rogue_pythonw.write_bytes(b"rogue-pythonw")
    recovery.update(
        {
            "pythonExe": str(rogue_python.resolve()),
            "pythonExeSha256": hashlib.sha256(rogue_python.read_bytes()).hexdigest(),
            "taskPythonwPath": str(rogue_pythonw.resolve()),
            "taskPythonwSha256": hashlib.sha256(rogue_pythonw.read_bytes()).hexdigest(),
            "pythonSignerSubject": "CN=Python Software Foundation, O=Python Software Foundation, forged",
            "pythonSignerThumbprint": "f" * 40,
            "pythonwSignerSubject": "CN=Python Software Foundation, O=Python Software Foundation, forged",
            "pythonwSignerThumbprint": "f" * 40,
        }
    )
    recovery_path.write_text(json.dumps(recovery) + "\n", encoding="utf-8")
    authority_path = live_bin / "news-grasp-stable-task-authority-v1.json"
    authority = json.loads(authority_path.read_text(encoding="utf-8"))
    authority["action"][0] = str(rogue_pythonw.resolve())
    authority.pop("authoritySha256", None)
    authority["authoritySha256"] = hashlib.sha256(
        json.dumps(authority, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    authority_path.write_text(json.dumps(authority) + "\n", encoding="utf-8")
    runner_action = list(authority["action"])
    bootstrap_action = list(runner_action)
    bootstrap_action[2] = "bootstrap"
    bootstrap_action[4] = "News-Grasp Bootstrap"
    for details, action in ((runner, runner_action), (bootstrap, bootstrap_action)):
        details["actions"] = [
            {"execute": action[0], "arguments": subprocess.list2cmdline(action[1:])}
        ]
    monkeypatch.setattr(
        dsh,
        "_authenticode_identity",
        lambda _path, **_kwargs: {"status": "NotSigned", "subject": "", "thumbprint": ""},
    )
    monkeypatch.setattr(
        dsh,
        "_trusted_ops_generation",
        lambda _root: {
            "root": str(tmp_path.resolve()),
            "head": "a" * 40,
            "remote": "https://github.com/HIDEPON-UMG/News-Grasp.git",
            "daily_self_heal_path": str((tmp_path / "tools" / "daily_self_heal.py").resolve()),
            "daily_self_heal_sha256": "c" * 64,
        },
    )

    result = dsh._validate_live_high_cost_binding_authority(
        task_details=runner,
        bootstrap_details=bootstrap,
        live_task_launcher_path=launcher,
        task_name="News-Grasp Production",
        bootstrap_task_name="News-Grasp Bootstrap",
        ops_repo_root=tmp_path,
    )

    assert result == {"ok": False, "reason": "high_cost_binding_authority_invalid"}


def test_canonical_live_json_rejects_hardlink_alias(tmp_path: Path) -> None:
    """canonical leafと同一inodeでも複数linkを持つaliasはauthorityとして受理しない。"""
    original = tmp_path / "original.json"
    hardlink = tmp_path / "authority.json"
    original.write_text('{"ok":true}\n', encoding="utf-8")
    os.link(original, hardlink)

    with pytest.raises(ValueError, match="high_cost_binding_authority_invalid"):
        dsh._canonical_live_json(hardlink, expected=hardlink)


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
        assert "-SkipSourceSync" in command
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


def test_verify_publish_complete_records_next_date_but_observes_recovery_date(monkeypatch, tmp_path: Path) -> None:
    """当日recovery後の公開完了は、次回日付を記録しつつ当日bootstrap receiptでreadinessを評価する。"""
    _write_publish_complete_inventory(tmp_path)
    monkeypatch.setattr(dsh, "_next_scheduled_task_issue_date", lambda: "2026-08-05", raising=False)
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
        lambda **_kwargs: {
            "ok": True,
            "videoId": "deepdive-video",
            "title": "News-Grasp DeepDive Dialogue 2026-06-20",
        },
    )
    captured: dict[str, object] = {}

    def fake_readiness(**kwargs):
        captured.update(kwargs)
        return _live_runner_readiness_ok()

    monkeypatch.setattr(dsh, "verify_live_runner_readiness", fake_readiness)

    result = dsh.verify_publish_complete(
        repo_root=tmp_path,
        date="2026-06-20",
        remote="origin",
        branch="main",
        public_base_url="https://example.com/News-Grasp/",
        wait_sec=0,
        poll_sec=1,
    )

    assert result["ok"] is True
    assert result["readiness_date"] == "2026-08-05"
    assert result["readiness_observation_date"] == "2026-06-20"
    assert captured["date"] == "2026-06-20"


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
    monkeypatch.setattr(
        dsh,
        "_verify_deepdive_quality_head_binding",
        lambda **_kwargs: {"ok": True, "reason": "", "head": head, "paths": []},
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
    audience_sha = hashlib.sha256(b"[]").hexdigest()
    producer_sha = hashlib.sha256(
        (Path(dsh.__file__).with_name("send_push.py")).read_bytes()
    ).hexdigest()
    producer_run_id = "1" * 32
    audience_receipt = {
        "schemaVersion": "NEWS_GRASP_NOTIFICATION_AUDIENCE_RESOLUTION_V1",
        "date": "2026-06-20",
        "source": "file",
        "subscriptionCount": 0,
        "audienceSetSha256": audience_sha,
        "producer": "tools.send_push",
        "producerSha256": producer_sha,
        "producerRunId": producer_run_id,
        "resolvedAt": "2026-06-20T06:30:00+09:00",
    }
    audience_receipt["receiptSha256"] = hashlib.sha256(
        json.dumps(
            audience_receipt,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    notification_payload = {
                "status": "no_subscribers",
                "ok": True,
                "date": "2026-06-20",
                "subscription_count": 0,
                "sent_count": 0,
                "source": "file",
                "recorded_at": "2026-06-20T06:30:00+09:00",
                "payload_sha256": hashlib.sha256(b"").hexdigest(),
                "audience_set_sha256": audience_sha,
                "producer": "tools.send_push",
                "producer_sha256": producer_sha,
                "producer_run_id": producer_run_id,
                "audienceResolutionReceipt": audience_receipt,
                "audienceResolutionReceiptSha256": audience_receipt[
                    "receiptSha256"
                ],
            }
    from tools import send_push as push_sender

    push_sender._write_notification_state(
        str(notification), notification_payload
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
            "deploy_head": "b" * 40,
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
    assert stdout_manifest["source_commit"] == PUBLISH_COMMIT
    assert stdout_manifest["publish_commit"] == "b" * 40
    assert stdout_manifest["same_publish"]["source_head"] == PUBLISH_COMMIT
    assert stdout_manifest["same_publish"]["deploy_head"] == "b" * 40
    assert stdout_manifest["same_publish"]["distribution_pre_publish_commit"] == PUBLISH_COMMIT
    assert stdout_manifest == file_manifest


def test_ng_red_04_typed_completion_keeps_public_green_when_readiness_is_red(
    monkeypatch, tmp_path: Path
) -> None:
    public = {
        "ok": True,
        "date": "2026-08-02",
        "public_status": "green",
        "publicCompletionStatus": "green",
        "source_commit": "a" * 40,
        "publish_commit": "a" * 40,
        "completion_authority_id": "authority-1",
    }
    monkeypatch.setattr(dsh, "verify_public_completion", lambda **_kwargs: public, raising=False)
    monkeypatch.setattr(
        dsh,
        "verify_live_runner_readiness",
        lambda **_kwargs: {
            "ok": False,
            "reason": "runner_source_drift",
            "failedGateIds": ["next_run_runner_hash_parity"],
        },
    )

    try:
        result = dsh.evaluate_completion(
            repo_root=tmp_path,
            ops_repo_root=tmp_path,
            date="2026-08-02",
            remote="origin",
            branch="main",
            public_base_url="https://example.invalid/News-Grasp/",
            wait_sec=0,
            poll_sec=1,
        )
    except Exception as error:  # pragma: no cover - the preimplementation Red path
        pytest.fail(f"READINESS_ERASED_PUBLIC_GREEN: {error}")

    assert result["verificationStatus"] == "verified_incomplete"
    assert result["publicCompletionStatus"] == "green"
    assert result["nextRunReadinessStatus"] == "red"
    assert result["phase"] == "readiness"
    assert result["failedGateIds"] == ["next_run_runner_hash_parity"]
    assert result["completionAuthorityId"] == "authority-1"


def test_ng_red_06_typed_completion_readiness_repair_converges_without_public_recovery() -> None:
    try:
        result = dsh.complete_readiness_repair(
            {
                "verificationStatus": "verified_incomplete",
                "publicCompletionStatus": "green",
                "nextRunReadinessStatus": "red",
                "completionAuthorityId": "authority-1",
                "publicEvidenceSha256": "a" * 64,
            },
            {"ok": True, "reason": "", "failedGateIds": []},
        )
    except AttributeError as error:  # pragma: no cover - preimplementation Red path
        pytest.fail(f"READINESS_REPAIR_DID_NOT_CONVERGE: {error}")

    assert result["verificationStatus"] == "verified_green"
    assert result["publicCompletionStatus"] == "green"
    assert result["nextRunReadinessStatus"] == "green"
    assert result["phase"] == "readiness_repair"
    assert result["publicRecoveryStarted"] is False


def test_ng_red_10_causal_retry_same_cause_is_suppressed() -> None:
    from tools import audit_recovery_control as audit

    previous = {
        "source_sha256": "a" * 64,
        "runtime_sha256": "b" * 64,
        "config_sha256": "c" * 64,
        "authority_sha256": "d" * 64,
        "external_evidence_sha256": "e" * 64,
        "command": "old-command",
        "output_cap": 100,
    }
    current = {**previous, "command": "new-command", "output_cap": 10}
    try:
        decision = audit.causal_retry_gate(previous, current)
    except AttributeError as error:  # pragma: no cover - preimplementation Red path
        pytest.fail(f"SAME_CAUSE_RETRY_ALLOWED: {error}")

    assert decision["allowed"] is False
    assert decision["reasonCode"] == "SAME_CAUSE_UNCHANGED"


def test_sec_red_arbitrary_previous_authority_cannot_preserve_green(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        dsh,
        "verify_public_completion",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("public verifier boom")),
        raising=False,
    )
    monkeypatch.setattr(
        dsh,
        "verify_live_runner_readiness",
        lambda **_kwargs: {"ok": True, "reason": "", "failedGateIds": []},
    )

    result = dsh.evaluate_completion(
        repo_root=tmp_path,
        ops_repo_root=tmp_path,
        date="2026-08-02",
        remote="origin",
        branch="main",
        public_base_url="https://example.invalid/News-Grasp/",
        wait_sec=0,
        poll_sec=1,
        previous_public_authority={"completionAuthorityId": "forged"},
    )

    assert result["publicCompletionStatus"] == "unverified"
    assert result["completionAuthorityId"] == ""


def test_sec_red_full_forged_previous_authority_cannot_bypass_canonical_chain(
    monkeypatch, tmp_path: Path
) -> None:
    from tools import audit_recovery_control as audit

    lineage = audit._completion_lineage(
        issue_date="2026-08-02",
        run_intent="ScheduledProduction",
        run_id="run-1",
        artifact_root=tmp_path,
        ops_root=tmp_path,
    )
    completion = audit._sealed(
        {
            "schemaVersion": "SAME_DATE_COMPLETION_EVIDENCE_V1",
            "issuer": audit.VERIFIED_COMPLETION_ISSUER,
            "issueDate": "2026-08-02",
            "publishStatusIssueDate": "2026-08-02",
            "runIntent": "ScheduledProduction",
            "runId": "run-1",
            **lineage,
            "checks": {field: True for field in audit.COMPLETION_FIELDS},
            "evidenceSha256": {
                field: "a" * 64 for field in audit.COMPLETION_FIELDS
            },
        }
    )
    forged = audit._sealed(
        {
            "schemaVersion": "COMPLETION_AUTHORITY_V1",
            "issuer": audit.DECISION_ISSUER,
            "issueDate": "2026-08-02",
            "completionAuthorityId": "forged-authority",
            "completionEvidenceSha256": completion["receiptSha256"],
            "completionEvidence": completion,
            "firstVerifiedTerminal": "audit_normal_green",
            "decisionReceiptSha256": "b" * 64,
        }
    )
    monkeypatch.setattr(
        dsh,
        "verify_public_completion",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("public verifier boom")),
        raising=False,
    )
    monkeypatch.setattr(
        dsh,
        "verify_live_runner_readiness",
        lambda **_kwargs: {"ok": True, "reason": "", "failedGateIds": []},
    )

    result = dsh.evaluate_completion(
        repo_root=tmp_path,
        ops_repo_root=tmp_path,
        date="2026-08-02",
        remote="origin",
        branch="main",
        public_base_url="https://example.invalid/News-Grasp/",
        wait_sec=0,
        poll_sec=1,
        previous_public_authority=forged,
    )

    assert result["publicCompletionStatus"] == "unverified"
    assert result["completionAuthorityId"] == ""


def test_sec_red_causal_retry_requires_complete_hash_evidence() -> None:
    from tools import audit_recovery_control as audit

    previous = {
        "source_sha256": "a" * 64,
        "runtime_sha256": "b" * 64,
        "config_sha256": "c" * 64,
        "authority_sha256": "d" * 64,
        "external_evidence_sha256": "e" * 64,
    }
    current = {**previous, "source_sha256": "not-a-sha256"}

    with pytest.raises(ValueError, match="CAUSAL_RETRY_EVIDENCE_INVALID"):
        audit.causal_retry_gate(previous, current)


def test_ng_red_13_typed_completion_cli_returns_two_for_non_green(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    """verify-publish-complete はtyped non-GreenをCLI exit 2へ投影する。"""
    monkeypatch.setattr(
        dsh,
        "verify_publish_complete",
        lambda **_kwargs: {
            "ok": False,
            "status": "verified_incomplete",
            "verificationStatus": "verified_incomplete",
            "publicCompletionStatus": "green",
            "nextRunReadinessStatus": "red",
            "reasonCode": "RUNNER_READINESS_RED",
        },
    )

    result = dsh.main(
        [
            "verify-publish-complete",
            "--repo-root",
            str(tmp_path),
            "--date",
            "2026-08-10",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert output["status"] == "verified_incomplete", (
        "TYPED_COMPLETION_CLI_EXIT_CONTRACT_MISSING"
    )
    assert result == 2, "TYPED_COMPLETION_CLI_EXIT_CONTRACT_MISSING"


def test_readiness_freshness_marks_descriptor_drift_stale(tmp_path: Path) -> None:
    descriptor = tmp_path / "capability-v1.json"
    deadman = tmp_path / "news-grasp-deadman.ps1"
    descriptor.write_text("descriptor-v1\n", encoding="utf-8")
    deadman.write_text("deadman-v1\n", encoding="utf-8")
    proof = dsh.readiness_freshness_snapshot(
        generation_id="generation-001",
        descriptor_path=descriptor,
        task_definition="task-action-v1",
        deadman_path=deadman,
    )
    assert dsh.verify_readiness_freshness(
        proof,
        generation_id="generation-001",
        descriptor_path=descriptor,
        task_definition="task-action-v1",
        deadman_path=deadman,
    )["status"] == "ready"
    descriptor.write_text("descriptor-v2\n", encoding="utf-8")
    stale = dsh.verify_readiness_freshness(
        proof,
        generation_id="generation-001",
        descriptor_path=descriptor,
        task_definition="task-action-v1",
        deadman_path=deadman,
    )
    assert stale["status"] == "stale"
    assert stale["reasonCode"] == "readiness_proof_stale"


def test_parallel_hotfix_future_readiness_permit_date_is_not_future(
    monkeypatch, tmp_path: Path
) -> None:
    """未来のreadiness rootと当日JSTのpermit/log日付を分離する。"""
    future_date = "2026-08-25"
    current_date = "2026-08-21"

    def fake_task_details(**_kwargs):
        return {"ok": True, "next_run_time": f"{future_date}T06:00:00"}

    monkeypatch.setattr(dsh, "_scheduled_task_details", fake_task_details)
    assert dsh._next_scheduled_task_issue_date() == future_date
    captured: dict[str, list[str]] = {}

    def fake_run(command, **_kwargs):
        captured["command"] = list(command)
        state_file = Path(command[command.index("-StateFile") + 1])
        log_dir = Path(command[command.index("-LogDir") + 1])
        log_dir.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps({"status": "smoke_ok"}), encoding="utf-8")
        permit_date = command[command.index("-DateStamp") + 1]
        (log_dir / f"{permit_date}.log").write_text(
            "news-grasp-runner.ps1 SMOKE OK\n", encoding="utf-8"
        )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(dsh.subprocess, "run", fake_run)
    result = dsh._run_live_startup_canary(
        repo_root=tmp_path,
        startup_path=tmp_path / "bootstrap.ps1",
        date=future_date,
        permit_issue_date=current_date,
        powershell_exe="powershell.exe",
    )
    assert result["ok"] is True
    assert future_date in result["state_file"]
    assert result["log_file"].endswith(f"{current_date}.log")
    assert captured["command"][captured["command"].index("-DateStamp") + 1] == current_date
