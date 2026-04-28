"""News Grasp メールテンプレート単体テスト用 HTML レンダラー。

`prompts/email-template.html` のプレースホルダ `{{...}}` を、
mock_data.py のサンプルデータで埋めて完成版 HTML を出力する。

使い方:
    # A) ローカルにプレビューだけ保存（送信なし）
    python tests/render_email.py
    # → tests/output/preview.html を吐き出す。ブラウザで開いて確認

    # C) Webhook へ実送信（テンプレ + 経路の E2E 確認）
    python tests/render_email.py --send

    # 既存の digest からレンダリング（B モード相当）は別スクリプト
    # tests/render_from_digest.py を使う（Claude Code 経由）

依存: 標準ライブラリのみ
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import os
import re
import sys
import urllib.request
from functools import lru_cache

# 自身が置かれている tests/ から repo ルートを解決
HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)

import mock_data  # noqa: E402

WEBHOOK_URL = (
    "https://script.google.com/macros/s/"
    "AKfycbxCNRk_M3s1xPyCm_9BObpVWAzilFGwXQxFi-XMBnBHu7-Ly3nhydzqL_cPJUOGYgGu/exec"
)
WEBHOOK_CLIENT = "news-grasp-routine"
RECIPIENTS = ["hideki.kusunoki@gmail.com"]  # テストはまず自分だけに

ASSETS_DIR = os.path.join(REPO_ROOT, "assets")


# ---------------------------------------------------------------------------
# 強調記法の HTML 化
# ---------------------------------------------------------------------------

def render_inline_emphasis(text: str, accent: str) -> str:
    """`[[太字]]` と `__下線__` を HTML に変換する。

    - `[[X]]` → <strong> + accent 色背景
    - `__X__` → <span> + 下線 + 太字
    """
    # まず HTML エスケープ（マーカー削除前）
    # ただし、マーカー記号は最初に置換用プレースホルダに退避
    BOLD = "\x00BOLD\x00"
    UND = "\x00UND\x00"

    bolds: list[str] = []
    unds: list[str] = []

    def stash_bold(m: re.Match) -> str:
        bolds.append(m.group(1))
        return f"{BOLD}{len(bolds)-1}{BOLD}"

    def stash_und(m: re.Match) -> str:
        unds.append(m.group(1))
        return f"{UND}{len(unds)-1}{UND}"

    s = re.sub(r"\[\[(.+?)\]\]", stash_bold, text)
    s = re.sub(r"__(.+?)__", stash_und, s)
    s = html.escape(s)

    def render_bold(m: re.Match) -> str:
        idx = int(m.group(1))
        inner = html.escape(bolds[idx])
        return (
            f'<strong style="font-weight:800;color:{accent};'
            f'background:{accent}18;padding:0 3px;border-radius:2px;">{inner}</strong>'
        )

    def render_und(m: re.Match) -> str:
        idx = int(m.group(1))
        inner = html.escape(unds[idx])
        return (
            f'<span style="border-bottom:2px solid {accent};'
            f'padding-bottom:1px;font-weight:700;">{inner}</span>'
        )

    s = re.sub(rf"{BOLD}(\d+){BOLD}", render_bold, s)
    s = re.sub(rf"{UND}(\d+){UND}", render_und, s)
    return s


# ---------------------------------------------------------------------------
# 個別ブロック HTML
# ---------------------------------------------------------------------------

@lru_cache(maxsize=16)
def ng_thumb_data_uri(name: str) -> str:
    """`assets/{name}.jpg` を base64 data URI に変換してキャッシュ。

    private repo でも動くよう、メール HTML には外部 URL ではなく
    data URI として画像を埋め込む。
    name は `ng-thumb-fx`（FEATURED 横長 1136x400）か
    `ng-thumb-common-fx`（共通サイド 280x180）の形式。
    """
    path = os.path.join(ASSETS_DIR, f"{name}.jpg")
    if not os.path.exists(path):
        # 万一ファイルが無くても致命的にならないよう 1px 透明 GIF を返す
        return (
            "data:image/gif;base64,"
            "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
        )
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


# cid: モード用のレジストリ（--send 時のみ使用、GAS 経路の遺物）
# render 完了後に post_to_webhook へ inlineImages として引き渡す。
_CID_MODE: bool = False
_CID_REGISTRY: dict[str, str] = {}

# CDN モード: NG プレースホルダ画像を公開 repo の raw URL で参照する
# Gmail のインボックスプレビューに「noname 添付」として表示されるのを回避するため。
# https://github.com/HIDEPON-UMG/news-grasp-assets (public) に同じ JPG が置いてある
NG_THUMB_CDN_BASE = "https://raw.githubusercontent.com/HIDEPON-UMG/news-grasp-assets/main"
_CDN_MODE: bool = False


def set_cdn_mode(enabled: bool) -> None:
    """NG プレースホルダを公開 CDN URL で参照するモード（SMTP 送信時に有効化推奨）。"""
    global _CDN_MODE
    _CDN_MODE = enabled


def set_cid_mode(enabled: bool) -> None:
    """cid: 参照モードの ON/OFF。OFF ならローカルプレビュー用に base64 inline する。"""
    global _CID_MODE, _CID_REGISTRY
    _CID_MODE = enabled
    _CID_REGISTRY = {}


def get_inline_images() -> dict[str, str]:
    """直近の render で集まった cid: → data URI マップを返す。"""
    return dict(_CID_REGISTRY)


def thumb_url(item: dict, cat_id: str, *, is_top: bool = False) -> str:
    """OGP があればそれ、無ければ位置に応じた NG プレースホルダ。

    is_top=True (FEATURED 568x200 枠) → カテゴリ別キービジュアル
    is_top=False (サイド 140x90 枠) → カテゴリ別共通サムネ

    優先順位:
        1. item.thumb が non-null → OGP URL を直返し
        2. _CDN_MODE → 公開 repo の raw URL を返す（Gmail プレビューに添付として出ない）
        3. _CID_MODE → cid:<name> + registry 登録（GAS Webhook 用、現在は遺物）
        4. それ以外 → base64 data URI（プレビュー用、ブラウザで直接開ける）
    """
    if item.get("thumb"):
        return item["thumb"]
    name = f"ng-thumb-{cat_id}" if is_top else f"ng-thumb-common-{cat_id}"
    if _CDN_MODE:
        return f"{NG_THUMB_CDN_BASE}/{name}.jpg"
    if _CID_MODE:
        if name not in _CID_REGISTRY:
            _CID_REGISTRY[name] = ng_thumb_data_uri(name)
        return f"cid:{name}"
    return ng_thumb_data_uri(name)


def build_toc_rows(categories: list[dict]) -> str:
    rows = []
    for i, c in enumerate(categories):
        rows.append(f"""
        <table width="100%" style="margin-bottom:6px;"><tr>
          <td width="32" style="font-family:'JetBrains Mono',Menlo,monospace;font-size:13px;color:{c['accent']};font-weight:700;">{i+1:02d}.</td>
          <td style="font-size:16px;font-weight:700;">
            <span style="color:{c['accent']};margin-right:6px;font-family:'JetBrains Mono',Menlo,monospace;">{c['glyph']}</span>
            {html.escape(c['name'])}
            <span style="color:#8B8B85;font-weight:400;font-size:12px;font-family:'JetBrains Mono',Menlo,monospace;margin-left:8px;">{html.escape(c['nameEn'])}</span>
          </td>
          <td align="right" style="font-family:'JetBrains Mono',Menlo,monospace;font-size:12px;color:#5C5A52;">{len(c['items'])} items</td>
        </tr></table>""".strip())
    return "\n".join(rows)


def build_article_card(it: dict, idx: int, cat: dict) -> str:
    """
    各記事のカード:
    - TOP (idx=0): FEATURED 大画像 (568x200)、画像クリックで記事 URL へ
    - 非TOP (idx>=1): サイドサムネ (140x90) を左、本文を右、画像クリックで記事 URL へ
    - 画像はいずれも OGP 優先 (item.thumb 有り) → 無ければ CDN/NG プレースホルダ
    """
    accent = cat["accent"]
    is_top = (idx == 0)
    img = thumb_url(it, cat["id"], is_top=is_top)
    url = html.escape(it.get("url", "#"))
    bullets_html = ""
    for b in it["bullets"]:
        bullets_html += (
            f'<div class="bul ng-card-body" style="color:{accent}">'
            f'<span style="color:#1A1A1A">{render_inline_emphasis(b, accent)}</span>'
            f'</div>'
        )

    bg = "#FAF7F0" if idx % 2 == 0 else "#F5F1E7"
    top_label = ""
    feature_img_html = ""
    side_img_html = ""

    if is_top:
        top_label = (
            f'<span style="background:{accent};color:#fff;font-family:\'JetBrains Mono\',Menlo,monospace;'
            'font-size:11px;padding:2px 6px;margin-right:8px;vertical-align:middle;letter-spacing:1px;">TOP</span>'
        )
        feature_img_html = f"""
        <a href="{url}" style="text-decoration:none;display:block;margin-bottom:14px;position:relative;border:1px solid #E2DED4;" class="ng-feature-img">
          <img src="{img}" alt="" width="568" style="width:100%;height:220px;object-fit:cover;display:block;">
          <div style="position:absolute;bottom:0;left:0;background:{accent};color:#fff;font-family:'JetBrains Mono',Menlo,monospace;font-size:11px;padding:4px 10px;letter-spacing:1.5px;">
            FEATURED · {html.escape(cat['nameEn'].upper())}
          </div>
        </a>"""
    else:
        side_img_html = f"""
          <td class="ng-side-thumb" width="140" valign="top" style="padding-right:16px;">
            <a href="{url}" class="db tdn"><img src="{img}" alt="" width="140" class="thb db ofc brd"></a>
          </td>"""

    return f"""
    <tr><td class="ng-card-pad" style="padding:24px 36px;background:{bg};border-bottom:1px solid #EDEAE3;">
      <div class="ng-card-meta" style="font-family:'JetBrains Mono',Menlo,monospace;font-size:11px;color:#5C5A52;letter-spacing:0.5px;margin-bottom:6px;">
        <span style="background:{accent};color:#fff;font-size:12px;font-weight:700;padding:2px 6px;">{idx+1:02d}</span>
        <span style="padding-left:8px;">{html.escape(it.get('time',''))} · {html.escape(it.get('source',''))} · SCORE {it.get('score','')}</span>
      </div>

      <h3 class="ng-card-title" style="font-size:{22 if is_top else 18}px;font-weight:800;line-height:1.45;margin:8px 0 12px;letter-spacing:-0.3px;">
        {top_label}<a href="{url}" style="color:#1A1A1A;text-decoration:none;">{html.escape(it.get('title',''))}</a>
      </h3>
      {feature_img_html}

      <table width="100%"><tr>
        {side_img_html}
        <td class="ng-side-text" valign="top">
          {bullets_html}
        </td>
      </tr></table>
    </td></tr>"""


def build_categories_html(categories: list[dict]) -> str:
    parts = []
    for ci, cat in enumerate(categories):
        # カテゴリヘッダー
        parts.append(f"""
        <tr><td class="ng-cat-pad" style="background:{cat['accent']};padding:20px 36px;">
          <table width="100%"><tr>
            <td style="vertical-align:middle;">
              <div style="font-family:'JetBrains Mono',Menlo,monospace;font-size:11px;color:rgba(255,255,255,0.7);letter-spacing:2px;margin-bottom:4px;">
                CATEGORY {ci+1:02d} / {len(categories):02d} · {html.escape(cat['nameEn'].upper())}
              </div>
              <div class="ng-cat-name" style="font-size:30px;font-weight:800;color:#fff;letter-spacing:-0.5px;line-height:1.1;">
                <span style="font-family:'JetBrains Mono',Menlo,monospace;margin-right:10px;">{cat['glyph']}</span>{html.escape(cat['name'])}
              </div>
            </td>
            <td align="right" style="vertical-align:middle;color:rgba(255,255,255,0.85);font-family:'JetBrains Mono',Menlo,monospace;font-size:12px;">
              {len(cat['items'])} stories
            </td>
          </tr></table>
          <div class="ng-cat-summary" style="font-size:14px;color:rgba(255,255,255,0.95);font-style:italic;line-height:1.6;margin-top:10px;padding-top:10px;border-top:1px solid rgba(255,255,255,0.25);">
            {html.escape(cat['summary'])}
          </div>
        </td></tr>""")

        for idx, it in enumerate(cat["items"]):
            parts.append(build_article_card(it, idx, cat))

    return "\n".join(parts)


def build_reflection_sections(sections: list[dict]) -> str:
    parts = []
    for si, sec in enumerate(sections):
        accent = sec["accent"]
        is_last = si == len(sections) - 1
        border = "none" if is_last else "1px dashed #E2DED4"
        parts.append(f"""
        <tr><td class="ng-section-pad" style="background:#FAF7F0;padding:0 36px;">
          <table width="100%" style="border-bottom:{border};"><tr>
            <td width="120" valign="top" class="ng-section-num-cell" style="padding:28px 16px 28px 0;white-space:nowrap;">
              <div class="ng-section-num" style="font-family:'JetBrains Mono',Menlo,monospace;font-size:42px;font-weight:900;color:{accent};line-height:0.9;letter-spacing:-2px;">§{si+1:02d}</div>
              <div style="font-family:'JetBrains Mono',Menlo,monospace;font-size:11px;color:#fff;background:{accent};padding:3px 8px;display:inline-block;letter-spacing:1px;margin-top:10px;white-space:nowrap;">{html.escape(sec['tag'])}</div>
            </td>
            <td valign="top" class="ng-section-text-cell" style="padding:28px 0 28px 20px;border-left:1px solid #E2DED4;">
              <h3 class="ng-section-heading" style="font-size:20px;font-weight:800;margin:0 0 14px;color:#1A1A1A;letter-spacing:-0.3px;line-height:1.4;">
                {html.escape(sec['heading'])}
              </h3>
              <div class="ng-section-body" style="font-size:15px;line-height:2.0;color:#1A1A1A;">
                {render_inline_emphasis(sec['body'], accent)}
              </div>
            </td>
          </tr></table>
        </td></tr>""")
    return "\n".join(parts)


def build_takeaways(takeaways: list[dict]) -> str:
    parts = []
    for i, t in enumerate(takeaways):
        parts.append(f"""
        <tr><td style="padding-bottom:12px;">
          <table width="100%" style="background:#fff;border:1px solid #E2DED4;"><tr>
            <td width="56" valign="middle" style="background:{t['color']};color:#fff;text-align:center;font-family:'JetBrains Mono',Menlo,monospace;font-size:20px;font-weight:900;padding:14px 0;">
              {i+1:02d}
            </td>
            <td style="padding:12px 16px;">
              <div style="font-family:'JetBrains Mono',Menlo,monospace;font-size:10px;color:{t['color']};font-weight:700;letter-spacing:1.5px;margin-bottom:4px;">
                {html.escape(t['tag'].upper())}
              </div>
              <div style="font-size:14.5px;line-height:1.75;font-weight:600;">
                {render_inline_emphasis(t['text'], t['color'])}
              </div>
            </td>
          </tr></table>
        </td></tr>""")
    return "\n".join(parts)


def build_related_issues(related: list[dict]) -> str:
    parts = []
    for i, r in enumerate(related):
        is_last = i == len(related) - 1
        border = "none" if is_last else "1px solid #E2DED4"
        parts.append(f"""
        <tr><td style="padding:10px 0;border-bottom:{border};">
          <table width="100%"><tr>
            <td width="100" style="font-family:'JetBrains Mono',Menlo,monospace;font-size:12px;color:#5C5A52;">{html.escape(r['date'])}</td>
            <td style="font-size:14px;font-weight:600;"><a href="{html.escape(r.get('url','#'))}" style="color:#1A1A1A;text-decoration:none;">{html.escape(r['title'])}</a></td>
            <td width="20" align="right" style="color:#5C5A52;">→</td>
          </tr></table>
        </td></tr>""")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# テンプレート展開
# ---------------------------------------------------------------------------

def render_email_html() -> str:
    template_path = os.path.join(REPO_ROOT, "prompts", "email-template.html")
    with open(template_path, encoding="utf-8") as f:
        tpl = f.read()

    cats = mock_data.CATEGORIES
    refl = mock_data.REFLECTION
    total_stories = sum(len(c["items"]) for c in cats)

    placeholders = {
        "{{ISSUE_DATE}}": mock_data.ISSUE_DATE,
        "{{ISSUE_WEEKDAY}}": mock_data.ISSUE_WEEKDAY,
        "{{ISSUE_NO}}": mock_data.ISSUE_NO,
        "{{TOTAL_CATEGORIES}}": str(len(cats)),
        "{{TOTAL_STORIES}}": str(total_stories),
        "{{TOTAL_SECTIONS}}": str(len(refl["sections"])),
        "{{TOC_ROWS_HTML}}": build_toc_rows(cats),
        "{{CATEGORIES_HTML}}": build_categories_html(cats),
        "{{REFLECTION_TITLE}}": html.escape(refl["title"]),
        "{{REFLECTION_SUBTITLE}}": html.escape(refl["subtitle"]),
        "{{REFLECTION_LEAD_HTML}}": render_inline_emphasis(refl["lead"], "#C9B98A"),
        "{{REFLECTION_PULL_QUOTE_HTML}}": render_inline_emphasis(refl["pull_quote"], "#8E2A19"),
        "{{REFLECTION_SECTIONS_HTML}}": build_reflection_sections(refl["sections"]),
        "{{TAKEAWAYS_HTML}}": build_takeaways(refl["takeaways"]),
        "{{RELATED_ISSUES_HTML}}": build_related_issues(refl["related"]),
    }

    out = tpl
    for k, v in placeholders.items():
        out = out.replace(k, v)
    return out


def minify_html(html_text: str) -> str:
    """HTML 圧縮: 頻出 inline スタイルを <style> ブロック内 atomic クラスに置換。

    背景: Gmail は htmlBody が ~102 KB を超えると「メッセージの一部のみ表示」で
    本文を切る。SMTP 経路でも受信側の制約として残るため、本関数で構造的に
    圧縮し、25 記事規模でも 102 KB 以下に収まるようにする。

    Outlook desktop は class セレクタを無視するが、本テンプレでは最低限のフォントは
    デフォルトで読めるレベルに収めている（記事内容は判読可能）。
    """
    s = html_text

    # 1. 頻出 inline スタイル断片を atomic クラスに置換
    #    declaration の trailing ; は任意（regex で吸収）
    INLINE_TO_CLASS = [
        ("font-family:'JetBrains Mono',Menlo,monospace", "m"),
        ("font-weight:900", "b9"),
        ("font-weight:800", "b8"),
        ("font-weight:700", "b7"),
        ("color:#5C5A52", "mut"),
        ("color:#1A1A1A", "dk"),
        ("color:#fff", "w"),
        ("color:#FFFFFF", "w"),
        ("letter-spacing:1.5px", "ls15"),
        ("letter-spacing:2px", "ls2"),
        ("letter-spacing:1px", "ls1"),
        ("letter-spacing:0.5px", "ls05"),
        ("font-style:italic", "it"),
        ("line-height:1.85", "lh185"),
        ("line-height:1.45", "lh145"),
        ("font-size:10px", "fz10"),
        ("font-size:11px", "fz11"),
        ("font-size:12px", "fz12"),
        ("font-size:13px", "fz13"),
        ("font-size:14px", "fz14"),
        ("text-decoration:none", "tdn"),
        ("object-fit:cover", "ofc"),
        ("display:block", "db"),
        ("display:none", "dn"),
        ("border-radius:2px", "br2"),
        ("padding:0 3px", "p3"),
        ("padding:2px 6px", "p26"),
        ("padding-bottom:1px", "pb1"),
        ("padding-left:8px", "pl8"),
        ("padding-right:16px", "pr16"),
        ("padding:24px 36px", "pcard"),
        ("margin-bottom:6px", "mb6"),
        ("margin-bottom:14px", "mb14"),
        ("margin-left:4px", "ml4"),
        ("margin-top:8px", "mt8"),
        ("margin:8px 0 12px", "t812"),
        ("letter-spacing:-0.3px", "lsm03"),
        ("background:#FAF7F0", "bgcard"),
        ("border-bottom:1px solid #EDEAE3", "bbcard"),
        ("border:1px solid #E2DED4", "brd"),
        ("vertical-align:middle", "vmid"),
        ("vertical-align:top", "vtop"),
        ("color:#B8860B", "acFx"),
        ("color:#2D5BB8", "acAi"),
        ("color:#2E6B52", "acIt"),
        ("color:#8E2A19", "acEc"),
        ("color:#5E3D8C", "acGm"),
        ("font-size:16px", "fz16"),
        ("font-size:9px", "fz9"),
    ]

    def transform_tag(match: re.Match) -> str:
        tag_text = match.group(0)
        if 'style="' not in tag_text:
            return tag_text
        added_classes: list[str] = []
        def style_replacer(style_match: re.Match) -> str:
            style_body = style_match.group(1)
            for needle, cls in INLINE_TO_CLASS:
                # ; 任意で挙動するため regex で吸収
                pattern = re.escape(needle) + r";?"
                if re.search(pattern, style_body):
                    style_body = re.sub(pattern, "", style_body)
                    if cls not in added_classes:
                        added_classes.append(cls)
            return f'style="{style_body}"'
        new_tag = re.sub(r'style="([^"]*)"', style_replacer, tag_text)
        if added_classes:
            class_str = " ".join(added_classes)
            if 'class="' in new_tag:
                new_tag = re.sub(
                    r'class="([^"]*)"',
                    lambda m: f'class="{m.group(1)} {class_str}"' if m.group(1) else f'class="{class_str}"',
                    new_tag, count=1,
                )
            else:
                new_tag = re.sub(
                    r"^(<\w+)",
                    rf'\1 class="{class_str}"',
                    new_tag, count=1,
                )
        # style 末尾の余分な ; や空 style="" を整理
        new_tag = re.sub(r';;+', ';', new_tag)
        new_tag = re.sub(r'style="\s*;\s*', 'style="', new_tag)
        new_tag = re.sub(r';\s*"', '"', new_tag)
        new_tag = re.sub(r'\s*style=""', "", new_tag)
        return new_tag

    s = re.sub(r"<[^>]+>", transform_tag, s)

    # 2. role="presentation" / cellpadding="0" cellspacing="0" border="0" を削除
    #    （アクセシビリティヒントだが email 用途では冗長、border-collapse で代用）
    s = re.sub(r'\s*role="presentation"', "", s)
    s = re.sub(r'\s*cellpadding="0"\s*cellspacing="0"\s*border="0"', "", s)
    s = re.sub(r'\s*cellpadding="0"', "", s)
    s = re.sub(r'\s*cellspacing="0"', "", s)
    # tbody は省略可能（多くのメーラーで自動補完される）
    s = re.sub(r"</?tbody>", "", s)

    # 3. 空白圧縮
    s = re.sub(r">\s+<", "><", s)
    s = re.sub(r"\n\s+", "\n", s)
    s = re.sub(r"  +", " ", s)
    return s


def post_to_webhook(
    html_body: str,
    recipients: list[str],
    subject: str,
    inline_images: dict[str, str] | None = None,
) -> dict:
    payload = {
        "client": WEBHOOK_CLIENT,
        "to": recipients,
        "subject": subject,
        "htmlBody": html_body,
    }
    if inline_images:
        payload["inlineImages"] = inline_images
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        WEBHOOK_URL, data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="News Grasp email template renderer")
    parser.add_argument("--send", action="store_true",
                        help="GAS Webhook 経由で送信（旧経路、200KB 上限あり）")
    parser.add_argument("--smtp", action="store_true",
                        help="本番経路: tools/send_email.py 経由で SMTP 直送（推奨）")
    parser.add_argument("--to", action="append", default=None,
                        help="送信先メールアドレス（複数指定可）。未指定時はテスト宛先のみ")
    parser.add_argument("--subject", default=f"[TEST] News Grasp #{mock_data.ISSUE_NO} mock",
                        help="メール件名")
    parser.add_argument("--out", default=os.path.join(HERE, "output", "preview.html"),
                        help="HTML 保存先")
    args = parser.parse_args()

    # プレビュー用は base64 inline（ブラウザで直接開ける）
    print(f"Rendering with mock data (issue #{mock_data.ISSUE_NO})...")
    set_cid_mode(False)
    html_body = render_email_html()

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html_body)
    print(f"Saved preview: {args.out}")
    print(f"  size: {len(html_body):,} bytes")
    print(f"  open in browser to inspect the layout")

    if args.send:
        # 送信用は cid: + inlineImages + 空白圧縮（GAS htmlBody ~200KB 上限を回避）
        set_cid_mode(True)
        send_html = minify_html(render_email_html())
        inline = get_inline_images()
        recipients = args.to or RECIPIENTS
        print(f"\nSending to: {recipients}")
        print(f"  send htmlBody size: {len(send_html):,} bytes (minified)")
        print(f"  inline images:      {len(inline)} ({sum(len(v) for v in inline.values()):,} chars total)")
        result = post_to_webhook(send_html, recipients, args.subject, inline_images=inline)
        print(f"Webhook response: {json.dumps(result, ensure_ascii=False, indent=2)}")
        if not result.get("ok"):
            print("FAIL: webhook reported failure")
            return 1
        print("OK: mail sent")

    if args.smtp:
        # 本番経路: 公開 CDN の raw URL で NG プレースホルダを参照
        #   → cid: inline 添付を使わず、Gmail インボックスプレビューに「noname」として
        #     画像が出るのを完全に回避できる
        # OGP URL 取得済の記事はそのまま外部 URL で参照（CDN もスキップ）
        # Gmail の htmlBody クリッピング (~102KB) 対策で minify は引き続き適用
        set_cid_mode(False)
        set_cdn_mode(True)
        send_html = minify_html(render_email_html())
        recipients = args.to or RECIPIENTS

        # 一時 HTML ファイルに書き出して send_email.py に渡す
        tmp_html = os.path.join(HERE, "output", "smtp_body.html")
        os.makedirs(os.path.dirname(tmp_html), exist_ok=True)
        with open(tmp_html, "w", encoding="utf-8") as f:
            f.write(send_html)

        import subprocess
        cmd = [
            sys.executable,
            os.path.join(os.path.dirname(HERE), "tools", "send_email.py"),
            "--html-file", tmp_html,
            "--subject", args.subject,
            "--to", ",".join(recipients),
        ]
        send_bytes = len(send_html.encode("utf-8"))
        print(f"\nSMTP send via tools/send_email.py")
        print(f"  htmlBody size: {send_bytes:,} bytes (minified, UTF-8 encoded)")
        if send_bytes > 102 * 1024:
            print(f"  WARN: Gmail clip threshold (~102 KB) exceeded by {send_bytes - 102*1024:,} bytes")
        rc = subprocess.call(cmd)
        if rc != 0:
            print("FAIL: SMTP send returned non-zero")
            return rc
        print("OK: SMTP mail sent")

    return 0


if __name__ == "__main__":
    sys.exit(main())
