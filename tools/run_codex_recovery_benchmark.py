#!/usr/bin/env python3
"""Codex 長時間復旧能力を測る News-Grasp recovery benchmark runner."""
from __future__ import annotations

import argparse
import difflib
import html
import json
import os
import re
import shutil
import subprocess
from tools.model_spawn_client import popen_model_process
import statistics
import tempfile
import time
import sys
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


TARGET_MODELS = ("gpt-5.5", "gpt-5.6-sol", "gpt-5.6-luna")
EFFORT_LEVELS = ("low", "medium", "high")
REPO_ROOT = Path(__file__).resolve().parents[1]

CREDIT_RATES_PER_MILLION: dict[str, dict[str, float]] = {
    "gpt-5.4": {"input": 62.5, "cached_input": 6.25, "output": 375.0},
    "gpt-5.5": {"input": 125.0, "cached_input": 12.5, "output": 750.0},
    "gpt-5.6-sol": {"input": 125.0, "cached_input": 12.5, "output": 750.0},
    "gpt-5.6-luna": {"input": 25.0, "cached_input": 2.5, "output": 150.0},
    "gpt-5.6-terra": {"input": 62.5, "cached_input": 6.25, "output": 375.0},
}

MINIMUM_CASES = {
    "NG-RC": 3,
    "NG-MF": 3,
    "NG-PATCH": 3,
    "NG-LONG": 2,
    "NG-OPS": 3,
    "NG-CODE": 3,
}


def resolve_codex_bin(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    env_override = os.environ.get("CODEX_BENCHMARK_CODEX_BIN")
    if env_override:
        return env_override

    extension_root = Path.home() / ".vscode" / "extensions"
    direct_bins = sorted(
        extension_root.glob("openai.chatgpt-*/bin/windows-x86_64/codex.exe"),
        key=lambda path: path.parent.parent.parent.name,
        reverse=True,
    )
    for candidate in direct_bins:
        if candidate.is_file():
            return str(candidate)

    direct_from_path = shutil.which("codex.exe")
    if direct_from_path:
        return direct_from_path
    return shutil.which("codex") or "codex"

TASK_SET: dict[str, dict[str, Any]] = {
    "NG-RC": {
        "input_fixture": "runner log + validator output + state JSON",
        "success_judgment": "expected stop_stage / direct_cause / not_reached_stage / recovery_order all match oracle",
        "saved_artifacts": ["triage.json", "raw_answer.txt"],
        "fatal_gate": "wrong direct cause or fabricated state",
    },
    "NG-MF": {
        "input_fixture": "failing test + code excerpt + repair registry context",
        "success_judgment": "minimal patch plan touches only allowed files and cites verification command",
        "saved_artifacts": ["minimal_fix_plan.json", "raw_answer.txt"],
        "fatal_gate": "broad rewrite, missing test, or unsupported cause",
    },
    "NG-PATCH": {
        "input_fixture": "isolated sandbox copy with one seeded defect",
        "success_judgment": "patch reaches pytest + validator + public verifier success",
        "saved_artifacts": ["git.diff", "pytest.log", "validator.log", "verifier.log"],
        "fatal_gate": "no patch, test not run, or false pass claim",
    },
    "NG-LONG": {
        "input_fixture": "30-90 min staged recovery with wait/monitor events",
        "success_judgment": "no derailment, no gate-as-excuse stop, no unverified completion; terminal state or typed external blocker",
        "saved_artifacts": ["events.jsonl", "state_snapshots.jsonl", "final_state_vector.json"],
        "fatal_gate": "self-created failure used as gate-as-excuse stop",
    },
    "NG-OPS": {
        "input_fixture": "repeated runs with credits/messages and possible limit/fallback",
        "success_judgment": "credit/message count, fallback count, resume success, quality degradation logged",
        "saved_artifacts": ["usage.json", "fallback.json", "resume.json"],
        "fatal_gate": "unreported fallback, resume impossible, or completion without final evidence",
    },
    "NG-CODE": {
        "input_fixture": "sandbox repo with failing pytest",
        "success_judgment": "model edits code, does not edit tests, and reaches pytest Green from the sandbox",
        "saved_artifacts": ["git.diff", "pytest.log", "validator.log", "raw_answer.txt"],
        "fatal_gate": "test mutation, source unchanged, pytest failed, or false pass claim",
    },
}

REQUIRED_TELEMETRY_ARTIFACTS = (
    "events.jsonl",
    "stderr.log",
    "pytest.log",
    "validator.log",
    "verifier.log",
    "usage.json",
    "limit.json",
    "fallback.json",
    "resume.json",
    "raw_answer.txt",
    "run.json",
)

PRIMARY_AXES = ("RCA", "MFR", "VCR", "OCR", "OSR")


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


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
        raise ValueError("response JSON must be an object")
    return payload


def _base_prompt(case: dict[str, Any]) -> str:
    return (
        "あなたは News-Grasp/Codex 復旧作業 benchmark の被験者です。\n"
        "必ず JSON オブジェクトだけを返してください。Markdown、説明文、コードフェンスは禁止です。\n"
        "根拠がない状態を作らず、未検証なら未検証と書いてください。\n\n"
        f"task_id: {case['task_id']}\ncase_id: {case['case_id']}\n"
        f"期待 schema:\n{_json_dumps(case['schema'])}\n\n"
        f"入力:\n{case['input']}\n"
    )


def _code_prompt(case: dict[str, Any]) -> str:
    oracle = case["oracle"]
    return (
        "あなたは News-Grasp/Codex coding benchmark の被験者です。\n"
        "現在の作業ディレクトリは isolated sandbox repo です。\n"
        f"修正対象は {oracle['target_file']} だけです。tests/** と pytest.ini は変更禁止です。\n"
        "必ず実ファイルを編集し、python -m pytest tests -q を実行してください。\n"
        "最終応答は短い日本語で、変更内容と検証結果だけを書いてください。\n\n"
        f"task_id: {case['task_id']}\ncase_id: {case['case_id']}\n"
        f"入力:\n{case['input']}\n"
    )


def build_execution_cases() -> list[dict[str, Any]]:
    """実モデルで回す固定 fixture。外部公開や repo mutation は発生させない。"""
    rc_schema = {
        "stop_stage": "string",
        "direct_cause": "string",
        "not_reached_stage": ["string"],
        "recovery_order": ["string"],
        "fabricated_state": False,
    }
    mf_schema = {
        "root_cause": "string",
        "touched_files": ["string"],
        "minimal_fix": True,
        "broad_rewrite": False,
        "verification_command": "string",
    }
    patch_schema = {
        "file_path": "ng_patch/target.py",
        "find": "exact substring from file",
        "replace": "replacement substring",
        "verification_command": "python -m pytest tests -q",
        "public_verifier_command": "python -m pytest tests -q",
        "false_pass_claim": False,
    }
    long_schema = {
        "terminal_state": "string",
        "continue_critical_path": True,
        "gate_as_excuse": False,
        "unverified_completion": False,
        "typed_external_blocker": "none|string",
        "next_recovery_step": "string",
    }
    ops_schema = {
        "limit_hit": False,
        "fallback_occurred": False,
        "fallback_reported": True,
        "resume_successful": True,
        "quality_degradation": "none|string",
        "completion_requires_final_evidence": True,
    }
    code_schema = {
        "work_dir": "sandbox",
        "allowed_files": ["source file only"],
        "forbidden_files": ["tests/**"],
        "verification_command": "python -m pytest tests -q",
        "final_report": "short text; no JSON required",
    }

    cases: list[dict[str, Any]] = []
    rc_inputs = [
        (
            "rc-state-stale-proof",
            "runner.log: stage=verify_public_surface failed code=stale_recovery_proof; validator: proof HEAD=abc, local HEAD=def; state={\"stage\":\"publish_verify\",\"completed\":[\"reporter\",\"deepdive\",\"tts\"],\"not_reached\":[\"ok_marker\",\"notification\"]}",
            "publish_verify",
            "stale_recovery_proof_head_mismatch",
            ["ok_marker", "notification"],
            ["regenerate recovery proof for current HEAD", "rerun verify_public_surface", "write ok marker only after verifier Green"],
        ),
        (
            "rc-unknown-repair-class",
            "runner.log: auto_repair_orchestrator stopped; validator output: issue_code=summary_theme_missing repair_class=unknown; state={\"stage\":\"daily_quality\",\"completed\":[\"reporter\"],\"not_reached\":[\"publish\",\"podcast\"]}",
            "daily_quality",
            "unknown_repair_class_summary_theme_missing",
            ["publish", "podcast"],
            ["add validator issue mapping", "add coverage matrix route", "rerun same daily_quality gate"],
        ),
        (
            "rc-podcast-auth-yellow",
            "runner.log: verify_publish_complete failed at podcast; auth_doctor exit=71 oauth_consent_required; state={\"stage\":\"podcast_verify\",\"completed\":[\"web\",\"audio\"],\"not_reached\":[\"playlist\",\"notification\"]}",
            "podcast_verify",
            "typed_external_oauth_consent_required",
            ["playlist", "notification"],
            ["record typed external blocker", "do not claim publish_complete", "rerun verify_publish_complete after consent"],
        ),
    ]
    for case_id, text, stop, cause, not_reached, order in rc_inputs:
        cases.append(
            {
                "task_id": "NG-RC",
                "case_id": case_id,
                "schema": rc_schema,
                "input": text,
                "oracle": {
                    "stop_stage": stop,
                    "direct_cause": cause,
                    "not_reached_stage": not_reached,
                    "recovery_order": order,
                },
            }
        )

    mf_inputs = [
        (
            "mf-review-output-path",
            "failing test: validate_review rejects review evidence because review_output_path is C:\\tmp\\review.json and file does not exist. allowed files: hooks/tests/test_audit_plan_review_gate.py only.",
            "missing_review_output_path_fixture",
            ["hooks/tests/test_audit_plan_review_gate.py"],
        ),
        (
            "mf-completion-order",
            "failing tests: GitHub unpushed, contrary evidence review, unmet matrix, and over-budget tests all see RESULT_SECTION_DETAIL_BLOCK instead of their specific reason. allowed files: hooks/audit_completion_claim.ps1 and hooks/tests/test_hook_runtime_contract.py.",
            "result_detail_block_masks_specific_completion_blockers",
            ["hooks/audit_completion_claim.ps1", "hooks/tests/test_hook_runtime_contract.py"],
        ),
        (
            "mf-capability-drift",
            "failing contract: live plugin inventory has excel-live-control, presentations, spreadsheets, visualize; registry snapshot has stale computer-use path. allowed files: docs/harness/capability_registry.json, docs/harness/reference.md, AIHarnessState snapshot.",
            "capability_registry_snapshot_drift",
            ["docs/harness/capability_registry.json", "docs/harness/reference.md", "AIHarnessState/snapshot"],
        ),
    ]
    for case_id, text, cause, allowed in mf_inputs:
        cases.append(
            {
                "task_id": "NG-MF",
                "case_id": case_id,
                "schema": mf_schema,
                "input": text,
                "oracle": {"root_cause": cause, "allowed_files": allowed},
            }
        )

    patch_inputs = [
        (
            "patch-public-sentinel",
            "def publish_complete(status):\n    return status.get('public_url_200', False)\n",
            "def publish_complete(status):\n    return status.get('public_url_200', False) and status.get('sentinel_found', False)\n",
            "公開 URL 200 だけでなく sentinel_found も必須にしてください。",
        ),
        (
            "patch-resume-stage",
            "def resume_stage(state):\n    return 'harvest'\n",
            "def resume_stage(state):\n    return state.get('last_valid_stage') or 'harvest'\n",
            "checkpoint がある場合は harvest から再開しないでください。",
        ),
        (
            "patch-token-gate",
            "def should_continue(error_code, agent_caused):\n    if error_code == 'token_efficiency':\n        return False\n    return not agent_caused\n",
            "def should_continue(error_code, agent_caused):\n    if error_code == 'token_efficiency':\n        return True\n    return not agent_caused\n",
            "token-efficiency 違反は取得経路を縮小する理由であり作業中止理由ではありません。",
        ),
    ]
    for case_id, defect, fixed, instruction in patch_inputs:
        cases.append(
            {
                "task_id": "NG-PATCH",
                "case_id": case_id,
                "schema": patch_schema,
                "input": f"{instruction}\n対象ファイル ng_patch/target.py:\n```python\n{defect}```",
                "oracle": {"defect": defect, "fixed": fixed},
            }
        )

    code_inputs = [
        {
            "case_id": "code-publish-status-contract",
            "target_file": "newsgrasp_gate.py",
            "initial_source": (
                "def publish_complete(status):\n"
                "    return bool(status.get('public_url_200'))\n"
            ),
            "fixed_source": (
                "def publish_complete(status):\n"
                "    return bool(status.get('public_url_200')) and bool(status.get('sentinel_found')) and status.get('distribution_state') == 'published_ok'\n"
            ),
            "test_file": "tests/test_newsgrasp_gate.py",
            "test_source": (
                "from newsgrasp_gate import publish_complete\n\n"
                "def test_publish_complete_requires_all_public_evidence():\n"
                "    assert publish_complete({'public_url_200': True, 'sentinel_found': True, 'distribution_state': 'published_ok'}) is True\n"
                "    assert publish_complete({'public_url_200': True, 'sentinel_found': False, 'distribution_state': 'published_ok'}) is False\n"
                "    assert publish_complete({'public_url_200': True, 'sentinel_found': True, 'distribution_state': 'draft'}) is False\n"
            ),
            "instruction": "公開完了判定は URL 200 だけでは不可。sentinel と distribution_state も必要。",
        },
        {
            "case_id": "code-repair-ledger-order",
            "target_file": "repair_ledger.py",
            "initial_source": (
                "def next_issue(issues):\n"
                "    open_issues = [issue for issue in issues if issue.get('status') != 'done']\n"
                "    return sorted(open_issues, key=lambda issue: issue.get('priority', 0))[0]\n"
            ),
            "fixed_source": (
                "def next_issue(issues):\n"
                "    for issue in issues:\n"
                "        if issue.get('status') != 'done':\n"
                "            return issue\n"
                "    return None\n"
            ),
            "test_file": "tests/test_repair_ledger.py",
            "test_source": (
                "from repair_ledger import next_issue\n\n"
                "def test_next_issue_preserves_runner_order_not_priority_sort():\n"
                "    issues = [\n"
                "        {'id': 'done-a', 'status': 'done', 'priority': 0},\n"
                "        {'id': 'runner-stop', 'status': 'open', 'priority': 5},\n"
                "        {'id': 'nice-to-have', 'status': 'open', 'priority': 1},\n"
                "    ]\n"
                "    assert next_issue(issues)['id'] == 'runner-stop'\n"
                "    assert next_issue([{'id': 'done', 'status': 'done'}]) is None\n"
            ),
            "instruction": "復旧 ledger は優先度ソートではなく runner が出した順に最初の未完了 issue を返す。",
        },
        {
            "case_id": "code-category-schedule",
            "target_file": "category_schedule.py",
            "initial_source": (
                "ALL_CATEGORIES = ['ai', 'economy', 'manufacturing', 'game']\n\n"
                "def categories_for_weekday(weekday):\n"
                "    return list(ALL_CATEGORIES)\n"
            ),
            "fixed_source": (
                "ALL_CATEGORIES = ['ai', 'economy', 'manufacturing', 'game']\n\n"
                "def categories_for_weekday(weekday):\n"
                "    categories = list(ALL_CATEGORIES)\n"
                "    if weekday == 'saturday':\n"
                "        return [item for item in categories if item not in {'economy', 'manufacturing'}]\n"
                "    if weekday == 'monday':\n"
                "        return [item for item in categories if item != 'game']\n"
                "    return categories\n"
            ),
            "test_file": "tests/test_category_schedule.py",
            "test_source": (
                "from category_schedule import categories_for_weekday\n\n"
                "def test_weekday_specific_category_exclusions():\n"
                "    assert categories_for_weekday('saturday') == ['ai', 'game']\n"
                "    assert categories_for_weekday('monday') == ['ai', 'economy', 'manufacturing']\n"
                "    assert categories_for_weekday('wednesday') == ['ai', 'economy', 'manufacturing', 'game']\n"
            ),
            "instruction": "曜日別カテゴリ除外を実装する。土曜は economy/manufacturing を除外、月曜は game を除外。",
        },
    ]
    for item in code_inputs:
        cases.append(
            {
                "task_id": "NG-CODE",
                "case_id": item["case_id"],
                "schema": code_schema,
                "input": (
                    f"{item['instruction']}\n"
                    f"作業ディレクトリ sandbox で {item['target_file']} だけを修正し、tests/** は変更禁止。\n"
                    "検証コマンド: python -m pytest tests -q"
                ),
                "oracle": item,
            }
        )

    long_inputs = [
        (
            "long-gate-excuse",
            "events: t1 runner stopped at daily_quality; t2 agent added broad rerun and hit token-efficiency warning; t3 no external auth/safety blocker; t4 public verifier not run. 判定: gate を言い訳に止めず、取得経路を縮小して same gate へ戻る必要がある。",
            "same_gate_reverify_needed",
        ),
        (
            "long-typed-external",
            "events: t1 podcast verifier hits auth_doctor exit=71 oauth_consent_required; t2 web/audio Green; t3 playlist not verified. 判定: typed external blocker であり publish_complete ではない。",
            "typed_external_blocked_not_complete",
        ),
    ]
    for case_id, text, terminal in long_inputs:
        cases.append(
            {
                "task_id": "NG-LONG",
                "case_id": case_id,
                "schema": long_schema,
                "input": text,
                "oracle": {"terminal_state": terminal},
            }
        )

    ops_inputs = [
        (
            "ops-clean",
            "usage: messages=8, limit_hit=false, fallback=false, resume=true, final_evidence=true",
            {"limit_hit": False, "fallback_occurred": False, "resume_successful": True},
        ),
        (
            "ops-fallback-reported",
            "usage: messages=21, limit_hit=true, fallback=true, fallback was explicitly reported, resume=true, final_evidence=true",
            {"limit_hit": True, "fallback_occurred": True, "resume_successful": True},
        ),
        (
            "ops-resume-failure",
            "usage: messages=18, limit_hit=false, fallback=false, resume=false, final_evidence=false",
            {"limit_hit": False, "fallback_occurred": False, "resume_successful": False},
        ),
    ]
    for case_id, text, oracle in ops_inputs:
        cases.append(
            {
                "task_id": "NG-OPS",
                "case_id": case_id,
                "schema": ops_schema,
                "input": text,
                "oracle": oracle,
            }
        )
    return cases


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def _norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").lower()).strip()


def _contains_all(text: Any, tokens: Iterable[str]) -> bool:
    normalized = _norm(text)
    return all(token.lower() in normalized for token in tokens)


def estimate_codex_credits(model: str, usage: dict[str, int | float]) -> float:
    rates = CREDIT_RATES_PER_MILLION[model]
    input_tokens = int(usage.get("input_tokens", 0) or 0)
    cached_tokens = min(input_tokens, int(usage.get("cached_input_tokens", 0) or 0))
    output_tokens = int(usage.get("output_tokens", 0) or 0)
    uncached_tokens = input_tokens - cached_tokens
    credits = (
        uncached_tokens * rates["input"]
        + cached_tokens * rates["cached_input"]
        + output_tokens * rates["output"]
    ) / 1_000_000
    return round(credits, 6)


def compute_primary_metrics(records: list[dict[str, Any]]) -> dict[str, float]:
    rc_cases = [row for row in records if row.get("task_id") == "NG-RC"]
    mf_cases = [row for row in records if row.get("task_id") in {"NG-MF", "NG-PATCH"}]
    vc_cases = [row for row in records if row.get("task_id") in {"NG-PATCH", "NG-LONG"}]
    os_cases = [row for row in records if row.get("task_id") in {"NG-LONG", "NG-OPS"}]
    code_cases = [row for row in records if row.get("task_id") == "NG-CODE"]

    rca = _ratio(sum(bool(row.get("root_cause_correct")) for row in rc_cases), len(rc_cases))
    mfr = _ratio(sum(bool(row.get("minimal_fix")) for row in mf_cases), len(mf_cases))
    vcr = _ratio(sum(bool(row.get("verified_closure")) for row in vc_cases), len(vc_cases))
    ocr = 1.0 - _ratio(sum(bool(row.get("false_or_overclaim")) for row in records), len(records))
    osr = _ratio(sum(bool(row.get("ops_stable")) for row in os_cases), len(os_cases))
    composite = statistics.mean([rca, mfr, vcr, ocr, osr])
    coding_pass_rate = _ratio(sum(bool(row.get("coding_pass")) for row in code_cases), len(code_cases))
    composite_with_coding = statistics.mean([composite, coding_pass_rate]) if code_cases else composite
    fatal_rate = _ratio(sum(bool(row.get("fatal")) for row in records), len(records))
    closure_stability = statistics.mean([vcr, osr])
    verified_closure_cases = sum(bool(row.get("verified_closure")) for row in records)
    total_credits = sum(float(row.get("credits", 0.0) or 0.0) for row in records)
    cost_per_closure = total_credits / max(1, verified_closure_cases)

    return {
        "RCA": round(rca, 6),
        "MFR": round(mfr, 6),
        "VCR": round(vcr, 6),
        "OCR": round(ocr, 6),
        "OSR": round(osr, 6),
        "Composite": round(composite, 6),
        "CodingPassRate": round(coding_pass_rate, 6),
        "CompositeWithCoding": round(composite_with_coding, 6),
        "FatalRate": round(fatal_rate, 6),
        "ClosureStability": round(closure_stability, 6),
        "CostPerClosure": round(cost_per_closure, 6),
    }


def summarize_by_model(records: Iterable[dict[str, Any]]) -> dict[str, dict[str, float]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in records:
        grouped.setdefault(str(row["model"]), []).append(row)
    return {model: compute_primary_metrics(rows) for model, rows in sorted(grouped.items())}


def summarize_by_model_effort(records: Iterable[dict[str, Any]]) -> dict[str, dict[str, dict[str, float]]]:
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for row in records:
        model = str(row["model"])
        effort = str(row.get("effort") or "unspecified")
        grouped.setdefault(model, {}).setdefault(effort, []).append(row)
    return {
        model: {effort: compute_primary_metrics(rows) for effort, rows in sorted(efforts.items())}
        for model, efforts in sorted(grouped.items())
    }


def classify_terra_vs_gpt54(summary: dict[str, dict[str, float]]) -> str:
    terra = summary["gpt-5.6-terra"]
    baseline = summary["gpt-5.4"]
    axis_deltas = {axis: terra[axis] - baseline[axis] for axis in PRIMARY_AXES}
    composite_delta = terra["Composite"] - baseline["Composite"]
    within_or_worse = sum(1 for delta in axis_deltas.values() if delta <= 0.10)
    closure_large_gain = axis_deltas["VCR"] >= 0.15 or axis_deltas["OSR"] >= 0.15
    if abs(composite_delta) <= 0.05 and within_or_worse >= 3 and not closure_large_gain:
        return "terra_similar_to_gpt54"
    if composite_delta <= -0.05 and not closure_large_gain:
        return "terra_worse_than_gpt54"

    large_axis_wins = sum(1 for delta in axis_deltas.values() if delta >= 0.15)
    if (
        composite_delta >= 0.10
        and large_axis_wins >= 2
        and terra["FatalRate"] <= baseline["FatalRate"]
        and terra["OCR"] >= baseline["OCR"]
    ):
        return "terra_advantage"
    return "inconclusive"


def classify_gpt55_vs_terra(summary: dict[str, dict[str, float]]) -> str:
    gpt55 = summary["gpt-5.5"]
    terra = summary["gpt-5.6-terra"]
    if (
        gpt55["Composite"] - terra["Composite"] >= 0.12
        and gpt55["ClosureStability"] - terra["ClosureStability"] >= 0.10
        and gpt55["FatalRate"] <= terra["FatalRate"]
        and gpt55["CostPerClosure"] <= terra["CostPerClosure"] * 1.5
    ):
        return "gpt55_primary_long_running_candidate"
    return "gpt55_review_or_root_cause_candidate"


def write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    last_error: OSError | None = None
    for attempt in range(5):
        tmp_path = path.with_name(f".{path.name}.{os.getpid()}.{attempt}.tmp")
        try:
            tmp_path.write_text(text, encoding="utf-8")
            tmp_path.replace(path)
            return path
        except OSError as exc:
            last_error = exc
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass
            time.sleep(0.2 * (attempt + 1))
    if last_error is not None:
        raise last_error
    return path


def write_run_artifacts(run_dir: Path, record: dict[str, Any]) -> list[Path]:
    run_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    events = record.get("events", [])
    events_path = run_dir / "events.jsonl"
    events_path.write_text(
        "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events),
        encoding="utf-8",
    )
    paths.append(events_path)
    paths.append(_write_text(run_dir / "stderr.log", str(record.get("stderr", ""))))
    paths.append(_write_text(run_dir / "pytest.log", str(record.get("pytest", {}).get("log", ""))))
    paths.append(_write_text(run_dir / "validator.log", str(record.get("validator", {}).get("log", ""))))
    paths.append(_write_text(run_dir / "verifier.log", str(record.get("public_verifier", {}).get("log", ""))))
    paths.append(write_json(run_dir / "usage.json", record.get("usage", {})))
    paths.append(write_json(run_dir / "limit.json", record.get("limit", {})))
    paths.append(write_json(run_dir / "fallback.json", record.get("fallback", {})))
    paths.append(write_json(run_dir / "resume.json", record.get("resume", {})))
    paths.append(_write_text(run_dir / "raw_answer.txt", str(record.get("raw_answer", ""))))
    paths.append(write_json(run_dir / "run.json", _summarize_run_record(record)))
    return paths


def _write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _summarize_run_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "model": record.get("model"),
        "effort": record.get("effort"),
        "task_id": record.get("task_id"),
        "case_id": record.get("case_id"),
        "pytest_exit_code": record.get("pytest", {}).get("exit_code"),
        "validator_exit_code": record.get("validator", {}).get("exit_code"),
        "public_verifier_exit_code": record.get("public_verifier", {}).get("exit_code"),
        "limit_hit": record.get("limit", {}).get("hit"),
        "fallback_occurred": record.get("fallback", {}).get("occurred"),
        "resume_successful": record.get("resume", {}).get("successful"),
        "credits": record.get("credits"),
        "messages": record.get("usage", {}).get("messages"),
    }


def _filter_cases(cases: list[dict[str, Any]], task_filter: Iterable[str] | None) -> list[dict[str, Any]]:
    selected = [str(item) for item in (task_filter or [])]
    if not selected:
        return cases
    selected_set = set(selected)
    unknown = selected_set - set(TASK_SET)
    if unknown:
        raise ValueError(f"unknown task_filter: {', '.join(sorted(unknown))}")
    return [case for case in cases if case["task_id"] in selected_set]


def build_manifest(task_filter: Iterable[str] | None = None) -> dict[str, Any]:
    selected_task_ids = [task_id for task_id in TASK_SET if not task_filter or task_id in set(task_filter)]
    selected_minimum_cases = {task_id: MINIMUM_CASES[task_id] for task_id in selected_task_ids}
    return {
        "schema_version": "codex_recovery_benchmark.v1",
        "target_models": list(TARGET_MODELS),
        "target_efforts": list(EFFORT_LEVELS),
        "selected_task_ids": selected_task_ids,
        "minimum_cases": selected_minimum_cases,
        "minimum_cases_per_model": sum(selected_minimum_cases.values()),
        "minimum_cases_per_model_effort": sum(selected_minimum_cases.values()),
        "task_set": TASK_SET,
        "required_telemetry_artifacts": list(REQUIRED_TELEMETRY_ARTIFACTS),
        "primary_axes": list(PRIMARY_AXES),
        "separate_axes": ["CodingPassRate"],
        "decision_rules": {
            "terra_similar_to_gpt54": "composite delta <= ±0.05, at least 3 primary axes within +0.10 or worse, and no VCR/OSR +0.15 gain",
            "terra_advantage": "composite delta >= +0.10, at least 2 axes +0.15, fatal rate not worse, OCR not worse",
            "gpt55_primary": "GPT-5.5 composite +0.12, closure stability +0.10, fatal rate not worse, cost per closure <= Terra * 1.5",
        },
        "official_source_boundary": "cost only; performance must be judged by task outcomes",
    }


def load_score_records(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("records"), list):
        return payload["records"]
    raise ValueError("score file must be a list or an object with records[]")


def write_summary(out_dir: Path, records: list[dict[str, Any]]) -> dict[str, Any]:
    summary = summarize_by_model(records)
    effort_summary = summarize_by_model_effort(records)
    result: dict[str, Any] = {
        "schema_version": "codex_recovery_benchmark_summary.v1",
        "models": summary,
        "model_efforts": effort_summary,
        "target_efforts": list(EFFORT_LEVELS),
        "measurement_limits": {
            "coding_axis": "NG-CODE measures small sandbox repo repair, not full production News-Grasp mutation",
            "recovery_axis": "NG-PATCH uses controlled replacement fixtures; NG-LONG is staged reasoning unless --task-filter selects live coding only",
        },
    }
    if "gpt-5.6-terra" in summary and "gpt-5.4" in summary:
        result["terra_vs_gpt54"] = classify_terra_vs_gpt54(summary)
    if "gpt-5.5" in summary and "gpt-5.6-terra" in summary:
        result["gpt55_vs_terra"] = classify_gpt55_vs_terra(summary)
    write_json(out_dir / "summary.json", result)
    return result


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _fmt_metric(value: Any) -> str:
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return "n/a"


def _model_rows(summary: dict[str, Any], coding: dict[str, Any]) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    models = [model for model in TARGET_MODELS if model in summary.get("models", {}) or model in coding.get("models", {})]
    extras = sorted((set(summary.get("models", {})) | set(coding.get("models", {}))) - set(models))
    return [(model, summary.get("models", {}).get(model, {}), coding.get("models", {}).get(model, {})) for model in models + extras]


def _effort_rows(summary: dict[str, Any], coding: dict[str, Any]) -> list[tuple[str, str, dict[str, Any], dict[str, Any]]]:
    rows: list[tuple[str, str, dict[str, Any], dict[str, Any]]] = []
    recovery_efforts = summary.get("model_efforts", {})
    coding_efforts = coding.get("model_efforts", {})
    models = [model for model in TARGET_MODELS if model in recovery_efforts or model in coding_efforts]
    models.extend(sorted((set(recovery_efforts) | set(coding_efforts)) - set(models)))
    for model in models:
        effort_names = [effort for effort in EFFORT_LEVELS if effort in recovery_efforts.get(model, {}) or effort in coding_efforts.get(model, {})]
        effort_names.extend(sorted((set(recovery_efforts.get(model, {})) | set(coding_efforts.get(model, {}))) - set(effort_names)))
        for effort in effort_names:
            rows.append((model, effort, recovery_efforts.get(model, {}).get(effort, {}), coding_efforts.get(model, {}).get(effort, {})))
    return rows


def generate_html_report(*, recovery_path: Path, coding_summary_path: Path, output_path: Path) -> Path:
    recovery = _load_json(recovery_path)
    coding = _load_json(coding_summary_path)
    rows = _model_rows(recovery, coding)
    effort_rows = _effort_rows(recovery, coding)
    verdict_code = str(recovery.get("terra_vs_gpt54", "inconclusive"))
    verdict_text = {
        "terra_worse_than_gpt54": "Terra は GPT-5.4 を下回る",
        "terra_similar_to_gpt54": "Terra は GPT-5.4 程度",
        "terra_advantage": "Terra は GPT-5.4 より優位",
    }.get(verdict_code, "Terra と GPT-5.4 の差は追加検証が必要")
    best_coding = max(rows, key=lambda row: float(row[2].get("CodingPassRate", 0.0) or 0.0))[0] if rows else "n/a"
    model_cards = "\n".join(
        (
            f"<tr><th>{html.escape(model)}</th>"
            f"<td>{_fmt_metric(recovery_metrics.get('Composite'))}</td>"
            f"<td>{_fmt_metric(coding_metrics.get('CodingPassRate'))}</td>"
            f"<td>{_fmt_metric(recovery_metrics.get('FatalRate'))}</td>"
            f"<td>{_fmt_metric(recovery_metrics.get('CostPerClosure'))}</td></tr>"
        )
        for model, recovery_metrics, coding_metrics in rows
    )
    score_rows = "\n".join(
        (
            f"<div class=\"bar-row\"><span>{html.escape(model)}</span>"
            f"<div class=\"bar-track\"><i style=\"width:{max(4.0, float(metrics.get('Composite', 0.0) or 0.0) * 100):.1f}%\"></i></div>"
            f"<b>{_fmt_metric(metrics.get('Composite'))}</b></div>"
        )
        for model, metrics, _ in rows
    )
    coding_rows = "\n".join(
        (
            f"<div class=\"bar-row\"><span>{html.escape(model)}</span>"
            f"<div class=\"bar-track coding\"><i style=\"width:{max(4.0, float(metrics.get('CodingPassRate', 0.0) or 0.0) * 100):.1f}%\"></i></div>"
            f"<b>{_fmt_metric(metrics.get('CodingPassRate'))}</b></div>"
        )
        for model, _, metrics in rows
    )
    effort_table_rows = "\n".join(
        (
            f"<tr><td>{html.escape(model)}</td><td>{html.escape(effort)}</td>"
            f"<td>{_fmt_metric(recovery_metrics.get('Composite'))}</td>"
            f"<td>{_fmt_metric(coding_metrics.get('CodingPassRate'))}</td>"
            f"<td>{_fmt_metric(recovery_metrics.get('FatalRate'))}</td></tr>"
        )
        for model, effort, recovery_metrics, coding_metrics in effort_rows
    ) or "<tr><td colspan=\"5\">effort 別 summary は未生成</td></tr>"
    html_text = f"""<!doctype html>
<html lang="ja" data-label-mode="symbol">
<head>
<meta charset="utf-8">
<title>GPT Codex Recovery and Coding Benchmark Report</title>
<style>
body {{ margin:0; background:#eef0f3; color:#1a1d24; font-family:Arial, 'Noto Sans JP', sans-serif; }}
main {{ max-width:1200px; margin:0 auto; padding:32px; }}
section {{ padding:42px 0; border-bottom:1px solid #dfe3e8; }}
.kicker {{ font-family:Consolas, monospace; font-size:12px; color:#647084; font-weight:700; letter-spacing:0; }}
h1 {{ font-size:42px; margin:10px 0 18px; letter-spacing:0; }}
h2 {{ font-size:28px; margin:8px 0 16px; letter-spacing:0; }}
.verdict-grid, .winner-grid {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(220px, 1fr)); gap:14px; }}
.verdict, .card {{ background:white; border:1px solid #e4e7ec; border-radius:12px; padding:18px; }}
.verdict b {{ display:block; font-size:22px; margin-bottom:8px; }}
table {{ width:100%; border-collapse:collapse; background:white; border:1px solid #e4e7ec; }}
th, td {{ padding:12px 14px; border-bottom:1px solid #eef0f3; text-align:left; }}
.score-explorer {{ background:white; border:1px solid #e4e7ec; border-radius:12px; padding:20px; }}
.bar-row {{ display:grid; grid-template-columns:180px 1fr 72px; gap:12px; align-items:center; margin:12px 0; }}
.bar-track {{ height:22px; background:#f1f3f5; border-radius:8px; overflow:hidden; }}
.bar-track i {{ display:block; height:100%; background:oklch(0.60 0.13 255); }}
.bar-track.coding i {{ background:oklch(0.58 0.13 195); }}
.limit {{ background:#1a1d24; color:white; border-radius:12px; padding:20px; }}
.audit {{ background:#f7f8fa; border:1px solid #e4e7ec; border-radius:12px; padding:18px; }}
code {{ font-family:Consolas, monospace; }}
</style>
</head>
<body>
<main>
<section data-report-layer="decision">
<div class="kicker">00 — Decision Brief</div>
<h1>GPT Codex Recovery and Coding Benchmark</h1>
<div class="verdict-grid">
  <div class="verdict"><b>{html.escape(verdict_text)}</b><span>既存 recovery benchmark の判定: <code>{html.escape(verdict_code)}</code></span></div>
  <div class="verdict"><b>Coding winner: {html.escape(best_coding)}</b><span>NG-CODE は実 source edit + pytest Green を別軸で測定。</span></div>
  <div class="verdict"><b>性能と費用は分離</b><span>credit tier は運用情報であり、能力結論には使わない。</span></div>
</div>
</section>
<section>
<div class="kicker">01 — Decision Matrix</div>
<h2>Decision Matrix</h2>
<table><thead><tr><th>Model</th><th>Recovery Composite</th><th>NG-CODE CodingPassRate</th><th>FatalRate</th><th>CostPerClosure</th></tr></thead><tbody>{model_cards}</tbody></table>
</section>
<section data-report-layer="evidence">
<div class="kicker">02 — Score Explorer</div>
<h2>Score Explorer</h2>
<div class="score-explorer" data-report-primary="true">
<p><strong>Recovery Composite</strong> / baseline = metric minimum</p>
{score_rows}
<p><strong>NG-CODE CodingPassRate</strong> / baseline = metric minimum</p>
{coding_rows}
</div>
</section>
<section data-report-section="score-method">
<div class="kicker">02.5 — Score Method</div>
<h2>Score Method</h2>
<p>品質は RCA / MFR / VCR / OCR / OSR の回復能力と NG-CODE の実コード修正結果で分けて読む。安定性は ClosureStability、FatalRate、fallback/resume を見る。形式制御は JSON-only・false claim・過剰 claim の有無で減点する。速度は elapsed_sec と timeout を補助指標として扱う。VRAM はクラウド Codex 比較では採点対象外で、ローカル LLM 方式との表示互換のために境界を明示する。日本語品質は外部 benchmark matrix 側の JA_NLU / summary 軸で扱い、最終判断の重みは recovery / coding / ops を分離して意思決定者が読めるようにする。</p>
</section>
<section>
<div class="kicker">02.6 — Effort Level Slice</div>
<h2>Effort Level Slice</h2>
<p>同じ model でも <code>model_reasoning_effort</code> の違いで recovery / coding を別集計する。model 平均へ混ぜる前の分散確認に使う。</p>
<table><thead><tr><th>Model</th><th>Effort</th><th>Recovery Composite</th><th>NG-CODE CodingPassRate</th><th>FatalRate</th></tr></thead><tbody>{effort_table_rows}</tbody></table>
</section>
<section>
<div class="kicker">03 — Usecase Winners</div>
<h2>Usecase Winners</h2>
<div class="winner-grid">
  <div class="card"><b>復旧ログ読解</b><p>既存 recovery summary の RCA / Composite を優先。</p></div>
  <div class="card"><b>実コード修正</b><p>NG-CODE の CodingPassRate を優先。勝者: {html.escape(best_coding)}</p></div>
  <div class="card"><b>長時間運用</b><p>ClosureStability、FatalRate、fallback/resume を分離して読む。</p></div>
</div>
</section>
<section>
<div class="kicker">04 — Operational Gate</div>
<h2>Operational Gate</h2>
<p>CostPerClosure と制限/fallback は品質点へ加算しない。長時間主力化は ClosureStability と CostPerClosure を同時に見る。</p>
</section>
<section>
<div class="limit"><strong>Measurement Limit</strong><p>NG-CODE measures small sandbox repo repair, not full production News-Grasp mutation. 既存 recovery benchmark と coding benchmark は統合表示するが、能力軸は混ぜない。</p></div>
</section>
<section>
<div class="kicker">05 — Evaluation Design</div>
<h2>Evaluation Design</h2>
<p>NG-CODE の fatal gates: source unchanged, test mutation, pytest failed, false_pass_claim. NG-PATCH は controlled replacement fixture のため coding 代理指標にはしない。</p>
</section>
<section>
<div class="kicker">06 — Case Library</div>
<h2>Case Library</h2>
<p><code>NG-RC</code>, <code>NG-MF</code>, <code>NG-PATCH</code>, <code>NG-LONG</code>, <code>NG-OPS</code>, <code>NG-CODE</code></p>
</section>
<section data-report-layer="audit">
<div class="kicker">07 — Audits</div>
<h2>Audits</h2>
<div class="audit">Coding Red tests reviewed by Codex gpt-5.5: reviewer_verdict=approve, blockers=[]。HTML report contract: decision-first order, evidence second, audit last, no external CDN.</div>
</section>
</main>
</body>
</html>
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_text, encoding="utf-8")
    return output_path


def _kill_process_tree(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
    else:
        proc.kill()


def _run_codex_exec_with_output_gate(
    *,
    args: list[str],
    prompt: str,
    output_path: Path,
    stdout_path: Path,
    stderr_path: Path,
    timeout_sec: int,
    output_stable_sec: int,
    env: dict[str, str],
) -> tuple[int, bool, bool, float]:
    start = time.monotonic()
    timed_out = False
    killed_after_output = False
    stable_since: float | None = None
    last_size = -1
    sleep_sec = 0.1 if output_stable_sec <= 0 else min(0.5, max(0.1, output_stable_sec / 4))

    with stdout_path.open("w", encoding="utf-8", errors="replace") as stdout_file, stderr_path.open(
        "w", encoding="utf-8", errors="replace"
    ) as stderr_file:
        proc = popen_model_process(
            args,
            route="codex_recovery_benchmark",
            stdin=subprocess.PIPE,
            stdout=stdout_file,
            stderr=stderr_file,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            env=env,
        )
        if proc.stdin is not None:
            proc.stdin.write(prompt)
            proc.stdin.close()
        while True:
            return_code = proc.poll()
            now = time.monotonic()
            if return_code is not None:
                break
            if now - start >= timeout_sec:
                timed_out = True
                _kill_process_tree(proc)
                return_code = 124
                break
            if output_path.exists():
                current_size = output_path.stat().st_size
                if current_size > 0:
                    if current_size != last_size:
                        last_size = current_size
                        stable_since = now
                    elif stable_since is not None and now - stable_since >= output_stable_sec:
                        killed_after_output = True
                        _kill_process_tree(proc)
                        return_code = 0
                        break
            time.sleep(sleep_sec)
        try:
            proc.wait(timeout=5)
        except Exception:
            pass

    elapsed_sec = round(time.monotonic() - start, 3)
    return int(return_code or 0), timed_out, killed_after_output, elapsed_sec


def run_codex_case(
    *,
    codex_bin: str,
    model: str,
    effort: str,
    case: dict[str, Any],
    run_dir: Path,
    timeout_sec: int,
    output_stable_sec: int,
) -> dict[str, Any]:
    run_dir.mkdir(parents=True, exist_ok=True)
    work_dir = run_dir
    if case["task_id"] == "NG-CODE":
        work_dir = prepare_code_sandbox(case, run_dir)
        prompt = _code_prompt(case)
    else:
        prompt = _base_prompt(case)
    output_path = run_dir / "raw_answer.txt"
    stdout_path = run_dir / "stdout.jsonl"
    stderr_path = run_dir / "stderr.log"
    args = [
        codex_bin,
        "exec",
        "-m",
        model,
        "-c",
        f'model_reasoning_effort="{effort}"',
        "--skip-git-repo-check",
        "-C",
        str(work_dir),
        "-o",
        str(output_path),
        "-",
    ]
    env = os.environ.copy()
    env["CODEX_NONINTERACTIVE_SESSION"] = "1"
    env["CODEX_OUTPUT_CONTRACT"] = "artifact-gate"
    env["PYTHONIOENCODING"] = "utf-8"
    return_code, timed_out, killed_after_output, elapsed_sec = _run_codex_exec_with_output_gate(
        args=args,
        prompt=prompt,
        output_path=output_path,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        timeout_sec=timeout_sec,
        output_stable_sec=output_stable_sec,
        env=env,
    )
    raw_answer = output_path.read_text(encoding="utf-8", errors="replace") if output_path.exists() else ""
    record = {
        "model": model,
        "effort": effort,
        "task_id": case["task_id"],
        "case_id": case["case_id"],
        "events": [
            {
                "type": "codex_exec",
                "elapsed_sec": elapsed_sec,
                "timeout_sec": timeout_sec,
                "timed_out": timed_out,
                "killed_after_output": killed_after_output,
                "exit_code": return_code,
            }
        ],
        "stderr": stderr_path.read_text(encoding="utf-8", errors="replace") if stderr_path.exists() else "",
        "raw_answer": raw_answer,
        "usage": estimate_usage(prompt, raw_answer),
        "limit": {"hit": False, "source": "codex_exec_exit"},
        "fallback": {"occurred": False, "source": "no_cli_fallback_signal"},
        "resume": {"successful": True, "source": "single_exec_case"},
    }
    record["credits"] = estimate_codex_credits(model, record["usage"])
    score_case_record(record, case, run_dir)
    write_run_artifacts(run_dir, record)
    return record


def estimate_usage(prompt: str, answer: str) -> dict[str, Any]:
    input_tokens = max(1, round(len(prompt) / 4))
    output_tokens = max(0, round(len(answer) / 4))
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": 0,
        "output_tokens": output_tokens,
        "messages": 1,
        "estimated": True,
        "source": "char_count_div_4_no_codex_usage_event",
    }


def score_case_record(record: dict[str, Any], case: dict[str, Any], run_dir: Path) -> None:
    task_id = str(case["task_id"])
    if task_id == "NG-CODE":
        record["parsed_answer"] = {}
        record["parse_error"] = ""
        _score_code(record, case, run_dir)
        record.setdefault("root_cause_correct", False)
        record.setdefault("minimal_fix", False)
        record.setdefault("verified_closure", False)
        record.setdefault("false_or_overclaim", False)
        record.setdefault("ops_stable", False)
        record.setdefault("fatal", False)
        return
    try:
        answer = _extract_json_object(str(record.get("raw_answer", "")))
        parse_error = ""
    except Exception as exc:
        answer = {}
        parse_error = str(exc)
    record["parsed_answer"] = answer
    record["parse_error"] = parse_error
    if task_id == "NG-RC":
        _score_rc(record, case, answer)
    elif task_id == "NG-MF":
        _score_mf(record, case, answer)
    elif task_id == "NG-PATCH":
        _score_patch(record, case, answer, run_dir)
    elif task_id == "NG-LONG":
        _score_long(record, case, answer)
    elif task_id == "NG-OPS":
        _score_ops(record, case, answer)
    else:
        record.update({"fatal": True, "false_or_overclaim": True})
    if parse_error:
        record["fatal"] = True
        record["false_or_overclaim"] = True
    record.setdefault("root_cause_correct", False)
    record.setdefault("minimal_fix", False)
    record.setdefault("verified_closure", False)
    record.setdefault("false_or_overclaim", False)
    record.setdefault("ops_stable", False)
    record.setdefault("fatal", False)


def prepare_code_sandbox(case: dict[str, Any], run_dir: Path) -> Path:
    oracle = case["oracle"]
    sandbox = (run_dir / "sandbox").resolve()
    source = sandbox / oracle["target_file"]
    test_file = sandbox / oracle["test_file"]
    source.parent.mkdir(parents=True, exist_ok=True)
    test_file.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(oracle["initial_source"], encoding="utf-8")
    test_file.write_text(oracle["test_source"], encoding="utf-8")
    (sandbox / "pytest.ini").write_text("[pytest]\naddopts =\npython_files = test_*.py\n", encoding="utf-8")
    write_json(
        sandbox / ".benchmark_baseline.json",
        {
            "target_file": oracle["target_file"],
            "test_file": oracle["test_file"],
            "initial_source": oracle["initial_source"],
            "test_source": oracle["test_source"],
        },
    )
    return sandbox


def _score_code(record: dict[str, Any], case: dict[str, Any], run_dir: Path) -> None:
    oracle = case["oracle"]
    sandbox = run_dir / "sandbox"
    if not sandbox.exists():
        sandbox = prepare_code_sandbox(case, run_dir)
    target = sandbox / oracle["target_file"]
    test_file = sandbox / oracle["test_file"]
    current_source = target.read_text(encoding="utf-8") if target.exists() else ""
    current_test = test_file.read_text(encoding="utf-8") if test_file.exists() else ""
    source_changed = current_source != oracle["initial_source"]
    tests_changed = current_test != oracle["test_source"]

    pytest_env = benchmark_subprocess_env(sandbox)
    safety_error = ""
    try:
        validate_benchmark_python(current_source)
    except ValueError as exc:
        safety_error = str(exc)
    proc = subprocess.CompletedProcess([], 125, "", safety_error) if safety_error else run_limited_benchmark_process(
        [os.sys.executable, "-m", "pytest", "tests", "-q"], cwd=sandbox, env=pytest_env
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
    _write_text(run_dir / "git.diff", f"diff -- {oracle['target_file']}\n{diff_text}" if diff_text else "")

    issues: list[str] = []
    if tests_changed:
        issues.append("test_mutation_forbidden")
    if not source_changed:
        issues.append("source_not_modified")
    if proc.returncode != 0:
        issues.append("pytest_failed")
    if safety_error:
        issues.append("unsafe_generated_code")
    raw_answer = _norm(record.get("raw_answer"))
    claimed_pass = any(token in raw_answer for token in ("passed", "pass", "通りました", "成功", "green"))
    false_pass = claimed_pass and (not source_changed or proc.returncode != 0 or tests_changed)
    if false_pass:
        issues.append("false_pass_claim")
    coding_pass = source_changed and not tests_changed and proc.returncode == 0
    record.update(
        {
            "coding_pass": coding_pass,
            "root_cause_correct": coding_pass,
            "minimal_fix": coding_pass,
            "verified_closure": coding_pass,
            "false_or_overclaim": false_pass or tests_changed,
            "ops_stable": True,
            "fatal": not coding_pass or false_pass or tests_changed,
            "validator": {"exit_code": 0 if not issues else 1, "log": "code oracle match" if not issues else ";".join(issues)},
            "pytest": {"exit_code": proc.returncode, "log": pytest_log},
            "public_verifier": {"exit_code": proc.returncode, "log": pytest_log},
        }
    )


def _score_rc(record: dict[str, Any], case: dict[str, Any], answer: dict[str, Any]) -> None:
    oracle = case["oracle"]
    stop = _norm(answer.get("stop_stage"))
    expected_stop = _norm(oracle["stop_stage"])
    if expected_stop == "publish_verify":
        stop_ok = stop in {"publish_verify", "verify_public_surface"}
    elif expected_stop == "daily_quality":
        stop_ok = stop in {"daily_quality", "auto_repair_orchestrator"}
    else:
        stop_ok = stop == expected_stop
    cause = _norm(answer.get("direct_cause"))
    expected_cause = _norm(oracle["direct_cause"])
    if "stale_recovery_proof" in expected_cause:
        cause_ok = "stale_recovery_proof" in cause and "head" in cause and (
            "mismatch" in cause or "does not match" in cause or "不一致" in cause
        )
    elif "unknown_repair_class" in expected_cause:
        cause_ok = "unknown" in cause and "repair" in cause
    elif "oauth_consent_required" in expected_cause:
        cause_ok = "oauth_consent_required" in cause or ("oauth" in cause and ("external" in cause or "外部" in cause))
    else:
        cause_ok = cause == expected_cause
    not_reached = set(answer.get("not_reached_stage") or [])
    root_ok = stop_ok and cause_ok and set(oracle["not_reached_stage"]).issubset(not_reached)
    order = list(answer.get("recovery_order") or [])
    record.update(
        {
            "root_cause_correct": root_ok,
            "minimal_fix": False,
            "verified_closure": False,
            "false_or_overclaim": bool(answer.get("fabricated_state")),
            "ops_stable": True,
            "fatal": (not root_ok) or bool(answer.get("fabricated_state")),
            "validator": {"exit_code": 0 if root_ok and order else 1, "log": "rc oracle match" if root_ok else "rc oracle mismatch"},
            "pytest": {"exit_code": None, "log": "not_applicable: triage task"},
            "public_verifier": {"exit_code": None, "log": "not_applicable: triage task"},
        }
    )


def _score_mf(record: dict[str, Any], case: dict[str, Any], answer: dict[str, Any]) -> None:
    oracle = case["oracle"]
    touched = list(answer.get("touched_files") or [])
    allowed = list(oracle["allowed_files"])
    touched_ok = bool(touched) and all(any(item.startswith(prefix) for prefix in allowed) for item in touched)
    cause_text = _norm(answer.get("root_cause"))
    expected = _norm(oracle["root_cause"])
    cause_ok = (
        cause_text == expected
        or all(token in cause_text for token in expected.split("_") if len(token) >= 5)
    )
    verify_ok = "pytest" in str(answer.get("verification_command", "")).lower()
    minimal = bool(answer.get("minimal_fix")) and not bool(answer.get("broad_rewrite")) and touched_ok
    ok = cause_ok and verify_ok and minimal
    record.update(
        {
            "root_cause_correct": cause_ok,
            "minimal_fix": minimal,
            "verified_closure": False,
            "false_or_overclaim": bool(answer.get("broad_rewrite")) or not verify_ok,
            "ops_stable": True,
            "fatal": not ok,
            "validator": {"exit_code": 0 if ok else 1, "log": "minimal fix oracle match" if ok else "minimal fix oracle mismatch"},
            "pytest": {"exit_code": None, "log": "not_applicable: plan task"},
            "public_verifier": {"exit_code": None, "log": "not_applicable: plan task"},
        }
    )


def _score_patch(record: dict[str, Any], case: dict[str, Any], answer: dict[str, Any], run_dir: Path) -> None:
    sandbox = (run_dir / "sandbox").resolve()
    target = sandbox / "ng_patch" / "target.py"
    tests = sandbox / "tests" / "test_target.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    tests.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(case["oracle"]["defect"], encoding="utf-8")
    tests.write_text(_patch_test_source(case["case_id"]), encoding="utf-8")
    pytest_config = sandbox / "pytest.ini"
    pytest_config.write_text("[pytest]\naddopts =\npython_files = test_*.py\n", encoding="utf-8")
    applied = False
    if answer.get("file_path") == "ng_patch/target.py":
        find = str(answer.get("find", ""))
        replace = str(answer.get("replace", ""))
        text = target.read_text(encoding="utf-8")
        if find and find in text:
            target.write_text(text.replace(find, replace, 1), encoding="utf-8")
            applied = True
    pytest_env = benchmark_subprocess_env(sandbox)
    safety_error = ""
    try:
        validate_benchmark_python(target.read_text(encoding="utf-8"))
    except ValueError as exc:
        safety_error = str(exc)
    proc = subprocess.CompletedProcess([], 125, "", safety_error) if safety_error else run_limited_benchmark_process(
        [os.sys.executable, "-m", "pytest", "-c", str(pytest_config), "--rootdir", str(sandbox), str(tests), "-q"],
        cwd=sandbox, env=pytest_env,
    )
    pytest_log = (proc.stdout or "") + (proc.stderr or "")
    verified = applied and proc.returncode == 0
    minimal = applied and target.read_text(encoding="utf-8") == case["oracle"]["fixed"]
    false_pass = bool(answer.get("false_pass_claim")) or ("pytest" not in str(answer.get("verification_command", "")).lower())
    validator_issues: list[str] = []
    if not minimal:
        validator_issues.append("patch_not_minimal_or_not_applied")
    if bool(answer.get("false_pass_claim")):
        validator_issues.append("false_pass_claim")
    if safety_error:
        validator_issues.append("unsafe_generated_code")
    record.update(
        {
            "root_cause_correct": applied,
            "minimal_fix": minimal,
            "verified_closure": verified,
            "false_or_overclaim": false_pass,
            "ops_stable": True,
            "fatal": (not verified) or false_pass,
            "validator": {"exit_code": 0 if not validator_issues else 1, "log": "minimal exact replacement" if not validator_issues else ";".join(validator_issues)},
            "pytest": {"exit_code": proc.returncode, "log": pytest_log},
            "public_verifier": {"exit_code": proc.returncode, "log": pytest_log},
        }
    )


def _patch_test_source(case_id: str) -> str:
    if case_id == "patch-public-sentinel":
        return (
            "from ng_patch.target import publish_complete\n\n"
            "def test_requires_public_url_and_sentinel():\n"
            "    assert publish_complete({'public_url_200': True, 'sentinel_found': True}) is True\n"
            "    assert publish_complete({'public_url_200': True, 'sentinel_found': False}) is False\n"
        )
    if case_id == "patch-resume-stage":
        return (
            "from ng_patch.target import resume_stage\n\n"
            "def test_resume_from_last_valid_stage():\n"
            "    assert resume_stage({'last_valid_stage': 'publish_verify'}) == 'publish_verify'\n"
            "    assert resume_stage({}) == 'harvest'\n"
        )
    return (
        "from ng_patch.target import should_continue\n\n"
        "def test_token_efficiency_switches_path_not_task():\n"
        "    assert should_continue('token_efficiency', True) is True\n"
        "    assert should_continue('security', True) is False\n"
    )


def _score_long(record: dict[str, Any], case: dict[str, Any], answer: dict[str, Any]) -> None:
    terminal = _norm(answer.get("terminal_state"))
    expected = _norm(case["oracle"]["terminal_state"])
    if expected == "same_gate_reverify_needed":
        terminal_ok = (
            terminal == expected
            or "same" in _norm(answer.get("next_recovery_step"))
            or "同じ" in str(answer.get("next_recovery_step", ""))
            or "同一" in str(answer.get("next_recovery_step", ""))
        )
    elif expected == "typed_external_blocked_not_complete":
        terminal_ok = (
            terminal == expected
            or "external" in terminal
            or "not_complete" in terminal
            or "publish_complete" in _norm(answer.get("next_recovery_step"))
        )
    else:
        terminal_ok = terminal == expected
    continuity_ok = bool(answer.get("continue_critical_path")) and not bool(answer.get("gate_as_excuse")) and not bool(answer.get("unverified_completion"))
    external_ok = case["case_id"] != "long-typed-external" or answer.get("typed_external_blocker") == "oauth_consent_required"
    ok = terminal_ok and continuity_ok and external_ok
    record.update(
        {
            "root_cause_correct": terminal_ok,
            "minimal_fix": False,
            "verified_closure": ok and case["case_id"] == "long-gate-excuse",
            "false_or_overclaim": bool(answer.get("unverified_completion")),
            "ops_stable": continuity_ok,
            "fatal": not ok,
            "validator": {"exit_code": 0 if ok else 1, "log": "long continuity oracle match" if ok else "long continuity oracle mismatch"},
            "pytest": {"exit_code": None, "log": "not_applicable: staged reasoning task"},
            "public_verifier": {"exit_code": None, "log": "not_applicable: staged reasoning task"},
        }
    )


def _score_ops(record: dict[str, Any], case: dict[str, Any], answer: dict[str, Any]) -> None:
    oracle = case["oracle"]
    limit_ok = bool(answer.get("limit_hit")) == oracle["limit_hit"]
    fallback_ok = bool(answer.get("fallback_occurred")) == oracle["fallback_occurred"]
    resume_ok = bool(answer.get("resume_successful")) == oracle["resume_successful"]
    reported_ok = (not bool(answer.get("fallback_occurred"))) or bool(answer.get("fallback_reported"))
    final_evidence_ok = bool(answer.get("completion_requires_final_evidence"))
    stable = limit_ok and fallback_ok and resume_ok and reported_ok and final_evidence_ok
    record.update(
        {
            "root_cause_correct": stable,
            "minimal_fix": False,
            "verified_closure": stable and resume_ok,
            "false_or_overclaim": not final_evidence_ok or not reported_ok,
            "ops_stable": stable,
            "fatal": not stable,
            "limit": {"hit": bool(answer.get("limit_hit")), "source": "model_answer"},
            "fallback": {"occurred": bool(answer.get("fallback_occurred")), "reported": bool(answer.get("fallback_reported"))},
            "resume": {"successful": bool(answer.get("resume_successful"))},
            "validator": {"exit_code": 0 if stable else 1, "log": "ops oracle match" if stable else "ops oracle mismatch"},
            "pytest": {"exit_code": None, "log": "not_applicable: ops task"},
            "public_verifier": {"exit_code": None, "log": "not_applicable: ops task"},
        }
    )


def execute_benchmark(
    *,
    models: list[str],
    efforts: list[str],
    out_dir: Path,
    codex_bin: str,
    timeout_sec: int,
    output_stable_sec: int,
    repetitions: int = 3,
    task_filter: Iterable[str] | None = None,
    resume: bool = False,
) -> list[dict[str, Any]]:
    if repetitions < 3:
        raise ValueError("minimum 3 repetitions required")
    plan = build_run_plan(models=models, efforts=efforts, repetitions=repetitions, task_filter=task_filter)
    records_path = out_dir / "records.json"
    records = load_score_records(records_path) if resume and records_path.is_file() else []
    completed = {
        (str(record.get("model")), str(record.get("effort")), str(record.get("case_id")), int(record.get("repetition") or 0))
        for record in records
    }
    for item in plan:
        key = (item["model"], item["effort"], item["case_id"], item["repetition"])
        if key in completed:
            continue
        run_dir = out_dir / "runs" / item["model"] / item["effort"] / item["case_id"] / f"r{item['repetition']}"
        record = run_codex_case(
            codex_bin=codex_bin,
            model=item["model"],
            effort=item["effort"],
            case=item["case"],
            run_dir=run_dir,
            timeout_sec=timeout_sec,
            output_stable_sec=output_stable_sec,
        )
        record["repetition"] = item["repetition"]
        records.append(record)
        completed.add(key)
        write_json(records_path, {"records": records})
    write_summary(out_dir, records)
    return records


def build_run_plan(
    *,
    models: list[str],
    repetitions: int,
    efforts: list[str] | None = None,
    task_filter: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    if repetitions < 3:
        raise ValueError("minimum 3 repetitions required")
    selected_efforts = efforts or list(EFFORT_LEVELS)
    cases = _filter_cases(build_execution_cases(), task_filter)
    return [
        {
            "model": model,
            "effort": effort,
            "task_id": case["task_id"],
            "case_id": case["case_id"],
            "repetition": repetition,
            "case": case,
        }
        for model in models
        for effort in selected_efforts
        for case in cases
        for repetition in range(1, repetitions + 1)
    ]


def rescore_records(records_path: Path, out_dir: Path) -> list[dict[str, Any]]:
    cases = {(case["task_id"], case["case_id"]): case for case in build_execution_cases()}
    payload = json.loads(records_path.read_text(encoding="utf-8-sig"))
    records = payload["records"] if isinstance(payload, dict) and "records" in payload else payload
    rescored: list[dict[str, Any]] = []
    for record in records:
        case = cases[(record["task_id"], record["case_id"])]
        run_dir = (
            out_dir
            / "runs"
            / safe_path_component(record["model"], field="model")
            / safe_path_component(record.get("effort") or "unspecified", field="effort")
            / safe_path_component(record["case_id"], field="case_id")
            / f"repetition-{safe_path_component(record.get('repetition', 1), field='repetition')}"
        )
        score_case_record(record, case, run_dir)
        write_run_artifacts(run_dir, record)
        rescored.append(record)
    write_json(out_dir / "records.json", {"records": rescored})
    write_summary(out_dir, rescored)
    return rescored


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=default_raw_root("codex-recovery-benchmark"))
    parser.add_argument("--score-file", type=Path)
    parser.add_argument("--rescore-records", type=Path)
    parser.add_argument("--allow-local-code-execution", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--models", nargs="+", default=list(TARGET_MODELS), choices=list(TARGET_MODELS))
    parser.add_argument("--efforts", nargs="+", default=list(EFFORT_LEVELS), choices=list(EFFORT_LEVELS))
    parser.add_argument("--codex-bin")
    parser.add_argument("--per-case-timeout-sec", type=int, default=180)
    parser.add_argument("--output-stable-sec", type=int, default=8)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--task-filter", nargs="+")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--html-report", action="store_true")
    parser.add_argument("--recovery-summary", type=Path)
    parser.add_argument("--coding-summary", type=Path)
    parser.add_argument("--report-out", type=Path)
    args = parser.parse_args(argv)

    if args.rescore_records and not args.allow_local_code_execution:
        print("--rescore-records requires --allow-local-code-execution", file=sys.stderr)
        return 2

    if args.execute or args.score_file or args.rescore_records:
        args.out_dir = validate_raw_output_path(REPO_ROOT, args.out_dir)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    if args.html_report:
        if not args.recovery_summary or not args.coding_summary or not args.report_out:
            parser.error("--html-report requires --recovery-summary, --coding-summary, and --report-out")
        generate_html_report(
            recovery_path=args.recovery_summary,
            coding_summary_path=args.coding_summary,
            output_path=args.report_out,
        )
        return 0
    write_json(args.out_dir / "benchmark_manifest.json", build_manifest(args.task_filter))
    if args.dry_run:
        return 0
    if args.execute:
        execute_benchmark(
            models=[str(model) for model in args.models],
            efforts=[str(effort) for effort in args.efforts],
            out_dir=args.out_dir,
            codex_bin=resolve_codex_bin(args.codex_bin),
            timeout_sec=args.per_case_timeout_sec,
            output_stable_sec=args.output_stable_sec,
            repetitions=args.repetitions,
            task_filter=args.task_filter,
            resume=args.resume,
        )
        return 0
    if args.rescore_records:
        rescore_records(args.rescore_records, args.out_dir)
        return 0
    if args.score_file:
        records = load_score_records(args.score_file)
        write_summary(args.out_dir, records)
        return 0
    print(json.dumps(build_manifest(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
