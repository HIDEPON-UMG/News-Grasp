"""記事 URL とサムネ品質に関する共通判定。"""
from __future__ import annotations

from urllib.parse import urlparse

GOOGLE_NEWS_RSS_MARKER = "news.google.com/rss/articles/"
GOOGLE_NEWS_PROXY_THUMB_HOST = "lh3.googleusercontent.com"

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
