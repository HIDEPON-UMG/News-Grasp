from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import ModuleType
from typing import Any


_BROKER_PATH = Path(__file__).resolve().parents[2] / "tools" / "harness" / "model_spawn_broker.py"


def resolve_broker_path() -> Path:
    workspace = os.environ.get("NEWS_GRASP_HIGH_COST_WORKSPACE_ROOT", "").strip()
    candidate = (
        Path(workspace).resolve() / "tools" / "harness" / "model_spawn_broker.py"
        if workspace
        else _BROKER_PATH.resolve()
    )
    if not candidate.is_file():
        raise RuntimeError("MODEL_SPAWN_BROKER_UNAVAILABLE")
    return candidate


def _load_broker() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "aiharness_model_spawn_broker", resolve_broker_path()
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("MODEL_SPAWN_BROKER_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_model_process(command: list[str], *, route: str, **kwargs: Any) -> Any:
    return _load_broker().run_model_subprocess(command, route=route, **kwargs)


def popen_model_process(command: list[str], *, route: str, **kwargs: Any) -> Any:
    return _load_broker().popen_model_process(command, route=route, **kwargs)
