#!/usr/bin/env python3
"""News-Grasp DeepDive (週次 TODAY'S THEME) ページレンダラ。

`digest/DeepDive/{YYYY-MM-DD}-DeepDive.md` を 1 テーマ深掘りページ
`docs/deepdive/{date}/index.html` に変換する。日次 digest の生成系
(generate_pages.build_all / _collect_entries) とは疎結合の独立レンダーパス
(deepdive_integration_spec.md オプション B)。

不変条件 (2026-05-31 DeepDive 事故) の本質は「日次 digest の **entry ストリーム**
(build_all / _collect_entries) を DeepDive で汚染しない」こと。カテゴリ・アーカイブ・
summary・LP のカード一覧 (lens_cards) はこの entry ストリーム由来なので従来どおり
DeepDive を含めない。
※ 例外: LP 上部ヒーローの SUMMARY ⇆ DEEP DIVE スライダーだけは別経路で、
  generate_pages の build_index が build_deepdive_context() を直接呼び最新 DeepDive を
  明示注入する (entries を経由しないので不変条件と両立)。

設計の一次ソース:
  - 出力スキーマ: prompts/weekly-research-system.md (fenced JSON ブロック定義)
  - ビジュアル仕様: Claude Design "News Grasp DeepDive.html" (handoff bundle)
                    docs/specs/_design_received/deepdive-*.jsx
  - レンダラはスキーマ (生成側が必ず出力する形) を一次に、デザインの追加装飾
    (stance バッジ等スキーマに無いフィールド) は graceful に省く。

新規描画 (旧レンダラ未対応):
  - relations: nodes+edges の SVG ネットワーク図。スキーマは座標を持たないため
    本モジュールが円環 auto-layout で node 座標を決める。
  - table: 整形済みデータ表。「未確認/未開示/非開示」セルは淡色+破線バッジ。

公開 API:
  build_deepdive_context(md_path) -> dict   # Jinja context
  build_deepdive_pages(docs_root, full)     # digest/DeepDive/*.md を全 render
"""
from __future__ import annotations

import html as _html
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

_PKG_ROOT = Path(__file__).resolve().parent.parent
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from tools.config import BASE_URL, CATEGORIES, SITE_TITLE  # noqa: E402
from tools.generate_pages import (  # noqa: E402
    _absolutize,
    _needs_rebuild,
    _templates_mtime,
    parse_frontmatter,
    render_page,
)

# ── DeepDive デザイントークン (deepdive-shared.jsx DD と同値) ──────────────────
INK = "#1A1A1A"          # DeepDive primary accent (near-black; 純黒 #000 は不可)
GOLD = "#C9A155"         # secondary accent (要約枠・罫線)
CREAM = "#F0EBE0"
DIM = "#5C5A52"
SOFT = "#8B8B85"
PAPER = "#FAF7F0"
BORDER = "#E2DED4"

# edge kind → 意味的パレット (relations。weekly-research-system.md の kind 語彙)
EDGE_KINDS: dict[str, dict[str, Any]] = {
    "競合": {"color": "#8E2A19", "dash": False},
    "規制": {"color": "#181C2A", "dash": False},
    "出資": {"color": "#B8860B", "dash": False},
    "提携": {"color": "#2E6B52", "dash": False},
    "供給": {"color": "#2D5BB8", "dash": False},
    "対立": {"color": "#8E2A19", "dash": True},
}
_DEFAULT_EDGE = {"color": INK, "dash": False}

# 関係図の構図ルール (2026-05-31 ユーザー指示で恒久化):
#   競合/対立 = 勢力の対立 → 左右に分けて配置 (rivalry を 2-color)
#   出資/提携/供給 = 協力 → 同じ側に寄せ、上下 (縦) に積む (出資元/親を上)
#   規制 = 監督 → 当局を中央下に置き、両勢力を見上げる三角構図にする
#   協調的競合 (frenemy) = 協力かつ競合 → 両者を左右に置き、協力線(緑)と競合線(赤)を
#     併走させる。「提携でありつつ人月モデルで競合」のような二面関係を 1 本に潰さず
#     描くための kind。group-to-group (陣営 vs 陣営) の中心命題を表現する (2026-05-31 追加)。
# 「とりあえず全部つなぐ」のではなく勢力構造が一目で分かる配置にするのが目的。
_RIVAL_KINDS = {"競合", "対立"}
_COOP_KINDS = {"出資", "提携", "供給", "協力"}
_AUTH_KINDS = {"規制"}
_FRENEMY_KINDS = {"協調的競合", "協力競合", "frenemy"}
# frenemy の二面エッジで使う色 (協力面=提携の緑 / 競合面=競合の赤)。
_FRENEMY_COOP_COLOR = "#2E6B52"
_FRENEMY_RIVAL_COLOR = "#8E2A19"

# 「裏が取れていない」ことを示すセル値 (table の淡色化判定)。
_UNCONFIRMED_TOKENS = ("未確認", "未開示", "非開示")

# fenced ブロック ```lang\n...\n```
_FENCED_RE = re.compile(r"^```([A-Za-z_]+)\r?\n(.*?)\r?\n```", re.DOTALL | re.MULTILINE)
# `## 見出し` セクション分割
_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
# 参考リンク bullet: `- 説明文: https://...`  (末尾 URL を拾う)
_SRC_URL_RE = re.compile(r"(https?://\S+)\s*$")


# ── ブロック / 本文抽出 ───────────────────────────────────────────────────────

def extract_blocks(body: str) -> dict[str, list[Any]]:
    """本文中の全 fenced JSON ブロックを lang ごとに集約して返す。

    chart は複数許容なので値は常に list。壊れた JSON のブロックは握りつぶさず
    stderr に警告を出して skip する (捏造で埋めない・loud 寄り)。
    """
    out: dict[str, list[Any]] = {}
    for m in _FENCED_RE.finditer(body):
        lang, raw = m.group(1), m.group(2)
        if lang not in ("timeline", "players", "relations", "chart", "table", "decision"):
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            print(f"[warn] DeepDive {lang} ブロックの JSON 解析失敗: {exc}", file=sys.stderr)
            continue
        out.setdefault(lang, []).append(data)
    return out


def _strip_fenced(text: str) -> str:
    """セクション本文から fenced ブロックを除去して散文だけ残す。"""
    return _FENCED_RE.sub("", text)


def split_sections(body: str) -> dict[str, str]:
    """`## 見出し` で本文を section 名 → 生テキストに分割する。"""
    sections: dict[str, str] = {}
    matches = list(_SECTION_RE.finditer(body))
    for i, m in enumerate(matches):
        name = m.group(1).strip().lstrip("§").strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        sections[name] = body[start:end]
    return sections


def _prose_paragraphs(section_text: str) -> list[str]:
    """セクションから fenced を除いた散文を段落 (空行区切り) のリストにする。"""
    plain = _strip_fenced(section_text)
    paras = [p.strip() for p in re.split(r"\r?\n\s*\r?\n", plain)]
    return [p.replace("\r", "").replace("\n", " ").strip() for p in paras if p.strip()]


def parse_sources(section_text: str) -> list[dict[str, str]]:
    """`## 参考リンク` の bullet を {text, url} のリストにする。"""
    sources: list[dict[str, str]] = []
    for line in section_text.splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        item = line[2:].strip()
        um = _SRC_URL_RE.search(item)
        url = um.group(1) if um else ""
        text = item[: um.start()].rstrip(" :：—-") if um else item
        sources.append({"text": text, "url": url})
    return sources


# ── relations: 座標なしスキーマ → 円環 auto-layout + SVG ──────────────────────

def _coop_order(members: list[str], coop: list[dict[str, Any]]) -> list[str]:
    """協力エッジ (出資元→出資先 等) で source を上に来る順に安定ソートする。"""
    mset = set(members)
    succ: dict[str, list[str]] = {m: [] for m in members}
    indeg: dict[str, int] = {m: 0 for m in members}
    for e in coop:
        a, b = e.get("from"), e.get("to")
        if a in mset and b in mset and a != b:
            succ[a].append(b)
            indeg[b] += 1
    order: list[str] = []
    avail = [m for m in members if indeg[m] == 0]  # 入力順を保つ (安定)
    while avail:
        u = avail.pop(0)
        order.append(u)
        for v in succ[u]:
            indeg[v] -= 1
            if indeg[v] == 0:
                avail.append(v)
    for m in members:  # 循環があれば残りを末尾に
        if m not in order:
            order.append(m)
    return order


def layout_relations(rel: dict[str, Any]) -> dict[str, Any]:
    """nodes を「意味」に基づいて配置し x/y/r を決める (2026-05-31 全面改訂)。

    スキーマ (weekly-research-system.md) の relations は座標を持たないため、
    レンダラ側が決定論的に配置する。ただし円環に等間隔で並べる素朴な方式は
    「勢力構造が読めない」「ラベルが枠からはみ出す」ため、edge の kind から
    構図を決める:

      競合/対立 → 当事者を左右に二分 (rivalry グラフを 2-color)。
      出資/提携/供給 → 協力相手と同じ側に寄せ、出資元/親を上に縦積み。
      規制 → 当局を中央下に置き、両勢力を見上げる三角構図にする。

    これにより「左右で勢力が割れ、協力は縦、規制当局は中央下」という
    一目で読める図になる。分類できないノードは中央列に退避する。
    """
    nodes = list(rel.get("nodes", []))
    edges = list(rel.get("edges", []))
    ids = [nd.get("id", "") for nd in nodes]
    idset = set(ids)
    vb_w, vb_h = 1040, 600

    deg: dict[str, int] = {}
    for e in edges:
        deg[e.get("from", "")] = deg.get(e.get("from", ""), 0) + 1
        deg[e.get("to", "")] = deg.get(e.get("to", ""), 0) + 1

    def _node_r(i: str) -> float:
        return min(40 + deg.get(i, 1) * 8, 62)

    max_r = max((_node_r(i) for i in ids), default=50)

    def _valid(e: dict[str, Any]) -> bool:
        return e.get("from") in idset and e.get("to") in idset

    # frenemy (協調的競合) も「左右に分かれて対峙」する点では rivalry と同じ配置にし、
    # ただし協力面 (緑線) も併走させる。配置のためここでは rival 扱いにする。
    rival = [e for e in edges if _valid(e)
             and (e.get("kind") in _RIVAL_KINDS or e.get("kind") in _FRENEMY_KINDS)]
    auth = [e for e in edges if _valid(e) and e.get("kind") in _AUTH_KINDS]
    coop = [e for e in edges if _valid(e) and e.get("kind") in _COOP_KINDS]
    # node.place == "center" は強制的に中央列へ (両陣営が奪い合う対象=発注企業/市場 等)。
    forced_center = {nd.get("id", "") for nd in nodes if nd.get("place") == "center"}

    # 1) 競合/協調的競合を左右に二分 (rivalry サブグラフを 2-color)
    side: dict[str, str] = {}
    radj: dict[str, list[str]] = {}
    for e in rival:
        radj.setdefault(e["from"], []).append(e["to"])
        radj.setdefault(e["to"], []).append(e["from"])
    for start in radj:
        if start in side:
            continue
        side[start] = "L"
        stack = [start]
        while stack:
            u = stack.pop()
            for v in radj.get(u, []):
                if v not in side:
                    side[v] = "R" if side[u] == "L" else "L"
                    stack.append(v)

    # 2) 規制当局 (規制エッジの source) と強制中央ノードは中央列へ
    center: set[str] = {e["from"] for e in auth} | forced_center
    for c in center:
        side.pop(c, None)

    # 3) 協力 (出資/提携/供給) は相手と同じ側に寄せる (伝播)
    changed = True
    while changed:
        changed = False
        for e in coop:
            a, b = e["from"], e["to"]
            if a in side and b not in side and b not in center:
                side[b] = side[a]
                changed = True
            elif b in side and a not in side and a not in center:
                side[a] = side[b]
                changed = True

    # 4) 未割当は中央列へ退避
    for i in ids:
        if i not in side and i not in center:
            center.add(i)

    # 5) 列 → 座標。rival-core を同じ baseline に揃え、協力は上下に展開。
    cols: dict[str, list[str]] = {"L": [], "C": [], "R": []}
    for i in ids:
        cols["C" if i in center else side[i]].append(i)
    col_x = {"L": vb_w * 0.19, "C": vb_w * 0.50, "R": vb_w * 0.81}
    rival_core = {i for e in rival for i in (e["from"], e["to"])}
    baseline = vb_h * 0.44
    slot = vb_h * 0.30
    pos: dict[str, tuple[float, float]] = {}
    for col, members in cols.items():
        if not members:
            continue
        order = _coop_order(members, coop)
        if col == "C":
            # 規制当局は下揃え、その他はその上に積む。
            for k, i in enumerate(reversed(order)):
                pos[i] = (col_x[col], vb_h * 0.82 - k * slot)
        else:
            anchor = next((k for k, i in enumerate(order) if i in rival_core),
                          len(order) // 2)
            for k, i in enumerate(order):
                pos[i] = (col_x[col], baseline + (k - anchor) * slot)

    placed = []
    for nd in nodes:
        i = nd.get("id", "")
        x, y = pos.get(i, (vb_w / 2, vb_h / 2))
        y = min(max(y, max_r + 12), vb_h - max_r - 12)  # 枠内クランプ
        placed.append({**nd, "x": round(x, 1), "y": round(y, 1),
                       "r": _node_r(i), "deg": deg.get(i, 1)})
    layout = dict(rel)
    layout["nodes"] = placed
    layout["vb_w"], layout["vb_h"] = vb_w, vb_h
    # 凡例 = 実際に登場した kind のみ。frenemy は協力(緑)+競合(赤)の 2 面に展開する。
    legend: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add_legend(kind: str, color: str) -> None:
        if kind and kind not in seen:
            seen.add(kind)
            legend.append({"kind": kind, "color": color, "dash": False})

    for e in edges:
        k = e.get("kind", "")
        if not k:
            continue
        if k in _FRENEMY_KINDS:
            _add_legend("提携", _FRENEMY_COOP_COLOR)
            _add_legend("競合", _FRENEMY_RIVAL_COLOR)
        else:
            ks = EDGE_KINDS.get(k, _DEFAULT_EDGE)
            _add_legend(k, ks["color"])
    layout["legend"] = legend
    return layout


def _node_font(label: str, r: float, max_fs: float) -> float:
    """ノード円 (直径 2r) に label が収まる font-size を求める。"""
    # Inter 900 のキャップ幅 ≈ 0.62em (ascii) / CJK ≈ 1.0em。
    width1 = _text_w(label, 1.0, ascii_factor=0.62) or 1.0
    fit = (2 * r - 16) / width1
    return max(11.0, min(max_fs, fit))


def _esc(s: Any) -> str:
    return _html.escape(str(s), quote=True)


# ── 色・テキスト幅ユーティリティ ──────────────────────────────────────────────

def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def rgba(h: str, a: float) -> str:
    r, g, b = _hex_to_rgb(h)
    return f"rgba({r},{g},{b},{a})"


def _is_cjk(ch: str) -> bool:
    return ord(ch) > 0x2E7F  # CJK 統合漢字・かな・全角記号は実幅が広い


def _text_w(s: str, fs: float, ascii_factor: float = 0.6) -> float:
    """CJK 全角=1em・半角=ascii_factor em で概算したテキスト描画幅 (px)。

    JetBrains Mono 等の等幅でも CJK は全角フォントにフォールバックして約 1em 幅に
    なるため、全角を半角扱いで見積もるとラベルが矩形からはみ出す (2026-05-31 修正)。
    """
    return sum(fs * (1.0 if _is_cjk(ch) else ascii_factor) for ch in s)


def resolve_accent(lens_id: str) -> str:
    """lens (カテゴリ id) からアクセント色を引く。未知なら near-black に退避。"""
    cat = CATEGORIES.get((lens_id or "").lower())
    return cat["accent"] if cat else INK


def relations_svg(rel: dict[str, Any]) -> str:
    """relations を SVG ネットワーク図 (ノード円 + ラベル付き有向エッジ) に描く。"""
    lay = layout_relations(rel)
    nodes = lay["nodes"]
    by_id = {nd["id"]: nd for nd in nodes if "id" in nd}
    vb_w, vb_h = lay["vb_w"], lay["vb_h"]
    parts: list[str] = [
        f'<svg viewBox="0 0 {vb_w} {vb_h}" width="100%" '
        f'style="display:block;background:{PAPER}" role="img" '
        f'aria-label="{_esc(rel.get("title", "当事者の関係図"))}">'
    ]

    fs = 12.5
    chip_h = 26.0

    def _label_chip(gx: float, gy: float, chip_w: float,
                    color: str, kind: str, label: str) -> str:
        return (
            f'<g transform="translate({gx:.1f},{gy:.1f})">'
            f'<rect width="{chip_w:.1f}" height="{chip_h}" fill="#fff" stroke="{color}" '
            f'stroke-width="1" rx="2"/>'
            f'<circle cx="13" cy="{chip_h / 2}" r="3.5" fill="{color}"/>'
            f'<text x="24" y="{chip_h / 2 + 4:.0f}" font-family="\'JetBrains Mono\',monospace" '
            f'font-size="{fs:.0f}" font-weight="600" fill="{INK}">'
            f'<tspan font-weight="700" fill="{color}">{_esc(kind)}</tspan> {_esc(label)}'
            f'</text></g>'
        )

    # ── edges: 線+矢印を先に全部描画。ラベルは (from, kind, label) で束ねる ──
    groups: dict[tuple[Any, str, str], dict[str, Any]] = {}
    for i, e in enumerate(rel.get("edges", [])):
        a, b = by_id.get(e.get("from")), by_id.get(e.get("to"))
        if not a or not b:
            continue
        # frenemy (協調的競合): 協力線(緑・上)と競合線(赤・下)を併走させ、各々に
        # 双方向矢印とラベルを付ける。「提携でありつつ人月モデルで競合」の二面性を
        # 1 本に潰さず描く中心命題用エッジ (2026-05-31 追加)。
        if e.get("kind") in _FRENEMY_KINDS:
            ax, ay, bx, by = a["x"], a["y"], b["x"], b["y"]
            dx, dy = bx - ax, by - ay
            length = math.hypot(dx, dy) or 1.0
            ux, uy = dx / length, dy / length
            px, py = -uy, ux
            gap, off = 7, 15
            faces = (
                (-1, _FRENEMY_COOP_COLOR, "提携", e.get("coop") or "協力（提携）"),
                (1, _FRENEMY_RIVAL_COLOR, "競合", e.get("rival") or "人月モデルで競合"),
            )
            for sign, color, kind, label in faces:
                ox, oy = px * off * sign, py * off * sign
                x1, y1 = ax + ux * (a["r"] + gap) + ox, ay + uy * (a["r"] + gap) + oy
                x2, y2 = bx - ux * (b["r"] + gap) + ox, by - uy * (b["r"] + gap) + oy
                parts.append(
                    f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                    f'stroke="{color}" stroke-width="2.4" opacity="0.9"/>'
                )
                parts.append(_arrow(x2, y2, ux, uy, color))   # 協力も競合も相互 = 双方向
                parts.append(_arrow(x1, y1, -ux, -uy, color))
                cw = 24 + _text_w(kind, fs) + (6 if label else 0) + _text_w(label, fs) + 14
                lx = (x1 + x2) / 2 + px * sign * (chip_h / 2 + 5)
                ly = (y1 + y2) / 2 + py * sign * (chip_h / 2 + 5)
                gx = min(max(lx - cw / 2, 4), vb_w - cw - 4)
                gy = min(max(ly - chip_h / 2, 4), vb_h - chip_h - 4)
                parts.append(_label_chip(gx, gy, cw, color, kind, label))
            continue
        ks = EDGE_KINDS.get(e.get("kind", ""), _DEFAULT_EDGE)
        color, dash = ks["color"], ks["dash"]
        ax, ay, bx, by = a["x"], a["y"], b["x"], b["y"]
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy) or 1.0
        ux, uy = dx / length, dy / length
        gap = 7
        x1, y1 = ax + ux * (a["r"] + gap), ay + uy * (a["r"] + gap)
        x2, y2 = bx - ux * (b["r"] + gap), by - uy * (b["r"] + gap)
        dash_attr = ' stroke-dasharray="6 5"' if dash else ""
        parts.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{color}" stroke-width="2" opacity="0.85"{dash_attr}/>'
        )
        parts.append(_arrow(x2, y2, ux, uy, color))
        # 競合/対立 は相互関係なので既定で双方向矢印 (⇔)。一方向に倒したいときだけ
        # dir に "one"/"forward" を明示する。出資/規制/供給は本来一方向 (親→子・
        # 当局→対象・供給元→供給先) なので片矢印のまま (2026-05-31)。
        bidirectional = e.get("dir") == "both" or (
            e.get("kind") in _RIVAL_KINDS and e.get("dir") not in ("one", "forward")
        )
        if bidirectional:
            parts.append(_arrow(x1, y1, -ux, -uy, color))
        # 同一 (from, kind, label) のエッジ群はラベルを 1 回だけ描く。当局→複数勢力で
        # 同文ラベルが 2 本並ぶと認知負荷を上げるだけなので束ねる (2026-05-31)。
        kind, label = e.get("kind", ""), e.get("label", "")
        g = groups.setdefault(
            (e.get("from"), kind, label),
            {"color": color, "kind": kind, "label": label,
             "mids": [], "idx": i, "px": -uy, "py": ux, "seg": (x1, y1, x2, y2)},
        )
        g["mids"].append(((x1 + x2) / 2, (y1 + y2) / 2))

    # ── ラベルチップ (束ねた単位で 1 個) ──
    for g in groups.values():
        kind, label, color = g["kind"], g["label"], g["color"]
        text_w = 24 + _text_w(kind, fs) + (6 if label else 0) + _text_w(label, fs)
        chip_w = text_w + 14
        mids = g["mids"]
        if len(mids) >= 2:
            # 複数辺で同一ラベル → 各中点の重心に 1 個 (扇状エッジの内側に収まる)
            lx = sum(m[0] for m in mids) / len(mids)
            ly = sum(m[1] for m in mids) / len(mids)
        else:
            # 単独辺 → 辺上でスタガリングし他ラベルとの衝突を避ける
            x1, y1, x2, y2 = g["seg"]
            t = (0.5, 0.4, 0.6)[g["idx"] % 3]
            perp = (0, -1, 1)[g["idx"] % 3] * (chip_h + 4)
            lx = x1 + (x2 - x1) * t + g["px"] * perp
            ly = y1 + (y2 - y1) * t + g["py"] * perp
        gx = min(max(lx - chip_w / 2, 4), vb_w - chip_w - 4)
        gy = min(max(ly - chip_h / 2, 4), vb_h - chip_h - 4)
        parts.append(_label_chip(gx, gy, chip_w, color, kind, label))

    # ── nodes (テキストは円内に収まるようフォントを自動縮小) ──
    for nd in nodes:
        x, y, r = nd["x"], nd["y"], nd["r"]
        group = str(nd.get("group", ""))
        # 規制ノード (group に "規制"/"当局" を含む) は破線円で差別化
        is_reg = ("規制" in group) or ("当局" in group)
        sw = 2.5 if nd.get("deg", 1) >= 2 else 1.5
        dash_attr = ' stroke-dasharray="5 4"' if is_reg else ""
        label = str(nd.get("label", ""))
        has_sub = bool(group)
        lfs = _node_font(label, r, 24 if r >= 50 else 18)
        parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="#fff" stroke="{INK}" '
            f'stroke-width="{sw}"{dash_attr}/>'
        )
        parts.append(
            f'<text x="{x:.1f}" y="{y - (4 if has_sub else -lfs * 0.34):.1f}" text-anchor="middle" '
            f'font-family="\'Inter\',sans-serif" font-weight="900" '
            f'font-size="{lfs:.1f}" fill="{INK}" letter-spacing="-0.5">{_esc(label)}</text>'
        )
        if has_sub:
            # サブラベルも円内に収まる範囲で。長い group は省略。
            sfs = 10.5
            max_chars = int((2 * r - 12) / (sfs * 0.95))
            sub = group if len(group) <= max_chars else group[: max(1, max_chars - 1)] + "…"
            parts.append(
                f'<text x="{x:.1f}" y="{y + lfs * 0.62 + 4:.1f}" text-anchor="middle" '
                f'font-family="\'Noto Serif JP\',serif" font-size="{sfs}" '
                f'fill="{DIM}">{_esc(sub)}</text>'
            )
    parts.append("</svg>")
    return "".join(parts)


def _arrow(x: float, y: float, ux: float, uy: float, color: str) -> str:
    s = 9
    bx, by = x - ux * s, y - uy * s
    px, py = -uy, ux
    p1 = f"{x:.1f},{y:.1f}"
    p2 = f"{bx + px * s * 0.5:.1f},{by + py * s * 0.5:.1f}"
    p3 = f"{bx - px * s * 0.5:.1f},{by - py * s * 0.5:.1f}"
    return f'<polygon points="{p1} {p2} {p3}" fill="{color}"/>'


# ── chart: bar / stacked_bar / line の SVG ────────────────────────────────────

_EXTRA_SERIES_COLORS = ["#2D5BB8", "#2E6B52"]


def _series_colors(accent: str) -> list[str]:
    """系列色: 1 本目はアクセント (= カテゴリ色)、以降は gold→青→緑。"""
    return [accent, GOLD, *_EXTRA_SERIES_COLORS]


def chart_svg(chart: dict[str, Any], accent: str = INK) -> str:
    """chart ブロックを SVG (棒 / 積み上げ棒 / 折れ線) に描く。

    スキーマは series:[{name,data}] (複数系列可)。単一系列なら凡例を出さない。
    1 本目の系列色はアクセント (カテゴリ色) を使う。
    """
    colors = _series_colors(accent)
    ctype = chart.get("type", "bar")
    cats = chart.get("categories", [])
    series = chart.get("series", [])
    if not series and "data" in chart:  # デザイン sample (単一 data 配列) も許容
        series = [{"name": chart.get("title", ""), "data": chart["data"]}]
    if not cats or not series:
        return ""

    vb_w, vb_h = 560, 270
    pad_l, pad_r, pad_t, pad_b = 16, 16, 40, 56
    plot_w = vb_w - pad_l - pad_r
    plot_h = vb_h - pad_t - pad_b
    n = len(cats)
    slot = plot_w / n

    # スケール最大値
    if ctype == "stacked_bar":
        totals = [sum(s["data"][i] for s in series if i < len(s["data"])) for i in range(n)]
        vmax = max(totals) * 1.18 if totals else 1
    else:
        flat = [v for s in series for v in s["data"]]
        vmax = (max(flat) * 1.18) if flat else 1
    vmax = vmax or 1

    parts: list[str] = [
        f'<svg viewBox="0 0 {vb_w} {vb_h}" width="100%" '
        f'style="display:block;background:#fff" role="img" '
        f'aria-label="{_esc(chart.get("title", "チャート"))}">'
    ]
    base_y = pad_t + plot_h
    parts.append(
        f'<line x1="{pad_l}" y1="{base_y}" x2="{vb_w - pad_r}" y2="{base_y}" '
        f'stroke="{BORDER}" stroke-width="1.5"/>'
    )
    if chart.get("unit"):
        parts.append(
            f'<text x="{pad_l}" y="{pad_t - 18}" font-family="\'JetBrains Mono\',monospace" '
            f'font-size="10" font-weight="700" letter-spacing="1" fill="{SOFT}">'
            f'{_esc(chart["unit"])}</text>'
        )

    if ctype == "line":
        parts.extend(_chart_line(series, cats, vmax, pad_l, base_y, plot_h, slot, colors))
    elif ctype == "stacked_bar":
        parts.extend(_chart_stacked(series, cats, vmax, pad_l, base_y, plot_h, slot, colors))
    else:  # bar (単一 or グルーピング)
        parts.extend(_chart_bar(series, cats, vmax, pad_l, base_y, plot_h, slot, colors))

    # カテゴリラベル
    for i, c in enumerate(cats):
        cxp = pad_l + slot * i + slot / 2
        parts.append(
            f'<text x="{cxp:.1f}" y="{base_y + 20}" text-anchor="middle" '
            f'font-family="\'JetBrains Mono\',monospace" font-size="10.5" '
            f'font-weight="600" fill="{DIM}">{_esc(c)}</text>'
        )

    # annotations: [{label, at}] → 該当カテゴリ下に注記
    for ann in chart.get("annotations", []):
        at = ann.get("at")
        if at in cats:
            i = cats.index(at)
            cxp = pad_l + slot * i + slot / 2
            parts.append(
                f'<text x="{cxp:.1f}" y="{base_y + 36}" text-anchor="middle" '
                f'font-family="\'JetBrains Mono\',monospace" font-size="9" '
                f'fill="#9A8F70">▲ {_esc(ann.get("label", ""))}</text>'
            )
    parts.append("</svg>")
    legend = _chart_legend(series, colors) if len(series) > 1 else ""
    return "".join(parts) + legend


def _bar_value_label(x: float, y: float, v: Any) -> str:
    return (
        f'<text x="{x:.1f}" y="{y - 8:.1f}" text-anchor="middle" '
        f'font-family="\'Inter\',sans-serif" font-weight="900" font-size="17" '
        f'fill="{INK}">{_esc(v)}</text>'
    )


def _chart_bar(series, cats, vmax, pad_l, base_y, plot_h, slot, colors):
    parts = []
    ns = len(series)
    group_w = min(slot * 0.62, 120)
    bw = group_w / ns
    for i in range(len(cats)):
        gx = pad_l + slot * i + (slot - group_w) / 2
        for si, s in enumerate(series):
            v = s["data"][i] if i < len(s["data"]) else 0
            h = (v / vmax) * plot_h
            x = gx + bw * si
            y = base_y - h
            color = colors[si % len(colors)]
            parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{h:.1f}" fill="{color}"/>')
            if ns <= 2:
                parts.append(_bar_value_label(x + bw / 2, y, v))
    return parts


def _chart_stacked(series, cats, vmax, pad_l, base_y, plot_h, slot, colors):
    parts = []
    bw = min(slot * 0.5, 90)
    for i in range(len(cats)):
        x = pad_l + slot * i + (slot - bw) / 2
        acc = 0.0
        for si, s in enumerate(series):
            v = s["data"][i] if i < len(s["data"]) else 0
            h = (v / vmax) * plot_h
            y = base_y - acc - h
            color = colors[si % len(colors)]
            parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{h:.1f}" fill="{color}"/>')
            acc += h
        total = sum(s["data"][i] for s in series if i < len(s["data"]))
        parts.append(_bar_value_label(x + bw / 2, base_y - acc, total))
    return parts


def _chart_line(series, cats, vmax, pad_l, base_y, plot_h, slot, colors):
    parts = []
    n = len(cats)

    def px(i):
        return pad_l + slot * i + slot / 2

    def py(v):
        return base_y - (v / vmax) * plot_h

    for si, s in enumerate(series):
        color = colors[si % len(colors)]
        pts = " ".join(f"{px(i):.1f},{py(s['data'][i]):.1f}" for i in range(min(n, len(s["data"]))))
        parts.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2.5"/>')
        for i in range(min(n, len(s["data"]))):
            v = s["data"][i]
            parts.append(f'<circle cx="{px(i):.1f}" cy="{py(v):.1f}" r="4" fill="{color}"/>')
            parts.append(_bar_value_label(px(i), py(v), v))
    return parts


def _chart_legend(series, colors) -> str:
    items = []
    for si, s in enumerate(series):
        color = colors[si % len(colors)]
        items.append(
            f'<span class="dd-chart-legend__item"><span class="dd-chart-legend__sw" '
            f'style="background:{color}"></span>{_esc(s.get("name", ""))}</span>'
        )
    return f'<div class="dd-chart-legend">{"".join(items)}</div>'


# ── table: 未確認セル判定 ─────────────────────────────────────────────────────

def _is_unconfirmed(cell: str) -> bool:
    return any(tok in cell for tok in _UNCONFIRMED_TOKENS)


def build_table(table: dict[str, Any]) -> dict[str, Any]:
    """table ブロックを描画用に整形 (未確認セルのフラグ付け)。"""
    columns = table.get("columns", [])
    rows_out = []
    for row in table.get("rows", []):
        cells = []
        for ci, val in enumerate(row):
            text = "" if val is None else str(val)
            cells.append({
                "text": text,
                "unconfirmed": _is_unconfirmed(text),
                "is_head": ci == 0,
                "is_source": ci == len(columns) - 1 and len(columns) >= 3,
            })
        rows_out.append(cells)
    return {
        "title": table.get("title", ""),
        "columns": columns,
        "rows": rows_out,
        "source": table.get("source", ""),
    }


# ── context builder ───────────────────────────────────────────────────────────

class DeepDiveIncompleteError(ValueError):
    """DeepDive md が必須ブロック (関係図/変遷チャート/データ表 等) を欠く = 未完成記事。

    weekly-research-system.md は relations を「必須」と 3 箇所で明記しているが、
    2026-05-31 記事は relations 無しのまま生成・公開された。プロンプトに「必須」と
    書くだけの防御 (記憶/指示頼み) は破れる。feedback_check_design_principles に従い、
    その最弱の層を **ビルド時の loud failure** という構造ガードに格上げし、関係図や
    データ表を欠いた未完成記事がサイレントに公開されるのを封じる。
    """


# weekly-research-system.md「出力スキーマ早見」の必須ブロックと一致させる。1 つでも
# 欠けたら未完成扱い (背景=timeline/players/relations・深掘り=chart/table・注目点=decision)。
_MANDATORY_BLOCKS: tuple[str, ...] = (
    "timeline", "players", "relations", "chart", "table", "decision",
)
# 深掘りの図表 (chart) の最低本数。1 つでは論点を多面的に示せない (2026-05-31 ユーザー指示)。
_MIN_CHARTS = 2


def _require_blocks(md_path: Path, blocks: dict[str, list[Any]]) -> None:
    """必須ブロックの欠落を loud に弾く (= サイレントな空描画を許さない)。"""
    name = Path(md_path).name
    missing = [b for b in _MANDATORY_BLOCKS if not blocks.get(b)]
    if missing:
        raise DeepDiveIncompleteError(
            f"{name}: 必須ブロック欠落 {missing}。"
            "timeline/players/relations/chart/table/decision は全て必須 "
            "(weekly-research-system.md 出力スキーマ早見)。関係図・変遷チャート・"
            "データ表が欠けた記事は未完成として公開しない。"
        )
    # 深掘りの図表 (chart) は最低 2 本。1 つでは論点を多面的に示せない
    # (2026-05-31 ユーザー指示で恒久化)。
    n_chart = len(blocks.get("chart", []))
    if n_chart < _MIN_CHARTS:
        raise DeepDiveIncompleteError(
            f"{name}: 深掘りの chart が {n_chart} 本。最低 {_MIN_CHARTS} 本必要 "
            "(図表 1 つでは論点を多面的に示せない)。異なる切り口の図を 2 本以上置く。"
        )


def build_deepdive_context(md_path: Path) -> dict[str, Any]:
    """DeepDive md 1 件から Jinja テンプレ用 context を組み立てる。"""
    text = Path(md_path).read_text(encoding="utf-8")
    fm, body = parse_frontmatter(text)
    blocks = extract_blocks(body)
    _require_blocks(md_path, blocks)  # 必須ブロック欠落は hard fail (未完成記事を公開しない)
    sections = split_sections(body)

    date_str = fm.get("date", "")
    issue = fm.get("issue", "") or date_str.replace("-", "")
    title = fm.get("title", "")
    theme = fm.get("theme", "")
    tags = _parse_tags(text)

    canonical = f"{BASE_URL}/deepdive/{date_str}/"
    og_image = _absolutize(fm.get("og_image", "") or "")

    # 各節の散文。背景/深掘り/注目点/要約。
    bg = sections.get("背景", "")
    di = sections.get("深掘り", "")
    watch = sections.get("注目点", "")
    summary_section = sections.get("要約", "")
    refs_section = sections.get("参考リンク", "")

    relations = blocks.get("relations", [None])[0]
    charts = blocks.get("chart", [])
    table = blocks.get("table", [None])[0]
    decision = blocks.get("decision", [None])[0]
    if decision:
        # decider を箇条書き用に正規化 (配列はそのまま / 文字列は「、」区切り)。
        # 決定者が複数いるときはテンプレが必ず <ul> で出す (2026-05-31 ユーザー指示)。
        _dec = decision.get("decider", "")
        _dl = ([str(x).strip() for x in _dec if str(x).strip()] if isinstance(_dec, list)
               else [s.strip() for s in re.split(r"[、,]", str(_dec)) if s.strip()])
        decision = {**decision, "decider_list": _dl}

    read_min = max(5, round(len(body) / 900))

    # アクセント = テーマのカテゴリ (lens) 色。frontmatter `lens:` を一次に、無ければ
    # tags からカテゴリ id を推定、それも不能なら near-black に退避。
    lens_id = _resolve_lens(fm, tags)
    lens = CATEGORIES.get(lens_id)
    accent = resolve_accent(lens_id)
    # 3 階層強調チップ等で使うアクセント由来の淡色 (CSS 変数に流し込む)。
    accent_chip_bg = rgba(accent, 0.12)
    accent_chip_line = rgba(accent, 0.22)
    accent_underline = rgba(accent, 0.55)

    return {
        # meta / OGP
        "title": title,
        "date": date_str,
        "date_dot": date_str.replace("-", "."),
        "issue": issue,
        "theme": theme,
        "tags": tags,
        "read_min": read_min,
        "canonical": canonical,
        "og_title": title,
        "og_description": theme[:180],
        "og_image": og_image,
        "og_url": canonical,
        "base_url": BASE_URL,
        "site_title": SITE_TITLE,
        # tokens (テンプレの inline style から参照)
        "ink": INK, "gold": GOLD, "cream": CREAM, "paper": PAPER, "border": BORDER,
        # accent (= カテゴリ色)。テンプレ CSS 変数 + chart SVG が参照。
        "accent": accent,
        "accent_chip_bg": accent_chip_bg,
        "accent_chip_line": accent_chip_line,
        "accent_underline": accent_underline,
        # lens chip (hero のカテゴリ表示)。lens 不明なら None。
        "lens_id": lens_id,
        "lens_name_en": (lens["label"].upper() if lens else ""),
        "lens_name_jp": (lens["jp"] if lens else ""),
        "lens_glyph": (lens["glyph"] if lens else ""),
        # blocks
        "timeline": blocks.get("timeline", [None])[0] or [],
        "players": blocks.get("players", [None])[0] or [],
        "relations_svg": relations_svg(relations) if relations else "",
        "relations_legend": layout_relations(relations).get("legend", []) if relations else [],
        "relations_title": (relations or {}).get("title", "当事者の関係図"),
        "relations_source": (relations or {}).get("source", ""),
        "charts": [
            {
                "title": c.get("title", ""),
                "note": _chart_note(c),
                "source": c.get("source", ""),
                "svg": chart_svg(c, accent),
            }
            for c in charts
        ],
        "table": build_table(table) if table else None,
        "decision": decision,
        # prose
        "bg_prose": _prose_paragraphs(bg),
        "di_prose": _prose_paragraphs(di),
        "watch_prose": _prose_paragraphs(watch),
        "summary_prose": " ".join(_prose_paragraphs(summary_section)),
        "sources": parse_sources(refs_section),
    }


def _chart_note(chart: dict[str, Any]) -> str:
    for ann in chart.get("annotations", []):
        if ann.get("label"):
            return ann["label"]
    return ""


# tags / 和名 → カテゴリ id 推定 (lens フィールドが無い digest 向けの保険)。
_JP_TO_CID = {meta["jp"]: cid for cid, meta in CATEGORIES.items() if cid != "summary"}


def _resolve_lens(fm: dict[str, str], tags: list[str]) -> str:
    """テーマのカテゴリ (lens) id を決める。

    優先順: frontmatter `lens` (確定値) > tags 中のカテゴリ id / 和名一致 > 空。
    空ならアクセントは near-black に退避する (resolve_accent)。
    """
    lens = (fm.get("lens") or "").lower()
    if lens in CATEGORIES and lens != "summary":
        return lens
    for t in tags:
        tl = t.lower()
        if tl in CATEGORIES and tl != "summary":
            return tl
        if t in _JP_TO_CID:
            return _JP_TO_CID[t]
    return ""


_TAGS_RE = re.compile(r'^tags:\s*\[(.*?)\]\s*$', re.MULTILINE)


def _parse_tags(text: str) -> list[str]:
    m = _TAGS_RE.search(text)
    if not m:
        return []
    return [t.strip().strip('"').strip("'") for t in m.group(1).split(",") if t.strip()]


# ── build (Option B: 独立レンダーパス) ────────────────────────────────────────

def build_deepdive_pages(
    *, docs_root: Path | None = None, full: bool = False,
    digest_dir: Path | None = None,
) -> list[Path]:
    """digest/DeepDive/*.md を docs/deepdive/{date}/index.html に全 render。"""
    docs = Path(docs_root) if docs_root else (_PKG_ROOT / "docs")
    src_dir = Path(digest_dir) if digest_dir else (_PKG_ROOT / "digest" / "DeepDive")
    if not src_dir.exists():
        return []
    written: list[Path] = []
    tmpl_mtime = _templates_mtime()  # テンプレ変更も増分判定に含める (generate_pages と同一境界)
    for src in sorted(src_dir.glob("*.md")):
        try:
            ctx = build_deepdive_context(src)
        except DeepDiveIncompleteError:
            raise  # 必須ブロック欠落は握りつぶさず伝播 (= 未完成記事の公開を構造的に阻止)
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] DeepDive context 構築失敗 {src.name}: {exc}", file=sys.stderr)
            continue
        if not ctx.get("date"):
            print(f"[skip] DeepDive date 欠落: {src.name}", file=sys.stderr)
            continue
        out = docs / "deepdive" / ctx["date"] / "index.html"
        if not full and not _needs_rebuild(src, out, tmpl_mtime):
            continue
        render_page(ctx, out, template_name="deepdive-template.html")
        written.append(out)
    return written


# ── テーマ書架 (DeepDive 連載インデックス) ────────────────────────────────────
# 日付 digest の date アーカイブ (docs/archive/) とは別系統。テーマ単位で深掘りだけを
# 時系列に束ね、レンズ (カテゴリ) 絞り込み + 全文検索できる読み物の書架。受領デザイン
# deepdive-ia.jsx ThemeArchiveIndex に準拠。出力は docs/deepdive/index.html。

_MONTHS_EN = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
              "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
# レンズチップ/タグの短縮 EN 表記 (受領 IA_CATS の en と一致: fx→FX … game→GAMING)。
_LENS_CODE = {"fx": "FX", "ai": "AI", "it": "IT",
              "mobility": "MOBILITY", "economy": "ECONOMY", "game": "GAMING"}


def _archive_item(md_path: Path) -> dict[str, Any] | None:
    """DeepDive md 1 件から書架の 1 行分メタデータを軽量抽出する。

    書架は一覧なので本文ブロックは描画しない (= 必須ブロックガードは通さない)。
    read_min は本文長から自動算出 (build_deepdive_context と同式)。
    """
    text = Path(md_path).read_text(encoding="utf-8")
    fm, body = parse_frontmatter(text)
    date_str = fm.get("date", "")
    if not date_str:
        return None
    tags = _parse_tags(text)
    lens_id = _resolve_lens(fm, tags)
    lens = CATEGORIES.get(lens_id)
    title = fm.get("title", "")
    theme = fm.get("theme", "")
    return {
        "date": date_str,
        "date_dot": date_str[5:].replace("-", "."),  # MM.DD
        "year": date_str[:4],
        "issue": fm.get("issue", "") or date_str.replace("-", ""),
        "title": title,
        "url": f"{BASE_URL}/deepdive/{date_str}/",
        "read_min": max(5, round(len(body) / 900)),
        "lens_id": lens_id,
        "lens_code": _LENS_CODE.get(lens_id, (lens["label"].upper() if lens else "")),
        "lens_glyph": (lens["glyph"] if lens else "❖"),
        "lens_accent": resolve_accent(lens_id),
        # 検索対象 = タイトル + テーマ + tags (固有名詞も拾えるように)。
        "search": " ".join([title, theme, *tags]),
    }


def collect_archive_items(*, digest_dir: Path | None = None) -> dict[str, Any]:
    """digest/DeepDive/*.md を束ね、アーカイブ DEEP DIVE ビュー用の items + chips を返す。

    旧テーマ書架と日付アーカイブ (/archive/) の DEEP DIVE スライドが共有する単一の
    収集経路 (= 境界 1 箇所に集約)。md が 1 件も無ければ items を空にして返す。
    """
    src_dir = Path(digest_dir) if digest_dir else (_PKG_ROOT / "digest" / "DeepDive")
    empty: dict[str, Any] = {"items": [], "chips": [], "theme_count": 0,
                             "month_label": "", "latest_url": ""}
    if not src_dir.exists():
        return empty
    items = [it for it in (_archive_item(p) for p in src_dir.glob("*.md")) if it]
    if not items:
        return empty
    items.sort(key=lambda it: it["date"], reverse=True)  # 新しい号が上
    items[0]["current"] = True                            # 最新 = ❖ TODAY 強調
    latest = items[0]
    mm = latest["date"][5:7]
    month_label = f"{_MONTHS_EN[int(mm) - 1]} {latest['year']}"
    # レンズチップ (ALL + 6 カテゴリ)。summary 疑似カテゴリは除外。
    chips = [
        {"id": cid, "code": _LENS_CODE.get(cid, meta["label"].upper()),
         "glyph": meta["glyph"], "accent": meta["accent"]}
        for cid, meta in CATEGORIES.items() if cid != "summary"
    ]
    return {"items": items, "chips": chips, "theme_count": len(items),
            "month_label": month_label, "latest_url": latest["url"]}


def build_deepdive_archive(*, docs_root: Path | None = None,
                           digest_dir: Path | None = None) -> Path | None:
    """旧テーマ書架 /deepdive/ は日付アーカイブ /archive/?view=deepdive に一本化済み。

    既存ブックマーク/被リンク保護のため 404 にせず、meta refresh + canonical の
    リダイレクトページを docs/deepdive/index.html に出力する。個別記事
    /deepdive/{date}/ は build_deepdive_pages がそのまま生成し維持する。
    digest_dir は後方互換のため受けるが未使用 (収集は collect_archive_items 側)。
    """
    docs = Path(docs_root) if docs_root else (_PKG_ROOT / "docs")
    target = f"{BASE_URL}/archive/?view=deepdive"
    html = (
        '<!doctype html>\n<html lang="ja">\n<head>\n<meta charset="utf-8">\n'
        f'<meta http-equiv="refresh" content="0; url={target}">\n'
        f'<link rel="canonical" href="{target}">\n'
        '<meta name="robots" content="noindex">\n'
        '<title>テーマ書架は日付アーカイブに統合されました</title>\n'
        f'<script>location.replace({target!r});</script>\n'
        '</head>\n<body>\n'
        f'<p>テーマ書架は <a href="{target}">日付アーカイブ (DEEP DIVE)</a> に統合されました。</p>\n'
        '</body>\n</html>\n'
    )
    out = docs / "deepdive" / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8", newline="\n")
    return out


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="News-Grasp DeepDive ページ生成")
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--docs-root", type=Path, default=None)
    a = ap.parse_args()
    paths = build_deepdive_pages(docs_root=a.docs_root, full=a.full)
    print(f"wrote {len(paths)} DeepDive page(s)")
    for p in paths:
        print(f"  - {p}")
    arch = build_deepdive_archive(docs_root=a.docs_root)
    print(f"wrote theme archive: {arch}")
