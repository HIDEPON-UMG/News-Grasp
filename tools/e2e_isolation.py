from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any


DATE_RE = re.compile(r"^20\d{2}-\d{2}-\d{2}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


class E2EIsolationError(RuntimeError):
    pass


def _run_git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise E2EIsolationError(f"E2E_ISOLATION_GIT_FAILED: {detail}")
    return completed.stdout.strip()


def _parse_articles(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not raw.strip():
                continue
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError(f"line={line_number} is not object")
            rows.append(value)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise E2EIsolationError("E2E_ISOLATION_ARTICLES_INVALID") from error
    return rows


def _legacy_session_matches(path: Path, issue_date: str) -> bool:
    if not path.is_file():
        return False
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise E2EIsolationError("E2E_ISOLATION_SESSION_URLS_INVALID") from error
    if not isinstance(value, dict) or not isinstance(value.get("date"), str):
        raise E2EIsolationError("E2E_ISOLATION_SESSION_URLS_INVALID")
    return value["date"] == issue_date


def _runner_artifact_predicate(root: Path, issue_date: str) -> bool:
    candidates = [
        *root.glob(f"digest/*/{issue_date}-*.md"),
        root / f"digest/Summary/{issue_date}.md",
        root / f"docs/{issue_date}/index.html",
    ]
    reporter = root / f"build/reporter-artifacts/{issue_date}"
    if reporter.is_dir() and any(reporter.iterdir()):
        return True
    return any(path.exists() for path in candidates)


def sanitize_issue_date(target_root: Path, issue_date: str) -> dict[str, Any]:
    root = Path(target_root).resolve(strict=True)
    if not DATE_RE.fullmatch(issue_date):
        raise E2EIsolationError("E2E_ISOLATION_DATE_INVALID")

    articles_path = root / "data/articles.jsonl"
    articles = _parse_articles(articles_path)
    legacy_session = root / "data/_session_urls.json"
    remove_legacy_session = _legacy_session_matches(legacy_session, issue_date)

    files = {
        *root.glob(f"digest/*/{issue_date}-*.md"),
        root / f"digest/Summary/{issue_date}.md",
        root / f"data/distribution/{issue_date}.json",
        root / f"data/deepdive-provenance/{issue_date}.json",
        *root.glob(f"data/gate_attempts/{issue_date}*.json"),
    }
    directories = {
        root / f"docs/{issue_date}",
        root / f"data/search_audit/{issue_date}",
        root / f"data/_session_urls.d/{issue_date}",
        root / f"build/reporter-artifacts/{issue_date}",
        *root.glob(f"docs/*/{issue_date}"),
    }
    if remove_legacy_session:
        files.add(legacy_session)

    removed: list[str] = []
    for path in sorted(files, key=lambda item: item.as_posix()):
        if path.is_file():
            path.unlink()
            removed.append(path.relative_to(root).as_posix())
    for path in sorted(directories, key=lambda item: item.as_posix(), reverse=True):
        if path.is_dir():
            shutil.rmtree(path)
            removed.append(path.relative_to(root).as_posix() + "/")

    retained_articles = [row for row in articles if row.get("date") != issue_date]
    removed_article_count = len(articles) - len(retained_articles)
    if articles_path.is_file() and removed_article_count:
        text = "".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            for row in retained_articles
        )
        articles_path.write_text(text, encoding="utf-8")
        removed.append("data/articles.jsonl#issue-date-records")

    predicate = _runner_artifact_predicate(root, issue_date)
    if predicate:
        raise E2EIsolationError("E2E_ISOLATION_RUNNER_ARTIFACTS_REMAIN")
    return {
        "schemaVersion": "NEWS_GRASP_E2E_ISOLATION_V1",
        "status": "Green",
        "issueDate": issue_date,
        "targetRoot": str(root),
        "removed": removed,
        "removedArticleCount": removed_article_count,
        "runnerArtifactPredicate": False,
    }


def prepare_isolated_worktree(
    *,
    source_repo: Path,
    target_root: Path,
    allowed_parent: Path,
    issue_date: str,
    expected_commit: str,
) -> dict[str, Any]:
    source = Path(source_repo).resolve(strict=True)
    target = Path(target_root).resolve(strict=False)
    allowed = Path(allowed_parent).resolve(strict=False)
    if target == source:
        raise E2EIsolationError("E2E_ISOLATION_TARGET_INVALID")
    try:
        target.relative_to(allowed)
    except ValueError as error:
        raise E2EIsolationError("E2E_ISOLATION_PATH_ESCAPE") from error
    if target.exists():
        raise E2EIsolationError("E2E_ISOLATION_TARGET_EXISTS")
    if not DATE_RE.fullmatch(issue_date):
        raise E2EIsolationError("E2E_ISOLATION_DATE_INVALID")
    if not COMMIT_RE.fullmatch(expected_commit):
        raise E2EIsolationError("E2E_ISOLATION_COMMIT_INVALID")
    source_commit = _run_git(source, "rev-parse", "HEAD")
    if source_commit != expected_commit:
        raise E2EIsolationError("E2E_ISOLATION_COMMIT_MISMATCH")

    allowed.mkdir(parents=True, exist_ok=True)
    _run_git(source, "worktree", "add", "--detach", str(target), expected_commit)
    try:
        target_commit = _run_git(target, "rev-parse", "HEAD")
        if target_commit != expected_commit:
            raise E2EIsolationError("E2E_ISOLATION_COMMIT_MISMATCH")
        receipt = sanitize_issue_date(target, issue_date)
    except Exception:
        try:
            _run_git(source, "worktree", "remove", "--force", str(target))
        except E2EIsolationError:
            pass
        raise
    receipt.update(
        {
            "sourceRepo": str(source),
            "sourceCommit": source_commit,
            "targetCommit": target_commit,
            "allowedParent": str(allowed),
        }
    )
    return receipt


def _write_receipt(path: Path, value: dict[str, Any]) -> None:
    output = Path(path).resolve(strict=False)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("prepare", nargs="?")
    parser.add_argument("--source-repo", type=Path, required=True)
    parser.add_argument("--target-root", type=Path, required=True)
    parser.add_argument("--allowed-parent", type=Path, required=True)
    parser.add_argument("--issue-date", required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = prepare_isolated_worktree(
            source_repo=args.source_repo,
            target_root=args.target_root,
            allowed_parent=args.allowed_parent,
            issue_date=args.issue_date,
            expected_commit=args.expected_commit,
        )
        _write_receipt(args.receipt, result)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except E2EIsolationError as error:
        print(str(error), file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(_main())
