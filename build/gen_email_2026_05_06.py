"""generate build/email.html for 2026-05-06 digest (FX, AI, IT-Consulting, Economy)"""
import re, pathlib

ROOT = pathlib.Path(__file__).parent.parent
TMPL = (ROOT / "prompts" / "email-template.html").read_text(encoding="utf-8")
OUT  = ROOT / "build" / "email.html"

FX_ACC  = "#B8860B"
AI_ACC  = "#2D5BB8"
IT_ACC  = "#2E6B52"
EC_ACC  = "#8E2A19"
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

def toc_row(glyph, acc, name, n):
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:8px;"><tbody><tr>'
        f'<td width="32" class="m" style="font-family:\'JetBrains Mono\',Consolas,\'Courier New\',monospace;font-size:14px;color:{acc};font-weight:700;">{glyph}</td>'
        f'<td style="font-size:14px;font-weight:700;">{name}</td>'
        f'<td align="right" class="m" style="font-family:\'JetBrains Mono\',Consolas,\'Courier New\',monospace;font-size:11px;color:#5C5A52;">{n} stories</td>'
        f'</tr></tbody></table>'
    )

# ── TOC ──────────────────────────────────────────────────────────────────────
TOC = (
    toc_row("¥", FX_ACC, "為替 (Foreign Exchange)", 5) +
    toc_row("◆", AI_ACC, "AI (Artificial Intelligence)", 5) +
    toc_row("▲", IT_ACC, "IT-Consulting (IT &amp; Consulting)", 5) +
    toc_row("■", EC_ACC, "経済 (Economy)", 5)
)

PH_FX     = f"{CDN}/ng-thumb-fx.jpg"
PH_FX_C   = f"{CDN}/ng-thumb-common-fx.jpg"
PH_AI     = f"{CDN}/ng-thumb-ai.jpg"
PH_AI_C   = f"{CDN}/ng-thumb-common-ai.jpg"
PH_IT     = f"{CDN}/ng-thumb-it.jpg"
PH_IT_C   = f"{CDN}/ng-thumb-common-it.jpg"
PH_EC     = f"{CDN}/ng-thumb-economy.jpg"
PH_EC_C   = f"{CDN}/ng-thumb-common-economy.jpg"

# ── CATEGORIES HTML ───────────────────────────────────────────────────────────
CATS = ""

# ─ FX (1/4) ──────────────────────────────────────────────────────────────────
CATS += cat_header(1, 4, "¥", "FOREIGN EXCHANGE", "為替", FX_ACC, 5,
    "GW明け最初の週、ドル円は157円台で膠着。4/30の約5.4兆円介入効果は1週間で半減しつつあり、今週の焦点は5月9日（金）の米雇用統計。非農業部門5.9万人・失業率4.5%予想を上回れば158円台トライ、下回れば利下げ観測で円高という二択が迫る。")

CATS += featured_card(92, "2026-05-06 07:30", "IG Markets Japan",
    "https://www.ig.com/jp/news-and-trade-ideas/jpy-may-see-easing-depreciation-pressure-if-boj-shows-hawkish-st-251226",
    "5/9 米雇用統計が今週の分水嶺 — ドル円158円試しか再介入か",
    PH_FX,
    [
        "今週のドル円の地合いは[[157円台]]で強さを維持しており、焦点は[[5月9日（金）の米雇用統計]]。非農業部門雇用者数の市場予想は5.9万人、失業率は4.5%で、予想を上回れば__2024年11月来のレジスタンス158.00円トライ__ が現実になる。",
        "日銀が4月会合で政策金利を[[0.75%]]に据え置いた（賛成6・反対3）一方、[[FRBのパウエル議長の任期が5月に終了]]。新議長就任後の利下げ観測が1〜2回浮上しており、日米金利差の縮小シナリオが円高圧力を高める可能性がある。",
        "GW明けの市場では「介入の残存効果」と「ドル需給の回復」が拮抗。__週初のポジション調整__ がドル円の方向感を決めるカギとなり、158円上抜けなら160円台再試験、157円割れなら介入ラインを巡る攻防再燃が予想される。",
    ], FX_ACC)

CATS += side_card(88, "2026-05-04 08:15", "日経新聞",
    "https://www.nikkei.com/article/DGXZQOUB040IZ0U6A500C2000000/",
    "GW明け追加介入警戒くすぶる — ドル円157円台で推移、ヘッジファンドの円ショートに目線",
    PH_FX_C,
    [
        "5月4日の外国為替市場で対ドルの円相場が一時[[155円台後半]]に急上昇。政府・日銀が4月30日に実施した[[円買い・ドル売り介入]]から1週間が経過した現在も、市場では追加介入への警戒が続いている。",
        "ヘッジファンドのポジションデータでは、円の買い持ちへの急転換が観察されておらず、__構造的な円売り圧力__ は解消されていない。[[160円の心理的節目]]がトリガーとして機能し続ける。",
    ], FX_ACC,
    related_tip("復状", "https://github.com/HIDEPON-UMG/News-Grasp/blob/main/digest/FX/2026-05-05-FX.md",
                "5/5号: 日本政府 345億ドル介入"))

CATS += side_card(84, "2026-05-06 09:00", "Trading Economics / BOJ",
    "https://tradingeconomics.com/japan/interest-rate",
    "BOJ 0.75%据え置き（6対3）・FRB新議長交代 — 日米政策格差縮小シナリオが浮上",
    PH_FX_C,
    [
        "日銀は4月の政策決定会合で政策金利を[[0.75%]]に据え置き（6対3の投票）。利上げを主張した少数派の存在は、[[夏以降の追加利上げ]]シグナルとして市場が読み取っており、植田総裁の「行間」に注目が集まる。",
        "一方、FRBは[[パウエル議長の任期が5月に終了]]。トランプ政権が推薦する新議長候補は「利下げ優先」の立場とされ、年内1〜2回の利下げが現実味を帯びる。__日米の政策方向が逆行__ するシナリオが円高圧力を高める。",
    ], FX_ACC)

CATS += side_card(79, "2026-05-06 10:00", "Forex.com",
    "https://www.forex.com/en-uk/news-and-analysis/euro-price-action-setups-into-fomc-eur-usd-eur-cad-eur-jpy/",
    "EUR/JPY 186円台がキーサポート — ECBとBOJの政策格差縮小でクロス円に変化",
    PH_FX_C,
    [
        "[[EUR/JPY]]は186円台で推移し、日足チャートではこの水準がキーサポートとして意識されている。ECBは段階的な利下げ局面に入っており、ECB・BOJ双方の緩和解除が進む中で、ユーロ高・円高がともに進む「クロス円下落」シナリオが現実味を持つ。",
        "__192円の1990年来高値__ への再試験には強いドル安材料が必要。GBP/JPY・AUD/JPYも同様に介入後の修正が続く。",
    ], FX_ACC)

CATS += side_card(74, "2026-05-01 09:30", "野村総合研究所（NRI）",
    "https://www.nri.com/jp/media/column/kiuchi/20260501.html",
    "構造的円安の持続性を問う — NRI木内：為替介入は「時間稼ぎ」に過ぎない",
    PH_FX_C,
    [
        "NRI・木内登英エグゼクティブ・エコノミストは「大型連休はざまの為替介入は[[時間稼ぎ]]の政策に過ぎない」と指摘。介入規模は5.4兆円と推計されるが、FXスポット市場の1日あたり売買高は数十兆円規模で、__投機的な円売りポジション__ の巻き戻しには不十分。",
        "木内氏は「円安の本質は[[日本の相対的成長力低下]]にある」と指摘。__政策の限界__ を正面から論じ、産業競争力の回復なしに為替の根本是正は難しいと警鐘を鳴らす。",
    ], FX_ACC,
    related_tip("復状", "https://github.com/HIDEPON-UMG/News-Grasp/blob/main/digest/FX/2026-05-01-FX.md",
                "5/1号: 4月30日介入規模5.4兆円"))

# ─ AI (2/4) ──────────────────────────────────────────────────────────────────
CATS += cat_header(2, 4, "◆", "ARTIFICIAL INTELLIGENCE", "AI", AI_ACC, 5,
    "AnthropicがMoody's・Microsoft 365統合でウォール街への深浸透を加速。Pentagon排除に対し提訴で対抗し、「安全性を武器にした商業モデル」が確立しつつある。Google DeepMindはAlphaEvolveで計算資源の自律最適化を実証し、OpenAIはAWS Bedrock統合でクラウド三社展開を完了した。")

CATS += featured_card(95, "2026-05-05 06:30", "Fortune",
    "https://fortune.com/2026/05/05/anthropic-wall-street-financial-services-agents-jamie-dimon/",
    "Anthropic、ウォール街深浸透を加速 — Moody's提携・Microsoft 365統合・AIエージェント展開",
    PH_AI,
    [
        "Anthropicが[[ウォール街]]への浸透をさらに深め、信用調査大手[[Moody's]]とのデータ提携、[[Microsoft 365]]フル統合（Word・Excel・Outlook上で直接Claude動作）を発表。金融機関の業務フローへの深い組み込みを実現した。",
        "JPMorganのジェイミー・ダイモンCEOが「Claude APIで社内ワークフローを再設計する」と明言。[[Blackstone・Goldman Sachs・H&F との15億ドル合弁JV]] に続く、エンタープライズ戦略の第二波として注目される。",
        "PentagonからAI安全性基準を理由に排除される一方、金融・法律・医療分野で実装を加速。__「安全性基準を武器に」__ 軍事利用を断りながら収益モデルを確立するというOpenAIとは異なる価値観ビジネスが具体的な形をとり始めた。",
    ], AI_ACC)

CATS += side_card(90, "2026-05-04 07:00", "Defense News",
    "https://www.defensenews.com/news/pentagon-congress/2026/05/01/pentagon-freezes-out-anthropic-as-it-signs-deals-with-ai-rivals/",
    "Pentagon排除のAnthropicが提訴 — 自律兵器条項拒否・供給連鎖リスク指定に異議",
    PH_AI_C,
    [
        "国防総省が[[OpenAI]]・[[Google]]・Microsoft・Amazon・Oracle・[[NVIDIA]]・SpaceX・Reflection AIの8社とAI調達契約を締結し、[[Anthropic]]を「供給連鎖リスク」として除外。Anthropicは連邦裁判所に提訴し排除阻止の仮処分を申請中。",
        "除外の背景は「すべての合法的目的」条項。国防総省が[[自律兵器・大規模監視]]への使用を含む広義の利用を要求したが、Anthropicが安全性ポリシーを理由に拒否。__「誰がAIのルールを決めるか」__ というガバナンスの核心問題を先鋭化させている。",
    ], AI_ACC,
    related_tip("復状", "https://github.com/HIDEPON-UMG/News-Grasp/blob/main/digest/AI/2026-05-05-AI.md",
                "5/5号: 米国防総省8社AI契約"))

CATS += side_card(85, "2026-05-06 08:00", "Google DeepMind",
    "https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/",
    "AlphaEvolve：Gemini搭載コーディングエージェントがGoogle計算資源を自律最適化",
    PH_AI_C,
    [
        "DeepMindが発表した[[AlphaEvolve]]は、Gemini駆動の進化的コーディングエージェント。[[Google全体のコンピューティングリソースの0.7%]]を自律的に回収しており、Gemini自体の重要カーネルを[[23%高速化]]してGemini訓練時間を1%削減した。",
        "行列乗算アルゴリズムの発見、データセンターの最適化、チップ設計の効率化など、科学・工学的問題を自律的に解く能力を示す。__Googleが計算資源規模を武器に差別化__ している点が注目される。",
    ], AI_ACC)

CATS += side_card(82, "2026-05-06 07:30", "OpenAI",
    "https://openai.com/index/introducing-gpt-5-5/",
    "OpenAI GPT-5.5 が AWS Bedrock に上陸 — クラウド三社展開完了でエンタープライズ席巻",
    PH_AI_C,
    [
        "OpenAIが[[GPT-5.5]]（GPT-5.4から6週間後にリリース）を[[Amazon Web Services]]のBedrockに統合。Azure・GCP・AWSの三大クラウドすべてでGPT-5.5が利用可能になり、企業の選択肢が大幅に広がった。",
        "GPT-5.5はOSWorld-Vベンチマークで[[75%]]を記録し、__マルチステップ自律タスク__ の実行能力を示す。AnthropicのPE直販JV vs. OpenAIのクラウドプラットフォーム戦略の分岐点が鮮明に。",
    ], AI_ACC,
    related_tip("対立", "https://github.com/HIDEPON-UMG/News-Grasp/blob/main/digest/IT-Consulting/2026-05-05-IT-Consulting.md",
                "5/5号: Anthropic直販JV"))

CATS += side_card(78, "2026-05-06 08:30", "Google",
    "https://blog.google/innovation-and-ai/models-and-research/gemini-models/next-generation-gemini-deep-research/",
    "Gemini 3.1 Deep Research Max：MCP対応・1M トークン・自律調査で長期研究を革新",
    PH_AI_C,
    [
        "Google DeepMindが[[Gemini 3.1 Pro]]をベースにした新たな[[Deep Research Max]]エージェントを発表。[[MCP（Model Context Protocol）]]対応により、社内・ウェブ横断の自律情報収集が可能になり、長期調査ワークフローを根本から変える。",
        "1Mトークン（65Kアウトプット）のコンテキスト長、18のベンチマーク中[[12ベンチマーク1位]]を達成。次の戦場はAPI呼び出しではなく__業務プロセス再設計__ であることを示している。",
    ], AI_ACC)

# ─ IT-Consulting (3/4) ───────────────────────────────────────────────────────
CATS += cat_header(3, 4, "▲", "IT &amp; CONSULTING", "IT-Consulting", IT_ACC, 5,
    "NTTデータが「LITRON Builder」エージェントAI基盤でグローバル2,000件超受注、アクセンチュアはDatabricksとの専門組織設立で直販攻勢に対抗。富士通「Kozuchi Enterprise AI Factory」は7月正式展開へ。日本のSI大手がAIエージェント基盤を製品化する動きが一斉に加速している。")

CATS += featured_card(90, "2026-05-06 07:00", "NTTデータ DATA INSIGHT",
    "https://www.nttdata.com/jp/ja/trends/data-insight/2026/032502/",
    "NTTデータ「LITRON Builder」でエージェントAI基盤を製品化 — グローバル2,000件超受注",
    PH_IT,
    [
        "NTTデータが2026年4月から提供を開始した[[LITRON Builder]]（エージェント型AI開発基盤）のグローバル展開を加速。すでに[[2,000件以上]]のSmart AI Agentプロジェクトを受注し、シリコンバレーの新会社が北米市場開拓を主導している。",
        "LLMを単体で使うのではなく、複数のエージェントが役割分担して複雑業務を自律実行する「[[マルチエージェント]]アーキテクチャ」が中心。SI業界の__人月型収益モデルからの脱却__ を目指した最大の戦略転換と位置づける。",
        "Anthropic・OpenAIのPE直販JVが「コンサルを飛ばす」戦略であるのに対し、NTTデータは「[[日本企業の業務知識]]を強みにAIエージェント構築を内製化支援する」差別化軸を打ち出しており、顧客争奪戦の構図が鮮明になってきた。",
    ], IT_ACC)

CATS += side_card(86, "2026-05-06 07:30", "アクセンチュア",
    "https://newsroom.accenture.jp/jp/news/2026/accenture-and-databricks-accelerate-enterprise-adoption-of-ai-applications-and-agents-at-scale",
    "アクセンチュア&times;Databricks：AIアプリ・エージェント導入専門組織を新設",
    PH_IT_C,
    [
        "アクセンチュアとDatabricksが戦略的パートナーシップを拡大し、[[アクセンチュア-データブリックス-ビジネスグループ]]（専門組織）を設立。DatabricksをコアデータAIプラットフォームとした大規模AIエージェント導入支援を提供する。",
        "アクセンチュア株（ACN）はAnthropicのGoldman JV発表以降40%超下落しているが、今回の発表はコンサルが「モデルの直販に対抗できる技術プラットフォームを持つ」という姿勢を示す__反撃の狼煙__ とみられる。",
    ], IT_ACC,
    related_tip("対立", "https://github.com/HIDEPON-UMG/News-Grasp/blob/main/digest/IT-Consulting/2026-05-05-IT-Consulting.md",
                "5/5号: Anthropic直販JV"))

CATS += side_card(83, "2026-05-06 08:00", "EnterpriseZine",
    "https://enterprisezine.jp/news/detail/23590",
    "富士通「Kozuchi Enterprise AI Factory」— 専有型AI基盤を日本・欧州で2026年7月正式展開",
    PH_IT_C,
    [
        "富士通が「[[Fujitsu Kozuchi Enterprise AI Factory]]」を日本と欧州で展開。顧客専有環境で業務特化した生成AIモデルを自律運用できる基盤として2026年2月から先行トライアル、[[7月に正式提供]]予定。",
        "SI業界でAIを「使う」から「作る」へのシフトが加速。富士通が独自アーキテクチャーを適用すれば、[[従来比70%の工数削減]] が可能という試算も出ており、業界の収益構造変革が不可避になる。",
    ], IT_ACC)

CATS += side_card(79, "2026-05-06 09:00", "ITmedia オルタナティブ・ブログ",
    "https://blogs.itmedia.co.jp/serial/2026/02/ainttnec.html",
    "富士通AI駆動開発がSI業態を再定義 — アクセンチュア・NTTデータ・NECとの三つ巴",
    PH_IT_C,
    [
        "富士通がAI駆動の開発フライホイールを本格稼働させれば、[[アクセンチュア]]・[[NTTデータ]]・[[NEC]]を駆逐するロジックが成立するとの見方が業界で広まりつつある。「AIがシステムを丸ごと開発する」富士通の戦略は人月ビジネスの根本を崩しかねない。",
        "NTTデータグループは2027年度に開発工程全体の40%効率化・適用案件比率50%を目標。富士通も70%工数削減を見込む。__二社の競争が業界全体の生産性基準を引き上げ__、中小SIerを市場から締め出す可能性がある。",
    ], IT_ACC)

CATS += side_card(75, "2026-05-06 10:00", "日経xTECH",
    "https://xtech.nikkei.com/atcl/nxt/column/18/00001/11238/",
    "IT大手4社、開発に生成AI適用本腰 — NTTデータG「2027年度に40%効率化」目標",
    PH_IT_C,
    [
        "NTTデータグループ・富士通・NEC・日立の大手4社が生成AI適用を本格化。NTTデータGは「[[2027年度に40%の開発工程効率化]]」「適用案件比率50%」を目標に掲げ、現在は500件のプロジェクトに生成AIを適用中。",
        "課題はROIの可視化と人月契約の見直し。生成AI適用で「工数が減る」ことは__顧客への請求額削減__ を意味する。SI業界全体の収益構造が根本から変わる岐路に立っている。",
    ], IT_ACC)

# ─ Economy (4/4) ─────────────────────────────────────────────────────────────
CATS += cat_header(4, 4, "■", "ECONOMY", "経済", EC_ACC, 5,
    "GW明けの日経平均は59,513円（5/1）から6万円台固めへ。今週はトヨタ2026年3月期決算（5/8）と米雇用統計（5/9）が最大の焦点。野村証券はS&amp;P500年末目標を7,500に引き上げたが、AI・半導体セクター集中型の上昇が全面高を伴わないという構造的な課題が続く。")

CATS += featured_card(91, "2026-05-06 07:00", "日経新聞",
    "https://www.nikkei.com/article/DGXZQOFL015830R00C26A5000000/",
    "日経平均 GW明け6万円台固めへ — トヨタ5/8・米雇用統計5/9が今週の関門",
    PH_EC,
    [
        "5月1日の東証大引けで[[日経平均株価]]は前日比228円高の59,513円と3日ぶり反発。GW明け後の最初の営業日（5/6、本日）も米株高の追い風を受け、[[6万円台]]の固めが期待される。",
        "今週最大の注目は[[トヨタ自動車の5/8決算説明会]]（14:00オンライン）と、[[5/9の米雇用統計]]。トヨタの2026年3月期は米関税影響1.4兆円を織り込んだ上で純利益3.5兆円（従来予想から0.6兆円上方修正）という着地が予想されており、市場は慎重に精査する。",
        "日経平均の上昇は[[AI・半導体・データセンター銘柄]]（アドバンテスト・東京エレクトロン等）に集中しており、内需系・金利感応度の高い銘柄は置き去り気味。__「6万円でも全面高ではない」__ という分極化は5月も継続する見通し。",
    ], EC_ACC)

CATS += side_card(88, "2026-05-06 08:00", "日経新聞",
    "https://www.nikkei.com/article/DGXZQOFD066IP0W5A800C2000000/",
    "トヨタ2026年3月期 純利益3.5兆円に上方修正 — 米関税影響1.4兆円、HV販売増が下支え",
    PH_EC_C,
    [
        "トヨタ自動車が2026年3月期の連結純利益を従来予想の2.9兆円（39%減）から[[3.5兆円（25%減）]]に上方修正。米国の追加関税による影響は[[1.4兆円]]を見込むものの、ハイブリッド車（HV）の販売増が収益を下支えした。",
        "第3四半期（9ヶ月累計）は売上高38.09兆円（前年比+6.8%）と増収確保。__「稼ぐ力」の底堅さ__ を示す内容。[[HV戦略の正当性]]が再び注目される一方、中国市場でのBYDとの競合が激しさを増している。",
    ], EC_ACC)

CATS += side_card(84, "2026-05-06 09:00", "野村證券 Wealth Style",
    "https://www.nomura.co.jp/wealthstyle/article/0714/",
    "野村証券：S&amp;P500 2026年末7,500に引き上げ — イラン和平収束とAI需要拡大を想定",
    PH_EC_C,
    [
        "野村証券のストラテジストが[[S&amp;P500]]の2026年末見通しを[[7,500]]（メインシナリオ）に引き上げ。前提はイラン情勢収束によるエネルギー価格安定と、AI関連設備投資による企業収益拡大。上振れシナリオでは7,900も想定。",
        "S&amp;P500のEPS（1株当たり純利益）を2025年の269.3から2026年は[[330.4]]に上方修正。AI・クラウドセクターの利益成長が指数全体を牽引しており、__バリュエーション正当化の根拠__ として提示された。",
    ], EC_ACC)

CATS += side_card(80, "2026-05-06 09:30", "SBI証券",
    "https://go.sbisec.co.jp/media/report/op225/op225_260428.html",
    "日経平均6万円 — AI・半導体が牽引するが全面高ではない。内需銘柄の置き去りに注意",
    PH_EC_C,
    [
        "日経平均が6万円を達成しても「全面高」ではなく、[[AI関連・半導体・データセンター・アドバンテスト]]などが指数を押し上げる一方、内需系・金利感応度の高い銘柄は置き去りになっている。__指数と個別銘柄の乖離__ が過去最大水準に達しているとの指摘も。",
        "外国人投資家の買いが一部の「AI受益銘柄」に集中している構図は、米国市場の「Magnificent 7」現象と類似。個人投資家は__分散投資の重要性__ が改めて問われる局面。",
    ], EC_ACC)

CATS += side_card(77, "2026-05-06 10:00", "株式基礎",
    "https://kabukiso.com/america/outlook/2026/sp500_may.html",
    "S&amp;P500 2026年5月の見通し — 4月最高値更新後の調整リスクと決算終盤の着地点",
    PH_EC_C,
    [
        "S&amp;P500は4月30日に[[7,209]]で終値ベースの最高値を更新した後、5月に入り利益確定売りの圧力が強まっている。年末目標7,500達成には、5月9日の雇用統計・5月12日の[[CPI発表]]・決算ラストスパートが「好材料三重奏」になる必要がある。",
        "テクニカル的には[[7,050〜7,100]]が主要サポート帯。このゾーンを守れれば「押し目買い」、割り込めば調整が本格化する分岐点。[[FOMCの次回会合は6月]]で、利下げ期待の維持が相場の底を支えている。",
    ], EC_ACC)

# ── REFLECTION ───────────────────────────────────────────────────────────────
REFLECTION_TITLE = "日米の岐路 — ドル円157円の攻防とAIエンタープライズの地殻変動"
REFLECTION_SUB   = "GW明けの市場で交錯する「為替防衛の限界」と「AI産業再編の加速」"

LEAD_HTML = hl(
    "本日4分野・20本のニュースから浮かび上がる最大のテーマは [[ドル円157円攻防]] と [[AIエンタープライズの地殻変動]] の同時進行である。"
    "4/30の5.4兆円介入効果が一週間で半減しつつある中、GW明けの市場はAnthropicのウォール街深浸透という新しい構造変化を同時に消化しなければならない。"
    "以下、各カテゴリを横断して読み解く。",
    "#C9B98A")

PULL_QUOTE_HTML = hl(
    "「軍事利用を断りながら収益を拡大する」——"
    "__Anthropicの価値観ビジネス__ が、投資家の懐疑を超えて具体的な形をとり始めた日。",
    AI_ACC)

SECTIONS_HTML = (
    sec(1, "総論", "为替介入の「時間稼ぎ」とAI直販の「構造転換」",
        hl("GW明けの相場は二つの構造変化が同時に進行している。第一に、[[5.4兆円介入の効果]]が一週間で半減しつつある現実。木内登英NRIエコノミストが指摘する通り、根本的な円安要因である[[日米金利差]]は変わっておらず、介入は時間稼ぎに過ぎない。第二に、AnthropicがMoody's・Microsoft 365を通じてウォール街に深く食い込み始めたこと。この二つは一見無関係だが、どちらも「旧来の秩序が臨界点に達した」という同じ信号を発している。", "#1A1A1A"),
        "#1A1A1A") +
    sec(2, "為替・経済", "5/9雇用統計と5/8トヨタ決算が今週の分水嶺",
        hl("ドル円は[[157円台]]で膠着している。5/9の米雇用統計（非農業部門5.9万人予想）が予想を上回れば158円台トライ、下回れば利下げ観測で円高という二択が迫る。同じ週に[[トヨタの5/8決算]]（純利益3.5兆円上方修正予想）がある。米関税影響1.4兆円を織り込んでも底堅い業績が確認されれば、AI・半導体集中型の日経平均に内需・製造業という新しい牽引役が加わる可能性がある。野村証券の__S&amp;P500年末7,500目標引き上げ__ はイラン和平収束という楽観シナリオが前提であり、5/9雇用統計の結果次第で上下に振れる。", FX_ACC),
        FX_ACC) +
    sec(3, "AI・技術", "Anthropicの商業モデル確立とGoogleの計算資源戦略",
        hl("AnthropicはPentagon排除という逆境の中で、[[Moody's]]・[[Microsoft 365]]・JPMorganダイモンCEOとの関係深化という「エンタープライズ深浸透」戦略を加速した。一方GoogleのDeepMindは[[AlphaEvolve]]でGoogleインフラの0.7%を自律回収するという実用的なAI最適化成果を示し、__計算資源規模を武器にした差別化__ を実証した。OpenAIのGPT-5.5 AWS Bedrock統合とAzure・GCPを合わせたクラウド三社展開完了は、エンタープライズへの最も広い「面」を形成する戦略だ。三社のアプローチが鮮明に分岐している。", AI_ACC),
        AI_ACC) +
    sec(4, "産業・IT", "日本SIの反攻：LITRON・Kozuchiがエージェント基盤を製品化",
        hl("NTTデータの[[LITRON Builder]]が2000件超受注というグローバルな実績を示し、富士通の[[Kozuchi]]が7月正式展開へ。これはAnthropicのPE直販JVに対する日本SI大手の回答だ。「モデルを持つ外資が直販する」のに対し「[[日本企業の業務知識を強みに内製化支援する]]」という差別化軸を打ち出している。NTTデータ・富士通・NECの70〜40%工数削減目標が示す通り、__SI業界の生産性基準自体が書き換えられつつある__。この変化は中小SIerの市場退出を加速させる。", IT_ACC),
        IT_ACC) +
    sec(5, "明日へ", "3つの注目タイミング",
        hl("今後1週間で注目すべきは三点。第一に、[[トヨタ5/8決算説明会]]（14:00オンライン）。米関税1.4兆円影響下でのHV戦略の正当性が問われる。第二に、[[5/9の米雇用統計]]。ドル円の方向と市場センチメントを決定づける今週最大の分水嶺。第三に、富士通[[Kozuchi]]の7月正式展開に向けた受注動向。日本のSI市場でAIエージェント基盤の製品化競争がどのような構図になるかを示す先行指標となる。", "#C9B98A"),
        "#C9B98A")
)

TAKEAWAYS_HTML = (
    takeaway("01", FX_ACC, "為替",
        hl("ドル円[[157円台]]の膠着は[[5/9雇用統計]]で解消される。予想超えなら158円台トライ、下回りなら円高シナリオ。__介入の構造的限界__ を木内NRIが改めて指摘。", FX_ACC)) +
    takeaway("02", AI_ACC, "AI",
        hl("Anthropicが[[Moody's・M365統合]]でウォール街に深浸透、AlphaEvolveがGoogleのインフラを自律最適化。AIは__エンタープライズのコアに埋め込まれる__ 段階に入った。", AI_ACC)) +
    takeaway("03", IT_ACC, "IT-コンサル",
        hl("NTTデータ[[LITRON Builder]]が2000件超受注、富士通[[Kozuchi]]が7月展開へ。日本SIの__AIエージェント基盤製品化__ がAnthropicPE直販JVへの本格対抗軸となる。", IT_ACC))
)

RELATED_ISSUES_HTML = (
    related_row("2026-05-05",
        "https://github.com/HIDEPON-UMG/News-Grasp/blob/main/digest/Summary/2026-05-05.md",
        "Anthropic PE直販JV発表・Pentagon AI8社契約・Switch 2発売初週") +
    related_row("2026-05-04",
        "https://github.com/HIDEPON-UMG/News-Grasp/blob/main/digest/Summary/2026-05-04.md",
        "Pentagon AI入札落選のAnthropicと企業向け反転攻勢") +
    related_row("2026-05-01",
        "https://github.com/HIDEPON-UMG/News-Grasp/blob/main/digest/Summary/2026-05-01.md",
        "4/30 5.4兆円介入・S&P500最高値更新・Sell in May開幕")
)

# ── FINAL SUBSTITUTION ────────────────────────────────────────────────────────
html = TMPL
html = html.replace("{{ISSUE_NO}}",           "20260506")
html = html.replace("{{ISSUE_DATE}}",         "2026-05-06")
html = html.replace("{{ISSUE_WEEKDAY}}",      "水")
html = html.replace("{{TOTAL_CATEGORIES}}",   "4")
html = html.replace("{{TOTAL_STORIES}}",      "20")
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
print(f"Written {len(html):,} bytes to {OUT}")
