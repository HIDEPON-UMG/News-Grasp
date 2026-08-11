"""News-Grasp product-local change admission の単一consumer。"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, NoReturn


SCHEMA = "NEWS_GRASP_PRODUCT_WRITE_ALLOWLIST_V1"
PACKET_SCHEMA = "NEWS_GRASP_CHANGE_PACKET_V1"
SNAPSHOT_SCHEMA = "NEWS_GRASP_CHANGE_SNAPSHOT_V1"
PRODUCT_ID = "News-Grasp"
EXIT_CONCURRENT_OWNER = 73
EXIT_REJECTED = 74


class NewsGraspChangeControlError(RuntimeError):
    """変更入口の拒否理由を機械的に保持する。"""

    def __init__(self, code: str, detail: str | None = None) -> None:
        super().__init__(code if detail is None else f"{code}:{detail}")
        self.code = code
        self.detail = detail


def _fail(code: str, detail: str | None = None) -> NoReturn:
    raise NewsGraspChangeControlError(code, detail)


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    try:
        with path.open("rb") as stream:
            digest = hashlib.sha256()
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
            return digest.hexdigest()
    except OSError as exc:
        _fail("NG_TARGET_READ_FAILED", str(exc))


def _is_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & 0x400)


def _repo_root(value: Path | str) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        _fail("NG_REPO_ROOT_INVALID")
    if _is_reparse(candidate):
        _fail("NG_REPARSE_COMPONENT")
    try:
        root = candidate.resolve(strict=True)
    except OSError as exc:
        _fail("NG_REPO_ROOT_INVALID", str(exc))
    if not root.is_dir():
        _fail("NG_REPO_ROOT_INVALID")
    return root


def _relative_path(repo: Path, value: str, *, must_exist: bool = False) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        _fail("NG_CHANGE_PATH_INVALID")
    normalized = value.replace("\\", "/")
    if normalized.startswith(("/", "//")) or ":" in normalized:
        _fail("NG_CHANGE_PATH_INVALID")
    parts = normalized.split("/")
    if any(part in ("", ".", "..") for part in parts):
        _fail("NG_CHANGE_PATH_INVALID")
    if any(part.startswith("\\") for part in parts):
        _fail("NG_CHANGE_PATH_INVALID")
    candidate = repo.joinpath(*parts)
    current = repo
    for part in parts:
        current = current / part
        if _is_reparse(current):
            _fail("NG_REPARSE_COMPONENT", normalized)
    try:
        resolved = candidate.resolve(strict=must_exist)
    except OSError as exc:
        _fail("NG_CHANGE_PATH_INVALID", str(exc))
    try:
        resolved.relative_to(repo)
    except ValueError:
        _fail("NG_CHANGE_PATH_INVALID")
    if must_exist and not resolved.is_file():
        _fail("NG_CHANGE_PATH_INVALID")
    if resolved.exists():
        try:
            if resolved.stat(follow_symlinks=False).st_nlink > 1:
                _fail("NG_HARDLINK_TARGET")
        except OSError as exc:
            _fail("NG_CHANGE_PATH_INVALID", str(exc))
    return candidate


def _allowlist(repo: Path) -> list[str]:
    path = repo / "config/news_grasp_product_write_allowlist_v1.json"
    if _is_reparse(path) or not path.is_file():
        _fail("NG_ALLOWLIST_INVALID")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail("NG_ALLOWLIST_INVALID", str(exc))
    if value.get("schemaVersion") != SCHEMA or value.get("productId") != PRODUCT_ID:
        _fail("NG_ALLOWLIST_INVALID")
    paths = value.get("allowedPaths")
    if not isinstance(paths, list) or not paths or any(not isinstance(item, str) for item in paths):
        _fail("NG_ALLOWLIST_INVALID")
    if len({item.casefold() for item in paths}) != len(paths):
        _fail("NG_ALLOWLIST_INVALID")
    for item in paths:
        _relative_path(repo, item)
    return paths


def _git(repo: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError:
        return "unavailable"
    return result.stdout.strip() if result.returncode == 0 else "unavailable"


def _state_root(repo: Path) -> Path:
    local = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir()))
    key = _sha256_bytes(str(repo).casefold().encode("utf-8"))[:32]
    root = local / "NewsGrasp" / "change-control" / key
    root.mkdir(parents=True, exist_ok=True)
    return root


@contextmanager
def _named_mutex(repo: Path) -> Iterator[None]:
    """Windows named mutex、その他OSでは排他的ロックファイルで直列化する。"""

    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, False, "Global\\NewsGraspProductChangeV1")
        if not handle:
            _fail("NG_MUTEX_UNAVAILABLE")
        try:
            wait = kernel32.WaitForSingleObject(handle, 30 * 60 * 1000)
            if wait not in (0, 0x80):
                _fail("NG_MUTEX_TIMEOUT")
            yield
        finally:
            kernel32.ReleaseMutex(handle)
            kernel32.CloseHandle(handle)
        return
    lock = _state_root(repo) / "change.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        _fail("NG_MUTEX_BUSY")
    try:
        os.write(fd, str(os.getpid()).encode("ascii"))
        yield
    finally:
        os.close(fd)
        lock.unlink(missing_ok=True)


def _owner_path(repo: Path) -> Path:
    return _state_root(repo) / "active-owner.json"


def _claim_owner(repo: Path, owner: str) -> None:
    if not isinstance(owner, str) or not owner:
        _fail("NG_OWNER_INVALID")
    owner_path = _owner_path(repo)
    if owner_path.exists():
        try:
            current = json.loads(owner_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            _fail("NG_CONCURRENT_OWNER_PRESENT")
        if current.get("ownerThreadId") != owner:
            _fail("NG_CONCURRENT_OWNER_PRESENT")
        return
    payload = {"ownerThreadId": owner, "pid": os.getpid(), "claimedAt": time.time()}
    try:
        with owner_path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        _fail("NG_CONCURRENT_OWNER_PRESENT")


def _manifest(value: Path | str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        loaded = json.loads(Path(value).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail("NG_MANIFEST_INVALID", str(exc))
    if not isinstance(loaded, dict):
        _fail("NG_MANIFEST_INVALID")
    return loaded


def _target_hashes(repo: Path, paths: list[str]) -> dict[str, str | None]:
    hashes: dict[str, str | None] = {}
    for relative in paths:
        target = _relative_path(repo, relative)
        hashes[relative] = _sha256_file(target) if target.is_file() else None
    return hashes


def snapshot(
    *, repo_root: Path | str, target_manifest: Path | str | dict[str, Any], output: Path | str
) -> dict[str, Any]:
    """全targetのbaselineとownerを一回で封印する。"""

    repo = _repo_root(repo_root)
    manifest = _manifest(target_manifest)
    paths = manifest.get("targets")
    owner = manifest.get("ownerThreadId")
    if not isinstance(paths, list) or not paths or any(not isinstance(item, str) for item in paths):
        _fail("NG_MANIFEST_INVALID")
    allowed = _allowlist(repo)
    if paths != allowed:
        _fail("NG_ALLOWLIST_SCOPE_INVALID")
    with _named_mutex(repo):
        _claim_owner(repo, owner)
        result: dict[str, Any] = {
            "schemaVersion": SNAPSHOT_SCHEMA,
            "productId": PRODUCT_ID,
            "repoRoot": str(repo),
            "ownerThreadId": owner,
            "targetPaths": paths,
            "targetHashes": _target_hashes(repo, paths),
            "gitHead": _git(repo, "rev-parse", "HEAD"),
            "gitRemoteHead": _git(repo, "ls-remote", "origin", "refs/heads/main"),
            "gitWorktrees": _git(repo, "worktree", "list", "--porcelain"),
        }
        result["snapshotSha256"] = _sha256_bytes(_canonical_json(result))
        destination = Path(output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(result, stream, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        return result


def _validate_packet_shape(packet: dict[str, Any], allowed: list[str]) -> None:
    if packet.get("schemaVersion") != PACKET_SCHEMA:
        _fail("NG_PACKET_CONTRACT_INVALID")
    executor = packet.get("executor")
    if executor != {"model": "gpt-5.6-luna", "reasoningEffort": "max", "noSubstitution": True}:
        _fail("NG_PACKET_CONTRACT_INVALID")
    if packet.get("unresolvedDecisionIds") != []:
        _fail("NG_PACKET_CONTRACT_INVALID")
    if packet.get("allowedWriteSet") != allowed:
        _fail("NG_PACKET_CONTRACT_INVALID")
    changes = packet.get("changes")
    if not isinstance(changes, list) or not changes:
        _fail("NG_PACKET_CONTRACT_INVALID")
    seen: set[str] = set()
    for change in changes:
        if not isinstance(change, dict) or not isinstance(change.get("path"), str):
            _fail("NG_PACKET_CONTRACT_INVALID")
        path = change["path"]
        if path in seen or path not in allowed:
            _fail("NG_PACKET_CONTRACT_INVALID")
        seen.add(path)
        if change.get("operation") != "replace" or not isinstance(change.get("content"), str):
            _fail("NG_PACKET_CONTRACT_INVALID")


def validate_packet(*, repo_root: Path | str, packet: Path | str | dict[str, Any]) -> dict[str, Any]:
    """packet、owner、allowlist、全target baselineをmutation前に再確認する。"""

    repo = _repo_root(repo_root)
    value = _manifest(packet)
    allowed = _allowlist(repo)
    for change in value.get("changes", []):
        if isinstance(change, dict) and isinstance(change.get("path"), str):
            _relative_path(repo, change["path"])
    _validate_packet_shape(value, allowed)
    if value.get("repoRoot") != str(repo):
        _fail("NG_PACKET_CONTRACT_INVALID")
    owner = value.get("ownerThreadId")
    snapshot_value = _manifest(value.get("snapshotPath", ""))
    if snapshot_value.get("repoRoot") != str(repo) or snapshot_value.get("ownerThreadId") != owner:
        _fail("NG_PACKET_CONTRACT_INVALID")
    if snapshot_value.get("targetPaths") != allowed:
        _fail("NG_PACKET_CONTRACT_INVALID")
    current = _target_hashes(repo, allowed)
    if snapshot_value.get("targetHashes") != current:
        _fail("NG_BASELINE_DRIFT")
    for change in value["changes"]:
        _relative_path(repo, change["path"])
    return {
        "status": "validated",
        "packetId": value.get("packetId"),
        "ownerThreadId": owner,
        "allowedWriteSet": allowed,
        "executor": value["executor"],
        "unresolvedDecisionIds": [],
        "baselineSha256": _sha256_bytes(_canonical_json(snapshot_value)),
    }


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def apply_packet(*, repo_root: Path | str, packet: Path | str | dict[str, Any]) -> dict[str, Any]:
    """validated packetをjournal付きatomic replaceで適用する。"""

    repo = _repo_root(repo_root)
    value = _manifest(packet)
    with _named_mutex(repo):
        validation = validate_packet(repo_root=repo, packet=value)
        state = _state_root(repo)
        transactions = state / "transactions"
        transactions.mkdir(parents=True, exist_ok=True)
        packet_id = str(value.get("packetId", ""))
        if not packet_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in packet_id):
            _fail("NG_PACKET_CONTRACT_INVALID")
        journal = transactions / f"{packet_id}.json"
        if journal.exists():
            _fail("NG_PACKET_REPLAY")
        originals: dict[str, str] = {}
        for change in value["changes"]:
            target = _relative_path(repo, change["path"])
            originals[change["path"]] = base64.b64encode(target.read_bytes() if target.exists() else b"").decode("ascii")
        prepared = {
            "schemaVersion": "NEWS_GRASP_CHANGE_TRANSACTION_V1",
            "packetId": packet_id,
            "status": "prepared",
            "repoRoot": str(repo),
            "originals": originals,
            "changes": value["changes"],
        }
        with journal.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(prepared, stream, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            for change in value["changes"]:
                target = _relative_path(repo, change["path"])
                _atomic_write(target, change["content"].encode("utf-8"))
            verify_result = verify(repo_root=repo, packet=value)
            prepared["status"] = "committed"
            prepared["result"] = verify_result
            _atomic_write(journal, _canonical_json(prepared) + b"\n")
            return {"status": "applied", "packetId": packet_id, "verification": verify_result, **validation}
        except Exception:
            for relative, encoded in originals.items():
                _atomic_write(_relative_path(repo, relative), base64.b64decode(encoded))
            prepared["status"] = "rolled_back"
            _atomic_write(journal, _canonical_json(prepared) + b"\n")
            raise


def verify(*, repo_root: Path | str, packet: Path | str | dict[str, Any]) -> dict[str, Any]:
    repo = _repo_root(repo_root)
    value = _manifest(packet)
    paths = [change.get("path") for change in value.get("changes", [])]
    if any(not isinstance(path, str) for path in paths):
        _fail("NG_PACKET_CONTRACT_INVALID")
    hashes = {path: _sha256_file(_relative_path(repo, path, must_exist=True)) for path in paths}
    expected = {
        path: _sha256_bytes(change["content"].encode("utf-8"))
        for path, change in ((item["path"], item) for item in value["changes"])
    }
    if hashes != expected:
        _fail("NG_VERIFY_FAILED")
    return {"status": "verified", "targetHashes": hashes}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("snapshot", "validate-packet", "apply-packet", "verify"))
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--target-manifest")
    parser.add_argument("--snapshot")
    parser.add_argument("--packet")
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        if args.command == "snapshot":
            result = snapshot(repo_root=args.repo_root, target_manifest=args.target_manifest, output=args.output)
        elif args.command == "validate-packet":
            result = validate_packet(repo_root=args.repo_root, packet=args.packet)
        elif args.command == "apply-packet":
            result = apply_packet(repo_root=args.repo_root, packet=args.packet)
        else:
            result = verify(repo_root=args.repo_root, packet=args.packet)
    except NewsGraspChangeControlError as exc:
        print(json.dumps({"status": "rejected", "reasonCode": exc.code, "detail": exc.detail}, ensure_ascii=False))
        return EXIT_CONCURRENT_OWNER if exc.code == "NG_CONCURRENT_OWNER_PRESENT" else EXIT_REJECTED
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
