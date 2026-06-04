#!/usr/bin/env python3
"""`_split_theme_phrases` 契約テスト (Hero h2 fallback の再発防止)。

# 検証する「なぜ重要か」

2026-06-05 朝バッチで LP の Hero h2 が **「時勢を掴み、日々に新たに」** (サイトタグライン
fallback) になっていた事故の恒久対策。真因は ``_split_theme_phrases`` の文字数上限
(14 文字) に長文 theme が引っかかり ("", "") を返し、テンプレ側が ``hero_phrase_left`` /
``hero_phrase_right`` 不在時の fallback「時勢を掴み...」を出していたこと。直近 7 日を
検証した結果、**7 日中 2 日 (06-04, 06-05) で fallback 発火が常態化**していたが見落と
されていた。

本テストは ``feedback_check_design_principles`` の Lv4 契約テストとして、`_split_theme_phrases`
が以下のケースで**必ず非空 (left, right) を返す**ことを locked-in する:

  1. 直近 7 日 (2026-05-30 〜 2026-06-05) の実 theme
  2. LLM が書きうる典型 theme パターン (sep が 「と」「・」「 — 」「、」のいずれか)
  3. ASCII 句読点・小数点を含む theme

実行:
  pytest tests/test_split_theme_phrases.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from generate_pages import _split_theme_phrases  # noqa: E402


# ── 過去 7 日 (実 theme) で必ず非空を locked-in ────────────────────────────────

PAST_7_DAY_THEMES = [
    ("2026-05-30", "AIの産業実装と通貨の均衡破り"),
    ("2026-05-31", "DynamicAIと介入警戒の週末"),
    ("2026-06-01", "課金と臨界点の週"),
    ("2026-06-02", "AIの兆ドル化と株高・円安の三重奏"),
    ("2026-06-03", "AIユニコーンの上場ラッシュと産業の分岐路"),
    ("2026-06-04", "BOJとWarshの6月決戦・AIの内製化と自動運転の量戦"),
    ("2026-06-05", "IPO 三つ巴と160円の壁 — AI バブルか革命か、円安か利上げか"),
]


@pytest.mark.parametrize("date,theme", PAST_7_DAY_THEMES)
def test_past_7_days_theme_does_not_fallback(date: str, theme: str):
    """直近 7 日の実 theme は ("", "") を返さない契約 (Hero h2 fallback 抑制)。"""
    left, right = _split_theme_phrases(theme)
    assert left and right, (
        f"{date} theme={theme!r} で _split_theme_phrases が空を返した "
        f"(Hero h2 が「時勢を掴み...」fallback になる)"
    )
    assert 2 <= len(left), f"left が短すぎる: {left!r}"
    assert 2 <= len(right), f"right が短すぎる: {right!r}"


# ── 典型 LLM パターン (新規 theme で再発しないため) ──────────────────────────


TYPICAL_LLM_PATTERNS = [
    # (description, theme, must_have_left_substr, must_have_right_substr)
    ("「と」区切り (短)", "金利の天井とAIの底入れ", "金利", "AI"),
    ("「・」区切り", "AIの兆ドル化・円安の三重奏", "兆ドル", "円安"),
    ("em dash 区切り (英文混在)", "IPO 三つ巴と160円の壁 — AI バブル", "IPO", "AI"),
    ("em dash 区切り (前後スペースなし)", "金利—AI", "金利", "AI"),
    ("「、」区切り単独", "金利の天井、AIの底入れ", "金利", "AI"),
    ("長文 right の二次短縮", "X要因とA、B、C", "X要因", "A"),
]


@pytest.mark.parametrize("desc,theme,need_l,need_r", TYPICAL_LLM_PATTERNS)
def test_typical_llm_patterns(desc: str, theme: str, need_l: str, need_r: str):
    """LLM が書きうる典型 theme パターンで必ず分割成功する契約。"""
    left, right = _split_theme_phrases(theme)
    assert left and right, f"{desc}: theme={theme!r} で空を返した"
    assert need_l in left, f"{desc}: left={left!r} に {need_l!r} を含まない"
    assert need_r in right, f"{desc}: right={right!r} に {need_r!r} を含まない"


# ── エッジケース ──────────────────────────────────────────────────────────────


def test_empty_returns_empty():
    assert _split_theme_phrases("") == ("", "")


def test_no_separator_returns_empty():
    """区切り無し theme は空 = テンプレ fallback (意図された挙動)。"""
    assert _split_theme_phrases("単一句のテーマ") == ("", "")


def test_left_too_short_returns_empty():
    """1 字 left は採用しない (短すぎ = 文章として不自然)。"""
    assert _split_theme_phrases("Aと長い右側のフレーズ") == ("", "")


def test_returns_within_upper_limit():
    """左右ともに上限 22 字以内で返る契約 (Hero h2 のレイアウト破綻防止)。"""
    theme = "AIの兆ドル化と株高・円安の三重奏"
    left, right = _split_theme_phrases(theme)
    assert 2 <= len(left) <= 22
    assert 2 <= len(right) <= 22
