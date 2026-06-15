#!/usr/bin/env python3
"""News-Grasp 出力品質ゲート (= 層 2: 最終出力の意味的品質再検証)。

設計の動機:
    既存 build 時 hard fail ガード (_require_blocks / require_live_urls /
    validate_ja_callout_coverage) は「層 1.5」までで、中間データの存在性・形式・
    URL 生存性は見るが、生成された最終 SVG/HTML の意味的品質 (線がノード円を貫通
    していないか、カテゴリトップで同テーマが連続採用されていないか等) を再検証
    しない。これが 2026-06-06 セッション内で同 class of bugs 2 件再発の構造的真因。

    本モジュールは「層 2」として、build 関数の最終 render 直後に必ず通すゲートを
    提供する ([[feedback_check_design_principles]] 1 段「失敗を表現できない構造に
    変える」+ 2 段「境界 1 箇所集約」)。

依存: stdlib のみ (math + re)。プロジェクト固有判定ロジック (例: 同テーマ判定式)
は呼出側 (generate_pages.py / render_deepdive.py) が `is_same_theme` 等として inject
する設計で、他プロジェクトへ ~/.codex/templates/output_quality.py のコピーだけで
横展開可能にする (Capstone / AI-Pulse / DriveSwipe / ITStr-StudyApp 等)。

公開 API:
    OutputQualityError(RuntimeError)
    check_relations_svg(svg, *, src) -> list[str]
    check_category_top_dedup(entries, *, kind, is_same_theme) -> list[str]
    assert_quality(checks) -> None
"""
from __future__ import annotations

import math
import re
from typing import Any, Callable, Iterable


class OutputQualityError(RuntimeError):
    """出力品質ゲートが違反を検出 → build を中止する loud failure 例外。

    既存の DeepDiveIncompleteError (relations 必須・edge 上限等) と並ぶ層 2 例外。
    build_deepdive_pages / build_category_pages は本例外を握りつぶさず再 raise し、
    docs/ への書き込みを物理的に阻止する。
    """


# ── relations SVG パース用 (render_deepdive.py のフォーマットと同期) ─────────
_NODE_CIRCLE_RE = re.compile(
    r'<circle cx="([-\d.]+)" cy="([-\d.]+)" r="([-\d.]+)" fill="#fff" stroke="#1A1A1A"')
_EDGE_LINE_RE = re.compile(
    r'<line x1="([-\d.]+)" y1="([-\d.]+)" x2="([-\d.]+)" y2="([-\d.]+)" '
    r'stroke="([^"]+)" stroke-width="([\d.]+)"')
# frenemy (協調的競合) の dual chip は高さ 44.0、通常 chip は 26.0
_CHIP_RECT_RE = re.compile(
    r'<g transform="translate\(([-\d.]+),([-\d.]+)\)"><rect width="([-\d.]+)" height="(26\.0|44\.0)"')


def _seg_point_dist(seg: tuple[float, float, float, float],
                    px: float, py: float) -> float:
    """線分 seg=(x1,y1,x2,y2) と点 (px,py) の最短距離。"""
    x1, y1, x2, y2 = seg
    dx, dy = x2 - x1, y2 - y1
    l2 = dx * dx + dy * dy
    if l2 < 1e-9:
        return math.hypot(px - x1, py - y1)
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / l2))
    return math.hypot(px - (x1 + dx * t), py - (y1 + dy * t))


def _rect_circle_hit(rect: tuple[float, float, float, float],
                     circ: tuple[float, float, float],
                     tol: float = 2.0) -> bool:
    """ラベル矩形↔ノード円の重なり判定。

    tol=2.0 (デフォルト) はゲート向けの実用閾値。1-2px の僅かな重なりはレンダリング
    上分からないため許容し、視覚的に「読めない」レベルの 3px 以上の重なりを検出する。
    既存契約テスト (test_relations_two_camps_split_left_right_and_no_overlap) は
    tol=0.5 で厳格に pin するが、本ゲートは「実機ビルドを止めるべきか」の判断なので
    閾値を緩めて誤検出 (= legacy fixture の軽微違反で build 全断) を防ぐ。
    """
    rx, ry, rw, rh = rect
    cx, cy, cr = circ
    qx = min(max(cx, rx), rx + rw)
    qy = min(max(cy, ry), ry + rh)
    return math.hypot(qx - cx, qy - cy) < cr - tol


def _rect_overlap(r1: tuple[float, float, float, float],
                  r2: tuple[float, float, float, float],
                  tol: float = 2.0) -> bool:
    """ラベル↔ラベルの重なり判定。tol=2.0 で実用閾値 (理由は _rect_circle_hit と同じ)。"""
    ax, ay, aw, ah = r1
    bx, by, bw, bh = r2
    ox = min(ax + aw, bx + bw) - max(ax, bx)
    oy = min(ay + ah, by + bh) - max(ay, by)
    return ox > tol and oy > tol


def check_relations_svg(svg: str, *, src: str, strict_objects: bool = False) -> list[str]:
    """関係図 SVG の幾何的品質を検査し、build を中止すべき重大違反のリストを返す。

    関係図の三原則:
      ・オブジェクトを被せない (ノード円、ラベルチップ、エッジ線の読解を邪魔しない)
      ・線はクロスが最小限になる配置を採る (避けられる交差は残さない)
      ・同じ役割のノードは、読み手が同列と分かるよう極力同じ行に揃える

    検出対象 (build 全断):
      ① 線がノード円 (端点以外) を貫通 (= 2026-06-06 BYD↔NVIDIA 線が Tesla を距離 0.0
         で直撃した事故クラス。図の読解不能で公開してはならない)
      ② strict_objects=True のとき、ノード円同士・ラベル矩形↔ノード円・ラベル矩形同士の
         重なりも重大違反として扱う。明示座標つき図など、編集済み配置にだけ使う。

    スコープ外 (本ゲートでは raise しない):
      ・ラベル矩形 ↔ ノード円の軽微重なり、ラベル矩形 ↔ ラベル矩形の軽微重なり
      ・線交差数の大域最適化、同役割ノードの y 行揃え
        — legacy fixture (deepdive_robotaxi.md / 2026-05-31-DeepDive.md 等) で
        1-2px 程度の許容範囲な重なりが運用上残っており、ゲートで raise すると
        既存の正常な build まで全断してしまう。これらは別経路 (契約テスト
        test_relations_two_camps_split_left_right_and_no_overlap や実日付 fixture で
        pin、または将来 warn 出力) で監視する。

    本ゲートの設計思想: 「重大な事故 (= pierce) を loud failure で公開阻止」に絞り、
    軽微な美観違反まで含めて build 全断する過剰検出を避ける (= ユーザー指摘
    『片手落ち』への構造解決を、誤検出 0 で実装するため)。

    src: 違反メッセージで識別する記事名 / カテゴリ等。
    """
    errors: list[str] = []
    circles = [(float(a), float(b), float(c))
               for a, b, c in _NODE_CIRCLE_RE.findall(svg)]
    segs = [(float(a), float(b), float(c), float(d))
            for a, b, c, d, _stroke, w in _EDGE_LINE_RE.findall(svg)
            if float(w) >= 2.0]
    rects = [(float(x), float(y), float(w), float(h))
             for x, y, w, h in _CHIP_RECT_RE.findall(svg)]

    # ① 線↔ノード貫通 (build 全断する重大違反)
    for si, seg in enumerate(segs):
        for ci, circ in enumerate(circles):
            cx, cy, cr = circ
            d = _seg_point_dist(seg, cx, cy)
            if d < cr - 1.0:
                errors.append(
                    f"[{src}] エッジ線 #{si} がノード円 #{ci} "
                    f"({cx:.0f},{cy:.0f},r={cr:.0f}) を距離 {d:.1f} で貫通"
                )

    if strict_objects:
        for i, c1 in enumerate(circles):
            for j, c2 in enumerate(circles[i + 1:], i + 1):
                dist = math.hypot(c1[0] - c2[0], c1[1] - c2[1])
                if dist < c1[2] + c2[2] + 2.0:
                    errors.append(
                        f"[{src}] ノード円 #{i} と #{j} が近接/重なり "
                        f"(distance={dist:.1f}, required>={c1[2] + c2[2] + 2.0:.1f})"
                    )
        for ri, rect in enumerate(rects):
            for ci, circ in enumerate(circles):
                if _rect_circle_hit(rect, circ):
                    errors.append(
                        f"[{src}] ラベル矩形 #{ri} がノード円 #{ci} に重なっています"
                    )
        for i, r1 in enumerate(rects):
            for j, r2 in enumerate(rects[i + 1:], i + 1):
                if _rect_overlap(r1, r2):
                    errors.append(
                        f"[{src}] ラベル矩形 #{i} と #{j} が重なっています"
                    )

    return errors


def check_category_top_dedup(
    entries: list[dict[str, Any]],
    *,
    kind: str,
    is_same_theme: Callable[[dict[str, Any], dict[str, Any]], bool],
) -> list[str]:
    """カテゴリトップ表示 (grid_9 / past_7) の連続 2 entry が同テーマ判定で並ばないこと。

    is_same_theme は呼出側が inject する 2 引数関数 (例: generate_pages.py の
    `_is_same_theme_for_display` ベース)。本モジュールは判定ロジックを持たないことで、
    他プロジェクトでも別の同テーマ判定 (記事 entity 比較等) を inject して再利用できる。
    """
    errors: list[str] = []
    for i in range(len(entries) - 1):
        if is_same_theme(entries[i], entries[i + 1]):
            t1 = entries[i].get("top_title") or entries[i].get("title", "")
            t2 = entries[i + 1].get("top_title") or entries[i + 1].get("title", "")
            d1 = entries[i].get("date", "?")
            d2 = entries[i + 1].get("date", "?")
            errors.append(
                f"[{kind}] 連続 entry #{i}/#{i+1} が同テーマで並んでいる: "
                f"{d1} {t1[:50]} / {d2} {t2[:50]}"
            )
    return errors


def assert_quality(checks: Iterable[tuple[str, list[str]]]) -> None:
    """checks=[(label, errors)] を集約し、errors 1 件でも OutputQualityError を raise。

    既存 _require_blocks / require_live_urls と並ぶ「層 2 loud failure ゲート」。
    build_* 関数の最終 render 直後で必ず通し、違反があれば docs/ への書き込みを
    物理的に阻止する設計 ([[feedback_check_design_principles]] 1 段)。
    """
    all_errors: list[str] = []
    for _label, errs in checks:
        all_errors.extend(errs)
    if all_errors:
        head = "\n".join(all_errors[:20])
        more = f"\n... and {len(all_errors) - 20} more" if len(all_errors) > 20 else ""
        raise OutputQualityError(
            f"出力品質ゲート失敗 ({len(all_errors)} 件):\n{head}{more}"
        )
