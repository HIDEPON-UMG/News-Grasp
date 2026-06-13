# 【Codex 引き継ぎ】News-Grasp 発行ワークフロー Codex 移行 + 収集/役割/ゲート再設計

> **本ドキュメントの位置づけ**: News-Grasp の日次発行ワークフローを **Claude Code (`claude -p`) から Codex (`codex exec`) へ完全移行**し、同時に「収集母数・記者/編集長の役割分担・ゲートのレジリエンス・強調表現の付与責任」を構造的に作り直すための**完全引き継ぎ仕様**。実装は Codex 側セッションが本書だけを読んで着手できる粒度で書く。HTML 仕様書は作らない。
>
> **読者（Codex 実装者）への注意**: 本書の現状コード参照（ファイル名・関数名・行数）は調査時点（2026-06-13）のもの。**着手時に必ず live コードと突き合わせて確認**すること（行数はドリフトしうる）。設計判断（後述の Lv1-4 / publish-always）は確定方針。

---

## 0. 最上位要件（これを破る設計は不可）

**「よっぽどでない限り、記事は必ず発行される」**。これがすべてのゲート設計より優先する。

- 現状は 1 つの gate が FAIL すると**号全体が fallback 通知（「品質確認中」バナー）に落ちる** all-or-nothing 構造で、過去 5 日この fallback が常態化した。これを根絶する。
- 新方針: **ゲートは「号をブロックする関門」ではなく「問題記事だけを取り除く per-article フィルタ」**。問題のある記事は**隔離（drop）して残りで号を組む**。
- **号を発行しない（=fallback 通知）のは、構造的に「発行する記事が物理的に存在しない」場合だけ**に限定する。具体的には:
  - (a) 全カテゴリ合計の生存記事が 0 件、または
  - (b) `generate_pages.py`（HTML 生成）が失敗、または
  - (c) `git commit` / `git push` が失敗。
  - **上記 (a)(b)(c) 以外で号が発行されない経路を作らない**（= 非発行を「表現不能」にする。Lv1）。
- この要件を満たすため、後述の **Stage 5「品質ゲート＝隔離パイプライン」** を本移行の中核に据える。

---

## 1. なぜやるか（背景）

### 1-1. 6/15 Claude 課金分離（移行の動機・確定事実）
2026-06-15 に Anthropic が `claude -p` / Agent SDK / Claude Code GitHub Actions を**サブスク枠から分離**し、専用クレジット（Pro $20 / **Max 5x $100** / Max 20x $200、月次・**API 従量・繰越なし**）へ移す。重いエージェント用途で**実質 12〜175 倍**の値上げ。対話 Claude.ai / 対話 Claude Code は影響なし。
- News-Grasp の daily 構成（編集長 + 記者7 + DeepDive が各々 WebSearch/WebFetch を多数発火）は最重量級で $100 を即枯渇 → 以降 API 課金。
- 出典: [codersera](https://codersera.com/blog/anthropic-june-2026-billing-change-claude-code/) / [the-decoder](https://the-decoder.com/claude-subscriptions-get-separate-budgets-for-programmatic-use-billed-at-full-api-prices/)
- **対策**: Codex（ChatGPT サブスク内・非 API）へ移す。Codex は "included in all ChatGPT plans"、token 従量化は Business/Enterprise のみ（個人サブスクは rate limit 制）。出典: [Codex Changelog](https://developers.openai.com/codex/changelog) / [Non-interactive mode](https://developers.openai.com/codex/noninteractive)

### 1-2. 過去 5 日の発行不安定（改善の動機）
| 日付 | 結末 | 主因 |
|---|---|---|
| 06-09 | 実行不可 | `claude` TIMEOUT 3600s → リトライ後諦め（→ TimeoutSec 4800 に延長で暫定対応） |
| 06-10 / 06-11 | 成功 | 一部 quality_shortfall で本数緩和 |
| 06-12 | fallback ×2 → 復旧 | url-liveness FAIL → fallback、再運転で復旧 |
| 06-13 | fallback（当日） | daily-quality FAIL（FX/Game/IT=**0 件**）→ url-liveness FAIL 連鎖 |

都度の暫定対応（TimeoutSec 延長 / schema gate のマスク解消 / PYTHONIOENCODING / RecoverOnly 復旧）で延命してきたが、**(i) 収集母数不足で 0 件カテゴリが出る、(ii) 1 gate FAIL で号全体が落ちる、(iii) 小修正（強調/英訳漏れ）でも修復されず fallback** という構造問題が残存。

---

## 2. 確認済みの技術前提（推測でなく実機/公式で確認済み）

| 項目 | 確認結果 |
|---|---|
| Codex 非対話実行 | `codex exec <prompt>` で TUI 無し・stdout 出力。wrapper `C:\Users\hidek\bin\codex.ps1` が `~/.vscode/extensions/openai.chatgpt-*` の codex.exe を `--dangerously-bypass-hook-trust` 付きで起動（サブスク認証） |
| Codex フラグ | `-m/--model` `-C/--cd` `--output-schema <json>` `--output-last-message <file>` `-s/--sandbox` `-a/--ask-for-approval` `-p/--profile` を確認 |
| Codex config | `C:\Users\hidek\.codex\config.toml`: `approval_policy="never"` / `sandbox_mode="workspace-write"` / `[sandbox_workspace_write] network_access=true`。codegraph MCP 登録済み |
| Codex web 検索 | web_search あり（既定 cached、`web_search="live"` で live 化可）。非対話でも利用可 |
| **Codex に並列サブエージェント spawn は無い** | 単一エージェントループ。Claude の `Task` 並列 spawn 相当は無い。→ **並列 fan-out は runner が `codex exec` を N 本外部起動する** |
| Python 群はハーネス非依存 | `tools/*.py`（収集・dedup・gate・validator）は Claude/Codex どちらの runner からも呼べる。**作り替えるのは orchestrator(runner.ps1) と prompts/agent 呼び出しだけ** |

---

## 3. 現状マップ（実装者が最初に読むべき現行構造）

### 3-1. オーケストレーション
- **runner**: `C:\Users\hidek\bin\news-grasp-runner.ps1`（git 管理外・約 786 行）。Task Scheduler が毎朝 06:00 JST に起動。
- **timeout wrapper**: `C:\Users\hidek\bin\run_claude_with_timeout.ps1`（rc=124 idle/timeout 判定 + 最大 3 回リトライ）。
- **Claude 起動**: `$ClaudeExe = C:\Users\hidek\.local\bin\claude.exe`、`claude --print`（非対話 JSON streaming）。digest=Sonnet / DeepDive=Opus。TimeoutSec 4800 / IdleTimeoutSec 900。
- **runner の責務**: gate 群 → repair → fallback → `generate_pages.py` → git commit/push → `send_push`。**Claude は生成まで、commit/push は禁止**という境界。

### 3-2. プロンプト / サブエージェント
- `prompts/runner-prompt.md`（入口・編集長ローダ）→ `prompts/newsroom-editor-system.md`（編集長 Sonnet）→ 記者を `Task` で並列 spawn。
- `prompts/newsroom-reporter-system.md`（記者 Sonnet・各カテゴリ専属）。
- `prompts/deepdive-runner-prompt.md` + `deepdive-research-system.md`（エース記者 Opus・非致命）。
- `prompts/routine-system.md`（旧単一セッション体制・退避保管）/ `runner-prompt-legacy.md`。
- `.claude/agents/ng-reporter.md`（model: sonnet / tools: WebSearch,WebFetch,Read,Write,Bash,Grep,Glob）/ `.claude/agents/ng-deepdive.md`（model: opus）。

### 3-3. 現行の役割分担（移行で変える点）
- **編集長**: 当日準備 → 記者 N 体 spawn → 各記者出力を `verify_reporter_output.py` で検証・差し戻し（最大1回）→ カテゴリ間 dedup 第2パス → Summary 考察執筆 → `articles.jsonl` 単一 append → DeepDive spawn。
- **記者**: `harvest_candidates --category` 実行 → WebSearch 補完 → dedup → 記事カード生成（**強調 `[[ ]]`/`**`/`__` 付与**・title_ja 付与）→ `digest/{Genre}/{号日}-{Genre}.md` + `tmp/newsroom/{号日}/{cat}.records.jsonl` + `data/search_audit/{号日}/{cat}.json` を出力。
- **問題点（移行で解消）**: 記者が各自 WebSearch 収集 → カテゴリ間重複・収集やり直し。記者 bullets の強調には **検証ゲートが無い**（サイレント劣化）。

### 3-4. 収集の現状
- `tools/harvest_candidates.py`: Google News RSS search feed（日本版）。`CATEGORY_QUERIES` に 7 カテゴリ × **1 本の OR クエリ**。`when:1d` で直近 24h 強制。上限 `--max-per-category` 既定 **50**。`<link>` は JS エンコード URL のため canonical は後段 LLM が `site:<媒体> <タイトル断片>` の限定 WebSearch で解決。
- `tools/_fetch.py`: **昇格ラダー**（urllib → Scrapling `Fetcher`(curl_cffi 偽装) → `StealthyFetcher`(headless・上限 10/プロセス)）。`_looks_blocked` で 403/429/503 や Cloudflare チャレンジを検知して昇格。
- **Scrapling の現在の使われ方（ユーザー懸念の核心）**: `fetch_ogp.py`（OGP 画像）/ `date_evidence.py`（公開日 htmldate 補完）/ `audit_all_article_urls.py`（URL 生存）/ `harvest_candidates.fetch_feed`（RSS 取得の 403 fallback）。**= 「記事を発見する段」には使われていない。OGP/日付/生存確認の補完のみ**。→ 収集可能サイトは増えていない。
- 棄却の主因: `dedup.py` の鮮度ゲート。06-11 実データで **drop 42 件中 36 件（86%）が freshness gate 起因**。真因は上流＝WebSearch が鮮度フィルタを持たず古記事を上位返しすること。
- `data/search_audit/{date}/{cat}.json` の実測（06-12 ai）: raw 52 → 候補 12 → 採用 5。他カテゴリは 06-13 で 0 件発生。

### 3-5. dedup の現状（流用する資産）
`tools/dedup.py`:
- **URL 正規化一致**（scheme/host 小文字・fragment 除去・utm 等除去・AMP→canonical・末尾スラッシュ・`m.` 除去）→ 完全一致は 24h 窓無視で常に除外。
- **タイトル類似**（記号除去・空白正規化後の完全一致 or 2-gram Jaccard ≥ **0.42**）。
- **言語非依存トークン照合**（英字 3 字以上・2 桁以上数値・カタカナ固有名詞・英日エイリアス）→ 同一イベント判定。
- **鮮度 3 段**（URL 日単位 → URL 月単位 → htmldate 補完 warn-pass・`--date-fetch-cap` 既定 20）。`--max-source-age-days` 既定 7（運用は 1）。
- **続報ゲート**（`--followup-gate`）: 前回掲載から新材料トークン 0 → drop。

### 3-6. ゲート / 修復 / fallback の現状
runner が呼ぶ gate（順）と現挙動:
1. summary-reflection（編集長 Summary の `## § 本日のテーマ考察` 構造）
2. daily-quality（hero 画像 / 記事 date が号日より 46+ 日前 / カテゴリ件数）
3. （DeepDive・非致命）
4. url-liveness（`audit_all_article_urls.py --gate`・URL 生存 + session 照合）
5. record-schema（`validate_record.py`・`thumb`/`date`/`url`/`genre` 検査。`_REQUIRED_KEYS=("date","title","url","thumb")`・**title_ja は必須でない**）
6. digest-articles-reconcile（`validate_digest_articles_reconcile.py`・digest md ↔ articles.jsonl URL 集合一致）
7. ja-callout（`test_title_ja_coverage.py`・英文記事に `> [!ja]` 必須）
8. pytest-static（`-m "not network"` 全件 PASS 要求）
9. public-html（docs/index.html の TOP STORY/hero 検証）
- **どの gate も FAIL → bounded repair（`gate_attempts.py`・`max_same_signature_retries=1` / `max_category_failures=2`）→ 修復失敗 or 予算切れで `Invoke-FallbackPublish` → 号全体が「品質確認中」notice 公開**。
- **強調表現は記者 bullets には validator 無し**。編集長 Summary の lead/section だけ `validate_summary_emphasis()`（`validate_daily_quality.py`）で 3 階層検証。
- **title_ja は pytest test（gate/repair 対象外）**。1 件欠落で全体 FAIL するのに修復ループに乗らない。
- 既知の repair 構造欠陥（移行で是正）: 署名が粗く「別記事の同種エラー」を「同一署名」と誤判定し 2 件目で修復拒否 → fallback 直行。

---

## 4. 目標アーキテクチャ（Codex ネイティブ 日次パイプライン）

オーケストレーションを「LLM 内 fan-out」から「**runner による外部 fan-out + 決定論前処理 + per-article 隔離**」へ反転する。**収集・dedup・鮮度・URL 生存・schema は全部コード（LLM 前/後の決定論）。LLM は要約・判断・強調・考察だけ**。

```
news-grasp-runner.ps1（Codex 版・全面改修）
 │
 ├ Stage0  収集（コード・LLM無）
 │    tools/harvest_candidates.py 拡張：媒体別RSS登録簿 + カテゴリ別複数クエリ + 上限↑
 │    出力: tmp/newsroom/{号日}/raw/{cat}.jsonl（候補プール・pubDate付）
 │
 ├ Stage1  横断 dedup + 鮮度（コード・LLM無）
 │    tools/dedup.py を全カテゴリ一括 1 回（--freshness-gate --followup-gate）
 │    出力: tmp/newsroom/{号日}/deduped/{cat}.jsonl（重複除去・鮮度確定・published_date 注釈付）
 │
 ├ Stage2  記者 ×N（codex exec・並列度制限）   ※ N=カテゴリ数
 │    入力: deduped/{cat}.jsonl（事前収集・dedup・鮮度済）
 │    仕事: 採用選定(件数可変) → 要約 bullets(強調なし) → title_ja → canonical URL 解決(web_search site:限定のみ)
 │    出力: tmp/newsroom/{号日}/{cat}.records.jsonl（--output-schema で JSON 強制）+ digest md カード素材
 │
 ├ Stage3  編集長 ×1（codex exec）
 │    横断重複の最終照合 → 全カードに 3 階層強調を一括付与 → Summary 考察執筆 → articles.jsonl 単一 append
 │
 ├ Stage4  DeepDive ×1（codex exec・上位モデル・非致命）
 │
 ├ Stage5  品質ゲート＝隔離パイプライン（コード + 限定 codex exec repair）★中核
 │    全 per-article gate を「FAIL 記事を drop して残りで続行」に変換。
 │    機械修復可能なものは先に repair。非発行は総生存0/build/push 失敗のみ。
 │
 └ Stage6  generate_pages → publish gate → git commit → git push → send_push（コード）
```

### Stage0：収集の決定論前処理化（懸念1の主対策・Lv2 境界集約）
- `tools/harvest_candidates.py` を拡張する。
  - **媒体別ネイティブ RSS 登録簿**を追加（カテゴリ → RSS URL リスト）。RSS を持つ媒体を直接購読して母数を底上げ。例（**実 URL は着手時に WebFetch で 200 を確認してから登録。記憶で URL を書かない＝`feedback_llm_url_fabrication_ban`**）: TechCrunch / The Verge / Ars Technica / Reuters / 日経 / Bloomberg / 各カテゴリ専門媒体の `/feed`・`/rss`。
  - **カテゴリ別の複数フォーカスクエリ**: 現状 1 本の OR クエリを、エンティティ/イベント別の 3〜5 本に分割し Google News RSS を複数回引く（`when:1d` は維持）。
  - 上限引き上げ（`--max-per-category` 50 → 多め。playground パラメータ）。
  - **全 fetch は `tools/_fetch.py` ラダー経由**（anti-bot RSS も剥がす。個別ツールに Scrapling を直 import しない）。
- 効果: 一次鮮度を `when:1d`/RSS pubDate で担保したまま母数を底上げ。drop 86%=鮮度起因の真因（WebSearch 依存収集）を決定論 RSS 一次ソースへ置換して根治。**鮮度ゲートは厳格のまま据え置き（Q3）。母数で殴る**。

### Stage1：横断 dedup + 鮮度を一括 1 回（懸念2・Lv2 境界集約）
- `tools/dedup.py` を**全カテゴリの候補をまとめて 1 回**実行（既存ロジックを流用、runner 配線変更が主）。
- これで「記者が各自 dedup → 編集長が再 dedup → カテゴリ間重複が漏れる」を**記者が候補を見る前に**横断照合して解消。memory 既知の「カテゴリ別分割 dedup はカテゴリ間重複を通す」を構造的に封じる。
- 出力をカテゴリ別に分割し、`published_date` + `date_evidence_source` 注釈を carry-over して Stage2 へ。

### Stage2：記者（codex exec・並列度制限）（懸念2）
- runner が `codex exec` を**カテゴリ数ぶん外部起動**。**並列度を制限**（既定 3 同時など。CPU/Codex rate limit 保護。playground 化）。1 記者 = 1 `codex exec` プロセス。
- 記者は **事前収集・dedup・鮮度確定済みの候補リスト**を受領。仕事は限定:
  1. 採用選定（**件数は可変＝Q2**。「最低 N 件」固定をやめ、品質を満たす分だけ採用）。
  2. 要約 bullets 執筆（**強調 `[[ ]]`/`**`/`__` は付けない＝Q4。編集長が一括付与**）。
  3. title_ja（英文タイトルの和訳）。
  4. canonical URL 解決（harvest URL が Google News エンコードのときだけ `web_search` の site: 限定）。
  5. `codex exec --output-schema <records.schema.json>` で records.jsonl + digest md カード素材を出力。
- 記者は**広域収集も dedup もしない**＝軽量・高速・低コスト・横断やり直しゼロ。

### Stage3：編集長（codex exec・単一）＋強調集約（懸念4・Lv1+Lv2）
- runner が `codex exec` を 1 本起動:
  - 横断重複の最終照合（**同一 URL の複数カテゴリ掲載**＝既知ギャップを潰す。06-12 の CPI/ECB 重複掲載クラス）。
  - **全カードに 3 階層強調を一括付与**（単一境界・Q4）。記者が出した素の bullets に編集長が `[[ ]]`/`**`/`__` を付ける。
  - Summary（テーマ考察）執筆（強調込み）。
  - `articles.jsonl` への**単一 append**（編集長＝単一ライターを維持）。
- **Lv1: 強調 omission を表現不能化**: 新 validator `validate_emphasis_coverage`（仮）が**全カードに 3 階層強調があること**を検査。付与責務が編集長 1 箇所に集約されたので検証も 1 validator で閉じる。Stage5 で per-article 隔離対象（強調欠落カードは編集長 repair で補完、補完不能なら drop）。

### Stage4：DeepDive（codex exec・上位モデル・非致命）
- 役割（テーマゲート式休載・commit 禁止）を維持し codex exec へ移植。失敗/休載は号を止めない。

### Stage5：品質ゲート＝隔離パイプライン（★publish-always の中核・懸念3・Lv1→Lv4）
**設計の肝**: 全ての per-article gate を「号をブロックする関門」から「**FAIL した記事だけを drop して残りで続行するフィルタ**」に変換する。これにより「発行する記事が 1 件でも残れば必ず発行」になり、非発行を構造的に排除する（Lv1）。

処理順（各記事ごとに評価し、quarantine ログに drop 理由を残す）:
1. **機械修復フェーズ（先に試す）**: 機械的・回復可能な違反（強調漏れ・title_ja 漏れ・thumb キー漏れ・schema フィールド漏れ）は、まず軽量修復で直す。可能なものは Lv1 で**発生不能化**:
   - `title_ja` を `validate_record._REQUIRED_KEYS` に追加（欠落 record を**書けない**構造に。現在 pytest 任せ→schema へ格上げ）。
   - 強調は Stage3 単一付与 + coverage validator。
   - 編集長 repair（`codex exec` 1 本・限定）で補完。
2. **per-article 隔離フェーズ（修復不能/構造的なものは drop）**:
   - url-liveness: **死リンク/捏造 URL の記事だけ drop**（号は落とさない）。`audit_all_article_urls.py` を「FAIL URL の記事を articles.jsonl/digest から除去して PASS させる」モードに拡張。
   - freshness 逸脱（古記事）: その記事だけ drop。
   - 横断重複: 重複側を 1 本だけ drop。
   - schema 不能修復: その record を drop。
3. **整合再構成**: drop 後に digest md ↔ articles.jsonl ↔ search_audit を再突合（`validate_digest_articles_reconcile.py` を「drop を反映して再整合」する向きに）。
4. **静的ゲート（コード健全性のみ）**: `pytest -m "not network"` は **content 依存テスト（title_ja coverage / emphasis coverage）を上記 per-article 隔離の後段に置く**ことで、生存集合が常に PASS する構造にする。静的テストの FAIL は「**コード/ビルドのリグレッション = 真の破滅事態**」だけになり、その時のみ fallback。
- **品質フロア（Q2）**: `validate_daily_quality` を「カテゴリ 5 件固定」から「**可変最低本数**」へ。薄いカテゴリは統合 or 当日非掲載で**集まった分を発行**。
- **repair 予算の粒度是正（Lv4）**: `gate_attempts` の署名に **artifact 単位（どの記事 URL）**を含め、「同一記事の同一エラーを 2 回」だけ stuck 判定。別記事の同種エラーを誤って拒否しない。
- **fallback 通知は最終手段に格下げ**: §0 の (a) 総生存 0 件 / (b) generate_pages 失敗 / (c) git 失敗 のみ。

### Stage6：generate_pages → publish → commit → push（コード）
- 既存 `generate_pages.py` / `publish_fallback.py`(mark-ok) / `send_push.py`（fallback 中は自己抑止）/ git 配線を維持。LLM でなく runner が担う境界は不変。

---

## 5. Claude → Codex 移行マッピング（コマンドテンプレ付き）

| 観点 | 現状(Claude) | 移行後(Codex) |
|---|---|---|
| 非対話起動 | `claude --print` via `run_claude_with_timeout.ps1` | `codex exec` via 新 `run_codex_with_timeout.ps1`（idle/timeout/retry を流用） |
| 並列記者 | 編集長が `Task` で N 体 spawn（LLM 内） | runner が `codex exec` を N 本外部起動（並列度制限） |
| モデル | Sonnet(digest)/Opus(DeepDive) | Codex 中位(記者/編集長)/上位(DeepDive)。**実 model id は着手時に `codex --help` / config で確認（推測で書かない）** |
| 構造化出力 | md/JSONL を free text 生成→parse | `codex exec --output-schema <schema.json>` で JSON 強制（パース不要・retry 内蔵） |
| サブエージェント定義 | `.claude/agents/*.md`（Task 前提） | prompt ファイル化して codex exec に渡す（Task 前提記述を除去） |
| URL 捏造防止の session 白リスト | PostToolUse hook が WebSearch/WebFetch URL 捕捉 → `audit --match-session` | Codex `web_search` 結果 URL を `.codex/hooks/append_session_urls.py` が捕捉する形へ改修（現状 legacy・race-free フラグメント方式へ）。**一次 URL は RSS harvest 由来＝実 URL なので捏造面は縮小。audit gate は最終防衛線として維持** |

**記者起動コマンドテンプレ（実装の出発点・要 live 確認）**:
```
codex.ps1 exec --model <mid> -C <RepoDir> \
  --output-schema <RepoDir>\schemas\reporter_records.schema.json \
  --output-last-message <tmp>\{cat}.last.txt \
  "<reporter system prompt> + カテゴリ={cat} + 号日={date} + 候補ファイル=deduped/{cat}.jsonl"
```
- `codex.ps1` が自動で `--dangerously-bypass-hook-trust` と harness preamble を注入する点に注意（既存仕様）。
- `run_codex_with_timeout.ps1` でこれをラップし rc=124 idle/timeout 判定 + リトライを付ける。

---

## 6. 懸念 → 設計レベル対応（チェックを増やさず構造で封じる）

| ユーザー懸念 | 主対策（Lv） |
|---|---|
| 1. 棄却後 1-3 件 / Scrapling が発見段未使用 | Stage0 決定論 RSS 拡張（Lv2 境界集約・一次鮮度は維持）。Scrapling を `_fetch` ラダーで anti-bot RSS にも適用＝発見段に効かせる |
| 2. 記者バラバラ収集 → 重複/やり直し | Stage0-1 で収集+横断 dedup をコード前処理化（Lv2）。記者は要約専任に軽量化。**統一リサーチ＝決定論前処理（LLM サブエージェントでなくコード）が答え** |
| 3. 小修正で error-drop / fallback 多発 | Stage5 隔離パイプライン（Lv1: 非発行を表現不能化 / title_ja・強調を発生不能化 / Lv4: repair 署名粒度是正）。品質フロア可変化 |
| 4. 強調の付与責任 | **編集長単一付与が適切**（Lv2 境界集約）＋coverage gate（Lv1 表現不能化）。記者付与は validator 不在でサイレント劣化していたため移管 |

> **懸念2「統一 vs 分野別リサーチ」への結論**: 収集（RSS harvest + 横断 dedup + 鮮度）は **1 つの決定論前処理（コード）に統一**するのが最大効率・最短・無駄ゼロ。LLM のリサーチサブエージェントを足すと収集の鮮度問題（WebSearch 依存）が再発するので足さない。要約・判断だけ分野別記者（LLM）に残す＝**統一収集 × 分野別執筆のハイブリッド**。

---

## 7. 変更ファイル一覧

| ファイル | 変更内容 | 種別 |
|---|---|---|
| `C:\Users\hidek\bin\news-grasp-runner.ps1` | Codex orchestration へ全面改修。Stage0-1 決定論前処理を先頭に、記者の外部並列 fan-out、Stage5 隔離パイプライン、品質フロア可変化、fallback 条件の §0 限定化を配線 | orchestrator |
| `C:\Users\hidek\bin\run_codex_with_timeout.ps1`（新規） | `run_claude_with_timeout.ps1` を派生（idle/timeout/retry 流用） | wrapper |
| `tools/harvest_candidates.py` | 媒体別 RSS 登録簿 + カテゴリ別複数クエリ + 上限↑（既存 `_fetch` ラダー流用） | 収集 |
| `tools/dedup.py` | 全カテゴリ一括 1 回呼び出しに対応（横断照合。ロジック流用、配線変更が主） | dedup |
| `tools/validate_record.py` | `_REQUIRED_KEYS` に `title_ja` 追加（Lv1） | schema |
| `tools/validate_daily_quality.py` | 強調 coverage を全カードへ拡張 + 品質フロア可変化 | 品質 |
| `tools/audit_all_article_urls.py` | per-article drop モード追加（FAIL URL 記事を除去して PASS）+ Codex session 照合 | URL |
| `tools/validate_digest_articles_reconcile.py` | drop 反映の再整合方向に対応 | 整合 |
| 新規 `tools/gate_policy.py`（仮） | gate 失敗の「機械修復 / per-article 隔離 / 破滅 fallback」3 分類（境界 1 箇所） | ゲート方針 |
| `tools/gate_attempts.py` | repair 署名に artifact 粒度追加 | repair |
| 新規 `schemas/reporter_records.schema.json` / `editor_*.schema.json` | `codex exec --output-schema` 用 | 構造化出力 |
| `prompts/newsroom-reporter-system.md` | 記者を「事前収集済み候補の要約専任・強調なし・収集/dedup しない」へ書換（Task 前提除去） | prompt |
| `prompts/newsroom-editor-system.md` | 編集長に「全カード強調一括付与」追加、記者 spawn を runner 外部起動前提へ | prompt |
| `.claude/agents/ng-reporter.md` / `ng-deepdive.md` | codex exec 用 prompt として再構成（または `prompts/` へ移設） | agent |
| `News-Grasp/.codex/hooks/append_session_urls.py` | Codex web_search URL 捕捉 + race-free フラグメント方式 | hook（**承認ゲート対象**） |

**流用する既存資産（新規実装しない）**: `tools/_fetch.py` / `tools/dedup.py` / `tools/audit_all_article_urls.py --gate` / `tools/publish_fallback.py` / `tools/send_push.py` / `tools/generate_pages.py` / 既存契約テスト群（427 passed を回帰基準に）。

---

## 8. 実装手順（Codex 向け・TDD Red→Green・順序付き）

> **HTML 仕様書は作らない**（本書が引き継ぎ仕様）。各ステップは**先に失敗テスト（Red）を書いてから実装（Green）**。`feedback_impact_analysis_before_modification`: 1 行も書く前に対象の呼出元/先を Grep で全列挙。

**Step A（harness 非依存・現 Claude runner でも動く Python 改善）**
1. `validate_record` の `title_ja` 必須化 ＋ Red テスト（欠落 reject）。
2. `validate_emphasis_coverage` 新設（全カード 3 階層）＋ Red テスト。
3. `gate_policy.py`（3 分類）＋ Red テスト（機械修復/隔離/破滅のルーティング）。
4. `gate_attempts` 署名 artifact 粒度化 ＋ Red テスト（別記事同種エラーを誤拒否しない）。
5. `validate_daily_quality` 品質フロア可変化 ＋ Red テスト（<5 件/カテゴリでも発行可）。
6. `audit_all_article_urls` per-article drop モード ＋ Red テスト（死 URL 記事 drop で残り PASS）。
7. `harvest_candidates` RSS 登録簿 + 複数クエリ + 上限 ＋ Red テスト（カテゴリあたり ≥N ソース）。
- ここまでで**現 Claude runner に Stage5 隔離・品質フロアを先行配線**し、移行前に fallback 減を実測できる。

**Step B（Codex orchestration）**
8. `run_codex_with_timeout.ps1` 派生 ＋ Red（rc=124 idle 判定の系）。
9. runner を codex exec 化（claude → codex exec）。Stage0-1 前処理を先頭配線。記者外部並列（並列度制限）。`--output-schema` 配線。**model id を `codex --help`/config で確認**。
10. prompts/agents を Codex 用に書換（記者=要約専任・強調なし / 編集長=強調集約。Task 前提除去）。
11. `.codex/hooks/append_session_urls.py` を Codex web_search 対応 + race-free 化（**ユーザー承認後**）。
12. runner 契約テスト: 新 runner が `codex exec`（not `claude --print`）を正しい flag で呼ぶ（既存 `test_runner_convergence_contract.py` 方式踏襲）。

**Step C（統合検証・切替）**
13. SmokeTest → 1 カテゴリ dry-run → フル E2E（テスト日付）。
14. 公開サイト実機確認（fallback バナー 0 / 7 lens / STORIES 件数）。
15. Task Scheduler の起動先を Codex runner へ切替。現 Claude runner は退避（ロールバック用）。

---

## 9. 検証（TDD・Lv4 契約テスト中心・個別 smoke 単独完了は禁止）

各不変条件を契約テスト 1 件で locked-in:
- `validate_record` が `title_ja` 欠落 record を reject（Lv1）。
- 全公開カードに 3 階層強調（emphasis coverage gate）。欠落で FAIL。
- dedup が全カテゴリ一括 1 回で実行され、同一 URL の複数カテゴリ掲載を検出。
- harvest 登録簿がカテゴリあたり ≥N ソースから候補を生成（母数増の系）。
- gate_policy が 機械修復/隔離/破滅 に正しくルーティング。
- gate_attempts が「別記事の同種エラー」を stuck 誤判定しない。
- **publish-always の系（最重要）**: per-article gate が「FAIL 記事を drop して残りで PASS」になり、**総生存 0/build/push 失敗以外で fallback に落ちない**。死 URL を 1 件混ぜても号は発行され、その 1 件だけ消える。
- 品質フロア: <5 件/カテゴリでも構造 gate 通過で発行。
- runner 契約テスト: codex exec を正しい flag で呼ぶ。
- 既存 `pytest -m "not network"` 全件 PASS（現状 427）を回帰維持。

実機: SmokeTest → 1 カテゴリ dry-run → フル E2E → 公開サイト目視。

---

## 10. Cutover / Rollback runbook
- **切替**: Task Scheduler の Action を `news-grasp-runner.ps1`（Codex 版）へ変更。現 Claude 版は `*.claude.bak` 等に退避。
- **ロールバック**: Codex 版が連続失敗したら Task Scheduler を Claude 版 bak に戻す（6/15 以降は API 課金が出るが発行は維持される）。`-RecoverOnly` で生成済み digest から復旧する既存経路も温存。
- **移行期間の API 課金最小化**: Step B-C を最短で回す。それまでは現 Claude runner 継続（6/15 を跨ぐ分は一部 API 課金）。

---

## 11. Codex 実装者が live コードで確認すべき未決事項（推測で埋めない）
1. **Codex 実 model id**: 中位/上位の正確な名前を `codex --help` / `config.toml` / `codex exec -m` で確認。
2. **Codex web_search の出力形式と session URL 捕捉**: `.codex/hooks/append_session_urls.py` が Codex の web_search 結果から URL をどう拾うか実 payload で確認（Claude の PostToolUse hook とは payload 形状が違う）。
3. **Codex サブスク rate limit**: 記者 N + 編集長 + DeepDive を daily で回して 5h/週次上限に当たらないか実測。並列度・モデル tier・DeepDive 隔日化で調整。
4. **runner の git 管理**: runner は `~/bin` 配下で repo 外。契約テスト・CodexHarnessState snapshot 同期方針を確定（`reference_codexharnessstate_commit_target` 準拠）。
5. **`.codex/hooks` 改修はハーネス変更承認ゲート対象**: 実装前にユーザー承認。
6. **RSS 登録簿の実 URL**: 各媒体の feed URL は WebFetch で 200 を確認してから登録（記憶で書かない）。
7. **Scrapling 発見段適用の負荷**: 一覧ページ scraping は StealthyFetcher 上限(10/プロセス)内。RSS で足りるカテゴリには使わない。

---

## 12. リスク
- Codex は単一エージェントのため記者並列は runner 外部起動依存 → プロセス管理・並列度・ゾンビ掃除（`feedback_codex_mcp_process_cleanup`）に注意。
- publish-always でも**収集品質が低いと薄い号が出続ける**ので、Stage0 の母数底上げ（RSS 拡張）と quarantine ログの監視はセットで運用。古記事混入は鮮度ゲート厳格維持（Q3）で防ぐ。
- `--output-schema` の JSON 強制で記者の自由記述（要約のニュアンス）が制約されすぎないようスキーマ設計に注意。
