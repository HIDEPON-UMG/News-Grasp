#!/usr/bin/env python3
"""公開必須 inventory の単一 manifest。

runner / validator / repair prompt が同じ artifact 集合を見るための境界。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path


CATEGORY_PATHS: dict[str, dict[str, str]] = {
    "fx": {"digest_folder": "FX", "docs_segment": "fx"},
    "ai": {"digest_folder": "AI", "docs_segment": "ai"},
    "it": {"digest_folder": "IT-Consulting", "docs_segment": "it"},
    "mobility": {"digest_folder": "Mobility", "docs_segment": "mobility"},
    "manufacturing": {"digest_folder": "Manufacturing", "docs_segment": "manufacturing"},
    "economy": {"digest_folder": "Economy", "docs_segment": "economy"},
    "game": {"digest_folder": "Game", "docs_segment": "game"},
}

CATEGORY_ORDER = ("fx", "ai", "it", "mobility", "manufacturing", "economy", "game")

PUBLICATION_SCHEDULE: dict[int, set[str]] = {
    0: {"fx", "ai", "it", "mobility", "manufacturing", "economy"},
    1: {"fx", "ai", "it", "mobility", "manufacturing", "economy", "game"},
    2: {"fx", "ai", "it", "mobility", "manufacturing", "economy"},
    3: {"fx", "ai", "it", "mobility", "manufacturing", "economy", "game"},
    4: {"fx", "ai", "it", "mobility", "manufacturing", "economy"},
    5: {"fx", "ai", "it", "mobility", "game"},
    6: {"fx", "ai", "it", "mobility", "game"},
}

PUBLISHED_DOC_TEMPLATES = (
    "docs/{date}/index.html",
    "docs/{date}/summary/index.html",
    "docs/{cat_id}/{date}/index.html",
    "digest/DeepDive/{date}-DeepDive.md",
    "docs/deepdive/{date}/index.html",
)


def _issue(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def scheduled_category_ids(issue: str | date) -> list[str]:
    """指定日の配信対象カテゴリを公開順で返す。"""
    day = _issue(issue)
    return [
        cat_id for cat_id in CATEGORY_ORDER
        if cat_id in PUBLICATION_SCHEDULE.get(day.weekday(), set())
    ]


def digest_artifact_for_category(cat_id: str, issue: str | date) -> str:
    day = _issue(issue)
    folder = CATEGORY_PATHS[cat_id]["digest_folder"]
    return f"digest/{folder}/{day.isoformat()}-{folder}.md"


def docs_artifact_for_category(cat_id: str, issue: str | date) -> str:
    day = _issue(issue)
    segment = CATEGORY_PATHS[cat_id]["docs_segment"]
    return f"docs/{segment}/{day.isoformat()}/index.html"


def required_digest_artifacts(issue: str | date) -> list[str]:
    """daily-quality gate と repair prompt が見る digest/data artifact。"""
    day = _issue(issue)
    artifacts = [
        digest_artifact_for_category(cat_id, day)
        for cat_id in sorted(scheduled_category_ids(day), key=lambda c: CATEGORY_PATHS[c]["digest_folder"])
    ]
    artifacts.append(f"digest/Summary/{day.isoformat()}.md")
    artifacts.append("data/articles.jsonl")
    return artifacts


def required_published_docs_artifacts(issue: str | date) -> list[str]:
    """generate_pages 後に必須な当日 docs artifact。"""
    day = _issue(issue)
    issue_str = day.isoformat()
    artifacts = [
        f"docs/{issue_str}/index.html",
        f"docs/{issue_str}/summary/index.html",
    ]
    artifacts.extend(docs_artifact_for_category(cat_id, day) for cat_id in scheduled_category_ids(day))
    return artifacts


def required_deepdive_artifacts(issue: str | date) -> list[str]:
    day = _issue(issue)
    issue_str = day.isoformat()
    return [
        f"digest/DeepDive/{issue_str}-DeepDive.md",
        f"docs/deepdive/{issue_str}/index.html",
    ]


def required_published_artifacts(issue: str | date) -> list[str]:
    """通常公開前の必須 docs + DeepDive artifact。"""
    return required_published_docs_artifacts(issue) + required_deepdive_artifacts(issue)


def missing_artifacts(repo_root: Path, artifacts: list[str]) -> list[str]:
    return [rel for rel in artifacts if not (repo_root / rel).exists()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="News-Grasp publish inventory manifest")
    parser.add_argument("--date", required=True)
    parser.add_argument("--kind", choices=["digest", "published-docs", "deepdive", "published"], required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.kind == "digest":
        artifacts = required_digest_artifacts(args.date)
    elif args.kind == "published-docs":
        artifacts = required_published_docs_artifacts(args.date)
    elif args.kind == "deepdive":
        artifacts = required_deepdive_artifacts(args.date)
    else:
        artifacts = required_published_artifacts(args.date)

    if args.json:
        print(json.dumps(artifacts, ensure_ascii=False))
    else:
        for artifact in artifacts:
            print(artifact)
    return 0


if __name__ == "__main__":
    sys.exit(main())
