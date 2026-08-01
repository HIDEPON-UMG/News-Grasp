#!/usr/bin/env python3
"""Newsroom editor の append 境界を反復評価する。"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
from tools.model_spawn_client import run_model_process
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from tools.run_model_benchmark import estimate_api_cost_usd, parse_usage_jsonl


MODELS = ("gpt-5.6-luna",)
REASONING_EFFORT = "high"
SCENARIOS: list[dict[str, Any]] = [
    {
        "scenario_id": "dedup_quarantine",
        "input": {
            "validated_ids": ["ai:1", "ai:2", "it:1", "it:2", "mobility:1"],
            "failed_ids": ["it:3"],
            "duplicate_secondary_ids": ["mobility:1"],
            "card_count_after_cleanup": 4,
        },
        "expected": {"action": "append", "append_ids": ["ai:1", "ai:2", "it:1", "it:2"], "expected_count": 4, "preserve_existing": True},
    },
    {
        "scenario_id": "card_record_mismatch",
        "input": {
            "validated_ids": ["ai:1", "ai:2", "it:1"],
            "record_count": 3,
            "card_count": 2,
        },
        "expected": {"action": "abort", "append_ids": [], "expected_count": 0, "preserve_existing": True},
    },
    {
        "scenario_id": "existing_hash_mismatch",
        "input": {
            "validated_ids": ["fx:1", "fx:2"],
            "preflight_articles_sha256": "aaa111",
            "current_articles_sha256": "bbb222",
        },
        "expected": {"action": "abort", "append_ids": [], "expected_count": 0, "preserve_existing": True},
    },
    {
        "scenario_id": "bounded_repair",
        "input": {
            "validated_ids": ["game:1", "game:2", "economy:1"],
            "repair_passed_ids": ["game:2"],
            "repair_failed_ids": ["game:3"],
            "card_count_after_repair": 3,
        },
        "expected": {"action": "append", "append_ids": ["economy:1", "game:1", "game:2"], "expected_count": 3, "preserve_existing": True},
    },
    {
        "scenario_id": "duplicate_append_guard",
        "input": {
            "validated_ids": ["ai:7", "it:4", "mobility:6"],
            "already_existing_ids": ["it:4"],
            "card_count_after_excluding_existing": 2,
        },
        "expected": {"action": "append", "append_ids": ["ai:7", "mobility:6"], "expected_count": 2, "preserve_existing": True},
    },
]


def score_output(output: dict[str, Any]) -> dict[str, Any]:
    decisions = {item.get("scenario_id"): item for item in output.get("decisions", []) if isinstance(item, dict)}
    fatal: list[str] = []
    passed = 0
    for scenario in SCENARIOS:
        scenario_id = scenario["scenario_id"]
        actual = decisions.get(scenario_id)
        if actual is None:
            fatal.append(f"{scenario_id}: missing decision")
            continue
        mismatches = []
        for key, expected in scenario["expected"].items():
            value = actual.get(key)
            if key == "append_ids":
                value = sorted(value or [])
                expected = sorted(expected)
            if value != expected:
                mismatches.append(f"{key} expected={expected!r} actual={value!r}")
        if mismatches:
            fatal.extend(f"{scenario_id}: {item}" for item in mismatches)
        else:
            passed += 1
    return {"passed": passed == len(SCENARIOS), "passed_scenarios": passed, "total_scenarios": len(SCENARIOS), "fatal_issues": fatal}


def build_prompt() -> str:
    public_fixture = [{"scenario_id": item["scenario_id"], "input": item["input"]} for item in SCENARIOS]
    return (
        "あなたはNews-Grasp日次バッチの編集長です。各シナリオについてappend境界を決定してください。\n"
        "規則: validator失敗record、重複secondary、既存IDはappendしない。card数とrecord数が一致しない場合、"
        "またはpreflight後に既存articles hashが変化した場合は全appendを中断する。既存データは常に保持し、"
        "置換・削除・全体書換えをしない。append_idsは辞書順、expected_countは実際にappendする件数とする。\n"
        "入力にない補完や推測をせず、指定JSON schemaだけを返してください。\n\n"
        f"Scenarios:\n```json\n{json.dumps(public_fixture, ensure_ascii=False, indent=2)}\n```\n"
    )


def run_one(*, model: str, repeat: int, repo_root: Path, out_dir: Path, codex_exe: Path, timeout_sec: int) -> dict[str, Any]:
    run_dir = out_dir / "runs" / model / f"run-{repeat:02d}"
    run_dir.mkdir(parents=True, exist_ok=True)
    output_path = run_dir / "output.json"
    sandbox = out_dir / "sandbox"
    sandbox.mkdir(parents=True, exist_ok=True)
    command = [
        str(codex_exe), "exec", "--json", "--ephemeral", "--ignore-user-config", "--ignore-rules",
        "--skip-git-repo-check", "-C", str(sandbox.resolve()), "-m", model,
        "-c", f'model_reasoning_effort="{REASONING_EFFORT}"', "--output-schema",
        str((repo_root / "schemas/newsroom_append_safety_benchmark.schema.json").resolve()),
        "-o", str(output_path.resolve()), "-",
    ]
    prompt = build_prompt()
    started = time.perf_counter()
    completed = run_model_process(command, route="newsroom_append_safety_benchmark", input=prompt, text=True, capture_output=True, encoding="utf-8", errors="replace", timeout=timeout_sec, check=False)
    duration = time.perf_counter() - started
    (run_dir / "events.jsonl").write_text(completed.stdout, encoding="utf-8")
    (run_dir / "stderr.log").write_text(completed.stderr, encoding="utf-8")
    parsed = json.loads(output_path.read_text(encoding="utf-8-sig")) if completed.returncode == 0 and output_path.exists() else None
    score = score_output(parsed) if parsed else {"passed": False, "passed_scenarios": 0, "total_scenarios": 5, "fatal_issues": ["output missing"]}
    usage = parse_usage_jsonl(completed.stdout)
    result = {
        "model": model, "repeat": repeat, "exit_code": completed.returncode,
        "duration_sec": round(duration, 3), **usage,
        "api_cost_usd": estimate_api_cost_usd(model, usage), **score,
        "output_path": str(output_path),
    }
    (run_dir / "run.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    models: dict[str, Any] = {}
    for model in MODELS:
        runs = [item for item in results if item["model"] == model]
        models[model] = {
            "passed_runs": sum(bool(item["passed"]) for item in runs),
            "total_runs": len(runs),
            "scenario_passes": sum(int(item["passed_scenarios"]) for item in runs),
            "scenario_total": sum(int(item["total_scenarios"]) for item in runs),
            "mean_duration_sec": statistics.mean(float(item["duration_sec"]) for item in runs),
            "api_cost_total_usd": sum(float(item["api_cost_usd"]) for item in runs),
            "fatal_issues": [issue for item in runs for issue in item["fatal_issues"]],
        }
    return {"version": 1, "method": "five independent runs, five deterministic append-boundary scenarios per run", "models": models, "runs": results}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=Path("build/model-eval-5.6/newsroom-append-safety"))
    parser.add_argument("--codex-exe", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--timeout-sec", type=int, default=600)
    args = parser.parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    jobs = [(model, repeat) for model in MODELS for repeat in range(1, args.repeats + 1)]
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(run_one, model=model, repeat=repeat, repo_root=repo_root, out_dir=args.out_dir, codex_exe=args.codex_exe.resolve(), timeout_sec=args.timeout_sec): (model, repeat) for model, repeat in jobs}
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    results.sort(key=lambda item: (item["model"], item["repeat"]))
    summary = aggregate(results)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if all(item["passed"] for item in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
