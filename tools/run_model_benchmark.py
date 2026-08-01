#!/usr/bin/env python3
"""News-Grasp role-matched model benchmark with repeat and API-cost telemetry."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
import subprocess
from tools.model_spawn_client import run_model_process
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


API_PRICES_PER_MILLION: dict[str, dict[str, float]] = {
    "gpt-5.4-mini": {"input": 0.75, "cached_input": 0.075, "output": 4.5},
    "gpt-5.4": {"input": 2.5, "cached_input": 0.25, "output": 15.0},
    "gpt-5.5": {"input": 5.0, "cached_input": 0.5, "output": 30.0},
    "gpt-5.6-luna": {"input": 1.0, "cached_input": 0.1, "output": 6.0},
    "gpt-5.6-terra": {"input": 2.5, "cached_input": 0.25, "output": 15.0},
    "gpt-5.6-sol": {"input": 5.0, "cached_input": 0.5, "output": 30.0},
}

CASES: dict[str, dict[str, Any]] = {
    "reporter": {
        "models": ["gpt-5.6-luna"],
        "reasoning": "high",
        "schema": "schemas/model_eval_output.schema.json",
    },
    "style_editor": {
        "models": ["gpt-5.6-luna"],
        "reasoning": "high",
        "schema": "schemas/model_eval_output.schema.json",
    },
    "newsroom_editor": {
        "models": ["gpt-5.6-luna"],
        "reasoning": "high",
        "schema": "schemas/newsroom_editor_eval_output.schema.json",
    },
    "deepdive": {
        "models": ["gpt-5.5", "gpt-5.6-sol"],
        "reasoning": "high",
        "schema": "schemas/model_benchmark_deepdive.schema.json",
    },
}


def parse_usage_jsonl(text: str) -> dict[str, int]:
    usage = {
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": 0,
    }
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "turn.completed" or not isinstance(event.get("usage"), dict):
            continue
        for key in usage:
            usage[key] = int(event["usage"].get(key, 0) or 0)
    return usage


def estimate_api_cost_usd(model: str, usage: dict[str, int]) -> float:
    price = API_PRICES_PER_MILLION[model]
    input_tokens = int(usage.get("input_tokens", 0))
    cached = min(input_tokens, int(usage.get("cached_input_tokens", 0)))
    uncached = input_tokens - cached
    output = int(usage.get("output_tokens", 0))
    return (
        uncached * price["input"]
        + cached * price["cached_input"]
        + output * price["output"]
    ) / 1_000_000


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _compact_items(items: list[dict[str, Any]], count: int = 7) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        genre = str(item.get("genre", "unknown"))
        if genre in seen:
            continue
        selected.append(item)
        seen.add(genre)
        if len(selected) >= count:
            break
    return selected or items[:count]


def build_case_prompt(case: str, repo_root: Path) -> tuple[str, str]:
    reporter_fixture = _load(repo_root / "build/model-eval/fixture.json")
    compact_reporter = {**reporter_fixture, "items": _compact_items(reporter_fixture["items"])}
    full_result = _load(repo_root / "build/model-eval/results/full.json")
    compact_editor = {**full_result, "items": full_result["items"][:7]}

    if case == "reporter":
        instruction = (repo_root / "prompts/model-eval-reporter.md").read_text(encoding="utf-8-sig")
        fixture = compact_reporter
    elif case == "style_editor":
        instruction = (repo_root / "prompts/model-eval-editor-rewrite.md").read_text(encoding="utf-8-sig")
        fixture = compact_editor
    elif case == "newsroom_editor":
        instruction = (repo_root / "prompts/model-eval-newsroom-editor.md").read_text(encoding="utf-8-sig")
        fixture = {
            "reporters": [
                {"category": "ai", "status": "pass", "records": 3, "quality": 4.6},
                {"category": "it", "status": "repairable", "records": 3, "issues": ["summary_too_long"]},
                {"category": "mobility", "status": "pass", "records": 3, "quality": 4.3},
            ],
            "dedup_conflicts": [{"left": "ai:2", "right": "it:1", "same_event": True}],
            "summary": {"required_categories": ["ai", "it", "mobility"], "categoryId_required": True},
            "append": {"expected_records": 8, "failed_records": ["it:3"]},
            "context_budget": {"forbid_full_articles_jsonl": True, "forbid_article_body_refetch": True},
            "deepdive_candidates": ["企業AIのPoCから本番移行", "Robotaxi運用規模の差"],
        }
    elif case == "deepdive":
        instruction = (
            "あなたはNews-Grasp DeepDiveの執筆者です。入力資料だけを根拠に、ITコンサルタントが"
            "読み進めやすい日本語の深掘り記事を作成してください。事実を足さず、論旨の流れ、"
            "情報密度、示唆、読みやすさを両立し、JSON schemaに従って返してください。"
        )
        fixture = {"source_items": compact_reporter["items"][:4]}
    else:
        raise KeyError(case)
    prompt = (
        f"{instruction.strip()}\n\nBenchmark case: {case}\n"
        "モデル自身の採点は参考値であり、事実を変えないことを最優先してください。\n\n"
        "Input fixture JSON:\n```json\n"
        f"{json.dumps(fixture, ensure_ascii=False, indent=2)}\n```\n"
    )
    fixture_hash = hashlib.sha256(
        json.dumps(fixture, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return prompt, fixture_hash


def _self_score(output: dict[str, Any]) -> float | None:
    scores: list[float] = []
    nodes = output.get("items") or output.get("tasks") or [output]
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get("self_score"), dict):
            continue
        values = [float(v) for v in node["self_score"].values() if isinstance(v, (int, float))]
        if values:
            scores.append(statistics.mean(values))
    return statistics.mean(scores) if scores else None


def run_one(
    *,
    case: str,
    model: str,
    repeat: int,
    prompt: str,
    fixture_hash: str,
    repo_root: Path,
    out_dir: Path,
    codex_exe: Path,
    timeout_sec: int,
) -> dict[str, Any]:
    run_dir = out_dir / "runs" / case / model / f"run-{repeat:02d}"
    run_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = run_dir / "prompt.md"
    output_path = run_dir / "output.json"
    stdout_path = run_dir / "events.jsonl"
    stderr_path = run_dir / "stderr.log"
    prompt_path.write_text(prompt, encoding="utf-8")
    schema = (repo_root / str(CASES[case]["schema"])).resolve()
    sandbox_dir = (out_dir / "sandbox").resolve()
    sandbox_dir.mkdir(parents=True, exist_ok=True)
    command = [
        str(codex_exe), "exec", "--json", "--ephemeral", "--ignore-user-config",
        "--ignore-rules", "--skip-git-repo-check", "-C", str(sandbox_dir),
        "-m", model, "-c", f'model_reasoning_effort="{CASES[case]["reasoning"]}"',
        "--output-schema", str(schema), "-o", str(output_path.resolve()), "-",
    ]
    started = time.perf_counter()
    timed_out = False
    try:
        completed = run_model_process(
            command,
            route="model_benchmark",
            input=prompt,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
            check=False,
        )
        rc = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        rc = 124
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
    duration = time.perf_counter() - started
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    usage = parse_usage_jsonl(stdout)
    output: dict[str, Any] | None = None
    parse_error = None
    if output_path.exists():
        try:
            output = _load(output_path)
        except (json.JSONDecodeError, OSError) as exc:
            parse_error = str(exc)
    success = rc == 0 and output is not None
    result = {
        "case": case,
        "model": model,
        "repeat": repeat,
        "reasoning": CASES[case]["reasoning"],
        "fixture_sha256": fixture_hash,
        "success": success,
        "exit_code": rc,
        "timed_out": timed_out,
        "parse_error": parse_error,
        "duration_sec": round(duration, 3),
        **usage,
        "api_cost_usd": estimate_api_cost_usd(model, usage),
        "self_score_reference": _self_score(output) if output else None,
        "paths": {
            "output": str(output_path),
            "events": str(stdout_path),
            "stderr": str(stderr_path),
        },
    }
    (run_dir / "run.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def aggregate(results: list[dict[str, Any]], repeats: int) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for case, cfg in CASES.items():
        for model in cfg["models"]:
            runs = [r for r in results if r["case"] == case and r["model"] == model]
            successful = [r for r in runs if r["success"]]
            scores = [r["self_score_reference"] for r in successful if r["self_score_reference"] is not None]
            durations = [r["duration_sec"] for r in runs]
            rows.append({
                "case": case,
                "model": model,
                "input_price_per_million_usd": API_PRICES_PER_MILLION[model]["input"],
                "cached_input_price_per_million_usd": API_PRICES_PER_MILLION[model]["cached_input"],
                "output_price_per_million_usd": API_PRICES_PER_MILLION[model]["output"],
                "expected_runs": repeats,
                "completed_runs": len(runs),
                "success_count": len(successful),
                "failure_count": len(runs) - len(successful),
                "success_rate": len(successful) / repeats if repeats else 0,
                "duration_mean_sec": statistics.mean(durations) if durations else None,
                "duration_stdev_sec": statistics.pstdev(durations) if len(durations) > 1 else 0.0,
                "input_tokens_total": sum(r["input_tokens"] for r in runs),
                "cached_input_tokens_total": sum(r["cached_input_tokens"] for r in runs),
                "output_tokens_total": sum(r["output_tokens"] for r in runs),
                "reasoning_output_tokens_total": sum(r["reasoning_output_tokens"] for r in runs),
                "api_cost_total_usd": sum(r["api_cost_usd"] for r in runs),
                "self_score_mean_reference": statistics.mean(scores) if scores else None,
                "self_score_stdev_reference": statistics.pstdev(scores) if len(scores) > 1 else 0.0,
            })
    return {
        "version": 1,
        "repeats_per_case_model": repeats,
        "cost_basis": "API-equivalent estimate; cached reads billed at 10% of input price; cache writes unavailable",
        "quality_note": "self_score is reference-only; reader-quality blind judging is reported separately",
        "rows": rows,
        "runs": results,
    }


def write_reports(summary: dict[str, Any], out_dir: Path) -> None:
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rows = summary["rows"]
    if rows:
        with (out_dir / "summary.csv").open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    lines = [
        "# News-Grasp Model Benchmark",
        "",
        "| Case | Model | Success | Mean sec | Input $/M | Output $/M | Total API $ | Self score ref |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        score = row["self_score_mean_reference"]
        lines.append(
            f"| {row['case']} | {row['model']} | {row['success_count']}/{row['expected_runs']} | "
            f"{row['duration_mean_sec']:.1f} | {row['input_price_per_million_usd']:.2f} | "
            f"{row['output_price_per_million_usd']:.2f} | {row['api_cost_total_usd']:.4f} | "
            f"{score:.3f} |" if score is not None else "n/a |"
        )
    lines.extend(["", "Self score is reference-only. Reader-quality evaluation is a separate artifact."])
    (out_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=Path("build/model-eval-5.6/benchmark"))
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--timeout-sec", type=int, default=900)
    parser.add_argument("--codex-exe", type=Path, required=True)
    parser.add_argument("--case", action="append", choices=sorted(CASES))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    repo_root = Path.cwd().resolve()
    cases = args.case or list(CASES)
    prompts = {case: build_case_prompt(case, repo_root) for case in cases}
    jobs = [
        (case, model, repeat)
        for case in cases
        for model in CASES[case]["models"]
        for repeat in range(1, args.repeats + 1)
    ]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    if args.dry_run:
        print(json.dumps({"jobs": jobs, "job_count": len(jobs)}, ensure_ascii=False, indent=2))
        return 0
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=args.max_workers) as pool:
        futures = {
            pool.submit(
                run_one,
                case=case,
                model=model,
                repeat=repeat,
                prompt=prompts[case][0],
                fixture_hash=prompts[case][1],
                repo_root=repo_root,
                out_dir=args.out_dir,
                codex_exe=args.codex_exe.resolve(),
                timeout_sec=args.timeout_sec,
            ): (case, model, repeat)
            for case, model, repeat in jobs
        }
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            print(json.dumps({k: result[k] for k in ("case", "model", "repeat", "success", "duration_sec", "api_cost_usd")}, ensure_ascii=False), flush=True)
    results.sort(key=lambda r: (r["case"], r["model"], r["repeat"]))
    summary = aggregate(results, args.repeats)
    write_reports(summary, args.out_dir)
    return 0 if all(r["success"] for r in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
