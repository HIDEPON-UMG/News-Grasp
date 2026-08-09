#!/usr/bin/env python3
"""Daily runner diagnosis, alerting, and publish verification helpers."""
from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlencode, urljoin, urlparse

from tools import deepdive_quality
from tools.publish_inventory import required_distribution_artifacts


ALERT_STATUSES = {
    "content_failed",
    "exhausted",
    "failed",
    "fallback_ok",
    "no_run_detected",
    "publish_failed",
    "stale",
}

RUNNER_START_MINUTES = 6 * 60
BOOTSTRAP_START_MINUTES = 5 * 60 + 55


def _current_jst_date() -> str:
    """次回production readinessを検証する現在のJST日付を返す。"""
    return datetime.now(timezone(timedelta(hours=9))).date().isoformat()


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def compare_files(repo_path: Path, live_path: Path) -> dict:
    repo_exists = repo_path.exists()
    live_exists = live_path.exists()
    repo_sha = sha256_file(repo_path) if repo_exists else None
    live_sha = sha256_file(live_path) if live_exists else None
    return {
        "repo_path": str(repo_path),
        "live_path": str(live_path),
        "repo_exists": repo_exists,
        "live_exists": live_exists,
        "repo_sha256": repo_sha,
        "live_sha256": live_sha,
        "synced": bool(repo_exists and live_exists and repo_sha == live_sha),
    }


def _default_live_runner_path() -> Path:
    return Path.home() / "bin" / "news-grasp-runner.ps1"


def _default_live_watcher_path() -> Path:
    return Path.home() / "bin" / "watch-news-grasp-runner.ps1"


def _default_live_bootstrap_path() -> Path:
    return Path.home() / "bin" / "news-grasp-bootstrap.ps1"


def _default_live_task_launcher_path() -> Path:
    return Path.home() / "bin" / "news-grasp-task-launcher.pyw"


def _command_path_text(value: Path | str) -> str:
    return str(value).strip().strip('"').replace("/", "\\").lower()


def _safe_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _time_minutes_from_text(value: object) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    match = re.search(r"(?:T|\s)(\d{1,2}):(\d{2})(?::\d{2})?\s*([AP]M)?", text, re.IGNORECASE)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2))
    marker = (match.group(3) or "").upper()
    if marker == "PM" and hour < 12:
        hour += 12
    elif marker == "AM" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return hour * 60 + minute


def _next_run_time_matches(details: dict, expected_minutes: int) -> bool:
    return _time_minutes_from_text(details.get("next_run_time")) == expected_minutes


def _missed_runs_zero(details: dict) -> bool:
    return _safe_int(details.get("number_of_missed_runs")) == 0


def _action_has_switch(action_summary: str, switch: str) -> bool:
    return bool(re.search(rf"(?i)(?:^|\s){re.escape(switch)}(?:\s|$)", action_summary))


def _action_option_value(action_summary: str, option: str) -> str:
    match = re.search(
        rf"(?i)(?:^|\s){re.escape(option)}\s+(?:\"([^\"]+)\"|'([^']+)'|(\S+))",
        action_summary,
    )
    if not match:
        return ""
    return next((part for part in match.groups() if part), "")


def _action_option_int(action_summary: str, option: str) -> int | None:
    return _safe_int(_action_option_value(action_summary, option))


def _is_isolated_smoke_path(value: str, *, kind: str) -> bool:
    text = _command_path_text(value)
    if not text:
        return False
    if kind == "state":
        return "smoke" in text and not text.endswith("\\news-grasp-runner-state.json")
    return "smoke" in text and "news-grasp-logs" not in text


def _task_launcher_source_contract(path: Path) -> dict:
    try:
        source = path.read_text(encoding="utf-8-sig", errors="replace")
        compact = re.sub(r"\s+", "", source)
    except OSError:
        return {"ok": False, "reason": "task_launcher_unreadable"}
    required_tokens = (
        'choices=("runner","bootstrap")',
        '"news-grasp-bootstrap.ps1"',
        'ifargs.mode=="runner"',
        '"-Start"',
        '"-SmokeTest"',
        '"-PollSeconds","1"',
        '"-TimeoutMinutes","2"',
        '"-StateFile","ng-smoke-state.json"',
        '"-LogDir","ng-smoke-logs"',
        "subprocess.CREATE_NO_WINDOW",
    )
    missing = [token for token in required_tokens if token not in compact]
    try:
        tree = ast.parse(source)
    except SyntaxError:
        tree = None
    runner_args: list[str] = []
    bootstrap_args: list[str] = []
    if tree is not None:
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or not any(
                isinstance(target, ast.Name) and target.id == "extra"
                for target in node.targets
            ):
                continue
            if not isinstance(node.value, ast.IfExp):
                continue
            if isinstance(node.value.body, ast.List):
                runner_args = [
                    str(item.value)
                    for item in node.value.body.elts
                    if isinstance(item, ast.Constant) and isinstance(item.value, str)
                ]
            if isinstance(node.value.orelse, ast.List):
                bootstrap_args = [
                    str(item.value)
                    for item in node.value.orelse.elts
                    if isinstance(item, ast.Constant) and isinstance(item.value, str)
                ]
            break
    required_runner_args = [
        "-Start",
        "-UseProductionRuntime",
        "-ScheduledTaskName",
        "News-Grasp Runner",
    ]
    required_bootstrap_args = [
        "-Start",
        "-UseProductionRuntime",
        "-ScheduledTaskName",
        "News-Grasp Bootstrap",
        "-SmokeTest",
        "-SkipSourceSync",
    ]
    if not all(item in runner_args for item in required_runner_args):
        missing.append("runner_args:UseProductionRuntime+ScheduledTaskName")
    if not all(item in bootstrap_args for item in required_bootstrap_args):
        missing.append("bootstrap_args:UseProductionRuntime+ScheduledTaskName")
    return {
        "ok": not missing,
        "reason": "" if not missing else "task_launcher_contract_invalid",
        "missing_tokens": missing,
        "timeout_minutes": 2,
        "state_file": "ng-smoke-state.json",
        "log_dir": "ng-smoke-logs",
    }


def _task_launcher_action_mode(action_summary: str, *, launcher_path_text: str, mode: str) -> bool:
    action_text = _command_path_text(action_summary)
    return bool(
        launcher_path_text
        and launcher_path_text in action_text
        and re.search(rf"(?i)(?:^|\s){re.escape(mode)}(?:\s|$)", action_summary)
    )


def _bootstrap_action_smoke_contract(
    action_summary: str,
    *,
    bootstrap_path_text: str,
    watcher_text: str,
    launcher_path_text: str = "",
    launcher_contract: dict | None = None,
) -> dict:
    action_text = _command_path_text(action_summary)
    timeout_minutes = _action_option_int(action_summary, "-TimeoutMinutes")
    state_file = _action_option_value(action_summary, "-StateFile")
    log_dir = _action_option_value(action_summary, "-LogDir")
    targets_live_bootstrap = bool(bootstrap_path_text in action_text)
    targets_live_watcher = bool(watcher_text in action_text)
    targets_live_task_launcher = bool(launcher_path_text and launcher_path_text in action_text)
    launcher_mode_ok = _task_launcher_action_mode(
        action_summary,
        launcher_path_text=launcher_path_text,
        mode="bootstrap",
    )
    launcher_ok = bool(targets_live_task_launcher and launcher_mode_ok and (launcher_contract or {}).get("ok"))
    if launcher_ok:
        targets_live_bootstrap = True
        timeout_minutes = _safe_int((launcher_contract or {}).get("timeout_minutes"))
        state_file = str((launcher_contract or {}).get("state_file") or "")
        log_dir = str((launcher_contract or {}).get("log_dir") or "")
    return {
        "targets_live_bootstrap": targets_live_bootstrap,
        "targets_live_watcher": targets_live_watcher,
        "targets_live_task_launcher": targets_live_task_launcher,
        "task_launcher_mode_ok": launcher_mode_ok,
        "is_smoke_test": _action_has_switch(action_summary, "-SmokeTest") or launcher_ok,
        "uses_short_timeout": isinstance(timeout_minutes, int) and timeout_minutes <= 2,
        "uses_isolated_state_log": _is_isolated_smoke_path(state_file, kind="state")
        and _is_isolated_smoke_path(log_dir, kind="log"),
        "state_file": state_file,
        "log_dir": log_dir,
        "timeout_minutes": timeout_minutes,
    }


def _runner_action_start_contract(
    action_summary: str,
    *,
    targets_live_watcher: bool,
    targets_live_bootstrap: bool,
    targets_live_runner: bool,
    targets_live_task_launcher: bool = False,
) -> dict:
    forbidden_switches = [
        "-SmokeTest",
        "-SkipSourceSync",
        "-Status",
        "-StartOnly",
        "-PreflightOnly",
        "-RecoverOnly",
        "-NoPublish",
        "-NoPush",
        "-Stage2EditorSmokeOnly",
        "-StopAfterEditorStart",
        "-StopBeforeDeepDive",
        "-ResumeFromStage",
    ]
    found_forbidden = [switch for switch in forbidden_switches if _action_has_switch(action_summary, switch)]
    requires_start = bool((targets_live_watcher or targets_live_bootstrap) and not targets_live_task_launcher)
    has_start = _action_has_switch(action_summary, "-Start")
    targets_known_entrypoint = bool(
        targets_live_watcher or targets_live_bootstrap or targets_live_runner or targets_live_task_launcher
    )
    return {
        "is_production_start": bool(
            targets_known_entrypoint
            and not found_forbidden
            and ((not requires_start) or has_start)
        ),
        "requires_start": requires_start,
        "has_start": has_start,
        "forbidden_switches": found_forbidden,
    }


def _runner_has_pre_run_bootstrap_interlock(live_runner_path: Path) -> bool:
    try:
        text = live_runner_path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return False
    try:
        marker_body = text.split("function Test-PreRunBootstrapSmokeMarker", 1)[1].split(
            "function Assert-PreRunBootstrapInterlock",
            1,
        )[0]
        interlock_body = text.split("function Assert-PreRunBootstrapInterlock", 1)[1].split(
            "function Convert-JsonStringArrayToStringList",
            1,
        )[0]
        reexec_body = text.split("function Invoke-SyncedRunnerReexec", 1)[1].split(
            "function Assert-RunnerBinaryInSync",
            1,
        )[0]
        sync_body = text.split("function Assert-RunnerBinaryInSync", 1)[1].split(
            "function Invoke-Logged",
            1,
        )[0]
        start_block = text.split("# ===== sentinel: 起動できた事実 =====", 1)[1].split(
            "$IsE2EOrDryRun",
            1,
        )[0]
    except IndexError:
        return False
    required_marker_body = (
        "$BootstrapSmokeEarliestMinutes",
        "$BootstrapSmokeFreshnessMinutes",
        "updated_at",
        "LastWriteTime",
        "TotalMinutes",
    )
    required_interlock_body = (
        "Start-Process",
        "$bootstrapArgs",
        "-SmokeTest",
        "-PollSeconds",
        "1",
        "-TimeoutMinutes",
        "2",
        "-StateFile",
        "$BootstrapSmokeStateFile",
        "-LogDir",
        "$BootstrapSmokeLogDir",
        "blocked_startup_self_repair_failed",
    )
    required_reexec_body = (
        "NEWS_GRASP_RUNNER_SYNC_REEXEC",
        "Get-RunnerScriptArguments",
        "Start-Process",
        "-Wait",
        "runner binary drift repaired; relaunching synced runner",
        "exit $exitCode",
    )
    required_sync_body = (
        "Test-NormalDailyPublishRun",
        "Assert-PreRunBootstrapInterlock -ForceRepair",
        "Invoke-SyncedRunnerReexec",
        "Invoke-RunnerBinarySyncApprovalBlock",
        "blocked_startup_self_repair_failed",
    )
    return bool(
        all(marker in text for marker in ("ng-smoke-state.json", "ng-smoke-logs", "function Test-NormalDailyPublishRun"))
        and all(marker in marker_body for marker in required_marker_body)
        and all(marker in interlock_body for marker in required_interlock_body)
        and all(marker in reexec_body for marker in required_reexec_body)
        and all(marker in sync_body for marker in required_sync_body)
        and sync_body.index("Assert-PreRunBootstrapInterlock -ForceRepair") < sync_body.index("Invoke-SyncedRunnerReexec")
        and sync_body.index("Test-NormalDailyPublishRun") < sync_body.index("Invoke-RunnerBinarySyncApprovalBlock")
        and "Assert-PreRunBootstrapInterlock" in start_block
        and "Assert-RunnerBinaryInSync" in start_block
        and start_block.index("Assert-PreRunBootstrapInterlock") < start_block.index("Assert-RunnerBinaryInSync")
    )


def _legacy_direct_clean_runtime_contract(live_runner_path: Path, live_bootstrap_path: Path) -> bool:
    """旧direct Task actionがclean production runtimeへ必ず移譲する契約を検証する。"""
    try:
        runner = live_runner_path.read_text(encoding="utf-8-sig", errors="replace")
        bootstrap = live_bootstrap_path.read_text(encoding="utf-8-sig", errors="replace")
        trampoline = runner.split("function Invoke-LegacyScheduledProductionTrampoline", 1)[1].split(
            "Invoke-LegacyScheduledProductionTrampoline", 1
        )[0]
        runner_prefix = runner.split("$RepoDir   = Resolve-NewsGraspRepoDir", 1)[0]
        scheduled_context = bootstrap.split("function Assert-ScheduledTaskLaunchContext", 1)[1].split(
            "function Invoke-BoundedGitFetch", 1
        )[0]
    except (OSError, IndexError):
        return False
    runner_tokens = (
        "Get-ScheduledTask -TaskName 'News-Grasp Runner'",
        "Get-ScheduledTaskInfo -TaskName 'News-Grasp Runner'",
        "news-grasp-runner\\.ps1",
        "-UseProductionRuntime",
        "-ScheduledTaskName",
        "-LegacyDirectEntrypoint",
        "exit $exitCode",
    )
    bootstrap_tokens = (
        "[switch] $LegacyDirectEntrypoint",
        "[bool] $AllowLegacyDirectEntrypoint",
        "news-grasp-runner\\.ps1",
        "-AllowLegacyDirectEntrypoint ([bool]$LegacyDirectEntrypoint)",
    )
    return bool(
        all(token in trampoline for token in runner_tokens)
        and "-SmokeTest" not in trampoline
        and "Invoke-LegacyScheduledProductionTrampoline" in runner_prefix
        and all(token in bootstrap for token in bootstrap_tokens)
        and "$AllowLegacyDirectEntrypoint" in scheduled_context
    )


def live_runner_readiness_manifest_ok(readiness: dict) -> bool:
    """publish-complete 履歴から再利用できる live ops readiness の正本判定。"""
    if not isinstance(readiness, dict) or not readiness.get("ok"):
        return False
    repo_runner = readiness.get("repo_runner") if isinstance(readiness.get("repo_runner"), dict) else {}
    live_runner = readiness.get("live_runner") if isinstance(readiness.get("live_runner"), dict) else {}
    repo_watcher = readiness.get("repo_watcher") if isinstance(readiness.get("repo_watcher"), dict) else {}
    live_watcher = readiness.get("live_watcher") if isinstance(readiness.get("live_watcher"), dict) else {}
    repo_bootstrap = readiness.get("repo_bootstrap") if isinstance(readiness.get("repo_bootstrap"), dict) else {}
    live_bootstrap = readiness.get("live_bootstrap") if isinstance(readiness.get("live_bootstrap"), dict) else {}
    repo_task_launcher = (
        readiness.get("repo_task_launcher") if isinstance(readiness.get("repo_task_launcher"), dict) else {}
    )
    live_task_launcher = (
        readiness.get("live_task_launcher") if isinstance(readiness.get("live_task_launcher"), dict) else {}
    )
    scheduled_task = readiness.get("scheduled_task") if isinstance(readiness.get("scheduled_task"), dict) else {}
    canary = readiness.get("canary") if isinstance(readiness.get("canary"), dict) else {}
    repo_sha = str(repo_runner.get("sha256") or "")
    live_sha = str(live_runner.get("sha256") or "")
    repo_watcher_sha = str(repo_watcher.get("sha256") or "")
    live_watcher_sha = str(live_watcher.get("sha256") or "")
    repo_bootstrap_sha = str(repo_bootstrap.get("sha256") or "")
    live_bootstrap_sha = str(live_bootstrap.get("sha256") or "")
    repo_task_launcher_sha = str(repo_task_launcher.get("sha256") or "")
    live_task_launcher_sha = str(live_task_launcher.get("sha256") or "")
    runner_schedule_ok = bool(
        scheduled_task.get("ok") is True
        and str(scheduled_task.get("state") or "") in {"Ready", "Running"}
        and _safe_int(scheduled_task.get("trigger_start_minutes")) == RUNNER_START_MINUTES
        and _time_minutes_from_text(scheduled_task.get("next_run_time")) == RUNNER_START_MINUTES
        and _safe_int(scheduled_task.get("number_of_missed_runs")) == 0
    )
    bootstrap_contract_ok = bool(
        scheduled_task.get("bootstrap_targets_live_bootstrap") is True
        and scheduled_task.get("bootstrap_action_is_smoke_test") is True
        and scheduled_task.get("bootstrap_action_uses_short_timeout") is True
        and scheduled_task.get("bootstrap_action_uses_isolated_state_log") is True
        and str(scheduled_task.get("bootstrap_state") or "") in {"Ready", "Running"}
        and scheduled_task.get("bootstrap_last_task_result") == 0
        and _safe_int(scheduled_task.get("bootstrap_trigger_start_minutes")) == BOOTSTRAP_START_MINUTES
        and _time_minutes_from_text(scheduled_task.get("bootstrap_next_run_time")) == BOOTSTRAP_START_MINUTES
        and _safe_int(scheduled_task.get("bootstrap_number_of_missed_runs")) == 0
        and scheduled_task.get("bootstrap_before_runner") is True
        and scheduled_task.get("bootstrap_repairs_before_run") is True
    )
    launcher_runner_target_ok = bool(
        scheduled_task.get("runner_action_is_production_start") is True
        and bootstrap_contract_ok
        and scheduled_task.get("targets_live_task_launcher") is True
        and scheduled_task.get("task_launcher_mode_ok") is True
        and scheduled_task.get("task_launcher_ready") is True
    )
    legacy_runner_target_ok = bool(
        scheduled_task.get("runner_action_is_production_start") is True
        and bootstrap_contract_ok
        and scheduled_task.get("targets_live_runner") is True
        and scheduled_task.get("legacy_direct_clean_runtime_trampoline") is True
        and scheduled_task.get("bootstrap_targets_live_task_launcher") is True
        and scheduled_task.get("bootstrap_task_launcher_mode_ok") is True
        and scheduled_task.get("task_launcher_ready") is True
    )
    runner_target_ok = launcher_runner_target_ok or legacy_runner_target_ok
    return bool(
        repo_sha
        and live_sha
        and repo_sha == live_sha
        and repo_watcher_sha
        and live_watcher_sha
        and repo_watcher_sha == live_watcher_sha
        and repo_bootstrap_sha
        and live_bootstrap_sha
        and repo_bootstrap_sha == live_bootstrap_sha
        and repo_task_launcher_sha
        and live_task_launcher_sha
        and repo_task_launcher_sha == live_task_launcher_sha
        and runner_schedule_ok
        and runner_target_ok
        and canary.get("ok") is True
        and str(canary.get("status") or "") == "smoke_ok"
    )


def _scheduled_task_action_summary(
    *,
    task_name: str = "News-Grasp Production",
    powershell_exe: str = "powershell.exe",
) -> str:
    safe_task_name = task_name.replace("'", "''")
    command = (
        f"$task=Get-ScheduledTask -TaskName '{safe_task_name}' -ErrorAction Stop; "
        "(@($task.Actions) | ForEach-Object { "
        "(([string]$_.Execute + ' ' + [string]$_.Arguments).Trim()) "
        "}) -join ' ; '"
    )
    try:
        proc = subprocess.run(
            [powershell_exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"unavailable: {exc}"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        return f"unavailable: {detail or f'rc={proc.returncode}'}"
    return proc.stdout.strip()


def _scheduled_task_details(
    *,
    task_name: str = "News-Grasp Production",
    powershell_exe: str = "powershell.exe",
) -> dict:
    safe_task_name = task_name.replace("'", "''")
    command = (
        f"$task=Get-ScheduledTask -TaskName '{safe_task_name}' -ErrorAction Stop; "
        f"$info=Get-ScheduledTaskInfo -TaskName '{safe_task_name}' -ErrorAction Stop; "
        "$actions=(@($task.Actions) | ForEach-Object { "
        "(([string]$_.Execute + ' ' + [string]$_.Arguments).Trim()) "
        "}) -join ' ; '; "
        "$triggers=@($task.Triggers) | ForEach-Object { "
        "[ordered]@{ start_boundary=[string]$_.StartBoundary; enabled=[bool]$_.Enabled } "
        "}; "
        "[ordered]@{ "
        "ok=$true; "
        "task_name=[string]$task.TaskName; "
        "state=[string]$task.State; "
        "action_summary=$actions; "
        "triggers=$triggers; "
        "last_run_time=[string]$info.LastRunTime; "
        "last_task_result=[int]$info.LastTaskResult; "
        "next_run_time=[string]$info.NextRunTime; "
        "number_of_missed_runs=[int]$info.NumberOfMissedRuns "
        "} | ConvertTo-Json -Depth 8"
    )
    try:
        proc = subprocess.run(
            [powershell_exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "reason": f"unavailable: {exc}", "action_summary": f"unavailable: {exc}"}
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        reason = f"unavailable: {detail or f'rc={proc.returncode}'}"
        return {"ok": False, "reason": reason, "action_summary": reason}
    try:
        payload = json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError):
        return {"ok": False, "reason": "scheduled_task_json_invalid", "action_summary": proc.stdout.strip()}
    return payload if isinstance(payload, dict) else {"ok": False, "reason": "scheduled_task_json_not_object"}


def _trigger_start_minutes(details: dict) -> int | None:
    triggers = details.get("triggers")
    if isinstance(triggers, dict):
        triggers = [triggers]
    if not isinstance(triggers, list):
        return None
    minutes: list[int] = []
    for trigger in triggers:
        if not isinstance(trigger, dict):
            continue
        if trigger.get("enabled") is False:
            continue
        boundary = str(trigger.get("start_boundary") or "")
        match = re.search(r"T(\d{2}):(\d{2})(?::\d{2})?", boundary)
        if match:
            minutes.append(int(match.group(1)) * 60 + int(match.group(2)))
    return min(minutes) if minutes else None


def _run_live_startup_canary(
    *,
    repo_root: Path,
    startup_path: Path,
    date: str,
    live_runner_path: Path | None = None,
    timeout_sec: int = 60,
    powershell_exe: str = "powershell.exe",
) -> dict:
    canary_root = repo_root / "build" / "live-runner-canary" / date
    log_dir = canary_root / "logs"
    state_file = canary_root / "state.json"
    log_file = log_dir / f"{date}.log"
    canary_root.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    if state_file.exists():
        state_file.unlink()
    if log_file.exists():
        log_file.unlink()
    command = [
        powershell_exe,
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(startup_path),
        "-Start",
        "-SmokeTest",
        "-SkipSourceSync",
        "-PollSeconds",
        "1",
        "-StaleMinutes",
        "2",
        "-TimeoutMinutes",
        "2",
        "-DateStamp",
        date,
        "-LogDir",
        str(log_dir),
        "-StateFile",
        str(state_file),
    ]
    if live_runner_path is not None:
        command += ["-RunnerPath", str(live_runner_path), "-BinDir", str(live_runner_path.parent)]
    command += ["-RepoDir", str(repo_root)]
    try:
        proc = subprocess.run(
            command,
            cwd=repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "reason": "canary_timeout",
            "state_file": str(state_file),
            "log_file": str(log_file),
            "timeout_sec": timeout_sec,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "ok": False,
            "reason": "canary_launch_failed",
            "state_file": str(state_file),
            "log_file": str(log_file),
            "detail": str(exc),
        }
    state: dict = {}
    if state_file.exists():
        try:
            loaded = json.loads(state_file.read_text(encoding="utf-8-sig"))
            if isinstance(loaded, dict):
                state = loaded
        except (json.JSONDecodeError, OSError, UnicodeDecodeError, ValueError):
            state = {}
    log_text = ""
    if log_file.exists():
        try:
            log_text = log_file.read_text(encoding="utf-8-sig", errors="replace")
        except (OSError, UnicodeDecodeError):
            log_text = ""
    status = str(state.get("status") or "")
    log_smoke_ok = "news-grasp-runner.ps1 SMOKE OK" in log_text
    stderr_tail = proc.stderr[-2000:]
    if proc.returncode != 0:
        reason = "canary_failed"
    elif "CommandNotFoundException" in proc.stderr or "Get-FileHash" in proc.stderr:
        reason = "canary_stderr_error"
    elif status != "smoke_ok":
        reason = "canary_state_not_smoke_ok"
    elif not log_smoke_ok:
        reason = "canary_log_missing_smoke_ok"
    else:
        reason = ""
    return {
        "ok": reason == "",
        "reason": reason,
        "returncode": proc.returncode,
        "status": status,
        "state_file": str(state_file),
        "log_file": str(log_file),
        "log_smoke_ok": log_smoke_ok,
        "stdout": proc.stdout[-2000:],
        "stderr": stderr_tail,
    }


def verify_live_runner_readiness(
    *,
    repo_root: Path,
    ops_repo_root: Path | None = None,
    date: str,
    live_runner_path: Path | None = None,
    live_watcher_path: Path | None = None,
    live_bootstrap_path: Path | None = None,
    live_task_launcher_path: Path | None = None,
    task_name: str = "News-Grasp Production",
    bootstrap_task_name: str = "News-Grasp Bootstrap",
    run_canary: bool = True,
    canary_timeout_sec: int = 60,
    powershell_exe: str = "powershell.exe",
) -> dict:
    repo_root = repo_root.resolve()
    ops_repo_root = (ops_repo_root or repo_root).resolve()
    live_runner_path = live_runner_path or _default_live_runner_path()
    live_watcher_path = live_watcher_path or _default_live_watcher_path()
    live_bootstrap_path = live_bootstrap_path or _default_live_bootstrap_path()
    live_task_launcher_path = live_task_launcher_path or _default_live_task_launcher_path()
    repo_runner = ops_repo_root / "scripts" / "ops" / "news-grasp-runner.ps1"
    repo_watcher = ops_repo_root / "scripts" / "ops" / "watch-news-grasp-runner.ps1"
    repo_bootstrap = ops_repo_root / "scripts" / "ops" / "news-grasp-bootstrap.ps1"
    repo_task_launcher = ops_repo_root / "scripts" / "ops" / "news-grasp-task-launcher.pyw"
    runner_checksum = compare_files(repo_runner, live_runner_path)
    watcher_checksum = compare_files(repo_watcher, live_watcher_path)
    bootstrap_checksum = compare_files(repo_bootstrap, live_bootstrap_path)
    task_launcher_checksum = compare_files(repo_task_launcher, live_task_launcher_path)
    task_launcher_contract = _task_launcher_source_contract(live_task_launcher_path)
    result = {
        "ok": False,
        "reason": "",
        "status": "not_ready",
        "date": date,
        "artifact_repo_root": str(repo_root),
        "ops_repo_root": str(ops_repo_root),
        "repo_runner": {
            "path": str(repo_runner),
            "exists": runner_checksum["repo_exists"],
            "sha256": runner_checksum["repo_sha256"],
        },
        "live_runner": {
            "path": str(live_runner_path),
            "exists": runner_checksum["live_exists"],
            "sha256": runner_checksum["live_sha256"],
        },
        "repo_watcher": {
            "path": str(repo_watcher),
            "exists": watcher_checksum["repo_exists"],
            "sha256": watcher_checksum["repo_sha256"],
        },
        "live_watcher": {
            "path": str(live_watcher_path),
            "exists": watcher_checksum["live_exists"],
            "sha256": watcher_checksum["live_sha256"],
        },
        "repo_bootstrap": {
            "path": str(repo_bootstrap),
            "exists": bootstrap_checksum["repo_exists"],
            "sha256": bootstrap_checksum["repo_sha256"],
        },
        "live_bootstrap": {
            "path": str(live_bootstrap_path),
            "exists": bootstrap_checksum["live_exists"],
            "sha256": bootstrap_checksum["live_sha256"],
        },
        "repo_task_launcher": {
            "path": str(repo_task_launcher),
            "exists": task_launcher_checksum["repo_exists"],
            "sha256": task_launcher_checksum["repo_sha256"],
        },
        "live_task_launcher": {
            "path": str(live_task_launcher_path),
            "exists": task_launcher_checksum["live_exists"],
            "sha256": task_launcher_checksum["live_sha256"],
            "contract": task_launcher_contract,
        },
        "scheduled_task": {},
        "next_run_readiness": {"ok": False, "status": "not_ready"},
        "last_scheduled_attempt": {"status": "unknown", "last_task_result": None, "last_run_time": ""},
        "canary": {},
    }
    if not runner_checksum["repo_exists"]:
        return {**result, "reason": "repo_runner_missing"}
    if not runner_checksum["live_exists"]:
        return {**result, "reason": "live_runner_missing"}
    if not watcher_checksum["repo_exists"]:
        return {**result, "reason": "repo_watcher_missing"}
    if not watcher_checksum["live_exists"]:
        return {**result, "reason": "live_watcher_missing"}
    if not bootstrap_checksum["repo_exists"]:
        return {**result, "reason": "repo_bootstrap_missing"}
    if not bootstrap_checksum["live_exists"]:
        return {**result, "reason": "live_bootstrap_missing"}
    if not runner_checksum["synced"]:
        return {**result, "reason": "live_runner_hash_mismatch"}
    if not watcher_checksum["synced"]:
        return {**result, "reason": "live_watcher_hash_mismatch"}
    if not bootstrap_checksum["synced"]:
        return {**result, "reason": "live_bootstrap_hash_mismatch"}

    task_details = _scheduled_task_details(task_name=task_name, powershell_exe=powershell_exe)
    last_task_result = task_details.get("last_task_result")
    if last_task_result == 0:
        last_scheduled_status = "succeeded"
    elif last_task_result is None:
        last_scheduled_status = "unknown"
    else:
        last_scheduled_status = "failed"
    result["last_scheduled_attempt"] = {
        "status": last_scheduled_status,
        "last_task_result": last_task_result,
        "last_run_time": task_details.get("last_run_time") or "",
    }
    action_summary = str(task_details.get("action_summary") or "")
    action_text = _command_path_text(action_summary)
    watcher_text = _command_path_text(live_watcher_path)
    runner_text = _command_path_text(live_runner_path)
    bootstrap_path_text = _command_path_text(live_bootstrap_path)
    task_launcher_path_text = _command_path_text(live_task_launcher_path)
    runner_targets_watcher = bool(action_summary and not action_summary.startswith("unavailable:") and watcher_text in action_text)
    runner_targets_runner = bool(action_summary and not action_summary.startswith("unavailable:") and runner_text in action_text)
    runner_targets_bootstrap = bool(
        action_summary and not action_summary.startswith("unavailable:") and bootstrap_path_text in action_text
    )
    runner_targets_task_launcher = bool(
        action_summary and not action_summary.startswith("unavailable:") and task_launcher_path_text in action_text
    )
    runner_task_launcher_mode_ok = _task_launcher_action_mode(
        action_summary,
        launcher_path_text=task_launcher_path_text,
        mode="runner",
    )
    task_launcher_ready = bool(task_launcher_checksum["synced"] and task_launcher_contract.get("ok"))
    runner_action_contract = _runner_action_start_contract(
        action_summary,
        targets_live_watcher=runner_targets_watcher,
        targets_live_bootstrap=runner_targets_bootstrap,
        targets_live_runner=runner_targets_runner,
        targets_live_task_launcher=bool(
            runner_targets_task_launcher and runner_task_launcher_mode_ok and task_launcher_ready
        ),
    )
    direct_runner_pre_run_interlock = _runner_has_pre_run_bootstrap_interlock(live_runner_path)
    direct_runner_pre_run_reexec = direct_runner_pre_run_interlock
    legacy_direct_clean_runtime_trampoline = _legacy_direct_clean_runtime_contract(
        live_runner_path, live_bootstrap_path
    )
    bootstrap_details = _scheduled_task_details(task_name=bootstrap_task_name, powershell_exe=powershell_exe)
    bootstrap_summary = str(bootstrap_details.get("action_summary") or "")
    bootstrap_text = _command_path_text(bootstrap_summary)
    bootstrap_action_contract = _bootstrap_action_smoke_contract(
        bootstrap_summary,
        bootstrap_path_text=bootstrap_path_text,
        watcher_text=watcher_text,
        launcher_path_text=task_launcher_path_text,
        launcher_contract=task_launcher_contract if task_launcher_checksum["synced"] else {"ok": False},
    )
    bootstrap_targets_watcher = bool(
        bootstrap_summary
        and not bootstrap_summary.startswith("unavailable:")
        and (watcher_text in bootstrap_text or bootstrap_path_text in bootstrap_text)
    )
    bootstrap_targets_task_launcher = bool(
        bootstrap_summary
        and not bootstrap_summary.startswith("unavailable:")
        and task_launcher_path_text in bootstrap_text
    )
    runner_state_ok = str(task_details.get("state") or "") in {"Ready", "Running"}
    bootstrap_state_ok = str(bootstrap_details.get("state") or "") in {"Ready", "Running"}
    bootstrap_last_result_ok = bootstrap_details.get("last_task_result") == 0
    runner_start = _trigger_start_minutes(task_details)
    bootstrap_start = _trigger_start_minutes(bootstrap_details)
    runner_trigger_ok = runner_start == RUNNER_START_MINUTES
    runner_next_run_ok = _next_run_time_matches(task_details, RUNNER_START_MINUTES)
    runner_missed_runs_ok = _missed_runs_zero(task_details)
    bootstrap_trigger_ok = bootstrap_start == BOOTSTRAP_START_MINUTES
    bootstrap_next_run_ok = _next_run_time_matches(bootstrap_details, BOOTSTRAP_START_MINUTES)
    bootstrap_missed_runs_ok = _missed_runs_zero(bootstrap_details)
    bootstrap_smoke_contract_ok = bool(
        bootstrap_action_contract["targets_live_bootstrap"]
        and bootstrap_action_contract["is_smoke_test"]
        and bootstrap_action_contract["uses_short_timeout"]
        and bootstrap_action_contract["uses_isolated_state_log"]
    )
    bootstrap_before_runner = (
        isinstance(bootstrap_start, int)
        and isinstance(runner_start, int)
        and bootstrap_start < runner_start
    )
    bootstrap_pre_run_ok = bool(
        bootstrap_smoke_contract_ok
        and bootstrap_state_ok
        and bootstrap_last_result_ok
        and bootstrap_trigger_ok
        and bootstrap_next_run_ok
        and bootstrap_missed_runs_ok
        and bootstrap_before_runner
    )
    runner_schedule_ok = bool(
        runner_state_ok
        and runner_trigger_ok
        and runner_next_run_ok
        and runner_missed_runs_ok
    )
    launcher_runner_ready = bool(
        runner_targets_task_launcher and runner_task_launcher_mode_ok and task_launcher_ready
    )
    legacy_direct_runner_ready = bool(
        runner_targets_runner
        and legacy_direct_clean_runtime_trampoline
        and bootstrap_targets_task_launcher
        and bootstrap_action_contract["task_launcher_mode_ok"]
        and task_launcher_ready
    )
    task_ok = bool(
        runner_schedule_ok
        and runner_action_contract["is_production_start"]
        and bootstrap_pre_run_ok
        and (launcher_runner_ready or legacy_direct_runner_ready)
    )
    result["scheduled_task"] = {
        "ok": task_ok,
        "task_name": task_name,
        "action_summary": action_summary,
        "state": task_details.get("state"),
        "next_run_time": task_details.get("next_run_time"),
        "last_run_time": task_details.get("last_run_time"),
        "last_task_result": task_details.get("last_task_result"),
        "number_of_missed_runs": task_details.get("number_of_missed_runs"),
        "trigger_start_minutes": runner_start,
        "trigger_is_daily_0600": runner_trigger_ok,
        "next_run_time_is_0600": runner_next_run_ok,
        "number_of_missed_runs_ok": runner_missed_runs_ok,
        "runner_action_is_production_start": runner_action_contract["is_production_start"],
        "runner_action_requires_start": runner_action_contract["requires_start"],
        "runner_action_has_start": runner_action_contract["has_start"],
        "runner_action_forbidden_switches": runner_action_contract["forbidden_switches"],
        "targets_live_watcher": runner_targets_watcher,
        "targets_live_runner": runner_targets_runner,
        "targets_live_bootstrap": runner_targets_bootstrap,
        "targets_live_task_launcher": runner_targets_task_launcher,
        "task_launcher_mode_ok": runner_task_launcher_mode_ok,
        "task_launcher_ready": task_launcher_ready,
        "direct_runner_pre_run_interlock": direct_runner_pre_run_interlock,
        "direct_runner_pre_run_reexec": direct_runner_pre_run_reexec,
        "legacy_direct_clean_runtime_trampoline": legacy_direct_clean_runtime_trampoline,
        "bootstrap_task_name": bootstrap_task_name,
        "bootstrap_action_summary": bootstrap_summary,
        "bootstrap_state": bootstrap_details.get("state"),
        "bootstrap_next_run_time": bootstrap_details.get("next_run_time"),
        "bootstrap_last_run_time": bootstrap_details.get("last_run_time"),
        "bootstrap_last_task_result": bootstrap_details.get("last_task_result"),
        "bootstrap_number_of_missed_runs": bootstrap_details.get("number_of_missed_runs"),
        "bootstrap_trigger_start_minutes": bootstrap_start,
        "bootstrap_trigger_is_0555": bootstrap_trigger_ok,
        "bootstrap_next_run_time_is_0555": bootstrap_next_run_ok,
        "bootstrap_number_of_missed_runs_ok": bootstrap_missed_runs_ok,
        "bootstrap_targets_watcher_or_bootstrap": bootstrap_targets_watcher,
        "bootstrap_targets_live_bootstrap": bootstrap_action_contract["targets_live_bootstrap"],
        "bootstrap_targets_live_watcher": bootstrap_action_contract["targets_live_watcher"],
        "bootstrap_targets_live_task_launcher": bootstrap_targets_task_launcher,
        "bootstrap_task_launcher_mode_ok": bootstrap_action_contract["task_launcher_mode_ok"],
        "bootstrap_action_is_smoke_test": bootstrap_action_contract["is_smoke_test"],
        "bootstrap_action_uses_short_timeout": bootstrap_action_contract["uses_short_timeout"],
        "bootstrap_action_uses_isolated_state_log": bootstrap_action_contract["uses_isolated_state_log"],
        "bootstrap_action_state_file": bootstrap_action_contract["state_file"],
        "bootstrap_action_log_dir": bootstrap_action_contract["log_dir"],
        "bootstrap_action_timeout_minutes": bootstrap_action_contract["timeout_minutes"],
        "bootstrap_before_runner": bootstrap_before_runner,
        "bootstrap_repairs_before_run": bootstrap_pre_run_ok,
    }
    if not task_ok:
        if not task_details.get("ok"):
            reason = "scheduled_task_unavailable"
        elif not runner_state_ok:
            reason = "scheduled_task_disabled"
        elif not runner_trigger_ok:
            reason = "scheduled_task_not_0600"
        elif not runner_next_run_ok:
            reason = "scheduled_task_next_run_missing"
        elif not runner_missed_runs_ok:
            reason = "scheduled_task_missed_runs"
        elif not (runner_targets_watcher or runner_targets_bootstrap or runner_targets_runner or runner_targets_task_launcher):
            reason = "scheduled_task_target_mismatch"
        elif not runner_action_contract["is_production_start"]:
            reason = "scheduled_task_action_not_production_start"
        elif runner_targets_task_launcher and not task_launcher_checksum["synced"]:
            reason = "task_launcher_hash_mismatch"
        elif runner_targets_task_launcher and not task_launcher_contract.get("ok"):
            reason = "task_launcher_contract_invalid"
        elif runner_targets_task_launcher and not runner_task_launcher_mode_ok:
            reason = "task_launcher_runner_mode_invalid"
        elif bootstrap_targets_task_launcher and not task_launcher_checksum["synced"]:
            reason = "bootstrap_task_launcher_hash_mismatch"
        elif bootstrap_targets_task_launcher and not task_launcher_contract.get("ok"):
            reason = "bootstrap_task_launcher_contract_invalid"
        elif bootstrap_targets_task_launcher and not bootstrap_action_contract["task_launcher_mode_ok"]:
            reason = "bootstrap_task_launcher_mode_invalid"
        elif runner_targets_runner and not direct_runner_pre_run_interlock:
            reason = "direct_runner_pre_run_interlock_missing"
        elif runner_targets_runner and not direct_runner_pre_run_reexec:
            reason = "direct_runner_pre_run_reexec_missing"
        elif runner_targets_runner and not bootstrap_action_contract["targets_live_bootstrap"]:
            reason = "bootstrap_task_target_mismatch"
        elif runner_targets_runner and not bootstrap_smoke_contract_ok:
            reason = "bootstrap_task_smoke_contract_invalid"
        elif runner_targets_runner and not bootstrap_state_ok:
            reason = "bootstrap_task_disabled"
        elif runner_targets_runner and not bootstrap_trigger_ok:
            reason = "bootstrap_task_not_0555"
        elif runner_targets_runner and not bootstrap_next_run_ok:
            reason = "bootstrap_task_next_run_missing"
        elif runner_targets_runner and not bootstrap_missed_runs_ok:
            reason = "bootstrap_task_missed_runs"
        elif runner_targets_runner and not bootstrap_last_result_ok:
            reason = "bootstrap_task_last_result_not_ok"
        elif runner_targets_runner and not bootstrap_before_runner:
            reason = "bootstrap_task_not_before_runner"
        elif not runner_targets_task_launcher:
            reason = "scheduled_task_launcher_required"
        else:
            reason = "scheduled_task_target_mismatch"
        return {**result, "reason": reason}

    if run_canary:
        canary = _run_live_startup_canary(
            repo_root=ops_repo_root,
            startup_path=live_bootstrap_path
            if (
                runner_targets_bootstrap
                or runner_targets_task_launcher
                or (runner_targets_runner and bootstrap_action_contract["targets_live_bootstrap"])
            )
            else live_watcher_path,
            live_runner_path=live_runner_path,
            date=date,
            timeout_sec=canary_timeout_sec,
            powershell_exe=powershell_exe,
        )
        result["canary"] = canary
        if not canary.get("ok"):
            return {**result, "reason": str(canary.get("reason") or "canary_failed")}
    else:
        result["canary"] = {"ok": True, "skipped": True}
    ready_status = "ready" if last_scheduled_status == "succeeded" else f"ready_with_{last_scheduled_status}_last_schedule"
    result["next_run_readiness"] = {
        "ok": True,
        "status": "ready",
        "next_run_time": task_details.get("next_run_time"),
        "number_of_missed_runs": task_details.get("number_of_missed_runs"),
        "canary_status": result["canary"].get("status", "skipped"),
    }
    return {**result, "ok": True, "reason": "", "status": ready_status}


def normalize_failure_signature(
    *, gate_id: str, error_code: str, artifact_identity: str = "", url_or_category: str = ""
) -> str:
    host_or_category = url_or_category.strip().lower()
    if "://" in host_or_category:
        host_or_category = urlparse(host_or_category).netloc.lower()
    parts = [
        gate_id.strip().lower(),
        error_code.strip().lower(),
        artifact_identity.strip().lower(),
        host_or_category,
    ]
    return "|".join(p or "-" for p in parts)


def classify_phase0(snapshot: dict) -> dict:
    scheduler = snapshot.get("scheduler") or snapshot.get("scheduled_task") or {}
    state = snapshot.get("state") or snapshot.get("runner") or {}
    repo_bin = snapshot.get("repo_bin") or snapshot.get("bin") or {}
    git = snapshot.get("git") or {}
    pages = snapshot.get("pages") or {}
    logs = snapshot.get("logs") or {}
    content = snapshot.get("content") or {}
    expected_date = snapshot.get("expected_date")
    last_result = scheduler.get("last_result", scheduler.get("last_task_result"))

    if not scheduler.get("exists", True):
        return {"root_cause": "scheduled_task_missing", "layer": "scheduler"}
    if scheduler.get("last_run_missing") or scheduler.get("days_since_last_run", 0) >= 1:
        return {"root_cause": "no_run_detected", "layer": "scheduler"}
    if not logs.get("runner_invoked", True):
        return {"root_cause": "runner_not_started", "layer": "runner"}
    if repo_bin and repo_bin.get("synced") is False:
        return {"root_cause": "bin_drift", "layer": "runner_sync"}
    if state.get("status") == "running" and (
        state.get("process_alive") is False
        or (expected_date and state.get("date") and state.get("date") != expected_date)
    ):
        return {"root_cause": "stale_runner", "layer": "watcher"}
    if git.get("dirty_required_files"):
        return {"root_cause": "uncommitted_required_changes", "layer": "git"}
    if git.get("local_head") and git.get("remote_head") and git["local_head"] != git["remote_head"]:
        return {"root_cause": "push_not_reflected", "layer": "git"}
    if git.get("push_failed"):
        return {"root_cause": "push_failed", "layer": "git"}
    if pages.get("deployment_success") is False or pages.get("public_sentinel_ok") is False:
        return {"root_cause": "pages_not_reflected", "layer": "pages"}
    if content.get("gate_failed"):
        return {
            "root_cause": "content_gate_failed",
            "layer": "content",
            "gate_id": content.get("gate_id", ""),
        }
    if last_result not in (None, 0):
        return {"root_cause": "runner_failed", "layer": "runner"}
    return {"root_cause": "no_issue_detected", "layer": "none"}


def evaluate_deadman(
    *,
    state: dict | None,
    now: datetime,
    expected_date: str,
    max_ok_age_hours: int,
) -> dict:
    state = state or {}
    status = str(state.get("status") or "no_run_detected")
    updated = _parse_dt(state.get("updated_at"))
    state_date = str(state.get("date") or "")

    if status in ALERT_STATUSES:
        return {"alert": True, "reason": status, "status": status}
    if status != "ok":
        return {"alert": True, "reason": "no_ok_state", "status": status}
    if state_date != expected_date:
        return {"alert": True, "reason": "ok_not_for_expected_date", "status": status}
    if updated is None:
        return {"alert": True, "reason": "ok_without_timestamp", "status": status}
    if now - updated > timedelta(hours=max_ok_age_hours):
        return {"alert": True, "reason": "ok_too_old", "status": status}
    return {"alert": False, "reason": "", "status": status}


def emit_alert(record: dict, *, alert_log: Path, marker_path: Path, webhook_url: str = "") -> dict:
    alert_log.parent.mkdir(parents=True, exist_ok=True)
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    key = f"{record.get('date','')}|{record.get('reason','')}|{record.get('status','')}"
    if marker_path.exists():
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            marker = {}
        if marker.get("key") == key:
            return {"sent": False, "duplicate": True, "key": key}

    payload = {**record, "key": key, "alerted_at": datetime.now(timezone.utc).isoformat()}
    with alert_log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    marker_path.write_text(json.dumps({"key": key}, ensure_ascii=False, indent=2), encoding="utf-8")

    if webhook_url:
        data = json.dumps({"text": f"News-Grasp daily alert: {key}"}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as res:  # noqa: S310 - operator configured URL
            res.read()
    return {"sent": True, "duplicate": False, "key": key}


def _git_output(repo_root: Path, args: list[str]) -> str:
    cp = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if cp.returncode != 0:
        raise RuntimeError((cp.stderr or cp.stdout).strip())
    return cp.stdout.strip()


def _is_git_worktree(repo_root: Path) -> bool:
    return (repo_root / ".git").exists()


def _git_tree_has_path(repo_root: Path, commit: str, rel_path: str) -> bool | None:
    if not _is_git_worktree(repo_root):
        return None
    try:
        _git_output(repo_root, ["cat-file", "-e", f"{commit}:{rel_path}"])
    except RuntimeError:
        return False
    return True


def _commit_is_ancestor(repo_root: Path, ancestor: str, descendant: str) -> bool:
    if ancestor == descendant:
        return True
    if not _is_git_worktree(repo_root):
        return False
    try:
        _git_output(repo_root, ["merge-base", "--is-ancestor", ancestor, descendant])
    except RuntimeError:
        return False
    return True


def _latest_audio_for_publish(repo_root: Path, date: str) -> dict[str, str] | None:
    latest_path = repo_root / "build" / "tts" / "latest_audio.json"
    if not latest_path.exists():
        return None
    try:
        data = json.loads(latest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if data.get("latest_audio_date") != date:
        return None
    url = str(data.get("latest_audio_url") or "")
    if not url:
        return {"latest_audio_date": date, "latest_audio_url": ""}
    return {"latest_audio_date": date, "latest_audio_url": url}


def _fetch_text(url: str) -> str:
    with urllib.request.urlopen(url, timeout=20) as res:  # noqa: S310 - fixed public URL from runner config
        return res.read().decode("utf-8-sig", errors="replace")


def _extract_sw_version(text: str) -> str:
    match = re.search(r"SW_VERSION\s*=\s*['\"]([^'\"]+)['\"]", text)
    return match.group(1).strip() if match else ""


def verify_public_sw_version(*, repo_root: Path, public_base_url: str) -> dict:
    local_sw = repo_root / "docs" / "sw.js"
    if not local_sw.exists():
        return {"ok": False, "reason": "local_sw_missing", "path": str(local_sw)}
    try:
        local_version = _extract_sw_version(local_sw.read_text(encoding="utf-8-sig"))
    except UnicodeDecodeError as exc:
        return {"ok": False, "reason": "local_sw_unreadable", "detail": str(exc), "path": str(local_sw)}
    if not local_version:
        return {"ok": False, "reason": "local_sw_version_missing", "path": str(local_sw)}

    public_sw_url = urljoin(public_base_url.rstrip("/") + "/", "sw.js")
    try:
        public_version = _extract_sw_version(_fetch_text(public_sw_url))
    except (urllib.error.URLError, TimeoutError, UnicodeDecodeError) as exc:
        return {
            "ok": False,
            "reason": "public_sw_fetch_failed",
            "detail": str(exc),
            "url": public_sw_url,
            "local_sw_version": local_version,
        }
    if not public_version:
        return {
            "ok": False,
            "reason": "public_sw_version_missing",
            "url": public_sw_url,
            "local_sw_version": local_version,
        }
    if public_version != local_version:
        return {
            "ok": False,
            "reason": "sw_version_mismatch",
            "url": public_sw_url,
            "local_sw_version": local_version,
            "public_sw_version": public_version,
        }
    return {
        "ok": True,
        "reason": "",
        "url": public_sw_url,
        "local_sw_version": local_version,
        "public_sw_version": public_version,
    }


def _url_head_ok(url: str) -> bool:
    req = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(req, timeout=20) as res:  # noqa: S310 - fixed public URL from runner config
        return int(getattr(res, "status", 200)) == 200


def verify_public_audio(*, repo_root: Path, date: str, public_base_url: str) -> dict:
    latest = _latest_audio_for_publish(repo_root, date)
    if latest is None:
        return {"checked": False, "ok": True, "reason": "no_audio_for_date"}
    audio_url = latest.get("latest_audio_url", "")
    if not audio_url:
        return {"checked": True, "ok": False, "reason": "audio_url_missing", "latest_audio_date": date}
    try:
        if not _url_head_ok(audio_url):
            return {"checked": True, "ok": False, "reason": "audio_url_not_200", "latest_audio_url": audio_url}
        base = public_base_url.rstrip("/") + "/"
        pages = {
            "home": base,
            "summary": urljoin(base, f"{date}/summary/"),
        }
        missing_from = [
            name
            for name, url in pages.items()
            if audio_url not in _fetch_text(url)
        ]
    except (urllib.error.URLError, TimeoutError, UnicodeDecodeError) as exc:
        return {
            "checked": True,
            "ok": False,
            "reason": "public_audio_verification_failed",
            "detail": str(exc),
            "latest_audio_url": audio_url,
        }
    if missing_from:
        return {
            "checked": True,
            "ok": False,
            "reason": "public_audio_missing",
            "missing_from": missing_from,
            "latest_audio_url": audio_url,
        }
    return {"checked": True, "ok": True, "latest_audio_url": audio_url}


def _load_podcast_row(state_path: Path, date: str) -> dict:
    if not state_path.exists():
        return {}
    try:
        data = json.loads(state_path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"_state_error": "podcast_state_corrupt"}
    row = data.get(date)
    return row if isinstance(row, dict) else {}


def _fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=20) as res:  # noqa: S310 - fixed public URL
        return json.loads(res.read().decode("utf-8-sig"))


def _title_from_watch_html(html: str, expected: str) -> str:
    match = re.search(r"<title>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    if match:
        title = re.sub(r"\s+", " ", match.group(1)).strip()
        suffix = " - YouTube"
        if title.endswith(suffix):
            title = title[: -len(suffix)].strip()
        if title:
            return title
    if expected in html:
        return expected
    return ""


def verify_podcast(
    *,
    date: str,
    state_path: Path,
    wait_sec: int = 0,
    poll_sec: int = 30,
    expected_title: str | None = None,
) -> dict:
    expected = expected_title or f"News-Grasp Daily News Briefing {date}"
    deadline = time.monotonic() + max(0, wait_sec)
    last: dict = {}
    while True:
        row = _load_podcast_row(state_path, date)
        if row.get("_state_error"):
            return {"ok": False, "reason": row["_state_error"], "state": str(state_path)}
        video_id = str(row.get("videoId") or "")
        playlist_id = str(row.get("playlistId") or "")
        status = str(row.get("status") or row.get("privacyStatus") or "")
        if not video_id:
            return {"ok": False, "reason": "public_podcast_missing", "state": str(state_path)}
        if status and status != "public":
            last = {"ok": False, "reason": "podcast_pending", "videoId": video_id, "status": status}
        else:
            try:
                watch_url = f"https://www.youtube.com/watch?v={quote(video_id)}"
                oembed_url = f"https://www.youtube.com/oembed?url={quote(watch_url, safe='')}&format=json"
                verification = "oembed_watch_playlist"
                try:
                    oembed = _fetch_json(oembed_url)
                    actual_title = str(oembed.get("title") or "")
                    if actual_title != expected:
                        return {
                            "ok": False,
                            "reason": "podcast_title_mismatch",
                            "videoId": video_id,
                            "expected_title": expected,
                            "actual_title": actual_title,
                        }
                except urllib.error.HTTPError as exc:
                    if exc.code != 401:
                        raise
                    actual_title = ""
                    verification = "watch_playlist_fallback"
                watch_html = _fetch_text(watch_url)
                if not actual_title:
                    actual_title = _title_from_watch_html(watch_html, expected)
                if expected not in watch_html and video_id not in watch_html:
                    last = {"ok": False, "reason": "podcast_watch_missing", "videoId": video_id}
                elif actual_title and actual_title != expected:
                    return {
                        "ok": False,
                        "reason": "podcast_title_mismatch",
                        "videoId": video_id,
                        "expected_title": expected,
                        "actual_title": actual_title,
                    }
                elif playlist_id:
                    playlist_url = f"https://www.youtube.com/playlist?list={quote(playlist_id)}"
                    playlist_html = _fetch_text(playlist_url)
                    if video_id not in playlist_html:
                        last = {
                            "ok": False,
                            "reason": "podcast_playlist_missing",
                            "videoId": video_id,
                            "playlistId": playlist_id,
                        }
                    else:
                        primary_playlist_id = str(row.get("primaryPodcastPlaylistId") or "")
                        if primary_playlist_id and primary_playlist_id != playlist_id:
                            primary_playlist_url = f"https://www.youtube.com/playlist?list={quote(primary_playlist_id)}"
                            primary_playlist_html = _fetch_text(primary_playlist_url)
                            if video_id not in primary_playlist_html:
                                last = {
                                    "ok": False,
                                    "reason": "primary_podcast_playlist_missing",
                                    "videoId": video_id,
                                    "playlistId": primary_playlist_id,
                                }
                                if time.monotonic() >= deadline:
                                    return last
                                time.sleep(max(1, poll_sec))
                                continue
                        return {
                            "ok": True,
                            "reason": "",
                            "videoId": video_id,
                            "playlistId": playlist_id,
                            "primaryPodcastPlaylistId": primary_playlist_id,
                            "title": actual_title,
                            "verification": verification,
                        }
                else:
                    return {
                        "ok": False,
                        "reason": "podcast_playlist_missing",
                        "videoId": video_id,
                        "playlistId": "",
                    }
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                last = {"ok": False, "reason": "podcast_pending", "videoId": video_id, "detail": str(exc)}
        if time.monotonic() >= deadline:
            return last or {"ok": False, "reason": "public_podcast_missing", "state": str(state_path)}
        time.sleep(max(1, poll_sec))


def _repo_slug_from_remote_url(remote_url: str) -> tuple[str, str] | None:
    value = remote_url.strip().rstrip("/")
    if value.endswith(".git"):
        value = value[:-4]
    match = re.fullmatch(r"https://github\.com/([^/\s]+)/([^/\s]+)", value)
    if not match:
        match = re.fullmatch(r"git@github\.com:([^/\s]+)/([^/\s]+)", value)
    if not match:
        return None
    owner, repo = match.group(1), match.group(2)
    if not owner or not repo:
        return None
    return owner, repo


def _gh_auth_token() -> str:
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        token = os.environ.get(name, "").strip()
        if token:
            return token
    try:
        proc = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def _github_headers(token: str = "") -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2026-03-10",
        "User-Agent": "News-Grasp-PublishVerifier/1.0",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _github_api_json(url: str) -> dict:
    token = os.environ.get("GITHUB_TOKEN", "").strip() or os.environ.get("GH_TOKEN", "").strip()
    tried_auth = bool(token)
    try:
        req = urllib.request.Request(url, headers=_github_headers(token))
        with urllib.request.urlopen(req, timeout=10) as res:  # noqa: S310 - fixed GitHub API URL derived from origin
            payload = json.loads(res.read().decode("utf-8-sig"))
    except urllib.error.HTTPError as exc:
        if exc.code not in {401, 403, 404} or tried_auth:
            raise
        token = _gh_auth_token()
        if not token:
            raise
        req = urllib.request.Request(url, headers=_github_headers(token))
        with urllib.request.urlopen(req, timeout=10) as res:  # noqa: S310 - fixed GitHub API URL derived from origin
            payload = json.loads(res.read().decode("utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("response_not_object")
    return payload


def _github_api_post(url: str, payload: dict) -> int:
    token = os.environ.get("GITHUB_TOKEN", "").strip() or os.environ.get("GH_TOKEN", "").strip()
    tried_auth = bool(token)
    data = json.dumps(payload).encode("utf-8")
    headers = {**_github_headers(token), "Content-Type": "application/json"}
    try:
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10) as res:  # noqa: S310 - fixed GitHub API URL derived from origin
            return int(getattr(res, "status", 204))
    except urllib.error.HTTPError as exc:
        if exc.code not in {401, 403, 404} or tried_auth:
            raise
        token = _gh_auth_token()
        if not token:
            raise
        headers = {**_github_headers(token), "Content-Type": "application/json"}
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10) as res:  # noqa: S310 - fixed GitHub API URL derived from origin
            return int(getattr(res, "status", 204))


def _verify_workflow_pages_status(*, owner: str, repo: str, branch: str, expected_commit: str, latest_detail: str = "") -> dict:
    url = f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}/pages"
    try:
        pages = _github_api_json(url)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        return {"ok": False, "reason": "pages_build_unavailable", "url": url, "detail": str(exc), "latest_detail": latest_detail}
    status = str(pages.get("status") or "")
    build_type = str(pages.get("build_type") or "")
    source = pages.get("source") if isinstance(pages.get("source"), dict) else {}
    source_branch = str(source.get("branch") or "")
    source_path = str(source.get("path") or "")
    if build_type != "workflow":
        return {
            "ok": False,
            "reason": "pages_build_unavailable",
            "url": url,
            "status": status,
            "build_type": build_type,
            "detail": "pages_build_latest_unavailable_for_non_workflow",
            "latest_detail": latest_detail,
        }
    if status != "built":
        return {
            "ok": False,
            "reason": "pages_build_not_built",
            "url": url,
            "status": status,
            "build_type": build_type,
            "source": source,
            "latest_detail": latest_detail,
        }
    if source_branch and source_branch != branch:
        return {
            "ok": False,
            "reason": "pages_build_commit_mismatch",
            "url": url,
            "status": status,
            "build_type": build_type,
            "source": source,
            "expected_branch": branch,
        }
    return {
        "ok": True,
        "reason": "",
        "status": status,
        "commit": expected_commit,
        "url": url,
        "build_type": build_type,
        "source_branch": source_branch,
        "source_path": source_path,
        "latest_detail": latest_detail,
    }


def verify_pages_build(repo_root: Path, remote: str, expected_commit: str, branch: str = "main") -> dict:
    """GitHub Pages latest build が対象 commit で built であることを検証する。"""
    try:
        remote_url = _git_output(repo_root, ["config", "--get", f"remote.{remote}.url"])
    except Exception as exc:
        return {"ok": False, "reason": "pages_remote_unparseable", "detail": str(exc)}
    slug = _repo_slug_from_remote_url(remote_url)
    if slug is None:
        return {"ok": False, "reason": "pages_remote_unparseable", "remote_url": remote_url}
    owner, repo = slug
    url = f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}/pages/builds/latest"
    try:
        build = _github_api_json(url)
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            return {"ok": False, "reason": "pages_build_unavailable", "url": url, "detail": str(exc)}
        return _verify_workflow_pages_status(
            owner=owner,
            repo=repo,
            branch=branch,
            expected_commit=expected_commit,
            latest_detail=str(exc),
        )
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        return {"ok": False, "reason": "pages_build_unavailable", "url": url, "detail": str(exc)}
    if not isinstance(build, dict):
        return {"ok": False, "reason": "pages_build_unavailable", "url": url, "detail": "response_not_object"}
    status = str(build.get("status") or "")
    commit = str(build.get("commit") or "")
    if status != "built":
        return {"ok": False, "reason": "pages_build_not_built", "status": status, "commit": commit, "url": url}
    if commit != expected_commit:
        workflow_pages = _verify_workflow_pages_status(
            owner=owner,
            repo=repo,
            branch=branch,
            expected_commit=expected_commit,
            latest_detail=f"latest_commit_mismatch:{commit}",
        )
        if workflow_pages["ok"]:
            return {**workflow_pages, "latest_build": {"status": status, "commit": commit, "url": url}}
        return {"ok": False, "reason": "pages_build_commit_mismatch", "status": status, "commit": commit, "expected_commit": expected_commit, "url": url, "workflow_pages": workflow_pages}
    return {"ok": True, "reason": "", "status": status, "commit": commit, "url": url}


def verify_deploy_workflow(repo_root: Path, remote: str, branch: str, expected_commit: str) -> dict:
    """Deploy Pages workflow が対象 commit で success したことを検証する。"""
    workflow_file = "deploy-pages.yml"
    workflow_path = repo_root / ".github" / "workflows" / workflow_file
    if not workflow_path.exists():
        return {"ok": False, "reason": "deploy_workflow_unavailable", "detail": f"workflow_missing:{workflow_file}"}
    try:
        remote_url = _git_output(repo_root, ["config", "--get", f"remote.{remote}.url"])
    except Exception as exc:
        return {"ok": False, "reason": "deploy_workflow_unavailable", "detail": str(exc)}
    slug = _repo_slug_from_remote_url(remote_url)
    if slug is None:
        return {"ok": False, "reason": "deploy_workflow_unavailable", "remote_url": remote_url}
    owner, repo = slug
    query = urlencode({"branch": branch, "head_sha": expected_commit, "per_page": 10})
    url = (
        f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}/actions/workflows/"
        f"{quote(workflow_file, safe='')}/runs?{query}"
    )
    try:
        payload = _github_api_json(url)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        return {"ok": False, "reason": "deploy_workflow_unavailable", "url": url, "detail": str(exc)}
    if not isinstance(payload, dict) or not isinstance(payload.get("workflow_runs"), list):
        return {"ok": False, "reason": "deploy_workflow_unavailable", "url": url, "detail": "workflow_runs_not_list"}
    runs = payload["workflow_runs"]
    if not runs:
        return {"ok": False, "reason": "deploy_workflow_not_success", "url": url, "workflow_file": workflow_file}
    matching_runs = [run for run in runs if isinstance(run, dict) and str(run.get("head_sha") or "") == expected_commit]
    if not matching_runs:
        first_head = ""
        for run in runs:
            if isinstance(run, dict):
                first_head = str(run.get("head_sha") or "")
                break
        return {
            "ok": False,
            "reason": "deploy_workflow_commit_mismatch",
            "url": url,
            "head_sha": first_head,
            "expected_commit": expected_commit,
            "workflow_file": workflow_file,
        }
    run = matching_runs[0]
    status = str(run.get("status") or "")
    conclusion = str(run.get("conclusion") or "")
    if status != "completed" or conclusion != "success":
        result = {
            "ok": False,
            "reason": "deploy_workflow_not_success",
            "status": status,
            "conclusion": conclusion,
            "head_sha": str(run.get("head_sha") or ""),
            "run_id": run.get("id", ""),
            "html_url": run.get("html_url", ""),
            "url": url,
            "workflow_file": workflow_file,
        }
        recovery = _deploy_workflow_fresh_dispatch_recovery(result=result, branch=branch, remote=remote)
        if recovery is not None:
            result["recovery"] = recovery
        return result
    return {
        "ok": True,
        "reason": "",
        "status": status,
        "conclusion": conclusion,
        "head_sha": str(run.get("head_sha") or ""),
        "event": str(run.get("event") or ""),
        "run_id": run.get("id", ""),
        "html_url": run.get("html_url", ""),
        "url": url,
        "workflow_file": workflow_file,
    }


def verify_deploy_workflow_covering_deploy_head(
    *, repo_root: Path, remote: str, branch: str, source_head: str, deploy_relevant_head: str
) -> dict:
    """同一docs treeを実際にdeployした祖先push tipを検証する。"""
    workflow_file = "deploy-pages.yml"
    if not (repo_root / ".github" / "workflows" / workflow_file).exists():
        return {
            "ok": False,
            "reason": "deploy_workflow_unavailable",
            "detail": f"workflow_missing:{workflow_file}",
        }
    try:
        remote_url = _git_output(repo_root, ["config", "--get", f"remote.{remote}.url"])
        ancestors = set(_git_output(repo_root, ["rev-list", source_head]).splitlines())
    except Exception as exc:
        return {"ok": False, "reason": "deploy_workflow_unavailable", "detail": str(exc)}
    slug = _repo_slug_from_remote_url(remote_url)
    if slug is None:
        return {"ok": False, "reason": "deploy_workflow_unavailable", "remote_url": remote_url}
    owner, repo = slug
    query = urlencode({"branch": branch, "per_page": 50})
    url = (
        f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}/actions/workflows/"
        f"{quote(workflow_file, safe='')}/runs?{query}"
    )
    try:
        payload = _github_api_json(url)
    except (
        urllib.error.HTTPError,
        urllib.error.URLError,
        TimeoutError,
        json.JSONDecodeError,
        UnicodeDecodeError,
        ValueError,
    ) as exc:
        return {"ok": False, "reason": "deploy_workflow_unavailable", "url": url, "detail": str(exc)}
    runs = payload.get("workflow_runs") if isinstance(payload, dict) else None
    if not isinstance(runs, list):
        return {
            "ok": False,
            "reason": "deploy_workflow_unavailable",
            "url": url,
            "detail": "workflow_runs_not_list",
        }
    for run in runs:
        if not isinstance(run, dict):
            continue
        candidate_head = str(run.get("head_sha") or "")
        if candidate_head not in ancestors:
            continue
        resolution = resolve_deploy_head(repo_root=repo_root, source_head=candidate_head)
        if not resolution.get("ok") or resolution.get("deploy_head") != deploy_relevant_head:
            continue
        status = str(run.get("status") or "")
        conclusion = str(run.get("conclusion") or "")
        return {
            "ok": status == "completed" and conclusion == "success",
            "reason": "" if status == "completed" and conclusion == "success" else "deploy_workflow_not_success",
            "status": status,
            "conclusion": conclusion,
            "head_sha": candidate_head,
            "covered_deploy_head": deploy_relevant_head,
            "event": str(run.get("event") or ""),
            "run_id": run.get("id", ""),
            "html_url": run.get("html_url", ""),
            "url": url,
            "workflow_file": workflow_file,
            "coverage_resolution": resolution,
        }
    return {
        "ok": False,
        "reason": "deploy_workflow_not_success",
        "url": url,
        "workflow_file": workflow_file,
        "covered_deploy_head": deploy_relevant_head,
    }


def wait_for_deploy_workflow_covering_deploy_head(
    *,
    repo_root: Path,
    remote: str,
    branch: str,
    source_head: str,
    deploy_relevant_head: str,
    deadline: float,
    poll_sec: int,
) -> dict:
    while True:
        result = verify_deploy_workflow_covering_deploy_head(
            repo_root=repo_root,
            remote=remote,
            branch=branch,
            source_head=source_head,
            deploy_relevant_head=deploy_relevant_head,
        )
        if result.get("ok") or not _is_retryable_deploy_workflow(result):
            return result
        if time.monotonic() >= deadline:
            return {**result, "detail": "deploy_workflow_wait_timeout"}
        time.sleep(max(1, poll_sec))


def _deploy_workflow_fresh_dispatch_recovery(*, result: dict, branch: str, remote: str) -> dict | None:
    if result.get("ok") or result.get("reason") != "deploy_workflow_not_success":
        return None
    status = str(result.get("status") or "")
    conclusion = str(result.get("conclusion") or "")
    if status != "completed" or not conclusion or conclusion == "success":
        return None
    return {
        "action": "workflow_dispatch",
        "workflow_file": "deploy-pages.yml",
        "branch": branch,
        "remote": remote,
        "reason": "completed_failure",
        "command": [
            "python",
            "-m",
            "tools.daily_self_heal",
            "dispatch-deploy-workflow",
            "--repo-root",
            ".",
            "--remote",
            remote,
            "--branch",
            branch,
        ],
    }


def dispatch_deploy_workflow_if_failed(repo_root: Path, remote: str, branch: str) -> dict:
    """同一 HEAD の Deploy Pages が completed/failure のときだけ fresh workflow dispatch する。"""
    workflow_file = "deploy-pages.yml"
    try:
        expected_commit = _git_output(repo_root, ["rev-parse", "HEAD"])
    except Exception as exc:
        return {"ok": False, "reason": "deploy_workflow_dispatch_unavailable", "detail": str(exc)}
    current = verify_deploy_workflow(
        repo_root=repo_root,
        remote=remote,
        branch=branch,
        expected_commit=expected_commit,
    )
    recovery = _deploy_workflow_fresh_dispatch_recovery(result=current, branch=branch, remote=remote)
    if recovery is None:
        return {
            "ok": False,
            "reason": "deploy_workflow_dispatch_not_applicable",
            "deploy_workflow": current,
        }
    try:
        remote_url = _git_output(repo_root, ["config", "--get", f"remote.{remote}.url"])
    except Exception as exc:
        return {"ok": False, "reason": "deploy_workflow_dispatch_unavailable", "detail": str(exc)}
    slug = _repo_slug_from_remote_url(remote_url)
    if slug is None:
        return {"ok": False, "reason": "deploy_workflow_dispatch_unavailable", "remote_url": remote_url}
    owner, repo = slug
    url = (
        f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}/actions/workflows/"
        f"{quote(workflow_file, safe='')}/dispatches"
    )
    try:
        status = _github_api_post(url, {"ref": branch})
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, UnicodeEncodeError, ValueError) as exc:
        return {
            "ok": False,
            "reason": "deploy_workflow_dispatch_failed",
            "url": url,
            "detail": str(exc),
            "deploy_workflow": current,
        }
    return {
        "ok": True,
        "reason": "",
        "action": "workflow_dispatch",
        "workflow_file": workflow_file,
        "branch": branch,
        "status": status,
        "expected_commit": expected_commit,
        "url": url,
        "deploy_workflow": current,
    }


def _is_retryable_deploy_workflow(result: dict) -> bool:
    if result.get("ok"):
        return False
    if result.get("reason") != "deploy_workflow_not_success":
        return False
    status = str(result.get("status") or "")
    conclusion = str(result.get("conclusion") or "")
    if status == "completed" and conclusion and conclusion != "success":
        return False
    return status in {"", "queued", "requested", "waiting", "pending", "in_progress"}


def wait_for_deploy_workflow(
    *,
    repo_root: Path,
    remote: str,
    branch: str,
    expected_commit: str,
    deadline: float,
    poll_sec: int,
) -> dict:
    """Deploy Pages workflow の transient pending だけを同一 deadline 内で待つ。"""
    while True:
        result = verify_deploy_workflow(
            repo_root=repo_root,
            remote=remote,
            branch=branch,
            expected_commit=expected_commit,
        )
        if result.get("ok") or not _is_retryable_deploy_workflow(result):
            return result
        if time.monotonic() >= deadline:
            return {**result, "detail": "deploy_workflow_wait_timeout"}
        time.sleep(max(1, poll_sec))


_DEPLOY_RELEVANT_PATHS = ("docs", ".github/workflows/deploy-pages.yml")


def resolve_deploy_head(*, repo_root: Path, source_head: str) -> dict:
    """制御コード HEAD から、Pages が実際に公開する直近 commit を解決する。"""
    try:
        deploy_head = _git_output(
            repo_root,
            ["rev-list", "-1", source_head, "--", *_DEPLOY_RELEVANT_PATHS],
        )
    except RuntimeError as exc:
        return {
            "ok": False,
            "reason": "deploy_head_unavailable",
            "detail": str(exc),
            "source_head": source_head,
            "deploy_head": "",
            "deploy_relevant_paths": list(_DEPLOY_RELEVANT_PATHS),
        }
    if not deploy_head:
        return {
            "ok": False,
            "reason": "deploy_relevant_commit_missing",
            "source_head": source_head,
            "deploy_head": "",
            "deploy_relevant_paths": list(_DEPLOY_RELEVANT_PATHS),
        }
    return {
        "ok": True,
        "reason": "",
        "source_head": source_head,
        "deploy_head": deploy_head,
        "resolution": (
            "source_head_is_deploy_relevant"
            if deploy_head == source_head
            else "latest_deploy_relevant_ancestor"
        ),
        "deploy_relevant_paths": list(_DEPLOY_RELEVANT_PATHS),
    }


def verify_publish(
    *,
    repo_root: Path,
    date: str,
    remote: str,
    branch: str,
    public_base_url: str,
    wait_sec: int,
    poll_sec: int,
    require_podcast: bool = False,
    podcast_state_path: Path | None = None,
) -> dict:
    local_head = _git_output(repo_root, ["rev-parse", "HEAD"])
    remote_head = _git_output(repo_root, ["ls-remote", remote, f"refs/heads/{branch}"]).split()[0]
    if local_head != remote_head:
        return {"ok": False, "reason": "remote_head_mismatch", "local_head": local_head, "remote_head": remote_head}
    if _is_git_worktree(repo_root):
        deploy_resolution = resolve_deploy_head(repo_root=repo_root, source_head=local_head)
        if not deploy_resolution["ok"]:
            return {
                "ok": False,
                "reason": deploy_resolution["reason"],
                "local_head": local_head,
                "remote_head": remote_head,
                "deploy_head_resolution": deploy_resolution,
            }
    else:
        # unit fixture など git metadata を持たない呼出元は従来契約を保つ。
        deploy_resolution = {
            "ok": True,
            "reason": "",
            "source_head": local_head,
            "deploy_head": local_head,
            "resolution": "source_head_without_git_metadata",
            "deploy_relevant_paths": list(_DEPLOY_RELEVANT_PATHS),
        }
    deploy_relevant_head = str(deploy_resolution["deploy_head"])
    deadline = time.monotonic() + max(0, wait_sec)
    # 複数 commit を 1 push した場合、workflow の head_sha は docs を変更した途中 commit
    # ではなく push の最終 HEAD になる。まず source HEAD を実測し、workflow が存在しない
    # tools-only push の場合だけ直近 deploy-relevant ancestor へフォールバックする。
    source_workflow = verify_deploy_workflow(
        repo_root=repo_root,
        remote=remote,
        branch=branch,
        expected_commit=local_head,
    )
    deploy_head = local_head
    if source_workflow.get("ok"):
        deploy_workflow = source_workflow
    elif _is_retryable_deploy_workflow(source_workflow) and str(source_workflow.get("status") or ""):
        if time.monotonic() < deadline:
            time.sleep(max(1, poll_sec))
        deploy_workflow = wait_for_deploy_workflow(
            repo_root=repo_root,
            remote=remote,
            branch=branch,
            expected_commit=local_head,
            deadline=deadline,
            poll_sec=poll_sec,
        )
    elif not _is_retryable_deploy_workflow(source_workflow):
        deploy_workflow = source_workflow
    elif deploy_relevant_head != local_head:
        deploy_workflow = wait_for_deploy_workflow_covering_deploy_head(
            repo_root=repo_root,
            remote=remote,
            branch=branch,
            source_head=local_head,
            deploy_relevant_head=deploy_relevant_head,
            deadline=deadline,
            poll_sec=poll_sec,
        )
        if deploy_workflow.get("ok"):
            deploy_head = str(deploy_workflow["head_sha"])
    else:
        deploy_workflow = wait_for_deploy_workflow(
            repo_root=repo_root,
            remote=remote,
            branch=branch,
            expected_commit=local_head,
            deadline=deadline,
            poll_sec=poll_sec,
        )
    head_state = {
        "local_head": local_head,
        "remote_head": remote_head,
        "deploy_head": deploy_head,
        "deploy_relevant_head": deploy_relevant_head,
        "deploy_head_resolution": deploy_resolution,
    }
    if not deploy_workflow["ok"]:
        return {
            "ok": False,
            "reason": deploy_workflow["reason"],
            **head_state,
            "deploy_workflow": deploy_workflow,
        }
    pages = verify_pages_build(repo_root=repo_root, remote=remote, expected_commit=deploy_head, branch=branch)
    if not pages["ok"]:
        return {
            "ok": False,
            "reason": pages["reason"],
            **head_state,
            "deploy_workflow": deploy_workflow,
            "pages": pages,
        }

    status_url = urljoin(public_base_url.rstrip("/") + "/", "publish-status.json")
    last_error = ""
    while True:
        try:
            with urllib.request.urlopen(status_url, timeout=20) as res:  # noqa: S310 - fixed public URL from runner config
                status = json.loads(res.read().decode("utf-8-sig"))
            if status.get("result") == "published_ok" and status.get("date") == date:
                pwa = verify_public_sw_version(repo_root=repo_root, public_base_url=public_base_url)
                if not pwa["ok"]:
                    return {
                        "ok": False,
                        "reason": pwa["reason"],
                        **head_state,
                        "url": status_url,
                        "deploy_workflow": deploy_workflow,
                        "pages": pages,
                        "pwa": pwa,
                    }
                audio = verify_public_audio(repo_root=repo_root, date=date, public_base_url=public_base_url)
                if audio["ok"]:
                    podcast = {"checked": False, "ok": True, "reason": "podcast_not_required"}
                    if require_podcast:
                        podcast = verify_podcast(
                            date=date,
                            state_path=podcast_state_path or repo_root / "build" / "youtube-podcast" / "uploads.json",
                            wait_sec=wait_sec,
                            poll_sec=poll_sec,
                        )
                        if not podcast["ok"]:
                            return {
                                "ok": False,
                                "reason": podcast["reason"],
                                **head_state,
                                "url": status_url,
                                "deploy_workflow": deploy_workflow,
                                "pages": pages,
                                "pwa": pwa,
                                "audio": audio,
                                "podcast": podcast,
                            }
                    return {
                        "ok": True,
                        "reason": "",
                        **head_state,
                        "url": status_url,
                        "deploy_workflow": deploy_workflow,
                        "pages": pages,
                        "pwa": pwa,
                        "audio": audio,
                        "podcast": podcast,
                    }
                return {
                    "ok": False,
                    "reason": audio["reason"],
                    **head_state,
                    "url": status_url,
                    "deploy_workflow": deploy_workflow,
                    "pages": pages,
                    "pwa": pwa,
                    "audio": audio,
                }
            last_error = f"publish-status mismatch: {status!r}"
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            last_error = str(exc)
        if time.monotonic() >= deadline:
            return {
                "ok": False,
                "reason": "public_sentinel_missing",
                "detail": last_error,
                **head_state,
                "url": status_url,
                "deploy_workflow": deploy_workflow,
                "pages": pages,
            }
        time.sleep(max(1, poll_sec))


def _distribution_artifact_manifest(repo_root: Path, date: str) -> dict:
    required = required_distribution_artifacts(date)
    missing = [rel for rel in required if not (repo_root / rel).exists()]
    manifest_rel = f"data/distribution/{date}.json"
    manifest_path = repo_root / manifest_rel
    manifest: dict = {}
    manifest_errors: list[str] = []
    manifest_reason = ""
    if manifest_path.exists():
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            loaded = {}
            manifest_errors.append(f"invalid_json:{exc}")
        if not isinstance(loaded, dict):
            manifest_errors.append("manifest_not_object")
            loaded = {}
        manifest = loaded
        required_text_fields = (
            "date",
            "primary_podcast_state",
            "deepdive_podcast_state",
            "latest_audio_state",
            "deepdive_audio_state",
            "generated_at",
        )
        for field in required_text_fields:
            value = str(manifest.get(field) or "").strip()
            if not value:
                manifest_errors.append(f"missing_field:{field}")
            elif field.endswith("_state") and Path(value).is_absolute():
                manifest_errors.append(f"absolute_path:{field}")
        if manifest_errors:
            manifest_reason = "distribution_manifest_invalid"
        elif str(manifest.get("date")) != date:
            manifest_reason = "distribution_manifest_mismatch"
        else:
            pre_publish_commit = str(manifest.get("pre_publish_commit") or "").strip()
            if not re.fullmatch(r"[0-9a-fA-F]{7,40}", pre_publish_commit):
                manifest_reason = "distribution_manifest_commit_missing"
            else:
                publish_commit = str(manifest.get("publish_commit") or "").strip()
                resolution = str(manifest.get("publish_commit_resolution") or "").strip()
                same_publish_contract = str(manifest.get("same_publish_contract") or "").strip()
                if not publish_commit and (
                    resolution != "post_push_verify"
                    or same_publish_contract != "pre_publish_commit_must_equal_verified_publish_commit"
                ):
                    manifest_reason = "distribution_manifest_publish_commit_resolution_missing"
    return {
        "required": required,
        "missing": missing,
        "manifest_path": manifest_rel,
        "manifest": manifest,
        "manifest_errors": manifest_errors,
        "manifest_reason": manifest_reason,
    }


def _canonical_quality_text_sha256(payload: bytes) -> str:
    text = payload.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _verify_deepdive_quality_head_binding(
    *,
    repo_root: Path,
    audit: dict,
) -> dict:
    """品質engineが読んだ全fileを現在のHEAD bytesへ束縛する。"""

    repo = Path(repo_root).resolve()
    raw_files = list(audit.get("auditedFiles") or [])
    if not raw_files:
        return {"ok": False, "reason": "deepdive_quality_audited_paths_missing", "paths": []}
    evidence_by_path: dict[str, dict] = {}
    try:
        for row in raw_files:
            if not isinstance(row, dict):
                raise ValueError("audit row is not an object")
            resolved = Path(str(row.get("path") or "")).resolve(strict=True)
            relative = resolved.relative_to(repo).as_posix()
            if relative in evidence_by_path:
                raise ValueError("duplicate audit path")
            evidence_by_path[relative] = row
    except (OSError, ValueError, TypeError):
        return {"ok": False, "reason": "deepdive_quality_path_invalid", "paths": []}
    relative_paths = sorted(evidence_by_path)
    if not _is_git_worktree(repo):
        return {
            "ok": False,
            "reason": "deepdive_quality_head_unavailable",
            "paths": relative_paths,
        }
    try:
        head = _git_output(repo, ["rev-parse", "HEAD"])
    except RuntimeError:
        return {
            "ok": False,
            "reason": "deepdive_quality_head_unavailable",
            "paths": relative_paths,
        }
    archive = subprocess.run(
        ["git", "-C", str(repo), "archive", "--format=tar", head, "--", *relative_paths],
        capture_output=True,
        check=False,
    )
    if archive.returncode != 0:
        return {
            "ok": False,
            "reason": "deepdive_quality_source_untracked",
            "head": head,
            "paths": relative_paths,
        }
    archived_hashes: dict[str, str] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:") as stream:
            for member in stream.getmembers():
                if not member.isfile() or member.name not in evidence_by_path:
                    continue
                extracted = stream.extractfile(member)
                if extracted is None:
                    raise tarfile.TarError("archive member body missing")
                archived_hashes[member.name] = _canonical_quality_text_sha256(
                    extracted.read()
                )
    except (tarfile.TarError, UnicodeError, OSError):
        return {
            "ok": False,
            "reason": "deepdive_quality_head_check_failed",
            "head": head,
            "paths": relative_paths,
        }
    if set(archived_hashes) != set(relative_paths):
        return {
            "ok": False,
            "reason": "deepdive_quality_source_untracked",
            "head": head,
            "paths": relative_paths,
        }
    mismatched = [
        path
        for path in relative_paths
        if str(evidence_by_path[path].get("canonicalTextSha256") or "")
        != archived_hashes[path]
    ]
    if mismatched:
        return {
            "ok": False,
            "reason": "deepdive_quality_head_blob_mismatch",
            "head": head,
            "paths": relative_paths,
            "mismatched_paths": mismatched,
        }
    return {"ok": True, "reason": "", "head": head, "paths": relative_paths}


def _producer_lineage_expected(
    *, repo_root: Path, ops_root: Path, date: str, run_intent: str, run_id: str
) -> dict[str, str]:
    artifact_root = str(repo_root.resolve())
    canonical_ops_root = str(ops_root.resolve())
    daily_root_id = hashlib.sha256(
        (
            f"News-Grasp|{date}|{artifact_root.casefold()}|"
            f"{canonical_ops_root.casefold()}"
        ).encode("utf-8")
    ).hexdigest()
    root_operation_id = hashlib.sha256(
        f"{daily_root_id}|{run_id}|root-operation".encode("utf-8")
    ).hexdigest()
    producer_operation_id = hashlib.sha256(
        f"{root_operation_id}|producer|{run_intent}".encode("utf-8")
    ).hexdigest()
    receipt_sha256 = hashlib.sha256(
        (
            "NEWS_GRASP_PRODUCER_LINEAGE_V1|"
            f"{date}|{artifact_root.casefold()}|{canonical_ops_root.casefold()}|"
            f"{daily_root_id}|{root_operation_id}|{producer_operation_id}|"
            f"{run_intent}|{run_id}"
        ).encode("utf-8")
    ).hexdigest()
    return {
        "artifactRoot": artifact_root,
        "opsRoot": canonical_ops_root,
        "dailyRootId": daily_root_id,
        "rootOperationId": root_operation_id,
        "producerOperationId": producer_operation_id,
        "producerRunIntent": run_intent,
        "lineageReceiptSha256": receipt_sha256,
    }


def _load_producer_lineage(
    *, repo_root: Path, state_path: Path, date: str
) -> dict[str, str] | None:
    if not state_path.is_file() or state_path.is_symlink():
        return None
    try:
        state = json.loads(state_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(state, dict) or state.get("date") != date:
        return None
    run_intent = str(state.get("run_intent") or "")
    run_id = str(state.get("run_id") or "")
    if not run_intent or not run_id:
        return None
    expected = _producer_lineage_expected(
        repo_root=repo_root,
        ops_root=state_path.parent,
        date=date,
        run_intent=run_intent,
        run_id=run_id,
    )
    if any(state.get(field) != value for field, value in expected.items()):
        return None
    return expected


def verify_publish_complete(
    *,
    repo_root: Path,
    ops_repo_root: Path | None = None,
    date: str,
    remote: str,
    branch: str,
    public_base_url: str,
    wait_sec: int,
    poll_sec: int,
    primary_podcast_state_path: Path | None = None,
    deepdive_podcast_state_path: Path | None = None,
    notification_state_path: Path | None = None,
    producer_state_path: Path | None = None,
) -> dict:
    """公開完了を remote/public/audio/podcast/local inventory の同一 manifest として検証する。"""
    readiness_date = _current_jst_date()
    distribution = _distribution_artifact_manifest(repo_root, date)
    base = {
        "ok": False,
        "reason": "",
        "public_status": "red",
        "scheduled_attempt_status": "unknown",
        "recovery_attempt_status": "not_verified",
        "date": date,
        "readiness_date": readiness_date,
        "distribution_artifacts": distribution,
    }
    if distribution["missing"]:
        return {**base, "reason": "distribution_artifact_missing"}
    if distribution.get("manifest_reason"):
        return {**base, "reason": distribution["manifest_reason"]}
    state_path = producer_state_path or (
        Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        / "News-Grasp"
        / "ops"
        / "news-grasp-runner-state.json"
    )
    producer_lineage = _load_producer_lineage(
        repo_root=repo_root,
        state_path=state_path,
        date=date,
    )
    if producer_lineage is None:
        return {**base, "reason": "producer_lineage_invalid"}

    shared_quality = deepdive_quality.audit_issue(
        repo_root=repo_root,
        issue_date=date,
        require_rendered_public=True,
    )
    base["deepdive_shared_quality"] = shared_quality
    if shared_quality.get("status") != "Green":
        issue_codes = [
            str(code)
            for code in shared_quality.get("issueCodes", [])
            if str(code)
        ]
        return {
            **base,
            "reason": (
                issue_codes[0]
                if issue_codes
                else "deepdive_shared_quality_invalid"
            ),
        }
    quality_head_binding = _verify_deepdive_quality_head_binding(
        repo_root=repo_root,
        audit=shared_quality,
    )
    base["deepdive_quality_head_binding"] = quality_head_binding
    if not quality_head_binding.get("ok"):
        return {
            **base,
            "reason": str(
                quality_head_binding.get("reason")
                or "deepdive_quality_source_dirty"
            ),
        }

    primary_state = primary_podcast_state_path or repo_root / "build" / "youtube-podcast" / "uploads.json"
    deepdive_state = deepdive_podcast_state_path or repo_root / "build" / "youtube-podcast-deepdive" / "uploads.json"
    publish = verify_publish(
        repo_root=repo_root,
        date=date,
        remote=remote,
        branch=branch,
        public_base_url=public_base_url,
        wait_sec=wait_sec,
        poll_sec=poll_sec,
        require_podcast=True,
        podcast_state_path=primary_state,
    )
    manifest = {
        **base,
        **producer_lineage,
        "publish": publish,
        "local_head": publish.get("local_head", ""),
        "remote_head": publish.get("remote_head", ""),
        "publish_status_url": publish.get("url", ""),
        "pwa": publish.get("pwa", {}),
        "audio": publish.get("audio", {}),
        "podcasts": {
            "primary": {"date": date, **dict(publish.get("podcast") or {})},
            "deepdive": {},
        },
        "distribution_manifest": distribution.get("manifest", {}),
    }
    if not publish.get("ok"):
        return {**manifest, "reason": str(publish.get("reason") or "publish_sentinel_failed")}

    local_head = str(publish.get("local_head") or "")
    remote_head = str(publish.get("remote_head") or "")
    deploy_head = str(publish.get("deploy_head") or local_head)
    if not local_head or local_head != remote_head:
        return {**manifest, "reason": "publish_commit_mismatch"}
    manifest["source_commit"] = local_head
    manifest["deploy_head"] = deploy_head
    if str(quality_head_binding.get("head") or "") != local_head:
        return {**manifest, "reason": "deepdive_quality_head_mismatch"}
    manifest_rel = str(distribution.get("manifest_path") or f"data/distribution/{date}.json")
    manifest_in_head = _git_tree_has_path(repo_root, local_head, manifest_rel)
    if manifest_in_head is False:
        return {**manifest, "reason": "distribution_manifest_remote_missing"}
    manifest["distribution_manifest_in_head"] = manifest_in_head
    distribution_manifest = dict(distribution.get("manifest") or {})
    pre_publish_commit = str(distribution_manifest.get("pre_publish_commit") or "").strip()
    publish_commit = str(distribution_manifest.get("publish_commit") or "").strip()
    if not _commit_is_ancestor(repo_root, pre_publish_commit, local_head):
        return {**manifest, "reason": "distribution_manifest_commit_mismatch"}
    if publish_commit and not _commit_is_ancestor(repo_root, publish_commit, local_head):
        return {**manifest, "reason": "distribution_manifest_commit_mismatch"}

    deepdive = verify_podcast(
        date=date,
        state_path=deepdive_state,
        wait_sec=wait_sec,
        poll_sec=poll_sec,
        expected_title=f"News-Grasp DeepDive Dialogue {date}",
    )
    manifest["podcasts"]["deepdive"] = {"date": date, **deepdive}
    if not deepdive.get("ok"):
        return {**manifest, "reason": "deepdive_podcast_missing"}

    if notification_state_path is not None:
        notification = _load_notification_state(notification_state_path, date)
        manifest["notification"] = notification.get("state", {})
        if notification.get("reason"):
            return {**manifest, "reason": notification["reason"], "notification": notification}

    live_readiness = verify_live_runner_readiness(
        repo_root=repo_root,
        ops_repo_root=ops_repo_root,
        date=readiness_date,
    )
    manifest["live_runner_readiness"] = live_readiness
    if not live_readiness.get("ok"):
        return {**manifest, "reason": str(live_readiness.get("reason") or "live_runner_readiness_failed")}

    last_scheduled = dict(live_readiness.get("last_scheduled_attempt") or {})
    last_scheduled_status = str(last_scheduled.get("status") or "unknown")
    if last_scheduled_status == "succeeded":
        scheduled_attempt_status = "succeeded"
        recovery_attempt_status = "not_required"
    elif last_scheduled_status == "failed":
        scheduled_attempt_status = "failed_then_recovered"
        recovery_attempt_status = "succeeded"
    else:
        scheduled_attempt_status = "unknown_then_recovered"
        recovery_attempt_status = "succeeded"

    return {
        **manifest,
        "ok": True,
        "reason": "",
        "public_status": "green",
        "scheduled_attempt_status": scheduled_attempt_status,
        "recovery_attempt_status": recovery_attempt_status,
        "source_commit": local_head,
        "publish_commit": deploy_head,
        "same_publish": {
            "date": date,
            "local_head": local_head,
            "remote_head": remote_head,
            "source_head": local_head,
            "deploy_head": deploy_head,
            "publish_commit": deploy_head,
            "distribution_date": str(distribution_manifest.get("date") or ""),
            "distribution_pre_publish_commit": pre_publish_commit,
            "distribution_publish_commit_resolution": str(
                distribution_manifest.get("publish_commit_resolution") or ""
            ),
            "distribution_same_publish_contract": str(
                distribution_manifest.get("same_publish_contract") or ""
            ),
        },
    }


_KNOWN_NOTIFICATION_STATUSES = {
    "sent",
    "send_failed",
    "no_subscribers",
    "dry_run",
    "skipped_fallback",
    "skipped_not_normal",
    "config_error",
    "external_error",
}


def _load_notification_state(path: Path, date: str) -> dict:
    if not path.exists():
        return {"path": str(path), "state": {}, "reason": "notification_state_missing"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError, ValueError) as exc:
        return {"path": str(path), "state": {}, "reason": "notification_state_invalid", "detail": str(exc)}
    if not isinstance(payload, dict):
        return {"path": str(path), "state": {}, "reason": "notification_state_invalid", "detail": "not_object"}
    status = str(payload.get("status") or "")
    if status not in _KNOWN_NOTIFICATION_STATUSES:
        return {
            "path": str(path),
            "state": payload,
            "reason": "notification_state_invalid",
            "detail": f"unknown_status:{status}",
        }
    payload_date = str(payload.get("date") or "")
    if payload_date and payload_date != date:
        return {
            "path": str(path),
            "state": payload,
            "reason": "notification_state_mismatch",
            "detail": f"date:{payload_date}",
        }
    return {"path": str(path), "state": payload, "reason": ""}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="News-Grasp daily self-healing helpers")
    sub = parser.add_subparsers(dest="cmd", required=True)

    checksum = sub.add_parser("checksum")
    checksum.add_argument("--repo-path", type=Path, required=True)
    checksum.add_argument("--live-path", type=Path, required=True)

    phase0 = sub.add_parser("phase0")
    phase0.add_argument("--snapshot-json", type=Path, required=True)

    deadman = sub.add_parser("deadman")
    deadman.add_argument("--state-file", type=Path, required=True)
    deadman.add_argument("--date", required=True)
    deadman.add_argument("--max-ok-age-hours", type=int, default=27)
    deadman.add_argument("--alert-log", type=Path, required=True)
    deadman.add_argument("--marker", type=Path, required=True)
    deadman.add_argument("--webhook-env", default="NEWS_GRASP_ALERT_WEBHOOK_URL")

    verify = sub.add_parser("verify-publish")
    verify.add_argument("--repo-root", type=Path, required=True)
    verify.add_argument("--date", required=True)
    verify.add_argument("--remote", default="origin")
    verify.add_argument("--branch", default="main")
    verify.add_argument("--public-base-url", default="https://hidepon-umg.github.io/News-Grasp/")
    verify.add_argument("--wait-sec", type=int, default=600)
    verify.add_argument("--poll-sec", type=int, default=30)
    verify.add_argument("--require-podcast", action="store_true")
    verify.add_argument("--podcast-state", type=Path, default=None)

    dispatch = sub.add_parser("dispatch-deploy-workflow")
    dispatch.add_argument("--repo-root", type=Path, required=True)
    dispatch.add_argument("--remote", default="origin")
    dispatch.add_argument("--branch", default="main")

    wait_deploy = sub.add_parser("wait-deploy-workflow")
    wait_deploy.add_argument("--repo-root", type=Path, required=True)
    wait_deploy.add_argument("--remote", default="origin")
    wait_deploy.add_argument("--branch", default="main")
    wait_deploy.add_argument("--wait-sec", type=int, default=600)
    wait_deploy.add_argument("--poll-sec", type=int, default=30)

    podcast = sub.add_parser("verify-podcast")
    podcast.add_argument("--date", required=True)
    podcast.add_argument("--state", type=Path, default=Path("build") / "youtube-podcast" / "uploads.json")
    podcast.add_argument("--wait-sec", type=int, default=1200)
    podcast.add_argument("--poll-sec", type=int, default=30)
    podcast.add_argument("--expected-title", default=None)

    complete = sub.add_parser("verify-publish-complete")
    complete.add_argument("--repo-root", type=Path, required=True)
    complete.add_argument("--ops-repo-root", type=Path, default=None)
    complete.add_argument("--date", required=True)
    complete.add_argument("--remote", default="origin")
    complete.add_argument("--branch", default="main")
    complete.add_argument("--public-base-url", default="https://hidepon-umg.github.io/News-Grasp/")
    complete.add_argument("--wait-sec", type=int, default=600)
    complete.add_argument("--poll-sec", type=int, default=30)
    complete.add_argument("--primary-podcast-state", type=Path, default=None)
    complete.add_argument("--deepdive-podcast-state", type=Path, default=None)
    complete.add_argument("--notification-state", type=Path, default=None)
    complete.add_argument("--output", type=Path, default=None)

    live_ready = sub.add_parser("verify-live-runner-readiness")
    live_ready.add_argument("--repo-root", type=Path, required=True)
    live_ready.add_argument("--ops-repo-root", type=Path, default=None)
    live_ready.add_argument("--date", required=True)
    live_ready.add_argument("--live-runner", type=Path, default=None)
    live_ready.add_argument("--live-watcher", type=Path, default=None)
    live_ready.add_argument("--live-bootstrap", type=Path, default=None)
    live_ready.add_argument("--task-name", default="News-Grasp Production")
    live_ready.add_argument("--bootstrap-task-name", default="News-Grasp Bootstrap")
    live_ready.add_argument("--skip-canary", action="store_true")
    live_ready.add_argument("--canary-timeout-sec", type=int, default=60)
    live_ready.add_argument("--powershell-exe", default="powershell.exe")
    live_ready.add_argument("--output", type=Path, default=None)

    args = parser.parse_args(argv)
    if args.cmd == "checksum":
        result = compare_files(args.repo_path, args.live_path)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["synced"] else 1
    if args.cmd == "phase0":
        snapshot = json.loads(args.snapshot_json.read_text(encoding="utf-8"))
        print(json.dumps(classify_phase0(snapshot), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "deadman":
        state = json.loads(args.state_file.read_text(encoding="utf-8")) if args.state_file.exists() else {}
        decision = evaluate_deadman(
            state=state,
            now=datetime.now(timezone.utc),
            expected_date=args.date,
            max_ok_age_hours=args.max_ok_age_hours,
        )
        if decision["alert"]:
            result = emit_alert(
                {"date": args.date, **decision},
                alert_log=args.alert_log,
                marker_path=args.marker,
                webhook_url=os.environ.get(args.webhook_env, ""),
            )
            print(json.dumps({**decision, **result}, ensure_ascii=False, indent=2))
            return 2
        print(json.dumps(decision, ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "verify-publish":
        result = verify_publish(
            repo_root=args.repo_root,
            date=args.date,
            remote=args.remote,
            branch=args.branch,
            public_base_url=args.public_base_url,
            wait_sec=args.wait_sec,
            poll_sec=args.poll_sec,
            require_podcast=args.require_podcast,
            podcast_state_path=args.podcast_state,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1
    if args.cmd == "dispatch-deploy-workflow":
        result = dispatch_deploy_workflow_if_failed(
            repo_root=args.repo_root,
            remote=args.remote,
            branch=args.branch,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1
    if args.cmd == "wait-deploy-workflow":
        expected_commit = _git_output(args.repo_root, ["rev-parse", "HEAD"])
        result = wait_for_deploy_workflow(
            repo_root=args.repo_root,
            remote=args.remote,
            branch=args.branch,
            expected_commit=expected_commit,
            deadline=time.monotonic() + max(0, args.wait_sec),
            poll_sec=args.poll_sec,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1
    if args.cmd == "verify-podcast":
        result = verify_podcast(
            date=args.date,
            state_path=args.state,
            wait_sec=args.wait_sec,
            poll_sec=args.poll_sec,
            expected_title=args.expected_title,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1
    if args.cmd == "verify-publish-complete":
        result = verify_publish_complete(
            repo_root=args.repo_root,
            ops_repo_root=args.ops_repo_root,
            date=args.date,
            remote=args.remote,
            branch=args.branch,
            public_base_url=args.public_base_url,
            wait_sec=args.wait_sec,
            poll_sec=args.poll_sec,
            primary_podcast_state_path=args.primary_podcast_state,
            deepdive_podcast_state_path=args.deepdive_podcast_state,
            notification_state_path=args.notification_state,
        )
        text = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text + "\n", encoding="utf-8")
        print(text)
        return 0 if result["ok"] else 1
    if args.cmd == "verify-live-runner-readiness":
        result = verify_live_runner_readiness(
            repo_root=args.repo_root,
            ops_repo_root=args.ops_repo_root,
            date=args.date,
            live_runner_path=args.live_runner,
            live_watcher_path=args.live_watcher,
            live_bootstrap_path=args.live_bootstrap,
            task_name=args.task_name,
            bootstrap_task_name=args.bootstrap_task_name,
            run_canary=not args.skip_canary,
            canary_timeout_sec=args.canary_timeout_sec,
            powershell_exe=args.powershell_exe,
        )
        text = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text + "\n", encoding="utf-8")
        print(text)
        return 0 if result["ok"] else 1
    raise AssertionError(args.cmd)


if __name__ == "__main__":
    sys.exit(main())
