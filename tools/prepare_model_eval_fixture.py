#!/usr/bin/env python3
"""モデル比較用に articles.jsonl から各カテゴリ N 件の fixture を作る。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


CANONICAL_GENRES = (
    "FX",
    "AI",
    "IT-Consulting",
    "Mobility",
    "Game",
    "Manufacturing",
    "Economy",
)


def build_eval_fixture(jsonl_path: Path, *, per_category: int = 3) -> dict[str, Any]:
    """各カテゴリ per_category 件を先頭から抽出して評価 fixture を返す。"""
    by_genre: dict[str, list[dict[str, Any]]] = {}
    with jsonl_path.open(encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            genre = str(row.get("genre") or "").strip()
            if genre not in CANONICAL_GENRES:
                continue
            if not row.get("title_ja"):
                continue
            bucket = by_genre.setdefault(genre, [])
            if len(bucket) >= per_category:
                continue
            bucket.append({
                "genre": genre,
                "title": row.get("title"),
                "title_ja": row.get("title_ja"),
                "source": row.get("source"),
                "published_date": row.get("published_date"),
                "url": row.get("url"),
                "summary": row.get("summary"),
                "bullets": row.get("bullets", []),
            })
    items: list[dict[str, Any]] = []
    missing = [genre for genre in CANONICAL_GENRES if len(by_genre.get(genre, [])) < per_category]
    if missing:
        raise ValueError(f"not enough title_ja records for categories: {', '.join(missing)}")
    for genre in CANONICAL_GENRES:
        items.extend(by_genre[genre][:per_category])
    return {
        "version": 1,
        "per_category": per_category,
        "genres": list(CANONICAL_GENRES),
        "items": items,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="News-Grasp model evaluation fixture builder")
    parser.add_argument("--jsonl", type=Path, default=Path("data") / "articles.jsonl")
    parser.add_argument("--per-category", type=int, default=3)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    fixture = build_eval_fixture(args.jsonl, per_category=args.per_category)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(fixture, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(fixture['items'])} item(s): {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
