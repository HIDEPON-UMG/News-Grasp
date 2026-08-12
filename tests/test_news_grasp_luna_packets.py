from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

import pytest

from tools.news_grasp_task_packet import validate_packet


ROOT = Path(__file__).resolve().parents[1]
PACKET_SET_PATH = ROOT / "config" / "news_grasp_luna_packets_v1.json"
EXPECTED_TODOS = tuple(f"TODO-{number}" for number in range(187, 199)) + (
    "TODO-200",
    "TODO-202",
    "TODO-203",
)
EXPECTED_DEPENDENCIES = {
    **{f"TODO-{number}": [f"TODO-{number - 1}"] for number in range(187, 191)},
    "TODO-191": ["TODO-198"],
    "TODO-192": ["TODO-191"],
    "TODO-193": ["TODO-197"],
    "TODO-194": ["TODO-193"],
    "TODO-195": ["TODO-192"],
    "TODO-196": ["TODO-195"],
    "TODO-197": ["TODO-196"],
    "TODO-198": ["TODO-190"],
    "TODO-200": ["TODO-197"],
    "TODO-202": ["TODO-201"],
    "TODO-203": ["TODO-202"],
}
EXPECTED_RETURN_CONDITIONS = {
    "write_set_expansion",
    "new_constitution_principle",
    "unregistered_failure_class",
    "public_semantics_change",
    "unresolved_shared_owner",
    "second_e2e_request",
}


def _value() -> dict[str, Any]:
    return json.loads(PACKET_SET_PATH.read_text(encoding="utf-8"))


def _expect_code(operation: Callable[[], object], code: str) -> None:
    with pytest.raises(ValueError) as captured:
        operation()
    assert str(captured.value).split(":", 1)[0] == code


def _validate(packet: dict[str, Any]) -> object:
    try:
        return validate_packet(packet, repo_root=ROOT)
    except TypeError as error:
        if "repo_root" not in str(error):
            raise
        return validate_packet(packet)


def _current_packet(packet: dict[str, Any]) -> dict[str, Any]:
    current = copy.deepcopy(packet)
    current["targetSourceHashes"] = {
        relative: (
            hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
            if (ROOT / relative).is_file()
            else "ABSENT"
        )
        for relative in current["writeSet"]
    }
    return current


def test_luna_packets_primary_are_decision_complete_and_sequential() -> None:
    value = _value()
    assert value["schemaVersion"] == "NEWS_GRASP_LUNA_PACKET_SET_V1"
    assert tuple(packet["todoId"] for packet in value["packets"]) == EXPECTED_TODOS
    baseline = ROOT / value["baselineReceiptPath"]
    assert hashlib.sha256(baseline.read_bytes()).hexdigest() == value["baselineReceiptSha256"]

    for packet in value["packets"]:
        assert packet["dependencyIds"] == EXPECTED_DEPENDENCIES[packet["todoId"]]
        try:
            result = _validate(packet)
        except Exception as error:
            pytest.fail(
                f"LUNA_PACKET_DECISION_COMPLETE_FIELDS_UNENFORCED:"
                f"{packet['todoId']}:{type(error).__name__}:{error}",
                pytrace=False,
            )
        assert result.baseline_commit == value["baselineCommit"]
        assert result.constitution_clauses
        assert result.expected_outputs
        assert result.expected_verification_exit == 0
        assert result.human_impact["noFocusTheft"] is True
        assert result.human_impact["rawProcessTermination"] is False
        assert set(result.return_to_sol_conditions) == EXPECTED_RETURN_CONDITIONS
        assert result.unresolvedDecisionIds == ()


def test_luna_packets_use_canonical_v2_execution_contract() -> None:
    packets = _value()["packets"]
    assert {packet["schemaVersion"] for packet in packets} == {
        "LUNA_EXECUTION_PACKET_V2"
    }, "NGI_RED_LUNA_EXECUTION_PACKET_V2_IDENTITY_DRIFT"

    obsolete_v3 = copy.deepcopy(packets[0])
    obsolete_v3["schemaVersion"] = "LUNA_EXECUTION_PACKET_V3"
    _expect_code(
        lambda: _validate(obsolete_v3),
        "LUNA_PACKET_SCHEMA_VERSION_INVALID",
    )


def test_luna_packet_v2_compatibility_accepts_verification_only_empty_write_set() -> None:
    """canonical V2のstrict packetでも検証専用packetだけはwrite不要である。"""
    for packet in _value()["packets"]:
        legacy = copy.deepcopy(packet)
        legacy["schemaVersion"] = "LUNA_EXECUTION_PACKET_V2"
        try:
            result = validate_packet(legacy, repo_root=ROOT)
        except Exception as error:
            pytest.fail(
                "NGI_RED_LUNA_PACKET_V2_VERIFICATION_WRITE_SET_INCOMPATIBLE:"
                f"{packet['todoId']}:{type(error).__name__}:{error}",
                pytrace=False,
            )
        if packet["mutationMode"] == "verification_only":
            assert result.write_set == ()
        else:
            assert result.write_set


def test_luna_packets_boundary_reject_hash_scope_and_human_impact_drift() -> None:
    packets = _value()["packets"]

    bad_hash = copy.deepcopy(packets[0])
    first_path = bad_hash["writeSet"][0]
    bad_hash["targetSourceHashes"][first_path] = "0" * 64
    _expect_code(
        lambda: _validate(bad_hash),
        "LUNA_PACKET_TARGET_HASH_DRIFT",
    )

    bad_scope = copy.deepcopy(packets[0])
    bad_scope["writeSet"].append("tools/unplanned_source.py")
    _expect_code(
        lambda: _validate(bad_scope),
        "LUNA_PACKET_TARGET_HASH_SET_MISMATCH",
    )

    bad_human_impact = copy.deepcopy(packets[0])
    bad_human_impact["humanImpact"]["noFocusTheft"] = False
    _expect_code(
        lambda: _validate(bad_human_impact),
        "LUNA_PACKET_HUMAN_IMPACT_INVALID",
    )

    bad_verification = copy.deepcopy(packets[4])
    bad_verification["writeSet"] = ["tools/news_grasp_daily_control.py"]
    bad_verification["targetSourceHashes"] = {
        "tools/news_grasp_daily_control.py": hashlib.sha256(
            (ROOT / "tools/news_grasp_daily_control.py").read_bytes()
        ).hexdigest()
    }
    _expect_code(
        lambda: _validate(bad_verification),
        "LUNA_PACKET_VERIFICATION_WRITE_FORBIDDEN",
    )


def test_luna_packets_recovery_has_single_causal_return_and_design_escape() -> None:
    packet = _value()["packets"][0]

    same_shape = copy.deepcopy(packet)
    same_shape["causalRetryCondition"] = "always retry until Green"
    _expect_code(
        lambda: _validate(same_shape),
        "LUNA_PACKET_CAUSAL_RETRY_INVALID",
    )

    missing_escape = copy.deepcopy(packet)
    missing_escape["returnToSolConditions"].remove("write_set_expansion")
    _expect_code(
        lambda: _validate(missing_escape),
        "LUNA_PACKET_RETURN_TO_SOL_CONDITION_INVALID",
    )

    unresolved = copy.deepcopy(packet)
    unresolved["unresolvedDecisionIds"] = ["D-UNKNOWN"]
    _expect_code(
        lambda: _validate(unresolved),
        "LUNA_PACKET_UNRESOLVED_DECISION",
    )


def test_luna_packets_primary_consume_operational_improvement_binding() -> None:
    result = _validate(_current_packet(_value()["packets"][0]))
    assert len(result.task_constitution_admission_sha256) == 64


def test_luna_packets_boundary_reject_missing_operational_improvement_binding() -> None:
    packet = _current_packet(_value()["packets"][0])
    packet.pop("taskConstitutionBindingSha256", None)
    _expect_code(
        lambda: _validate(packet),
        "LUNA_PACKET_TASK_CONSTITUTION_BINDING_REQUIRED",
    )


def test_todo_196_primary_seals_complete_multi_root_write_scope() -> None:
    packet = next(
        item for item in _value()["packets"] if item["todoId"] == "TODO-196"
    )
    assert packet["packetSetSelfMutationPolicy"] == (
        "parent_goal_owned_canonical_metadata_excluded_from_target_hash"
    )
    assert set(packet["writeSet"]) == {
        "automation/skills/news-grasp-e2e-discipline/SKILL.md",
        "config/news_grasp_skill_binding_v1.json",
        "config/news_grasp_skill_cross_layer_graph_v1.json",
        "scripts/ops/install-news-grasp-ops.ps1",
        "tests/test_news_grasp_luna_packets.py",
        "tests/test_news_grasp_operational_skill_contract.py",
        "tests/test_news_grasp_skill_cross_layer_graph.py",
        "tests/test_operational_redesign_contract.py",
        "tests/test_operational_redesign_matrix.py",
        "tools/news_grasp_change_control.py",
        "tools/news_grasp_constitution.py",
        "tools/news_grasp_operational_contract.py",
        "tools/news_grasp_task_packet.py",
    }
    assert set(packet["derivedWriteSet"]) == {
        "AGENTS.md",
        "CLAUDE.md",
        "config/news_grasp_active_object_catalog_v1.json",
        "config/news_grasp_constitution_projection_v1.json",
        "config/news_grasp_spec_disposition_v1.json",
        "config/news_grasp_test_constitution_map_v1.json",
        "docs/specs/2026-08-12_news-grasp-product-constitution.html",
    }
    assert {
        authority["command"] for authority in packet["derivedWriteAuthorities"]
    } == {
        "python -m tools.news_grasp_constitution generate-active-catalog --repo-root .",
        "python -m tools.news_grasp_constitution generate-projections --repo-root .",
    }
    assert set(packet["sharedOwnerWriteSet"]) == {
        "inventory/allowlist.txt",
        "snapshot/ProjectFolders/docs/harness/capability_registry.json",
        "snapshot/ProjectFolders/docs/harness/reference.md",
        "snapshot/ProjectFolders/harness_mapping.md",
        "snapshot/ProjectFolders/tools/tests/test_news_grasp_operational_skill_integrity.py",
        "snapshot/codex/skills/news-grasp-e2e-discipline/SKILL.md",
        "snapshot/codex/skills/ops-codex-long-running-work/SKILL.md",
        "snapshot/codex/skills/ops-codex-long-running-work/agents/openai.yaml",
        "snapshot/codex/skills/ops-write-operational-plan/SKILL.md",
        "snapshot/codex/skills/ops-write-operational-plan/agents/openai.yaml",
    }


def test_todo_196_boundary_rejects_unowned_packet_set_mutation() -> None:
    packet = _current_packet(
        next(item for item in _value()["packets"] if item["todoId"] == "TODO-196")
    )
    packet.pop("packetSetSelfMutationPolicy", None)
    _expect_code(
        lambda: _validate(packet),
        "LUNA_PACKET_SELF_MUTATION_POLICY_REQUIRED",
    )


def test_todo_196_boundary_rejects_unsealed_derived_writes() -> None:
    packet = _current_packet(
        next(item for item in _value()["packets"] if item["todoId"] == "TODO-196")
    )
    packet["derivedWriteAuthorities"] = []
    _expect_code(
        lambda: _validate(packet),
        "LUNA_PACKET_DERIVED_WRITE_AUTHORITY_INVALID",
    )
