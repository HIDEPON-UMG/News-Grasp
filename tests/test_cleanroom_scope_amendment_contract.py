from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "news_grasp_cleanroom_scope_amendment_20260821_v1.json"
CATALOG_PATH = ROOT / "config" / "news_grasp_active_object_catalog_v1.json"

EXPECTED_TODO_IDS: list[str] = []
EXPECTED_REQUIREMENT_IDS: list[str] = []
EXPECTED_RETENTION_KEYS = {
    "retainedTodoIds",
    "state",
    "countsTowardCompleted",
}


def _config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _catalog_record() -> dict[str, Any]:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    records = [
        obj
        for obj in catalog["objects"]
        if obj.get("path") == "config/news_grasp_cleanroom_scope_amendment_20260821_v1.json"
    ]
    assert len(records) == 1, (
        "active object catalog must contain exactly one scope-amendment record; "
        f"found {len(records)}"
    )
    return records[0]


def _out_of_scope_entries() -> list[dict[str, Any]]:
    entries = _config()["outOfScopeApproved"]
    assert isinstance(entries, list), "outOfScopeApproved must be a list"
    assert all(isinstance(entry, dict) for entry in entries), (
        "outOfScopeApproved entries must be objects"
    )
    return entries


def test_scope_amendment_completion_denominator_preserves_original_todo_count() -> None:
    enforcement = _config()["enforcement"]
    assert enforcement["completionDenominator"] == 20


def test_scope_amendment_out_of_scope_todo_ids_are_exact_and_ordered() -> None:
    actual = [entry["todoId"] for entry in _out_of_scope_entries()]
    assert actual == EXPECTED_TODO_IDS, (
        "outOfScopeApproved.todoId must be the exact ordered B-task set "
        f"{EXPECTED_TODO_IDS!r}; got {actual!r}"
    )


def test_scope_amendment_out_of_scope_requirement_ids_are_exact_and_ordered() -> None:
    actual = [entry["requirement_id"] for entry in _out_of_scope_entries()]
    assert actual == EXPECTED_REQUIREMENT_IDS, (
        "outOfScopeApproved.requirement_id must be the exact ordered requirement set "
        f"{EXPECTED_REQUIREMENT_IDS!r}; got {actual!r}"
    )


def test_scope_amendment_does_not_put_any_no20_todo_in_out_of_scope() -> None:
    todo_ids = [entry["todoId"] for entry in _out_of_scope_entries()]
    no20_ids = [todo_id for todo_id in todo_ids if todo_id.startswith("No.20/")]
    assert no20_ids == [], f"No.20 must remain natural L9, but found {no20_ids!r}"


def test_scope_amendment_retains_b_tasks_without_completed_contribution() -> None:
    retention = _config()["enforcement"]["todoRetention"]
    assert isinstance(retention, dict), "todoRetention must explicitly encode retention semantics"
    assert set(retention) == EXPECTED_RETENTION_KEYS, (
        "todoRetention must expose only the machine-checkable retention contract keys; "
        f"got {sorted(retention)!r}"
    )
    assert retention["retainedTodoIds"] == EXPECTED_TODO_IDS
    assert retention["state"] == "out_of_scope_approved"
    assert retention["countsTowardCompleted"] is False


def test_scope_amendment_separates_natural_l9_and_completed_count() -> None:
    enforcement = _config()["enforcement"]
    assert enforcement["outOfScopeCompletedCountContribution"] == 0
    assert enforcement["naturalL9TodoId"] == "No.20/A-20"


def test_scope_amendment_preserves_existing_write_policy() -> None:
    enforcement = _config()["enforcement"]
    assert enforcement["globalHarnessWritesAllowed"] is False
    assert enforcement["newsGraspProductWritesAllowed"] is True


def test_scope_amendment_catalog_hash_matches_config_bytes() -> None:
    actual_hash = hashlib.sha256(CONFIG_PATH.read_bytes()).hexdigest()
    assert _catalog_record()["sha256"] == actual_hash
