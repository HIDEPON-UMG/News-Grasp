#!/usr/bin/env python3
"""決定論 RSS ハーベスタ CLI（収集改善 A: 上流の鮮度を構造で担保する）。

## 背景（なぜ作るか）

2026-06-11 実データで dedup の drop 42 件中 36 件（86%）が freshness gate 起因だった。
真因は上流収集にある: `WebSearch` ツールには鮮度フィルタが構造的に無く、検索エンジンが
過去の高被リンク記事を上位返ししてしまう（古記事ばかり拾って後段で大量 drop）。

本 CLI は Google News RSS の search feed に `when:1d` 演算子を必ず付与して
**決定論的に直近 24 時間の候補だけ**を取る（実測で全件 24h 以内）。LLM の検索勘に
頼らない上流ゲートとして、3-A の WebSearch を補完する。

## 出力（dedup.py へ接続できる候補 jsonl）

1 行 1 候補の JSON Lines を stdout に出す。`tools/dedup.py` の入力スキーマに合わせ、
最低限 `title` / `url` を持ち、`source`（発行元ドメイン）・`category`・`pubDate`
（ISO 8601・取得前の鮮度判定に使える）・`query` を付ける。

**重要（canonical URL は CLI では解決しない）**: Google News RSS の `<link>` は
JavaScript 必須のエンコード URL（`https://news.google.com/rss/articles/CBM...`）で、
そのままでは記事 canonical に飛べない。本 CLI は `<source url>`（発行元ドメイン）と
タイトルを jsonl に含めるだけにし、canonical URL の解決は後段の LLM が
「site:<発行元ドメイン> <タイトル断片>」の限定 WebSearch で引く前提とする。
`url` フィールドには Google News のエンコード URL をそのまま入れる（生存確認用）。

## 設定（カテゴリ別 検索クエリ）

`tools/config.py` の `CATEGORIES`（fx/ai/it/mobility/manufacturing/economy/game）に
整合させたクエリ辞書 `CATEGORY_QUERIES` を本ファイル内に最小新設する。クエリは
イベント/エンティティ駆動を基本にし、過去月の日付語は入れない（収集改善 B と同方針）。

## 上限

1 カテゴリあたり **上限 50 件**（`--max-per-category`）。

## ネットワーク

標準は urllib。403/blocked 時のみ `tools/_fetch.fetch_with_escalation` を補助に使う。
RSS XML のパース部（`parse_rss` 等）は純関数として分離し、オフラインでテストできる。

## CLI

```
./.venv/Scripts/python.exe tools/harvest_candidates.py --category ai
./.venv/Scripts/python.exe tools/harvest_candidates.py --all > candidates.jsonl
./.venv/Scripts/python.exe tools/harvest_candidates.py --category fx --max-per-category 30
```
"""
from __future__ import annotations

import argparse
import re
import json
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# tools パッケージ経由（python -m / pytest）と flat 実行（python tools/...）両対応の import。
try:
    from tools._fetch import fetch_with_escalation
    from tools.config import CATEGORIES
except ModuleNotFoundError:  # python tools/harvest_candidates.py で tools/ だけが sys.path
    from _fetch import fetch_with_escalation
    from config import CATEGORIES

# ── 定数 ─────────────────────────────────────────────────────────────────────

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
DEFAULT_MAX_PER_CATEGORY = 50

# Google News RSS の言語/地域パラメータ。日英両方を拾うため日本版を基準にする
# （hl=ja で日本語ニュースを軸に、ceid で en も混在する。英語専用が必要なら別クエリ）。
DEFAULT_HL = "ja"
DEFAULT_GL = "JP"
DEFAULT_CEID = "JP:ja"

# urllib 段で使う UA。
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class SourceDefinition:
    """カテゴリ別 source catalog の 1 エントリ。"""

    url: str
    mode: str = "rss"
    trust_tier: str = "industry"
    include_keywords: tuple[str, ...] = field(default_factory=tuple)
    exclude_keywords: tuple[str, ...] = field(default_factory=tuple)
    max_items: int = 10

# ── カテゴリ別 検索クエリ（config.CATEGORIES に整合）─────────────────────────────
#
# 収集改善 B と同方針: 過去月の日付語（"May 2026" 型）はクエリに入れない。エンティティ
# （企業名・製品名）と イベント語（発表・買収・規制 等）を OR で並べ、`when:1d` で鮮度を
# 担保する（when:1d は build_query で必ず付与するためここには書かない）。
# summary カテゴリは digest 総括用で収集対象外（config には在るが harvest しない）。
CATEGORY_QUERIES: dict[str, str] = {
    "fx": "為替 OR ドル円 OR 円安 OR FX OR 利上げ OR FOMC OR 日銀 OR forex",
    "ai": (
        "生成AI OR LLM OR OpenAI OR Anthropic OR Claude OR Gemini OR "
        '"artificial intelligence" OR "AI model"'
    ),
    "it": (
        "IT OR クラウド OR SaaS OR サイバーセキュリティ OR コンサルティング OR "
        '"enterprise software" OR cybersecurity'
    ),
    "mobility": (
        "自動運転 OR EV OR ロボタクシー OR Waymo OR Tesla OR モビリティ OR "
        '"autonomous driving" OR robotaxi'
    ),
    "manufacturing": (
        "製造 OR 半導体 OR 工場 OR サプライチェーン OR 量産 OR 特許 OR "
        '"semiconductor" OR "supply chain"'
    ),
    "economy": (
        "経済 OR 景気 OR GDP OR インフレ OR 金融政策 OR 決算 OR "
        '"economy" OR "interest rate"'
    ),
    "game": (
        "ゲーム OR Nintendo OR PlayStation OR Steam OR ゲーム業界 OR "
        '"video game" OR "game release"'
    ),
}

CATEGORY_QUERY_SETS: dict[str, list[str]] = {
    "fx": [
        CATEGORY_QUERIES["fx"],
        "ドル円 OR USDJPY OR 日銀 OR BOJ OR 為替介入",
        "FRB OR Fed OR ECB OR 金利 OR forex",
    ],
    "ai": [
        CATEGORY_QUERIES["ai"],
        "OpenAI OR Anthropic OR Claude OR ChatGPT OR Gemini",
        "AIエージェント OR RAG OR LLM OR 生成AI",
    ],
    "it": [
        CATEGORY_QUERIES["it"],
        "SaaS OR クラウド OR enterprise software OR cybersecurity",
        "コンサルティング OR DX OR ガバメントクラウド OR IT投資",
    ],
    "mobility": [
        CATEGORY_QUERIES["mobility"],
        "EV OR SDV OR ADAS OR 自動運転 OR robotaxi",
        "Toyota OR Tesla OR Waymo OR BYD OR ホンダ",
    ],
    "manufacturing": [
        CATEGORY_QUERIES["manufacturing"],
        "半導体 OR TSMC OR Samsung OR Intel OR 量産",
        "工場 OR サプライチェーン OR 製造業 OR 特許",
    ],
    "economy": [
        CATEGORY_QUERIES["economy"],
        "GDP OR インフレ OR 金融政策 OR 決算 OR 日経平均",
        "景気 OR 雇用統計 OR 消費者物価 OR interest rate",
    ],
    "game": [
        CATEGORY_QUERIES["game"],
        "Nintendo OR Switch 2 OR PlayStation OR Steam",
        "ゲーム株 OR gamebiz OR ファミ通 OR Game Watch",
    ],
}

SOURCE_CATALOG_BY_CATEGORY: dict[str, list[SourceDefinition]] = {
    "fx": [
        SourceDefinition("https://www.forexlive.com/feed/", trust_tier="industry", max_items=10),
        SourceDefinition("https://www.federalreserve.gov/feeds/press_monetary.xml", trust_tier="official", max_items=5),
        SourceDefinition("https://www.boj.or.jp/rss/whatsnew.xml", trust_tier="official", max_items=5),
        SourceDefinition("https://www.mof.go.jp/english/news.rss", trust_tier="official", max_items=5),
    ],
    "ai": [
        SourceDefinition("https://techcrunch.com/category/artificial-intelligence/feed/", trust_tier="industry", max_items=10),
        SourceDefinition("https://venturebeat.com/category/ai/feed/", trust_tier="industry", max_items=8),
        SourceDefinition("https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", trust_tier="industry", max_items=8),
        SourceDefinition("https://www.technologyreview.com/feed/", trust_tier="industry", include_keywords=("ai", "artificial intelligence", "openai", "anthropic", "model"), max_items=8),
        SourceDefinition("https://deepmind.google/blog/rss.xml", trust_tier="official", max_items=5),
    ],
    "it": [
        SourceDefinition("https://www.infoq.com/feed/", trust_tier="industry", max_items=10),
        SourceDefinition("https://www.ciodive.com/feeds/news/", trust_tier="industry", max_items=8),
        SourceDefinition("https://www.cybersecuritydive.com/feeds/news/", trust_tier="industry", max_items=8),
        SourceDefinition("https://www.theregister.com/software/headlines.atom", mode="scrapling_page", trust_tier="industry", max_items=8),
    ],
    "mobility": [
        SourceDefinition("https://electrek.co/feed/", trust_tier="industry", max_items=10),
        SourceDefinition("https://insideevs.com/rss/news/all/", trust_tier="industry", max_items=8),
        SourceDefinition("https://insideevs.com/rss/category/autonomous-vehicles/", trust_tier="industry", max_items=6),
        SourceDefinition("https://insideevs.com/rss/category/battery-tech/", trust_tier="industry", max_items=6),
        SourceDefinition("https://feeds.highgearmedia.com/?sites=GreenCarReports&tags=news", trust_tier="industry", max_items=8),
    ],
    "manufacturing": [
        SourceDefinition("https://semiengineering.com/feed/", trust_tier="industry", max_items=10),
        SourceDefinition("https://www.manufacturingdive.com/feeds/news/", trust_tier="industry", max_items=8),
        SourceDefinition("https://www.supplychainbrain.com/rss/articles", trust_tier="industry", max_items=8),
        SourceDefinition("https://www.supplychainbrain.com/rss/topic/1148-technology", trust_tier="industry", max_items=6),
        SourceDefinition("https://semiwiki.com/feed/", trust_tier="industry", max_items=5),
    ],
    "economy": [
        SourceDefinition("https://feeds.bbci.co.uk/news/business/rss.xml", trust_tier="industry", max_items=10),
        SourceDefinition("https://www.federalreserve.gov/feeds/press_all.xml", trust_tier="official", max_items=5),
        SourceDefinition("https://www.boj.or.jp/rss/whatsnew.xml", trust_tier="official", max_items=5),
        SourceDefinition("https://www.mof.go.jp/english/news.rss", trust_tier="official", max_items=5),
        SourceDefinition("https://www.fsa.go.jp/fsaEnNewsList_rss2.xml", trust_tier="official", max_items=5),
        SourceDefinition("https://www.cnbc.com/id/10001147/device/rss/rss.html", trust_tier="industry", max_items=8),
    ],
    "game": [
        SourceDefinition("https://www.gematsu.com/feed/", trust_tier="industry", max_items=10),
        SourceDefinition("https://www.gamesindustry.biz/feed", trust_tier="industry", max_items=8),
        SourceDefinition("https://www.gamedeveloper.com/rss.xml", trust_tier="industry", max_items=8),
        SourceDefinition("https://www.videogameschronicle.com/feed/", trust_tier="industry", max_items=8),
        SourceDefinition("https://www.nintendo.co.uk/news.xml", trust_tier="official", max_items=5),
    ],
}


def _rss_feeds_from_catalog(catalog: dict[str, list[SourceDefinition]]) -> dict[str, list[str]]:
    return {
        category: [source.url for source in sources if source.mode == "rss"]
        for category, sources in catalog.items()
    }


_DEFAULT_RSS_FEEDS_BY_CATEGORY = _rss_feeds_from_catalog(SOURCE_CATALOG_BY_CATEGORY)
RSS_FEEDS_BY_CATEGORY: dict[str, list[str]] = {
    category: list(urls) for category, urls in _DEFAULT_RSS_FEEDS_BY_CATEGORY.items()
}

# 収集対象カテゴリ（config の summary を除く順序保持リスト）。
HARVEST_CATEGORIES: list[str] = [c for c in CATEGORIES if c != "summary"]


def category_queries(category: str) -> list[str]:
    """カテゴリの複数フォーカスクエリを返す。"""
    queries = CATEGORY_QUERY_SETS.get(category)
    if queries is None:
        base = CATEGORY_QUERIES.get(category)
        if base is None:
            raise KeyError(f"未知のカテゴリ: {category}（CATEGORY_QUERIES に未定義）")
        return [base]
    return list(queries)


# ── クエリ URL 生成（純関数）─────────────────────────────────────────────────


def build_query(base_query: str) -> str:
    """検索クエリに `when:1d`（直近 24 時間）を必ず付与する。

    実測で `when:1d` を付けると Google News RSS が全件直近 24h に絞られる。鮮度を
    決定論で担保する本 CLI の肝なので、base_query に既に when: が無いときだけ足す。
    """
    if "when:" in base_query:
        return base_query
    return f"{base_query} when:1d"


def build_feed_url(
    base_query: str,
    *,
    hl: str = DEFAULT_HL,
    gl: str = DEFAULT_GL,
    ceid: str = DEFAULT_CEID,
) -> str:
    """Google News RSS search feed の URL を組み立てる（純関数）。

    `https://news.google.com/rss/search?q=<query>+when:1d&hl=ja&gl=JP&ceid=JP:ja`
    形式。クエリは URL エンコードし、when:1d を build_query で必ず付ける。
    """
    q = build_query(base_query)
    params = urllib.parse.urlencode({"q": q, "hl": hl, "gl": gl, "ceid": ceid})
    return f"{GOOGLE_NEWS_RSS}?{params}"


# ── RSS パース（純関数・オフラインテスト可能）────────────────────────────────


def _parse_pubdate(raw: str | None) -> str | None:
    """RFC 822 形式の `<pubDate>` を ISO 8601（UTC）文字列に正規化する。

    Google News RSS は `Wed, 11 Jun 2026 09:00:00 GMT` 形式。取得前の鮮度判定に使える
    よう ISO 8601 に揃える。パース不能なら None。
    """
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def parse_rss(xml_text: str, category: str, query: str) -> list[dict]:
    """RSS XML 文字列から候補 dict のリストを作る（純関数・ネット非依存）。

    Google News RSS の `<item>` 構造:
        <title>記事タイトル - 発行元</title>
        <link>https://news.google.com/rss/articles/CBM...（JS 必須エンコード URL）</link>
        <pubDate>Wed, 11 Jun 2026 09:00:00 GMT</pubDate>
        <source url="https://www.example.com">Example</source>

    返り値の各 dict は dedup.py 入力スキーマ互換のフィールド構成:
        title    : 発行元サフィックス（" - 発行元"）を剥がしたタイトル
        url      : Google News のエンコード URL（canonical ではない・生存確認用）
        source   : 発行元ドメイン（<source url> の host）or 発行元名
        category : カテゴリ ID
        pubDate  : ISO 8601（UTC）or None
        query    : この候補を引いた検索クエリ（監査・トレース用）
    """
    out: list[dict] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out
    channel = root.find("channel")
    if channel is None:
        return out
    for item in channel.findall("item"):
        title_raw = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pubdate = _parse_pubdate(item.findtext("pubDate"))

        # <source url="..."> から発行元ドメインを取る（後段の site: 限定 WebSearch 用）。
        source_el = item.find("source")
        source_name = (source_el.text or "").strip() if source_el is not None else ""
        source_url = source_el.get("url", "").strip() if source_el is not None else ""
        source_domain = ""
        if source_url:
            try:
                source_domain = urllib.parse.urlsplit(source_url).netloc.lower()
            except ValueError:
                source_domain = ""

        # Google News タイトルは「記事名 - 発行元」形式が多い。発行元サフィックスを剥がす。
        title = title_raw
        if source_name and title_raw.endswith(f" - {source_name}"):
            title = title_raw[: -len(f" - {source_name}")].strip()

        if not title or not link:
            continue

        out.append({
            "title": title,
            "url": link,
            "source": source_domain or source_name,
            "category": category,
            "pubDate": pubdate,
            "query": query,
        })
    return out


class _AnchorCollector(HTMLParser):
    """一覧 HTML から `<a href>` と表示テキストだけを抜く最小 parser。"""

    def __init__(self) -> None:
        super().__init__()
        self.anchors: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self._href = href
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self._href:
            return
        title = re.sub(r"\s+", " ", " ".join(self._text)).strip()
        if title:
            self.anchors.append((self._href, title))
        self._href = None
        self._text = []


def _source_id(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    path = parts.path.strip("/")
    if path:
        return f"{parts.netloc.lower()}/{path}".rstrip("/")
    return parts.netloc.lower()


def _text_for_filter(row: dict) -> str:
    return " ".join(str(row.get(k) or "") for k in ("title", "url", "source")).lower()


_POSITIVE_OVERRIDE = (
    "regulation", "regulatory", "policy", "m&a", "merger", "acquisition", "investment",
    "invests", "funding", "mass production", "量産", "投資", "規制", "政策", "買収", "決算",
    "safety standard", "安全基準", "platform policy",
)

_NEGATIVE_FILTERS: dict[str, list[tuple[str, tuple[str, ...]]]] = {
    "global": [
        ("homepage_or_section", ("category/", "/tag/", "/topics/", "トップ", "homepage")),
        ("video_only", ("video:", "動画のみ", "/video/")),
        ("podcast_only", ("podcast", "ポッドキャスト")),
        ("jobs", ("jobs", "careers", "求人", "採用")),
        ("affiliate", ("affiliate", "アフィリエイト")),
        ("ranking_only", ("ranking", "rankings", "top 10", "ランキング")),
    ],
    "fx": [
        ("single_currency_tick", ("usd/jpy ticks", "usd/jpy tick", "ドル円 tick", "単一通貨")),
        ("technical_analysis_only", ("technical analysis", "テクニカル分析")),
        ("weekly_range_forecast", ("weekly range", "週間レンジ")),
        ("broker_promo", ("broker", "口座開設", "spread", "ブローカー")),
        ("calendar_only", ("economic calendar", "経済指標カレンダー")),
    ],
    "ai": [
        ("tutorial", ("how to", "使い方", "tutorial")),
        ("tool_listicle", ("best ai tools", "tool list", "ツール紹介", "まとめ")),
        ("prompt_collection", ("prompt collection", "プロンプト集")),
        ("crypto_scam_only", ("crypto scam", "暗号資産詐欺")),
    ],
    "it": [
        ("crowdfunding", ("crowdfunding", "クラウドファンディング")),
        ("course_pr", ("certification", "資格", "講座", "course")),
        ("saas_promo", ("free trial", "導入事例", "saas promo")),
        ("consumer_gadget_review", ("gadget review", "ガジェットレビュー")),
        ("generic_dx", ("dx啓発", "digital transformation tips")),
    ],
    "mobility": [
        ("car_review", ("road test", "test drive", "試乗", "review and buying advice")),
        ("spy_shot", ("spy shot", "スパイショット")),
        ("rendering", ("rendering", "予想cg", "レンダリング")),
        ("buying_advice", ("buying advice", "値引き", "買い方")),
        ("single_crash", ("crash", "事故")),
        ("motorsport_result", ("motorsport result", "race result")),
    ],
    "manufacturing": [
        ("stock_pick", ("stock pick", "株価材料")),
        ("pr_wire_only", ("pr newswire", "business wire", "kyodo news prwire")),
        ("local_factory_low_impact", ("local factory", "地元工場")),
        ("product_catalog", ("product catalog", "製品カタログ")),
        ("opinion_column", ("opinion:", "コラム")),
    ],
    "economy": [
        ("individual_stock", ("stock quote", "share price", "個別株", "株価")),
        ("fund_nav", ("nav", "基準価額")),
        ("saving_tips", ("saving tips", "節約術")),
        ("local_event", ("local economic event", "地域経済イベント")),
        ("price_table_only", ("price table", "価格表")),
    ],
    "game": [
        ("sports_result", ("nba", "mlb", "premier league", "beat celtics", "試合結果")),
        ("game_theory", ("game theory", "ゲーム理論")),
        ("guide_sale_patch", ("攻略", "sale", "セール", "patch note", "パッチノート")),
        ("esports_notice", ("esports schedule", "eスポーツ告知")),
        ("merchandise", ("merchandise", "グッズ")),
    ],
}


def _contains_override_keyword(text: str, keyword: str) -> bool:
    if keyword.isascii() and re.search(r"[A-Za-z]", keyword):
        return re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", text) is not None
    return keyword in text


def negative_filter_reason(row: dict) -> str | None:
    """候補を deterministic に落とす理由を返す。残す場合は None。"""
    category = str(row.get("category") or "")
    text = _text_for_filter(row)
    has_override = any(_contains_override_keyword(text, keyword) for keyword in _POSITIVE_OVERRIDE)
    for scope in ("global", category):
        for reason, needles in _NEGATIVE_FILTERS.get(scope, []):
            if any(needle in text for needle in needles):
                if scope != "global" and has_override:
                    continue
                return f"{scope}:{reason}"
    return None


def _source_accepts_row(row: dict, source: SourceDefinition) -> bool:
    return _source_reject_reason(row, source) is None


def _source_reject_reason(row: dict, source: SourceDefinition) -> str | None:
    text = _text_for_filter(row)
    if source.include_keywords and not any(keyword.lower() in text for keyword in source.include_keywords):
        return "source:include_keyword_miss"
    if source.exclude_keywords and any(keyword.lower() in text for keyword in source.exclude_keywords):
        return "source:exclude_keyword"
    return None


def parse_scrapling_page(html_text: str, category: str, source: SourceDefinition) -> list[dict]:
    """Scrapling で取得した一覧 HTML から候補を抽出する。"""
    parser = _AnchorCollector()
    parser.feed(html_text)
    rows: list[dict] = []
    seen: set[str] = set()
    source_domain = urllib.parse.urlsplit(source.url).netloc.lower()
    for href, title in parser.anchors:
        url = urllib.parse.urljoin(source.url, href)
        if not url.startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url)
        row = {
            "title": title,
            "url": url,
            "source": source_domain,
            "category": category,
            "pubDate": None,
            "query": source.url,
            "feed_url": source.url,
            "source_mode": source.mode,
            "trust_tier": source.trust_tier,
        }
        rows.append(row)
        if len(rows) >= source.max_items:
            break
    return rows


# ── fetch（urllib → _fetch 昇格）──────────────────────────────────────────────


def fetch_feed(url: str, *, timeout: float = 15.0) -> str | None:
    """RSS feed XML を取得する。標準は urllib、403/blocked 時のみ _fetch 昇格。

    返り値は XML 文字列 or None（取得不能）。
    """
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/rss+xml,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            if status is not None and 200 <= status < 300:
                return resp.read().decode("utf-8", errors="replace")
    except Exception:
        pass
    # urllib で取れなければ昇格ラダーで再試行（Google News は通常 urllib で取れるが保険）。
    res = fetch_with_escalation(url, timeout=timeout, allow_stealthy=False)
    return res.html if res.ok else None


def fetch_scrapling_page(url: str, *, timeout: float = 15.0):
    """Scrapling Fetcher を含む昇格ラダーで HTML/listing page を取得する。"""
    return fetch_with_escalation(url, timeout=timeout, allow_stealthy=False)


def _looks_broken_scrapling_page(html_text: str | None) -> bool:
    if not html_text or not html_text.strip():
        return True
    head = html_text[:4000].lower()
    broken_markers = (
        "404 -", "404 |", ">404<", "page not found", "not found",
        "sign in", "log in", "login required", "access denied",
    )
    return any(marker in head for marker in broken_markers)


def _source_definitions_for_category(category: str) -> list[SourceDefinition]:
    """source catalog を返す。旧 RSS_FEEDS_BY_CATEGORY monkeypatch も互換維持する。"""
    rss_urls = RSS_FEEDS_BY_CATEGORY.get(category, [])
    if rss_urls != _DEFAULT_RSS_FEEDS_BY_CATEGORY.get(category, []):
        return [
            SourceDefinition(url=url, mode="rss", trust_tier="industry", max_items=10)
            for url in rss_urls
        ]
    return list(SOURCE_CATALOG_BY_CATEGORY.get(category, []))


def _empty_audit(category: str) -> dict:
    return {
        "category_id": category,
        "queries": [],
        "raw_results_total": 0,
        "candidates_total": 0,
        "selected_total": 0,
        "source_breakdown": {},
        "negative_filter_dropped": {},
        "scrapling_sources_used": [],
        "broken_sources": [],
    }


def _source_stats(source: SourceDefinition | None, url: str) -> dict:
    return {
        "url": url,
        "mode": source.mode if source else "google_news_rss",
        "trust_tier": source.trust_tier if source else "search",
        "raw": 0,
        "candidates": 0,
        "dropped": 0,
    }


def _add_negative_drop(audit: dict, reason: str) -> None:
    dropped = audit["negative_filter_dropped"]
    dropped[reason] = dropped.get(reason, 0) + 1


def _filter_rows(rows: list[dict], audit: dict, source_id: str, source: SourceDefinition | None = None) -> list[dict]:
    kept: list[dict] = []
    stats = audit["source_breakdown"][source_id]
    stats["raw"] += len(rows)
    audit["raw_results_total"] += len(rows)
    for row in rows:
        if source:
            source_reason = _source_reject_reason(row, source)
            if source_reason:
                stats["dropped"] += 1
                _add_negative_drop(audit, source_reason)
                continue
        reason = negative_filter_reason(row)
        if reason:
            stats["dropped"] += 1
            _add_negative_drop(audit, reason)
            continue
        stats["candidates"] += 1
        kept.append(row)
    return kept


def _mark_broken(audit: dict, source: SourceDefinition, reason: str) -> None:
    audit["broken_sources"].append({"url": source.url, "mode": source.mode, "reason": reason})


def harvest_category_with_audit(
    category: str,
    *,
    max_per_category: int = DEFAULT_MAX_PER_CATEGORY,
    timeout: float = 15.0,
) -> tuple[list[dict], dict]:
    """1 カテゴリの候補と収集監査を返す。"""
    items: list[dict] = []
    audit = _empty_audit(category)

    query_specs: list[tuple[str, str]] = []
    for base_query in category_queries(category):
        url = build_feed_url(base_query)
        query = build_query(base_query)
        audit["queries"].append(query)
        source_id = f"google_news:{query}"
        audit["source_breakdown"][source_id] = _source_stats(None, url)
        query_specs.append((url, source_id))

    source_specs = _source_definitions_for_category(category)
    for source in source_specs:
        source_id = _source_id(source.url)
        audit["queries"].append(source.url)
        audit["source_breakdown"][source_id] = _source_stats(source, source.url)

    fetch_jobs: list[tuple[str, int, Any]] = []
    for index, (url, _source_id_value) in enumerate(query_specs):
        fetch_jobs.append(("query", index, (url, None)))
    for index, source in enumerate(source_specs):
        fetch_jobs.append(("source", index, (source.url, source)))

    fetched: dict[tuple[str, int], Any] = {}
    with ThreadPoolExecutor(max_workers=min(8, len(fetch_jobs))) as pool:
        futures = {}
        for kind, index, (url, source) in fetch_jobs:
            if source is not None and source.mode == "scrapling_page":
                future = pool.submit(fetch_scrapling_page, url, timeout=timeout)
            else:
                future = pool.submit(fetch_feed, url, timeout=timeout)
            futures[future] = (kind, index)
        for future in as_completed(futures):
            fetched[futures[future]] = future.result()

    for index, (url, source_id) in enumerate(query_specs):
        xml_text = fetched[("query", index)]
        if not xml_text:
            print(f"WARN: feed 取得失敗 category={category} url={url}", file=sys.stderr)
            continue
        rows = parse_rss(xml_text, category, query)
        for row in rows:
            row["feed_url"] = url
            row["source_mode"] = "google_news_rss"
            row["trust_tier"] = "search"
        items.extend(_filter_rows(rows, audit, source_id))

    for index, source in enumerate(source_specs):
        source_id = _source_id(source.url)
        fetched_value = fetched[("source", index)]
        if source.mode == "rss":
            xml_text = fetched_value
            if not xml_text:
                print(f"WARN: RSS 取得失敗 category={category} url={source.url}", file=sys.stderr)
                _mark_broken(audit, source, "fetch_failed")
                continue
            rows = parse_rss(xml_text, category, source.url)
            if not rows:
                _mark_broken(audit, source, "empty_or_unparseable")
            for row in rows:
                row["feed_url"] = source.url
                row["source_mode"] = source.mode
                row["trust_tier"] = source.trust_tier
            items.extend(_filter_rows(rows[: source.max_items], audit, source_id, source))
            continue

        if source.mode == "scrapling_page":
            res = fetched_value
            if not getattr(res, "ok", False) or _looks_broken_scrapling_page(getattr(res, "html", None)):
                print(f"WARN: Scrapling 取得失敗 category={category} url={source.url}", file=sys.stderr)
                _mark_broken(audit, source, "scrapling_failed_or_broken_page")
                continue
            audit["scrapling_sources_used"].append(source.url)
            rows = parse_scrapling_page(res.html or "", category, source)
            if not rows:
                _mark_broken(audit, source, "empty_listing")
            items.extend(_filter_rows(rows, audit, source_id))
            continue

        _mark_broken(audit, source, f"unknown_mode:{source.mode}")

    selected = items[:max_per_category]
    audit["candidates_total"] = len(items)
    audit["selected_total"] = len(selected)
    if len(selected) < 5:
        audit["quality_shortfall_reason"] = "filtered_candidates_below_minimum_5"
    return selected, audit


def write_harvest_audit(audit_dir: Path, category: str, audit: dict) -> Path:
    """reporter audit と衝突しない `harvest-{category}.json` に Stage0 監査を書く。"""
    audit_dir.mkdir(parents=True, exist_ok=True)
    path = audit_dir / f"harvest-{category}.json"
    path.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


def harvest_category(
    category: str,
    *,
    max_per_category: int = DEFAULT_MAX_PER_CATEGORY,
    timeout: float = 15.0,
) -> list[dict]:
    """1 カテゴリの候補を取得する（fetch + parse + 上限適用）。

    クエリは CATEGORY_QUERIES から引く。上限 max_per_category 件で切る。
    """
    items, _audit = harvest_category_with_audit(
        category,
        max_per_category=max_per_category,
        timeout=timeout,
    )
    return items


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    p = argparse.ArgumentParser(description="Google News RSS ハーベスタ（when:1d 鮮度担保）")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--category", choices=HARVEST_CATEGORIES, help="単一カテゴリを収集")
    g.add_argument("--all", action="store_true", help="全カテゴリ（summary 除く）を収集")
    p.add_argument("--max-per-category", type=int, default=DEFAULT_MAX_PER_CATEGORY,
                   help=f"1 カテゴリあたりの上限件数（既定: {DEFAULT_MAX_PER_CATEGORY}）")
    p.add_argument("--timeout", type=float, default=15.0, help="1 feed あたりのタイムアウト秒")
    p.add_argument("--audit-dir", type=Path, help="Stage0 収集監査 JSON の出力先ディレクトリ")
    args = p.parse_args()

    targets = HARVEST_CATEGORIES if args.all else [args.category]
    total = 0
    for cat in targets:
        items, audit = harvest_category_with_audit(
            cat,
            max_per_category=args.max_per_category,
            timeout=args.timeout,
        )
        for it in items:
            print(json.dumps(it, ensure_ascii=False))
        if args.audit_dir:
            audit_path = write_harvest_audit(args.audit_dir, cat, audit)
            print(f"harvest audit: {audit_path}", file=sys.stderr)
        print(f"harvest: category={cat} {len(items)} 件", file=sys.stderr)
        total += len(items)
    print(f"harvest 合計: {total} 件 ({len(targets)} カテゴリ)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
