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


# ── カテゴリトップ 同テーマ連続採用バグの契約テスト (★2026-06-06 ユーザー指摘) ──
# 2026-06-06 AI カテゴリトップで「Microsoft の OpenAI 依存軽減」(06-04+06-05)、
# 「Anthropic IPO 申請」(06-02+06-03)、「Anthropic Claude Opus 4.8」(05-30+05-31)
# の 3 ペアが同テーマ連続採用されていた。dedup.py の find_match は「24h 超は続報
# 扱い」で意図的に通過させており、出力段で連続採用を構造的に弾く必要があった。
# 表示層 (build_category_pages の grid_9/past_7 入力) で _dedupe_by_theme を 1 箇所
# 通す境界集約 (= [[feedback_check_design_principles]] 2 段) で解決し、
# 「同テーマ key が連続する 2 entry に並ばない」不変条件を本テストで locked-in する。

def test_category_top_no_consecutive_same_theme() -> None:
    """カテゴリトップの grid_9 / past_7 で連続 2 entry の theme key が同一にならない。

    なぜ重要か: 2026-06-06 AI カテゴリトップで同社・同テーマ記事の連続採用が 3 ペア
    並んだ実害 (Microsoft / Anthropic IPO / Anthropic Opus 4.8)。dedup.py は 24h 超を
    続報扱いで通すため別 URL の同テーマ記事は意図通り採用される。表示段で連続採用を
    抑止する `_dedupe_by_theme` を境界 1 箇所で適用し、その振る舞いをここで固定する。
    """
    from tools.generate_pages import _dedupe_by_theme

    # 実機 entry 構造: title = digest ヘッダ「News Grasp #YYYYMMDD — Artificial Intelligence」/
    # top_title = digest 内 TOP score 個別記事タイトル。_theme_tokens は top_title を優先する
    # ので、全 entry が「News/Grasp/Artificial/Intelligence」共通で同テーマ判定される問題
    # (2026-06-06 実機調査) を構造的に回避する形を契約として固定する。
    def _e(date: str, top_title: str) -> dict:
        return {"date": date, "category_id": "ai",
                "title": f"News Grasp #{date.replace('-', '')} — Artificial Intelligence",
                "top_title": top_title}

    entries = [
        # 同テーマ重複ペア 1: Microsoft AI モデル発表 (実例: 06-04/06-05 CNBC)
        _e("2026-06-05", "Microsoft unveils new AI models to lessen reliance on OpenAI and lower costs"),
        _e("2026-06-04", "Microsoft unveils MAI-Code-1-Flash — AI coding model to reduce reliance on OpenAI"),
        # 同テーマ重複ペア 2: Anthropic IPO 申請 (実例: 06-02/06-03 CNBC/Fortune)
        _e("2026-06-03", "Anthropic confidentially files SEC S-1 for IPO at $965B valuation surpassing OpenAI"),
        _e("2026-06-02", "Anthropic confidentially files for IPO at $965B valuation after $65B Series H"),
        # 別テーマ (Google DeepMind Gemma) — 並んで残るべき
        _e("2026-06-01", "Google DeepMind launches Gemma 4 12B bringing frontier AI to laptops"),
        # 同テーマ重複ペア 3: Claude Opus 4.8 リリース (実機 05-30/05-31)。T2 は
        # 「Anthropic」を含まず製品名直接 — version 番号 4.8 が dedup.py の数値抽出で
        # ドット分解→1 桁除外され same_event 判定を抜けるため、共通固有 2 緩和が必要。
        _e("2026-05-31", "Anthropic、Claude Opus 4.8リリース — DynamicWorkflowsとDreamingで長期エージェント作業が進化"),
        _e("2026-05-30", "Claude Opus 4.8リリース — コード品質4倍改善、Fast Mode 2.5倍高速・3倍廉価"),
    ]
    out = _dedupe_by_theme(entries, max_window=7)

    # 連続 2 entry が「同テーマ判定」(_is_same_theme_for_display) で被らない
    from tools.dedup import significant_tokens
    from tools.generate_pages import _is_same_theme_for_display

    for i in range(len(out) - 1):
        cw, cn = significant_tokens(out[i]["top_title"])
        nw, nn = significant_tokens(out[i + 1]["top_title"])
        assert not _is_same_theme_for_display(cw, cn, nw, nn), (
            f"連続2 entry が同テーマで並んでいる: "
            f"#{i} {out[i]['top_title'][:50]} / #{i+1} {out[i+1]['top_title'][:50]}"
        )

    # Microsoft / Anthropic IPO / Claude Opus の同テーマペアはそれぞれ 1 件のみ採用
    titles = [e["top_title"] for e in out]
    ms_count = sum(1 for t in titles if "Microsoft" in t)
    assert ms_count <= 1, f"Microsoft 同テーマが {ms_count} 件採用された: {titles}"
    anth_ipo = sum(1 for t in titles if "Anthropic" in t and "IPO" in t)
    assert anth_ipo <= 1, f"Anthropic IPO 同テーマが {anth_ipo} 件採用された: {titles}"
    opus = sum(1 for t in titles if "Claude Opus 4.8" in t)
    assert opus <= 1, f"Claude Opus 4.8 同テーマが {opus} 件採用された: {titles}"


def test_dedupe_by_theme_preserves_distinct_themes() -> None:
    """別テーマの entry はそのまま並びを保つ (フィルタが過剰に落とさない)。

    Jaccard 閾値 0.5 を超えない別社・別テーマの記事は順序保持で全件採用される
    ことを契約として固定し、フィルタが「同テーマ続報の連続だけ」を抑止する境界を
    守らせる (誤検出で legit 記事を落とす退行を防ぐ)。
    """
    from tools.generate_pages import _dedupe_by_theme
    def _e(date: str, top_title: str) -> dict:
        return {"date": date, "category_id": "ai",
                "title": f"News Grasp #{date.replace('-', '')} — Artificial Intelligence",
                "top_title": top_title}

    entries = [
        _e("2026-06-05", "Microsoft unveils MAI-Code-1-Flash AI coding model"),
        _e("2026-06-04", "Google DeepMind launches Gemma 4 12B for laptops"),
        _e("2026-06-03", "Anthropic IPO filing at $965B valuation"),
        _e("2026-06-02", "OpenAI to release GPT-5.6 in June 2026"),
    ]
    out = _dedupe_by_theme(entries, max_window=7)
    assert len(out) == 4, f"別テーマ 4 件は全件残るべき: 残数={len(out)}"
    assert [e["date"] for e in out] == ["2026-06-05", "2026-06-04", "2026-06-03", "2026-06-02"]
