# Codex Migration Acceptance Matrix

状態: 一部未完了

| ID | Requirement | Status | Evidence |
| --- | --- | --- | --- |
| AM-01 | 作業前 snapshot を取り、既存変更を破壊しない | Green | `docs/codex_migration_evidence_register.md` Snapshot |
| AM-02 | Evidence Register / Assumption Ledger で思い込みを遮断する | Green | `tests/test_codex_migration_evidence_contract.py` |
| AM-03 | PS5.1 UTF-8 JSONL 問題を検証する | Green | `build/verify-candidates/ps51-ai.jsonl`, helper contract tests |
| AM-04 | Codex CLI 現行引数に合わせる | Green | `codex exec --help`, wrapper/model eval tests; `--search` removed |
| AM-05 | 記者・編集者モデルを評価後に決める | Green | `build/model-eval-selection/combo_summary.json`, `build/model-eval-newsroom-editor/newsroom_editor_summary.json` |
| AM-06 | model_policy / prompts / runner を評価結果に同期する | Green | `tools/model_policy.py`, prompts, runner model-policy contract tests |
| AM-07 | Stage0 harvest 全カテゴリ UTF-8 実測 | Green | 7 categories x 50 rows in `build/verify-candidates` |
| AM-08 | Stage1 cross-category dedup 実測 | Green | 350 input / 287 passed / 63 dropped |
| AM-09 | reporter/editor schema 境界を作る | Green | `schemas/reporter_records.schema.json`, `schemas/editor_summary.schema.json` |
| AM-10 | Claude 実行依存ゼロ | Green | runner/wrapper/prompts/tools scan; only negative test strings remain |
| AM-11 | OpenAI API key / SDK 不使用 | Green | runtime scan; only gate/test strings remain |
| AM-12 | `-SmokeTest` pass | Green | 2026-06-14 runner log `SMOKE OK` after newsroom model-policy wiring |
| AM-13 | non-network pytest pass | Green | 2026-06-14 rerun: `.venv\Scripts\python.exe -m pytest tests/ -q --tb=line --no-header -m "not network"` rc=0 |
| AM-14 | RSS registry 根拠付き登録 | Green | `tools/harvest_candidates.py`, `build/rss-registry-verification.json`; 7 feeds HTTP 200 / parsed_items > 0 |
| AM-15 | Stage2 reporter fan-out | Green | runner now invokes category reporter prompts; `tests/test_complete_codex_migration_contract.py` passed |
| AM-16 | Stage3 editor artifact integration | Green | runner writes `$EditorInputManifest` and invokes newsroom editor with `schemas/editor_summary.schema.json` |
| AM-17 | Publish-always per-article quarantine | Yellow | URL gate now quarantines before fallback; other gate classes still use fallback policy |
| AM-18 | URL quarantine / liveness gate | Red | 2026-06-14 manual gate: `audit_all_article_urls.py --gate --match-session` rc=1; HEAD/GET 188/188 OK, but date verification flagged 31 suspected stale records |
| AM-19 | `fetch_article_body.py` decision | Green | `tools/fetch_article_body.py`, `tests/test_fetch_article_body.py`, reporter prompt scoped to reporter-only use |
| AM-20 | Codex hook live payload verification | Green | camelCase/snake_case payload fixture and subprocess hook tests passed |
| AM-21 | E2E 前 no-Codex preflight | Green | `tools.newsroom_preflight`, `-PreflightOnly -NoPush`, prompt/schema/manifest contract tests |
| AM-22 | Full category `-NoPush` E2E | Waived | 2026-06-14 user instruction: Full E2E is unnecessary; verification scope narrowed to reporter artifact resume + editor/downstream gates |
| AM-23 | public HTML gate | Green | 2026-06-14 manual gate: `generate_pages.py` rc=0, `validate_public_home --date 2026-06-14` rc=0, `validate_availability` rc=0 |
| AM-24 | scheduler / push cutover | Blocked | only allowed after all rows Green |

## Completion Rule

この Matrix に Red / Yellow / Blocked が残る限り、完了宣言は禁止する。報告は必ず「一部未完了」とする。
