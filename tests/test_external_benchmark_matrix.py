from __future__ import annotations

import json
import importlib
import hashlib
import sys
from pathlib import Path
from collections import Counter, defaultdict

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def require_matrix():
    try:
        return importlib.import_module("tools.run_external_benchmark_matrix")
    except ModuleNotFoundError as exc:
        pytest.fail(f"missing external benchmark matrix runner: {exc}")


def test_external_benchmark_types_are_source_grounded_and_not_single_run() -> None:
    matrix = require_matrix()
    task_types = matrix.TASK_TYPES

    assert matrix.MIN_REPETITIONS == 3
    assert list(matrix.TARGET_MODELS) == ["GPT-5.5", "GPT-5.6 Terra", "GPT-5.4"]
    assert set(matrix.TARGET_MODELS) == {"GPT-5.5", "GPT-5.6 Terra", "GPT-5.4"}
    assert set(task_types) == {"CODE_REPAIR", "CODE_SYNTH", "JA_NLU", "JA_SUMMARY"}
    assert "HumanEval" in task_types["CODE_SYNTH"]["external_basis"]
    assert "MBPP" in task_types["CODE_SYNTH"]["external_basis"]
    assert "SWE-bench" in task_types["CODE_REPAIR"]["external_basis"]
    assert "LiveCodeBench" in task_types["CODE_REPAIR"]["external_basis"]
    assert "JGLUE" in task_types["JA_NLU"]["external_basis"]
    assert "llm-jp-eval" in task_types["JA_NLU"]["external_basis"]
    assert "XL-Sum" in task_types["JA_SUMMARY"]["external_basis"]
    assert task_types["CODE_REPAIR"]["oracle"] == "patch + pytest"
    assert task_types["JA_SUMMARY"]["measurement_limit"]


def test_matrix_cases_cover_coding_japanese_and_summary_axes() -> None:
    matrix = require_matrix()
    cases = matrix.build_matrix_cases()
    external_sources = matrix.EXTERNAL_SOURCES

    case_counts = Counter(case["task_type"] for case in cases)
    assert set(case_counts) == set(matrix.TASK_TYPES)
    assert all(count >= matrix.CASE_COUNT_MIN for count in case_counts.values())
    assert all(case["external_source_ids"] for case in cases)
    assert all(source_id in external_sources for case in cases for source_id in case["external_source_ids"])
    assert all(case["source_text"] for case in cases)
    assert all(case["source_sha256"] == hashlib.sha256(case["source_text"].encode("utf-8")).hexdigest() for case in cases)
    assert all(external_sources[source_id]["url"].startswith("https://") for case in cases for source_id in case["external_source_ids"])
    assert all(external_sources[source_id]["license_or_access"] for case in cases for source_id in case["external_source_ids"])
    assert all(case["fatal_gates"] for case in cases)
    assert all(case["difficulty_features"] for case in cases)
    assert all("easy_bypass_guard" in case["difficulty_features"] for case in cases)
    assert next(case for case in cases if case["task_type"] == "CODE_REPAIR")["run_mode"] == "sandbox_edit"
    assert next(case for case in cases if case["task_type"] == "CODE_SYNTH")["run_mode"] == "generated_code"
    assert next(case for case in cases if case["task_type"] == "JA_NLU")["run_mode"] == "json_answer"
    assert next(case for case in cases if case["task_type"] == "JA_SUMMARY")["run_mode"] == "json_answer"


def test_fixture_manifest_and_difficulty_features_prevent_easy_bypass() -> None:
    matrix = require_matrix()
    cases = matrix.build_matrix_cases()
    manifest = matrix.FIXTURE_MANIFEST

    assert matrix.CASE_COUNT_MIN >= 2
    assert set(manifest) == {case["case_id"] for case in cases}
    assert len({case["case_id"] for case in cases}) == len(cases)
    assert len({entry["source_id"] for entry in manifest.values()}) >= len(matrix.TASK_TYPES)

    nlu_labels = set()
    summary_guard_count = 0
    hashes_by_task_type: dict[str, set[str]] = defaultdict(set)
    for case in cases:
        entry = manifest[case["case_id"]]
        assert entry["case_id"] == case["case_id"]
        assert entry["source_id"] in case["external_source_ids"]
        assert entry["source_sha256"] == case["source_sha256"]
        assert entry["license_or_access"]
        assert entry["derivation_note"]
        hashes_by_task_type[case["task_type"]].add(case["source_sha256"])

        if case["task_type"] == "CODE_REPAIR":
            assert case["oracle"]["edge_test_count"] >= 4
            assert case["oracle"]["target_edit_required"] is True
            assert case["oracle"]["test_mutation_forbidden"] is True
        elif case["task_type"] == "CODE_SYNTH":
            assert case["oracle"]["hidden_test_count"] >= 4
            assert case["oracle"]["pytest_oracle"] is True
        elif case["task_type"] == "JA_NLU":
            nlu_labels.update(item["label"] for item in case["oracle"]["items"])
            assert case["oracle"]["requires_evidence_span"] is True
            assert case["oracle"]["requires_multiple_labels"] is True
            assert any("negation" in feature for feature in case["difficulty_features"])
        elif case["task_type"] == "JA_SUMMARY":
            summary_guard_count += 1
            assert len(case["oracle"]["required_terms"]) >= 2
            assert case["oracle"]["forbidden_claims"]
            assert case["oracle"]["fact_inversions"]
            assert case["oracle"]["omission_markers"]

    assert nlu_labels >= {"entailment", "contradiction", "neutral"}
    assert summary_guard_count >= matrix.CASE_COUNT_MIN
    assert all(len(hashes) >= matrix.CASE_COUNT_MIN for hashes in hashes_by_task_type.values())


def test_external_provenance_snapshot_is_hashed_and_resolves_sources() -> None:
    matrix = require_matrix()
    snapshot_path = matrix.external_provenance_snapshot_path()
    snapshot_text = snapshot_path.read_text(encoding="utf-8")
    snapshot = json.loads(snapshot_text)

    assert snapshot["schema_version"] == "external_benchmark_sources.v1"
    assert matrix.PROVENANCE_SNAPSHOT_SHA256 == hashlib.sha256(snapshot_text.encode("utf-8")).hexdigest()
    assert set(snapshot["sources"]) >= set(matrix.EXTERNAL_SOURCES)
    for source_id, source in matrix.EXTERNAL_SOURCES.items():
        entry = snapshot["sources"][source_id]
        assert entry["url"] == source["url"]
        assert entry["name"] == source["name"]
        assert entry["checked_at"]
        assert entry["benchmark_design_use"]


def test_local_llm_comparison_materials_are_primary_source_of_truth() -> None:
    matrix = require_matrix()
    materials = matrix.LOCAL_LLM_MATERIALS
    source = materials["source_of_truth"]
    contract = materials["primary_method_contract"]

    assert materials["schema_version"] == "local_llm_comparison_materials.v1"
    assert source["id"] == "ai_pulse_2026_06_04_local_llm_investigation"
    assert source["role"] == "primary"
    assert source["report_md"].endswith("AI-Pulse/docs/eval/2026-06-04_local_llm_investigation.md")
    assert source["raw_score_json"].endswith("AI-Pulse/docs/eval/2026-06-04_blind_judge_raw.json")
    assert contract["sample_count"] == 11
    assert contract["judged_count"] == 10
    assert contract["score_scale"] == {"min": 1, "max": 5, "meaning": "axis rubric score; 5 is best"}
    assert contract["quality_axes"] == ["factual", "summary", "points", "rationale", "overall"]
    assert "blind candidate labels A/B/C" in contract["required_controls"]
    assert "completion rate separate from quality" in contract["required_controls"]
    assert "latency separate from quality" in contract["required_controls"]
    assert any("Do not use reports/oss_model_coding_comparison" in guard for guard in materials["misuse_guards"])
    assert all(item["role"] != "primary" for item in materials["secondary_materials"])


def test_code_repair_scoring_requires_source_edit_tests_and_no_test_mutation(tmp_path: Path) -> None:
    matrix = require_matrix()
    case = next(case for case in matrix.build_matrix_cases() if case["task_type"] == "CODE_REPAIR")
    sandbox = matrix.prepare_sandbox_case(case, tmp_path)
    target = sandbox / case["oracle"]["target_file"]
    target.write_text(case["oracle"]["fixed_source"], encoding="utf-8")
    record = {"raw_answer": "pytest passed", "usage": {"input_tokens": 1, "output_tokens": 1, "cached_input_tokens": 0, "messages": 1}}

    matrix.score_case(record, case, tmp_path)

    assert record["pass"] is True
    assert record["fatal"] is False
    assert record["score"] == 10.0
    assert "diff --" in (tmp_path / "git.diff").read_text(encoding="utf-8")


def test_code_repair_scoring_fails_easy_oracle_bypass(tmp_path: Path) -> None:
    matrix = require_matrix()
    case = next(case for case in matrix.build_matrix_cases() if case["task_type"] == "CODE_REPAIR")
    sandbox = matrix.prepare_sandbox_case(case, tmp_path)
    test_file = sandbox / case["oracle"]["test_file"]
    test_file.write_text("def test_shortcut():\n    assert True\n", encoding="utf-8")
    record = {"raw_answer": "tests changed and passed", "usage": {"input_tokens": 1, "output_tokens": 1, "cached_input_tokens": 0, "messages": 1}}

    matrix.score_case(record, case, tmp_path)

    assert record["pass"] is False
    assert record["fatal"] is True
    assert "test_mutation_forbidden" in record["validator"]["log"]


def test_code_repair_scoring_rejects_no_edit_wrong_edit_and_edge_failure(tmp_path: Path) -> None:
    matrix = require_matrix()
    case = next(case for case in matrix.build_matrix_cases() if case["task_type"] == "CODE_REPAIR")

    matrix.prepare_sandbox_case(case, tmp_path / "no-edit")
    no_edit = {"raw_answer": "claimed done", "usage": {"input_tokens": 1, "output_tokens": 1, "cached_input_tokens": 0, "messages": 1}}
    matrix.score_case(no_edit, case, tmp_path / "no-edit")
    assert no_edit["pass"] is False
    assert no_edit["fatal"] is True
    assert no_edit["score"] == 0.0
    assert "no_source_edit" in no_edit["validator"]["log"]

    wrong_sandbox = matrix.prepare_sandbox_case(case, tmp_path / "wrong-edit")
    target = wrong_sandbox / case["oracle"]["target_file"]
    target.write_text(case["oracle"]["wrong_source"], encoding="utf-8")
    wrong_edit = {"raw_answer": "edited source", "usage": {"input_tokens": 1, "output_tokens": 1, "cached_input_tokens": 0, "messages": 1}}
    matrix.score_case(wrong_edit, case, tmp_path / "wrong-edit")
    assert wrong_edit["pass"] is False
    assert wrong_edit["fatal"] is False
    assert 0.0 < wrong_edit["score"] < 10.0
    assert "pytest_failed" in wrong_edit["validator"]["log"]

    edge_sandbox = matrix.prepare_sandbox_case(case, tmp_path / "edge-fail")
    edge_target = edge_sandbox / case["oracle"]["target_file"]
    edge_target.write_text(case["oracle"]["edge_failure_source"], encoding="utf-8")
    edge_failure = {"raw_answer": "edited source but missed edge cases"}
    matrix.score_case(edge_failure, case, tmp_path / "edge-fail")
    assert edge_failure["pass"] is False
    assert edge_failure["fatal"] is False
    assert 0.0 < edge_failure["score"] < 10.0
    assert "edge_tests_failed" in edge_failure["validator"]["log"]


def test_code_synth_scoring_uses_generated_code_pytest_oracle(tmp_path: Path) -> None:
    matrix = require_matrix()
    case = next(case for case in matrix.build_matrix_cases() if case["task_type"] == "CODE_SYNTH")
    good = {
        "raw_answer": json.dumps(
            {
                "code": case["oracle"]["reference_solution"],
                "verification_command": "python -m pytest tests -q",
            }
        )
    }
    matrix.score_case(good, case, tmp_path / "good-code")
    assert good["pass"] is True
    assert good["score"] == 10.0

    bad = {
        "raw_answer": json.dumps(
            {
                "code": "def build_digest_windows(events):\n    return []\n",
                "verification_command": "python -m pytest tests -q",
            }
        )
    }
    matrix.score_case(bad, case, tmp_path / "bad-code")
    assert bad["pass"] is False
    assert bad["fatal"] is False
    assert 0.0 < bad["score"] < 10.0
    assert "pytest_failed" in bad["validator"]["log"]


def test_fatal_severity_boundary_always_zeroes_score_and_fails() -> None:
    matrix = require_matrix()
    record = {
        "pass": True,
        "fatal": True,
        "score": 0.75,
        "validator": {"log": ["hallucinated_or_forbidden_claim"]},
    }

    matrix.apply_severity_tiers(record)

    assert record["pass"] is False
    assert record["fatal"] is True
    assert record["score"] == 0.0
    assert record["quality_score_1_to_5"] == 1.0


def test_json_answer_scoring_is_mechanical_and_detects_hallucination(tmp_path: Path) -> None:
    matrix = require_matrix()
    nlu = next(case for case in matrix.build_matrix_cases() if case["task_type"] == "JA_NLU")
    summary = next(case for case in matrix.build_matrix_cases() if case["task_type"] == "JA_SUMMARY")

    nlu_record = {
        "raw_answer": json.dumps(
            {
                "answers": [
                    {"id": item["id"], "label": item["label"], "evidence": item["evidence"]}
                    for item in nlu["oracle"]["items"]
                ]
            },
            ensure_ascii=False,
        )
    }
    matrix.score_case(nlu_record, nlu, tmp_path / "nlu")
    assert nlu_record["pass"] is True

    wrong_answers = [
        {"id": item["id"], "label": item["label"], "evidence": item["evidence"]}
        for item in nlu["oracle"]["items"]
    ]
    wrong_answers[0]["label"] = "entailment" if wrong_answers[0]["label"] != "entailment" else "contradiction"
    wrong_label = {"raw_answer": json.dumps({"answers": wrong_answers}, ensure_ascii=False)}
    matrix.score_case(wrong_label, nlu, tmp_path / "wrong")
    assert wrong_label["pass"] is False
    assert wrong_label["fatal"] is False
    assert 0.0 < wrong_label["score"] < 10.0
    assert "wrong_label" in wrong_label["validator"]["log"]

    missing_answers = [
        {"id": item["id"], "label": item["label"], "evidence": item["evidence"]}
        for item in nlu["oracle"]["items"]
    ]
    missing_answers[0]["evidence"] = ""
    missing_evidence = {"raw_answer": json.dumps({"answers": missing_answers}, ensure_ascii=False)}
    matrix.score_case(missing_evidence, nlu, tmp_path / "missing")
    assert missing_evidence["pass"] is False
    assert 0.0 < missing_evidence["score"] < 10.0
    assert "missing_required_evidence" in missing_evidence["validator"]["log"]

    unrelated_answers = [
        {"id": item["id"], "label": item["label"], "evidence": item["evidence"]}
        for item in nlu["oracle"]["items"]
    ]
    unrelated_answers[0]["evidence"] = "これは本文にない証拠です"
    unrelated_evidence = {"raw_answer": json.dumps({"answers": unrelated_answers}, ensure_ascii=False)}
    matrix.score_case(unrelated_evidence, nlu, tmp_path / "unrelated")
    assert unrelated_evidence["pass"] is False
    assert unrelated_evidence["score"] == 0.0
    assert "evidence_not_in_source" in unrelated_evidence["validator"]["log"]

    wrong_span_answers = [
        {"id": item["id"], "label": item["label"], "evidence": item["evidence"]}
        for item in nlu["oracle"]["items"]
    ]
    wrong_span_answers[0]["evidence"] = nlu["oracle"]["wrong_but_in_source_evidence"]
    wrong_span = {"raw_answer": json.dumps({"answers": wrong_span_answers}, ensure_ascii=False)}
    matrix.score_case(wrong_span, nlu, tmp_path / "wrong-span")
    assert wrong_span["pass"] is False
    assert 0.0 < wrong_span["score"] < 10.0
    assert "evidence_not_allowed" in wrong_span["validator"]["log"]

    invalid_json = {"raw_answer": "ラベルは contradiction です"}
    matrix.score_case(invalid_json, nlu, tmp_path / "invalid")
    assert invalid_json["pass"] is False
    assert invalid_json["score"] == 0.0
    assert "invalid_json" in invalid_json["validator"]["log"]

    summary_record = {
        "raw_answer": json.dumps(
            {
                "headline": "誤った資金調達ニュース",
                "bullets": ["資金調達があった", "全カテゴリを毎日配信する"],
                "must_not_claim": [],
            },
            ensure_ascii=False,
        )
    }
    matrix.score_case(summary_record, summary, tmp_path / "summary")
    assert summary_record["pass"] is False
    assert summary_record["fatal"] is True
    assert summary_record["score"] == 0.0
    assert "hallucinated_or_forbidden_claim" in summary_record["validator"]["log"]

    inverted_fact = {
        "raw_answer": json.dumps(
            {
                "headline": summary["oracle"]["required_terms"][0],
                "bullets": ["公開URL 200 だけで完了と判定できる", summary["oracle"]["required_terms"][1]],
                "must_not_claim": summary["oracle"]["must_not_claim"],
            },
            ensure_ascii=False,
        )
    }
    matrix.score_case(inverted_fact, summary, tmp_path / "inverted")
    assert inverted_fact["pass"] is False
    assert inverted_fact["score"] == 0.0
    assert "fact_inversion" in inverted_fact["validator"]["log"]

    missing_must_not = {
        "raw_answer": json.dumps(
            {
                "headline": summary["oracle"]["required_terms"][0],
                "bullets": summary["oracle"]["required_terms"],
                "must_not_claim": [],
            },
            ensure_ascii=False,
        )
    }
    matrix.score_case(missing_must_not, summary, tmp_path / "missing-must-not")
    assert missing_must_not["pass"] is False
    assert missing_must_not["fatal"] is False
    assert 0.0 < missing_must_not["score"] < 10.0
    assert "missing_must_not_claim_acknowledgement" in missing_must_not["validator"]["log"]

    good_summary = {
        "raw_answer": json.dumps(
            {
                "headline": summary["oracle"]["required_terms"][0],
                "bullets": summary["oracle"]["required_terms"],
                "must_not_claim": summary["oracle"]["must_not_claim"],
            },
            ensure_ascii=False,
        )
    }
    matrix.score_case(good_summary, summary, tmp_path / "good-summary")
    assert good_summary["pass"] is True
    assert good_summary["fatal"] is False
    assert good_summary["score"] == pytest.approx(10.0)


def test_local_llm_style_scoring_policy_uses_partial_scores_and_separate_operational_metrics() -> None:
    matrix = require_matrix()

    assert matrix.SCORE_SCALE_MAX == 10.0
    assert matrix.QUALITY_SCORE_MIN == 1.0
    assert matrix.QUALITY_SCORE_MAX == 5.0
    assert matrix.SCORING_POLICY["local_llm_investigation_method"] == "AI-Pulse 2026-06-04 primary report"
    assert matrix.SCORING_POLICY["local_llm_materials_sha256"] == matrix.LOCAL_LLM_MATERIALS_SHA256
    assert matrix.SCORING_POLICY["local_llm_quality_scale"] == {"min": 1, "max": 5}
    assert matrix.DECISION_WEIGHTS == {
        "coding_generation": 0.25,
        "repair_patch": 0.20,
        "japanese_nlu": 0.20,
        "grounded_summary": 0.20,
        "format_control": 0.15,
    }
    assert "runtime" not in matrix.DECISION_WEIGHTS
    assert "credits" not in matrix.DECISION_WEIGHTS
    assert matrix.SCORING_POLICY["fatal_gate_zeroes_oracle_score_only"] is True
    assert matrix.SCORING_POLICY["quality_score_floor_1_to_5"] == 1.0
    assert matrix.SCORING_POLICY["partial_credit_for_nonfatal_defects"] is True
    assert matrix.SCORING_POLICY["speed_credits_excluded_from_quality_score"] is True


def test_aggregation_uses_three_repetition_mean_and_stdev() -> None:
    matrix = require_matrix()
    records = [
        {"model": "gpt-5.4", "task_type": "CODE_REPAIR", "pass": True, "score": 1.0, "fatal": False, "credits": 1, "repetition": 1},
        {"model": "gpt-5.4", "task_type": "CODE_REPAIR", "pass": False, "score": 0.0, "fatal": True, "credits": 1, "repetition": 2},
        {"model": "gpt-5.4", "task_type": "CODE_REPAIR", "pass": True, "score": 1.0, "fatal": False, "credits": 1, "repetition": 3},
        {"model": "gpt-5.4", "task_type": "JA_SUMMARY", "pass": True, "score": 0.75, "fatal": False, "credits": 1, "repetition": 1},
        {"model": "gpt-5.4", "task_type": "JA_SUMMARY", "pass": True, "score": 0.50, "fatal": False, "credits": 1, "repetition": 2},
        {"model": "gpt-5.4", "task_type": "JA_SUMMARY", "pass": True, "score": 1.0, "fatal": False, "credits": 1, "repetition": 3},
    ]

    summary = matrix.aggregate_records(records, allow_partial=True)

    assert summary["models"]["gpt-5.4"]["runs"] == 6
    assert summary["models"]["gpt-5.4"]["repetitions_min"] == 3
    assert summary["models"]["gpt-5.4"]["task_types"]["CODE_REPAIR"]["mean_score"] == pytest.approx(0.666667)
    assert summary["models"]["gpt-5.4"]["local_llm_task_projection"]["CODE_REPAIR"]["mean_score_1_to_5"] == pytest.approx(1.266667)
    assert summary["local_llm_materials"]["source_of_truth"]["id"] == "ai_pulse_2026_06_04_local_llm_investigation"
    assert summary["local_llm_materials"]["primary_method_contract"]["score_scale"]["max"] == 5
    assert summary["strength_exchange_summary"]
    assert summary["models"]["gpt-5.4"]["task_types"]["CODE_REPAIR"]["stdev_score"] > 0
    assert summary["models"]["gpt-5.4"]["macro_mean"] == pytest.approx(0.729167)


def test_repetition_contract_rejects_under_three_runs(tmp_path: Path) -> None:
    matrix = require_matrix()

    assert matrix.main(["--out-dir", str(tmp_path / "r1"), "--dry-run", "--repetitions", "1"]) == 2
    assert matrix.main(["--out-dir", str(tmp_path / "r2"), "--dry-run", "--repetitions", "2"]) == 2

    single_run_records = [
        {"model": "gpt-5.4", "task_type": "CODE_REPAIR", "case_id": "x", "pass": True, "score": 1.0, "fatal": False, "credits": 1, "repetition": 1}
    ]
    with pytest.raises(ValueError, match="minimum 3 repetitions"):
        matrix.aggregate_records(single_run_records)


def test_final_summary_and_report_reject_incomplete_task_type_slice(tmp_path: Path) -> None:
    matrix = require_matrix()
    partial_records = [
        {"model": "gpt-5.4", "task_type": "CODE_REPAIR", "case_id": "x", "pass": True, "score": 1.0, "fatal": False, "credits": 1, "repetition": 1},
        {"model": "gpt-5.4", "task_type": "CODE_REPAIR", "case_id": "x", "pass": True, "score": 1.0, "fatal": False, "credits": 1, "repetition": 2},
        {"model": "gpt-5.4", "task_type": "CODE_REPAIR", "case_id": "x", "pass": True, "score": 1.0, "fatal": False, "credits": 1, "repetition": 3},
    ]

    with pytest.raises(ValueError, match="complete task type coverage"):
        matrix.write_summary(tmp_path, partial_records)

    partial_summary = matrix.aggregate_records(partial_records, allow_partial=True)
    with pytest.raises(ValueError, match="single-run or partial decision is forbidden"):
        matrix.generate_html_report(partial_summary, tmp_path / "partial.html")


def make_complete_records(matrix):
    records = []
    for model in matrix.TARGET_MODELS:
        for case in matrix.build_matrix_cases():
            for repetition in range(1, matrix.MIN_REPETITIONS + 1):
                records.append(
                    {
                        "model": model,
                        "task_type": case["task_type"],
                        "case_id": case["case_id"],
                        "pass": True,
                        "score": 1.0,
                        "fatal": False,
                        "credits": 1,
                        "messages": 1,
                        "repetition": repetition,
                    }
                )
    return records


def test_balanced_model_case_repetition_coverage_is_required_for_decision(tmp_path: Path) -> None:
    matrix = require_matrix()
    complete_records = make_complete_records(matrix)
    missing_model = [record for record in complete_records if record["model"] != matrix.TARGET_MODELS[-1]]

    with pytest.raises(ValueError, match="balanced coverage"):
        matrix.write_summary(tmp_path / "missing-model", missing_model)

    wrong_model = [dict(record, model="GPT-5.6 Sol") if record["model"] == matrix.TARGET_MODELS[0] else record for record in complete_records]
    with pytest.raises(ValueError, match="balanced coverage"):
        matrix.write_summary(tmp_path / "wrong-model", wrong_model)

    missing_case = complete_records[:-1]
    with pytest.raises(ValueError, match="balanced coverage"):
        matrix.write_summary(tmp_path / "missing-case", missing_case)

    summary = matrix.write_summary(tmp_path / "complete", complete_records)
    by_model_task = defaultdict(set)
    for record in complete_records:
        by_model_task[(record["model"], record["task_type"])].add(record["case_id"])

    assert summary["complete_coverage"] is True
    assert set(summary["models"]) == set(matrix.TARGET_MODELS)
    assert all(len(case_ids) >= matrix.CASE_COUNT_MIN for case_ids in by_model_task.values())

    broken_summary = dict(summary)
    broken_summary["coverage_matrix"] = dict(summary["coverage_matrix"])
    broken_summary["coverage_matrix"]["missing"] = [
        {"model": matrix.TARGET_MODELS[0], "case_id": matrix.build_matrix_cases()[0]["case_id"], "repetition": 3}
    ]
    with pytest.raises(ValueError, match="balanced coverage"):
        matrix.generate_html_report(broken_summary, tmp_path / "broken-coverage.html")


def test_dry_run_and_html_report_include_external_sources_and_repetition_contract(tmp_path: Path) -> None:
    matrix = require_matrix()
    rc = matrix.main(["--out-dir", str(tmp_path), "--dry-run", "--repetitions", "3"])
    manifest = json.loads((tmp_path / "benchmark_manifest.json").read_text(encoding="utf-8"))

    assert rc == 0
    assert manifest["minimum_repetitions"] == 3
    assert manifest["single_run_decision_allowed"] is False
    assert manifest["provenance_snapshot_sha256"] == matrix.PROVENANCE_SNAPSHOT_SHA256
    assert manifest["local_llm_materials_sha256"] == matrix.LOCAL_LLM_MATERIALS_SHA256
    assert manifest["local_llm_source_of_truth"]["id"] == "ai_pulse_2026_06_04_local_llm_investigation"
    assert manifest["local_llm_primary_method_contract"]["quality_axes"] == ["factual", "summary", "points", "rationale", "overall"]
    assert "HumanEval" in manifest["external_sources"]["human_eval"]["name"]
    assert "XL-Sum" in manifest["external_sources"]["xlsum"]["name"]

    records = make_complete_records(matrix)
    summary = matrix.write_summary(tmp_path, records)
    report = matrix.generate_html_report(summary, tmp_path / "external-report.html")
    html = report.read_text(encoding="utf-8")

    assert "External Benchmark Grounding" in html
    assert "minimum repetitions: 3" in html
    assert "single-run decision is forbidden" in html
    assert "Local LLM Method Projection" in html
    assert "AI-Pulse" in html
    assert "盲検実施済みとは主張しない" in html
    assert "Decision Matrix" in html
    assert "Score Explorer" in html
    assert "Usecase Winners" in html
    assert "Operational Gate" in html
    assert "Measurement Limit" in html
    assert "Evaluation Design" in html
    assert "Case Library" in html
    assert "Harness Audit" in html
    assert 'class="hero-grid"' in html
    assert 'class="score-explorer"' in html
    assert 'class="winner-grid"' in html
    assert 'class="tabs"' not in html
    assert 'role="tablist"' not in html
    assert "1-5 projection" in html
    assert "今回の根本問題" in html
    assert html.count('class="reflection-row"') >= 30
    assert "HumanEval" in html and "JGLUE" in html and "XL-Sum" in html
    assert 'data-report-primary="true"' in html
    assert 'data-report-section="score-method"' in html
    assert 'data-report-section="local-llm-projection"' in html
    assert all(term in html for term in ["品質", "安定性", "形式制御", "速度", "VRAM", "日本語品質", "重み"])
    assert 'data-report-section="decision-support"' in html
    assert "意思決定者向けサマリ" in html
    assert all(term in html for term in ["採用判断", "判断理由", "主要リスク", "次アクション"])
    assert "単一の総合点で採否を決めない" in html
    assert "H-00" in html
    assert 'data-label-mode="symbol"' in html
    assert 'class="label-toggle"' in html
    assert "M1–Mn" in html and "モデル名" in html


def test_html_report_follows_reference_linear_structure_not_tabbed_summary(tmp_path: Path) -> None:
    matrix = require_matrix()
    records = make_complete_records(matrix)
    summary = matrix.write_summary(tmp_path, records)
    report = matrix.generate_html_report(summary, tmp_path / "reference-structure.html")
    html = report.read_text(encoding="utf-8")

    expected_order = [
        "GPT External Benchmark Matrix",
        "用途別判断",
        "Score Method",
        "Local LLM Method Projection",
        "Task Type Mean Scores",
        "Usecase Winners",
        "Operational Gate",
        "Measurement Limit",
        "External Benchmark Grounding",
        "Case Library",
        "Harness Audit",
    ]
    positions = [html.index(term) for term in expected_order]

    assert positions == sorted(positions)
    assert html.count('class="card') >= 4
    assert html.count('class="case-card"') >= len(matrix.build_matrix_cases())
    assert "M1" in html and "M2" in html and "M3" in html
    assert "速度・VRAM・credits は品質点に加算しない" in html
    assert "新規live実行ではなく既存run再集計" in html


def test_html_report_is_decision_support_document_not_visual_shell(tmp_path: Path) -> None:
    matrix = require_matrix()
    records = make_complete_records(matrix)
    summary = matrix.write_summary(tmp_path, records)
    report = matrix.generate_html_report(summary, tmp_path / "decision-support.html")
    html = report.read_text(encoding="utf-8")

    required_terms = [
        "意思決定者向けサマリ",
        "採用判断",
        "判断理由",
        "主要リスク",
        "次アクション",
        "どのモデルを、どの用途で、どの条件なら使うか",
        "単一の総合点で採否を決めない",
        "品質トップでも運用主力とは限らない",
        "Terra が GPT-5.4 程度か",
        "追加で必要な証拠",
    ]

    for term in required_terms:
        assert term in html

    assert html.index("意思決定者向けサマリ") < html.index("用途別判断")
    assert html.index("用途別判断") < html.index("Score Method")
    assert html.count('class="decision-row"') >= len(matrix.TARGET_MODELS)
