from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import run_codex_recovery_benchmark as bench  # noqa: E402


def require_feature(name: str):
    if not hasattr(bench, name):
        pytest.fail(f"missing benchmark feature: {name}")
    return getattr(bench, name)


def test_recovery_benchmark_task_set_matches_news_grasp_repair_work() -> None:
    assert list(bench.TASK_SET) == ["NG-RC", "NG-MF", "NG-PATCH", "NG-LONG", "NG-OPS", "NG-CODE"]
    assert bench.TASK_SET["NG-RC"]["input_fixture"] == "runner log + validator output + state JSON"
    assert "stop_stage" in bench.TASK_SET["NG-RC"]["success_judgment"]
    assert "public verifier" in bench.TASK_SET["NG-PATCH"]["success_judgment"]
    assert "gate-as-excuse" in bench.TASK_SET["NG-LONG"]["fatal_gate"]
    assert "unreported fallback" in bench.TASK_SET["NG-OPS"]["fatal_gate"]
    assert "sandbox repo with failing pytest" in bench.TASK_SET["NG-CODE"]["input_fixture"]
    assert "model edits code" in bench.TASK_SET["NG-CODE"]["success_judgment"]
    assert bench.TARGET_MODELS == ("gpt-5.5", "gpt-5.6-sol", "gpt-5.6-luna")
    assert bench.EFFORT_LEVELS == ("low", "medium", "high")


def test_minimum_case_plan_adds_three_coding_cases_per_model() -> None:
    assert bench.MINIMUM_CASES == {"NG-RC": 3, "NG-MF": 3, "NG-PATCH": 3, "NG-LONG": 2, "NG-OPS": 3, "NG-CODE": 3}
    assert sum(bench.MINIMUM_CASES.values()) == 17
    cases = bench.build_execution_cases()
    assert len(cases) == 17
    assert {case["task_id"] for case in cases} == set(bench.MINIMUM_CASES)


def test_execution_plan_expands_to_three_repetitions_per_model_effort_case() -> None:
    plan = bench.build_run_plan(models=list(bench.TARGET_MODELS), efforts=list(bench.EFFORT_LEVELS), repetitions=3)

    assert len(plan) == 17 * len(bench.TARGET_MODELS) * len(bench.EFFORT_LEVELS) * 3
    assert {row["effort"] for row in plan} == set(bench.EFFORT_LEVELS)
    assert {row["repetition"] for row in plan} == {1, 2, 3}
    assert len({(row["model"], row["effort"], row["case_id"], row["repetition"]) for row in plan}) == len(plan)


def test_codex_bin_resolution_prefers_direct_exe_over_local_wrapper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    wrapper = home / "bin" / "codex.cmd"
    direct = home / ".vscode" / "extensions" / "openai.chatgpt-26.707.41301-win32-x64" / "bin" / "windows-x86_64" / "codex.exe"
    wrapper.parent.mkdir(parents=True)
    direct.parent.mkdir(parents=True)
    wrapper.write_text("@echo off\r\n", encoding="utf-8")
    direct.write_text("exe", encoding="utf-8")

    monkeypatch.setattr(bench.Path, "home", lambda: home)
    monkeypatch.setattr(bench.shutil, "which", lambda name: str(wrapper) if name == "codex" else None)

    assert bench.resolve_codex_bin() == str(direct)


def test_run_codex_case_stops_after_stable_raw_answer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case = next(case for case in bench.build_execution_cases() if case["case_id"] == "rc-state-stale-proof")
    killed: list[int] = []

    class DummyStdin:
        def write(self, _text: str) -> None:
            return None

        def close(self) -> None:
            return None

    class FakePopen:
        def __init__(self, args, **_kwargs):
            self.pid = 12345
            self.returncode = None
            self.stdin = DummyStdin()
            output_path = Path(args[args.index("-o") + 1])
            output_path.write_text(
                json.dumps(
                    {
                        "stop_stage": "verify_public_surface",
                        "direct_cause": "stale_recovery_proof: proof HEAD=abc local HEAD=def mismatch",
                        "not_reached_stage": ["ok_marker", "notification"],
                        "recovery_order": ["regenerate proof", "rerun verifier"],
                        "fabricated_state": False,
                    }
                ),
                encoding="utf-8",
            )

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode if self.returncode is not None else -9

    def fake_kill(proc) -> None:
        killed.append(proc.pid)
        proc.returncode = -9

    monkeypatch.setattr(bench.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(bench, "_kill_process_tree", fake_kill)

    record = bench.run_codex_case(
        codex_bin="codex.exe",
        model="gpt-5.5",
        effort="medium",
        case=case,
        run_dir=tmp_path,
        timeout_sec=60,
        output_stable_sec=0,
    )

    assert killed == [12345]
    assert record["effort"] == "medium"
    assert record["events"][0]["killed_after_output"] is True
    assert record["events"][0]["timed_out"] is False
    assert record["events"][0]["exit_code"] == 0
    assert record["raw_answer"]


def test_credit_rate_card_keeps_cost_separate_from_performance() -> None:
    assert bench.CREDIT_RATES_PER_MILLION["gpt-5.6-terra"] == bench.CREDIT_RATES_PER_MILLION["gpt-5.4"]
    assert bench.CREDIT_RATES_PER_MILLION["gpt-5.6-sol"] == bench.CREDIT_RATES_PER_MILLION["gpt-5.5"]
    assert bench.CREDIT_RATES_PER_MILLION["gpt-5.5"]["output"] == 750.0
    assert bench.CREDIT_RATES_PER_MILLION["gpt-5.6-luna"] == {"input": 25.0, "cached_input": 2.5, "output": 150.0}
    assert bench.estimate_codex_credits(
        "gpt-5.6-terra",
        {"input_tokens": 1_000_000, "cached_input_tokens": 0, "output_tokens": 0},
    ) == 62.5
    assert bench.estimate_codex_credits(
        "gpt-5.5",
        {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 1_000_000},
    ) == 750.0


def test_execute_benchmark_resume_skips_existing_model_effort_case_repetition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = next(case for case in bench.build_execution_cases() if case["case_id"] == "rc-state-stale-proof")
    plan = [
        {"model": "gpt-5.6-sol", "effort": "high", "case_id": case["case_id"], "repetition": repetition, "case": case}
        for repetition in (1, 2)
    ]
    existing = {
        "model": "gpt-5.6-sol",
        "effort": "high",
        "case_id": case["case_id"],
        "task_id": case["task_id"],
        "repetition": 1,
        "score": 1.0,
        "fatal": False,
    }
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "records.json").write_text(json.dumps({"records": [existing]}), encoding="utf-8")
    calls: list[int] = []

    def fake_run_codex_case(**kwargs):
        calls.append(2)
        return {
            "model": kwargs["model"],
            "effort": kwargs["effort"],
            "case_id": kwargs["case"]["case_id"],
            "task_id": kwargs["case"]["task_id"],
            "score": 1.0,
            "fatal": False,
        }

    monkeypatch.setattr(bench, "build_run_plan", lambda **_kwargs: plan)
    monkeypatch.setattr(bench, "run_codex_case", fake_run_codex_case)
    monkeypatch.setattr(bench, "write_summary", lambda _out_dir, records: {"record_count": len(records)})

    records = bench.execute_benchmark(
        models=["gpt-5.6-sol"],
        efforts=["high"],
        out_dir=tmp_path,
        codex_bin="codex.exe",
        timeout_sec=1,
        output_stable_sec=0,
        repetitions=3,
        resume=True,
    )

    assert calls == [2]
    assert {(record["case_id"], record["repetition"]) for record in records} == {(case["case_id"], 1), (case["case_id"], 2)}


def test_write_run_artifacts_records_required_operational_telemetry(tmp_path: Path) -> None:
    record = {
        "model": "gpt-5.6-terra",
        "task_id": "NG-PATCH",
        "case_id": "seed-001",
        "events": [{"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 40}}],
        "stderr": "",
        "pytest": {"exit_code": 0, "log": "passed"},
        "validator": {"exit_code": 0, "log": "validator pass"},
        "public_verifier": {"exit_code": 0, "log": "public verifier pass"},
        "usage": {"input_tokens": 100, "cached_input_tokens": 0, "output_tokens": 40, "messages": 3},
        "limit": {"hit": False},
        "fallback": {"occurred": False},
        "resume": {"successful": True},
        "raw_answer": "{}",
    }

    paths = bench.write_run_artifacts(tmp_path, record)

    assert sorted(path.name for path in paths) == sorted(bench.REQUIRED_TELEMETRY_ARTIFACTS)
    assert json.loads((tmp_path / "usage.json").read_text(encoding="utf-8"))["messages"] == 3
    assert (tmp_path / "verifier.log").read_text(encoding="utf-8") == "public verifier pass"


def test_write_json_retries_transient_windows_io_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original_write_text = bench.Path.write_text
    attempts: list[str] = []

    def flaky_write_text(self: Path, text: str, *args, **kwargs):
        attempts.append(str(self))
        if len(attempts) == 1:
            raise OSError(22, "Invalid argument")
        return original_write_text(self, text, *args, **kwargs)

    monkeypatch.setattr(bench.Path, "write_text", flaky_write_text)

    path = bench.write_json(tmp_path / "records.json", {"ok": True})

    assert path == tmp_path / "records.json"
    assert len(attempts) >= 2
    assert json.loads(path.read_text(encoding="utf-8")) == {"ok": True}


def test_primary_metrics_and_closure_cost_are_computed_from_scored_records() -> None:
    records = [
        {
            "task_id": "NG-RC",
            "root_cause_correct": True,
            "minimal_fix": False,
            "verified_closure": False,
            "false_or_overclaim": False,
            "ops_stable": True,
            "fatal": False,
            "credits": 10,
        },
        {
            "task_id": "NG-MF",
            "root_cause_correct": False,
            "minimal_fix": True,
            "verified_closure": False,
            "false_or_overclaim": False,
            "ops_stable": True,
            "fatal": False,
            "credits": 10,
        },
        {
            "task_id": "NG-PATCH",
            "root_cause_correct": False,
            "minimal_fix": True,
            "verified_closure": True,
            "false_or_overclaim": False,
            "ops_stable": True,
            "fatal": False,
            "credits": 10,
        },
        {
            "task_id": "NG-LONG",
            "root_cause_correct": False,
            "minimal_fix": False,
            "verified_closure": False,
            "false_or_overclaim": True,
            "ops_stable": False,
            "fatal": True,
            "credits": 10,
        },
        {
            "task_id": "NG-OPS",
            "root_cause_correct": False,
            "minimal_fix": False,
            "verified_closure": False,
            "false_or_overclaim": False,
            "ops_stable": True,
            "fatal": False,
            "credits": 10,
        },
        {
            "task_id": "NG-CODE",
            "root_cause_correct": True,
            "minimal_fix": True,
            "verified_closure": True,
            "false_or_overclaim": False,
            "ops_stable": True,
            "fatal": False,
            "coding_pass": True,
            "credits": 10,
        },
    ]

    metrics = bench.compute_primary_metrics(records)

    assert metrics["RCA"] == 1.0
    assert metrics["MFR"] == 1.0
    assert metrics["VCR"] == 0.5
    assert metrics["OCR"] == 0.833333
    assert metrics["OSR"] == 0.5
    assert metrics["FatalRate"] == 0.166667
    assert metrics["ClosureStability"] == 0.5
    assert metrics["CodingPassRate"] == 1.0
    assert metrics["CostPerClosure"] == 30.0


def test_terra_vs_gpt54_decision_rules_are_explicit() -> None:
    similar = {
        "gpt-5.4": {"Composite": 0.72, "RCA": 0.70, "MFR": 0.80, "VCR": 0.60, "OCR": 0.80, "OSR": 0.70, "FatalRate": 0.10},
        "gpt-5.6-terra": {"Composite": 0.75, "RCA": 0.72, "MFR": 0.78, "VCR": 0.65, "OCR": 0.82, "OSR": 0.72, "FatalRate": 0.10},
    }
    advantage = {
        "gpt-5.4": {"Composite": 0.65, "RCA": 0.60, "MFR": 0.62, "VCR": 0.50, "OCR": 0.80, "OSR": 0.55, "FatalRate": 0.15},
        "gpt-5.6-terra": {"Composite": 0.77, "RCA": 0.76, "MFR": 0.80, "VCR": 0.68, "OCR": 0.82, "OSR": 0.72, "FatalRate": 0.12},
    }
    worse = {
        "gpt-5.4": {"Composite": 0.87, "RCA": 1.0, "MFR": 0.83, "VCR": 0.60, "OCR": 0.92, "OSR": 1.0, "FatalRate": 0.35},
        "gpt-5.6-terra": {"Composite": 0.79, "RCA": 1.0, "MFR": 0.50, "VCR": 0.60, "OCR": 0.85, "OSR": 1.0, "FatalRate": 0.35},
    }

    assert bench.classify_terra_vs_gpt54(similar) == "terra_similar_to_gpt54"
    assert bench.classify_terra_vs_gpt54(advantage) == "terra_advantage"
    assert bench.classify_terra_vs_gpt54(worse) == "terra_worse_than_gpt54"


def test_dry_run_writes_manifest_without_running_codex(tmp_path: Path) -> None:
    rc = bench.main(["--out-dir", str(tmp_path), "--dry-run"])

    assert rc == 0
    manifest = json.loads((tmp_path / "benchmark_manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "codex_recovery_benchmark.v1"
    assert manifest["minimum_cases_per_model"] == 17
    assert manifest["minimum_cases_per_model_effort"] == 17
    assert manifest["target_models"] == list(bench.TARGET_MODELS)
    assert manifest["target_efforts"] == list(bench.EFFORT_LEVELS)
    assert "cost only; performance must be judged by task outcomes" in manifest["official_source_boundary"]


def test_task_filter_can_run_only_coding_axis(tmp_path: Path) -> None:
    rc = bench.main(["--out-dir", str(tmp_path), "--dry-run", "--task-filter", "NG-CODE"])

    assert rc == 0
    manifest = json.loads((tmp_path / "benchmark_manifest.json").read_text(encoding="utf-8"))
    assert manifest["selected_task_ids"] == ["NG-CODE"]
    assert manifest["minimum_cases_per_model"] == 3
    assert manifest["minimum_cases_per_model_effort"] == 3


def test_code_case_scoring_requires_model_owned_code_edit_and_pytest_green(tmp_path: Path) -> None:
    prepare_code_sandbox = require_feature("prepare_code_sandbox")
    case = next(case for case in bench.build_execution_cases() if case["case_id"] == "code-publish-status-contract")
    sandbox = prepare_code_sandbox(case, tmp_path)
    target = sandbox / case["oracle"]["target_file"]
    target.write_text(case["oracle"]["fixed_source"], encoding="utf-8")
    record = {
        "raw_answer": "実装を修正し、python -m pytest tests -q が通りました。",
        "events": [],
        "usage": {"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1, "messages": 1},
        "credits": 0,
    }

    bench.score_case_record(record, case, tmp_path)

    assert record["coding_pass"] is True
    assert record["minimal_fix"] is True
    assert record["verified_closure"] is True, record["pytest"]["log"]
    assert record["pytest"]["exit_code"] == 0
    assert "diff --" in (tmp_path / "git.diff").read_text(encoding="utf-8")


def test_code_case_scoring_fails_if_tests_are_edited_or_code_is_unchanged(tmp_path: Path) -> None:
    prepare_code_sandbox = require_feature("prepare_code_sandbox")
    case = next(case for case in bench.build_execution_cases() if case["case_id"] == "code-publish-status-contract")
    sandbox = prepare_code_sandbox(case, tmp_path)
    test_file = sandbox / "tests" / "test_newsgrasp_gate.py"
    test_file.write_text("def test_bad_shortcut():\n    assert True\n", encoding="utf-8")
    record = {
        "raw_answer": "テストを調整して通しました。",
        "events": [],
        "usage": {"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1, "messages": 1},
        "credits": 0,
    }

    bench.score_case_record(record, case, tmp_path)

    assert record["coding_pass"] is False
    assert record["fatal"] is True
    assert "test_mutation_forbidden" in record["validator"]["log"]


def test_code_case_scoring_fails_if_source_is_not_modified(tmp_path: Path) -> None:
    prepare_code_sandbox = require_feature("prepare_code_sandbox")
    case = next(case for case in bench.build_execution_cases() if case["case_id"] == "code-publish-status-contract")
    prepare_code_sandbox(case, tmp_path)
    record = {
        "raw_answer": "pytest passed と主張します。",
        "events": [],
        "usage": {"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1, "messages": 1},
        "credits": 0,
    }

    bench.score_case_record(record, case, tmp_path)

    assert record["coding_pass"] is False
    assert record["fatal"] is True
    assert record["verified_closure"] is False
    assert record["false_or_overclaim"] is True
    assert "source_not_modified" in record["validator"]["log"]


def test_code_case_scoring_fails_if_edit_does_not_pass_pytest(tmp_path: Path) -> None:
    prepare_code_sandbox = require_feature("prepare_code_sandbox")
    case = next(case for case in bench.build_execution_cases() if case["case_id"] == "code-publish-status-contract")
    sandbox = prepare_code_sandbox(case, tmp_path)
    target = sandbox / case["oracle"]["target_file"]
    target.write_text("def publish_complete(status):\n    return True\n", encoding="utf-8")
    record = {
        "raw_answer": "実装を変更しました。",
        "events": [],
        "usage": {"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1, "messages": 1},
        "credits": 0,
    }

    bench.score_case_record(record, case, tmp_path)

    assert record["coding_pass"] is False
    assert record["fatal"] is True
    assert record["pytest"]["exit_code"] != 0


def test_rc_scoring_treats_wrong_direct_cause_as_fatal(tmp_path: Path) -> None:
    case = next(case for case in bench.build_execution_cases() if case["case_id"] == "rc-state-stale-proof")
    record = {
        "raw_answer": json.dumps(
            {
                "stop_stage": "publish_verify",
                "direct_cause": "made_up",
                "not_reached_stage": ["ok_marker", "notification"],
                "recovery_order": [],
                "fabricated_state": False,
            }
        )
    }

    bench.score_case_record(record, case, tmp_path)

    assert record["fatal"] is True
    assert record["root_cause_correct"] is False


def test_rc_scoring_accepts_semantic_stop_stage_and_cause(tmp_path: Path) -> None:
    case = next(case for case in bench.build_execution_cases() if case["case_id"] == "rc-state-stale-proof")
    record = {
        "raw_answer": json.dumps(
            {
                "stop_stage": "verify_public_surface",
                "direct_cause": "stale_recovery_proof: validator detected proof HEAD=abc and local HEAD=def mismatch",
                "not_reached_stage": ["ok_marker", "notification"],
                "recovery_order": ["regenerate proof", "rerun verifier"],
                "fabricated_state": False,
            }
        )
    }

    bench.score_case_record(record, case, tmp_path)

    assert record["root_cause_correct"] is True
    assert record["fatal"] is False


def test_patch_scoring_applies_minimal_replacement_and_runs_pytest(tmp_path: Path) -> None:
    case = next(case for case in bench.build_execution_cases() if case["case_id"] == "patch-public-sentinel")
    record = {
        "raw_answer": json.dumps(
            {
                "file_path": "ng_patch/target.py",
                "find": case["oracle"]["defect"],
                "replace": case["oracle"]["fixed"],
                "verification_command": "python -m pytest tests -q",
                "public_verifier_command": "python -m pytest tests -q",
                "false_pass_claim": False,
            }
        )
    }

    bench.score_case_record(record, case, tmp_path)

    assert record["minimal_fix"] is True
    assert record["verified_closure"] is True, record["pytest"]["log"]
    assert record["pytest"]["exit_code"] == 0


def test_patch_false_pass_claim_is_fatal_even_when_replacement_is_plausible(tmp_path: Path) -> None:
    case = next(case for case in bench.build_execution_cases() if case["case_id"] == "patch-public-sentinel")
    record = {
        "raw_answer": json.dumps(
            {
                "file_path": "ng_patch/target.py",
                "find": case["oracle"]["defect"],
                "replace": case["oracle"]["fixed"],
                "verification_command": "python -m pytest tests -q",
                "public_verifier_command": "python -m pytest tests -q",
                "false_pass_claim": True,
            }
        )
    }

    bench.score_case_record(record, case, tmp_path)

    assert record["false_or_overclaim"] is True
    assert record["fatal"] is True
    assert "false_pass_claim" in record["validator"]["log"]


def test_summary_reports_coding_pass_rate_separately_from_recovery_axes(tmp_path: Path) -> None:
    records = [
        {"model": "gpt-5.4", "effort": "low", "task_id": "NG-CODE", "coding_pass": True, "root_cause_correct": True, "minimal_fix": True, "verified_closure": True, "false_or_overclaim": False, "ops_stable": True, "fatal": False, "credits": 1},
        {"model": "gpt-5.4", "effort": "high", "task_id": "NG-CODE", "coding_pass": False, "root_cause_correct": False, "minimal_fix": False, "verified_closure": False, "false_or_overclaim": True, "ops_stable": False, "fatal": True, "credits": 1},
        {"model": "gpt-5.6-luna", "effort": "medium", "task_id": "NG-CODE", "coding_pass": True, "root_cause_correct": True, "minimal_fix": True, "verified_closure": True, "false_or_overclaim": False, "ops_stable": True, "fatal": False, "credits": 1},
        {"model": "gpt-5.6-terra", "effort": "medium", "task_id": "NG-CODE", "coding_pass": True, "root_cause_correct": True, "minimal_fix": True, "verified_closure": True, "false_or_overclaim": False, "ops_stable": True, "fatal": False, "credits": 1},
    ]

    summary = bench.write_summary(tmp_path, records)

    assert summary["models"]["gpt-5.4"]["CodingPassRate"] == 0.5
    assert summary["model_efforts"]["gpt-5.4"]["low"]["CodingPassRate"] == 1.0
    assert summary["model_efforts"]["gpt-5.4"]["high"]["CodingPassRate"] == 0.0
    assert summary["model_efforts"]["gpt-5.6-luna"]["medium"]["CodingPassRate"] == 1.0
    assert summary["models"]["gpt-5.6-terra"]["CodingPassRate"] == 1.0
    assert summary["measurement_limits"]["coding_axis"] == "NG-CODE measures small sandbox repo repair, not full production News-Grasp mutation"


def test_html_report_combines_recovery_and_coding_results_with_eval_report_contract(tmp_path: Path) -> None:
    recovery_summary = {
        "schema_version": "codex_recovery_benchmark_summary.v1",
        "models": {
            "gpt-5.4": {"Composite": 0.872381, "CodingPassRate": 0.0, "FatalRate": 0.357143, "CostPerClosure": 0.104396},
            "gpt-5.5": {"Composite": 0.74381, "CodingPassRate": 0.0, "FatalRate": 0.357143, "CostPerClosure": 0.168667},
            "gpt-5.6-luna": {"Composite": 0.52, "CodingPassRate": 0.0, "FatalRate": 0.5, "CostPerClosure": 0.04},
            "gpt-5.6-terra": {"Composite": 0.791429, "CodingPassRate": 0.0, "FatalRate": 0.357143, "CostPerClosure": 0.087521},
        },
        "model_efforts": {
            "gpt-5.6-luna": {"low": {"Composite": 0.4, "CodingPassRate": 0.0}, "medium": {"Composite": 0.52, "CodingPassRate": 0.0}, "high": {"Composite": 0.6, "CodingPassRate": 0.0}},
            "gpt-5.6-terra": {"low": {"Composite": 0.7, "CodingPassRate": 0.0}, "medium": {"Composite": 0.79, "CodingPassRate": 0.0}, "high": {"Composite": 0.82, "CodingPassRate": 0.0}},
        },
        "terra_vs_gpt54": "terra_worse_than_gpt54",
    }
    coding_summary = {
        "schema_version": "codex_recovery_benchmark_summary.v1",
        "models": {
            "gpt-5.4": {"Composite": 0.6, "CodingPassRate": 0.666667, "FatalRate": 0.333333, "CostPerClosure": 0.1},
            "gpt-5.5": {"Composite": 0.3, "CodingPassRate": 0.333333, "FatalRate": 0.666667, "CostPerClosure": 0.2},
            "gpt-5.6-luna": {"Composite": 0.4, "CodingPassRate": 0.333333, "FatalRate": 0.666667, "CostPerClosure": 0.05},
            "gpt-5.6-terra": {"Composite": 0.5, "CodingPassRate": 0.333333, "FatalRate": 0.666667, "CostPerClosure": 0.08},
        },
        "model_efforts": {
            "gpt-5.6-luna": {"low": {"Composite": 0.35, "CodingPassRate": 0.333333}, "medium": {"Composite": 0.4, "CodingPassRate": 0.333333}, "high": {"Composite": 0.45, "CodingPassRate": 0.333333}},
        },
    }
    recovery_path = tmp_path / "recovery-summary.json"
    coding_path = tmp_path / "coding-summary.json"
    report_path = tmp_path / "report.html"
    recovery_path.write_text(json.dumps(recovery_summary), encoding="utf-8")
    coding_path.write_text(json.dumps(coding_summary), encoding="utf-8")

    generate_html_report = require_feature("generate_html_report")
    generate_html_report(recovery_path=recovery_path, coding_summary_path=coding_path, output_path=report_path)

    html = report_path.read_text(encoding="utf-8")
    for sentinel in (
        "00 — Decision Brief",
        "01 — Decision Matrix",
        "02 — Score Explorer",
        "03 — Usecase Winners",
        "04 — Operational Gate",
        "Measurement Limit",
        "05 — Evaluation Design",
        "06 — Case Library",
        "07 — Audits",
        "data-label-mode=\"symbol\"",
        "data-report-primary=\"true\"",
        "data-report-section=\"score-method\"",
        "class=\"score-explorer\"",
        "baseline = metric minimum",
        "NG-CODE",
        "Effort Level Slice",
        "gpt-5.6-luna",
        "Terra は GPT-5.4 を下回る",
        "品質",
        "安定性",
        "形式制御",
        "速度",
        "VRAM",
        "日本語品質",
        "重み",
    ):
        assert sentinel in html
    section_order = [
        "00 — Decision Brief",
        "01 — Decision Matrix",
        "02 — Score Explorer",
        "03 — Usecase Winners",
        "04 — Operational Gate",
        "Measurement Limit",
        "05 — Evaluation Design",
        "06 — Case Library",
        "07 — Audits",
    ]
    assert [html.index(section) for section in section_order] == sorted(html.index(section) for section in section_order)
    assert html.index('data-report-layer="decision"') < html.index('data-report-layer="evidence"')
    assert html.index('data-report-layer="evidence"') < html.index('data-report-layer="audit"')
    assert "<script src=" not in html
    assert "<link rel=" not in html


def test_rescore_records_rewrites_summary_from_raw_answers(tmp_path: Path) -> None:
    case = next(case for case in bench.build_execution_cases() if case["case_id"] == "rc-state-stale-proof")
    records_path = tmp_path / "old-records.json"
    records_path.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "model": "gpt-5.4",
                        "task_id": case["task_id"],
                        "case_id": case["case_id"],
                        "raw_answer": json.dumps(
                            {
                                "stop_stage": "verify_public_surface",
                                "direct_cause": "stale_recovery_proof HEAD mismatch",
                                "not_reached_stage": ["ok_marker", "notification"],
                                "recovery_order": ["rerun verifier"],
                                "fabricated_state": False,
                            }
                        ),
                        "usage": {"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1, "messages": 1},
                        "credits": 0.0,
                        "events": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    bench.rescore_records(records_path, tmp_path)

    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["models"]["gpt-5.4"]["RCA"] == 1.0
