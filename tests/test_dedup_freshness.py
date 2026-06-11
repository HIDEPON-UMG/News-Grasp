#!/usr/bin/env python3
"""`tools/dedup.py --freshness-gate` 強化版の契約テスト (2026-06-11)。

# 検証する「なぜ重要か」

鮮度ゲートは従来 URL パスの**日単位日付**しか見ておらず、
  - `/2026/01/slug` のように**月までしか持たない URL** (crowdfundinsider 型)
  - そもそも URL に日付が無い記事
を fail-open で素通りさせていた。結果、当日号に数か月前の記事が混入し得た。

本テストは強化版が以下を locked-in することを保証する:

  1. 月単位 URL `/2026/01/` の古候補 → drop (crowdfundinsider 型再現)
  2. 月単位 URL **当月** → drop されない (誤爆ゼロ)。htmldate None なら warn-pass
  3. htmldate 補完: 古い→drop / 新しい→pass + published_date・date_evidence_source 注釈 /
     None→warn-pass (注釈なし)
  4. 既存 `extract_source_date_from_url` は月単位 URL に対して従来どおり None を返す
     (push 前 gate 等の他呼出元の互換性を壊さない)
  5. fetch 上限: URL 日付なし 21 件で htmldate fetch は 20 回しか呼ばれない
  6. (validate_daily_quality の刻印検証は test_validate_daily_quality.py で別途)

すべてネットワークに出ない (date_fetch_fn を注入)。NEWS_GRASP_SKIP_URL_CHECK=1 が
立っていても htmldate 経路を検証できるよう、各テストで monkeypatch.delenv する。

実行:
  pytest tests/test_dedup_freshness.py -v
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import dedup  # noqa: E402
from dedup import dedup_candidates  # noqa: E402

JST = timezone(timedelta(hours=9))

# 基準日を固定注入する (テストが暦に依存しない)。当日 = 2026-06-11。
NOW = datetime(2026, 6, 11, 6, 0, tzinfo=JST)


def _cand(title: str, url: str) -> dict:
    return {"title": title, "url": url, "score": 90}


@pytest.fixture(autouse=True)
def _enable_fetch_path(monkeypatch):
    """NEWS_GRASP_SKIP_URL_CHECK=1 が立っていても htmldate 経路を踏めるようにする。

    本番テストコマンドは全体に SKIP_URL_CHECK=1 を立てるが、本ファイルは fetch を
    monkeypatch で完全に差し替えるためネットワークには出ない。env を消して、注入した
    date_fetch_fn が実際に呼ばれる経路を検証する。
    """
    monkeypatch.delenv("NEWS_GRASP_SKIP_URL_CHECK", raising=False)


# ── 1. 月単位 URL の古候補は drop (crowdfundinsider 型) ──────────────────────


def test_month_url_old_is_dropped():
    """`/2026/01/` の月単位 URL は、当月 (2026-06) より古い月なので drop する。"""
    cand = _cand(
        "Crowdfunding platform old story",
        "https://www.crowdfundinsider.com/2026/01/old-fintech-story/",
    )
    # 古い月なので htmldate 補完前に月粒度で落ちる = fetch は呼ばれない
    def _no_fetch(url):  # noqa: ARG001
        raise AssertionError("古い月は月粒度で drop され fetch されないはず")

    passed, dropped = dedup_candidates(
        [cand], [], freshness_gate=True, max_source_age_days=1,
        now=NOW, date_fetch_fn=_no_fetch,
    )
    assert len(passed) == 0, f"古い月単位 URL は drop される契約: passed={passed}"
    assert len(dropped) == 1
    assert "source month 2026-01" in dropped[0].get("dedup_reason", ""), (
        f"月粒度 drop 理由が付くはず: {dropped[0].get('dedup_reason')}"
    )


# ── 2. 月単位 URL 当月は drop されない (誤爆ゼロ) ─────────────────────────────


def test_month_url_current_month_not_dropped_warn_pass():
    """当月 (2026-06) の月単位 URL は確定扱いしないが drop もしない。

    htmldate None → warn-pass (注釈は url-path-month のみ残す)。当月記事を月粒度だけで
    誤爆 drop しないことを locked-in する。
    """
    cand = _cand(
        "Crowdfunding platform fresh story",
        "https://www.crowdfundinsider.com/2026/06/fresh-fintech-story/",
    )
    passed, dropped = dedup_candidates(
        [cand], [], freshness_gate=True, max_source_age_days=1,
        now=NOW, date_fetch_fn=lambda url: None,  # htmldate 解決不能
    )
    assert len(dropped) == 0, f"当月の月単位 URL は drop しない契約: dropped={dropped}"
    assert len(passed) == 1
    # 月証拠だけは url-path-month として残る (確定扱いではない)
    assert passed[0].get("date_evidence_source") == "url-path-month"
    assert passed[0].get("published_date") == "2026-06-01"


def test_month_url_current_month_htmldate_resolves_fresh():
    """当月の月単位 URL でも htmldate が当日を解決すれば htmldate 注釈で通過する。"""
    cand = _cand(
        "Crowdfunding platform today story",
        "https://www.crowdfundinsider.com/2026/06/today-fintech-story/",
    )
    passed, dropped = dedup_candidates(
        [cand], [], freshness_gate=True, max_source_age_days=1,
        now=NOW, date_fetch_fn=lambda url: date(2026, 6, 11),
    )
    assert len(dropped) == 0
    assert passed[0].get("date_evidence_source") == "htmldate"
    assert passed[0].get("published_date") == "2026-06-11"


# ── 3. htmldate 補完 (URL 日付なし候補) ──────────────────────────────────────


def test_htmldate_old_is_dropped():
    """URL 日付なしでも htmldate が古い公開日を返せば drop する。"""
    cand = _cand("No date in url, but old", "https://example.com/topic/old-news")
    passed, dropped = dedup_candidates(
        [cand], [], freshness_gate=True, max_source_age_days=1,
        now=NOW, date_fetch_fn=lambda url: date(2026, 3, 5),
    )
    assert len(passed) == 0
    assert "via htmldate" in dropped[0].get("dedup_reason", ""), (
        f"htmldate drop 理由が付くはず: {dropped[0].get('dedup_reason')}"
    )


def test_htmldate_fresh_passes_with_annotation():
    """URL 日付なしで htmldate が新しい公開日を返せば pass + htmldate 注釈が付く。"""
    cand = _cand("No date in url, fresh", "https://example.com/topic/fresh-news")
    passed, dropped = dedup_candidates(
        [cand], [], freshness_gate=True, max_source_age_days=1,
        now=NOW, date_fetch_fn=lambda url: date(2026, 6, 11),
    )
    assert len(dropped) == 0
    assert passed[0].get("published_date") == "2026-06-11"
    assert passed[0].get("date_evidence_source") == "htmldate"


def test_htmldate_none_is_warn_pass_without_annotation():
    """URL 日付なし + htmldate None は warn-pass。注釈は一切付かない。"""
    cand = _cand("No date, unresolved", "https://example.com/topic/unknown-news")
    passed, dropped = dedup_candidates(
        [cand], [], freshness_gate=True, max_source_age_days=1,
        now=NOW, date_fetch_fn=lambda url: None,
    )
    assert len(dropped) == 0, f"日付不明は drop しない (warn-pass) 契約: dropped={dropped}"
    assert passed[0].get("published_date") is None
    assert passed[0].get("date_evidence_source") is None


# ── 4. 既存関数の後方互換 (他呼出元を壊さない) ───────────────────────────────


def test_legacy_extract_returns_none_for_month_url():
    """`extract_source_date_from_url` (既存) は月単位 URL に対し従来どおり None を返す。

    push 前 gate (audit_all_article_urls) 等が「URL 由来 = 日単位日付 or None」前提で
    動いているため、月粒度を既存関数に漏らすと新着誤判定で fatal を起こす。
    """
    month_urls = [
        "https://www.crowdfundinsider.com/2026/01/old-fintech-story/",
        "https://www.crowdfundinsider.com/2026/06/fresh-fintech-story/",
    ]
    for url in month_urls:
        assert dedup.extract_source_date_from_url(url) is None, (
            f"既存関数は月単位 URL に None を返す契約: {url}"
        )
    # 日単位 URL は従来どおり日付を返す (回帰防止)
    assert dedup.extract_source_date_from_url(
        "https://www.cnbc.com/2026/02/17/sample.html"
    ) == date(2026, 2, 17)
    # 粒度付き関数は month を返す
    d, gran = dedup.extract_source_date_from_url_with_granularity(month_urls[0])
    assert (d, gran) == (date(2026, 1, 1), "month")


# ── 5. fetch 上限 (1 実行あたり date_fetch_cap 件まで) ───────────────────────


def test_fetch_cap_limits_htmldate_calls():
    """URL 日付なし 21 件で htmldate fetch は date_fetch_cap (=20) 回しか呼ばれない。

    21 件目以降は fetch せず warn-pass。呼び出し回数を計測して上限を locked-in する。
    """
    calls = {"n": 0}

    def _counting_fetch(url):  # noqa: ARG001
        calls["n"] += 1
        return None  # 全件 htmldate None → warn-pass (drop なし)

    # タイトル類似 dedup で内部重複扱いされないよう、互いに無関係な固有名詞タイトルにする
    # (鮮度ゲートの fetch 上限だけを検証したいので、重複判定の混入を避ける)。
    topics = [
        "Acme robotics quarterly earnings", "Brightwave solar grid expansion",
        "Cobalt mining royalty dispute", "Delphi pharma trial readout",
        "Everest cloud outage postmortem", "Falcon satellite launch delay",
        "Granite bank merger approval", "Halcyon chip foundry permit",
        "Indigo airline route opening", "Juniper coffee export tariff",
        "Kestrel drone delivery pilot", "Lumen broadband rural rollout",
        "Magnolia hospital staffing rule", "Nimbus weather model upgrade",
        "Orchid biotech patent grant", "Pinnacle steel furnace closure",
        "Quasar telescope first light", "Radiant fusion funding round",
        "Sequoia forest carbon credit", "Tundra logistics warehouse deal",
        "Umbra security breach report",
    ]
    cands = [
        _cand(topics[i], f"https://example.com/topic/news-{i}")
        for i in range(21)
    ]
    passed, dropped = dedup_candidates(
        cands, [], freshness_gate=True, max_source_age_days=1,
        now=NOW, date_fetch_cap=20, date_fetch_fn=_counting_fetch,
    )
    assert calls["n"] == 20, f"fetch は date_fetch_cap=20 回まで: 実測 {calls['n']}"
    # 全件 warn-pass (drop は無い)。21 件すべて通過する
    assert len(dropped) == 0, f"鮮度 warn-pass は drop しない: dropped={[d.get('title') for d in dropped]}"
    assert len(passed) == 21
