本リポジトリ内の prompts/routine-system.md を Read で読み込み、その指示に厳密に従って **2026-05-24 (土曜)** の News-Grasp 日次 backfill digest を生成してください。これは当日 (今日 = 2026-05-25) ではなく **1 日前の 2026-05-24 分を遡及生成する backfill** です。

## 日付パラメータ (routine-system.md「ステップ 1: 当日情報の準備」を上書き)

- 当日 = **2026-05-24**
- 曜日 = **土曜**
- issue 番号 = **20260524**
- 対象カテゴリ = **FX, AI, IT-Consulting, Game** (4 カテゴリ、土曜マトリクス通り)

## backfill 固有の遵守事項

1. **WebSearch クエリ**: 必ず「2026-05-24」「May 24 2026」「May 23 2026」等の日付トークンを含めること。`24 hours ago` ではなく **2026-05-24 の日付を含むニュース** を能動的に検索する。

2. **過去 90 日 dedup チェック**: `data/articles.jsonl` のうち **`seen_at` が `2026-05-24T00:00:00+09:00` 以前 (= 5/23 以前)** のものに対して url_norm / title 類似度の dedup を行う。
   - 5/24 0時以降に追記された `seen_at` (= 5/25 backfill で入った 2026-05-25 のエントリ) は **dedup 対象から除外**し、純粋に「2026-05-24 時点で未収録だった記事」のみを採用候補とする。

3. **ステップ 3-B サムネ取得**: 必ず `tools/fetch_ogp.py` を Stage 1 で先に実行し、`ng-thumb-common-{cat}.jpg` を digest md に直接書き込まないこと (2026-05-25 強化ルール)。

4. **生成物**:
   - `digest/FX/2026-05-24-FX.md`
   - `digest/AI/2026-05-24-AI.md`
   - `digest/IT-Consulting/2026-05-24-IT-Consulting.md`
   - `digest/Game/2026-05-24-Game.md`
   - `digest/Summary/2026-05-24.md`
   - `data/articles.jsonl` に 2026-05-24 分の記事を追記
   - `data/_status.md` に 2026-05-24 行を追記

5. **commit**: routine-system.md ステップ 6 通り、`git -c user.name="HIDEPON" -c user.email="hideki.kusunoki@gmail.com" commit -m "daily: 2026-05-24 digest (backfill, FX, AI, IT-Consulting, Game)"` まで実行。**git push は絶対に実行しない** (block_remote_git.ps1 hook に deny される + 後段で別プロセスが push する設計)。

> **本ファイルは 2026-05-24 backfill 用の歴史的プロンプト**。2026-06-05 にメール配信機能ごと削除済みのため、旧 step 6 (`tools/send_email.py` Gmail SMTP 直送) と step 7 (GAS Webhook 旧経路) は実行できない。再実行する場合は commit までで停止。

完了後、commit hash を **末尾サマリ** として明示してください (例: "✅ commit: <sha>")。
