#!/usr/bin/env python3
"""per-article 隔離 gate policy の契約テスト。"""
from __future__ import annotations

from tools.gate_policy import GateAction, classify_gate_failure


def test_schema_and_emphasis_failures_are_repairable() -> None:
    assert classify_gate_failure("record-schema", "ERROR: 必須キー欠落: 'title_ja'") == GateAction.REPAIRABLE
    assert classify_gate_failure("daily-quality", "card #01 lacks required emphasis") == GateAction.REPAIRABLE


def test_url_and_freshness_failures_are_quarantine() -> None:
    assert classify_gate_failure("url-liveness", "404 Not Found") == GateAction.QUARANTINE
    assert classify_gate_failure("daily-quality", "source URL date 2026-05-01 is old") == GateAction.QUARANTINE


def test_build_and_git_failures_are_fatal() -> None:
    assert classify_gate_failure("generate-pages", "Traceback") == GateAction.FATAL
    assert classify_gate_failure("git-push", "rejected") == GateAction.FATAL
