#!/usr/bin/env python3
"""RSS 登録簿の実ネット検証 artifact を生成する。

目的:
  RSS_FEEDS_BY_CATEGORY に入れた URL が、runner で使う parser と同じ経路で
  候補化できることを記録する。URL の存在確認だけで Green にしないため、
  HTTP 2xx / XML parse / parse_rss item count をまとめて JSON に残す。
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

try:
    from tools.harvest_candidates import RSS_FEEDS_BY_CATEGORY, parse_rss
except ModuleNotFoundError:
    from harvest_candidates import RSS_FEEDS_BY_CATEGORY, parse_rss


_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


def verify_url(category: str, url: str, *, timeout: float) -> dict:
    rec: dict = {"category": category, "url": url, "ok": False}
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": _UA, "Accept": "application/rss+xml,application/xml,text/xml,*/*"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            xml_text = resp.read().decode("utf-8", errors="replace")
        rows = parse_rss(xml_text, category, url)
        rec.update({
            "status": status,
            "parsed_items": len(rows),
            "sample_titles": [row["title"] for row in rows[:3]],
            "ok": 200 <= int(status) < 300 and bool(rows),
        })
    except Exception as exc:
        rec["error"] = f"{type(exc).__name__}: {exc}"
    return rec


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    p = argparse.ArgumentParser(description="RSS_FEEDS_BY_CATEGORY の実ネット検証")
    p.add_argument("--output", default="build/rss-registry-verification.json")
    p.add_argument("--timeout", type=float, default=15.0)
    args = p.parse_args()

    records = [
        verify_url(category, url, timeout=args.timeout)
        for category, urls in RSS_FEEDS_BY_CATEGORY.items()
        for url in urls
    ]
    payload = {
        "ok": bool(records) and all(record["ok"] for record in records),
        "total_feeds": len(records),
        "records": records,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
