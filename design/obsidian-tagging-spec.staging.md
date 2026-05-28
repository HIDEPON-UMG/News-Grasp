# News Grasp — Obsidian タグ設計仕様

Obsidian Vault に取り込む際の `frontmatter` タグ生成ルールを定義する。
News-Grasp Runner（Windows タスクスケジューラ → `news-grasp-runner.bat` → `claude --print`）
が記事 JSON を生成するときも、本仕様の `entities` / `topics` / `industries` /
`events` フィールドを必ず埋めてもらうことを前提にする。

## 0. 設計方針（決定事項）

- **タグ値は日本語を優先**する（人名・国名・業界・トピック・イベント）。
- ただし**英字の固有名詞**（OpenAI / NVIDIA / GitHub / AWS / SAP / iPhone / Steam 等）と
  **国際略号**（FOMC / ECB / PBOC / IMF / OECD / NATO 等）は、無理に和訳せずそのまま使う。
- **階層タグ**（`cat/fx`, `co/トヨタ`, `country/日本` …）を採用。Obsidian の
  タグペインで折りたためるため。
- **共通固定タグ**（`daily` / `newsletter` / `news-grasp` / `issue-{号番号}`）は
  英字のままにする（Obsidian の慣用と Vault 全体の検索互換性のため）。
- **タグ値に使えない文字（重要）**
  - 半角スペース、`,`、`:`、`#`、`@` → **ハイフン `-`** に置換（`Switch 2` → `Switch-2`、`Apple Intelligence` → `Apple-Intelligence`）
  - スラッシュ `/` → **削除**して連結（`USD/JPY` → `USDJPY`）。階層タグの区切りと衝突するため
  - **ピリオド `.` → アンダースコア `_`** に置換（`GPT-5.5` → `GPT-5_5`、`Gemini-3.1` → `Gemini-3_1`、`v1.2.3` → `v1_2_3`）。Obsidian でタグの終端と解釈されてしまう
  - 中点 `・` と長音 `ー` はそのまま使ってよい

## 1. タグの分類とプレフィックス

| 種別 | プレフィクス | 値の規則 | 例 |
| --- | --- | --- | --- |
| 共通固定 | （なし） | 4 種・英字 | `daily` / `newsletter` / `news-grasp` / `issue-{号番号}` |
| カテゴリー | `cat/` | News Grasp の 5 カテゴリ id（既存） | `cat/fx` `cat/ai` `cat/it` `cat/econ` `cat/game` |
| 企業 | `co/` | 日本での一般通用表記 | `co/トヨタ` `co/任天堂` `co/NTTデータ` `co/OpenAI` `co/NVIDIA` `co/アクセンチュア` |
| 国 | `country/` | 日本語国名。EU は `EU` を許容 | `country/日本` `country/米国` `country/中国` `country/EU` `country/トルコ` |
| サービス／製品 | `svc/` | 固有名詞は原文、和名がある場合は日本語 | `svc/Claude` `svc/ChatGPT` `svc/iPhone` `svc/Switch-2` `svc/ウィッチャー` |
| 人名 | `person/` | 日本語フルネーム。中点 `・` を使ってよい | `person/植田和男` `person/ジェローム・パウエル` `person/サム・アルトマン` |
| ティッカー／通貨 | `ticker/` | 大文字の取引所コード or 通貨ペア | `ticker/NVDA` `ticker/USDJPY` `ticker/7974` |
| トピック | `topic/` | 日本語スラッグ。ただし国際略号は英字でよい | `topic/利下げ` `topic/利上げ` `topic/FOMC` `topic/規制` `topic/M&A` |
| 業界 | `industry/` | 日本語 | `industry/半導体` `industry/ゲーム` `industry/IT-コンサル` `industry/AI` |
| イベント種別 | `event/` | 日本語 | `event/決算` `event/製品発表` `event/政策会合` `event/規制公表` |
| 重要度 | `score/` | 日本語 1 文字 | `score/高` / `score/中` / `score/低` |

### 重要度のしきい値

`item.score` から自動付与する。

- `score >= 85` → `score/高`
- `65 <= score < 85` → `score/中`
- `score < 65` → `score/低`

## 2. スラッグ生成ルール

- **半角スペースは使用不可**。複合語はハイフンに置換する（`Switch 2` → `Switch-2`）。
- **スラッシュ `/` も使用不可**（階層タグの区切りと衝突する）。
  通貨ペア `USD/JPY` は `USDJPY` に圧縮する。
- **ピリオド `.` も使用不可**（Obsidian がタグの終端として解釈し、無効タグ表示になる）。
  アンダースコア `_` に置換する（`GPT-5.5` → `GPT-5_5`、`Gemini-3.1` → `Gemini-3_1`）。
- **中点 `・`** と **長音 `ー`** は OK（Obsidian で動作確認済み）。
- **日本企業／日本人**：日本語の正式表記を使う（`co/任天堂`, `co/トヨタ`,
  `co/NTTデータ`, `person/植田和男`）。
- **海外企業**：原語で広く流通している綴りを優先する（`co/OpenAI`, `co/NVIDIA`,
  `co/Apple`, `co/Microsoft`）。カナ表記が広く通用する場合（コンサル系・自動車系）は
  カナ（`co/アクセンチュア`, `co/デロイト`, `co/マッキンゼー`）。
- **政府機関・国際機関**は `co/` でも `topic/` でも構わないが、原則 `topic/` 側に
  寄せる（`topic/日銀`, `topic/FOMC`, `topic/ECB`, `topic/IMF`）。
- **国コード**は日本語国名（`country/日本`, `country/米国`, `country/中国`,
  `country/韓国`, `country/ドイツ`, `country/フランス`, `country/英国`,
  `country/メキシコ`, `country/トルコ`, `country/豪州`, `country/スイス`,
  `country/ポーランド`, `country/インド`, `country/EU`）。
- **通貨ペア**は `USDJPY` のように主要通貨を先、対通貨を後ろに連結し、`ticker/`
  プレフィックスを付ける（`ticker/USDJPY`, `ticker/EURUSD`）。
- **株式ティッカー**は米国株は大文字（`NVDA`, `AAPL`, `MSFT`）、東証は 4 桁コード
  （`7974` = 任天堂、`6758` = ソニー、`9613` = NTTデータ、`9684` = スクウェア・エニックス、`6702` = 富士通）。

## 3. 記事 JSON のスキーマ拡張

`window.NEWS_DATA.categories[].items[]` に以下のオプションフィールドを追加する。
無い場合は空配列扱いとする。

```js
{
  score: 95,
  time: '07:42',
  source: 'Bloomberg Markets',
  title: 'ドル円、151円台前半まで急伸後に反落──FOMC議事要旨で利下げ示唆',
  body: '...',
  thumb: '...',

  entities: {
    companies: [],                       // 企業（日本語ないし英字固有名詞）
    countries: ['米国', '日本'],          // 国（日本語）
    services: [],                        // サービス／製品
    people: [],                          // 人名（日本語）
    tickers: ['USDJPY'],                 // ティッカー／通貨ペア（大文字）
  },
  topics:     ['利下げ', 'FOMC'],         // 出来事・テーマ
  industries: [],                        // 業界
  events:     ['政策議事録'],              // イベント種別
}
```

## 4. frontmatter への展開ルール（**圧縮版・スマホ可読性優先**）

タグ数を絞る方針（旧仕様の 1/5〜1/3 程度）。Obsidian モバイルでタグペインが肥大
化するとスクロール挙動が破綻するため、**多くの軸は記事カード行のみで運用**し、
frontmatter には集約しない。

### Summary `digest/Summary/{YYYY-MM-DD}.md`

`tags:` に**含めるのは以下だけ**（重複排除、プレフィックス順 → 値の昇順）：

1. **共通固定**（4 件・先頭固定）：`daily` / `newsletter` / `news-grasp` / `issue-{ISSUE_NO}`
2. **カテゴリタグ**：当日扱った全カテゴリ id を `cat/{id}` で追加
3. **企業タグ**：全 20〜25 記事の `co/*` 集約
4. **国タグ**：全記事の `country/*` 集約
5. **人名タグ**：全記事の `person/*` 集約

`svc/` `ticker/` `topic/` `industry/` `event/` `score/*` は **frontmatter には入れない**
（記事カード行のみ）。

### カテゴリ別 `digest/{Genre}/{YYYY-MM-DD}-{Genre}.md`

Summary と同じ方針で、対象を当該カテゴリ 5 記事に絞る：

1. 共通固定 4 件
2. 当該カテゴリのみ `cat/{id}`
3. 当該カテゴリ 5 記事の `co/*` 集約
4. 当該カテゴリ 5 記事の `country/*` 集約
5. 当該カテゴリ 5 記事の `person/*` 集約

### 各記事カード行

メタ行の直下に **`#tag #tag ...` を 4〜7 個並べる**。優先順位：

1. `cat/{id}`（必須・当該カテゴリ 1 個）
2. `co/{値}` 主要 1〜3 個（記事の中核企業のみ）
3. `country/{値}` 0〜1 個（主題国のみ）
4. `topic/{値}` 0〜1 個（最も中核のテーマ 1 つだけ）
5. `event/{値}` 0〜1 個（該当する場合のみ）
6. `score/{高｜中｜低}`（必須・末尾固定 1 個）

`svc/` `ticker/` `industry/` `person/` は frontmatter にも記事カード行にも原則出さ
ない（必要な特定記事のみ例外的に追加可）。`tags` フィールド（記事 JSON 側）は内
部処理用に従来通り全種を保持してよいが、Markdown レンダリング時に上記フィルタを
必ず通す。

`tags:` リストはプレフィックス順 → 値の昇順でソートする（共通固定 4 件のみ先頭固
定）。

## 5. Runner 側プロンプト用の指示テンプレ（抜粋）

News-Grasp Runner が JSON を出力するときは、各 item に以下の `entities` / `topics`
/ `industries` / `events` を**必ず**含めてください。空でも `[]` を出力すること。

```text
- entities.companies: 記事に明示的に登場した企業／組織を日本語表記で列挙
                      （海外固有名詞は原文 OK：OpenAI, NVIDIA, GitHub 等）
- entities.countries: 記事の主題国・関連国を日本語で列挙（日本/米国/中国/EU 等）
- entities.services:  サービス・製品名を日本語ないし固有名詞で列挙
                      （半角スペースはハイフン化、ピリオドはアンダースコア化、
                       例：Switch-2、Apple-Intelligence、GPT-5_5、Gemini-3_1）
- entities.people:    記事に登場した個人を日本語フルネームで列挙
                      （海外要人は中点 ・ 区切りのカナ）
- entities.tickers:   株式ティッカー・通貨ペアを大文字で列挙
- topics:             記事の主題テーマを 1〜3 個。日本語推奨、国際略号 OK
- industries:         該当する業界を日本語で 0〜2 個
- events:             決算 / 製品発表 / 政策会合 / 規制公表 等の種別を 0〜2 個
```

スラッグの命名で迷ったら「**既存の Vault 内タグの綴りに合わせる**」を最優先する。
新しい綴りを作る場合は本仕様の「2. スラッグ生成ルール」に従い、半角スペースを
含めないこと。
