from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


HASH_RE = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_KEYS = {
    "schemaVersion",
    "taskIdentity",
    "latestActualUserEventHash",
    "operationKind",
    "fullE2EAttemptReservation",
    "taskState",
}
RESERVATION_KEYS = {"attemptId", "eventSequence", "idempotent"}


class HighCostAdmissionReceiptError(RuntimeError):
    pass


def validate_admission_receipt(
    path: Path,
    *,
    expected_operation_kind: str,
    expected_attempt_id: str,
) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).resolve(strict=True).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise HighCostAdmissionReceiptError("HIGH_COST_ADMISSION_INVALID") from error
    if (
        not isinstance(value, dict)
        or set(value) != REQUIRED_KEYS
        or value.get("schemaVersion") != "HIGH_COST_OPERATION_ADMISSION_V2"
        or not isinstance(value.get("taskIdentity"), str)
        or not value["taskIdentity"]
        or not isinstance(value.get("latestActualUserEventHash"), str)
        or not HASH_RE.fullmatch(value["latestActualUserEventHash"])
    ):
        raise HighCostAdmissionReceiptError("HIGH_COST_ADMISSION_INVALID")
    if value.get("operationKind") != expected_operation_kind:
        raise HighCostAdmissionReceiptError("HIGH_COST_ADMISSION_OPERATION_DRIFT")
    if value.get("taskState") != "running":
        raise HighCostAdmissionReceiptError("HIGH_COST_ADMISSION_NOT_RUNNING")
    reservation = value.get("fullE2EAttemptReservation")
    if not isinstance(reservation, dict):
        raise HighCostAdmissionReceiptError("HIGH_COST_ADMISSION_RESERVATION_REQUIRED")
    if (
        set(reservation) != RESERVATION_KEYS
        or not isinstance(reservation.get("eventSequence"), int)
        or isinstance(reservation.get("eventSequence"), bool)
        or reservation["eventSequence"] <= 0
    ):
        raise HighCostAdmissionReceiptError("HIGH_COST_ADMISSION_INVALID")
    if reservation.get("attemptId") != expected_attempt_id:
        raise HighCostAdmissionReceiptError("HIGH_COST_ADMISSION_ATTEMPT_DRIFT")
    if reservation.get("idempotent") is not False:
        raise HighCostAdmissionReceiptError("HIGH_COST_ADMISSION_REPLAY_RECEIPT")
    return value


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("validate", nargs="?")
    parser.add_argument("--path", type=Path, required=True)
    parser.add_argument("--expected-operation-kind", required=True)
    parser.add_argument("--expected-attempt-id", required=True)
    args = parser.parse_args(argv)
    try:
        value = validate_admission_receipt(
            args.path,
            expected_operation_kind=args.expected_operation_kind,
            expected_attempt_id=args.expected_attempt_id,
        )
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
        return 0
    except HighCostAdmissionReceiptError as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(_main())
