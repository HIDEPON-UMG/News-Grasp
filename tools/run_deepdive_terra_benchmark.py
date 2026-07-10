#!/usr/bin/env python3
"""DeepDive の Terra 追加生成と 5.5 / Sol との匿名比較を実行する。"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from tools.run_model_benchmark import build_case_prompt, estimate_api_cost_usd, parse_usage_jsonl, run_one


TERRA_MODEL = "gpt-5.6-terra"
JUDGE_MODEL = "gpt-5.4"
PAIRS = (("gpt-5.5", TERRA_MODEL), (TERRA_MODEL, "gpt-5.6-sol"))
DIMENSIONS = (
    "readability", "coherence", "natural_japanese", "information_density",
    "insight", "non_repetition", "reader_usefulness",
)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def strip_scores(node: Any) -> Any:
    if isinstance(node, dict):
        return {key: strip_scores(value) for key, value in node.items() if key not in {"model", "self_score"}}
    if isinstance(node, list):
        return [strip_scores(value) for value in node]
    return node


def output_path(*, model: str, repeat: int, core_dir: Path, terra_dir: Path) -> Path:
    base = terra_dir if model == TERRA_MODEL else core_dir
    return base / "runs" / "deepdive" / model / f"run-{repeat:02d}" / "output.json"


def build_bundle(*, first: str, second: str, core_dir: Path, terra_dir: Path) -> dict[str, Any]:
    pairs = []
    for repeat in range(1, 6):
        pairs.append({
            "repeat": repeat,
            "A": strip_scores(load_json(output_path(model=first, repeat=repeat, core_dir=core_dir, terra_dir=terra_dir))),
            "B": strip_scores(load_json(output_path(model=second, repeat=repeat, core_dir=core_dir, terra_dir=terra_dir))),
        })
    return {"case": "deepdive", "pairs": pairs}


def run_judge(*, first: str, second: str, order: int, core_dir: Path, terra_dir: Path, out_dir: Path, codex_exe: Path, timeout_sec: int) -> dict[str, Any]:
    a_model, b_model = (first, second) if order == 1 else (second, first)
    bundle = build_bundle(first=a_model, second=b_model, core_dir=core_dir, terra_dir=terra_dir)
    prompt = f"""あなたはNews-Grasp DeepDiveの匿名比較評価者です。モデル名と自己採点は除去済みです。
各repeatのA/Bを readability, coherence, natural_japanese, information_density, insight,
non_repetition, reader_usefulness の7軸で1-5点評価してください。入力にない事実、数値や
固有名詞の変造、論旨を損なう欠落はfatal_issuesに記録し、流暢さで救済しないでください。
指定JSON schemaだけを返してください。

匿名出力:
```json
{json.dumps(bundle, ensure_ascii=False)}
```
"""
    pair_name = f"{first}__vs__{second}"
    run_dir = out_dir / "judges" / pair_name / f"order-{order}"
    run_dir.mkdir(parents=True, exist_ok=True)
    output = run_dir / "judge.json"
    sandbox = out_dir / "sandbox"
    sandbox.mkdir(parents=True, exist_ok=True)
    command = [
        str(codex_exe), "exec", "--json", "--ephemeral", "--ignore-user-config", "--ignore-rules",
        "--skip-git-repo-check", "-C", str(sandbox.resolve()), "-m", JUDGE_MODEL,
        "-c", 'model_reasoning_effort="medium"', "--output-schema",
        str(Path("schemas/model_benchmark_reader_judge.schema.json").resolve()),
        "-o", str(output.resolve()), "-",
    ]
    started = time.perf_counter()
    completed = subprocess.run(command, input=prompt, text=True, capture_output=True, encoding="utf-8", errors="replace", timeout=timeout_sec, check=False)
    duration = time.perf_counter() - started
    (run_dir / "events.jsonl").write_text(completed.stdout, encoding="utf-8")
    (run_dir / "stderr.log").write_text(completed.stderr, encoding="utf-8")
    usage = parse_usage_jsonl(completed.stdout)
    result = {
        "pair": [first, second], "order": order, "A_model": a_model, "B_model": b_model,
        "success": completed.returncode == 0 and output.exists(), "exit_code": completed.returncode,
        "duration_sec": round(duration, 3), **usage,
        "api_cost_usd": estimate_api_cost_usd(JUDGE_MODEL, usage), "judge_path": str(output),
    }
    (run_dir / "run.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def aggregate(generation: list[dict[str, Any]], judges: list[dict[str, Any]]) -> dict[str, Any]:
    comparisons: dict[str, Any] = {}
    for first, second in PAIRS:
        runs = [item for item in judges if item["pair"] == [first, second] and item["success"]]
        scores = {first: [], second: []}
        wins = {first: 0, second: 0}
        ties = 0
        fatal: list[str] = []
        rationales: list[str] = []
        for run in runs:
            judged = load_json(Path(run["judge_path"]))
            for review in judged["run_reviews"]:
                for label in ("A", "B"):
                    model = run[f"{label}_model"]
                    values = review[f"{label.lower()}_scores"]
                    scores[model].append(statistics.mean(float(values[key]) for key in DIMENSIONS))
                winner = review["winner"]
                if winner == "tie":
                    ties += 1
                else:
                    wins[run[f"{winner}_model"]] += 1
                fatal.extend(review["fatal_issues"])
            rationales.append(judged["overall_rationale"])
        comparisons[f"{first}__vs__{second}"] = {
            "models": [first, second],
            "reader_quality_mean": {model: statistics.mean(values) if values else None for model, values in scores.items()},
            "pairwise_wins": wins, "ties": ties, "fatal_issues": fatal, "judge_rationales": rationales,
        }
    return {
        "version": 1,
        "method": "Terra five generations; blind A/B position reversal against current 5.5 and Sol",
        "terra_generation": {
            "successful_runs": sum(bool(item["success"]) for item in generation),
            "total_runs": len(generation),
            "mean_duration_sec": statistics.mean(float(item["duration_sec"]) for item in generation),
            "api_cost_total_usd": sum(float(item["api_cost_usd"]) for item in generation),
        },
        "comparisons": comparisons,
        "judge_api_cost_total_usd": sum(float(item["api_cost_usd"]) for item in judges),
        "incremental_api_cost_total_usd": sum(float(item["api_cost_usd"]) for item in generation + judges),
        "generation_runs": generation,
        "judge_runs": judges,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--core-dir", type=Path, default=Path("build/model-eval-5.6/benchmark"))
    parser.add_argument("--out-dir", type=Path, default=Path("build/model-eval-5.6/deepdive-terra-retest"))
    parser.add_argument("--codex-exe", type=Path, required=True)
    parser.add_argument("--timeout-sec", type=int, default=900)
    args = parser.parse_args(argv)
    prompt, fixture_hash = build_case_prompt("deepdive", Path.cwd())
    terra_dir = args.out_dir / "benchmark"
    generation: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run_one, case="deepdive", model=TERRA_MODEL, repeat=repeat, prompt=prompt, fixture_hash=fixture_hash, repo_root=Path.cwd(), out_dir=terra_dir, codex_exe=args.codex_exe.resolve(), timeout_sec=args.timeout_sec) for repeat in range(1, 6)]
        for future in as_completed(futures):
            generation.append(future.result())
    generation.sort(key=lambda item: item["repeat"])
    judges: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run_judge, first=first, second=second, order=order, core_dir=args.core_dir, terra_dir=terra_dir, out_dir=args.out_dir, codex_exe=args.codex_exe.resolve(), timeout_sec=args.timeout_sec) for first, second in PAIRS for order in (1, 2)]
        for future in as_completed(futures):
            judges.append(future.result())
    judges.sort(key=lambda item: (item["pair"], item["order"]))
    summary = aggregate(generation, judges)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if all(item["success"] for item in generation + judges) else 2


if __name__ == "__main__":
    raise SystemExit(main())
