"""Sol review lane B の sealed ExpectedRed 契約。

R2/R6/R7 は Task Scheduler や high-cost 実行へ接続せず、readiness と
launcher/ledger の deterministic seam だけを観測する。
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib
import importlib.machinery
import inspect
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "config" / "news_grasp_cleanroom_task_manifest_v1.json"
LAUNCHER_PATH = ROOT / "scripts" / "ops" / "news-grasp-task-launcher.pyw"
INSTALLER_PATH = ROOT / "scripts" / "ops" / "install-news-grasp-ops.ps1"
ISSUE_DATE = "2026-08-22"
OBSERVED_AT = datetime(2026, 8, 22, 6, 0, tzinfo=timezone(timedelta(hours=9)))
BOOTSTRAP_OBSERVED_AT = "2026-08-22T05:55:00+09:00"
INSTALLED_GENERATION_TIMESTAMP = "2026-08-22T05:50:00+09:00"
RUNTIME_ROOT = Path.home() / ".news-grasp-runtime"


def _manifest_and_snapshot() -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    task = manifest["tasks"][0]
    snapshot = {
        "schemaVersion": "NEWS_GRASP_LIVE_TASK_SNAPSHOT_V1",
        "tasks": [
            {
                "taskPath": task["taskPath"],
                "taskName": task["taskName"],
                "enabled": True,
                "multipleInstancesPolicy": task["multipleInstancesPolicy"],
                "triggers": deepcopy(task["triggers"]),
                "action": deepcopy(task["action"]),
            }
        ],
        "extraEnabledTasks": [],
    }
    return manifest, snapshot


def _stable_authority(daily_self_heal: Any, *, generation_id: str) -> dict[str, Any]:
    return {
        "schemaVersion": "STABLE_TASK_AUTHORITY_V1",
        "taskName": "News-Grasp Production",
        "taskPath": "\\",
        "multipleInstancesPolicy": "IgnoreNew",
        "principal": {
            "userId": "TEST\\news-grasp",
            "logonType": "Interactive",
            "runLevel": "Limited",
        },
        "manifestAction": deepcopy(daily_self_heal._CLEANROOM_MANIFEST_ACTION),
        "triggers": deepcopy(daily_self_heal._CLEANROOM_MANIFEST_TRIGGERS),
        "workingDirectoryToken": "<RUNTIME_ROOT>",
        "authoritySha256": "a" * 64,
        "generationId": generation_id,
    }


def _patch_readiness_seams(
    monkeypatch: pytest.MonkeyPatch,
    daily_self_heal: Any,
    tmp_path: Path,
    *,
    bootstrap_last_run_time: str = "2026-08-22T05:55:00+09:00",
    bootstrap_generation_id: str = "generation-20260822",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """verify_live_runner_readiness を全て deterministic な観測へ束ねる。"""
    manifest, live_snapshot = _manifest_and_snapshot()
    authority = _stable_authority(
        daily_self_heal,
        generation_id="generation-20260822",
    )
    launcher = tmp_path / "bin" / "news-grasp-task-launcher.pyw"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text("# deterministic lane-b launcher fixture\n", encoding="utf-8")
    deadman_launcher = launcher.parent / "news-grasp-deadman-launcher.pyw"
    deadman_launcher.write_text("# deterministic deadman fixture\n", encoding="utf-8")
    binding_path = tmp_path / "bin" / "news-grasp-high-cost-binding-v1.json"
    binding_path.write_text("{}\n", encoding="utf-8")

    def checksum(_repo: Path, _live: Path) -> dict[str, Any]:
        return {
            "repo_exists": True,
            "live_exists": True,
            "repo_sha256": "1" * 64,
            "live_sha256": "1" * 64,
            "synced": True,
        }

    production_details = {
        "ok": True,
        "enabled": True,
        "state": "Ready",
        "task_name": "News-Grasp Production",
        "task_path": "\\",
        "multiple_instances_policy": "IgnoreNew",
        "principal_user_id": "TEST\\news-grasp",
        "current_user_id": "TEST\\news-grasp",
        "principal_logon_type": "Interactive",
        "principal_run_level": "Limited",
        "last_task_result": 0,
        "last_run_time": "2026-08-22T06:00:00+09:00",
        "triggers": [
            {
                "enabled": True,
                "trigger_type": "MSFT_TaskDailyTrigger",
                "days_interval": 1,
                "start_boundary": "2026-08-22T06:00:00+09:00",
            },
        ],
        "actions": [
            {
                "execute": "C:\\Python312\\pythonw.exe",
                "arguments": "news-grasp-task-launcher.pyw dispatch",
                "workingDirectory": str(RUNTIME_ROOT / "production-runtime"),
            }
        ],
        "task_topology": [
            {
                "task_name": "News-Grasp Deadman",
                "task_path": "\\",
                "enabled": True,
                "state": "Ready",
                "multiple_instances_policy": "IgnoreNew",
                "execution_time_limit": "PT1H45M",
                "principal_user_id": "TEST\\news-grasp",
                "principal_logon_type": "Interactive",
                "principal_run_level": "Limited",
                "actions": [
                    {
                        "execute": "C:\\Python312\\pythonw.exe",
                        "arguments": subprocess.list2cmdline([str(deadman_launcher)]),
                        "workingDirectory": str(deadman_launcher.parent),
                    }
                ],
                "triggers": [
                    {
                        "enabled": True,
                        "trigger_type": "MSFT_TaskDailyTrigger",
                        "days_interval": 1,
                        "start_boundary": "2026-08-22T06:40:00+09:00",
                        "repetition_interval": "PT1H",
                        "repetition_duration": "P1D",
                        "stop_at_duration_end": False,
                    }
                ],
            },
            {"task_name": "News-Grasp Pull", "task_path": "\\", "enabled": False},
            {"task_name": "News-Grasp Runner", "task_path": "\\", "enabled": False},
        ],
    }
    bootstrap_details = {
        "ok": True,
        "enabled": True,
        "state": "Ready",
        "task_name": "News-Grasp Bootstrap",
        "task_path": "\\",
        "multiple_instances_policy": "IgnoreNew",
        "principal_user_id": "TEST\\news-grasp",
        "current_user_id": "TEST\\news-grasp",
        "principal_logon_type": "Interactive",
        "principal_run_level": "Limited",
        "last_task_result": 0,
        "last_run_time": bootstrap_last_run_time,
        "lastRunTime": bootstrap_last_run_time,
        "issue_date": ISSUE_DATE,
        "issueDate": ISSUE_DATE,
        "generation_id": bootstrap_generation_id,
        "generationId": bootstrap_generation_id,
        "triggers": [
            {
                "enabled": True,
                "trigger_type": "MSFT_TaskDailyTrigger",
                "days_interval": 1,
                "start_boundary": "2026-08-22T05:55:00+09:00",
            }
        ],
        "actions": [
            {
                "execute": "C:\\Python312\\pythonw.exe",
                "arguments": "news-grasp-task-launcher.pyw bootstrap",
                "workingDirectory": str(RUNTIME_ROOT / "production-runtime"),
            }
        ],
    }
    cleanroom_definition = {
        "recognized": True,
        "ok": True,
        "reason": "",
        "stableAuthority": deepcopy(authority),
        "authority": deepcopy(authority),
        "manifest": deepcopy(manifest),
        "live_snapshot": deepcopy(live_snapshot),
        "topology": {
            "manifest": deepcopy(manifest),
            "live_snapshot": deepcopy(live_snapshot),
            "bootstrap": deepcopy(bootstrap_details),
        },
    }
    binding = {
        "ok": True,
        "binding_path": str(binding_path),
        "binding_receipt_sha256": "b" * 64,
        "task_pythonw_path": "C:\\Python312\\pythonw.exe",
    }

    monkeypatch.setattr(daily_self_heal, "compare_files", checksum)
    monkeypatch.setattr(
        daily_self_heal,
        "_task_launcher_source_contract",
        lambda _path: {"ok": True},
    )
    monkeypatch.setattr(
        daily_self_heal,
        "_scheduled_task_details",
        lambda *, task_name, powershell_exe: (
            deepcopy(bootstrap_details)
            if task_name == "News-Grasp Bootstrap"
            else deepcopy(production_details)
        ),
    )
    monkeypatch.setattr(
        daily_self_heal,
        "_cleanroom_live_task_definition",
        lambda **_kwargs: deepcopy(cleanroom_definition),
    )
    monkeypatch.setattr(
        daily_self_heal,
        "_validate_live_high_cost_binding_authority",
        lambda **_kwargs: deepcopy(binding),
    )
    monkeypatch.setattr(
        daily_self_heal,
        "_run_live_startup_canary",
        lambda **_kwargs: {"ok": True, "status": "smoke_ok"},
    )
    monkeypatch.setattr(
        daily_self_heal,
        "_probe_external_control_plane_readiness",
        lambda: {
            "schemaVersion": "EXTERNAL_CONTROL_PLANE_READINESS_V1",
            "status": "ready",
            "reasonCode": "fixture_ready",
            "modelLaunchCount": 0,
            "receiptSha256": "c" * 64,
        },
    )
    return authority, bootstrap_details


def _definition_fixture(
    tmp_path: Path,
    daily_self_heal: Any,
    *,
    bootstrap_last_run_time: str = "2026-08-22T05:55:00+09:00",
    bootstrap_generation_id: str = "generation-20260822",
) -> tuple[dict[str, Any], dict[str, Any], Path, dict[str, Any]]:
    launcher = tmp_path / "bin" / "news-grasp-task-launcher.pyw"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text("# definition fixture\n", encoding="utf-8")
    pythonw = launcher.parent / "pythonw.exe"
    pythonw.write_bytes(b"pythonw fixture")
    authority = _stable_authority(
        daily_self_heal,
        generation_id="generation-20260822",
    )
    authority_path = launcher.parent / "news-grasp-stable-task-authority-v1.json"
    authority_path.write_text(
        json.dumps(authority, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    production_args = subprocess.list2cmdline(
        [
            str(launcher),
            "dispatch",
            "--schedule-id",
            "news-grasp-daily-v1",
            "--intent",
            "reconcile",
        ]
    )
    task_details = {
        "ok": True,
        "enabled": True,
        "state": "Ready",
        "task_name": "News-Grasp Production",
        "task_path": "\\",
        "multiple_instances_policy": "IgnoreNew",
        "principal_user_id": "TEST\\news-grasp",
        "current_user_id": "TEST\\news-grasp",
        "principal_logon_type": "Interactive",
        "principal_run_level": "Limited",
        "actions": [
            {
                "execute": str(pythonw),
                "arguments": production_args,
                "workingDirectory": str(RUNTIME_ROOT / "production-runtime"),
            }
        ],
        "triggers": [
            {
                "enabled": True,
                "trigger_type": "MSFT_TaskDailyTrigger",
                "days_interval": 1,
                "start_boundary": "2026-08-22T06:00:00+09:00",
            },
        ],
    }
    bootstrap_details = {
        "ok": True,
        "enabled": True,
        "state": "Ready",
        "task_name": "News-Grasp Bootstrap",
        "task_path": "\\",
        "multiple_instances_policy": "IgnoreNew",
        "principal_user_id": "TEST\\news-grasp",
        "current_user_id": "TEST\\news-grasp",
        "principal_logon_type": "Interactive",
        "principal_run_level": "Limited",
        "last_task_result": 0,
        "last_run_time": bootstrap_last_run_time,
        "lastRunTime": bootstrap_last_run_time,
        "issue_date": ISSUE_DATE,
        "issueDate": ISSUE_DATE,
        "generation_id": bootstrap_generation_id,
        "generationId": bootstrap_generation_id,
        "installed_generation_id": "generation-20260822",
        "installedGenerationId": "generation-20260822",
        "installed_generation_timestamp": INSTALLED_GENERATION_TIMESTAMP,
        "installedGenerationTimestamp": INSTALLED_GENERATION_TIMESTAMP,
        "installed_manifest_sha256": hashlib.sha256(MANIFEST_PATH.read_bytes()).hexdigest(),
        "installedManifestSha256": hashlib.sha256(MANIFEST_PATH.read_bytes()).hexdigest(),
        "triggers": [
            {
                "enabled": True,
                "trigger_type": "MSFT_TaskDailyTrigger",
                "days_interval": 1,
                "start_boundary": "2026-08-22T05:55:00+09:00",
            }
        ],
        "actions": [
            {
                "execute": str(pythonw),
                "arguments": subprocess.list2cmdline(
                    [str(launcher), "bootstrap", "--scheduled-task-name", "News-Grasp Bootstrap"]
                ),
                "workingDirectory": str(RUNTIME_ROOT / "production-runtime"),
            }
        ],
    }
    execution_receipt = {
        "schemaVersion": "NEWS_GRASP_BOOTSTRAP_EXECUTION_RECEIPT_V1",
        "status": "succeeded",
        "issueDate": ISSUE_DATE,
        "observedAt": BOOTSTRAP_OBSERVED_AT,
        "generationId": "generation-20260822",
        "manifestSha256": bootstrap_details["installed_manifest_sha256"],
        "stableAuthoritySha": authority["authoritySha256"],
        "stableAuthorityFileSha256": hashlib.sha256(authority_path.read_bytes()).hexdigest(),
        "taskName": "News-Grasp Bootstrap",
        "originWitness": {
            "taskName": "News-Grasp Bootstrap",
            "source": "scheduled-task",
            "mode": "bootstrap",
        },
        "childExitCode": 0,
    }
    return task_details, bootstrap_details, launcher, execution_receipt


def test_r2_readiness_completion_calls_release_parity_on_exact_live_topology(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """completion path は collector の Production/Bootstrap topology を release gate へ渡す。"""
    release = importlib.import_module("tools.news_grasp_cleanroom_release")
    original_validator = release.validate_live_task_parity
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def validator_spy(*args: Any, **kwargs: Any) -> bool:
        calls.append((args, dict(kwargs)))
        return original_validator(*args, **kwargs)

    monkeypatch.setattr(release, "validate_live_task_parity", validator_spy)
    daily_self_heal = importlib.import_module("tools.daily_self_heal")
    if callable(getattr(daily_self_heal, "validate_live_task_parity", None)):
        monkeypatch.setattr(daily_self_heal, "validate_live_task_parity", validator_spy)
    _patch_readiness_seams(monkeypatch, daily_self_heal, tmp_path)

    launcher = tmp_path / "bin" / "news-grasp-task-launcher.pyw"
    result = daily_self_heal.verify_live_runner_readiness(
        repo_root=tmp_path,
        ops_repo_root=tmp_path,
        date=ISSUE_DATE,
        live_runner_path=tmp_path / "runner.ps1",
        live_watcher_path=tmp_path / "watcher.ps1",
        live_bootstrap_path=tmp_path / "bootstrap.ps1",
        live_task_launcher_path=launcher,
        run_canary=True,
        powershell_exe="pwsh",
    )
    assert result["ok"] is True
    assert len(calls) == 1, "release parity validator must be called exactly once on completion"
    args, kwargs = calls[0]
    assert not kwargs and len(args) == 2
    manifest, live_snapshot = args
    assert [task["taskName"] for task in manifest["tasks"]] == ["News-Grasp Production"]
    assert [task["taskName"] for task in live_snapshot["tasks"]] == ["News-Grasp Production"]
    assert live_snapshot["extraEnabledTasks"] == []
    assert all(
        task.get("taskName") not in {"News-Grasp Deadman", "News-Grasp Runner"}
        for task in live_snapshot["extraEnabledTasks"]
    )
    assert result["scheduled_task"]["bootstrap_definition_ok"] is True
    assert result["scheduled_task"]["bootstrap_last_observation_ok"] is True


@pytest.mark.parametrize(
    ("mutation", "expected_reason"),
    [
        ("stale_issue_date", "bootstrap_last_run_issue_date_stale"),
        ("generation_drift", "execution_receipt_mismatch"),
    ],
    ids=("bootstrap-last-run-time-is-current-issue-date-0555", "bootstrap-generation-is-bound"),
)
def test_r6_bootstrap_last_run_is_issue_date_0555_fresh_and_generation_bound(
    mutation: str,
    expected_reason: str,
    tmp_path: Path,
) -> None:
    daily_self_heal = importlib.import_module("tools.daily_self_heal")
    task_details, bootstrap_details, launcher, execution_receipt = _definition_fixture(
        tmp_path,
        daily_self_heal,
    )
    fresh = daily_self_heal._cleanroom_live_task_definition(
        task_details=task_details,
        bootstrap_details=bootstrap_details,
        live_task_launcher_path=launcher,
        execution_receipt=execution_receipt,
        issue_date=ISSUE_DATE,
        installed_generation_timestamp=INSTALLED_GENERATION_TIMESTAMP,
    )
    assert fresh["ok"] is True
    assert fresh["bootstrapObservationOk"] is True

    mutated = deepcopy(bootstrap_details)
    mutated_receipt = deepcopy(execution_receipt)
    if mutation == "stale_issue_date":
        mutated["last_run_time"] = "2026-08-21T05:55:00+09:00"
        mutated["lastRunTime"] = mutated["last_run_time"]
        mutated["issue_date"] = ISSUE_DATE
        mutated["issueDate"] = ISSUE_DATE
    else:
        mutated["generation_id"] = "generation-old"
        mutated["generationId"] = "generation-old"
        mutated_receipt["generationId"] = "generation-old"
    observed = daily_self_heal._cleanroom_live_task_definition(
        task_details=task_details,
        bootstrap_details=mutated,
        live_task_launcher_path=launcher,
        execution_receipt=mutated_receipt,
        issue_date=ISSUE_DATE,
        installed_generation_timestamp=INSTALLED_GENERATION_TIMESTAMP,
    )
    assert observed["ok"] is True, (
        f"{mutation} keeps the structural definition valid while observation fails closed"
    )
    assert observed["bootstrapObservationOk"] is False
    assert observed["bootstrapObservationReason"] == expected_reason


def test_r7_ledger_renew_slot_is_fenced_and_launcher_renews_periodically() -> None:
    """長い child の renewal は slot/fence を再検証し、stale renewal を閉じる。"""
    ledger = importlib.import_module("tools.news_grasp_cleanroom_ledger")
    control_ledger = getattr(ledger, "ControlLedger", None)
    assert control_ledger is not None, "ControlLedger must remain the ledger authority"
    renew_slot = getattr(control_ledger, "renew_slot", None)
    assert callable(renew_slot), "ControlLedger.renew_slot is the fenced lease renewal seam"
    parameters = inspect.signature(renew_slot).parameters
    assert {"slot_key", "fence_token", "observed_at"}.issubset(parameters), (
        "renew_slot must bind slot_key, fence_token, and observed_at"
    )

    launcher_source = LAUNCHER_PATH.read_text(encoding="utf-8-sig")
    assert "renew_slot" in launcher_source
    assert "fence_token" in launcher_source or "fenceToken" in launcher_source
    assert any(token in launcher_source for token in ("Popen", ".poll(", "monotonic", "sleep(")), (
        "launcher must keep a bounded child loop so lease renewal is periodic"
    )
    assert re.search(r"renew_slot[\s\S]{0,1200}(fence_token|fenceToken)", launcher_source)
    assert "STALE_FENCE" in Path(ledger.__file__).read_text(encoding="utf-8-sig")


def _installer_function_source(function_name: str, next_marker: str) -> str:
    source = INSTALLER_PATH.read_text(encoding="utf-8-sig")
    start = source.index(f"function {function_name}")
    end = source.index(next_marker, start)
    return source[start:end].casefold()


def test_installer_entry_canary_never_uses_empty_trigger_and_quiesces_before_start() -> None:
    """Canary停止中も空Triggerを渡さず、Task停止を先に確定してraceを閉じる。"""
    function_source = _installer_function_source(
        "Invoke-NewsGraspProductionEntryCanary",
        "trap {",
    )
    assert not re.search(
        r"set-scheduledtask[\s\S]{0,300}-trigger\s+@\(\)",
        function_source,
    ), "installer must never pass an empty Trigger collection"
    disable_at = function_source.index("disable-scheduledtask")
    start_at = function_source.index("start-scheduledtask")
    assert disable_at < start_at
    action_update = re.search(
        r"set-scheduledtask[\s\S]{0,300}-action\s+\$entrycanaryaction",
        function_source,
    )
    assert action_update, "canary must update only the action while triggers remain valid"
    assert "register-scheduledtask" in function_source
    assert "productiontasksnapshotxml" in function_source


def test_installer_entry_canary_stops_running_instance_before_snapshot_restore() -> None:
    """Timeout/mismatch時は一時instanceを停止・非Running確認してからXMLを戻す。"""
    helper_source = _installer_function_source(
        "Stop-NewsGraspTaskAndWait",
        "function Invoke-NewsGraspRollbackJournal",
    )
    for token in (
        "disable-scheduledtask",
        "stop-scheduledtask",
        "get-scheduledtask",
        "running",
        "deadline",
        "start-sleep",
    ):
        assert token in helper_source, f"quiescence helper must contain {token}"
    function_source = _installer_function_source(
        "Invoke-NewsGraspProductionEntryCanary",
        "trap {",
    )
    stop_at = function_source.index("stop-newsgrasptaskandwait")
    restore_match = re.search(r"(?m)^\s*register-scheduledtask\b", function_source)
    assert restore_match, "canary must restore the full XML snapshot"
    restore_at = restore_match.start()
    assert stop_at < restore_at


def test_installer_rollbacks_quiesce_tasks_before_files_then_restore_xml() -> None:
    """中断回復と通常rollbackはTaskを先にquiesceし、partial filesを見せない。"""
    helper_source = _installer_function_source(
        "Stop-NewsGraspTaskAndWait",
        "function Invoke-NewsGraspRollbackJournal",
    )
    for token in (
        "disable-scheduledtask",
        "stop-scheduledtask",
        "get-scheduledtask",
        "running",
        "deadline",
        "start-sleep",
    ):
        assert token in helper_source, f"quiescence helper must contain {token}"
    functions = (
        _installer_function_source(
            "Invoke-NewsGraspRollbackJournal",
            "function Recover-NewsGraspInterruptedInstall",
        ),
        _installer_function_source(
            "Invoke-NewsGraspInstallRollback",
            "function Write-NewsGraspInstallJournal",
        ),
    )
    for function_source in functions:
        first_quiesce = function_source.index("stop-newsgrasptaskandwait")
        first_file_restore = min(
            position
            for token in ("restore-newsgraspverifiedfile", "remove-newsgraspverifiedfile")
            for position in [function_source.find(token)]
            if position >= 0
        )
        assert first_quiesce < first_file_restore
        first_xml_restore = function_source.index("register-scheduledtask")
        assert first_file_restore < first_xml_restore


def _load_launcher_module() -> Any:
    loader = importlib.machinery.SourceFileLoader(
        "news_grasp_lane_b_launcher",
        str(LAUNCHER_PATH),
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_cleanroom_child_drains_bounded_pipes_and_renews_during_large_output(
    tmp_path: Path,
) -> None:
    """stdout/stderrがpipe容量を超えてもchildをdrainし、lease renewalを継続する。"""
    launcher = _load_launcher_module()
    command = [
        sys.executable,
        "-c",
        (
            "import sys,time; "
            "payload=b'x'*(128*1024); "
            "sys.stdout.buffer.write(payload); sys.stdout.buffer.flush(); "
            "sys.stderr.buffer.write(payload); sys.stderr.buffer.flush(); "
            "time.sleep(0.20)"
        ),
    ]
    renewals: list[int] = []
    result = launcher._run_cleanroom_child(
        "pipe-drain-test",
        command,
        bin_dir=tmp_path,
        safety={
            "timeout": 2.0,
            "creationflags": 0,
            "owned_process_module": str(ROOT / "tools" / "news_grasp_owned_process.py"),
        },
        renew_slot=lambda: renewals.append(1) or {"status": "renewed"},
        renewal_interval_seconds=0.05,
        renewal_sleep=lambda seconds: __import__("time").sleep(seconds),
    )
    assert result == 0
    assert renewals, "long child must renew the lease while output is drained"
