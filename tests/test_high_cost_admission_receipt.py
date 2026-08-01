from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.high_cost_admission_receipt import (
    HighCostAdmissionReceiptError,
    validate_admission_receipt,
)


def _receipt(tmp_path: Path, **overrides: object) -> Path:
    value: dict[str, object] = {
        "schemaVersion": "HIGH_COST_OPERATION_ADMISSION_V2",
        "taskIdentity": "goal-1",
        "latestActualUserEventHash": "a" * 64,
        "operationKind": "full_e2e",
        "fullE2EAttemptReservation": {
            "attemptId": "nopublish:2026-08-01",
            "eventSequence": 7,
            "idempotent": False,
        },
        "taskState": "running",
    }
    value.update(overrides)
    path = tmp_path / "admission.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_valid_wrapper_reservation_is_accepted_once_by_runner(tmp_path: Path) -> None:
    value = validate_admission_receipt(
        _receipt(tmp_path),
        expected_operation_kind="full_e2e",
        expected_attempt_id="nopublish:2026-08-01",
    )
    assert value["fullE2EAttemptReservation"]["eventSequence"] == 7


def _assert_rejected(tmp_path: Path, expected_error: str, **overrides: object) -> None:
    with pytest.raises(HighCostAdmissionReceiptError, match=expected_error):
        validate_admission_receipt(
            _receipt(tmp_path, **overrides),
            expected_operation_kind="full_e2e",
            expected_attempt_id="nopublish:2026-08-01",
        )


def test_receipt_rejects_operation_drift(tmp_path: Path) -> None:
    _assert_rejected(
        tmp_path,
        "HIGH_COST_ADMISSION_OPERATION_DRIFT",
        operationKind="resume_model",
    )


def test_receipt_rejects_attempt_identity_drift(tmp_path: Path) -> None:
    _assert_rejected(
        tmp_path,
        "HIGH_COST_ADMISSION_ATTEMPT_DRIFT",
        fullE2EAttemptReservation={
            "attemptId": "nopublish:2026-08-02",
            "eventSequence": 7,
            "idempotent": False,
        },
    )


def test_receipt_requires_real_full_e2e_reservation(tmp_path: Path) -> None:
    _assert_rejected(
        tmp_path,
        "HIGH_COST_ADMISSION_RESERVATION_REQUIRED",
        fullE2EAttemptReservation=None,
    )


def test_receipt_rejects_idempotent_replay_shape(tmp_path: Path) -> None:
    _assert_rejected(
        tmp_path,
        "HIGH_COST_ADMISSION_REPLAY_RECEIPT",
        fullE2EAttemptReservation={
            "attemptId": "nopublish:2026-08-01",
            "eventSequence": 7,
            "idempotent": True,
        },
    )


def test_receipt_requires_running_task_state(tmp_path: Path) -> None:
    _assert_rejected(
        tmp_path,
        "HIGH_COST_ADMISSION_NOT_RUNNING",
        taskState="complete",
    )


def test_receipt_rejects_schema_shape_tamper(tmp_path: Path) -> None:
    path = _receipt(tmp_path, unexpected=True)
    with pytest.raises(HighCostAdmissionReceiptError, match="HIGH_COST_ADMISSION_INVALID"):
        validate_admission_receipt(
            path,
            expected_operation_kind="full_e2e",
            expected_attempt_id="nopublish:2026-08-01",
        )
