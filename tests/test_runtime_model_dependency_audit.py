from __future__ import annotations

from pathlib import Path

from tools.audit_runtime_model_dependencies import audit_runtime_model_dependencies


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_audit_rejects_retired_models_in_production_case_insensitively(tmp_path: Path) -> None:
    _write(tmp_path, "tools/model_policy.py", 'MODEL = "GPT-5.4"\n')
    _write(tmp_path, "prompts/runner-prompt.md", "GPT-5.6 Terra\n")

    report = audit_runtime_model_dependencies(tmp_path)

    assert report["status"] == "fail"
    assert {row["token"] for row in report["prohibited"]} == {"GPT-5.4", "GPT-5.6 Terra"}


def test_audit_rejects_gpt54_suffix_and_unknown_paths(tmp_path: Path) -> None:
    _write(tmp_path, "tools/newsroom_preflight.py", 'MODEL = "gpt-5.4-mini"\n')
    _write(tmp_path, "misc/runtime.txt", "gpt-5.6-terra\n")

    report = audit_runtime_model_dependencies(tmp_path)

    assert report["status"] == "fail"
    assert report["prohibited"][0]["token"] == "gpt-5.4-mini"
    assert report["unknown"][0]["path"] == "misc/runtime.txt"


def test_audit_classifies_benchmark_and_content_evidence(tmp_path: Path) -> None:
    _write(tmp_path, "tools/run_model_benchmark.py", 'MODEL = "gpt-5.4"\n')
    _write(tmp_path, "data/articles.jsonl", '{"title":"GPT-5.6 Terra"}\n')

    report = audit_runtime_model_dependencies(tmp_path)

    assert report["status"] == "pass"
    assert report["prohibited"] == []
    assert report["unknown"] == []
    assert report["allowed_benchmark_history"][0]["reason"]
    assert report["allowed_content_evidence"][0]["reason"]


def test_current_repo_has_no_retired_production_model_dependency() -> None:
    report = audit_runtime_model_dependencies(Path("."))

    assert report["status"] == "pass", report
    assert report["prohibited"] == []
    assert report["unknown"] == []
