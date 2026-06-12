#!/usr/bin/env python3
"""digest md の記事カード URL が articles.jsonl に全て append されているか突合する gate。

# 検証する「なぜ重要か」

2026-06-12 号で filtered 34 件中 23 件が、digest md には記事カードとして掲載された
のに `data/articles.jsonl` への append 漏れし archive 側で欠落した。どの既存 gate も
この「md には出たが jsonl に無い」サイレント欠落を検出できなかった: record-schema /
url-liveness gate は articles.jsonl の中身しか見ず、md と突合しないためである
(= 鮮度ゲートでは原理的に検出できない append 漏れの class)。

本 gate は当日号のカテゴリ digest md からカード URL (`[元記事](URL)`) を抽出し、
当日号 (date == issue_date) の articles.jsonl URL 集合に **全て含まれる** ことを突合する。
含まれない URL = append 漏れ = fatal。([[feedback_check_design_principles]] Lv4 契約 +
将来 runner.ps1 push gate へ配線して Lv3 化する想定。)

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


def articles_urls_for_issue(articles_path: Path, issue_date: str) -> set[str]:
    """当日号 (date == issue_date) の articles.jsonl URL 集合 (正規化済)。"""
    urls: set[str] = set()
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
                urls.add(_normalize_url(u))
    return urls


def reconcile(digest_dir: Path, articles_path: Path, issue_date: str) -> list[str]:
    """digest md カード URL ⊆ articles.jsonl URL を検査。

    md にあり jsonl に無い URL (= append 漏れ) を `"{genre}: {url}"` の list で返す。
    空なら全カード URL が articles.jsonl に存在 = 突合 OK。
    """
    card_urls = digest_card_urls(digest_dir, issue_date)
    jsonl_urls = articles_urls_for_issue(articles_path, issue_date)
    missing: list[str] = []
    for genre, urls in card_urls.items():
        for u in urls:
            if u not in jsonl_urls:
                missing.append(f"{genre}: {u}")
    return missing


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    ap = argparse.ArgumentParser(
        description="digest md カード URL が articles.jsonl に全て append されているか突合する gate"
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

    missing = reconcile(args.digest_dir, args.articles, args.issue_date)
    if missing:
        print(
            f"FAIL: digest md に掲載されたが articles.jsonl に append 漏れの URL {len(missing)} 件 "
            f"(号日 {args.issue_date}):",
            file=sys.stderr,
        )
        for m in missing[:40]:
            print(f"  - {m}", file=sys.stderr)
        if len(missing) > 40:
            print(f"  ... and {len(missing) - 40} more", file=sys.stderr)
        return 1

    print(f"PASS: digest md カード URL は全て articles.jsonl に存在 (号日 {args.issue_date})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
