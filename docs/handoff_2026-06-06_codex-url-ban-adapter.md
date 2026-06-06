# Handoff: Codex 用 URL fabrication ban hook adapter の実装

- **作成日**: 2026-06-06
- **状態**: 設計確定・実装未着手（別セッションで実装する方針をユーザー承認済み）
- **対象**: News-Grasp の `.codex/hooks.json` + `.codex/hooks/append_session_urls.py`（現状 untracked）
- **前提読み物**: [[feedback_llm_url_fabrication_ban]] / [[feedback_check_design_principles]] / `~/.claude/CLAUDE.md` の memory dispatch 規約

## 1. 背景と問題

URL fabrication ban の Lv2 境界 hook（[[feedback_check_design_principles]] 「境界 1 箇所集約」）として、
WebSearch / WebFetch で観測した URL を `data/_session_urls.json` にハーネス層が直接 append し、
`tools/audit_all_article_urls.py --gate --match-session` が「session 白リストに無い URL = 記憶から捏造」を
fatal 化する設計。Claude Code 側は `.claude/hooks/append_session_urls.py`（commit 済 + 契約テスト
`tests/test_append_session_urls_hook.py`）で完成している。

2026-06-06 早朝に Codex 側対応として `.codex/hooks.json` + `.codex/hooks/append_session_urls.py`
（`.claude/hooks/` の完全コピー）を作ったが、**Codex CLI 実機検証で一切発火しないことが判明した**。

## 2. Codex 実機検証で判明した「3 つの壊れ」（codex-cli 0.137.0-alpha.4）

| # | 壊れている点 | 詳細 |
|---|---|---|
| 1 | **matcher 不一致** | Codex の first-party web 検索 tool 名は `web_search`（Claude Code は `WebSearch`）。現 matcher `WebSearch\|WebFetch` は Codex の `{"type":"item.started","item":{"type":"web_search"}}` に一致しない |
| 2 | **env 未展開** | Codex CLI は `${CLAUDE_PROJECT_DIR}` を展開しない（`CODEX_PROJECT_DIR` も存在しない）。安定 env は `CODEX_HOME` 等のみ。hook command は session `cwd` で実行される |
| 3 | **payload parser 不一致** | `append_session_urls.py` の `extract_urls_from_event` は `tool_name == "WebSearch"/"WebFetch"` 前提。Codex の `web_search` payload では URL 抽出が空になる |

実機ログ要点（Codex 側で検証済）:
- `codex exec` で実 Web 検索 → `item.type=web_search` / exit 0 だが `_session_urls.json` の mtime 変化なし・audit log 不在
- hook script 自体は正しい Python 絶対パス + Claude payload サンプルでは正常動作（`tool=WebFetch urls=1`）→ **配線だけの問題**

### Codex hook 仕様の確定事項

- `.codex/hooks.json` の配置自体は OK。`.codex/config.toml` で `hooks = "<absolute>"` を別途指定する方式**ではない**（信頼済み project layer の `.codex/hooks.json` が探索される）
- hooks 機能は `features.hooks` が canonical で**既定 ON**（`codex_hooks` は deprecated alias）。通常 `[features] hooks = true` の明示は不要
- **初回は CLI の `/hooks` で対象 hook を review/trust する必要がある**。automation では `--dangerously-bypass-hook-trust`
- Windows 用コマンドは `commandWindows` フィールドで指定できる
- 参照: https://developers.openai.com/codex/hooks / https://developers.openai.com/codex/config-basic

## 3. 実装方針（adapter 分離 + 共通 merge 共有）

[[feedback_check_design_principles]] に沿って、Claude / Codex の tool 名・payload 差分を混ぜず、
**Lv2 境界（merge ロジック）を 1 箇所共有**しつつ payload 抽出だけを各ハーネス用に分ける。

```
.claude/hooks/append_session_urls.py   ← Claude payload 専用（現状維持・変更しない）
.codex/hooks/append_session_urls.py    ← Codex web_search payload を読む。共通 merge 関数を import or 内製コピー
data/_session_urls.json                ← 共通出力先（schema 不変: {"date":"YYYY-MM-DD","urls":[...]}）
```

### 修正 1: `.codex/hooks.json`

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "^web_search$",
        "hooks": [
          {
            "type": "command",
            "commandWindows": "\"C:\\Users\\hidek\\OneDrive\\ドキュメント\\ProjectFolders\\News-Grasp\\.venv\\Scripts\\python.exe\" \"C:\\Users\\hidek\\OneDrive\\ドキュメント\\ProjectFolders\\News-Grasp\\.codex\\hooks\\append_session_urls.py\"",
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

> 絶対パス hardcode は移植性が低いトレードオフがあるが、Codex は env 未展開なので最も事故りにくい。
> 移植性が要るなら wrapper で `git rev-parse --show-toplevel` から repo root 解決（ただし Windows quoting と
> stdin 継承の追加検証が必要）。News-Grasp は本機専用運用なので絶対パスで十分。
> **個人パス hardcode を含むため、commit 時の safe-commit ゲート 1 で「本機専用ファイル」と明示判断が要る**。

### 修正 2: `.codex/hooks/append_session_urls.py`

- `extract_urls_from_event` に `tool_name == "web_search"` 分岐を追加（または Codex 専用 `extract_urls_from_codex_event`）
- **未確認 → 実装第一歩で要調査**: Codex の `web_search` **完了** payload schema（`item.started` ではなく結果を含む payload）で URL がどのキーに入るか。`codex exec --json` の完了 item を実機で dump して確定する
- `merge_into_session_file` / `_strip_tail` / `_append_audit` は Claude 版とロジック同一 → 共有 or コピー。共有するなら共通モジュール（例 `tools/session_urls_core.py`）に切り出し、両 hook が import

### 修正 3: 契約テスト（Lv4・必須）

- `tests/test_append_session_urls_hook.py` に Codex payload fixture を追加、または `tests/test_codex_append_session_urls_hook.py` を新設
- pin する不変条件: Codex `web_search` 完了 payload → `extract_urls` が観測 URL を返す / `merge_into_session_file` が共通 schema で append / 空・壊れ payload で exit 0
- **Lv5 個別 smoke 単独の完了報告は禁止**（dispatch 規約）。最低この契約テスト 1 件を含める

### ProjectFolders CLAUDE.md ルールとの整合（要対応）

> 「新規または変更する hook は、可能な限り `hook_runtime.py` の payload 正規化層を使い、Claude/Codex 両 fixture の契約テストを追加する」

- **News-Grasp には現状 `hook_runtime.py` が存在しない**（未整備）。実装時に (a) 軽量な正規化層を新設して両 hook を載せる、(b) News-Grasp 単独では正規化層を作らず両 hook で payload 分岐を持つ、のどちらかを選ぶ
- 推奨は (a) の簡易版（`tools/session_urls_core.py` に `normalize_event(harness, raw) -> set[str]` を置き、Claude/Codex 両 fixture でテスト）。これが上記ルールに最も沿う

## 4. 影響範囲（着手前 Grep 済・全列挙）

session_urls を消費・言及する全ファイル（`.venv` 除く）:

- **出力・消費**: `.claude/hooks/append_session_urls.py`（変更しない）/ `.claude/settings.json`（変更しない）/ `.codex/hooks/append_session_urls.py`（**改修対象**）/ `.codex/hooks.json`（**改修対象**）/ `data/_session_urls.json`（共通出力・schema 不変）/ `tools/audit_all_article_urls.py`（`--match-session` 消費側・変更しない）
- **テスト**: `tests/test_append_session_urls_hook.py`（**Codex fixture 追加 or 新設**）/ `tests/test_session_urls_match.py`（match gate・回帰確認のみ）
- **prompts（言及のみ・変更不要）**: `prompts/routine-system.md` / `prompts/deepdive-research-system.md` / `prompts/deepdive-runner-prompt.md`
- **handoff（履歴）**: `handoff_2026-06-04_url-fabrication-audit-action.md` / `docs/handoff_2026-06-05_*.md`

## 5. 運用前提（なぜ優先度が中なのか）

- News-Grasp の**本番 daily runner は `news-grasp-runner.ps1` の `claude.exe` 専用**（digest / DeepDive 両方）。Codex は本番運用で一切使わない
- つまり Codex hook は「Codex で News-Grasp 記事を手動 WebSearch するとき」だけ効く保険。本番の URL fabrication ban は `.claude/hooks/` で完全カバー済
- 優先度は中。Codex で News-Grasp を触る運用を実際に始めるタイミングで実装するのが費用対効果的に正しい

## 6. 完了条件（DoD）

1. Codex 実機（`codex exec` または `/hooks` trust 後の TUI）で実 WebSearch → `data/_session_urls.json` に URL が append され、audit log が増える実機検証ログを残す
2. 契約テスト（Codex payload fixture）が PASS（`pytest tests/ -q -m "not network"` 全件 PASS を維持）
3. `.codex/hooks.json` の個人パス hardcode を safe-commit ゲート 1 で「本機専用」と明示してから commit
4. `harness_mapping.md` に Claude `.claude/hooks/` ⇔ Codex `.codex/hooks/` の対応・tool 名差分（`WebSearch` vs `web_search`）・env 未展開の制約を追記
5. push は明示指示があるときだけ
