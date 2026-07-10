# Codex Migration Evidence Register

この register は、News-Grasp の Codex 完全移行で「思い込みによる実装漏れ・破壊」を防ぐための正本である。

- 作成日: 2026-06-14
- 対象 runner: `C:\Users\hidek\bin\news-grasp-runner.ps1`
- 対象 wrapper: `C:\Users\hidek\bin\run_codex_with_timeout.ps1`
- 前提: Codex CLI サブスク認証を使う。OpenAI API key / SDK は使わない。
- 破壊防止: scheduler 変更、push、本番 cutover は Acceptance Matrix 全 Green まで禁止する。

## Snapshot

| Key | Evidence |
| --- | --- |
| repo | `C:\Users\hidek\OneDrive\ドキュメント\ProjectFolders\News-Grasp` |
| branch | `main` |
| head | `50a98311f621562f3e5294e1d1e97f6de48bad1a` |
| runner sha256 | `82FECD58FF63A02E5373E452ECC4FA968F6AC55F378617AB71CB2B9B4C16866B` |
| wrapper sha256 | `8F2B9E53EE41C5CEB4992C9EDF53CC2E51148E9A789FBE91B9CABD9AE8DA5342` |
| scheduler | `\News-Grasp Runner` points to `powershell.exe ... -File "C:\Users\hidek\bin\news-grasp-runner.ps1"` |
| model eval found | `build/model-eval/summary.json` only |
| model eval missing | `build/model-eval-verify/summary.json`, `build/model-eval-selection/summary.json` before this work |

## Evidence Results

| Evidence ID | Result |
| --- | --- |
| EVID-103 | PS5.1 compatible `ProcessStartInfo` path wrote `build/verify-candidates/ps51-ai.jsonl`; Python decoded 5 JSONL rows as UTF-8, no UTF-16 BOM. |
| EVID-104 | `tests/test_complete_codex_migration_contract.py::test_runner_stage0_stdout_writer_is_windows_powershell_51_compatible` and wrapper argument tests passed. |
| EVID-105 | `tools/run_model_eval.py` now separates reporter, style editor, and newsroom editor-in-chief evaluation. |
| EVID-106 | `build/model-eval-selection/combo_summary.json` was generated through `C:\Users\hidek\bin\codex.ps1`; reporter/style-editor combo coverage is complete. |
| EVID-107 | `build/model-eval-selection/combo_summary.json` selected `full__mini-editor` for reporter plus selective style rewrite only. `full__no-editor` tied final quality with lower cost, supporting the hypothesis that style editor should not be always-on. |
| EVID-108 | `tools/model_policy.py`, `prompts/runner-prompt.md`, and `prompts/newsroom-editor-system.md` now separate selective style rewrite from newsroom editor-in-chief selection. |
| EVID-129 | `docs/newsroom_editor_model_eval_framework.md`, `prompts/model-eval-newsroom-editor.md`, and `schemas/newsroom_editor_eval_output.schema.json` define full-duty editor-in-chief evaluation tasks. |
| EVID-130 | `build/model-eval-newsroom-editor/newsroom_editor_summary.json` was generated through `C:\Users\hidek\bin\codex.ps1`; default newsroom editor is `newsroom-editor-mini`, quality leader/escalation is `newsroom-editor-54`. |
| EVID-131 | `C:\Users\hidek\bin\news-grasp-runner.ps1` now reads `newsroom_editor.default` for the main newsroom Codex call; `tests/test_complete_codex_migration_contract.py::test_runner_main_codex_call_uses_newsroom_editor_model_policy` passed. |
| EVID-132 | `rg UseClaude|ClaudeExe|run_claude...` found only negative contract-test strings; `.claude` directory is absent and `.codex` exists. |
| EVID-109 | Stage0 verification generated 50 UTF-8 JSONL candidates each for `fx`, `ai`, `it`, `mobility`, `manufacturing`, `economy`, `game`. |
| EVID-111 | Stage1 verification read 350 candidates and produced 287 passed / 63 dropped candidates in `build/verify-deduped-candidates`. |
| EVID-120 | Operational Claude execution remnants are absent from runner/tools/prompts/schemas/.codex; command-pattern scan returned 0 Claude executable/wrapper hits. |
| EVID-121 | Runtime path has no OpenAI API key / SDK usage; runtime scan returned 0 API/SDK hits after excluding explicit `uses_openai_api_key: False` metadata. |
| EVID-122 | `powershell.exe ... news-grasp-runner.ps1 -SmokeTest` exited 0 and logged `news-grasp-runner.ps1 SMOKE OK` after newsroom model-policy wiring. |
| EVID-123 | `.venv\Scripts\python.exe -m pytest tests/ -q --tb=line --no-header -m "not network"` passed after URL quarantine and thumb restore. |
| EVID-133 | `RSS_FEEDS_BY_CATEGORY` now contains one verified feed per harvest category; `tools.verify_rss_registry` wrote `build/rss-registry-verification.json` with 7/7 feeds OK. |
| EVID-134 | Stage2 runner contract now requires category reporter prompts, reporter model policy, `tools.verify_reporter_output`, and reporter artifact paths; `tests/test_complete_codex_migration_contract.py` passed. |
| EVID-135 | Stage3 runner contract now writes `$EditorInputManifest` with `reporter_artifacts`, `dedup_file`, `source_policy` and invokes newsroom editor with `schemas/editor_summary.schema.json`. |
| EVID-136 | URL liveness failure now runs `audit_all_article_urls.py --gate --match-session --quarantine-articles --apply`, then rechecks before fallback; 2026-06-14 manual gate quarantined 12 session-unverified URLs and final `--gate --match-session` exited 0. |
| EVID-137 | `tools/fetch_article_body.py` exists and is scoped in `prompts/newsroom-reporter-system.md` to reporter-local use only; tests passed. |
| EVID-138 | Codex hook URL extraction accepts snake_case/camelCase and WebSearch/web_search payloads; hook subprocess tests passed. |
| EVID-139 | E2E前 no-Codex preflight was added: `tools.newsroom_preflight`, `schemas/reporter_fanout_return.schema.json`, runner `-PreflightOnly`; `-PreflightOnly -NoPush` exited 0 before git pull/Codex. |
| EVID-140 | `tools/audit_all_article_urls.py` quarantine drop now synchronizes `data/search_audit/<issue>/<category>.json` `selected_total` with surviving digest cards; AM-17 contract tests passed. |
| EVID-141 | 2026-06-14 downstream gate recovered from `thumb` all-null by fetching 19/19 OGP images with `tools.fetch_ogp`, updating `data/articles.jsonl` and 4 digest files, then passing daily-quality/public HTML gates. |
| EVID-142 | `C:\Users\hidek\bin\news-grasp-runner.ps1` now routes content gate failures through `Stop-ContentGateWithoutFallback` instead of publishing fallback notice; `tests/test_runner_convergence_contract.py::test_content_gates_do_not_publish_fallback_notice` passed. |
| EVID-143 | PowerShell syntax pitfalls are blocked in `C:\Users\hidek\.codex\hooks\pre_shell_guard.ps1`: bash heredoc (`<<EOF`) and `Select-Object -Index N..M`; hook contract tests passed. |

## Blocked Results

| Evidence ID | Blocker |
| --- | --- |
| EVID-116 | AM-17 is complete for this non-E2E scope: URL/session/date liveness is quarantine-first, metadata is resynchronized after drops, and content gate failures no longer publish fallback notice. |
| EVID-117 | URL quarantine branch is contract-tested and manually proven on 2026-06-14 artifacts, but full runner `-NoPush` E2E was waived by user instruction and is not claimed. |
| EVID-124 | Full `-NoPush` E2E is explicitly waived for this continuation by 2026-06-14 user instruction; do not use it as cutover evidence. |
| EVID-126 | Acceptance Matrix cannot be all Green while AM-24 remains Blocked; scheduler cutover is still out of scope. |

## Source Documents

| Evidence ID | Source | Evidence |
| --- | --- | --- |
| EVID-001 | `docs/codex_migration_plan_2026-06-06.md` | Phase 0-5 PoC, model quality evaluation, Codex flag confirmation are required before adoption. |
| EVID-002 | `docs/handoff_2026-06-13_codex-migration.md` | Stage0/1/2/3/5, publish-always quarantine, model id confirmation, full E2E are required. |
| EVID-003 | `build/model-eval/summary.json` | Existing artifact recommends `mini-editor`, but does not evaluate `mini-editor-55`. |
| EVID-004 | `tools/model_policy.py` | Current policy keeps reporter/repair on `gpt-5.4`, selects `gpt-5.6-luna` for style editing, `gpt-5.6-terra` for newsroom editing, and `gpt-5.6-sol` for DeepDive. |
| EVID-005 | `C:\Users\hidek\bin\news-grasp-runner.ps1` | Stage0/1 run before Codex; Stage2 currently has single wrapper call plus "fan-out 相当" wording. |
| EVID-006 | `C:\Users\hidek\bin\run_codex_with_timeout.ps1` | Codex CLI wrapper uses `codex exec` through `Start-Process`; no OpenAI SDK path. |
| EVID-007 | `.codex/hooks.json`, `.codex/hooks/append_session_urls.py` | Codex hook exists and must be verified with Codex payload shape, not Claude payload shape. |
| EVID-008 | `tools/harvest_candidates.py` | Google News RSS harvest exists; `RSS_FEEDS_BY_CATEGORY` currently has empty lists. |
| EVID-009 | `tools/audit_all_article_urls.py`, `tools/gate_policy.py` | URL/session/date quarantine path is proven for 2026-06-14 artifacts; full publish-always coverage across non-URL gates remains incomplete. |
| EVID-010 | `tests/test_complete_codex_migration_contract.py` | Existing contract covers Claude execution removal, Stage0/1 order, style-guide, model eval variants. |

## TODO Evidence Map

| Evidence ID | TODO | Evidence file | Current evidence | Implementation permission |
| --- | --- | --- | --- | --- |
| EVID-100 | T00 | git/scheduler/hash snapshot | Dirty tree and runner hashes recorded in this register. | Allowed: record only. |
| EVID-101 | T01 | this file | Evidence register exists and maps T00-T28. | Allowed. |
| EVID-102 | T02 | `docs/codex_migration_assumption_ledger.md` | Unproven claims are rejected or blocked. | Allowed after ledger exists. |
| EVID-103 | T03 | runner helper + PS5.1 smoke | `Invoke-PythonStdoutFileUtf8` uses `ProcessStartInfo.Arguments`; decode must be verified. | Test before runner edit. |
| EVID-104 | T04 | wrapper contract | wrapper uses `Start-Process -ArgumentList $effectiveArgString`; PS5.1 smoke required. | Test before wrapper edit. |
| EVID-105 | T05 | `tools/run_model_eval.py` | Variants exist but editor task scope must be separated. | Test before policy edit. |
| EVID-106 | T06 | model eval output | Reporter/style-editor optimality is covered by combo evaluation only. | Evaluation required before adoption. |
| EVID-107 | T07 | model eval output | `mini-editor-55` was compared as style rewrite combo, not editor-in-chief. | Do not use for newsroom editor adoption. |
| EVID-108 | T08 | `tools/model_policy.py` | Current policy must not treat style rewrite as full editor-in-chief. | Full-duty eval required. |
| EVID-109 | T09 | Stage0 build artifacts | Harvest must produce UTF-8 JSONL per category. | Dry-run allowed. |
| EVID-110 | T10 | `tools/harvest_candidates.py` | RSS registry now has verified URLs; artifact is `build/rss-registry-verification.json`. | Done; reverify before feed changes. |
| EVID-111 | T11 | Stage1 build artifacts | Dedup must read Stage0 UTF-8 output. | Dry-run allowed. |
| EVID-112 | T12 | runner contract | Category reporter fan-out is now physically wired in runner. | Done; full E2E still required. |
| EVID-113 | T13 | `schemas/reporter_records.schema.json` | Reporter schema now exists as a boundary. | Reporter artifacts must validate. |
| EVID-114 | T14 | runner/editor contract | Editor manifest now constrains input to reporter artifacts and Stage1 dedup. | Done; full E2E still required. |
| EVID-115 | T15 | `schemas/editor_summary.schema.json` | Editor schema now exists as a boundary. | Editor artifacts must validate. |
| EVID-116 | T16 | runner/gate contract | URL/session/date gate is quarantine-first; content gates do not publish fallback notice. | Done for non-E2E scope. |
| EVID-117 | T17 | quarantine fixture | Runner branch is contract-tested; 2026-06-14 artifacts were manually quarantined/rechecked; full E2E waived and not claimed. | E2E no longer required for this continuation; still not cutover evidence. |
| EVID-118 | T18 | handoff Step A | `fetch_article_body.py` implemented for reporter-local body snippets. | Done. |
| EVID-119 | T19 | Codex hook payload | Snake/camel payload fixtures and subprocess hook execution passed. | Done. |
| EVID-120 | T20 | command-pattern `rg` audit | Claude executable/wrapper command hits are 0 in runner/tools/prompts/schemas/.codex. | Done. |
| EVID-121 | T21 | OpenAI API/SDK `rg` audit | API key / SDK runtime hits are 0 after excluding false metadata. | Done. |
| EVID-122 | T22 | runner smoke log | `-SmokeTest` must pass on Windows PowerShell 5.1. | Smoke required. |
| EVID-123 | T23 | pytest output | Current dirty tree must pass non-network tests. | Required. |
| EVID-139 | T24 | runner preflight log | `-PreflightOnly` must pass before Full E2E. | Done. |
| EVID-124 | T25 | runner NoPush log | Full category E2E was waived by user instruction and is not cutover evidence. | Waived for this continuation. |
| EVID-125 | T26 | HTML gate output | Local/public HTML gates passed after manual artifact repair and page generation. | Done. |
| EVID-126 | T26 | Acceptance Matrix | Matrix still has AM-24 Blocked because scheduler cutover is out of scope. | Required before cutover claim. |
| EVID-127 | T27 | `rg` review output | runner/prompts/tests/tools/.codex/.claude must be reviewed. | Required. |
| EVID-128 | T28 | scheduler/push evidence | Cutover is last and only after T00-T27 Green. | Blocked until Matrix Green. |
