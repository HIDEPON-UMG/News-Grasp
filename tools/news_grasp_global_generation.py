"""News-Graspが参照する外部ハーネス世代をproduct-localへ封印する。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "NEWS_GRASP_GLOBAL_DEPENDENCY_GENERATION_MANIFEST_V1"
REQUIRED_FIELDS = {
    "schemaVersion",
    "generationId",
    "ownerRepo",
    "ownerCommit",
    "sourceSnapshotPath",
    "sourceSnapshotSha256",
    "installedRuntimePath",
    "installedRuntimeSha256",
    "ownerAuthorityReceiptPath",
    "ownerAuthorityReceiptSha256",
    "validForGoalId",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _managed_file(path: Path, root: Path) -> Path:
    candidate = path.resolve(strict=True)
    boundary = root.resolve(strict=True)
    if not candidate.is_file() or candidate.is_symlink() or not candidate.is_relative_to(boundary):
        raise ValueError("NEWS_GRASP_GLOBAL_GENERATION_MANAGED_PATH_INVALID")
    cursor = candidate
    while True:
        if cursor.is_symlink():
            raise ValueError("NEWS_GRASP_GLOBAL_GENERATION_REPARSE_INVALID")
        if cursor == boundary:
            break
        cursor = cursor.parent
        if boundary not in cursor.parents and cursor != boundary:
            raise ValueError("NEWS_GRASP_GLOBAL_GENERATION_MANAGED_PATH_INVALID")
    return candidate


def create_manifest(
    *,
    output_path: Path,
    product_root: Path,
    generation_id: str,
    owner_repo: str,
    owner_commit: str,
    source_snapshot_path: Path,
    installed_runtime_path: Path,
    owner_authority_receipt_path: Path,
    valid_for_goal_id: str,
) -> dict[str, Any]:
    source = _managed_file(source_snapshot_path, product_root)
    installed = _managed_file(installed_runtime_path, product_root)
    receipt = _managed_file(owner_authority_receipt_path, product_root)
    if not generation_id or not owner_repo or len(owner_commit) not in (40, 64) or not valid_for_goal_id:
        raise ValueError("NEWS_GRASP_GLOBAL_GENERATION_IDENTITY_INVALID")
    value = {
        "schemaVersion": SCHEMA_VERSION,
        "generationId": generation_id,
        "ownerRepo": owner_repo,
        "ownerCommit": owner_commit,
        "sourceSnapshotPath": str(source),
        "sourceSnapshotSha256": sha256_file(source),
        "installedRuntimePath": str(installed),
        "installedRuntimeSha256": sha256_file(installed),
        "ownerAuthorityReceiptPath": str(receipt),
        "ownerAuthorityReceiptSha256": sha256_file(receipt),
        "validForGoalId": valid_for_goal_id,
    }
    if output_path.exists():
        raise ValueError("NEWS_GRASP_GLOBAL_GENERATION_OUTPUT_EXISTS")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return value


def validate_manifest(value: object, *, product_root: Path) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != REQUIRED_FIELDS or value.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError("NEWS_GRASP_GLOBAL_GENERATION_MANIFEST_INVALID")
    for path_key, hash_key in (
        ("sourceSnapshotPath", "sourceSnapshotSha256"),
        ("installedRuntimePath", "installedRuntimeSha256"),
        ("ownerAuthorityReceiptPath", "ownerAuthorityReceiptSha256"),
    ):
        path = _managed_file(Path(str(value[path_key])), product_root)
        if sha256_file(path) != value[hash_key]:
            raise ValueError("NEWS_GRASP_GLOBAL_GENERATION_MANIFEST_DRIFT")
    if not str(value.get("ownerCommit", "")) or not str(value.get("validForGoalId", "")):
        raise ValueError("NEWS_GRASP_GLOBAL_GENERATION_MANIFEST_INVALID")
    return dict(value)
