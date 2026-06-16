from __future__ import annotations

from pathlib import Path


PROMPT = Path("prompts/newsroom-editor-system.md")


def test_editor_prompt_requires_human_impressions_in_audio_script():
    text = PROMPT.read_text(encoding="utf-8")

    assert "あなたの短い感想" in text
    assert "事実の羅列" in text
    assert "聞き手に説教しない" in text
    assert "今日の観点・考察" in text


def test_editor_prompt_defines_listener_persona_and_value_contract():
    text = PROMPT.read_text(encoding="utf-8")

    assert "リスナーのペルソナ" in text
    assert "ITコンサル" in text
    assert "事業・技術判断" in text
    assert "次の会話・提案・判断で使える観点" in text


def test_editor_prompt_defines_warm_speaker_strategy():
    text = PROMPT.read_text(encoding="utf-8")

    assert "話し手としての親しみやすさ" in text
    assert "同じニュースを一緒に見ている伴走者" in text
    assert "共感" in text
    assert "小さな感想" in text
    assert "カテゴリ間の橋渡し" in text


def test_editor_prompt_defines_speaker_as_peer_with_self_relevant_reactions():
    text = PROMPT.read_text(encoding="utf-8")

    assert "話者本人のペルソナ" in text
    assert "リスナーと同じ立場" in text
    assert "同僚" in text
    assert "自分事" in text
    assert "どう感じ、どうするべきと考えたか" in text
    assert "小さなエピソード" in text


def test_editor_prompt_requires_opening_news_greeting_and_no_heading_reading():
    text = PROMPT.read_text(encoding="utf-8")

    assert "朝のニュースをお伝えします" in text
    assert "音声朗読原稿" in text
    assert "読み上げ本文には含めない" in text
