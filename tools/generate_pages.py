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
# Editorial summary digest 内の `### §NN ...` (考察 7 セクション) ヘッダ。
# digest/Summary/{date}.md 本文から §01-§07 を構造化抽出するのに使う。
_ESSAY_SECTION_RE = re.compile(r"^###\s+§(\d{2})\s+(.+?)\s*$", re.MULTILINE)
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

# Summary digest の考察 (reflection) ブロック抽出用。
# `## § 本日のテーマ考察` 見出し / `> [!quote]` PULL QUOTE / `### KEY TAKEAWAYS`。
_THEME_ESSAY_HEADER_RE = re.compile(r"^##\s+§?\s*本日のテーマ考察\s*$", re.MULTILINE)
_PULLQUOTE_RE = re.compile(r"^>\s*\[!quote\][^\n]*\r?\n((?:>.*(?:\r?\n|$))+)", re.MULTILINE)
_TAKEAWAYS_HEADER_RE = re.compile(r"^###\s+KEY\s+TAKEAWAYS\s*$", re.MULTILINE)
_TAKEAWAY_ITEM_RE = re.compile(r"^-\s+\*\*\[([^\]]+)\]\*\*\s*(.+?)\s*$", re.MULTILINE)
# Hero / LP の考察文末尾に付く定型の遷移句 (「以下、各カテゴリを横断して読み解く。」)。
# LP の「本日のテーマ考察」ボックスは単体で読まれるため、表示時に除去する。
_HOME_LEAD_TRAILER_RE = re.compile(
    r"以下[、,]?\s*各カテゴリを横断して読み解く[。\.\-—─]*\s*$"
)
# 考察 §NN 見出しの先頭ラベル (為替/AI/...) を category id に対応付ける。
# CATEGORIES["it"]["jp"] は "IT-Consulting" だが、digest 見出しは "IT —" 表記なので別途 alias。
TAG_TO_CID: dict[str, str] = {
    "為替": "fx",
    "AI": "ai",
    "IT": "it",
    "IT-Consulting": "it",
    "モビリティ": "mobility",
    "経済": "economy",
    "ゲーム": "game",
}


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

    DESIGN.md「強調記法 (3 階層)」と一致させるため、クラス名は render_emph と統一:
        1. HTML escape (`&` `<` `>` `"`)
        2. `[[X|Y]]` -> Y / `[[X]]` -> X (<strong class="emph-bold">、マーカー最強)
        3. `__X__` -> <span class="emph-und">X</span> (下線、弱・含意)
        4. `**X**` -> <strong>X</strong> (太字、中)
    """
    s = _html.escape(text, quote=False)

    def _wikilink(m: re.Match[str]) -> str:
        label = m.group(2) or m.group(1)
        return f'<strong class="emph-bold">{label}</strong>'
    s = _WIKILINK_RE.sub(_wikilink, s)

    s = _UNDERLINE_RE.sub(r'<span class="emph-und">\1</span>', s)
    s = _BOLD_RE.sub(r"<strong>\1</strong>", s)
    return s


def strip_inline(text: str) -> str:
    """digest 本文の inline 装飾記法 (`[[X|Y]]` `__X__` `**X**`) を剥がして素テキスト化。

    `render_emph` を通さず `{{ }}` で素表示する箇所 (LP / Hero のリード文など) で使う。
    マーカー文字がそのまま画面に出るのを防ぐ。HTML escape はテンプレ側の autoescape に任せる。
    """
    if not text:
        return ""
    s = _WIKILINK_RE.sub(lambda m: m.group(2) or m.group(1), text)
    s = _UNDERLINE_RE.sub(r"\1", s)
    s = _BOLD_RE.sub(r"\1", s)
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

    category_id = (fm.get("categoryId") or "").lower()
    if category_id not in CATEGORIES:
        # categoryId 欠落/不正時は親フォルダ名から導出 (digest/FX → fx)。
        # 無条件 summary 既定化は categoryId 欠落のカテゴリ digest を summary に
        # 化けさせ、同日重複 entry → 「準備中」fallback を生むため廃止 (2026-05-30)。
        category_id = _resolve_cat_from_dirname(Path(digest_path).parent.name) or "summary"
    cat = CATEGORIES[category_id]

    date_str = fm.get("date", "")
    # category=summary は廃止された `/summary/{date}/` ではなく統合先
    # `/{date}/summary/` を canonical とする (2026-05-26 統合)。
    if category_id == "summary":
        canonical = f"{BASE_URL}/{date_str}/summary/"
    else:
        canonical = f"{BASE_URL}/{category_id}/{date_str}/"

    title = _normalize_title(fm.get("title", ""), cat["label"])

    summary_text = extract_summary_text(body).replace("\n", " ").replace("\r", " ").strip()
    og_description = truncate(summary_text, OG_DESCRIPTION_MAX)

    # summary digest のみ考察 (reflection) と theme フレーズを抽出。
    # LP / overview / summary ページの「本日のテーマ考察」「総論」「PULL QUOTE」
    # 「KEY TAKEAWAYS」はここで抽出したデータを使う (entry に同梱して再パースを避ける)。
    reflection = parse_reflection(body) if category_id == "summary" else {}

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
        if cid != "summary"  # lens nav は 6 lenses 想定
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
        "theme": fm.get("theme", ""),
        "reflection": reflection,
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
        # 統合方針 (2026-05-26): summary カテゴリの個別ページ /summary/{date}/ は廃止し、
        # /{date}/summary/ (build_summary 出力) に統合した。digest/Summary/*.md は
        # build_summary 側でのみ消費するため、ここでは個別ページ生成をスキップする。
        if ctx["category_id"] == "summary":
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
        "theme": ctx.get("theme", ""),
        "reflection": ctx.get("reflection") or {},
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

_MONTH_ABBR = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
               "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


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


# 配信スケジュール (確定ルール / prompts/routine-system.md L78-84 に準拠)
#   月=0 ... 日=6。値は配信するカテゴリ ID のセット。
#   月: FX/AI/IT/Mobility/Economy (5)
#   火: + Game (6)
#   水: 月と同じ (5)
#   木: 火と同じ (6)
#   金: 月と同じ (5)
#   土: FX/AI/IT/Mobility/Game (Economy 除く、5)
#   日: 土と同じ (5)
_PUBLICATION_SCHEDULE: dict[int, set[str]] = {
    0: {"fx", "ai", "it", "mobility", "economy"},          # 月
    1: {"fx", "ai", "it", "mobility", "economy", "game"},  # 火
    2: {"fx", "ai", "it", "mobility", "economy"},          # 水
    3: {"fx", "ai", "it", "mobility", "economy", "game"},  # 木
    4: {"fx", "ai", "it", "mobility", "economy"},          # 金
    5: {"fx", "ai", "it", "mobility", "game"},             # 土
    6: {"fx", "ai", "it", "mobility", "game"},             # 日
}


# cat_id → data/articles.jsonl の "genre" 表記揺れ吸収マッピング
_GENRE_ALIASES: dict[str, set[str]] = {
    "fx":       {"FX", "Foreign Exchange"},
    "ai":       {"AI", "Artificial Intelligence"},
    "it":       {"IT", "IT-Consulting", "IT & Consulting"},
    "mobility": {"Mobility"},
    "economy":  {"Economy"},
    "game":     {"Game"},
}


def _resolve_cat_from_dirname(dirname: str) -> str | None:
    """digest の親フォルダ名 (FX / IT-Consulting 等) を cat_id に逆引きする。

    _GENRE_ALIASES (cat_id → 表記揺れ set) を再利用し、大文字小文字差は casefold で無視する。
    親フォルダ "Summary" は casefold で "summary" となり CATEGORIES に含まれるため summary を
    返す (summary digest は summary のままが正しい)。どの cat にも該当しなければ None を返し、
    呼び出し側で summary に既定化される。
    """
    if not dirname:
        return None
    key = dirname.casefold()
    if key in CATEGORIES:
        return key
    for cat_id, aliases in _GENRE_ALIASES.items():
        if any(key == alias.casefold() for alias in aliases):
            return cat_id
    return None


def _articles_as_grid_entries(cat_id: str, date: str,
                               skip_url: str | None = None) -> list[dict[str, Any]]:
    """data/articles.jsonl から同日同カテゴリ記事を more-card 用 entry-like dict に変換。

    backfill 未着手の新設カテゴリ (Mobility 等) で 1 entry しか持たない場合の fallback。
    score 降順で最大 9 件返す。featured 記事 (top_source_url) は skip。
    """
    import json as _json
    p = _PKG_ROOT / "data" / "articles.jsonl"
    if not p.exists():
        return []
    aliases = _GENRE_ALIASES.get(cat_id, set())
    out: list[dict[str, Any]] = []
    cat_meta = CATEGORIES.get(cat_id, {})
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = _json.loads(line)
            except _json.JSONDecodeError:
                continue
            genre = o.get("genre", "")
            if genre not in aliases and o.get("category_id") != cat_id:
                continue
            if o.get("date") != date:
                continue
            if skip_url and o.get("url") == skip_url:
                continue
            out.append({
                "date": o.get("date", date),
                "top_title": o.get("title", ""),
                "top_thumb": o.get("thumb"),
                "top_source": o.get("source"),
                "top_source_url": o.get("url"),
                "top_score": o.get("score"),
                "summary_text": o.get("summary", "") or "",
                "top_bullets": [],
                "canonical": o.get("url", ""),
                "category_id": cat_id,
                "category_label": cat_meta.get("label", cat_id),
                "category_jp": cat_meta.get("jp", cat_id),
                "accent": cat_meta.get("accent", ""),
                "glyph": cat_meta.get("glyph", ""),
            })
    out.sort(key=lambda x: x.get("top_score") or 0, reverse=True)
    return out[:9]


def compute_publication_matrix(entries: list[dict[str, Any]],
                                today_date: str,
                                days: int = 30) -> dict[str, Any]:
    """配信スケジュール (確定ルール) を行列形式で返す。

    routine-system.md L78-84 の表を _PUBLICATION_SCHEDULE に固定値で持つ。
    entries / days 引数は後方互換のため受けるが、確定ルールの返却には使わない。

    返却 dict:
      {
        "weekdays": ["月","火","水","木","金","土","日"],
        "today_idx": int,  # 今日の曜日 (月=0)
        "rows": [
          {"id": "fx", "jp": "為替", "glyph": "¥", "label": "...", "accent": "#...",
           "cells": [{"scheduled": bool}, ...×7]}, ...
        ],
      }
    """
    from datetime import date as _date
    weekdays = list(_WEEKDAY_JP)
    today_idx = 0
    if today_date and len(today_date) >= 10:
        try:
            today_idx = _date(int(today_date[0:4]),
                              int(today_date[5:7]),
                              int(today_date[8:10])).weekday()
        except (ValueError, IndexError):
            today_idx = 0

    rows: list[dict[str, Any]] = []
    for cid, meta in CATEGORIES.items():
        if cid == "summary":
            continue
        cells = [
            {"scheduled": (cid in _PUBLICATION_SCHEDULE[i])}
            for i in range(7)
        ]
        rows.append({
            "id": cid,
            "jp": meta["jp"],
            "label": meta["label"],
            "glyph": meta["glyph"],
            "accent": meta["accent"],
            "cells": cells,
        })
    return {
        "weekdays": weekdays,
        "today_idx": today_idx,
        "rows": rows,
    }


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
                # 末尾の英文節 (". Read more" 等) だけを落とす。小数点 (3.8% 等) は残す
                # ため、"." の後ろが空白/英字のときのみ分割する。
                right = re.split(r"\.(?=\s|[A-Za-z])", parts[1].split("。", 1)[0], maxsplit=1)[0]
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
            "stats": {"stories": 0, "categories": 6, "essay": 7, "reading_min": 15},
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

    # 6 lens cards (summary を除く 6 カテゴリ、同日最新 entry を引く)
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

    # Today's Theme フレーズ: frontmatter `theme:` ("A と B" 形式) を 2 トーンに分割。
    # 旧実装は editorial.summary_text (= 本文先頭の [!summary] = 為替カテゴリ要約) を使い
    # 為替語句しか出なかったため、日全体を表す theme 由来に変更。
    reflection = (editorial.get("reflection") if editorial else None) or {}
    theme_phrase = (editorial.get("theme") if editorial else "") or ""
    hero_phrase_left, hero_phrase_right = _split_theme_phrases(theme_phrase)

    # 本日のテーマ考察 (多カテゴリ横断・150〜250字)。考察 lead の末尾遷移句だけ除去し、
    # 装飾記法 (`[[ ]]` `__ __` `**`) は保持 → テンプレ側で render_emph により
    # マーカー/太字/下線を描画し、長文の可読性を上げる (デザインを害さないネイビー強調)。
    # 取れない (旧 digest) ときは従来どおり summary_text にフォールバック。
    editorial_essay = _strip_lead_trailer(reflection.get("lead", ""))
    if not editorial_essay and editorial:
        editorial_essay = editorial.get("summary_text", "")

    # Hero lead: LP 上部 TODAY'S THEME の導入文。同じ考察を装飾なしの素テキストで簡潔に。
    hero_lead = strip_inline(editorial_essay)
    if not hero_lead and hero_story and hero_story.get("summary_text"):
        hero_lead = strip_inline(hero_story["summary_text"])
    if not hero_lead:
        hero_lead = f"本日 {len(same_day)} カテゴリのダイジェストをお届けします。"
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
        "editorial_essay": editorial_essay,

        "hero_story": hero_story,
        "editor_top3": editor_top3,
        "lens_cards": lens_cards,
        "editorial": editorial,

        "stats": {
            "stories": stories_total,
            "categories": 6,
            "essay": 7,
            "reading_min": 15,
        },
        "categories": [{"id": k, **v} for k, v in CATEGORIES.items()],
        "publication_matrix": compute_publication_matrix(entries, today_date, days=30),
    }
    out = Path(docs_root) / "index.html"
    return render_page(ctx, out, template_name="index-template.html")


def build_overview(date: str, entries: list[dict[str, Any]], docs_root: Path) -> Path:
    """Phase 4: 日付別 Daily Overview (Pattern C) docs/{date}/index.html を生成。

    entries は **同一 date の** entries だけを渡す前提。summary を含む全カテゴリの
    最新ダイジェストを集約して、6 lens の 1 ページサマリを作る (v2: Mobility 追加)。
    """
    same_day = [e for e in entries if e["date"] == date]
    if not same_day:
        raise ValueError(f"build_overview: entries に date={date} が無い")

    # 各カテゴリ (summary 除く 6 lens) を CATEGORIES 順に並べる
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
    # フレーズは frontmatter `theme:` 由来 (日全体を表す)。為替偏重だった summary_text から変更。
    theme_phrase = (editorial.get("theme") if editorial else "") or ""
    hero_phrase_left, hero_phrase_right = _split_theme_phrases(theme_phrase)

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
            "categories": 6,
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


# ---------- Phase 5: Editorial Summary (Pattern D) ----------

# 7 セクションの固定タグ + accent (Claude Design site/desktop-extra.jsx の DesktopSummaryOnly より)
_SUMMARY_SECTION_TAGS = ["総論", "為替", "AI", "IT", "経済", "ゲーム", "明日へ"]
_SUMMARY_SECTION_COLORS = ["#1A1A1A", "#B8860B", "#2D5BB8", "#2E6B52", "#8E2A19", "#5E3D8C", "#C9A155"]
# §02-06 を担当する category id (順序固定)
_SUMMARY_CAT_ORDER = [None, "fx", "ai", "it", "economy", "game", None]


def parse_essay_sections(body: str) -> dict[int, dict[str, str]]:
    """summary digest md 本文から `### §NN ...` の 7 セクションを構造化辞書で返す。

    キーは 1..7、値は `{heading, body}`。
    - heading: 行頭 `### §NN ` の直後の文字列 (例: `総論 — 金利の壁とAIの自律が交差した一日`)
    - body: 次の `### ` ヘッダ直前まで or `### KEY TAKEAWAYS` 直前までの段落テキスト

    digest が §01-§07 全部含む前提だが、欠けていればその番号のキーは作らない。
    """
    matches = list(_ESSAY_SECTION_RE.finditer(body))
    sections: dict[int, dict[str, str]] = {}
    for idx, m in enumerate(matches):
        num = int(m.group(1))
        heading = m.group(2).strip()
        start = m.end()
        if idx + 1 < len(matches):
            end = matches[idx + 1].start()
        else:
            end = len(body)
        section_body = body[start:end]
        # `### KEY TAKEAWAYS` や `### ` で始まる別ブロックは段落から除外
        cut = re.search(r"^###\s", section_body, re.MULTILINE)
        if cut:
            section_body = section_body[: cut.start()]
        sections[num] = {
            "heading": heading,
            "body": section_body.strip(),
        }
    return sections


def _parse_theme_intro(body: str) -> tuple[str, str]:
    """`## § 本日のテーマ考察` 直下の subtitle (斜体) と lead (考察 blockquote) を返す。

    digest の構造 (毎朝 routine が生成):
        ## § 本日のテーマ考察
        *{subtitle}*
        > {lead 本文。多カテゴリ横断・150〜250字}
        > [!quote] PULL QUOTE   ← lead はここの手前で切る
    lead は最初に現れる「callout でない blockquote ブロック」。取れなければ ("", "")。
    """
    m = _THEME_ESSAY_HEADER_RE.search(body)
    if not m:
        return ("", "")
    rest = body[m.end():]
    nxt = re.search(r"^###\s", rest, re.MULTILINE)
    region = rest[: nxt.start()] if nxt else rest

    subtitle = ""
    sub_m = re.search(r"^\*(.+?)\*\s*$", region, re.MULTILINE)
    if sub_m:
        subtitle = sub_m.group(1).strip()

    lead_lines: list[str] = []
    in_block = False
    for line in region.splitlines():
        s = line.rstrip()
        if s.startswith(">"):
            content = s[1:].strip()
            if content.startswith("[!"):
                if in_block:
                    break  # lead は callout の手前まで
                continue
            if not content:
                if in_block:
                    break
                continue
            lead_lines.append(content)
            in_block = True
        elif in_block:
            break
    lead = " ".join(lead_lines).strip()
    return (subtitle, lead)


def _parse_pull_quote(body: str) -> dict[str, str]:
    """`> [!quote] PULL QUOTE` ブロックから {text, from} を取り出す。無ければ空。"""
    m = _PULLQUOTE_RE.search(body)
    if not m:
        return {"text": "", "from": ""}
    lines: list[str] = []
    frm = ""
    for line in m.group(1).splitlines():
        c = line.lstrip(">").strip()
        if not c:
            continue
        attr = re.match(r"^[─—-]+\s*(.+?)\s*より\s*$", c)
        if attr:
            frm = attr.group(1).strip()
            continue
        lines.append(c)
    return {"text": " ".join(lines).strip(), "from": frm}


def _parse_takeaways(body: str) -> list[dict[str, str]]:
    """`### KEY TAKEAWAYS` の `- **[tag]** text` 形式 bullet を [{tag, text}] で返す。"""
    m = _TAKEAWAYS_HEADER_RE.search(body)
    if not m:
        return []
    rest = body[m.end():]
    nxt = re.search(r"^(?:###\s|>\s*\[!|---\s*$|←\s|\*🤖)", rest, re.MULTILINE)
    region = rest[: nxt.start()] if nxt else rest
    out: list[dict[str, str]] = []
    for tm in _TAKEAWAY_ITEM_RE.finditer(region):
        out.append({"tag": tm.group(1).strip(), "text": tm.group(2).strip()})
    return out


def parse_reflection(body: str) -> dict[str, Any]:
    """summary digest 本文から考察 (reflection) 構造を抽出。

    返却: {subtitle, lead, pull_quote{text,from}, sections{NN:{heading,body}}, takeaways[]}。
    summary 以外の digest や考察ブロックが無い digest では各値が空のまま返る。
    """
    subtitle, lead = _parse_theme_intro(body)
    return {
        "subtitle": subtitle,
        "lead": lead,
        "pull_quote": _parse_pull_quote(body),
        "sections": parse_essay_sections(body),
        "takeaways": _parse_takeaways(body),
    }


def _strip_lead_trailer(lead: str) -> str:
    """lead 末尾の定型遷移句 (「以下、各カテゴリを横断して読み解く。」) を除去。装飾記法は保持。

    LP「本日のテーマ考察」ボックスは render_emph で太字/下線/マーカーを描画するため、
    `[[ ]]` `__ __` `**` を残したまま末尾の遷移句だけ落とす。
    """
    return _HOME_LEAD_TRAILER_RE.sub("", (lead or "").strip()).rstrip()


def _theme_essay_for_home(lead: str) -> str:
    """LP Hero リード用に lead を素テキスト化し末尾遷移句も除去 (装飾なしの簡潔表示向け)。"""
    return strip_inline(_strip_lead_trailer(lead))


def _build_essay_sections(sections: dict[int, dict[str, str]],
                          by_cat: dict[str, dict[str, Any]]) -> list[dict[str, Any]] | None:
    """digest の `### §NN` から抽出した考察を summary-template 用 sections に変換。

    sections が空 (考察ブロック非対応の digest) なら None を返し、呼び出し側の
    fallback (7-grid) に委ねる。各 §NN の見出し先頭ラベルからカテゴリを判定し、
    総論/明日へは自己ページ表示なので「詳細を読む」リンク (canonical) を出さない。
    """
    if not sections:
        return None
    out: list[dict[str, Any]] = []
    for num in sorted(sections.keys()):
        es = sections[num]
        heading = es.get("heading", "")
        body = es.get("body", "")
        label = re.split(r"\s*[—–\-]\s*", heading, maxsplit=1)[0].strip() if heading else ""
        cid = TAG_TO_CID.get(label)
        bullets: list[str] = []
        if num == 1 or "総論" in heading:
            tag, color, canonical = "総論", "#1A1A1A", ""
        elif "明日" in heading or "明日" in label:
            tag, color, canonical = "明日へ", "#C9A155", ""
        elif cid:
            tag = CATEGORIES[cid]["jp"] if cid != "it" else "IT"
            color = CATEGORIES[cid]["accent"]
            canonical = f"{BASE_URL}/{cid}/"
            e = by_cat.get(cid)
            bullets = list((e.get("top_bullets") if e else []) or [])[:3]
        else:
            tag, color, canonical = (label or f"§{num:02d}"), "#475569", ""
        out.append({
            "number": num,
            "tag": tag,
            "color": color,
            "heading": heading,
            "body": body,
            "bullets": bullets,
            "canonical": canonical,
        })
    return out


def _fallback_sections(editorial: dict[str, Any] | None,
                       by_cat: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """考察 §NN が取れない digest 用の 7-grid fallback。

    §01 総論 / §02-06 各カテゴリ Top1 + bullets / §07 明日へ を必ず描画する。
    §01・§07 は summary_text にフォールバックし、自己ページ表示なので canonical を出さない。
    """
    summary_text = (editorial.get("summary_text") if editorial else "") or ""
    sections: list[dict[str, Any]] = []
    for i in range(7):
        tag = _SUMMARY_SECTION_TAGS[i]
        color = _SUMMARY_SECTION_COLORS[i]
        cid = _SUMMARY_CAT_ORDER[i]
        bullets: list[str] = []
        if i == 0:
            heading = "本日の総論"
            body = summary_text or "本日の総論データ準備中。"
            canonical = ""
        elif i == 6:
            heading = "明日への示唆"
            body = (summary_text[-200:] if len(summary_text) > 200 else summary_text) \
                or "明日への示唆データ準備中。"
            canonical = ""
        else:
            e = by_cat.get(cid) if cid else None
            if e:
                heading = e.get("top_title") or f"{CATEGORIES[cid]['jp']}本日のテーマ"
                body = e.get("summary_text") or f"{CATEGORIES[cid]['jp']}カテゴリのダイジェスト準備中。"
                bullets = list(e.get("top_bullets") or [])[:3]
                canonical = e["canonical"]
            else:
                heading = f"{CATEGORIES[cid]['jp']}本日のテーマ" if cid else tag
                body = "本日は配信日ではありません。"
                canonical = f"{BASE_URL}/{cid}/" if cid else ""
        sections.append({
            "number": i + 1,
            "tag": tag,
            "color": color,
            "heading": heading,
            "body": body,
            "bullets": bullets,
            "canonical": canonical,
        })
    return sections


def build_summary(date: str, entries: list[dict[str, Any]], docs_root: Path,
                  digest_sources: list[Path] | None = None) -> Path:
    """Phase 5: 日付別 Editorial Summary (Pattern D) docs/{date}/summary/index.html を生成。

    γ schema digest があれば reflection 各フィールドを正しく注入。無ければテンプレ
    fallback (lead = summary_text / pull_quote 非表示 / takeaways = Top 3 記事タイトル /
    sections = 6 カテゴリ summary + 総論 / 明日へ プレースホルダ) で必ず描画する。
    """
    same_day = [e for e in entries if e["date"] == date]
    if not same_day:
        raise ValueError(f"build_summary: entries に date={date} が無い")

    # summary digest (同日カテゴリ summary)
    editorial = next((e for e in same_day if e["category_id"] == "summary"), None)

    # reflection は build_context が summary digest 本文から抽出済 (entry に同梱)。
    # subtitle / lead / pull_quote / sections / takeaways を保持する。
    # digest_sources は後方互換のため引数に残すが、現在は entry.reflection を一次ソースにする。
    reflection: dict[str, Any] = (editorial.get("reflection") if editorial else None) or {}

    # 同日カテゴリ entry (sections / takeaways の fallback・bullets で使う)。
    by_cat: dict[str, dict[str, Any]] = {}
    for e in same_day:
        cid = e["category_id"]
        if cid != "summary" and cid not in by_cat:
            by_cat[cid] = e

    # ---- Hero: subtitle / lead ----
    # 考察 lead (多カテゴリ横断) を優先。装飾記法は保持し、テンプレ側で render_emph 描画
    # (マーカー/太字/下線で長文の可読性を上げる)。末尾の遷移句は §セクションへ続くので残す。
    hero_lead = (
        reflection.get("lead")
        or (editorial["summary_text"] if editorial else "")
    ) or "本日の Editorial Digest 準備中。"
    if len(hero_lead) > 260:
        hero_lead = hero_lead[:258] + "…"

    # フレーズは frontmatter `theme:` 由来 (為替偏重だった summary_text から変更)。
    theme_phrase = (editorial.get("theme") if editorial else "") or ""
    left, right = _split_theme_phrases(theme_phrase)
    hero_subtitle = reflection.get("subtitle") or (
        f"{left}と{right}" if left and right else ""
    )

    # ---- Pull quote ----
    pull_quote = reflection.get("pull_quote") or {"text": "", "from": ""}

    # ---- Sections ----
    # 考察 §NN が取れれば data-driven (総論 + 各カテゴリ + 明日へ、digest の節数どおり)。
    # 取れない (考察ブロック非対応の digest) ときは 7-grid fallback。
    sections = _build_essay_sections(reflection.get("sections") or {}, by_cat)
    if sections is None:
        sections = _fallback_sections(editorial, by_cat)

    # ---- Key Takeaways (3 件) ----
    takeaways_raw = reflection.get("takeaways") or []
    if len(takeaways_raw) >= 3:
        takeaways = []
        for i, t in enumerate(takeaways_raw[:3]):
            tag = t.get("tag") or _SUMMARY_SECTION_TAGS[1 + i]
            cid = TAG_TO_CID.get(tag)
            color = CATEGORIES[cid]["accent"] if cid else _SUMMARY_SECTION_COLORS[1 + i]
            takeaways.append({
                "n": i + 1,
                "tag": tag,
                "color": color,
                "text": t.get("text") or "",
            })
    else:
        # Fallback: 同日 Top 3 entries (score 降順) を takeaways に流用
        sorted_by_score = sorted(same_day, key=lambda e: e.get("top_score", 0), reverse=True)
        top3 = sorted_by_score[:3]
        takeaways = []
        for i, e in enumerate(top3):
            takeaways.append({
                "n": i + 1,
                "tag": e["category_label"],
                "color": e["accent"],
                "text": e.get("top_title") or e.get("summary_text", ""),
            })
        # 3 件未満なら空セル詰め
        while len(takeaways) < 3:
            takeaways.append({
                "n": len(takeaways) + 1,
                "tag": "—",
                "color": "#5C5A52",
                "text": "本日の結論準備中。",
            })

    # ---- Stats ----
    sources_count = sum(e.get("articles_count", 0) for e in same_day)
    stats = {
        "sections": len(sections),
        "read_min": 9,
        "takeaways": len(takeaways),
        "sources": sources_count or len(same_day),
    }

    # ---- Render ----
    issue_no = date.replace("-", "")
    canonical = f"{BASE_URL}/{date}/summary/"
    ctx = {
        "site_title": SITE_TITLE,
        "site_tagline": SITE_DESCRIPTION,
        "base_url": BASE_URL,
        "canonical": canonical,

        "date": date,
        "issue_no": issue_no,

        "hero_subtitle": hero_subtitle,
        "hero_lead": hero_lead,

        "pull_quote": pull_quote,
        "sections": sections,
        "takeaways": takeaways,
        "stats": stats,
    }
    out = Path(docs_root) / date / "summary" / "index.html"
    return render_page(ctx, out, template_name="summary-template.html")


def build_all_summaries(entries: list[dict[str, Any]], docs_root: Path,
                        digest_sources: list[Path] | None = None) -> list[Path]:
    """全 unique date について summary ページを生成。

    digest_sources を渡すと build_summary 側で digest/Summary/*.md の `### §NN`
    本文を抽出し §01 / §07 を全文表示する (2026-05-26 統合)。
    """
    unique_dates = sorted({e["date"] for e in entries if e.get("date")}, reverse=True)
    written: list[Path] = []
    for d in unique_dates:
        try:
            written.append(build_summary(d, entries, docs_root,
                                         digest_sources=digest_sources))
        except Exception as exc:
            print(f"[warn] summary build failed for {d}: {exc}", file=sys.stderr)
    return written


def build_category_pages(entries: list[dict[str, Any]], docs_root: Path) -> list[Path]:
    """カテゴリ別アーカイブ docs/{cat}/index.html を生成 (v2 Magazine Spread)。

    v2 リデザイン (Phase 3 / 2026-05-28): リスト型 71 行から B Magazine Spread
    (hero + TOP feature + 9 grid + past 7) にフル置換。ctx を追加：

        featured  : 最新 1 件 (= cat_entries[0]) を TOP feature と hero summary に流用
        grid_9    : featured を除く直近 9 件 (cat_entries[1:10])。SVG 構図と一致
        past_7    : featured を除く直近 7 日 (cat_entries[1:8])。
                    各カードは <a href="{base_url}/{cat_id}/{date}/"> で詳細ページにラップ
        nav_categories : 6 lens pill 用 (summary 除く)、is_active で現カテゴリ強調
    """
    written: list[Path] = []
    # nav_categories は全 cat 共通 (is_active は内側でセット)。summary 除く 6 lens
    nav_base = [
        {
            "id": cid,
            "name_en": meta["label"],
            "name_jp": meta["jp"],
            "glyph": meta["glyph"],
            "accent": meta["accent"],
        }
        for cid, meta in CATEGORIES.items()
        if cid != "summary"
    ]
    for cat_id, cat in CATEGORIES.items():
        # 統合方針 (2026-05-26): summary カテゴリのアーカイブ /summary/ は廃止
        # (日付別考察 /{date}/summary/ に統合)。
        if cat_id == "summary":
            continue
        cat_entries = [e for e in entries if e["category_id"] == cat_id]
        if not cat_entries:
            continue
        # 日付降順は _collect_entries で保証済だが、念のため
        cat_entries_sorted = sorted(cat_entries, key=lambda e: e["date"], reverse=True)
        featured = cat_entries_sorted[0]
        if len(cat_entries_sorted) >= 2:
            grid_9 = cat_entries_sorted[1:10]
            past_7 = cat_entries_sorted[1:8]
        else:
            # data 不足 (= backfill 未着手の新設カテゴリ) の fallback:
            # data/articles.jsonl の同日 5 記事を grid に展開して、他カテゴリと粒度を揃える
            grid_9 = _articles_as_grid_entries(
                cat_id, featured["date"], skip_url=featured.get("top_source_url")
            )
            past_7 = []
        nav_categories = [
            {**n, "is_active": (n["id"] == cat_id)} for n in nav_base
        ]
        ctx = {
            "site_title": SITE_TITLE,
            "base_url": BASE_URL,
            "category_id": cat_id,
            "category_label": cat["label"],
            "category_jp": cat["jp"],
            "accent": cat["accent"],
            "glyph": cat["glyph"],
            "canonical": f"{BASE_URL}/{cat_id}/",
            "entries": cat_entries_sorted,
            "featured": featured,
            "grid_9": grid_9,
            "past_7": past_7,
            "nav_categories": nav_categories,
        }
        out = Path(docs_root) / cat_id / "index.html"
        written.append(render_page(ctx, out, template_name="category-template.html"))
    return written


def _archive_headline(summary_text: str, lead: dict[str, Any] | None) -> str:
    """号の見出し。総括 (summary_text) 先頭 1 文を優先、無ければ lead 記事タイトル。"""
    if summary_text:
        head = summary_text.split("。", 1)[0].strip()
        # マーカー (** __ [[]]) は render_emph フィルタ側で装飾するためここでは残す
        if head:
            return head
    if lead:
        return lead["title"]
    return "この日は準備中"


def build_archive(entries: list[dict[str, Any]], docs_root: Path) -> Path:
    """日付横断アーカイブ docs/archive/index.html を生成。

    Claude Design "News Grasp Archive" (Editorial Timeline / Variant B) に準拠。
    日付ごとに 1 号 (issue) としてまとめ、各カテゴリの最上位記事を stories に整形する。
    """
    lens_order = [cid for cid in CATEGORIES if cid != "summary"]

    cats_meta = [
        {
            "id": cid,
            "jp": CATEGORIES[cid]["jp"],
            "en": cid.upper(),
            "label": CATEGORIES[cid]["label"],
            "accent": CATEGORIES[cid]["accent"],
            "glyph": CATEGORIES[cid]["glyph"],
            "is_new": cid == "mobility",
        }
        for cid in lens_order
    ]

    by_date: dict[str, list[dict[str, Any]]] = {}
    for e in entries:
        by_date.setdefault(e["date"], []).append(e)

    dates_desc = sorted(by_date.keys(), reverse=True)
    dates_asc = list(reversed(dates_desc))
    seq_no = {d: i + 1 for i, d in enumerate(dates_asc)}  # 最古号 = 1

    cover_count = {cid: 0 for cid in lens_order}
    month_counts: dict[str, int] = {}
    total_cat_pages = 0

    def _score_int(v: Any) -> int:
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0

    issues: list[dict[str, Any]] = []
    for d in dates_desc:
        day_entries = by_date[d]
        summary_e = next((e for e in day_entries if e["category_id"] == "summary"), None)
        cat_entries = [e for e in day_entries if e["category_id"] in CATEGORIES and e["category_id"] != "summary"]

        stories: list[dict[str, Any]] = []
        for e in cat_entries:
            cid = e["category_id"]
            meta = CATEGORIES[cid]
            stories.append({
                "cat": cid,
                "title": e.get("top_title") or e.get("title") or meta["jp"],
                "score": _score_int(e.get("top_score")),
                "accent": meta["accent"],
                "glyph": meta["glyph"],
                "en": cid.upper(),
                "jp": meta["jp"],
                "href": e.get("canonical", "#"),
            })
            cover_count[cid] += 1
            total_cat_pages += 1
        stories.sort(key=lambda s: (-s["score"], lens_order.index(s["cat"])))

        lead = stories[0] if stories else None
        summary_text = (summary_e or {}).get("summary_text", "") if summary_e else ""
        month = d[:7]
        month_counts[month] = month_counts.get(month, 0) + 1

        issues.append({
            "date": d,
            "no": seq_no[d],
            "weekday": _date_weekday_jp(d),
            "day": d[8:10],
            "ym": d[:7],
            "month": month,
            "month_label": _MONTH_ABBR[int(d[5:7]) - 1],
            "year": d[:4],
            "featured": False,  # 最新号を下で True に
            "sparse": lead is None,
            "headline": _archive_headline(summary_text, lead),
            "essay": summary_text,
            "has_summary": summary_e is not None,
            "summary_href": (summary_e or {}).get("canonical", "") if summary_e else "",
            "open_href": (lead["href"] if lead else ((summary_e or {}).get("canonical") if summary_e else "#")),
            "stories": stories,
            "lead": lead,
            "rest": stories[1:],
            "cats": [s["cat"] for s in stories],
        })
    if issues:
        issues[0]["featured"] = True

    n_issues = len(dates_desc)
    n_essays = sum(1 for it in issues if it["has_summary"])

    most_covered = None
    if n_issues and any(cover_count.values()):
        most_cid = max(lens_order, key=lambda c: cover_count[c])
        most_covered = {
            "jp": CATEGORIES[most_cid]["jp"],
            "glyph": CATEGORIES[most_cid]["glyph"],
            "accent": CATEGORIES[most_cid]["accent"],
            "count": cover_count[most_cid],
            "pct": round(cover_count[most_cid] * 100 / n_issues),
        }

    months_meta = [
        {"id": m, "label": _MONTH_ABBR[int(m[5:7]) - 1], "year": m[:4], "count": month_counts[m]}
        for m in sorted(month_counts.keys())  # 古い → 新しい (ジャンプ用ボタン)
    ]

    stats = {
        "days": n_issues,
        "pages": total_cat_pages,
        "essays": n_essays,
        "span_from": dates_asc[0] if dates_asc else "",
        "span_to": dates_desc[0] if dates_desc else "",
    }

    ctx = {
        "site_title": SITE_TITLE,
        "base_url": BASE_URL,
        "canonical": f"{BASE_URL}/archive/",
        "issues": issues,
        "cats_meta": cats_meta,
        "months_meta": months_meta,
        "month_counts": month_counts,
        "stats": stats,
        "most_covered": most_covered,
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
        digests_all = scan_digests()
        entries = _collect_entries(digests_all)
        idx = build_index(entries, docs_root)
        cat_pages = build_category_pages(entries, docs_root)
        arc = build_archive(entries, docs_root)
        overviews = build_all_overviews(entries, docs_root)
        summaries = build_all_summaries(entries, docs_root,
                                        digest_sources=digests_all)
        print(
            f"wrote index/archive: {idx.name}, {len(cat_pages)} category page(s), "
            f"{arc.parent.name}/{arc.name}, {len(overviews)} overview page(s), "
            f"{len(summaries)} summary page(s)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
