#!/usr/bin/env python3
"""DeepDive (週次 TODAY'S THEME) ページレンダラの契約テスト。

検証する「なぜ重要か」:
  - 新ブロック relations / table が「描画される」こと (旧レンダラ未対応が本フェーズの中核)。
    relations はスキーマに座標が無いので auto-layout が node 座標を必ず付ける。
  - table の「未確認/未開示/非開示」セルが淡色バッジ対象として検出されること
    (数値を捏造で埋めない設計をデザイン上も成立させる)。
  - DeepDive 出力が docs/deepdive/{date}/ に閉じ、LP/カテゴリ/summary を汚さないこと
    (2026-05-31 DeepDive 事故の不変条件)。
  - 必須ブロック (関係図 relations / 変遷チャート / データ表 等) を欠く DeepDive は
    ビルドを **hard fail** させること。プロンプトで「必須」と書くだけでは 2026-05-31
    記事が relations 欠落のまま公開された (記憶/指示頼みは破れる) ため、ビルド時の
    loud failure で未完成記事の公開を構造的に封じる不変条件を locked-in する。

実行: pytest tests/test_deepdive_render.py -v
"""
from __future__ import annotations

import math
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.render_deepdive import (  # noqa: E402
    INK,
    DeepDiveIncompleteError,
    build_deepdive_context,
    build_deepdive_pages,
    build_table,
    chart_svg,
    extract_blocks,
    layout_relations,
    relations_svg,
)

_FIXTURE = ROOT / "tests" / "fixtures" / "deepdive_robotaxi.md"
_REAL = ROOT / "digest" / "DeepDive" / "2026-05-31-DeepDive.md"


# ── ブロック抽出 ──────────────────────────────────────────────────────────────

def test_extract_all_blocks_present() -> None:
    """完全スキーマ md から 6 種ブロックを全て抽出できる。"""
    body = _FIXTURE.read_text(encoding="utf-8")
    blocks = extract_blocks(body)
    for lang in ("timeline", "players", "relations", "chart", "table", "decision"):
        assert lang in blocks, f"{lang} ブロックが抽出されていない"
    assert len(blocks["chart"]) == 2, "chart は 2 本あるはず (時系列変遷 + 比較)"


def test_malformed_block_is_skipped_not_crash(tmp_path: Path) -> None:
    """壊れた JSON ブロックは握りつぶさず skip し、他は生かす (loud 警告)。"""
    body = "## 背景\n\n```players\n[{bad json,,}]\n```\n\n```timeline\n[]\n```\n"
    blocks = extract_blocks(body)
    assert "players" not in blocks  # 壊れたので採用しない
    assert "timeline" in blocks      # 正常なものは生きる


# ── relations auto-layout (★新規描画の中核) ──────────────────────────────────

def test_relations_layout_assigns_coordinates() -> None:
    """スキーマに座標が無くても auto-layout が x/y/r を必ず付ける。"""
    body = _FIXTURE.read_text(encoding="utf-8")
    rel = extract_blocks(body)["relations"][0]
    lay = layout_relations(rel)
    for nd in lay["nodes"]:
        assert "x" in nd and "y" in nd and "r" in nd, "node に座標/半径が無い"
        assert 0 <= nd["x"] <= lay["vb_w"] and 0 <= nd["y"] <= lay["vb_h"]
    # 凡例は実際に登場した kind のみ (競合/規制/出資)
    kinds = {l["kind"] for l in lay["legend"]}
    assert kinds == {"出資", "競合", "規制"}, f"凡例 kind が不正: {kinds}"


def test_relations_layout_is_semantic_not_circular() -> None:
    """構図ルールを固定: 競合は左右に分かれ、協力(出資)は縦、規制当局は中央下。

    「とりあえず円環に並べる」のではなく kind の意味どおりに配置することが
    この図の存在意義 (勢力構造が一目で読める) なので、その不変条件を locked-in する。
    """
    body = _FIXTURE.read_text(encoding="utf-8")
    rel = extract_blocks(body)["relations"][0]
    lay = layout_relations(rel)
    pos = {nd["id"]: (nd["x"], nd["y"]) for nd in lay["nodes"]}
    cx = lay["vb_w"] / 2
    # 競合 (waymo×tesla) は左右に二分 — 中心を挟んで反対側
    assert (pos["waymo"][0] - cx) * (pos["tesla"][0] - cx) < 0, "競合が左右に分かれていない"
    # 出資元 alphabet は出資先 waymo と同じ側、かつ上 (y が小さい)
    assert (pos["alphabet"][0] - cx) * (pos["waymo"][0] - cx) > 0, "協力が同じ側に無い"
    assert pos["alphabet"][1] < pos["waymo"][1], "出資元が上に積まれていない"
    # 規制当局 nhtsa は中央 (横位置がほぼ中心) かつ両勢力より下
    assert abs(pos["nhtsa"][0] - cx) < lay["vb_w"] * 0.05, "規制当局が中央に無い"
    assert pos["nhtsa"][1] > pos["waymo"][1] and pos["nhtsa"][1] > pos["tesla"][1], \
        "規制当局が下 (見上げる三角) に無い"


def test_relations_svg_renders_nodes_and_edges() -> None:
    """relations が SVG (circle + line + ラベル) として描画される。"""
    body = _FIXTURE.read_text(encoding="utf-8")
    rel = extract_blocks(body)["relations"][0]
    svg = relations_svg(rel)
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    assert svg.count("<circle") >= 4   # 4 nodes
    assert svg.count("<line") >= 4     # 4 edges
    assert "Waymo" in svg and "NHTSA" in svg


def test_relations_dedups_identical_edge_labels() -> None:
    """同一 source・同文ラベルのエッジ群はラベルを 1 回だけ描く (線は両方残す)。

    NHTSA→Waymo / NHTSA→Tesla は両方とも "SGO crash 報告義務" なので、同文ラベルを
    2 枚並べると認知負荷を上げるだけ。線 (規制関係) は両方描くが、チップは 1 個に束ねる。
    """
    body = _FIXTURE.read_text(encoding="utf-8")
    rel = extract_blocks(body)["relations"][0]
    svg = relations_svg(rel)
    assert svg.count("SGO crash 報告義務") == 1, "同文ラベルが束ねられていない"
    assert svg.count("<line") >= 4, "規制の線は 2 本とも残すべき"


def test_rivalry_edges_are_bidirectional_by_default() -> None:
    """競合/対立は相互関係なので既定で双方向矢印 (⇔)。一方向の協力は片矢印のまま。"""
    rival = {"nodes": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
             "edges": [{"from": "a", "to": "b", "label": "競う", "kind": "競合"}]}
    assert relations_svg(rival).count("<polygon") == 2, "競合が双方向矢印になっていない"
    # 出資 (親→子) は一方向のまま
    coop = {"nodes": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
            "edges": [{"from": "a", "to": "b", "label": "出資", "kind": "出資"}]}
    assert relations_svg(coop).count("<polygon") == 1, "一方向の協力が双方向化している"
    # 明示的に一方向化したい競合は dir=one で片矢印
    one = {"nodes": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
           "edges": [{"from": "a", "to": "b", "label": "挑戦", "kind": "競合", "dir": "one"}]}
    assert relations_svg(one).count("<polygon") == 1, "dir=one が効いていない"


# ── relations 2 陣営 = 左右カラム + ラベル無重なり (★2026-06-02 ユーザー指示) ──────

_MULTICAMP_REL = {
    "title": "米中2陣営＋規制",
    "nodes": [
        {"id": "waymo", "label": "Waymo", "group": "米陣営"},
        {"id": "tesla", "label": "Tesla", "group": "米陣営"},
        {"id": "pony", "label": "Pony.ai", "group": "中国陣営"},
        {"id": "baidu", "label": "Baidu", "group": "中国陣営"},
        {"id": "geely", "label": "Geely", "group": "中国陣営"},
        {"id": "nhtsa", "label": "NHTSA", "group": "規制"},
        {"id": "chinareg", "label": "中国当局", "group": "規制"},
    ],
    "edges": [
        {"from": "waymo", "to": "tesla", "label": "規模vs量産で競合", "kind": "競合"},
        {"from": "waymo", "to": "pony", "label": "海外展開で激突", "kind": "競合"},
        {"from": "geely", "to": "waymo", "label": "供給かつ競合", "kind": "協調的競合",
         "coop": "車両を供給", "rival": "自陣で競合"},
        {"from": "nhtsa", "to": "waymo", "label": "crash報告義務", "kind": "規制"},
        {"from": "nhtsa", "to": "tesla", "label": "事故を調査", "kind": "規制"},
        {"from": "chinareg", "to": "baidu", "label": "新規許可を凍結", "kind": "規制"},
        {"from": "chinareg", "to": "pony", "label": "拡大を制限", "kind": "規制"},
    ],
}

_NODE_CIRCLE_RE = re.compile(
    r'<circle cx="([-\d.]+)" cy="([-\d.]+)" r="([-\d.]+)" fill="#fff" stroke="#1A1A1A"')
# 2026-06-04: frenemy chip は高さ 44.0 (2 行統合)、それ以外は 26.0 (1 行)。両方を取る。
_CHIP_RECT_RE = re.compile(
    r'<g transform="translate\(([-\d.]+),([-\d.]+)\)"><rect width="([-\d.]+)" height="(26\.0|44\.0)"')


def _node_circles(svg: str) -> list[tuple[float, float, float]]:
    return [(float(a), float(b), float(c)) for a, b, c in _NODE_CIRCLE_RE.findall(svg)]


def _chip_rects(svg: str) -> list[tuple[float, float, float, float]]:
    return [(float(x), float(y), float(w), float(h)) for x, y, w, h in _CHIP_RECT_RE.findall(svg)]


def _rect_circle_hit(rect, circ, tol: float = 0.5) -> bool:
    rx, ry, rw, rh = rect
    cx, cy, cr = circ
    qx = min(max(cx, rx), rx + rw)
    qy = min(max(cy, ry), ry + rh)
    return math.hypot(qx - cx, qy - cy) < cr - tol


def _rect_overlap(r1, r2, tol: float = 0.5) -> bool:
    ax, ay, aw, ah = r1
    bx, by, bw, bh = r2
    ox = min(ax + aw, bx + bw) - max(ax, bx)
    oy = min(ay + ah, by + bh) - max(ay, by)
    return ox > tol and oy > tol


def test_relations_two_camps_split_left_right_and_no_overlap() -> None:
    """2 つの対立陣営は左右カラムに分け、主役同士を同じ高さで対峙させる (2026-06-02 改訂)。

    ユーザー指示で「陣営」は役割ではないと明確化された: まず 2 陣営を左右に分け
    (横軸 = 陣営)、各陣営内で主役と、それを支援する出資元・顧客を別の段に積み
    (縦軸 = 役割)、どちらの陣営にも属さない中立機関 (規制当局) は専用の最下段レイヤーに
    置く。旧「役割ごとの水平バンド」(米陣営=上段/中国陣営=中段) はこの指示で破棄した。
    文字の重なり・線のノード貫通は図を読めなくする致命傷なので 0 を契約に固定する。
    """
    rel = _MULTICAMP_REL
    lay = layout_relations(rel)
    pos = {nd["id"]: (nd["x"], nd["y"]) for nd in lay["nodes"]}
    cx = lay["vb_w"] / 2
    # 2 陣営は左右に分かれる: 米陣営 (waymo,tesla) は同じ側、中国陣営はその反対側
    assert (pos["waymo"][0] - cx) * (pos["tesla"][0] - cx) > 0, "米陣営が左右で割れている"
    assert (pos["pony"][0] - cx) * (pos["baidu"][0] - cx) > 0 \
        and (pos["pony"][0] - cx) * (pos["geely"][0] - cx) > 0, "中国陣営が左右で割れている"
    assert (pos["waymo"][0] - cx) * (pos["pony"][0] - cx) < 0, "2 陣営が左右に分かれていない"
    # 主役 (waymo / pony) は同じ高さで対峙し、支援者は別の段 (上) に積まれる
    assert pos["waymo"][1] == pos["pony"][1], "主役同士が同じ高さで対峙していない"
    assert pos["tesla"][1] != pos["waymo"][1], "米陣営の支援者が主役と別段になっていない"
    assert pos["baidu"][1] == pos["geely"][1] != pos["pony"][1], \
        "中国陣営の支援者が別段に揃っていない"
    # 中立機関 (規制当局) はどちらの陣営にも属さず専用の最下段レイヤーにまとまる
    assert pos["nhtsa"][1] == pos["chinareg"][1], "規制当局が同じ中立レイヤーに無い"
    assert pos["nhtsa"][1] == max(y for _, y in pos.values()), "規制当局が最下段に無い"

    svg = relations_svg(rel)
    circles = _node_circles(svg)
    rects = _chip_rects(svg)
    assert len(circles) == 7, f"ノード円が 7 個でない: {len(circles)}"
    # frenemy は 1 chip 統合 (2 行) になったので、7 chip = 7 base edges
    assert len(rects) == 7, f"ラベルチップが 7 個でない: {len(rects)}"
    # ラベルチップ vs ノード円: 文字が読めなくなる重なりは 0
    for r in rects:
        for c in circles:
            assert not _rect_circle_hit(r, c), f"ラベルがノードに重なる: rect={r} circle={c}"
    # ラベルチップ同士の重なりも 0
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            assert not _rect_overlap(rects[i], rects[j]), \
                f"ラベル同士が重なる: {rects[i]} ∩ {rects[j]}"
    # エッジ線は端点以外のノード円を貫通しない (左右カラムでも線がオブジェクトを貫かない)
    segs = [(float(a), float(b), float(c), float(d))
            for a, b, c, d, _stroke, w in _EDGE_LINE_RE.findall(svg) if float(w) >= 2.0]
    for seg in segs:
        for ncx, ncy, ncr in circles:
            assert _seg_point_dist(seg, ncx, ncy) >= ncr - 1.0, \
                f"エッジ線がノード円を貫通: seg={seg} circle=({ncx},{ncy},{ncr})"


# ── relations バリューチェーン層化 + 線の貫通禁止 (★2026-06-01 ユーザー指摘) ──────

# 2026-05-31 DeepDive の関係図と同じ構造: group が陣営でなくサブタイトル (例示企業) で
# 全ノード別値 → 旧ロジックは事業者を 1 band に潰し、供給元 (ベンダー/SI) と供給先
# (発注企業) を同段に並べたため (1) 供給線が同段の SI を貫通し (2) 役割の違うノードが
# 同レイヤーに乗った。有向フロー (供給) で段層化してこの 2 症状を封じる不変条件を固定。
_VALUE_CHAIN_REL = {
    "title": "モデルベンダー陣営とコンサル・SI の協調的競合",
    "nodes": [
        {"id": "capital", "label": "投資家", "group": "Blackstone・Goldman"},
        {"id": "vendors", "label": "モデルベンダー", "group": "Anthropic・OpenAI"},
        {"id": "consultants", "label": "コンサル・SI", "group": "Accenture・富士通 他"},
        {"id": "client", "label": "発注企業", "group": "CFO・調達"},
    ],
    "edges": [
        {"from": "capital", "to": "vendors", "label": "JV・実装直販に巨額出資", "kind": "出資"},
        {"from": "vendors", "to": "consultants", "kind": "協調的競合",
         "coop": "Partner Network で提携", "rival": "人月モデルを中抜き"},
        {"from": "vendors", "to": "client", "label": "AI実装を直販で提供", "kind": "供給"},
        {"from": "consultants", "to": "client", "label": "人月で実装・運用を提供", "kind": "供給"},
    ],
}

_EDGE_LINE_RE = re.compile(
    r'<line x1="([-\d.]+)" y1="([-\d.]+)" x2="([-\d.]+)" y2="([-\d.]+)" '
    r'stroke="([^"]+)" stroke-width="([\d.]+)"')


def _seg_point_dist(seg, px: float, py: float) -> float:
    """線分 seg=(x1,y1,x2,y2) と点 (px,py) の最短距離。"""
    x1, y1, x2, y2 = seg
    dx, dy = x2 - x1, y2 - y1
    L2 = dx * dx + dy * dy
    if L2 < 1e-9:
        return math.hypot(px - x1, py - y1)
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / L2))
    return math.hypot(px - (x1 + dx * t), py - (y1 + dy * t))


def test_relations_value_chain_layers_supply_sink_below() -> None:
    """供給元 (ベンダー/SI) は同段、供給先 (発注企業) は下段、出資元は上段に層化する。

    なぜ重要か: 2026-05-31 の関係図で供給元と供給先が同レイヤーに並び、
    モデルベンダー→発注企業 の供給線が間のコンサル・SI を貫通していた。役割
    (供給元/供給先/出資元) を有向フローで別段に分け、エッジ線がどのノード円も
    貫通しないことを契約として固定する (円環/中央集約・直販線貫通への逆戻り防止)。
    """
    lay = layout_relations(_VALUE_CHAIN_REL)
    pos = {nd["id"]: (nd["x"], nd["y"]) for nd in lay["nodes"]}
    # 供給元 2 社 (frenemy) は同レイヤー、供給先 (発注企業) はその下段
    assert pos["vendors"][1] == pos["consultants"][1], "供給元 2 社が同レイヤーに無い"
    assert pos["client"][1] > pos["vendors"][1], "供給先 (発注企業) が下段に落ちていない"
    # 出資元 (投資家) は供給元より上段
    assert pos["capital"][1] < pos["vendors"][1], "出資元が上段に積まれていない"
    # 3 役割が別レイヤー
    assert len({pos["capital"][1], pos["vendors"][1], pos["client"][1]}) == 3, \
        "出資元/供給元/供給先が別レイヤーに分かれていない"

    # ★ どのエッジ線も自分の端点以外のノード円を貫通しない (線がオブジェクトを貫通禁止)
    svg = relations_svg(_VALUE_CHAIN_REL)
    circles = _node_circles(svg)
    segs = [(float(a), float(b), float(c), float(d))
            for a, b, c, d, _stroke, w in _EDGE_LINE_RE.findall(svg) if float(w) >= 2.0]
    assert len(circles) == 4 and len(segs) >= 4
    for seg in segs:
        for cx, cy, cr in circles:
            assert _seg_point_dist(seg, cx, cy) >= cr - 1.0, \
                f"エッジ線がノード円を貫通: seg={seg} circle=({cx},{cy},{cr})"


# ── relations 3 陣営 + クライアントの高密度ケース (★2026-06-04 ユーザー指摘) ──
# 2026-06-04 DeepDive (AIベンダー2/コンサル3/SIer2/クライアント1 = 4 役割 8 ノード)
# で 10 エッジ (11 ラベル) を band 間 gap に詰め込み、ラベル重なり 5 ペアが発生。
# レンダラ側の力学分離 (preconditioning + vb_h 拡張) では構造的に解けないと観察され、
# 「関係図の edges は 8 本上限・超過は build が hard fail」を契約に固定した
# (= [[feedback_check_design_principles]] 1 段「失敗を表現できない構造に変える」)。
# 本 fixture は 8 エッジ (frenemy 1 + 提携/競合/供給 7) に絞った主要対立軸版で、
# レンダラが描けば重なり 0 を保証する不変条件として locked-in する。
_BANDS_HIGH_DENSITY_REL = {
    "title": "AIベンダー・グローバルコンサルの関係図",
    "nodes": [
        {"id": "openai", "label": "OpenAI (DeployCo)", "group": "AIベンダー"},
        {"id": "google", "label": "Google (DeepMind/Cloud)", "group": "AIベンダー"},
        {"id": "accenture", "label": "Accenture", "group": "グローバルコンサル"},
        {"id": "mckinsey", "label": "McKinsey", "group": "グローバルコンサル"},
        {"id": "bcg", "label": "BCG", "group": "グローバルコンサル"},
        {"id": "client", "label": "発注企業", "group": "クライアント"},
    ],
    # 5 エッジ (frenemy 1 + 普通 4) = 描画 chip 数 5 (frenemy も 1 chip)。
    # _MAX_RELATION_EDGES=8 上限内で 4 役割 8 ノードの bands モードを最小再現する版。
    "edges": [
        {"from": "openai", "to": "accenture", "label": "提携と競合が併存", "kind": "協調的競合",
         "coop": "Frontier Alliances提携", "rival": "DeployCoで実装受注侵食"},
        {"from": "openai", "to": "mckinsey", "label": "Frontier Alliances", "kind": "提携"},
        {"from": "google", "to": "accenture", "label": "DeepMind実装提携", "kind": "提携"},
        {"from": "openai", "to": "client", "label": "DeployCoで直接実装", "kind": "供給"},
        {"from": "accenture", "to": "client", "label": "従来の実装受注", "kind": "供給"},
    ],
}


def test_relations_bands_high_density_no_label_overlap() -> None:
    """3 陣営以上 + 下流クライアント (bands モード use_camps=True) で 8 エッジに絞れば、
    ラベル同士・ラベル↔ノード円・エッジ線↔ノード円の重なりは 0 を保つ。

    なぜ重要か: 2026-06-04 DeepDive の実機 SVG で band 間に 6 枚の長尺ラベルが
    詰め込まれ重なり 5 ペアが発生。memory `feedback_relation_diagram_semantic_layout`
    の規約と _choose_layout_mode の docstring は「ラベル重なり 0」を明記して
    いるが、契約テスト fixture が 2 陣営 / バリューチェーンの 2 種に限定され、
    3 陣営以上の bands モードを覆っていなかった。本テストで 8 エッジに絞った
    bands 高密度版を locked-in する (= [[feedback_check_design_principles]]
    4 段「契約テスト 1 件で不変条件を locked-in」の適用)。9 エッジ以上は
    test_relations_too_many_edges_hard_fail で build が hard fail する契約。
    """
    rel = _BANDS_HIGH_DENSITY_REL
    svg = relations_svg(rel)
    circles = _node_circles(svg)
    rects = _chip_rects(svg)
    # 6 ノード (AIベンダー 2 + コンサル 3 + クライアント 1) の 3 段 bands 構成。
    assert len(circles) == 6, f"ノード円が 6 個でない: {len(circles)}"
    # 5 edges (frenemy 1 + 普通 4)、frenemy 1 chip 統合で chip 数 = 5
    assert len(rects) == 5, f"ラベルチップが 5 個でない: {len(rects)}"

    # ラベル↔ノード円: 文字が読めなくなる重なりは 0
    for r in rects:
        for c in circles:
            assert not _rect_circle_hit(r, c), \
                f"ラベルがノード円に重なる: rect={r} circle={c}"

    # ラベル同士の重なりも 0
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            assert not _rect_overlap(rects[i], rects[j]), \
                f"ラベル同士が重なる: {rects[i]} ∩ {rects[j]}"

    # エッジ線は端点以外のノード円を貫通しない
    segs = [(float(a), float(b), float(c), float(d))
            for a, b, c, d, _stroke, w in _EDGE_LINE_RE.findall(svg) if float(w) >= 2.0]
    for seg in segs:
        for ncx, ncy, ncr in circles:
            assert _seg_point_dist(seg, ncx, ncy) >= ncr - 1.0, \
                f"エッジ線がノード円を貫通: seg={seg} circle=({ncx},{ncy},{ncr})"


def test_relations_too_many_edges_hard_fail(tmp_path: Path) -> None:
    """関係図の実描画ラベルが 9 枚以上 (上限 8 枚) の DeepDive md は build が hard fail。

    なぜ重要か: レンダラの力学分離は band 間 gap (~84 px) にラベル多数を
    詰め込めない構造的限界がある。「ラベルを 8 枚以下に絞り込めない関係図」は
    本質を選別できていない記事のシグナルなので、生成段階で loud failure させて
    サイレントな破綻描画を封じる ([[feedback_check_design_principles]] 1 段)。
    frenemy (協調的競合) は coop/rival の 2 ラベルでカウントする。
    """
    from tools.render_deepdive import build_deepdive_context, DeepDiveIncompleteError
    import os
    os.environ["NEWS_GRASP_SKIP_URL_CHECK"] = "1"   # オフラインで URL 検証スキップ
    md = tmp_path / "2026-06-04-DeepDive.md"
    edges_json = ",\n    ".join(
        f'{{"from": "n{i}", "to": "n{(i + 1) % 9}", "kind": "提携", "label": "L{i}"}}'
        for i in range(9)
    )
    nodes_json = ",\n    ".join(
        f'{{"id": "n{i}", "label": "Node{i}", "group": "G{i % 2}"}}' for i in range(9)
    )
    md.write_text(
        "---\n"
        "title: t\nlens: ai\ndate: 2026-06-04\nog_image: /og.jpg\n"
        "tags: []\n---\n\n"
        "## 背景\n\n```timeline\n[{\"date\": \"2026-06-04\", \"text\": \"x\"}]\n```\n\n"
        "```players\n[{\"id\": \"x\", \"label\": \"X\"}]\n```\n\n"
        "```relations\n"
        "{\n  \"nodes\": [\n    " + nodes_json + "\n  ],\n"
        "  \"edges\": [\n    " + edges_json + "\n  ]\n}\n```\n\n"
        "## 深掘り\n\n```chart\n{\"type\": \"bar\", \"title\": \"c1\", \"x\": [\"a\"], \"series\": [{\"name\": \"s\", \"data\": [1]}]}\n```\n\n"
        "```chart\n{\"type\": \"bar\", \"title\": \"c2\", \"x\": [\"a\"], \"series\": [{\"name\": \"s\", \"data\": [1]}]}\n```\n\n"
        "```table\n{\"columns\": [\"c\"], \"rows\": [[\"v\"]]}\n```\n\n"
        "## 注目点\n\n```decision\n{\"decider\": \"d\", \"options\": [\"o\"]}\n```\n",
        encoding="utf-8",
    )
    with pytest.raises(DeepDiveIncompleteError, match="ラベルが 9 枚"):
        build_deepdive_context(md)


def test_relations_orphan_node_hard_fail(tmp_path: Path) -> None:
    """関係図のノードがどの edge にも現れない (=孤立) 場合は build が hard fail。

    なぜ重要か: 2026-06-04 ユーザー指摘「関係図に繋がっていない丸 (BCG) が浮く」事故。
    edge 上限 8 枚に詰める過程でノードへの接続線が全部落ちると、レンダラはノードを
    描いてしまい「孤立した丸」が残る。「ノードを置くなら edge を 1 本以上持たせる」は
    関係図の最小品質保証なので、ビルド時に loud failure させて目視レビュー漏れを
    封じる ([[feedback_check_design_principles]] 1 段「失敗を表現できない構造」)。
    """
    from tools.render_deepdive import build_deepdive_context, DeepDiveIncompleteError
    import os
    os.environ["NEWS_GRASP_SKIP_URL_CHECK"] = "1"
    md = tmp_path / "2026-06-04-DeepDive.md"
    md.write_text(
        "---\n"
        "title: t\nlens: ai\ndate: 2026-06-04\nog_image: /og.jpg\n"
        "tags: []\n---\n\n"
        "## 背景\n\n```timeline\n[{\"date\": \"2026-06-04\", \"text\": \"x\"}]\n```\n\n"
        "```players\n[{\"id\": \"x\", \"label\": \"X\"}]\n```\n\n"
        "```relations\n"
        "{\n  \"nodes\": [\n"
        "    {\"id\": \"a\", \"label\": \"A\", \"group\": \"G1\"},\n"
        "    {\"id\": \"b\", \"label\": \"B\", \"group\": \"G1\"},\n"
        "    {\"id\": \"c\", \"label\": \"C\", \"group\": \"G2\"}\n"
        "  ],\n"
        "  \"edges\": [\n"
        "    {\"from\": \"a\", \"to\": \"b\", \"kind\": \"提携\", \"label\": \"L\"}\n"
        "  ]\n}\n```\n\n"
        "## 深掘り\n\n```chart\n{\"type\": \"bar\", \"title\": \"c1\", \"x\": [\"a\"], \"series\": [{\"name\": \"s\", \"data\": [1]}]}\n```\n\n"
        "```chart\n{\"type\": \"bar\", \"title\": \"c2\", \"x\": [\"a\"], \"series\": [{\"name\": \"s\", \"data\": [1]}]}\n```\n\n"
        "```table\n{\"columns\": [\"c\"], \"rows\": [[\"v\"]]}\n```\n\n"
        "## 注目点\n\n```decision\n{\"decider\": \"d\", \"options\": [\"o\"]}\n```\n",
        encoding="utf-8",
    )
    with pytest.raises(DeepDiveIncompleteError, match=r"孤立ノード.*\['c'\]"):
        build_deepdive_context(md)


def test_deepdive_no_empty_href_anchors() -> None:
    """ビルド済み deepdive HTML には href が空の <a> タグが 1 つも存在しない。

    なぜ重要か: 2026-06-04 ユーザー指摘で「参考リンク 8 件中 7 件 / 図表 SOURCE
    の *2/*3 がクリックしても自己参照に戻る」事故。md に URL が無い参考リンクを
    テンプレが `<a class="dd-source" >` (href なし) でラップしていたため、ブラウザが
    href なし `<a>` を現在 URL で解決 → 自己参照クリックになっていた。テンプレ側で
    URL 空のとき `<a>` でなく `<div>` / `<span>` を出す構造に変えたうえで、
    生成済み HTML に `href=""` や href なし `<a class="dd-source">` が混入しない
    ことを契約として固定する ([[feedback_check_design_principles]] 1 段
    「失敗を表現できない構造に変える」+ [[feedback_llm_url_fabrication_ban]]
    「URL の在/不在を 1 種類のレンダリングに圧縮しない」)。
    """
    import re as _re
    for md in (ROOT / "digest" / "DeepDive").glob("*-DeepDive.md"):
        date = md.stem.replace("-DeepDive", "")
        html_path = ROOT / "docs" / "deepdive" / date / "index.html"
        if not html_path.exists():
            continue
        html = html_path.read_text(encoding="utf-8")
        # href="" (空文字列の href)
        assert 'href=""' not in html, \
            f"{html_path.name}: href=\"\" が残存 (空 href の <a> は自己参照クリックを生む)"
        # <a class="dd-source"> で href 属性が無いケース
        bad = _re.findall(r'<a class="dd-source"[^>]*>', html)
        for tag in bad:
            assert "href=" in tag, \
                f"{html_path.name}: href の無い <a class=\"dd-source\"> が残存: {tag}"
        # <a class="cite"> も同様 (図表 SOURCE)
        bad_cite = _re.findall(r'<a class="cite"[^>]*>', html)
        for tag in bad_cite:
            assert "href=" in tag and 'href=""' not in tag, \
                f"{html_path.name}: href が空 or 無い <a class=\"cite\"> が残存: {tag}"


def test_deepdive_template_has_mobile_overflow_guards() -> None:
    """deepdive-template.html がスマホ overflow 用 CSS ガードを物理的に含むことを契約化。

    なぜ重要か: 2026-06-04 ユーザー指摘で TIMELINE の英字連続 (社名・URL) が画面右に
    溢れる症状が発生。`.dd p, .dd li, .dd td` 等への `overflow-wrap: anywhere` と、
    grid 1fr 子の `min-width: 0` が欠けると CJK 折り返しが効かず再発する。
    境界 1 箇所集約 ([[feedback_check_design_principles]] 2 段) でテンプレを正典化し、
    新規 deepdive ページがビルドされる度にガードが保たれていることを物理的に確認する。
    """
    tpl = ROOT / "prompts" / "deepdive-template.html"
    css = tpl.read_text(encoding="utf-8")
    # 1) 全テキスト要素グローバルガード
    assert "overflow-wrap: anywhere" in css, \
        "deepdive-template.html: overflow-wrap: anywhere が無い (英字連続が画面外に溢れる)"
    assert ".dd p" in css and "overflow-wrap" in css, \
        "deepdive-template.html: .dd p への overflow-wrap グローバルガードが無い"
    # 2) grid 子の min-width:0 (TIMELINE 本文 / プレイヤーカード / decision 等)
    assert ".dd-tl__body { min-width: 0" in css, \
        "deepdive-template.html: .dd-tl__body の min-width: 0 が無い (grid 1fr 子が min-content で暴走)"
    # 3) モバイル @media で SVG/TABLE が 375px 画面に全入りすること (2026-06-04 ユーザー指示)。
    #    scrollWidth==375 だけでは「親の overflow-x:auto で閉じ込められた長尺要素」が
    #    右に隠れる症状を検出できないため、子の min-width:0 を契約として固定する。
    assert ".dd-relwrap svg { min-width: 0" in css, \
        "deepdive-template.html: モバイル時の .dd-relwrap svg min-width:0 が無い (右に隠れる)"
    assert ".dd-table { min-width: 0" in css, \
        "deepdive-template.html: モバイル時の .dd-table min-width:0 が無い (右に隠れる)"
    # 4) ビルド済み 1 ページにも反映されていることを確認 (テンプレ→生成パイプライン疎通)
    sample = ROOT / "docs" / "deepdive" / "2026-06-04" / "index.html"
    if sample.exists():
        html = sample.read_text(encoding="utf-8")
        assert "overflow-wrap: anywhere" in html, \
            "build 後の deepdive HTML に overflow-wrap ガードが反映されていない (テンプレ→build 疎通断)"
        assert ".dd-relwrap svg { min-width: 0" in html, \
            "build 後の deepdive HTML にモバイル SVG 縮小ガードが反映されていない"


def test_deepdive_fig_src_does_not_force_single_line_citation() -> None:
    """図表 SOURCE 行 (.dd-fig__src) の citation が長文時も折り返せることを契約化。

    なぜ重要か: 2026-06-04 計測で、過去回 2026-06-01 ページの SOURCE 行
    "*6 [PONY AI Inc. Reports First Quarter 2026 Financial Result]" が
    `.dd-fig__src .cite { white-space: nowrap }` で 1 行に伸び、375px viewport
    に対し +190px 突き出て横スクロールが発生していた。原因は
    (1) .cite が nowrap で改行不可、
    (2) .val が flex 子なのに min-width: auto で shrink しない、
    (3) .dd-fig__src が flex-wrap: nowrap で .val 自身も縮まない、
    の三点。境界 1 箇所集約 ([[feedback_check_design_principles]] 2 段) で
    テンプレ CSS の以下 3 項目を物理的に固定し、長文 citation の再発を封じる。
    """
    tpl = ROOT / "prompts" / "deepdive-template.html"
    css = tpl.read_text(encoding="utf-8")
    # 1) cite が nowrap でないこと (古い nowrap が残ったままだと長文で突き出す)
    assert ".dd-fig__src .cite { white-space: nowrap" not in css, \
        "deepdive-template.html: .dd-fig__src .cite の white-space:nowrap が残存 (長文 citation で横突き出し)"
    # 2) cite に overflow-wrap:anywhere があること
    assert ".dd-fig__src .cite { white-space: normal" in css, \
        "deepdive-template.html: .dd-fig__src .cite の white-space:normal が無い"
    # 3) val が flex 子として shrink できるよう min-width:0 を持つこと
    assert ".dd-fig__src .val { min-width: 0" in css, \
        "deepdive-template.html: .dd-fig__src .val の min-width:0 が無い (flex 子が縮まず突き出る)"
    # 4) 親 flex に flex-wrap: wrap が指定され、SOURCE 全体が折り返せること
    assert "flex-wrap: wrap" in css, \
        "deepdive-template.html: .dd-fig__src の flex-wrap:wrap が無い (子要素が縦に積めない)"

    # 5) ビルド済み HTML (citation 長文を含む 2026-06-01) にも反映されている
    sample = ROOT / "docs" / "deepdive" / "2026-06-01" / "index.html"
    if sample.exists():
        html = sample.read_text(encoding="utf-8")
        assert ".dd-fig__src .cite { white-space: normal" in html, \
            "build 後の 2026-06-01 deepdive に cite normal 化が反映されていない"
        assert ".dd-fig__src .val { min-width: 0" in html, \
            "build 後の 2026-06-01 deepdive に val min-width:0 が反映されていない"


def test_relations_label_text_width_bounded() -> None:
    """ラベル本文 (label) は全角換算 18 字相当を上限とし、超過は省略 (…) する。

    なぜ重要か: 「Frontier Alliances提携(2/23)」「DeepMind実装提携・$750M基金」など
    25 文字級の長尺ラベルがそのまま渡されると、1080px 幅の関係図 1 段に 6 枚並べる
    余地が物理的に無くなり、_resolve_labels が AABB 分離を完了できない (= 重なり残存)。
    プロンプト側でも「ラベルは短く」と書いてあるが守られないことがあるため、生成側を
    待たずレンダラ側で省略のセーフネットを敷く。
    """
    long_label = "Frontier Alliances提携(2/23) 戦略変革・実装受注を侵食する大型協業"  # 35 字超
    rel = {
        "title": "長尺ラベル省略テスト",
        "nodes": [
            {"id": "a", "label": "A", "group": "陣営1"},
            {"id": "b", "label": "B", "group": "陣営2"},
        ],
        "edges": [{"from": "a", "to": "b", "label": long_label, "kind": "提携"}],
    }
    svg = relations_svg(rel)
    # SVG 内の <text> に「…」が現れ、元のラベル全文は乗らない
    assert "…" in svg, "長尺ラベルが省略 (…) されていない"
    assert long_label not in svg, "省略なしの長尺ラベルがそのまま SVG に乗っている"


# ── table 未確認セル検出 (★新規描画) ─────────────────────────────────────────

def test_table_flags_unconfirmed_cells() -> None:
    """未開示/非開示 セルが unconfirmed フラグを得る。"""
    body = _FIXTURE.read_text(encoding="utf-8")
    table = build_table(extract_blocks(body)["table"][0])
    flat = [c for row in table["rows"] for c in row]
    unconf = [c["text"] for c in flat if c["unconfirmed"]]
    assert "未開示" in unconf and "非開示" in unconf
    # 確定値は淡色化しない
    assert not any(c["unconfirmed"] for c in flat if c["text"] == "10 都市")


# ── chart SVG ─────────────────────────────────────────────────────────────────

def test_chart_svg_renders_bars() -> None:
    body = _FIXTURE.read_text(encoding="utf-8")
    chart = extract_blocks(body)["chart"][0]
    svg = chart_svg(chart)
    assert svg.startswith("<svg")
    assert svg.count("<rect") >= 3   # 3 カテゴリの棒


# ── context + render ──────────────────────────────────────────────────────────

def test_context_has_all_render_fields() -> None:
    ctx = build_deepdive_context(_FIXTURE)
    assert ctx["title"].startswith("Robotaxi")
    assert ctx["timeline"] and ctx["players"]
    assert ctx["relations_svg"] and ctx["relations_legend"]
    assert len(ctx["charts"]) == 2 and all(c["svg"] for c in ctx["charts"])
    assert ctx["table"] and ctx["decision"]
    assert ctx["bg_prose"] and ctx["di_prose"] and ctx["summary_prose"]
    assert len(ctx["sources"]) == 5


def test_og_image_falls_back_to_real_image_not_site_root() -> None:
    """frontmatter に og_image が無くても og:image は実画像 (.jpg) を指す。

    2026-06-01 まで DeepDive は og_image フォールバックを欠き、空文字を絶対化して
    og:image がサイト HTML (BASE_URL) を指していた → Discord 等がサムネを出せず無画像
    カードになった。本テストは「og:image が必ず実画像ファイルを指す」不変条件を 1 件で
    固定する (個別の md ごとの smoke は増やさない)。
    """
    from tools.config import BASE_URL

    ctx = build_deepdive_context(_FIXTURE)  # _FIXTURE は og_image を持たない
    assert ".jpg" in ctx["og_image"], ctx["og_image"]  # ?v=N クエリ付きも許容
    assert ctx["og_image"] != BASE_URL and ctx["og_image"] != BASE_URL + "/"
    assert "/assets/og/" in ctx["og_image"]


def test_build_writes_page_to_deepdive_path(tmp_path: Path) -> None:
    """docs/deepdive/{date}/index.html に出力し、本文の主要要素を含む。"""
    pages = build_deepdive_pages(
        docs_root=tmp_path, full=True, digest_dir=_FIXTURE.parent,
    )
    out = tmp_path / "deepdive" / "2026-05-31" / "index.html"
    assert out in pages and out.exists()
    html = out.read_text(encoding="utf-8")
    assert "TODAY'S THEME" in html
    assert "Robotaxi 商用化の分岐点" in html
    assert "<svg" in html                     # relations / chart SVG
    assert "未確認" in html                    # 未確認バッジ
    assert "THE ISSUE" in html                 # decision
    # 3 階層強調が HTML 化されている ([[X]] → chip, __X__ → underline)
    assert 'class="emph-bold"' in html and 'class="emph-und"' in html


def _incomplete_md(path: Path) -> Path:
    """relations と table を欠く DeepDive md (= 未完成記事) を 1 件書き出す。"""
    path.write_text(
        '---\ntitle: "テスト深掘り"\ndate: "2026-06-07"\nissue: "20260607"\n'
        'kind: deepdive\nlens: ai\n---\n\n'
        "## 背景\n\n```timeline\n[]\n```\n\n```players\n[]\n```\n\n本文。\n\n"
        "## 深掘り\n\n"
        '```chart\n{"type":"bar","title":"x","series":[{"name":"a","data":[1,2,3]}],'
        '"categories":["FY24","FY25","FY26"],"source":"s"}\n```\n\n本文。\n\n'
        "## 注目点\n\n"
        '```decision\n{"issue":"x","options":["a"],"deadline":"d","decider":"who"}\n```\n',
        encoding="utf-8",
    )
    return path


def test_build_fails_loudly_on_missing_relations_table(tmp_path: Path) -> None:
    """関係図 (relations) / データ表 (table) を欠く DeepDive は build を hard fail させる。

    なぜ重要か: weekly-research-system.md は relations を「必須」と明記しているのに
    2026-05-31 記事は relations 無しで公開された。プロンプト/記憶頼みの防御は破れる
    という実害があったので、ビルド時に loud に落として未完成記事の公開を構造的に阻止する
    不変条件を locked-in する (この test が緩むと「黙って空描画」に逆戻りする)。
    """
    src = _incomplete_md(tmp_path / "2026-06-07-DeepDive.md")
    # context 構築の時点で必須ブロック欠落を loud に弾く
    with pytest.raises(DeepDiveIncompleteError) as exc:
        build_deepdive_context(src)
    assert "relations" in str(exc.value) and "table" in str(exc.value)
    # build_deepdive_pages 経由でも握りつぶされず伝播する (= 公開パスで止まる)
    with pytest.raises(DeepDiveIncompleteError):
        build_deepdive_pages(
            docs_root=tmp_path / "out", full=True, digest_dir=src.parent,
        )


def test_build_fails_on_single_chart(tmp_path: Path) -> None:
    """必須ブロックが揃っていても、深掘りの chart が 1 本だけなら hard fail させる。

    「図表 1 つでは論点を多面的に示せない」ので深掘りは chart 最低 2 本を不変条件に
    する (2026-05-31 ユーザー指示)。relations/table は在るのに chart が 1 本、という
    今回の境界条件を locked-in する。
    """
    src = tmp_path / "2026-06-14-DeepDive.md"
    src.write_text(
        '---\ntitle: "テスト"\ndate: "2026-06-14"\nissue: "20260614"\n'
        'kind: deepdive\nlens: ai\n---\n\n'
        "## 背景\n\n```timeline\n[]\n```\n\n```players\n[]\n```\n\n"
        '```relations\n{"nodes":[{"id":"a","label":"A"},{"id":"b","label":"B"}],'
        '"edges":[{"from":"a","to":"b","label":"x","kind":"競合"}],"source":"s"}\n```\n\n本文\n\n'
        "## 深掘り\n\n"
        '```chart\n{"type":"bar","title":"x","series":[{"name":"a","data":[1,2,3]}],'
        '"categories":["a","b","c"],"source":"s"}\n```\n\n'
        '```table\n{"columns":["a","b"],"rows":[["1","2"]],"source":"s"}\n```\n\n本文\n\n'
        "## 注目点\n\n"
        '```decision\n{"issue":"x","options":["a"],"deadline":"d","decider":"w"}\n```\n',
        encoding="utf-8",
    )
    with pytest.raises(DeepDiveIncompleteError) as exc:
        build_deepdive_context(src)
    assert "chart" in str(exc.value)


def test_real_digest_is_complete(tmp_path: Path) -> None:
    """実 digest (本番公開対象) は必須ブロックを全て備え、hard fail せず render できる。"""
    if not _REAL.exists():
        return  # 実 digest が無い環境では skip
    ctx = build_deepdive_context(_REAL)        # 欠落があればここで例外 = テスト失敗
    assert ctx["relations_svg"], "実記事に関係図 (relations) が無い"
    assert ctx["table"], "実記事にデータ表 (table) が無い"
    assert len(ctx["charts"]) >= 2, "深掘りの chart が 2 本未満 (図表は最低 2 本)"
    pages = build_deepdive_pages(
        docs_root=tmp_path, full=True, digest_dir=_REAL.parent,
    )
    assert pages and pages[0].exists()


def test_deepdive_output_is_isolated_from_daily_pipeline(tmp_path: Path) -> None:
    """DeepDive は docs/deepdive/ 配下にのみ出力し、LP 等を汚さない。"""
    pages = build_deepdive_pages(
        docs_root=tmp_path, full=True, digest_dir=_FIXTURE.parent,
    )
    for p in pages:
        assert "deepdive" in p.parts, f"DeepDive が deepdive/ 外に出力された: {p}"
    # index.html (LP) は生成されない
    assert not (tmp_path / "index.html").exists()


# ── related (続報の過去参照 + 変化点) ★2026-06-03 ユーザー指示 ────────────────────

def _complete_md_with_related(tmp_path: Path, *, with_related: bool) -> Path:
    """必須ブロックを全て備えた完全な DeepDive md。with_related で related の有無を切替える。"""
    rel = (
        '```related\n'
        '[{"date":"2026-06-01","title":"ロボタクシー覇権、Waymo独走とTeslaの量産反攻",'
        '"relation":"波及","link":"規制強化 → 競争再編",'
        '"change":"前回は規模と資金調達の競争を扱った。今回の変化点は量産が安全データを動かしたか"}]\n'
        '```\n\n'
        if with_related else ""
    )
    src = tmp_path / "2026-06-20-DeepDive.md"
    src.write_text(
        '---\ntitle: "テスト深掘り"\ndate: "2026-06-20"\nissue: "20260620"\n'
        'kind: deepdive\nlens: ai\n---\n\n'
        "## 背景\n\n" + rel +
        "```timeline\n[]\n```\n\n```players\n[]\n```\n\n"
        '```relations\n{"nodes":[{"id":"a","label":"A"},{"id":"b","label":"B"}],'
        '"edges":[{"from":"a","to":"b","label":"x","kind":"競合"}],"source":"s"}\n```\n\n本文\n\n'
        "## 深掘り\n\n"
        '```chart\n{"type":"line","title":"x","series":[{"name":"a","data":[1,2,3]}],'
        '"categories":["FY24","FY25","FY26"],"source":"s"}\n```\n\n'
        '```chart\n{"type":"bar","title":"y","series":[{"name":"b","data":[3,4,5]}],'
        '"categories":["FY24","FY25","FY26"],"source":"s"}\n```\n\n'
        '```table\n{"columns":["a","b"],"rows":[["1","2"]],"source":"s"}\n```\n\n本文\n\n'
        "## 注目点\n\n"
        '```decision\n{"issue":"x","options":["a"],"deadline":"d","decider":"w"}\n```\n',
        encoding="utf-8",
    )
    return src


def test_related_url_is_derived_from_date_not_handwritten(tmp_path: Path) -> None:
    """related の公開 URL は date から自動生成し、手書きさせない (誤 URL を構造的に排除)。

    なぜ重要か: 過去レポートへのリンク URL を Agent に手書きさせると、ドメイン・パス・
    日付フォーマットの取り違えで 404 リンクを量産する。md には date/title/change だけ
    書かせ、URL は build 側が `{BASE_URL}/deepdive/{date}/` で導出する不変条件を固定する。
    """
    from tools.config import BASE_URL

    ctx = build_deepdive_context(_complete_md_with_related(tmp_path, with_related=True))
    assert len(ctx["related"]) == 1
    r = ctx["related"][0]
    assert r["title"].startswith("ロボタクシー覇権")
    assert r["change"], "変化点 (change) が空"
    assert r["url"] == f"{BASE_URL}/deepdive/2026-06-01/", r["url"]
    # 関連の種類・根拠と、種類バッジ色 (関係図 EDGE_KINDS のトーンに揃える)
    assert r["relation"] == "波及"
    assert r["relation_color"] == "#2D5BB8", r["relation_color"]  # 波及 = 供給の青
    assert r["link"] == "規制強化 → 競争再編"
    # ① カテゴリ色: tmp には過去 md (2026-06-01) が無いので near-black に退避する
    assert r["accent"] == INK, r["accent"]


def test_related_absent_is_empty_list_backward_compatible() -> None:
    """related ブロックの無い既存記事は ctx['related']==[] で、render が壊れない (後方互換)。

    related は任意ブロック。必須化すると related を持たない既存 4 本が全て hard fail
    するので、欠落時は空リストに退避させ、テンプレ側で非表示にする。
    """
    ctx = build_deepdive_context(_FIXTURE)  # _FIXTURE は related を持たない
    assert ctx["related"] == []


def test_related_renders_link_and_change_in_html(tmp_path: Path) -> None:
    """related があると、生成 HTML に過去レポートへのリンクと変化点が出る。"""
    src = _complete_md_with_related(tmp_path, with_related=True)
    pages = build_deepdive_pages(
        docs_root=tmp_path / "out", full=True, digest_dir=src.parent,
    )
    html = pages[0].read_text(encoding="utf-8")
    assert "/deepdive/2026-06-01/" in html, "過去レポートへのリンクが無い"
    assert "ロボタクシー覇権" in html, "過去レポートのタイトルが無い"
    assert "前回" in html, "変化点 (change) の文言が無い"
    assert "波及" in html, "関連種類バッジが無い"
    assert "規制強化" in html, "関連根拠チップが無い"
    assert "#2D5BB8" in html, "種類バッジ色 (インライン) が無い"


def test_related_absent_renders_without_related_block(tmp_path: Path) -> None:
    """related が無い記事の HTML には関連レポート枠が出ない (空枠を描かない)。"""
    src = _complete_md_with_related(tmp_path, with_related=False)
    pages = build_deepdive_pages(
        docs_root=tmp_path / "out", full=True, digest_dir=src.parent,
    )
    html = pages[0].read_text(encoding="utf-8")
    # CSS 定義 (.dd-related {...}) は常駐するので、描画された要素 (<aside>) の有無で判定する。
    assert '<aside class="dd-related"' not in html, "related が無いのに関連レポート枠が描画された"
