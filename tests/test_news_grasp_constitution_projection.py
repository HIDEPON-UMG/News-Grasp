from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import news_grasp_constitution as constitution


ROOT = Path(__file__).resolve().parents[1]


def test_constitution_projection_is_exact_mirrored_and_generated() -> None:
    result = constitution.validate_constitution_projections(ROOT)

    assert result["status"] == "Green"
    assert result["agentProjectionMirror"] is True
    assert result["manualProjectionDrift"] is False
    assert result["mermaidDiagramCount"] == 3
    assert result["htmlLineCount"] >= 100


def test_constitution_projection_rejects_unpaired_or_duplicate_markers() -> None:
    block = f"{constitution.PROJECTION_START}\nprojection\n{constitution.PROJECTION_END}"

    with pytest.raises(
        ValueError, match="CONSTITUTION_AGENT_PROJECTION_MARKER_UNPAIRED"
    ):
        constitution._replace_agent_projection(
            f"prefix\n{constitution.PROJECTION_START}\nprojection\n", block
        )
    with pytest.raises(
        ValueError, match="CONSTITUTION_AGENT_PROJECTION_MARKER_DUPLICATE"
    ):
        constitution._replace_agent_projection(
            f"{block}\n{block}\n", block
        )


def test_constitution_html_projection_has_accessible_offline_contract() -> None:
    html = (ROOT / constitution.HTML_SPEC_RELATIVE_PATH).read_text(encoding="utf-8")

    assert '<html lang="ja">' in html
    assert 'class="skip-link"' in html
    assert 'role="tablist"' in html
    assert 'role="tabpanel"' in html
    assert 'aria-live="polite"' in html
    assert "@media (forced-colors: active)" in html
    assert "https://" not in html
    assert "http://" not in html


def test_constitution_asset_projection_hashes_every_versioned_asset() -> None:
    value = json.loads(
        (ROOT / constitution.PROJECTION_RELATIVE_PATH).read_text(encoding="utf-8")
    )
    asset_projection = value["assetProjection"]

    assert value["schemaVersion"] == constitution.PROJECTION_SCHEMA_VERSION
    assert asset_projection["assetCount"] == len(asset_projection["assets"])
    assert asset_projection["assetCount"] > 0
    assert all(len(row["sourceSha256"]) == 64 for row in asset_projection["assets"])
