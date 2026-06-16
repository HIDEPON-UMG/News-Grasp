#!/usr/bin/env python3
"""DeepDive (kind: deepdive) digest が summary に化けないことの契約テスト。

2026-05-31 事故の回帰防止:
  新設した `digest/DeepDive/*.md` が「未知ディレクトリ」のため category 解決で
  無条件 "summary" に既定化され、本物の `digest/Summary/{date}.md` を
  「同一日付 2 本目の summary」でシャドーした。editorial 選択
  (`next(e for e in same_day if e.category_id == "summary")`) が reflection 空の
  DeepDive 側を先に拾い、LP の TODAY'S THEME 見出し・本日のテーマ考察・各カテゴリ
  考察の強調が一斉に消えた。原因は「データ」ではなく generate_pages の category 解決。

ここで守る不変条件 (assert で loud に落とす):
  1. 非カテゴリ digest (kind: deepdive / 未知ディレクトリ) は category_id "summary" を取らない
  2. 実 digest ツリー全体で、同一日付に category_id == "summary" のエントリは高々 1 本
  3. 最新の summary エントリは reflection (考察) を持つ = LP の本日のテーマ考察が空にならない

実行:
    pytest tests/test_deepdive_not_summary.py -v
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.generate_pages import (  # noqa: E402
    _collect_entries,
    build_context,
    scan_digests,
)

_DEEPDIVE_MD = '''---
title: "週次ディープダイブ"
date: "2026-01-02"
kind: deepdive
theme: "生成AIエージェントの普及が大手コンサルと日系SIerの人月課金モデルをどう侵食するか"
---

## 背景

本文。
'''


def test_deepdive_not_classified_as_summary(tmp_path: Path) -> None:
    """kind: deepdive の未知ディレクトリ digest は summary に化けない。"""
    d = tmp_path / "DeepDive"
    d.mkdir()
    f = d / "2026-01-02-DeepDive.md"
    f.write_text(_DEEPDIVE_MD, encoding="utf-8")
    ctx = build_context(f)
    assert ctx.get("category_id") != "summary", (
        f"DeepDive が summary に化けた (本物の Summary をシャドーする): "
        f"category_id={ctx.get('category_id')!r}"
    )


def test_at_most_one_summary_entry_per_date() -> None:
    """実 digest ツリー全体で、同一日付に summary エントリが 2 本以上無いこと。"""
    entries = _collect_entries(scan_digests())
    counts = Counter(e["date"] for e in entries if e["category_id"] == "summary")
    dups = {date: n for date, n in counts.items() if n > 1}
    assert not dups, (
        f"同一日付に summary エントリが複数あります (DeepDive 等が summary に化けて "
        f"本物をシャドーしている可能性): {dups}"
    )


def test_audio_script_is_not_scanned_as_digest(tmp_path: Path) -> None:
    """音声原稿は TTS 入力であり、公開 digest エントリとして扱わない。"""
    summary = tmp_path / "Summary"
    summary.mkdir()
    normal = summary / "2026-06-16.md"
    audio = summary / "2026-06-16-audio-script.md"
    normal.write_text("---\ndate: 2026-06-16\ncategoryId: summary\n---\n\n# Summary\n", encoding="utf-8")
    audio.write_text("---\ndate: 2026-06-16\ncategoryId: summary\ntype: audio-script\n---\n\n# Audio\n", encoding="utf-8")

    scanned = {p.name for p in scan_digests(tmp_path)}

    assert "2026-06-16.md" in scanned
    assert "2026-06-16-audio-script.md" not in scanned


def test_latest_summary_entry_has_reflection() -> None:
    """最新の summary エントリは考察 (reflection) を持つ = LP の本日のテーマ考察が空にならない。"""
    entries = _collect_entries(scan_digests())
    summaries = sorted(
        (e for e in entries if e["category_id"] == "summary"),
        key=lambda e: e["date"],
        reverse=True,
    )
    assert summaries, "summary エントリが 1 本も無い: digest/Summary/ の scan に失敗"
    latest = summaries[0]
    refl = latest.get("reflection") or {}
    assert refl.get("lead") or refl.get("sections"), (
        f"最新 summary entry ({latest['date']}) の reflection が空です。"
        f"LP の TODAY'S THEME / 本日のテーマ考察が標語フォールバック・空欄に化けます。"
    )
