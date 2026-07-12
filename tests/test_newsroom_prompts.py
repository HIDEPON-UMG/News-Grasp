#!/usr/bin/env python3
"""Newsroom prompt の Codex 正本契約テスト。"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EDITOR_PROMPT = ROOT / "prompts" / "newsroom-editor-system.md"
REPORTER_PROMPT = ROOT / "prompts" / "newsroom-reporter-system.md"
RUNNER_PROMPT = ROOT / "prompts" / "runner-prompt.md"
ROUTINE_PROMPT = ROOT / "prompts" / "routine-system.md"
DEEPDIVE_RESEARCH_PROMPT = ROOT / "prompts" / "deepdive-research-system.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def test_editor_prompt_forbids_commit_push_docs() -> None:
    text = _read(EDITOR_PROMPT)
    assert "commit / push は一切しない" in text
    assert "git push" in text
    assert "git commit" in text
    assert "docs/` の生成" in text or "docs 生成" in text
    assert "publish gate" in text
    assert "news-grasp-runner.ps1" in text


def test_reporter_prompt_forbids_articles_append() -> None:
    text = _read(REPORTER_PROMPT)
    assert "articles.jsonl" in text
    assert "への append は絶対禁止" in text
    assert "編集長が単一ライター" in text or "編集長の単一ライター" in text


def test_runner_prompt_uses_newsroom_editor_entrypoint() -> None:
    text = _read(RUNNER_PROMPT)
    assert "prompts/newsroom-editor-system.md" in text
    assert "tools.harvest_candidates --category" in text
    assert "date` は号日" in text
    assert "published_date` は記事公開日" in text
    assert "git commit / git push / docs 生成 / publish gate 実行は絶対に行わない" in text
    assert "Web Push も絶対に行わない" in text


def test_active_newsroom_prompts_do_not_reference_legacy_agents() -> None:
    forbidden = [
        r"\bTask\b",
        r"ng-reporter",
        r"ng-deepdive",
        r"Sonnet",
        r"Opus",
        r"claude --print",
        r"\.claude",
    ]
    for path in [EDITOR_PROMPT, REPORTER_PROMPT, RUNNER_PROMPT]:
        text = _read(path)
        assert "prompts/style-guide.md" in text
        for pattern in forbidden:
            assert not re.search(pattern, text), f"{path}: {pattern}"


def test_reporter_prompt_date_and_thumb_contracts() -> None:
    text = _read(REPORTER_PROMPT)
    assert "thumb" in text
    assert "キー省略" in text and "gate FAIL" in text
    assert "fetch_ogp" in text
    assert "号日" in text
    assert "published_date" in text
    assert "date_evidence_source" in text
    assert "記事公開日ではない" in text


def test_reporter_prompt_uses_current_summary_frame_labels() -> None:
    """Reporter は生成段階から公開UIと同じ3層ラベルを使う。"""
    for path in [REPORTER_PROMPT, ROUTINE_PROMPT]:
        text = _read(path)
        for label in ["【事実・概要】：", "【背景・要点】：", "【影響・展望】："]:
            assert label in text, path
        for stale in ["【事実】：", "【背景】：", "【展望】："]:
            assert stale not in text, path


def test_category_summary_prompts_require_sentence_complete_100_chars() -> None:
    """カテゴリートップ hero 用 summary は生成段階でも文単位完結を要求する。"""
    for path in [REPORTER_PROMPT, ROUTINE_PROMPT]:
        text = _read(path)
        for phrase in [
            "2〜3 文・合計 100 字以内",
            "各文は必ず「。」で終える",
            "体言止め・途中終了は禁止",
            "文中で省略記号「…」を使って切らない",
        ]:
            assert phrase in text, path


def test_reporter_prompt_does_not_override_freshness_gate_for_manufacturing() -> None:
    """manufacturing 例外でも validator の鮮度窓を破らせない。"""
    text = _read(REPORTER_PROMPT)

    assert "--max-source-age-days 1" in text
    assert "manufacturing でも freshness gate は破らない" in text
    assert "24h 超 −10 は適用しない" not in text


def test_reporter_prompt_lists_required_coverage_terms_for_all_categories() -> None:
    """search_audit coverage_terms_checked は全カテゴリの必須語を明記する。"""
    text = _read(REPORTER_PROMPT)
    for term in [
        "OpenAI", "Anthropic", "Google", "Apple", "Microsoft", "Meta", "NVIDIA",
        "USDJPY", "EURUSD", "BOJ", "Fed", "ECB",
        "McKinsey", "BCG", "Accenture", "Deloitte", "PwC", "NTT",
        "Tesla", "Waymo", "BYD", "Toyota", "Uber",
        "TSMC", "Samsung", "Intel", "Foxconn",
        "Nikkei", "S&P 500", "SoftBank",
        "Nintendo", "Switch 2", "Sony", "Capcom", "Square Enix",
    ]:
        assert term in text


def test_thumb_prompts_forbid_google_news_proxy_thumbnail() -> None:
    for path in [REPORTER_PROMPT, ROUTINE_PROMPT]:
        text = _read(path)
        assert "Google News 代理サムネ" in text, path
        assert "lh3.googleusercontent.com" in text, path
        assert "thumb: null" in text, path


def test_deepdive_prompt_forbids_flat_line_chart() -> None:
    text = _read(DEEPDIVE_RESEARCH_PROMPT)
    assert "全点同一" in text
    assert "フラットな折れ線" in text
    assert "build が hard fail" in text


def test_reporter_prompt_forbids_homepage_rounded_urls() -> None:
    text = _read(REPORTER_PROMPT)
    assert "媒体トップ URL" in text
    assert "カテゴリトップ URL" in text
    assert "元記事単位の canonical URL" in text


def test_reporter_prompt_allows_article_body_fetch_only_inside_reporter_context() -> None:
    text = _read(REPORTER_PROMPT)
    assert "tools/fetch_article_body.py" in text
    assert "記者のローカル文脈内" in text
    assert "編集長 manifest に全文を含めてはいけない" in text


def test_editor_prompt_has_core_responsibilities() -> None:
    text = _read(EDITOR_PROMPT)
    assert "verify_reporter_output" in text
    assert "再 spawn" not in text
    assert "再実行" in text or "repair" in text
    assert "dedup" in text and "第 2 パス" in text
    assert "categoryId" in text
    assert "codex-deepdive" in text or "DeepDive" in text
    assert "全文 Read 禁止" in text or "全文を Read していない" in text


def test_editor_prompt_sets_reflection_lead_floor_above_validator_threshold() -> None:
    """編集長 prompt は validator 180 字ぎりぎりではなく、余裕を持つ下限を指示する。"""
    text = _read(EDITOR_PROMPT)
    routine_text = _read(ROUTINE_PROMPT)

    assert "lead は 220〜250 字" in text
    assert "validate_summary_reflection" in text
    assert "180文字以上" in text
    assert "lead は 220〜250 字" in routine_text
    assert "validate_summary_reflection" in routine_text
    assert "180文字以上" in routine_text


def test_summary_prompts_generate_theme_lanes_before_display_split() -> None:
    """本日のテーマ考察は表示側の文分割ではなく生成時点で3観点を持つ。"""
    for path in [EDITOR_PROMPT, ROUTINE_PROMPT]:
        text = _read(path)
        for phrase in [
            "theme_lanes",
            '"fact"',
            '"context"',
            '"outlook"',
            "FACT / CONTEXT / OUTLOOK",
            "現在の lead を後から文分割して割り振らない",
        ]:
            assert phrase in text, path


def test_summary_prompts_generate_section_lanes_for_category_boards() -> None:
    """カテゴリ別考察も body からの後処理ではなく role 別本文を生成する。"""
    for path in [EDITOR_PROMPT, ROUTINE_PROMPT]:
        text = _read(path)
        for phrase in [
            '"lanes"',
            "事実・概要",
            "背景・要点",
            "影響・展望",
            "各 section の lanes",
            "Tomorrow Board",
        ]:
            assert phrase in text, path


def test_summary_prompts_bind_category_focus_heading_to_category_hero() -> None:
    """生成段階でカテゴリートップ hero の今日の焦点を § 見出しに持たせる。"""
    for path in [EDITOR_PROMPT, ROUTINE_PROMPT]:
        text = _read(path)
        for phrase in [
            "カテゴリートップ hero の「今日の焦点」",
            "`### §NN {tag} — {focus_title}`",
            "8〜32 字",
            "件数文",
            "記事数・カテゴリ名だけの見出しは禁止",
            "body と lanes は focus_title を説明する",
            "tools.validate_summary_reflection",
        ]:
            assert phrase in text, path


def test_editor_prompt_forbids_unscheduled_summary_sections() -> None:
    """編集長 prompt は非対象カテゴリを休載文でも Summary に載せさせない。"""
    text = _read(EDITOR_PROMPT)
    routine_text = _read(ROUTINE_PROMPT)

    for prompt in [text, routine_text]:
        assert "scheduled_categories" in prompt
        assert "Summary frontmatter の categories / tags は scheduled_categories のみ" in prompt
        assert "非対象カテゴリの section を作らない" in prompt
        assert "休載文で繋ぐ" not in prompt
        assert "sections は必ず 9 件" not in prompt


def test_editor_prompt_audio_script_uses_scheduled_categories_not_fixed_seven() -> None:
    """音声原稿も当日 scheduled_categories だけを巡回し、7カテゴリ固定に戻さない。"""
    text = _read(EDITOR_PROMPT)
    routine_text = _read(ROUTINE_PROMPT)

    for prompt in [text, routine_text]:
        assert "朗読原稿" in prompt
        assert "scheduled_categories 件" in prompt
        assert "カテゴリ巡回 7 件を各" not in prompt


def test_editor_prompt_audio_script_requires_thematic_depth_not_padding() -> None:
    """1000字不足を補足文の水増しではなく、テーマ深掘り不足として扱う。"""
    text = _read(EDITOR_PROMPT)

    required_phrases = [
        "字数不足はテーマ深掘り不足",
        "なぜ今このニュースなのか",
        "どの制約や前提が変わったのか",
        "次に観測すべきシグナル",
        "字数合わせの補足文で埋めない",
    ]
    for phrase in required_phrases:
        assert phrase in text
def test_newsroom_editor_output_fields_are_materialization_payloads() -> None:
    text = (ROOT / "prompts" / "newsroom-editor-system.md").read_text(encoding="utf-8")
    assert "summary_markdown は公開用 Summary Markdown 全文" in text
    assert "append_records は data/articles.jsonl へ materialize する実記事 record" in text
    assert "example.invalid" in text
    assert "完了報告を入れない" in text
