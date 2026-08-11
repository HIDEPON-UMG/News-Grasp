"""カテゴリートップヒーロー用の FX レート取得と fallback。

ExchangeRate-API Open endpoint は no-key だが attribution required のため、
公開 UI へ provider link を渡す。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any

from tools.news_grasp_runtime_input import RuntimeInputError, RuntimeInputStore

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SNAPSHOT = ROOT / "data" / "fx_rates_snapshot.json"
API_URL = "https://open.er-api.com/v6/latest/USD"
RUNTIME_INPUT_ROOT = ROOT / "build" / "runtime-inputs"


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


def _runtime_snapshot_path(*, issue_date: str, product_generation_id: str) -> Path:
    return RUNTIME_INPUT_ROOT / issue_date / "fx_rates" / f"{product_generation_id}.json"


def get_fx_panel(
    *,
    snapshot_path: Path | None = None,
    timeout: float = 4.0,
    issue_date: str | None = None,
    product_generation_id: str | None = None,
) -> dict[str, Any]:
    """API、immutable runtime snapshot、LKG snapshot、静的fallbackの順で返す。

    引数なしのproduction経路はtracked ``data/fx_rates_snapshot.json``へ書かない。
    明示されたsnapshot_pathは既存fixture互換のread-only入力として扱う。
    """
    tracked_snapshot = snapshot_path or DEFAULT_SNAPSHOT
    runtime_mode = snapshot_path is None
    target_date = issue_date or os.environ.get("NEWS_GRASP_ISSUE_DATE") or datetime.now().strftime("%Y-%m-%d")
    generation = product_generation_id or os.environ.get("NEWS_GRASP_PRODUCT_GENERATION_ID") or "runtime-input-unbound"
    if os.environ.get("NEWS_GRASP_SKIP_URL_CHECK") == "1":
        if runtime_mode:
            try:
                runtime_store = RuntimeInputStore(RUNTIME_INPUT_ROOT)
                runtime_manifest = runtime_store.read_current(
                    input_kind="fx_rates", issue_date=target_date, product_generation_id=generation
                )
                return build_fx_panel(
                    runtime_store.read_payload(runtime_manifest),
                    source="runtime_snapshot",
                    has_provider_data=True,
                )
            except RuntimeInputError:
                pass
        try:
            return build_fx_panel(read_snapshot(tracked_snapshot), source="snapshot", has_provider_data=True)
        except FxRateError:
            return build_fx_panel(STATIC_FALLBACK, source="fallback", has_provider_data=False)

    try:
        payload = fetch_fx_rates(timeout=timeout)
        panel = build_fx_panel(payload, source="api", has_provider_data=True)
        if not runtime_mode:
            # 明示fixture pathは既存testの互換性を保つが、production defaultはここへ来ない。
            write_snapshot(payload, tracked_snapshot)
            return panel
        try:
            store = RuntimeInputStore(RUNTIME_INPUT_ROOT)
            operation_id = hashlib.sha256(
                json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            snapshot = store.commit(
                input_kind="fx_rates",
                issue_date=target_date,
                product_generation_id=generation,
                producer_id="fx-api",
                producer_operation_id=operation_id,
                payload=payload,
                schema_id="FX_RATES_V1",
                oracle_id="fx-payload-v1",
                source_status="api",
            )
            panel["runtimeInputSnapshot"] = snapshot
            return panel
        except RuntimeInputError:
            # API結果をtracked sourceへ戻さず、直前LKGへ安全にフォールバックする。
            panel["runtimeInputSnapshot"] = None
            return panel
    except FxRateError:
        try:
            if runtime_mode:
                try:
                    runtime_manifest = RuntimeInputStore(RUNTIME_INPUT_ROOT).read_current(
                        input_kind="fx_rates",
                        issue_date=target_date,
                        product_generation_id=generation,
                    )
                    runtime_payload = RuntimeInputStore(RUNTIME_INPUT_ROOT).read_payload(runtime_manifest)
                    return build_fx_panel(runtime_payload, source="runtime_snapshot", has_provider_data=True)
                except (FxRateError, RuntimeInputError):
                    pass
            return build_fx_panel(read_snapshot(tracked_snapshot), source="snapshot", has_provider_data=True)
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
