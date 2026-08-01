from __future__ import annotations

import ast
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "fixtures" / "deepdive_quality" / "tdd_acceptance_matrix.json"


def test_every_acceptance_has_a_unique_executable_fixture() -> None:
    value = json.loads(MATRIX.read_text(encoding="utf-8"))
    assert value["schemaVersion"] == "NEWS_GRASP_DEEPDIVE_TDD_ACCEPTANCE_MATRIX_V1"
    rows = value["rows"]
    assert len(rows) >= 35
    ids = [row["id"] for row in rows]
    fixtures = [row["fixture"] for row in rows]
    assert len(ids) == len(set(ids))
    assert len(fixtures) == len(set(fixtures))
    for row in rows:
        assert set(row) == {
            "id",
            "family",
            "polarity",
            "requirement",
            "failureMode",
            "fixture",
        }
        assert row["polarity"] in {"positive", "negative"}
        relative, function_name = row["fixture"].split("::", 1)
        path = ROOT / relative
        assert path.is_file(), row
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        functions = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert function_name in functions, row


def test_matrix_covers_each_failure_family_and_both_polarities() -> None:
    rows = json.loads(MATRIX.read_text(encoding="utf-8"))["rows"]
    counts = Counter(row["family"] for row in rows)
    assert counts["e2e_admission"] >= 12
    assert counts["url_provenance"] >= 10
    assert counts["podcast_value"] >= 9
    assert counts["route_parity"] >= 5
    assert {row["polarity"] for row in rows} == {"positive", "negative"}
