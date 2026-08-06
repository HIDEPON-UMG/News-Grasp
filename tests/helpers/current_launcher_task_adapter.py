from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch


CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CANONICAL_PRODUCTION_TASK_NAME = "News-Grasp Production"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def observe_scheduled_task_action(task_name: str = CANONICAL_PRODUCTION_TASK_NAME) -> dict[str, Any]:
    safe_task_name = task_name.replace("'", "''")
    script = r"""
$task = Get-ScheduledTask -TaskName '__TASK_NAME__' -ErrorAction Stop
$info = Get-ScheduledTaskInfo -TaskName '__TASK_NAME__' -ErrorAction Stop
[pscustomobject]@{
  Execute = [string]$task.Actions[0].Execute
  Arguments = [string]$task.Actions[0].Arguments
  WorkingDirectory = [string]$task.Actions[0].WorkingDirectory
  LastTaskResult = [int]$info.LastTaskResult
  LastRunTime = $info.LastRunTime.ToString('o')
} | ConvertTo-Json -Compress
""".replace("__TASK_NAME__", safe_task_name)
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        timeout=30,
        creationflags=CREATE_NO_WINDOW,
    )
    result = json.loads(completed.stdout) if completed.stdout.strip() else {}
    arguments = str(result.get("Arguments") or "")
    execute = str(result.get("Execute") or "")
    live_runner = Path.home() / "bin" / "news-grasp-runner.ps1"
    launcher = "news-grasp-task-launcher.pyw" in arguments or "news-grasp-task-launcher.pyw" in execute
    bound_task_name = f'--scheduled-task-name "{task_name}"' in arguments or f"--scheduled-task-name {task_name}" in arguments
    return {
        "schemaVersion": "CURRENT_SCHEDULED_TASK_ACTION_OBSERVATION_V1",
        "returnCode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "result": result,
        "taskName": task_name,
        "directRunner": "news-grasp-runner.ps1" in arguments,
        "launcher": launcher,
        "taskIdentityBound": bound_task_name,
        "lineageAuthority": "versioned_launcher" if launcher and bound_task_name else "unbound",
        "consumerSources": [
            {
                "path": str(
                    live_runner
                    if live_runner.is_file()
                    else Path(__file__).resolve()
                ),
                "symbol": f"Windows.TaskScheduler.{task_name}.Action",
            }
        ],
    }


def observe_task_history_recovery(repo: Path) -> dict[str, Any]:
    observation = observe_scheduled_task_action()
    launcher = repo / "scripts" / "ops" / "news-grasp-task-launcher.pyw"
    spec = importlib.util.spec_from_file_location(
        "news_grasp_task_history_consumer", launcher
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("TASK_HISTORY_CONSUMER_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    derived = module.record_missing_pre_attempt_from_task_history(
        dict(observation["result"])
    )
    observation.update(derived)
    observation["consumerSources"] = [
        {
            "path": str(launcher),
            "symbol": "record_missing_pre_attempt_from_task_history",
        }
    ]
    return observation


def observe_launcher_failure(
    *, repo: Path, isolation_root: Path, child_return_code: int = 73
) -> dict[str, Any]:
    launcher = repo / "scripts" / "ops" / "news-grasp-task-launcher.pyw"
    module_name = "current_news_grasp_task_launcher"
    spec = importlib.util.spec_from_file_location(module_name, launcher)
    if spec is None or spec.loader is None:
        raise RuntimeError("LAUNCHER_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    fake_home = isolation_root / "temp" / "launcher-home"
    bin_dir = fake_home / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    bootstrap = bin_dir / "news-grasp-bootstrap.ps1"
    bootstrap.write_text("# harmless launcher fixture\n", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append([str(item) for item in argv])
        return SimpleNamespace(returncode=child_return_code)

    argv_before = list(sys.argv)
    try:
        sys.argv = [str(launcher), "runner"]
        with patch.object(module.Path, "home", return_value=fake_home), patch.object(
            module.subprocess, "run", side_effect=fake_run
        ):
            return_code = int(module.main())
    finally:
        sys.argv = argv_before
    log_path = bin_dir / "news-grasp-task-launcher.log"
    wal_path = bin_dir / "news-grasp-task-launcher-wal.json"
    wal = (
        json.loads(wal_path.read_text(encoding="utf-8"))
        if wal_path.is_file()
        else {}
    )
    return {
        "schemaVersion": "CURRENT_LAUNCHER_OBSERVATION_V1",
        "launcherPath": str(launcher),
        "launcherSha256": _sha256(launcher),
        "consumerSources": [
            {"path": str(launcher), "symbol": "main"}
        ],
        "returnCode": return_code,
        "childReturnCode": child_return_code,
        "childArgv": calls,
        "logExists": log_path.is_file(),
        "startupState": wal.get("preAttemptStatus", "unverified"),
        "continuationState": wal.get("continuationState", "ABSENT"),
        "walClosed": wal.get("walClosed", False),
        "observerReconstructable": wal.get("observerReconstructable", False),
        "rootOperationId": wal.get("rootOperationId", "ABSENT"),
        "launchKey": wal.get("launchKey", "ABSENT"),
        "preAttemptStatus": wal.get("preAttemptStatus", "ABSENT"),
        "scheduledRecoveryFullAuthorityProvable": wal.get(
            "scheduledRecoveryFullAuthorityProvable", False
        ),
        "walPath": str(wal_path),
        "input": {
            "childReturnCode": child_return_code,
            "argv": [str(launcher), "runner"],
        },
    }
