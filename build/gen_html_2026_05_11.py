# coding: utf-8
"""
2026-05-11 HTML メール生成
gen_email_2026_05_11.py の CATEGORIES / REFLECTION を参照して
prompts/email-template.html のプレースホルダを埋めて build/email.html を出力する
"""
import re, os, sys

BASE = r"C:\Users\hidek\Obsidian\New's Grasp\News-Grasp"
CDN  = "https://raw.githubusercontent.com/HIDEPON-UMG/news-grasp-assets/main"

sys.path.insert(0, os.path.join(BASE, "build"))
from gen_email_2026_05_11 import CATEGORIES, REFLECTION, ISSUE_DATE, ISSUE_NO, WEEKDAY

TOTAL_CATEGORIES = len(CATEGORIES)
TOTAL_STORIES    = sum(len(c["items"]) for c in CATEGORIES)
TOTAL_SECTIONS   = len(REFLECTION["sections"])


# ────────────────────────────────────────────────────────────────
# ヘルパー
# ────────────────────────────────────────────────────────────────
def h(text):
    """[[X]] → <strong style="background:accent;color:#1A1A1A;padding:0 3px;">X</strong>,
       __X__ → <span style="border-bottom:2px solid currentColor;">X</span>"""
    text = re.sub(
        r'\[\[(.+?)\]\]',
        r'<strong style="background:#C9B98A;color:#1A1A1A;padding:0 3px;">\1</strong>',
        text
    )
    text = re.sub(
        r'__(.+?)__',
        r'<span style="border-bottom:2px solid currentColor;">\1</span>',
        text
    )
    return text

def ng_thumb(cat_id, kind="common"):
    if kind == "featured":
        return f"{CDN}/ng-thumb-{cat_id}.jpg"
    return f"{CDN}/ng-thumb-{kind}-{cat_id}.jpg"

def cat_acclass(cat_id):
    return {"fx":"acFx","ai":"acAi","it":"acIt","economy":"acEc","game":"acGm"}.get(cat_id,"acFx")


# ────────────────────────────────────────────────────────────────
# TOC rows
# ────────────────────────────────────────────────────────────────
def build_toc_rows():
    rows = ""
    for cat in CATEGORIES:
        rows += f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:6px;"><tbody><tr>
  <td width="32" class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:12px;color:{cat['accent']};font-weight:700;">{cat['glyph']}</td>
  <td style="font-size:14px;font-weight:700;">{cat['name']} <span class="mut fz10 m" style="color:#5C5A52;font-size:11px;font-family:'JetBrains Mono',Consolas,'Courier New',monospace;">{cat['nameEn']}</span></td>
  <td align="right" class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:11px;color:#5C5A52;">{len(cat['items'])} stories</td>
</tr></tbody></table>"""
    return rows


# ────────────────────────────────────────────────────────────────
# Article card (FEATURED = item 0, side-thumb = item 1-4)
# ────────────────────────────────────────────────────────────────
def build_featured_card(item, cat):
    thumb = item["thumb"] if item["thumb"] else ng_thumb(cat["id"], "featured")
    bullets_html = "".join(
        f'<div class="bul ng-card-body" style="color:{cat["accent"]}"><span class="dk">{h(b)}</span></div>'
        for b in item["bullets"]
    )
    related_html = ""
    if "related" in item:
        r = item["related"]
        related_html = f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:14px;padding-top:10px;border-top:1px dashed #E2DED4;">
  <tbody><tr><td class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:9px;color:#5C5A52;letter-spacing:1.5px;">↩ 続報: {r['axis']}</td></tr>
  <tr><td style="font-size:12px;font-weight:600;">{h(r.get('note',''))}</td></tr></tbody>
</table>"""
    return f"""<tr><td class="ng-card-pad bgcard bbcard pcard" style="background:#FAF7F0;padding:28px 36px;border-bottom:1px solid #EDEAE3;">
  <div class="ng-card-meta m mut fz10 ls05 mb6" style="margin-bottom:8px;">
    <span class="b7 p26 br2 w" style="background:{cat['accent']};color:#fff;padding:3px 8px;font-size:11px;letter-spacing:1px;border-radius:2px;">★ TOP</span>
    <span class="pl8" style="padding-left:10px;">{item['time']} · {item['source']} · SCORE {item['score']}</span>
  </div>
  <h3 class="ng-card-title b8 lh145 t812 lsm03" style="font-size:22px;font-weight:800;margin:10px 0 14px;line-height:1.35;">
    <a href="{item['url']}" class="dk tdn" style="color:#1A1A1A;text-decoration:none;">{item['title']}</a>
  </h3>
  <div class="ng-feature-img" style="margin-bottom:16px;">
    <a href="{item['url']}" class="db tdn" style="display:block;text-decoration:none;">
      <img src="{thumb}" width="568" height="220" alt="" class="db ofc brd" style="display:block;width:100%;height:220px;object-fit:cover;border:1px solid #E2DED4;">
    </a>
  </div>
  {bullets_html}{related_html}
</td></tr>"""


def build_side_card(item, cat, idx):
    thumb = item["thumb"] if item["thumb"] else ng_thumb(cat["id"], "common")
    bullets_html = "".join(
        f'<div class="bul ng-card-body" style="color:{cat["accent"]}"><span class="dk">{h(b)}</span></div>'
        for b in item["bullets"]
    )
    return f"""<tr><td class="ng-card-pad bgcard bbcard pcard" style="background:#FAF7F0;padding:22px 36px;border-bottom:1px solid #EDEAE3;">
  <div class="ng-card-meta m mut fz10 ls05 mb6" style="margin-bottom:6px;">
    <span class="b7 p26 br2 w" style="background:{cat['accent']};color:#fff;padding:2px 6px;font-size:12px;">{idx:02d}</span>
    <span class="pl8" style="padding-left:8px;">{item['time']} · {item['source']} · SCORE {item['score']}</span>
  </div>
  <h3 class="ng-card-title b8 lh145 t812 lsm03" style="font-size:18px;font-weight:800;margin:8px 0 12px;line-height:1.4;">
    <a href="{item['url']}" class="dk tdn" style="color:#1A1A1A;text-decoration:none;">{item['title']}</a>
  </h3>
  <table width="100%" class="ng-side-table"><tbody><tr>
    <td class="ng-card-thumb thb pr16 vtop" width="140" style="width:140px;height:90px;vertical-align:top;padding-right:16px;">
      <a href="{item['url']}" class="db tdn" style="display:block;text-decoration:none;">
        <img src="{thumb}" width="140" height="90" alt="" class="ng-card-thumb-img db ofc brd" style="display:block;width:140px;height:90px;object-fit:cover;border:1px solid #E2DED4;">
      </a>
    </td>
    <td class="ng-card-body-cell vtop" style="vertical-align:top;">
      {bullets_html}
    </td>
  </tr></tbody></table>
</td></tr>"""


# ────────────────────────────────────────────────────────────────
# Category block
# ────────────────────────────────────────────────────────────────
def build_category_block(cat):
    cards = build_featured_card(cat["items"][0], cat)
    for i, item in enumerate(cat["items"][1:], 2):
        cards += build_side_card(item, cat, i)

    return f"""<tr><td class="ng-cat-pad" style="background:{cat['accent']};padding:22px 36px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tbody><tr>
    <td style="vertical-align:middle;">
      <div class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:10px;color:rgba(255,255,255,0.7);letter-spacing:2px;margin-bottom:4px;">CATEGORY {cat['index']} / {TOTAL_CATEGORIES} · {cat['nameEn'].upper()}</div>
      <div style="font-size:28px;font-weight:800;color:#fff;letter-spacing:-0.5px;line-height:1.1;">
        <span class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;margin-right:10px;">{cat['glyph']}</span>{cat['name']}
      </div>
    </td>
    <td align="right" style="vertical-align:middle;color:rgba(255,255,255,0.85);font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:11px;">{len(cat['items'])} stories</td>
  </tr></tbody></table>
  <div class="ng-cat-summary" style="font-size:13px;color:rgba(255,255,255,0.95);font-style:italic;line-height:1.6;margin-top:10px;padding-top:10px;border-top:1px solid rgba(255,255,255,0.25);">{cat['summary']}</div>
</td></tr>
{cards}"""


# ────────────────────────────────────────────────────────────────
# Reflection sections
# ────────────────────────────────────────────────────────────────
def build_sections():
    sec_accents = ["#1A1A1A","#B8860B","#2D5BB8","#2E6B52","#C9B98A"]
    html = ""
    for i, s in enumerate(REFLECTION["sections"], 1):
        acc = sec_accents[min(i-1, len(sec_accents)-1)]
        html += f"""<tr><td class="ng-section-pad" style="background:#FAF7F0;padding:0 36px;">
  <table width="100%" style="border-bottom:1px dashed #E2DED4;"><tbody><tr>
    <td class="ng-section-num-cell" width="80" valign="top" style="padding:28px 16px 28px 0;vertical-align:top;">
      <div class="m ng-section-num" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:38px;font-weight:900;color:{acc};line-height:0.9;letter-spacing:-2px;">§{i:02d}</div>
      <div class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:9px;color:#fff;background:{acc};padding:2px 6px;display:inline-block;letter-spacing:1.5px;margin-top:8px;">{s['tag']}</div>
    </td>
    <td class="ng-section-text-cell" valign="top" style="padding:28px 0 28px 20px;border-left:1px solid #E2DED4;vertical-align:top;">
      <h3 class="ng-section-heading" style="font-size:18px;font-weight:800;margin:0 0 14px;">{s['heading']}</h3>
      <div class="ng-section-body" style="font-size:13.5px;line-height:2.0;">{h(s['body'])}</div>
    </td>
  </tr></tbody></table>
</td></tr>"""
    return html


# ────────────────────────────────────────────────────────────────
# Takeaways
# ────────────────────────────────────────────────────────────────
def build_takeaways():
    html = ""
    for i, t in enumerate(REFLECTION["takeaways"], 1):
        html += f"""<tr><td style="padding-bottom:12px;">
  <table width="100%" style="background:#fff;border:1px solid #E2DED4;border-collapse:collapse;"><tbody><tr>
    <td width="56" valign="middle" class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;background:{t['color']};color:#fff;text-align:center;font-size:18px;font-weight:900;padding:14px 0;width:56px;vertical-align:middle;">{i}</td>
    <td style="padding:12px 16px;">
      <div class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:9px;color:{t['color']};font-weight:700;letter-spacing:1.5px;margin-bottom:4px;">{t['tag'].upper()}</div>
      <div style="font-size:13px;line-height:1.7;font-weight:600;">{h(t['text'])}</div>
    </td>
  </tr></tbody></table>
</td></tr>"""
    return html


# ────────────────────────────────────────────────────────────────
# Related issues
# ────────────────────────────────────────────────────────────────
def build_related():
    html = ""
    for r in REFLECTION["related"]:
        html += f"""<tr><td style="padding:10px 0;border-bottom:1px solid #E2DED4;">
  <table width="100%"><tbody><tr>
    <td width="100" class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:11px;color:#5C5A52;">{r['date']}</td>
    <td style="font-size:13px;font-weight:600;"><a href="https://github.com/HIDEPON-UMG/News-Grasp/blob/main/digest/Summary/{r['date']}.md" style="color:#1A1A1A;text-decoration:none;">{r['title']}</a></td>
    <td width="20" align="right" style="color:#5C5A52;">→</td>
  </tr></tbody></table>
</td></tr>"""
    return html


# ────────────────────────────────────────────────────────────────
# メイン生成
# ────────────────────────────────────────────────────────────────
def main():
    tmpl_path = os.path.join(BASE, "prompts", "email-template.html")
    with open(tmpl_path, encoding="utf-8") as f:
        tmpl = f.read()

    categories_html = "\n".join(build_category_block(cat) for cat in CATEGORIES)

    replacements = {
        "{{ISSUE_DATE}}":         ISSUE_DATE,
        "{{ISSUE_WEEKDAY}}":      WEEKDAY,
        "{{ISSUE_NO}}":           ISSUE_NO,
        "{{TOTAL_CATEGORIES}}":   str(TOTAL_CATEGORIES),
        "{{TOTAL_STORIES}}":      str(TOTAL_STORIES),
        "{{TOTAL_SECTIONS}}":     str(TOTAL_SECTIONS),
        "{{TOC_ROWS_HTML}}":      build_toc_rows(),
        "{{CATEGORIES_HTML}}":    categories_html,
        "{{REFLECTION_TITLE}}":   REFLECTION["title"],
        "{{REFLECTION_SUBTITLE}}": REFLECTION["subtitle"],
        "{{REFLECTION_LEAD_HTML}}": h(REFLECTION["lead"]),
        "{{REFLECTION_PULL_QUOTE_HTML}}": h(REFLECTION["pull_quote"]),
        "{{REFLECTION_SECTIONS_HTML}}": build_sections(),
        "{{TAKEAWAYS_HTML}}":     build_takeaways(),
        "{{RELATED_ISSUES_HTML}}": build_related(),
    }

    html = tmpl
    for k, v in replacements.items():
        html = html.replace(k, v)

    out_path = os.path.join(BASE, "build", "email.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    size_kb = os.path.getsize(out_path) / 1024
    print(f"Written: {out_path} ({size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
