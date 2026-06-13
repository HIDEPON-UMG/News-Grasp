#!/usr/bin/env python3
"""runner gate 失敗を repair / quarantine / fatal に分類する境界モジュール。"""
from __future__ import annotations

from enum import StrEnum


class GateAction(StrEnum):
    """gate 失敗後に runner が取るべき処置。"""

    REPAIRABLE = "repairable"
    QUARANTINE = "quarantine"
    FATAL = "fatal"


_FATAL_GATES = {
    "generate-pages",
    "git-commit",
    "git-push",
    "availability",
}

_REPAIRABLE_HINTS = (
    "title_ja",
    "必須キー欠落",
    "schema",
    "lacks required emphasis",
    "card #",
    "reflection",
    "hero_left",
    "hero_right",
)

_QUARANTINE_HINTS = (
    "404",
    "410",
    "session 未確認",
    "source url date",
    "top article date",
    "follow-up matched_with",
    "偽日付",
    "stale",
)


def classify_gate_failure(gate_id: str, output: str) -> GateAction:
    """gate 失敗を runner の処置単位へ分類する。

    fatal は build/publish/git の破滅系だけに限定し、記事単位で落とせるものは
    quarantine、機械補完できるものは repairable へ寄せる。
    """
    gate = gate_id.strip().casefold()
    text = output.casefold()
    if gate in _FATAL_GATES or gate.startswith("git-"):
        return GateAction.FATAL
    if gate == "url-liveness":
        return GateAction.QUARANTINE
    if any(h.casefold() in text for h in _QUARANTINE_HINTS):
        return GateAction.QUARANTINE
    if any(h.casefold() in text for h in _REPAIRABLE_HINTS):
        return GateAction.REPAIRABLE
    return GateAction.REPAIRABLE
