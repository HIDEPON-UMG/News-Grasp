"""News-Grasp 日次公開の固定 production adapter。

この module は外部 outbox から呼ばれる本番 adapter の唯一の登録点である。
caller が provider を差し替えたり、run identity を再定義したりできないよう、
全 adapter は同一の keyword-only 契約を受け、provider 呼出し直前に共通
preflight を実行する。provider の返却値は raw のまま保存せず、有限な payload
を含む ``NEWS_GRASP_EXTERNAL_ADAPTER_RECEIPT_V1`` へ射影する。
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any
from urllib.parse import urljoin, urlsplit


EXTERNAL_ADAPTER_RECEIPT_SCHEMA = "NEWS_GRASP_EXTERNAL_ADAPTER_RECEIPT_V1"
PROTECTED_RELEASE = "2026-09-02"
CANONICAL_RUN_INTENT = "scheduled_production_direct"
PUBLIC_BASE_URL = "https://hidepon-umg.github.io/News-Grasp/"
REPO_ROOT = Path(__file__).resolve().parents[1]

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SHA1_RE = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
_RUN_ALIAS_NAMES = frozenset({"final", "latest", "current"})
_EXTERNAL_OPERATION_IDS = (
    "audio_daily_upload",
    "audio_deepdive_upload",
    "youtube_daily_prepare",
    "youtube_deepdive_prepare",
    "git_release_push",
    "pages_deployment_wait",
    "youtube_daily_finalize",
    "youtube_deepdive_finalize",
    "notification_send",
    "completion_attestation_publish",
)
_NOTIFICATION_ARG_LOCK = threading.Lock()
_MISSING = object()


class ProductionAdapterError(RuntimeError):
    """副作用開始前またはprovider結果検証時の typed Red。"""


def _json_file_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    candidate = Path(raw)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_json_file_bytes(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(candidate, path)
    finally:
        candidate.unlink(missing_ok=True)


def _read_json_object(path: Path, reason: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProductionAdapterError(reason) from exc
    if not isinstance(value, dict):
        raise ProductionAdapterError(reason)
    return value


def _receipt_hash(value: Mapping[str, Any]) -> str:
    body = {key: item for key, item in value.items() if key != "receiptSha256"}
    return hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _first(mapping: Mapping[str, Any], *keys: str, default: Any = _MISSING) -> Any:
    """camel/snake alias を一箇所で読み、競合を fail-closed にする。"""

    values = [mapping[key] for key in keys if key in mapping]
    if not values:
        return default
    first = values[0]
    if any(value != first for value in values[1:]):
        raise ProductionAdapterError("production_adapter_identity_alias_conflict")
    return first


def _nested_identity_values(context: Mapping[str, Any], *keys: str) -> list[Any]:
    """seal/run の nested mapping にある identity 値を抽出する。"""

    values: list[Any] = []
    containers: list[Mapping[str, Any]] = [context]
    for container_key in ("run", "publish_seal", "publishSeal", "seal"):
        candidate = context.get(container_key)
        if isinstance(candidate, Mapping):
            containers.append(candidate)
    for container in containers:
        value = _first(container, *keys, default=_MISSING)
        if value is not _MISSING:
            values.append(value)
    return values


def _required_context_value(context: Mapping[str, Any], *keys: str) -> Any:
    value = _first(context, *keys, default=_MISSING)
    if value is _MISSING or value is None or str(value).strip() == "":
        raise ProductionAdapterError(f"production_adapter_context_required:{keys[0]}")
    return value


def _validate_issue_date(value: Any) -> str:
    day = str(value or "").strip()
    if not _DATE_RE.fullmatch(day):
        raise ProductionAdapterError("production_adapter_issue_date_invalid")
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError as exc:
        raise ProductionAdapterError("production_adapter_issue_date_invalid") from exc
    if day == PROTECTED_RELEASE:
        raise ProductionAdapterError("protected_release_reexecution_forbidden")
    return day


def _safe_repo_root(context: Mapping[str, Any]) -> Path:
    value = _required_context_value(context, "repo_root", "repoRoot")
    try:
        candidate = Path(os.path.abspath(os.fspath(value))).resolve()
    except (TypeError, ValueError, OSError) as exc:
        raise ProductionAdapterError("production_adapter_repo_root_invalid") from exc
    expected = REPO_ROOT.resolve()
    if candidate != expected:
        raise ProductionAdapterError("production_adapter_repo_root_outside")
    if not candidate.is_dir():
        raise ProductionAdapterError("production_adapter_repo_root_missing")
    return candidate


def _safe_repo_path(root: Path, value: Any, *, expected: Path | None = None) -> Path:
    try:
        candidate = Path(os.fspath(value))
        if not candidate.is_absolute():
            candidate = root / candidate
        candidate = Path(os.path.abspath(os.fspath(candidate)))
        probe = candidate
        while probe != root and root in probe.parents:
            if probe.is_symlink():
                raise ProductionAdapterError("production_adapter_reparse_path_forbidden")
            probe = probe.parent
        if probe.is_symlink():
            raise ProductionAdapterError("production_adapter_reparse_path_forbidden")
        candidate = candidate.resolve()
    except (TypeError, ValueError, OSError) as exc:
        raise ProductionAdapterError("production_adapter_path_invalid") from exc
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ProductionAdapterError("production_adapter_path_outside_repo") from exc
    if expected is not None and candidate != expected.resolve():
        raise ProductionAdapterError("production_adapter_path_mismatch")
    if candidate.is_symlink():
        raise ProductionAdapterError("production_adapter_reparse_path_forbidden")
    return candidate


def _validate_common(
    *,
    context: Mapping[str, Any],
    operation_id: str,
    side_effect_id: str,
    idempotency_key: str,
    manifest_id: str,
    bundle_id: str,
    run_id: str,
    fencing_token: int,
) -> dict[str, Any]:
    if not isinstance(context, Mapping):
        raise ProductionAdapterError("production_adapter_context_mapping_required")
    # protected release は、他の identity 検証より前に閉じる。これにより
    # 2026-09-02 の provider import や filesystem/network access も起きない。
    day = _validate_issue_date(_required_context_value(context, "issue_date", "issueDate"))
    if operation_id not in _EXTERNAL_OPERATION_IDS:
        raise ProductionAdapterError("production_adapter_unknown_operation")
    if side_effect_id != operation_id:
        raise ProductionAdapterError("production_adapter_side_effect_id_mismatch")
    key = str(idempotency_key or "").strip()
    if not key:
        raise ProductionAdapterError("production_adapter_idempotency_key_required")
    actual_run_id = str(run_id or "").strip()
    if not actual_run_id or actual_run_id.casefold() in _RUN_ALIAS_NAMES:
        raise ProductionAdapterError("run_id_alias_forbidden")
    manifest = str(manifest_id or "").strip().casefold()
    if not _SHA256_RE.fullmatch(manifest):
        raise ProductionAdapterError("production_adapter_manifest_id_invalid")
    bundle = str(bundle_id or "").strip()
    if not bundle:
        raise ProductionAdapterError("production_adapter_bundle_id_required")
    if isinstance(fencing_token, bool):
        raise ProductionAdapterError("production_adapter_fencing_token_invalid")
    try:
        fence = int(fencing_token)
    except (TypeError, ValueError) as exc:
        raise ProductionAdapterError("production_adapter_fencing_token_invalid") from exc
    if fence <= 0:
        raise ProductionAdapterError("production_adapter_fencing_token_invalid")

    run_intent = str(_required_context_value(context, "run_intent", "runIntent")).strip()
    if run_intent != CANONICAL_RUN_INTENT:
        raise ProductionAdapterError("production_adapter_run_intent_invalid")
    root = _safe_repo_root(context)

    # adapter context は sealed identity を再定義できない。daily gate から
    # identity fields が省略される世代もあるため、存在する field だけを比較し、
    # 欠落は outbox の sealed identity によって補完する。
    comparisons = (
        (("run_id", "runId"), actual_run_id, "production_adapter_run_id_mismatch"),
        (("manifest_id", "manifestId"), manifest, "production_adapter_manifest_id_mismatch"),
        (("bundle_id", "bundleId"), bundle, "production_adapter_bundle_id_mismatch"),
    )
    for aliases, expected, failure in comparisons:
        supplied_values = _nested_identity_values(context, *aliases)
        for supplied in supplied_values:
            if str(supplied).strip().casefold() != str(expected).strip().casefold():
                raise ProductionAdapterError(failure)
    supplied_issue_values = _nested_identity_values(context, "issue_date", "issueDate")
    if any(str(value).strip() != day for value in supplied_issue_values):
        raise ProductionAdapterError("production_adapter_issue_date_mismatch")
    supplied_intent_values = _nested_identity_values(context, "run_intent", "runIntent")
    if any(str(value).strip() != run_intent for value in supplied_intent_values):
        raise ProductionAdapterError("production_adapter_run_intent_mismatch")
    supplied_fence = _first(context, "fencing_token", "fencingToken", default=_MISSING)
    if supplied_fence is not _MISSING:
        try:
            if int(supplied_fence) != fence:
                raise ProductionAdapterError("production_adapter_fencing_token_mismatch")
        except (TypeError, ValueError) as exc:
            raise ProductionAdapterError("production_adapter_fencing_token_mismatch") from exc

    return {
        "context": context,
        "repo_root": root,
        "issue_date": day,
        "run_intent": run_intent,
        "run_id": actual_run_id,
        "manifest_id": manifest,
        "bundle_id": bundle,
        "fencing_token": fence,
        "operation_id": operation_id,
        "side_effect_id": side_effect_id,
        "idempotency_key": key,
    }


def _sealed_sha(context: Mapping[str, Any]) -> str:
    values = _nested_identity_values(
        context,
        "release_commit_sha",
        "releaseCommitSha",
        "sealed_release_sha",
        "sealedReleaseSha",
    )
    if not values:
        raise ProductionAdapterError("production_adapter_release_commit_sha_required")
    normalized = {str(value or "").strip().casefold() for value in values}
    if len(normalized) != 1 or not _SHA1_RE.fullmatch(next(iter(normalized))):
        raise ProductionAdapterError("production_adapter_release_commit_sha_invalid")
    return next(iter(normalized))


def _sealed_remote_base_sha(context: Mapping[str, Any]) -> str:
    values = _nested_identity_values(context, "remote_base_sha", "remoteBaseSha")
    if not values:
        raise ProductionAdapterError("production_adapter_remote_base_sha_required")
    normalized = {str(value or "").strip().casefold() for value in values}
    if len(normalized) != 1 or not _SHA1_RE.fullmatch(next(iter(normalized))):
        raise ProductionAdapterError("production_adapter_remote_base_sha_invalid")
    return next(iter(normalized))


def _sealed_external_input_hash(identity: Mapping[str, Any], relative: str) -> str:
    context = identity["context"]
    seal = _first(context, "publish_seal", "publishSeal", default={})
    if not isinstance(seal, Mapping):
        raise ProductionAdapterError("production_adapter_publish_seal_missing")
    values = _first(seal, "externalInputHashes", "external_input_hashes", default={})
    if not isinstance(values, Mapping):
        raise ProductionAdapterError("production_adapter_external_input_binding_missing")
    digest = str(values.get(relative) or "").casefold()
    if not _SHA256_RE.fullmatch(digest):
        raise ProductionAdapterError("production_adapter_external_input_hash_missing")
    return digest


def _youtube_external_identity(
    identity: Mapping[str, Any],
    *,
    kind: str,
    phase: str,
) -> tuple[str, str]:
    """YouTube用sealed mp4 hashとprovider-native markerを一箇所で確定する。"""

    relative = (
        f"build/youtube-podcast/{identity['issue_date']}.mp4"
        if kind == "daily"
        else f"build/youtube-podcast-deepdive/{identity['issue_date']}.mp4"
    )
    payload_identity = _sealed_external_input_hash(identity, relative)
    operation_id = f"youtube_{kind}_{phase}"
    from tools.youtube_podcast.upload_episode import build_operation_marker

    marker = build_operation_marker(
        run_id=str(identity["run_id"]),
        bundle_id=str(identity["bundle_id"]),
        operation_id=operation_id,
        payload_identity=payload_identity,
    )
    return payload_identity, marker


def _validate_youtube_result_identity(
    result: Mapping[str, Any],
    *,
    identity: Mapping[str, Any],
    kind: str,
    phase: str,
    payload_identity: str,
    operation_marker: str,
) -> None:
    """provider result rowがsealed release identityから逸脱していないか検査する。"""

    expected_operation = f"youtube_{kind}_{phase}"
    if (
        str(result.get("operationMarker") or "") != operation_marker
        or str(result.get("operationId") or "") != expected_operation
        or str(result.get("runId") or "") != str(identity["run_id"])
        or str(result.get("bundleId") or "") != str(identity["bundle_id"])
        or str(result.get("payloadIdentity") or "").casefold() != payload_identity
    ):
        raise ProductionAdapterError(f"youtube_{kind}_{phase}_identity_mismatch")


def _canonical_value(value: Any) -> Any:
    """provider結果から有限・秘密値を含まない payload を作る。"""

    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        if isinstance(value, str) and len(value) > 4096:
            return value[:4096] + "…"
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if any(token in key_text.casefold() for token in ("secret", "token", "password", "private_key")):
                result[key_text] = "[redacted]"
            else:
                result[key_text] = _canonical_value(item)
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_canonical_value(item) for item in list(value)[:256]]
    return str(value)


def _provider_payload_identity(operation_id: str, payload: Mapping[str, Any]) -> str:
    """idempotency keyではなくproviderへ渡した実payloadのidentityを返す。"""

    for key in ("input_sha256", "payload_identity"):
        value = str(payload.get(key) or "").strip().casefold()
        if _SHA256_RE.fullmatch(value):
            return value
    release_sha = str(
        payload.get("release_commit_sha")
        or payload.get("observed_remote_sha")
        or ""
    ).strip().casefold()
    if _SHA1_RE.fullmatch(release_sha):
        return hashlib.sha256(f"{operation_id}\0{release_sha}".encode("utf-8")).hexdigest()
    raise ProductionAdapterError(f"provider_payload_identity_unavailable:{operation_id}")


def _receipt(identity: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    safe_payload = _canonical_value(payload)
    encoded = json.dumps(
        safe_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    output_hash = hashlib.sha256(encoded).hexdigest()
    operation_id = str(identity["operation_id"])
    side_effect_id = str(identity["side_effect_id"])
    key = str(identity["idempotency_key"])
    payload_identity = _provider_payload_identity(operation_id, safe_payload)
    # receipt schema は既存 external validator の camelCase boundary に合わせ、
    # canonical internal identity も同値で併記する。値は常に一つの identity。
    return {
        "schemaVersion": EXTERNAL_ADAPTER_RECEIPT_SCHEMA,
        "ok": True,
        "status": "completed",
        "operationId": operation_id,
        "operation_id": operation_id,
        "sideEffectId": side_effect_id,
        "side_effect_id": side_effect_id,
        "idempotencyKey": key,
        "idempotency_key": key,
        "outputHash": output_hash,
        "output_hash": output_hash,
        "providerAckStatus": "unknown_unobtainable",
        "provider_ack_status": "unknown_unobtainable",
        "payloadIdentity": payload_identity,
        "payload_identity": payload_identity,
        "payload": safe_payload,
        "issueDate": str(identity["issue_date"]),
        "issue_date": str(identity["issue_date"]),
        "runId": str(identity["run_id"]),
        "run_id": str(identity["run_id"]),
        "runIntent": str(identity["run_intent"]),
        "run_intent": str(identity["run_intent"]),
        "manifestId": str(identity["manifest_id"]),
        "manifest_id": str(identity["manifest_id"]),
        "bundleId": str(identity["bundle_id"]),
        "bundle_id": str(identity["bundle_id"]),
        "fencingToken": int(identity["fencing_token"]),
        "fencing_token": int(identity["fencing_token"]),
    }


def _binding_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    body = dict(value)
    body["receiptSha256"] = _receipt_hash(body)
    return body


def _binding_location(root: Path, day: str, operation_id: str) -> tuple[Path, bool]:
    if operation_id == "audio_daily_upload":
        return root / "build" / "tts" / "daily" / "latest_audio.json", False
    if operation_id == "audio_deepdive_upload":
        return root / "build" / "tts" / "deepdive" / "latest_audio.json", False
    if operation_id.startswith("youtube_daily_"):
        return root / "build" / "youtube-podcast" / "uploads.json", True
    if operation_id.startswith("youtube_deepdive_"):
        return root / "build" / "youtube-podcast-deepdive" / "uploads.json", True
    if operation_id == "notification_send":
        return root / "build" / "notification" / f"{day}.json", False
    return root / "build" / "external-outbox-bindings" / f"{day}.json", False


def _provider_binding_key(*, run_id: str, bundle_id: str, operation_id: str) -> str:
    if not run_id or not bundle_id or operation_id not in _EXTERNAL_OPERATION_IDS:
        raise ProductionAdapterError("external_provider_binding_composite_identity_invalid")
    return hashlib.sha256(
        f"{run_id}\0{bundle_id}\0{operation_id}".encode("utf-8")
    ).hexdigest()


def _select_external_binding(
    target: Mapping[str, Any],
    *,
    run_id: str,
    bundle_id: str,
    operation_id: str,
) -> Mapping[str, Any] | None:
    """append-only V2 historyから当該releaseだけを選択し、legacyを限定読取する。"""

    key = _provider_binding_key(
        run_id=run_id,
        bundle_id=bundle_id,
        operation_id=operation_id,
    )
    history = target.get("externalOutboxBindingHistoryV2")
    if isinstance(history, Mapping) and isinstance(history.get(key), Mapping):
        return history[key]
    legacy = target.get("externalOutboxBindings")
    candidate = legacy.get(operation_id) if isinstance(legacy, Mapping) else None
    if (
        isinstance(candidate, Mapping)
        and candidate.get("runId") == run_id
        and candidate.get("bundleId") == bundle_id
    ):
        return candidate
    return None


def record_external_provider_binding(
    *,
    context: Mapping[str, Any],
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """provider stateとexternal outbox identityを原子的に結合する。

    provider call後・runtime DB完了前に書くため、DB commit前crashではこの
    canonical bindingを次processのreconcilerが読める。秘密値やwriter leaseは
    永続化しない。
    """

    root = _safe_repo_root(context)
    day = _validate_issue_date(_required_context_value(context, "issue_date", "issueDate"))
    operation_id = str(receipt.get("operation_id") or receipt.get("operationId") or "")
    idempotency_key = str(receipt.get("idempotency_key") or receipt.get("idempotencyKey") or "")
    if (
        operation_id not in _EXTERNAL_OPERATION_IDS
        or not idempotency_key
        or len(idempotency_key.encode("utf-8")) > 2048
        or "\x00" in idempotency_key
        or not _SHA256_RE.fullmatch(
            str(receipt.get("payload_identity") or receipt.get("payloadIdentity") or "").casefold()
        )
    ):
        raise ProductionAdapterError("external_provider_binding_identity_invalid")
    provider_evidence = receipt.get("provider_evidence")
    if not isinstance(provider_evidence, Mapping):
        raise ProductionAdapterError("external_provider_binding_evidence_missing")
    binding = _binding_receipt(
        {
            "schemaVersion": "NEWS_GRASP_EXTERNAL_PROVIDER_BINDING_V1",
            "operationId": operation_id,
            "payloadIdentity": str(receipt.get("payload_identity") or receipt.get("payloadIdentity") or ""),
            "idempotencyKey": idempotency_key,
            "issueDate": day,
            "runId": str(receipt.get("run_id") or ""),
            "runIntent": str(_required_context_value(context, "run_intent", "runIntent")),
            "manifestId": str(receipt.get("manifest_id") or ""),
            "bundleId": str(receipt.get("bundle_id") or ""),
            "releaseCommitSha": str(receipt.get("release_commit_sha") or ""),
            "providerOutputHash": str(receipt.get("output_hash") or ""),
            "providerEvidenceSha256": hashlib.sha256(
                json.dumps(dict(provider_evidence), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
            ).hexdigest(),
            "providerEvidence": dict(provider_evidence),
            "status": "completed",
            "ledgerBound": True,
        }
    )
    path, nested = _binding_location(root, day, operation_id)
    state = _read_json_object(path, "external_provider_binding_state_missing") if path.is_file() else {}
    target = state
    if nested:
        row = state.get(day)
        if not isinstance(row, Mapping):
            raise ProductionAdapterError("external_provider_binding_issue_row_missing")
        target = dict(row)
    bindings = target.get("externalOutboxBindings")
    bindings_value = dict(bindings) if isinstance(bindings, Mapping) else {}
    binding_key = _provider_binding_key(
        run_id=str(binding["runId"]),
        bundle_id=str(binding["bundleId"]),
        operation_id=operation_id,
    )
    history = target.get("externalOutboxBindingHistoryV2")
    history_value = dict(history) if isinstance(history, Mapping) else {}
    existing = history_value.get(binding_key)
    if isinstance(existing, Mapping) and dict(existing) != binding:
        raise ProductionAdapterError("external_provider_binding_conflict")
    history_value[binding_key] = binding
    active = target.get("activeExternalOutboxBindingKeys")
    active_value = dict(active) if isinstance(active, Mapping) else {}
    active_value[operation_id] = binding_key
    # V1 mapはactive projectionとしてだけ維持し、V2 historyをappend-only
    # authorityにする。同日explicit new releaseでも旧bindingを消さない。
    bindings_value[operation_id] = binding
    target["externalOutboxBindings"] = bindings_value
    target["externalOutboxBindingHistoryV2"] = history_value
    target["activeExternalOutboxBindingKeys"] = active_value
    if nested:
        state[day] = target
    else:
        state = target
    _atomic_json(path, state)
    if operation_id == "notification_send":
        # notification stateへprovider bindingを固定した後のfinal bytesだけを
        # distribution producerが一回hashする。reconcileでも同じ境界を通る。
        final_notification_state = _read_json_object(
            path,
            "notification_state_invalid_after_binding",
        )
        _materialize_distribution_evidence(
            {
                "repo_root": root,
                "issue_date": day,
                "run_id": str(receipt.get("run_id") or ""),
                "run_intent": str(_required_context_value(context, "run_intent", "runIntent")),
                "manifest_id": str(receipt.get("manifest_id") or ""),
                "bundle_id": str(receipt.get("bundle_id") or ""),
                "context": context,
            },
            final_notification_state,
        )
    return binding


def _existing_external_binding(identity: Mapping[str, Any]) -> Mapping[str, Any] | None:
    root = identity["repo_root"]
    day = str(identity["issue_date"])
    operation_id = str(identity["operation_id"])
    path, nested = _binding_location(root, day, operation_id)
    if not path.is_file():
        return None
    state = _read_json_object(path, "external_provider_binding_state_invalid")
    target: Mapping[str, Any] = state
    if nested:
        row = state.get(day)
        if not isinstance(row, Mapping):
            return None
        target = row
    binding = _select_external_binding(
        target,
        run_id=str(identity["run_id"]),
        bundle_id=str(identity["bundle_id"]),
        operation_id=operation_id,
    )
    if not isinstance(binding, Mapping):
        return None
    body = {key: item for key, item in binding.items() if key != "receiptSha256"}
    evidence = binding.get("providerEvidence")
    evidence_hash = hashlib.sha256(
        json.dumps(dict(evidence), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest() if isinstance(evidence, Mapping) else ""
    expected_payload_identity = (
        _provider_payload_identity(operation_id, evidence)
        if isinstance(evidence, Mapping)
        else ""
    )
    if (
        binding.get("schemaVersion") != "NEWS_GRASP_EXTERNAL_PROVIDER_BINDING_V1"
        or binding.get("receiptSha256") != _receipt_hash(body)
        or binding.get("operationId") != operation_id
        or binding.get("payloadIdentity") != expected_payload_identity
        or binding.get("idempotencyKey") != identity["idempotency_key"]
        or binding.get("issueDate") != day
        or binding.get("runId") != identity["run_id"]
        or binding.get("runIntent") != identity["run_intent"]
        or binding.get("manifestId") != identity["manifest_id"]
        or binding.get("bundleId") != identity["bundle_id"]
        or binding.get("releaseCommitSha") != _sealed_sha(identity["context"])
        or binding.get("providerEvidenceSha256") != evidence_hash
        or not _SHA256_RE.fullmatch(str(binding.get("providerOutputHash") or ""))
        or binding.get("ledgerBound") is not True
        or binding.get("status") != "completed"
    ):
        raise ProductionAdapterError("external_provider_binding_invalid")
    return binding


def _receipt_from_binding(identity: Mapping[str, Any], binding: Mapping[str, Any]) -> dict[str, Any]:
    evidence = binding.get("providerEvidence")
    if not isinstance(evidence, Mapping):
        raise ProductionAdapterError("external_provider_binding_evidence_missing")
    result = _receipt(identity, dict(evidence))
    if result["outputHash"] != binding.get("providerOutputHash"):
        raise ProductionAdapterError("external_provider_binding_output_hash_mismatch")
    return result


def _invoke_provider(function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """既存 provider の人間向け stdout を machine receipt から分離する。"""

    with redirect_stdout(io.StringIO()):
        return function(*args, **kwargs)


def _audio_path(identity: Mapping[str, Any], *, context_key: str, relative: str) -> Path:
    root = identity["repo_root"]
    expected = root / relative.format(issue_date=identity["issue_date"])
    supplied = _first(identity["context"], context_key, _camelize(context_key), default=str(expected))
    path = _safe_repo_path(root, supplied, expected=expected)
    if not path.is_file():
        raise ProductionAdapterError(f"production_adapter_audio_missing:{relative}")
    return path


def _camelize(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(item[:1].upper() + item[1:] for item in tail)


def _audio_daily_upload(
    *,
    context: Mapping[str, Any],
    operation_id: str,
    side_effect_id: str,
    idempotency_key: str,
    manifest_id: str,
    bundle_id: str,
    run_id: str,
    fencing_token: int,
) -> Mapping[str, Any]:
    identity = _validate_common(
        context=context,
        operation_id=operation_id,
        side_effect_id=side_effect_id,
        idempotency_key=idempotency_key,
        manifest_id=manifest_id,
        bundle_id=bundle_id,
        run_id=run_id,
        fencing_token=fencing_token,
    )
    path = _audio_path(identity, context_key="daily_audio_path", relative="build/tts/{issue_date}.mp3")
    from tools.tts.publish_audio import publish, versioned_audio_url

    result = _invoke_provider(
        publish,
        identity["issue_date"],
        path,
        dry_run=False,
        run_id=identity["run_id"],
        rotate_history=False,
    )
    if not isinstance(result, Mapping):
        raise ProductionAdapterError("daily_audio_provider_result_invalid")
    input_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    if input_hash != _sealed_external_input_hash(identity, f"build/tts/{identity['issue_date']}.mp3"):
        raise ProductionAdapterError("daily_audio_provider_input_hash_mismatch")
    expected_url = versioned_audio_url(identity["issue_date"], path)
    if (
        result.get("latest_audio_date") != identity["issue_date"]
        or result.get("latest_audio_url") != expected_url
    ):
        raise ProductionAdapterError("daily_audio_provider_postcondition_invalid")
    return _receipt(
        identity,
        {
            "provider": "github_release_audio_daily",
            "result": result,
            "path": path,
            "input_sha256": input_hash,
            "release_commit_sha": _sealed_sha(context),
        },
    )


def _audio_deepdive_upload(
    *,
    context: Mapping[str, Any],
    operation_id: str,
    side_effect_id: str,
    idempotency_key: str,
    manifest_id: str,
    bundle_id: str,
    run_id: str,
    fencing_token: int,
) -> Mapping[str, Any]:
    identity = _validate_common(
        context=context,
        operation_id=operation_id,
        side_effect_id=side_effect_id,
        idempotency_key=idempotency_key,
        manifest_id=manifest_id,
        bundle_id=bundle_id,
        run_id=run_id,
        fencing_token=fencing_token,
    )
    path = _audio_path(identity, context_key="deepdive_audio_path", relative="build/tts/deepdive/{issue_date}.mp3")
    from tools.tts.deepdive_audio import publish, versioned_deepdive_audio_url

    result = _invoke_provider(
        publish,
        identity["issue_date"],
        path,
        dry_run=False,
        run_id=identity["run_id"],
    )
    if not isinstance(result, Mapping):
        raise ProductionAdapterError("deepdive_audio_provider_result_invalid")
    input_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    if input_hash != _sealed_external_input_hash(
        identity,
        f"build/tts/deepdive/{identity['issue_date']}.mp3",
    ):
        raise ProductionAdapterError("deepdive_audio_provider_input_hash_mismatch")
    expected_url = versioned_deepdive_audio_url(identity["issue_date"], path)
    if (
        result.get("deepdive_audio_date") != identity["issue_date"]
        or result.get("deepdive_audio_url") != expected_url
    ):
        raise ProductionAdapterError("deepdive_audio_provider_postcondition_invalid")
    return _receipt(
        identity,
        {
            "provider": "github_release_audio_deepdive",
            "result": result,
            "path": path,
            "input_sha256": input_hash,
            "release_commit_sha": _sealed_sha(context),
        },
    )


def _youtube_prepare(
    *,
    context: Mapping[str, Any],
    operation_id: str,
    side_effect_id: str,
    idempotency_key: str,
    manifest_id: str,
    bundle_id: str,
    run_id: str,
    fencing_token: int,
    kind: str,
) -> Mapping[str, Any]:
    identity = _validate_common(
        context=context,
        operation_id=operation_id,
        side_effect_id=side_effect_id,
        idempotency_key=idempotency_key,
        manifest_id=manifest_id,
        bundle_id=bundle_id,
        run_id=run_id,
        fencing_token=fencing_token,
    )
    from tools.youtube_podcast.upload_episode import YouTubeOperationError, prepare

    payload_identity, operation_marker = _youtube_external_identity(
        identity,
        kind=kind,
        phase="prepare",
    )
    try:
        result = _invoke_provider(
            prepare,
            identity["issue_date"],
            kind=kind,
            run_id=str(identity["run_id"]),
            bundle_id=str(identity["bundle_id"]),
            operation_id=str(operation_id),
            payload_identity=payload_identity,
            operation_marker=operation_marker,
        )
    except YouTubeOperationError as exc:
        raise ProductionAdapterError(str(exc)) from exc
    if not isinstance(result, Mapping):
        raise ProductionAdapterError(f"youtube_{kind}_prepare_result_invalid")
    if str(result.get("mp4_sha256") or "").casefold() != payload_identity:
        raise ProductionAdapterError(f"youtube_{kind}_prepare_input_hash_mismatch")
    _validate_youtube_result_identity(
        result,
        identity=identity,
        kind=kind,
        phase="prepare",
        payload_identity=payload_identity,
        operation_marker=operation_marker,
    )
    if (
        result.get("date") != identity["issue_date"]
        or str(result.get("status") or "").casefold() != "private"
        or not str(result.get("videoId") or "")
    ):
        raise ProductionAdapterError(f"youtube_{kind}_prepare_postcondition_invalid")
    return _receipt(
        identity,
        {
            "provider": "youtube_podcast",
            "phase": "prepare",
            "kind": kind,
            "result": result,
            "input_sha256": payload_identity,
            "release_commit_sha": _sealed_sha(context),
        },
    )


def _youtube_finalize(
    *,
    context: Mapping[str, Any],
    operation_id: str,
    side_effect_id: str,
    idempotency_key: str,
    manifest_id: str,
    bundle_id: str,
    run_id: str,
    fencing_token: int,
    kind: str,
) -> Mapping[str, Any]:
    identity = _validate_common(
        context=context,
        operation_id=operation_id,
        side_effect_id=side_effect_id,
        idempotency_key=idempotency_key,
        manifest_id=manifest_id,
        bundle_id=bundle_id,
        run_id=run_id,
        fencing_token=fencing_token,
    )
    from tools.youtube_podcast.upload_episode import YouTubeOperationError, finalize

    payload_identity, operation_marker = _youtube_external_identity(
        identity,
        kind=kind,
        phase="finalize",
    )
    try:
        result = _invoke_provider(
            finalize,
            identity["issue_date"],
            kind=kind,
            run_id=str(identity["run_id"]),
            bundle_id=str(identity["bundle_id"]),
            operation_id=str(operation_id),
            payload_identity=payload_identity,
            operation_marker=operation_marker,
        )
    except YouTubeOperationError as exc:
        raise ProductionAdapterError(str(exc)) from exc
    if not isinstance(result, Mapping):
        raise ProductionAdapterError(f"youtube_{kind}_finalize_result_invalid")
    if str(result.get("mp4_sha256") or "").casefold() != payload_identity:
        raise ProductionAdapterError(f"youtube_{kind}_finalize_input_hash_mismatch")
    _validate_youtube_result_identity(
        result,
        identity=identity,
        kind=kind,
        phase="finalize",
        payload_identity=payload_identity,
        operation_marker=operation_marker,
    )
    if (
        result.get("date") != identity["issue_date"]
        or str(result.get("status") or "").casefold() != "public"
        or any(not str(result.get(field) or "") for field in ("videoId", "playlistId", "playlistItemId"))
        or (
            kind == "deepdive"
            and any(
                not str(result.get(field) or "")
                for field in ("primaryPodcastPlaylistId", "primaryPodcastPlaylistItemId")
            )
        )
    ):
        raise ProductionAdapterError(f"youtube_{kind}_finalize_postcondition_invalid")
    return _receipt(
        identity,
        {
            "provider": "youtube_podcast",
            "phase": "finalize",
            "kind": kind,
            "result": result,
            "input_sha256": payload_identity,
            "release_commit_sha": _sealed_sha(context),
        },
    )


def _run_git(root: Path, args: list[str], *, timeout: float = 30.0) -> tuple[int, str, str]:
    """Windowsでも黒窓を出さない read-only git 境界。"""

    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            shell=False,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProductionAdapterError(f"production_adapter_git_observation_failed:{type(exc).__name__}") from exc
    return int(completed.returncode), str(completed.stdout or ""), str(completed.stderr or "")


def _git_sha(root: Path, *args: str) -> str:
    returncode, stdout, _stderr = _run_git(root, list(args))
    value = stdout.strip().splitlines()[0] if stdout.strip() else ""
    if returncode != 0 or not _SHA1_RE.fullmatch(value):
        raise ProductionAdapterError("production_adapter_git_sha_unavailable")
    return value.casefold()


def _remote_ref_sha(root: Path) -> str:
    return _git_sha(root, "rev-parse", "--verify", "refs/remotes/origin/main")


def _remote_target_sha(root: Path) -> str:
    """origin/main の実リモート値を read-only で取得する。"""

    returncode, stdout, _stderr = _run_git(
        root,
        ["ls-remote", "--exit-code", "--end-of-options", "origin", "refs/heads/main"],
    )
    value = stdout.strip().split()[0] if stdout.strip() else ""
    if returncode != 0 or not _SHA1_RE.fullmatch(value):
        raise ProductionAdapterError("production_adapter_remote_target_sha_unavailable")
    return value.casefold()


def _verify_push_identity(identity: Mapping[str, Any]) -> tuple[str, str]:
    root = identity["repo_root"]
    context = identity["context"]
    release_sha = _sealed_sha(context)
    remote_base_sha = _sealed_remote_base_sha(context)
    head = _git_sha(root, "rev-parse", "--verify", "HEAD")
    if head != release_sha:
        raise ProductionAdapterError("git_release_head_sealed_sha_mismatch")
    returncode, branch_ref, _stderr = _run_git(root, ["symbolic-ref", "-q", "HEAD"])
    if returncode == 0 and branch_ref.strip() != "refs/heads/main":
        raise ProductionAdapterError("git_release_main_or_detached_required")
    observed_remote_base = _remote_ref_sha(root)
    if observed_remote_base != remote_base_sha:
        raise ProductionAdapterError("git_release_remote_base_sha_cas_mismatch")
    observed_remote_target = _remote_target_sha(root)
    if observed_remote_target != remote_base_sha:
        raise ProductionAdapterError("git_release_remote_target_sha_cas_mismatch")
    return release_sha, remote_base_sha


def _git_release_push(
    *,
    context: Mapping[str, Any],
    operation_id: str,
    side_effect_id: str,
    idempotency_key: str,
    manifest_id: str,
    bundle_id: str,
    run_id: str,
    fencing_token: int,
) -> Mapping[str, Any]:
    identity = _validate_common(
        context=context,
        operation_id=operation_id,
        side_effect_id=side_effect_id,
        idempotency_key=idempotency_key,
        manifest_id=manifest_id,
        bundle_id=bundle_id,
        run_id=run_id,
        fencing_token=fencing_token,
    )
    release_sha, remote_base_sha = _verify_push_identity(identity)
    result, stdout, stderr = _run_git(
        identity["repo_root"],
        [
            "push",
            "--porcelain",
            "--atomic",
            "origin",
            f"{release_sha}:refs/heads/main",
        ],
        timeout=120.0,
    )
    if result != 0:
        raise ProductionAdapterError(f"git_release_push_failed:{result}:{stderr.strip()[:256]}")
    observed_remote = _remote_target_sha(identity["repo_root"])
    if observed_remote != release_sha:
        raise ProductionAdapterError("git_release_push_remote_sha_mismatch")
    return _receipt(
        identity,
        {
            "provider": "git",
            "remote": "origin",
            "branch": "main",
            "release_commit_sha": release_sha,
            "remote_base_sha": remote_base_sha,
            "return_code": int(result),
            "stdout": stdout.strip()[:512],
            "observed_remote_sha": observed_remote,
        },
    )


def _pages_workflow_once(identity: Mapping[str, Any], release_sha: str) -> Mapping[str, Any]:
    from tools.news_grasp_direct_completion import _pages_workflow_observation

    context = identity["context"]
    changed_paths_value = _first(context, "exact_write_set", "exactWriteSet", "write_set", "writeSet", default=[])
    changed_paths = [str(item) for item in changed_paths_value] if isinstance(changed_paths_value, (list, tuple)) else []
    observed = _pages_workflow_observation(
        remote_head=release_sha,
        manifest_id=str(identity["manifest_id"]),
        issue_date=str(identity["issue_date"]),
        release_kind="public",
        changed_paths=changed_paths,
    )
    if not isinstance(observed, Mapping):
        raise ProductionAdapterError("pages_workflow_observation_invalid")
    return observed


def _pages_deployment_wait(
    *,
    context: Mapping[str, Any],
    operation_id: str,
    side_effect_id: str,
    idempotency_key: str,
    manifest_id: str,
    bundle_id: str,
    run_id: str,
    fencing_token: int,
) -> Mapping[str, Any]:
    identity = _validate_common(
        context=context,
        operation_id=operation_id,
        side_effect_id=side_effect_id,
        idempotency_key=idempotency_key,
        manifest_id=manifest_id,
        bundle_id=bundle_id,
        run_id=run_id,
        fencing_token=fencing_token,
    )
    release_sha = _sealed_sha(context)
    # Pages wait は外部副作用を持たないため、bounded read-only pollだけを許す。
    try:
        wait_seconds = float(_first(context, "pages_wait_seconds", "pagesWaitSeconds", default=120.0))
        poll_seconds = float(_first(context, "pages_poll_seconds", "pagesPollSeconds", default=10.0))
    except (TypeError, ValueError) as exc:
        raise ProductionAdapterError("pages_poll_budget_invalid") from exc
    if wait_seconds < 0 or poll_seconds < 0:
        raise ProductionAdapterError("pages_poll_budget_invalid")
    deadline = time.monotonic() + wait_seconds
    attempts = 0
    last: Mapping[str, Any] | None = None
    while True:
        attempts += 1
        last = _pages_workflow_once(identity, release_sha)
        binding = last.get("deploymentBinding") if isinstance(last.get("deploymentBinding"), Mapping) else {}
        observed_sha = str(
            binding.get("deploymentSha")
            or binding.get("deployment_sha")
            or ""
        ).strip().casefold()
        if last.get("ok") is True and observed_sha == release_sha:
            return _receipt(
                identity,
                {
                    "provider": "github-pages",
                    "attempts": attempts,
                    "release_commit_sha": release_sha,
                    "observation": last,
                },
            )
        if time.monotonic() >= deadline or poll_seconds == 0:
            reason = "pages_deployment_not_ready"
            if observed_sha and observed_sha != release_sha:
                reason = "pages_deployment_sha_mismatch"
            raise ProductionAdapterError(reason)
        time.sleep(min(poll_seconds, max(0.0, deadline - time.monotonic())))


def _materialize_distribution_evidence(
    identity: Mapping[str, Any],
    notification_state: Mapping[str, Any],
) -> dict[str, Any]:
    """全provider完了後の可変状態をmanifest rebindなしで同一outboxへ束縛する。"""

    root = Path(identity["repo_root"])
    day = str(identity["issue_date"])
    notification_path = root / "build" / "notification" / f"{day}.json"
    live_notification_state = _read_json_object(
        notification_path,
        "distribution_notification_state_invalid",
    )
    if dict(live_notification_state) != dict(notification_state):
        raise ProductionAdapterError("distribution_notification_state_binding_drift")
    daily_path = root / "build" / "youtube-podcast" / "uploads.json"
    deep_path = root / "build" / "youtube-podcast-deepdive" / "uploads.json"
    daily_all = _read_json_object(daily_path, "distribution_youtube_daily_state_invalid")
    deep_all = _read_json_object(deep_path, "distribution_youtube_deepdive_state_invalid")
    daily = daily_all.get(day)
    deep = deep_all.get(day)
    if not isinstance(daily, Mapping) or not isinstance(deep, Mapping):
        raise ProductionAdapterError("distribution_youtube_receipt_missing")
    for kind, row in (("daily", daily), ("deepdive", deep)):
        if str(row.get("status") or "") != "public":
            raise ProductionAdapterError(f"distribution_youtube_{kind}_not_public")
        for field in ("videoId", "playlistId", "playlistItemId"):
            if not str(row.get(field) or ""):
                raise ProductionAdapterError(f"distribution_youtube_{kind}_{field}_missing")
    daily_audio_path = root / "build" / "tts" / "daily" / "latest_audio.json"
    deep_audio_path = root / "build" / "tts" / "deepdive" / "latest_audio.json"
    daily_audio = _read_json_object(daily_audio_path, "distribution_daily_audio_state_invalid")
    deep_audio = _read_json_object(deep_audio_path, "distribution_deepdive_audio_state_invalid")
    if any(
        str(row.get("issueDate") or "") != day
        or str(row.get("runId") or "") != str(identity["run_id"])
        for row in (daily_audio, deep_audio)
    ):
        raise ProductionAdapterError("distribution_audio_identity_mismatch")
    sent_count = int(notification_state.get("sentCount") or notification_state.get("sent_count") or 0)
    subscription_count = int(notification_state.get("subscriptionCount") or notification_state.get("subscription_count") or 0)
    if (
        str(notification_state.get("runId") or notification_state.get("run_id") or "") != str(identity["run_id"])
        or str(notification_state.get("runIntent") or notification_state.get("run_intent") or "") != str(identity["run_intent"])
    ):
        raise ProductionAdapterError("distribution_notification_run_identity_mismatch")
    if sent_count != subscription_count or sent_count <= 0:
        raise ProductionAdapterError("distribution_notification_delivery_count_invalid")

    release_sha = _sealed_sha(identity["context"])

    playlist = {
        "schemaVersion": "NEWS_GRASP_PLAYLIST_BINDING_V2",
        "issueDate": day,
        "runId": str(identity["run_id"]),
        "runIntent": str(identity["run_intent"]),
        "manifestId": str(identity["manifest_id"]),
        "bundleId": str(identity["bundle_id"]),
        "releaseCommitSha": release_sha,
        "status": "verified",
        "daily": {key: daily[key] for key in daily if key in {"videoId", "playlistId", "playlistItemId"}},
        "deepdive": {
            key: deep[key]
            for key in deep
            if key in {
                "videoId", "playlistId", "playlistItemId",
                "primaryPodcastPlaylistId", "primaryPodcastPlaylistItemId",
            }
        },
        "sourceReceipts": {
            "dailySha256": hashlib.sha256(daily_path.read_bytes()).hexdigest(),
            "deepdiveSha256": hashlib.sha256(deep_path.read_bytes()).hexdigest(),
        },
    }
    playlist["receiptSha256"] = _receipt_hash(playlist)
    # retry/reconcileでもbytesが変わらないよう、送信stateの固定時刻を使用する。
    generated_at = str(
        notification_state.get("recorded_at")
        or notification_state.get("recordedAt")
        or (
            notification_state.get("deliveryReceiptV2", {}).get("verifiedAt")
            if isinstance(notification_state.get("deliveryReceiptV2"), Mapping)
            else ""
        )
        or f"{day}T00:00:00+09:00"
    ).strip()
    distribution = {
        "schemaVersion": "NEWS_GRASP_DIRECT_DISTRIBUTION_V2",
        "ok": True,
        "status": "published_ok",
        "reason": "",
        "date": day,
        "run_id": str(identity["run_id"]),
        "run_intent": str(identity["run_intent"]),
        "manifest_id": str(identity["manifest_id"]),
        "bundle_id": str(identity["bundle_id"]),
        "pre_publish_commit": release_sha,
        "publish_commit": release_sha,
        "publish_commit_resolution": "sealed_release_commit",
        "same_publish_contract": "publish_commit_equals_sealed_release_commit",
        "checks": {
            "daily_podcast_public": True,
            "deepdive_podcast_public": True,
            "deepdive_primary_playlist": bool(deep.get("primaryPodcastPlaylistId")),
            "daily_audio_current": True,
            "deepdive_audio_current": True,
            "notification_sent": True,
        },
        "primary_podcast_state": "build/youtube-podcast/uploads.json",
        "deepdive_podcast_state": "build/youtube-podcast-deepdive/uploads.json",
        "latest_audio_state": "build/tts/daily/latest_audio.json",
        "deepdive_audio_state": "build/tts/deepdive/latest_audio.json",
        "notification_state": f"build/notification/{day}.json",
        "notification": {
            "status": "sent", "sent_count": sent_count,
            "subscription_count": subscription_count,
            "state_path": f"build/notification/{day}.json",
        },
        "generated_at": generated_at,
        "playlist": {"status": "public", "daily": dict(playlist["daily"]), "deepdive": dict(playlist["deepdive"])},
        "value": {
            "date": day,
            "primary_podcast_state": "build/youtube-podcast/uploads.json",
            "deepdive_podcast_state": "build/youtube-podcast-deepdive/uploads.json",
            "latest_audio_state": "build/tts/daily/latest_audio.json",
            "deepdive_audio_state": "build/tts/deepdive/latest_audio.json",
            "generated_at": generated_at,
            "playlist": "public",
            "notification": "sent",
        },
    }
    distribution_path = root / "data" / "distribution" / f"{day}.json"
    playlist_path = root / "build" / "distribution" / day / "playlist.json"
    _atomic_json(distribution_path, distribution)
    _atomic_json(playlist_path, playlist)
    binding = {
        "schemaVersion": "NEWS_GRASP_DISTRIBUTION_BINDING_V2",
        "issueDate": day,
        "runId": str(identity["run_id"]),
        "runIntent": str(identity["run_intent"]),
        "manifestId": str(identity["manifest_id"]),
        "bundleId": str(identity["bundle_id"]),
        "releaseCommitSha": release_sha,
        "status": "verified",
        "distributionSha256": hashlib.sha256(distribution_path.read_bytes()).hexdigest(),
        "dailyAudioProjectionSha256": hashlib.sha256(daily_audio_path.read_bytes()).hexdigest(),
        "deepdiveAudioProjectionSha256": hashlib.sha256(deep_audio_path.read_bytes()).hexdigest(),
        "notificationStateSha256": hashlib.sha256(notification_path.read_bytes()).hexdigest(),
        "youtubeDailyStateSha256": hashlib.sha256(daily_path.read_bytes()).hexdigest(),
        "youtubeDeepdiveStateSha256": hashlib.sha256(deep_path.read_bytes()).hexdigest(),
        "playlistBindingStateSha256": hashlib.sha256(playlist_path.read_bytes()).hexdigest(),
        "playlistReceiptSha256": playlist["receiptSha256"],
    }
    binding["receiptSha256"] = _receipt_hash(binding)
    binding_path = root / "build" / "distribution" / day / "binding.json"
    _atomic_json(binding_path, binding)
    return {
        "distribution_path": distribution_path.relative_to(root).as_posix(),
        "playlist_path": playlist_path.relative_to(root).as_posix(),
        "binding_path": binding_path.relative_to(root).as_posix(),
        "distribution_sha256": hashlib.sha256(distribution_path.read_bytes()).hexdigest(),
        "binding_receipt_sha256": binding["receiptSha256"],
    }


def _notification_send(
    *,
    context: Mapping[str, Any],
    operation_id: str,
    side_effect_id: str,
    idempotency_key: str,
    manifest_id: str,
    bundle_id: str,
    run_id: str,
    fencing_token: int,
) -> Mapping[str, Any]:
    identity = _validate_common(
        context=context,
        operation_id=operation_id,
        side_effect_id=side_effect_id,
        idempotency_key=idempotency_key,
        manifest_id=manifest_id,
        bundle_id=bundle_id,
        run_id=run_id,
        fencing_token=fencing_token,
    )
    root = identity["repo_root"]
    day = str(identity["issue_date"])
    state_path = _safe_repo_path(root, root / "build" / "notification" / f"{day}.json")
    public_base = str(_first(context, "public_base_url", "publicBaseUrl", default=PUBLIC_BASE_URL)).rstrip("/") + "/"
    target_url = urljoin(public_base, f"{day}/")
    from tools import send_push

    body = send_push.default_body_for_today(datetime.strptime(day, "%Y-%m-%d").weekday())
    argv = [
        str(root / "tools" / "send_push.py"),
        "--url",
        target_url,
        "--body",
        body,
        "--record-state",
        str(state_path),
        "--run-id",
        str(identity["run_id"]),
        "--run-intent",
        str(identity["run_intent"]),
        "--skip-prune",
    ]
    with _NOTIFICATION_ARG_LOCK:
        original_argv = sys.argv
        original_today = send_push._today_jst_str
        try:
            sys.argv = argv
            send_push._today_jst_str = lambda: day
            return_code = int(_invoke_provider(send_push.main))
        finally:
            send_push._today_jst_str = original_today
            sys.argv = original_argv
    if return_code != 0:
        raise ProductionAdapterError(f"notification_send_failed:{return_code}")
    if not state_path.is_file():
        raise ProductionAdapterError("notification_state_missing_after_send")
    try:
        state = json.loads(state_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProductionAdapterError("notification_state_invalid_after_send") from exc
    if not isinstance(state, Mapping) or str(state.get("date") or "") != day:
        raise ProductionAdapterError("notification_state_issue_date_mismatch")
    state_run_id = str(state.get("run_id") or state.get("runId") or "")
    state_run_intent = str(state.get("run_intent") or state.get("runIntent") or "")
    if state_run_id != str(identity["run_id"]) or state_run_intent != str(identity["run_intent"]):
        raise ProductionAdapterError("notification_state_run_identity_mismatch")
    state_status = str(state.get("status") or "")
    if state_status not in {"sent", "already_sent"}:
        raise ProductionAdapterError("notification_state_not_sent")
    subscription_count = int(state.get("subscriptionCount") or state.get("subscription_count") or 0)
    sent_count = int(state.get("sentCount") or state.get("sent_count") or 0)
    if subscription_count <= 0 or sent_count != subscription_count:
        raise ProductionAdapterError("notification_state_delivery_count_invalid")
    delivery_v2 = state.get("deliveryReceiptV2")
    payload_identity = str(
        delivery_v2.get("payloadIdentity") if isinstance(delivery_v2, Mapping) else ""
    ).casefold()
    if not _SHA256_RE.fullmatch(payload_identity):
        raise ProductionAdapterError("notification_payload_identity_missing")
    provider_receipt = _receipt(
        identity,
        {
            "provider": "web-push",
            "return_code": return_code,
            "state_path": state_path,
            "state_status": state_status,
            "subscription_count": subscription_count,
            "sent_count": sent_count,
            "payload_identity": payload_identity,
        },
    )
    return provider_receipt


def audio_daily_upload(
    *,
    context: Mapping[str, Any],
    operation_id: str,
    side_effect_id: str,
    idempotency_key: str,
    manifest_id: str,
    bundle_id: str,
    run_id: str,
    fencing_token: int,
) -> Mapping[str, Any]:
    return _audio_daily_upload(
        context=context,
        operation_id=operation_id,
        side_effect_id=side_effect_id,
        idempotency_key=idempotency_key,
        manifest_id=manifest_id,
        bundle_id=bundle_id,
        run_id=run_id,
        fencing_token=fencing_token,
    )


def audio_deepdive_upload(
    *,
    context: Mapping[str, Any],
    operation_id: str,
    side_effect_id: str,
    idempotency_key: str,
    manifest_id: str,
    bundle_id: str,
    run_id: str,
    fencing_token: int,
) -> Mapping[str, Any]:
    return _audio_deepdive_upload(
        context=context,
        operation_id=operation_id,
        side_effect_id=side_effect_id,
        idempotency_key=idempotency_key,
        manifest_id=manifest_id,
        bundle_id=bundle_id,
        run_id=run_id,
        fencing_token=fencing_token,
    )


def youtube_daily_prepare(
    *,
    context: Mapping[str, Any],
    operation_id: str,
    side_effect_id: str,
    idempotency_key: str,
    manifest_id: str,
    bundle_id: str,
    run_id: str,
    fencing_token: int,
) -> Mapping[str, Any]:
    return _youtube_prepare(
        kind="daily",
        context=context,
        operation_id=operation_id,
        side_effect_id=side_effect_id,
        idempotency_key=idempotency_key,
        manifest_id=manifest_id,
        bundle_id=bundle_id,
        run_id=run_id,
        fencing_token=fencing_token,
    )


def youtube_deepdive_prepare(
    *,
    context: Mapping[str, Any],
    operation_id: str,
    side_effect_id: str,
    idempotency_key: str,
    manifest_id: str,
    bundle_id: str,
    run_id: str,
    fencing_token: int,
) -> Mapping[str, Any]:
    return _youtube_prepare(
        kind="deepdive",
        context=context,
        operation_id=operation_id,
        side_effect_id=side_effect_id,
        idempotency_key=idempotency_key,
        manifest_id=manifest_id,
        bundle_id=bundle_id,
        run_id=run_id,
        fencing_token=fencing_token,
    )


def git_release_push(
    *,
    context: Mapping[str, Any],
    operation_id: str,
    side_effect_id: str,
    idempotency_key: str,
    manifest_id: str,
    bundle_id: str,
    run_id: str,
    fencing_token: int,
) -> Mapping[str, Any]:
    return _git_release_push(
        context=context,
        operation_id=operation_id,
        side_effect_id=side_effect_id,
        idempotency_key=idempotency_key,
        manifest_id=manifest_id,
        bundle_id=bundle_id,
        run_id=run_id,
        fencing_token=fencing_token,
    )


def pages_deployment_wait(
    *,
    context: Mapping[str, Any],
    operation_id: str,
    side_effect_id: str,
    idempotency_key: str,
    manifest_id: str,
    bundle_id: str,
    run_id: str,
    fencing_token: int,
) -> Mapping[str, Any]:
    return _pages_deployment_wait(
        context=context,
        operation_id=operation_id,
        side_effect_id=side_effect_id,
        idempotency_key=idempotency_key,
        manifest_id=manifest_id,
        bundle_id=bundle_id,
        run_id=run_id,
        fencing_token=fencing_token,
    )


def youtube_daily_finalize(
    *,
    context: Mapping[str, Any],
    operation_id: str,
    side_effect_id: str,
    idempotency_key: str,
    manifest_id: str,
    bundle_id: str,
    run_id: str,
    fencing_token: int,
) -> Mapping[str, Any]:
    return _youtube_finalize(
        kind="daily",
        context=context,
        operation_id=operation_id,
        side_effect_id=side_effect_id,
        idempotency_key=idempotency_key,
        manifest_id=manifest_id,
        bundle_id=bundle_id,
        run_id=run_id,
        fencing_token=fencing_token,
    )


def youtube_deepdive_finalize(
    *,
    context: Mapping[str, Any],
    operation_id: str,
    side_effect_id: str,
    idempotency_key: str,
    manifest_id: str,
    bundle_id: str,
    run_id: str,
    fencing_token: int,
) -> Mapping[str, Any]:
    return _youtube_finalize(
        kind="deepdive",
        context=context,
        operation_id=operation_id,
        side_effect_id=side_effect_id,
        idempotency_key=idempotency_key,
        manifest_id=manifest_id,
        bundle_id=bundle_id,
        run_id=run_id,
        fencing_token=fencing_token,
    )


def notification_send(
    *,
    context: Mapping[str, Any],
    operation_id: str,
    side_effect_id: str,
    idempotency_key: str,
    manifest_id: str,
    bundle_id: str,
    run_id: str,
    fencing_token: int,
) -> Mapping[str, Any]:
    return _notification_send(
        context=context,
        operation_id=operation_id,
        side_effect_id=side_effect_id,
        idempotency_key=idempotency_key,
        manifest_id=manifest_id,
        bundle_id=bundle_id,
        run_id=run_id,
        fencing_token=fencing_token,
    )


def _completion_distribution_assets(identity: Mapping[str, Any]) -> list[dict[str, Any]]:
    root = Path(identity["repo_root"])
    day = str(identity["issue_date"])
    run_id = str(identity["run_id"])
    sources = (
        ("distribution", root / "data" / "distribution" / f"{day}.json"),
        ("distribution-binding", root / "build" / "distribution" / day / "binding.json"),
        ("playlist-binding", root / "build" / "distribution" / day / "playlist.json"),
        ("notification-ledger", root / "build" / "notification" / f"{day}.json"),
        ("daily-audio-projection", root / "build" / "tts" / "daily" / "latest_audio.json"),
        ("deepdive-audio-projection", root / "build" / "tts" / "deepdive" / "latest_audio.json"),
        ("youtube-daily-state", root / "build" / "youtube-podcast" / "uploads.json"),
        ("youtube-deepdive-state", root / "build" / "youtube-podcast-deepdive" / "uploads.json"),
    )
    assets: list[dict[str, Any]] = []
    for artifact_id, source in sources:
        path = _safe_repo_path(root, source, expected=source)
        if not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
            raise ProductionAdapterError(f"completion_distribution_asset_invalid:{artifact_id}")
        raw = path.read_bytes()
        try:
            value = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ProductionAdapterError(f"completion_distribution_asset_invalid:{artifact_id}") from exc
        if not isinstance(value, Mapping):
            raise ProductionAdapterError(f"completion_distribution_asset_invalid:{artifact_id}")
        observed_run_id = str(value.get("runId") or value.get("run_id") or "")
        observed_intent = str(value.get("runIntent") or value.get("run_intent") or "")
        if observed_run_id != run_id or observed_intent != identity["run_intent"]:
            raise ProductionAdapterError(f"completion_distribution_asset_identity_mismatch:{artifact_id}")
        if artifact_id in {"distribution-binding", "playlist-binding"} and (
            value.get("receiptSha256") != _receipt_hash(value)
            or value.get("manifestId") != identity["manifest_id"]
            or value.get("bundleId") != identity["bundle_id"]
            or value.get("releaseCommitSha") != _sealed_sha(identity["context"])
        ):
            raise ProductionAdapterError(f"completion_distribution_asset_binding_invalid:{artifact_id}")
        filename = f"{day}-{run_id}-{artifact_id}.json"
        url = (
            "https://github.com/HIDEPON-UMG/News-Grasp/releases/download/"
            f"audio-daily/{filename}"
        )
        assets.append(
            {
                "artifactId": artifact_id,
                "localPath": path.relative_to(root).as_posix(),
                "publicUrl": url,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size": len(raw),
                "raw": raw,
            }
        )
    return assets


def _completion_attestation_bytes(identity: Mapping[str, Any]) -> bytes:
    root = identity["repo_root"]
    day = str(identity["issue_date"])
    rows: list[dict[str, Any]] = []
    for prior_operation in _EXTERNAL_OPERATION_IDS:
        if prior_operation == "completion_attestation_publish":
            continue
        path, nested = _binding_location(root, day, prior_operation)
        state = _read_json_object(path, "completion_attestation_binding_missing")
        target: Mapping[str, Any] = state
        if nested:
            issue_row = state.get(day)
            if not isinstance(issue_row, Mapping):
                raise ProductionAdapterError("completion_attestation_binding_missing")
            target = issue_row
        binding = _select_external_binding(
            target,
            run_id=str(identity["run_id"]),
            bundle_id=str(identity["bundle_id"]),
            operation_id=prior_operation,
        )
        if not isinstance(binding, Mapping):
            raise ProductionAdapterError(f"completion_attestation_binding_missing:{prior_operation}")
        body = {key: item for key, item in binding.items() if key != "receiptSha256"}
        if (
            binding.get("schemaVersion") != "NEWS_GRASP_EXTERNAL_PROVIDER_BINDING_V1"
            or binding.get("receiptSha256") != _receipt_hash(body)
            or binding.get("operationId") != prior_operation
            or binding.get("issueDate") != day
            or binding.get("runId") != identity["run_id"]
            or binding.get("runIntent") != identity["run_intent"]
            or binding.get("manifestId") != identity["manifest_id"]
            or binding.get("bundleId") != identity["bundle_id"]
            or binding.get("releaseCommitSha") != _sealed_sha(identity["context"])
            or binding.get("status") != "completed"
            or binding.get("ledgerBound") is not True
            or not _SHA256_RE.fullmatch(str(binding.get("payloadIdentity") or ""))
        ):
            raise ProductionAdapterError(f"completion_attestation_binding_invalid:{prior_operation}")
        rows.append(
            {
                "operationId": prior_operation,
                "payloadIdentity": str(binding["payloadIdentity"]),
                "providerOutputHash": str(binding.get("providerOutputHash") or ""),
                "providerEvidenceSha256": str(binding.get("providerEvidenceSha256") or ""),
                "providerBindingReceiptSha256": str(binding.get("receiptSha256") or ""),
                "providerAckStatus": "unknown_unobtainable",
            }
        )
    distribution_assets = _completion_distribution_assets(identity)
    value = {
        "schemaVersion": "NEWS_GRASP_PUBLIC_COMPLETION_ATTESTATION_V1",
        "issueDate": day,
        "runId": str(identity["run_id"]),
        "runIntent": str(identity["run_intent"]),
        "manifestId": str(identity["manifest_id"]),
        "bundleId": str(identity["bundle_id"]),
        "releaseCommitSha": _sealed_sha(identity["context"]),
        "operations": rows,
        "distributionArtifacts": [
            {key: item for key, item in row.items() if key != "raw"}
            for row in distribution_assets
        ],
        "providerDeliveryAckStatus": "unknown_unobtainable",
    }
    return _json_file_bytes(value)


def _completion_attestation_url(identity: Mapping[str, Any]) -> str:
    filename = f"{identity['issue_date']}-{identity['run_id']}-completion.json"
    if not re.fullmatch(r"[0-9A-Za-z._-]+", filename):
        raise ProductionAdapterError("completion_attestation_filename_invalid")
    return (
        "https://github.com/HIDEPON-UMG/News-Grasp/releases/download/"
        f"audio-daily/{filename}"
    )


def _public_bytes_match(url: str, expected: bytes) -> bool:
    try:
        request = urllib.request.Request(
            url,
            headers={"Cache-Control": "no-cache", "Pragma": "no-cache"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            observed = response.read(len(expected) + 1)
            return int(getattr(response, "status", 200)) == 200 and observed == expected
    except (OSError, urllib.error.URLError, ValueError):
        return False


def _completion_attestation_publish(
    *,
    context: Mapping[str, Any],
    operation_id: str,
    side_effect_id: str,
    idempotency_key: str,
    manifest_id: str,
    bundle_id: str,
    run_id: str,
    fencing_token: int,
) -> Mapping[str, Any]:
    identity = _validate_common(
        context=context,
        operation_id=operation_id,
        side_effect_id=side_effect_id,
        idempotency_key=idempotency_key,
        manifest_id=manifest_id,
        bundle_id=bundle_id,
        run_id=run_id,
        fencing_token=fencing_token,
    )
    distribution_assets = _completion_distribution_assets(identity)
    raw = _completion_attestation_bytes(identity)
    payload_identity = hashlib.sha256(raw).hexdigest()
    url = _completion_attestation_url(identity)
    pending_assets = [
        (str(item["publicUrl"]), bytes(item["raw"]))
        for item in distribution_assets
        if not _public_bytes_match(str(item["publicUrl"]), bytes(item["raw"]))
    ]
    if not _public_bytes_match(url, raw):
        pending_assets.append((url, raw))
    if pending_assets:
        from tools.tts import proc

        view = proc.quiet_run(
            ["gh", "release", "view", "audio-daily", "--json", "tagName"],
            check=False,
            timeout=120,
        )
        if view.returncode != 0:
            raise ProductionAdapterError("completion_attestation_release_missing")
        with tempfile.TemporaryDirectory(prefix="news-grasp-completion-attestation-") as temp_root:
            targets: list[str] = []
            for asset_url, asset_raw in pending_assets:
                target = Path(temp_root) / asset_url.rsplit("/", 1)[-1]
                target.write_bytes(asset_raw)
                targets.append(str(target))
            proc.quiet_run(
                ["gh", "release", "upload", "audio-daily", *targets],
                timeout=120,
            )
    for item in distribution_assets:
        if not _public_bytes_match(str(item["publicUrl"]), bytes(item["raw"])):
            raise ProductionAdapterError(
                f"completion_distribution_asset_public_hash_mismatch:{item['artifactId']}"
            )
    if not _public_bytes_match(url, raw):
        raise ProductionAdapterError("completion_attestation_public_hash_mismatch")
    return _receipt(
        identity,
        {
            "provider": "github_release_completion_attestation",
            "attestation_url": url,
            "payload_identity": payload_identity,
            "operation_count": len(_EXTERNAL_OPERATION_IDS) - 1,
            "distribution_artifact_count": len(distribution_assets),
            "release_commit_sha": _sealed_sha(context),
        },
    )


def completion_attestation_publish(
    **kwargs: Any,
) -> Mapping[str, Any]:
    return _completion_attestation_publish(**kwargs)


def reconcile_external_operation(
    *,
    context: Mapping[str, Any],
    operation_id: str,
    side_effect_id: str,
    idempotency_key: str,
    manifest_id: str,
    bundle_id: str,
    run_id: str,
    fencing_token: int,
) -> Mapping[str, Any]:
    """fresh provider stateを照合し、substep ledger上の未開始分だけを再開する。"""

    identity = _validate_common(
        context=context,
        operation_id=operation_id,
        side_effect_id=side_effect_id,
        idempotency_key=idempotency_key,
        manifest_id=manifest_id,
        bundle_id=bundle_id,
        run_id=run_id,
        fencing_token=fencing_token,
    )
    existing = _existing_external_binding(identity)
    if existing is not None:
        if operation_id == "notification_send":
            state = _read_json_object(
                identity["repo_root"] / "build" / "notification" / f"{identity['issue_date']}.json",
                "notification_reconcile_state_missing",
            )
            _materialize_distribution_evidence(identity, state)
        return _receipt_from_binding(identity, existing)

    root = identity["repo_root"]
    day = str(identity["issue_date"])
    release_sha = _sealed_sha(context)
    if operation_id in {"audio_daily_upload", "audio_deepdive_upload"}:
        kind = "daily" if operation_id == "audio_daily_upload" else "deepdive"
        state_path = root / "build" / "tts" / kind / "latest_audio.json"
        from tools.news_grasp_audio_projection import _probe_public_audio, validate_audio_projection

        source_relative = (
            f"build/tts/{day}.mp3"
            if kind == "daily"
            else f"build/tts/deepdive/{day}.mp3"
        )
        expected_input = _sealed_external_input_hash(identity, source_relative)
        source_path = root / source_relative
        if kind == "daily":
            from tools.tts.publish_audio import versioned_audio_url, write_latest_audio

            url = versioned_audio_url(day, source_path)
        else:
            from tools.tts.deepdive_audio import versioned_deepdive_audio_url, write_latest_audio

            url = versioned_deepdive_audio_url(day, source_path)
        public_observation = _probe_public_audio(url, expected_sha256=expected_input)
        if public_observation.get("ok") is not True:
            raise ProductionAdapterError("audio_reconcile_public_hash_unconfirmed")
        state = _read_json_object(state_path, "audio_reconcile_state_missing") if state_path.is_file() else {}
        validation = validate_audio_projection(
            state,
            issue_date=day,
            run_intent=str(identity["run_intent"]),
            expected_run_id=str(identity["run_id"]),
        )
        if validation.get("ok") is not True or str(state.get("publicUrl") or "") != url:
            write_latest_audio(
                day,
                url,
                run_id=str(identity["run_id"]),
                run_intent=str(identity["run_intent"]),
            )
            state = _read_json_object(state_path, "audio_reconcile_state_missing")
            validation = validate_audio_projection(
                state,
                issue_date=day,
                run_intent=str(identity["run_intent"]),
                expected_run_id=str(identity["run_id"]),
            )
        if validation.get("ok") is not True or str(state.get("publicUrl") or "") != url:
            raise ProductionAdapterError("audio_reconcile_identity_unconfirmed")
        payload = {
            "provider": f"github_release_audio_{kind}",
            "reconciled": True,
            "state_path": state_path,
            "public_url": url,
            "public_observation": public_observation,
            "input_sha256": expected_input,
            "release_commit_sha": release_sha,
        }
    elif operation_id.startswith("youtube_"):
        kind = "daily" if operation_id.startswith("youtube_daily_") else "deepdive"
        phase = "finalize" if operation_id.endswith("_finalize") else "prepare"
        payload_identity, operation_marker = _youtube_external_identity(
            identity,
            kind=kind,
            phase=phase,
        )
        from tools.youtube_podcast.upload_episode import YouTubeOperationError, finalize, reconcile

        try:
            if phase == "finalize":
                # finalizeはprivacy/playlist substepごとにfresh観測してから、
                # 未成立のstepだけを実行する。適用済みstepはprovider stateと
                # uploadHistoryV2 receiptにより再実行されない。
                result = _invoke_provider(
                    finalize,
                    day,
                    kind=kind,
                    run_id=str(identity["run_id"]),
                    bundle_id=str(identity["bundle_id"]),
                    operation_id=str(operation_id),
                    payload_identity=payload_identity,
                    operation_marker=operation_marker,
                )
            else:
                result = _invoke_provider(
                    reconcile,
                    day,
                    kind=kind,
                    phase=phase,
                    run_id=str(identity["run_id"]),
                    bundle_id=str(identity["bundle_id"]),
                    operation_id=str(operation_id),
                    payload_identity=payload_identity,
                    operation_marker=operation_marker,
                )
        except YouTubeOperationError as exc:
            raise ProductionAdapterError(str(exc)) from exc
        if not isinstance(result, Mapping):
            raise ProductionAdapterError(f"youtube_{kind}_reconcile_result_invalid")
        if str(result.get("mp4_sha256") or "").casefold() != payload_identity:
            raise ProductionAdapterError("youtube_reconcile_input_hash_mismatch")
        _validate_youtube_result_identity(
            result,
            identity=identity,
            kind=kind,
            phase=phase,
            payload_identity=payload_identity,
            operation_marker=operation_marker,
        )
        if (
            result.get("date") != day
            or not str(result.get("videoId") or "")
            or str(result.get("status") or "").casefold() != ("public" if phase == "finalize" else "private")
            or (
                phase == "finalize"
                and any(not str(result.get(field) or "") for field in ("playlistId", "playlistItemId"))
            )
            or (
                phase == "finalize"
                and kind == "deepdive"
                and any(
                    not str(result.get(field) or "")
                    for field in ("primaryPodcastPlaylistId", "primaryPodcastPlaylistItemId")
                )
            )
        ):
            raise ProductionAdapterError("youtube_reconcile_public_binding_unconfirmed")
        payload = {
            "provider": "youtube_podcast",
            "phase": phase,
            "kind": kind,
            "reconciled": True,
            "result": result,
            "input_sha256": payload_identity,
            "release_commit_sha": release_sha,
        }
    elif operation_id == "git_release_push":
        if _remote_target_sha(root) != release_sha:
            raise ProductionAdapterError("git_release_reconcile_remote_sha_mismatch")
        payload = {
            "provider": "git",
            "reconciled": True,
            "remote": "origin",
            "branch": "main",
            "release_commit_sha": release_sha,
            "observed_remote_sha": release_sha,
        }
    elif operation_id == "pages_deployment_wait":
        observed = _pages_workflow_once(identity, release_sha)
        binding = observed.get("deploymentBinding") if isinstance(observed.get("deploymentBinding"), Mapping) else {}
        if observed.get("ok") is not True or str(binding.get("deploymentSha") or "").casefold() != release_sha:
            raise ProductionAdapterError("pages_reconcile_deployment_unconfirmed")
        payload = {
            "provider": "github_pages",
            "reconciled": True,
            "release_commit_sha": release_sha,
            "workflow": dict(observed),
        }
    elif operation_id == "notification_send":
        # senderはrecipient-events ledgerをfresh読込し、sent/gone recipientを
        # skipして未開始recipientだけを送る。reservedのまま停止したrecipientは
        # unknown_deliveryとして再送せずfail-closedする。
        return _notification_send(
            context=context,
            operation_id=operation_id,
            side_effect_id=side_effect_id,
            idempotency_key=idempotency_key,
            manifest_id=manifest_id,
            bundle_id=bundle_id,
            run_id=run_id,
            fencing_token=fencing_token,
        )
    elif operation_id == "completion_attestation_publish":
        distribution_assets = _completion_distribution_assets(identity)
        raw = _completion_attestation_bytes(identity)
        url = _completion_attestation_url(identity)
        payload_identity = hashlib.sha256(raw).hexdigest()
        if not _public_bytes_match(url, raw):
            raise ProductionAdapterError("completion_attestation_reconcile_unconfirmed")
        for item in distribution_assets:
            if not _public_bytes_match(str(item["publicUrl"]), bytes(item["raw"])):
                raise ProductionAdapterError("completion_distribution_asset_reconcile_unconfirmed")
        payload = {
            "provider": "github_release_completion_attestation",
            "reconciled": True,
            "attestation_url": url,
            "payload_identity": payload_identity,
            "operation_count": len(_EXTERNAL_OPERATION_IDS) - 1,
            "distribution_artifact_count": len(distribution_assets),
            "release_commit_sha": release_sha,
        }
    else:
        raise ProductionAdapterError("external_reconciler_operation_unknown")
    return _receipt(identity, payload)


PRODUCTION_ADAPTERS: Mapping[str, Callable[..., Mapping[str, Any]]] = MappingProxyType(
    {
        "audio_daily_upload": audio_daily_upload,
        "audio_deepdive_upload": audio_deepdive_upload,
        "youtube_daily_prepare": youtube_daily_prepare,
        "youtube_deepdive_prepare": youtube_deepdive_prepare,
        "git_release_push": git_release_push,
        "pages_deployment_wait": pages_deployment_wait,
        "youtube_daily_finalize": youtube_daily_finalize,
        "youtube_deepdive_finalize": youtube_deepdive_finalize,
        "notification_send": notification_send,
        "completion_attestation_publish": completion_attestation_publish,
    }
)

PRODUCTION_RECONCILERS: Mapping[str, Callable[..., Mapping[str, Any]]] = MappingProxyType(
    {operation_id: reconcile_external_operation for operation_id in _EXTERNAL_OPERATION_IDS}
)


__all__ = [
    "EXTERNAL_ADAPTER_RECEIPT_SCHEMA",
    "PROTECTED_RELEASE",
    "REPO_ROOT",
    "ProductionAdapterError",
    "audio_daily_upload",
    "audio_deepdive_upload",
    "youtube_daily_prepare",
    "youtube_deepdive_prepare",
    "git_release_push",
    "pages_deployment_wait",
    "youtube_daily_finalize",
    "youtube_deepdive_finalize",
    "notification_send",
    "completion_attestation_publish",
    "reconcile_external_operation",
    "record_external_provider_binding",
    "PRODUCTION_ADAPTERS",
    "PRODUCTION_RECONCILERS",
]
