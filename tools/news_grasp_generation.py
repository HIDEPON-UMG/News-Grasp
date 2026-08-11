"""News-Graspのimmutable production generation manifest。"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any


SCHEMA = "PRODUCTION_GENERATION_MANIFEST_V2"


class NewsGraspGenerationError(RuntimeError):
    """generation parity / promotion違反。"""


def _json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_head(root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
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
    manifest: dict[str, Any] = {
        "schemaVersion": SCHEMA,
        "productId": "News-Grasp",
        "generationId": generation_id,
        "previousGenerationId": previous_generation_id,
        "source": {
            "root": str(source),
            "commit": _git_head(source),
            "origin": "origin/main",
            "commonDir": str(source / ".git"),
            "trackedFiles": source_files,
        },
        "runtime": {"root": str(runtime), "files": runtime_files},
        "configSha256": _hash(config),
        "installedLauncherHashes": launcher_hashes,
        "scheduledTask": {"action": task_action, "trigger": task_trigger},
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
) -> dict[str, Any]:
    value = _load(manifest)
    validate_task_action(task_action)
    source = Path(source_root).resolve()
    runtime = Path(runtime_root).resolve()
    if value["source"]["root"] != str(source) or value["runtime"]["root"] != str(runtime):
        raise NewsGraspGenerationError("NG_GENERATION_DRIFT")
    if value["source"]["trackedFiles"] != _files(source, list(value["source"]["trackedFiles"])):
        raise NewsGraspGenerationError("NG_GENERATION_DRIFT")
    if value["runtime"]["files"] != _files(runtime, list(value["runtime"]["files"])):
        raise NewsGraspGenerationError("NG_GENERATION_DRIFT")
    if value["configSha256"] != _hash(Path(config_path).resolve()):
        raise NewsGraspGenerationError("NG_GENERATION_DRIFT")
    actual_launchers = {str(Path(path).resolve()): _hash(Path(path).resolve()) for path in launcher_paths}
    if value["installedLauncherHashes"] != actual_launchers:
        raise NewsGraspGenerationError("NG_GENERATION_DRIFT")
    if value["scheduledTask"] != {"action": task_action, "trigger": task_trigger}:
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
