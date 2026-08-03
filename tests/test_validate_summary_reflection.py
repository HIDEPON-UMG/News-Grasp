#!/usr/bin/env python3
"""tools.validate_summary_reflection の契約テスト。"""
from __future__ import annotations

from pathlib import Path

from tools.validate_summary_reflection import (
    find_latest_summary,
    main,
    validate_summary_category_focus,
    validate_summary_headline,
    validate_summary_reflection,
)


def test_validate_summary_reflection_rejects_plain_summary(tmp_path: Path) -> None:
    """本日のテーマ考察 block が無い Summary は明示エラーにする。"""
    summary = tmp_path / "2026-06-08.md"
    summary.write_text(
        "# News Grasp #20260608\n\n"
        "## 総論\n\n"
        "### 本日の3大テーマ\n\n"
        "1. AI と市場のニュース。\n",
        encoding="utf-8",
    )

    errs = validate_summary_reflection(summary)

    assert errs
    assert "reflection が空" in errs[0]
    assert "本日のテーマ考察" in "\n".join(errs)


def test_validate_summary_reflection_accepts_theme_block(tmp_path: Path) -> None:
    """generate_pages.py が読む reflection 形式なら通す。"""
    summary = tmp_path / "2026-06-08.md"
    summary.write_text(
        "# News Grasp #20260608\n\n"
        "## § 本日のテーマ考察\n\n"
        "*AI と市場が同時に動いた一日*\n\n"
        "> [[AI]] と __為替__ の変化が同じ方向を向き、**投資判断**と産業戦略が同時に更新された。"
        "今日は単一カテゴリの速報ではなく、政策イベント、企業AI、半導体供給網、ゲーム供給計画が同時に読まれる日だった。"
        "短期の市場反応と中期の実装力を分けて読み、どの材料が継続して運用できるかを確認する必要がある。"
        "LPではこの段落が読者の入口になるため、単なる一文要約ではなく、複数カテゴリの関係を十分に説明する。\n\n"
        "### §01 総論 — AI と市場の同時進行\n\n"
        "[[AI]] と __市場__ が相互に影響した。\n",
        encoding="utf-8",
    )

    assert validate_summary_reflection(summary) == []


def test_validate_summary_reflection_rejects_short_lead_even_with_sections(tmp_path: Path) -> None:
    """§ セクションがあっても、LP に出る lead が短すぎれば落とす。"""
    summary = tmp_path / "2026-06-08.md"
    summary.write_text(
        "# News Grasp #20260608\n\n"
        "## § 本日のテーマ考察\n\n"
        "*AI と市場が同時に動いた一日*\n\n"
        "> 今日の焦点は実装力だ。\n\n"
        "### §01 総論 — AI と市場の同時進行\n\n"
        "本文はある。\n",
        encoding="utf-8",
    )

    errs = validate_summary_reflection(summary)

    assert any("短すぎます" in e for e in errs)


def test_validate_summary_reflection_rejects_count_like_category_focus(tmp_path: Path) -> None:
    """カテゴリ § 見出しが件数文なら、hero の今日の焦点として使わせない。"""
    summary = tmp_path / "2026-06-08.md"
    summary.write_text(
        "# News Grasp #20260608\n\n"
        "## § 本日のテーマ考察\n\n"
        "*AI と市場が同時に動いた一日*\n\n"
        "> [[AI]] と __市場__ の変化が同じ方向を向き、**投資判断**と産業戦略が同時に更新された。"
        "今日は単一カテゴリの速報ではなく、政策イベント、企業AI、半導体供給網、ゲーム供給計画が同時に読まれる日だった。"
        "短期の市場反応と中期の実装力を分けて読み、どの材料が継続して運用できるかを確認する必要がある。"
        "LPではこの段落が読者の入口になるため、単なる一文要約ではなく、複数カテゴリの関係を十分に説明する。\n\n"
        "### §02 AI — AIは5件\n\n"
        "[[AI]] の **配布面** と __導入条件__ が焦点になった。\n"
        "- 【事実・概要】：AI関連の発表が複数出た。\n"
        "- 【背景・要点】：配布と審査の条件が変わった。\n"
        "- 【影響・展望】：次は導入前審査を見る。\n",
        encoding="utf-8",
    )

    errs = validate_summary_reflection(summary)

    assert any("category hero focus is count/list-like" in e for e in errs)


def test_validate_summary_category_focus_accepts_concise_focus_and_required_link(tmp_path: Path) -> None:
    """カテゴリ § 見出し・lanes・required category の紐付けが揃えば通す。"""
    summary = tmp_path / "2026-06-08.md"
    summary.write_text(
        "# News Grasp #20260608\n\n"
        "## § 本日のテーマ考察\n\n"
        "*AI と市場が同時に動いた一日*\n\n"
        "> [[AI]] と __市場__ の変化が同じ方向を向き、**投資判断**と産業戦略が同時に更新された。"
        "今日は単一カテゴリの速報ではなく、政策イベント、企業AI、半導体供給網、ゲーム供給計画が同時に読まれる日だった。"
        "短期の市場反応と中期の実装力を分けて読み、どの材料が継続して運用できるかを確認する必要がある。"
        "LPではこの段落が読者の入口になるため、単なる一文要約ではなく、複数カテゴリの関係を十分に説明する。\n\n"
        "### §02 AI — 資本と配布面が常用の条件になる\n\n"
        "[[AI]] の **配布面** と __導入条件__ が焦点になった。\n"
        "- 【事実・概要】：AI関連の発表が複数出た。\n"
        "- 【背景・要点】：配布と審査の条件が変わった。\n"
        "- 【影響・展望】：次は導入前審査を見る。\n",
        encoding="utf-8",
    )

    assert validate_summary_category_focus(summary, required_category_ids=["ai"]) == []


def test_find_latest_summary_uses_yyyy_mm_dd_name(tmp_path: Path) -> None:
    """最新判定は YYYY-MM-DD.md の名前だけで行い、余計な md は無視する。"""
    (tmp_path / "README.md").write_text("ignore", encoding="utf-8")
    old = tmp_path / "2026-06-07.md"
    new = tmp_path / "2026-06-08.md"
    old.write_text("old", encoding="utf-8")
    new.write_text("new", encoding="utf-8")

    assert find_latest_summary(tmp_path) == new


def test_cli_returns_nonzero_for_missing_reflection(tmp_path: Path, capsys) -> None:
    """runner から呼ぶ CLI は欠落時に exit 1 と stderr を返す。"""
    summary_dir = tmp_path / "Summary"
    summary_dir.mkdir()
    (summary_dir / "2026-06-08.md").write_text("# Summary\n", encoding="utf-8")

    rc = main(["--summary-dir", str(summary_dir), "--date", "2026-06-08"])

    captured = capsys.readouterr()
    assert rc == 1
    assert "ERROR:" in captured.err


def test_summary_headline_rejects_abstract_two_phrase_slogan(tmp_path: Path) -> None:
    summary = tmp_path / "2026-08-03.md"
    summary.write_text(
        "---\n"
        "date: 2026-08-03\n"
        "title: 'News Grasp #20260803 — 広がる入口、狭める境界'\n"
        "hero_headline: '広がる入口、狭める境界'\n"
        "---\n",
        encoding="utf-8",
    )

    errs = validate_summary_headline(summary)

    assert any("具体的なニュース見出し" in err for err in errs)


def test_summary_headline_accepts_concrete_subject_action_and_contiguous_hero(tmp_path: Path) -> None:
    summary = tmp_path / "2026-08-03.md"
    summary.write_text(
        "---\n"
        "date: 2026-08-03\n"
        "title: 'News Grasp #20260803 — 日米が円買い協調介入、ドル円は一時155円台前半へ'\n"
        "hero_headline: '日米が円買い協調介入、ドル円は一時155円台前半へ'\n"
        "---\n",
        encoding="utf-8",
    )

    assert validate_summary_headline(summary) == []
