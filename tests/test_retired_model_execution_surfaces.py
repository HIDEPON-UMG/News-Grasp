from __future__ import annotations

import re

from tools import run_codex_recovery_benchmark as recovery
from tools import run_deepdive_terra_benchmark as legacy_deepdive
from tools import run_external_benchmark_matrix as external_matrix
from tools import run_model_benchmark as role_benchmark
from tools import run_model_eval as model_eval
from tools import run_newsroom_append_safety_benchmark as append_safety


RETIRED_MODEL_RE = re.compile(r"gpt-5\.4(?:-[a-z0-9.-]+)?|gpt-5\.6-terra", re.IGNORECASE)


def _assert_supported(models: list[str] | tuple[str, ...]) -> None:
    assert models
    assert not [model for model in models if RETIRED_MODEL_RE.fullmatch(model)]


def test_all_live_benchmark_execution_surfaces_exclude_retired_models() -> None:
    _assert_supported(recovery.TARGET_MODELS)
    _assert_supported(tuple(str(cfg["model"]) for cfg in model_eval.VARIANTS.values()))
    _assert_supported(tuple(model for cfg in role_benchmark.CASES.values() for model in cfg["models"]))
    _assert_supported(append_safety.MODELS)


def test_every_luna_execution_surface_uses_high_effort() -> None:
    luna_eval_variants = [cfg for cfg in model_eval.VARIANTS.values() if cfg["model"] == "gpt-5.6-luna"]
    assert luna_eval_variants
    assert {cfg["reasoning"] for cfg in luna_eval_variants} == {"high"}

    luna_role_cases = [cfg for cfg in role_benchmark.CASES.values() if "gpt-5.6-luna" in cfg["models"]]
    assert luna_role_cases
    assert {cfg["reasoning"] for cfg in luna_role_cases} == {"high"}
    assert append_safety.REASONING_EFFORT == "high"


def test_historical_comparison_runners_cannot_start_live_model_execution(tmp_path) -> None:
    assert external_matrix.LIVE_EXECUTION_DISABLED is True
    assert legacy_deepdive.LIVE_EXECUTION_DISABLED is True
    assert external_matrix.main(["--execute", "--out-dir", str(tmp_path / "external")]) == 2
    assert legacy_deepdive.main([
        "--codex-exe",
        str(tmp_path / "missing-codex.exe"),
        "--out-dir",
        str(tmp_path / "deepdive"),
    ]) == 2
