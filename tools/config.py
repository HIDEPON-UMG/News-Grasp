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

# Web Push 購読ストア Worker の URL。tools/send_push.py が購読一覧の取得に使う。
# 環境変数 NEWS_GRASP_PUSH_WORKER_URL で上書き可。
# ★ docs/push.js の WORKER_URL 定数と必ず同じ値にすること（client と server の両輪）。
PUSH_WORKER_URL: str = os.environ.get(
    "NEWS_GRASP_PUSH_WORKER_URL",
    "https://news-grasp-push.news-grasp-push.workers.dev",
).rstrip("/")

SITE_TITLE: str = "News Grasp"
SITE_DESCRIPTION: str = "時勢を掴み、日々に新たに。"
SITE_TAGLINE_EN: str = "SIX LENSES ON TODAY"

OG_DESCRIPTION_MAX: int = 180
OG_IMAGE_WIDTH: int = 1120
OG_IMAGE_HEIGHT: int = 587  # 1120 / 1.91 ≈ 587 (OGP 標準 1.91:1)
TOP_RECENT_DAYS: int = 7

DEFAULT_OG_IMAGE: str = f"{BASE_URL}/assets/og/summary.jpg"

# Magazine palette (DESIGN.md と同期)
NAVY: str = "#181C2A"
CREAM: str = "#F0EBE0"
GOLD: str = "#C9A155"
PAPER: str = "#FAF7F0"
PAPER_SOFT: str = "#F2EEE3"
INK: str = "#1A1A1A"
INK_DIM: str = "#5C5A52"
BORDER: str = "#E2DED4"

# カテゴリ定義: digest frontmatter の categoryId をキーに引く。
# economy キーは公開済 URL `/economy/` 互換のため維持 (Claude Design 仕様の `econ` は内部慣用名)。
# accent / glyph / name_en は Claude Design Handoff README に整合させた値。
# 並び順: ナビバー・メール・γ schema 全てで fx → ai → it → mobility → economy → game の順で表示する。
CATEGORIES: dict[str, dict[str, str]] = {
    "fx":       {"label": "Foreign Exchange",        "jp": "為替",          "accent": "#B8860B", "glyph": "¥"},
    "ai":       {"label": "Artificial Intelligence", "jp": "AI",            "accent": "#2D5BB8", "glyph": "◆"},
    "it":       {"label": "IT & Consulting",         "jp": "IT-Consulting", "accent": "#2E6B52", "glyph": "⌗"},
    "mobility": {"label": "Mobility",                "jp": "モビリティ",     "accent": "#3A7B8C", "glyph": "◎"},
    "economy":  {"label": "Economy",                 "jp": "経済",          "accent": "#8E2A19", "glyph": "■"},
    "game":     {"label": "Gaming",                  "jp": "ゲーム",         "accent": "#5E3D8C", "glyph": "▶"},
    "summary":  {"label": "Summary",                 "jp": "総括",          "accent": "#475569", "glyph": "★"},
}
