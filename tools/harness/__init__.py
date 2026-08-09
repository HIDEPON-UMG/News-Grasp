"""workspace-global harness を product-local import 面へ接続する薄いadapter。"""
from __future__ import annotations

import os
from pathlib import Path


def resolve_workspace_harness_path() -> Path:
    workspace = os.environ.get("NEWS_GRASP_HIGH_COST_WORKSPACE_ROOT", "").strip()
    if workspace:
        candidate = Path(workspace).resolve() / "tools" / "harness"
        if candidate.is_dir():
            return candidate
        raise RuntimeError(f"WORKSPACE_HARNESS_UNAVAILABLE: {candidate}")

    source = Path(__file__).resolve()
    checked: list[Path] = []
    for ancestor in source.parents:
        candidate = ancestor / "tools" / "harness"
        checked.append(candidate)
        if (
            candidate.is_dir()
            and (candidate / "model_spawn_broker.py").is_file()
        ):
            return candidate.resolve()
    raise RuntimeError(
        "WORKSPACE_HARNESS_UNAVAILABLE: "
        + ", ".join(str(path) for path in checked)
    )


_WORKSPACE_HARNESS = resolve_workspace_harness_path()

# 実装を複製せず、ProjectFolders正本だけを submodule 探索対象へ加える。
__path__.append(str(_WORKSPACE_HARNESS))
