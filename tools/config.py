"""News-Grasp 公開 web 配信 / メール生成の設定一元化。

実装ステップ 6 (2026-05-21): tools/generate_pages.py と tools/generate_email.py が
共通で参照する定数を集約。ベース URL は環境変数で上書き可能。

ユーザー決定パラメータ (2026-05-21):
    - OG_DESCRIPTION_MAX = 180  ... og:description 上限文字数
    - OG_IMAGE_WIDTH     = 1120 ... og:image 推奨幅 (px)
    - TOP_RECENT_DAYS    = 7    ... トップに並べる最近の日数
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

BASE_URL: str = os.environ.get(
    "NEWS_GRASP_BASE_URL",
    "https://hidepon-umg.github.io/News-Grasp",
).rstrip("/")

SITE_TITLE: str = "News Grasp"
SITE_DESCRIPTION: str = "時勢を掴み、日々に新たに。"

OG_DESCRIPTION_MAX: int = 180
OG_IMAGE_WIDTH: int = 1120
OG_IMAGE_HEIGHT: int = 587  # 1120 / 1.91 ≈ 587 (OGP 標準 1.91:1)
TOP_RECENT_DAYS: int = 7

DEFAULT_OG_IMAGE: str = f"{BASE_URL}/assets/og/summary.jpg"

# カテゴリ定義: digest frontmatter の categoryId をキーに引く。
# accent / glyph は既存 .obsidian/snippets/news-grasp.css と整合させること。
CATEGORIES: dict[str, dict[str, str]] = {
    "fx":      {"label": "Foreign Exchange",        "jp": "為替",        "accent": "#B8860B", "glyph": "¥"},
    "ai":      {"label": "Artificial Intelligence", "jp": "AI",          "accent": "#8B5CF6", "glyph": "◆"},
    "it":      {"label": "IT Consulting",           "jp": "IT/コンサル", "accent": "#2563EB", "glyph": "▲"},
    "economy": {"label": "Economy",                  "jp": "経済",        "accent": "#047857", "glyph": "●"},
    "game":    {"label": "Game",                     "jp": "ゲーム",      "accent": "#DC2626", "glyph": "◇"},
    "summary": {"label": "Summary",                  "jp": "総括",        "accent": "#475569", "glyph": "★"},
}
