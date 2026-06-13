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
| EVID-120 | Operational Claude execution remnants are absent from runner/wrapper/prompts/tools; remaining matches are negative contract tests only. |
| EVID-121 | Runtime path has no OpenAI API key / SDK usage; matches are gate/test strings only. |
| EVID-122 | `powershell.exe ... news-grasp-runner.ps1 -SmokeTest` exited 0 and logged `news-grasp-runner.ps1 SMOKE OK` after newsroom model-policy wiring. |
| EVID-123 | `.venv\Scripts\python.exe -m pytest tests/ -q -m "not network"` passed on the current tree. |
| EVID-133 | `RSS_FEEDS_BY_CATEGORY` now contains one verified feed per harvest category; `tools.verify_rss_registry` wrote `build/rss-registry-verification.json` with 7/7 feeds OK. |
| EVID-134 | Stage2 runner contract now requires category reporter prompts, reporter model policy, `tools.verify_reporter_output`, and reporter artifact paths; `tests/test_complete_codex_migration_contract.py` passed. |
| EVID-135 | Stage3 runner contract now writes `$EditorInputManifest` with `reporter_artifacts`, `dedup_file`, `source_policy` and invokes newsroom editor with `schemas/editor_summary.schema.json`. |
| EVID-136 | URL liveness failure now runs `audit_all_article_urls.py --gate --match-session --quarantine-articles --apply`, then rechecks before fallback. |
| EVID-137 | `tools/fetch_article_body.py` exists and is scoped in `prompts/newsroom-reporter-system.md` to reporter-local use only; tests passed. |
| EVID-138 | Codex hook URL extraction accepts snake_case/camelCase and WebSearch/web_search payloads; hook subprocess tests passed. |
| EVID-139 | E2E前 no-Codex preflight was added: `tools.newsroom_preflight`, `schemas/reporter_fanout_return.schema.json`, runner `-PreflightOnly`; `-PreflightOnly -NoPush` exited 0 before git pull/Codex. |

## Blocked Results

| Evidence ID | Blocker |
| --- | --- |
| EVID-116 | Publish-always is still partial; URL liveness is quarantine-first, but several non-URL gates still fallback by design or pending policy split. |
| EVID-117 | URL quarantine branch is contract-tested, but full runner `-NoPush` E2E is not proven after the fan-out rewrite. |
| EVID-124 | Full `-NoPush` E2E is blocked while the worktree is dirty, because runner may create local commits. |
| EVID-125 | public HTML gate is blocked until full E2E creates the candidate public artifact. |
| EVID-126 | Acceptance Matrix cannot be all Green while EVID-116/117/124/125 remain Yellow/Blocked. |

## Source Documents

| Evidence ID | Source | Evidence |
| --- | --- | --- |
| EVID-001 | `docs/codex_migration_plan_2026-06-06.md` | Phase 0-5 PoC, model quality evaluation, Codex flag confirmation are required before adoption. |
| EVID-002 | `docs/handoff_2026-06-13_codex-migration.md` | Stage0/1/2/3/5, publish-always quarantine, model id confirmation, full E2E are required. |
| EVID-003 | `build/model-eval/summary.json` | Existing artifact recommends `mini-editor`, but does not evaluate `mini-editor-55`. |
| EVID-004 | `tools/model_policy.py` | Current policy uses `gpt-5.4-mini` reporter/editor default and `gpt-5.4` escalation. |
| EVID-005 | `C:\Users\hidek\bin\news-grasp-runner.ps1` | Stage0/1 run before Codex; Stage2 currently has single wrapper call plus "fan-out 相当" wording. |
| EVID-006 | `C:\Users\hidek\bin\run_codex_with_timeout.ps1` | Codex CLI wrapper uses `codex exec` through `Start-Process`; no OpenAI SDK path. |
| EVID-007 | `.codex/hooks.json`, `.codex/hooks/append_session_urls.py` | Codex hook exists and must be verified with Codex payload shape, not Claude payload shape. |
| EVID-008 | `tools/harvest_candidates.py` | Google News RSS harvest exists; `RSS_FEEDS_BY_CATEGORY` currently has empty lists. |
| EVID-009 | `tools/audit_all_article_urls.py`, `tools/gate_policy.py` | URL quarantine primitives exist; runner integration is not proven complete. |
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
| EVID-116 | T16 | runner/gate contract | URL gate is quarantine-first; broader publish-always policy remains partial. | Further policy work required. |
| EVID-117 | T17 | quarantine fixture | Runner branch is contract-tested; full E2E not proven. | E2E required. |
| EVID-118 | T18 | handoff Step A | `fetch_article_body.py` implemented for reporter-local body snippets. | Done. |
| EVID-119 | T19 | Codex hook payload | Snake/camel payload fixtures and subprocess hook execution passed. | Done. |
| EVID-120 | T20 | `rg claude|Claude|...` | Operational Claude remnants must be zero or classified. | Review required. |
| EVID-121 | T21 | `rg OPENAI_API|api_key|...` | API/SDK fallback must be zero in runtime path. | Review required. |
| EVID-122 | T22 | runner smoke log | `-SmokeTest` must pass on Windows PowerShell 5.1. | Smoke required. |
| EVID-123 | T23 | pytest output | Current dirty tree must pass non-network tests. | Required. |
| EVID-139 | T24 | runner preflight log | `-PreflightOnly` must pass before Full E2E. | Done. |
| EVID-124 | T25 | runner NoPush log | Full category E2E must skip push/send_push. | Required before cutover. |
| EVID-125 | T26 | HTML gate output | Local/public HTML gates must not be conflated. | Required. |
| EVID-126 | T26 | Acceptance Matrix | All rows must be Green with evidence. | Required before completion claim. |
| EVID-127 | T27 | `rg` review output | runner/prompts/tests/tools/.codex/.claude must be reviewed. | Required. |
| EVID-128 | T28 | scheduler/push evidence | Cutover is last and only after T00-T27 Green. | Blocked until Matrix Green. |
