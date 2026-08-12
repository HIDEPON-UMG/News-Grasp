from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tools import news_grasp_constitution as constitution


ROOT = Path(__file__).resolve().parents[1]


def test_skill_cross_layer_graph_primary_binds_all_loaded_skills() -> None:
    compiled = constitution.compile_constitution(ROOT)

    assert "skillCrossLayerGraph" in compiled, "NGC_RED_SKILL_GRAPH_MISSING"
    graph = compiled["skillCrossLayerGraph"]
    assert graph["schemaVersion"] == "NEWS_GRASP_SKILL_CROSS_LAYER_GRAPH_V1"
    assert len(graph["skills"]) == 11
    assert graph["orphanSkillIds"] == []
    assert graph["cycleSkillIds"] == []
    assert graph["duplicateStateOwnerIds"] == []


def test_skill_cross_layer_graph_boundary_rejects_stale_shared_skill_hash() -> None:
    binding = json.loads(
        (ROOT / "config" / "news_grasp_skill_binding_v1.json").read_text(
            encoding="utf-8"
        )
    )
    stale = copy.deepcopy(binding)
    target = next(
        row
        for row in stale["skills"]
        if row["skillId"] == "ops-sdd-tdd-harness-governance"
    )
    target["sourceSha256"] = "0" * 64

    try:
        constitution.validate_skill_binding(
            stale,
            ROOT,
            verify_shared_sources=False,
            skill_owner_root=ROOT,
        )
    except ValueError as exc:
        assert str(exc) == "CONSTITUTION_SKILL_OWNER_SOURCE_HASH_DRIFT"
    else:
        pytest.fail("NGC_RED_SHARED_SKILL_HASH_NOT_VERIFIED")


def test_skill_cross_layer_graph_recovery_rejects_cycle_or_duplicate_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relative = Path(
        "tests/fixtures/constitutional-operations/"
        "skill-cross-layer-cycle-invalid-v1.json"
    )
    monkeypatch.setattr(
        constitution,
        "SKILL_CROSS_LAYER_GRAPH_RELATIVE_PATH",
        relative,
        raising=False,
    )

    try:
        constitution.compile_constitution(ROOT)
    except ValueError as exc:
        assert str(exc) in {
            "CONSTITUTION_SKILL_GRAPH_CYCLE_OR_OWNER_INVALID",
            "CONSTITUTION_SKILL_GRAPH_CYCLE_INVALID",
            "CONSTITUTION_SKILL_GRAPH_DUPLICATE_STATE_OWNER",
        }
    else:
        pytest.fail("NGC_RED_SKILL_GRAPH_CYCLE_NOT_REJECTED")


def _graph_inputs() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    compiled = constitution.compile_constitution(ROOT)
    graph = json.loads(
        (ROOT / "config" / "news_grasp_skill_cross_layer_graph_v1.json").read_text(
            encoding="utf-8"
        )
    )
    binding = json.loads(
        (ROOT / "config" / "news_grasp_skill_binding_v1.json").read_text(
            encoding="utf-8"
        )
    )
    return graph, compiled["constitution"], binding


def test_skill_cross_layer_graph_boundary_rejects_unproven_consumer_symbol() -> None:
    graph, source, binding = _graph_inputs()
    invalid = copy.deepcopy(graph)
    target = next(
        row for row in invalid["skills"] if row["skillId"] == "ops-write-operational-plan"
    )
    target["consumerEvidenceByRoute"] = {
        "tools/news_grasp_task_packet.py": ["MISSING_CONSUMER_SYMBOL_FOR_RED"]
    }

    with pytest.raises(
        ValueError, match="CONSTITUTION_SKILL_GRAPH_CONSUMER_EVIDENCE_INVALID"
    ):
        constitution.validate_skill_cross_layer_graph(invalid, ROOT, source, binding)


def test_skill_cross_layer_graph_recovery_rejects_unproven_skill_purpose() -> None:
    graph, source, binding = _graph_inputs()
    invalid = copy.deepcopy(graph)
    target = next(
        row for row in invalid["skills"] if row["skillId"] == "news-grasp-e2e-discipline"
    )
    target["sourceEvidence"] = ["MISSING_SKILL_PURPOSE_FOR_RED"]

    with pytest.raises(
        ValueError, match="CONSTITUTION_SKILL_GRAPH_SOURCE_EVIDENCE_INVALID"
    ):
        constitution.validate_skill_cross_layer_graph(invalid, ROOT, source, binding)
