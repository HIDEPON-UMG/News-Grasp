from __future__ import annotations

from pathlib import Path
import re

import pytest

from tools import generate_pages
from tools.validate_daily_quality import daily_quality_issue_code, validate_summary_hero
from tools.validate_generation_quality import _validate_summary


ROOT = Path(__file__).resolve().parent.parent
GOOD_HEADLINE = "日米が円買い協調介入、ドル円は一時155円台前半へ"
BAD_PUBLISHED_HEADLINE = "円買い介入とAI値下げ、企業に運用再設計迫る"


def _write_summary(path: Path, *, date: str, frontmatter: str, lead: str) -> Path:
    path.write_text(
        "---\n"
        f"date: {date}\n"
        f"{frontmatter}"
        "---\n\n"
        "## § 本日のテーマ考察\n\n"
        f"> {lead}\n",
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize(
    "headline",
    [
        "広がる入口、狭める境界",
        "承認された技術を運用で測る",
        "動いた数字が現場の採算を映す",
    ],
)
def test_new_summary_rejects_abstract_editorial_slogan(tmp_path: Path, headline: str) -> None:
    summary = _write_summary(
        tmp_path / "2026-08-03.md",
        date="2026-08-03",
        frontmatter=f"hero_headline: '{headline}'\n",
        lead="当日の主要ニュースを整理する。",
    )

    errors = validate_summary_hero(summary)

    assert any("具体的なニュース見出し" in error for error in errors)


@pytest.mark.parametrize(
    "headline",
    [
        BAD_PUBLISHED_HEADLINE,
        "AI導入・防衛クラウド・軽EVが拡大、運用責任の線引き問う",
    ],
)
def test_new_summary_rejects_cross_topic_stitching(tmp_path: Path, headline: str) -> None:
    summary = _write_summary(
        tmp_path / "2026-08-03.md",
        date="2026-08-03",
        frontmatter=f"hero_headline: '{headline}'\n",
        lead="複数カテゴリのニュースを整理する。",
    )

    errors = validate_summary_hero(summary)

    assert any("複数の独立ニュース" in error for error in errors)


def test_new_summary_accepts_one_subject_event_and_result(tmp_path: Path) -> None:
    summary = _write_summary(
        tmp_path / "2026-08-03.md",
        date="2026-08-03",
        frontmatter=f"hero_headline: '{GOOD_HEADLINE}'\n",
        lead="日米の協調介入と為替への影響を整理する。",
    )

    assert validate_summary_hero(summary) == []


def test_new_summary_does_not_accept_legacy_pair_as_substitute(tmp_path: Path) -> None:
    summary = _write_summary(
        tmp_path / "2026-08-03.md",
        date="2026-08-03",
        frontmatter="hero_left: '広がる入口'\nhero_right: '狭める境界'\n",
        lead="当日の主要ニュースを整理する。",
    )

    errors = validate_summary_hero(summary)

    assert any("hero_headline" in error for error in errors)


def test_legacy_summary_pair_remains_renderable(tmp_path: Path) -> None:
    summary = _write_summary(
        tmp_path / "2026-08-01.md",
        date="2026-08-01",
        frontmatter="hero_left: '既存記事'\nhero_right: '後方互換'\n",
        lead="過去号は従来の二分見出しで表示できる。",
    )

    assert validate_summary_hero(summary) == []


def test_renderer_prefers_single_news_headline_and_keeps_legacy_pair() -> None:
    resolver = getattr(generate_pages, "_hero_content", None)
    assert callable(resolver), "単一見出しを正本化するrenderer境界が未実装"

    assert resolver({"hero_headline": GOOD_HEADLINE}) == (GOOD_HEADLINE, "", "")
    assert resolver({"hero_left": "既存記事", "hero_right": "後方互換"}) == (
        "",
        "既存記事",
        "後方互換",
    )


def test_prompts_require_single_story_headline_instead_of_forced_pair() -> None:
    for rel in ("prompts/newsroom-editor-system.md", "prompts/routine-system.md"):
        text = (ROOT / rel).read_text(encoding="utf-8-sig")
        assert "hero_headline" in text, rel
        assert "単一の主役ニュース" in text, rel
        assert "複数の独立ニュース" in text, rel
        assert "`hero_left` / `hero_right` を必ず" not in text, rel


def test_home_templates_have_single_headline_primary_branch() -> None:
    for rel in ("prompts/index-template.html", "prompts/overview-template.html"):
        text = (ROOT / rel).read_text(encoding="utf-8-sig")
        assert "hero_headline" in text, rel
        assert text.index("hero_headline") < text.index("hero_phrase_left"), rel
    assert (ROOT / "prompts/index-template.html").read_text(encoding="utf-8-sig").count("hero_headline") >= 3


def test_obsidian_output_template_uses_single_news_headline() -> None:
    text = (ROOT / "prompts/obsidian-template.md").read_text(encoding="utf-8-sig")
    assert 'hero_headline: "{{HERO_HEADLINE}}"' in text
    assert "HERO_LEFT" not in text
    assert "HERO_RIGHT" not in text


def test_generation_gate_emits_typed_error_for_cross_topic_headline(tmp_path: Path) -> None:
    summary_dir = tmp_path / "digest" / "Summary"
    summary_dir.mkdir(parents=True)
    summary = _write_summary(
        summary_dir / "2026-08-03.md",
        date="2026-08-03",
        frontmatter=f"hero_headline: '{BAD_PUBLISHED_HEADLINE}'\n",
        lead="複数カテゴリのニュースを整理する。",
    )

    errors = _validate_summary(tmp_path, summary.relative_to(tmp_path).as_posix(), "2026-08-03")

    assert any(error.code == "summary_news_headline_invalid" for error in errors)


def test_generation_summary_validator_does_not_raise_on_invalid_issue_argument(tmp_path: Path) -> None:
    summary_dir = tmp_path / "digest" / "Summary"
    summary_dir.mkdir(parents=True)
    summary = _write_summary(
        summary_dir / "2026-08-03.md",
        date="2026-08-03",
        frontmatter=f"hero_headline: '{GOOD_HEADLINE}'\n",
        lead="日米の協調介入と為替への影響を整理する。",
    )

    errors = _validate_summary(tmp_path, summary.relative_to(tmp_path).as_posix(), "invalid-date")

    assert not any(error.code == "summary_news_headline_invalid" for error in errors)


@pytest.mark.parametrize(
    "message",
    [
        "frontmatter hero_headline が不足しています。",
        "hero_headline は複数の独立ニュースを接合せず、単一の主役ニュースにしてください。",
    ],
)
def test_daily_quality_maps_news_headline_errors_to_repair_code(message: str) -> None:
    assert daily_quality_issue_code(message) == "summary_news_headline_invalid"


def test_daily_quality_does_not_classify_field_name_only_as_headline_failure() -> None:
    assert daily_quality_issue_code("debug: hero_headline renderer field present") == "unknown"


def test_home_renderer_outputs_single_headline_without_forced_contrast(tmp_path: Path) -> None:
    entry = {
        "title": "News Grasp #20260803 — Summary",
        "date": "2026-08-03",
        "category_id": "summary",
        "category_label": "Summary",
        "category_jp": "総括",
        "canonical": "https://example.test/2026-08-03/summary/",
        "summary_text": "本日の主要ニュースを整理する。",
        "theme": "",
        "hero_headline": GOOD_HEADLINE,
        "hero_left": "",
        "hero_right": "",
        "reflection": {"lead": "日米の協調介入と為替への影響を整理する。"},
        "articles_count": 0,
        "top_score": 0,
        "top3": [],
    }

    html = generate_pages.build_index([entry], tmp_path).read_text(encoding="utf-8")

    visible_text = re.sub(r"<wbr\s*/?>", "", html)
    assert GOOD_HEADLINE in visible_text
    assert f"{GOOD_HEADLINE} と" not in visible_text


def test_august_third_public_source_uses_single_story_headline() -> None:
    summary = (ROOT / "digest" / "Summary" / "2026-08-03.md").read_text(encoding="utf-8-sig")
    rendered = (ROOT / "docs" / "2026-08-03" / "summary" / "index.html").read_text(encoding="utf-8-sig")

    assert f"hero_headline: '{GOOD_HEADLINE}'" in summary
    assert BAD_PUBLISHED_HEADLINE not in summary
    assert GOOD_HEADLINE in re.sub(r"<wbr\s*/?>", "", rendered)
    assert BAD_PUBLISHED_HEADLINE not in rendered
