#!/usr/bin/env python3
"""カテゴリ横断で候補を dedup し、記者入力を決定論的に絞る。"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

try:
    from tools import dedup
except ModuleNotFoundError:
    import dedup  # type: ignore


DEFAULT_CATEGORIES = ("fx", "ai", "it", "mobility", "game", "manufacturing", "economy")


@dataclass(frozen=True)
class CrossDedupResult:
    input_count: int
    passed: int
    dropped: int
    output_dir: Path


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line:
            continue
        out.append(json.loads(line))
    return out


def _write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def load_category_candidates(input_dir: Path, categories: Iterable[str]) -> list[dict]:
    """カテゴリ別 JSONL を読み、category 欠落時はファイル名で補完する。"""
    out: list[dict] = []
    for category in categories:
        path_candidates = [
            input_dir / f"{category}.jsonl",
            input_dir / f"{category}_candidates.jsonl",
        ]
        if category == "it":
            path_candidates.append(input_dir / "it_consulting_candidates.jsonl")
        path = next((candidate for candidate in path_candidates if candidate.exists()), path_candidates[0])
        for row in _read_jsonl(path):
            row = dict(row)
            row.setdefault("category", category)
            out.append(row)
    return out


def write_category_outputs(output_dir: Path, rows: Iterable[dict], categories: Iterable[str]) -> None:
    by_category = {category: [] for category in categories}
    all_rows = list(rows)
    for row in all_rows:
        category = str(row.get("category") or "").strip()
        if category in by_category:
            by_category[category].append(row)
    for category, category_rows in by_category.items():
        _write_jsonl(output_dir / f"{category}.jsonl", category_rows)
    _write_jsonl(output_dir / "all.jsonl", all_rows)


def run_cross_category_dedup(
    *,
    input_dir: Path,
    output_dir: Path,
    articles_jsonl: Path,
    categories: Iterable[str] = DEFAULT_CATEGORIES,
    freshness_gate: bool = True,
    followup_gate: bool = True,
    window_hours: float = 24.0,
    max_source_age_days: int = dedup.DEFAULT_MAX_SOURCE_AGE_DAYS,
) -> CrossDedupResult:
    """全カテゴリ候補を一括 dedup し、カテゴリ別出力を作る。"""
    categories = tuple(categories)
    candidates = load_category_candidates(input_dir, categories)
    existing = dedup.load_existing(articles_jsonl)
    passed, dropped = dedup.dedup_candidates(
        candidates,
        existing,
        window_hours=window_hours,
        followup_gate=followup_gate,
        freshness_gate=freshness_gate,
        max_source_age_days=max_source_age_days,
    )
    write_category_outputs(output_dir, passed, categories)
    _write_jsonl(output_dir / "dropped.jsonl", dropped)
    return CrossDedupResult(
        input_count=len(candidates),
        passed=len(passed),
        dropped=len(dropped),
        output_dir=output_dir,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="News-Grasp cross-category candidate dedup")
    parser.add_argument("--input-dir", type=Path, default=Path("build") / "candidates")
    parser.add_argument("--output-dir", type=Path, default=Path("build") / "deduped-candidates")
    parser.add_argument("--articles-jsonl", type=Path, default=Path("data") / "articles.jsonl")
    parser.add_argument("--categories", nargs="+", default=list(DEFAULT_CATEGORIES))
    parser.add_argument("--no-freshness-gate", action="store_true")
    parser.add_argument("--no-followup-gate", action="store_true")
    parser.add_argument("--window-hours", type=float, default=24.0)
    parser.add_argument("--max-source-age-days", type=int, default=dedup.DEFAULT_MAX_SOURCE_AGE_DAYS)
    args = parser.parse_args(argv)

    result = run_cross_category_dedup(
        input_dir=args.input_dir,
        output_dir=args.output_dir,
        articles_jsonl=args.articles_jsonl,
        categories=args.categories,
        freshness_gate=not args.no_freshness_gate,
        followup_gate=not args.no_followup_gate,
        window_hours=args.window_hours,
        max_source_age_days=args.max_source_age_days,
    )
    print(json.dumps({
        "input_count": result.input_count,
        "passed": result.passed,
        "dropped": result.dropped,
        "output_dir": str(result.output_dir),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
