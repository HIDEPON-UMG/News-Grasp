"""workspace-global harness を product-local import 面へ接続する薄いadapter。"""
from __future__ import annotations

from pathlib import Path


_WORKSPACE_HARNESS = Path(__file__).resolve().parents[3] / "tools" / "harness"
if not _WORKSPACE_HARNESS.is_dir():
    raise RuntimeError(f"WORKSPACE_HARNESS_UNAVAILABLE: {_WORKSPACE_HARNESS}")

# 実装を複製せず、ProjectFolders正本だけを submodule 探索対象へ加える。
__path__.append(str(_WORKSPACE_HARNESS))
