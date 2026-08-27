from __future__ import annotations

import json
import re
import subprocess
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
            gp.inline_html(
                f"【事実・概要】：{meta['jp']}の[[重要論点]]を中心に、"
                "__制度整備__と**供給網**の変化をカード内で最後まで確認できる。"
            ),
            gp.inline_html(
                "【背景・要点】：供給条件と制度対応が焦点で、"
                "短い導線だけではなく最低限の判断材料を持ち帰れる。"
            ),
            gp.inline_html("【影響・展望】：明日の判断材料を整理。"),
        ],
        "top_tags": ["制度整備", "co/キヤノンITソリューションズ", "供給網"],
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
    assert "home-cat-card--rest" in game
    assert "NO PHOTO" in game
    assert "home-cat-card__score" not in game

    security = _card_segment(synthetic_home, "security")
    assert "home-cat-card--wide" in security


def test_home_category_cards_use_3d_structured_body(synthetic_home: str) -> None:
    """by Category 3d は長文 summary ではなくメタ・代表見出し・論点・キーワードで読ませる。"""
    fx = _card_segment(synthetic_home, "fx")
    assert "home-cat-card__meta" in fx
    assert "home-cat-card__top-title" in fx
    assert "home-cat-card__points" in fx
    assert fx.count("home-cat-card__point") >= 2
    assert "home-cat-card__keywords" in fx
    assert "home-cat-card__keyword" in fx
    assert "Example News" in fx
    assert "FX トップ記事" not in fx
    assert "為替 トップ記事" in fx
    assert "制度整備" in fx
    assert "キヤノンITソリューションズ" in fx
    assert "供給網" in fx
    assert "カード内で最後まで確認できる" in fx
    assert "最低限の判断材料を持ち帰れる" in fx
    assert "…" not in fx
    assert '<strong class="emph-bold">重要論点</strong>' in fx
    assert '<span class="emph-und">制度整備</span>' in fx
    assert "<strong>供給網</strong>" in fx
    assert "MORE 3 STORIES" in fx
    assert "次の一手まで読む" in fx
    assert "この先の論点を見る" not in fx
    assert "詳細はこちら" not in fx
    assert "home-cat-card__summary" not in fx
    assert "[[" not in fx


def test_home_category_rest_card_uses_no_issue_fallback(synthetic_home: str) -> None:
    """0件カテゴリはカードを落とさず、休載状態として成立させる。"""
    game = _card_segment(synthetic_home, "game")
    assert "home-cat-card--rest" in game
    assert "本日休載" in game
    assert "NO ISSUE" in game
    assert "home-cat-card__foot" not in game


def test_home_editorial_uses_three_lanes_and_existing_emphasis(synthetic_home: str) -> None:
    assert "home-editorial__masthead" not in synthetic_home
    assert "━━ 2b" not in synthetic_home
    assert "home-editorial__panel" in synthetic_home
    assert "home-editorial-lane__icon" not in synthetic_home
    assert 'data-editorial-lanes="true"' in synthetic_home
    for index, lane in enumerate(("fact", "context", "outlook"), start=1):
        assert f'data-editorial-lane="{lane}"' in synthetic_home
        assert f"home-editorial-lane__shape--{lane}" in synthetic_home
        assert f">{index:02d}</div>" in synthetic_home
    for label in ("FACT", "CONTEXT", "OUTLOOK", "事実・概要", "背景・要点", "影響・展望"):
        assert label in synthetic_home

    editorial = synthetic_home.split('data-editorial-lanes="true"', 1)[1].split(
        "home-editorial__cta", 1
    )[0]
    assert "[[" not in editorial
    assert "__制度整備__" not in editorial
    assert "**供給網**" not in editorial
    assert '<strong class="emph-bold">AI投資</strong>' in editorial
    assert '<span class="emph-und">制度整備</span>' in editorial
    assert "<strong>供給網</strong>" in editorial


def test_home_editorial_lane_labels_are_readable_size() -> None:
    css = (ROOT / "docs" / "assets" / "site.css").read_text(encoding="utf-8")
    en = re.search(r"\.home-editorial-lane__en\s*\{[^}]*font-size:\s*(\d+)px", css)
    jp = re.search(r"\.home-editorial-lane__jp\s*\{[^}]*font-size:\s*(\d+)px", css)
    assert en, "FACT / CONTEXT / OUTLOOK label font-size is missing"
    assert jp, "Japanese lane helper label font-size is missing"
    assert int(en.group(1)) >= 14
    assert int(jp.group(1)) >= 13


def test_home_category_card_text_is_readable_and_not_css_truncated() -> None:
    css = (ROOT / "docs" / "assets" / "site.css").read_text(encoding="utf-8")
    title = re.search(r"\.home-cat-card__top-title\s*\{(?P<body>[^}]*)\}", css)
    point = re.search(r"\.home-cat-card__point span\s*\{(?P<body>[^}]*)\}", css)
    meta = re.search(r"\.home-cat-card__meta\s*\{(?P<body>[^}]*)\}", css)
    assert title and point and meta
    title_body = title.group("body")
    point_body = point.group("body")
    meta_body = meta.group("body")
    assert int(float(re.search(r"font-size:\s*([\d.]+)px", title_body).group(1))) >= 18
    assert int(float(re.search(r"font-size:\s*([\d.]+)px", point_body).group(1))) >= 15
    assert float(re.search(r"font-size:\s*([\d.]+)px", meta_body).group(1)) >= 11
    assert "-webkit-line-clamp" not in title_body
    assert "text-overflow" not in point_body
    assert "-webkit-line-clamp" not in point_body


def test_home_category_cards_computed_visual_contract(
    synthetic_home: str,
    tmp_path: Path,
) -> None:
    """By Category の本文領域を、実CSS適用後の computed style で固定する。"""
    html_path = tmp_path / "home.html"
    html_path.write_text(synthetic_home, encoding="utf-8")
    css_path = ROOT / "docs" / "assets" / "site.css"
    script = r"""
const fs = require('fs');
const { chromium } = require('playwright');

const [htmlPath, cssPath] = process.argv.slice(1);
const html = fs.readFileSync(htmlPath, 'utf8');
const css = fs.readFileSync(cssPath, 'utf8');
const styleTag = `<style>${css}</style>`;
const documentHtml = html.includes('site.css')
  ? html.replace(/<link[^>]+site\.css[^>]*>/, styleTag)
  : html.replace('</head>', `${styleTag}</head>`);

async function launchBrowser() {
  try {
    return await chromium.launch({ channel: 'chrome', headless: true });
  } catch (error) {
    return await chromium.launch({ headless: true });
  }
}

(async () => {
  const browser = await launchBrowser();
  const viewports = [
    { name: 'desktop', width: 1366, height: 900 },
    { name: 'mobile', width: 390, height: 844 },
  ];
  const results = [];
  for (const viewport of viewports) {
    const page = await browser.newPage({ viewport });
    await page.setContent(documentHtml, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('.home-cats');
    results.push(await page.evaluate((name) => {
      const cats = document.querySelector('.home-cats');
      const pointSpans = Array.from(document.querySelectorAll('.home-cat-card__point span'));
      const titles = Array.from(document.querySelectorAll('.home-cat-card__top-title'));
      const pointSizes = pointSpans.map((node) => parseFloat(getComputedStyle(node).fontSize));
      const titleSizes = titles.map((node) => parseFloat(getComputedStyle(node).fontSize));
      return {
        viewport: name,
        pointCount: pointSpans.length,
        titleCount: titles.length,
        minPointFontSize: Math.min(...pointSizes),
        minTitleFontSize: Math.min(...titleSizes),
        noEllipsisInHomeCats: !cats.innerText.includes('…'),
        emphasisCount: cats.querySelectorAll('.home-cat-card__point .emph-bold, .home-cat-card__point .emph-und, .home-cat-card__point strong').length,
        horizontalOverflowPx: document.documentElement.scrollWidth - window.innerWidth,
      };
    }, viewport.name));
    await page.close();
  }
  await browser.close();
  const failures = results.filter((result) =>
    result.pointCount < 1 ||
    result.titleCount < 1 ||
    result.minPointFontSize < 13 ||
    result.minTitleFontSize < 16 ||
    !result.noEllipsisInHomeCats ||
    result.emphasisCount < 1 ||
    result.horizontalOverflowPx > 1
  );
  if (failures.length > 0) {
    console.error(JSON.stringify({ results, failures }, null, 2));
    process.exit(1);
  }
  console.log(JSON.stringify({ results }, null, 2));
    })();
    """
    try:
        playwright_probe = subprocess.run(
            ["node", "-e", "require.resolve('playwright')"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except FileNotFoundError as error:
        pytest.fail(f"Node.js is required for the computed visual contract: {error}")
    if playwright_probe.returncode != 0:
        if "Cannot find module 'playwright'" in playwright_probe.stderr:
            pytest.skip("Playwright npm module is not installed")
        pytest.fail(playwright_probe.stderr or playwright_probe.stdout)
    result = subprocess.run(
        ["node", "-e", script, str(html_path), str(css_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    metrics = json.loads(result.stdout)
    assert {item["viewport"] for item in metrics["results"]} == {"desktop", "mobile"}
    for item in metrics["results"]:
        assert item["minPointFontSize"] >= 15
        assert item["minTitleFontSize"] >= 18


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
