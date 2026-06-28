#!/usr/bin/env python3
"""カテゴリ digest の記事 bullet を事実・背景・展望の3層へ正規化する。"""
from __future__ import annotations

import argparse
import difflib
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

ROLE_PREFIXES = ("【事実・概要】：", "【背景・要点】：", "【影響・展望】：")
LEGACY_ROLE_PREFIXES = ("【事実】：", "【背景】：", "【展望】：")
_CATEGORY_DIRS = {
    "ai",
    "economy",
    "fx",
    "game",
    "it-consulting",
    "manufacturing",
    "mobility",
}
_ARTICLE_START_RE = re.compile(r"(?m)^###\s+")
_BULLET_RE = re.compile(r"^- (?P<body>.+)$")
_URL_RE = re.compile(r"https?://[^\s)>\"]+")
_WIKI_RE = re.compile(r"\[\[[^\]]+\]\]")
_BOLD_RE = re.compile(r"\*\*[^*\r\n]+\*\*")
_UNDERLINE_RE = re.compile(r"__[^_\r\n]+__")


@dataclass(frozen=True)
class RewriteReport:
    changed_articles: int
    total_articles: int
    total_bullets: int


def collect_digest_targets(digest_root: Path) -> list[Path]:
    """`digest/Summary` を除外し、カテゴリ別 digest Markdown だけを返す。"""
    root = Path(digest_root)
    targets: list[Path] = []
    for path in sorted(root.rglob("*.md")):
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        if not rel.parts:
            continue
        if rel.parts[0].casefold() not in _CATEGORY_DIRS:
            continue
        targets.append(path)
    return targets


def _split_article_blocks(text: str) -> list[tuple[int, int]]:
    starts = [m.start() for m in _ARTICLE_START_RE.finditer(text)]
    blocks: list[tuple[int, int]] = []
    for idx, start in enumerate(starts):
        end = starts[idx + 1] if idx + 1 < len(starts) else len(text)
        blocks.append((start, end))
    return blocks


def _strip_existing_prefix(body: str) -> str:
    s = body.strip()
    for prefix in (*ROLE_PREFIXES, *LEGACY_ROLE_PREFIXES):
        if s.startswith(prefix):
            return s[len(prefix):].strip()
        ascii_prefix = prefix.replace("：", ":")
        if s.startswith(ascii_prefix):
            return s[len(ascii_prefix):].strip()
    return s


def _normalize_bodies(bodies: list[str]) -> list[str]:
    if not bodies:
        raise ValueError("article bullet count must be at least 1")
    stripped = [_strip_existing_prefix(body) for body in bodies]
    if len(stripped) == 1:
        return [stripped[0], stripped[0], stripped[0]]
    if len(stripped) == 2:
        return [stripped[0], stripped[1], stripped[1]]
    if len(stripped) == 3:
        return stripped
    return [stripped[0], stripped[1], " / ".join(stripped[2:])]


def _rewrite_block(block: str) -> tuple[str, bool, int]:
    lines = block.splitlines(keepends=True)
    bullet_indexes = [
        idx for idx, line in enumerate(lines)
        if _BULLET_RE.match(line.rstrip("\r\n"))
    ]
    if not bullet_indexes:
        return block, False, 0
    bodies: list[str] = []
    line_endings: list[str] = []
    for line_idx in bullet_indexes:
        line = lines[line_idx]
        newline = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
        raw = line[:-len(newline)] if newline else line
        match = _BULLET_RE.match(raw)
        if match:
            bodies.append(match.group("body"))
            line_endings.append(newline)

    changed = False
    normalized = _normalize_bodies(bodies)
    first_newline = line_endings[0] if line_endings else "\n"
    new_bullet_lines = [
        f"- {ROLE_PREFIXES[role_idx]}{body}{first_newline}"
        for role_idx, body in enumerate(normalized)
    ]
    for offset, line_idx in enumerate(bullet_indexes):
        if offset < len(new_bullet_lines):
            new_line = new_bullet_lines[offset]
        else:
            new_line = ""
        if new_line != lines[line_idx]:
            changed = True
            lines[line_idx] = new_line
    if len(bullet_indexes) < 3:
        insert_at = bullet_indexes[-1] + 1
        missing = new_bullet_lines[len(bullet_indexes):]
        lines[insert_at:insert_at] = missing
        changed = True
    return "".join(lines), changed, len(bullet_indexes)


def rewrite_markdown_text(text: str) -> tuple[str, RewriteReport]:
    blocks = _split_article_blocks(text)
    if not blocks:
        return text, RewriteReport(changed_articles=0, total_articles=0, total_bullets=0)

    parts: list[str] = []
    cursor = 0
    changed_articles = 0
    total_bullets = 0
    for start, end in blocks:
        parts.append(text[cursor:start])
        rewritten, changed, bullet_count = _rewrite_block(text[start:end])
        parts.append(rewritten)
        cursor = end
        total_bullets += bullet_count
        if changed:
            changed_articles += 1
    parts.append(text[cursor:])
    return "".join(parts), RewriteReport(
        changed_articles=changed_articles,
        total_articles=len(blocks),
        total_bullets=total_bullets,
    )


def _find_all(pattern: re.Pattern[str], text: str) -> set[str]:
    return set(pattern.findall(text))


def validate_rewrite(before: str, after: str) -> None:
    """URL と強調マーカーを欠落させていないことを確認する。"""
    for label, pattern in [
        ("url", _URL_RE),
        ("wikilink", _WIKI_RE),
        ("bold", _BOLD_RE),
        ("underline", _UNDERLINE_RE),
    ]:
        missing = _find_all(pattern, before) - _find_all(pattern, after)
        if missing:
            raise ValueError(f"rewrite lost {label}: {sorted(missing)[:5]}")


def rewrite_file(path: Path, *, apply: bool) -> RewriteReport:
    before = path.read_text(encoding="utf-8")
    after, report = rewrite_markdown_text(before)
    validate_rewrite(before, after)
    if apply and after != before:
        path.write_text(after, encoding="utf-8", newline="\n")
    return report


def _unified_diff(path: Path, before: str, after: str) -> str:
    return "".join(difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"{path}:before",
        tofile=f"{path}:after",
    ))


def run(paths: Iterable[Path], *, apply: bool, diff_limit: int = 0) -> int:
    total_files = 0
    changed_files = 0
    total_articles = 0
    changed_articles = 0
    emitted_diffs = 0
    for path in paths:
        before = path.read_text(encoding="utf-8")
        after, report = rewrite_markdown_text(before)
        validate_rewrite(before, after)
        total_files += 1
        total_articles += report.total_articles
        changed_articles += report.changed_articles
        if after != before:
            changed_files += 1
            if apply:
                path.write_text(after, encoding="utf-8", newline="\n")
            elif diff_limit > 0 and emitted_diffs < diff_limit:
                print(_unified_diff(path, before, after))
                emitted_diffs += 1
    mode = "apply" if apply else "dry-run"
    print(
        f"[rewrite-bullets-3layer] mode={mode} files={total_files} "
        f"changed_files={changed_files} articles={total_articles} "
        f"changed_articles={changed_articles}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--digest-root", type=Path, default=Path("digest"))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--diff-limit", type=int, default=3)
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args(argv)

    paths = args.paths or collect_digest_targets(args.digest_root)
    return run(paths, apply=args.apply, diff_limit=args.diff_limit)


if __name__ == "__main__":
    raise SystemExit(main())
