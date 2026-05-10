#!/usr/bin/env python3
"""tools/fetch_ogp.py の実機スモーク。

articles.jsonl からドメイン重複なしで N 件 (default 20) URL を抽出し、
fetch_ogp.fetch_ogp を順次呼んで成功率を測る。WebFetch 経由 (実効率 4%) と
本ツール (生 HTML パース) の差分を数値で出すのが目的。

通常の単体テストより遅い (1〜3 分かかる) ため、CI / pre-commit からは外す。
手動でのみ走らせる: `py tests/test_fetch_ogp_smoke.py`
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import fetch_ogp  # type: ignore

JSONL = ROOT / "data" / "articles.jsonl"
N_URLS = 20


def collect_urls(limit: int) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    with JSONL.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            url = rec.get("url", "")
            host = (urlparse(url).hostname or "").lower()
            if host.startswith("www."):
                host = host[4:]
            if not host or host in seen:
                continue
            seen.add(host)
            urls.append(url)
            if len(urls) >= limit:
                break
    return urls


def main() -> int:
    urls = collect_urls(N_URLS)
    if not urls:
        print("error: no URLs found in articles.jsonl", file=sys.stderr)
        return 2

    print(f"smoke: {len(urls)} URLs (one per domain)\n")
    ok = no_meta = err_count = 0
    rows: list[tuple[str, str, str]] = []

    for u in urls:
        r = fetch_ogp.fetch_ogp(u, timeout=10.0, retries=1)
        img = r["og_image"] or r["twitter_image"] or ""
        if img:
            ok += 1
        elif r["status"] == "no_meta":
            no_meta += 1
        else:
            err_count += 1
        rows.append((r["status"], u, img))

    print(f"== summary == total={len(urls)} ok={ok} no_meta={no_meta} other_failure={err_count}")
    print(f"== success rate == {ok / len(urls) * 100:.1f}%\n")
    for status, u, img in rows:
        u_disp = u if len(u) <= 60 else u[:57] + "..."
        img_disp = (img[:67] + "...") if isinstance(img, str) and len(img) > 70 else (img or "-")
        print(f"  [{status:14}] {u_disp:60} -> {img_disp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
