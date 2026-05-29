# coding: utf-8
"""
News-Grasp 2026-05-11 digest generator
月曜日: FX / AI / IT-Consulting / Economy
"""

import json, os, textwrap

ISSUE_DATE   = "2026-05-11"
ISSUE_NO     = "20260511"
WEEKDAY      = "月"
PREV_DATE    = "2026-05-10"
NEXT_DATE    = "2026-05-12"
SEEN_AT      = "2026-05-11T06:00:00+09:00"
BASE         = r"C:\Users\hidek\Obsidian\New's Grasp\News-Grasp"
CDN          = "https://raw.githubusercontent.com/HIDEPON-UMG/news-grasp-assets/main"

# ────────────────────────────────────────────────────────────────
# 記事データ
# ────────────────────────────────────────────────────────────────
CATEGORIES = [
  {
    "id": "fx", "name": "為替", "nameEn": "Foreign Exchange",
    "accent": "#B8860B", "glyph": "¥", "index": 1,
    "summary": "ベッセント財務長官の訪日を機に日米通貨外交が最前面へ。米CPI×FRB議長交代×日銀意見公表が週内に集中し、ドル円156円台の攻防は一段と激化する見通し。",
    "items": [
      {
        "score": 96, "time": "06:00", "source": "日本経済新聞",
        "title": "ベッセント米財務長官 本日訪日 — 高市首相・植田日銀総裁・片山財務相と会談へ、円安議論が焦点",
        "url": "https://www.nikkei.com/article/DGXZQOGN30CBA0Q6A430C2000000/",
        "url_norm": "nikkei.com/article/DGXZQOGN30CBA0Q6A430C2000000",
        "thumb": "https://article-image-ix.nikkei.com/https%3A%2F%2Fimgix-proxy.n8s.jp%2FDSXZQO2969140030042026000000-1.jpg?auto=compress&bg=FFFF&crop=focalpoint&fit=crop&fm=jpg&fp-y=0.36&h=630&w=1200&s=a91251e04e2d680871585940cb1d839c",
        "bullets": [
          "[[ベッセント]] 米財務長官が3日間の日程で本日訪日し、__高市首相・植田日銀総裁・片山財務相の3者と個別会談__を予定。投機的円売りへの対処策が主要議題。",
          "GW中に実施された[[350億ドル規模]]の為替介入効果が剥落しつつある中、財務長官直接会談は「外交的介入」として市場への心理的抑止力を期待される。",
          "日米貿易交渉と連携した形で通貨問題を俎上に載せる狙いも透けており、__ドル高・円安の持続を容認しない共同姿勢__を示すシグナルとなりうる。"
        ],
        "entities": {"companies": [], "countries": ["米国", "日本"], "services": [], "people": ["スコット・ベッセント", "高市早苗", "植田和男", "片山さつき"], "tickers": ["USDJPY"]},
        "topics": ["為替介入", "日米通貨政策"], "industries": ["金融"], "events": ["政策会合"],
        "tags": ["cat/fx", "country/日本", "country/米国", "person/スコット・ベッセント", "person/植田和男", "topic/為替介入", "event/政策会合", "score/高"]
      },
      {
        "score": 91, "time": "08:00", "source": "外為どっとコム",
        "title": "来週ドル円予想：米CPI（13日）× FRB議長交代（15日）で156円台に荒波",
        "url": "https://www.gaitame.com/media/entry/2026/05/09/080000",
        "url_norm": "gaitame.com/media/entry/2026/05/09/080000",
        "thumb": None,
        "bullets": [
          "米4月[[CPI]]が5月13日発表予定で、前月比+0.3%がコンセンサス。再加速なら__ドル買い・円安圧力__が再燃し157円台を試す展開も想定される。",
          "パウエルFRB議長の任期が15日に満了。後任ウォーシュ氏は利下げに積極的との見方が多く、__就任後の初期スタンス__がドル方向性を左右するカギ。",
          "ベッセント財務長官来日と[[日銀主な意見]]公表が重なり、「政策ミスマッチ」が同時多発的に発現する可能性があり週初からポジション管理が難しい週。"
        ],
        "entities": {"companies": [], "countries": ["米国", "日本"], "services": [], "people": ["スコット・ベッセント", "ジェローム・パウエル"], "tickers": ["USDJPY"]},
        "topics": ["FRB", "CPI", "金利差"], "industries": ["金融"], "events": ["政策会合"],
        "tags": ["cat/fx", "country/米国", "country/日本", "topic/FRB", "topic/CPI", "score/高"]
      },
      {
        "score": 88, "time": "07:50", "source": "みんかぶFX",
        "title": "FRB議長 パウエル任期5月15日満了 — ウォーシュ承認で来週は政策転換の号砲か",
        "url": "https://fx.minkabu.jp/news/366682",
        "url_norm": "fx.minkabu.jp/news/366682",
        "thumb": "https://mfx-assets.s3.ap-northeast-1.amazonaws.com/news_ogp/forex.png",
        "bullets": [
          "[[パウエル]]FRB議長の任期が5月15日に満了し、ウォーシュ次期議長が就任見込み。市場は利下げ積極姿勢への転換を先取りし__ドル安で反応する公算__が高い。",
          "来週のイベントは「12日日銀主な意見」「13日米CPI」「15日議長交代」と__政策イベントが連日集中__し、ポジション管理が極めて困難な週となる。",
          "米中首脳会談も同週末に予定されており、[[貿易摩擦]]緩和期待のドル売り/リスクオンと金利政策の綱引きが為替変動率を押し上げる見通し。"
        ],
        "entities": {"companies": [], "countries": ["米国", "日本", "中国"], "services": [], "people": ["ジェローム・パウエル", "ケビン・ウォーシュ"], "tickers": ["USDJPY"]},
        "topics": ["FRB", "金融政策", "米中関係"], "industries": ["金融"], "events": ["政策会合"],
        "tags": ["cat/fx", "country/米国", "person/ジェローム・パウエル", "topic/FRB", "topic/金融政策", "score/高"]
      },
      {
        "score": 82, "time": "07:00", "source": "日本経済新聞",
        "title": "米財務長官訪日で「異例の会談」— 円安抑止に効果的との声、市場の神経戦が始まる",
        "url": "https://www.nikkei.com/article/DGXZQOFL070RJTX00C26A5000000/",
        "url_norm": "nikkei.com/article/DGXZQOFL070RJTX00C26A5000000",
        "thumb": "https://article-image-ix.nikkei.com/https%3A%2F%2Fimgix-proxy.n8s.jp%2FDSXZQO2983687007052026000000-1.jpg?auto=compress&bg=FFFF&crop=focalpoint&fit=crop&fm=jpg&fp-x=0.41&fp-y=0.28&h=630&upscale=false&w=1200&s=c3ae0b482ae2780c450bf64f5dffd075",
        "bullets": [
          "米財務長官が来日し[[日銀]]総裁と直接会談するのは極めて異例で、__市場への強いシグナル効果__が期待されている。単発の介入より持続力がある可能性。",
          "「日本発金融危機を警戒するベッセントが自ら動いた」との分析もあり、円安が米国経済へも波及するリスクを[[米国側]]が意識し始めた構図が鮮明に。",
          "GW介入後もドル高圧力が持続している状況で、今回の会談が次の実弾介入前の__外交的牽制__として機能するか、週末の共同声明の文言に注目。"
        ],
        "entities": {"companies": [], "countries": ["米国", "日本"], "services": [], "people": ["スコット・ベッセント", "植田和男"], "tickers": ["USDJPY"]},
        "topics": ["為替介入", "日米通貨政策"], "industries": ["金融"], "events": [],
        "tags": ["cat/fx", "country/日本", "country/米国", "person/スコット・ベッセント", "topic/為替介入", "score/中"]
      },
      {
        "score": 76, "time": "06:32", "source": "みんかぶFX",
        "title": "ドル円 NY為替概況 — ドル安やや優勢で156円台後半、週初の攻防ラインを意識",
        "url": "https://fx.minkabu.jp/news/366674",
        "url_norm": "fx.minkabu.jp/news/366674",
        "thumb": "https://mfx-assets.s3.ap-northeast-1.amazonaws.com/news_ogp/forex_overview.png",
        "bullets": [
          "ニューヨーク外為市場では[[ドル安]]がやや優勢となり、ドル円は156円台後半で終了。欧州通貨・資源国通貨に対してもドル売りが広がった。",
          "米雇用統計後の調整局面が続いており、__週明け以降のイベントリスクを前にポジション軽量化__が主体。方向感に乏しい横ばいの展開が継続。",
          "テクニカル的には[[156.50]]を維持できるかが短期の焦点で、割れた場合は155円台中盤までの調整が視野に入る。ベッセント訪日がトレンドの転換点になるか。"
        ],
        "entities": {"companies": [], "countries": ["米国", "日本"], "services": [], "people": [], "tickers": ["USDJPY", "EURUSD"]},
        "topics": ["為替相場", "テクニカル分析"], "industries": ["金融"], "events": [],
        "tags": ["cat/fx", "country/米国", "country/日本", "topic/為替相場", "score/中"]
      },
    ]
  },
  {
    "id": "ai", "name": "AI", "nameEn": "Artificial Intelligence",
    "accent": "#2D5BB8", "glyph": "◆", "index": 2,
    "summary": "Wall StreetのAIチップ投資がNVIDIAから多極化し、OpenAIはGPT-5.5-Cyberをセキュリティ分野に限定展開。AIモデル競争が「セキュリティ応用」という新たな軸で激化している。",
    "items": [
      {
        "score": 95, "time": "07:30", "source": "CNBC",
        "title": "Wall Street の AI チップ愛が NVIDIA から AMD・Intel・Micron へ — 半導体投資の多極化が加速",
        "url": "https://www.cnbc.com/2026/05/08/wall-street-ai-chip-love-moves-from-nvidia-to-intel-amd-and-micron.html",
        "url_norm": "cnbc.com/2026/05/08/wall-street-ai-chip-love-moves-from-nvidia-to-intel-amd-and-micron.html",
        "thumb": "https://image.cnbcfm.com/api/v1/image/108302934-1778074131329-LisaSu4.jpg?v=1778074231&w=1920&h=1080",
        "bullets": [
          "[[AMD]] CEO リサ・スーが「今後3〜5年のサーバーCPU市場成長を35%と予測」と上方修正。MI450 GPU搭載の Helios ラックスケールサーバーも今年後半に出荷開始予定。",
          "NVIDIA への集中投資から Intel・AMD・Micron への分散投資へと Wall Street のセンチメントがシフト。__エージェントAI時代にCPUが再び主役__に返り咲くとの見立てが背景。",
          "[[NVIDIA]] Vera Rubin プラットフォームが下半期出荷予定で、データセンター売上は FY2026 で 1,935 億ドルを記録。ただし競合の追い上げで一強時代の終焉を市場は警戒。"
        ],
        "entities": {"companies": ["NVIDIA", "AMD", "Intel", "Micron"], "countries": ["米国"], "services": ["Vera-Rubin", "Helios", "MI450"], "people": ["リサ・スー"], "tickers": ["NVDA", "AMD", "INTC", "MU"]},
        "topics": ["AI半導体", "データセンター投資"], "industries": ["半導体", "AI"], "events": [],
        "tags": ["cat/ai", "co/NVIDIA", "co/AMD", "co/Intel", "country/米国", "topic/AI半導体", "score/高"]
      },
      {
        "score": 90, "time": "09:00", "source": "CNBC",
        "title": "OpenAI、GPT-5.5-Cyber を限定展開 — セキュリティチームに Anthropic の1ヶ月差で追随",
        "url": "https://www.cnbc.com/2026/05/07/openai-rolls-out-new-gpt-5point5-cyber-to-vetted-cybersecurity-teams.html",
        "url_norm": "cnbc.com/2026/05/07/openai-rolls-out-new-gpt-5point5-cyber-to-vetted-cybersecurity-teams.html",
        "thumb": "https://image.cnbcfm.com/api/v1/image/108276814-1773254522162-gettyimages-2265991644-0d6a8221_0m2z4cha.jpeg?v=1776066514&w=1920&h=1080",
        "bullets": [
          "[[GPT-5.5-Cyber]] が審査済みのサイバーセキュリティチームへの限定プレビューとして公開。Anthropic の Claude Mythos Preview が32ステップのサイバー攻撃レンジを突破した1ヶ月後の対応。",
          "モデルはより「許容的なセキュリティワークフロー」向けにチューニングされており、__脆弱性発見・ペネトレーションテスト支援__に特化。国防省AI契約とも連動。",
          "OpenAI と Anthropic がそれぞれ [[軍事・安全保障]] 応用AIを投入する中、AIモデル競争は一般コンシューマー向けから「セキュリティ応用」という新軸へと拡張している。"
        ],
        "entities": {"companies": ["OpenAI", "Anthropic"], "countries": ["米国"], "services": ["GPT-5_5-Cyber", "Claude-Mythos-Preview"], "people": [], "tickers": []},
        "topics": ["AIセキュリティ", "サイバー防衛"], "industries": ["AI", "セキュリティ"], "events": ["製品発表"],
        "tags": ["cat/ai", "co/OpenAI", "co/Anthropic", "country/米国", "topic/AIセキュリティ", "event/製品発表", "score/高"]
      },
      {
        "score": 85, "time": "10:00", "source": "Future AGI",
        "title": "Best LLMs of May 2026 — GPT-5.5・Claude Mythos・Gemini 3 が実用生産環境をリード",
        "url": "https://futureagi.com/blog/best-llms-may-2026",
        "url_norm": "futureagi.com/blog/best-llms-may-2026",
        "thumb": "https://futureagi.com/images/blog/monthly-compare/2026-05/hero.webp",
        "bullets": [
          "2026年5月のLLM実力評価では、[[GPT-5.5]] Instant が ChatGPT デフォルト化でコード・分析に首位を維持。Claude Mythos Preview はセキュリティ特化で別格の評価。",
          "Apple が iOS 27 で__サードパーティAIモデルのシステムレベル選択__を解禁予定と報道。OpenAI 2年独占体制に初の亀裂が入り、マルチAIスマートフォン時代が到来か。",
          "SubQ が12Mトークンのサブ二次アテンション文脈ウィンドウを持つ [[SubQ-LLM]] を 2,900万ドルのシードで公開。長文脈処理の低コスト化競争に新たな参入者。"
        ],
        "entities": {"companies": ["OpenAI", "Anthropic", "Apple", "Google"], "countries": ["米国"], "services": ["GPT-5_5", "Claude-Mythos-Preview", "iOS-27"], "people": [], "tickers": ["AAPL"]},
        "topics": ["LLMベンチマーク", "AIエージェント"], "industries": ["AI"], "events": [],
        "tags": ["cat/ai", "co/OpenAI", "co/Apple", "co/Anthropic", "country/米国", "topic/LLMベンチマーク", "score/高"]
      },
      {
        "score": 80, "time": "08:30", "source": "RoboRhythms",
        "title": "Pentagon が Anthropic を AI 契約から排除 — 「全適法」条項の拒否が理由、独自戦略へ",
        "url": "https://www.roborhythms.com/pentagon-ai-contracts-anthropic-excluded-may-2026/",
        "url_norm": "roborhythms.com/pentagon-ai-contracts-anthropic-excluded-may-2026",
        "thumb": "https://www.roborhythms.com/wp-content/uploads/2026/05/pentagon-ai-contracts-anthropic-excluded-may-2026-1024x538.jpg",
        "bullets": [
          "国防省が OpenAI・Google・Microsoft・NVIDIA・SpaceX 等8社と機密ネットワークAI契約を締結した一方、[[Anthropic]] は「全適法目的での使用」条項拒否により排除された。",
          "Anthropic は大量監視・完全自律型兵器への悪用リスクを理由に条件を拒否。__Project Glasswing で金融・医療向けセキュリティAIに特化__する独自路線を強化。",
          "[[Claude Mythos Preview]] が32ステップのサイバー攻撃シミュレーションを突破した実績を背景に、民間セキュリティ市場での差別化を図る戦略的布陣が鮮明になった。"
        ],
        "entities": {"companies": ["Anthropic", "OpenAI", "Google", "Microsoft", "NVIDIA", "SpaceX"], "countries": ["米国"], "services": ["Claude-Mythos-Preview"], "people": ["ダリオ・アモデイ"], "tickers": ["NVDA", "GOOG", "MSFT"]},
        "topics": ["AI規制", "国防AI", "AIガバナンス"], "industries": ["AI", "防衛"], "events": [],
        "tags": ["cat/ai", "co/Anthropic", "co/OpenAI", "country/米国", "topic/AI規制", "topic/国防AI", "score/高"]
      },
      {
        "score": 76, "time": "11:00", "source": "MarketingProfs",
        "title": "AI Update May 8, 2026 — Glasswing・GPT-5.5-Cyber・AI Oversight が週の3大トピック",
        "url": "https://www.marketingprofs.com/opinions/2026/54655/ai-update-may-8-2026-ai-news-and-views-from-the-past-week",
        "url_norm": "marketingprofs.com/opinions/2026/54655/ai-update-may-8-2026-ai-news-and-views-from-the-past-week",
        "thumb": "https://i.marketingprofs.com/assets/images/articles/lg/MP-AI-Update-lg.jpg",
        "bullets": [
          "5月8日週のAI重要ニュース3本柱は「[[Project Glasswing]] (Anthropic)」「GPT-5.5-Cyber (OpenAI)」「CAISI による政府AIモデル評価の拡大」。",
          "OpenAI 社長グレッグ・ブロックマンが米上院で「2026年インフラ投資__500億ドル__を見込む」と証言。2025年のAIインフラ向けVC総額を上回る規模。",
          "[[ChatGPT]] のデフォルトモデルが GPT-5.5 Instant に切り替わり、ハルシネーション52.5%減を内部評価で確認。企業ユーザーからの信頼度が急上昇。"
        ],
        "entities": {"companies": ["OpenAI", "Anthropic"], "countries": ["米国"], "services": ["ChatGPT", "GPT-5_5"], "people": ["グレッグ・ブロックマン"], "tickers": []},
        "topics": ["AIガバナンス", "AI投資"], "industries": ["AI"], "events": [],
        "tags": ["cat/ai", "co/OpenAI", "co/Anthropic", "country/米国", "topic/AI投資", "score/中"]
      },
    ]
  },
  {
    "id": "it", "name": "IT-Consulting", "nameEn": "IT & Consulting",
    "accent": "#2E6B52", "glyph": "▲", "index": 3,
    "summary": "McKinseyがAIエージェントでコンサルタントのチーム配置を自動化すると発表。BCG・PwC・EYも独自AIフレームワークを整備し、コンサル業界の「エージェント時代」移行が本格化。",
    "items": [
      {
        "score": 91, "time": "09:00", "source": "Bloomberg",
        "title": "McKinsey、AI エージェントでコンサルタント配置を決定へ — クライアントチーム選定も AI が担う",
        "url": "https://www.bloomberg.com/news/articles/2026-05-01/mckinsey-plans-to-use-ai-agents-to-help-choose-client-teams",
        "url_norm": "bloomberg.com/news/articles/2026-05-01/mckinsey-plans-to-use-ai-agents-to-help-choose-client-teams",
        "thumb": None,
        "bullets": [
          "[[McKinsey]] がクライアントチームへのコンサルタント割り当てに AI エージェントを導入予定と発表。Agents-at-Scale スイートの一環で、__人材配置の意思決定そのものを自動化__する方針。",
          "採用面接・能力評価から始まり、プロジェクト案件マッチングまで AI が関与。Bloomberg の報道では「コンサルタントが知らぬ間に AI に評価されている」実態が浮き彫りに。",
          "BCG・Bain でも AI が新卒採用基準に影響しており、コンサル業界の [[組織構造]] は伝統的ピラミッド型を保ちつつも内側からAIに侵食されている状況。"
        ],
        "entities": {"companies": ["McKinsey", "BCG", "Bain"], "countries": ["米国"], "services": [], "people": [], "tickers": []},
        "topics": ["AIエージェント", "コンサル業界"], "industries": ["IT-コンサル"], "events": [],
        "tags": ["cat/it", "co/マッキンゼー", "country/米国", "topic/AIエージェント", "topic/コンサル業界", "score/高"]
      },
      {
        "score": 85, "time": "10:30", "source": "Future of Consulting AI",
        "title": "2026年コンサル AI 革命アップデート：数十億ドル投資でも旧ピラミッド構造は温存",
        "url": "https://futureofconsulting.ai/ai-leadership/2026-consultings-ai-revolution-update/",
        "url_norm": "futureofconsulting.ai/ai-leadership/2026-consultings-ai-revolution-update",
        "thumb": "https://futureofconsulting.ai/wp-content/uploads/2026/01/Consulting-AI-2026-Update-1170x745.jpg",
        "bullets": [
          "McKinsey・BCG・PwC・KPMG が 2025〜2026 年にかけてエージェントAI への投資を数十億ドル規模で実行したが、[[パートナー制度]]の本質的な変革には至っていないと指摘。",
          "86%のコンサル購入者が「AIを使った企業に仕事を発注したい」と回答（IBM調査）。__AI非採用のコンサル会社は契約打ち切りリスク__にさらされていることが明らかに。",
          "各社の差異化軸：McKinsey=方法論、BCG=価値捕捉経済学、EY=規制業界向けソブリンAI、PwC=[[ChatPwC]]20万人展開でスケール優位。"
        ],
        "entities": {"companies": ["McKinsey", "BCG", "PwC", "EY", "KPMG"], "countries": ["米国"], "services": [], "people": [], "tickers": []},
        "topics": ["AIコンサル", "デジタル変革"], "industries": ["IT-コンサル"], "events": [],
        "tags": ["cat/it", "co/マッキンゼー", "co/アクセンチュア", "country/米国", "topic/AIコンサル", "score/高"]
      },
      {
        "score": 82, "time": "08:00", "source": "Bloomberg",
        "title": "AI が McKinsey・BCG・Bain の新卒採用基準を変革 — エントリーレベルコンサルに AI 評価が侵入",
        "url": "https://www.bloomberg.com/news/articles/2026-04-15/ai-influences-how-mckinsey-bcg-bain-hire-for-entry-level-consulting-jobs",
        "url_norm": "bloomberg.com/news/articles/2026-04-15/ai-influences-how-mckinsey-bcg-bain-hire-for-entry-level-consulting-jobs",
        "thumb": None,
        "bullets": [
          "[[McKinsey]]・BCG・Bain の3社が採用面接に AI スクリーニングを導入。候補者のケース面接動画を AI が解析し、__論理的思考パターンと非言語コミュニケーション__を採点。",
          "従来の「ケースインタビュー一発合格」から「AI+人間の複合評価」への移行で、コンサル採用の民主化か逆に新バイアス導入かで業界が二分される議論。",
          "PwC の調査では66%の[[コンサル発注者]]が「AIを活用しない企業とは取引を止める」と回答。採用から人材価値評価まで全体がAIに最適化される加速が続く。"
        ],
        "entities": {"companies": ["McKinsey", "BCG", "Bain", "PwC"], "countries": ["米国"], "services": [], "people": [], "tickers": []},
        "topics": ["AI採用", "コンサル業界"], "industries": ["IT-コンサル"], "events": [],
        "tags": ["cat/it", "co/マッキンゼー", "country/米国", "topic/AI採用", "score/中"]
      },
      {
        "score": 78, "time": "11:00", "source": "Consulting Huber",
        "title": "Big4 AI フレームワーク比較 2026 — EY が Big4 唯一の NVIDIA Dell AI Factory 導入、ソブリン AI で差別化",
        "url": "https://consulting-huber.com/ai-consulting-frameworks-compared.html",
        "url_norm": "consulting-huber.com/ai-consulting-frameworks-compared.html",
        "thumb": "https://consulting-huber.com/og-image.png",
        "bullets": [
          "[[EY]] が Big4 で唯一 NVIDIA Dell AI Factory をオンプレミス導入。規制業界（金融・医療・政府）向けの「ソブリンAI」で、クラウド依存リスクを排除した__プライベート AI 基盤__を構築。",
          "PwC は約20万人への ChatPwC 展開でスケール優位を持ち、2026年2月に「Human + AI Skillset」カリキュラム30スキル（AI15・人間力15）を全社展開。",
          "McKinsey は [[Rewired]] 6能力フレームワークと年次調査を「知的権威」として活用。BCG は10-20-70（技術10:データ20:人間70）で[[価値捕捉]]経済学を訴求。"
        ],
        "entities": {"companies": ["EY", "PwC", "McKinsey", "BCG", "KPMG", "NVIDIA"], "countries": ["米国"], "services": [], "people": [], "tickers": ["NVDA"]},
        "topics": ["AIコンサル", "ソブリンAI"], "industries": ["IT-コンサル", "AI"], "events": [],
        "tags": ["cat/it", "co/EY", "co/NVIDIA", "country/米国", "topic/AIコンサル", "topic/ソブリンAI", "score/中"]
      },
      {
        "score": 74, "time": "09:30", "source": "Plus AI",
        "title": "PwC「Human + AI Skillset」— 30スキルカリキュラムで全社展開、AI×人間力の融合を急ぐ",
        "url": "https://plusai.com/blog/how-consulting-firms-use-ai",
        "url_norm": "plusai.com/blog/how-consulting-firms-use-ai",
        "thumb": "https://plusai.com/62375700635d76646ef2457f/690130bb00d5d62159784ce8_openai-gives-mckinsey-company-an-award-for-passing-100-v0-78mcg23u09xf1.webp",
        "bullets": [
          "[[PwC]] が2026年2月に「Human + AI Skillset」カリキュラムを全社展開。30スキルのうち AI 関連15・人間力関連15で、__AIを前提とした業務遂行能力__を全員に装着。",
          "Accenture、Deloitte も同様の全社AI研修を加速。コンサル業界では「AIを使えるコンサルタント」が標準スペックとなり、従来の知識優位が__スキル再定義__を迫られている。",
          "コンサル大手が AI に本格投資する中、IT コンサル全社の年間 AI 関連新規受注高は[[59億ドル]]水準（Accenture 単体）に達し、市場は前年の2倍規模で急拡大している。"
        ],
        "entities": {"companies": ["PwC", "Accenture", "Deloitte"], "countries": ["米国"], "services": [], "people": [], "tickers": ["ACN"]},
        "topics": ["AI研修", "スキル開発"], "industries": ["IT-コンサル"], "events": [],
        "tags": ["cat/it", "co/PwC", "co/アクセンチュア", "country/米国", "topic/AI研修", "score/中"]
      },
    ]
  },
  {
    "id": "economy", "name": "経済", "nameEn": "Economy",
    "accent": "#8E2A19", "glyph": "■", "index": 4,
    "summary": "日経平均が4月に+16.1%と史上最高値圏を記録。5月は米CPI発表・FRB議長交代・日銀利上げ期待が重なる試練の週を迎え、株価・為替ともに高ボラティリティな週初となる見通し。",
    "items": [
      {
        "score": 94, "time": "08:00", "source": "三井住友DSアセットマネジメント",
        "title": "2026年4月マーケット振り返り — 日経平均+16.1%、史上最高値を更新した1ヶ月の全解析",
        "url": "https://www.smd-am.co.jp/market/lastweek/monthly/2026/month260507gl/",
        "url_norm": "smd-am.co.jp/market/lastweek/monthly/2026/month260507gl",
        "thumb": "https://www.smd-am.co.jp/common_files/images/ogimage.png",
        "bullets": [
          "日本株市場は[[フィラデルフィア半導体指数]]（SOX）の上昇に追随して半導体・電子部品株が急騰し、日経平均は4月に+16.10%と月間で史上最高値水準を更新した。",
          "賃金と物価の好循環・企業統治改革・高市政権の経済対策が重なる「__日本株の構造的強さ__」が評価され、海外機関投資家の資金流入が継続。",
          "原油価格高騰はエネルギー純輸入国の日本に下押し圧力だが、[[製造業]]の収益改善期待が打ち消す形で株価は5月も上昇基調継続と分析。"
        ],
        "entities": {"companies": [], "countries": ["日本", "米国"], "services": [], "people": [], "tickers": ["N225", "SOX"]},
        "topics": ["株式市場", "日本経済"], "industries": ["金融"], "events": [],
        "tags": ["cat/economy", "country/日本", "country/米国", "topic/株式市場", "topic/日本経済", "score/高"]
      },
      {
        "score": 91, "time": "12:29", "source": "外為どっとコム",
        "title": "S&P500 最高値 7,300 に死角 — CPI 再加速警戒・FRB「8対4」亀裂・月次 OP 満期が重なる週",
        "url": "https://www.gaitame.com/media/entry/2026/05/08/122903",
        "url_norm": "gaitame.com/media/entry/2026/05/08/122903",
        "thumb": None,
        "bullets": [
          "[[S&P500]]が7,300の最高値圏で迎える週は、4月CPIの再加速懸念・FRB「8対4」利下げ意見亀裂・月次オプション満期が三重苦として重なり__相場は不安定__になりやすい。",
          "FRB内部では「8票が利下げ継続支持、4票が据え置き・利上げ」との亀裂が表面化。議長交代でどちらの路線が採用されるかで[[株価リスク]]の方向性が変わる。",
          "月次オプション満期（Monthly Expiry）が13日CPIと同週に重なり、過去データでは変動率が平均1.8倍に拡大。AI・半導体銘柄が相場牽引の正念場を迎える。"
        ],
        "entities": {"companies": [], "countries": ["米国"], "services": [], "people": [], "tickers": ["SPX", "NDX"]},
        "topics": ["米国株", "CPI", "FRB"], "industries": ["金融"], "events": [],
        "tags": ["cat/economy", "country/米国", "topic/米国株", "topic/FRB", "score/高"]
      },
      {
        "score": 88, "time": "09:00", "source": "三井住友DSアセットマネジメント",
        "title": "日銀利上げスタンスを読み解く — 2026年7月・12月の年2回シナリオが市場コンセンサスに",
        "url": "https://www.smd-am.co.jp/market/macroview/2026/mvreport20260501_2/",
        "url_norm": "smd-am.co.jp/market/macroview/2026/mvreport20260501_2",
        "thumb": "https://www.smd-am.co.jp/common_files/images/ogimage.png",
        "bullets": [
          "[[日銀]]が段階的利上げを継続する中、金融市場では「2026年7月1回・12月1回の合計2回」がコンセンサス。長期金利は着地点2.0%への険しい道のりが続く。",
          "石油のほぼ全量を中東に依存する日本では、エネルギー価格上昇という外的ショックに対し__金融政策での対応は困難__とされており、利上げは内需改善が前提条件。",
          "FRBが2027年に利下げ再開する見通しと組み合わせると、[[日米金利差]]縮小による円高シナリオが2027年以降に本格化。長期での円安是正への道筋が見えてきた。"
        ],
        "entities": {"companies": [], "countries": ["日本", "米国"], "services": [], "people": ["植田和男"], "tickers": ["USDJPY"]},
        "topics": ["日銀", "利上げ", "金融政策"], "industries": ["金融"], "events": ["政策会合"],
        "tags": ["cat/economy", "country/日本", "person/植田和男", "topic/日銀", "topic/利上げ", "event/政策会合", "score/高"]
      },
      {
        "score": 83, "time": "07:08", "source": "日本経済新聞",
        "title": "日経平均 上値メド6万2500円 — 米半導体株高が追い風、高市政権の経済対策が株高の二段ロケットに",
        "url": "https://www.nikkei.com/article/DGXZQOFL0708VTX00C26A5000000/",
        "url_norm": "nikkei.com/article/DGXZQOFL0708VTX00C26A5000000",
        "thumb": "https://article-image-ix.nikkei.com/https%3A%2F%2Fimgix-proxy.n8s.jp%2FDSXZQO2980810007052026000000-1.jpg?auto=compress&bg=FFFF&crop=focalpoint&fit=crop&fm=jpg&h=630&w=1200&s=8b9f0c623501bb2967f245e7b6758401",
        "bullets": [
          "市場関係者は日経平均の次の上値メドとして[[6万2500円]]を提示。米半導体株（SOX指数）の上昇が NVIDIA 等の日本子会社・サプライチェーンを直撃し、連動高が続く。",
          "高市政権の経済対策として半導体・AI関連投資促進策の具体化が進んでおり、__政策期待と業績実態の二重の後押し__が株価を支える構造が形成されている。",
          "ただし[[日銀利上げ]]継続と為替円高転換リスクが後半に顕在化した場合、輸出企業の業績下方修正が相場の天井を作る可能性も市場は織り込み始めている。"
        ],
        "entities": {"companies": ["NVIDIA"], "countries": ["日本", "米国"], "services": [], "people": ["高市早苗"], "tickers": ["N225", "NVDA"]},
        "topics": ["日本株", "半導体", "経済対策"], "industries": ["金融", "半導体"], "events": [],
        "tags": ["cat/economy", "country/日本", "person/高市早苗", "topic/日本株", "topic/半導体", "score/中"]
      },
      {
        "score": 78, "time": "10:00", "source": "野村證券",
        "title": "野村証券 S&P500 年末7,300予想を維持 — FRB議長交代がカタリスト、米中会談後の収束シナリオ",
        "url": "https://www.nomura.co.jp/wealthstyle/article/0574/",
        "url_norm": "nomura.co.jp/wealthstyle/article/0574",
        "thumb": "https://www.nomura.co.jp/wealthstyle/article/0574/images/og_a_0574_01.png",
        "bullets": [
          "野村證券ストラテジストが[[S&P500]]の2026年末予想7,300・2027年末7,600・2028年末7,900の長期路線を維持。FRB議長交代が__強気相場の新たな触媒__になると評価。",
          "主シナリオでは米中首脳会談で中東情勢が4〜6月中に収束し、景気・業績拡大が続く前提。2026年内にFRBが利下げ2回を実施し企業の資本コストが低下。",
          "リスク要因は[[CPI再加速]]と議長交代後の政策不透明感。CPIが上ブレした場合は利下げ先送りで株価調整が6,900水準まで下落するシナリオも想定。"
        ],
        "entities": {"companies": [], "countries": ["米国", "中国"], "services": [], "people": [], "tickers": ["SPX"]},
        "topics": ["米国株", "FRB", "投資見通し"], "industries": ["金融"], "events": [],
        "tags": ["cat/economy", "country/米国", "topic/米国株", "topic/FRB", "score/中"]
      },
    ]
  }
]

# ────────────────────────────────────────────────────────────────
# テーマ考察
# ────────────────────────────────────────────────────────────────
REFLECTION = {
  "title": "通貨外交と AI 軍拡が同時進行する月曜朝",
  "subtitle": "ベッセント訪日・FRB交代・Pentagon AI契約が一週間に凝縮された「政策の波乱週」",
  "lead": "本日5分野・20本のニュースから浮かび上がる最大のテーマは [[ベッセント訪日]] と [[AIの軍事応用化]] の同時進行である。為替市場では日米財務長官・日銀総裁の直接会談という異例の外交的介入が走り、AIの世界ではPentagonの契約に込められた「倫理vs実利」の葛藤が各社の戦略を分岐させた。以下、各カテゴリを横断して読み解く。",
  "pull_quote": "「数十億ドルの AI 投資でも旧ピラミッドは壊れない」──コンサル業界の__構造的慣性__は、テクノロジーではなく人間の権力構造が守っている。",
  "sections": [
    {
      "tag": "総論", "accent": "#1A1A1A",
      "heading": "政策・技術・資本が交差する「決定週」の幕開け",
      "body": "2026年5月11日週は為替・株式・AI・コンサルの全分野で「決定的なイベント」が集中する稀有な週だ。[[ベッセント訪日]]（FX）、米4月CPI発表（経済）、FRB議長交代（FX・経済）、Pentagon AI契約（AI）、McKinsey AIエージェント導入（IT）──これら5つの出来事はそれぞれ独立して見えるが、根底では「__人間の意思決定をどこまでAIと政策に委ねるか__」という同じ問いに収束している。"
    },
    {
      "tag": "為替・経済", "accent": "#B8860B",
      "heading": "ベッセント訪日と CPI が為替の「二重の試練」を作る",
      "body": "ドル円156円台は今週、FRB議長交代とCPI発表という二大イベントに挟まれる。[[ベッセント]]財務長官が植田日銀総裁と直接会談する「外交的介入」は実弾介入より持続力があると市場は評価するが、CPIが上ブレすれば__介入の効果を相殺するドル高圧力__が一気に復活する。日経平均が4月+16.1%の史上最高値水準で週を迎える中、S&P500の7,300水準と合わせた「株高・ドル高・円安」の三角形が今週崩れるか否かに注目が集まる。"
    },
    {
      "tag": "AI・技術", "accent": "#2D5BB8",
      "heading": "GPT-5.5-Cyber と Pentagon 排除が示す「AI の軍事分岐」",
      "body": "OpenAI の[[GPT-5.5-Cyber]]と Anthropic の Claude Mythos Preview が同時に登場した今週のAI市場は、消費者向けからサイバーセキュリティ・国防応用へと軸足を移した。Pentagon が Anthropic を排除した理由は「全適法目的」条項の拒否であり、__AI各社が「使われ方」で戦略を分岐させている__ことを示す。Wall Street では NVIDIA 一極集中から AMD・Intel・Micron への分散投資が進み、AI半導体市場の多極化が加速している。"
    },
    {
      "tag": "産業・業界", "accent": "#2E6B52",
      "heading": "McKinsey の AI エージェント採用は「コンサル産業の鏡」",
      "body": "[[McKinsey]] が AI エージェントでコンサルタント配置を決定するという発表は、コンサル業界が「AI で人間を動かす」段階に入ったことを象徴する。しかし調査では投資数十億ドルでも__旧ピラミッド構造は温存__されており、Big4 の差別化軸（EY=ソブリンAI、PwC=全社スキル化、McKinsey=方法論権威）はむしろ鮮明になっている。AI はコンサルの「中身」を変えつつも「形式」は守っている構造的逆説。"
    },
    {
      "tag": "明日へ", "accent": "#C9B98A",
      "heading": "今週の3つの「決定」が来週の相場を作る",
      "body": "今週末には①[[米4月CPI]]（13日）②FRB議長就任（15日）③日米通貨会談の共同声明（11〜13日）という3つの決定が出揃う。CPI再加速 → ドル買い・株安・円安三重苦、CPI鈍化 → ドル安・株高・円高反転の二択だ。AI分野では Pentagon と Anthropic の決裂後、__金融・医療・民間セキュリティ向けのAI市場__が新たな主戦場として急浮上している。来週の digest では、この「決定週」の結果を振り返る予定だ。"
    }
  ],
  "takeaways": [
    {"tag": "為替", "color": "#B8860B", "text": "[[ベッセント訪日]]が外交的円高圧力を作るが、米CPI再加速が相殺リスク。ドル円156円台の攻防は今週が分水嶺。"},
    {"tag": "AI",   "color": "#2D5BB8", "text": "Pentagon AI契約からの Anthropic 排除は「倫理 vs 実利」の業界分岐を示す。__セキュリティ応用AI__が次の主戦場に。"},
    {"tag": "産業", "color": "#2E6B52", "text": "McKinseyのAIエージェント採用は、コンサル業界がAIで「人を管理する」段階に到達したことを証明した。[[組織変革]]の本命はここ。"}
  ],
  "related": [
    {"date": "2026-05-10", "title": "2026-05-10 digest (FX, AI, IT-Consulting, Game)"},
    {"date": "2026-05-09", "title": "2026-05-09 digest (FX, AI, IT-Consulting, Game)"},
    {"date": "2026-05-08", "title": "2026-05-08 digest (FX, AI, IT-Consulting, Economy)"},
  ]
}


# ────────────────────────────────────────────────────────────────
# ヘルパー
# ────────────────────────────────────────────────────────────────
def md_markup(text):
    """[[X]] → **X** (bold), __X__ → <u>X</u> 相当の表現"""
    import re
    text = re.sub(r'\[\[(.+?)\]\]', r'**\1**', text)
    text = re.sub(r'__(.+?)__', r'*\1*', text)
    return text

def ng_thumb(cat_id, kind="common"):
    if kind == "featured":
        return f"{CDN}/ng-thumb-{cat_id}.jpg"
    return f"{CDN}/ng-thumb-{kind}-{cat_id}.jpg"

def build_category_md(cat):
    lines = []
    lines.append(f"## {cat['glyph']} {cat['index']}. {cat['name']} ({cat['nameEn']})")
    lines.append("")
    lines.append(f"> [!summary]")
    lines.append(f"> {cat['summary']}")
    lines.append("")
    for i, item in enumerate(cat["items"]):
        score_label = "高" if item["score"] >= 85 else ("中" if item["score"] >= 65 else "低")
        score_badge = "🔥 TOP FEATURED" if i == 0 else f"#{i+1:02d}"
        lines.append(f"### [{item['score']}] {item['title']}")
        lines.append("")
        lines.append(f"📅 {ISSUE_DATE} {item['time']} · 📰 {item['source']} · 🔗 [元記事]({item['url']})")
        lines.append("")
        # card tags (filtered: cat + co + country + topic + event + score)
        card_tags = []
        card_tags.append(f"#cat/{cat['id']}")
        for t in item["tags"]:
            if t.startswith("co/") or t.startswith("country/"):
                if t not in card_tags:
                    card_tags.append(f"#{t}")
        for t in item["tags"]:
            if t.startswith("topic/") or t.startswith("event/"):
                if t not in card_tags:
                    card_tags.append(f"#{t}")
                if len(card_tags) >= 6:
                    break
        card_tags.append(f"#score/{score_label}")
        lines.append(" ".join(card_tags))
        lines.append("")
        # thumb
        thumb = item["thumb"] if item["thumb"] else ng_thumb(cat["id"], "featured" if i == 0 else "common")
        lines.append(f"![]({thumb})")
        lines.append("")
        for b in item["bullets"]:
            lines.append(f"- {md_markup(b)}")
        lines.append("")
        if "related" in item:
            rel = item["related"]
            lines.append(f"> [!tip] 🔗 関連: {rel['axis']}")
            lines.append(f"> {rel['note']}")
            lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)

def collect_tags_for_md(items, cat_id, issue_no):
    """frontmatter tags を圧縮版ルールで構築"""
    fixed = ["daily", "newsletter", "news-grasp", f"issue-{issue_no}"]
    cat_tags = [f"cat/{cat_id}"]
    co_tags = set()
    country_tags = set()
    person_tags = set()
    for item in items:
        for t in item["tags"]:
            if t.startswith("co/"): co_tags.add(t)
            elif t.startswith("country/"): country_tags.add(t)
            elif t.startswith("person/"): person_tags.add(t)
    result = fixed + cat_tags + sorted(co_tags) + sorted(country_tags) + sorted(person_tags)
    return result

def write_category_file(cat):
    tags = collect_tags_for_md(cat["items"], cat["id"], ISSUE_NO)
    tag_lines = "\n".join(f"  - {t}" for t in tags)
    genre_dir = {"fx": "FX", "ai": "AI", "it": "IT-Consulting", "economy": "Economy", "game": "Game"}[cat["id"]]
    genre_file = genre_dir
    front = f"""---
title: "News Grasp #{ISSUE_NO} — {cat['nameEn']}"
date: {ISSUE_DATE}
issue: {ISSUE_NO}
weekday: {WEEKDAY}
category: {cat['nameEn']}
categoryId: {cat['id']}
accent: "{cat['accent']}"
glyph: "{cat['glyph']}"
edition: Morning Edition
tags:
{tag_lines}
---

# {cat['glyph']} {cat['name']} ({cat['nameEn']}) — News Grasp #{ISSUE_NO}

#daily #news-grasp #issue-{ISSUE_NO}

"""
    body = build_category_md(cat)
    content = front + body
    path = os.path.join(BASE, "digest", genre_dir, f"{ISSUE_DATE}-{genre_file}.md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Written: {path}")

def write_summary_file():
    # collect all tags
    all_items = [item for cat in CATEGORIES for item in cat["items"]]
    fixed = ["daily", "newsletter", "news-grasp", f"issue-{ISSUE_NO}"]
    cat_tags = [f"cat/{cat['id']}" for cat in CATEGORIES]
    co_tags = set(); country_tags = set(); person_tags = set()
    for item in all_items:
        for t in item["tags"]:
            if t.startswith("co/"): co_tags.add(t)
            elif t.startswith("country/"): country_tags.add(t)
            elif t.startswith("person/"): person_tags.add(t)
    all_tags = fixed + sorted(cat_tags) + sorted(co_tags) + sorted(country_tags) + sorted(person_tags)
    tag_lines = "\n".join(f"  - {t}" for t in all_tags)
    cat_ids_csv = ", ".join(cat["id"] for cat in CATEGORIES)

    toc = "\n".join(
        f"- [[#{cat['glyph']}-{cat['index']}-{cat['name'].replace(' ', '-')}|{cat['glyph']} {cat['index']}. {cat['name']}]]"
        for cat in CATEGORIES
    )

    # categories body for summary
    cats_body = ""
    for cat in CATEGORIES:
        cats_body += build_category_md(cat) + "\n"

    # reflection sections
    sec_body = ""
    for i, s in enumerate(REFLECTION["sections"], 1):
        sec_body += f"### §{i:02d} {s['tag']} — {s['heading']}\n\n{md_markup(s['body'])}\n\n"

    # takeaways
    tkw = "\n".join(f"- **[{t['tag']}]** {md_markup(t['text'])}" for t in REFLECTION["takeaways"])

    # related
    rel_list = "\n".join(f"> - {r['date']} — [[{r['date']}|{r['title']}]]" for r in REFLECTION["related"])

    front = f"""---
title: "News Grasp #{ISSUE_NO} — 時勢を掴み、日々に新たに。"
date: {ISSUE_DATE}
issue: {ISSUE_NO}
weekday: {WEEKDAY}
edition: Morning Edition
publisher: News Grasp
tags:
{tag_lines}
categories: [{cat_ids_csv}]
theme: "{REFLECTION['title']}"
---

# News Grasp #{ISSUE_NO} — 時勢を掴み、日々に新たに。

#daily #news-grasp #issue-{ISSUE_NO}

> [!info] Today's Theme
> **{REFLECTION['title']}** — {REFLECTION['subtitle']}

## 📑 目次

{toc}
- [[#§-本日のテーマ考察|§ 本日のテーマ考察]]

---

"""
    body = cats_body
    body += f"""## § 本日のテーマ考察

*{REFLECTION['subtitle']}*

> {md_markup(REFLECTION['lead'])}

> [!quote] PULL QUOTE
> {md_markup(REFLECTION['pull_quote'])}

{sec_body}
### KEY TAKEAWAYS

{tkw}

> [!link] Related Issues
{rel_list}

---

← [[{PREV_DATE}|前号]] | [[{NEXT_DATE}|翌号]] →

*🤖 Auto-generated by News-Grasp Runner — `news-grasp-runner.bat` @ {ISSUE_DATE} 06:00 JST*
"""
    path = os.path.join(BASE, "digest", "Summary", f"{ISSUE_DATE}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(front + body)
    print(f"Written: {path}")

def write_articles_jsonl():
    path = os.path.join(BASE, "data", "articles.jsonl")
    entries = []
    for cat in CATEGORIES:
        for item in cat["items"]:
            entry = {
                "date": ISSUE_DATE,
                "seen_at": SEEN_AT,
                "genre": cat["nameEn"],
                "title": item["title"],
                "url": item["url"],
                "url_norm": item["url_norm"],
                "source": item["source"],
                "summary": item["bullets"][0][:80] if item["bullets"] else "",
                "entities": item["entities"],
                "topics": item["topics"],
                "industries": item["industries"],
                "events": item["events"],
                "tags": item["tags"],
                "thumb": item["thumb"]
            }
            entries.append(entry)
    with open(path, "a", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    print(f"Appended {len(entries)} entries to articles.jsonl")
    return entries

if __name__ == "__main__":
    for cat in CATEGORIES:
        write_category_file(cat)
    write_summary_file()
    entries = write_articles_jsonl()
    print("Done.")
