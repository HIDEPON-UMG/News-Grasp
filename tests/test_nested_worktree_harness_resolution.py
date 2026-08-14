from __future__ import annotations

from pathlib import Path
import json

import pytest

from tools import model_spawn_client
from tools.news_grasp_high_cost_binding import HighCostBindingError, resolve_binding

try:
    from tools import harness
except HighCostBindingError:
    harness = None


pytestmark = pytest.mark.skipif(
    harness is None,
    reason="live workspace high-cost binding is unavailable; isolated binding tests remain separate",
)


def _activate_binding(
    args: list[str], monkeypatch: pytest.MonkeyPatch
) -> Path:
    binding_path = Path(args[args.index("-HighCostBindingPath") + 1])
    receipt = args[args.index("-HighCostBindingReceiptSha256") + 1]
    monkeypatch.setenv("NEWS_GRASP_HIGH_COST_BINDING_PATH", str(binding_path))
    monkeypatch.setenv("NEWS_GRASP_HIGH_COST_BINDING_RECEIPT_SHA256", receipt)
    monkeypatch.delenv("NEWS_GRASP_HIGH_COST_TEST_WORKSPACE_ROOT", raising=False)
    return binding_path


def test_workspace_broker_uses_explicit_binding(
    canonical_model_broker: tuple[list[str], dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args, _ = canonical_model_broker
    binding_path = _activate_binding(args, monkeypatch)
    binding = resolve_binding(
        binding_path=binding_path,
        expected_receipt_sha256=args[
            args.index("-HighCostBindingReceiptSha256") + 1
        ],
    )
    assert model_spawn_client.resolve_broker_path() == Path(
        binding["brokerInstalledPath"]
    ).resolve()


def test_missing_explicit_binding_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NEWS_GRASP_HIGH_COST_BINDING_PATH", raising=False)
    monkeypatch.delenv("NEWS_GRASP_HIGH_COST_BINDING_RECEIPT_SHA256", raising=False)
    monkeypatch.delenv("NEWS_GRASP_HIGH_COST_TEST_WORKSPACE_ROOT", raising=False)
    with pytest.raises(RuntimeError, match="HIGH_COST_WORKSPACE_BINDING_MISSING"):
        model_spawn_client.resolve_broker_path()


def test_test_workspace_root_cannot_bypass_missing_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NEWS_GRASP_HIGH_COST_BINDING_PATH", raising=False)
    monkeypatch.delenv("NEWS_GRASP_HIGH_COST_BINDING_RECEIPT_SHA256", raising=False)
    monkeypatch.setenv("NEWS_GRASP_HIGH_COST_TEST_WORKSPACE_ROOT", str(tmp_path))
    with pytest.raises(RuntimeError, match="HIGH_COST_WORKSPACE_BINDING_MISSING"):
        harness.resolve_workspace_harness_path()


def test_harness_namespace_uses_same_explicit_binding(
    canonical_model_broker: tuple[list[str], dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args, _ = canonical_model_broker
    binding_path = _activate_binding(args, monkeypatch)
    binding = json.loads(binding_path.read_text(encoding="utf-8"))
    harness_root = Path(binding["workspaceRoot"]) / "tools" / "harness"
    assert harness.resolve_workspace_harness_path() == harness_root.resolve()


def test_nested_worktree_does_not_restore_ancestor_search(
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
    monkeypatch.delenv("NEWS_GRASP_HIGH_COST_BINDING_PATH", raising=False)
    monkeypatch.delenv("NEWS_GRASP_HIGH_COST_BINDING_RECEIPT_SHA256", raising=False)
    monkeypatch.delenv("NEWS_GRASP_HIGH_COST_TEST_WORKSPACE_ROOT", raising=False)
    monkeypatch.setattr(harness, "__file__", str(nested_module))

    with pytest.raises(RuntimeError, match="HIGH_COST_WORKSPACE_BINDING_MISSING"):
        harness.resolve_workspace_harness_path()
