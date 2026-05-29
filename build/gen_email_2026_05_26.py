"""2026-05-26 号 HTML メール生成スクリプト (火曜: FX/AI/IT/Economy/Game)"""
import re, sys, os
sys.stdout.reconfigure(encoding='utf-8')
os.chdir(os.path.dirname(os.path.abspath(__file__)) + '/..')

ISSUE_DATE = '2026-05-26'
ISSUE_NO   = '20260526'
WEEKDAY    = '火'
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
# DATA — 2026-05-26 (火曜: FX / AI / IT / Economy / Game)
# ═══════════════════════════════════════════════════════════════════════════════
cats = [
  {
    'id': 'fx', 'name_jp': '為替', 'name_en': 'Foreign Exchange', 'glyph': '¥',
    'accent': '#B8860B',
    'summary': 'タカ派 FOMC 議事録がドル急騰を演出し USD/JPY は 159 円台で 7 連騰。4 票反対という 1992 年以来初の少数意見多発は利上げ復活シグナルとして市場に衝撃を与えた。今週は 5/28 FOMC 議事録公表・5/30 PCE インフレが次の分水嶺となる。',
    'articles': [
      {'score': 93, 'time': '07:00', 'source': 'Forex.com',
       'title': 'タカ派FOMC議事録でドル急騰──USD/JPY 159円台でブレイクアウト圏へ迫る',
       'url': 'https://www.forex.com/en-us/news-and-analysis/us-dollar-jumps-on-hawkish-fomc-minutes-usd-jpy-near-breakout-zone/',
       'thumb': None,
       'bullets': [
         '[[FOMC議事録]]が「緩和バイアスの削除を検討」と開示──市場はこれを**利上げ復活への地ならし**と解釈し、ドルは主要通貨に対して一斉高。USD/JPY は 159 円台で **7 連騰**を達成',
         '利上げ確率（年内）が火曜朝ゼロから一気に **3.5%** へ跳ね上がり、ドル円のシフト幅はわずか数時間で約 80 銭——__緩和終了の「シグナル」が現実の「プライシング」に転化した瞬間__',
         '次のカタリストは 5/28 FOMC 議事録の本文開示・**5/30 PCE コアデフレーター**——特に PCE が 3.5% 超を維持するなら 160 円突破と介入リスクが同時進行するシナリオ',
       ],
       'related': {'axis': '復状', 'ref_title': 'Warsh 就任初週（5/25）', 'ref_date': '2026-05-25', 'note': '就任直後のタカ派宣言が FOMC 議事録で補強されたかたちとなり、ドル高シナリオの信頼度が増した。'}},
      {'score': 90, 'time': '06:00', 'source': 'Federal Reserve',
       'title': 'FRBが4票反対で3.5-3.75%据え置き──1992年以来初の少数意見多発',
       'url': 'https://www.federalreserve.gov/newsevents/pressreleases/monetary20260429a.htm',
       'thumb': 'https://www.federalreserve.gov/images/social-media/social-default-image-opengraph.jpg',
       'bullets': [
         '**4/29 FOMC** 声明で FF 金利を **3.5〜3.75%** に 3 回連続据え置き。賛成 8・反対 4 という**1992 年 10 月以来 34 年ぶりの異例票数**——反対票は「将来の対応への懸念」として市場に衝撃',
         '[[FRB]] 内部では「緩和バイアス削除派」と「現状維持継続派」が拮抗——声明文の文言変更が「利上げへの地ならし」と読み切られており、__1 回の会合で相場の均衡が様変わりした__',
         'USD/JPY は声明直後に 158 円台から 159 円台へ急伸——日銀 6 月会合利上げ観測（55%）と FRB 据え置きの組み合わせが**金利差縮小より拡大方向**に相場を傾ける逆説的な状況',
       ],
       'related': None},
      {'score': 87, 'time': '09:00', 'source': 'FXStreet',
       'title': 'Forex Today: ドル高・タカ派FRB再評価後にFOMC議事録へ焦点移行',
       'url': 'https://www.fxstreet.com/news/forex-today-us-dollar-benefits-from-hawkish-fed-repricing-focus-shifts-to-fomc-minutes-202605200756',
       'thumb': 'https://editorial.fxsstatic.com/images/i/Federal-Reserve-Building_5_Medium.png',
       'bullets': [
         '[[ドル円]]が**タカ派 FRB の再評価**を織り込む形でジリ高継続——FXStreet は「FOMC 議事録本文公開（5/28）まで短期的なドル優位は変わらない」と分析',
         'EUR/USD は 1.1240 付近で上値重く、ECB が 6 月に追加利下げを示唆する中での**ユーロとドルの逆方向サイクル**がドル全面高を演出',
         '__今週の為替相場は PCE（5/30）一点集中型__——3.5% 超維持なら「FRB 利上げ」が現実味を帯び、2% 台急低下なら利下げ再期待でドル安反転の二択シナリオ',
       ],
       'related': None},
      {'score': 82, 'time': '05:00', 'source': 'BitMEX Research',
       'title': 'USD/JPY 2026年予測──159円台でドル優位継続、円高転換のKeyLevel分析',
       'url': 'https://www.bitmex.com/blog/usd-jpy-forecast-2026',
       'thumb': None,
       'bullets': [
         'BitMEX の 2026 年 USD/JPY 年間予測：ベースケースは **155〜162 円**のレンジ内で推移し、円高転換の「閾値」は BOJ が 3 回連続利上げ達成後',
         '[[円高]]転換には日米金利差の逆転が必要条件——FRB が 3.5〜3.75% を維持する限り、BOJ の 0.5% 刻み利上げでも**実効金利差は依然 3% 超**と計算される',
         'キーレベルは上値 **161.95 円**（財務省介入トリガーゾーン）と下値 **155.50 円**（BOJ 複数回利上げ後の節目）——__現状は上値が重く下値も堅い「均衡なき均衡」状態__',
       ],
       'related': None},
      {'score': 77, 'time': '06:30', 'source': 'TradingEconomics',
       'title': 'ドル円159台、円安持続──4月インフレ軟化でBOJ緩和継続観測',
       'url': 'https://tradingeconomics.com/japan/currency',
       'thumb': None,
       'bullets': [
         '4 月の日本 CPI（コア除く）が鈍化し、市場が BOJ の緊急利上げ観測を後退——ドル円は金曜 **159.20 円**クローズ後も週明けの薄商いを挟み底堅さを維持',
         '国内インフレ圧力の後退が BOJ 内の「様子見派」を勢いづかせ、**6 月利上げ確率を 55% から 45% 前後**へ押し下げる材料として機能',
         '[[ドル円]]の次の転換点は BOJ 6/19 会合——__利上げ見送りなら 160 円台接近→介入リスク上昇という逆説的なシナリオ__が浮上し、相場の方向性が不透明に',
       ],
       'related': None},
    ]
  },
  {
    'id': 'ai', 'name_jp': 'AI', 'name_en': 'Artificial Intelligence', 'glyph': '◆',
    'accent': '#2D5BB8',
    'summary': 'Anthropic がエージェント自己改善システム「Dreaming」を発表し Harvey 社の完了率を 6 倍化。Claude Opus 4.7 の全面 GA と GPT-5.5 の PhD 数学クリアが重なり、モデル競争からエージェントハーネス競争へ戦場が移行した。',
    'articles': [
      {'score': 94, 'time': '08:00', 'source': 'VentureBeat',
       'title': 'Anthropic「Dreaming」──エージェントが過去セッションから自己学習、Harvey完了率6倍達成',
       'url': 'https://venturebeat.com/technology/anthropic-introduces-dreaming-a-system-that-lets-ai-agents-learn-from-their-own-mistakes',
       'thumb': 'https://images.ctfassets.net/jdtwqhzvc2n1/5I4zuQbx738JfOAQbToZdD/b5c7c367d367e55b484dc1480e399cba/Nuneybits_Vector_art_of_burnt-orange_moonlit_sleeper_dissolving_71200d84-78a7-48eb-890e-fb59ef136db1.webp?w=800&q=75',
       'bullets': [
         '[[Anthropic]] が「Dreaming（夢見る）」を 5/6 に正式リリース——スケジュール化されたプロセスが**過去のエージェントセッションを精査**し、パターンを抽出・記憶を整理することでエージェントが自動的に改善する仕組み',
         'リーガル AI の [[Harvey]] が最初に実装——タスク完了率が**約 6 倍**に跳ね上がり「繰り返しの失敗から学ぶ AI」の実用価値を初めて定量化。__自己反省するエージェントが企業 AI の次世代標準になる__',
         'Dreaming は単体機能ではなく **Outcomes・マルチエージェント連携**と組み合わせて Claude Managed Agents に統合——記憶 → 反省 → 修正 → 再実行のループで長期自律タスクの信頼性が大幅向上',
       ],
       'related': None},
      {'score': 91, 'time': '07:00', 'source': 'Anthropic',
       'title': 'Claude Opus 4.7 全面GA──高解像度ビジョン2576px・1Mコンテキスト、価格据え置き',
       'url': 'https://www.anthropic.com/news/claude-opus-4-7',
       'thumb': None,
       'bullets': [
         '[[Claude Opus 4.7]] は高解像度画像対応（**最大 2576px / 3.75MP**）を Claude シリーズ初搭載——スライド・設計図・財務資料などの視覚情報処理精度が前世代比で大幅向上し、マルチモーダル業務用途を本格解放',
         '1M トークン文脈窓・128k 最大出力・アダプティブ思考を維持しつつ**上位モデルとして難易度最高の SW エンジニアリングで際立つ改善**——「最も困難なタスクも Opus 4.7 に渡せる」の評価',
         '価格は Opus 4.6 と同水準（入力 $5・出力 $25 / M トークン）で全 API・製品から即利用可能——__「性能は上げるがコストは維持」というプライシング戦略が企業導入の加速装置になりうる__',
       ],
       'related': {'axis': '続報', 'ref_title': 'エージェント制御プレーン（5/24）', 'ref_date': '2026-05-24', 'note': 'Opus 4.7 は制御プレーン上で動くモデル基盤として、エージェント競争を最高性能のエンジンで支える。'}},
      {'score': 88, 'time': '09:00', 'source': 'R&D World',
       'title': 'GPT-5.5 ProがPhD水準数学を1時間で──Fields賞受賞者証言、ハーネス競争が本番戦場へ',
       'url': 'https://www.rdworldonline.com/this-week-in-ai-research-fields-medalist-says-gpt-5-5-pro-did-phd-level-math-in-an-hour-anthropic-teaches-claude-to-dream/',
       'thumb': 'https://www.rdworldonline.com/wp-content/uploads/2026/05/AdobeStock_992948266-scaled.jpeg',
       'bullets': [
         '[[Fields賞]] 受賞者がライブ実験を公開——[[GPT-5.5]] Pro に未解決の博士レベル数学問題を入力したところ **1 時間以内に証明ステップを完成**させた事実が学術コミュニティを震撼',
         '**Codex vs Claude Code** が「現実の生産性ベンチ」として定着——OpenAI は Codex 利用 2 か月無料を企業に提供、Anthropic は Dreaming で品質で対抗。__モデルの点数勝負よりハーネスの信頼性競争が実態__',
         '4/24 リリースの GPT-5.5 は「エージェント作業モデル」として設計——長期コーディング・複雑タスク計画・知識集約業務が主戦場で、ベンチマーク指標では捉えきれない能力差が実務評価で噴出中',
       ],
       'related': None},
      {'score': 85, 'time': '08:30', 'source': 'Axios',
       'title': 'Karpathy、Anthropic入社──事前学習チームへ合流、Claude活用の研究加速プロジェクト始動',
       'url': 'https://www.axios.com/2026/05/19/anthropic-openai-karpathy-andrej-claude',
       'thumb': None,
       'bullets': [
         '[[Andrej・Karpathy]] が [[Anthropic]] の事前学習チームに参画——**Claude 自身を使って事前学習研究を加速する新チーム**を立ち上げ。AI 研究者が自社モデルの力を借りて次世代モデルを開発する「**AI 自己強化ループ**」が現実化',
         'OpenAI 創設メンバーがライバル Anthropic へ移籍——Karpathy の専門である**大規模言語モデルの事前学習・解釈可能性研究**は Claude の性能向上に直結し、企業市場シェア争いに影響',
         '__モデル開発の最前線人材の移動がエコシステム占有率を動かすリスク__——OpenAI は GPT-5.5 の無料提供でユーザー基盤固定化を急ぐ一方、Anthropic は Karpathy 採用でモデル品質の長期優位を狙う',
       ],
       'related': {'axis': '復状', 'ref_title': 'Karpathy 合流速報（5/22）', 'ref_date': '2026-05-22', 'note': '当初速報だったが詳細な研究方針が確認され、事前学習チームでの具体的な役割が明確化された。'}},
      {'score': 82, 'time': '07:30', 'source': "Let's Data Science",
       'title': 'Code with Claude 2026──Dreaming・Outcomes・マルチエージェント連携が一気に実装',
       'url': 'https://letsdatascience.com/blog/anthropic-dreaming-claude-managed-agents-self-improving-may-6',
       'thumb': 'https://letsdatascience.com/lds-og-image.png',
       'bullets': [
         '**Code with Claude 2026** カンファレンス（5/6）で [[Anthropic]] が一挙 5 機能を発表——Dreaming（自己改善）・Outcomes（成功指標の自動学習）・マルチエージェント連携・メモリ開放・スケジュール実行が同時リリース',
         'Claude Managed Agents の「**メモリ開放**」が最大の変更点——異なるエージェントが同一コードベースのノートを共有し、新しいエージェントが前任の知見を引き継いで即座に高品質な出力を出せる',
         '__エージェントが「学び・覚え・引き継ぐ」能力を持った今、従来の RPA や Rule-based 自動化が置き換わる速度が加速__——企業 AI 導入の計画サイクルを半年→四半期に圧縮する可能性',
       ],
       'related': None},
    ]
  },
  {
    'id': 'it', 'name_jp': 'IT-Consulting', 'name_en': 'IT &amp; Consulting', 'glyph': '▲',
    'accent': '#2E6B52',
    'summary': 'NTTデータが「コンサルティングセグメント」を新設しアクセンチュアへの追い上げを本格化。アクセンチュアも Databricks・ServiceNow との連携でエージェント AI 本番化を加速し、日米コンサル大手の AI 変革競争が激化した。',
    'articles': [
      {'score': 93, 'time': '08:30', 'source': 'NTTデータグループ',
       'title': 'NTTデータ「コンサルティングセグメント」新設──AIフラグシップ案件牽引、FY2030 EBITDA 1.2兆円へ',
       'url': 'https://www.nttdata.com/global/ja/news/release/2026/050807/',
       'thumb': 'https://www.nttdata.com/global/ja/-/media/assets/images/sns_share.png?rev=583d5951ace649c08be7a88679328a3b',
       'bullets': [
         '[[NTTデータ]] が 5/8 に**コンサルティング×AI**を中核とした組織転換を発表——既存 3 セグメントに加え全社横断の「コンサルティングセグメント」を新設。経営層へのトップアプローチで **AI フラグシップ案件**を自ら創出する攻撃的な体制へ',
         'テクノロジーセグメントを再編し**AI 事業本部を新設**——分散していた AI 関連機能を集約した「テクノロジービジネス／AI／インフラ」3 本部体制へ移行。__コンサルからインフラまでを End-to-End で提供するフルスタックモデルは、アクセンチュアが長年展開してきた型__',
         'FY2030 の **EBITDA 1.2 兆円**をゴールに設定——AI 時代に特有の「経営変革構想策定→事業モデル再設計→新価値創出の具体化→実装」を一体提供する能力で、SI 事業からの本格転換を宣言',
       ],
       'related': {'axis': '続報', 'ref_title': 'コンサル大手 AI 代替（5/24）', 'ref_date': '2026-05-24', 'note': '欧米コンサルが AI で人員を削減する一方、NTTデータは AI でコンサル機能を拡大する「使い方の違い」が対照的。'}},
      {'score': 90, 'time': '07:00', 'source': 'Accenture',
       'title': 'Accenture × Databricks──大規模AIエージェント採用を全業種展開',
       'url': 'https://newsroom.accenture.com/news/2026/accenture-and-databricks-accelerate-enterprise-adoption-of-ai-applications-and-agents-at-scale',
       'thumb': 'https://newsroom.accenture.com/news/2026/media_19a4f469815ca6b6ee41ba7fefa7d9927eef19eb4.png?width=1200&format=pjpg&optimize=medium',
       'bullets': [
         '[[アクセンチュア]] と Databricks が AI アプリケーション・エージェントの大規模エンタープライズ展開を加速するための戦略的パートナーシップを発表——金融・製造・ヘルスケア・小売の 4 業種でフルスタック AI 実装が主眼',
         '**データ統合→モデル微調整→エージェント本番化**の一気通貫を Databricks の Data Intelligence Platform と Accenture の業種知見で提供——__単一ベンダーでは実現しがたい「データ品質担保＋実装速度」の二律背反を解決するモデル__',
         '今後 3 年で**数百の顧客企業**に展開予定、生産性向上率 30〜50% をベンチマーク目標として設定。AI エージェント時代のコンサルティング収益モデルを具現化する先行事例として注目',
       ],
       'related': None},
      {'score': 86, 'time': '09:00', 'source': 'NTT DATA',
       'title': 'NTT DATA、AIでCO₂変換触媒発見を20倍加速──脱炭素産学連携が社会実装フェーズへ',
       'url': 'https://us.nttdata.com/en/news/press-release/2026/may/ntt-data-uses-ai-to-accelerate-co2-capture-and-conversion-research',
       'thumb': 'https://dam.nttdata.com/api/public/content/58a7bf38e10945b5b81adc3671b9a129?v=facc38d9',
       'bullets': [
         '[[NTTデータ]] がパレルモ大学・カタンザーロ大学と共同で開発した **AI フレームワーク**が CO₂ 変換用触媒の発見プロセスを**従来比 20 倍に加速**——計算化学と機械学習の融合で「試行錯誤型の材料探索」を「予測型の最適化探索」へ転換',
         '産業プロセスの脱炭素化において触媒設計がボトルネックだった——CO₂ を有用な化学物質（メタノール・CO 等）に変換する触媒の選定が**数年単位の実験**から**数週間の AI 計算**へ短縮され、CCS/CCU の現実実装に道筋',
         '__AI による科学加速（AI for Science）がコンサル・IT 企業の新価値領域として浮上__——NTTデータが「テクノロジー実装だけでなく科学的発見への貢献」を示した今回の事例は、IT 企業の ESG 戦略とも直結',
       ],
       'related': None},
      {'score': 83, 'time': '08:00', 'source': 'Accenture',
       'title': 'ServiceNow × Accenture、FDEプログラム──エージェントAIを本番環境へ共同移行支援',
       'url': 'https://newsroom.accenture.com/news/2026/servicenow-and-accenture-launch-forward-deployed-engineering-program-to-scale-agentic-ai-across-the-enterprise',
       'thumb': 'https://newsroom.accenture.com/news/2026/media_1234823475cfefd0870b4b9f98ace3f47701b3202.png?width=1200&format=pjpg&optimize=medium',
       'bullets': [
         'ServiceNow の AI-native FDE チームと [[アクセンチュア]] の業種別 FDE が**顧客環境に常駐**して agentic AI ワークフローを ServiceNow AI Platform 上で共同構築——「PoC から本番」への移行失敗率を下げる「ハンドオン型支援」',
         '企業が AI を「試行」から「生産規模」に移せない最大の壁は**本番環境特有の例外処理・ガバナンス設計**——FDE プログラムはこの壁を両社専門家が顧客の中で直接解体するアプローチ',
         '__コンサルが「提案書を書く」時代から「実装まで入り込む」時代へのシフト__——Accenture の FDE 投資は McKinsey/BCG との差別化戦略として、実装能力のある「ハイブリッドコンサル」像を強化',
       ],
       'related': None},
      {'score': 80, 'time': '08:30', 'source': 'NTTデータグループ',
       'title': 'NTTデータグループ、AIを中核とした成長戦略加速──End-to-Endフルスタック体制強化',
       'url': 'https://www.nttdata.com/global/ja/news/release/2026/050806/',
       'thumb': 'https://www.nttdata.com/global/ja/-/media/assets/images/sns_share.png?rev=583d5951ace649c08be7a88679328a3b',
       'bullets': [
         '[[NTTデータグループ]] が「AI-Empowered New Value &amp; Productivity」と「Next-Gen Infrastructure」を 2 本柱とする中期成長戦略を具体化——**コンサルティングからインフラまでを一体提供する End-to-End フルスタック**化を完了宣言',
         '**シリコンバレー新会社**が 2027 年度に AI エージェント関連ビジネスで**売上 3,000 億円**を目指して本格始動——日本のリソースと北米スタートアップ文化を掛け合わせた「ハイブリッドイノベーション」拠点',
         '__SIer からグローバルコンサルへの変身を目指す NTTデータの施策群が、アクセンチュア・デロイトと正面から競合する領域に踏み込みつつある__',
       ],
       'related': None},
    ]
  },
  {
    'id': 'economy', 'name_jp': '経済', 'name_en': 'Economy', 'glyph': '■',
    'accent': '#8E2A19',
    'summary': '日経平均が一時 6 万 5000 円台に突入し過去最大規模の上昇幅を記録。S&amp;P500 Q1 EPS +27% の好決算が追い風となる中、今週は FOMC 議事録（5/28）と PCE（5/30）が方向性を決する。',
    'articles': [
      {'score': 95, 'time': '15:30', 'source': '日本経済新聞',
       'title': '日経平均、一時6万5000円台突破──2000円超高で過去最大級の上昇',
       'url': 'https://www.nikkei.com/article/DGXZQOUB250C90V20C26A5000000/',
       'thumb': 'https://article-image-ix.nikkei.com/https%3A%2F%2Fimgix-proxy.n8s.jp%2FDSXZQO3066611025052026000000-1.jpg?auto=compress&bg=FFFF&crop=focalpoint&fit=crop&fm=jpg&fp-x=0.9&fp-y=0.89&h=630&upscale=false&w=1200&s=5a68bf4791465333709f3b6a4623ff08',
       'bullets': [
         '[[日経平均]] が終値 **6 万 5158 円**で史上最高値を更新——上昇幅 2000 円超は 2024 年 8 月 6 日に次ぐ過去最大規模。米 NVIDIA 決算や OpenAI IPO を材料に **AI・半導体関連が怒涛の買い**を集めた',
         '「S&amp;P500 全体で**前年比 27% 増益**」という Q1 好決算の現実が、「AI バブル懸念」の弱気論を一掃——__企業収益が実態を示したことで相場上昇に「説明責任」が生まれ機関投資家の逡巡が吹き飛んだ__',
         '次の焦点は FOMC 議事録（**5/28**）と PCE デフレーター（**5/30**）——タカ派継続なら利益確定売りリスク、ハト派回帰なら更なる株高の二択。日米同時高は続くか',
       ],
       'related': None},
      {'score': 92, 'time': '08:00', 'source': '野村証券 ウェルスタイル',
       'title': '野村証券、S&P500年末目標7,500に引き上げ──イラン情勢収束とAI需要拡大で強気継続',
       'url': 'https://www.nomura.co.jp/wealthstyle/article/0714/',
       'thumb': 'https://www.nomura.co.jp/wealthstyle/article/0714/images/og_a_0714_01.png',
       'bullets': [
         '[[野村證券]] ストラテジストが S&amp;P500 の 2026 年末目標を **7,500** に引き上げ——2027 年末 7,900・2028 年末 8,300 と強気シナリオを継続。背景は「イラン情勢収束による地政学リスク後退」と「AI サプライチェーン需要の持続的拡大」',
         '4 月の **10.4% 急反発**（2020 年 11 月以来最大の月間上昇）が「恐怖から欲望へ」の転換点——**テック・AI セクター集中への懸念より「FOMO（乗り遅れ恐怖）」が強まる**局面へと移行',
         'リスクシナリオ：FRB が利上げに転じた場合は S&amp;P500 **7,000 割れ**も——__AI バブルの入口か出口かを問われた時、企業収益がまだ語りかける間は上昇余地あり__',
       ],
       'related': None},
      {'score': 88, 'time': '07:00', 'source': '株式ポートフォリオ',
       'title': 'S&P500 Q1 2026 EPS +27%──4年ぶり好決算、テック・AI主導で増益幅が予想を大幅超過',
       'url': 'https://kabukiso.com/america/outlook/2026/sp500_may.html',
       'thumb': None,
       'bullets': [
         '2026 年 Q1 の S&amp;P500 EPS は**前年同期比 +27%**——アナリスト予測（+20%）を大幅に超え、好決算サプライズが続出。[[NVIDIA]] と [[Microsoft]] が突出した成長を牽引、ハイパースケーラー 3 社の AI インフラ投資継続も収益押し上げ要因',
         '4 年ぶりの好決算という評価に加え、**コンセンサスを上方修正する銘柄比率が 75%** に達し過去最高水準——__稼げる AI がついに株価を正当化しはじめた__',
         '国内でも日経平均採用企業の Q1 決算は **+20%** 増益ペース。円安・AI 需要・半導体景気の三重奏が続く中、**5/30 PCE** が FRB の次の動きを規定し日米株の連動性をテスト',
       ],
       'related': None},
      {'score': 85, 'time': '09:00', 'source': '野村証券 ウェルスタイル',
       'title': '「年内早期7万円」シナリオ浮上──日経平均ストラテジスト見通し相次ぐ上方修正',
       'url': 'https://www.nomura.co.jp/wealthstyle/article/0724/',
       'thumb': 'https://www.nomura.co.jp/wealthstyle/article/0724/images/og_a_0724_01.png',
       'bullets': [
         '[[日経平均]] の年末目標を **63,000 円**（メインシナリオ）に据えつつ、上振れシナリオは「**2026 年末 7 万円台突破**」——昨年末時点での予測から 10,000 円以上の上方修正が相次ぎ、AI 主導の上昇モメンタムが従来の想定を覆した',
         '「AI・半導体の勢いが鮮明」は既知だが、**日本固有の円安恩恵＋企業の株主還元強化**が重なり外国人投資家の継続流入を促進——外需依存度の高い輸出製造業も恩恵を享受',
         '__7 万円達成の条件は「AI 投資が収益化する証拠の積み重ね」と「FRB の利上げ見送り継続」の二点__。今週の FOMC 議事録がその条件の一方を審判する',
       ],
       'related': None},
      {'score': 80, 'time': '06:00', 'source': 'マネックス証券',
       'title': '今週の経済指標カレンダー──FOMC議事録（5/28）・PCE（5/30）が株・円の分岐点',
       'url': 'https://info.monex.co.jp/us-stock/basic-guide/knowledge/schedule2026.html',
       'thumb': None,
       'bullets': [
         '今週の最重要スケジュール：5/27（火）ECB 議事要旨、**5/28（水）FOMC 議事録**（4/29 会合分）、5/29（木）米 GDP 改定値・日本鉱工業生産・小売売上高、**5/30（金）PCE コアデフレーター**',
         '[[FOMC]] 議事録で「緩和バイアス削除の議論がどこまで深まっていたか」が判明——議事録が想定以上にタカ派なら **USD/JPY 160 円突破・日本株の利益確定売り**が連動して起きるリスク',
         'PCE コアが **3.5% 超**を維持した場合と **3.2% 以下**に低下した場合で市場のシナリオが真逆になる——__今週は「相場が語りかけるのではなく、数字が相場を決める」週__',
       ],
       'related': None},
    ]
  },
  {
    'id': 'game', 'name_jp': 'ゲーム', 'name_en': 'Gaming', 'glyph': '●',
    'accent': '#5E3D8C',
    'summary': 'Switch 2 が値上げ後に各地で完売、古川社長がメモリ高騰・円安を理由に説明し未発表新作ソフトの追加も予告。SIE が PS5 State of Play を 6/2 確定させ、Forza Horizon 6 の日本舞台オープンワールドが好評を集める。',
    'articles': [
      {'score': 93, 'time': '10:00', 'source': 'ファミ通',
       'title': 'Switch 2値上げを古川社長が説明──メモリ高騰・円安・石油高、未発表新作ソフトも予告',
       'url': 'https://www.famitsu.com/article/202605/74473',
       'thumb': 'https://cimg.kgl-systems.io/camion/files/74473/thumbnail_gaBU.jpg?x=1280',
       'bullets': [
         '[[任天堂]] 古川俊太郎社長が Switch 2 の **1 万円値上げ**（49,980 → 59,980 円、5/25 実施）を公式に説明——AI データセンター向け需要急増による**メモリ供給逼迫・円安 156 円台・エネルギーコスト上昇**の三重苦が要因',
         '「発表済みのソフト以外も用意している」と**未発表タイトルの追加**を示唆——値上げへの反発を抑制する「コンテンツ価値向上」の補填戦略として、次世代ソフトウェアラインナップへの期待が再燃',
         '__値上げで旧価格 4 万 9980 円時代の「最も売れたゲーム機」という伝説が終わる一方__、高価格帯でも買い続けるコア層の存在が任天堂エコシステムの底堅さを証明',
       ],
       'related': {'axis': '続報', 'ref_title': 'Switch 2 値上げ騒動（5/24）', 'ref_date': '2026-05-24', 'note': '前日の購入殺到から翌日の公式説明へと発展し、値上げの正当性をめぐるコミュニティの議論が活発化。'}},
      {'score': 91, 'time': '11:00', 'source': 'SWITCH速報',
       'title': 'Switch 2、値上げ後に各地で完売──駆け込み購入と在庫枯渇で旧価格品が市場から消える',
       'url': 'https://switchsoku.com/sale/122388',
       'thumb': 'https://switchsoku.com/wp-content/uploads/2026/05/switch2-kanbai.webp',
       'bullets': [
         '5/25 の値上げ実施と同時に旧価格在庫が全国量販店で完売——ビックカメラ・ヤマダ電機・ゲオなど主要チェーンで「**在庫ゼロ**」が相次ぎ、オンラインも朝には売り切れ。新価格 **59,980 円**での再入荷待ちが始まった',
         '[[任天堂]] マイニンテンドーストアは前日夜に在庫を復活させ**ラストチャンス販促**——転売対策と旧価格消化の二重目的が功を奏し、転売市場での流通は新上限価格に押えられる形',
         '__旧価格在庫の消滅は「1 回目のマジックライン」であり、新価格 6 万円での市場定着試験が今週から始まる__——ARPPU（ユーザーあたり平均収益）の上昇が任天堂の株価にも連鎖するか注目',
       ],
       'related': None},
      {'score': 87, 'time': '09:00', 'source': 'インサイド',
       'title': 'Forza Horizon 6、日本舞台の東京5倍サイズ──Xbox/PC先行リリース後の反響と評価',
       'url': 'https://www.inside-games.jp/article/2026/05/06/180933.html',
       'thumb': 'https://www.inside-games.jp/imgs/ogp_f/1721330.png',
       'bullets': [
         '5/19 リリースの [[Forza Horizon 6]] が「**日本・東京を舞台にした史上最大の Forza マップ**」として高い評価——Tokyo エリアは過去シリーズ最大都市の 5 倍規模で再現、Shibuya・Shinjuku・Akihabara を実名で収録したリアリティが話題',
         'Xbox Game Pass に含まれた初日から Steam / Xbox 両プラットフォームで**レビュー平均 91 点**（Metacritic）——PC ゲーマーへの訴求力と Switch 2 移植の可否が、今後の売上拡大シナリオに影響',
         '__日本ゲーム市場が「舞台」として世界に選ばれる現象が加速__——Forza が東京を選んだ 2026 年は、GTA VI のフロリダ以来の「文化的舞台選定」として語られる',
       ],
       'related': None},
      {'score': 84, 'time': '08:00', 'source': 'EventHubs',
       'title': 'PlayStation State of Play 6月2日確定──SIEが夏向けPS5新作ラインナップを世界発信',
       'url': 'https://www.eventhubs.com/news/2026/may/20/playstation-state-play-june-2nd/',
       'thumb': 'https://media.eventhubs.com/images/2026/05/20_state-play-bnrt.webp',
       'bullets': [
         '[[SIE]] が 6 月 2 日に **PlayStation State of Play** を開催確定——夏向けの PS5 タイトルを世界へ公開、Nintendo Direct / Xbox Games Showcase と 2026 年夏のゲーム情報戦が本格化',
         'E3 廃止後の「分散型情報発信戦略」が定着——各社が独自のイベントを任意に設定する中、State of Play の「固定視聴者 200 万人以上」の実績が SIE に有利な情報発信の場を確保',
         'Switch 2 値上げ直後のタイミングでの開催は**PlayStation 陣営のポジティブな対比**を狙った可能性も——__値頃感でも内容でも PS5 の魅力を提示できれば、Switch 2 購入をためらう層を取り込むチャンス__',
       ],
       'related': {'axis': '続報', 'ref_title': 'State of Play 速報（5/24）', 'ref_date': '2026-05-24', 'note': '確定情報として注目度が増す中、注目タイトルの予測記事も急増している。'}},
      {'score': 79, 'time': '12:00', 'source': 'Yahoo!ニュース エキスパート',
       'title': 'Switch 2値上げで「子どもの遊びの格差」浮上──専門家が教育視点で影響を分析',
       'url': 'https://news.yahoo.co.jp/expert/articles/45ab86d05a0dea1d0f105ec3564915505a1b49ca',
       'thumb': 'https://newsatcl-pctr.c.yimg.jp/t/iwiz-yn/rpr/shinoharashuji/02619461/title-1778302807744.jpeg?exp=10800',
       'bullets': [
         '教育専門家が指摘：Switch 2 が **6 万円**の高額ゲーム機になることで「買える家庭と買えない家庭」の乖離が生まれ、友達との遊び格差が子どもの**社会的孤立リスク**につながる懸念',
         '一方で「子どもへのゲーム出費は聖域」とみる家庭も多く、**実際の普及率はスマートフォン普及初期に近い**軌跡をたどる可能性——教育費とゲーム費の優先度が問われるシーズンに突入',
         '__同様の議論は PS3 60GB（49,980 円）発売時（2006 年）にもあったが、最終的に価格は受け入れられた__——任天堂ブランドの信頼と「遊びの質」で正当化できるかが焦点',
       ],
       'related': None},
    ]
  },
]

reflection = {
  'title': '金利の天井とエージェント革命の夜明け',
  'subtitle': 'USD/JPY 159円台・日経65000円突破・Anthropic Dreaming発表が同日に重なる、2026年5月26日（火）',
  'lead': '本日5分野・25本のニュースから浮かび上がる最大のテーマは [[FRBタカ派転換]] と [[AIエージェント自己学習]] の同時進行である。金融市場では利上げ復活シグナルがドル円を159円台の7連騰に押し上げ、AIの世界ではAnthropicが「夢を見るエージェント」を実装した。日経平均の6万5000円突破・Switch 2の6万円市場定着・NTTデータのコンサル組織転換が重なり、__2026年5月26日は「金利の天井」と「AIの底入れ」が同時に語られた日__として記憶されうる。以下、各カテゴリを横断して読み解く──',
  'pull_quote': '「エージェントが夢を見はじめた日、AIの戦場はモデルのベンチマーク比較から__ハーネスの信頼性競争__へと移った。」',
  'sections': [
    {'tag': '総論', 'heading': '金利の壁とAIの自律が交差した一日', 'accent': '#1A1A1A',
     'body': '本日の5分野を貫く構造は「**金利上昇圧力とAI投資の続行**」という矛盾した二軸の共存だ。FRB は 4 票反対という異例の票数で据え置きを決め、利上げ復活シグナルを出しながらも、S&P500 は Q1 EPS +27% 好決算で最高値圏を維持している。[[金融と実体経済の乖離]] は続くが、AI が収益化しはじめた現実が相場を正当化しつつある。__「バブルだから崩れる」と「稼げるから続く」の綱引きは今週の FOMC 議事録・PCE で一度答えが出る。__'},
    {'tag': '為替', 'heading': 'タカ派議事録がドル高の「第二波」を呼ぶ', 'accent': '#B8860B',
     'body': 'FOMC の 4 票反対（[[1992 年以来初]]）と「緩和バイアス削除を検討」という文言が、ドル円の 7 連騰を演出した。USD/JPY が 159 円台でブレイクアウト圏に迫る中、**BOJ の 6 月利上げ確率は 45% 前後まで後退**し「利上げしても円高にならない逆説」が定着している。5/28 の FOMC 議事録と 5/30 の PCE が今週の山場だ。__PCE が 3.5% 超を維持するなら 160 円突破→財務省介入の連鎖シナリオが現実味を帯びる。__'},
    {'tag': 'AI', 'heading': 'エージェントが「夢を見る」ことで何が変わるか', 'accent': '#2D5BB8',
     'body': 'Anthropic の「Dreaming」は Harvey 社のタスク完了率を 6 倍にした。これは単なる性能向上ではなく、**AI が「失敗を覚え・反省し・改善する」という自律サイクルを持ちはじめた**ことを意味する。Claude Opus 4.7 の高解像度ビジョン GA と GPT-5.5 の PhD 数学クリアが同週に重なり、__「どのモデルが賢いか」よりも「どのハーネスが信頼できるか」が企業 AI 選定の主軸に移行した。__'},
    {'tag': 'IT', 'heading': 'NTTデータとアクセンチュアの「コンサル×AI」競争', 'accent': '#2E6B52',
     'body': 'NTTデータが「コンサルティングセグメント」を新設し FY2030 EBITDA 1.2 兆円を宣言、同日アクセンチュアは Databricks との全業種エージェント AI 展開を発表した。**SI 企業がコンサル機能を取り込み、コンサルが実装機能を内製化**する同質化が進行中だ。[[NTTデータ]] の CO₂ 触媒加速（20 倍）は「AI for Science」という新価値領域への参入を示す。__コンサルの付加価値が「知識の提供」から「AI が動く実装の品質保証」へと移行するスピードが加速している。__'},
    {'tag': '経済', 'heading': '日経65000円と「稼げるAI」の証明', 'accent': '#8E2A19',
     'body': '日経平均の一時 6 万 5000 円突破は、AI・半導体の企業収益化が相場を正当化しはじめた象徴だ。野村証券は S&P500 年末目標を 7,500 に引き上げ、日経平均 7 万円シナリオも複数のストラテジストが言及しはじめた。**Q1 EPS +27% という事実が「AI バブル」という懸念を封じる構造**になっている。__ただし FRB が利上げに転じた場合は S&P500 7,000 割れシナリオも存在し、今週の経済指標が「良いニュース = 悪いニュース」の逆説を起こしうる。__'},
    {'tag': 'ゲーム', 'heading': 'Switch 2「6万円市場」の本当の試練はこれから', 'accent': '#5E3D8C',
     'body': '5/25 の値上げ後に各地で完売が相次いだが、これは旧価格の駆け込みによる一時的現象だ。古川社長が「メモリ高騰・円安・石油高」を公式に説明し未発表ソフトも追加予告したことで、コンテンツへの期待感が値上げ批判を部分的に中和した。[[Forza Horizon 6]] の東京舞台・SIE の 6/2 State of Play が「対任天堂コンテンツ戦争」の火蓋を切る中、__59,980 円という新価格が長期的に市場に定着するかどうかは、今後 3 か月のソフト販売実績が決める。__'},
    {'tag': '明日へ', 'heading': '今週は「数字が相場を決める週」', 'accent': '#C9B98A',
     'body': '今週は 5/28（FOMC 議事録）・5/29（米 GDP 改定・日本鉱工業生産）・5/30（PCE コア）という密度の高いスケジュールが並ぶ。**AI・株・円の三軸が同時に動く可能性**があり、ポートフォリオの方向感はいずれの数字が出るかで大きく分かれる。Anthropic Dreaming の本番展開・NTTデータのコンサルセグメント稼働・Switch 2 の新価格定着——これら 3 つの「新しい均衡点」が定まるのは 6 月末の決算シーズンになるだろう。__今週は観察の週であり、7 月の行動を決める情報収集の週だ。__'},
  ],
  'takeaways': [
    {'num': '01', 'tag': '為替', 'color': '#B8860B',
     'text': '[[FOMC議事録]]（5/28）と PCE（5/30）が USD/JPY の方向性を決定——タカ派議事録なら **160 円突破・介入リスク**、PCE 低下ならドル安反転の二択が確定する'},
    {'num': '02', 'tag': 'AI', 'color': '#2D5BB8',
     'text': 'Anthropic「[[Dreaming]]」でエージェント完了率 6 倍を実証——**モデル能力の差より「自己改善できるか」が企業 AI 選定の新しい基準**に'},
    {'num': '03', 'tag': '産業', 'color': '#2E6B52',
     'text': '[[NTTデータとアクセンチュア]]が同日に AI 変革施策を発表——**SIer のコンサル化 vs コンサルの実装化**という同質化競争が、2026 年 IT 業界の最重要テーマに浮上'},
  ],
  'related': [
    {'date': '2026-05-25', 'title': '前号: Warsh就任初週・タカ派スタンスと BOJ 利上げ競合'},
    {'date': '2026-05-24', 'title': '2日前号: GPT-5.5 Instant・Gemini 3.5 Flash GA・NTTデータ WinWire 買収'},
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
