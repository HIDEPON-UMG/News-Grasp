from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "deploy-pages.yml"


def test_pages_deploy_workflow_uses_actions_source_and_node24_runtime() -> None:
    """legacy Pages build の Node20 warning を repo 管理 workflow で置き換える。"""
    assert WORKFLOW.exists(), "Pages deploy workflow is missing"
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "name: Deploy Pages" in text
    assert "branches: [main]" in text
    assert "contents: read" in text
    assert "pages: write" in text
    assert "id-token: write" in text
    assert "group: pages" in text
    assert "cancel-in-progress: false" in text
    assert "FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: true" in text
    assert "uses: actions/configure-pages@v6" in text
    assert "uses: actions/upload-pages-artifact@v5" in text
    assert "uses: actions/deploy-pages@v4" in text
    assert "path: ./docs" in text
