"""遅延起動時の必須content生成許可と共有model budgetの境界。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tools import news_grasp_direct_runtime as runtime


ISSUE_DATE = "2026-09-06"
RUN_INTENT = runtime.RUN_INTENT
NOW = datetime.fromisoformat("2026-09-06T07:31:00+09:00")


@dataclass
class _LateRun:
    store: runtime.DirectRunStore
    repo: Path
    run: dict[str, Any]


@pytest.fixture
def late_run(tmp_path: Path) -> _LateRun:
    class FakeClock:
        def __init__(self, value: datetime) -> None:
            self.value = value

        def __call__(self) -> datetime:
            return self.value

    clock = FakeClock(NOW)
    store = runtime.DirectRunStore(
        tmp_path / "state",
        clock=clock,
        test_only_allow_semantic_verifier=True,
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    trigger = NOW - timedelta(minutes=91)
    run = runtime.start_run(
        store,
        cwd=repo,
        issue_date=ISSUE_DATE,
        run_intent=RUN_INTENT,
        scheduler_trigger_at=trigger.isoformat(),
    )
    return _LateRun(store=store, repo=repo, run=run)


def _admit(late: _LateRun, operation_id: str) -> dict[str, Any]:
    return runtime.admit_daily_operation(
        late.store,
        run_id=str(late.run["run_id"]),
        writer_lease=str(late.run["writer_lease"]),
        fencing_token=int(late.run["fencing_token"]),
        operation_id=operation_id,
    )


def test_delayed_start_allows_required_content_only_for_current_issue(
    late_run: _LateRun,
) -> None:
    """91分遅延でも必須contentだけ許可し、任意高コスト禁止は維持する。"""

    current_issue = _admit(late_run, "current_issue_integration")
    public_verification = _admit(late_run, "consumer_public_verification")

    assert current_issue["elapsed_minutes"] == pytest.approx(91.0)
    assert current_issue["dispatch"] == "deadline_revision"
    assert current_issue["required_content_generation_allowed"] is True
    assert current_issue["model_regeneration_allowed"] is False
    assert current_issue["high_cost_generation_allowed"] is False
    assert public_verification["required_content_generation_allowed"] is False
    assert public_verification["model_regeneration_allowed"] is False
    assert public_verification["same_run_resume_allowed"] is True


def test_required_content_permission_does_not_bypass_shared_model_budget(
    late_run: _LateRun,
) -> None:
    """必須content許可が初回5回・修復4回の共有ledger制限を迂回しない。"""

    admission = _admit(late_run, "current_issue_integration")
    assert admission["required_content_generation_allowed"] is True
    ledger = runtime.DailyArtifactLedger(
        late_run.store,
        run_id=str(late_run.run["run_id"]),
        issue_date=ISSUE_DATE,
        writer_lease=str(late_run.run["writer_lease"]),
        fencing_token=int(late_run.run["fencing_token"]),
    )

    for index in range(5):
        ledger.reserve_model_call(
            call_id=f"required-content-initial-{index}",
            budget_class="initial",
            artifact_id=f"required-content-artifact-{index}",
            input_hash=f"required-content-input-{index}",
        )
    with pytest.raises(RuntimeError, match="INITIAL_BUDGET_EXHAUSTED"):
        ledger.reserve_model_call(
            call_id="required-content-initial-5",
            budget_class="initial",
            artifact_id="required-content-artifact-5",
            input_hash="required-content-input-5",
        )

    for index in range(4):
        ledger.reserve_model_call(
            call_id=f"required-content-repair-{index}",
            budget_class="repair",
            artifact_id=f"required-content-repair-artifact-{index}",
            input_hash=f"required-content-repair-input-{index}",
        )
    with pytest.raises(RuntimeError, match="REPAIR_BUDGET_EXHAUSTED"):
        ledger.reserve_model_call(
            call_id="required-content-repair-4",
            budget_class="repair",
            artifact_id="required-content-repair-artifact-4",
            input_hash="required-content-repair-input-4",
        )
    assert ledger.model_call_usage() == {"initial": 5, "repair": 4, "total": 9}
