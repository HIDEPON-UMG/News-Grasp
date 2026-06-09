#!/usr/bin/env python3
"""公開HTMLのホーム/summary hero退化をpublish前に検出するgate。"""
from __future__ import annotations

import argparse
import re
import sys
from html.parser import HTMLParser
from pathlib import Path


MIN_LEAD_CHARS = 180
_COLOR_PANEL_RE = re.compile(r"width\s*:\s*100%\s*;\s*height\s*:\s*100%\s*;", re.I)


class _ClassTextParser(HTMLParser):
    """class単位でテキストとimg srcを抜き出す最小HTML parser。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._class_stack: list[set[str]] = []
        self.text_by_class: dict[str, list[str]] = {}
        self.img_src_by_class: dict[str, list[str]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        classes = set((attr.get("class") or "").split())
        self._class_stack.append(classes)
        if tag.lower() == "img":
            src = attr.get("src") or ""
            for cls_set in self._class_stack:
                for cls in cls_set:
                    self.img_src_by_class.setdefault(cls, []).append(src)

    def handle_endtag(self, tag: str) -> None:
        if self._class_stack:
            self._class_stack.pop()

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if not text:
            return
        for cls_set in self._class_stack:
            for cls in cls_set:
                self.text_by_class.setdefault(cls, []).append(text)


def _first_text(parser: _ClassTextParser, class_name: str) -> str:
    return " ".join(parser.text_by_class.get(class_name, [])).strip()


def _extract_top_story_block(html: str) -> str:
    marker = 'class="home-featured'
    start = html.find(marker)
    if start < 0:
        return ""
    next_section = html.find("<section", start + len(marker))
    if next_section < 0:
        return html[start:]
    return html[start:next_section]


def validate_public_home(docs_dir: Path, date: str | None = None) -> list[str]:
    """docs/index.html と当日 summary HTML の公開品質エラーを返す。"""
    errors: list[str] = []
    index_path = docs_dir / "index.html"
    if not index_path.exists():
        return [f"docs/index.html が存在しません: {index_path}"]

    html = index_path.read_text(encoding="utf-8-sig", errors="replace")
    parser = _ClassTextParser()
    parser.feed(html)

    top_block = _extract_top_story_block(html)
    if not top_block:
        errors.append("docs/index.html: TOP STORY block (.home-featured*) が見つかりません。")
    else:
        top_parser = _ClassTextParser()
        top_parser.feed(top_block)
        top_imgs = [
            src for values in top_parser.img_src_by_class.values()
            for src in values
            if src
        ]
        if not top_imgs:
            errors.append("docs/index.html: TOP STORY block に <img src=...> がありません。")
        if _COLOR_PANEL_RE.search(top_block):
            errors.append(
                "docs/index.html: TOP STORY が色面fallback "
                "`width: 100%; height: 100%;` に退化しています。"
            )

    home_lead = _first_text(parser, "home-hero__lead")
    if len(home_lead) < MIN_LEAD_CHARS:
        errors.append(
            f"docs/index.html: home-hero__lead が短すぎます "
            f"({len(home_lead)} chars, min={MIN_LEAD_CHARS})。"
        )

    if date:
        summary_path = docs_dir / date / "summary" / "index.html"
        if not summary_path.exists():
            errors.append(f"Summary HTML が存在しません: {summary_path}")
        else:
            summary_html = summary_path.read_text(encoding="utf-8-sig", errors="replace")
            summary_parser = _ClassTextParser()
            summary_parser.feed(summary_html)
            summary_lead = _first_text(summary_parser, "summary-hero__lead")
            if len(summary_lead) < MIN_LEAD_CHARS:
                errors.append(
                    f"{summary_path}: summary-hero__lead が短すぎます "
                    f"({len(summary_lead)} chars, min={MIN_LEAD_CHARS})。"
                )

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="公開HTMLのTOP STORY画像/hero lead退化を検査します。",
    )
    parser.add_argument("--docs-dir", type=Path, default=Path("docs"))
    parser.add_argument("--date", help="検査対象日 YYYY-MM-DD。指定時は summary HTML も検査します。")
    args = parser.parse_args(argv)

    errors = validate_public_home(args.docs_dir, args.date)
    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1

    suffix = f" date={args.date}" if args.date else ""
    print(f"PASS: public home HTML OK ({args.docs_dir}{suffix})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
