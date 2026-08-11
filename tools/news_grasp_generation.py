"""News-Graspのimmutable production generation manifest。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Mapping


SCHEMA = "PRODUCTION_GENERATION_MANIFEST_V2"
ProductionGenerationManifestV2 = SCHEMA
immutable_generation = "immutable_generation"


class NewsGraspGenerationError(RuntimeError):
    """generation parity / promotion違反。"""


def _json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _json_insertion_order(value: object) -> bytes:
    """既存PowerShell installerのordered JSON authorityと互換のhash入力。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=False, separators=(",", ":")).encode("utf-8")


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_value(root: Path, *arguments: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *arguments],
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError:
        return "unavailable"
    return result.stdout.strip() if result.returncode == 0 else "unavailable"


def _git_head(root: Path) -> str:
    return _git_value(root, "rev-parse", "HEAD")


def _git_common_dir(root: Path) -> str:
    value = _git_value(root, "rev-parse", "--git-common-dir")
    if value == "unavailable":
        return value
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    return str(path.resolve())


def _manifest_hash(rows: Mapping[str, str]) -> str:
    return hashlib.sha256(_json(dict(sorted(rows.items())))).hexdigest()


def _input_manifest_hash(value: Mapping[str, Any] | Path | str | None) -> str:
    if value is None:
        return hashlib.sha256(_json({})).hexdigest()
    if isinstance(value, Mapping):
        return hashlib.sha256(_json(dict(value))).hexdigest()
    path = Path(value).resolve()
    if not path.is_file() or path.is_symlink():
        raise NewsGraspGenerationError("NG_INPUT_MANIFEST_INVALID")
    return _hash(path)


def _files(root: Path, paths: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for relative in paths:
        path = root / relative
        if not path.is_file() or path.is_symlink():
            raise NewsGraspGenerationError("NG_GENERATION_FILE_INVALID")
        result[relative.replace("\\", "/")] = _hash(path)
    return result


def validate_task_action(action: list[str]) -> None:
    joined = " ".join(action).casefold()
    if "--repo-dir" in joined or "--worktree" in joined:
        raise NewsGraspGenerationError("NG_TASK_ACTION_WORKTREE_OVERRIDE")
    if not action or "news-grasp-task-launcher.pyw" not in joined:
        raise NewsGraspGenerationError("NG_TASK_ACTION_INVALID")


def create_stable_task_authority(
    *,
    task_name: str,
    launcher_path: Path | str,
    launcher_sha256: str,
    action: list[str],
    trigger: dict[str, Any],
    bootstrap_path: str = "",
    bootstrap_sha256: str = "",
) -> dict[str, Any]:
    """generation pathを含まないScheduled Task authorityを作る。"""
    validate_task_action(action)
    joined = " ".join(action).casefold()
    if "--repo-dir" in joined or "--worktree" in joined:
        raise NewsGraspGenerationError("NG_STABLE_TASK_REPO_ARGUMENT_FORBIDDEN")
    if not task_name or "news-grasp-task-launcher.pyw" not in joined:
        raise NewsGraspGenerationError("NG_STABLE_TASK_ACTION_INVALID")
    if not re.fullmatch(r"[0-9a-f]{64}", str(launcher_sha256)):
        raise NewsGraspGenerationError("NG_STABLE_TASK_LAUNCHER_HASH_INVALID")
    body: dict[str, Any] = {
        "schemaVersion": "STABLE_TASK_AUTHORITY_V1",
        "taskName": task_name,
        "stableLauncherPath": str(Path(launcher_path).resolve()),
        "stableLauncherSha256": launcher_sha256,
        "bootstrapPath": bootstrap_path,
        "bootstrapSha256": bootstrap_sha256,
        "action": list(action),
        "trigger": dict(trigger),
        "repoArgumentCount": 0,
        "actionSha256": hashlib.sha256(_json(list(action))).hexdigest(),
        "triggerSha256": hashlib.sha256(_json(dict(trigger))).hexdigest(),
    }
    body["authoritySha256"] = hashlib.sha256(_json(body)).hexdigest()
    return body


def validate_stable_task_authority(value: Mapping[str, Any]) -> dict[str, Any]:
    """stable task authorityを自己hash・action・triggerへ束縛する。"""
    body = dict(value)
    authority_sha256 = str(body.pop("authoritySha256", ""))
    if body.get("schemaVersion") != "STABLE_TASK_AUTHORITY_V1":
        raise NewsGraspGenerationError("NG_STABLE_TASK_AUTHORITY_INVALID")
    action = body.get("action")
    trigger = body.get("trigger")
    if not isinstance(action, list) or not all(isinstance(item, str) for item in action):
        raise NewsGraspGenerationError("NG_STABLE_TASK_AUTHORITY_INVALID")
    if not isinstance(trigger, dict):
        raise NewsGraspGenerationError("NG_STABLE_TASK_AUTHORITY_INVALID")
    validate_task_action(action)
    if body.get("repoArgumentCount") != 0:
        raise NewsGraspGenerationError("NG_STABLE_TASK_REPO_ARGUMENT_FORBIDDEN")
    if body.get("actionSha256") not in {
        None,
        hashlib.sha256(_json(action)).hexdigest(),
    }:
        raise NewsGraspGenerationError("NG_STABLE_TASK_AUTHORITY_INVALID")
    if body.get("triggerSha256") not in {
        None,
        hashlib.sha256(_json(trigger)).hexdigest(),
    }:
        raise NewsGraspGenerationError("NG_STABLE_TASK_AUTHORITY_INVALID")
    allowed_hashes = {
        hashlib.sha256(_json(body)).hexdigest(),
        hashlib.sha256(_json_insertion_order(body)).hexdigest(),
    }
    if authority_sha256 not in allowed_hashes:
        raise NewsGraspGenerationError("NG_STABLE_TASK_AUTHORITY_INVALID")
    return {**body, "authoritySha256": authority_sha256}


def validate_installed_launcher_identity(
    *,
    launcher_path: Path | str,
    stable_task_authority: Mapping[str, Any],
    active_generation: Mapping[str, Any] | None = None,
    expected_generation_id: str = "",
) -> dict[str, Any]:
    """installed launcherをstable task authorityとactive generationへ束縛する。"""
    authority = validate_stable_task_authority(stable_task_authority)
    launcher = Path(launcher_path).resolve()
    if not launcher.is_file() or launcher.is_symlink():
        raise NewsGraspGenerationError("NG_INSTALLED_LAUNCHER_IDENTITY_INVALID")
    try:
        expected_path = Path(str(authority["stableLauncherPath"])).resolve()
    except (KeyError, OSError, RuntimeError, ValueError) as exc:
        raise NewsGraspGenerationError("NG_INSTALLED_LAUNCHER_IDENTITY_INVALID") from exc
    if expected_path != launcher or authority.get("stableLauncherSha256") != _hash(launcher):
        raise NewsGraspGenerationError("NG_INSTALLED_LAUNCHER_IDENTITY_INVALID")
    action = list(authority["action"])
    if len(action) < 2 or Path(action[1]).resolve() != launcher:
        raise NewsGraspGenerationError("NG_INSTALLED_LAUNCHER_IDENTITY_INVALID")
    generation_id = ""
    if active_generation is not None:
        generation_id = str(active_generation.get("generationId") or "")
        if active_generation.get("schemaVersion") not in {
            "NEWS_GRASP_ACTIVE_GENERATION_V1",
            "NEWS_GRASP_ACTIVE_GENERATION_V2",
        }:
            raise NewsGraspGenerationError("NG_ACTIVE_GENERATION_INVALID")
        if expected_generation_id and generation_id != expected_generation_id:
            raise NewsGraspGenerationError("NG_ACTIVE_GENERATION_DRIFT")
        authority_hash = str(active_generation.get("stableTaskAuthoritySha256") or "")
        if authority_hash and authority_hash != authority["authoritySha256"]:
            raise NewsGraspGenerationError("NG_ACTIVE_GENERATION_DRIFT")
    return {
        "status": "green",
        "launcherPath": str(launcher),
        "launcherSha256": _hash(launcher),
        "stableTaskAuthoritySha256": authority["authoritySha256"],
        "generationId": generation_id,
    }


PROMOTION_PHASES = (
    "admitted",
    "runtime_staged",
    "input_bound",
    "candidate_verified",
    "active_pointer_committed",
    "transaction_committed",
)


def promote_generation(
    *,
    active_pointer: Path | str,
    old_generation_id: str,
    new_generation_id: str,
    phase: str,
    stable_task_authority: Mapping[str, Any],
    runtime_manifest_sha256: str = "",
    input_manifest_sha256: str = "",
    external_readiness: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """promotion phaseを検証し、最終phaseだけactive pointerをreplaceする。"""
    if (
        phase not in PROMOTION_PHASES
        or phase != "transaction_committed"
        or not old_generation_id
        or not new_generation_id
        or old_generation_id == new_generation_id
    ):
        raise NewsGraspGenerationError("NG_PROMOTION_PHASE_INVALID")
    validated_task_authority = validate_stable_task_authority(stable_task_authority)
    if external_readiness is not None and external_readiness.get("status") != "ready":
        raise NewsGraspGenerationError("NG_EXTERNAL_DEPENDENCY_DEFERRED")
    pointer = Path(active_pointer)
    body: dict[str, Any] = {
        "schemaVersion": "NEWS_GRASP_ACTIVE_GENERATION_V2",
        "generationId": new_generation_id,
        "previousGenerationId": old_generation_id,
        "stableTaskAuthoritySha256": validated_task_authority["authoritySha256"],
        "runtimeManifestSha256": runtime_manifest_sha256,
        "inputManifestSha256": input_manifest_sha256,
        "phase": phase,
    }
    body["pointerSha256"] = hashlib.sha256(_json(body)).hexdigest()
    pointer.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=pointer.parent, prefix=".active-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(_json(body) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, pointer)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return body


def recover_promotion(*, wal: Mapping[str, Any], active_pointer: Path | str) -> dict[str, Any]:
    """pointer commit前はold保持、commit後はexactnewへforwardする。"""
    phase = str(wal.get("phase") or "")
    old_id = str(wal.get("oldGenerationId") or "")
    new_id = str(wal.get("newGenerationId") or "")
    if phase not in PROMOTION_PHASES or not old_id or not new_id:
        raise NewsGraspGenerationError("NG_PROMOTION_WAL_INVALID")
    if phase != "active_pointer_committed" and phase != "transaction_committed":
        return {"status": "old_generation_retained", "generationId": old_id}
    pointer = Path(active_pointer)
    if not pointer.is_file():
        raise NewsGraspGenerationError("NG_PROMOTION_POINTER_MISSING")
    value = _load(pointer)
    if value.get("generationId") not in {old_id, new_id}:
        raise NewsGraspGenerationError("NG_PROMOTION_DIVERGED")
    return {"status": "forward_generation_retained", "generationId": value["generationId"]}


def create_manifest(
    *,
    source_root: Path | str,
    source_paths: list[str],
    runtime_root: Path | str,
    runtime_paths: list[str],
    config_path: Path | str,
    launcher_paths: list[Path | str],
    task_action: list[str],
    task_trigger: dict[str, Any],
    generation_id: str,
    previous_generation_id: str | None,
    output: Path | str,
    input_manifest: Mapping[str, Any] | Path | str | None = None,
) -> dict[str, Any]:
    source = Path(source_root).resolve()
    runtime = Path(runtime_root).resolve()
    validate_task_action(task_action)
    if not generation_id or generation_id == previous_generation_id:
        raise NewsGraspGenerationError("NG_GENERATION_ID_INVALID")
    source_files = _files(source, source_paths)
    runtime_files = _files(runtime, runtime_paths)
    config = Path(config_path).resolve()
    if not config.is_file():
        raise NewsGraspGenerationError("NG_CONFIG_INVALID")
    launcher_hashes = {str(Path(path).resolve()): _hash(Path(path).resolve()) for path in launcher_paths}
    source_commit = _git_head(source)
    source_common_dir = _git_common_dir(source)
    origin_url = _git_value(source, "config", "--get", "remote.origin.url")
    remote_head = _git_value(source, "rev-parse", "refs/remotes/origin/main")
    manifest: dict[str, Any] = {
        "schemaVersion": SCHEMA,
        "productId": "News-Grasp",
        "generationId": generation_id,
        "previousGenerationId": previous_generation_id,
        "source": {
            "root": str(source),
            "commit": source_commit,
            "origin": "origin/main",
            "originUrl": origin_url,
            "remoteHead": remote_head,
            "commonDir": source_common_dir,
            "trackedFiles": source_files,
            "trackedManifestSha256": _manifest_hash(source_files),
        },
        "runtime": {
            "root": str(runtime),
            "files": runtime_files,
            "manifestSha256": _manifest_hash(runtime_files),
        },
        "configSha256": _hash(config),
        "installedLauncherHashes": launcher_hashes,
        "installedLauncherManifestSha256": _manifest_hash(launcher_hashes),
        "inputManifestSha256": _input_manifest_hash(input_manifest),
        "scheduledTask": {
            "action": task_action,
            "actionSha256": hashlib.sha256(_json(task_action)).hexdigest(),
            "trigger": task_trigger,
            "triggerSha256": hashlib.sha256(_json(task_trigger)).hexdigest(),
        },
    }
    manifest["manifestSha256"] = hashlib.sha256(_json(manifest)).hexdigest()
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(manifest, stream, ensure_ascii=False, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    return manifest


def _load(value: Path | str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        loaded = json.loads(Path(value).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NewsGraspGenerationError("NG_GENERATION_MANIFEST_INVALID") from exc
    if not isinstance(loaded, dict) or loaded.get("schemaVersion") != SCHEMA:
        raise NewsGraspGenerationError("NG_GENERATION_MANIFEST_INVALID")
    return loaded


def verify_parity(
    *,
    manifest: Path | str | dict[str, Any],
    source_root: Path | str,
    runtime_root: Path | str,
    config_path: Path | str,
    launcher_paths: list[Path | str],
    task_action: list[str],
    task_trigger: dict[str, Any],
    input_manifest: Mapping[str, Any] | Path | str | None = None,
) -> dict[str, Any]:
    value = _load(manifest)
    validate_task_action(task_action)
    source = Path(source_root).resolve()
    runtime = Path(runtime_root).resolve()
    if value["source"]["root"] != str(source) or value["runtime"]["root"] != str(runtime):
        raise NewsGraspGenerationError("NG_GENERATION_DRIFT")
    actual_source_files = _files(source, list(value["source"]["trackedFiles"]))
    if value["source"]["trackedFiles"] != actual_source_files:
        raise NewsGraspGenerationError("NG_GENERATION_DRIFT")
    if value["source"].get("trackedManifestSha256") != _manifest_hash(actual_source_files):
        raise NewsGraspGenerationError("NG_GENERATION_DRIFT")
    current_commit = _git_head(source)
    current_common_dir = _git_common_dir(source)
    current_origin_url = _git_value(source, "config", "--get", "remote.origin.url")
    current_remote_head = _git_value(source, "rev-parse", "refs/remotes/origin/main")
    if any(
        value["source"].get(field) != actual
        for field, actual in {
            "commit": current_commit,
            "commonDir": current_common_dir,
            "originUrl": current_origin_url,
            "remoteHead": current_remote_head,
        }.items()
    ):
        raise NewsGraspGenerationError("NG_GENERATION_DRIFT")
    actual_runtime_files = _files(runtime, list(value["runtime"]["files"]))
    if value["runtime"]["files"] != actual_runtime_files:
        raise NewsGraspGenerationError("NG_GENERATION_DRIFT")
    if value["runtime"].get("manifestSha256") != _manifest_hash(actual_runtime_files):
        raise NewsGraspGenerationError("NG_GENERATION_DRIFT")
    if value["configSha256"] != _hash(Path(config_path).resolve()):
        raise NewsGraspGenerationError("NG_GENERATION_DRIFT")
    actual_launchers = {str(Path(path).resolve()): _hash(Path(path).resolve()) for path in launcher_paths}
    if value["installedLauncherHashes"] != actual_launchers:
        raise NewsGraspGenerationError("NG_GENERATION_DRIFT")
    if value.get("installedLauncherManifestSha256") != _manifest_hash(actual_launchers):
        raise NewsGraspGenerationError("NG_GENERATION_DRIFT")
    if value.get("inputManifestSha256") != _input_manifest_hash(input_manifest):
        raise NewsGraspGenerationError("NG_GENERATION_INPUT_DRIFT")
    actual_task = {
        "action": task_action,
        "actionSha256": hashlib.sha256(_json(task_action)).hexdigest(),
        "trigger": task_trigger,
        "triggerSha256": hashlib.sha256(_json(task_trigger)).hexdigest(),
    }
    if value["scheduledTask"] != actual_task:
        raise NewsGraspGenerationError("NG_SCHEDULED_TASK_DRIFT")
    expected_hash = value.get("manifestSha256")
    body = dict(value)
    body.pop("manifestSha256", None)
    if expected_hash != hashlib.sha256(_json(body)).hexdigest():
        raise NewsGraspGenerationError("NG_GENERATION_MANIFEST_INVALID")
    return {"status": "green", "generationId": value["generationId"], "manifestSha256": expected_hash}


def activate(*, manifest: Path | str | dict[str, Any], active_pointer: Path | str, **verify_kwargs: Any) -> dict[str, Any]:
    result = verify_parity(manifest=manifest, **verify_kwargs)
    destination = Path(active_pointer)
    payload = {"schemaVersion": "NEWS_GRASP_ACTIVE_GENERATION_V1", **result}
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".active-", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(_json(payload) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, destination)
    return payload


def rollback(*, previous_manifest: Path | str | dict[str, Any], active_pointer: Path | str, **verify_kwargs: Any) -> dict[str, Any]:
    return activate(manifest=previous_manifest, active_pointer=active_pointer, **verify_kwargs)
