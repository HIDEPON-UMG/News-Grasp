# -*- coding: utf-8 -*-
"""2026-05-10 メール HTML 生成スクリプト"""
import pathlib

OUT = pathlib.Path(r"C:\Users\hidek\Obsidian\New's Grasp\News-Grasp\build\email.html")

def hl(text):  # [[太字]] → highlight span
    return f'<strong style="background:#F5F0E8;padding:0 3px;border-radius:2px;">{text}</strong>'

def ul(text):  # __下線__ → underline span
    return f'<span style="border-bottom:1.5px solid #1A1A1A;padding-bottom:1px;">{text}</span>'

def bul(text):
    return f'<p style="margin:0 0 9px;font-size:14px;line-height:1.9;color:#1A1A1A;padding-left:0;">▸ {text}</p>\n'

MONO = "font-family:'JetBrains Mono',Consolas,'Courier New',monospace;"

# ── TOC ─────────────────────────────────────────────────────────────────────
TOC_ROWS = [
    ("#B8860B", "¥1.", "為替 (Foreign Exchange)", "5"),
    ("#2D5BB8", "◆2.", "AI (Artificial Intelligence)", "5"),
    ("#2E6B52", "▲3.", "IT-Consulting (IT &amp; Consulting)", "5"),
    ("#5E3D8C", "●4.", "ゲーム (Gaming)", "5"),
]

toc_html = ""
for ac, num, name, cnt in TOC_ROWS:
    toc_html += f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:5px;"><tbody><tr>
  <td width="36" style="{MONO}font-size:12px;color:{ac};font-weight:700;">{num}</td>
  <td style="font-size:14px;font-weight:700;">{name}</td>
  <td align="right" style="{MONO}font-size:11px;color:#5C5A52;">{cnt} stories</td>
</tr></tbody></table>\n"""

# ── CATEGORY HEADER ──────────────────────────────────────────────────────────
def cat_header(idx, total, accent, glyph, name_en, name_jp, summary, count):
    return f"""<tr><td style="background:{accent};padding:20px 36px;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0"><tbody><tr>
    <td style="vertical-align:middle;">
      <div style="{MONO}font-size:10px;color:rgba(255,255,255,0.7);letter-spacing:2px;margin-bottom:4px;">CATEGORY {idx} / {total} · {name_en.upper()}</div>
      <div style="font-size:28px;font-weight:800;color:#fff;letter-spacing:-0.5px;line-height:1.1;">
        <span style="{MONO}margin-right:10px;">{glyph}</span>{name_jp}
      </div>
    </td>
    <td align="right" valign="middle" style="{MONO}font-size:11px;color:rgba(255,255,255,0.85);">{count} stories</td>
  </tr></tbody></table>
  <div style="font-size:13px;color:rgba(255,255,255,0.95);font-style:italic;line-height:1.65;margin-top:10px;padding-top:10px;border-top:1px solid rgba(255,255,255,0.25);">{summary}</div>
</td></tr>\n"""

# ── ARTICLE CARDS ────────────────────────────────────────────────────────────
def meta_row(date_str, source, score, accent):
    return f'<div style="{MONO}font-size:10px;color:#5C5A52;letter-spacing:1.2px;margin-bottom:10px;"><span style="background:{accent};color:#fff;padding:2px 6px;margin-right:8px;font-weight:700;">SCORE {score}</span>{date_str} · {source}</div>\n'

def card_title(title, url, size=22):
    return f'<h3 style="font-size:{size}px;font-weight:800;line-height:1.35;letter-spacing:-0.3px;margin:0 0 14px;color:#1A1A1A;"><a href="{url}" style="color:#1A1A1A;text-decoration:none;">{title}</a></h3>\n'

def feature_img(url_img, url_link):
    return f'<div style="margin-bottom:16px;"><a href="{url_link}"><img src="{url_img}" width="100%" style="display:block;height:180px;object-fit:cover;border:1px solid #E2DED4;" alt=""></a></div>\n'

def related_tip(date_str, label, anchor_text):
    return f'<div style="{MONO}font-size:10px;color:#5C5A52;margin-top:12px;padding-top:10px;border-top:1px dashed #E2DED4;letter-spacing:0.8px;">🔗 関連 ({label}): {date_str} — {anchor_text}</div>\n'

def card_top(accent, score, date_str, source, title, url, img_url, bullets, related=None):
    out = f'<tr><td style="padding:24px 36px;background:#FAF7F0;border-bottom:1px solid #EDEAE3;">\n'
    out += meta_row(date_str, source, score, accent)
    out += card_title(title, url)
    out += feature_img(img_url, url)
    for b in bullets:
        out += bul(b)
    if related:
        out += related_tip(*related)
    out += '</td></tr>\n'
    return out

def card_side(accent, score, date_str, source, title, url, thumb_url, bullets, related=None):
    out = f'<tr><td style="padding:20px 36px;background:#FAF7F0;border-bottom:1px solid #EDEAE3;">\n'
    out += meta_row(date_str, source, score, accent)
    out += card_title(title, url, size=17)
    out += f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" class="ng-side-table"><tbody><tr>\n'
    out += f'<td class="ng-card-thumb" width="140" style="padding-right:16px;vertical-align:top;"><a href="{url}"><img class="ng-card-thumb-img" src="{thumb_url}" width="140" style="display:block;height:90px;object-fit:cover;border:1px solid #E2DED4;" alt=""></a></td>\n'
    out += '<td class="ng-card-body-cell" style="vertical-align:top;">\n'
    for b in bullets:
        out += bul(b)
    out += '</td>\n</tr></tbody></table>\n'
    if related:
        out += related_tip(*related)
    out += '</td></tr>\n'
    return out

THUMB_BASE = "https://raw.githubusercontent.com/HIDEPON-UMG/news-grasp-assets/main"

# ════════════════════════════════════════════════════════════════
# FX
# ════════════════════════════════════════════════════════════════
fx_acc = "#B8860B"
fx_thumb = f"{THUMB_BASE}/ng-thumb-common-fx.jpg"

fx_html = cat_header(1, 4, fx_acc, "¥", "Foreign Exchange", "為替 (Foreign Exchange)",
    "為替介入一過——GW期間中の5兆円規模介入が市場を揺さぶった後、USD/JPYは米4月NFP（+11.5万人）を受けて157.3円でクローズ。週明けの158円接近に介入再動・FRB新議長交代（5/15）・日銀6月利上げ観測が交差する複合相場。",
    5)

fx_html += card_top(fx_acc, 95, "2026-05-10 07:30", "日本経済新聞",
    "5月GW連休中も為替介入、4〜5兆円規模か — 相場反転効果は限定的",
    "https://www.nikkei.com/article/DGXZQOUB070R90X00C26A5000000/",
    f"{THUMB_BASE}/ng-thumb-fx.jpg",
    [
        f"{hl('4〜5兆円規模')}の円買い介入をGW連休中（4月30日〜5月6日）に複数回実施。一時155円台まで急騰したが即日に157円台へ逆戻りし、{ul('介入効果の持続性')}が改めて問われる局面となっている。",
        f"今回の介入は過去最大規模（2022年の9兆円超）には及ばないが、薄商いを突いた「奇襲型」として機能。市場参加者は{hl('160円')}を次の防衛ラインと読み、週明けも158円以上では再介入リスクを強く意識している。",
        f"日銀の6月会合での{ul('利上げ確率66%')}が市場に織り込まれているが、FRB利下げ前進なしに日米金利差は縮まらず、構造的な円安が続くとの見解が支配的。",
    ],
    ("2026-05-04", "復状", "「政府・日銀4月30日夜に5〜6兆円規模の円買介入」"))

fx_html += card_side(fx_acc, 88, "2026-05-10 10:00", "NRI研究員の時事解説",
    "連休中に為替が円高に振れる — 今後も予想される為替介入",
    "https://www.nri.com/jp/media/column/kiuchi/20260507.html", fx_thumb,
    [
        f"NRI木内英生氏は、GW中の介入で一時{hl('155円台')}に急騰したドル円が直後に157円へ戻した構図を「時間稼ぎ」と評価。{ul('日米金利差が縮まらない限り')}、介入は繰り返す運命にあると分析する。",
        f"5月7日に157円台前半で「介入らしき動き」が再び観測され、{hl('158〜159円台')}を許容しない「暗黙のキャップ」として機能し始めている可能性を指摘。",
        f"根本的解決策として日銀の{ul('段階的利上げ加速')}を提示。6月会合での0.25%利上げで政策金利1%が視野に入れば、円安圧力の緩和に繋がるとの見通しを示す。",
    ])

fx_html += card_side(fx_acc, 82, "2026-05-10 08:00", "NOMURA ウェルスタイル",
    "2026年末の米ドル円見通しを152.5円に引き上げ — 中東情勢で強まる米ドル高圧力",
    "https://www.nomura.co.jp/wealthstyle/article/0676/", fx_thumb,
    [
        f"野村証券の後藤氏は{hl('中東和平進展')}によるドル高圧力を受け、年末ドル円見通しを従来の150円から{ul('152.5円に上方修正')}。地政学リスク緩和がパラドックス的にドル強含みを促す構図を指摘する。",
        f"FRBの次期議長ウォーシュ氏は「インフレ対応優先」の立場が強く、早期利下げに慎重。{hl('日米金利差')}は2026年後半でも3.5%以上を維持する見込みが高まり、円安の構造的持続を示唆する。",
        f"EUR/USDも1.06台と欧州景気懸念でドル高傾向。輸入物価上昇を通じたCPIへの二次効果が{hl('6月日銀会合')}での判断に影響する可能性がある。",
    ])

fx_html += card_side(fx_acc, 78, "2026-05-10 20:00", "外為どっとコム マネ育チャンネル",
    "為替介入「厳しい副作用がドル円に」— GW後の157円回帰と介入の限界",
    "https://www.gaitame.com/media/entry/2026/05/05/200000", fx_thumb,
    [
        f"政府の{hl('為替介入')}が「速効性あり・持続性なし」の典型パターンをなぞっており、ヘッジファンドが「介入後の戻り売り」を戦略化しているとの指摘が目立つ。",
        f"副作用として{ul('ボラティリティの上昇')}と日本国債需給不安が挙げられる。介入原資は外貨準備（米国債）の売却であり、米国からの政治的プレッシャーが潜在するリスクも存在する。",
        f"ドル円の適正レンジとして{hl('155〜160円')}を示しながら、5月12日米CPI・6月15〜16日日銀会合をトレードの次の節目として位置付けている。",
    ])

fx_html += card_side(fx_acc, 74, "2026-05-10 09:00", "みんかぶ FX",
    "為替介入、標的を絞った対応の可能性との指摘も — NY外為市場分析",
    "https://fx.minkabu.jp/news/366374", fx_thumb,
    [
        f"ニューヨーク外為市場で{hl('標的を絞った介入')}が議論されている。特定節目（158〜160円）接近時のみ介入する「バンド管理的アプローチ」に財務省がシフトしている可能性を指摘する。",
        f"CFTC報告では投機的な円売りポジションが45%急減し、{ul('大口は介入リスクを警戒')}してポジションを縮小中。円安再燃には新たな触媒が必要な状況となっている。",
        f"EUR/JPYも{hl('172円台')}で落ち着き、クロス円全般が介入リスクを織り込んだ横ばい推移。日銀利上げ観測と欧州景気減速が相互に作用する複合相場を示す。",
    ])

# ════════════════════════════════════════════════════════════════
# AI
# ════════════════════════════════════════════════════════════════
ai_acc = "#2D5BB8"
ai_thumb = f"{THUMB_BASE}/ng-thumb-common-ai.jpg"

ai_html = cat_header(2, 4, ai_acc, "◆", "Artificial Intelligence", "AI (Artificial Intelligence)",
    "「軍事とAIの衝突点」—— Google DeepMind UK職員が98%の賛成でペンタゴン契約反対の組合結成へ。GPT-5.5 InstantがChatGPT新デフォルトに、AnthropicのProject GlasswingがAIサイバー防衛の新標準を示す。",
    5)

ai_html += card_top(ai_acc, 92, "2026-05-10 14:00", "Fortune",
    "Google DeepMind UK staff 98% vote to unionize over Pentagon AI contract",
    "https://fortune.com/2026/05/05/google-deepmind-unionize-vote-military-ai-contracts-internal-backlash-pentagon-deal-israeli-defense-forces/",
    f"{THUMB_BASE}/ng-thumb-ai.jpg",
    [
        f"英国を拠点とする{hl('Google DeepMind')}の職員が英通信労働組合（CWU）への加入を98%の賛成多数で可決。ペンタゴンとの機密AI契約に対する内部反発が直接の引き金で、{ul('フロンティアAIラボ初の組合')}として歴史的転換点となる。",
        f"2018年のProject Maven騒動後に表明した「兵器開発・監視への非関与」方針が2025年2月に撤回されており、今回の組合化は{hl('AIと軍事利用')}を巡る職場内倫理の亀裂が不可逆段階に達したことを象徴する。",
        f"組合側は10営業日以内に経営側が団交を受け入れなければ{ul('法的手続き')}に移行すると通告。イスラエル国防軍への技術提供停止も要求しており、Google親会社Alphabetの経営判断に影響を与えうる。",
    ],
    ("2026-05-03", "対立", "「Pentagon strikes deals with 8 Big Tech companies」"))

ai_html += card_side(ai_acc, 88, "2026-05-10 09:00", "TechCrunch",
    "OpenAI releases GPT-5.5 Instant, a new default model for ChatGPT",
    "https://techcrunch.com/2026/05/05/openai-releases-gpt-5-5-instant-a-new-default-model-for-chatgpt/", ai_thumb,
    [
        f"OpenAIは{hl('GPT-5.5 Instant')}を5月5日にリリースし、ChatGPTの新デフォルトモデルに据えた。{ul('レイテンシを40%削減')}しながらベンチマーク性能はGPT-5.5と同等水準を維持するという。",
        f"「インスタント」系はエンタープライズ向け量的拡張を担い、{hl('APIコスト')}も50%削減。Claude Sonnet 4.6との直接競合が激化し、ミドルスペックモデル市場での覇権争いが本格化した。",
        f"同週にGPT-5.5がAWS Bedrockにも上陸し、主要クラウド三社展開が完了。{ul('エンタープライズ囲い込み')}において「どのクラウドでも使える」OpenAIの戦略的優位が確立されつつある。",
    ])

ai_html += card_side(ai_acc, 85, "2026-05-10 22:00", "Anthropic",
    "Project Glasswing: Claude Mythos Preview で AI サイバー防衛の新標準を確立",
    "https://www.anthropic.com/glasswing", ai_thumb,
    [
        f"AnthropicはClaude Mythos Preview搭載のサイバーセキュリティAI「Project Glasswing」を正式発表。全主要OS・ブラウザに存在する高リスク脆弱性を数千件発見した実績を引き提げ、約{hl('50の大企業・銀行')}に限定公開している。",
        f"Mythos Previewはリポジトリを自律的に解析し、{ul('CVSSスコア9.0超のRCE脆弱性')}を人間のトップ研究者を上回る速度で特定できるとされる。「AIサイバー攻防の核心が変わった」との評価が専門家から相次ぐ。",
        f"一般公開を見送った背景には{hl('攻撃・防御の非対称性')}問題があり、悪意ある行為者が入手した場合の被害規模を試算した内部レポートがAI安全性議論を加速させている。",
    ])

ai_html += card_side(ai_acc, 80, "2026-05-10 12:00", "Air Street Press",
    "State of AI: May 2026 — Anthropic・OpenAI 二強が支配する構図と次の断層線",
    "https://press.airstreet.com/p/state-of-ai-may-2026", ai_thumb,
    [
        f"2026年5月時点のAI市場では、{hl('Anthropic')}と{hl('OpenAI')}が基盤モデルとエンタープライズ契約の両軸で支配的地位を確立。GoogleはTPU垂直統合とGeminiシリーズで独自の優位性を維持するが、一般商用展開ではClaude/GPTが先行している。",
        f"特筆すべきは{ul('LLMの「コモディティ化」の加速')}。Meta Llama 4 Maverick（オープンソース）がGPT-5.4に迫るベンチマーク性能を無料で提供し、クローズドモデルとの価格差競争がさらに激化している。",
        f"AIエージェントの企業利用率が初めて{hl('17.8%')}を突破（Microsoft調査）し、「試験段階」から「業務組み込み段階」への転換期に突入。法律・金融・コードレビューが主要3ユースケースとして台頭している。",
    ])

ai_html += card_side(ai_acc, 76, "2026-05-10 08:00", "AI Weekly",
    "Anthropic just had AI's biggest week of 2026 — Q1 ARR $44B・前年比80倍の衝撃",
    "https://aiweekly.co/issues/anthropic-just-had-ais-biggest-week-of-2026", ai_thumb,
    [
        f"Anthropicの2026年Q1における年率換算ARRが{hl('44億ドル（約6.4兆円）')}を突破し、前年同期比{ul('80倍')}という業界史上例のない急成長率を記録。Goldman Sachs・Visaといったウォール街大手のエンタープライズ導入が主な成長ドライバー。",
        f"同週にはSpaceX Colossusとのインフラ協定（22万GPU・300MW確保）、Google TPU長期契約（200億ドル規模）、Blackstone×Goldman Sachsとの新会社設立が相次いで発表され、{hl('Anthropicのエコシステム構築')}が本格加速していることを示した。",
        f"PentagonとのAI契約を{hl('倫理条項')}を理由に拒絶したことで、主要競合との「軍事AI路線」の差別化が明確化。米政府との距離感が中長期的なエンタープライズ展開の足かせになるリスクも指摘されている。",
    ])

# ════════════════════════════════════════════════════════════════
# IT-Consulting
# ════════════════════════════════════════════════════════════════
it_acc = "#2E6B52"
it_thumb = f"{THUMB_BASE}/ng-thumb-common-it.jpg"

it_html = cat_header(3, 4, it_acc, "▲", "IT & Consulting", "IT-Consulting (IT &amp; Consulting)",
    "「コンサルティング×AI」変革の号砲 —— NTTデータがグローバル成長戦略加速を宣言、McKinseyのAI起因人員削減予測、アクセンチュア株25%下落の深層が、コンサル業界の不可逆な構造変化を照らし出す。",
    5)

it_html += card_top(it_acc, 90, "2026-05-10 10:00", "NTTデータグループ",
    "NTTデータグループ、AIを中核とした成長戦略をグローバルで加速 — コンサルティングセグメント新設",
    "https://www.nttdata.com/global/ja/news/release/2026/050806/",
    f"{THUMB_BASE}/ng-thumb-it.jpg",
    [
        f"NTTデータグループは{hl('コンサルティング×AI')}を中核に事業構造を転換し、既存3セグメントに加えて全社横断の「コンサルティングセグメント」を新設すると発表。経営変革構想の策定からAIエージェント実装までを一貫して担う体制を整える。",
        f"再編後の「AI事業本部」には分散していたAI関連機能を集約し、2027年度には{ul('AIエージェント関連ビジネスで3,000億円')}の売上を目標に据える。シリコンバレー新会社「NTT DATA AIVista」が北米市場での先行事例を創出する。",
        f"同社が掲げる「AIネイティブ開発」では{hl('2026年度中')}に生成AIがシステム開発の主役となる技術体制を整備予定。SI業態そのものの定義を変えうる宣言として業界に衝撃を与えている。",
    ],
    ("2026-05-09", "進展", "「NTTデータグループ大規模組織改編発表」"))

it_html += card_side(it_acc, 84, "2026-05-10 09:00", "NTTデータ経営研究所",
    "NTTデータ経営研究所、金融機関向けAI導入コンサルティングサービスを開始",
    "https://www.nttdata-strategy.com/newsrelease/260507/", it_thumb,
    [
        f"NTTデータ経営研究所が5月7日、{hl('金融機関')}向けの生成AI活用コンサルティングサービスを正式に提供開始。リスク管理・コンプライアンス・顧客向けサービス品質の向上を三本柱に据え、銀行・保険・証券を対象とする。",
        f"金融分野はAI導入に際して規制対応とデータガバナンスが特に厳格であり、{ul('レギュレーションに精通したAI導入支援')}という差別化ポジションで、外資コンサルとの競合に挑む。",
        f"NTTデータグループ全体の「コンサルティング×AI」戦略と連動し、{hl('グループシナジー')}を活かした上流から実装・運用までのEnd-to-Endサービスが特徴。国内金融大手との実績構築を経て2027年度に海外展開を計画。",
    ])

it_html += card_side(it_acc, 80, "2026-05-10 07:00", "日経クロステック",
    "IT大手5社 2026年の事業展望 — AIネイティブSI転換の正念場",
    "https://xtech.nikkei.com/atcl/nxt/column/18/03454/", it_thumb,
    [
        f"富士通・NEC・NTTデータ・日立・CTCのIT大手5社が{hl('「御用聞きSI」から「提案型AIコンサル」')}への転換を2026年度の最重要テーマに据えている。5社合計の2025年度受注高はいずれも過去最高に迫る水準。",
        f"NTTデータが全社にAI事業本部を新設したことが業界内で「触媒」として機能し、{ul('各社のAIシフト計画')}が相次いで上方修正・前倒し発表される連鎖が起きている。",
        f"最大の課題は{hl('AIエンジニアの確保')}。各社がリスキリング投資を加速する一方、OpenAI・Anthropicとの直接契約を選択する顧客企業が増え、「中間SIの存在意義」が問われる構造的ジレンマに直面している。",
    ])

it_html += card_side(it_acc, 75, "2026-05-10 11:00", "The HR Digest",
    "McKinsey Job Cuts Predicted for 2026 — AI自動化が戦略コンサルの雇用構造を変える",
    "https://www.thehrdigest.com/mckinsey-job-cuts-predicted-for-2026-with-ai-ambitions-to-credit-for-the-change/", it_thumb,
    [
        f"業界調査ではMcKinseyの2026年内人員削減規模は数千人に上る可能性があり、主因は{hl('AIアシスタントの導入')}による分析・研究業務の自動化。アナリスト・コンサルタント層が最も影響を受けやすいとされる。",
        f"McKinseyのプロジェクトのうち{ul('40%がAI関連')}となっており（自社調査）、皮肉にも顧客企業にAI導入を提案することで自社のAI代替リスクが現実化している構図。BCGも25%の収益をAI関連とするが、組織スリム化の方向性は共通している。",
        f"この動きはコンサル業界全体に波及し、{hl('ジュニア人材のキャリア')}が根本的に変わると専門家は指摘。AI時代のコンサルタントに求められるのは「ビジネス設計」と「AI監督」の複合スキルへと移行しつつある。",
    ])

it_html += card_side(it_acc, 70, "2026-05-10 13:00", "TIKR.com",
    "アクセンチュア株25%下落の深層 — 投資家が「AIを活用する企業」より「AIを作る企業」を選好",
    "https://www.tikr.com/blog/accenture-is-down-25-as-investors-favor-faster-ai-winners-heres-where-the-stock-could-go-in-2026", it_thumb,
    [
        f"アクセンチュア株は過去1年で25%下落しており、市場は{hl('純粋AIプレー')}（NVIDIA・Anthropic）を選好し「AIを活用するSI企業」への評価を引き下げている。Q2 FY2026は売上$180億で予想超過だったが、Q3の伸び悩み見通しが株安を加速させた。",
        f"生成AI受注の{ul('累計は約100億ドル')}に達しており、実ビジネスとしては着実な成長を示している。しかし「AIそのものを作る企業」と比較されるバリュエーションの構造的な不利が続いている。",
        f"2026年内に{hl('AI ROI')}を証明する大型成功事例が出れば株価回復の転換点となる可能性。アナリストの目標株価中央値は現在比+35%で、打ち込まれ過ぎとの見方も根強い。",
    ])

# ════════════════════════════════════════════════════════════════
# Game
# ════════════════════════════════════════════════════════════════
gm_acc = "#5E3D8C"
gm_thumb = f"{THUMB_BASE}/ng-thumb-common-game.jpg"

gm_html = cat_header(4, 4, gm_acc, "●", "Gaming", "ゲーム (Gaming)",
    "Switch 2成熟期の戦略転換 —— 任天堂が1986万台・2.3兆円売上の快挙後、FY2027は値上げを軸に「台数から質」の成長へ。Cygamesが「リトル ノア 楽園の後継者」を本日発売しコンソールIPクロス展開を加速。",
    5)

gm_html += card_top(gm_acc, 92, "2026-05-10 08:00", "Nintendo Everything",
    "Nintendo Switch 2 ミリオンセラー詳報 — Pokemon Pokopia 400万本超、マリカワールド1,470万本",
    "https://nintendoeverything.com/nintendo-switch-2-and-switch-million-sellers-for-may-2026-pokemon-pokopia-and-firered-leafgreen-over-4-million-more/",
    f"{THUMB_BASE}/ng-thumb-game.jpg",
    [
        f"任天堂FY2026決算で開示されたミリオンセラーリストによると、Switch 2向け新作{hl('ポケモン ポコピア')}が400万本を突破。マリオカートワールドの1,470万本・スプラトゥーン4の340万本とともに「キラーコンテンツ群」が出揃いつつある。",
        f"リメイク版FireRed/LeafGreenも合計{ul('400万本超')}を達成し、任天堂IPの新旧バランスが取れた販売構造が確認された。1本のシステムセラーに依存しない多軸展開戦略が功を奏している。",
        f"Switch 2の{hl('ソフトウェア附着率')}（ハード1台あたりのソフト購入本数）はFY2026通期で平均5.4本となり、据え置き機としては過去最高水準。ゲームパスとの競合下でもパッケージ販売の底堅さが証明された。",
    ],
    ("2026-05-09", "進展", "「任天堂FY2026通期決算 — Switch 2累計1,986万台・売上2.3兆円」"))

gm_html += card_side(gm_acc, 86, "2026-05-10 07:00", "Nintendo Life",
    "任天堂、Switch 2主要タイトルのリリース窓を再確認 — スプラレイダース・ゼルダが2026年夏冬に",
    "https://www.nintendolife.com/news/2026/05/nintendo-reconfirms-release-windows-for-major-upcoming-switch-2-games", gm_thumb,
    [
        f"任天堂は決算説明の場で2026〜2027年度の主要Switch 2タイトルのリリース窓を改めて確認した。{hl('スプラトゥーン レイダース')}（2026年夏）・新ゼルダ伝説（2026年冬）・3Dマリオ新作（2027年春）の3本柱が計画通りに進行中とされる。",
        f"5月12日に控えるIndiana Jones and the Great Circle Switch 2版を筆頭に、{ul('月間2〜3本ペース')}で中〜大型タイトルが投入される予定。「発売後1年のコンテンツ供給」がSwitch 2市場維持の鍵を担う。",
        f"リズム天国 ミラクルスターズ・スプラレイダースなど{hl('2026年夏の目玉')}が複数確認されており、Switchシリーズの「夏枯れ回避」戦略の徹底が見て取れる。FY2027のソフト6,000万本目標達成に向けた伏線と見られる。",
    ])

gm_html += card_side(gm_acc, 80, "2026-05-10 06:00", "Cygames",
    "Cygames「リトル ノア 楽園の後継者」本日発売 — ウマ娘コラボDLC決定でIP展開加速",
    "https://www.cygames.co.jp/news/id-21293/", gm_thumb,
    [
        f"Cygamesがローグライトアクション{hl('リトル ノア 楽園の後継者')}をNintendo Switch・PS4・Steam向けに本日（5月10日）正式リリース。人気スマホタイトル「リトル ノア」を原作とするコンシューマー向け完全新作で、パッケージ販売の路線を取る。",
        f"発売と同時に「{ul('ウマ娘 プリティーダービー')}」とのコラボDLCも発表され、Cygames IPのクロスプロモーション戦略が際立つ。モバイル発のゲームIPをコンシューマーに移植・拡張する試みとして業界注目度が高い。",
        f"ウマ娘の世界累計収益は3,790億円を突破しており、Cygamesの{hl('IPライセンス価値')}の高さが改めて浮き彫りに。コンシューマー×スマホのクロスプラットフォーム展開が今後の業界標準になる可能性を示唆する。",
    ])

gm_html += card_side(gm_acc, 75, "2026-05-10 09:00", "Gamereactor",
    "Switch 2、2年目も初代を凌駕するペースで推移 — 値上げ後の下半期が試練",
    "https://www.gamereactor.eu/the-nintendo-switch-2-continues-to-outpace-the-original-switch-in-terms-of-sales-1716143/", gm_thumb,
    [
        f"Switch 2は発売後1年（FY2026）で{hl('1,986万台')}を記録し、Nintendo Switch本体が同期間に記録した820万台を大幅に超える推移。「据え置き×携帯の融合型」コンセプトが2020年代でも通用することを証明した。",
        f"しかし{ul('FY2027は1,650万台予測')}と前年比約17%減の見通しが懸念を呼んでいる。5月25日の日本価格改定（49,980円→59,980円）が需要に与える影響が第1四半期（4〜6月）に反映される予定。",
        f"分析者は{hl('値上げと減産調整')}こそ任天堂が利益率を守る伝統的な成熟期戦略であり、台数減を株価減少に直結させるのは誤りと指摘。FY2027の{ul('営業利益率22%超')}維持が真の評価軸となる。",
    ])

gm_html += card_side(gm_acc, 68, "2026-05-10 10:00", "Game Informer",
    "任天堂、Switch 2が20百万台に迫るも来期減益予測 — 値上げ戦略の真意",
    "https://gameinformer.com/2026/05/08/as-nintendo-switch-2-nears-20-million-units-sold-the-company-expects-sales-to-decline", gm_thumb,
    [
        f"Game InformerはFY2027の{hl('連結売上2兆500億円')}（前期比11.4%減）の見通しを分析。Switch 2本体値上げが日欧米で段階的に実施され、短期的な買い控えが発生する可能性を指摘している。",
        f"一方でソフトウェア部門は「6,000万本×単価上昇」によって{ul('前期並み以上')}を維持できるとの試算も。DLC・NSO会費収入の積み上がりがハード台数減少を補完する構造が定着しつつある。",
        f"コナミG（過去最高益）・ソニー（営業益過去最高）など国内ゲーム産業は全体として強い成長期にある中、{hl('任天堂の減益予測')}は「市場全体の退潮」ではなく「Switch 2ライフサイクルの成熟化」として解釈すべきだと結論付けている。",
    ])

categories_html = fx_html + ai_html + it_html + gm_html

# ════════════════════════════════════════════════════════════════
# REFLECTION SECTIONS
# ════════════════════════════════════════════════════════════════
def section(num, accent, tag, heading, body_html):
    return f"""<tr><td class="ng-section-pad" style="background:#FAF7F0;padding:0 36px;">
  <table width="100%" style="border-bottom:1px dashed #E2DED4;" cellpadding="0" cellspacing="0"><tbody><tr>
    <td class="ng-section-num-cell" width="80" valign="top" style="padding:28px 16px 28px 0;">
      <div class="m ng-section-num" style="{MONO}font-size:38px;font-weight:900;color:{accent};line-height:0.9;letter-spacing:-2px;">§{num:02d}</div>
      <div class="m" style="{MONO}font-size:9px;color:#fff;background:{accent};padding:2px 6px;display:inline-block;letter-spacing:1.5px;margin-top:8px;">{tag}</div>
    </td>
    <td class="ng-section-text-cell" valign="top" style="padding:28px 0 28px 20px;border-left:1px solid #E2DED4;">
      <h3 class="ng-section-heading" style="font-size:18px;font-weight:800;margin:0 0 14px;">{heading}</h3>
      <div class="ng-section-body" style="font-size:13.5px;line-height:2.0;">{body_html}</div>
    </td>
  </tr></tbody></table>
</td></tr>\n"""

sec_html = ""
sec_html += section(1, "#1A1A1A", "総論",
    "AIと軍事の衝突点が「職場」に降りてきた",
    f"Google DeepMindの英国職員が98%の賛成でCWUへの加入を決議した。この事実が持つ意味は単純ではない。{hl('フロンティアAIラボ')}における倫理規範の遵守を、経営判断でなく「労働権」として守ろうとする試みが初めて現実化した。2018年のProject Maven騒動ではGooglerたちが抗議の署名を集めて契約撤回を勝ち取ったが、今回は組合という{ul('制度的な交渉力')}を盾にしている。AIが国家安全保障に組み込まれる速度が上がるほど、この種の内部摩擦は増えるだろう。")

sec_html += section(2, "#B8860B", "為替経済",
    "介入一過、次の節目はFRB新議長と日銀6月会合",
    f"USD/JPYは米4月NFP（+11.5万人、予想の1.8倍）を受けて157.3円でクローズ。{hl('5兆円規模')}のGW介入効果は2週間を待たずに剥落し、NRI木内氏が指摘する「時間稼ぎ」の評価が裏付けられた形となった。5月15日にはパウエルFRB議長が任期を終え、タカ派寄りのウォーシュ氏が新議長に就任予定。{ul('日米金利差の縮小シナリオ')}が後退するか否かが、短期的なドル円の方向性を決める。日銀の6月利上げ確率66%はすでに織り込まれており、実際に利上げが実施されれば「予定通りの円高」として材料出尽くし感が広がるリスクもある。")

sec_html += section(3, "#2D5BB8", "AI技術",
    "Mythos/GPT-5.5競争とDeepMind組合化が示す「AI二重権力」",
    f"一方でAnthropicとOpenAIはAIサービスの商業化を一段と加速させている。{hl('GPT-5.5 Instant')}がChatGPTのデフォルトモデルに据えられ、Project GlasswingはMythos Previewをサイバー防衛の切り札に位置付けた。Anthropicの年率ARRは前年比80倍の44億ドルに達し、エコシステム構築（SpaceX・Google・Goldman Sachs）が急ピッチで進む。しかしこの「商業化」の猛進に{ul('DeepMind職員の組合化')}という形で「待った」がかかった。AIが持つ二面性——富と危険の同時生産——が、企業ガバナンスと労働関係という古典的な問題を引き連れて浮上してきた。")

sec_html += section(4, "#2E6B52", "産業業界",
    "NTTデータ再編×McKinsey人員削減：コンサルの役割が溶解している",
    f"NTTデータが「コンサルティング×AI」を掲げて全社再編し、McKinseyがAIによる数千人の削減に直面する——この二つの動きは同じ力学の表裏だ。{hl('AIがコンサルタントの仕事を奪い')}、同時にAIを活用するコンサルが付加価値を高めるという矛盾の中で、業界の中間層が消えていく。アクセンチュア株の25%下落は「AIを活用するSI企業は評価されない」という市場の冷酷な判断を反映している。しかしこの価格は{ul('過剰調整の可能性')}があり、AI ROI実証の先行事例が出れば一転する局面が来る。")

sec_html += section(5, "#C9B98A", "明日へ",
    "Switch 2値上げ・DeepMind組合が問う「誰がAIを所有するか」",
    f"任天堂のSwitch 2値上げ（1万円）とGoogle DeepMind組合化は、表面上は無関係な事件に見える。しかし両者は共通の問いを投げかけている。{hl('プラットフォームを持つ者')}が価格を決め、{hl('職員が倫理を守る組合')}を作らなければAI利用の線引きは経営者に委ねられる。技術の民主化（Llama 4オープンソース、GPT-5.5 Instant低価格化）が進む一方で、権力の集中（Pentagon AI deals、巨大企業連合）も加速している。この緊張は今週末の日曜に、特に鮮明に結晶化した。")

# ════════════════════════════════════════════════════════════════
# TAKEAWAYS
# ════════════════════════════════════════════════════════════════
def takeaway(num, color, tag, text_html):
    return f"""<tr><td style="padding-bottom:12px;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#fff;border:1px solid #E2DED4;"><tbody><tr>
    <td width="56" valign="middle" style="background:{color};color:#fff;text-align:center;{MONO}font-size:18px;font-weight:900;padding:14px 0;">{num:02d}</td>
    <td style="padding:12px 16px;">
      <div style="{MONO}font-size:9px;color:{color};font-weight:700;letter-spacing:1.5px;margin-bottom:4px;">{tag.upper()}</div>
      <div style="font-size:13px;line-height:1.75;font-weight:600;">{text_html}</div>
    </td>
  </tr></tbody></table>
</td></tr>\n"""

tw_html = ""
tw_html += takeaway(1, "#B8860B", "為替",
    f"USD/JPYは157台でクローズ。FRB新議長ウォーシュ就任（5/15）が{hl('日米金利差縮小シナリオ')}を押し戻すか、6月日銀利上げとの綱引きが当面のレンジを規定する。週明け158円接近で再介入の可能性。")

tw_html += takeaway(2, "#2D5BB8", "AI",
    f"Google DeepMind英国組合化は「AIを誰が統治するか」問題を初めて職場レベルに引き下ろした。GPT-5.5 Instant・Claude Mythos Previewの商業化競争と{ul('倫理的ガバナンス')}の間の亀裂が深まる。")

tw_html += takeaway(3, "#2E6B52", "産業",
    f"NTTデータ×McKinseyの動きはコンサル業界が「人が知識を売る」から「AIを実装・監督する」業態へ移行する岐路を示す。{hl('Switch 2値上げ')}は成熟期プラットフォームの「質で稼ぐ」モデルへのシフトと読める。")

# ════════════════════════════════════════════════════════════════
# RELATED ISSUES
# ════════════════════════════════════════════════════════════════
def related_issue(date, title):
    return f"""<tr><td style="padding:10px 0;border-bottom:1px solid #E2DED4;">
  <table width="100%" cellpadding="0" cellspacing="0"><tbody><tr>
    <td width="100" style="{MONO}font-size:11px;color:#5C5A52;">{date}</td>
    <td style="font-size:13px;font-weight:600;">{title}</td>
    <td width="20" align="right" style="color:#5C5A52;">→</td>
  </tr></tbody></table>
</td></tr>\n"""

rel_html = ""
rel_html += related_issue("2026-05-09", "前号: 米4月NFP・任天堂/コナミ/ソニー決算")
rel_html += related_issue("2026-05-07", "2026-05-07: CFTC円売り残高急減・Dreaming発表")
rel_html += related_issue("2026-05-04", "2026-05-04: GW介入・Pentagon AI deals")

# ════════════════════════════════════════════════════════════════
# READ TEMPLATE AND SUBSTITUTE
# ════════════════════════════════════════════════════════════════
import re
tmpl_path = pathlib.Path(r"C:\Users\hidek\Obsidian\New's Grasp\News-Grasp\prompts\email-template.html")
raw = tmpl_path.read_text(encoding="utf-8")
# HTMLコメントを除去してGmailの102KB制限に収める
html = re.sub(r'<!--.*?-->', '', raw, flags=re.DOTALL)
# 空行を圧縮
html = re.sub(r'\n{3,}', '\n\n', html)

subs = {
    "{{ISSUE_NO}}":              "20260510",
    "{{ISSUE_DATE}}":            "2026-05-10",
    "{{ISSUE_WEEKDAY}}":         "日",
    "{{TOTAL_CATEGORIES}}":      "4",
    "{{TOTAL_STORIES}}":         "20",
    "{{TOTAL_SECTIONS}}":        "5",
    "{{TOC_ROWS_HTML}}":         toc_html,
    "{{CATEGORIES_HTML}}":       categories_html,
    "{{REFLECTION_TITLE}}":      "値上げと組合化",
    "{{REFLECTION_SUBTITLE}}":   "プラットフォーム成熟期と軍事AI規制の断層線",
    "{{REFLECTION_LEAD_HTML}}":  (
        f"本日4分野・20本のニュースから浮かび上がる最大のテーマは{hl('AIの軍事化・民主化・商業化')}の三つ巴の緊張と、"
        f"それに抵抗する「{ul('労働者の組合化')}」と「プラットフォームの値上げ」という二つの「秩序形成」行動の同時進行である。以下、各カテゴリを横断して読み解く。"
    ),
    "{{REFLECTION_PULL_QUOTE_HTML}}": (
        f"「{hl('AI利用の線引き')}を市場でなく{ul('内部職員の投票')}が動かす日が来た——Google DeepMind組合化が問いかけるもの」"
    ),
    "{{REFLECTION_SECTIONS_HTML}}":  sec_html,
    "{{TAKEAWAYS_HTML}}":            tw_html,
    "{{RELATED_ISSUES_HTML}}":       rel_html,
}

for k, v in subs.items():
    html = html.replace(k, v)

OUT.write_text(html, encoding="utf-8")
size_kb = OUT.stat().st_size / 1024
print(f"OK email.html generated: {OUT} ({size_kb:.1f} KB)")
