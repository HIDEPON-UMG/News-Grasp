"""カテゴリートップヒーロー用の FX レート取得と fallback。

ExchangeRate-API Open endpoint は no-key だが attribution required のため、
公開 UI へ provider link を渡す。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SNAPSHOT = ROOT / "data" / "fx_rates_snapshot.json"
API_URL = "https://open.er-api.com/v6/latest/USD"


class FxRateError(RuntimeError):
    """FX レート payload が使えない場合の typed error。"""


STATIC_FALLBACK: dict[str, Any] = {
    "result": "success",
    "provider": "https://www.exchangerate-api.com",
    "documentation": "https://www.exchangerate-api.com/docs/free",
    "time_last_update_utc": "fallback",
    "base_code": "USD",
    "rates": {
        "USD": 1,
        "JPY": 162.24,
        "EUR": 0.9164,
        "GBP": 0.8610,
        "AUD": 1.5420,
        "CNH": 7.2104,
        "CHF": 0.8932,
    },
}


def fetch_fx_rates(*, timeout: float = 4.0, url: str = API_URL) -> dict[str, Any]:
    """ExchangeRate-API Open endpoint から USD base の rate payload を取得する。"""
    req = urllib.request.Request(url, headers={"User-Agent": "News-Grasp/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            if res.status != 200:
                raise FxRateError(f"HTTP {res.status}")
            raw = res.read().decode("utf-8")
    except (OSError, urllib.error.URLError) as exc:
        raise FxRateError(str(exc)) from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FxRateError("invalid json") from exc
    _validate_payload(payload)
    return payload


def read_snapshot(path: Path = DEFAULT_SNAPSHOT) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FxRateError(str(exc)) from exc
    _validate_payload(payload)
    return payload


def write_snapshot(payload: dict[str, Any], path: Path = DEFAULT_SNAPSHOT) -> None:
    _validate_payload(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def get_fx_panel(*, snapshot_path: Path = DEFAULT_SNAPSHOT, timeout: float = 4.0) -> dict[str, Any]:
    """API、snapshot、静的 fallback の順に FX hero panel context を返す。"""
    if os.environ.get("NEWS_GRASP_SKIP_URL_CHECK") == "1":
        try:
            return build_fx_panel(read_snapshot(snapshot_path), source="snapshot", has_provider_data=True)
        except FxRateError:
            return build_fx_panel(STATIC_FALLBACK, source="fallback", has_provider_data=False)

    try:
        payload = fetch_fx_rates(timeout=timeout)
        write_snapshot(payload, snapshot_path)
        return build_fx_panel(payload, source="api", has_provider_data=True)
    except FxRateError:
        try:
            return build_fx_panel(read_snapshot(snapshot_path), source="snapshot", has_provider_data=True)
        except FxRateError:
            return build_fx_panel(STATIC_FALLBACK, source="fallback", has_provider_data=False)


def build_fx_panel(
    payload: dict[str, Any],
    *,
    source: str = "api",
    has_provider_data: bool = True,
) -> dict[str, Any]:
    """USD base payload から Turn 4a の表示値を作る。"""
    _validate_payload(payload)
    rates = payload["rates"]
    usd_jpy = _rate(rates, "JPY")
    eur_usd = 1 / _rate(rates, "EUR")
    gbp_jpy = usd_jpy / _rate(rates, "GBP")
    aud_usd = 1 / _rate(rates, "AUD")
    usd_cnh = _rate(rates, "CNH")
    usd_chf = _rate(rates, "CHF")
    ticker_parts = [
        f"USD/JPY {_fmt(usd_jpy, 2)} ▲",
        f"EUR/USD {_fmt(eur_usd, 4)} ▼",
        f"GBP/JPY {_fmt(gbp_jpy, 2)} ▲",
        f"AUD/USD {_fmt(aud_usd, 4)} ▼",
        f"USD/CNH {_fmt(usd_cnh, 4)} ◆",
        f"USD/CHF {_fmt(usd_chf, 4)} ▼",
    ]
    return {
        "source": source,
        "has_provider_data": has_provider_data,
        "ticker_label": "LIVE RATES",
        "ticker_text": " · ".join(ticker_parts),
        "primary_pair": "USD / JPY",
        "primary_value": _fmt(usd_jpy, 2),
        "primary_delta": "▲",
        "note": "ExchangeRate-API Open · daily refreshed",
        "updated_at": str(payload.get("time_last_update_utc") or ""),
        "attribution_label": "Rates By Exchange Rate API",
        "attribution_url": "https://www.exchangerate-api.com",
    }


def _validate_payload(payload: dict[str, Any]) -> None:
    if payload.get("result") != "success":
        raise FxRateError("result is not success")
    if payload.get("base_code") != "USD":
        raise FxRateError("base_code is not USD")
    rates = payload.get("rates")
    if not isinstance(rates, dict):
        raise FxRateError("rates missing")
    for code in ("JPY", "EUR", "GBP", "AUD", "CNH", "CHF"):
        _rate(rates, code)


def _rate(rates: dict[str, Any], code: str) -> float:
    try:
        value = float(rates[code])
    except (KeyError, TypeError, ValueError) as exc:
        raise FxRateError(f"missing rate {code}") from exc
    if value <= 0:
        raise FxRateError(f"invalid rate {code}")
    return value


def _fmt(value: float, digits: int) -> str:
    return f"{value:.{digits}f}"
