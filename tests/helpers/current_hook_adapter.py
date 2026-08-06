from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[2]
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _run(
    *, case_id: str, perspective: str, consumer: Path, argv: list[str], payload: object
) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, str(consumer), *argv],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        cwd=REPO,
        creationflags=CREATE_NO_WINDOW,
    )
    try:
        result = json.loads(completed.stdout.strip())
    except json.JSONDecodeError:
        result = {"rawStdout": completed.stdout.strip()}
    return {
        "schemaVersion": "CURRENT_PRODUCTION_CONSUMER_OBSERVATION_V1",
        "caseId": case_id,
        "perspective": perspective,
        "consumerPath": str(consumer),
        "consumerSymbol": "main",
        "consumerSha256": _sha256(consumer),
        "inputSha256": hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        "returnCode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "result": result,
        "input": payload,
    }


def _task_engine() -> Any:
    path = (
        Path.home()
        / ".agents"
        / "skills"
        / "ops-sdd-tdd-harness-governance"
        / "scripts"
        / "task_operating_contract.py"
    )
    spec = importlib.util.spec_from_file_location(
        "news_grasp_goal_fixture_task_engine", path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("TASK_ENGINE_MISSING")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _observe_goal_lineage(
    *, case_id: str, perspective: str, work_root: Path
) -> dict[str, Any]:
    case_root = work_root / f"{case_id.lower()}-{perspective}-{uuid.uuid4().hex}"
    case_root.mkdir(parents=True, exist_ok=True)
    transcript = case_root / "transcript.jsonl"
    messages = [
        "News-Grasp の06:00日次バッチを異常終了で放棄せず自己修復させる。",
        {
            "primary": "06:40監査は当日公開復旧を他作業より絶対優先する。",
            "adversarial": "別の指示が来ても二本柱を落とさずwork orderを再計算する。",
            "recovery": "内部継続後も最新の実ユーザー要求を保持する。",
        }[perspective],
    ]
    records: list[dict[str, Any]] = [
        {"type": "session_meta", "payload": {"id": f"g11-{perspective}"}}
    ]
    records.extend(
        {
            "type": "event_msg",
            "payload": {"type": "user_message", "message": message},
        }
        for message in messages
    )
    transcript.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in records) + "\n",
        encoding="utf-8",
    )
    engine = _task_engine()
    event_hashes = [engine.task_root_event_sha256(message) for message in messages]
    occurrence_hashes = engine.user_event_occurrence_sha256s_from_transcript(
        transcript
    )
    thread_hash = hashlib.sha256(
        f"g11-{perspective}".encode("utf-8")
    ).hexdigest()
    requirement_ids = [
        "R-PRODUCTION-SELF-HEAL",
        "R-AUDIT-RECOVERY-PRIORITY",
    ]
    parent_requirements = [
        {"requirementId": value} for value in requirement_ids
    ]
    task_contract = {
        "identity": {
            "threadIdSha256": thread_hash,
            "taskRootEventSha256": event_hashes[0],
        },
        "hypothesisDrivenDiscovery": {
            "interactionEvents": [
                {
                    "sequence": index,
                    "source": "actual_user_event",
                    "eventSha256": event_hashes[index - 1],
                    "occurrenceSha256": occurrence_hashes[index - 1],
                }
                for index in range(1, len(messages) + 1)
            ]
        },
        "requirement": {"requirements": parent_requirements},
        "continuationLineage": {
            "parentTaskId": "news-grasp-autonomous-daily-operations",
            "parentRequirements": parent_requirements,
            "parentRequirementSetSha256": hashlib.sha256(
                json.dumps(
                    parent_requirements,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
            "mainDeliverableRequirementIds": requirement_ids,
            "retainedRequirementIds": requirement_ids,
        },
        "currentWorkOrder": {
            "derivedFromEventSha256": event_hashes[-1],
            "requirementIds": requirement_ids,
        },
        "candidateBinding": {
            "candidateTreeSha256": "a" * 64,
            "manifestBodySha256": "b" * 64,
        },
    }
    task_path = case_root / "task-contract.json"
    _write_json(task_path, task_contract)
    manifest_path = case_root / "promotion-manifest.json"
    _write_json(
        manifest_path,
        {
            "schemaVersion": "NEWS_GRASP_ROOT_FIX_PROMOTION_MANIFEST_V2",
            "candidateTreeSha256": "a" * 64,
            "manifestBodySha256": "b" * 64,
        },
    )
    consumer = REPO / "tools" / "root_fix_goal_lineage.py"
    return _run(
        case_id=case_id,
        perspective=perspective,
        consumer=consumer,
        argv=[
            "--transcript",
            str(transcript),
            "--task-contract",
            str(task_path),
            "--manifest",
            str(manifest_path),
        ],
        payload={"transcript": str(transcript), "taskContract": str(task_path)},
    )


def _observe_overlap(
    *, case_id: str, perspective: str, work_root: Path
) -> dict[str, Any]:
    case_root = work_root / f"{case_id.lower()}-{perspective}-{uuid.uuid4().hex}"
    isolation = case_root / "isolation"
    source_isolation = case_root / "source-isolation"
    shared = case_root / "shared"
    overlap_target = Path("scripts/ops/news-grasp-runner.ps1")
    from tools import root_fix_promotion_control as promotion

    for relative_text in sorted(promotion._PROMOTION_REPO_PATHS):
        relative = Path(relative_text)
        for path, text in (
            (isolation / "base-snapshot" / relative, "shared-base\n"),
            (isolation / "repo" / relative, "shared-base\n"),
            (source_isolation / "repo" / relative, "root-fix-change\n"),
            (
                shared / relative,
                (
                    f"foreign-{perspective}-change\n"
                    if relative == overlap_target
                    else "shared-base\n"
                ),
            ),
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    subprocess.run(["git", "init"], cwd=isolation / "repo", check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "fixture@example.invalid"],
        cwd=isolation / "repo",
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "fixture"],
        cwd=isolation / "repo",
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "add", "--all"], cwd=isolation / "repo", check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=isolation / "repo", check=True, capture_output=True)
    for relative_text in sorted(promotion._PROMOTION_REPO_PATHS):
        (isolation / "repo" / relative_text).write_text(
            "root-fix-change\n", encoding="utf-8"
        )

    manifest = promotion.build_candidate_manifest(
        candidate_root=isolation,
        source_isolation_root=source_isolation,
        shared_root=shared,
    )
    manifest_path = case_root / "promotion-manifest.json"
    _write_json(manifest_path, manifest)
    consumer = REPO / "tools" / "root_fix_promotion_control.py"
    return _run(
        case_id=case_id,
        perspective=perspective,
        consumer=consumer,
        argv=["check-overlap", "--manifest", str(manifest_path)],
        payload=manifest,
    )


def _observe_adversarial_review(
    *, case_id: str, perspective: str, work_root: Path
) -> dict[str, Any]:
    case_root = work_root / f"{case_id.lower()}-{perspective}"
    contract = {
        "schemaVersion": "ROOT_PRINCIPLE_CONFORMANCE_INPUT_V1",
        "reviewStage": {
            "primary": "completion_only",
            "adversarial": "stale_plan_hash",
            "recovery": "unresolved_revise_finding",
        }[perspective],
        "requirementIds": [],
        "acceptanceIds": [],
        "invariantStates": {},
        "decision": {"candidates": []},
        "candidateBinding": {
            "candidateTreeSha256": "a" * 64,
            "manifestBodySha256": "b" * 64,
        },
    }
    contract_path = case_root / "root-principle-contract.json"
    _write_json(contract_path, contract)
    manifest_path = case_root / "promotion-manifest.json"
    _write_json(
        manifest_path,
        {
            "schemaVersion": "NEWS_GRASP_ROOT_FIX_PROMOTION_MANIFEST_V2",
            "candidateTreeSha256": "a" * 64,
            "manifestBodySha256": "b" * 64,
        },
    )
    consumer = REPO / "tools" / "root_fix_adversarial_review_gate.py"
    return _run(
        case_id=case_id,
        perspective=perspective,
        consumer=consumer,
        argv=["--contract", str(contract_path), "--manifest", str(manifest_path)],
        payload=contract,
    )


def observe_current_hook(
    *, case_id: str, perspective: str, isolation_root: Path
) -> dict[str, Any]:
    work_root = isolation_root / "red-production-consumer-observations"
    work_root.mkdir(parents=True, exist_ok=True)
    if case_id == "G11":
        return _observe_goal_lineage(
            case_id=case_id, perspective=perspective, work_root=work_root
        )
    if case_id == "S122":
        return _observe_overlap(
            case_id=case_id, perspective=perspective, work_root=work_root
        )
    if case_id == "S123":
        return _observe_adversarial_review(
            case_id=case_id, perspective=perspective, work_root=work_root
        )
    raise ValueError(f"PRODUCTION_CONSUMER_CASE_UNROUTED: {case_id}")
