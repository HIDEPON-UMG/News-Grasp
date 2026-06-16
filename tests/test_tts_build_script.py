from __future__ import annotations

from pathlib import Path

from tools.tts import build_script


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "tts"


def test_good_audio_script_mentions_all_categories_and_length_is_valid():
    text = (FIXTURE_DIR / "good-audio-script.md").read_text(encoding="utf-8")

    assert build_script.validate_script(text) == []


def test_missing_category_reports_category_shortage():
    text = (FIXTURE_DIR / "missing-category.md").read_text(encoding="utf-8")

    issues = build_script.validate_script(text)

    assert any("カテゴリ不足" in issue for issue in issues)


def test_too_short_reports_length_shortage():
    text = (FIXTURE_DIR / "too-short.md").read_text(encoding="utf-8")

    issues = build_script.validate_script(text)

    assert any("字数不足" in issue for issue in issues)


def test_effective_length_requires_at_least_2500_chars():
    text = "為替 AI IT-Consulting モビリティ 製造 経済 ゲーム\n" + ("今日は条件設計を確認する日でした。" * 95)

    issues = build_script.validate_script(text)

    assert build_script.effective_char_count(text) < 2500
    assert any("2500〜3000字" in issue for issue in issues)


def test_audio_script_for_date_requires_opening_news_greeting():
    text = "為替 AI IT-Consulting モビリティ 製造 経済 ゲーム\n" + ("今日は条件設計を確認する日でした。" * 160)

    issues = build_script.validate_script(text, date="2026-06-16")

    assert any("冒頭セリフ不足" in issue for issue in issues)


def test_audio_script_heading_is_not_read_by_tts():
    raw = "# ニュース グラスプ #20260616 音声朗読原稿\n\n今日は6月16日です。朝のニュースをお伝えします。"

    normalized = build_script.normalize_for_tts(raw)

    assert "音声朗読原稿" not in normalized
    assert normalized.startswith("今日は6月16日です。朝のニュースをお伝えします。")


def test_normalize_for_tts_strips_markdown_url_and_wikilink():
    raw = "## 見出し\n- [[OpenAI]] は https://example.com/path を発表しました。"

    normalized = build_script.normalize_for_tts(raw)

    assert "OpenAI" in normalized
    assert "[[" not in normalized
    assert "https://example.com" not in normalized
    assert "##" not in normalized
    assert "- " not in normalized


def test_normalize_for_tts_reads_news_grasp_as_japanese():
    normalized = build_script.normalize_for_tts("News Grasp の朗読版です。")

    assert "ニュース グラスプ" in normalized
    assert "News Grasp" not in normalized
