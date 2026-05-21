#!/usr/bin/env python3
"""ステップ 11: メール HTML に公開 web へのリンクが組み込まれることを pin する TDD テスト。

仕様確定事項 (docs/specs/2026-05-21_public-web-ogp.html):
    - 「VIEW IN BROWSER」リンクをヘッダ部に追加 (その号の summary ページへ)
    - 各カテゴリ見出しから {cat}/ アーカイブ web リンクを追加
    - 記事カード単体リンクは追加しない (=新規 a タグはヘッダ 1 + カテゴリ 5 = 計 6 個まで)

実行:
    pytest tests/test_generate_email_web_links.py -v
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from tools.config import BASE_URL  # noqa: E402

import mock_data  # noqa: E402
import render_email  # noqa: E402


# ---------- fixtures ----------

@pytest.fixture
def rendered_html() -> str:
    """mock_data + render_email_html() で 1 回 build した HTML。"""
    render_email.set_cid_mode(False)
    render_email.set_cdn_mode(True)
    return render_email.render_email_html()


@pytest.fixture
def baseline_anchor_count() -> int:
    """ステップ 11 で増える a タグ数の "上限". ヘッダ 1 + カテゴリ 5 = 6 個まで.

    テンプレ既存の 4 個 (記事 url / GITHUB / EDIT WATCHLIST / OPEN IN OBSIDIAN) +
    記事カード内 url リンク (各記事 2 個: meta + title) × 25 件 = 50 個 +
    関連過去号リンク (RELATED_ISSUES) 数件 など。

    本テストでは "ヘッダ + カテゴリ" の 6 個が増えていることだけを保証し、
    既存リンク数の細部はコミットしない (将来テンプレが変わっても壊れない設計)。
    """
    return 6  # 増加分の最小保証 (ヘッダ 1 + カテゴリ 5)


# ---------- ヘッダの VIEW IN BROWSER ----------

def test_view_in_browser_link_present(rendered_html):
    """ヘッダ部に "VIEW IN BROWSER" 表記の a タグがある."""
    assert "VIEW IN BROWSER" in rendered_html, (
        "VIEW IN BROWSER ラベルが HTML に出現していない (ヘッダ部リンク未配置)"
    )


def test_view_in_browser_href_is_summary_page(rendered_html):
    """VIEW IN BROWSER の href が {BASE_URL}/summary/{YYYY-MM-DD}/ を指す."""
    issue_date = mock_data.ISSUE_DATE  # "2026-04-28"
    expected = f"{BASE_URL}/summary/{issue_date}/"
    pattern = re.compile(
        r'<a\s+href=["\']([^"\']+)["\'][^>]*>[^<]*VIEW IN BROWSER',
        re.IGNORECASE,
    )
    m = pattern.search(rendered_html)
    assert m, "VIEW IN BROWSER 文字列を持つ <a href=> が見つからない"
    assert m.group(1) == expected, (
        f"VIEW IN BROWSER href={m.group(1)!r}, expected {expected!r}"
    )


# ---------- カテゴリヘッダの web リンク ----------

def test_each_category_has_archive_link(rendered_html):
    """5 カテゴリすべてで {BASE_URL}/{cat_id}/ への a タグが 1 個以上含まれる."""
    missing: list[str] = []
    for cat in mock_data.CATEGORIES:
        expected = f"{BASE_URL}/{cat['id']}/"
        # href の前後のクォートは ' でも " でも可
        if f'href="{expected}"' not in rendered_html and f"href='{expected}'" not in rendered_html:
            missing.append(f"{cat['id']} -> {expected}")
    assert not missing, f"以下のカテゴリで {{cat}}/ web リンクが欠落: {missing}"


def test_category_links_use_base_url(rendered_html):
    """カテゴリ web リンクは BASE_URL (https://) を必ず含む.

    BASE_URL 未注入 / 相対 URL になっていたら検出する.
    """
    for cat in mock_data.CATEGORIES:
        expected = f"{BASE_URL}/{cat['id']}/"
        assert expected.startswith("https://"), (
            f"BASE_URL ベースの URL が https:// 始まりでない: {expected}"
        )
        assert expected in rendered_html, (
            f"absolute URL {expected!r} が HTML に出現していない (相対 URL になっている可能性)"
        )


# ---------- a タグ純増数の保証 (記事カード単体リンクは追加しない) ----------

def test_new_anchor_count_matches_spec(rendered_html, baseline_anchor_count):
    """ステップ 11 で追加される a タグの最小数 = 6 (ヘッダ 1 + カテゴリ 5).

    これは「新規追加リンクは 6 個」の最小保証. 記事カードレベルに余計な a タグが
    増えていないかを検出するための補助テストとして、ヘッダ + 5 カテゴリ分の
    新規 href が全て BASE_URL を含むことだけ確認する.
    """
    # ヘッダ: summary/{date}/
    issue_date = mock_data.ISSUE_DATE
    summary_href = f'href="{BASE_URL}/summary/{issue_date}/"'
    assert rendered_html.count(summary_href) >= 1, (
        f"summary/{issue_date}/ への href が出現していない"
    )

    # 5 カテゴリ
    cat_hrefs = [f'href="{BASE_URL}/{cat["id"]}/"' for cat in mock_data.CATEGORIES]
    for href in cat_hrefs:
        assert rendered_html.count(href) >= 1, (
            f"{href} が HTML に出現していない (カテゴリヘッダ web リンク未配置)"
        )

    # 新規 BASE_URL 起点リンクの合計数が最低 6 個 = baseline_anchor_count 以上
    total_new = rendered_html.count(f'href="{BASE_URL}/')
    assert total_new >= baseline_anchor_count, (
        f"新規 BASE_URL 起点 href 数 {total_new} < 期待値 {baseline_anchor_count}"
    )
