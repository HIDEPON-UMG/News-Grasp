"""2026-05-04 HTML email generator"""
import pathlib, re

TEMPLATE = pathlib.Path("C:/Users/hidek/Obsidian/New's Grasp/News-Grasp/prompts/email-template.html").read_text(encoding='utf-8')
OUT = pathlib.Path("C:/Users/hidek/Obsidian/New's Grasp/News-Grasp/build/email.html")

def hl(text):
    """Convert [[word]] to bold highlight and __word__ to underline"""
    text = re.sub(r'\[\[(.+?)\]\]',
        r'<strong style="background:#F5E9C8;color:#1A1A1A;padding:0 2px;">\1</strong>', text)
    text = re.sub(r'__(.+?)__',
        r'<span style="border-bottom:2px solid #1A1A1A;">\1</span>', text)
    return text

# ── TOC ──────────────────────────────────────────────────────────────────────
TOC_ROWS = """
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:6px;"><tbody><tr>
  <td width="32" class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:12px;color:#B8860B;font-weight:700;">¥</td>
  <td style="font-size:14px;font-weight:700;">為替 <span style="font-weight:400;color:#5C5A52;">Foreign Exchange</span></td>
  <td align="right" class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:11px;color:#5C5A52;">5 stories</td>
</tr></tbody></table>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:6px;"><tbody><tr>
  <td width="32" class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:12px;color:#2D5BB8;font-weight:700;">◆</td>
  <td style="font-size:14px;font-weight:700;">AI <span style="font-weight:400;color:#5C5A52;">Artificial Intelligence</span></td>
  <td align="right" class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:11px;color:#5C5A52;">5 stories</td>
</tr></tbody></table>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:6px;"><tbody><tr>
  <td width="32" class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:12px;color:#2E6B52;font-weight:700;">▲</td>
  <td style="font-size:14px;font-weight:700;">IT-Consulting <span style="font-weight:400;color:#5C5A52;">IT &amp; Consulting</span></td>
  <td align="right" class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:11px;color:#5C5A52;">5 stories</td>
</tr></tbody></table>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:6px;"><tbody><tr>
  <td width="32" class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:12px;color:#8E2A19;font-weight:700;">■</td>
  <td style="font-size:14px;font-weight:700;">経済 <span style="font-weight:400;color:#5C5A52;">Economy</span></td>
  <td align="right" class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:11px;color:#5C5A52;">5 stories</td>
</tr></tbody></table>
"""

# ── カテゴリ+記事 ─────────────────────────────────────────────────────────────
def card_featured(idx_str, accent, time_str, source, score, title, url, thumb, bullets, related_html=""):
    b_html = "".join(f'<div class="bul ng-card-body" style="color:{accent}"><span class="dk">{hl(b)}</span></div>' for b in bullets)
    return f"""
<tr><td class="ng-card-pad pcard bgcard" style="padding:24px 36px;background:#FAF7F0;">
  <div class="ng-card-meta m mut fz10 ls05 mb6" style="margin-bottom:8px;">
    <span class="b7 w" style="background:{accent};color:#fff;padding:2px 8px;font-size:12px;letter-spacing:1px;">TOP</span>
    <span class="pl8 mut">{time_str} · {source} · SCORE {score}</span>
  </div>
  <h3 class="ng-card-title b8 lh145 t812 lsm03" style="font-size:22px;margin:8px 0 14px;">
    <a href="{url}" class="dk tdn" style="color:#1A1A1A;text-decoration:none;">{title}</a>
  </h3>
  <div class="ng-feature-img db" style="margin-bottom:16px;">
    <a href="{url}" class="db tdn"><img src="{thumb}" width="568" style="width:100%;max-height:220px;object-fit:cover;border:1px solid #E2DED4;display:block;" alt=""></a>
  </div>
  {b_html}
  {related_html}
</td></tr>"""

def card_side(idx_num, accent, time_str, source, score, title, url, thumb, bullets, related_html=""):
    b_html = "".join(f'<div class="bul ng-card-body" style="color:{accent}"><span class="dk">{hl(b)}</span></div>' for b in bullets)
    return f"""
<tr><td class="ng-card-pad bgcard bbcard pcard" style="padding:24px 36px;background:#FAF7F0;border-bottom:1px solid #EDEAE3;">
  <div class="ng-card-meta m mut fz10 ls05 mb6" style="margin-bottom:6px;">
    <span class="b7 w" style="background:{accent};color:#fff;padding:2px 6px;font-size:12px;">{idx_num:02d}</span>
    <span class="pl8">{time_str} · {source} · SCORE {score}</span>
  </div>
  <h3 class="ng-card-title b8 lh145 t812 lsm03" style="font-size:20px;margin:8px 0 12px;">
    <a href="{url}" class="dk tdn" style="color:#1A1A1A;text-decoration:none;">{title}</a>
  </h3>
  <table width="100%" class="ng-side-table"><tr>
    <td class="ng-card-thumb thb pr16 vtop" width="140" style="vertical-align:top;padding-right:16px;">
      <a href="{url}" class="db tdn"><img src="{thumb}" width="140" height="90" class="ng-card-thumb-img db ofc brd" style="width:140px;height:90px;object-fit:cover;border:1px solid #E2DED4;display:block;" alt=""></a>
    </td>
    <td class="ng-card-body-cell vtop" style="vertical-align:top;">
      {b_html}
      {related_html}
    </td>
  </tr></table>
</td></tr>"""

def cat_header(index, total, accent, glyph, name_jp, name_en, count, summary):
    return f"""
<tr><td class="ng-cat-pad" style="background:{accent};padding:20px 36px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tbody><tr>
    <td style="vertical-align:middle;">
      <div class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:10px;color:rgba(255,255,255,0.7);letter-spacing:2px;margin-bottom:4px;">
        CATEGORY {index} / {total} · {name_en.upper()}
      </div>
      <div class="ng-cat-name" style="font-size:28px;font-weight:800;color:#fff;letter-spacing:-0.5px;line-height:1.1;">
        <span style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;margin-right:10px;">{glyph}</span>{name_jp}
      </div>
    </td>
    <td align="right" style="vertical-align:middle;color:rgba(255,255,255,0.85);font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:11px;">{count} stories</td>
  </tr></tbody></table>
  <div class="ng-cat-summary" style="font-size:13px;color:rgba(255,255,255,0.95);font-style:italic;line-height:1.6;margin-top:10px;padding-top:10px;border-top:1px solid rgba(255,255,255,0.25);">
    {summary}
  </div>
</td></tr>"""

CDN = "https://raw.githubusercontent.com/HIDEPON-UMG/news-grasp-assets/main"

# ── FX ───────────────────────────────────────────────────────────────────────
CAT_FX = cat_header(1, 4, "#B8860B", "¥", "為替", "Foreign Exchange", 5,
    "4月30日の5〜6兆円介入でドル円は155円台に急騰したが現在156円台で膠着。FRBは金利据え置きを決定しパウエル議長が理事続投を表明、日銀も0.75%維持で3委員が利上げ支持と内部亀裂が露呈。NRIは介入を「時間稼ぎ」と断じた。")

FX1 = card_featured("TOP","#B8860B","02:30","Bloomberg Markets",92,
    "FRB・4月FOMC金利据え置き決定——パウエル議長「理事として残る」表明、利下げ路線に亀裂",
    "https://www.bloomberg.com/jp/news/articles/2026-04-29/TE9OUOKK3NY800",
    f"{CDN}/ng-thumb-fx.jpg",
    ["4月29日のFOMCでFF金利の据え置きを全会一致で決定、[[インフレ再加速リスク]]と関税影響を見極める方針を強調した",
     "__パウエル議長は任期終了後も理事として残る意向を表明__し、FRBの「法的攻撃」への警戒感を訴えた",
     "FRB内は利下げ派と据え置き派に分裂し、2026年内利下げ見通しは中央値1回と年初の3回予測から大幅後退"])

FX2 = card_side(2,"#B8860B","08:00","時事通信",90,
    "政府・日銀、4月30日夜に5〜6兆円規模の円買い介入——GW薄商いを突き一時155円台へ急騰",
    "https://www.jiji.com/jc/article?k=2026050100362&g=eco",
    f"{CDN}/ng-thumb-common-fx.jpg",
    ["[[政府・日銀]]が4月30日夜に円買い・ドル売り介入を実施、規模は市場推計で5〜6兆円と4年ぶりの大規模水準",
     "直前に片山財務相が「断固たる措置が近い」と警告、GW中で流動性の薄い海外市場を狙い撃ちした",
     "__ドル円は160円台後半から155円台半ばへ5円超急騰__したが、日米金利差が残存するため効果の持続性に疑問"])

FX3 = card_side(3,"#B8860B","15:00","日本経済新聞",83,
    "日銀4月会合：0.75%据え置き・3委員が利上げ支持票——政策委員会に異例の亀裂",
    "https://www.nikkei.com/article/DGXZQOGN293F10Z20C26A4000000/",
    f"{CDN}/ng-thumb-common-fx.jpg",
    ["日銀は4月会合で[[政策金利0.75%]]の据え置きを決定したが、9人の政策委員のうち3人が利上げを支持する異例の分裂",
     "植田総裁は「賃金・物価が見通しに沿って推移」と発言しつつも、__関税の影響を見極めるまで次の一手は保留__",
     "市場は次回6月会合での利上げ確率を40%と評価し、日米金利差縮小シナリオを手前引きしている"])

FX4 = card_side(4,"#B8860B","10:00","野村総合研究所",76,
    "NRI木内登英：為替介入は「時間稼ぎ」——金利差が縮まなければ円安は再燃必至",
    "https://www.nri.com/jp/media/column/kiuchi/20260501.html",
    f"{CDN}/ng-thumb-common-fx.jpg",
    ["NRI木内論説員は[[為替介入は時間稼ぎの政策]]と断言、日米金利差4%超が残る限り投機的ドル買い圧力は繰り返す",
     "1ドル160円という市場の「反介入ライン」が定着しつつあり、__介入の閾値が問われ始めている__という構造変化を指摘",
     "持続的な円高には日銀の追加利上げかFRBの利下げが不可欠と結論付け、政策の非対称性を問題視"])

FX5 = card_side(5,"#B8860B","09:00","OANDA Japan",70,
    "ドル円GW期間156円台半ばで膠着——追加介入警戒でヘッジファンドが慎重スタンス",
    "https://www.oanda.jp/lab-education/market_news/2026_05_01_usdjpy/",
    f"{CDN}/ng-thumb-common-fx.jpg",
    ["介入後のドル円は[[156円台半ば]]で膠着し、GWの薄商いの中で追加介入リスクが投機的ポジションを抑制している",
     "EURJPY・GBPJPYも連動して円高方向を維持、欧州勢が深夜に持ち高調整を本格化させる可能性がある",
     "__次のトリガーは5月8日発表の米4月雇用統計__、予想比上振れなら156円台後半〜157円再試験のシナリオが浮上"])

# ── AI ───────────────────────────────────────────────────────────────────────
CAT_AI = cat_header(2, 4, "#2D5BB8", "◆", "AI", "Artificial Intelligence", 5,
    "OpenAIがGPT-5.5で14ベンチマーク首位を奪還し、AnthropicはGPT-5.5に「Claude Mythos」で対抗しつつ評価額$900Bの資金調達交渉を開始。Googleは第8世代TPUを外販展開してNVIDIA依存の脱却を加速。AlphabetのQ1純利益81%増がAI投資回収フェーズへの移行を証明した。")

AI1 = card_featured("TOP","#2D5BB8","17:00","OpenAI / VentureBeat",95,
    "OpenAI、GPT-5.5を正式リリース——14ベンチマーク世界首位奪還、ProプランではClaude Opus 4.7を突き放す",
    "https://venturebeat.com/ai/openais-gpt-5-5-is-here-and-its-no-potato-narrowly-beats-anthropics-claude-mythos-preview-on-terminal-bench-2-0",
    f"{CDN}/ng-thumb-ai.jpg",
    ["OpenAIが[[GPT-5.5]]を4月23日にリリース、Terminal-Bench 2.0含む14ベンチマークでClaudeおよびGeminiを上回り世界首位を奪還",
     "コーディング・データ分析・複数ツール横断タスクに特化した設計で、Pro版は法務・データサイエンス向けに高精度モードを搭載",
     "__GPT-5.4リリースからわずか6週間での次世代投入__は、AIモデル競争のリリースサイクルが超高速化していることを示す"])

AI2 = card_side(2,"#2D5BB8","09:00","CNBC",92,
    "Anthropic、評価額9,000億ドル（約135兆円）での資金調達交渉開始——OpenAIの評価額を超え業界初の$900B企業へ",
    "https://www.cnbc.com/2026/04/29/anthropic-weighs-raising-funds-at-900b-valuation-topping-openai.html",
    f"{CDN}/ng-thumb-common-ai.jpg",
    ["Anthropicが投資家に対し評価額[[9,000億ドル（$900B）]]での資金調達を検討し、年間収益$30B突破でOpenAIの評価額を初めて上回る",
     "GoogleのTPU gigawatt級供給・AmazonのAWS計算資源確保など、__インフラ面での「陣営形成」が評価額を押し上げ__る独自構造",
     "Claudeの月間アクティブユーザーは1,800〜3,000万人と公開モデルでは最少だが、企業API収益が急拡大し単価優位を証明"])

AI3 = card_side(3,"#2D5BB8","10:00","TechCrunch",88,
    "Google、第8世代TPUを発表——訓練3倍高速・NVIDIAへの依存脱却加速、外販も開始",
    "https://techcrunch.com/2026/04/22/google-cloud-next-new-tpu-ai-chips-compete-with-nvidia/",
    f"{CDN}/ng-thumb-common-ai.jpg",
    ["Googleが[[Cloud Next 2026]]でTPU第8世代（訓練用8t・推論用8i）を発表、モデル訓練速度3倍・コスト効率80%改善を謳う",
     "従来の内部利用から外販モデルに転換し、100万TPUクラスタを単一インフラで構成できる拡張性をアピール",
     "__NVIDIAはデータセンターGPU市場の92%を占有するが__、GoogleのTPU外販とAMDのMI400展開が2026年後半の市場構造を変える可能性"])

AI4 = card_side(4,"#2D5BB8","06:00","CNBC",85,
    "Alphabet Q1 2026: 売上$109.9B・純利益81%増——Google Cloud+63%、AI投資「回収フェーズ」へ移行を証明",
    "https://www.cnbc.com/2026/04/29/alphabet-googl-q1-2026-earnings.html",
    f"{CDN}/ng-thumb-common-ai.jpg",
    ["AlphabetがQ1 2026の売上[[$109.9B]]（コンセンサス比$3B上振れ）、純利益は81%増の$62.58B（EPS $5.11）を達成",
     "Google CloudはYoY+63%の急成長、[[Geminiが月間アクティブユーザー7.5億人]]を突破し広告・クラウド双方で収益化に成功",
     "__AI設備投資が利益を圧迫するという従来懸念が崩れ始め__、投資家の見方をコスト懸念から成長期待へと180度転換させた"])

AI5 = card_side(5,"#2D5BB8","22:00","CNBC",78,
    "Meta・OpenAI・DeepMind幹部が続々独立——大手AIラボからスタートアップへの人材大流出が加速",
    "https://www.cnbc.com/2026/04/28/meta-google-big-tech-staff-ai-labs-investors.html",
    f"{CDN}/ng-thumb-common-ai.jpg",
    ["Meta・Google DeepMind・OpenAI・xAI出身者が相次いで新AI研究機関（Periodic Labs、Ricursive Intelligence等）を設立",
     "[[AGI開発競争の激化]]が高待遇人材の流動を促し、VCが数ヶ月齢スタートアップに数億ドル規模の資金を供与している",
     "__「単一企業が独占する」フェーズから「エコシステム型の競争」フェーズへ__のパラダイムシフトの兆しを示す"])

# ── IT-Consulting ─────────────────────────────────────────────────────────────
CAT_IT = cat_header(3, 4, "#2E6B52", "▲", "IT-Consulting", "IT &amp; Consulting", 5,
    "NECが自社LLM「cotomi」で1兆円AI事業を宣言し、NTTデータは2026年度末にシステム開発の主役を生成AIへ切り替える。一方アクセンチュアはQ3見通しが下振れ、大型IT変革案件の先送りが顕在化。NTT DOCOMO GlobalとアクセンチュアのUniversal Wallet協業はDXの新しい収益モデルを示している。")

IT1 = card_featured("TOP","#2E6B52","09:00","AI・DX News（wa2.ai）",90,
    "NEC、自社開発LLM「cotomi」で生成AI市場を本格攻略——2026年度AI事業1兆円目標を設定",
    "https://wa2.ai/ai-news/nec-ai-dx-blustellar-2026-trend",
    f"{CDN}/ng-thumb-it.jpg",
    ["NECが高い日本語処理能力を持つ自社開発LLM「[[cotomi]]」を軸に、エージェントAI市場を本格攻略する中期戦略を公表した",
     "2026〜2028年の中期計画でAI事業分野のみで[[1兆円規模]]の売上を目標に設定、日本語特化の競合優位を核にする",
     "__大手SIerが自社LLMを持つことで、グローバルAI企業依存からの脱却を図る__業界の自立化という構造転換を象徴"])

IT2 = card_side(2,"#2E6B52","07:00","日本経済新聞",87,
    "NTTデータ、2026年度末までにシステム開発の主役を生成AIに切替——IT人材不足への抜本策",
    "https://www.nikkei.com/article/DGXKKZO93538910R00C26A1MM8000/",
    f"{CDN}/ng-thumb-common-it.jpg",
    ["NTTデータグループは[[生成AIによるシステム開発自動化]]を2026年度末までに実装、開発工程全体で20%の生産性向上を目標",
     "2025年度に500案件への生成AI適用を完了し、2027年度には適用比率50%・工数削減40%へのロードマップを公表",
     "__コード生成だけでなく設計・テスト・保守工程への適用拡大__が焦点で、SIビジネスの単価モデルの再定義につながる"])

IT3 = card_side(3,"#2E6B52","07:00","AlphaSpread",81,
    "アクセンチュアQ2 FY2026: AI需要で売上+8.3%超過、しかしQ3見通しは下振れ——顧客の大型IT変革投資に慎重姿勢",
    "https://www.alphaspread.com/market-news/earnings/accenture-q2-fiscal-2026-revenue-rises-on-ai-demand-but-outlook-disappoints",
    f"{CDN}/ng-thumb-common-it.jpg",
    ["アクセンチュアQ3の売上見通しを$17.6〜18.3Bと発表、コンセンサス$18.6Bを下回り__株価が急落__した",
     "経営陣は「[[クライアントが大型IT変革プロジェクトの発注を先送り]]」と説明、景気不透明感が収益成長を圧迫",
     "コンサル需要の二極化が鮮明：AI活用の小型・短期案件は急増するが、ERPやCoreマイグレーションの大型案件は延期傾向"])

IT4 = card_side(4,"#2E6B52","10:00","Accenture Japan Newsroom",76,
    "NTT DOCOMO GlobalとアクセンチュアがUniversal Wallet Infrastructureを共同展開——デジタル信頼基盤を構築",
    "https://newsroom.accenture.jp/jp/news/2026/release-20260107",
    f"{CDN}/ng-thumb-common-it.jpg",
    ["NTT DOCOMO GlobalとアクセンチュアがAI・データ主導型社会に向けた[[Universal Wallet Infrastructure（UWI）]]を共同構築",
     "デジタルアイデンティティ・マネー・文書の発行・検証・管理を統合した企業向けデジタル信頼サービス基盤として設計",
     "__日本の通信キャリアとグローバルコンサルの掛け算__が生む新形態のDX基盤として、アジア太平洋市場での先行優位が焦点"])

IT5 = card_side(5,"#2E6B52","08:00","日経XTECH",73,
    "NEC・富士通・NTTデータ・日立が「御用聞きSI」から脱却——戦略コンサルへの本格転換を宣言",
    "https://xtech.nikkei.com/atcl/nxt/column/18/03330/091000001/",
    f"{CDN}/ng-thumb-common-it.jpg",
    ["日本の大手SIer4社が「要件定義書待ち」のビジネスモデルを脱し、[[クライアント経営課題から入る戦略コンサル型]]への転換を宣言",
     "グローバル外資コンサル（アクセンチュア・マッキンゼー・BCG）との直接競合を意識した訴求力強化が課題",
     "__「デジタル人材の内製化支援」が新たな収益源__として浮上し、2026年度から専任チームを設置する動きが相次ぐ"])

# ── Economy ──────────────────────────────────────────────────────────────────
CAT_EC = cat_header(4, 4, "#8E2A19", "■", "経済", "Economy", 5,
    "GAFAM Q1 2026決算が出揃い、Alphabet純利益81%増・Amazon AWS +28%など軒並み最高益でAI投資回収フェーズへの移行を証明。FRBは金利を据え置き、S&amp;P500は7,230ptで最高値を更新した。3月雇用統計の予想3倍超えがFRBをジレンマに追い込む中、日経平均は6万円台を維持している。")

EC1 = card_featured("TOP","#8E2A19","07:00","Uncover Alpha / CNBC",95,
    "GAFAM Q1 2026決算出揃う: AI・クラウドが軒並み最高益——5社の年間AI設備投資650億ドル超が確定",
    "https://www.uncoveralpha.com/p/amazon-google-microsoft-meta-q1-earnings",
    f"{CDN}/ng-thumb-economy.jpg",
    ["Alphabet・Amazon・Meta・Microsoftが揃って最高益を更新し、5社の2026年AI設備投資見込みは[[$650B超]]に達することが確認された",
     "Alphabet純利益81%増（$62.6B）、Amazon AWS +28%、Microsoft営業利益+20%と__AI投資の利益圧迫懸念が完全に覆された__",
     "Apple単独ではTim Cook CEOの9月退任報道が重しとなったが、クラウド・AI系4社の好決算がS&amp;P500を底上げする構図"])

EC2 = card_side(2,"#8E2A19","08:00","FP Trendy",90,
    "FRB・4月FOMC金利据え置き——利下げ見通し年1回に後退、中東インフレと関税リスクを警戒",
    "https://www.fptrendy.com/2026/04/30/frb-rate-hold-powell-independence/",
    f"{CDN}/ng-thumb-common-economy.jpg",
    ["FRBが[[4月29日FOMC]]で政策金利4.50%の据え置きを全会一致で決定、エネルギー価格上昇と関税の影響を見極める方針",
     "2026年の利下げ見通しはFOMC参加者の中央値で年1回に後退し、__6月以降も利下げを急がないタカ派メッセージ__が際立つ",
     "パウエル議長は「法的攻撃」への警戒感を示しつつFRB独立性を強調、5月15日任期切れ後も理事継続を表明した"])

EC3 = card_side(3,"#8E2A19","07:00","OANDA Japan",85,
    "S&amp;P500が7,230pt・2営業日連続で最高値更新——好決算と利下げ期待剥落が同居する「逆説的強気」",
    "https://www.oanda.jp/lab-education/market_news/2026_05_01_us500/",
    f"{CDN}/ng-thumb-common-economy.jpg",
    ["S&amp;P500が5月1日終値[[7,230.12pt]]で最高値を更新し、4月30日比+21pt（+0.29%）と2日続伸した",
     "11セクター中10セクターが上昇し__GAFAM決算が全体を底上げ__、エネルギーのみ前日比マイナスとなった",
     "利下げ年1回への見通し後退と好決算が同時進行する「逆説的強気相場」が定着、野村は年末目標を7,500ptへ引き上げ"])

EC4 = card_side(4,"#8E2A19","23:00","FinancialContent",80,
    "米3月非農業部門雇用者+17.8万人、予想の3倍——底堅い労働市場がFRBの利下げ判断をさらに困難に",
    "https://markets.financialcontent.com/stocks/article/marketminute-2026-4-7-strong-jobs-report-us-hiring-surprises-as-unemployment-tumbles",
    f"{CDN}/ng-thumb-common-economy.jpg",
    ["4月3日発表の3月雇用統計で非農業部門雇用者数が[[+17.8万人]]（予想+6万人の3倍）と大幅上振れ、失業率4.3%",
     "医療・建設・物流が牽引した一方、連邦政府雇用は-1.8万人と政府効率化（DOGE）の影響が継続している",
     "__4月分は5月8日発表予定__で3月の底堅さがFRBの利下げ判断をさらに困難にする可能性が高い"])

EC5 = card_side(5,"#8E2A19","10:00","三井住友DSアセットマネジメント",75,
    "日経平均6万円台定着——TOPIXとの乖離18% vs 9%、フィジカルAI・ロボット銘柄に集中",
    "https://www.smd-am.co.jp/market/ichikawa/2026/04/irepo260428/",
    f"{CDN}/ng-thumb-common-economy.jpg",
    ["4月27日に[[6万円台]]を突破した日経平均が連休前後も定着し、三井住友DSが「6万円は通過点」との強気見通しを維持",
     "ファナックを中心とするフィジカルAI・産業ロボット銘柄がTOPIX比+9ptの超過リターンをけん引している",
     "__年初来+18%の日経平均 vs +9%のTOPIX__という乖離拡大は、AI関連セクターへの集中が市場の歪みを生む警戒信号でもある"])

CATEGORIES_HTML = (
    CAT_FX + FX1 + FX2 + FX3 + FX4 + FX5 +
    CAT_AI + AI1 + AI2 + AI3 + AI4 + AI5 +
    CAT_IT + IT1 + IT2 + IT3 + IT4 + IT5 +
    CAT_EC + EC1 + EC2 + EC3 + EC4 + EC5
)

# ── 考察セクション ────────────────────────────────────────────────────────────
def section(num, tag, accent, heading, body):
    return f"""
<tr><td class="ng-section-pad" style="background:#FAF7F0;padding:0 36px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-bottom:1px dashed #E2DED4;"><tbody><tr>
    <td class="ng-section-num-cell" width="80" valign="top" style="padding:28px 16px 28px 0;">
      <div class="m ng-section-num" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:38px;font-weight:900;color:{accent};line-height:0.9;letter-spacing:-2px;">§{num:02d}</div>
      <div class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:9px;color:#fff;background:{accent};padding:2px 6px;display:inline-block;letter-spacing:1.5px;margin-top:8px;">{tag}</div>
    </td>
    <td class="ng-section-text-cell" valign="top" style="padding:28px 0 28px 20px;border-left:1px solid #E2DED4;">
      <h3 class="ng-section-heading" style="font-size:18px;font-weight:800;margin:0 0 14px;">{heading}</h3>
      <div class="ng-section-body" style="font-size:13.5px;line-height:2.0;">{hl(body)}</div>
    </td>
  </tr></tbody></table>
</td></tr>"""

SECTIONS_HTML = (
    section(1,"総論","#1A1A1A","「収穫期」と「試練期」が同時進行",
        "2026年春は、AIと経済の「収穫期」と為替・金融政策の「試練期」が一枚の地政学的な絵として描かれた週となった。AlphabetがQ1純利益81%増を叩き出し、[[GAFAM5社の年間AI設備投資が$650Bを超える]]ことが確定した一方、日本当局は5〜6兆円という大規模介入で「円安の番人」を自任した。__投資の回収と政策の賭けが同時に進行している__のが現在の市場の本質である。") +
    section(2,"為替・経済","#B8860B","介入効果と金利差の構造問題",
        "政府・日銀の円買い介入は短期的に功を奏し、ドル円は160円台後半から155円台へ急騰した。しかしNRI木内論説員が指摘するように、[[日米金利差4%超]]が残存する限り投機的なドル買い圧力は構造的に繰り返す。FRBが4月FOMC後に利下げ見通しを年1回へ後退させ、__パウエル議長任期後の体制移行リスクも加わった今__、市場は5月8日の4月雇用統計を次のトリガーとして見定めている。") +
    section(3,"AI・技術","#2D5BB8","GPT-5.5台頭とエコシステム争い",
        "OpenAIがGPT-5.5で14ベンチマーク首位を奪還し、AnthropicはClaude Mythos Previewを限定公開しながら評価額$900Bという数字で市場を驚かせた。Googleは第8世代TPUの外販という新戦略でNVIDIA依存脱却を宣言した。ここで注目すべきは、[[AIモデル競争の主軸が「性能」から「エコシステムの占有率」]]へ移行しつつある点だ。企業APIの採用量・インフラ計算資源の確保量・業界特化パートナーシップの数が、次の競争ラウンドの勝敗を決める。") +
    section(4,"産業・業界","#2E6B52","SIerのコンサル転換という長い変革",
        "日本のIT業界では、NECの「cotomi」1兆円戦略とNTTデータの生成AI開発自動化宣言が同日のように並走した。一方でアクセンチュアのQ3ガイダンス下振れは、__「コンサル需要の二極化」がグローバルに定着した__ことを示す。AI活用の小型・短期案件は急増するが、ERP刷新・Coreマイグレーションという大型変革案件は先送りされる。NTT DOCOMO GlobalとアクセンチュアのUniversal Wallet協業のような「新形態のDXインフラ」が次の収益柱として浮上している。") +
    section(5,"明日へ","#C9B98A","5月8日雇用統計とGW後の市場再開",
        "今週最大のイベントは[[5月8日（金）発表の米4月雇用統計]]である。3月の+17.8万人という底堅い数字を踏まえて予想が引き上げられており、上振れなら「利下げさらに遠のく」→ドル高→ドル円157円試験のシナリオが描ける。日本市場はGW明けで週初から為替と株価の二重リスクに直面する。__AIセクターへのセクター集中とTOPIX乖離拡大という歪みが一時的に是正される可能性__も否定できない週になる。")
)

# ── Takeaways ─────────────────────────────────────────────────────────────────
def takeaway(num, color, tag, text):
    return f"""<tr><td style="padding-bottom:12px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#fff;border:1px solid #E2DED4;"><tbody><tr>
    <td width="56" valign="middle" class="m" style="background:{color};color:#fff;text-align:center;font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:18px;font-weight:900;padding:14px 0;">{num}</td>
    <td style="padding:12px 16px;">
      <div class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:9px;color:{color};font-weight:700;letter-spacing:1.5px;margin-bottom:4px;">{tag.upper()}</div>
      <div style="font-size:13px;line-height:1.7;font-weight:600;">{hl(text)}</div>
    </td>
  </tr></tbody></table>
</td></tr>"""

TAKEAWAYS_HTML = (
    takeaway(1,"#B8860B","為替",
        "FRB据え置き・日銀亀裂・[[介入は時間稼ぎ]]——5/8雇用統計が次のドル円トリガー、157円再試験に警戒が必要") +
    takeaway(2,"#2D5BB8","AI",
        "GPT-5.5がClaude打倒、Anthropic $900B・GAFAM $650B AI設備投資で__新次元の競争環境が形成__された") +
    takeaway(3,"#2E6B52","産業",
        "GAFAMは最高益で「AI回収フェーズ」を証明、日本SIerは自社LLMとコンサル転換で生き残りを模索している")
)

# ── Related Issues ────────────────────────────────────────────────────────────
def related_row(date, title, url):
    return f"""<tr><td style="padding:10px 0;border-bottom:1px solid #E2DED4;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tbody><tr>
    <td width="100" class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:11px;color:#5C5A52;">{date}</td>
    <td style="font-size:13px;font-weight:600;"><a href="{url}" style="color:#1A1A1A;text-decoration:none;">{title}</a></td>
    <td width="20" align="right" style="color:#5C5A52;">→</td>
  </tr></tbody></table>
</td></tr>"""

RELATED_HTML = (
    related_row("2026-05-03","#20260503 — FX, AI, IT-Consulting, Game","https://github.com/HIDEPON-UMG/News-Grasp/tree/main/digest/Summary/2026-05-03.md") +
    related_row("2026-05-01","#20260501 — FX, AI, IT-Consulting, Economy, Game","https://github.com/HIDEPON-UMG/News-Grasp/tree/main/digest/Summary/2026-05-01.md") +
    related_row("2026-04-29","#20260429 — GAFAM決算前夜・日経6万円突破","https://github.com/HIDEPON-UMG/News-Grasp/tree/main/digest/Summary/2026-04-29.md")
)

# ── LEAD & Pull Quote HTML ────────────────────────────────────────────────────
LEAD_HTML = hl("本日は為替・AI・IT-Consulting・経済の4分野、計20本のニュースから浮かび上がる最大のテーマは [[GAFAMのAI投資回収]] と [[為替介入後の政策ゲーム]] の同時進行である。以下、各カテゴリを横断して読み解く。")
PULL_HTML = hl("[[AIへの巨額投資]]は『利益圧迫』という懸念を吹き飛ばした——しかし__市場が次に問い始めたのは「その先の競争優位は誰が持続できるのか」__という、より難しい問いだ。")

# ── Build ─────────────────────────────────────────────────────────────────────
html = TEMPLATE
html = html.replace("{{ISSUE_NO}}", "20260504")
html = html.replace("{{ISSUE_DATE}}", "2026-05-04")
html = html.replace("{{ISSUE_WEEKDAY}}", "月")
html = html.replace("{{TOTAL_CATEGORIES}}", "4")
html = html.replace("{{TOTAL_STORIES}}", "20")
html = html.replace("{{TOTAL_SECTIONS}}", "5")
html = html.replace("{{TOC_ROWS_HTML}}", TOC_ROWS)
html = html.replace("{{CATEGORIES_HTML}}", CATEGORIES_HTML)
html = html.replace("{{REFLECTION_TITLE}}", "AI決算の収穫期と通貨の地政学")
html = html.replace("{{REFLECTION_SUBTITLE}}", "GAFAMが証明した投資回収フェーズ、GW介入後の円市場が問う「政策の持続力」")
html = html.replace("{{REFLECTION_LEAD_HTML}}", LEAD_HTML)
html = html.replace("{{REFLECTION_PULL_QUOTE_HTML}}", PULL_HTML)
html = html.replace("{{REFLECTION_SECTIONS_HTML}}", SECTIONS_HTML)
html = html.replace("{{TAKEAWAYS_HTML}}", TAKEAWAYS_HTML)
html = html.replace("{{RELATED_ISSUES_HTML}}", RELATED_HTML)

OUT.write_text(html, encoding='utf-8')
print(f"Written: {OUT} ({len(html):,} bytes)")
