from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4


def write_startup_failure_state(
    *, state_path: Path, returncode: int, issue_date: str, detail: str
) -> None:
    """runner 到達前の失敗を、6:40 監査が回収できる fixed state に凍結する。"""
    now = datetime.now().astimezone().isoformat(timespec="milliseconds")
    payload = {
        "status": "blocked_startup_self_repair_failed",
        "message": detail,
        "exit_code": int(returncode),
        "updated_at": now,
        "heartbeat_at": now,
        "date": issue_date,
        "run_intent": "ScheduledProduction",
        "run_id": f"launcher-{uuid4().hex}",
        "phase": "startup_self_repair",
        "attempt_terminal": True,
        "recovery_class": "startup_self_repair_failure",
        "scheduled_attempt_status": "failed",
        "recovery_attempt_status": "not_started",
    }
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_path.with_name(f".{state_path.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(state_path)


def freeze_startup_failure_if_needed(
    *, state_path: Path, returncode: int, issue_date: str, detail: str
) -> None:
    try:
        current = json.loads(state_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError):
        current = {}
    if (
        current.get("date") == issue_date
        and current.get("run_intent") == "ScheduledProduction"
        and isinstance(current.get("exit_code"), int)
        and current["exit_code"] > 0
        and current.get("status") != "running"
    ):
        return
    write_startup_failure_state(
        state_path=state_path,
        returncode=returncode,
        issue_date=issue_date,
        detail=detail,
    )


def _write_json_atomic(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    temp.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temp.replace(path)


def _pre_attempt_identity(mode: str, script: Path) -> dict[str, object]:
    launch_evidence = {
        "mode": mode,
        "launcherPath": str(Path(__file__).resolve()),
        "scriptPath": str(script.resolve()),
        "processId": os.getpid(),
        "processStartNonce": time.time_ns(),
    }
    launch_key = hashlib.sha256(
        json.dumps(
            launch_evidence,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    root_operation_id = hashlib.sha256(
        f"News-Grasp|{launch_key}|root-operation".encode("utf-8")
    ).hexdigest()
    return {
        "schemaVersion": "NEWS_GRASP_PRE_ATTEMPT_WAL_V1",
        "launchKey": launch_key,
        "rootOperationId": root_operation_id,
        "preAttemptStatus": "launch_reserved",
        "continuationState": "pre_controller_running",
        "walClosed": False,
        "observerReconstructable": True,
        "scheduledRecoveryFullAuthorityProvable": False,
        "launchEvidence": launch_evidence,
    }


def record_missing_pre_attempt_from_task_history(
    task_evidence: dict[str, object],
) -> dict[str, object]:
    """launcher/broker未到達時のidentityをTask Scheduler一次証拠から復元する。"""
    required = ("Execute", "Arguments", "LastRunTime", "LastTaskResult")
    if any(task_evidence.get(field) in {None, ""} for field in required):
        raise ValueError("TASK_HISTORY_EVIDENCE_INCOMPLETE")
    evidence = {field: task_evidence[field] for field in required}
    launch_key = hashlib.sha256(
        json.dumps(
            evidence,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    root_operation_id = hashlib.sha256(
        f"News-Grasp|{launch_key}|root-operation".encode("utf-8")
    ).hexdigest()
    failed = int(task_evidence["LastTaskResult"]) != 0
    return {
        "schemaVersion": "NEWS_GRASP_TASK_HISTORY_PRE_ATTEMPT_V1",
        "launchKey": launch_key,
        "rootOperationId": root_operation_id,
        "preAttemptStatus": (
            "failed_before_attempt" if failed else "task_action_completed"
        ),
        "scheduledRecoveryFullAuthorityProvable": failed,
        "callerAttemptIdentityAccepted": False,
        "taskEvidenceSha256": hashlib.sha256(
            json.dumps(
                evidence,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("runner", "bootstrap"))
    parser.add_argument("--probe", type=Path)
    parser.add_argument("--repo-dir", type=Path)
    parser.add_argument("--python-exe", type=Path)
    parser.add_argument("--evidence-repo-dir", type=Path)
    parser.add_argument("--scheduled-task-name", required=False)
    args = parser.parse_args()
    if args.probe:
        args.probe.parent.mkdir(parents=True, exist_ok=True)
        args.probe.write_text("probe_ok", encoding="utf-8")
        return 0
    bin_dir = Path.home() / "bin"
    script = bin_dir / "news-grasp-bootstrap.ps1"
    extra = [
        "-Start", "-UseProductionRuntime", "-ScheduledTaskName", "News-Grasp Runner",
        "-ProductionTaskName", "News-Grasp Production",
    ] if args.mode == "runner" else [
        "-Start", "-UseProductionRuntime", "-ScheduledTaskName", "News-Grasp Bootstrap",
        "-ProductionTaskName", "News-Grasp Production",
        "-SmokeTest",
        "-SkipSourceSync",
        "-PollSeconds", "1", "-TimeoutMinutes", "2",
        "-StateFile", "ng-smoke-state.json", "-LogDir", "ng-smoke-logs",
    ]
    if args.scheduled_task_name:
        extra[extra.index("-ScheduledTaskName") + 1] = args.scheduled_task_name
    runtime_repo = args.repo_dir
    runtime_python = args.python_exe
    runtime_evidence: Path | None = args.evidence_repo_dir
    if runtime_repo is None:
        runtime_config = bin_dir / "news-grasp-runtime-root-v1.json"
        if runtime_config.is_file():
            try:
                config = json.loads(runtime_config.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError, TypeError):
                return 66
            if set(config) != {"schemaVersion", "repoDir", "pythonExe", "evidenceRepoDir"} or config.get("schemaVersion") != "NEWS_GRASP_RUNTIME_ROOT_V1":
                return 66
            runtime_repo = Path(str(config.get("repoDir", "")))
            runtime_python = Path(str(config.get("pythonExe", "")))
            runtime_evidence = Path(str(config.get("evidenceRepoDir", "")))
    if runtime_repo is not None:
        try:
            repo_dir = runtime_repo.resolve(strict=True)
        except OSError:
            return 66
        if not (repo_dir / "tools" / "daily_self_heal.py").is_file():
            return 66
        extra.extend(["-RepoDir", str(repo_dir)])
        if runtime_python is None:
            return 66
        try:
            python_exe = runtime_python.resolve(strict=True)
        except OSError:
            return 66
        if not python_exe.is_file():
            return 66
        extra.extend(["-PythonExe", str(python_exe)])
        if runtime_evidence is None:
            runtime_evidence = repo_dir
        try:
            evidence_repo = runtime_evidence.resolve(strict=True)
        except OSError:
            return 66
        extra.extend(["-EvidenceRepoDir", str(evidence_repo)])
    powershell = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
    issue_date = date.today().isoformat()
    failure_state = bin_dir / (
        "news-grasp-runner-state.json" if args.mode == "runner" else "ng-smoke-state.json"
    )
    if not script.is_file():
        freeze_startup_failure_if_needed(
            state_path=failure_state,
            returncode=66,
            issue_date=issue_date,
            detail="STARTUP_SCRIPT_MISSING",
        )
        return 66
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    log = bin_dir / "news-grasp-task-launcher.log"
    wal = bin_dir / "news-grasp-task-launcher-wal.json"
    pre_attempt = _pre_attempt_identity(args.mode, script)
    _write_json_atomic(wal, pre_attempt)
    with log.open("a", encoding="utf-8", errors="replace") as stream:
        result = subprocess.run(
            [str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script), *extra],
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
            check=False,
        )
    effective_returncode = int(result.returncode)
    if effective_returncode == 0 and args.mode == "bootstrap":
        state_path = bin_dir / "ng-smoke-state.json"
        try:
            state = json.loads(state_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, TypeError):
            effective_returncode = 73
        else:
            if state.get("status") != "smoke_ok":
                effective_returncode = 73
    if effective_returncode != 0:
        freeze_startup_failure_if_needed(
            state_path=failure_state,
            returncode=effective_returncode,
            issue_date=issue_date,
            detail=f"STARTUP_SELF_REPAIR_FAILED exit={effective_returncode}",
        )
    pre_attempt.update(
        {
            "childReturnCode": effective_returncode,
            "preAttemptStatus": (
                "controller_started"
                if effective_returncode == 0
                else "failed_before_attempt"
            ),
            "continuationState": (
                "controller_owns_continuation"
                if effective_returncode == 0
                else "scheduled_recovery_required"
            ),
            "walClosed": True,
            "scheduledRecoveryFullAuthorityProvable": effective_returncode != 0,
        }
    )
    _write_json_atomic(wal, pre_attempt)
    return effective_returncode


if __name__ == "__main__":
    raise SystemExit(main())
