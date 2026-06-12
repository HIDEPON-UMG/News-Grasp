#!/usr/bin/env python3
"""digest md ↔ articles.jsonl 突合 gate の契約テスト。

# 検証する「なぜ重要か」

2026-06-12 号で digest md と articles.jsonl がずれ、片方向の突合だけでは
「freshness gate が古記事を jsonl から正しく落としたのに md に残った」ケースを
append 漏れと誤検出し得ることが分かった。本テストは digest md カード URL と
articles.jsonl 当日 URL の完全一致を locked-in する。
"""
from __future__ import annotations

import json
from pathlib import Path

from tools.validate_digest_articles_reconcile import main, reconcile


def _write_digest(digest_dir: Path, genre: str, issue_date: str, urls: list[str]) -> None:
    cards = []
    for i, u in enumerate(urls):
        cards.append(
            f"### [{90 - i}] sample title {i}\n\n"
            f"📅 {issue_date} 10:00 · 📰 Sample · 🔗 [元記事]({u})\n\n"
            f"![thumb](https://cdn.example.com/thumb-{i}.jpg)\n\n"
            "- 本文サンプル\n"
        )
    body = f"---\ntitle: x\ndate: {issue_date}\ncategory: {genre}\n---\n\n# {genre}\n\n" + "\n---\n".join(cards)
    d = digest_dir / genre
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{issue_date}-{genre}.md").write_text(body, encoding="utf-8")


def _write_articles(articles_path: Path, issue_date: str, urls: list[str]) -> None:
    articles_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps({
            "date": issue_date, "genre": "AI", "title": f"t{i}", "url": u, "thumb": None,
        }, ensure_ascii=False)
        for i, u in enumerate(urls)
    ]
    articles_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_reconcile_detects_digest_only_url(tmp_path: Path) -> None:
    """digest md にだけある URL を古記事残存または append 漏れとして検出する。"""
    issue = "2026-06-12"
    digest = tmp_path / "digest"
    articles = tmp_path / "data" / "articles.jsonl"
    card_urls = ["https://a.example.com/1", "https://a.example.com/2", "https://a.example.com/3"]
    _write_digest(digest, "AI", issue, card_urls)
    # 3 件中 1 件 (…/2) を articles.jsonl から欠落させる = 23 件追記漏れの class
    _write_articles(articles, issue, ["https://a.example.com/1", "https://a.example.com/3"])

    result = reconcile(digest, articles, issue)
    assert result == {
        "digest_only": ["AI: https://a.example.com/2"],
        "articles_only": [],
    }

    rc = main(["--issue-date", issue, "--digest-dir", str(digest), "--articles", str(articles)])
    assert rc == 1, "append 漏れがあれば exit 1 のはず"


def test_reconcile_detects_articles_only_url(tmp_path: Path) -> None:
    """articles.jsonl にだけある URL をカード生成漏れとして検出する。"""
    issue = "2026-06-12"
    digest = tmp_path / "digest"
    articles = tmp_path / "data" / "articles.jsonl"
    _write_digest(digest, "AI", issue, ["https://a.example.com/1"])
    _write_articles(articles, issue, ["https://a.example.com/1", "https://a.example.com/2"])

    result = reconcile(digest, articles, issue)
    assert result == {
        "digest_only": [],
        "articles_only": ["AI: https://a.example.com/2"],
    }

    rc = main(["--issue-date", issue, "--digest-dir", str(digest), "--articles", str(articles)])
    assert rc == 1, "カード生成漏れがあれば exit 1 のはず"


def test_reconcile_passes_when_complete(tmp_path: Path) -> None:
    """カード URL と articles.jsonl URL が完全一致すれば PASS (末尾スラッシュ差は吸収)。"""
    issue = "2026-06-12"
    digest = tmp_path / "digest"
    articles = tmp_path / "data" / "articles.jsonl"
    _write_digest(digest, "AI", issue, ["https://a.example.com/1", "https://a.example.com/2/"])
    _write_articles(articles, issue, ["https://a.example.com/1", "https://a.example.com/2"])

    assert reconcile(digest, articles, issue) == {"digest_only": [], "articles_only": []}
    rc = main(["--issue-date", issue, "--digest-dir", str(digest), "--articles", str(articles)])
    assert rc == 0


def test_reconcile_excludes_deepdive_and_thumb(tmp_path: Path) -> None:
    """DeepDive md と thumb URL は突合対象外 (誤検出しない)。"""
    issue = "2026-06-12"
    digest = tmp_path / "digest"
    articles = tmp_path / "data" / "articles.jsonl"
    _write_digest(digest, "AI", issue, ["https://a.example.com/1"])
    # DeepDive は除外対象: jsonl に無い URL を含んでも fatal にならない
    _write_digest(digest, "DeepDive", issue, ["https://deepdive.example.com/x"])
    _write_articles(articles, issue, ["https://a.example.com/1"])

    result = reconcile(digest, articles, issue)
    assert result == {"digest_only": [], "articles_only": []}, (
        f"DeepDive / thumb を誤検出した: {result}"
    )
