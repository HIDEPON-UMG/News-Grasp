from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "tests" / "fixtures" / "2026_08_16_recovery_replay.json"


def test_20260816_fixture_captures_fail_closed_and_observation_boundaries() -> None:
    value = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert value["issueDate"] == "2026-08-16"
    assert value["stateMachine"] == "AUDIT_RECOVERY_TRANSACTION_V3"
    assert {event["expectedStop"] for event in value["events"]} == {
        "process_start_zero",
        "append_only_observation_event",
        "readiness_proof_stale",
    }


def test_20260816_major_incident_is_observation_not_transaction_terminal(tmp_path: Path) -> None:
    from tools.news_grasp_recovery_transaction import MISSION_TERMINALS, RecoveryTransactionStore, TRANSACTION_SCHEMA

    started = datetime(2026, 8, 16, 6, 40, tzinfo=timezone.utc)
    store = RecoveryTransactionStore(tmp_path)
    owner = store.acquire(issue_date="2026-08-16", trigger="automation_0640", owner_id="owner", now=started)
    store.advance_phase(issue_date="2026-08-16", owner_id="owner", fencing_token=owner["fencingToken"], phase="envelope_validated", now=started)
    store.observe(issue_date="2026-08-16", owner_id="owner", fencing_token=owner["fencingToken"], observation={"reasonCode": "AUDIT_MAJOR_INCIDENT_OPEN"}, now=started)
    result = store.complete(issue_date="2026-08-16", owner_id="owner", fencing_token=owner["fencingToken"], terminal={"terminal": "audit_major_incident_open", "exitCode": 2}, now=started)
    persisted = json.loads((tmp_path / "2026-08-16.json").read_text(encoding="utf-8"))
    assert result["status"] == "terminal"
    assert persisted["schemaVersion"] == TRANSACTION_SCHEMA
    assert persisted["missionTerminal"] in MISSION_TERMINALS
    assert persisted["terminalProjection"]["terminal"] == "audit_major_incident_open"
    assert any(event["schemaVersion"] == "AuditObservationEventV1" for event in persisted["observationEvents"])


def test_20260816_replay_closes_with_single_fenced_owner(tmp_path: Path) -> None:
    from tools.audit_recovery_control import ensure_audit_0640

    calls: list[str] = []

    def execute(*, issue_date: str) -> dict[str, object]:
        calls.append(issue_date)
        return {"schemaVersion": "AUDIT_RECOVERY_DECISION_V2", "issueDate": issue_date, "terminal": "audit_normal_green", "exitCode": 0}

    first = ensure_audit_0640(issue_date="2026-08-16", trigger="deadman_0640", transaction_root=tmp_path, executor=execute)
    second = ensure_audit_0640(issue_date="2026-08-16", trigger="watcher_failure", transaction_root=tmp_path, executor=execute)
    assert calls == ["2026-08-16"]
    assert first["transactionStatus"] == "terminal"
    assert second["transactionStatus"] == "terminal_projection"
