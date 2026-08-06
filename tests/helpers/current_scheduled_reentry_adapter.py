from __future__ import annotations

import hashlib
import importlib.util
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Any


ISSUE_DATE = "2026-08-05"


def _load_control(workspace_harness: Path) -> tuple[ModuleType, Path]:
    source = workspace_harness / "tools" / "harness" / "high_cost_control_v2.py"
    spec = importlib.util.spec_from_file_location(
        "isolated_scheduled_reentry_high_cost_control_v2", source
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("SCHEDULED_REENTRY_CONTROL_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module, source


def observe_scheduled_reentry(
    *, workspace_harness: Path, isolation_root: Path, perspective: str
) -> dict[str, Any]:
    control, source = _load_control(workspace_harness)
    runtime_parent = isolation_root / "artifacts" / "red-runtime"
    runtime_parent.mkdir(parents=True, exist_ok=True)
    runtime_root = Path(tempfile.mkdtemp(prefix="s121-", dir=str(runtime_parent)))
    store = control.HighCostControlStore.create_for_test(
        runtime_root / "ledger.sqlite3", control.MemoryAnchor()
    )
    try:
        production = control.admit_scheduled_news_grasp_operation_in_store(
            store=store,
            issue_date=ISSUE_DATE,
            operation_kind="scheduled_production",
        )
        failure = control.record_scheduled_news_grasp_failure_in_store(
            store=store,
            issue_date=ISSUE_DATE,
            run_id="current-scheduled-reentry",
            last_task_result=76,
            runner_state="operation_rejected_high_cost_admission",
            state_sha256="1" * 64,
            log_sha256="2" * 64,
            task_action_sha256="3" * 64,
            runner_sha256="4" * 64,
            failure_stage="high_cost_admission",
        )
        result: dict[str, Any]
        return_code = 0
        if perspective == "primary":
            try:
                control.admit_scheduled_news_grasp_operation_in_store(
                    store=store,
                    issue_date=ISSUE_DATE,
                    operation_kind="scheduled_production",
                )
            except Exception as error:
                return_code = 2
                result = {
                    "reason": str(error),
                    "freshScheduledReentryAccepted": False,
                }
            else:
                result = {"freshScheduledReentryAccepted": True}
        elif perspective == "adversarial":
            mission = control.issue_news_grasp_audit_mission_authority(
                [
                    {"eventSha256": event_hash}
                    for event_hash in control.NEWS_GRASP_AUDIT_MISSION_EVENT_HASHES
                ]
            )
            permit = control.issue_scheduled_production_launch_permit(
                issue_date=ISSUE_DATE,
                task_action_sha256="3" * 64,
                runner_sha256="4" * 64,
                launch_nonce="same-launch-nonce",
                mission_authority=mission,
            )
            try:
                control.validate_scheduled_operation_authority_evidence(
                    operation_kind="scheduled_production",
                    issue_date=ISSUE_DATE,
                    authority_evidence=permit,
                    expected_task_action_sha256="3" * 64,
                    expected_runner_sha256="4" * 64,
                    seen_nonces={"same-launch-nonce"},
                )
            except Exception as error:
                return_code = 2
                result = {"reason": str(error), "replayedNonceAccepted": False}
            else:
                result = {"replayedNonceAccepted": True}
        elif perspective == "recovery":
            mission = control.issue_news_grasp_audit_mission_authority(
                [
                    {"eventSha256": event_hash}
                    for event_hash in control.NEWS_GRASP_AUDIT_MISSION_EVENT_HASHES
                ]
            )
            recovery_authority = control.derive_scheduled_recovery_authority_in_store(
                store=store,
                issue_date=ISSUE_DATE,
                mission_authority=mission,
                failure_receipt=failure,
                run_intent="ScheduledRecoveryFull",
                current_task_action_sha256="3" * 64,
                current_runner_sha256="4" * 64,
            )
            recovery = control.admit_scheduled_news_grasp_operation_in_store(
                store=store,
                issue_date=ISSUE_DATE,
                operation_kind="scheduled_recovery",
                authority_evidence=recovery_authority,
            )
            result = {
                "selectedAuthority": recovery["operationKind"],
                "runIntent": recovery_authority["runIntent"],
                "failureReceiptSha256": failure["receiptSha256"],
            }
        else:
            raise ValueError(f"S121_PERSPECTIVE_UNKNOWN:{perspective}")
        return {
            "schemaVersion": "CURRENT_SCHEDULED_REENTRY_OBSERVATION_V1",
            "returnCode": return_code,
            "perspective": perspective,
            "result": result,
            "productionAdmissionSha256": production["receiptSha256"],
            "failureReceiptSha256": failure["receiptSha256"],
            "consumerSha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "input": {"issueDate": ISSUE_DATE, "perspective": perspective},
            "consumerSources": [
                {
                    "path": str(source),
                    "symbol": (
                        "admit_scheduled_news_grasp_operation_in_store+"
                        "validate_scheduled_operation_authority_evidence"
                    ),
                }
            ],
        }
    finally:
        store.close()
