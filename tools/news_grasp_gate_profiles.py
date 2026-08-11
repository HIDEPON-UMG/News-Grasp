"""Daily gateとRelease gateのproduct-local profile分離。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping


class NewsGraspGateProfileError(RuntimeError):
    """gate profile違反。"""


DAILY_ORACLES = (
    "artifact_schema_quality",
    "required_public_bundle",
    "public_surface",
    "distribution",
    "notification",
    "pure_readiness",
)
RELEASE_ORACLES = (
    "pytest_regression",
    "playwright",
    "historical_period",
    "crash_replay_drift",
    "final_nopublish_e2e",
)


@dataclass(frozen=True)
class GateProfile:
    profile_id: str
    oracles: tuple[str, ...]
    reachable_from_scheduled: bool


DAILY_PROFILE = GateProfile("DailyProductGateProfileV1", DAILY_ORACLES, True)
RELEASE_PROFILE = GateProfile("ReleaseGateProfileV1", RELEASE_ORACLES, False)


def validate_profiles() -> dict[str, Any]:
    if set(DAILY_PROFILE.oracles) & set(RELEASE_PROFILE.oracles):
        raise NewsGraspGateProfileError("NG_GATE_ORACLE_OVERLAP")
    if RELEASE_PROFILE.reachable_from_scheduled:
        raise NewsGraspGateProfileError("NG_RELEASE_GATE_REACHABLE_FROM_DAILY")
    return {
        "status": "validated",
        "daily": list(DAILY_PROFILE.oracles),
        "release": list(RELEASE_PROFILE.oracles),
    }


def evaluate_daily(results: Mapping[str, bool]) -> dict[str, Any]:
    missing = [oracle for oracle in DAILY_ORACLES if results.get(oracle) is not True]
    return {"profile": DAILY_PROFILE.profile_id, "status": "green" if not missing else "red", "missing": missing}


def evaluate_release(results: Mapping[str, bool]) -> dict[str, Any]:
    missing = [oracle for oracle in RELEASE_ORACLES if results.get(oracle) is not True]
    return {"profile": RELEASE_PROFILE.profile_id, "status": "green" if not missing else "red", "missing": missing}


def scheduled_call_graph(*, calls: list[str]) -> dict[str, Any]:
    forbidden = {"pytest", "playwright", "historical_period", "final_nopublish_e2e"}
    reached = sorted(forbidden.intersection(calls))
    if reached:
        raise NewsGraspGateProfileError("NG_RELEASE_GATE_REACHED_FROM_DAILY")
    return {"status": "green", "calls": calls, "releaseCalls": []}


# 実consumer名をstable contractとして公開する。
daily_gate = evaluate_daily
release_gate = evaluate_release
