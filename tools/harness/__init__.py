"""workspace-global harness を product-local import 面へ接続する薄いadapter。"""
from __future__ import annotations

import os
from pathlib import Path


def resolve_workspace_harness_path() -> Path:
    workspace = os.environ.get("NEWS_GRASP_HIGH_COST_WORKSPACE_ROOT", "").strip()
    candidate = (
        Path(workspace).resolve() / "tools" / "harness"
        if workspace
        else Path(__file__).resolve().parents[3] / "tools" / "harness"
    )
    if not candidate.is_dir():
        raise RuntimeError(f"WORKSPACE_HARNESS_UNAVAILABLE: {candidate}")
    return candidate


_WORKSPACE_HARNESS = resolve_workspace_harness_path()

# 実装を複製せず、ProjectFolders正本だけを submodule 探索対象へ加える。
__path__.append(str(_WORKSPACE_HARNESS))
