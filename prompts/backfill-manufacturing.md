# Manufacturing カテゴリ単独 backfill prompt（6/3 試験生成）

本リポジトリ内の `prompts/routine-system.md` を Read で読み込み、その指示に **Manufacturing カテゴリだけに限定**して厳密に従ってください。
これは当日ではなく、**過去日（試験対象 = 2026-06-03）の Manufacturing digest を遡及生成する backfill** です。
製造カテゴリは 2026-06-03 に新設したばかりで本番運用記事がまだ無いため、これは**初回の試験生成**です。

> **実行前提**: 本 prompt はローカル runner（`codex exec`、リポジトリを Read できる環境）で実行します。ブラウザ版 Claude では使えません（ローカルファイルにアクセスできないため）。

## 日付パラメータ

- 当日（TARGET_DATE）= **2026-06-03**
- 曜日 = 2026-06-03 の JST 曜日を計算（本番では製造は平日=月〜金のみだが、本件は**試験生成なので曜日に関わらず生成してよい**）
- issue 番号 = `20260603`
- **対象カテゴリ = Manufacturing のみ**（他カテゴリは生成しない）

## ★ Manufacturing 固有：重要度スコアは 3-A.1-M を使う（最重要）

**標準スコア 3-A.1 は使わないでください。** `prompts/routine-system.md` の新節 **「3-A.1-M Manufacturing（製造）カテゴリの重要度スコア特則」** に厳密に従って採点します。要点（必ず routine-system.md 本文で確認）:

- 軸 = **産業インパクト 30% / 技術的新規性・深度 25% / 戦略的シグナル 25% / 一次情報度 20%**
- **話題性（拡散量）を軸から外す**（拡散しなくても構造的に重要な記事＝デンソー/アイシン特許分析型を拾う）
- **時間減衰なし**（特許分析・戦略シフトはストック型。過去日 backfill でも価値が落ちない＝本件と相性が良い）
- 読者像 = 産業・技術の観測者（製造業従事者／技術者／事業・経営企画）。「何を買えるか」でなく「製造業の競争力・技術蓄積・サプライチェーンが今どう動いているか」を最上位に
- **Mobility 境界**: 使う/乗る/サービスを受ける→Mobility（拾わない）、作る/誰が作る/作る計画→Manufacturing（拾う）。境界記事は「製品計画の意思決定が主題なら Manufacturing」。`tools/dedup.py` が全カテゴリ横断照合するので Mobility と重複掲載は構造的に起きない

## backfill 固有の遵守事項

### 1. WebSearch クエリ

「2026-06-03」「June 3 2026」等の日付トークンを含めて、**2026-06-03 前後のニュース**を能動的に検索する（`24 hours ago` ではなく）。

`data/watchlist.md` の `## Manufacturing` 節（優先情報源＝日経xTECH/日経Automotive/日経ものづくり/日刊工業/各社IR・適時開示/Google Patents・J-PlatPat 等、完成車OEM・Tier1/2・車載半導体・電池素材、生産技術KW＝ギガキャスト/全固体電池量産/車載半導体内製/特許出願/設備投資 等）を主軸に、広めに 15-30 件収集してから 3-A.1-M で採点・選定する。

### 2. 件数（無理に埋めない）

- 目標 = **5 件程度**。ただし 3-A.1-M の方針どおり **該当が薄ければ 3 件で可**（質の低い続報で水増ししない）。
- 3-A.5-F の「満たなくても OK」を製造では特に徹底。

### 3. 過去 90 日 dedup チェック

`data/articles.jsonl` のうち `seen_at` が `2026-06-03T00:00:00+09:00` **以前**のエントリ全件を dedup 対象とする。Mobility 既存記事と内容が重なる場合は、上記「Mobility 境界」で Manufacturing 側の主題（作り手・生産技術）に該当するもののみ採用。

### 4. ステップ 3-B サムネ取得

必ず `tools/fetch_ogp.py` を Stage 1 で先に実行する。`ng-thumb-common-manufacturing.jpg` 等の NG プレースホルダを digest md に**直接書き込まない**。`thumb` フィールドには段階 1 の戻り値（実 OGP URL or null）を入れる。

### 5. 既存ファイル check

着手前に必ず確認:

- `digest/Manufacturing/2026-06-03-Manufacturing.md` が既に存在 → **何もせず即終了**。最終出力: `⏭️ SKIP: digest/Manufacturing/2026-06-03-Manufacturing.md already exists`
- 存在しない → 通常通り生成へ進む

### 6. 生成物（Manufacturing 単独）

- `digest/Manufacturing/2026-06-03-Manufacturing.md`（新規 1 ファイル。frontmatter の `categoryId: manufacturing` を**必ず**含める＝欠落は summary 誤判定の原因）
- `data/articles.jsonl` に **Manufacturing 記事のみ**追記（`genre` は **`"Manufacturing"`** 表記、`seen_at` は `2026-06-03T06:00:00+09:00` 固定）
- 階層タグは `cat/manufacturing` を必ず付与（`prompts/obsidian-tagging-spec.md` 準拠）
- **他カテゴリの digest 生成・追記は一切しない**
- `data/_status.md` の更新は不要

### 7. routine-system.md ステップ範囲

以下のみ実行し、Summary 生成 / page 生成は**全てスキップ**:

- ステップ 1（当日情報の準備）— 上記パラメータで上書き
- ステップ 2（状態ファイル取得）
- ステップ 3（Manufacturing カテゴリ 1 つのみ）＋ **3-A.1-M スコア特則**
- **スキップ**: ステップ 4（テーマ考察 γ schema）/ `tools/generate_pages.py`
- ステップ 6（commit）— 下記 8 番参照

> **メール配信は 2026-06-05 廃止**: ステップ 5/7 (メール生成・SMTP 送信) と `tools/send_email.py` / `tools/generate_email.py` / `build/email.html` は機能ごと削除済み。

### 8. commit

1 ファイル単位で commit（push はしない）:

```
git -c user.name="HIDEPON" -c user.email="hideki.kusunoki@gmail.com" add \
  digest/Manufacturing/2026-06-03-Manufacturing.md data/articles.jsonl
git -c user.name="HIDEPON" -c user.email="hideki.kusunoki@gmail.com" commit \
  -m "daily: 2026-06-03 Manufacturing backfill (試験生成)"
```

**git push は絶対に実行しない**（後段で別プロセスがまとめて push する設計）。

## スコープ外（絶対に触らない）

- `digest/Summary/2026-06-03.md`（既存の総括は残置）
- `digest/{FX,AI,IT-Consulting,Mobility,Economy,Game}/2026-06-03-*.md`（他カテゴリ）
- `tools/generate_pages.py` / `docs/` 配下（page 生成は後段で親 Claude が一括実行）

## 最終出力フォーマット

完了後、以下を末尾に必ず出力:

- 既存スキップ時: `⏭️ SKIP: digest/Manufacturing/2026-06-03-Manufacturing.md already exists`
- 新規生成完了時: `✅ commit: <sha>`（commit hash 7 桁）＋ 採用件数と各記事の 3-A.1-M スコア内訳を 1 行ずつ

## 生成後に親 Claude / 次セッションが行うこと（page 反映）

backfill 自体は digest md と articles.jsonl までで止める。生成後、製造記事を**ライブの web に反映**するには次を別途実行する（このプロンプトの範囲外）:

1. `python -m tools.generate_pages`（または runner の通常ビルド）で `docs/manufacturing/2026-06-03/index.html` 等を生成。`docs/index.html` のレンズカード `製造` が「準備中」→実記事に変わり、`docs/sw.js` の SW_VERSION を bump
2. `safe-commit` ゲートを通して commit → push（明示指示時のみ）

## 実行方法（参考）

ローカル runner で、本ファイル内容を prompt として `codex exec` に渡して実行する（既存 backfill 運用と同じ）。試験のため SMTP・page 生成は上記のとおりスキップされる。
