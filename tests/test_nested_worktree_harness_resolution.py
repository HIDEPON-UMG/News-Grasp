from __future__ import annotations

from pathlib import Path

import pytest

from tools import model_spawn_client
from tools import harness


def test_workspace_broker_uses_explicit_environment_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    broker = workspace / "tools/harness/model_spawn_broker.py"
    broker.parent.mkdir(parents=True)
    broker.write_text("VALUE = 1\n", encoding="utf-8")
    monkeypatch.setenv("NEWS_GRASP_HIGH_COST_WORKSPACE_ROOT", str(workspace))
    assert model_spawn_client.resolve_broker_path() == broker.resolve()


def test_missing_explicit_workspace_broker_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEWS_GRASP_HIGH_COST_WORKSPACE_ROOT", str(tmp_path))
    with pytest.raises(RuntimeError, match="MODEL_SPAWN_BROKER_UNAVAILABLE"):
        model_spawn_client.resolve_broker_path()


def test_harness_namespace_uses_same_explicit_workspace_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    harness_root = workspace / "tools/harness"
    harness_root.mkdir(parents=True)
    monkeypatch.setenv("NEWS_GRASP_HIGH_COST_WORKSPACE_ROOT", str(workspace))
    assert harness.resolve_workspace_harness_path() == harness_root.resolve()


def test_default_nested_worktree_walks_to_workspace_harness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    nested_module = (
        workspace
        / "_worktrees"
        / "News-Grasp-clean"
        / "tools"
        / "harness"
        / "__init__.py"
    )
    nested_module.parent.mkdir(parents=True)
    nested_module.write_text("# fixture\n", encoding="utf-8")
    harness_root = workspace / "tools" / "harness"
    harness_root.mkdir(parents=True)
    (harness_root / "model_spawn_broker.py").write_text(
        "# fixture\n", encoding="utf-8"
    )
    monkeypatch.delenv("NEWS_GRASP_HIGH_COST_WORKSPACE_ROOT", raising=False)
    monkeypatch.setattr(harness, "__file__", str(nested_module))

    assert harness.resolve_workspace_harness_path() == harness_root.resolve()
