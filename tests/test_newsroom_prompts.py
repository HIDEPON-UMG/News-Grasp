#!/usr/bin/env python3
"""Newsroom Architecture プロンプト群の契約テスト (Phase 2)。

# 検証する「なぜ重要か」

Newsroom 体制では、編集長 (ng-newsroom editor) と記者 (ng-reporter) と
エース記者 (ng-deepdive) の **責務境界がプロンプト規約だけで担保される**
(物理 hook ブロックは持たない)。境界が崩れると 06-11/06-12 の実害
(カテゴリ間重複・date=記事公開日の誤記・thumb キー欠落・append し忘れ・
Claude が commit/push まで実行) が再発する。本テストは、その責務境界を
プロンプト本文の **必須文言の存在** で locked-in する:

  (a) editor prompt に commit/push/docs 生成の禁止文言がある
  (b) reporter prompt に articles.jsonl append 禁止文言がある
  (c) ng-reporter.md frontmatter が model: sonnet
  (d) ng-deepdive.md frontmatter が model: opus
  (e) runner-prompt.md が newsroom-editor-system.md を入口にする
  (f) reporter prompt に thumb キー必須 / date=号日 の規約文言がある

実行:
  pytest tests/test_newsroom_prompts.py -v
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EDITOR_PROMPT = ROOT / "prompts" / "newsroom-editor-system.md"
REPORTER_PROMPT = ROOT / "prompts" / "newsroom-reporter-system.md"
RUNNER_PROMPT = ROOT / "prompts" / "runner-prompt.md"
LEGACY_PROMPT = ROOT / "prompts" / "runner-prompt-legacy.md"
NG_REPORTER_AGENT = ROOT / ".claude" / "agents" / "ng-reporter.md"
NG_DEEPDIVE_AGENT = ROOT / ".claude" / "agents" / "ng-deepdive.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _frontmatter(text: str) -> dict[str, str]:
    """agent md の YAML frontmatter (--- ... ---) を素朴に key: value で読む。

    値はスカラー前提 (name / description / model / tools)。リスト記法は使わない。
    """
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    assert m is not None, "frontmatter (--- ... ---) が見つからない"
    fm: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        fm[key.strip()] = val.strip()
    return fm


# ── (a) editor prompt: commit/push/docs 生成の禁止文言 ────────────────────────


def test_editor_prompt_forbids_commit_push_docs() -> None:
    """編集長は生成専用。commit/push/docs 生成/publish gate は runner 所有に固定する。"""
    text = _read(EDITOR_PROMPT)
    # commit / push の禁止
    assert "commit / push は一切しない" in text
    assert "git push" in text  # 禁止対象として言及されている
    assert "git commit" in text
    # docs 生成の禁止
    assert "docs/` の生成" in text or "docs 生成" in text
    assert "publish gate" in text
    # 一元管理の所在 (runner) が明記されている
    assert "news-grasp-runner.ps1" in text


# ── (b) reporter prompt: articles.jsonl append 禁止文言 ───────────────────────


def test_reporter_prompt_forbids_articles_append() -> None:
    """記者は articles.jsonl へ絶対に append しない (編集長が単一ライター)。"""
    text = _read(REPORTER_PROMPT)
    assert "articles.jsonl" in text
    assert "への append は絶対禁止" in text
    assert "編集長が単一ライター" in text or "編集長の単一ライター" in text


def test_editor_prompt_is_single_writer_of_articles() -> None:
    """編集長は articles.jsonl の単一ライターであることを明示する。"""
    text = _read(EDITOR_PROMPT)
    assert "単一ライター" in text
    assert "append_after_dedup.py" in text  # 正本 append 経路


# ── (c) ng-reporter.md frontmatter: model sonnet / tools ─────────────────────


def test_ng_reporter_agent_model_sonnet() -> None:
    """ng-reporter は Sonnet・指定 tools を持つ薄いローダであること。"""
    text = _read(NG_REPORTER_AGENT)
    fm = _frontmatter(text)
    assert fm.get("name") == "ng-reporter"
    assert fm.get("model") == "sonnet"
    # 必須 tools がすべて宣言されている
    tools = fm.get("tools", "")
    for tool in ("WebSearch", "WebFetch", "Read", "Write", "Bash", "Grep", "Glob"):
        assert tool in tools, f"ng-reporter の tools に {tool} が無い"
    # 薄いローダ: reporter-system.md を Read させる
    assert "prompts/newsroom-reporter-system.md" in text


# ── (d) ng-deepdive.md frontmatter: model opus ───────────────────────────────


def test_ng_deepdive_agent_model_opus() -> None:
    """ng-deepdive は Opus・deepdive-research-system.md を Read する薄いローダ。"""
    text = _read(NG_DEEPDIVE_AGENT)
    fm = _frontmatter(text)
    assert fm.get("name") == "ng-deepdive"
    assert fm.get("model") == "opus"
    assert "prompts/deepdive-research-system.md" in text
    # commit はしない (runner step 2.9 が git add で拾う) ことを明示
    assert "git commit" in text
    assert "commit" in text and "しない" in text


# ── (e) runner-prompt.md が Newsroom 入口へ切替済みであること ────────────────


def test_runner_prompt_uses_newsroom_editor_entrypoint() -> None:
    """runner-prompt.md は Newsroom 編集長を唯一の生成入口にする。"""
    text = _read(RUNNER_PROMPT)
    assert "prompts/newsroom-editor-system.md" in text
    assert "tools.harvest_candidates --category" in text
    assert "date` は号日" in text
    assert "published_date` は記事公開日" in text
    assert "git commit / git push / docs 生成 / publish gate 実行は絶対に行わない" in text
    assert "Web Push も絶対に行わない" in text
    assert "prompts/routine-system.md` を runner の入口として直接読んではいけません" in text


def test_legacy_prompt_keeps_old_routine_entrypoint() -> None:
    """runner-prompt-legacy.md は切替前の旧 routine 入口を退避している。"""
    legacy = _read(LEGACY_PROMPT)
    legacy_body = re.sub(r"^#\s*旧体制退避.*?\n+", "", legacy, count=1).strip()
    assert "prompts/routine-system.md" in legacy_body
    assert "prompts/newsroom-editor-system.md" not in legacy_body


# ── (f) reporter prompt: thumb キー必須 / date=号日 の規約 ────────────────────


def test_reporter_prompt_thumb_key_required() -> None:
    """thumb キー必須 (段階 1 を必ず実行・キー省略は gate FAIL) の規約がある。"""
    text = _read(REPORTER_PROMPT)
    assert "thumb" in text
    assert "キー省略" in text and "gate FAIL" in text
    assert "fetch_ogp" in text  # 段階 1 を必ず実行


def test_reporter_prompt_date_is_issue_date() -> None:
    """date=号日 / published_date=記事公開日 の分離規約がある (06-12 違反 2 対策)。"""
    text = _read(REPORTER_PROMPT)
    assert "号日" in text
    assert "published_date" in text
    # date は記事公開日ではなく号日であることを明示
    assert "記事公開日ではない" in text


# ── 補強: editor prompt の主要責務 (差し戻し / dedup 第2パス / categoryId) ─────


def test_editor_prompt_has_core_responsibilities() -> None:
    """編集長の中核責務がプロンプトに揃っていること (plan 仕様の主要点)。"""
    text = _read(EDITOR_PROMPT)
    assert "verify_reporter_output" in text          # 機械検証
    assert "再 spawn" in text                          # 差し戻し
    assert "dedup" in text and "第 2 パス" in text     # カテゴリ間 dedup 第2パス
    assert "categoryId" in text                        # 2026-05-16 fallback 対策
    assert "ng-deepdive" in text or "エース記者" in text  # エース記者 spawn
    assert "全文 Read 禁止" in text or "全文を Read していない" in text  # 文脈予算規律
