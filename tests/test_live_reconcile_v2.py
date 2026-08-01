from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    "failure_point",
    [
        "backup",
        "staging",
        "replace",
        "action_verify",
        "installed_restore",
        "task_xml_restore",
        "rollback_verify",
    ],
)
def test_transaction_and_rollback_failures_never_expose_mixed_generation(tmp_path, failure_point) -> None:
    from tools.harness.live_reconcile_v2 import simulate_reconcile_failure

    result = simulate_reconcile_failure(tmp_path, failure_point=failure_point)
    assert result["lastRunTimeChanged"] is False
    assert result["mixedGenerationExecutable"] is False
    assert result["reconcilePossible"] is True
    if result["rollbackComplete"]:
        assert result["allRolesGeneration"] == "old"
    else:
        assert result["taskEnabled"] is False
        assert result["durableRecoveryJournal"] is True
