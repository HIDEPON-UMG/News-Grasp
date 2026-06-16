from __future__ import annotations

from pathlib import Path


PROMPT = Path("prompts/newsroom-editor-system.md")


def test_editor_prompt_requires_human_impressions_in_audio_script():
    text = PROMPT.read_text(encoding="utf-8")

    assert "あなたの短い感想" in text
    assert "事実の羅列" in text


def test_editor_prompt_requires_opening_news_greeting_and_no_heading_reading():
    text = PROMPT.read_text(encoding="utf-8")

    assert "朝のニュースをお伝えします" in text
    assert "音声朗読原稿" in text
    assert "読み上げ本文には含めない" in text
