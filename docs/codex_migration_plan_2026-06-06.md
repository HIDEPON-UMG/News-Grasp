# Codex 移行実行プラン (News-Grasp / 2026-06-06)

- **状態**: 移行決定済・PoC 未着手・実装未着手
- **対象**: News-Grasp の日次自動化 (`claude --print` → `codex exec`) への移行
- **由来**: `ProjectFolders/research/codex-migration-2026-06-04/` の go/no-go 調査 7 ファイルから、**移行実行に必要な記載だけ**を統合したもの。意思決定材料 (選択肢比較 A-D / go-no-go 判定表 / ロックイン P1-P4 / 調査反省ログ) は research 側にスナップショットとして残置し、本書は「どう移行するか」だけを扱う

## 0. 前提 (なぜ移行するか・要約のみ)

2026-06-15 の Anthropic 課金改定で `claude --print` / `claude -p` / Agent SDK / GitHub Actions が subscription 枠から分離され、Max 5x には専用 credit pool $100/月が付与される。News-Grasp は早朝 Task Scheduler で `claude --print` を日次実行しており、実消費が月 $360-500 (API rate 換算) で $100 credit を 3.6-5 倍超過する。同価格帯の Codex Pro 5x ($100) へ News-Grasp だけ分離移行する。

> 他システム (AI-Pulse / CCA-StudyApp / ITStr-StudyApp 等) は Anthropic 非依存または MCP 経由 (subscription 内) で改定影響ゼロ。詳細は research 側 `05_ecosystem-lockin.md`。

## 1. 移行前に潰す不確定要素 (PoC 必須・4 件)

実装前に PoC で確定する。公式 doc が沈黙している項目なので推測で進めない ([[feedback_no_speculation]])。

| ID | 不確定要素 | 確定方法 | 実装への影響 |
| --- | --- | --- | --- |
| **I1** | Codex `web_search` (live) の content depth。`context_size="high"` で snippet か full content かは公式沈黙 | `codex exec --json --search` を 1 回走らせ `item.web_search.result` の payload サイズを実測 | snippet のみ → `tools/fetch_article_body.py` 新規必須 / full → 新規不要 (工数 -2.5h) |
| **I2** | Codex Pro 5x の weekly cap (公式非公開)。Issue #19585 で Pro 20x ユーザーが「2 日で枯渇」報告 | 1 ヶ月 trial 課金後、毎日 News-Grasp 規模を回して `codex status` で残量推移を観察 | 週末枯渇 → Pro 5x 単独不可 / 月末まで余裕 → go / 中間 → weekly cap 監視を runner に追加 |
| **I3** | GPT-5.5 / GPT-5.5-Codex の日本語品質 (Editorial Summary・3 階層強調・関係図構図・R1-R5 採点) | 同日テーマで Claude と並行生成 → 価値ルーブリック R1-R5 で採点比較。最低 3 日分 | R1-R5 合計 8+ かつ R1・R2 が両方 2 点で品質 PASS |
| **I4** | Codex の `--sandbox workspace-write` で shell exec の外部通信 (urllib) が通るか (公式沈黙) | `tools/fetch_ogp.py` を `codex exec --sandbox workspace-write` 経由で走らせ HTTP 通信可否を確認 | 通る → workspace-write で全工程可 / 通らない → `--yolo` 採用 |

### PoC 計画 (Phase 0-5)

- **Phase 0** セットアップ (0.5 日): `npm install -g @openai/codex` / Pro 5x 購読 / `codex login --device-auth` → `~/.codex/auth.json` / `~/.codex/config.toml` 作成 (`web_search="live"` / `[tools.web_search] context_size="high"` / model) / `codex --version` `codex status` 確認
- **Phase 1** I1+I4 確定 (0.5 日): 最小プロンプトで `codex exec --json --search` 1 回 → web_search payload 確認 + fetch_ogp の sandbox 通過確認 → wrapper の sandbox flag 確定
- **Phase 2** digest 品質 PoC (1 日): `prompts/runner-prompt.md` ベースで Codex 流に書換 + (必要なら) fetch_article_body 新規 → Claude と並行生成 → R1-R5 採点 (理想 3 日分)
- **Phase 3** DeepDive 品質 PoC (1 日): `prompts/deepdive-runner-prompt.md` で同様 → 関係図/チャート/表/decision を比較 → R1-R5 採点
- **Phase 4** I2 計測 (1 ヶ月 trial): Phase 2+3 PASS なら毎朝本番走行 + `codex status` 残量を log 追記 → 週次枯渇有無を実測
- **Phase 5** 本実装 (Phase 4 が go なら §3 を実装。実装 8-14.5h)

PoC コストは Pro 5x 1 ヶ月 $100 で Phase 0-4 完走。

## 2. 機能対応表 (claude --print → codex exec)

| 機能 | claude --print | codex exec | 互換 |
| --- | --- | --- | --- |
| WebSearch (5 軸・24h) | `WebSearch` default tool | `web_search` first-party (`--search` で live・config で常時 live 化) | ◎ |
| URL→本文取得 | `WebFetch` default tool | **`web_fetch`/`url_fetch` built-in 無し** → `tools/fetch_article_body.py` 新規 (urllib+html.parser・`fetch_ogp.py` パターン) を Bash で呼ぶ | ○ (実装で代替) |
| Read / Write / Edit | `Read`/`Write`/`Edit` | `read_file` / `write_file` / `apply_patch` | ◎ |
| シェル実行 | `Bash` + `--dangerously-skip-permissions` | `--sandbox workspace-write` または `--dangerously-bypass-approvals-and-sandbox` (`--yolo`) | ◎ |
| git push | hook deny → ps1 代行 | Codex に hook 無し → 同じく ps1 代行で温存 | ◎ |
| 実行体 | `C:\Users\hidek\.local\bin\claude.exe` | `codex.exe` (npm `@openai/codex` global install) | - |
| stdin prompt | RedirectStandardInput + UTF-8 StreamWriter | `cat prompt \| codex exec -` で同パターン流用 | ◎ |
| 出力 / token 計上 | text + `~/.claude/projects/*/jsonl` (5/27 以降欠落) | `--json` NDJSON の `turn.completed` で `input_tokens/cached_input_tokens/output_tokens` を確定取得 (改善方向) | ◎ |
| モデル | `--model sonnet` (digest) / `--model opus` (DeepDive) | `--model gpt-5.5` または `gpt-5.5-codex`。Opus 相当なしで DeepDive も gpt-5.5 に統合 | ○ |
| 認証 | Max 5x 自動継承 | `codex login --device-auth` → `~/.codex/auth.json` | - |

## 3. 改修チェックリスト (実装 8-14.5h + 品質 PoC 4-6h)

| カテゴリ | 改修内容 | 影響ファイル | 工数 |
| --- | --- | --- | --- |
| A. Prompt ツール記述 | `WebSearch`→`web_search`/「Web で検索」、「WebFetch で本文取得」→`tools/fetch_article_body.py` を Bash 呼出、headless 制約文言を Codex 流に | `prompts/routine-system.md` / `prompts/deepdive-research-system.md` | 1-3h |
| B. wrapper.ps1 + 認証 | 実行体パスを codex.exe に / 起動 flag を `exec --search --sandbox ... \|--yolo --model gpt-5.5\|gpt-5.5-codex` に / stdin・timeout・異常終了コード規約は流用 / `codex login` 初回 | `C:\Users\hidek\bin\run_claude_with_timeout.ps1` | 2-3h |
| C. sandbox 経路 | `workspace-write` 採用時 `--add-dir C:\Users\hidek\bin\news-grasp-logs` (log append) + `--add-dir C:\Users\hidek\.secrets` (SMTP/VAPID 読込)。`--yolo` 採用なら 0 | wrapper.ps1 | 0.5-1h (yolo 時 0) |
| D. Hook 対応 | Codex に hook 概念なし → push は ps1 代行で温存。**URL fabrication ban hook の Codex adapter は別途 §4** | - | 0 (本体) |
| E. cost ログ整形 (任意) | `--json` parse して `news-grasp-logs/YYYY-MM-DD.log` に cost 行追記 | wrapper.ps1 | 1-2h |
| F. モデル切替 | `-Model` parameter 互換 (実装 0・品質は PoC) | wrapper.ps1 | 0 |
| G. test 書換 | `test_runner_wrapper_smoke.py` のみ wrapper 引数仕様変更で書換必須。他 23 件は出力物構造検証でそのまま動く | `tests/test_runner_wrapper_smoke.py` | 0.5h |
| H. runner.ps1 本体 | wrapper invoke (digest / DeepDive) の引数仕様調整反映のみ。URL gate / push / generate_pages / send_push は無改修 | `C:\Users\hidek\bin\news-grasp-runner.ps1` | 0.5-1h |
| I. 新規スクリプト | `tools/fetch_article_body.py` (urllib+html.parser・Mozilla UA・10s timeout・1 retry・paywall stub) + 契約テスト 1 件 | News-Grasp/tools/ | 2.5-3.5h |
| J. 環境変数 / 設定 | `npm install -g @openai/codex` / `~/.codex/config.toml` 作成 | - | 0.5h |

## 4. Hook 移行: URL fabrication ban adapter

URL fabrication ban の Lv2 境界 hook (`.claude/hooks/append_session_urls.py`) は §3 の改修チェックリスト (2026-06-04 作成) には含まれない**調査後の追加差分**。Codex CLI の tool 名・payload・env 展開が Claude Code と異なるため、専用 adapter が必要。実装手順は別 handoff に分離済み:

- [handoff_2026-06-06_codex-url-ban-adapter.md](handoff_2026-06-06_codex-url-ban-adapter.md)

要点: Codex の web 検索 tool 名は `web_search` (Claude は `WebSearch`)、`${CLAUDE_PROJECT_DIR}` は Codex で未展開 (絶対パス or `commandWindows` 使用)、payload parser を Codex 用に分岐。本 §3 の移行と同時に着手する。

## 5. 改修しない (= LLM 非依存で温存する) 要素

Codex 移行を理由に巻き込まない ([[feedback_delete_scope_stay_within_feature]] の精神):

- `tools/dedup.py` (URL 正規化 / タイトル類似 / cross-language トークン一致)
- `tools/fetch_ogp.py` / `tools/send_email.py` / `tools/send_push.py` / `tools/generate_pages.py` / `tools/render_deepdive.py`
- `tools/validate_deepdive_urls.py` / `tools/audit_all_article_urls.py` (URL ゲート)
- `prompts/email-template.html` / `prompts/obsidian-template.md` / `prompts/obsidian-tagging-spec.md`
- ルーチン構造 (digest → DeepDive → URL gate → push → docs → push → send_push)
- `data/articles.jsonl` スキーマ / 採点軸 (4 軸+ガードレール・Manufacturing 特則・DeepDive 3 軸ゲート・R1-R5) / 強調記法 3 階層 / Pattern D γ schema

## 6. 実行に効く環境事実

- **secrets パス** (sandbox `--add-dir` 対象・workspace 外・生フルパス): `C:\Users\hidek\.secrets\news-grasp-smtp.txt` (Gmail App Password) / `C:\Users\hidek\.secrets\news-grasp-vapid.pem` (Web Push VAPID 秘密鍵)
- **log パス** (`--add-dir` 対象): `C:\Users\hidek\bin\news-grasp-logs\YYYY-MM-DD.log`
- **runner / wrapper** (workspace 外): `C:\Users\hidek\bin\news-grasp-runner.ps1` (226 行) / `C:\Users\hidek\bin\run_claude_with_timeout.ps1`
- 改定後コスト: 移行で News-Grasp 分の月 $362-500 超過を Codex Pro 5x $100 に置換 (Max 5x $100 と並存で計 $200/月)

## 7. 一次ソース (research/04_sources.md より・実装時参照用)

研究フォルダで 200 応答確認済の公式 doc を中心に転記 (販売側ブログ・第三者検証は research/04_sources.md を参照):

- Codex CLI overview: <https://developers.openai.com/codex/cli>
- Codex features (built-in tools / MCP / model): <https://developers.openai.com/codex/cli/features>
- Codex 全 subcommand / flag: <https://developers.openai.com/codex/cli/reference>
- Codex non-interactive (codex exec): <https://developers.openai.com/codex/noninteractive>
- Codex pricing (Plus / Pro 5x / Pro 20x): <https://developers.openai.com/codex/pricing>
- Codex config.toml 全 key (web_search / sandbox / model): <https://developers.openai.com/codex/config-reference>
- Codex hooks (URL ban adapter 用): <https://developers.openai.com/codex/hooks>
- weekly cap 実報告: <https://github.com/openai/codex/issues/19585> (Pro 20x で 2 日枯渇) / <https://github.com/openai/codex/issues/3734>

## 8. 移行の進め方 (実行順)

1. **Phase 0-1** (§1): Codex CLI セットアップ + I1/I4 を実機確定 → sandbox flag と fetch_article_body 要否を決める
2. **Phase 2-3** (§1): digest / DeepDive を Claude 並行生成で品質 PoC (I3 = R1-R5 採点)
3. **Phase 4** (§1): 1 ヶ月 trial で weekly cap (I2) を実測
4. **go なら Phase 5**: §3 の改修 (A-J) + §4 の hook adapter を本実装 → `pytest tests/ -q -m "not network"` 全 PASS を維持 → `test_runner_wrapper_smoke.py` 書換
5. 本番 runner を Codex 経路に切替後、初日は手動監視で digest / DeepDive / URL gate / push / send_push の全段を実機確認
