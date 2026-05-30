#!/usr/bin/env python3
"""generate_pages.py の OGP メタ契約テスト (TDD 失敗テスト先行)。

実装ステップ 5: 失敗テスト先行。
ユーザー決定パラメータ (2026-05-21):
    - og:description 最大文字数: 180 文字
    - og:image 推奨幅: 1120 px
    - トップに並べる最近の日数: 7 日

ここでは tools/generate_pages.py の build_context() と
tools/config.py の BASE_URL / CATEGORIES / OG_DESCRIPTION_MAX / OG_IMAGE_WIDTH /
TOP_RECENT_DAYS への契約を pin する。本ファイル時点では実装が存在しないため
全テストが ImportError で FAIL するのが正しい状態。

実行:
    pytest tests/test_generate_pages.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 実装が無い時点では ImportError で FAIL する (TDD 期待状態)。
from tools.config import (  # noqa: E402
    BASE_URL,
    CATEGORIES,
    OG_DESCRIPTION_MAX,
    OG_IMAGE_WIDTH,
    TOP_RECENT_DAYS,
)
from tools.generate_pages import build_context  # noqa: E402


SAMPLE_DIGEST = ROOT / "digest" / "FX" / "2026-05-20-FX.md"


# ---------- 設定値の契約 (ユーザー決定パラメータ) ----------

def test_user_param_og_description_max_is_180():
    """2026-05-21 ユーザー決定: og:description 上限 180 文字。"""
    assert OG_DESCRIPTION_MAX == 180


def test_user_param_og_image_width_is_1120():
    """2026-05-21 ユーザー決定: og:image 推奨幅 1120 px。"""
    assert OG_IMAGE_WIDTH == 1120


def test_user_param_top_recent_days_is_7():
    """2026-05-21 ユーザー決定: トップに並べる最近の日数 7 日。"""
    assert TOP_RECENT_DAYS == 7


def test_base_url_is_https():
    """BASE_URL は https:// 始まりであること (絶対 URL 契約の起点)。"""
    assert BASE_URL.startswith("https://"), f"BASE_URL must be HTTPS: {BASE_URL!r}"


def test_categories_include_all_six():
    """カテゴリは fx / ai / it / economy / game / summary の 6 種を含む。"""
    expected = {"fx", "ai", "it", "economy", "game", "summary"}
    assert expected.issubset(set(CATEGORIES.keys())), (
        f"missing categories: {expected - set(CATEGORIES.keys())}"
    )


# ---------- 実 digest を入力にした OGP メタ契約 ----------

@pytest.fixture
def sample_ctx():
    if not SAMPLE_DIGEST.exists():
        pytest.skip(f"sample digest not found: {SAMPLE_DIGEST}")
    return build_context(SAMPLE_DIGEST)


def test_og_image_is_absolute_https(sample_ctx):
    """og:image は必ず https:// で始まる絶対 URL。"""
    assert sample_ctx["og_image"].startswith("https://"), (
        f"og:image must be absolute HTTPS: {sample_ctx['og_image']!r}"
    )


def test_og_url_matches_canonical(sample_ctx):
    """og:url と canonical は一致し、BASE_URL 配下。"""
    assert sample_ctx["og_url"] == sample_ctx["canonical"]
    assert sample_ctx["og_url"].startswith(BASE_URL), (
        f"og:url must live under BASE_URL: {sample_ctx['og_url']!r}"
    )


def test_og_title_not_empty_and_contains_news_grasp(sample_ctx):
    """og:title は非空で、サイト名 'News Grasp' を含む。"""
    assert sample_ctx["og_title"]
    assert "News Grasp" in sample_ctx["og_title"]


def test_og_description_within_180_chars(sample_ctx):
    """og:description は 1 文字以上 180 文字以下。"""
    desc = sample_ctx["og_description"]
    assert 0 < len(desc) <= OG_DESCRIPTION_MAX, (
        f"og:description length {len(desc)} out of (0, {OG_DESCRIPTION_MAX}]: {desc!r}"
    )


def test_og_description_no_newlines(sample_ctx):
    """og:description には改行コードが含まれない (SNS プレビュー崩れ防止)。"""
    desc = sample_ctx["og_description"]
    assert "\n" not in desc
    assert "\r" not in desc


def test_og_type_is_article(sample_ctx):
    """og:type は article。"""
    assert sample_ctx["og_type"] == "article"


def test_twitter_card_is_summary_large_image(sample_ctx):
    """twitter:card は summary_large_image (1120px og:image を活かす)。"""
    assert sample_ctx["twitter_card"] == "summary_large_image"


# ---------- 合成 digest を入力にした fallback 契約 ----------

def _write_minimal_digest(path: Path, category_id: str, has_thumb: bool = False) -> None:
    thumb_line = "![thumb](https://example.com/external.jpg)" if has_thumb else ""
    path.write_text(
        f"""---
title: "News Grasp #20260520 — Foreign Exchange"
date: 2026-05-20
issue: 20260520
weekday: 水
category: Foreign Exchange
categoryId: {category_id}
accent: "#B8860B"
glyph: "¥"
---

# ¥ FX — Foreign Exchange

> [!summary]
> テスト用サマリ本文。

---

### [88] テスト記事

📅 2026-05-20 不明 · 📰 Test Source · 🔗 [元記事](https://example.com)

#cat/fx

{thumb_line}

- bullet 1
- bullet 2
- bullet 3
""",
        encoding="utf-8",
    )


def test_og_image_fallback_for_category_when_no_thumb(tmp_path):
    """frontmatter に og_image なし、本文に自前ドメイン thumb なしなら
    {BASE_URL}/assets/og/{category_id}.jpg を返す (3 段目フォールバック)。"""
    digest = tmp_path / "2026-05-20-FX.md"
    _write_minimal_digest(digest, "fx", has_thumb=False)
    ctx = build_context(digest)
    assert ctx["og_image"] == f"{BASE_URL}/assets/og/fx.jpg", (
        f"expected category fallback but got: {ctx['og_image']!r}"
    )


def test_og_image_fallback_for_each_category(tmp_path):
    """全カテゴリで category-id に対応した fallback URL が出る。"""
    for cat_id in ("fx", "ai", "it", "economy", "game", "summary"):
        digest = tmp_path / f"2026-05-20-{cat_id}.md"
        _write_minimal_digest(digest, cat_id, has_thumb=False)
        ctx = build_context(digest)
        assert ctx["og_image"] == f"{BASE_URL}/assets/og/{cat_id}.jpg", (
            f"category {cat_id}: expected fallback but got {ctx['og_image']!r}"
        )


def test_category_id_derived_from_parent_dir_when_frontmatter_missing(tmp_path):
    """categoryId 欠落のカテゴリ digest は親フォルダ名から cat_id を導出し summary に化けない。

    回帰防止 (2026-05-16 の class of bugs):
      categoryId 欠落 → build_context が無条件 summary 既定化
      → カテゴリ digest が summary 扱い → 同日に reflection 空の重複 summary entry
      → build_summary が空 entry を掴み「準備中」fallback。
    親フォルダ FX から fx を導出できれば、この illegal state を構造的に作れない。
    """
    cat_dir = tmp_path / "FX"
    cat_dir.mkdir()
    digest = cat_dir / "2026-05-20-FX.md"
    # _write_minimal_digest と同等だが categoryId 行を意図的に省く
    digest.write_text(
        """---
title: "News Grasp #20260520 — Foreign Exchange"
date: 2026-05-20
issue: 20260520
weekday: 水
category: Foreign Exchange
accent: "#B8860B"
glyph: "¥"
---

# ¥ FX — Foreign Exchange

> [!summary]
> テスト用サマリ本文。

---

### [88] テスト記事

📰 Test · 🔗 [元記事](https://example.com)

- a
- b
- c
""",
        encoding="utf-8",
    )
    ctx = build_context(digest)
    assert ctx["category_id"] == "fx", (
        f"categoryId 欠落でも親フォルダ FX から fx を導出すべきだが "
        f"{ctx['category_id']!r} になった (summary 誤判定の回帰)"
    )


def test_og_description_truncates_at_180_chars(tmp_path):
    """200 文字超の summary callout でも og:description は 180 文字以下に truncate される。"""
    long_summary = "あ" * 250  # 250 chars
    digest = tmp_path / "2026-05-20-FX.md"
    digest.write_text(
        f"""---
title: "News Grasp #20260520 — Foreign Exchange"
date: 2026-05-20
issue: 20260520
weekday: 水
category: Foreign Exchange
categoryId: fx
accent: "#B8860B"
glyph: "¥"
---

# ¥ FX — Foreign Exchange

> [!summary]
> {long_summary}

---

### [88] テスト記事

📰 Test · 🔗 [元記事](https://example.com)

- a
- b
- c
""",
        encoding="utf-8",
    )
    ctx = build_context(digest)
    assert len(ctx["og_description"]) <= OG_DESCRIPTION_MAX
    # truncate された場合は ellipsis を末尾に持つ想定 (実装契約)
    assert ctx["og_description"].endswith("…") or len(ctx["og_description"]) == OG_DESCRIPTION_MAX


# ---------- URL 設計の契約 ----------

def test_og_url_uses_pretty_url_pattern(sample_ctx):
    """og:url は /{genre}/{YYYY-MM-DD}/ 形式 (trailing slash 必須)。"""
    url = sample_ctx["og_url"]
    assert url.endswith("/"), f"og:url must end with '/': {url!r}"
    # BASE_URL の後ろに /fx/2026-05-20/ のような segments が来る
    rel = url[len(BASE_URL):]
    parts = [p for p in rel.split("/") if p]
    assert len(parts) == 2, f"expected 2 segments (genre/date), got: {parts}"
    assert parts[0] in {"fx", "ai", "it", "economy", "game", "summary"}
    # 日付っぽい形式
    assert len(parts[1]) == 10 and parts[1][4] == "-" and parts[1][7] == "-"
