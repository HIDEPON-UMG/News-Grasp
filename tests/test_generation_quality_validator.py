from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.publish_inventory import CATEGORY_PATHS, scheduled_category_ids
from tools.validate_generation_quality import main, validate_generation_quality


ISSUE = "2026-06-16"


def _frontmatter(title: str, category: str, category_id: str) -> str:
    return (
        "---\n"
        f"title: {title}\n"
        f"date: {ISSUE}\n"
        f"category: {category}\n"
        f"categoryId: {category_id}\n"
        "---\n\n"
    )


def _write_complete_fixture(root: Path) -> None:
    article_lines: list[str] = []
    for idx, cat_id in enumerate(scheduled_category_ids(ISSUE), start=1):
        folder = CATEGORY_PATHS[cat_id]["digest_folder"]
        url = f"https://example.com/{cat_id}/{idx}"
        digest = root / "digest" / folder / f"{ISSUE}-{folder}.md"
        digest.parent.mkdir(parents=True, exist_ok=True)
        digest.write_text(
            _frontmatter(f"{folder} digest", folder, cat_id)
            + f"# {folder}\n\n"
            + "> [!summary]\n> summary body\n\n"
            + f"### [{idx:02d}] {folder} article\n\n"
            + f"📅 {ISSUE} · 📰 Example · 🔗 [元記事]({url})\n\n"
            + "- body line\n",
            encoding="utf-8",
        )
        article_lines.append(json.dumps({"date": ISSUE, "title": f"{folder} article", "url": url}, ensure_ascii=False))

    summary = root / "digest" / "Summary" / f"{ISSUE}.md"
    summary.parent.mkdir(parents=True, exist_ok=True)
    summary.write_text(
        "---\n"
        "title: Summary\n"
        f"date: {ISSUE}\n"
        "category: Summary\n"
        "categoryId: summary\n"
        "hero_left: Left\n"
        "hero_right: Right\n"
        "---\n\n"
        "# Summary\n\n"
        "## § 本日のテーマ考察\n\n"
        "本文です。\n",
        encoding="utf-8",
    )

    deepdive = root / "digest" / "DeepDive" / f"{ISSUE}-DeepDive.md"
    deepdive.parent.mkdir(parents=True, exist_ok=True)
    deepdive.write_text(
        "---\n"
        "title: DeepDive\n"
        f"date: {ISSUE}\n"
        "kind: deepdive\n"
        "---\n\n"
        "## 背景\n\n"
        "本文です。\n",
        encoding="utf-8",
    )

    data = root / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "articles.jsonl").write_text("\n".join(article_lines) + "\n", encoding="utf-8")
    (data / "_status.md").write_text("ok\n", encoding="utf-8")
    (data / "search_audit" / ISSUE).mkdir(parents=True, exist_ok=True)


def test_generation_quality_passes_complete_fixture(tmp_path: Path) -> None:
    _write_complete_fixture(tmp_path)

    result = validate_generation_quality(tmp_path, ISSUE)

    assert result.exit_code == 0
    assert result.errors == []


def test_generation_quality_rejects_missing_category_digest(tmp_path: Path) -> None:
    _write_complete_fixture(tmp_path)
    missing = tmp_path / "digest" / "Game" / f"{ISSUE}-Game.md"
    missing.unlink()

    result = validate_generation_quality(tmp_path, ISSUE)

    assert result.exit_code == 1
    assert any(err.code == "missing_artifact" and err.artifact.endswith("Game.md") for err in result.errors)


def test_generation_quality_rejects_placeholder_digest(tmp_path: Path) -> None:
    _write_complete_fixture(tmp_path)
    target = tmp_path / "digest" / "AI" / f"{ISSUE}-AI.md"
    target.write_text(_frontmatter("AI", "AI", "ai") + "# AI\n\n準備中\n", encoding="utf-8")

    result = validate_generation_quality(tmp_path, ISSUE)

    assert result.exit_code == 1
    assert any(err.code == "placeholder_digest" and err.artifact.endswith("AI.md") for err in result.errors)


def test_generation_quality_rejects_summary_without_hero(tmp_path: Path) -> None:
    _write_complete_fixture(tmp_path)
    summary = tmp_path / "digest" / "Summary" / f"{ISSUE}.md"
    summary.write_text(
        "---\n"
        "title: Summary\n"
        f"date: {ISSUE}\n"
        "category: Summary\n"
        "---\n\n"
        "# Summary\n\n"
        "## § 本日のテーマ考察\n\n本文\n",
        encoding="utf-8",
    )

    result = validate_generation_quality(tmp_path, ISSUE)

    assert result.exit_code == 1
    assert any(err.code == "summary_hero_missing" for err in result.errors)


def test_generation_quality_rejects_missing_deepdive(tmp_path: Path) -> None:
    _write_complete_fixture(tmp_path)
    (tmp_path / "digest" / "DeepDive" / f"{ISSUE}-DeepDive.md").unlink()

    result = validate_generation_quality(tmp_path, ISSUE)

    assert result.exit_code == 1
    assert any(err.code == "missing_artifact" and "DeepDive" in err.artifact for err in result.errors)


def test_generation_quality_rejects_zero_issue_articles(tmp_path: Path) -> None:
    _write_complete_fixture(tmp_path)
    (tmp_path / "data" / "articles.jsonl").write_text(
        json.dumps({"date": "2026-06-15", "title": "old", "url": "https://example.com/old"}) + "\n",
        encoding="utf-8",
    )

    result = validate_generation_quality(tmp_path, ISSUE)

    assert result.exit_code == 1
    assert any(err.code == "articles_issue_empty" for err in result.errors)


def test_generation_quality_json_errors_are_machine_readable(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_complete_fixture(tmp_path)
    (tmp_path / "digest" / "AI" / f"{ISSUE}-AI.md").unlink()

    rc = main(["--date", ISSUE, "--repo-root", str(tmp_path), "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert {"code", "artifact", "category", "retryable"} <= set(payload["errors"][0])


def test_generation_quality_config_error_returns_exit_2(tmp_path: Path) -> None:
    result = validate_generation_quality(tmp_path, "not-a-date")

    assert result.exit_code == 2
    assert any(err.retryable is False for err in result.errors)
