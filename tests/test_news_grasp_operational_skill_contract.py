from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tools import news_grasp_operational_contract as operational


ROOT = Path(__file__).resolve().parents[1]
E2E = ROOT / "automation" / "skills" / "news-grasp-e2e-discipline" / "SKILL.md"


def _task_constitution() -> dict[str, object]:
    graph = json.loads(
        (ROOT / "config" / "news_grasp_skill_cross_layer_graph_v1.json").read_text(
            encoding="utf-8"
        )
    )
    row = next(
        item for item in graph["skills"] if item["skillId"] == "ops-write-operational-plan"
    )
    return {
        "schemaVersion": "NEWS_GRASP_TASK_CONSTITUTION_REQUEST_V2",
        "taskId": "TODO-196",
        "durableGoalId": "b3c2f6bd-e729-58bd-9dfd-6c1d19bbe3d0",
        "todoDefinitionSetSha256": "a" * 64,
        "reviewPolicy": "no_additional_review",
        "reviewAttemptCount": 0,
        "clauseIds": row["clauseIds"],
        "requirementIds": ["R08"],
        "acceptanceIds": ["A08"],
        "writeSet": ["tools/news_grasp_change_control.py"],
        "skillIds": [row["skillId"]],
        "purposeIds": row["purposeIds"],
        "flowIds": row["flowIds"],
        "taskIds": row["taskIds"],
        "consumerRoutes": row["consumerRoutes"],
        "stateIds": row["stateIds"],
        "evidenceIds": row["evidenceIds"],
        "efficiencyCandidates": [
            {
                "candidateId": "reuse-single-consumer",
                "goalFidelity": True,
                "safetyComplete": True,
                "expectedTotalResource": 10.0,
                "resourceVector": {
                    "modelCalls": 0,
                    "toolCalls": 4,
                    "expectedRetries": 0,
                    "broadRegressions": 0,
                    "e2eAttempts": 0,
                    "humanOperations": 0,
                    "wallClockMinutes": 20,
                },
            },
            {
                "candidateId": "new-review-and-gate-series",
                "goalFidelity": True,
                "safetyComplete": True,
                "expectedTotalResource": 24.0,
                "resourceVector": {
                    "modelCalls": 2,
                    "toolCalls": 10,
                    "expectedRetries": 1,
                    "broadRegressions": 1,
                    "e2eAttempts": 0,
                    "humanOperations": 0,
                    "wallClockMinutes": 90,
                },
            },
        ],
        "selectedCandidateId": "reuse-single-consumer",
        "unresolvedDecisionIds": [],
    }


def test_product_e2e_overlay_keeps_constitution_and_composition_contract() -> None:
    text = E2E.read_text(encoding="utf-8-sig")
    for marker in (
        "Product Constitution",
        "NEWS_GRASP_INSTALLED_NOPUBLISH_LAUNCH_AUTHORITY_V1",
        "externalHealthAuthorityFixturePath",
        "externalHealthAuthorityFixtureSha256",
        "-ExternalHealthAuthorityPathOverride",
        "official wrapper→installed launcher→runner→broker",
        "claim witnessはcanonical file path",
        "追加review seriesを開始しない",
    ):
        assert marker in text, f"NEWS_GRASP_PRODUCT_SKILL_CONTRACT_MISSING:{marker}"


def test_operational_improvement_primary_binds_five_layers_and_efficiency() -> None:
    result = operational.admit_task_constitution(_task_constitution(), repo_root=ROOT)
    assert result["schemaVersion"] == "NEWS_GRASP_TASK_CONSTITUTION_ADMISSION_V1"
    assert result["skillIds"] == ("ops-write-operational-plan",)
    assert result["flowIds"] == ("flow-requirement-to-task-packet",)
    assert result["consumerRoutes"] == ("tools/news_grasp_task_packet.py",)
    assert result["selectedCandidateId"] == "reuse-single-consumer"
    assert result["reviewPolicy"] == "no_additional_review"


def test_operational_improvement_boundary_rejects_self_declared_layer_binding() -> None:
    payload = copy.deepcopy(_task_constitution())
    payload["consumerRoutes"] = ["tools/news_grasp_change_control.py"]
    with pytest.raises(ValueError, match="NEWS_GRASP_TASK_LAYER_BINDING_MISMATCH"):
        operational.admit_task_constitution(payload, repo_root=ROOT)


def test_operational_improvement_recovery_rejects_wasteful_selected_route() -> None:
    payload = copy.deepcopy(_task_constitution())
    payload["selectedCandidateId"] = "new-review-and-gate-series"
    with pytest.raises(ValueError, match="NEWS_GRASP_TASK_EFFICIENCY_SELECTION_INVALID"):
        operational.admit_task_constitution(payload, repo_root=ROOT)
