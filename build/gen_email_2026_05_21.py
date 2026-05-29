"""2026-05-21 号 HTML メール生成スクリプト"""
import re, sys, os
sys.stdout.reconfigure(encoding='utf-8')
os.chdir(os.path.dirname(os.path.abspath(__file__)) + '/..')

ISSUE_DATE = '2026-05-21'
ISSUE_NO   = '20260521'
WEEKDAY    = '木'
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
    'summary': "Moody's格下げ後もドル一強が継続しUSD/JPY 159円台を維持。BOJ4月議事要旨でインフレ予測2.8%上方修正・タカ派3名が1.0%利上げを主張し7月利上げシナリオが固まる。",
    'articles': [
      {'score': 93, 'time': '08:00', 'source': 'IG Japan',
       'title': "ドル高の本質: Moody's格下げ後もドル需要が衰えない理由 — 安全資産性と金利差の二重効果",
       'url': 'https://www.ig.com/jp/news-and-trade-ideas/jpy-stays-weak-even-after-boj-show-off-hawkish-messages-260428',
       'thumb': 'https://a.c-dn.net/c/content/dam/publicsites/igcom/uk/images/news-article-image-folder/bb_USDJPY_Japan_flag_14_11_2024.jpg/jcr:content/renditions/cq5dam.web.1280.1280.jpeg',
       'bullets': [
         "[[Moody's格下げ]]後、ドル安を期待した市場の一部は裏切られた。USD/JPY 159円台が維持されている背景には「[[安全資産としてのドル]]需要」と「日米525bpの金利差」が二重に作用している構造がある。",
         "米国財政の長期悪化（GDP比債務134%/2035年見通し）はドル売り材料だが、__代替通貨が存在しない中でドルに替わる安全資産が見当たらない__という現実が売りを抑制している。",
         "160円介入ラインへの接近とともに財務省の口先介入頻度が増加。実弾介入の現実味は高まっているが、発動タイミングの不確実性がむしろボラティリティを高めている。",
       ],
       'related': {'axis': '続報', 'ref_title': '5/20号: ドル円159.09円で6連騰', 'ref_date': '2026-05-20', 'note': "Moody's後もドル高が維持されるという新たな局面へ移行。"}},
      {'score': 89, 'time': '09:30', 'source': '日本銀行',
       'title': 'BOJ: コアインフレ予測2.8%に上方修正、タカ派3名が1.0%利上げ主張 — 7月実施シナリオが優勢に',
       'url': 'https://www.boj.or.jp/statistics/market/forex/fxdaily/index.htm',
       'thumb': None,
       'bullets': [
         '日銀4月会合の議事要旨で、政策委員9名中3名が「直ちに[[0.75%→1.0%]]への利上げ」を主張していたことが明らかになった。コアCPIの年度予測が1.9%から[[2.8%]]へ大幅に上方修正されたことが根拠。',
         '反対3名の発言は「過半数による7月利上げ支持」への地ならしと市場は読む。FOMCが利下げを先送りする中で日銀が利上げすると__日米金利差が縮小し理論的には円高圧力__が生まれる。',
         '市場は7〜9月の利上げ実施を60%以上の確率で織り込み始め、OIS（翌日物金利スワップ）に変化が出ている。実現すれば0.75→1.0%と2000年代初頭以来の高水準に達する。',
       ],
       'related': {'axis': '続報', 'ref_title': '5/17号: 日銀0.75%据え置き・3名反対', 'ref_date': '2026-05-17', 'note': '今回の議事要旨はその3名の主張の根拠が判明した続報。'}},
      {'score': 83, 'time': '07:15', 'source': 'ECB',
       'title': 'EUR/JPY 183円高止まり — ECBの次の一手が見えずユーロ円が方向感を失う',
       'url': 'https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/eurofxref-graph-jpy.en.html',
       'thumb': None,
       'bullets': [
         'ECBは4月に利下げを実施し政策金利を2.00%に引き下げたが、その後のインフレ指標が鈍化と再上昇を繰り返し、[[6月の追加利下げ]]シナリオが不透明化している。EUR/JPYは183円台で推移。',
         'ユーロ側の安定と円安が重なり合い急変しにくい環境だが、__ECBが利下げを続ければユーロ安・円安で相殺し合いレンジ継続__というトレーダーのコンセンサスが形成されつつある。',
         '日銀が7月に利上げした場合はEUR/JPYが一時180円割れも視野。クロス円全体での方向転換に備えたリスク管理が重要になっている。',
       ],
       'related': None},
      {'score': 78, 'time': '16:30', 'source': 'Reuters',
       'title': 'GBP/USD 1.3401 — 英失業率5%・賃金鈍化でポンドがドル全面高の波に屈する',
       'url': 'https://wise.com/us/currency-converter/usd-to-jpy-rate/history',
       'thumb': None,
       'bullets': [
         '英国の直近労働統計は失業率[[5.0%]]（2年ぶり高水準）、賃金上昇率4.3%（鈍化傾向）と、BOEの利下げを後押しするデータが相次いでいる。GBP/USDは1.3401まで軟化。',
         '__BOEが6月に利下げを決定すれば1.32まで下落余地がある__とテクニカル分析は指摘。ポンドはユーロとの連動性も高く、ECBの動向にも左右される脆弱な状況にある。',
         'ドル全面高の中でポンドの下落が目立つのは英国固有の景気鈍化懸念が加わっているため。Brexit後の欧州単一市場へのアクセス制限による輸出競争力低下も長期的な構造要因として意識される。',
       ],
       'related': None},
      {'score': 72, 'time': '06:30', 'source': 'IG International',
       'title': 'AUD/USD: RBA利下げ観測後退とコモディティ需要で底堅さ維持',
       'url': 'https://www.ig.com/en/news-and-trade-ideas/forex-market-outlook-for-2026-251211',
       'thumb': None,
       'bullets': [
         'RBAは3月の利下げ後、4月以降のCPI高止まりを受けて追加利下げを一旦停止。これがAUD/USDを対ドル比で0.63〜0.65レンジに安定させている。',
         '[[鉄鉱石・石炭]]の対中輸出が堅調で、コモディティ通貨としての豪ドルを下支えている。__中国の景気刺激策が継続する間は下値が限られる__という構造は変わっていない。',
         'ドル高傾向の中で相対的に堅調なAUDは、対円ではAUD/JPYが上昇基調。日本の個人投資家によるキャリートレードの新たな対象として豪ドルへの注目が高まっている。',
       ],
       'related': None},
    ]
  },
  {
    'id': 'ai', 'name_jp': 'AI', 'name_en': 'Artificial Intelligence', 'glyph': '◆',
    'accent': '#2D5BB8',
    'summary': "Google I/O 2026余波が続く中、Gemini SparkとGemini Omniが「always-on AIエージェント」時代の到来を宣言。AnthropicはProject GlasswingでClaude MythosによるOS・ブラウザの数千件ゼロデイ発見を公開した。",
    'articles': [
      {'score': 96, 'time': '07:00', 'source': 'TechCrunch',
       'title': 'Gemini Spark発表: 24時間稼働パーソナルAIエージェントがGmail統合でタスク自律化',
       'url': 'https://techcrunch.com/2026/05/19/google-introduces-gemini-spark-a-24-7-agentic-assistant-with-gmail-integration/',
       'thumb': 'https://techcrunch.com/wp-content/uploads/2026/05/spark.jpg?resize=1200,673',
       'bullets': [
         'GoogleのCEO Sundar Pichaiが「AIが補助するのではなく自律的に動くエージェント時代が始まった」と宣言し、[[Gemini Spark]]を発表。Gmail・Calendar・Docsを横断して長期複数ステップのタスクを自律実行する24時間稼働の個人AIエージェント。',
         '来週からGoogle AI Ultra加入者（月額$200〜）向けに提供開始予定。__デジタルライフを全面再設計するほぼ手放し運用__を実現するとされ、Anthropic Claude SonnetやOpenAI GPT-5.5との三極競争に「always-on」という新軸が加わった。',
         'バックグラウンドで動き続けるAIはプライバシーとデータ管理の論争に火を付ける。EUのAI法との整合性や個人情報保護規制への対応が今後数ヶ月の最大の争点になるとみられる。',
       ],
       'related': None},
      {'score': 91, 'time': '07:10', 'source': 'CyberNews',
       'title': 'Gemini Omni: テキスト・画像・動画をあらゆるモダリティで入出力 — マルチモーダルの新水準',
       'url': 'https://cybernews.com/ai-news/google-io-2026-gemini-omni-antigravity-agentic-ai/',
       'thumb': 'https://media.cybernews.com/images/featured-big/2026/05/Googleio2026keynote.jpg',
       'bullets': [
         '[[Gemini Omni]]はGoogleが発表した新世代マルチモーダルモデル。テキスト・画像・動画・音声の任意の組み合わせで入力と出力に対応し、まず動画生成から開放、画像・テキスト出力も順次提供予定。',
         'Gemini Omniは検索UIの[[25年ぶり大刷新]]とも連動し、検索ボックスをAIエージェントの入口に変革。__Google検索が情報取得から「課題解決プラットフォーム」に変わる__歴史的な分岐点として注目される。',
         '競合するOpenAI Sora 2・Anthropic Mythosと比較して動画→テキスト変換の精度が特に高いと早期テスターが報告。クリエイター・マーケティング・映像制作業界への波及が大きい。',
       ],
       'related': {'axis': '続報', 'ref_title': '5/19号: Google I/O 2026開幕・Gemini Intelligence発表', 'ref_date': '2026-05-19', 'note': '製品名確定・詳細仕様の続報。'}},
      {'score': 87, 'time': '08:30', 'source': 'Security Boulevard',
       'title': 'Anthropic Project Glasswing: 知見共有を解禁 — Claude MythosがOS・ブラウザ数千件のゼロデイを発見',
       'url': 'https://securityboulevard.com/2026/05/anthropic-allows-glasswing-partners-to-share-mythos-based-findings/',
       'thumb': None,
       'bullets': [
         'Anthropicが[[Project Glasswing]]パートナーへの知見共有制限を緩和。主要OS（Windows/macOS/Linux）と主要ブラウザ（Chrome/Firefox/Safari）に存在する[[数千件のゼロデイ脆弱性]]情報が業界横断で共有可能になった。',
         '[[Claude Mythos Preview]]は数週間でこれらの脆弱性を自律的に発見。従来の人手によるペネトレーションテストを__根本から置き換える可能性__が示され、セキュリティ産業の構造変化が始まっている。',
         'AWS・Apple・Cisco・Google・Microsoft・NVIDIAなど業界横断コンソーシアムが知見を集約。サイロ化した脅威インテリジェンスを共有することで攻撃者より先を行く防御モデルが構築される。',
       ],
       'related': None},
      {'score': 83, 'time': '09:00', 'source': 'GSMArena',
       'title': 'Verizon、Project Glasswingに参加 — 通信インフラでClaude Mythos実証テスト開始',
       'url': 'https://www.gsmarena.com/verizon_joins_project_glasswing_to_test_anthropics_claude_mythos_model_on_its_infrastructure-news-72850.php',
       'thumb': None,
       'bullets': [
         '米最大手通信事業者[[Verizon]]がAnthropicのProject Glasswingに加入し、Claude Mythos Previewを通信インフラのセキュリティテストに活用する実証を開始。IT/金融セクターから通信へとGlasswingが拡大した。',
         '通信インフラは国家安全保障と直結するため、AI主導のセキュリティテストが業界標準になれば影響は甚大。__通信キャリア発の知見がGlasswingを強化する__好循環が期待される。',
         'Claude Mythosの「攻撃的能力」の管理体制への問いも高まっており、セキュリティAIの倫理的ガバナンスが新たな議論の場に。同モデルは現在一般公開なし（招待制Preview）の状態が維持されている。',
       ],
       'related': None},
      {'score': 79, 'time': '07:30', 'source': 'Google Developers Blog',
       'title': 'Google Antigravity 2.0: サブエージェント並列化・Git保護でエンタープライズ開発を加速',
       'url': 'https://developers.googleblog.com/all-the-news-from-the-google-io-2026-developer-keynote/',
       'thumb': 'https://storage.googleapis.com/gweb-developer-goog-blog-assets/images/GoogleForDevelopers-ComboIO-Wagta.2e16d0ba.fill-1200x600_984jqJ0.png',
       'bullets': [
         '[[Antigravity 2.0]]は複雑なワークフローを複数の専門サブエージェントに分割して並列処理する能力を備える。端末間の自動サンドボックス・クレデンシャル保護・Gitポリシー適用が標準装備。',
         'Antigravity CLIからワンコマンドでマルチエージェント環境を立ち上げられるため、__単一エンジニアによる大規模コードベース管理が現実的__な水準に達した。',
         'Gemini Spark（個人向け）とAntigravity 2.0（開発者向け）を両翼に、Googleはエンドユーザーから企業エンジニアまでを取り込む二段戦略を鮮明にした。Anthropic Claude CodeやOpenAI Codexとの競合が激化する。',
       ],
       'related': None},
    ]
  },
  {
    'id': 'it', 'name_jp': 'IT-Consulting', 'name_en': 'IT & Consulting', 'glyph': '▲',
    'accent': '#2E6B52',
    'summary': 'NTTデータが2026年度中の生成AI全工程技術投入・工数70%削減へ。NTTデータ経営研究所が金融AI導入コンサル18サービスを本格展開。OpenAI Frontier×4大コンサルの役割分担が3ヶ月で定着。',
    'articles': [
      {'score': 91, 'time': '07:00', 'source': '日本経済新聞',
       'title': 'NTTデータ: 2026年度中に生成AIがシステム開発の全工程をほぼ担う技術投入 — 工数70%削減へ',
       'url': 'https://www.nikkei.com/article/DGXZQOUC254OB0V21C25A2000000/',
       'thumb': None,
       'bullets': [
         '[[NTTデータ]]は2026年度末までに生成AIを使ったシステム開発の自動化を本番環境に投入する計画を発表。要件定義からデプロイまでの工数を2025年比で[[50%削減]]（既存ツール30%+AI20%）し、2030年には[[70%削減]]を目指す。',
         '国内SIerの人材不足が深刻化する中、__生成AIによるシステム開発の自動化は業界全体の変革トリガー__になり得る。競合する富士通・NEC・IBMコンサルティングも対応を迫られる状況に。',
         'OpenAI・AnthropicとのAPI連携基盤も整備しており、顧客向けAIコンサルティングサービスも同時拡充する方針。グローバルITコンサル市場争奪戦が国内でも激化する。',
       ],
       'related': {'axis': '復状', 'ref_title': '5/19号: NTT Data 2030 AIVista長期戦略', 'ref_date': '2026-05-19', 'note': '長期戦略発表から今回の具体的な2026年度技術投入計画へ。'}},
      {'score': 87, 'time': '08:00', 'source': 'NTTデータ経営研究所',
       'title': 'NTTデータ経営研究所: 金融機関向けAI導入コンサルを本格展開 — 18サービスで銀行・保険を主要ターゲットに',
       'url': 'https://www.nttdata-strategy.com/newsrelease/260507/',
       'thumb': 'https://www.nttdata-strategy.com/images/ogp/ogp-common.jpg',
       'bullets': [
         '[[NTTデータ経営研究所]]が2026年5月7日から金融機関特化のAI導入コンサルティング18サービスの提供を開始。銀行・保険・証券の各セグメントに特化した導入支援・効果測定・ガバナンス設計まで一体で提供する。',
         '金融業界はAI規制整備が遅れており、__「AI導入が進む企業と止まっている企業」の二極化__が深刻。専門コンサルサービスはその差を埋める役割を担う位置づけ。',
         'BCG調査によれば2026年の企業AI投資の30%超がエージェント型AI専用予算に充当される見込みで、金融機関の需要は特に大きい。NTTデータは親会社の技術力と経営研のコンサル力を組み合わせた差別化を図る。',
       ],
       'related': {'axis': '続報', 'ref_title': '5/17号: NTTデータ経営研究所 金融AI18サービス', 'ref_date': '2026-05-17', 'note': 'サービス詳細追加・市場分析の続報。'}},
      {'score': 83, 'time': '09:00', 'source': 'Fortune',
       'title': 'OpenAI Frontier × 4大コンサル: 3ヶ月後の現在地 — Accenture&Capgeminiが実装、McKinsey&BCGが戦略で役割分担確立',
       'url': 'https://fortune.com/2026/02/23/openai-partners-with-mckinsey-bcg-accenture-and-capgemini-to-push-its-frontier-ai-agent-platform/',
       'thumb': None,
       'bullets': [
         '2月発表の[[OpenAI Frontier]]と4大コンサル提携から3ヶ月。Accenture・Capgeminiがシステム統合・実装を担い、McKinsey・BCGが経営変革のフレームワーク設計を担う役割分担が定着しつつある。',
         '[[Frontier Alliances]]は企業のAI導入を__「戦略から実装まで一気通貫で支援する新たな産業インフラ」__として機能し始めており、Uber・State Farm・Intuitなど初期ユーザーで成果事例が積み上がっている。',
         'OpenAIの企業向け売上は既に全体の40%を超え、2026年末には消費者向けと拮抗する見通し。コンサル経由の企業契約が収益の主柱になりつつある。',
       ],
       'related': {'axis': '続報', 'ref_title': '5/20号: OpenAI Frontier×4大コンサル提携', 'ref_date': '2026-05-20', 'note': '提携発表から3ヶ月後の実装状況確認。'}},
      {'score': 78, 'time': '06:30', 'source': 'CaseBasix',
       'title': 'Accenture売上$65B — AI実装型ファームがMBBの約2倍の成長率を達成、コンサル業界の版図が塗り替わる',
       'url': 'https://www.casebasix.com/pages/top-consulting-firms',
       'thumb': None,
       'bullets': [
         '2026年のコンサル業界最大手は[[Accenture]]（売上$650億）。AI実装・テック統合を中核とする同社はMcKinsey・BCG・Bainの成長率6〜8%に対し__約15%の高成長__を達成し、業界の勢力図が変わりつつある。',
         '戦略特化のMBBと実装型のAccenture・Capgeminiの二極化が完成。コンサル業界は「アイデアを売る時代」から「__テックで結果にコミットする時代__」に移行した。',
         '日本市場でも野村総研・NTTデータ・富士通コンサルといった国産SIerとの差が縮まりつつある。OpenAIとの提携深化を国内展開に活かせるかが試されている。',
       ],
       'related': None},
      {'score': 73, 'time': '06:00', 'source': 'RoadToOffer',
       'title': 'グローバルITコンサル市場、2026年に初の$3,750億超えへ — GenAIがアジア太平洋で最速拡大',
       'url': 'https://www.roadtooffer.com/blog/top-consulting-firms',
       'thumb': None,
       'bullets': [
         'グローバル経営コンサル市場は2025年に3,500億ドルを突破し、2026年は[[3,750億ドル]]超えが見込まれる。[[生成AIサービスライン]]が最速成長セグメントで、ほぼ全大手ファームで主力に育っている。',
         '地域別ではアジア太平洋が最速成長地域として台頭。インド・シンガポール・日本の需要が牽引し、__欧米コンサルが一斉にアジア拠点を強化__している動きが顕著。',
         '日本企業のDX遅延がかえって需要を生んでおり、国内市場だけでも年1兆円超のコンサル費用が使われると推計。外資系ファームの日本市場攻略が本格化しつつある。',
       ],
       'related': None},
    ]
  },
  {
    'id': 'economy', 'name_jp': '経済', 'name_en': 'Economy', 'glyph': '■',
    'accent': '#8E2A19',
    'summary': "Moody's格下げ余震で米30年国債利回り5.19%・S&P500が3連続安と市場が財政リスクを消化中。米4月CPI+3.8%でWarsh FRB議長の高金利維持路線が正当化され、野村証券は日経平均2026年末6万円に上方修正。",
    'articles': [
      {'score': 95, 'time': '06:00', 'source': 'NBC News',
       'title': "Moody's格下げ余震: 米30年国債利回り5.19%、S&P500が3連続安 — 財政赤字134%/GDPを市場が消化",
       'url': 'https://www.nbcnews.com/business/markets/markets-closing-numbers-monday-moodys-us-credit-downgrade-rcna207730',
       'thumb': 'https://media-cldnry.s-nbcnews.com/image/upload/t_nbcnews-fp-1200-630,f_auto,q_auto:best/rockcms/2025-05/250519-new-york-stock-exchange-ew-126p-6fb0d1.jpg',
       'bullets': [
         "[[Moody's]]が米国信用格付けをAaaからAa1に1ノッチ格下げ（5月16日）した余震が市場を揺さぶっている。米[[30年国債利回りが5.19%]]に上昇し、10年債も4.56%到達。モーゲージ金利が7%超に。",
         "S&P500の3日連続下落は累計1.8%にとどまり「サプライズでなく3度目の格下げ」として消化されつつある一方、__真の懸念はGDP比債務が2024年の98%→2035年の134%へ拡大しても財政改善策が示されないこと__。",
         '利払い費が歳出の20%超に達するシナリオが現実味を帯び、市場の「長期金利上昇リスク」への懸念が定着しつつある。国債への資金流入が減速すれば金融市場全体への連鎖リスクが高まる。',
       ],
       'related': {'axis': '続報', 'ref_title': '5/20号: S&P500 3連続安・30年債利回り5.19%', 'ref_date': '2026-05-20', 'note': '財政・市場動向の深堀り。'}},
      {'score': 90, 'time': '08:30', 'source': 'Bloomberg',
       'title': '米4月CPI+3.8% — エネルギー+17.9%が主因、Warsh FRB議長の高金利維持路線を正当化',
       'url': 'https://www.ig.com/jp/us-stock-market-analysis/2026-key-topics-of-us-equities-and-spx500-outlook-260104',
       'thumb': None,
       'bullets': [
         '米国4月消費者物価指数（[[CPI]]）は前年同月比+3.8%と市場予想を上回る高止まり。エネルギー価格が[[+17.9%]]と急騰し、コアCPIも+3.3%で依然として高水準を維持している。',
         'Warsh新FRB議長が就任時に示した「__データドリブンでインフレを最優先__」路線が早期に裏付けられた形となり、2026年内の利下げ回数見通しが0〜1回に絞り込まれた。',
         'エネルギー高の主因は中東緊張とイラン制裁強化に伴う原油供給制約。7月のFOMC前に発表される5・6月CPIが利下げ再開の可否を左右する最重要指標として注目されている。',
       ],
       'related': {'axis': '続報', 'ref_title': '5/19号: Warsh FRB議長就任・インフレ最優先宣言', 'ref_date': '2026-05-19', 'note': 'インフレ最優先宣言が今回のCPI+3.8%で正当化された。'}},
      {'score': 86, 'time': '07:00', 'source': 'Diamond ZAI',
       'title': 'S&P500、史上初7,500超えの後の調整局面 — AI株主導から広範な収益成長への移行が問われる',
       'url': 'https://diamond.jp/zai/articles/-/1066989',
       'thumb': None,
       'bullets': [
         'S&P500は5月11日に[[7,500台]]を初めて突破し連日最高値を更新したが、Moody\'s格下げ・CPI高止まりを受けて今週は3連続安。現在7,412付近で踊り場を形成している。',
         '初期の上昇はNVIDIA・Microsoft・Alphabetなど[[AIメガキャップ10社]]への過度な集中によるもので、__残りの490社の収益成長が伴わないという懸念__が再浮上。エコシステム全体への波及が次の試練。',
         '野村証券のS&P500年末予想は7,700（上方修正済み）。ただし30年国債利回り5%超が続くとバリュエーション下押し圧力が強まる。AIテック一強から広範な利益成長への転換が今後6ヶ月のテーマとなる。',
       ],
       'related': None},
      {'score': 82, 'time': '09:00', 'source': '野村証券',
       'title': '野村証券: 日経平均2026年末6万円に上方修正 — 総選挙自民大勝・企業業績好調が後押し',
       'url': 'https://www.nomura.co.jp/wealthstyle/article/0607/',
       'thumb': 'https://www.nomura.co.jp/wealthstyle/article/0607/images/og_a_0607_01.png',
       'bullets': [
         '野村証券は2026年末の日経平均目標を[[55,000円→60,000円]]に上方修正（5月上旬）。2月総選挙での自民党大勝による政策継続性・AI半導体牽引の企業業績・外国人投資家の買い戻しが材料。',
         '現在の日経平均は62,000円台で推移。ウォーシュ体制FRBの高金利が続き円安が継続する間は__輸出企業の外貨建て収益が嵩上げされ、円安・業績好調の二重支援__が継続中。',
         '上振れシナリオ（70,500円）到達には日銀利上げ限定化と米景気軟着陸の同時実現が必要。下振れシナリオ（53,000円）はWarsh利上げ再開による急速な円高・輸出企業業績悪化が想定される。',
       ],
       'related': {'axis': '波及', 'ref_title': '5/19号: 日経平均6.2万円台', 'ref_date': '2026-05-19', 'note': '野村証券の正式上方修正へ。'}},
      {'score': 77, 'time': '06:30', 'source': 'OECD',
       'title': 'OECD: 日本2026年GDP成長率を0.7%に下方修正 — 原油高・円安・高齢化の三重苦を警告',
       'url': 'https://www.ig.com/jp/us-stock-market-analysis/2026-key-topics-of-us-equities-and-spx500-outlook-260104',
       'thumb': None,
       'bullets': [
         '[[OECD]]は日本の2026年実質GDP成長率予想を1.1%から[[0.7%]]に大幅引き下げ。原油価格高止まりによる輸入コスト増、円安による購買力低下、高齢化に伴う労働参加率低下を主因に挙げた。',
         'IMFの世界経済見通しでも日本の成長率は先進国最低水準に分類され、__構造的な人口減少と生産性停滞が政策の枠組みを超えた課題__として国際的に認識されている。',
         '訪日ベッセント米財務長官との協議では為替と貿易不均衡が議題となり、円安是正への国際的な関心が高まっている。ただし「市場の動きに従うべき」との発言は為替介入には否定的だった。',
       ],
       'related': None},
    ]
  },
  {
    'id': 'game', 'name_jp': 'ゲーム', 'name_en': 'Gaming', 'glyph': '●',
    'accent': '#5E3D8C',
    'summary': 'テイルズ・オブ・アライズとヨッシーとフカシギの図鑑がSwitch 2向けに本日同時発売。任天堂は来期出荷16.5M台に下方修正し株価-8.44%。サイバーエージェントの英語版ウマ娘が海外ヒットでQ2大幅増収増益。',
    'articles': [
      {'score': 94, 'time': '00:00', 'source': 'Famitsu',
       'title': 'テイルズ・オブ・アライズ Switch 2版、本日正式発売 — SQUARE ENIXマルチプラット戦略の本命タイトル',
       'url': 'https://www.famitsu.com/schedule/switch2',
       'thumb': f'{CDN}/ng-thumb-game.jpg',
       'bullets': [
         '[[テイルズ オブ アライズ Beyond the Dawn Edition]]がSwitch 2向けに本日5月21日発売。本編と後日譚DLC「Beyond the Dawn」を含む完全版で、[[SQUARE ENIX]] CEO「Switch 2に全力」宣言の第一弾と位置付けられる。',
         'RPG累計[[680万本超]]のIPのSwitch 2移植はマルチプラットフォーム戦略の試金石。初週売上が6月3日発売予定のFFVIIリバースSwitch 2版とあわせ、同社戦略の成否を測る指標として注目される。',
         '__Switch 2のRPGカタログを充実させる一手__として、他のサードパーティにとっても移植判断の基準となる。19.9M台の累計販売実績があるSwitch 2のRPGユーザー規模は小さくない。',
       ],
       'related': {'axis': '続報', 'ref_title': '5/19号: SQUARE ENIXがTales of Arise Switch 2版を5/21発売', 'ref_date': '2026-05-19', 'note': '発売日確認として。'}},
      {'score': 91, 'time': '07:00', 'source': 'Nintendo Life',
       'title': '任天堂: Switch 2来期出荷16.5M台に下方修正、株価-8.44% — メモリ高騰が価格改定を強いる',
       'url': 'https://www.nintendolife.com/news/2026/05/nintendo-announces-sweeping-price-hikes-for-switch-2',
       'thumb': 'https://images.nintendolife.com/f3c0363d630fb/large.jpg',
       'bullets': [
         '[[任天堂]]は2026年3月期Switch 2出荷実績19.9Mに対し、2027年3月期の目標を[[16.5M台]]に下方修正。価格改定（日本[[¥59,980]]・米$499.99・欧€499.99）が販売計画を3M台以上圧迫した形。',
         '価格改定の要因はメモリ（AI需要急増による高騰）・米国関税・円安の三重苦。任天堂株は発表翌日に[[8.44%]]下落し、時価総額で約6,000億円が失われた。__ハードウェアビジネスの為替・コストリスクが浮き彫り__に。',
         '5月25日の値上げ前に在庫枯渇と再販争奪が続いており短期的な需要は底堅い。発売1周年（6月5日）に向けた台数確保と秋のサプライズ予告（Star Fox・スプラトゥーンレイダーズ）が来期カギ。',
       ],
       'related': None},
      {'score': 87, 'time': '00:00', 'source': 'Famitsu',
       'title': 'ヨッシーとフカシギの図鑑: Switch 2版、本日発売 — 任天堂ファースト2Dアクションでファミリー層を確保',
       'url': 'https://www.famitsu.com/article/202605/72453',
       'thumb': 'https://cimg.kgl-systems.io/camion/files/72453/thumbnail_eVqp.jpg?x=1280',
       'bullets': [
         '[[ヨッシーとフカシギの図鑑]]がSwitch 2用に本日発売。図鑑の中に迷い込んだ謎の生き物を調査する2Dアクションで、5月Switch 2大型投入ラッシュの中で任天堂ファーストの重要な一角を担う。',
         'テイルズ・インディ・FFVIIリバースとサードパーティが揃う中、__任天堂ファーストの新作は「広いユーザー層を引き寄せる錨」__として機能する。子育て世帯のSwitch 2購入層への訴求力が注目される。',
         'Switch 2の高精細ディスプレイを活かしたビジュアル体験を訴求する戦略とみられ、2Dアクション形式の採用は携帯モードでの遊びやすさも意識したもの。ファミリー層の取り込みに成功すれば来期16.5M目標にも貢献できる。',
       ],
       'related': {'axis': '続報', 'ref_title': '5/18号: ヨッシーと不思議な本、Switch2で5月21日発売', 'ref_date': '2026-05-18', 'note': '発売日確認。'}},
      {'score': 83, 'time': '08:30', 'source': 'Famitsu',
       'title': 'サイバーエージェントQ2: 英語版ウマ娘が海外ヒット — ゲーム事業がQoQで大幅増収増益',
       'url': 'https://www.famitsu.com/article/202605/74688',
       'thumb': 'https://cimg.kgl-systems.io/camion/files/74688/thumbnail_XMMy.jpg?x=1280',
       'bullets': [
         '[[サイバーエージェント]]の2026年9月期Q2（1〜3月）で、英語版「[[ウマ娘 プリティーダービー]]」の海外展開が予想を上回るヒットとなり、ゲーム事業がQoQで大幅増収増益を達成。',
         'コナミとの特許紛争和解（特別損失7.27億円）を計上しながらも海外収益がカバー。__グローバルIPへの転換が成功し__、評価軸が国内ガチャ依存モデルから国際IPプレイヤーへ変わりつつある。',
         '韓国・台湾・北米でもランキング上位定着が継続し、アジア全域での収益化が軌道に乗った。2027年3月期への貢献額と次のグローバルIP展開（「ウマ娘 Gold-Triumph」英語版等）が次の注目点。',
       ],
       'related': None},
      {'score': 79, 'time': '09:00', 'source': '電ファミニコゲーマー',
       'title': 'カプコン2026年3月期: 販売5,907万本のうちパッケージはわずか7% — デジタル化の完成形',
       'url': 'https://news.denfaminicogamer.jp/news/260514k',
       'thumb': 'https://news.denfaminicogamer.jp/wp-content/uploads/2026/05/explanation_2026_full_02_pages-to-jpg-0019.jpg',
       'bullets': [
         '[[カプコン]]の2026年3月期決算補足資料によると、全販売本数[[5,907万本]]のうちパッケージ比率はわずか[[7.0%]]。実質的にデジタル販売一本化が完成した。',
         '売上[[811億円]]（前期比+43.9%）、純利益275億円（同+80.1%）と全セグメントで増収増益。バイオ・モンハン・スト6のリピート購入と新IP「プラグマタ」投入の合わせ技が奏功した形。',
         'パッケージ7%は__業界全体のデジタル化完成の先行指標__として注目される。小売との関係・下取り市場の縮小など業界構造変化がこの数字に凝縮されており、他社の参照データとして機能する。',
       ],
       'related': None},
    ]
  },
]

reflection = {
  'title': 'AI自律化とドル一強が交差する構造転換の日',
  'subtitle': 'Google I/O余波・Moody\'s格下げ・Switch 2値上げカウントダウン——変化の断層が同時に走る',
  'lead': "本日5分野・25本のニュースから浮かび上がる最大のテーマは[[AIエージェントの自律化]]と[[Moody's格下げ後の財政・為替リスク]]の同時進行である。以下、各カテゴリを横断して読み解く。",
  'pull_quote': '「単一の強い製品」から「__エコシステムでの占有率__」へ——AIプラットフォーム経済が成熟期に入った日。',
  'sections': [
    {'tag': '総論', 'heading': 'Google I/O後の世界でAIエージェントが個人・企業・インフラに同時浸透', 'accent': '#1A1A1A',
     'body': '[[Gemini Spark]]（個人向け24時間エージェント）・[[Antigravity 2.0]]（エンタープライズ開発）・Project Glasswing（インフラセキュリティ）という3層が5月21日に同時進行している。AIはもはや「使うツール」ではなく「__常に稼働する基盤__」として社会に組み込まれ始めた。この変化はユーザーのプライバシー管理観・企業のIT予算配分・国家のセキュリティ戦略の全てを同時に書き換える。'},
    {'tag': '為替・経済', 'heading': "Moody's格下げが市場の「財政免疫」を試す週", 'accent': '#B8860B',
     'body': "[[Moody's]]のAa1格下げから5日が経ち、市場は「3度目のサプライズではない」として消化しつつある。だが30年債利回り5.19%が示すように、__財政悪化への長期懸念が高金利を引き留め続けている__。皮肉なことにこれがドル高圧力を生み出しており、Moody's格下げがむしろ円安加速の遠因になっている。一方で日銀7月利上げシナリオが固まれば、この金利差が縮む転換点となり得る。"},
    {'tag': 'AI・技術', 'heading': 'Gemini SparkとAnthropicのサイバー防衛で日常とセキュリティが同時変わる', 'accent': '#2D5BB8',
     'body': 'Google Gemini Sparkは「個人の認知負荷を引き受けるAI」という新カテゴリを作り、AnthropicのGlasswingはAIが「セキュリティ研究者の役割」を代替し始めた実例を示した。両社の方向性は異なるが、共通するのは__「AIが人間の代理として動く領域が一段階拡大した」__という事実だ。Verizonのインフラ参加でGlasswingが通信網に広がったことは、国家安全保障レベルの議論をもAIが担い始めたことを意味する。'},
    {'tag': '産業・業界', 'heading': 'コンサルとゲームで「物量→デジタル→エコシステム」転換が加速', 'accent': '#2E6B52',
     'body': 'NTTデータの工数70%削減目標は「人手でのSI」の終焉を予告し、Accentureの$65Bはコンサル業界で「実装力」が「提言力」より高く評価される時代の到来を証明した。ゲーム業界ではカプコンのデジタル比率93%が業界標準を引き上げ、サイバーエージェントの英語版ウマ娘海外展開は__日本IPのグローバル耐久性が試される最前線__となっている。両業界ともプラットフォームとエコシステムの制覇者が最終的に勝つ構造に移行した。'},
    {'tag': '明日へ', 'heading': 'Switch 2値上げ(5/25)・FOMC(7月)に向けた意思決定の準備を', 'accent': '#C9B98A',
     'body': '4日後の5月25日にSwitch 2が¥49,980→¥59,980に値上がりする。ゲームファンにとっては購入判断の期限が迫っており、任天堂にとっては来期出荷台数修正後の初の「高価格ハード」販売が始まる日でもある。経済面では7月FOMC（Warsh初の政策会合）が最大のイベントとして迫っており、__日銀7月利上げとFOMC高金利維持の組み合わせ__が為替・株式市場の構造を大きく変え得る。今週はその前哨戦として最重要週に位置付けられる。'},
  ],
  'takeaways': [
    {'num': '01', 'tag': '為替', 'color': '#B8860B',
     'text': "[[Moody's格下げ]]後もドル一強継続——日銀7月利上げが円反転の最後のトリガーとなるか、米財政と日米金利差の綱引きに注目"},
    {'num': '02', 'tag': 'AI', 'color': '#2D5BB8',
     'text': '[[Gemini Spark]]が示す「always-on AI」の時代——プライバシーと生産性の新しいトレードオフを社会がどう折り合いをつけるかが次の問い'},
    {'num': '03', 'tag': '産業', 'color': '#5E3D8C',
     'text': '[[カプコン]]デジタル93%・[[ウマ娘]]海外化がゲーム業界の未来形——IPの国際耐久性と価格転嫁力が勝敗を分ける'},
  ],
  'related': [
    {'date': '2026-05-20', 'title': '前回号: S&P500調整・Warsh就任・NTT DATA WinWire買収'},
    {'date': '2026-05-19', 'title': 'Google I/O 2026開幕・Warsh就任・Switch 2カウントダウン'},
    {'date': '2026-05-16', 'title': 'S&P500初の7500超え・ウォーシュ就任宣言'},
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
