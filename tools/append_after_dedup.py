#!/usr/bin/env python3
"""articles.jsonl 追記の境界スクリプト。

stdin の JSON Lines 候補を `tools.dedup` の重複・続報・鮮度ゲートに通し、
通過したレコードだけを `data/articles.jsonl` に append する。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

import dedup

try:
    from googlenewsdecoder import gnewsdecoder
except ModuleNotFoundError:  # pragma: no cover - dependency gate is exercised by runtime
    gnewsdecoder = None  # type: ignore[assignment]

try:
    from tools.fetch_ogp import fetch_ogp
except ModuleNotFoundError:
    from fetch_ogp import fetch_ogp


GOOGLE_NEWS_RSS_MARKER = "news.google.com/rss/articles/"


def read_candidates(stdin) -> list[dict]:
    out: list[dict] = []
    for line in stdin:
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def append_records(path: Path, records: list[dict]) -> None:
    if not records:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    prefix = ""
    if path.exists() and path.read_text(encoding="utf-8-sig").strip():
        prefix = "\n"
    payload = "\n".join(json.dumps(r, ensure_ascii=False) for r in records)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(prefix + payload)


def is_google_news_rss_url(url: str) -> bool:
    return GOOGLE_NEWS_RSS_MARKER in url


def _decode_status_ok(value: Any) -> bool:
    return value is True or str(value).lower() in {"ok", "success", "true"}


def decode_google_news_url(
    url: str,
    *,
    google_decoder: Callable[..., dict[str, Any]] | None = None,
) -> str | None:
    decoder = google_decoder if google_decoder is not None else gnewsdecoder
    if decoder is None:
        return None
    try:
        result = decoder(url)
    except Exception:
        return None
    if not isinstance(result, dict) or not _decode_status_ok(result.get("status")):
        return None
    decoded = result.get("decoded_url")
    if isinstance(decoded, str) and decoded.startswith(("http://", "https://")):
        return decoded
    return None


def _pick_thumb(result: dict[str, Any]) -> str | None:
    for key in ("og_image", "twitter_image"):
        value = result.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
    return None


def hydrate_thumbnails(
    records: list[dict],
    *,
    google_decoder: Callable[..., dict[str, Any]] | None = None,
    fetch_ogp_func: Callable[..., dict[str, Any]] = fetch_ogp,
    timeout: float = 10.0,
    retries: int = 1,
) -> tuple[list[dict], list[dict]]:
    """append 前に URL と thumb を補完する。

    Google News RSS URL は元記事 URL に解決できない限り append しない。解決後または
    通常 URL の thumb が空なら OGP / Twitter Card 画像を取得して `thumb` に入れる。
    """
    hydrated: list[dict] = []
    dropped: list[dict] = []
    for rec in records:
        item = dict(rec)
        url = str(item.get("url") or "")
        if is_google_news_rss_url(url):
            decoded = decode_google_news_url(url, google_decoder=google_decoder)
            if not decoded:
                item["dedup_reason"] = "google_news_unresolved"
                dropped.append(item)
                continue
            item["url"] = decoded
            item["url_norm"] = dedup.normalize_url(decoded)
            url = decoded

        if "thumb" not in item or item.get("thumb") in ("", None):
            item["thumb"] = None
            try:
                ogp = fetch_ogp_func(url, timeout=timeout, retries=retries)
            except Exception:
                ogp = {}
            thumb = _pick_thumb(ogp)
            if thumb:
                item["thumb"] = thumb
        hydrated.append(item)
    return hydrated, dropped


def filter_records(
    candidates: list[dict],
    existing: list[dict],
    *,
    window_hours: float,
    title_threshold: float,
    followup_gate: bool,
    freshness_gate: bool,
    max_source_age_days: int,
    date_fetch_cap: int = dedup.DEFAULT_DATE_FETCH_CAP,
) -> tuple[list[dict], list[dict]]:
    return dedup.dedup_candidates(
        candidates,
        existing,
        window_hours=window_hours,
        title_threshold=title_threshold,
        followup_gate=followup_gate,
        freshness_gate=freshness_gate,
        max_source_age_days=max_source_age_days,
        date_fetch_cap=date_fetch_cap,
    )


def main() -> int:
    p = argparse.ArgumentParser(description="dedup 通過後の記事だけ articles.jsonl に追記")
    p.add_argument("--jsonl", default="data/articles.jsonl",
                   help="追記先 articles.jsonl（既定: data/articles.jsonl）")
    p.add_argument("--window-hours", type=float, default=24.0)
    p.add_argument("--title-threshold", type=float, default=dedup.DEFAULT_TITLE_THRESHOLD)
    p.add_argument("--followup-gate", action="store_true", default=True,
                   help="続報候補の新材料ゲートを有効化（既定: 有効）")
    p.add_argument("--no-followup-gate", dest="followup_gate", action="store_false",
                   help="保守用途。続報候補の新材料ゲートを無効化")
    p.add_argument("--freshness-gate", action="store_true", default=True,
                   help="URL 発行日ベースの鮮度ゲートを有効化（既定: 有効）")
    p.add_argument("--no-freshness-gate", dest="freshness_gate", action="store_false",
                   help="保守用途。鮮度ゲートを無効化")
    p.add_argument("--max-source-age-days", type=int, default=dedup.DEFAULT_MAX_SOURCE_AGE_DAYS)
    p.add_argument("--date-fetch-cap", type=int, default=dedup.DEFAULT_DATE_FETCH_CAP,
                   help="鮮度ゲートの htmldate 補完 fetch 上限件数（既定: dedup と同じ）")
    p.add_argument("--hydrate-thumbs", action="store_true", default=True,
                   help="append 前に Google News URL 解決と OGP thumb 補完を行う（既定: 有効）")
    p.add_argument("--no-hydrate-thumbs", dest="hydrate_thumbs", action="store_false",
                   help="保守用途。サムネ補完を無効化")
    p.add_argument("--thumb-timeout", type=float, default=10.0,
                   help="サムネ取得 1 回あたりのタイムアウト秒")
    p.add_argument("--thumb-retries", type=int, default=1,
                   help="サムネ取得のリトライ回数")
    args = p.parse_args()

    jsonl_path = Path(args.jsonl)
    candidates = read_candidates(sys.stdin)
    existing = dedup.load_existing(jsonl_path)
    passed, dropped = filter_records(
        candidates,
        existing,
        window_hours=args.window_hours,
        title_threshold=args.title_threshold,
        followup_gate=args.followup_gate,
        freshness_gate=args.freshness_gate,
        max_source_age_days=args.max_source_age_days,
        date_fetch_cap=args.date_fetch_cap,
    )
    if args.hydrate_thumbs:
        passed, thumb_dropped = hydrate_thumbnails(
            passed,
            timeout=args.thumb_timeout,
            retries=args.thumb_retries,
        )
        dropped.extend(thumb_dropped)
    append_records(jsonl_path, passed)

    for r in passed:
        print(json.dumps(r, ensure_ascii=False))
    print(
        f"append_after_dedup: appended {len(passed)}, dropped {len(dropped)} "
        f"to {jsonl_path}",
        file=sys.stderr,
    )
    for r in dropped:
        print(f"  DROP: {r.get('title', '')[:60]} | {r.get('dedup_reason', '')}",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
