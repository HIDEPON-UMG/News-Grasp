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
SAFE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
REMOVAL_POLICY_VERSION = "NEWS_GRASP_E2E_ISSUE_REMOVAL_POLICY_V1"


class E2EIsolationError(RuntimeError):
    pass


def isolation_removed_set_sha256(removed: list[str]) -> str:
    payload = json.dumps(
        sorted(removed), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_removed_issue_artifacts(
    removed: object,
    *,
    issue_date: str,
    removed_article_count: object,
) -> list[str]:
    """sanitize receiptの削除対象を当日artifactの閉集合へ制限する。"""

    if not DATE_RE.fullmatch(issue_date):
        raise E2EIsolationError("E2E_ISOLATION_DATE_INVALID")
    if (
        not isinstance(removed, list)
        or not all(isinstance(item, str) and item for item in removed)
        or len(set(removed)) != len(removed)
        or isinstance(removed_article_count, bool)
        or not isinstance(removed_article_count, int)
        or removed_article_count < 0
    ):
        raise E2EIsolationError("E2E_ISOLATION_REMOVAL_SET_INVALID")

    article_marker = "data/articles.jsonl#issue-date-records"
    if (article_marker in removed) != (removed_article_count > 0):
        raise E2EIsolationError("E2E_ISOLATION_REMOVAL_SET_INVALID")
    exact_files = {
        f"digest/Summary/{issue_date}.md",
        f"data/distribution/{issue_date}.json",
        f"data/deepdive-provenance/{issue_date}.json",
        "data/_session_urls.json",
        article_marker,
    }
    exact_directories = {
        f"docs/{issue_date}/",
        f"data/search_audit/{issue_date}/",
        f"data/_session_urls.d/{issue_date}/",
        f"build/reporter-artifacts/{issue_date}/",
    }
    for item in removed:
        if (
            "\\" in item
            or item.startswith(("/", "./"))
            or ":" in item
            or any(part in {"", ".", ".."} for part in item.rstrip("/").split("/"))
        ):
            raise E2EIsolationError("E2E_ISOLATION_REMOVAL_SET_INVALID")
        if item in exact_files or item in exact_directories:
            continue
        parts = item.rstrip("/").split("/")
        allowed = False
        if (
            len(parts) == 3
            and parts[0] == "digest"
            and SAFE_COMPONENT_RE.fullmatch(parts[1])
            and parts[2].startswith(f"{issue_date}-")
            and parts[2].endswith(".md")
            and not item.endswith("/")
        ):
            allowed = True
        elif (
            len(parts) == 3
            and parts[:2] == ["data", "gate_attempts"]
            and parts[2].startswith(issue_date)
            and parts[2].endswith(".json")
            and not item.endswith("/")
        ):
            allowed = True
        elif (
            len(parts) == 3
            and parts[0] == "docs"
            and SAFE_COMPONENT_RE.fullmatch(parts[1])
            and parts[2] == issue_date
            and item.endswith("/")
        ):
            allowed = True
        if not allowed:
            raise E2EIsolationError("E2E_ISOLATION_REMOVAL_SET_INVALID")
    return list(removed)


def _run_git(repo: Path, *args: str) -> str:
    git_env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
    }
    git_env["PYTHONIOENCODING"] = "utf-8"
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=repo,
            env=git_env,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as error:
        raise E2EIsolationError("E2E_ISOLATION_GIT_TIMEOUT") from error
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise E2EIsolationError(f"E2E_ISOLATION_GIT_FAILED: {detail}")
    return completed.stdout.strip()


def _registered_worktrees(repo: Path) -> set[Path]:
    """Gitのporcelain出力から登録済みworktreeの絶対pathだけを得る。"""

    registered: set[Path] = set()
    for line in _run_git(repo, "worktree", "list", "--porcelain").splitlines():
        if not line.startswith("worktree "):
            continue
        raw_path = line.removeprefix("worktree ")
        try:
            registered.add(Path(raw_path).resolve(strict=False))
        except OSError as error:
            raise E2EIsolationError("E2E_ISOLATION_WORKTREE_LIST_INVALID") from error
    return registered


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


def _issue_removal_candidates(
    root: Path,
    issue_date: str,
) -> tuple[set[Path], set[Path], list[dict[str, Any]], int]:
    articles_path = root / "data/articles.jsonl"
    articles = _parse_articles(articles_path)
    legacy_session = root / "data/_session_urls.json"
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
    if _legacy_session_matches(legacy_session, issue_date):
        files.add(legacy_session)
    removed_article_count = sum(row.get("date") == issue_date for row in articles)
    return files, directories, articles, removed_article_count


def expected_issue_removals(root: Path, issue_date: str) -> tuple[list[str], int]:
    source = Path(root).resolve(strict=True)
    files, directories, _articles, removed_article_count = _issue_removal_candidates(
        source, issue_date
    )
    removed = [
        path.relative_to(source).as_posix()
        for path in sorted(files, key=lambda item: item.as_posix())
        if path.is_file()
    ]
    removed.extend(
        path.relative_to(source).as_posix() + "/"
        for path in sorted(directories, key=lambda item: item.as_posix(), reverse=True)
        if path.is_dir()
    )
    if removed_article_count:
        removed.append("data/articles.jsonl#issue-date-records")
    validate_removed_issue_artifacts(
        removed,
        issue_date=issue_date,
        removed_article_count=removed_article_count,
    )
    return removed, removed_article_count


def validate_sanitized_issue_transform(
    *,
    source_root: Path,
    target_root: Path,
    issue_date: str,
    removed: list[str],
    removed_article_count: int,
) -> None:
    source = Path(source_root).resolve(strict=True)
    target = Path(target_root).resolve(strict=True)
    expected_removed, expected_count = expected_issue_removals(source, issue_date)
    if removed != expected_removed or removed_article_count != expected_count:
        raise E2EIsolationError("E2E_ISOLATION_EXACT_TRANSFORM_INVALID")
    for item in expected_removed:
        if item == "data/articles.jsonl#issue-date-records":
            continue
        if (target / item.rstrip("/")).exists():
            raise E2EIsolationError("E2E_ISOLATION_EXACT_TRANSFORM_INVALID")
    if expected_count:
        source_rows = _parse_articles(source / "data/articles.jsonl")
        retained = [row for row in source_rows if row.get("date") != issue_date]
        expected_text = "".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            for row in retained
        )
        try:
            actual_text = (target / "data/articles.jsonl").read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise E2EIsolationError("E2E_ISOLATION_EXACT_TRANSFORM_INVALID") from error
        if actual_text != expected_text:
            raise E2EIsolationError("E2E_ISOLATION_EXACT_TRANSFORM_INVALID")


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
    files, directories, articles, removed_article_count = _issue_removal_candidates(
        root, issue_date
    )

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
        "removalPolicyVersion": REMOVAL_POLICY_VERSION,
        "removedSetSha256": isolation_removed_set_sha256(removed),
        "runnerArtifactPredicate": False,
    }


def _prepare_isolated_worktree_impl(
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
    add_attempted = False
    try:
        add_attempted = True
        _run_git(source, "worktree", "add", "--detach", str(target), expected_commit)
        target_commit = _run_git(target, "rev-parse", "HEAD")
        if target_commit != expected_commit:
            raise E2EIsolationError("E2E_ISOLATION_COMMIT_MISMATCH")
        receipt = sanitize_issue_date(target, issue_date)
        validate_sanitized_issue_transform(
            source_root=source,
            target_root=target,
            issue_date=issue_date,
            removed=list(receipt["removed"]),
            removed_article_count=int(receipt["removedArticleCount"]),
        )
    except Exception as error:
        cleanup_failed = False
        if add_attempted:
            try:
                _run_git(source, "worktree", "remove", "--force", str(target))
            except Exception:
                try:
                    _run_git(
                        source,
                        "worktree",
                        "remove",
                        "--force",
                        "--force",
                        str(target),
                    )
                except Exception:
                    cleanup_failed = True
            try:
                target_registered = target in _registered_worktrees(source)
            except Exception:
                target_registered = True
            if target.exists() or target_registered:
                cleanup_failed = True
        if cleanup_failed:
            raise E2EIsolationError("E2E_ISOLATION_CLEANUP_REQUIRED") from error
        if isinstance(error, E2EIsolationError):
            raise
        raise E2EIsolationError("E2E_ISOLATION_PREPARE_FAILED") from error
    receipt.update(
        {
            "sourceRepo": str(source),
            "sourceCommit": source_commit,
            "targetCommit": target_commit,
            "allowedParent": str(allowed),
        }
    )
    return receipt


def prepare_isolated_worktree(
    *,
    source_repo: Path,
    target_root: Path,
    allowed_parent: Path,
    issue_date: str,
    expected_commit: str,
) -> dict[str, Any]:
    """隔離作成全体をtyped failure境界で包む。"""

    try:
        return _prepare_isolated_worktree_impl(
            source_repo=source_repo,
            target_root=target_root,
            allowed_parent=allowed_parent,
            issue_date=issue_date,
            expected_commit=expected_commit,
        )
    except E2EIsolationError:
        raise
    except Exception as error:
        raise E2EIsolationError("E2E_ISOLATION_PREPARE_FAILED") from error


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
