#!/usr/bin/env python3
"""tools/harvest_candidates.py の契約テスト（RSS ハーベスタの不変条件を locked-in）。

意図: 「pubDate を ISO 化できる / 全クエリに when:1d が付く / 上限 50 件/カテゴリ /
出力が dedup.py 入力スキーマ互換」という収集改善 A の不変条件を、ネット非依存
（RSS XML は文字列 fixture）に固定する。

これが壊れると (1) 取得前鮮度判定ができない、(2) when:1d 欠落で古記事を大量に拾う、
(3) 上限なしで 1 カテゴリが候補を埋め尽くす、(4) dedup.py に渡せないフィールド構成、
のいずれかが再発する。
"""
from __future__ import annotations

import json

from tools import harvest_candidates as h
from tools._fetch import FetchResult
from tools.dedup import dedup_candidates, normalize_url

# Google News RSS search feed の最小 fixture（item 2 件）。
RSS_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>"AI" - Google News</title>
    <item>
      <title>OpenAI が新モデルを発表 - Example News</title>
      <link>https://news.google.com/rss/articles/CBMiAAAA1</link>
      <pubDate>Wed, 11 Jun 2026 09:00:00 GMT</pubDate>
      <source url="https://www.example.com">Example News</source>
    </item>
    <item>
      <title>Anthropic raises funding</title>
      <link>https://news.google.com/rss/articles/CBMiAAAA2</link>
      <pubDate>Wed, 11 Jun 2026 12:30:00 +0900</pubDate>
      <source url="https://techexample.co.jp/news">TechExample</source>
    </item>
  </channel>
</rss>"""


# ── ① RSS XML fixture から pubDate parse（ISO 化）─────────────────────────────


def test_parse_rss_pubdate_to_iso() -> None:
    rows = h.parse_rss(RSS_FIXTURE, "ai", "AI when:1d")
    assert len(rows) == 2
    # GMT は UTC ISO に正規化される
    assert rows[0]["pubDate"] == "2026-06-11T09:00:00+00:00"
    # +0900 は UTC へ変換される（12:30 JST = 03:30 UTC）
    assert rows[1]["pubDate"] == "2026-06-11T03:30:00+00:00"


def test_parse_pubdate_invalid_returns_none() -> None:
    assert h._parse_pubdate(None) is None
    assert h._parse_pubdate("not a date") is None


def test_parse_rss_strips_source_suffix() -> None:
    """「記事名 - 発行元」のサフィックスを剥がしてタイトルを正規化する。"""
    rows = h.parse_rss(RSS_FIXTURE, "ai", "AI when:1d")
    assert rows[0]["title"] == "OpenAI が新モデルを発表"  # " - Example News" 剥がし済み
    assert rows[1]["title"] == "Anthropic raises funding"  # サフィックス無しはそのまま


def test_parse_rss_extracts_source_domain() -> None:
    rows = h.parse_rss(RSS_FIXTURE, "ai", "AI when:1d")
    assert rows[0]["source"] == "www.example.com"
    assert rows[1]["source"] == "techexample.co.jp"


def test_parse_rss_malformed_returns_empty() -> None:
    assert h.parse_rss("<not-xml", "ai", "q") == []
    assert h.parse_rss("<rss></rss>", "ai", "q") == []  # channel 無し


# ── ② when:1d 付きクエリ URL 生成 ───────────────────────────────────────────


def test_build_query_always_appends_when_1d() -> None:
    assert h.build_query("AI OR LLM") == "AI OR LLM when:1d"


def test_build_query_does_not_double_when() -> None:
    # 既に when: があれば二重付与しない
    assert h.build_query("foo when:1d") == "foo when:1d"


def test_build_feed_url_contains_when_1d_encoded() -> None:
    url = h.build_feed_url("AI OR LLM")
    # when:1d は URL エンコードされて q に含まれる（when%3A1d）
    assert "when%3A1d" in url
    assert url.startswith("https://news.google.com/rss/search?")
    assert "hl=ja" in url and "ceid=JP%3Aja" in url


def test_all_category_queries_get_when_1d() -> None:
    """全カテゴリのクエリが when:1d を付けて feed URL を生成できる（過去月日付語禁止の担保）。"""
    for cat, q in h.CATEGORY_QUERIES.items():
        built = h.build_query(q)
        assert built.endswith("when:1d") or "when:" in built
        # 過去月日付語が混入していないこと（"2025年" や "May 2025" のような過去年月を入れない）
        assert "2025" not in q and "2024" not in q


# ── ③ 上限 50 件/カテゴリ ────────────────────────────────────────────────────


def _big_rss(n: int) -> str:
    items = "".join(
        f'<item><title>記事{i} - Src</title>'
        f'<link>https://news.google.com/rss/articles/X{i}</link>'
        f'<pubDate>Wed, 11 Jun 2026 09:00:00 GMT</pubDate>'
        f'<source url="https://src{i}.example">Src</source></item>'
        for i in range(n)
    )
    return f'<?xml version="1.0"?><rss><channel>{items}</channel></rss>'


def test_max_per_category_caps_at_50(monkeypatch) -> None:
    # feed が 80 件返しても 50 件で切る
    monkeypatch.setattr(h, "fetch_feed", lambda url, timeout=15.0: _big_rss(80))
    rows = h.harvest_category("ai", max_per_category=50)
    assert len(rows) == 50


def test_max_per_category_default_is_50() -> None:
    assert h.DEFAULT_MAX_PER_CATEGORY == 50


def test_category_query_list_exposes_multiple_focus_queries() -> None:
    """カテゴリごとに複数フォーカスクエリを持てる構造にする。"""
    queries = h.category_queries("ai")
    assert len(queries) >= 3
    assert all("when:" not in q for q in queries)


def test_harvest_category_fetches_all_focus_queries(monkeypatch) -> None:
    """1カテゴリ1 OR クエリだけでなく複数クエリを順に収集する。"""
    seen_urls: list[str] = []

    def fake_fetch(url: str, timeout: float = 15.0) -> str:
        seen_urls.append(url)
        return _big_rss(1)

    monkeypatch.setattr(h, "fetch_feed", fake_fetch)
    rows = h.harvest_category("ai", max_per_category=10)

    assert len(seen_urls) >= 3
    assert rows


def test_harvest_category_includes_registered_rss(monkeypatch) -> None:
    """媒体別 RSS 登録簿の feed も Google News RSS と同じ候補プールに入る。"""
    rss_url = "https://feeds.example.com/ai.xml"
    monkeypatch.setattr(h, "RSS_FEEDS_BY_CATEGORY", {"ai": [rss_url]})
    seen_urls: list[str] = []

    def fake_fetch(url: str, timeout: float = 15.0) -> str:
        seen_urls.append(url)
        return _big_rss(1)

    monkeypatch.setattr(h, "fetch_feed", fake_fetch)
    rows = h.harvest_category("ai", max_per_category=10)

    assert rss_url in seen_urls
    assert any(row.get("feed_url") == rss_url for row in rows)


def test_registered_rss_registry_covers_all_harvest_categories() -> None:
    """全 harvest 対象カテゴリに、検証済み RSS 登録簿の入口を持つ。"""
    assert set(h.RSS_FEEDS_BY_CATEGORY) == set(h.CATEGORY_QUERIES)
    for category in h.HARVEST_CATEGORIES:
        urls = h.RSS_FEEDS_BY_CATEGORY[category]
        assert urls, category
        assert len(urls) == len(set(urls)), category
        assert all(url.startswith(("https://", "http://")) for url in urls)


def test_harvest_category_custom_cap(monkeypatch) -> None:
    monkeypatch.setattr(h, "fetch_feed", lambda url, timeout=15.0: _big_rss(80))
    rows = h.harvest_category("fx", max_per_category=30)
    assert len(rows) == 30


# ── ④ dedup.py 入力スキーマ互換のフィールド構成 ─────────────────────────────


def test_output_schema_compatible_with_dedup() -> None:
    """harvest 出力（title/url 必須）が dedup.py をそのまま通過できることを確認する。

    dedup_candidates は最低 title/url を要求し、url_norm/is_followup/seen_at を付与する。
    harvest の候補がそのまま dedup に流せる = パイプライン接続可能の不変条件。
    """
    rows = h.parse_rss(RSS_FIXTURE, "ai", "AI when:1d")
    # 必須フィールドの存在
    for r in rows:
        assert "title" in r and r["title"]
        assert "url" in r and r["url"]
        assert "source" in r
        assert "category" in r
        assert "pubDate" in r
    # dedup へ通す（freshness_gate なし = ネット非依存。url 一致重複なしなので全件 pass）
    passed, dropped = dedup_candidates(rows, existing=[])
    assert len(passed) == len(rows)
    # dedup が付与する url_norm が正規化済み
    for p in passed:
        assert p["url_norm"] == normalize_url(p["url"])
        assert p["is_followup"] is False


def test_output_dedup_drops_same_url_duplicate() -> None:
    """同一 Google News URL の候補は dedup の url 一致で落ちる（重複接続の担保）。"""
    rows = h.parse_rss(RSS_FIXTURE, "ai", "AI when:1d")
    dup = dict(rows[0])  # 1 件目と同 URL
    passed, dropped = dedup_candidates([rows[0], dup], existing=[])
    assert len(passed) == 1
    assert len(dropped) == 1


# ── ⑤ source catalog / Scrapling / negative filter / audit ────────────────


def test_source_catalog_keeps_rss_feed_compatibility() -> None:
    """source catalog 導入後も既存 RSS 登録簿 API を壊さない。"""
    assert set(h.SOURCE_CATALOG_BY_CATEGORY) == set(h.CATEGORY_QUERIES)
    assert set(h.RSS_FEEDS_BY_CATEGORY) == set(h.CATEGORY_QUERIES)
    assert any(
        src.mode == "scrapling_page"
        for sources in h.SOURCE_CATALOG_BY_CATEGORY.values()
        for src in sources
    )
    assert "https://venturebeat.com/category/ai/feed/" in h.RSS_FEEDS_BY_CATEGORY["ai"]
    assert "https://www.fsa.go.jp/fsaEnNewsList_rss2.xml" in h.RSS_FEEDS_BY_CATEGORY["economy"]


def test_negative_filter_drops_noisy_category_items_without_dropping_policy_news() -> None:
    noisy_rows = [
        {"title": "Lakers beat Celtics in NBA finals", "url": "https://sports.example/game", "source": "sports.example", "category": "game"},
        {"title": "USDJPY technical analysis weekly range forecast", "url": "https://fx.example/ta", "source": "fx.example", "category": "fx"},
        {"title": "Best SaaS crowdfunding campaign launches", "url": "https://it.example/crowdfunding", "source": "it.example", "category": "it"},
        {"title": "EV road test review and buying advice", "url": "https://car.example/review", "source": "car.example", "category": "mobility"},
        {"title": "Toyota invests in EV battery mass production", "url": "https://car.example/investment", "source": "car.example", "category": "mobility"},
        {"title": "Nintendo faces platform policy regulation probe", "url": "https://game.example/policy", "source": "game.example", "category": "game"},
    ]

    kept: list[dict] = []
    dropped: list[dict] = []
    for row in noisy_rows:
        reason = h.negative_filter_reason(row)
        if reason:
            dropped.append({**row, "drop_reason": reason})
        else:
            kept.append(row)

    assert {row["title"] for row in dropped} == {
        "Lakers beat Celtics in NBA finals",
        "USDJPY technical analysis weekly range forecast",
        "Best SaaS crowdfunding campaign launches",
        "EV road test review and buying advice",
    }
    assert {row["title"] for row in kept} == {
        "Toyota invests in EV battery mass production",
        "Nintendo faces platform policy regulation probe",
    }


def test_harvest_category_with_audit_extracts_scrapling_page_and_filters(monkeypatch) -> None:
    """RSS parse 不能でも Scrapling 取得できる source は scrapling_page として候補化する。"""
    source = h.SourceDefinition(
        url="https://register.example/software",
        mode="scrapling_page",
        trust_tier="industry",
        include_keywords=(),
        exclude_keywords=(),
        max_items=5,
    )
    html = """
    <html><head><title>Software headlines</title></head><body>
      <a href="/news/cloud-regulation">Cloud regulation changes enterprise procurement</a>
      <a href="/news/gadget-review">Consumer gadget review roundup</a>
    </body></html>
    """

    monkeypatch.setattr(h, "CATEGORY_QUERY_SETS", {"it": []})
    monkeypatch.setattr(h, "SOURCE_CATALOG_BY_CATEGORY", {"it": [source]})
    monkeypatch.setattr(h, "fetch_scrapling_page", lambda url, timeout=15.0: FetchResult(url=url, status=200, html=html, stage="fetcher", ok=True))

    rows, audit = h.harvest_category_with_audit("it", max_per_category=10)

    assert [row["title"] for row in rows] == ["Cloud regulation changes enterprise procurement"]
    assert rows[0]["url"] == "https://register.example/news/cloud-regulation"
    assert rows[0]["source_mode"] == "scrapling_page"
    assert audit["scrapling_sources_used"] == ["https://register.example/software"]
    assert audit["source_breakdown"]["register.example/software"]["raw"] == 2
    assert audit["negative_filter_dropped"]["it:consumer_gadget_review"] == 1


def test_harvest_category_with_audit_marks_broken_sources(monkeypatch) -> None:
    broken = h.SourceDefinition(
        url="https://broken.example/feed.xml",
        mode="rss",
        trust_tier="industry",
        max_items=5,
    )

    monkeypatch.setattr(h, "CATEGORY_QUERY_SETS", {"ai": []})
    monkeypatch.setattr(h, "SOURCE_CATALOG_BY_CATEGORY", {"ai": [broken]})
    monkeypatch.setattr(h, "fetch_feed", lambda url, timeout=15.0: None)

    rows, audit = h.harvest_category_with_audit("ai", max_per_category=10)

    assert rows == []
    assert audit["broken_sources"] == [
        {"url": "https://broken.example/feed.xml", "mode": "rss", "reason": "fetch_failed"}
    ]
    assert audit["quality_shortfall_reason"] == "filtered_candidates_below_minimum_5"


def test_write_harvest_audit_uses_non_colliding_filename(tmp_path) -> None:
    audit = {
        "category_id": "game",
        "source_breakdown": {},
        "negative_filter_dropped": {},
        "scrapling_sources_used": [],
        "broken_sources": [],
    }

    path = h.write_harvest_audit(tmp_path, "game", audit)

    assert path.name == "harvest-game.json"
    assert json.loads(path.read_text(encoding="utf-8"))["category_id"] == "game"
