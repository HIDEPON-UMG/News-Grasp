#!/usr/bin/env python3
"""生成済み markdown artifact の軽微な形式差を決定論的に正規化する。"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import sys

from tools.publish_inventory import CATEGORY_PATHS, required_generated_artifacts


@dataclass(frozen=True)
class NormalizeResult:
    normalized_files: list[Path]


def _category_from_path(path: Path) -> tuple[str, str] | None:
    folder = path.parent.name
    if folder == "Summary":
        return "Summary", "summary"
    if folder == "DeepDive":
        return "DeepDive", "deepdive"
    for cat_id, meta in CATEGORY_PATHS.items():
        if meta["digest_folder"] == folder:
            return folder, cat_id
    return None


def _split_frontmatter(text: str) -> tuple[list[str], str]:
    if not text.startswith("---\n"):
        return [], text
    parts = text.split("---\n", 2)
    if len(parts) < 3:
        return [], text
    lines = [line.rstrip() for line in parts[1].splitlines()]
    return lines, parts[2]


def _upsert_frontmatter(lines: list[str], key: str, value: str) -> list[str]:
    prefix = f"{key}:"
    if any(line.strip().startswith(prefix) for line in lines):
        return lines
    return lines + [f"{key}: {value}"]


def _normalize_markdown(path: Path, issue: str) -> bool:
    raw = path.read_bytes()
    text = raw.decode("utf-8-sig")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = text.replace("[記事を読む](", "[元記事](")
    if not text.endswith("\n"):
        text += "\n"

    frontmatter, body = _split_frontmatter(text)
    category = _category_from_path(path)
    if frontmatter:
        frontmatter = _upsert_frontmatter(frontmatter, "date", issue)
        if category:
            category_name, category_id = category
            frontmatter = _upsert_frontmatter(frontmatter, "category", category_name)
            frontmatter = _upsert_frontmatter(frontmatter, "categoryId", category_id)
        normalized = "---\n" + "\n".join(frontmatter).strip() + "\n---\n" + body.lstrip("\n")
    else:
        normalized = text

    if normalized != raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n"):
        path.write_text(normalized, encoding="utf-8", newline="\n")
        return True
    if raw.startswith(b"\xef\xbb\xbf"):
        path.write_text(normalized, encoding="utf-8", newline="\n")
        return True
    return False


def normalize_generated_artifacts(repo_root: Path, issue: str) -> NormalizeResult:
    normalized: list[Path] = []
    for rel in required_generated_artifacts(issue):
        if not rel.startswith("digest/") or not rel.endswith(".md"):
            continue
        path = repo_root / rel
        if not path.exists() or not path.is_file():
            continue
        if _normalize_markdown(path, issue):
            normalized.append(path)
    return NormalizeResult(normalized_files=normalized)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Normalize generated News-Grasp markdown artifacts")
    parser.add_argument("--date", required=True)
    parser.add_argument("--repo-root", default=".")
    args = parser.parse_args(argv)
    result = normalize_generated_artifacts(Path(args.repo_root), args.date)
    print(f"normalized files: {len(result.normalized_files)}")
    for path in result.normalized_files:
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
