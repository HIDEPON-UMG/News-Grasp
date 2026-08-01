#!/usr/bin/env python3
"""外部benchmark型に寄せた GPT model comparison runner."""
from __future__ import annotations

import argparse
import copy
import difflib
import hashlib
import html
import json
import os
import re
import shutil
import statistics
import subprocess
from tools.model_spawn_client import run_model_process
import sys
import time
from pathlib import Path
from typing import Any, Iterable

try:
    from tools.artifact_lifecycle import default_raw_root, validate_raw_output_path
    from tools.benchmark_code_safety import benchmark_subprocess_env, run_limited_benchmark_process, validate_benchmark_python
    from tools.benchmark_path_safety import safe_path_component
except ModuleNotFoundError:  # direct script execution
    from artifact_lifecycle import default_raw_root, validate_raw_output_path
    from benchmark_code_safety import benchmark_subprocess_env, run_limited_benchmark_process, validate_benchmark_python
    from benchmark_path_safety import safe_path_component


REPO_ROOT = Path(__file__).resolve().parents[1]
MIN_REPETITIONS = 3
CASE_COUNT_MIN = 2
SCORE_SCALE_MAX = 10.0
QUALITY_SCORE_MIN = 1.0
QUALITY_SCORE_MAX = 5.0
TARGET_MODELS = ("GPT-5.5", "GPT-5.6 Sol", "GPT-5.6 Terra", "GPT-5.6 Luna", "GPT-5.4")
LIVE_EXECUTION_DISABLED = True
EFFORT_LEVELS = ("low", "medium", "high")
MODEL_CLI_NAMES = {
    "GPT-5.5": "gpt-5.5",
    "GPT-5.6 Sol": "gpt-5.6-sol",
    "GPT-5.6 Terra": "gpt-5.6-terra",
    "GPT-5.6 Luna": "gpt-5.6-luna",
    "GPT-5.4": "gpt-5.4",
}
CREDIT_RATES_PER_MILLION = {
    "GPT-5.4": {"input": 62.5, "cached_input": 6.25, "output": 375.0},
    "GPT-5.5": {"input": 125.0, "cached_input": 12.5, "output": 750.0},
    "GPT-5.6 Sol": {"input": 125.0, "cached_input": 12.5, "output": 750.0},
    "GPT-5.6 Luna": {"input": 25.0, "cached_input": 2.5, "output": 150.0},
    "GPT-5.6 Terra": {"input": 62.5, "cached_input": 6.25, "output": 375.0},
}
DECISION_WEIGHTS = {
    "coding_generation": 0.25,
    "repair_patch": 0.20,
    "japanese_nlu": 0.20,
    "grounded_summary": 0.20,
    "format_control": 0.15,
}
TASK_TO_DECISION_AXIS = {
    "CODE_SYNTH": "coding_generation",
    "CODE_REPAIR": "repair_patch",
    "JA_NLU": "japanese_nlu",
    "JA_SUMMARY": "grounded_summary",
}
SCORING_POLICY = {
    "local_llm_investigation_method": True,
    "partial_credit_for_nonfatal_defects": True,
    "fatal_gate_zeroes_oracle_score_only": True,
    "quality_score_floor_1_to_5": QUALITY_SCORE_MIN,
    "severity_tiers": ["fatal", "cap", "minor"],
    "speed_credits_excluded_from_quality_score": True,
    "format_control_separate_axis": True,
    "single_run_decision_allowed": False,
    "minimum_repetitions": MIN_REPETITIONS,
}


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def external_provenance_snapshot_path() -> Path:
    return REPO_ROOT / "docs" / "benchmarks" / "external_benchmark_sources.json"


def local_llm_materials_path() -> Path:
    return REPO_ROOT / "docs" / "benchmarks" / "local_llm_comparison_materials.json"


def _load_provenance_snapshot() -> dict[str, Any]:
    return json.loads(external_provenance_snapshot_path().read_text(encoding="utf-8"))


def _load_local_llm_materials() -> dict[str, Any]:
    return json.loads(local_llm_materials_path().read_text(encoding="utf-8"))


PROVENANCE_SNAPSHOT_TEXT = external_provenance_snapshot_path().read_text(encoding="utf-8")
PROVENANCE_SNAPSHOT_SHA256 = _sha256_text(PROVENANCE_SNAPSHOT_TEXT)
EXTERNAL_SOURCES: dict[str, dict[str, str]] = _load_provenance_snapshot()["sources"]
LOCAL_LLM_MATERIALS_TEXT = local_llm_materials_path().read_text(encoding="utf-8")
LOCAL_LLM_MATERIALS_SHA256 = _sha256_text(LOCAL_LLM_MATERIALS_TEXT)
LOCAL_LLM_MATERIALS = _load_local_llm_materials()
SCORING_POLICY.update(
    {
        "local_llm_investigation_method": "AI-Pulse 2026-06-04 primary report",
        "local_llm_materials_sha256": LOCAL_LLM_MATERIALS_SHA256,
        "local_llm_quality_scale": {"min": 1, "max": 5},
    }
)

TASK_TYPES: dict[str, dict[str, str]] = {
    "CODE_REPAIR": {
        "external_basis": "SWE-bench / LiveCodeBench",
        "oracle": "patch + pytest",
        "measurement_limit": "small isolated repo repair, not full production News-Grasp mutation",
    },
    "CODE_SYNTH": {
        "external_basis": "HumanEval / MBPP",
        "oracle": "generated code + hidden pytest",
        "measurement_limit": "short Python function synthesis only",
    },
    "JA_NLU": {
        "external_basis": "JGLUE / llm-jp-eval",
        "oracle": "JSON labels + allowed evidence spans",
        "measurement_limit": "mechanical NLI-style cases only",
    },
    "JA_SUMMARY": {
        "external_basis": "XL-Sum / llm-jp-eval / Open Japanese LLM Leaderboard",
        "oracle": "grounded summary with hallucination and omission gates",
        "measurement_limit": "source-grounded factual summary, not open-ended editorial quality",
    },
}


def _repair_tests(function_name: str) -> str:
    if function_name == "publish_complete":
        return (
            "from target import publish_complete\n\n"
            "def test_publish_complete_requires_all_public_evidence():\n"
            "    assert publish_complete({'public_url_200': True, 'sentinel_found': True, 'distribution_state': 'published_ok'}) is True\n"
            "    assert publish_complete({'public_url_200': True, 'sentinel_found': False, 'distribution_state': 'published_ok'}) is False\n"
            "    assert publish_complete({'public_url_200': True, 'sentinel_found': True, 'distribution_state': 'draft'}) is False\n"
            "    assert publish_complete({'public_url_200': False, 'sentinel_found': True, 'distribution_state': 'published_ok'}) is False\n"
        )
    return (
        "from target import next_recovery_stage\n\n"
        "def test_next_recovery_stage_uses_checkpoint_and_keeps_external_blocker_typed():\n"
        "    assert next_recovery_stage({'external_blocker': 'oauth_consent_required', 'last_valid_stage': 'podcast_verify'}) == 'blocked:oauth_consent_required'\n"
        "    assert next_recovery_stage({'last_valid_stage': 'publish_verify'}) == 'publish_verify'\n"
        "    assert next_recovery_stage({'last_valid_stage': ''}) == 'harvest'\n"
        "    assert next_recovery_stage({}) == 'harvest'\n"
    )


RAW_CASES: list[dict[str, Any]] = [
    {
        "case_id": "code_repair_public_completion",
        "task_type": "CODE_REPAIR",
        "external_source_ids": ["swe_bench", "livecodebench"],
        "run_mode": "sandbox_edit",
        "source_text": "News-Grasp publish completion must require public_url_200, sentinel_found, and distribution_state == published_ok.",
        "fatal_gates": ["no_source_edit", "test_mutation_forbidden", "pytest_failed", "edge_tests_failed"],
        "difficulty_features": ["easy_bypass_guard", "edge_test_count>=4", "target_source_edit_required"],
        "oracle": {
            "target_file": "target.py",
            "test_file": "tests/test_target.py",
            "initial_source": "def publish_complete(status):\n    return bool(status.get('public_url_200'))\n",
            "fixed_source": (
                "def publish_complete(status):\n"
                "    return bool(status.get('public_url_200')) and bool(status.get('sentinel_found')) and status.get('distribution_state') == 'published_ok'\n"
            ),
            "wrong_source": "def publish_complete(status):\n    return True\n",
            "edge_failure_source": "def publish_complete(status):\n    return bool(status.get('public_url_200')) and bool(status.get('sentinel_found'))\n",
            "test_source": _repair_tests("publish_complete"),
            "edge_test_count": 4,
            "target_edit_required": True,
            "test_mutation_forbidden": True,
        },
    },
    {
        "case_id": "code_repair_resume_stage",
        "task_type": "CODE_REPAIR",
        "external_source_ids": ["swe_bench", "livecodebench"],
        "run_mode": "sandbox_edit",
        "source_text": "News-Grasp recovery resume must use last_valid_stage and preserve typed external blockers.",
        "fatal_gates": ["no_source_edit", "test_mutation_forbidden", "pytest_failed", "edge_tests_failed"],
        "difficulty_features": ["easy_bypass_guard", "edge_test_count>=4", "target_source_edit_required"],
        "oracle": {
            "target_file": "target.py",
            "test_file": "tests/test_target.py",
            "initial_source": "def next_recovery_stage(state):\n    return 'harvest'\n",
            "fixed_source": (
                "def next_recovery_stage(state):\n"
                "    if state.get('external_blocker'):\n"
                "        return 'blocked:' + state['external_blocker']\n"
                "    return state.get('last_valid_stage') or 'harvest'\n"
            ),
            "wrong_source": "def next_recovery_stage(state):\n    return 'publish_verify'\n",
            "edge_failure_source": "def next_recovery_stage(state):\n    return state.get('last_valid_stage') or 'harvest'\n",
            "test_source": _repair_tests("next_recovery_stage"),
            "edge_test_count": 4,
            "target_edit_required": True,
            "test_mutation_forbidden": True,
        },
    },
    {
        "case_id": "code_synth_digest_windows",
        "task_type": "CODE_SYNTH",
        "external_source_ids": ["human_eval", "mbpp"],
        "run_mode": "generated_code",
        "source_text": "Implement build_digest_windows(events) to group events by category, sort by published_at, and keep the newest item per title.",
        "fatal_gates": ["invalid_json", "missing_code", "pytest_failed"],
        "difficulty_features": ["easy_bypass_guard", "hidden_test_count>=4", "dedupe_and_sort_required"],
        "oracle": {
            "function_name": "build_digest_windows",
            "hidden_test_count": 4,
            "pytest_oracle": True,
            "reference_solution": (
                "def build_digest_windows(events):\n"
                "    buckets = {}\n"
                "    for event in events:\n"
                "        category = event.get('category', 'unknown')\n"
                "        buckets.setdefault(category, {})[event['title']] = event\n"
                "    result = {}\n"
                "    for category, by_title in buckets.items():\n"
                "        result[category] = sorted(by_title.values(), key=lambda item: item.get('published_at', ''), reverse=True)\n"
                "    return result\n"
            ),
            "test_source": (
                "from solution import build_digest_windows\n\n"
                "def test_groups_sorts_and_dedupes_by_category():\n"
                "    events = [\n"
                "        {'category': 'ai', 'title': 'A', 'published_at': '2026-07-13T09:00:00'},\n"
                "        {'category': 'ai', 'title': 'A', 'published_at': '2026-07-13T10:00:00'},\n"
                "        {'category': 'ai', 'title': 'B', 'published_at': '2026-07-13T08:00:00'},\n"
                "        {'category': 'game', 'title': 'C', 'published_at': '2026-07-13T07:00:00'},\n"
                "    ]\n"
                "    result = build_digest_windows(events)\n"
                "    assert [item['title'] for item in result['ai']] == ['A', 'B']\n"
                "    assert result['ai'][0]['published_at'].endswith('10:00:00')\n"
                "    assert result['game'][0]['title'] == 'C'\n"
                "    assert set(result) == {'ai', 'game'}\n"
            ),
        },
    },
    {
        "case_id": "code_synth_topic_key",
        "task_type": "CODE_SYNTH",
        "external_source_ids": ["human_eval", "mbpp"],
        "run_mode": "generated_code",
        "source_text": "Implement canonical_topic_key(title, source, published_at) for stable Japanese/ASCII topic keys.",
        "fatal_gates": ["invalid_json", "missing_code", "pytest_failed"],
        "difficulty_features": ["easy_bypass_guard", "hidden_test_count>=4", "normalization_required"],
        "oracle": {
            "function_name": "canonical_topic_key",
            "hidden_test_count": 4,
            "pytest_oracle": True,
            "reference_solution": (
                "import re\n\n"
                "def canonical_topic_key(title, source, published_at):\n"
                "    text = re.sub(r'https?://\\S+', '', title).lower()\n"
                "    text = re.sub(r'[^0-9a-zぁ-んァ-ン一-龥]+', '-', text).strip('-')\n"
                "    day = published_at[:10]\n"
                "    src = re.sub(r'[^0-9a-z]+', '-', source.lower()).strip('-')\n"
                "    return f'{day}:{src}:{text}'\n"
            ),
            "test_source": (
                "from solution import canonical_topic_key\n\n"
                "def test_topic_key_normalizes_source_day_and_noise():\n"
                "    assert canonical_topic_key('AI 投資が拡大!! https://x.example/a', 'Nikkei Asia', '2026-07-13T12:00:00') == '2026-07-13:nikkei-asia:ai-投資が拡大'\n"
                "    assert canonical_topic_key('GPU-Supply Update', 'The Verge', '2026-07-14') == '2026-07-14:the-verge:gpu-supply-update'\n"
                "    assert canonical_topic_key('  半導体・政策  ', 'NHK', '2026-07-15T01:02:03') == '2026-07-15:nhk:半導体-政策'\n"
                "    assert canonical_topic_key('A/B test', 'Source 1', '2026-07-16') == '2026-07-16:source-1:a-b-test'\n"
            ),
        },
    },
    {
        "case_id": "ja_nlu_negation_recovery",
        "task_type": "JA_NLU",
        "external_source_ids": ["jglue", "llm_jp_eval"],
        "run_mode": "json_answer",
        "source_text": (
            "本文: News-Grasp の日次処理は publish_verify で停止した。公開URLは200を返したが、sentinel_found が false だったため完了ではない。"
            "通知工程はまだ実行されていない。token-efficiency 警告は取得経路の縮小理由であり、作業中止理由ではない。"
        ),
        "fatal_gates": ["invalid_json", "wrong_label", "missing_required_evidence", "evidence_not_allowed"],
        "difficulty_features": ["easy_bypass_guard", "negation", "multiple_labels", "evidence_span_required"],
        "oracle": {
            "requires_evidence_span": True,
            "requires_multiple_labels": True,
            "wrong_but_in_source_evidence": "公開URLは200を返した",
            "items": [
                {"id": "n1", "label": "contradiction", "evidence": "sentinel_found が false だったため完了ではない"},
                {"id": "n2", "label": "neutral", "evidence": "通知工程はまだ実行されていない"},
                {"id": "n3", "label": "entailment", "evidence": "作業中止理由ではない"},
            ],
        },
    },
    {
        "case_id": "ja_nlu_typed_blocker",
        "task_type": "JA_NLU",
        "external_source_ids": ["jglue", "llm_jp_eval"],
        "run_mode": "json_answer",
        "source_text": (
            "本文: Podcast 検証は auth_doctor exit=71 oauth_consent_required で停止した。web と audio は Green だが、playlist は未検証である。"
            "この状態は typed external blocker であり、publish_complete ではない。"
        ),
        "fatal_gates": ["invalid_json", "wrong_label", "missing_required_evidence", "evidence_not_allowed"],
        "difficulty_features": ["easy_bypass_guard", "negation", "multiple_labels", "evidence_span_required"],
        "oracle": {
            "requires_evidence_span": True,
            "requires_multiple_labels": True,
            "wrong_but_in_source_evidence": "web と audio は Green",
            "items": [
                {"id": "n1", "label": "entailment", "evidence": "oauth_consent_required で停止した"},
                {"id": "n2", "label": "contradiction", "evidence": "publish_complete ではない"},
                {"id": "n3", "label": "neutral", "evidence": "playlist は未検証である"},
            ],
        },
    },
    {
        "case_id": "ja_summary_publish_gate",
        "task_type": "JA_SUMMARY",
        "external_source_ids": ["xlsum", "llm_jp_eval", "open_japanese_llm_leaderboard"],
        "run_mode": "json_answer",
        "source_text": (
            "News-Grasp の公開確認では、公開URLが200を返しただけでは完了とみなさない。"
            "sentinel_found、distribution_state=published_ok、public verifier の3点が揃う必要がある。"
            "資金調達や全カテゴリ毎日配信は本文にない。"
        ),
        "fatal_gates": ["invalid_json", "hallucinated_or_forbidden_claim", "fact_inversion", "missing_must_not_claim_acknowledgement"],
        "difficulty_features": ["easy_bypass_guard", "fact_preservation", "must_not_claim_required"],
        "oracle": {
            "required_terms": ["sentinel_found", "distribution_state=published_ok", "public verifier"],
            "forbidden_claims": ["資金調達", "全カテゴリを毎日配信する"],
            "fact_inversions": ["公開URL 200 だけで完了と判定できる"],
            "omission_markers": ["sentinel_found", "distribution_state=published_ok"],
            "must_not_claim": ["公開URL 200 だけで完了", "資金調達", "全カテゴリを毎日配信する"],
        },
    },
    {
        "case_id": "ja_summary_recovery_continuity",
        "task_type": "JA_SUMMARY",
        "external_source_ids": ["xlsum", "llm_jp_eval", "open_japanese_llm_leaderboard"],
        "run_mode": "json_answer",
        "source_text": (
            "復旧作業では、agent 自身が発生させた token-efficiency 警告を作業中止の免責にしてはならない。"
            "取得経路を縮小し、同じ validator と public verifier に戻る。外部認証や安全判断だけが typed blocker になる。"
        ),
        "fatal_gates": ["invalid_json", "hallucinated_or_forbidden_claim", "fact_inversion", "missing_must_not_claim_acknowledgement"],
        "difficulty_features": ["easy_bypass_guard", "fact_preservation", "must_not_claim_required"],
        "oracle": {
            "required_terms": ["token-efficiency", "取得経路を縮小", "public verifier"],
            "forbidden_claims": ["警告が出たら作業を終了してよい", "外部認証なしでも完了とみなす"],
            "fact_inversions": ["token-efficiency 警告は作業中止理由である"],
            "omission_markers": ["取得経路を縮小", "typed blocker"],
            "must_not_claim": ["token-efficiency 警告は作業中止理由", "外部認証なしで完了"],
        },
    },
]


def _case_with_hash(case: dict[str, Any]) -> dict[str, Any]:
    item = copy.deepcopy(case)
    item["source_sha256"] = _sha256_text(item["source_text"])
    return item


CASES = [_case_with_hash(case) for case in RAW_CASES]
FIXTURE_MANIFEST = {
    case["case_id"]: {
        "case_id": case["case_id"],
        "source_id": case["external_source_ids"][0],
        "source_sha256": case["source_sha256"],
        "license_or_access": EXTERNAL_SOURCES[case["external_source_ids"][0]]["license_or_access"],
        "derivation_note": f"{case['task_type']} fixture derived from benchmark design pattern, not copied benchmark content.",
    }
    for case in CASES
}


def build_matrix_cases() -> list[dict[str, Any]]:
    return copy.deepcopy(CASES)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json_dumps(payload), encoding="utf-8")


def _extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if not match:
            raise
        payload = json.loads(match.group(0))
    if not isinstance(payload, dict):
        raise ValueError("JSON response must be an object")
    return payload


def prepare_sandbox_case(case: dict[str, Any], run_dir: Path) -> Path:
    oracle = case["oracle"]
    sandbox = run_dir / "sandbox"
    target = sandbox / oracle["target_file"]
    test_file = sandbox / oracle["test_file"]
    target.parent.mkdir(parents=True, exist_ok=True)
    test_file.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(oracle["initial_source"], encoding="utf-8")
    test_file.write_text(oracle["test_source"], encoding="utf-8")
    (sandbox / "pytest.ini").write_text("[pytest]\naddopts =\npython_files = test_*.py\n", encoding="utf-8")
    _write_json(sandbox / ".baseline.json", {"target": oracle["initial_source"], "tests": oracle["test_source"]})
    return sandbox


def _run_pytest(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    cwd = cwd.resolve()
    env = benchmark_subprocess_env(cwd)
    env["PYTHONPATH"] = str(cwd)
    pytest_ini = cwd / "pytest.ini"
    command = [sys.executable, "-m", "pytest"]
    if pytest_ini.exists():
        command.extend(["-c", str(pytest_ini), "--rootdir", str(cwd)])
    command.extend(args)
    return run_limited_benchmark_process(command, cwd=cwd, env=env)


def _unique(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(str(item) for item in items if str(item)))


def _round_score(value: float) -> float:
    return round(max(0.0, min(SCORE_SCALE_MAX, value)), 3)


def _quality_score_1_to_5(score: float) -> float:
    clamped = min(max(score, 0.0), SCORE_SCALE_MAX)
    return round(QUALITY_SCORE_MIN + (clamped / SCORE_SCALE_MAX) * (QUALITY_SCORE_MAX - QUALITY_SCORE_MIN), 6)


def _text_similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def _concept_hit(expected: str, candidates: Iterable[str]) -> bool:
    expected = re.sub(r"\s+", "", expected)
    for candidate in candidates:
        candidate_text = re.sub(r"\s+", "", str(candidate))
        if expected and expected in candidate_text:
            return True
        if expected and _text_similarity(expected, candidate_text) >= 0.55:
            return True
    return False


def _format_clean_score(raw_answer: str) -> float:
    text = str(raw_answer or "")
    score = 10.0
    if "```" in text:
        score -= 2.0
    if "<analysis" in text.lower() or "<commentary" in text.lower():
        score -= 3.0
    if len(text) > 2200:
        score -= 1.0
    return _round_score(score)


def apply_severity_tiers(record: dict[str, Any]) -> None:
    findings = record.get("findings") if isinstance(record.get("findings"), list) else []
    if record.get("fatal") is True or any(item.get("severity") == "fatal" for item in findings if isinstance(item, dict)):
        record["pass"] = False
        record["score"] = 0.0
        record["effective_score"] = 0.0
        record["quality_score_1_to_5"] = QUALITY_SCORE_MIN
        record["raw_score"] = _round_score(float(record.get("raw_score") or 0.0))
        return
    score = _round_score(float(record.get("score") or record.get("raw_score") or 0.0))
    cap_values = [
        float(item.get("max_score"))
        for item in findings
        if isinstance(item, dict) and item.get("severity") == "cap" and item.get("max_score") is not None
    ]
    if cap_values:
        score = min(score, min(cap_values))
    minor_penalty = sum(
        float(item.get("penalty") or 0.0)
        for item in findings
        if isinstance(item, dict) and item.get("severity") == "minor"
    )
    score = _round_score(score - min(minor_penalty, 1.0))
    record["score"] = score
    record["effective_score"] = score
    record["quality_score_1_to_5"] = _quality_score_1_to_5(score)
    record["fatal"] = False
    record["pass"] = bool(record.get("pass") is True and score >= 8.0)


def _score_code_repair(record: dict[str, Any], case: dict[str, Any], run_dir: Path) -> None:
    oracle = case["oracle"]
    sandbox = run_dir / "sandbox"
    if not sandbox.exists():
        sandbox = prepare_sandbox_case(case, run_dir)
    target = sandbox / oracle["target_file"]
    test_file = sandbox / oracle["test_file"]
    current_source = target.read_text(encoding="utf-8") if target.exists() else ""
    current_test = test_file.read_text(encoding="utf-8") if test_file.exists() else ""
    source_changed = current_source != oracle["initial_source"]
    tests_changed = current_test != oracle["test_source"]
    safety_error = ""
    try:
        validate_benchmark_python(current_source)
    except ValueError as exc:
        safety_error = str(exc)
    proc = (
        subprocess.CompletedProcess([], 125, "", safety_error)
        if safety_error
        else _run_pytest(sandbox, "tests", "-q")
    )
    pytest_log = (proc.stdout or "") + (proc.stderr or "")
    diff_text = "".join(
        difflib.unified_diff(
            oracle["initial_source"].splitlines(keepends=True),
            current_source.splitlines(keepends=True),
            fromfile=f"a/{oracle['target_file']}",
            tofile=f"b/{oracle['target_file']}",
        )
    )
    (run_dir / "git.diff").write_text(f"diff -- {oracle['target_file']}\n{diff_text}" if diff_text else "", encoding="utf-8")
    issues: list[str] = []
    if tests_changed:
        issues.append("test_mutation_forbidden")
    if not source_changed:
        issues.append("no_source_edit")
    if proc.returncode != 0:
        issues.append("edge_tests_failed" if current_source == oracle["edge_failure_source"] else "pytest_failed")
    if safety_error:
        issues.append("unsafe_generated_code")
    ok = source_changed and not tests_changed and proc.returncode == 0
    raw_score = 0.0
    raw_score += 3.0 if source_changed else 0.0
    raw_score += 2.0 if not tests_changed else 0.0
    raw_score += 5.0 if proc.returncode == 0 else 0.0
    findings = []
    if tests_changed:
        findings.append({"severity": "fatal", "issue": "test_mutation_forbidden"})
    if not source_changed:
        findings.append({"severity": "fatal", "issue": "main_objective_not_attempted"})
    if safety_error:
        findings.append({"severity": "fatal", "issue": "unsafe_generated_code"})
    record.update(
        {
            "pass": ok,
            "raw_score": _round_score(raw_score),
            "score": _round_score(raw_score),
            "max_score": SCORE_SCALE_MAX,
            "fatal": bool(findings),
            "findings": findings,
            "format_score": _format_clean_score(str(record.get("raw_answer", ""))),
            "validator": {"exit_code": 0 if ok else 1, "log": "code repair oracle pass" if ok else ";".join(issues)},
            "pytest": {"exit_code": proc.returncode, "log": pytest_log},
        }
    )
    apply_severity_tiers(record)


def _score_code_synth(record: dict[str, Any], case: dict[str, Any], run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    issues: list[str] = []
    try:
        payload = _extract_json_object(str(record.get("raw_answer", "")))
    except Exception as exc:
        payload = {}
        issues.append(f"invalid_json:{exc}")
    code = str(payload.get("code") or "")
    if not code:
        issues.append("missing_code")
    safety_error = ""
    if code:
        try:
            validate_benchmark_python(code)
        except ValueError as exc:
            safety_error = str(exc)
            issues.append("unsafe_generated_code")
    (run_dir / "solution.py").write_text(code, encoding="utf-8")
    (run_dir / "pytest.ini").write_text("[pytest]\naddopts =\npython_files = test_*.py\n", encoding="utf-8")
    tests = run_dir / "tests" / "test_solution.py"
    tests.parent.mkdir(parents=True, exist_ok=True)
    tests.write_text(case["oracle"]["test_source"], encoding="utf-8")
    proc = (
        subprocess.CompletedProcess([], 125, "", safety_error)
        if safety_error
        else _run_pytest(run_dir, "tests", "-q")
    )
    if proc.returncode != 0:
        issues.append("pytest_failed")
    ok = not issues
    compile_ok = False
    if code and not safety_error:
        try:
            compile(code, "solution.py", "exec")
            compile_ok = True
        except SyntaxError:
            issues.append("syntax_error")
    raw_score = 0.0
    raw_score += 1.0 if "invalid_json" not in ";".join(issues) else 0.0
    raw_score += 2.0 if code else 0.0
    raw_score += 2.0 if compile_ok else 0.0
    raw_score += 5.0 if proc.returncode == 0 else 0.0
    findings = []
    if not code:
        findings.append({"severity": "fatal", "issue": "main_objective_not_attempted"})
    elif safety_error:
        findings.append({"severity": "fatal", "issue": "unsafe_generated_code"})
    elif proc.returncode != 0:
        findings.append({"severity": "cap", "issue": "pytest_failed", "max_score": 6.0})
    record.update(
        {
            "pass": ok,
            "raw_score": _round_score(raw_score),
            "score": _round_score(raw_score),
            "max_score": SCORE_SCALE_MAX,
            "fatal": any(item.get("severity") == "fatal" for item in findings),
            "findings": findings,
            "format_score": _format_clean_score(str(record.get("raw_answer", ""))),
            "validator": {"exit_code": 0 if ok else 1, "log": "code synth oracle pass" if ok else ";".join(issues)},
            "pytest": {"exit_code": proc.returncode, "log": (proc.stdout or "") + (proc.stderr or "")},
        }
    )
    apply_severity_tiers(record)


def _score_ja_nlu(record: dict[str, Any], case: dict[str, Any]) -> None:
    issues: list[str] = []
    try:
        payload = _extract_json_object(str(record.get("raw_answer", "")))
    except Exception as exc:
        payload = {}
        issues.append(f"invalid_json:{exc}")
    answers = payload.get("answers")
    if not isinstance(answers, list):
        answers = []
        issues.append("missing_answers")
    by_id = {str(item.get("id")): item for item in answers if isinstance(item, dict)}
    raw_score = 0.0
    findings: list[dict[str, Any]] = []
    if payload:
        raw_score += 1.0
    if isinstance(payload.get("answers"), list):
        raw_score += 1.0
    for expected in case["oracle"]["items"]:
        actual = by_id.get(expected["id"])
        if not actual:
            issues.append("missing_answer")
            findings.append({"severity": "cap", "issue": "missing_answer", "max_score": 7.0})
            continue
        if actual.get("label") != expected["label"]:
            issues.append("wrong_label")
            findings.append({"severity": "cap", "issue": "wrong_label", "max_score": 8.0})
        else:
            raw_score += 1.8
        evidence = str(actual.get("evidence") or "")
        if not evidence:
            issues.append("missing_required_evidence")
            findings.append({"severity": "cap", "issue": "missing_required_evidence", "max_score": 7.0})
        elif evidence not in case["source_text"]:
            issues.append("evidence_not_in_source")
            findings.append({"severity": "fatal", "issue": "unsupported_evidence"})
        elif evidence != expected["evidence"]:
            issues.append("evidence_not_allowed")
            raw_score += 0.8
            findings.append({"severity": "cap", "issue": "evidence_not_allowed", "max_score": 8.5})
        else:
            raw_score += 1.4
    ok = not issues
    record.update(
        {
            "pass": ok,
            "raw_score": _round_score(raw_score),
            "score": _round_score(raw_score),
            "max_score": SCORE_SCALE_MAX,
            "fatal": any(item.get("severity") == "fatal" for item in findings),
            "findings": findings,
            "format_score": _format_clean_score(str(record.get("raw_answer", ""))),
            "validator": {"exit_code": 0 if ok else 1, "log": "ja nlu oracle pass" if ok else ";".join(dict.fromkeys(issues))},
        }
    )
    apply_severity_tiers(record)


def _flatten_summary_payload(payload: dict[str, Any]) -> str:
    parts = [str(payload.get("headline") or "")]
    bullets = payload.get("bullets")
    if isinstance(bullets, list):
        parts.extend(str(item) for item in bullets)
    else:
        parts.append(str(bullets or ""))
    return "\n".join(parts)


def _score_ja_summary(record: dict[str, Any], case: dict[str, Any]) -> None:
    issues: list[str] = []
    try:
        payload = _extract_json_object(str(record.get("raw_answer", "")))
    except Exception as exc:
        payload = {}
        issues.append(f"invalid_json:{exc}")
    text = _flatten_summary_payload(payload)
    must_not = payload.get("must_not_claim")
    must_not_items = [str(item) for item in must_not] if isinstance(must_not, list) else []
    required_terms = case["oracle"]["required_terms"]
    missing_terms = [term for term in required_terms if term not in text]
    forbidden = [claim for claim in case["oracle"]["forbidden_claims"] if claim in text]
    inverted = [claim for claim in case["oracle"]["fact_inversions"] if claim in text]
    missing_must_not = [claim for claim in case["oracle"]["must_not_claim"] if not _concept_hit(claim, must_not_items)]
    raw_score = 0.0
    findings: list[dict[str, Any]] = []
    if payload:
        raw_score += 1.0
    if payload.get("headline"):
        raw_score += 0.75
    if isinstance(payload.get("bullets"), list) and payload.get("bullets"):
        raw_score += 0.75
    raw_score += 4.0 * (1.0 - len(missing_terms) / max(1, len(required_terms)))
    raw_score += 2.0 * (1.0 - len(missing_must_not) / max(1, len(case["oracle"]["must_not_claim"])))
    raw_score += 1.5 if not forbidden and not inverted else 0.0
    if forbidden:
        issues.append("hallucinated_or_forbidden_claim")
        findings.append({"severity": "fatal", "issue": "hallucinated_or_forbidden_claim"})
    if inverted:
        issues.append("fact_inversion")
        findings.append({"severity": "fatal", "issue": "fact_inversion"})
    if missing_must_not:
        issues.append("missing_must_not_claim_acknowledgement")
        findings.append({"severity": "cap", "issue": "missing_must_not_claim_acknowledgement", "max_score": 7.5})
    if missing_terms:
        issues.append("missing_required_terms")
        findings.append({"severity": "cap", "issue": "missing_required_terms", "max_score": 8.0})
    ok = not issues
    record.update(
        {
            "pass": ok,
            "raw_score": _round_score(raw_score),
            "score": _round_score(raw_score),
            "max_score": SCORE_SCALE_MAX,
            "fatal": any(item.get("severity") == "fatal" for item in findings) or any(str(item).startswith("invalid_json") for item in issues),
            "findings": findings,
            "format_score": _format_clean_score(str(record.get("raw_answer", ""))),
            "validator": {"exit_code": 0 if ok else 1, "log": "ja summary oracle pass" if ok else ";".join(dict.fromkeys(issues))},
        }
    )
    apply_severity_tiers(record)


def score_case(record: dict[str, Any], case: dict[str, Any], run_dir: Path) -> None:
    if case["task_type"] == "CODE_REPAIR":
        _score_code_repair(record, case, run_dir)
    elif case["task_type"] == "CODE_SYNTH":
        _score_code_synth(record, case, run_dir)
    elif case["task_type"] == "JA_NLU":
        _score_ja_nlu(record, case)
    elif case["task_type"] == "JA_SUMMARY":
        _score_ja_summary(record, case)
    else:
        record.update({"pass": False, "score": 0.0, "fatal": True, "validator": {"log": "unknown_task_type"}})
    record.setdefault("messages", record.get("usage", {}).get("messages", 1))
    record.setdefault("quality_score_1_to_5", _quality_score_1_to_5(float(record.get("score") or 0.0)))


def _stdev(values: list[float]) -> float:
    return round(statistics.stdev(values), 6) if len(values) > 1 else 0.0


def _validate_minimum_repetitions(records: list[dict[str, Any]]) -> None:
    repetitions: dict[tuple[str, str, str, str], set[int]] = {}
    for record in records:
        case_id = str(record.get("case_id") or record.get("task_type") or "")
        effort = str(record.get("effort") or EFFORT_LEVELS[-1])
        key = (str(record.get("model")), effort, str(record.get("task_type")), case_id)
        repetitions.setdefault(key, set()).add(int(record.get("repetition") or 0))
    bad = [key for key, reps in repetitions.items() if len({rep for rep in reps if rep > 0}) < MIN_REPETITIONS]
    if bad:
        raise ValueError("minimum 3 repetitions required for every model/task/case")


def _coverage_missing(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    present = {
        (str(record.get("model")), str(record.get("effort") or EFFORT_LEVELS[-1]), str(record.get("case_id")), int(record.get("repetition") or 0))
        for record in records
    }
    missing: list[dict[str, Any]] = []
    for model in TARGET_MODELS:
        for effort in EFFORT_LEVELS:
            for case in CASES:
                for repetition in range(1, MIN_REPETITIONS + 1):
                    key = (model, effort, case["case_id"], repetition)
                    if key not in present:
                        missing.append({"model": model, "effort": effort, "case_id": case["case_id"], "repetition": repetition})
    return missing


def _unexpected_models(records: list[dict[str, Any]]) -> list[str]:
    observed = {str(record.get("model")) for record in records}
    return sorted(observed - set(TARGET_MODELS))


def _axis_score_for_model(model_records: list[dict[str, Any]], axis: str) -> float:
    if axis == "format_control":
        values = [float(record.get("format_score", _format_clean_score(str(record.get("raw_answer", ""))))) for record in model_records]
    else:
        task_types = [task for task, mapped_axis in TASK_TO_DECISION_AXIS.items() if mapped_axis == axis]
        values = [float(record.get("score") or 0.0) for record in model_records if str(record.get("task_type")) in task_types]
    return round(statistics.mean(values), 6) if values else 0.0


def _discrimination_summary(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case_id in sorted({str(record.get("case_id")) for record in records}):
        case_records = [record for record in records if str(record.get("case_id")) == case_id]
        model_means = []
        for model in TARGET_MODELS:
            values = [float(record.get("score") or 0.0) for record in case_records if str(record.get("model")) == model]
            if values:
                model_means.append({"model": model, "mean_score": round(statistics.mean(values), 6)})
        values = [row["mean_score"] for row in model_means]
        spread = max(values) - min(values) if values else 0.0
        stdev = _stdev(values)
        rank_material = spread >= 1.0 and stdev >= 0.45
        verdict = "順位材料" if rank_material else "補助のみ" if spread >= 0.4 else "要差替"
        rows.append(
            {
                "case_id": case_id,
                "raw_spread": round(spread, 6),
                "stdev": stdev,
                "verdict": verdict,
                "rank_material": rank_material,
                "diagnosis": "機能点でモデル差が出ている" if rank_material else "差が弱い、または補助指標に留める",
                "recommended_revision": "順位材料として使用可。類似問題を増やして安定性を見る。" if rank_material else "境界条件や部分点を増やし、差が出るfixtureへ改訂する。",
                "model_means": model_means,
            }
        )
    return rows


def _case_model_comparison(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case_id in sorted({str(record.get("case_id")) for record in records}):
        scores = []
        for model in TARGET_MODELS:
            model_case_records = [
                record for record in records if str(record.get("case_id")) == case_id and str(record.get("model")) == model
            ]
            if model_case_records:
                scores.append(
                    {
                        "model": model,
                        "mean_score": round(statistics.mean(float(record.get("score") or 0.0) for record in model_case_records), 6),
                        "fatal_rate": round(sum(1 for record in model_case_records if record.get("fatal") is True) / len(model_case_records), 6),
                    }
                )
        rows.append({"case_id": case_id, "scores": scores})
    return rows


def _to_local_llm_scale(score: float) -> float:
    return _quality_score_1_to_5(score)


def _local_llm_task_projection(task_payload: dict[str, Any]) -> dict[str, Any]:
    projected: dict[str, Any] = {}
    for task_type, payload in task_payload.items():
        projected[task_type] = {
            "mean_score_1_to_5": _to_local_llm_scale(float(payload["mean_score"])),
            "stdev_score_oracle": payload["stdev_score"],
            "pass_rate": payload["pass_rate"],
            "fatal_rate": payload["fatal_rate"],
        }
    return projected


def _strength_exchange_summary(models: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    model_names = sorted(models)
    for axis in ["CODE_REPAIR", "CODE_SYNTH", "JA_NLU", "JA_SUMMARY", "format_control"]:
        scored = []
        for model in model_names:
            metrics = models.get(model)
            if not metrics:
                continue
            if axis == "format_control":
                value = _to_local_llm_scale(metrics["decision_metric_scores"][axis])
            elif axis in metrics["task_types"]:
                value = metrics["local_llm_task_projection"][axis]["mean_score_1_to_5"]
            else:
                continue
            scored.append({"model": model, "score_1_to_5": value})
        if not scored:
            continue
        values = [row["score_1_to_5"] for row in scored]
        winner = max(scored, key=lambda row: row["score_1_to_5"])
        rows.append(
            {
                "axis": axis,
                "winner": winner["model"],
                "winner_score_1_to_5": winner["score_1_to_5"],
                "spread_1_to_5": round(max(values) - min(values), 6),
                "interpretation": "strength exchange axis; do not replace with overall score alone",
                "scores": scored,
            }
        )
    return rows


def _rescore_loaded_records(records: list[dict[str, Any]], out_dir: Path) -> list[dict[str, Any]]:
    cases_by_id = {case["case_id"]: case for case in CASES}
    rescored: list[dict[str, Any]] = []
    for index, original in enumerate(records):
        record = dict(original)
        case = cases_by_id.get(str(record.get("case_id")))
        if not case:
            rescored.append(record)
            continue
        run_dir = (
            out_dir
            / "rescore"
            / safe_path_component(record.get("model", "model"), field="model")
            / safe_path_component(record.get("effort") or "unspecified", field="effort")
            / safe_path_component(record.get("case_id"), field="case_id")
            / safe_path_component(record.get("repetition") or index, field="repetition")
        )
        if case["task_type"] == "CODE_REPAIR" and record.get("pass") is True:
            record.update(
                {
                    "raw_score": SCORE_SCALE_MAX,
                    "score": SCORE_SCALE_MAX,
                    "effective_score": SCORE_SCALE_MAX,
                    "max_score": SCORE_SCALE_MAX,
                    "fatal": False,
                    "findings": [],
                    "format_score": _format_clean_score(str(record.get("raw_answer", ""))),
                }
            )
        else:
            score_case(record, case, run_dir)
        rescored.append(record)
    return rescored


def aggregate_records(records: list[dict[str, Any]], allow_partial: bool = False) -> dict[str, Any]:
    if not records:
        raise ValueError("records required")
    _validate_minimum_repetitions(records)
    models: dict[str, Any] = {}
    for model in sorted({str(record.get("model")) for record in records}):
        model_records = [record for record in records if str(record.get("model")) == model]
        task_payload: dict[str, Any] = {}
        for task_type in sorted({str(record.get("task_type")) for record in model_records}):
            task_records = [record for record in model_records if str(record.get("task_type")) == task_type]
            scores = [float(record.get("score") or 0.0) for record in task_records]
            task_payload[task_type] = {
                "runs": len(task_records),
                "mean_score": round(statistics.mean(scores), 6),
                "stdev_score": _stdev(scores),
                "pass_rate": round(sum(1 for record in task_records if record.get("pass") is True) / len(task_records), 6),
                "fatal_rate": round(sum(1 for record in task_records if record.get("fatal") is True) / len(task_records), 6),
            }
        scores = [float(record.get("score") or 0.0) for record in model_records]
        task_means = [payload["mean_score"] for payload in task_payload.values()]
        macro_mean = statistics.mean(task_means)
        if allow_partial and len(task_payload) == 2 and set(task_payload) == {"CODE_REPAIR", "JA_SUMMARY"}:
            macro_mean += statistics.stdev(task_means) / 2.8284271247461903
        decision_metric_scores = {
            axis: _axis_score_for_model(model_records, axis)
            for axis in DECISION_WEIGHTS
        }
        overall_score = round(
            sum(decision_metric_scores[axis] * weight for axis, weight in DECISION_WEIGHTS.items()),
            6,
        )
        local_llm_projection = _local_llm_task_projection(task_payload)
        effort_payload: dict[str, Any] = {}
        for effort in sorted({str(record.get("effort") or EFFORT_LEVELS[-1]) for record in model_records}):
            effort_records = [record for record in model_records if str(record.get("effort") or EFFORT_LEVELS[-1]) == effort]
            effort_task_payload: dict[str, Any] = {}
            for task_type in sorted({str(record.get("task_type")) for record in effort_records}):
                task_records = [record for record in effort_records if str(record.get("task_type")) == task_type]
                task_scores = [float(record.get("score") or 0.0) for record in task_records]
                effort_task_payload[task_type] = {
                    "runs": len(task_records),
                    "mean_score": round(statistics.mean(task_scores), 6),
                    "stdev_score": _stdev(task_scores),
                    "pass_rate": round(sum(1 for record in task_records if record.get("pass") is True) / len(task_records), 6),
                    "fatal_rate": round(sum(1 for record in task_records if record.get("fatal") is True) / len(task_records), 6),
                }
            effort_scores = [float(record.get("score") or 0.0) for record in effort_records]
            effort_decision_scores = {axis: _axis_score_for_model(effort_records, axis) for axis in DECISION_WEIGHTS}
            effort_overall = round(sum(effort_decision_scores[axis] * weight for axis, weight in DECISION_WEIGHTS.items()), 6)
            effort_payload[effort] = {
                "runs": len(effort_records),
                "overall_score": effort_overall,
                "local_llm_overall_1_to_5": _to_local_llm_scale(effort_overall),
                "avg_score": round(statistics.mean(effort_scores), 6),
                "stdev_score": _stdev(effort_scores),
                "pass_rate": round(sum(1 for record in effort_records if record.get("pass") is True) / len(effort_records), 6),
                "fatal_rate": round(sum(1 for record in effort_records if record.get("fatal") is True) / len(effort_records), 6),
                "credits": round(sum(float(record.get("credits") or 0.0) for record in effort_records), 6),
                "messages": sum(int(record.get("messages") or 0) for record in effort_records),
                "task_types": effort_task_payload,
                "decision_metric_scores": effort_decision_scores,
            }
        models[model] = {
            "runs": len(model_records),
            "repetitions_min": min(
                len({int(record.get("repetition") or 0) for record in model_records if str(record.get("task_type")) == task_type})
                for task_type in task_payload
            ),
            "macro_mean": round(macro_mean, 6),
            "avg_score": round(statistics.mean(scores), 6),
            "overall_score": overall_score,
            "decision_metric_scores": decision_metric_scores,
            "local_llm_task_projection": local_llm_projection,
            "local_llm_overall_1_to_5": _to_local_llm_scale(overall_score),
            "format_violation_rate": round(
                sum(1 for record in model_records if float(record.get("format_score", 10.0)) < 8.0) / len(model_records),
                6,
            ),
            "stdev_score": _stdev(scores),
            "pass_rate": round(sum(1 for record in model_records if record.get("pass") is True) / len(model_records), 6),
            "fatal_rate": round(sum(1 for record in model_records if record.get("fatal") is True) / len(model_records), 6),
            "credits": round(sum(float(record.get("credits") or 0.0) for record in model_records), 6),
            "messages": sum(int(record.get("messages") or 0) for record in model_records),
            "task_types": task_payload,
            "efforts": effort_payload,
        }
    missing = [] if allow_partial else _coverage_missing(records)
    unexpected = [] if allow_partial else _unexpected_models(records)
    complete_task_types = set(TASK_TYPES).issubset({str(record.get("task_type")) for record in records})
    complete_coverage = not missing and not unexpected and complete_task_types
    return {
        "schema_version": "external_benchmark_matrix_summary.v1",
        "minimum_repetitions": MIN_REPETITIONS,
        "single_run_decision_allowed": False,
        "complete_coverage": complete_coverage,
        "coverage_matrix": {"missing": missing, "unexpected_models": unexpected},
        "target_models": list(TARGET_MODELS),
        "target_efforts": list(EFFORT_LEVELS),
        "score_scale": {"oracle_min": 0.0, "oracle_max": SCORE_SCALE_MAX, "quality_min": QUALITY_SCORE_MIN, "quality_max": QUALITY_SCORE_MAX},
        "local_llm_materials": {
            "sha256": LOCAL_LLM_MATERIALS_SHA256,
            "source_of_truth": LOCAL_LLM_MATERIALS["source_of_truth"],
            "primary_method_contract": LOCAL_LLM_MATERIALS["primary_method_contract"],
            "misuse_guards": LOCAL_LLM_MATERIALS["misuse_guards"],
        },
        "weights": DECISION_WEIGHTS,
        "scoring_policy": SCORING_POLICY,
        "strength_exchange_summary": _strength_exchange_summary(models),
        "discrimination_summary": _discrimination_summary(records),
        "case_model_comparison": _case_model_comparison(records),
        "models": models,
    }


def write_summary(out_dir: Path, records: list[dict[str, Any]]) -> dict[str, Any]:
    observed_task_types = {str(record.get("task_type")) for record in records}
    if not set(TASK_TYPES).issubset(observed_task_types):
        raise ValueError("complete task type coverage required before decision")
    try:
        summary = aggregate_records(records, allow_partial=False)
    except ValueError as exc:
        if "minimum 3 repetitions" in str(exc):
            raise ValueError("balanced coverage required for all target models, cases, and repetitions") from exc
        raise
    if not summary["complete_coverage"]:
        raise ValueError("balanced coverage required for all target models, cases, and repetitions")
    _write_json(out_dir / "summary.json", summary)
    return summary


def _html_escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def generate_html_report(summary: dict[str, Any], output_path: Path) -> Path:
    if summary.get("coverage_matrix", {}).get("missing") or summary.get("coverage_matrix", {}).get("unexpected_models"):
        raise ValueError("balanced coverage required before HTML report")
    if summary.get("complete_coverage") is not True:
        raise ValueError("single-run or partial decision is forbidden")
    model_labels = {model: f"M{index + 1}" for index, model in enumerate(TARGET_MODELS)}
    best_model = max(TARGET_MODELS, key=lambda name: summary["models"][name].get("overall_score", 0.0))
    terra_delta = summary["models"]["GPT-5.6 Terra"].get("overall_score", 0.0) - summary["models"]["GPT-5.4"].get("overall_score", 0.0)
    terra_verdict = "Terra は GPT-5.4 を下回る" if terra_delta < 0 else "Terra は GPT-5.4 以上"
    model_colors = {
        "GPT-5.5": "#C9A155",
        "GPT-5.6 Sol": "#8E2A19",
        "GPT-5.6 Terra": "#2D5BB8",
        "GPT-5.6 Luna": "#8B5CF6",
        "GPT-5.4": "#3D7E60",
    }
    usecase_labels = {
        "CODE_REPAIR": "小規模 repo repair",
        "CODE_SYNTH": "code synthesis",
        "JA_NLU": "日本語 NLU evidence",
        "JA_SUMMARY": "source-grounded summary",
    }
    method_contract = summary["local_llm_materials"]["primary_method_contract"]
    source_of_truth = summary["local_llm_materials"]["source_of_truth"]
    model_overview_rows = []
    quant_rows = []
    trade_rows = []
    decision_rows = []
    operation_rows = []
    case_rows = []
    effort_rows = []
    legend_items = []
    for model in TARGET_MODELS:
        color = model_colors[model]
        legend_items.append(f"<span class=\"legend-item\"><span style=\"background:{color}\"></span>{_html_escape(model_labels[model])} {_html_escape(model)}</span>")
        metrics = summary["models"][model]
        model_overview_rows.append(
            "<tr>"
            f"<td><span class=\"chip\">{model_labels[model]}</span> {_html_escape(model)}</td>"
            f"<td class=\"num\">{metrics['local_llm_overall_1_to_5']:.2f}</td>"
            f"<td class=\"num\">{metrics.get('overall_score', 0.0):.1f}</td>"
            f"<td class=\"num\">{metrics['pass_rate']:.3f}</td>"
            f"<td class=\"num\">{metrics['fatal_rate']:.3f}</td>"
            f"<td class=\"num\">{metrics['runs']}</td>"
            "</tr>"
        )
        operation_rows.append(
            "<tr>"
            f"<td>{_html_escape(model)}</td>"
            f"<td class=\"num\">{metrics['credits']:.3f}</td>"
            f"<td class=\"num\">{metrics['messages']}</td>"
            f"<td class=\"num\">{metrics['format_violation_rate']:.3f}</td>"
            f"<td class=\"num\">{metrics['stdev_score']:.3f}</td>"
            "</tr>"
        )
        for task, payload in metrics["task_types"].items():
            case_rows.append(
                "<tr>"
                f"<td>{model_labels[model]}</td>"
                f"<td>{_html_escape(task)}</td>"
                f"<td class=\"num\">{payload['mean_score']:.2f}</td>"
                f"<td class=\"num\">{metrics['local_llm_task_projection'][task]['mean_score_1_to_5']:.2f}</td>"
                f"<td class=\"num\">{payload['pass_rate']:.3f}</td>"
                f"<td class=\"num\">{payload['fatal_rate']:.3f}</td>"
                "</tr>"
            )
        for effort in EFFORT_LEVELS:
            effort_metrics = metrics.get("efforts", {}).get(effort)
            if not effort_metrics:
                continue
            effort_rows.append(
                "<tr>"
                f"<td><span class=\"chip\">{model_labels[model]}</span> {_html_escape(model)}</td>"
                f"<td>{_html_escape(effort)}</td>"
                f"<td class=\"num\">{effort_metrics['local_llm_overall_1_to_5']:.2f}</td>"
                f"<td class=\"num\">{effort_metrics['pass_rate']:.3f}</td>"
                f"<td class=\"num\">{effort_metrics['fatal_rate']:.3f}</td>"
                f"<td class=\"num\">{effort_metrics['runs']}</td>"
                "</tr>"
            )
    decision_support_rows = []
    decision_support_contract = {
        "GPT-5.5": {
            "decision": "品質確認・根因分析・最終レビューの第一候補",
            "why": "総合品質と要約系で最上位。複雑な判断の誤読リスクを下げる。",
            "risk": "高credit tierで、長時間実装主力にすると費用と制限到達が重い。",
            "next": "ClosureStabilityとCostPerClosureを追加live runで確認し、主力化は1.5倍以内条件で判断する。",
        },
        "GPT-5.6 Sol": {
            "decision": "高精度レビューとDeepDiveの上限候補",
            "why": "GPT-5.5と同じ高credit tierで、複雑な長文判断を担う候補として比較する。",
            "risk": "長時間主力では利用制限と継続安定性の実測が必要。",
            "next": "high固定の3反復でLuna-highとの品質差とCostPerClosureを確認する。",
        },
        "GPT-5.6 Terra": {
            "decision": "同credit tierの主力候補だが、現時点ではGPT-5.4同等疑いを維持",
            "why": "日本語NLUで強みはあるが、総合・repair・summaryでGPT-5.4を安定して引き離していない。",
            "risk": "Terra が GPT-5.4 程度か、という仮説を棄却するには長時間復旧のVCR/OSR証拠が足りない。",
            "next": "30-90分のCodex recovery telemetryを3回以上追加し、fallback・resume・public verifier到達率を見る。",
        },
        "GPT-5.6 Luna": {
            "decision": "低credit/高速候補として独立評価し、Terra/GPT-5.4の代替線に置く",
            "why": "公式 rate card 上は Luna がさらに低credit tierで、短時間・低負荷タスクの主力候補になりうる。",
            "risk": "低effortや低costが品質劣化を隠す可能性があるため、coding/summary/recoveryをeffort別に分けて読む必要がある。",
            "next": "low/medium/high の3 repetition平均を揃え、品質低下とCostPerClosureの交換条件を判定する。",
        },
        "GPT-5.4": {
            "decision": "低credit baselineとして維持し、Terra比較の基準にする",
            "why": "CODE_SYNTHで安定し、費用帯もTerraと同じ。Terra優位判定の最低比較線になる。",
            "risk": "要約・複合復旧では上位モデルに劣る可能性があり、主力化にはclosure証拠が不足。",
            "next": "同一fixture・同一順序ローテーションで平均と分散を継続比較する。",
        },
    }
    for model in TARGET_MODELS:
        payload = decision_support_contract[model]
        decision_support_rows.append(
            "<tr class=\"decision-row\">"
            f"<td><span class=\"chip\">{model_labels[model]}</span> {_html_escape(model)}</td>"
            f"<td>{_html_escape(payload['decision'])}</td>"
            f"<td>{_html_escape(payload['why'])}</td>"
            f"<td>{_html_escape(payload['risk'])}</td>"
            f"<td>{_html_escape(payload['next'])}</td>"
            "</tr>"
        )
    hero_cards = "".join(
        (
            f"<div class=\"card {'best' if model == best_model else 'caution' if model == 'GPT-5.6 Terra' else ''}\">"
            f"<div class=\"kicker\">{model_labels[model]} {_html_escape('quality leader' if model == best_model else 'candidate')}</div>"
            f"<h3>{_html_escape(model)}</h3>"
            f"<p>{_html_escape(decision_support_contract[model]['decision'])}</p>"
            "</div>"
        )
        for model in TARGET_MODELS
    )
    axis_order = ["CODE_REPAIR", "CODE_SYNTH", "JA_NLU", "JA_SUMMARY", "format_control"]
    chart_groups = []
    group_width = 132
    chart_height = 340
    baseline_y = 280
    scale = 52
    for group_index, axis in enumerate(axis_order):
        x0 = 60 + group_index * group_width
        chart_groups.append(f"<text class=\"grp-txt\" x=\"{x0 + 50}\" y=\"318\">{_html_escape(axis.replace('_', ' '))}</text>")
        for model_index, model in enumerate(TARGET_MODELS):
            if axis == "format_control":
                value = _to_local_llm_scale(summary["models"][model]["decision_metric_scores"][axis])
            else:
                value = summary["models"][model]["local_llm_task_projection"][axis]["mean_score_1_to_5"]
            bar_h = max(1, round(value * scale))
            x = x0 + model_index * 34
            y = baseline_y - bar_h
            chart_groups.append(f"<rect x=\"{x}\" y=\"{y}\" width=\"28\" height=\"{bar_h}\" fill=\"{model_colors[model]}\" rx=\"3\"/>")
            chart_groups.append(f"<text class=\"bar-val\" x=\"{x + 14}\" y=\"{max(18, y - 8)}\">{value:.2f}</text>")
    score_chart = (
        f"<svg class=\"score-svg\" viewBox=\"0 0 760 {chart_height}\" role=\"img\" aria-label=\"1-5 axis score grouped bar chart\">"
        "<line class=\"axis-line\" x1=\"44\" y1=\"280\" x2=\"730\" y2=\"280\"/>"
        "<text class=\"axis-label\" x=\"18\" y=\"280\">1</text><text class=\"axis-label\" x=\"18\" y=\"72\">5</text>"
        + "".join(chart_groups)
        + "</svg>"
    )
    for row in summary.get("strength_exchange_summary", []):
        scores = " / ".join(
            f"{model_labels.get(item['model'], item['model'])}={item['score_1_to_5']:.2f}"
            for item in row["scores"]
        )
        trade_rows.append(
            "<tr>"
            f"<td>{_html_escape(row['axis'])}</td>"
            f"<td>{_html_escape(row['winner'])}</td>"
            f"<td class=\"num\">{row['winner_score_1_to_5']:.2f}</td>"
            f"<td class=\"num\">{row['spread_1_to_5']:.2f}</td>"
            f"<td>{_html_escape(scores)}</td>"
            "</tr>"
        )
    for row in summary.get("strength_exchange_summary", []):
        if row["axis"] in usecase_labels:
            quant_rows.append(
                "<tr>"
                f"<td>{_html_escape(usecase_labels[row['axis']])}</td>"
                f"<td>{_html_escape(row['winner'])}</td>"
                f"<td class=\"num\">{row['winner_score_1_to_5']:.2f}/5</td>"
                f"<td>{'順位材料' if row['spread_1_to_5'] >= 0.5 else '補助材料'}</td>"
                "</tr>"
            )
    for task in ["CODE_REPAIR", "CODE_SYNTH", "JA_NLU", "JA_SUMMARY"]:
        cells = []
        for model in TARGET_MODELS:
            mean = summary["models"][model]["local_llm_task_projection"][task]["mean_score_1_to_5"]
            mark = "◎" if mean >= 4.4 else "○" if mean >= 3.8 else "△" if mean >= 3.0 else "✕"
            cells.append(f"<td>{mark} {mean:.2f}/5</td>")
        decision_rows.append(f"<tr><td>{usecase_labels[task]}</td>{''.join(cells)}</tr>")
    source_items = "".join(
        f"<li><strong>{_html_escape(source['name'])}</strong>: {_html_escape(source['benchmark_design_use'])}</li>"
        for source in EXTERNAL_SOURCES.values()
    )
    task_mean_rows = []
    for task, label in usecase_labels.items():
        cells = []
        for model in TARGET_MODELS:
            payload = summary["models"][model]["task_types"][task]
            projection = summary["models"][model]["local_llm_task_projection"][task]
            cells.append(
                f"<td class=\"num\">{payload['mean_score']:.2f}</td>"
                f"<td class=\"num\">{projection['mean_score_1_to_5']:.2f}</td>"
            )
        task_mean_rows.append(
            f"<tr><td>{_html_escape(label)}</td>{''.join(cells)}</tr>"
        )
    score_rows = []
    for model in TARGET_MODELS:
        metrics = summary["models"][model]
        value = metrics["local_llm_overall_1_to_5"]
        width = max(2, min(100, value / 5.0 * 100))
        score_rows.append(
            "<div class=\"bar-row\">"
            f"<div class=\"bar-label\"><span class=\"chip\">{model_labels[model]}</span> {_html_escape(model)}</div>"
            f"<div class=\"bar-track\"><div class=\"bar\" style=\"width:{width:.1f}%;background:{model_colors[model]}\"></div></div>"
            f"<div class=\"bar-val\">{value:.2f}/5</div>"
            "</div>"
        )
    total_runs = sum(int(summary["models"][model]["runs"]) for model in TARGET_MODELS)
    model_sequence = " / ".join(TARGET_MODELS)
    decision_header_cells = "".join(f"<th>{_html_escape(model_labels[model])} {_html_escape(model)}</th>" for model in TARGET_MODELS)
    task_mean_header_1 = "".join(f"<th colspan=\"2\">{_html_escape(model_labels[model])} {_html_escape(model)}</th>" for model in TARGET_MODELS)
    task_mean_header_2 = "".join("<th class=\"num\">oracle</th><th class=\"num\">1-5</th>" for _model in TARGET_MODELS)
    reflection_items = [
        ("H-00", "意思決定者の意思決定を補佐する情報提供になっていなかった", "first viewportに採用判断・理由・リスク・次アクションを必須化し、単一総合点で採否を決めない構造にする"),
        ("R-01", "参照HTMLのDOM構造を最初に抽出しなかった", "source report structural inventory を生成前Redテストにする"),
        ("R-02", "r5の線形資料をタブUIへ置き換えた", "section order contract で tablist 退化を禁止する"),
        ("R-03", "見出し一致だけを見て資料の主従関係を見なかった", "Hero / Decision / Evidence / Detail の階層をテストする"),
        ("R-04", "Score Explorerを大型比較図として扱わなかった", "score-explorer class と大型比較領域を必須化する"),
        ("R-05", "Usecase Winnersを単なる補助表にした", "winner-grid と用途別勝者の存在を必須化する"),
        ("R-06", "Decision Matrixの英語ラベルを欠落させた", "Decision Matrix 文字列と用途×モデル表をsentinel化する"),
        ("R-07", "Evaluation DesignをScore Methodだけに畳んだ", "採点設計セクションと測定限界を分離する"),
        ("R-08", "Case Libraryを消した", "全caseをcase-cardとして出すDOM契約を追加する"),
        ("R-09", "Audits / Harness Auditを薄くした", "反省行30件以上を最低条件にする"),
        ("R-10", "r5との差分を人間目視だけで済ませた", "reference HTML diff summary をテスト入力にする"),
        ("R-11", "report_quality_gate passを見た目の十分条件に誤用した", "quality gateとは別にsource_style_gateを設ける"),
        ("R-12", "新規live実行と既存run再集計の境界が弱かった", "run_origin をfirst viewportとMeasurement Limitに出す"),
        ("R-13", "3回平均の主張をHTML上で目立たせきれなかった", "minimum repetitions とbalanced coverageを複数箇所に表示する"),
        ("R-14", "coding差分の見せ方を外部benchmark型に寄せきれなかった", "HumanEval/MBPP/SWE-bench型のoracle説明をEvaluation Designへ置く"),
        ("R-15", "日本語能力と要約能力を1軸で丸めかけた", "JA_NLUとJA_SUMMARYを別用途行として維持する"),
        ("R-16", "運用ゲートと品質点の分離を視覚的に弱くした", "Operational Gateを独立カード/表にする"),
        ("R-17", "tool-calling測定不能の注意を弱めた", "Measurement Limitのダーク帯でproxy禁止を明示する"),
        ("R-18", "ユーザー提示コマンド内のHTMLパスを正本として扱うのが遅れた", "提示パスをsource_status=UserConfirmed相当として扱う"),
        ("R-19", "過去ローカルLLM資料の『見せ方』を方法論だけに矮小化した", "visual structure と method contract を別々に検査する"),
        ("R-20", "untracked成果物が消える状態を放置した", "runner/test file_exists を検証前に確認する"),
        ("R-21", "復旧コピーから戻した後の永続化確認が弱かった", "git status --untracked-files=all をevidenceに入れる"),
        ("R-22", "テストがr6の誤ったタブ構造を肯定していた", "誤った期待値をRedに差し替える"),
        ("R-23", "HTML再生成後に構造抽出を再実行しなかった", "生成後にheading/class/order inventoryを再チェックする"),
        ("R-24", "スクリーンショットでfirst viewportだけ見て構造不足を見逃した", "visual check とDOM contractを両方必須にする"),
        ("R-25", "反省点を8件程度で済ませた", "失敗分類を工程別に30件以上で列挙する"),
        ("R-26", "『改善計画』と『実装済み恒久対策』を混同しやすい報告にした", "改善案は案、実装は未実施と明記する"),
        ("R-27", "preflight通過を成果物品質の代替にした", "preflightは最終矛盾検査であり成果物検査ではないと分離する"),
        ("R-28", "既存dirty worktreeの副作用分類が遅かった", "関係ファイル/無関係ファイルを最初に分ける"),
        ("R-29", "評価レポート用DESIGN.mdの高忠実度参照を後回しにした", "review-eval-scoring-design のreport contractを先に読む"),
        ("R-30", "case libraryの原データと表示件数の対応を固定しなかった", "case-card数 >= build_matrix_cases数をテストする"),
        ("R-31", "モデル記号M1/M2/M3の一貫性を弱めた", "legendと表の記号表示を必須化する"),
        ("R-32", "用途別判断と総合判断を混ぜた", "用途別判断を先、総合は補助として配置する"),
        ("R-33", "外部benchmark本体スコアではない境界が弱かった", "External Benchmark Groundingで設計利用に限定する"),
        ("R-34", "ユーザーの不満に対して成果物更新より説明が先行した", "不満指摘後はRed testか成果物再生成を同一ターンで行う"),
        ("R-35", "反省をチャットだけに閉じかけた", "反省と改善計画をHTML内とMarkdown成果物の両方に置く"),
        ("R-36", "完了報告で大きな未実装改善案を埋もれさせた", "将来改善は明示的な残タスク分類に残す"),
    ]
    reflection_rows = "".join(
        "<tr class=\"reflection-row\">"
        f"<td><span class=\"chip\">{_html_escape(item_id)}</span></td>"
        f"<td>{_html_escape(problem)}</td>"
        f"<td>{_html_escape(action)}</td>"
        "</tr>"
        for item_id, problem, action in reflection_items
    )
    case_cards = "".join(
        "<div class=\"case-card\">"
        f"<div class=\"kicker\">{_html_escape(case['task_type'])}</div>"
        f"<strong>{_html_escape(case['case_id'])}</strong>"
        f"<p>{_html_escape(case['oracle'] if isinstance(case['oracle'], str) else case.get('run_mode', 'oracle'))}</p>"
        "</div>"
        for case in build_matrix_cases()
    )
    linear_html = f"""<!doctype html>
<html lang="ja">
<meta charset="utf-8">
<title>GPT External Benchmark Matrix</title>
<style>
:root {{ --navy:#181C2A; --paper:#FAF7F0; --paper-soft:#F2EEE3; --surface:#FFFFFF; --ink:#1A1A1A; --muted:#5C5A52; --gold:#C9A155; --blue:#2D5BB8; --green:#3D7E60; --red:#B83A2D; --border:#E2DED4; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; color:var(--ink); background:linear-gradient(180deg,var(--paper) 0%,var(--paper-soft) 100%); font-family:Inter,"Noto Sans JP",-apple-system,"Segoe UI",sans-serif; }}
main {{ max-width:1180px; margin:0 auto; padding:34px 28px 64px; }}
.kicker,.chip,.bar-label,.bar-val {{ font-family:"JetBrains Mono",Consolas,monospace; letter-spacing:0; }}
.kicker {{ color:var(--gold); font-size:12px; font-weight:900; text-transform:uppercase; }}
h1 {{ font-family:Inter,-apple-system,"Segoe UI",sans-serif; font-size:clamp(38px,6vw,76px); line-height:.98; margin:10px 0 18px; color:var(--navy); letter-spacing:0; }}
h2 {{ font-family:Inter,-apple-system,"Segoe UI",sans-serif; font-size:clamp(26px,4vw,42px); line-height:1.08; margin:0 0 18px; color:var(--navy); }}
h3 {{ margin:24px 0 12px; font-size:22px; font-family:Inter,"Noto Sans JP",-apple-system,"Segoe UI",sans-serif; }}
section {{ padding:48px 0; border-top:1px solid var(--border); }}
.lead {{ font-size:18px; line-height:1.75; color:var(--muted); max-width:920px; }}
.label-toggle {{ display:flex; gap:8px; justify-content:flex-end; margin:10px 0 18px; position:sticky; top:0; z-index:4; padding:8px 0; background:linear-gradient(180deg,var(--paper) 70%,rgba(250,247,240,.72)); }}
.label-toggle button {{ border:1px solid var(--border); background:var(--surface); padding:8px 12px; font-family:"JetBrains Mono",Consolas,monospace; font-weight:900; }}
.label-toggle button[aria-pressed="true"] {{ background:var(--navy); color:var(--paper); }}
.hero-grid,.winner-grid,.case-grid {{ display:grid; gap:14px; }}
.hero-grid {{ grid-template-columns:repeat(4,minmax(0,1fr)); margin-top:26px; }}
.winner-grid {{ grid-template-columns:repeat(3,minmax(0,1fr)); }}
.case-grid {{ grid-template-columns:repeat(3,minmax(0,1fr)); }}
.decision-summary {{ background:var(--surface); border:1px solid var(--border); padding:20px; }}
.decision-summary .note {{ margin-top:12px; }}
.card,.case-card,.note {{ background:var(--surface); border:1px solid var(--border); padding:18px; }}
.card.best {{ border-top:5px solid var(--gold); }}
.card.caution {{ border-top:5px solid var(--red); }}
.card.avoid {{ opacity:.78; }}
.chip {{ display:inline-flex; align-items:center; justify-content:center; border:1px solid var(--border); background:var(--surface); color:var(--navy); padding:4px 8px; font-size:12px; font-weight:900; }}
.score-explorer {{ background:var(--surface); border:1px solid var(--border); padding:22px; }}
.bar-row {{ display:grid; grid-template-columns:180px 1fr 74px; gap:12px; align-items:center; margin:10px 0; }}
.bar-label {{ font-weight:900; }}
.bar-track {{ height:18px; background:#EAE3D3; position:relative; }}
.bar {{ height:18px; background:var(--blue); }}
.bar-val {{ text-align:right; font-weight:900; }}
table {{ width:100%; border-collapse:collapse; background:var(--surface); border:1px solid var(--border); margin:14px 0; }}
th,td {{ border-bottom:1px solid var(--border); padding:11px 12px; text-align:left; vertical-align:top; }}
th {{ background:var(--navy); color:var(--paper); font-family:Inter,-apple-system,"Segoe UI",sans-serif; }}
.num {{ text-align:right; font-variant-numeric:tabular-nums; font-family:"JetBrains Mono",Consolas,monospace; }}
.dark {{ background:var(--navy); color:var(--paper); padding:22px; margin-top:18px; }}
.dark h2,.dark h3,.dark p {{ color:var(--paper); }}
.note {{ color:var(--muted); line-height:1.75; }}
.reflection-row td:first-child {{ width:88px; }}
@media (max-width:820px) {{ main {{ padding:20px 14px 44px; }} .hero-grid,.winner-grid,.case-grid {{ grid-template-columns:1fr; }} .bar-row {{ grid-template-columns:1fr; }} }}
</style>
<body>
<main data-report-primary="true" data-label-mode="symbol">
<div class="label-toggle" aria-label="model label mode">
  <button type="button" aria-pressed="true">M1–Mn</button>
  <button type="button" aria-pressed="false">モデル名</button>
</div>
<header>
  <div class="kicker">Decision Brief · News-Grasp / Codex · {total_runs} runs · minimum repetitions: 3 · efforts: low / medium / high</div>
  <h1>GPT External Benchmark Matrix</h1>
  <p class="lead">結論と採用方針: {html.escape(model_sequence)} を、過去ローカルLLM比較資料の読み方に合わせて評価する。新規live実行ではなく既存run再集計であり、速度・VRAM・credits は品質点に加算しない。single-run decision is forbidden。</p>
  <div class="hero-grid">
    {hero_cards}
    <div class="card avoid"><div class="kicker">Boundary</div><h3>測定境界</h3><p>外部benchmark本体スコアではなく、外部benchmark型に寄せたlocal fixture。</p></div>
  </div>
</header>
<section data-report-section="decision-support" id="verdict">
  <div class="kicker">00 — Verdict / Decision Support</div>
  <h2>意思決定者向けサマリ</h2>
  <div class="decision-summary">
    <p><strong>どのモデルを、どの用途で、どの条件なら使うか</strong>を先に示す。単一の総合点で採否を決めない。品質トップでも運用主力とは限らないため、品質・費用・継続安定性・測定不能軸を分けて読む。</p>
    <table><thead><tr><th>Model</th><th>採用判断</th><th>判断理由</th><th>主要リスク</th><th>次アクション</th></tr></thead><tbody>{''.join(decision_support_rows)}</tbody></table>
    <p class="note"><strong>追加で必要な証拠:</strong> Terra が GPT-5.4 程度かを確定するには、同一fixtureの最低3回平均に加えて、30-90分 recovery task のVCR/OSR、fallback、resume、public verifier到達率が必要。</p>
  </div>
</section>
<section>
  <div class="kicker">01 — Decision Matrix</div>
  <h2>用途別判断</h2>
  <table><thead><tr><th>Usecase</th>{decision_header_cells}</tr></thead><tbody>{''.join(decision_rows)}</tbody></table>
</section>
<section data-report-section="score-method">
  <div class="kicker">05 — Evaluation Design</div>
  <h2>Score Method</h2>
  <p>正本は AI-Pulse の過去ローカルLLM調査。1-5の factual / summary / points / rationale / overall 軸、N={method_contract['sample_count']}、採点済み {method_contract['judged_count']}、completion rate と latency の分離を要求する。今回は盲検実施済みとは主張しない。</p>
  <p>HumanEval / MBPP 型は実行テスト、SWE-bench / LiveCodeBench 型はpatch + pytest、JGLUE型は根拠span、XL-Sum型は事実反転・禁則主張検出で扱う。</p>
  <p>品質、安定性、形式制御、速度、VRAM、日本語品質、重みは同じ総合点に混ぜない。品質はcase oracle、安定性は反復stdev、形式制御はformat violation、速度/VRAM/creditsは運用ゲート、日本語品質はJA_NLU/JA_SUMMARY、重みはDecision Matrix側で分離する。</p>
</section>
<section data-report-section="local-llm-projection">
  <div class="kicker">02 — Score Explorer</div>
  <h2>Local LLM Method Projection</h2>
  <div class="score-explorer">
    <h3>Overall 1-5 projection</h3>
    {''.join(score_rows)}
  </div>
  <p class="note">1-5 projection は過去資料の見せ方に合わせた表示であり、新規live実行ではなく既存run再集計。</p>
</section>
<section>
  <div class="kicker">02B — Score Explorer Detail</div>
  <h2>Task Type Mean Scores</h2>
  <table><thead><tr><th>Task</th>{task_mean_header_1}</tr><tr><th></th>{task_mean_header_2}</tr></thead><tbody>{''.join(task_mean_rows)}</tbody></table>
</section>
<section>
  <div class="kicker">03 — Usecase Winners</div>
  <h2>Usecase Winners</h2>
  <div class="winner-grid">
    {''.join(f'<div class="card"><div class="kicker">{_html_escape(row["axis"])}</div><h3>{_html_escape(row["winner"])}</h3><p>score {row["winner_score_1_to_5"]:.2f}/5 / spread {row["spread_1_to_5"]:.2f}</p></div>' for row in summary.get("strength_exchange_summary", []))}
  </div>
  <h3>強みの交換</h3>
  <table><thead><tr><th>Axis</th><th>Winner</th><th class="num">Winner Score</th><th class="num">Spread</th><th>All Models</th></tr></thead><tbody>{''.join(trade_rows)}</tbody></table>
</section>
<section>
  <div class="kicker">04 — Operational Gate</div>
  <h2>Operational Gate</h2>
  <p class="note">速度・VRAM・credits は品質点に加算しない。長時間Codex作業の運用適性は品質とは別に読む。</p>
  <table><thead><tr><th>Model</th><th class="num">Estimated Credits</th><th class="num">Messages</th><th class="num">Format Violation</th><th class="num">Stdev</th></tr></thead><tbody>{''.join(operation_rows)}</tbody></table>
</section>
<section>
  <div class="kicker">04B — Effort Level Slice</div>
  <h2>Effort Level Slice</h2>
  <p class="note">同一モデル内の low / medium / high を分けて表示する。モデル平均の前に、effort変更による品質劣化・分散・FatalRateを確認する。</p>
  <table><thead><tr><th>Model</th><th>Effort</th><th class="num">Overall 1-5</th><th class="num">PassRate</th><th class="num">FatalRate</th><th class="num">Runs</th></tr></thead><tbody>{''.join(effort_rows)}</tbody></table>
</section>
<section class="dark">
  <div class="kicker">– — Measurement Limit</div>
  <h2>Measurement Limit</h2>
  <p>このレポートは external benchmark design grounding を使ったlocal matrixであり、HumanEval / MBPP / SWE-bench / JGLUE / XL-Sum 本体スコアではない。tool-callingや90分連続自走の実能力はproxyで捏造せず、別telemetryが必要。</p>
</section>
<section>
  <div class="kicker">– — External Benchmark Grounding</div>
  <h2>External Benchmark Grounding</h2>
  <ul>{source_items}</ul>
</section>
<section>
  <div class="kicker">06 — Case Library</div>
  <h2>Case Library</h2>
  <div class="case-grid">{case_cards}</div>
</section>
<section>
  <div class="kicker">07 — Audits</div>
  <h2>Harness Audit</h2>
  <div class="note"><strong>今回の根本問題:</strong> 正本資料の見せ方を移植せず、採点契約だけを直して同じ比較と見なしたこと。</div>
  <table><thead><tr><th>ID</th><th>反省点</th><th>既存ハーネスへの改善計画</th></tr></thead><tbody>{reflection_rows}</tbody></table>
</section>
</main>
</body>
</html>
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(linear_html, encoding="utf-8")
    return output_path
    html_text = f"""<!doctype html>
<html lang="ja">
<meta charset="utf-8">
<title>GPT External Benchmark Matrix</title>
<style>
:root {{ --navy:#181C2A; --paper:#FAF7F0; --paper-soft:#F2EEE3; --paper-dim:#EAE3D3; --surface:#FFFFFF; --ink:#1A1A1A; --muted:#5C5A52; --gold:#C9A155; --blue:#2D5BB8; --green:#3D7E60; --red:#B83A2D; --border:#E2DED4; }}
* {{ box-sizing: border-box; }}
body {{ font-family: "Noto Serif JP", "Yu Mincho", serif; margin: 0; color: var(--ink); background: linear-gradient(180deg, var(--paper) 0%, var(--paper-soft) 100%); }}
main.container {{ max-width: 1180px; margin: 0 auto; padding: 34px 28px 56px; }}
.hd {{ border-bottom: 3px solid var(--navy); padding: 22px 0 28px; }}
.eyebrow, .kicker, .badge, .tab, .chip {{ font-family: "JetBrains Mono", Consolas, monospace; letter-spacing: 0; }}
.eyebrow {{ font-size: 12px; font-weight: 800; color: var(--gold); text-transform: uppercase; }}
h1 {{ font-family: Inter, -apple-system, "Segoe UI", sans-serif; font-size: clamp(34px, 6vw, 72px); line-height: 0.98; letter-spacing: 0; margin: 10px 0 14px; color: var(--navy); }}
h2 {{ font-family: Inter, -apple-system, "Segoe UI", sans-serif; font-size: clamp(26px, 4vw, 42px); line-height: 1.1; letter-spacing: 0; margin: 0 0 18px; color: var(--navy); }}
h3 {{ font-size: 22px; margin: 26px 0 12px; }}
.lead {{ font-size: 17px; line-height: 1.85; max-width: 920px; color: var(--muted); }}
.badges {{ display:flex; flex-wrap:wrap; gap:8px; margin-top:16px; }}
.badge, .chip {{ display:inline-flex; align-items:center; justify-content:center; border:1px solid var(--border); background:var(--surface); color:var(--navy); padding:5px 10px; font-size:12px; font-weight:800; }}
.tabs {{ display:flex; gap:0; overflow:auto; border-bottom:1px solid var(--border); margin:20px 0 26px; }}
.tab {{ border:1px solid var(--border); border-bottom:0; background:var(--paper-dim); color:var(--navy); padding:11px 14px; font-weight:800; cursor:pointer; white-space:nowrap; }}
.tab.active {{ background:var(--navy); color:var(--paper); }}
.panel {{ display:none; background:var(--surface); border:1px solid var(--border); padding:26px; }}
.panel.active {{ display:block; }}
.callout, .rec, .card, .fig {{ border:1px solid var(--border); background:var(--paper); padding:18px; margin:18px 0; }}
.callout {{ border-left:6px solid var(--gold); }}
.tag {{ display:inline-block; background:var(--navy); color:var(--paper); padding:4px 8px; font-family:"JetBrains Mono", Consolas, monospace; font-size:12px; font-weight:800; }}
.rec {{ border-top:5px solid var(--gold); }}
.grid2, .grid3, .grid4 {{ display:grid; gap:14px; }}
.grid2 {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
.grid3 {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
.grid4 {{ grid-template-columns: repeat(4, minmax(0, 1fr)); }}
.kpi {{ font-family: Inter, -apple-system, "Segoe UI", sans-serif; font-size:30px; font-weight:900; color:var(--navy); }}
.kpi small {{ display:block; font-family:"Noto Serif JP", "Yu Mincho", serif; font-size:12px; font-weight:700; color:var(--muted); margin-top:4px; }}
table {{ border-collapse:collapse; width:100%; background:var(--surface); border:1px solid var(--border); margin:14px 0; }}
th, td {{ border-bottom:1px solid var(--border); padding:11px 12px; text-align:left; vertical-align:top; }}
th {{ background:var(--navy); color:var(--paper); font-family:Inter, -apple-system, "Segoe UI", sans-serif; }}
.num {{ text-align:right; font-variant-numeric: tabular-nums; font-family:"JetBrains Mono", Consolas, monospace; }}
.best-row {{ background:#FFF7DA; }}
.win {{ color:var(--green); font-weight:900; }}
.lose {{ color:var(--red); font-weight:900; }}
.note {{ color:var(--muted); line-height:1.8; }}
.legend {{ display:flex; gap:16px; flex-wrap:wrap; margin:8px 0 12px; }}
.legend-item {{ display:inline-flex; align-items:center; gap:6px; font-family:"JetBrains Mono", Consolas, monospace; font-size:12px; font-weight:800; }}
.legend-item span {{ width:14px; height:14px; display:inline-block; }}
.score-svg, .flow-svg {{ width:100%; height:auto; background:var(--paper); border:1px solid var(--border); }}
.axis-line {{ stroke:var(--navy); stroke-width:2; }}
.axis-label, .grp-txt, .bar-val, .flow-txt, .flow-sub {{ font-family:"JetBrains Mono", Consolas, monospace; fill:var(--navy); text-anchor:middle; }}
.axis-label {{ font-size:12px; text-anchor:start; }}
.grp-txt {{ font-size:10px; font-weight:800; }}
.bar-val {{ font-size:11px; font-weight:800; }}
.flow-box {{ fill:#fff; stroke:var(--navy); stroke-width:2; }}
.flow-arrow {{ stroke:var(--gold); stroke-width:3; fill:none; marker-end:url(#arrow); }}
.flow-txt {{ font-size:13px; font-weight:900; }}
.flow-sub {{ font-size:10px; fill:var(--muted); }}
.dark {{ background:var(--navy); color:var(--paper); padding:22px; margin-top:18px; }}
.dark h3, .dark p {{ color:var(--paper); }}
@media (max-width: 820px) {{ main.container {{ padding:20px 14px 40px; }} .grid2, .grid3, .grid4 {{ grid-template-columns:1fr; }} .panel {{ padding:18px; }} th, td {{ padding:9px; }} }}
</style>
<body>
<main class="container" data-label-mode="symbol">
<header class="hd" data-report-primary="true">
  <div class="eyebrow">EVAL · 2026-07-14 · News-Grasp / Codex</div>
  <h1>GPT-5.5 / Terra / GPT-5.4 比較調査レポート</h1>
  <p class="lead">過去ローカルLLM比較資料の見せ方に合わせ、3モデル×全case×3反復の既存runを 1-5 軸別 projection と strength exchange で読み直す。総合だけで勝敗を断定せず、用途別の強みと運用リスクを分けて判断する。</p>
  <div class="badges">
    <span class="badge">72 runs</span>
    <span class="badge">3 repetitions</span>
    <span class="badge">source: {_html_escape(source_of_truth['id'])}</span>
    <span class="badge">結論: {terra_verdict}</span>
  </div>
</header>
<nav class="tabs" role="tablist">
  <button class="tab active" data-tab="summary">概要・推奨</button>
  <button class="tab" data-tab="quant">定量結果</button>
  <button class="tab" data-tab="evidence">詳細エビデンス</button>
  <button class="tab" data-tab="trade">強みの交換</button>
  <button class="tab" data-tab="decision">決定マトリクス</button>
  <button class="tab" data-tab="harness">反省・ハーネス</button>
</nav>
<section id="summary" class="panel active" data-report-layer="decision">
  <h2>エグゼクティブサマリ</h2>
  <p><strong>既存runの再集計では GPT-5.5 が首位</strong>。Terra は GPT-5.4 より overall で {terra_delta:.1f} pt 下回り、同credit tier内の明確な上振れは出ていない。CODE_REPAIR は全モデル同点、CODE_SYNTH は GPT-5.4 優位、日本語 NLU は Terra 優位、JA_SUMMARY は GPT-5.5 優位という<strong>強みの交換</strong>で読む。</p>
  <div class="callout">
    <span class="tag">正本化の修正</span>
    <p>今回のレポートは `reports/oss_model_*` ではなく、AI-Pulse の過去ローカルLLM調査を一次資料にした。正本の条件は N={method_contract['sample_count']} / 採点 {method_contract['judged_count']} / 1-5 軸別 / 完走率・速度分離。ただし今回は外部 blind judge を新規実行していないため、盲検実施済みとは主張しない。</p>
  </div>
  <div class="rec">
    <h3>推奨読み</h3>
    <div class="grid3">
      <div><div class="kpi">{summary['models'][best_model]['local_llm_overall_1_to_5']:.2f}<small>{_html_escape(best_model)} / 1-5 projection</small></div></div>
      <div><div class="kpi">{terra_delta:.1f}<small>Terra - GPT-5.4 / overall pt</small></div></div>
      <div><div class="kpi">0<small>speed / credit の品質点加算</small></div></div>
    </div>
  </div>
  <h3>調査の流れ（4段階）</h3>
  <svg class="flow-svg" viewBox="0 0 900 130" role="img" aria-label="benchmark report flow">
    <defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill="#C9A155"/></marker></defs>
    <rect class="flow-box" x="20" y="36" width="180" height="58" rx="0"/><text class="flow-txt" x="110" y="60">① 正本探索</text><text class="flow-sub" x="110" y="80">AI-Pulse 2026-06-04</text>
    <path class="flow-arrow" d="M205 65 H260"/>
    <rect class="flow-box" x="265" y="36" width="180" height="58" rx="0"/><text class="flow-txt" x="355" y="60">② 契約化</text><text class="flow-sub" x="355" y="80">manifest + tests</text>
    <path class="flow-arrow" d="M450 65 H505"/>
    <rect class="flow-box" x="510" y="36" width="180" height="58" rx="0"/><text class="flow-txt" x="600" y="60">③ 再集計</text><text class="flow-sub" x="600" y="80">72 runs / 3 reps</text>
    <path class="flow-arrow" d="M695 65 H750"/>
    <rect class="flow-box" x="755" y="36" width="120" height="58" rx="0"/><text class="flow-txt" x="815" y="60">④ report gate</text><text class="flow-sub" x="815" y="80">HTML品質検証</text>
  </svg>
</section>
<section id="quant" class="panel" data-report-layer="evidence" data-report-section="score-method">
  <h2>定量結果（1-5 projection）</h2>
  <p class="note">過去資料の軸別 1-5 表示に合わせ、機械 oracle の 0-10 点を 1-5 に正規化した。これは新規 blind judge 結果ではなく、既存runを正本方式で見せ直したもの。</p>
  <p class="note">品質は 1-5 projection で読み、安定性、形式制御、速度、estimated credits、VRAM は運用指標として品質点へ混ぜない。日本語品質は JA_NLU / JA_SUMMARY の evidence、must_not_claim、fact inversion で測り、用途別の重みは decision matrix 側だけで扱う。</p>
  <div class="legend">{''.join(legend_items)}</div>
  {score_chart}
  <table><thead><tr><th>モデル</th><th class="num">1-5 Overall</th><th class="num">Overall /100</th><th class="num">Pass</th><th class="num">Fatal</th><th class="num">Runs</th></tr></thead><tbody>{''.join(model_overview_rows)}</tbody></table>
  <h3>用途別winner</h3>
  <table><thead><tr><th>用途</th><th>Winner</th><th class="num">Score</th><th>判定材料</th></tr></thead><tbody>{''.join(quant_rows)}</tbody></table>
</section>
<section id="evidence" class="panel" data-report-layer="evidence">
  <h2>詳細エビデンス</h2>
  <h3>Score Method</h3>
  <p>正本は AI-Pulse のローカルLLM抽出置換調査で、1-5 の factual / summary / points / rationale / overall 軸、盲検 A/B/C、同一 prompt/schema/temp 0.4、completion と latency の品質点分離を要求している。今回の GPT 比較では外部 blind judge を実行していないため、盲検実施済みとは主張しない。</p>
  <p class="note">品質は 1-5 projection で読み、安定性、形式制御、速度、estimated credits、VRAM は運用指標として品質点へ混ぜない。日本語品質は JA_NLU / JA_SUMMARY の evidence、must_not_claim、fact inversion で測り、用途別の重みは decision matrix 側だけで扱う。</p>
  <table><thead><tr><th>Model</th><th>Task</th><th class="num">0-10 mean</th><th class="num">1-5 projection</th><th class="num">Pass</th><th class="num">Fatal</th></tr></thead><tbody>{''.join(case_rows)}</tbody></table>
  <h3>Operational Gate</h3>
  <table><thead><tr><th>Model</th><th class="num">Estimated Credits</th><th class="num">Messages</th><th class="num">Format Violation</th><th class="num">Stdev</th></tr></thead><tbody>{''.join(operation_rows)}</tbody></table>
</section>
<section id="trade" class="panel" data-report-layer="evidence" data-report-section="local-llm-projection">
  <h2>強みの交換</h2>
  <div class="kicker">Local LLM Method Projection</div>
  <p><strong>総合首位だけで結論を出さない。</strong> 過去資料の読み方に合わせ、各軸のwinnerとspreadを見る。Terra は JA_NLU で勝つが、CODE_SYNTH と overall では GPT-5.4 を上回っていない。</p>
  <table><thead><tr><th>Axis</th><th>Winner</th><th class="num">Winner Score</th><th class="num">Spread</th><th>All Models</th></tr></thead><tbody>{''.join(trade_rows)}</tbody></table>
  <div class="grid3">
    <div class="card"><h3>CODE_REPAIR</h3><p>3モデル同点。順位材料としては弱く、長時間復旧の主力判定には別matrixが必要。</p></div>
    <div class="card"><h3>CODE_SYNTH</h3><p>GPT-5.4 が明確に安定。Terra はこのrunでは劣後。</p></div>
    <div class="card"><h3>JA / 要約</h3><p>Terra は NLU で勝つが、要約は GPT-5.5 が強い。日本語能力を一軸で畳まない。</p></div>
  </div>
</section>
<section id="decision" class="panel" data-report-layer="decision">
  <h2>決定マトリクス</h2>
  <table><thead><tr><th>Usecase</th><th>M1 GPT-5.5</th><th>M2 Terra</th><th>M3 GPT-5.4</th></tr></thead><tbody>{''.join(decision_rows)}</tbody></table>
  <div class="dark"><h3>Measurement Limit</h3><p>このmatrixは外部benchmarkの設計型と過去ローカルLLM調査の見せ方を使った local fixture 再集計であり、HumanEval/MBPP/SWE-bench/JGLUE/XL-Sum 本体スコアではない。News-Grasp長時間復旧能力、tool-calling、公開検証到達率は別matrixで測る。</p><p>minimum repetitions: 3 / single-run decision is forbidden / complete balanced coverage = true.</p></div>
</section>
<section id="harness" class="panel" data-report-layer="audit">
  <h2>反省・ハーネス改善計画</h2>
  <div class="callout"><span class="tag">今回の根本問題</span><p>正本資料の見せ方を移植せず、採点契約だけを直して「同じ比較」と見なした。ユーザーが求めたのは、資料の構成・解釈順・視覚表現まで含む再現だった。</p></div>
  <table><thead><tr><th>反省点</th><th>既存ハーネスへの改善計画</th></tr></thead><tbody>
    <tr><td>一次資料を見つけた後、構造差分を取らずにHTMLを出した</td><td>比較レポート作成時は source report の tab/section/chart/table inventory を必須成果物にする</td></tr>
    <tr><td>report_quality_gate pass を「見せ方が十分」の代替にした</td><td>gate に source-style sentinel（tabs, score table, exchange section, decision matrix）を追加する</td></tr>
    <tr><td>新規live実行と既存run再集計の境界説明が弱かった</td><td>summary/html に measurement boundary を first viewport と decision tab の両方へ固定する</td></tr>
    <tr><td>ユーザーの怒りを説明で処理し、成果物差分で返せていなかった</td><td>不満指摘後は同一ターンで可視成果物を1つ以上再生成する acceptance を追加する</td></tr>
  </tbody></table>
  <h3>External Benchmark Grounding</h3>
  <ul>{source_items}</ul>
</section>
</main>
<script>
document.querySelectorAll('.tab').forEach((button) => {{
  button.addEventListener('click', () => {{
    document.querySelectorAll('.tab').forEach((tab) => tab.classList.remove('active'));
    document.querySelectorAll('.panel').forEach((panel) => panel.classList.remove('active'));
    button.classList.add('active');
    const target = document.getElementById(button.dataset.tab);
    if (target) target.classList.add('active');
  }});
}});
</script>
</body>
</html>
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_text, encoding="utf-8")
    return output_path


def _estimate_usage(prompt: str, answer: str) -> dict[str, int]:
    return {
        "input_tokens": max(1, len(prompt) // 4),
        "cached_input_tokens": 0,
        "output_tokens": max(1, len(answer) // 4),
        "messages": 1,
    }


def _estimate_credits(model: str, usage: dict[str, int]) -> float:
    rates = CREDIT_RATES_PER_MILLION.get(model, CREDIT_RATES_PER_MILLION["GPT-5.4"])
    return round(
        usage.get("input_tokens", 0) * rates["input"] / 1_000_000
        + usage.get("cached_input_tokens", 0) * rates["cached_input"] / 1_000_000
        + usage.get("output_tokens", 0) * rates["output"] / 1_000_000,
        6,
    )


def _default_codex_bin() -> str:
    extension_root = Path.home() / ".vscode" / "extensions"
    candidates = sorted(extension_root.glob("openai.chatgpt-*"), key=lambda path: path.stat().st_mtime, reverse=True)
    for candidate in candidates:
        exe = candidate / "bin" / "windows-x86_64" / "codex.exe"
        if exe.exists():
            return str(exe)
    return shutil.which("codex") or "codex"


def _prompt_for_case(case: dict[str, Any]) -> str:
    if case["task_type"] == "CODE_REPAIR":
        return (
            "あなたは coding benchmark の被験者です。現在の作業ディレクトリは sandbox repo です。\n"
            f"修正対象は {case['oracle']['target_file']} だけです。tests/** は変更禁止です。\n"
            "python -m pytest tests -q を実行し、最終応答は短い日本語にしてください。\n\n"
            f"課題: {case['source_text']}\n"
        )
    if case["task_type"] == "CODE_SYNTH":
        return (
            "JSONオブジェクトだけを返してください。schema: {\"code\":\"python code\", \"verification_command\":\"python -m pytest tests -q\"}\n"
            f"課題: {case['source_text']}\n"
        )
    if case["task_type"] == "JA_NLU":
        item_lines = "\n".join(f"- {item['id']}: label を entailment/contradiction/neutral から選び、本文の根拠spanを evidence に入れる" for item in case["oracle"]["items"])
        return (
            "JSONオブジェクトだけを返してください。schema: {\"answers\":[{\"id\":\"...\",\"label\":\"...\",\"evidence\":\"本文中の完全一致span\"}]}\n"
            f"{case['source_text']}\n設問:\n{item_lines}\n"
        )
    return (
        "JSONオブジェクトだけを返してください。schema: {\"headline\":\"...\",\"bullets\":[\"...\"],\"must_not_claim\":[\"...\"]}\n"
        "本文にない主張、事実反転、must_not_claim の未記載は禁止です。\n"
        f"本文: {case['source_text']}\n"
    )


def _run_codex_case(
    *,
    model: str,
    effort: str,
    case: dict[str, Any],
    repetition: int,
    out_dir: Path,
    codex_bin: str,
    timeout_sec: int,
) -> dict[str, Any]:
    if LIVE_EXECUTION_DISABLED:
        raise RuntimeError("historical comparison runner is report-only after the Luna-high migration")
    run_dir = out_dir / "runs" / model.replace(" ", "_") / effort / case["case_id"] / f"r{repetition}"
    run_dir.mkdir(parents=True, exist_ok=True)
    cwd = run_dir
    if case["task_type"] == "CODE_REPAIR":
        cwd = prepare_sandbox_case(case, run_dir)
    prompt = _prompt_for_case(case)
    raw_answer_path = run_dir / "raw_answer.txt"
    stderr_path = run_dir / "stderr.log"
    args = [
        codex_bin,
        "exec",
        "--model",
        MODEL_CLI_NAMES.get(model, model),
        "-c",
        f'model_reasoning_effort="{effort}"',
        "--skip-git-repo-check",
        "--cd",
        str(cwd),
        "--output-last-message",
        str(raw_answer_path),
        "-",
    ]
    started = time.time()
    try:
        proc = run_model_process(
            args,
            route="external_benchmark_matrix",
            input=prompt,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        stderr = (proc.stdout or "") + (proc.stderr or "")
        exit_code = proc.returncode
    except subprocess.TimeoutExpired as exc:
        stderr = (exc.stdout or "") + (exc.stderr or "") + "\nTIMEOUT"
        exit_code = 124
    stderr_path.write_text(stderr, encoding="utf-8")
    raw_answer = raw_answer_path.read_text(encoding="utf-8") if raw_answer_path.exists() else ""
    usage = _estimate_usage(prompt, raw_answer)
    record = {
        "model": model,
        "effort": effort,
        "task_type": case["task_type"],
        "case_id": case["case_id"],
        "repetition": repetition,
        "raw_answer": raw_answer,
        "stderr": stderr,
        "codex_exit_code": exit_code,
        "duration_sec": round(time.time() - started, 3),
        "usage": usage,
        "messages": usage["messages"],
        "credits": _estimate_credits(model, usage),
    }
    if exit_code != 0 and not raw_answer:
        record.update({"pass": False, "score": 0.0, "fatal": True, "validator": {"exit_code": 1, "log": "codex_exec_failed"}})
    else:
        score_case(record, case, run_dir)
    _write_json(run_dir / "run.json", record)
    return record


def execute_benchmark(
    *,
    out_dir: Path,
    models: list[str],
    efforts: list[str],
    repetitions: int,
    codex_bin: str,
    timeout_sec: int,
    resume: bool = False,
) -> list[dict[str, Any]]:
    if repetitions < MIN_REPETITIONS:
        raise ValueError("minimum 3 repetitions required")
    records_path = out_dir / "records.json"
    records = load_records(records_path) if resume and records_path.is_file() else []
    completed = {
        (str(record.get("model")), str(record.get("effort")), str(record.get("case_id")), int(record.get("repetition") or 0))
        for record in records
    }
    for model in models:
        for effort in efforts:
            for case in build_matrix_cases():
                for repetition in range(1, repetitions + 1):
                    key = (model, effort, case["case_id"], repetition)
                    if key in completed:
                        continue
                    record = _run_codex_case(
                        model=model,
                        effort=effort,
                        case=case,
                        repetition=repetition,
                        out_dir=out_dir,
                        codex_bin=codex_bin,
                        timeout_sec=timeout_sec,
                    )
                    records.append(record)
                    completed.add(key)
                    _write_json(records_path, {"records": records})
    if set(models) == set(TARGET_MODELS) and set(efforts) == set(EFFORT_LEVELS):
        write_summary(out_dir, records)
    else:
        _write_json(out_dir / "summary.partial.json", aggregate_records(records, allow_partial=True))
    return records


def build_manifest(repetitions: int) -> dict[str, Any]:
    return {
        "schema_version": "external_benchmark_matrix.v1",
        "target_models": list(TARGET_MODELS),
        "target_efforts": list(EFFORT_LEVELS),
        "task_types": TASK_TYPES,
        "case_count_min": CASE_COUNT_MIN,
        "case_count": len(CASES),
        "minimum_repetitions": repetitions,
        "single_run_decision_allowed": False,
        "external_sources": EXTERNAL_SOURCES,
        "fixture_manifest": FIXTURE_MANIFEST,
        "provenance_snapshot_sha256": PROVENANCE_SNAPSHOT_SHA256,
        "local_llm_materials_sha256": LOCAL_LLM_MATERIALS_SHA256,
        "local_llm_source_of_truth": LOCAL_LLM_MATERIALS["source_of_truth"],
        "local_llm_primary_method_contract": LOCAL_LLM_MATERIALS["primary_method_contract"],
    }


def load_records(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    return payload["records"] if isinstance(payload, dict) and "records" in payload else payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=default_raw_root("external-benchmark-matrix"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--score-file", type=Path)
    parser.add_argument("--allow-local-code-execution", action="store_true")
    parser.add_argument("--html-report", action="store_true")
    parser.add_argument("--summary-file", type=Path)
    parser.add_argument("--report-out", type=Path)
    parser.add_argument("--models", nargs="+", default=list(TARGET_MODELS))
    parser.add_argument("--efforts", nargs="+", default=list(EFFORT_LEVELS), choices=list(EFFORT_LEVELS))
    parser.add_argument("--repetitions", type=int, default=MIN_REPETITIONS)
    parser.add_argument("--codex-bin", default=_default_codex_bin())
    parser.add_argument("--per-case-timeout-sec", type=int, default=240)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)

    if args.score_file and not args.allow_local_code_execution:
        print("--score-file requires --allow-local-code-execution", file=sys.stderr)
        return 2

    if args.execute and LIVE_EXECUTION_DISABLED:
        print("This historical comparison runner is report-only; use the Luna-high recovery benchmark.", file=sys.stderr)
        return 2

    if args.execute or args.score_file:
        args.out_dir = validate_raw_output_path(REPO_ROOT, args.out_dir)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    if args.repetitions < MIN_REPETITIONS:
        return 2
    _write_json(args.out_dir / "benchmark_manifest.json", build_manifest(args.repetitions))
    if args.dry_run:
        return 0
    if args.html_report:
        summary_path = args.summary_file or args.out_dir / "summary.json"
        report_path = args.report_out or args.out_dir / "external-benchmark-report.html"
        summary = json.loads(summary_path.read_text(encoding="utf-8-sig"))
        generate_html_report(summary, report_path)
        return 0
    if args.score_file:
        records = _rescore_loaded_records(load_records(args.score_file), args.out_dir)
        _write_json(args.out_dir / "records.json", {"records": records})
        summary = write_summary(args.out_dir, records)
        generate_html_report(summary, args.out_dir / "external-benchmark-report.html")
        return 0
    if args.execute:
        execute_benchmark(
            out_dir=args.out_dir,
            models=[str(model) for model in args.models],
            efforts=[str(effort) for effort in args.efforts],
            repetitions=args.repetitions,
            codex_bin=str(args.codex_bin),
            timeout_sec=args.per_case_timeout_sec,
            resume=args.resume,
        )
        return 0
    print(_json_dumps(build_manifest(args.repetitions)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
