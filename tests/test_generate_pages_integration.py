#!/usr/bin/env python3
"""generate_pages.py の統合テスト: 実 build → HTML パース → OGP 7 種を再確認。

WebFetch は使わず urllib + html.parser ベース (既存 tests/test_fetch_ogp.py 流派)。
合成 digest を tmp_path に置いて build_all(--full) で render し、
出力 HTML の <meta> を読み戻して以下を pin する:

    必須 OGP 7 種
        og:type / og:title / og:description / og:image / og:url
        twitter:card / canonical (link rel)
    全て https:// 始まりの絶対 URL
    og:url と canonical が一致
    body[data-category] が digest の categoryId と一致

実行:
    pytest tests/test_generate_pages_integration.py -v
"""
from __future__ import annotations

import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.config import BASE_URL  # noqa: E402
from tools.generate_pages import build_all  # noqa: E402


# ---------- 軽量 HTML パーサ ----------

class _MetaCollector(HTMLParser):
    """<meta property/name> と <link rel="canonical"> と <body data-category> を集める。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.og: dict[str, str] = {}
        self.tw: dict[str, str] = {}
        self.canonical: str | None = None
        self.body_category: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        if tag == "meta":
            prop = a.get("property", "")
            name = a.get("name", "")
            content = a.get("content", "")
            if prop.startswith("og:"):
                self.og[prop] = content
            if name.startswith("twitter:"):
                self.tw[name] = content
        elif tag == "link" and a.get("rel") == "canonical":
            self.canonical = a.get("href", "")
        elif tag == "body":
            self.body_category = a.get("data-category", "")


def _parse_meta(html_text: str) -> _MetaCollector:
    p = _MetaCollector()
    p.feed(html_text)
    return p


# ---------- digest fixtures ----------

# 統合方針 (2026-05-26): category_id=summary は build_all 対象外
# (個別ページ /summary/{date}/ を廃止し /{date}/summary/ に統合)。
# build_all を pin する本テストでは summary を含めない。
_FIXTURE_DIGESTS: list[dict[str, str]] = [
    {
        "category_id": "fx",
        "date": "2026-05-20",
        "title": "News Grasp #20260520 — Foreign Exchange",
    },
    {
        "category_id": "ai",
        "date": "2026-05-20",
        "title": "News Grasp #20260520 — Artificial Intelligence",
    },
]


def _write_digest(root: Path, spec: dict[str, str]) -> Path:
    cat = spec["category_id"]
    date = spec["date"]
    digest_dir = root / "digest" / cat.upper()
    digest_dir.mkdir(parents=True, exist_ok=True)
    path = digest_dir / f"{date}-{cat.upper()}.md"
    path.write_text(
        f"""---
title: "{spec['title']}"
date: {date}
issue: 20260520
weekday: 水
category: {cat.title()}
categoryId: {cat}
---

# {cat.upper()}

> [!summary]
> 統合テスト用サマリ。{cat} カテゴリの本文サマリを 1 行で記述。

---

### [88] テスト記事

📅 {date} 不明 · 📰 Test Source · 🔗 [元記事](https://example.com)

#cat/{cat} #topic/test #score/高

- bullet 1
- bullet 2
- bullet 3
""",
        encoding="utf-8",
    )
    return path


@pytest.fixture(scope="module")
def built_pages(tmp_path_factory) -> dict[str, Path]:
    """3 件 digest を tmp に置き build_all(--full) で render。

    {category_id: out_path} を返す。
    """
    root = tmp_path_factory.mktemp("ngsite")
    docs = root / "docs"
    sources: list[Path] = []
    for spec in _FIXTURE_DIGESTS:
        sources.append(_write_digest(root, spec))
    written = build_all(full=True, docs_root=docs, digests=sources)
    assert len(written) == len(_FIXTURE_DIGESTS), (
        f"expected {len(_FIXTURE_DIGESTS)} pages built, got {len(written)}"
    )
    mapping: dict[str, Path] = {}
    for spec, path in zip(_FIXTURE_DIGESTS, written):
        mapping[spec["category_id"]] = path
    return mapping


# ---------- 統合テスト本体 ----------

def test_all_required_ogp_meta_present(built_pages):
    """og:type / og:title / og:description / og:image / og:url / twitter:card / canonical の 7 種が
    全 build ページに存在する。"""
    for cat, out in built_pages.items():
        html_text = out.read_text(encoding="utf-8")
        meta = _parse_meta(html_text)
        missing = [k for k in (
            "og:type", "og:title", "og:description", "og:image", "og:url",
        ) if k not in meta.og]
        assert not missing, f"{cat}: missing og:* = {missing} (path={out})"
        assert "twitter:card" in meta.tw, f"{cat}: missing twitter:card (path={out})"
        assert meta.canonical, f"{cat}: missing <link rel=canonical> (path={out})"


def test_all_urls_are_absolute_https(built_pages):
    """og:image / og:url / canonical が https:// 始まりの絶対 URL。"""
    for cat, out in built_pages.items():
        meta = _parse_meta(out.read_text(encoding="utf-8"))
        for k in ("og:image", "og:url"):
            v = meta.og[k]
            assert v.startswith("https://"), (
                f"{cat}: {k}={v!r} must be absolute https"
            )
        assert meta.canonical.startswith("https://"), (
            f"{cat}: canonical={meta.canonical!r} must be absolute https"
        )


def test_og_url_matches_canonical(built_pages):
    """og:url と canonical link は完全一致。"""
    for cat, out in built_pages.items():
        meta = _parse_meta(out.read_text(encoding="utf-8"))
        assert meta.og["og:url"] == meta.canonical, (
            f"{cat}: og:url={meta.og['og:url']!r} != canonical={meta.canonical!r}"
        )


def test_og_url_under_base_url(built_pages):
    """og:url は BASE_URL 配下。"""
    for cat, out in built_pages.items():
        meta = _parse_meta(out.read_text(encoding="utf-8"))
        assert meta.og["og:url"].startswith(BASE_URL), (
            f"{cat}: og:url={meta.og['og:url']!r} must start with BASE_URL={BASE_URL}"
        )


def test_og_image_category_fallback(built_pages):
    """合成 digest は thumb が外部ドメイン or 無しなので、og:image は
    {BASE_URL}/assets/og/{cat}.jpg にフォールバックされる。"""
    for cat, out in built_pages.items():
        meta = _parse_meta(out.read_text(encoding="utf-8"))
        expected = f"{BASE_URL}/assets/og/{cat}.jpg"
        assert meta.og["og:image"] == expected, (
            f"{cat}: og:image={meta.og['og:image']!r} expected {expected!r}"
        )


def test_twitter_card_summary_large_image(built_pages):
    """twitter:card は summary_large_image (1120px og:image を活かす)。"""
    for cat, out in built_pages.items():
        meta = _parse_meta(out.read_text(encoding="utf-8"))
        assert meta.tw["twitter:card"] == "summary_large_image", (
            f"{cat}: twitter:card={meta.tw['twitter:card']!r}"
        )


def test_body_data_category_matches(built_pages):
    """<body data-category> が digest の categoryId と一致 (accent CSS 切替の前提)。"""
    for cat, out in built_pages.items():
        meta = _parse_meta(out.read_text(encoding="utf-8"))
        assert meta.body_category == cat, (
            f"{cat}: body[data-category]={meta.body_category!r} expected {cat!r}"
        )


def test_og_description_within_180_chars(built_pages):
    """og:description は ユーザー決定の 180 文字以下に収まる。"""
    from tools.config import OG_DESCRIPTION_MAX
    for cat, out in built_pages.items():
        meta = _parse_meta(out.read_text(encoding="utf-8"))
        desc = meta.og["og:description"]
        assert 0 < len(desc) <= OG_DESCRIPTION_MAX, (
            f"{cat}: og:description length {len(desc)} out of (0, {OG_DESCRIPTION_MAX}]"
        )


def test_out_path_layout(built_pages):
    """出力パスが docs/{cat}/{YYYY-MM-DD}/index.html 形式。"""
    for cat, out in built_pages.items():
        parts = out.parts[-4:]
        assert parts[0] == "docs", f"{cat}: out_path parts={parts}"
        assert parts[1] == cat, f"{cat}: out_path parts={parts}"
        assert parts[2] == "2026-05-20", f"{cat}: out_path parts={parts}"
        assert parts[3] == "index.html", f"{cat}: out_path parts={parts}"
