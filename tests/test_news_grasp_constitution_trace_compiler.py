from __future__ import annotations

import copy

import pytest

from tools import news_grasp_constitution as constitution


ROOT = constitution.ROOT


def test_trace_compiler_primary_binds_user_outcome_requirement_acceptance() -> None:
    compiled = constitution.compile_constitution(ROOT)

    assert "compiledTraceGraph" in compiled, "NGC_RED_COMPILED_TRACE_GRAPH_MISSING"
    graph = compiled["compiledTraceGraph"]
    assert graph["requirementIds"] == [f"R{number:02d}" for number in range(1, 24)]
    assert graph["acceptanceIds"] == [f"A{number:02d}" for number in range(1, 24)]
    assert graph["orphanNodeIds"] == []
    assert graph["duplicateEdgeIds"] == []
    assert graph["physicalDeliveryNodeId"] == "physical-delivery"

    mermaid = constitution._mermaid_sources(compiled)
    assert [row["id"] for row in mermaid] == [
        "constitution-map",
        "trace-map",
        "skill-map",
    ]
    assert graph["edgeSetSha256"] in {row["sourceSha256"] for row in mermaid}


def test_trace_compiler_boundary_rejects_missing_user_outcome() -> None:
    value = copy.deepcopy(constitution.load_constitution(ROOT))
    value["pillars"][0]["userOutcome"] = ""

    try:
        constitution.validate_constitution(value)
    except ValueError as exc:
        assert str(exc) == "CONSTITUTION_USER_OUTCOME_INVALID"
    else:
        pytest.fail("NGC_RED_USER_OUTCOME_NOT_VALIDATED")


def test_trace_compiler_recovery_rejects_requirement_reuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = constitution.compile_constitution(ROOT)["trace"]["acceptanceBindings"][0]
    duplicate_requirement = copy.deepcopy(base)
    duplicate_requirement.update(
        {
            "acceptanceId": "A99",
            "todoId": "TODO-179",
            "stateId": "duplicate_requirement_state",
            "recoveryId": "reject_duplicate_requirement",
            "evidenceId": "duplicate_requirement_evidence",
            "testNodeIds": [
                "test_duplicate_requirement_primary",
                "test_duplicate_requirement_boundary",
                "test_duplicate_requirement_recovery",
            ],
        }
    )
    monkeypatch.setattr(
        constitution,
        "_extension_acceptance_bindings",
        lambda _root: [duplicate_requirement],
    )

    try:
        constitution.compile_constitution(ROOT)
    except ValueError as exc:
        assert str(exc) == "CONSTITUTION_REQUIREMENT_ACCEPTANCE_NOT_UNIQUE"
    else:
        pytest.fail("NGC_RED_REQUIREMENT_ACCEPTANCE_ALIAS_NOT_REJECTED")
