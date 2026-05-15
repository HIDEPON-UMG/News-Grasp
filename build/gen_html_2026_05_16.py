# coding: utf-8
"""
2026-05-16 HTML メール生成
prompts/email-template.html のプレースホルダを埋めて build/email.html を出力する
金曜日: FX / AI / IT-Consulting / Economy (4カテゴリ)
"""
import re, os

BASE = r"C:\Users\hidek\Obsidian\New's Grasp\News-Grasp"
CDN  = "https://raw.githubusercontent.com/HIDEPON-UMG/news-grasp-assets/main"

ISSUE_DATE = "2026-05-16"
ISSUE_NO   = "20260516"
WEEKDAY    = "金"

CATEGORIES = [
  {
    "id": "fx", "name": "FX", "nameEn": "Foreign Exchange",
    "accent": "#B8860B", "glyph": "¥", "index": 1,
    "summary": "ウォーシュFRB議長が就任初日に「独立した行動者」と表明した一方、CPI+3.8%・PPI+6.0%確認後のインフレ圧力が「利上げ観測」をドルに乗せ、ドル円は158.6円と4連騰。160円介入ラインまで約1.4円に迫り攻防が続く。",
    "items": [
      {
        "score": 97, "time": "07:15", "source": "FXStreet",
        "title": "ドル円158.6円 — ウォーシュ就任初日・インフレ加速で4連騰、160円ライン緊迫",
        "url": "https://www.fxstreet.com/currencies/usdjpy",
        "thumb": f"{CDN}/ng-thumb-fx.jpg",
        "bullets": [
          "[[ドル円]]は158.6円まで上昇し4営業日連続の円安。ウォーシュFRB議長が「独立した行動者」と宣言しつつも、CPI+3.8%を前に__即時利下げを否定する文脈でドル買いが勢いを増した__。",
          "介入警戒ライン160円まで約1.4円の距離に縮まり、財務省は「あらゆる手段を排除しない」と再度牽制。ヘッジファンドは160円プット・オプションを積み増す動き。",
        ]
      },
      {
        "score": 90, "time": "06:30", "source": "MTFX Group",
        "title": "米ドル5月見通し — Warsh就任後はUSD/JPY 156〜162の「均衡なき均衡」レンジ相場入りか",
        "url": "https://www.mtfxgroup.com/fx-monthly-us/",
        "thumb": "https://content.mtfxgroup.com/uploads/OG_Image2_349da0dbd1.jpg",
        "bullets": [
          "MTFXがドル5月見通しを公開。[[Warsh就任後]]のFOMC（6月17日）まで__ドルは高止まりだが方向感を欠くレンジ相場__と予測。CPI・PPI・原油の三変数が方向を決める。",
          "USDJPY月間レンジ予想は156.0〜162.0円。160円突破時には日本当局の直接介入が現実的とし、「攻防ライン」として最重要の節目と分析。",
        ]
      },
      {
        "score": 85, "time": "08:00", "source": "野村総合研究所",
        "title": "NRI木内登英「介入効果は15日で半減」— 円安の構造要因、根治は日銀利上げのみ",
        "url": "https://www.nri.com/jp/media/column/kiuchi/20260507.html",
        "thumb": "https://www.nri.com/jp/media/column/kiuchi/files/000061005.png",
        "bullets": [
          "NRI[[木内登英]]氏が連休中に戻った円安の構造的要因を3点分析：(1)日米金利差継続、(2)経常赤字のデジタル収支悪化、(3)国内投資家の海外逃避。__介入効果は平均15日で半減__するとデータで実証。",
          "日米金利差が3.0%超を維持する限り、円売りは構造的需給に起因し口先・実弾介入では根本解決にならない。日銀利上げが唯一の「根治療法」と断言。",
        ]
      },
      {
        "score": 78, "time": "09:00", "source": "CBS News",
        "title": "ウォーシュ議長「独立した行動者」宣言 — CBS分析が示す4つの利下げシナリオ",
        "url": "https://www.cbsnews.com/news/kevin-warsh-federal-reserve-chair-interest-rates/",
        "thumb": "https://assets3.cbsnewsstatic.com/hub/i/r/2026/05/12/7ebfc5ff-9608-4c40-85b9-0f2028b18c39/thumbnail/1200x630g2/dfa29d7b732dcd9b24489b22fd45dc5c/ap26111849758674.jpg",
        "bullets": [
          "[[ウォーシュ]]議長はトランプ系議員からの利下げ圧力に対して「自らの判断で動く」と明言。過去の任期中はタカ派路線で知られ、__インフレ再加速下では「引き締め」を選好する公算大__とCBSが分析。",
          "CBSが提示する4シナリオ：①データ次第で6月利下げ、②年内据置、③25bp利上げ、④50bp以上の利上げ。現在の市場確率は③が最も上昇中で18%。",
        ]
      },
      {
        "score": 72, "time": "06:00", "source": "Trading Economics",
        "title": "円 対ドル週間マイナス — ベッセント訪日効果が剥落、円安再加速の構図",
        "url": "https://jp.tradingeconomics.com/japan/currency",
        "thumb": f"{CDN}/ng-thumb-common-fx.jpg",
        "bullets": [
          "円は対ドルで今週約1%下落し、4月30日以来の介入効果がほぼ剥落。[[ベッセント]]米財務長官の訪日で得た「協調介入への期待感」は、インフレデータと新FRB議長就任で上書きされた。",
          "Trading Economicsのコンセンサスでは、日本円の対ドル3ヵ月予想が155.0円（強気）から158.5円（弱気）へ修正。__日銀が6月に動かなければ160円到達の時間軸が前倒し__される。",
        ]
      },
    ]
  },
  {
    "id": "ai", "name": "AI", "nameEn": "Artificial Intelligence",
    "accent": "#2D5BB8", "glyph": "◆", "index": 2,
    "summary": "AnthropicがClaude Platform on AWS GAとMCP Connectorを一斉リリースし、エンタープライズ浸透が次フェーズへ。Google I/O開幕まで3日で期待値が沸騰。OpenAIはDeployCoを創設してAIコンサル市場に直接参入、プラットフォーム戦争がサービス領域に拡大した。",
    "items": [
      {
        "score": 95, "time": "07:00", "source": "InfoQ",
        "title": "Anthropic、Claude Platform on AWS GA + MCP Connector + Managed Agents を一斉リリース",
        "url": "https://www.infoq.com/news/2026/05/anthropic-claude-aws/",
        "thumb": "https://res.infoq.com/news/2026/05/anthropic-claude-aws/en/headerimage/generatedHeaderImage-1778682420283.jpg",
        "bullets": [
          "[[Claude Platform on AWS]]が一般提供（GA）開始。AWSの認証・課金・監視をそのまま使いながらAnthropicのネイティブAPIに直接アクセス可能。Managed Agents（β）とMCP Connector（β）を同時リリース。",
          "MCP Connectorにより__クライアントコードを書かずに任意のリモートMCPサーバーへ接続__が可能になった。Thomson Reuters CoCounsel、Dun & Bradstreet KYCなど主要SaaS連携が即日ライブ。",
        ]
      },
      {
        "score": 90, "time": "08:15", "source": "AI Business",
        "title": "OpenAI、DeployCo創設（$40億）+ Tomoro買収 — AIコンサル市場への直接参入が加速",
        "url": "https://aibusiness.com/generative-ai/openai-launches-ai-consulting-company-anthropic",
        "thumb": "https://eu-images.contentstack.com/v3/assets/blt6b0f74e5591baa03/blt3e21d962ce3ce6a6/6a0246ed751e95096d8c06bb/OpenAI_logo.jpg?disable=upscale&width=1200&height=630&fit=crop",
        "bullets": [
          "OpenAIが[[DeployCo]]を初期投資$40億で創設。応用AIコンサルのTomoroを同時買収し、企業向けAIシステム構築・展開を一貫支援する独立子会社として立ち上げた。",
          "背景にあるのは「生成AIパイロット95%がROI未達」という現実。Frontier Alliance（McKinsey/BCG/Accenture）を通じた間接展開ではスピードが不足と判断し、__内製コンサル部隊を持つ戦略に転換__。",
        ]
      },
      {
        "score": 85, "time": "09:30", "source": "Engadget",
        "title": "Google I/O 2026 まで3日 — Android Show先行発表が解禁、Gemini 4・Android 17の全貌が明らかに",
        "url": "https://www.engadget.com/2171038/everything-announced-at-android-show-google-io-2026/",
        "thumb": "https://www.engadget.com/img/gallery/everything-announced-during-the-android-show-io-2026-edition/l-intro-1778605292.jpg",
        "bullets": [
          "Google I/O 2026の前哨戦「[[Android Show | I/O Edition]]」ですでに複数の発表が解禁。Android 17の新機能・Gemini 4の能力強化・プロアクティブAIアシスタント「Remy」が主役。",
          "Gemini 4は__Responsive AIからProactive AIへの転換__が最大の革新。ユーザーがタスクを依頼するのではなく、AIが先回りしてワークフローを代替するエージェントへと進化。",
        ]
      },
      {
        "score": 80, "time": "06:45", "source": "Ramp AI Index",
        "title": "Claude Code が全GitHubコミットの4% — 1ヶ月で倍増、エンタープライズ採用加速を示すシグナル",
        "url": "https://ramp.com/leading-indicators/ai-index-may-2026",
        "thumb": "https://cdn.sanity.io/images/6jz6vxxd/production/9088989de14173fd6c74125f5eb139b26d8c8bf0-2400x1260.png?rect=0,3,2400,1254&w=1200&h=627&fit=crop",
        "bullets": [
          "Ramp AI Indexによれば、[[Claude Code]]が全GitHub公開コミットに占める割合が1ヶ月で2%→4%と倍増。AnthropicのビジネスAI採用率（34.4%）がOpenAI（32.3%）を初めて上回った要因の一つ。",
          "$100万超の大型契約企業が2ヶ月で倍増し、ARRは$300億を突破。__エンタープライズARR成長率180%という数字は、GPT-5.5ファミリーの製品展開が加速するOpenAIに対して優位を保っている__。",
        ]
      },
      {
        "score": 75, "time": "10:00", "source": "Thomson Reuters",
        "title": "Thomson Reuters × Anthropic — CoCounsel LegalにClaude MCP統合、法務AIが実用域へ",
        "url": "https://www.thomsonreuters.com/en/press-releases/2026/may/thomson-reuters-and-anthropic-expand-partnership-to-connect-claude-with-cocounsel-legal",
        "thumb": "https://www.thomsonreuters.com/content/dam/ewp-m/images/thomsonreuters/en/photography/reuters/rtr1zkvu-luke-macgregor-tr.jpg.transform/rect-768/q90/image.jpg",
        "bullets": [
          "Thomson ReutersがAnthropicとのパートナーシップを拡大し、[[CoCounsel Legal]]にClaude MCPを統合。法律調査・契約審査・デューデリジェンスをClaudeが直接支援する形に。",
          "MCP統合により、CoCounselは単なる検索支援から__法的判断の一次草稿まで自律的に作成するアシスタント__へと進化。法律事務所・企業法務での本格導入が現実味を帯びた。",
        ]
      },
    ]
  },
  {
    "id": "it", "name": "IT-Consulting", "nameEn": "IT & Consulting",
    "accent": "#2E6B52", "glyph": "▲", "index": 3,
    "summary": "McKinseyがCEO自ら「AIエージェント2.5万体が実稼働中」と公言し、成果連動・株式報酬型への経営転換を宣言。KPMGは低付加価値業務400人を先行削減しAI再編を加速。富士通はデジタルリハーサルをグローバル展開し、コンサル×SIer境界の溶解が1段深まった。",
    "items": [
      {
        "score": 95, "time": "07:00", "source": "Market Realist",
        "title": "McKinsey、AIエージェント2.5万体が実稼働 — CEO「40,000人+25,000体」で成果連動型コンサルへ転換",
        "url": "https://marketrealist.com/how-is-mc-kinsey-changing-its-workforce/",
        "thumb": "https://media.marketrealist.com/brand-img/rcXGimV79/1200x628/pn/787155/uploads/469022b0-f04b-11f0-9829-b7b52e025ebf_1200_630.jpeg",
        "bullets": [
          "McKinsey CEO Bob Sternfelsが[[AIエージェント2.5万体]]の実稼働を公式に認めた。40,000人の人間コンサルタントと並走する形で、調査・データ分析・文書作成を担う「バーチャル人材」として機能。",
          "直近1.5百万時間の業務をAIが代替した実績を踏まえ、__パートナー報酬の一部を株式へシフト__する方針を発表。AI×成果連動で収益が不安定化する前提に立ち、固定費を圧縮する資本構造の転換が始まった。",
        ]
      },
      {
        "score": 88, "time": "08:00", "source": "TheStreet",
        "title": "KPMG 米アドバイザリー400人削減 — AI再編で低付加価値業務を先行カット、Big4の構造改革加速",
        "url": "https://www.thestreet.com/employment/big-four-firm-kpmg-cuts-100s-of-jobs-as-consulting-demand-slows",
        "thumb": f"{CDN}/ng-thumb-common-it.jpg",
        "bullets": [
          "[[KPMG]]が米アドバイザリー部門の約4%にあたる400名を削減。規制リスクアドバイザリー・カスタマーオペレーション・金融サービスに集中した実施で、パートナー層への影響はなし。",
          "削減理由は「需要の戦略的再調整」と説明しつつも、AIが合成・分析・調査を担う現在、__ジュニア・ミドルレベルの作業の市場価値が急速に収縮している__ことが背景にある。",
        ]
      },
      {
        "score": 85, "time": "09:00", "source": "Fujitsu Global",
        "title": "富士通「デジタルリハーサル」グローバル展開 — AI×デジタルツインでサプライチェーン輸送コスト30%削減実証",
        "url": "https://global.fujitsu/en-global/insight/tl-scm-digital-rehearsal-202600302",
        "thumb": "https://global.fujitsu/-/media/Project/Fujitsu/Fujitsu-HQ/insight/tl-scm-digital-rehearsal-202600302/ogp-scm-digital-rehearsal-1200x630.jpg?rev=ba1097759e624e8d9f393d536834e1bd",
        "bullets": [
          "富士通が「[[デジタルリハーサル]]」をグローバル展開。デジタルツイン環境でAIが実世界のサプライチェーンをミラーリングし、地政学リスク・気候変動・需要急変への対応策を事前シミュレーション。",
          "医薬品SCM（ロート製薬×東工大との共同実証）で輸送コスト30%削減を検証済み。強化学習エンジンが__数千シナリオを数時間で評価__し、人間では不可能な大域最適を実現。",
        ]
      },
      {
        "score": 80, "time": "08:30", "source": "Strat-Bridge",
        "title": "コンサルのパートナーシップモデルが変わる — AI×成果連動でパートナー報酬を株式化、業界経営が転換期",
        "url": "https://www.strat-bridge.com/insights/consultings-partnership-model-is-shifting-with-ai/",
        "thumb": "https://www.strat-bridge.com/wp-content/uploads/2026/05/Insight-Blog-feature-pic-53.png",
        "bullets": [
          "Strat-Bridgeが分析するコンサル業界の経営転換：AI×成果連動型プライシングにより、収益の予測可能性が下がるため、[[McKinsey・BCG]]を含む上位5社がパートナー報酬の固定部分を圧縮し始めた。",
          "従来の年収ベースから__エクイティ比率を高めた成果報酬型__へのシフトは、ベンチャーキャピタルのインセンティブ設計に近い。コンサル会社がVC的な投資リスクを負うことを意味する。",
        ]
      },
      {
        "score": 75, "time": "06:00", "source": "The Business Research Company",
        "title": "ITコンサル市場 2026年$3,750億規模 — AI需要が成長エンジン、アジア太平洋が最速成長地域",
        "url": "https://www.thebusinessresearchcompany.com/report/it-consulting-global-market-report",
        "thumb": "https://www.thebusinessresearchcompany.com/images/tbrc-logo-og.png",
        "bullets": [
          "The Business Research Companyが2026年のITコンサル市場規模を$3,750億と発表。2025年比約5%成長で、成長ドライバーは[[生成AIとクラウド移行]]の複合需要。",
          "地域別ではアジア太平洋（APAC）が最速成長。日本・インド・韓国での大型DX案件とAI投資義務化規制が牽引。__欧米の成長率鈍化を新興市場が補完する構図__が2027年以降も続く見込み。",
        ]
      },
    ]
  },
  {
    "id": "economy", "name": "経済", "nameEn": "Economy",
    "accent": "#8E2A19", "glyph": "■", "index": 4,
    "summary": "S&P500が史上初めて7,500ポイントを超えて引けた一方、ウォーシュFRB議長の「データ次第・独立路線」発言を受け「利上げ観測」が市場語彙に復活した。日経平均は63,700円台で高値圏維持。CPI+3.8%がバリュエーションへの圧力として意識され始め、転換点となる週を迎えた。",
    "items": [
      {
        "score": 97, "time": "07:00", "source": "Charles Schwab",
        "title": "S&P500、史上初の7,500超え — ウォーシュ就任初日に利上げ観測急浮上、転換点となる週",
        "url": "https://www.schwab.com/learn/story/stock-market-update-open",
        "thumb": "https://www.schwab.com/sites/g/files/eyrktu1071/files/41301781_2x1_0.jpg",
        "bullets": [
          "S&P500が[[史上初めて7,500ポイント]]を超えて引けた。しかし同時に、CPI+3.8%と新FRB議長就任が重なり「利上げが利下げより先に来る」という観測がSchwab・金利ストラテジストから出始めた。",
          "Schwab Charles Martin氏「__年内利下げはほぼ消滅した。議論の焦点は「何月に利上げするか」に移りつつある__」と発言。3ヵ月債利回りが年内+0.25bp利上げを44%の確率で織り込んでいる。",
        ]
      },
      {
        "score": 92, "time": "08:00", "source": "Trading Economics",
        "title": "日経平均63,700円台 — 企業業績好調・AI半導体牽引で高値更新基調、ウォーシュ演説が外部変数",
        "url": "https://tradingeconomics.com/japan/stock-market",
        "thumb": f"{CDN}/ng-thumb-common-economy.jpg",
        "bullets": [
          "日経平均は63,700円台で推移し、強い企業業績（FY2027ガイダンス好調）とAI半導体関連株の牽引が高値更新基調を維持。[[TOPIXとの乖離]]（NT倍率16.4倍）が拡大しており、指数効果が株高を増幅。",
          "米ウォーシュ議長の演説と今週のFX動向が外部変数として最大の不確実性。ドル高進行→外資の円ヘッジ見直し→短期売り圧力という経路が今週末に意識される。",
        ]
      },
      {
        "score": 87, "time": "09:00", "source": "CNBC",
        "title": "ウォーシュ「独立した行動者」就任宣言 — インフレが制約、data drivenで市場に緊張感",
        "url": "https://www.cnbc.com/2026/05/04/fed-kevin-warsh-interest-rates.html",
        "thumb": "https://image.cnbcfm.com/api/v1/image/108294688-1776783714597-gettyimages-2271829405-WARSH_CONFIRMATION.jpeg?v=1777488458&w=1920&h=1080",
        "bullets": [
          "CNBCが[[ウォーシュ]]議長の初期スタンスを解析。「トランプ政権の要求には屈しない」と明言しつつも、過去の任期中はタカ派路線が定評。__インフレ第二波の現環境では最初の行動が利下げになりにくい__。",
          "市場が懸念するのは「Warshが独立しすぎてサプライズ利上げに踏み切るシナリオ」。利上げ確率は3週前の5%から現在18%まで上昇し、リスクプレミアムが株式バリュエーションに乗り始めた。",
        ]
      },
      {
        "score": 83, "time": "08:30", "source": "Economy Middle East",
        "title": "インフレ第二波×Warsh就任 — S&P500のバリュエーション調整か、年末8,000への分水嶺",
        "url": "https://economymiddleeast.com/news/stock-markets-today-sp-500-nikkei-and-euro-stoxx-plunge-as-warsh-fed-pick-metals-cash-rattle-investors/",
        "thumb": "https://economymiddleeast.com/wp-content/uploads/2025/10/stock-markets.jpg",
        "bullets": [
          "Economy Middle EastがS&P500と日経・ユーロ・金属市場の連動を分析。[[CPI+3.8%×Warsh就任]]の組み合わせが「安全資産への逃避」を誘発し、金・国債が短期急騰した局面もあった。",
          "2シナリオの分水嶺：①Warshが積極前倒し利下げなら年末S&P500は8,000超え、②インフレ再燃で据置・利上げなら6,500割れ。__現在の市場はシナリオ①に7割を、②に3割を配分__している。",
        ]
      },
      {
        "score": 78, "time": "07:30", "source": "野村証券",
        "title": "野村証券 S&P500年末予想を7,500→7,700に上方修正 — 米中会談収束シナリオがカタリスト",
        "url": "https://www.nomura.co.jp/wealthstyle/article/0714/",
        "thumb": "https://www.nomura.co.jp/wealthstyle/article/0714/images/og_a_0714_01.png",
        "bullets": [
          "野村証券が[[S&P500年末予想]]を7,500から7,700へ上方修正。米中対話の再開・AI需要拡大・イラン情勢の漸進的収束の3条件が揃いつつあることを根拠に強気見解を維持。",
          "ダウンサイドリスクは「インフレ再燃によるFRBの政策転換」と「中東情勢の急変」。Warsh議長が6月FOMCでハト派シグナルを出せれば__年末7,700は射程内__と試算。",
        ]
      },
    ]
  },
]

REFLECTION = {
  "title": "金利の天井とAIの底入れ",
  "subtitle": "ウォーシュ就任初日と史上初7,500超え — 利上げ観測とAI覇権争いが同時進行する「高温の金曜日」",
  "lead": "本日4分野・20本のニュースから浮かび上がる最大のテーマは [[ウォーシュ体制下の金利不確実性]] と [[AIのエンタープライズ浸透加速]] の同時進行である。一見逆方向に動く二つの力が、今週の市場を同じ「高温」状態に置いている。",
  "pull_quote": "「単一のモデル性能」から「__エコシステムでの占有率__」へ── AIプラットフォーム競争は生産性指標を戦場にしてきた。",
  "sections": [
    {
      "tag": "総論", "accent": "#1A1A1A",
      "heading": "転換点か、継続か",
      "body": "本日最大の構造的問いは「インフレ第二波（CPI+3.8%）とAI成長（Claude Code 全GitHubコミット4%）が[[共存できるか]]」である。歴史的に高インフレは株式のバリュエーション圧縮要因だが、AIの生産性向上が企業収益の底上げとして先行している。S&P500の7,500超えは__「AI期待値の前払い」が「金利コスト」を上回っている状態__の可視化である。この均衡がいつ崩れるかが今夏の最大の問い。"
    },
    {
      "tag": "為替・経済", "accent": "#B8860B",
      "heading": "ウォーシュ就任が開けたパンドラの箱",
      "body": "ドル円は[[158.6円]]と4連騰し、160円介入ラインに迫った。新FRB議長ウォーシュが「独立した行動者」と宣言したことで市場が読みにくくなり、3ヵ月債が年内利上げを18%織り込み始めた。__介入効果は平均15日で半減__（NRI木内登英）というデータが示す通り、財務省の口先牽制の限界が再び可視化されつつある。6月日銀会合（利上げ確率50%超）との連携が円安の唯一の構造的解決策として浮かび上がった。"
    },
    {
      "tag": "AI・技術", "accent": "#2D5BB8",
      "heading": "エンタープライズの「摩擦ゼロ」競争",
      "body": "Anthropicが[[Claude on AWS GA]]・MCP Connector・Managed Agentsを一斉リリースし、企業内展開の摩擦を大幅に低減した。同時にClaude Codeが全GitHubコミットの4%に達し1ヵ月で倍増、デファクト化への坂を駆け上がっている。OpenAIもDeployCo（$40億）とTomoro買収で「ベンダー直接コンサル」へと進出し、AI企業がITサービス産業の全バリューチェーンを取り込む動きが加速している。Google I/O（5/19）の[[Gemini 4]]発表で、週明けに競争がさらに激化する。"
    },
    {
      "tag": "産業・業界", "accent": "#2E6B52",
      "heading": "コンサル業界の「25,000体の衝撃」",
      "body": "McKinseyのCEOが「25,000体のAIエージェントが実稼働」と公言し、パートナー報酬の株式化を発表。[[Big4もKPMG]]が400人を削減し、低付加価値業務の終焉が現実になった。富士通のデジタルリハーサル（輸送コスト30%削減実証）に代表されるように、SIerがコンサル領域に技術で侵入している。AI企業（OpenAI・Anthropic）が直接コンサルを展開する動きと合わせ、__コンサル業界はクライアント・AI企業・SIerの三方向から挟まれる構造に入った__。"
    },
    {
      "tag": "明日へ", "accent": "#C9B98A",
      "heading": "今夏の4つのシナリオ軸",
      "body": "今夏の市場を決める4軸：①[[6月日銀利上げ（可否）]] — 円安構造の唯一の解決策。植田総裁が「物価目標達成は視野」と発言済み。②[[6月17日FOMC（Warsh初会合）]] — 利下げ・据置・利上げ、どのシグナルを出すかが2026年下半期の金融市場を決定。③Google I/O（5/19）[[Gemini 4]] — 3日後の発表でAI競争の次の覇権軸が決まる。④S&P500 PER調整 — 7,500のバリュエーション（PER 24.1倍）が高インフレ継続で正当化できるか。__これら4つのうち1つでも予想外の方向に振れれば、複合的な市場変動が起きる__。"
    },
  ],
  "takeaways": [
    {"tag": "為替", "color": "#B8860B", "text": "ドル円158.6円と4連騰。[[160円ラインまで1.4円]]。ウォーシュ就任とCPI+3.8%が重なりドル高圧力が増大。NRI木内「介入効果は15日で半減、根治は日銀利上げのみ」"},
    {"tag": "AI",   "color": "#2D5BB8", "text": "AnthropicがClaude on AWS GA・MCP・Managed Agentsを一斉リリース。[[Claude Code]]が全GitHubコミットの4%（1ヵ月で倍増）。OpenAIはDeployCo $40億でコンサル直接参入"},
    {"tag": "産業", "color": "#2E6B52", "text": "McKinsey「[[AI 2.5万体実稼働]]、パートナー報酬を株式化」。KPMG 400人削減。富士通SCMデジタルリハーサルグローバル展開。__コンサル×SIer×AI三つ巴の構造転換が加速__"},
  ],
  "related": [
    {"date": "2026-05-15", "title": "ウォーシュ就任シナリオ・S&P500 7,250警戒・Anthropic逆転OpenAI（34.4% vs 32.3%）"},
    {"date": "2026-05-14", "title": "ウォーシュ議会承認・日経平均62,742円・FRB議長交代市場影響"},
    {"date": "2026-05-11", "title": "ベッセント訪日・CPI発表前・Wall Street AI半導体多極化"},
  ]
}


TOTAL_CATEGORIES = len(CATEGORIES)
TOTAL_STORIES    = sum(len(c["items"]) for c in CATEGORIES)
TOTAL_SECTIONS   = len(REFLECTION["sections"])


def h(text):
    text = re.sub(
        r'\[\[(.+?)\]\]',
        r'<strong style="background:#C9B98A;color:#1A1A1A;padding:0 3px;">\1</strong>',
        text
    )
    text = re.sub(
        r'__(.+?)__',
        r'<span style="border-bottom:2px solid currentColor;">\1</span>',
        text
    )
    return text

def ng_thumb(cat_id, kind="common"):
    if kind == "featured":
        return f"{CDN}/ng-thumb-{cat_id}.jpg"
    return f"{CDN}/ng-thumb-{kind}-{cat_id}.jpg"


def build_toc_rows():
    rows = ""
    for cat in CATEGORIES:
        rows += f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:6px;"><tbody><tr>
  <td width="32" class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:12px;color:{cat['accent']};font-weight:700;">{cat['glyph']}</td>
  <td style="font-size:14px;font-weight:700;">{cat['name']} <span style="color:#5C5A52;font-size:11px;font-family:'JetBrains Mono',Consolas,'Courier New',monospace;">{cat['nameEn']}</span></td>
  <td align="right" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:11px;color:#5C5A52;">{len(cat['items'])} stories</td>
</tr></tbody></table>"""
    return rows


def build_featured_card(item, cat):
    thumb = item["thumb"] if item["thumb"] else ng_thumb(cat["id"], "featured")
    bullets_html = "".join(
        f'<div class="bul ng-card-body" style="color:{cat["accent"]}"><span style="color:#1A1A1A;">{h(b)}</span></div>'
        for b in item["bullets"][:2]
    )
    return f'<tr><td style="background:#FAF7F0;padding:24px 36px;border-bottom:1px solid #EDEAE3;"><div style="margin-bottom:6px;"><span style="background:{cat["accent"]};color:#fff;padding:2px 7px;font-size:11px;font-family:\'JetBrains Mono\',Consolas,monospace;font-weight:700;">★ TOP</span><span style="padding-left:8px;font-family:\'JetBrains Mono\',Consolas,monospace;font-size:11px;color:#5C5A52;">{item["time"]} · {item["source"]} · {item["score"]}</span></div><h3 class="ng-card-title" style="font-size:20px;font-weight:800;margin:8px 0 12px;line-height:1.35;"><a href="{item["url"]}" style="color:#1A1A1A;text-decoration:none;">{item["title"]}</a></h3><div style="margin-bottom:14px;"><a href="{item["url"]}" style="display:block;text-decoration:none;"><img src="{thumb}" width="568" height="200" alt="" style="display:block;width:100%;height:200px;object-fit:cover;border:1px solid #E2DED4;"></a></div>{bullets_html}</td></tr>'


def build_side_card(item, cat, idx):
    thumb = item["thumb"] if item["thumb"] else ng_thumb(cat["id"], "common")
    bullets_html = "".join(
        f'<div class="bul ng-card-body" style="color:{cat["accent"]}"><span style="color:#1A1A1A;">{h(b)}</span></div>'
        for b in item["bullets"][:2]
    )
    return f'<tr><td style="background:#FAF7F0;padding:18px 36px;border-bottom:1px solid #EDEAE3;"><div style="margin-bottom:5px;"><span style="background:{cat["accent"]};color:#fff;padding:1px 5px;font-size:11px;font-family:\'JetBrains Mono\',Consolas,monospace;font-weight:700;">{idx:02d}</span><span style="padding-left:7px;font-family:\'JetBrains Mono\',Consolas,monospace;font-size:11px;color:#5C5A52;">{item["time"]} · {item["source"]} · {item["score"]}</span></div><h3 class="ng-card-title" style="font-size:17px;font-weight:800;margin:6px 0 10px;line-height:1.4;"><a href="{item["url"]}" style="color:#1A1A1A;text-decoration:none;">{item["title"]}</a></h3><table width="100%" class="ng-side-table" role="presentation" cellpadding="0" cellspacing="0" border="0"><tbody><tr><td class="ng-card-thumb" width="152" style="width:152px;vertical-align:top;padding-right:14px;"><a href="{item["url"]}" style="display:block;text-decoration:none;"><img src="{thumb}" width="140" height="88" alt="" class="ng-card-thumb-img" style="display:block;width:140px;height:88px;object-fit:cover;border:1px solid #E2DED4;"></a></td><td class="ng-card-body-cell" style="vertical-align:top;">{bullets_html}</td></tr></tbody></table></td></tr>'


def build_category_block(cat):
    cards = build_featured_card(cat["items"][0], cat)
    for i, item in enumerate(cat["items"][1:], 2):
        cards += build_side_card(item, cat, i)
    return f"""<tr><td class="ng-cat-pad" style="background:{cat['accent']};padding:22px 36px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tbody><tr>
    <td style="vertical-align:middle;">
      <div style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:10px;color:rgba(255,255,255,0.7);letter-spacing:2px;margin-bottom:4px;">CATEGORY {cat['index']} / {TOTAL_CATEGORIES} · {cat['nameEn'].upper()}</div>
      <div style="font-size:28px;font-weight:800;color:#fff;letter-spacing:-0.5px;line-height:1.1;">
        <span style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;margin-right:10px;">{cat['glyph']}</span>{cat['name']}
      </div>
    </td>
    <td align="right" style="vertical-align:middle;color:rgba(255,255,255,0.85);font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:11px;">{len(cat['items'])} stories</td>
  </tr></tbody></table>
  <div class="ng-cat-summary" style="font-size:13px;color:rgba(255,255,255,0.95);font-style:italic;line-height:1.6;margin-top:10px;padding-top:10px;border-top:1px solid rgba(255,255,255,0.25);">{cat['summary']}</div>
</td></tr>
{cards}"""


def build_sections():
    sec_accents = ["#1A1A1A","#B8860B","#2D5BB8","#2E6B52","#C9B98A"]
    html = ""
    for i, s in enumerate(REFLECTION["sections"], 1):
        acc = sec_accents[min(i-1, len(sec_accents)-1)]
        html += f"""<tr><td class="ng-section-pad" style="background:#FAF7F0;padding:0 36px;">
  <table width="100%" style="border-bottom:1px dashed #E2DED4;border-collapse:collapse;"><tbody><tr>
    <td class="ng-section-num-cell" width="80" valign="top" style="padding:28px 16px 28px 0;vertical-align:top;">
      <div class="m ng-section-num" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:38px;font-weight:900;color:{acc};line-height:0.9;letter-spacing:-2px;">§{i:02d}</div>
      <div style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:9px;color:#fff;background:{acc};padding:2px 6px;display:inline-block;letter-spacing:1.5px;margin-top:8px;">{s['tag']}</div>
    </td>
    <td class="ng-section-text-cell" valign="top" style="padding:28px 0 28px 20px;border-left:1px solid #E2DED4;vertical-align:top;">
      <h3 class="ng-section-heading" style="font-size:18px;font-weight:800;margin:0 0 14px;">{s['heading']}</h3>
      <div class="ng-section-body" style="font-size:13.5px;line-height:2.0;">{h(s['body'])}</div>
    </td>
  </tr></tbody></table>
</td></tr>"""
    return html


def build_takeaways():
    html = ""
    for i, t in enumerate(REFLECTION["takeaways"], 1):
        html += f"""<tr><td style="padding-bottom:12px;">
  <table width="100%" style="background:#fff;border:1px solid #E2DED4;border-collapse:collapse;"><tbody><tr>
    <td width="56" valign="middle" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;background:{t['color']};color:#fff;text-align:center;font-size:18px;font-weight:900;padding:14px 0;width:56px;vertical-align:middle;">{i}</td>
    <td style="padding:12px 16px;">
      <div style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:9px;color:{t['color']};font-weight:700;letter-spacing:1.5px;margin-bottom:4px;">{t['tag'].upper()}</div>
      <div style="font-size:13px;line-height:1.7;font-weight:600;">{h(t['text'])}</div>
    </td>
  </tr></tbody></table>
</td></tr>"""
    return html


def build_related():
    html = ""
    for r in REFLECTION["related"]:
        html += f"""<tr><td style="padding:10px 0;border-bottom:1px solid #E2DED4;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0"><tbody><tr>
    <td width="100" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:11px;color:#5C5A52;">{r['date']}</td>
    <td style="font-size:13px;font-weight:600;"><a href="https://github.com/HIDEPON-UMG/News-Grasp/blob/main/digest/Summary/{r['date']}.md" style="color:#1A1A1A;text-decoration:none;">{r['title']}</a></td>
    <td width="20" align="right" style="color:#5C5A52;">→</td>
  </tr></tbody></table>
</td></tr>"""
    return html


def main():
    tmpl_path = os.path.join(BASE, "prompts", "email-template.html")
    with open(tmpl_path, encoding="utf-8") as f:
        tmpl = f.read()

    categories_html = "\n".join(build_category_block(cat) for cat in CATEGORIES)

    replacements = {
        "{{ISSUE_DATE}}":              ISSUE_DATE,
        "{{ISSUE_WEEKDAY}}":           WEEKDAY,
        "{{ISSUE_NO}}":                ISSUE_NO,
        "{{TOTAL_CATEGORIES}}":        str(TOTAL_CATEGORIES),
        "{{TOTAL_STORIES}}":           str(TOTAL_STORIES),
        "{{TOTAL_SECTIONS}}":          str(TOTAL_SECTIONS),
        "{{TOC_ROWS_HTML}}":           build_toc_rows(),
        "{{CATEGORIES_HTML}}":         categories_html,
        "{{REFLECTION_TITLE}}":        REFLECTION["title"],
        "{{REFLECTION_SUBTITLE}}":     REFLECTION["subtitle"],
        "{{REFLECTION_LEAD_HTML}}":    h(REFLECTION["lead"]),
        "{{REFLECTION_PULL_QUOTE_HTML}}": h(REFLECTION["pull_quote"]),
        "{{REFLECTION_SECTIONS_HTML}}": build_sections(),
        "{{TAKEAWAYS_HTML}}":          build_takeaways(),
        "{{RELATED_ISSUES_HTML}}":     build_related(),
    }

    html = tmpl
    for k, v in replacements.items():
        html = html.replace(k, v)

    html = re.sub(r'<!--.*?-->', '', html, flags=re.DOTALL)
    html = re.sub(r'\n{3,}', '\n\n', html)

    out_path = os.path.join(BASE, "build", "email.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    size_kb = os.path.getsize(out_path) / 1024
    print(f"Written: {out_path} ({size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
