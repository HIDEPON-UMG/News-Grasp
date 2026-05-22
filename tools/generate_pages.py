#!/usr/bin/env python3
"""News-Grasp 公開 web 配信 SSG (context builder + Jinja2 renderer)。

実装ステップ 6 (2026-05-21): context builder のみ。
実装ステップ 7 (2026-05-21): page-template.html を Jinja2 で render し
                              docs/{cat}/{YYYY-MM-DD}/index.html を生成。
                              digest md からの記事パース・本文 HTML 変換を含む。

公開 API:
    build_context(digest_path) -> dict     # OGP メタ + 記事配列 + summary
    render_page(ctx, out_path)             # Jinja2 で 1 ページ出力
    scan_digests(root)                     # digest/**/*.md を列挙
    main(argv)                             # `python tools/generate_pages.py [--full]`
"""
from __future__ import annotations

import argparse
import html as _html
import re
import sys
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin

# `python tools/generate_pages.py` 直接実行と `pytest tests/...` 経由の両方で
# `from tools.config import ...` が引けるよう、リポジトリルートを sys.path に入れる。
_PKG_ROOT = Path(__file__).resolve().parent.parent
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from tools.config import (  # noqa: E402
    BASE_URL,
    CATEGORIES,
    OG_DESCRIPTION_MAX,
    SITE_DESCRIPTION,
    SITE_TAGLINE_EN,
    SITE_TITLE,
    TOP_RECENT_DAYS,
)

# CRLF / LF 両対応の frontmatter 抽出 (Windows + git autocrlf 環境向け)。
_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
_THUMB_RE = re.compile(r"!\[thumb\]\((https?://[^)]+)\)")
_SUMMARY_RE = re.compile(r"> \[!summary\]\r?\n((?:>.*\r?\n)+)")

# 各記事ヘッダ: `### [88] タイトル ...`
_ARTICLE_HEAD_RE = re.compile(r"^###\s*(?:\[(\d+)\]\s*)?(.+?)\s*$", re.MULTILINE)
# メタ行: `📅 2026-05-20 不明 · 📰 Trading Economics · 🔗 [元記事](https://...)`
_META_DATE_RE = re.compile(r"📅\s*([\d\-/]+(?:\s+[^··\n]+)?)")
_META_SOURCE_RE = re.compile(r"📰\s*([^··\n]+?)(?=\s*[··]|\s*🔗|\s*$)")
_META_LINK_RE = re.compile(r"🔗\s*\[[^\]]+\]\((https?://[^)]+)\)")
# タグ行: `#cat/fx #country/日本 ...`
_TAG_LINE_RE = re.compile(r"^(?:#[\w/\-぀-ヿ一-鿿]+\s*){2,}$", re.MULTILINE)
# bullet
_BULLET_RE = re.compile(r"^-\s+(.+)$", re.MULTILINE)
# inline 装飾
_WIKILINK_RE = re.compile(r"\[\[([^\]|]+?)(?:\|([^\]]+))?\]\]")
_UNDERLINE_RE = re.compile(r"__(.+?)__")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """YAML 風 frontmatter を簡易パースして (dict, body) を返す。

    本タスクではスカラー値のみ扱う (tags 等のリストは本実装スコープ外)。
    値は前後の "  /  '  を剥がす。インデントされた継続行・コメントは無視。
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    fm: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if not line or line.startswith(" ") or line.startswith("-") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if value:
            fm[key] = value
    body = text[m.end():]
    return fm, body


def extract_summary_text(body: str) -> str:
    """本文の `> [!summary]` callout から本文テキストだけを取り出して 1 行に連結。

    callout が見つからなければ空文字列を返す。
    """
    m = _SUMMARY_RE.search(body)
    if not m:
        return ""
    parts: list[str] = []
    for line in m.group(1).splitlines():
        stripped = line.lstrip(">").strip()
        if stripped:
            parts.append(stripped)
    return " ".join(parts)


def truncate(text: str, max_len: int) -> str:
    """Unicode 単位で max_len 以下に truncate。超えたら末尾に '…' を付ける。"""
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _is_own_domain(url: str) -> bool:
    return url.startswith(BASE_URL)


def _absolutize(url: str) -> str:
    """相対 URL を BASE_URL ベースで絶対化。既に絶対なら素通し。"""
    if url.startswith(("http://", "https://")):
        return url
    return urljoin(BASE_URL + "/", url.lstrip("/"))


def resolve_og_image(fm: dict[str, str], body: str, category_id: str) -> str:
    """og:image 4 段フォールバック (DESIGN 通り):

    1. frontmatter `og_image` キーがあれば最優先
    2. 本文の最初の `![thumb](URL)` が自前ドメインなら採用
    3. デフォルト: {BASE_URL}/assets/og/{category_id}.jpg
    4. 相対 URL は urljoin で絶対化
    """
    fm_img = fm.get("og_image")
    if fm_img:
        return _absolutize(fm_img)

    m = _THUMB_RE.search(body)
    if m and _is_own_domain(m.group(1)):
        return m.group(1)

    return f"{BASE_URL}/assets/og/{category_id}.jpg"


def _normalize_title(raw_title: str, category_label: str) -> str:
    """og:title に 'News Grasp' を必ず含める。digest frontmatter の title は
    既存運用で 'News Grasp #YYYYMMDD — {label}' 形式なので通常はそのまま通る。
    """
    if not raw_title:
        return f"{SITE_TITLE} — {category_label}"
    if SITE_TITLE in raw_title:
        return raw_title
    return f"{SITE_TITLE} — {raw_title}"


def inline_html(text: str) -> str:
    """digest 本文の inline 記法を HTML に変換 (escape 込み・|safe で渡す前提)。

    変換順:
        1. HTML escape (`&` `<` `>` `"`)
        2. `[[X|Y]]` -> Y / `[[X]]` -> X (wikilink クラスでハイライト)
        3. `__X__` -> <u class="underline">X</u>
        4. `**X**` -> <strong>X</strong>
        5. `[text](url)` -> <a> (ただしメタ行で消費済みのことが多い)
    """
    s = _html.escape(text, quote=False)

    def _wikilink(m: re.Match[str]) -> str:
        label = m.group(2) or m.group(1)
        return f'<span class="wikilink">{label}</span>'
    s = _WIKILINK_RE.sub(_wikilink, s)

    s = _UNDERLINE_RE.sub(r'<u class="underline">\1</u>', s)
    s = _BOLD_RE.sub(r"<strong>\1</strong>", s)
    return s


def _parse_article_block(block: str) -> dict[str, Any] | None:
    """記事 1 ブロック (### 行から次の `---` まで) を dict に変換。

    block は `### [score] title` 行を含む文字列。
    """
    head = _ARTICLE_HEAD_RE.search(block)
    if not head:
        return None
    score = head.group(1) or ""
    title = head.group(2).strip()

    # メタ行を取り出す。📅 / 📰 / 🔗 が同一行に並ぶ前提。
    date = ""
    source = ""
    source_url = ""
    m = _META_DATE_RE.search(block)
    if m:
        date = m.group(1).strip()
    m = _META_SOURCE_RE.search(block)
    if m:
        source = m.group(1).strip()
    m = _META_LINK_RE.search(block)
    if m:
        source_url = m.group(1).strip()

    # タグ行 (空白区切り `#x` が 2 個以上並ぶ行を 1 本だけ拾う)。
    tags: list[str] = []
    tag_m = _TAG_LINE_RE.search(block)
    if tag_m:
        tag_line = tag_m.group(0)
        tags = [t.lstrip("#") for t in tag_line.split() if t.startswith("#")]

    # サムネ
    thumb = ""
    tm = _THUMB_RE.search(block)
    if tm:
        thumb = tm.group(1)

    # bullets (HTML inline 変換済み)
    bullets: list[str] = []
    for bm in _BULLET_RE.finditer(block):
        bullets.append(inline_html(bm.group(1).strip()))

    return {
        "title": title,
        "score": score,
        "date": date,
        "source": source,
        "source_url": source_url,
        "tags": tags,
        "thumb": thumb,
        "bullets": bullets,
    }


def parse_articles(body: str) -> list[dict[str, Any]]:
    """body から記事カード配列を抽出。

    digest 構造は `### [N] title ... \n\n---\n` で区切られている前提。
    `← [[...]] | [[...]] →` のような末尾ナビ行 / `*Auto-generated*` 行は無視。
    """
    # 横線で分割し、`###` を含む block だけ採用。
    blocks = re.split(r"\r?\n---\r?\n", body)
    articles: list[dict[str, Any]] = []
    for blk in blocks:
        if "### " not in blk:
            continue
        parsed = _parse_article_block(blk)
        if parsed and parsed["title"]:
            articles.append(parsed)
    return articles


def build_context(digest_path: Path) -> dict[str, Any]:
    """digest md ファイル 1 件から Jinja2 テンプレート用 context dict を組み立てる。

    出力 dict のキーは tests/test_generate_pages.py の契約に従う:
      title / date / issue / category_id / category_label / accent / glyph /
      og_type / og_title / og_description / og_image / og_url /
      canonical / twitter_card / base_url / site_title /
      summary_text / articles
    """
    text = Path(digest_path).read_text(encoding="utf-8")
    fm, body = parse_frontmatter(text)

    category_id = (fm.get("categoryId") or "summary").lower()
    if category_id not in CATEGORIES:
        category_id = "summary"
    cat = CATEGORIES[category_id]

    date_str = fm.get("date", "")
    canonical = f"{BASE_URL}/{category_id}/{date_str}/"

    title = _normalize_title(fm.get("title", ""), cat["label"])

    summary_text = extract_summary_text(body).replace("\n", " ").replace("\r", " ").strip()
    og_description = truncate(summary_text, OG_DESCRIPTION_MAX)

    og_image = _absolutize(resolve_og_image(fm, body, category_id))

    articles = parse_articles(body)

    # ===== Magazine Spread 用の追加 context =====
    top = articles[0] if articles else None
    more = articles[1:10] if len(articles) > 1 else []

    # hero スタッツ: 平均スコア
    scores = [int(a["score"]) for a in articles if str(a.get("score") or "").isdigit()]
    avg_score = round(sum(scores) / len(scores)) if scores else 0

    # 日付スタンプ "MM·DD"
    date_mmdd = ""
    if date_str and len(date_str) >= 10:
        date_mmdd = f"{date_str[5:7]}·{date_str[8:10]}"

    # economy だけ画像ファイル名が "economy" (Claude Design Handoff のアセット命名規約)
    thumb_slug = category_id

    # editorial section 番号 (Claude Design の 7 セクション中、各カテゴリは固定インデックス)
    # 総論(§01) / 為替(§02) / AI(§03) / IT(§04) / 経済(§05) / ゲーム(§06) / 明日へ(§07)
    essay_index_map = {"fx": 2, "ai": 3, "it": 4, "economy": 5, "game": 6, "summary": 1}
    essay_index = essay_index_map.get(category_id, 1)

    # Editorial outline 7 セクション固定ラベル
    essay_outline = [
        ("§01", "総論"),
        ("§02", "為替"),
        ("§03", "AI"),
        ("§04", "IT"),
        ("§05", "経済"),
        ("§06", "ゲーム"),
        ("§07", "明日へ"),
    ]

    # categories (lens nav 用)
    nav_categories = [
        {
            "id": cid,
            "name_jp": meta["jp"],
            "name_en": meta["label"],
            "glyph": meta["glyph"],
            "accent": meta["accent"],
            "is_active": cid == category_id,
        }
        for cid, meta in CATEGORIES.items()
        if cid != "summary"  # lens nav は 5 lenses 想定
    ]

    return {
        # ----- OGP / meta (既存契約) -----
        "title": title,
        "date": date_str,
        "issue": fm.get("issue", ""),
        "category_id": category_id,
        "category_label": cat["label"],
        "category_jp": cat["jp"],
        "accent": fm.get("accent", cat["accent"]),
        "glyph": fm.get("glyph", cat["glyph"]),
        "og_type": "article",
        "og_title": title,
        "og_description": og_description,
        "og_image": og_image,
        "og_url": canonical,
        "canonical": canonical,
        "twitter_card": "summary_large_image",
        "base_url": BASE_URL,
        "site_title": SITE_TITLE,
        "summary_text": summary_text,
        "articles": articles,
        # ----- Magazine Spread 追加 context -----
        "top": top,
        "more": more,
        "avg_score": avg_score,
        "date_mmdd": date_mmdd,
        "thumb_slug": thumb_slug,
        "essay_index": essay_index,
        "essay_outline": essay_outline,
        "nav_categories": nav_categories,
        "issue_no": fm.get("issue", "") or date_str.replace("-", ""),
    }


# ---------- Jinja2 render ----------

_TEMPLATE_DIR = _PKG_ROOT / "prompts"
_jinja_env = None


def _get_jinja_env():
    """Jinja2 Environment を lazy 初期化 (テンプレ未配置時は import エラーを後ろ倒し)。

    render_emph フィルタは [[X]] / __X__ マーカーを Magazine デザインの
    accent カラー強調 HTML に変換する。Python 側で html.escape を完全に通してから
    inline 装飾だけ Markup で挿入するため、autoescape 環境下でも安全。
    """
    global _jinja_env
    if _jinja_env is None:
        from jinja2 import Environment, FileSystemLoader, select_autoescape
        from markupsafe import Markup

        _jinja_env = Environment(
            loader=FileSystemLoader(str(_TEMPLATE_DIR)),
            autoescape=select_autoescape(["html"]),
            keep_trailing_newline=True,
        )

        def _render_emph(text: str) -> Markup:
            if text is None:
                return Markup("")
            s = _html.escape(str(text), quote=False)
            # [[X|Y]] -> Y, [[X]] -> X として bold + accent 背景
            s = _WIKILINK_RE.sub(
                lambda m: f'<strong class="emph-bold">{_html.escape(m.group(2) or m.group(1), quote=False)}</strong>',
                s,
            )
            # __X__ -> underline + bold
            s = _UNDERLINE_RE.sub(r'<span class="emph-und">\1</span>', s)
            # **X** -> bold (累積)
            s = _BOLD_RE.sub(r'<strong>\1</strong>', s)
            return Markup(s)

        _jinja_env.filters["render_emph"] = _render_emph
    return _jinja_env


def render_page(ctx: dict[str, Any], out_path: Path, template_name: str = "page-template.html") -> Path:
    """ctx を Jinja2 テンプレで render し UTF-8 で out_path に書き出す。"""
    env = _get_jinja_env()
    template = env.get_template(template_name)
    html_text = template.render(**ctx)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_text, encoding="utf-8", newline="\n")
    return out_path


# ---------- digest scanner ----------

def scan_digests(root: Path | None = None) -> list[Path]:
    """digest/**/*.md を全て列挙して mtime 昇順で返す (古い → 新しい)。"""
    base = Path(root) if root else (_PKG_ROOT / "digest")
    if not base.exists():
        return []
    paths = [p for p in base.rglob("*.md") if p.is_file()]
    paths.sort(key=lambda p: p.stat().st_mtime)
    return paths


def _out_path_for(ctx: dict[str, Any], docs_root: Path) -> Path:
    """ctx の category_id / date から `docs/{cat}/{YYYY-MM-DD}/index.html` を作る。"""
    return docs_root / ctx["category_id"] / ctx["date"] / "index.html"


def _needs_rebuild(src: Path, out: Path) -> bool:
    if not out.exists():
        return True
    return src.stat().st_mtime > out.stat().st_mtime


def build_all(*, full: bool = False, docs_root: Path | None = None, digests: Iterable[Path] | None = None) -> list[Path]:
    """全 digest を render。--full なら mtime 判定を無視して全件再生成。"""
    docs = Path(docs_root) if docs_root else (_PKG_ROOT / "docs")
    sources = list(digests) if digests is not None else scan_digests()
    written: list[Path] = []
    for src in sources:
        try:
            ctx = build_context(src)
        except Exception as exc:
            print(f"[warn] failed to build context for {src.name}: {exc}", file=sys.stderr)
            continue
        if not ctx.get("date") or not ctx.get("category_id"):
            print(f"[skip] missing date/category_id: {src.name}", file=sys.stderr)
            continue
        out = _out_path_for(ctx, docs)
        if not full and not _needs_rebuild(src, out):
            continue
        render_page(ctx, out)
        written.append(out)
    return written


# ---------- index / category / archive ----------

def _summary_entry(ctx: dict[str, Any]) -> dict[str, Any]:
    """個別ページ ctx から index / category / archive 用の軽量 entry を抽出。

    Phase 3 (Variant B Home) 追加フィールド:
      top_score / top_title / top_thumb / top_source / top_source_url /
      top_date / top_bullets / articles_count
    Hero Featured / Editor's Top 3 / category 件数表示で使用。
    """
    top = ctx.get("top") or {}
    raw_score = top.get("score", "")
    try:
        top_score_int = int(raw_score) if str(raw_score).strip().isdigit() else 0
    except (TypeError, ValueError):
        top_score_int = 0
    # Phase 4 (Overview C) 用: 全 articles の score 配列 + Top 3 を持つ
    all_articles = ctx.get("articles") or []
    scores: list[int] = []
    for a in all_articles:
        s = a.get("score") or ""
        if str(s).strip().isdigit():
            scores.append(int(s))
        else:
            scores.append(0)
    top3 = [
        {
            "title": a.get("title", ""),
            "score": a.get("score") or "",
            "date": a.get("date", ""),
            "source": a.get("source", ""),
        }
        for a in all_articles[:3]
    ]
    return {
        "title": ctx["title"],
        "date": ctx["date"],
        "category_id": ctx["category_id"],
        "category_label": ctx["category_label"],
        "category_jp": ctx["category_jp"],
        "canonical": ctx["canonical"],
        "summary_text": ctx.get("summary_text", ""),
        "og_image": ctx["og_image"],
        "accent": ctx["accent"],
        "glyph": ctx["glyph"],
        # Variant B Home 用
        "top_score": top_score_int,
        "top_title": top.get("title", ""),
        "top_thumb": top.get("thumb", ""),
        "top_source": top.get("source", ""),
        "top_source_url": top.get("source_url", ""),
        "top_date": top.get("date", ""),
        "top_bullets": top.get("bullets", []),
        "articles_count": len(all_articles),
        # Overview C 用
        "scores": scores,
        "top3": top3,
    }


def _collect_entries(digests: Iterable[Path]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for src in digests:
        try:
            ctx = build_context(src)
        except Exception as exc:
            print(f"[warn] entry skip {src.name}: {exc}", file=sys.stderr)
            continue
        if not ctx.get("date") or not ctx.get("category_id"):
            continue
        entries.append(_summary_entry(ctx))
    # 日付降順 (新しい→古い)
    entries.sort(key=lambda e: (e["date"], e["category_id"]), reverse=True)
    return entries


_WEEKDAY_JP = ["月", "火", "水", "木", "金", "土", "日"]


def _date_weekday_jp(date_str: str) -> str:
    """YYYY-MM-DD から日本語の曜日 1 文字を返す。失敗時は空文字。"""
    if not date_str or len(date_str) < 10:
        return ""
    try:
        from datetime import date as _date
        y, m, d = int(date_str[0:4]), int(date_str[5:7]), int(date_str[8:10])
        return _WEEKDAY_JP[_date(y, m, d).weekday()]
    except (ValueError, IndexError):
        return ""


def _split_theme_phrases(summary_text: str) -> tuple[str, str]:
    """summary_text から Hero 用の 2 フレーズ ("金利の天井" "AIの底入れ" 風) を抽出。

    句読点 (「、」「。」) で切り、最初の名詞句 2 つを返す。最大 9 文字程度に揃える。
    取れなければ ("", "") を返し、テンプレ側のフォールバックに任せる。
    """
    if not summary_text:
        return ("", "")
    # 最初の「。」までを切り、それを「、」「と」で分ける
    head = summary_text.split("。", 1)[0]
    # 「と」で挟まれた 2 句がある場合
    for sep in (" と ", "と", "、"):
        if sep in head:
            parts = [p.strip() for p in head.split(sep, 1)]
            if len(parts) == 2 and parts[0] and parts[1]:
                left = parts[0]
                right = parts[1].split("。", 1)[0].split(".", 1)[0]
                if 2 <= len(left) <= 14 and 2 <= len(right) <= 14:
                    return (left, right)
    return ("", "")


def build_index(entries: list[dict[str, Any]], docs_root: Path,
                recent_days: int = TOP_RECENT_DAYS) -> Path:
    """Variant B Magazine Spread Home (docs/index.html) を生成。

    context 構成:
      site_title / base_url / total_pages
      today_date / today_date_mmdd / today_weekday / issue_no
      hero_phrase_left / hero_phrase_right / hero_lead
      hero_story / editor_top3 / lens_cards / editorial / stats
      categories (raw)
    """
    if not entries:
        # 空配列でも render は走る。fallback メッセージで安全に
        ctx = {
            "site_title": SITE_TITLE,
            "site_tagline": SITE_DESCRIPTION,
            "site_tagline_en": SITE_TAGLINE_EN,
            "base_url": BASE_URL,
            "total_pages": 0,
            "today_date": "",
            "today_date_mmdd": "",
            "today_weekday": "",
            "issue_no": "",
            "hero_phrase_left": "",
            "hero_phrase_right": "",
            "hero_lead": "本日のダイジェスト準備中。",
            "hero_story": None,
            "editor_top3": [],
            "lens_cards": [{"id": cid, "name_jp": meta["jp"], "name_en": meta["label"],
                            "glyph": meta["glyph"], "accent": meta["accent"],
                            "summary": "", "canonical": f"{BASE_URL}/{cid}/", "stories": 0}
                           for cid, meta in CATEGORIES.items() if cid != "summary"],
            "editorial": None,
            "stats": {"stories": 0, "categories": 5, "essay": 7, "reading_min": 15},
            "categories": [{"id": k, **v} for k, v in CATEGORIES.items()],
        }
        out = Path(docs_root) / "index.html"
        return render_page(ctx, out, template_name="index-template.html")

    # entries は日付降順なので最初の entry の日付が今日
    today_date = entries[0]["date"]
    same_day = [e for e in entries if e["date"] == today_date]

    # Editor's Top 3: score 降順、上位 3 件 (同一カテゴリ重複は許容、デザイン仕様通り)
    sorted_by_score = sorted(same_day, key=lambda e: e.get("top_score", 0), reverse=True)
    editor_top3 = sorted_by_score[:3]
    hero_story = sorted_by_score[0] if sorted_by_score else None

    # 5 lens cards (summary を除く 5 カテゴリ、同日最新 entry を引く)
    by_cat: dict[str, dict[str, Any]] = {}
    for e in same_day:
        cid = e["category_id"]
        if cid != "summary" and cid not in by_cat:
            by_cat[cid] = e
    lens_cards: list[dict[str, Any]] = []
    for cid, meta in CATEGORIES.items():
        if cid == "summary":
            continue
        e = by_cat.get(cid)
        lens_cards.append({
            "id": cid,
            "name_jp": meta["jp"],
            "name_en": meta["label"],
            "glyph": meta["glyph"],
            "accent": meta["accent"],
            "summary": e["summary_text"] if e else "",
            "canonical": e["canonical"] if e else f"{BASE_URL}/{cid}/",
            "stories": e.get("articles_count", 0) if e else 0,
        })

    # Editorial preview: 同日の summary digest を引く。無ければ全 entry から最新の summary を探す
    editorial = next((e for e in same_day if e["category_id"] == "summary"), None)
    if editorial is None:
        editorial = next((e for e in entries if e["category_id"] == "summary"), None)

    # Today's Theme: editorial.summary_text から 2 フレーズ抽出を試みる
    theme_source = editorial["summary_text"] if editorial else ""
    hero_phrase_left, hero_phrase_right = _split_theme_phrases(theme_source)

    # Hero lead: editorial の summary が最も豊か。無ければ hero_story.summary_text に
    hero_lead = ""
    if editorial and editorial.get("summary_text"):
        hero_lead = editorial["summary_text"]
    elif hero_story and hero_story.get("summary_text"):
        hero_lead = hero_story["summary_text"]
    else:
        hero_lead = f"本日 {len(same_day)} カテゴリのダイジェストをお届けします。"
    # lead を 180 文字程度に丸める
    if len(hero_lead) > 200:
        hero_lead = hero_lead[:198] + "…"

    # Stats
    stories_total = sum(e.get("articles_count", 0) for e in same_day)
    if stories_total == 0:
        stories_total = len(same_day)

    issue_no = today_date.replace("-", "") if today_date else ""

    today_date_mmdd = ""
    if today_date and len(today_date) >= 10:
        today_date_mmdd = f"{today_date[5:7]}·{today_date[8:10]}"

    ctx = {
        "site_title": SITE_TITLE,
        "site_tagline": SITE_DESCRIPTION,
        "site_tagline_en": SITE_TAGLINE_EN,
        "base_url": BASE_URL,
        "total_pages": len(entries),

        "today_date": today_date,
        "today_date_mmdd": today_date_mmdd,
        "today_weekday": _date_weekday_jp(today_date),
        "issue_no": issue_no,

        "hero_phrase_left": hero_phrase_left,
        "hero_phrase_right": hero_phrase_right,
        "hero_lead": hero_lead,

        "hero_story": hero_story,
        "editor_top3": editor_top3,
        "lens_cards": lens_cards,
        "editorial": editorial,

        "stats": {
            "stories": stories_total,
            "categories": 5,
            "essay": 7,
            "reading_min": 15,
        },
        "categories": [{"id": k, **v} for k, v in CATEGORIES.items()],
    }
    out = Path(docs_root) / "index.html"
    return render_page(ctx, out, template_name="index-template.html")


def build_overview(date: str, entries: list[dict[str, Any]], docs_root: Path) -> Path:
    """Phase 4: 日付別 Daily Overview (Pattern C) docs/{date}/index.html を生成。

    entries は **同一 date の** entries だけを渡す前提。summary を含む全カテゴリの
    最新ダイジェストを集約して、5 lens の 1 ページサマリを作る。
    """
    same_day = [e for e in entries if e["date"] == date]
    if not same_day:
        raise ValueError(f"build_overview: entries に date={date} が無い")

    # 各カテゴリ (summary 除く 5 lens) を CATEGORIES 順に並べる
    by_cat: dict[str, dict[str, Any]] = {}
    for e in same_day:
        cid = e["category_id"]
        if cid != "summary" and cid not in by_cat:
            by_cat[cid] = e
    cat_rows: list[dict[str, Any]] = []
    for cid, meta in CATEGORIES.items():
        if cid == "summary":
            continue
        e = by_cat.get(cid)
        scores = e.get("scores", []) if e else []
        # 10 本に整形 (足りなければ 0 で詰める)
        scores10 = (scores + [0] * 10)[:10]
        non_zero = [s for s in scores if s > 0]
        avg_score = round(sum(non_zero) / len(non_zero)) if non_zero else 0
        max_score = max(non_zero) if non_zero else 0
        cat_rows.append({
            "id": cid,
            "name_jp": meta["jp"],
            "name_en": meta["label"],
            "glyph": meta["glyph"],
            "accent": meta["accent"],
            "summary": e["summary_text"] if e else "",
            "canonical": e["canonical"] if e else f"{BASE_URL}/{cid}/",
            "articles_count": e.get("articles_count", 0) if e else 0,
            "scores": scores10,
            "avg_score": avg_score,
            "max_score": max_score,
            "top3": e.get("top3", []) if e else [],
        })

    # Theme banner: 同日 summary digest から 2 フレーズ抽出
    editorial = next((e for e in same_day if e["category_id"] == "summary"), None)
    theme_source = editorial["summary_text"] if editorial else ""
    hero_phrase_left, hero_phrase_right = _split_theme_phrases(theme_source)

    # Stats
    stories_total = sum(r["articles_count"] for r in cat_rows)
    # ざっくり: 全記事 50 本想定で ~30 分。スケールは記事数 / 50 * 30 で計算
    full_read_min = max(5, round(stories_total / 50 * 30)) if stories_total else 30

    issue_no = date.replace("-", "")
    date_mmdd = f"{date[5:7]}·{date[8:10]}" if len(date) >= 10 else date
    canonical = f"{BASE_URL}/{date}/"

    ctx = {
        "site_title": SITE_TITLE,
        "site_tagline": SITE_DESCRIPTION,
        "base_url": BASE_URL,
        "canonical": canonical,

        "date": date,
        "date_mmdd": date_mmdd,
        "issue_no": issue_no,

        "hero_phrase_left": hero_phrase_left,
        "hero_phrase_right": hero_phrase_right,

        "editorial_date": editorial["date"] if editorial else "",

        "cat_rows": cat_rows,
        "stats": {
            "stories": stories_total,
            "categories": 5,
            "essay": 7,
            "full_read_min": full_read_min,
        },
    }
    out = Path(docs_root) / date / "index.html"
    return render_page(ctx, out, template_name="overview-template.html")


def build_all_overviews(entries: list[dict[str, Any]], docs_root: Path) -> list[Path]:
    """全 unique date について overview ページを生成。"""
    unique_dates = sorted({e["date"] for e in entries if e.get("date")}, reverse=True)
    written: list[Path] = []
    for d in unique_dates:
        try:
            written.append(build_overview(d, entries, docs_root))
        except Exception as exc:
            print(f"[warn] overview build failed for {d}: {exc}", file=sys.stderr)
    return written


def build_category_pages(entries: list[dict[str, Any]], docs_root: Path) -> list[Path]:
    """カテゴリ別アーカイブ docs/{cat}/index.html を生成。"""
    written: list[Path] = []
    for cat_id, cat in CATEGORIES.items():
        cat_entries = [e for e in entries if e["category_id"] == cat_id]
        if not cat_entries:
            continue
        ctx = {
            "site_title": SITE_TITLE,
            "base_url": BASE_URL,
            "category_id": cat_id,
            "category_label": cat["label"],
            "category_jp": cat["jp"],
            "canonical": f"{BASE_URL}/{cat_id}/",
            "entries": cat_entries,
        }
        out = Path(docs_root) / cat_id / "index.html"
        written.append(render_page(ctx, out, template_name="category-template.html"))
    return written


def build_archive(entries: list[dict[str, Any]], docs_root: Path) -> Path:
    """日付横断アーカイブ docs/archive/index.html を生成。日付ごとにグループ化して降順。"""
    by_date: dict[str, list[dict[str, Any]]] = {}
    for e in entries:
        by_date.setdefault(e["date"], []).append(e)
    days = [
        {"date": d, "entries": sorted(by_date[d], key=lambda x: x["category_id"])}
        for d in sorted(by_date.keys(), reverse=True)
    ]

    ctx = {
        "site_title": SITE_TITLE,
        "base_url": BASE_URL,
        "canonical": f"{BASE_URL}/archive/",
        "days": days,
        "total_pages": len(entries),
    }
    out = Path(docs_root) / "archive" / "index.html"
    return render_page(ctx, out, template_name="archive-template.html")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="News-Grasp 公開 web SSG")
    parser.add_argument("--full", action="store_true",
                        help="mtime 判定をスキップして全件強制再生成")
    parser.add_argument("--docs-root", type=Path, default=None,
                        help="出力ルート (デフォルト: <repo>/docs)")
    parser.add_argument("--no-index", action="store_true",
                        help="index/category/archive のビルドをスキップ (個別ページのみ)")
    args = parser.parse_args(argv)

    # Windows cp932 stdout で em-dash 等が落ちないよう UTF-8 化。
    if getattr(sys.stdout, "encoding", "").lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass

    docs_root = args.docs_root or (_PKG_ROOT / "docs")
    written = build_all(full=args.full, docs_root=docs_root)
    print(f"wrote {len(written)} article page(s)")
    for p in written[:5]:
        try:
            rel = p.relative_to(_PKG_ROOT)
        except ValueError:
            rel = p
        print(f"  - {rel}")
    if len(written) > 5:
        print(f"  ... and {len(written) - 5} more")

    if not args.no_index:
        entries = _collect_entries(scan_digests())
        idx = build_index(entries, docs_root)
        cat_pages = build_category_pages(entries, docs_root)
        arc = build_archive(entries, docs_root)
        overviews = build_all_overviews(entries, docs_root)
        print(
            f"wrote index/archive: {idx.name}, {len(cat_pages)} category page(s), "
            f"{arc.parent.name}/{arc.name}, {len(overviews)} overview page(s)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
