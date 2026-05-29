#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""2026-05-24 backfill email HTML 生成スクリプト"""
import pathlib, textwrap

OUT = pathlib.Path(r"C:\Users\hidek\Obsidian\New's Grasp\News-Grasp\build\email.html")

# ── ヘルパー ──────────────────────────────────────────────────────────────────

def hl(text, cat):
    """[[keyword]] → highlight span"""
    import re
    colors = {"fx":"184,134,11","ai":"45,91,184","it":"46,107,82","game":"94,61,140"}
    c = colors.get(cat, "26,26,26")
    def rep(m):
        kw = m.group(1)
        return f'<strong style="background:rgba({c},0.13);padding:0 3px;">{kw}</strong>'
    return re.sub(r'\[\[([^\]]+)\]\]', rep, text)

def ul(text):
    """__phrase__ → underline span"""
    import re
    return re.sub(r'__([^_]+)__',
        r'<span style="border-bottom:2px solid #1A1A1A;padding-bottom:1px;">\1</span>',
        text)

def fmt(text, cat):
    return ul(hl(text, cat))


# ── 記事データ ────────────────────────────────────────────────────────────────

FX_CDN  = "https://raw.githubusercontent.com/HIDEPON-UMG/news-grasp-assets/main/ng-thumb-fx.jpg"
AI_CDN  = "https://raw.githubusercontent.com/HIDEPON-UMG/news-grasp-assets/main/ng-thumb-ai.jpg"
IT_CDN  = "https://raw.githubusercontent.com/HIDEPON-UMG/news-grasp-assets/main/ng-thumb-it.jpg"
GM_CDN  = "https://raw.githubusercontent.com/HIDEPON-UMG/news-grasp-assets/main/ng-thumb-game.jpg"

ARTICLES = {
  "fx": [
    {
      "score": 91,
      "title": "来週5/25週のドル円——PCE・東京CPI・FOMC議事録が円高/円安の分水嶺",
      "url": "https://www.gaitame.com/media/entry/2026/05/24/120000_1",
      "source": "外為どっとコム",
      "date": "2026-05-24 09:00",
      "thumb": FX_CDN,
      "bullets": [
        "来週（5/25週）の重要スケジュールは[[5/28 FOMC議事録]]・5/29 米GDP改定値・5/30 PCEコア価格指数の三連弾。5/25はメモリアルデーで米国全休のため、相場は火曜以降に本番を迎える。",
        "PCEが市場予想3.5%を上回れば[[FRB利下げ後退]]観測が高まりドル買い優勢。逆に東京CPIが前月比加速なら日銀利上げ期待が再燃し__円買いが波状的に広がるシナリオ__が待つ。",
        "ドル円のコアレンジを158〜161円と想定。[[160円手前]]の介入警戒ラインは依然有効で、MOFの口頭介入で158円割れを目指す動きが週中に起きれば好機と見るトレーダーも多い。",
      ]
    },
    {
      "score": 88,
      "title": "日本4月輸出14.8%増・貿易黒字3,019億円——半導体・自動車好調が円の上値サポートに",
      "url": "https://www.cnbc.com/2026/05/21/japan-exports-semiconductor-autos-imports-trade.html",
      "source": "CNBC",
      "date": "2026-05-24 08:30",
      "thumb": "https://image.cnbcfm.com/api/v1/image/107137735-1666228139613-gettyimages-1235971665-JAPAN_TRADE.jpeg?v=1734478196&w=1920&h=1080",
      "bullets": [
        "日本の4月輸出が前年比[[14.8%増]]（市場予想9.3%を大幅超過）、貿易収支は[[3,019億円の黒字]]。前年同月の1,495億円赤字から一転し、11月以来最大の月次黒字を記録した。",
        "半導体・電子部品・自動車向け輸出がけん引。円安効果と相まって__輸出主導の経常黒字拡大が中期的な円安修正圧力として機能__する可能性が高まった。",
        "5月初旬の約5兆円介入後もドル円は158円台へ戻しており、[[ファンダメンタルズ改善]]が介入効果よりも機能するシナリオが現実化しつつある。",
      ]
    },
    {
      "score": 82,
      "title": "G10 FX 5月展望——AUD/USDキャリー通貨に浮上、GBPは英国政治リスクで上値抑制",
      "url": "https://think.ing.com/articles/g10-fx-talking-may-2026/",
      "source": "ING Think",
      "date": "2026-05-24 10:00",
      "thumb": "https://think.ing.com/uploads/hero/_w800h450/shutterstock_editorial_15109405b_%281%29_1.jpg",
      "bullets": [
        "[[AUD/USD]]はRBA利下げ観測後退とコモディティ需要堅調を背景に0.72台で底堅く、G10通貨の中で「キャリー通貨の最有力候補」に格上げされた。",
        "GBP/USDは[[Labour党首交代論]]がリスク要因として浮上。Starmer首相続投が相場安定の最善シナリオで、政治的不確実性が__GBPの上値を1.34〜1.35台に抑える__構図は2〜3ヶ月続く見通し。",
        "EUR/USDは[[1.17台]]を維持。ECBの利上げ確率86%が高水準で織り込まれているが、地政学リスクとエネルギー価格の高止まりがユーロの上値を制限する。",
      ]
    },
    {
      "score": 80,
      "title": "JPMorgan: ドル強含み継続——インフレ高止まりとWarsh議長タカ派でドル優位が続く理由",
      "url": "https://www.jpmorgan.com/insights/global-research/currencies/currency-volatility-dollar-strength",
      "source": "JPMorgan",
      "date": "2026-05-24 07:30",
      "thumb": "https://www.jpmorgan.com/content/dam/jpm/cib/complex/content/research/forex_volatility/Forex_Banner.jpg",
      "bullets": [
        "JPMorganはドルの強含みが当面継続するシナリオを提示。[[Warsh FRB議長]]がタカ派姿勢を鮮明にするなか、コアインフレが3%台後半で高止まりしており利下げへの転換には時間がかかると分析する。",
        "米国の「[[成長+インフレ]]」の組み合わせが他の主要国と対照的に機能し、金利差が投資家のドルロング継続を動機付けている。",
        "一方で__ドル高が持続するリスクは米国自身の財政赤字拡大__にもある。2026年の赤字がGDP比6.5%に達する見通しの中、6〜12ヶ月ベースではドル小幅安シナリオも共存する。",
      ]
    },
    {
      "score": 75,
      "title": "ドル円週末158.88円クローズ——薄商いのメモリアルデー週へ、PCE/FOMC議事録が方向性を決する",
      "url": "https://www.fxstreet.com/currencies/usdjpy",
      "source": "FXStreet",
      "date": "2026-05-24 07:00",
      "thumb": FX_CDN,
      "bullets": [
        "ドル円は5/24（土）の週末クローズが[[158.88円]]。5/22のNYクローズ（159.18円）から0.19%円高に推移し、日本の貿易収支改善を受けた円の持ち直しが小幅に作用した。",
        "5/25はメモリアルデーで米国市場が全休のため、週明けは火曜日まで[[薄商い]]が続く見通し。__MOFの口頭介入シグナルには特に注意__が必要な週となる。",
        "週後半のPCEコア（5/30）とFOMC議事録（5/28）が方向性決定因子。市場コンセンサスはPCE3.4%・タカ派トーン継続で、この読み通りならドル買い継続となる。",
      ]
    },
  ],
  "ai": [
    {
      "score": 93,
      "title": "GPT-5.5 Instant、ChatGPT新デフォルト——「規制分野の幻覚低減」を前面にOpenAIが企業市場攻勢",
      "url": "https://whatllm.org/blog/new-ai-models-may-2026",
      "source": "WhatLLM",
      "date": "2026-05-24 09:00",
      "thumb": "https://whatllm.org/opengraph-image",
      "bullets": [
        "[[GPT-5.5 Instant]]がChatGPTのデフォルトモデルに昇格。OpenAIが強調するのは「規制分野（医療・法律・金融）における幻覚率の大幅低減」で、同等タスクを30〜40%低いコストで処理できるアーキテクチャ改良が施された。",
        "レイテンシとコストの最適化が主眼で、__企業API利用者にとって直接のコスト削減__につながる設計。AnthropicのClaude APIとの価格競争が激化する。",
        "[[McKinsey・BCG・Accenture]]がOpenAI Frontier Alliancesに参画し、GPT-5.5系を成果連動型フィーで展開するエコシステムが完成形に近づきつつある。",
      ]
    },
    {
      "score": 89,
      "title": "Gemini 3.5 Flash GA——$1.50/$9/1Mトークン・コーディングとエージェントで3.1 Proを超えるコスパ実現",
      "url": "https://llm-stats.com/llm-updates",
      "source": "LLM Stats",
      "date": "2026-05-24 08:00",
      "thumb": "https://llm-stats.com/og/main.png",
      "bullets": [
        "[[Gemini 3.5 Flash]]が一般提供（GA）を開始。入力$1.50・出力$9.00/1Mトークンという価格設定はClaude 3.7 Sonnetの約40%安。Googleは「コーディングとエージェントタスクでGemini 3.1 Proを上回る」と主張している。",
        "1Mコンテキストウィンドウを維持しながらのGA価格下落は[[エージェント型AIの民主化]]を意味し、スタートアップ・中堅企業がエンタープライズ級のAIエージェントを構築しやすくなる。",
        "Google I/O（5/19）からわずか1週間でのGA移行は__「速さと低コスト」で差別化する路線が鮮明__になっており、価格主導の市場再編が進む。",
      ]
    },
    {
      "score": 87,
      "title": "Claude Security パブリックベータ——Project Glasswingで1万件超の脆弱性を発見した能力を企業に解放",
      "url": "https://www.anthropic.com/glasswing",
      "source": "Anthropic",
      "date": "2026-05-24 10:00",
      "thumb": AI_CDN,
      "bullets": [
        "[[Claude Security]]がパブリックベータを開始。Project Glasswing（参加企業50社超）でClaude Mythos Previewが[[1万件超の高・重大レベル脆弱性]]を発見した実績をベースに企業向けセキュリティスキャニングとして正式提供を開始する。",
        "対象は「認証を受けたセキュリティチーム」で、コードベースのスキャン・脆弱性トリアージ・自動修正案生成を行う。__AIが攻撃者視点で実際に悪用可能なパスを探索する「攻撃的防御」__アプローチを採用している。",
        "Anthropicは同時に$100Mのモデル利用クレジットとオープンソース改善費$4Mを拠出し、AI時代のセキュリティインフラ構築への非収益的コミットメントを示した。",
      ]
    },
    {
      "score": 85,
      "title": "Anthropic、企業AI市場25%確保——OpenAIから顧客流入、軍事応用拒否姿勢が差別化の核心に",
      "url": "https://www.axios.com/2026/05/14/anthropic-claude-price-openai-tokens",
      "source": "Axios",
      "date": "2026-05-24 07:30",
      "thumb": "https://images.axios.com/sxwE8M5Rti7vSpqmks3lifdeuRc=/0x0:1590x894/1366x768/2026/05/14/1778719461763.png",
      "bullets": [
        "[[Anthropic]]は企業向けAIサブスクリプション市場で約25%のシェアを確保。OpenAIが軍事AI応用への接近姿勢を強める中、AnthropicがそのOpenAIから顧客を引き込んでいる構図が判明した。",
        "差別化の核心は「[[軍事応用拒否]]」という明示的な姿勢。医療・法律・教育セクターの企業はAnthropicの「Constitutional AI」方針に信頼を置いており、GDPRや医療倫理規制への適合性でも高評価を受ける。",
        "ただし__トークン単価はAnthropicの方が高い傾向__にあり、「倫理的差別化×高価格」戦略の持続可能性が長期的な競争力の焦点となっている。",
      ]
    },
    {
      "score": 80,
      "title": "Claude次の戦場はモデルでなくエージェント制御プレーン——VentureBeatが読む企業AI覇権争いの新局面",
      "url": "https://venturebeat.com/orchestration/claudes-next-enterprise-battle-is-not-models-its-the-agent-control-plane",
      "source": "VentureBeat",
      "date": "2026-05-24 11:00",
      "thumb": "https://images.ctfassets.net/jdtwqhzvc2n1/QIFFk030xew6nEvO7DFQB/2e91ff2cbababc24cd63134f601983a0/ChatGPT_Image_May_15__2026__09_09_07_AM.png?w=800&q=75",
      "bullets": [
        "モデル性能の差が縮まりつつある今、企業AIの次の主戦場は「[[エージェント制御プレーン]]」——複数AIエージェントのオーケストレーション・権限管理・監査ログをどのベンダーが握るかという問いに移っている。",
        "Anthropicは「Agent Control Plane」に相当する機能を開発中とされ、Claude APIの上位レイヤーとしてエンタープライズの業務フロー全体を制御する基盤を目指す。__AWSのBedrock・Microsoft CopilotとのAPIレイヤー主導権争い__が本格化する。",
        "[[MCP（Model Context Protocol）]]のようなオープン規格が普及するかどうかが業界構造を左右するポイントとなっている。",
      ]
    },
  ],
  "it": [
    {
      "score": 93,
      "title": "McKinsey・PwC・EYが幹部秘書職を削減——AI自動化が白カラー支援業務を代替、業界の構造変化が加速",
      "url": "https://www.bloomberg.com/news/features/2026-05-21/mckinsey-pwc-and-ey-lay-off-executive-assistants-as-ai-accelerates",
      "source": "Bloomberg",
      "date": "2026-05-24 08:30",
      "thumb": IT_CDN,
      "bullets": [
        "Bloombergの報道によると、[[McKinsey・PwC・EY]]の3社が幹部秘書（Executive Assistant）職を相次ぎ削減。PwCは米国で約600名、McKinseyは200名超の技術・支援スタッフを削減済み。[[Big4のうち3社が支援職を整理]]という同時進行が確認された。",
        "削減対象は「スケジュール管理・調査・報告・文書作成」といった定型業務群で、__内部AIツール（Microsoft Copilot/専用AI）への移行が人件費圧縮の直接要因__となっている。",
        "バックオフィス（ミドルレイヤー）の静かな収縮として進んでいる点が特徴的。コンサル産業の「__人×時間=フィー__」モデルが内部から溶解していくサインとして受け止められている。",
      ]
    },
    {
      "score": 88,
      "title": "NTT DATA、WinWire買収でエンタープライズAIをMicrosoft Azureで加速——全産業のエージェントAI採用を拡大",
      "url": "https://www.nttdata.com/global/en/news/press-release/2026/may/051800",
      "source": "NTT DATA Global",
      "date": "2026-05-24 09:00",
      "thumb": "https://www.nttdata.com/global/en/-/media/assets/images/sns_share.png?rev=583d5951ace649c08be7a88679328a3b",
      "bullets": [
        "[[NTT DATA]]が5月15日付でWinWire（米国のMicrosoft専門AIパートナー、約850名）の買収に正式合意。WinWireはAgentic AI・AI on Azure・データエンジニアリングを専門とし、Microsoft Gold Partner資格を最上位レベルで保有している。",
        "この買収でNTT DATAは[[Microsoft Azureエコシステム]]での実装力を大幅に強化。顧客産業は製造・金融・小売・医療にまたがる多様な垂直市場。",
        "統合後は「Azure AI Studio + NTT DATA LITRON Builder」の組み合わせで[[AIエージェントの本番稼働]]を6ヶ月以内に実現するパッケージを展開する計画。__NTTデータとして初のアジェンティックAI実装全社規模の買収__と位置付けられる。",
      ]
    },
    {
      "score": 84,
      "title": "NTT DATAがグローバルデータセンター事業を再編——AIとクラウド需要急増でグローバル統括体制へ移行",
      "url": "https://services.global.ntt/en-us/newsroom/ntt-data-globalizes-sales-and-client-services-of-its-data-center-business",
      "source": "NTT Global",
      "date": "2026-05-24 10:00",
      "thumb": "https://services.global.ntt/-/media/ntt/global/newsroom/ntt-blue-logo-2.jpg?rev=267dd8aab4f74063969e661cd164a44c",
      "bullets": [
        "NTT DATAが5月21日、グローバルデータセンタービジネスの営業・顧客サービス機能を一元化する組織変更を発表。AIワークロードとクラウドインフラへの需要急増を受け、[[サイロ体制からグローバル統括体制]]に移行する。",
        "親会社NTTはすでに[[$16.4B規模でNTT DATAの株式公開分を買い戻し]]、完全非公開化を推進中。グローバルデータセンターの再編はこの統合戦略の具体的な実行フェーズ。",
        "ほぼ1ギガワット規模の新データセンター容量を計画中で、__競合の商業クラウドと「中立インフラ」として差別化__するNTTの戦略が具体化しつつある。",
      ]
    },
    {
      "score": 80,
      "title": "Deloitte Tech Trends 2026——「アダプティブ企業」と「AI基盤化」が2大テーマ、人間×機械融合が競争優位に",
      "url": "https://www.deloitte.com/us/en/insights/topics/technology-management/tech-trends.html",
      "source": "Deloitte Insights",
      "date": "2026-05-24 07:00",
      "thumb": "https://media.deloitte.com/is/image/deloitte/US188546_Social:1200-x-627",
      "bullets": [
        "Deloitteが公表したTech Trends 2026の主要テーマは[[アダプティブ企業]]（変化に動的に適応する組織）と「AIの基盤インフラ化」。AIはもはやツールではなく電力や通信網と同じ「産業基盤」として位置付けられている。",
        "上位トレンドは「Agentic Architectures」「Human×Machine Collaboration」「Trust Architecture（AI信頼設計）」の3つ。__従来の「AI活用」から「AIとの協働で組織を再設計する」フェーズへの移行__が宣言されている。",
        "Deloitteの調査では74%の企業がAIに高い期待を持つ一方、実際の成果を上げているのは[[わずか20%]]。このギャップを「死の谷」と呼び、その橋渡しを担うのがアジェンティックAI実装の専門コンサルタントだとDeloitteは自社を位置付ける。",
      ]
    },
    {
      "score": 77,
      "title": "McKinseyリストラが示すコンサル業界への警告——AIがジュニア層業務を代替し、業界の階層構造自体が揺らいでいる",
      "url": "https://www.fastcompany.com/91463039/why-the-mckinsey-layoffs-are-a-warning-signal-for-consulting-in-the-ai-age-ai-layoffs-management-consulting",
      "source": "Fast Company",
      "date": "2026-05-24 11:00",
      "thumb": IT_CDN,
      "bullets": [
        "Fast Companyの分析は、McKinseyの削減を「コンサル業界の構造変化の氷山の一角」と位置付ける。問題は幹部秘書だけでなく、[[ジュニアコンサルタント・アナリスト]]という「コンサルの梯子」の底部を成す層がAIで代替されつつある点にある。",
        "従来の「新卒→2年でPA昇格→5年で管理職」というピラミッドモデルは、__生成AIが最も得意とする__高ボリューム・構造化・文書集約型タスクをジュニアが担うことで成立していた。",
        "Accentureが新卒採用を逆説的に増やすなか、McKinseyが削減するという対照的な動きは、[[コンサル業界の二極分化]]が2026年の中核テーマとして定着しつつあることを示す。",
      ]
    },
  ],
  "game": [
    {
      "score": 92,
      "title": "Switch 2値上げ前日に購入騒動——Bic Camera等が制限措置、転売抑制と旧価格駆け込みが交錯",
      "url": "https://www.notebookcheck.net/Switch-2-price-increase-in-Japan-causes-hysteria-as-stores-restrict-console-sales.1293250.0.html",
      "source": "Notebookcheck",
      "date": "2026-05-24 09:30",
      "thumb": "https://www.notebookcheck.net/fileadmin/Notebooks/News/_nc5/Switc2Japan.jpg",
      "bullets": [
        "5月24日（日本時間）、[[Switch 2]]翌日の値上げ（¥49,980→¥59,980）を前に全国の量販店で在庫争奪が起きた。[[Bic Camera]]は「ブランドカード保有者限定」という販売制限を導入し、転売目的の大量購入を阻止しようとした。",
        "価格改定の公式理由はAI半導体需要急増による[[メモリチップ高騰]]と米国関税・円安の三重苦。任天堂の来期出荷目標16.5M台（前期比▲3M台修正）の重石になる可能性がある。",
        "__¥59,980という任天堂史上最高値__となるSwitch 2は、品薄プレミアム効果で需要の長期持続も示す逆説的な兆候となっている。",
      ]
    },
    {
      "score": 85,
      "title": "PlayStation State of Play、6月2日開催確定——SIEが夏向けPS5タイトル最新ラインナップを世界に発信へ",
      "url": "https://www.eventhubs.com/news/2026/may/20/playstation-state-play-june-2nd/",
      "source": "EventHubs",
      "date": "2026-05-24 08:00",
      "thumb": "https://media.eventhubs.com/images/2026/05/20_state-play-bnrt.webp",
      "bullets": [
        "[[SIE（Sony Interactive Entertainment）]]が「PlayStation State of Play」を[[6月2日（火）]]に開催すると正式発表。Switch 2のStar Fox（6/25）・Splatoon Raiders（7/23）に対抗する夏向けPS5タイトルのラインナップを世界に向けてライブ配信する。",
        "コミュニティが期待する発表には「Resident Evil Requiem」の最終映像・「Metal Gear Solid 4 PS5版」のリリース日・「Konami Rev. Noir」の発売確定が含まれる。",
        "6/2のState of Playは任天堂の6/3 FF VII Rebirth発売の前日という絶妙なタイミング。__互いの夏商戦ラインナップが1日差で発信される__「ゲーム業界の情報戦」が本格化する週となる。",
      ]
    },
    {
      "score": 82,
      "title": "ウマ娘、累計$2.5B達成・直近4年で最高売上記録日——英語版グローバル展開がCygamesに新収益の基軸をもたらす",
      "url": "https://www.pocketgamer.biz/umamusume-pretty-derby-hits-25bn-after-most-lucrative-day-in-four-years/",
      "source": "PocketGamer.biz",
      "date": "2026-05-24 07:30",
      "thumb": "https://media.pocketgamer.biz/images/135886/87480/uma-musume-pretty-derby-silence-suzuka-race_l1200.jpg",
      "bullets": [
        "[[ウマ娘 プリティーダービー]]が累計収益[[＄2.5B（約3,800億円）]]を突破し、直近4年間で最高の1日売上を記録した。英語版グローバル展開（2025年6月から）が数字の押し上げに直接貢献しており、グローバル月次で50%超の収益が日本国外から生まれている。",
        "SensorTowerによれば5月の月次収益は$63.1M（前月比+17.1%）で、App Store順位を10位上昇させた。[[ケンタッキーダービー]]スポンサーとして現地PRを展開した5月初旬の効果が持続している。",
        "$2.5Bというマイルストーンは__「同人的IPから産業的IPへ」転換する象徴的な数字__として業界に刻まれた。「馬×アイドル×競馬」というユニークジャンルが英語圏でも機能したことを証明している。",
      ]
    },
    {
      "score": 75,
      "title": "Zenless Zone Zero v2.x最終フェーズ——v3.0（6/17）直前のバナー動向とmiHoYo 5月モバイル収益",
      "url": "https://revenue.ennead.cc/games/zenless",
      "source": "GACHA REVENUE",
      "date": "2026-05-24 10:30",
      "thumb": GM_CDN,
      "bullets": [
        "[[Zenless Zone Zero]]（miHoYo/HoYoverse）はv3.0を6月17日にリリース予定。v2.xの最終フェーズとなる現在、新都市「海螺デパート」実装に向けたミランダ・フィンレイの最終バナーが集金機能を担っている。",
        "グローバル収益はGenshin ImpactとHonkai: Star Railに比べ低水準だが、日本市場では[[ダウンロード比率6.3%に対し収益比率29%]]という高ARPU構造を維持している。",
        "miHoYoの次の主力タイトルとして「wild-world」（仮称）が開発中とされ、__スマホゲーム市場全体のARPU収縮の中でmiHoYoの高ARPUモデルが業界の羅針盤的存在__として注目されている。",
      ]
    },
    {
      "score": 72,
      "title": "Switch 2 5月後半リリーススケジュール——Stray Switch 2版（5/28）を控え波状展開が続く",
      "url": "https://nintendoeverything.com/nintendo-release-schedule-may-2026/",
      "source": "Nintendo Everything",
      "date": "2026-05-24 07:00",
      "thumb": "https://nintendoeverything.com/wp-content/uploads/Nintendo-release-schedule-May-2026.webp",
      "bullets": [
        "Nintendo EverythingのSwitch 2・5月発売スケジュールまとめによると、5/28には人気猫探索ゲーム「[[Stray（Switch 2ネイティブ版）]]」がリリース予定。2022年PCで話題をさらった作品のSwitch 2最適化版として期待が高い。",
        "5月後半のニンテンドーeShopチャートは[[Tomodachi Life: Living the Dream]]が首位を独走中。6/3 FF VII Rebirthを控え、大型RPG待ちのユーザーがインディタイトルを消費する「合間商戦」の構図が続く。",
        "任天堂の月次リリーススケジュール展開の緻密さは__「常に次のタイトルを購入動機として存在させる」という戦略__を体現しており、Tomodachi Life→Stray→FF VII Rebirth→Star Fox→Splatoon Raidersという夏への波状展開が値上げ後の需要維持に機能する。",
      ]
    },
  ],
}

CATS = [
  ("fx",   "¥",  "#B8860B", "acFx", "為替",       "Foreign Exchange",    "FOREIGN EXCHANGE",   1),
  ("ai",   "◆", "#2D5BB8", "acAi", "AI",          "Artificial Intelligence", "ARTIFICIAL INTELLIGENCE", 2),
  ("it",   "▲", "#2E6B52", "acIt", "IT-Consulting","IT & Consulting",    "IT & CONSULTING",    3),
  ("game", "●", "#5E3D8C", "acGm", "ゲーム",      "Gaming",              "GAMING",             4),
]

# ── ヘルパー関数 ──────────────────────────────────────────────────────────────

def meta_line(score, date, source, cat_accent):
    return f'''<div class="ng-card-meta" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:11px;color:#5C5A52;letter-spacing:0.5px;margin-bottom:10px;">
        <span style="background:{cat_accent};color:#fff;padding:2px 7px;font-size:10px;font-weight:700;letter-spacing:1px;">[{score}]</span>
        &nbsp;{date} · {source}
      </div>'''

def bullet_html(bullets, cat):
    rows = ""
    for b in bullets:
        b2 = fmt(b, cat)
        rows += f'<p class="bul" style="padding-left:20px;margin:0 0 8px;font-size:14.5px;line-height:1.9;color:#1A1A1A;">{b2}</p>\n      '
    return rows

def card_featured(art, cat, accent):
    """idx=0: 全幅画像カード"""
    bullets = bullet_html(art["bullets"], cat)
    return f'''<tr><td class="ng-card-pad" style="padding:24px 36px;background:#FAF7F0;border-bottom:1px solid #EDEAE3;">
    {meta_line(art["score"], art["date"], art["source"], accent)}
    <h3 class="ng-card-title" style="font-size:20px;font-weight:800;line-height:1.35;letter-spacing:-0.2px;margin:0 0 14px;color:#1A1A1A;">
      <a href="{art["url"]}" style="color:#1A1A1A;text-decoration:none;">{art["title"]}</a>
    </h3>
    <div class="ng-feature-img" style="margin:0 0 16px;">
      <img src="{art["thumb"]}" width="568" height="200" alt="" style="width:100%;height:200px;object-fit:cover;display:block;">
    </div>
    <div class="ng-card-body" style="font-size:14.5px;line-height:1.9;color:#1A1A1A;">
      {bullets}
    </div>
  </td></tr>'''

def card_side(art, cat, accent):
    """idx>0: サイドサムネカード"""
    bullets = bullet_html(art["bullets"], cat)
    return f'''<tr><td class="ng-card-pad" style="padding:20px 36px;background:#FAF7F0;border-bottom:1px solid #EDEAE3;">
    {meta_line(art["score"], art["date"], art["source"], accent)}
    <table class="ng-side-table" role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tbody><tr>
      <td class="ng-card-thumb" width="140" style="padding-right:16px;vertical-align:top;">
        <img class="ng-card-thumb-img" src="{art["thumb"]}" width="140" height="90" alt="" style="width:140px;height:90px;object-fit:cover;display:block;">
      </td>
      <td class="ng-card-body-cell" valign="top">
        <h3 class="ng-card-title" style="font-size:18px;font-weight:800;line-height:1.4;letter-spacing:-0.1px;margin:0 0 12px;color:#1A1A1A;">
          <a href="{art["url"]}" style="color:#1A1A1A;text-decoration:none;">{art["title"]}</a>
        </h3>
        <div class="ng-card-body" style="font-size:14px;line-height:1.9;color:#1A1A1A;">
          {bullets}
        </div>
      </td>
    </tr></tbody></table>
  </td></tr>'''


# ── TOC rows ──────────────────────────────────────────────────────────────────

def toc_rows():
    rows = ""
    for (cat, glyph, accent, ac_cls, jp, en, _, idx) in CATS:
        cnt = len(ARTICLES[cat])
        rows += f'''<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:6px;"><tbody><tr>
        <td width="32" class="m {ac_cls}" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:13px;color:{accent};font-weight:700;">{glyph}</td>
        <td style="font-size:14px;font-weight:700;">{jp} <span class="mut" style="color:#5C5A52;font-weight:400;">{en}</span></td>
        <td align="right" class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:11px;color:#5C5A52;">{cnt} stories</td>
      </tr></tbody></table>'''
    return rows


# ── カテゴリブロック ──────────────────────────────────────────────────────────

def cat_block(cat, glyph, accent, ac_cls, jp, en, en_upper, idx):
    arts = ARTICLES[cat]
    summary_map = {
        "fx": "ドル円は週末158.88円クローズ。日本の4月輸出が14.8%増・貿易黒字3,019億円と予想を大幅に上回り、円の中期的ファンダメンタルズが改善。来週は5/28 FOMC議事録・5/30 PCEコアが最大の焦点で、二方向リスクが対称的に存在する。",
        "ai": "GPT-5.5 InstantがChatGPTのデフォルトモデルに昇格し、Gemini 3.5 Flash GAが$1.50/1Mトークンという低価格エージェント向けインフラとして登場。Anthropicは企業AI市場で25%を確保しOpenAIからシェアを奪取しつつ、Claude Securityパブリックベータで脆弱性スキャン市場にも参入。",
        "it": "McKinsey・PwC・EYが幹部秘書職を相次ぎ削減し、AIが白カラー支援職を代替する「見えない自動化」が実態として浮かび上がった。NTT DATAはWinWire買収とグローバルデータセンター再編でAI受注基盤を拡充。Deloitte Tech Trends 2026は「アダプティブ企業」と「AI基盤化」を2026年の2大テーマと宣言する。",
        "game": "Switch 2は値上げ（¥59,980）を前にBic Camera等の全国店舗で購入制限と品薄が発生。ウマ娘が累計$2.5Bを達成し直近4年で最高売上記録日を更新。PlayStation State of Play（6/2）の開催が確定し、SIEが夏向けPS5ラインナップを世界に発信する準備に入った。",
    }
    summary = summary_map[cat]
    header = f'''<tr><td class="ng-cat-pad" style="background:{accent};padding:20px 36px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tbody><tr>
    <td style="vertical-align:middle;">
      <div class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:10px;color:rgba(255,255,255,0.7);letter-spacing:2px;margin-bottom:4px;">CATEGORY {idx} / 4 · {en_upper}</div>
      <div class="ng-cat-name" style="font-size:28px;font-weight:800;color:#fff;letter-spacing:-0.5px;line-height:1.1;">
        <span style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;margin-right:10px;">{glyph}</span>{jp}
      </div>
    </td>
    <td align="right" style="vertical-align:middle;color:rgba(255,255,255,0.85);font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:11px;">{len(arts)} stories</td>
  </tr></tbody></table>
  <div class="ng-cat-summary" style="font-size:13px;color:rgba(255,255,255,0.95);font-style:italic;line-height:1.6;margin-top:10px;padding-top:10px;border-top:1px solid rgba(255,255,255,0.25);">{summary}</div>
</td></tr>'''
    cards = ""
    for i, art in enumerate(arts):
        if i == 0:
            cards += card_featured(art, cat, accent)
        else:
            cards += card_side(art, cat, accent)
    return header + "\n" + cards


# ── テーマ考察セクション ──────────────────────────────────────────────────────

def sec_html(num, tag, tag_color, heading, body_html):
    border = "" if num < 7 else "border-bottom:none;"
    return f'''<tr><td class="ng-section-pad" style="background:#FAF7F0;padding:0 36px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="{border}border-bottom:1px dashed #E2DED4;"><tbody><tr>
    <td class="ng-section-num-cell" width="80" valign="top" style="padding:28px 16px 28px 0;">
      <div class="m ng-section-num" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:38px;font-weight:900;color:{tag_color};line-height:0.9;letter-spacing:-2px;">§{num:02d}</div>
      <div class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:9px;color:#fff;background:{tag_color};padding:2px 6px;display:inline-block;letter-spacing:1.5px;margin-top:8px;">{tag}</div>
    </td>
    <td class="ng-section-text-cell" valign="top" style="padding:28px 0 28px 20px;border-left:1px solid #E2DED4;">
      <h3 class="ng-section-heading" style="font-size:18px;font-weight:800;margin:0 0 14px;">{heading}</h3>
      <div class="ng-section-body" style="font-size:13.5px;line-height:2.0;">{body_html}</div>
    </td>
  </tr></tbody></table>
</td></tr>'''

SECTIONS = [
  (1, "OVERVIEW", "#1A1A1A", "総論 — 「AIの表と裏」が同時加速した土曜日",
   'AIが経済を豊かにするという「表」の物語と、雇用や製品価格を押し上げるという「裏」の物語が、今週の土曜日に同時に可視化された。<strong style="background:rgba(45,91,184,0.13);padding:0 3px;">GPT-5.5 Instant</strong>と<strong style="background:rgba(45,91,184,0.13);padding:0 3px;">Gemini 3.5 Flash</strong>の相次ぐ新製品はAI産業の活況を象徴するが、McKinsey・PwCの幹部秘書削減と任天堂Switch 2値上げ（メモリ高騰要因）は同じコインの裏面だ。<span style="border-bottom:2px solid #1A1A1A;padding-bottom:1px;">AIが生み出すものと奪うものが同一の週に同時に発生している</span>という事実が、2026年の経済構造を最も端的に表している。'),
  (2, "FX", "#B8860B", "為替 — 貿易黒字の復活と「介入警戒×PCE待ち」の二重拘束",
   '日本の4月貿易収支が<strong style="background:rgba(184,134,11,0.13);padding:0 3px;">3,019億円の黒字</strong>に転換し、輸出が14.8%増と驚きの数字を出した。半導体・自動車向け輸出の堅調さは円の中期的なファンダメンタルズ改善を示し、介入なき円高修正シナリオの現実味を高める。ただし来週はPCEとFOMC議事録という二大指標が待ち構え、<span style="border-bottom:2px solid #1A1A1A;padding-bottom:1px;">ドル円は両方向のリスクが対称的に拮抗する難しい週</span>となる。AUD/USDがG10でキャリー通貨の最有力候補に浮上した点も注目で、資金フローの分散が進んでいる。'),
  (3, "AI", "#2D5BB8", "AI — モデル戦争から「制御プレーン」争奪へ",
   '<strong style="background:rgba(45,91,184,0.13);padding:0 3px;">GPT-5.5 Instant</strong>・<strong style="background:rgba(45,91,184,0.13);padding:0 3px;">Gemini 3.5 Flash</strong>・<strong style="background:rgba(45,91,184,0.13);padding:0 3px;">Claude Security</strong>の三連弾が示すのは、AI競争の主戦場が「モデルの賢さ」から「エコシステムの深さ」へ移行しつつあるという現実だ。VentureBeatが指摘する「エージェント制御プレーン」は次の覇権として、AWS・Microsoft・Anthropicが水面下で争っている。Anthropicの企業AI25%確保は<span style="border-bottom:2px solid #1A1A1A;padding-bottom:1px;">倫理的差別化が実際に市場シェアに変換されている</span>ことを示す最初の証拠であり、モデル価格競争だけでは勝者が決まらない構造が固まりつつある。'),
  (4, "INDUSTRY", "#2E6B52", "IT — 「採用増とリストラ」が同時進行する業界の二極化",
   'McKinsey・PwC・EYが幹部秘書職を削減する一方、AccentureとDeloitteはAI実装専任部隊を拡充している。この対照は<strong style="background:rgba(46,107,82,0.13);padding:0 3px;">コンサル業界の二極化</strong>が単なる業績格差ではなく、ビジネスモデルの根本的な違いから来ていることを示す。NTT DATAのWinWire買収とグローバルデータセンター再編は、「Microsoft Azure × AI実装」という軸でのエコシステム整備として機能する。<span style="border-bottom:2px solid #1A1A1A;padding-bottom:1px;">Deloitte Tech Trends 2026が「アダプティブ企業」を競争優位の核心と呼ぶ</span>ならば、コンサル自身が最もアダプティブな変容を迫られているのは皮肉だ。'),
  (5, "ECONOMY", "#8E2A19", "経済 — 本日は経済ジャンル休載（土曜日は対象外）",
   '経済カテゴリは月〜金の平日のみ掲載。ただし関連指標として、日本の4月貿易収支黒字転換（§02 為替セクション）とSwitch 2のメモリ高騰要因（§06 ゲームセクション）は経済的なシグナルとして参照されたい。来週5/30のPCEコア価格指数と東京CPIが、日米双方のインフレ動向を巡る最重要データポイントとなる予定。'),
  (6, "GAMING", "#5E3D8C", "ゲーム — 「AI半導体の直撃」と「IPの強さ」が同日に現れた土曜日",
   'Switch 2値上げ前日の購入騒動と<strong style="background:rgba(94,61,140,0.13);padding:0 3px;">ウマ娘$2.5B</strong>マイルストーンの達成が同日に並ぶ5月24日は、ゲーム産業の「コスト圧力」と「IPの強靭性」が同時に示されたという意味で象徴的だ。AI半導体需要がメモリ価格を高騰させ、任天堂ハードウェアのコスト構造を直撃している一方、Cygamesのウマ娘はソフトウェア・IPという「シリコンに依存しない」収益モデルで国際展開の果実を刈り取っている。<span style="border-bottom:2px solid #1A1A1A;padding-bottom:1px;">PlayStation State of Play（6/2）はこの夏のゲーム業界の方向性を決める最大のイベント</span>となり、Nintendo vs SIEの夏商戦が本格化する。'),
  (7, "TOMORROW", "#C9B98A", "明日へ — 来週のポイントと5月最終週の焦点",
   '''<ul style="margin:0;padding-left:20px;line-height:2.0;">
      <li><strong>5/28（火）</strong>: FOMC議事録公表。Warsh議長の利下げスタンスが文字として確認される</li>
      <li><strong>5/30（木）</strong>: PCEコア価格指数（米）と東京CPI（都区部）が同日発表。この2指標でドル円の6月方向性が決まる</li>
      <li><strong>6/2（火）</strong>: PlayStation State of Play。PS5夏ラインナップの全容開示</li>
      <li><strong>6/3（水）</strong>: FF VII Rebirth（Switch 2版）発売。Switch 2とPS5の両プラットフォームに跨るRPG大作の成否が問われる</li>
      <li><strong>継続監視</strong>: Anthropicのエンタープライズ25%シェア維持とGPT-5.5 InstantのOpenAI利用拡大動向</li>
    </ul>'''),
]


# ── TAKEAWAYS ─────────────────────────────────────────────────────────────────

TAKEAWAYS = [
  ("#B8860B", "01", "FX",
   '日本4月貿易黒字3,019億円の復活が<span style="border-bottom:2px solid #B8860B;padding-bottom:1px;">介入に依存しない円高修正の芽</span>を生み出しており、PCE次第で来週の相場が急変しうる'),
  ("#2D5BB8", "02", "AI",
   'GPT-5.5 InstantとGemini 3.5 Flash GAのコスト競争はモデル価格を押し下げる一方、<strong style="background:rgba(45,91,184,0.13);padding:0 3px;">Anthropic25%シェア確保</strong>が「倫理差別化×高価格」モデルの持続可能性を証明しつつある'),
  ("#2E6B52", "03", "INDUSTRY",
   'McKinsey・PwCの幹部秘書削減とNTT DATA WinWire買収は<strong style="background:rgba(46,107,82,0.13);padding:0 3px;">コンサル業界の二極分化</strong>の両端を示す——同じAI圧力が採用拡大と削減という逆の経営判断を同時に生み出している'),
]

def takeaway_rows():
    rows = ""
    for (color, num, tag, text) in TAKEAWAYS:
        rows += f'''<tr><td style="padding-bottom:12px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#fff;border:1px solid #E2DED4;"><tbody><tr>
      <td width="56" valign="middle" class="m" style="background:{color};color:#fff;text-align:center;font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:18px;font-weight:900;padding:14px 0;">{num}</td>
      <td style="padding:12px 16px;">
        <div class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:9px;color:{color};font-weight:700;letter-spacing:1.5px;margin-bottom:4px;">{tag}</div>
        <div style="font-size:13px;line-height:1.7;font-weight:600;">{text}</div>
      </td>
    </tr></tbody></table>
  </td></tr>'''
    return rows


# ── RELATED ISSUES ───────────────────────────────────────────────────────────

RELATED = [
  ("2026-05-23", "https://hidepon-umg.github.io/News-Grasp/2026-05-23", "前日号 #20260523: SpaceX/OpenAI/AnthropicのIPOウェーブと幾何学証明の衝撃"),
  ("2026-05-25", "https://hidepon-umg.github.io/News-Grasp/2026-05-25", "翌日号 #20260525: Gemini Spark ベータ・日経63,339円・Warsh体制初週FRB"),
]

def related_rows():
    rows = ""
    for (dt, url, title) in RELATED:
        rows += f'''<tr><td style="padding:10px 0;border-bottom:1px solid #E2DED4;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tbody><tr>
      <td width="100" class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:11px;color:#5C5A52;">{dt}</td>
      <td style="font-size:13px;font-weight:600;"><a href="{url}" style="color:#1A1A1A;text-decoration:none;">{title}</a></td>
      <td width="20" align="right" style="color:#5C5A52;">→</td>
    </tr></tbody></table>
  </td></tr>'''
    return rows


# ── HTML 本体の構築 ───────────────────────────────────────────────────────────

def build_html():
    toc = toc_rows()
    cats_html = ""
    for args in CATS:
        cats_html += cat_block(*args)

    secs_html = ""
    for s in SECTIONS:
        secs_html += sec_html(*s)

    tw = takeaway_rows()
    rel = related_rows()

    lead = fmt(
        '今週の最大テーマは[[AI経済の両面性]]だ。GPT-5.5 InstantのデフォルトプロモーションとGemini 3.5 Flash GAが示す「安く速くエージェント対応」へのレース激化はAI産業の成熟を告げる一方、Bloombergが報じた[[McKinsey・PwC・EY]]の幹部秘書職削減は、同じAIが組織内部を再構成しつつある現実を突きつける。さらに任天堂Switch 2の値上げはAI半導体需要がゲーム産業のコスト構造を直撃していることを可視化した——__AIの「表」と「裏」が同時に進行する土曜日__だ。',
        "ai"
    )

    pull_quote = fmt('[[白カラーの侵食]]は静かに、そして加速しながら進む——コンサルのバックオフィスからゲーム機のシリコンまで、AIは見えない手でコストを書き換えている', "it")

    with open(r"C:\Users\hidek\Obsidian\New's Grasp\News-Grasp\prompts\email-template.html", encoding="utf-8") as f:
        tmpl = f.read()

    html = tmpl
    html = html.replace("{{ISSUE_NO}}", "20260524")
    html = html.replace("{{ISSUE_DATE}}", "2026-05-24")
    html = html.replace("{{ISSUE_WEEKDAY}}", "土")
    html = html.replace("{{ISSUE_WEB_URL}}", "https://hidepon-umg.github.io/News-Grasp/2026-05-24")
    html = html.replace("{{TOTAL_CATEGORIES}}", "4")
    html = html.replace("{{TOTAL_STORIES}}", "20")
    html = html.replace("{{TOTAL_SECTIONS}}", "7")
    html = html.replace("{{TOC_ROWS_HTML}}", toc)
    html = html.replace("{{CATEGORIES_HTML}}", cats_html)
    html = html.replace("{{REFLECTION_TITLE}}", "AIの表と裏")
    html = html.replace("{{REFLECTION_SUBTITLE}}", "モデル競争と白カラー侵食が同時加速する週末")
    html = html.replace("{{REFLECTION_LEAD_HTML}}", lead)
    html = html.replace("{{REFLECTION_PULL_QUOTE_HTML}}", pull_quote)
    html = html.replace("{{REFLECTION_SECTIONS_HTML}}", secs_html)
    html = html.replace("{{TAKEAWAYS_HTML}}", tw)
    html = html.replace("{{RELATED_ISSUES_HTML}}", rel)

    return html


if __name__ == "__main__":
    html = build_html()
    OUT.write_text(html, encoding="utf-8")
    size_kb = len(html.encode("utf-8")) / 1024
    print(f"✅ email.html 生成完了 — {size_kb:.1f} KB → {OUT}")
    if size_kb > 100:
        print(f"⚠️  Gmail クリッピング閾値(~102KB)に近い。要確認。")
