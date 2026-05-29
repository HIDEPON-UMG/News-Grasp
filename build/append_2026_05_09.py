# -*- coding: utf-8 -*-
import json, pathlib, sys

ROOT = pathlib.Path(__file__).parent.parent
JSONL = ROOT / "data" / "articles.jsonl"

new_entries = [
    # ── FX ───────────────────────────────────────────────────
    {
        "date": "2026-05-09",
        "seen_at": "2026-05-09T06:00:00+09:00",
        "genre": "FX",
        "title": "米4月NFP +11.5万人 予想の1.8倍超 — ドル円157.3円へ急伸、158円防衛ライン巡り週明け介入警戒",
        "url": "https://www.bloomberg.com/jp/news/articles/2026-05-08/TEPXOCGETF5S00",
        "url_norm": "www.bloomberg.com/jp/news/articles/2026-05-08/tepxocgetf5s00",
        "source": "Bloomberg Markets",
        "summary": "米4月NFPが予想+6.5万人に対し+11.5万人。ドル円157.3円へ急伸。失業率4.3%高止まり。FRB利下げ先送り観測が強まり158円防衛ラインとのせめぎ合いが週明け焦点。",
        "entities": {"companies": ["日銀"], "countries": ["米国", "日本"], "services": [], "people": [], "tickers": ["USDJPY"]},
        "topics": ["雇用統計", "為替介入", "ドル円"],
        "industries": ["FX", "経済"],
        "events": ["指標発表"],
        "tags": ["cat/fx", "co/日銀", "country/米国", "country/日本", "topic/雇用統計", "event/指標発表", "score/高"]
    },
    {
        "date": "2026-05-09",
        "seen_at": "2026-05-09T06:00:00+09:00",
        "genre": "FX",
        "title": "USD/JPY 157台で週末クローズ — 介入ゾーン158円前に「買いも売りも入れにくい」均衡",
        "url": "https://www.fxstreet.com/analysis/usd-jpy-price-forecast-struggles-to-lure-buyers-amid-jpy-intervention-fears-us-nfp-eyed-202605080907",
        "url_norm": "www.fxstreet.com/analysis/usd-jpy-price-forecast-struggles-to-lure-buyers-amid-jpy-intervention-fears-us-nfp-eyed-202605080907",
        "source": "FXStreet",
        "summary": "NFP後のUSD/JPYは157.2〜157.5円で週末入り。158円超えで159円視野だが介入リスクが上値抑制。ウォーシュFRB新議長就任後の6月FOMCが構造転換の分水嶺。",
        "entities": {"companies": [], "countries": ["米国", "日本"], "services": [], "people": ["ウォーシュ"], "tickers": ["USDJPY"]},
        "topics": ["ドル円", "テクニカル分析", "FOMC"],
        "industries": ["FX"],
        "events": [],
        "tags": ["cat/fx", "country/米国", "country/日本", "person/ウォーシュ", "topic/ドル円", "topic/テクニカル分析", "score/高"]
    },
    {
        "date": "2026-05-09",
        "seen_at": "2026-05-09T06:00:00+09:00",
        "genre": "FX",
        "title": "ドル円5月見通し「介入一過、焦点はイラン情勢に」 — 地政学×金利差の複合相場、原油価格連動に注目",
        "url": "https://www.gaitame.com/media/entry/2026/05/07/194754",
        "url_norm": "www.gaitame.com/media/entry/2026/05/07/194754",
        "source": "外為どっとコム（外為総研）",
        "summary": "GW介入後ドル円157台に反発。外為総研は構造的円安ドライバーが金利差からイラン発地政学リスクへ移行と指摘。イラン沈静化なら円高・原油安の同時進行シナリオが台頭。",
        "entities": {"companies": [], "countries": ["日本", "米国", "イラン"], "services": [], "people": [], "tickers": ["USDJPY"]},
        "topics": ["地政学リスク", "為替見通し", "原油"],
        "industries": ["FX"],
        "events": [],
        "tags": ["cat/fx", "country/日本", "country/米国", "country/イラン", "topic/地政学リスク", "topic/為替見通し", "score/高"]
    },
    {
        "date": "2026-05-09",
        "seen_at": "2026-05-09T06:00:00+09:00",
        "genre": "FX",
        "title": "ドル円156円台前半で介入らしき急落を観測（5/7） — NFP発表後のポジション整理と週明けシナリオ",
        "url": "https://www.oanda.jp/lab-education/market_news/2026_05_07_usdjpy/",
        "url_norm": "www.oanda.jp/lab-education/market_news/2026_05_07_usdjpy",
        "source": "OANDA Japan",
        "summary": "5/7 NY時間にドル円157.5→156.1円へ1.4円急落。財務省発言との時間的一致から介入観測。NFP後157台再上昇でポジション整理。オプションバリア157.50に大量売り。",
        "entities": {"companies": [], "countries": ["日本"], "services": [], "people": [], "tickers": ["USDJPY"]},
        "topics": ["為替介入", "ポジション", "ドル円"],
        "industries": ["FX"],
        "events": [],
        "tags": ["cat/fx", "country/日本", "topic/為替介入", "topic/ポジション", "score/中"]
    },
    {
        "date": "2026-05-09",
        "seen_at": "2026-05-09T06:00:00+09:00",
        "genre": "FX",
        "title": "【海外市場注目点】4月米雇用統計と今週のドル円 — 強い数字でも158円防衛ラインが上値を制限",
        "url": "https://fx.minkabu.jp/news/366577",
        "url_norm": "fx.minkabu.jp/news/366577",
        "source": "みんかぶFX",
        "summary": "海外市場の注目は4月雇用統計。予想+6.5万人。強い結果で158円試し、弱い結果で155.5円サポートテスト。EUR/JPYは172台でECB利下げ後ずれ観測でクロス円強含み。",
        "entities": {"companies": [], "countries": ["米国", "日本"], "services": [], "people": [], "tickers": ["USDJPY", "EURJPY"]},
        "topics": ["雇用統計", "ドル円", "市場展望"],
        "industries": ["FX"],
        "events": ["指標発表"],
        "tags": ["cat/fx", "country/米国", "country/日本", "topic/雇用統計", "topic/ドル円", "event/指標発表", "score/中"]
    },
    # ── AI ───────────────────────────────────────────────────
    {
        "date": "2026-05-09",
        "seen_at": "2026-05-09T06:00:00+09:00",
        "genre": "AI",
        "title": "Anthropic「Dreaming」正式公開 — エージェントが過去セッションから学習・自己改善、Harvey法律AIで完了率6倍を達成",
        "url": "https://venturebeat.com/technology/anthropic-introduces-dreaming-a-system-that-lets-ai-agents-learn-from-their-own-mistakes",
        "url_norm": "venturebeat.com/technology/anthropic-introduces-dreaming-a-system-that-lets-ai-agents-learn-from-their-own-mistakes",
        "source": "VentureBeat",
        "summary": "AnthropicがClaude Managed Agentsの研究プレビュー「Dreaming」を公開。エージェントが過去セッションを定期バッチで回顧しミスパターンを自動抽出しメモリ更新。Harveyでタスク完了率6倍。",
        "entities": {"companies": ["Anthropic", "Harvey"], "countries": ["米国"], "services": ["Claude"], "people": [], "tickers": []},
        "topics": ["AIエージェント", "自己改善", "記憶システム"],
        "industries": ["AI"],
        "events": ["製品発表"],
        "tags": ["cat/ai", "co/Anthropic", "co/Harvey", "country/米国", "topic/AIエージェント", "event/製品発表", "score/高"]
    },
    {
        "date": "2026-05-09",
        "seen_at": "2026-05-09T06:00:00+09:00",
        "genre": "AI",
        "title": "Claude Managed Agentsに3新機能 — Dreaming・Multiplayer・Long Context 500k を Code w/Claude 2026で一挙解放",
        "url": "https://9to5mac.com/2026/05/07/anthropic-updates-claude-managed-agents-with-three-new-features/",
        "url_norm": "9to5mac.com/2026/05/07/anthropic-updates-claude-managed-agents-with-three-new-features",
        "source": "9to5Mac",
        "summary": "Code w/Claude 2026でDreaming・Multiplayer・Long Context 500kが正式ベータ公開。MultiplayerはHandoffなしに複数Claudeエージェントが並行協調。500kはGemini 1Mより低コストで実用的。",
        "entities": {"companies": ["Anthropic"], "countries": ["米国"], "services": ["Claude"], "people": [], "tickers": []},
        "topics": ["AIエージェント", "マルチエージェント", "コンテキストウィンドウ"],
        "industries": ["AI"],
        "events": ["製品発表"],
        "tags": ["cat/ai", "co/Anthropic", "country/米国", "topic/AIエージェント", "event/製品発表", "score/高"]
    },
    {
        "date": "2026-05-09",
        "seen_at": "2026-05-09T06:00:00+09:00",
        "genre": "AI",
        "title": "AlphaEvolve 詳報 — GoogleのDC電力効率25%改善、NVIDIA依存脱却前夜に「自律最適化」エンジンが稼働",
        "url": "https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-adv",
        "url_norm": "deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-adv",
        "source": "Google DeepMind",
        "summary": "Geminiベースのコーディングエージェントがデータセンターを自律最適化。電力効率25%改善・コンピュート割当の無駄0.7%削減。TPU設計や行列乗算最適化にまで拡張。",
        "entities": {"companies": ["Google-DeepMind", "NVIDIA"], "countries": ["米国"], "services": ["Gemini", "AlphaEvolve"], "people": [], "tickers": ["GOOGL", "NVDA"]},
        "topics": ["AIエージェント", "データセンター最適化", "TPU"],
        "industries": ["AI", "クラウド"],
        "events": [],
        "tags": ["cat/ai", "co/Google-DeepMind", "co/NVIDIA", "country/米国", "topic/AIエージェント", "score/高"]
    },
    {
        "date": "2026-05-09",
        "seen_at": "2026-05-09T06:00:00+09:00",
        "genre": "AI",
        "title": "Google・Microsoft・xAIが米政府のAI事前評価プログラムに参加合意 — 「安全性テスト」を主流モデルの義務へ",
        "url": "https://www.cnn.com/2026/05/05/tech/microsoft-google-xai-government-test-ai-models",
        "url_norm": "www.cnn.com/2026/05/05/tech/microsoft-google-xai-government-test-ai-models",
        "source": "CNN Business",
        "summary": "米NIST傘下のCAISIがGoogle DeepMind・Microsoft・xAIと事前評価協定締結。サイバー攻撃・バイオ・化学兵器リスクのスクリーニングが義務化。AnthropicとOpenAIは不参加。",
        "entities": {"companies": ["Google-DeepMind", "Microsoft", "xAI"], "countries": ["米国"], "services": ["Grok"], "people": [], "tickers": []},
        "topics": ["AI安全性", "規制", "政府評価"],
        "industries": ["AI"],
        "events": ["規制公表"],
        "tags": ["cat/ai", "co/Google-DeepMind", "co/Microsoft", "co/xAI", "country/米国", "topic/AI安全性", "event/規制公表", "score/高"]
    },
    {
        "date": "2026-05-09",
        "seen_at": "2026-05-09T06:00:00+09:00",
        "genre": "AI",
        "title": "GoogleとAmazonのAI利益の半分は「Anthropic株式」評価益 — 実体収益との乖離が生む「AI決算バブル」論",
        "url": "https://fortune.com/2026/04/30/google-amazon-ai-profits-anthropic-stake-bubble-earnings-2026/",
        "url_norm": "fortune.com/2026/04/30/google-amazon-ai-profits-anthropic-stake-bubble-earnings-2026",
        "source": "Fortune",
        "summary": "Q1 2026のAlphabet・Amazon決算で営業外利益の大半をAnthropicの株式評価益が占める。Anthropic評価額9,000億ドルへの資金調達交渉中。バブル構造との指摘。",
        "entities": {"companies": ["Google", "Amazon", "Anthropic"], "countries": ["米国"], "services": [], "people": [], "tickers": ["GOOGL", "AMZN"]},
        "topics": ["AI投資", "決算", "バブル"],
        "industries": ["AI"],
        "events": ["決算"],
        "tags": ["cat/ai", "co/Google", "co/Amazon", "co/Anthropic", "country/米国", "topic/AI投資", "event/決算", "score/中"]
    },
    # ── IT-Consulting ─────────────────────────────────────────
    {
        "date": "2026-05-09",
        "seen_at": "2026-05-09T06:00:00+09:00",
        "genre": "IT-Consulting",
        "title": "NTTデータグループ 大規模組織改編発表 — 「コンサルティング×AI」中核に新セグメント新設・グローバルAIユニット設立",
        "url": "https://www.nttdata.com/global/ja/news/release/2026/050807/",
        "url_norm": "www.nttdata.com/global/ja/news/release/2026/050807",
        "source": "NTTデータグループ",
        "summary": "NTTデータGが2026/5/8にコンサルティングセグメント新設を発表。構想から実装・成果創出を一気通貫。グローバルAIユニット設立・NTT DATA AIVistaを2026年Q2より提供。2027年度AIエージェント3,000億円目標。",
        "entities": {"companies": ["NTTデータ"], "countries": ["日本"], "services": ["NTT DATA AIVista"], "people": [], "tickers": ["9613"]},
        "topics": ["DX", "AIエージェント", "組織改編"],
        "industries": ["IT-コンサル"],
        "events": [],
        "tags": ["cat/it", "co/NTTデータ", "country/日本", "topic/DX", "topic/AIエージェント", "score/高"]
    },
    {
        "date": "2026-05-09",
        "seen_at": "2026-05-09T06:00:00+09:00",
        "genre": "IT-Consulting",
        "title": "Accenture×ServiceNow FDE Program — 企業AIを「パイロット→本番」へ橋渡し、300プリビルドスキル＋AI Control Tower",
        "url": "https://newsroom.accenture.com/news/2026/servicenow-and-accenture-launch-forward-deployed-engineering-program-to-scale-agentic-ai-across-the-enterprise",
        "url_norm": "newsroom.accenture.com/news/2026/servicenow-and-accenture-launch-forward-deployed-engineering-program-to-scale-agentic-ai-across-the-enterprise",
        "source": "Accenture Newsroom",
        "summary": "Forward Deployed EngineeringプログラムでServiceNow・Accenture両社エンジニアが顧客環境に常駐。300以上のプリビルドAIスキルでPoC地獄を解消。AI Control Towerで一元管理。",
        "entities": {"companies": ["アクセンチュア", "ServiceNow"], "countries": ["米国"], "services": [], "people": [], "tickers": ["ACN", "NOW"]},
        "topics": ["AIエージェント", "エンタープライズAI", "PoC"],
        "industries": ["IT-コンサル"],
        "events": [],
        "tags": ["cat/it", "co/アクセンチュア", "co/ServiceNow", "country/米国", "topic/AIエージェント", "score/高"]
    },
    {
        "date": "2026-05-09",
        "seen_at": "2026-05-09T06:00:00+09:00",
        "genre": "IT-Consulting",
        "title": "NTT DATA AIVista、2026年度Q2より提供開始 — シリコンバレー新会社が本格始動、AIエージェント関連3,000億円を狙う",
        "url": "https://enterprisezine.jp/news/detail/23340",
        "url_norm": "enterprisezine.jp/news/detail/23340",
        "source": "EnterpriseZine",
        "summary": "2025/12設立のNTT DATA AIVistaがシリコンバレーで本格始動。LLM不可知論的設計でClaude・GPT・Geminiいずれでも稼働。2027年度AIエージェント3,000億円目標。LITRON Builder受注2,000件超がベース。",
        "entities": {"companies": ["NTTデータ"], "countries": ["日本", "米国"], "services": ["NTT DATA AIVista", "LITRON Builder"], "people": [], "tickers": []},
        "topics": ["AIエージェント", "AIプラットフォーム", "エンタープライズAI"],
        "industries": ["IT-コンサル", "AI"],
        "events": [],
        "tags": ["cat/it", "co/NTTデータ", "country/日本", "country/米国", "topic/AIエージェント", "score/高"]
    },
    {
        "date": "2026-05-09",
        "seen_at": "2026-05-09T06:00:00+09:00",
        "genre": "IT-Consulting",
        "title": "IBM Think 2026 全発表まとめ — Context Studio・Process Studio・量子AI統合、Enterprise Advantageで「アセット基盤型」を標榜",
        "url": "https://businessdailynetwork.com/stories/681788650-ibm-announces-new-ai-consulting-capabilities-and-partnerships-at-think-2026",
        "url_norm": "businessdailynetwork.com/stories/681788650-ibm-announces-new-ai-consulting-capabilities-and-partnerships-at-think-2026",
        "source": "Business Daily Network",
        "summary": "Think 2026でIBMがEnterprise Advantageを発表。Context Studio・Process Studioの2ツール。医療大手Providenceで採用工数90%削減・内部異動12日短縮。2027年に量子×watsonx連携でDC電力最適化宣言。",
        "entities": {"companies": ["IBM"], "countries": ["米国"], "services": ["watsonx", "Enterprise Advantage"], "people": ["アービンド・クリシュナ"], "tickers": ["IBM"]},
        "topics": ["DX", "AIコンサル", "量子コンピューター"],
        "industries": ["IT-コンサル", "AI"],
        "events": ["製品発表"],
        "tags": ["cat/it", "co/IBM", "country/米国", "person/アービンド・クリシュナ", "topic/DX", "event/製品発表", "score/中"]
    },
    {
        "date": "2026-05-09",
        "seen_at": "2026-05-09T06:00:00+09:00",
        "genre": "IT-Consulting",
        "title": "IT大手4社、生成AI適用「本腰」 — NTTデータG「2027年度に開発工程40%効率化」の具体工程が明らかに",
        "url": "https://xtech.nikkei.com/atcl/nxt/column/18/00001/11238",
        "url_norm": "xtech.nikkei.com/atcl/nxt/column/18/00001/11238",
        "source": "日経クロステック",
        "summary": "NTTデータGが2027年度末に生成AIネーティブ開発を全プロジェクトの主流にする計画。要件定義から開発・テストまで40%削減。富士通はKozuchi、NECはcotomi Actで同様の取り組みを加速。",
        "entities": {"companies": ["NTTデータ", "富士通", "NEC"], "countries": ["日本"], "services": ["cotomi Act", "Kozuchi"], "people": [], "tickers": []},
        "topics": ["生成AI導入", "SIer変革", "アウトカム報酬"],
        "industries": ["IT-コンサル"],
        "events": [],
        "tags": ["cat/it", "co/NTTデータ", "co/富士通", "co/NEC", "country/日本", "topic/生成AI導入", "score/中"]
    },
    # ── Game ─────────────────────────────────────────────────
    {
        "date": "2026-05-09",
        "seen_at": "2026-05-09T06:00:00+09:00",
        "genre": "Game",
        "title": "任天堂FY2026通期決算 — Switch 2累計1,986万台・売上2.3兆円・5月25日に1万円値上げ",
        "url": "https://news.denfaminicogamer.jp/news/2605082s",
        "url_norm": "news.denfaminicogamer.jp/news/2605082s",
        "source": "電ファミニコゲーマー",
        "summary": "任天堂FY2026通期は売上2兆3,136億円(+98.6%)・純利益4,240億円(+52.1%)。Switch 2初年度1,986万台達成。ソフト4,871万本。5/25より日本語専用版を49,980→59,980円に値上げ。FY2027は減益見通し。",
        "entities": {"companies": ["任天堂"], "countries": ["日本"], "services": ["Switch-2", "マリカワールド"], "people": [], "tickers": ["7974"]},
        "topics": ["決算", "ゲーム機販売", "価格戦略"],
        "industries": ["ゲーム"],
        "events": ["決算"],
        "tags": ["cat/game", "co/任天堂", "country/日本", "topic/決算", "event/決算", "score/高"]
    },
    {
        "date": "2026-05-09",
        "seen_at": "2026-05-09T06:00:00+09:00",
        "genre": "Game",
        "title": "任天堂FY2027は減益見通し — Switch 2値上げで販売台数減を想定、「逆説的成長」戦略の読み方",
        "url": "https://www.nikkei.com/article/DGXZQOUF075130X00C26A5000000/",
        "url_norm": "www.nikkei.com/article/dgxzqouf075130x00c26a5000000",
        "source": "日本経済新聞",
        "summary": "任天堂FY2027は販売台数16.5万台(前期比-17%)と減少を想定しながら単価上昇で収益を維持する価格弾力性重視戦略。米国・欧州も9月に499.99ドル/ユーロへ値上げ。最終利益は減益見通し。",
        "entities": {"companies": ["任天堂"], "countries": ["日本", "米国", "EU"], "services": ["Switch-2"], "people": [], "tickers": ["7974"]},
        "topics": ["決算", "価格戦略", "ゲーム市場"],
        "industries": ["ゲーム"],
        "events": ["決算"],
        "tags": ["cat/game", "co/任天堂", "country/日本", "country/米国", "country/EU", "topic/決算", "topic/価格戦略", "event/決算", "score/高"]
    },
    {
        "date": "2026-05-09",
        "seen_at": "2026-05-09T06:00:00+09:00",
        "genre": "Game",
        "title": "コナミG FY2026通期決算 — 事業利益31.6%増・1,435億円の過去最高、MGSΔ200万本・3期連続最高益を達成",
        "url": "https://gamebiz.jp/news/425576",
        "url_norm": "gamebiz.jp/news/425576",
        "source": "GameBiz.jp",
        "summary": "コナミGFY2026は売上4,937億円(+17.1%)・事業利益1,435億円(+31.6%)・純利益1,000億円(+33.9%)で全指標過去最高。MGSΔ世界累計200万本・eFootball esports展開で多角IP帝国モデルが証明。",
        "entities": {"companies": ["コナミ"], "countries": ["日本"], "services": ["METAL GEAR SOLID Delta", "eFootball"], "people": [], "tickers": ["9766"]},
        "topics": ["決算", "ゲーム市場", "esports"],
        "industries": ["ゲーム"],
        "events": ["決算"],
        "tags": ["cat/game", "co/コナミ", "country/日本", "topic/決算", "event/決算", "score/高"]
    },
    {
        "date": "2026-05-09",
        "seen_at": "2026-05-09T06:00:00+09:00",
        "genre": "Game",
        "title": "ソニーグループFY2026 営業益1.4475兆円（+13%）過去最高 — PlayStation部門も為替・ネットワーク収益で増益、FY2027は1.6兆円計画",
        "url": "https://gamebiz.jp/news/425542",
        "url_norm": "gamebiz.jp/news/425542",
        "source": "GameBiz.jp",
        "summary": "ソニーGFY2026は売上12.4兆円(+3.7%)・営業益1.4475兆円(+13.4%)と2期連続最高益。PlayStation Plus会員増でG&NS部門増益。FY2027で自己株取得5,000億円を同時発表。",
        "entities": {"companies": ["ソニー"], "countries": ["日本"], "services": ["PlayStation"], "people": [], "tickers": ["6758"]},
        "topics": ["決算", "ゲーム市場", "サブスクリプション"],
        "industries": ["ゲーム"],
        "events": ["決算"],
        "tags": ["cat/game", "co/ソニー", "country/日本", "topic/決算", "event/決算", "score/高"]
    },
    {
        "date": "2026-05-09",
        "seen_at": "2026-05-09T06:00:00+09:00",
        "genre": "Game",
        "title": "【決算分析】Switch 2爆発ヒットで任天堂「V字復活」 — 営業利益3,601億円・値上げ後の「逆張り成長」シナリオ",
        "url": "https://www.today-jp.com/news/nintendo-switch-2-earnings-report-fy2026-profit-surge",
        "url_norm": "www.today-jp.com/news/nintendo-switch-2-earnings-report-fy2026-profit-surge",
        "source": "Today Japan News",
        "summary": "任天堂の営業利益3,601億円(+27.5%)。FY2025の落ち込みからV字復活。Switch 2発売初年度がゲーム機史上2位の初年度出荷規模。値上げ後のFY2027減益宣言が透明経営として評価。",
        "entities": {"companies": ["任天堂"], "countries": ["日本"], "services": ["Switch-2", "マリカワールド"], "people": [], "tickers": ["7974"]},
        "topics": ["決算", "V字回復", "価格戦略"],
        "industries": ["ゲーム"],
        "events": ["決算"],
        "tags": ["cat/game", "co/任天堂", "country/日本", "topic/決算", "score/中"]
    },
]

# 既存エントリのURL正規化セットを取得してdedup
existing_urls = set()
if JSONL.exists():
    with open(JSONL, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    obj = json.loads(line)
                    existing_urls.add(obj.get("url_norm", ""))
                except Exception:
                    pass

appended = 0
with open(JSONL, "a", encoding="utf-8") as f:
    for entry in new_entries:
        if entry["url_norm"] not in existing_urls:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            existing_urls.add(entry["url_norm"])
            appended += 1

print(f"Appended {appended} entries to {JSONL}")
