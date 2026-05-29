# -*- coding: utf-8 -*-
"""2026-05-09 News Grasp メール HTML 生成"""
import pathlib, re

ROOT   = pathlib.Path(__file__).parent.parent
TMPL   = ROOT / "prompts" / "email-template.html"
OUTPUT = ROOT / "build" / "email.html"

import re as _re
_raw = TMPL.read_text(encoding="utf-8")
# HTMLコメントを除去（Outlookの条件付きコメント以外）
tmpl = _re.sub(r'<!--(?!\[if)(?!<!\[endif).*?-->', '', _raw, flags=_re.DOTALL)

# ── TOC ──────────────────────────────────────────────────────────────────────
TOC_ROWS = """\
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:6px;"><tbody><tr>
  <td width="32" class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:12px;color:#B8860B;font-weight:700;">¥.</td>
  <td style="font-size:14px;font-weight:700;">為替 (Foreign Exchange)</td>
  <td align="right" class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:11px;color:#5C5A52;">5 stories</td>
</tr></tbody></table>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:6px;"><tbody><tr>
  <td width="32" class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:12px;color:#2D5BB8;font-weight:700;">◆.</td>
  <td style="font-size:14px;font-weight:700;">AI (Artificial Intelligence)</td>
  <td align="right" class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:11px;color:#5C5A52;">5 stories</td>
</tr></tbody></table>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:6px;"><tbody><tr>
  <td width="32" class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:12px;color:#2E6B52;font-weight:700;">▲.</td>
  <td style="font-size:14px;font-weight:700;">IT-Consulting (IT &amp; Consulting)</td>
  <td align="right" class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:11px;color:#5C5A52;">5 stories</td>
</tr></tbody></table>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:6px;"><tbody><tr>
  <td width="32" class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:12px;color:#5E3D8C;font-weight:700;">●.</td>
  <td style="font-size:14px;font-weight:700;">ゲーム (Gaming)</td>
  <td align="right" class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:11px;color:#5C5A52;">5 stories</td>
</tr></tbody></table>"""

# ── カテゴリ共通ヘルパー ────────────────────────────────────────────────────
def cat_header(accent, idx, total, glyph, name_en, name_jp, n_stories, summary):
    return f"""<tr><td style="background:{accent};padding:20px 36px;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0"><tbody><tr>
    <td style="vertical-align:middle;">
      <div class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:10px;color:rgba(255,255,255,0.7);letter-spacing:2px;margin-bottom:4px;">
        CATEGORY {idx} / {total} · {name_en.upper()}
      </div>
      <div style="font-size:28px;font-weight:800;color:#fff;letter-spacing:-0.5px;line-height:1.1;">
        <span style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;margin-right:10px;">{glyph}</span>{name_jp}
      </div>
    </td>
    <td align="right" style="vertical-align:middle;color:rgba(255,255,255,0.85);font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:11px;">{n_stories} stories</td>
  </tr></tbody></table>
  <div style="font-size:13px;color:rgba(255,255,255,0.95);font-style:italic;line-height:1.6;margin-top:10px;padding-top:10px;border-top:1px solid rgba(255,255,255,0.25);">
    {summary}
  </div>
</td></tr>"""

def article_featured(accent, score, title, url, date_str, source, thumb_url, bullets):
    bullets = bullets[:2]  # FEATURED は2バレット上限
    bul_html = "".join(f'<div class="bul" style="padding-left:20px;margin:0 0 8px;font-size:14.5px;line-height:1.9;color:#1A1A1A;">{b}</div>' for b in bullets)
    return f"""<tr><td class="ng-card-pad" style="padding:24px 36px;background:#FAF7F0;border-bottom:1px solid #EDEAE3;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tbody>
    <tr>
      <td>
        <table role="presentation" cellpadding="0" cellspacing="0" border="0"><tbody><tr>
          <td class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;background:{accent};color:#fff;font-size:10px;font-weight:700;padding:3px 8px;letter-spacing:1px;">🔝 TOP</td>
          <td class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:10px;font-weight:700;color:{accent};padding:3px 8px;border:1px solid {accent};">SCORE {score}</td>
          <td class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:10px;color:#5C5A52;padding-left:10px;">{date_str} · {source}</td>
        </tr></tbody></table>
      </td>
    </tr>
    <tr><td style="padding-top:10px;">
      <h2 class="ng-card-title" style="font-size:20px;font-weight:800;line-height:1.4;margin:0 0 12px;letter-spacing:-0.2px;">
        <a href="{url}" style="color:#1A1A1A;text-decoration:none;">{title}</a>
      </h2>
    </td></tr>
    <tr><td class="ng-feature-img" style="padding-bottom:14px;">
      <img src="{thumb_url}" width="568" alt="" style="display:block;width:100%;height:200px;object-fit:cover;border-radius:2px;">
    </td></tr>
    <tr><td>{bul_html}</td></tr>
  </tbody></table>
</td></tr>"""

def article_side(accent, score, title, url, date_str, source, thumb_url, bullets):
    bullets = bullets[:1]  # サイドは1バレット上限
    bul_html = "".join(f'<div class="bul" style="padding-left:20px;margin:0 0 6px;font-size:14px;line-height:1.85;color:#1A1A1A;">{b}</div>' for b in bullets)
    return f"""<tr><td class="ng-card-pad bbcard" style="padding:20px 36px;background:#FAF7F0;border-bottom:1px solid #EDEAE3;">
  <table role="presentation" class="ng-side-table" width="100%" cellpadding="0" cellspacing="0" border="0"><tbody><tr>
    <td class="ng-card-thumb" width="140" valign="top" style="padding-right:16px;vertical-align:top;">
      <img class="ng-card-thumb-img" src="{thumb_url}" width="140" height="90" alt="" style="display:block;width:140px;height:90px;object-fit:cover;border-radius:2px;">
    </td>
    <td class="ng-card-body-cell" valign="top" style="vertical-align:top;">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:8px;"><tbody><tr>
        <td class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:9px;font-weight:700;color:{accent};padding:2px 6px;border:1px solid {accent};">SCORE {score}</td>
        <td class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:9px;color:#5C5A52;padding-left:8px;">{date_str} · {source}</td>
      </tr></tbody></table>
      <div class="ng-card-title" style="font-size:15px;font-weight:700;line-height:1.45;margin-bottom:8px;">
        <a href="{url}" style="color:#1A1A1A;text-decoration:none;">{title}</a>
      </div>
      {bul_html}
    </td>
  </tr></tbody></table>
</td></tr>"""

CDN = "https://raw.githubusercontent.com/HIDEPON-UMG/news-grasp-assets/main"

# ── FX ───────────────────────────────────────────────────────────────────────
fx_html  = cat_header("#B8860B", 1, 4, "¥", "Foreign Exchange", "為替 (Foreign Exchange)", 5,
    "米4月NFPが予想の1.8倍超となる+11.5万人を記録し、ドル円は157円台へ急伸。158円の介入警戒ラインを前に週末クローズとなり、ウォーシュFRB新議長承認後の6月FOMCが次の分水嶺として焦点化している。")
fx_html += article_featured("#B8860B", 95,
    "米4月NFP +11.5万人 予想の1.8倍超 — ドル円157.3円へ急伸、158円防衛ライン巡り週明け介入警戒",
    "https://www.bloomberg.com/jp/news/articles/2026-05-08/TEPXOCGETF5S00",
    "2026-05-08", "Bloomberg Markets",
    f"{CDN}/ng-thumb-fx.jpg",
    ["米4月非農業部門雇用者数は前月比<strong>+11.5万人</strong>と市場予想（+6.5万人）の1.8倍超。失業率4.3%高止まりでFRBの利下げ先送り観測が強まった。",
     "ドル円は発表直後に<strong>157.3円台</strong>へ急伸。日本財務省の<strong>158円防衛ライン</strong>と市場のせめぎ合いが週明けの焦点となる。",
     "前月比+18.5万人から大幅鈍化。移民減少と製造業の雇用喪失トレンドは継続しており、「見かけ上の強さ」との見方もある。"])
fx_html += article_side("#B8860B", 87,
    "USD/JPY 157台で週末クローズ — 介入ゾーン158円前に「買いも売りも入れにくい」均衡",
    "https://www.fxstreet.com/analysis/usd-jpy-price-forecast-struggles-to-lure-buyers-amid-jpy-intervention-fears-us-nfp-eyed-202605080907",
    "2026-05-08", "FXStreet",
    f"{CDN}/ng-thumb-common-fx.jpg",
    ["NFP後のUSD/JPYは<strong>157.2〜157.5円</strong>のレンジで週末入り。158.00円超えで「1.5年ぶり高値圏159円」が視野に入る。",
     "ウォーシュFRB新議長の就任承認後に「タカ派シナリオ再評価」が入る見通し。<strong>6月FOMC前後がドル円の構造転換の分水嶺</strong>。"])
fx_html += article_side("#B8860B", 83,
    "ドル円5月見通し「介入一過、焦点はイラン情勢に」 — 地政学×金利差の複合相場、原油価格連動に注目",
    "https://www.gaitame.com/media/entry/2026/05/07/194754",
    "2026-05-07", "外為どっとコム（外為総研）",
    f"{CDN}/ng-thumb-common-fx.jpg",
    ["GW介入による円急騰は「時間稼ぎ」にとどまりドル円は157台に反発。外為総研は<strong>構造的円安ドライバーが金利差からイラン発の地政学リスクへ移行</strong>と指摘。",
     "イラン情勢が沈静化すれば<strong>円高・原油安の同時進行</strong>シナリオが市場コンセンサスに浮上している。"])
fx_html += article_side("#B8860B", 80,
    "ドル円156円台前半で介入らしき急落を観測（5/7） — NFP発表後のポジション整理と週明けシナリオ",
    "https://www.oanda.jp/lab-education/market_news/2026_05_07_usdjpy/",
    "2026-05-07", "OANDA Japan",
    f"{CDN}/ng-thumb-common-fx.jpg",
    ["5月7日のNY時間にドル円が<strong>157.5→156.1円</strong>へ1.4円の急落。日本財務省関係者発言との時間的一致から<strong>介入らしき動き</strong>とOANDAが判定。",
     "CFTC円ショート残高の45%急減後、海外ヘッジファンドの<strong>再ポジション構築フェーズ</strong>入りが観測される。"])
fx_html += article_side("#B8860B", 76,
    "【海外市場注目点】4月米雇用統計と今週のドル円 — 強い数字でも158円防衛ラインが上値を制限",
    "https://fx.minkabu.jp/news/366577",
    "2026-05-08", "みんかぶFX",
    f"{CDN}/ng-thumb-common-fx.jpg",
    ["強い結果で<strong>158円試し</strong>、弱い結果で155.5円サポートテスト、というシナリオが市場コンセンサス。",
     "EUR/JPYは172台。ECBの次回利下げが9月に後ずれするとの見方が広がり、<strong>クロス円が相対的に強含み</strong>。"])

# ── AI ───────────────────────────────────────────────────────────────────────
ai_html  = cat_header("#2D5BB8", 2, 4, "◆", "Artificial Intelligence", "AI (Artificial Intelligence)", 5,
    "AnthropicのClaude Managed Agentsに「Dreaming」が正式公開。エージェントが過去セッションを自律レビューして自己改善する機能はHarveyで完了率6倍を達成し、AIエージェント進化の新フェーズが幕を開けた。")
ai_html += article_featured("#2D5BB8", 97,
    "Anthropic「Dreaming」正式公開 — エージェントが過去セッションから学習・自己改善、Harvey法律AIで完了率6倍を達成",
    "https://venturebeat.com/technology/anthropic-introduces-dreaming-a-system-that-lets-ai-agents-learn-from-their-own-mistakes",
    "2026-05-07", "VentureBeat",
    f"{CDN}/ng-thumb-ai.jpg",
    ["Dreamingはエージェントが過去セッションを定期バッチで回顧し、<strong>繰り返すミスや有効なワークフローのパターンを自動抽出してメモリを更新</strong>する。",
     "法律AIのHarveyが導入後、タスク完了率が<strong>約6倍に向上</strong>。モデル重みを変更せず、制御粒度はユーザーが設定可能（全自動/承認制）。",
     "<strong>自己教師あり記憶キュレーション</strong>によるエージェント進化が実用段階に達した。AIエージェントの自律進化という新フェーズの幕開け。"])
ai_html += article_side("#2D5BB8", 91,
    "Claude Managed Agentsに3新機能 — Dreaming・Multiplayer・Long Context 500k を Code w/Claude 2026で一挙解放",
    "https://9to5mac.com/2026/05/07/anthropic-updates-claude-managed-agents-with-three-new-features/",
    "2026-05-07", "9to5Mac",
    f"{CDN}/ng-thumb-common-ai.jpg",
    ["<strong>Multiplayer</strong>（複数エージェントの並行協調）と<strong>Long Context 500k</strong>が正式ベータ公開。Code w/Claude 2026で開発者エコシステムを大幅強化。",
     "Long Context 500kはGemini 3.1の1Mトークンには及ばないが<strong>実用的なコスト構造</strong>を維持。Multiplayerでコードレビュー・翻訳・調査の並行処理が可能に。"])
ai_html += article_side("#2D5BB8", 84,
    "AlphaEvolve 詳報 — GoogleのDC電力効率25%改善、NVIDIA依存脱却前夜に「自律最適化」エンジンが稼働",
    "https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-adv",
    "2026-05-06", "Google DeepMind",
    f"{CDN}/ng-thumb-common-ai.jpg",
    ["AlphaEvolveはGeminiベースのコーディングエージェントが自社データセンターを自律最適化。<strong>電力効率25%改善・コンピュート割当の無駄を0.7%削減</strong>を達成。",
     "AlphaEvolveが生成したアルゴリズムはTPU設計や行列乗算最適化にまで拡張。<strong>数十年かかっていた問題を数日で解く事例が続出</strong>。"])
ai_html += article_side("#2D5BB8", 82,
    "Google・Microsoft・xAIが米政府のAI事前評価プログラムに参加合意 — 「安全性テスト」を主流モデルの義務へ",
    "https://www.cnn.com/2026/05/05/tech/microsoft-google-xai-government-test-ai-models",
    "2026-05-05", "CNN Business",
    f"{CDN}/ng-thumb-common-ai.jpg",
    ["米NIST傘下のCAISIがGoogle・Microsoft・xAIと事前評価協定を締結。モデル公開前の<strong>サイバー攻撃・バイオ・化学兵器リスクのスクリーニング</strong>が義務化。",
     "AnthropicとOpenAIは未参加。<strong>「安全性証明書制度」の分断</strong>が加速している。"])
ai_html += article_side("#2D5BB8", 78,
    "GoogleとAmazonのAI利益の半分は「Anthropic株式」評価益 — 実体収益との乖離が生む「AI決算バブル」論",
    "https://fortune.com/2026/04/30/google-amazon-ai-profits-anthropic-stake-bubble-earnings-2026/",
    "2026-04-30", "Fortune",
    f"{CDN}/ng-thumb-common-ai.jpg",
    ["Q1 2026のAlphabet・Amazon決算で、営業外利益の大半を<strong>Anthropic株式の評価益</strong>が占めることが判明。",
     "Anthropicは<strong>評価額9,000億ドル</strong>への資金調達交渉中。次四半期も「AI利益急増」が演出される構造になっている。"])

# ── IT-Consulting ─────────────────────────────────────────────────────────────
it_html  = cat_header("#2E6B52", 3, 4, "▲", "IT-Consulting", "IT-Consulting (IT &amp; Consulting)", 5,
    "NTTデータGが「コンサルティング×AI」を軸とした大規模組織改編を発表し、日本のSI老舗の戦略コンサル化が加速。Accenture×ServiceNow FDEプログラムとIBM Think 2026発表が重なり、エンタープライズAIの「PoC地獄」脱出競争が激化している。")
it_html += article_featured("#2E6B52", 95,
    "NTTデータグループ 大規模組織改編発表 — 「コンサルティング×AI」中核に新セグメント新設・グローバルAIユニット設立",
    "https://www.nttdata.com/global/ja/news/release/2026/050807/",
    "2026-05-08", "NTTデータグループ",
    f"{CDN}/ng-thumb-it.jpg",
    ["NTTデータGは既存の公共・金融・法人セグメントを横断する<strong>コンサルティングセグメント</strong>を新設。「構想から提言・実装・成果創出を一気通貫」するAI変革推進体制に転換。",
     "海外子会社NTT DATA, Inc.に<strong>グローバルAIユニット</strong>を新設。「NTT DATA AIVista」コアAIプラットフォームを2026年度Q2より提供開始。",
     "「コンサルティング×AI」という旗印は、アクセンチュアやBCGとの直接対決を意味する。<strong>国内SI老舗の「戦略コンサル化」宣言</strong>は業界再編の号砲。"])
it_html += article_side("#2E6B52", 88,
    "Accenture×ServiceNow FDE Program — 企業AIを「パイロット→本番」へ橋渡し、300プリビルドスキル＋AI Control Tower",
    "https://newsroom.accenture.com/news/2026/servicenow-and-accenture-launch-forward-deployed-engineering-program-to-scale-agentic-ai-across-the-enterprise",
    "2026-05-06", "Accenture Newsroom",
    f"{CDN}/ng-thumb-common-it.jpg",
    ["Forward Deployed EngineeringプログラムでServiceNow・Accenture両社のエンジニアが顧客環境に常駐。300以上のプリビルドAIスキルで<strong>PoC停滞を解消して本番稼働を加速</strong>。",
     "中枢の<strong>AI Control Tower</strong>がエージェントのパフォーマンス・セキュリティ・スケールを一元統制。Accenture FY2026 Q1のAI案件受注は22億ドルと前年比倍増。"])
it_html += article_side("#2E6B52", 84,
    "NTT DATA AIVista、2026年度Q2より提供開始 — シリコンバレー新会社が本格始動、AIエージェント関連3,000億円を狙う",
    "https://enterprisezine.jp/news/detail/23340",
    "2026-05-07", "EnterpriseZine",
    f"{CDN}/ng-thumb-common-it.jpg",
    ["AIVistaは<strong>LLM不可知論的設計</strong>で、Claude・GPT・Geminiのいずれでも稼働可能。特定AIベンダーへの依存を排除し、コンサルのニュートラル性を売り物にする。",
     "2027年度にAIエージェント関連で<strong>売上3,000億円</strong>という目標。グローバル2,000件超のLITRON Builder受注がベースラインとなっている。"])
it_html += article_side("#2E6B52", 79,
    "IBM Think 2026 全発表まとめ — Context Studio・Process Studio・量子AI統合、Enterprise Advantageで「アセット基盤型」を標榜",
    "https://businessdailynetwork.com/stories/681788650-ibm-announces-new-ai-consulting-capabilities-and-partnerships-at-think-2026",
    "2026-05-06", "Business Daily Network",
    f"{CDN}/ng-thumb-common-it.jpg",
    ["Think 2026でIBMが<strong>Enterprise Advantage</strong>を発表。Context Studio（エージェントを組織データに根拠付け）とProcess Studio（レガシー業務手順書のエージェント化）の2ツール。",
     "医療大手Providenceで採用工数<strong>90%削減</strong>・内部異動12日短縮を達成。量子×watsonx連携で2027年に実用量子優位性達成を宣言。"])
it_html += article_side("#2E6B52", 77,
    "IT大手4社、生成AI適用「本腰」 — NTTデータG「2027年度に開発工程40%効率化」の具体工程が明らかに",
    "https://xtech.nikkei.com/atcl/nxt/column/18/00001/11238",
    "2026-05-06", "日経クロステック",
    f"{CDN}/ng-thumb-common-it.jpg",
    ["NTTデータGは2027年度末までに生成AIネーティブ開発を全プロジェクトの主流にする計画。要件定義〜テストの各工程に生成AIを組み込み、<strong>人による作業を約40%削減</strong>する。",
     "富士通はKozuchi AIアシスタント、NECは「cotomi Act」でナレッジエージェントが稼働中。SIerの真の目的は<strong>「人月ビジネスを固定価格・アウトカム報酬型へ転換」</strong>にある。"])

# ── Game ─────────────────────────────────────────────────────────────────────
gm_html  = cat_header("#5E3D8C", 4, 4, "●", "Gaming", "ゲーム (Gaming)", 5,
    "任天堂FY2026通期決算でSwitch 2累計1,986万台・売上2.3兆円の好結果が出た一方、5月25日から1万円値上げを発表しFY2027は減益見通しと正直な計画を開示。コナミGも過去最高益を達成し、ゲーム業界「決算週」が最高の形で着地した。")
gm_html += article_featured("#5E3D8C", 96,
    "任天堂FY2026通期決算 — Switch 2累計1,986万台・売上2.3兆円・5月25日に1万円値上げ",
    "https://news.denfaminicogamer.jp/news/2605082s",
    "2026-05-08", "電ファミニコゲーマー",
    f"{CDN}/ng-thumb-game.jpg",
    ["任天堂FY2026通期は<strong>売上高2兆3,136億円（前期比+98.6%）</strong>・純利益4,240億円（+52.1%）と大幅改善。Switch 2が初年度で<strong>累計1,986万台</strong>を達成。",
     "主要ソフト：マリカワールド14.7万本・ドンキーコング4.52万本。Switch 2ソフト合計<strong>4,871万本</strong>と旺盛なソフト付着率を記録。",
     "5月25日より日本語専用版を<strong>49,980→59,980円</strong>（+10,000円）に値上げ。FY2027は販売台数16.5万台予測・<strong>最終減益見通し</strong>に転じており「量から質」への転換期に入った。"])
gm_html += article_side("#5E3D8C", 91,
    "任天堂FY2027は減益見通し — Switch 2値上げで販売台数減を想定、「逆説的成長」戦略の読み方",
    "https://www.nikkei.com/article/DGXZQOUF075130X00C26A5000000/",
    "2026-05-08", "日本経済新聞",
    f"{CDN}/ng-thumb-common-game.jpg",
    ["任天堂FY2027の会社計画：Switch 2を<strong>16.5万台（前期比-17%）</strong>に抑えながら、単価上昇で収益水準を維持する「価格弾力性重視」戦略。",
     "値上げは<strong>米国は499.99ドル・欧州は499.99ユーロ（9月）</strong>へ段階拡大。アナリストは「ソフトの粘着性が高く棚落ちリスクは低い」と評価。"])
gm_html += article_side("#5E3D8C", 88,
    "コナミG FY2026通期決算 — 事業利益31.6%増・1,435億円の過去最高、MGSΔ200万本・3期連続最高益を達成",
    "https://gamebiz.jp/news/425576",
    "2026-05-08", "GameBiz.jp",
    f"{CDN}/ng-thumb-common-game.jpg",
    ["コナミGFY2026：売上高4,937億円（+17.1%）・事業利益<strong>1,435億円（+31.6%）</strong>・純利益1,000億円（+33.9%）と全指標で過去最高を更新。",
     "<strong>METAL GEAR SOLID Δ: SNAKE EATER</strong>（世界累計200万本）とeFootballが牽引。esportsフランチャイズ価値を大幅強化した。"])
gm_html += article_side("#5E3D8C", 83,
    "ソニーグループFY2026 営業益1.4475兆円（+13%）過去最高 — PlayStation部門も増益、FY2027は1.6兆円計画",
    "https://gamebiz.jp/news/425542",
    "2026-05-08", "GameBiz.jp",
    f"{CDN}/ng-thumb-common-game.jpg",
    ["ソニーGFY2026：売上高12.4兆円（+3.7%）・<strong>営業益1.4475兆円（+13.4%）</strong>と2期連続最高益。PlayStation Plus会員増で増益を確保。",
     "FY2027計画では<strong>自己株取得5,000億円</strong>を同時発表。PlayStation IPのクロスメディア展開が今後の成長エンジンとして注目される。"])
gm_html += article_side("#5E3D8C", 79,
    "【決算分析】Switch 2爆発ヒットで任天堂「V字復活」 — 営業利益3,601億円・値上げ後の「逆張り成長」シナリオ",
    "https://www.today-jp.com/news/nintendo-switch-2-earnings-report-fy2026-profit-surge",
    "2026-05-08", "Today Japan News",
    f"{CDN}/ng-thumb-common-game.jpg",
    ["任天堂の営業利益<strong>3,601億円（+27.5%）</strong>。FY2025（Switch販売不振）から半分以下への落ち込みを経た「V字復活」。Switch 2が<strong>ゲーム機史上2位の初年度出荷規模</strong>。",
     "値上げ（5/25）後のFY2027計画で<strong>減益見通しを宣言した「透明経営」</strong>が投資家の信頼を高めた。次世代ソフトのパイプラインが豊富で夏商戦に向け強固な布陣。"])

CATEGORIES_HTML = fx_html + ai_html + it_html + gm_html

# ── Reflection ───────────────────────────────────────────────────────────────
REFLECTION_SECTIONS = """\
<tr><td class="ng-section-pad" style="background:#FAF7F0;padding:0 36px;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-bottom:1px dashed #E2DED4;"><tbody><tr>
    <td class="ng-section-num-cell" width="80" valign="top" style="padding:28px 16px 28px 0;">
      <div class="m ng-section-num" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:38px;font-weight:900;color:#1A1A1A;line-height:0.9;letter-spacing:-2px;">§01</div>
      <div class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:9px;color:#fff;background:#1A1A1A;padding:2px 6px;display:inline-block;letter-spacing:1.5px;margin-top:8px;">総論</div>
    </td>
    <td class="ng-section-text-cell" valign="top" style="padding:28px 0 28px 20px;border-left:1px solid #E2DED4;">
      <h3 class="ng-section-heading" style="font-size:18px;font-weight:800;margin:0 0 14px;">「成果から学ぶ」が今日の共通軸</h3>
      <div class="ng-section-body" style="font-size:13.5px;line-height:2.0;">
        本日の4分野を俯瞰すると、<strong>AIエージェントの自己進化</strong>と<strong>ゲーム業界の正直な減益宣言</strong>という一見無関係な2つのニュースが、同じ「過去の成果から学び、持続的成長に転換する」という経営哲学を体現していることに気づく。任天堂が最高益の中で減益見通しを宣言し、AnthropicがDreamingで「失敗から学ぶ」エージェントを解放した日は、テクノロジー産業全体が「<strong>規模から質へ</strong>」転換する象徴的な一日となった。
      </div>
    </td>
  </tr></tbody></table>
</td></tr>
<tr><td class="ng-section-pad" style="background:#FAF7F0;padding:0 36px;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-bottom:1px dashed #E2DED4;"><tbody><tr>
    <td class="ng-section-num-cell" width="80" valign="top" style="padding:28px 16px 28px 0;">
      <div class="m ng-section-num" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:38px;font-weight:900;color:#B8860B;line-height:0.9;letter-spacing:-2px;">§02</div>
      <div class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:9px;color:#fff;background:#B8860B;padding:2px 6px;display:inline-block;letter-spacing:1.5px;margin-top:8px;">為替・経済</div>
    </td>
    <td class="ng-section-text-cell" valign="top" style="padding:28px 0 28px 20px;border-left:1px solid #E2DED4;">
      <h3 class="ng-section-heading" style="font-size:18px;font-weight:800;margin:0 0 14px;">NFP後の157円急伸と介入警戒継続</h3>
      <div class="ng-section-body" style="font-size:13.5px;line-height:2.0;">
        米4月NFPが予想+6.5万人に対し<strong>+11.5万人</strong>という強い結果で着地。ドル円は157.3円台へ急伸したが、日本財務省の<strong>158円防衛ライン</strong>が上値を抑え、週末は157台クローズとなった。GW介入で45%急減した円ショート残高の再構築がどこで始まるか、<strong>ウォーシュ新議長の就任後・6月FOMCがタイムラインの決め手</strong>になる。強い雇用統計が利下げ遠のきを意味する「逆説的ドル高」の構図が改めて鮮明となった。
      </div>
    </td>
  </tr></tbody></table>
</td></tr>
<tr><td class="ng-section-pad" style="background:#FAF7F0;padding:0 36px;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-bottom:1px dashed #E2DED4;"><tbody><tr>
    <td class="ng-section-num-cell" width="80" valign="top" style="padding:28px 16px 28px 0;">
      <div class="m ng-section-num" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:38px;font-weight:900;color:#2D5BB8;line-height:0.9;letter-spacing:-2px;">§03</div>
      <div class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:9px;color:#fff;background:#2D5BB8;padding:2px 6px;display:inline-block;letter-spacing:1.5px;margin-top:8px;">AI・技術</div>
    </td>
    <td class="ng-section-text-cell" valign="top" style="padding:28px 0 28px 20px;border-left:1px solid #E2DED4;">
      <h3 class="ng-section-heading" style="font-size:18px;font-weight:800;margin:0 0 14px;">Dreamingの衝撃：AIが「失敗から自律学習」する時代の幕開け</h3>
      <div class="ng-section-body" style="font-size:13.5px;line-height:2.0;">
        AnthropicのDreaming機能は、<strong>モデルの重みを変えずにエージェントが自己改善できる</strong>という点で画期的だ。Harveyでの6倍完了率という実績は「試験管内の成功」ではなく本番環境での証明である。並行してGoogle DeepMindがAlphaEvolveのDC効率<strong>25%改善</strong>詳報を公開。AI自律最適化の「実力」が次々に可視化されてきた。一方、Google・Microsoft・xAIが米政府のモデル安全審査に合意したことで、AIの「<strong>規制と普及</strong>」の両輪が同時に進む局面が始まった。
      </div>
    </td>
  </tr></tbody></table>
</td></tr>
<tr><td class="ng-section-pad" style="background:#FAF7F0;padding:0 36px;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-bottom:1px dashed #E2DED4;"><tbody><tr>
    <td class="ng-section-num-cell" width="80" valign="top" style="padding:28px 16px 28px 0;">
      <div class="m ng-section-num" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:38px;font-weight:900;color:#2E6B52;line-height:0.9;letter-spacing:-2px;">§04</div>
      <div class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:9px;color:#fff;background:#2E6B52;padding:2px 6px;display:inline-block;letter-spacing:1.5px;margin-top:8px;">産業・業界</div>
    </td>
    <td class="ng-section-text-cell" valign="top" style="padding:28px 0 28px 20px;border-left:1px solid #E2DED4;">
      <h3 class="ng-section-heading" style="font-size:18px;font-weight:800;margin:0 0 14px;">日本IT老舗の「コンサル×AI」宣言とゲーム最高益の相乗り</h3>
      <div class="ng-section-body" style="font-size:13.5px;line-height:2.0;">
        NTTデータGの「コンサルティングセグメント新設」は、日本のSI業界が人月型ビジネスから脱却しようとする宣言である。同日にIBMとAccentureが欧米市場で類似の動きを見せており、<strong>グローバルIT産業の「SI→コンサル→アウトカム報酬型」への大移行</strong>が同時多発している。ゲーム業界では任天堂・コナミ・ソニーが揃い踏みで最高益を発表し、Switch 2効果が業界全体を底上げした。任天堂の値上げ宣言は「<strong>アップルがiPhoneを高単価で展開する戦略</strong>」との類似を呼ぶ。
      </div>
    </td>
  </tr></tbody></table>
</td></tr>
<tr><td class="ng-section-pad" style="background:#FAF7F0;padding:0 36px;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-bottom:1px dashed #E2DED4;"><tbody><tr>
    <td class="ng-section-num-cell" width="80" valign="top" style="padding:28px 16px 28px 0;">
      <div class="m ng-section-num" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:38px;font-weight:900;color:#C9B98A;line-height:0.9;letter-spacing:-2px;">§05</div>
      <div class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:9px;color:#1A1A1A;background:#C9B98A;padding:2px 6px;display:inline-block;letter-spacing:1.5px;margin-top:8px;">明日へ</div>
    </td>
    <td class="ng-section-text-cell" valign="top" style="padding:28px 0 28px 20px;border-left:1px solid #E2DED4;">
      <h3 class="ng-section-heading" style="font-size:18px;font-weight:800;margin:0 0 14px;">ウォーシュ議長承認と週明けドル円・任天堂株の動向</h3>
      <div class="ng-section-body" style="font-size:13.5px;line-height:2.0;">
        週明けの最大イベントはウォーシュFRB新議長の上院承認（5/11週）。承認後の<strong>6月FOMC</strong>でタカ派シグナルが出ればドル円は再び158円を試しにいく展開となる。任天堂株（7974）は値上げ発表後の評価が割れており、<strong>「長期は強気・短期は慎重」</strong>というコンセンサスが形成されつつある。Dreamingの普及が始まれば、次の決算ターンでのエンタープライズAI導入案件数増が焦点となる。
      </div>
    </td>
  </tr></tbody></table>
</td></tr>"""

TAKEAWAYS = """\
<tr><td style="padding-bottom:12px;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#fff;border:1px solid #E2DED4;"><tbody><tr>
    <td width="56" valign="middle" class="m" style="background:#B8860B;color:#fff;text-align:center;font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:18px;font-weight:900;padding:14px 0;">01</td>
    <td style="padding:12px 16px;">
      <div class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:9px;color:#B8860B;font-weight:700;letter-spacing:1.5px;margin-bottom:4px;">為替</div>
      <div style="font-size:13px;line-height:1.7;font-weight:600;">ドル円は157台で週末入りも、<strong>158円防衛ライン</strong>が上値を抑制。ウォーシュ新議長就任後の6月FOMC前後が構造転換の分水嶺。</div>
    </td>
  </tr></tbody></table>
</td></tr>
<tr><td style="padding-bottom:12px;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#fff;border:1px solid #E2DED4;"><tbody><tr>
    <td width="56" valign="middle" class="m" style="background:#2D5BB8;color:#fff;text-align:center;font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:18px;font-weight:900;padding:14px 0;">02</td>
    <td style="padding:12px 16px;">
      <div class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:9px;color:#2D5BB8;font-weight:700;letter-spacing:1.5px;margin-bottom:4px;">AI</div>
      <div style="font-size:13px;line-height:1.7;font-weight:600;"><strong>Dreaming</strong>はAIエージェントが人間の監督なしに過去の失敗から学ぶ初の実用システム。Harvey社での6倍完了率が「投資対効果」を証明した。</div>
    </td>
  </tr></tbody></table>
</td></tr>
<tr><td style="padding-bottom:12px;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#fff;border:1px solid #E2DED4;"><tbody><tr>
    <td width="56" valign="middle" class="m" style="background:#2E6B52;color:#fff;text-align:center;font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:18px;font-weight:900;padding:14px 0;">03</td>
    <td style="padding:12px 16px;">
      <div class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:9px;color:#2E6B52;font-weight:700;letter-spacing:1.5px;margin-bottom:4px;">産業</div>
      <div style="font-size:13px;line-height:1.7;font-weight:600;">NTTデータの「コンサルティング×AI」組織改編と任天堂の値上げ宣言は、<strong>「規模より収益率」</strong>という2026年の産業テーマを同日に体現した。</div>
    </td>
  </tr></tbody></table>
</td></tr>"""

RELATED_ISSUES = """\
<tr><td style="padding:10px 0;border-bottom:1px solid #E2DED4;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0"><tbody><tr>
    <td width="100" class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:11px;color:#5C5A52;">2026-05-08</td>
    <td style="font-size:13px;font-weight:600;">Switch 2値上げ発表前夜・FRB前夜・Anthropic SpaceX協定</td>
    <td width="20" align="right" style="color:#5C5A52;">→</td>
  </tr></tbody></table>
</td></tr>
<tr><td style="padding:10px 0;border-bottom:1px solid #E2DED4;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0"><tbody><tr>
    <td width="100" class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:11px;color:#5C5A52;">2026-05-07</td>
    <td style="font-size:13px;font-weight:600;">ペンタゴンAI排除・CFTC円ショート急減・カプコン決算</td>
    <td width="20" align="right" style="color:#5C5A52;">→</td>
  </tr></tbody></table>
</td></tr>"""

# ── プレースホルダー置換 ────────────────────────────────────────────────────
replacements = {
    "{{ISSUE_NO}}":                  "20260509",
    "{{ISSUE_DATE}}":                "2026-05-09",
    "{{ISSUE_WEEKDAY}}":             "土",
    "{{TOTAL_CATEGORIES}}":          "4",
    "{{TOTAL_STORIES}}":             "20",
    "{{TOTAL_SECTIONS}}":            "5",
    "{{TOC_ROWS_HTML}}":             TOC_ROWS,
    "{{CATEGORIES_HTML}}":           CATEGORIES_HTML,
    "{{REFLECTION_TITLE}}":          "過去から学び未来を値踏む一日",
    "{{REFLECTION_SUBTITLE}}":       "AIエージェントの「Dreaming自己進化」とゲーム決算の「最高益でも減益宣言」が示した誠実な前進",
    "{{REFLECTION_LEAD_HTML}}":      "本日4分野・20本のニュースから浮かび上がる最大のテーマは<strong>AIエージェントの自己進化</strong>と<strong>ゲーム業界の価値転換</strong>の同時進行である。AnthropicがDreamingで「失敗から学ぶ」エージェントを解放した同じ日に、任天堂が最高益の中で減益見通しを宣言するという「誠実な前進」を見せた。これはテクノロジー産業全体が「<strong>規模から質へ</strong>」転換する象徴的な一日だ。",
    "{{REFLECTION_PULL_QUOTE_HTML}}": "「最高益を叩き出しながら減益見通しを宣言する」——任天堂の<span style='border-bottom:2px solid #8E2A19;'>透明経営</span>は、数字の裏にある戦略の誠実さを示した。",
    "{{REFLECTION_SECTIONS_HTML}}":  REFLECTION_SECTIONS,
    "{{TAKEAWAYS_HTML}}":            TAKEAWAYS,
    "{{RELATED_ISSUES_HTML}}":       RELATED_ISSUES,
}

html = tmpl
for placeholder, value in replacements.items():
    html = html.replace(placeholder, value)

OUTPUT.write_text(html, encoding="utf-8")
print(f"Generated: {OUTPUT}")
size_kb = OUTPUT.stat().st_size / 1024
print(f"File size: {size_kb:.1f} KB")
if size_kb > 102:
    print("WARNING: File size exceeds Gmail 102KB clipping threshold!")
