"""2026-05-18 号 HTML メール生成スクリプト"""
import re, sys, os
sys.stdout.reconfigure(encoding='utf-8')
os.chdir(os.path.dirname(os.path.abspath(__file__)) + '/..')

ISSUE_DATE = '2026-05-18'
ISSUE_NO   = '20260518'
WEEKDAY    = '日'
CDN        = 'https://raw.githubusercontent.com/HIDEPON-UMG/news-grasp-assets/main'

# ── 強調マーカー変換 ──────────────────────────────────────────────────────────
def render_bullets(bullets: list, accent: str) -> str:
    """bullets リストを HTML <div class="bul"> 列に変換。"""
    bg = accent + '22'  # 半透明背景
    out = []
    for b in bullets:
        # [[keyword]] → bold + accent bg
        b = re.sub(r'\[\[(.+?)\]\]',
            f'<strong style="background:{bg};padding:1px 3px;border-radius:2px;">\\1</strong>', b)
        # __text__ → underline
        b = re.sub(r'__(.+?)__',
            '<span style="border-bottom:2px solid ' + accent + ';padding-bottom:1px;">\\1</span>', b)
        out.append(f'<div class="bul ng-card-body" style="color:{accent}">'
                   f'<span class="dk">{b}</span></div>')
    return '\n'.join(out)

def render_lead(text: str) -> str:
    text = re.sub(r'\[\[(.+?)\]\]',
        '<strong style="background:#C9B98A33;padding:1px 3px;border-radius:2px;">\\1</strong>', text)
    text = re.sub(r'__(.+?)__',
        '<span style="border-bottom:2px solid #C9B98A;padding-bottom:1px;">\\1</span>', text)
    return text

def render_pullquote(text: str) -> str:
    text = re.sub(r'\[\[(.+?)\]\]',
        '<strong style="background:#C9B98A33;padding:1px 3px;">\\1</strong>', text)
    text = re.sub(r'__(.+?)__',
        '<span style="border-bottom:2px solid #8E2A19;padding-bottom:1px;">\\1</span>', text)
    return text

def render_section_body(text: str) -> str:
    text = re.sub(r'\[\[(.+?)\]\]',
        '<strong style="background:#C9B98A33;padding:1px 3px;border-radius:2px;">\\1</strong>', text)
    text = re.sub(r'__(.+?)__',
        '<span style="border-bottom:2px solid #C9B98A;padding-bottom:1px;">\\1</span>', text)
    return text

def thumb_src(thumb, cat):
    if thumb:
        return thumb
    return f'{CDN}/ng-thumb-common-{cat}.jpg'

def thumb_featured(thumb, cat):
    if thumb:
        return thumb
    return f'{CDN}/ng-thumb-{cat}.jpg'

# ── TOC 行 ────────────────────────────────────────────────────────────────────
def toc_row(idx, cat_id, name_jp, name_en, glyph, accent, n):
    return f'''<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:8px;"><tbody><tr>
  <td width="32" class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:12px;color:{accent};font-weight:700;">{glyph}</td>
  <td style="font-size:14px;font-weight:700;">{idx}. {name_jp} <span style="color:#5C5A52;font-weight:400;font-size:12px;">({name_en})</span></td>
  <td align="right" class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:11px;color:#5C5A52;">{n} stories</td>
</tr></tbody></table>'''

# ── カテゴリ帯 ────────────────────────────────────────────────────────────────
def cat_header(idx, total, cat_id, name_jp, name_en, glyph, accent, n, summary):
    en_upper = name_en.upper()
    return f'''<tr><td class="ng-cat-pad" style="background:{accent};padding:20px 36px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tbody><tr>
    <td style="vertical-align:middle;">
      <div class="m ng-cat-name" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:10px;color:rgba(255,255,255,0.7);letter-spacing:2px;margin-bottom:4px;">
        CATEGORY {idx} / {total} · {en_upper}
      </div>
      <div style="font-size:28px;font-weight:800;color:#fff;letter-spacing:-0.5px;line-height:1.1;">
        <span style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;margin-right:10px;">{glyph}</span>{name_jp}
      </div>
    </td>
    <td align="right" style="vertical-align:middle;color:rgba(255,255,255,0.85);font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:11px;">{n} stories</td>
  </tr></tbody></table>
  <div class="ng-cat-summary" style="font-size:13px;color:rgba(255,255,255,0.95);font-style:italic;line-height:1.6;margin-top:10px;padding-top:10px;border-top:1px solid rgba(255,255,255,0.25);">
    {summary}
  </div>
</td></tr>'''

# ── FEATURED 記事カード（TOP） ────────────────────────────────────────────────
def card_featured(art, cat_id, accent, rank_label):
    bg = accent + '22'
    src = thumb_featured(art['thumb'], cat_id)
    bullets_html = render_bullets(art['bullets'], accent)
    title_safe = art['title'].replace('&', '&amp;')
    rel = ''
    if art.get('related'):
        r = art['related']
        rel = f'''<div style="margin-top:16px;padding:10px 14px;background:#F2EEE3;border-left:3px solid {accent};font-size:12px;color:#5C5A52;">
      <span class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-weight:700;color:{accent};">↩ 関連: {r["axis"]}</span>
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

# ── サイドサムネ記事カード ─────────────────────────────────────────────────────
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

# ── セクション行 ──────────────────────────────────────────────────────────────
def section_row(num, tag, heading, body_text, accent):
    body_html = render_section_body(body_text)
    return f'''<tr><td class="ng-section-pad" style="background:#FAF7F0;padding:0 36px;">
  <table width="100%" style="border-bottom:1px dashed #E2DED4;"><tbody><tr>
    <td class="ng-section-num-cell" width="80" valign="top" style="padding:28px 16px 28px 0;vertical-align:top;">
      <div class="m ng-section-num" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:38px;font-weight:900;color:{accent};line-height:0.9;letter-spacing:-2px;">§{num:02d}</div>
      <div class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:9px;color:#fff;background:{accent};padding:2px 6px;display:inline-block;letter-spacing:1.5px;margin-top:8px;">{tag}</div>
    </td>
    <td class="ng-section-text-cell" valign="top" style="padding:28px 0 28px 20px;border-left:1px solid #E2DED4;vertical-align:top;">
      <h3 class="ng-section-heading" style="font-size:18px;font-weight:800;margin:0 0 14px;">{heading}</h3>
      <div class="ng-section-body" style="font-size:13.5px;line-height:2.0;">{body_html}</div>
    </td>
  </tr></tbody></table>
</td></tr>'''

# ── TAKEAWAY 行 ───────────────────────────────────────────────────────────────
def takeaway_row(num, tag, color, text):
    text_html = re.sub(r'\[\[(.+?)\]\]',
        f'<strong style="background:{color}22;padding:1px 2px;">\\1</strong>', text)
    text_html = re.sub(r'__(.+?)__',
        f'<span style="border-bottom:2px solid {color};">\\1</span>', text_html)
    return f'''<tr><td style="padding-bottom:12px;">
  <table width="100%" style="background:#fff;border:1px solid #E2DED4;"><tbody><tr>
    <td width="56" valign="middle" class="m" style="background:{color};color:#fff;text-align:center;font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:18px;font-weight:900;padding:14px 0;">{num}</td>
    <td style="padding:12px 16px;">
      <div class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:9px;color:{color};font-weight:700;letter-spacing:1.5px;margin-bottom:4px;">{tag.upper()}</div>
      <div style="font-size:13px;line-height:1.7;font-weight:600;">{text_html}</div>
    </td>
  </tr></tbody></table>
</td></tr>'''

# ── 関連過去号行 ──────────────────────────────────────────────────────────────
def related_row(date_str, title, url='#'):
    return f'''<tr><td style="padding:10px 0;border-bottom:1px solid #E2DED4;">
  <table width="100%"><tbody><tr>
    <td width="100" class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:11px;color:#5C5A52;">{date_str}</td>
    <td style="font-size:13px;font-weight:600;"><a href="{url}" style="color:#1A1A1A;text-decoration:none;">{title}</a></td>
    <td width="20" align="right" style="color:#5C5A52;">→</td>
  </tr></tbody></table>
</td></tr>'''

# ═══════════════════════════════════════════════════════════════════════════════
# DATA
# ═══════════════════════════════════════════════════════════════════════════════

cats = [
  {
    'id': 'fx', 'name_jp': '為替', 'name_en': 'Foreign Exchange', 'glyph': '¥',
    'accent': '#B8860B',
    'summary': 'USD/JPY 158円台で週明けへ — BOJ利上げ確率77%急騰と介入効果剥落が交錯、Google I/O週に決定的な水準攻防へ',
    'articles': [
      {'score':92,'time':'08:15','source':'TradingKey',
       'title':'BOJ利上げ確率77%に急騰 — 円安圧力の中でも次の一手を市場が先読み',
       'url':'https://www.tradingkey.com/analysis/economic/central-banks/261884189-boj-rate-hike-yen-weakness-pricing-divergence-oil-geopolitics-intervention-tradingkey',
       'thumb':None,
       'bullets':['市場の[[BOJ]] 6月利上げ確率が[[77%]]まで急騰。円安が続くなかでも「利上げが近い」と先読みする勢力が増加し、__スワップ市場に明確なプライシングが生まれた__。',
                  '現行政策金利は[[0.75%]]。3名の審議委員が1.0%への利上げを主張しており、次回会合での追加引き締めが現実的な選択肢として浮上している。',
                  '利上げ実施なら円高圧力が加速するが、__USD/JPYの水準がすでに158円台と高く、政策効果の先取りがどこまで進むか__が市場の焦点。'],
       'related':None},
      {'score':88,'time':'07:30','source':'CNBC',
       'title':'介入効果の半減と再警戒 — USD/JPY 158円台回帰でMoF再出動を市場が催促',
       'url':'https://www.cnbc.com/2026/05/07/japan-yen-intervention-boj-rate-gap-currency-pressure.html',
       'thumb':None,
       'bullets':['財務省・日銀による[[$637億]]の円買い介入効果が半減し、USD/JPYが再び[[158円台]]に接近。4月30日〜5月1日の介入で141円台まで下げたが揺り戻しが進んだ。',
                  '__159〜161円圏は2024年夏に$1,000億規模の介入を行ったゾーン__。市場は同水準で財務省が再出動すると見ており、緊張感が高まる。',
                  '米財務長官[[ベッセント]]が日本の介入を「支持」と発言したことで、G7協調の文脈が意識されているが、効果の持続性については懐疑的な声も根強い。'],
       'related':{'axis':'復状','ref_title':'USD/JPY 158円台で週を終える','ref_date':'2026-05-17','note':'前日号で4日続落と報じた介入後の揺り戻しが引き続き進行。'}},
      {'score':85,'time':'06:45','source':'Bloomberg',
       'title':'ECBの6月利上げ観測に暗雲 — インフレ鈍化でEUR/USD 1.1733高止まり',
       'url':'https://www.bloomberg.com/news/articles/2026-05-14/why-the-ecb-s-june-interest-rate-hike-is-becoming-less-certain',
       'thumb':None,
       'bullets':['[[ECB]]の6月利上げ確率が一時86%に達したが、エネルギー価格の安定化とユーロ圏の景気停滞を背景に当局者が慎重姿勢に転換。__「利上げか据え置きか」の二択が再び浮上している__。',
                  'EUR/USDは[[1.1733]]で高止まりしており、利上げ見送りなら1.15方向へ調整リスク。逆に実施なら1.19〜1.20を目指す展開も。',
                  '次回ECB理事会は[[6月11日]]。インフレ第二波の深刻度とドル高の持続が判断材料となる。'],
       'related':None},
      {'score':80,'time':'10:00','source':'FXStreet',
       'title':'週明け為替展望 — ドル全面高のなか158-160円攻防、Google I/O週に突入',
       'url':'https://www.fxstreet.com/analysis/weekly-forex-forecast-eur-usd-xau-usd-gbp-usd-usd-jpy-bitcoin-and-more-video-202605111410',
       'thumb':None,
       'bullets':['USD/JPYが[[158〜160円]]の攻防圏に突入。週明けは[[Google I/O 2026]]開幕（5月19日）に加え、米経済指標発表が相次ぐため、ドル高トレンドの継続可否が問われる週となる。',
                  'GBP/USDは1.35台を維持しているが、__ドル全面高局面では英ポンドが最初に崩れる通貨__として注目が集まる。英BOEは当面4.75%を維持する見通し。',
                  'EUR/JPY・GBP/JPYはドル独歩高よりも円売りの側面が主導。__クロス円全般が上昇圧力下__にあり、介入ラインの160円超えが現実味を帯びてきた。'],
       'related':None},
      {'score':75,'time':'09:00','source':'NBC Economics',
       'title':'AUD/USD底堅さの背景 — RBA利下げ観測後退とコモディティ需要で下支え',
       'url':'https://www.nbc.ca/content/dam/bnc/taux-analyses/analyse-eco/mensuel/forex.pdf',
       'thumb':None,
       'bullets':['[[AUD/USD]]はドル全面高のなかでも底堅く推移。背景には[[RBA]]の利下げ観測後退と、鉄鉱石・銅などのコモディティ価格の下支え。',
                  '__豪中金利差の縮小が為替を押し上げる構図__はFX投資家にとって注目点。RBAは政策金利を現行水準で維持する見通し。',
                  'ドル高圧力が続く局面でも相対的に強い豪ドルは、新興国通貨からの資金流入とともに「質への逃避」先としても機能している。'],
       'related':None},
    ]
  },
  {
    'id': 'ai', 'name_jp': 'AI', 'name_en': 'Artificial Intelligence', 'glyph': '◆',
    'accent': '#2D5BB8',
    'summary': 'AnthropicがゲイツとAI公共財へ — OpenAI超アプリ化・Google I/O前夜・中国LLM台頭が同時並行し、週明けはAI大再編の一手が出揃う',
    'articles': [
      {'score':93,'time':'08:00','source':'Anthropic News',
       'title':'Anthropic×ゲイツ財団2億ドル提携 — 医療・教育・農業でClaudeが公共財に',
       'url':'https://www.anthropic.com/news/gates-foundation-partnership',
       'thumb':None,
       'bullets':['[[Anthropic]]と[[ゲイツ財団]]が[[2億ドル]]の提携を締結（5月14日発表）。助成金・Claude利用クレジット・技術支援を4年間提供し、__グローバルヘルス・教育・農業の3領域でAIを「公共財」へ転換する__。',
                  '医療分野では46億人が基礎的医療サービスを受けられない低中所得国を対象に、ポリオ・HPV・妊娠合併症の研究にClaudeを活用。アフリカ語データセットは公開財として提供予定。',
                  '農業分野では[[20億人]]の小農民の生産性向上を目指しClaude農業特化版を開発。教育分野でも米国K-12・サブサハラ・インドでAI個別指導を展開する。'],
       'related':{'axis':'波及','ref_title':'Anthropic Claude for Small Business','ref_date':'2026-05-17','note':'中小企業向けから国際公共機関向けへとAnthropicの事業射程が拡大する動き。'}},
      {'score':90,'time':'06:30','source':'TechTimes',
       'title':'OpenAI、ChatGPT×Codex×Atlasを統合スーパーアプリ化 — IPO前夜の組織再編',
       'url':'https://www.techtimes.com/articles/316730/20260516/openai-unifies-chatgpt-codex-developer-api-under-co-founder-brockman-four-days-before-google-i-o.htm',
       'thumb':None,
       'bullets':['[[OpenAI]]がChatGPT・Codex・APIを単一製品組織に統合し、[[グレッグ・ブロックマン]]がプロダクト戦略のトップに復帰。__「会話・コード生成・ブラウジングを1つのアプリで」を目指すスーパーアプリ計画__が動き出した。',
                  'Codexの年間収益は[[10億ドル超]]。統合後のスーパーアプリはChatGPT×Codex×Atlasブラウザを包含し、マルチステップタスクを自律実行するAIエージェントプラットフォームへ進化する。',
                  '発表はGoogle I/O 2026の[[4日前]]という戦略的タイミング。IPO評価額8,520億ドルを目標にQ4上場を視野に入れており、製品統合は投資家向けシグナル発信でもある。'],
       'related':None},
      {'score':88,'time':'07:00','source':'Android Authority',
       'title':'Google I/O 2026前夜 — Gemini Spark・Android 17・XRグラスが明日解禁',
       'url':'https://www.androidauthority.com/what-to-expect-from-google-io-2026-3664979/',
       'thumb':None,
       'bullets':['Google I/O 2026が[[5月19日]]に開幕。メインキーノートでは[[Gemini Spark]]（マルチステップ自律エージェント）・Android 17・XRグラスの3本柱が発表される見込み。',
                  'Gemini Sparkはメール管理・ショッピング予約などを人間の介入なしに自律実行するエージェントで、__「人間が常にループに残る」設計__がプライバシー懸念への回答となる。',
                  'DeepMindチームはClaude CodeとOpenAI Codexに対抗するコーディング支援を強化中。[[Android 17]]はGemini Intelligence統合で時計・車・XRグラス横断のマルチデバイス体験を提供する。'],
       'related':None},
      {'score':82,'time':'09:30','source':'TradingKey',
       'title':'OpenAI年間収益250億ドル突破・IPO本格化 — 評価額8,520億ドルでQ4上場へ',
       'url':'https://www.tradingkey.com/analysis/stocks/us-stocks/261902715-openai-ipo-chatgpt-codex-api-ai-agent-brockman-tradingkey',
       'thumb':None,
       'bullets':['[[OpenAI]]が年間収益[[250億ドル]]超を達成。2024年の$37億から約7倍に拡大し、AIソフトウェア企業として史上最速の成長曲線を描く。',
                  'Q4 2026年中のIPOを目指し評価額[[8,520億ドル]]を設定。構造改革（非営利→PBC）とChatGPT/Codex統合が投資家向けシグナル。',
                  '__GPT-5.5の稼働が収益の主要ドライバー__となっており、エンタープライズAPIと個人サブスクが双輪で成長中。Anthropicの評価額6,000億ドルを大きく上回る。'],
       'related':{'axis':'対立','ref_title':'xAI解散→AnthropicへコンピュートW移管','ref_date':'2026-05-17','note':'業界二強のOpenAI・Anthropicに対し規模・資金面で格差が拡大している。'}},
      {'score':78,'time':'11:00','source':'Fox21Online',
       'title':'Z.ai、開発現場向けLLM「GLM-4.7」をオープンソース公開 — Claude対抗で中国勢が巻き返し',
       'url':'https://www.fox21online.com/i/z-ai%E3%80%81%E7%8F%BE%E5%A0%B4%E3%81%A7%E3%81%AE%E9%96%8B%E7%99%BA%E5%90%91%E3%81%91%E3%81%AB%E8%A8%AD%E8%A8%88%E3%81%95%E3%82%8C%E3%81%9F%E6%96%B0%E4%B8%96%E4%BB%A3%E3%81%AE%E5%A4%A7%E8%A6%8F/',
       'thumb':None,
       'bullets':['[[Z.ai]]（旧 Zhipu AI）が[[GLM-4_7]]をオープンソース公開。現場開発向けに設計された次世代LLMで、ツール呼び出し・長文コンテキスト処理に特化している。',
                  'Claude Code・Codexへの対抗として中国勢が欧米市場に参入。__オープンソース戦略で開発者コミュニティを取り込む__動きはMeta LLaMAと同じ路線。',
                  '米国の輸出規制にもかかわらず中国LLMの品質は向上を続けており、[[Claude Mythos]]や[[GPT-5_5]]との性能差はコード・推論タスクで縮まりつつある。'],
       'related':None},
    ]
  },
  {
    'id': 'it', 'name_jp': 'IT-Consulting', 'name_en': 'IT & Consulting', 'glyph': '▲',
    'accent': '#2E6B52',
    'summary': 'デロイトが職名廃止・BCGがAI収益初開示 — Big4/3のAI再編が同時進行し、コンサル業界の「知識階層」が根底から書き換わる日曜日',
    'articles': [
      {'score':92,'time':'08:30','source':'Fortune',
       'title':'Deloitte、6月から181,500人の職名を全廃 — AIが削ったピラミッドに「リーダー職」を新設',
       'url':'https://fortune.com/2026/01/22/deloitte-job-title-change-ai-reshapes-big-4-accounting-consulting-firms/',
       'thumb':None,
       'bullets':['[[デロイト]]が[[6月1日]]から米国の[[181,500人]]全員の職名を廃止。アナリスト→コンサルタント→マネジャーというピラミッド階層を廃し、「職能ファミリー＋サブファミリー」体系に移行する。',
                  '背景にあるのは[[AI]]による中間業務の自動化。__「アナリストの仕事をAIがやるなら、そのタイトルに意味はない」__というメッセージが業界に強く波紋を広げている。',
                  'EYが英国新卒採用を11%、PwCが6%削減する中で、デロイトは再編を「スキルの市場価値の見直し」と位置付け。ビッグ4の構造転換が一斉に加速している。'],
       'related':{'axis':'波及','ref_title':'ビッグ4上級幹部がAIスタートアップへ流出加速','ref_date':'2026-05-17','note':'職名廃止と人材流出が同時進行するBig4の構造変化。'}},
      {'score':88,'time':'09:00','source':'Future of Consulting AI',
       'title':'BCG AI収益が3.6億ドルに — ビッグ3戦略ファームで初開示、成果連動型が主流へ',
       'url':'https://futureofconsulting.ai/ai-leadership/2026-consultings-ai-revolution-update/',
       'thumb':None,
       'bullets':['[[BCG]]が2026年4月に[[$144億]]の2025年収益の[[25%]]＝[[36億ドル]]がAI案件だと公式開示。ビッグ3（マッキンゼー・BCG・ベイン）で初めてAI比率を数値化した歴史的な発表。',
                  '__「AI仕事を取れるかどうかが中期の生死を分ける」という競争軸が業界内で確定__した。マッキンゼーの「エージェント at Scale」・PwCの「Agent OS」・KPMGの「Workbench」が同様の収益化レースを展開。',
                  'Big4・Big3合計の[[AI投資額は100億ドル超]]（2023年以来累計）。コンサル費用の成果連動化が進む中で、AI案件の収益構造が従来のTime&Materialsから変わりつつある。'],
       'related':None},
      {'score':85,'time':'10:00','source':'Accenture Newsroom',
       'title':'アクセンチュア×Databricks、AIエージェント大規模展開を加速 — 専用BG立ち上げ',
       'url':'https://newsroom.accenture.jp/jp/news/2026/accenture-and-databricks-accelerate-enterprise-adoption-of-ai-applications-and-agents-at-scale',
       'thumb':None,
       'bullets':['[[アクセンチュア]]と[[Databricks]]が「Accenture-Databricksビジネスグループ」を設立。企業向けAIアプリ・エージェントの大規模展開支援を一体型で提供する体制を整えた。',
                  'Databricksの[[データレイクハウス]]プラットフォームとアクセンチュアの業界別実装力を組み合わせ、__製造・金融・流通の3業界でAIエージェントの本番稼働を半年以内に実現する__支援プログラムを提供。',
                  'アクセンチュアは既にAnthropicとの日本展開でも30,000名研修を進めており、複数のAI提携を同時進行させる「マルチクラウド×マルチLLM」コンサル戦略が鮮明になった。'],
       'related':{'axis':'波及','ref_title':'アクセンチュア×Anthropic日本本格始動','ref_date':'2026-05-17','note':'Anthropicに続きDatabricksとも提携拡大 — マルチLLM戦略の布石。'}},
      {'score':80,'time':'11:00','source':'Perform by AI',
       'title':'コンサル二極化が鮮明に — AI対応加速の大手とパイロット止まりの中小で業界二分',
       'url':'https://performbyai.com/articles/ai-splitting-consulting-industry-gulf',
       'thumb':None,
       'bullets':['AI導入加速の大手コンサル（Big4・Big3）と「パイロット段階から抜け出せない」中小コンサルとの間で[[二極化]]が鮮明に。調査では中小プロジェクトの[[約半数]]がPOCのまま本番移行できていない。',
                  '大手の優位性は「専有AI基盤（PairD / ChatPwC / Rewired）」と「大量スタッフのAI訓練済み人材」にある。__技術単体ではなく、実装を量産できる組織規模が差別化要因__になっている。',
                  '中東・GCCなど新興市場ではローカルコンサルがBig4の代替となるケースも増えており、[[地域分断]]と[[AI二極化]]が重なってコンサル業界の地図が書き換わりつつある。'],
       'related':None},
      {'score':75,'time':'07:00','source':'PwC / Road to Offer',
       'title':'PwC「Human+AI Skillset」30スキル — 20万人ChatPwCユーザーに協働を定着',
       'url':'https://www.roadtooffer.com/blog/big-4-consulting-firms',
       'thumb':None,
       'bullets':['[[PwC]]が2026年2月に「Human+AI Skillset」カリキュラムを開始。[[30スキル]]（AI系15・人間系15）を全職員に展開し、[[ChatPwC]]の20万人ユーザーベースを活用した大規模習熟化を推進。',
                  '__人間の差別化優位（創造性・批判的思考・交渉力）をAIと組み合わせる設計__で、「AIに仕事を奪われる」という懸念から「AIと共に働く」マインドセットへの転換を図る。',
                  'Big4はPwC・デロイト・EYがそれぞれAI研修を大規模展開中だが、スキーム設計の独自性でPwCの30スキル型が注目されている。KPMGの「Workbench AI」との違いは現場実装のスピードにある。'],
       'related':None},
    ]
  },
  {
    'id': 'game', 'name_jp': 'ゲーム', 'name_en': 'Gaming', 'glyph': '●',
    'accent': '#5E3D8C',
    'summary': 'Switch2が5月ソフト最盛期へ — スクウェア・任天堂・Microsoft・バンダイナムコが一斉に大型タイトルを投下し、ハード普及の加速局面に突入',
    'articles': [
      {'score':88,'time':'08:00','source':'Nintendo Life',
       'title':'スクウェア・エニックスがSwitch2マルチ戦略を強化 — CEO「特にSwitch2に注力」と明言',
       'url':'https://www.nintendolife.com/news/2026/05/square-enix-wants-to-further-promote-its-multi-platform-strategy-especially-on-switch-2',
       'thumb':None,
       'bullets':['[[SQUARE ENIX]] CEOがSwitch2を「特に重点展開するプラットフォーム」と明言。[[FF VII Rebirth]]（6月3日）をはじめ大型RPGのSwitch2展開を継続的に進める姿勢を鮮明にした。',
                  'Switch2の初年度ソフトラインナップに関してカプコン・スクウェア・エニックスが中核を担っており、__サードパーティ主導の販売促進がハードの普及速度を左右する__構図が定着してきた。',
                  'Switch2向けのタイトル数は2026年通年で[[50本超]]が見込まれ、前世代機Switch初代の初年度24本から倍増。スクウェアのマルチ戦略が他パブリッシャーへの横展開を促すと業界は見ている。'],
       'related':{'axis':'復状','ref_title':'FF VII Rebirth Switch2/Xbox/PC版6月3日発売確定','ref_date':'2026-05-17','note':'CEO宣言がFF VII Rebirth発売直前のタイミングで出た戦略的な強調。'}},
      {'score':85,'time':'07:30','source':'Nintendo Life',
       'title':'任天堂、Switch2向け5月大型タイトル群の発売窓を再確認 — 11本が集中投下',
       'url':'https://www.nintendolife.com/news/2026/05/nintendo-reconfirms-release-windows-for-major-upcoming-switch-2-games',
       'thumb':None,
       'bullets':['[[任天堂]]が5〜6月のSwitch2発売スケジュールを改めて確認。5月21日ヨッシー・5月22日Tales of Arise・6月3日FF VII Rebirthなど[[11本の大型タイトル]]が集中投下される。',
                  '任天堂ファースト（ヨッシーと不思議な本）とサード（スクウェア・バンナム・カプコン）が混在した強力なラインナップで、__Switch2は初年度から「コンテンツ不足」という従来の課題を払拭__した形。',
                  '週末入荷と予約状況も好調で、ハード販売台数は5月末時点で[[700万台超]]のペースと推定されており、Switch初代の立ち上がり速度を上回る可能性がある。'],
       'related':None},
      {'score':82,'time':'09:00','source':'Game Rant',
       'title':'インディ・ジョーンズSwitch2版が好調 — XboxのAAA移植でサードパーティ参入加速',
       'url':'https://gamerant.com/may-2026-big-month-for-nintendo-switch-2-future/',
       'thumb':None,
       'bullets':['[[インディ・ジョーンズ]]のSwitch2版（5月12日配信）が好調な滑り出し。XboxおよびPC専用だったタイトルがSwitch2に移植されることで、[[Microsoft]]の「コンテンツのプラットフォーム非依存化」戦略が加速している。',
                  'スイッチ版への移植でプレイヤー層が拡大し、サブスクからシングル購入への転換が起きている。__「ゲームはどこでも・誰にでも届ける」という方向が、今や業界標準のビジョン__になりつつある。',
                  'カプコンの「プラグマタ」200万本突破と合わせると、Switch2は発売から1ヶ月以内に[[複数のAAA IPが同時ヒット]]するプラットフォームとして、業界の注目ポイントになっている。'],
       'related':{'axis':'波及','ref_title':'カプコン「プラグマタ」16日で200万本突破','ref_date':'2026-05-17','note':'カプコンに続きXboxタイトルもSwitch2で好調 — サード参入が加速。'}},
      {'score':78,'time':'10:30','source':'Nintendo Life',
       'title':'Tales of Arise Switch2版5月22日発売 — Beyond the Dawn EditionでRPGライン充実',
       'url':'https://www.nintendolife.com/guides/upcoming-nintendo-switch-2-games-and-accessories-for-may-and-june-2026',
       'thumb':None,
       'bullets':['[[バンダイナムコ]]の「Tales of Arise: Beyond the Dawn Edition」がSwitch2向けに[[5月22日]]発売。本編＋拡張DLC一体型パッケージで、RPGジャンルのSwitch2ラインナップを充実させる。',
                  '__2021年発売の本作が5年越しでSwitch2に登場__した点は、旧世代タイトルの「遅延移植」需要がまだ市場に残っていることを示す。Tales of Ariseのメタスコアは87点と高く、初見プレイヤーも多い。',
                  'FF VII Rebirth（6月3日）・ヨッシー（5月21日）・Tales of Arise（5月22日）という3本が1週間に集中する形は、任天堂の意図的なタイトル集中戦略とも見られている。'],
       'related':None},
      {'score':75,'time':'11:30','source':'Game Rant',
       'title':'ヨッシーと不思議な本、Switch2で5月21日発売 — ファースト新作でファミリー層確保',
       'url':'https://gamerant.com/nintendo-switch-2-new-games-coming-out-soon-list-may-2026/',
       'thumb':None,
       'bullets':['[[任天堂]]ファーストタイトル「ヨッシーと不思議な本」が[[5月21日]]にSwitch2で発売。Switch2初の大型ファーストパーティゲームとしてファミリー・低年齢層へのリーチを強化する。',
                  '__ヨッシーシリーズは歴代Switch/Wii Uで安定した販売実績__を持ち、マリオ・ゼルダほどの話題性はないが底堅い客層を持つ。2023年の「ヨッシーのクラフトワールド」は世界300万本超。',
                  '5月21日〜22日の2日間でヨッシー＋Tales of Ariseが連続投下されることで、Switch2ユーザーの週次購入行動が喚起される。任天堂の「小分けリリース」戦略が週ごとの話題を絶やさない効果を狙う。'],
       'related':None},
    ]
  },
]

reflection = {
  'title': 'AI再編・円安攻防・Switch2旋風',
  'subtitle': '週明けGoogle I/O前夜に世界は同時進行で動く — 5月第4週のクロスロード',
  'lead': '本日4分野・20本のニュースから浮かび上がる最大のテーマは [[AI再編]] と [[円安の臨界点]] の同時進行である。以下、各カテゴリを横断して読み解く。',
  'pull_quote': '「単一モデルの性能競争」から「__エコシステムを誰が握るか__」へ──AIの主戦場が移った週末。',
  'sections': [
    {'tag':'総論','heading':'同時多発のリセットが示す「転換の週」','accent':'#1A1A1A',
     'body': '[[Google I/O]]前夜のこの日曜日、OpenAIの組織再編・Deloitteの職名廃止・BOJの利上げ先読みが同時に動いた。いずれも「昨日までの構造が今日終わる」という性格のニュースであり、__週明けは前週と異なる地図で市場と産業が動き始める__可能性が高い。'},
    {'tag':'為替・経済','heading':'158円という「再警戒ライン」','accent':'#B8860B',
     'body': 'USD/JPYが[[158円台]]で週を終え、BOJ利上げ確率は[[77%]]に急騰した。介入効果の半減とドル高の持続が交錯するなか、__160円超えが再現するか否かで財務省の再出動が問われる__。ECBの6月利上げ不確実性も絡み、円・ユーロ・ポンドが同時に試される週が来る。'},
    {'tag':'AI・技術','heading':'AnthropicとOpenAIで分岐する「AIの使い途」','accent':'#2D5BB8',
     'body': 'Anthropicは[[2億ドル]]のゲイツ財団提携でAIを「医療・農業・教育の公共財」として位置付け、一方OpenAIはIPO前夜に「スーパーアプリ」という商業モデルを選んだ。__どちらが「AI民主化」の正解かは、2026年末には数字で答えが出る__。Z.aiのGLM-4.7登場は、中国発OSS勢力が議論に加わることを示す。'},
    {'tag':'産業・業界','heading':'コンサルと「知識労働のリセット」','accent':'#2E6B52',
     'body': 'Deloitteの[[181,500人]]職名廃止とBCGのAI収益[[25%]]開示は、コンサル業の「知識産業」定義が書き換わることを端的に示す。__「アナリストがAIにできる仕事をするなら、そのタイトルを名乗る意味がない」__という問いは、コンサル以外の産業でも2026年内に突きつけられる。'},
    {'tag':'明日へ','heading':'週明けに見るべき3つのシグナル','accent':'#C9B98A',
     'body': '①[[Google I/O]]キーノートでGemini Sparkが正式発表されるか（AIエージェント競争の天井が見える）、②USD/JPYが[[160円]]に接近した時点で財務省が動くか（円安の政策上限が明示される）、③Switch2の[[5月累計販売台数]]が700万台を超えるかどうか（ゲームハード普及の新基準が定まる）。いずれも来週号で答え合わせをしたい。'},
  ],
  'takeaways': [
    {'num':'01','tag':'為替','color':'#B8860B','text':'BOJ利上げ確率77%でも円安は続く — 次の介入トリガーは159〜160円。[[ドル円]]を動かすのは政策発表ではなく、財務省の「行動」だ。'},
    {'num':'02','tag':'AI','color':'#2D5BB8','text':'OpenAIの「スーパーアプリ」とAnthropicの「公共財」は相反しない。__AIは同時に「商業プロダクト」と「社会インフラ」として拡大する__。'},
    {'num':'03','tag':'産業','color':'#2E6B52','text':'Deloitteの職名廃止は終わりではなく始まり。「アナリスト」「コンサルタント」の定義が[[2026年末]]には全産業で問われる。'},
  ],
  'related': [
    {'date':'2026-05-17','title':'FX, AI, IT-Consulting, Game 号 — xAI解散・NVIDIA10GW・Switch2旋風'},
    {'date':'2026-05-16','title':'FX, AI, IT-Consulting, Economy 号 — S&P500史上初7,500超え'},
    {'date':'2026-05-15','title':'FX, AI, IT-Consulting, Economy, Game 号 — Switch2・経済最盛期'},
  ]
}

# ═══════════════════════════════════════════════════════════════════════════════
# RENDERING
# ═══════════════════════════════════════════════════════════════════════════════

# TOC
toc_rows = ''
for i, c in enumerate(cats, 1):
    toc_rows += toc_row(i, c['id'], c['name_jp'], c['name_en'], c['glyph'], c['accent'], len(c['articles']))

# Categories
cats_html = ''
for i, c in enumerate(cats, 1):
    cats_html += cat_header(i, len(cats), c['id'], c['name_jp'], c['name_en'],
                            c['glyph'], c['accent'], len(c['articles']), c['summary'])
    for j, art in enumerate(c['articles']):
        if j == 0:
            cats_html += card_featured(art, c['id'], c['accent'], 'TOP')
        else:
            cats_html += card_side(art, c['id'], c['accent'], j + 1)

# Sections
sections_html = ''
for i, s in enumerate(reflection['sections'], 1):
    sections_html += section_row(i, s['tag'], s['heading'], s['body'], s['accent'])

# Takeaways
takeaways_html = ''
for t in reflection['takeaways']:
    takeaways_html += takeaway_row(t['num'], t['tag'], t['color'], t['text'])

# Related issues
related_html = ''
for r in reflection['related']:
    related_html += related_row(r['date'], r['title'])

# Load template
with open('prompts/email-template.html', encoding='utf-8') as f:
    tmpl = f.read()

total_stories = sum(len(c['articles']) for c in cats)
html = tmpl
html = html.replace('{{ISSUE_NO}}', ISSUE_NO)
html = html.replace('{{ISSUE_DATE}}', ISSUE_DATE)
html = html.replace('{{ISSUE_WEEKDAY}}', WEEKDAY)
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
print(f'生成完了: build/email.html ({size_kb:.1f} KB)')
