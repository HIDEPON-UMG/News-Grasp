#!/usr/bin/env python3
"""記事本文の公開部分を記者コンテキスト用に短く抽出する CLI。

Codex CLI には旧 WebFetch 相当の本文取得 tool が常にあるとは限らないため、
記者だけが必要時に呼ぶ補助ツールとして提供する。編集長へ全文を渡さないよう、
出力は title / text / url の短い JSON に制限する。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from html.parser import HTMLParser

try:
    from tools._fetch import fetch_with_escalation
except ModuleNotFoundError:
    from _fetch import fetch_with_escalation


class _ArticleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.article_parts: list[str] = []
        self.body_parts: list[str] = []
        self._stack: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "nav", "footer", "aside", "noscript", "svg"}:
            self._skip_depth += 1
        self._stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "nav", "footer", "aside", "noscript", "svg"} and self._skip_depth:
            self._skip_depth -= 1
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i] == tag:
                del self._stack[i:]
                break

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = _normalize_ws(data)
        if not text:
            return
        if "title" in self._stack:
            self.title_parts.append(text)
        if "article" in self._stack or any(tag in self._stack for tag in ("main",)):
            self.article_parts.append(text)
        elif "body" in self._stack:
            self.body_parts.append(text)


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def extract_article_body(html: str, *, url: str, max_chars: int = 5000) -> dict:
    parser = _ArticleTextParser()
    parser.feed(html)
    title = _normalize_ws(" ".join(parser.title_parts))
    parts = parser.article_parts or parser.body_parts
    text = _normalize_ws(" ".join(parts))
    if max_chars > 0:
        text = text[:max_chars]
    return {"url": url, "title": title, "text": text}


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    p = argparse.ArgumentParser(description="記事本文の公開部分を短い JSON として取得")
    p.add_argument("url")
    p.add_argument("--max-chars", type=int, default=5000)
    p.add_argument("--timeout", type=float, default=12.0)
    args = p.parse_args()

    res = fetch_with_escalation(args.url, timeout=args.timeout, allow_stealthy=False)
    if not res.ok or not res.html:
        print(json.dumps({"url": args.url, "ok": False, "error": res.error or res.reason}, ensure_ascii=False))
        return 1
    payload = extract_article_body(res.html, url=args.url, max_chars=args.max_chars)
    payload["ok"] = bool(payload["text"])
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
