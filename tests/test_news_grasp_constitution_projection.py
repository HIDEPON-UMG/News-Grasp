from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import news_grasp_constitution as constitution


ROOT = Path(__file__).resolve().parents[1]


def test_collection_harness_root_accepts_explicit_absolute_cleanroom_binding(
    tmp_path: Path,
    monkeypatch,
) -> None:
    cleanroom = tmp_path / "cleanroom" / "repo"
    cleanroom.mkdir(parents=True)
    workspace = tmp_path / "workspace"
    (workspace / "tools" / "harness").mkdir(parents=True)
    monkeypatch.setenv("NEWS_GRASP_WORKSPACE_HARNESS_ROOT", str(workspace.resolve()))

    assert constitution._resolve_workspace_harness_root(cleanroom) == workspace.resolve()


def test_collection_harness_root_rejects_relative_binding(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NEWS_GRASP_WORKSPACE_HARNESS_ROOT", "relative-workspace")

    with pytest.raises(
        ValueError,
        match="CONSTITUTION_COLLECTION_HARNESS_ROOT_NOT_ABSOLUTE",
    ):
        constitution._resolve_workspace_harness_root(tmp_path / "repo")


def test_collection_harness_root_rejects_invalid_absolute_without_ambient_fallback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = tmp_path / "workspace" / "repo"
    repo.mkdir(parents=True)
    (tmp_path / "workspace" / "tools" / "harness").mkdir(parents=True)
    missing = (tmp_path / "missing-workspace").resolve()
    monkeypatch.setenv("NEWS_GRASP_WORKSPACE_HARNESS_ROOT", str(missing))

    with pytest.raises(ValueError, match="CONSTITUTION_COLLECTION_HARNESS_UNAVAILABLE"):
        constitution._resolve_workspace_harness_root(repo)


def test_active_universe_includes_transitive_runtime_consumers() -> None:
    discovered = constitution._discover_active_candidates(ROOT)

    assert "tools/news_grasp_title_control.py" in discovered
    assert "tools/validate_daily_quality.py" in discovered


def test_constitution_projection_is_exact_mirrored_and_generated() -> None:
    result = constitution.validate_constitution_projections(ROOT)

    assert result["status"] == "Green"
    assert result["agentProjectionMirror"] is True
    assert result["manualProjectionDrift"] is False
    assert result["mermaidDiagramCount"] == 3
    assert result["operationalDesignDiagramCount"] == 7
    assert result["htmlLineCount"] >= 100


def test_public_recovery_operational_design_is_trace_generated_and_hash_bound() -> None:
    result = constitution.validate_constitution_projections(ROOT)
    path = ROOT / constitution.PUBLIC_RECOVERY_OPERATIONAL_DESIGN_RELATIVE_PATH
    text = path.read_text(encoding="utf-8")
    projection = json.loads(
        (ROOT / constitution.PROJECTION_RELATIVE_PATH).read_text(encoding="utf-8")
    )
    receipt = projection["publicRecoveryOperationalDesign"]

    assert result["operationalDesignSha256"] == receipt["documentSha256"]
    assert receipt["requirementIds"] == [f"NG-RC-{number:02d}" for number in range(1, 7)]
    assert receipt["diagramCount"] == 7
    assert text.count("```mermaid") == 7
    for heading in (
        "As-Is System Context",
        "To-Be System Context",
        "Operational Use Cases",
        "Public Recovery L5 Sequence",
        "Post-public-Green State Machine",
        "Source / Worktree / Runtime Deployment",
        "Receipt / Ledger Data Model",
        "Operational Design Inventory",
        "FitGap",
        "Responsibility Matrix",
        "Requirement–Consumer–Fixture–Evidence Traceability",
        "Red / Green Matrix",
    ):
        assert heading in text
    assert text.count("node set SHA256") == 7
    assert text.count("edge set SHA256") == 7


def test_public_recovery_operational_design_rejects_unknown_edge_node() -> None:
    with pytest.raises(ValueError, match="PUBLIC_RECOVERY_DIAGRAM_EDGE_NODE_UNKNOWN"):
        constitution._public_recovery_mermaid(
            {
                "kind": "flowchart",
                "nodes": [["known", "Known"]],
                "edges": [["known", "unknown", "must fail"]],
            }
        )


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
