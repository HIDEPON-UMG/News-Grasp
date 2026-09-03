"""Daily gateとRelease gateのproduct-local profile分離。"""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import dataclass
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Mapping


class NewsGraspGateProfileError(RuntimeError):
    """gate profile違反。"""


DAILY_ORACLES = (
    "artifact_schema_quality",
    "required_public_bundle",
    "public_surface",
    "distribution",
    "notification",
    "pure_readiness",
)
RELEASE_ORACLES = (
    "pytest_regression",
    "playwright",
    "historical_period",
    "crash_replay_drift",
    "final_nopublish_e2e",
)


@dataclass(frozen=True)
class GateProfile:
    profile_id: str
    oracles: tuple[str, ...]
    reachable_from_scheduled: bool


DAILY_PROFILE = GateProfile("DailyProductGateProfileV1", DAILY_ORACLES, True)
RELEASE_PROFILE = GateProfile("ReleaseGateProfileV1", RELEASE_ORACLES, False)


# Daily brokerから到達できるrouteはここで固定する。pytest等の汎用実行器を
# capabilityとして再利用せず、broker自身の一操作CLIだけを許可する。
DAILY_OPERATIONS = (
    "static_check",
    "scoped_contract_unit",
    "current_issue_integration",
    "external_publication",
    "consumer_public_verification",
    "atomic_completion",
)
DAILY_PYTHON = r"C:\Users\hidek\AppData\Local\Programs\Python\Python312\python.exe"
DAILY_OPERATION_COMMANDS = {
    operation: (DAILY_PYTHON, "-m", "tools.news_grasp_daily_gate", operation)
    for operation in DAILY_OPERATIONS
}
DAILY_ROUTE_SCHEMA = "NEWS_GRASP_DAILY_ROUTE_CAPABILITY_V1"
_ROOT = Path(__file__).resolve().parents[1]
_DAILY_ROUTE_REGISTRY = _ROOT / "config" / "news_grasp_daily_control_routes.json"
_DAILY_ENTRY_MODULES = (
    _ROOT / "tools" / "news_grasp_daily_launcher.py",
    _ROOT / "tools" / "news_grasp_daily_gate.py",
    _ROOT / "tools" / "news_grasp_gate_profiles.py",
)
_FORBIDDEN_DAILY_IMPORTS = frozenset(
    {
        "tools.news_grasp_release_gate",
        "tools.news_grasp_history_isolation",
        "tools.news_grasp_nopublish",
        "pytest",
        "playwright",
    }
)


def _argv_digest(argv: Sequence[str]) -> str:
    return hashlib.sha256(
        json.dumps(list(argv), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def build_daily_route_capability(
    operation_id: str,
    *,
    runtime_generation: str = "NEWS_GRASP_DIRECT_RUNTIME_V2",
) -> dict[str, Any]:
    """Daily brokerがspawnへ渡す型付きcapabilityを作る。"""

    argv = daily_operation_command(operation_id)
    generation = str(runtime_generation or "").strip()
    if not generation:
        raise NewsGraspGateProfileError("daily_runtime_generation_missing")
    return {
        "schemaVersion": DAILY_ROUTE_SCHEMA,
        "profile": DAILY_PROFILE.profile_id,
        "operation_id": operation_id,
        "argv": list(argv),
        "argv_sha256": _argv_digest(argv),
        "runtime_generation": generation,
        "capability": "scheduled_production_daily",
    }


def _operation_error(code: str, operation_id: Any, command: Any) -> NewsGraspGateProfileError:
    return NewsGraspGateProfileError(
        f"{code}: operation_id={operation_id!r} command={command!r}"
    )


def daily_operation_command(operation_id: str) -> tuple[str, ...]:
    """登録済みDaily operationのcanonical argvを返す。"""

    try:
        return DAILY_OPERATION_COMMANDS[operation_id]
    except (KeyError, TypeError) as exc:
        raise _operation_error("daily_operation_unknown", operation_id, None) from exc


def authorize_daily_operation(
    operation_id: str,
    command: Sequence[str],
    *,
    capability: Mapping[str, Any] | None = None,
    runtime_generation: str = "",
) -> dict[str, Any]:
    """spawn前にDaily operationとargvを完全一致で認可する。

    ``command``は文字列を含むシーケンスとして受け取るが、比較はtuple化した
    canonical argvとの完全一致だけで行う。未知route、raw Python、Release-only
    command、pytestの全件実行は、子processを起動する前に同じ型付き拒否となる。
    """

    forbidden_operation = {
        "pytest",
        "release_gate",
        "playwright",
        "historical_period",
        "crash_replay_drift",
        "final_nopublish_e2e",
    }
    if operation_id in forbidden_operation or any(
        token in {"pytest", "playwright", "historical", "historical_period", "crash_replay_drift", "final_nopublish_e2e", "news_grasp_release_gate"}
        for token in (command if isinstance(command, Sequence) and not isinstance(command, (str, bytes, bytearray)) else ())
    ):
        raise _operation_error("daily_release_only_operation", operation_id, command)
    expected = DAILY_OPERATION_COMMANDS.get(operation_id)
    if expected is None:
        raise _operation_error("daily_operation_unknown", operation_id, command)
    if isinstance(command, (str, bytes, bytearray)):
        raise _operation_error("daily_operation_argv_invalid", operation_id, command)
    try:
        observed = tuple(command)
    except TypeError as exc:
        raise _operation_error("daily_operation_argv_invalid", operation_id, command) from exc
    if any(not isinstance(item, str) for item in observed):
        raise _operation_error("daily_operation_argv_invalid", operation_id, command)
    if observed != expected:
        reason = "daily_release_only_operation" if any(
            token in {"pytest", "playwright", "historical", "historical_period", "crash_replay_drift", "final_nopublish_e2e", "news_grasp_release_gate"}
            for token in observed
        ) else "daily_operation_argv_mismatch"
        raise _operation_error(reason, operation_id, command)
    if capability is not None:
        if not isinstance(capability, Mapping):
            raise _operation_error("daily_route_capability_invalid", operation_id, command)
        required_generation = str(runtime_generation or capability.get("runtime_generation") or "").strip()
        if not required_generation:
            raise _operation_error("daily_runtime_generation_missing", operation_id, command)
        if capability.get("schemaVersion") != DAILY_ROUTE_SCHEMA:
            raise _operation_error("daily_route_capability_schema_invalid", operation_id, command)
        if str(capability.get("profile") or "") != DAILY_PROFILE.profile_id:
            raise _operation_error("daily_route_capability_profile_invalid", operation_id, command)
        if str(capability.get("operation_id") or "") != operation_id:
            raise _operation_error("daily_route_capability_operation_invalid", operation_id, command)
        if list(capability.get("argv") or ()) != list(expected):
            raise _operation_error("daily_route_capability_argv_invalid", operation_id, command)
        if str(capability.get("argv_sha256") or "") != _argv_digest(expected):
            raise _operation_error("daily_route_capability_digest_invalid", operation_id, command)
        if str(capability.get("runtime_generation") or "") != required_generation:
            raise _operation_error("daily_route_capability_generation_invalid", operation_id, command)
        if capability.get("capability") != "scheduled_production_daily":
            raise _operation_error("daily_route_capability_kind_invalid", operation_id, command)
    return {
        "schemaVersion": "NEWS_GRASP_DAILY_OPERATION_AUTHORIZATION_V1",
        "ok": True,
        "status": "authorized",
        "profile": DAILY_PROFILE.profile_id,
        "operation_id": operation_id,
        "operationId": operation_id,
        "argv": list(expected),
        "command": list(expected),
        "argv_sha256": _argv_digest(expected),
        "runtime_generation": str(runtime_generation or (capability or {}).get("runtime_generation") or ""),
        "route_capability": dict(capability) if isinstance(capability, Mapping) else None,
        "humanImpact": {
            "noFocusTheft": True,
            "noAutoOpen": True,
            "noUserMonitoring": True,
        },
    }


def claim_predicate_once(
    *,
    store: dict[str, Any],
    generation_id: str,
    predicate_id: str,
    owner: str,
    source_identity: str,
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """in-memory predicate ledger。

    generationとpredicateの組み合わせは一度だけ消費する。既存claimのownerが
    異なる場合はowner mismatch、同一ownerでも二回目はalready consumedとする。
    source/evidenceは最初のclaimへ束縛し、callerの上書きを許可しない。
    """

    values = {
        "generation_id": generation_id,
        "predicate_id": predicate_id,
        "owner": owner,
        "source_identity": source_identity,
    }
    if any(not isinstance(value, str) or not value.strip() for value in values.values()):
        raise NewsGraspGateProfileError("predicate_claim_binding_invalid")
    if not isinstance(evidence, Mapping):
        raise NewsGraspGateProfileError("predicate_claim_evidence_invalid")
    claims = store.setdefault("_predicate_claims", {})
    if not isinstance(claims, dict):
        raise NewsGraspGateProfileError("predicate_ledger_invalid")
    key = f"{generation_id}\x1f{predicate_id}"
    existing = claims.get(key)
    if existing is not None:
        if not isinstance(existing, Mapping):
            raise NewsGraspGateProfileError("predicate_ledger_invalid")
        if str(existing.get("owner") or "") != owner:
            raise NewsGraspGateProfileError("predicate_owner_mismatch")
        raise NewsGraspGateProfileError("predicate_already_consumed")
    claim = {
        "generation_id": generation_id,
        "predicate_id": predicate_id,
        "owner": owner,
        "source_identity": source_identity,
        "evidence": dict(evidence),
    }
    claims[key] = claim
    return {
        "schemaVersion": "NEWS_GRASP_PREDICATE_CLAIM_V1",
        "ok": True,
        "status": "claimed",
        **claim,
    }


def validate_profiles() -> dict[str, Any]:
    if set(DAILY_PROFILE.oracles) & set(RELEASE_PROFILE.oracles):
        raise NewsGraspGateProfileError("NG_GATE_ORACLE_OVERLAP")
    if RELEASE_PROFILE.reachable_from_scheduled:
        raise NewsGraspGateProfileError("NG_RELEASE_GATE_REACHABLE_FROM_DAILY")
    try:
        registry = json.loads(_DAILY_ROUTE_REGISTRY.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise NewsGraspGateProfileError("NG_DAILY_ROUTE_REGISTRY_INVALID") from exc
    if not isinstance(registry, Mapping):
        raise NewsGraspGateProfileError("NG_DAILY_ROUTE_REGISTRY_INVALID")
    expected_ids = list(DAILY_OPERATIONS)
    expected_routes = [
        {
            "routeId": operation_id,
            "consumerPath": "tools/news_grasp_daily_gate.py",
            "consumerSymbol": f"_default_{operation_id}",
            "productionCallerPath": "tools/news_grasp_daily_launcher.py",
            "productionCallSymbol": "run_sequence",
        }
        for operation_id in DAILY_OPERATIONS
    ]
    direct = registry.get("directMainline")
    if not isinstance(direct, Mapping):
        raise NewsGraspGateProfileError("NG_DAILY_ROUTE_REGISTRY_INVALID")
    for projection in (
        "declaredRouteIds",
        "consumerRouteIds",
        "positiveFixtureRouteIds",
        "negativeFixtureRouteIds",
    ):
        if registry.get(projection) != expected_ids or direct.get(projection) != expected_ids:
            raise NewsGraspGateProfileError(f"NG_DAILY_ROUTE_PARITY_INVALID:{projection}")
    if registry.get("routes") != expected_routes:
        raise NewsGraspGateProfileError("NG_DAILY_ROUTE_CONSUMER_BINDING_INVALID")
    direct_routes = direct.get("routes")
    if not isinstance(direct_routes, list) or [
        {
            "routeId": row.get("routeId"),
            "consumerPath": row.get("consumerPath"),
            "consumerSymbol": row.get("consumerSymbol"),
        }
        for row in direct_routes
        if isinstance(row, Mapping)
    ] != [
        {
            "routeId": row["routeId"],
            "consumerPath": row["consumerPath"],
            "consumerSymbol": row["consumerSymbol"],
        }
        for row in expected_routes
    ]:
        raise NewsGraspGateProfileError("NG_DAILY_DIRECT_ROUTE_CONSUMER_BINDING_INVALID")
    for source_path in _DAILY_ENTRY_MODULES:
        try:
            parsed = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        except (OSError, UnicodeError, SyntaxError) as exc:
            raise NewsGraspGateProfileError("NG_DAILY_ENTRY_SOURCE_INVALID") from exc
        imports: set[str] = set()
        for node in ast.walk(parsed):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
                imports.update(f"{node.module}.{alias.name}" for alias in node.names)
        reached = sorted(
            forbidden
            for forbidden in _FORBIDDEN_DAILY_IMPORTS
            if forbidden in imports
        )
        if reached:
            raise NewsGraspGateProfileError(
                f"NG_RELEASE_GATE_REACHED_FROM_DAILY:{source_path.name}:{reached}"
            )
    return {
        "status": "validated",
        "daily": list(DAILY_PROFILE.oracles),
        "release": list(RELEASE_PROFILE.oracles),
        "dailyOperationIds": expected_ids,
        "routeRegistry": str(_DAILY_ROUTE_REGISTRY),
        "releaseImports": [],
    }


def evaluate_daily(results: Mapping[str, bool]) -> dict[str, Any]:
    missing = [oracle for oracle in DAILY_ORACLES if results.get(oracle) is not True]
    return {
        "profile": DAILY_PROFILE.profile_id,
        "status": "green" if not missing else "red",
        "missing": missing,
        "report_only": True,
        "completion_authority": "none",
    }


def evaluate_release(results: Mapping[str, bool]) -> dict[str, Any]:
    missing = [oracle for oracle in RELEASE_ORACLES if results.get(oracle) is not True]
    return {
        "profile": RELEASE_PROFILE.profile_id,
        "status": "green" if not missing else "red",
        "missing": missing,
        "report_only": True,
        "completion_authority": "none",
    }


def scheduled_call_graph(*, calls: list[str]) -> dict[str, Any]:
    forbidden = {
        "pytest", "playwright", "historical_period", "final_nopublish_e2e",
        "crash_replay_drift", "release_gate", "raw_python",
    }
    reached = sorted(forbidden.intersection(calls))
    unknown = sorted(set(calls) - set(DAILY_OPERATIONS))
    if reached or unknown:
        raise NewsGraspGateProfileError("NG_RELEASE_GATE_REACHED_FROM_DAILY")
    if list(calls) and list(calls) != list(DAILY_OPERATIONS[: len(calls)]):
        raise NewsGraspGateProfileError("NG_DAILY_ROUTE_ORDER_INVALID")
    return {"status": "green", "calls": calls, "releaseCalls": []}


# 実consumer名をstable contractとして公開する。
daily_gate = evaluate_daily
release_gate = evaluate_release
