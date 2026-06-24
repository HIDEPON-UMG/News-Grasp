#!/usr/bin/env python3
"""公開必須カテゴリ digest の実在を daily-quality gate で固定する契約テスト。"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from tools.validate_daily_quality import validate_issue_schedule


ISSUE = date(2026, 6, 16)  # 火曜日: 7カテゴリすべて配信対象


def _write_summary(root: Path, *, body: str = "") -> None:
    summary_dir = root / "digest" / "Summary"
    summary_dir.mkdir(parents=True, exist_ok=True)
    (summary_dir / f"{ISSUE.isoformat()}.md").write_text(
        "---\n"
        "title: Summary\n"
        f"date: {ISSUE.isoformat()}\n"
        "categoryId: summary\n"
        "weekday: 火曜日\n"
        "---\n\n"
        "# Summary\n\n"
        f"{body}\n",
        encoding="utf-8",
    )


def _write_category(root: Path, cat_id: str, folder: str) -> None:
    cat_dir = root / "digest" / folder
    cat_dir.mkdir(parents=True, exist_ok=True)
    (cat_dir / f"{ISSUE.isoformat()}-{folder}.md").write_text(
        "---\n"
        f"title: {folder}\n"
        f"date: {ISSUE.isoformat()}\n"
        f"categoryId: {cat_id}\n"
        "---\n\n"
        f"# {folder}\n",
        encoding="utf-8",
    )


def _write_all_scheduled_categories_except(root: Path, missing: str) -> None:
    for cat_id, folder in [
        ("fx", "FX"),
        ("ai", "AI"),
        ("it", "IT-Consulting"),
        ("mobility", "Mobility"),
        ("manufacturing", "Manufacturing"),
        ("economy", "Economy"),
        ("game", "Game"),
    ]:
        if cat_id != missing:
            _write_category(root, cat_id, folder)


def test_issue_schedule_rejects_missing_scheduled_category_digest(tmp_path: Path) -> None:
    """配信対象カテゴリ digest が欠けたら、カテゴリ名つき fatal にする。"""
    _write_summary(tmp_path)
    _write_all_scheduled_categories_except(tmp_path, missing="game")

    errs = validate_issue_schedule(tmp_path / "digest", ISSUE)

    joined = "\n".join(errs)
    assert "scheduled category digest missing" in joined
    assert "game" in joined


def test_issue_schedule_allows_per_category_intentional_pause_marker(tmp_path: Path) -> None:
    """Summary に当該カテゴリ名つき休載理由があれば、その1カテゴリだけ免除する。"""
    _write_summary(tmp_path, body="### Game\n\nGame は本日、正当な休載理由により休載。")
    _write_all_scheduled_categories_except(tmp_path, missing="game")

    assert validate_issue_schedule(tmp_path / "digest", ISSUE) == []


def test_issue_schedule_ignores_unscheduled_category_digest_for_required_presence(tmp_path: Path) -> None:
    """非対象カテゴリ digest が残っていても required missing と混同しない。"""
    issue = date(2026, 6, 24)  # 水曜日: game は非対象
    summary_dir = tmp_path / "digest" / "Summary"
    summary_dir.mkdir(parents=True, exist_ok=True)
    (summary_dir / f"{issue.isoformat()}.md").write_text(
        "---\n"
        "title: Summary\n"
        f"date: {issue.isoformat()}\n"
        "categoryId: summary\n"
        "weekday: 水曜日\n"
        "---\n\n"
        "# Summary\n",
        encoding="utf-8",
    )
    for cat_id, folder in [
        ("fx", "FX"),
        ("ai", "AI"),
        ("it", "IT-Consulting"),
        ("mobility", "Mobility"),
        ("manufacturing", "Manufacturing"),
        ("economy", "Economy"),
        ("game", "Game"),
    ]:
        cat_dir = tmp_path / "digest" / folder
        cat_dir.mkdir(parents=True, exist_ok=True)
        (cat_dir / f"{issue.isoformat()}-{folder}.md").write_text(
            "---\n"
            f"title: {folder}\n"
            f"date: {issue.isoformat()}\n"
            f"categoryId: {cat_id}\n"
            "---\n\n"
            f"# {folder}\n",
            encoding="utf-8",
        )

    assert validate_issue_schedule(tmp_path / "digest", issue) == []
