from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import run_codex_recovery_benchmark as recovery
from tools import run_external_benchmark_matrix as external
from tools.benchmark_path_safety import safe_path_component


@pytest.mark.parametrize(
    "value",
    ["..", "../escape", r"..\escape", r"C:\escape", "/tmp/escape", "a/b", "CON", "aux.txt", "trailing."],
)
def test_safe_path_component_rejects_path_escape(value: str) -> None:
    with pytest.raises(ValueError, match="unsafe path component"):
        safe_path_component(value, field="model")


def test_safe_path_component_does_not_normalize_distinct_values_to_same_path() -> None:
    assert safe_path_component("a b", field="model") != safe_path_component("a_b", field="model")


def test_recovery_rescore_rejects_untrusted_model_path_before_writing(tmp_path: Path, monkeypatch) -> None:
    case = recovery.build_execution_cases()[0]
    records = tmp_path / "records.json"
    records.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "task_id": case["task_id"],
                        "case_id": case["case_id"],
                        "model": "../escape",
                        "effort": "high",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(recovery, "score_case_record", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(recovery, "write_run_artifacts", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(recovery, "write_summary", lambda *_args, **_kwargs: None)

    with pytest.raises(ValueError, match="unsafe path component"):
        recovery.rescore_records(records, tmp_path / "out")


def test_external_rescore_rejects_untrusted_repetition_path(tmp_path: Path) -> None:
    case = external.build_matrix_cases()[0]
    record = {
        "case_id": case["case_id"],
        "model": "gpt-5.6-luna",
        "repetition": "../escape",
        "pass": True,
    }

    with pytest.raises(ValueError, match="unsafe path component"):
        external._rescore_loaded_records([record], tmp_path / "out")


def test_external_rescore_keeps_effort_artifacts_separate(tmp_path: Path, monkeypatch) -> None:
    case = next(case for case in external.build_matrix_cases() if case["task_type"] == "JA_SUMMARY")
    seen: list[Path] = []

    def fake_score(record, _case, run_dir):  # type: ignore[no-untyped-def]
        seen.append(run_dir)
        record["pass"] = True

    monkeypatch.setattr(external, "score_case", fake_score)
    records = [
        {
            "case_id": case["case_id"],
            "model": "GPT-5.6 Luna",
            "effort": effort,
            "repetition": 1,
            "raw_answer": "{}",
        }
        for effort in ("low", "medium", "high")
    ]

    external._rescore_loaded_records(records, tmp_path / "out")

    assert len(set(seen)) == 3
    assert {path.parts[-3] for path in seen} == {"low", "medium", "high"}


def test_recovery_rescore_keeps_repetition_artifacts_separate(tmp_path: Path, monkeypatch) -> None:
    case = recovery.build_execution_cases()[0]
    records = tmp_path / "records.json"
    records.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "task_id": case["task_id"],
                        "case_id": case["case_id"],
                        "model": "gpt-5.6-luna",
                        "effort": "high",
                        "repetition": repetition,
                    }
                    for repetition in (1, 2, 3)
                ]
            }
        ),
        encoding="utf-8",
    )
    written: list[Path] = []
    monkeypatch.setattr(recovery, "score_case_record", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(recovery, "write_run_artifacts", lambda run_dir, _record: written.append(run_dir))
    monkeypatch.setattr(recovery, "write_summary", lambda *_args, **_kwargs: None)

    recovery.rescore_records(records, tmp_path / "out")

    assert len(set(written)) == 3
    assert {path.name for path in written} == {"repetition-1", "repetition-2", "repetition-3"}


def test_rescore_cli_requires_explicit_local_code_execution_opt_in(tmp_path: Path) -> None:
    records = tmp_path / "records.json"
    records.write_text('{"records": []}', encoding="utf-8")

    assert recovery.main(["--rescore-records", str(records), "--out-dir", str(tmp_path / "recovery")]) == 2
    assert external.main(["--score-file", str(records), "--out-dir", str(tmp_path / "external")]) == 2
