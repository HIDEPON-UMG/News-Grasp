from __future__ import annotations

from pathlib import Path

from tools.judge_deepdive_triad import JUDGE_MODEL, MODELS
from tools.judge_model_benchmark import JUDGE_MODEL as ROLE_JUDGE_MODEL


def test_all_judge_defaults_use_luna_high() -> None:
    assert set(ROLE_JUDGE_MODEL.values()) == {"gpt-5.6-luna"}
    assert JUDGE_MODEL == "gpt-5.6-luna"
    assert "gpt-5.6-terra" not in MODELS

    for relative in ("tools/judge_model_benchmark.py", "tools/judge_deepdive_triad.py"):
        source = Path(relative).read_text(encoding="utf-8-sig")
        assert 'model_reasoning_effort="high"' in source
        assert 'model_reasoning_effort="medium"' not in source
