#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""2026-05-24 backfill: articles.jsonl へ 20 件追記"""
import json, pathlib

JSONL = pathlib.Path(r"C:\Users\hidek\Obsidian\New's Grasp\News-Grasp\data\articles.jsonl")

articles = [
    # ── FX ──────────────────────────────────────────────────────────────
    {
        "date": "2026-05-24", "seen_at": "2026-05-24T06:00:00+09:00",
        "category": "FX", "score": 91,
        "title": "来週5/25週のドル円——PCE・東京CPI・FOMC議事録が円高/円安の分水嶺",
        "url": "https://www.gaitame.com/media/entry/2026/05/24/120000_1",
        "url_norm": "gaitame.com/media/entry/2026/05/24/120000_1",
        "source": "外為どっとコム",
        "thumb": None,
        "entities": ["FRB", "MOF", "FOMC"],
        "topics": ["FOMC", "経済指標"],
        "industries": ["金融"],
        "events": ["政策会合"],
        "tags": ["cat/fx", "country/日本", "country/米国", "topic/FOMC", "event/政策会合", "score/高"]
    },
    {
        "date": "2026-05-24", "seen_at": "2026-05-24T06:00:00+09:00",
        "category": "FX", "score": 88,
        "title": "日本4月輸出14.8%増・貿易黒字3,019億円——半導体・自動車好調が円の上値サポートに",
        "url": "https://www.cnbc.com/2026/05/21/japan-exports-semiconductor-autos-imports-trade.html",
        "url_norm": "cnbc.com/2026/05/21/japan-exports-semiconductor-autos-imports-trade.html",
        "source": "CNBC",
        "thumb": "https://image.cnbcfm.com/api/v1/image/107137735-1666228139613-gettyimages-1235971665-JAPAN_TRADE.jpeg?v=1734478196&w=1920&h=1080",
        "entities": ["財務省"],
        "topics": ["経済指標"],
        "industries": ["製造業", "自動車"],
        "events": [],
        "tags": ["cat/fx", "country/日本", "country/米国", "topic/経済指標", "score/高"]
    },
    {
        "date": "2026-05-24", "seen_at": "2026-05-24T06:00:00+09:00",
        "category": "FX", "score": 82,
        "title": "G10 FX 5月展望——AUD/USDキャリー通貨に浮上、GBPは英国政治リスクで上値抑制",
        "url": "https://think.ing.com/articles/g10-fx-talking-may-2026/",
        "url_norm": "think.ing.com/articles/g10-fx-talking-may-2026",
        "source": "ING Think",
        "thumb": "https://think.ing.com/uploads/hero/_w800h450/shutterstock_editorial_15109405b_%281%29_1.jpg",
        "entities": ["RBA", "ECB", "ING"],
        "topics": ["金利差"],
        "industries": ["金融"],
        "events": [],
        "tags": ["cat/fx", "country/豪州", "country/英国", "country/EU", "topic/金利差", "score/高"]
    },
    {
        "date": "2026-05-24", "seen_at": "2026-05-24T06:00:00+09:00",
        "category": "FX", "score": 80,
        "title": "JPMorgan: ドル強含み継続——インフレ高止まりとWarsh議長タカ派でドル優位が続く理由",
        "url": "https://www.jpmorgan.com/insights/global-research/currencies/currency-volatility-dollar-strength",
        "url_norm": "jpmorgan.com/insights/global-research/currencies/currency-volatility-dollar-strength",
        "source": "JPMorgan",
        "thumb": "https://www.jpmorgan.com/content/dam/jpm/cib/complex/content/research/forex_volatility/Forex_Banner.jpg",
        "entities": ["JPMorgan", "FRB", "Warsh"],
        "topics": ["FRB", "金利差"],
        "industries": ["金融"],
        "events": [],
        "tags": ["cat/fx", "country/米国", "topic/FRB", "topic/金利差", "score/高"]
    },
    {
        "date": "2026-05-24", "seen_at": "2026-05-24T06:00:00+09:00",
        "category": "FX", "score": 75,
        "title": "ドル円週末158.88円クローズ——薄商いのメモリアルデー週へ、PCE/FOMC議事録が方向性を決する",
        "url": "https://www.fxstreet.com/currencies/usdjpy",
        "url_norm": "fxstreet.com/currencies/usdjpy",
        "source": "FXStreet",
        "thumb": None,
        "entities": ["FRB", "MOF"],
        "topics": ["FOMC"],
        "industries": ["金融"],
        "events": [],
        "tags": ["cat/fx", "country/米国", "country/日本", "topic/FOMC", "score/中"]
    },
    # ── AI ──────────────────────────────────────────────────────────────
    {
        "date": "2026-05-24", "seen_at": "2026-05-24T06:00:00+09:00",
        "category": "AI", "score": 93,
        "title": "GPT-5.5 Instant、ChatGPT新デフォルト——「規制分野の幻覚低減」を前面にOpenAIが企業市場攻勢",
        "url": "https://whatllm.org/blog/new-ai-models-may-2026",
        "url_norm": "whatllm.org/blog/new-ai-models-may-2026",
        "source": "WhatLLM",
        "thumb": "https://whatllm.org/opengraph-image",
        "entities": ["OpenAI", "McKinsey", "BCG", "Accenture"],
        "topics": ["生成AI"],
        "industries": ["IT"],
        "events": ["製品発表"],
        "tags": ["cat/ai", "co/OpenAI", "country/米国", "topic/生成AI", "event/製品発表", "score/高"]
    },
    {
        "date": "2026-05-24", "seen_at": "2026-05-24T06:00:00+09:00",
        "category": "AI", "score": 89,
        "title": "Gemini 3.5 Flash GA——$1.50/$9/1Mトークン・コーディングとエージェントで3.1 Proを超えるコスパ実現",
        "url": "https://llm-stats.com/llm-updates",
        "url_norm": "llm-stats.com/llm-updates",
        "source": "LLM Stats",
        "thumb": "https://llm-stats.com/og/main.png",
        "entities": ["Google", "Gemini"],
        "topics": ["生成AI"],
        "industries": ["IT"],
        "events": ["製品発表"],
        "tags": ["cat/ai", "co/Google", "country/米国", "topic/生成AI", "event/製品発表", "score/高"]
    },
    {
        "date": "2026-05-24", "seen_at": "2026-05-24T06:00:00+09:00",
        "category": "AI", "score": 87,
        "title": "Claude Security パブリックベータ——Project Glasswingで1万件超の脆弱性を発見した能力を企業に解放",
        "url": "https://www.anthropic.com/glasswing",
        "url_norm": "anthropic.com/glasswing",
        "source": "Anthropic",
        "thumb": None,
        "entities": ["Anthropic", "Claude"],
        "topics": ["サイバーセキュリティ"],
        "industries": ["IT", "セキュリティ"],
        "events": ["製品発表"],
        "tags": ["cat/ai", "co/Anthropic", "country/米国", "topic/サイバーセキュリティ", "event/製品発表", "score/高"]
    },
    {
        "date": "2026-05-24", "seen_at": "2026-05-24T06:00:00+09:00",
        "category": "AI", "score": 85,
        "title": "Anthropic、企業AI市場25%確保——OpenAIから顧客流入、軍事応用拒否姿勢が差別化の核心に",
        "url": "https://www.axios.com/2026/05/14/anthropic-claude-price-openai-tokens",
        "url_norm": "axios.com/2026/05/14/anthropic-claude-price-openai-tokens",
        "source": "Axios",
        "thumb": "https://images.axios.com/sxwE8M5Rti7vSpqmks3lifdeuRc=/0x0:1590x894/1366x768/2026/05/14/1778719461763.png",
        "entities": ["Anthropic", "OpenAI"],
        "topics": ["AI投資"],
        "industries": ["IT"],
        "events": [],
        "tags": ["cat/ai", "co/Anthropic", "co/OpenAI", "country/米国", "topic/AI投資", "score/高"]
    },
    {
        "date": "2026-05-24", "seen_at": "2026-05-24T06:00:00+09:00",
        "category": "AI", "score": 80,
        "title": "Claude次の戦場はモデルでなくエージェント制御プレーン——VentureBeatが読む企業AI覇権争いの新局面",
        "url": "https://venturebeat.com/orchestration/claudes-next-enterprise-battle-is-not-models-its-the-agent-control-plane",
        "url_norm": "venturebeat.com/orchestration/claudes-next-enterprise-battle-is-not-models-its-the-agent-control-plane",
        "source": "VentureBeat",
        "thumb": "https://images.ctfassets.net/jdtwqhzvc2n1/QIFFk030xew6nEvO7DFQB/2e91ff2cbababc24cd63134f601983a0/ChatGPT_Image_May_15__2026__09_09_07_AM.png?w=800&q=75",
        "entities": ["Anthropic", "AWS", "Microsoft"],
        "topics": ["AIエージェント"],
        "industries": ["IT"],
        "events": [],
        "tags": ["cat/ai", "co/Anthropic", "country/米国", "topic/AIエージェント", "score/中"]
    },
    # ── IT-Consulting ────────────────────────────────────────────────────
    {
        "date": "2026-05-24", "seen_at": "2026-05-24T06:00:00+09:00",
        "category": "IT-Consulting", "score": 93,
        "title": "McKinsey・PwC・EYが幹部秘書職を削減——AI自動化が白カラー支援業務を代替、業界の構造変化が加速",
        "url": "https://www.bloomberg.com/news/features/2026-05-21/mckinsey-pwc-and-ey-lay-off-executive-assistants-as-ai-accelerates",
        "url_norm": "bloomberg.com/news/features/2026-05-21/mckinsey-pwc-and-ey-lay-off-executive-assistants-as-ai-accelerates",
        "source": "Bloomberg",
        "thumb": None,
        "entities": ["McKinsey", "PwC", "EY", "KPMG"],
        "topics": ["DX"],
        "industries": ["コンサル"],
        "events": [],
        "tags": ["cat/it", "co/マッキンゼー", "co/PwC", "country/米国", "topic/DX", "score/高"]
    },
    {
        "date": "2026-05-24", "seen_at": "2026-05-24T06:00:00+09:00",
        "category": "IT-Consulting", "score": 88,
        "title": "NTT DATA、WinWire買収でエンタープライズAIをMicrosoft Azureで加速——全産業のエージェントAI採用を拡大",
        "url": "https://www.nttdata.com/global/en/news/press-release/2026/may/051800",
        "url_norm": "nttdata.com/global/en/news/press-release/2026/may/051800",
        "source": "NTT DATA Global",
        "thumb": "https://www.nttdata.com/global/en/-/media/assets/images/sns_share.png?rev=583d5951ace649c08be7a88679328a3b",
        "entities": ["NTT DATA", "WinWire", "Microsoft"],
        "topics": ["M&A"],
        "industries": ["IT", "コンサル"],
        "events": ["M&A"],
        "tags": ["cat/it", "co/NTTデータ", "country/米国", "country/日本", "topic/M&A", "event/M&A", "score/高"]
    },
    {
        "date": "2026-05-24", "seen_at": "2026-05-24T06:00:00+09:00",
        "category": "IT-Consulting", "score": 84,
        "title": "NTT DATAがグローバルデータセンター事業を再編——AIとクラウド需要急増でグローバル統括体制へ移行",
        "url": "https://services.global.ntt/en-us/newsroom/ntt-data-globalizes-sales-and-client-services-of-its-data-center-business",
        "url_norm": "services.global.ntt/en-us/newsroom/ntt-data-globalizes-sales-and-client-services-of-its-data-center-business",
        "source": "NTT Global",
        "thumb": "https://services.global.ntt/-/media/ntt/global/newsroom/ntt-blue-logo-2.jpg?rev=267dd8aab4f74063969e661cd164a44c",
        "entities": ["NTT DATA", "NTT", "AWS", "Azure", "Google Cloud"],
        "topics": ["DX"],
        "industries": ["IT", "データセンター"],
        "events": [],
        "tags": ["cat/it", "co/NTTデータ", "country/日本", "country/米国", "topic/DX", "score/高"]
    },
    {
        "date": "2026-05-24", "seen_at": "2026-05-24T06:00:00+09:00",
        "category": "IT-Consulting", "score": 80,
        "title": "Deloitte Tech Trends 2026——「アダプティブ企業」と「AI基盤化」が2大テーマ、人間×機械融合が競争優位に",
        "url": "https://www.deloitte.com/us/en/insights/topics/technology-management/tech-trends.html",
        "url_norm": "deloitte.com/us/en/insights/topics/technology-management/tech-trends.html",
        "source": "Deloitte Insights",
        "thumb": "https://media.deloitte.com/is/image/deloitte/US188546_Social:1200-x-627",
        "entities": ["Deloitte"],
        "topics": ["IT投資"],
        "industries": ["コンサル"],
        "events": [],
        "tags": ["cat/it", "co/デロイト", "country/米国", "topic/IT投資", "score/高"]
    },
    {
        "date": "2026-05-24", "seen_at": "2026-05-24T06:00:00+09:00",
        "category": "IT-Consulting", "score": 77,
        "title": "McKinseyリストラが示すコンサル業界への警告——AIがジュニア層業務を代替し、業界の階層構造自体が揺らいでいる",
        "url": "https://www.fastcompany.com/91463039/why-the-mckinsey-layoffs-are-a-warning-signal-for-consulting-in-the-ai-age-ai-layoffs-management-consulting",
        "url_norm": "fastcompany.com/91463039/why-the-mckinsey-layoffs-are-a-warning-signal-for-consulting-in-the-ai-age-ai-layoffs-management-consulting",
        "source": "Fast Company",
        "thumb": None,
        "entities": ["McKinsey", "Accenture"],
        "topics": ["コンサル業界"],
        "industries": ["コンサル"],
        "events": [],
        "tags": ["cat/it", "co/マッキンゼー", "country/米国", "topic/コンサル業界", "score/中"]
    },
    # ── Game ────────────────────────────────────────────────────────────
    {
        "date": "2026-05-24", "seen_at": "2026-05-24T06:00:00+09:00",
        "category": "Game", "score": 92,
        "title": "Switch 2値上げ前日に購入騒動——Bic Camera等が制限措置、転売抑制と旧価格駆け込みが交錯",
        "url": "https://www.notebookcheck.net/Switch-2-price-increase-in-Japan-causes-hysteria-as-stores-restrict-console-sales.1293250.0.html",
        "url_norm": "notebookcheck.net/switch-2-price-increase-in-japan-causes-hysteria-as-stores-restrict-console-sales.1293250.0.html",
        "source": "Notebookcheck",
        "thumb": "https://www.notebookcheck.net/fileadmin/Notebooks/News/_nc5/Switc2Japan.jpg",
        "entities": ["任天堂", "Bic Camera"],
        "topics": ["ゲーム市場"],
        "industries": ["ゲーム"],
        "events": ["製品発表"],
        "tags": ["cat/game", "co/任天堂", "country/日本", "topic/ゲーム市場", "event/製品発表", "score/高"]
    },
    {
        "date": "2026-05-24", "seen_at": "2026-05-24T06:00:00+09:00",
        "category": "Game", "score": 85,
        "title": "PlayStation State of Play、6月2日開催確定——SIEが夏向けPS5タイトル最新ラインナップを世界に発信へ",
        "url": "https://www.eventhubs.com/news/2026/may/20/playstation-state-play-june-2nd/",
        "url_norm": "eventhubs.com/news/2026/may/20/playstation-state-play-june-2nd",
        "source": "EventHubs",
        "thumb": "https://media.eventhubs.com/images/2026/05/20_state-play-bnrt.webp",
        "entities": ["SIE", "Sony"],
        "topics": ["新作発売"],
        "industries": ["ゲーム"],
        "events": ["製品発表"],
        "tags": ["cat/game", "co/SIE", "country/米国", "country/日本", "topic/新作発売", "event/製品発表", "score/高"]
    },
    {
        "date": "2026-05-24", "seen_at": "2026-05-24T06:00:00+09:00",
        "category": "Game", "score": 82,
        "title": "ウマ娘、累計$2.5B達成・直近4年で最高売上記録日——英語版グローバル展開がCygamesに新収益の基軸をもたらす",
        "url": "https://www.pocketgamer.biz/umamusume-pretty-derby-hits-25bn-after-most-lucrative-day-in-four-years/",
        "url_norm": "pocketgamer.biz/umamusume-pretty-derby-hits-25bn-after-most-lucrative-day-in-four-years",
        "source": "PocketGamer.biz",
        "thumb": "https://media.pocketgamer.biz/images/135886/87480/uma-musume-pretty-derby-silence-suzuka-race_l1200.jpg",
        "entities": ["Cygames", "サイバーエージェント"],
        "topics": ["ソーシャルゲーム"],
        "industries": ["ゲーム"],
        "events": [],
        "tags": ["cat/game", "co/Cygames", "country/日本", "country/米国", "topic/ソーシャルゲーム", "score/高"]
    },
    {
        "date": "2026-05-24", "seen_at": "2026-05-24T06:00:00+09:00",
        "category": "Game", "score": 75,
        "title": "Zenless Zone Zero v2.x最終フェーズ——v3.0（6/17）直前のバナー動向とmiHoYo 5月モバイル収益",
        "url": "https://revenue.ennead.cc/games/zenless",
        "url_norm": "revenue.ennead.cc/games/zenless",
        "source": "GACHA REVENUE",
        "thumb": None,
        "entities": ["miHoYo", "HoYoverse"],
        "topics": ["ソーシャルゲーム"],
        "industries": ["ゲーム"],
        "events": [],
        "tags": ["cat/game", "country/日本", "country/米国", "topic/ソーシャルゲーム", "score/中"]
    },
    {
        "date": "2026-05-24", "seen_at": "2026-05-24T06:00:00+09:00",
        "category": "Game", "score": 72,
        "title": "Switch 2 5月後半リリーススケジュール——Stray Switch 2版（5/28）を控え波状展開が続く",
        "url": "https://nintendoeverything.com/nintendo-release-schedule-may-2026/",
        "url_norm": "nintendoeverything.com/nintendo-release-schedule-may-2026",
        "source": "Nintendo Everything",
        "thumb": "https://nintendoeverything.com/wp-content/uploads/Nintendo-release-schedule-May-2026.webp",
        "entities": ["任天堂"],
        "topics": ["新作発売"],
        "industries": ["ゲーム"],
        "events": [],
        "tags": ["cat/game", "co/任天堂", "country/日本", "topic/新作発売", "score/中"]
    },
]

with open(JSONL, "a", encoding="utf-8") as f:
    for a in articles:
        f.write(json.dumps(a, ensure_ascii=False) + "\n")

print(f"✅ {len(articles)} 件追記完了 → {JSONL}")
