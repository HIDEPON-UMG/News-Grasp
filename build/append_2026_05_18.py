"""2026-05-18 分の記事を articles.jsonl に追記するスクリプト"""
import json, sys, os
sys.stdout.reconfigure(encoding='utf-8')

os.chdir(os.path.dirname(os.path.abspath(__file__)) + '/..')

date = '2026-05-18'
seen_at = '2026-05-18T06:00:00+09:00'

articles = [
    # FX
    {
        'date': date, 'seen_at': seen_at, 'genre': 'FX',
        'title': 'BOJ利上げ確率77%に急騰 — 円安圧力の中でも次の一手を市場が先読み',
        'url': 'https://www.tradingkey.com/analysis/economic/central-banks/261884189-boj-rate-hike-yen-weakness-pricing-divergence-oil-geopolitics-intervention-tradingkey',
        'url_norm': 'tradingkey.com/analysis/economic/central-banks/261884189-boj-rate-hike-yen-weakness-pricing-divergence-oil-geopolitics-intervention-tradingkey',
        'source': 'TradingKey', 'summary': 'BOJ6月利上げ確率が77%に急騰。スワップ市場にプライシング形成。審議委員3名が1.0%への引き上げを主張。', 'thumb': None,
        'entities': {'companies': ['日銀'], 'countries': ['日本', '米国'], 'services': [], 'people': ['植田和男'], 'tickers': ['USDJPY']},
        'topics': ['利上げ', '金融政策', '金利差'], 'industries': ['金融'], 'events': ['政策会合'],
        'tags': ['cat/fx', 'co/日銀', 'country/日本', 'country/米国', 'person/植田和男', 'ticker/USDJPY', 'topic/利上げ', 'topic/金融政策', 'industry/金融', 'event/政策会合', 'score/高']
    },
    {
        'date': date, 'seen_at': seen_at, 'genre': 'FX',
        'title': '介入効果の半減と再警戒 — USD/JPY 158円台回帰でMoF再出動を市場が催促',
        'url': 'https://www.cnbc.com/2026/05/07/japan-yen-intervention-boj-rate-gap-currency-pressure.html',
        'url_norm': 'cnbc.com/2026/05/07/japan-yen-intervention-boj-rate-gap-currency-pressure',
        'source': 'CNBC', 'summary': '介入効果が半減しUSD/JPY158円台に回帰。$637億の円買いの揺り戻し。159-161円圏で再介入観測。', 'thumb': None,
        'entities': {'companies': [], 'countries': ['日本', '米国'], 'services': [], 'people': ['スコット・ベッセント'], 'tickers': ['USDJPY']},
        'topics': ['為替介入', '円安', '金利差'], 'industries': ['金融'], 'events': [],
        'tags': ['cat/fx', 'country/日本', 'country/米国', 'person/スコット・ベッセント', 'ticker/USDJPY', 'topic/為替介入', 'topic/円安', 'industry/金融', 'score/高']
    },
    {
        'date': date, 'seen_at': seen_at, 'genre': 'FX',
        'title': 'ECBの6月利上げ観測に暗雲 — インフレ鈍化でEUR/USD 1.1733高止まり',
        'url': 'https://www.bloomberg.com/news/articles/2026-05-14/why-the-ecb-s-june-interest-rate-hike-is-becoming-less-certain',
        'url_norm': 'bloomberg.com/news/articles/2026-05-14/why-the-ecb-s-june-interest-rate-hike-is-becoming-less-certain',
        'source': 'Bloomberg', 'summary': 'ECB6月利上げ確率が後退。エネルギー安定とユーロ圏停滞で当局が慎重姿勢。EUR/USD 1.1733。', 'thumb': None,
        'entities': {'companies': [], 'countries': ['EU', '米国'], 'services': [], 'people': [], 'tickers': ['EURUSD', 'EURJPY']},
        'topics': ['ECB', '利上げ', 'インフレ'], 'industries': ['金融'], 'events': ['政策会合'],
        'tags': ['cat/fx', 'country/EU', 'country/米国', 'ticker/EURUSD', 'topic/ECB', 'topic/利上げ', 'event/政策会合', 'score/高']
    },
    {
        'date': date, 'seen_at': seen_at, 'genre': 'FX',
        'title': '週明け為替展望 — ドル全面高のなか158-160円攻防、Google I/O週に突入',
        'url': 'https://www.fxstreet.com/analysis/weekly-forex-forecast-eur-usd-xau-usd-gbp-usd-usd-jpy-bitcoin-and-more-video-202605111410',
        'url_norm': 'fxstreet.com/analysis/weekly-forex-forecast-eur-usd-xau-usd-gbp-usd-usd-jpy-bitcoin-and-more-video-202605111410',
        'source': 'FXStreet', 'summary': 'USD/JPY158-160円攻防。週明けGoogle I/O開幕で指標ラッシュ。GBP/USD1.35維持。クロス円全面高圧力。', 'thumb': None,
        'entities': {'companies': [], 'countries': ['日本', '米国', 'EU', '英国'], 'services': [], 'people': [], 'tickers': ['USDJPY', 'EURUSD', 'GBPUSD', 'EURJPY']},
        'topics': ['円安', 'ドル高', '週間予想'], 'industries': ['金融'], 'events': [],
        'tags': ['cat/fx', 'country/EU', 'country/英国', 'country/日本', 'country/米国', 'ticker/USDJPY', 'ticker/EURUSD', 'topic/円安', 'topic/ドル高', 'score/中']
    },
    {
        'date': date, 'seen_at': seen_at, 'genre': 'FX',
        'title': 'AUD/USD底堅さの背景 — RBA利下げ観測後退とコモディティ需要で下支え',
        'url': 'https://www.nbc.ca/content/dam/bnc/taux-analyses/analyse-eco/mensuel/forex.pdf',
        'url_norm': 'nbc.ca/content/dam/bnc/taux-analyses/analyse-eco/mensuel/forex.pdf',
        'source': 'NBC Economics', 'summary': 'AUD/USDがドル高でも底堅い。RBA利下げ観測後退と鉄鉱石・銅のコモディティ下支え。', 'thumb': None,
        'entities': {'companies': [], 'countries': ['豪州', '米国', '中国'], 'services': [], 'people': [], 'tickers': ['AUDUSD']},
        'topics': ['金融政策', 'コモディティ', 'ドル高'], 'industries': ['金融'], 'events': [],
        'tags': ['cat/fx', 'country/中国', 'country/豪州', 'country/米国', 'ticker/AUDUSD', 'topic/金融政策', 'topic/コモディティ', 'score/中']
    },
    # AI
    {
        'date': date, 'seen_at': seen_at, 'genre': 'AI',
        'title': 'Anthropic×ゲイツ財団2億ドル提携 — 医療・教育・農業でClaudeが公共財に',
        'url': 'https://www.anthropic.com/news/gates-foundation-partnership',
        'url_norm': 'anthropic.com/news/gates-foundation-partnership',
        'source': 'Anthropic News', 'summary': 'Anthropic×ゲイツ財団が$200M提携。医療・教育・農業の3領域でClaudeを公共財化。アフリカ語データセット公開予定。', 'thumb': None,
        'entities': {'companies': ['Anthropic', 'ゲイツ財団'], 'countries': ['米国'], 'services': ['Claude'], 'people': ['ビル・ゲイツ'], 'tickers': []},
        'topics': ['AI投資', 'グローバルヘルス', 'AI社会実装'], 'industries': ['AI', '医療'], 'events': ['提携発表'],
        'tags': ['cat/ai', 'co/Anthropic', 'co/ゲイツ財団', 'country/米国', 'person/ビル・ゲイツ', 'topic/AI投資', 'event/提携発表', 'score/高']
    },
    {
        'date': date, 'seen_at': seen_at, 'genre': 'AI',
        'title': 'OpenAI、ChatGPT×Codex×Atlasを統合スーパーアプリ化 — IPO前夜の組織再編',
        'url': 'https://www.techtimes.com/articles/316730/20260516/openai-unifies-chatgpt-codex-developer-api-under-co-founder-brockman-four-days-before-google-i-o.htm',
        'url_norm': 'techtimes.com/articles/316730/20260516/openai-unifies-chatgpt-codex-developer-api-under-co-founder-brockman-four-days-before-google-i-o',
        'source': 'TechTimes', 'summary': 'OpenAIがChatGPT・Codex・APIを統合しスーパーアプリ化。ブロックマン復帰。Codex年収$10億超。IPO評価額$8520億。', 'thumb': None,
        'entities': {'companies': ['OpenAI'], 'countries': ['米国'], 'services': ['ChatGPT', 'Codex', 'Atlas'], 'people': ['グレッグ・ブロックマン'], 'tickers': []},
        'topics': ['AIエージェント', 'プロダクト統合', 'IPO'], 'industries': ['AI'], 'events': ['製品発表'],
        'tags': ['cat/ai', 'co/OpenAI', 'country/米国', 'person/グレッグ・ブロックマン', 'topic/AIエージェント', 'event/製品発表', 'score/高']
    },
    {
        'date': date, 'seen_at': seen_at, 'genre': 'AI',
        'title': 'Google I/O 2026前夜 — Gemini Spark・Android 17・XRグラスが明日解禁',
        'url': 'https://www.androidauthority.com/what-to-expect-from-google-io-2026-3664979/',
        'url_norm': 'androidauthority.com/what-to-expect-from-google-io-2026-3664979',
        'source': 'Android Authority', 'summary': 'Google I/O 2026が5/19開幕。Gemini Spark自律エージェント・Android 17・XRグラス発表予定。DeepMindがコーディング強化。', 'thumb': None,
        'entities': {'companies': ['Google', 'Google DeepMind'], 'countries': ['米国'], 'services': ['Gemini', 'Android', 'Gemini-Spark'], 'people': [], 'tickers': ['GOOGL']},
        'topics': ['AIエージェント', 'マルチモーダル', '製品発表'], 'industries': ['AI'], 'events': ['製品発表'],
        'tags': ['cat/ai', 'co/Google', 'co/Google-DeepMind', 'country/米国', 'topic/AIエージェント', 'event/製品発表', 'score/高']
    },
    {
        'date': date, 'seen_at': seen_at, 'genre': 'AI',
        'title': 'OpenAI年間収益250億ドル突破・IPO本格化 — 評価額8,520億ドルでQ4上場へ',
        'url': 'https://www.tradingkey.com/analysis/stocks/us-stocks/261902715-openai-ipo-chatgpt-codex-api-ai-agent-brockman-tradingkey',
        'url_norm': 'tradingkey.com/analysis/stocks/us-stocks/261902715-openai-ipo-chatgpt-codex-api-ai-agent-brockman-tradingkey',
        'source': 'TradingKey', 'summary': 'OpenAI年収$250億超を達成。2024年$37億から7倍。Q4 2026年IPO目標、評価額$8520億。GPT-5.5が主要ドライバー。', 'thumb': None,
        'entities': {'companies': ['OpenAI', 'Anthropic'], 'countries': ['米国'], 'services': ['ChatGPT', 'GPT-5_5'], 'people': [], 'tickers': []},
        'topics': ['IPO', 'AI投資'], 'industries': ['AI'], 'events': ['IPO'],
        'tags': ['cat/ai', 'co/Anthropic', 'co/OpenAI', 'country/米国', 'topic/IPO', 'event/IPO', 'score/高']
    },
    {
        'date': date, 'seen_at': seen_at, 'genre': 'AI',
        'title': 'Z.ai、開発現場向けLLM「GLM-4.7」をオープンソース公開 — Claude対抗で中国勢が巻き返し',
        'url': 'https://www.fox21online.com/i/z-ai%E3%80%81%E7%8F%BE%E5%A0%B4%E3%81%A7%E3%81%AE%E9%96%8B%E7%99%BA%E5%90%91%E3%81%91%E3%81%AB%E8%A8%AD%E8%A8%88%E3%81%95%E3%82%8C%E3%81%9F%E6%96%B0%E4%B8%96%E4%BB%A3%E3%81%AE%E5%A4%A7%E8%A6%8F/',
        'url_norm': 'fox21online.com/i/z-ai-llm-glm-4-7',
        'source': 'Fox21Online', 'summary': 'Z.aiがGLM-4.7をOSS公開。開発現場向けLLM、ツール呼び出し・長文コンテキスト特化。中国勢がClaude Code/Codexに対抗。', 'thumb': None,
        'entities': {'companies': ['Z.ai'], 'countries': ['中国', '米国'], 'services': ['GLM-4_7'], 'people': [], 'tickers': []},
        'topics': ['LLM', 'オープンソース', 'AI安全'], 'industries': ['AI'], 'events': ['製品発表'],
        'tags': ['cat/ai', 'co/Z_ai', 'country/中国', 'country/米国', 'topic/LLM', 'event/製品発表', 'score/中']
    },
    # IT-Consulting
    {
        'date': date, 'seen_at': seen_at, 'genre': 'IT-Consulting',
        'title': 'Deloitte、6月から181,500人の職名を全廃 — AIが削ったピラミッドに「リーダー職」を新設',
        'url': 'https://fortune.com/2026/01/22/deloitte-job-title-change-ai-reshapes-big-4-accounting-consulting-firms/',
        'url_norm': 'fortune.com/2026/01/22/deloitte-job-title-change-ai-reshapes-big-4-accounting-consulting-firms',
        'source': 'Fortune', 'summary': 'Deloitteが6/1から米国181,500人の職名廃止。AIが中間業務を自動化しピラミッド階層が崩壊。職能ファミリー体系に移行。', 'thumb': None,
        'entities': {'companies': ['デロイト'], 'countries': ['米国'], 'services': [], 'people': [], 'tickers': []},
        'topics': ['組織改革', 'AI変革', '人材'], 'industries': ['IT-コンサル'], 'events': [],
        'tags': ['cat/it', 'co/デロイト', 'country/米国', 'topic/組織改革', 'topic/AI変革', 'score/高']
    },
    {
        'date': date, 'seen_at': seen_at, 'genre': 'IT-Consulting',
        'title': 'BCG AI収益が3.6億ドルに — ビッグ3戦略ファームで初開示、成果連動型が主流へ',
        'url': 'https://futureofconsulting.ai/ai-leadership/2026-consultings-ai-revolution-update/',
        'url_norm': 'futureofconsulting.ai/ai-leadership/2026-consultings-ai-revolution-update',
        'source': 'Future of Consulting AI', 'summary': 'BCGが2025年収益$144億の25%=$36億がAI案件と開示。ビッグ3初の数値化。Big4/3合計AI投資$100億超。', 'thumb': None,
        'entities': {'companies': ['BCG', 'マッキンゼー', 'PwC', 'KPMG'], 'countries': ['米国'], 'services': [], 'people': [], 'tickers': []},
        'topics': ['AI投資', '成果連動', '収益モデル'], 'industries': ['IT-コンサル'], 'events': [],
        'tags': ['cat/it', 'co/BCG', 'co/KPMG', 'co/PwC', 'co/マッキンゼー', 'country/米国', 'topic/AI投資', 'score/高']
    },
    {
        'date': date, 'seen_at': seen_at, 'genre': 'IT-Consulting',
        'title': 'アクセンチュア×Databricks、AIエージェント大規模展開を加速 — 専用BG立ち上げ',
        'url': 'https://newsroom.accenture.jp/jp/news/2026/accenture-and-databricks-accelerate-enterprise-adoption-of-ai-applications-and-agents-at-scale',
        'url_norm': 'newsroom.accenture.jp/jp/news/2026/accenture-and-databricks-accelerate-enterprise-adoption-of-ai-applications-and-agents-at-scale',
        'source': 'Accenture Newsroom', 'summary': 'アクセンチュア×DatabricksがBG設立。製造・金融・流通でAIエージェント本番稼働を半年以内に実現する支援プログラム。', 'thumb': None,
        'entities': {'companies': ['アクセンチュア', 'Databricks'], 'countries': ['米国', '日本'], 'services': [], 'people': [], 'tickers': []},
        'topics': ['DX', 'AIエージェント', '提携'], 'industries': ['IT-コンサル', 'AI'], 'events': ['提携発表'],
        'tags': ['cat/it', 'co/Databricks', 'co/アクセンチュア', 'country/日本', 'country/米国', 'topic/DX', 'event/提携発表', 'score/高']
    },
    {
        'date': date, 'seen_at': seen_at, 'genre': 'IT-Consulting',
        'title': 'コンサル二極化が鮮明に — AI対応加速の大手とパイロット止まりの中小で業界二分',
        'url': 'https://performbyai.com/articles/ai-splitting-consulting-industry-gulf',
        'url_norm': 'performbyai.com/articles/ai-splitting-consulting-industry-gulf',
        'source': 'Perform by AI', 'summary': 'Big4/3とその他の中小コンサルで二極化鮮明。中小の約半数がPOC段階から抜け出せず。地域分断とAI二極化が重なる。', 'thumb': None,
        'entities': {'companies': [], 'countries': ['米国', 'EU'], 'services': [], 'people': [], 'tickers': []},
        'topics': ['DX', 'AI変革', '二極化'], 'industries': ['IT-コンサル'], 'events': [],
        'tags': ['cat/it', 'country/EU', 'country/米国', 'topic/DX', 'topic/AI変革', 'score/中']
    },
    {
        'date': date, 'seen_at': seen_at, 'genre': 'IT-Consulting',
        'title': 'PwC「Human+AI Skillset」30スキル — 20万人ChatPwCユーザーに協働を定着',
        'url': 'https://www.roadtooffer.com/blog/big-4-consulting-firms',
        'url_norm': 'roadtooffer.com/blog/big-4-consulting-firms',
        'source': 'PwC / Road to Offer', 'summary': 'PwCが「Human+AI Skillset」30スキルカリキュラムを開始。AI系15・人間系15を全職員に展開。ChatPwC20万人ユーザー活用。', 'thumb': None,
        'entities': {'companies': ['PwC'], 'countries': ['米国'], 'services': ['ChatPwC'], 'people': [], 'tickers': []},
        'topics': ['AI変革', '人材育成', 'DX'], 'industries': ['IT-コンサル'], 'events': [],
        'tags': ['cat/it', 'co/PwC', 'country/米国', 'topic/AI変革', 'topic/人材育成', 'score/中']
    },
    # Game
    {
        'date': date, 'seen_at': seen_at, 'genre': 'Game',
        'title': 'スクウェア・エニックスがSwitch2マルチ戦略を強化 — CEO「特にSwitch2に注力」と明言',
        'url': 'https://www.nintendolife.com/news/2026/05/square-enix-wants-to-further-promote-its-multi-platform-strategy-especially-on-switch-2',
        'url_norm': 'nintendolife.com/news/2026/05/square-enix-wants-to-further-promote-its-multi-platform-strategy-especially-on-switch-2',
        'source': 'Nintendo Life', 'summary': 'スクウェア・エニックスCEOがSwitch2を最重点プラットフォームと明言。2026年通年50本超のタイトル展開見込み。', 'thumb': None,
        'entities': {'companies': ['SQUARE ENIX', '任天堂'], 'countries': ['日本'], 'services': ['Switch-2'], 'people': [], 'tickers': ['9684', '7974']},
        'topics': ['マルチプラット', 'ゲーム戦略', '新作発売'], 'industries': ['ゲーム'], 'events': [],
        'tags': ['cat/game', 'co/SQUARE-ENIX', 'co/任天堂', 'country/日本', 'topic/マルチプラット', 'score/高']
    },
    {
        'date': date, 'seen_at': seen_at, 'genre': 'Game',
        'title': '任天堂、Switch2向け5月大型タイトル群の発売窓を再確認 — 11本が集中投下',
        'url': 'https://www.nintendolife.com/news/2026/05/nintendo-reconfirms-release-windows-for-major-upcoming-switch-2-games',
        'url_norm': 'nintendolife.com/news/2026/05/nintendo-reconfirms-release-windows-for-major-upcoming-switch-2-games',
        'source': 'Nintendo Life', 'summary': '任天堂がSwitch2の5〜6月スケジュールを確認。11本が集中投下。ハード販売台数5月末700万台超のペース。', 'thumb': None,
        'entities': {'companies': ['任天堂'], 'countries': ['日本'], 'services': ['Switch-2'], 'people': [], 'tickers': ['7974']},
        'topics': ['新作発売', 'ゲーム戦略'], 'industries': ['ゲーム'], 'events': ['製品発表'],
        'tags': ['cat/game', 'co/任天堂', 'country/日本', 'topic/新作発売', 'event/製品発表', 'score/高']
    },
    {
        'date': date, 'seen_at': seen_at, 'genre': 'Game',
        'title': 'インディ・ジョーンズSwitch2版が好調 — XboxのAAA移植でサードパーティ参入加速',
        'url': 'https://gamerant.com/may-2026-big-month-for-nintendo-switch-2-future/',
        'url_norm': 'gamerant.com/may-2026-big-month-for-nintendo-switch-2-future',
        'source': 'Game Rant', 'summary': 'インディ・ジョーンズSwitch2版(5/12)が好調。MicrosoftのAAA移植でサードパーティ参入加速。プラットフォーム非依存化が加速。', 'thumb': None,
        'entities': {'companies': ['任天堂', 'Microsoft'], 'countries': ['日本', '米国'], 'services': ['Switch-2'], 'people': [], 'tickers': ['7974', 'MSFT']},
        'topics': ['マルチプラット', '新作発売'], 'industries': ['ゲーム'], 'events': ['製品発表'],
        'tags': ['cat/game', 'co/Microsoft', 'co/任天堂', 'country/日本', 'country/米国', 'topic/マルチプラット', 'score/高']
    },
    {
        'date': date, 'seen_at': seen_at, 'genre': 'Game',
        'title': 'Tales of Arise Switch2版5月22日発売 — Beyond the Dawn EditionでRPGライン充実',
        'url': 'https://www.nintendolife.com/guides/upcoming-nintendo-switch-2-games-and-accessories-for-may-and-june-2026',
        'url_norm': 'nintendolife.com/guides/upcoming-nintendo-switch-2-games-and-accessories-for-may-and-june-2026',
        'source': 'Nintendo Life', 'summary': 'バンダイナムコ「Tales of Arise: Beyond the Dawn Edition」がSwitch2向け5/22発売。2021年発売の遅延移植需要が存在。', 'thumb': None,
        'entities': {'companies': ['バンダイナムコ', '任天堂'], 'countries': ['日本'], 'services': ['Switch-2'], 'people': [], 'tickers': ['7974']},
        'topics': ['新作発売', 'RPG'], 'industries': ['ゲーム'], 'events': ['製品発表'],
        'tags': ['cat/game', 'co/バンダイナムコ', 'co/任天堂', 'country/日本', 'topic/新作発売', 'event/製品発表', 'score/中']
    },
    {
        'date': date, 'seen_at': seen_at, 'genre': 'Game',
        'title': 'ヨッシーと不思議な本、Switch2で5月21日発売 — ファースト新作でファミリー層確保',
        'url': 'https://gamerant.com/nintendo-switch-2-new-games-coming-out-soon-list-may-2026/',
        'url_norm': 'gamerant.com/nintendo-switch-2-new-games-coming-out-soon-list-may-2026',
        'source': 'Game Rant', 'summary': '任天堂ファーストタイトル「ヨッシーと不思議な本」がSwitch2で5/21発売。ファミリー・低年齢層へのリーチを強化。', 'thumb': None,
        'entities': {'companies': ['任天堂'], 'countries': ['日本'], 'services': ['Switch-2'], 'people': [], 'tickers': ['7974']},
        'topics': ['新作発売', 'ゲーム戦略'], 'industries': ['ゲーム'], 'events': ['製品発表'],
        'tags': ['cat/game', 'co/任天堂', 'country/日本', 'topic/新作発売', 'event/製品発表', 'score/中']
    },
]

with open('data/articles.jsonl', 'a', encoding='utf-8') as f:
    for a in articles:
        f.write(json.dumps(a, ensure_ascii=False) + '\n')

print(f'追記完了: {len(articles)}件')
