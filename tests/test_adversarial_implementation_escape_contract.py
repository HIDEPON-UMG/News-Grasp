from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.helpers.current_completion_consumer_adapter import (
    observe_current_completion_consumer,
)
from tests.helpers.historical_goal_replay_adapter import (
    _derive_pillars,
    observe_historical_goal_replay,
)
from tools.news_grasp_operational_contract import (
    OPERATIONAL_TRUTH_ISSUER,
    finalize_audit_decision,
    select_recovery_branch_from_truth,
)
from tools import audit_recovery_control


ROOT = Path(__file__).resolve().parents[1]
ISOLATION_ROOT = ROOT.parent
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _text_sha(value: str) -> str:
    return hashlib.sha256(value.strip().encode("utf-8")).hexdigest()


def test_goal_binding_uses_raw_latest_user_requirement_hash() -> None:
    observation = observe_historical_goal_replay(
        case_id="S126", perspective="primary"
    )
    result = observation["result"]
    actual_requirement = str(result["latestActualUserRequirement"])
    assert result["latestActualUserRequirementHash"] == _text_sha(
        actual_requirement
    )
    binding = result["goal"]["requirementBinding"]
    assert binding["actualUserRecordSha256"] == observation[
        "latestRequirementRecordSha256"
    ]
    assert binding["objectiveSha256"] == result["goal"]["objectiveHash"]
    assert binding["semanticBindingSha256"] == hashlib.sha256(
        json.dumps(
            {
                "actualUserRecordSha256": binding[
                    "actualUserRecordSha256"
                ],
                "objectiveSha256": binding["objectiveSha256"],
                "requirementIds": binding["requirementIds"],
                "semanticClaims": binding["semanticClaims"],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def test_goal_pillars_reject_negated_keyword_stuffing() -> None:
    attack = (
        "06:00 production daily batchは、回復可能な異常で処理を放棄せず"
        "当日public outcomeまで自己修復して完走することを禁止する。"
        "06:40 audit/recovery batchは、その復旧を報告、恒久対策、test、"
        "harness、incident polishより絶対に優先することを禁止し、"
        "deferredやreportをterminalにしない方針も採用しない。"
    )
    assert _derive_pillars(attack) == []


def test_recovery_branch_cannot_be_selected_by_caller_label() -> None:
    decision = {
        "schemaVersion": "AUDIT_RECOVERY_DECISION_V1",
        "issueDate": "2026-08-05",
        "scheduledAttemptStatus": "failed",
        "recoveryAttemptStatus": "not_started",
        "publicStatus": "incomplete",
        "action": "scheduled_recovery",
        "terminal": None,
        "reasonCode": "TYPED_RECOVERY_AUTHORITY_READY",
    }
    evidence = {
        "schemaVersion": "NEWS_GRASP_OPERATIONAL_TRUTH_V1",
        "issuer": OPERATIONAL_TRUTH_ISSUER,
        "stopPointKnown": True,
        "scheduledAttemptReachedRunner": False,
        "artifactDelta": {
            "exists": False,
            "manifestSha256": "1" * 64,
        },
        "failureReceiptSha256": "2" * 64,
        "authorityReceiptSha256": "3" * 64,
    }
    evidence["receiptSha256"] = hashlib.sha256(
        json.dumps(
            evidence,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    branches = {
        finalize_audit_decision(
            {
                "issueDate": "2026-08-05",
                "repairDecision": {"classification": "recoverable"},
                "_verifiedOperationalTruth": evidence,
                "requestedRecoveryBranch": injected,
                "competingWorkOrder": "harness_mutation",
            },
            decision,
        )["recoveryBranch"]
        for injected in (
            "ResumeFromStage",
            "ScheduledRecoveryFull",
            "minimal_unblocker",
        )
    }
    assert branches == {"ScheduledRecoveryFull"}


def test_minimal_unblocker_requires_actual_single_notification_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    state_path = tmp_path / "bin" / "news-grasp-runner-state.json"
    issue_date = "2026-08-05"
    present = (
        repo / "digest" / "Summary" / f"{issue_date}.md",
        repo / "digest" / "DeepDive" / f"{issue_date}-DeepDive.md",
        repo / "docs" / issue_date / "index.html",
        repo / "data" / "distribution" / f"{issue_date}.json",
    )
    for path in present:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("evidence\n", encoding="utf-8")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "date": issue_date,
                "run_id": "scheduled-20260805",
                "run_intent": "ScheduledProduction",
                "phase": "notification_pending",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(audit_recovery_control, "CANONICAL_REPO_ROOT", repo)
    monkeypatch.setattr(
        audit_recovery_control, "CANONICAL_RUNNER_STATE_PATH", state_path
    )
    monkeypatch.setattr(
        audit_recovery_control,
        "_verify_public_without_notification",
        lambda **_: audit_recovery_control._sealed(
            {
                "schemaVersion": "SAME_DATE_PUBLIC_WITHOUT_NOTIFICATION_V1",
                "issuer": audit_recovery_control.VERIFIED_COMPLETION_ISSUER,
                "issueDate": issue_date,
                "publishManifestSha256": "f" * 64,
                "publishCommit": "a" * 40,
                "publicStatus": "green_without_notification",
            }
        ),
    )
    witness = audit_recovery_control._sealed(
        {
            "schemaVersion": "SCHEDULED_ATTEMPT_LEDGER_WITNESS_V1",
            "productId": "News-Grasp",
            "issueDate": issue_date,
            "scheduledAttemptStatus": "failed",
            "recoveryAttemptStatus": "not_started",
            "scheduledEventSequence": 1,
            "scheduledEventHash": "a" * 64,
        }
    )
    truth = audit_recovery_control._observe_operational_truth(
        issue_date=issue_date, attempt_witness=witness
    )
    assert truth.get("minimalUnblockerReceiptSha256")
    assert select_recovery_branch_from_truth(truth) == "minimal_unblocker"


def _run_hook(path: Path, payload: dict[str, object]) -> subprocess.CompletedProcess[str]:
    command = (
        ["pwsh.exe", "-NoProfile", "-File", str(path)]
        if path.suffix.lower() == ".ps1"
        else [sys.executable, str(path)]
    )
    return subprocess.run(
        command,
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        env=os.environ.copy(),
        creationflags=CREATE_NO_WINDOW,
        timeout=30,
    )


def test_fixture_only_hook_keys_cannot_change_production_verdict(
    tmp_path: Path,
) -> None:
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "event_msg",
                "payload": {
                    "type": "user_message",
                    "message": "News-Graspのroot fixを実装する。",
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    hook = Path.home() / ".codex" / "hooks" / "require_plan_or_todo_before_modification.py"
    base = {
        "tool_name": "functions.apply_patch",
        "call_id": "production-route",
        "tool_input": {"patch": "*** Begin Patch\n*** End Patch"},
        "transcript_path": str(transcript),
        "cwd": str(ROOT),
    }
    injected = json.loads(json.dumps(base))
    injected["tool_input"]["newsGraspScenario"] = {
        "plannedMutation": "incident_report_only",
        "publicContribution": "none",
    }
    plain_result = _run_hook(hook, base)
    injected_result = _run_hook(hook, injected)
    assert injected_result.returncode == plain_result.returncode
    assert injected_result.stdout == plain_result.stdout
    assert "valueBearingWork" not in injected_result.stdout


def test_completion_requires_producer_originated_lineage() -> None:
    observation = observe_current_completion_consumer(
        repo=ROOT,
        isolation_root=ISOLATION_ROOT,
        perspective="primary",
    )
    producer = observation["producerManifest"]
    completion = observation["manifest"]
    assert observation["producerLineageProcessReturnCode"] == 0
    assert observation["accepted"] is True
    for field in (
        "artifactRoot",
        "opsRoot",
        "dailyRootId",
        "rootOperationId",
        "producerOperationId",
        "producerRunIntent",
        "lineageReceiptSha256",
    ):
        assert producer[field] == completion[field]
    for perspective in ("adversarial", "recovery"):
        rejected = observe_current_completion_consumer(
            repo=ROOT,
            isolation_root=ISOLATION_ROOT,
            perspective=perspective,
        )
        assert rejected["producerLineageProcessReturnCode"] == 0
        assert rejected["accepted"] is False
