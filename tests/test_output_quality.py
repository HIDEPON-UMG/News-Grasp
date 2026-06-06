#!/usr/bin/env python3
"""tools/output_quality.py (層 2 出力品質ゲート) の契約テスト。

なぜ重要か:
    2026-06-06 セッションで同 class of bugs 2 件 (関係図線がノード貫通 / カテゴリ
    トップ同テーマ連続) が pytest 251 件 PASS のまま公開された構造的真因
    = 「層 2 = 最終出力の意味的品質再検証」の欠落。本モジュールはその検出関数を
    集約しており、各 check_* が「違反データを必ず errors として返す」「errors 1 件で
    assert_quality が必ず raise する」ことを契約として locked-in する。

    既存の test_relations_same_band_peer_edge_no_pierce / test_category_top_no_consecutive_same_theme
    は「正常データで build が違反を生まない」ことを pin するが、本ファイルは
    「ゲート関数自体が違反を見逃さない」ことを pin する。両者は別目的なので併存させる。

実行:
    pytest tests/test_output_quality.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.output_quality import (  # noqa: E402
    OutputQualityError,
    assert_quality,
    check_category_top_dedup,
    check_relations_svg,
)


# ── check_relations_svg: 線がノード貫通 / ラベル衝突を検出 ─────────────────────

def _svg_pierce_fixture() -> str:
    """両端 (250,114) と (830,114) の線が中央 (540,114, r=56) ノードを貫通する SVG。

    実機 render_deepdive.py の出力フォーマットに準拠 (gap=7 で a.r+gap, b.r+gap 開始)。
    """
    return (
        '<svg viewBox="0 0 1080 695">'
        # BYD↔NVIDIA 水平線 — 中央 Tesla を距離 0 で貫通する状況
        '<line x1="319.0" y1="114.0" x2="767.0" y2="114.0" '
        'stroke="#8E2A19" stroke-width="2" opacity="0.85"/>'
        # ノード円 3 つ (中央 Tesla が貫通対象)
        '<circle cx="250.0" cy="114.0" r="62.0" fill="#fff" stroke="#1A1A1A" stroke-width="2.5"/>'
        '<circle cx="540.0" cy="114.0" r="56.0" fill="#fff" stroke="#1A1A1A" stroke-width="2.5"/>'
        '<circle cx="830.0" cy="114.0" r="56.0" fill="#fff" stroke="#1A1A1A" stroke-width="2.5"/>'
        '</svg>'
    )


def _svg_clean_fixture() -> str:
    """BYD を中央 hub に置く正常配置 (peer-aware order 適用後)。線がノード貫通せず。"""
    return (
        '<svg viewBox="0 0 1080 695">'
        # Tesla(left) ↔ BYD(center) 線 (Tesla→BYD の左半分のみ)
        '<line x1="306.0" y1="114.0" x2="478.0" y2="114.0" '
        'stroke="#8E2A19" stroke-width="2" opacity="0.85"/>'
        # BYD(center) → NVIDIA(right) 線
        '<line x1="602.0" y1="114.0" x2="774.0" y2="114.0" '
        'stroke="#8E2A19" stroke-width="2" opacity="0.85"/>'
        '<circle cx="250.0" cy="114.0" r="56.0" fill="#fff" stroke="#1A1A1A" stroke-width="2.5"/>'
        '<circle cx="540.0" cy="114.0" r="62.0" fill="#fff" stroke="#1A1A1A" stroke-width="2.5"/>'
        '<circle cx="830.0" cy="114.0" r="56.0" fill="#fff" stroke="#1A1A1A" stroke-width="2.5"/>'
        '</svg>'
    )


def test_check_relations_svg_detects_pierce() -> None:
    """同段中央ノードを貫通する SVG fixture が errors として返ること。

    2026-06-06 BYD↔NVIDIA 線が Tesla を距離 0 で貫通した実例を fixture 化。
    本ゲートが build 時に走れば事故そのものが公開まで届かなかった構造を locked-in する。
    """
    errors = check_relations_svg(_svg_pierce_fixture(), src="test")
    assert errors, "貫通している SVG fixture で errors が空 — ゲートが機能していない"
    assert any("貫通" in e for e in errors), f"errors に貫通検出が無い: {errors}"


def test_check_relations_svg_passes_clean_layout() -> None:
    """正常配置 (peer-aware order 適用後) では errors が空であること (誤検出回避)。"""
    errors = check_relations_svg(_svg_clean_fixture(), src="test")
    assert not errors, f"正常 SVG fixture で errors が出た (誤検出): {errors}"


# ── check_category_top_dedup: 連続同テーマ検出 ────────────────────────────────

def test_check_category_top_dedup_detects_consecutive_same_theme() -> None:
    """同テーマ続報 2 件が連続する entries が errors として返ること。

    is_same_theme を inject する設計を契約: 呼出側 (generate_pages.py) が
    `_is_same_theme_for_display` を渡す形を本テストで pin。
    """
    entries = [
        {"date": "2026-06-05", "top_title": "Microsoft AI モデル発表"},
        {"date": "2026-06-04", "top_title": "Microsoft 別 AI モデル発表"},  # 同テーマ続報
        {"date": "2026-06-03", "top_title": "Google Gemma 別件"},
    ]
    # 「先頭単語が同じなら same theme」とみなす簡易判定 (本テスト用)
    def _same(a: dict, b: dict) -> bool:
        return a["top_title"].split()[0] == b["top_title"].split()[0]

    errors = check_category_top_dedup(entries, kind="ai/grid_9", is_same_theme=_same)
    assert errors, "連続同テーマ entries で errors が空 — ゲートが機能していない"
    assert any("Microsoft" in e for e in errors), f"errors に Microsoft 検出が無い: {errors}"


def test_check_category_top_dedup_passes_distinct_themes() -> None:
    """別テーマ entries では errors が空 (誤検出回避)。"""
    entries = [
        {"date": "2026-06-05", "top_title": "Microsoft AI モデル発表"},
        {"date": "2026-06-04", "top_title": "Google Gemma 4 12B"},
        {"date": "2026-06-03", "top_title": "Anthropic IPO 申請"},
    ]
    def _same(a: dict, b: dict) -> bool:
        return a["top_title"].split()[0] == b["top_title"].split()[0]

    errors = check_category_top_dedup(entries, kind="ai/grid_9", is_same_theme=_same)
    assert not errors, f"別テーマ entries で errors が出た (誤検出): {errors}"


# ── assert_quality: errors 1 件でも raise ─────────────────────────────────────

def test_assert_quality_raises_on_any_errors() -> None:
    """errors が 1 件でもあれば OutputQualityError を raise すること。

    layer 2 の核となる契約 — build_deepdive_pages / build_category_pages 内で
    本関数を呼ぶことで、違反があれば docs/ への書き込みを物理的に阻止する。
    """
    with pytest.raises(OutputQualityError) as exc:
        assert_quality([("relations_svg", ["dummy violation 1"])])
    assert "1 件" in str(exc.value)
    assert "dummy violation 1" in str(exc.value)


def test_assert_quality_passes_on_no_errors() -> None:
    """errors が空なら何も raise しないこと (正常時)。"""
    assert_quality([("relations_svg", []), ("category_top", [])])
    # raise されなければ test PASS
