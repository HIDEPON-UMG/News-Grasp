#!/usr/bin/env python3
"""検証済み newsroom editor 出力を日次成果物へ原子的に反映する。"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

# runner は sitecustomize/PYTHONPATH 注入を遮断するため -I でこの正本を直接実行する。
# import root は実行時 cwd ではなく、検証対象 script 自身の親 repo へ固定する。
_CANONICAL_IMPORT_ROOT = str(Path(__file__).resolve().parents[1])
if _CANONICAL_IMPORT_ROOT not in sys.path:
    sys.path.insert(0, _CANONICAL_IMPORT_ROOT)

from tools.validate_editor_output_preview import validate_editor_output_preview


class MaterializationError(RuntimeError):
    """editor 出力を安全に反映できない場合の typed error。"""


MAX_SOURCE_BYTES = 8 * 1024 * 1024
MAX_SUMMARY_BYTES = 2 * 1024 * 1024
MAX_ARTICLES_BYTES = 128 * 1024 * 1024
MAX_APPEND_RECORDS = 1000
MAX_RECORD_BYTES = 256 * 1024


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_bounded(path: Path, *, limit: int, code: str) -> bytes:
    try:
        with path.open("rb") as handle:
            data = handle.read(limit + 1)
    except OSError as exc:
        raise MaterializationError(f"{code}: {exc}") from exc
    if len(data) > limit:
        raise MaterializationError(code)
    return data


def _validate_issue_date(value: str) -> str:
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise MaterializationError("EDITOR_OUTPUT_ISSUE_DATE_INVALID") from exc
    if parsed.isoformat() != value:
        raise MaterializationError("EDITOR_OUTPUT_ISSUE_DATE_INVALID")
    return value


def _contained_output(repo_root: Path, *parts: str) -> Path:
    root_text = os.path.normcase(os.path.abspath(repo_root))
    candidate_raw = os.path.abspath(repo_root.joinpath(*parts))
    candidate_text = os.path.normcase(candidate_raw)
    try:
        contained = os.path.commonpath((root_text, candidate_text)) == root_text
    except ValueError:
        contained = False
    if not contained or candidate_text == root_text:
        raise MaterializationError("EDITOR_OUTPUT_PATH_INVALID")
    candidate = Path(candidate_raw)
    relative = candidate.relative_to(repo_root)
    current = repo_root
    for part in relative.parts[:-1]:
        current = current / part
        is_junction = getattr(current, "is_junction", lambda: False)
        if current.exists() and (current.is_symlink() or is_junction()):
            raise MaterializationError("EDITOR_OUTPUT_PATH_INVALID")
    return candidate


@contextlib.contextmanager
def _exclusive_lock(path: Path):
    """OS所有のadvisory lock。process crash時もOSが自動解放する。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, candidate = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    candidate_path = Path(candidate)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        candidate_path.replace(path)
    except BaseException:
        candidate_path.unlink(missing_ok=True)
        raise


def _atomic_write_text(path: Path, text: str) -> None:
    _atomic_write_bytes(path, text.encode("utf-8"))


def _load_existing_keys(data: bytes) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for line in data.decode("utf-8-sig").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        keys.add((str(record.get("date") or ""), str(record.get("url") or "")))
    return keys


def _transaction_path(repo_root: Path, issue_date: str) -> Path:
    return _contained_output(
        repo_root, "build", "transactions", f"editor-materialize-{issue_date}"
    )


def _cleanup_transaction(transaction_dir: Path, repo_root: Path) -> None:
    expected_parent = _contained_output(repo_root, "build", "transactions", ".sentinel").parent
    if transaction_dir.parent != expected_parent or transaction_dir.is_symlink():
        raise MaterializationError("EDITOR_OUTPUT_TRANSACTION_INVALID")
    if not transaction_dir.exists():
        return
    for item in transaction_dir.iterdir():
        if (
            item.is_dir()
            or item.is_symlink()
            or (
                item.name not in {"manifest.json", "commit.json"}
                and not item.name.startswith("backup-")
            )
        ):
            raise MaterializationError("EDITOR_OUTPUT_TRANSACTION_INVALID")
        item.unlink()
    transaction_dir.rmdir()


def _recover_pending_transaction(repo_root: Path, issue_date: str) -> None:
    transaction_dir = _transaction_path(repo_root, _validate_issue_date(issue_date))
    if not transaction_dir.exists():
        return
    if not transaction_dir.is_dir() or transaction_dir.is_symlink():
        raise MaterializationError("EDITOR_OUTPUT_TRANSACTION_INVALID")
    manifest_path = transaction_dir / "manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise MaterializationError("EDITOR_OUTPUT_TRANSACTION_INVALID")
    try:
        manifest_bytes = _read_bounded(
            manifest_path,
            limit=1024 * 1024,
            code="EDITOR_OUTPUT_TRANSACTION_INVALID",
        )
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MaterializationError("EDITOR_OUTPUT_TRANSACTION_INVALID") from exc
    entries = manifest.get("entries")
    if (
        manifest.get("schemaVersion") != "EDITOR_MATERIALIZATION_TRANSACTION_V1"
        or manifest.get("issueDate") != issue_date
        or not isinstance(entries, list)
    ):
        raise MaterializationError("EDITOR_OUTPUT_TRANSACTION_INVALID")
    entry_paths = [str(entry.get("path") or "") for entry in entries if isinstance(entry, dict)]
    required_paths = {
        f"build/reporter-artifacts/{issue_date}/editor-output.preview.json",
        f"digest/Summary/{issue_date}.md",
    }
    if (
        len(entry_paths) != len(entries)
        or len(set(entry_paths)) != len(entry_paths)
        or not required_paths.issubset(entry_paths)
    ):
        raise MaterializationError("EDITOR_OUTPUT_TRANSACTION_INVALID")
    commit_path = transaction_dir / "commit.json"
    if commit_path.exists():
        if not commit_path.is_file() or commit_path.is_symlink():
            raise MaterializationError("EDITOR_OUTPUT_TRANSACTION_INVALID")
        try:
            commit = json.loads(
                _read_bounded(
                    commit_path,
                    limit=64 * 1024,
                    code="EDITOR_OUTPUT_TRANSACTION_INVALID",
                ).decode("utf-8")
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MaterializationError("EDITOR_OUTPUT_TRANSACTION_INVALID") from exc
        output_hashes = commit.get("outputSha256")
        if (
            commit.get("schemaVersion") != "EDITOR_MATERIALIZATION_COMMIT_V1"
            or commit.get("manifestSha256") != _sha256_bytes(manifest_bytes)
            or not isinstance(output_hashes, dict)
            or set(output_hashes) != set(entry_paths)
        ):
            raise MaterializationError("EDITOR_OUTPUT_TRANSACTION_INVALID")
        allowed_paths = {
            f"build/reporter-artifacts/{issue_date}/editor-output.preview.json",
            f"digest/Summary/{issue_date}.md",
            "data/articles.jsonl",
        }
        for relative, expected_sha256 in output_hashes.items():
            if relative not in allowed_paths or not re.fullmatch(
                r"[0-9a-f]{64}", str(expected_sha256 or "")
            ):
                raise MaterializationError("EDITOR_OUTPUT_TRANSACTION_INVALID")
            destination = _contained_output(repo_root, *Path(relative).parts)
            if (
                not destination.is_file()
                or destination.is_symlink()
                or _sha256_bytes(
                    _read_bounded(
                        destination,
                        limit=MAX_ARTICLES_BYTES,
                        code="EDITOR_OUTPUT_TRANSACTION_INVALID",
                    )
                )
                != expected_sha256
            ):
                raise MaterializationError("EDITOR_OUTPUT_TRANSACTION_INVALID")
        _cleanup_transaction(transaction_dir, repo_root)
        return

    allowed_paths = {
        f"build/reporter-artifacts/{issue_date}/editor-output.preview.json",
        f"digest/Summary/{issue_date}.md",
        "data/articles.jsonl",
    }
    errors: list[str] = []
    for entry in entries:
        try:
            relative = str(entry.get("path") or "")
            if relative not in allowed_paths:
                raise MaterializationError("path")
            destination = _contained_output(repo_root, *Path(relative).parts)
            if entry.get("existed") is True:
                backup_name = str(entry.get("backup") or "")
                if not re.fullmatch(r"backup-[0-9]+", backup_name):
                    raise MaterializationError("backup")
                backup = transaction_dir / backup_name
                if not backup.is_file() or backup.is_symlink():
                    raise MaterializationError("backup")
                backup_bytes = _read_bounded(
                    backup,
                    limit=MAX_ARTICLES_BYTES,
                    code="EDITOR_OUTPUT_TRANSACTION_INVALID",
                )
                if _sha256_bytes(backup_bytes) != entry.get("backupSha256"):
                    raise MaterializationError("hash")
                _atomic_write_bytes(destination, backup_bytes)
            elif entry.get("existed") is False:
                if destination.exists():
                    if destination.is_dir() or destination.is_symlink():
                        raise MaterializationError("destination")
                    destination.unlink()
            else:
                raise MaterializationError("state")
        except (OSError, MaterializationError, TypeError) as exc:
            errors.append(f"{entry!r}:{exc}")
    if errors:
        raise MaterializationError(
            "EDITOR_OUTPUT_TRANSACTION_RECOVERY_REQUIRED: " + " | ".join(errors)
        )
    _cleanup_transaction(transaction_dir, repo_root)


def _prepare_transaction(
    repo_root: Path, issue_date: str, originals: dict[Path, bytes | None]
) -> tuple[Path, str]:
    transaction_dir = _transaction_path(repo_root, issue_date)
    if transaction_dir.exists():
        raise MaterializationError("EDITOR_OUTPUT_TRANSACTION_INVALID")
    parent = transaction_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    candidate = Path(
        tempfile.mkdtemp(prefix=f".editor-materialize-{issue_date}.", dir=parent)
    )
    try:
        entries: list[dict[str, Any]] = []
        for index, (path, original) in enumerate(originals.items()):
            relative = path.relative_to(repo_root).as_posix()
            row: dict[str, Any] = {"path": relative, "existed": original is not None}
            if original is not None:
                backup_name = f"backup-{index}"
                _atomic_write_bytes(candidate / backup_name, original)
                row.update(
                    {
                        "backup": backup_name,
                        "backupSha256": _sha256_bytes(original),
                    }
                )
            entries.append(row)
        manifest = json.dumps(
            {"schemaVersion": "EDITOR_MATERIALIZATION_TRANSACTION_V1", "issueDate": issue_date, "entries": entries},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        _atomic_write_bytes(candidate / "manifest.json", manifest)
        candidate.replace(transaction_dir)
        return transaction_dir, _sha256_bytes(manifest)
    except BaseException:
        if candidate.exists():
            for item in candidate.iterdir():
                if item.is_file() and not item.is_symlink():
                    item.unlink()
            candidate.rmdir()
        raise


def materialize_editor_output(
    *, source: Path, repo_root: Path, issue_date: str
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    source = source.resolve()
    issue_date = _validate_issue_date(issue_date)
    if not repo_root.is_dir() or repo_root.is_symlink() or not source.is_file():
        raise MaterializationError("EDITOR_OUTPUT_SOURCE_MISSING")

    try:
        source_bytes = _read_bounded(
            source, limit=MAX_SOURCE_BYTES, code="EDITOR_OUTPUT_SOURCE_TOO_LARGE"
        )
        payload = json.loads(source_bytes.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MaterializationError(f"EDITOR_OUTPUT_JSON_INVALID: {exc}") from exc
    if str(payload.get("issue_date") or "") != issue_date:
        raise MaterializationError("EDITOR_OUTPUT_ISSUE_DATE_MISMATCH")

    preview = _contained_output(
        repo_root,
        "build",
        "reporter-artifacts",
        issue_date,
        "editor-output.preview.json",
    )
    preview.parent.mkdir(parents=True, exist_ok=True)
    canonical_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    canonical_json_bytes = canonical_json.encode("utf-8")
    fd, candidate = tempfile.mkstemp(prefix=".editor-output.candidate.", dir=preview.parent)
    candidate_path = Path(candidate)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(canonical_json)
            handle.flush()
            os.fsync(handle.fileno())
        errors = validate_editor_output_preview(
            candidate_path, issue_date=issue_date, repo_root=repo_root
        )
        if errors:
            raise MaterializationError(
                "EDITOR_OUTPUT_SEMANTIC_INVALID: " + " | ".join(errors)
            )
        candidate_path.unlink(missing_ok=True)
    except BaseException:
        candidate_path.unlink(missing_ok=True)
        raise

    summary_bytes = (str(payload.get("summary_markdown") or "").rstrip() + "\n").encode("utf-8")
    if len(summary_bytes) > MAX_SUMMARY_BYTES:
        raise MaterializationError("EDITOR_OUTPUT_SUMMARY_TOO_LARGE")
    raw_records = payload.get("append_records") or []
    if not isinstance(raw_records, list) or len(raw_records) > MAX_APPEND_RECORDS:
        raise MaterializationError("EDITOR_OUTPUT_RECORD_BUDGET_EXCEEDED")
    encoded_records: list[tuple[dict[str, Any], bytes]] = []
    for raw_record in raw_records:
        record = dict(raw_record)
        encoded = (
            json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        if len(encoded) > MAX_RECORD_BYTES:
            raise MaterializationError("EDITOR_OUTPUT_RECORD_BUDGET_EXCEEDED")
        encoded_records.append((record, encoded))

    summary_path = _contained_output(repo_root, "digest", "Summary", f"{issue_date}.md")
    articles_path = _contained_output(repo_root, "data", "articles.jsonl")
    lock_path = _contained_output(repo_root, "build", "locks", "editor-materialize.lock")
    additions: list[dict[str, Any]] = []
    duplicates = 0
    with _exclusive_lock(lock_path):
        _recover_pending_transaction(repo_root, issue_date)
        prior_articles = (
            _read_bounded(
                articles_path,
                limit=MAX_ARTICLES_BYTES,
                code="EDITOR_OUTPUT_ARTICLES_TOO_LARGE",
            )
            if articles_path.exists()
            else b""
        )
        existing_keys = _load_existing_keys(prior_articles)
        append_bytes = bytearray()
        for record, encoded in encoded_records:
            key = (str(record.get("date") or ""), str(record.get("url") or ""))
            if key in existing_keys:
                duplicates += 1
                continue
            existing_keys.add(key)
            additions.append(record)
            append_bytes.extend(encoded)
        if prior_articles and not prior_articles.endswith(b"\n"):
            prior_articles += b"\n"
        next_articles = prior_articles + bytes(append_bytes)
        if len(next_articles) > MAX_ARTICLES_BYTES:
            raise MaterializationError("EDITOR_OUTPUT_ARTICLES_TOO_LARGE")

        updates: list[tuple[Path, bytes]] = [
            (preview, canonical_json_bytes),
            (summary_path, summary_bytes),
        ]
        if additions:
            updates.append((articles_path, next_articles))
        originals = {
            path: (
                _read_bounded(path, limit=MAX_ARTICLES_BYTES, code="EDITOR_OUTPUT_ROLLBACK_INPUT_TOO_LARGE")
                if path.exists()
                else None
            )
            for path, _ in updates
        }
        transaction_dir, manifest_sha256 = _prepare_transaction(
            repo_root, issue_date, originals
        )
        try:
            for path, data in updates:
                _atomic_write_bytes(path, data)
            output_hashes = {
                path.relative_to(repo_root).as_posix(): _sha256_bytes(data)
                for path, data in updates
            }
            commit = json.dumps(
                {
                    "schemaVersion": "EDITOR_MATERIALIZATION_COMMIT_V1",
                    "manifestSha256": manifest_sha256,
                    "outputSha256": output_hashes,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            _atomic_write_bytes(
                transaction_dir / "commit.json",
                commit,
            )
            _cleanup_transaction(transaction_dir, repo_root)
        except BaseException as original_error:
            try:
                _recover_pending_transaction(repo_root, issue_date)
            except BaseException as recovery_error:
                raise MaterializationError(
                    "EDITOR_OUTPUT_TRANSACTION_RECOVERY_REQUIRED: "
                    f"write={original_error}; recovery={recovery_error}"
                ) from recovery_error
            raise
        preview_sha256 = _sha256_bytes(canonical_json_bytes)
        summary_sha256 = _sha256_bytes(summary_bytes)

    def relative(path: Path) -> str:
        try:
            return path.relative_to(repo_root).as_posix()
        except ValueError:
            return path.name

    return {
        "schemaVersion": "NEWS_GRASP_EDITOR_OUTPUT_MATERIALIZATION_V1",
        "status": "materialized_validated_editor_output",
        "issueDate": issue_date,
        "sourcePath": relative(source),
        "sourceSha256": _sha256_bytes(source_bytes),
        "previewPath": relative(preview),
        "previewSha256": preview_sha256,
        "summaryPath": relative(summary_path),
        "summarySha256": summary_sha256,
        "articlesPath": relative(articles_path),
        "appendedCount": len(additions),
        "duplicateCount": duplicates,
    }


def recover_editor_materialization(*, repo_root: Path, issue_date: str) -> dict[str, Any]:
    """外部 editor 起動前に未完の materialization を一意に rollback/commit 確定する。"""
    repo_root = repo_root.resolve()
    issue_date = _validate_issue_date(issue_date)
    if not repo_root.is_dir() or repo_root.is_symlink():
        raise MaterializationError("EDITOR_OUTPUT_REPO_ROOT_INVALID")
    lock_path = _contained_output(repo_root, "build", "locks", "editor-materialize.lock")
    transaction_path = _transaction_path(repo_root, issue_date)
    had_pending_transaction = transaction_path.exists()
    with _exclusive_lock(lock_path):
        _recover_pending_transaction(repo_root, issue_date)
    return {
        "schemaVersion": "NEWS_GRASP_EDITOR_OUTPUT_RECOVERY_V1",
        "status": "transaction_recovered" if had_pending_transaction else "no_pending_transaction",
        "issueDate": issue_date,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--recover-only", action="store_true")
    args = parser.parse_args()
    try:
        if args.recover_only:
            if args.source is not None:
                raise MaterializationError("EDITOR_OUTPUT_RECOVERY_ARGUMENT_INVALID")
            receipt = recover_editor_materialization(
                repo_root=args.repo_root, issue_date=args.date
            )
        else:
            if args.source is None:
                raise MaterializationError("EDITOR_OUTPUT_SOURCE_MISSING")
            receipt = materialize_editor_output(
                source=args.source, repo_root=args.repo_root, issue_date=args.date
            )
    except MaterializationError as exc:
        print(str(exc))
        return 1
    output = json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
    if args.receipt:
        _atomic_write_text(args.receipt.resolve(), output)
    print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
