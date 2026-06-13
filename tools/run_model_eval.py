#!/usr/bin/env python3
"""News-Grasp モデル評価の prompt 生成・Codex 実行・集計 CLI。"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


VARIANTS: dict[str, dict[str, Any]] = {
    "mini": {
        "model": "gpt-5.4-mini",
        "prompt": Path("prompts") / "model-eval-reporter.md",
        "cost_weight": 1.0,
    },
    "full": {
        "model": "gpt-5.4",
        "prompt": Path("prompts") / "model-eval-reporter.md",
        "cost_weight": 3.3,
    },
    "mini-editor": {
        "model": "gpt-5.4-mini",
        "prompt": Path("prompts") / "model-eval-editor-rewrite.md",
        "cost_weight": 1.6,
    },
}

VARIANT_ORDER = ("mini", "full", "mini-editor")

SCORE_KEYS = (
    "fact_retention",
    "naturalness",
    "news_grasp_style",
    "compression",
    "emphasis_ready",
)


def build_prompt(*, instruction: str, fixture: dict[str, Any], variant: str, model: str) -> str:
    return (
        f"{instruction.strip()}\n\n"
        f"Evaluation variant: {variant}\n"
        f"Target model: {model}\n\n"
        "Input fixture JSON:\n"
        "```json\n"
        f"{json.dumps(fixture, ensure_ascii=False, indent=2)}\n"
        "```\n"
    )


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_variant_prompts(*, fixture_path: Path, output_dir: Path, variants: dict[str, dict[str, Any]] = VARIANTS) -> dict[str, Path]:
    fixture = _load_json(fixture_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for name, cfg in variants.items():
        instruction = cfg["prompt"].read_text(encoding="utf-8-sig")
        prompt = build_prompt(instruction=instruction, fixture=fixture, variant=name, model=str(cfg["model"]))
        path = output_dir / f"{name}.prompt.md"
        path.write_text(prompt, encoding="utf-8")
        paths[name] = path
    return paths


def run_codex_variant(
    *,
    prompt_path: Path,
    output_path: Path,
    log_path: Path,
    model: str,
    schema_path: Path,
    repo_root: Path,
    codex_exe: str = "codex",
    timeout_sec: int = 3600,
) -> int:
    """codex exec を直接実行し、構造化 JSON を output_path に保存する。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        codex_exe,
        "--search",
        "exec",
        "-C",
        str(repo_root),
        "-m",
        model,
        "--output-schema",
        str(schema_path),
        "-o",
        str(output_path),
        "-",
    ]
    with prompt_path.open("r", encoding="utf-8-sig") as stdin, log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.run(
            cmd,
            stdin=stdin,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=repo_root,
            timeout=timeout_sec,
            check=False,
        )
    return proc.returncode


def aggregate_scores(results_dir: Path) -> dict[str, Any]:
    """評価 JSON 群から variant 別平均と cost-adjusted score を作る。"""
    variants: dict[str, Any] = {}
    for path in sorted(results_dir.glob("*.json")):
        data = _load_json(path)
        items = data.get("items", [])
        totals = {key: 0.0 for key in SCORE_KEYS}
        for item in items:
            score = item.get("self_score", {})
            for key in SCORE_KEYS:
                totals[key] += float(score.get(key, 0))
        count = len(items)
        averages = {
            key: (totals[key] / count if count else 0.0)
            for key in SCORE_KEYS
        }
        quality = sum(averages.values()) / len(SCORE_KEYS)
        cost_weight = float(data.get("cost_weight") or VARIANTS.get(path.stem, {}).get("cost_weight") or 1.0)
        variants[path.stem] = {
            "model": data.get("model", path.stem),
            "item_count": count,
            "averages": averages,
            "quality_score": quality,
            "cost_weight": cost_weight,
            "cost_adjusted_score": quality / cost_weight if cost_weight else quality,
        }
    recommended = None
    if variants:
        quality_floor = 4.0
        affordable_ceiling = 2.0
        qualified = [
            name for name, value in variants.items()
            if value["quality_score"] >= quality_floor and value["cost_weight"] <= affordable_ceiling
        ]
        if qualified:
            recommended = max(
                qualified,
                key=lambda name: (
                    variants[name]["quality_score"],
                    variants[name]["cost_adjusted_score"],
                ),
            )
        else:
            recommended = max(
                variants,
                key=lambda name: (
                    variants[name]["cost_adjusted_score"],
                    variants[name]["quality_score"],
                ),
            )
    return {
        "version": 1,
        "selection_policy": {
            "quality_floor": 4.0,
            "affordable_cost_ceiling": 2.0,
            "fallback": "highest cost_adjusted_score",
        },
        "variants": variants,
        "recommended_variant": recommended,
    }


def _attach_cost_weight(path: Path, weight: float) -> None:
    data = _load_json(path)
    data["cost_weight"] = weight
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run News-Grasp model evaluation via codex exec")
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=Path("build") / "model-eval")
    parser.add_argument("--schema", type=Path, default=Path("schemas") / "model_eval_output.schema.json")
    parser.add_argument("--variant", choices=sorted(VARIANTS), action="append")
    parser.add_argument("--codex-exe", default="codex")
    parser.add_argument("--timeout-sec", type=int, default=3600)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args(argv)

    repo_root = Path.cwd()
    prompt_dir = args.out_dir / "prompts"
    result_dir = args.out_dir / "results"
    log_dir = args.out_dir / "logs"
    prompt_paths = write_variant_prompts(fixture_path=args.fixture, output_dir=prompt_dir)
    selected = args.variant or list(VARIANT_ORDER)
    if args.prepare_only:
        print(json.dumps({"prompts": {k: str(v) for k, v in prompt_paths.items()}}, ensure_ascii=False, indent=2))
        return 0

    for name in selected:
        cfg = VARIANTS[name]
        prompt_path = prompt_paths[name]
        if name == "mini-editor":
            mini_result = result_dir / "mini.json"
            if not mini_result.exists():
                print("mini-editor requires mini result first", file=sys.stderr)
                return 2
            instruction = cfg["prompt"].read_text(encoding="utf-8-sig")
            prompt = build_prompt(
                instruction=instruction,
                fixture=_load_json(mini_result),
                variant=name,
                model=str(cfg["model"]),
            )
            prompt_path = prompt_dir / "mini-editor.prompt.md"
            prompt_path.write_text(prompt, encoding="utf-8")
        out_path = result_dir / f"{name}.json"
        rc = run_codex_variant(
            prompt_path=prompt_path,
            output_path=out_path,
            log_path=log_dir / f"{name}.log",
            model=str(cfg["model"]),
            schema_path=args.schema,
            repo_root=repo_root,
            codex_exe=args.codex_exe,
            timeout_sec=args.timeout_sec,
        )
        if rc != 0:
            print(f"{name}: codex exec failed rc={rc}", file=sys.stderr)
            return rc
        _attach_cost_weight(out_path, float(cfg["cost_weight"]))

    report = aggregate_scores(result_dir)
    report_path = args.out_dir / "summary.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
