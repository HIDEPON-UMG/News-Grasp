"""2026-05-25 号 HTML メール生成スクリプト"""
import re, sys, os
sys.stdout.reconfigure(encoding='utf-8')
os.chdir(os.path.dirname(os.path.abspath(__file__)) + '/..')

ISSUE_DATE = '2026-05-25'
ISSUE_NO   = '20260525'
WEEKDAY    = '月'
CDN        = 'https://raw.githubusercontent.com/HIDEPON-UMG/news-grasp-assets/main'

def render_bullets(bullets, accent):
    bg = accent + '22'
    out = []
    for b in bullets:
        b = re.sub(r'\[\[(.+?)\]\]',
            f'<strong style="background:{bg};padding:1px 3px;border-radius:2px;">\\1</strong>', b)
        b = re.sub(r'__(.+?)__',
            '<span style="border-bottom:2px solid ' + accent + ';padding-bottom:1px;">\\1</span>', b)
        out.append(f'<div class="bul ng-card-body" style="color:{accent}">'
                   f'<span class="dk">{b}</span></div>')
    return '\n'.join(out)

def render_lead(text):
    text = re.sub(r'\[\[(.+?)\]\]',
        '<strong style="background:#C9B98A33;padding:1px 3px;border-radius:2px;">\\1</strong>', text)
    text = re.sub(r'__(.+?)__',
        '<span style="border-bottom:2px solid #C9B98A;padding-bottom:1px;">\\1</span>', text)
    return text

def render_pullquote(text):
    text = re.sub(r'\[\[(.+?)\]\]',
        '<strong style="background:#C9B98A33;padding:1px 3px;">\\1</strong>', text)
    text = re.sub(r'__(.+?)__',
        '<span style="border-bottom:2px solid #8E2A19;padding-bottom:1px;">\\1</span>', text)
    return text

def render_section_body(text):
    text = re.sub(r'\[\[(.+?)\]\]',
        '<strong style="background:#C9B98A33;padding:1px 3px;border-radius:2px;">\\1</strong>', text)
    text = re.sub(r'__(.+?)__',
        '<span style="border-bottom:2px solid #C9B98A;padding-bottom:1px;">\\1</span>', text)
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
    bg = accent + '22'
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
# DATA
# ═══════════════════════════════════════════════════════════════════════════════
cats = [
  {
    'id': 'fx', 'name_jp': '為替', 'name_en': 'Foreign Exchange', 'glyph': '¥',
    'accent': '#B8860B',
    'summary': 'Warsh新FRB議長が就任初週を迎え、タカ派スタンス継続のもとドル円は159円台で均衡。メモリアルデーによる米国薄商いの中、BOJ6月利上げ観測と介入ライン160円が焦点となる週明けとなった。',
    'articles': [
      {'score': 92, 'time': '09:00', 'source': 'CNBC',
       'title': 'Warsh新FRB議長、就任初週はタカ派スタンス継続——インフレ3.8%を前に利下げ封印',
       'url': 'https://www.cnbc.com/2026/05/22/trump-kevin-warsh-fed-chair-interest-rates.html',
       'thumb': 'https://image.cnbcfm.com/api/v1/image/108311287-1779466491747-gettyimages-2277688148-rs2_5781_wb3w.jpeg?v=1779466515&w=1920&h=1080',
       'bullets': [
         '[[ケビン・ウォーシュ]]が5/22にホワイトハウスで就任宣誓——__利下げ拒絶スタンスを就任初週に明確化__した意義は大きく、市場は年内利下げ回数を2回→1回以下に修正しドル買い継続',
         'CPI3.8%を背景に「政策の柔軟性は持ちつつも利下げには慎重」と初期シグナル——FOMC議事録公表(5/28)が次の転換点',
         '[[USDJPY]]への影響：Warsh体制継続でドル高圧力が維持され、159円台の均衡が5月末まで続く可能性',
       ],
       'related': {'axis': '続報', 'ref_title': '5/23号: Warshタカ派×BOJ打診の攻防', 'ref_date': '2026-05-23', 'note': 'Warsh就任宣誓を経て、タカ派継続シナリオがより確信度を高めた。'}},
      {'score': 88, 'time': '08:30', 'source': 'IG証券',
       'title': 'ドル円159円台・円安圧力鮮明——BOJがタカ派シグナルを出しても円は戻せない構造',
       'url': 'https://www.ig.com/jp/news-and-trade-ideas/jpy-stays-weak-even-after-boj-show-off-hawkish-messages-260428',
       'thumb': 'https://a.c-dn.net/c/content/dam/publicsites/igcom/uk/images/news-article-image-folder/bb_USDJPY_Japan_flag_14_11_2024.jpg/jcr:content/renditions/cq5dam.web.1280.1280.jpeg',
       'bullets': [
         '[[植田和男]]総裁が利上げ意欲を示してもドル円が159円台を維持——__日米金利差の縮小速度より米国側の「利下げなし」が支配的__であることが浮き彫りに',
         'コアCPIが4年ぶり低水準で、BOJ内部に「急ぎすぎるな」の声。介入ライン[[160円]]が最重要節目',
         '財務省の実弾警戒は継続しており、上値追いには機関投資家もブレーキ',
       ],
       'related': None},
      {'score': 85, 'time': '07:00', 'source': 'MarketPulse OANDA',
       'title': 'BOJ 6月利上げ観測——コアCPI鈍化でも引き締めサイクル継続の根拠とリスク',
       'url': 'https://www.marketpulse.com/markets/boj-meeting-preview-balancing-act-between-growth-and-inflation-as-usdjpy-approaches-1594516195-key-intervention-risk-zone/',
       'thumb': 'https://storage.googleapis.com/web-content.oanda.com/images/Bank_of_Japan-GettyImages-633058538.original.jpg',
       'bullets': [
         'BOJ 6月会合（6/19）に向けた利上げ確率は約55%——__「コアCPI低下」vs「賃金インフレ継続」の二派が拮抗__。USD/JPY 159.45〜161.95円が「介入リスクゾーン」',
         'MOFは4/30〜5/1で$30B超の介入実績——上値を抑制するアンカーとして機能し続けている',
         '利上げ実施なら円高効果は一時的か持続的か——Q1 GDP2.1%が金融当局に自信を与えつつも、外需依存度の高さが慎重論を下支え',
       ],
       'related': None},
      {'score': 80, 'time': '09:30', 'source': 'MUFG Research',
       'title': 'MUFG 5月FX月次レポート——ドル高圧力の持続性と円の「押し目買い」判断分岐点',
       'url': 'https://www.mufgresearch.com/fx/monthly-foreign-exchange-outlook-may-2026/',
       'thumb': 'https://www.mufgresearch.com/media/yutnrx30/shutterstock_122945524.png',
       'bullets': [
         '[[MUFG]]の5月FXレポートはドル高の主因を「米国インフレ高止まり＋Warsh就任によるFRB独立性への不確実性」と分析——__短期はドル高継続、中期は円高修正のシナリオ__',
         'EUR/USD 1.15台での下値支持は「ECBの利下げ速度差」よりも「欧州製造業の回復鈍さ」に起因。1.17台へ戻す条件はFRBの態度軟化か',
         'GBP/USDは1.35前後で底堅さ——英国のインフレ鈍化が[[BOE]]の利下げ余地を広げ、対円でのポンド安が視野に',
       ],
       'related': None},
      {'score': 75, 'time': '06:00', 'source': 'Forex Factory',
       'title': '週間FXカレンダー5/25〜5/30——FOMC議事録・米PCE・日本鉱工業生産が相場の鍵',
       'url': 'https://www.forexfactory.com/calendar?month=this',
       'thumb': None,
       'bullets': [
         '米国は本日（5/25）[[メモリアルデー]]で市場全休——週前半は薄商い。主要イベントは5/28（水）FOMC議事録、5/29（木）米GDP改定値、5/30（金）PCEコア価格指数',
         '日本サイドでは5/29に鉱工業生産・小売売上高——__GDP2.1%成長後の4月データで内需の「勢い持続」を確認できるかが焦点__。ここで失速なら6月BOJ利上げの根拠が揺らぐ',
         'EUR/JPY と GBP/USD はECB理事会議事要旨（5/27）前後に動意。ハト派メッセージが強ければユーロ下落→ドル円間接支援の経路',
       ],
       'related': None},
    ]
  },
  {
    'id': 'ai', 'name_jp': 'AI', 'name_en': 'Artificial Intelligence', 'glyph': '◆',
    'accent': '#2D5BB8',
    'summary': 'Gemini Sparkがベータ開始し、OpenAI DeployCoとAnthropicのWall Street JVが同時進行。AIエージェントが「実証」から「実装」へ移行する週の幕開けとなった。一方でCloudflare 1,100人削減が示すようにAI内製化は雇用構造を静かに書き換えている。',
    'articles': [
      {'score': 93, 'time': '10:00', 'source': 'TechCrunch',
       'title': 'Gemini Spark ベータ開始——Google AI Ultraで「眠らないAIアシスタント」が5/25週に動き出す',
       'url': 'https://techcrunch.com/2026/05/19/google-introduces-gemini-spark-a-24-7-agentic-assistant-with-gmail-integration/',
       'thumb': 'https://techcrunch.com/wp-content/uploads/2026/05/spark.jpg?resize=1200,673',
       'bullets': [
         '[[Gemini Spark]]がGoogle I/O（5/19）から「1週間後」のベータ開始——Gmail・Docs・Slideと連携した__クラウドベース24時間エージェント__はユーザー不在中も動作継続',
         'Gemini 3.5＋Antigravityハーネス採用——購入や情報共有を事前確認なしで行う可能性についても明記し「自律性と監督」の問いを投げかける',
         '__競合との差別化軸は「OS全体への統合深度」__——[[エコシステム占有率]]が真の戦場になった',
       ],
       'related': None},
      {'score': 90, 'time': '08:00', 'source': 'TechJournal',
       'title': 'OpenAI DeployCo $4B始動——McKinsey・TPG・Goldman Sachsが支える「AI展開子会社」の全容',
       'url': 'https://techjournal.org/openai-launches-4-billion-deployment-company',
       'thumb': 'https://techjournal.org/wp-content/uploads/2026/05/openai-launches-4-billion-deployment-company.jpg',
       'bullets': [
         '[[OpenAI]]がTPG・Goldman Sachs・Bain Capital・McKinseyら19社から4Bドルを調達し「DeployCo」を設立——__OpenAI過半数支配の展開子会社が企業のAI戦略策定から本番稼働まで一貫支援__',
         '埋め込みエンジニア型（6ヶ月常駐）でGPT-5系モデルを基幹システムに連結',
         '[[エンタープライズ収益]]を全体の50%に引き上げる目標の核——API単価競争からの脱却戦略が鮮明',
       ],
       'related': None},
      {'score': 87, 'time': '07:30', 'source': 'CNBC',
       'title': 'Anthropic × Goldman × Blackstone $1.5Bジョイントベンチャー——ClaudeがPE傘下企業群に自律エージェントを展開',
       'url': 'https://www.cnbc.com/2026/05/04/anthropic-goldman-blackstone-ai-venture.html',
       'thumb': 'https://image.cnbcfm.com/api/v1/image/108269270-1771956501559-gettyimages-2261854833-AFP_98666EE.jpeg?v=1779385068&w=1920&h=1080',
       'bullets': [
         '[[Anthropic]]がGoldman Sachs・Blackstone・Hellman & Friedmanと$1.5BのJVを設立——PEファンド傘下の数千社に[[Claude]]の自律エージェント機能を組み込む「McKinseyのAI版」',
         'Goldman Sachs内部ではすでにClaudeを会計・コンプライアンス業務の自律化パイロットで活用——Anthropicエンジニアが6ヶ月間バンク内に常駐してシステムを共同開発',
         '__Anthropicが少数株主として参画するデザインはOpenAI DeployCoの過半数支配と対照的__——「モデル提供者」の立ち位置を維持しながら実装側の収益も取り込む二重戦略',
       ],
       'related': {'axis': '波及', 'ref_title': '5/23号: Anthropic初の黒字化・年収$10.9B達成', 'ref_date': '2026-05-23', 'note': '$10.9B収益達成の翌月のWall Street JVはAnthropicが「提供者→共同実装者」へ変容する証左。'}},
      {'score': 83, 'time': '09:00', 'source': 'Yahoo Finance',
       'title': 'Cloudflare 1,100人削減——AI内製化600%増が「人的コスト」問題を顕在化させた構造',
       'url': 'https://finance.yahoo.com/sectors/technology/articles/layoffs-accelerate-may-2026-firms-040430218.html',
       'thumb': 'https://s.yimg.com/ny/api/res/1.2/GDCcuGuvR4Kq30ZvSTcriA--/YXBwaWQ9aGlnaGxhbmRlcjt3PTEyMDA7aD02NzU-/https://media.zenfs.com/en/beincrypto_us_662/31e4e6650e51130faa1cd1500a1876ab',
       'bullets': [
         '[[Cloudflare]]が全社員の20%（1,100人超）を削減——内部のAI活用が3ヶ月で600%増加したことを受け、__同じアウトプット量を少ない人員で達成できるとの経営判断__が下された',
         '5月2026年の削減ラッシュは「Cloudflareに限らずフェーズ転換期の構造的現象」——AI投資が加速する一方で、従来型の「人海戦術」ポジションが消滅しつつある',
         '__削減対象はサポート・運用・QA系が中心__とされ、「AIコパイロットが人の仕事を奪う」という議論が数字で証明された初の大規模事例として市場の注目を集める',
       ],
       'related': None},
      {'score': 78, 'time': '06:30', 'source': 'CNBC',
       'title': 'Trump政権、AI事前テスト義務化——Google・Microsoft・xAIが規制当局に「早期アクセス」提供合意',
       'url': 'https://www.cnbc.com/2026/05/05/ai-oversight-trump-google-microsoft-xai.html',
       'thumb': 'https://image.cnbcfm.com/api/v1/image/108301477-1777896715502-gettyimages-2273875296-rs2_2378.jpeg?v=1778786289&w=1920&h=1080',
       'bullets': [
         '[[Trump]]政権がAIセーフティ大統領令を廃止した一方で、新たに「主要AIモデルの政府機関への早期アクセス義務」を打ち出し——[[Google]]・[[Microsoft]]・[[xAI]]が合意済み',
         '__「規制なし」から「政府が先に使う」へ——AIガバナンスの方向性が変質した__。廃止した旧令の「縛り」の代わりに、政府自身がモデルを評価・活用する枠組みへ移行',
         '欧州AI規制法（AI Act）との対比が鮮明——EUが「リスク分類・禁止行為」で縛るのに対し、米国は「ファーストアドプター型ガバナンス」へ。日本企業への波及は来四半期以降',
       ],
       'related': None},
    ]
  },
  {
    'id': 'it', 'name_jp': 'IT-Consulting', 'name_en': 'IT & Consulting', 'glyph': '▲',
    'accent': '#2E6B52',
    'summary': 'AccentureがUK Post Office（£2.69億）とFaculty買収・XBOW投資を立て続けに完遂。「AI実装＋サイバーセキュリティ＋政府DX」の三正面作戦でコンサル最前線を独走する週明けとなった。IT-Consulting市場は2026年に$126.79Bへ13.3%成長の見通し。',
    'articles': [
      {'score': 90, 'time': '09:00', 'source': 'The Register',
       'title': 'UK郵便局ホライゾン代替——Accentureが富士通を£2.69億で引き継ぐ「Walk In Take Over」',
       'url': 'https://www.theregister.com/paas-and-iaas/2026/05/21/years-after-uk-post-office-scandal-broke-accenture-and-oneview-commerce-bag-contract-to-replace-fujitsu/5244243',
       'thumb': 'https://image.theregister.com/5244269.jpg?imageId=5244269&x=0&y=0&cropw=100&croph=100&panox=0&panoy=0&panow=100&panoh=100&width=1200&height=683',
       'bullets': [
         '[[Accenture]]とOneView CommerceがUK郵便局の[[Horizon]]システム代替案件（£4.1億）を受注——[[富士通]]が1996年から構築した冤罪事件の温床となったシステムを「Walk In Take Over」方式で引き継ぐ',
         '__既存システムを「稼働させたまま移行」する高難度ミッション__がAccentureの政府DX実績と信頼性を世界に示すケースとなる',
         '富士通の英国公共部門からの退場は「Horizonスキャンダル」の象徴的終章——DX調達における「透明性と説明責任」が改めて問われる',
       ],
       'related': None},
      {'score': 87, 'time': '08:00', 'source': 'Accenture Newsroom',
       'title': 'Accenture、Faculty買収完了——400人のAIエンジニアで「決定論的AI」実装力を強化',
       'url': 'https://newsroom.accenture.com/news/2026/accenture-completes-acquisition-of-faculty',
       'thumb': 'https://newsroom.accenture.com/default-meta-image.png?width=1200&format=pjpg&optimize=medium',
       'bullets': [
         '[[Accenture]]がUK AIスタートアップ[[Faculty]]の買収を3/16に完了——400人超のデータサイエンティスト・AIエンジニアが統合。FacultyのDECIDEシミュレーション&amp;最適化プロダクトがAccentureクライアント向けに展開',
         'Faculty創業者Marc WarnerがAccentureの英国AI部門を率いる——__「コンサル大手が独立AIスタジオを丸ごと飲み込む」M&amp;A型AI能力獲得がトレンド化__',
         'FacultyのDECIDEエンジンは「__複雑なシステムの意思決定を数値化・最適化__」する能力が英国政府にも評価済み——政府調達とコンサルティングの垂直統合が進む',
       ],
       'related': None},
      {'score': 83, 'time': '10:00', 'source': 'Accenture Newsroom',
       'title': 'NTT DOCOMO × Accenture、ユニバーサルウォレット——AIドリブン社会に向けたデジタルID基盤を共同展開',
       'url': 'https://newsroom.accenture.com/news/2026/ntt-docomo-global-and-accenture-launch-universal-wallet-infrastructure-powering-the-trusted-future-of-a-data-led-ai-driven-digital-society',
       'thumb': 'https://newsroom.accenture.com/default-meta-image.png?width=1200&format=pjpg&optimize=medium',
       'bullets': [
         '[[NTTドコモ]]グローバルと[[Accenture]]がユニバーサルウォレット基盤を共同ローンチ——AIが情報を読み解き、個人の同意に基づいてデジタルIDを横断的に管理する__「AIドリブン・デジタル社会の信頼インフラ」__を提唱',
         '金融・医療・行政など複数分野のサービス連携を単一ウォレットIDで実現——欧州eID（EUDI Wallet）との互換性を念頭に設計',
         '日本の携帯キャリアとグローバルコンサルの結合は「__NTTグループがDXインフラで世界プレイヤーへ転換する意志__」の表れ',
       ],
       'related': None},
      {'score': 80, 'time': '09:30', 'source': 'Accenture Newsroom',
       'title': 'Accenture、XBOW投資——自律型AIサイバーセキュリティで「攻撃的テスト」を産業として確立',
       'url': 'https://newsroom.accenture.com/news/2026/accenture-invests-in-xbow-to-advance-continuous-offensive-security-testing-and-exposure-management',
       'thumb': 'https://newsroom.accenture.com/news/2026/media_14b3576a03d2f026b2c290114872c60f871a0fb72.png?width=1200&format=pjpg&optimize=medium',
       'bullets': [
         '[[Accenture]]が[[XBOW]]（自律型サイバーセキュリティテストプラットフォーム）に戦略投資——AIエージェントが自動で「攻撃シナリオ」を生成・実行し、__組織の防御網の弱点を人間の攻撃者より先に発見__する仕組み',
         'Accentureのクライアントに対してXBOWの連続的露出管理（Continuous Exposure Management）を提供——従来の年次ペネトレーションテストから「常時稼働型セキュリティ評価」へ',
         'AI時代のサイバー脅威は__「攻撃もAI、防御もAI」の自動化戦争__へ移行——XBOWへの投資はAccentureがセキュリティ分野でのAI実装競争で先手を打つ布石',
       ],
       'related': None},
      {'score': 72, 'time': '07:00', 'source': 'Management Consulted',
       'title': 'IT Consulting市場2026年13.3%成長——AIエージェント需要が$126.79Bへ市場を押し上げる',
       'url': 'https://managementconsulted.com/management-consulting-industry-report/',
       'thumb': 'https://assets.managementconsulted.com/app/uploads/2025/08/04090850/Management-Consulting-Industry-Report.png',
       'bullets': [
         '2026年のIT Consulting市場規模は$126.79B（前年$111.95Bから13.3%成長）——[[生成AI導入]]・[[エージェント型AI]]の需要が伸長の主因',
         'スケールドインテグレーター（Accenture・IBM・Capgemini等）が依然として案件の60%超を独占——__「AI実装を自社でやれる大手だけが勝つ」寡占化が統計にも現れ始めた__',
         '中規模SIへの影響：大手が「AI付加価値」を前提に価格競争力を高める中、中規模ファームは「業種特化型ニッチ」へのピボットを迫られている',
       ],
       'related': None},
    ]
  },
  {
    'id': 'economy', 'name_jp': '経済', 'name_en': 'Economy', 'glyph': '■',
    'accent': '#8E2A19',
    'summary': '米国メモリアルデーで主要市場が全休する月曜日、日本はQ1 GDP 2.1%成長と日経平均63,339円高の余韻を引き継ぐ。Warsh体制初週のFRBがCPI3.8%を抑えに利下げを封じる中、週後半の米PCEとFOMC議事録が相場の方向性を決める。',
    'articles': [
      {'score': 93, 'time': '09:00', 'source': 'CNBC',
       'title': '日本Q1 GDP年率2.1%——自動車輸出回復と5期連続の個人消費増で市場予想を上回る成長',
       'url': 'https://www.cnbc.com/2026/05/19/japan-first-quarter-gdp-economy-inflation-energy.html',
       'thumb': 'https://image.cnbcfm.com/api/v1/image/108188376-1755681997373-gettyimages-1252632438-AFP_33EE2UE.jpeg?v=1755682695&w=1920&h=1080',
       'bullets': [
         '内閣府の2026年1〜3月期GDP速報値は実質前期比+0.5%（年率[[2.1%]]成長）——2四半期連続プラス、市場予想の1.7%を上回り__「脱デフレ」の定着を裏付ける基調確認__となった',
         '個人消費（GDP比50%超）は+0.3%で5期連続プラス——エネルギー価格の落ち着きと賃上げが実質購買力を下支え。設備投資も+0.3%と2期連続増加',
         '外需寄与度+0.3%pt：米向け自動車輸出が回復し輸出が大きく牽引——__今後はWarshタカ派FRBによる米消費減速リスクが外需の「尾引き要因」として浮上してきた__',
       ],
       'related': None},
      {'score': 88, 'time': '10:30', 'source': 'BBNTimes',
       'title': '日経平均63,339円・SoftBank主導ラリー——AI投資期待と外国人買いが東京を多年来高値に押し上げ',
       'url': 'https://www.bbntimes.com/global-economy/nikkei-225-japan-s-benchmark-surges-2-68-to-63-339-as-softbank-roars-and-ai-optimism-sends-tokyo-to-multi-year-highs',
       'thumb': 'https://www.bbntimes.com/images/Nikkei_225_-_Japans_Benchmark_Surges_268_to_63339_as_SoftBank_Roars_and_AI_Optimism_Sends_Tokyo_to_Multi-Year_Highs.jpg',
       'bullets': [
         '5/22（金）の日経平均は+2.68%・63,339円で引け——[[SoftBank（9984）]]が前日比+12%（前々日は+20%）という異常なラリーを牽引。OpenAI・SB EnergyのIPO観測報道が引き金',
         '外国人投資家が週間で¥948億超を買い越し——「__日銀利上げ期待×AI投資期待のダブル追い風__」という構図が日本株に向かうグローバルマネーを増幅させている',
         '5/25（月）は米市場休場のため東京市場への資金フローが集中するか注目——日経平均の63,000円超え定着が「本物」かどうかが今週の週初から試される',
       ],
       'related': {'axis': '復状', 'ref_title': '5/22号: 日経平均61,782円ラリー', 'ref_date': '2026-05-22', 'note': '5/22の61,782円から5/23には63,339円へ急騰し、ラリーの加速が鮮明になった続報。'}},
      {'score': 84, 'time': '06:00', 'source': 'InvestingLive',
       'title': '米国メモリアルデー全休——週前半薄商い・後半はPCEとFOMC議事録が相場の分水嶺に',
       'url': 'https://investinglive.com/stock-market-update/us-markets-face-shortened-friday-and-full-monday-closure-for-memorial-day-20260522/',
       'thumb': 'https://images.investinglive.com/images/USA_id_4a13eb37-c453-4cfa-8845-f18b9f793950_size975.jpg',
       'bullets': [
         '5/25（月）は[[メモリアルデー]]で米国株式・債券・先物市場が全休——流動性が著しく低い状態での為替・コモディティ動向に注意が必要',
         '今週の重要スケジュール：5/28（水）FOMC議事録・5/29（木）米GDP改定値・5/30（金）PCEコア価格指数——__これら3本が「Warsh体制初の経済診断」として機能し、市場の次の方向性を決定する__',
         'S&amp;P500は7,405水準でAI集中リスクが浮上——ヘルファインダール指数（HHI）が過去最高を記録し、「[[非AI株の格差]]」は依然+16%に留まる',
       ],
       'related': None},
      {'score': 80, 'time': '08:00', 'source': 'T. Rowe Price',
       'title': 'Warsh体制初週のFRB——CPI3.8%でタカ派継続・年内利下げ2回観測が1回以下に後退',
       'url': 'https://www.troweprice.com/personal-investing/resources/insights/global-markets-weekly-update.html',
       'thumb': 'https://www.troweprice.com/etc.clientlibs/iinvestor/clientlibs/global-personal-investor/resources/css/images/TRP_OG_Default.png',
       'bullets': [
         '[[ケビン・ウォーシュ]]新議長の初週はタカ派スタンスの確認週——CPI3.8%を背景に「政策の柔軟性を維持しつつ利下げには慎重」というメッセージを維持する公算大',
         'フェデラルファンド金利の市場織り込み：__年内利下げ2回観測（25bp×2）から1回以下（25bp×1 or 0）へ急速にシフト__——住宅ローン6.51%（8月以来最高）が実体経済への冷却効果を発揮',
         'T. Rowe Priceの週次レポートは「__次のリスクは中東緊張再燃またはPCEサプライズによる利下げ期待の完全消滅__」と分析',
       ],
       'related': None},
      {'score': 76, 'time': '07:00', 'source': 'ダイヤモンドZAI',
       'title': '日本10年債1.8%・日米金利差縮小シナリオ——BOJ 6月利上げで「実質金利転換点」を模索',
       'url': 'https://diamond.jp/zai/articles/-/1066989',
       'thumb': 'https://dfinance.ismcdn.jp/zai/mwimgs/0/1/-/img_0189c3d91eb4e2955d68b595fc6296c873783.jpg',
       'bullets': [
         '日本の10年国債利回りが1.8%を超え始め、日米金利差縮小シナリオが現実味を帯びてきた——[[植田和男]]総裁のBOJが6月会合で利上げに踏み切れば、__実質金利がマイナス圏を脱出する「転換点」として歴史に刻まれる__可能性',
         '雇用統計（5/22）では新規失業保険申請件数が予想より増加——米国の労働市場に軟化の兆しが見え始め、Warshが望むインフレ低下に時間的余裕が生まれるか',
         '日米金利差縮小の「本物度」は5/30 PCEが鍵——PCEコアが前月比+0.2%以下に収まれば円高圧力が一気に強まり、BOJ利上げとのシナジーでドル円の[[158円]]割れも射程内',
       ],
       'related': None},
    ]
  },
]

reflection = {
  'title': 'Warsh就任×Gemini Spark——AIエコシステムと新金融秩序の同時着地',
  'subtitle': 'メモリアルデーの薄商いが象徴するように、5/25週は「新体制の確認」と「AIエージェント実装元年の幕開け」が静かに交差する。',
  'lead': '本日5/25（月）は米国メモリアルデーで薄商いながら、[[ウォーシュFRB]]初週とGemini Sparkベータ始動という2つの「着地点」が同時に確認される週の幕開けとなった。どちらも「試験段階から実運用段階への移行」を告げる起点であり、__AIエコシステムと新金融秩序が「同日に着地する」歴史的な週初め__となっている。',
  'pull_quote': '「使って試す」から「__組み込んで動かす__」へ——AIエージェントが2026年初夏、ついに静かな生産システムへと着地した。',
  'sections': [
    {'tag': '総論', 'heading': '「移行の週」: 新FRB体制とAI実装フェーズが同時に動き出す', 'accent': '#1A1A1A',
     'body': '[[ウォーシュFRB]]が就任初週を迎え、AI大手3社はエンタープライズ展開の号砲を同時に鳴らした。これは偶然ではなく「2026年の相場テーマ」の集約——__金融政策の方向性確認と、AIが「実験」から「実装」へ転じる構造的変化__が、同一週に確認を求めてきた格好だ。メモリアルデーの薄商いはむしろ「雑音を排した観察期間」として機能する。'},
    {'tag': '為替', 'heading': '159円台の「均衡なき均衡」がWarsh就任で固まる', 'accent': '#B8860B',
     'body': '[[USDJPY]]は159円台を週末から引き継ぎ、[[ウォーシュ]]就任後もタカ派スタンスが揺らがない中でドル高圧力が継続した。BOJの6月利上げ観測が55%まで上昇しているにもかかわらず円が戻せない理由は__「日米金利差の縮小速度より、米国の利下げ拒絶が支配的」__という構造にある。介入ライン160円と5/28 FOMC議事録が週のピボット。'},
    {'tag': 'AI', 'heading': 'Gemini Spark・DeployCo・Anthropic JV: 3社同時に「エージェント実装戦争」が開幕', 'accent': '#2D5BB8',
     'body': '[[Gemini Spark]]ベータ、[[DeployCo]]本格稼働、Anthropic×Goldman JV——この3点が「AIエージェント実装戦争元年の号砲」として同週に並んだことの意味は大きい。各社の共通項は「モデルを売る」から「組み込んで動かす」へのビジネスモデル転換であり、__戦場は「API単価」から「エコシステム占有率」へ完全に移行した__。Cloudflareの1,100人削減はその裏側——人を減らす側にも同じAIが動いている。'},
    {'tag': 'IT', 'heading': 'Accentureの「三正面作戦」: 政府DX・AI M&amp;A・サイバーで同時進行', 'accent': '#2E6B52',
     'body': '[[Accenture]]はUK Post Office受注（£2.69億）、Faculty買収完了（400人AIエンジニア統合）、XBOW投資を1週間に集中させた。この「三正面同時展開」は、__「AIコンサルは実装力のある大手のみが生き残る」寡占化の加速__を象徴している。富士通の英国退場は対照的な事例——信頼性の喪失が国際案件からの排除に直結することを改めて示した。'},
    {'tag': '経済', 'heading': '日本GDP2.1%と日経平均63,339円: 「実力株高」かどうかが5/25週に試される', 'accent': '#8E2A19',
     'body': '日本のQ1 GDP2.1%成長と日経平均63,339円という数字は「脱デフレ定着」の強いシグナルだ。しかし[[SoftBank]]主導のラリーはIPO期待という一時的要因も含んでいる。__「外国人買いの継続性」と「米メモリアルデー後の週後半相場」が日本株の実力を篩にかける__格好の試験台になる。5/28 FOMC議事録と5/30 PCEがドル円経由で日本株にも影響を与える構造を忘れずに。'},
    {'tag': 'ゲーム', 'heading': '本日休載', 'accent': '#5E3D8C',
     'body': '月曜日はゲームカテゴリ定例休載日。次回は火曜（5/26）から再開予定。'},
    {'tag': '明日へ', 'heading': '5/26〜5/30に向けて注目すべき4点', 'accent': '#C9B98A',
     'body': '5/26（火）米国市場再開後の「Warsh体制初の本格稼働日」にドル円がどう動くか / 5/27（水）ECB議事要旨で欧州利下げペースが加速するならEUR/USDの反転余地 / 5/28（水）FOMC議事録——タカ派色の濃さによって年内利下げ期待が消滅するかどうか / __5/30（金）PCEコア価格指数——前月比+0.2%以下なら利下げ期待が復活し、円高・株高の連鎖が走る可能性__。+0.3%以上ならドル高・日本株下落リスク'},
  ],
  'takeaways': [
    {'num': '01', 'tag': '為替', 'color': '#B8860B',
     'text': '[[ウォーシュ就任初週×タカ派継続]]でドル円159円台の均衡が続く。介入ライン160円と5/28 FOMC議事録が週の転換点'},
    {'num': '02', 'tag': 'AI', 'color': '#2D5BB8',
     'text': '[[Gemini SparkとDeployCo]]が「AIエージェント実装戦争」元年の号砲。戦場はAPI単価からエコシステム占有率へ移行した'},
    {'num': '03', 'tag': '産業', 'color': '#2E6B52',
     'text': '[[Accenture 2連続M&A＋政府IT受注]]で「AIコンサル最前線」を独走。富士通の英国退場と対照的な光景'},
  ],
  'related': [
    {'date': '2026-05-23', 'title': '前号: WarshタカHA派×BOJ打診の攻防'},
    {'date': '2026-05-22', 'title': 'Google Cloud $750Mパートナーファンド始動号'},
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
