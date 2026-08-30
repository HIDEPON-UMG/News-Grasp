from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tools import artifact_lifecycle as lifecycle


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_atomic_manifest_replace_retries_transient_permission_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "manifest.json"
    target.write_text('{"old":true}\n', encoding="utf-8")
    real_replace = os.replace
    calls = 0

    def flaky_replace(source: str | Path, destination: str | Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PermissionError(5, "OneDrive temporary lock")
        real_replace(source, destination)

    monkeypatch.setattr(lifecycle.os, "replace", flaky_replace)
    lifecycle.atomic_write_json(target, {"new": True})

    assert calls == 2
    assert json.loads(target.read_text(encoding="utf-8")) == {"new": True}


def test_raw_output_must_stay_under_ops(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    accepted = lifecycle.validate_raw_output_path(repo, repo / "_ops" / "benchmark-runs" / "run-1")
    assert accepted == (repo / "_ops" / "benchmark-runs" / "run-1").resolve()

    with pytest.raises(ValueError, match="raw output must be under"):
        lifecycle.validate_raw_output_path(repo, repo / "build" / "codex-recovery-benchmark")

    with pytest.raises(ValueError, match="raw output must be under"):
        lifecycle.validate_raw_output_path(repo, tmp_path / "outside")


def test_default_raw_roots_are_ignored_ops_paths() -> None:
    assert lifecycle.default_raw_root("codex-recovery-benchmark").as_posix() == "_ops/benchmark-runs/codex-recovery-benchmark"
    assert lifecycle.default_raw_root("external-benchmark-matrix").as_posix() == "_ops/benchmark-runs/external-benchmark-matrix"


def test_retired_runner_has_no_editor_attempt_snapshot_authority() -> None:
    """旧runner削除後はrunner内snapshot契約を復活させず、legacy tombstoneだけを許す。"""

    runner_path = REPO_ROOT / "scripts" / "ops" / "news-grasp-runner.ps1"
    bootstrap = (REPO_ROOT / "scripts" / "ops" / "news-grasp-bootstrap.ps1").read_text(
        encoding="utf-8-sig"
    )

    assert not runner_path.exists()
    assert "retired News-Grasp Runner is a legacy tombstone only" in bootstrap
    assert "news-grasp-runner.ps1" in bootstrap
    assert "New-EditorAttemptSnapshot" not in bootstrap
    assert "Remove-EditorAttemptSnapshot" not in bootstrap
    assert '"_ops\\editor-attempt-snapshots\\$DateStamp\\attempt-$Attempt"' not in bootstrap
    assert '"build\\editor-attempt-snapshots\\$DateStamp\\attempt-$Attempt"' not in bootstrap


def test_archive_transaction_resumes_without_recopying_verified_file(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    source = repo / "build" / "codex-recovery-benchmark" / "run" / "events.jsonl"
    source.parent.mkdir(parents=True)
    source.write_text('{"event":"ok"}\n', encoding="utf-8")
    archive_root = repo / "_ops" / "benchmark-archive"
    state_root = repo / "_ops" / "artifact-lifecycle"

    tx = lifecycle.ArchiveTransaction.create(
        repo_root=repo,
        archive_root=archive_root,
        state_root=state_root,
        relative_paths=[source.relative_to(repo)],
        txid="tx-test",
    )
    tx.copy_pending()
    destination = archive_root / "tx-test" / source.relative_to(repo)
    assert destination.read_bytes() == source.read_bytes()

    # crash後の再生成を模擬し、同じjournalから冪等に再開する。
    resumed = lifecycle.ArchiveTransaction.load(state_root=state_root, txid="tx-test")
    resumed.copy_pending()
    resumed.verify_copies()
    resumed.delete_sources()
    resumed.commit()

    manifest = json.loads((state_root / "transactions" / "tx-test.json").read_text(encoding="utf-8"))
    assert manifest["state"] == "committed"
    assert not source.exists()
    assert destination.exists()


def test_copy_uses_bounded_manifest_checkpoints(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    paths: list[Path] = []
    for index in range(205):
        path = repo / "build" / "codex-recovery-benchmark" / f"{index}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(index), encoding="utf-8")
        paths.append(path.relative_to(repo))
    tx = lifecycle.ArchiveTransaction.create(
        repo_root=repo,
        archive_root=repo / "_ops" / "benchmark-archive",
        state_root=repo / "_ops" / "artifact-lifecycle",
        relative_paths=paths,
        txid="checkpoint-test",
    )
    calls = 0
    real_save = tx._save

    def counted_save() -> None:
        nonlocal calls
        calls += 1
        real_save()

    monkeypatch.setattr(tx, "_save", counted_save)
    tx.copy_pending()

    assert calls <= 5


def test_live_lock_owner_blocks_second_transaction(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    with lifecycle.exclusive_lock(state_root, txid="first"):
        with pytest.raises(lifecycle.ArtifactLifecycleBusy):
            with lifecycle.exclusive_lock(state_root, txid="second"):
                raise AssertionError("unreachable")


def test_retention_selects_only_completed_oldest_runs() -> None:
    runs = [
        {"txid": "active", "state": "copying", "created_utc": "2026-01-01T00:00:00Z", "bytes": 100},
        {"txid": "old", "state": "committed", "created_utc": "2026-01-02T00:00:00Z", "bytes": 100},
        {"txid": "new", "state": "committed", "created_utc": "2026-07-15T00:00:00Z", "bytes": 100},
    ]

    selected = lifecycle.select_retention_deletions(
        runs,
        now_utc="2026-07-15T00:00:00Z",
        max_age_days=30,
        max_runs=20,
        max_bytes=5 * 1024**3,
    )

    assert [run["txid"] for run in selected] == ["old"]
