#!/usr/bin/env python3
"""記事公開日の独立証拠検証 (2026-06-11 偽日付事故の恒久対策)。

2026-06-11 号で、LLM 自己申告の date (2026-06-11) により実発表 2026-03-05 の
記事 (OpenAI GPT-5.4) が当日記事として digest TOP に掲載された。既存の鮮度
ゲートは URL パス内日付 (extract_source_date_from_url) の 1 系統しか独立証拠を
持たず、URL に日付が無い記事は自己申告を無条件信頼する fail-open だった。

本モジュールは「出版社の自己申告 × 独立観測のクロスチェック」二系統を提供する:

1. htmldate: 記事 HTML から公開日を独立抽出 (OGP/JSON-LD/構造/テキストの多段)。
   original_date=True で初出日を優先 (dateModified によるフレッシュニング対策)。
2. Wayback CDX: htmldate が None の記事のフォールバック。claimed より古い
   スナップショットの存在 = 偽日付の確実な否定証拠 (改竄不能な第三者観測)。

判定 (evaluate_date_evidence / date_discrepancy_verdict) はネット非依存の
純関数として分離し、fetch (fetch_html / wayback_earliest_snapshot) と切り離す。
契約テスト (tests/test_date_evidence.py) は純関数だけをオフラインで叩く。
"""
from __future__ import annotations

import gzip
import io
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parent.parent
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from tools.validate_deepdive_urls import _UAS  # noqa: E402

# Wayback CDX 照合が timeout/エラーで判定不能だったことを示す sentinel。
# None (= スナップショット無し) と区別するために必要 (None は「通過+警告」、
# AMBIGUOUS は「オフライン誤発火防止のため warn のみ」と扱いが異なる)。
AMBIGUOUS = "ambiguous"

# 乖離許容日数。米国時間夕方発表 = JST 翌日のタイムゾーン差は正当なので 1 日。
# (2026-06-11 ユーザー決定: 週末発表→月曜掲載の正当ケースが落ちたら個別対応)
DEFAULT_ALLOWED_LAG_DAYS = 1

# タイトル整合の警告閾値 (containment 類似度)。fail-open で開始し実データで調整。
DEFAULT_TITLE_WARN_THRESHOLD = 0.25

_WAYBACK_CDX = "https://web.archive.org/cdx/search/cdx"


@dataclass
class DateEvidence:
    """1 記事分の独立日付証拠の判定結果。fatal_reason が非 None なら公開不可。"""

    url: str
    claimed: date
    extracted: date | None = None
    method: str = "none"  # "htmldate" | "wayback" | "none"
    fatal_reason: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.fatal_reason is None


def fetch_html(url: str, *, timeout: float = 15.0) -> str | None:
    """記事 HTML を full GET で取得する (htmldate / タイトル整合用)。

    audit_all_article_urls の liveness 検証 (HEAD / GET range 4KB) は body を
    持たないため、日付証拠が必要な当日分のみ本関数で全文を取る。
    UA は validate_deepdive_urls と同じ 2 段フォールバック (anti-bot 剥がし)。
    取得不能なら None (呼び出し側で Wayback フォールバックへ)。
    """
    for ua in _UAS:
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": ua,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9,ja;q=0.8",
            })
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    try:
                        raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                    except OSError:
                        pass
                charset = resp.headers.get_content_charset() or "utf-8"
                try:
                    return raw.decode(charset, errors="replace")
                except LookupError:
                    return raw.decode("utf-8", errors="replace")
        except (urllib.error.URLError, OSError, ValueError):
            continue
    return None


def extract_published_date(html: str, *, max_date: date) -> date | None:
    """htmldate で HTML から記事日付の上界 (初出日と更新日の新しい方) を抽出する。

    original (初出日) でなく max(original, modified) を判定に使う理由:
    live ブログ・リリースノート等の継続更新ページは original が「ページ最古
    日付」になり、当日記事でも数十日古く誤爆する (2026-06-10 実測: Google
    release-notes は original=04-22 / modified=06-09)。「本当に古い記事」は
    両方とも古い (GPT-5.4 検体は両方 2026-03-05) ので、新しい方を上界として
    判定すれば正当検知を保ったまま誤爆だけが消える。トレードオフ: dateModified
    を自動更新するフレッシュニング系サイトの偽装は本関数では逃す (Wayback /
    既存 URL パス日付チェックが部分カバー)。

    max_date=号日付: 未来日付の誤抽出を防ぐ。抽出不能なら None。
    """
    try:
        from htmldate import find_date
    except ImportError:
        return None
    results: list[date] = []
    for original in (True, False):
        try:
            r = find_date(
                html,
                original_date=original,
                extensive_search=True,
                max_date=max_date.strftime("%Y-%m-%d"),
            )
        except Exception:
            r = None
        if r:
            try:
                results.append(datetime.strptime(r, "%Y-%m-%d").date())
            except ValueError:
                pass
    return max(results) if results else None


def date_discrepancy_verdict(
    claimed: date,
    extracted: date,
    *,
    allowed_lag_days: int = DEFAULT_ALLOWED_LAG_DAYS,
) -> str | None:
    """自己申告 date と独立抽出日の乖離を判定する。fatal なら理由文字列を返す。

    古い方向のみ厳格に見る: extracted が claimed より allowed_lag_days を超えて
    古い = 「過去の記事を当日の新着と申告」の偽日付。新しい方向は max_date
    クランプで原理的に出ないため判定しない。
    """
    lag = (claimed - extracted).days
    if lag > allowed_lag_days:
        return (
            f"独立抽出した公開日 {extracted.isoformat()} が自己申告 date "
            f"{claimed.isoformat()} より {lag} 日古い (許容 {allowed_lag_days} 日)"
        )
    return None


def wayback_earliest_snapshot(url: str, *, timeout: float = 60.0):
    """Wayback CDX API で最古スナップショット日を取る。

    返り値: date (最古スナップ日) | None (スナップ無し) | AMBIGUOUS (API 失敗)。
    CDX はレート不安定 (2026-06-11 実測で timeout 1 回) のため、htmldate None の
    少数記事のみに使うフォールバック専用。失敗は AMBIGUOUS としてオフライン
    誤発火を防ぐ (audit_all_article_urls の ambiguous 設計と同じ思想)。
    """
    q = urllib.parse.urlencode({
        "url": url,
        "output": "json",
        "limit": "1",
        "fl": "timestamp",
        "filter": "statuscode:200",
    })
    try:
        req = urllib.request.Request(f"{_WAYBACK_CDX}?{q}", headers={"User-Agent": _UAS[0]})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return AMBIGUOUS
    # 形式: [["timestamp"], ["20260305182311"]] / スナップ無しは [] または header のみ
    if not isinstance(data, list) or len(data) < 2:
        return None
    ts = str(data[1][0]) if data[1] else ""
    if not re.fullmatch(r"\d{14}", ts):
        return None
    try:
        return datetime.strptime(ts[:8], "%Y%m%d").date()
    except ValueError:
        return None


class _TitleParser(HTMLParser):
    """og:title 優先・<title> フォールバックでページタイトルを抽出する。

    WebFetch は LLM 要約で <meta> を落とすため使えない
    (feedback_webfetch_ogp_unfit の urllib + html.parser 実績パターン)。
    """

    def __init__(self) -> None:
        super().__init__()
        self.og_title: str | None = None
        self.title_parts: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "meta":
            d = dict(attrs)
            if d.get("property") == "og:title" and d.get("content"):
                self.og_title = self.og_title or d["content"]
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)


def extract_page_title(html: str) -> str | None:
    p = _TitleParser()
    try:
        # <head> はほぼ先頭にあるため 200KB で打ち切り (巨大ページの無駄パース防止)
        p.feed(html[:200_000])
    except Exception:
        pass
    if p.og_title:
        return p.og_title.strip()
    t = "".join(p.title_parts).strip()
    return t or None


_TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)


def title_similarity(record_title: str, page_title: str) -> float:
    """英数字トークンの containment 類似度 (0.0-1.0)。

    Jaccard でなく containment (共通 / 短い方) を使う理由: 実ページの <title> は
    「記事名 | サイト名」のようにサフィックスが付くため、Jaccard だと正当な
    記事でもスコアが沈む。record 側タイトルが実ページに包含されていれば高得点。
    """
    a = {t.lower() for t in _TOKEN_RE.findall(record_title)}
    b = {t.lower() for t in _TOKEN_RE.findall(page_title)}
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def evaluate_date_evidence(
    claimed: date,
    url: str,
    html: str | None,
    *,
    record_title: str | None = None,
    allowed_lag_days: int = DEFAULT_ALLOWED_LAG_DAYS,
    title_warn_threshold: float = DEFAULT_TITLE_WARN_THRESHOLD,
    wayback_fn=wayback_earliest_snapshot,
) -> DateEvidence:
    """1 記事の独立日付証拠を総合判定する (ネット非依存の純関数)。

    HTML と Wayback 結果 (wayback_fn で注入可能 = テストでモック) を入力に、
    2026-06-11 確定ポリシーを適用する:
      - htmldate 抽出成功 → 乖離 > allowed_lag_days = fatal
      - 抽出 None → Wayback: claimed より古いスナップ = fatal /
        スナップ無し = 通過 + 警告 / AMBIGUOUS = 通過 + 警告
      - タイトル整合 < 閾値 = 警告のみ (fail-open で開始)
    """
    ev = DateEvidence(url=url, claimed=claimed)

    if html:
        extracted = extract_published_date(html, max_date=claimed)
        if extracted is not None:
            ev.extracted = extracted
            ev.method = "htmldate"
            ev.fatal_reason = date_discrepancy_verdict(
                claimed, extracted, allowed_lag_days=allowed_lag_days
            )
        if record_title:
            page_title = extract_page_title(html)
            if page_title:
                sim = title_similarity(record_title, page_title)
                if sim < title_warn_threshold:
                    ev.warnings.append(
                        f"タイトル整合が低い (similarity={sim:.2f}): "
                        f"record='{record_title[:60]}' page='{page_title[:60]}'"
                    )
            else:
                ev.warnings.append("実ページタイトルを抽出できず整合チェック skip")

    if ev.method == "none":
        # htmldate で証拠が取れない記事のみ Wayback フォールバック
        snap = wayback_fn(url)
        if snap == AMBIGUOUS:
            ev.warnings.append(
                "独立日付証拠なし (htmldate=None, Wayback CDX 失敗=ambiguous)。素通り注意"
            )
        elif snap is None:
            ev.warnings.append(
                "独立日付証拠なし (htmldate=None, Wayback スナップショット無し)。"
                "新着の可能性が高いが未確証"
            )
        else:
            ev.extracted = snap
            ev.method = "wayback"
            # スナップショットは「公開日 ≤ スナップ日」の上界証明。claimed より
            # allowed_lag_days を超えて古いスナップがある = claimed は偽日付。
            ev.fatal_reason = date_discrepancy_verdict(
                claimed, snap, allowed_lag_days=allowed_lag_days
            )
            if ev.fatal_reason:
                ev.fatal_reason = "Wayback 否定証拠: " + ev.fatal_reason

    return ev
