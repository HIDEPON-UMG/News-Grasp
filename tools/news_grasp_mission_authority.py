"""既存News-Grasp audit mission authorityのpure validator。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


MAX_AUTHORITY_BYTES = 64 * 1024
MISSION_EVENT_SHA256 = [
    "021a893039bbfabeebfe366d985bd37a5bd3a99f3c8edb939007ea76e0b6868d",
    "6926615fce93fdba64bbd43af82bb3ef71df22e4569f8bd96787f64c2863b03e",
    "81bcd6403a58cd11b51812a0d6be2e201985245f40a83b9dc31ffa585d428017",
]
EXPECTED_BODY: dict[str, Any] = {
    "schemaVersion": "AUDIT_MISSION_AUTHORITY_V1",
    "productId": "News-Grasp",
    "automationId": "news-grasp-6-40",
    "missionEventSha256": MISSION_EVENT_SHA256,
    "allowedEffects": [
        "scheduled_recovery",
        "publish_same_date_surface",
        "notification_same_date",
        "private_incident_evidence",
    ],
    "forbiddenEffects": [
        "full_e2e",
        "fallback_success",
        "raw_process_termination",
        "public_incident_report",
    ],
    "maxExternalModelCalls": 9,
    "maxFullE2EAttempts": 0,
    "terminalEnum": [
        "audit_normal_green",
        "audit_recovered_green",
        "audit_major_incident_open",
    ],
    "noFocusTheft": True,
    "noUserMonitoring": True,
    "noAutoOpen": True,
}
EXPECTED_KEYS = {*EXPECTED_BODY, "receiptSha256"}


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def validate_mission_authority(value: Mapping[str, Any]) -> dict[str, Any]:
    """固定mission契約とself-hashが完全一致するauthorityだけを受理する。"""
    if not isinstance(value, Mapping) or set(value) != EXPECTED_KEYS:
        raise ValueError("AUDIT_MISSION_AUTHORITY_INVALID")
    body = {key: item for key, item in value.items() if key != "receiptSha256"}
    receipt = str(value.get("receiptSha256") or "")
    if body != EXPECTED_BODY or receipt != _sha256(body):
        raise ValueError("AUDIT_MISSION_AUTHORITY_INVALID")
    return {
        "schemaVersion": "NEWS_GRASP_MISSION_AUTHORITY_VALIDATION_V1",
        "status": "Green",
        "receiptSha256": receipt,
    }


def validate_existing(path: Path) -> dict[str, Any]:
    """単一handleのbounded readで既存authorityを検証する。"""
    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_file():
        raise ValueError("AUDIT_MISSION_AUTHORITY_INVALID")
    try:
        with candidate.open("rb") as stream:
            before = os.fstat(stream.fileno())
            if before.st_size > MAX_AUTHORITY_BYTES or getattr(before, "st_nlink", 1) != 1:
                raise ValueError("AUDIT_MISSION_AUTHORITY_INVALID")
            payload = stream.read(MAX_AUTHORITY_BYTES + 1)
            after = os.fstat(stream.fileno())
    except ValueError:
        raise
    except OSError as error:
        raise ValueError("AUDIT_MISSION_AUTHORITY_INVALID") from error
    if len(payload) > MAX_AUTHORITY_BYTES or (
        before.st_dev,
        before.st_ino,
        before.st_size,
    ) != (after.st_dev, after.st_ino, after.st_size):
        raise ValueError("AUDIT_MISSION_AUTHORITY_INVALID")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("AUDIT_MISSION_AUTHORITY_INVALID") from error
    result = validate_mission_authority(value)
    result["fileSha256"] = hashlib.sha256(payload).hexdigest()
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("validate-existing",))
    parser.add_argument("--path", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = validate_existing(args.path)
    except ValueError as error:
        print(
            json.dumps(
                {
                    "schemaVersion": "NEWS_GRASP_MISSION_AUTHORITY_VALIDATION_V1",
                    "status": "Red",
                    "reasonCode": str(error),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
