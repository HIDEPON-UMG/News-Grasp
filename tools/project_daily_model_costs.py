#!/usr/bin/env python3
"""正常完走日のusageログからモデル切替時のAPI換算費用を推計する。"""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from tools.model_policy import DEFAULT_MODEL_POLICY


def _role(flow: str) -> str | None:
    if flow.startswith("reporter:"):
        return "reporter"
    if flow == "newsroom_editor":
        return "newsroom_editor"
    if flow == "deepdive":
        return "deepdive"
    if flow.startswith("repair:"):
        return "repair"
    if flow in {"editor", "style_editor"}:
        return "style_editor"
    return None


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def load_calibration(summary_path: Path) -> dict[str, dict[str, float | str]]:
    summary = json.loads(summary_path.read_text(encoding="utf-8-sig"))
    rows = {(row["case"], row["model"]): row for row in summary["rows"]}

    def unit(case: str, model: str) -> float:
        row = rows[(case, model)]
        tokens = int(row["input_tokens_total"]) + int(row["output_tokens_total"])
        return float(row["api_cost_total_usd"]) / tokens

    reporter_model = str(DEFAULT_MODEL_POLICY["reporter"]["default"])
    newsroom_model = str(DEFAULT_MODEL_POLICY["newsroom_editor"]["default"])
    editor_model = str(DEFAULT_MODEL_POLICY["editor"]["default"])
    deepdive_model = str(DEFAULT_MODEL_POLICY["deepdive"]["default"])
    reporter_row = rows[("reporter", reporter_model)]
    newsroom_row = rows[("newsroom_editor", newsroom_model)]
    blended_tokens = sum(int(row["input_tokens_total"]) + int(row["output_tokens_total"]) for row in (reporter_row, newsroom_row))
    blended_cost = sum(float(row["api_cost_total_usd"]) for row in (reporter_row, newsroom_row))
    return {
        "reporter": {"current_unit": unit("reporter", reporter_model), "candidate_multiplier": 1.0, "current_model": reporter_model, "candidate_model": reporter_model},
        "newsroom_editor": {"current_unit": unit("newsroom_editor", newsroom_model), "candidate_multiplier": 1.0, "current_model": newsroom_model, "candidate_model": newsroom_model},
        "deepdive": {"current_unit": unit("deepdive", deepdive_model), "candidate_multiplier": 1.0, "current_model": deepdive_model, "candidate_model": deepdive_model},
        "repair": {"current_unit": blended_cost / blended_tokens, "candidate_multiplier": 1.0, "current_model": str(DEFAULT_MODEL_POLICY["repair"]["default"]), "candidate_model": str(DEFAULT_MODEL_POLICY["repair"]["default"])},
        "style_editor": {"current_unit": unit("style_editor", editor_model), "candidate_multiplier": 1.0, "current_model": editor_model, "candidate_model": editor_model},
    }


def project_records(records: list[dict[str, Any]], calibration: dict[str, dict[str, Any]]) -> dict[str, Any]:
    role_rows: dict[str, dict[str, float | int]] = defaultdict(lambda: {"tokens": 0, "current_usd": 0.0, "candidate_usd": 0.0})
    direct: dict[str, dict[str, float | int]] = defaultdict(lambda: {"tokens": 0, "current_usd": 0.0, "candidate_usd": 0.0})
    unpriced: list[str] = []
    for record in records:
        tokens = record.get("tokens_used")
        if not isinstance(tokens, int) or tokens < 0 or int(record.get("exit_code", 0)) != 0:
            continue
        flow = str(record.get("flow", ""))
        role = _role(flow)
        if role is None:
            continue
        if role not in calibration:
            unpriced.append(flow)
            continue
        current = tokens * float(calibration[role]["current_unit"])
        candidate = current * float(calibration[role]["candidate_multiplier"])
        role_rows[role]["tokens"] += tokens
        role_rows[role]["current_usd"] += current
        role_rows[role]["candidate_usd"] += candidate
        if role == "reporter":
            category = flow.split(":", 1)[1]
            direct[category]["tokens"] += tokens
            direct[category]["current_usd"] += current
            direct[category]["candidate_usd"] += candidate

    reporter_tokens = sum(int(row["tokens"]) for row in direct.values())
    shared_current = sum(float(row["current_usd"]) for role, row in role_rows.items() if role != "reporter")
    shared_candidate = sum(float(row["candidate_usd"]) for role, row in role_rows.items() if role != "reporter")
    categories = []
    for category in sorted(direct):
        row = direct[category]
        share = int(row["tokens"]) / reporter_tokens if reporter_tokens else 0.0
        current = float(row["current_usd"]) + shared_current * share
        candidate = float(row["candidate_usd"]) + shared_candidate * share
        categories.append({
            "category": category,
            "reporter_tokens": int(row["tokens"]),
            "allocation_share": share,
            "direct_current_usd": float(row["current_usd"]),
            "direct_candidate_usd": float(row["candidate_usd"]),
            "shared_current_usd": shared_current * share,
            "shared_candidate_usd": shared_candidate * share,
            "current_usd": current,
            "candidate_usd": candidate,
            "delta_usd": candidate - current,
            "delta_percent": ((candidate / current) - 1.0) * 100 if current else 0.0,
        })
    current_total = sum(float(row["current_usd"]) for row in role_rows.values())
    candidate_total = sum(float(row["candidate_usd"]) for row in role_rows.values())
    return {
        "overall": {
            "tokens": sum(int(row["tokens"]) for row in role_rows.values()),
            "current_usd": current_total,
            "candidate_usd": candidate_total,
            "delta_usd": candidate_total - current_total,
            "delta_percent": ((candidate_total / current_total) - 1.0) * 100 if current_total else 0.0,
        },
        "roles": [{"role": role, **row, "delta_usd": float(row["candidate_usd"]) - float(row["current_usd"])} for role, row in sorted(role_rows.items())],
        "categories": categories,
        "unpriced_flows": sorted(set(unpriced)),
    }


def aggregate_dates(date_reports: list[dict[str, Any]]) -> dict[str, Any]:
    overall = {key: sum(float(report["overall"][key]) for report in date_reports) for key in ("tokens", "current_usd", "candidate_usd", "delta_usd")}
    overall["tokens"] = int(overall["tokens"])
    overall["delta_percent"] = ((overall["candidate_usd"] / overall["current_usd"]) - 1.0) * 100 if overall["current_usd"] else 0.0
    categories: dict[str, dict[str, Any]] = defaultdict(lambda: {"reporter_tokens": 0, "current_usd": 0.0, "candidate_usd": 0.0, "days_present": 0})
    roles: dict[str, dict[str, Any]] = defaultdict(lambda: {"tokens": 0, "current_usd": 0.0, "candidate_usd": 0.0})
    for report in date_reports:
        for row in report["categories"]:
            target = categories[row["category"]]
            target["reporter_tokens"] += row["reporter_tokens"]
            target["current_usd"] += row["current_usd"]
            target["candidate_usd"] += row["candidate_usd"]
            target["days_present"] += 1
        for row in report["roles"]:
            target = roles[row["role"]]
            target["tokens"] += row["tokens"]
            target["current_usd"] += row["current_usd"]
            target["candidate_usd"] += row["candidate_usd"]
    category_rows = []
    for category, row in sorted(categories.items()):
        current, candidate = row["current_usd"], row["candidate_usd"]
        category_rows.append({"category": category, **row, "delta_usd": candidate - current, "delta_percent": ((candidate / current) - 1.0) * 100 if current else 0.0})
    role_rows = []
    for role, row in sorted(roles.items()):
        role_rows.append({"role": role, **row, "delta_usd": row["candidate_usd"] - row["current_usd"], "delta_percent": ((row["candidate_usd"] / row["current_usd"]) - 1.0) * 100 if row["current_usd"] else 0.0})
    return {"overall": overall, "categories": category_rows, "roles": role_rows}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dates", nargs="+", required=True)
    parser.add_argument("--usage-dir", type=Path, default=Path("build/codex-usage"))
    parser.add_argument("--runner-log-dir", type=Path, default=Path.home() / "bin/news-grasp-logs")
    parser.add_argument("--benchmark-summary", type=Path, default=Path("build/model-eval-5.6/benchmark/summary.json"))
    parser.add_argument("--output", type=Path, default=Path("build/model-eval-5.6/daily-cost-projection.json"))
    args = parser.parse_args(argv)
    calibration = load_calibration(args.benchmark_summary)
    date_reports = []
    completion_evidence = []
    for date in args.dates:
        usage_path = args.usage_dir / f"{date}.jsonl"
        log_path = args.runner_log_dir / f"{date}.log"
        log_text = log_path.read_text(encoding="utf-8-sig", errors="replace")
        completed = "publish-complete manifest verification OK" in log_text and "news-grasp-runner.ps1 OK" in log_text
        if not completed:
            raise RuntimeError(f"date is not a verified successful run: {date}")
        report = project_records(load_jsonl(usage_path), calibration)
        report["date"] = date
        date_reports.append(report)
        completion_evidence.append({"date": date, "usage_log": str(usage_path), "runner_log": str(log_path), "publish_complete": True})
    aggregate = aggregate_dates(date_reports)
    payload = {
        "version": 1,
        "basis": "API-equivalent estimate from runner tokens_used; current role cost-per-token calibrated by benchmark; candidate task-cost multiplier calibrated by same-fixture five-run benchmark",
        "limits": [
            "runner usage does not preserve input/output/cache split; this is an estimate, not an invoice",
            "shared newsroom/deepdive/repair cost is allocated to categories by each day's reporter-token share",
            "style editor had zero observed standalone invocations on both source dates, so its historical delta is zero",
        ],
        "calibration": calibration,
        "completion_evidence": completion_evidence,
        "dates": date_reports,
        "aggregate": aggregate,
        "average_day": {key: value / len(date_reports) for key, value in aggregate["overall"].items() if key != "delta_percent"},
    }
    payload["average_day"]["delta_percent"] = aggregate["overall"]["delta_percent"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    csv_path = args.output.with_suffix(".categories.csv")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(aggregate["categories"][0]))
        writer.writeheader()
        writer.writerows(aggregate["categories"])
    print(json.dumps({"output": str(args.output), "category_csv": str(csv_path), "aggregate": aggregate["overall"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
