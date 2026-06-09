#!/usr/bin/env python3
"""runner gate retry budget の契約テスト。"""
from __future__ import annotations

from pathlib import Path

from tools.gate_contract import GateFailure, record_gate_failure


def test_gate_failure_allows_one_targeted_repair(tmp_path: Path) -> None:
    artifact = tmp_path / "digest" / "Summary" / "2026-06-09.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("short lead", encoding="utf-8")
    state: dict = {"version": 1, "gates": {}}
    failure = GateFailure(
        gate_id="summary-reflection",
        category="summary",
        artifact_paths=("digest/Summary/2026-06-09.md",),
        output="ERROR: home-hero__lead が短すぎます",
    )

    decision = record_gate_failure(state, failure, repo_root=tmp_path)

    assert decision.retry_allowed is True
    assert decision.same_signature_failures == 1


def test_same_failure_signature_stops_second_loop(tmp_path: Path) -> None:
    artifact = tmp_path / "digest" / "Summary" / "2026-06-09.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("short lead", encoding="utf-8")
    state: dict = {"version": 1, "gates": {}}
    failure = GateFailure(
        gate_id="summary-reflection",
        category="summary",
        artifact_paths=("digest/Summary/2026-06-09.md",),
        output="ERROR: home-hero__lead が短すぎます",
    )

    first = record_gate_failure(state, failure, repo_root=tmp_path)
    second = record_gate_failure(state, failure, repo_root=tmp_path)

    assert first.retry_allowed is True
    assert second.retry_allowed is False
    assert second.reason == "same failure_signature repeated"


def test_non_retryable_security_failure_never_calls_repair(tmp_path: Path) -> None:
    state: dict = {"version": 1, "gates": {}}
    failure = GateFailure(
        gate_id="safe-commit",
        category="security",
        artifact_paths=("docs/index.html",),
        output="ERROR: secret token appears in staged diff",
    )

    decision = record_gate_failure(state, failure, repo_root=tmp_path)

    assert decision.retry_allowed is False
    assert decision.reason == "non-retryable failure class"
