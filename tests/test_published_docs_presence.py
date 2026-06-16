#!/usr/bin/env python3
"""日付 docs / per-category docs の実在を publish 前 gate で固定する契約テスト。"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from tools.validate_daily_quality import validate_daily_quality, validate_published_docs_presence


ISSUE = date(2026, 6, 16)  # 火曜日: 7カテゴリすべて配信対象


def _write_file(path: Path, text: str = "<!doctype html>2026-06-16") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_required_docs(root: Path, *, missing_category: str | None = None) -> None:
    _write_file(root / "docs" / ISSUE.isoformat() / "index.html")
    _write_file(root / "docs" / ISSUE.isoformat() / "summary" / "index.html")
    for cat_id in ["fx", "ai", "it", "mobility", "manufacturing", "economy", "game"]:
        if cat_id != missing_category:
            _write_file(root / "docs" / cat_id / ISSUE.isoformat() / "index.html")


def _write_minimal_digest(root: Path) -> None:
    summary = root / "digest" / "Summary" / f"{ISSUE.isoformat()}.md"
    summary.parent.mkdir(parents=True, exist_ok=True)
    summary.write_text(
        "---\n"
        "title: Summary\n"
        f"date: {ISSUE.isoformat()}\n"
        "categoryId: summary\n"
        "weekday: 火曜日\n"
        "hero_left: 実装\n"
        "hero_right: 検証\n"
        "---\n\n"
        "# Summary\n",
        encoding="utf-8",
    )
    deepdive = root / "digest" / "DeepDive" / f"{ISSUE.isoformat()}-DeepDive.md"
    deepdive.parent.mkdir(parents=True, exist_ok=True)
    deepdive.write_text("---\ntitle: DeepDive\n---\n\n# DeepDive\n", encoding="utf-8")
    _write_file(root / "docs" / "deepdive" / ISSUE.isoformat() / "index.html")
    for cat_id, folder in [
        ("fx", "FX"),
        ("ai", "AI"),
        ("it", "IT-Consulting"),
        ("mobility", "Mobility"),
        ("manufacturing", "Manufacturing"),
        ("economy", "Economy"),
        ("game", "Game"),
    ]:
        cat = root / "digest" / folder / f"{ISSUE.isoformat()}-{folder}.md"
        cat.parent.mkdir(parents=True, exist_ok=True)
        cat.write_text(
            "---\n"
            f"title: {folder}\n"
            f"date: {ISSUE.isoformat()}\n"
            f"categoryId: {cat_id}\n"
            "---\n\n"
            f"### [90] {folder} article\n\n"
            f"📅 {ISSUE.isoformat()} 06:00 · 📰 Example · 🔗 [元記事](https://example.com/2026/06/16/{cat_id})\n\n"
            "- [[test]] **test** __test__\n\n"
            "---\n",
            encoding="utf-8",
        )


def test_published_docs_presence_rejects_missing_date_index(tmp_path: Path) -> None:
    """docs/<date>/index.html が欠けた公開物を fatal にする。"""
    _write_required_docs(tmp_path)
    (tmp_path / "docs" / ISSUE.isoformat() / "index.html").unlink()

    errs = validate_published_docs_presence(docs_root=tmp_path / "docs", issue=ISSUE)

    joined = "\n".join(errs)
    assert "日付 docs index が存在しません" in joined
    assert f"docs/{ISSUE.isoformat()}/index.html" in joined.replace("\\", "/")


def test_published_docs_presence_rejects_missing_per_category_docs(tmp_path: Path) -> None:
    """配信対象カテゴリの docs/<cat>/<date>/index.html 欠落を fatal にする。"""
    _write_required_docs(tmp_path, missing_category="game")

    errs = validate_published_docs_presence(docs_root=tmp_path / "docs", issue=ISSUE)

    joined = "\n".join(errs)
    assert "カテゴリ日付 docs が存在しません" in joined
    assert "game" in joined
    assert f"docs/game/{ISSUE.isoformat()}/index.html" in joined.replace("\\", "/")


def test_daily_quality_require_deepdive_runs_published_docs_presence(tmp_path: Path) -> None:
    """runner の deepdive-required gate 経由でも docs 実在検査が走る。"""
    _write_minimal_digest(tmp_path)
    _write_required_docs(tmp_path, missing_category="game")
    data = tmp_path / "data" / "articles.jsonl"
    data.parent.mkdir(parents=True, exist_ok=True)
    data.write_text("", encoding="utf-8")

    errs = validate_daily_quality(
        issue_date=ISSUE.isoformat(),
        digest_root=tmp_path / "digest",
        jsonl_path=data,
        docs_root=tmp_path / "docs",
        audit_root=tmp_path / "data" / "search_audit",
        require_deepdive=True,
    )

    assert "カテゴリ日付 docs が存在しません" in "\n".join(errs)
