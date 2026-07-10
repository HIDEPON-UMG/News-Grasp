#!/usr/bin/env python3
"""DeepDive 3モデルを同一尺度で匿名採点する。"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from tools.run_deepdive_terra_benchmark import output_path, strip_scores
from tools.run_model_benchmark import estimate_api_cost_usd, parse_usage_jsonl


MODELS = ("gpt-5.5", "gpt-5.6-terra", "gpt-5.6-sol")
ORDERS = (
    ("gpt-5.5", "gpt-5.6-terra", "gpt-5.6-sol"),
    ("gpt-5.6-terra", "gpt-5.6-sol", "gpt-5.5"),
    ("gpt-5.6-sol", "gpt-5.5", "gpt-5.6-terra"),
)
JUDGE_MODEL = "gpt-5.4"
DIMENSIONS = ("readability", "coherence", "natural_japanese", "information_density", "insight", "non_repetition", "reader_usefulness")
QUALITY_WEIGHTS = {
    "readability": 0.10,
    "coherence": 0.15,
    "natural_japanese": 0.10,
    "information_density": 0.15,
    "insight": 0.25,
    "non_repetition": 0.05,
    "reader_usefulness": 0.20,
}


def weighted_quality_score(values: dict[str, float | int]) -> float:
    return sum(float(values[key]) * weight for key, weight in QUALITY_WEIGHTS.items())


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def build_bundle(order: tuple[str, str, str], core_dir: Path, terra_dir: Path) -> dict[str, Any]:
    rows = []
    for repeat in range(1, 6):
        row: dict[str, Any] = {"repeat": repeat}
        for label, model in zip(("A", "B", "C"), order, strict=True):
            row[label] = strip_scores(load_json(output_path(model=model, repeat=repeat, core_dir=core_dir, terra_dir=terra_dir)))
        rows.append(row)
    return {"case": "deepdive", "candidates": rows}


def run_order(*, order_index: int, order: tuple[str, str, str], core_dir: Path, terra_dir: Path, out_dir: Path, codex_exe: Path, timeout_sec: int) -> dict[str, Any]:
    bundle = build_bundle(order, core_dir, terra_dir)
    prompt = f"""あなたはNews-Grasp DeepDiveの匿名品質評価者です。A/B/Cは同じ資料から生成した3候補です。
各repeatについて3候補すべてを同時に、readability, coherence, natural_japanese,
information_density, insight, non_repetition, reader_usefulnessの7軸で1-5点採点してください。
同じ基準と厳しさを3候補へ適用し、入力にない事実・数値や固有名詞の変造はfatal_issuesへ記録します。
モデル名を推測せず、指定JSON schemaだけを返してください。

匿名候補:
```json
{json.dumps(bundle, ensure_ascii=False)}
```
"""
    run_dir = out_dir / f"order-{order_index}"
    run_dir.mkdir(parents=True, exist_ok=True)
    output = run_dir / "judge.json"
    sandbox = out_dir / "sandbox"
    sandbox.mkdir(parents=True, exist_ok=True)
    command = [str(codex_exe), "exec", "--json", "--ephemeral", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check", "-C", str(sandbox.resolve()), "-m", JUDGE_MODEL, "-c", 'model_reasoning_effort="medium"', "--output-schema", str(Path("schemas/deepdive_triad_judge.schema.json").resolve()), "-o", str(output.resolve()), "-"]
    started = time.perf_counter()
    completed = subprocess.run(command, input=prompt, text=True, capture_output=True, encoding="utf-8", errors="replace", timeout=timeout_sec, check=False)
    duration = time.perf_counter() - started
    (run_dir / "events.jsonl").write_text(completed.stdout, encoding="utf-8")
    (run_dir / "stderr.log").write_text(completed.stderr, encoding="utf-8")
    usage = parse_usage_jsonl(completed.stdout)
    result = {"order_index": order_index, "order": list(order), "success": completed.returncode == 0 and output.exists(), "exit_code": completed.returncode, "duration_sec": round(duration, 3), **usage, "api_cost_usd": estimate_api_cost_usd(JUDGE_MODEL, usage), "judge_path": str(output)}
    (run_dir / "run.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def aggregate(runs: list[dict[str, Any]]) -> dict[str, Any]:
    weighted_scores: dict[str, list[float]] = {model: [] for model in MODELS}
    raw_scores: dict[str, list[float]] = {model: [] for model in MODELS}
    dimension_scores: dict[str, dict[str, list[float]]] = {model: {key: [] for key in DIMENSIONS} for model in MODELS}
    winner_counts = {model: 0 for model in MODELS}
    fatal: list[str] = []
    for run in runs:
        judged = load_json(Path(run["judge_path"]))
        for review in judged["run_reviews"]:
            for label in ("A", "B", "C"):
                model = run["order"][ord(label) - ord("A")]
                values = review[f"{label.lower()}_scores"]
                for key in DIMENSIONS:
                    dimension_scores[model][key].append(float(values[key]))
                raw_scores[model].append(statistics.mean(float(values[key]) for key in DIMENSIONS))
                weighted_scores[model].append(weighted_quality_score(values))
            if review["winner"] != "tie":
                winner_counts[run["order"][ord(review["winner"]) - ord("A")]] += 1
            fatal.extend(review["fatal_issues"])
    return {
        "version": 1,
        "method": "three candidates scored together; three position rotations; five outputs per model; DeepDive role-specific weighted quality",
        "weight_policy": QUALITY_WEIGHTS,
        "quality_mean": {model: statistics.mean(values) for model, values in weighted_scores.items()},
        "raw_equal_weight_mean": {model: statistics.mean(values) for model, values in raw_scores.items()},
        "dimension_mean": {model: {key: statistics.mean(values) for key, values in dimensions.items()} for model, dimensions in dimension_scores.items()},
        "winner_counts": winner_counts,
        "fatal_issues": fatal,
        "judge_api_cost_total_usd": sum(float(run["api_cost_usd"]) for run in runs),
        "judge_runs": runs,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--core-dir", type=Path, default=Path("build/model-eval-5.6/benchmark"))
    parser.add_argument("--terra-dir", type=Path, default=Path("build/model-eval-5.6/deepdive-terra-retest/benchmark"))
    parser.add_argument("--out-dir", type=Path, default=Path("build/model-eval-5.6/deepdive-triad-judge"))
    parser.add_argument("--codex-exe", type=Path, required=True)
    parser.add_argument("--timeout-sec", type=int, default=900)
    parser.add_argument("--aggregate-only", action="store_true")
    args = parser.parse_args(argv)
    if args.aggregate_only:
        runs = [load_json(args.out_dir / f"order-{index}" / "run.json") for index in range(1, 4)]
    else:
        runs = []
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(run_order, order_index=index, order=order, core_dir=args.core_dir, terra_dir=args.terra_dir, out_dir=args.out_dir, codex_exe=args.codex_exe.resolve(), timeout_sec=args.timeout_sec) for index, order in enumerate(ORDERS, 1)]
            for future in as_completed(futures):
                runs.append(future.result())
    runs.sort(key=lambda run: run["order_index"])
    summary = aggregate(runs)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if all(run["success"] for run in runs) else 2


if __name__ == "__main__":
    raise SystemExit(main())
