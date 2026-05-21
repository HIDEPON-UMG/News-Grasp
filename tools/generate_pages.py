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

    return {
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
    }


# ---------- Jinja2 render ----------

_TEMPLATE_DIR = _PKG_ROOT / "prompts"
_jinja_env = None


def _get_jinja_env():
    """Jinja2 Environment を lazy 初期化 (テンプレ未配置時は import エラーを後ろ倒し)。"""
    global _jinja_env
    if _jinja_env is None:
        from jinja2 import Environment, FileSystemLoader, select_autoescape

        _jinja_env = Environment(
            loader=FileSystemLoader(str(_TEMPLATE_DIR)),
            autoescape=select_autoescape(["html"]),
            keep_trailing_newline=True,
        )
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
    """個別ページ ctx から index / category / archive 用の軽量 entry を抽出。"""
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


def build_index(entries: list[dict[str, Any]], docs_root: Path,
                recent_days: int = TOP_RECENT_DAYS) -> Path:
    """トップページ docs/index.html を生成。直近 recent_days 日分のカードを並べる。"""
    from datetime import date as _date

    # 一意な日付の降順から recent_days 日分を抜き出して、その日に出た全 entry を集める。
    dates_sorted = sorted({e["date"] for e in entries}, reverse=True)
    recent_dates = set(dates_sorted[:recent_days])
    recent = [e for e in entries if e["date"] in recent_dates]

    ctx = {
        "site_title": SITE_TITLE,
        "site_tagline": SITE_DESCRIPTION,
        "base_url": BASE_URL,
        "recent": recent,
        "recent_days": recent_days,
        "total_pages": len(entries),
        "categories": [{"id": k, **v} for k, v in CATEGORIES.items()],
    }
    out = Path(docs_root) / "index.html"
    return render_page(ctx, out, template_name="index-template.html")


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
        print(f"wrote index/archive: {idx.name}, {len(cat_pages)} category page(s), {arc.parent.name}/{arc.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
