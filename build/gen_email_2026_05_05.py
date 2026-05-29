"""generate build/email.html for 2026-05-05 digest (FX, AI, IT-Consulting, Economy, Game)"""
import re, pathlib

ROOT = pathlib.Path(__file__).parent.parent
TMPL = (ROOT / "prompts" / "email-template.html").read_text(encoding="utf-8")
OUT  = ROOT / "build" / "email.html"

FX_ACC  = "#B8860B"
AI_ACC  = "#2D5BB8"
IT_ACC  = "#2E6B52"
EC_ACC  = "#8E2A19"
GM_ACC  = "#5E3D8C"
CDN     = "https://raw.githubusercontent.com/HIDEPON-UMG/news-grasp-assets/main"

def hl(text, acc):
    text = re.sub(r'\[\[(.+?)\]\]',
        lambda m: f'<strong style="background:rgba(0,0,0,0.07);padding:0 3px 1px;border-radius:2px;">{m.group(1)}</strong>',
        text)
    text = re.sub(r'__(.+?)__',
        lambda m: f'<span style="border-bottom:1.5px solid {acc};padding-bottom:1px;">{m.group(1)}</span>',
        text)
    return text

def bul(items, acc):
    return "".join(
        f'<p class="bul" style="padding-left:20px;margin:0 0 8px;font-size:14.5px;line-height:1.9;color:#1A1A1A;">{hl(i, acc)}</p>'
        for i in items
    )

def meta(score, dt, src, url):
    return (
        f'<div class="ng-card-meta" style="font-family:\'JetBrains Mono\',Consolas,\'Courier New\',monospace;'
        f'font-size:11px;color:#5C5A52;letter-spacing:0.5px;margin-bottom:8px;">'
        f'<span style="background:#1A1A1A;color:#FAF7F0;padding:1px 6px;font-weight:700;margin-right:6px;">[{score}]</span>'
        f'{dt} &nbsp;&middot;&nbsp; {src} &nbsp;&middot;&nbsp; '
        f'<a href="{url}" style="color:#5C5A52;text-decoration:none;">元記事 &rarr;</a></div>'
    )

def ttitle(text, url, acc):
    return (
        f'<h3 class="ng-card-title" style="font-size:20px;font-weight:800;line-height:1.4;'
        f'letter-spacing:-0.2px;margin:0 0 12px;color:#1A1A1A;">'
        f'<a href="{url}" style="color:#1A1A1A;text-decoration:none;border-bottom:2px solid {acc};">{text}</a></h3>'
    )

def related_tip(label, link_url, link_text):
    return (
        f'<div style="margin-top:14px;background:#F2EEE3;border-left:3px solid #C9B98A;padding:8px 12px;font-size:12px;color:#5C5A52;">'
        f'&#128279; <strong>関連:</strong> {label} &mdash; '
        f'<a href="{link_url}" style="color:#5C5A52;">{link_text}</a></div>'
    )

def featured_card(score, dt, src, url, ttl, thumb, bullets, acc, tip_html=""):
    return f"""
<tr><td class="ng-card-pad" style="padding:24px 36px;background:#FAF7F0;border-bottom:1px solid #EDEAE3;">
  {meta(score, dt, src, url)}
  {ttitle(ttl, url, acc)}
  <div class="ng-feature-img" style="margin-bottom:16px;">
    <a href="{url}" class="db tdn"><img src="{thumb}" width="568" style="width:100%;max-height:280px;object-fit:cover;display:block;border:1px solid #E2DED4;" alt=""></a>
  </div>
  {bul(bullets, acc)}
  {tip_html}
</td></tr>"""

def side_card(score, dt, src, url, ttl, thumb, bullets, acc, tip_html=""):
    return f"""
<tr><td class="ng-card-pad" style="padding:18px 36px;background:#FAF7F0;border-bottom:1px solid #EDEAE3;">
  {meta(score, dt, src, url)}
  {ttitle(ttl, url, acc)}
  <table role="presentation" class="ng-side-table" width="100%" cellpadding="0" cellspacing="0" border="0"><tbody><tr>
    <td class="ng-card-thumb" width="156" valign="top" style="padding-right:14px;">
      <a href="{url}" class="db tdn"><img class="ng-card-thumb-img" src="{thumb}" width="140" height="90" style="width:140px;height:90px;object-fit:cover;display:block;border:1px solid #E2DED4;" alt=""></a>
    </td>
    <td class="ng-card-body-cell" valign="top">
      {bul(bullets, acc)}
    </td>
  </tr></tbody></table>
  {tip_html}
</td></tr>"""

def cat_header(idx, total, glyph, name_en_up, name_jp, acc, n_stories, summary):
    return f"""
<tr><td class="ng-cat-pad" style="background:{acc};padding:20px 36px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tbody><tr>
    <td style="vertical-align:middle;">
      <div class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:10px;color:rgba(255,255,255,0.7);letter-spacing:2px;margin-bottom:4px;">
        CATEGORY {idx} / {total} &nbsp;&middot;&nbsp; {name_en_up}
      </div>
      <div style="font-size:28px;font-weight:800;color:#fff;letter-spacing:-0.5px;line-height:1.1;">
        <span style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;margin-right:10px;">{glyph}</span>{name_jp}
      </div>
    </td>
    <td align="right" style="vertical-align:middle;color:rgba(255,255,255,0.85);font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:11px;">{n_stories} stories</td>
  </tr></tbody></table>
  <div class="ng-cat-summary" style="font-size:13px;color:rgba(255,255,255,0.95);font-style:italic;line-height:1.6;margin-top:10px;padding-top:10px;border-top:1px solid rgba(255,255,255,0.25);">
    {summary}
  </div>
</td></tr>"""

def sec(num, tag, heading, body_html, acc):
    return f"""
<tr><td class="ng-section-pad" style="background:#FAF7F0;padding:0 36px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-bottom:1px dashed #E2DED4;"><tbody><tr>
    <td class="ng-section-num-cell" width="80" valign="top" style="padding:28px 16px 28px 0;">
      <div class="m ng-section-num" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:38px;font-weight:900;color:{acc};line-height:0.9;letter-spacing:-2px;">&sect;{num:02d}</div>
      <div class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:9px;color:#fff;background:{acc};padding:2px 6px;display:inline-block;letter-spacing:1.5px;margin-top:8px;">{tag}</div>
    </td>
    <td class="ng-section-text-cell" valign="top" style="padding:28px 0 28px 20px;border-left:1px solid #E2DED4;">
      <h3 class="ng-section-heading" style="font-size:18px;font-weight:800;margin:0 0 14px;">{heading}</h3>
      <div class="ng-section-body" style="font-size:13.5px;line-height:2.0;">{body_html}</div>
    </td>
  </tr></tbody></table>
</td></tr>"""

def takeaway(num, color, tag, text_html):
    return f"""
<tr><td style="padding-bottom:12px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#fff;border:1px solid #E2DED4;"><tbody><tr>
    <td width="56" valign="middle" class="m" style="background:{color};color:#fff;text-align:center;font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:18px;font-weight:900;padding:14px 0;">{num}</td>
    <td style="padding:12px 16px;">
      <div class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:9px;color:{color};font-weight:700;letter-spacing:1.5px;margin-bottom:4px;">{tag}</div>
      <div style="font-size:13px;line-height:1.7;font-weight:600;">{text_html}</div>
    </td>
  </tr></tbody></table>
</td></tr>"""

def related_row(date, url, title_txt):
    return f"""
<tr><td style="padding:10px 0;border-bottom:1px solid #E2DED4;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tbody><tr>
    <td width="100" class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:11px;color:#5C5A52;">{date}</td>
    <td style="font-size:13px;font-weight:600;"><a href="{url}" style="color:#1A1A1A;text-decoration:none;">{title_txt}</a></td>
    <td width="20" align="right" style="color:#5C5A52;">&rarr;</td>
  </tr></tbody></table>
</td></tr>"""

# ── TOC ──────────────────────────────────────────────────────────────────────
def toc_row(glyph, acc, name, n):
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:8px;"><tbody><tr>'
        f'<td width="32" class="m" style="font-family:\'JetBrains Mono\',Consolas,\'Courier New\',monospace;font-size:14px;color:{acc};font-weight:700;">{glyph}</td>'
        f'<td style="font-size:14px;font-weight:700;">{name}</td>'
        f'<td align="right" class="m" style="font-family:\'JetBrains Mono\',Consolas,\'Courier New\',monospace;font-size:11px;color:#5C5A52;">{n} stories</td>'
        f'</tr></tbody></table>'
    )

TOC = (
    toc_row("¥", FX_ACC, "為替 (Foreign Exchange)", 5) +
    toc_row("◆", AI_ACC, "AI (Artificial Intelligence)", 5) +
    toc_row("▲", IT_ACC, "IT-Consulting (IT &amp; Consulting)", 5) +
    toc_row("■", EC_ACC, "経済 (Economy)", 5) +
    toc_row("●", GM_ACC, "ゲーム (Gaming)", 5)
)

# ── NG PLACEHOLDER URLS ───────────────────────────────────────────────────────
PH_FX     = f"{CDN}/ng-thumb-fx.jpg"
PH_FX_C   = f"{CDN}/ng-thumb-common-fx.jpg"
PH_AI     = f"{CDN}/ng-thumb-ai.jpg"
PH_AI_C   = f"{CDN}/ng-thumb-common-ai.jpg"
PH_IT     = f"{CDN}/ng-thumb-it.jpg"
PH_IT_C   = f"{CDN}/ng-thumb-common-it.jpg"
PH_EC     = f"{CDN}/ng-thumb-economy.jpg"
PH_EC_C   = f"{CDN}/ng-thumb-common-economy.jpg"
PH_GM     = f"{CDN}/ng-thumb-game.jpg"
PH_GM_C   = f"{CDN}/ng-thumb-common-game.jpg"

# ── CATEGORIES HTML ───────────────────────────────────────────────────────────
CATS = ""

# ─ FX (1/5) ──────────────────────────────────────────────────────────────────
CATS += cat_header(1, 5, "¥", "FOREIGN EXCHANGE", "為替", FX_ACC, 5,
    "日本政府が推計345億ドルの為替介入を実施しドル円を160円台から155円台へ急落させた。しかし日米金利差という構造要因は変わらず、USD/JPYはゴールデンウィーク明けに向け157〜160円の攻防が続く。")

CATS += featured_card(90, "2026-05-01 07:30", "Bloomberg",
    "https://www.bloomberg.com/news/articles/2026-05-01/japan-likely-spent-34-5-billion-in-fx-intervention-to-boost-yen",
    "日本政府 ドル円160円台の防衛に345億ドル投入 — 史上最大規模の可能性",
    PH_FX,
    [
        "日本政府が5月1日に[[160円台]]を突破したドル円を防衛するため、推計[[345億ドル]]（約5.3兆円）を投じた為替介入を実施。__史上最大規模__ の可能性があり、円は155円台まで急騰した。",
        "財務省は公式介入を確認していないが、[[片山財務相]]と[[三村財務官]]が「最終警告」を発した直後に大規模な円買い圧力が観察され、市場参加者は追加介入を警戒している。",
        "トランプ政権との緊張も高まっており、為替操作国認定リスクが浮上。__短期的には155〜158円レンジ__ での神経質な展開が続く見通し。",
    ], FX_ACC)

CATS += side_card(88, "2026-05-01 08:00", "CNBC",
    "https://www.cnbc.com/2026/05/01/yen-steadies-after-japan-intervention-traders-brace-for-more-action.html",
    "円、介入後も157円付近で膠着 — 海外勢の円売り圧力は根強く",
    PH_FX_C,
    [
        "5月1日の介入後、[[USD/JPY]]は155円台から157円台に戻し、大型連休の薄商いの中で方向感を欠く展開。日本政府の追加介入を警戒しつつも、ドル買いポジションを維持する動きが続く。",
        "__米日金利差（約3%）__ が依然としてドル高・円安を支持しており、FRBの利下げ実施時期が後ずれするほど円売り圧力が持続するとみられる。",
    ], FX_ACC,
    related_tip("復状", "https://github.com/HIDEPON-UMG/News-Grasp/blob/main/digest/FX/2026-04-28-FX.md",
                "4/28号: 160円防衛ライン 財務省の口先介入"))

CATS += side_card(85, "2026-05-01 07:15", "CNBC",
    "https://www.cnbc.com/2026/05/01/japanese-fx-intervention-wipes-out-yens-iran-war-losses.html",
    "イラン戦争勃発が円安を加速 — 安全資産へのドル逃避が介入を誘発",
    PH_FX_C,
    [
        "イラン情勢の悪化によりドルに安全資産需要が集中し、[[USD/JPY]]は一時[[160.73円]]まで上昇。これが財務省の為替介入を誘発し、数時間で5円超の急落を引き起こした。",
        "エネルギー価格の高騰（原油WTI+12%）が日本の貿易収支を直撃。EUR/USDも1.1727とユーロ高継続。__地政学リスクの収束__ が円安局面を終息させる鍵を握る。",
    ], FX_ACC)

CATS += side_card(82, "2026-05-04 09:00", "TradingPedia",
    "https://www.tradingpedia.com/2026/05/04/usd-jpy-rebounds-after-drop-on-intervention-talk/",
    "USD/JPY 157台に反発 — 介入後の巻き戻しで短期ベアトレンドに修正",
    PH_FX_C,
    [
        "5月4日の取引で[[USD/JPY]]は157台まで反発。介入で形成された安値155.56円から短期巻き戻しが進み、日米金利差を材料視したドル買いが再燃した。",
        "テクニカル的には[[158.50円]]を上抜けすると介入前水準への回帰シナリオが現実味を帯び、__160円の攻防__ が再び注目される。",
    ], FX_ACC)

CATS += side_card(80, "2026-05-01 09:17", "外為どっとコム",
    "https://www.gaitame.com/media/entry/2026/05/01/091757",
    "ドル円急落！5月1日 為替介入らしき動き — 連続介入の可能性を分析",
    PH_FX_C,
    [
        "5月1日の東京時間、[[USD/JPY]]が160.73円から155.56円へ約[[5円急落]]。最大[[345億ドル]]投入の可能性があり、過去のパターンから追加介入が今週実施される可能性が高い。",
        "FRBの利下げ期待が後退する中、日銀は4月会合で利上げを据え置き。__日米金利差がドル高の根拠__ として機能し続けるため、介入の効果は一時的にとどまる見方が多い。",
    ], FX_ACC)

# ─ AI (2/5) ──────────────────────────────────────────────────────────────────
CATS += cat_header(2, 5, "◆", "ARTIFICIAL INTELLIGENCE", "AI", AI_ACC, 5,
    "米国防総省がOpenAI・Google等8社とAI契約を締結しAnthropicを排除。同日Anthropicは15億ドルのエンタープライズ合弁で反転攻勢に出た。AMD Q1決算本日発表でAI半導体の選別相場が本格化。")

CATS += featured_card(92, "2026-05-04 10:24", "gHacks Tech News",
    "https://www.ghacks.net/2026/05/04/pentagon-signs-ai-deals-with-openai-google-microsoft-nvidia-and-others-cutting-out-anthropic/",
    "米国防総省 OpenAI・Google・Microsoft・NVIDIA等8社とAI契約 — Anthropicを排除",
    "https://www.ghacks.net/wp-content/uploads/2026/05/gHacks-articles-2026-05-04T102344.230.png",
    [
        "米国防総省が[[OpenAI]]・[[Google]]・[[Microsoft]]・[[NVIDIA]]など8社とAI調達契約を締結。[[Anthropic]]は自律兵器・大規模監視への使用を含む「すべての合法的目的」条項を拒否したとして排除された。",
        "トランプ政権はAnthropicとの関係断絶を宣言したが、カリフォルニア連邦裁判所が排除阻止の仮処分を発令。ダリオ・アモデイCEOがホワイトハウスを訪問し__関係修復の糸口__ を探っている。",
        "国防AI市場をめぐる争奪戦は、モデル安全方針と軍事利用の境界をどこに引くかという__AIガバナンスの核心問題__ を提起。株式市場ではNVDA・MSFT・GOOGLが続伸した。",
    ], AI_ACC)

CATS += side_card(90, "2026-05-04 11:30", "CNBC",
    "https://www.cnbc.com/2026/05/04/anthropic-goldman-blackstone-ai-venture.html",
    "Anthropic、BlackstoneとGoldman Sachsと15億ドルのエンタープライズAI新会社を設立",
    PH_AI_C,
    [
        "[[Anthropic]]が[[Blackstone]]・Hellman &amp; Friedman・[[Goldman Sachs]]と合弁会社を設立。資本金[[15億ドル]]（各社3億ドル拠出）で、PE傘下の中堅企業にClaudeを導入するエージェント実装サービスを提供する。",
        "競合[[OpenAI]]も同日、TPGとBain Capitalと類似の合弁構造を発表。__モデルを持つAI企業 &times; 資本を持つPE__ という新業態が企業向けAI市場に勃興している。",
    ], AI_ACC,
    related_tip("復状", "https://github.com/HIDEPON-UMG/News-Grasp/blob/main/digest/AI/2026-04-28-AI.md",
                "4/28号: Google AnthropicへのGoogle 400億ドル投資"))

CATS += side_card(85, "2026-05-05 06:00", "Computing.net",
    "https://computing.net/news/stocks/amd-q1-2026-earnings-preview-analysts-forecast-33-revenue-jump-as-ai-momentum-builds/",
    "AMD 2026年Q1決算 本日発表 — AI加速でデータセンター売上33%増予想",
    PH_AI_C,
    [
        "[[AMD]]が本日5月5日にQ1 2026決算を発表予定。アナリスト予想は売上[[98.4億ドル]]（前年同期比+33%）、EPS 1.28ドル。Instinct MI350シリーズのデータセンター需要が牽引役。",
        "D.A. DavidsonがBuyへ格上げ（目標375ドル）する一方、__TSMCの製造キャパシティ制約__ がアップサイドを抑えるとHSBCが指摘。Buy/Hold評価が割れた状態で発表を迎える。",
    ], AI_ACC)

CATS += side_card(83, "2026-05-04 15:00", "24/7 Wall St.",
    "https://247wallst.com/investing/2026/05/04/amd-sinks-6-despite-a-holding-pattern-in-intel-and-nvidia-the-selective-ai-chip-trade-is-here/",
    "AMD 株価6%急落 — NVIDIA・Intelが横ばいの中で「選別AI相場」が鮮明に",
    PH_AI_C,
    [
        "5月4日、[[AMD]]株が6%安の340ドル付近まで急落する一方、[[NVIDIA]]と[[Intel]]は横ばい。AI半導体の「選別売買」が始まったと市場では解釈されている。",
        "NVIDIA時価総額は[[5.26兆ドル]]の史上最高値を維持。Blackwell/Vera Rubin次世代アーキで__2026〜2027年に1兆ドル売上__ という強気シナリオが依然現役だ。",
    ], AI_ACC)

CATS += side_card(80, "2026-05-04 12:00", "TechCrunch",
    "https://techcrunch.com/2026/05/04/anthropic-and-openai-are-both-launching-joint-ventures-for-enterprise-ai-services/",
    "AnthropicとOpenAIが同日エンタープライズAI合弁を発表 — コンサル業界に黒船",
    PH_AI_C,
    [
        "[[Anthropic]]と[[OpenAI]]が5月4日同日にPEファームと組んだエンタープライズAI合弁の設立を発表。中堅企業向けの「__AIエージェント全包括型サービス__」でコンサル業界に直接参入。",
        "この動きは従来型ITコンサル企業のビジネスモデルへの直接的挑戦として業界に衝撃を与えており、アクセンチュア株は4月後半から下落基調が続いている。",
    ], AI_ACC)

# ─ IT-Consulting (3/5) ───────────────────────────────────────────────────────
CATS += cat_header(3, 5, "▲", "IT &amp; CONSULTING", "IT-Consulting", IT_ACC, 5,
    "AnthropicとOpenAIのPE直販JVがコンサル業界を揺さぶり、アクセンチュア株が4月以降40%超下落。NTTデータ等日本勢はAIスペース構想で対抗。BCG調査ではAI投資が倍増しエージェントシフトが加速している。")

CATS += featured_card(85, "2026-05-04 11:00", "Fortune",
    "https://fortune.com/2026/05/04/anthropic-claude-consulting-industry-joint-venture-blackstone-goldman-sachs/",
    "Anthropic、コンサル業界に宣戦布告 — Wall Street大手とのJVでAIエージェント導入を直販",
    PH_IT,
    [
        "Anthropicが[[Blackstone]]・Goldman Sachsと設立した[[15億ドル]]のAI合弁は「コンサル業界へのショット」と評されている。中堅企業にClaudeを使ったワークフロー再設計をエンジニア派遣で直提供し、[[アクセンチュア]]等の仲介役を飛ばす。",
        "アクセンチュア株（ACN）は4月以降に40%超下落しており、AI時代の収益モデルが旧来のアドバイザリー型から「__ツール構築・導入・保守__」へ移行していることを市場が先取りした格好だ。",
        "マッキンゼーはAI関連案件が業務の40%を占め自社ツール「Lilli」を全社展開しているが、モデルベンダーが直販に乗り出したことで__既存顧客の囲い込み合戦__ が激化する。",
    ], IT_ACC)

CATS += side_card(82, "2026-04-15 10:00", "NTT DATA",
    "https://www.nttdata.com/global/ja/news/topics/2026/041502/",
    "NTTデータ・ソフトバンク等8団体「AIスペース構想」でxIPFコンソーシアム設立",
    PH_IT_C,
    [
        "NTTデータ・富士通・ソフトバンクなど産学8団体が「[[AIスペース]]」構想実現を目指すxIPFコンソーシアムを2026年4月10日に設立。企業の壁を越えてAIとデータを共有する基盤の構築を目指す。",
        "[[NTTデータ]]は「[[AIネーティブ開発]]」を2026年度中に全社展開し、2027年度にはAI適用案件比率50%・開発生産性40%向上を目標とする。__IT人材不足への抜本的対策__ として位置づけている。",
    ], IT_ACC)

CATS += side_card(80, "2026-04-28 09:00", "Business Insider Japan",
    "https://www.businessinsider.jp/article/2604-consulting-mckinsey-accenture-bcg-ai-silicon-valley-enterprise-partnerships/",
    "外資コンサルとシリコンバレーの「蜜月関係」 — AI契約のエコシステムが急成長",
    PH_IT_C,
    [
        "元コンサルタント従業員の証言によると、[[マッキンゼー]]・[[アクセンチュア]]・BCGはOpenAI/MicrosoftのAIツールを優先採用する「__パートナー割引__」を受けている。",
        "マッキンゼーのAI関連案件は[[全業務の40%]]を占め急拡大。従来の「人材貸し出し」モデルから「AIツール＋エンジニア実装」へ移行が加速している。",
    ], IT_ACC)

CATS += side_card(78, "2026-05-01 08:00", "BCG / PRtimes",
    "https://prtimes.jp/main/html/rd/p/000000035.000145445.html",
    "BCG調査: 2026年企業のAI投資が倍増 — 30%以上をAIエージェントに充当",
    PH_IT_C,
    [
        "BCGが世界16市場CEO 2,360人を調査。[[2026年のAI投資]]は売上高比1.7%に達し[[2024年比で倍増]]する見込み。",
        "AI投資の[[30%超をAIエージェント]]に充てる計画が示され、単なるパイロット段階から「__全業務フローの再設計__」フェーズへ移行した。",
    ], IT_ACC)

CATS += side_card(75, "2026-02-28 09:30", "Business Insider Japan",
    "https://www.businessinsider.jp/article/2602-mckinsey-bcg-pwc-ey-ai-agents-adoption-value-consulting-industry/",
    "コンサル4大社がAIエージェントの価値計測を本格開始 — 測定指標の標準化が課題",
    PH_IT_C,
    [
        "[[マッキンゼー]]・BCG・PwC・EYの4社がAIエージェントROI計算モデルの構築を開始。マッキンゼーは法務・会計業務の[[70%を自動化]]できると試算。",
        "PwCはクライアント企業のAIエージェント導入支援で2026年度に__売上10億ドル超__を目標とし、EYも専任チームを新設。コンサル業界がエージェントを前提に再構築している。",
    ], IT_ACC)

# ─ Economy (4/5) ─────────────────────────────────────────────────────────────
CATS += cat_header(4, 5, "■", "ECONOMY", "経済", EC_ACC, 5,
    "S&amp;P500が7,209で最高値更新。好決算・イラン和平期待・原油安の三重奏が支えるが決算終盤で「好材料出尽くし」懸念も台頭。米3月CPI 3.3%、日銀は0.75%据え置きながらCPI見通しを2.8%へ大幅上方修正。")

CATS += featured_card(88, "2026-05-01 09:00", "OANDA Japan",
    "https://www.oanda.jp/lab-education/market_news/2026_05_01_us500/",
    "S&amp;P500が7,209で最高値更新 — 好決算・イラン和平進展・原油安が三重奏",
    PH_EC,
    [
        "4月30日のS&amp;P500は[[7,209.01]]（+1.02%）で終値ベースの史上最高値を更新。イランの和平提案・[[Appleの好決算]]（市場予想を83%の企業が超過）・原油価格下落が同時に株高を後押しした。",
        "ただし決算発表が終盤に差し掛かり、S&amp;P500が最高値水準にある中では「__好材料出尽くし__」のリスクが意識されており、5月の相場展開は売り方が主導権を握り始める可能性がある。",
        "「[[Sell in May]]」格言通りの展開を警戒するアナリストも増えている。金利上昇局面でのバリュエーション修正は避けられず、ナスダック指数の高PERが特に懸念される。",
    ], EC_ACC)

CATS += side_card(85, "2026-05-04 10:00", "財経新聞",
    "https://www.zaikei.co.jp/article/20260504/852352.html",
    "相場展望5月5日号 — 米ハイテク株の二極化が鮮明、決算後の失速に警戒",
    PH_EC_C,
    [
        "S&amp;P500・ナスダックは[[最高値更新]]を継続しているが、上昇を牽引したハイテク株内部で[[二極化]]が目立ち始めた。AI半導体の勝者と敗者の選別が進んでいる。",
        "日本株は[[ゴールデンウィーク（5月3〜6日）]]に東証が休場。__5月7日の寄り付き__ が海外動向の全取り込みポイントとして注目される。",
    ], EC_ACC)

CATS += side_card(82, "2026-05-01 08:00", "野村證券",
    "https://www.nomura.co.jp/wealthstyle/article/0714/",
    "野村證券がS&amp;P500年末目標を7,500に引き上げ — AI需要とイラン情勢収束を想定",
    PH_EC_C,
    [
        "野村證券ストラテジストが[[S&amp;P500の2026年末目標]]を7,500に引き上げ（従来7,200）。AI需要の拡大持続とイラン情勢の外交的収束を前提とし、2027年末は[[7,900]]を見込む。",
        "米国企業業績は「__AI投資を原動力としたマルチプル拡大__」フェーズに入っており、決算発表314社のうち83%が利益予想を上回った好実績が評価を裏付ける。",
    ], EC_ACC)

CATS += side_card(80, "2026-04-10 09:30", "Trading Economics / BLS",
    "https://tradingeconomics.com/united-states/inflation-cpi",
    "米3月CPI +3.3%、2024年5月以来の最高水準 — イラン戦争でガソリン+18.9%",
    PH_EC_C,
    [
        "米3月CPIは前年同月比[[+3.3%]]と2024年5月以来の最高水準。エネルギー価格が+12.5%（ガソリン[[+18.9%]]・燃料油+44.2%）に急騰し、イラン戦争による供給不安を反映した。",
        "コアCPIは[[+2.6%]]で制御可能な水準。__4月CPI は5月12日発表予定__ で、和平進展による原油安効果が注目される。",
    ], EC_ACC)

CATS += side_card(78, "2026-04-28 16:00", "日本経済新聞",
    "https://www.nikkei.com/article/DGXZQOFL164QYTW6A110C2000000/",
    "日銀4月会合 政策金利0.75%据え置き — CPI見通し+2.8%へ大幅上方修正",
    PH_EC_C,
    [
        "日銀は4月会合で[[0.75%]]据え置きを6対3で決定。[[植田和男]]総裁は6月会合での利上げを示唆する発言を避けた。",
        "展望レポートでCPI見通しを[[+1.9%]]から[[+2.8%]]へ大幅引き上げ。エネルギー高騰と円安を反映し、__7月会合での0.25%利上げ__ が有力視されている。",
    ], EC_ACC,
    related_tip("復状", "https://github.com/HIDEPON-UMG/News-Grasp/blob/main/digest/FX/2026-04-28-FX.md",
                "4/28号: BOJ・FOMC同週開催 USD/JPY 159.42"))

# ─ Game (5/5) ────────────────────────────────────────────────────────────────
CATS += cat_header(5, 5, "●", "GAMING", "ゲーム", GM_ACC, 5,
    "Nintendo Switch 2の「元年本番」が5月に幕を開け、6本の大作が続々リリース。カプコンはモンハンワイルズ1,100万本で9期連続最高益。ウマ娘は世界累計3,790億円突破と日本ゲーム産業の地力を示した。")

CATS += featured_card(85, "2026-05-04 09:00", "Game Rant",
    "https://gamerant.com/nintendo-switch-2-new-games-coming-out-soon-list-may-2026/",
    "Nintendo Switch 2、5月に6本の大作リリース — IndyやYoshiでラインナップ充実",
    "https://static0.gamerantimages.com/wordpress/wp-content/uploads/2026/04/switch-2-games-releasing-may-2026.jpg",
    [
        "Nintendo Switch 2向けに5月だけで[[6本]]の注目タイトルがリリース予定。5月12日に[[Indiana Jones and the Great Circle]]、5月21日に任天堂新作[[ヨッシーとフカシギの図鑑]]、5月22日にはTales of Ariseが続く。",
        "__ヨッシーとフカシギの図鑑__ は『ヨッシーのクラフトワールド（2019）』以来7年ぶりの正統続編であり、Switch 2最初の「真の独占大作」として期待値が高い。",
        "2026年後半には『スプラトゥーン レイダース』『[[リズム天国 ミラクルスターズ]]』（11年ぶり）など大型IPが控えており、[[Switch 2元年の本番]]が始まった。",
    ], GM_ACC)

CATS += side_card(83, "2026-05-13 16:00", "日本経済新聞",
    "https://www.nikkei.com/article/DGXZQOUF12ABB0S5A510C2000000/",
    "カプコン 26年3月期決算: モンハンワイルズ累計1,100万本で9期連続最高益達成",
    "https://article-image-ix.nikkei.com/https%3A%2F%2Fimgix-proxy.n8s.jp%2FDSXZQO6379927012052025000000-1.png?ixlib=js-3.8.0&w=638&h=359&auto=format%2Ccompress&fit=crop&bg=FFFFFF&s=bb4f6a06d57bd4dac6f85fb6c7c23cd3",
    [
        "カプコンが[[26年3月期決算]]を発表。[[モンスターハンターワイルズ]]の累計販売本数が[[1,100万本]]を突破し、売上高・営業利益ともに過去最高の[[9期連続最高益]]を達成した。",
        "Switch 2向けポートが各人気シリーズの販売本数を押し上げており、バイオハザード・ストリートファイター6のリピート需要も[[継続的に増加]]しているという。",
    ], GM_ACC)

CATS += side_card(80, "2026-05-02 12:00", "ファミ通.com",
    "https://www.famitsu.com/article/202605/72453",
    "2026年5月〜9月 Switch/Switch 2新作26選 — スプラトゥーンやリズム天国など期待作",
    "https://cimg.kgl-systems.io/camion/files/72453/thumbnail_eVqp.jpg?x=1280",
    [
        "ファミ通が5月〜9月発売予定の注目作[[26本]]を紹介。任天堂の[[スプラトゥーン レイダース]]（新機軸タワーオフェンス型）、[[リズム天国 ミラクルスターズ]]（11年ぶり完全新作）が特に注目度が高い。",
        "Switch 2の特性を活かした新機軸タイトルが相次ぎ、__新体験型コンテンツ__の充実がハード普及を後押しする見込み。",
    ], GM_ACC)

CATS += side_card(78, "2026-05-01 10:00", "ファミ通.com",
    "https://www.famitsu.com/article/202405/4097",
    "ウマ娘 世界累計収益3,790億円突破 — 若い競馬ファン開拓でランキング上位を安定維持",
    "https://cimg.kgl-systems.io/camion/files/4097/thumbnail_3ARbs1g.jpg?x=1280",
    [
        "[[ウマ娘 プリティーダービー]]の世界累計収益が[[約3,790億円]]（約24億ドル）を突破。リアル競馬ファンの若年層へのリーチ戦略が奏功し、セルランキングの上位を安定的に維持している。",
        "Cygamesのゲームエンジンと[[IP管理の精緻さ]]が長期的な収益基盤を形成。__既存コンテンツとのクロスメディア展開__ が同ジャンルの成功モデルとして機能している。",
    ], GM_ACC)

CATS += side_card(75, "2026-05-04 09:00", "Game Rant",
    "https://gamerant.com/upcoming-nintendo-switch-2-games-2026/",
    "Indiana Jones and the Great Circle、Switch 2版5月12日配信 — PC/Xbox版の完全移植",
    PH_GM_C,
    [
        "MicrosoftのBethesda開発[[Indiana Jones and the Great Circle]]がSwitch 2向けに5月12日に配信。Switch 2のマウスモードを活用したFPS体験が可能になる。",
        "XboxタイトルのSwitch 2移植は今後も続く予定で、__任天堂プラットフォームへの外部大作流入__ が加速。Nintendo Switch Online拡充への期待も高まる。",
    ], GM_ACC)

# ── REFLECTION ───────────────────────────────────────────────────────────────
REFLECTION_TITLE = "為替防衛とAI覇権の交差点"
REFLECTION_SUB   = "イランの火種が市場を揺さぶる中、AIは国防・企業・コンサルを同時に再編する"

LEAD_HTML = hl(
    "本日5分野・25本のニュースから浮かび上がる最大のテーマは [[地政学リスク]] と [[AI産業再編]] の同時進行である。"
    "為替介入345億ドルとAnthropicの合弁設立という一見無関係な二つのニュースは、"
    "どちらも「旧来の秩序が臨界点に達した」という同じ信号を発している。以下、各カテゴリを横断して読み解く。",
    "#C9B98A")

PULL_QUOTE_HTML = hl(
    "「アドバイスを売る」から「エージェントを埋め込む」へ——"
    "__コンサルティングの本質__ が、シリコンバレーの黒船によって根底から問い直された日。",
    "#8E2A19")

SECTIONS_HTML = (
    sec(1, "総論", "地政学とAIが同時に構造を動かす日",
        hl("イランを震源とする地政学リスクが[[原油価格]]を押し上げ、米国のインフレを再燃させた。その結果、[[FRBの利下げ期待]]が後退し、ドル高・円安が進行、日本の為替介入を誘発した。これは単なる「円安対策」ではなく、__地政学リスクが金融政策を縛るという構造変化__ の象徴だ。同時に、AIをめぐる国防総省とAnthropicの攻防は、技術の倫理的制約と安全保障のトレードオフという普遍的問題を軍事調達という具体的な文脈で顕在化させた。", "#1A1A1A"),
        "#1A1A1A") +
    sec(2, "為替・経済", "イラン戦争が生み出した「介入の必要条件」",
        hl("5月1日の[[345億ドル介入]]は、日本政府がイラン戦争という外生ショックによるドル高に対して「ハードライン160円」を引いたことを示す。だが日米金利差という__構造的なドル高要因__ は変わらず、介入は時間稼ぎに過ぎない。野村がS&amp;P500目標を7,500に引き上げたのも、イラン和平進展によるインフレ収束シナリオが前提であり、[[5月12日の4月CPI発表]]が今後の相場の方向を決定する分岐点になる。", FX_ACC),
        FX_ACC) +
    sec(3, "AI・技術", "国防排除と企業直販、Anthropicの二正面戦略",
        hl("国防総省から排除されたAnthropicが同日に[[Blackstone・Goldman Sachsとの合弁]]を発表したのは偶然ではない。軍事利用を断ってもエンタープライズ市場で収益を確保できるという意思表示だ。一方、OpenAIは軍事契約を受け入れつつ同様の合弁を組み、__両社の価値観の分岐__ が産業構造上の差別化要因になりつつある。AMD決算の行方は、NVIDIA独占がいつ崩れるかというAI半導体市場の根本問題への答えを提供するだろう。", AI_ACC),
        AI_ACC) +
    sec(4, "産業・業界", "コンサル・SIer・ゲームが同時に曲がり角へ",
        hl("アクセンチュア株が40%超下落したという事実は、[[コンサル業界の構造変化]]への市場の評価を端的に示す。NTTデータのxIPFコンソーシアムは日本版「AIスペース」への対応だが、Anthropic・OpenAIの直販モデルに対抗できる付加価値を確立できるかが問われる。ゲーム業界では[[カプコンの9期連続最高益]]が「IP資産型経営」の強さを証明し、Switch 2のラインナップ充実が市場全体を底上げしている。__業界構造の変化スピードが、各社の適応力の差を拡大させている。__", IT_ACC),
        IT_ACC) +
    sec(5, "明日へ", "3つの注目タイミング",
        hl("今後1週間で注目すべきは三点。第一に、[[AMD Q1決算]]（本日発表）の結果がAI半導体選別相場の行方を決める。第二に、5月12日の[[米4月CPI発表]]がイラン和平効果を測る試金石となり、FRBの利下げ時期見通しを更新する。第三に、Switch 2向け[[Indiana Jones and the Great Circle]]（5月12日）の初週セルスルーが、Switch 2のゲームソフト市場規模の実力を示す最初の大型指標になる。三つとも、今日浮かび上がったテーマの「続き」として読むべき数字だ。", "#C9B98A"),
        "#C9B98A")
)

TAKEAWAYS_HTML = (
    takeaway("01", FX_ACC, "為替",
        hl("日本政府の[[345億ドル介入]]は日米金利差という構造要因を変えられず、157〜160円の綱引きが続く。__5月12日の米4月CPI発表__ が介入の「次の判断基準」になる", FX_ACC)) +
    takeaway("02", AI_ACC, "AI",
        hl("[[AnthropicとOpenAI]]のPE直販JVがコンサルを脅かし、AIガバナンスの国防論争が先鋭化。__AMD決算本日発表__ で半導体選別相場の方向が決まる", AI_ACC)) +
    takeaway("03", GM_ACC, "産業",
        hl("Switch 2はカプコン・任天堂の強力IPで「[[元年の本番]]」を迎え、ゲーム市場に活況。コンサル・SIerは直販AIに対抗するモデルの__再構築を迫られている__", GM_ACC))
)

RELATED_ISSUES_HTML = (
    related_row("2026-04-28",
        "https://github.com/HIDEPON-UMG/News-Grasp/blob/main/digest/Summary/2026-04-28.md",
        "BOJ・FOMC同週開催 USD/JPY 159.42 — 利上げ観測と介入ライン攻防") +
    related_row("2026-05-01",
        "https://github.com/HIDEPON-UMG/News-Grasp/blob/main/digest/Summary/2026-05-01.md",
        "Sell in May の幕開け、介入後ドル円と米国株最高値の競合") +
    related_row("2026-05-04",
        "https://github.com/HIDEPON-UMG/News-Grasp/blob/main/digest/Summary/2026-05-04.md",
        "Pentagon AI入札落選のAnthropicと企業向け反転攻勢")
)

# ── FINAL SUBSTITUTION ────────────────────────────────────────────────────────
html = TMPL
html = html.replace("{{ISSUE_NO}}",           "20260505")
html = html.replace("{{ISSUE_DATE}}",         "2026-05-05")
html = html.replace("{{ISSUE_WEEKDAY}}",      "火")
html = html.replace("{{TOTAL_CATEGORIES}}",   "5")
html = html.replace("{{TOTAL_STORIES}}",      "25")
html = html.replace("{{TOTAL_SECTIONS}}",     "5")
html = html.replace("{{TOC_ROWS_HTML}}",      TOC)
html = html.replace("{{CATEGORIES_HTML}}",    CATS)
html = html.replace("{{REFLECTION_TITLE}}",   REFLECTION_TITLE)
html = html.replace("{{REFLECTION_SUBTITLE}}", REFLECTION_SUB)
html = html.replace("{{REFLECTION_LEAD_HTML}}", LEAD_HTML)
html = html.replace("{{REFLECTION_PULL_QUOTE_HTML}}", PULL_QUOTE_HTML)
html = html.replace("{{REFLECTION_SECTIONS_HTML}}", SECTIONS_HTML)
html = html.replace("{{TAKEAWAYS_HTML}}",     TAKEAWAYS_HTML)
html = html.replace("{{RELATED_ISSUES_HTML}}", RELATED_ISSUES_HTML)

OUT.write_text(html, encoding="utf-8")
print(f"Written {len(html):,} bytes → {OUT}")
