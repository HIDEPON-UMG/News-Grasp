from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any


POWERSHELL = Path(
    r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
)
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    return value if isinstance(value, dict) else {}


@lru_cache(maxsize=None)
def _observe_cached(
    repo_text: str,
    isolation_root_text: str,
    mode: str,
    seed_status: str,
) -> dict[str, Any]:
    repo = Path(repo_text)
    isolation_root = Path(isolation_root_text)
    temp_parent = isolation_root / "temp" / "runner-observations"
    temp_parent.mkdir(parents=True, exist_ok=True)
    case_root = Path(
        tempfile.mkdtemp(prefix=f"{mode}-", dir=str(temp_parent))
    )
    log_dir = case_root / "logs"
    log_dir.mkdir()
    state_path = case_root / "runner-state.json"
    if seed_status:
        state_path.write_text(
            json.dumps(
                {
                    "status": seed_status,
                    "message": "seeded prior public meaning",
                    "exit_code": 0,
                    "date": "2026-08-05",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    authority_path = case_root / "launch-permit.json"
    if mode != "authority_missing":
        authority_path.write_text(
            json.dumps(
                {
                    "schemaVersion": "ISOLATED_RUNNER_AUTHORITY_STUB_V1",
                    "issueDate": "2026-08-05",
                }
            ),
            encoding="utf-8",
        )
    runner = repo / "scripts" / "ops" / "news-grasp-runner.ps1"
    broker_stub = (
        repo
        / "tests"
        / "fixtures"
        / "autonomous_operations"
        / "stub_model_spawn_broker.py"
    )
    env = os.environ.copy()
    env["NEWS_GRASP_BROKER_MODE"] = mode
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    command = [
        str(POWERSHELL),
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(runner),
        "-RepoDirOverride",
        str(repo),
        "-CodexExeOverride",
        str(Path(os.environ.get("ComSpec", r"C:\Windows\System32\cmd.exe"))),
        "-PyExeOverride",
        sys.executable,
        "-DateStampOverride",
        "2026-08-05",
        "-LogDirOverride",
        str(log_dir),
        "-StateFileOverride",
        str(state_path),
        "-HighCostBudgetToolPath",
        str(broker_stub),
        "-HighCostWorkspaceRoot",
        str(isolation_root / "workspace-harness"),
        "-ScheduledAuthorityEvidencePath",
        str(authority_path),
        "-ScheduledFailureReceiptRootOverride",
        str(case_root / "authoritative-failure-receipts"),
    ]
    completed = subprocess.run(
        command,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        timeout=45,
        env=env,
        cwd=str(repo),
        creationflags=CREATE_NO_WINDOW,
    )
    log_path = log_dir / "2026-08-05.log"
    state = _read_json(state_path)
    log_text = (
        log_path.read_text(encoding="utf-8-sig", errors="replace")
        if log_path.is_file()
        else ""
    )
    return {
        "schemaVersion": "CURRENT_RUNNER_OBSERVATION_V1",
        "mode": mode,
        "seedStatus": seed_status or None,
        "runnerPath": str(runner),
        "runnerSha256": _sha256(runner),
        "consumerSources": [
            {"path": str(runner), "symbol": "news-grasp-runner.ps1"}
        ],
        "brokerStubPath": str(broker_stub),
        "brokerStubSha256": _sha256(broker_stub),
        "argv": command,
        "returnCode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "statePath": str(state_path),
        "state": state,
        "logPath": str(log_path),
        "log": log_text,
        "failureReceiptExists": any(
            path.is_file()
            for path in case_root.rglob("*failure*receipt*.json")
        ),
        "continuationState": state.get("continuationState", "ABSENT"),
        "controlEventFields": {
            field: state.get(field, "ABSENT")
            for field in ("eventSequence", "previousEventHash", "dailyRootId")
        },
        "stateVector": {
            field: state.get(field, "ABSENT")
            for field in (
                "preAttemptStatus",
                "scheduledAttemptStatus",
                "recoveryAttemptStatus",
                "productionPublicOutcomeStatus",
            )
        },
        "input": {
            "mode": mode,
            "seedStatus": seed_status or None,
            "argv": command,
            "authorityArtifactPath": str(authority_path),
        },
    }


def observe_current_runner_failure(
    *,
    repo: Path,
    isolation_root: Path,
    mode: str,
    seed_status: str = "",
) -> dict[str, Any]:
    return _observe_cached(
        str(repo.resolve()),
        str(isolation_root.resolve()),
        mode,
        seed_status,
    )


def observe_parallel_runner_failures(
    *, repo: Path, isolation_root: Path
) -> dict[str, Any]:
    temp_parent = isolation_root / "temp" / "runner-parallel-observations"
    temp_parent.mkdir(parents=True, exist_ok=True)
    case_root = Path(tempfile.mkdtemp(prefix="parallel-", dir=str(temp_parent)))
    log_dir = case_root / "logs"
    log_dir.mkdir()
    state_path = case_root / "shared-runner-state.json"
    runner = repo / "scripts" / "ops" / "news-grasp-runner.ps1"
    broker_stub = (
        repo
        / "tests"
        / "fixtures"
        / "autonomous_operations"
        / "stub_model_spawn_broker.py"
    )
    commands: list[list[str]] = []
    for index in (1, 2):
        authority_path = case_root / f"launch-permit-{index}.json"
        authority_path.write_text(
            json.dumps(
                {
                    "schemaVersion": "ISOLATED_RUNNER_AUTHORITY_STUB_V1",
                    "issueDate": "2026-08-05",
                    "index": index,
                }
            ),
            encoding="utf-8",
        )
        commands.append(
            [
                str(POWERSHELL),
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(runner),
                "-RepoDirOverride",
                str(repo),
                "-CodexExeOverride",
                str(
                    Path(
                        os.environ.get(
                            "ComSpec", r"C:\Windows\System32\cmd.exe"
                        )
                    )
                ),
                "-PyExeOverride",
                sys.executable,
                "-DateStampOverride",
                "2026-08-05",
                "-LogDirOverride",
                str(log_dir),
                "-StateFileOverride",
                str(state_path),
                "-HighCostBudgetToolPath",
                str(broker_stub),
                "-HighCostWorkspaceRoot",
                str(isolation_root / "workspace-harness"),
                "-ScheduledAuthorityEvidencePath",
                str(authority_path),
                "-ScheduledFailureReceiptRootOverride",
                str(case_root / "authoritative-failure-receipts"),
            ]
        )
    env = os.environ.copy()
    env["NEWS_GRASP_BROKER_MODE"] = "reject_exit_1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    processes = [
        subprocess.Popen(
            command,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(repo),
            env=env,
            creationflags=CREATE_NO_WINDOW,
        )
        for command in commands
    ]
    outputs = [process.communicate(timeout=45) for process in processes]
    return_codes = [int(process.returncode) for process in processes]
    state = _read_json(state_path)
    events = state.get("immutableControlEvents")
    if not isinstance(events, list):
        events = []
    authoritative_receipts = [
        value
        for path in sorted((case_root / "authoritative-failure-receipts").glob("*.json"))
        if (value := _read_json(path)).get("schemaVersion")
        == "SCHEDULED_FAILURE_RECEIPT_V1"
    ]
    combined_output = "\n".join(
        f"{stdout}\n{stderr}" for stdout, stderr in outputs
    )
    return {
        "schemaVersion": "CURRENT_PARALLEL_RUNNER_OBSERVATION_V1",
        "runnerPath": str(runner),
        "runnerSha256": _sha256(runner),
        "consumerSources": [
            {"path": str(runner), "symbol": "news-grasp-runner.ps1"}
        ],
        "returnCodes": return_codes,
        "outputs": [
            {"stdout": stdout, "stderr": stderr}
            for stdout, stderr in outputs
        ],
        "state": state,
        "immutableControlEvents": events,
        "retainedFailureEventCount": sum(
            1
            for event in events
            if isinstance(event, dict)
            and event.get("eventType") == "scheduled_attempt_failed"
        ),
        "authoritativeFailureReceiptCount": len(authoritative_receipts),
        "failureReceiptRunIds": sorted(
            str(receipt.get("runId") or "") for receipt in authoritative_receipts
        ),
        "terminalizerFailureCount": combined_output.count(
            "SCHEDULED_FAILURE_TERMINALIZER_FAILED"
        ),
        "controlTransitionPredicate": (
            "append_only_hash_chain" if events else "ABSENT"
        ),
        "input": {
            "argv": commands,
            "concurrency": 2,
            "sharedStatePath": str(state_path),
        },
    }
