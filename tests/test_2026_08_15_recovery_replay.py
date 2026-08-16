from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "tests" / "fixtures" / "2026_08_15_recovery_replay.json"


def test_20260815_fixture_is_bound_to_transaction_v3() -> None:
    value = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert value["issueDate"] == "2026-08-15"
    assert value["stateMachine"] == "AUDIT_RECOVERY_TRANSACTION_V3"
    assert value["events"]
    assert "c:\\users\\" not in FIXTURE.read_text(encoding="utf-8").lower()


def test_20260815_replay_uses_one_coordinator_and_projection(tmp_path: Path) -> None:
    from tools.audit_recovery_control import ensure_audit_0640

    calls: list[str] = []

    def execute(*, issue_date: str) -> dict[str, object]:
        calls.append(issue_date)
        return {"schemaVersion": "AUDIT_RECOVERY_DECISION_V2", "issueDate": issue_date, "terminal": "audit_normal_green", "exitCode": 0}

    first = ensure_audit_0640(issue_date="2026-08-15", trigger="deadman_0640", transaction_root=tmp_path, executor=execute)
    second = ensure_audit_0640(issue_date="2026-08-15", trigger="automation_0640", transaction_root=tmp_path, executor=execute)
    assert calls == ["2026-08-15"]
    assert first["transactionStatus"] == "terminal"
    assert second["transactionStatus"] == "terminal_projection"


def test_20260815_replay_preserves_fencing_and_terminal_projection(tmp_path: Path) -> None:
    from tools.news_grasp_recovery_transaction import RecoveryTransactionStore

    started = datetime(2026, 8, 15, 6, 40, tzinfo=timezone(timedelta(hours=9)))
    store = RecoveryTransactionStore(tmp_path)
    owner = store.acquire(issue_date="2026-08-15", trigger="deadman_0640", owner_id="owner", now=started)
    result = store.complete(issue_date="2026-08-15", owner_id="owner", fencing_token=owner["fencingToken"], terminal={"terminal": "audit_normal_green", "exitCode": 0}, now=started + timedelta(minutes=10))
    assert result["status"] == "terminal"
    projected = store.acquire(issue_date="2026-08-15", trigger="watcher_failure", owner_id="watcher", now=started + timedelta(minutes=11))
    assert projected["status"] == "terminal_projection"
