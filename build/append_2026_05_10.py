"""2026-05-10 articles.jsonl 追記スクリプト"""
import json

seen_at = "2026-05-10T06:00:00+09:00"
date = "2026-05-10"

new_entries = [
    # FX
    {
        "date": date, "seen_at": seen_at, "genre": "FX",
        "title": "5月GW連休中も為替介入、4〜5兆円規模か — 相場反転効果は限定的",
        "url": "https://www.nikkei.com/article/DGXZQOUB070R90X00C26A5000000/",
        "url_norm": "nikkei.com/article/DGXZQOUB070R90X00C26A5000000",
        "source": "日本経済新聞",
        "summary": "GW連休中の政府・日銀による4〜5兆円規模の円買い介入が複数回実施。一時155円台まで急騰したが即日に157円台へ逆戻りし、介入効果の持続性が問われる。158円以上で再介入リスク警戒。",
        "entities": {"companies": [], "countries": ["日本", "米国"], "services": [], "people": ["植田和男"], "tickers": ["USDJPY"]},
        "topics": ["為替介入", "円安"], "industries": ["金融"], "events": ["為替介入"],
        "tags": ["cat/fx", "country/日本", "country/米国", "event/為替介入", "person/植田和男", "ticker/USDJPY", "topic/円安", "topic/為替介入", "score/高"]
    },
    {
        "date": date, "seen_at": seen_at, "genre": "FX",
        "title": "連休中に為替が円高に振れる — 今後も予想される為替介入",
        "url": "https://www.nri.com/jp/media/column/kiuchi/20260507.html",
        "url_norm": "nri.com/jp/media/column/kiuchi/20260507",
        "source": "NRI研究員の時事解説",
        "summary": "NRI木内英生氏が介入の「時間稼ぎ」的性格を分析。日米金利差が縮まらない限り為替介入は繰り返す。日銀の段階的利上げ加速が根本的解決策と指摘。",
        "entities": {"companies": ["NRI"], "countries": ["日本"], "services": [], "people": ["木内英生"], "tickers": ["USDJPY"]},
        "topics": ["為替介入", "金利差"], "industries": ["金融"], "events": [],
        "tags": ["cat/fx", "co/NRI", "country/日本", "person/木内英生", "ticker/USDJPY", "topic/為替介入", "topic/金利差", "score/高"]
    },
    {
        "date": date, "seen_at": seen_at, "genre": "FX",
        "title": "2026年末の米ドル円見通しを152.5円に引き上げ — 中東情勢で強まる米ドル高圧力",
        "url": "https://www.nomura.co.jp/wealthstyle/article/0676/",
        "url_norm": "nomura.co.jp/wealthstyle/article/0676",
        "source": "NOMURA ウェルスタイル",
        "summary": "野村証券が年末ドル円見通しを150円から152.5円に上方修正。中東情勢緩和がパラドックス的にドル高を促す。FRB新議長ウォーシュ就任で日米金利差縮小シナリオが後退。",
        "entities": {"companies": ["野村証券"], "countries": ["日本", "米国"], "services": [], "people": ["後藤祐二朗"], "tickers": ["USDJPY", "EURUSD"]},
        "topics": ["金利差", "円安"], "industries": ["金融"], "events": [],
        "tags": ["cat/fx", "co/野村証券", "country/日本", "country/米国", "person/後藤祐二朗", "ticker/USDJPY", "topic/円安", "topic/金利差", "score/高"]
    },
    {
        "date": date, "seen_at": seen_at, "genre": "FX",
        "title": "為替介入「厳しい副作用がドル円に」— GW後の157円回帰と介入の限界",
        "url": "https://www.gaitame.com/media/entry/2026/05/05/200000",
        "url_norm": "gaitame.com/media/entry/2026/05/05/200000",
        "source": "外為どっとコム マネ育チャンネル",
        "summary": "外為どっとコムが介入の「速効性あり・持続性なし」パターンを分析。ヘッジファンドが介入後の戻り売りを戦略化。次の節目は5/12米CPI・6月日銀会合。",
        "entities": {"companies": [], "countries": ["日本", "米国"], "services": [], "people": [], "tickers": ["USDJPY"]},
        "topics": ["為替介入"], "industries": ["金融"], "events": [],
        "tags": ["cat/fx", "country/日本", "country/米国", "ticker/USDJPY", "topic/為替介入", "score/中"]
    },
    {
        "date": date, "seen_at": seen_at, "genre": "FX",
        "title": "為替介入、標的を絞った対応の可能性との指摘も — NY外為市場分析",
        "url": "https://fx.minkabu.jp/news/366374",
        "url_norm": "fx.minkabu.jp/news/366374",
        "source": "みんかぶ FX",
        "summary": "NY外為市場で財務省が158〜160円接近時のみ介入する「バンド管理的アプローチ」に転換した可能性。CFTC報告で投機的円売りが45%急減し大口が介入リスクを警戒。",
        "entities": {"companies": [], "countries": ["日本", "米国"], "services": [], "people": [], "tickers": ["USDJPY"]},
        "topics": ["為替介入"], "industries": ["金融"], "events": [],
        "tags": ["cat/fx", "country/日本", "country/米国", "ticker/USDJPY", "topic/為替介入", "score/中"]
    },
    # AI
    {
        "date": date, "seen_at": seen_at, "genre": "AI",
        "title": "Google DeepMind UK staff 98% vote to unionize over Pentagon AI contract",
        "url": "https://fortune.com/2026/05/05/google-deepmind-unionize-vote-military-ai-contracts-internal-backlash-pentagon-deal-israeli-defense-forces/",
        "url_norm": "fortune.com/2026/05/05/google-deepmind-unionize-vote-military-ai-contracts-internal-backlash-pentagon-deal-israeli-defense-forces",
        "source": "Fortune",
        "summary": "英DeepMind職員が98%賛成でCWU加入を可決。ペンタゴン機密AI契約（Gemini「あらゆる合法目的」使用）への反発が直接の引き金。フロンティアAIラボ初の組合として歴史的転換点。",
        "entities": {"companies": ["Google", "DeepMind"], "countries": ["英国", "米国"], "services": ["Gemini"], "people": [], "tickers": ["GOOGL"]},
        "topics": ["AI規制", "AIと軍事"], "industries": ["AI"], "events": ["組合設立"],
        "tags": ["cat/ai", "co/Google", "country/英国", "country/米国", "svc/Gemini", "ticker/GOOGL", "topic/AI規制", "event/組合設立", "score/高"]
    },
    {
        "date": date, "seen_at": seen_at, "genre": "AI",
        "title": "OpenAI releases GPT-5.5 Instant, a new default model for ChatGPT",
        "url": "https://techcrunch.com/2026/05/05/openai-releases-gpt-5-5-instant-a-new-default-model-for-chatgpt/",
        "url_norm": "techcrunch.com/2026/05/05/openai-releases-gpt-5-5-instant-a-new-default-model-for-chatgpt",
        "source": "TechCrunch",
        "summary": "OpenAIがGPT-5.5 InstantをChatGPT新デフォルトモデルに。レイテンシ40%削減・API50%コスト削減。AWS Bedrockにも上陸し主要クラウド三社展開完了でエンタープライズ囲い込みが加速。",
        "entities": {"companies": ["OpenAI"], "countries": ["米国"], "services": ["GPT-5_5-Instant", "ChatGPT"], "people": [], "tickers": []},
        "topics": ["LLM", "AIエージェント"], "industries": ["AI"], "events": ["製品発表"],
        "tags": ["cat/ai", "co/OpenAI", "country/米国", "svc/GPT-5_5-Instant", "topic/LLM", "event/製品発表", "score/高"]
    },
    {
        "date": date, "seen_at": seen_at, "genre": "AI",
        "title": "Project Glasswing: Claude Mythos PreviewでAIサイバー防衛の新標準を確立",
        "url": "https://www.anthropic.com/glasswing",
        "url_norm": "anthropic.com/glasswing",
        "source": "Anthropic",
        "summary": "AnthropicがProject Glasswingを正式発表。Mythos PreviewはCVSSスコア9.0超のRCE脆弱性を自律発見。約50の大企業・銀行に限定公開。攻撃・防御の非対称性問題から一般公開は見送り。",
        "entities": {"companies": ["Anthropic"], "countries": ["米国"], "services": ["Claude-Mythos-Preview"], "people": [], "tickers": []},
        "topics": ["AIセキュリティ", "AI安全性"], "industries": ["AI"], "events": ["製品発表"],
        "tags": ["cat/ai", "co/Anthropic", "country/米国", "svc/Claude-Mythos-Preview", "topic/AIセキュリティ", "event/製品発表", "score/高"]
    },
    {
        "date": date, "seen_at": seen_at, "genre": "AI",
        "title": "State of AI: May 2026 — Anthropic・OpenAI二強が支配する構図と次の断層線",
        "url": "https://press.airstreet.com/p/state-of-ai-may-2026",
        "url_norm": "press.airstreet.com/p/state-of-ai-may-2026",
        "source": "Air Street Press",
        "summary": "2026年5月のAI市場概観。Anthropic・OpenAIが基盤モデルとエンタープライズ契約の両軸で支配的地位。AIエージェント企業利用率が初めて17.8%を突破し業務組み込み段階へ移行。",
        "entities": {"companies": ["Anthropic", "OpenAI", "Google", "Meta"], "countries": ["米国"], "services": [], "people": [], "tickers": []},
        "topics": ["AI市場", "AIエージェント"], "industries": ["AI"], "events": [],
        "tags": ["cat/ai", "co/Anthropic", "co/OpenAI", "co/Google", "country/米国", "topic/AI市場", "score/中"]
    },
    {
        "date": date, "seen_at": seen_at, "genre": "AI",
        "title": "Anthropic just had AIs biggest week of 2026 — Q1 ARR $44B・前年比80倍の衝撃",
        "url": "https://aiweekly.co/issues/anthropic-just-had-ais-biggest-week-of-2026",
        "url_norm": "aiweekly.co/issues/anthropic-just-had-ais-biggest-week-of-2026",
        "source": "AI Weekly",
        "summary": "Anthropicの2026年Q1 ARRが44億ドルを突破し前年比80倍。Goldman Sachs・Visaなどウォール街のエンタープライズ導入が成長ドライバー。Pentagon契約拒絶で軍事AI路線の差別化が明確化。",
        "entities": {"companies": ["Anthropic"], "countries": ["米国"], "services": ["Claude"], "people": ["ダリオ・アモデイ"], "tickers": []},
        "topics": ["AI市場", "AIエージェント"], "industries": ["AI"], "events": [],
        "tags": ["cat/ai", "co/Anthropic", "country/米国", "svc/Claude", "person/ダリオ・アモデイ", "topic/AI市場", "score/中"]
    },
    # IT-Consulting
    {
        "date": date, "seen_at": seen_at, "genre": "IT-Consulting",
        "title": "NTTデータグループ、AIを中核とした成長戦略をグローバルで加速",
        "url": "https://www.nttdata.com/global/ja/news/release/2026/050806/",
        "url_norm": "nttdata.com/global/ja/news/release/2026/050806",
        "source": "NTTデータグループ",
        "summary": "NTTデータグループが「コンサルティング×AI」を中核に事業構造を転換。コンサルティングセグメント新設・AI事業本部集約・2027年度AIエージェント関連ビジネス3,000億円目標を発表。",
        "entities": {"companies": ["NTTデータ"], "countries": ["日本"], "services": ["NTT-DATA-AIVista"], "people": [], "tickers": ["9613"]},
        "topics": ["AI導入", "DX"], "industries": ["IT-コンサル"], "events": ["組織改編"],
        "tags": ["cat/it", "co/NTTデータ", "country/日本", "ticker/9613", "topic/AI導入", "topic/DX", "event/組織改編", "score/高"]
    },
    {
        "date": date, "seen_at": seen_at, "genre": "IT-Consulting",
        "title": "NTTデータ経営研究所、金融機関向けAI導入コンサルティングサービスを開始",
        "url": "https://www.nttdata-strategy.com/newsrelease/260507/",
        "url_norm": "nttdata-strategy.com/newsrelease/260507",
        "source": "NTTデータ経営研究所",
        "summary": "NTTデータ経営研究所が金融機関向け生成AI活用コンサルを5月7日より開始。リスク管理・コンプライアンス・サービス品質向上が三本柱。規制対応に精通した差別化ポジションで外資コンサルと競合。",
        "entities": {"companies": ["NTTデータ"], "countries": ["日本"], "services": [], "people": [], "tickers": []},
        "topics": ["AI導入", "DX"], "industries": ["IT-コンサル", "金融"], "events": ["製品発表"],
        "tags": ["cat/it", "co/NTTデータ", "country/日本", "topic/AI導入", "event/製品発表", "score/高"]
    },
    {
        "date": date, "seen_at": seen_at, "genre": "IT-Consulting",
        "title": "IT大手5社 2026年の事業展望 — AIネイティブSI転換の正念場",
        "url": "https://xtech.nikkei.com/atcl/nxt/column/18/03454/",
        "url_norm": "xtech.nikkei.com/atcl/nxt/column/18/03454",
        "source": "日経クロステック",
        "summary": "富士通・NEC・NTTデータ・日立・CTCのIT大手5社が御用聞きSIから提案型AIコンサルへの転換を最重要テーマに。AIエンジニア確保が最大課題。中間SIの存在意義が問われる構造的ジレンマ。",
        "entities": {"companies": ["富士通", "NEC", "NTTデータ", "日立"], "countries": ["日本"], "services": [], "people": [], "tickers": ["6702", "6701", "9613"]},
        "topics": ["DX", "AI導入"], "industries": ["IT-コンサル"], "events": [],
        "tags": ["cat/it", "co/NTTデータ", "co/富士通", "country/日本", "topic/DX", "topic/AI導入", "score/中"]
    },
    {
        "date": date, "seen_at": seen_at, "genre": "IT-Consulting",
        "title": "McKinsey Job Cuts Predicted for 2026, with AI Ambitions to Credit for the Change",
        "url": "https://www.thehrdigest.com/mckinsey-job-cuts-predicted-for-2026-with-ai-ambitions-to-credit-for-the-change/",
        "url_norm": "thehrdigest.com/mckinsey-job-cuts-predicted-for-2026-with-ai-ambitions-to-credit-for-the-change",
        "source": "The HR Digest",
        "summary": "McKinseyの2026年内の人員削減規模が数千人に上る可能性。AIアシスタントによる分析業務の自動化が主因。プロジェクトの40%がAI関連となり、コンサルタント自身がAIに代替されるリスクが現実化。",
        "entities": {"companies": ["マッキンゼー", "BCG"], "countries": ["米国"], "services": [], "people": [], "tickers": []},
        "topics": ["AI雇用", "DX"], "industries": ["IT-コンサル"], "events": [],
        "tags": ["cat/it", "co/マッキンゼー", "country/米国", "topic/AI雇用", "score/中"]
    },
    {
        "date": date, "seen_at": seen_at, "genre": "IT-Consulting",
        "title": "アクセンチュア株25%下落の深層 — 投資家が「AIを活用する企業」より「AIを作る企業」を選好",
        "url": "https://www.tikr.com/blog/accenture-is-down-25-as-investors-favor-faster-ai-winners-heres-where-the-stock-could-go-in-2026",
        "url_norm": "tikr.com/blog/accenture-is-down-25-as-investors-favor-faster-ai-winners-heres-where-the-stock-could-go-in-2026",
        "source": "TIKR.com",
        "summary": "アクセンチュア株が過去1年で25%下落。Q2 FY2026は売上$180億で予想超過も生成AI受注累計100億ドルに達しているが株価評価は低迷。AI ROI証明が株価回復の転換点になるとアナリストは見る。",
        "entities": {"companies": ["アクセンチュア"], "countries": ["米国"], "services": [], "people": [], "tickers": ["ACN"]},
        "topics": ["IT投資"], "industries": ["IT-コンサル"], "events": [],
        "tags": ["cat/it", "co/アクセンチュア", "country/米国", "ticker/ACN", "topic/IT投資", "score/中"]
    },
    # Game
    {
        "date": date, "seen_at": seen_at, "genre": "Game",
        "title": "Nintendo Switch 2ミリオンセラー詳報 — Pokemon Pokopia 400万本超、マリカワールド1,470万本",
        "url": "https://nintendoeverything.com/nintendo-switch-2-and-switch-million-sellers-for-may-2026-pokemon-pokopia-and-firered-leafgreen-over-4-million-more/",
        "url_norm": "nintendoeverything.com/nintendo-switch-2-and-switch-million-sellers-for-may-2026-pokemon-pokopia-and-firered-leafgreen-over-4-million-more",
        "source": "Nintendo Everything",
        "summary": "FY2026ミリオンセラーリスト開示。ポケモンポコピア400万本超、マリカワールド1470万本、スプラトゥーン4の340万本。ソフト附着率5.4本で据え置き機過去最高水準を達成。",
        "entities": {"companies": ["任天堂"], "countries": ["日本"], "services": ["Switch-2"], "people": [], "tickers": ["7974"]},
        "topics": ["Switch2", "決算"], "industries": ["ゲーム"], "events": ["決算"],
        "tags": ["cat/game", "co/任天堂", "country/日本", "svc/Switch-2", "ticker/7974", "topic/Switch2", "event/決算", "score/高"]
    },
    {
        "date": date, "seen_at": seen_at, "genre": "Game",
        "title": "任天堂、Switch 2主要タイトルのリリース窓を再確認 — スプラレイダース・ゼルダが2026年夏冬に",
        "url": "https://www.nintendolife.com/news/2026/05/nintendo-reconfirms-release-windows-for-major-upcoming-switch-2-games",
        "url_norm": "nintendolife.com/news/2026/05/nintendo-reconfirms-release-windows-for-major-upcoming-switch-2-games",
        "source": "Nintendo Life",
        "summary": "決算発表で2026〜2027年度の主要Switch 2タイトルのリリース窓を確認。スプラトゥーンレイダース（夏）・新ゼルダ（冬）・3Dマリオ（来春）の3本柱が計画通り。Indiana Jones 5月12日配信。",
        "entities": {"companies": ["任天堂"], "countries": ["日本"], "services": ["Switch-2"], "people": [], "tickers": ["7974"]},
        "topics": ["Switch2", "新作"], "industries": ["ゲーム"], "events": ["製品発表"],
        "tags": ["cat/game", "co/任天堂", "country/日本", "svc/Switch-2", "ticker/7974", "topic/Switch2", "event/製品発表", "score/高"]
    },
    {
        "date": date, "seen_at": seen_at, "genre": "Game",
        "title": "Cygames「リトル ノア 楽園の後継者」本日発売 — ウマ娘コラボDLC決定でIP展開加速",
        "url": "https://www.cygames.co.jp/news/id-21293/",
        "url_norm": "cygames.co.jp/news/id-21293",
        "source": "Cygames",
        "summary": "Cygamesがローグライトアクション「リトル ノア 楽園の後継者」をSwitch/PS4/Steamで本日リリース。ウマ娘コラボDLCも同時発表。モバイル発IPのコンシューマー展開の成功事例を狙う。",
        "entities": {"companies": ["Cygames"], "countries": ["日本"], "services": ["ウマ娘"], "people": [], "tickers": []},
        "topics": ["新作", "IPライセンス"], "industries": ["ゲーム"], "events": ["製品発表"],
        "tags": ["cat/game", "co/Cygames", "country/日本", "svc/ウマ娘", "topic/新作", "event/製品発表", "score/中"]
    },
    {
        "date": date, "seen_at": seen_at, "genre": "Game",
        "title": "Switch 2、2年目も初代を凌駕するペースで推移 — 値上げ後の下半期が試練",
        "url": "https://www.gamereactor.eu/the-nintendo-switch-2-continues-to-outpace-the-original-switch-in-terms-of-sales-1716143/",
        "url_norm": "gamereactor.eu/the-nintendo-switch-2-continues-to-outpace-the-original-switch-in-terms-of-sales-1716143",
        "source": "Gamereactor",
        "summary": "Switch 2がFY2026で1986万台を記録し初代の同期820万台を大幅超過。FY2027は1650万台予測と17%減が見込まれるが、値上げと減産調整は任天堂の伝統的成熟期戦略。",
        "entities": {"companies": ["任天堂"], "countries": ["日本"], "services": ["Switch-2"], "people": [], "tickers": ["7974"]},
        "topics": ["Switch2", "決算"], "industries": ["ゲーム"], "events": [],
        "tags": ["cat/game", "co/任天堂", "country/日本", "svc/Switch-2", "ticker/7974", "topic/Switch2", "score/中"]
    },
    {
        "date": date, "seen_at": seen_at, "genre": "Game",
        "title": "任天堂、Switch 2が20百万台に迫るも来期減益予測 — 値上げ戦略の真意",
        "url": "https://gameinformer.com/2026/05/08/as-nintendo-switch-2-nears-20-million-units-sold-the-company-expects-sales-to-decline",
        "url_norm": "gameinformer.com/2026/05/08/as-nintendo-switch-2-nears-20-million-units-sold-the-company-expects-sales-to-decline",
        "source": "Game Informer",
        "summary": "FY2027の連結売上2兆500億円（前期比11.4%減）の見通しを分析。Switch 2値上げが日欧米で段階的実施。ソフト6000万本×単価上昇でハード台数減少を補完する構造。",
        "entities": {"companies": ["任天堂", "コナミ"], "countries": ["日本"], "services": ["Switch-2"], "people": [], "tickers": ["7974"]},
        "topics": ["Switch2", "決算"], "industries": ["ゲーム"], "events": ["決算"],
        "tags": ["cat/game", "co/任天堂", "country/日本", "svc/Switch-2", "ticker/7974", "topic/Switch2", "event/決算", "score/低"]
    },
]

path = r"C:\Users\hidek\Obsidian\New's Grasp\News-Grasp\data\articles.jsonl"
with open(path, "a", encoding="utf-8") as f:
    for entry in new_entries:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

print(f"Added {len(new_entries)} entries to articles.jsonl")
