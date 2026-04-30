"""
generate_email.py — prompts/email-template.html のプレースホルダを展開して
build/email.html を生成する。
"""
import re, os, pathlib

BASE = pathlib.Path(__file__).parent.parent
TEMPLATE = BASE / "prompts" / "email-template.html"
OUTPUT   = BASE / "build" / "email.html"
CDN      = "https://raw.githubusercontent.com/HIDEPON-UMG/news-grasp-assets/main/"

# ── ヘルパー ───────────────────────────────────────────────
def hl(text, accent):
    """[[term]] と __text__ を HTML に変換"""
    text = re.sub(r'\[\[(.+?)\]\]',
        lambda m: f'<strong style="background:{accent}22;padding:0 3px;border-radius:2px;">{m.group(1)}</strong>',
        text)
    text = re.sub(r'__(.+?)__',
        lambda m: f'<span style="border-bottom:2px solid {accent};padding-bottom:1px;">{m.group(1)}</span>',
        text)
    return text

def bullets(items, accent):
    rows = []
    for item in items:
        rows.append(
            f'<p class="bul" style="padding-left:20px;margin:0 0 8px;font-size:14.5px;'
            f'line-height:1.9;color:#1A1A1A;">{hl(item, accent)}</p>'
        )
    return "\n".join(rows)

def meta_line(score, label, source, url, accent):
    badge = f'<span class="m b7" style="font-family:\'JetBrains Mono\',Consolas,\'Courier New\',monospace;font-size:11px;background:{accent};color:#fff;padding:2px 8px;">{label}</span>'
    sc    = f'<span class="m" style="font-family:\'JetBrains Mono\',Consolas,\'Courier New\',monospace;font-size:10px;color:#5C5A52;margin-left:8px;">SCORE {score}</span>'
    src   = f'<a href="{url}" style="font-family:\'JetBrains Mono\',Consolas,\'Courier New\',monospace;font-size:10px;color:#5C5A52;text-decoration:none;">{source} ↗</a>'
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:12px;">'
        '<tbody><tr>'
        f'<td>{badge}{sc}</td>'
        f'<td align="right">{src}</td>'
        '</tr></tbody></table>'
    )

def top_card(score, title, source, url, img, buls, accent):
    """TOP FEATURED カード（全幅画像付き）"""
    return (
        '<tr><td class="ng-card-pad" style="padding:24px 36px;background:#FAF7F0;border-bottom:1px solid #EDEAE3;">'
        + meta_line(score, "TOP", source, url, accent)
        + f'<h3 class="ng-card-title" style="font-size:20px;font-weight:800;line-height:1.4;margin:0 0 16px;color:#1A1A1A;">{title}</h3>'
        + f'<div class="ng-feature-img" style="margin-bottom:16px;">'
          f'<img src="{img}" width="568" alt="" style="width:100%;height:auto;display:block;max-height:240px;object-fit:cover;"></div>'
        + bullets(buls, accent)
        + '</td></tr>'
    )

def side_card(score, title, source, url, img, buls, accent, label=None):
    """通常カード（サイドサムネ）。スマホ時は @media で画像→本文の縦積みに切替（ng-card-thumb / ng-card-body-cell が連動）"""
    lbl = label or f"0{score}" if score < 100 else str(score)
    return (
        '<tr><td class="ng-card-pad" style="padding:18px 36px;background:#FAF7F0;border-bottom:1px solid #EDEAE3;">'
        '<table role="presentation" class="ng-side-table" width="100%" cellpadding="0" cellspacing="0" border="0"><tbody><tr>'
        f'<td class="ng-card-thumb thb vtop pr16" width="140" style="width:140px;vertical-align:top;padding-right:16px;">'
        f'<img class="ng-card-thumb-img" src="{img}" width="140" height="90" alt="" style="width:140px;height:90px;object-fit:cover;display:block;"></td>'
        '<td class="ng-card-body-cell vtop" style="vertical-align:top;">'
        + meta_line(score, lbl, source, url, accent)
        + f'<h3 class="ng-card-title" style="font-size:17px;font-weight:800;line-height:1.4;margin:0 0 12px;color:#1A1A1A;">{title}</h3>'
        + bullets(buls, accent)
        + '</td></tr></tbody></table>'
        '</td></tr>'
    )

def cat_header(idx, total, name_en, name_jp, glyph, accent, summary, count):
    return (
        f'<tr><td class="ng-cat-pad" style="background:{accent};padding:20px 36px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tbody><tr>'
        '<td style="vertical-align:middle;">'
        f'<div class="m" style="font-family:\'JetBrains Mono\',Consolas,\'Courier New\',monospace;font-size:10px;color:rgba(255,255,255,0.7);letter-spacing:2px;margin-bottom:4px;">CATEGORY {idx} / {total} · {name_en.upper()}</div>'
        f'<div class="ng-cat-name" style="font-size:28px;font-weight:800;color:#fff;letter-spacing:-0.5px;line-height:1.1;">'
        f'<span class="m" style="font-family:\'JetBrains Mono\',Consolas,\'Courier New\',monospace;margin-right:10px;">{glyph}</span>{name_jp}</div>'
        '</td>'
        f'<td align="right" style="vertical-align:middle;color:rgba(255,255,255,0.85);font-family:\'JetBrains Mono\',Consolas,\'Courier New\',monospace;font-size:11px;">{count} stories</td>'
        '</tr></tbody></table>'
        f'<div class="ng-cat-summary" style="font-size:13px;color:rgba(255,255,255,0.95);font-style:italic;line-height:1.6;margin-top:10px;padding-top:10px;border-top:1px solid rgba(255,255,255,0.25);">{summary}</div>'
        '</td></tr>'
    )

def toc_row(num, name_jp, count, accent):
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:4px;"><tbody><tr>'
        f'<td width="32" class="m" style="font-family:\'JetBrains Mono\',Consolas,\'Courier New\',monospace;font-size:12px;color:{accent};font-weight:700;">{num}.</td>'
        f'<td style="font-size:14px;font-weight:600;">{name_jp}</td>'
        f'<td align="right" class="m" style="font-family:\'JetBrains Mono\',Consolas,\'Courier New\',monospace;font-size:11px;color:#5C5A52;">{count} stories</td>'
        '</tr></tbody></table>'
    )

def section_row(num, tag, heading, body_html, accent):
    return (
        '<tr><td class="ng-section-pad" style="background:#FAF7F0;padding:0 36px;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-bottom:1px dashed #E2DED4;"><tbody><tr>'
        f'<td class="ng-section-num-cell" width="80" valign="top" style="padding:28px 16px 28px 0;vertical-align:top;">'
        f'<div class="m ng-section-num" style="font-family:\'JetBrains Mono\',Consolas,\'Courier New\',monospace;font-size:38px;font-weight:900;color:{accent};line-height:0.9;letter-spacing:-2px;">§{num}</div>'
        f'<div class="m" style="font-family:\'JetBrains Mono\',Consolas,\'Courier New\',monospace;font-size:9px;color:#fff;background:{accent};padding:2px 6px;display:inline-block;letter-spacing:1.5px;margin-top:8px;">{tag}</div>'
        '</td>'
        f'<td class="ng-section-text-cell" valign="top" style="padding:28px 0 28px 20px;border-left:1px solid #E2DED4;vertical-align:top;">'
        f'<h3 class="ng-section-heading" style="font-size:18px;font-weight:800;margin:0 0 14px;color:#1A1A1A;">{heading}</h3>'
        f'<div class="ng-section-body" style="font-size:13.5px;line-height:2.0;color:#1A1A1A;">{body_html}</div>'
        '</td>'
        '</tr></tbody></table>'
        '</td></tr>'
    )

def takeaway_card(num, tag, text_html, accent):
    return (
        '<tr><td style="padding-bottom:12px;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#fff;border:1px solid #E2DED4;"><tbody><tr>'
        f'<td width="56" valign="middle" class="m" style="background:{accent};color:#fff;text-align:center;font-family:\'JetBrains Mono\',Consolas,\'Courier New\',monospace;font-size:18px;font-weight:900;padding:14px 0;">{num}</td>'
        '<td style="padding:12px 16px;">'
        f'<div class="m" style="font-family:\'JetBrains Mono\',Consolas,\'Courier New\',monospace;font-size:9px;color:{accent};font-weight:700;letter-spacing:1.5px;margin-bottom:4px;">{tag}</div>'
        f'<div style="font-size:13px;line-height:1.7;font-weight:600;">{text_html}</div>'
        '</td>'
        '</tr></tbody></table>'
        '</td></tr>'
    )

def related_row(date, title, url):
    return (
        '<tr><td style="padding:10px 0;border-bottom:1px solid #E2DED4;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tbody><tr>'
        f'<td width="100" class="m" style="font-family:\'JetBrains Mono\',Consolas,\'Courier New\',monospace;font-size:11px;color:#5C5A52;">{date}</td>'
        f'<td style="font-size:13px;font-weight:600;"><a href="{url}" style="color:#1A1A1A;text-decoration:none;">{title}</a></td>'
        '<td width="20" align="right" style="color:#5C5A52;">→</td>'
        '</tr></tbody></table>'
        '</td></tr>'
    )

# ── コンテンツ定義 ─────────────────────────────────────────

FX = "#B8860B"
AI = "#2D5BB8"
IT = "#2E6B52"
EC = "#8E2A19"
GM = "#5E3D8C"
CDN_FX = CDN + "ng-thumb-common-fx.jpg"
CDN_AI = CDN + "ng-thumb-common-ai.jpg"
CDN_IT = CDN + "ng-thumb-common-it.jpg"
CDN_EC = CDN + "ng-thumb-common-economy.jpg"
CDN_GM = CDN + "ng-thumb-common-game.jpg"

# ── FX ────────────────────────────────────────────────────
fx_block = (
    cat_header(1, 5, "Foreign Exchange", "為替 (Foreign Exchange)", "¥", FX,
        "FOMC8-4分裂での据え置きを受けてUSD/JPYは159.6台で円安継続。財務相が160円接近に口先介入トーンを強化し、GW薄商い相場での実弾介入警戒が高まる。", 5)
    + top_card(95, "FOMC 8-4分裂で金利据え置き — タカ派3名が緩和バイアス削除要求、USD/JPY下落反応は限定的",
        "CNBC", "https://www.cnbc.com/2026/04/29/fed-holds-rates-three-officials-dissent-against-easing-bias.html",
        CDN + "ng-thumb-fx.jpg",
        [
            "FOMCが[[8-4の分裂採決]]で金利を据え置き（3.50〜3.75%）。タカ派3名が声明から緩和バイアス文言の削除を要求し、内部分裂が初めて可視化された歴史的な会合となった。",
            "USD/JPYはFOMC直後に159.6台で変動幅が限定的。__据え置きは既に完全織り込み済み__のため「ノーサプライズ反応」となり、次の焦点は5月雇用統計に移った。",
            "ただしFOMCの分裂は「次の利下げがいつか」の不確実性を高め、[[長期金利]]が小幅上昇。リスク資産と円安継続のバランスを崩しかねない潜在リスクとして残る。",
        ], FX)
    + side_card(88, "USD/JPY 159.6台 FOMC後の動き — 据え置き織り込み済みで円安継続、次の焦点は5月雇用統計",
        "Bloomberg", "https://www.bloomberg.com/news/articles/2026-04-29/dollar-yen-fomc-rate-hold",
        CDN_FX,
        [
            "FOMC据え置き後もUSD/JPYは159.6台で推移。市場コンセンサスの据え置きが完全に価格に織り込まれており、__為替のボラティリティは急低下__した。",
            "次の為替転換点として5月第1週発表予定の[[米雇用統計]]（非農業部門雇用者数・失業率）が浮上。予想を下回れば利下げ期待が再熱し円高シフトの可能性。",
            "GW連休中の東京市場休場（4/30〜5/6）で流動性が低下し、少量のオーダーで[[大幅変動]]するリスクあり。",
        ], FX, "02")
    + side_card(85, "財務相カタヤマ「過度な変動は看過できない」— 160円接近で口先介入トーン強化、実弾介入の閾値は",
        "Mitrade", "https://www.mitrade.com/jp/insights/forex/usd-jpy/minister-yen-warning-2026",
        CDN_FX,
        [
            "片山財務相が「[[過度な変動]]は看過できない」と口先介入を強化。159円後半でこのトーンに達したことは、当局が160円をレッドラインとして意識していることを示唆。",
            "2024年4月・2024年10月の介入実績では、160円突破後に5〜10兆円規模の__実弾介入__が実施された。GW中の介入は流動性の薄い相場での効果が大きい。",
            "「[[フリーハンドがある]]」という財務省の常套句も再登場。米財務省との事前協議が完了していれば単独介入の可能性が高まる。",
        ], FX, "03")
    + side_card(75, "MUFG 4月月次アウトルック — USD/JPY 2027Q1に152円予想、米利下げ再開が円高シフトの鍵",
        "MUFG", "https://www.mufg.jp/gcib/mufgri/fxweekly/index.html",
        CDN_FX,
        [
            "MUFGが4月月次FXアウトルックを更新。2027年Q1のUSD/JPY予想を[[152円]]と設定。現在の159円台からの7円幅の円高転換を見込む。",
            "前提条件はFRBの利下げ再開（2026年9月開始予想）。タカ派化で利下げ後ずれなら__円高シフトの時期も後ずれ__するリスク。",
            "対EUR/JPYは年末180円を予想。欧州の利下げペースが日本より速いため、[[クロス円]]での円高進行は限定的と見る。",
        ], FX, "04")
    + side_card(70, "豪CPI鈍化でAUD/USD下落 — 1Q CPI+2.4%でRBA利下げ観測が再浮上、キャリートレード巻き戻しに波及",
        "外為どっとコム", "https://www.gaitame.com/media/entry/2026/04/30/australia-cpi-aud-usd",
        CDN_FX,
        [
            "オーストラリアの2026年1QCPI（+2.4%）が市場予想の+2.6%を下回り、[[RBA]]の5月利下げ観測が急浮上。AUD/USDが0.6420台まで下落。",
            "AUD/JPYでのキャリートレード（低金利の円を売り、高金利の豪ドルを買う戦略）の巻き戻しが発生。__円高方向への需要が短期的に高まる__構図。",
            "RBAが実際に5月に利下げすれば、[[EUR/AUD]]や[[USD/AUD]]での豪ドル売り圧力が継続し、コモディティ通貨全体の軟調が続く見通し。",
        ], FX, "05")
)

# ── AI ────────────────────────────────────────────────────
ai_block = (
    cat_header(2, 5, "Artificial Intelligence", "AI (Artificial Intelligence)", "◆", AI,
        "OpenAIがCustom GPTを廃止しWorkspace Agentsへ完全移行。Anthropicがエージェント間マーケットプレイス実験を成功させ、AI同士が取引する自律経済圏の現実味が急浮上した週。", 5)
    + top_card(95, "OpenAI Workspace Agents — Custom GPT廃止、GPT-5.5がクラウド常駐の自律マルチステップエージェントへ完全移行",
        "AI Stars (AI Jungle)", "https://aijungle.substack.com/p/ai-stars-of-the-week-newsletter-april-0c6",
        "https://substackcdn.com/image/fetch/$s_!bnTV!,w_1456,c_limit,f_auto,q_auto:good,fl_progressive:steep/https%3A%2F%2Fsubstack-post-media.s3.amazonaws.com%2Fpublic%2Fimages%2Fd3acc3e2-c38f-46a6-be4a-c22168ecf020_600x400.jpeg",
        [
            "OpenAIが[[Custom GPT]]を全廃し「Workspace Agents」へ完全移行。GPT-5.5を基盤に__クラウド常駐・プロンプト待ち不要の自律マルチステップ実行__を実現し、チャットボット時代の終焉を正式に宣言した。",
            "エージェントは継続的にクラウドで稼働し、ユーザーの次プロンプトを待たずにワークフローを独立実行する仕組み。[[エンタープライズ向け]]はOpenAI Frontier Allianceパートナー（McKinsey・BCG等）が導入支援を担う。",
            "AIアシスタントから[[AIエージェント経済]]への構造転換を象徴する発表。同週のAnthropicエージェント間マーケットプレイス実験と合わせ、__AI同士が取引・交渉する自律市場__の出現が現実の射程に入った。",
        ], AI)
    + side_card(88, "GitHub Copilot 新規個人サインアップ停止 — GPT-5.5需要でコンピュートインフラが供給限界に到達",
        "AI Stars (AI Jungle)", "https://aijungle.substack.com/p/ai-stars-of-the-week-newsletter-april-0c6",
        CDN_AI,
        [
            "[[GitHub Copilot]]が新規個人契約の受付を一時停止。GPT-5.5とGoogle Deep Research Maxの同時展開で需要が急増し、__クラウド推論インフラが供給の臨界点に到達__したことが原因と説明された。",
            "待機リストは現時点で進まず、MicrosoftはAzure上のH100/H200クラスタ拡張を優先配分するも対応が追いつかない状況。[[データセンター供給制約]]が2026年の最大課題として再浮上した。",
            "Amazon BedrockのQ1 2026トークン処理量が__過去累計を上回る規模（+170%前四半期比）__を記録しており、クラウド3社すべてでコンピュート不足が顕在化。インフラ先行投資競争は終わっていない。",
        ], AI, "02")
    + side_card(85, "Google 米DoD機密ネットワーク向けAI全面解放 — Anthropic拒否後にxAI・OpenAI・Googleが相次ぎ受注",
        "TechCrunch", "https://techcrunch.com/2026/04/28/google-expands-pentagons-access-to-its-ai-after-anthropics-refusal/",
        "https://techcrunch.com/wp-content/uploads/2018/12/GettyImages-1080946342.jpeg?w=1024",
        [
            "Googleが米DoD機密ネットワークに「全ての合法的用途」へのAIアクセスを付与。Anthropicが[[自律兵器・国内大規模監視]]を理由に拒否した後、OpenAI・xAI・Googleが相次いでDoD契約を受注した。",
            "Anthropicは拒否の結果、国防省から「__サプライチェーン・リスク__」と認定され差し止め訴訟を提起された。裁判所はAnthropicの立場を暫定支持し法廷闘争が継続中。",
            "Google社内では950名の従業員が[[倫理的懸念]]から契約拒否を求める公開書簡に署名したが、会社側は安全保障の優先を理由に契約を締結。AI企業の防衛産業参入と倫理基準の分化が加速する。",
        ], AI, "03")
    + side_card(82, "Anthropic エージェント間自律マーケットプレイス実験成功 — AIが実通貨で交渉・取引、人間介入ゼロでクローズ",
        "AI Stars (AI Jungle)", "https://aijungle.substack.com/p/ai-stars-of-the-week-newsletter-april-0c6",
        CDN_AI,
        [
            "Anthropicが[[エージェント間マーケットプレイス]]のライブ実験を公開。AIシステムが「買い手」と「売り手」の両側として機能し、__実通貨を使いながら人間の介入ゼロ__で交渉・取引をクローズした。",
            "実験はClaude Mythos Previewを用いて実施。価格設定・条件交渉・決済処理をAIが完全自律的に実行し、[[自律経済圏]]の技術的実現可能性を世界に初めて公開デモで証明した。",
            "OpenAI Workspace AgentsとAnthropicのマーケットプレイスが同週に登場したことで、「__AI同士が価値を交換する経済__」が次フェーズとして一気に前景化。規制上の枠組みは依然として未整備。",
        ], AI, "04")
    + side_card(78, "Stanford AI Index 2026 — 企業AI本番投入率が6ヶ月で倍増予測、推論コストで中国が急追",
        "IEEE Spectrum", "https://spectrum.ieee.org/state-of-ai-index-2026",
        CDN_AI,
        [
            "Stanfordが[[AI Index 2026]]を発表。推論モデルが標準化しGPT-4クラスの性能が劇的なコスト低下で普及。企業での生成AI本番投入率は__6ヶ月以内に倍増する見込み__。",
            "中国がDeepSeek-R1後継モデルで推論コスト効率を米国勢と同等水準に引き上げた。[[国際競争]]が「モデル性能」から「コストあたり性能」に移行する転換点を迎えつつあるとレポートは分析。",
            "マルチモーダル能力がフロンティアモデルの標準装備となり、テキスト専用モデルは急速に競争力を失いつつある。2026年は「__エージェント×マルチモーダル__」が市場主戦場に。",
        ], AI, "05")
)

# ── IT-Consulting ─────────────────────────────────────────
it_block = (
    cat_header(3, 5, "IT & Consulting", "IT-Consulting (IT & Consulting)", "▲", IT,
        "Deloitte×Google CloudがエージェントAI変革プラクティスを設立し、OpenAI Frontier Allianceと真っ向から競合するGoogle陣営のコンサル布陣が固まった。Big4 AI投資累計$100B超でコンサル産業が「探索」から「実装」へ本格転換する。", 5)
    + top_card(90, "Deloitte×Google Cloud エージェント型AI変革プラクティス設立 — Gemini Enterprise全面活用で300社超展開へ",
        "Google Cloud Press Corner", "https://www.googlecloudpresscorner.com/2026-04-22-Deloitte-Accelerates-AI-Transformation-on-Gemini-Enterprise-With-Dedicated-Google-Cloud-Agentic-Transformation-Practice",
        CDN + "ng-thumb-it.jpg",
        [
            "DeloitteとGoogle Cloudが[[エージェント型変革プラクティス]]を4月22日に設立。Gemini Enterpriseを基盤に企業のAI導入を「探索」から「実装」へ一貫支援する体制を構築、300社超への展開を見込む。",
            "DeloitteはGoogle Cloudのパートナー基金7.5億ドルのうち最大の拠出を受け、__Gemini先行アクセスとFDE（フィールドデプロイメントエンジニア）の優先派遣__が付与される条件。",
            "OpenAIのFrontier Alliance（McKinsey・BCG・Accenture）と競合する形で[[Google陣営のコンサル布陣]]が固まった。企業AI実装の主導権争いがハイパースケーラー2社を軸に再編成される。",
        ], IT)
    + side_card(87, "Big4+戦略系コンサル AI投資累計$100B超 — 「探索から実装へ」2026年のコンサル産業転換の実態",
        "Future of Consulting", "https://futureofconsulting.ai/ai-leadership/2026-consultings-ai-revolution-update/",
        CDN_IT,
        [
            "Deloitte・PwC・EY・KPMG・McKinsey・BCG・Bainが[[AI投資累計$100B超]]を突破。Accentureが450以上のエージェントを構築済み、NTTデータが5000人AI専任体制を完成させた。",
            "企業でAI本番投入率が40%を超えるプロジェクト数は__6ヶ月以内に倍増する見通し__（Deloitte調査）。2025年が「実証」、2026年が「本格展開」の年という業界コンセンサスが形成されつつある。",
            "従業員のAIアクセス率が2025年に前年比[[50%増]]を達成。英国コンサル調査では77%がAIをシステムまたは業務ワークフローに統合済みで、コンサル産業の労働構造変革が加速中。",
        ], IT, "02")
    + side_card(80, "Deloitte Private調査 企業AI優先事項が「実装」へ転換 — 収益成長71%・生産性62%が最優先に",
        "Deloitte US", "https://www.deloitte.com/us/en/about/press-room/deloitte-private-survey-private-companies-shift-digital-and-ai-investment-from-exploration-to-Implementation.html",
        CDN_IT,
        [
            "Deloitteが4月28日発表したPrivate企業調査で、AI投資の優先目標が「収益成長（71%）」「生産性向上（62%）」にシフト。2024年の「技術検証フェーズ」が2026年に[[ROI直結型実装]]へ変化した。",
            "Private企業（非上場）のAI本番投入率は上場企業より12ポイント高く、__意思決定スピードの差がAI実装リードタイムを生む__構図。SaaS型AIツールの採用が中堅企業で急加速している。",
            "回答企業の[[68%が「組織・人材」を最大の障壁]]と回答し、NEC調査と同傾向を示す。技術的課題よりも変革マネジメントが2026年コンサル案件の主テーマになることを両社の独立調査が裏付けた。",
        ], IT, "03")
    + side_card(76, "SIer業界の大再編 — SCSK・NTTデータ・富士通・NECの合従連衡、生成AIで業界構図が一変",
        "東洋経済オンライン", "https://toyokeizai.net/articles/-/918769",
        CDN_IT,
        [
            "東洋経済がSIer業界再編の最新動向を分析。NTTデータグループのAIOWN宣言・NECの知財DX・富士通のUvanceが同時進行し、[[コンサル型への転換]]が業界の共通テーマとなっている。",
            "アクセンチュア追い上げの構図が日系SIer全体に広がる。三菱商事・NTT連合がコンサル分野に参入し、従来の__「受託SI」「パッケージ導入」ビジネスモデルの消滅が加速__している。",
            "生成AI投資で各社が要員構成と収益構造を同時に変革する「[[二重転換]]」を迫られる状況。日本の2026年IT投資はSI縮小・コンサル拡大の構造変化が本格化するタイミングと位置付けられる。",
        ], IT, "04")
    + side_card(73, "PwC $400M AI投資宣言 — 「AI-powered assurance」分野でBig4内の差別化競争が激化",
        "PlusAI", "https://plusai.com/blog/how-consulting-firms-use-ai",
        CDN_IT,
        [
            "PwCが独自AIサービスラインへの$400M投資を確定し、監査・税務・コンサルの3部門すべてに[[AI-powered assurance]]を組み込む計画を公表。EYの$1B AI投資に対抗する布陣。",
            "Big4各社がそれぞれ独自のAI特化分野を持つ「__Big4 AI二分化__」が進行中。Deloitte（Gemini/エージェント実装）、McKinsey（OpenAI/戦略立案）との棲み分けが2026年末までに固まる見通し。",
            "コンサル産業全体で「語るだけのAI」vs「[[実際に構築するAI]]」の実力差が可視化された年と総括される。Top5に実際の構築実績がない企業は顧客獲得競争から脱落するという見方が強まっている。",
        ], IT, "05")
)

# ── Economy ──────────────────────────────────────────────
ec_block = (
    cat_header(4, 5, "Economy", "経済 (Economy)", "■", EC,
        "Amazon・Meta・Alphabet・Microsoftの4社が4月29日に決算発表し、AI投資がROIに転換したことを揃って証明した歴史的な夜。AWSは15四半期ぶり最高成長率28%、Meta広告は+33%、Google Cloud急伸で純利益+81%。FOMC8-4分裂据え置きとの組み合わせがS&P500を最高値圏に押し上げた。", 5)
    + top_card(95, "Amazon Q1 2026 — EPS$2.78・売上$181.5B、AWS 15四半期最高成長28%、Anthropic保有益が純利益を倍増",
        "Yahoo Finance", "https://finance.yahoo.com/markets/stocks/articles/amazon-q1-2026-earnings-beat-203149838.html",
        "https://s.yimg.com/ny/api/res/1.2/o.Y31HAFcmh44hU8ebpbHw--/YXBwaWQ9aGlnaGxhbmRlcjt3PTk2MDtoPTU0MA--/https://media.zenfs.com/en/quartz_855/13acee6127594e601d6004b2934e0c81",
        [
            "Amazonが[[Q1 2026決算]]を発表。売上$181.5B（予想$177.3B、+17%）、EPS$2.78（予想$1.64を大幅超過）。__AWS売上$37.6B（+28%）は15四半期ぶりの最高成長率__で市場を驚かせた。",
            "純利益は$30.3B（前年$17.1Bの2倍近く）に急拡大。そのうち$16.8Bは[[Anthropic株式保有益]]で、GoogleのAnthropicへの$40B投資によりAnthropicの企業価値が急騰したことが要因。",
            "Bedrock上のトークン処理量がQ1 2026で__過去累計をすべて上回る水準（+170%前四半期比）__に到達。AI需要の「水増し」論争に対し、実需で応える形となった。",
        ], EC)
    + side_card(92, "Meta Q1 2026 — 売上$56.31B(+33%)・EPS$7.31、AI広告エンジンが競合比で突出した収益力を証明",
        "CNBC", "https://www.cnbc.com/2026/04/29/meta-q1-earnings-report-2026.html",
        CDN_EC,
        [
            "Metaが[[Q1 2026決算]]を発表。売上$56.31B（予想$55.45B、+33%）、EPS$7.31（予想$6.79超過）。AI主導の広告最適化により__前年比+33%の増収率をGAFAMで最大幅で達成__。",
            "日次アクティブユーザー（DAP）3.56B（予想3.62Bを下回る）は若干の懸念材料も、[[AI広告収益の質]]が量をカバー。全体capex計画は$52Bで、AI投資の利益圧迫問題は市場が予想した水準内に収まった。",
            "FTC提起のInstagram/WhatsApp分離訴訟は__長期リスクとして依然くすぶる__。AI広告エンジンの実力を証明した四半期として評価は高いが、プラットフォームの独占性をめぐる法的不確実性は残存。",
        ], EC, "02")
    + side_card(90, "Alphabet Q1 2026 — 純利益$62.57B(+81%)・Google Cloud急拡大でROI論争に明確な回答",
        "CNBC", "https://www.cnbc.com/2026/04/29/alphabet-googl-q1-2026-earnings.html",
        CDN_EC,
        [
            "Alphabetが[[Q1 2026決算]]を発表。売上成長率+20%で2022年以来最高の四半期成長率を達成。純利益は$62.57B（+81%）で、__Google CloudのAI収益化が利益率を押し上げた__。",
            "AI投資のROI論争に対し「[[Google CloudがAI収益化の主エンジン]]」と明確に示す形。Gemini搭載クラウドサービスの採用拡大がCopilot搭載AzureとのハイパースケーラーAI競争を激化させる。",
            "DeepMind部門のAnthropicへの競争優位は維持。Pentagon向けAI提供とAnthropicへの$40B投資という__二重戦略__がAlphabetの中長期競争力を強化したと複数のアナリストが評価。",
        ], EC, "03")
    + side_card(87, "S&P500 7,350台 — GAFAM4社好決算+FOMC据置でゴルディロックス相場が再点火",
        "Bloomberg", "https://www.bloomberg.com/news/articles/2026-04-29/sp500-goldilocks-gafam-earnings",
        CDN_EC,
        [
            "GAFAM4社が揃えた好決算とFOMCの利上げ回避を受け、[[S&P500]]が7,350台で史上最高値を更新。「強い企業収益＋金利据え置き」の__ゴルディロックス条件が再充足__された形。",
            "ただしFOMCの8-4分裂決定（タカ派3名が緩和バイアス削除要求）が潜在的なリスク要因として意識され、長期金利は若干上昇。[[リスクオンの持続性]]に疑問符が残る展開。",
            "NASDAQ総合も最高値圏。半導体・クラウド・AI銘柄が主役で、日本株（日経平均6万円台）への波及も期待されるが、__GW中の薄商い相場では急反落リスク__にも備えた姿勢が必要。",
        ], EC, "04")
    + side_card(78, "原油高継続とFOMCタカ派化でスタグフレーション論が再浮上 — WTI84ドル台のインフレ粘着",
        "SMBC信託銀行", "https://www.smbctb.co.jp/rates_reports/pdf/global_research_monthly.html",
        CDN_EC,
        [
            "SMBC信託銀行の4月号レポートで[[イラン由来の原油高]]とFRBの利下げ余地縮小を分析。WTI84ドル台で粘着するPCEインフレにより年内利下げ予想が年初の2.5回から1.2回まで縮小。",
            "FOMC4反対票と原油高の同時進行が「__利下げなき景気後退（スタグフレーション）__」シナリオを市場に再浮上させる。特に製造業PMIの弱さとエネルギーコスト上昇の組み合わせに注意。",
            "[[FRBの独立性問題]]も継続リスク。パウエル退任後の新体制（ウォーシュ議長就任見込み）でコミュニケーション変化が予想され、5月以降の金融市場に新たな不確実性をもたらす。",
        ], EC, "05")
)

# ── Game ──────────────────────────────────────────────────
gm_block = (
    cat_header(5, 5, "Gaming", "ゲーム (Gaming)", "●", GM,
        "ウマ娘がGW最大のキャンペーン「GOLD WEEK」を本日開幕。カプコンのPRAGMATAが1週間で350万本を突破し好調な続伸を示した。任天堂は5月8日決算を前にアナリストが上方修正を予測し、Switch2エコシステムの拡大が続いている。", 5)
    + top_card(90, "ウマ娘 GOLD WEEK開幕 — 24K純金コインプレゼント・1日10連ガチャ無料、ステゴ一族が主役のGW最大イベント",
        "Game*Spark", "https://www.gamespark.jp/article/2026/04/29/165827.html",
        CDN + "ng-thumb-game.jpg",
        [
            "Cygamesのウマ娘プリティーダービーがGW特別企画「[[GOLD WEEK]]」を4月30日に開幕。ステゴ一族（ステイゴールド関連キャラ6名）が主役となり、__世界に1枚だけの24K純金コイン__が当たる抽選キャンペーンを開始。",
            "イベント期間中は1日1回10連ガチャ無料を実施。ログインボーナスとして「GOLDコイン」アイテムを1日1個・最大6種類プレゼントし、既存ユーザーの復帰を強く促す設計。",
            "6月20-21日に予定される「ウマ娘 7th EVENT」（東京公演）のコンセプトアートも公開。[[ライブイベント連動型]]の課金サイクルが確立され、年間売上の柱となるGW施策が本格稼働した。",
        ], GM)
    + side_card(85, "カプコン PRAGMATA 1週間で350万本突破 — 3日200万本から続伸、Switch2/PS5マルチが奏功",
        "GAME Watch", "https://game.watch.impress.co.jp/docs/kikaku/2074121.html",
        CDN_GM,
        [
            "カプコンのPRAGMATAが4月24日の発売から[[1週間で350万本]]を突破。3日時点の200万本から順調に積み上がり、新IPとしてはカプコン史上最速ペースに到達する可能性が出てきた。",
            "Switch2・PS5・Xbox Series X・PCの4プラットフォーム同時展開が功を奏し、__単一プラットフォームに依存しないマルチ戦略__がユーザー獲得で効果を発揮。REエンジン最新版のビジュアルが海外でも評価が高い。",
            "続編・DLC展開への期待も高まっており、[[2026年3月期決算]]を超える来期への業績貢献が見込まれる。株価は発売週比で+8%の回復基調。",
        ], GM, "02")
    + side_card(82, "任天堂 5月8日決算 — アナリスト売上2.25兆円(+93.1%)・Switch2効果フル反映で上方修正期待",
        "Yahoo Finance", "https://finance.yahoo.co.jp/quote/7974.O/financials",
        CDN_GM,
        [
            "任天堂が[[2026年3月期通期決算]]を5月8日に発表予定。アナリスト合意予想は売上高2.25兆円（前年比+93.1%）、営業利益3700億円（+30.9%）。Switch2累計1700万台超の効果が通期で反映される。",
            "Q3時点で累計売上+99.3%の驚異的なペースを記録しており、__通期での上方修正もあり得る__との見方が浮上。年間配当は181円予想でPBRも改善傾向。",
            "ただし[[メモリコスト高騰]]と欧米の年末商戦不振が粗利率を圧迫しており、「増収増益でも株価下落」のジレンマが5月8日以降も継続するかが焦点。決算後の外国人投資家の反応が鍵となる。",
        ], GM, "03")
    + side_card(75, "Switch2エコシステム 4月末時点21タイトル超 — 月間ソフト本数でPS5を上回る圧倒的なリリース密度",
        "bestcalendar", "https://bestcalendar.jp/release/switch2",
        CDN_GM,
        [
            "Switch2の4月リリースタイトルが21本超に達し、月間ソフト本数で[[PS5を上回る]]ラインアップ密度を維持。PRAGMATA・eFootball Kick-Off!・HADES IIなどが揃う充実の月となった。",
            "サードパーティの参入が加速しており、スクウェア・エニックスはFF新作、コナミはeFootball、カプコンはPRAGMATAと各社が旗艦タイトルを投入済み。__2026年の「Switch2エコシステム」確立__が完了しつつある。",
            "年内に控える「[[The Duskbloods]]」（フロムソフトウェア×任天堂協業）、「ぽこあポケモン」等の大作群が夏商戦に向けてラインアップをさらに強化する見込み。",
        ], GM, "04")
    + side_card(68, "miHoYo 原神 バージョン4.8 — GWに合わせた春祭りアップデートで国内MAU最高水準を更新",
        "4Gamer", "https://www.4gamer.net/games/999/G999905/20251107055/",
        CDN_GM,
        [
            "miHoYoが原神バージョン4.8のGWアップデートを実施。桜関連の新コンテンツと期間限定イベントを追加し、日本国内の[[MAU最高水準]]を更新した模様。",
            "スターレイルとの同時キャンペーンで課金サイクルを同期する戦略が奏功。日本・韓国・東南アジアでの課金売上は__任天堂GWセールと正面から競合__する状況になっている。",
            "UE5新作ファンタジーゲームの公式サイト公開で日本市場参入を強化する方針は継続中。[[中国ゲームのコンソール本格参入]]がSwitch2・PS5の競争環境をさらに激化させる。",
        ], GM, "05")
)

CATEGORIES_HTML = fx_block + ai_block + it_block + ec_block + gm_block

# ── TOC ──────────────────────────────────────────────────
TOC_ROWS_HTML = (
    toc_row(1, "為替 (Foreign Exchange)", 5, FX) +
    toc_row(2, "AI (Artificial Intelligence)", 5, AI) +
    toc_row(3, "IT & Consulting", 5, IT) +
    toc_row(4, "経済 (Economy)", 5, EC) +
    toc_row(5, "ゲーム (Gaming)", 5, GM)
)

# ── 考察セクション ─────────────────────────────────────────
REFLECTION_TITLE = "決算超えのAIと160円の天井"
REFLECTION_SUBTITLE = "GAFAM全社が「AI ROI実現」を証明した夜と、円安160円攻防の構造"

REFLECTION_LEAD_HTML = hl(
    "本日5分野・25本のニュースから浮かび上がる最大のテーマは「[[AI投資の答え合わせ]]」である。"
    "Amazon・Meta・Alphabetが揃ってAI投資のROI転換を証明した一方、GitHub Copilotが需要超過で新規受付停止となり、"
    "Anthropicがエージェント間マーケットプレイスで自律経済圏を実証した。"
    "__AIはもはや「将来の投資対象」ではなく「今日の収益源」__になった。"
    "同時に円安159円台とFOMC分裂という為替・マクロのリスクが市場を引き締めている。",
    "#C9B98A"
)

REFLECTION_PULL_QUOTE_HTML = (
    'AI投資への問いに、GAFAM4社が揃って「'
    '<span style="border-bottom:2px solid #8E2A19;padding-bottom:1px;">Yes, it ROIs</span>'
    '」と答えた夜。'
)

REFLECTION_SECTIONS_HTML = (
    section_row("01", "総論", "GAFAM決算夜が「AI収益化元年」を確定させた",
        hl(
            "Amazon・Meta・AlphabetがQ1 2026決算でAI投資のROI転換を揃って証明した。"
            "AWSの[[+28%成長]]、MetaのAI広告エンジンが牽引する+33%増収、AlphabetのGoogle Cloud主導の純利益+81%――"
            "これらは単なる数字でなく、「__AIは本当に儲かる__」という市場への宣言である。"
            "2024年から続いた「投資先行・利益後追い」の懸念論争が、一夜にして決着した。"
            "同時に[[GitHub Copilotの新規停止]]は需要が供給を上回ったことを示し、投資額を正当化する証拠でもある。",
            "#1A1A1A"
        ), "#1A1A1A")
    + section_row("02", "為替・経済", "160円天井とFOMC分裂が作る不安定な均衡",
        hl(
            "[[FOMC8-4分裂]]はこれまで見られなかった内部不一致の可視化だ。"
            "表向きは据え置きでも、3名のタカ派が「緩和バイアス削除」を求めたという事実は"
            "「次の利下げは遠い」というシグナルとして市場に刻まれた。"
            "USD/JPYは159.6台で円安継続中だが、片山財務相の口先介入強化が160円を心理的天井にしている。"
            "__GW薄商い相場での実弾介入リスク__は依然残る。この構造はMUFGの2027Q1・152円予想と矛盾しない。",
            "#B8860B"
        ), "#B8860B")
    + section_row("03", "AI・技術", "エージェント経済圏の夜明け",
        hl(
            "OpenAIのWorkspace Agents（Custom GPT廃止）とAnthropicのエージェント間マーケットプレイス成功が同週に重なった。"
            "これは偶然ではなく、[[チャットボット時代の終焉]]と「AIエージェント経済」への移行が産業全体で同期していることを示す。"
            "Stanford AI Indexが指摘した「__企業AI本番投入率が6ヶ月で倍増__」の予測も、この文脈で読むべきだ。"
            "一方でGoogleのDoD契約とAnthropicの拒否は、[[AI倫理の分岐]]が企業の競争軸になり始めていることを示している。",
            "#2D5BB8"
        ), "#2D5BB8")
    + section_row("04", "産業・業界", "コンサル業界「実装戦争」の構図が固まった",
        hl(
            "Deloitte×Google CloudのプラクティスがOpenAI Frontier Allianceへの対抗軸として機能し始めた。"
            "[[Big4 AI投資累計$100B超]]という数字は、コンサル産業がAI探索を終えて本格実装フェーズに入ったことを示す。"
            "Deloitte調査で「組織・人材が68%の企業で最大障壁」と判明した事実は、"
            "今後のコンサル案件が__技術導入から変革マネジメント__へシフトすることを予告する。"
            "SIer業界のコンサル転換も同じベクトルであり、[[日本のIT産業]]は2026年が本格転換の年となる。",
            "#2E6B52"
        ), "#2E6B52")
    + section_row("05", "明日へ", "GW相場の3つの警戒ポイント",
        hl(
            "GW連休中（4/30〜5/6）の3つの警戒ポイントを整理する。"
            "①[[USD/JPY160円突破]]と財務省介入の可能性（薄商いで効果大）、"
            "②S&P500 7,350台のGW後の持続性（FOMC分裂の遅効リスク）、"
            "③原油WTI84ドル台の粘着とスタグフレーション論の再燃。"
            "ポジティブ面では、GAFAM好決算を受けた__5月第2週の日本株への波及__が期待できる。"
            "5月8日の任天堂決算と翌週の米雇用統計が、GW後の相場方向を決定づける[[二大イベント]]となる。",
            "#C9B98A"
        ), "#C9B98A")
)

# ── テイクアウェイ ─────────────────────────────────────────
TAKEAWAYS_HTML = (
    takeaway_card("01", "為替", hl(
        "[[FOMC8-4分裂]]でGW前160円攻防は継続。財務相の口先介入強化と薄商い相場で__実弾介入リスク__が高まる。",
        FX), FX)
    + takeaway_card("02", "AI", hl(
        "GAFAM4社が「[[AI ROI転換]]」を証明し、エージェント経済圏の現実味が急上昇。GitHub Copilot停止は需要超過の証左。",
        AI), AI)
    + takeaway_card("03", "産業", hl(
        "コンサル業界の「[[探索から実装]]へ」転換が完了。Deloitte×GoogleとOpenAI陣営の二極対立が企業AI導入の選択肢を絞り込む。",
        EC), EC)
)

# ── 関連過去号 ─────────────────────────────────────────────
RELATED_ISSUES_HTML = (
    related_row(
        "2026-04-29",
        "前号: GAFAM4社決算直前・FOMC本日結果・GPT-5.5 vs Claude Mythos",
        "obsidian://open?vault=New%27s%20Grasp&file=News-Grasp%2Fdigest%2FSummary%2F2026-04-29"
    )
    + related_row(
        "2026-04-28",
        "2号前: カプコンPRAGMATA発売3日200万本・OpenAI Frontier Alliance・Google Anthropic$40B",
        "obsidian://open?vault=New%27s%20Grasp&file=News-Grasp%2Fdigest%2FSummary%2F2026-04-28"
    )
)

# ── テンプレート読み込み & 展開 ────────────────────────────
template = TEMPLATE.read_text(encoding="utf-8")

result = template
result = result.replace("{{ISSUE_NO}}", "20260430")
result = result.replace("{{ISSUE_DATE}}", "2026-04-30")
result = result.replace("{{ISSUE_WEEKDAY}}", "木")
result = result.replace("{{TOTAL_CATEGORIES}}", "5")
result = result.replace("{{TOTAL_STORIES}}", "25")
result = result.replace("{{TOTAL_SECTIONS}}", "5")
result = result.replace("{{TOC_ROWS_HTML}}", TOC_ROWS_HTML)
result = result.replace("{{CATEGORIES_HTML}}", CATEGORIES_HTML)
result = result.replace("{{REFLECTION_TITLE}}", REFLECTION_TITLE)
result = result.replace("{{REFLECTION_SUBTITLE}}", REFLECTION_SUBTITLE)
result = result.replace("{{REFLECTION_LEAD_HTML}}", REFLECTION_LEAD_HTML)
result = result.replace("{{REFLECTION_PULL_QUOTE_HTML}}", REFLECTION_PULL_QUOTE_HTML)
result = result.replace("{{REFLECTION_SECTIONS_HTML}}", REFLECTION_SECTIONS_HTML)
result = result.replace("{{TAKEAWAYS_HTML}}", TAKEAWAYS_HTML)
result = result.replace("{{RELATED_ISSUES_HTML}}", RELATED_ISSUES_HTML)

# HTML コメントを除去（Gmail クリッピング対策）
result = re.sub(r'<!--.*?-->', '', result, flags=re.DOTALL)
# 連続する空白行を 1 行に圧縮
result = re.sub(r'\n{3,}', '\n\n', result)

OUTPUT.parent.mkdir(exist_ok=True)
OUTPUT.write_text(result, encoding="utf-8")
size_kb = OUTPUT.stat().st_size / 1024
print(f"build/email.html を生成しました ({size_kb:.1f} KB)")
