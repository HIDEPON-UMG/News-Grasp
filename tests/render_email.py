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


# cid: モード用のレジストリ（--send 時のみ使用）
# render 完了後に post_to_webhook へ inlineImages として引き渡す。
_CID_MODE: bool = False
_CID_REGISTRY: dict[str, str] = {}


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

    cid_mode=True のときは `cid:<name>` を返し、data URI をレジストリに記録する。
    GAS 側で MIME multipart/related の inlineImages として展開される。
    """
    if item.get("thumb"):
        return item["thumb"]
    name = f"ng-thumb-{cat_id}" if is_top else f"ng-thumb-common-{cat_id}"
    if _CID_MODE:
        if name not in _CID_REGISTRY:
            _CID_REGISTRY[name] = ng_thumb_data_uri(name)
        return f"cid:{name}"
    return ng_thumb_data_uri(name)


def build_toc_rows(categories: list[dict]) -> str:
    rows = []
    for i, c in enumerate(categories):
        rows.append(f"""
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:4px;"><tbody><tr>
          <td width="32" style="font-family:'JetBrains Mono',Menlo,monospace;font-size:12px;color:{c['accent']};font-weight:700;">{i+1:02d}.</td>
          <td style="font-size:14px;font-weight:700;">
            <span style="color:{c['accent']};margin-right:6px;font-family:'JetBrains Mono',Menlo,monospace;">{c['glyph']}</span>
            {html.escape(c['name'])}
            <span style="color:#8B8B85;font-weight:400;font-size:11px;font-family:'JetBrains Mono',Menlo,monospace;margin-left:8px;">{html.escape(c['nameEn'])}</span>
          </td>
          <td align="right" style="font-family:'JetBrains Mono',Menlo,monospace;font-size:11px;color:#5C5A52;">{len(c['items'])} items</td>
        </tr></tbody></table>""".strip())
    return "\n".join(rows)


def build_article_card(it: dict, idx: int, cat: dict) -> str:
    accent = cat["accent"]
    is_top = (idx == 0)
    img = thumb_url(it, cat["id"], is_top=is_top)
    bullets_html = ""
    for b in it["bullets"]:
        bullets_html += f"""
        <li style="position:relative;padding-left:18px;margin-bottom:8px;font-size:13px;line-height:1.85;color:#1A1A1A;list-style:none;">
          <span style="position:absolute;left:0;top:0;color:{accent};font-weight:700;font-family:'JetBrains Mono',Menlo,monospace;">▸</span>
          {render_inline_emphasis(b, accent)}
        </li>""".strip()

    bg = "#FAF7F0" if idx % 2 == 0 else "#F5F1E7"
    top_label = ""
    feature_img_html = ""
    side_img_html = ""

    if idx == 0:
        top_label = (
            f'<span style="background:{accent};color:#fff;font-family:\'JetBrains Mono\',Menlo,monospace;'
            'font-size:10px;padding:2px 6px;margin-right:8px;vertical-align:middle;letter-spacing:1px;">TOP</span>'
        )
        feature_img_html = f"""
        <div style="margin-bottom:14px;position:relative;border:1px solid #E2DED4;">
          <img src="{img}" alt="" width="568" style="width:100%;height:200px;object-fit:cover;display:block;">
          <div style="position:absolute;bottom:0;left:0;background:{accent};color:#fff;font-family:'JetBrains Mono',Menlo,monospace;font-size:10px;padding:4px 10px;letter-spacing:1.5px;">
            FEATURED · {html.escape(cat['nameEn'].upper())}
          </div>
        </div>"""
    else:
        side_img_html = f"""
          <td width="140" valign="top" style="padding-right:16px;">
            <img src="{img}" alt="" width="140" style="width:140px;height:90px;object-fit:cover;display:block;border:1px solid #E2DED4;">
            <div style="font-family:'JetBrains Mono',Menlo,monospace;font-size:8px;color:#8B8B85;letter-spacing:1px;margin-top:4px;text-align:center;">
              ▢ IMG · {idx+1:02d}
            </div>
          </td>"""

    return f"""
    <tr><td style="padding:24px 36px;background:{bg};border-bottom:1px solid #EDEAE3;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:6px;"><tbody><tr>
        <td>
          <table role="presentation" cellpadding="0" cellspacing="0" border="0"><tbody><tr>
            <td style="background:{accent};color:#fff;font-family:'JetBrains Mono',Menlo,monospace;font-size:11px;font-weight:700;padding:2px 6px;letter-spacing:0.5px;">
              {idx+1:02d}
            </td>
            <td style="padding-left:8px;font-family:'JetBrains Mono',Menlo,monospace;font-size:10px;color:#5C5A52;letter-spacing:0.5px;">
              {html.escape(it.get('time',''))} · {html.escape(it.get('source',''))}
            </td>
          </tr></tbody></table>
        </td>
        <td align="right" style="font-family:'JetBrains Mono',Menlo,monospace;font-size:10px;color:#5C5A52;">
          SCORE <span style="color:{accent};font-weight:700;font-size:14px;margin-left:4px;">{it.get('score','')}</span>
        </td>
      </tr></tbody></table>

      <h3 style="font-size:{19 if idx==0 else 16}px;font-weight:800;line-height:1.45;margin:8px 0 12px;letter-spacing:-0.3px;">
        {top_label}<a href="{html.escape(it.get('url','#'))}" style="color:#1A1A1A;text-decoration:none;">{html.escape(it.get('title',''))}</a>
      </h3>
      {feature_img_html}

      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tbody><tr>
        {side_img_html}
        <td valign="top">
          <ul style="margin:0;padding-left:0;list-style:none;">
            {bullets_html}
          </ul>
        </td>
      </tr></tbody></table>
    </td></tr>"""


def build_categories_html(categories: list[dict]) -> str:
    parts = []
    for ci, cat in enumerate(categories):
        # カテゴリヘッダー
        parts.append(f"""
        <tr><td style="background:{cat['accent']};padding:20px 36px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tbody><tr>
            <td style="vertical-align:middle;">
              <div style="font-family:'JetBrains Mono',Menlo,monospace;font-size:10px;color:rgba(255,255,255,0.7);letter-spacing:2px;margin-bottom:4px;">
                CATEGORY {ci+1:02d} / {len(categories):02d} · {html.escape(cat['nameEn'].upper())}
              </div>
              <div style="font-size:28px;font-weight:800;color:#fff;letter-spacing:-0.5px;line-height:1.1;">
                <span style="font-family:'JetBrains Mono',Menlo,monospace;margin-right:10px;">{cat['glyph']}</span>{html.escape(cat['name'])}
              </div>
            </td>
            <td align="right" style="vertical-align:middle;color:rgba(255,255,255,0.85);font-family:'JetBrains Mono',Menlo,monospace;font-size:11px;">
              {len(cat['items'])} stories
            </td>
          </tr></tbody></table>
          <div style="font-size:13px;color:rgba(255,255,255,0.95);font-style:italic;line-height:1.6;margin-top:10px;padding-top:10px;border-top:1px solid rgba(255,255,255,0.25);">
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
        <tr><td style="background:#FAF7F0;padding:0 36px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-bottom:{border};"><tbody><tr>
            <td width="80" valign="top" style="padding:28px 16px 28px 0;">
              <div style="font-family:'JetBrains Mono',Menlo,monospace;font-size:38px;font-weight:900;color:{accent};line-height:0.9;letter-spacing:-2px;">§{si+1:02d}</div>
              <div style="font-family:'JetBrains Mono',Menlo,monospace;font-size:9px;color:#fff;background:{accent};padding:2px 6px;display:inline-block;letter-spacing:1.5px;margin-top:8px;">{html.escape(sec['tag'])}</div>
            </td>
            <td valign="top" style="padding:28px 0 28px 20px;border-left:1px solid #E2DED4;">
              <h3 style="font-size:18px;font-weight:800;margin:0 0 14px;color:#1A1A1A;letter-spacing:-0.3px;line-height:1.4;">
                {html.escape(sec['heading'])}
              </h3>
              <div style="font-size:13.5px;line-height:2.0;color:#1A1A1A;">
                {render_inline_emphasis(sec['body'], accent)}
              </div>
            </td>
          </tr></tbody></table>
        </td></tr>""")
    return "\n".join(parts)


def build_takeaways(takeaways: list[dict]) -> str:
    parts = []
    for i, t in enumerate(takeaways):
        parts.append(f"""
        <tr><td style="padding-bottom:12px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#fff;border:1px solid #E2DED4;"><tbody><tr>
            <td width="56" valign="middle" style="background:{t['color']};color:#fff;text-align:center;font-family:'JetBrains Mono',Menlo,monospace;font-size:18px;font-weight:900;padding:14px 0;">
              {i+1:02d}
            </td>
            <td style="padding:12px 16px;">
              <div style="font-family:'JetBrains Mono',Menlo,monospace;font-size:9px;color:{t['color']};font-weight:700;letter-spacing:1.5px;margin-bottom:4px;">
                {html.escape(t['tag'].upper())}
              </div>
              <div style="font-size:13px;line-height:1.7;font-weight:600;">
                {render_inline_emphasis(t['text'], t['color'])}
              </div>
            </td>
          </tr></tbody></table>
        </td></tr>""")
    return "\n".join(parts)


def build_related_issues(related: list[dict]) -> str:
    parts = []
    for i, r in enumerate(related):
        is_last = i == len(related) - 1
        border = "none" if is_last else "1px solid #E2DED4"
        parts.append(f"""
        <tr><td style="padding:10px 0;border-bottom:{border};">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tbody><tr>
            <td width="100" style="font-family:'JetBrains Mono',Menlo,monospace;font-size:11px;color:#5C5A52;">{html.escape(r['date'])}</td>
            <td style="font-size:13px;font-weight:600;"><a href="{html.escape(r.get('url','#'))}" style="color:#1A1A1A;text-decoration:none;">{html.escape(r['title'])}</a></td>
            <td width="20" align="right" style="color:#5C5A52;">→</td>
          </tr></tbody></table>
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
    """簡易 HTML 圧縮: タグ間空白除去・連続空白の畳み込み・改行整理。

    Gmail / Outlook はインラインスタイル必須なので `<style>` 抽出はせず、
    空白だけ削る。実測で ~14% 削減。GAS htmlBody 200KB 上限回避用。
    """
    s = re.sub(r">\s+<", "><", html_text)
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
                        help="Webhook 経由でメールを送信する（デフォルト: ローカル保存のみ）")
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

    return 0


if __name__ == "__main__":
    sys.exit(main())
