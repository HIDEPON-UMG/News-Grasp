from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.news_grasp_finalization import (
    FinalizationError,
    build_candidate_state,
    close_wal,
    commit_candidate,
    load_wal,
    prepare_wal,
    record_guard_decision,
    recover_wal,
    sha256_value,
    write_atomic_json,
    write_outcome_sidecar,
)


def _state() -> dict[str, object]:
    return {
        "schemaVersion": "NEWS_GRASP_RUNNER_STATE_V2",
        "date": "2026-08-16",
        "status": "running",
        "exit_code": -1,
        "run_id": "run-1",
        "scheduled_attempt_status": "failed",
        "recovery_attempt_status": "running",
        "updated_at": "2026-08-16T06:40:00+09:00",
    }


def _prepared(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, object]]:
    state_path = tmp_path / "news-grasp-runner-state.json"
    candidate_path = tmp_path / "news-grasp-runner-state.candidate.json"
    wal_path = tmp_path / "2026-08-16.finalization-wal.json"
    before = _state()
    write_atomic_json(state_path, before)
    candidate = build_candidate_state(
        before,
        issue_date="2026-08-16",
        manifest_path=tmp_path / "2026-08-16.json",
        publish_commit="a" * 40,
        finalization_receipt_path=tmp_path / "2026-08-16.finalization-receipt.json",
        finalization_receipt_sha256="b" * 64,
    )
    prepare_wal(
        wal_path=wal_path,
        candidate_path=candidate_path,
        state_path=state_path,
        before_state=before,
        candidate_state=candidate,
        manifest_sha256="c" * 64,
        finalization_receipt_sha256="b" * 64,
        execution_receipt_sha256="d" * 64,
        issue_date="2026-08-16",
    )
    return state_path, candidate_path, wal_path, candidate


def test_prepare_guard_commit_close_is_single_forward_chain(tmp_path: Path) -> None:
    state_path, candidate_path, wal_path, candidate = _prepared(tmp_path)
    before = json.loads(state_path.read_text(encoding="utf-8"))
    assert before["status"] == "running"
    wal = record_guard_decision(
        wal_path=wal_path,
        candidate_path=candidate_path,
        decision={"ok": True, "integrityOnly": True, "failures": []},
    )
    assert wal["phase"] == "guard_passed"
    committed = commit_candidate(wal_path=wal_path)
    assert committed["phase"] == "committed"
    assert json.loads(state_path.read_text(encoding="utf-8"))["status"] == "publish_complete"
    closed = close_wal(wal_path=wal_path)
    assert closed["phase"] == "closed"
    assert closed["afterStateSha256"] == sha256_value(candidate)


def test_guard_red_does_not_commit_candidate(tmp_path: Path) -> None:
    state_path, candidate_path, wal_path, _ = _prepared(tmp_path)
    with pytest.raises(FinalizationError, match="FINALIZATION_GUARD_RED"):
        record_guard_decision(
            wal_path=wal_path,
            candidate_path=candidate_path,
            decision={"ok": False, "integrityOnly": True, "failures": ["red"]},
        )
    assert json.loads(state_path.read_text(encoding="utf-8"))["status"] == "running"
    assert load_wal(wal_path)["phase"] == "prepared"


def test_guard_after_crash_forward_resumes_with_same_wal(tmp_path: Path) -> None:
    state_path, candidate_path, wal_path, _ = _prepared(tmp_path)
    record_guard_decision(
        wal_path=wal_path,
        candidate_path=candidate_path,
        decision={"ok": True, "integrityOnly": True},
    )
    resumed = recover_wal(wal_path=wal_path)
    assert resumed["phase"] == "committed"
    assert state_path.exists()
    assert not candidate_path.exists()
    closed = recover_wal(wal_path=wal_path)
    assert closed["phase"] == "closed"


def test_state_after_rename_before_wal_close_forward_closes(tmp_path: Path) -> None:
    state_path, candidate_path, wal_path, _ = _prepared(tmp_path)
    record_guard_decision(
        wal_path=wal_path,
        candidate_path=candidate_path,
        decision={"ok": True, "integrityOnly": True},
    )
    # crash point: state rename succeeded, WAL terminal update did not
    candidate_path.replace(state_path)
    resumed = recover_wal(wal_path=wal_path)
    assert resumed["phase"] == "committed"
    assert resumed["afterStateSha256"]


def test_divergent_state_is_fail_closed_without_new_wal(tmp_path: Path) -> None:
    state_path, candidate_path, wal_path, _ = _prepared(tmp_path)
    record_guard_decision(
        wal_path=wal_path,
        candidate_path=candidate_path,
        decision={"ok": True, "integrityOnly": True},
    )
    write_atomic_json(state_path, {**_state(), "status": "unexpected"})
    with pytest.raises(FinalizationError, match="FINALIZATION_DIVERGENT_STATE"):
        commit_candidate(wal_path=wal_path)
    assert load_wal(wal_path)["phase"] == "guard_passed"


def test_outcome_sidecar_requires_committed_wal(tmp_path: Path) -> None:
    _, candidate_path, wal_path, _ = _prepared(tmp_path)
    with pytest.raises(FinalizationError, match="FINALIZATION_COMMIT_REQUIRED"):
        write_outcome_sidecar(
            path=tmp_path / "outcome.json",
            outcome={"sloStatus": "slo_failed"},
            wal_path=wal_path,
        )
    record_guard_decision(
        wal_path=wal_path,
        candidate_path=candidate_path,
        decision={"ok": True, "integrityOnly": True},
    )
    commit_candidate(wal_path=wal_path)
    outcome = write_outcome_sidecar(
        path=tmp_path / "outcome.json",
        outcome={"sloStatus": "slo_failed", "processExitCode": 2},
        wal_path=wal_path,
    )
    assert outcome["publicAuthorityPreserved"] is True
    assert outcome["schemaVersion"] == "COMPLETION_OUTCOME_ENVELOPE_V2"
