# coding: utf-8
"""
2026-05-15 HTML メール生成
prompts/email-template.html のプレースホルダを埋めて build/email.html を出力する
"""
import re, os

BASE = r"C:\Users\hidek\Obsidian\New's Grasp\News-Grasp"
CDN  = "https://raw.githubusercontent.com/HIDEPON-UMG/news-grasp-assets/main"

ISSUE_DATE = "2026-05-15"
ISSUE_NO   = "20260515"
WEEKDAY    = "木"

CATEGORIES = [
  {
    "id": "fx", "name": "FX", "nameEn": "Foreign Exchange",
    "accent": "#B8860B", "glyph": "¥", "index": 1,
    "summary": "ウォーシュFRB議長が本日正式就任。CPI+3.8%・PPI+6.0%の「インフレ第二波」を背景に158円防衛ラインが迫り、就任演説が今後の利下げ観測と円相場の命運を握る一日となる。",
    "items": [
      {
        "score": 95, "time": "06:30", "source": "CNBC",
        "title": "ウォーシュ FRB議長 本日正式就任 — 54対45の上院承認、就任スピーチが利下げ観測を左右",
        "url": "https://www.cnbc.com/2026/05/13/kevin-warsh-wins-senate-confirmation-as-the-next-federal-reserve-chair.html",
        "thumb": "https://image.cnbcfm.com/api/v1/image/108295278-1776860361515-gettyimages-2271888399-warsh_senate_517_042126.jpeg?v=1778700082&w=1920&h=1080",
        "bullets": [
          "[[ケビン・ウォーシュ]]氏がパウエル議長の任期満了（5/15）と同時にFRB第17代議長に正式就任。上院54対45と現代最僅差の承認で、党派対立が鮮明だった。",
          "就任スピーチが最大焦点。__「2026年利下げゼロ」示唆ならドル高・株安、利下げ積極姿勢ならドル安・円高__の双方向リスクが同時に存在する。",
          "CPI+3.8%・PPI+6.0%という「インフレ第二波」確認の中でのスタートは、トランプ大統領の強制的な利下げ圧力との衝突局面を早くも形成している。"
        ],
        "related": {"axis": "復状", "note": "[[2026-05-14-FX]] — ウォーシュFRB新議長 明日就任（5/14）記事と復状。就任前日の「タカ派か利下げ派か」の問いに今日の初演説が答える。"}
      },
      {
        "score": 88, "time": "07:00", "source": "Investing.com",
        "title": "USD/JPY 157.9円 — 158円防衛ラインに迫る、ウォーシュ発言次第で介入圏突入",
        "url": "https://www.investing.com/currencies/usd-jpy",
        "thumb": None,
        "bullets": [
          "USD/JPYは157.9円前後で推移し、財務省が介入警戒ゾーンとして意識する[[158円ライン]]に接近している。GW介入後の実需ドル買いが下値を支える構造が変わっていない。",
          "[[CPI+3.8%]]余波とウォーシュ就任でドル買い圧力が二重にかかり、__テクニカル上も200日移動平均を大幅に上回る過熱圏__での攻防となっている。",
          "財務省の「断固たる措置」発言は継続中だが、単発介入の費用対効果は低下しており、日米協調メッセージへのシフトが注目される。"
        ]
      },
      {
        "score": 84, "time": "06:00", "source": "Yahoo Finance",
        "title": "CPI+3.8%・PPI+6.0% — インフレ「第二波」確認、ウォーシュの初手を複雑化",
        "url": "https://finance.yahoo.com/economy/policy/article/kevin-warsh-confirmed-new-fed-chair-as-inflation-kicks-higher-complicating-the-central-banks-path-164303609.html",
        "thumb": None,
        "bullets": [
          "米4月[[CPI前年比+3.8%]]（3年ぶり高水準）と[[PPI+6.0%]]（2022年以来最大）が相次いで確認され、インフレ第二波の実態が統計で裏付けられた。",
          "年内利下げゼロシナリオが台頭し、__ウォーシュ議長は就任初日からFRBの独立性とトランプ政権の利下げ圧力の板挟み__に直面する。",
          "ドル高による日本の輸入インフレ再燃で、日銀の利上げ根拠である「好循環型インフレ」シナリオが揺らぎ始めている。"
        ]
      },
      {
        "score": 79, "time": "07:30", "source": "FXStreet",
        "title": "EUR/USD 1.0810 — ドル全面高が欧州通貨を圧迫、ECB5月利下げ見送り濃厚",
        "url": "https://www.fxstreet.com/currencies/usdjpy",
        "thumb": None,
        "bullets": [
          "EUR/USD 1.0810と2ヶ月ぶり安値圏。[[ドル全面高]]が欧州通貨を押し下げ、EUR/JPYも183円台で膠着している。",
          "[[ECB]]は欧州インフレ鈍化から5月利下げを検討していたが、__ドル高によるインポートインフレ再燃懸念__から利下げ見送りが有力な情勢に変わった。",
          "ユーロ圏製造業PMI 51.2と回復基調にあるが、対ドル通貨安が外需回復の恩恵を相殺するリスクが意識されている。"
        ]
      },
      {
        "score": 73, "time": "08:00", "source": "Trading Economics",
        "title": "BOJ 夏利上げ再評価 — 輸入インフレ加速で「7月より12月優先」派が台頭",
        "url": "https://tradingeconomics.com/japan/currency",
        "thumb": None,
        "bullets": [
          "米CPI+3.8%による円安加速で日本の輸入インフレが再燃する懸念から、[[植田和男]]総裁の7月利上げ確率が40%台前半に低下した。",
          "「利上げで円高誘導→輸入インフレ抑制」という日銀の従来論拠が通用しにくくなり、__12月利上げ優先論が市場で復活__しつつある。",
          "BOJは「物価・経済の推移次第」の条件付きスタンスを維持するが、米インフレ再燃はそのシナリオの不確実性を大幅に高めている。"
        ]
      },
    ]
  },
  {
    "id": "ai", "name": "AI", "nameEn": "Artificial Intelligence",
    "accent": "#2D5BB8", "glyph": "◆", "index": 2,
    "summary": "AnthropicがOpenAIを初めてビジネス採用率で逆転（34.4% vs 32.3%）し、ARR$300億突破・Google Cloudへ$2,000億コミットという驚異的スケールを実現。5/19のGoogle I/Oに向け「Gemini 4」「Googlebook」のプレビューが過熱する一週間が始まる。",
    "items": [
      {
        "score": 96, "time": "09:00", "source": "VentureBeat",
        "title": "Anthropic、ビジネスAI採用率で初めてOpenAIを逆転 — 34.4% vs 32.3%、ARR$300億突破",
        "url": "https://venturebeat.com/technology/anthropic-finally-beat-openai-in-business-ai-adoption-but-3-big-threats-could-erase-its-lead",
        "thumb": "https://images.ctfassets.net/jdtwqhzvc2n1/4m169U8ajMEpWjEn6pQgzK/7690906968897882b8756a902d8848c6/Nuneybits_Vector_art_of_two_rising_lines_on_a_graph_burnt_orang_937edfc7-d114-495e-aad5-a2f1297757c6.webp?w=800&q=75",
        "bullets": [
          "[[Anthropic]]の法人AI採用率が34.4%となり、[[OpenAI]] 32.3%を初めて上回った。AIレース開始以来初の逆転で、Claudeの企業信頼性とカスタマイズ性が評価されたと分析される。",
          "ARR（年換算売上）は$300億超を突破し、前年の$90億から3倍超の急成長。$100万超の契約企業数も500社→1,000社超へと2ヶ月で倍増した。",
          "ただし記事は「3大脅威」として①Google自社AIシフト・②OpenAIの価格攻勢・③Microsoft Copilotのデフォルト化を指摘し、__リードの持続可能性に疑問__を呈している。"
        ]
      },
      {
        "score": 92, "time": "07:30", "source": "The Information / Yahoo Finance",
        "title": "Anthropic、Google Cloudに2,000億ドル支出コミット — 最大顧客と最大出資元が同一の異例構造",
        "url": "https://finance.yahoo.com/sectors/technology/articles/anthropic-commits-spending-200-billion-204952501.html",
        "thumb": "https://s.yimg.com/os/en/reuters.com/dff2f74e627cab8d03b9a730cffe45b3",
        "bullets": [
          "AnthropicがGoogleのクラウドインフラに[[2,000億ドル]]の支出をコミットしたとThe Informationが報道。Google既存の400億ドル出資と合わせ、__最大顧客と最大出資元が同一という前例のない資本構造__が完成した。",
          "この「資本の共依存」はAnthropicの独立性に対する規制当局の疑念を呼ぶ可能性があり、EU AI Act審査でも注視される見込みだ。",
          "独占禁止の観点からはGoogleとOpenAI（Microsoft）の二極構造を強化し、中規模AI企業のクラウド選択肢が事実上縮小するリスクがある。"
        ],
        "related": {"axis": "波及", "note": "[[2026-04-28-AI]] — Google Anthropicに最大400億ドル追加投資（4/28）。出資に続き今度は調達コミットで相互依存が深化。"}
      },
      {
        "score": 88, "time": "08:30", "source": "Android Authority",
        "title": "Google I/O 2026（5/19）完全プレビュー — Gemini 4・Android 17・Googlebook が5日後に全解禁",
        "url": "https://www.androidauthority.com/what-to-expect-from-google-io-2026-3664979/",
        "thumb": None,
        "bullets": [
          "Google I/O 2026は5月19日（月）開幕。目玉は①[[Gemini 4]]または新世代Geminiモデル ②[[Android 17]] ③最新AIラップトップ「Googlebook」の3本柱となる見込み。",
          "AndroidShowで先行発表された「Gemini Intelligence」はアシスタントからエージェントへのシフトを体現し、__Gmail・カレンダー・Mapsを横断する自律タスク実行__が中心機能となる。",
          "OpenAI GPT-5.5との直接競合が続く中、Gemini 4は長文脈処理と多言語化で差別化を狙うとされ、Google Cloudの法人向けデモも同時公開予定だ。"
        ]
      },
      {
        "score": 82, "time": "09:30", "source": "Revolution in AI",
        "title": "Orbit vs Pulse vs Proactive Assist — プロアクティブAI三つ巴、日次briefingを巡る覇権戦",
        "url": "https://www.revolutioninai.com/2026/05/google-remy-vs-anthropic-orbit-ai-agent-2026.html",
        "thumb": "https://blogger.googleusercontent.com/img/a/AVvXsEh02T81Oy-2Kh4fVhnMRTffVng9UVaQx33c4dU91CAzgDzkhNdUKAbE23LdeGSPdxTFjeB_txASgbB156c3AIu5-_g-tziJcU98h13LLGyb8HGWzTO0D8w_kGGZ4QGlQyo6ylMY68mqRFSxt_4rrbh96Mo0eCM0e98weUN9FLas6qC7DznOV2PXVUh1xmKo=w640-h338",
        "bullets": [
          "Anthropic「[[Orbit]]」・OpenAI「ChatGPT Pulse」・Google「Proactive Assistance」の三者が同時並行で「ユーザーが問わずとも先回りして情報を届ける」プロアクティブAIを展開している。",
          "OrbitはGmail/Slack/GitHub/Calendarと接続し毎朝パーソナライズドブリーフィングを生成する。__Pulseは2025年9月にPro向けで先行し、今は差別化でライフコーチ機能を追加中__。",
          "プロアクティブAIの競争軸は「コネクタの数 × プライバシー設計」で決まるとされ、企業向けはAnthropicのAPI品質、コンシューマーはGoogleのAndroid統合が優位を持つとの見方が有力だ。"
        ]
      },
      {
        "score": 77, "time": "08:00", "source": "Fortune",
        "title": "Fortune Tech: Googlebook 登場・Musk の OpenAI持分 — ビッグテック地殻変動の一週",
        "url": "https://fortune.com/2026/05/13/behold-the-googlebook/",
        "thumb": None,
        "bullets": [
          "Fortuneが「[[Googlebook]]」を今週の最大ニュースと位置付け。GoogleがAndroidベースの高性能AI統合ラップトップ新カテゴリを打ち出し、AppleのM4 MacBookとの真っ向対決を宣言した。",
          "Musk氏がOpenAIの持分取得を模索しているという報道も交錯し、__OpenAIの支配構造を巡る綱引きが再燃__。Altman-Musk間の法廷闘争との連関も注目される。",
          "「単一の強い製品」から「__エコシステムでの占有率__」へ──プラットフォーム経済が成熟期に入ったことを象徴する一週間だった。"
        ]
      },
    ]
  },
  {
    "id": "it", "name": "IT-Consulting", "nameEn": "IT & Consulting",
    "accent": "#2E6B52", "glyph": "▲", "index": 3,
    "summary": "OpenAI・AnthropicがPrivate Equity企業向けAIサービスへ直接参入し、コンサル業界への「プラットフォーム侵食」が本格化。アクセンチュアはAI人材8.5万人・生成AI収益$27億（3倍増）で実行力格差を拡大している。",
    "items": [
      {
        "score": 92, "time": "08:00", "source": "Nerd Level Tech",
        "title": "OpenAI×Anthropic、PE系企業向けAIサービスへ直接参入 — コンサル業界の主戦場に侵食",
        "url": "https://nerdleveltech.com/openai-anthropic-private-equity-consulting-ventures",
        "thumb": None,
        "bullets": [
          "[[OpenAI]]と[[Anthropic]]が相次いでPrivate Equity・大企業向けのAIサービス子会社を設立し、従来コンサルファームが独占してきた「戦略立案＋実装支援」市場に直接侵食を開始した。",
          "OpenAIのDeployCo（$40億）とAnthropicのBlackstone/Goldman Sachs支援エンタープライズJVは、__コンサルタントの代替ではなくコンサルファームそのものの代替__を目指す構造だ。",
          "Big4・MBBは「AIモデル調達者」から「AIサービスプロバイダー」に自らも変質しつつあるが、AIネイティブ企業の速度と価格競争力に対して劣後するリスクが高まっている。"
        ]
      },
      {
        "score": 88, "time": "09:00", "source": "NTTデータ経営研究所",
        "title": "NTTデータ経営研究所、金融機関向けAI導入コンサル18サービス体制 — EU AI Act・米FDIC指針に準拠",
        "url": "https://www.nttdata-strategy.com/newsrelease/260507/",
        "thumb": "https://www.nttdata-strategy.com/images/ogp/ogp-common.jpg",
        "bullets": [
          "[[NTTデータ経営研究所]]が2026年5月7日付で金融機関向けAI導入コンサルティング18サービスの提供を開始。EU AI Actの高リスクAI適用（2026年8月）と米FDIC・OCC・FRBの統合モデルリスク管理指針（2026年4月）への対応を網羅する。",
          "__金融規制×AI実装のダブル対応__を単一コンサルパッケージで提供するのは国内初水準とされ、銀行・証券・保険での早期採用が見込まれる。",
          "NTTデータグループが別途5,000名をAIエージェント開発に専任投入する戦略と連動しており、規制対応から開発まで一貫提供する体制を整えつつある。"
        ],
        "related": {"axis": "復状", "note": "[[2026-05-14-IT-Consulting]] — NTTデータ AIエージェント5,000名（5/14）。グループ全体のAI戦略との連動が本日明確化。"}
      },
      {
        "score": 84, "time": "07:30", "source": "CNBC",
        "title": "OpenAI Frontier Alliance 詳報 — McKinsey/BCG/アクセンチュア/Capgemini が認定チーム・投資義務",
        "url": "https://www.cnbc.com/2026/02/23/open-ai-consulting-accenture-boston-capgemini-mckinsey-frontier.html",
        "thumb": "https://image.cnbcfm.com/api/v1/image/108267428-17715195742026-02-19t075251z_840998235_rc2vojaaaikb_rtrmadp_0_india-ai.jpeg?v=1771519587&w=1920&h=1080",
        "bullets": [
          "OpenAIが2026年2月に締結した「Frontier Alliance」の詳細が明らかに。[[McKinsey]] / [[BCG]] / [[アクセンチュア]] / Capgeminiの4社が、専任認定チームの設置と自社内AIエージェント実装への投資義務を条件に締結した多年度契約。",
          "各社は「OpenAI認定パートナー」として顧客のエンタープライズAI戦略策定から本番実装まで一貫支援し、__OpenAIはコンサルを「販売チャネル」として取り込む構造__を確立した。",
          "皮肉にも今週のOpenAI+AnthropicのPEベンチャー設立ニュースと合わせると、AIがコンサルに「パートナー」と「競合」の二重の顔を持って向き合う姿が浮かぶ。"
        ],
        "related": {"axis": "対立", "note": "OpenAI×Anthropic PE参入（本日）と対立の関係。Allianceでコンサルを取り込む一方、別途直接競合する二面戦略が鮮明。"}
      },
      {
        "score": 80, "time": "08:30", "source": "Copilot Experts",
        "title": "アクセンチュア、AI人材8.5万人・生成AI収益$27億（前年比3倍） — 実行力格差が拡大",
        "url": "https://copilot-experts.com/top-ai-consulting-firm/",
        "thumb": None,
        "bullets": [
          "[[アクセンチュア]]のAI・データプロフェッショナルが85,000名超（2026年3月）に達し、Big4やMBBを圧倒する人材規模を確立。FY2025の生成AI収益は$27億（前年比3倍）・受注残$59億。",
          "__実行力重視（Deloitte・EY・アクセンチュア）と戦略重視（McKinsey・BCG・Bain）の成長格差が拡大__し、近年実行型の成長率が戦略型の約2倍となっている。",
          "英Faculty買収完了（400名のデータ科学者・AIエンジニア即戦力合流）など人材M&Aも継続し、AI実装能力のスケール競争が続く。"
        ]
      },
      {
        "score": 75, "time": "09:00", "source": "Plus AI",
        "title": "2026年コンサルのAI活用最前線 — PwC「Human+AI」・Deloitte・アクセンチュアの人材育成格差",
        "url": "https://plusai.com/blog/how-consulting-firms-use-ai",
        "thumb": None,
        "bullets": [
          "[[PwC]]が「Human + AI Skillset」30スキルカリキュラムを全社展開（AI関連15・人間力関連15）。アクセンチュアのLearnVantage・DeloitteのAI AcademyとともにBig4各社の人材育成競争が激化している。",
          "__AIが実務を担う割合が増えても「発注側・評価側」としての人間力は不可欠__という共通認識が業界に広がり、クライアント説明力・倫理判断・コミュニケーション能力が次の差別化軸となりつつある。",
          "一方でMcKinsey・BCG・Bainは「AIリテラシー」より「問題設定力」を重視する採用基準を維持しており、コンサルタント像の哲学的分岐が生じている。"
        ],
        "related": {"axis": "復状", "note": "[[2026-05-11-IT-Consulting]] — PwC Human+AI Skillset（5/11）からの続報。詳細な業界比較に発展している。"}
      },
    ]
  },
  {
    "id": "economy", "name": "Economy", "nameEn": "Economy",
    "accent": "#8E2A19", "glyph": "■", "index": 4,
    "summary": "ウォーシュ新FRB議長が本日就任し、CPI+3.8%・PPI+6.0%のインフレ第二波確認済みの中でスピーチに市場が固唾を飲む。日経平均は61,000〜65,000円レンジ内で推移し、米インフレ再燃による外資利益確定の波が到達するかが今週の焦点だ。",
    "items": [
      {
        "score": 94, "time": "07:00", "source": "Washington Post",
        "title": "ウォーシュ就任後の市場シナリオ — 「利下げなし」示唆なら株安・円高、積極利下げなら株高",
        "url": "https://www.washingtonpost.com/business/2026/05/14/warsh-be-confirmed-fed-chair-trump-allies-warn-rate-cuts/",
        "thumb": None,
        "bullets": [
          "[[ケビン・ウォーシュ]]新議長はトランプ陣営から「積極利下げ」を期待されているが、CPI+3.8%・PPI+6.0%というインフレ現実の前では__FRBの独立性を守るためタカ派を演じざるを得ない__との見方も多い。",
          "WaPoによればトランプ系議員の一部は「利下げがなければウォーシュ更迭」とまで発言しており、政治的干渉リスクが市場の不確実性プレミアムを押し上げている。",
          "S&P500は7,200〜7,300のレンジで就任演説を待機しており、発言内容によって翌日の動きが決定される「バイナリーイベント」の性格を帯びている。"
        ]
      },
      {
        "score": 90, "time": "08:00", "source": "247 Wall St.",
        "title": "S&P500 7,250割れ警戒 — PPI+6.0%・ウォーシュ不確実性・割高バリュエーションが重なる",
        "url": "https://247wallst.com/investing/2026/05/13/stock-market-live-may-13-2026-sp-500-down-on-jump-in-inflation/",
        "thumb": None,
        "bullets": [
          "[[S&P500]]は5/13のPPI発表後に続落し、最高値7,300から7,250を割り込む場面も見られた。インフレ第二波確認で__年内利下げゼロシナリオのPER調整圧力__が強まっている。",
          "半導体株も利益確定売りの対象となり、NVIDIAは200ドル台で推移。FOMC期待剥落と高バリュエーションの「双重苦」が指数を下押しする構図だ。",
          "ただしAI設備投資の継続が企業業績を支えており、下値の堅さもあることからボラタイル・レンジ相場が続くとのコンセンサスが形成されている。"
        ],
        "related": {"axis": "復状", "note": "[[2026-05-14-Economy]] — 米PPI前年比+6.0%（5/14）と復状の関係。インフレ統計から株価への波及が本日の焦点に。"}
      },
      {
        "score": 85, "time": "09:00", "source": "トウシル（楽天証券）",
        "title": "日経平均 今週61,000〜65,000円レンジ — ウォーシュ就任後のドル円動向が外資行動を左右",
        "url": "https://media.rakuten-sec.net/articles/-/52102",
        "thumb": None,
        "bullets": [
          "[[日経平均]]は5/15週の予想レンジとして61,000〜65,000円が提示されている。直近62,742円（5/12終値）から、ウォーシュ就任演説の内容次第でレンジ上下どちらにも振れる局面だ。",
          "ドル高が続けば外資の円ヘッジコストが上昇し、__利益確定と為替ヘッジの見直しが重なって短期的な売り圧力__になる可能性がある。",
          "一方でAI関連中小型株の出遅れ解消が続いており、個人投資家の押し目買いが下値を支える動きが観測されている。"
        ]
      },
      {
        "score": 80, "time": "09:30", "source": "株基礎.com",
        "title": "S&P500 2026年展望 — ウォーシュ体制がフロントローディング利下げなら年末8,000も射程",
        "url": "https://kabukiso.com/america/outlook/2026/sp500_may.html",
        "thumb": None,
        "bullets": [
          "5月時点のS&P500見通しを整理。ウォーシュ体制が「フロントローディング（前倒し大幅）利下げ」を選択すれば、AI主導の企業業績回復と相まって__2026年末に8,000ポイントを目指すブル相場__が想定される。",
          "一方でインフレ再燃シナリオでは年内利下げゼロ・逆に利上げ示唆の可能性もあり、6,500ポイントを割り込むベアシナリオも現実味を持つ。",
          "地政学（米中・イラン）・財政（関税収入と税収）・AI（設備投資の実需貢献）の3変数が2026年下半期の分岐点を形成するとの分析が主流だ。"
        ]
      },
      {
        "score": 76, "time": "08:30", "source": "EBC Financial Group",
        "title": "日本10年国債利回り上昇継続 — 日銀利上げ×輸入インフレ×財政懸念が「三重圧力」",
        "url": "https://www.ebc.com/jp/forex/288738.html",
        "thumb": None,
        "bullets": [
          "日本の[[10年国債利回り]]は上昇を続け、市場では2.0%への着地点シナリオが浮上している。日銀利上げ期待・ホルムズ海峡経由エネルギー高による輸入インフレ・財政懸念の三重圧力が要因だ。",
          "__円安・金利上昇・株高が同時進行する「ヤマト相場」__は理論的には成立しにくいが、AI投資ブームが資本フローを変質させ一時的に共存している。",
          "野村証券の上方修正（2026年末63,000円・上振れ7万円台）と日銀利上げ加速シナリオが両立するかが下半期の論点となる。"
        ]
      },
    ]
  },
  {
    "id": "game", "name": "Game", "nameEn": "Gaming",
    "accent": "#5E3D8C", "glyph": "●", "index": 5,
    "summary": "Nintendo Switch 2が5月25日から1万円値上げ（¥59,980）へ。メモリ高騰・関税・為替の三重苦を受けた改定で、駆け込み購入による通販品薄が発生中。Cygamesはグリオグルーヴ完全子会社化でIP映像品質の強化に踏み込んだ。",
    "items": [
      {
        "score": 94, "time": "06:00", "source": "ファミ通",
        "title": "Nintendo Switch 2 — 5/25から1万円値上げ¥59,980、メモリ高騰・関税・為替の三重苦",
        "url": "https://www.famitsu.com/article/202605/74473",
        "thumb": "https://cimg.kgl-systems.io/camion/files/74473/thumbnail_gaBU.jpg?x=1280",
        "bullets": [
          "[[任天堂]]が5月25日よりSwitch 2（日本/日本向け専用モデル）の価格を[[¥49,980 → ¥59,980]]へ1万円引き上げると発表（5/8）。メモリ価格高騰・関税コスト・為替変動・グローバル価格バランス調整が複合要因。",
          "古川社長は「中長期的な市場環境変化が本体事業に及ぼす影響を考慮した」と説明し、__値上げは「成熟期の収益最適化」であり撤退・減産とは異なる戦略__との立場を強調した。",
          "Nintendo Switch Onlineも7月1日から年間¥2,400→¥3,000に値上げ。本体＋サービスの実質総コストが増加し、潜在ユーザーの参入ハードルが上昇する懸念がある。"
        ]
      },
      {
        "score": 88, "time": "07:00", "source": "GAME Watch",
        "title": "任天堂 古川社長「発表済以外の新作も用意」— 値上げ後の下半期をソフト力で支える戦略",
        "url": "https://game.watch.impress.co.jp/docs/news/2107890.html",
        "thumb": None,
        "bullets": [
          "任天堂2026年3月期決算説明会Q&Aで古川社長が「発表済みのタイトル以外にも用意がある」と発言。__スプラトゥーン レイダース（夏）・新ゼルダ（冬）に加えてサプライズ新作の存在__を示唆した。",
          "Switch 2のFY2027販売台数予測は1,650万台（前期比17%減）だが、ソフトウェア単価上昇とMy Nintendo Store直販比率向上で収益を補完するモデルが強調された。",
          "[[FY2026はSwitch 2が1,986万台]]と初代の同期820万台を大幅超過。2年目に値上げ施策を打てる台数基盤が確立されており、これが「攻めの値上げ」を可能にしている。"
        ],
        "related": {"axis": "復状", "note": "[[2026-05-10-Game]] — Switch 2、2年目も初代を凌駕するペース（5/10）と復状の関係。台数実績確認後の値上げ戦略として整合する。"}
      },
      {
        "score": 83, "time": "08:00", "source": "Cygames",
        "title": "Cygames、CG映像制作グリオグルーヴを完全子会社化 — ウマ娘等のIP映像クオリティ強化へ",
        "url": "https://www.cygames.co.jp/news/id-24837/",
        "thumb": "https://www.cygames.co.jp/app/wp-content/uploads/2026/04/b9f664f9838beac022960c423c023ad2.jpg",
        "bullets": [
          "[[Cygames]]が創業31年の3DCG・VFX制作プロダクション[[グリオグルーヴ]]の全株式を取得し完全子会社化（5/1付）。取得価額は非公開。",
          "グリオグルーヴは映画・CM・ゲームに渡るリアリスティック3DCGを強みとし、Cygamesとの連携で__ウマ娘・グランブルーファンタジー等のIPトレーラー・アニメ映像のさらなる品質向上__と制作効率化が期待される。",
          "モバイルIPのコンシューマー/アニメ展開が加速する中、自社内に映像制作機能を持つことで外注コスト削減と機密保持の両立を狙う動きと分析される。"
        ]
      },
      {
        "score": 78, "time": "09:00", "source": "ファミ通",
        "title": "Switch 2 2026年5〜9月 注目新作26選 — スプラレイダース・リズム天国・ほの暮しが夏三本柱",
        "url": "https://www.famitsu.com/article/202605/72453",
        "thumb": "https://cimg.kgl-systems.io/camion/files/72453/thumbnail_eVqp.jpg?x=1280",
        "bullets": [
          "ファミ通が2026年5〜9月発売予定のSwitch/Switch 2タイトル26本を特集。[[スプラトゥーン レイダース]]・[[リズム天国 ミラクルスターズ]]・[[ほの暮しの庭]]の3本が夏の注目枠として筆頭に挙げられた。",
          "SQUARE ENIXの新作ロールプレイングや[[カプコン]]PRAGMATA DLCなど大手タイトルも参入し、__夏休み商戦でSwitch 2がライバル機を引き離せるかが下半期の試金石__となる。",
          "値上げ後初の繁忙期となるため、各社のプライシング戦略も注目点。本体¥59,980に対してソフト1本¥8,000超が続くと「遊び続けるコスト」の見直し論が強まる可能性がある。"
        ]
      },
      {
        "score": 73, "time": "10:00", "source": "Nintendo Switch 情報ブログ",
        "title": "Switch 2 値上げ発表後、通販在庫が瞬間蒸発 — 「駆け込み購入」需要が過熱",
        "url": "https://ninten-switch.com/switch-2-kakaku-2026-kaitei-shinausu",
        "thumb": None,
        "bullets": [
          "5月8日の価格改定発表後、楽天ブックス・ノジマオンライン・ヨドバシ・ジョーシンなど主要通販サイトで[[Switch 2]]在庫が即日完売・品薄状態に突入した。",
          "消費税抜き実質値上げ額は約1万円であり、__旧価格での購入チャンスを逃すまいとする消費者の合理的行動__が在庫枯渇を引き起こした形だ。",
          "アンチ転売対策として任天堂はマイニンテンドーストア購入履歴との照合を強化しており、転売目的の買い占めには一定の抑止効果が期待される。"
        ]
      },
    ]
  },
]

REFLECTION = {
  "title": "ウォーシュ就任とAnthropicの逆転",
  "subtitle": "インフレ第二波とAI再編が同時進行する木曜日",
  "lead": "本日5分野・25本のニュースから浮かび上がる最大のテーマは[[ウォーシュ就任]]と[[Anthropic逆転]]の同時進行である。為替・経済では「インフレ第二波 × 新FRB議長」というバイナリーイベントが発動し、AIではAnthropicが初めてOpenAIのビジネス採用率を逆転した歴史的な日が到来した。以下、各カテゴリを横断して読み解く。",
  "pull_quote": "「単一の強い製品」から「__エコシステムでの占有率__」へ──Anthropicが$2,000億をGoogleにコミットした日、プラットフォーム経済が次のフェーズに入った。",
  "sections": [
    {
      "tag": "総論", "accent": "#1A1A1A",
      "heading": "ウォーシュ就任がすべての変数を書き換える日",
      "body": "[[ケビン・ウォーシュ]]新FRB議長が本日正式就任した。CPI+3.8%・PPI+6.0%というインフレ第二波を前に、市場は「タカ派か利下げ派か」という二択を就任演説から読もうとしている。__どちらに振れても今日という日は為替・株式・AI投資すべての変数を書き換える節目__である。ベッセント財務長官との日米通貨協調も継続しており、円相場は158円という「介入の壁」と「実需の床」の間で緊張が高まっている。"
    },
    {
      "tag": "為替・経済", "accent": "#B8860B",
      "heading": "インフレ第二波が「利下げ夢想」を打ち砕く",
      "body": "4月の米CPI・PPIは「インフレは収束に向かっている」という市場の楽観シナリオを完全に破壊した。[[CPI+3.8%・PPI+6.0%]]は2022年来の水準であり、__年内利下げシナリオはほぼ消えた__と断言しても過言でない。日本にとっての影響は二重で、ドル高による輸入インフレ加速と、日銀の「好循環型インフレ」根拠の揺らぎが同時に発生している。日経平均の61,000〜65,000円レンジ維持にはウォーシュ演説が穏健であることが必要条件だ。"
    },
    {
      "tag": "AI・技術", "accent": "#2D5BB8",
      "heading": "Anthropicの逆転が証明した「信頼性の経済学」",
      "body": "AI史上初めて[[Anthropic]]が[[OpenAI]]のビジネス採用率を逆転した（34.4% vs 32.3%）。この逆転の背景には、Claude Opus 4.7の性能向上と「安全性」「カスタマイズ性」「コンプライアンス対応」という3軸での評価が高まったことがある。一方で$2,000億のGoogle Cloudコミットは「最大顧客と最大出資元が同一」という構造的依存を生み出し、__独立性への疑念と競争力の両立というジレンマ__を抱え込んだ形だ。"
    },
    {
      "tag": "産業・業界", "accent": "#2E6B52",
      "heading": "コンサルへの侵食とSwitch 2の「価値の問い直し」",
      "body": "IT-Consultingでは[[OpenAI]]・[[Anthropic]]が同時にPrivate Equity向けAIサービス会社を設立し、McKinsey・BCGが「Alliance締結相手」から「競合」に変わる日が近づいている。Gameでは[[Nintendo-Switch-2]]が1万円値上げで「この価格でも欲しいか？」というユーザーへの問い直しを行った。どちらも「既存の価値提供者が置換されるかどうか」という共通の問いを持つ。"
    },
    {
      "tag": "明日へ", "accent": "#C9B98A",
      "heading": "Google I/O（5/19）とウォーシュ演説が次の相場を決める",
      "body": "5月19日のGoogle I/Oは[[Gemini 4]]・[[Android 17]]・[[Googlebook]]の3本柱が全解禁される。これがAnthropicの逆転に対するGoogleの回答となる。FX・株式ではウォーシュ演説の内容が今後数週間の方向性を決める。__両イベントが同週に重なる5月第3〜4週は、2026年下半期のAI相場の基点__となる可能性が高い。"
    },
  ],
  "takeaways": [
    {"tag": "為替", "color": "#B8860B", "text": "ウォーシュ就任演説が「タカ派」なら158円超→[[財務省介入]]パッケージ、「利下げ積極」ならドル安・円高へ急反転という二択バイナリーイベントが本日発動する"},
    {"tag": "AI",   "color": "#2D5BB8", "text": "[[Anthropic]]がOpenAIを初めてビジネス採用率で逆転した歴史的な日。ARR$300億突破・$2,000億Google Cloudコミットで「独立系AI」の夢と「プラットフォーム依存」の現実が同居する"},
    {"tag": "産業", "color": "#2E6B52", "text": "AI企業がコンサルへ直接侵食（OpenAI DeployCo・Anthropic JV）しつつAllianceでコンサルを販売チャネルにする二面戦略——最終的にどちらが業界の主役を担うか2026年下半期が分岐点"},
  ],
  "related": [
    {"date": "2026-05-14", "title": "ウォーシュ就任前日・PPI+6.0%・Anthropic金融エージェント"},
    {"date": "2026-05-11", "title": "ベッセント訪日・AMD半導体多極化・McKinsey AI採用変革"},
    {"date": "2026-05-10", "title": "Switch 2ミリオンセラー詳報・Cygames Little Noah発売"},
  ]
}


# ────────────────────────────────────────────────────────────────
# ヘルパー
# ────────────────────────────────────────────────────────────────
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


TOTAL_CATEGORIES = len(CATEGORIES)
TOTAL_STORIES    = sum(len(c["items"]) for c in CATEGORIES)
TOTAL_SECTIONS   = len(REFLECTION["sections"])


# ────────────────────────────────────────────────────────────────
# TOC rows
# ────────────────────────────────────────────────────────────────
def build_toc_rows():
    rows = ""
    for cat in CATEGORIES:
        rows += f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:6px;"><tbody><tr>
  <td width="32" class="m" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:12px;color:{cat['accent']};font-weight:700;">{cat['glyph']}</td>
  <td style="font-size:14px;font-weight:700;">{cat['name']} <span style="color:#5C5A52;font-size:11px;font-family:'JetBrains Mono',Consolas,'Courier New',monospace;">{cat['nameEn']}</span></td>
  <td align="right" style="font-family:'JetBrains Mono',Consolas,'Courier New',monospace;font-size:11px;color:#5C5A52;">{len(cat['items'])} stories</td>
</tr></tbody></table>"""
    return rows


# ────────────────────────────────────────────────────────────────
# Article cards
# ────────────────────────────────────────────────────────────────
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


# ────────────────────────────────────────────────────────────────
# Category block
# ────────────────────────────────────────────────────────────────
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


# ────────────────────────────────────────────────────────────────
# Reflection sections
# ────────────────────────────────────────────────────────────────
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


# ────────────────────────────────────────────────────────────────
# Takeaways
# ────────────────────────────────────────────────────────────────
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


# ────────────────────────────────────────────────────────────────
# Related issues
# ────────────────────────────────────────────────────────────────
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


# ────────────────────────────────────────────────────────────────
# メイン生成
# ────────────────────────────────────────────────────────────────
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

    # コメント除去（Gmailクリッピング閾値102KB対策）
    html = re.sub(r'<!--.*?-->', '', html, flags=re.DOTALL)
    # 連続空行を1行に圧縮
    html = re.sub(r'\n{3,}', '\n\n', html)

    out_path = os.path.join(BASE, "build", "email.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    size_kb = os.path.getsize(out_path) / 1024
    print(f"Written: {out_path} ({size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
