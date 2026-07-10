#!/usr/bin/env python3
"""Blind reader-quality judging for News-Grasp benchmark outputs."""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from tools.run_model_benchmark import CASES, estimate_api_cost_usd, parse_usage_jsonl


JUDGE_MODEL = {
    "reporter": "gpt-5.5",
    "style_editor": "gpt-5.5",
    "newsroom_editor": "gpt-5.5",
    "deepdive": "gpt-5.4",
}
DIMENSIONS = (
    "readability", "coherence", "natural_japanese", "information_density",
    "insight", "non_repetition", "reader_usefulness",
)


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _strip_scores(node: Any) -> Any:
    if isinstance(node, dict):
        return {
            key: _strip_scores(value)
            for key, value in node.items()
            if key not in {"model", "self_score"}
        }
    if isinstance(node, list):
        return [_strip_scores(value) for value in node]
    return node


def build_bundle(benchmark_dir: Path, case: str, first: str, second: str) -> dict[str, Any]:
    pairs = []
    for repeat in range(1, 6):
        a = _load(benchmark_dir / "runs" / case / first / f"run-{repeat:02d}" / "output.json")
        b = _load(benchmark_dir / "runs" / case / second / f"run-{repeat:02d}" / "output.json")
        pairs.append({"repeat": repeat, "A": _strip_scores(a), "B": _strip_scores(b)})
    return {"case": case, "order": "A_then_B", "pairs": pairs}


def run_judge(
    *, benchmark_dir: Path, out_dir: Path, case: str, order: int,
    codex_exe: Path, timeout_sec: int,
) -> dict[str, Any]:
    baseline, candidate = CASES[case]["models"]
    first, second = (baseline, candidate) if order == 1 else (candidate, baseline)
    bundle = build_bundle(benchmark_dir, case, first, second)
    prompt = f"""あなたはNews-Graspの読者品質を比較する匿名評価者です。
モデル名や自己採点は伏せられています。A/Bの位置ではなく文章そのものを評価してください。
各repeatについて A と B を次の7軸で1-5点評価します: readability, coherence,
natural_japanese, information_density, insight, non_repetition, reader_usefulness。
入力にない事実の追加、数値・固有名詞の変造、主目的未達は fatal_issues に記録し、
流暢さで救済しないでください。case={case} の役割に即して比較し、指定schemaのJSONだけを返してください。

匿名出力:
```json
{json.dumps(bundle, ensure_ascii=False)}
```
"""
    run_dir = out_dir / case / f"order-{order}"
    run_dir.mkdir(parents=True, exist_ok=True)
    output = run_dir / "judge.json"
    schema = Path("schemas/model_benchmark_reader_judge.schema.json").resolve()
    sandbox = (out_dir / "sandbox").resolve()
    sandbox.mkdir(parents=True, exist_ok=True)
    judge_model = JUDGE_MODEL[case]
    cmd = [
        str(codex_exe), "exec", "--json", "--ephemeral", "--ignore-user-config",
        "--ignore-rules", "--skip-git-repo-check", "-C", str(sandbox),
        "-m", judge_model, "-c", 'model_reasoning_effort="medium"',
        "--output-schema", str(schema), "-o", str(output.resolve()), "-",
    ]
    started = time.perf_counter()
    completed = subprocess.run(
        cmd, input=prompt, text=True, capture_output=True, encoding="utf-8",
        errors="replace", timeout=timeout_sec, check=False,
    )
    duration = time.perf_counter() - started
    (run_dir / "events.jsonl").write_text(completed.stdout, encoding="utf-8")
    (run_dir / "stderr.log").write_text(completed.stderr, encoding="utf-8")
    usage = parse_usage_jsonl(completed.stdout)
    result = {
        "case": case,
        "order": order,
        "A_model": first,
        "B_model": second,
        "judge_model": judge_model,
        "success": completed.returncode == 0 and output.exists(),
        "exit_code": completed.returncode,
        "duration_sec": round(duration, 3),
        **usage,
        "api_cost_usd": estimate_api_cost_usd(judge_model, usage),
        "judge_path": str(output),
    }
    (run_dir / "run.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def aggregate(results: list[dict[str, Any]], out_dir: Path) -> dict[str, Any]:
    cases: dict[str, Any] = {}
    for case in CASES:
        case_runs = [r for r in results if r["case"] == case and r["success"]]
        model_scores: dict[str, list[float]] = {model: [] for model in CASES[case]["models"]}
        wins: dict[str, int] = {model: 0 for model in CASES[case]["models"]}
        ties = 0
        fatal: list[str] = []
        rationales: list[str] = []
        for run in case_runs:
            judge = _load(Path(run["judge_path"]))
            for review in judge["run_reviews"]:
                for label in ("A", "B"):
                    model = run[f"{label}_model"]
                    scores = review[f"{label.lower()}_scores"]
                    model_scores[model].append(statistics.mean(float(scores[key]) for key in DIMENSIONS))
                winner = review["winner"]
                if winner == "tie":
                    ties += 1
                else:
                    wins[run[f"{winner}_model"]] += 1
                fatal.extend(review["fatal_issues"])
            rationales.append(judge["overall_rationale"])
        means = {model: (statistics.mean(values) if values else None) for model, values in model_scores.items()}
        best = max(means, key=lambda model: means[model] if means[model] is not None else -1)
        cases[case] = {
            "models": CASES[case]["models"],
            "reader_quality_mean": means,
            "pairwise_wins": wins,
            "ties": ties,
            "recommended_by_reader_quality": best,
            "fatal_issues": fatal,
            "judge_rationales": rationales,
        }
    return {
        "version": 1,
        "method": "blind model-name removal plus A/B position reversal; five repeats judged in both orders",
        "judge_limit": "LLM judge bias remains; self-scores are not used in this artifact",
        "judge_api_cost_total_usd": sum(r["api_cost_usd"] for r in results),
        "cases": cases,
        "judge_runs": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark-dir", type=Path, default=Path("build/model-eval-5.6/benchmark"))
    parser.add_argument("--out-dir", type=Path, default=Path("build/model-eval-5.6/reader-quality"))
    parser.add_argument("--codex-exe", type=Path, required=True)
    parser.add_argument("--timeout-sec", type=int, default=900)
    args = parser.parse_args(argv)
    jobs = [(case, order) for case in CASES for order in (1, 2)]
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(
                run_judge, benchmark_dir=args.benchmark_dir, out_dir=args.out_dir,
                case=case, order=order, codex_exe=args.codex_exe.resolve(),
                timeout_sec=args.timeout_sec,
            ): (case, order)
            for case, order in jobs
        }
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    results.sort(key=lambda r: (r["case"], r["order"]))
    summary = aggregate(results, args.out_dir)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# Reader Quality", "", "| Case | Model | Mean | Wins | Recommendation |", "|---|---|---:|---:|---|"]
    for case, info in summary["cases"].items():
        for model in info["models"]:
            lines.append(
                f"| {case} | {model} | {info['reader_quality_mean'][model]:.3f} | "
                f"{info['pairwise_wins'][model]} | "
                f"{'yes' if model == info['recommended_by_reader_quality'] else ''} |"
            )
    lines.extend(["", f"Judge API-equivalent cost: ${summary['judge_api_cost_total_usd']:.4f}"])
    (args.out_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0 if all(r["success"] for r in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
