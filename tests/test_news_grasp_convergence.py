"""WP-15 A18 convergence decision Red/Green契約。"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools import news_grasp_daily_control as daily_control


def test_ng3_a18_primary_composite_state_unique_action_sequence() -> None:
    assert callable(getattr(daily_control, "decide_operational_convergence", None))
    result = daily_control.decide_operational_convergence(
        state={"source": "drift", "runtime": "green", "task": "stale", "external": "ready"},
        previous_event=None,
    )
    assert result["nextActionId"] == "active_generation_reconcile"
    assert result["actionClass"] == "mutation"
    assert result["retryEligible"] is True


def test_ng3_a18_adversarial_same_cause_no_duplicate_effect(tmp_path: Path) -> None:
    assert callable(getattr(daily_control, "record_convergence_event", None))
    first = daily_control.record_convergence_event(
        path=tmp_path / "events.jsonl",
        state_hash="a" * 64,
        cause_fingerprint="c" * 64,
        action_id="active_generation_reconcile",
        action_class="mutation",
        result="accepted",
    )
    second = daily_control.record_convergence_event(
        path=tmp_path / "events.jsonl",
        state_hash="a" * 64,
        cause_fingerprint="c" * 64,
        action_id="active_generation_reconcile",
        action_class="mutation",
        result="accepted",
    )
    assert first["accepted"] is True
    assert second["accepted"] is False
    assert second["reasonCode"] == "CONVERGENCE_SAME_CAUSE_NO_DUPLICATE"


def test_ng3_a18_recovery_delayed_success_not_terminalized() -> None:
    assert callable(getattr(daily_control, "reverify_convergence", None))
    result = daily_control.reverify_convergence(
        submission_state="accepted",
        observed_effect=False,
        deadline_expired=False,
        progress_evidence_hash="d" * 64,
    )
    assert result["submissionState"] == "pending_external"
    assert result["terminal"] is False
