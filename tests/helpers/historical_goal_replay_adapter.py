from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


OFFICIAL_TRANSCRIPT = (
    Path.home()
    / ".codex"
    / "sessions"
    / "2026"
    / "08"
    / "02"
    / "rollout-2026-08-02T11-06-42-019fc039-3435-74b0-888e-9f2959cd4a8a.jsonl"
)
LATEST_TWO_PILLAR_MARKER = "異常終了で止まらずに自己修復する日次バッチ"


@dataclass(frozen=True)
class GoalCapture:
    line_number: int
    record_sha256: str
    objective: str
    status: str


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.strip().encode("utf-8"))


def _output_text(payload: dict[str, Any]) -> str:
    output = payload.get("output")
    if not isinstance(output, list):
        return ""
    return "".join(
        str(item.get("text") or "")
        for item in output
        if isinstance(item, dict)
    )


def _goal_captures() -> list[GoalCapture]:
    captures: list[GoalCapture] = []
    for line_number, raw in enumerate(
        OFFICIAL_TRANSCRIPT.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if '\\"objective\\"' not in raw:
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            continue
        payload = record.get("payload") or {}
        if payload.get("type") != "custom_tool_call_output":
            continue
        text = _output_text(payload)
        start = text.find('{"goal"')
        if start < 0:
            continue
        try:
            value = json.loads(text[start:])
        except json.JSONDecodeError:
            continue
        goal = value.get("goal") or {}
        objective = str(goal.get("objective") or "")
        if not objective:
            continue
        captures.append(
            GoalCapture(
                line_number=line_number,
                record_sha256=_sha256_text(raw),
                objective=objective,
                status=str(goal.get("status") or ""),
            )
        )
    unique: list[GoalCapture] = []
    seen: set[str] = set()
    for capture in captures:
        if capture.objective in seen:
            continue
        seen.add(capture.objective)
        unique.append(capture)
    return unique


def _latest_two_pillar_requirement() -> tuple[str, int, str]:
    for line_number, raw in enumerate(
        OFFICIAL_TRANSCRIPT.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if LATEST_TWO_PILLAR_MARKER not in raw:
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            continue
        payload = record.get("payload") or {}
        if payload.get("type") != "message" or payload.get("role") != "user":
            continue
        text = "".join(
            str(item.get("text") or item.get("text", ""))
            for item in payload.get("content") or []
            if isinstance(item, dict)
        )
        if LATEST_TWO_PILLAR_MARKER in text:
            return text, line_number, _sha256_text(raw)
    raise RuntimeError("OFFICIAL_TWO_PILLAR_USER_REQUIREMENT_MISSING")


def _select_current_capture(requirement_line: int) -> GoalCapture:
    candidates = [
        capture
        for capture in _goal_captures()
        if capture.line_number > requirement_line
        and _derive_pillars(capture.objective)
        == ["production_self_heal", "audit_recovery_priority"]
        and capture.status == "active"
    ]
    if not candidates:
        raise RuntimeError("CURRENT_TWO_PILLAR_DURABLE_GOAL_MISSING")
    return max(candidates, key=lambda item: item.line_number)


def _derive_pillars(objective: str) -> list[str]:
    def affirmed(marker: str) -> bool:
        index = objective.find(marker)
        if index < 0:
            return False
        suffix = objective[index + len(marker) : index + len(marker) + 24]
        return not any(
            negation in suffix
            for negation in (
                "ことを禁止",
                "のを禁止",
                "方針も採用しない",
                "方針を採用しない",
                "とはしない",
            )
        )

    pillars: list[str] = []
    if all(
        affirmed(marker)
        for marker in (
            "06:00 production daily batch",
            "回復可能な異常で処理を放棄せず当日public outcomeまで自己修復して完走する",
        )
    ):
        pillars.append("production_self_heal")
    if all(
        affirmed(marker)
        for marker in (
            "06:40 audit/recovery batch",
            "その復旧を報告、恒久対策、test、harness、incident polishより絶対に優先する",
            "deferredやreportをterminalにしない",
        )
    ):
        pillars.append("audit_recovery_priority")
    return pillars


def _semantic_claims(requirement: str, objective: str) -> list[dict[str, str]]:
    claims = [
        {
            "requirementId": "R-PRODUCTION-SELF-HEAL",
            "requirementEvidence": "異常終了で止まらずに自己修復する日次バッチ",
            "objectiveEvidence": (
                "回復可能な異常で処理を放棄せず当日public outcomeまで"
                "自己修復して完走する"
            ),
        },
        {
            "requirementId": "R-AUDIT-RECOVERY-PRIORITY",
            "requirementEvidence": "タスクの優先度を絶対に間違えない監査バッチ",
            "objectiveEvidence": (
                "その復旧を報告、恒久対策、test、harness、incident polishより"
                "絶対に優先する"
            ),
        },
    ]
    for claim in claims:
        if (
            claim["requirementEvidence"] not in requirement
            or claim["objectiveEvidence"] not in objective
        ):
            raise RuntimeError(
                f"GOAL_SEMANTIC_CLAIM_UNBOUND:{claim['requirementId']}"
            )
    return claims


def observe_historical_goal_replay(
    *, case_id: str, perspective: str
) -> dict[str, Any]:
    requirement, requirement_line, requirement_record_sha = (
        _latest_two_pillar_requirement()
    )
    capture = _select_current_capture(requirement_line)
    pillars = _derive_pillars(capture.objective)
    objective_sha = _sha256_text(capture.objective)
    requirement_ids = [
        "R-PRODUCTION-SELF-HEAL",
        "R-AUDIT-RECOVERY-PRIORITY",
    ]
    semantic_claims = _semantic_claims(requirement, capture.objective)
    binding_body = {
        "actualUserRecordSha256": requirement_record_sha,
        "objectiveSha256": objective_sha,
        "requirementIds": requirement_ids,
        "semanticClaims": semantic_claims,
    }
    binding = {
        **binding_body,
        "semanticBindingSha256": _sha256_bytes(
            json.dumps(
                binding_body,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ),
    }
    return {
        "schemaVersion": "OFFICIAL_GOAL_REPLAY_OBSERVATION_V1",
        "caseId": case_id,
        "perspective": perspective,
        "sourcePath": str(OFFICIAL_TRANSCRIPT),
        "sourceSha256": _sha256_bytes(OFFICIAL_TRANSCRIPT.read_bytes()),
        "consumerSources": [
            {
                "path": str(Path(__file__).resolve()),
                "symbol": "observe_historical_goal_replay",
            }
        ],
        "captureLine": capture.line_number,
        "captureRecordSha256": capture.record_sha256,
        "latestRequirementLine": requirement_line,
        "latestRequirementRecordSha256": requirement_record_sha,
        "returnCode": 0,
        "input": {
            "capture": {
                "line": capture.line_number,
                "recordSha256": capture.record_sha256,
                "objective": capture.objective,
                "status": capture.status,
            },
            "latestRequirement": {
                "line": requirement_line,
                "recordSha256": requirement_record_sha,
                "text": requirement,
            },
        },
        "result": {
            "goal": {
                "objective": capture.objective,
                "status": capture.status,
                "objectiveHash": objective_sha,
                "requirementBinding": binding,
            },
            "latestActualUserRequirement": requirement,
            "latestActualUserRequirementHash": _sha256_text(requirement),
            "latestActualUserEventRecordSha256": requirement_record_sha,
            "pillars": pillars,
            "productionPillar": "production_self_heal" in pillars,
            "auditPillar": "audit_recovery_priority" in pillars,
            "twoPillarCompleteness": len(pillars) == 2,
        },
    }
