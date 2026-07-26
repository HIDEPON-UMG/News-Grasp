from __future__ import annotations

from pathlib import Path

from tests.test_generation_quality_validator import ISSUE, _write_complete_fixture
from tools.normalize_generated_artifacts import normalize_generated_artifacts
from tools.validate_generation_quality import validate_generation_quality


def test_normalize_adds_missing_date_and_category_from_path(tmp_path: Path) -> None:
    target = tmp_path / "digest" / "AI" / f"{ISSUE}-AI.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(
        b"\xef\xbb\xbf---\r\n"
        b"title: AI\r\n"
        b"---\r\n\r\n"
        b"# AI  \r\n\r\n"
        b"body\r\n"
    )

    result = normalize_generated_artifacts(tmp_path, ISSUE)

    text = target.read_text(encoding="utf-8")
    assert result.normalized_files == [target]
    assert not target.read_bytes().startswith(b"\xef\xbb\xbf")
    assert "\r\n" not in text
    assert "date: 2026-06-16" in text
    assert "category: AI" in text
    assert "categoryId: ai" in text
    assert "AI  \n" not in text


def test_normalize_does_not_repair_placeholder_body(tmp_path: Path) -> None:
    _write_complete_fixture(tmp_path)
    target = tmp_path / "digest" / "AI" / f"{ISSUE}-AI.md"
    target.write_text(
        "---\n"
        "title: AI\n"
        "---\n\n"
        "# AI\n\n"
        "準備中\n",
        encoding="utf-8",
    )

    normalize_generated_artifacts(tmp_path, ISSUE)
    result = validate_generation_quality(tmp_path, ISSUE)

    assert "準備中" in target.read_text(encoding="utf-8")
    assert any(err.code == "placeholder_digest" and err.artifact.endswith("AI.md") for err in result.errors)


def test_normalize_canonicalizes_reporter_read_link_label(tmp_path: Path) -> None:
    target = tmp_path / "digest" / "Mobility" / f"{ISSUE}-Mobility.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "---\n"
        "title: Mobility\n"
        "---\n\n"
        "### [90] Mobility item\n\n"
        "*2026-06-16 07:00 JST · Example · [記事を読む](https://example.com/story)*\n",
        encoding="utf-8",
    )

    result = normalize_generated_artifacts(tmp_path, ISSUE)

    text = target.read_text(encoding="utf-8")
    assert target in result.normalized_files
    assert "[記事を読む]" not in text
    assert "[元記事](https://example.com/story)" in text
