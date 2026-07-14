from __future__ import annotations

import json
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = REPO_ROOT / "docs" / "benchmarks" / "external_benchmark_sources.json"
LOCAL_METHOD = REPO_ROOT / "docs" / "benchmarks" / "local_llm_comparison_materials.json"


def test_external_benchmark_source_snapshot_is_not_ad_hoc_or_unsourced() -> None:
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    sources = snapshot["sources"]
    required = {
        "human_eval",
        "mbpp",
        "swe_bench",
        "livecodebench",
        "jglue",
        "llm_jp_eval",
        "xlsum",
        "open_japanese_llm_leaderboard",
    }

    assert snapshot["schema_version"] == "external_benchmark_sources.v1"
    assert set(sources) >= required
    for source_id in required:
        source = sources[source_id]
        assert source["url"].startswith("https://")
        assert re.fullmatch(r"20\d{2}-\d{2}-\d{2}", source["checked_at"])
        assert source["benchmark_design_use"]
        assert source["license_or_access"]


def test_past_local_llm_method_is_primary_source_and_forbids_zero_one_scoring() -> None:
    materials = json.loads(LOCAL_METHOD.read_text(encoding="utf-8"))
    contract = materials["primary_method_contract"]

    assert materials["source_of_truth"]["role"] == "primary"
    assert contract["score_scale"] == {"min": 1, "max": 5, "meaning": "axis rubric score; 5 is best"}
    assert "blind candidate labels A/B/C" in contract["required_controls"]
    assert "completion rate separate from quality" in contract["required_controls"]
    assert "Do not use 0/1 pass-only scoring for model comparison." in materials["misuse_guards"]
