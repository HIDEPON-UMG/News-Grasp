from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


ISSUE_DATE = "2026-08-05"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _seal(value: dict[str, Any]) -> dict[str, Any]:
    body = dict(value)
    body["receiptSha256"] = hashlib.sha256(_canonical(body)).hexdigest()
    return body


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _write_fixture_broker(profile: Path) -> Path:
    broker = profile / "bin" / "ai-model-spawn-broker.py"
    broker.parent.mkdir(parents=True, exist_ok=True)
    broker.write_text(
        """from __future__ import annotations
import json
import os
from pathlib import Path
import sys

fixture_path = Path(os.environ["NEWS_GRASP_RED_BROKER_FIXTURE"])
fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
log_path = Path(os.environ["NEWS_GRASP_RED_BROKER_ARGV_LOG"])
with log_path.open("a", encoding="utf-8", newline="\\n") as stream:
    stream.write(json.dumps(sys.argv[1:], ensure_ascii=False) + "\\n")
command = sys.argv[1] if len(sys.argv) > 1 else ""
behavior = fixture.get("behavior", {}).get(command, {})
if int(behavior.get("exitCode", 0)) != 0:
    print(str(behavior.get("stderr", "RED_FIXTURE_BROKER_REJECTED")), file=sys.stderr)
    raise SystemExit(int(behavior["exitCode"]))
if "rawStdout" in behavior:
    print(str(behavior["rawStdout"]))
    raise SystemExit(0)
if command == "inspect-news-grasp-attempt":
    value = fixture["inspect"]
elif command == "validate-news-grasp-recovery-authority":
    value = fixture["authorityWitness"]
else:
    print("RED_FIXTURE_BROKER_COMMAND_INVALID", file=sys.stderr)
    raise SystemExit(2)
print(json.dumps(value, ensure_ascii=False, sort_keys=True))
""",
        encoding="utf-8-sig",
        newline="\n",
    )
    return broker


def _failure_evidence(repo: Path) -> tuple[Path, Path, dict[str, Any], dict[str, Any]]:
    evidence_dir = repo / "build" / "recovery" / "authority"
    failure = _seal(
        {
            "schemaVersion": "SCHEDULED_FAILURE_RECEIPT_V1",
            "issueDate": ISSUE_DATE,
            "scheduledAttemptStatus": "failed",
            "lastTaskResult": 76,
            "runnerState": "operation_rejected_high_cost_admission",
            "stateSha256": "1" * 64,
            "logSha256": "2" * 64,
            "taskActionSha256": "3" * 64,
            "runnerSha256": "4" * 64,
        }
    )
    failure_path = evidence_dir / "scheduled-failure.json"
    _write_json(failure_path, failure)
    authority = _seal(
        {
            "schemaVersion": "SCHEDULED_RECOVERY_AUTHORITY_V1",
            "productId": "News-Grasp",
            "issueDate": ISSUE_DATE,
            "operationKind": "scheduled_recovery",
            "runIntent": "ScheduledRecoveryFull",
            "missionAuthoritySha256": "5" * 64,
            "failureReceiptSha256": failure["receiptSha256"],
            "taskActionSha256": "6" * 64,
            "runnerSha256": "7" * 64,
            "failedTaskActionSha256": "3" * 64,
            "failedRunnerSha256": "4" * 64,
            "maxExternalModelCalls": 9,
            "maxFullE2EAttempts": 0,
            "noFocusTheft": True,
            "noUserMonitoring": True,
            "noAutoOpen": True,
        }
    )
    authority_path = evidence_dir / "recovery-authority.json"
    _write_json(authority_path, authority)
    return failure_path, authority_path, failure, authority


def observe_current_audit_decision(
    *,
    repo: Path,
    isolation_root: Path,
    mode: str,
    payload_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """fixture broker境界を通して現行audit CLIの実出力を採取する。"""
    node_suffix = "baseline"
    if payload_extra:
        case_label = str(payload_extra.get("redCaseId") or "case")
        perspective_label = str(
            payload_extra.get("evidencePerspective") or "perspective"
        )
        node_suffix = f"{case_label.lower()}-{perspective_label.lower()}"
    profile = isolation_root / "profile" / f"audit-{mode}-{node_suffix}"
    artifact_dir = isolation_root / "artifacts" / "audit" / mode / node_suffix
    artifact_dir.mkdir(parents=True, exist_ok=True)
    _write_fixture_broker(profile)
    argv_log = artifact_dir / "broker-argv.jsonl"
    argv_log.unlink(missing_ok=True)
    fixture_path = artifact_dir / "broker-fixture.json"
    payload_path = artifact_dir / "input.json"

    behavior: dict[str, Any] = {}
    if mode in {"reserved_incomplete", "human_invalid"}:
        inspect = _seal(
            {
                "schemaVersion": "SCHEDULED_ATTEMPT_LEDGER_WITNESS_V1",
                "productId": "News-Grasp",
                "issueDate": ISSUE_DATE,
                "scheduledAttemptStatus": "reserved",
                "recoveryAttemptStatus": "not_started",
                "scheduledEventSequence": 1,
                "scheduledEventHash": "a" * 64,
            }
        )
        payload: dict[str, Any] = {
            "issueDate": ISSUE_DATE,
            "repairDecision": {"classification": "normal"},
            "humanImpact": {
                "noFocusTheft": True,
                "noUserMonitoring": True,
                "noAutoOpen": True,
            },
        }
        authority_witness: dict[str, Any] = {}
        if mode == "human_invalid":
            payload["humanImpact"] = {
                "noFocusTheft": True,
                "noUserMonitoring": False,
                "noAutoOpen": True,
            }
    elif mode in {"broker_inspect_nonzero", "broker_inspect_malformed"}:
        inspect = {}
        authority_witness = {}
        payload = {
            "issueDate": ISSUE_DATE,
            "repairDecision": {"classification": "recoverable"},
            "humanImpact": {
                "noFocusTheft": True,
                "noUserMonitoring": True,
                "noAutoOpen": True,
            },
        }
        if mode == "broker_inspect_nonzero":
            behavior = {
                "inspect-news-grasp-attempt": {
                    "exitCode": 73,
                    "stderr": "SCHEDULED_ATTEMPT_LEDGER_INVALID",
                }
            }
        else:
            behavior = {
                "inspect-news-grasp-attempt": {"rawStdout": "{invalid-json"}
            }
    elif mode in {"failed_recoverable", "failed_recovery_invalid"}:
        failure_path, authority_path, failure, authority = _failure_evidence(repo)
        inspect = _seal(
            {
                "schemaVersion": "SCHEDULED_ATTEMPT_LEDGER_WITNESS_V1",
                "productId": "News-Grasp",
                "issueDate": ISSUE_DATE,
                "scheduledAttemptStatus": "failed",
                "recoveryAttemptStatus": "not_started",
                "scheduledEventSequence": 1,
                "scheduledEventHash": "a" * 64,
                "failureReceiptSha256": failure["receiptSha256"],
                "failureEventSequence": 2,
                "failureEventHash": "b" * 64,
            }
        )
        authority_witness = _seal(
            {
                "schemaVersion": "SCHEDULED_RECOVERY_AUTHORITY_LEDGER_WITNESS_V1",
                "issueDate": ISSUE_DATE,
                "failureReceiptSha256": failure["receiptSha256"],
                "authorityReceiptSha256": authority["receiptSha256"],
                "ledgerEventSequence": 3,
                "ledgerEventHash": "c" * 64,
            }
        )
        if mode == "failed_recovery_invalid":
            authority_witness["failureReceiptSha256"] = "d" * 64
            authority_witness = _seal(
                {
                    key: value
                    for key, value in authority_witness.items()
                    if key != "receiptSha256"
                }
            )
        payload = {
            "issueDate": ISSUE_DATE,
            "repairDecision": {"classification": "recoverable"},
            "scheduledFailureReceiptPath": str(failure_path),
            "recoveryAuthorityPath": str(authority_path),
            "humanImpact": {
                "noFocusTheft": True,
                "noUserMonitoring": True,
                "noAutoOpen": True,
            },
        }
    else:
        raise ValueError(f"RED_AUDIT_MODE_UNKNOWN: {mode}")

    if payload_extra:
        payload.update(payload_extra)
    _write_json(
        fixture_path,
        {
            "inspect": inspect,
            "authorityWitness": authority_witness,
            "behavior": behavior,
        },
    )
    _write_json(payload_path, payload)
    env = os.environ.copy()
    env.update(
        {
            "USERPROFILE": str(profile),
            "HOME": str(profile),
            "PYTHONPATH": str(repo),
            "NEWS_GRASP_RED_BROKER_FIXTURE": str(fixture_path),
            "NEWS_GRASP_RED_BROKER_ARGV_LOG": str(argv_log),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.audit_recovery_control",
            "decide",
            "--input",
            str(payload_path),
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
        creationflags=CREATE_NO_WINDOW,
    )
    result: dict[str, Any] | None = None
    if completed.stdout.strip():
        result = json.loads(completed.stdout)
    observation = {
        "mode": mode,
        "returnCode": completed.returncode,
        "stdoutSha256": hashlib.sha256(completed.stdout.encode("utf-8")).hexdigest(),
        "stderrSha256": hashlib.sha256(completed.stderr.encode("utf-8")).hexdigest(),
        "result": result,
        "brokerArgv": (
            argv_log.read_text(encoding="utf-8").splitlines()
            if argv_log.exists()
            else []
        ),
        "consumerSources": [
            {
                "path": str(repo / "tools" / "audit_recovery_control.py"),
                "symbol": "main.decide",
            }
        ],
        "inputArtifactPath": str(payload_path),
        "input": payload,
    }
    _write_json(artifact_dir / "observation.json", observation)
    return observation
