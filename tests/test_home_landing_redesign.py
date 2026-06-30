from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import generate_pages as gp  # noqa: E402


DATE = "2026-07-01"


def _card_segment(html: str, category_id: str) -> str:
    match = re.search(
        rf'<a\b[^>]*data-category-card="{re.escape(category_id)}"[\s\S]*?</a>',
        html,
    )
    assert match, f"category card missing: {category_id}"
    return match.group(0)


def _entry(category_id: str, *, score: int, thumb: bool = True, count: int = 3) -> dict:
    meta = gp.CATEGORIES[category_id]
    return {
        "title": f"{meta['jp']} Digest",
        "date": DATE,
        "category_id": category_id,
        "category_label": meta["label"],
        "category_jp": meta["jp"],
        "canonical": f"{gp.BASE_URL}/{DATE}/{category_id}/",
        "summary_text": f"{meta['jp']}は[[重要論点]]を中心に制度・供給・販売を確認する。",
        "theme": "",
        "hero_left": "",
        "hero_right": "",
        "reflection": {},
        "og_image": "",
        "accent": meta["accent"],
        "glyph": meta["glyph"],
        "top_score": score,
        "top_title": f"{meta['jp']} トップ記事",
        "top_title_ja": f"{meta['jp']}の注目論点",
        "top_thumb": f"https://example.test/{category_id}.jpg" if thumb else "",
        "top_source": "Example News",
        "top_source_url": f"https://example.test/{category_id}",
        "top_date": DATE,
        "top_bullets": [
            gp.inline_html(f"【事実・概要】：{meta['jp']}の主要ニュースを確認。"),
            gp.inline_html("【背景・要点】：供給条件と制度対応が焦点。"),
            gp.inline_html("【影響・展望】：明日の判断材料を整理。"),
        ],
        "top_tags": [],
        "score_note": "",
        "score_signals": [],
        "key_numbers": [],
        "articles_count": count,
        "scores": [score],
        "top3": [],
    }


def _categories_with_security() -> dict:
    cats = {cid: meta.copy() for cid, meta in gp.CATEGORIES.items() if cid != "summary"}
    cats["security"] = {
        "label": "Security",
        "jp": "セキュリティ",
        "accent": "#6F4B8B",
        "glyph": "◇",
    }
    cats["summary"] = gp.CATEGORIES["summary"].copy()
    return cats


def _synthetic_entries(cats: dict) -> list[dict]:
    entries = []
    for index, category_id in enumerate([cid for cid in cats if cid != "summary"]):
        entries.append(
            _entry(
                category_id,
                score=max(0, 98 - index * 8),
                thumb=category_id not in {"game"},
                count=0 if category_id == "game" else 3,
            )
        )
    entries.append(
        {
            **_entry("summary", score=1, thumb=False, count=0),
            "canonical": f"{gp.BASE_URL}/{DATE}/summary/",
            "summary_text": "本日の全体論点を整理する。",
            "reflection": {
                "lead": (
                    "今日は[[AI投資]]とモビリティ制度が同時に前進した。"
                    "__制度整備__と**供給網**の再設計が各カテゴリに波及する。"
                    "明日は販売・標準・人材配置の実装力が差になる。"
                ),
                "sections": [],
            },
        }
    )
    return entries


@pytest.fixture()
def synthetic_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    cats = _categories_with_security()
    monkeypatch.setattr(gp, "CATEGORIES", cats)

    entries = _synthetic_entries(cats)
    out = gp.build_index(entries, tmp_path)
    return out.read_text(encoding="utf-8")


def test_home_categories_render_all_cards_with_thumbnail_and_layout(synthetic_home: str) -> None:
    for category_id in ("fx", "ai", "it", "mobility", "manufacturing", "economy", "game", "security"):
        assert f'data-category-card="{category_id}"' in synthetic_home

    assert synthetic_home.count('data-category-card="') == 8
    assert "home-cat-card__thumb" in synthetic_home
    assert "home-cat-card__thumb-img" in synthetic_home

    game = _card_segment(synthetic_home, "game")
    assert "home-cat-card--wide" in game
    assert "home-cat-card--rest" in game
    assert "NO PHOTO" in game
    assert "home-cat-card__score" not in game

    security = _card_segment(synthetic_home, "security")
    assert "home-cat-card--standard" in security
    assert "home-cat-card--wide" not in security


def test_home_editorial_uses_three_lanes_and_existing_emphasis(synthetic_home: str) -> None:
    assert "home-editorial__masthead" in synthetic_home
    assert "home-editorial__panel" in synthetic_home
    assert "home-editorial-lane__icon" not in synthetic_home
    assert 'data-editorial-lanes="true"' in synthetic_home
    for index, lane in enumerate(("fact", "context", "outlook"), start=1):
        assert f'data-editorial-lane="{lane}"' in synthetic_home
        assert f"home-editorial-lane__shape--{lane}" in synthetic_home
        assert f">{index:02d}</div>" in synthetic_home

    editorial = synthetic_home.split('data-editorial-lanes="true"', 1)[1].split(
        "home-editorial__cta", 1
    )[0]
    assert "[[" not in editorial
    assert "__制度整備__" not in editorial
    assert "**供給網**" not in editorial
    assert '<strong class="emph-bold">AI投資</strong>' in editorial
    assert '<span class="emph-und">制度整備</span>' in editorial
    assert "<strong>供給網</strong>" in editorial


def test_home_editorial_strips_decorated_info_callout_heading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cats = _categories_with_security()
    monkeypatch.setattr(gp, "CATEGORIES", cats, raising=False)
    entries = _synthetic_entries(cats)
    summary_entry = next(e for e in entries if e["category_id"] == "summary")
    summary_entry["reflection"]["lead"] = (
        "*__*[!info] Today's Theme**__ "
        "今日は、[[AI]] の計算資源が並び、**機能の派手さ**より "
        "__前提条件__ が主役でした。[[FX]] は月末フローを見ます。"
    )

    out = gp.build_index(entries, tmp_path).read_text(encoding="utf-8")
    editorial = out.split('data-editorial-lanes="true"', 1)[1].split(
        "home-editorial__cta", 1
    )[0]
    visible_text = re.sub(r"<[^>]+>", "", editorial)

    assert "[!info]" not in visible_text
    assert "Today's Theme" not in visible_text
    assert "*__*" not in visible_text
    assert "**" not in visible_text
    assert "__" not in visible_text
    assert '<strong class="emph-bold">AI</strong>' in editorial
    assert "<strong>機能の派手さ</strong>" in editorial
    assert '<span class="emph-und">前提条件</span>' in editorial
