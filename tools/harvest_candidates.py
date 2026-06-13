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
import json
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

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

# 実 URL は実装・運用時に 200 を確認してから追加する。空登録簿でも構造は有効。
RSS_FEEDS_BY_CATEGORY: dict[str, list[str]] = {
    cat: [] for cat in CATEGORY_QUERIES
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


def harvest_category(
    category: str,
    *,
    max_per_category: int = DEFAULT_MAX_PER_CATEGORY,
    timeout: float = 15.0,
) -> list[dict]:
    """1 カテゴリの候補を取得する（fetch + parse + 上限適用）。

    クエリは CATEGORY_QUERIES から引く。上限 max_per_category 件で切る。
    """
    items: list[dict] = []
    for base_query in category_queries(category):
        url = build_feed_url(base_query)
        query = build_query(base_query)
        xml_text = fetch_feed(url, timeout=timeout)
        if not xml_text:
            print(f"WARN: feed 取得失敗 category={category} url={url}", file=sys.stderr)
            continue
        rows = parse_rss(xml_text, category, query)
        for row in rows:
            row["feed_url"] = url
        items.extend(rows)

    for feed_url in RSS_FEEDS_BY_CATEGORY.get(category, []):
        xml_text = fetch_feed(feed_url, timeout=timeout)
        if not xml_text:
            print(f"WARN: RSS 取得失敗 category={category} url={feed_url}", file=sys.stderr)
            continue
        rows = parse_rss(xml_text, category, feed_url)
        for row in rows:
            row["feed_url"] = feed_url
        items.extend(rows)

    return items[:max_per_category]


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
    args = p.parse_args()

    targets = HARVEST_CATEGORIES if args.all else [args.category]
    total = 0
    for cat in targets:
        items = harvest_category(cat, max_per_category=args.max_per_category, timeout=args.timeout)
        for it in items:
            print(json.dumps(it, ensure_ascii=False))
        print(f"harvest: category={cat} {len(items)} 件", file=sys.stderr)
        total += len(items)
    print(f"harvest 合計: {total} 件 ({len(targets)} カテゴリ)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
