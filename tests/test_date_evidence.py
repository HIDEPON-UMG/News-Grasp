"""tools/date_evidence.py の契約テスト (2026-06-11 偽日付事故の Lv4 locked-in)。

意図: 「LLM 自己申告 date と独立証拠 (htmldate / Wayback) が乖離した記事は
公開状態として表現不能 (gate fatal)」という不変条件を、事故の実例
(OpenAI GPT-5.4: 実発表 2026-03-05 を date: 2026-06-11 と自己申告して
digest TOP 掲載) を模した fixture でネット非依存に固定する。

全テストはオフラインで完結する (HTML は文字列 fixture、Wayback は wayback_fn
注入でモック)。本物の HTTP を叩くのは runner step 2.6 の gate 実走のみ。
"""
from __future__ import annotations

from datetime import date

import pytest

from tools.date_evidence import (
    AMBIGUOUS,
    date_discrepancy_verdict,
    evaluate_date_evidence,
    extract_page_title,
    extract_published_date,
    title_similarity,
)

ISSUE = date(2026, 6, 11)  # 事故当日の号日付

# 事故検体を模した HTML: メタタグに公開日 (2026-03-05 = 実発表日)。
# 実検体 (openai.com/index/introducing-gpt-5-4/) はメタ無し・本文テキスト
# 「March 5, 2026」のみだったが、htmldate がテキスト走査でも 2026-03-05 を
# 返すことは 2026-06-11 に実 HTML で実測済み。契約テストは決定論性のため
# メタタグ形式で固定する (検証対象は「抽出後の判定」の不変条件)。
FAKE_DATE_HTML = """<html><head>
<meta property="article:published_time" content="2026-03-05T18:00:00Z"/>
<meta property="og:title" content="Introducing GPT-5.4 | OpenAI"/>
<title>Introducing GPT-5.4 | OpenAI</title>
</head><body><p>We are introducing GPT-5.4, our most capable model.</p></body></html>"""

FRESH_HTML = """<html><head>
<meta property="article:published_time" content="2026-06-10T22:00:00Z"/>
<meta property="og:title" content="Some Fresh AI News | Example"/>
</head><body><p>fresh article</p></body></html>"""

TWO_DAY_OLD_HTML = FRESH_HTML.replace("2026-06-10", "2026-06-09")

# 継続更新ページ (リリースノート / live ブログ): 初出は古いが当日更新あり。
# 2026-06-10 実測 (Google release-notes: original=04-22 / modified=06-09) の再現。
# original だけで判定すると誤爆するため「新しい方を上界」とする設計の locked-in。
LIVING_PAGE_HTML = """<html><head>
<meta property="article:published_time" content="2026-04-22T10:00:00Z"/>
<meta property="article:modified_time" content="2026-06-09T15:00:00Z"/>
<meta property="og:title" content="Release notes | Example Cloud"/>
</head><body><p>release notes updated regularly</p></body></html>"""

# 日付の手がかりが一切無い HTML (htmldate が None を返す → Wayback フォールバック)
NO_DATE_HTML = """<html><head>
<meta property="og:title" content="Undated Press Release | Vendor"/>
</head><body><p>no date anywhere in this page body text at all</p></body></html>"""


# ---------------------------------------------------------------- 抽出層

def test_extract_published_date_from_meta() -> None:
    assert extract_published_date(FAKE_DATE_HTML, max_date=ISSUE) == date(2026, 3, 5)


def test_extract_published_date_none_when_undated() -> None:
    assert extract_published_date(NO_DATE_HTML, max_date=ISSUE) is None


# ---------------------------------------------------------------- 乖離判定 (純関数)

def test_discrepancy_98_days_is_fatal() -> None:
    reason = date_discrepancy_verdict(ISSUE, date(2026, 3, 5))
    assert reason is not None and "98 日" in reason


def test_discrepancy_timezone_lag_1day_is_ok() -> None:
    # 米国時間夕方発表 = JST 翌日は正当 (許容 1 日)
    assert date_discrepancy_verdict(ISSUE, date(2026, 6, 10)) is None


def test_discrepancy_2days_is_fatal() -> None:
    assert date_discrepancy_verdict(ISSUE, date(2026, 6, 9)) is not None


# ---------------------------------------------------------------- 総合判定 (事故再現 ★Lv4 本体)

def test_fake_date_article_is_fatal() -> None:
    """事故の不変条件: 3 ヶ月前の記事を当日 date で自己申告 → 公開不可 (fatal)。

    これが通らなくなる変更は 2026-06-11 偽日付事故の再発経路を開くため禁止。
    """
    ev = evaluate_date_evidence(
        ISSUE,
        "https://example.com/introducing-gpt-5-4/",  # URL パスに日付なし = 旧ゲートの死角
        FAKE_DATE_HTML,
        record_title="Introducing GPT-5.4",
        wayback_fn=lambda url: pytest.fail("htmldate 成功時は Wayback を呼ばない契約"),
    )
    assert not ev.ok
    assert ev.method == "htmldate"
    assert ev.extracted == date(2026, 3, 5)


def test_living_page_with_recent_update_passes() -> None:
    """継続更新ページ (初出 04-22 / 更新 06-09) は claimed 06-10 で誤爆しない。

    判定は max(original, modified) の上界で行う設計の locked-in。これが壊れると
    リリースノート・live ブログ出典の正当記事で毎朝 gate が止まる。
    """
    ev = evaluate_date_evidence(
        date(2026, 6, 10),  # 実測ケースの号日付 (claimed 06-10 / modified 06-09 = 乖離 1 日)
        "https://example.com/release-notes", LIVING_PAGE_HTML,
        wayback_fn=lambda url: pytest.fail("htmldate 成功時は Wayback を呼ばない契約"),
    )
    assert ev.ok
    assert ev.extracted == date(2026, 6, 9)


def test_fresh_article_passes() -> None:
    ev = evaluate_date_evidence(
        ISSUE, "https://example.com/fresh/", FRESH_HTML,
        record_title="Some Fresh AI News",
        wayback_fn=lambda url: pytest.fail("htmldate 成功時は Wayback を呼ばない契約"),
    )
    assert ev.ok and ev.method == "htmldate"


def test_undated_with_old_wayback_snapshot_is_fatal() -> None:
    """htmldate None でも Wayback に claimed より古いスナップがあれば偽日付確定。"""
    ev = evaluate_date_evidence(
        ISSUE, "https://example.com/undated/", NO_DATE_HTML,
        wayback_fn=lambda url: date(2026, 3, 5),
    )
    assert not ev.ok
    assert ev.method == "wayback"
    assert "Wayback 否定証拠" in ev.fatal_reason


def test_undated_without_snapshot_passes_with_warning() -> None:
    """確定ポリシー: スナップ無し = 新着の可能性が高いので通過 + 警告ログ。"""
    ev = evaluate_date_evidence(
        ISSUE, "https://example.com/undated/", NO_DATE_HTML,
        wayback_fn=lambda url: None,
    )
    assert ev.ok
    assert any("スナップショット無し" in w for w in ev.warnings)


def test_undated_with_ambiguous_wayback_passes_with_warning() -> None:
    """確定ポリシー: CDX 失敗 (レート/timeout) はオフライン誤発火防止で warn のみ。"""
    ev = evaluate_date_evidence(
        ISSUE, "https://example.com/undated/", NO_DATE_HTML,
        wayback_fn=lambda url: AMBIGUOUS,
    )
    assert ev.ok
    assert any("ambiguous" in w for w in ev.warnings)


def test_fetch_failure_falls_back_to_wayback() -> None:
    """HTML 取得不能 (anti-bot 等) でも Wayback の否定証拠は効く。"""
    ev = evaluate_date_evidence(
        ISSUE, "https://example.com/blocked/", None,
        wayback_fn=lambda url: date(2026, 3, 5),
    )
    assert not ev.ok and ev.method == "wayback"


# ---------------------------------------------------------------- 出典整合 (警告モード)

def test_title_mismatch_warns_but_not_fatal() -> None:
    """取り違え/捏造疑いは現段階では警告のみ (fail-open で閾値調整中)。"""
    ev = evaluate_date_evidence(
        ISSUE, "https://example.com/fresh/", FRESH_HTML,
        record_title="Completely Unrelated Quantum Banana Story",
        wayback_fn=lambda url: None,
    )
    assert ev.ok  # 日付は健全なので fatal にしない
    assert any("タイトル整合が低い" in w for w in ev.warnings)


def test_title_match_no_warning() -> None:
    ev = evaluate_date_evidence(
        ISSUE, "https://example.com/fresh/", FRESH_HTML,
        record_title="Some Fresh AI News",
        wayback_fn=lambda url: None,
    )
    assert not any("タイトル整合" in w for w in ev.warnings)


def test_extract_page_title_prefers_og_title() -> None:
    assert extract_page_title(FAKE_DATE_HTML) == "Introducing GPT-5.4 | OpenAI"


def test_title_similarity_containment_ignores_site_suffix() -> None:
    # 実ページ <title> の「| サイト名」サフィックスで沈まない (containment 採用理由)
    assert title_similarity("Introducing GPT-5.4", "Introducing GPT-5.4 | OpenAI") == 1.0
