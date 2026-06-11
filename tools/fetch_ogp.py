"""記事 URL から OGP / Twitter Card 画像 URL を抽出する CLI ツール。

Routine の `Bash` から呼び出して、各記事の `thumb` フィールドを埋めるのに使う。
標準ライブラリのみで動作する（外部パッケージへの依存を増やさない）。

使い方:
    py tools/fetch_ogp.py "https://example.com/article"

stdout (JSON 1 行):
    {
      "url": "https://example.com/article",
      "og_image":      "https://example.com/static/og.jpg" | null,
      "twitter_image": "https://example.com/static/tw.jpg" | null,
      "status": "ok" | "http_403" | "timeout" | "skip_non_html" | ...,
      "elapsed_sec": 1.23,
      "error": null | "..."
    }

設計上の前提（routine-system.md 3-B 参照）:
- WebFetch は LLM 要約パスで <head> の <meta> を見落とす実効率約 4% の経路だった
- 本ツールは Mozilla 系 User-Agent で生 HTML を取得し、html.parser で <meta> を直接抽出
- 1 記事あたり 10 秒タイムアウト + 1 回リトライ、合計上限 12 秒
- 戻り値が全 null の場合でも Routine 側は `thumb: null` を必ず格納する（キー欠落禁止）
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse

# fetch 昇格ラダー境界モジュール（urllib → Scrapling Fetcher → StealthyFetcher）。
# bloomberg/nikkei/cnbc/newspicks/nri 等 anti-bot サイトの thumb null 解消が目的。
# tools パッケージ経由・flat 実行（python tools/fetch_ogp.py）両対応で import する。
try:
    from tools._fetch import fetch_with_escalation
except ModuleNotFoundError:
    from _fetch import fetch_with_escalation

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

DEFAULT_TIMEOUT_SEC = 10.0
DEFAULT_RETRIES = 1
MAX_BYTES = 2_000_000  # 2 MB だけ読めば <head> は十分含まれる
NON_HTML_SUFFIXES = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip")


class _OGPParser(HTMLParser):
    """HTML 全体から og:image / twitter:image の最初の 1 件を拾う。

    かつては `<body>` 突入で解析を打ち切っていたが、Next.js / React の SSR ストリーミング
    系サイト (anthropic.com 等) は SEO meta を `<head>` ではなく `<body>` より後方に
    出力するため、body-stop だと og:image を取り逃して `no_meta` に落ちる
    (2026-05-31 News-Grasp トップ記事サムネ事故)。MAX_BYTES (2MB) で読込量は既に
    上限化されているので、body-stop を外し両画像が揃った時点で早期終了する。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.og_image: str | None = None
        self.twitter_image: str | None = None
        self._stop = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._stop:
            return
        if tag != "meta":
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        prop = a.get("property", "").lower()
        name = a.get("name", "").lower()
        content = a.get("content", "")
        if not content:
            return
        if prop == "og:image" and self.og_image is None:
            self.og_image = content
        elif name in ("twitter:image", "twitter:image:src") and self.twitter_image is None:
            self.twitter_image = content
        # og:image と twitter:image が両方揃えば以降は読み飛ばす (早期終了)。
        if self.og_image is not None and self.twitter_image is not None:
            self._stop = True

    def feed_until_stop(self, data: str) -> None:
        try:
            self.feed(data)
        except Exception:
            # html.parser は壊れた HTML でたまに例外を吐くが <meta> が拾えていれば十分
            pass


def _looks_non_html(url: str) -> bool:
    path = urlparse(url).path.lower()
    return path.endswith(NON_HTML_SUFFIXES)


def _decode_html(raw: bytes, content_type: str) -> str:
    """Content-Type の charset を優先しつつ、無ければ utf-8 → cp932 の順で試す。"""
    encoding = "utf-8"
    ct = content_type.lower()
    if "charset=" in ct:
        encoding = ct.split("charset=", 1)[1].split(";", 1)[0].strip() or "utf-8"
    for enc in (encoding, "utf-8", "cp932", "latin-1"):
        try:
            return raw.decode(enc, errors="strict")
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _absolutize(base_url: str, maybe_relative: str | None) -> str | None:
    if not maybe_relative:
        return None
    if maybe_relative.startswith(("http://", "https://")):
        return maybe_relative
    if maybe_relative.startswith("//"):
        return "https:" + maybe_relative
    return urljoin(base_url, maybe_relative)


def fetch_once(url: str, timeout: float) -> tuple[str, str]:
    """1 回だけ HTTP GET（fetch 昇格ラダー経由）。戻り値: (decoded_html, content_type)。

    bloomberg/nikkei/cnbc/newspicks/nri 等の anti-bot サイトで urllib が 403/blocked を
    返す場合に Scrapling Fetcher → StealthyFetcher へ昇格して og:image を取り直す
    （2026-06-12 実測で nikkei/cnbc は Fetcher、bloomberg は StealthyFetcher で 200）。

    既存の fetch_ogp() ループは HTTPError / URLError / ValueError の status で分岐するため、
    昇格ラダーの失敗を同じ例外型に翻訳して **呼び出し側の契約を不変に保つ**:
      - blocked のまま全段失敗 / 4xx → HTTPError(status)
      - 取得不能（timeout/DNS）→ URLError
      - HTML が取れたのに content-type が非 HTML 相当 → ValueError（従来同様）
    """
    res = fetch_with_escalation(url, timeout=timeout)
    if res.ok:
        # content_type は昇格ラダーが個別に持たないため html 既定で扱う（OGP 抽出は
        # html.parser が担うので content_type は "text/html" 相当で十分）。
        return res.html or "", "text/html"
    status = res.status
    if status is not None and status >= 400:
        # 403/404/410/451 等は fetch_ogp() が status で分岐して扱う。
        raise HTTPError(url, status, res.error or "blocked", {}, None)  # type: ignore[arg-type]
    # status を取れなかった（timeout/DNS/全段例外）→ URLError 扱い。
    reason = res.error or "fetch failed"
    if "timed out" in reason.lower() or "timeout" in reason.lower():
        raise URLError("timed out")
    raise URLError(reason)


def fetch_ogp(url: str, *, timeout: float = DEFAULT_TIMEOUT_SEC, retries: int = DEFAULT_RETRIES) -> dict:
    started = time.monotonic()
    result = {
        "url": url,
        "og_image": None,
        "twitter_image": None,
        "status": "unknown",
        "elapsed_sec": 0.0,
        "error": None,
    }

    if _looks_non_html(url):
        result["status"] = "skip_non_html"
        result["elapsed_sec"] = round(time.monotonic() - started, 3)
        return result

    last_err: str | None = None
    last_status = "unknown"
    for attempt in range(retries + 1):
        try:
            html, _ct = fetch_once(url, timeout=timeout)
        except HTTPError as e:
            last_err = f"HTTP {e.code} {e.reason}"
            last_status = f"http_{e.code}"
            # 403/404/410 はリトライしても同じなので即抜ける
            if e.code in (403, 404, 410, 451):
                break
            continue
        except URLError as e:
            reason = getattr(e, "reason", e)
            last_err = f"URLError: {reason}"
            last_status = "timeout" if "timed out" in str(reason) else "url_error"
            continue
        except ValueError as e:
            last_err = str(e)
            last_status = "skip_non_html" if "non-html" in str(e) else "value_error"
            break
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            last_status = "fetch_error"
            continue

        parser = _OGPParser()
        parser.feed_until_stop(html)
        result["og_image"] = _absolutize(url, parser.og_image)
        result["twitter_image"] = _absolutize(url, parser.twitter_image)
        result["status"] = "ok" if (result["og_image"] or result["twitter_image"]) else "no_meta"
        result["elapsed_sec"] = round(time.monotonic() - started, 3)
        return result

    result["status"] = last_status
    result["error"] = last_err
    result["elapsed_sec"] = round(time.monotonic() - started, 3)
    return result


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("url", help="OGP を取りたい記事 URL")
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SEC, help=f"1 回あたりのタイムアウト秒 (default: {DEFAULT_TIMEOUT_SEC})")
    p.add_argument("--retries", type=int, default=DEFAULT_RETRIES, help=f"リトライ回数 (default: {DEFAULT_RETRIES})")
    args = p.parse_args()

    result = fetch_ogp(args.url, timeout=args.timeout, retries=args.retries)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
