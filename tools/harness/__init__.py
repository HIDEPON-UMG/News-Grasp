"""workspace-global harness を product-local import 面へ接続する薄いadapter。"""
from __future__ import annotations

from pathlib import Path

from tools.news_grasp_high_cost_binding import resolve_binding_from_environment


def resolve_workspace_harness_path() -> Path:
    resolved = resolve_binding_from_environment()
    candidate = Path(str(resolved["workspaceRoot"])).resolve() / "tools" / "harness"
    if not candidate.is_dir():
        raise RuntimeError(f"WORKSPACE_HARNESS_UNAVAILABLE: {candidate}")
    return candidate


_WORKSPACE_HARNESS = resolve_workspace_harness_path()

# 実装を複製せず、ProjectFolders正本だけを submodule 探索対象へ加える。
__path__.append(str(_WORKSPACE_HARNESS))
