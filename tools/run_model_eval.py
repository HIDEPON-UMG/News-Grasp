#!/usr/bin/env python3
"""News-Grasp モデル評価の prompt 生成・Codex 実行・集計 CLI。"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from tools.model_spawn_client import run_model_process
import sys
from pathlib import Path
from typing import Any


VARIANTS: dict[str, dict[str, Any]] = {
    "mini": {
        "role": "reporter",
        "model": "gpt-5.6-luna",
        "prompt": Path("prompts") / "model-eval-reporter.md",
        "cost_weight": 1.0,
        "reasoning": "high",
    },
    "full": {
        "role": "reporter",
        "model": "gpt-5.6-luna",
        "prompt": Path("prompts") / "model-eval-reporter.md",
        "cost_weight": 1.0,
        "reasoning": "high",
    },
    "mini-editor": {
        "role": "style_editor",
        "model": "gpt-5.6-luna",
        "prompt": Path("prompts") / "model-eval-editor-rewrite.md",
        "cost_weight": 1.0,
        "reasoning": "high",
    },
    "mini-editor-54": {
        "role": "style_editor",
        "model": "gpt-5.6-luna",
        "prompt": Path("prompts") / "model-eval-editor-rewrite.md",
        "cost_weight": 1.0,
        "reasoning": "high",
    },
    "mini-editor-55": {
        "role": "style_editor",
        "model": "gpt-5.5",
        "prompt": Path("prompts") / "model-eval-editor-rewrite.md",
        "cost_weight": 5.0,
        "reasoning": "high",
    },
    "newsroom-editor-mini": {
        "role": "newsroom_editor",
        "model": "gpt-5.6-luna",
        "prompt": Path("prompts") / "model-eval-newsroom-editor.md",
        "cost_weight": 1.0,
        "reasoning": "high",
    },
    "newsroom-editor-54": {
        "role": "newsroom_editor",
        "model": "gpt-5.6-luna",
        "prompt": Path("prompts") / "model-eval-newsroom-editor.md",
        "cost_weight": 1.0,
        "reasoning": "high",
    },
    "newsroom-editor-55": {
        "role": "newsroom_editor",
        "model": "gpt-5.5",
        "prompt": Path("prompts") / "model-eval-newsroom-editor.md",
        "cost_weight": 5.0,
        "reasoning": "high",
    },
}

VARIANT_ORDER = (
    "mini",
    "mini-editor",
    "mini-editor-55",
)

NEWSROOM_EDITOR_VARIANT_ORDER = (
    "newsroom-editor-mini",
    "newsroom-editor-55",
)

SCORE_KEYS = (
    "fact_retention",
    "naturalness",
    "news_grasp_style",
    "compression",
    "emphasis_ready",
)

NEWSROOM_EDITOR_SCORE_KEYS = (
    "orchestration",
    "gate_decision",
    "dedup_resolution",
    "summary_planning",
    "append_safety",
    "context_budget",
    "deepdive_direction",
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


def write_combo_prompts(
    *,
    results_dir: Path,
    output_dir: Path,
    reporter_variants: list[str] | None = None,
    editor_variants: list[str] | None = None,
) -> dict[str, Path]:
    """reporter 出力ごとに editor 評価 prompt を作る。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    reporters = reporter_variants or [
        name for name, cfg in VARIANTS.items()
        if cfg.get("role") == "reporter"
    ]
    editors = editor_variants or [
        name for name, cfg in VARIANTS.items()
        if cfg.get("role") == "style_editor"
    ]
    paths: dict[str, Path] = {}
    for reporter_name in reporters:
        reporter_result = results_dir / f"{reporter_name}.json"
        if not reporter_result.exists():
            continue
        fixture = _load_json(reporter_result)
        for editor_name in editors:
            cfg = VARIANTS[editor_name]
            instruction = cfg["prompt"].read_text(encoding="utf-8-sig")
            combo_name = f"{reporter_name}__{editor_name}"
            prompt = build_prompt(
                instruction=instruction,
                fixture=fixture,
                variant=combo_name,
                model=str(cfg["model"]),
            )
            path = output_dir / f"{combo_name}.prompt.md"
            path.write_text(prompt, encoding="utf-8")
            paths[combo_name] = path
    return paths


def run_codex_variant(
    *,
    prompt_path: Path,
    output_path: Path,
    log_path: Path,
    model: str,
    reasoning_effort: str = "high",
    schema_path: Path,
    repo_root: Path,
    codex_exe: str = "codex",
    timeout_sec: int = 3600,
) -> int:
    """codex exec を直接実行し、構造化 JSON を output_path に保存する。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    arg_list = [
        "exec",
        "-C",
        str(repo_root),
        "-m",
        model,
        "-c",
        f'model_reasoning_effort="{reasoning_effort}"',
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(output_path),
    ]
    exe_lower = codex_exe.casefold()
    if os.name == "nt" and exe_lower.endswith(".ps1"):
        cmd = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            codex_exe,
            *arg_list,
        ]
    elif os.name == "nt" and (exe_lower.endswith(".cmd") or exe_lower.endswith(".bat")):
        raise ValueError("unsupported Codex executable extension: use .exe or .ps1")
    else:
        cmd = [
        codex_exe,
            *arg_list,
        ]
    with prompt_path.open("r", encoding="utf-8-sig") as stdin, log_path.open("w", encoding="utf-8") as log:
        proc = run_model_process(
            cmd,
            route="model_eval",
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
        if "__" in path.stem:
            continue
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
        role = str(data.get("role") or VARIANTS.get(path.stem, {}).get("role") or "unknown")
        variants[path.stem] = {
            "role": role,
            "model": data.get("model", path.stem),
            "item_count": count,
            "averages": averages,
            "quality_score": quality,
            "cost_weight": cost_weight,
            "cost_adjusted_score": quality / cost_weight if cost_weight else quality,
        }
    def _recommend(names: list[str]) -> str | None:
        if not names:
            return None
        quality_floor = 4.0
        affordable_ceiling = 2.0
        qualified = [
            name for name in names
            for value in [variants[name]]
            if value["quality_score"] >= quality_floor and value["cost_weight"] <= affordable_ceiling
        ]
        if qualified:
            return max(
                qualified,
                key=lambda name: (
                    variants[name]["quality_score"],
                    variants[name]["cost_adjusted_score"],
                ),
            )
        return max(
            names,
            key=lambda name: (
                variants[name]["cost_adjusted_score"],
                variants[name]["quality_score"],
            ),
        )
    recommended = _recommend(list(variants))
    roles: dict[str, Any] = {}
    expected_by_role = {
        role: sorted(name for name, cfg in VARIANTS.items() if cfg.get("role") == role)
        for role in sorted({str(cfg.get("role")) for cfg in VARIANTS.values()})
    }
    for role in sorted({v["role"] for v in variants.values()} | set(expected_by_role)):
        names = [name for name in sorted(variants) if variants[name]["role"] == role]
        missing = [name for name in expected_by_role.get(role, []) if name not in names]
        recommended_for_role = _recommend(names)
        roles[role] = {
            "candidate_variants": names,
            "expected_variants": expected_by_role.get(role, []),
            "missing_variants": missing,
            "decision_rule": "summary_result_required",
            "recommended_variant": recommended_for_role,
            "selected_variant": recommended_for_role if not missing else None,
            "blocking_reasons": (
                [f"missing_result:{name}" for name in missing]
                if missing else []
            ),
        }
    required_roles = {"reporter", "style_editor"}
    selection_status = "selected"
    for role in required_roles:
        info = roles.get(role, {})
        if not info.get("selected_variant") or info.get("blocking_reasons"):
            selection_status = "undecided"
            break
    return {
        "version": 1,
        "selection_status": selection_status,
        "source_of_truth": "codex exec subscription evaluation artifact",
        "uses_openai_api_key": False,
        "uses_openai_sdk": False,
        "selection_policy": {
            "quality_floor": 4.0,
            "affordable_cost_ceiling": 2.0,
            "fallback": "highest cost_adjusted_score",
        },
        "variants": variants,
        "roles": roles,
        "recommended_variant": recommended,
    }


def _result_quality(data: dict[str, Any]) -> dict[str, Any]:
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
    return {
        "item_count": count,
        "averages": averages,
        "quality_score": quality,
    }


def aggregate_combo_scores(results_dir: Path) -> dict[str, Any]:
    """記者・編集者の組み合わせ別に最終品質と合算コストを集計する。"""
    reporter_names = [
        name for name, cfg in VARIANTS.items()
        if cfg.get("role") == "reporter"
    ]
    editor_names = [
        name for name, cfg in VARIANTS.items()
        if cfg.get("role") == "style_editor"
    ]
    reporter_results: dict[str, dict[str, Any]] = {}
    combo_results: dict[str, dict[str, Any]] = {}

    for name in reporter_names:
        path = results_dir / f"{name}.json"
        if path.exists():
            reporter_results[name] = _load_json(path)

    for path in sorted(results_dir.glob("*.json")):
        stem = path.stem
        if "__" in stem:
            reporter_name, editor_name = stem.split("__", 1)
            if reporter_name in reporter_names and editor_name in editor_names:
                combo_results[stem] = _load_json(path)
        elif stem in editor_names and "mini" in reporter_results:
            combo_results[f"mini__{stem}"] = _load_json(path)

    combos: dict[str, Any] = {}
    for reporter_name, reporter_data in sorted(reporter_results.items()):
        reporter_quality = _result_quality(reporter_data)
        reporter_cost = float(
            reporter_data.get("cost_weight")
            or VARIANTS[reporter_name].get("cost_weight")
            or 1.0
        )
        no_editor_name = f"{reporter_name}__no-editor"
        combos[no_editor_name] = {
            "reporter_variant": reporter_name,
            "reporter_model": reporter_data.get("model", VARIANTS[reporter_name]["model"]),
            "editor_variant": None,
            "editor_model": None,
            "item_count": reporter_quality["item_count"],
            "averages": reporter_quality["averages"],
            "reporter_quality_score": reporter_quality["quality_score"],
            "final_quality_score": reporter_quality["quality_score"],
            "reporter_cost_weight": reporter_cost,
            "editor_cost_weight": 0.0,
            "total_cost_weight": reporter_cost,
            "cost_adjusted_score": (
                reporter_quality["quality_score"] / reporter_cost
                if reporter_cost else reporter_quality["quality_score"]
            ),
            "source_files": [f"{reporter_name}.json"],
        }

    for combo_name, combo_data in sorted(combo_results.items()):
        reporter_name, editor_name = combo_name.split("__", 1)
        if reporter_name not in reporter_results:
            continue
        reporter_data = reporter_results[reporter_name]
        reporter_quality = _result_quality(reporter_data)
        final_quality = _result_quality(combo_data)
        reporter_cost = float(
            reporter_data.get("cost_weight")
            or VARIANTS[reporter_name].get("cost_weight")
            or 1.0
        )
        editor_cost = float(
            combo_data.get("cost_weight")
            or VARIANTS[editor_name].get("cost_weight")
            or 1.0
        )
        total_cost = reporter_cost + editor_cost
        combos[combo_name] = {
            "reporter_variant": reporter_name,
            "reporter_model": reporter_data.get("model", VARIANTS[reporter_name]["model"]),
            "editor_variant": editor_name,
            "editor_model": combo_data.get("model", VARIANTS[editor_name]["model"]),
            "item_count": final_quality["item_count"],
            "averages": final_quality["averages"],
            "reporter_quality_score": reporter_quality["quality_score"],
            "final_quality_score": final_quality["quality_score"],
            "reporter_cost_weight": reporter_cost,
            "editor_cost_weight": editor_cost,
            "total_cost_weight": total_cost,
            "cost_adjusted_score": (
                final_quality["quality_score"] / total_cost
                if total_cost else final_quality["quality_score"]
            ),
            "source_files": [f"{reporter_name}.json", f"{combo_name}.json"],
        }

    expected_combos = [
        f"{reporter}__{editor}"
        for reporter in reporter_names
        for editor in editor_names
    ]
    missing_combos = [name for name in expected_combos if name not in combos]
    selectable = {
        name: combo for name, combo in combos.items()
        if combo["editor_variant"] is not None
    }
    recommended = None
    if selectable:
        recommended = max(
            sorted(selectable),
            key=lambda name: (
                selectable[name]["final_quality_score"],
                -selectable[name]["total_cost_weight"],
                selectable[name]["cost_adjusted_score"],
            ),
        )
    return {
        "version": 1,
        "selection_status": "selected" if recommended else "undecided",
        "coverage_status": "complete" if not missing_combos else "incomplete",
        "source_of_truth": "reporter_editor_combo_final_quality_and_total_cost",
        "uses_openai_api_key": False,
        "uses_openai_sdk": False,
        "selection_policy": {
            "primary": "highest final_quality_score",
            "tie_breaker_1": "lowest total_cost_weight",
            "tie_breaker_2": "highest cost_adjusted_score",
            "requires_all_reporter_editor_pairs": True,
        },
        "expected_combos": expected_combos,
        "missing_combos": missing_combos,
        "combos": combos,
        "recommended_combo": recommended,
    }


def aggregate_newsroom_editor_scores(results_dir: Path) -> dict[str, Any]:
    """編集長の実業務評価 JSON 群から full-duty score を集計する。"""
    variants: dict[str, Any] = {}
    expected = sorted(
        name for name, cfg in VARIANTS.items()
        if cfg.get("role") == "newsroom_editor"
    )
    for name in expected:
        path = results_dir / f"{name}.json"
        if not path.exists():
            continue
        data = _load_json(path)
        tasks = data.get("tasks", [])
        totals = {key: 0.0 for key in NEWSROOM_EDITOR_SCORE_KEYS}
        for task in tasks:
            score = task.get("self_score", {})
            for key in NEWSROOM_EDITOR_SCORE_KEYS:
                totals[key] += float(score.get(key, 0))
        count = len(tasks)
        averages = {
            key: (totals[key] / count if count else 0.0)
            for key in NEWSROOM_EDITOR_SCORE_KEYS
        }
        quality = sum(averages.values()) / len(NEWSROOM_EDITOR_SCORE_KEYS)
        cost_weight = float(data.get("cost_weight") or VARIANTS[name].get("cost_weight") or 1.0)
        variants[name] = {
            "role": "newsroom_editor",
            "model": data.get("model", VARIANTS[name]["model"]),
            "task_count": count,
            "averages": averages,
            "quality_score": quality,
            "cost_weight": cost_weight,
            "cost_adjusted_score": quality / cost_weight if cost_weight else quality,
        }
    missing = [name for name in expected if name not in variants]
    quality_floor = 4.5
    recommended = None
    quality_leader = None
    if variants:
        quality_leader = max(
            sorted(variants),
            key=lambda name: (
                variants[name]["quality_score"],
                -variants[name]["cost_weight"],
                variants[name]["cost_adjusted_score"],
            ),
        )
        qualified = [
            name for name in sorted(variants)
            if variants[name]["quality_score"] >= quality_floor
        ]
        selectable = qualified or sorted(variants)
        recommended = max(
            selectable,
            key=lambda name: (
                variants[name]["cost_adjusted_score"],
                variants[name]["quality_score"],
                -variants[name]["cost_weight"],
            ),
        )
    return {
        "version": 1,
        "selection_status": "selected" if recommended and not missing else "undecided",
        "coverage_status": "complete" if not missing else "incomplete",
        "source_of_truth": "newsroom_editor_full_duty_eval",
        "uses_openai_api_key": False,
        "uses_openai_sdk": False,
        "selection_policy": {
            "quality_floor": quality_floor,
            "primary": "highest cost_adjusted_score among variants meeting quality_floor",
            "fallback": "highest cost_adjusted_score when no variant meets quality_floor",
            "quality_leader": "reported separately for escalation decisions",
            "style_rewrite_score_is_not_sufficient": True,
        },
        "score_keys": list(NEWSROOM_EDITOR_SCORE_KEYS),
        "expected_variants": expected,
        "missing_variants": missing,
        "variants": variants,
        "recommended_variant": recommended,
        "quality_leader_variant": quality_leader,
    }


def _attach_cost_weight(path: Path, weight: float) -> None:
    data = _load_json(path)
    data["cost_weight"] = weight
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run News-Grasp model evaluation via codex exec")
    default_schema = Path("schemas") / "model_eval_output.schema.json"
    newsroom_schema = Path("schemas") / "newsroom_editor_eval_output.schema.json"
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=Path("build") / "model-eval")
    parser.add_argument("--schema", type=Path, default=default_schema)
    parser.add_argument("--variant", choices=sorted(VARIANTS), action="append")
    parser.add_argument("--codex-exe", default="codex")
    parser.add_argument("--timeout-sec", type=int, default=3600)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--run-combos", action="store_true")
    parser.add_argument("--combo-only", action="store_true")
    parser.add_argument("--newsroom-editor-only", action="store_true")
    args = parser.parse_args(argv)
    if args.newsroom_editor_only and args.schema == default_schema:
        args.schema = newsroom_schema

    repo_root = Path.cwd()
    prompt_dir = args.out_dir / "prompts"
    result_dir = args.out_dir / "results"
    log_dir = args.out_dir / "logs"
    selected = args.variant or (
        list(NEWSROOM_EDITOR_VARIANT_ORDER)
        if args.newsroom_editor_only else list(VARIANT_ORDER)
    )
    selected_variants = {name: VARIANTS[name] for name in selected}
    prompt_paths = write_variant_prompts(
        fixture_path=args.fixture,
        output_dir=prompt_dir,
        variants=selected_variants,
    )
    if args.prepare_only:
        print(json.dumps({"prompts": {k: str(v) for k, v in prompt_paths.items()}}, ensure_ascii=False, indent=2))
        return 0

    for name in ([] if args.combo_only else selected):
        cfg = VARIANTS[name]
        prompt_path = prompt_paths[name]
        if name.startswith("mini-editor"):
            mini_result = result_dir / "mini.json"
            if not mini_result.exists():
                print(f"{name} requires mini result first", file=sys.stderr)
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
            reasoning_effort=str(cfg["reasoning"]),
            schema_path=args.schema,
            repo_root=repo_root,
            codex_exe=args.codex_exe,
            timeout_sec=args.timeout_sec,
        )
        if rc != 0:
            print(f"{name}: codex exec failed rc={rc}", file=sys.stderr)
            return rc
        _attach_cost_weight(out_path, float(cfg["cost_weight"]))

    if args.run_combos or args.combo_only:
        combo_prompt_paths = write_combo_prompts(results_dir=result_dir, output_dir=prompt_dir)
        if not combo_prompt_paths:
            print("combo evaluation requires reporter result files first", file=sys.stderr)
            return 2
        for combo_name, prompt_path in combo_prompt_paths.items():
            _reporter_name, editor_name = combo_name.split("__", 1)
            cfg = VARIANTS[editor_name]
            out_path = result_dir / f"{combo_name}.json"
            rc = run_codex_variant(
                prompt_path=prompt_path,
                output_path=out_path,
                log_path=log_dir / f"{combo_name}.log",
                model=str(cfg["model"]),
                reasoning_effort=str(cfg["reasoning"]),
                schema_path=args.schema,
                repo_root=repo_root,
                codex_exe=args.codex_exe,
                timeout_sec=args.timeout_sec,
            )
            if rc != 0:
                print(f"{combo_name}: codex exec failed rc={rc}", file=sys.stderr)
                return rc
            _attach_cost_weight(out_path, float(cfg["cost_weight"]))

    report = aggregate_scores(result_dir)
    report_path = args.out_dir / "summary.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    combo_report = aggregate_combo_scores(result_dir)
    combo_report_path = args.out_dir / "combo_summary.json"
    combo_report_path.write_text(json.dumps(combo_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    newsroom_report = aggregate_newsroom_editor_scores(result_dir)
    newsroom_report_path = args.out_dir / "newsroom_editor_summary.json"
    newsroom_report_path.write_text(json.dumps(newsroom_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output = {
        **report,
        "combo_summary": str(combo_report_path),
        "combo_selection": combo_report,
        "newsroom_editor_summary": str(newsroom_report_path),
        "newsroom_editor_selection": newsroom_report,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
