"""News-Grasp専用automation・skill資産の決定論的検証。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


SCHEMA = "NEWS_GRASP_AUTOMATION_ASSET_MANIFEST_V1"


class AssetManifestError(ValueError):
    """資産manifestが不正。"""


def _relative(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AssetManifestError(f"NG_ASSET_{field.upper()}_INVALID")
    normalized = value.replace("\\", "/")
    path = Path(normalized)
    if normalized.startswith(("/", "//")) or ":" in normalized.split("/", 1)[0]:
        raise AssetManifestError("NG_ASSET_ABSOLUTE_PATH")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise AssetManifestError("NG_ASSET_RELATIVE_PATH_INVALID")
    return "/".join(path.parts)


def load_manifest(path: Path | str) -> dict[str, Any]:
    manifest_path = Path(path)
    try:
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AssetManifestError("NG_ASSET_MANIFEST_INVALID") from exc
    if not isinstance(value, dict) or value.get("schemaVersion") != SCHEMA or value.get("productId") != "News-Grasp":
        raise AssetManifestError("NG_ASSET_MANIFEST_SCHEMA_INVALID")
    if value.get("installRoot") != "news-grasp-assets":
        raise AssetManifestError("NG_ASSET_INSTALL_ROOT_INVALID")
    assets = value.get("assets")
    if not isinstance(assets, list) or not assets:
        raise AssetManifestError("NG_ASSET_LIST_INVALID")
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for asset in assets:
        if not isinstance(asset, dict) or not isinstance(asset.get("assetId"), str):
            raise AssetManifestError("NG_ASSET_ENTRY_INVALID")
        asset_id = asset["assetId"]
        if asset_id in seen:
            raise AssetManifestError("NG_ASSET_DUPLICATE_ID")
        seen.add(asset_id)
        source = _relative(asset.get("sourcePath"), field="source_path")
        install = _relative(asset.get("installPath"), field="install_path")
        if asset.get("kind") not in {"skill", "guard", "automation"}:
            raise AssetManifestError("NG_ASSET_KIND_INVALID")
        normalized.append({"assetId": asset_id, "kind": asset["kind"], "sourcePath": source, "installPath": install})
    result = dict(value)
    result["assets"] = normalized
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_assets(repo_root: Path | str, manifest: dict[str, Any]) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    rows: list[dict[str, str]] = []
    for asset in manifest["assets"]:
        source = root / asset["sourcePath"]
        if not source.is_file() or source.is_symlink():
            raise AssetManifestError("NG_ASSET_SOURCE_INVALID")
        rows.append({**asset, "sourceSha256": _sha256(source)})
    return {**manifest, "assets": rows}


def verify_snapshot(repo_root: Path | str, snapshot: dict[str, Any]) -> bool:
    current = snapshot_assets(repo_root, load_manifest_from_value(snapshot))
    expected = [(row["assetId"], row["sourceSha256"]) for row in snapshot["assets"]]
    actual = [(row["assetId"], row["sourceSha256"]) for row in current["assets"]]
    return expected == actual


def load_manifest_from_value(value: dict[str, Any]) -> dict[str, Any]:
    temp = dict(value)
    temp["assets"] = [{k: row[k] for k in ("assetId", "kind", "sourcePath", "installPath")} for row in value.get("assets", [])]
    if temp.get("schemaVersion") != SCHEMA or temp.get("productId") != "News-Grasp":
        raise AssetManifestError("NG_ASSET_MANIFEST_SCHEMA_INVALID")
    return load_manifest_value(temp)


def load_manifest_value(value: dict[str, Any]) -> dict[str, Any]:
    assets = value.get("assets")
    if not isinstance(assets, list):
        raise AssetManifestError("NG_ASSET_LIST_INVALID")
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for asset in assets:
        source = _relative(asset.get("sourcePath"), field="source_path")
        install = _relative(asset.get("installPath"), field="install_path")
        asset_id = asset.get("assetId")
        if not isinstance(asset_id, str) or asset_id in seen:
            raise AssetManifestError("NG_ASSET_DUPLICATE_ID")
        seen.add(asset_id)
        normalized.append({"assetId": asset_id, "kind": asset.get("kind"), "sourcePath": source, "installPath": install})
    if value.get("installRoot") != "news-grasp-assets":
        raise AssetManifestError("NG_ASSET_INSTALL_ROOT_INVALID")
    return {**value, "assets": normalized}
