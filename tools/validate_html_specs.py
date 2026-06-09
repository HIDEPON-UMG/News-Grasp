#!/usr/bin/env python3
"""docs/specs HTML が SPEC.html 系フォーマットを守っているか検査する。"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


REQUIRED_SNIPPETS = (
    'class="container"',
    'class="spec-header"',
    'class="spec-eyebrow"',
    'class="tabs"',
    'role="tablist"',
    'class="tab active"',
    'class="tab-panel active"',
    'class="tab-panel"',
    'class="card"',
    'class="playground"',
    'id="copy-prompt"',
)
REQUIRED_TAB_IDS = ("overview", "alternatives", "dataflow", "impl", "playground")
EXTERNAL_ASSET_RE = re.compile(r"<(?:link|script)[^>]+(?:href|src)=\"https?://", re.I)
HEX_RE = re.compile(r"#[0-9A-Fa-f]{3,8}")
CSS_VAR_DEF_RE = re.compile(r"--[a-z0-9-]+\s*:\s*#[0-9A-Fa-f]{3,8}", re.I)


def _line_count(text: str) -> int:
    return len(text.splitlines())


def _is_svg_color_line(line: str) -> bool:
    return any(attr in line for attr in (" fill=\"", " stroke=\"", " stop-color=\""))


def _bad_hex_lines(text: str) -> list[str]:
    errors: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not HEX_RE.search(line):
            continue
        if CSS_VAR_DEF_RE.search(line):
            continue
        if _is_svg_color_line(line):
            continue
        if "rgba(" in line:
            continue
        errors.append(f"line {lineno}: 色直書きは CSS 変数または SVG fill/stroke だけにしてください: {line.strip()}")
    return errors


def validate_spec(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    errors: list[str] = []
    if _line_count(text) < 100:
        errors.append(f"100 行未満です ({_line_count(text)} lines)。100行未満は Markdown のままにしてください。")
    for snippet in REQUIRED_SNIPPETS:
        if snippet not in text:
            errors.append(f"SPEC.html 共通構造がありません: {snippet}")
    for tab_id in REQUIRED_TAB_IDS:
        if f'data-tab="{tab_id}"' not in text or f'id="{tab_id}"' not in text:
            errors.append(f"必須タブが不足しています: {tab_id}")
    if EXTERNAL_ASSET_RE.search(text):
        errors.append("外部 CDN / 外部 script/link 依存があります。単一 HTML にしてください。")
    errors.extend(_bad_hex_lines(text))
    if "document.querySelectorAll('.tab')" not in text:
        errors.append("SPEC.html の tab 切替 JS がありません。")
    if "navigator.clipboard.writeText" not in text:
        errors.append("playground のプロンプトコピー JS がありません。")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="HTML仕様書の共通フォーマットを検査します。")
    parser.add_argument("paths", nargs="*", type=Path, help="検査対象 HTML。未指定なら docs/specs/*.html")
    args = parser.parse_args(argv)
    paths = args.paths or sorted(Path("docs/specs").glob("*.html"))
    if not paths:
        print("html-spec-lint: 該当なし")
        return 0

    failed = False
    for path in paths:
        errors = validate_spec(path)
        if errors:
            failed = True
            print(f"NG: {path}", file=sys.stderr)
            for err in errors:
                print(f"  - {err}", file=sys.stderr)
        else:
            print(f"OK: {path}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
