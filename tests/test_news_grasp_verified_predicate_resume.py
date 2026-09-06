"""品質を再検証した再開だけに同一claimの冪等再利用を許す。"""
import pytest
from tools import news_grasp_direct_runtime as runtime


@pytest.fixture
def claims(tmp_path):
    store = runtime.DirectRunStore(tmp_path / "state", test_only_allow_semantic_verifier=True)
    ledger = runtime.PredicateLedger(store)
    values = dict(generation_id="run-generation", predicate_id="quality", owner="producer",
        source_identity="stable-reservation", evidence={"issue_date": "2026-09-05", "producer_id": "producer"})
    first = ledger.claim_once(**values)
    return store, ledger, values, first


def test_strict_claim_still_rejects_duplicate(claims):
    _, ledger, values, _ = claims
    with pytest.raises(RuntimeError, match="already_consumed"):
        ledger.claim_once(**values)


def test_verified_identical_claim_reuses_original_without_new_consumption(claims):
    store, ledger, values, first = claims
    second = ledger.claim_once(**values, reuse_identical=True)
    assert second["status"] == "reused"
    assert second["claimed_at"] == first["claimed_at"]
    assert second["evidence"] == first["evidence"]
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM predicate_claims").fetchone()[0] == 1


@pytest.mark.parametrize("change", [{"owner": "other"}, {"source_identity": "other"}, {"evidence": {"issue_date": "other"}}])
def test_verified_reuse_cannot_change_binding(claims, change):
    _, ledger, values, _ = claims
    with pytest.raises((RuntimeError, PermissionError, ValueError)):
        ledger.claim_once(**(values | change), reuse_identical=True)
