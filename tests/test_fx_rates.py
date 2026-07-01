#!/usr/bin/env python3
"""ExchangeRate-API Open を使う FX レート panel の contract tests。"""
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
    "rates": {
        "USD": 1,
        "JPY": 162.24,
        "EUR": 0.916,
        "GBP": 0.861,
        "AUD": 1.542,
        "CNH": 7.2104,
        "CHF": 0.8932,
    },
}


def test_build_fx_panel_from_success_payload() -> None:
    panel = fx_rates.build_fx_panel(SAMPLE)

    assert panel["source"] == "api"
    assert panel["ticker_label"] == "LIVE RATES"
    assert "USD/JPY 162.24" in panel["ticker_text"]
    assert "EUR/USD 1.0917" in panel["ticker_text"]
    assert "GBP/JPY 188.43" in panel["ticker_text"]
    assert "AUD/USD 0.6485" in panel["ticker_text"]
    assert "USD/CNH 7.2104" in panel["ticker_text"]
    assert panel["primary_pair"] == "USD / JPY"
    assert panel["primary_value"] == "162.24"
    assert panel["attribution_label"] == "Rates By Exchange Rate API"
    assert panel["attribution_url"] == "https://www.exchangerate-api.com"
    assert panel["updated_at"] == "Wed, 01 Jul 2026 00:02:31 +0000"


def test_build_fx_panel_rejects_invalid_payload() -> None:
    with pytest.raises(fx_rates.FxRateError):
        fx_rates.build_fx_panel({"result": "error", "rates": {}})


def test_get_fx_panel_uses_snapshot_when_fetch_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot = tmp_path / "fx.json"
    snapshot.write_text(json.dumps(SAMPLE), encoding="utf-8")

    def _boom(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise fx_rates.FxRateError("timeout")

    monkeypatch.setattr(fx_rates, "fetch_fx_rates", _boom)
    panel = fx_rates.get_fx_panel(snapshot_path=snapshot)

    assert panel["source"] == "snapshot"
    assert panel["primary_value"] == "162.24"
    assert panel["has_provider_data"] is True


def test_get_fx_panel_uses_static_fallback_when_snapshot_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise fx_rates.FxRateError("timeout")

    monkeypatch.setattr(fx_rates, "fetch_fx_rates", _boom)
    panel = fx_rates.get_fx_panel(snapshot_path=tmp_path / "missing.json")

    assert panel["source"] == "fallback"
    assert "USD/JPY 162.24" in panel["ticker_text"]
    assert panel["has_provider_data"] is False
