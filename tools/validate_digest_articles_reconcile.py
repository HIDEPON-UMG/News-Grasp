#!/usr/bin/env python3
"""digest md の記事カード URL と articles.jsonl の当日 URL が一致するか突合する gate。

# 検証する「なぜ重要か」

2026-06-12 号で digest md と `data/articles.jsonl` がずれた。片方向だけ見ると
「md には出たが jsonl に無い」append 漏れは検出できるが、freshness gate が正しく
古記事を jsonl から落としたのに digest md だけに古記事が残るケースを append 漏れと
誤判定してしまう。

本 gate は当日号のカテゴリ digest md からカード URL (`[元記事](URL)`) を抽出し、
当日号 (date == issue_date) の articles.jsonl URL 集合と **完全一致** することを突合する。
digest-only URL は「古記事が md に残った / append 漏れ」のどちらもあり得るため fatal。
articles-only URL は「jsonl にはあるがカード生成漏れ」として fatal。
freshness 済み append 集合と公開 md を一致させる境界 gate である。

対象は articles.jsonl の category record と対応するカテゴリ digest md のみ
(AI / FX / IT-Consulting / Mobility / Manufacturing / Economy / Game)。DeepDive / Summary
は articles.jsonl の category record とは別管理なので除外する。

CLI:
  python -m tools.validate_digest_articles_reconcile --issue-date 2026-06-12

ファイル名 `2026-06-12-AI.md` 形式の md が対象。digest/data の既定はリポジトリ配下。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parent.parent

# digest md カードの正典 URL は `🔗 [元記事](https://...)`。thumb (`![thumb](...)`) や
# wikilink (`[[...]]`) は対象外なので、リンクテキストが「元記事」のものだけを拾う。
_GENMOTO_RE = re.compile(r"\[元記事\]\((https?://[^)\s]+)\)")

# articles.jsonl の category record と突合するカテゴリ digest のみ対象にする。
_EXCLUDE_DIRS: frozenset[str] = frozenset({"DeepDive", "Summary"})

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _normalize_url(url: str) -> str:
    """突合用に URL を正規化 (前後空白除去 + 末尾スラッシュ除去)。"""
    return url.strip().rstrip("/")


def digest_card_urls(digest_dir: Path, issue_date: str) -> dict[str, list[str]]:
    """{genre: [card url, ...]} を返す。当日号のカテゴリ digest md のみ走査。"""
    out: dict[str, list[str]] = {}
    for md in sorted(digest_dir.glob(f"*/{issue_date}-*.md")):
        genre = md.parent.name
        if genre in _EXCLUDE_DIRS:
            continue
        text = md.read_text(encoding="utf-8-sig", errors="replace")
        urls = [_normalize_url(u) for u in _GENMOTO_RE.findall(text)]
        if urls:
            out[genre] = urls
    return out


def articles_urls_for_issue(articles_path: Path, issue_date: str) -> dict[str, str]:
    """当日号 (date == issue_date) の articles.jsonl URL -> genre (正規化済)。"""
    urls: dict[str, str] = {}
    with articles_path.open(encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("date") != issue_date:
                continue
            u = rec.get("url")
            if isinstance(u, str) and u.strip():
                urls[_normalize_url(u)] = str(rec.get("genre") or "unknown")
    return urls


def reconcile(digest_dir: Path, articles_path: Path, issue_date: str) -> dict[str, list[str]]:
    """digest md カード URL と articles.jsonl URL の完全一致を検査。

    Returns:
        {
          "digest_only": md にあり jsonl に無い URL,
          "articles_only": jsonl にあり md に無い URL,
        }
        両方空なら公開 md と freshness 済み articles.jsonl が一致 = 突合 OK。
    """
    card_urls = digest_card_urls(digest_dir, issue_date)
    digest_index: dict[str, str] = {}
    for genre, urls in card_urls.items():
        for u in urls:
            digest_index[u] = genre

    jsonl_urls = articles_urls_for_issue(articles_path, issue_date)
    digest_only = [
        f"{genre}: {u}"
        for u, genre in sorted(digest_index.items(), key=lambda item: (item[1], item[0]))
        if u not in jsonl_urls
    ]
    articles_only = [
        f"{genre}: {u}"
        for u, genre in sorted(jsonl_urls.items(), key=lambda item: (item[1], item[0]))
        if u not in digest_index
    ]
    return {"digest_only": digest_only, "articles_only": articles_only}


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    ap = argparse.ArgumentParser(
        description="digest md カード URL と articles.jsonl 当日 URL が一致するか突合する gate"
    )
    ap.add_argument("--issue-date", required=True, help="号日 (YYYY-MM-DD)")
    ap.add_argument("--digest-dir", type=Path, default=_PKG_ROOT / "digest")
    ap.add_argument("--articles", type=Path, default=_PKG_ROOT / "data" / "articles.jsonl")
    args = ap.parse_args(argv)

    if not _DATE_RE.match(args.issue_date):
        print(f"FATAL: --issue-date は 'YYYY-MM-DD' 形式: got {args.issue_date!r}", file=sys.stderr)
        return 2
    if not args.articles.exists():
        print(f"FATAL: articles.jsonl not found: {args.articles}", file=sys.stderr)
        return 2

    result = reconcile(args.digest_dir, args.articles, args.issue_date)
    digest_only = result["digest_only"]
    articles_only = result["articles_only"]
    if digest_only or articles_only:
        print(
            f"FAIL: digest md と articles.jsonl の当日 URL が一致しません "
            f"(号日 {args.issue_date}, digest-only={len(digest_only)}, articles-only={len(articles_only)}):",
            file=sys.stderr,
        )
        if digest_only:
            print("  digest-only (md だけに存在。古記事残存または append 漏れの疑い):", file=sys.stderr)
            for m in digest_only[:40]:
                print(f"    - {m}", file=sys.stderr)
            if len(digest_only) > 40:
                print(f"    ... and {len(digest_only) - 40} more", file=sys.stderr)
        if articles_only:
            print("  articles-only (articles.jsonl だけに存在。カード生成漏れの疑い):", file=sys.stderr)
            for m in articles_only[:40]:
                print(f"    - {m}", file=sys.stderr)
            if len(articles_only) > 40:
                print(f"    ... and {len(articles_only) - 40} more", file=sys.stderr)
        return 1

    print(f"PASS: digest md カード URL と articles.jsonl 当日 URL は一致 (号日 {args.issue_date})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
