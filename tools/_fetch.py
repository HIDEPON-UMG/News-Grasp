#!/usr/bin/env python3
"""BOT 対策回避 fetch を 1 箇所に集約する境界モジュール（fetch 昇格ラダー）。

News-Grasp の各ツール（fetch_ogp / date_evidence / audit_all_article_urls /
harvest_candidates）が記事 HTML を取得するとき、anti-bot で 403/blocked を返す
発行元（bloomberg / nikkei / cnbc / newspicks / nri 等）への昇格 fetch を本モジュール
1 箇所に閉じ込める。他ツールに `scrapling` を直接 import させない（= 失敗が表現できない
構造に寄せる / feedback_check_design_principles Lv2「境界 1 箇所集約」）。

## 用途の限定（重要）

本モジュールは **OGP メタ・発行日メタの抽出、および URL 生存確認** にのみ使う。
paywall の本文取得には使わない（見出し・公開部分・メタタグまで）。記事本文を
丸ごと取りに行く用途に転用しないこと（routine-system.md 3-A / 3-B の方針）。

## 昇格ラダー（3 段）

1. **urllib**（現行方式）— まずこれで GET。大半のサイトはこれで 200 が返る。
2. **Scrapling Fetcher**（curl_cffi による TLS/JA3 偽装）— urllib が 403/429/
   Cloudflare bot チャレンジ等で blocked のときのみ昇格。
3. **Scrapling StealthyFetcher**（ヘッドレスブラウザ）— Fetcher でも blocked の
   ときの最終段限定。重いので 1 プロセス（= 1 号の実行）あたり上限 10 件。

scrapling の import は関数内の遅延 import にする。scrapling 未導入環境でも urllib 段
だけで動き、テストが network marker なしで通る（= scrapling に依存せずユニットテスト
できる構造）。

## 実測根拠（2026-06-12 実打検証済・再検証不要）

- nikkei / cnbc: Fetcher で 200 + og:image 抽出成功
- bloomberg: StealthyFetcher で 200
- `scrapling.exe install`（ブラウザ取得）は本機で完走済み

依存: `scrapling[fetchers]==0.4.9`（requirements.txt に pin）。0.4.x で `css_first` は
廃止済みのため、呼び出し側は `result.select_css(...)` ヘルパ（内部で `css()` を使う）
を経由すること。
"""
from __future__ import annotations

import gzip
import io
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

# ── 定数 ─────────────────────────────────────────────────────────────────────

DEFAULT_TIMEOUT_SEC = 15.0
MAX_BYTES = 2_000_000  # <head> のメタ抽出には 2MB で十分（本文全取得はしない）

# urllib 段で使う UA（fetch_ogp / date_evidence と揃えた Chrome 系）。
_URLLIB_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

# StealthyFetcher（ヘッドレスブラウザ）の 1 プロセスあたり起動上限。重い & 不安定なので
# 最終段の暴発を防ぐ。モジュールレベルカウンタで数える（= 1 号の実行 = 1 プロセス）。
DEFAULT_STEALTHY_BUDGET = 10
_stealthy_used = 0  # プロセス内で StealthyFetcher を起動した回数（モジュールレベル）


def reset_stealthy_budget() -> None:
    """StealthyFetcher 起動カウンタをリセットする（テスト用・長時間常駐プロセス用）。"""
    global _stealthy_used
    _stealthy_used = 0


# ── 返り値 ───────────────────────────────────────────────────────────────────


@dataclass
class FetchResult:
    """fetch 昇格ラダーの結果。呼び出し側 4 箇所が必要とする情報を持つ。

    属性:
        url:       要求した URL
        status:    最終的に得た HTTP ステータスコード（取得不能なら None）
        html:      取得した HTML 本文（取得不能/非 HTML なら None）
        stage:     最終的に成功/失敗した段の名前
                   "guard" | "urllib" | "fetcher" | "stealthy" | "none"
                   ("guard" は http(s) 以外のスキームを 0 段目で拒否した場合)
        ok:        本文 HTML を取得できたか（status 2xx かつ html 非 None）
        blocked:   anti-bot による blocked 判定だったか（昇格契機の記録）
        error:     最後のエラー説明（None なら正常）
        elapsed_sec: 経過秒
        attempts:  各段の試行記録 [(stage, status, note), ...]（監査/テスト用）
    """

    url: str
    status: int | None = None
    html: str | None = None
    stage: str = "none"
    ok: bool = False
    blocked: bool = False
    error: str | None = None
    elapsed_sec: float = 0.0
    attempts: list[tuple[str, int | None, str]] = field(default_factory=list)

    def select_css(self, selector: str) -> list:
        """取得済み HTML に対する CSS セレクタ抽出（scrapling 0.4.x 互換ヘルパ）。

        0.4.x で `css_first` は廃止されたため、呼び出し側はこのヘルパ経由で `css()` を
        使う。html が無い場合は空リストを返す。scrapling の Selector を遅延 import し、
        未導入時は空リストにフォールバック（urllib 段だけの環境でも壊れない）。
        """
        if not self.html:
            return []
        try:
            from scrapling.parser import Selector  # 遅延 import
        except Exception:
            return []
        try:
            return list(Selector(self.html).css(selector))
        except Exception:
            return []


# ── blocked 判定 ─────────────────────────────────────────────────────────────

# anti-bot による blocked と見なすステータス（昇格の契機）。
_BLOCKED_STATUSES = frozenset({403, 429, 503})

# 本文に出る Cloudflare / bot チャレンジの痕跡（200 で返ってくる challenge ページ対策）。
_CHALLENGE_MARKERS = (
    "cf-browser-verification",
    "cf-challenge",
    "/cdn-cgi/challenge-platform/",
    "Checking your browser before accessing",
    "Just a moment...",
    "Attention Required! | Cloudflare",
    "Enable JavaScript and cookies to continue",
    "Access denied",
    "captcha-delivery",  # DataDome
    "px-captcha",        # PerimeterX
)


def _looks_blocked(status: int | None, html: str | None) -> bool:
    """status / 本文から anti-bot blocked を判定する純関数（テスト対象）。

    - status が 403/429/503 → blocked
    - status 200 でも本文に Cloudflare/bot チャレンジ痕跡 → blocked
      （challenge ページを 200 で返すサイトを取りこぼさない）
    """
    if status in _BLOCKED_STATUSES:
        return True
    if html:
        head = html[:4000]
        if any(marker in head for marker in _CHALLENGE_MARKERS):
            return True
    return False


# ── 各段の fetch ─────────────────────────────────────────────────────────────


def _fetch_urllib(url: str, timeout: float) -> tuple[int | None, str | None, str]:
    """1 段目: 標準ライブラリ urllib で GET。返り値 (status, html, note)。

    HTTPError は status を取り出して本文も読む（403 challenge ページの本文判定用）。
    取得不能（DNS/timeout 等）は (None, None, note)。
    """
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _URLLIB_UA,
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,ja;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            raw = resp.read(MAX_BYTES)
            raw = _maybe_gunzip(raw, resp.headers.get("Content-Encoding"))
            charset = resp.headers.get_content_charset() or "utf-8"
            html = _decode(raw, charset)
            return status, html, "ok"
    except urllib.error.HTTPError as e:
        # 403/429 等は本文も読んで blocked 判定に使う（challenge ページ検知）。
        try:
            raw = e.read(MAX_BYTES)
            raw = _maybe_gunzip(raw, e.headers.get("Content-Encoding") if e.headers else None)
            html = _decode(raw, "utf-8")
        except Exception:
            html = None
        return e.code, html, f"HTTPError {e.code}"
    except (urllib.error.URLError, OSError, ValueError) as e:
        return None, None, f"{type(e).__name__}: {e}"


def _fetch_scrapling(url: str, timeout: float, *, stealthy: bool) -> tuple[int | None, str | None, str]:
    """2/3 段目: scrapling の Fetcher / StealthyFetcher で GET。

    scrapling は遅延 import（未導入なら note にその旨を入れて (None, None) を返し、
    呼び出し側は昇格できずに直前段の結果で確定する）。
    返り値 (status, html, note)。
    """
    try:
        from scrapling.fetchers import Fetcher, StealthyFetcher  # 遅延 import
    except Exception as e:  # ImportError 含む
        return None, None, f"scrapling 未導入: {type(e).__name__}"

    try:
        if stealthy:
            # ヘッドレスブラウザ。timeout はミリ秒指定の API のため *1000。
            page = StealthyFetcher.fetch(
                url,
                headless=True,
                network_idle=True,
                timeout=int(timeout * 1000),
            )
        else:
            # curl_cffi による TLS/JA3 偽装。stealthy=True で実ブラウザ指紋を使う。
            page = Fetcher.get(
                url,
                stealthy_headers=True,
                timeout=timeout,
            )
    except Exception as e:
        stage = "stealthy" if stealthy else "fetcher"
        return None, None, f"{stage} 例外: {type(e).__name__}: {e}"

    status = getattr(page, "status", None)
    html = _extract_html_from_page(page)
    return status, html, "ok"


def _extract_html_from_page(page) -> str | None:
    """scrapling のレスポンスオブジェクトから HTML 文字列を取り出す。

    scrapling 0.4.x のレスポンスは Selector を兼ねており、`.html_content` / `.body` /
    `str(page)` のいずれかで生 HTML が取れる。バージョン差を吸収するため複数経路を試す。
    """
    for attr in ("html_content", "body"):
        val = getattr(page, attr, None)
        if isinstance(val, bytes):
            return _decode(val, "utf-8")
        if isinstance(val, str) and val:
            return val
    try:
        s = str(page)
        return s or None
    except Exception:
        return None


# ── デコード補助 ─────────────────────────────────────────────────────────────


def _maybe_gunzip(raw: bytes, content_encoding: str | None) -> bytes:
    if content_encoding == "gzip":
        try:
            return gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        except OSError:
            return raw
    return raw


def _decode(raw: bytes, charset: str) -> str:
    for enc in (charset, "utf-8", "cp932", "latin-1"):
        try:
            return raw.decode(enc, errors="strict")
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


# ── 公開関数 ─────────────────────────────────────────────────────────────────


def fetch_with_escalation(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SEC,
    stealthy_budget: int = DEFAULT_STEALTHY_BUDGET,
    allow_stealthy: bool = True,
) -> FetchResult:
    """3 段昇格ラダーで URL を fetch する（境界モジュールの唯一の公開 fetch 関数）。

    1. urllib で GET。200 系かつ blocked でなければそこで確定（昇格しない）。
    2. urllib が blocked（403/429/503 or challenge ページ）なら Fetcher へ昇格。
    3. Fetcher も blocked なら StealthyFetcher へ昇格（最終段限定・プロセス上限内のみ）。

    Args:
        url: 取得する URL。
        timeout: 1 段あたりのタイムアウト秒。
        stealthy_budget: StealthyFetcher を起動できるプロセス上限件数（既定 10）。
        allow_stealthy: False なら 3 段目を使わない（urllib + Fetcher のみ）。

    Returns:
        FetchResult。ok=True なら本文取得成功。blocked が全段で続いたら ok=False。

    用途は OGP メタ・発行日メタ・生存確認限定（paywall 本文取得には使わない）。
    """
    global _stealthy_used
    started = time.monotonic()
    result = FetchResult(url=url)

    # ── 0 段目: スキームガード（SSRF 予防）────────────────────────────────
    # 本モジュールは外部 URL を昇格 fetch する境界なので、http(s) 以外
    # (file: / ftp: 等) は将来の転用経路も含めてここで一律拒否する。
    scheme = urllib.parse.urlsplit(url).scheme.lower()
    if scheme not in ("http", "https"):
        result.attempts.append(("guard", None, f"unsupported scheme: {scheme or '(empty)'}"))
        return _finalize(
            result, "guard", None, None, started,
            blocked=False, error=f"unsupported url scheme: {scheme or '(empty)'}",
        )

    # ── 1 段目: urllib ──────────────────────────────────────────────────────
    status, html, note = _fetch_urllib(url, timeout)
    result.attempts.append(("urllib", status, note))
    blocked = _looks_blocked(status, html)
    if status is not None and 200 <= status < 300 and not blocked:
        return _finalize(result, "urllib", status, html, started, blocked=False)

    # blocked でない非 2xx（404/410/500 等）は昇格しても無意味なのでここで確定。
    # 「anti-bot blocked」のときだけ Fetcher へ昇格する。
    if not blocked:
        return _finalize(
            result, "urllib", status, html, started,
            blocked=False,
            error=note if (status is None or status >= 400) else None,
        )

    result.blocked = True

    # ── 2 段目: Scrapling Fetcher（curl_cffi 偽装）─────────────────────────────
    status2, html2, note2 = _fetch_scrapling(url, timeout, stealthy=False)
    result.attempts.append(("fetcher", status2, note2))
    blocked2 = _looks_blocked(status2, html2)
    if status2 is not None and 200 <= status2 < 300 and not blocked2:
        return _finalize(result, "fetcher", status2, html2, started, blocked=False)

    # Fetcher が scrapling 未導入や例外で status を返せなかった場合も、ここで打ち切る
    # 判断は「blocked が続いているか」で行う。非 blocked の確定エラーは昇格しない。
    if status2 is not None and not blocked2:
        return _finalize(result, "fetcher", status2, html2, started, blocked=False, error=note2)

    # ── 3 段目: StealthyFetcher（ヘッドレスブラウザ・最終段限定）───────────────
    if not allow_stealthy:
        return _finalize(result, "fetcher", status2, html2, started, blocked=True,
                         error="stealthy 無効化指定で昇格せず")
    if _stealthy_used >= stealthy_budget:
        # プロセス上限超過 → 昇格せず失敗を返す（暴発防止）。
        return _finalize(
            result, "fetcher", status2, html2, started, blocked=True,
            error=f"StealthyFetcher 上限 {stealthy_budget} 件超過のため昇格拒否",
        )
    _stealthy_used += 1
    status3, html3, note3 = _fetch_scrapling(url, timeout, stealthy=True)
    result.attempts.append(("stealthy", status3, note3))
    blocked3 = _looks_blocked(status3, html3)
    if status3 is not None and 200 <= status3 < 300 and not blocked3:
        return _finalize(result, "stealthy", status3, html3, started, blocked=False)

    return _finalize(
        result, "stealthy", status3, html3, started, blocked=True,
        error=note3 if note3 != "ok" else "全段で blocked",
    )


def _finalize(
    result: FetchResult,
    stage: str,
    status: int | None,
    html: str | None,
    started: float,
    *,
    blocked: bool,
    error: str | None = None,
) -> FetchResult:
    """FetchResult を確定して返す内部ヘルパ。"""
    result.stage = stage
    result.status = status
    result.html = html
    result.blocked = blocked
    result.ok = bool(status is not None and 200 <= status < 300 and html and not blocked)
    if error is not None:
        result.error = error
    elif not result.ok and result.error is None:
        result.error = f"stage={stage} status={status} blocked={blocked}"
    result.elapsed_sec = round(time.monotonic() - started, 3)
    return result
