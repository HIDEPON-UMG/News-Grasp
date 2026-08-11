"""News-Grasp scheduled/release gateの専用consumer。"""

from __future__ import annotations

from typing import Any, Mapping

from tools import news_grasp_gate_profiles as profiles


def daily_gate(results: Mapping[str, bool]) -> dict[str, Any]:
    """scheduled/recoveryからRelease gateへ到達させず当日gateだけを評価する。"""
    return profiles.evaluate_daily(results)


def release_gate(results: Mapping[str, bool]) -> dict[str, Any]:
    """safe commit前のRelease gateを明示的に一回評価する。"""
    return profiles.evaluate_release(results)


def validate_scheduled_calls(calls: list[str]) -> dict[str, Any]:
    return profiles.scheduled_call_graph(calls=calls)
