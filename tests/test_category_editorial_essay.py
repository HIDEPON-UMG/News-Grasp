#!/usr/bin/env python3
"""カテゴリページ「本日のテーマ考察」を "カテゴリ固有" の §NN 考察に正常化した改修の契約テスト。

背景 (2026-05-30):
    直前修正 (ba16084) で各カテゴリページ docs/{cat}/index.html の「本日のテーマ考察」
    navy band に、日全体の総論 (reflection.lead) を流し込んでしまい、6 カテゴリすべてが
    同一文になっていた。本来カテゴリページの考察は、summary digest の `### §NN` のうち
    見出しラベルが当該カテゴリに一致する節の body (= カテゴリ固有) を出すべき。

検証する意図 (= この class of bugs を 1 テストで封じる):
    - FX ページは §為替 の固有フレーズを含む
    - AI ページは §AI の固有フレーズを含み、§為替の固有フレーズは含まない (= ページごとに分かれる)
    - IT ページは §IT の固有フレーズを含む (3 カテゴリが互いに異なる本文)
    - 日全体の総論 lead 固有フレーズは "どのカテゴリページにも漏れない" (lead 流用への回帰防止)
    - 考察 body の装飾記法 ([[ ]] / __ __ / **) が render_emph で描画される (生マーカーが残らない)
    - 該当 §NN が無いカテゴリは featured.summary_text に fallback する

実行:
    pytest tests/test_category_editorial_essay.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.generate_pages import (  # noqa: E402
    build_all,
    build_category_pages,
    parse_reflection,
    _category_essay,
    _section_label_to_cid,
    _collect_entries,
    _get_jinja_env,
)


# ============================================================
# fixtures
# ============================================================
# §NN 見出しラベルは TAG_TO_CID (為替→fx / AI→ai / IT→it / モビリティ→mobility /
# 経済→economy / ゲーム→game) で cid に解決される。各 body / heading に "そのカテゴリだけ"
# に出る sentinel を仕込み、ページごとに内容が分かれることを文字列一致で検証する。
_SUMMARY_DIGEST = """---
title: "News Grasp #20260530 — テスト"
date: 2026-05-30
issue: 20260530
weekday: 金
categoryId: summary
theme: "為替とAIの交差"
---

# News Grasp #20260530

> [!summary]
> 日全体の [!summary] 本文。

---

## § 本日のテーマ考察

*日全体の総論サブタイトル*

> 本日の総論リード。SENTINEL_LEAD_ONLY が含まれる多カテゴリ横断の文。以下、各カテゴリを横断して読み解く。

### §01 総論 — 全体俯瞰
[[総論]]の本文。SENTINEL_SOURON。

### §02 為替 — 介入と東京CPI
[[ドル円]]は**159円**台へ。SENTINEL_FX_BODY。

### §03 AI — Anthropic評価額の膨張
[[Anthropic]]が__9650億ドル__に到達。[[R&D]]投資も拡大。SENTINEL_AI_BODY。

### §04 IT — 国内3社がそろい踏み
[[NEC]]がAnthropicと提携。SENTINEL_IT_BODY。

### §05 モビリティ — Waymo拡大
[[Waymo]]がOjaiを公開。SENTINEL_MOBILITY_BODY。

### §06 経済 — S&P最高値
[[S&P500]]が最高値。SENTINEL_ECONOMY_BODY。

### §07 ゲーム — Forza記録
[[Forza]]がSteam最高同接。SENTINEL_GAME_BODY。

### §08 明日へ — 来週の焦点
来週の焦点。

### KEY TAKEAWAYS

- **[為替]** 為替結論。
- **[AI]** AI結論。
- **[産業]** 産業結論。

---
"""

# モビリティは §NN を持つが、後述 fallback 検証用に "game" を §NN から落とした派生は使わず、
# fallback は純関数 _category_essay で別途検証する (ページ build を増やさずトークン節約)。
_CATEGORY_DIGEST = """---
title: "News Grasp #20260530 — {label}"
date: 2026-05-30
issue: 20260530
weekday: 金
categoryId: {cat_id}
---

# {LABEL}

> [!summary]
> {cat_id} カテゴリ自身の [!summary] サマリ (フォールバック用)。

---

### [88] {label} テスト記事

📅 2026-05-30 不明 · 📰 Test Source · 🔗 [元記事](https://example.com/{cat_id})

#cat/{cat_id} #topic/test #score/高

- bullet 1
- bullet 2
"""


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """6 カテゴリ + summary digest を tmp に置き、カテゴリページを生成して HTML を読む。"""
    root = tmp_path_factory.mktemp("cat_editorial")
    docs = root / "docs"
    sources: list[Path] = []
    for cat_id, label in [("fx", "FX"), ("ai", "AI"), ("it", "IT-Consulting"),
                          ("mobility", "Mobility"), ("manufacturing", "Manufacturing"),
                          ("economy", "Economy"), ("game", "Game")]:
        d = root / "digest" / label.upper()
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"2026-05-30-{label}.md"
        p.write_text(
            _CATEGORY_DIGEST.format(label=label, LABEL=label.upper(), cat_id=cat_id),
            encoding="utf-8",
        )
        sources.append(p)
    sd = root / "digest" / "Summary"
    sd.mkdir(parents=True, exist_ok=True)
    sp = sd / "2026-05-30.md"
    sp.write_text(_SUMMARY_DIGEST, encoding="utf-8")
    sources.append(sp)

    build_all(full=True, docs_root=docs, digests=sources)
    entries = _collect_entries(sources)
    build_category_pages(entries, docs)
    return {
        "fx": (docs / "fx" / "index.html").read_text(encoding="utf-8"),
        "ai": (docs / "ai" / "index.html").read_text(encoding="utf-8"),
        "it": (docs / "it" / "index.html").read_text(encoding="utf-8"),
    }


# ============================================================
# pure unit tests (ラベル→cid 解決と body 引き当て)
# ============================================================

def test_section_label_to_cid():
    assert _section_label_to_cid("為替 — 東京CPI・介入実績") == "fx"
    assert _section_label_to_cid("AI — Anthropic1兆ドル") == "ai"
    assert _section_label_to_cid("IT — 国内3社") == "it"
    assert _section_label_to_cid("モビリティ — Waymo") == "mobility"
    assert _section_label_to_cid("経済 — S&P") == "economy"
    assert _section_label_to_cid("ゲーム — Forza") == "game"
    # 総論 / 明日へ / 未知ラベルは None (カテゴリに対応しない)
    assert _section_label_to_cid("総論 — 全体俯瞰") is None
    assert _section_label_to_cid("明日へ — 来週") is None
    assert _section_label_to_cid("") is None


def test_category_essay_picks_matching_section():
    reflection = parse_reflection(_SUMMARY_DIGEST)
    fx_head, fx_body = _category_essay(reflection, "fx")
    ai_head, ai_body = _category_essay(reflection, "ai")
    assert "SENTINEL_FX_BODY" in fx_body
    assert "SENTINEL_AI_BODY" not in fx_body          # FX は AI 節を拾わない
    assert "SENTINEL_AI_BODY" in ai_body
    assert fx_head.startswith("為替")
    assert ai_head.startswith("AI")
    # 装飾記法は body にそのまま残る (render_emph はテンプレ側)
    assert "__9650億ドル__" in ai_body


def test_category_essay_fallback_when_no_section():
    """該当 §NN が無ければ ("", "") を返す (呼び出し側で summary_text に fallback)。"""
    assert _category_essay({}, "fx") == ("", "")
    assert _category_essay({"sections": {}}, "ai") == ("", "")
    # 総論しか無い reflection では fx は引けない
    only_souron = {"sections": {1: {"heading": "総論 — 全体", "body": "x"}}}
    assert _category_essay(only_souron, "fx") == ("", "")


# ============================================================
# integration: 生成済カテゴリページ HTML
# ============================================================

def test_each_category_page_shows_its_own_essay(built):
    """FX / AI / IT ページがそれぞれ "自分の" 節本文を出す。"""
    assert "SENTINEL_FX_BODY" in built["fx"]
    assert "SENTINEL_AI_BODY" in built["ai"]
    assert "SENTINEL_IT_BODY" in built["it"]


def test_category_pages_do_not_share_essay(built):
    """6 ページ同一文だったバグの回帰防止: 他カテゴリの節本文が混ざらない。"""
    assert "SENTINEL_AI_BODY" not in built["fx"]
    assert "SENTINEL_IT_BODY" not in built["fx"]
    assert "SENTINEL_FX_BODY" not in built["ai"]
    assert "SENTINEL_IT_BODY" not in built["ai"]
    assert "SENTINEL_FX_BODY" not in built["it"]
    assert "SENTINEL_AI_BODY" not in built["it"]


def test_category_pages_do_not_leak_daywide_lead(built):
    """日全体の総論 lead 固有フレーズはカテゴリページに漏れない (lead 流用への回帰防止)。"""
    for html in (built["fx"], built["ai"], built["it"]):
        assert "SENTINEL_LEAD_ONLY" not in html
        assert "SENTINEL_SOURON" not in html  # §01 総論本文も出さない


def test_category_essay_renders_emphasis(built):
    """考察 body の装飾 ([[ ]] / __ __) が描画され、生マーカーが漏れない。"""
    ai = built["ai"]
    assert "emph-bold" in ai                      # [[Anthropic]] マーカー
    assert "emph-und" in ai                       # __9650億ドル__ 下線
    assert "[[Anthropic]]" not in ai              # 生 wikilink が素通りしていない
    assert "__9650億ドル__" not in ai             # 生 underline が素通りしていない
    # [[R&D]] が単一エスケープで出る (二重エスケープ R&amp;amp;D に化けない)
    assert "R&amp;D" in ai
    assert "R&amp;amp;D" not in ai


def test_render_emph_no_double_escape():
    """render_emph が [[ ]] 内の & < > を二重エスケープしない回帰テスト。

    バグ (2026-05-30): _render_emph が文字列全体を escape した後、wikilink 捕捉群を
    もう一度 escape していたため [[S&P500]] が S&amp;amp;P500 と二重化し、画面に
    「S&amp;P500」と化けて表示されていた (economy ページ / 既存 summary・LP も同様)。
    この class of bug を render_emph 単体で 1 件封じる。
    """
    render_emph = _get_jinja_env().filters["render_emph"]
    out = str(render_emph("[[S&P500]]が最高値"))
    assert "S&amp;P500" in out            # 単一エスケープ (正しい)
    assert "S&amp;amp;P500" not in out    # 二重エスケープしない
    # 素テキスト中の & も単一エスケープのまま
    assert str(render_emph("AT&T")) == "AT&amp;T"
    # < > を含む wikilink も単一
    lt = str(render_emph("[[<tag>]]"))
    assert "&lt;tag&gt;" in lt
    assert "&amp;lt;" not in lt


def test_category_essay_subtitle_is_per_category(built):
    """見出し直下サブタイトル (§NN heading) がカテゴリごとに異なる。"""
    assert 'class="editorial__subtitle"' in built["fx"]
    assert "介入と東京CPI" in built["fx"]          # §為替 heading 副題
    assert "Anthropic評価額の膨張" in built["ai"]  # §AI heading 副題
    assert "介入と東京CPI" not in built["ai"]      # FX の副題は AI に出ない
