# Watchlist

Routine がここを読み込み、毎朝の検索対象として使う。**自由に追加・削除して commit すれば翌日から反映される。**

各ジャンル下にプレーン箇条書きで企業・タイトル・キーワードを並べる。1 行 1 項目。`#` 始まりはコメント扱いで Routine から無視される。

---

## AI

- Anthropic
- OpenAI
- Google DeepMind
- Meta AI
- NVIDIA
- AMD

### 汎用キーワード

- 大規模言語モデル
- マルチモーダル
- 推論モデル
- AIエージェント
- LLM benchmark
- AI safety / alignment

---

## IT-Consulting

- Accenture
- デロイトトーマツ
- PwC
- EY
- マッキンゼー
- BCG
- 米カレントコンサルティング
- NTTデータ
- CTC
- 富士通
- NEC

### 汎用キーワード

- DX
- 生成AI導入
- システムインテグレーター
- IT投資
- コンサル業界 動向

---

## FX

主要通貨ペアと中銀政策。**毎日掲載（独立カテゴリ）**。

- USD/JPY
- EUR/USD
- EUR/JPY
- GBP/USD
- USD/CNH
- AUD/USD

### 中銀

- 日銀（BOJ）
- 米FRB
- ECB
- BOE
- 人民銀行
- RBA

### 汎用キーワード

- 為替介入
- 金利差
- カバードコール / オプション
- ヘッジファンド ポジション

---

## Mobility

毎日掲載。**EV / 自動運転 / MaaS / 完成車・部品サプライヤー**を独立カテゴリで追跡。
エントリーが少ない日が続いたため、情報源（媒体）と対象企業・キーワードを拡充した（2026-05-30）。

### 優先情報源（媒体）

- Electrek
- InsideEVs
- CleanTechnica
- The Verge  # Transportation
- TechCrunch  # Transportation / Mobility
- Reuters  # Autos
- Bloomberg  # Hyperdrive
- Automotive News
- Auto Connected Car News
- 日経 xTECH / 日経Automotive
- レスポンス (Response.jp)
- Car Watch (Impress)

### 完成車メーカー

- Tesla
- トヨタ
- 日産
- ホンダ
- スバル
- マツダ
- 三菱自動車
- BMW
- メルセデス・ベンツ
- フォルクスワーゲン (VW)
- BYD (比亜迪)
- 現代自動車 (Hyundai)
- 起亜 (Kia)
- Stellantis
- Ford
- GM (General Motors)
- NIO (蔚来)
- XPeng (小鵬)
- Li Auto (理想汽車)
- 吉利 (Geely)
- Zeekr
- Xiaomi EV (小米汽車)
- Leapmotor (零跑)
- Rivian
- Lucid Motors

### 自動運転・モビリティサービス

- Waymo
- Cruise
- Zoox  # Amazon
- Uber
- Lyft
- DiDi
- Aurora
- Mobileye
- Pony.ai (小馬智行)
- WeRide (文遠知行)
- Baidu Apollo (百度)
- Nuro
- May Mobility
- Wayve

### 電池・サプライヤー

- CATL
- BYD  # 電池部門
- LG Energy Solution
- Samsung SDI
- SK On
- Panasonic Energy
- パナソニック ホールディングス
- QuantumScape
- デンソー
- ボッシュ (Bosch)
- Continental
- ZF
- Aptiv
- Valeo
- Magna
- Hyundai Mobis
- NVIDIA  # DRIVE / 車載
- Qualcomm  # Snapdragon Digital Chassis

### 汎用キーワード

- 自動運転
- 自動運転レベル4 / レベル5
- ADAS
- LiDAR
- EV化
- EV販売台数
- 商用EV / EVトラック
- MaaS
- Robotaxi
- 配車サービス
- ライドシェア
- 電池技術
- 全固体電池
- LFP電池
- 超急速充電
- 充電インフラ
- バッテリースワップ
- V2G
- 空飛ぶクルマ
- eVTOL
- 自動車補助金
- 燃費規制
- リコール

---

## Manufacturing

平日（月〜金）に掲載。**OEM の生産・開発、Tier1/2、生産技術、車載半導体・電池素材を "作り手" 視点で追跡**。
Mobility が「使う／乗る／サービスを受ける」消費者視点なのに対し、Manufacturing は「作る／誰が作る／作る計画をどうするか」という産業・技術観測者の視点で別立てする。
Mobility の「電池・サプライヤー」節と対象企業が重なるが、`tools/dedup.py` が全カテゴリ横断で URL/タイトル照合するため同一記事の重複掲載は構造的に起きない（どちらに振るかは routine-system.md 3-A.1-M の境界ルールで決める）。

### 優先情報源（媒体）

- 日経 xTECH / 日経Automotive / 日経ものづくり
- 日刊工業新聞
- 各社 IR・適時開示 / プレスリリース
- Google Patents / J-PlatPat（特許）
- EE Times Japan
- 日経エレクトロニクス
- Nikkei Asia
- レスポンス (Response.jp)
- 地域局（静岡新聞SBS 等）

### 完成車 OEM（生産・開発視点）

- トヨタ
- レクサス
- 日産
- ホンダ
- スバル
- マツダ
- 三菱自動車
- スズキ
- ダイハツ
- BYD
- フォルクスワーゲン (VW)
- テスラ  # 工場・生産

### Tier1 / Tier2 サプライヤー

- デンソー
- アイシン
- 豊田自動織機
- ジェイテクト
- 豊田合成
- トヨタ紡織
- 日本電産 (ニデック)
- ボッシュ (Bosch)
- コンチネンタル (Continental)
- ZF
- ヴァレオ (Valeo)
- マグナ (Magna)
- 現代モービス (Hyundai Mobis)

### 車載半導体・電池素材

- ルネサス
- ローム
- 三菱電機  # パワー半導体
- 富士電機
- 東芝  # パワー半導体
- インフィニオン (Infineon)
- STマイクロ (STMicroelectronics)
- オンセミ (onsemi)
- TSMC  # 車載
- CATL
- パナソニックエナジー
- 日本製鉄  # 電磁鋼板
- JFE
- 旭化成  # セパレータ

### 生産技術キーワード

- ギガキャスト / メガキャスト
- 一体成形
- 全固体電池 量産
- LFP 内製
- 工場新設 / マザー工場
- 設備投資
- 内製化
- 歩留まり
- EV 専用ライン
- 生産能力
- サプライチェーン
- 車載半導体 内製
- 特許出願
- リコール対応  # 品質

---

## Economy

平日（月〜金）に掲載。**為替は独立カテゴリ「FX」へ移動済み**。

### 優先情報源（媒体）

- 日経新聞
- ロイター
- Yahoo!ニュース
- NewsPicks  # ※有料記事は公開部分のみ

### 株価指数

- 日経平均
- TOPIX
- S&P500
- NASDAQ Composite
- 上海総合
- DAX

### 汎用キーワード

- 米FRB 利上げ / 利下げ
- 日銀 金融政策
- 決算速報
- マクロ経済 指標
- 雇用統計
- CPI / PCE

---

## Game

- ウマ娘
- 任天堂
- SQUARE ENIX
- SIE (Sony Interactive Entertainment)
- Cygames
- カプコン
- コナミ
- テンセント
- ネットイース
- miHoYo

### 汎用キーワード

- ソーシャルゲーム 売上
- 新作 発表
- アップデート
- esports
- ゲーム業界 M&A
