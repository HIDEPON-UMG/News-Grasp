"""2026-05-29 の HTMLメールを生成して build/email.html に出力"""
import re, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from tools.config import BASE_URL as WEB_BASE

CDN = 'https://raw.githubusercontent.com/HIDEPON-UMG/news-grasp-assets/main/'
TEMPLATE = (pathlib.Path(__file__).parent.parent / 'prompts' / 'email-template.html').read_text(encoding='utf-8')
OUTPUT = pathlib.Path(__file__).parent.parent / 'build' / 'email.html'

FX='#B8860B'; AI='#2D5BB8'; IT='#2E6B52'; MB='#3A7B8C'; EC='#8E2A19'; GM='#5E3D8C'
CDN_FX=CDN+'ng-thumb-common-fx.jpg'; CDN_AI=CDN+'ng-thumb-common-ai.jpg'
CDN_IT=CDN+'ng-thumb-common-it.jpg'; CDN_MB=CDN+'ng-thumb-common-mobility.jpg'
CDN_EC=CDN+'ng-thumb-common-economy.jpg'; CDN_GM=CDN+'ng-thumb-common-game.jpg'

def hl(text, accent):
    text = re.sub(r'\[\[(.+?)\]\]', lambda m: f'<strong style="background:{accent}22;padding:0 3px;border-radius:2px;">{m.group(1)}</strong>', text)
    text = re.sub(r'__(.+?)__', lambda m: f'<span style="border-bottom:2px solid {accent};padding-bottom:1px;">{m.group(1)}</span>', text)
    text = re.sub(r'\*\*(.+?)\*\*', lambda m: f'<strong>{m.group(1)}</strong>', text)
    return text

def buls(items, acc):
    return ''.join(f'<p class="bul" style="padding-left:20px;margin:0 0 8px;font-size:14.5px;line-height:1.9;color:#1A1A1A;">{hl(i,acc)}</p>' for i in items)

def meta_line(score, label, source, url, acc):
    badge = f'<span class="m b7" style="font-family:\'JetBrains Mono\',monospace;font-size:11px;background:{acc};color:#fff;padding:2px 8px;">{label}</span>'
    sc = f'<span class="m" style="font-family:\'JetBrains Mono\',monospace;font-size:10px;color:#5C5A52;margin-left:8px;">SCORE {score}</span>'
    src = f'<a href="{url}" style="font-family:\'JetBrains Mono\',monospace;font-size:10px;color:#5C5A52;text-decoration:none;">{source} ↗</a>'
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:12px;"><tbody><tr>'
            f'<td>{badge}{sc}</td><td align="right">{src}</td></tr></tbody></table>')

def top_card(score, title, source, url, img, bs, acc):
    return (f'<tr><td class="ng-card-pad" style="padding:20px 36px 8px;background:#FAF7F0;border-bottom:1px solid #EDEAE3;">'
        + meta_line(score,'TOP',source,url,acc)
        + f'<h3 class="ng-card-title" style="font-size:20px;font-weight:800;line-height:1.4;margin:0 0 16px;"><a href="{url}" style="color:#1A1A1A;text-decoration:none;">{title}</a></h3>'
        + f'<div class="ng-feature-img" style="margin-bottom:16px;"><a href="{url}" style="display:block;"><img src="{img}" width="568" alt="" style="width:100%;height:auto;display:block;max-height:240px;object-fit:cover;border:1px solid #E2DED4;"></a></div>'
        + buls(bs,acc) + '</td></tr>')

def side_card(score, title, source, url, img, bs, acc, label=None):
    lbl = label or str(score)
    return (f'<tr><td class="ng-card-pad" style="padding:18px 36px 8px;background:#FAF7F0;border-bottom:1px solid #EDEAE3;">'
        + '<table role="presentation" class="ng-side-table" width="100%" cellpadding="0" cellspacing="0" border="0"><tbody><tr>'
        + f'<td class="ng-card-thumb vtop" width="140" style="width:140px;vertical-align:top;padding-right:16px;"><a href="{url}" style="display:block;"><img class="ng-card-thumb-img" src="{img}" width="140" height="90" alt="" style="width:140px;height:90px;object-fit:cover;display:block;border:1px solid #E2DED4;"></a></td>'
        + '<td class="ng-card-body-cell vtop" style="vertical-align:top;">'
        + meta_line(score,lbl,source,url,acc)
        + f'<h3 class="ng-card-title" style="font-size:17px;font-weight:800;line-height:1.4;margin:0 0 12px;"><a href="{url}" style="color:#1A1A1A;text-decoration:none;">{title}</a></h3>'
        + buls(bs,acc) + '</td></tr></tbody></table></td></tr>')

def cat_hdr(idx,total,name_en,name_jp,glyph,acc,summary,count,cat_id):
    cat_url = f'{WEB_BASE}/{cat_id}/'
    return (f'<tr><td class="ng-cat-pad" style="background:{acc};padding:20px 36px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tbody><tr>'
        '<td style="vertical-align:middle;">'
        f'<div class="m" style="font-family:\'JetBrains Mono\',monospace;font-size:10px;color:rgba(255,255,255,0.7);letter-spacing:2px;margin-bottom:4px;">CATEGORY {idx} / {total} · {name_en.upper()}</div>'
        f'<div class="ng-cat-name" style="font-size:28px;font-weight:800;color:#fff;">'
        f'<span class="m" style="font-family:\'JetBrains Mono\',monospace;margin-right:10px;">{glyph}</span>{name_jp}</div>'
        '</td>'
        f'<td align="right" style="vertical-align:middle;color:rgba(255,255,255,0.85);font-family:\'JetBrains Mono\',monospace;font-size:11px;">'
        f'{count} stories<br><a href="{cat_url}" style="color:rgba(255,255,255,0.95);text-decoration:none;font-size:10px;">VIEW WEB →</a></td>'
        '</tr></tbody></table>'
        f'<div class="ng-cat-summary" style="font-size:13px;color:rgba(255,255,255,0.95);font-style:italic;line-height:1.6;margin-top:10px;padding-top:10px;border-top:1px solid rgba(255,255,255,0.25);">{summary}</div>'
        '</td></tr>')

def toc_row(num,name_jp,count,acc):
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:4px;"><tbody><tr>'
        f'<td width="32" class="m" style="font-family:\'JetBrains Mono\',monospace;font-size:12px;color:{acc};font-weight:700;">{num}.</td>'
        f'<td style="font-size:14px;font-weight:600;">{name_jp}</td>'
        f'<td align="right" class="m" style="font-family:\'JetBrains Mono\',monospace;font-size:11px;color:#5C5A52;">{count} stories</td>'
        '</tr></tbody></table>')

def sec_row(num,tag,heading,body_html,acc):
    return (f'<tr><td class="ng-section-pad" style="background:#FAF7F0;padding:0 36px;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-bottom:1px dashed #E2DED4;"><tbody><tr>'
        f'<td class="ng-section-num-cell" width="80" valign="top" style="padding:28px 16px 28px 0;vertical-align:top;">'
        f'<div class="m ng-section-num" style="font-family:\'JetBrains Mono\',monospace;font-size:38px;font-weight:900;color:{acc};line-height:0.9;letter-spacing:-2px;">§{num}</div>'
        f'<div class="m" style="font-family:\'JetBrains Mono\',monospace;font-size:9px;color:#fff;background:{acc};padding:2px 6px;display:inline-block;letter-spacing:1.5px;margin-top:8px;">{tag}</div></td>'
        f'<td class="ng-section-text-cell" valign="top" style="padding:28px 0 28px 20px;border-left:1px solid #E2DED4;vertical-align:top;">'
        f'<h3 class="ng-section-heading" style="font-size:18px;font-weight:800;margin:0 0 14px;color:#1A1A1A;">{heading}</h3>'
        f'<div class="ng-section-body" style="font-size:13.5px;line-height:2.0;color:#1A1A1A;">{body_html}</div>'
        '</td></tr></tbody></table></td></tr>')

def tkw(num,tag,text_html,acc):
    return (f'<tr><td style="padding-bottom:12px;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#fff;border:1px solid #E2DED4;"><tbody><tr>'
        f'<td width="56" valign="middle" class="m" style="background:{acc};color:#fff;text-align:center;font-family:\'JetBrains Mono\',monospace;font-size:18px;font-weight:900;padding:14px 0;">{num}</td>'
        f'<td style="padding:12px 16px;"><div class="m" style="font-family:\'JetBrains Mono\',monospace;font-size:9px;color:{acc};font-weight:700;letter-spacing:1.5px;margin-bottom:4px;">{tag}</div>'
        f'<div style="font-size:13px;line-height:1.7;font-weight:600;">{text_html}</div></td>'
        '</tr></tbody></table></td></tr>')

def rel_row(date,title,url):
    return (f'<tr><td style="padding:10px 0;border-bottom:1px solid #E2DED4;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tbody><tr>'
        f'<td width="100" class="m" style="font-family:\'JetBrains Mono\',monospace;font-size:11px;color:#5C5A52;">{date}</td>'
        f'<td style="font-size:13px;font-weight:600;"><a href="{url}" style="color:#1A1A1A;text-decoration:none;">{title}</a></td>'
        '<td width="20" align="right" style="color:#5C5A52;">→</td>'
        '</tr></tbody></table></td></tr>')

# ─── FX ────────────────────────────────────────────────────
FX_B = (
    cat_hdr(1,6,'Foreign Exchange','為替 (Foreign Exchange)','¥',FX,
        '米・イラン停戦延長合意でドル安優勢、ドル円159円台前半。財務省が4〜5月分の介入実績を本日公表。東京CPI速報値が日銀6月利上げ判断の鍵となる。',5,'fx')
    + top_card(92,'ドル円159円台前半 — 米・イラン合意報道でドル安優勢','みんかぶ','https://fx.minkabu.jp/news/368505',
        'https://mfx-assets.s3.ap-northeast-1.amazonaws.com/news_ogp/forex.png',
        ['[[米・イラン両国]]が**60日間停戦延長**と**核開発交渉開始**で合意との報道を受け、ドル安が優勢となり**ドル円は159.21円**台前半まで下落した。',
         'ホルムズ海峡リスク後退で**原油先物も急落**しインフレ懸念が和らぎ、ドル売り・円買いに拍車をかけた。',
         '__中東情勢の変化が為替の新たな変数__として浮上し、断続的な報道が相場を揺さぶる構図が続く。'], FX)
    + side_card(90,'外国為替平衡操作の実施状況 — 財務省5月月次公表','財務省','https://www.mof.go.jp/policy/international_policy/reference/feio/data/monthly/index.html',
        'https://www.mof.go.jp/common/images/og_img.png',
        ['[[財務省]]が**5月29日に4〜5月分の介入実績**を公表。推計**8.6〜10兆円規模**の円買い介入が4月30日以降に実施されたとされる。',
         '実績値の確定が円相場の次の焦点となり、**160円ライン防衛への政府意思**を改めて市場が確認する機会となった。',
         '__介入の効果と持続性__を巡る議論が再燃し、日銀追加利上げとの二段構え通貨防衛戦略が問われる局面に入った。'], FX,'02')
    + side_card(88,'日銀の6月利上げは長期金利安定の最低条件','野村證券','https://www.nomura.co.jp/wealthstyle/article/0739/',
        'https://www.nomura.co.jp/wealthstyle/article/0739/images/og_a_0739_01.png',
        ['[[野村證券]]の宍戸氏が「**6月利上げは長期金利安定の最低条件**」と指摘。OIS市場が[[日銀]]の**6月利上げ確率を75%**程度まで織り込んだ。',
         '**長期金利が2.8%**まで上昇の背景に原油高インフレ懸念があり、利上げ遅れは「**日本売り**」リスクと警告。',
         '__金利安定か円防衛かの二律背反__の中で日銀の決断が円相場の構造変化を決定づける局面に入った。'], FX,'03')
    + side_card(88,'本日の注目点：ドル円一時159円25銭 — 東京CPI・介入実績発表が焦点','Yahoo!ファイナンス','https://finance.yahoo.co.jp/news/detail/fe69ddfdf9b543b1d6eb8724766181feb4f52407',
        'https://s.yimg.jp/images/finance/common/image/ogp.png',
        ['[[東京都区部CPI]]の**5月速報値**発表が今日の最大焦点。**日銀の6月利上げ判断**に直結する数字として市場が固唾を呑む。',
         '午前中のドル円は一時**159円25銭**まで上昇したが、財務省介入実績公表前の様子見ムードから**上値は重い展開**。',
         '__東京CPI次第で相場の方向感が決まる__という市場の認識を外為各社が注目ポイントとして列挙。'], FX,'04')
    + side_card(85,'Japanese Yen Forecast: USD/JPY Jumps After Japan CPI Slide','FX Empire','https://www.fxempire.com/forecasts/article/japanese-yen-forecast-usd-jpy-jumps-after-japan-cpi-slide-1580747',
        'https://responsive.fxempire.com/v7/_fxempire_/2025/09/USDJPY-1.jpg?func=cover&q=70&width=700',
        ['[[東京CPI]]の鈍化を受けて**USD/JPY**が上昇。**日銀の6月利上げ確率が低下**するとの観測が先行し円売りが広がった。',
         '**技術的には159〜160円**のレンジで推移。上値では政府介入の警戒感、下値では**日米金利差縮小期待**が支える構造。',
         '__CPI鈍化と介入ライン接近の板挟み__という複雑な相場環境の中で東京CPI速報が判断材料を提供する。'], FX,'05')
)

# ─── AI ────────────────────────────────────────────────────
AI_B = (
    cat_hdr(2,6,'Artificial Intelligence','AI (Artificial Intelligence)','◆',AI,
        'AnthropicがシリーズH 650億ドル調達で評価額9,650億ドル達成。OpenAIがIPO向けS-1をSECに提出、NVIDIAは売上816億ドル+85%達成。AIバリュエーション膨張が加速する歴史的な日。',5,'ai')
    + top_card(98,'AnthropicがシリーズHで650億ドル調達 — 評価額9,650億ドルでOpenAIを抜き世界最高値に','TechCrunch','https://techcrunch.com/2026/05/28/anthropic-raises-65-billion-nears-1t-valuation-ahead-of-ipo/',
        'https://techcrunch.com/wp-content/uploads/2025/09/Screenshot-2025-09-02-at-12.22.37PM.png?resize=1200,671',
        ['[[Anthropic]]がシリーズHで**650億ドル**を調達し評価額**9,650億ドル**（1兆ドル目前）に到達。[[OpenAI]]を抜き世界最高値のプライベートAI企業となった。',
         '売上年換算**470億ドル**でAltimeter Capital・Sequoiaが主導。**Claude Code**の企業向け展開がNetflix・Spotify・KPMGに広がりQ2で**130%増**を記録。',
         '__IPO前最終資金調達とみられ__、1兆ドルバリュエーションへの歴史的な一里塚として市場に衝撃が走った。'], AI)
    + side_card(95,'OpenAIがGoldman Sachs主幹でIPO向け機密S-1書類をSECに提出','CNBC','https://www.cnbc.com/2026/05/20/openai-ipo-filing.html',
        'https://image.cnbcfm.com/api/v1/image/108276787-1773253292188-gettyimages-2265445220-BLACKROCK_INFRASTRUCTURE_SUMMIT.jpeg?v=1779296174&w=1920&h=1080',
        ['[[OpenAI]]が5月22日にSECへ**機密IPO目論見書（S-1）**を提出。**2026年9月上場**を目標に評価額**8,520億〜1兆ドル**を目指す。',
         '[[Goldman Sachs]]・Morgan Stanleyが主幹事。機関投資家への開示を進める段階に入り**ChatGPTの収益化構造**が初めて公式書類として開示される。',
         '__AI覇権企業の上場が現実を帯びる__中、Anthropicの評価額更新と合わせ「AIバブル到達点か転換点か」の議論が加速している。'], AI,'02')
    + side_card(90,'NVIDIA Q1 FY2027決算 — 売上816億ドル+85%、データセンターが全収益の87%','Intellectia AI','https://intellectia.ai/blog/nvda-stock-earnings-analysis-may-2026',
        CDN_AI,
        ['[[NVIDIA]]が**売上816億ドル**（前年比+85%）で過去最高を記録。**データセンター収益391億ドル**（+69%）が全収益の87%を占める。',
         'Sovereign AIが**前年比3倍超の300億ドル超**に達し成長を牽引。次世代**Vera Rubin NVL72**が年後半にAWS・Google Cloudで展開予定。',
         '__AIインフラ需要が飽和を知らない__という決算証明が示され、データセンター投資サイクルの長期化観測がさらに強まった。'], AI,'03')
    + side_card(84,'Metaが「Meta One」サブスク世界展開 — Instagram/Facebook/WhatsApp有料プラン開始','TechCrunch','https://techcrunch.com/2026/05/27/meta-officially-launches-instagram-facebook-and-whatsapp-subscriptions-with-more-to-come-including-ai-plans/',
        'https://techcrunch.com/wp-content/uploads/2026/05/meta-apps-GettyImages-2164040793.jpg?w=1024',
        ['[[Meta]]が月額3.99〜19.99ドルの**Meta Oneサブスクプラン**を世界展開。**Meta One Premium**では高度推論・画像生成強化を提供する。',
         '**SNS広告モデル**から課金モデルへの大転換として注目。**Instagram・Facebook・WhatsApp**3プラットフォームを一括有料化する大胆な戦略。',
         '__AI機能を収益化する新モデルの試金石__として、OpenAI・GoogleとのROI比較が投資家から迫られる展開になる。'], AI,'04')
    + side_card(78,'富士通が自己進化マルチAIエージェント技術を開発 — 業務中に平均28ポイント精度向上','Fujitsu Global','https://global.fujitsu/en-global/pr/news/2026/05/25-01',
        'https://global.fujitsu/-/media/Project/Fujitsu/Fujitsu-HQ/pr/news/common/ogp/news-ogp-ai02.png?rev=b2c92af86d8c4104ad3ab0c62589d393',
        ['[[富士通]]が複数AIエージェントが**チームで業務を遂行しながら失敗から自律学習**する技術を発表。自社LLM「[[Takane]]」適用で**平均28pt精度向上**を確認。',
         '**カーネギーメロン大**との共同研究で軽量版も開発中。**業務インフラに組み込まれる自己進化AI**という概念実証が日本企業初の成果として提示された。',
         '__AIが仕事をしながら賢くなる__設計思想は固定モデル運用から動的学習モデルへのパラダイムシフトを象徴する試みだ。'], AI,'05')
)

# ─── IT-Consulting ─────────────────────────────────────────
IT_B = (
    cat_hdr(3,6,'IT & Consulting','IT-Consulting (IT & Consulting)','▲',IT,
        'AccentureがサウジアラビアPIF傘下HUMAINと官民AI加速パートナーシップを締結。NEC・日立・富士通がAnthropicとそろい踏みの提携を完成させ、日本IT産業の構造転換が決定的となった。',5,'it')
    + top_card(92,'AccentureとHUMAIN、サウジアラビアでAI導入を官民横断で加速','Accenture Newsroom','https://newsroom.accenture.com/news/2026/humain-and-accenture-accelerate-ai-adoption-at-scale-across-public-and-private-sectors-in-saudi-arabia',
        'https://newsroom.accenture.com/default-meta-image.png?width=1200&format=pjpg&optimize=medium',
        ['[[Accenture]]と[[HUMAIN]]（[[サウジアラビア]]PIF傘下）が**戦略的パートナーシップ**を締結。官民のAI実装を**実験段階から本番運用**に移行させる5分野で協業開始。',
         'Vision 2030加速とGCC地域全域への波及を視野に入れた大規模コンサル案件で、**中東市場の主導権**を巡るMBBとの競争が激化する。',
         '__新興AI経済圏でのコンサル覇権争い__という新局面を開く案件で、中東発のAI投資規模がシリコンバレーに匹敵しはじめている。'], IT)
    + side_card(91,'富士通、AnthropicおよびOpenAIと提携し法人向けAI営業を強化','日本経済新聞','https://www.nikkei.com/article/DGXZQOUC275S50X20C26A5000000/',
        'https://article-image-ix.nikkei.com/https%3A%2F%2Fimgix-proxy.n8s.jp%2FDSXZQO3081848027052026000000-1.jpg?auto=compress&bg=FFFF&crop=focalpoint&fit=crop&fm=jpg&h=630&w=1200&s=da72e0b4557ecac8d35e44f22295fc17',
        ['[[富士通]]が[[Anthropic]]と[[OpenAI]]の**両社と提携**を発表。法人向けサービス開発・サイバーセキュリティに**Claude**等を活用する**マルチモデル戦略**を採用。',
         '社内**10万人への展開**も計画。NEC・日立・富士通が相次ぎAnthropicと提携し、**国内IT大手3社がそろい踏み**という歴史的な布陣が完成した。',
         '__AIファースト転換を迫られた日本IT産業__の構造変化を象徴する一幕で、シリコンバレー依存型コンサル再編が本格化する。'], IT,'02')
    + side_card(90,'NEC・日立・富士通が相次ぎAnthropicと提携 — 国内IT大手3社のAI戦略が鮮明','ITmedia ビジネスオンライン','https://www.itmedia.co.jp/business/articles/2605/27/news119.html',
        'https://image.itmedia.co.jp/business/articles/2605/27/cover_news119.jpg',
        ['NECに続き[[日立]]（5/19）・[[富士通]]（5/27）が[[Anthropic]]との協業を発表。**国内IT大手3社がClaude採用**でそろい踏みという前例なき態勢が整った。',
         '各社戦略の核心が明らかになり、**法人向けClaude統合**・[[Anthropic Shock]]への対応・**自社LLMとの併用**という三方向が浮かぶ。',
         '__NECの「8,000兆円消失」警告から半年__、防戦から積極活用へ転換した大手IT各社の意思決定速度が問われる局面。'], IT,'03')
    + side_card(89,'McKinsey、AIエージェントをコンサルタントのチーム配置選定に活用する計画','Bloomberg','https://www.bloomberg.com/news/articles/2026-05-01/mckinsey-plans-to-use-ai-agents-to-help-choose-client-teams',
        CDN_IT,
        ['[[McKinsey]]が**4万人規模のコンサルタント配置にAIエージェント**を活用すると発表。社内AI「[[Lilli]]」は月**50万プロンプト超**を処理し知識業務で**30%時間削減**を実現。',
         '人員配置という**コンサル企業の根幹業務**にAIが入ることで、**プロジェクト粒度のコスト構造**が自動最適化される次世代モデルを先取りする形。',
         '__コンサル選定もAIが行う時代__へ、McKinseyが業界変革の先陣を切る意図が鮮明となった。'], IT,'04')
    + side_card(87,'NTTデータグループ 2025年度決算 — AIフルスタック体制でFY2030 EBITDA1.2兆円へ','NTTデータグループ','https://www.nttdata.com/global/ja/news/release/2026/050806/',
        'https://www.nttdata.com/global/ja/-/media/assets/images/sns_share.png?rev=583d5951ace649c08be7a88679328a3b',
        ['[[NTTデータ]]グループが2025年度通期決算を発表。**AIを中核とした成長戦略**をグローバルで加速する方針を提示し、**FY2030 EBITDA1.2兆円**目標を明示。',
         '新設の**コンサルティングセグメント**が牽引役となり、[[Accenture]]超えを目指す「**AIフルスタック体制**」への転換を本格化させる。',
         '__国内SIerからグローバルコンサルへの脱皮__という宿願が、AIブームに乗じてようやく現実味を帯びてきたターニングポイントの決算。'], IT,'05')
)

# ─── Mobility ──────────────────────────────────────────────
MB_B = (
    cat_hdr(4,6,'Mobility','モビリティ (Mobility)','◎',MB,
        'WaymoがZeekr製第6世代ロボタクシー「Ojai」を公開開放、テキサス州が操縦装置なし自動運転を合法化。IEAは2026年世界EV販売2,300万台（新車の28%）予測を発表、自動運転とEV双方で変革が加速。',5,'mobility')
    + top_card(95,'WaymoがOjai第6世代ロボタクシーを一部ライダーに公開開放 — サンディエゴ・ラスベガス展開へ','CNBC','https://www.cnbc.com/2026/05/28/waymo-opens-ojai-robotaxis-to-some-riders-aims-to-lower-cost-of-fleet.html',
        'https://image.cnbcfm.com/api/v1/image/108261073-1770161242648-gettyimages-2259268678-ALPHABET_EARNS.jpeg?v=1770161334&w=1920&h=1080',
        ['[[Waymo]]が新型ロボタクシー「[[Ojai]]」をSF・LA・フェニックスの一部ユーザーに無料開放。**Zeekr製ワゴン型車体**に**第6世代Driver**を搭載しコスト削減を実現。',
         '現行Jaguar I-PACEより**製造コストが大幅低減**。**週50万件の有償ライド**を既達成しており、今夏**サンディエゴ・ラスベガス・デンバー**にも展開予定。',
         '__ロボタクシー商業化の正念場__を迎えるWaymoが、スケーラブルな次世代車両で勝負に出た。国際展開（ロンドン・東京）も視野に入れている。'], MB)
    + side_card(92,'IEA「Global EV Outlook 2026」 — 世界EV販売2,300万台、新車の28%に','IEA','https://www.iea.org/reports/global-ev-outlook-2026',
        'https://iea.imgix.net/5ba2f5b7-885c-4f0c-b015-fecda65514a8/GlobalEVOutlook2026_GettyImages-1471913353.jpg?auto=compress%2Cformat&fit=min&h=600&q=80&rect=2111%2C322%2C2678%2C2678&w=1200',
        ['[[IEA]]が年次EVレポートを公開。**2026年のEV販売は2,300万台**（新車の28%）に達する見通しで、**中国55%・欧州28%**のシェアを詳報。',
         '中国需要の若干の減速を認めつつも、**補助金政策の効果**と充電インフラ整備の進展が世界的な普及曲線を支えると分析。',
         '__2030年の50%目標に向けた中間点__として、規制・インフラ・価格の三位一体の課題解決状況を業界全体が自己点検する機会となった。'], MB,'02')
    + side_card(90,'テキサス州、操縦装置なし完全自動運転を合法化 — Tesla Cybercab量産加速に直結','TechXplore','https://techxplore.com/news/2026-04-tesla-robotaxi-production-cybercab-ramp.html',
        CDN_MB,
        ['[[テキサス州]]知事がSB2807に署名（5月28日施行）。**ハンドル・ブレーキペダルなし車両**の公道走行を正式許可し、自動運転規制の最前線に立つ。',
         '[[Tesla]]の**Cybercab量産**に直結し、**ダラスで12都市以上**に拡大中のフリートがさらに加速する見通し。**24エーカーの専用フリートセンター**建設計画も進行。',
         '__米州規制の先行実装がWaymoとTeslaの競争を後押し__する構図で、連邦政府の統一ルール整備前に実績地域が増え続けるリスクも孕む。'], MB,'03')
    + side_card(86,'中国当局、Baidu Apollo Go事故後に自動運転ライセンスを一時停止','InsideEVs','https://insideevs.com/news/category/autonomous-vehicles/',
        'https://cdn.motor1.com/custom/share/inside_evs_loadimage.png',
        ['[[Baidu]] [[Apollo Go]]のロボタクシーが武漢で**路上停車・追突事故を複数発生**させ、中国当局が**新規自動運転ライセンスの発行を一時停止**する措置を発動。',
         '**WeRide・Pony.ai**など同地域の競合他社の事業にも影響が波及する可能性があり、中国での**ロボタクシー拡張計画が一時的に足踏み**状態に。',
         '__規制と普及のせめぎ合い__という自動運転産業固有の課題が中国でも顕在化し、安全規制の国際標準化議論が急務となっている。'], MB,'04')
    + side_card(85,'Tesla Cybercabフリートがダラスで拡大 — 12都市以上・専用フリートセンター建設中','Basenor','https://www.basenor.com/blogs/news/tesla-cybercab-fleet-growing-in-dallas-as-robotaxi-launch-nears',
        'https://www.basenor.com/cdn/shop/articles/09bff6b053462291c951d8b4cd4fe4dd.jpg?v=1779909175',
        ['[[Tesla]]が[[ダラス]]に**Cybercabを増車中**。テキサス州内**12都市以上**に拡大済みで、**24エーカーの専用フリートセンター**建設計画も同州内で進行。',
         'テキサス州新法（SB2807）施行と連動し、**Cybercabの完全自動運転サービス**が法的基盤を持つ最初の大規模市場として実証が加速する見通し。',
         '__Waymoとの3,000台格差__という批判に答える意味でも、フリート規模の急拡大は[[Elon Musk]]の2026年末100万台宣言への試金石となる。'], MB,'05')
)

# ─── Economy ───────────────────────────────────────────────
EC_B = (
    cat_hdr(5,6,'Economy','経済 (Economy)','■',EC,
        'S&P500がSnowflake急騰でAI牽引の最高値更新を継続する一方、米4月CPI3.8%が利下げシナリオを再び遠のかせた。Goldman Sachsは年末目標を8,000に引き上げ、日経平均は「5月売り」で一時1000円超安。',5,'economy')
    + top_card(92,'S&P 500・NasdaqがAIけん引で最高値更新 — Snowflake 36%急騰','CNBC','https://www.cnbc.com/2026/05/27/stock-market-today-live-updates.html',
        'https://image.cnbcfm.com/api/v1/image/108313253-1779982389070-Traders-Photo-20260528-KK-PRESS-005.jpg?v=1779982579&w=1920&h=1080',
        ['5月28日の[[S&P500]]は**0.58%高の7,563pt**と最高値を更新。[[Snowflake]]がQ2ガイダンス好調で**36.5%急騰**し、[[Microsoft]]・[[Palantir]]も3〜4%上昇。',
         'NVIDIA Vera Rubin発表後も**AIデータセンター関連株が強含み**継続し、**NASDAQ 26,000台**超えを定着させる相場環境が維持された。',
         '__AIマネーフローが一点集中から幅広いエコシステム株に拡散__し始めた初期兆候として市場関係者がターニングポイントと注目。'], EC)
    + side_card(91,'米インフレ率2026年4月3.8%に加速 — 2023年5月以来の高水準','Trading Economics','https://tradingeconomics.com/united-states/inflation-cpi',
        CDN_EC,
        ['米国の**年間インフレ率が2026年4月に3.8%**へ上昇し、**2023年5月以来の最高水準**を記録。3月の3.3%から急加速した。',
         '**エネルギー価格上昇**（原油+中東リスク）と**食品・住居費の高止まり**が主因。[[Warsh]]議長の「**利下げ封印**」スタンスを裏付ける形になった。',
         '__FRBの年内2回利下げシナリオが崩れ__、高金利継続がドル高・株高の持続力と消費減速リスクの二面性として市場に突き刺さる。'], EC,'02')
    + side_card(90,'日経平均終値306円安 — 「5月売り」観測とSBG続落で一時1000円超安','日本経済新聞','https://www.nikkei.com/article/DGXZQOUB278GHTX20C26A5000000/',
        'https://article-image-ix.nikkei.com/https%3A%2F%2Fimgix-proxy.n8s.jp%2FDSXZQO3085847028052026000000-2.jpg?auto=compress&bg=FFFF&crop=focalpoint&fit=crop&fm=jpg&h=630&upscale=false&w=1200&s=960ab8acca47e2bf9e4a589df858e532',
        ['[[日経平均]]は5月28日終値**306円安の64,693円**。「**5月売り**」の季節的需給と[[ソフトバンクG]]続落が重しとなり、一時**1,000円超の下落幅**を記録した。',
         '史上最高値更新連続から一転、**65,000円台の節目**を巡る攻防が始まった。**PCE・GDP同時公表**後の米市場反応待ちで様子見ムードも交錯。',
         '__最高値更新の熱気と「5月売り」アノマリー__という相反する力学の狭間で、機関投資家がポジション調整を急ぐ短期的な不安定局面。'], EC,'03')
    + side_card(88,'Goldman Sachs、S&P 500年末目標を8,000に引き上げ — AI EPS40%牽引','CNBC','https://www.cnbc.com/2026/05/27/goldman-raises-its-sp-500-year-end-forecast-its-for-one-simple-reason.html',
        'https://image.cnbcfm.com/api/v1/image/108235929-1764773476430-gettyimages-2249102764-AFP_86YD9M4.jpeg?v=1779891901&w=1920&h=1080',
        ['[[Goldman Sachs]]が[[S&P500]]の年末目標を**7,600から8,000**に上方修正。**AI関連投資がEPS成長の約40%**を牽引するとし、2026年EPS予測を**340ドル**に引き上げ。',
         'インフレ懸念・FRB不透明感を踏まえながらも、**決算超過率84%・増益率+27%**という好決算が強気バイアスを正当化すると主張。',
         '__S&P 8,000は現水準から+6%__のレンジで他社ストラテジストが追随するかどうかが今後1〜2週間の注目点となる。'], EC,'04')
    + side_card(88,'日本2026年1〜3月期GDP 実質年率+2.1% — 2四半期連続プラス成長','マネクリ（マネックス証券）','https://media.monex.co.jp/articles/amp/29387',
        'https://media.monex.co.jp/mwimgs/c/3/-/img_c392ac838e0894ef5371a4811d7010bc226832.png',
        ['2026年1〜3月期の[[日本]]の**実質GDP成長率は前期比年率+2.1%**と**2四半期連続のプラス**成長を記録。個人消費の底堅さが支えとなった。',
         'ただし中東情勢不安定化を受けた**先行きの下振れリスクが大きい**と指摘。**輸出の伸び悩み**と原油高起因のコスト上昇が次の懸念材料。',
         '__プラス成長継続と日銀利上げ余地の確保__という組み合わせが、円安対応の政策オプションを広げる鍵となる。'], EC,'05')
)

# ─── Game ──────────────────────────────────────────────────
GM_B = (
    cat_hdr(6,6,'Gaming','ゲーム (Gaming)','●',GM,
        'Forza Horizon 6がSteam同接30万人超でシリーズ最高記録を達成。スクウェア・エニックスが賞金総額10億円の開発コンテストを発表、PlayStation Days of Play 2026も開幕。Cygamesが映像スタジオ買収。',5,'game')
    + top_card(90,'Forza Horizon 6、正式リリース後Steam最高同接30万人超 — シリーズ史上最大ローンチ','Game*Spark','https://www.gamespark.jp/article/2026/05/19/166588.html',
        'https://www.gamespark.jp/imgs/ogp_f/1130232.jpg',
        ['日本を舞台にした[[Forza Horizon 6]]が正式リリース。**Steam最高同接302,645人**を記録し**前作比約3倍**のユーザーを集め、シリーズ史上最大のローンチを達成。',
         '**メタスコアも今年暫定トップ**を獲得。東京・大阪・京都の実写スキャンデータと日本固有の車文化が海外プレイヤーに高く評価された。',
         '__「日本が主役の欧米AAA作品」という稀有なポジション__が全世界のゲーマーを引きつけ、ゲームツーリズム的な経済効果も生まれつつある。'], GM)
    + side_card(88,'スクウェア・エニックス、賞金総額10億円のゲーム開発コンテスト発表','AUTOMATON','https://automaton-media.com/articles/newsjp/20260520-444097/',
        'https://automaton-media.com/wp-content/uploads/2026/05/20260520-444097-header.jpg',
        ['[[スクウェア・エニックス]]が**賞金総額10億円**のゲーム開発コンテストを発表。最優秀賞**3億円**・傑作賞**1億円**で受賞作をスクエニが全面パブリッシング。',
         '個人・チーム問わず参加可で**応募開始は12月15日**。**出版・配信・マーケティングまで全面支援**という破格の条件でインディー開発者を囲い込む戦略。',
         '__大手パブリッシャーが才能発掘に本腰を入れ始めた__という業界転換の象徴で、IndieゲームとAAAゲームの境界線がさらに溶け合う。'], GM,'02')
    + side_card(85,'PlayStation「Days of Play 2026」が5月27日スタート — PS5最大1万円引き','PlayStation Blog Japan','https://blog.ja.playstation.com/2026/05/27/20260527-ps5-daysofplay-retail-psstore-sale-s/',
        'https://blog.ja.playstation.com/tachyon/sites/7/2026/05/53f99ea06acc65275619ffe0caea34d0a1d37c3e.jpg',
        ['[[SIE]]の年次セール「[[Days of Play 2026]]」が5月27日〜6月10日開催。**PS5ハード最大1万円引き**とGhost of Yōtei・Death Stranding 2など**最大85%オフ**セール。',
         '**PS Plus年額も最大33%割引**。Switch 2値上げとタイミングを合わせた**PS陣営の反攻策**として業界が注目する大型施策となった。',
         '__ハード価格の攻防__が夏商戦のコンシューマゲーム市場を形成する鍵となり、任天堂とソニーの価格戦略の対比が鮮明に。'], GM,'03')
    + side_card(80,'Cygames、3DCG/VFXスタジオ「Griot Groove」を買収 — 映像制作の内製化を加速','Anime News Network','https://www.animenewsnetwork.com/news/2026-05-04/cygames-acquires-3dcg-vfx-studio-griot-groove/.237077',
        'https://www.animenewsnetwork.com/thumbnails/crop600x315gIH/cms/news.9/237077/b9f664f9838beac022960c423c023ad2-1024x538.webp',
        ['[[Cygames]]が1996年創業の**3DCG・モーションキャプチャスタジオ[[Griot Groove]]**を完全子会社化。[[進撃の巨人]]・[[チェンソーマン]]等の実績を持つ映像制作会社。',
         '**ゲームムービークオリティの強化**とリアルタイムCGの内製化が主目的。モバイル・コンシューマを超えた**IP総合展開**をさらに加速する。',
         '__ゲーム会社が映像スタジオを取り込む垂直統合戦略__が加速しており、コンテンツ産業の境界線がゲームを中心に再編されつつある。'], GM,'04')
    + side_card(82,'任天堂、Switch 2向け5月リリース9タイトルを確認 — サードパーティも複数参入','Nintendo Life','https://www.nintendolife.com/news/2026/05/nintendo-highlights-multiple-major-third-party-releases-for-switch-2-in-2026',
        'https://images.nintendolife.com/fbba51e86a081/large.jpg',
        ['[[任天堂]]が2026年5月に**Switch 2とSwitch向け9作品**の発売を公式確認。**Indiana Jones and the Great Circle**（5/14）など多ジャンルの大作が揃い踏み。',
         '**値上げ後の完売**が続く中でソフトラインナップの充実が購買意欲を維持。**FF7 Rebirth・Star Fox・Splatoon Raiders**など6月以降も続く巨大コンテンツ波。',
         '__ハード値上げをコンテンツ価値で正当化する戦略__が機能しており、プラットフォームエコシステム設計の巧みさが際立つ。'], GM,'05')
)

# ─── 組み立て ───────────────────────────────────────────────
CATEGORIES_HTML = FX_B + AI_B + IT_B + MB_B + EC_B + GM_B

TOC_ROWS_HTML = (toc_row(1,'為替 Foreign Exchange',5,FX)+toc_row(2,'AI Artificial Intelligence',5,AI)
    +toc_row(3,'IT-Consulting IT & Consulting',5,IT)+toc_row(4,'モビリティ Mobility',5,MB)
    +toc_row(5,'経済 Economy',5,EC)+toc_row(6,'ゲーム Gaming',5,GM))

REFLECTION_TITLE = '1兆ドルとCPI3.8%の衝突'
REFLECTION_SUBTITLE = 'AIバリュエーション膨張と米インフレ再加速が同日に起きた2026年の根本矛盾'
REFLECTION_LEAD_HTML = hl(
    '本日6分野・30件のニュースから浮かび上がる最大のテーマは[[Anthropic]]の**評価額9,650億ドル**到達と**米CPI3.8%加速**という真逆のシグナルの同時進行である。'
    'AIへの資本流入が歴史的速度で拡大する一方、高金利の長期化が実体経済に重くのしかかる構図は、Waymoのロボタクシー拡大・Goldman Sachsの強気目標引き上げにも反映されている。'
    '__金融相場から実体相場へ──2026年後半の決定的なテーゼ転換__が始まっているかもしれない。', '#C9B98A')

REFLECTION_PULL_QUOTE_HTML = ('「単一のAI企業の評価額」が「利下げ観測」を上回る速度で膨張する日、それが今日だ。'
    '<span style="border-bottom:2px solid #8E2A19;padding-bottom:1px;">金融政策の天井とAIの底なし井戸</span>。')

REFLECTION_SECTIONS_HTML = (
    sec_row('01','総論','1兆ドルとCPI3.8%の同時撃',
        hl('[[Anthropic]]の評価額が**9,650億ドル**に到達した同じ日、米インフレが**3.8%**という3年ぶりの高水準を記録した。AI企業の「夢の値段」と中央銀行の「現実の温度」が乖離し続けるという構造矛盾が、本日30件のニュースを貫く通底テーマだ。__利下げ封印__というFed新議長の宣言は、高金利下でも続く**AIバリュエーション膨張**と鋭く対立する。この二律背反は今後6か月の相場の核心となる。', '#1A1A1A'), '#1A1A1A')
    + sec_row('02','為替','東京CPI・介入実績・イラン合意の三重奏',
        hl('[[米・イラン]]の**60日停戦合意**がドル安をもたらし、ドル円は**159円台前半**まで下落した。同時に[[財務省]]が**4〜5月分の介入実績**を月次公表し、推計10兆円超の規模が確定された。**東京CPI速報値**は日銀が6月利上げに踏み切れるかどうかの最終判断材料となる。__為替は今日初めて「中東・インフレ・日銀」という三頭立ての馬車で動いた__。', FX), FX)
    + sec_row('03','AI','Anthropic1兆ドル、OpenAI上場申請、NVIDIA 816億ドル',
        hl('本日のAI分野は歴史的な密度の日だ。[[Anthropic]]が**650億ドル調達**で評価額**9,650億ドル**に。[[OpenAI]]は**IPO向けS-1**をSECに機密申請。[[NVIDIA]]は**Q1売上816億ドル+85%**という過去最高を記録した。3社合計のインパクトは2026年AI産業の**「金融化」が完成期に入った**ことを告げている。__AI評価額は誰も止められない__。', AI), AI)
    + sec_row('04','IT','日本IT大手3社がそろい踏み、中東AI覇権戦争が開幕',
        hl('[[NEC]]・[[日立]]・[[富士通]]がAnthropicと相次いで提携し、**国内IT大手3社がClaudeで揃い踏み**という前例なき構図が完成した。同時に[[Accenture]]が[[サウジアラビア]]PIF傘下の[[HUMAIN]]と官民AI加速PFを締結し、**中東市場の覇権争い**がシリコンバレー発から世界展開フェーズへ移行した。__日本とアラビア半島が同日にAI転換の局面を迎えた__という偶然の一致に注目したい。', IT), IT)
    + sec_row('05','モビリティ','Ojai・テキサス合法化・IEA 2300万台の三連打',
        hl('[[Waymo]]の**第6世代ロボタクシー「Ojai」**が公開開放され、[[テキサス州]]が操縦装置なし車両の公道走行を合法化した。[[IEA]]の**Global EV Outlook 2026**は世界EV販売**2,300万台**（新車の28%）予測を提示。一方で中国では[[Baidu]] Apollo Goの事故で**自動運転ライセンスが一時停止**という規制リスクも顕在化した。__モビリティ革命は規制との絶えない綱引きの中でしか前進できない__。', MB), MB)
    + sec_row('06','経済','S&P最高値・Goldman 8000・日経5月売りの三叉路',
        hl('[[S&P500]]が[[Snowflake]]急騰でAIけん引の最高値更新を継続する一方、[[日経平均]]は「**5月売り**」アノマリーで一時**1,000円超安**と乱高下した。[[Goldman Sachs]]が年末目標を**7,600→8,000**に引き上げ、日本の**GDP+2.1%**も日銀利上げ余地を支持する。__強気と弱気が並走する夏前の乱流期__に入った可能性がある。', EC), EC)
    + sec_row('07','ゲーム','Forza 30万人・スクエニ10億円・SIE攻勢の夏商戦号砲',
        hl('[[Forza Horizon 6]]が**Steam最高同接302,645人**でシリーズ最高記録を更新。[[スクウェア・エニックス]]が**賞金総額10億円**のコンテストでインディー開発者を囲い込み、[[SIE]]が**Days of Play 2026**でSwitch 2値上げに対抗する大型セールを展開。__Switch 2の値上げとPS陣営の値下げが交差する夏商戦__の号砲が鳴った。', GM), GM)
    + sec_row('08','明日へ','6月の焦点：日銀会合・Anthropic IPO観測・FF7 Rebirth',
        hl('来週6月はAI・金融・ゲームの三分野で重要イベントが集中する。**日銀6月16〜17日会合**（利上げ判断）、**OpenAI上場スケジュール確認**（IPO申請後の次の動き）、**FF7 Rebirth・Star Fox Switch 2**（任天堂夏コンテンツ波）。__Anthropicが1兆ドルを突破するか・日銀が利上げに踏み切るか__の二大問いが、6月の相場の縦糸となる。', '#C9B98A'), '#C9B98A')
)

TAKEAWAYS_HTML = (
    tkw('01','為替',hl('[[東京CPI速報+財務省介入実績]]という「二重確認」が本日揃い、日銀の**6月利上げ判断**は事実上今日決まる。__円相場の構造転換を占う歴史的なデータポイント__。',FX),FX)
    + tkw('02','AI',hl('[[Anthropic]] **9,650億ドル**・[[OpenAI]] S-1提出・[[NVIDIA]] **816億ドル決算**が同日重複。「**AIバブル到達点か持続的成長か**」という問いに対し、今日の3つの事実は「まだ持続中」と答えた。',AI),AI)
    + tkw('03','産業',hl('国内IT大手3社がAnthropicとそろい踏みでAI導入を宣言。**「Anthropic Shock」警告から半年**で防衛から攻勢へ転じた速度は、__日本企業のAI対応スピードが想定より速い__ことを示す。',IT),IT)
)

RELATED_ISSUES_HTML = (
    rel_row('2026-05-28','前号: FOMC議事録・PCE・日経最高値','obsidian://open?vault=New%27s%20Grasp&file=News-Grasp%2Fdigest%2FSummary%2F2026-05-28')
    + rel_row('2026-05-27','OpenAI DeployCo始動・日経65,000突破','obsidian://open?vault=New%27s%20Grasp&file=News-Grasp%2Fdigest%2FSummary%2F2026-05-27')
    + rel_row('2026-05-26','Forza H6発表・Switch 2値上げ解説','obsidian://open?vault=New%27s%20Grasp&file=News-Grasp%2Fdigest%2FSummary%2F2026-05-26')
)

# ─── 出力 ──────────────────────────────────────────────────
result = TEMPLATE
result = result.replace('{{ISSUE_NO}}','20260529')
result = result.replace('{{ISSUE_DATE}}','2026-05-29')
result = result.replace('{{ISSUE_WEEKDAY}}','木')
result = result.replace('{{TOTAL_CATEGORIES}}','6')
result = result.replace('{{TOTAL_STORIES}}','30')
result = result.replace('{{TOTAL_SECTIONS}}','8')
result = result.replace('{{WEB_BASE_URL}}',WEB_BASE)
result = result.replace('{{ISSUE_WEB_URL}}',f'{WEB_BASE}/summary/2026-05-29/')
result = result.replace('{{TOC_ROWS_HTML}}',TOC_ROWS_HTML)
result = result.replace('{{CATEGORIES_HTML}}',CATEGORIES_HTML)
result = result.replace('{{REFLECTION_TITLE}}',REFLECTION_TITLE)
result = result.replace('{{REFLECTION_SUBTITLE}}',REFLECTION_SUBTITLE)
result = result.replace('{{REFLECTION_LEAD_HTML}}',REFLECTION_LEAD_HTML)
result = result.replace('{{REFLECTION_PULL_QUOTE_HTML}}',REFLECTION_PULL_QUOTE_HTML)
result = result.replace('{{REFLECTION_SECTIONS_HTML}}',REFLECTION_SECTIONS_HTML)
result = result.replace('{{TAKEAWAYS_HTML}}',TAKEAWAYS_HTML)
result = result.replace('{{RELATED_ISSUES_HTML}}',RELATED_ISSUES_HTML)
result = re.sub(r'<!--.*?-->','',result,flags=re.DOTALL)
result = re.sub(r'\n{3,}','\n\n',result)
OUTPUT.parent.mkdir(exist_ok=True)
OUTPUT.write_text(result,encoding='utf-8')
size_kb = OUTPUT.stat().st_size/1024
print(f'build/email.html を生成しました ({size_kb:.1f} KB)')
