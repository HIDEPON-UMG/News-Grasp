#!/usr/bin/env python3
"""Summary digest の reflection 欠落を公開前に検出する gate。"""
from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable
from pathlib import Path

from tools.generate_pages import CATEGORIES, TAG_TO_CID, parse_reflection

_DATE_FILE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.md$")
_HEADING_SPLIT_RE = re.compile(r"\s+[—–-]\s+", re.ASCII)
_COUNT_ONLY_RE = re.compile(r"(?:\d+|[一二三四五六七八九十]+)\s*件")
_INLINE_MARK_RE = re.compile(r"\[\[([^\]|]+?)(?:\|([^\]]+))?\]\]|\*\*(.+?)\*\*|__(.+?)__")


def _plain_text(text: str) -> str:
    """Markdown 装飾を外して validator 用の短い素テキストにする。"""
    def repl(match: re.Match[str]) -> str:
        return next((g for g in match.groups() if g), "")

    return _INLINE_MARK_RE.sub(repl, text).strip()


def _split_category_focus(heading: str) -> tuple[str, str]:
    parts = _HEADING_SPLIT_RE.split((heading or "").strip(), maxsplit=1)
    if len(parts) == 1:
        return (parts[0].strip(), "")
    return (parts[0].strip(), parts[1].strip())


def _focus_quality_errors(path: Path, *, num: int, heading: str) -> list[str]:
    label, focus = _split_category_focus(heading)
    cat_id = TAG_TO_CID.get(label)
    if not cat_id:
        return []

    errs: list[str] = []
    label_name = CATEGORIES.get(cat_id, {}).get("jp", cat_id)
    plain = _plain_text(focus)
    if not plain:
        errs.append(
            f"{path}: reflection section §{num:02d} category hero focus missing "
            f"({cat_id}). 見出しは `### §NN {label} — 端的な今日の焦点` 形式にしてください。"
        )
        return errs
    if len(plain) < 8 or len(plain) > 32:
        errs.append(
            f"{path}: reflection section §{num:02d} category hero focus length invalid "
            f"({cat_id}, {len(plain)} chars): {plain}"
        )
    if _COUNT_ONLY_RE.search(plain) or "記事" in plain or "カテゴリ" in plain:
        errs.append(
            f"{path}: reflection section §{num:02d} category hero focus is count/list-like "
            f"({cat_id}): {plain}"
        )
    if plain == label or plain == label_name or plain.startswith(f"{label}は"):
        errs.append(
            f"{path}: reflection section §{num:02d} category hero focus repeats category label "
            f"({cat_id}): {plain}"
        )
    if len(re.findall(r"[、,／/・]", plain)) >= 2:
        errs.append(
            f"{path}: reflection section §{num:02d} category hero focus is too list-like "
            f"({cat_id}): {plain}"
        )
    return errs


def validate_summary_category_focus(
    path: Path,
    *,
    required_category_ids: Iterable[str] | None = None,
) -> list[str]:
    """Summary § 見出しがカテゴリートップ hero の「今日の焦点」正本になるか検査する。"""
    if not path.exists():
        return []
    body = path.read_text(encoding="utf-8-sig", errors="replace")
    reflection = parse_reflection(body)
    sections = reflection.get("sections") or {}
    if not sections:
        return []

    errs: list[str] = []
    present: set[str] = set()
    for num, sec in sorted(sections.items()):
        heading = str((sec or {}).get("heading") or "")
        label, _focus = _split_category_focus(heading)
        cat_id = TAG_TO_CID.get(label)
        if not cat_id:
            continue
        present.add(cat_id)
        errs.extend(_focus_quality_errors(path, num=num, heading=heading))
        if not (sec or {}).get("lanes"):
            errs.append(
                f"{path}: reflection section §{num:02d} category lanes missing ({cat_id}). "
                "カテゴリ別 FACT / CONTEXT / OUTLOOK と hero 焦点を同じ section に紐付けてください。"
            )

    if required_category_ids is not None:
        required = [str(cid).strip().casefold() for cid in required_category_ids if str(cid).strip()]
        for cat_id in required:
            if cat_id not in present:
                label = CATEGORIES.get(cat_id, {}).get("jp", cat_id)
                errs.append(
                    f"{path}: reflection category section missing for scheduled category "
                    f"({cat_id}: {label}). カテゴリートップ hero の今日の焦点を生成できません。"
                )
    return errs


def find_latest_summary(summary_dir: Path) -> Path:
    """digest/Summary 内の最新 YYYY-MM-DD.md を返す。"""
    candidates = sorted(p for p in summary_dir.glob("*.md") if _DATE_FILE_RE.match(p.name))
    if not candidates:
        raise FileNotFoundError(f"Summary digest が見つかりません: {summary_dir}")
    return candidates[-1]


def validate_summary_reflection(path: Path) -> list[str]:
    """Summary digest に LP 用 reflection があるか検査し、エラー一覧を返す。"""
    if not path.exists():
        return [f"Summary digest が存在しません: {path}"]
    body = path.read_text(encoding="utf-8-sig", errors="replace")
    reflection = parse_reflection(body)
    lead = reflection.get("lead") or ""
    sections = reflection.get("sections") or {}
    errs: list[str] = []
    if not lead and not sections:
        errs.extend([
            f"{path}: reflection が空です。",
            "期待形式: `## § 本日のテーマ考察` 直下に lead blockquote、または `### §01 ...` セクションを出力してください。",
            "このままでは LP の TODAY'S THEME / 本日のテーマ考察が空欄またはフォールバックになります。",
        ])
    if len(lead.strip()) < 180:
        errs.extend([
            f"{path}: reflection lead が短すぎます ({len(lead.strip())} chars)。",
            "LP の TODAY'S THEME に出る本文として、`## § 本日のテーマ考察` 直下へ180文字以上の blockquote lead を置いてください。",
        ])
    errs.extend(validate_summary_category_focus(path))
    return errs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="最新または指定日の Summary digest に reflection があることを検査します。",
    )
    parser.add_argument(
        "--summary-dir",
        type=Path,
        default=Path("digest") / "Summary",
        help="Summary digest ディレクトリ (default: digest/Summary)",
    )
    parser.add_argument(
        "--date",
        help="検査対象日 YYYY-MM-DD。未指定なら最新 Summary を検査します。",
    )
    args = parser.parse_args(argv)

    target = args.summary_dir / f"{args.date}.md" if args.date else find_latest_summary(args.summary_dir)
    errs = validate_summary_reflection(target)
    if errs:
        for err in errs:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1

    print(f"PASS: summary reflection OK ({target})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
