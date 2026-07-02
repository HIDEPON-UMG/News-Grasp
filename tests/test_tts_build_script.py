from __future__ import annotations

from pathlib import Path

from tools.tts import build_script
from tools import repair_audio_script_length


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "tts"


def _valid_script(extra: str = "") -> str:
    return (
        "今日は6月16日です。朝のニュースをお伝えします。"
        "為替 AI IT-Consulting モビリティ 製造 経済 ゲーム。"
        + ("今日は条件設計を確認する日でした。" * 152)
        + extra
        + "今日の観点・考察です。責任分界と供給制約を誰が引き受けるかが焦点です。"
    )


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


def test_cli_returns_nonzero_when_audio_script_validation_fails(tmp_path, monkeypatch):
    script_dir = tmp_path / "Summary"
    script_dir.mkdir()
    (script_dir / "2026-06-16-audio-script.md").write_text(
        "---\ndate: 2026-06-16\n---\n\n"
        "今日は6月16日です。朝のニュースをお伝えします。"
        "為替 AI IT-Consulting モビリティ 製造 経済 ゲーム。",
        encoding="utf-8",
    )
    monkeypatch.setattr(build_script, "SCRIPT_DIR", script_dir)
    monkeypatch.setattr(build_script, "BUILD_DIR", tmp_path / "build")

    assert build_script.main(["2026-06-16"]) == 1


def test_build_uses_scheduled_categories_not_all_categories_on_wednesday(tmp_path, monkeypatch):
    """水曜の音声原稿では非対象 Game をカテゴリ不足にしない。"""
    issue = "2026-06-24"
    script_dir = tmp_path / "Summary"
    script_dir.mkdir()
    (script_dir / f"{issue}-audio-script.md").write_text(
        "---\n"
        f"date: {issue}\n"
        "---\n\n"
        "今日は6月24日です。朝のニュースをお伝えします。"
        "為替 AI IT-Consulting モビリティ 製造 経済。"
        + ("今日は認証と防御と供給網の順番を確認する日でした。" * 105)
        + "今日の観点・考察です。責任分界と供給制約を誰が引き受けるかが焦点です。",
        encoding="utf-8",
    )
    monkeypatch.setattr(build_script, "SCRIPT_DIR", script_dir)
    monkeypatch.setattr(build_script, "BUILD_DIR", tmp_path / "build")

    assert build_script.main([issue]) == 0


def test_effective_length_requires_at_least_2500_chars():
    text = "為替 AI IT-Consulting モビリティ 製造 経済 ゲーム\n" + ("今日は条件設計を確認する日でした。" * 95)

    issues = build_script.validate_script(text)

    assert build_script.effective_char_count(text) < 2500
    assert any("2500〜3000字" in issue for issue in issues)


def test_audio_script_for_date_requires_opening_news_greeting():
    text = "為替 AI IT-Consulting モビリティ 製造 経済 ゲーム\n" + ("今日は条件設計を確認する日でした。" * 160)

    issues = build_script.validate_script(text, date="2026-06-16")

    assert any("冒頭セリフ不足" in issue for issue in issues)


def test_audio_script_rejects_patronizing_listener_guidance():
    text = (
        "今日は6月16日です。朝のニュースをお伝えします。"
        "為替 AI IT-Consulting モビリティ 製造 経済 ゲーム。"
        + ("今日は条件設計を確認する日でした。" * 150)
        + "聞くニュースとしては、細かな数字を全部覚えるより、今日の判断軸を一つ持ち帰ることが大切です。"
        + "落ち着いて追えば、流れは見えてきます。"
    )

    issues = build_script.validate_script(text, date="2026-06-16")

    assert any("上から目線" in issue for issue in issues)


def test_audio_script_requires_final_viewpoints_and_analysis():
    text = (
        "今日は6月16日です。朝のニュースをお伝えします。"
        "為替 AI IT-Consulting モビリティ 製造 経済 ゲーム。"
        + ("今日は条件設計を確認する日でした。" * 160)
        + "以上、6月16日のニュース グラスプでした。"
    )

    issues = build_script.validate_script(text, date="2026-06-16")

    assert any("今日の観点・考察" in issue for issue in issues)


def test_audio_script_rejects_three_or_more_sentences_reused_from_recent_history():
    repeated = (
        "ここは少し身構えます。"
        "朝会で一言添えるなら、誰が説明するかが焦点です。"
        "今日の観点・考察です。"
    )
    current = _valid_script(repeated)
    history = [_valid_script(repeated)]

    issues = build_script.validate_script(current, date="2026-06-16", history_texts=history)

    assert any("過去原稿との同一文" in issue for issue in issues)


def test_audio_script_rejects_prompt_example_copy():
    current = _valid_script("ここは少し意外でした。このニュースは地味ですが、後から効いてきそうです。")

    issues = build_script.validate_script(current, date="2026-06-16")

    assert any("例文コピー" in issue for issue in issues)


def test_audio_script_rejects_repeated_motifs_from_recent_history():
    motifs = "ここは少し地味ですが、あとから効きそうです。今日の軸は誰が説明し誰が運用するかです。"
    current = _valid_script(motifs)
    history = [_valid_script(motifs)]

    issues = build_script.validate_script(current, date="2026-06-16", history_texts=history)

    assert any("過去原稿との定型表現" in issue for issue in issues)


def test_audio_script_rejects_category_template_repetition():
    repeated = (
        "FXでは、見出しの強さだけでなく、誰が説明責任を持つかが焦点です。"
        "AIでは、見出しの強さだけでなく、誰が説明責任を持つかが焦点です。"
        "ITでは、見出しの強さだけでなく、誰が説明責任を持つかが焦点です。"
        "Mobilityでは、見出しの強さだけでなく、誰が説明責任を持つかが焦点です。"
    )
    current = _valid_script(repeated)

    issues = build_script.validate_script(current, date="2026-06-16")

    assert any("カテゴリ別補足の同型反復" in issue for issue in issues)


def test_audio_script_compares_against_one_available_history_day():
    repeated = (
        "ここは少し身構えます。"
        "朝会で一言添えるなら、誰が説明するかが焦点です。"
        "今日の観点・考察です。"
    )

    issues = build_script.validate_script(_valid_script(repeated), date="2026-06-16", history_texts=[_valid_script(repeated)])

    assert any("過去原稿" in issue for issue in issues)


def test_audio_script_allows_first_day_without_history():
    issues = build_script.validate_script(_valid_script(), date="2026-06-16", history_texts=[])

    assert issues == []


def test_repair_audio_script_length_extends_short_script_to_safe_range(tmp_path):
    issue = "2026-06-24"
    summary_dir = tmp_path / "digest" / "Summary"
    summary_dir.mkdir(parents=True)
    short_script = (
        "---\n"
        f"date: {issue}\n"
        "---\n\n"
        "今日は6月24日です。朝のニュースをお伝えします。"
        "ニュース グラスプです。"
        "為替 AI IT-Consulting モビリティ 製造 経済。"
        + ("今日は認証と防御と供給網の順番を確認する日でした。" * 92)
        + "最後に、今日の観点・考察です。責任分界と供給制約を誰が引き受けるかが焦点です。ニュース グラスプでした。"
    )
    target = summary_dir / f"{issue}-audio-script.md"
    target.write_text(short_script, encoding="utf-8")

    assert repair_audio_script_length.repair_file(tmp_path, issue) is True

    repaired = target.read_text(encoding="utf-8")
    repaired_body = repaired.split("---", 2)[2].strip()
    count = build_script.effective_char_count(repaired)
    assert 2600 <= count <= 2800
    assert "ニュース グラスプでした。" not in repaired
    assert "最後に、今日の観点・考察です。" not in repaired
    assert build_script.validate_script(
        repaired_body,
        date=issue,
        required_categories=("fx", "ai", "it", "mobility", "manufacturing", "economy"),
    ) == []


def test_newsroom_editor_prompt_requires_tts_history_and_no_example_copy():
    prompt = (Path(__file__).resolve().parent.parent / "prompts" / "newsroom-editor-system.md").read_text(encoding="utf-8")

    assert "過去 2 日" in prompt
    assert "例文コピー禁止" in prompt
    assert "構成・感想・締めの反復禁止" in prompt


def test_audio_script_heading_is_not_read_by_tts():
    raw = "# ニュース グラスプ #20260616 音声朗読原稿\n\n今日は6月16日です。朝のニュースをお伝えします。"

    normalized = build_script.normalize_for_tts(raw)

    assert "音声朗読原稿" not in normalized
    assert "#20260616" not in normalized
    assert "#" not in normalized
    assert normalized.startswith("今日は6月16日です。朝のニュースをお伝えします。")


def test_frontmatter_title_is_never_read_by_tts():
    raw = (
        "---\n"
        "title: \"News Grasp #20260616 — 音声朗読原稿\"\n"
        "date: 2026-06-16\n"
        "---\n\n"
        "今日は6月16日です。朝のニュースをお伝えします。"
    )

    normalized = build_script.normalize_for_tts(raw)

    assert "News Grasp" not in normalized
    assert "ニュース グラスプ #20260616" not in normalized
    assert "#20260616" not in normalized
    assert "音声朗読原稿" not in normalized
    assert "#" not in normalized


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


def test_normalize_for_tts_applies_pronunciation_overrides_for_known_misreads():
    normalized = build_script.normalize_for_tts("後工程の設計と上方修正が焦点です。")

    assert "あとこうてい" in normalized
    assert "じょうほうしゅうせい" in normalized
    assert "後工程" not in normalized
    assert "上方修正" not in normalized


def test_normalize_for_tts_reads_jalapeno_chip_as_japanese():
    normalized = build_script.normalize_for_tts("OpenAI の新チップ Jalapeño が焦点です。")

    assert "ハラペーニョ" in normalized
    assert "Jalapeño" not in normalized


def test_normalize_for_tts_converts_us_currency_units_to_japanese_units():
    normalized = build_script.normalize_for_tts(
        "新規予約は$17.4Bで、別表記では17.4Bドル。"
        "売上はUS$250M、投資枠は＄1.2T、費用は50Mドル。"
    )

    assert "$17.4B" not in normalized
    assert "17.4Bドル" not in normalized
    assert "US$250M" not in normalized
    assert "＄1.2T" not in normalized
    assert "50Mドル" not in normalized
    assert "174億ドル" in normalized
    assert normalized.count("174億ドル") == 2
    assert "2.5億ドル" in normalized
    assert "1.2兆ドル" in normalized
    assert "5000万ドル" in normalized
