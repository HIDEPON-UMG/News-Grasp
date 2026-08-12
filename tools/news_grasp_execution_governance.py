"""News-Grasp実行のmodel・Usage・retry・progress・stopを一括判定する。"""

from __future__ import annotations

import json
import hashlib
import re
import sqlite3
from pathlib import Path
from typing import Any, Mapping


SCHEMA = "NEWS_GRASP_EXECUTION_GOVERNANCE_V1"
REQUEST_SCHEMA = "NEWS_GRASP_EXECUTION_GOVERNANCE_REQUEST_V1"
DECISION_SCHEMA = "NEWS_GRASP_EXECUTION_GOVERNANCE_DECISION_V1"
CONFIG_PATH = Path("config/news_grasp_execution_governance_v1.json")
_TODO_RE = re.compile(r"^TODO-\d{3}$")
_TODO_DISPLAY_RE = re.compile(
    r"^(?P<marker>[☑☐◉⊘■]) \[(?P<id>TODO-\d{3})\]"
    r"\[(?P<time>[^\]|]+)\|(?P<usage>\d+(?:\.\d+)?%)\] (?P<content>\S.*)$"
)
_STATUS_MARKERS = {
    "completed": "☑",
    "pending": "☐",
    "in_progress": "◉",
    "operation_deferred": "⊘",
    "terminal": "■",
}
_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")
CONSUMPTION_LEDGER_ROOT: Path | None = None
_RESTORATION_ORDER = (
    "durable_goal",
    "append_only_todo_ledger",
    "durable_delta_packet",
    "worktree",
)


def _load(repo_root: Path) -> dict[str, Any]:
    try:
        value = json.loads((repo_root / CONFIG_PATH).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("NEWS_GRASP_EXECUTION_GOVERNANCE_CONFIG_INVALID") from error
    if value.get("schemaVersion") != SCHEMA or value.get("productId") != "News-Grasp":
        raise ValueError("NEWS_GRASP_EXECUTION_GOVERNANCE_CONFIG_INVALID")
    phase_executors = value.get("phaseExecutors")
    expected = {
        "requirements_design": ("sol_max", "max"),
        "security_judgment": ("sol_max", "max"),
        "fixed_implementation": ("luna_max", "max"),
        "deterministic_verification": ("local_tool", "deterministic"),
    }
    if not isinstance(phase_executors, dict) or set(phase_executors) != set(expected):
        raise ValueError("NEWS_GRASP_EXECUTION_GOVERNANCE_CONFIG_INVALID")
    for phase, (executor, effort) in expected.items():
        if phase_executors[phase] != {
            "executor": executor,
            "reasoningEffort": effort,
        }:
            raise ValueError("NEWS_GRASP_EXECUTION_GOVERNANCE_CONFIG_INVALID")
    usage = value.get("weeklyUsage")
    if (
        not isinstance(usage, dict)
        or float(usage.get("limitPercent", -1)) != 8.8
        or usage.get("measurement") != "WP開始前に予定model消費から算定"
    ):
        raise ValueError("NEWS_GRASP_EXECUTION_GOVERNANCE_CONFIG_INVALID")
    if value.get("progressPolicy", {}).get("requiredDurableStates") != list(
        _RESTORATION_ORDER
    ):
        raise ValueError("NEWS_GRASP_EXECUTION_GOVERNANCE_CONFIG_INVALID")
    return value


def _progress(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("NEWS_GRASP_TODO_PROGRESS_INVALID")
    previous = tuple(str(item) for item in value.get("previousTodoIds", []))
    proposed = tuple(str(item) for item in value.get("proposedTodoIds", []))
    statuses = tuple(str(item) for item in value.get("statuses", []))
    entries = tuple(str(item) for item in value.get("todoEntries", []))
    if (
        not proposed
        or len(proposed) != len(statuses)
        or len(proposed) != len(entries)
        or len(proposed) != len(set(proposed))
        or any(not _TODO_RE.fullmatch(item) for item in proposed)
    ):
        raise ValueError("NEWS_GRASP_TODO_PROGRESS_INVALID")
    for todo_id, status, entry in zip(proposed, statuses, entries, strict=True):
        matched = _TODO_DISPLAY_RE.fullmatch(entry)
        if (
            matched is None
            or matched.group("id") != todo_id
            or _STATUS_MARKERS.get(status) != matched.group("marker")
        ):
            raise ValueError("NEWS_GRASP_TODO_DISPLAY_FORMAT_INVALID")
    if len(proposed) < len(previous) or proposed[: len(previous)] != previous:
        raise ValueError("NEWS_GRASP_TODO_PREFIX_DROPPED")
    if statuses.count("in_progress") != 1:
        raise ValueError("NEWS_GRASP_TODO_IN_PROGRESS_COUNT_INVALID")
    current = str(value.get("currentTodoId", ""))
    if current not in proposed or statuses[proposed.index(current)] != "in_progress":
        raise ValueError("NEWS_GRASP_TODO_CURRENT_INVALID")
    if value.get("durableGoalPresent") is not True:
        raise ValueError("NEWS_GRASP_DURABLE_GOAL_REQUIRED")
    if value.get("durableDeltaPacketPresent") is not True:
        raise ValueError("NEWS_GRASP_DURABLE_DELTA_REQUIRED")
    return {
        "todoCount": len(proposed),
        "currentTodoId": current,
        "todoEntries": list(entries),
        "restorationOrder": list(_RESTORATION_ORDER),
        "appendOnly": True,
    }


def consume_once(
    *,
    repo_root: Path,
    kind: str,
    key_parts: tuple[str, ...],
    ledger_root: Path | None = None,
) -> dict[str, Any]:
    """固定managed rootのSQLite一意制約で因果operationを一回だけ消費する。"""

    root = (
        Path(ledger_root)
        if ledger_root is not None
        else repo_root / "build" / "operational-state"
    ).resolve()
    root.mkdir(parents=True, exist_ok=True)
    ledger_path = root / "execution-governance-consumption-v1.sqlite3"
    key_sha256 = hashlib.sha256(
        json.dumps(
            {"kind": kind, "keyParts": list(key_parts)},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    connection = sqlite3.connect(str(ledger_path), timeout=30, isolation_level=None)
    try:
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS operation_consumption (
                kind TEXT NOT NULL,
                key_sha256 TEXT NOT NULL,
                consumed_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (kind, key_sha256)
            )
            """
        )
        try:
            connection.execute(
                "INSERT INTO operation_consumption(kind, key_sha256) VALUES (?, ?)",
                (kind, key_sha256),
            )
        except sqlite3.IntegrityError:
            connection.rollback()
            consumed = False
        else:
            connection.commit()
            consumed = True
    finally:
        connection.close()
    return {
        "consumed": consumed,
        "consumptionKeySha256": key_sha256,
        "ledgerPath": str(ledger_path),
    }


def _retry(
    value: object,
    *,
    user_stopped: bool,
    repo_root: Path,
    current_todo_id: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("NEWS_GRASP_RETRY_STATE_INVALID")
    previous = str(value.get("previousFingerprint", ""))
    current = str(value.get("currentFingerprint", ""))
    if not _FINGERPRINT_RE.fullmatch(previous) or not _FINGERPRINT_RE.fullmatch(current):
        raise ValueError("NEWS_GRASP_RETRY_FINGERPRINT_INVALID")
    if user_stopped:
        return {"allowed": False, "reasonCode": "USER_STOPPED"}
    if previous == current:
        return {"allowed": False, "reasonCode": "SAME_SHAPE_RETRY_FORBIDDEN"}
    if value.get("causeInputChanged") is not True:
        return {"allowed": False, "reasonCode": "CAUSAL_INPUT_UNCHANGED"}
    if value.get("retryConsumed") is True:
        return {"allowed": False, "reasonCode": "CAUSAL_RETRY_ALREADY_CONSUMED"}
    consumption = consume_once(
        repo_root=repo_root,
        ledger_root=CONSUMPTION_LEDGER_ROOT,
        kind="causal_retry",
        key_parts=(current_todo_id, previous, current),
    )
    if consumption["consumed"] is not True:
        return {
            "allowed": False,
            "reasonCode": "CAUSAL_RETRY_ALREADY_CONSUMED",
            "consumptionKeySha256": consumption["consumptionKeySha256"],
        }
    return {
        "allowed": True,
        "reasonCode": "CAUSAL_INPUT_CHANGED_ONE_SHOT",
        "consumptionKeySha256": consumption["consumptionKeySha256"],
    }


def _delegation(value: object, requested: bool) -> bool:
    if not isinstance(value, Mapping):
        raise ValueError("NEWS_GRASP_RESOURCE_VECTOR_INVALID")
    local = value.get("localOnly")
    delegated = value.get("withDelegation")
    if not isinstance(local, Mapping) or not isinstance(delegated, Mapping):
        raise ValueError("NEWS_GRASP_RESOURCE_VECTOR_INVALID")
    if not requested:
        return False
    if local.get("acceptanceComplete") is not True or delegated.get(
        "acceptanceComplete"
    ) is not True:
        return False
    try:
        return float(delegated["expectedTotalResource"]) < float(
            local["expectedTotalResource"]
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("NEWS_GRASP_RESOURCE_VECTOR_INVALID") from error


def evaluate(
    payload: Mapping[str, Any], *, repo_root: Path | str
) -> dict[str, Any]:
    if payload.get("schemaVersion") != REQUEST_SCHEMA:
        raise ValueError("NEWS_GRASP_EXECUTION_REQUEST_INVALID")
    root = Path(repo_root).resolve()
    config = _load(root)
    unresolved = tuple(str(item) for item in payload.get("unresolvedDecisionIds", []))
    if unresolved:
        raise ValueError("NEWS_GRASP_EXECUTION_UNRESOLVED_DECISION")
    phase = str(payload.get("taskPhase", ""))
    phase_contract = config["phaseExecutors"].get(phase)
    if not isinstance(phase_contract, dict):
        raise ValueError("NEWS_GRASP_EXECUTION_PHASE_INVALID")
    if (
        payload.get("requestedExecutor") != phase_contract["executor"]
        or payload.get("reasoningEffort") != phase_contract["reasoningEffort"]
    ):
        raise ValueError("NEWS_GRASP_EXECUTOR_ROLE_INVALID")
    try:
        current_usage = float(payload.get("weeklyUsagePercent"))
        planned_usage = float(payload.get("plannedUsagePercent"))
    except (TypeError, ValueError) as error:
        raise ValueError("NEWS_GRASP_WEEKLY_USAGE_INVALID") from error
    usage_after = round(current_usage + planned_usage, 4)
    if current_usage < 0 or planned_usage < 0 or usage_after > float(
        config["weeklyUsage"]["limitPercent"]
    ):
        raise ValueError("NEWS_GRASP_WEEKLY_USAGE_LIMIT_EXCEEDED")

    progress = _progress(payload.get("progress"))
    user_stopped = payload.get("operationEvent") == "user_stop"
    delegation_allowed = False
    if not user_stopped:
        delegation_allowed = _delegation(
            payload.get("candidateResources"),
            payload.get("delegationRequested") is True,
        )
    retry = _retry(
        payload.get("retry"),
        user_stopped=user_stopped,
        repo_root=root,
        current_todo_id=progress["currentTodoId"],
    )
    if user_stopped:
        return {
            "schemaVersion": DECISION_SCHEMA,
            "status": "terminal",
            "terminal": "user_stopped",
            "executor": phase_contract["executor"],
            "reasoningEffort": phase_contract["reasoningEffort"],
            "delegationAllowed": False,
            "weeklyUsageAfterPercent": usage_after,
            "progress": progress,
            "retry": retry,
        }
    return {
        "schemaVersion": DECISION_SCHEMA,
        "status": "admitted",
        "executor": phase_contract["executor"],
        "reasoningEffort": phase_contract["reasoningEffort"],
        "delegationAllowed": delegation_allowed,
        "weeklyUsageAfterPercent": usage_after,
        "usageLimitPercent": config["weeklyUsage"]["limitPercent"],
        "progress": progress,
        "retry": retry,
    }
