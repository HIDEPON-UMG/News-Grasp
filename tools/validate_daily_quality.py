#!/usr/bin/env python3
"""日次 digest の公開前品質を検査する gate。"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

from tools.dedup import extract_source_date_from_url
from tools.generate_pages import parse_articles, parse_frontmatter

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_issue_date(value: str) -> date:
    if not _DATE_RE.match(value):
        raise ValueError(f"date は YYYY-MM-DD で指定してください: {value}")
    return date.fromisoformat(value)


def validate_summary_hero(summary_path: Path) -> list[str]:
    """LP hero がブランド文言 fallback に落ちないための短文句を検査する。"""
    if not summary_path.exists():
        return [f"Summary digest が存在しません: {summary_path}"]
    fm, _body = parse_frontmatter(summary_path.read_text(encoding="utf-8-sig", errors="replace"))
    left = (fm.get("hero_left") or "").strip()
    right = (fm.get("hero_right") or "").strip()
    if left and right:
        return []
    return [
        f"{summary_path}: frontmatter hero_left / hero_right が不足しています。",
        "このままでは LP TODAY'S THEME 見出しが「時勢を掴み、日々に新たに。」へ fallback します。",
    ]


def _stale_source_url_errors(*, issue: date, label: str, title: str, url: str) -> list[str]:
    src_date = extract_source_date_from_url(url)
    if src_date is None or src_date >= issue:
        return []
    age = (issue - src_date).days
    return [
        f"{label}: source URL date {src_date.isoformat()} is {age} day(s) older than issue {issue.isoformat()}: {title}",
        f"  url={url}",
    ]


def validate_digest_source_freshness(digest_root: Path, issue: date) -> list[str]:
    """当日カテゴリ digest の記事 URL パス日付が前日以前なら落とす。"""
    errs: list[str] = []
    for md in sorted(digest_root.glob(f"*/*{issue.isoformat()}*.md")):
        if md.parent.name in {"Summary", "DeepDive"}:
            continue
        fm, body = parse_frontmatter(md.read_text(encoding="utf-8-sig", errors="replace"))
        cat = fm.get("categoryId") or fm.get("category") or md.parent.name
        for idx, article in enumerate(parse_articles(body), 1):
            url = article.get("source_url") or ""
            errs.extend(_stale_source_url_errors(
                issue=issue,
                label=f"{md} [{cat} #{idx:02d}]",
                title=article.get("title") or "",
                url=url,
            ))
    return errs


def validate_jsonl_source_freshness(jsonl_path: Path, issue: date) -> list[str]:
    """data/articles.jsonl の当日 record URL パス日付が前日以前なら落とす。"""
    if not jsonl_path.exists():
        return [f"articles jsonl が存在しません: {jsonl_path}"]
    errs: list[str] = []
    for lineno, line in enumerate(jsonl_path.read_text(encoding="utf-8-sig", errors="replace").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record: dict[str, Any] = json.loads(line)
        except json.JSONDecodeError as exc:
            return [f"{jsonl_path}:{lineno}: JSON decode error: {exc}"]
        if record.get("date") != issue.isoformat():
            continue
        errs.extend(_stale_source_url_errors(
            issue=issue,
            label=f"{jsonl_path}:{lineno} [{record.get('genre', '')}]",
            title=record.get("title") or "",
            url=record.get("url") or "",
        ))
    return errs


def validate_daily_quality(
    *,
    issue_date: str,
    digest_root: Path = Path("digest"),
    jsonl_path: Path = Path("data") / "articles.jsonl",
) -> list[str]:
    """指定日の Summary hero と記事 URL 鮮度をまとめて検査する。"""
    issue = _parse_issue_date(issue_date)
    errs: list[str] = []
    errs.extend(validate_summary_hero(digest_root / "Summary" / f"{issue.isoformat()}.md"))
    errs.extend(validate_digest_source_freshness(digest_root, issue))
    errs.extend(validate_jsonl_source_freshness(jsonl_path, issue))
    return errs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="日次 digest の公開前品質を検査します。")
    parser.add_argument("--date", required=True, help="検査対象日 YYYY-MM-DD")
    parser.add_argument("--digest-root", type=Path, default=Path("digest"))
    parser.add_argument("--jsonl", type=Path, default=Path("data") / "articles.jsonl")
    args = parser.parse_args(argv)

    errs = validate_daily_quality(
        issue_date=args.date,
        digest_root=args.digest_root,
        jsonl_path=args.jsonl,
    )
    if errs:
        for err in errs:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1
    print(f"PASS: daily quality OK ({args.date})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
