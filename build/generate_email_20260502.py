# -*- coding: utf-8 -*-
"""News Grasp 2026-05-02 HTML メール生成スクリプト"""
import re, os, pathlib

BASE = pathlib.Path(r"C:\Users\hidek\Obsidian\New's Grasp\News-Grasp")
TEMPLATE = (BASE / "prompts" / "email-template.html").read_text(encoding="utf-8")
OUT = BASE / "build" / "email.html"

CDN = "https://raw.githubusercontent.com/HIDEPON-UMG/news-grasp-assets/main"

# --- ヘルパー ---------------------------------------------------------------
def fmt(text, accent):
    """[[keyword]] → highlight, __text__ → underline"""
    text = re.sub(r'\[\[([^\]]+)\]\]',
        f'<strong style="background:{accent};color:#fff;padding:1px 5px;border-radius:2px;">'
        r'\1</strong>', text)
    text = re.sub(r'__([^_]+)__',
        r'<span style="border-bottom:2px solid currentColor;padding-bottom:1px;">\1</span>', text)
    return text

def mk_cat_header(idx, total, glyph, name_jp, name_en, accent, summary, count):
    return f"""
<tr><td class="ng-cat-pad" style="background:{accent};padding:20px 36px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tbody><tr>
    <td style="vertical-align:middle;">
      <div class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:10px;color:rgba(255,255,255,0.7);letter-spacing:2px;margin-bottom:4px;">
        CATEGORY {idx} / {total} · {name_en.upper()}
      </div>
      <div style="font-size:28px;font-weight:800;color:#fff;letter-spacing:-0.5px;line-height:1.1;">
        <span style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;margin-right:10px;">{glyph}</span>{name_jp}
      </div>
    </td>
    <td align="right" style="vertical-align:middle;color:rgba(255,255,255,0.85);font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:11px;">{count} stories</td>
  </tr></tbody></table>
  <div class="ng-cat-summary" style="font-size:13px;color:rgba(255,255,255,0.95);font-style:italic;line-height:1.6;margin-top:10px;padding-top:10px;border-top:1px solid rgba(255,255,255,0.25);">
    {summary}
  </div>
</td></tr>"""

def mk_featured_card(rank, time, source, title, url, thumb, bullets, accent, related=None):
    buls_html = "".join(
        f'<div class="bul ng-card-body" style="color:{accent}">'
        f'<span class="dk">{fmt(b, accent)}</span></div>'
        for b in bullets)
    img = f'<img src="{thumb}" width="568" style="width:100%;display:block;border:1px solid #E2DED4;border-radius:2px;" class="ng-card-thumb-img db ofc brd">'
    rel_html = ""
    if related:
        rel_html = f"""
<div style="margin-top:14px;padding:10px 14px;background:#F2EEE3;border-left:3px solid {accent};font-size:12px;line-height:1.7;">
  <span style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:9px;color:{accent};font-weight:700;letter-spacing:1px;">🔗 関連: {related['axis']}</span><br>
  {fmt(related['note'], accent)}
</div>"""
    return f"""
<tr><td class="ng-card-pad bgcard bbcard pcard" style="background:#FAF7F0;padding:24px 36px;border-bottom:1px solid #EDEAE3;">
  <div class="ng-card-meta m mut fz10 ls05 mb6" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:11px;color:#5C5A52;letter-spacing:0.5px;margin-bottom:6px;">
    <span class="b7" style="background:{accent};color:#fff;padding:2px 6px;font-size:12px;border-radius:2px;">TOP</span>
    <span class="pl8">{time} · {source} · SCORE {rank}</span>
  </div>
  <h3 class="ng-card-title b8 lh145 t812 lsm03" style="font-size:22px;font-weight:800;line-height:1.45;margin:8px 0 12px;letter-spacing:-0.3px;">
    <a href="{url}" class="dk tdn" style="color:#1A1A1A;text-decoration:none;">{title}</a>
  </h3>
  <div class="ng-feature-img" style="margin:0 0 16px;">
    <a href="{url}" style="display:block;text-decoration:none;">{img}</a>
  </div>
  {buls_html}
  {rel_html}
</td></tr>"""

def mk_side_card(rank, time, source, title, url, thumb, bullets, accent, related=None):
    buls_html = "".join(
        f'<div class="bul ng-card-body" style="color:{accent}">'
        f'<span class="dk">{fmt(b, accent)}</span></div>'
        for b in bullets)
    img = f'<img src="{thumb}" width="140" height="90" class="ng-card-thumb-img db ofc brd" style="width:140px;height:90px;display:block;object-fit:cover;border:1px solid #E2DED4;border-radius:2px;">'
    rel_html = ""
    if related:
        rel_html = f"""
<div style="margin-top:10px;padding:8px 12px;background:#F2EEE3;border-left:3px solid {accent};font-size:11px;line-height:1.7;">
  <span style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:9px;color:{accent};font-weight:700;letter-spacing:1px;">🔗 {related['axis']}</span><br>
  {fmt(related['note'], accent)}
</div>"""
    return f"""
<tr><td class="ng-card-pad bgcard bbcard pcard" style="background:#FAF7F0;padding:24px 36px;border-bottom:1px solid #EDEAE3;">
  <div class="ng-card-meta m mut fz10 ls05 mb6" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:11px;color:#5C5A52;letter-spacing:0.5px;margin-bottom:6px;">
    <span class="b7" style="background:{accent};color:#fff;padding:2px 6px;font-size:12px;border-radius:2px;">{str(rank).zfill(2)}</span>
    <span class="pl8">{time} · {source} · SCORE {rank}</span>
  </div>
  <h3 class="ng-card-title b8 lh145 t812 lsm03" style="font-size:20px;font-weight:800;line-height:1.45;margin:8px 0 12px;letter-spacing:-0.3px;">
    <a href="{url}" class="dk tdn" style="color:#1A1A1A;text-decoration:none;">{title}</a>
  </h3>
  <table width="100%" class="ng-side-table" cellpadding="0" cellspacing="0" border="0"><tbody><tr>
    <td class="ng-card-thumb thb pr16 vtop" width="140" style="width:140px;height:90px;padding-right:16px;vertical-align:top;">
      <a href="{url}" class="db tdn" style="display:block;text-decoration:none;">{img}</a>
    </td>
    <td class="ng-card-body-cell vtop" style="vertical-align:top;">
      {buls_html}
      {rel_html}
    </td>
  </tr></tbody></table>
</td></tr>"""

def mk_section(num, tag, accent, heading, body, accent_hex):
    return f"""
<tr><td class="ng-section-pad" style="background:#FAF7F0;padding:0 36px;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-bottom:1px dashed #E2DED4;"><tbody><tr>
    <td class="ng-section-num-cell" width="80" valign="top" style="padding:28px 16px 28px 0;">
      <div class="m ng-section-num" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:38px;font-weight:900;color:{accent_hex};line-height:0.9;letter-spacing:-2px;">§{num:02d}</div>
      <div class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:9px;color:#fff;background:{accent_hex};padding:2px 6px;display:inline-block;letter-spacing:1.5px;margin-top:8px;">{tag}</div>
    </td>
    <td class="ng-section-text-cell" valign="top" style="padding:28px 0 28px 20px;border-left:1px solid #E2DED4;">
      <h3 class="ng-section-heading" style="font-size:18px;font-weight:800;margin:0 0 14px;">{heading}</h3>
      <div class="ng-section-body" style="font-size:13.5px;line-height:2.0;">{body}</div>
    </td>
  </tr></tbody></table>
</td></tr>"""

# =============================================================================
# DATA
# =============================================================================
FX_ACCENT = "#B8860B"
AI_ACCENT = "#2D5BB8"
IT_ACCENT = "#2E6B52"
GM_ACCENT = "#5E3D8C"

fx_articles = [
  {"score":95,"time":"06:30","source":"IG Markets Japan",
   "title":"為替介入でUSD/JPY 160→155円台急落 — 日本政府が1年9ヶ月ぶりに円買い介入",
   "url":"https://www.ig.com/jp/news-and-trade-ideas/jpy-is-pushed-up-seemingly-by-japanese-government-intervention-260501",
   "thumb":f"{CDN}/ng-thumb-fx.jpg",
   "bullets":["4月30日夜、USD/JPYが[[160.72円]]から[[155.57円]]へ約5円急伸。財務省が[[34.5億ドル]]規模と推計される円買い・ドル売り介入を実施、2024年7月以来1年9ヶ月ぶりの実弾介入となった。",
              "介入直前に財務大臣と財務官が相次いで「__断固たる措置を辞さない__」と警告し口先介入から実弾へ移行。5月1日午後には157.28円まで反発した。",
              "2022年以降のパターンでは最初の介入から__3営業日以内に2回目__が行われており、GW明け週の追加介入リスクが市場の最大警戒材料となっている。"],
   "related":{"axis":"復状","note":"BOJ会合3名反対から実際の市場介入へという政策圧力の連鎖が一週間で完成した。"}},
  {"score":85,"time":"09:30","source":"野村総合研究所（NRI）",
   "title":"NRI分析：GW介入は「時間稼ぎ」— 構造的円安の本質は変わらず",
   "url":"https://www.nri.com/jp/media/column/kiuchi/20260501.html",
   "thumb":f"{CDN}/ng-thumb-common-fx.jpg",
   "bullets":["[[日米金利差]]縮小なしに円安転換は困難。[[FRB]]は3.5-3.75%を維持し、[[日銀]]の利上げペースは依然緩慢なまま。今回の介入は「__時間を買う__」意味合いが強い。",
              "160円台を放置すれば輸入物価上昇が国民生活を直撃という政治的判断。中長期的には[[日銀]]の6月利上げの有無が__真の分岐点__となる。",
              "3名の反対票（利上げ要求）は市場期待を強く形成しており、6月会合は本年最大の政策イベントとなりつつある。"],
   "related":None},
  {"score":82,"time":"08:00","source":"FXStreet / BNY",
   "title":"BNY: USD/JPY追加介入リスクと中東原油が為替の二大焦点",
   "url":"https://www.fxstreet.com/news/usd-jpy-intervention-risk-and-oil-focus-shape-outlook-bny-202605011352",
   "thumb":f"{CDN}/ng-thumb-common-fx.jpg",
   "bullets":["ホルムズ海峡の混乱継続で[[原油価格]]上昇→FRB利下げ遅延→円安圧力が継続。[[USD/JPY]]の上値抵抗は[[160.50円]]。BNYは「__介入は160円台に上限を設けた__」と分析。",
              "[[AUD/USD]]など資源国通貨は原油高の恩恵で上昇。ドル独歩高が[[EUR/JPY]]にも波及している。",
              "GW中の薄商いで相場反応が通常より増幅されやすい局面。週明けの追加介入有無が最大の注目点。"],
   "related":None},
  {"score":80,"time":"07:30","source":"forex.com",
   "title":"EUR/USD Forex Friday: ECB追加利上げ検討、エネルギー危機でスタンス転換",
   "url":"https://www.forex.com/en/news-and-analysis/eur-usd-forecast-forex-friday-may-1-2026/",
   "thumb":f"{CDN}/ng-thumb-common-fx.jpg",
   "bullets":["[[ECB]]はホルムズ海峡閉鎖継続なら6月に__利上げへの転換__も辞さない姿勢を初めて示唆。エネルギーインフレの持続が欧州中銀の方向性を変えつつある。",
              "[[EUR/USD]]は1.087付近。FRBが「エネルギーショックは一過性」と判断すればドル売りに傾き上値目途は1.10。逆にタカ派維持なら1.075が下値。",
              "日本のGW中は流動性低下で介入に対する相場反応が__通常より増幅__されやすい。薄商い中の大きな値動きには注意が必要。"],
   "related":None},
  {"score":75,"time":"10:00","source":"IG Markets Japan",
   "title":"USD/JPY 週足テクニカル：155.50は61.8%フィボナッチ、次の節目を探る",
   "url":"https://www.ig.com/jp/news-and-trade-ideas/usjdpy-forecast-and-key-levels-260501",
   "thumb":f"{CDN}/ng-thumb-common-fx.jpg",
   "bullets":["155.50円は2月安値から4月高値の上昇幅の[[フィボナッチ61.8%]]水準で、介入後の急落はこのレベルでぴったり止まった。次の上値抵抗は156.50（50%戻し）。",
              "__週明け157円維持__がGW後の方向性を決める重要なテクニカルポイント。週末の薄商いでの動きは月曜日の本格取引再開でリセットされる可能性がある。",
              "[[ヘッジファンド]]のネット・ショートポジションは依然大きく、追加介入があれば__ショートカバーが集中__して急反発リスクも。"],
   "related":None},
]

ai_articles = [
  {"score":92,"time":"08:00","source":"TechBriefly / SecurityWeek",
   "title":"Anthropic Mythos vs OpenAI GPT-5.5-Cyber — サイバーAI軍拡競争の構図",
   "url":"https://techbriefly.com/2026/04/30/new-gpt-5-5-cyber-model-follows-anthropics-secret-mythos-release/",
   "thumb":f"{CDN}/ng-thumb-ai.jpg",
   "bullets":["[[Anthropic]]の[[Mythos]]はゼロデイ脆弱性を自律検出し[[AWS]]・[[Apple]]・[[Microsoft]]・[[Google]]・[[Cisco]]ら12パートナー限定公開。[[OpenAI]]はMythosを批判しつつ[[GPT-5.5-Cyber]]で同様の制限戦略を採用した。",
              "GPT-5.5-Cyberは__「高」リスク分類__（生物・サイバー両域）で一般ユーザーへの提供を制限しつつ認定セキュリティ専門家向けには拡大。両社の安全哲学の差は実質縮小した。",
              "[[Anthropic]]は使用クレジット[[1億ドル]]とOSS支援[[4百万ドル]]を提供し「責任あるAI能力開放」の実証実験と位置づける。__企業責任の実験場__としてのサイバーAI競争という新フェーズが始まった。"],
   "related":None},
  {"score":85,"time":"07:00","source":"TechCrunch / Fortune",
   "title":"Anthropic 年間収益$300億突破 — Google $400億投資コミットで巨大AIエコシステム形成",
   "url":"https://techcrunch.com/2026/04/24/google-to-invest-up-to-40b-in-anthropic-in-cash-and-compute/",
   "thumb":f"{CDN}/ng-thumb-common-ai.jpg",
   "bullets":["[[Anthropic]]のラン・レート収益が[[300億ドル]]（約4.5兆円）を突破。2025年末の$90億から3ヶ月で急増し、[[Google]]の最大[[400億ドル]]投資（バリュエーション3,500億ドル）が背景にある。",
              "[[Google]]は10億ドルを即時出資し残り300億ドルは業績達成条件付き。__コンピュートとキャッシュの両軸__で出資する形態は業界初の試みだ。",
              "[[Anthropic]]・[[Google]]・[[Broadcom]]の3社パートナーシップでAIインフラの垂直統合が加速。[[Claude]]の訓練と推論コストを削減する専用チップ開発が進む。"],
   "related":None},
  {"score":83,"time":"09:00","source":"MIT Technology Review / Google DeepMind",
   "title":"Google DeepMind AlphaEvolve：AIが数学の「新発見」をし自社インフラを自律最適化",
   "url":"https://cleverhack.com/frontier-ai-lab-news",
   "thumb":f"{CDN}/ng-thumb-common-ai.jpg",
   "bullets":["[[AlphaEvolve]]（[[Gemini]]搭載のコーディングエージェント）が新数学的構造を自律発見し、[[Google]]の世界規模コンピューティングリソースの__0.7%を回収__。",
              "[[Gemini]]アーキテクチャの主要カーネルを[[23%高速化]]した実績があり、1年以上にわたりGoogleの内部インフラに継続展開。研究成果が内部導入へと即転換される循環が確立した。",
              "「__AIがAIのインフラを最適化する__」ループが現実のものとなりつつある。AlphaEvolveは企業インフラの継続的自律改善という産業的意義を持つ。"],
   "related":None},
  {"score":80,"time":"08:30","source":"Fortune / New AI Model Releases",
   "title":"OpenAI 年間収益$250億超・IPO準備加速 — GPT-5.4で100万トークンエージェント展開",
   "url":"https://blog.mean.ceo/new-ai-model-releases-news-april-2026/",
   "thumb":f"{CDN}/ng-thumb-common-ai.jpg",
   "bullets":["[[OpenAI]]の年間収益が[[250億ドル]]を突破し、2026年末のIPOを視野に初期準備ステップに着手。[[Microsoft]]とのAzureパートナーシップがAI需要の主要エンジン。",
              "[[GPT-5.4]]は[[100万トークン]]のコンテキストウィンドウと多段階ワークフロー自律実行能力を搭載。[[OSWorld-V]]ベンチマークで75%スコアを達成しデスクトップ生産性タスクを自律実行できる。",
              "AIエージェントの実用化が__収益化の本流__として浮上。単なるチャットボットから「__作業代替AI__」への移行が加速している。"],
   "related":None},
  {"score":76,"time":"10:00","source":"mem0.ai / Clarifai",
   "title":"AI-Agent Memory 2026：長期記憶・外部ストアがエージェント進化の次の戦場",
   "url":"https://mem0.ai/blog/state-of-ai-agent-memory-2026",
   "thumb":f"{CDN}/ng-thumb-common-ai.jpg",
   "bullets":["エージェントAIの実用化に伴い[[長期記憶]]管理が新たな技術課題として浮上。外部ベクターストアと短期コンテキストの組み合わせが主流設計となりつつある。",
              "[[Kimi-K2.6]]・[[MiMo-V2.5]]・[[Qwen3.5]]などがマルチモーダルとメモリ機能を統合。フロンティアモデルでは__マルチモーダルが「標準装備」__化している。",
              "「覚える能力」から「__文脈をまたいで自律行動する能力__」へ——エージェントAIの定義が再定義されつつある。"],
   "related":None},
]

it_articles = [
  {"score":90,"time":"07:00","source":"Inc. / Fast Company / Metaintro",
   "title":"McKinsey 10%削減：AI自動化でコンサル業界の「ピラミッド型人材構造」が崩壊",
   "url":"https://www.metaintro.com/blog/mckinsey-layoffs-2026-ai-white-collar-consulting",
   "thumb":f"{CDN}/ng-thumb-it.jpg",
   "bullets":["[[McKinsey]]がバックオフィスを中心に[[10%]]（数千人規模）を削減。「[[生成AI]]が数分でこなす仕事」が対象で[[ボブ・スターンフェルス]]は「今後2年継続する」と明言。__知識労働の自動化が現実のコスト削減__へと進化した転換点。",
              "コンプライアンス・リサーチ・レポーティングなど「アナリスト・ピラミッドが数週間かけてこなした仕事」が対象。__アナリスト職の存在意義__が根本から問われている。",
              "コンサル需要そのものは消えず、__付加価値の源泉が「情報整理」から「判断」へ__移行中。[[BCG]]と[[Bain]]は独自AIコパイロットで差別化を図る。"],
   "related":None},
  {"score":85,"time":"08:00","source":"Bloomberg",
   "title":"Bloomberg: AIがMcKinsey・BCG・Bainの新卒採用を激変 — アナリスト職の価値再定義",
   "url":"https://www.bloomberg.com/news/articles/2026-04-15/ai-influences-how-mckinsey-bcg-bain-hire-for-entry-level-consulting-jobs",
   "thumb":f"{CDN}/ng-thumb-common-it.jpg",
   "bullets":["2026年の採用市場は「AI時代に__不可欠と証明できる候補者__」のみを選抜する構造に変化。ピーク時より内定率が大幅低下し、合格者は過去最高レベルという逆説的状況。",
              "プリンストン大学生は「アナリストとして不可欠かどうかが不明確になっている」と発言。__エントリーレベル職の存在意義__が問われている。",
              "[[BCG]]・[[Bain]]はプロプライエタリAIコパイロットを導入しジュニアコンサルタントの生産性を向上させつつ採用枠を絞り込んでいる。「少人数・高品質」へのシフトが始まった。"],
   "related":None},
  {"score":80,"time":"09:00","source":"The Business Research Company",
   "title":"AI Advisory市場、2033年に$257億へ急拡大 — コンサルに新成長フロンティア",
   "url":"https://www.thebusinessresearchcompany.com/report/it-consulting-global-market-report",
   "thumb":f"{CDN}/ng-thumb-common-it.jpg",
   "bullets":["AI advisory市場は2025年の[[110億ドル]]から2033年には[[2,570億ドル]]へ、__年率成長率47%超__で急拡大する予測。コンサルが削減するボリュームを超える新市場が生まれつつある。",
              "コンサル大手にとって従来のIT実装を超える「AI経済の__インフラ層__」を担うビジネスが形成されつつある。エンタープライズAI展開のROI保証が新たな差別化領域。",
              "[[Accenture]]・[[デロイト]]・[[PwC]]・[[McKinsey]]・[[BCG]]がAIアドバイザリーを最優先戦略分野に設定。「リストラと成長が同時進行する」という矛盾が業界を象徴している。"],
   "related":None},
  {"score":78,"time":"10:00","source":"AInvest",
   "title":"Accenture $22億AI投資の現実：パイロット95%が失敗するギャップを「コンサル基盤」で解決",
   "url":"https://www.ainvest.com/news/accenture-2-2-billion-ai-bet-exposes-95-pilot-failure-gap-driving-consulting-infrastructure-play-2604/",
   "thumb":f"{CDN}/ng-thumb-common-it.jpg",
   "bullets":["[[Accenture]]は2026年度のAI関連投資を[[22億ドル]]と発表したが、企業のAIパイロット失敗率は依然[[95%]]と高い。失敗の最大要因はデータ品質・組織変革の欠如・ROI指標の不在。",
              "[[Accenture]]は「[[Faculty]]買収」「[[Databricks]]パートナーシップ」「[[Cyber.AI]]（Anthropic [[Claude]]搭載）」の3軸で__統合コンサル基盤__を構築中。",
              "__「AIができること」より「AIを実装できる組織」__を作ることが競争優位の源泉になるという認識が業界全体に浸透しつつある。"],
   "related":None},
  {"score":74,"time":"07:30","source":"Fortune / CNBC",
   "title":"OpenAI Frontier Alliance：Accenture・BCG・McKinsey・Capgeminiが企業AI展開の最前線同盟に",
   "url":"https://fortune.com/2026/02/23/openai-partners-with-mckinsey-bcg-accenture-and-capgemini-to-push-its-frontier-ai-agent-platform/",
   "thumb":f"{CDN}/ng-thumb-common-it.jpg",
   "bullets":["[[OpenAI]]が[[Accenture]]・[[BCG]]・[[McKinsey]]・[[Capgemini]]と複数年の「Frontier Alliance」を締結。[[Accenture]]・[[Capgemini]]がSI、[[BCG]]・[[McKinsey]]が戦略担当という役割分担が確立。",
              "[[Frontier AIエージェントプラットフォーム]]の企業導入でコンサルが仲介役として機能し、AI投資ROIの__測定と説明責任__を顧客に提供。",
              "__「AIを売る」から「AIで組織を作る」__という新しい価値提案が業界標準になりつつある。コンサルの位置づけはベンダーではなく変革パートナーへと進化している。"],
   "related":None},
]

gm_articles = [
  {"score":92,"time":"07:00","source":"GameBiz / 野村モーニングスター",
   "title":"任天堂FY2026決算：Switch 2出荷1900万台に上方修正、売上2兆2500億円を予想",
   "url":"https://gamebiz.jp/news/405347",
   "thumb":f"{CDN}/ng-thumb-game.jpg",
   "bullets":["2026年3月期第3四半期で[[任天堂]]の売上高は[[1兆9058億円]]（前年同期比+99.3%）、営業利益[[3003億円]]（同+21.3%）。[[Switch 2]]の爆発的な立ち上がりが全体業績を牽引した。",
              "通期出荷見通しは当初の[[1500万台]]から[[1900万台]]に引き上げ。__任天堂史上最速ペース__の普及を記録しているが、株価は決算発表後に[[12%安]]と大幅下落した。",
              "市場は「好決算でも株価下落」という逆説を見せており、欧米年末商戦の不振や__ソフトウェア収益性__への懸念が根底にある。"],
   "related":None},
  {"score":85,"time":"09:00","source":"GameBiz / カプコン",
   "title":"カプコン「バイオハザード レクイエム」Switch 2版ゲームプレイ映像公開 — RE Engineフル活用",
   "url":"https://gamebiz.jp/news/420513",
   "thumb":f"{CDN}/ng-thumb-common-game.jpg",
   "bullets":["[[カプコン]]が[[バイオハザード レクイエム]]の[[Switch 2]]版ゲームプレイ動画を公開。[[RE Engine]]最新版で高品質グラフィックスを携帯モードでも実現した。",
              "[[バイオハザード レクイエム]]はPS5・Xbox版と__同日同価格__でのリリースを予定。マルチプラットフォーム対等戦略がSwitch 2の「本格ゲーム機」認知を後押しする。",
              "2026年は「__カプコンの年__」と評されており、[[プラグマタ]]の完全新作と合わせてSwitch 2向けの最重要サードパーティ地位を確立。"],
   "related":None},
  {"score":80,"time":"10:00","source":"GameLuster / NintendoLife",
   "title":"Square Enix「Tales of Arise Beyond the Dawn Edition」Switch 2版 5/22発売確定",
   "url":"https://www.nintendolife.com/guides/upcoming-nintendo-switch-2-games-and-accessories-for-may-and-june-2026",
   "thumb":f"{CDN}/ng-thumb-common-game.jpg",
   "bullets":["[[スクウェア・エニックス]]の[[テイルズ オブ アライズ]] Beyond the Dawn Editionが[[5月22日]]に[[Switch 2]]向けに発売。大型DLC同梱の完全版でのSwitch 2初登場となる。",
              "[[FF7リバース]]のSwitch 2版も[[6月3日]]に控えており、[[SQEX]]は2026年前半を[[Switch 2]]の重要タイトル供給期間と明確に位置づける。",
              "大手JRPGパブリッシャーのSwitch 2参入加速は、任天堂ハードの「__RPGプレイヤー取り込み力__」を証明するシグナルだ。"],
   "related":None},
  {"score":78,"time":"08:30","source":"ComicBook.com",
   "title":"Capcom Pragmata：SF完全新作がSwitch 2でDay-and-Date — 次世代カプコン戦略の柱",
   "url":"https://comicbook.com/gaming/feature/capcom-2026-year-op-ed/",
   "thumb":f"{CDN}/ng-thumb-common-game.jpg",
   "bullets":["[[カプコン]]の[[プラグマタ]]（月面SFアクション）がPS5・Xbox Series X|Sと同日で[[Switch 2]]にリリース予定。主人公ヒューと少女アンドロイドのディアナが月面脱出を目指す完全新規IP。",
              "[[プラグマタ]]は[[RE Engine]]の物理演算を最大限に活用し、「__Switch 2と共に設計された作品__」として開発されたと公式がコメント。Switch 2の技術的ポテンシャルを示す。",
              "[[フロム・ソフトウェア]]・[[バンダイナムコ]]らとともにセカンドウェーブのサードパーティ支持が[[Switch 2]]エコシステムの成熟を示す。__任天堂とサードパーティの共闘__が本格化している。"],
   "related":None},
  {"score":75,"time":"11:00","source":"日本経済新聞 / ダイヤモンド",
   "title":"Switch 2 歴代最速販売も株価12%急落：欧米年末商戦「不振の元凶」とは",
   "url":"https://diamond.jp/articles/-/385041",
   "thumb":f"{CDN}/ng-thumb-common-game.jpg",
   "bullets":["[[Switch 2]]の累計出荷は任天堂ハード歴代最速販売ペースだが、欧米年末商戦で販売が伸び悩み。__日本・アジアとの地域格差__が株価下落の一因となっている。",
              "[[任天堂]]の[[Switch 2]]は定価3万7980円（日本）で欧米ではさらに高価格帯。__インフレ後の可処分所得減少__が欧米消費者の大型ゲーム機購入を抑制している。",
              "市場は「__成長の踊り場__」入りを懸念。ただしカプコン・スクエニのラインナップ充実で第2四半期以降の回復を期待する声も多く、楽観と悲観が交錯している。"],
   "related":None},
]

# =============================================================================
# BUILD HTML
# =============================================================================

# --- TOC ---
toc_rows = ""
for idx, (glyph, name_jp, name_en, accent) in enumerate([
    ("¥","為替","Foreign Exchange",FX_ACCENT),
    ("◆","AI","Artificial Intelligence",AI_ACCENT),
    ("▲","IT-Consulting","IT & Consulting",IT_ACCENT),
    ("●","ゲーム","Gaming",GM_ACCENT),
], 1):
    toc_rows += f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:6px;"><tbody><tr>
  <td width="32" class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:14px;color:{accent};font-weight:900;">{glyph}</td>
  <td style="font-size:14px;font-weight:700;">{idx}. {name_jp} <span style="font-weight:400;color:#5C5A52;font-size:12px;">({name_en})</span></td>
  <td align="right" class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:11px;color:#5C5A52;">5 stories</td>
</tr></tbody></table>"""

# --- Categories ---
cats_html = ""
categories = [
    (1, "¥","為替","Foreign Exchange",FX_ACCENT,"fx",
     "GW中の為替介入でUSD/JPYが160.72から155.57円へ急落後、157円台で小康。追加介入への警戒と日米金利差の構造的葛藤が続く。",
     fx_articles),
    (2, "◆","AI","Artificial Intelligence",AI_ACCENT,"ai",
     "AIサイバーセキュリティモデル競争が加熱。AnthropicのMythosとOpenAIのGPT-5.5-Cyberが相次いで限定公開され、高リスクAIの制御的展開という新フェーズに突入した。",
     ai_articles),
    (3, "▲","IT-Consulting","IT &amp; Consulting",IT_ACCENT,"it",
     "McKinseyが10%リストラを断行し、AIによるバックオフィス自動化がコンサル業界の雇用モデルを根本から変えつつある。一方でAI advisory市場は爆発的成長予測という二面性が鮮明に。",
     it_articles),
    (4, "●","ゲーム","Gaming",GM_ACCENT,"game",
     "任天堂が通期決算で売上2.25兆円・Switch 2出荷1900万台見込みを発表。歴代最速販売ペースも欧米年末商戦の不振が株価に影を落とし、カプコン・スクエニが5月のラインナップで盛り上げを図る。",
     gm_articles),
]

for (idx, glyph, name_jp, name_en, accent, cat_id, summary, arts) in categories:
    cats_html += mk_cat_header(idx, 4, glyph, name_jp, name_en, accent, summary, 5)
    for i, art in enumerate(arts):
        rel = art.get("related")
        if i == 0:
            cats_html += mk_featured_card(art["score"], art["time"], art["source"],
                art["title"], art["url"], art["thumb"], art["bullets"], accent, rel)
        else:
            cats_html += mk_side_card(art["score"], art["time"], art["source"],
                art["title"], art["url"], art["thumb"], art["bullets"], accent, rel)

# --- Reflection ---
LEAD_ACCENT = "#C9B98A"

def fmtr(text):
    """Reflection section formatter (gold accent for [[]])"""
    text = re.sub(r'\[\[([^\]]+)\]\]',
        r'<strong style="background:#C9B98A;color:#1A1A1A;padding:1px 5px;border-radius:2px;">\1</strong>', text)
    text = re.sub(r'__([^_]+)__',
        r'<span style="border-bottom:2px solid #C9B98A;padding-bottom:1px;">\1</span>', text)
    return text

def fmtb(text):
    """Body section formatter (dark accent for [[]])"""
    text = re.sub(r'\[\[([^\]]+)\]\]',
        r'<strong style="background:#E2DED4;color:#1A1A1A;padding:1px 5px;border-radius:2px;">\1</strong>', text)
    text = re.sub(r'__([^_]+)__',
        r'<span style="border-bottom:2px solid #5C5A52;padding-bottom:1px;">\1</span>', text)
    return text

LEAD = fmtr("本日4分野・20本のニュースから浮かび上がる最大のテーマは [[管理された加速]] と [[防衛的調整]] の同時進行である。政府は為替に介入し、AI企業は能力を限定公開し、コンサル大手は人員を削減し、ゲーム企業は最速販売でも株価を下げる。__表面上の成功が「次のフェーズへの移行コスト」を問われる日__が来た。以下、各カテゴリを横断して読み解く。")

PQUOTE = "量的な成功（最速販売・最高収益・最大出荷）が必ずしも質的な評価と一致しない日——市場は<span style=\"border-bottom:2px solid #1A1A1A;\">次のフェーズを先読み</span>し始めている。"

sections_data = [
    (1,"総論","#1A1A1A","「成功の次の問い」という新フェーズ",
     "今日の4つのカテゴリに共通するのは「記録的な数字が出ているのに市場や当局が不安を感じている」という構図だ。[[USD/JPY]]は[[160台]]の記録を更新し、[[任天堂]]は歴代最速で[[Switch 2]]を販売し、[[OpenAI]]・[[Anthropic]]は収益最高値を更新した。なのに政府は介入し、コンサルは削減し、株は下がる。__成功の定義そのものが書き換えられている__局面であり、「量的成功の次に何が問われるか」が2026年の問いとなっている。"),
    (2,"為替・経済",FX_ACCENT,"160円防衛線と構造的円安の綱引き",
     "[[日本政府]]が[[34.5億ドル]]規模の為替介入で[[USD/JPY]]を5円急落させた。しかし[[NRI]]の[[木内登英]]が指摘するように、これは「__時間稼ぎ__」に過ぎない。[[FRB]]の利下げ余地が乏しく[[日銀]]の利上げペースが緩慢なままでは構造的円安は変わらない。6月の[[日銀]]政策会合が__本当の分岐点__だ。介入は160円に上限を設けたが、その防衛ラインを維持できるかは金利政策にかかっている。"),
    (3,"AI・技術",AI_ACCENT,"サイバーAIの「管理された公開」という新パラダイム",
     "[[Anthropic]]の[[Mythos]]と[[OpenAI]]の[[GPT-5.5-Cyber]]が同時期に登場し、いずれも「12パートナー限定」「認定専門家限定」の制限公開を採用した。高性能AIほど公開範囲を絞るという逆説的戦略は、AI企業が「能力の証明」より「__信頼性の構築__」を優先するフェーズに入ったことを示す。一方[[AlphaEvolve]]と[[GPT-5.4]]は内部最適化と収益化で着実に前進しており、「フロントドア制限・バックドア進行」の構造が鮮明だ。"),
    (4,"産業・業界",IT_ACCENT,"コンサルの価値再定義：「情報整理」から「判断」へ",
     "[[McKinsey]]の10%削減は単なるリストラではない。「AIが数分でこなす仕事」のために人を雇う時代の終焉だ。しかし[[AI advisory]]市場は2033年に[[2,570億ドル]]まで拡大する予測がある。コンサルの本質的価値は「__何をすべきか__」という判断にあり、そこに人間の余地がある。Accenture・McKinsey・BCGが各々の方法で「AI時代のコンサル価値」を再定義しようとしている。"),
    (5,"明日へ","#C9B98A","GW明けの三叉路：為替・AI・Switch 2の次の一手",
     "GW中の薄商いが終わり、来週から本格的な市場再開を迎える。[[USD/JPY]]が[[157円台]]を維持できるか（追加介入の有無）、[[Mythos]]や[[GPT-5.5-Cyber]]の展開が次の競争に与える影響、そして[[Switch 2]]が欧米市場でどう反転するかが__5月前半の三大焦点__となる。「守り」の動きが一巡した後、どの領域で「攻め」が再び加速するか——それが問われる週明けだ。"),
]

sections_html = ""
for num, tag, accent_hex, heading, body in sections_data:
    sections_html += mk_section(num, tag, accent_hex, heading, fmtb(body), accent_hex)

# --- Takeaways ---
takeaways_html = ""
for i, (color, tag, text) in enumerate([
    (FX_ACCENT,"為替","日本政府の介入で[[160円]]が当面の天井となったが構造的円安は継続。6月の[[日銀]]追加利上げが真の転換点になるか——[[日米金利差]]縮小なき円高は幻想に過ぎない。"),
    (AI_ACCENT,"AI","[[Mythos]] vs [[GPT-5.5-Cyber]]のサイバーモデル競争は「AI能力の誇示」から「__信頼できる管理展開__」の競争へ移行中。制限公開が業界標準化することで、AI企業の責任論が実装フェーズに入る。"),
    (IT_ACCENT,"産業","[[McKinsey]] 10%削減はコンサル業界の終わりでなく「__価値の再定義__」の始まり。AI advisory市場は爆発的拡大へ——人間コンサルの価値は「情報整理」ではなく「判断と変革」にある。"),
], 1):
    tk_text = re.sub(r'\[\[([^\]]+)\]\]',
        f'<strong style="background:{color};color:#fff;padding:1px 4px;border-radius:2px;">\\1</strong>', text)
    tk_text = re.sub(r'__([^_]+)__',
        r'<span style="border-bottom:2px solid currentColor;">\1</span>', tk_text)
    takeaways_html += f"""<tr><td style="padding-bottom:12px;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#fff;border:1px solid #E2DED4;"><tbody><tr>
    <td width="56" valign="middle" class="m" style="background:{color};color:#fff;text-align:center;font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:18px;font-weight:900;padding:14px 0;">{i:02d}</td>
    <td style="padding:12px 16px;">
      <div class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:9px;color:{color};font-weight:700;letter-spacing:1.5px;margin-bottom:4px;">{tag.upper()}</div>
      <div style="font-size:13px;line-height:1.7;font-weight:600;">{tk_text}</div>
    </td>
  </tr></tbody></table>
</td></tr>"""

# --- Related Issues ---
related_issues_html = ""
for rdate, rtitle in [("2026-05-01","FOMC分裂と為替介入前夜——大型連休の市場圧力"),
                       ("2026-04-30","BOJ 3名反対票と円安の潮目")]:
    related_issues_html += f"""<tr><td style="padding:10px 0;border-bottom:1px solid #E2DED4;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0"><tbody><tr>
    <td width="100" class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:11px;color:#5C5A52;">{rdate}</td>
    <td style="font-size:13px;font-weight:600;">{rtitle}</td>
    <td width="20" align="right" style="color:#5C5A52;">→</td>
  </tr></tbody></table>
</td></tr>"""

# --- Fill template ---
html = TEMPLATE
html = html.replace("{{ISSUE_NO}}", "20260502")
html = html.replace("{{ISSUE_DATE}}", "2026-05-02")
html = html.replace("{{ISSUE_WEEKDAY}}", "土")
html = html.replace("{{TOTAL_CATEGORIES}}", "4")
html = html.replace("{{TOTAL_STORIES}}", "20")
html = html.replace("{{TOTAL_SECTIONS}}", "5")
html = html.replace("{{TOC_ROWS_HTML}}", toc_rows)
html = html.replace("{{CATEGORIES_HTML}}", cats_html)
html = html.replace("{{REFLECTION_TITLE}}", "守りながら攻める")
html = html.replace("{{REFLECTION_SUBTITLE}}", "介入・制限・リストラが映す転換期の世界")
html = html.replace("{{REFLECTION_LEAD_HTML}}", LEAD)
html = html.replace("{{REFLECTION_PULL_QUOTE_HTML}}", PQUOTE)
html = html.replace("{{REFLECTION_SECTIONS_HTML}}", sections_html)
html = html.replace("{{TAKEAWAYS_HTML}}", takeaways_html)
html = html.replace("{{RELATED_ISSUES_HTML}}", related_issues_html)

OUT.write_text(html, encoding="utf-8")
size_kb = OUT.stat().st_size / 1024
print(f"Generated: {OUT}  ({size_kb:.1f} KB)")
