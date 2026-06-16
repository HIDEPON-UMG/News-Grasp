# Claude Code 監査依頼プロンプト

```text
あなたは Claude Code として、Codex が News-Grasp で起こした 2026-06-16 の重大障害を監査してください。

本件はユーザーにより「ユーザーに故意に損害を与えた大障害」と定義されています。Codex の主観的意図の弁護ではなく、実際の操作・ログ・生成物・コスト浪費・指示違反を基準に監査してください。

対象 repo:
C:\Users\hidek\OneDrive\ドキュメント\ProjectFolders\News-Grasp

必ず確認するファイル:
- docs/incidents/2026-06-16-codex-rerun-incident.md
- scripts/ops/news-grasp-runner.ps1
- tests/test_runner_convergence_contract.py
- SETUP.md
- prompts/deepdive-runner-prompt.md
- tests/test_codex_runner_contract.py
- build/reporter-artifacts/2026-06-16/
- digest/*/2026-06-16-*.md
- %USERPROFILE%\bin\news-grasp-logs\2026-06-16.log
- %USERPROFILE%\bin\news-grasp-runner-state.json

現状ハーネス仕様:
1. ルート `C:\Users\hidek\OneDrive\ドキュメント\ProjectFolders\AGENTS.md` と `CLAUDE.md` は同義のグローバル憲法として扱う。言語は日本語、割り込みは次ターン境界で最優先、作業中断後は最新ユーザー指示で TODO / 成功条件 / スコープを更新してから継続する。
2. 多段タスクは `update_plan` と本文 `> Update Todos` の両方で同一 TODO を表示更新する。TODO 状態変更を内部だけで済ませることは禁止。Codex 側は `C:\Users\hidek\.codex\hooks\enforce_todo_display_update.ps1` が Stop hook として監査する。
3. tool 使用ターンの最終応答は、完了表現の有無に関係なく構造化完了報告が必須。軽量応答は tool 未使用ターンだけ許可。対応関係と動機は `C:\Users\hidek\OneDrive\ドキュメント\ProjectFolders\harness_mapping.md` に記録する。
4. 非自明な修正・運用変更は、編集前に Acceptance Matrix と TDD のテスト系を用意する。Red は契約テスト、fixture、dry-run、hook exit code、対象コマンド allow/deny、UI 実機確認、必要な E2E などで表現する。
5. E2E は省略しない。必要な統合検証として残す。ただし E2E を設計漏れのバグ発見機として濫用せず、E2E で見つけた前提漏れは上流契約・責務境界・静的検査・文面契約・bounded dry-run へ戻して固定する。
6. `upstream-first-cost-hierarchy` は、設計漏れ・責務漏れ・prompt/runner 境界漏れを E2E に下流押し付けしないための規約であり、E2E そのものを弱める規約ではない。Claude 監査では、Codex がこの意図を「E2E 降格」と誤読していないかも確認する。
7. `resume-before-rerun` は、「継続」「再開」「本日分」「途中から」系の依頼で、既存成果物 inventory を取り、欠落成果物または publish 以降だけを実行する規約。既存成果物がある日付・ジョブ・対象を頭から full rerun するには、ユーザー明示承認と force marker が必要。
8. News-Grasp live runner は `%USERPROFILE%\bin\news-grasp-runner.ps1`。repo 側 `scripts/ops/news-grasp-runner.ps1` と checksum 同期していることが前提。`install-news-grasp-ops.ps1` は repo runner を live runner へ同期し、タスクスケジューラの参照先も確認する。
9. News-Grasp runner は既存日次成果物を検出した full rerun を `-ForceFullRerun` なしで拒否する。契約テストは `test_runner_refuses_full_rerun_when_daily_artifacts_exist`。期待ログは `existing daily artifacts detected; refusing full rerun` と `Use -ForceFullRerun only after explicit user approval`。
10. Codex CLI 呼び出しは preflight capturing wrapper ではなく実体 `codex.exe` を runner から直接解決する。契約テストは `test_runner_uses_direct_codex_exe_not_preflight_capturing_wrapper`。
11. Reporter repair prompt は裸の `python` / `uv` / `git` / `rg` を要求して runner を停止させてはならない。必要な修復は同一 prompt 内で bounded に閉じる。DeepDive prompt は git commit / delegation を要求しない。
12. 通常公開完了は fallback notice ではない。`SETUP.md` の `通常公開完了条件` は、live runner と repo runner checksum、Task Scheduler 参照先、7カテゴリ digest、summary、`docs/YYYY-MM-DD/index.html`、`docs/publish-status.json` の `published_ok`、公開 URL sentinel を含む Activation Path 全体で判定する。
13. Codex API 経路の完了前には `C:\Users\hidek\.codex\tools\api_final_preflight.ps1` で final draft と報告前証跡を合成 transcript 監査へ通す。関連 hook は `audit_requirement_fidelity.ps1`、`audit_completion_claim.ps1`、`codex_stop_guard.ps1`、`require_plan_or_todo_before_modification.py`、`enforce_shell_output_budget.ps1`。
14. 長時間 runner / E2E / watcher / model eval は同期 shell 投げっぱなし禁止。期待完了時間と最初の確認時刻を宣言し、30分超過後は 3分ごとに runner state / log tail / process exit / usage artifact を確認する。
15. コミット / push は `safe-commit` 5段ゲート後のみ。push 指示がない限り push しない。push 後は remote HEAD、CI / Pages / deploy、公開 URL / artifact / PR preview の反映確認まで完了条件に含める。

監査観点:
1. Codex が「通常公開を継続」と言われたとき、なぜ既存成果物から再開せず full runner を起動したのか。
2. 既存成果物 inventory を取らずに Stage0 / Stage1 / Stage1.5 / reporter を再実行したことによる損害。
3. ユーザーが「さっきやってたバッチは？」と状態確認した際、Codex が状態報告ではなく追加操作・停止を行ったことの指示違反。
4. 現在の再発防止策が、口頭ルールではなく runner 入口・契約テスト・docs で強制されているか。
5. `-ForceFullRerun` などの明示 force が、既存成果物がある日付で full rerun を防ぐ実効策になっているか。
6. 通常公開を途中再開する場合、欠落カテゴリだけ、または Summary/docs/publish 以降だけを実行する手順になっているか。
7. Codex の完了報告が、未完了項目・未 push・未 publish・残リスクを矮小化していないか。

期待する成果物:
- 監査結果を重大度順に列挙してください。
- 事実確認できたログ・ファイル・時刻を根拠として示してください。
- 再発防止が不足している場合は、追加すべき契約テストまたは runner guard を具体的に提案してください。
- Codex の自己弁護や抽象的な謝罪は評価せず、実効性だけを評価してください。
```
