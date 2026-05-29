#!/usr/bin/env python3
"""LP「本日のテーマ考察」を考察 lead 由来に正常化した改修の契約テスト。

背景 (2026-05-29):
    LP の Editorial プレビューは `editorial.summary_text` (= 本文先頭の [!summary]
    callout = 為替カテゴリ要約) を表示しており、「為替の話しかしていない・短すぎる」
    という指摘を受けた。本来の多カテゴリ横断の考察文 (lead) は summary digest の
    `## § 本日のテーマ考察` 直下 blockquote に存在するが `_extract_reflection` が
    スタブのため使われていなかった。

検証する意図:
    - reflection パーサが lead / subtitle / pull_quote / §NN / KEY TAKEAWAYS を取り出す
    - LP の Editorial ボックスが為替偏重の summary_text ではなく多カテゴリの lead を出す
    - LP では lead 末尾の定型遷移句が除去される (単体で読めるように)
    - summary ページが PULL QUOTE / 実 KEY TAKEAWAYS / §08 まで描画する

実行:
    pytest tests/test_reflection_theme_essay.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.generate_pages import (  # noqa: E402
    build_all,
    build_index,
    build_summary,
    parse_reflection,
    strip_inline,
    _theme_essay_for_home,
    _collect_entries,
)


# ============================================================
# fixtures
# ============================================================

_SUMMARY_DIGEST = """---
title: "News Grasp #20260529 — テスト"
date: 2026-05-29
issue: 20260529
weekday: 木
categoryId: summary
theme: "1兆ドルとCPI3.8%の衝突"
---

# News Grasp #20260529

> [!info] Today's Theme
> **1兆ドルとCPI3.8%の衝突** — AI評価額の膨張とインフレ再加速の根本矛盾。

---

## ¥ 1. 為替 Foreign Exchange

> [!summary]
> 米・イラン停戦延長合意でドル安・円高方向にシフト。財務省の介入実績月次公表が焦点。

---

## § 本日のテーマ考察

*AI評価額の膨張とインフレ再加速が交差した一日*

> 本日6分野・30件のニュースから浮かび上がる最大のテーマは[[Anthropic]]の評価額9,650億ドルと米CPI3.8%加速の同時進行である。AIへの資本流入が拡大する一方、[[Waymo]]のロボタクシー拡大や[[Goldman Sachs]]の強気目標にも同じ構図が反映される。__金融相場から実体相場へ__。以下、各カテゴリを横断して読み解く。

> [!quote] PULL QUOTE
> 「単一のAI企業の評価額」が「利下げ観測」を上回る速度で膨張する日。__金融政策の天井とAIの底なし井戸__。

### §01 総論 — 1兆ドルとCPI3.8%の同時撃

[[Anthropic]]の評価額が**9,650億ドル**に到達した同じ日、米インフレが**3.8%**を記録した。AI企業の夢の値段と中央銀行の現実の温度が乖離する構造矛盾が本日を貫く。

### §02 為替 — 東京CPI・介入実績・イラン合意の三重奏

[[米・イラン]]の停戦合意がドル安をもたらし、ドル円は159円台前半まで下落した。

### §03 AI — Anthropic1兆ドル、OpenAI上場申請

[[Anthropic]]が650億ドル調達で評価額9,650億ドルに。[[OpenAI]]はS-1を機密申請した。

### §04 IT — 日本IT大手3社がそろい踏み

[[NEC]]・[[日立]]・[[富士通]]がAnthropicと相次いで提携した。

### §05 モビリティ — Ojai・テキサス合法化・IEA 2300万台

[[Waymo]]の第6世代ロボタクシー「Ojai」が公開開放された。

### §06 経済 — S&P最高値・Goldman 8000

[[S&P500]]が最高値更新を継続する一方、[[日経平均]]は1,000円超安と乱高下した。

### §07 ゲーム — Forza 30万人・スクエニ10億円

[[Forza Horizon 6]]がSteam最高同接302,645人を記録した。

### §08 明日へ — 6月の焦点：日銀会合・Anthropic IPO観測

来週6月は日銀会合とOpenAI上場スケジュールが焦点となる。

### KEY TAKEAWAYS

- **[為替]** [[東京CPI速報+財務省介入実績]]という二重確認が揃い、日銀の6月利上げ判断は事実上今日決まる。
- **[AI]** [[Anthropic]] **9,650億ドル**・[[OpenAI]] S-1提出が同日重複。
- **[産業]** 国内IT大手3社がAnthropicとそろい踏みでAI導入を宣言。

---
"""

_CATEGORY_DIGEST = """---
title: "News Grasp #20260529 — {label}"
date: 2026-05-29
issue: 20260529
weekday: 木
categoryId: {cat_id}
---

# {LABEL}

> [!summary]
> {cat_id} カテゴリの本文サマリを 1 行で記述。

---

### [88] {label} テスト記事

📅 2026-05-29 不明 · 📰 Test Source · 🔗 [元記事](https://example.com/{cat_id})

#cat/{cat_id} #topic/test #score/高

- bullet 1
- bullet 2
"""


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """6 カテゴリ + summary digest を tmp に置き LP / summary ページを生成。"""
    root = tmp_path_factory.mktemp("reflection")
    docs = root / "docs"
    sources: list[Path] = []
    for cat_id, label in [("fx", "FX"), ("ai", "AI"), ("it", "IT-Consulting"),
                          ("mobility", "Mobility"), ("economy", "Economy"),
                          ("game", "Game")]:
        d = root / "digest" / label.upper()
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"2026-05-29-{label}.md"
        p.write_text(
            _CATEGORY_DIGEST.format(label=label, LABEL=label.upper(), cat_id=cat_id),
            encoding="utf-8",
        )
        sources.append(p)
    sd = root / "digest" / "Summary"
    sd.mkdir(parents=True, exist_ok=True)
    sp = sd / "2026-05-29.md"
    sp.write_text(_SUMMARY_DIGEST, encoding="utf-8")
    sources.append(sp)

    build_all(full=True, docs_root=docs, digests=sources)
    entries = _collect_entries(sources)
    home = build_index(entries, docs).read_text(encoding="utf-8")
    summary = build_summary("2026-05-29", entries, docs).read_text(encoding="utf-8")
    return {"home": home, "summary": summary}


# ============================================================
# pure unit tests
# ============================================================

def test_strip_inline_removes_markup():
    assert strip_inline("[[Anthropic]]の**評価額**が__9650億__") == "Anthropicの評価額が9650億"
    assert strip_inline("[[USD/JPY|ドル円]]は159円") == "ドル円は159円"
    assert strip_inline("") == ""


def test_theme_essay_for_home_strips_trailer_and_markup():
    lead = ("[[Anthropic]]と米CPIの同時進行である。"
            "以下、各カテゴリを横断して読み解く。")
    out = _theme_essay_for_home(lead)
    assert out.endswith("同時進行である。"), out
    assert "以下" not in out
    assert "[[" not in out


def test_parse_reflection_extracts_all_blocks():
    r = parse_reflection(_SUMMARY_DIGEST)
    # lead = 多カテゴリ横断の考察文 (為替単独ではない)
    assert "9,650億ドル" in r["lead"]
    assert "Waymo" in r["lead"]            # AI 以外のカテゴリにも言及
    assert "以下、各カテゴリを横断して読み解く" in r["lead"]  # 末尾遷移句は raw には残る
    # subtitle (斜体)
    assert "交差した一日" in r["subtitle"]
    # pull_quote
    assert "単一のAI企業の評価額" in r["pull_quote"]["text"]
    # sections: §01-§08 (モビリティ含む 8 件)
    assert len(r["sections"]) == 8
    assert "モビリティ" in r["sections"][5]["heading"]
    assert "明日" in r["sections"][8]["heading"]
    assert "9,650億ドル" in r["sections"][1]["body"]
    # takeaways: 3 件、tag 付き
    assert len(r["takeaways"]) == 3
    assert r["takeaways"][0]["tag"] == "為替"
    assert r["takeaways"][2]["tag"] == "産業"


def test_parse_reflection_empty_on_plain_digest():
    """考察ブロックの無い digest では各値が空 (fallback に委ねる)。"""
    r = parse_reflection("# foo\n\n> [!summary]\n> bar\n")
    assert r["lead"] == ""
    assert r["sections"] == {}
    assert r["takeaways"] == []
    assert r["pull_quote"]["text"] == ""


# ============================================================
# LP (build_index) 結合
# ============================================================

def test_home_editorial_shows_multicategory_essay(built):
    """LP の Editorial ボックスが多カテゴリ横断の考察文を出す (為替単独ではない)。"""
    home = built["home"]
    assert "home-editorial" in home
    assert "9,650億ドル" in home          # 考察 lead の内容
    assert "Waymo" in home                # AI 以外のカテゴリも考察に含まれる
    assert "Goldman Sachs" in home


def test_home_editorial_renders_emphasis(built):
    """本日のテーマ考察に太字/下線/マーカー強調が描画され、生の markdown 記法は漏れない。"""
    home = built["home"]
    assert "emph-bold" in home            # [[ ]] マーカー (固有名詞/数値)
    assert "emph-und" in home             # __ __ 下線 (含意フレーズ)
    assert "[[" not in home               # 生 wikilink 記法が素通りしていない
    assert "__金融相場" not in home        # 生 underline 記法が素通りしていない


def test_home_editorial_strips_trailing_transition(built):
    """LP では lead 末尾の「以下、各カテゴリを横断して読み解く」を除去する。"""
    assert "以下、各カテゴリを横断して読み解く" not in built["home"]


def test_home_theme_phrases_from_frontmatter(built):
    """Hero / Editorial subtitle のフレーズが frontmatter theme 由来 (為替語句ではない)。"""
    home = built["home"]
    assert "1兆ドル" in home
    assert "CPI3.8%の衝突" in home


# ============================================================
# summary ページ (build_summary) 結合
# ============================================================

def test_summary_pull_quote_rendered(built):
    """考察に PULL QUOTE があれば summary-pull セクションが出る。"""
    summary = built["summary"]
    assert 'class="summary-pull"' in summary
    assert "単一のAI企業の評価額" in summary


def test_summary_renders_8_sections_with_mobility(built):
    """digest の §01-§08 を data-driven 描画。モビリティ・明日へが出る。"""
    summary = built["summary"]
    assert "§08" in summary
    assert "§05" in summary
    assert ">モビリティ<" in summary
    assert ">明日へ<" in summary
    # §01 総論本文 (全文表示)。9,650億ドルは <strong> で囲まれるため後続テキストで確認。
    assert "に到達した同じ日" in summary


def test_summary_takeaways_from_digest(built):
    """KEY TAKEAWAYS が digest の実 3 結論 (Top3 記事タイトル流用ではない)。"""
    summary = built["summary"]
    assert "二重確認" in summary          # 為替 takeaway 本文
    assert "産業" in summary              # digest 固有タグ (記事流用なら出ない)
