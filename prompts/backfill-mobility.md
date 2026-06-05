# Mobility カテゴリ単独 backfill prompt

本リポジトリ内の `prompts/routine-system.md` を Read で読み込み、その指示に**Mobility カテゴリだけに限定**して厳密に従ってください。
これは当日 (今日 = **2026-05-28**) ではなく、引数で指定された **過去日の Mobility digest を遡及生成する backfill** です。

## 日付パラメータ (routine-system.md「ステップ 1: 当日情報の準備」を上書き)

**↓ 末尾に target_date が prompt 引数として連結されます。1 ファイル目の冒頭に `TARGET_DATE=<YYYY-MM-DD>` という形で確認してから処理開始してください。**

- 当日 = 引数で渡された `<TARGET_DATE>` (例: `2026-05-22`)
- 曜日 = `<TARGET_DATE>` の JST 曜日を計算
- issue 番号 = `<TARGET_DATE>` の `YYYYMMDD`
- **対象カテゴリ = Mobility のみ** (他カテゴリは生成しない)
- 件数 = **5 件固定**

## backfill 固有の遵守事項

### 1. WebSearch クエリ

必ず「`<TARGET_DATE>` の YYYY-MM-DD」「`May DD 2026`」等の日付トークンを含めること。`24 hours ago` ではなく **`<TARGET_DATE>` の日付を含むニュース**を能動的に検索する。

watchlist の Mobility セクション (`data/watchlist.md` の `## Mobility` 節) と汎用キーワード (EV / 自動運転 / MaaS / Robotaxi / 完成車・部品サプライヤー) で広めに 20-30 件収集。

### 2. 過去 90 日 dedup チェック

`data/articles.jsonl` のうち、`seen_at` が `<TARGET_DATE>T00:00:00+09:00` **以前**のエントリ全件を dedup 対象とする (= 後追いの backfill エントリも、過去日として正しく考慮)。

### 3. ステップ 3-B サムネ取得

必ず `tools/fetch_ogp.py` を Stage 1 で先に実行し、`ng-thumb-common-mobility.jpg` を digest md に**直接書き込まない**こと (2026-05-25 強化ルール)。`thumb` フィールドには段階 1 の戻り値 (実 OGP URL or null) を入れる。

### 4. 既存ファイル check

着手前に必ず以下を確認:

- `digest/Mobility/<TARGET_DATE>-Mobility.md` が既に存在 → **何もせず即終了**してください (上書きしない)。最終出力: `⏭️ SKIP: digest/Mobility/<TARGET_DATE>-Mobility.md already exists`
- 存在しない → 通常通り生成へ進む

### 5. 生成物 (Mobility 単独)

- `digest/Mobility/<TARGET_DATE>-Mobility.md` (新規 1 ファイル)
- `data/articles.jsonl` に **Mobility 5 件のみ**追記 (`seen_at` は当日 06:00 JST 固定 = `<TARGET_DATE>T06:00:00+09:00`)
- **他カテゴリの digest 生成・追記は一切しない**
- `data/_status.md` の更新は不要 (= backfill は status 履歴に残さない)

### 6. routine-system.md ステップ範囲

以下のステップのみ実行し、Summary 生成 / page 生成は**全てスキップ**:

- ステップ 1 (当日情報の準備) — 引数で上書き
- ステップ 2 (状態ファイル取得)
- ステップ 3 (Mobility カテゴリ 1 つのみ)
- **スキップ**: ステップ 4 (テーマ考察 γ schema) / `tools/generate_pages.py`
- ステップ 6 (commit) — 下記 7 番参照

> **メール配信は 2026-06-05 廃止**: ステップ 5/7 (メール生成・SMTP 送信) と `tools/send_email.py` / `tools/generate_email.py` / `build/email.html` は機能ごと削除済み。

### 7. commit

routine-system.md ステップ 6 通り、1 ファイル単位で commit:

```
git -c user.name="HIDEPON" -c user.email="hideki.kusunoki@gmail.com" add \
  digest/Mobility/<TARGET_DATE>-Mobility.md data/articles.jsonl
git -c user.name="HIDEPON" -c user.email="hideki.kusunoki@gmail.com" commit \
  -m "daily: <TARGET_DATE> Mobility backfill (5 articles)"
```

**git push は絶対に実行しない** (後段で別プロセスがまとめて push する設計)。

## 最終出力フォーマット

完了後、以下のサマリを末尾に必ず出力:

- 既存スキップ時: `⏭️ SKIP: digest/Mobility/<TARGET_DATE>-Mobility.md already exists`
- 新規生成完了時: `✅ commit: <sha>` (commit hash 7 桁)

## スコープ外 (絶対に触らない)

- `digest/Summary/<TARGET_DATE>.md` (= γ schema 7 sections の旧版で残置)
- `digest/{FX,AI,IT-Consulting,Economy,Game}/<TARGET_DATE>-*.md` (他カテゴリ)
- `tools/generate_pages.py` / `docs/` 配下 (page 生成は後段で親 Claude が一括実行)

---

**TARGET_DATE は本 prompt の末尾に追記されます。↓**

TARGET_DATE = 
