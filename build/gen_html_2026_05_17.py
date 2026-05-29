# coding: utf-8
"""
2026-05-17 HTML メール生成
prompts/email-template.html のプレースホルダを埋めて build/email.html を出力する
土曜日: FX / AI / IT-Consulting / Game (4カテゴリ)
"""
import re, os

BASE = r"C:\Users\hidek\Obsidian\New's Grasp\News-Grasp"
CDN  = "https://raw.githubusercontent.com/HIDEPON-UMG/news-grasp-assets/main"

ISSUE_DATE = "2026-05-17"
ISSUE_NO   = "20260517"
WEEKDAY    = "土"

CATEGORIES = [
  {
    "id": "fx", "name": "FX", "nameEn": "Foreign Exchange",
    "accent": "#B8860B", "glyph": "¥", "index": 1,
    "summary": "USD/JPY は 158 円台で週を終え、4 月末の実弾介入効果が半減。米インフレ再加速と FRB タカ派転換が円安の新たな原動力となり、160 円介入ラインへの再接近が鮮明。ECB は 6 月利上げ確率 86% でユーロ強含み、一方ポンドは週間最悪パフォーマンスを記録。",
    "items": [
      {
        "score": 90, "time": "08:30", "source": "OANDA MarketPulse",
        "title": "USD/JPY 158円台で週を終える — 米インフレ加速で4日続落・160円介入ライン維持",
        "url": "https://www.marketpulse.com/markets/chart-alert-usdjpy-plunging-below-158-on-suspected-intervention-watch-15750-support/",
        "thumb": "https://storage.googleapis.com/web-content.oanda.com/images/JPY_1920x1080-1.original.jpg",
        "bullets": [
          "[[USD/JPY]] は週末にかけて 158 円台に沈み、4 月 30 日〜5 月 1 日の実弾介入で叩き出した 156 円台から半値戻しを消化した形となった。__FRB 追加利上げ観測__が再燃し、日米金利差が依然 5% 超で円を押し下げ続ける構図。",
          "財務省は「24 時間体制で監視」の姿勢を崩さず、[[160 円]] 突破時の再介入を市場が警戒。ヘッジファンドは 160 円プット・オプションを積み増しており、攻防ラインまで約 2 円の距離に縮まった。",
        ]
      },
      {
        "score": 84, "time": "09:00", "source": "野村証券 NOMURAウェルスタイル",
        "title": "FOMCタカ派観測×ウォーシュ就任 — 円の行方と160円再突破シナリオ",
        "url": "https://www.nomura.co.jp/wealthstyle/article/0713/",
        "thumb": "https://www.nomura.co.jp/wealthstyle/article/0713/images/og_a_0713_01.png",
        "bullets": [
          "[[ウォーシュ FRB 議長]] は就任初日に「独立した行動者」を宣言、インフレ抑制を最優先課題と位置付け __データ次第での追加利上げ__ を示唆した。野村証券は USD/JPY 年末見通しを 152.5〜155 円として、介入有無と FOMC 決定が分水嶺と分析。",
          "S&P500 年末予想 7,700 への上方修正は AI 期待値が高金利コストを上回る「アンバランスな均衡」の継続を示し、ドル需要の根強さが [[円安]] 継続の主因であることを示している。",
        ]
      },
      {
        "score": 82, "time": "07:30", "source": "Forex.com",
        "title": "GBP/USD週間2%下落 — ドル全面高でポンドが重要水準攻防",
        "url": "https://www.forex.com/en-au/news-and-analysis/gbp-usd-technical-outlook-british-pound-struggles-at-key-level-as-downside-risks-begin-to-build-5-14-2026/",
        "thumb": f"{CDN}/ng-thumb-common-fx.jpg",
        "bullets": [
          "[[GBP/USD]] は過去数か月で最悪の週間パフォーマンスを記録、ドル高圧力が英ポンドの 3 月上昇トレンドを崩壊寸前まで押し下げた。BoE は 3.75% で据え置き継続の見通し、__ポンドの追い風材料が不在__。",
          "テクニカル的に重要サポート水準の攻防が続き、ブレイク時は次の下値目標へ急落の可能性。ドル全面高が続く今週の流れがポンドの反転を阻む。",
        ]
      },
      {
        "score": 79, "time": "10:00", "source": "EBC Financial Group",
        "title": "ECB預金金利2.00%据え置き、6月利上げ確率86%に上昇 — EUR/USD 1.1702で高値圏",
        "url": "https://www.ebc.com/forex/usd-jpy-forecast-the-bojs-160-intervention-danger-zone",
        "thumb": "https://www.ebc.com/upload/portal/20260424/7b4cd93be132f826315750393d2dcdc8.jpeg",
        "bullets": [
          "ECB は 4 月 30 日会合で [[預金金利 2.00%]] を据え置いたが、市場は 6 月 11 日会合での 25bp 利上げを 86% で織り込み済み。EUR/USD は 1.17 台で推移し、__ユーロ強含みのファンダメンタルズ__ を維持。",
          "EUR/JPY は 186 円台に位置し日本の貿易赤字拡大がユーロ円にも上昇圧力をかけている。対ドル・対ユーロ・対ポンドいずれでも円は軟調で「通貨として最も弱いカテゴリー」との分析。",
        ]
      },
      {
        "score": 76, "time": "08:00", "source": "OANDA MarketPulse",
        "title": "日銀0.75%据え置き・3名反対でタカ派ホールド継続 — 次の利上げ時期と円の行方",
        "url": "https://www.marketpulse.com/markets/usdjpy-the-intervention-aftermath-has-the-boj-bought-time-or-reversed-the-trend/",
        "thumb": f"{CDN}/ng-thumb-common-fx.jpg",
        "bullets": [
          "4 月 28 日の [[BOJ 決定会合]] は植田ガバナンス下で最大の意見分裂（3 名反対）を記録、9 月以降の利上げ観測が市場コンセンサスに。実弾介入後も円安圧力が収まらない構図は「__BOJ が時間を稼いだに過ぎない__」との見方を裏付ける。",
          "次の利上げタイミングと日米金利差縮小シナリオが円反転の鍵で、9 月の FOMC と BOJ 会合の同期に注目が集まる。[[植田総裁]] は「物価目標達成は視野」と発言済みで、次回会合でのシグナルが焦点。",
        ]
      },
    ]
  },
  {
    "id": "ai", "name": "AI", "nameEn": "Artificial Intelligence",
    "accent": "#2D5BB8", "glyph": "◆", "index": 2,
    "summary": "NVIDIA×OpenAI が 10GW 超インフラ展開を宣言し AI 投資の桁が変わった。Anthropic は Claude Mythos Preview で OSWorld 人間水準（79.6%）に到達し、中小企業向け Small Business ローンチと PwC 提携拡大で事業展開を加速。xAI 解散でコンピュートが Anthropic へ集約され、業界二強構造が確定しつつある。",
    "items": [
      {
        "score": 93, "time": "07:00", "source": "NVIDIA Newsroom",
        "title": "NVIDIA×OpenAI 10GW超インフラ展開 — 300億ドル出資・GB200 NVL72でGPT-5.5稼働",
        "url": "https://nvidianews.nvidia.com/news/openai-and-nvidia-announce-strategic-partnership-to-deploy-10gw-of-nvidia-systems",
        "thumb": "https://iprsoftwaremedia.com/219/files/202509/68d164573d63320d88f176b7_openai-and-nvidia/openai-and-nvidia_cbdd3def-150e-4b20-bf18-b15de5fb1eb0-prv.png",
        "bullets": [
          "[[NVIDIA]] と [[OpenAI]] は「AI 史上最大のインフラ展開」となる __10 ギガワット超の計算インフラ協定__ を締結、Vera Rubin プラットフォームで 2026 年 H2 に最初の 1GW を稼働予定。NVIDIA の AI 向け株式投資は 2026 年累計 400 億ドル超、うち OpenAI へ単独 300 億ドルを投下。",
          "[[GPT-5.5]] を搭載するコーディングエージェント Codex は GB200 NVL72 ラックスケールシステム上で動作し、推論効率で従来比 40% 向上を実現。オープンウェイト版 gpt-oss-120b / gpt-oss-20b も同時に公開され、エコシステム拡大を図る。",
        ]
      },
      {
        "score": 88, "time": "08:00", "source": "Air Street Press",
        "title": "Claude Mythos PreviewがOSWorldで79.6% — AIエージェントが汎用タスクで人間水準に並ぶ",
        "url": "https://press.airstreet.com/p/state-of-ai-may-2026",
        "thumb": "https://img.digitimes.com/newsshow/20260511pd229_files/3_b.jpg",
        "bullets": [
          "[[Claude Mythos Preview]] が OSWorld ベンチマーク 79.6%（人間ベースライン 72〜84%）を達成し、GPT-5.4（75%）・Claude Opus 4.6（72.7%）を上回る首位。__汎用 PC タスクでの人間水準__ 到達は「AGI への実用的な到達点」として業界に衝撃を与えている。",
          "サイバーセキュリティ特化の Project Glasswing の一環として限定公開され、32 ステップのエンドツーエンド攻撃経路を単月でクリア。OpenAI の GPT-5.5 も同 3 週後に同タスクをクリアし、フロンティアモデル 2 強が人間水準を超えた週となった。",
        ]
      },
      {
        "score": 86, "time": "09:00", "source": "Anthropic",
        "title": "Anthropic Claude for Small Business — 15エージェンティックワークフローで中小企業変革",
        "url": "https://www.anthropic.com/news/claude-for-small-business",
        "thumb": f"{CDN}/ng-thumb-ai.jpg",
        "bullets": [
          "Anthropic は QuickBooks・PayPal・HubSpot 上で動く財務・営業・人事・カスタマーサービス等 __6 業務領域 15 エージェンティックワークフロー__ を中小企業向けにローンチ。シカゴを皮切りに 10 都市ツアーで無料 AI フルエンシーセミナーを開催。",
          "[[Claude]] サブスクリプション型エージェント課金（API 準拠、6/15 施行）もセットで発表し、Pro ユーザーは月 $20 クレジットを取得。大企業中心だった AI エージェント展開が「中小企業の日常業務」へ降下してきた転換点。",
        ]
      },
      {
        "score": 83, "time": "10:00", "source": "PwC Newsroom",
        "title": "PwC×Anthropic提携拡大 — 全世界数十万人にClaudeを展開・30,000名認定プログラム",
        "url": "https://www.pwc.com/us/en/about-us/newsroom/press-releases/anthropic-pwc-expand-alliance-agentic-enterprise.html",
        "thumb": "https://www.pwc.com/glob/dpe/en/inline-images/adobestock-177641313.jpg",
        "bullets": [
          "PwC は [[Anthropic]] との戦略的提携を拡大し、Claude Code と Cowork を米国チームから全世界の __数十万人のプロフェッショナル__ に展開する計画を発表。30,000 名を対象とした Claude 認定プログラムを開始。",
          "成果連動型フィーモデルの試験も並行して進め、AI 生産性向上をクライアントへの付加価値として直接提示する新たなコンサルビジネスモデルを模索。コンサル大手では __最大規模の AI 変革プロジェクト__ となる。",
        ]
      },
      {
        "score": 78, "time": "11:00", "source": "Digitimes",
        "title": "xAI解散→AnthropicへコンピュートW移管 — マスク撤退でAI業界二強構造が確定",
        "url": "https://www.digitimes.com/news/a20260511PD229/anthropic-xai-elon-musk-computing-power-2026.html",
        "thumb": "https://img.digitimes.com/newsshow/20260511pd229_files/3_b.jpg",
        "bullets": [
          "イーロン・マスクは 5 月 6 日に xAI の独立会社化断念を表明し、保有する大規模 AI コンピュートを [[Anthropic]] へ移管することを決定。Anthropic の ARR は 190 億ドルに迫り、OpenAI（ARR 250 億ドル）との二強体制がさらに強固に。",
          "__業界再編__ の布石として Cohere×Aleph Alpha 合併（カナダ×ドイツ政府公認）も同週に発表され、欧米での AI 競争軸が地域覇者型に整備された。残存プレイヤーは「グローバル二強 vs 地域特化」の二極化路線を迫られている。",
        ]
      },
    ]
  },
  {
    "id": "it", "name": "IT-Consulting", "nameEn": "IT & Consulting",
    "accent": "#2E6B52", "glyph": "▲", "index": 3,
    "summary": "アクセンチュアと PwC が相次いで Anthropic との大型提携を具体化し日本でも 30,000 名規模の Claude 研修が始動。NTT DATA は 2030 年 EBITDA 1.2 兆円目標を公表し AI ネイティブビジネスへの転換を宣言。ビッグ 4 の上級幹部が AI スタートアップへ流出する「報酬モデル崩壊」の前兆も顕在化。",
    "items": [
      {
        "score": 88, "time": "09:00", "source": "アクセンチュア Newsroom",
        "title": "アクセンチュア×Anthropic日本本格始動 — 30,000名Claude研修、企業変革を一体支援",
        "url": "https://newsroom.accenture.jp/jp/news/2026/accenture-and-anthropic-to-drive-enterprise-reinvention-with-ai-in-japan",
        "thumb": "https://newsroom.accenture.jp/default-meta-image.png?width=1200&format=pjpg&optimize=medium",
        "bullets": [
          "[[アクセンチュア]] は 2026 年 5 月 1 日より「アクセンチュア Anthropic ビジネスグループ」の日本活動を本格始動。[[Claude]] を活用したコンサルティングから基幹系モダナイゼーションまでを一体提供し、__30,000 名のアクセンチュア専門家__ を対象に Claude 認定研修をグローバルで推進。",
          "全社 AI 変革の設計・実行・サイバーセキュリティ変革という 3 軸で大企業の DX 加速をワンストップで担う体制を整えた。2025 年 12 月に締結した複数年戦略パートナーシップが日本市場で本格的に動き出した形。",
        ]
      },
      {
        "score": 85, "time": "10:00", "source": "PwC Newsroom",
        "title": "PwC×Anthropic — Claude Code+Coworkで数十万人のコンサルタントを変革",
        "url": "https://www.pwc.com/us/en/about-us/newsroom/press-releases/anthropic-pwc-expand-alliance-agentic-enterprise.html",
        "thumb": "https://www.pwc.com/glob/dpe/en/inline-images/adobestock-177641313.jpg",
        "bullets": [
          "PwC と Anthropic の提携は企業向けエージェント型 AI の実装へシフトし、[[Claude Code]] と Cowork を米国チームから世界展開する段階に入った。__「ヒューマン×エージェント混成チーム」__ 体制を試験中、AI 起因の生産性向上をフィーに反映させる成果連動型試験も実施。",
          "30,000 名の認定プログラムは全業種のクライアント向けサービス品質を底上げする狙いがあり、競合他社（デロイト・EY）との差別化を急ぐ。コンサル大手の AI 武装競争はサービス品質の差別化から「AI 人材の排他的占有」へと次フェーズに移行している。",
        ]
      },
      {
        "score": 80, "time": "11:00", "source": "NTTデータグループ",
        "title": "NTT DATA 2030年EBITDA1.2兆円目標 — AIVista始動・フルスタックで「質の成長」へ",
        "url": "https://www.nttdata.com/global/ja/news/release/2026/050806/",
        "thumb": "https://www.nttdata.com/global/ja/-/media/assets/images/sns_share.png?rev=583d5951ace649c08be7a88679328a3b",
        "bullets": [
          "NTT データグループは AI 時代の「Quality Growth（質を伴った成長）」戦略を発表、AI×クラウドを軸に 2025 年度の [[EBITDA]] 約 8,000 億円を 2030 年度に 1.2 兆円へ拡大。業界特化型 AI とコアプラットフォームを組み合わせる「NTT DATA AIVista」を 2026 年夏から特定プロジェクトへ提供開始。",
          "コンサルからインフラまでのフルスタックサービスを強化し、アジア太平洋での最速成長市場（IT コンサル全体 2026 年$3,750 億規模）を取り込む戦略。__AI ネイティブビジネス__ の創出でプロジェクト型受託から継続収益モデルへの転換を急ぐ。",
        ]
      },
      {
        "score": 76, "time": "12:00", "source": "NTTデータ経営研究所",
        "title": "NTTデータ経営研究所 — 金融機関向けAI導入コンサル18サービス開始",
        "url": "https://www.nttdata-strategy.com/newsrelease/260507/",
        "thumb": "https://www.nttdata-strategy.com/images/ogp/ogp-common.jpg",
        "bullets": [
          "NTTデータ経営研究所は 5 月 7 日より、メガバンク・地方銀行・証券会社を対象に [[金融機関向け AI 導入コンサルティング]] 全 18 サービスの提供を開始。AI モデルの選定から運用・ガバナンス体制構築まで、__リスク管理と実効性のバランス__ を重視した金融規制対応型のサービスラインナップが特徴。",
          "各金融機関が個別に取り組むより低コストで AI 内製化を進められる支援モデルを提供、地銀の AI 格差解消も視野に入れた体系的サービス群。NTT データグループの成長戦略（AIVista）とも連携し、金融業界での AI 浸透を加速させる構え。",
        ]
      },
      {
        "score": 73, "time": "08:00", "source": "Business Insider Japan",
        "title": "ビッグ4上級幹部がAIスタートアップへ流出加速 — コンサル報酬モデル崩壊の前兆",
        "url": "https://www.businessinsider.jp/article/consulting-deloitte-pwc-ey-partner-c/",
        "thumb": f"{CDN}/ng-thumb-common-it.jpg",
        "bullets": [
          "デロイト米国法人元 AI 責任者がビッグ 4 を離れ AI 特化スタートアップのテラゴニアへ転じた事例を皮切りに、[[Big4]] パートナー級の人材流出が加速している。AI がルーティン調査・分析業務を代替することで、下位スタッフの採用削減と __上位専門家のスタートアップ移籍__ が同時進行する二極化が鮮明。",
          "KPMG が米アドバイザリー 400 名削減（5/16 報道）と同時期に、逆方向の人材吸引力を持つ AI ベンチャーが台頭しており、コンサル業界の報酬・キャリアモデルが変曲点を迎えている。削減と流出の同時進行が「コンサル業界の報酬モデル崩壊」の始まりを示唆。",
        ]
      },
    ]
  },
  {
    "id": "game", "name": "ゲーム", "nameEn": "Gaming",
    "accent": "#5E3D8C", "glyph": "●", "index": 4,
    "summary": "Nintendo Switch 2 の 5 月ラッシュが本格化し、インディ・ジョーンズ・ヨッシー・テイルズ・オブ・アライズが相次いで投入される。カプコン「プラグマタ」は 16 日で 200 万本を達成し完全新規 IP の快進撃が続く。任天堂は秋のサプライズを示唆し、スクウェア・エニックスはマルチプラット展開を加速。",
    "items": [
      {
        "score": 88, "time": "08:00", "source": "Game Rant",
        "title": "Nintendo Switch 2 — 5月に6本大型タイトルが集中投入、ヨッシー・インディ・テイルズが核",
        "url": "https://gamerant.com/nintendo-switch-2-new-games-coming-out-soon-list-may-2026/",
        "thumb": "https://static0.gamerantimages.com/wordpress/wp-content/uploads/2026/04/switch-2-games-releasing-may-2026.jpg?w=1600&h=900&fit=crop",
        "bullets": [
          "[[Nintendo Switch 2]] は 5 月に 6 本の大型タイトルを集中投下、ヨッシーと不思議な本（5/21）・インディ・ジョーンズ（5/12 済）・テイルズ オブ アライズ BE（5/22）が核。Mixtape（5/7 済）・Stray（5/28）・リズム天国 ミラクルスターズも控え __Switch 2 の月間タイトル密度__ が旧世代比で過去最高に。",
          "任天堂は 9 つのリリースを 5 月に確認済みと正式発表しており、ホリデー商戦に向けたラインナップ充実を急いでいる。物語性アドベンチャー・シミュレーター・アクション・パズルと幅広いジャンルをカバーし、旧 Switch ユーザーの移行を促進する戦略。",
        ]
      },
      {
        "score": 85, "time": "09:00", "source": "Gamestalk",
        "title": "カプコン「プラグマタ」16日で200万本突破 — 完全新規IPがSwitch2×PS5でグローバルヒット",
        "url": "https://gamestalk.net/capcom-repeat-sales-2027/",
        "thumb": "https://gamestalk.net/wp-content/uploads/2026/05/Capcom_260513.jpg",
        "bullets": [
          "[[カプコン]] の完全新規 IP「プラグマタ」は 4 月 17 日発売後わずか 16 日で __販売本数 200 万本__ を突破、Switch 2 版（4/24）も加えた同時多発展開が功を奏した。完全新規 IP がこれだけの速度で 200 万本を達成した事例は稀で、カプコンのブランド力と IP 開発力を示す。",
          "2027 年 3 月期はリピートタイトルが 5,300 万本・新作 1,200 万本の販売計画で、「稼ぐ構造の 8 割はバックカタログ」という戦略が明確化。鬼武者 Way of the Sword（2026 年内予定）も同時進行でアクション IP の複数展開体制が整っている。",
        ]
      },
      {
        "score": 82, "time": "10:00", "source": "Kotaku",
        "title": "任天堂、秋にさらなるSwitch2サプライズを示唆 — Star Fox・スプラトゥーンレイダーズを続々予告",
        "url": "https://kotaku.com/nintendo-hints-at-more-big-switch-2-games-this-fall-and-the-ongoing-importance-of-its-last-gen-console-2000695687",
        "thumb": "https://kotaku.com/app/uploads/2026/05/Switch-2-Question-1200x675.jpg",
        "bullets": [
          "任天堂は [[Star Fox]] を Switch 2 向けに 6 月 25 日、スプラトゥーン レイダーズを 7 月 23 日に投入、さらに __秋に未発表の大型タイトル__ を複数準備していることを示唆。Fire Emblem: Fortune's Weave の 2026 年内発売も視野に、年間を通じたラインナップ戦略で旧 Switch ユーザーの移行を促進。",
          "旧 Switch も引き続き「重要なプラットフォーム」と位置付け、両ハードの共存期間を意図的に設けることで急激な市場縮小を避ける狙い。[[Switch 2]] のフォームファクターが既存ユーザーの移行コストを低く抑えていることも移行率を高める要因。",
        ]
      },
      {
        "score": 78, "time": "11:00", "source": "gamebiz.jp",
        "title": "ウマ娘 Gold-Triumph新曲追加&GWスタミナ半減キャンペーン — ソーシャルゲーム収益維持の施策",
        "url": "https://gamebiz.jp/news/425182",
        "thumb": "https://i3.gamebiz.jp/media/aac40ca6-3ac0-490f-9a13-836b059ae5ed.jpg",
        "bullets": [
          "[[Cygames]] の「ウマ娘 プリティーダービー」は 4 月 30 日から Gold-Triumph（6 名キャスト）新曲を追加、YouTube MV と音楽配信を同日展開しキャラクター IP の多角収益を継続。__スタミナ消費 1/2 キャンペーン__ と Select Pickup スタンプガチャ（〜5/29）で GW 課金モチベーションを維持。",
          "他の大型タイトル投入ラッシュ（Switch 2 等）との競合の中でも、既存 IP の深掘り施策がソシャゲの生命線となっている。運営コストの低い楽曲追加でコンテンツ量を補いながら ARR を安定させる「コスト効率重視」の運営モデルが機能している。",
        ]
      },
      {
        "score": 75, "time": "07:30", "source": "スクウェア・エニックス",
        "title": "FF VII Rebirth Switch2/Xbox/PC版6月3日発売確定 — スクウェア・エニックスのマルチプラット戦略加速",
        "url": "https://www.hd.square-enix.com/jpn/news/",
        "thumb": f"{CDN}/ng-thumb-common-game.jpg",
        "bullets": [
          "[[スクウェア・エニックス]] は「ファイナルファンタジー VII リバース」の Nintendo Switch 2・Xbox Series X|S・Windows 版を 6 月 3 日に発売確定と発表、PS5 独占期間終了後の全面展開を明示。2026 年 3 月期 HDゲームで __営業利益 128 億円__ （前年同期 46 億円）と大幅改善。",
          "2027 年 1 月予定の FF XIV 拡張「白銀のワンダラー」と 2026 年 8 月の Switch 2 版も控え、年間リリースカレンダーを充実させている。PS5 専占期間の短縮（本作は約 1 年）はマルチプラット収益最大化への戦略転換を示しており、[[マルチプラット戦略]] が 2026 年以降のスクエニの標準手法となっていく。",
        ]
      },
    ]
  },
]

REFLECTION = {
  "title": "エコシステム占有の時代",
  "subtitle": "AI もゲームもコンサルも、2026 年の覇権は「どこに根を張ったか」で決まる",
  "lead": "本日 4 分野・20 本のニュースから浮かび上がる最大のテーマは [[AI エコシステムの占有競争]] と [[プラットフォーム支配の固定化]] の同時進行である。NVIDIA×OpenAI の 10GW 宣言と Switch 2 の月間 6 本投入は、全く異なる産業で同じ文法（占有率の確立）が動いている証拠である。以下、各カテゴリを横断して読み解く。",
  "pull_quote": "「単一の強い製品」から「__エコシステムでの占有率__」へ── AI もゲームもコンサルも、2026 年の覇権は「どこに根を張ったか」で決まる日。",
  "sections": [
    {
      "tag": "総論", "accent": "#1A1A1A",
      "heading": "「性能」から「占有」へ。プラットフォーム経済が成熟期に入った日",
      "body": "NVIDIA×OpenAI の 10GW 宣言は、AI を「モデルの性能競争」から「__計算資源と展開網の占有競争__」へと転換する宣言である。同日、Anthropic が中小企業向けエージェント群と PwC・アクセンチュアへのコンサル浸透を同時に進めた構図は偶然ではない。[[エコシステム占有]] こそが 2026 年の支配原理であり、ゲーム業界の Switch 2 戦略と本質的に同型である。この法則を理解しないまま「性能の良い製品」だけを持つプレイヤーは、プラットフォームオーナーに征服される側になる。"
    },
    {
      "tag": "為替・経済", "accent": "#B8860B",
      "heading": "ウォーシュ就任後の「強いドル時代」と円の受難",
      "body": "USD/JPY 158 円台は「介入効果の剥落」を示す指標として読むより、__ウォーシュ FRB × インフレ再加速__ が生み出す構造的円安の到達点として認識すべきだ。ECB の 6 月利上げ確率 86% とポンドの週間最悪パフォーマンスが示すように、ドル独歩高ではなく「中銀政策格差の拡大」が 2026 年後半の通貨相場の軸になる。日銀 9 月の利上げシナリオ実現が [[円反転]] の唯一の鍵だ。それまでは「介入ごとに半値戻し」のパターンを繰り返す。"
    },
    {
      "tag": "AI・技術", "accent": "#2D5BB8",
      "heading": "Anthropicの「全方位制圧」と xAI の消失が示す二強時代の固定化",
      "body": "Claude Mythos Preview が OSWorld で人間水準に並んだ数字（79.6%）だけを見てはいけない。注目すべきは、Anthropic が同週に【中小企業・コンサル大手・サイバーセキュリティ】という異なる 3 市場を同時攻略し、__xAI のコンピュートまで吸収__ した点にある。OpenAI が NVIDIA と 10GW インフラを固め Anthropic が人材とエコシステムを固める──この [[二強構造の固定化]] が、後続プレイヤー（Cohere/Aleph Alpha 合併等）を「地域覇者」路線へ追い込んでいる。"
    },
    {
      "tag": "産業・業界", "accent": "#2E6B52",
      "heading": "コンサル界の「AI武装競争」と人材流出の逆説",
      "body": "アクセンチュアが 30,000 名を Claude で武装する一方で、ビッグ 4 の上級幹部が AI スタートアップに流出している逆説は「[[コンサル業界]] の報酬モデル崩壊」の予兆である。AI がルーティン業務を代替することで若手採用が減り（KPMG 400 名削減）、同時にトップ人材の外部吸引力が高まる──この二極化が加速すると、コンサル業界は「大手＋AI 特化ブティック」に収斂する可能性がある。[[NTT DATA]] の EBITDA 1.2 兆円目標は SI 大手がこの再編を自覚した上での先手であろう。"
    },
    {
      "tag": "明日へ", "accent": "#C9B98A",
      "heading": "Switch 2の「プラットフォーム占有教科書」をAIに読み解く",
      "body": "任天堂の Switch 2 戦略は「タイトル密度」「後方互換」「サプライズ予告」の三段構えで旧ユーザーを囲い込む教科書的手法である。[[AI]] もゲームも「どの OS に根を張るか」が勝負の本質であり、カプコン「プラグマタ」の 16 日 200 万本が示す通り、__プラットフォームとの同時展開__ がヒットを最大化する。Anthropic が Claude を QuickBooks・Salesforce・HubSpot に埋め込む戦略は、スクウェア・エニックスが Switch 2 と Xbox を同時に選んだ理由と同じロジックである。"
    },
  ],
  "takeaways": [
    {"tag": "為替", "color": "#B8860B", "text": "USD/JPY 158 円台は「介入の限界」を示しており、[[ウォーシュ]] 就任後の FRB タカ派転換と日銀 9 月利上げ待ちの間、__160 円という重力__ がゆっくり働き続ける。"},
    {"tag": "AI",   "color": "#2D5BB8", "text": "Anthropic は Claude Mythos で「性能証明」、Small Business と PwC で「市場占有」、xAI コンピュートで「資源確保」の三拍子を同時に決め、__二強確定の決定打__ を打った週だった。"},
    {"tag": "産業", "color": "#2E6B52", "text": "ゲームもコンサルも「プラットフォームに根を張った者が勝つ」の法則が貫いており、[[Nintendo Switch 2]] と Anthropic エコシステムは 2026 年の覇権争いを象徴する双極である。"},
  ],
  "related": [
    {"date": "2026-05-16", "title": "S&P500 7,500 超え・ウォーシュ就任・AI コンサル再編"},
    {"date": "2026-05-15", "title": "ゲーム決算・BOJ 据え置き・AI エージェント競争"},
    {"date": "2026-05-11", "title": "Claude Mythos Preview・AI サイバー防衛・ベッセント訪日"},
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
    import re, os
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
