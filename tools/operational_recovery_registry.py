"""登録済みNews-Grasp復旧handlerだけをdispatchする。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping


REGISTRY_SCHEMA = "OPERATIONAL_RECOVERY_REGISTRY_V1"
exact_handler_dispatch = "exact_handler_dispatch"
REGISTRY_PATH = Path("config/operational_recovery_registry_v1.json")
Handler = Callable[[Mapping[str, Any]], Mapping[str, Any]]


class OperationalRecoveryRegistryError(RuntimeError):
    """registry違反を型付きで返す。"""


@dataclass(frozen=True)
class RecoveryRegistration:
    handler_id: str
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class RecoveryDispatch:
    status: str
    handler_id: str
    reason_code: str
    result: Mapping[str, Any]


EXPECTED_HANDLER_IDS = (
    "active_generation_reconcile",
    "previous_generation_restore",
    "checkpoint_continuation",
    "summary_audio_script_builder",
    "deepdive_dialogue_builder",
    "deepdive_audio_builder",
    "current_issue_page_renderer",
    "distribution_manifest_builder",
    "deepdive_provenance_repair",
    "deepdive_dialogue_repair",
    "deepdive_public_repair",
    "reporter_artifact_model_route",
    "summary_model_route",
    "deepdive_article_model_route",
    "checkpoint_public_republish",
    "major_incident_terminal",
)


def _load(repo_root: Path) -> tuple[tuple[RecoveryRegistration, ...], str]:
    path = repo_root / REGISTRY_PATH
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OperationalRecoveryRegistryError("NG_RECOVERY_REGISTRY_INVALID") from exc
    if value.get("schemaVersion") != REGISTRY_SCHEMA or value.get("productId") != "News-Grasp":
        raise OperationalRecoveryRegistryError("NG_RECOVERY_REGISTRY_INVALID")
    entries = value.get("handlers")
    if not isinstance(entries, list) or len(entries) != len(EXPECTED_HANDLER_IDS):
        raise OperationalRecoveryRegistryError("NG_RECOVERY_REGISTRY_INVALID")
    registrations: list[RecoveryRegistration] = []
    for item in entries:
        if not isinstance(item, dict):
            raise OperationalRecoveryRegistryError("NG_RECOVERY_REGISTRY_INVALID")
        handler_id = item.get("handlerId")
        codes = item.get("reasonCodes")
        if not isinstance(handler_id, str) or not isinstance(codes, list) or not codes:
            raise OperationalRecoveryRegistryError("NG_RECOVERY_REGISTRY_INVALID")
        if handler_id not in EXPECTED_HANDLER_IDS or any(not isinstance(code, str) or not code for code in codes):
            raise OperationalRecoveryRegistryError("NG_RECOVERY_REGISTRY_INVALID")
        if any(" or " in code.casefold() or "*" in code for code in codes):
            raise OperationalRecoveryRegistryError("NG_RECOVERY_REGISTRY_INVALID")
        registrations.append(RecoveryRegistration(handler_id, tuple(codes)))
    if tuple(item.handler_id for item in registrations) != EXPECTED_HANDLER_IDS:
        raise OperationalRecoveryRegistryError("NG_RECOVERY_REGISTRY_INVALID")
    unknown = value.get("unknownReasonHandler")
    if unknown != "major_incident_terminal":
        raise OperationalRecoveryRegistryError("NG_RECOVERY_REGISTRY_INVALID")
    return tuple(registrations), unknown


def validate_registry(repo_root: Path | str) -> dict[str, Any]:
    registrations, unknown = _load(Path(repo_root).resolve())
    mapping: dict[str, str] = {}
    for registration in registrations:
        for reason_code in registration.reason_codes:
            if reason_code in mapping:
                raise OperationalRecoveryRegistryError("NG_RECOVERY_REASON_DUPLICATE")
            mapping[reason_code] = registration.handler_id
    return {
        "status": "validated",
        "schemaVersion": REGISTRY_SCHEMA,
        "handlerCount": len(registrations),
        "reasonCount": len(mapping),
        "unknownReasonHandler": unknown,
    }


def resolve_handler_id(*, repo_root: Path | str, reason_code: str) -> str:
    """reason codeの完全一致から一意なhandlerだけを返す。"""
    registrations, unknown = _load(Path(repo_root).resolve())
    matched = [
        registration.handler_id
        for registration in registrations
        if reason_code in registration.reason_codes
    ]
    if len(matched) > 1:
        raise OperationalRecoveryRegistryError("NG_RECOVERY_REASON_DUPLICATE")
    return matched[0] if matched else unknown


def dispatch(
    *, repo_root: Path | str, reason_code: str, context: Mapping[str, Any], handlers: Mapping[str, Handler]
) -> RecoveryDispatch:
    registrations, unknown = _load(Path(repo_root).resolve())
    selected = resolve_handler_id(repo_root=repo_root, reason_code=reason_code)
    if selected == unknown and not any(
        reason_code in registration.reason_codes for registration in registrations
    ):
        selected = unknown
        reason_code = "UNKNOWN_REASON"
    handler = handlers.get(selected)
    if handler is None:
        raise OperationalRecoveryRegistryError("NG_RECOVERY_HANDLER_NOT_REGISTERED")
    result = handler(context)
    if not isinstance(result, Mapping):
        raise OperationalRecoveryRegistryError("NG_RECOVERY_HANDLER_RESULT_INVALID")
    return RecoveryDispatch("dispatched", selected, reason_code, result)


def default_handlers() -> dict[str, Handler]:
    """product-localで登録できるhandler集合を返す。

    model routeはここでモデルを起動せず、上流で発行されたroute receiptを
    渡された場合だけ登録済みrouteとして返す。未知理由はmajor incidentへ閉じる。
    """
    from tools import news_grasp_deterministic_builders as builders

    def _typed(action: str, *, mutation_count: int = 0) -> Handler:
        def handler(context: Mapping[str, Any]) -> Mapping[str, Any]:
            return {
                "status": "completed",
                "action": action,
                "mutationCount": mutation_count,
                "lineageId": context.get("dailyOperationLineageId"),
            }

        return handler

    def _summary(context: Mapping[str, Any]) -> Mapping[str, Any]:
        return builders.build_summary_audio_script(context["summary"])

    def _dialogue(context: Mapping[str, Any]) -> Mapping[str, Any]:
        return builders.build_deepdive_dialogue(context["article"])

    def _audio(context: Mapping[str, Any]) -> Mapping[str, Any]:
        turns = context.get("turns")
        if not isinstance(turns, list) or not turns:
            raise OperationalRecoveryRegistryError("NG_RECOVERY_HANDLER_INPUT_INVALID")
        return {"schemaVersion": "DEEPDIVE_AUDIO_V1", "turnCount": len(turns), "modelCalls": 0}

    def _manifest(context: Mapping[str, Any]) -> Mapping[str, Any]:
        return builders.build_distribution_manifest(context["artifacts"])

    def _model_route(route_id: str) -> Handler:
        def handler(context: Mapping[str, Any]) -> Mapping[str, Any]:
            receipt = context.get("routeReceipt")
            if not isinstance(receipt, Mapping) or receipt.get("routeId") != route_id:
                raise OperationalRecoveryRegistryError("NG_MODEL_ROUTE_RECEIPT_REQUIRED")
            return {"status": "route_registered", "routeId": route_id, "modelCallRequired": True}

        return handler

    def _major(context: Mapping[str, Any]) -> Mapping[str, Any]:
        return {
            "status": "incident_open",
            "action": "append_only_major_incident",
            "mutationCount": 0,
            "reasonCode": context.get("reasonCode", "UNKNOWN_REASON"),
        }

    return {
        "active_generation_reconcile": _typed("active_generation_reconcile", mutation_count=1),
        "previous_generation_restore": _typed("previous_generation_restore", mutation_count=1),
        "checkpoint_continuation": _typed("checkpoint_continuation"),
        "summary_audio_script_builder": _summary,
        "deepdive_dialogue_builder": _dialogue,
        "deepdive_audio_builder": _audio,
        "current_issue_page_renderer": _typed("current_issue_page_renderer", mutation_count=1),
        "distribution_manifest_builder": _manifest,
        "deepdive_provenance_repair": _typed("deepdive_provenance_repair", mutation_count=1),
        "deepdive_dialogue_repair": _typed("deepdive_dialogue_repair", mutation_count=1),
        "deepdive_public_repair": _typed("deepdive_public_repair", mutation_count=1),
        "reporter_artifact_model_route": _model_route("reporter_artifact_model_route"),
        "summary_model_route": _model_route("summary_model_route"),
        "deepdive_article_model_route": _model_route("deepdive_article_model_route"),
        "checkpoint_public_republish": _typed("checkpoint_public_republish"),
        "major_incident_terminal": _major,
    }
