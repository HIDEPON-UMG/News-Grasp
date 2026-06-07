#!/usr/bin/env python3
"""tools.validate_summary_reflection の契約テスト。"""
from __future__ import annotations

from pathlib import Path

from tools.validate_summary_reflection import (
    find_latest_summary,
    main,
    validate_summary_reflection,
)


def test_validate_summary_reflection_rejects_plain_summary(tmp_path: Path) -> None:
    """本日のテーマ考察 block が無い Summary は明示エラーにする。"""
    summary = tmp_path / "2026-06-08.md"
    summary.write_text(
        "# News Grasp #20260608\n\n"
        "## 総論\n\n"
        "### 本日の3大テーマ\n\n"
        "1. AI と市場のニュース。\n",
        encoding="utf-8",
    )

    errs = validate_summary_reflection(summary)

    assert errs
    assert "reflection が空" in errs[0]
    assert "本日のテーマ考察" in "\n".join(errs)


def test_validate_summary_reflection_accepts_theme_block(tmp_path: Path) -> None:
    """generate_pages.py が読む reflection 形式なら通す。"""
    summary = tmp_path / "2026-06-08.md"
    summary.write_text(
        "# News Grasp #20260608\n\n"
        "## § 本日のテーマ考察\n\n"
        "*AI と市場が同時に動いた一日*\n\n"
        "> [[AI]] と __為替__ の変化が同じ方向を向き、**投資判断**と産業戦略が同時に更新された。\n\n"
        "### §01 総論 — AI と市場の同時進行\n\n"
        "[[AI]] と __市場__ が相互に影響した。\n",
        encoding="utf-8",
    )

    assert validate_summary_reflection(summary) == []


def test_find_latest_summary_uses_yyyy_mm_dd_name(tmp_path: Path) -> None:
    """最新判定は YYYY-MM-DD.md の名前だけで行い、余計な md は無視する。"""
    (tmp_path / "README.md").write_text("ignore", encoding="utf-8")
    old = tmp_path / "2026-06-07.md"
    new = tmp_path / "2026-06-08.md"
    old.write_text("old", encoding="utf-8")
    new.write_text("new", encoding="utf-8")

    assert find_latest_summary(tmp_path) == new


def test_cli_returns_nonzero_for_missing_reflection(tmp_path: Path, capsys) -> None:
    """runner から呼ぶ CLI は欠落時に exit 1 と stderr を返す。"""
    summary_dir = tmp_path / "Summary"
    summary_dir.mkdir()
    (summary_dir / "2026-06-08.md").write_text("# Summary\n", encoding="utf-8")

    rc = main(["--summary-dir", str(summary_dir), "--date", "2026-06-08"])

    captured = capsys.readouterr()
    assert rc == 1
    assert "ERROR:" in captured.err
