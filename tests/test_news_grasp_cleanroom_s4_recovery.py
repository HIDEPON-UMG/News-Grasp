"""S4 clean-room recovery plane のsealed Expected Red suite。"""

from __future__ import annotations

from datetime import datetime
import hashlib
import importlib
import inspect
import json
from pathlib import Path
import sqlite3
from typing import Any
from zoneinfo import ZoneInfo

import pytest


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "news_grasp_cleanroom_s4_cases.json"
S1_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "news_grasp_cleanroom_s1_cases.json"
TOKYO = ZoneInfo("Asia/Tokyo")


def _sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _at(hour: int, minute: int) -> datetime:
    return datetime(2026, 8, 21, hour, minute, tzinfo=TOKYO)


def _cases() -> dict[str, Any]:
    value = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert value["schemaVersion"] == "NEWS_GRASP_CLEANROOM_S4_CASES_V1"
    assert value["packetId"] == "NG-CLEANROOM-S4-RED-V1"
    assert value["lineages"] == ["Scheduled", "Recovery", "Public", "Readiness"]
    return value


def _s1_runtime(tmp_path: Path, index: int) -> Path:
    root = tmp_path / f"日本語-回復面-{index}"
    root.mkdir(parents=True)
    s1 = json.loads(S1_FIXTURE_PATH.read_text(encoding="utf-8"))
    manifest = {
        "schemaVersion": s1["normative"]["manifest"]["schemaVersion"],
        "scheduleId": s1["normative"]["manifest"]["scheduleId"],
        "tasks": [s1["normative"]["manifest"]["task"]],
    }
    manifest_path = root.parent / f"manifest-{index}.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    controller = importlib.import_module("tools.news_grasp_cleanroom_controller").Controller(
        runtime_root=root,
        manifest_path=manifest_path,
    )
    writer = {
        "writerId": f"s4-test-{index}",
        "bootId": "s4-test-boot",
        "pid": 7000 + index,
        "processStartToken": f"s4-process-{index}",
    }
    controller.reconcile(
        raw_argv=s1["normative"]["rawArgv"]["exact"],
        observed_at=_at(6, 1),
        writer=writer,
    )
    audit_writer = {**writer, "writerId": f"s4-audit-{index}", "pid": 8000 + index}
    controller.reconcile(
        raw_argv=s1["normative"]["rawArgv"]["exact"],
        observed_at=_at(6, 41),
        writer=audit_writer,
    )
    ledger = importlib.import_module("tools.news_grasp_cleanroom_ledger").ControlLedger(root)
    ledger.commit_slot(
        slot_key=f'{s1["normative"]["manifest"]["scheduleId"]}/2026-08-21/Scheduled',
        writer=writer,
        fence_token=1,
        terminal_state="FAILED",
        result_hash=_sha({"issueDate": "2026-08-21", "slot": "Scheduled", "state": "FAILED"}),
        observed_at=_at(6, 42),
    )
    return root


def _slot(root: Path, kind: str) -> dict[str, Any]:
    with sqlite3.connect(root / "control" / "control-ledger-v1.sqlite3") as connection:
        row = connection.execute(
            "SELECT schedule_id,issue_date,slot_kind,generation,state,owner_key,fence_token,terminal_state,result_hash "
            "FROM slots WHERE slot_kind=?",
            (kind,),
        ).fetchone()
    if row is None:
        raise AssertionError(f"S1 {kind} slot is missing")
    return {
        "scheduleId": row[0],
        "issueDate": row[1],
        "slotKind": row[2],
        "generation": row[3],
        "state": row[4],
        "ownerKey": row[5],
        "fenceToken": row[6],
        "terminalState": row[7],
        "resultHash": row[8],
    }


def _parent(cases: dict[str, Any], scheduled: dict[str, Any]) -> dict[str, Any]:
    value = {
        "schemaVersion": "RECOVERY_PARENT_V1",
        "lineage": "Scheduled",
        "issueDate": cases["issueDate"],
        "scheduleId": cases["scheduleId"],
        "slotKey": cases["scheduledSlotKey"],
        "terminalState": "FAILED",
        "terminalHash": scheduled["resultHash"] or "s" * 64,
        "generation": scheduled["generation"],
    }
    value["parentSha256"] = _sha(value)
    return value


def _authority(cases: dict[str, Any], scheduled: dict[str, Any], audit: dict[str, Any]) -> dict[str, Any]:
    value = {
        "schemaVersion": "RECOVERY_AUTHORITY_V1",
        "authorityId": cases["authority"]["authorityId"],
        "issueDate": cases["issueDate"],
        "scheduledParentTerminalHash": scheduled["resultHash"] or "s" * 64,
        "scheduledGeneration": scheduled["generation"],
        "auditOwnerKey": audit["ownerKey"],
        "auditFenceToken": audit["fenceToken"],
        "maxAttempts": cases["authority"]["maxAttempts"],
    }
    value["authoritySha256"] = _sha(value)
    return value


def _budget(cases: dict[str, Any], authority: dict[str, Any], remaining: int = 1) -> dict[str, Any]:
    value = {
        "schemaVersion": "RECOVERY_BUDGET_V1",
        "authorityId": authority["authorityId"],
        "authoritySha256": authority["authoritySha256"],
        "remainingAttempts": remaining,
    }
    value["budgetSha256"] = _sha(value)
    return value


class Child:
    def __init__(self, name: str, *, terminal: str = "CONFIRMED") -> None:
        self.name = name
        self.terminal = terminal
        self.calls: list[dict[str, Any]] = []

    def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(dict(request))
        return {
            "schemaVersion": f"{self.name.upper()}_RECONCILE_RESULT_V1",
            "status": self.terminal,
            "lineage": self.name,
            "terminalHash": _sha({"name": self.name, "request": request}),
        }


def _controller(module: Any, root: Path, execution: Child, public: Child, legacy: Any = None, **kwargs: Any) -> Any:
    return module.RecoveryController(
        root,
        execution_reconciler=execution,
        public_reconciler=public,
        legacy_reader=legacy,
        **kwargs,
    )


def _audit(controller: Any, cases: dict[str, Any], parent: dict[str, Any], authority: dict[str, Any], budget: dict[str, Any]) -> Any:
    return controller.audit(
        issue_date=cases["issueDate"],
        parent=parent,
        authority=authority,
        budget=budget,
        observed_at=_at(6, 41),
    )


def _legacy_file(root: Path, case: str) -> Path:
    path = root / "legacy-v3.json"
    if case == "valid_v3":
        payload = {"schemaVersion": "LEGACY_RECOVERY_V3", "issueDate": "2026-08-21", "status": "FAILED"}
        payload["payloadSha256"] = _sha({key: value for key, value in payload.items() if key != "payloadSha256"})
        path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    elif case == "unknown_schema":
        path.write_text(json.dumps({"schemaVersion": "LEGACY_UNKNOWN_V9"}), encoding="utf-8")
    else:
        path.write_bytes(b"not-json")
    return path


def test_s4_recovery_authority_matrix(tmp_path: Path) -> None:
    module = importlib.import_module("tools.news_grasp_cleanroom_recovery")
    cases = _cases()
    root = _s1_runtime(tmp_path, 0)
    scheduled = _slot(root, "Scheduled")
    audit = _slot(root, "Audit")
    parent = _parent(cases, scheduled)
    authority = _authority(cases, scheduled, audit)
    valid_budget = _budget(cases, authority)
    execution = Child("execution")
    public = Child("public")
    result = _audit(_controller(module, root, execution, public), cases, parent, authority, valid_budget)
    assert result["schemaVersion"] == "RECOVERY_RECONCILE_RESULT_V1"
    assert len(execution.calls) == 1 and len(public.calls) == 1
    failures = {}
    for index, failure in enumerate(cases["authorityFailures"], start=1):
        failure_root = _s1_runtime(tmp_path, index)
        failure_scheduled = _slot(failure_root, "Scheduled")
        failure_audit = _slot(failure_root, "Audit")
        failure_parent = _parent(cases, failure_scheduled)
        failure_authority = _authority(cases, failure_scheduled, failure_audit)
        failure_budget = _budget(cases, failure_authority)
        if failure == "MALFORMED_AUTHORITY":
            failure_authority.pop("authoritySha256")
        elif failure == "WRONG_PARENT":
            failure_parent["terminalHash"] = "p" * 64
            failure_parent["parentSha256"] = _sha({key: value for key, value in failure_parent.items() if key != "parentSha256"})
        elif failure == "STALE_GENERATION":
            failure_authority["scheduledGeneration"] += 1
            failure_authority["authoritySha256"] = _sha({key: value for key, value in failure_authority.items() if key != "authoritySha256"})
        elif failure == "STALE_OWNER":
            failure_authority["auditOwnerKey"] = "stale-owner"
            failure_authority["authoritySha256"] = _sha({key: value for key, value in failure_authority.items() if key != "authoritySha256"})
        elif failure == "STALE_FENCE":
            failure_authority["auditFenceToken"] += 1
            failure_authority["authoritySha256"] = _sha({key: value for key, value in failure_authority.items() if key != "authoritySha256"})
        elif failure == "EXHAUSTED_BUDGET":
            failure_budget = _budget(cases, failure_authority, remaining=0)
        elif failure == "MISMATCHED_BUDGET":
            failure_budget["authoritySha256"] = "b" * 64
            failure_budget["budgetSha256"] = _sha({key: value for key, value in failure_budget.items() if key != "budgetSha256"})
        else:
            failure_parent["route"] = "unknown"
            failure_parent["parentSha256"] = _sha({key: value for key, value in failure_parent.items() if key != "parentSha256"})
        child_execution = Child("execution")
        child_public = Child("public")
        try:
            _audit(_controller(module, failure_root, child_execution, child_public), cases, failure_parent, failure_authority, failure_budget)
        except Exception as caught:
            failures[failure] = (getattr(caught, "reason", type(caught).__name__), len(child_execution.calls), len(child_public.calls))
        else:
            failures[failure] = ("returned", len(child_execution.calls), len(child_public.calls))
    reasons = [reason for reason, _, _ in failures.values()]
    assert all(reason.startswith("RECOVERY_") for reason in reasons), failures
    assert len(set(reasons)) == len(reasons), failures
    assert all(execution_calls == 0 and public_calls == 0 for _, execution_calls, public_calls in failures.values())


def test_s4_recovery_retry_preserves_lineage(tmp_path: Path) -> None:
    module = importlib.import_module("tools.news_grasp_cleanroom_recovery")
    cases = _cases()
    observations = []
    for index, boundary in enumerate(cases["crashBoundaries"], start=100):
        root = _s1_runtime(tmp_path, index)
        scheduled = _slot(root, "Scheduled")
        audit = _slot(root, "Audit")
        parent = _parent(cases, scheduled)
        authority = _authority(cases, scheduled, audit)
        budget = _budget(cases, authority)
        before = (parent["terminalHash"], (root / "control" / "control-ledger-v1.sqlite3").read_bytes())
        execution = Child("execution")
        public = Child("public")
        crashed = {"active": True}

        def hook(name: str) -> None:
            if crashed["active"] and name == boundary:
                crashed["active"] = False
                raise RuntimeError(f"test-owned recovery crash: {name}")

        first = _controller(module, root, execution, public, boundary_hook=hook)
        try:
            _audit(first, cases, parent, authority, budget)
        except Exception:
            pass
        second = _audit(_controller(module, root, execution, public), cases, parent, authority, budget)
        terminal_retry = _audit(_controller(module, root, execution, public), cases, parent, authority, budget)
        observations.append(
            {
                "boundary": boundary,
                "result": second,
                "terminalRetry": terminal_retry,
                "executionCalls": len(execution.calls),
                "publicCalls": len(public.calls),
                "before": before,
                "after": (parent["terminalHash"], (root / "control" / "control-ledger-v1.sqlite3").read_bytes()),
            }
        )
    assert all(item["result"]["recoveryId"] == item["terminalRetry"]["recoveryId"] for item in observations)
    assert len({item["result"]["recoveryId"] for item in observations}) == len(observations)
    assert all(item["executionCalls"] == 1 and item["publicCalls"] == 1 for item in observations)
    assert all(item["before"] == item["after"] for item in observations)
    assert all(item["result"]["attemptsUsed"] <= 1 for item in observations)
    assert all(item["result"]["recoveryHistory"][-1]["lineage"] == "Recovery" for item in observations)


def test_s4_legacy_writer_zero(tmp_path: Path) -> None:
    module = importlib.import_module("tools.news_grasp_cleanroom_recovery")
    cases = _cases()
    for index, legacy_case in enumerate(cases["legacyCases"], start=200):
        root = _s1_runtime(tmp_path, index)
        scheduled = _slot(root, "Scheduled")
        audit = _slot(root, "Audit")
        parent = _parent(cases, scheduled)
        authority = _authority(cases, scheduled, audit)
        budget = _budget(cases, authority)
        path = _legacy_file(root, legacy_case)
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        legacy = module.LegacyReadOnlyAdapter(path)
        assert not any(hasattr(legacy, name) for name in ("write", "append", "replace", "update", "delete"))
        execution = Child("execution")
        public = Child("public")
        controller = _controller(module, root, execution, public, legacy=legacy)
        if legacy_case == "valid_v3":
            result = _audit(controller, cases, parent, authority, budget)
            assert result["legacyWriterCount"] == 0
            assert result["legacy"]["bytesSha256"] == hashlib.sha256(before[0]).hexdigest()
            assert not inspect.getmembers(legacy, predicate=inspect.ismethod) or not any(name == "write" for name, _ in inspect.getmembers(legacy, predicate=inspect.ismethod))
        else:
            with pytest.raises(module.RecoveryControlError) as caught:
                _audit(controller, cases, parent, authority, budget)
            assert getattr(caught.value, "reason", None) in {"LEGACY_STATE_INVALID", "LEGACY_STATE_UNKNOWN"}
        assert (path.read_bytes(), path.stat().st_mtime_ns) == before
