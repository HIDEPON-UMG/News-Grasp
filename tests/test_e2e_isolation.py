from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from tools.e2e_isolation import (
    E2EIsolationError,
    prepare_isolated_worktree,
    sanitize_issue_date,
)


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.stdout.strip()


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _seed_artifacts(root: Path) -> None:
    for relative in (
        "digest/AI/2026-08-01-AI.md",
        "digest/Summary/2026-08-01.md",
        "digest/AI/2026-07-31-AI.md",
        "docs/2026-08-01/index.html",
        "docs/ai/2026-08-01/index.html",
        "docs/ai/2026-07-31/index.html",
        "data/search_audit/2026-08-01/ai.json",
        "data/search_audit/2026-07-31/ai.json",
        "data/distribution/2026-08-01.json",
        "data/deepdive-provenance/2026-08-01.json",
        "data/_session_urls.d/2026-08-01/ai.json",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("artifact\n", encoding="utf-8")
    _write_jsonl(
        root / "data/articles.jsonl",
        [
            {"date": "2026-07-31", "url": "https://example.com/old"},
            {"date": "2026-08-01", "url": "https://example.com/target"},
        ],
    )


def test_sanitize_removes_every_target_date_surface_and_runner_guard_inputs(
    tmp_path: Path,
) -> None:
    _seed_artifacts(tmp_path)

    receipt = sanitize_issue_date(tmp_path, "2026-08-01")

    assert receipt["status"] == "Green"
    assert not list((tmp_path / "digest").glob("*/2026-08-01*.md"))
    assert not (tmp_path / "docs/2026-08-01").exists()
    assert not list((tmp_path / "docs").glob("*/2026-08-01"))
    assert not (tmp_path / "data/search_audit/2026-08-01").exists()
    assert not (tmp_path / "data/distribution/2026-08-01.json").exists()
    assert not (tmp_path / "data/deepdive-provenance/2026-08-01.json").exists()
    assert not (tmp_path / "data/_session_urls.d/2026-08-01").exists()
    rows = [
        json.loads(line)
        for line in (tmp_path / "data/articles.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    assert {row["date"] for row in rows} == {"2026-07-31"}
    assert receipt["runnerArtifactPredicate"] is False


def test_sanitize_preserves_every_non_target_date_surface(tmp_path: Path) -> None:
    _seed_artifacts(tmp_path)
    before = {
        path.relative_to(tmp_path).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in tmp_path.rglob("*")
        if path.is_file() and "2026-08-01" not in path.as_posix()
        and path.name != "articles.jsonl"
    }

    sanitize_issue_date(tmp_path, "2026-08-01")

    after = {
        path.relative_to(tmp_path).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in tmp_path.rglob("*")
        if path.is_file() and path.name != "articles.jsonl"
    }
    assert before == after


def test_sanitize_fails_closed_before_mutation_on_malformed_jsonl(
    tmp_path: Path,
) -> None:
    _seed_artifacts(tmp_path)
    target = tmp_path / "digest/AI/2026-08-01-AI.md"
    (tmp_path / "data/articles.jsonl").write_text("{broken\n", encoding="utf-8")

    with pytest.raises(E2EIsolationError, match="E2E_ISOLATION_ARTICLES_INVALID"):
        sanitize_issue_date(tmp_path, "2026-08-01")

    assert target.is_file()


def test_prepare_rejects_source_repo_as_target(tmp_path: Path) -> None:
    source = tmp_path / "source"
    allowed = tmp_path / "allowed"
    source.mkdir()
    allowed.mkdir()
    with pytest.raises(E2EIsolationError, match="E2E_ISOLATION_TARGET_INVALID"):
        prepare_isolated_worktree(
            source_repo=source,
            target_root=source,
            allowed_parent=allowed,
            issue_date="2026-08-01",
            expected_commit="a" * 40,
        )


def test_prepare_rejects_target_path_outside_allowed_parent(tmp_path: Path) -> None:
    source = tmp_path / "source"
    allowed = tmp_path / "allowed"
    source.mkdir()
    allowed.mkdir()
    with pytest.raises(E2EIsolationError, match="E2E_ISOLATION_PATH_ESCAPE"):
        prepare_isolated_worktree(
            source_repo=source,
            target_root=tmp_path / "escaped",
            allowed_parent=allowed,
            issue_date="2026-08-01",
            expected_commit="a" * 40,
        )


def test_prepare_rejects_existing_target_without_deleting_it(tmp_path: Path) -> None:
    source = tmp_path / "source"
    allowed = tmp_path / "allowed"
    target = allowed / "existing"
    source.mkdir()
    target.mkdir(parents=True)
    sentinel = target / "keep.txt"
    sentinel.write_text("keep\n", encoding="utf-8")

    with pytest.raises(E2EIsolationError, match="E2E_ISOLATION_TARGET_EXISTS"):
        prepare_isolated_worktree(
            source_repo=source,
            target_root=target,
            allowed_parent=allowed,
            issue_date="2026-08-01",
            expected_commit="a" * 40,
        )
    assert sentinel.read_text(encoding="utf-8") == "keep\n"


def test_prepare_binds_exact_source_and_target_commit(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init")
    _git(source, "config", "user.email", "test@example.com")
    _git(source, "config", "user.name", "Test")
    _seed_artifacts(source)
    _git(source, "add", ".")
    _git(source, "commit", "-m", "seed")
    commit = _git(source, "rev-parse", "HEAD")
    allowed = tmp_path / "allowed"
    target = allowed / "run-1"

    receipt = prepare_isolated_worktree(
        source_repo=source,
        target_root=target,
        allowed_parent=allowed,
        issue_date="2026-08-01",
        expected_commit=commit,
    )

    assert receipt["sourceCommit"] == commit
    assert receipt["targetCommit"] == commit
    assert receipt["runnerArtifactPredicate"] is False
    assert _git(target, "rev-parse", "HEAD") == commit


def test_prepare_leaves_source_worktree_unchanged(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init")
    _git(source, "config", "user.email", "test@example.com")
    _git(source, "config", "user.name", "Test")
    _seed_artifacts(source)
    _git(source, "add", ".")
    _git(source, "commit", "-m", "seed")
    commit = _git(source, "rev-parse", "HEAD")
    source_status_before = _git(source, "status", "--porcelain")

    prepare_isolated_worktree(
        source_repo=source,
        target_root=tmp_path / "allowed/run-1",
        allowed_parent=tmp_path / "allowed",
        issue_date="2026-08-01",
        expected_commit=commit,
    )

    assert _git(source, "status", "--porcelain") == source_status_before


def test_prepare_rejects_commit_mismatch_before_worktree_creation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init")
    _git(source, "config", "user.email", "test@example.com")
    _git(source, "config", "user.name", "Test")
    (source / "tracked.txt").write_text("x\n", encoding="utf-8")
    _git(source, "add", ".")
    _git(source, "commit", "-m", "seed")
    target = tmp_path / "allowed/run-1"

    with pytest.raises(E2EIsolationError, match="E2E_ISOLATION_COMMIT_MISMATCH"):
        prepare_isolated_worktree(
            source_repo=source,
            target_root=target,
            allowed_parent=tmp_path / "allowed",
            issue_date="2026-08-01",
            expected_commit="b" * 40,
        )
    assert not target.exists()
