#!/usr/bin/env python3
"""tools.fetch_article_body の契約テスト。"""
from __future__ import annotations

import json

from tools.fetch_article_body import extract_article_body


def test_extract_article_body_prefers_article_text_and_strips_noise() -> None:
    html = """
    <html><head><title>Example Title</title><script>bad()</script></head>
    <body>
      <nav>menu text should disappear</nav>
      <article>
        <h1>Important launch</h1>
        <p>First paragraph has useful evidence.</p>
        <p>Second paragraph explains why it matters.</p>
      </article>
      <footer>footer text should disappear</footer>
    </body></html>
    """

    data = extract_article_body(html, url="https://example.com/a")

    assert data["url"] == "https://example.com/a"
    assert data["title"] == "Example Title"
    assert "Important launch" in data["text"]
    assert "First paragraph has useful evidence." in data["text"]
    assert "menu text" not in data["text"]
    assert "bad()" not in data["text"]


def test_extract_article_body_caps_text_for_reporter_context() -> None:
    html = "<article><p>" + ("x" * 9000) + "</p></article>"

    data = extract_article_body(html, url="https://example.com/long", max_chars=1200)

    assert len(data["text"]) == 1200
    assert json.dumps(data, ensure_ascii=False)
