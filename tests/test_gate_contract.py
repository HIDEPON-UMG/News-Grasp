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


def test_same_error_on_different_article_identity_is_not_same_signature(tmp_path: Path) -> None:
    """別記事の同種エラーは artifact identity を含む署名で別失敗として扱う。"""
    artifact = tmp_path / "data" / "articles.jsonl"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("[]", encoding="utf-8")
    state: dict = {"version": 1, "gates": {}}

    first = record_gate_failure(
        state,
        GateFailure(
            gate_id="url-liveness",
            category="urls",
            artifact_paths=("data/articles.jsonl",),
            output="ERROR: URL liveness failed with 404",
            artifact_identity="https://example.com/a",
        ),
        repo_root=tmp_path,
        max_category_failures=3,
    )
    second = record_gate_failure(
        state,
        GateFailure(
            gate_id="url-liveness",
            category="urls",
            artifact_paths=("data/articles.jsonl",),
            output="ERROR: URL liveness failed with 404",
            artifact_identity="https://example.com/b",
        ),
        repo_root=tmp_path,
        max_category_failures=3,
    )

    assert first.retry_allowed is True
    assert second.retry_allowed is True
    assert second.same_signature_failures == 1


def test_category_budget_allows_configured_number_of_distinct_failures(tmp_path: Path) -> None:
    """同じ gate/category でも別失敗なら上限回数までは修復を許可する。"""
    artifact = tmp_path / "digest" / "Summary" / "2026-06-13.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("first state", encoding="utf-8")
    state: dict = {"version": 1, "gates": {}}

    first = record_gate_failure(
        state,
        GateFailure(
            gate_id="daily-quality",
            category="daily",
            artifact_paths=("digest/Summary/2026-06-13.md",),
            output="ERROR: reflection section §06 lacks required emphasis",
        ),
        repo_root=tmp_path,
        max_category_failures=2,
    )
    artifact.write_text("second state", encoding="utf-8")
    second = record_gate_failure(
        state,
        GateFailure(
            gate_id="daily-quality",
            category="daily",
            artifact_paths=("digest/Summary/2026-06-13.md",),
            output="ERROR: selected_total does not match digest article count",
        ),
        repo_root=tmp_path,
        max_category_failures=2,
    )

    assert first.retry_allowed is True
    assert second.retry_allowed is True
    assert second.category_failures == 2


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
