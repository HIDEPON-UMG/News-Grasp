from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Sequence


SCHEMA = "news-grasp-artifact-lifecycle-v1"
LOCK_SCHEMA = "news-grasp-artifact-lock-v1"
DEFAULT_ARCHIVE_HEADROOM = 1024**3
KNOWN_UNTRACKED_PREFIXES = (
    "build/codex-recovery-benchmark/",
    "build/external-benchmark-matrix/",
    "build/editor-attempt-snapshots/",
    "build/incident-render/",
    "build/editor-cross-",
)


class ArtifactLifecycleBusy(RuntimeError):
    pass


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _canonical_relative(path: Path) -> Path:
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe relative path: {path}")
    return Path(*path.parts)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def default_raw_root(runner_name: str) -> Path:
    if not runner_name or any(char in runner_name for char in "/\\"):
        raise ValueError(f"invalid runner name: {runner_name!r}")
    return Path("_ops") / "benchmark-runs" / runner_name


def validate_raw_output_path(repo_root: Path, output_path: Path) -> Path:
    repo = repo_root.resolve()
    resolved = output_path.resolve() if output_path.is_absolute() else (repo / output_path).resolve()
    allowed = (repo / "_ops" / "benchmark-runs").resolve()
    if resolved == allowed or not _is_relative_to(resolved, allowed):
        raise ValueError(f"raw output must be under {allowed}: {resolved}")
    return resolved


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    _replace_with_retry(temporary, path)


def _replace_with_retry(source: Path, destination: Path, *, attempts: int = 10) -> None:
    for attempt in range(attempts):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if attempt + 1 >= attempts:
                raise
            time.sleep(min(0.05 * (2**attempt), 1.0))


def _append_journal(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


@contextlib.contextmanager
def exclusive_lock(state_root: Path, *, txid: str) -> Iterator[Path]:
    state_root.mkdir(parents=True, exist_ok=True)
    lock_path = state_root / "lock.json"
    payload = {
        "schema": LOCK_SCHEMA,
        "pid": os.getpid(),
        "txid": txid,
        "created_utc": _utc_now(),
        "command_line": sys.argv,
    }
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ArtifactLifecycleBusy(f"artifact lifecycle lock is already held: {lock_path}") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        yield lock_path
    finally:
        try:
            current = json.loads(lock_path.read_text(encoding="utf-8"))
            if current.get("pid") == os.getpid() and current.get("txid") == txid:
                lock_path.unlink()
        except (FileNotFoundError, json.JSONDecodeError):
            pass


def _manifest_path(state_root: Path, txid: str) -> Path:
    return state_root / "transactions" / f"{txid}.json"


def _journal_path(state_root: Path, txid: str) -> Path:
    return state_root / "journal" / f"{txid}.jsonl"


@dataclass
class ArchiveTransaction:
    repo_root: Path
    archive_root: Path
    state_root: Path
    txid: str
    manifest: dict[str, Any]

    @classmethod
    def create(
        cls,
        *,
        repo_root: Path,
        archive_root: Path,
        state_root: Path,
        relative_paths: Sequence[Path],
        txid: str | None = None,
    ) -> "ArchiveTransaction":
        repo = repo_root.resolve()
        archive = archive_root.resolve() if archive_root.is_absolute() else (repo / archive_root).resolve()
        state = state_root.resolve() if state_root.is_absolute() else (repo / state_root).resolve()
        identifier = txid or dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        entries: list[dict[str, Any]] = []
        for item in sorted({_canonical_relative(Path(value)) for value in relative_paths}, key=lambda value: value.as_posix()):
            source = repo / item
            if not source.is_file():
                raise FileNotFoundError(source)
            entries.append(
                {
                    "relative_path": item.as_posix(),
                    "bytes": source.stat().st_size,
                    "sha256": sha256_file(source),
                    "status": "planned",
                }
            )
        payload = {
            "schema": SCHEMA,
            "txid": identifier,
            "state": "inventory_complete",
            "created_utc": _utc_now(),
            "repo_root": str(repo),
            "archive_root": str(archive),
            "source_count": len(entries),
            "source_bytes": sum(int(entry["bytes"]) for entry in entries),
            "entries": entries,
        }
        transaction = cls(repo, archive, state, identifier, payload)
        transaction._save()
        return transaction

    @classmethod
    def load(cls, *, state_root: Path, txid: str) -> "ArchiveTransaction":
        state = state_root.resolve()
        payload = json.loads(_manifest_path(state, txid).read_text(encoding="utf-8"))
        if payload.get("schema") != SCHEMA:
            raise ValueError(f"unsupported manifest schema: {payload.get('schema')}")
        return cls(
            Path(payload["repo_root"]).resolve(),
            Path(payload["archive_root"]).resolve(),
            state,
            txid,
            payload,
        )

    @property
    def journal_path(self) -> Path:
        return _journal_path(self.state_root, self.txid)

    def _save(self) -> None:
        atomic_write_json(_manifest_path(self.state_root, self.txid), self.manifest)

    def _event(self, action: str, entry: dict[str, Any]) -> None:
        _append_journal(
            self.journal_path,
            {
                "at": _utc_now(),
                "action": action,
                "relative_path": entry["relative_path"],
                "sha256": entry["sha256"],
                "bytes": entry["bytes"],
            },
        )

    def ensure_capacity(self, *, headroom: int = DEFAULT_ARCHIVE_HEADROOM) -> None:
        free = shutil.disk_usage(self.archive_root.parent if self.archive_root.parent.exists() else self.repo_root).free
        required = int(self.manifest["source_bytes"]) * 2 + headroom
        if free < required:
            self.manifest["state"] = "paused_capacity"
            self.manifest["free_bytes"] = free
            self.manifest["required_bytes"] = required
            self._save()
            raise OSError(f"archive capacity gate failed: free={free} required={required}")

    def copy_pending(self) -> None:
        self.manifest["state"] = "copying"
        self._save()
        for index, entry in enumerate(self.manifest["entries"], start=1):
            source = self.repo_root / Path(entry["relative_path"])
            destination = self.archive_root / self.txid / Path(entry["relative_path"])
            if destination.is_file() and destination.stat().st_size == entry["bytes"] and sha256_file(destination) == entry["sha256"]:
                entry["status"] = "copied"
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
            shutil.copy2(source, temporary)
            if temporary.stat().st_size != entry["bytes"] or sha256_file(temporary) != entry["sha256"]:
                temporary.unlink(missing_ok=True)
                raise OSError(f"archive copy verification failed: {entry['relative_path']}")
            _replace_with_retry(temporary, destination)
            entry["status"] = "copied"
            self._event("copied", entry)
            if index % 100 == 0:
                self._save()
        self._save()

    def verify_copies(self) -> None:
        for entry in self.manifest["entries"]:
            destination = self.archive_root / self.txid / Path(entry["relative_path"])
            if not destination.is_file() or destination.stat().st_size != entry["bytes"] or sha256_file(destination) != entry["sha256"]:
                raise OSError(f"archive verification failed: {entry['relative_path']}")
            entry["status"] = "verified"
            self._event("verified", entry)
        self.manifest["state"] = "copied_verified"
        self._save()

    def delete_sources(self) -> None:
        if self.manifest.get("state") != "copied_verified":
            raise RuntimeError(f"delete requires copied_verified: {self.manifest.get('state')}")
        self.manifest["state"] = "deleting"
        self._save()
        for index, entry in enumerate(self.manifest["entries"], start=1):
            source = self.repo_root / Path(entry["relative_path"])
            destination = self.archive_root / self.txid / Path(entry["relative_path"])
            if not source.exists():
                entry["status"] = "deleted"
                continue
            if sha256_file(source) != entry["sha256"] or sha256_file(destination) != entry["sha256"]:
                raise OSError(f"source/archive drift before delete: {entry['relative_path']}")
            source.unlink()
            entry["status"] = "deleted"
            self._event("deleted", entry)
            if index % 100 == 0:
                self._save()
        self._save()

    def commit(self) -> None:
        if any(entry.get("status") != "deleted" for entry in self.manifest["entries"]):
            raise RuntimeError("commit requires every source to be deleted")
        self.manifest["state"] = "committed"
        self.manifest["committed_utc"] = _utc_now()
        self._save()

    def rollback(self) -> None:
        for entry in reversed(self.manifest["entries"]):
            source = self.repo_root / Path(entry["relative_path"])
            archive = self.archive_root / self.txid / Path(entry["relative_path"])
            if source.exists():
                if sha256_file(source) != entry["sha256"]:
                    raise OSError(f"source drift blocks rollback: {entry['relative_path']}")
                continue
            if not archive.is_file() or sha256_file(archive) != entry["sha256"]:
                raise OSError(f"archive drift blocks rollback: {entry['relative_path']}")
            source.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(archive, source)
            if sha256_file(source) != entry["sha256"]:
                raise OSError(f"rollback verification failed: {entry['relative_path']}")
            self._event("restored", entry)
            entry["status"] = "restored"
        self.manifest["state"] = "rolled_back"
        self.manifest["rolled_back_utc"] = _utc_now()
        self._save()


def select_retention_deletions(
    runs: Sequence[dict[str, Any]],
    *,
    now_utc: str,
    max_age_days: int,
    max_runs: int,
    max_bytes: int,
) -> list[dict[str, Any]]:
    now = _parse_utc(now_utc)
    completed = sorted(
        (dict(run) for run in runs if run.get("state") == "committed"),
        key=lambda run: (str(run.get("created_utc", "")), str(run.get("txid", ""))),
    )
    selected: dict[str, dict[str, Any]] = {}
    cutoff = now - dt.timedelta(days=max_age_days)
    for run in completed:
        if _parse_utc(str(run["created_utc"])) < cutoff:
            selected[str(run["txid"])] = run
    remaining = [run for run in completed if str(run["txid"]) not in selected]
    while len(remaining) > max_runs:
        run = remaining.pop(0)
        selected[str(run["txid"])] = run
    total = sum(int(run.get("bytes", 0)) for run in remaining)
    while total > max_bytes and remaining:
        run = remaining.pop(0)
        selected[str(run["txid"])] = run
        total -= int(run.get("bytes", 0))
    return sorted(selected.values(), key=lambda run: (str(run.get("created_utc", "")), str(run.get("txid", ""))))


def _run_git(repo_root: Path, args: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def git_untracked(repo_root: Path) -> list[Path]:
    result = _run_git(repo_root, ["ls-files", "--others", "--exclude-standard", "-z"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace"))
    return [Path(value.decode("utf-8", errors="surrogateescape")) for value in result.stdout.split(b"\0") if value]


def classify_untracked(paths: Sequence[Path]) -> tuple[list[Path], list[Path]]:
    candidates: list[Path] = []
    held: list[Path] = []
    for path in paths:
        normalized = path.as_posix()
        if normalized.startswith(KNOWN_UNTRACKED_PREFIXES):
            candidates.append(path)
        else:
            held.append(path)
    return candidates, held


def inventory(repo_root: Path) -> dict[str, Any]:
    paths = git_untracked(repo_root)
    candidates, held = classify_untracked(paths)
    candidate_bytes = sum((repo_root / path).stat().st_size for path in candidates if (repo_root / path).is_file())
    return {
        "schema": SCHEMA,
        "created_utc": _utc_now(),
        "repo_root": str(repo_root.resolve()),
        "untracked_count": len(paths),
        "candidate_count": len(candidates),
        "candidate_bytes": candidate_bytes,
        "candidates": [path.as_posix() for path in candidates],
        "held": [path.as_posix() for path in held],
    }


def _check_ignored(repo_root: Path, path: Path) -> None:
    result = _run_git(repo_root, ["check-ignore", "-q", "--", path.as_posix()])
    if result.returncode != 0:
        raise RuntimeError(f"archive/state path is not ignored: {path}")


def _command_inventory(args: argparse.Namespace) -> int:
    payload = inventory(args.repo_root)
    atomic_write_json(args.output, payload)
    print(json.dumps({key: payload[key] for key in ("untracked_count", "candidate_count", "candidate_bytes")}, ensure_ascii=False))
    return 0


def _command_archive(args: argparse.Namespace) -> int:
    payload = json.loads(args.inventory.read_text(encoding="utf-8"))
    repo_root = args.repo_root.resolve()
    current = inventory(repo_root)
    if current["candidates"] != payload["candidates"]:
        raise RuntimeError("candidate set drifted after inventory; regenerate inventory")
    txid = args.txid or dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive_root = args.archive_root.resolve() if args.archive_root.is_absolute() else (repo_root / args.archive_root).resolve()
    state_root = args.state_root.resolve() if args.state_root.is_absolute() else (repo_root / args.state_root).resolve()
    _check_ignored(repo_root, archive_root.relative_to(repo_root))
    _check_ignored(repo_root, state_root.relative_to(repo_root))
    with exclusive_lock(state_root, txid=txid):
        transaction = ArchiveTransaction.create(
            repo_root=repo_root,
            archive_root=archive_root,
            state_root=state_root,
            relative_paths=[Path(value) for value in payload["candidates"]],
            txid=txid,
        )
        transaction.ensure_capacity()
        transaction.copy_pending()
        transaction.verify_copies()
        transaction.delete_sources()
        transaction.commit()
    print(json.dumps({"txid": txid, "state": "committed", "count": len(payload["candidates"])}, ensure_ascii=False))
    return 0


def _command_resume(args: argparse.Namespace) -> int:
    state_root = args.state_root.resolve()
    with exclusive_lock(state_root, txid=args.txid):
        transaction = ArchiveTransaction.load(state_root=state_root, txid=args.txid)
        transaction.copy_pending()
        transaction.verify_copies()
        transaction.delete_sources()
        transaction.commit()
    print(json.dumps({"txid": args.txid, "state": "committed"}, ensure_ascii=False))
    return 0


def _command_rollback(args: argparse.Namespace) -> int:
    state_root = args.state_root.resolve()
    with exclusive_lock(state_root, txid=args.txid):
        transaction = ArchiveTransaction.load(state_root=state_root, txid=args.txid)
        transaction.rollback()
    print(json.dumps({"txid": args.txid, "state": "rolled_back"}, ensure_ascii=False))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="News-Grasp local artifact lifecycle")
    subparsers = parser.add_subparsers(dest="command", required=True)
    inventory_parser = subparsers.add_parser("inventory")
    inventory_parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    inventory_parser.add_argument("--output", type=Path, required=True)
    inventory_parser.set_defaults(handler=_command_inventory)
    archive_parser = subparsers.add_parser("archive")
    archive_parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    archive_parser.add_argument("--inventory", type=Path, required=True)
    archive_parser.add_argument("--state-root", type=Path, default=Path("_ops/artifact-lifecycle"))
    archive_parser.add_argument("--archive-root", type=Path, default=Path("_ops/benchmark-archive"))
    archive_parser.add_argument("--txid")
    archive_parser.set_defaults(handler=_command_archive)
    for name, handler in (("resume", _command_resume), ("rollback", _command_rollback)):
        command_parser = subparsers.add_parser(name)
        command_parser.add_argument("--state-root", type=Path, required=True)
        command_parser.add_argument("--txid", required=True)
        command_parser.set_defaults(handler=handler)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except ArtifactLifecycleBusy as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except OSError as exc:
        print(str(exc), file=sys.stderr)
        return 7
    except (RuntimeError, ValueError, FileNotFoundError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
