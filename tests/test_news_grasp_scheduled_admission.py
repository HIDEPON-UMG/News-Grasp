from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest


ISSUE_DATE = "2026-08-21"
AUTHORITY_SHA = "b" * 64
HEX32 = "c" * 32
HEX64 = "d" * 64


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _seal(body: dict[str, object]) -> dict[str, object]:
    value = dict(body)
    value["receiptSha256"] = hashlib.sha256(_canonical(value)).hexdigest()
    return value


def _base(*, schema: str, operation_kind: str, max_calls: int) -> dict[str, object]:
    return {
        "schemaVersion": schema,
        "productId": "News-Grasp",
        "issueDate": ISSUE_DATE,
        "authorityKind": "scheduled_news_grasp",
        "taskIdentity": HEX64,
        "latestActualUserEventHash": HEX64,
        "operationKind": operation_kind,
        "operationAuthoritySha256": AUTHORITY_SHA,
        "maxExternalModelCalls": max_calls,
        "attemptReservation": {
            "attemptId": ISSUE_DATE,
            "eventSequence": 1,
            "idempotent": False,
        },
        "taskState": "running",
    }


def _admissions() -> list[dict[str, object]]:
    scheduled = _base(
        schema="HIGH_COST_SCHEDULED_OPERATION_ADMISSION_V1",
        operation_kind="scheduled_production",
        max_calls=9,
    )
    continuation = _base(
        schema="HIGH_COST_SCHEDULED_RECOVERY_CONTINUATION_V1",
        operation_kind="scheduled_recovery",
        max_calls=9,
    )
    continuation.update(
        {
            "sourceAdmissionReceiptSha256": HEX64,
            "sourceRunId": HEX32,
            "sourceRunnerStateSha256": HEX64,
            "sourceTerminalStatus": "blocked_refill_unresolved",
            "resumeStage": "deepdive",
            "allowedModelRoutes": ["deepdive"],
            "continuationEventSequence": 2,
        }
    )
    incident = _base(
        schema="HIGH_COST_SCHEDULED_INCIDENT_REPAIR_V1",
        operation_kind="scheduled_recovery",
        max_calls=10,
    )
    incident.update(
        {
            "sourceAdmissionReceiptSha256": HEX64,
            "sourceRunId": HEX32,
            "sourceRunnerStateSha256": HEX64,
            "sourceTerminalStatus": "blocked_refill_unresolved",
            "allowedModelRoutes": ["repair:incident-publication"],
            "allowedArtifactHashes": {
                f"digest/DeepDive/{ISSUE_DATE}-DeepDive.md": HEX64,
                f"digest/Summary/{ISSUE_DATE}-audio-script.md": HEX64,
            },
            "incidentBudgetEventSequence": 2,
        }
    )
    return [scheduled, continuation, incident]


def _contracts_module():
    from tools import news_grasp_operational_contract

    return news_grasp_operational_contract


def test_scheduled_admission_validator_accepts_only_exact_sealed_three_schema_bodies() -> None:
    """3 admission schemaはcanonical receipt付きexact bodyだけを受理する。"""

    contracts = _contracts_module()
    validator = getattr(contracts, "validate_scheduled_admission_receipt", None)
    assert callable(validator), "RED_SCHEDULED_ADMISSION_VALIDATOR_MISSING"

    for body in _admissions():
        sealed = _seal(body)
        validator(
            sealed,
            expected_operation_kind=str(body["operationKind"]),
            expected_issue_date=ISSUE_DATE,
            expected_operation_authority_sha256=AUTHORITY_SHA,
        )

        tampered = dict(sealed)
        tampered["maxExternalModelCalls"] = int(body["maxExternalModelCalls"]) + 1
        with pytest.raises(ValueError, match="HIGH_COST_SCHEDULED_ADMISSION_INVALID"):
            validator(
                tampered,
                expected_operation_kind=str(body["operationKind"]),
                expected_issue_date=ISSUE_DATE,
                expected_operation_authority_sha256=AUTHORITY_SHA,
            )

        extra = dict(body)
        extra["unexpected"] = "scope-escape"
        extra_sealed = _seal(extra)
        with pytest.raises(ValueError, match="HIGH_COST_SCHEDULED_ADMISSION_INVALID"):
            validator(
                extra_sealed,
                expected_operation_kind=str(body["operationKind"]),
                expected_issue_date=ISSUE_DATE,
                expected_operation_authority_sha256=AUTHORITY_SHA,
            )

        missing_receipt = dict(sealed)
        missing_receipt.pop("receiptSha256")
        with pytest.raises(ValueError, match="HIGH_COST_SCHEDULED_ADMISSION_INVALID"):
            validator(
                missing_receipt,
                expected_operation_kind=str(body["operationKind"]),
                expected_issue_date=ISSUE_DATE,
                expected_operation_authority_sha256=AUTHORITY_SHA,
            )


def test_seal_fresh_broker_admission_rejects_replay_tamper_and_extra_key() -> None:
    """broker出力のfresh unsealed bodyだけを一度sealし、再利用を拒否する。"""

    contracts = _contracts_module()
    sealer = getattr(contracts, "seal_fresh_broker_admission", None)
    assert callable(sealer), "RED_SCHEDULED_ADMISSION_SEALER_MISSING"
    body = _base(
        schema="HIGH_COST_SCHEDULED_OPERATION_ADMISSION_V1",
        operation_kind="scheduled_production",
        max_calls=9,
    )
    sealed = sealer(
        body,
        expected_operation_kind="scheduled_production",
        expected_issue_date=ISSUE_DATE,
        expected_operation_authority_sha256=AUTHORITY_SHA,
    )
    assert sealed["receiptSha256"] == hashlib.sha256(_canonical(body)).hexdigest()

    with pytest.raises(ValueError, match="HIGH_COST_SCHEDULED_ADMISSION_INVALID"):
        sealer(
            sealed,
            expected_operation_kind="scheduled_production",
            expected_issue_date=ISSUE_DATE,
            expected_operation_authority_sha256=AUTHORITY_SHA,
        )

    wrong_context = dict(body)
    wrong_context["operationKind"] = "scheduled_recovery"
    with pytest.raises(ValueError, match="HIGH_COST_SCHEDULED_ADMISSION_INVALID"):
        sealer(
            wrong_context,
            expected_operation_kind="scheduled_production",
            expected_issue_date=ISSUE_DATE,
            expected_operation_authority_sha256=AUTHORITY_SHA,
        )

    extra = dict(body)
    extra["unexpected"] = "scope-escape"
    with pytest.raises(ValueError, match="HIGH_COST_SCHEDULED_ADMISSION_INVALID"):
        sealer(
            extra,
            expected_operation_kind="scheduled_production",
            expected_issue_date=ISSUE_DATE,
            expected_operation_authority_sha256=AUTHORITY_SHA,
        )
