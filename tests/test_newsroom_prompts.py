#!/usr/bin/env python3
"""Newsroom prompt の Codex 正本契約テスト。"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EDITOR_PROMPT = ROOT / "prompts" / "newsroom-editor-system.md"
REPORTER_PROMPT = ROOT / "prompts" / "newsroom-reporter-system.md"
RUNNER_PROMPT = ROOT / "prompts" / "runner-prompt.md"


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
