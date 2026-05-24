#!/usr/bin/env python3
"""digest md 内の `ng-thumb-common-*` フォールバック URL を実 OGP URL に置き換える。

routine (runner.bat の Sonnet 4.6) が `tools/fetch_ogp.py` を呼ばず最初から
共通フォールバックを digest に書き込んでしまうケースに対する事後リカバリ。
(2026-05-25 検証で 40〜80% の記事で発生していることが判明)

使い方:
    python tools/recover_thumbs.py            # 全 digest を走査して書き換え
    python tools/recover_thumbs.py --dry-run  # 検出のみ (書き換えない)
    python tools/recover_thumbs.py --glob "digest/AI/2026-05-2*.md"

挙動:
    1. digest md を全件スキャン
    2. 各記事ブロックで `![thumb](https://.../ng-thumb-common-*.jpg)` を検出
    3. その記事の `🔗 [元記事](https://...)` URL を抽出
    4. `tools/fetch_ogp.py` 相当の処理で OGP を取得 (キャッシュ付き)
    5. og_image / twitter_image が取れたら digest md の thumb 行を書き換え
    6. 失敗時は fallback URL を維持 (= 元記事が本当に OGP を持っていない)

副作用: digest md ファイルを書き換える。Git で確認してから commit すること。

由来: 2026-05-25 ユーザーから「サムネイル画像が全部とれていないことはおかしい」の
指摘で原因調査 → routine 遵守問題と判明 → 既存 digest の修復用に作成。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

_PKG_ROOT = Path(__file__).resolve().parent.parent
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from tools.fetch_ogp import fetch_ogp  # noqa: E402

# digest md の各記事ブロックは `### [score] title` から `---` までで区切られる。
_ARTICLE_BLOCK_RE = re.compile(r"(### \[[^\]]+\].*?)(?=\r?\n---\r?\n|\Z)", re.DOTALL)
_THUMB_LINE_RE = re.compile(r"!\[thumb\]\((https?://[^)]+)\)")
_SOURCE_URL_RE = re.compile(r"🔗\s*\[[^\]]+\]\((https?://[^)]+)\)")
_FALLBACK_PATTERN = re.compile(r"/ng-thumb-common-(?:fx|ai|it|economy|game)\.(?:jpg|png)$", re.I)

# 過去判定済み URL のキャッシュ (URL → og_image_or_null)
_CACHE_PATH = _PKG_ROOT / "tests" / "output" / "recover_cache.json"


def load_cache() -> dict[str, str | None]:
    if _CACHE_PATH.exists():
        try:
            return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def save_cache(cache: dict[str, str | None]) -> None:
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_PATH.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")


def is_fallback(thumb_url: str) -> bool:
    return bool(_FALLBACK_PATTERN.search(thumb_url))


def resolve_real_ogp(source_url: str, cache: dict[str, str | None]) -> str | None:
    """記事 URL から OGP URL を解決。キャッシュヒットならそれを返す。失敗時 None。"""
    if source_url in cache:
        return cache[source_url]
    try:
        result = fetch_ogp(source_url, timeout=10.0)
    except Exception as exc:
        print(f"  [error] fetch_ogp failed for {source_url[:80]}: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        cache[source_url] = None
        return None
    og = result.get("og_image") or result.get("twitter_image")
    if og:
        cache[source_url] = og
        return og
    cache[source_url] = None
    return None


def process_digest(path: Path, cache: dict[str, str | None], dry_run: bool) -> dict[str, int]:
    """1 つの digest md を走査して fallback URL を実 OGP に置き換える。

    Returns:
        {"found": fallback 件数, "replaced": 置換成功件数, "failed": 取得失敗件数}
    """
    text = path.read_text(encoding="utf-8")
    original = text
    stats = {"found": 0, "replaced": 0, "failed": 0}

    # 各記事ブロックを取り出して個別処理
    for m in _ARTICLE_BLOCK_RE.finditer(text):
        block = m.group(1)
        thumb_match = _THUMB_LINE_RE.search(block)
        if not thumb_match:
            continue
        thumb_url = thumb_match.group(1)
        if not is_fallback(thumb_url):
            continue
        # source URL を取り出す
        src_match = _SOURCE_URL_RE.search(block)
        if not src_match:
            continue
        source_url = src_match.group(1)
        stats["found"] += 1

        real = resolve_real_ogp(source_url, cache)
        if real:
            text = text.replace(f"![thumb]({thumb_url})", f"![thumb]({real})", 1)
            stats["replaced"] += 1
            print(f"  [OK]   {source_url[:70]}\n         → {real[:90]}")
        else:
            stats["failed"] += 1
            print(f"  [skip] {source_url[:70]} (OGP 取得不能)")

    if text != original and not dry_run:
        path.write_text(text, encoding="utf-8")
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="digest md の fallback thumb URL を実 OGP に置換")
    parser.add_argument("--glob", default="digest/**/*.md",
                        help="対象 glob (default: digest/**/*.md)")
    parser.add_argument("--dry-run", action="store_true", help="検出のみで書き換えない")
    args = parser.parse_args(argv)

    if getattr(sys.stdout, "encoding", "").lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass

    cache = load_cache()
    targets = sorted(_PKG_ROOT.glob(args.glob))
    if not targets:
        print(f"対象 0 件 ({args.glob})", file=sys.stderr)
        return 1

    total = {"files": 0, "found": 0, "replaced": 0, "failed": 0}
    for path in targets:
        try:
            rel = path.relative_to(_PKG_ROOT)
        except ValueError:
            rel = path
        print(f"\n--- {rel} ---")
        stats = process_digest(path, cache, args.dry_run)
        if stats["found"] == 0:
            print("  (fallback 検出なし)")
        total["files"] += 1
        total["found"] += stats["found"]
        total["replaced"] += stats["replaced"]
        total["failed"] += stats["failed"]

    save_cache(cache)
    print(f"\n========")
    print(f"対象 {total['files']} ファイル / fallback 検出 {total['found']} 件")
    print(f"  置換成功 {total['replaced']} / 取得不能 {total['failed']}")
    if args.dry_run:
        print("(dry-run のため digest md は書き換えていません)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
