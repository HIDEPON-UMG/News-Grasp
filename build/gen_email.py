"""generate build/email.html for 2026-05-03 digest"""
import re, pathlib, textwrap

ROOT = pathlib.Path(__file__).parent.parent
TMPL = (ROOT / "prompts" / "email-template.html").read_text(encoding="utf-8")
OUT  = ROOT / "build" / "email.html"

FX_ACC  = "#B8860B"
AI_ACC  = "#2D5BB8"
IT_ACC  = "#2E6B52"
GM_ACC  = "#5E3D8C"
CDN     = "https://raw.githubusercontent.com/HIDEPON-UMG/news-grasp-assets/main"

def hl(text, acc):
    """[[word]] → bold highlight, __word__ → underline"""
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
        f'{dt} &nbsp;·&nbsp; {src} &nbsp;·&nbsp; '
        f'<a href="{url}" style="color:#5C5A52;text-decoration:none;">元記事 →</a></div>'
    )

def title(text, url, acc):
    return (
        f'<h3 class="ng-card-title" style="font-size:20px;font-weight:800;line-height:1.4;'
        f'letter-spacing:-0.2px;margin:0 0 12px;color:#1A1A1A;">'
        f'<a href="{url}" style="color:#1A1A1A;text-decoration:none;border-bottom:2px solid {acc};">{text}</a></h3>'
    )

def related_tip(label, link_url, link_text):
    return (
        f'<div style="margin-top:14px;background:#F2EEE3;border-left:3px solid #C9B98A;padding:8px 12px;font-size:12px;color:#5C5A52;">'
        f'🔗 <strong>関連:</strong> {label} — '
        f'<a href="{link_url}" style="color:#5C5A52;">{link_text}</a></div>'
    )

def featured_card(score, dt, src, url, ttl, thumb, bullets, acc, tip_html=""):
    return f"""
<tr><td class="ng-card-pad" style="padding:24px 36px;background:#FAF7F0;border-bottom:1px solid #EDEAE3;">
  {meta(score, dt, src, url)}
  {title(ttl, url, acc)}
  <div class="ng-feature-img" style="margin-bottom:16px;">
    <img src="{thumb}" width="568" style="width:100%;max-height:280px;object-fit:cover;display:block;" alt="">
  </div>
  {bul(bullets, acc)}
  {tip_html}
</td></tr>"""

def side_card(score, dt, src, url, ttl, thumb, bullets, acc, tip_html=""):
    return f"""
<tr><td class="ng-card-pad" style="padding:18px 36px;background:#FAF7F0;border-bottom:1px solid #EDEAE3;">
  {meta(score, dt, src, url)}
  {title(ttl, url, acc)}
  <table role="presentation" class="ng-side-table" width="100%" cellpadding="0" cellspacing="0" border="0"><tbody><tr>
    <td class="ng-card-thumb" width="156" valign="top" style="padding-right:14px;">
      <img class="ng-card-thumb-img" src="{thumb}" width="140" height="90" style="width:140px;height:90px;object-fit:cover;display:block;" alt="">
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
        CATEGORY {idx} / {total} &nbsp;·&nbsp; {name_en_up}
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

def toc_row(glyph, acc, name, n):
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:8px;"><tbody><tr>'
        f'<td width="32" class="m" style="font-family:\'JetBrains Mono\',Consolas,\'Courier New\',monospace;font-size:14px;color:{acc};font-weight:700;">{glyph}</td>'
        f'<td style="font-size:14px;font-weight:700;">{name}</td>'
        f'<td align="right" class="m" style="font-family:\'JetBrains Mono\',Consolas,\'Courier New\',monospace;font-size:11px;color:#5C5A52;">{n} stories</td>'
        f'</tr></tbody></table>'
    )

def section(num, tag, heading, body_html, acc):
    return f"""
<tr><td class="ng-section-pad" style="background:#FAF7F0;padding:0 36px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-bottom:1px dashed #E2DED4;"><tbody><tr>
    <td class="ng-section-num-cell" width="80" valign="top" style="padding:28px 16px 28px 0;">
      <div class="m ng-section-num" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:38px;font-weight:900;color:{acc};line-height:0.9;letter-spacing:-2px;">§{num:02d}</div>
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
    <td width="20" align="right" style="color:#5C5A52;">→</td>
  </tr></tbody></table>
</td></tr>"""

# ── TOC ──
TOC = (
    toc_row("¥", FX_ACC, "為替 (Foreign Exchange)", 5) +
    toc_row("◆", AI_ACC, "AI (Artificial Intelligence)", 5) +
    toc_row("▲", IT_ACC, "IT-Consulting (IT &amp; Consulting)", 5) +
    toc_row("●", GM_ACC, "ゲーム (Gaming)", 5)
)

# ── CATEGORIES ──
PH_FX = f"{CDN}/ng-thumb-fx.jpg"
PH_AI = f"{CDN}/ng-thumb-common-ai.jpg"
PH_IT = f"{CDN}/ng-thumb-common-it.jpg"
PH_GM = f"{CDN}/ng-thumb-common-game.jpg"

CATS = ""

# ─ FX ─
CATS += cat_header(1, 4, "¥", "FOREIGN EXCHANGE", "為替", FX_ACC, 5,
    "GW最終日、為替介入後のUSD/JPYは155〜157円台で推移。追加介入リスクと日米金利差縮小シナリオが交差する中、GW明けの日銀6月会合が真の円安転換点として浮上している。")

CATS += featured_card(95, "2026-05-03 05:30", "野村証券（後藤祐二朗）",
    "https://www.nomura.co.jp/wealthstyle/article/0715/",
    "野村証券: 米ドル円急落 一時155円台 — 追加介入の可能性と効果の持続性が焦点",
    "https://www.nomura.co.jp/wealthstyle/article/0715/images/a_0715_01.png",
    [
        "[[USD/JPY]]が[[160.73円]]から[[155.57円]]へ急落。後藤祐二朗氏は「追加介入の可能性は相応にある」と指摘。2022年以降のパターンでは__3営業日以内に2回目の介入__が実施されており、GW明け週が最大の警戒ゾーン。",
        "[[日米金利差]]が3%以上で残存する間は構造的円安圧力が継続。__FRBの利下げか日銀の利上げ__なしに根本解決にならないとの見方が確認された。",
        "GW薄商い終了後のヘッジファンドポジション調整が本格化。[[フィボナッチ61.8%]]の[[155.50円]]維持が週明け最初のテクニカル焦点。",
    ], FX_ACC,
    related_tip("復状", "https://github.com/HIDEPON-UMG/News-Grasp/blob/main/digest/FX/2026-05-02-FX.md",
                "5/2号: 実弾介入直後分析"))

CATS += side_card(87, "2026-05-03 09:18", "外為どっとコム",
    "https://www.gaitame.com/media/entry/2026/05/01/091757",
    "外為どっとコム: ドル円急落！為替介入らしき動き 2026/5/1",
    "https://cdn-ak.f.st-hatena.com/images/fotolife/g/gaitamesk/20260501/20260501091656.png",
    [
        "[[160.73円]]から[[155.56円]]へ__約5円急落__。片山財務大臣・三村財務官が「断固たる措置を辞さない」と警告後に実弾介入。[[EUR/JPY]]・[[GBP/JPY]]も急騰。",
        "GW中の薄商い環境を狙った__コスト効率の高い__介入。2022〜2024年のパターンでは__初回から1週間以内に複数回__の介入が行われており、今週継続動向が焦点。",
    ], FX_ACC)

CATS += side_card(80, "2026-05-03 07:00", "マネクリ（マネックス証券）",
    "https://media.monex.co.jp/articles/-/28497",
    "マネクリ: 2026年は日米金利差縮小が円高への転換を招く",
    PH_FX,
    [
        "[[FRB]]追加利下げで[[日米金利差]]は今後1年で約1%縮小予想。__円高への転換点は金利差2%割れ__が鍵。",
        "[[日銀]]6月会合での追加利上げが有力視され、3名の反対票が次回会合の注目度を本年最高水準に押し上げている。",
    ], FX_ACC)

CATS += side_card(75, "2026-05-03 10:00", "ORICON NEWS",
    "https://life.oricon.co.jp/rank-foreign-currency-deposits/special/weak-yen/how-long-yen-low/",
    "oricon: 2026年5月 円安はいつまで続く？今後の見通しと対策",
    PH_FX,
    [
        "[[155〜160円台]]で推移する円安の背景と個人向け対策。日米金利差・原油高・地政学の三重苦で__長期化する新常態__として定着しつつある。",
    ], FX_ACC)

CATS += side_card(70, "2026-05-03 08:30", "EBC Financial Group",
    "https://www.ebc.com/jp/forex/280960.html",
    "EBC Financial: 2026年ドル円の予想 — 日米金利・金融政策から読む為替の行方",
    PH_FX,
    [
        "2026年の[[USD/JPY]]は[[155〜165円]]のレンジ予測。過去の介入実績では__1〜3ヶ月で元の水準に戻る傾向__が確認されており、構造的円安の長期継続リスクが定説化している。",
    ], FX_ACC)

# ─ AI ─
CATS += cat_header(2, 4, "◆", "ARTIFICIAL INTELLIGENCE", "AI", AI_ACC, 5,
    "Pentagonが5月1日、OpenAI/Google/NVIDIA等7社と分類AI契約を締結しAnthropicを「サプライチェーンリスク」として除外。Sonnet 4.8のソースコードリークも重なり、安全guardrailと軍事AI活用の折り合いが業界最大の焦点に浮上した。")

CATS += featured_card(95, "2026-05-03 08:00", "CNN Business",
    "https://www.cnn.com/2026/05/01/tech/pentagon-ai-anthropic",
    "Pentagon、7社と分類AI契約締結 — Anthropicを「サプライチェーンリスク」に認定し除外",
    f"{CDN}/ng-thumb-ai.jpg",
    [
        "[[米国防総省]]が[[SpaceX]]・[[OpenAI]]・[[Google]]・[[NVIDIA]]・[[Microsoft]]・[[AWS]]・[[Reflection]]・[[Oracle]]の8社と分類AI契約を締結。[[Anthropic]]は安全ガードレール要求で「__サプライチェーンリスク__」として除外された。",
        "[[ダリオ・アモデイ]]CEOはホワイトハウス協議継続中。[[トランプ]]大統領は「__合意は可能__」と言及したが自律型兵器へのAI活用制約を巡る溝は深い。",
        "ペンタゴン除外は軍事AI活用における安全guardrailと戦略的優位性のトレードオフを業界全体に突きつけた。国内AI企業への初適用という前例が、__今後の米国AI政策の標準__を形成する。",
    ], AI_ACC,
    related_tip("復状", "https://github.com/HIDEPON-UMG/News-Grasp/blob/main/digest/AI/2026-04-30-AI.md",
                "4/30号: Google DoD単独契約から7社一括締結へ"))

CATS += side_card(86, "2026-05-03 09:15", "AI Nexus Daily",
    "https://www.ainexusdaily.com/article/anthropic-source-leak-claude-sonnet-4-8-undercover-mode",
    "Anthropicソースコードリークで Claude Sonnet 4.8 と「潜伏モード」が判明",
    PH_AI,
    [
        "Claude Code npmパッケージのリークで[[Claude Sonnet 4.8]]の存在が判明。[[Opus 4.7]]（4/16）のパターンからSonnet 4.8は__5月下旬〜6月__が有力。",
        "リークコードに「__undercover mode（潜伏モード）__」の痕跡。自身がAIであることを開示せずに会話できる特殊モードで、企業向けペルソナ機能の一環と見られる。",
    ], AI_ACC)

CATS += side_card(82, "2026-05-03 06:00", "Releasebot.io",
    "https://releasebot.io/updates/anthropic",
    "Anthropic 2026年モデルロードマップ — Opus 4.7リリース済み、Sonnet 4.8が射程圏内",
    PH_AI,
    [
        "[[Opus 4.6]]がMicrosoft PowerPoint・ExcelアドインとしてMS Officeに初統合。[[Opus 4.7]]でコード生成・数学的推論が大幅向上。[[約2ヶ月毎のリリースペース]]が鮮明に。",
        "[[Gemini 3.1 Flash-Lite]]が2.5倍速、[[GPT-5.4]]がOSWorld-V 75%達成。__三社リリースラッシュ__が半年以内に消費者・企業の乗り換えを加速。",
    ], AI_ACC)

CATS += side_card(77, "2026-05-03 07:30", "LLM Stats",
    "https://llm-stats.com/llm-updates",
    "AI Updates May 2026 — Google Gemini 7.5億MAU、GPT-5.4・Gemini 3.1の競演",
    PH_AI,
    [
        "[[Google Gemini]] [[7.5億MAU]]、[[Copilot]] 1.5億、[[Claude]] 1800〜3000万。__ユーザー規模の格差__がプラットフォーム持続性を左右。",
        "旧モデルのデプリケーション（6/15）でモデルサイクル高速化への対応コストが顕在化。__AI導入障壁__として新たに浮上。",
    ], AI_ACC)

CATS += side_card(70, "2026-05-03 06:30", "MIT Technology Review",
    "https://www.technologyreview.com/2026/03/02/1133850/openais-compromise-with-the-pentagon-is-what-anthropic-feared/",
    "MIT Tech Review: OpenAIのPentagon「妥協的合意」はAnthropicが最も恐れていた事態",
    PH_AI,
    [
        "3月の予測通り、[[OpenAI]]のペンタゴン\"妥協的合意\"が[[Anthropic]]排除を5/1に現実にした。__企業が安全原則を保ちつつ国防省と取引できる上限__が可視化された。",
        "MIT Tech Reviewは「AnthropicのリスクはAI倫理よりも__ビジネスの孤立__にある」と指摘。Anthropicが次に打つ外交的解決策の行方が注目される。",
    ], AI_ACC)

# ─ IT ─
CATS += cat_header(3, 4, "▲", "IT &amp; CONSULTING", "IT-Consulting", IT_ACC, 5,
    "Accenture Q2 FY2026が$22.1Bの過去最高受注を記録し、BCGのAI収益が全売上の25%に到達。コンサル業界のAIシフトが「期待値」から「実績値」に変わり、McKinsey/PwC/KPMGのAIエージェント基盤競争が本格化している。")

CATS += featured_card(90, "2026-05-03 07:00", "Investing.com",
    "https://www.investing.com/news/transcripts/earnings-call-transcript-accenture-q2-2026-beats-forecasts-but-stock-dips-93CH-4570789",
    "Accenture Q2 FY2026 beats forecasts — $18.0B収益・$22.1B過去最高受注を達成",
    "https://i-invdn-com.investing.com/news/LYNXMPEA7H0NX_L.jpg",
    [
        "[[Accenture]]のQ2 FY2026収益は[[180億ドル]]、予想比0.95%上振れでUSDベース[[8%成長]]。四半期受注は[[221億ドル]]と過去最高、[[1億ドル]]超クライアントが[[41社]]と過去最多。",
        "生成AI関連の累計受注はFY2025下半期比で__倍増ペース__で推移。AIエージェント・ERPモダナイゼーション・クラウド移行が牽引し、マネージドサービスが__前年比+10%__と高成長。",
        "株価は発表後一時下落。通期ガイダンス据え置きとFederal事業縮小（約1%影響）への懸念が重荷。ただし__AI受注増は構造的__であり中長期のトレンドは変わらずとの見方が大勢。",
    ], IT_ACC,
    related_tip("復状", "https://github.com/HIDEPON-UMG/News-Grasp/blob/main/digest/IT-Consulting/2026-05-02-IT-Consulting.md",
                "5/2号: Accenture AI投資戦略の背景がQ2実績として裏付け"))

CATS += side_card(84, "2026-05-03 08:30", "Metaintro / Bloomberg",
    "https://www.metaintro.com/blog/bcg-25-percent-ai-revenue-consulting-jobs-2026",
    "BCGのAI収益が全売上の25%（$3.6B）に — 「コンサル業界史上最大の転換」",
    PH_IT,
    [
        "[[BCG]]の2025年総収益[[144億ドル]]のうち[[36億ドル]]（[[25%]]）が[[AI関連コンサル]]。Bloomberg 4/23報道で「コンサル業界史上最大のシフト」と評価。",
        "[[BCG Gamma]]プラットフォームが受注増を牽引。大手3社（Accenture・McKinsey・BCG）のAI関連売上合計は__年間100億ドル超__と推定。__「人員削減と収益成長が同時進行」__する構造的転換期に入った。",
    ], IT_ACC)

CATS += side_card(78, "2026-05-03 10:00", "White Hat SEO",
    "https://whitehat-seo.co.uk/blog/ai-impact-on-consulting",
    "2026年のコンサルAI競争 — McKinsey「Agents-at-Scale」・PwC「Agent OS」・KPMGが激突",
    PH_IT,
    [
        "[[McKinsey]] 「[[Agents-at-Scale]]」・[[PwC]] 「[[Agent OS]]」・[[KPMG]] 「[[Workbench]]」が相次いで発表。AIエージェント基盤の自社開発がコンサル競争の__新フロンティア__に。",
        "BCGの試算でエージェントAIが2028年に全AI価値の[[29%]]を占める予測。__「単体AIアシスタント」→「複数エージェント協調」__へのシフトが加速。",
    ], IT_ACC)

CATS += side_card(72, "2026-05-03 09:30", "Plus AI",
    "https://plusai.com/blog/how-consulting-firms-use-ai",
    "大手コンサルのAI活用完全比較 — McKinsey/Accenture/Deloitte/EYの戦略を解剖",
    PH_IT,
    [
        "[[McKinsey]]の内部AI「LillyRose」でコンサルタントの__「付加価値時間」を60%向上__と公称。[[Accenture]] AI Refineryは1000社超に展開済み。",
        "「AI導入でコンサル不要論 vs AI導入支援でコンサル需要増大」という__逆説が業界を定義__。短期は後者が勝っているが5年スパンでの雇用モデルへの影響は避けられない。",
    ], IT_ACC)

CATS += side_card(65, "2026-05-03 06:00", "ResearchAndMarkets / BusinessWire",
    "https://www.businesswire.com/news/home/20251124215144/en/AI-Consulting-and-Support-Services-Analysis-Report-2025-2032-with-Market-Positioning-of-Key-Companies---Accenture-IBM-Deloitte-Touche-Tohmatsu-PwC-EY-McKinsey-Co-BCG-Tata-Consultancy---ResearchAndMarkets.com",
    "AI Consulting市場 2025-2032 — Accenture/IBM/Deloitte/PwC/EYが60%以上を独占",
    PH_IT,
    [
        "AI consulting市場は2025年[[500億ドル]]から年率__18.5%成長__予測。上位5社で__60%以上を独占__する構造が続く見通し。",
        "日本市場はDX推進政策と労働力不足でAI投資が__世界平均を上回るペース__で拡大中。NTTデータ・富士通・NECとのコラボレーションが活発化している。",
    ], IT_ACC)

# ─ Game ─
CATS += cat_header(4, 4, "●", "GAMING", "ゲーム", GM_ACC, 5,
    "Switch 2の5月は6大タイトル（Indiana Jones・Yoshi等）が並ぶ豊作月。カプコンGWセール21タイトルとeショップGWセールが同時開催され、ハード購入後の「ソフト積み増し」の最初の山場を迎えた。")

CATS += featured_card(90, "2026-05-03 09:00", "Game Rant",
    "https://gamerant.com/nintendo-switch-2-new-games-coming-out-soon-list-may-2026/",
    "Nintendo Switch 2に5月だけで6大タイトル — Indiana Jones・Yoshi・Strayが上陸",
    "https://static0.gamerantimages.com/wordpress/wp-content/uploads/2026/04/switch-2-games-releasing-may-2026.jpg",
    [
        "[[Switch 2]]の5月ラインナップは[[6タイトル]]確定：[[Mixtape]]（5/7）・[[Indiana Jones and the Great Circle]]（5/12）・[[Yoshi and the Mysterious Book]]（5/21）・[[Stray]]（5/28）など。任天堂唯一の独占[[Yoshi]]を含む豊作月。",
        "5月の多くは__サードパーティ・マルチプラットフォーム作品__がSwitch 2に参入。任天堂以外のパブリッシャーの自発的参入意欲の高さがSwitch 2普及を後押し。",
        "[[Indiana Jones]]は5/12リリース。海外での知名度の高さからGW以降の欧米販売回復の__起爆剤__として期待。任天堂FY2026通期見通し[[1900万台]]達成の鍵を握る。",
    ], GM_ACC,
    related_tip("復状", "https://github.com/HIDEPON-UMG/News-Grasp/blob/main/digest/Game/2026-05-02-Game.md",
                "5/2号: 欧米不振で株価12%急落との復状"))

CATS += side_card(83, "2026-05-03 08:00", "Nintendo Insider",
    "https://www.nintendo-insider.com/2026-capcom-golden-week-sale-discounts-21-nintendo-switch-games/",
    "カプコン GWセール — 21 Nintendo Switchタイトルが最大80%オフ",
    PH_GM,
    [
        "[[カプコン]]がGWセール（4/28〜5/11）で[[21タイトル]]を最大[[80%オフ]]で提供。バイオハザード Village・モンスターハンター ワイルズ等の人気シリーズが対象。",
        "Switch 2後方互換でSwitch 1タイトルが即プレイ可能なため、__Switch 2ユーザーへのライブラリ拡充誘導__として機能。カプコンの新旧同時展開戦略が鮮明に。",
    ], GM_ACC)

CATS += side_card(78, "2026-05-03 07:30", "Nintendo Life",
    "https://www.nintendolife.com/guides/upcoming-nintendo-switch-2-games-and-accessories-for-may-and-june-2026",
    "Switch 2 5〜6月の全ラインナップ — Tales of Arise・FF7リバース・The Duskbloods",
    PH_GM,
    [
        "5〜6月は[[Tales of Arise BtDE]]（5/22）・[[FF7リバース]]（6/3）・[[The Duskbloods]]（任天堂×フロム）など__JRPGと高難度アクションの波__が控える。",
        "5〜6月は「ハードウェア購入層の__ソフト積み増し__」の最初の山場。月次販売データが任天堂FY2026後半の見通しを左右する重要局面となる。",
    ], GM_ACC)

CATS += side_card(73, "2026-05-03 10:00", "ファミ通",
    "https://www.famitsu.com/schedule/switch2",
    "ファミ通: 2026年5月 Switch 2 発売スケジュール — 25本が一挙リリース",
    PH_GM,
    [
        "5月Switch 2発売予定は[[25本]]。GW直後（5/7〜）から毎週コンスタントにリリースが続き、インディーズタイトルも__3〜4本/週ペース__を維持。",
        "Switch 2の[[GameChat機能]]を活かした__コミュニティ前提の設計__が業界全体に定着しつつある。",
    ], GM_ACC)

CATS += side_card(67, "2026-05-03 09:30", "ゲームウィズ",
    "https://gamewith.jp/switch/article/show/1513",
    "eショップ GWセール 5月 — 500本以上・カプコン/スクエニ/集英社が参加",
    PH_GM,
    [
        "任天堂・サードパーティ合計[[500本以上]]が対象のGWセール（4/28〜5/11）開催中。カプコン・スクウェア・エニックス・集英社ゲームズが参加。",
        "次回大型セールは[[夏セール]]（7月予定）か。__年間を通じた価格プロモーション__がeショップの恒常的な集客手段として定着しつつある。",
    ], GM_ACC)

# ── REFLECTION SECTIONS ──
SECS = ""
SECS += section(1, "OVERVIEW", "「制限」が新たな競争軸になった日",
    hl("今日の4カテゴリに共通するのは「何かが制限された」という構図だ。[[USD/JPY]]は[[160円]]に上限が設けられ、[[Anthropic]]は[[Pentagon]]市場から締め出され、[[Switch 2]]は欧米普及に苦戦し、[[コンサル業界]]は従来型の雇用モデルを縮小している。しかし制限は悲観を意味しない。為替介入は新たな均衡を模索する過程であり、Anthropicの孤立はAI安全性原則の価値を証明し、Switch 2の苦戦は5月ソフト攻勢によって覆されようとしている。__制限の裏側に加速が隠れている__局面だ。", "#1A1A1A"),
    "#1A1A1A")

SECS += section(2, "FX/ECONOMY", "GW最終日、155円台と6月の日銀という二重の焦点",
    hl("[[日本政府]]が[[34.5億ドル]]規模の介入で[[160円台]]に蓋をした。しかし[[野村証券]]の[[後藤祐二朗]]が指摘するように、追加介入リスクが残る中でも__構造的円安の根本は変わっていない__。FRBの利下げサイクルが進み[[日米金利差]]が縮小するまで円安基調は継続する。GW明け週のヘッジファンドポジション調整と、6月の[[日銀]]政策会合——その二重の焦点が為替市場の次の方向性を決める。「__時間を買った__」介入の効果持続性が今週の最大のテーマだ。", FX_ACC),
    FX_ACC)

SECS += section(3, "AI/TECH", "Pentagonのゲートキーパー化とAnthropicの孤立",
    hl("[[Pentagon]]が[[OpenAI]]・[[Google]]・[[NVIDIA]]ら7社と契約し[[Anthropic]]を除外したことは、AI業界の地政学的再編を象徴する出来事だ。「[[サプライチェーンリスク]]」という前例のないラベルを国内AI企業に貼り付けるという手法は、__安全原則への固執がビジネス的孤立につながる__という前例を作った。一方で[[Sonnet 4.8]]のリークが示すように、Anthropicの技術競争力は損なわれていない。[[ダリオ・アモデイ]]がどこで「妥協」の線引きをするか——それが2026年後半のAI業界の分水嶺になる。", AI_ACC),
    AI_ACC)

SECS += section(4, "INDUSTRY", "コンサルのAI収益が「期待」から「数字」に変わった",
    hl("[[Accenture]]のQ2過去最高受注[[221億ドル]]と[[BCG]]のAI収益[[25%]]（[[36億ドル]]）は、コンサル業界のAIシフトが__「戦略発表」から「実績」のフェーズに入った__ことを示す。同時に[[McKinsey]]・[[PwC]]・[[KPMG]]がAIエージェント基盤の競争を激化させており、「__どのファームが最もAIを使いこなせるか__」という競争軸が明確になってきた。人員削減と収益成長の同時進行という矛盾は、価値の所在が変わったことへの適応であり、コンサル業の終わりではない。", IT_ACC),
    IT_ACC)

SECS += section(5, "OUTLOOK", "GW明けの三叉路：介入・Pentagon・Switch 2攻勢",
    hl("GW明け（5/4〜）に三つの注目点が重なる。①[[USD/JPY]]が[[155〜157円]]を維持できるか、追加介入が入るか。②[[Anthropic]]とPentagonの協議が進展するか、または[[Sonnet 4.8]]が5月中にリリースされてAI競争の焦点が技術に戻るか。③[[Indiana Jones and the Great Circle]]（5/12）が欧米Switch 2販売の火付け役になるか——__この三点が5月前半の市場センチメントを決定する__。「制限と加速」のどちらが勝つかは、今週末までにおおよその答えが出る。", "#C9B98A"),
    "#C9B98A")

# ── TAKEAWAYS ──
TAKE = ""
TAKE += takeaway("01", FX_ACC, "FX · 為替",
    hl("[[為替介入]]で[[160円]]が当面の天井となったが構造的円安は継続。[[FRB]]利下げ・[[日銀]]利上げという二つの条件が揃うまで円高転換は幻想。GW明けの__6月日銀会合が真の分岐点__。", FX_ACC))

TAKE += takeaway("02", AI_ACC, "AI · 人工知能",
    hl("[[Pentagon]]のAnthropicへの「[[サプライチェーンリスク]]」認定はAI業界最大の地政学的イベント。__安全guardrailと軍事活用の折り合い__が今後の業界標準を決める。Anthropicの次の一手に注目。", AI_ACC))

TAKE += takeaway("03", IT_ACC, "CONSULTING · 産業",
    hl("[[Accenture]] Q2過去最高受注+[[BCG]] AI収益25%——コンサルのAI特需が「__予測から実績へ__」。McKinsey/PwC/KPMGのAIエージェント基盤競争が本格化し、ファームの価値基準が「人員数」から「AIの実装力」へ移行中。", IT_ACC))

# ── RELATED ──
REL = ""
REL += related_row("2026-05-02",
    "https://github.com/HIDEPON-UMG/News-Grasp/blob/main/digest/Summary/2026-05-02.md",
    "前回号: 守りながら攻める——介入・制限・リストラが映す転換期")
REL += related_row("2026-04-30",
    "https://github.com/HIDEPON-UMG/News-Grasp/blob/main/digest/Summary/2026-04-30.md",
    "FOMC分裂と円安の潮目——GW前夜の市場圧力")

# ── REFLECTION LEAD & PULL QUOTE ──
LEAD = hl(
    "本日4分野・20本のニュースから浮かび上がる最大のテーマは[[AI地政学の再編]]と[[GW後の相場再起動]]の同時進行である。PentagonがAnthropicを排除し、為替介入が160円に蓋をし、コンサルが数字でAI特需を証明し、Switch 2が5月ソフト攻勢で欧米回復を狙う。__制限する力と加速する力が同じ24時間の中に共存した日__だ。",
    "#C9B98A")

PULL = hl(
    "AIに安全ガードレールを課すことは「商業的孤立」を意味するのか——[[Anthropic]]の除外が示した問いに、業界全体が向き合わされている。__技術の最高値と倫理の最低線__は、同じ平面に引けるのか。",
    "#8E2A19")

# ── ASSEMBLE ──
html = TMPL
html = html.replace("{{ISSUE_NO}}", "20260503")
html = html.replace("{{ISSUE_DATE}}", "2026-05-03")
html = html.replace("{{ISSUE_WEEKDAY}}", "日")
html = html.replace("{{TOTAL_CATEGORIES}}", "4")
html = html.replace("{{TOTAL_STORIES}}", "20")
html = html.replace("{{TOTAL_SECTIONS}}", "5")
html = html.replace("{{TOC_ROWS_HTML}}", TOC)
html = html.replace("{{CATEGORIES_HTML}}", CATS)
html = html.replace("{{REFLECTION_TITLE}}", "AI地政学の再編と<br>GW後の相場再起動")
html = html.replace("{{REFLECTION_SUBTITLE}}", "Pentagon contract · yen slide · consulting AI · console war")
html = html.replace("{{REFLECTION_LEAD_HTML}}", LEAD)
html = html.replace("{{REFLECTION_PULL_QUOTE_HTML}}", PULL)
html = html.replace("{{REFLECTION_SECTIONS_HTML}}", SECS)
html = html.replace("{{TAKEAWAYS_HTML}}", TAKE)
html = html.replace("{{RELATED_ISSUES_HTML}}", REL)

OUT.write_text(html, encoding="utf-8")
size = OUT.stat().st_size
print(f"Generated {OUT} ({size:,} bytes)")
