from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.news_grasp_global_generation import (
    create_manifest,
    validate_manifest,
)


def _files(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "product"
    root.mkdir()
    source = root / "source.py"
    installed = root / "installed.py"
    receipt = root / "owner-receipt.json"
    source.write_text("source\n", encoding="utf-8")
    installed.write_text("installed\n", encoding="utf-8")
    receipt.write_text(json.dumps({"status": "selected_generation_issued"}) + "\n", encoding="utf-8")
    return source, installed, receipt


def test_create_and_validate_product_local_global_generation(tmp_path: Path) -> None:
    source, installed, receipt = _files(tmp_path)
    manifest_path = source.parent / "manifest.json"
    manifest = create_manifest(
        output_path=manifest_path,
        product_root=source.parent,
        generation_id="global:20260812:owner",
        owner_repo="AIHarnessState",
        owner_commit="a" * 40,
        source_snapshot_path=source,
        installed_runtime_path=installed,
        owner_authority_receipt_path=receipt,
        valid_for_goal_id="019fe434-c58f-7441-9a23-6f62aaf7c23b",
    )
    assert validate_manifest(manifest, product_root=source.parent)["generationId"] == manifest["generationId"]


def test_global_generation_rejects_receipt_drift(tmp_path: Path) -> None:
    source, installed, receipt = _files(tmp_path)
    manifest_path = source.parent / "manifest.json"
    manifest = create_manifest(
        output_path=manifest_path,
        product_root=source.parent,
        generation_id="global:20260812:owner",
        owner_repo="AIHarnessState",
        owner_commit="a" * 40,
        source_snapshot_path=source,
        installed_runtime_path=installed,
        owner_authority_receipt_path=receipt,
        valid_for_goal_id="goal",
    )
    receipt.write_text("drift\n", encoding="utf-8")
    with pytest.raises(ValueError, match="MANIFEST_DRIFT"):
        validate_manifest(manifest, product_root=source.parent)
