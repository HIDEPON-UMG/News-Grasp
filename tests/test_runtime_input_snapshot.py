"""WP-14 A17 のruntime input Red/Green契約。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import fx_rates


SAMPLE = {
    "result": "success",
    "provider": "https://www.exchangerate-api.com",
    "documentation": "https://www.exchangerate-api.com/docs/free",
    "time_last_update_utc": "Wed, 01 Jul 2026 00:02:31 +0000",
    "base_code": "USD",
    "rates": {"USD": 1, "JPY": 162.24, "EUR": 0.916, "GBP": 0.861, "AUD": 1.542, "CNH": 7.2104, "CHF": 0.8932},
}


def test_ng3_a17_primary_fx_api_keeps_tracked_snapshot_clean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NEWS_GRASP_SKIP_URL_CHECK", raising=False)
    tracked = tmp_path / "tracked-fx.json"
    original = json.dumps({"fixture": "lkg"}, ensure_ascii=False) + "\n"
    tracked.write_text(original, encoding="utf-8")
    monkeypatch.setattr(fx_rates, "DEFAULT_SNAPSHOT", tracked)
    monkeypatch.setattr(fx_rates, "fetch_fx_rates", lambda **_kwargs: SAMPLE)
    panel = fx_rates.get_fx_panel()
    assert tracked.read_text(encoding="utf-8") == original
    assert panel["source"] == "api"
    assert panel["runtimeInputSnapshot"]["schemaVersion"] == "RUNTIME_INPUT_SNAPSHOT_V1"


def test_ng3_a17_adversarial_runtime_pointer_replay_and_foreign_generation(tmp_path: Path) -> None:
    assert callable(getattr(__import__("tools.news_grasp_runtime_input", fromlist=["RuntimeInputStore"]), "RuntimeInputStore", None))
    from tools.news_grasp_runtime_input import RuntimeInputError, RuntimeInputStore

    store = RuntimeInputStore(tmp_path / "runtime")
    first = store.commit(
        input_kind="fx_rates",
        issue_date="2026-08-11",
        product_generation_id="generation-1",
        producer_id="fx-api",
        producer_operation_id="op-1",
        payload=SAMPLE,
        schema_id="FX_RATES_V1",
        oracle_id="fx-payload-v1",
    )
    assert first["sequence"] == 1
    with pytest.raises(RuntimeInputError, match="RUNTIME_INPUT_SEQUENCE_INVALID"):
        store.commit(
            input_kind="fx_rates",
            issue_date="2026-08-11",
            product_generation_id="generation-1",
            producer_id="fx-api",
            producer_operation_id="op-0",
            payload=SAMPLE,
            schema_id="FX_RATES_V1",
            oracle_id="fx-payload-v1",
            sequence=0,
        )


def test_ng3_a17_recovery_same_operation_is_idempotent_and_winner_is_single(tmp_path: Path) -> None:
    from tools.news_grasp_runtime_input import RuntimeInputStore

    store = RuntimeInputStore(tmp_path / "runtime")
    kwargs = {
        "input_kind": "fx_rates",
        "issue_date": "2026-08-11",
        "product_generation_id": "generation-1",
        "producer_id": "fx-api",
        "producer_operation_id": "op-1",
        "payload": SAMPLE,
        "schema_id": "FX_RATES_V1",
        "oracle_id": "fx-payload-v1",
    }
    first = store.commit(**kwargs)
    replay = store.commit(**kwargs)
    assert first["manifestSha256"] == replay["manifestSha256"]
    assert store.read_current(input_kind="fx_rates", issue_date="2026-08-11", product_generation_id="generation-1")["sequence"] == 1
