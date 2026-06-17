#!/usr/bin/env python3
"""reporter へ渡す Stage1 候補を決定論的に整える。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

from tools import dedup
from tools.append_after_dedup import decode_google_news_url
from tools.fetch_ogp import fetch_ogp
from tools.url_quality import is_google_news_rss_url, looks_homepage_or_section_landing

try:
    from googlenewsdecoder import decoderv1 as _local_google_news_decode
except Exception:  # pragma: no cover - dependency absence is handled at runtime
    _local_google_news_decode = None

_SKIP_FILES = {"all.jsonl", "dropped.jsonl"}


def _decode_google_news_url_with_timeout(url: str, timeout_sec: float) -> str | None:
    """Google News RSS URL を元記事 URL へ解決する。

    以前は `googlenewsdecoder` がハングした場合に備えて multiprocessing で
    timeout を掛けていたが、Windows のタスク環境で Queue/Semaphore 作成が
    PermissionError になり、候補生成が全件空になる事故が起きた。decode 失敗は
    1 候補の drop に閉じればよいので、プロセス分離せず例外を None に丸める。
    """
    _ = timeout_sec
    if _local_google_news_decode is not None:
        try:
            decoded = _local_google_news_decode(url)
        except Exception:
            decoded = None
        if isinstance(decoded, str) and decoded.startswith(("http://", "https://")):
            return decoded

    try:
        value = decode_google_news_url(url)
    except Exception:
        return None
    return value if isinstance(value, str) else None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    payload = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
    if payload:
        payload += "\n"
    path.write_text(payload, encoding="utf-8", newline="\n")


def _pick_thumb(result: dict[str, Any]) -> str | None:
    for key in ("og_image", "twitter_image"):
        value = result.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
    return None


def prepare_rows(
    rows: list[dict[str, Any]],
    *,
    google_decoder: Callable[..., dict[str, Any]] | None = None,
    fetch_ogp_func: Callable[..., dict[str, Any]] = fetch_ogp,
    max_rows: int = 25,
    thumb_limit: int = 25,
    decode_timeout: float = 3.0,
    thumb_timeout: float = 6.0,
    thumb_retries: int = 0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """候補 URL を元記事 URL に寄せ、reporter に渡せない候補を drop する。"""
    prepared: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    thumb_attempts = 0
    for row in rows[:max_rows]:
        item = dict(row)
        url = str(item.get("url") or "")
        if is_google_news_rss_url(url):
            if google_decoder is not None:
                decoded = decode_google_news_url(url, google_decoder=google_decoder)
            else:
                decoded = _decode_google_news_url_with_timeout(url, decode_timeout)
            if not decoded:
                item["google_news_decode_status"] = "unresolved"
                item["url_norm"] = dedup.normalize_url(url)
                item["google_news_url"] = url
                item["url_resolution_action"] = "reporter_must_resolve_canonical"
                decoded = None
            else:
                item["url"] = decoded
                item["url_norm"] = dedup.normalize_url(decoded)
                item["google_news_url"] = url
                item["url_resolution_action"] = "canonical_resolved"
                url = decoded
        elif url:
            item["url_norm"] = dedup.normalize_url(url)

        if not url or looks_homepage_or_section_landing(url):
            item["drop_reason"] = "homepage_or_section_landing_url"
            dropped.append(item)
            continue

        if item.get("thumb") in ("", None) and thumb_attempts < thumb_limit:
            item["thumb"] = None
            thumb_attempts += 1
            try:
                ogp = fetch_ogp_func(url, timeout=thumb_timeout, retries=thumb_retries)
            except Exception:
                ogp = {}
            thumb = _pick_thumb(ogp)
            if thumb:
                item["thumb"] = thumb
        prepared.append(item)
    return prepared, dropped


def prepare_directory(
    input_dir: Path,
    *,
    google_decoder: Callable[..., dict[str, Any]] | None = None,
    fetch_ogp_func: Callable[..., dict[str, Any]] = fetch_ogp,
    max_rows_per_file: int = 25,
    thumb_limit_per_file: int = 25,
    decode_timeout: float = 3.0,
    thumb_timeout: float = 6.0,
    thumb_retries: int = 0,
) -> dict[str, Any]:
    """ディレクトリ内の `*.jsonl` を in-place で reporter 用に整える。"""
    summary: dict[str, Any] = {
        "input_dir": str(input_dir),
        "input_count": 0,
        "prepared_count": 0,
        "dropped_count": 0,
        "files": [],
    }
    for path in sorted(input_dir.glob("*.jsonl")):
        if path.name in _SKIP_FILES:
            continue
        rows = _read_jsonl(path)
        prepared, dropped = prepare_rows(
            rows,
            google_decoder=google_decoder,
            fetch_ogp_func=fetch_ogp_func,
            max_rows=max_rows_per_file,
            thumb_limit=thumb_limit_per_file,
            decode_timeout=decode_timeout,
            thumb_timeout=thumb_timeout,
            thumb_retries=thumb_retries,
        )
        _write_jsonl(path, prepared)
        file_summary = {
            "file": str(path),
            "input_count": len(rows),
            "prepared_count": len(prepared),
            "dropped_count": len(dropped),
            "thumb_nonempty": sum(bool(row.get("thumb")) for row in prepared),
        }
        summary["input_count"] += len(rows)
        summary["prepared_count"] += len(prepared)
        summary["dropped_count"] += len(dropped)
        summary["files"].append(file_summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="Stage1 候補を reporter 前に URL/サムネ補完する。")
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--max-rows-per-file", type=int, default=25)
    parser.add_argument("--decode-timeout", type=float, default=3.0)
    parser.add_argument("--thumb-limit-per-file", type=int, default=25)
    parser.add_argument("--thumb-timeout", type=float, default=6.0)
    parser.add_argument("--thumb-retries", type=int, default=0)
    args = parser.parse_args(argv)

    summary = prepare_directory(
        args.input_dir,
        max_rows_per_file=args.max_rows_per_file,
        thumb_limit_per_file=args.thumb_limit_per_file,
        decode_timeout=args.decode_timeout,
        thumb_timeout=args.thumb_timeout,
        thumb_retries=args.thumb_retries,
    )
    print(json.dumps(summary, ensure_ascii=False))
    if summary["prepared_count"] < 1:
        print("ERROR: reporter candidates are empty after preparation", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
