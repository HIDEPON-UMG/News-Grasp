#!/usr/bin/env python3
"""tools.validate_public_home の契約テスト。"""
from __future__ import annotations

from pathlib import Path

from tools.validate_public_home import main, validate_public_home


LONG_LEAD = (
    "政策イベントと企業AI、半導体供給網、ゲーム供給計画が同じ日に並ぶとき、"
    "公開ページの入口は単なる短文要約では足りない。読者が今日の全体像を理解できるよう、"
    "市場、産業、運用、供給の関係を十分な長さで説明し、各カテゴリへ自然に進める必要がある。"
    "この段落はLPの最初に読まれるため、退化すると日次号全体の読み筋が失われる。"
    "そのため、公開HTMLそのものを検査して、テンプレートや入力データのどちらが原因でも"
    "push前に止められる状態を維持する。"
)


def _write_public_html(root: Path, *, top_img: bool = True, color_panel: bool = False, home_lead: str = LONG_LEAD, summary_lead: str = LONG_LEAD) -> None:
    docs = root / "docs"
    summary = docs / "2026-06-09" / "summary"
    summary.mkdir(parents=True)
    top_media = (
        '<img src="https://hidepon-umg.github.io/News-Grasp/assets/og/it.jpg" alt="">'
        if top_img
        else ""
    )
    fallback_style = "width: 100%; height: 100%;" if color_panel else ""
    (docs / "index.html").write_text(
        f"""
<!doctype html>
<section class="home-hero">
  <p class="home-hero__lead">{home_lead}</p>
</section>
<section class="home-featured">
  <span class="home-featured__badge">TOP STORY</span>
  <div class="home-featured__media" style="{fallback_style}">{top_media}</div>
</section>
""",
        encoding="utf-8",
    )
    (summary / "index.html").write_text(
        f"""
<!doctype html>
<section class="summary-hero">
  <div class="summary-hero__lead">{summary_lead}</div>
</section>
""",
        encoding="utf-8",
    )


def test_validate_public_home_accepts_current_shape(tmp_path: Path) -> None:
    """TOP STORY img と十分な lead があれば公開HTML gateを通す。"""
    _write_public_html(tmp_path)

    assert validate_public_home(tmp_path / "docs", "2026-06-09") == []


def test_validate_public_home_rejects_top_story_without_img(tmp_path: Path) -> None:
    """TOP STORY が色面だけになる回帰は publish 前に落とす。"""
    _write_public_html(tmp_path, top_img=False)

    errs = validate_public_home(tmp_path / "docs", "2026-06-09")

    assert any("TOP STORY block に <img" in e for e in errs)


def test_validate_public_home_rejects_color_panel_fallback(tmp_path: Path) -> None:
    """旧fallbackの `width: 100%; height: 100%;` は画像退化として落とす。"""
    _write_public_html(tmp_path, color_panel=True)

    errs = validate_public_home(tmp_path / "docs", "2026-06-09")

    assert any("色面fallback" in e for e in errs)


def test_validate_public_home_rejects_short_home_lead(tmp_path: Path) -> None:
    """LPのhome-hero__leadが短文退化したら落とす。"""
    _write_public_html(tmp_path, home_lead="短いlead。")

    errs = validate_public_home(tmp_path / "docs", "2026-06-09")

    assert any("home-hero__lead が短すぎます" in e for e in errs)


def test_validate_public_home_rejects_short_summary_lead(tmp_path: Path) -> None:
    """Summary詳細ページ側のhero lead短文退化も同じ公開HTML境界で落とす。"""
    _write_public_html(tmp_path, summary_lead="短いlead。")

    errs = validate_public_home(tmp_path / "docs", "2026-06-09")

    assert any("summary-hero__lead が短すぎます" in e for e in errs)


def test_cli_returns_nonzero_for_public_html_regression(tmp_path: Path, capsys) -> None:
    """runnerから呼ぶCLIは公開HTML退化時にexit 1とstderrを返す。"""
    _write_public_html(tmp_path, top_img=False)

    rc = main(["--docs-dir", str(tmp_path / "docs"), "--date", "2026-06-09"])

    captured = capsys.readouterr()
    assert rc == 1
    assert "ERROR:" in captured.err
