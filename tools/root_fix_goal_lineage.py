from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any


REQUIRED_MAIN_REQUIREMENTS = {
    "R-PRODUCTION-SELF-HEAL",
    "R-AUDIT-RECOVERY-PRIORITY",
}


def _load_completion_gate() -> Any:
    tool_root = Path.home() / ".codex" / "tools"
    path = tool_root / "completion_authority_gate.py"
    if str(tool_root) not in sys.path:
        sys.path.insert(0, str(tool_root))
    spec = importlib.util.spec_from_file_location(
        "news_grasp_completion_authority_gate", path
    )
    if spec is None or spec.loader is None:
        raise ValueError("GOAL_LINEAGE_CONSUMER_MISSING")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("GOAL_LINEAGE_INPUT_INVALID") from error
    if not isinstance(value, dict):
        raise ValueError("GOAL_LINEAGE_INPUT_INVALID")
    return value


def validate_goal_lineage(
    *,
    transcript_path: Path,
    task_contract_path: Path,
    candidate_manifest: object,
) -> dict[str, Any]:
    gate = _load_completion_gate()
    actual = gate._actual_user_lineage_binding(transcript_path)
    task = gate._task_actual_user_lineage_binding(task_contract_path)
    contract = _load_json(task_contract_path)
    requirements = contract.get("requirement", {}).get("requirements")
    lineage = contract.get("continuationLineage")
    discovery = contract.get("hypothesisDrivenDiscovery")
    work_order = contract.get("currentWorkOrder")
    candidate_binding = contract.get("candidateBinding")
    expected_candidate_binding = (
        {
            "candidateTreeSha256": candidate_manifest.get("candidateTreeSha256"),
            "manifestBodySha256": candidate_manifest.get("manifestBodySha256"),
        }
        if isinstance(candidate_manifest, dict)
        else None
    )
    if (
        actual != task
        or not isinstance(requirements, list)
        or not isinstance(lineage, dict)
        or not isinstance(discovery, dict)
        or not isinstance(work_order, dict)
        or candidate_binding != expected_candidate_binding
    ):
        raise ValueError("GOAL_LINEAGE_MISMATCH")
    requirement_ids = {
        str(row.get("requirementId") or "")
        for row in requirements
        if isinstance(row, dict)
    }
    main_ids = set(lineage.get("mainDeliverableRequirementIds") or [])
    retained_ids = set(lineage.get("retainedRequirementIds") or [])
    events = discovery.get("interactionEvents")
    if (
        not REQUIRED_MAIN_REQUIREMENTS <= requirement_ids
        or not REQUIRED_MAIN_REQUIREMENTS <= main_ids
        or not main_ids <= retained_ids <= requirement_ids
        or not isinstance(events, list)
        or not events
    ):
        raise ValueError("PARENT_REQUIREMENT_RETENTION_INVALID")
    latest_event_sha = str(events[-1].get("eventSha256") or "")
    if (
        re.fullmatch(r"[0-9a-f]{64}", latest_event_sha) is None
        or work_order.get("derivedFromEventSha256") != latest_event_sha
        or set(work_order.get("requirementIds") or []) != requirement_ids
    ):
        raise ValueError("LATEST_USER_WORK_ORDER_NOT_RECALCULATED")
    return {
        "schemaVersion": "NEWS_GRASP_GOAL_LINEAGE_DECISION_V1",
        "parentRequirementRetention": True,
        "workOrderRecalculated": True,
        "latestActualUserEventBound": True,
        "mainDeliverableRequirementIds": sorted(main_ids),
        "latestActualUserEventSha256": latest_event_sha,
        **expected_candidate_binding,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transcript", type=Path, required=True)
    parser.add_argument("--task-contract", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        manifest = _load_json(args.manifest.resolve())
        result = validate_goal_lineage(
            transcript_path=args.transcript.resolve(),
            task_contract_path=args.task_contract.resolve(),
            candidate_manifest=manifest,
        )
        code = 0
    except (ValueError, RuntimeError) as error:
        result = {
            "schemaVersion": "NEWS_GRASP_GOAL_LINEAGE_DECISION_V1",
            "reason": str(error),
            "parentRequirementRetention": False,
            "workOrderRecalculated": False,
            "latestActualUserEventBound": False,
        }
        code = 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
