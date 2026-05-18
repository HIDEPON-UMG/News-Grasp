"""2026-05-19 号 HTML メール生成スクリプト"""
import re, sys, os
sys.stdout.reconfigure(encoding='utf-8')
os.chdir(os.path.dirname(os.path.abspath(__file__)) + '/..')

ISSUE_DATE = '2026-05-19'
ISSUE_NO   = '20260519'
WEEKDAY    = '火'
CDN        = 'https://raw.githubusercontent.com/HIDEPON-UMG/news-grasp-assets/main'

# ── 強調マーカー変換 ──────────────────────────────────────────────────────────
def render_bullets(bullets: list, accent: str) -> str:
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

# =============================================================================
# DATA
# =============================================================================

cats = [
  {
    'id': 'fx', 'name_jp': '為替', 'name_en': 'Foreign Exchange', 'glyph': '¥',
    'accent': '#B8860B',
    'summary': 'Warsh 新 FRB 議長就任でドル強含みが継続、USD/JPY は 158〜159 円レンジを試す週に入る。日銀の 9 月利上げ確率が 77% に達し、円の下値硬直性は増しているが介入閾値の 160 円まで余裕あり。',
    'articles': [
      {'score': 92, 'time': '06:30', 'source': 'NPR / Reuters',
       'title': 'Warsh 新 FRB 議長就任——インフレ優先・データドリブン路線で利下げ期待を冷却',
       'url': 'https://www.npr.org/2026/05/13/nx-s1-5816235/kevin-warsh-federal-reserve-chair-jerome-powell',
       'thumb': 'https://npr.brightspotcdn.com/dims3/default/strip/false/crop/3542x1992+0+185/resize/1400/quality/85/format/jpeg/?url=http%3A%2F%2Fnpr-brightspot.s3.amazonaws.com%2Fb8%2F0e%2F62a09b724e0ca347faddf737929f%2Fgettyimages-2272388349.jpg',
       'bullets': [
         '[[Kevin Warsh]] が上院承認を経て FRB 議長に正式就任。就任声明で「インフレ 2% 達成前に利下げは行わない」と明言し、__ホワイトハウスの早期緩和圧力を明確に拒絶__した。',
         'CPI 前年比 3.8%（2023 年来最高）が背景にあり、FOMC 内のタカ派メンバーが「年内利下げ不要論」を公言。USD/JPY は 158.9 円を推移。',
         'Warsh は「自身の判断で政策を決定する」と宣言。金融市場は "Warsh ショック" への警戒から米国債利回りが上昇し、[[FRB プット]]の不確実性が急拡大。',
       ],
       'related': {'axis': '復状', 'ref_title': 'ウォーシュ利下げ不全シナリオ', 'ref_date': '2026-05-16', 'note': 'Warsh 就任決定を受けて相場の方向感が強まった。'},
      },
      {'score': 88, 'time': '07:15', 'source': 'Bloomberg / BOJ',
       'title': 'USD/JPY 158〜159 円レンジ継続——Google I/O 当日も上値重く推移',
       'url': 'https://www.boj.or.jp/statistics/market/forex/fxdaily/index.htm',
       'thumb': None,
       'bullets': [
         'ドル円は [[158.9 円]]台で週明け推移。前週末比 MoF 口頭介入の効果は 1 円程度に縮小し、一時 159 円台を試すも Google I/O 前の様子見ムードで上値は限定的。',
         '160 円を超えると実弾介入の蓋然性が高まるとの見方が市場コンセンサス。__夏季の輸入決済ドル需要と輸出企業のヘッジ玉がせめぎ合う__構図が続く。',
         '週内に BOJ 政策インタビューと米雇用統計（5/21）を控え、ショートポジション整理と様子見の交錯が相場の振れ幅を抑制。',
       ],
       'related': None,
      },
      {'score': 85, 'time': '06:00', 'source': 'IG 証券 / Bloomberg',
       'title': '日銀 9 月利上げ確率が 77% に上昇——CPI 3.8% が政策転換を後押し',
       'url': 'https://www.ig.com/jp/news-and-trade-ideas/jpy-stays-weak-even-after-boj-show-off-hawkish-messages-260428',
       'thumb': 'https://a.c-dn.net/c/content/dam/publicsites/igcom/uk/images/news-article-image-folder/bb_USDJPY_Japan_flag_14_11_2024.jpg/jcr:content/renditions/cq5dam.web.1280.1280.jpeg',
       'bullets': [
         '国内 CPI 前年比 3.8%（3 年ぶり高水準）を受け、[[市場参加者の 77%]] が 9 月 BOJ 会合での利上げを織り込む水準まで上昇。',
         '田村審議委員は「中東リスク起因のエネルギーコスト長期化」を利上げ理由として挙げ、__ハト派寄り委員からも追加引き締め支持が広まる__。',
         '利上げ実施となれば政策金利は 1.00% に到達。日米金利差縮小が円高圧力となり、USD/JPY の上値をじわじわ抑える構造変化へ。',
       ],
       'related': {'axis': '復状', 'ref_title': 'BOJ 利上げ確率 77% 急騰', 'ref_date': '2026-05-18', 'note': '週明けも確率が維持されている。'},
      },
      {'score': 80, 'time': '07:45', 'source': 'ECB / Forex.com',
       'title': 'EUR/JPY 183 円台——ECB 利下げサイクル 2.0% で一巡、ユーロ底堅く',
       'url': 'https://www.forex.com/en-us/news-and-analysis/japanese-yen-technical-analysis-usd-jpy-eur-jpy-gbp-jpy-into-fed-boj/',
       'thumb': None,
       'bullets': [
         'EUR/JPY は [[183 円台]]前後で推移。ECB の利下げサイクルが預金ファシリティ金利 2.0% で完了との市場観測が固まり、__ユーロの下値を支える__。',
         'EUR/USD は 1.16 近辺。ドル全面高の地合いでも、ユーロは相対的に底堅いパフォーマンス。GBP/USD は英インフレ反発でポンドが 1.33 台で強含み。',
         '次の ECB アクションは「データ次第の小幅な調整」との見方。日欧金利差縮小のペースは日米より鈍く、EUR/JPY の円高方向リスクは限定的。',
       ],
       'related': None,
      },
      {'score': 76, 'time': '08:00', 'source': 'Investing.com / Wise',
       'title': 'ヘッジファンドの円キャリー解消観測が浮上——雇用統計・BOJ インタビューが引き金に',
       'url': 'https://jp.investing.com/currencies/usd-jpy-historical-data',
       'thumb': None,
       'bullets': [
         '直近 3 週で積み上がった円売りポジションが過去最大圏。[[ヘッジファンド]]がショートスクイーズを警戒してポジション整理を検討しているとの観測が流通。',
         '5/21 米雇用統計と BOJ 政策インタビューが同週に重なる。__どちらかが想定外の結果であれば急激なポジション巻き戻しリスク__がある。',
         '160 円超でも国内個人投資家の追い証が連鎖するため、ボラティリティ上昇は双方向に注意が必要な週となる。',
       ],
       'related': None,
      },
    ],
  },
  {
    'id': 'ai', 'name_jp': 'AI', 'name_en': 'Artificial Intelligence', 'glyph': '◆',
    'accent': '#2D5BB8',
    'summary': 'Google I/O 2026 が本日開幕。Gemini Intelligence がアプリを横断してタスクを自律実行する「OS 統合型 AI」戦略を発表。一方 Anthropic が職場シェアで OpenAI を初逆転し、AI モデル三極競争が新局面へ。',
    'articles': [
      {'score': 98, 'time': '02:00', 'source': 'Android Central / The Next Web',
       'title': 'Google I/O 2026 開幕：Gemini Intelligence・Android XR・Googlebook 一斉発表',
       'url': 'https://www.androidcentral.com/phones/live/google-i-o-2026-live-blog-android-17-android-xr-glasses-and-all-the-gemini-ai-news',
       'thumb': 'https://cdn.mos.cms.futurecdn.net/Mc3W2B94FeGi8Rhn6HNziD-1746-80.jpg',
       'bullets': [
         'Google I/O 2026 キーノート（PT 10:00/JST 02:00）で [[Gemini Intelligence]] スイートを発表。AI が Gmail から始まり教科書注文まで __マルチステップタスクを OS レベルで自律実行__する機能群で、ChatGPT・Claude との差別化を「OS 統合」軸で鮮明化。',
         '新カテゴリ [[Googlebook]]（AI ファースト Android ラップトップ）と Android XR スマートグラス（Samsung / Warby Parker / Gentle Monster 製）もプレビュー。Gemini 2.5 Pro によるリアルタイム翻訳・ナビ・視覚理解を搭載。',
         'Android 17 には「Create My Widget」（Gemini に話しかけてウィジェット生成）が追加。Google の「すべてのサーフェスに AI を埋め込む」戦略が本格始動。',
       ],
       'related': None,
      },
      {'score': 88, 'time': '05:00', 'source': 'Ramp AI Index / Business Insider',
       'title': 'Anthropic の職場シェアが OpenAI を初逆転——34.4% vs 32.3%、Claude Code が主役',
       'url': 'https://xenospectrum.com/deloitte-ai-report-2026-revenue-gap-analysis/',
       'thumb': None,
       'bullets': [
         'Ramp が公開した 2026 年 5 月 AI 採用指数で [[Anthropic]] の職場利用率が [[34.4%]] に達し、OpenAI の 32.3% を初めて逆転。エンタープライズコーディング市場シェアは __54%__。',
         'Claude Code が単独で数十億ドルの収益ラインに成長。次世代モデル候補として「[[Mythos]]」「Capybara」「Jupiter」のコードネームが内部で確認。',
         'ARR $300 億超えが視野に入り、Anthropic の企業価値評価が OpenAI を追いかける展開。Google との $20 億コミットを上回る「$200 億枠」の活用が加速中。',
       ],
       'related': {'axis': '復状', 'ref_title': 'Anthropic 職場シェア首位接近', 'ref_date': '2026-05-15', 'note': '今週ついに逆転が確認された。'},
      },
      {'score': 85, 'time': '02:30', 'source': 'TechTimes / AIxploria',
       'title': 'Google Gemini Spark がプロアクティブ AI に参入——Mythos・GPT-5.5 との三極競争が激化',
       'url': 'https://www.techtimes.com/articles/316755/20260517/google-i-o-2026-keynote-opens-tuesday-new-gemini-lands-behind-mythos-gpt-55.htm',
       'thumb': None,
       'bullets': [
         'Google I/O で「[[Gemini Spark]]」をプロアクティブ AI ブリーフィング機能として発表。受信トレイ整理・会議メモ・ニュースダイジェストを __ユーザーの操作なしに自動生成__。',
         'Anthropic Mythos Preview（OSWorld 79.6%）と ChatGPT Super App に囲まれ、Google は「OS 統合という独自優位性」を前面に出す差別化戦略を選択。',
         'Gemini Omni（テキスト＋画像＋動画を 1 パイプラインで生成）も同時発表。マルチモーダル AIO 路線が Anthropic・OpenAI との競争軸に。',
       ],
       'related': None,
      },
      {'score': 82, 'time': '04:00', 'source': 'OpenAI / IBM Mixture of Experts',
       'title': 'OpenAI GPT-5.5 ファミリー：幻覚率 52.5% 削減、医療・法務での採用急拡大',
       'url': 'https://openai.com/index/introducing-gpt-5-5/',
       'thumb': None,
       'bullets': [
         'OpenAI の [[GPT-5.5 Instant]] が ChatGPT デフォルトモデルに昇格。医学領域での幻覚率が __52.5% 削減__され、病院・法律事務所からの採用が急増。',
         'サイバーセキュリティ専用版 GPT-5.5-Cyber を EU へリリース。Anthropic Mythos Preview の独自規制対応を牽制する競合ポジション取り。',
         '社内ログに「GPT-5.6」の存在が浮上。OpenAI のモデルリリースサイクルが加速しており、四半期単位での差別化更新が常態化する見通し。',
       ],
       'related': None,
      },
      {'score': 78, 'time': '03:00', 'source': 'The Next Web / Analytics Insight',
       'title': 'Android XR スマートグラス発表——Warby Parker・Gentle Monster でファッション路線に転換',
       'url': 'https://thenextweb.com/news/google-io-2026-gemini-intelligence-android-xr-glasses',
       'thumb': 'https://media.thenextweb.com/2026/05/google-io-2026-gemini-intelligence-android-xr-glasses.avif',
       'bullets': [
         'Android XR グラスに [[Warby Parker]] と Gentle Monster が参画。Meta の Ray-Ban モデルを意識し、__日常使いのスタイリッシュなスマートグラス__路線を明確化。',
         'Samsung とは Android XR ヘッドセット向けパートナーシップを継続し、Apple Vision Pro との対抗構図が鮮明。Gemini 2.5 Pro をオンデバイス推論で搭載しパイプライン最適化でバッテリー課題を克服。',
         'XR 市場は Apple・Meta・Google の三つ巴が本格化。Google の「Android エコシステム拡張」戦略がハードウェア合従連衡を加速させている。',
       ],
       'related': None,
      },
    ],
  },
  {
    'id': 'it', 'name_jp': 'IT-Consulting', 'name_en': 'IT & Consulting', 'glyph': '▲',
    'accent': '#2E6B52',
    'summary': 'BCG が AI エージェント専用予算の急拡大を報告する一方、Deloitte の調査は「74% 期待 vs 20% 実績」という業界横断の死の谷を暴露。Google I/O 発表の Gemini Intelligence は SIer の主力事業に直撃弾となる。',
    'articles': [
      {'score': 91, 'time': '08:00', 'source': 'Biz/Zine / BCG',
       'title': 'BCG 調査：2026 年企業 AI 投資の 30% 超がエージェント専用予算へ',
       'url': 'https://bizzine.jp/news/detail/12755',
       'thumb': 'https://bizzine.jp/static/images/article/12755/12755_top.png',
       'bullets': [
         'BCG の 2026 年調査で企業 AI 投資額が 2025 年比 __2 倍__に増加。うち [[30% 以上]]がエージェント専用予算として計上されるとの実測値を公表。',
         'エージェント導入企業ではスライド作成など低付加価値業務が 15% 削減され、その 70% が高付加価値分析に再投資。BCG 自社も同モデルを適用し、__コンサルタント当たり生産性が 2.3 倍に向上__。',
         'Big3（McKinsey/BCG/PwC）はプロプライエタリ AI モデルへの投資を本格化。戦略・価格設定・運用の三領域でコンサルタントと AI が並走する体制が完成しつつある。',
       ],
       'related': None,
      },
      {'score': 88, 'time': '07:00', 'source': 'Deloitte / XenoSpectrum',
       'title': 'Deloitte AI レポート：74% 期待 vs 20% 実績——「死の谷」が業界横断の課題に',
       'url': 'https://xenospectrum.com/deloitte-ai-report-2026-revenue-gap-analysis/',
       'thumb': 'https://media.xenospectrum.com/large_A_futuristic_office_scene_viewed_from_a_slightly_high_angle_ae454bc662.webp',
       'bullets': [
         'Deloitte の 2026 年 AI 導入調査で AI に期待する企業 [[74%]] に対し ROI を実際に測定できたのは __20%__ のみ。PwC と連名で「PoC 成功→本番化 25% の壁」解消のための P&L 直結設計を提唱。',
         '元 Deloitte AI 責任者が「[[コンサルタントの大群を送り込む手法]]はもはや通用しない」と明言。人材配置モデルの抜本見直しを宣言。',
         '「実験から変革へ」のシフトを実現した企業は ROI が 3〜5 倍に跳ね上がるとのデータも公表。__AI ガバナンスと KPI の直結が生き残りの条件__となってきた。',
       ],
       'related': {'axis': '復状', 'ref_title': 'Deloitte 6か月で 18 万人削減全容', 'ref_date': '2026-05-18', 'note': '調査背景を明らかにした一報。'},
      },
      {'score': 85, 'time': '09:00', 'source': "Let's Data Science / Business Today",
       'title': 'Google I/O 衝撃波：Gemini Intelligence が SIer・コンサルのサービス設計に直撃',
       'url': 'https://letsdatascience.com/news/google-debuts-gemini-focused-updates-at-io-2026-4be4fde6',
       'thumb': None,
       'bullets': [
         'Google I/O で発表の [[Gemini Intelligence]] は OS 横断型マルチステップ AI エージェントで、SIer 各社が主力事業としてきたワークフロー自動化ソリューションに真正面から競合。',
         'アクセンチュア・NTT データ・富士通は Google Cloud パートナーシップをテコに「[[Gemini Intelligence 導入支援]]」へ素早く転換する動き。__パートナー陣営の即応力が競争優位の新軸に__。',
         'McKinsey の AI エージェント展開（2.5 万人規模）も Google の新スタックに沿った再設計が必要になる。コンサルファームが「競合」から「補完」へと役割転換を迫られる転換点。',
       ],
       'related': None,
      },
      {'score': 83, 'time': '07:30', 'source': 'Business Insider Japan',
       'title': 'Big4 人材モデルの崩壊期——McKinsey・PwC・KPMG が「AI エンジニア優位」路線へ',
       'url': 'https://www.businessinsider.jp/article/2602-mckinsey-bcg-pwc-ey-ai-agents-adoption-value-consulting-industry/',
       'thumb': None,
       'bullets': [
         'McKinsey CEO が「40,000 人 ＋ [[25,000 人]] AI エージェント」体制を宣言。コンサルタント人件費主体から AI インフラ費用主体へのコスト構造転換が進む。',
         'PwC は「Human+AI Skillset」として 30 スキル体系を策定し、20 万人の ChatPwC ユーザーへの受講を義務化。__ジェネラリストからエンジニア兼コンサルへ人材像が再定義__。',
         'KPMG がアドバイザー [[400 人を削減]]（AI 代替）——Big4 で初の大規模置き換え事例として業界に衝撃。EY・デロイトも同路線を追う可能性。',
       ],
       'related': None,
      },
      {'score': 78, 'time': '09:30', 'source': 'NTT Data Press / NDW',
       'title': 'NTT Data 2030 AIVista：EBITDA 1.2 兆円目標でフルスタック AI コンサルへ転換',
       'url': 'https://www.ndw.jp/catalog-software-2026/',
       'thumb': None,
       'bullets': [
         'NTT Data が 2030 年 EBITDA [[1.2 兆円]]を目標とする AIVista フレームワークを再公表。グローバル 5 万人規模を AI 導入支援に特化し、法務・財務向け AI コンサル 18 サービスが稼働済み。',
         'NTT DOCOMO Global × アクセンチュアの __Universal Wallet Infrastructure__（AI 駆動決済基盤）と連携予定。決済×コンサル×AI のフルスタック路線が鮮明。',
         'Google I/O 発表の Gemini Intelligence を取り込むことで、NTT Data の既存 Google Cloud パートナー地位がさらに強化される見通し。',
       ],
       'related': None,
      },
    ],
  },
  {
    'id': 'economy', 'name_jp': '経済', 'name_en': 'Economy', 'glyph': '■',
    'accent': '#8E2A19',
    'summary': 'Warsh 新 FRB 議長就任で「インフレ優先・政治圧力拒絶」路線が確定。同日の Google I/O が AI 株を押し上げ S&P500 は 7,500 台で攻防。日経平均は 6.2 万円台を維持するも訪日中のベッセント財務長官発言が焦点。',
    'articles': [
      {'score': 93, 'time': '06:30', 'source': 'NPR / Bloomberg',
       'title': 'Warsh FRB 議長就任——インフレ優先・データドリブン路線で金融市場を試す',
       'url': 'https://www.npr.org/2026/05/13/nx-s1-5816235/kevin-warsh-federal-reserve-chair-jerome-powell',
       'thumb': 'https://npr.brightspotcdn.com/dims3/default/strip/false/crop/3542x1992+0+185/resize/1400/quality/85/format/jpeg/?url=http%3A%2F%2Fnpr-brightspot.s3.amazonaws.com%2Fb8%2F0e%2F62a09b724e0ca347faddf737929f%2Fgettyimages-2272388349.jpg',
       'bullets': [
         '[[Kevin Warsh]] が FRB 議長に正式就任。「CPI が 2% に向かって明確に動くまで利下げはしない」と明言し、__トランプ政権の早期緩和要求を公開で拒絶__。',
         'CPI 前年比 3.8%（2023 年来最高）が壁となり、年内 2 回の利下げ見通しが急後退。米 10 年債利回りが上昇し、株式のリスクプレミアム再評価が始まる。',
         'Warsh は「自身の判断で政策決定」と宣言し [[FRB の独立性]] を強調。市場参加者は「Warsh ショック」による金利の上振れリスクをポートフォリオに折り込み始めた。',
       ],
       'related': None,
      },
      {'score': 88, 'time': '04:00', 'source': 'TradingKey / Yahoo Finance',
       'title': 'Google I/O 2026 でアルファベット株急騰——AI メガキャップが S&P500 を 7,500 台へ押し上げ',
       'url': 'https://www.tradingkey.com/analysis/stocks/us-stocks/261894049-google-io-2026-alphabe-gemini-tradingkey',
       'thumb': 'https://resource.tradingkey.com/uploads/20260514/google2026-9728511d2edb476ab14ab2204a66c60d.jpeg',
       'bullets': [
         'Google I/O 基調講演を受け [[Alphabet (GOOGL)]] 株が時間外で +4% 超。Gemini Intelligence の企業向け収益化ストーリーが評価され、AI 銘柄全般に買いが波及。',
         'AI 銘柄（NVDA/GOOG/MSFT/META）が S&P500 時価総額の __45%__ を占有。指数全体の 142% 上昇に対し非 AI 株は 16% にとどまり、セクター集中が極まる状況。',
         'Capital Economics の S&P500 年末目標は 7,250 だが、Warsh 議長就任による金利上振れリスクが唯一の下押し要因として意識されている。',
       ],
       'related': None,
      },
      {'score': 84, 'time': '08:00', 'source': 'Diamond ZAI / Bloomberg',
       'title': '日経平均 6.2 万円台で高値圏維持——訪日ベッセント財務長官の発言に市場注目',
       'url': 'https://diamond.jp/zai/articles/-/1066989',
       'thumb': None,
       'bullets': [
         '日経平均は [[62,742 円]]前後で推移。AI・半導体主導の上昇基調が続くが、円安によるコスト圧力が製造業の重し。',
         '訪日中の [[Scott Bessent]] 米財務長官が為替・貿易フレームワークに関する日米協議を継続。__円高誘導プレッシャー__の存否が市場の最大注目点。',
         '2027 年 3 月期の業績予想が強気な AI インフラ・電力・自動車セクターへの集中が顕著で、個別銘柄選択がより重要な局面に入った。',
       ],
       'related': None,
      },
      {'score': 81, 'time': '07:00', 'source': 'Capital Economics / EBC Financial Group',
       'title': 'S&P500、7,500 の壁——AI 集中リスクと Warsh 利上げリスクの二重圧力',
       'url': 'https://www.ebc.com/jp/forex/288738.html',
       'thumb': None,
       'bullets': [
         'S&P500 は 7,500 台前半を試す局面。Capital Economics の [[7,250 年末予測]]に対し、AI バリュエーション過熱と Warsh の金融引き締め長期化リスクが頭を抑える構図。',
         'AI 株除外ベースでは S&P500 の上昇率はわずか 16%。__Herfindahl 指数（セクター集中度）が過去最高水準__に達し、尾リスク管理が機関投資家の最大課題に。',
         '中間選挙（11 月）と FRB 政策の双方向リスクが混在し、短期的なボラティリティ拡大が予想される。分散投資と押し目買いが基本戦略と分析各社が推奨。',
       ],
       'related': None,
      },
      {'score': 76, 'time': '07:45', 'source': 'Monex / IG 証券',
       'title': '日本国債 10 年利回り上昇——BOJ 利上げ観測が長期金利正常化を加速',
       'url': 'https://media.monex.co.jp/articles/-/28497',
       'thumb': None,
       'bullets': [
         '日本 10 年国債利回りが [[1.8% 近辺]]まで上昇。BOJ の 9 月利上げ確率 77% が徐々に長期金利に織り込まれ始め、__国内金利正常化が本格化__するシグナルが灯る。',
         '生保・銀行などの国内機関投資家が米国債から日本国債への資金シフトを加速。構造的なドル需要の軟化要因として為替市場にも波及。',
         '日米金利差縮小が進む 2026 年後半に向け、円高転換シナリオが具体性を帯びてきた。USD/JPY 150 円台への戻りは 2026 年 Q3〜Q4 が最有力窓口。',
       ],
       'related': None,
      },
    ],
  },
  {
    'id': 'game', 'name_jp': 'ゲーム', 'name_en': 'Gaming', 'glyph': '●',
    'accent': '#5E3D8C',
    'summary': 'Nintendo Switch 2 の発売まで 6 日。5/25 の 17 本一斉リリースと価格改定後の在庫争奪戦が過熱。SQUARE ENIX は Tales of Arise Switch 2 版を 5/21 に投下しマルチプラットフォーム戦略を本格始動。',
    'articles': [
      {'score': 92, 'time': '10:00', 'source': 'Famitsu / Nintendo',
       'title': 'Nintendo Switch 2 まで 6 日——17 本一斉リリース・価格 ¥59,980 で在庫争奪戦再燃',
       'url': 'https://www.famitsu.com/article/202605/72453',
       'thumb': 'https://cimg.kgl-systems.io/camion/files/72453/thumbnail_eVqp.jpg?x=1280',
       'bullets': [
         '[[Nintendo Switch 2]] 発売まで 6 日（5/25）。価格が [[¥59,980]] に改定され、同日 17 本が一斉リリース。スプラトゥーン レイダース・リズム天国 ミラクルスターズが最注目作。',
         '価格改定前の在庫は全国の抽選販売が終了済み。__入手難は再び深刻化__し、転売価格が約 1.5 倍水準で流通し始めている。',
         '予約済みユーザー向けには 5/24 早朝から受け取り可能な店舗が多く、メディア・インフルエンサーのレビュー解禁タイミングも注目される。',
       ],
       'related': None,
      },
      {'score': 85, 'time': '09:00', 'source': 'Famitsu / Amazon JP',
       'title': 'SQUARE ENIX が Tales of Arise Switch 2 版を 5/21 発売——マルチプラットフォーム戦略加速',
       'url': 'https://www.famitsu.com/article/202605/72453',
       'thumb': None,
       'bullets': [
         '[[テイルズ オブ アライズ：Beyond the Dawn Edition]] が 5/21 に Switch 2 向けリリース。CEO 「Switch 2 に全力」発言（5/18 確認）の第一弾タイトルとして注目を集める。',
         'Switch 2 の PS5 並み性能による高品質移植が証明されれば、他社 AAA タイトルの参入が加速する見通し。__Switch 2 が「高品質プラットフォーム」として市場に認知される分水嶺__。',
         'バンダイナムコとの協力作品として、SQUARE ENIX のマルチプラットフォーム展開が IP 単位で本格化。次弾は FF XVI Switch 2 版とも噂される。',
       ],
       'related': {'axis': '復状', 'ref_title': 'SQUARE ENIX Switch2 マルチ戦略 CEO 発言', 'ref_date': '2026-05-18', 'note': '実際の発売日が直前に迫った。'},
      },
      {'score': 81, 'time': '10:30', 'source': 'Gamebiz / 4Gamer',
       'title': 'Cygames がアメリカンオークスのメインスポンサーに就任——ウマ娘 IP のリアル競馬連携を強化',
       'url': 'https://gamebiz.jp/news/425182',
       'thumb': None,
       'bullets': [
         'Cygames が [[アメリカンオークス]] メインスポンサーに就任。ウマ娘にも登場するシーザリオが 2005 年に勝利した国際 GI レースで、__IP とリアルスポーツの境界を消す体験型マーケティング__の最新事例。',
         'ゲーム内では「アグネスタキオンの因子研究」イベント（5/14〜）が継続中。GW スタンプシートガチャ（5/29 まで）と連動した施策が長期エンゲージメントを維持。',
         'Cygames の海外展開戦略において「競馬 IP の国際 GI レース冠」は認知向上の最短ルート。北米・欧州のコアファン獲得が狙いとみられる。',
       ],
       'related': None,
      },
      {'score': 80, 'time': '09:30', 'source': 'Capcom IR / Famitsu',
       'title': 'Capcom 新 IP「プラグマタ」Switch 2/PS5 で 200 万本突破視野——マルチ戦略の旗手に',
       'url': 'https://www.capcom.co.jp/ir/english/news/html/e250403.html',
       'thumb': None,
       'bullets': [
         'Capcom の新規 IP [[プラグマタ]] が 4/24 発売から 3 週間で Switch 2・PS5・PC マルチにて __200 万本超え視野__とアナリストが試算。フレームレート維持・レイトレーシング品質が海外レビュアーから高評価。',
         '6/5 に Street Fighter 6 の Switch 2 版も予定。Capcom の「マルチプラットフォーム戦略で各機種の強みを活かす」方針が結果を出しつつある。',
         '2026 年は Capcom にとって「[[Switch 2 イヤー]]」と位置づけ。怪物ハンターシリーズの次回作との組み合わせで年間 IP 収益の過去最高更新が視野に。',
       ],
       'related': None,
      },
      {'score': 73, 'time': '10:00', 'source': 'Famitsu',
       'title': '2026 年 5〜9 月の Switch / Switch 2 注目作 26 選——インディ前年比 40% 増でエコシステム多様化',
       'url': 'https://www.famitsu.com/article/202605/72453',
       'thumb': None,
       'bullets': [
         'ファミ通が 2026 年 [[5〜9 月の注目タイトル 26 選]] を発表。スプラトゥーン レイダース・リズム天国 ミラクルスターズ・ほの暮しの庭がトップ 3 を占める。',
         '任天堂自社 9 本が 5 月中に発売確認。ヨッシーとフカシギの図鑑（5/21）も加わり、__ファーストパーティの密度が歴代最高水準__。',
         'インディタイトルが前年比 40% 増のリリース数で、Switch 2 エコシステムの多様化が加速。ロングテール型の収益基盤がプラットフォーム全体の生命線となりつつある。',
       ],
       'related': None,
      },
    ],
  },
]

reflection = {
  'title': 'Google I/O の衝撃と Warsh 就任が映す AI シフトの不可逆性',
  'subtitle': 'OS に溶け込む AI と引き締める金融——二つの断絶が描く新経済地図',
  'lead': '本日 5 分野・25 本のニュースから浮かび上がる最大のテーマは [[Google I/O]] の衝撃と [[Warsh FRB]] 就任という二極の同時進行である。前者は AI を「道具」から「インフラ」に昇格させ、後者はそのインフラへの投資資金を絞る金利水準を維持する。この引力と斥力が交錯する今日、__どちらに乗るかではなく両者の緊張をどう活かすかが問われる__。以下、各カテゴリを横断して読み解く。',
  'pull_quote': '「単一モデルの賢さ」から「__OS に統合された自律エージェント__」へ——Google I/O 2026 は AI 競争の定義そのものを書き換えた日。',
  'sections': [
    {'tag': '総論', 'heading': 'AI と金融の「二重のバトンリレー」が始まった', 'accent': '#1A1A1A',
     'body': 'Google I/O で発表された [[Gemini Intelligence]] は「AI はアプリではなく OS になる」という宣言だ。Gmail から教科書注文まで OS レイヤーでシームレスに繋ぐこの機能は、Claude・ChatGPT との競争を「モデルの性能」から「エコシステムの統合度」へと一段引き上げる。一方で [[Kevin Warsh]] FRB 議長はインフレ 3.8% を盾に利下げを拒否した。__資金調達コストが高止まりする中での AI 投資加速__は、キャッシュリッチな大企業（Google/Microsoft/Anthropic）と中小スタートアップの間に資本格差を生む。この二重構造が今後 6〜12 カ月の業界再編を決定づける。'},
    {'tag': '為替・経済', 'heading': 'Warsh と日銀の「利上げ競争」が円の床を作る', 'accent': '#B8860B',
     'body': '[[Warsh]] は「データ次第」を繰り返しながら本質的にはタカ派。米 10 年債利回りが上昇し、ドルは全面高。しかし同時に [[日銀]] の 9 月利上げ確率が 77% まで上昇しており、__160 円手前で均衡する構造__が維持されている。円安が急進すれば MoF の実弾介入が発動し、急騰すれば輸出株が重くなる。為替は「膠着の中の高ボラティリティ」という矛盾した状況に置かれており、5/21 の米雇用統計が最初のカタリストとなる。'},
    {'tag': 'AI・技術', 'heading': '「OS 統合」と「エージェント性能」の二軸勝負へ', 'accent': '#2D5BB8',
     'body': '[[Gemini Intelligence]] の発表は、Google が「自社が握る Android × Gmail × Search × Chrome というエコシステムを AI の文脈で再定義する」選択をしたことを示す。これに対し Anthropic は Claude Code のエンタープライズ浸透（54%）、OpenAI は ChatGPT Super App による垂直統合でそれぞれ異なる戦場を選ぶ。3 社の戦略は「OS 統合 vs エンタープライズコーディング vs エコシステム垂直化」と読み解ける。__次の勝負はエージェントのタスク完了率と企業への収益化__ だ。'},
    {'tag': '産業・業界', 'heading': 'コンサルの「死の谷」と Google I/O の直撃弾', 'accent': '#2E6B52',
     'body': '[[BCG]] が「AI 投資 2 倍・エージェント専用予算 30%」を報告した同じ週に、[[Deloitte]] が「74% 期待 vs 20% 実績」という冷たいデータを突きつけた。そして Google I/O が発表した Gemini Intelligence は SIer のワークフロー自動化事業に真正面から競合する。コンサル業界は「Gemini 導入支援業者」に転身するか、独自の付加価値を作り続けるか、二者択一の岐路に立たされた。__KPMG の 400 人削減が象徴するように、AI は既に人員配置を変えている__。'},
    {'tag': '明日へ', 'heading': 'Switch 2 発売 6 日前の「ゲーム産業の賭け」', 'accent': '#C9B98A',
     'body': 'Nintendo Switch 2 まで 6 日。¥59,980 という高価格帯での出発は「ゲームハードはカジュアル層に安く届ける」という従来モデルとの決別だ。[[SQUARE ENIX]] が Tales of Arise で試す Switch 2 の性能実証、[[Cygames]] が米競馬で試す IP の国際展開——いずれも「ゲームがエンタメの中心に戻ってくる」という賭けだ。__AI が知的作業を代替する時代に、人間の情動に訴えるゲームの価値がむしろ上がる__という逆説も見えてくる。'},
  ],
  'takeaways': [
    {'num': '01', 'tag': '為替', 'color': '#B8860B',
     'text': '[[Warsh FRB 議長]]就任でドル高基調は当面継続。USD/JPY の 160 円超えには MoF 介入が立ち塞がり、実質レンジは 158〜160 円に収縮。'},
    {'num': '02', 'tag': 'AI', 'color': '#2D5BB8',
     'text': 'Google I/O 2026 は「AI ＝ OS 統合時代」の幕を開けた。Anthropic・OpenAI との差別化は性能ではなくエコシステムの深さへ移行。__次の競争軸はエージェントのタスク完了率__。'},
    {'num': '03', 'tag': '産業', 'color': '#2E6B52',
     'text': 'コンサル業界の「死の谷」（期待 74% vs 実績 20%）は Business Transformation への本気度の問題。[[Gemini Intelligence]] 直撃で転換スピードが加速する。'},
  ],
  'related': [
    {'date': '2026-05-18', 'title': '前号: Anthropic + Gates Foundation / Deloitte 削減全容'},
    {'date': '2026-05-16', 'title': 'S&P500 7,500 試行 / Warsh データドリブン宣言'},
    {'date': '2026-05-15', 'title': 'Google I/O 5/19 予告 / Switch 2 価格改定発表'},
  ],
}

# =============================================================================
# RENDERING
# =============================================================================

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
print(f'OK - build/email.html ({size_kb:.1f} KB)')
