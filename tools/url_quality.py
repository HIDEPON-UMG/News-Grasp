"""記事 URL とサムネ品質に関する共通判定。"""
from __future__ import annotations

import re
from urllib.parse import urlparse

GOOGLE_NEWS_RSS_MARKER = "news.google.com/rss/articles/"
GOOGLE_NEWS_PROXY_THUMB_HOST = "lh3.googleusercontent.com"
_NEWS_GRASP_THUMB_RE = re.compile(
    r"^https?://hidepon-umg\.github\.io/News-Grasp/(?:assets/og/|assets/news-grasp)",
    re.IGNORECASE,
)

_LANDING_PATH_SEGMENTS = {
    "article",
    "articles",
    "business",
    "category",
    "economy",
    "finance",
    "game",
    "games",
    "market",
    "markets",
    "news",
    "tech",
    "technology",
    "topics",
}


def is_google_news_rss_url(url: str) -> bool:
    """Google News RSS の中継 URL なら True。"""
    return GOOGLE_NEWS_RSS_MARKER in url


def is_google_news_proxy_thumb(url: object) -> bool:
    """Google News が配る代理サムネ URL なら True。"""
    if not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and parsed.netloc.lower() == GOOGLE_NEWS_PROXY_THUMB_HOST


def is_news_grasp_self_thumb(url: object) -> bool:
    """News-Grasp 自身の公開画像を記事固有サムネとして参照していれば True。"""
    return isinstance(url, str) and bool(_NEWS_GRASP_THUMB_RE.search(url))


def looks_homepage_or_section_landing(url: str) -> bool:
    """元記事ではなく媒体トップ/浅いカテゴリトップへ丸まった URL を検知する。"""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    segments = [seg for seg in parsed.path.split("/") if seg]
    if not segments:
        return True
    if len(segments) == 1 and segments[0].lower() in _LANDING_PATH_SEGMENTS:
        return True
    return False
