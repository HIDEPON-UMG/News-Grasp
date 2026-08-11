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
ROUTE_SCHEMA = "NEWS_GRASP_PRODUCT_CHANGE_ROUTES_V1"
ROUTE_RELATIVE_PATH = "config/news_grasp_product_change_routes_v1.json"
NEWS_GRASP_PRODUCT_CHANGE_ALLOWLIST_V1 = "NEWS_GRASP_PRODUCT_CHANGE_ALLOWLIST_V1"
PRODUCT_ID = "News-Grasp"
EXIT_CONCURRENT_OWNER = 73
EXIT_REJECTED = 74
EXPECTED_ROUTE_EXECUTORS: dict[str, dict[str, Any]] = {
    "codex": {"actor": "codex", "mode": "change_packet_only", "noDirectWrite": True},
    "claude": {"actor": "claude", "mode": "change_packet_only", "noDirectWrite": True},
    "direct-script": {"actor": "direct-script", "deterministic": True, "noDirectWrite": True},
    "luna": {"model": "gpt-5.6-luna", "reasoningEffort": "max", "noSubstitution": True},
}


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
        if current.is_dir():
            try:
                aliases = [entry.name for entry in os.scandir(current) if entry.name.casefold() == part.casefold()]
            except OSError as exc:
                _fail("NG_CHANGE_PATH_INVALID", str(exc))
            if len(aliases) > 1 or (aliases and aliases[0] != part):
                _fail("NG_PATH_CASE_ALIAS", normalized)
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


def validate_route_registry(value: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if (
        value.get("schemaVersion") != ROUTE_SCHEMA
        or value.get("productId") != PRODUCT_ID
        or value.get("unknownRoutePolicy") != "fail_closed"
        or value.get("consumer") != "tools.news_grasp_change_control.apply_packet"
    ):
        _fail("NG_ROUTE_REGISTRY_INVALID")
    rows = value.get("routes")
    if not isinstance(rows, list) or len(rows) != len(EXPECTED_ROUTE_EXECUTORS):
        _fail("NG_ROUTE_REGISTRY_INVALID")
    routes: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("routeId"), str):
            _fail("NG_ROUTE_REGISTRY_INVALID")
        route_id = row["routeId"]
        if route_id in routes or route_id not in EXPECTED_ROUTE_EXECUTORS:
            _fail("NG_ROUTE_REGISTRY_INVALID")
        if row.get("executor") != EXPECTED_ROUTE_EXECUTORS[route_id] or not row.get("producer"):
            _fail("NG_ROUTE_REGISTRY_INVALID")
        routes[route_id] = row
    if set(routes) != set(EXPECTED_ROUTE_EXECUTORS):
        _fail("NG_ROUTE_REGISTRY_INVALID")
    return routes


def load_route_registry(repo_root: Path | str) -> dict[str, dict[str, Any]]:
    repo = _repo_root(repo_root)
    path = _relative_path(repo, ROUTE_RELATIVE_PATH, must_exist=True)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail("NG_ROUTE_REGISTRY_INVALID", str(exc))
    if not isinstance(value, dict):
        _fail("NG_ROUTE_REGISTRY_INVALID")
    return validate_route_registry(value)


def validate_actor_route(
    *, actor_route_id: object, executor: object, routes: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    if not isinstance(actor_route_id, str) or actor_route_id not in routes:
        _fail("NG_UNKNOWN_ACTOR_ROUTE")
    route = routes[actor_route_id]
    if executor != route["executor"]:
        _fail("NG_PACKET_CONTRACT_INVALID")
    return route


def _target_paths(repo: Path, values: object, allowed: list[str]) -> list[str]:
    if not isinstance(values, list) or not values or any(not isinstance(item, str) for item in values):
        _fail("NG_MANIFEST_INVALID")
    if len({item.casefold() for item in values}) != len(values):
        _fail("NG_MANIFEST_INVALID")
    if any(item not in allowed for item in values):
        _fail("NG_ALLOWLIST_SCOPE_INVALID")
    for item in values:
        _relative_path(repo, item)
    return values


def _git(repo: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"
    return result.stdout.strip() if result.returncode == 0 else "unavailable"


def _git_authority(repo: Path) -> dict[str, str]:
    authority = {
        "gitRoot": _git(repo, "rev-parse", "--show-toplevel"),
        "gitHead": _git(repo, "rev-parse", "HEAD"),
        "gitRemoteHead": _git(repo, "ls-remote", "origin", "refs/heads/main"),
        "gitWorktrees": _git(repo, "worktree", "list", "--porcelain"),
    }
    if authority["gitHead"] == "unavailable":
        if authority["gitRoot"] != "unavailable":
            _fail("NG_GIT_AUTHORITY_INVALID")
        return authority
    if "unavailable" in authority.values():
        _fail("NG_GIT_AUTHORITY_UNAVAILABLE")
    try:
        observed_root = Path(authority["gitRoot"]).resolve(strict=True)
    except OSError as exc:
        _fail("NG_GIT_AUTHORITY_INVALID", str(exc))
    if observed_root != repo:
        _fail("NG_GIT_ROOT_IDENTITY_MISMATCH")
    return authority


def _state_root(repo: Path) -> Path:
    local_candidate = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir()))
    try:
        local_candidate.mkdir(parents=True, exist_ok=True)
        local = local_candidate.resolve(strict=True)
    except OSError as exc:
        _fail("NG_STATE_ROOT_INVALID", str(exc))
    if _is_reparse(local):
        _fail("NG_STATE_ROOT_REPARSE_FORBIDDEN")
    key = _sha256_bytes(str(repo).casefold().encode("utf-8"))[:32]
    current = local
    for part in ("NewsGrasp", "change-control", key):
        current = current / part
        try:
            current.mkdir(exist_ok=True)
        except OSError as exc:
            _fail("NG_STATE_ROOT_INVALID", str(exc))
        if _is_reparse(current):
            _fail("NG_STATE_ROOT_REPARSE_FORBIDDEN")
    return current


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


def _claim_owner(repo: Path, owner: str) -> bool:
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
        return False
    payload = {"ownerThreadId": owner, "pid": os.getpid(), "claimedAt": time.time()}
    try:
        with owner_path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        _fail("NG_CONCURRENT_OWNER_PRESENT")
    return True


def _assert_owner(repo: Path, owner: str) -> None:
    try:
        current = json.loads(_owner_path(repo).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _fail("NG_CONCURRENT_OWNER_PRESENT")
    if current.get("ownerThreadId") != owner:
        _fail("NG_CONCURRENT_OWNER_PRESENT")


def release_owner(*, repo_root: Path | str, owner: str) -> dict[str, Any]:
    repo = _repo_root(repo_root)
    with _named_mutex(repo):
        _assert_owner(repo, owner)
        _owner_path(repo).unlink()
    return {"status": "released", "ownerThreadId": owner}


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
    allowed = _allowlist(repo)
    paths = _target_paths(repo, manifest.get("targets"), allowed)
    owner = manifest.get("ownerThreadId")
    actor_route_id = manifest.get("actorRouteId")
    routes = load_route_registry(repo)
    if actor_route_id not in routes:
        _fail("NG_UNKNOWN_ACTOR_ROUTE")
    with _named_mutex(repo):
        git_authority = _git_authority(repo)
        owner_created = _claim_owner(repo, owner)
        try:
            result: dict[str, Any] = {
                "schemaVersion": SNAPSHOT_SCHEMA,
                "productId": PRODUCT_ID,
                "repoRoot": str(repo),
                "ownerThreadId": owner,
                "actorRouteId": actor_route_id,
                "targetPaths": paths,
                "targetHashes": _target_hashes(repo, paths),
                "routeRegistrySha256": _sha256_file(repo / ROUTE_RELATIVE_PATH),
                **git_authority,
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
        except Exception:
            if owner_created:
                _assert_owner(repo, str(owner))
                _owner_path(repo).unlink()
            raise


def _validate_packet_shape(
    packet: dict[str, Any], repo: Path, allowed: list[str], routes: dict[str, dict[str, Any]]
) -> None:
    if packet.get("schemaVersion") != PACKET_SCHEMA:
        _fail("NG_PACKET_CONTRACT_INVALID")
    actor_route_id = packet.get("actorRouteId")
    executor = packet.get("executor")
    validate_actor_route(actor_route_id=actor_route_id, executor=executor, routes=routes)
    if packet.get("unresolvedDecisionIds") != []:
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
    change_paths = [str(change["path"]) for change in changes]
    write_set = packet.get("allowedWriteSet")
    if write_set != change_paths:
        _fail("NG_PACKET_CONTRACT_INVALID")
    _target_paths(repo, write_set, allowed)


def validate_packet(*, repo_root: Path | str, packet: Path | str | dict[str, Any]) -> dict[str, Any]:
    """packet、owner、allowlist、全target baselineをmutation前に再確認する。"""

    repo = _repo_root(repo_root)
    value = _manifest(packet)
    allowed = _allowlist(repo)
    routes = load_route_registry(repo)
    for change in value.get("changes", []):
        if isinstance(change, dict) and isinstance(change.get("path"), str):
            _relative_path(repo, change["path"])
    _validate_packet_shape(value, repo, allowed, routes)
    if value.get("repoRoot") != str(repo):
        _fail("NG_PACKET_CONTRACT_INVALID")
    owner = value.get("ownerThreadId")
    snapshot_value = _manifest(value.get("snapshotPath", ""))
    snapshot_sha256 = snapshot_value.get("snapshotSha256")
    unsigned_snapshot = {key: item for key, item in snapshot_value.items() if key != "snapshotSha256"}
    if snapshot_sha256 != _sha256_bytes(_canonical_json(unsigned_snapshot)):
        _fail("NG_SNAPSHOT_INTEGRITY_INVALID")
    if snapshot_value.get("repoRoot") != str(repo) or snapshot_value.get("ownerThreadId") != owner:
        _fail("NG_PACKET_CONTRACT_INVALID")
    if snapshot_value.get("actorRouteId") != value.get("actorRouteId"):
        _fail("NG_PACKET_CONTRACT_INVALID")
    if snapshot_value.get("targetPaths") != value.get("allowedWriteSet"):
        _fail("NG_PACKET_CONTRACT_INVALID")
    _assert_owner(repo, str(owner))
    if snapshot_value.get("routeRegistrySha256") != _sha256_file(repo / ROUTE_RELATIVE_PATH):
        _fail("NG_ROUTE_REGISTRY_DRIFT")
    git_fields = _git_authority(repo)
    if snapshot_value.get("gitRoot") != git_fields["gitRoot"]:
        _fail("NG_GIT_ROOT_IDENTITY_MISMATCH")
    if snapshot_value.get("gitHead") != git_fields["gitHead"]:
        _fail("NG_SOURCE_GENERATION_DRIFT")
    if snapshot_value.get("gitRemoteHead") != git_fields["gitRemoteHead"]:
        _fail("NG_REMOTE_HEAD_DRIFT")
    if snapshot_value.get("gitWorktrees") != git_fields["gitWorktrees"]:
        _fail("NG_WORKTREE_SET_DRIFT")
    current = _target_hashes(repo, value["allowedWriteSet"])
    if snapshot_value.get("targetHashes") != current:
        _fail("NG_BASELINE_DRIFT")
    for change in value["changes"]:
        _relative_path(repo, change["path"])
    return {
        "status": "validated",
        "packetId": value.get("packetId"),
        "ownerThreadId": owner,
        "actorRouteId": value["actorRouteId"],
        "allowedWriteSet": value["allowedWriteSet"],
        "executor": value["executor"],
        "unresolvedDecisionIds": [],
        "baselineSha256": snapshot_sha256,
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
        try:
            if journal.exists():
                _fail("NG_PACKET_REPLAY")
            originals: dict[str, dict[str, Any]] = {}
            for change in value["changes"]:
                target = _relative_path(repo, change["path"])
                existed = target.exists()
                originals[change["path"]] = {
                    "existed": existed,
                    "contentBase64": base64.b64encode(target.read_bytes() if existed else b"").decode("ascii"),
                }
            prepared = {
                "schemaVersion": "NEWS_GRASP_CHANGE_TRANSACTION_V1",
                "packetId": packet_id,
                "status": "prepared",
                "repoRoot": str(repo),
                "actorRouteId": value["actorRouteId"],
                "originals": originals,
                "changes": value["changes"],
            }
            with journal.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(prepared, stream, ensure_ascii=False, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            for change in value["changes"]:
                target = _relative_path(repo, change["path"])
                _atomic_write(target, change["content"].encode("utf-8"))
            verify_result = verify(repo_root=repo, packet=value)
            prepared["status"] = "committed"
            prepared["result"] = verify_result
            _atomic_write(journal, _canonical_json(prepared) + b"\n")
            return {**validation, "status": "applied", "packetId": packet_id, "verification": verify_result}
        except Exception:
            if "originals" in locals():
                for relative, original in originals.items():
                    target = _relative_path(repo, relative)
                    if original["existed"]:
                        _atomic_write(target, base64.b64decode(original["contentBase64"]))
                    elif target.exists():
                        target.unlink()
            if "prepared" in locals() and journal.exists():
                prepared["status"] = "rolled_back"
                _atomic_write(journal, _canonical_json(prepared) + b"\n")
            raise
        finally:
            _assert_owner(repo, str(value.get("ownerThreadId")))
            _owner_path(repo).unlink()


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
    parser.add_argument("command", choices=("snapshot", "validate-packet", "apply-packet", "verify", "release-owner"))
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--target-manifest")
    parser.add_argument("--snapshot")
    parser.add_argument("--packet")
    parser.add_argument("--output")
    parser.add_argument("--owner")
    args = parser.parse_args()
    try:
        if args.command == "snapshot":
            result = snapshot(repo_root=args.repo_root, target_manifest=args.target_manifest, output=args.output)
        elif args.command == "validate-packet":
            result = validate_packet(repo_root=args.repo_root, packet=args.packet)
        elif args.command == "apply-packet":
            result = apply_packet(repo_root=args.repo_root, packet=args.packet)
        elif args.command == "verify":
            result = verify(repo_root=args.repo_root, packet=args.packet)
        else:
            result = release_owner(repo_root=args.repo_root, owner=args.owner)
    except NewsGraspChangeControlError as exc:
        print(json.dumps({"status": "rejected", "reasonCode": exc.code, "detail": exc.detail}, ensure_ascii=False))
        return EXIT_CONCURRENT_OWNER if exc.code == "NG_CONCURRENT_OWNER_PRESENT" else EXIT_REJECTED
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
