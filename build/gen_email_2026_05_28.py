# -*- coding: utf-8 -*-
"""2026-05-28 号 HTML メール生成スクリプト (水曜: FX/AI/IT-Consulting/Mobility/Economy)"""
import re, sys, os
sys.stdout.reconfigure(encoding='utf-8')
os.chdir(os.path.dirname(os.path.abspath(__file__)) + '/..')

ISSUE_DATE = '2026-05-28'
ISSUE_NO   = '20260528'
WEEKDAY    = '水'
CDN        = 'https://raw.githubusercontent.com/HIDEPON-UMG/news-grasp-assets/main'
ISSUE_WEB_URL = 'https://github.com/HIDEPON-UMG/News-Grasp/blob/main/digest/Summary/2026-05-28.md'

def _hl(text, accent):
    bg = accent + '22'
    text = re.sub(r'\[\[(.+?)\]\]',
        f'<strong style="background:{bg};padding:1px 3px;border-radius:2px;">\\1</strong>', text)
    text = re.sub(r'__(.+?)__',
        '<span style="border-bottom:2px solid ' + accent + ';padding-bottom:1px;">\\1</span>', text)
    text = re.sub(r'\*\*(.+?)\*\*', '<b>\\1</b>', text)
    return text

def render_bullets(bullets, accent):
    out = []
    for b in bullets:
        out.append(f'<div class="bul ng-card-body" style="color:{accent}">'
                   f'<span class="dk">{_hl(b, accent)}</span></div>')
    return '\n'.join(out)

def render_lead(text):
    text = re.sub(r'\[\[(.+?)\]\]',
        '<strong style="background:#C9B98A33;padding:1px 3px;border-radius:2px;">\\1</strong>', text)
    text = re.sub(r'__(.+?)__',
        '<span style="border-bottom:2px solid #C9B98A;padding-bottom:1px;">\\1</span>', text)
    text = re.sub(r'\*\*(.+?)\*\*', '<b>\\1</b>', text)
    return text

def render_pullquote(text):
    text = re.sub(r'\[\[(.+?)\]\]',
        '<strong style="background:#C9B98A33;padding:1px 3px;">\\1</strong>', text)
    text = re.sub(r'__(.+?)__',
        '<span style="border-bottom:2px solid #8E2A19;padding-bottom:1px;">\\1</span>', text)
    text = re.sub(r'\*\*(.+?)\*\*', '<b>\\1</b>', text)
    return text

def render_section_body(text):
    text = re.sub(r'\[\[(.+?)\]\]',
        '<strong style="background:#C9B98A33;padding:1px 3px;border-radius:2px;">\\1</strong>', text)
    text = re.sub(r'__(.+?)__',
        '<span style="border-bottom:2px solid #C9B98A;padding-bottom:1px;">\\1</span>', text)
    text = re.sub(r'\*\*(.+?)\*\*', '<b>\\1</b>', text)
    return text

def thumb_src(thumb, cat):
    return thumb if thumb else f'{CDN}/ng-thumb-common-{cat}.jpg'

def thumb_featured(thumb, cat):
    return thumb if thumb else f'{CDN}/ng-thumb-{cat}.jpg'

def toc_row(idx, cat_id, name_jp, name_en, glyph, accent, n):
    return f'''<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:8px;"><tbody><tr>
  <td width="32" class="m" style="font-family:\'JetBrains Mono\',Consolas,\'Courier New\',monospace;font-size:12px;color:{accent};font-weight:700;">{glyph}</td>
  <td style="font-size:14px;font-weight:700;">{idx}. {name_jp} <span style="color:#5C5A52;font-weight:400;font-size:12px;">({name_en})</span></td>
  <td align="right" class="m" style="font-family:\'JetBrains Mono\',Consolas,\'Courier New\',monospace;font-size:11px;color:#5C5A52;">{n} stories</td>
</tr></tbody></table>'''

def cat_header(idx, total, cat_id, name_jp, name_en, glyph, accent, n, summary):
    en_upper = name_en.upper()
    return f'''<tr><td class="ng-cat-pad" style="background:{accent};padding:20px 36px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tbody><tr>
    <td style="vertical-align:middle;">
      <div class="m ng-cat-name" style="font-family:\'JetBrains Mono\',Consolas,\'Courier New\',monospace;font-size:10px;color:rgba(255,255,255,0.7);letter-spacing:2px;margin-bottom:4px;">
        CATEGORY {idx} / {total} · {en_upper}
      </div>
      <div style="font-size:28px;font-weight:800;color:#fff;letter-spacing:-0.5px;line-height:1.1;">
        <span style="font-family:\'JetBrains Mono\',Consolas,\'Courier New\',monospace;margin-right:10px;">{glyph}</span>{name_jp}
      </div>
    </td>
    <td align="right" style="vertical-align:middle;color:rgba(255,255,255,0.85);font-family:\'JetBrains Mono\',Consolas,\'Courier New\',monospace;font-size:11px;">{n} stories</td>
  </tr></tbody></table>
  <div class="ng-cat-summary" style="font-size:13px;color:rgba(255,255,255,0.95);font-style:italic;line-height:1.6;margin-top:10px;padding-top:10px;border-top:1px solid rgba(255,255,255,0.25);">
    {summary}
  </div>
</td></tr>'''

def card_featured(art, cat_id, accent, rank_label):
    src = thumb_featured(art['thumb'], cat_id)
    bullets_html = render_bullets(art['bullets'], accent)
    title_safe = art['title'].replace('&', '&amp;')
    rel = ''
    if art.get('related'):
        r = art['related']
        rel = f'''<div style="margin-top:16px;padding:10px 14px;background:#F2EEE3;border-left:3px solid {accent};font-size:12px;color:#5C5A52;">
      <span class="m" style="font-family:\'JetBrains Mono\',Consolas,\'Courier New\',monospace;font-weight:700;color:{accent};">↩ 関連: {r["axis"]}</span>
      &nbsp;{r["ref_title"]} ({r["ref_date"]}) — {r.get("note","")}</div>'''
    return f'''<tr><td class="ng-card-pad bgcard bbcard pcard" style="background:#FAF7F0;padding:24px 36px;border-bottom:1px solid #EDEAE3;">
  <div class="ng-card-meta m mut fz10 ls05" style="margin-bottom:6px;">
    <span class="b7 w p26 br2" style="background:{accent};color:#fff;padding:2px 6px;font-size:12px;font-weight:700;">{rank_label}</span>
    <span class="pl8">{art["time"]} · {art["source"]} · SCORE {art["score"]}</span>
  </div>
  <h3 class="ng-card-title b8 lh145 t812 lsm03" style="font-size:22px;font-weight:800;margin:8px 0 14px;letter-spacing:-0.3px;">
    <a href="{art["url"]}" class="dk tdn" style="color:#1A1A1A;text-decoration:none;">{title_safe}</a>
  </h3>
  <div class="ng-feature-img" style="margin-bottom:16px;background:#E8E4DA;">
    <a href="{art["url"]}" style="display:block;">
      <img src="{src}" width="568" alt="" style="display:block;width:100%;height:200px;object-fit:cover;border:1px solid #E2DED4;">
    </a>
  </div>
  {bullets_html}
  {rel}
</td></tr>'''

def card_side(art, cat_id, accent, rank_num):
    src = thumb_src(art['thumb'], cat_id)
    bullets_html = render_bullets(art['bullets'], accent)
    title_safe = art['title'].replace('&', '&amp;')
    rel = ''
    if art.get('related'):
        r = art['related']
        rel = f'''<div style="margin-top:12px;padding:8px 12px;background:#F2EEE3;border-left:3px solid {accent};font-size:11px;color:#5C5A52;">
      <span style="font-weight:700;color:{accent};">↩ 関連: {r["axis"]}</span>
      &nbsp;{r["ref_title"]} ({r["ref_date"]}) — {r.get("note","")}</div>'''
    return f'''<tr><td class="ng-card-pad bgcard bbcard pcard" style="background:#FAF7F0;padding:20px 36px;border-bottom:1px solid #EDEAE3;">
  <div class="ng-card-meta m mut fz10 ls05" style="margin-bottom:6px;">
    <span class="b7 p26 br2" style="background:{accent};color:#fff;padding:2px 6px;font-size:12px;font-weight:700;">{rank_num:02d}</span>
    <span class="pl8">{art["time"]} · {art["source"]} · SCORE {art["score"]}</span>
  </div>
  <h3 class="ng-card-title b8 lh145 t812 lsm03" style="font-size:18px;font-weight:800;margin:8px 0 12px;letter-spacing:-0.2px;">
    <a href="{art["url"]}" class="dk tdn" style="color:#1A1A1A;text-decoration:none;">{title_safe}</a>
  </h3>
  <table width="100%" class="ng-side-table"><tbody><tr>
    <td class="ng-card-thumb thb pr16 vtop" width="140" style="vertical-align:top;padding-right:16px;">
      <a href="{art["url"]}" class="db tdn" style="display:block;text-decoration:none;">
        <img src="{src}" width="140" height="90" alt="" class="ng-card-thumb-img db ofc brd"
             style="display:block;object-fit:cover;border:1px solid #E2DED4;border-radius:2px;">
      </a>
    </td>
    <td class="ng-card-body-cell vtop" style="vertical-align:top;">
      {bullets_html}
    </td>
  </tr></tbody></table>
  {rel}
</td></tr>'''

def section_row(num, tag, heading, body_text, accent):
    body_html = render_section_body(body_text)
    return f'''<tr><td class="ng-section-pad" style="background:#FAF7F0;padding:0 36px;">
  <table width="100%" style="border-bottom:1px dashed #E2DED4;"><tbody><tr>
    <td class="ng-section-num-cell" width="80" valign="top" style="padding:28px 16px 28px 0;vertical-align:top;">
      <div class="m ng-section-num" style="font-family:\'JetBrains Mono\',Consolas,\'Courier New\',monospace;font-size:38px;font-weight:900;color:{accent};line-height:0.9;letter-spacing:-2px;">§{num:02d}</div>
      <div class="m" style="font-family:\'JetBrains Mono\',Consolas,\'Courier New\',monospace;font-size:9px;color:#fff;background:{accent};padding:2px 6px;display:inline-block;letter-spacing:1.5px;margin-top:8px;">{tag}</div>
    </td>
    <td class="ng-section-text-cell" valign="top" style="padding:28px 0 28px 20px;border-left:1px solid #E2DED4;vertical-align:top;">
      <h3 class="ng-section-heading" style="font-size:18px;font-weight:800;margin:0 0 14px;">{heading}</h3>
      <div class="ng-section-body" style="font-size:13.5px;line-height:2.0;">{body_html}</div>
    </td>
  </tr></tbody></table>
</td></tr>'''

def takeaway_row(num, tag, color, text):
    text_html = re.sub(r'\[\[(.+?)\]\]',
        f'<strong style="background:{color}22;padding:1px 2px;">\\1</strong>', text)
    text_html = re.sub(r'__(.+?)__',
        f'<span style="border-bottom:2px solid {color};">\\1</span>', text_html)
    text_html = re.sub(r'\*\*(.+?)\*\*', '<b>\\1</b>', text_html)
    return f'''<tr><td style="padding-bottom:12px;">
  <table width="100%" style="background:#fff;border:1px solid #E2DED4;"><tbody><tr>
    <td width="56" valign="middle" class="m" style="background:{color};color:#fff;text-align:center;font-family:\'JetBrains Mono\',Consolas,\'Courier New\',monospace;font-size:18px;font-weight:900;padding:14px 0;">{num}</td>
    <td style="padding:12px 16px;">
      <div class="m" style="font-family:\'JetBrains Mono\',Consolas,\'Courier New\',monospace;font-size:9px;color:{color};font-weight:700;letter-spacing:1.5px;margin-bottom:4px;">{tag.upper()}</div>
      <div style="font-size:13px;line-height:1.7;font-weight:600;">{text_html}</div>
    </td>
  </tr></tbody></table>
</td></tr>'''

def related_row(date_str, title, url='#'):
    return f'''<tr><td style="padding:10px 0;border-bottom:1px solid #E2DED4;">
  <table width="100%"><tbody><tr>
    <td width="100" class="m" style="font-family:\'JetBrains Mono\',Consolas,\'Courier New\',monospace;font-size:11px;color:#5C5A52;">{date_str}</td>
    <td style="font-size:13px;font-weight:600;"><a href="{url}" style="color:#1A1A1A;text-decoration:none;">{title}</a></td>
    <td width="20" align="right" style="color:#5C5A52;">→</td>
  </tr></tbody></table>
</td></tr>'''

# ═══════════════════════════════════════════════════════════════════════════════
# DATA — 2026-05-28 (水曜: FX / AI / IT-Consulting / Mobility / Economy)
# ═══════════════════════════════════════════════════════════════════════════════
cats = [
  {
    'id': 'fx', 'name_jp': '為替', 'name_en': 'Foreign Exchange', 'glyph': '¥',
    'accent': '#B8860B',
    'summary': '本日21:30JSTに米4月PCEとQ1 GDP第2次推計が同時公開される。ヘッドラインCPIが3.8%と予想を上振れた4月の流れを引き継ぎ高インフレ確認なら<b>USD/JPY 160円</b>接近が再浮上。Warsh議長の「双方向リスク」宣言と石油インフレ長期化懸念が今週の為替相場を規定する。',
    'articles': [
      {'score': 95, 'time': '08:00', 'source': 'HeyGoTrade',
       'title': 'PCE・GDP同時公開で「高インフレ確認」──USD/JPY 159台後半が週内射程、利下げシナリオ後退',
       'url': 'https://www.heygotrade.com/en/news/weekly-economic-outlook-2026-05-25/',
       'thumb': 'https://files-tr8.s3.ap-southeast-1.amazonaws.com/blog_images/us-economiccalendar_1769422651.webp',
       'bullets': [
         '本日21:30JST公開の[[米4月PCE]]と[[Q1 GDP第2次推計]]が今週の最大焦点で、ヘッドラインCPIの**3.8%**超過が示す通り高インフレ継続の場合は**USD/JPY 160円**接近リスクが高まる',
         '[[コアPCE]]が3月の**3.2%**から上振れした場合、FRBの年内利下げシナリオは実質的に崩壊し、__タカ派路線が固定化__される可能性が高い',
         '一方でホルムズ海峡再開交渉による**原油安**がインフレ押し下げ材料として機能し、PCEが予想内に収まれば**ドル売り転換**の好機となる',
       ],
       'related': None},
      {'score': 91, 'time': '07:30', 'source': 'HeyGoTrade',
       'title': 'Warsh議長初FOMC議事録「双方向リスク」宣言──タカ派6名 vs 非タカ派4名の温度差が鮮明',
       'url': 'https://www.heygotrade.com/en/blog/warsh-fomc-minutes-may-20-dissent-map/',
       'thumb': 'https://files-tr8.s3.ap-southeast-1.amazonaws.com/blog-posts/55b582f5be020f5bdcf61df3/feature-images/6a0a82021b515-feature-image.jpg',
       'bullets': [
         '[[ウォーシュ議長]]の初FOMC議事録（5月20日公表）は「双方向リスク」を明記し、6名がタカ派・4名が非タカ派と**温度差が明確**で、次回6/16-17会合が真の試金石となる',
         '__ウォーシュ体制下の利下げ封印__は市場にドル高圧力を与え続け、FRBが**インフレ目標2%達成**を最優先する姿勢を鮮明にした',
         '議事録では「**石油主導インフレ**の長期化リスク」が繰り返し言及され、中東情勢の安定化なしには金融緩和に踏み切れないとの見解が支配的',
       ],
       'related': None},
      {'score': 87, 'time': '07:00', 'source': 'Wichita Liberty',
       'title': '米Q1 GDP第2次推計2.0%確定・PCEデフレーター4.5%──「スタグフレーション」論争が再燃',
       'url': 'https://www.wichitaliberty.org/economics/us-gdp-q1-2026-advance-estimate-inflation-stagflation/',
       'thumb': 'https://www.wichitaliberty.org/wp-content/uploads/2026/04/gdp-2026-q1-1.jpg',
       'bullets': [
         '米Q1 GDP成長率**2.0%**は底堅さを示したが、同時にPCEデフレーターが**4.5%**と高止まりし、成長とインフレが並走する「[[スタグフレーション的景色]]」が市場の不安を煽る',
         'コア個人消費支出デフレーターも**4.3%**と前期比で大幅上昇し、FRBが目標とする2%の**2倍超**の水準が継続的な引き締めを正当化している',
         '__成長は維持しつつインフレが高止まり__という組み合わせが確認されれば、USD/JPYは当面の高値圏を維持するとの見方が市場コンセンサスに浮上',
       ],
       'related': None},
      {'score': 83, 'time': '06:30', 'source': 'HeyGoTrade',
       'title': 'FOMC議事録「石油主導インフレ長期化」懸念──6月利下げなしほぼ確定、年内2回も疑念',
       'url': 'https://www.heygotrade.com/en/blog/may-2026-fomc-minutes-two-sided-framework-oil-inflation/',
       'thumb': 'https://files-tr8.s3.ap-southeast-1.amazonaws.com/blog-posts/a9ee897e94bf0258778c24d4/feature-images/6a0fc9335bdf7-feature-image.jpg',
       'bullets': [
         '[[FOMC議事録]]は石油価格の上昇が近期インフレ期待を**構造的に押し上げている**と指摘し、FRBメンバーが最も警戒するシナリオとして「原油高→賃金波及」を挙げた',
         '**6月利下げ**は事実上排除、市場が織り込む「年2回利下げ」シナリオも「年1回」へと修正されつつあり、__金利差縮小による円高転換時期__はさらに先送り',
         'ウォラー理事の「年2回可能」論が議事録内で**少数派**と確認され、本日のPCE次第でその少数派意見も修正を迫られる局面',
       ],
       'related': None},
      {'score': 78, 'time': '06:00', 'source': 'みんかぶFX',
       'title': 'FOMC議事録がトランプ「ドル安容認」を示唆──NYC FRBのドル円確認で日米160円防衛が焦点に',
       'url': 'https://fx.minkabu.jp/news/358841',
       'thumb': 'https://mfx-assets.s3.ap-northeast-1.amazonaws.com/news_ogp/forex.png',
       'bullets': [
         '[[FOMCシグナル]]がトランプ政権のドル安容認を示唆した背景は米国輸出競争力維持と財政赤字ファイナンスの両立への配慮で、**ドル安・ドル高のどちらにも踏み切れない**板挟み状態が続く',
         'NYC FRBが[[ドル円]]レートを積極確認していた事実が露呈し、日米両国が**160円防衛**を非公式に協調している可能性をアナリストが指摘している',
         '__為替政策の透明性低下__は短期的なスペキュレーティブポジションを難しくしており、実需主体（輸出企業）の**売り引き付け**姿勢が市場を下支え',
       ],
       'related': None},
    ]
  },
  {
    'id': 'ai', 'name_jp': 'AI', 'name_en': 'Artificial Intelligence', 'glyph': '◆',
    'accent': '#2D5BB8',
    'summary': 'AnthropicがSequoia主導の<b>$300億成長ラウンド</b>を完了し評価額$9000億超到達。同日「Claude for Small Business」を発表し中小企業市場に参入、さらにStainless社を$3億超で買収しAPI生態系を内製化。KPMGとPwCとの巨大アライアンスと合わせ、Anthropicがエコシステムの覇権を一気に固める週となった。',
    'articles': [
      {'score': 94, 'time': '07:45', 'source': 'Crescendo.ai',
       'title': 'AnthropicがSequoia主導$300億調達・評価額$9000億超──ARR $30B、12週で2倍の異常成長',
       'url': 'https://www.crescendo.ai/news/latest-ai-news-and-updates',
       'thumb': 'https://cdn.prod.website-files.com/67166bf779ba8852260f7d1f/694a731a76c9d6876ddf8a8f_latest-ai-news-and-updates.png',
       'bullets': [
         '[[Anthropic]]がSequoia Capital・Dragoneer・Altimeter・Greenoaks主導の$300億Growth Roundを完了し、評価額は**$9000億超**に到達——2026年2度目の$300億調達という異例の成長速度',
         '[[ダリオ・アモデイ]]CEOは「**ARRが2月$140億から4月$300億へ12週で2倍**」と報告し、ChatGPTとの差を縮めながらエンタープライズ市場での圧倒的な伸びを強調',
         '__AIの覇権は資金力と生態系の掌握__で決まる段階に入り、Anthropicが今週だけで調達・製品・M&Aを三連打した事実が示すのはスピードを競うフェーズの到達点だ',
       ],
       'related': None},
      {'score': 91, 'time': '07:20', 'source': 'Anthropic',
       'title': 'Claude for Small Business──QB/PayPal/HubSpot 7連携で米中小企業44%GDP層を攻略',
       'url': 'https://www.anthropic.com/news/claude-for-small-business',
       'thumb': None,
       'bullets': [
         '[[Claude for Small Business]]はQuickBooks・PayPal・HubSpot・Canva・DocuSign・Google Workspace・Microsoft 365の**7サービスとワンクリック連携**し、給与計算・請求書追跡・月末締めを自動化する',
         '米国GDP44%・雇用50%近くを占める中小企業は**エンタープライズ向けAIから取り残されていた**層であり、Anthropicは「テイラーメードな操作性」を訴求して同市場を初めて本格開拓する',
         '__中小企業への橋渡し__はOpenAI ChatGPT TeamsやMicrosoft Copilotが未だ十分にカバーできていない領域で、Anthropicが先手を打つことで**市場シェアの非連続拡大**が期待される',
       ],
       'related': None},
      {'score': 88, 'time': '06:50', 'source': 'Axios',
       'title': 'AnthropicがStainless社を$3億超で買収──SDK自動生成でAPI普及インフラを内製化',
       'url': 'https://www.axios.com/2026/05/21/google-ai-anthropic-openai-war',
       'thumb': None,
       'bullets': [
         '[[Anthropic]]が買収した[[Stainless]]はOpenAI・Google・Cloudflareを含む**主要APIファーストの企業が標準採用**するSDK自動生成ツールで、「APIの普及経路」そのものを手中に収める',
         '報道された買収額は**$3億超**——見かけの数字より重要なのは、開発者がAIエンジニアリングの入り口で最初に触れる「**配布メカニズム**」をAnthropicが掌握した点にある',
         '__インフラの支配がコンテンツの支配に先行する__というプラットフォーム経済の法則が、ここでもまた繰り返されている——競合からみれば**抜き難い堀**が誕生した瞬間だ',
       ],
       'related': None},
      {'score': 84, 'time': '06:20', 'source': 'TechRadar',
       'title': 'Anthropic、銀行・保険向けClaude AIエージェント10本組みを公開──金融業の最多時間消費タスクを直撃',
       'url': 'https://www.techradar.com/pro/anthropic-rolls-out-a-host-of-new-ai-agents-to-target-the-most-time-consuming-work-in-financial-services',
       'thumb': 'https://cdn.mos.cms.futurecdn.net/vWN5Up37jj5BPydYATu5UR-1920-80.png',
       'bullets': [
         '[[Anthropic]]は銀行・保険・資産運用向けに**10本のプリビルトClaude AIエージェント**を公開し、Claude Cowork・Claude Code・Claude Managed Agentsを通じて即時展開を可能にした',
         '対象タスクは規制変更への対応・顧客ドキュメント処理・リスク審査などで、従来**数週間かかっていた作業が数分**に短縮されるとAnthropicはデモを示す',
         '__金融機関のAI導入を「週次作業の短縮」ではなく「組織ワークフローの刷新」__として位置付けた点が新しく、KPMGとの27.6万人同盟と相乗りする勝ちパターンが見えてくる',
       ],
       'related': None},
      {'score': 79, 'time': '05:50', 'source': 'CNBC',
       'title': 'トランプ政権がAI監督フレームワーク策定──Google・MS・xAIを政府テスト対象に指定',
       'url': 'https://www.cnbc.com/2026/05/05/ai-oversight-trump-google-microsoft-xai.html',
       'thumb': 'https://image.cnbcfm.com/api/v1/image/108301477-1777896715502-gettyimages-2273875296-rs2_2378.jpeg?v=1778786289&w=1920&h=1080',
       'bullets': [
         '[[トランプ政権]]はAI安全性の政府監督フレームワークを策定し、[[Google]]・[[Microsoft]]・[[xAI]]のモデルを最初の**政府テスト対象**に指定——規制の方向性が「イノベーション優先」から「安全検証義務化」へと微妙にシフト',
         'Anthropicは既にホワイトハウスとの関係強化を進めており、**規制枠組みへの関与**でOpenAI・Googleと並ぶ「制度設計者」ポジションを確立しようとしている',
         '__AI監督の制度化はスタートアップより既存大手に有利__に働く傾向があり、コンプライアンスコストが競合排除の参入障壁として機能する皮肉な逆効果も指摘されている',
       ],
       'related': None},
    ]
  },
  {
    'id': 'it', 'name_jp': 'IT-Consulting', 'name_en': 'IT &amp; Consulting', 'glyph': '▲',
    'accent': '#2E6B52',
    'summary': 'KPMGとAnthropicの<b>27.6万人同盟</b>、PwCとの拡大契約と相次ぐBig 4連携でAnthropicがコンサル業界の基盤AIとなる宣言をした週。一方でOpenAI DeployCo・FourWeekMBAが分析する<b>$40億コンサル戦争</b>は「AIとコンサルの共存か競争か」の決着点を問い直す。日本SIerはコンサル転換の正念場を迎えている。',
    'articles': [
      {'score': 93, 'time': '07:40', 'source': 'KPMG',
       'title': 'KPMGとAnthropicがグローバル同盟──27.6万人全員Claude利用、Digital Gateway powered by Claude',
       'url': 'https://kpmg.com/xx/en/media/press-releases/2026/05/kpmg-and-anthropic-sign-global-alliance-and-launch-digital-gateway-powered-by-claude.html',
       'thumb': 'https://kpmg.com/content/dam/kpmgsites/xx/images/2026/05/blue-gradient-background-with-glowing-dotted-network-pattern.jpg',
       'bullets': [
         '[[KPMG]]と[[Anthropic]]が2026年5月19日にグローバル同盟を締結し、KPMG Digital Gatewayに**Claude Managed Agentsを全面統合**——27.6万人超の全社員が即日Claude利用可能になった',
         'PEファーム・税務クライアント向けの「**KPMG Blaze**」はClaude Codeでレガシーシステムのモダナイズを自動化し、エージェントワークフロー構築が**従来数週間→数分**に短縮されると実演',
         '__Big 4がAnthropicの旗艦エンタープライズパートナーに並ぶ__流れは、Claudeが「コンサル業界の標準AI」として定着しつつある証左であり、競合コンサルへの**ブランド・スイッチコスト**をさらに高める',
       ],
       'related': None},
      {'score': 89, 'time': '07:10', 'source': 'Anthropic',
       'title': 'PwCとAnthropicが拡大パートナーシップ──PE・税務・M&A案件をClaude Codeで自動化、Big4 AI競争が加熱',
       'url': 'https://www.anthropic.com/news/pwc-expanded-partnership',
       'thumb': None,
       'bullets': [
         '[[PwC]]はAnthropicとの既存パートナーシップを拡大し、PE・税務・M&Aアドバイザリーの業務プロセスに**Claude Codeを本番展開**——エージェント型ワークフローで規制対応速度を従来比大幅短縮',
         'KPMGとの同盟発表と連続することで、Big 4の2社が**同週にAnthropicと深化契約**という異例の動きとなり、残るDeloitte・EYの選択を巡る観測が業界内で過熱中',
         '__コンサルはAIを「ツール」として使う時代から「Claudeを中核に置いた新サービスアーキテクチャ」を構築する時代__に入ったと言えるが、そのサービスを設計するのは依然コンサルタントであるという逆説',
       ],
       'related': None},
      {'score': 85, 'time': '06:40', 'source': 'FourWeekMBA',
       'title': 'OpenAI vs Accenture「$40億コンサル戦争」──DeployCo 150名がコンサル聖域に直接侵食',
       'url': 'https://fourweekmba.com/openai-vs-accenture-the-4b-consulting-war-that-redefines-enterprise-ai/',
       'thumb': None,
       'bullets': [
         '[[OpenAI]] DeployCo（5月11日設立・$40億投資・FDE 150名）はアクセンチュア・マッキンゼーが支配してきたエンタープライズAI実装市場に**直接参戦**し、コンサルなき「AI直販」モデルの可能性を実証しようとしている',
         'McKinsey・Bainが**DeployCoに自ら出資**したことは、「戦うより参加する」という適応戦略が最善と見た証拠で、__コンサル業界の分断線はAI企業との競合ではなく取り込みか排除か__の二択に移った',
         'アクセンチュアのCEOは「**AIエージェントの実装は構造的なコンサルティングを要する**」と反論するが、DeployCo 150名が現場に常駐して「設計不要のゼロ摩擦展開」を売りにする対比は鮮烈だ',
       ],
       'related': None},
      {'score': 82, 'time': '06:10', 'source': 'Anthropic',
       'title': 'AnthropicがWorkday・Intuit・LinkedIn等とエンタープライズAIサービス会社を設立',
       'url': 'https://www.anthropic.com/news/enterprise-ai-services-company',
       'thumb': None,
       'bullets': [
         '[[Anthropic]]はWorkday・Intuit・LinkedIn等の主要エンタープライズSaaSと共同出資で「**エンタープライズAIサービス会社**」を設立し、既存のSaaSワークフローにClaude Agentsを縫い込む統合サービスを提供開始',
         'これはKPMG・PwCとは別の補完軸——「**コンサルが届かない中堅企業層**」に対してSaaSベンダー経由でClaude展開を図るツーサイド戦略であり、AnthropicのGTMが多層化していることを示す',
         '__エコシステムの縦横展開__は単一サービスの改良ではなく、AI産業の「**産業インフラ化**」そのものを狙うAnthropicの野望の輪郭が一段と明確になってきた',
       ],
       'related': None},
      {'score': 77, 'time': '05:40', 'source': '日経クロステック',
       'title': '日本SIerが「御用聞き」脱却へ──NEC・富士通・NTTデータ・日立、コンサル転換の正念場',
       'url': 'https://xtech.nikkei.com/atcl/nxt/column/18/03330/091000001/',
       'thumb': 'https://xtech.nikkei.com/atcl/nxt/column/18/03330/091000001/topm.jpg?20220512',
       'bullets': [
         '[[NTTデータ]]・[[富士通]]・NEC・日立が「御用聞き型SI」から脱し、DX戦略を顧客と共に描く「**コンサルティング型SIer**」への転換を中期経営計画で掲げている',
         'アクセンチュアを追うための武器はコンサルタントの採用・育成と**AI駆動開発データフライホイール**で、特に富士通はAI自動化で従来比20倍の触媒発見速度を実証した実績が評価されている',
         '__「コンサルできるSIer」と「SIできるコンサル」の競争__は2026年が正念場で、国内市場でのブランド再構築に成功するかどうかは__OpenAI DeployCoの日本上陸まで__に間に合うかという時間軸に収斂しつつある',
       ],
       'related': None},
    ]
  },
  {
    'id': 'mobility', 'name_jp': 'モビリティ', 'name_en': 'Mobility', 'glyph': '◎',
    'accent': '#3A7B8C',
    'summary': 'WaymoがカバレッジをロードアイランドState超の<b>1,400平方マイル・11都市</b>に拡大し「ロボタクシーの本命」を実証する一方、TeslaのRoboTaxi実稼働は<b>20台</b>に縮小と格差が広がる。BYDの<b>記録的10%割引</b>にトヨタ・ホンダのCEOが「生き残れない」と危機感を表明、Honda-日産は対抗SDVプラットフォームで連合を組む。',
    'articles': [
      {'score': 93, 'time': '07:35', 'source': 'Electrek',
       'title': 'Waymo、カバレッジ20%超拡大で1,400平方マイル達成──11都市体制でロードアイランド州超え',
       'url': 'https://electrek.co/2026/05/13/waymo-expands-coverage-1400-square-miles-11-cities/',
       'thumb': 'https://i0.wp.com/electrek.co/wp-content/uploads/sites/3/2026/05/Waymo-market-expansion.jpeg?resize=1200%2C628&quality=82&strip=all&ssl=1',
       'bullets': [
         '[[Waymo]]は5月13日にサービスカバレッジを約**1,400平方マイル（ロードアイランド州超）**に拡大し、Phoenix周辺を含む11都市体制でロボタクシー週間**100万トリップ達成**を年内目標に据える',
         'Alphabet傘下が今年調達した**$160億**（バリュエーション$1260億）の大部分はMagna International製造拠点（アリゾナ州メサ）整備に充当され、年産**2,000台超**の拡大生産ラインが始動中',
         '__Waymoが「2026年はロボタクシー元年」という業界テーゼを単独で証明しつつある__一方、Tesla・Waymoという二強の格差が可視化され、自動運転市場の「勝者総取り」構造が鮮明になってきた',
       ],
       'related': None},
      {'score': 89, 'time': '07:05', 'source': 'CBT News',
       'title': 'WaymoマイアミローンチとNHTSA安全調査が同時進行──スクールバス問題で規制リスクが顕在化',
       'url': 'https://www.cbtnews.com/waymo-launches-miami-service-amid-safety-probe/',
       'thumb': 'https://d9s1543upwp3n.cloudfront.net/wp-content/uploads/2026/01/01252026-Waymo-Miami-scaled.jpg',
       'bullets': [
         '[[Waymo]]は1月にマイアミ商業ローンチ（Design District・Wynwood・Brickell・Coral Gables 60平方マイル）を行い、4月に**Miami Beach・I-95・Palmetto Expressway**へ高速道路展開を拡大した',
         '同時にNHTSAは[[Waymo]]がAustin・Atlantaで停止スクールバスを適切に認識・停車できなかった事案を調査中で、**商業展開加速と規制調査の並走**という前例のない局面に突入している',
         '__スクールバス対応不全は自動運転の「社会的な信頼構築」における最も敏感な論点__であり、万一の事故・規制措置が拡張ロードマップを遅延させるテールリスクとして投資家が注目し始めた',
       ],
       'related': {'axis': '関連', 'ref_title': 'Waymo May 13 1400sqmi拡張', 'ref_date': '2026-05-27', 'note': 'ロボタクシー競争の実力差がより鮮明に見えてくる。'}},
      {'score': 85, 'time': '06:35', 'source': 'Electrek',
       'title': 'Tesla Robotaxiフリートが20台に縮小──Waymoとの3,000台格差で「業界の本命」が問われる',
       'url': 'https://electrek.co/2026/05/26/tesla-robotaxi-fleet-shrinking-not-growing/',
       'thumb': 'https://i0.wp.com/electrek.co/wp-content/uploads/sites/3/2026/04/Tesla-Robotaxi-hero.webp?resize=1200%2C628&quality=82&strip=all&ssl=1',
       'bullets': [
         '[[Tesla]]の無監視ロボタクシー実稼働フリートは**20台**（Austin 14台・Dallas/Houston 各3台）に縮小——4月末の25台から減少し、Waymoの3,000台規模との格差が**150倍以上**に開いた',
         'マスクは「**FSD v15への書き直しが完成後に本格展開**」と発言し、タイムラインを2026年末〜2027年初頭に後退させた。安全性を優先した縮小判断は正しいが、**ビジネスケースへの打撃**は深刻だ',
         '__Tesla Robotaxiの「スケールしない現実」と「マスクの語るビジョン」の乖離__は投資家評価に影を落とし、EVの売上減少と重なることで__モビリティ戦略の再設計__が急務になっている',
       ],
       'related': None},
      {'score': 82, 'time': '06:05', 'source': 'Automotive World',
       'title': 'BYD値引き10%で「価格戦争に終わりなし」──トヨタ・ホンダCEOが「生き残れない」と異例の危機発言',
       'url': 'https://www.automotiveworld.com/news/chinas-ev-price-war-rages-on-as-byd-sets-record-discounts/',
       'thumb': 'https://media.automotiveworld.com/app/uploads/2026/02/10145310/byd-cars-in-dealership-lot-scaled.jpeg',
       'bullets': [
         '[[BYD]]の平均割引率が**3月に記録的10%**に達し、中国当局がコスト割れ禁止ルール（2月施行）を設けても価格戦争が止まらない——下限ルールの抜け穴が次々と発見されている',
         'Honda CEO 三部敏宏氏が「**このままでは勝ち目がない**」、Toyota CEO 佐藤恒治氏が「**業界が危機的状態**」と異口同音に危機感を表明——日系2強トップが同時期に公開の場で存続リスクを認めるのは異例',
         '__価格戦争の本質は「規模の経済×内製バッテリー×ソフトウェア定義車」という構造優位__にあり、日系が旧来の設計・調達思想を維持する限り格差は広がり続けるという__不可逆的な競争劣化__のシグナルだ',
       ],
       'related': None},
      {'score': 79, 'time': '05:35', 'source': 'AInvest',
       'title': 'Honda・日産がSDV共同プラットフォーム──BYD対抗でAI・OTA統合、2026年中に仕様確定へ',
       'url': 'https://www.ainvest.com/news/strategic-alliances-auto-industry-honda-nissan-mitsubishi-path-competing-chinese-ev-giants-2507/',
       'thumb': 'https://lh-prod-oper-pub-opercenter.s3.amazonaws.com/discovery-image/compress-19bd30b25e4bc001.png',
       'bullets': [
         '[[Honda]]・[[日産]]・三菱が共同で独自**SDV（ソフトウェア定義車両）プラットフォーム**を2026年中に仕様確定する計画で、OTA更新・AI機能統合・バッテリーイノベーションの共通基盤を構築する',
         'BYDのコスト優位を打破するには**単独ではスケールが足りない**という判断で、VWがXPeng・Mobileye等と中国テック連携を深める動きと並行し、日系も技術リソースの結集を迫られている',
         '__3社連合がBYD対比でどこまでコストを削れるか__は不透明だが、「単独戦略から同盟戦略へ」の転換は__中国EV支配という構造変化__に日系が初めて本格的に向き合ったサインとして注目される',
       ],
       'related': None},
    ]
  },
  {
    'id': 'economy', 'name_jp': '経済', 'name_en': 'Economy', 'glyph': '■',
    'accent': '#8E2A19',
    'summary': '日経平均が<b>65,800円</b>に迫る史上最高値更新連続で始まった本日は、21:30JSTに米4月PCEとQ1 GDP第2次推計が同時公開される正念場。S&amp;P500の7,500台定着を懸けた分岐点で、タカ派インフレなら<b>利上げ観測再浮上</b>、ハト派なら強気継続——今夜の数字が相場シナリオを塗り替える。',
    'articles': [
      {'score': 95, 'time': '07:50', 'source': '日本経済新聞',
       'title': '日経平均65,800突破・最高値更新連続──AI半導体主導のウォール街追随、PCE/GDP待ちで様子見も',
       'url': 'https://www.nikkei.com/article/DGXZQOFL074420X00C26A5000000/',
       'thumb': 'https://www.nikkei.com/.resources/k-components/rectangle.rev-d54ea30.png',
       'bullets': [
         '日経平均は**65,800円台**（前日比+1.3%）で水曜大引けを迎え、AI・半導体関連が連日の上昇をけん引——5月第4週の過去最大規模上昇（5/26+2,000円超）に続く連続最高値更新',
         '東証大引けの主役は[[NVIDIA]]決算好調を追い風に動く半導体関連と[[ソフトバンク]]の+8%超で、外国人投資家の買い継続と円安効果が押し上げ要因として機能している',
         '__本日21:30JSTに米PCEとGDPが同時公開されるため、後場は様子見ムードが台頭__——タカ派データなら翌日の調整圧力、ハト派なら7万円台シナリオの現実味が増すという**二択の瀬戸際**に立つ',
       ],
       'related': None},
      {'score': 90, 'time': '07:15', 'source': 'Investing.com',
       'title': '米4月PCE・GDP第2次推計、本日8:30ET同時公開──高インフレ継続なら利下げ遠のき利上げ観測再浮上',
       'url': 'https://www.investing.com/analysis/upcoming-market-dates-pce-inflation-and-q1-gdp-determining-the-feds-next-move-200679249',
       'thumb': 'https://i-invdn-com.investing.com/redesign/images/seo/investingcom_analysis_og.jpg',
       'bullets': [
         '本日8:30ET（21:30JST）に[[米4月PCE]]コア価格指数と[[Q1 GDP]]第2次推計が同時公表——前回3月PCEコア**3.2%**から上振れ確認なら年内利下げシナリオは事実上崩壊し、**「利上げ再開」への警戒**が一段と高まる',
         'GDP第2次推計は第1次推計の**2.0%成長**を維持・小幅修正の見込みで、重要度はPCEデフレーターと**コーポレートプロフィット（初公開）**——前者はインフレの温度計、後者は株式市場の高値正当化材料となる',
         '__4月CPI 3.8%（予想3.7%上振れ）・PPIも強い流れを引き継いでのPCEは高インフレ確認の可能性大__で、市場が最も恐れるのは「成長2%+インフレ4.5%=スタグフレーション型の利上げ」という相場に最悪のシナリオだ',
       ],
       'related': None},
      {'score': 87, 'time': '06:45', 'source': '外為どっとコム',
       'title': 'S&P500 7,500台定着を問うPCE関門──タカ派インフレなら7,350調整、ハト派なら7,600試す',
       'url': 'https://www.gaitame.com/media/entry/2026/05/22/125152',
       'thumb': 'https://cdn.image.st-hatena.com/image/scale/ab2edb32bf8bda02830591af2e2dd993254c3de6/backend=imagemagick;version=1;width=1300/https%3A%2F%2Fcdn-ak.f.st-hatena.com%2Fimages%2Ffotolife%2Fg%2Fgaitamesk%2F20251107%2F20251107140021.jpg',
       'bullets': [
         '[[S&amp;P500]]は7,350〜7,520のボックス圏でもみ合っており、本日PCEが落ち着いた内容（コア3.0%以下）なら**7,500台定着トライ**、上振れなら**7,350方向**への調整リスクが高まる',
         '市場参加者の中で年内利下げを織り込む者は極めて少数で、**3割超が1回以上の利上げ**を見込む——この分布がPCE数字一本で大きく変わる不安定なセンチメントが現状だ',
         '__5月末持高調整とFOMO（乗り遅れ恐怖）が交差する局面__で、PCEが予想内に収まれば「懸念より喜びが優先」のリリーフラリーが起きやすく、夏の利確前の最後のエントリーとなる可能性もある',
       ],
       'related': None},
      {'score': 83, 'time': '06:15', 'source': '野村ウェルスタイル',
       'title': '野村證券、S&P500 7,500・日経平均6.3万目標維持──AI強気継続、上振れで7万円台突破へ',
       'url': 'https://www.nomura.co.jp/wealthstyle/article/0724/',
       'thumb': 'https://www.nomura.co.jp/wealthstyle/article/0724/images/og_a_0724_01.png',
       'bullets': [
         '[[野村證券]]は2026年末S&amp;P500**7,500**・日経平均**6.3万**をメインシナリオとして維持、上振れシナリオでは日経平均**7万円台突破**も視野に入れた強気見通しを発表',
         'イラン情勢収束・AI需要拡大・企業業績の好調が3本柱で、特にAI/半導体主導の**増益率+27%超（Q1 S&amp;P500）**という事実が高バリュエーションを正当化している',
         '__「年末63,000円メインに対する年末70,000円超シナリオ」は現実的に射程に入りつつある__が、それが実現するかどうかの分水嶺は本日のPCE数字と[[FRB]]の6月会合での第一声にかかっている',
       ],
       'related': None},
      {'score': 79, 'time': '05:45', 'source': '株式ポートフォリオ',
       'title': '米Q1企業利益+27%・EPS84%ポジサプライズ──4年ぶり好決算が高値バリュエーションを正当化',
       'url': 'https://kabukiso.com/america/outlook/2026/may.html',
       'thumb': 'https://kabukiso.com/common/img/ic/ico_twitter_security.png',
       'bullets': [
         '2026年Q1 S&amp;P500のEPSは前年同期比**+27%**と4年ぶりの高水準で、アナリスト予測+20%を大幅上振れ——構成企業の**84%**がポジティブサプライズを記録した',
         'NVIDIAとMicrosoftが牽引するAI・半導体・クラウド主導の好決算で、コンセンサス上方修正銘柄比率は**過去最高の75%**——「業績の天井が見えない」という強気論を数字が裏付けた形',
         '__日本株でも2027年3月期の強気予想が高値を正当化する理論が定着しつつあり__、外国人投資家の円安恩恵ダブル効果と合わさって「日経高値論」の循環論が強化される——__ただしPCE次第でこの好循環が一晩で反転するリスク__も同居している',
       ],
       'related': None},
    ]
  },
]

reflection = {
  'title': 'インフレの踊り場とAI資本戦争',
  'subtitle': 'PCE高止まりが市場の分水嶺となる日、AnthropicはBig4を取り込みエコシステムの覇権を狙う — 2026年5月28日（水）',
  'lead': '本日5分野・25本のニュースから浮かびあがる最大のテーマは [[高インフレの踊り場]] と [[Anthropicの資本攻勢]] が同日に収斂したことだ。米4月PCEとGDP第2次推計が21:30JSTに公表され、タカ派ならドル高・株安の反転、ハト派なら日経7万円台シナリオの現実化と**180度違う翌朝**を迎える可能性がある。同時にAnthropicはSequoia主導$300億調達・KPMG同盟・Claude for Small Business・Stainless買収と四連打を決め、__AIのエコシステム支配を一気に固める史上最大級の週__を演出した。',
  'pull_quote': '「AIの覇権は製品の優秀さではなく、**エコシステムでの配布経路**を誰が握るかで決まる」——Stainless買収が露わにした法則。',
  'sections': [
    {'tag': '総論', 'heading': 'インフレとAIが同日に分岐点を迎える', 'accent': '#1A1A1A',
     'body': '本日の[[PCE]]・[[GDP]]・[[Anthropic資本攻勢]]という三本の軸が2026年5月28日に収斂した。インフレは**高止まりか鎮静化か**の答えが今夜出る。AIは**エコシステムの主導権争いが資本・M&A・アライアンス**という三次元で同時展開された。モビリティでは__Waymo vs Tesla の実力差が公式化__し、BYD価格戦争が日系OEMの生存を問い直す。この三つが交差する水曜日は、年後半の相場観を決める分水嶺になる可能性がある。'},
    {'tag': '為替', 'heading': 'PCE公開前夜のドル円は「待機即応型」', 'accent': '#B8860B',
     'body': '[[ウォーシュ議長]]の「双方向リスク」宣言と[[Q1 PCEデフレーター4.5%]]という高水準が、市場の**利下げ期待を崩壊**寸前に追い込んでいる。USD/JPYは158円後半で膠着しているが、本日21:30JSTのPCEが3.5%超を示せば**159台後半→160円**接近シナリオが再浮上する。__日米金利差6.5%という構造が変わらない限り、介入は時間稼ぎに過ぎない__——この見立てが正しければ今夜のPCEが今年最大の円安トリガーになりうる。'},
    {'tag': 'AI', 'heading': 'Anthropicの四連打が示すエコシステム支配の法則', 'accent': '#2D5BB8',
     'body': '**$300億調達（Sequoia主導）・Stainless買収・KPMG同盟・Claude for Small Business**——Anthropicがこれだけの施策を一週間に集中させた事実は、「AIの競争は製品改良からインフラ掌握フェーズへ移行した」という宣言だ。[[Stainless]]はAPIの入り口、[[KPMG]]は27.6万人のプロフェッショナル、Small Businessは米国GDP44%——__資金・インフラ・企業・消費者の4層を同時に押さえる戦略は前例がない__。競合他社は「個別製品で勝つ」から「生態系ごと差をつけられる」時代への対応を迫られた。'},
    {'tag': 'IT', 'heading': 'Big 4のAI軸足がAnthropicに傾く', 'accent': '#2E6B52',
     'body': '[[KPMG]]と[[PwC]]が同週にAnthropicとの深化契約を締結したことで、Big 4の2社が**Claude基盤型**のサービス設計に舵を切った。残るDeloitte・EYの動向次第で「Big 4のClaude依存度」という新指標が業界の競争軸になる可能性がある。一方[[OpenAI DeployCo]]はFDE 150名でコンサルの現場に直接踏み込み、__AIと既存コンサルの「協調か競争か」の決着__は2026年後半に持ち越された。日本SIerはこの国際競争に間に合うかどうかの**時間リスク**を認識せねばならない。'},
    {'tag': 'モビリティ', 'heading': '自動運転の「実力差」が公式化した週', 'accent': '#3A7B8C',
     'body': '[[Waymo]] 3,000台 vs [[Tesla]] 20台——この格差は**技術思想の違い**（ライダー+HD地図 vs カメラのみ）がそのまま規模に反映された結果だ。マスクは「FSD v15完成後に展開」と後退を認めており、__TeslaのEV販売鈍化とRoboTaxiの縮小が重なった2026年前半__はブランドへの信頼毀損期間として記録されるかもしれない。BYD側では10%割引が法規制をすり抜けて継続し、ホンダ・トヨタCEOの危機発言という**日系首脳の公開降参**に近いシグナルが出た。'},
    {'tag': '経済', 'heading': '日経65,800、PCE次第で7万円か調整か', 'accent': '#8E2A19',
     'body': '[[日経平均]]65,800円台は前週比+5%超の驚異的な上昇ペースで、AI/半導体主導のウォール街高を追随する外国人買いと円安ダブル効果が原動力だ。[[S&amp;P500]] Q1 EPS +27%・アナリスト予想超過84%という決算水準は**高バリュエーションの理論的根拠**となっているが、PCEが上振れすれば「業績は良いが利上げ再考」というスタグフレーション的懸念が一瞬で相場観を逆転させる。__野村の年末63,000・上振れ70,000シナリオは今夜の数字で確定か覆るかの分岐点__にある。'},
    {'tag': 'ゲーム', 'heading': 'ゲーム枠は本日（水曜）休載', 'accent': '#5E3D8C',
     'body': 'ゲーム関連ニュースは本日（水曜）の対象外。次回木曜（2026-05-29）にGame枠を再開。Nintendo Switch 2値上げ後市場の動向、SIE PlayStation State of Play（6月2日）の続報は翌日号を参照。'},
    {'tag': '明日へ', 'heading': 'PCE結果が相場のシナリオ表を塗り替える', 'accent': '#C9B98A',
     'body': '今夜21:30JSTの[[PCE]]発表後、世界市場は「利下げ→リスクオン継続」か「高インフレ→利上げ再考→調整」かの二択を突きつけられる。どちらに転んでもAnthropicの資本攻勢は止まらず、Waymoのロボタクシー展開もBYDの価格戦争も独自の論理で進む。__金融市場の短期変動とAI・モビリティの構造変化が完全に切り離されて動く時代__——明日の朝刊は「PCE後の世界」をどう描くかが問われる。'},
  ],
  'takeaways': [
    {'num': '01', 'tag': '為替', 'color': '#B8860B',
     'text': '本日21:30JSTの[[米PCE]]が利下げか利上げかの分水嶺。3.8%CPI追認なら**USD/JPY 160円**接近、予想内収束なら**ドル売り転換**の好機'},
    {'num': '02', 'tag': 'AI', 'color': '#2D5BB8',
     'text': '[[Anthropic]]の$300億調達・Stainless買収・KPMG同盟・Small Businessの四連打は「**資金・インフラ・企業・消費者**」の全4層を一週間で押さえた史上初の攻勢'},
    {'num': '03', 'tag': 'モビリティ', 'color': '#3A7B8C',
     'text': '[[Waymo]] 3,000台 vs [[Tesla]] 20台——自動運転の**実力差150倍超**が確定し、BYDの10%値引きで日系OEMの生存戦略が問い直される週'},
  ],
  'related': [
    {'date': '2026-05-27', 'title': '前号: Warsh体制初週・AI統合フェーズ到来'},
    {'date': '2026-05-26', 'title': '日経65,000突破・Anthropic Dreaming正式リリース'},
    {'date': '2026-05-22', 'title': 'Switch 2値上げ前夜・FRB「双方向リスク」浮上'},
  ]
}

# ═══════════════════════════════════════════════════════════════════════════════
# RENDERING
# ═══════════════════════════════════════════════════════════════════════════════
toc_rows = ''
for i, c in enumerate(cats, 1):
    toc_rows += toc_row(i, c['id'], c['name_jp'], c['name_en'], c['glyph'], c['accent'], len(c['articles']))

cats_html = ''
for i, c in enumerate(cats, 1):
    cats_html += cat_header(i, len(cats), c['id'], c['name_jp'], c['name_en'],
                            c['glyph'], c['accent'], len(c['articles']), c['summary'])
    for j, art in enumerate(c['articles']):
        if j == 0:
            cats_html += card_featured(art, c['id'], c['accent'], 'TOP')
        else:
            cats_html += card_side(art, c['id'], c['accent'], j + 1)

sections_html = ''
for i, s in enumerate(reflection['sections'], 1):
    sections_html += section_row(i, s['tag'], s['heading'], s['body'], s['accent'])

takeaways_html = ''
for t in reflection['takeaways']:
    takeaways_html += takeaway_row(t['num'], t['tag'], t['color'], t['text'])

related_html = ''
for r in reflection['related']:
    related_html += related_row(r['date'], r['title'])

with open('prompts/email-template.html', encoding='utf-8') as f:
    tmpl = f.read()

total_stories = sum(len(c['articles']) for c in cats)
html = tmpl
html = html.replace('{{ISSUE_NO}}', ISSUE_NO)
html = html.replace('{{ISSUE_DATE}}', ISSUE_DATE)
html = html.replace('{{ISSUE_WEEKDAY}}', WEEKDAY)
html = html.replace('{{ISSUE_WEB_URL}}', ISSUE_WEB_URL)
html = html.replace('{{TOTAL_CATEGORIES}}', str(len(cats)))
html = html.replace('{{TOTAL_STORIES}}', str(total_stories))
html = html.replace('{{TOTAL_SECTIONS}}', str(len(reflection['sections'])))
html = html.replace('{{TOC_ROWS_HTML}}', toc_rows)
html = html.replace('{{CATEGORIES_HTML}}', cats_html)
html = html.replace('{{REFLECTION_TITLE}}', reflection['title'])
html = html.replace('{{REFLECTION_SUBTITLE}}', reflection['subtitle'])
html = html.replace('{{REFLECTION_LEAD_HTML}}', render_lead(reflection['lead']))
html = html.replace('{{REFLECTION_PULL_QUOTE_HTML}}', render_pullquote(reflection['pull_quote']))
html = html.replace('{{REFLECTION_SECTIONS_HTML}}', sections_html)
html = html.replace('{{TAKEAWAYS_HTML}}', takeaways_html)
html = html.replace('{{RELATED_ISSUES_HTML}}', related_html)

with open('build/email.html', 'w', encoding='utf-8') as f:
    f.write(html)

size_kb = len(html.encode('utf-8')) / 1024
print(f'✅ 生成完了: build/email.html ({size_kb:.1f} KB) — {total_stories} stories / {len(cats)} categories')
