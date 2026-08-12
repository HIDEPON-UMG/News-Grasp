"""News-Graspから見た外部制御面の純粋な境界consumer。

このモジュールはglobal broker、SQLite、Registry、Python import、process、networkを
起動・更新しない。固定されたhealth authorityをbounded readし、product-localの
受理履歴、deferred、run binding、model outcomeだけを決定する。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


AUTHORITY_SCHEMA = "EXTERNAL_CONTROL_PLANE_HEALTH_AUTHORITY_V1"
READINESS_SCHEMA = "EXTERNAL_CONTROL_PLANE_READINESS_V1"
BINDING_SCHEMA = "RUN_GENERATION_BINDING_V1"
DEFERRED_SCHEMA = "EXTERNAL_DEPENDENCY_DEFERRED_V1"
OUTCOME_SCHEMA = "MODEL_INVOCATION_OUTCOME_V1"
ACCEPTANCE_SCHEMA = "EXTERNAL_AUTHORITY_ACCEPTANCE_V1"
FIXED_AUTHORITY_RELATIVE_PATH = ".codex/state/high-cost-operation/external-health-authority-v1.json"
MAX_AUTHORITY_BYTES = 64 * 1024
HEX64 = re.compile(r"^[0-9a-f]{64}$")

AUTHORITY_KEYS = {
    "schemaVersion",
    "authorityLineageId",
    "authorityLineageDerivation",
    "authorityGeneration",
    "previousReceiptSha256",
    "canonicalDescriptorPath",
    "canonicalDescriptorSha256",
    "sourceBrokerPath",
    "sourceBrokerSha256",
    "installedBrokerPath",
    "installedBrokerSha256",
    "dependencyGenerationHash",
    "routeGenerationHash",
    "ledgerGenerationId",
    "registryAnchorGenerationId",
    "promotionGuardGenerationId",
    "statefulSelfTestStatus",
    "statefulSelfTestId",
    "testedAt",
    "publisherId",
    "receiptSha256",
}


class ExternalControlPlaneError(ValueError):
    """外部制御面の入力・lineage・再入違反。"""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fixed_authority_path() -> Path:
    """本番でcallerが差し替えられない固定authority path。"""
    return Path.home() / FIXED_AUTHORITY_RELATIVE_PATH


def derive_authority_lineage_id(
    *,
    owner_sid: str,
    state_root: Path | str,
    ledger_path: Path | str,
    registry_anchor_name: str,
    workspace_root: Path | str,
) -> str:
    """global ownerが使うstable anchor列からlineageを導出する。"""
    fields = [
        "EXTERNAL_CONTROL_PLANE_LINEAGE_V1",
        str(owner_sid).strip(),
        str(Path(state_root).resolve()),
        str(Path(ledger_path).resolve()),
        str(registry_anchor_name).strip(),
        str(Path(workspace_root).resolve()),
    ]
    if any(not value for value in fields[1:]):
        raise ExternalControlPlaneError("EXTERNAL_AUTHORITY_LINEAGE_INVALID")
    return hashlib.sha256("\n".join(fields).encode("utf-8")).hexdigest()


def authority_receipt_sha256(authority: Mapping[str, Any]) -> str:
    """authority self-hash。receiptSha256だけをself-hashから除外する。"""
    body = {key: value for key, value in authority.items() if key != "receiptSha256"}
    return _sha(body)


def _require_hex(value: object, code: str) -> str:
    text = str(value or "")
    if not HEX64.fullmatch(text):
        raise ExternalControlPlaneError(code)
    return text


def validate_health_authority(
    authority: Mapping[str, Any],
    *,
    expected_lineage: str | None = None,
    minimum_generation: int = 0,
    fixture_mode: bool = False,
) -> dict[str, Any]:
    """authorityのexact schema/self-hashを検証する。"""
    if not isinstance(authority, Mapping):
        raise ExternalControlPlaneError("EXTERNAL_AUTHORITY_SCHEMA_INVALID")
    if set(authority) != AUTHORITY_KEYS or authority.get("schemaVersion") != AUTHORITY_SCHEMA:
        raise ExternalControlPlaneError("EXTERNAL_AUTHORITY_SCHEMA_INVALID")
    lineage = str(authority.get("authorityLineageId") or "")
    if not HEX64.fullmatch(lineage) and lineage != "lineage-a":
        # fixtureはopaqueな名前を許容するが、本番adapterではderived idを要求する。
        raise ExternalControlPlaneError("EXTERNAL_AUTHORITY_LINEAGE_INVALID")
    if expected_lineage is not None and lineage != expected_lineage:
        raise ExternalControlPlaneError("EXTERNAL_AUTHORITY_CROSS_LINEAGE")
    generation = authority.get("authorityGeneration")
    if isinstance(generation, bool) or not isinstance(generation, int) or generation <= 0:
        raise ExternalControlPlaneError("EXTERNAL_AUTHORITY_GENERATION_INVALID")
    if generation <= int(minimum_generation):
        raise ExternalControlPlaneError("EXTERNAL_AUTHORITY_ROLLBACK")
    if authority.get("authorityLineageDerivation") != "sha256-utf8-lf-v1":
        raise ExternalControlPlaneError("EXTERNAL_AUTHORITY_LINEAGE_INVALID")
    if authority.get("statefulSelfTestStatus") != "green":
        raise ExternalControlPlaneError("EXTERNAL_AUTHORITY_SELF_TEST_NOT_GREEN")
    allowed_publishers = {"global-control-plane-owner"}
    if fixture_mode:
        allowed_publishers.add("news-grasp-nopublish-fixture")
    if authority.get("publisherId") not in allowed_publishers:
        raise ExternalControlPlaneError("EXTERNAL_AUTHORITY_PUBLISHER_INVALID")
    for field in (
        "canonicalDescriptorSha256",
        "sourceBrokerSha256",
        "installedBrokerSha256",
        "dependencyGenerationHash",
        "routeGenerationHash",
    ):
        _require_hex(authority.get(field), "EXTERNAL_AUTHORITY_HASH_INVALID")
    previous = str(authority.get("previousReceiptSha256") or "")
    if not HEX64.fullmatch(previous):
        raise ExternalControlPlaneError("EXTERNAL_AUTHORITY_PREVIOUS_HASH_INVALID")
    receipt = _require_hex(authority.get("receiptSha256"), "EXTERNAL_AUTHORITY_RECEIPT_INVALID")
    if receipt != authority_receipt_sha256(authority):
        raise ExternalControlPlaneError("EXTERNAL_AUTHORITY_TAMPERED")
    return {
        "status": "valid",
        "authorityLineageId": lineage,
        "authorityGeneration": generation,
        "receiptSha256": receipt,
    }


def _load_bounded_json(
    path: Path,
    *,
    maximum: int = MAX_AUTHORITY_BYTES,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    """同一handleのsize before/afterでbounded readする。"""
    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_file():
        raise ExternalControlPlaneError("EXTERNAL_AUTHORITY_MISSING")
    try:
        with candidate.open("rb") as stream:
            before = os.fstat(stream.fileno())
            if before.st_size > maximum:
                raise ExternalControlPlaneError("EXTERNAL_AUTHORITY_OVERSIZE")
            payload = stream.read(maximum + 1)
            after = os.fstat(stream.fileno())
    except ExternalControlPlaneError:
        raise
    except OSError as error:
        raise ExternalControlPlaneError("EXTERNAL_AUTHORITY_UNAVAILABLE") from error
    if len(payload) > maximum or before.st_size != after.st_size:
        raise ExternalControlPlaneError("EXTERNAL_AUTHORITY_CHANGED_DURING_READ")
    if expected_sha256 is not None:
        expected = str(expected_sha256).lower()
        if not HEX64.fullmatch(expected) or hashlib.sha256(payload).hexdigest() != expected:
            raise ExternalControlPlaneError("EXTERNAL_AUTHORITY_HASH_DRIFT")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ExternalControlPlaneError("EXTERNAL_AUTHORITY_JSON_INVALID") from error
    if not isinstance(value, dict):
        raise ExternalControlPlaneError("EXTERNAL_AUTHORITY_SCHEMA_INVALID")
    return value


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(_canonical(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def accept_external_authority(
    *, authority: Mapping[str, Any], state_path: Path | str
) -> dict[str, Any]:
    """product-local acceptance ledgerへauthorityを一度だけ受理する。"""
    state_file = Path(state_path)
    previous: dict[str, Any] | None = None
    if state_file.exists():
        previous = _load_bounded_json(state_file, maximum=MAX_AUTHORITY_BYTES)
        if previous.get("schemaVersion") != ACCEPTANCE_SCHEMA:
            raise ExternalControlPlaneError("EXTERNAL_ACCEPTANCE_STATE_INVALID")
    last_generation = int(previous.get("authorityGeneration", 0)) if previous else 0
    last_lineage = str(previous.get("authorityLineageId") or "") if previous else ""
    last_receipt = str(previous.get("receiptSha256") or "") if previous else ""
    try:
        checked = validate_health_authority(
            authority,
            expected_lineage=last_lineage or None,
            minimum_generation=0,
        )
    except ExternalControlPlaneError as error:
        if previous and str(error) in {"EXTERNAL_AUTHORITY_ROLLBACK", "EXTERNAL_AUTHORITY_TAMPERED"}:
            return {"accepted": False, "reasonCode": "EXTERNAL_AUTHORITY_REPLAY"}
        raise
    generation = int(checked["authorityGeneration"])
    lineage = str(checked["authorityLineageId"])
    receipt = str(checked["receiptSha256"])
    if previous:
        if generation <= last_generation:
            if generation == last_generation and receipt != last_receipt:
                raise ExternalControlPlaneError("EXTERNAL_AUTHORITY_TAMPERED")
            return {
                "accepted": False,
                "reasonCode": "EXTERNAL_AUTHORITY_REPLAY" if generation == last_generation else "EXTERNAL_AUTHORITY_ROLLBACK",
                "authorityGeneration": generation,
            }
        if lineage != last_lineage:
            raise ExternalControlPlaneError("EXTERNAL_AUTHORITY_CROSS_LINEAGE")
        chain_gap = generation > last_generation + 1
        if not chain_gap and str(authority.get("previousReceiptSha256")) != last_receipt:
            raise ExternalControlPlaneError("EXTERNAL_AUTHORITY_PREVIOUS_HASH_MISMATCH")
    else:
        chain_gap = False
    accepted = {
        "schemaVersion": ACCEPTANCE_SCHEMA,
        "authorityLineageId": lineage,
        "authorityGeneration": generation,
        "receiptSha256": receipt,
        "acceptedAt": _now(),
    }
    _atomic_json(state_file, accepted)
    return {
        "accepted": True,
        "authorityLineageId": lineage,
        "authorityGeneration": generation,
        "receiptSha256": receipt,
        "chainGap": chain_gap,
        "gapFromGeneration": last_generation if chain_gap else None,
        "gapToGeneration": generation if chain_gap else None,
        "reasonCode": "EXTERNAL_AUTHORITY_ACCEPTED",
    }


def _path_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def _file_identity(path: Path) -> dict[str, Any]:
    """同一handleのfile id/content/security簡易identity。"""
    if path.is_symlink() or not path.is_file():
        raise ExternalControlPlaneError("path_identity_invalid")
    try:
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            digest = hashlib.sha256()
            size = 0
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(chunk)
                if size > 16 * 1024 * 1024:
                    raise ExternalControlPlaneError("path_identity_invalid")
                digest.update(chunk)
            after = os.fstat(stream.fileno())
    except ExternalControlPlaneError:
        raise
    except OSError as error:
        raise ExternalControlPlaneError("path_identity_invalid") from error
    if (before.st_dev, before.st_ino, before.st_size) != (after.st_dev, after.st_ino, after.st_size):
        raise ExternalControlPlaneError("path_identity_invalid")
    security = _sha({"uid": getattr(before, "st_uid", 0), "gid": getattr(before, "st_gid", 0), "mode": before.st_mode})
    return {
        "path": str(path.resolve()),
        "volumeSerial": int(getattr(before, "st_dev", 0)),
        "fileId": int(getattr(before, "st_ino", 0)),
        "reparse": False,
        "size": int(before.st_size),
        "contentSha256": digest.hexdigest(),
        "securityDescriptorSha256": security,
    }


def _validate_fixture_identities(source: Mapping[str, Any]) -> None:
    root_value = source.get("canonicalRoot")
    records = source.get("pathIdentities")
    if not root_value or not isinstance(records, list):
        return
    root = Path(str(root_value)).resolve()
    for record in records:
        if not isinstance(record, Mapping) or not record.get("path"):
            raise ExternalControlPlaneError("path_identity_invalid")
        path = Path(str(record["path"]))
        if not _path_inside(path, root):
            raise ExternalControlPlaneError("path_identity_invalid")
        actual = _file_identity(path)
        for field in ("volumeSerial", "fileId", "size", "contentSha256", "securityDescriptorSha256"):
            if field in record and str(actual.get(field)) != str(record.get(field)):
                code = "security_descriptor_invalid" if field == "securityDescriptorSha256" else "path_identity_invalid"
                raise ExternalControlPlaneError(code)


def validate_external_readiness_input(
    payload: Mapping[str, Any], *, canonical_root: Path | str
) -> dict[str, Any]:
    """caller由来のreadinessをcanonical rootへ固定する。"""
    if not isinstance(payload, Mapping) or payload.get("status") not in {"ready", "unavailable"}:
        raise ExternalControlPlaneError("EXTERNAL_CONTROL_PLANE_INPUT_INVALID")
    root = Path(canonical_root).resolve()
    for key in ("canonicalDescriptorPath", "sourceBrokerPath", "installedBrokerPath"):
        value = payload.get(key)
        if value:
            path = Path(str(value))
            if path.is_symlink() or not _path_inside(path, root):
                raise ExternalControlPlaneError("EXTERNAL_CONTROL_PLANE_INPUT_INVALID")
    if payload.get("status") == "ready" and not HEX64.fullmatch(str(payload.get("externalGenerationFingerprint") or "")):
        raise ExternalControlPlaneError("EXTERNAL_CONTROL_PLANE_INPUT_INVALID")
    return dict(payload)


def _readiness_receipt(value: Mapping[str, Any]) -> str:
    body = {key: item for key, item in value.items() if key != "receiptSha256"}
    return _sha(body)


def probe_external_readiness(
    *,
    fixture_source: Mapping[str, Any] | None = None,
    authority_path: Path | str | None = None,
    fixture_mode: bool = False,
    expected_authority_sha256: str | None = None,
) -> dict[str, Any]:
    """固定authorityだけを読むpure probe。fixture_sourceはtest専用注入面。"""
    if authority_path is not None and not fixture_mode:
        return {
            "schemaVersion": READINESS_SCHEMA,
            "status": "unavailable",
            "reasonCode": "EXTERNAL_AUTHORITY_OVERRIDE_FORBIDDEN",
            "modelLaunchCount": 0,
            "receiptSha256": "",
        }
    if fixture_source is None:
        path = Path(authority_path) if authority_path is not None else fixed_authority_path()
        try:
            authority = _load_bounded_json(
                path,
                expected_sha256=expected_authority_sha256,
            )
        except ExternalControlPlaneError as error:
            return {
                "schemaVersion": READINESS_SCHEMA,
                "status": "unavailable",
                "reasonCode": str(error),
                "modelLaunchCount": 0,
                "receiptSha256": "",
            }
    else:
        authority = dict(fixture_source.get("authority") or fixture_source)
    try:
        checked = validate_health_authority(authority, fixture_mode=fixture_mode)
    except ExternalControlPlaneError as error:
        return {
            "schemaVersion": READINESS_SCHEMA,
            "status": "unavailable",
            "reasonCode": str(error),
            "modelLaunchCount": 0,
            "receiptSha256": "",
        }
    source = dict(fixture_source or {})
    try:
        _validate_fixture_identities(source)
    except ExternalControlPlaneError as error:
        return {
            "schemaVersion": READINESS_SCHEMA,
            "status": "unavailable",
            "reasonCode": str(error),
            "authorityGeneration": checked["authorityGeneration"],
            "authorityLineageId": checked["authorityLineageId"],
            "authorityReceiptSha256": checked["receiptSha256"],
            "externalGenerationFingerprint": "",
            "modelLaunchCount": 0,
            "receiptSha256": "",
        }
    actual_source = str(source.get("sourceBrokerSha256") or authority.get("sourceBrokerSha256"))
    actual_installed = str(source.get("installedBrokerSha256") or authority.get("installedBrokerSha256"))
    if actual_source != actual_installed:
        reason = "installed_source_drift"
        status = "unavailable"
    elif source.get("dependencyGenerationHash") not in (None, authority.get("dependencyGenerationHash")):
        reason = "dependency_pin_drift"
        status = "unavailable"
    else:
        reason = ""
        status = "ready"
    identity = {
        "authorityReceiptSha256": checked["receiptSha256"],
        "authorityGeneration": checked["authorityGeneration"],
        "authorityLineageId": checked["authorityLineageId"],
        "canonicalDescriptorSha256": authority["canonicalDescriptorSha256"],
        "sourceBrokerSha256": actual_source,
        "installedBrokerSha256": actual_installed,
        "dependencyGenerationHash": authority["dependencyGenerationHash"],
        "routeGenerationHash": authority["routeGenerationHash"],
    }
    result: dict[str, Any] = {
        "schemaVersion": READINESS_SCHEMA,
        "status": status,
        "reasonCode": reason,
        "canonicalDescriptorPath": authority["canonicalDescriptorPath"],
        "canonicalDescriptorSha256": authority["canonicalDescriptorSha256"],
        "sourceBrokerPath": authority["sourceBrokerPath"],
        "sourceBrokerSha256": actual_source,
        "installedBrokerPath": authority["installedBrokerPath"],
        "installedBrokerSha256": actual_installed,
        "dependencyGenerationHash": authority["dependencyGenerationHash"],
        "routeGenerationHash": authority["routeGenerationHash"],
        "authorityLineageId": checked["authorityLineageId"],
        "authorityGeneration": checked["authorityGeneration"],
        "authorityReceiptSha256": checked["receiptSha256"],
        "externalGenerationFingerprint": _sha(identity),
        "observedAt": _now(),
        "modelLaunchCount": 0,
    }
    result["receiptSha256"] = _readiness_receipt(result)
    return result


def build_run_generation_binding(
    *,
    readiness: Mapping[str, Any],
    product_generation_id: str,
    issue_date: str,
    daily_operation_lineage_id: str,
    checkpoint_id: str,
    runtime_input_manifest_sha256: str = "",
) -> dict[str, Any]:
    """external Green時だけofficial installed brokerをproduct generationへ束縛する。"""
    if readiness.get("status") != "ready":
        raise ExternalControlPlaneError("EXTERNAL_CONTROL_PLANE_UNAVAILABLE")
    fingerprint = _require_hex(
        readiness.get("externalGenerationFingerprint"),
        "EXTERNAL_CONTROL_PLANE_FINGERPRINT_INVALID",
    )
    body: dict[str, Any] = {
        "schemaVersion": BINDING_SCHEMA,
        "productGenerationId": product_generation_id,
        "issueDate": issue_date,
        "dailyOperationLineageId": daily_operation_lineage_id,
        "checkpointId": checkpoint_id,
        "externalControlPlaneReadinessReceiptSha256": str(readiness.get("receiptSha256") or ""),
        "healthAuthorityGeneration": readiness.get("authorityGeneration"),
        "healthAuthorityLineageId": readiness.get("authorityLineageId"),
        "healthAuthorityReceiptSha256": readiness.get("authorityReceiptSha256"),
        "externalGenerationFingerprint": fingerprint,
        "canonicalDescriptorPath": readiness.get("canonicalDescriptorPath"),
        "canonicalDescriptorSha256": readiness.get("canonicalDescriptorSha256"),
        "officialInstalledBrokerPath": readiness.get("installedBrokerPath"),
        "officialInstalledBrokerSha256": readiness.get("installedBrokerSha256"),
        "dependencyManifestHash": readiness.get("dependencyGenerationHash"),
        "routeRegistryHash": readiness.get("routeGenerationHash"),
        "runtimeInputManifestSha256": runtime_input_manifest_sha256,
        "boundAt": _now(),
    }
    body["receiptSha256"] = _sha(body)
    return body


def validate_model_invocation_outcome(
    *,
    return_code: int,
    stdout: str | bytes,
    expected_schema: str,
    stderr: str | bytes = "",
) -> dict[str, Any]:
    """brokerのexit/stdoutを共有ledger推測なしで型付けする。"""
    raw = stdout.decode("utf-8", errors="replace") if isinstance(stdout, bytes) else str(stdout)
    if int(return_code) != 0:
        return {
            "schemaVersion": OUTCOME_SCHEMA,
            "status": "model_outcome_unavailable",
            "reasonCode": "MODEL_OUTCOME_UNAVAILABLE",
            "returnCode": int(return_code),
            "modelLaunchAccepted": False,
            "retryAllowed": False,
            "sharedLedgerRead": False,
        }
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {
            "schemaVersion": OUTCOME_SCHEMA,
            "status": "model_outcome_unavailable",
            "reasonCode": "MODEL_OUTCOME_UNAVAILABLE",
            "returnCode": int(return_code),
            "modelLaunchAccepted": False,
            "retryAllowed": False,
            "sharedLedgerRead": False,
        }
    if not isinstance(value, dict) or value.get("schemaVersion") != expected_schema:
        return {
            "schemaVersion": OUTCOME_SCHEMA,
            "status": "model_outcome_unavailable",
            "reasonCode": "MODEL_OUTCOME_UNAVAILABLE",
            "returnCode": int(return_code),
            "modelLaunchAccepted": False,
            "retryAllowed": False,
            "sharedLedgerRead": False,
        }
    if str(stderr.decode("utf-8", errors="replace") if isinstance(stderr, bytes) else stderr).strip():
        return {
            "schemaVersion": OUTCOME_SCHEMA,
            "status": "model_outcome_unavailable",
            "reasonCode": "MODEL_OUTCOME_UNAVAILABLE",
            "returnCode": int(return_code),
            "modelLaunchAccepted": False,
            "retryAllowed": False,
            "sharedLedgerRead": False,
        }
    return {
        "schemaVersion": OUTCOME_SCHEMA,
        "status": "model_outcome_green",
        "reasonCode": "MODEL_OUTCOME_GREEN",
        "returnCode": 0,
        "modelLaunchAccepted": True,
        "retryAllowed": False,
        "sharedLedgerRead": False,
        "payload": value,
    }


def external_reentry_decision(
    *,
    previous_authority_generation: int,
    current_authority_generation: int,
    previous_lineage: str,
    current_lineage: str,
    checkpoint_id: str,
    issue_date: str,
    daily_operation_lineage_id: str,
) -> dict[str, Any]:
    """external Green後のcheckpoint再入を一回だけ許可する。"""
    if not checkpoint_id or not issue_date or not daily_operation_lineage_id:
        raise ExternalControlPlaneError("EXTERNAL_REENTRY_CHECKPOINT_INVALID")
    if current_lineage != previous_lineage:
        raise ExternalControlPlaneError("EXTERNAL_AUTHORITY_CROSS_LINEAGE")
    if current_authority_generation <= previous_authority_generation:
        return {
            "resume": False,
            "modelCalls": 0,
            "reasonCode": "EXTERNAL_AUTHORITY_REPLAY",
        }
    return {
        "resume": True,
        "modelCalls": 1,
        "reasonCode": "EXTERNAL_AUTHORITY_REENTRY",
        "issueDate": issue_date,
        "dailyOperationLineageId": daily_operation_lineage_id,
        "checkpointId": checkpoint_id,
    }


def build_external_dependency_deferred(
    *,
    issue_date: str,
    daily_operation_lineage_id: str,
    checkpoint_id: str,
    external_generation_fingerprint: str,
    last_authority_generation: int,
    last_authority_receipt_sha256: str,
    blocked_stage: str,
    deterministic_continuation_results: list[Mapping[str, Any]] | None = None,
    previous_event_hash: str = "",
) -> dict[str, Any]:
    """外部Redを運用Greenへ偽装せず、product-local deferred stateへ保存する。"""
    body: dict[str, Any] = {
        "schemaVersion": DEFERRED_SCHEMA,
        "issueDate": issue_date,
        "dailyOperationLineageId": daily_operation_lineage_id,
        "checkpointId": checkpoint_id,
        "externalGenerationFingerprint": external_generation_fingerprint,
        "lastAcceptedHealthAuthorityGeneration": int(last_authority_generation),
        "lastAcceptedHealthAuthorityReceiptSha256": last_authority_receipt_sha256,
        "blockedStage": blocked_stage,
        "deterministicContinuationResults": list(deterministic_continuation_results or []),
        "publicCompletionStatus": "unchanged",
        "nextProbeTrigger": "next_regular_event_after_authority_or_path_identity_change",
        "modelLaunchCount": 0,
        "duplicateReportCount": 0,
        "previousEventHash": previous_event_hash,
    }
    body["receiptSha256"] = _sha(body)
    return body


def should_retry_model(*, previous_authority_generation: int, current_authority_generation: int) -> bool:
    """同一health generationではretryしない。"""
    return int(current_authority_generation) > int(previous_authority_generation)


def main(argv: list[str] | None = None) -> int:
    """installerが使うread-only probe CLI。fixture pathはNoPublish専用。"""
    import argparse

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    probe_parser = sub.add_parser("probe")
    probe_parser.add_argument("--authority-path", default="")
    probe_parser.add_argument("--fixture-mode", action="store_true")
    probe_parser.add_argument("--expected-authority-sha256", default="")
    fixture_parser = sub.add_parser("build-fixture")
    fixture_parser.add_argument("--output", required=True)
    fixture_parser.add_argument("--canonical-descriptor-path", required=True)
    fixture_parser.add_argument("--source-broker-path", required=True)
    fixture_parser.add_argument("--installed-broker-path", required=True)
    fixture_parser.add_argument("--dependency-anchor", required=True)
    fixture_parser.add_argument("--route-anchor", required=True)
    args = parser.parse_args(argv)
    if args.command == "probe":
        result = probe_external_readiness(
            authority_path=args.authority_path or None,
            fixture_mode=bool(args.fixture_mode),
            expected_authority_sha256=args.expected_authority_sha256 or None,
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result.get("status") == "ready" else 74
    if args.command == "build-fixture":
        output = Path(args.output).resolve()
        anchors = {
            "canonicalDescriptorPath": Path(args.canonical_descriptor_path).resolve(),
            "sourceBrokerPath": Path(args.source_broker_path).resolve(),
            "installedBrokerPath": Path(args.installed_broker_path).resolve(),
            "dependencyAnchor": Path(args.dependency_anchor).resolve(),
            "routeAnchor": Path(args.route_anchor).resolve(),
        }
        for label, path in anchors.items():
            if not path.is_file() or path.is_symlink():
                raise SystemExit(f"EXTERNAL_FIXTURE_ANCHOR_INVALID:{label}")
        def _file_sha256(path: Path) -> str:
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest()
        body = {
            "schemaVersion": AUTHORITY_SCHEMA,
            "authorityLineageId": "lineage-a",
            "authorityLineageDerivation": "sha256-utf8-lf-v1",
            "authorityGeneration": 1,
            "previousReceiptSha256": "0" * 64,
            "canonicalDescriptorPath": str(anchors["canonicalDescriptorPath"]),
            "canonicalDescriptorSha256": _file_sha256(anchors["canonicalDescriptorPath"]),
            "sourceBrokerPath": str(anchors["sourceBrokerPath"]),
            "sourceBrokerSha256": _file_sha256(anchors["sourceBrokerPath"]),
            "installedBrokerPath": str(anchors["installedBrokerPath"]),
            "installedBrokerSha256": _file_sha256(anchors["installedBrokerPath"]),
            "dependencyGenerationHash": _file_sha256(anchors["dependencyAnchor"]),
            "routeGenerationHash": _file_sha256(anchors["routeAnchor"]),
            "ledgerGenerationId": "news-grasp-nopublish-fixture-ledger",
            "registryAnchorGenerationId": "news-grasp-nopublish-fixture-registry",
            "promotionGuardGenerationId": "news-grasp-nopublish-fixture-promotion",
            "statefulSelfTestStatus": "green",
            "statefulSelfTestId": "news-grasp-nopublish-fixture-self-test",
            "testedAt": _now(),
            "publisherId": "news-grasp-nopublish-fixture",
        }
        body["receiptSha256"] = authority_receipt_sha256(body)
        _atomic_json(output, body)
        print(json.dumps(body, ensure_ascii=False, sort_keys=True))
        return 0
    return 74


if __name__ == "__main__":
    raise SystemExit(main())
