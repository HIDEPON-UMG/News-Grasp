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
LEGACY_EXPECTED_BODY: dict[str, Any] = {
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
EXPECTED_BODY: dict[str, Any] = {
    **LEGACY_EXPECTED_BODY,
    "schemaVersion": "AUDIT_MISSION_AUTHORITY_V2",
    "auditDecisionSchemaVersion": "AUDIT_RECOVERY_DECISION_V2",
    "terminalEnum": [
        "audit_normal_green",
        "audit_recovered_green",
        "audit_observation_unverified",
        "audit_major_incident_open",
    ],
}
EXPECTED_KEYS = {
    *EXPECTED_BODY,
    "sourceAuthority",
    "sourceAuthorityReceiptSha256",
    "receiptSha256",
}
LEGACY_EXPECTED_KEYS = {*LEGACY_EXPECTED_BODY, "receiptSha256"}


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def validate_mission_authority(value: Mapping[str, Any]) -> dict[str, Any]:
    """V2をcurrent authority、V1を履歴read-only互換として検証する。"""
    if not isinstance(value, Mapping):
        raise ValueError("AUDIT_MISSION_AUTHORITY_INVALID")
    body = {key: item for key, item in value.items() if key != "receiptSha256"}
    receipt = str(value.get("receiptSha256") or "")
    legacy = set(value) == LEGACY_EXPECTED_KEYS and body == LEGACY_EXPECTED_BODY
    source = body.get("sourceAuthority")
    source_receipt = str(body.get("sourceAuthorityReceiptSha256") or "")
    current_body = {
        key: item
        for key, item in body.items()
        if key not in {"sourceAuthority", "sourceAuthorityReceiptSha256"}
    }
    current = bool(
        set(value) == EXPECTED_KEYS
        and current_body == EXPECTED_BODY
        and isinstance(source, Mapping)
        and set(source) == LEGACY_EXPECTED_KEYS
        and {key: item for key, item in source.items() if key != "receiptSha256"}
        == LEGACY_EXPECTED_BODY
        and source.get("receiptSha256") == _sha256(LEGACY_EXPECTED_BODY)
        and source_receipt == source.get("receiptSha256")
    )
    if (not current and not legacy) or receipt != _sha256(body):
        raise ValueError("AUDIT_MISSION_AUTHORITY_INVALID")
    return {
        "schemaVersion": "NEWS_GRASP_MISSION_AUTHORITY_VALIDATION_V2",
        "status": "Green",
        "authorityVersion": "current" if current else "legacy_read_only",
        "auditDecisionSchemaVersion": body.get(
            "auditDecisionSchemaVersion", "AUDIT_RECOVERY_DECISION_V1"
        ),
        "receiptSha256": receipt,
    }


def wrap_legacy_authority(value: Mapping[str, Any]) -> dict[str, Any]:
    """Bind the broker-issued V1 effect authority to the V2 terminal adapter."""

    validated = validate_mission_authority(value)
    if validated["authorityVersion"] != "legacy_read_only":
        raise ValueError("AUDIT_MISSION_AUTHORITY_LEGACY_SOURCE_REQUIRED")
    body = {
        **EXPECTED_BODY,
        "sourceAuthority": dict(value),
        "sourceAuthorityReceiptSha256": value["receiptSha256"],
    }
    return {**body, "receiptSha256": _sha256(body)}


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
    parser.add_argument("command", choices=("validate-existing", "wrap-legacy"))
    parser.add_argument("--path", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "validate-existing":
            result = validate_existing(args.path)
        else:
            candidate = Path(args.path)
            if candidate.is_symlink() or not candidate.is_file():
                raise ValueError("AUDIT_MISSION_AUTHORITY_INVALID")
            raw = candidate.read_bytes()
            if len(raw) > MAX_AUTHORITY_BYTES:
                raise ValueError("AUDIT_MISSION_AUTHORITY_INVALID")
            legacy = json.loads(raw.decode("utf-8-sig"))
            result = wrap_legacy_authority(legacy)
    except ValueError as error:
        print(
            json.dumps(
                {
                    "schemaVersion": "NEWS_GRASP_MISSION_AUTHORITY_VALIDATION_V2",
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
