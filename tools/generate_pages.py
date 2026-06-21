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
import json
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
    INK,
    OG_DESCRIPTION_MAX,
    SITE_DESCRIPTION,
    SITE_TAGLINE_EN,
    SITE_TITLE,
    TOP_RECENT_DAYS,
)
from tools.dedup import same_event_by_tokens, significant_tokens  # noqa: E402  (表示層 dedup で再利用)
from tools.tts.deepdive_audio import deepdive_audio_for_pages  # noqa: E402

_LATEST_AUDIO_JSON = _PKG_ROOT / "build" / "tts" / "latest_audio.json"
_TTS_BUILD_DIR = _PKG_ROOT / "build" / "tts"

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
# 英文タイトル直下の和訳サブタイトル: `> [!ja] マイクロソフト、…` (Obsidian / GitHub callout)
# 全文英文の記事に対してのみ digest md で 1 行付与し、page-template.html で
# `<p class="story-title-ja">` として英文タイトル直下に小サイズ表示する。
_TITLE_JA_RE = re.compile(r"^>\s*\[!ja\]\s*(.+?)\s*$", re.MULTILINE)
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
    "製造": "manufacturing",
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

    # 英文タイトル直下の和訳サブタイトル (任意・英文タイトルのみ付与)
    title_ja = ""
    jm = _TITLE_JA_RE.search(block)
    if jm:
        title_ja = jm.group(1).strip()

    # bullets (HTML inline 変換済み)
    bullets: list[str] = []
    for bm in _BULLET_RE.finditer(block):
        bullets.append(inline_html(bm.group(1).strip()))

    return {
        "title": title,
        "title_ja": title_ja,
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
        category_id = _resolve_cat_from_dirname(Path(digest_path).parent.name) or ""
    if category_id not in CATEGORIES:
        # 未知ディレクトリ / 非カテゴリ digest (kind: deepdive 等) は LP に載せない。
        # ここで summary に既定化すると本物の Summary digest を「同日 2 本目の summary」で
        # シャドーし、editorial が reflection 空の側を拾って LP のテーマ・考察が全滅する
        # (2026-05-31 DeepDive 事故)。date/category_id を空で返し、呼び出し側
        # (build_all / _collect_entries) の date/category_id ガードで skip させる。
        return {"category_id": "", "date": fm.get("date", ""), "kind": (fm.get("kind") or "").lower()}
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
    # og/meta description は装飾記法 ([[ ]] ** __) を HTML 化できないので素テキスト化する。
    # summary_text 自体は cat-hero / editorial 側で render_emph に渡るため記法を残す。
    og_description = truncate(strip_inline(summary_text), OG_DESCRIPTION_MAX)

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
        "accent": cat["accent"],
        "glyph": cat["glyph"],
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
        "hero_left": fm.get("hero_left", ""),
        "hero_right": fm.get("hero_right", ""),
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
            # [[X|Y]] -> Y, [[X]] -> X として bold + accent 背景。
            # s は上で escape 済みなので捕捉群を再 escape しない (再 escape すると
            # [[S&P500]] が S&amp;amp;P500 と二重エスケープされて画面に化ける)。inline_html と統一。
            s = _WIKILINK_RE.sub(
                lambda m: f'<strong class="emph-bold">{m.group(2) or m.group(1)}</strong>',
                s,
            )
            # __X__ -> underline + bold
            s = _UNDERLINE_RE.sub(r'<span class="emph-und">\1</span>', s)
            # **X** -> bold (累積)
            s = _BOLD_RE.sub(r'<strong>\1</strong>', s)
            return Markup(s)

        def _insert_wbr(text: str) -> Markup:
            """日本語句読点 (、。，．・) の直後に <wbr> を挿入した安全 HTML を返す。

            word-break: keep-all と組み合わせ、CJK の中途改行を禁じつつ
            文節境界 (句読点) でのみ折り返しを許す。半角空白も <wbr> 同等で
            break opportunity になるため追加挿入しない。
            ホーム hero 見出しのように
            「AI バブルか革命か、円安か利上げか」を 1 行に収まらないときだけ
            「AI バブルか革命か、」+ 「円安か利上げか」の文節単位で改行する
            目的で導入 (2026-06-05)。
            """
            if text is None:
                return Markup("")
            s = _html.escape(str(text), quote=False)
            s = re.sub(r"([、。，．・])", r"\1<wbr>", s)
            return Markup(s)

        _jinja_env.filters["render_emph"] = _render_emph
        _jinja_env.filters["insert_wbr"] = _insert_wbr
    return _jinja_env


def render_page(ctx: dict[str, Any], out_path: Path, template_name: str = "page-template.html") -> Path:
    """ctx を Jinja2 テンプレで render し UTF-8 で out_path に書き出す。"""
    env = _get_jinja_env()
    template = env.get_template(template_name)
    html_text = template.render(**ctx)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_text, encoding="utf-8", newline="\n")
    return out_path


def _local_audio_for_pages(date: str | None) -> dict[str, str]:
    if not date:
        return {"latest_audio_url": "", "latest_audio_date": ""}
    mp3_path = _TTS_BUILD_DIR / f"{date}.mp3"
    if not mp3_path.exists():
        return {"latest_audio_url": "", "latest_audio_date": ""}
    from tools.tts.publish_audio import versioned_audio_url

    return {
        "latest_audio_url": versioned_audio_url(date, mp3_path),
        "latest_audio_date": date,
    }


def latest_audio_for_pages(date: str | None = None) -> dict[str, str]:
    """音声ステップが 200 確認済みで書いた最新 mp3 URL を SSG コンテキストへ渡す。"""
    if not _LATEST_AUDIO_JSON.exists():
        return _local_audio_for_pages(date)
    try:
        data = json.loads(_LATEST_AUDIO_JSON.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return _local_audio_for_pages(date)
    if date and data.get("latest_audio_date") != date:
        return _local_audio_for_pages(date)
    return {
        "latest_audio_url": str(data.get("latest_audio_url") or ""),
        "latest_audio_date": str(data.get("latest_audio_date") or ""),
    }


# ---------- digest scanner ----------

def validate_ja_callout_coverage() -> list[str]:
    """全 digest md (DeepDive 除く) の「メタ行を持つ英文 ### 記事」に
    `> [!ja] 和訳` callout が必ず付いていることを確認。欠落リストを返す。

    feedback_check_design_principles Lv1 (illegal state unrepresentable):
    付与漏れがある状態を「ビルド成功」として表現できない構造にする。
    本関数は main() の冒頭で呼び、欠落があれば即 exit 1。

    Lv4 契約テスト (tests/test_title_ja_coverage.py) と同じルールを build パイプライン
    にも組み込むことで、pytest をスキップした手動 push 等の抜け道を塞ぐ。
    """
    digest_root = _PKG_ROOT / "digest"
    if not digest_root.exists():
        return []
    missing: list[str] = []
    article_split = re.compile(r"^(?=### )", re.MULTILINE)
    title_re = re.compile(r"^###\s*(?:\[(\d+)\]\s*)?(.+?)\s*$", re.MULTILINE)
    ja_re = re.compile(r"^>\s*\[!ja\]\s*(.+?)\s*$", re.MULTILINE)
    meta_re = re.compile(r"📅|🔗|📰")

    def _is_english(title: str) -> bool:
        return not any(
            "぀" <= c <= "ヿ"
            or "一" <= c <= "鿿"
            or "＀" <= c <= "￯"
            for c in title
        )

    for md in sorted(digest_root.rglob("*.md")):
        if "DeepDive" in md.name:
            continue
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue
        for block in article_split.split(text):
            tm = title_re.match(block)
            if not tm:
                continue
            title = tm.group(2).strip()
            if not _is_english(title):
                continue
            if not meta_re.search(block):
                continue
            if not ja_re.search(block):
                rel = md.relative_to(digest_root).as_posix()
                missing.append(f"  - {rel}: {title[:80]}")
    return missing


def scan_digests(root: Path | None = None) -> list[Path]:
    """digest/**/*.md を全て列挙して mtime 昇順で返す (古い → 新しい)。"""
    base = Path(root) if root else (_PKG_ROOT / "digest")
    if not base.exists():
        return []
    paths = [
        p for p in base.rglob("*.md")
        if p.is_file() and not p.name.endswith("-audio-script.md")
    ]
    paths.sort(key=lambda p: p.stat().st_mtime)
    return paths


def _out_path_for(ctx: dict[str, Any], docs_root: Path) -> Path:
    """ctx の category_id / date から `docs/{cat}/{YYYY-MM-DD}/index.html` を作る。"""
    return docs_root / ctx["category_id"] / ctx["date"] / "index.html"


def _templates_mtime(template_dir: Path = _TEMPLATE_DIR) -> float:
    """prompts/ 配下テンプレート群 (_partials の include 含む) の最新 mtime。

    「どのページがどのテンプレを使うか」の対応表を持たず、テンプレが 1 つでも
    更新されたら全 incremental ページを stale 扱いにするための単一の指標。
    対応表を持たない = テンプレ追加時に「このページの依存に入れ忘れる」取りこぼし
    (= バグ再発) が構造的に起きない。rglob で _partials も拾う。
    """
    times = [p.stat().st_mtime for p in template_dir.rglob("*.html")]
    return max(times) if times else 0.0


def _needs_rebuild(src: Path, out: Path, template_mtime: float = 0.0) -> bool:
    """out が src またはテンプレ群 (template_mtime) より古ければ再生成が必要。

    src だけでなく template_mtime も判定に含めるのが要点。これを忘れると
    「テンプレを張り替えても src md の mtime が据え置きで古い HTML が残る」
    class of bug (2026-06-01 DeepDive 旧テーマ書架リンク残存事故) を踏む。
    増分判定は本関数 1 箇所に集約し、DeepDive 個別記事 (render_deepdive
    .build_deepdive_pages) もインライン複製せず本関数を共有して通す。
    """
    if not out.exists():
        return True
    out_mtime = out.stat().st_mtime
    return src.stat().st_mtime > out_mtime or template_mtime > out_mtime


def build_all(*, full: bool = False, docs_root: Path | None = None, digests: Iterable[Path] | None = None) -> list[Path]:
    """全 digest を render。--full なら mtime 判定を無視して全件再生成。"""
    docs = Path(docs_root) if docs_root else (_PKG_ROOT / "docs")
    sources = list(digests) if digests is not None else scan_digests()
    written: list[Path] = []
    # 1st pass: 全 ctx を構築 (build_context は元々 src 毎に呼んでおり追加コストは無い)。
    # 「本日のテーマ考察」navy band にカテゴリ固有の装飾本文を出すため、同日 summary digest の
    # reflection を date→reflection で先に集める (category-template と同じ仕組みを page にも適用)。
    built: list[tuple[Path, dict[str, Any]]] = []
    for src in sources:
        try:
            ctx = build_context(src)
        except Exception as exc:
            print(f"[warn] failed to build context for {src.name}: {exc}", file=sys.stderr)
            continue
        if not ctx.get("date") or not ctx.get("category_id"):
            print(f"[skip] missing date/category_id: {src.name}", file=sys.stderr)
            continue
        built.append((src, ctx))
    summary_reflection_by_date = {
        ctx["date"]: (ctx.get("reflection") or {})
        for _, ctx in built if ctx["category_id"] == "summary"
    }
    tmpl_mtime = _templates_mtime()  # テンプレ変更も増分判定に含める (古い HTML 残存を防ぐ)
    for src, ctx in built:
        # 統合方針 (2026-05-26): summary カテゴリの個別ページ /summary/{date}/ は廃止し、
        # /{date}/summary/ (build_summary 出力) に統合した。digest/Summary/*.md は
        # build_summary 側でのみ消費するため、ここでは個別ページ生成をスキップする。
        if ctx["category_id"] == "summary":
            continue
        # カテゴリ固有の考察 (装飾記法 ** __ [[]] 付き) を同日 summary の §NN から注入。
        # 該当節が無ければ ("", "") で、テンプレ側が summary_text (プレーン) に fallback する。
        refl = summary_reflection_by_date.get(ctx["date"]) or {}
        ctx["editorial_heading"], ctx["editorial_essay"] = _category_essay(refl, ctx["category_id"])
        out = _out_path_for(ctx, docs)
        if not full and not _needs_rebuild(src, out, tmpl_mtime):
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
    cat_meta = CATEGORIES.get(ctx["category_id"], {})
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
    articles_count = 0 if ctx["category_id"] == "summary" else len(all_articles)
    top3 = [
        {
            "title": a.get("title", ""),
            "title_ja": a.get("title_ja", ""),
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
        "category_label": cat_meta.get("label", ctx["category_label"]),
        "category_jp": cat_meta.get("jp", ctx["category_jp"]),
        "canonical": ctx["canonical"],
        "summary_text": ctx.get("summary_text", ""),
        "theme": ctx.get("theme", ""),
        "hero_left": ctx.get("hero_left", ""),
        "hero_right": ctx.get("hero_right", ""),
        "reflection": ctx.get("reflection") or {},
        "og_image": ctx["og_image"],
        "accent": cat_meta.get("accent", ctx["accent"]),
        "glyph": cat_meta.get("glyph", ctx["glyph"]),
        # Variant B Home 用
        "top_score": top_score_int,
        "top_title": top.get("title", ""),
        "top_title_ja": top.get("title_ja", ""),
        "top_thumb": top.get("thumb", ""),
        "top_source": top.get("source", ""),
        "top_source_url": top.get("source_url", ""),
        "top_date": top.get("date", ""),
        "top_bullets": top.get("bullets", []),
        "articles_count": articles_count,
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
#   月: FX/AI/IT/Mobility/Manufacturing/Economy (6)
#   火: + Game (7)
#   水: 月と同じ (6)
#   木: 火と同じ (7)
#   金: 月と同じ (6)
#   土: FX/AI/IT/Mobility/Game (Manufacturing・Economy 除く、5)
#   日: 土と同じ (5)
_PUBLICATION_SCHEDULE: dict[int, set[str]] = {
    0: {"fx", "ai", "it", "mobility", "manufacturing", "economy"},          # 月
    1: {"fx", "ai", "it", "mobility", "manufacturing", "economy", "game"},  # 火
    2: {"fx", "ai", "it", "mobility", "manufacturing", "economy"},          # 水
    3: {"fx", "ai", "it", "mobility", "manufacturing", "economy", "game"},  # 木
    4: {"fx", "ai", "it", "mobility", "manufacturing", "economy"},          # 金
    5: {"fx", "ai", "it", "mobility", "game"},             # 土
    6: {"fx", "ai", "it", "mobility", "game"},             # 日
}


def is_category_scheduled_on(cat_id: str, date_str: str) -> bool:
    """指定日がカテゴリの配信日なら True。"""
    from datetime import date as _date
    if not date_str or len(date_str) < 10:
        return True
    try:
        weekday = _date(int(date_str[0:4]), int(date_str[5:7]), int(date_str[8:10])).weekday()
    except (ValueError, IndexError):
        return True
    return cat_id in _PUBLICATION_SCHEDULE.get(weekday, set())


def _category_pause_notice(cat_id: str, today_date: str) -> dict[str, str] | None:
    """今日の配信対象外カテゴリならカテゴリトップ用の休載表示を返す。"""
    if is_category_scheduled_on(cat_id, today_date):
        return None
    weekday = _date_weekday_jp(today_date)
    return {
        "title": "本日は休載です。",
        "label": "REST DAY",
        "date": today_date,
        "weekday": weekday,
        "body": "このカテゴリは本日の配信対象外です。直近の掲載号を下に表示しています。",
    }


# cat_id → data/articles.jsonl の "genre" 表記揺れ吸収マッピング
_GENRE_ALIASES: dict[str, set[str]] = {
    "fx":       {"FX", "Foreign Exchange"},
    "ai":       {"AI", "Artificial Intelligence"},
    "it":       {"IT", "IT-Consulting", "IT & Consulting"},
    "mobility": {"Mobility"},
    "manufacturing": {"Manufacturing"},
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
                               skip_url: str | None = None,
                               digests: Iterable[Path] | None = None) -> list[dict[str, Any]]:
    """同日同カテゴリの digest md を解析し more-card 用 entry-like dict に変換する fallback。

    backfill 直後の新設カテゴリ (Manufacturing 等) は digest が 1 日分しか無く、通常の
    grid (他日付の featured を並べる) を埋められない。その場合に同日 digest の全記事を
    grid に展開する。bullets は inline_html 済みを保持し、テンプレ側 ``{{ b|safe }}`` で
    強調が描画される。featured (skip_url) は除外し score 降順で最大 9 件返す。

    2026-06-04 修正: 旧実装は articles.jsonl の raw summary をそのまま summary_text に
    渡し top_bullets を空にしていたため、カテゴリ索引ページ (/{cat}/) の grid カードで
    ``**bold**`` / ``[[wikilink]]`` が生 markdown のまま表示されていた (Manufacturing
    初回 backfill で露見)。digest md から整形済み bullets を取り直すことで根治する。
    """
    cat_meta = CATEGORIES.get(cat_id, {})

    def _score_int(v: Any) -> int:
        return int(v) if str(v).strip().isdigit() else 0

    # 1) 本筋: 同日同カテゴリの digest md を探し、整形済み bullets ごと grid 化する。
    for src in (list(digests) if digests is not None else scan_digests()):
        try:
            fm, body = parse_frontmatter(src.read_text(encoding="utf-8"))
        except OSError:
            continue
        if fm.get("categoryId") != cat_id or fm.get("date") != date:
            continue
        out: list[dict[str, Any]] = []
        for a in parse_articles(body):
            url = a.get("source_url", "")
            if skip_url and url == skip_url:
                continue
            out.append({
                "date": date,
                "top_title": a.get("title", ""),
                "top_title_ja": a.get("title_ja", ""),
                "top_thumb": a.get("thumb"),
                "top_source": a.get("source"),
                "top_source_url": url,
                "top_score": _score_int(a.get("score")),
                "summary_text": "",
                "top_bullets": a.get("bullets", []),
                "canonical": url,
                "category_id": cat_id,
                "category_label": cat_meta.get("label", cat_id),
                "category_jp": cat_meta.get("jp", cat_id),
                "accent": cat_meta.get("accent", ""),
                "glyph": cat_meta.get("glyph", ""),
            })
        out.sort(key=lambda x: x.get("top_score") or 0, reverse=True)
        return out[:9]

    # 2) digest 不在の異常時のみ articles.jsonl で埋める。装飾記法は strip_inline で
    #    剥がし、raw markdown が画面に出ないようにする (bullets は持てないので素テキスト)。
    import json as _json
    p = _PKG_ROOT / "data" / "articles.jsonl"
    if not p.exists():
        return []
    aliases = _GENRE_ALIASES.get(cat_id, set())
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            o = _json.loads(line)
        except _json.JSONDecodeError:
            continue
        if o.get("genre", "") not in aliases and o.get("category_id") != cat_id:
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
            "top_score": _score_int(o.get("score")),
            "summary_text": strip_inline(o.get("summary", "") or ""),
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


_THEME_SPLIT_MAX_LEN = 22  # 2026-06-05 14→22 緩和。LLM の長文 theme で fallback 常態化対策。
# 2026-06-05 強化: em dash ` — ` / 中黒 `・` / em dash 等を sep に追加。
# 旧 (sep=(と)) のみ だと 7 日中 2 日 fallback 発火していた:
#   06-04: 「BOJとWarshの6月決戦・AIの内製化と自動運転の量戦」(left 候補が 14 字超で空)
#   06-05: 「IPO 三つ巴と160円の壁 — AI バブルか革命か、円安か利上げか」(right 候補が 22 字超で空)
# 長すぎる right は更に内側の sep (「、」「・」「と」) で二次短縮を試みる。
_THEME_PRIMARY_SEPS = (" — ", " ― ", "—", "―", " と ", "・", "と", "、")
_THEME_SECONDARY_SEPS = ("、", "・", " と ", "と")


def _split_theme_phrases(summary_text: str) -> tuple[str, str]:
    """summary_text から Hero 用の 2 フレーズ ("金利の天井" "AIの底入れ" 風) を抽出。

    句読点 (「。」「、」「・」)・em dash (「 — 」「—」) で切り、最初の名詞句 2 つを返す。
    上限は左右各 ``_THEME_SPLIT_MAX_LEN`` 文字。取れなければ ("", "") を返し、
    テンプレ側のフォールバックに任せる。

    2026-06-05 強化 (Lv2 境界 1 箇所集約):
      - sep に em dash と中黒を追加し、LLM が書く長文 theme (例「X — Y」「X・Y」) に対応
      - 上限を 14 → 22 に緩和
      - 一次分割で right が長すぎたら内側 sep で更に二次短縮を試みる
      - 契約テスト ``tests/test_split_theme_phrases.py`` が直近 7 日 theme + 典型
        LLM パターンで空を返さないことを locked-in (= class of bugs 再発防止)
    """
    if not summary_text:
        return ("", "")
    # 最初の「。」までを切る
    head = summary_text.split("。", 1)[0].strip()
    # 一次分割: 強い sep (em dash → 「と」「・」「、」の順) で 2 句に切る
    for sep in _THEME_PRIMARY_SEPS:
        if sep in head:
            parts = [p.strip() for p in head.split(sep, 1)]
            if len(parts) != 2 or not (parts[0] and parts[1]):
                continue
            left = parts[0]
            # 末尾の英文節 (". Read more" 等) だけを落とす。小数点 (3.8% 等) は残す
            # ため、"." の後ろが空白/英字のときのみ分割する。
            right = re.split(r"\.(?=\s|[A-Za-z])", parts[1].split("。", 1)[0], maxsplit=1)[0].strip()
            # 二次短縮: 一次分割で取れた right が長すぎる場合、内側 sep で更に短縮を試みる。
            # 「AI バブルか革命か、円安か利上げか」→ 「AI バブルか革命か」
            if len(right) > _THEME_SPLIT_MAX_LEN:
                for sub in _THEME_SECONDARY_SEPS:
                    if sub in right:
                        sub_first = right.split(sub, 1)[0].strip()
                        if 2 <= len(sub_first) <= _THEME_SPLIT_MAX_LEN:
                            right = sub_first
                            break
            # left も同様に長すぎる場合は二次短縮 (但しレアケース。06-04 では left 側で発生)
            if len(left) > _THEME_SPLIT_MAX_LEN:
                for sub in _THEME_SECONDARY_SEPS:
                    if sub in left:
                        sub_first = left.split(sub, 1)[0].strip()
                        if 2 <= len(sub_first) <= _THEME_SPLIT_MAX_LEN:
                            left = sub_first
                            break
            if 2 <= len(left) <= _THEME_SPLIT_MAX_LEN and 2 <= len(right) <= _THEME_SPLIT_MAX_LEN:
                return (left, right)
    return ("", "")


def _hero_phrases(editorial: dict[str, Any] | None) -> tuple[str, str]:
    """Hero 2 トーン見出し (hl-gold / hl-blue) 用の左右フレーズを取得する。

    frontmatter `hero_left` / `hero_right` (LLM が当日オーサする短い 2 句) を
    最優先で使い、両方揃うときだけ採用する。欠ける過去 digest では従来の
    ``_split_theme_phrases`` (theme の機械分割) にフォールバックする。

    2026-06-06: 機械分割が長文 theme を断片化し「Gemma 4 12B と AI」のように
    文意を失う事故 (LP hero の意味不明改行) を受け、LLM 直接出力を一次ソース化。
    機械分割は過去 digest 互換のためフォールバックとして温存する。
    """
    if editorial:
        left = (editorial.get("hero_left") or "").strip()
        right = (editorial.get("hero_right") or "").strip()
        if left and right:
            return (left, right)
    theme_phrase = (editorial.get("theme") if editorial else "") or ""
    return _split_theme_phrases(theme_phrase)


def _emphasize_entities(text: str, tags: list[str]) -> str:
    """text 内に出現する固有名詞 (tags 由来) を hl-gold マーカーで強調した安全 HTML。

    Summary 側はダイジェスト本文に埋め込まれた強調記法を render_emph で描くが、
    DeepDive の theme/title は素テキスト。そこで DeepDive が実際に持つ tags (= 固有
    名詞/キーワード) を text 内で探して同系統のマーカーを当てる。語は DeepDive 自身の
    メタデータ由来なので恣意的な語選びにならない。長い語を優先し二重置換を防ぐ。
    """
    import html as _h
    esc = _h.escape(text)
    skip = {"deepdive", "weekly", "news-grasp", "news grasp"}
    terms = sorted(
        {t for t in (tags or [])
         if len(t) >= 2 and t.lower() not in skip and not t.lower().startswith("issue-")},
        key=len, reverse=True,
    )
    spans: list[str] = []
    for term in terms:
        et = _h.escape(term)
        if et and et in esc:
            esc = esc.replace(et, f"\x00{len(spans)}\x00")
            spans.append(f'<span class="hl-gold">{et}</span>')
    for i, span in enumerate(spans):
        esc = esc.replace(f"\x00{i}\x00", span)
    return esc


def _deepdive_report_items(dd: dict[str, Any]) -> list[str]:
    """DeepDive context の実ブロックから「IN THIS REPORT」manifest を生成する。

    デザイン (deepdive-ia.jsx) の固定サンプルではなく **実データ** から組む
    (relations/table が無い回もあるため)。RELATIONS / TABLE は design 通り ★ を付す。
    """
    items: list[str] = []
    if dd.get("timeline"):
        items.append("時系列 TIMELINE")
    if dd.get("players"):
        items.append("当事者 PLAYERS")
    if dd.get("relations_svg"):
        items.append("関係図 RELATIONS ★")
    n_charts = len(dd.get("charts") or [])
    if n_charts:
        items.append(f"数値 CHART ×{n_charts}" if n_charts > 1 else "数値 CHART")
    if dd.get("table"):
        items.append("データ表 TABLE ★")
    if dd.get("decision"):
        items.append("意思決定 DECISION")
    return items


def _latest_deepdive_card(target_date: str | None = None) -> dict[str, Any] | None:
    """最新 DeepDive (週次 TODAY'S THEME) を LP 上部ヒーローの
    SUMMARY ⇆ DEEP DIVE スライダー用に 1 枚分のデータへ整形する。

    `target_date` を渡すと、その日付 **以前 (<=)** に公開された最新 DeepDive を選ぶ。
    昨日 LP (docs/{昨日}/index.html) で target_date=昨日 を渡すことで、当日 LP と
    同じ「最新 DeepDive」を載せてしまう不具合 (昨日 LP の DEEP DIVE スライダーに
    当日のテーマが出る) を防ぐ。DeepDive は休載日があるため「当日ちょうど」でなく
    「その日以前の最新」で引く。None (当日 LP) なら従来どおり全体の最新を返す。

    不変条件 (2026-05-31 事故) の本質は「日次 digest の **entry ストリーム**
    (build_all / _collect_entries) を DeepDive で汚染しない」こと。ここでは
    entries を一切触らず digest/DeepDive/*.md を **直接** 読んで LP の独立 pane
    に明示注入するため、entry 汚染は起きず不変条件と両立する。DeepDive が
    無い・壊れているときは None を返し、テンプレ側でトグル自体を出さない。
    """
    src_dir = _PKG_ROOT / "digest" / "DeepDive"
    if not src_dir.exists():
        return None
    mds = sorted(src_dir.glob("*.md"))  # ファイル名 = YYYY-MM-DD 昇順 → 末尾が最新
    if target_date:
        # ファイル名先頭の YYYY-MM-DD で「target_date 以前」に絞る (辞書順 = 日付順)。
        mds = [p for p in mds if p.name[:10] <= target_date]
    if not mds:
        return None
    # 遅延 import (循環回避)
    from tools.render_deepdive import DeepDiveIncompleteError, build_deepdive_context
    try:
        dd = build_deepdive_context(mds[-1])
    except DeepDiveIncompleteError:
        raise  # 未完成 DeepDive (関係図等の欠落) は LP にも黙って載せず build を止める
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] LP DeepDive カード構築失敗 {mds[-1].name}: {exc}", file=sys.stderr)
        return None
    if not dd.get("date") or not dd.get("title"):
        return None
    tags = dd.get("tags") or []
    title = dd.get("title", "")
    theme = dd.get("theme", "")
    # hero lead は本文「## 背景」導入段落 (**太字** __下線__ [[マーカー]] が密) を採用し、
    # Summary の essay と同じ render_emph で 3 階層強調を描く。素テキストの theme は
    # tag 一致でしか光らず薄いため使わない。背景散文が空のときだけ theme に退避する。
    bg_prose = dd.get("bg_prose") or []
    lead_md = bg_prose[0] if bg_prose else theme
    return {
        "title": title,
        "title_html": _emphasize_entities(title, tags),
        "theme": theme,
        "lead_md": lead_md,
        "date": dd.get("date", ""),
        "date_dot": dd.get("date_dot", ""),
        "canonical": dd.get("canonical", ""),
        "read_min": dd.get("read_min", 0),
        "lens_id": dd.get("lens_id", ""),
        "lens_name_en": dd.get("lens_name_en", ""),
        "lens_name_jp": dd.get("lens_name_jp", ""),
        "lens_glyph": dd.get("lens_glyph", ""),
        "accent": dd.get("accent", INK),
        "tags": tags[:4],
        **deepdive_audio_for_pages(str(dd.get("date", ""))),
        # 「IN THIS REPORT」manifest (実ブロックから動的生成)
        "report_items": _deepdive_report_items(dd),
    }


def build_index(entries: list[dict[str, Any]], docs_root: Path,
                recent_days: int = TOP_RECENT_DAYS,
                *, target_date: str | None = None, is_yesterday: bool = False) -> Path:
    """Variant B Magazine Spread Home (docs/index.html) を生成。

    target_date を指定すると、その日付を「当日」とみなした LP を
    docs/{target_date}/index.html に生成する (sticky nav の「YESTERDAY」遷移先)。
    is_yesterday=True で hero 見出しを「YESTERDAY'S THEME」に切り替え、nav の現在地を
    YESTERDAY 側にし、body data-variant="yesterday" でページ背景をやや暗くする。

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
            "yesterday_date": "",
            "is_yesterday": False,
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
            "latest_deepdive": _latest_deepdive_card(),
        }
        out = Path(docs_root) / "index.html"
        return render_page(ctx, out, template_name="index-template.html")

    # entries は日付降順。target_date 指定時はその日を、無指定なら最新日を「当日」とする。
    today_date = target_date or entries[0]["date"]
    same_day = [e for e in entries if e["date"] == today_date]
    # sticky nav の「YESTERDAY」リンク用: today より前の最新 unique date (無ければ空)。
    # 当日 LP では YESTERDAY をこの日付へリンクし、昨日 LP では YESTERDAY を現在地表示にする。
    prior_dates = [e["date"] for e in entries if e.get("date") and e["date"] < today_date]
    yesterday_date = max(prior_dates) if prior_dates else ""

    # Editor's Top 3: score 降順、上位 3 件 (同一カテゴリ重複は許容、デザイン仕様通り)
    sorted_by_score = sorted(same_day, key=lambda e: e.get("top_score", 0), reverse=True)
    editor_top3 = sorted_by_score[:5]  # 右ヒーローは TOP5 (左の DeepDive スライダーと縦幅を揃える)
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
            "summary": strip_inline(e["summary_text"]) if e else "",
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
    hero_phrase_left, hero_phrase_right = _hero_phrases(editorial)

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
    # 切詰禁止: 「…」省略は読者に不全感を与えるため絶対 NG (2026-06-05 指摘)。
    # `.home-hero__lead` は max-width: 560px の横固定だが縦は flex で伸びる設計なので、
    # 長文でも枠がそのまま下に広がる。長すぎる lead は digest md オーサ側で短く書く方針。

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
        "yesterday_date": yesterday_date,
        "is_yesterday": is_yesterday,
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
        "audio_label": "昨日のニュース朗読" if is_yesterday else "今日のニュース朗読",
        **latest_audio_for_pages(today_date),
        # LP 上部ヒーローの SUMMARY ⇆ DEEP DIVE スライダー用。entry ストリームとは
        # 独立に DeepDive md を直接読んだデータ (不変条件の本質 = entry 非汚染を維持)。
        # 昨日 LP では target_date(=昨日) 以前の DeepDive を引き、当日のテーマが
        # 昨日 LP に出る不具合を防ぐ。当日 LP (is_yesterday=False) は従来どおり最新。
        "latest_deepdive": _latest_deepdive_card(today_date if is_yesterday else None),
    }
    # target_date 指定時は docs/{date}/index.html (昨日 LP)、無指定は docs/index.html (当日 LP)
    out = (Path(docs_root) / target_date / "index.html") if target_date else (Path(docs_root) / "index.html")
    return render_page(ctx, out, template_name="index-template.html")


def build_overview(date: str, entries: list[dict[str, Any]], docs_root: Path) -> Path:
    """Phase 4: 日付別 Daily Overview (Pattern C) docs/{date}/index.html を生成。

    entries は **同一 date の** entries だけを渡す前提。summary を含む全カテゴリの
    最新ダイジェストを集約して、7 lens の 1 ページサマリを作る (Mobility/Manufacturing 追加)。
    """
    same_day = [e for e in entries if e["date"] == date]
    if not same_day:
        raise ValueError(f"build_overview: entries に date={date} が無い")

    # 各カテゴリを配信スケジュールに沿って CATEGORIES 順に並べる。
    by_cat: dict[str, dict[str, Any]] = {}
    for e in same_day:
        cid = e["category_id"]
        if cid != "summary" and cid not in by_cat:
            by_cat[cid] = e
    cat_rows: list[dict[str, Any]] = []
    for cid, meta in CATEGORIES.items():
        if cid == "summary" or not is_category_scheduled_on(cid, date):
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
            "summary": strip_inline(e["summary_text"]) if e else "",
            "canonical": e["canonical"] if e else f"{BASE_URL}/{cid}/",
            "articles_count": e.get("articles_count", 0) if e else 0,
            "scores": scores10,
            "avg_score": avg_score,
            "max_score": max_score,
            "top3": e.get("top3", []) if e else [],
        })

    # Theme banner: 同日 summary digest から 2 フレーズ抽出
    editorial = next((e for e in same_day if e["category_id"] == "summary"), None)
    # フレーズは frontmatter `hero_left`/`hero_right` (LLM オーサ) 優先、無ければ theme 機械分割。
    hero_phrase_left, hero_phrase_right = _hero_phrases(editorial)

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
            "categories": len(cat_rows),
            "essay": 7,
            "full_read_min": full_read_min,
        },
    }
    out = Path(docs_root) / date / "index.html"
    return render_page(ctx, out, template_name="overview-template.html")


def build_all_overviews(entries: list[dict[str, Any]], docs_root: Path) -> list[Path]:
    """全 unique date について overview ページを生成。

    ※ 最新日の 1 つ前 (yesterday) は main() で build_index(target_date=...) により
    LP 体裁の「昨日ページ」で上書きされる (docs/{昨日}/index.html)。ここでは全日付の
    overview を一旦生成し、上書きは呼び出し側の順序に委ねる。
    """
    unique_dates = sorted({e["date"] for e in entries if e.get("date")}, reverse=True)
    written: list[Path] = []
    for d in unique_dates:
        try:
            written.append(build_overview(d, entries, docs_root))
        except Exception as exc:
            print(f"[warn] overview build failed for {d}: {exc}", file=sys.stderr)
    return written


# ---------- Phase 5: Editorial Summary (Pattern D) ----------

# 9 セクションの固定タグ + accent (Claude Design site/desktop-extra.jsx の DesktopSummaryOnly より)。
# 2026-06-03: モビリティ (Mobility 追加時の積み残し是正) と製造 (新カテゴリ) を加え 7→9 に拡張。
_SUMMARY_SECTION_TAGS = ["総論", "為替", "AI", "IT", "モビリティ", "製造", "経済", "ゲーム", "明日へ"]
_SUMMARY_SECTION_COLORS = ["#1A1A1A", "#B8860B", "#2D5BB8", "#2E6B52", "#3A7B8C", "#5A6B7B", "#8E2A19", "#5E3D8C", "#C9A155"]
# §02-08 を担当する category id (順序固定)。先頭 (総論) と末尾 (明日へ) は None。
_SUMMARY_CAT_ORDER = [None, "fx", "ai", "it", "mobility", "manufacturing", "economy", "game", None]


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
                if content.lower().startswith("[!quote]"):
                    break
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
    if not lead and len(subtitle) >= 80:
        # 誤って長い本文を subtitle 斜体に入れた場合でも、LP の TODAY'S THEME が
        # 短文 fallback に退化しないようにする。正規形は subtitle + blockquote lead。
        lead = subtitle
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


def _section_label(heading: str) -> str:
    """考察 §NN 見出し ("為替 — 副題") の先頭ラベルを em/en/hyphen 区切りで切り出す。

    `_build_essay_sections` (summary ページ) と `_category_essay` (カテゴリページ) が
    同じ正規化で参照する単一ソース。区切り記号や前後空白の扱いを 2 箇所に書かないため。
    """
    if not heading:
        return ""
    return re.split(r"\s*[—–\-]\s*", heading, maxsplit=1)[0].strip()


def _section_label_to_cid(heading: str) -> str | None:
    """考察 §NN 見出しの先頭ラベル (為替/AI/...) を category id に対応付ける。

    総論/明日へ等カテゴリに対応しない節や未知ラベルは None。TAG_TO_CID を単一ソースに引く。
    """
    return TAG_TO_CID.get(_section_label(heading))


def _category_essay(reflection: dict[str, Any], cat_id: str) -> tuple[str, str]:
    """summary digest の reflection.sections から cat_id "固有" の考察 (heading, body) を引く。

    各 §NN 見出しの先頭ラベルを _section_label_to_cid で cid に解決し、cat_id と一致する
    最初の節の (heading, body) を返す。該当が無ければ ("", "") (呼び出し側でカテゴリ自身の
    summary_text に fallback)。body には装飾記法 (** __ [[]]) が残るので render_emph で描画する。
    """
    sections = reflection.get("sections") or {}
    for num in sorted(sections.keys()):
        es = sections[num]
        if _section_label_to_cid(es.get("heading", "")) == cat_id:
            return (es.get("heading", "").strip(), es.get("body", "").strip())
    return ("", "")


def _build_essay_sections(sections: dict[int, dict[str, str]],
                          by_cat: dict[str, dict[str, Any]]) -> list[dict[str, Any]] | None:
    """digest の `### §NN` から抽出した考察を summary-template 用 sections に変換。

    sections が空 (考察ブロック非対応の digest) なら None を返し、呼び出し側の
    fallback (9-grid) に委ねる。各 §NN の見出し先頭ラベルからカテゴリを判定し、
    総論/明日へは自己ページ表示なので「詳細を読む」リンク (canonical) を出さない。
    """
    if not sections:
        return None
    out: list[dict[str, Any]] = []
    for num in sorted(sections.keys()):
        es = sections[num]
        heading = es.get("heading", "")
        body = es.get("body", "")
        label = _section_label(heading)
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
    """考察 §NN が取れない digest 用の 9-grid fallback。

    §01 総論 / §02-08 各カテゴリ Top1 + bullets / §09 明日へ を必ず描画する。
    §01・§09 は summary_text にフォールバックし、自己ページ表示なので canonical を出さない。
    """
    summary_text = (editorial.get("summary_text") if editorial else "") or ""
    sections: list[dict[str, Any]] = []
    last = len(_SUMMARY_SECTION_TAGS) - 1  # 末尾 = 明日へ。カテゴリ増減に追従させハードコード番号を残さない
    for i in range(len(_SUMMARY_SECTION_TAGS)):
        tag = _SUMMARY_SECTION_TAGS[i]
        color = _SUMMARY_SECTION_COLORS[i]
        cid = _SUMMARY_CAT_ORDER[i]
        bullets: list[str] = []
        if i == 0:
            heading = "本日の総論"
            body = summary_text or "本日の総論データ準備中。"
            canonical = ""
        elif i == last:
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
    # 切詰禁止: 「…」省略は読者に不全感を与えるため絶対 NG (2026-06-05 指摘)。
    # `.summary-hero__lead` の CSS は max-height / line-clamp なしで縦に flex する設計なので、
    # 長文でも枠がそのまま下に広がる。長すぎる lead は digest md オーサ側で短く書く方針。

    # フレーズは frontmatter `hero_left`/`hero_right` 優先、無ければ theme 機械分割 (summary subtitle fallback 用)。
    left, right = _hero_phrases(editorial)
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
        "audio_label": "今日のニュース朗読",
        **latest_audio_for_pages(date),
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


def _theme_tokens(entry: dict[str, Any]) -> tuple[set[str], set[str]]:
    """カテゴリトップ表示用の同テーマ判定トークン (proper, numerics)。

    entry["top_title"] (= digest 内 TOP score の個別記事タイトル) を一次に、無ければ
    entry["title"] (= digest ヘッダ「News Grasp #YYYYMMDD — カテゴリ名」) に fallback。
    digest ヘッダだけだと全 entry に「News」「Grasp」「Artificial」「Intelligence」が
    共通で乗り全 entry が同テーマ判定されてしまうため、必ず top_title を見る。

    dedup.py の `significant_tokens` を再利用 (英字固有名詞 + 2 桁以上の数値)。
    日本語タイトルでも英字固有名詞 (Microsoft/Anthropic/OpenAI 等) と数値は拾える。
    """
    return significant_tokens(entry.get("top_title") or entry.get("title", ""))


def _is_same_theme_for_display(
    cw: set[str], cn: set[str], pw: set[str], pn: set[str],
) -> bool:
    r"""カテゴリトップ表示用「同テーマ」判定 (`_dedupe_by_theme` の核)。

    判定:
      ① dedup.py の `same_event_by_tokens` で同一イベント判定 (固有 3 共通 /
         固有 2 + 数値 1 / 固有 1 + 数値 2) → True
      ② 製品名+バージョン番号 (例: Claude Opus 4.8) は `significant_tokens` の
         `_NUM_RE = \d[\d,]*` がドット非対応で `4.8` を `4`/`8` に分解し 1 桁
         除外 → 数値共通 0 で ① を抜ける。これを補うため、共通固有名詞が
         2 つ以上ある場合は表示段だけ同テーマ扱いする緩和判定を追加する。

    なぜ表示段限定か: dedup.py 本体に同じ緩和を入れると「同社別ニュース 2 日連続
    の正当な続報」も別 URL で落としてしまう。表示段なら片方を最終出力 (grid_9 /
    past_7) から外すだけで data/digest 本体は残り、別エンドポイント (記事個別ページ /
    archive) は依然到達可能 ([[feedback_check_design_principles]] 2 段「境界 1
    箇所集約」を表示層で守る)。誤検出のリスク (同社別四半期決算 2 連続表示など)
    は AI トップでの実害が小さく、同テーマ続報の連続表示を防ぐ価値を優先する。
    """
    if same_event_by_tokens(cw, cn, pw, pn):
        return True
    return len(cw & pw) >= 2


def _dedupe_by_theme(entries: list[dict[str, Any]], *,
                     max_window: int = 10) -> list[dict[str, Any]]:
    """直前採用 entry と同テーマの entry を skip し、次の entry を順次補充する。

    判定は `_is_same_theme_for_display` (= dedup.py の same_event 基準 + 共通固有 2 緩和)。
    dedup.py 本体は「24h 超は続報扱い」で意図通り通すため、別 URL の同社・同論点記事は
    ストリームに残る。これがカテゴリトップ (grid_9 / past_7) で並ぶと「同テーマ続報が
    日替わりで再掲」されてしまうので、最終出力段で連続出現だけを構造的に弾く
    (digest md / data/articles.jsonl は不変、歴史保存)。

    フェイルセーフ: 固有名詞が取れない entry (純カタカナ stopword のみ等) は
    同テーマ判定が False を返すので落とさない (誤検出回避)。
    """
    out: list[dict[str, Any]] = []
    for e in entries:
        if not out:
            out.append(e)
        else:
            cw, cn = _theme_tokens(e)
            pw, pn = _theme_tokens(out[-1])
            if _is_same_theme_for_display(cw, cn, pw, pn):
                continue  # 直前と同テーマ → skip し次の entry を採用
            out.append(e)
        if len(out) >= max_window:
            break
    return out


def build_category_pages(entries: list[dict[str, Any]], docs_root: Path,
                         *, digests: Iterable[Path] | None = None) -> list[Path]:
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
    # 「本日のテーマ考察」navy band は "そのカテゴリ固有" の考察を出す (LP の日全体総論とは別)。
    # カテゴリ entry 自体は reflection={} なので、同日の summary digest entry から引く。
    summary_by_date = {
        e["date"]: e for e in entries if e["category_id"] == "summary"
    }
    today_date = max((e["date"] for e in entries if e.get("date")), default="")
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
        same_day_grid = _articles_as_grid_entries(
            cat_id, featured["date"],
            skip_url=featured.get("top_source_url"), digests=digests
        )
        if len(cat_entries_sorted) >= 2:
            # featured 除く過去号を theme dedup pass で連続同テーマを抑止してから切り出す。
            # 2026-06-06 AI トップ事故: dedup.py が「24h 超は続報扱い」で同社別 URL の
            # 続報を通すため、past_7/grid_9 に「Microsoft AI モデル新発表」が 2 日連続、
            # 「Anthropic IPO 申請」が 2 日連続のように同テーマが並んだ。表示段で
            # 連続出現を構造的に弾く ([[feedback_check_design_principles]] 1 段+2 段)。
            # featured は 1 件なので対象外。
            deduped_tail = _dedupe_by_theme(cat_entries_sorted[1:], max_window=10)
            # More stories は「本日号の残り記事」。過去号は past_7 だけに出す。
            # 2026-06-08 AI トップで過去号カードが本日の記事に見える事故を固定する。
            grid_9 = same_day_grid[:9]
            past_7 = deduped_tail[:7]
            # 層 2 出力品質ゲート (plan v2): _dedupe_by_theme が漏れた場合の
            # 最終 assert。is_same_theme は _is_same_theme_for_display を inject。
            from tools.output_quality import assert_quality, check_category_top_dedup
            _same = lambda a, b: _is_same_theme_for_display(
                *_theme_tokens(a), *_theme_tokens(b))
            assert_quality([
                (f"{cat_id}/past_7", check_category_top_dedup(
                    past_7, kind=f"{cat_id}/past_7", is_same_theme=_same)),
            ])
        else:
            # data 不足 (= backfill 未着手の新設カテゴリ) の fallback:
            # data/articles.jsonl の同日 5 記事を grid に展開して、他カテゴリと粒度を揃える
            grid_9 = same_day_grid[:9]
            past_7 = []
        # 「本日のテーマ考察」は summary digest の §NN のうち、見出しラベルが当該カテゴリに
        # 一致する節の body (= カテゴリ固有の考察)。装飾記法は保持しテンプレ側で render_emph 描画。
        # 該当節が無ければ (旧 digest で §NN 非対応／その日そのカテゴリ非配信 等) カテゴリ自身の
        # summary_text に fallback (テンプレ側で判定)。editorial_heading は見出しサブタイトル用。
        sum_e = summary_by_date.get(featured["date"])
        editorial_heading, editorial_essay = (
            _category_essay(sum_e.get("reflection") or {}, cat_id) if sum_e else ("", "")
        )
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
            "editorial_essay": editorial_essay,
            "editorial_heading": editorial_heading,
            "grid_9": grid_9,
            "past_7": past_7,
            "nav_categories": nav_categories,
            "pause_notice": _category_pause_notice(cat_id, today_date),
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


def build_archive(entries: list[dict[str, Any]], docs_root: Path,
                  deepdive_items: list[dict[str, Any]] | None = None,
                  lens_chips: list[dict[str, Any]] | None = None) -> Path:
    """日付横断アーカイブ docs/archive/index.html を生成。

    Claude Design "News Grasp Archive" (Editorial Timeline / Variant B) に準拠。
    日付ごとに 1 号 (issue) としてまとめ、各カテゴリの最上位記事を stories に整形する。
    deepdive_items / lens_chips は DEEP DIVE スライド (旧テーマ書架の一本化先) 用で、
    render_deepdive.collect_archive_items() の戻り値をそのまま渡す。
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
            "is_new": cid == "manufacturing",
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
                "title_ja": e.get("top_title_ja", ""),
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
        # DEEP DIVE スライド (旧テーマ書架 /deepdive/ の一本化先)
        "deepdive_items": deepdive_items or [],
        "lens_chips": lens_chips or [],
        "deepdive_count": len(deepdive_items or []),
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

    # Lv1 illegal state guard: 英文 ### 記事に > [!ja] 和訳 callout が欠落していれば
    # SSG ビルドを中止する。docs/ への反映を物理的に止めることで「和訳ない記事が公開
    # される」状態を構造的に不可能化する (feedback_check_design_principles Lv1)。
    missing_ja = validate_ja_callout_coverage()
    if missing_ja:
        print(
            f"[ERROR] 英文 ### 記事に > [!ja] 和訳 callout が欠落: {len(missing_ja)} 件",
            file=sys.stderr,
        )
        for m in missing_ja[:30]:
            print(m, file=sys.stderr)
        if len(missing_ja) > 30:
            print(f"  ... ({len(missing_ja) - 30} 件略)", file=sys.stderr)
        print(
            "\nビルドを中止します。digest md に `> [!ja] {和訳}` を付与してから再実行してください。",
            file=sys.stderr,
        )
        return 1

    written = build_all(full=args.full, docs_root=docs_root)
    print(f"wrote {len(written)} article page(s)")

    # DeepDive (週次 TODAY'S THEME) は日次パイプラインとは疎結合の独立レンダーパス。
    # 不変条件 (2026-05-31 事故) の本質は「日次 digest の **entry ストリーム**
    # (build_all / _collect_entries) を DeepDive で汚染しない」こと → ここは従来どおり
    # entries に載せず digest/DeepDive/*.md だけを docs/deepdive/{date}/ に出力する。
    # ※ LP 上部ヒーローの SUMMARY ⇆ DEEP DIVE スライダーだけは別経路: build_index が
    #   _latest_deepdive_card() で DeepDive md を直接読み独立データとして明示注入する
    #   (entries 非汚染なので不変条件と両立。deepdive_integration_spec.md オプション B)。
    # 遅延 import (循環回避)
    from tools.render_deepdive import (build_deepdive_archive, build_deepdive_pages,
                                       collect_archive_items)
    dd_pages = build_deepdive_pages(docs_root=docs_root, full=args.full)
    build_deepdive_archive(docs_root=docs_root)  # テーマ書架 (/deepdive/) も同時に生成
    if dd_pages:
        print(f"wrote {len(dd_pages)} DeepDive page(s)")
        for p in dd_pages[:5]:
            try:
                print(f"  - {p.relative_to(_PKG_ROOT)}")
            except ValueError:
                print(f"  - {p}")
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
        cat_pages = build_category_pages(entries, docs_root, digests=digests_all)
        dd = collect_archive_items()
        arc = build_archive(entries, docs_root,
                            deepdive_items=dd["items"], lens_chips=dd["chips"])
        overviews = build_all_overviews(entries, docs_root)
        # 「YESTERDAY」ナビの遷移先: 最新日の 1 つ前を当日 LP と同じ体裁で docs/{昨日}/ に
        # 上書き生成する (build_all_overviews が出した同日 overview を LP 昨日版で置換)。
        _yesterday_dates = sorted({e["date"] for e in entries if e.get("date")}, reverse=True)
        if len(_yesterday_dates) > 1:
            build_index(entries, docs_root, target_date=_yesterday_dates[1], is_yesterday=True)
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
