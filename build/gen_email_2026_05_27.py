"""2026-05-27 号 HTML メール生成スクリプト (水曜: FX/AI/IT/Economy — Game休載)"""
import re, sys, os
sys.stdout.reconfigure(encoding='utf-8')
os.chdir(os.path.dirname(os.path.abspath(__file__)) + '/..')

ISSUE_DATE = '2026-05-27'
ISSUE_NO   = '20260527'
WEEKDAY    = '水'
CDN        = 'https://raw.githubusercontent.com/HIDEPON-UMG/news-grasp-assets/main'

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
# DATA — 2026-05-27 (水曜: FX / AI / IT / Economy — Game休載)
# ═══════════════════════════════════════════════════════════════════════════════
cats = [
  {
    'id': 'fx', 'name_jp': '為替', 'name_en': 'Foreign Exchange', 'glyph': '¥',
    'accent': '#B8860B',
    'summary': 'USD/JPY 158円台後半で膠着。FOMC議事録・PCE待ちの様子見ムードが続く中、介入効果の限界と日銀追加利上げの板挟みが円の行方を左右する週。',
    'articles': [
      {'score': 92, 'time': '06:30', 'source': 'Investing.com',
       'title': 'USD/JPY 158円台後半で膠着──Warsh新議長の利下げ封印でドル高持続、160円介入ラインへ再接近',
       'url': 'https://www.investing.com/analysis/usdjpy-forecast-japans-intervention-dilemma-deepens-as-fed-outlook-shifts-200680270',
       'thumb': f'{CDN}/ng-thumb-fx.jpg',
       'bullets': [
         '[[USD/JPY]] は158円台後半で様子見が続く。**Warsh新Fed議長**の「利下げ封印」スタンスが米金利を高止まりさせ、__均衡なき円安が継続__する構図に。',
         '**介入ライン160円**が再び意識され始め、財務省・日銀の牽制発言も増加。4月末・5月頭の円買い介入（推定**5兆円規模**）の効果が薄れつつある段階。',
         '市場の次の焦点は**5/28 FOMC議事録**。タカ派・ハト派の温度差が確認されれば、ドル円の方向感が一時的に定まる可能性。',
       ],
       'related': None},
      {'score': 87, 'time': '07:15', 'source': 'Japan Times',
       'title': '日本、円安対応に苦慮──介入後の膠着と追加利上げの板挟みで政策オプションが狭まる',
       'url': 'https://www.japantimes.co.jp/business/2026/05/02/markets/japan-yen-intervention-focus/',
       'thumb': 'https://www.japantimes.co.jp/japantimes/uploads/images/2026/05/02/538402.jpg?v=3.1',
       'bullets': [
         '[[日銀]] は追加利上げの意向を維持しつつも、**中東情勢**（ホルムズ海峡封鎖懸念）でエネルギー輸入コストが変動。__インフレ目標2%と実質購買力低下の狭間__で政策立案が難航。',
         '**廣瀬副総裁**は「利上げ方向性は変わらない」と表明しながら、具体的な時期については言及を回避。市場は**9月会合**での次の一手を予測している。',
         '円安が158〜160円台で推移する中、**財務省**の外貨準備高から試算される介入余力は「まだ十分」との見方が多いが、繰り返し介入の効果減衰を懸念する声も。',
       ],
       'related': None},
      {'score': 78, 'time': '06:00', 'source': 'FXStreet',
       'title': 'EUR/USD 1.162台──ECB 6月利下げ観測でドル高一服、USDインデックス4週ぶり小幅調整',
       'url': 'https://www.fxstreet.com/currencies/eurusd',
       'thumb': f'{CDN}/ng-thumb-fx.jpg',
       'bullets': [
         '[[EUR/USD]] が1.162台で推移。**ECBの6月利下げ**観測が根強く、対ユーロでのドル売り圧力が欧州通貨を下支え。__ドル一極集中相場が部分的に緩む__局面か。',
         '米国の**関税政策不確実性**と原油価格の急変動が欧米の政策差縮小を促す方向へ。ECBは**インフレ率2.2%**まで低下を確認したうえで、利下げ再開への道筋を整備中。',
         'ユーロ圏の**製造業PMI**が48台（収縮圏）と弱さが続く一方、サービス業は堅調でECBの判断は複雑。__景気と物価の二元方程式__が続く。',
       ],
       'related': None},
      {'score': 72, 'time': '06:15', 'source': 'Forex.com',
       'title': 'GBP/USD 英賃金5.5%高止まり──BOE早期利下げ期待が後退、ポンドが対ドルで底堅く推移',
       'url': 'https://www.forex.com/en-us/news-and-analysis/us-dollar-eur-usd-gbp-usd-aud-usd-gold-oil-weekly-technical-outlook-1-20-2026/',
       'thumb': f'{CDN}/ng-thumb-fx.jpg',
       'bullets': [
         '[[GBP/USD]] はポンドが対ドルでやや強含み。英国の**4月雇用統計**で賃金上昇率（除ボーナス）が**5.5%**と予想を上回り、**BOE**の利下げサイクル開始が後ずれする観測が浮上。',
         'ポンドは**欧州通貨の中で最も底堅い**動きを示し、対ユーロでも前週比プラス圏。英経済の__スタグフレーション懸念は薄れつつある__が、長期的な成長率見通しは依然慎重。',
         '次の焦点は**6月19日BOE会合**。市場は25bpの利下げ確率を38%程度に切り下げた段階。',
       ],
       'related': None},
      {'score': 70, 'time': '06:10', 'source': 'MarketPulse (OANDA)',
       'title': '介入後遺症：BOJは時間を買えたか──円安トレンド反転への道筋と実効性の再検証',
       'url': 'https://www.marketpulse.com/markets/usdjpy-the-intervention-aftermath-has-the-boj-bought-time-or-reversed-the-trend/',
       'thumb': 'https://storage.googleapis.com/web-content.oanda.com/images/JPY_1920x1080-3.original.jpg',
       'bullets': [
         '4月末・5月初の[[円買い介入]]（推定**5兆円**規模）から約3週間。効果は一時的で、**158円台**まで円安が巻き戻し。__買った時間で何ができたか__が問われる段階へ。',
         '日米**金利差6.5%超**の根本的な構造が変わらない限り、介入は流れに逆らう「時間稼ぎ」に過ぎない。市場参加者のポジションは依然**ドル買い優位**。',
         '**BOJ** が利上げを加速させるか、**Fed** が利下げを前倒しするか──どちらかの条件が揃わなければ__構造的円安の解消は難しい__。',
       ],
       'related': None},
    ]
  },
  {
    'id': 'ai', 'name_jp': 'AI', 'name_en': 'Artificial Intelligence', 'glyph': '◆',
    'accent': '#2D5BB8',
    'summary': 'セキュリティAI（Mythos）・パーソナルエージェント（Gemini Spark）・資金調達統合フェーズという3テーマが並走。LLMの競争サイクルが高速化する一方で、AIの実装・安全性・エコシステム統合が主戦場へシフト。',
    'articles': [
      {'score': 95, 'time': '05:30', 'source': 'Help Net Security',
       'title': 'Claude Mythos、1万件超の重大脆弱性を自律発見──27年前FreeBSD RCEバグ含む、Project Glasswingアップデート',
       'url': 'https://www.helpnetsecurity.com/2026/05/26/anthropic-project-glasswing-update/',
       'thumb': 'https://img.helpnetsecurity.com/wp-content/uploads/2026/03/11120918/anthropic-2-1500.webp',
       'bullets': [
         '[[Claude Mythos]] がProject Glasswingのパートナー企業（AWS・Apple・Cisco・Google・Microsoft・NVIDIA等）に公開されて以来、**1万件超の重大・高重要度脆弱性**をクリティカルソフトウェアから自律検出した。',
         '特筆すべきは**27年前のFreeBSD RCE脆弱性**（NFS経由でroot取得可能）を完全自律で発見・エクスプロイト作成した事例。wolfSSL（数十億台搭載）では偽証明書生成の手口を実証済み。__脆弱性発見の容易さと修正の難しさが新たなボトルネック__と指摘。',
         'Anthropicは一般公開の前提として「**現時点でどの企業も構築できていないセーフガード**」の実現を挙げており、__リスクと能力の非対称問題__に正面から向き合う姿勢を示した。',
       ],
       'related': {'axis': '波及', 'ref_title': 'Claude Opus 4.7 全面GA（5/26）', 'ref_date': '2026-05-26', 'note': 'Opus 4.7のGA化に続き、限定公開の特化型セキュリティモデルMythosが登場。Anthropicはフロンティアと安全の両軸で展開を拡大している。'}},
      {'score': 88, 'time': '06:45', 'source': 'TechCrunch',
       'title': 'Gemini Spark、$100/月AI Ultraで24時間パーソナルエージェント提供開始──Gmail統合でバックグラウンド常時稼働',
       'url': 'https://techcrunch.com/2026/05/19/google-introduces-gemini-spark-a-24-7-agentic-assistant-with-gmail-integration/',
       'thumb': 'https://techcrunch.com/wp-content/uploads/2026/05/spark.jpg?resize=1200,673',
       'bullets': [
         '[[Google]] のGemini Sparkが「**AI Ultra**」（$100/月）加入者向けに提供開始。Gmail・Google Docsと深く統合された**24時間常時稼働型パーソナルAIエージェント**で、スマホロック中もバックグラウンドでタスクを実行。',
         'ユーザーは専用**Gmailアドレス**に指示を送るだけでタスクを依頼でき、定期レポート生成・サブスクリプション費用チェック・学校通知の日次サマリーなど**長時間タスク**を自律実行。__アシスタントから能動的パートナーへ__の質的転換。',
         'Google Antigravityのエージェントハーネスをベースに構築。20TBクラウド＋**YouTube Premium**も同梱で、**$100/月**プランへの引力を高める。__エコシステム占有率が真の戦場__へ移行した。',
       ],
       'related': None},
      {'score': 85, 'time': '07:00', 'source': 'StartupHub.ai',
       'title': '4社が5日で買収──AI統合フェーズ到来、Anthropic $9000億バリュエーション・ARR $30B達成の異例成長',
       'url': 'https://www.startuphub.ai/ai-news/ai-news/2026/four-labs-four-acquisitions-ai-consolidation-may-2026/',
       'thumb': 'https://d15shllkswkct0.cloudfront.net/wp-content/blogs.dir/1/files/2026/05/Screenshot-from-2026-05-22-08-09-51.png',
       'bullets': [
         '5月第3週に**4つのAIラボが5日間で買収**されるという異例の集中が発生。AIの開発フェーズから__AIの統合・実装フェーズへ__と業界の重力が移動していることを示す。',
         '[[Anthropic]] は第2の**$300億ラウンド**でバリュエーションが**$9000億超**に到達。ARRが2月の$140億から4月には**$300億**へと12週で2倍増という急成長が資金調達を後押し。',
         '[[OpenAI]] の$1220億ラウンド（$8520億時価）に対抗する構図で、**VC・戦略的投資家**の資金が両社に集中。__フロンティアAIは事実上3〜4社の寡占市場__に収束しつつある。',
       ],
       'related': None},
      {'score': 80, 'time': '06:30', 'source': 'Tech Analysis',
       'title': 'ChatGPT Workspace Agents発表──OpenAI、Claude Code・GitHub Copilotとエンタープライズ争奪戦に参戦',
       'url': 'https://pasqualepillitteri.it/en/news/1321/chatgpt-workspace-agents-openai-comparison-2026',
       'thumb': 'https://pasqualepillitteri.it/uploads/img/news/chatgpt-workspace-agents-openai-2026.png',
       'bullets': [
         '[[OpenAI]] がChatGPT Workspace Agentsを発表。**Slack・Outlook・Google Workspace**に直接統合したエージェントが、会議メモの自動処理・メール起草・タスク管理を自律実行する機能を提供。',
         '**Claude**（Anthropic）・**Copilot**（Microsoft）・**Gemini**（Google）が先行展開していたエンタープライズ領域への本格参入。企業の**ワークフロー埋め込み**が主戦場となり、__単一の強いモデルから「業務システムとの統合度」勝負へ__移行した。',
         '価格は既存の**ChatGPT Team/Enterprise プラン**の延長で追加費用なし。**大手SaaS**との統合深度が採用の鍵となる局面。',
       ],
       'related': None},
      {'score': 75, 'time': '05:00', 'source': 'LLM Stats',
       'title': 'Qwen 3.7 Max登場──GPT-5.5・Claude Opus 4.7と三強体制へ、LLMリーダーボードが2週で2回更新',
       'url': 'https://llm-stats.com/llm-updates',
       'thumb': 'https://llm-stats.com/og/main.png',
       'bullets': [
         '[[Alibaba]] が **Qwen 3.7 Max** を発表。GPT-5.5（OpenAI）・Claude Opus 4.7（Anthropic）・Gemini 3.1 Pro（Google）に並ぶ**最上位推論クラス**を主張し、数学・コード生成ベンチマークで競合と拮抗。',
         '5月に入ってからLLMリーダーボードが**2週間で2回刷新**された（5/19 Google I/O → 5/20 Qwen 3.7 Max）。__競争サイクルの高速化が加速__し、6ヶ月前の最高性能モデルが「普通」になる速さ。',
         '中国系モデルの進化はDeepSeek V4に続く流れ。米中の**半導体輸出規制**下でも推論効率の向上で対抗しており、__エコシステム獲得が次の差別化要因__との指摘が増加。',
       ],
       'related': None},
    ]
  },
  {
    'id': 'it', 'name_jp': 'IT-Consulting', 'name_en': 'IT &amp; Consulting', 'glyph': '▲',
    'accent': '#2E6B52',
    'summary': 'OpenAI DeployCoがコンサル業界に直接参入し、PwC・Accentureへの著名VC警告が浮上。富士通・NTTデータは国内軸足でAI実装を加速。Big 4もAI本番移行で競争激化。',
    'articles': [
      {'score': 93, 'time': '06:00', 'source': 'eWeek',
       'title': 'OpenAI DeployCo始動──$40億・FDE 150名でアクセンチュア・マッキンゼーのエンタープライズ聖域に直接参入',
       'url': 'https://www.eweek.com/news/openai-deployco-enterprise-ai-consulting/',
       'thumb': 'https://assets.eweek.com/uploads/2026/05/openai-consulting.jpg?f=jpeg',
       'bullets': [
         '[[OpenAI]] が5月11日、エンタープライズAI実装専業子会社「**OpenAI Deployment Company (DeployCo)**」を正式立ち上げ。初期投資は**$40億超**、TPG・Goldman Sachs・Bain Capital・マッキンゼーら19社が出資参画。',
         '**FDE（Forward Deployed Engineers）150名**が顧客企業に直接常駐し、AIワークフローを既存システムに組み込む——これはアクセンチュア・マッキンゼーが数十年間独占してきたビジネスモデルそのもの。__コンサル業界が自ら育てた競合__が牙を剥く構図。',
         'Tomoro社の買収でFDE人材を即時補強。企業の「**実験的AI → 本番運用**」移行を年単位から月単位に短縮する狙いで、**$3750億のエンタープライズIT市場**を直接狙う。',
       ],
       'related': None},
      {'score': 85, 'time': '07:30', 'source': 'crypto.news',
       'title': 'Chamath「PwC・AccentureはOpenAI/Anthropicを組織に入れるな」──コンサル業界の存亡リスクを著名VCが警告',
       'url': 'https://crypto.news/vc-warns-pwc-accenture-deploying-openai-anthropic-is-letting-the-fox-in-the-hen-house/',
       'thumb': 'https://media.crypto.news/2026/05/openai-1.webp',
       'bullets': [
         '5月17日、著名VC [[チャマス・パリハピティヤ]] が [[PwC]] と [[アクセンチュア]] を名指し：「OpenAIとAnthropicを自組織に導入するのは**ニワトリ小屋にキツネを入れる**のと同じ」と警告。両社はすでにコンサル事業の直接競合を資金援助している。',
         'アクセンチュアとPwCは各々OpenAI・Anthropicとの**戦略的パートナーシップ**を深化させているが、そのパートナー企業が自社の競合になるリスクを内包。__パートナーシップの蜜月が終わるとき__どう身を守るかが問われる。',
         'コンサル業界は「**AIと競争するか、AIに取り込まれるか**」の二択に直面。McKinsey・Bainは逆にDeployCoへの出資で**敵と組む**道を選んだ。',
       ],
       'related': None},
      {'score': 80, 'time': '07:00', 'source': 'ITmedia オルタナティブブログ',
       'title': '富士通のAI駆動開発データフライホイール──医療・自治体向け全67製品が「法改正即日対応」へ標準化',
       'url': 'https://blogs.itmedia.co.jp/serial/2026/02/ainttnec.html',
       'thumb': 'https://blogs.itmedia.co.jp/serial/assets_c/2026/02/bd17472345425c80bc9566eb7f9f7cb85e940d1a-thumb-660x562-64906.png',
       'bullets': [
         '[[富士通]] が2026年度末を目標に、医療・自治体向け全**67種ソフトウェア**をAI駆動開発プラットフォームへ移行する計画を進行中。「**法改正への即日対応**」をSLAレベルで標準化する野心的な構想。',
         '鍵は**データフライホイール**戦略：AI開発のアウトプットが次の学習データとなる自己強化ループ。NTTデータ・NECとの差別化軸として、__行政・医療という規制業種での実装速度__が勝負になる。',
         '富士通のアプローチが成功すれば、アクセンチュア・NTTデータ・NECが**競合の土俵**に引き込まれる可能性。__垂直統合の壁を崩すAI生産性__の競争が本格化する。',
       ],
       'related': None},
      {'score': 78, 'time': '06:30', 'source': '日経クロステック',
       'title': 'NTTデータ新社長が語る野望──アクセンチュアを超えるフルスタック戦略と課題を率直に語る',
       'url': 'https://xtech.nikkei.com/atcl/nxt/mag/nc/18/020600001/062700011/',
       'thumb': 'https://xtech.nikkei.com/atcl/nxt/mag/nc/18/020600001/062700011/topm.jpg?20220512',
       'bullets': [
         '[[NTTデータ]] の新社長は「**アクセンチュアに勝る強み**」として、SIとコンサルの融合による**フルスタック一括支援体制**を強調。AI中核戦略でFY2030にEBITDA **1.2兆円**を目指す。',
         '強みは**日本の公共・金融分野**での深い信頼関係と長期保守実績。弱点として率直に認めたのは「**人材の質・速度感・グローバル展開力**」でアクセンチュアに及ばない点。__弱みを認識した上での差別化戦略__が問われる。',
         'AIVistaプラットフォームでエージェントAIの業務組込みを**FY2026 Q2から先行展開**。__SIからAIプラットフォーマーへ__の転換が鮮明に打ち出された。',
       ],
       'related': None},
      {'score': 75, 'time': '06:00', 'source': 'Bloomberg Tax',
       'title': 'Big 4会計事務所、AIで企業サービス刷新──EY・デロイト・PwC・KPMGが本番移行を加速',
       'url': 'https://news.bloombergtax.com/financial-accounting/big-four-firms-embrace-ai-to-revamp-corporate-service-offerings',
       'thumb': 'https://bwrite-static.bloombergindustry.com/dims4/default/0f207ac/2147483647/legacy_thumbnail/960x370%3E/quality/90/?url=https%3A%2F%2Fbloomberg-bna-brightspot.s3.us-east-1.amazonaws.com%2F4e%2F3a%2F666cd2094d3f8c14a871eaf7c141%2Fbli-2023-the-big-four-3d.png',
       'bullets': [
         '[[EY]]・[[デロイト]]・[[PwC]]・[[KPMG]] の**Big 4**がAIを活用した企業向けサービスの刷新を本格加速。監査・税務・アドバイザリーの各領域でAI自動化を進め、少ない人員で**高マージン**を実現するビジネスモデルへ転換中。',
         '**エージェントAIの実用化**で、従来のコンサルタントが担っていた情報収集・文書作成・規制照合が自動化可能に。__人海戦術型の大型ファームが自己否定を迫られる__逆説。',
         'Big 4は**監査法人ライセンス**と**機密データへのアクセス**という固有の参入障壁を持ち、AIとの組み合わせで__レギュレーション・アドバイザリー市場を独占しやすい__位置にある。',
       ],
       'related': None},
    ]
  },
  {
    'id': 'economy', 'name_jp': '経済', 'name_en': 'Economy', 'glyph': '■',
    'accent': '#8E2A19',
    'summary': 'Micron時価総額1兆ドル突破がNASDAQ史上最高値を牽引。日経は65,408円の高値更新後に利益確定。5/28 FOMC議事録と5/30 PCEが今週の分岐点。',
    'articles': [
      {'score': 92, 'time': '06:00', 'source': 'Motley Fool',
       'title': 'Micron時価総額1兆ドル突破──AI楽観論がS&P500（7,519）・NASDAQ（26,656）を史上最高値へ押し上げ',
       'url': 'https://www.fool.com/coverage/stock-market-today/2026/05/26/stock-market-today-may-26-micron-surges-after-ubs-lifts-price-target-on-ai-optimism/',
       'thumb': 'https://g.foolcdn.com/image/?url=https%3A%2F%2Fcdn.content.foolcdn.com%2Fimages%2F1umn9qeh%2Fproduction%2F446dde0c7af61257ba43e10a3a4377ea2eab4d36-300x180.png%3Fw%3D800%26q%3D75%26fit%3Dmax%26auto%3Dformat&w=1200&op=resize',
       'bullets': [
         '[[Micron Technology]] が**+19%急騰**でついに**時価総額1兆ドル**を突破。UBSがAI楽観論を背景に目標株価を大幅引き上げたことが契機。**S&amp;P500は7,519**・**NASDAQは26,656**と揃って史上最高値を更新。',
         'AI半導体・データセンター需要の強さが米企業業績を牽引。**S&amp;P500全体のQ1 EPSは+27%**（4年ぶり好決算）の恩恵が今も市場心理を強気に保つ。__半導体がバロメーターとなる相場構造__が定着。',
         'ただし**ダウ工業株は-118ドル**と逆行。__テック集中vs.景気敏感の二極化__が一段と鮮明で、割高懸念も燻る。5/28 FOMC議事録が過熱を冷ます材料になり得る。',
       ],
       'related': None},
      {'score': 86, 'time': '07:00', 'source': 'Trading Key',
       'title': '日経平均65,408円史上最高値後に利益確定──SoftBank 8%超高が支え、5/28 FOMC議事録控えた様子見',
       'url': 'https://www.tradingkey.com/analysis/stocks/more/261926903-ni225-jpy-softbank-tradingkey',
       'thumb': 'https://resource.tradingkey.com/cms_uploads/img/20231120/4ada2d4406e80607391d4ebb9cb7bdb9.jpg',
       'bullets': [
         '5月26日、日経平均は**65,408円**と史上最高値をつけた後に反落。高値圏での利益確定売りが優勢となるも、[[SoftBank]] が**+8%超**と急騰し下値を支えた。**ARM展開加速とAI投資評価**が背景。',
         '5/28 FOMC議事録公表を控え、投資家は利益確定と**様子見**のバランスを模索。過去最大の上げ幅（**+3,320円**）を記録した直後だけに、__高値と足踏みの狭間__での判断が難しい。',
         '日本株の上昇を支えるのは**AI・半導体関連の寄与度上昇**と**円安効果**。野村証券は年末目標を**6.3万円**に引き上げ、上振れシナリオでは**7万円台**を想定する。',
       ],
       'related': None},
      {'score': 82, 'time': '05:30', 'source': 'CNBC',
       'title': 'ホルムズ海峡再開交渉でアジア株高・原油急落──インフレ鎮静化への期待が台頭',
       'url': 'https://www.cnbc.com/amp/2026/05/25/asia-markets-today-live-updates-asx-nikkei-sensei-iran-us-trump-oil-hormuz.html',
       'thumb': 'https://image.cnbcfm.com/api/v1/image/102374303-1779669624837-GettyImages-465836965.jpg?v=1779669640',
       'bullets': [
         '米イランの**ホルムズ海峡再開交渉**が進展との観測で、アジア市場が5/26朝方に一斉高。日経平均の史上最高値更新もこの**原油急落観測**（$106台→急落）が追い風となった。',
         'エネルギーコスト低下は**企業コスト削減と消費回復**を後押しする方向。FRBの利下げシナリオにも「__インフレ収束が前倒しになる可能性__」を示す材料として機能し始めている。',
         'ただし地政学リスクは依然不安定で、**中東情勢の悪化**に転じれば即座に巻き戻しも。__原油価格は最も予測困難な変数__として市場参加者が警戒を緩めない。',
       ],
       'related': None},
      {'score': 80, 'time': '06:30', 'source': '日本経済新聞',
       'title': '米企業4年ぶり好決算、S&P500全体で3割増益──テック・AI主導の増益が予想を大幅超過',
       'url': 'https://www.nikkei.com/nkd/company/us/MU/news/?DisplayType=1&ng=DGXZQOGN071LB007052026000000',
       'thumb': 'https://article-image-ix.nikkei.com/https%3A%2F%2Fimgix-proxy.n8s.jp%2FDSXZQO2986254007052026000000-3.jpg?auto=format%2Ccompress&ch=Width%2CDPR&crop=focalpoint&fit=crop&fp-x=0.5&fp-y=0.5&fp-z=1&h=500&ixlib=java-1.2.0&w=800&s=9f29ab633c899c5381fab414bc46bf0f',
       'bullets': [
         'Q1決算発表が概ね完了し、S&amp;P500全体のEPS伸長率は**+27〜30%**と**4年ぶりの高水準**。テック・AI・半導体セクターが「**予想大幅超過**」の中心で、Micronの+19%急騰はその象徴。',
         '日経も同様の傾向で、**2027年3月期の強気業績予想**が日本株の高値を正当化する材料に。__バリュエーション高でも業績が伴う相場__という強気派の論拠が揃い始めた。',
         'リスクは**金利高止まり長期化**と**関税コスト**の後半期への波及。今後の注目は**5/28 FOMC議事録**でタカ派発言が確認されるかどうか。',
       ],
       'related': None},
      {'score': 77, 'time': '05:00', 'source': 'OANDA Japan',
       'title': '5/28 FOMC議事録・5/30 PCEが今週の焦点──タカ・ハト温度差と利下げ路線の行方',
       'url': 'https://www.oanda.jp/lab-education/beginners/fundamentals_analysis/fomc-meeting/',
       'thumb': 'https://storage.googleapis.com/oanda-prod-asne1-oj-jp-wordpress/2018/11/d2fb5b70699e1b5c7232e64d1bbafa24.jpg',
       'bullets': [
         '今週の最大イベントは**5/28（木）公表のFOMC議事録**。[[Warsh新議長]]のタカ派スタンスとFOMC内のハト派の温度差が浮き彫りになれば、__ドル円・債券・株式の三市場を同時に動かす__変数となる。',
         '加えて**5/30（土）公表のPCEデフレーター**がFRBの利下げ判断の直接材料。前月比での**コアPCE上昇率**が市場予想（0.3%）を上回れば年内利下げシナリオが後退。',
         'ホルムズ再開・原油安を受けて、**エネルギー価格の低下**がPCEを押し下げる方向に作用する可能性もあり、__今週の2指標で年後半の方向感が決まる__。',
       ],
       'related': None},
    ]
  },
]

reflection = {
  'title': '半導体と利上げの分岐点',
  'subtitle': 'AIの技術フロンティアとエネルギー地政学が同時に動く週、FOMCとPCEが全市場の針路を決める',
  'lead': '本日4分野・20本超のニュースから浮かび上がる最大のテーマは [[AI統合フェーズへの移行]] と [[地政学リスク緩和の好機]] の同時進行である。Claude MythosがProject Glasswingで1万件超の重大脆弱性を自律発見し、AIの能力が安全保障領域に侵食。OpenAI DeployCoはコンサル業界の心臓部に参入し、PwC・Accentureの地位を揺るがす。一方でホルムズ海峡再開交渉が進み、原油安・株高の連鎖がFRBの利下げ路線を早める好機ともなり得る。__5/28 FOMC議事録と5/30 PCEが、このすべての行方を左右する。__',
  'pull_quote': '「単一の強い製品」から「エコシステムでの占有率」へ──AIの戦場が技術からビジネスプロセスへと完全に移った一日。',
  'sections': [
    {'tag': '総論', 'heading': 'AI実装・地政学・金融政策が同時に動く複合週', 'accent': '#1A1A1A',
     'body': '本日4カテゴリのニュースは、AIが「技術」から「インフラ」へ成熟しつつある段階を如実に示す。[[Claude Mythos]]の脆弱性自律発見は**AI安全保障の主戦場**が変わった証左であり、OpenAI DeployCoはコンサルという**人間産業の核心**に斬り込んだ。同時にホルムズ海峡再開交渉は原油安を通じてインフレ鎮静化を後押しし、__全市場が5/28 FOMC議事録待ち__というフェーズへ突入した。'},
    {'tag': '為替', 'heading': '158円台の膠着、FOMC待ちで方向感なく週明けへ', 'accent': '#B8860B',
     'body': '[[USD/JPY]]は158円台後半で膠着。**Warsh議長の利下げ封印**スタンスとBOJの追加利上げ模索という構図は変わらず、__均衡なき円安が継続__する。160円の介入ラインへの再接近が意識される中、EUR/USDはECBの6月利下げ観測でドル高を一部緩和。**GBP/USD**は英賃金5.5%高止まりでBOE早期利下げ期待が後退し、ポンドが支えられた。__方向感なく週明けへ__突入する中、**5/28 FOMC議事録**が次の焦点となる。'},
    {'tag': 'AI', 'heading': 'Mythos・Spark・DeployCoで「AIが人間の領域に踏み込む」週', 'accent': '#2D5BB8',
     'body': 'Claude Mythosが**1万件超の重大脆弱性**を自律発見した事実は、__AIが人間の専門領域に踏み込む段階__を象徴する。Gemini Sparkの24時間パーソナルエージェント化はGmailを通じた**個人の仕事プロセス**への深い侵入であり、Anthropicの**$9000億バリュエーション**到達は**AIの価値形成速度**が人類の認識を超えた証左だ。__エコシステム占有率が真の戦場__へ移ったいま、モデル性能の比較は脇役に。'},
    {'tag': 'IT', 'heading': 'OpenAI DeployCoがコンサル聖域に参入', 'accent': '#2E6B52',
     'body': '[[OpenAI DeployCo]]の$40億規模での参入は、アクセンチュア・マッキンゼーという**コンサル業の核心**への直接攻撃を意味する。Chamathの警告はその逆説——__PwC・Accentureが自らを食う立場の企業と組んでいる__——を鋭く突く。富士通は医療・自治体向けの垂直特化でAI実装を加速、NTTデータは新社長の下でアクセンチュア超えを宣言し、**国内2強の棲み分け**が試される年に。'},
    {'tag': '経済', 'heading': 'Micron 1兆ドル・日経65,408円・5/28 FOMC議事録が今週を決める', 'accent': '#8E2A19',
     'body': 'Micronの時価総額**1兆ドル**突破がNASDAQ史上最高値（**26,656**）を牽引し、日経平均も**65,408円**で過去最高を更新後に利益確定。__高値と足踏みの狭間__で市場は次の材料を待っている。ホルムズ海峡再開交渉によるエネルギー安は**インフレ鎮静化**への近道として好感され、**5/28 FOMC議事録**・**5/30 PCE**の二指標が__今後6ヶ月の市場の針路を決定する__。'},
    {'tag': 'ゲーム', 'heading': '本日休載（次回5/28木）', 'accent': '#5E3D8C',
     'body': 'ゲーム関連は本日（水曜）休載。**火・木・土・日**がGame掲載日。次回は5/28（木）に、Nintendo・SQUARE ENIXを中心とした最新動向をお届けする。PlayStation State of Play（6/2）に向けたSIEの事前情報が出始める可能性があり、__5/28は今週最大の情報密度の日__になる見通し。'},
    {'tag': '明日へ', 'heading': 'FOMC議事録・ゲーム再開・AI続報', 'accent': '#C9B98A',
     'body': '明日（5/28木）は**FOMC議事録公表**という最重要イベントが控える。[[Warsh議長]]のタカ派発言の程度次第でドル円・米金利・株式の全市場が同時に動く可能性。AIでは**Gemini SparkのAI Ultra展開**と**Claude Mythos続報**（Project Glasswing）に注目。ゲームカテゴリも再開、**PlayStation State of Play（6/2）**に向けたSIEの事前情報が出始める可能性がある。__5/28は今週最大の情報密度の日__になる見通し。'},
  ],
  'takeaways': [
    {'num': '01', 'tag': '為替', 'color': '#B8860B',
     'text': '[[USD/JPY]] 158円台で方向感なく週明けへ。**5/28 FOMC議事録**がドル高・円安の次の基準点を決める——タカ派確認なら160円接近・介入リスク、ハト派なら円高反転の二択。'},
    {'num': '02', 'tag': 'AI', 'color': '#2D5BB8',
     'text': '[[Claude Mythos]]が1万件超の脆弱性を自律検出。**AIの戦場はモデル性能から「実装・安全・エコシステム統合」へ**本格シフト——エコシステム占有率が真の競争軸に。'},
    {'num': '03', 'tag': '産業', 'color': '#2E6B52',
     'text': '[[OpenAI DeployCo]]のコンサル参入でIT大手の収益モデルが根底から問われる局面。**富士通・NTTデータの国内特化戦略**が試される——AI実装速度で差別化できるかが分岐点。'},
  ],
  'related': [
    {'date': '2026-05-26', 'title': '前号: AI Dreaming・日経65000円突破・NTTデータ新戦略'},
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
print(f'✅ 生成完了: build/email.html ({size_kb:.1f} KB) — {total_stories} stories / {len(cats)} categories')
