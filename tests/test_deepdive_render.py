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


# ── relations 役割レイヤー + ラベル無重なり (★2026-06-01 ユーザー指示) ──────────

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
_CHIP_RECT_RE = re.compile(
    r'<g transform="translate\(([-\d.]+),([-\d.]+)\)"><rect width="([-\d.]+)" height="26.0"')


def _node_circles(svg: str) -> list[tuple[float, float, float]]:
    return [(float(a), float(b), float(c)) for a, b, c in _NODE_CIRCLE_RE.findall(svg)]


def _chip_rects(svg: str) -> list[tuple[float, float, float, float]]:
    return [(float(x), float(y), float(w), 26.0) for x, y, w in _CHIP_RECT_RE.findall(svg)]


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


def test_relations_layered_by_role_and_no_label_overlap() -> None:
    """役割 (米陣営/中国陣営/規制) を別レイヤーに積み、ラベルはノード/相互で重ねない。

    2026-06-01 ユーザー指示「異なる役割は別レイヤー・同じ役割は同レイヤー」「文字の
    重なりで読めないことは絶対不可」を契約として locked-in する。ラベルチップが
    ノード円や他チップと (読めなくなるほど) 重なったら fail させ、円環/中央集約レイアウト
    への逆戻りを構造的に防ぐ。
    """
    rel = _MULTICAMP_REL
    lay = layout_relations(rel)
    pos = {nd["id"]: (nd["x"], nd["y"]) for nd in lay["nodes"]}
    # 同じ役割は同じ y (同レイヤー)、異なる役割は別の y
    assert pos["waymo"][1] == pos["tesla"][1], "米陣営が同レイヤーに無い"
    assert pos["pony"][1] == pos["baidu"][1] == pos["geely"][1], "中国陣営が同レイヤーに無い"
    assert pos["nhtsa"][1] == pos["chinareg"][1], "規制が同レイヤーに無い"
    ys = {pos["waymo"][1], pos["pony"][1], pos["nhtsa"][1]}
    assert len(ys) == 3, "3 役割が別レイヤーに分かれていない"
    assert pos["nhtsa"][1] == max(ys), "規制当局が最下段 (見上げる三角) に無い"

    svg = relations_svg(rel)
    circles = _node_circles(svg)
    rects = _chip_rects(svg)
    assert len(circles) == 7, f"ノード円が 7 個でない: {len(circles)}"
    assert len(rects) == 8, f"ラベルチップが 8 個でない: {len(rects)}"
    # ラベルチップ vs ノード円: 文字が読めなくなる重なりは 0
    for r in rects:
        for c in circles:
            assert not _rect_circle_hit(r, c), f"ラベルがノードに重なる: rect={r} circle={c}"
    # ラベルチップ同士の重なりも 0
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            assert not _rect_overlap(rects[i], rects[j]), \
                f"ラベル同士が重なる: {rects[i]} ∩ {rects[j]}"


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
