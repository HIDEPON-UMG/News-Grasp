# Codex Migration Assumption Ledger

unresolved_implementation_assumptions: 0

この ledger は、未検証の仮定を「実装済み」と誤認しないための遮断表である。未検証項目は Green ではなく、`Blocked` または `Rejected` として扱う。

| Claim | Why risky | Status | Required evidence |
| --- | --- | --- | --- |
| gpt-5.5 editor 採用済み | `full__mini-editor-55` は final quality 5.0 で同点だが total cost 8.3 のため `full__mini-editor` より劣る。 | False | `build/model-eval-selection/combo_summary.json` で `full__mini-editor-55` が winner になること。 |
| 編集長本体モデル選定済み | 既存 combo 評価は style rewrite のみだったが、full-duty 評価を追加して実測した。 | Resolved | `build/model-eval-newsroom-editor/newsroom_editor_summary.json` が coverage complete で winner を持つこと。 |
| 文体 editor 常設が必要 | `full__no-editor` と `full__mini-editor` が同品質であり、常設 editor の費用対効果は未証明。 | False | style rewrite ありの combo が no-editor を品質で上回る fixture 結果。 |
| Stage2 fan-out 実装済み | runner は単一 wrapper 呼び出しで、文言に `fan-out 相当` が残る。 | False | カテゴリ別 Codex exec artifact と runner contract test。 |
| RSS registry 完備 | `RSS_FEEDS_BY_CATEGORY` は空 list。 | False | 各 feed の HTTP 200 / XML parse / category relevance artifact。 |
| Publish-always 完了 | runner は複数 gate 失敗で fallback publish へ落ちる。 | False | 個別記事 quarantine/drop で通常号継続する E2E。 |
| Codex hook 実 payload 検証済み | 既存 hook test はあるが、今回の移行後 payload は再確認が必要。 | Blocked | Codex PostToolUse payload fixture または live exec log。 |
| OpenAI API key / SDK 不使用 | wrapper は CLI 経由だが runtime 全体監査は未完了。 | Blocked | runtime path `rg OPENAI_API|from openai|import openai|api_key` 監査。 |
| E2E 完了 | `-SmokeTest` / non-network pytest / full `-NoPush` / HTML gate が未完了。 | Blocked | 各コマンドの exit 0 と log artifact。 |

## Rule

- `False` は採用禁止。
- `Blocked` は検証完了まで実装・完了宣言禁止。
- `Resolved` に変更できるのは、証拠ファイルまたはコマンド出力を Evidence Register に追記した後だけ。
