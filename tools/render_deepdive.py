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
import hashlib
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
from tools.deepdive_content import strip_internal_metadata  # noqa: E402
from tools.generate_pages import (  # noqa: E402
    _absolutize,
    _needs_rebuild,
    _templates_mtime,
    parse_frontmatter,
    render_page,
)
from tools.tts.deepdive_audio import deepdive_audio_for_pages  # noqa: E402
from tools.validate_deepdive_urls import (  # noqa: E402
    DeepDiveUrlError,
    require_live_urls,
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
    "提携": {"color": "#2E6B52", "dash": False},
    "出資": {"color": "#B8860B", "dash": False},
    "供給": {"color": "#2D5BB8", "dash": False},
    "競合": {"color": "#8E2A19", "dash": False},
    "対立": {"color": "#8E2A19", "dash": True},
    "規制": {"color": "#181C2A", "dash": False},
    "統制": {"color": "#5E3D8C", "dash": False},
    "依存": {"color": "#3A7B8C", "dash": True},
}

# edge kind の意味 (配置・矢印への効き方と構図規約の正典は _choose_layout_mode の docstring):
#   競合/対立 = 勢力の対立 / 出資・提携・供給 = 協力 / 規制 = 監督 /
#   統制/依存 = 上流から下流へ作用する一方向の関係。
_RIVAL_KINDS = {"競合", "対立"}
_AUTH_KINDS = {"規制"}
# 一方向 (→) のフロー・エッジ: 親→子 (出資)・供給元→先 (供給)・当局→対象 (規制)。
# 統制・依存も上流から下流へ読むため、専用の kind として同じ層化規則を適用する。
_FLOW_KINDS = {"出資", "供給", "規制", "統制", "依存"}

# 関連レポート (related) の種類 → バッジ色。関係図 EDGE_KINDS のトーンに揃え、News-Grasp
# 内で色の意味を一貫させる: 続報=基調 navy / 主役共有=提携の緑 / 波及=供給の青 / 対比=競合の赤。
_RELATION_STYLE: dict[str, str] = {
    "続報": "#181C2A",
    "主役共有": EDGE_KINDS["提携"]["color"],
    "波及": EDGE_KINDS["供給"]["color"],
    "対比": EDGE_KINDS["競合"]["color"],
}
_DEFAULT_RELATION_COLOR = DIM

# 「裏が取れていない」ことを示すセル値 (table の淡色化判定)。
_UNCONFIRMED_TOKENS = ("未確認", "未開示", "非開示")

# DeepDive 共通の og:image フォールバック (design/build_deepdive_og.py で生成)。
# 日次記事の resolve_og_image がカテゴリ別 assets/og/{cat}.jpg に退避するのと同じく、
# DeepDive は frontmatter に og_image が無ければ全号でこの 1 枚を共有する。空文字を
# _absolutize すると BASE_URL (サイト HTML) になり Discord 等が画像を出せないため、
# 必ず実画像へ退避させる (2026-06-01 サムネ欠落の構造対策)。
# 末尾の ?v=N はキャッシュバスター。Discord の media proxy は og:image を URL 単位で
# 長時間キャッシュし、同一パスでファイルを差し替えても古い画像を出し続けるため、画像の
# 見た目を更新したら N を上げて URL を変える (= proxy に別画像として再取得させる)。
_DEEPDIVE_OG_IMAGE = "assets/og/deepdive.jpg?v=2"

# fenced ブロック ```lang\n...\n```
_FENCED_RE = re.compile(r"^```([A-Za-z_]+)\r?\n(.*?)\r?\n```", re.DOTALL | re.MULTILINE)
# `## 見出し` セクション分割
_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
# 参考リンク bullet: `- 説明文: https://...`  (末尾 URL を拾う)
_SRC_URL_RE = re.compile(r"(https?://\S+)\s*$")
_SRC_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://\S+)\)\s*$")


# ── ブロック / 本文抽出 ───────────────────────────────────────────────────────

def extract_blocks(body: str) -> dict[str, list[Any]]:
    """本文中の全 fenced JSON ブロックを lang ごとに集約して返す。

    chart は複数許容なので値は常に list。壊れた JSON のブロックは握りつぶさず
    stderr に警告を出して skip する (捏造で埋めない・loud 寄り)。
    """
    out: dict[str, list[Any]] = {}
    for m in _FENCED_RE.finditer(body):
        lang, raw = m.group(1), m.group(2)
        if lang not in ("timeline", "players", "relations", "chart", "table", "decision", "related"):
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
    """セクションから公開可能な散文を段落 (空行区切り) のリストにする。

    `### §NN` はMarkdown側の構造化見出しであり、テンプレートへそのまま渡すと
    公開HTMLに記号が露出するため、共通の公開本文境界で除去する。
    """
    plain = strip_internal_metadata(_strip_fenced(section_text))
    paras = [p.strip() for p in re.split(r"\r?\n\s*\r?\n", plain)]
    return [p.replace("\r", "").replace("\n", " ").strip() for p in paras if p.strip()]


def parse_sources(section_text: str) -> list[dict[str, str]]:
    """`## 参考リンク` の bullet を {text, url, name, rest} のリストにする。

    name = メディア名 (「記事タイトル」より前)、rest = それ以降 (タイトル+日付)。テンプレ側で
    name をカテゴリ色に着色しメディアを認知しやすくする (rest は本文色のまま)。

    URL 無しの参考リンクは silent drop する (2026-06-04 ユーザー指摘の恒久対策)。理由:
    md に URL を書けないものを通すと、テンプレが <div> フォールバックでクリック不能な
    「押せない参考リンク」を出力し UX が破綻する。memory feedback_llm_url_fabrication_ban
    の「200 確認できない URL は採用しない」原則とも整合し、参考リンクは「全件クリック可能」
    を境界 1 箇所で保証する (feedback_check_design_principles 2 段)。drop は stderr に
    記録し、生成段階で URL を取り損なった事案が後段の参考リンク欠落で気付ける形にする。
    """
    sources: list[dict[str, str]] = []
    for line in section_text.splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        item = line[2:].strip()
        markdown_link = _SRC_MARKDOWN_LINK_RE.search(item)
        if markdown_link:
            prefix = item[: markdown_link.start()].rstrip(" :：—-")
            text = " ".join(
                part for part in (prefix, markdown_link.group(1).strip()) if part
            )
            url = markdown_link.group(2)
        else:
            um = _SRC_URL_RE.search(item)
            url = um.group(1) if um else ""
            text = item[: um.start()].rstrip(" :：—-") if um else item
        bm = re.search(r"[「『（(]", text)
        name = text[: bm.start()].strip() if bm else text
        rest = text[bm.start():] if bm else ""
        if not url:
            print(
                f"[drop] DeepDive 参考リンク: URL 無しのため除外 (押せないリンクを出さない): {text[:80]}",
                file=sys.stderr,
            )
            continue
        sources.append({"text": text, "url": url, "name": name, "rest": rest})
    return sources


def _publisher_name(biblio_text: Any) -> str:
    """参考リンク 1 件の出版社名 (「記事タイトル」より前の社名部分) を取り出す。"""
    return re.split(r"[「『（(]", str(biblio_text or ""), maxsplit=1)[0].strip()


def _publisher_key(name: str) -> str:
    """出版社名から照合キー (最長の英数字ラン) を作り、図の source 文字列と部分一致させる。

    例:「ITmedia NEWS」→ ITmedia /「The Information」→ Information /「Investing.com」→ そのまま。
    英数字を含まない日本語社名は名前全体をキーにする。
    """
    runs = [r for r in re.findall(r"[A-Za-z0-9.]+", name) if len(r) >= 4]
    return max(runs, key=len) if runs else name.strip()


def _figure_citations(raw: Any, biblio: list[dict[str, str]]) -> dict[str, Any] | None:
    """図 (relations/chart/table) の source 文字列を、参考リンク番号付きの引用に変換する。

    複数出典 (例「Axios / Investing.com / ITmedia」) を 1 リンクに潰さず、各出版社を末尾の
    参考リンク一覧 (biblio。1-based 番号がそのまま脚注番号) と照合して `*N 出版社名` の
    個別リンク (リンク先は出典サイト URL) にする。最初の 。より前を引用部、以降を著者の
    注記として扱う (生 URL は番号リンクに置換するため除去)。参考リンクに該当が無ければ
    従来どおり全文 1 リンクへフォールバックする。空文字なら None (SOURCE 行を出さない)。
    """
    s = str(raw or "").strip()
    if not s:
        return None
    um = re.search(r"https?://\S+", s)
    url = um.group(0) if um else ""
    s = re.sub(r"\s*https?://\S+", "", s).strip()
    head, _sep, note = s.partition("。")
    note = note.strip(" 　、。")
    cites: list[dict[str, Any]] = []
    seen: set[int] = set()
    for idx, b in enumerate(biblio, 1):
        name = _publisher_name(b.get("text", ""))
        key = _publisher_key(name)
        if key and key.lower() in head.lower() and idx not in seen:
            seen.add(idx)
            cites.append({"num": idx, "name": name, "url": b.get("url", "")})
    if cites:
        return {"cites": cites, "note": note, "fallback": None}
    # 参考リンクに該当が無い → 全文 1 リンク (旧挙動) にフォールバック
    text = head.strip(" 　:：—-/。、") or note
    if not text and not url:
        return None
    return {"cites": [], "note": "", "fallback": {"text": text or url, "url": url}}


# ── relations: 座標なしスキーマ → 役割レイヤー (band) 配置 + SVG ────────────────

def _neutral_institutions(
    order: list[str], grp: dict[str, str], edges: list[dict[str, Any]],
) -> set[str]:
    """中立機関 = group に「規制」「当局」を含む or 規制エッジ (_AUTH_KINDS) の source。

    どちらの陣営にも属さない監督主体。camps モードでは専用の最下段レイヤー、bands
    モードでは最下段の規制 band へ置く。_camp_columns と _relation_bands が同じ定義を
    共有するための単一ソース (旧: 両関数に同じ式が重複していた)。
    """
    reg = {i for i in order if ("規制" in grp[i]) or ("当局" in grp[i])}
    reg |= {str(e["from"]) for e in edges if e.get("kind") in _AUTH_KINDS}
    return reg


def _camp_groups(members: list[str], grp: dict[str, str]) -> list[str]:
    """members (中立を除く事業者) のうち同一 group が 2 ノード以上ある group を出現順で返す。

    これが「陣営」。ちょうど 2 つなら左右カラム (_camp_columns)、それ以外は水平バンド
    (_band_layout) へ分岐する。_choose_layout_mode と _camp_columns が共有する単一ソース。
    """
    seen: list[str] = []
    for i in members:
        if grp[i] and grp[i] not in seen:
            seen.append(grp[i])
    return [g for g in seen if sum(1 for i in members if grp[i] == g) >= 2]


def _choose_layout_mode(
    nodes: list[dict[str, Any]], edges: list[dict[str, Any]],
) -> str:
    """relations の配置モードを 1 箇所で決める ── 関係図レイアウトの単一の正典。

    ★ この docstring が関係図 (relations / 勢力図 / 相関図) の配置規約の正典。memory
       `feedback_relation_diagram_semantic_layout` とプロンプト `deepdive-research-system.md`
       の relations 節はここを指すポインタで、配置アルゴリズムの詳細を二重に書かない
       (二重記述が腐ってプロンプトだけ旧モデルに取り残された 2026-06-02 の反省)。

    nodes/edges は座標を持たないので、kind と group の「意味」から決定論的に配置する。
    目的は装飾でなく構造の可視化 ── 誰と誰が対立し・誰が誰を支え・誰が監督するかを一目で
    読ませることなので、「とりあえず全部つなぐ」円環 auto-layout は採らない。

    配置モードは 2 つ (bands は内部でさらに 3 分岐):

      "camps"  中立機関を除く事業者が "ちょうど 2 つ" の対立陣営 (各 2 ノード以上) に
               分かれる。→ _camp_columns。「陣営」は役割ではない。①事業者を陣営で左右に
               分け (横軸 = 陣営)、②各陣営内で主役 (中心事業者) と支援者を役割ティアに積み
               (縦軸: 出資元/支援者 = 上 / 主役 = 中 / 顧客・下流 = 下)、主役同士を同じ
               高さで左右に対峙させる。③どちらの陣営にも属さない中立機関 (規制当局・両陣営へ
               供給するベンダー等) は専用の最下段レイヤーにまとめる。

      "bands"  上記以外。→ _band_layout が役割を水平レイヤーに積む。内部でさらに:
               ・3 陣営以上 (各 2 ノード以上)         → 陣営ごとの水平バンド
               ・陣営なし + 一方向フロー (出資/供給/規制) → _flow_layers でバリューチェーンを
                 最長路段層化し供給先を供給元より下段へ落とす (同段ノードを貫く直販線を消す)
               ・フローも無い                          → 単一バンドで競合を左右二分 (_rivalry_sides)

    kind → 配置の効き方 / 矢印の向き:
      競合・対立           = 勢力の対立 → 左右に二分。相互関係なので既定で双方向 (⇔)。
      出資・提携・供給      = 協力       → 同じ側に縦積み (出資元/親が上)。一方向 (→)。
      規制                = 監督       → 当局を最下段に。当局→対象の一方向 (→)。
      二面関係           = 提携 edge と競合 edge を別々に置く。単一の edge に複数の意味を
                            圧縮せず、各 edge の label と kind を個別に描画する。

    判定の母集団 (意図的差分):
      "camps" の判定 = 中立機関を除く group 付き事業者 (= _camp_groups の members)。
      bands 内のサブ分岐 (_relation_bands の use_camps) は出資元 (parents) も除いた
      operators 基準。出資元は陣営の一員でなく独立の上段 band に置く意図のため、母集団が
      "camps" 判定とは異なる。完全統一すると出資元の段が反転して挙動が変わるので統一しない。

    配置の不変条件 (競合=左右 / 協力=縦 / 規制=最下段 / 2 陣営=左右カラム+主役対峙+中立最下段 /
    バリューチェーン=供給先を下段 / ラベルの重なり 0 / ノード同士を被せない /
    エッジ線がノード円を貫通しない / 線交差は意味を壊さない範囲で最小化 /
    同じ役割・同じレイヤーのノードは、読み手が同列と分かるよう極力同じ y 行に揃える) は
    tests/test_deepdive_render.py の relations 系 3 件 (test_relations_layout_is_semantic_not_circular
    / test_relations_two_camps_split_left_right_and_no_overlap /
    test_relations_value_chain_layers_supply_sink_below) で契約として固定する。
    """
    order = [str(n.get("id", "")) for n in nodes]
    grp = {str(n.get("id", "")): str(n.get("group", "")) for n in nodes}
    neutral = _neutral_institutions(order, grp, edges)
    camp_members = [i for i in order if i not in neutral and grp[i]]
    return "camps" if len(_camp_groups(camp_members, grp)) == 2 else "bands"


def _flow_layers(
    operators: list[str], edges: list[dict[str, Any]], order: list[str],
) -> list[list[str]]:
    """陣営を成さない事業者を有向フロー (一方向エッジ) の最長路ランクで段に分ける。

    供給/出資/規制のような一方向エッジ (from→to) だけを使い、各ノードのランクを
    「そのノードへ入る最長フロー鎖の長さ」とする (DAG の longest-path layering)。供給先
    などバリューチェーンの下流は供給元より下段に落ち、同段ノードを貫く直販供給線が
    消える。競合/対立 (双方向の peer 関係) はランクに使わず同段に残す。フローが
    無く全ノードが同ランクなら単一 band を返し、従来の競合左右二分にフォールバックする。
    """
    ops = set(operators)
    succ: dict[str, list[str]] = {i: [] for i in operators}
    indeg: dict[str, int] = {i: 0 for i in operators}
    for e in edges:
        a, b = str(e.get("from", "")), str(e.get("to", ""))
        kind = str(e.get("kind", ""))
        is_peer = kind in (_RIVAL_KINDS | {"提携"})
        is_directed_flow = kind in _FLOW_KINDS or (bool(kind) and not is_peer)
        if is_directed_flow and a in ops and b in ops and a != b:
            succ[a].append(b)
            indeg[b] += 1
    rank: dict[str, int] = {i: 0 for i in operators}
    queue = [i for i in operators if indeg[i] == 0]
    while queue:                      # Kahn 順で最長路ランクを伝播 (DAG 前提)
        u = queue.pop(0)
        for v in succ[u]:
            rank[v] = max(rank[v], rank[u] + 1)
            indeg[v] -= 1
            if indeg[v] == 0:
                queue.append(v)
    n_ranks = max(rank.values(), default=0) + 1
    if n_ranks <= 1:
        return [list(operators)]
    return [[i for i in order if i in ops and rank[i] == k]
            for k in range(n_ranks) if any(rank[i] == k for i in operators)]


def _relation_bands(
    nodes: list[dict[str, Any]], edges: list[dict[str, Any]], ids: list[str],
) -> tuple[list[list[str]], set[str], set[str], bool]:
    """ノードを役割レイヤー (band) に分類し、上→下の band 行リストを返す。

    ユーザー指示 (2026-06-01): 「陣営や当局など異なる役割は必ず別レイヤーに分け、
    同じ役割は必ず同じレイヤーに入れる」。これを満たすため役割で水平レイヤーを切る:

      出資元・親会社 (出資エッジの source で被出資でない) … 最上段
      事業者 (それ以外)                                     … 中段
      規制当局 (規制エッジの source / group に「規制」「当局」) … 最下段

    事業者が 2 つ以上の陣営 group (どれか 1 つでも 2 ノード以上) に分かれるときは、
    陣営ごとに別の中段 band へ積む (例: 米陣営 / 中国陣営)。陣営が形成されない
    (group が全て単独) ときは、事業者を有向フロー (供給/出資 = 一方向) の段で層化し
    (バリューチェーン: 供給元の上段→供給先の下段)、フローも無ければ事業者を 1 band に
    置き競合を左右に二分する従来配置にフォールバックする (単一陣営 fixture を保つ)。

    返り値: (rows, reg, parents, use_camps)
    """
    order = [str(n.get("id", "")) for n in nodes]
    grp = {str(n.get("id", "")): str(n.get("group", "")) for n in nodes}

    reg = _neutral_institutions(order, grp, edges)
    invest_to = {e["to"] for e in edges if e.get("kind") == "出資"}
    parents = {e["from"] for e in edges if e.get("kind") == "出資"} - invest_to - reg
    operators = [i for i in order if i not in reg and i not in parents]

    # 事業者の陣営 group を出現順で集計。2 群以上 (各 2 ノード以上が 1 つでもあれば)
    # 陣営 band に割る。全て単独 group なら単一事業者 band (競合左右) にする。
    seen_g: list[str] = []
    for i in operators:
        if grp[i] and grp[i] not in seen_g:
            seen_g.append(grp[i])
    counts = {g: sum(1 for i in operators if grp[i] == g) for g in seen_g}
    use_camps = len(seen_g) >= 2 and any(c >= 2 for c in counts.values())

    rows: list[list[str]] = []
    if parents:
        rows.append([i for i in order if i in parents])
    if use_camps:
        for g in seen_g:
            rows.append([i for i in operators if grp[i] == g])
        ungrouped = [i for i in operators if not grp[i]]
        if ungrouped:
            rows.append(ungrouped)
    elif operators:
        # 陣営を成さない事業者は有向フロー (供給/出資/規制 = 一方向) の段で層化する。
        # 供給先 (下流) を供給元より下段へ落とし、同段ノードを貫く直販供給線を消す。
        rows.extend(_flow_layers(operators, edges, order))
    if reg:
        rows.append([i for i in order if i in reg])
    if not rows:
        rows = [list(order)]
    return rows, reg, parents, use_camps


def _rivalry_sides(rival_edges: list[dict[str, Any]]) -> dict[str, str]:
    """競合/対立サブグラフを 2-color して L/R の側を返す (単一事業者 band の左右二分用)。"""
    side: dict[str, str] = {}
    radj: dict[str, list[str]] = {}
    for e in rival_edges:
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
    return side


def _camp_columns(
    nodes: list[dict[str, Any]], edges: list[dict[str, Any]], ids: list[str],
    deg: dict[str, int], node_r: Any,
) -> dict[str, Any] | None:
    """2 つの対立陣営を「左右カラム + 役割ティア」で配置する (2026-06-02 ユーザー指示)。

    重要: 「陣営」は役割ではない。まず事業者を 2 陣営へ左右に分け (横軸 = 陣営)、各陣営
    の内部で『主役』と、それを支援する出資元・顧客を別の段に積む (縦軸 = 役割ティア。
    出資元/支援者 = 上 / 主役 = 中 / 顧客・下流 = 下)。主役同士は同じ高さで左右に対峙し、
    それが図の対立軸になる。どちらの陣営にも属さない中立機関 (規制当局・両陣営へ供給する
    ベンダー等) は専用の最下段レイヤーにまとめる。

    陣営 group (中立を除く事業者で同一 group が 2 ノード以上) が「ちょうど 2 つ」ある
    ときだけ適用し、それ以外 (単一陣営・3 陣営以上・バリューチェーン) は None を返して
    既存の _band_layout (役割レイヤー積み) へフォールバックする。
    """
    order = [str(n.get("id", "")) for n in nodes]
    grp = {str(n.get("id", "")): str(n.get("group", "")) for n in nodes}

    # 中立機関 (規制当局等) と陣営 group は _choose_layout_mode と同じヘルパーで判定する
    # (旧: 同じ式を両関数に重複記述していた → 単一ソースへ集約)。
    neutral_inst = _neutral_institutions(order, grp, edges)
    camp_members = [i for i in order if i not in neutral_inst and grp[i]]
    camps = _camp_groups(camp_members, grp)
    if len(camps) != 2:   # camps モード前提。通常は _choose_layout_mode 経由でのみ呼ばれる
        return None
    campA, campB = camps[0], camps[1]
    members = {g: [i for i in order if grp[i] == g and i not in neutral_inst]
               for g in camps}
    in_camp = set(members[campA]) | set(members[campB])
    camp_of = {i: (campA if i in members[campA] else campB) for i in in_camp}
    # 陣営に属さないノード (供給ベンダー・規制当局・単独 group 事業者) は中立 = 最下段。
    neutral = [i for i in order if i not in in_camp]

    # 左右割当: 2 陣営をつなぐ最初の cross-camp 対立 (競合/対立) エッジの
    # to 側を左に置く (OpenAI=左 のユーザー既定整理に一致する決定論ルール)。cross 対立が
    # 無ければ出現順で campA を左にする。
    left = campA
    for e in edges:
        if e.get("kind") in _RIVAL_KINDS:
            a, b = str(e.get("from", "")), str(e.get("to", ""))
            if a in camp_of and b in camp_of and camp_of[a] != camp_of[b]:
                left = camp_of[b]
                break
    side = {g: ("L" if g == left else "R") for g in camps}

    # 各陣営の主役 = 相手陣営との対立に絡む member (複数なら最大次数)、無ければ最大次数。
    rival_pairs = [(str(e.get("from", "")), str(e.get("to", "")))
                   for e in edges if e.get("kind") in _RIVAL_KINDS]

    def _principal(g: str) -> str:
        cross = [m for m in members[g] if any(
            (m == x and y in in_camp and camp_of.get(y) != g)
            or (m == y and x in in_camp and camp_of.get(x) != g)
            for x, y in rival_pairs)]
        pool = cross or members[g]
        return max(pool, key=lambda m: (deg.get(m, 0), -order.index(m)))

    principal = {g: _principal(g) for g in camps}
    princ_set = {principal[g] for g in camps}

    # 役割ティア: 0 = 出資元/支援者 (上) / 1 = 主役 (中) / 2 = 顧客・下流 (下)。
    # 主役→m が供給なら m は下流 (顧客) = 下段、それ以外の非主役は支援者 = 上段。
    supply_sink = {str(e["to"]) for e in edges
                   if e.get("kind") == "供給" and str(e["from"]) in princ_set}

    def _tier(i: str) -> int:
        if i in princ_set:
            return 1
        if i in supply_sink:
            return 2
        return 0

    tier = {i: _tier(i) for i in in_camp}
    used_tiers = sorted({tier[i] for i in in_camp})
    row_of_tier = {t: k for k, t in enumerate(used_tiers)}
    n_rows = len(used_tiers) + (1 if neutral else 0)
    neutral_row = len(used_tiers)

    vb_w = 1080
    max_r = max((node_r(i) for i in order), default=50.0)
    top_pad = max_r + 56
    bot_pad = max_r + 70
    vb_h = int(max(560, 200 + n_rows * 185))
    usable = vb_h - top_pad - bot_pad
    row_y = ({0: vb_h / 2.0} if n_rows <= 1
             else {k: top_pad + usable * (k / (n_rows - 1)) for k in range(n_rows)})

    xl_c, xr_c = vb_w * 0.27, vb_w * 0.73   # 左右カラムの中心 x (中央に対立軸チャネルを残す)
    half_span = vb_w * 0.19                  # カラム内で同段ノードが広がれる半幅
    x: dict[str, float] = {}
    y: dict[str, float] = {}

    def _spread(items: list[str], center: float, row: int) -> None:
        items = sorted(items, key=lambda i: order.index(i))
        m = len(items)
        if m == 1:
            x[items[0]] = center
        elif m > 1:
            step = min((2 * half_span) / (m - 1), 200.0)
            start = center - step * (m - 1) / 2
            for j, i in enumerate(items):
                x[i] = start + step * j
        for i in items:
            y[i] = row_y[row]

    # 各 (陣営, ティア) セルを左右カラムへ。主役は必ずカラム中心に揃え左右で対峙させる。
    for g in camps:
        c = xl_c if side[g] == "L" else xr_c
        for t in used_tiers:
            _spread([i for i in members[g] if tier[i] == t], c, row_of_tier[t])
    if neutral:
        _spread(neutral, vb_w / 2.0, neutral_row)

    placed = []
    for nd in nodes:
        i = str(nd.get("id", ""))
        xx = min(max(x.get(i, vb_w / 2.0), node_r(i) + 8), vb_w - node_r(i) - 8)
        placed.append({**nd, "x": round(xx, 1), "y": round(y.get(i, vb_h / 2.0), 1),
                       "r": node_r(i), "deg": deg.get(i, 1)})
    return {"nodes": placed, "vb_w": vb_w, "vb_h": vb_h}


def _band_layout(
    nodes: list[dict[str, Any]], edges: list[dict[str, Any]], ids: list[str],
    deg: dict[str, int], node_r: Any, max_r: float,
) -> tuple[list[dict[str, Any]], int, int]:
    """役割レイヤー (band) を縦に積む配置 (単一陣営・3 陣営以上・バリューチェーン用)。

    2 陣営の対立は _camp_columns が左右カラムで処理するため、ここへは来ない。残りの
    ケースを「出資元/親 = 上段、事業者 = 中段 (陣営ごとに別 band)、規制当局 = 下段」の
    水平レイヤーに積み、barycenter スイープで交差を抑える (旧 layout_relations 本体)。
    """
    rows, reg, parents, use_camps = _relation_bands(nodes, edges, ids)
    n_levels = len(rows)
    band_of = {i: k for k, row in enumerate(rows) for i in row}

    vb_w = 1080
    vb_h = int(max(560, 170 + n_levels * 175))
    top_pad = max_r + 52
    bot_pad = max_r + 70
    if n_levels <= 1:
        band_y = {0: vb_h / 2.0}
    else:
        usable = vb_h - top_pad - bot_pad
        band_y = {k: top_pad + usable * (k / (n_levels - 1)) for k in range(n_levels)}

    # barycenter 計算用の隣接 (自 band 内の辺も含めてよい)
    nbr: dict[str, list[str]] = {i: [] for i in ids}
    for e in edges:
        a, b = str(e["from"]), str(e["to"])
        nbr[a].append(b)
        nbr[b].append(a)

    margin = 100.0
    lo, hi = margin, vb_w - margin

    # 事業者/陣営 band = ANCHOR、出資元/規制 band = FLOATING (重心へ寄せる)。
    anchor_rows = [k for k, row in enumerate(rows)
                   if not (set(row) <= reg or set(row) <= parents)]
    rival_edges = [e for e in edges if e.get("kind") in _RIVAL_KINDS]
    single_op_band = (not use_camps) and len(anchor_rows) == 1
    side = _rivalry_sides(rival_edges) if single_op_band else {}

    x: dict[str, float] = {}

    def _peer_aware_row(row: list[str]) -> list[str]:
        """同段 peer エッジ (競合/対立) で結ばれたノードが隣接する順列に並べ替える。

        2026-06-06 BYD↔NVIDIA 競合線が同段中央の Tesla を貫通した事故の構造解決。
        bands モードで anchor row を出現順 (例: [byd, tesla, nvidia]) で `_even_slots`
        すると、hub-and-spoke (hub=BYD, spokes=Tesla/NVIDIA) のとき hub が端に置かれ、
        両端 spoke を結ぶ peer 線が中央の他ノードを真っ二つに貫通する宿命になる。

        対策: row 内の peer エッジを集計し、(1) 全 peer を 1 ノードが抱える星型なら
        hub を中央、spokes を左右対称に振り分ける / (2) それ以外は最大 peer-degree
        ノードから BFS 連結展開して path 状に並べる。peer 接続のないノードは末尾に
        元の出現順で追加する。これにより peer 線が他ノードを貫通する配置を
        `_even_slots` 段階で構造的に排除する ([[feedback_relation_diagram_semantic_layout]]
        「エッジ線がノード円を貫通しない」を境界 1 箇所で保証 / [[feedback_check_design_principles]]
        1 段「失敗を表現できない構造に変える」)。
        """
        rs = set(row)
        nbr_peer: dict[str, list[str]] = {i: [] for i in row}
        for e in rival_edges:
            a, b = str(e.get("from", "")), str(e.get("to", ""))
            if a in rs and b in rs and a != b:
                nbr_peer[a].append(b)
                nbr_peer[b].append(a)
        if all(not v for v in nbr_peer.values()):
            return list(row)
        total_pairs = sum(len(v) for v in nbr_peer.values()) // 2
        hubs = [i for i in row
                if len(nbr_peer[i]) == total_pairs and total_pairs >= 2]
        if hubs:
            hub = hubs[0]
            spokes = sorted(nbr_peer[hub], key=lambda j: row.index(j))
            left, right = [], []
            for j, sp in enumerate(spokes):
                (left if j % 2 == 0 else right).append(sp)
            ordered = list(reversed(left)) + [hub] + right
        else:
            start = max(row, key=lambda i: (len(nbr_peer[i]), -row.index(i)))
            ordered, seen = [], {start}
            cur = [start]
            while cur:
                nxt = []
                for u in cur:
                    ordered.append(u)
                    for v in nbr_peer[u]:
                        if v not in seen:
                            seen.add(v)
                            nxt.append(v)
                cur = nxt
        for i in row:
            if i not in ordered:
                ordered.append(i)
        return ordered

    def _even_slots(order_ids: list[str]) -> None:
        m = len(order_ids)
        if m == 1:
            x[order_ids[0]] = vb_w / 2.0
            return
        # 2 ノード段は中央ラベルが両円へ食い込みやすい。横幅を使って左右へ広げる。
        # 3 ノード以上は peer-aware 順序と線貫通回避を優先し、従来寄りの密度を保つ。
        max_step = 480.0 if m == 2 else 290.0
        step = min((hi - lo) / (m - 1), max_step)
        start = (vb_w - step * (m - 1)) / 2.0
        for j, i in enumerate(order_ids):
            x[i] = start + step * j

    def _space_row(row: list[str], desired: dict[str, float]) -> None:
        items = sorted(row, key=lambda i: desired.get(i, vb_w / 2.0))
        gaps = [(node_r(a) + node_r(b) + 28.0) for a, b in zip(items, items[1:])]
        xs = [desired.get(i, vb_w / 2.0) for i in items]
        for j in range(1, len(items)):
            if xs[j] < xs[j - 1] + gaps[j - 1]:
                xs[j] = xs[j - 1] + gaps[j - 1]
        if xs and xs[-1] > hi:
            xs[-1] = hi
            for j in range(len(items) - 2, -1, -1):
                if xs[j] > xs[j + 1] - gaps[j]:
                    xs[j] = xs[j + 1] - gaps[j]
        if xs and xs[0] < lo:
            shift = lo - xs[0]
            xs = [v + shift for v in xs]
        for i, v in zip(items, xs):
            x[i] = v

    # anchor row (事業者段) の row 自体を peer-aware 順に置換する。初期 _even_slots
    # だけでなく、後段の barycenter sweep で tie-break として元 row 順が参照される
    # (`sorted(row, key=desired)` は stable) ため、rows 自体を置換しないと sweep で
    # 順序が元に戻ってしまう。rows を更新することで初期配置と sweep の両方が同じ
    # peer-aware 順序を保ち、hub-and-spoke 型の peer 線が同段他ノードを貫通する
    # 配置が構造的に排除される (2026-06-06 BYD↔NVIDIA 線が Tesla を貫通した事故の
    # 境界 1 箇所集約)。single_op_band は別系統 (L/R 二分) なので対象外。
    if not single_op_band:
        rows = [_peer_aware_row(list(row)) if k in anchor_rows else row
                for k, row in enumerate(rows)]

    # 初期 x: 全 band を均等スロット (単一事業者 band のみ競合 2-color で L→R)
    for k, row in enumerate(rows):
        if single_op_band and k in anchor_rows and side:
            _even_slots([i for i in row if side.get(i) == "L"]
                        + [i for i in row if side.get(i) != "L"])
        else:
            _even_slots(list(row))

    def _bary(i: str) -> float:
        xs = [x[j] for j in nbr[i] if j in x]
        return sum(xs) / len(xs) if xs else x.get(i, vb_w / 2.0)

    # barycenter スイープ: FLOATING(出資元/規制) は重心へ寄せ、陣営 ANCHOR は重心順で
    # 再スロットして交差を減らす。単一事業者 band は競合左右を保持するため固定する。
    for _ in range(8):
        for k, row in enumerate(rows):
            if single_op_band and k in anchor_rows:
                continue
            desired = {i: _bary(i) for i in row}
            if k in anchor_rows and len(row) > 1:
                _even_slots(sorted(row, key=lambda i: desired[i]))
            else:
                _space_row(row, desired)

    # 仕上げ: FLOATING (出資元/規制) を確定後アンカー位置の重心へ最終整列する。スイープ内
    # では各反復でアンカーより先に処理され 1 反復古い位置を読むため、出資元が出資先でなく
    # 隣のノードの真上に残ることがある (出資元は出資先の上、の規約を最後に満たし直す)。
    for k, row in enumerate(rows):
        if k not in anchor_rows:
            _space_row(row, {i: _bary(i) for i in row})

    def _seg_point_dist_xy(ax: float, ay: float, bx: float, by: float, px: float, py: float) -> float:
        dx, dy = bx - ax, by - ay
        l2 = dx * dx + dy * dy
        if l2 < 1e-9:
            return math.hypot(px - ax, py - ay)
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / l2))
        return math.hypot(px - (ax + dx * t), py - (ay + dy * t))

    def _x_on_segment_at_y(ax: float, ay: float, bx: float, by: float, py: float) -> float:
        if abs(by - ay) < 1e-9:
            return (ax + bx) / 2.0
        t = max(0.0, min(1.0, (py - ay) / (by - ay)))
        return ax + (bx - ax) * t

    def _relieve_long_edge_crossings() -> None:
        """複数 band をまたぐ edge が中間の単独ノード円を貫通する配置を後処理で逃がす。

        2026-07-04 AI 基盤図では、AWS→Anthropic の長い供給線が中間 band の
        Samsung 円を貫通した。これは同段 peer ではなく、flow layering 後の単独 row が
        長距離 edge の線上へ重心配置される事故なので、row 内衝突のない単独ノードだけを
        線分から左右へ逃がす。
        """
        for _ in range(4):
            moved = False
            for e in edges:
                a, b = str(e.get("from", "")), str(e.get("to", ""))
                if a not in x or b not in x or a not in band_of or b not in band_of:
                    continue
                ka, kb = band_of[a], band_of[b]
                if abs(ka - kb) < 2:
                    continue
                lo_band, hi_band = sorted((ka, kb))
                ax, ay, bx, by = x[a], band_y[ka], x[b], band_y[kb]
                for c in ids:
                    kc = band_of.get(c)
                    if c in (a, b) or kc is None or not (lo_band < kc < hi_band):
                        continue
                    if len(rows[kc]) != 1:
                        continue
                    cy = band_y[kc]
                    clearance = node_r(c) + 22.0
                    if _seg_point_dist_xy(ax, ay, bx, by, x[c], cy) >= clearance:
                        continue
                    line_x = _x_on_segment_at_y(ax, ay, bx, by, cy)
                    side_sign = 1.0 if x[c] >= line_x else -1.0
                    target = line_x + side_sign * (clearance + 36.0)
                    clamped = min(max(target, node_r(c) + 8.0), vb_w - node_r(c) - 8.0)
                    if abs(clamped - x[c]) > 0.5:
                        x[c] = clamped
                        moved = True
            if not moved:
                break

    _relieve_long_edge_crossings()

    placed = []
    for nd in nodes:
        i = str(nd.get("id", ""))
        xx = x.get(i, vb_w / 2.0)
        yy = band_y.get(band_of.get(i, 0), vb_h / 2.0)
        xx = min(max(xx, node_r(i) + 8), vb_w - node_r(i) - 8)
        placed.append({**nd, "x": round(xx, 1), "y": round(yy, 1),
                       "r": node_r(i), "deg": deg.get(i, 1)})
    return placed, vb_w, vb_h


def _validate_relations(rel: dict[str, Any]) -> None:
    """関係図入力の kind / 単一 kind 根拠を公開前に確定する。

    ``EDGE_KINDS`` に無い値を暗黙の既定色へ落とすと、生成側の輸送語彙が
    見た目だけの関係線として公開される。kind は canonical な 8 種だけを
    公開面へ通し、旧輸送表記は入力不備として停止する。
    """
    edges = rel.get("edges") or []
    if not isinstance(edges, list):
        raise DeepDiveIncompleteError("relations edges must be a list")

    unknown: list[str] = []
    kinds: set[str] = set()
    for edge in edges:
        if not isinstance(edge, dict):
            unknown.append("(missing)")
            continue
        raw_kind = edge.get("kind")
        kind = raw_kind.strip() if isinstance(raw_kind, str) else str(raw_kind or "").strip()
        if kind not in EDGE_KINDS:
            unknown.append(kind or "(missing)")
        else:
            edge["kind"] = kind
            kinds.add(kind)
    if unknown:
        values = ", ".join(sorted(set(unknown)))
        raise DeepDiveIncompleteError(f"unknown relation kind: {values}")

    if len(edges) >= 4 and len(kinds) == 1:
        rationale = rel.get("singleKindRationale")
        if not isinstance(rationale, str) or len(rationale.strip()) < 16:
            raise DeepDiveIncompleteError(
                "singleKindRationale is required for relations with four or more edges "
                "of one kind"
            )


def layout_relations(rel: dict[str, Any]) -> dict[str, Any]:
    """nodes に x/y/r を決定論的に付与する (relations は座標を持たないため)。

    配置モードの選択と各モードの意味は _choose_layout_mode の docstring が正典
    (camps = 2 陣営左右カラム / bands = 単一陣営・3 陣営以上・バリューチェーン)。
    ラベルの重なり回避は relations_svg 側の _resolve_labels が担う。
    """
    _validate_relations(rel)
    nodes = list(rel.get("nodes", []))
    ids = [str(nd.get("id", "")) for nd in nodes]
    idset = set(ids)
    edges = [e for e in rel.get("edges", [])
             if str(e.get("from", "")) in idset and str(e.get("to", "")) in idset]

    deg: dict[str, int] = {}
    for e in edges:
        deg[str(e["from"])] = deg.get(str(e["from"]), 0) + 1
        deg[str(e["to"])] = deg.get(str(e["to"]), 0) + 1

    def _node_r(i: str) -> float:
        return float(min(40 + deg.get(i, 1) * 8, 62))

    max_r = max((_node_r(i) for i in ids), default=50.0)

    # 生成側が x/y を明示した場合は、その編集判断を尊重する。
    # 「政策当局」が主役の回では自動 band 判定が最下段へ落としてしまい、図の重心が
    # 右下に寄ることがある。明示座標はその例外を data 側で表現するための逃げ道。
    # ただし明示座標も上の関係図規約から免除されない。ノード/ラベルを被せず、不要な
    # 線交差を避け、同じ役割・同じレイヤーは極力同じ y 行に揃えること。
    explicit_xy = all(
        isinstance(nd.get("x"), (int, float)) and isinstance(nd.get("y"), (int, float))
        for nd in nodes
    )
    if explicit_xy:
        vb_w = int(rel.get("width") or 1080)
        vb_h = int(rel.get("height") or 640)
        placed = []
        for nd in nodes:
            i = str(nd.get("id", ""))
            r = float(nd.get("r") or _node_r(i))
            xx = min(max(float(nd["x"]), r + 8), vb_w - r - 8)
            yy = min(max(float(nd["y"]), r + 8), vb_h - r - 8)
            placed.append({**nd, "x": round(xx, 1), "y": round(yy, 1),
                           "r": r, "deg": deg.get(i, 1)})
    else:
        # 配置モードは _choose_layout_mode の docstring が正典 (camps=2 陣営左右カラム / bands=その他)。
        cc = _camp_columns(nodes, edges, ids, deg, _node_r) \
            if _choose_layout_mode(nodes, edges) == "camps" else None
        if cc is not None:
            placed, vb_w, vb_h = cc["nodes"], cc["vb_w"], cc["vb_h"]
        else:
            placed, vb_w, vb_h = _band_layout(nodes, edges, ids, deg, _node_r, max_r)

    layout = dict(rel)
    layout["nodes"] = placed
    layout["vb_w"], layout["vb_h"] = vb_w, vb_h
    # 凡例 = 実際に登場した canonical kind のみ。
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
        ks = EDGE_KINDS[k]
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


# relations のラベル本文の全角換算上限。
# 1080px 幅の関係図 1 段に長尺ラベルが密集すると _resolve_labels が AABB 分離を
# 収束できないため、レンダラ側の最後の砦として physical な truncate を入れる。
# 全角換算は CJK=1.0 / ASCII=0.5 で測り、超過分は末尾を「…」に置換する。
# プロンプト規約 (prompts/deepdive-research-system.md) でも「ラベルは短く」と
# 指示するが、生成側を待たずレンダラで保証する設計 (= [[feedback_check_design_principles]]
# 「失敗を表現できない構造に変える」)。2026-06-04 ユーザー指摘で恒久化。
LABEL_MAX_FULLWIDTH_UNITS: float = 18.0


def _label_width_units(s: str) -> float:
    """全角換算の文字数 (CJK=1.0 / それ以外=0.5)。LABEL_MAX_FULLWIDTH_UNITS の判定用。"""
    return sum(1.0 if _is_cjk(ch) else 0.5 for ch in s)


def _truncate_label(s: str, limit: float = LABEL_MAX_FULLWIDTH_UNITS) -> str:
    """全角換算で limit を超えるラベルを「…」付きで切り詰める (関係図の見栄えと
    _resolve_labels の収束を守るため)。limit 内なら何もしない。"""
    if not s or _label_width_units(s) <= limit:
        return s
    acc, out = 0.0, []
    cap = max(1.0, limit - 1.0)   # 末尾の「…」分 (CJK 1.0 相当) を確保
    for ch in s:
        unit = 1.0 if _is_cjk(ch) else 0.5
        if acc + unit > cap:
            break
        out.append(ch)
        acc += unit
    return "".join(out).rstrip() + "…"


def resolve_accent(lens_id: str) -> str:
    """lens (カテゴリ id) からアクセント色を引く。未知なら near-black に退避。"""
    cat = CATEGORIES.get((lens_id or "").lower())
    return cat["accent"] if cat else INK


def _resolve_labels(
    specs: list[dict[str, Any]], circles: list[tuple[float, float, float]],
    vb_w: float, vb_h: float,
) -> tuple[list[dict[str, Any]], float]:
    """ラベルチップ同士・チップとノード円の重なりを反復で 0 にする (決定論的な力学分離)。

    各 spec は中心 (cx,cy)・寸法 (w,h)・アンカー (ax,ay = エッジ上の初期位置) を持つ。
    アンカーへ弱いバネで引き戻しつつ、(1) 他チップとの AABB 重なり、(2) ノード円との
    重なりを押し離す。仕上げにバネ無しの分離パスを回し、残留重なりを潰す。乱数なし。

    縦余地不足で AABB 分離が収束しないとき (高密度な bands モード) は、viewBox の高さを
    段階的に拡張して specs を初期位置へ戻し再収束を試みる。返り値は (specs, 確定 vb_h)。
    呼び出し側はこの vb_h を SVG ヘッダーに反映する (= [[feedback_check_design_principles]]
    「失敗を表現できない構造に変える」: クランプに挟まれて重なり残存する状態を物理的に消す)。
    """
    n = len(specs)

    # ── anchor preconditioning ──
    # _anchor() は同じ zone_center (band 間の空白帯) に複数ラベルを集中投入する。
    # vb_h を後で拡張しても、引き戻しのバネ (ax,ay) が同じ y に集中していると
    # specs は再収束で結局同じ場所に戻り重なりが解けない (2026-06-04 観察)。
    # ここでクラスタを検出して anchor を chip_h より広い pitch で段差付け、初期分散を
    # 確保する (pitch は _separate の oy 押し合い閾値 h+5 を必ず超える 12 マージン)。
    if n >= 2:
        cluster_h = max((s["h"] for s in specs), default=26.0)
        # 連鎖クラスタ判定: ay 差がこれ以下なら同クラスタ。
        cluster_th = cluster_h + 24
        # 配分ピッチ: 多数 specs を band 間 gap に詰め込めないため、cluster は band 円
        # 範囲外 (上方向・下方向) まで広がる前提。pitch を大きく取ることで _separate の
        # oy 押し合い閾値 (h+5) を確実に上回り、引き戻しバネと衝突しても元に戻らない。
        pitch = cluster_h * 2.5
        idx = sorted(range(n), key=lambda i: (specs[i]["ay"], specs[i]["ax"]))
        i = 0
        while i < len(idx):
            cluster = [idx[i]]
            j = i + 1
            # クラスタは連鎖で拡張する (最後尾との ay 差で判定)。先頭固定だと
            # 中央集中した specs 群が分断され、境界の specs 同士が分離されないまま残る。
            while j < len(idx) and abs(specs[idx[j]]["ay"] - specs[cluster[-1]]["ay"]) <= cluster_th:
                cluster.append(idx[j])
                j += 1
            if len(cluster) >= 2:
                # クラスタを cx 順に並べ替え、cy を中心からピッチで上下対称に再配置
                cluster.sort(key=lambda k: specs[k]["ax"])
                base_y = sum(specs[k]["ay"] for k in cluster) / len(cluster)
                m = len(cluster)
                for o, k in enumerate(cluster):
                    new_y = base_y + (o - (m - 1) / 2) * pitch
                    specs[k]["ay"] = specs[k]["cy"] = new_y
            i = j

    init = [(s["cx"], s["cy"], s["ax"], s["ay"]) for s in specs]

    def _separate(local_vb_h: float) -> bool:
        moved = False
        for a in range(n):
            s = specs[a]
            for b in range(a + 1, n):
                t = specs[b]
                ox = (s["w"] + t["w"]) / 2 + 6 - abs(s["cx"] - t["cx"])
                oy = (s["h"] + t["h"]) / 2 + 5 - abs(s["cy"] - t["cy"])
                if ox > 0 and oy > 0:
                    if oy <= ox:  # 浅い側 (縦) で割る
                        d = oy / 2 + 0.5
                        s["cy"], t["cy"] = ((s["cy"] - d, t["cy"] + d)
                                            if s["cy"] <= t["cy"] else (s["cy"] + d, t["cy"] - d))
                    else:
                        d = ox / 2 + 0.5
                        s["cx"], t["cx"] = ((s["cx"] - d, t["cx"] + d)
                                            if s["cx"] <= t["cx"] else (s["cx"] + d, t["cx"] - d))
                    moved = True
        for s in specs:
            hw, hh = s["w"] / 2, s["h"] / 2
            for (ncx, ncy, nr) in circles:
                qx = min(max(ncx, s["cx"] - hw), s["cx"] + hw)
                qy = min(max(ncy, s["cy"] - hh), s["cy"] + hh)
                vx, vy = qx - ncx, qy - ncy
                d = math.hypot(vx, vy)
                need = nr + 7
                if d < need:
                    if d < 1e-6:
                        vx, vy, d = 0.0, (1.0 if s["cy"] >= ncy else -1.0), 1.0
                    push = need - d
                    s["cx"] += vx / d * push
                    s["cy"] += vy / d * push
                    moved = True
        for s in specs:
            s["cx"] = min(max(s["cx"], s["w"] / 2 + 5), vb_w - s["w"] / 2 - 5)
            s["cy"] = min(max(s["cy"], s["h"] / 2 + 5), local_vb_h - s["h"] / 2 - 5)
        return moved

    def _has_residual_overlap() -> bool:
        for a in range(n):
            s = specs[a]
            for b in range(a + 1, n):
                t = specs[b]
                ox = (s["w"] + t["w"]) / 2 + 1 - abs(s["cx"] - t["cx"])
                oy = (s["h"] + t["h"]) / 2 + 1 - abs(s["cy"] - t["cy"])
                if ox > 0 and oy > 0:
                    return True
            hw, hh = s["w"] / 2, s["h"] / 2
            for (ncx, ncy, nr) in circles:
                qx = min(max(ncx, s["cx"] - hw), s["cx"] + hw)
                qy = min(max(ncy, s["cy"] - hh), s["cy"] + hh)
                if math.hypot(qx - ncx, qy - ncy) < nr - 0.5:
                    return True
        return False

    def _run(current_vb_h: float) -> None:
        for _ in range(400):
            for s in specs:  # アンカーへ弱いバネ
                s["cx"] += (s["ax"] - s["cx"]) * 0.015
                s["cy"] += (s["ay"] - s["cy"]) * 0.03
            _separate(current_vb_h)
        for _ in range(200):  # 仕上げ: バネ無しで残留重なりを潰し切る
            if not _separate(current_vb_h):
                break

    _run(vb_h)
    # 残留重なりがあれば vb_h を 1.18 倍ずつ拡張し、specs を初期位置へ戻して再収束。
    # 最大 4 回 (上限 vb_h * 1.94) で打ち切り (それでも解けないのは生成側の問題)。
    grow = 1.0
    for _ in range(4):
        if not _has_residual_overlap():
            break
        grow *= 1.18
        for s, (cx0, cy0, ax0, ay0) in zip(specs, init):
            s["cx"], s["cy"], s["ax"], s["ay"] = cx0, cy0, ax0, ay0
        vb_h *= 1.18
        _run(vb_h)
    return specs, vb_h


def _layout_relations_mobile(rel: dict[str, Any]) -> dict[str, Any]:
    """360px 幅の関係図用に group を縦レイヤーへ配置する。

    desktop の陣営配置は広い viewBox を前提にしているため、その座標を単純に
    縮小するとノード同士とラベルが重なる。mobile は group ごとに y レイヤーを
    固定し、同一レイヤー内だけを横へ展開する専用配置として、幅を 360px に閉じる。
    """
    base = layout_relations(rel)
    nodes = list(rel.get("nodes", []))
    ids = [str(nd.get("id", "")) for nd in nodes]
    node_ids = set(ids)
    edges = [
        e for e in (rel.get("edges") or [])
        if isinstance(e, dict)
        and str(e.get("from", "")) in node_ids
        and str(e.get("to", "")) in node_ids
    ]

    degree: dict[str, int] = {i: 0 for i in ids}
    adjacency: dict[str, list[str]] = {i: [] for i in ids}
    for edge in edges:
        a, b = str(edge.get("from", "")), str(edge.get("to", ""))
        if a == b or a not in node_ids or b not in node_ids:
            continue
        degree[a] += 1
        degree[b] += 1
        adjacency[a].append(b)
        adjacency[b].append(a)

    grouped: dict[str, list[str]] = {}
    base_x_by_id = {
        str(nd.get("id", "")): float(nd.get("x", 540.0))
        for nd in base.get("nodes", [])
        if isinstance(nd.get("x"), (int, float))
    }
    for nd in nodes:
        node_id = str(nd.get("id", ""))
        group = str(nd.get("group", "")).strip() or "__ungrouped__"
        grouped.setdefault(group, []).append(node_id)

    def _row_order(row: list[str]) -> list[str]:
        """同段の線が第三ノードを貫かないよう、接続ノードを近接配置する。"""
        if len(row) <= 2:
            return list(row)
        members = set(row)
        local = {i: [j for j in adjacency.get(i, []) if j in members] for i in row}
        pair_count = sum(len(v) for v in local.values()) // 2
        if pair_count == 0:
            return list(row)
        hubs = [i for i in row if len(local[i]) == pair_count and pair_count >= 2]
        if hubs:
            hub = hubs[0]
            spokes = sorted(local[hub], key=lambda i: row.index(i))
            left = [node for index, node in enumerate(spokes) if index % 2 == 0]
            right = [node for index, node in enumerate(spokes) if index % 2 == 1]
            ordered = list(reversed(left)) + [hub] + right
        else:
            start = max(row, key=lambda i: (len(local[i]), -row.index(i)))
            ordered, seen, queue = [], {start}, [start]
            while queue:
                current = queue.pop(0)
                ordered.append(current)
                for neighbor in sorted(local[current], key=lambda i: row.index(i)):
                    if neighbor not in seen:
                        seen.add(neighbor)
                        queue.append(neighbor)
            ordered.extend(i for i in row if i not in seen)
        return ordered

    # directed edge の longest-path rank を group 層へ写像する。入力順のままでは、
    # 供給元/投資家が対象より後ろに置かれ、長い edge が途中の group を貫通する。
    succ: dict[str, list[str]] = {i: [] for i in ids}
    indeg: dict[str, int] = {i: 0 for i in ids}
    for edge in edges:
        a, b = str(edge.get("from", "")), str(edge.get("to", ""))
        kind = str(edge.get("kind", ""))
        is_peer = kind in (_RIVAL_KINDS | {"提携"})
        if a in node_ids and b in node_ids and a != b and not is_peer:
            succ[a].append(b)
            indeg[b] += 1
    rank: dict[str, int] = {i: 0 for i in ids}
    queue = [i for i in ids if indeg[i] == 0]
    while queue:
        current = queue.pop(0)
        for target in succ[current]:
            rank[target] = max(rank[target], rank[current] + 1)
            indeg[target] -= 1
            if indeg[target] == 0:
                queue.append(target)
    group_index = {group: index for index, group in enumerate(grouped)}
    ordered_group_names = sorted(
        grouped,
        key=lambda group: (
            min((rank.get(node_id, 0) for node_id in grouped[group]), default=0),
            group_index[group],
        ),
    )
    # Keep every semantic group as its own vertical row on mobile.  Coalescing
    # groups that happen to share a rank lets a long edge cross an unrelated
    # group, defeating the dedicated mobile geometry contract.
    rows = [list(grouped[group]) for group in ordered_group_names]

    max_row_count = max((len(row) for row in rows), default=1)
    # 360px の左右 12px を保ち、円の間には最低 8px の空きを確保する。
    row_radius_cap = (
        (360.0 - 24.0 - (max_row_count - 1) * 8.0) / (2.0 * max_row_count)
        if max_row_count > 1 else 40.0
    )
    row_pitch = max(116.0, min(40.0, max(12.0, row_radius_cap)) * 2 + 44.0)
    top = 74.0
    vb_h = max(360.0, top + max(0, len(rows) - 1) * row_pitch + 74.0)

    x_by_id: dict[str, float] = {}
    y_by_id: dict[str, float] = {}
    r_by_id: dict[str, float] = {}
    for layer, original_row in enumerate(rows):
        row = _row_order(original_row)
        count = len(row)
        layer_cap = (
            (360.0 - 24.0 - (count - 1) * 8.0) / (2.0 * count)
            if count > 1 else 40.0
        )
        layer_radius = min(40.0, max(12.0, layer_cap))
        left = 12.0 + layer_radius
        right = 360.0 - left
        step = (right - left) / (count - 1) if count > 1 else 0.0
        y = top + layer * row_pitch
        for index, node_id in enumerate(row):
            nd_radius = min(layer_radius, 30.0 + degree.get(node_id, 1) * 2.0)
            if count > 1:
                x_by_id[node_id] = left + step * index
            else:
                # desktop が持つ意味的な左右関係を 360px 幅へ写像する。全ノードを
                # 中央へ置くと、段を飛び越す edge が中間ノードを貫通するため。
                desktop_x = base_x_by_id.get(node_id, 540.0)
                desktop_w = float(base.get("vb_w") or 1080.0)
                normalized = min(max(desktop_x / desktop_w, 0.0), 1.0)
                x_by_id[node_id] = min(max(
                    12.0 + nd_radius, 12.0 + normalized * 336.0,
                ), 348.0 - nd_radius)
            y_by_id[node_id] = y
            r_by_id[node_id] = nd_radius

    placed = []
    for nd in nodes:
        node_id = str(nd.get("id", ""))
        placed.append({
            **nd,
            "x": round(x_by_id.get(node_id, 180.0), 1),
            "y": round(y_by_id.get(node_id, top), 1),
            "r": round(r_by_id.get(node_id, 32.0), 1),
            "deg": degree.get(node_id, 1),
        })

    mobile = dict(base)
    mobile["nodes"] = placed
    mobile["vb_w"] = 360
    mobile["vb_h"] = int(round(vb_h))
    return mobile


def relations_svg(rel: dict[str, Any], layout: str = "desktop") -> str:
    """relations を SVG ネットワーク図 (ノード円 + ラベル付き有向エッジ) に描く。

    viewBox の高さ vb_h はラベル分離 (_resolve_labels) の結果で動的に拡張される
    ことがあるため、SVG ヘッダーは末尾で最終 vb_h を反映して parts[0] に prepend する
    ([[feedback_check_design_principles]] 1 段「失敗を表現できない構造に変える」)。
    """
    if layout == "desktop":
        lay = layout_relations(rel)
        is_mobile = False
    elif layout == "mobile":
        lay = _layout_relations_mobile(rel)
        is_mobile = True
    else:
        raise DeepDiveIncompleteError(
            f"relations layout must be 'desktop' or 'mobile', got {layout!r}"
        )
    nodes = lay["nodes"]
    by_id = {nd["id"]: nd for nd in nodes if "id" in nd}
    vb_w, vb_h = lay["vb_w"], lay["vb_h"]
    parts: list[str] = []   # SVG ヘッダーは末尾で最終 vb_h を反映して prepend する

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

    # ノードが無い水平帯 (zone) の中心 y を求める。ラベルはまずこの空白帯にスナップし、
    # その後 _resolve_labels が重なりを 0 にする (band 間にラベルを逃がす設計)。
    ys = sorted({round(nd["y"], 1) for nd in nodes})
    rmax_at: dict[float, float] = {}
    for nd in nodes:
        yk = round(nd["y"], 1)
        rmax_at[yk] = max(rmax_at.get(yk, 0.0), float(nd["r"]))
    zone_centers: list[float] = [max(chip_h, (ys[0] - rmax_at[ys[0]]) / 2)]
    for i in range(len(ys) - 1):
        zlo = ys[i] + rmax_at[ys[i]] + 8
        zhi = ys[i + 1] - rmax_at[ys[i + 1]] - 8
        zone_centers.append((zlo + zhi) / 2)
    last = ys[-1] + rmax_at[ys[-1]]
    zone_centers.append(min(vb_h - chip_h, last + (vb_h - last) / 2))

    def _anchor(x1: float, y1: float, x2: float, y2: float) -> tuple[float, float]:
        """エッジを、ノード無し zone の高さで横切る点 (ラベル初期位置) を返す。"""
        # 水平 (同 band 内) エッジ: そのバンドの「上の gap」(最上段なら上余白) に逃がす。
        # 外側の狭い余白へ押し込むと窮屈なので、内側の広い gap を優先する。
        if abs(y2 - y1) <= 1.0:
            mx = (x1 + x2) / 2
            if is_mobile:
                # 360px では水平 edge の chip 幅が端点ノードまで届きやすい。
                # 同段の空き帯へ逃がして、ノード円との重なりを構造的に避ける。
                bi = min(range(len(ys)), key=lambda i: abs(ys[i] - y1))
                return mx, zone_centers[bi]
            # 中央チャネルが空く水平エッジ (2 陣営の主役対立など) は gap へ逃がさず線上
            # (対立軸の真上) に載せ、上段の他エッジのラベルと混ざらないようにする。
            if all(math.hypot(mx - nd["x"], y1 - nd["y"]) > nd["r"] + 36 for nd in nodes):
                return mx, y1
            bi = min(range(len(ys)), key=lambda i: abs(ys[i] - y1))
            return mx, zone_centers[bi]
        mid_y = (y1 + y2) / 2
        ylo, yhi = min(y1, y2), max(y1, y2)
        cand = [c for c in zone_centers if ylo - 32 <= c <= yhi + 32]
        gc = min(cand or zone_centers, key=lambda c: abs(c - mid_y))
        t = min(max((gc - y1) / (y2 - y1), 0.12), 0.88)
        return x1 + (x2 - x1) * t, y1 + (y2 - y1) * t

    def _chip_w(kind: str, label: str) -> float:
        return 24 + _text_w(kind, fs) + (6 if label else 0) + _text_w(label, fs) + 14

    # ── edges: 線+矢印を全描画し、ラベルは spec として収集 (配置は後で一括解決) ──
    specs: list[dict[str, Any]] = []
    groups: dict[tuple[Any, str, str], dict[str, Any]] = {}
    for e in rel.get("edges", []):
        a, b = by_id.get(e.get("from")), by_id.get(e.get("to"))
        if not a or not b:
            continue
        ks = EDGE_KINDS[e.get("kind", "")]
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
        # 競合/対立 は相互関係なので既定で双方向矢印 (⇔)。出資/規制/供給は一方向のまま。
        bidirectional = e.get("dir") == "both" or (
            e.get("kind") in _RIVAL_KINDS and e.get("dir") not in ("one", "forward")
        )
        if bidirectional:
            parts.append(_arrow(x1, y1, -ux, -uy, color))
        # 同一 (from, kind, label) のエッジ群はラベルを 1 回だけ束ねる。
        # 物理的な _resolve_labels の収束を守るため、レンダラ側で truncate を確定させる。
        kind = e.get("kind", "")
        label = _truncate_label(e.get("label", ""))
        g = groups.setdefault(
            (e.get("from"), kind, label),
            {"color": color, "kind": kind, "label": label, "mids": [], "segs": []},
        )
        g["mids"].append(((x1 + x2) / 2, (y1 + y2) / 2))
        g["segs"].append((x1, y1, x2, y2))

    for g in groups.values():
        if len(g["mids"]) >= 2:   # 扇状の同文ラベル → 中点重心を最寄り zone へ
            cxm = sum(m[0] for m in g["mids"]) / len(g["mids"])
            cym = sum(m[1] for m in g["mids"]) / len(g["mids"])
            anx, an_y = cxm, min(zone_centers, key=lambda c: abs(c - cym))
        else:
            anx, an_y = _anchor(*g["segs"][0])
        specs.append({"cx": anx, "cy": an_y, "ax": anx, "ay": an_y,
                      "w": _chip_w(g["kind"], g["label"]), "h": chip_h,
                      "color": g["color"], "kind": g["kind"], "label": g["label"]})

    # ── ラベル重なり解消 → リーダ線 → チップ描画 ──
    circles = [(float(nd["x"]), float(nd["y"]), float(nd["r"])) for nd in nodes]
    # _resolve_labels は縦余地不足で AABB 分離が収束しないとき vb_h を動的に拡張する。
    # 確定後の vb_h で SVG ヘッダーを後置 prepend する (parts.insert(0, ...) は最後の手前)。
    base_vb_h = vb_h
    _, vb_h = _resolve_labels(specs, circles, vb_w, vb_h)
    if specs or circles:
        content_bottom = max(
            [cy + cr for _cx, cy, cr in circles]
            + [s["cy"] + s["h"] / 2 for s in specs]
        )
        # _resolve_labels は衝突回避のため一時的に高さを伸ばすが、解消後に
        # 実描画範囲外の巨大な下余白を残す必要はない。公開 SVG は本文中の
        # 図版として自然な余白へ trim する。
        vb_h = min(vb_h, max(base_vb_h, content_bottom + 48))
    for s in specs:   # アンカーから離れたチップは細いリーダ線で対応エッジを示す
        if math.hypot(s["cx"] - s["ax"], s["cy"] - s["ay"]) > 22:
            parts.append(
                f'<line x1="{s["ax"]:.1f}" y1="{s["ay"]:.1f}" x2="{s["cx"]:.1f}" '
                f'y2="{s["cy"]:.1f}" stroke="#B8B2A4" stroke-width="1" opacity="0.7"/>'
            )
    for s in specs:
        parts.append(_label_chip(s["cx"] - s["w"] / 2, s["cy"] - s["h"] / 2,
                                 s["w"], s["color"], s["kind"], s["label"]))

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
    # SVG ヘッダーを最終 vb_h で確定 (動的拡張済み) して先頭に挿入する。
    # mobile は専用 360px viewBox を viewport 幅へ収め、desktop の自然幅は従来どおり保つ。
    if is_mobile:
        svg_header = (
            f'<svg data-layout="mobile" viewBox="0 0 360 {vb_h:.0f}" width="100%" '
            f'height="{vb_h:.0f}" style="display:block;background:{PAPER}" role="img" '
            f'aria-label="{_esc(rel.get("title", "当事者の関係図"))}">'
        )
    else:
        svg_header = (
            f'<svg viewBox="0 0 {vb_w} {vb_h:.0f}" width="{vb_w}" height="{vb_h:.0f}" '
            f'style="display:block;background:{PAPER}" role="img" '
            f'aria-label="{_esc(rel.get("title", "当事者の関係図"))}">'
        )
    parts.insert(0, svg_header)
    parts.append("</svg>")
    svg = "".join(parts)
    # ── 層 2 出力品質ゲート (2026-06-06 plan v2): 線がノード貫通 / ラベル衝突を
    # 検出して build を中止する loud failure ([[feedback_check_design_principles]]
    # 1 段 + 2 段)。境界 1 箇所集約は tools/output_quality.py。
    from tools.output_quality import assert_quality, check_relations_svg
    title = rel.get("title", "(no title)")
    assert_quality([(
        f"relations:{title}",
        check_relations_svg(svg, src=title, strict_objects=is_mobile),
    )])
    return svg


def _arrow(x: float, y: float, ux: float, uy: float, color: str) -> str:
    s = 9
    bx, by = x - ux * s, y - uy * s
    px, py = -uy, ux
    p1 = f"{x:.1f},{y:.1f}"
    p2 = f"{bx + px * s * 0.5:.1f},{by + py * s * 0.5:.1f}"
    p3 = f"{bx - px * s * 0.5:.1f},{by - py * s * 0.5:.1f}"
    return f'<polygon points="{p1} {p2} {p3}" fill="{color}"/>'


# ── chart: bar / stacked_bar / line の SVG ────────────────────────────────────

_FALLBACK_SERIES_COLORS = [
    GOLD,
    "#2D5BB8",
    "#2E6B52",
    "#8E2A19",
    "#5E3D8C",
    "#3A7B8C",
    "#5A6B7B",
]


def _series_colors(accent: str, required: int = 0) -> list[str]:
    """系列色: 同一チャート内で凡例色が重複しないように割り当てる。"""
    colors: list[str] = []
    seen: set[str] = set()
    for color in [accent, *_FALLBACK_SERIES_COLORS]:
        key = color.lower()
        if key in seen:
            continue
        seen.add(key)
        colors.append(color)
    if required and required > len(colors):
        raise DeepDiveIncompleteError(
            f"chart の系列数 {required} に対し、識別可能な系列色が {len(colors)} 色しかありません。"
            "同一チャート内の凡例色重複を避けるため、系列を絞るか色パレットを追加してください。"
        )
    return colors


def _numeric_values(values: list[Any]) -> list[float]:
    out: list[float] = []
    for value in values:
        if isinstance(value, (int, float)):
            out.append(float(value))
    return out


def _validate_chart_information_gain(chart: dict[str, Any], ctype: str, series: list[dict[str, Any]]) -> None:
    """見た目だけのチャートを公開前に止める。"""
    if ctype != "line":
        return
    for item in series:
        values = _numeric_values(item.get("data", []))
        if len(values) >= 2 and len(set(values)) == 1:
            title = chart.get("title") or "チャート"
            name = item.get("name") or "series"
            raise DeepDiveIncompleteError(
                f"chart '{title}' の系列 '{name}' が全点同一です。"
                "本文で説明する変化を別系列または注釈値として入れるか、"
                "チャートではなく本文・KPIカードで表現してください。"
            )


def chart_svg(chart: dict[str, Any], accent: str = INK) -> str:
    """chart ブロックを SVG (棒 / 積み上げ棒 / 折れ線) に描く。

    スキーマは series:[{name,data}] (複数系列可)。単一系列なら凡例を出さない。
    1 本目の系列色はアクセント (カテゴリ色) を使う。
    """
    ctype = chart.get("type", "bar")
    cats = chart.get("categories", [])
    series = chart.get("series", [])
    if not series and "data" in chart:  # デザイン sample (単一 data 配列) も許容
        series = [{"name": chart.get("title", ""), "data": chart["data"]}]
    if not cats or not series:
        return ""
    colors = _series_colors(accent, len(series))
    _validate_chart_information_gain(chart, ctype, series)

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

# 関係図 (relations) の実描画ラベル数の上限。レンダラの _resolve_labels は band 間 gap
# に対しラベル数が多すぎると物理的にラベル重なりを 0 にできない (高密度クラスタの構造的限界)。
# 2026-06-04 ユーザー指示で「8 本以下に絞り込めない関係図は本質を選別できていない記事」
# と定義し、生成段階で hard fail させる ([[feedback_check_design_principles]] 1 段
# 「失敗を表現できない構造に変える」)。canonical edge は 1 本につき 1 chip として
# 描画するため、実描画ラベル数は edge 数で数える。
_MAX_RELATION_EDGES = 8


def _relation_label_count(rel: dict[str, Any]) -> int:
    """関係図の実描画ラベル数 (canonical edge 1 本につき 1 chip) を返す。"""
    return len([e for e in (rel or {}).get("edges", []) if e.get("from") and e.get("to")])


def _require_blocks(md_path: Path, blocks: dict[str, list[Any]]) -> None:
    """必須ブロックの欠落・関係図エッジ過剰を loud に弾く (= サイレントな空/破綻描画を許さない)。"""
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
    # 関係図 (relations) の実描画ラベル数は最大 _MAX_RELATION_EDGES。これを超えるとレンダラの
    # 力学分離が解けない (band 間 gap にラベルが収まらない・2026-06-04 ユーザー指摘)。
    # canonical edge は描画上 1 chip となるため、edge 本数でカウントする。
    rel = (blocks.get("relations") or [None])[0]
    n_labels = _relation_label_count(rel) if isinstance(rel, dict) else 0
    if n_labels > _MAX_RELATION_EDGES:
        raise DeepDiveIncompleteError(
            f"{name}: 関係図の実描画ラベルが {n_labels} 枚 (上限 {_MAX_RELATION_EDGES} 枚)。"
            "本質を絞り込めていない関係図は読めない。主要な対立軸・出資線・供給線に"
            "絞って描き直すこと (二面関係は提携 edge と競合 edge を別々に記述する)。"
        )
    # 関係図の孤立ノード (どの edge にも現れないノード) は hard fail。
    # 2026-06-04 ユーザー指摘: edge 8 枚上限に詰める過程で BCG ノードへの線が
    # 全部落ち、図の右側に「繋がっていない丸」が浮く事故が発生した。
    # 「ノードを置くなら edge を 1 本以上持たせる」が関係図の最小品質保証。
    # feedback_check_design_principles 1 段「失敗を表現できない構造」: 描画後の
    # 目視ではなく、ビルド時に loud failure させてサイレント公開を封じる。
    if isinstance(rel, dict):
        nodes = rel.get("nodes") or []
        edges = rel.get("edges") or []
        endpoints = set()
        for e in edges:
            if e.get("from"):
                endpoints.add(e["from"])
            if e.get("to"):
                endpoints.add(e["to"])
        orphans = [n.get("id") for n in nodes if n.get("id") and n.get("id") not in endpoints]
        if orphans:
            raise DeepDiveIncompleteError(
                f"{name}: 関係図に孤立ノード {orphans} (どの edge にも現れない)。"
                "ノードを置くなら必ず edge を 1 本以上持たせる。出さない理由が無いなら "
                "nodes から削除、出す理由があるなら edges を追加するか、近接ノードと "
                "ラベル統合 (例: McKinsey/BCG) して 1 ノードに畳む。edge 上限 "
                f"{_MAX_RELATION_EDGES} 枚と両立できないなら、独自情報の薄い edge を "
                "1 本削って枠を作る。"
            )


def build_deepdive_context(
    md_path: Path,
    *,
    validate_live_urls: bool = True,
) -> dict[str, Any]:
    """DeepDive md 1 件から Jinja テンプレ用 context を組み立てる。"""
    text = Path(md_path).read_text(encoding="utf-8")
    canonical_text = text.replace("\r\n", "\n").replace("\r", "\n")
    source_sha256 = hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()
    fm, body = parse_frontmatter(text)
    blocks = extract_blocks(body)
    _require_blocks(md_path, blocks)  # 必須ブロック欠落は hard fail (未完成記事を公開しない)
    # 参考リンク・timeline・relations/chart/table.source の URL を実機 HEAD で生存検証する。
    # 1 件でも 404 等 fatal があれば DeepDiveUrlError で公開を阻止。捏造 URL のサイレント
    # 公開を構造的に封じる (2026-06-03 三菱UFJ FX_Monthly 事故の恒久対策・境界 1 箇所集約)。
    # オフライン/CI 環境は NEWS_GRASP_SKIP_URL_CHECK=1 で全スキップできる。
    if validate_live_urls:
        require_live_urls(Path(md_path), text)
    sections = split_sections(body)

    date_str = fm.get("date", "")
    issue = fm.get("issue", "") or date_str.replace("-", "")
    title = fm.get("title", "")
    theme = fm.get("theme", "")
    tags = _parse_tags(text)

    canonical = f"{BASE_URL}/deepdive/{date_str}/"
    og_image = _absolutize(fm.get("og_image", "") or _DEEPDIVE_OG_IMAGE)

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

    # 参考リンク (脚注番号付き) を図の出典より先に用意し、各図の source を番号付き引用へ変換。
    # 複数出典は *N で個別リンク化し、末尾の参考リンク一覧の番号と紐付ける (2026-06-02)。
    biblio = parse_sources(refs_section)
    table_ctx = build_table(table) if table else None
    if table_ctx:
        table_ctx["source"] = _figure_citations(table_ctx.get("source", ""), biblio)

    timeline = blocks.get("timeline", [None])[0] or []
    if isinstance(timeline, dict) and isinstance(timeline.get("items"), list):
        timeline = timeline["items"]

    # 関連レポート (続報時のみ・任意ブロック)。URL と「左バー/下線のカテゴリ色」は date から
    # 導出し、手書きの誤記を構造的に排除する (md には date/title/relation/link/change だけ書く)。
    related = []
    for r in (blocks.get("related", [None])[0] or []):
        rd = str(r.get("date", "")).strip()
        # ① カテゴリ色 = 過去レポートの lens 色。date から過去 md を引いて解決し、
        #    md が見つからなければ near-black に退避する (URL と同じく date 由来で誤記を排除)。
        cat_accent = INK
        if rd:
            past = Path(md_path).parent / f"{rd}-DeepDive.md"
            if past.exists():
                ptext = past.read_text(encoding="utf-8")
                pfm, _ = parse_frontmatter(ptext)
                cat_accent = resolve_accent(_resolve_lens(pfm, _parse_tags(ptext)))
        rel_kind = str(r.get("relation", "")).strip()
        related.append({
            "date": rd,
            "date_dot": rd.replace("-", "."),
            "title": r.get("title", ""),
            "relation": rel_kind,
            "relation_color": _RELATION_STYLE.get(rel_kind, _DEFAULT_RELATION_COLOR),
            "link": r.get("link", ""),
            "change": r.get("change", ""),
            "accent": cat_accent,
            "url": f"{BASE_URL}/deepdive/{rd}/" if rd else "",
        })

    read_min = max(5, round(len(body) / 900))

    # 同じ relations データから desktop/mobile を独立生成する。テンプレート側は
    # viewport の media query だけで表示を切り替え、片方の座標を縮小して再利用しない。
    relations_svg_desktop = relations_svg(relations) if relations else ""
    relations_svg_mobile = relations_svg(relations, layout="mobile") if relations else ""
    relations_legend = layout_relations(relations).get("legend", []) if relations else []

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
        "source_sha256": source_sha256,
        "date": date_str,
        "date_dot": date_str.replace("-", "."),
        "issue": issue,
        "theme": theme,
        "tags": tags,
        "read_min": read_min,
        **deepdive_audio_for_pages(date_str, digest_dir=Path(md_path).parent),
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
        "timeline": timeline,
        "players": blocks.get("players", [None])[0] or [],
        "relations_svg": relations_svg_desktop,
        "relations_svg_mobile": relations_svg_mobile,
        "relations_legend": relations_legend,
        "relations_title": (relations or {}).get("title", "当事者の関係図"),
        "relations_source": _figure_citations((relations or {}).get("source", ""), biblio),
        "charts": [
            {
                "title": c.get("title", ""),
                "note": _chart_note(c),
                "source": _figure_citations(c.get("source", ""), biblio),
                "svg": chart_svg(c, accent),
            }
            for c in charts
        ],
        "table": table_ctx,
        "decision": decision,
        # prose
        "bg_prose": _prose_paragraphs(bg),
        "di_prose": _prose_paragraphs(di),
        "watch_prose": _prose_paragraphs(watch),
        "summary_prose": " ".join(_prose_paragraphs(summary_section)),
        "sources": biblio,
        # 関連レポート (続報の過去参照 + 変化点)。無い記事では [] でテンプレ非表示。
        "related": related,
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
    issue_date: str | None = None,
    validate_live_urls: bool = True,
) -> list[Path]:
    """DeepDiveを増分生成する。復旧時はissue_dateの1件だけを強制再生成する。"""
    if issue_date is not None and re.fullmatch(r"20\d{2}-\d{2}-\d{2}", issue_date) is None:
        raise ValueError(f"invalid issue_date: {issue_date}")
    docs = Path(docs_root) if docs_root else (_PKG_ROOT / "docs")
    src_dir = Path(digest_dir) if digest_dir else (_PKG_ROOT / "digest" / "DeepDive")
    if not src_dir.exists():
        return []
    written: list[Path] = []
    tmpl_mtime = _templates_mtime()  # テンプレ変更も増分判定に含める (generate_pages と同一境界)
    for src in sorted(src_dir.glob("*.md")):
        if issue_date is not None and src.name != f"{issue_date}-DeepDive.md":
            continue
        fm, _ = parse_frontmatter(src.read_text(encoding="utf-8"))
        if str(fm.get("kind", "")).strip() != "deepdive":
            continue
        try:
            ctx = build_deepdive_context(
                src,
                validate_live_urls=validate_live_urls,
            )
        except (DeepDiveIncompleteError, DeepDiveUrlError):
            raise  # 必須ブロック欠落・URL 生存検証 NG は握りつぶさず伝播 (= 未完成記事/捏造 URL の公開を構造的に阻止)
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] DeepDive context 構築失敗 {src.name}: {exc}", file=sys.stderr)
            continue
        if not ctx.get("date"):
            print(f"[skip] DeepDive date 欠落: {src.name}", file=sys.stderr)
            continue
        out = docs / "deepdive" / ctx["date"] / "index.html"
        if issue_date is None and not full and not _needs_rebuild(src, out, tmpl_mtime):
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
    if str(fm.get("kind", "")).strip() != "deepdive":
        return None
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
    ap.add_argument("--date", dest="issue_date", default=None)
    ap.add_argument("--docs-root", type=Path, default=None)
    a = ap.parse_args()
    paths = build_deepdive_pages(
        docs_root=a.docs_root,
        full=a.full,
        issue_date=a.issue_date,
    )
    print(f"wrote {len(paths)} DeepDive page(s)")
    for p in paths:
        print(f"  - {p}")
    arch = build_deepdive_archive(docs_root=a.docs_root)
    print(f"wrote theme archive: {arch}")
