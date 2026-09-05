"""News-Grasp 日次公開用の外部 side-effect outbox 実行器。

この module は外部 provider の実装を持たない。production では固定された
``PRODUCTION_ADAPTERS`` だけを参照し、adapter が登録されていなければ、
副作用を予約する前に typed Red を返す。実際の provider adapter を差し替え
られるのは ``DirectRunStore(test_only_allow_semantic_verifier=True)`` の
fixture/test 境界だけである。

外部処理の authority は caller が渡す ``ok`` ではない。run の start/publish
seal、actual run ID、writer lease/fencing token、outbox の CAS 状態、adapter
receipt の全てを同じ release identity へ束縛する。adapter の戻りが不明な
場合は ``unknown_delivery`` とし、同じ run から再送しない。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any

from tools import news_grasp_direct_runtime as runtime
from tools.news_grasp_production_adapters import (
    PRODUCTION_ADAPTERS as _FIXED_PRODUCTION_ADAPTERS,
    PRODUCTION_RECONCILERS as _FIXED_PRODUCTION_RECONCILERS,
    record_external_provider_binding as _record_external_provider_binding,
)
from tools.news_grasp_trusted_process import (
    TrustedProcessError,
    sanitized_git_environment,
    trusted_git_executable,
)


EXTERNAL_PUBLICATION_RECEIPT_SCHEMA = "NEWS_GRASP_EXTERNAL_PUBLICATION_RECEIPT_V1"
EXTERNAL_ADAPTER_RECEIPT_SCHEMA = "NEWS_GRASP_EXTERNAL_ADAPTER_RECEIPT_V1"
EXTERNAL_RECOVERY_PROJECTION_SCHEMA = "NEWS_GRASP_EXTERNAL_RECOVERY_PROJECTION_V1"
PUBLISH_SEAL_SCHEMA = "NEWS_GRASP_PUBLISH_SEAL_V1"


# 順序は外部副作用の実行順序そのもの。並べ替え、alias、subset 実行を
# caller に許可しない。distribution は local sealed bundle であり、この
# external side-effect 列には含めない。
EXTERNAL_OPERATION_ORDER: tuple[str, ...] = (
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
EXTERNAL_OPERATION_IDS = EXTERNAL_OPERATION_ORDER
DAILY_EXTERNAL_OPERATION_ORDER = EXTERNAL_OPERATION_ORDER


@dataclass(frozen=True)
class ExternalOperationSpec:
    """一つの外部 side-effect の sealed 実行契約。"""

    operation_id: str
    side_effect_id: str
    requires_prior: tuple[str, ...]
    provider_idempotency_capability: bool
    ambiguous_recovery_policy: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "side_effect_id": self.side_effect_id,
            "requires_prior": list(self.requires_prior),
            "provider_idempotency_capability": self.provider_idempotency_capability,
            "ambiguous_recovery_policy": self.ambiguous_recovery_policy,
        }

    # 外部 fixture が mapping 形式で spec を読む場合にも、dataclass の
    # canonical attribute と同じ値だけを返す。別 schema や自由 field は持たせない。
    def __getitem__(self, key: str) -> Any:
        if key == "requires_prior":
            return self.requires_prior
        return getattr(self, key)


def _spec(
    operation_id: str,
    *,
    requires_prior: tuple[str, ...] = (),
    provider_idempotency_capability: bool,
    ambiguous_recovery_policy: str,
) -> ExternalOperationSpec:
    return ExternalOperationSpec(
        operation_id=operation_id,
        # operation と side-effect は一対一。surface alias を許すと、prepare と
        # finalize の idempotency key が衝突するため、alias は使わない。
        side_effect_id=operation_id,
        requires_prior=requires_prior,
        provider_idempotency_capability=provider_idempotency_capability,
        ambiguous_recovery_policy=ambiguous_recovery_policy,
    )


EXTERNAL_OPERATION_SPECS: dict[str, ExternalOperationSpec] = {
    "audio_daily_upload": _spec(
        "audio_daily_upload",
        provider_idempotency_capability=False,
        ambiguous_recovery_policy="reconcile_before_retry",
    ),
    "audio_deepdive_upload": _spec(
        "audio_deepdive_upload",
        requires_prior=("audio_daily_upload",),
        provider_idempotency_capability=False,
        ambiguous_recovery_policy="reconcile_before_retry",
    ),
    "youtube_daily_prepare": _spec(
        "youtube_daily_prepare",
        provider_idempotency_capability=False,
        ambiguous_recovery_policy="reconcile_before_retry",
    ),
    "youtube_deepdive_prepare": _spec(
        "youtube_deepdive_prepare",
        provider_idempotency_capability=False,
        ambiguous_recovery_policy="reconcile_before_retry",
    ),
    "git_release_push": _spec(
        "git_release_push",
        requires_prior=(
            "audio_daily_upload",
            "audio_deepdive_upload",
            "youtube_daily_prepare",
            "youtube_deepdive_prepare",
        ),
        provider_idempotency_capability=True,
        ambiguous_recovery_policy="cas_reconcile_before_retry",
    ),
    "pages_deployment_wait": _spec(
        "pages_deployment_wait",
        requires_prior=(
            "git_release_push", "audio_daily_upload", "audio_deepdive_upload",
            "youtube_daily_prepare", "youtube_deepdive_prepare",
        ),
        provider_idempotency_capability=True,
        ambiguous_recovery_policy="read_only_reconcile",
    ),
    "youtube_daily_finalize": _spec(
        "youtube_daily_finalize",
        requires_prior=("pages_deployment_wait", "youtube_daily_prepare"),
        provider_idempotency_capability=False,
        ambiguous_recovery_policy="reconcile_before_retry",
    ),
    "youtube_deepdive_finalize": _spec(
        "youtube_deepdive_finalize",
        requires_prior=("pages_deployment_wait", "youtube_deepdive_prepare"),
        provider_idempotency_capability=False,
        ambiguous_recovery_policy="reconcile_before_retry",
    ),
    "notification_send": _spec(
        "notification_send",
        requires_prior=("youtube_daily_finalize", "youtube_deepdive_finalize"),
        provider_idempotency_capability=False,
        ambiguous_recovery_policy="immutable_ledger_reconcile_before_retry",
    ),
    "completion_attestation_publish": _spec(
        "completion_attestation_publish",
        requires_prior=("notification_send",),
        provider_idempotency_capability=True,
        ambiguous_recovery_policy="read_only_reconcile",
    ),
}
EXTERNAL_OPERATION_SPEC_LIST: tuple[ExternalOperationSpec, ...] = tuple(
    EXTERNAL_OPERATION_SPECS[item] for item in EXTERNAL_OPERATION_ORDER
)
EXTERNAL_OPERATIONS = EXTERNAL_OPERATION_SPEC_LIST
EXTERNAL_SIDE_EFFECT_IDS: tuple[str, ...] = tuple(
    item.side_effect_id for item in EXTERNAL_OPERATION_SPEC_LIST
)


# provider は固定 production adapter module の9 operationだけを登録する。辞書を
# module外から変更できないようにし、caller injectionや未登録operationへの迂回を
# 受理しない。
PRODUCTION_ADAPTERS: Mapping[str, Callable[..., Mapping[str, Any]]] = MappingProxyType(
    dict(_FIXED_PRODUCTION_ADAPTERS)
)
PRODUCTION_RECONCILERS: Mapping[str, Callable[..., Mapping[str, Any]]] = MappingProxyType(
    dict(_FIXED_PRODUCTION_RECONCILERS)
)

_ALIAS_NAMES = frozenset({"final", "latest", "current"})
_SHA256_RE = re.compile(r"[0-9a-f]{64}", re.IGNORECASE)
_SHA1_RE = re.compile(r"[0-9a-f]{40}", re.IGNORECASE)
_MISSING = object()
_SAFE_PROVIDER_ACKS = frozenset(
    {
        "",
        "acknowledged",
        "delivered",
        "success",
        "sent",
        "unknown",
        "unknown_unobtainable",
        "unavailable",
    }
)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _red(reason: str, **extra: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schemaVersion": EXTERNAL_PUBLICATION_RECEIPT_SCHEMA,
        "ok": False,
        "status": "red",
        "failures": [reason],
        "duplicate_call_count": 0,
        "duplicateCallCount": 0,
        "humanImpact": {
            "noFocusTheft": True,
            "noAutoOpen": True,
            "noUserMonitoring": True,
        },
    }
    result.update(extra)
    return result


def _reconcile(
    reason: str,
    *,
    operation_id: str,
    operations: list[dict[str, Any]],
    run_id: str,
    manifest_id: str,
    bundle_id: str,
    fencing_token: int,
    adapter_call_count: int,
    provider_ack_unknown_ids: list[str],
) -> dict[str, Any]:
    # failure が途中で発生しても receipt の operation row 数は常に9。未実行
    # row は明示的に not_executed とし、caller が部分配列を全成功と誤読できない
    # projection にする。
    by_operation = {
        str(item.get("operation_id") or item.get("operationId") or ""): item
        for item in operations
        if isinstance(item, Mapping)
    }
    complete_rows: list[dict[str, Any]] = []
    for spec in EXTERNAL_OPERATION_SPEC_LIST:
        complete_rows.append(
            dict(
                by_operation.get(spec.operation_id)
                or _operation_row(
                    spec=spec,
                    row={"status": "not_executed"},
                    adapter_called=False,
                    idempotent=False,
                )
            )
        )
    return {
        "schemaVersion": EXTERNAL_PUBLICATION_RECEIPT_SCHEMA,
        "ok": False,
        "status": "reconcile_required",
        "failures": [reason],
        "exact_successor": f"external_reconcile:{operation_id}",
        "run_id": run_id,
        "runId": run_id,
        "manifest_id": manifest_id,
        "manifestId": manifest_id,
        "bundle_id": bundle_id,
        "bundleId": bundle_id,
        "operations": complete_rows,
        "operation_rows": complete_rows,
        "adapter_call_count": adapter_call_count,
        "adapterCallCount": adapter_call_count,
        "duplicate_call_count": 0,
        "duplicateCallCount": 0,
        "provider_ack_unknown_ids": provider_ack_unknown_ids,
        "providerAckUnknownIds": provider_ack_unknown_ids,
        "humanImpact": {
            "noFocusTheft": True,
            "noAutoOpen": True,
            "noUserMonitoring": True,
        },
    }


def _first(mapping: Mapping[str, Any], *keys: str, default: Any = _MISSING) -> Any:
    """camel/snake の読み取りを一箇所に限定する。"""

    present: list[Any] = [mapping[key] for key in keys if key in mapping]
    if not present:
        return default
    first = present[0]
    for value in present[1:]:
        if value != first:
            raise ValueError("external_identity_alias_conflict")
    return first


def _mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _normalise_hash(value: Any, *, length: int) -> str | None:
    text = str(value or "").strip().casefold()
    regex = _SHA256_RE if length == 64 else _SHA1_RE
    return text if regex.fullmatch(text) else None


def _freeze(value: Any) -> Any:
    """adapter へ渡す context の再帰 immutable copy。"""

    if isinstance(value, Mapping):
        frozen = {
            key: _freeze(item)
            for key, item in value.items()
        }
        return MappingProxyType(frozen)
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze(item) for item in value)
    return value


def _outbox_rows(store: Any, run_id: str) -> dict[str, dict[str, Any]]:
    """outbox の監査に必要な field だけを read-only で取得する。"""

    with store.connect() as conn:
        rows = conn.execute(
            """
            SELECT logical_operation_id,run_id,side_effect_id,status,payload_json,
                   idempotency_key,fencing_token,provider_ack_status,output_hash,
                   started_at,completed_at,provider_receipt_json,
                   provider_receipt_hash,provider_output_hash
            FROM external_outbox
            WHERE run_id=?
            ORDER BY logical_operation_id
            """,
            (run_id,),
        ).fetchall()
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        values = {
            "operation_id": str(row[0]),
            "run_id": str(row[1]),
            "side_effect_id": str(row[2]),
            "status": str(row[3]),
            "payload_json": str(row[4]),
            "idempotency_key": str(row[5]),
            "fencing_token": int(row[6] or 0),
            "provider_ack_status": str(row[7] or ""),
            "output_hash": str(row[8] or ""),
            "started_at": str(row[9] or ""),
            "completed_at": str(row[10] or ""),
            "provider_receipt_json": str(row[11] or "{}"),
            "provider_receipt_hash": str(row[12] or ""),
            "provider_output_hash": str(row[13] or ""),
        }
        # logical operation IDはrun scope。別日の固定9 operationを混入しない。
        result[values["operation_id"]] = values
    return result


def _run_row_identity(store: Any, run_id: str) -> dict[str, Any]:
    with store.connect() as conn:
        row = conn.execute(
            """
            SELECT run_id,writer_lease,fencing_token,status,manifest_id,
                   publish_seal_json,external_started_at,issue_date,run_intent
            FROM runs WHERE run_id=?
            """,
            (run_id,),
        ).fetchone()
    if row is None:
        raise ValueError("run_not_found")
    return {
        "run_id": str(row[0]),
        "writer_lease": str(row[1]),
        "fencing_token": int(row[2] or 0),
        "status": str(row[3]),
        "manifest_id": str(row[4] or "").casefold(),
        "publish_seal_json": str(row[5] or "{}"),
        "external_started_at": str(row[6] or ""),
        "issue_date": str(row[7] or ""),
        "run_intent": str(row[8] or ""),
    }


def _sealed_identity(
    store: Any,
    *,
    run_id: Any,
    writer_lease: Any,
    fencing_token: Any,
    context: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str | None]:
    """全 operation reserve より前に identity を一括検証する。"""

    candidate_run_id = str(run_id or "").strip()
    if not candidate_run_id:
        return None, None, "actual_run_id_required"
    if candidate_run_id.casefold() in _ALIAS_NAMES:
        return None, None, "run_id_alias_forbidden"
    candidate_lease = str(writer_lease or "").strip()
    if not candidate_lease:
        return None, None, "writer_lease_required"
    if isinstance(fencing_token, bool):
        return None, None, "fencing_token_invalid"
    try:
        candidate_fence = int(fencing_token)
    except (TypeError, ValueError):
        return None, None, "fencing_token_invalid"
    if candidate_fence <= 0:
        return None, None, "fencing_token_invalid"

    try:
        state = runtime.inspect_run(store, run_id=candidate_run_id)
        row = _run_row_identity(store, candidate_run_id)
    except Exception as exc:  # noqa: BLE001 - typed preflight Red
        return None, None, f"external_identity_inspection_failed:{type(exc).__name__}"

    stored_issue_date = str(row.get("issue_date") or "").strip()
    stored_run_intent = str(row.get("run_intent") or "").strip()
    try:
        supplied_issue_date = _first(context, "issue_date", "issueDate", default=_MISSING)
        supplied_run_intent = _first(context, "run_intent", "runIntent", default=_MISSING)
    except (TypeError, ValueError):
        return None, None, "external_context_identity_alias_conflict"
    if stored_issue_date == "2026-09-02":
        return None, None, "protected_release_reexecution_forbidden"
    if (
        supplied_issue_date is _MISSING
        or str(supplied_issue_date).strip() != stored_issue_date
    ):
        return None, None, "external_context_issue_date_mismatch"
    if (
        supplied_run_intent is _MISSING
        or str(supplied_run_intent).strip() != stored_run_intent
    ):
        return None, None, "external_context_run_intent_mismatch"
    if (
        str(state.get("issue_date") or "").strip() != stored_issue_date
        or str(state.get("run_intent") or "").strip() != stored_run_intent
    ):
        return None, None, "external_runtime_identity_projection_mismatch"
    if state.get("run_id") != candidate_run_id or row["run_id"] != candidate_run_id:
        return None, None, "actual_run_id_mismatch"
    if row["writer_lease"] != candidate_lease:
        return None, None, "writer_lease_mismatch"
    if row["fencing_token"] != candidate_fence:
        return None, None, "fencing_token_fenced"
    if row["status"] not in {"active", "executing"}:
        return None, None, "external_run_not_active"
    try:
        seal = json.loads(row["publish_seal_json"])
    except (TypeError, json.JSONDecodeError):
        return None, None, "publish_seal_invalid_json"
    if not isinstance(seal, Mapping):
        return None, None, "publish_seal_invalid"
    try:
        seal_schema = _first(seal, "schemaVersion", "schema_version", default="")
        manifest_id_value = _first(seal, "manifestId", "manifest_id", default="")
        bundle_id_value = _first(seal, "bundleId", "bundle_id", default="")
        external_ids_value = _first(
            seal,
            "externalOperationIds",
            "external_operation_ids",
            default=_MISSING,
        )
        sealed_run_id = _first(seal, "runId", "run_id", default="")
        sealed_fence = _first(seal, "fencingToken", "fencing_token", default=_MISSING)
        release_commit_sha = _first(seal, "releaseCommitSha", "release_commit_sha", default="")
        exact_write_set = _first(seal, "exactWriteSet", "exact_write_set", default=_MISSING)
        file_hashes = _first(seal, "fileHashes", "file_hashes", default=_MISSING)
        external_input_hashes = _first(
            seal,
            "externalInputHashes",
            "external_input_hashes",
            default=_MISSING,
        )
    except (TypeError, ValueError):
        return None, None, "publish_seal_identity_alias_conflict"
    if seal_schema != PUBLISH_SEAL_SCHEMA:
        return None, None, "publish_seal_schema_invalid"
    manifest_id = _normalise_hash(manifest_id_value, length=64)
    if manifest_id is None:
        return None, None, "publish_seal_manifest_id_invalid"
    bundle_id = str(bundle_id_value or "").strip()
    if not bundle_id:
        return None, None, "publish_seal_bundle_id_invalid"
    if sealed_run_id not in {"", candidate_run_id}:
        return None, None, "publish_seal_run_id_mismatch"
    if (
        sealed_fence is _MISSING
        or isinstance(sealed_fence, bool)
        or not isinstance(sealed_fence, int)
        or not 0 < sealed_fence <= candidate_fence
    ):
        return None, None, "publish_seal_fencing_token_invalid"
    if row["manifest_id"] != manifest_id:
        return None, None, "run_manifest_id_mismatch"
    if not isinstance(external_ids_value, (list, tuple)) or isinstance(
        external_ids_value, (str, bytes, bytearray)
    ):
        return None, None, "publish_seal_external_operation_ids_invalid"
    external_ids = [str(item) for item in external_ids_value]
    if external_ids != list(EXTERNAL_OPERATION_ORDER):
        return None, None, "publish_seal_external_operation_ids_mismatch"
    if _normalise_hash(release_commit_sha, length=40) is None:
        return None, None, "publish_seal_release_commit_sha_invalid"
    if (
        not isinstance(exact_write_set, (list, tuple))
        or isinstance(exact_write_set, (str, bytes, bytearray))
        or not exact_write_set
        or not isinstance(file_hashes, Mapping)
        or set(str(item) for item in exact_write_set) != set(str(key) for key in file_hashes)
        or any(_normalise_hash(value, length=64) is None for value in file_hashes.values())
    ):
        return None, None, "publish_seal_file_binding_invalid"
    expected_external_inputs = {
        f"build/tts/{stored_issue_date}.mp3",
        f"build/tts/deepdive/{stored_issue_date}.mp3",
        f"build/youtube-podcast/{stored_issue_date}.mp4",
        f"build/youtube-podcast-deepdive/{stored_issue_date}.mp4",
    }
    if bool(getattr(store, "test_only_allow_semantic_verifier", False)) and external_input_hashes is _MISSING:
        external_input_hashes = {}
    elif (
        not isinstance(external_input_hashes, Mapping)
        or set(str(key) for key in external_input_hashes) != expected_external_inputs
        or any(_normalise_hash(value, length=64) is None for value in external_input_hashes.values())
    ):
        return None, None, "publish_seal_external_input_binding_invalid"

    expected_payload_identities: dict[str, str] = {}
    if not bool(getattr(store, "test_only_allow_semantic_verifier", False)):
        try:
            root = Path(
                os.path.abspath(
                    os.fspath(_first(context, "repo_root", "repoRoot", default=""))
                )
            ).resolve(strict=True)
            from tools.news_grasp_publish_contract import load_manifest, verify_manifest

            manifest = load_manifest(root, stored_issue_date)
            manifest_check = verify_manifest(manifest, repo_root=root, require_files=True)
            operation_contract = manifest.get("externalOperationContract")
        except (OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError):
            return None, None, "external_manifest_contract_unobservable"
        if (
            manifest_check.get("ok") is not True
            or manifest.get("manifestId") != manifest_id
            or manifest.get("runId") != candidate_run_id
            or manifest.get("runIntent") != stored_run_intent
            or not isinstance(operation_contract, Mapping)
            or operation_contract.get("operationIds") != list(EXTERNAL_OPERATION_ORDER)
            or operation_contract.get("externalInputHashes")
            != {str(key): str(value).casefold() for key, value in external_input_hashes.items()}
        ):
            return None, None, "external_manifest_contract_binding_invalid"
        operation_rows = operation_contract.get("operations")
        if not isinstance(operation_rows, list) or len(operation_rows) != len(EXTERNAL_OPERATION_ORDER):
            return None, None, "external_manifest_operation_contract_invalid"
        for operation_id, operation_row in zip(EXTERNAL_OPERATION_ORDER, operation_rows):
            if (
                not isinstance(operation_row, Mapping)
                or operation_row.get("operationId") != operation_id
                or operation_row.get("sideEffectId") != operation_id
            ):
                return None, None, "external_manifest_operation_contract_invalid"
            authority = str(operation_row.get("payloadIdentityAuthority") or "")
            payload_identity = str(operation_row.get("payloadIdentity") or "").casefold()
            if authority in {
                "sealed_external_input_sha256",
                "canonical_notification_payload_sha256",
            }:
                if _normalise_hash(payload_identity, length=64) is None:
                    return None, None, "external_manifest_payload_identity_invalid"
                expected_payload_identities[operation_id] = payload_identity
            elif authority == "sealed_release_commit_sha256":
                expected_payload_identities[operation_id] = hashlib.sha256(
                    f"{operation_id}\0{str(release_commit_sha).casefold()}".encode("utf-8")
                ).hexdigest()
            elif authority != "prior_provider_binding_receipt_set":
                return None, None, "external_manifest_payload_authority_invalid"

    # Context は identity を再定義できない。camel/snake 両方が渡された場合も
    # _first で衝突を検出し、manifest/bundle/run/fence drift を fail-closed にする。
    checks = (
        (("run_id", "runId"), candidate_run_id, "external_context_run_id_mismatch"),
        (("manifest_id", "manifestId"), manifest_id, "external_context_manifest_id_mismatch"),
        (("bundle_id", "bundleId"), bundle_id, "external_context_bundle_id_mismatch"),
        (("fencing_token", "fencingToken"), candidate_fence, "external_context_fencing_token_mismatch"),
    )
    for keys, expected, reason in checks:
        try:
            supplied = _first(context, *keys, default=_MISSING)
        except (TypeError, ValueError):
            return None, None, "external_context_identity_alias_conflict"
        if supplied is not _MISSING:
            if keys[0] == "fencing_token":
                try:
                    equal = int(supplied) == int(expected)
                except (TypeError, ValueError):
                    equal = False
            else:
                equal = str(supplied).strip() == str(expected)
            if not equal:
                return None, None, reason

    identity = {
        "run_id": candidate_run_id,
        "issue_date": stored_issue_date,
        "run_intent": stored_run_intent,
        "manifest_id": manifest_id,
        "bundle_id": bundle_id,
        "fencing_token": candidate_fence,
        "operation_ids": tuple(EXTERNAL_OPERATION_ORDER),
        "release_commit_sha": str(release_commit_sha).casefold(),
        "exact_write_set": tuple(str(item) for item in exact_write_set),
        "file_hashes": {str(key): str(value).casefold() for key, value in file_hashes.items()},
        "external_input_hashes": {
            str(key): str(value).casefold()
            for key, value in external_input_hashes.items()
        },
        "expected_payload_identities": expected_payload_identities,
    }
    return identity, state, None


def _verify_sealed_release_files(
    identity: Mapping[str, Any],
    context: Mapping[str, Any],
    *,
    operation_id: str | None = None,
) -> str | None:
    """seal済みbytesを検査する。

    admission/reconcileでは全外部入力を一回確認する。個別provider call直前は
    そのoperationが消費する大容量mediaだけを再hashし、TOCTOU防止を保ったまま
    同じ4ファイルを10回読む無駄を除く。
    """

    try:
        root = Path(os.path.abspath(os.fspath(_first(context, "repo_root", "repoRoot", default="")))).resolve(strict=True)
    except (OSError, TypeError, ValueError):
        return "external_repo_root_invalid"
    if not root.is_dir() or root.is_symlink():
        return "external_repo_root_invalid"
    release_sha = str(identity.get("release_commit_sha") or "")
    try:
        git = str(trusted_git_executable())
        git_env = sanitized_git_environment()
        head = subprocess.run(
            [git, "rev-parse", "HEAD"], cwd=str(root), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="strict",
            timeout=15, check=False, shell=False, env=git_env,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        )
    except (OSError, UnicodeError, TrustedProcessError, subprocess.SubprocessError):
        return "external_release_commit_unobservable"
    if head.returncode != 0 or head.stdout.strip().casefold() != release_sha:
        return "external_release_commit_drift"
    try:
        status = subprocess.run(
            [
                git, "-c", "core.quotepath=false", "status",
                "--porcelain=v1", "-z", "--untracked-files=all",
            ],
            cwd=str(root), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=30, check=False, shell=False,
            env=git_env,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        )
    except (OSError, subprocess.SubprocessError):
        return "external_source_worktree_unobservable"
    if status.returncode != 0:
        return "external_source_worktree_unobservable"
    if status.stdout:
        return "superseded_after_external_start:external_source_worktree_drift"
    for relative in identity.get("exact_write_set") or ():
        path = root / str(relative)
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(root)
            worktree_hash = hashlib.sha256(resolved.read_bytes()).hexdigest()
        except (OSError, ValueError):
            return f"external_release_file_missing:{relative}"
        expected = str((identity.get("file_hashes") or {}).get(str(relative)) or "").casefold()
        if worktree_hash != expected:
            return f"external_release_worktree_drift:{relative}"
        committed = subprocess.run(
            [git, "show", f"{release_sha}:{relative}"], cwd=str(root), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=False, shell=False,
            env=git_env,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        )
        if committed.returncode != 0 or hashlib.sha256(committed.stdout).hexdigest() != expected:
            return f"external_release_commit_file_drift:{relative}"
    operation_input = {
        "audio_daily_upload": f"build/tts/{identity.get('issue_date')}.mp3",
        "audio_deepdive_upload": f"build/tts/deepdive/{identity.get('issue_date')}.mp3",
        "youtube_daily_prepare": f"build/youtube-podcast/{identity.get('issue_date')}.mp4",
        "youtube_deepdive_prepare": f"build/youtube-podcast-deepdive/{identity.get('issue_date')}.mp4",
        "youtube_daily_finalize": f"build/youtube-podcast/{identity.get('issue_date')}.mp4",
        "youtube_deepdive_finalize": f"build/youtube-podcast-deepdive/{identity.get('issue_date')}.mp4",
    }.get(str(operation_id or ""))
    for relative, expected in (identity.get("external_input_hashes") or {}).items():
        if operation_id is not None and relative != operation_input:
            continue
        candidate = root / str(relative)
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
            if resolved.is_symlink() or not resolved.is_file():
                return f"external_input_file_invalid:{relative}"
            observed = hashlib.sha256(resolved.read_bytes()).hexdigest()
        except (OSError, ValueError):
            return f"external_input_file_missing:{relative}"
        if observed != str(expected):
            return f"external_input_file_drift:{relative}"
    return None


def _verify_remote_cas_before_side_effects(
    identity: Mapping[str, Any],
    context: Mapping[str, Any],
    rows: Mapping[str, Mapping[str, Any]],
) -> str | None:
    """最初のprivate prepareより前にremote baseとmain branchを固定する。"""

    try:
        root = Path(os.path.abspath(os.fspath(_first(context, "repo_root", "repoRoot", default="")))).resolve(strict=True)
        git = str(trusted_git_executable())
        git_env = sanitized_git_environment()
        branch = subprocess.run(
            [git, "symbolic-ref", "-q", "HEAD"], cwd=str(root), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="strict",
            timeout=15, check=False, shell=False, env=git_env,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        )
        if branch.returncode == 0 and branch.stdout.strip() != "refs/heads/main":
            return "external_main_branch_required"
        head = subprocess.run(
            [git, "rev-parse", "--verify", "HEAD"], cwd=str(root), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="strict",
            timeout=15, check=False, shell=False, env=git_env,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        )
        release_sha = str(identity.get("release_commit_sha") or "")
        parent = subprocess.run(
            [git, "rev-parse", f"{release_sha}^"], cwd=str(root), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="strict",
            timeout=15, check=False, shell=False, env=git_env,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        )
        remote = subprocess.run(
            [git, "ls-remote", "--exit-code", "--end-of-options", "origin", "refs/heads/main"],
            cwd=str(root), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="strict", timeout=30, check=False, shell=False,
            env=git_env,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        )
    except (
        OSError,
        UnicodeError,
        ValueError,
        TrustedProcessError,
        subprocess.SubprocessError,
    ):
        return "external_remote_cas_unobservable"
    start_seal = _mapping(_first(context, "start_seal", "startSeal", default={})) or {}
    remote_base = str(_first(start_seal, "remoteBaseSha", "remote_base_sha", default="") or "").casefold()
    if (
        head.returncode != 0
        or head.stdout.strip().casefold() != release_sha
        or parent.returncode != 0
        or parent.stdout.strip().casefold() != remote_base
    ):
        return "external_release_parent_remote_base_mismatch"
    observed_remote = remote.stdout.split()[0].casefold() if remote.returncode == 0 and remote.stdout.split() else ""
    git_status = str((rows.get("git_release_push") or {}).get("status") or "")
    expected_remote = release_sha if git_status == "completed" else remote_base
    if observed_remote != expected_remote:
        return "external_remote_base_cas_mismatch"
    return None


def _verify_pre_notification_public_readiness(
    identity: Mapping[str, Any],
    context: Mapping[str, Any],
) -> str | None:
    """通知直前にPages SHAと公開HTML意味内容をconsumer実装でfresh確認する。"""

    try:
        from tools.news_grasp_direct_completion import (
            _pages_workflow_observation,
            _public_web,
        )
        from tools.news_grasp_publish_contract import load_manifest, verify_manifest

        root = Path(
            os.path.abspath(os.fspath(_first(context, "repo_root", "repoRoot", default="")))
        ).resolve(strict=True)
        issue_date = str(identity.get("issue_date") or "")
        release_sha = str(identity.get("release_commit_sha") or "")
        manifest = load_manifest(root, issue_date)
        manifest_check = verify_manifest(manifest, repo_root=root, require_files=True)
        if manifest_check.get("ok") is not True:
            return "notification_preflight_manifest_red"
        workflow = _pages_workflow_observation(
            remote_head=release_sha,
            manifest_id=str(identity.get("manifest_id") or ""),
            issue_date=issue_date,
            release_kind="public",
            changed_paths=[str(item) for item in identity.get("exact_write_set") or ()],
        )
        binding = workflow.get("deploymentBinding") if isinstance(workflow, Mapping) else {}
        if (
            not isinstance(binding, Mapping)
            or workflow.get("ok") is not True
            or str(binding.get("deploymentSha") or "").casefold() != release_sha
        ):
            return "notification_preflight_pages_sha_red"
        public = _public_web(
            root,
            issue_date,
            public_base_url="https://hidepon-umg.github.io/News-Grasp/",
            remote="origin",
            branch="main",
            wait_sec=0,
            poll_sec=30,
            manifest=manifest,
            cache_bust=True,
        )
        if public.get("ok") is not True or public.get("semantic_ok") is not True:
            return "notification_preflight_public_semantic_red"
    except Exception as exc:  # noqa: BLE001 - external send must fail closed.
        return f"notification_preflight_unobservable:{type(exc).__name__}"
    return None


def validate_external_adapter_receipt(
    receipt: Any,
    *,
    operation_id: str,
    side_effect_id: str,
    idempotency_key: str,
    identity: Mapping[str, Any] | None = None,
    require_provider_evidence: bool = False,
) -> dict[str, Any]:
    """provider の結果を canonical typed receipt へ検証する。

    ``{"ok": true}`` のような caller 側 boolean は、schema、identity、output
    hash が揃わない限り受理しない。戻り値には adapter payload を含めないため、
    path/secret/raw response が永続 receipt へ漏れない。
    """

    if not isinstance(receipt, Mapping):
        return {
            "schemaVersion": EXTERNAL_ADAPTER_RECEIPT_SCHEMA,
            "ok": False,
            "status": "invalid",
            "failures": ["adapter_receipt_mapping_required"],
        }
    try:
        schema = _first(receipt, "schemaVersion", "schema_version", default="")
        ok = _first(receipt, "ok", default=_MISSING)
        status = str(_first(receipt, "status", default="") or "").strip().casefold()
        returned_operation = _first(receipt, "operationId", "operation_id", default=_MISSING)
        returned_side_effect = _first(receipt, "sideEffectId", "side_effect_id", default=_MISSING)
        returned_key = _first(receipt, "idempotencyKey", "idempotency_key", default=_MISSING)
        output_hash_value = _first(receipt, "outputHash", "output_hash", default=_MISSING)
        provider_ack = _first(
            receipt,
            "providerAckStatus",
            "provider_ack_status",
            default="",
        )
        payload_identity_value = _first(
            receipt,
            "payloadIdentity",
            "payload_identity",
            default=_MISSING,
        )
        provider_evidence = _first(receipt, "payload", "providerEvidence", "provider_evidence", default=_MISSING)
    except (TypeError, ValueError):
        return {
            "schemaVersion": EXTERNAL_ADAPTER_RECEIPT_SCHEMA,
            "ok": False,
            "status": "invalid",
            "failures": ["adapter_receipt_identity_alias_conflict"],
        }
    if schema != EXTERNAL_ADAPTER_RECEIPT_SCHEMA:
        reason = "adapter_receipt_schema_invalid"
    elif ok is not True:
        reason = "adapter_receipt_ok_true_required"
    elif status not in {"sent", "completed"}:
        reason = "adapter_receipt_status_invalid"
    elif returned_operation is _MISSING or str(returned_operation) != operation_id:
        reason = "adapter_receipt_operation_id_mismatch"
    elif returned_side_effect is _MISSING or str(returned_side_effect) != side_effect_id:
        reason = "adapter_receipt_side_effect_id_mismatch"
    elif returned_key is _MISSING or str(returned_key) != idempotency_key:
        reason = "adapter_receipt_idempotency_key_mismatch"
    elif output_hash_value is _MISSING or _normalise_hash(output_hash_value, length=64) is None:
        reason = "adapter_receipt_output_hash_required"
    elif require_provider_evidence and (
        payload_identity_value is _MISSING
        or _normalise_hash(payload_identity_value, length=64) is None
    ):
        reason = "adapter_receipt_payload_identity_required"
    elif str(provider_ack or "").strip().casefold() not in _SAFE_PROVIDER_ACKS:
        reason = "adapter_receipt_provider_ack_status_invalid"
    elif require_provider_evidence and not isinstance(provider_evidence, Mapping):
        reason = "adapter_receipt_provider_evidence_missing"
    else:
        safe_evidence: dict[str, Any] = {}
        if isinstance(provider_evidence, Mapping):
            try:
                evidence_json = _canonical_json(dict(provider_evidence))
            except (TypeError, ValueError, OverflowError):
                reason = "adapter_receipt_provider_evidence_invalid"
            else:
                forbidden_keys = {
                    str(key)
                    for key in provider_evidence
                    if any(token in str(key).casefold() for token in ("secret", "token", "password", "private_key"))
                }
                if len(evidence_json.encode("utf-8")) > 131_072 or forbidden_keys:
                    reason = "adapter_receipt_provider_evidence_invalid"
                elif hashlib.sha256(evidence_json.encode("utf-8")).hexdigest() != str(
                    _normalise_hash(output_hash_value, length=64) or ""
                ):
                    reason = "adapter_receipt_output_hash_mismatch"
                else:
                    safe_evidence = dict(provider_evidence)
                    reason = ""
        else:
            reason = ""
        if reason:
            return {
                "schemaVersion": EXTERNAL_ADAPTER_RECEIPT_SCHEMA,
                "ok": False,
                "status": "invalid",
                "failures": [reason],
            }
        identity_value = dict(identity or {})
        if require_provider_evidence:
            required_identity = {
                "run_id": _first(receipt, "runId", "run_id", default=""),
                "manifest_id": _first(receipt, "manifestId", "manifest_id", default=""),
                "bundle_id": _first(receipt, "bundleId", "bundle_id", default=""),
                "fencing_token": _first(receipt, "fencingToken", "fencing_token", default=0),
            }
            if any(str(required_identity[key]) != str(identity_value.get(key) or "") for key in required_identity):
                return {
                    "schemaVersion": EXTERNAL_ADAPTER_RECEIPT_SCHEMA,
                    "ok": False,
                    "status": "invalid",
                    "failures": ["adapter_receipt_release_identity_mismatch"],
                }
        provider_ack_status = str(provider_ack or "").strip().casefold()
        if provider_ack_status in {"", "unknown", "unavailable"}:
            provider_ack_status = "unknown_unobtainable"
        return {
            "schemaVersion": EXTERNAL_ADAPTER_RECEIPT_SCHEMA,
            "ok": True,
            "status": status,
            "operation_id": operation_id,
            "side_effect_id": side_effect_id,
            "idempotency_key": idempotency_key,
            "output_hash": str(_normalise_hash(output_hash_value, length=64)),
            "provider_ack_status": provider_ack_status,
            "payload_identity": str(
                _normalise_hash(payload_identity_value, length=64)
                or _normalise_hash(output_hash_value, length=64)
                or ""
            ),
            "run_id": str(identity_value.get("run_id") or ""),
            "manifest_id": str(identity_value.get("manifest_id") or ""),
            "bundle_id": str(identity_value.get("bundle_id") or ""),
            "fencing_token": int(identity_value.get("fencing_token") or 0),
            "release_commit_sha": str(identity_value.get("release_commit_sha") or ""),
            "provider_evidence": safe_evidence,
        }
    return {
        "schemaVersion": EXTERNAL_ADAPTER_RECEIPT_SCHEMA,
        "ok": False,
        "status": "invalid",
        "failures": [reason],
    }


def _completed_outbox_row_failure(
    row: Mapping[str, Any],
    *,
    spec: ExternalOperationSpec,
    identity: Mapping[str, Any],
    strict_provider: bool,
) -> str | None:
    """completed rowをstatusではなくreceipt bytesとprovider postconditionで検査する。"""

    if str(row.get("status") or "") != "completed":
        return f"external_outbox_not_completed:{spec.operation_id}"
    try:
        receipt = json.loads(str(row.get("provider_receipt_json") or "{}"))
    except json.JSONDecodeError:
        return f"external_outbox_receipt_json_invalid:{spec.operation_id}"
    if not isinstance(receipt, Mapping):
        return f"external_outbox_receipt_json_invalid:{spec.operation_id}"
    receipt_json = _canonical_json(dict(receipt))
    receipt_hash = hashlib.sha256(receipt_json.encode("utf-8")).hexdigest()
    evidence = receipt.get("provider_evidence")
    output_hash = str(receipt.get("output_hash") or "").casefold()
    if (
        receipt_hash != str(row.get("provider_receipt_hash") or "").casefold()
        or receipt.get("schemaVersion") != EXTERNAL_ADAPTER_RECEIPT_SCHEMA
        or receipt.get("ok") is not True
        or receipt.get("status") not in {"sent", "completed"}
        or receipt.get("operation_id") != spec.operation_id
        or receipt.get("side_effect_id") != spec.side_effect_id
        or receipt.get("idempotency_key") != row.get("idempotency_key")
        or receipt.get("run_id") != identity.get("run_id")
        or receipt.get("manifest_id") != identity.get("manifest_id")
        or receipt.get("bundle_id") != identity.get("bundle_id")
        or int(receipt.get("fencing_token") or 0) != int(identity.get("fencing_token") or 0)
        or receipt.get("release_commit_sha") != identity.get("release_commit_sha")
        or output_hash != str(row.get("provider_output_hash") or "").casefold()
        or _normalise_hash(output_hash, length=64) is None
    ):
        return f"external_outbox_receipt_binding_invalid:{spec.operation_id}"
    if not strict_provider:
        return None
    if not isinstance(evidence, Mapping):
        return f"external_outbox_provider_evidence_missing:{spec.operation_id}"
    if hashlib.sha256(_canonical_json(dict(evidence)).encode("utf-8")).hexdigest() != output_hash:
        return f"external_outbox_provider_output_hash_mismatch:{spec.operation_id}"
    expected_payload = str(
        (identity.get("expected_payload_identities") or {}).get(spec.operation_id) or ""
    ).casefold()
    observed_payload = str(
        receipt.get("payload_identity") or receipt.get("payloadIdentity") or ""
    ).casefold()
    if expected_payload and observed_payload != expected_payload:
        return f"external_outbox_manifest_payload_identity_mismatch:{spec.operation_id}"
    release_sha = str(identity.get("release_commit_sha") or "")
    external_inputs = identity.get("external_input_hashes") if isinstance(identity.get("external_input_hashes"), Mapping) else {}
    if spec.operation_id in {"audio_daily_upload", "audio_deepdive_upload"}:
        relative = (
            f"build/tts/{identity.get('issue_date')}.mp3"
            if spec.operation_id == "audio_daily_upload"
            else f"build/tts/deepdive/{identity.get('issue_date')}.mp3"
        )
        if evidence.get("input_sha256") != external_inputs.get(relative) or evidence.get("release_commit_sha") != release_sha:
            return f"external_outbox_audio_postcondition_invalid:{spec.operation_id}"
    elif spec.operation_id.startswith("youtube_"):
        result = evidence.get("result") if isinstance(evidence.get("result"), Mapping) else {}
        kind = "daily" if spec.operation_id.startswith("youtube_daily_") else "deepdive"
        relative = (
            f"build/youtube-podcast/{identity.get('issue_date')}.mp4"
            if kind == "daily"
            else f"build/youtube-podcast-deepdive/{identity.get('issue_date')}.mp4"
        )
        if (
            evidence.get("input_sha256") != external_inputs.get(relative)
            or evidence.get("release_commit_sha") != release_sha
            or not str(result.get("videoId") or "")
        ):
            return f"external_outbox_youtube_postcondition_invalid:{spec.operation_id}"
        if spec.operation_id.endswith("_finalize") and (
            str(result.get("status") or "").casefold() != "public"
            or any(not str(result.get(field) or "") for field in ("playlistId", "playlistItemId"))
        ):
            return f"external_outbox_youtube_finalize_postcondition_invalid:{spec.operation_id}"
    elif spec.operation_id == "git_release_push":
        if evidence.get("observed_remote_sha") != release_sha or evidence.get("release_commit_sha") != release_sha:
            return "external_outbox_git_postcondition_invalid"
    elif spec.operation_id == "pages_deployment_wait":
        workflow = evidence.get("workflow") if isinstance(evidence.get("workflow"), Mapping) else {}
        binding = workflow.get("deploymentBinding") if isinstance(workflow.get("deploymentBinding"), Mapping) else {}
        if workflow.get("ok") is not True or str(binding.get("deploymentSha") or "").casefold() != release_sha:
            return "external_outbox_pages_postcondition_invalid"
    elif spec.operation_id == "notification_send":
        sent = evidence.get("sent_count")
        subscriptions = evidence.get("subscription_count")
        if (
            isinstance(sent, bool)
            or not isinstance(sent, int)
            or isinstance(subscriptions, bool)
            or not isinstance(subscriptions, int)
            or sent <= 0
            or sent != subscriptions
            or _normalise_hash(evidence.get("payload_identity"), length=64) is None
        ):
            return "external_outbox_notification_postcondition_invalid"
    elif spec.operation_id == "completion_attestation_publish":
        attestation_url = str(evidence.get("attestation_url") or "")
        payload_identity = str(evidence.get("payload_identity") or "").casefold()
        expected_suffix = (
            f"/HIDEPON-UMG/News-Grasp/releases/download/audio-daily/"
            f"{identity.get('issue_date')}-{identity.get('run_id')}-completion.json"
        )
        if (
            not attestation_url.startswith("https://github.com/")
            or not attestation_url.endswith(expected_suffix)
            or _normalise_hash(payload_identity, length=64) is None
            or payload_identity
            != str(receipt.get("payload_identity") or receipt.get("payloadIdentity") or "").casefold()
            or evidence.get("operation_count") != len(EXTERNAL_OPERATION_ORDER) - 1
            or evidence.get("distribution_artifact_count") != 8
            or evidence.get("release_commit_sha") != release_sha
        ):
            return "external_outbox_completion_attestation_postcondition_invalid"
    return None


def _operation_row(
    *,
    spec: ExternalOperationSpec,
    row: Mapping[str, Any] | None,
    adapter_called: bool,
    idempotent: bool,
    provider_ack_status: str = "",
) -> dict[str, Any]:
    status = str((row or {}).get("status") or "missing")
    ack = provider_ack_status or str((row or {}).get("provider_ack_status") or "")
    return {
        "operation_id": spec.operation_id,
        "operationId": spec.operation_id,
        "side_effect_id": spec.side_effect_id,
        "sideEffectId": spec.side_effect_id,
        "status": status,
        "adapter_called": bool(adapter_called),
        "idempotent": bool(idempotent),
        "provider_ack_status": ack,
        "provider_receipt_hash": str((row or {}).get("provider_receipt_hash") or ""),
        "provider_output_hash": str((row or {}).get("provider_output_hash") or ""),
        "requires_prior": list(spec.requires_prior),
    }


def _operation_rows_from_db(
    rows: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """DB の全9 rowを安全な projection へ変換する。"""

    projected: list[dict[str, Any]] = []
    unknown_ack: list[str] = []
    for spec in EXTERNAL_OPERATION_SPEC_LIST:
        row = rows.get(spec.operation_id)
        status = str((row or {}).get("status") or "not_executed")
        ack = str((row or {}).get("provider_ack_status") or "")
        if status == "completed" and not ack:
            ack = "unknown_unobtainable"
        if status == "completed" and ack == "unknown_unobtainable":
            unknown_ack.append(spec.operation_id)
        projected.append(
            _operation_row(
                spec=spec,
                row=row or {"status": "not_executed"},
                adapter_called=False,
                idempotent=status == "completed",
                provider_ack_status=ack,
            )
        )
    return projected, unknown_ack


def _record_timing(
    store: Any,
    *,
    run_id: str,
    writer_lease: str,
    fencing_token: int,
    event_kind: str,
    operation_id: str,
    started_at: datetime,
    ended_at: datetime,
    status: str,
) -> str | None:
    elapsed = max(0.0, (ended_at - started_at).total_seconds())
    try:
        runtime.record_timing_event(
            store,
            run_id=run_id,
            writer_lease=writer_lease,
            fencing_token=fencing_token,
            event_kind=event_kind,
            started_at=started_at,
            ended_at=ended_at,
            elapsed_seconds=elapsed,
            evidence={
                "operation_id": operation_id,
                "phase": "external_adapter",
                "status": status,
            },
        )
    except Exception as exc:  # noqa: BLE001 - timing failure is typed Red
        return f"timing_record_failed:{event_kind}:{type(exc).__name__}"
    return None


def _adapter_for(
    *,
    store: Any,
    adapters: Mapping[str, Any] | None,
    operation_id: str,
) -> tuple[Callable[..., Any] | None, str | None]:
    test_only = bool(getattr(store, "test_only_allow_semantic_verifier", False))
    if not test_only and adapters is not None:
        return None, "production_adapter_injection_forbidden"
    source: Mapping[str, Any]
    if test_only:
        source = adapters or {}
    else:
        source = PRODUCTION_ADAPTERS
    adapter = source.get(operation_id)
    if not callable(adapter):
        return None, f"external_adapter_unavailable:{operation_id}"
    return adapter, None


def _reconciler_for(
    *,
    store: Any,
    reconcilers: Mapping[str, Any] | None,
    operation_id: str,
) -> tuple[Callable[..., Any] | None, str | None]:
    test_only = bool(getattr(store, "test_only_allow_semantic_verifier", False))
    if not test_only and reconcilers is not None:
        return None, "production_reconciler_injection_forbidden"
    source = (reconcilers or {}) if test_only else PRODUCTION_RECONCILERS
    reconciler = source.get(operation_id)
    if not callable(reconciler):
        return None, f"external_reconciler_unavailable:{operation_id}"
    return reconciler, None


def _reserve_missing(
    store: Any,
    *,
    run_id: str,
    writer_lease: str,
    fencing_token: int,
    context_payload: Mapping[str, Any],
    identity: Mapping[str, Any],
    rows: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], str | None]:
    """全 row をまず reserved に揃える。ここでは adapter を呼ばない。"""

    try:
        payload_json = _canonical_json(dict(context_payload))
    except (TypeError, ValueError, OverflowError):
        return rows, "external_context_json_invalid"
    payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    for spec in EXTERNAL_OPERATION_SPEC_LIST:
        existing = rows.get(spec.operation_id)
        if existing is not None:
            if existing["run_id"] != run_id:
                return rows, "external_outbox_run_id_mismatch"
            if existing["side_effect_id"] != spec.side_effect_id:
                return rows, f"external_outbox_side_effect_id_mismatch:{spec.operation_id}"
            if existing["fencing_token"] != int(identity["fencing_token"]):
                return rows, f"external_outbox_fencing_token_mismatch:{spec.operation_id}"
            if existing["payload_json"] != payload_json:
                return rows, f"external_outbox_payload_conflict:{spec.operation_id}"
            expected_key = f"{run_id}:{spec.operation_id}:{spec.side_effect_id}:{identity['manifest_id']}:{identity['bundle_id']}"
            if existing["idempotency_key"] != expected_key:
                return rows, f"external_outbox_idempotency_key_conflict:{spec.operation_id}"
            if existing["output_hash"] and existing["output_hash"] != payload_hash:
                return rows, f"external_outbox_payload_hash_conflict:{spec.operation_id}"
            continue
        try:
            runtime.record_external_outbox(
                store,
                run_id=run_id,
                writer_lease=writer_lease,
                operation_id=spec.operation_id,
                side_effect_id=spec.side_effect_id,
                status="reserved",
                payload=dict(context_payload),
                idempotency_key=(
                    f"{run_id}:{spec.operation_id}:{spec.side_effect_id}:"
                    f"{identity['manifest_id']}:{identity['bundle_id']}"
                ),
                fencing_token=int(identity["fencing_token"]),
            )
        except Exception as exc:  # noqa: BLE001 - typed reserve Red
            return rows, f"external_outbox_reserve_failed:{spec.operation_id}:{type(exc).__name__}"
        # reread after every atomic insert. A later reserve failure never leads to
        # an adapter call; a reserved row is safe to attach on the next run.
        try:
            rows = _outbox_rows(store, run_id)
        except Exception as exc:  # noqa: BLE001
            return rows, f"external_outbox_observation_failed:{type(exc).__name__}"
    return rows, None


def _adapter_call(
    *,
    adapter: Callable[..., Any],
    context: Mapping[str, Any],
    spec: ExternalOperationSpec,
    identity: Mapping[str, Any],
    idempotency_key: str,
) -> Any:
    # キーワードを固定し、adapter が positional caller 由来の曖昧な identity を
    # 受け取らないようにする。context は外側/内側とも immutable。
    return adapter(
        context=_freeze(context),
        operation_id=spec.operation_id,
        side_effect_id=spec.side_effect_id,
        idempotency_key=idempotency_key,
        manifest_id=str(identity["manifest_id"]),
        bundle_id=str(identity["bundle_id"]),
        run_id=str(identity["run_id"]),
        fencing_token=int(identity["fencing_token"]),
    )


def execute_external_publication(
    *,
    store: Any,
    run_id: str,
    writer_lease: str,
    fencing_token: int,
    adapters: Mapping[str, Callable[..., Mapping[str, Any]]] | None = None,
    reconcilers: Mapping[str, Callable[..., Mapping[str, Any]]] | None = None,
    context: Mapping[str, Any] | None = None,
    allow_provider_send: bool = True,
) -> dict[str, Any]:
    """sealed external outbox を一回だけ順序実行する。

    予約、依存確認、adapter call、receipt 状態遷移をこの順に行う。既に
    ``completed`` の row は reuse し、``started``/``sent``/``unknown_delivery``
    は reconcile required として止める。どの分岐でも同じ operation の
    adapter を二度呼ばない。
    """

    if not isinstance(context, Mapping) and context is not None:
        return _red("external_context_mapping_required")
    context_value: Mapping[str, Any] = dict(context or {})
    if adapters is not None and not isinstance(adapters, Mapping):
        return _red("external_adapters_mapping_required")
    if reconcilers is not None and not isinstance(reconcilers, Mapping):
        return _red("external_reconcilers_mapping_required")
    # production で injection を拒否することを adapter availability より先に
    # 判定し、副作用も state mutation も起こさない。
    if not bool(getattr(store, "test_only_allow_semantic_verifier", False)) and adapters is not None:
        return _red("production_adapter_injection_forbidden")
    if not bool(getattr(store, "test_only_allow_semantic_verifier", False)) and reconcilers is not None:
        return _red("production_reconciler_injection_forbidden")
    if bool(getattr(store, "test_only_allow_semantic_verifier", False)) and isinstance(adapters, Mapping):
        unknown_adapter_ids = sorted(
            str(key) for key in adapters.keys() if str(key) not in EXTERNAL_OPERATION_ORDER
        )
        if unknown_adapter_ids:
            return _red(
                "external_adapter_unknown_operation",
                unknown_operation_ids=unknown_adapter_ids,
            )
    if bool(getattr(store, "test_only_allow_semantic_verifier", False)) and isinstance(reconcilers, Mapping):
        unknown_reconciler_ids = sorted(
            str(key) for key in reconcilers.keys() if str(key) not in EXTERNAL_OPERATION_ORDER
        )
        if unknown_reconciler_ids:
            return _red(
                "external_reconciler_unknown_operation",
                unknown_operation_ids=unknown_reconciler_ids,
            )
    try:
        _canonical_json(dict(context_value))
    except (TypeError, ValueError, OverflowError):
        return _red("external_context_json_invalid")

    identity, state, identity_failure = _sealed_identity(
        store,
        run_id=run_id,
        writer_lease=writer_lease,
        fencing_token=fencing_token,
        context=context_value,
    )
    if identity_failure or identity is None or state is None:
        reason = identity_failure or "external_identity_invalid"
        if reason == "protected_release_reexecution_forbidden":
            return _red(
                reason,
                exact_successor="explicit_new_release_authority_required",
            )
        return _red(reason)
    try:
        rows = _outbox_rows(store, str(identity["run_id"]))
    except Exception as exc:  # noqa: BLE001
        return _red(f"external_outbox_observation_failed:{type(exc).__name__}", **dict(identity))
    unexpected = sorted(
        operation_id
        for operation_id, row in rows.items()
        if row["run_id"] == str(identity["run_id"])
        and operation_id not in EXTERNAL_OPERATION_ORDER
    )
    if unexpected:
        return _red("external_outbox_unexpected_operation", unexpected_operation_ids=unexpected, **dict(identity))

    # 以前の process が provider call 後に停止した場合は、adapter availability
    # を調べる前に reconcile へ止める。production の未登録 adapter Red が、
    # 既に started/unknown の side-effect を再実行する判断へすり替わらないようにする。
    ambiguous_existing = next(
        (
            spec
            for spec in EXTERNAL_OPERATION_SPEC_LIST
            if (rows.get(spec.operation_id) or {}).get("status")
            in {"started", "sent", "acknowledged", "unknown_delivery", "unknown_unobtainable"}
        ),
        None,
    )
    if ambiguous_existing is not None:
        if not bool(getattr(store, "test_only_allow_semantic_verifier", False)):
            release_failure = _verify_sealed_release_files(identity, context_value)
            if release_failure:
                return _red(release_failure, **dict(identity))
        reconciler, reconciler_failure = _reconciler_for(
            store=store,
            reconcilers=reconcilers,
            operation_id=ambiguous_existing.operation_id,
        )
        if reconciler is not None and reconciler_failure is None:
            current = rows[ambiguous_existing.operation_id]
            try:
                raw_reconciled = _adapter_call(
                    adapter=reconciler,
                    context=context_value,
                    spec=ambiguous_existing,
                    identity=identity,
                    idempotency_key=str(current["idempotency_key"]),
                )
                validated_reconciled = validate_external_adapter_receipt(
                    raw_reconciled,
                    operation_id=ambiguous_existing.operation_id,
                    side_effect_id=ambiguous_existing.side_effect_id,
                    idempotency_key=str(current["idempotency_key"]),
                    identity=identity,
                    require_provider_evidence=not bool(getattr(store, "test_only_allow_semantic_verifier", False)),
                )
                if validated_reconciled.get("ok") is not True:
                    raise RuntimeError("external_reconciler_receipt_invalid")
                if not bool(getattr(store, "test_only_allow_semantic_verifier", False)):
                    _record_external_provider_binding(context=context_value, receipt=validated_reconciled)
                runtime.complete_external_outbox_atomic(
                    store,
                    run_id=str(identity["run_id"]),
                    writer_lease=str(writer_lease),
                    fencing_token=int(identity["fencing_token"]),
                    operation_id=ambiguous_existing.operation_id,
                    provider_receipt=validated_reconciled,
                    provider_ack_status=str(validated_reconciled.get("provider_ack_status") or "unknown_unobtainable"),
                    reconciled=True,
                )
                rows = _outbox_rows(store, str(identity["run_id"]))
                ambiguous_existing = None
            except Exception as exc:  # noqa: BLE001 - reconciliation is fail-closed
                reconciler_failure = f"external_reconcile_unconfirmed:{ambiguous_existing.operation_id}:{type(exc).__name__}"
        if ambiguous_existing is None:
            pass
        else:
            projected_rows, unknown_ack = _operation_rows_from_db(rows)
            return _reconcile(
                reconciler_failure or f"external_reconcile_required:{ambiguous_existing.operation_id}:{rows[ambiguous_existing.operation_id]['status']}",
                operation_id=ambiguous_existing.operation_id,
                operations=projected_rows,
                run_id=str(identity["run_id"]),
                manifest_id=str(identity["manifest_id"]),
                bundle_id=str(identity["bundle_id"]),
                fencing_token=int(identity["fencing_token"]),
                adapter_call_count=0,
                provider_ack_unknown_ids=unknown_ack,
            )

    # seal の operation 列を検査した後、既存 status に応じて必要な adapter だけ
    # availability check。completed/unknown は provider call を要求しない。
    valid_statuses = {
        "reserved",
        "started",
        "sent",
        "acknowledged",
        "completed",
        "unknown_delivery",
        "unknown_unobtainable",
    }
    for spec in EXTERNAL_OPERATION_SPEC_LIST:
        row = rows.get(spec.operation_id)
        if row is not None and row["status"] not in valid_statuses:
            return _red(
                f"external_outbox_status_invalid:{spec.operation_id}",
                **dict(identity),
            )
        if allow_provider_send and (row is None or row["status"] == "reserved"):
            _adapter, adapter_failure = _adapter_for(
                store=store,
                adapters=adapters,
                operation_id=spec.operation_id,
            )
            if adapter_failure:
                return _red(adapter_failure, **dict(identity))

    if not bool(getattr(store, "test_only_allow_semantic_verifier", False)):
        remote_failure = _verify_remote_cas_before_side_effects(identity, context_value, rows)
        if remote_failure:
            return _red(remote_failure, **dict(identity))

    if not bool(getattr(store, "test_only_allow_semantic_verifier", False)):
        release_failure = _verify_sealed_release_files(identity, context_value)
        if release_failure:
            return _red(release_failure, **dict(identity))

    rows, reserve_failure = _reserve_missing(
        store,
        run_id=str(identity["run_id"]),
        writer_lease=str(writer_lease),
        fencing_token=int(identity["fencing_token"]),
        context_payload=context_value,
        identity=identity,
        rows=rows,
    )
    if reserve_failure:
        return _red(reserve_failure, **dict(identity))

    operation_rows: list[dict[str, Any]] = []
    provider_ack_unknown_ids: list[str] = []
    adapter_call_count = 0

    for spec in EXTERNAL_OPERATION_SPEC_LIST:
        current = rows.get(spec.operation_id)
        if current is None:
            return _red(
                f"external_outbox_row_missing:{spec.operation_id}",
                operations=operation_rows,
                **dict(identity),
            )
        current_status = current["status"]
        if current_status in {"started", "sent", "acknowledged", "unknown_delivery", "unknown_unobtainable"}:
            return _reconcile(
                f"external_reconcile_required:{spec.operation_id}:{current_status}",
                operation_id=spec.operation_id,
                operations=operation_rows
                + [_operation_row(spec=spec, row=current, adapter_called=False, idempotent=False)],
                run_id=str(identity["run_id"]),
                manifest_id=str(identity["manifest_id"]),
                bundle_id=str(identity["bundle_id"]),
                fencing_token=int(identity["fencing_token"]),
                adapter_call_count=adapter_call_count,
                provider_ack_unknown_ids=provider_ack_unknown_ids,
            )
        if current_status == "completed":
            completed_failure = _completed_outbox_row_failure(
                current,
                spec=spec,
                identity=identity,
                strict_provider=not bool(getattr(store, "test_only_allow_semantic_verifier", False)),
            )
            if completed_failure:
                return _red(completed_failure, operations=operation_rows, **dict(identity))
            ack = current["provider_ack_status"] or "unknown_unobtainable"
            if ack == "unknown_unobtainable":
                provider_ack_unknown_ids.append(spec.operation_id)
            operation_rows.append(
                _operation_row(
                    spec=spec,
                    row=current,
                    adapter_called=False,
                    idempotent=True,
                    provider_ack_status=ack,
                )
            )
            continue
        if current_status != "reserved":
            return _red(
                f"external_outbox_status_invalid:{spec.operation_id}",
                operations=operation_rows,
                **dict(identity),
            )

        if not allow_provider_send:
            return _reconcile(
                f"external_provider_send_frozen:{spec.operation_id}",
                operation_id=spec.operation_id,
                operations=operation_rows
                + [_operation_row(spec=spec, row=current, adapter_called=False, idempotent=False)],
                run_id=str(identity["run_id"]),
                manifest_id=str(identity["manifest_id"]),
                bundle_id=str(identity["bundle_id"]),
                fencing_token=int(identity["fencing_token"]),
                adapter_call_count=adapter_call_count,
                provider_ack_unknown_ids=provider_ack_unknown_ids,
            )

        # 依存 operation が completed であることを、adapter 呼出し前に確認する。
        status_by_id = {
            str(item["operation_id"]): str(item["status"])
            for item in operation_rows
        }
        status_by_id.update({spec.operation_id: current_status})
        for prior in spec.requires_prior:
            prior_status = status_by_id.get(prior) or str((rows.get(prior) or {}).get("status") or "")
            if prior_status != "completed":
                if prior_status in {"started", "sent", "acknowledged", "unknown_delivery", "unknown_unobtainable"}:
                    return _reconcile(
                        f"external_dependency_reconcile_required:{prior}",
                        operation_id=prior,
                        operations=operation_rows,
                        run_id=str(identity["run_id"]),
                        manifest_id=str(identity["manifest_id"]),
                        bundle_id=str(identity["bundle_id"]),
                        fencing_token=int(identity["fencing_token"]),
                        adapter_call_count=adapter_call_count,
                        provider_ack_unknown_ids=provider_ack_unknown_ids,
                    )
                return _red(
                    f"external_dependency_not_completed:{spec.operation_id}:{prior}",
                    operations=operation_rows,
                    **dict(identity),
                )

        adapter, adapter_failure = _adapter_for(
            store=store,
            adapters=adapters,
            operation_id=spec.operation_id,
        )
        if adapter_failure or adapter is None:
            return _red(
                adapter_failure or f"external_adapter_unavailable:{spec.operation_id}",
                operations=operation_rows,
                **dict(identity),
            )
        expected_key = current["idempotency_key"]
        if not bool(getattr(store, "test_only_allow_semantic_verifier", False)):
            # long external loopの開始時観測を再利用しない。各副作用のCAS
            # transition直前にremoteとsealed bytesを再観測し、stale release
            # の動画公開・通知送信を防ぐ。
            remote_failure = _verify_remote_cas_before_side_effects(
                identity,
                context_value,
                rows,
            )
            if remote_failure:
                return _red(remote_failure, operations=operation_rows, **dict(identity))
            release_failure = _verify_sealed_release_files(
                identity,
                context_value,
                operation_id=spec.operation_id,
            )
            if release_failure:
                return _red(release_failure, operations=operation_rows, **dict(identity))
            if spec.operation_id == "notification_send":
                readiness_failure = _verify_pre_notification_public_readiness(
                    identity,
                    context_value,
                )
                if readiness_failure:
                    return _red(readiness_failure, operations=operation_rows, **dict(identity))
        started_at = store.now()
        try:
            transition = runtime.start_external_outbox_atomic(
                store,
                run_id=str(identity["run_id"]),
                writer_lease=str(writer_lease),
                fencing_token=int(identity["fencing_token"]),
                operation_id=spec.operation_id,
                event_kind="external_wait",
                started_at=started_at,
                evidence={
                    "operation_id": spec.operation_id,
                    "phase": "external_adapter",
                    "boundary": "before_adapter_call",
                },
            )
            if transition.get("status") != "started":
                raise RuntimeError("external_outbox_start_transition_invalid")
        except Exception as exc:  # noqa: BLE001
            return _red(
                f"external_outbox_start_failed:{spec.operation_id}:{type(exc).__name__}",
                operations=operation_rows,
                **dict(identity),
            )

        adapter_call_count += 1
        try:
            raw_receipt = _adapter_call(
                adapter=adapter,
                context=context_value,
                spec=spec,
                identity=identity,
                idempotency_key=expected_key,
            )
            validated = validate_external_adapter_receipt(
                raw_receipt,
                operation_id=spec.operation_id,
                side_effect_id=spec.side_effect_id,
                idempotency_key=expected_key,
                identity=identity,
                require_provider_evidence=not bool(
                    getattr(store, "test_only_allow_semantic_verifier", False)
                ),
            )
        except Exception as exc:  # noqa: BLE001 - provider response is unknown
            validated = {
                "schemaVersion": EXTERNAL_ADAPTER_RECEIPT_SCHEMA,
                "ok": False,
                "status": "invalid",
                "failures": [f"adapter_exception:{type(exc).__name__}"],
            }
        ended_at = store.now()
        timing_failure = _record_timing(
            store,
            run_id=str(identity["run_id"]),
            writer_lease=str(writer_lease),
            fencing_token=int(identity["fencing_token"]),
            event_kind="external_wait",
            operation_id=spec.operation_id,
            started_at=started_at,
            ended_at=ended_at,
            status="completed" if validated.get("ok") is True else "unknown_delivery",
        )

        if validated.get("ok") is not True:
            failure_timing = _record_timing(
                store,
                run_id=str(identity["run_id"]),
                writer_lease=str(writer_lease),
                fencing_token=int(identity["fencing_token"]),
                event_kind="failure",
                operation_id=spec.operation_id,
                started_at=started_at,
                ended_at=ended_at,
                status="unknown_delivery",
            )
            if timing_failure is None and failure_timing is not None:
                timing_failure = failure_timing
            try:
                runtime.transition_external_outbox(
                    store,
                    run_id=str(identity["run_id"]),
                    writer_lease=str(writer_lease),
                    fencing_token=int(identity["fencing_token"]),
                    operation_id=spec.operation_id,
                    expected_status="started",
                    next_status="unknown_delivery",
                    provider_ack_status="unknown_unobtainable",
                )
            except Exception as exc:  # noqa: BLE001
                return _reconcile(
                    f"external_unknown_transition_failed:{spec.operation_id}:{type(exc).__name__}",
                    operation_id=spec.operation_id,
                    operations=operation_rows,
                    run_id=str(identity["run_id"]),
                    manifest_id=str(identity["manifest_id"]),
                    bundle_id=str(identity["bundle_id"]),
                    fencing_token=int(identity["fencing_token"]),
                    adapter_call_count=adapter_call_count,
                    provider_ack_unknown_ids=provider_ack_unknown_ids,
                )
            failure = f"external_adapter_receipt_invalid:{spec.operation_id}"
            if timing_failure:
                failure = timing_failure
            return _reconcile(
                failure,
                operation_id=spec.operation_id,
                operations=operation_rows
                + [
                    _operation_row(
                        spec=spec,
                        row={"status": "unknown_delivery", "provider_ack_status": "unknown_unobtainable"},
                        adapter_called=True,
                        idempotent=False,
                        provider_ack_status="unknown_unobtainable",
                    )
                ],
                run_id=str(identity["run_id"]),
                manifest_id=str(identity["manifest_id"]),
                bundle_id=str(identity["bundle_id"]),
                fencing_token=int(identity["fencing_token"]),
                adapter_call_count=adapter_call_count,
                provider_ack_unknown_ids=provider_ack_unknown_ids,
            )

        if not bool(getattr(store, "test_only_allow_semantic_verifier", False)):
            try:
                _record_external_provider_binding(context=context_value, receipt=validated)
            except Exception as exc:  # noqa: BLE001 - provider may already have completed
                try:
                    runtime.transition_external_outbox(
                        store,
                        run_id=str(identity["run_id"]),
                        writer_lease=str(writer_lease),
                        fencing_token=int(identity["fencing_token"]),
                        operation_id=spec.operation_id,
                        expected_status="started",
                        next_status="unknown_delivery",
                        provider_ack_status="unknown_unobtainable",
                    )
                except Exception:
                    pass
                return _reconcile(
                    f"external_provider_binding_failed:{spec.operation_id}:{type(exc).__name__}",
                    operation_id=spec.operation_id,
                    operations=operation_rows,
                    run_id=str(identity["run_id"]),
                    manifest_id=str(identity["manifest_id"]),
                    bundle_id=str(identity["bundle_id"]),
                    fencing_token=int(identity["fencing_token"]),
                    adapter_call_count=adapter_call_count,
                    provider_ack_unknown_ids=provider_ack_unknown_ids,
                )

        provider_ack_status = str(validated.get("provider_ack_status") or "unknown_unobtainable")
        try:
            completed = runtime.complete_external_outbox_atomic(
                store,
                run_id=str(identity["run_id"]),
                writer_lease=str(writer_lease),
                fencing_token=int(identity["fencing_token"]),
                operation_id=spec.operation_id,
                provider_receipt=validated,
                provider_ack_status=provider_ack_status,
            )
            if completed.get("status") != "completed":
                raise RuntimeError("external_outbox_completed_transition_invalid")
        except Exception as exc:  # noqa: BLE001 - side effect may already exist
            return _reconcile(
                f"external_outbox_completion_transition_failed:{spec.operation_id}:{type(exc).__name__}",
                operation_id=spec.operation_id,
                operations=operation_rows
                + [
                    _operation_row(
                        spec=spec,
                        row={"status": "started", "provider_ack_status": provider_ack_status},
                        adapter_called=True,
                        idempotent=False,
                        provider_ack_status=provider_ack_status,
                    )
                ],
                run_id=str(identity["run_id"]),
                manifest_id=str(identity["manifest_id"]),
                bundle_id=str(identity["bundle_id"]),
                fencing_token=int(identity["fencing_token"]),
                adapter_call_count=adapter_call_count,
                provider_ack_unknown_ids=provider_ack_unknown_ids,
            )

        if provider_ack_status == "unknown_unobtainable":
            provider_ack_unknown_ids.append(spec.operation_id)
        final_row = {
            "status": "completed",
            "provider_ack_status": provider_ack_status,
            "idempotency_key": expected_key,
            "provider_receipt_json": _canonical_json(dict(validated)),
            "provider_receipt_hash": str(completed.get("provider_receipt_hash") or ""),
            "provider_output_hash": str(completed.get("provider_output_hash") or ""),
        }
        rows[spec.operation_id] = final_row
        completed_failure = _completed_outbox_row_failure(
            final_row,
            spec=spec,
            identity=identity,
            strict_provider=not bool(getattr(store, "test_only_allow_semantic_verifier", False)),
        )
        if completed_failure:
            return _red(completed_failure, operations=operation_rows, **dict(identity))
        operation_rows.append(
            _operation_row(
                spec=spec,
                row=final_row,
                adapter_called=True,
                idempotent=False,
                provider_ack_status=provider_ack_status,
            )
        )
        # timing failure is never hidden by a successful provider receipt. The
        # external effect is completed and must not be retried, but operation
        # result remains non-Green for operational repair.
        if timing_failure:
            # 外部 side-effect 自体は既に completed。timing repair を external
            # reconcile と誤表示すると、再送担当が adapter を重ねるため、
            # exact successor を timing 修復へ分離する。
            return {
                "schemaVersion": EXTERNAL_PUBLICATION_RECEIPT_SCHEMA,
                "ok": False,
                "status": "red",
                "failures": [timing_failure],
                "exact_successor": f"timing_repair:{spec.operation_id}",
                "run_id": str(identity["run_id"]),
                "runId": str(identity["run_id"]),
                "manifest_id": str(identity["manifest_id"]),
                "manifestId": str(identity["manifest_id"]),
                "bundle_id": str(identity["bundle_id"]),
                "bundleId": str(identity["bundle_id"]),
                "operations": operation_rows,
                "operation_rows": operation_rows,
                "adapter_call_count": adapter_call_count,
                "adapterCallCount": adapter_call_count,
                "duplicate_call_count": 0,
                "duplicateCallCount": 0,
                "provider_ack_unknown_ids": provider_ack_unknown_ids,
                "providerAckUnknownIds": provider_ack_unknown_ids,
                "humanImpact": {
                    "noFocusTheft": True,
                    "noAutoOpen": True,
                    "noUserMonitoring": True,
                },
            }
        try:
            rows = _outbox_rows(store, str(identity["run_id"]))
        except Exception as exc:  # noqa: BLE001
            return _reconcile(
                f"external_outbox_observation_failed:{type(exc).__name__}",
                operation_id=spec.operation_id,
                operations=operation_rows,
                run_id=str(identity["run_id"]),
                manifest_id=str(identity["manifest_id"]),
                bundle_id=str(identity["bundle_id"]),
                fencing_token=int(identity["fencing_token"]),
                adapter_call_count=adapter_call_count,
                provider_ack_unknown_ids=provider_ack_unknown_ids,
            )

    completed_statuses = [str(item.get("status")) == "completed" for item in operation_rows]
    all_completed = len(operation_rows) == len(EXTERNAL_OPERATION_ORDER) and all(completed_statuses)
    return {
        "schemaVersion": EXTERNAL_PUBLICATION_RECEIPT_SCHEMA,
        "ok": all_completed,
        "status": "completed" if all_completed else "red",
        "failures": [] if all_completed else ["external_operation_incomplete"],
        "run_id": str(identity["run_id"]),
        "runId": str(identity["run_id"]),
        "manifest_id": str(identity["manifest_id"]),
        "manifestId": str(identity["manifest_id"]),
        "bundle_id": str(identity["bundle_id"]),
        "bundleId": str(identity["bundle_id"]),
        "operation_ids": list(EXTERNAL_OPERATION_ORDER),
        "externalOperationIds": list(EXTERNAL_OPERATION_ORDER),
        "operations": operation_rows,
        "operation_rows": operation_rows,
        "adapter_call_count": adapter_call_count,
        "adapterCallCount": adapter_call_count,
        "duplicate_call_count": 0,
        "duplicateCallCount": 0,
        "provider_ack_unknown_ids": provider_ack_unknown_ids,
        "providerAckUnknownIds": provider_ack_unknown_ids,
        "humanImpact": {
            "noFocusTheft": True,
            "noAutoOpen": True,
            "noUserMonitoring": True,
        },
    }


def build_external_recovery_projection(
    *,
    store: Any | None = None,
    run_id: str | None = None,
    receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """外部 outbox の read-only recovery projection を作る。"""

    source_rows: list[Mapping[str, Any]] = []
    source_run_id = str(run_id or "").strip()
    if isinstance(receipt, Mapping):
        value = receipt.get("operations") or receipt.get("operation_rows") or ()
        if isinstance(value, (list, tuple)):
            source_rows = [item for item in value if isinstance(item, Mapping)]
        source_run_id = source_run_id or str(receipt.get("run_id") or receipt.get("runId") or "")
    elif store is not None and source_run_id:
        try:
            source_rows = list(_outbox_rows(store, source_run_id).values())
        except Exception as exc:  # noqa: BLE001
            return {
                "schemaVersion": EXTERNAL_RECOVERY_PROJECTION_SCHEMA,
                "ok": False,
                "status": "red",
                "failures": [f"recovery_observation_failed:{type(exc).__name__}"],
                "run_id": source_run_id,
                "operations": [],
                "humanImpact": {"noFocusTheft": True, "noAutoOpen": True, "noUserMonitoring": True},
            }
    if not source_rows:
        return {
            "schemaVersion": EXTERNAL_RECOVERY_PROJECTION_SCHEMA,
            "ok": False,
            "status": "red",
            "failures": ["recovery_outbox_empty"],
            "run_id": source_run_id,
            "operations": [],
            "humanImpact": {"noFocusTheft": True, "noAutoOpen": True, "noUserMonitoring": True},
        }

    rows_by_id = {
        str(item.get("operation_id") or item.get("operationId") or ""): item
        for item in source_rows
    }
    projected: list[dict[str, Any]] = []
    successors: list[str] = []
    failures: list[str] = []
    for spec in EXTERNAL_OPERATION_SPEC_LIST:
        row = rows_by_id.get(spec.operation_id)
        status = str((row or {}).get("status") or "missing")
        action = "reuse_completed" if status == "completed" else (
            f"external_reconcile:{spec.operation_id}"
            if status in {"started", "sent", "acknowledged", "unknown_delivery", "unknown_unobtainable"}
            else "attach_reserved"
            if status == "reserved"
            else "stop_missing"
        )
        if action.startswith("external_reconcile:"):
            successors.append(action)
            failures.append(f"reconcile_required:{spec.operation_id}")
        if status == "missing":
            failures.append(f"outbox_missing:{spec.operation_id}")
        projected.append(
            {
                "operation_id": spec.operation_id,
                "side_effect_id": spec.side_effect_id,
                "status": status,
                "action": action,
                "requires_prior": list(spec.requires_prior),
            }
        )
    if failures:
        status = "reconcile_required" if successors else "red"
    else:
        status = "completed"
    return {
        "schemaVersion": EXTERNAL_RECOVERY_PROJECTION_SCHEMA,
        "ok": status == "completed",
        "status": status,
        "failures": failures,
        "exact_successors": successors,
        "run_id": source_run_id,
        "operations": projected,
        "duplicate_call_count": 0,
        "humanImpact": {"noFocusTheft": True, "noAutoOpen": True, "noUserMonitoring": True},
    }


# recovery projection の呼び出し側が名称を固定していない世代でも、同じ実装を
# 参照させる。別実装・別 predicate は作らない。
project_external_recovery = build_external_recovery_projection
external_recovery_projection = build_external_recovery_projection
recover_external_publication = build_external_recovery_projection


__all__ = [
    "EXTERNAL_OPERATION_ORDER",
    "EXTERNAL_OPERATION_IDS",
    "DAILY_EXTERNAL_OPERATION_ORDER",
    "ExternalOperationSpec",
    "EXTERNAL_OPERATION_SPECS",
    "EXTERNAL_OPERATION_SPEC_LIST",
    "EXTERNAL_OPERATIONS",
    "EXTERNAL_SIDE_EFFECT_IDS",
    "PRODUCTION_ADAPTERS",
    "EXTERNAL_PUBLICATION_RECEIPT_SCHEMA",
    "EXTERNAL_ADAPTER_RECEIPT_SCHEMA",
    "EXTERNAL_RECOVERY_PROJECTION_SCHEMA",
    "validate_external_adapter_receipt",
    "execute_external_publication",
    "build_external_recovery_projection",
    "project_external_recovery",
    "external_recovery_projection",
    "recover_external_publication",
]
