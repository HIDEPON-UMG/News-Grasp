from __future__ import annotations

import argparse
import json
import subprocess
import sys
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("runner", "bootstrap"))
    parser.add_argument("--probe", type=Path)
    args = parser.parse_args()
    if args.probe:
        args.probe.parent.mkdir(parents=True, exist_ok=True)
        args.probe.write_text("probe_ok", encoding="utf-8")
        return 0
    bin_dir = Path.home() / "bin"
    script = bin_dir / ("news-grasp-bootstrap.ps1" if args.mode == "runner" else "news-grasp-bootstrap.ps1")
    extra = [
        "-Start", "-UseProductionRuntime", "-ScheduledTaskName", "News-Grasp Runner",
    ] if args.mode == "runner" else [
        "-Start", "-UseProductionRuntime", "-ScheduledTaskName", "News-Grasp Bootstrap",
        "-SmokeTest", "-SkipSourceSync",
        "-PollSeconds", "1", "-TimeoutMinutes", "2",
        "-StateFile", "ng-smoke-state.json", "-LogDir", "ng-smoke-logs",
    ]
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
    if result.returncode != 0:
        freeze_startup_failure_if_needed(
            state_path=failure_state,
            returncode=int(result.returncode),
            issue_date=issue_date,
            detail=f"STARTUP_SELF_REPAIR_FAILED exit={result.returncode}",
        )
        return int(result.returncode)
    if args.mode == "bootstrap":
        state_path = bin_dir / "ng-smoke-state.json"
        try:
            state = json.loads(state_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, TypeError):
            return 73
        if state.get("status") != "smoke_ok":
            return 73
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
