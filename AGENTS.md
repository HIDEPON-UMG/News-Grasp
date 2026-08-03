# Repo Agent Context

This is the root routing contract for Claude Code and Codex.

## Root Workflow Contract

### Same-day public recovery constitution

- `same_day_public_recovery_first`: 対象日の公開面が Definition of Done を満たさない間は、同日公開の typed recovery を最優先の作業とする。
- 公開 Green 前に許されるのは `scheduled_recovery`、その開始に不可欠な `minimal_recovery_unblocker`、または復旧不能を明示する `escalate_major_incident` だけである。
- `incident_report_polish`、`root_cause_hardening`、無関係な cleanup は公開 Green の後に行う。公開欠落を報告作成やハーネス改善より後回しにしてはならない。
- この順序は `tools.audit_recovery_control` の sealed decision と 6:40 automation が同じ predicate で fail-closed に強制する。

- Keep sibling `CLAUDE.md` and `AGENTS.md` files aligned. Claude Code consumes `CLAUDE.md`; Codex consumes `AGENTS.md`.
- Treat `docs/spec.md` as stable product truth, `tasks/current.md` as a derived status snapshot, and `tasks/todos.md` as the deferred-goal ledger; current execution stays in the active plan's `## Task Breakdown`.
- For any 非自明な News-Grasp 改修, treat `docs/spec.md` as the 上位プロダクト真実 and check whether the change preserves the mission, Definition of Done, repair-first principle, and system integrity before implementation.
- Do not work from memory or local test results alone when judging News-Grasp completion. Read `docs/spec.md` before any non-trivial recovery, incident report, E2E judgement, publish readiness claim, or "complete / perfect / self-running / no bugs" answer, then cite the relevant sections in the plan and final report.
- Always treat self-running operation, no known bugs, and completion-readiness as baseline acceptance conditions for non-trivial News-Grasp work. Do not wait for the user to ask for perfection. Do not claim completion or stop work until `docs/spec.md` Definition of Done, affected matrix rows, runtime state, public surface, and fresh E2E/dry-run evidence for the same run intent are all Green. An external, permission, safety, or outside-state condition defers only the affected operation as `operation_deferred`; it never creates a task-level terminal state, and every reachable alternate critical path continues.
- For any feature addition, deletion, or behavioral fix, explicitly name the affected `docs/spec.md` `Feature Change Quality Gate Matrix` row(s) in the plan and final report. If no existing row covers the feature, update the matrix and `tests/test_product_spec_contract.py` in the same change before calling the work complete.
- For any News-Grasp bugfix, incident, recovery, E2E failure, runner failure, repair failure, or publish verification failure, apply `docs/spec.md` `Incident Bugfix Horizontal Investigation Covenant`: runner / repair / state / report の横並び調査を同じ incident 単位で実施し、1 レーンでも未調査なら修正完了にしてはならない. Reflect new or reclassified incidents in `tools.historical_failure_scenarios` and `tests/test_historical_failure_scenarios.py`.
- For any repair change, apply `docs/spec.md` `Repair Decision Debt Covenant` before adding downstream checks: validator emits structured issues, coverage matrix owns routing/status, orchestrator preserves an ordered issue ledger, registry owns handler/scope integrity, and runner passes only selected issue artifacts. Normal daily runner fallback publish is forbidden; `fallback_ok` / `published_fallback_with_notice` are historical or manual-emergency evidence only, never terminal success.
- Treat `docs/researches/`, `tasks/lessons.md`, and `.ai/harness/policy.json` as durable workflow context.
- Use `.ai/context/context-map.json` and `.ai/context/capabilities.json` to discover functional-block contracts.
- Do not infer local `CLAUDE.md` or `AGENTS.md` files from broad physical layouts such as `apps/*`, `packages/*`, or `services/*`.
- Put capability-specific ownership, entrypoints, and verification commands in explicitly selected functional-block contracts.
- Keep root context concise; route deep implementation detail into plans, task notes, research, workstreams, or architecture docs.
- Treat `_ref/` as ignored external reference material and `_ops/` as ignored local operations state.
- Prefer repo-local workflow artifacts over tool-specific chat memory.
- **News-Grasp 通常公開 inventory 必須** (`news-grasp-publish-inventory-required`): News-Grasp の通常公開・本日分公開・途中再開を完了報告する場合、7カテゴリ digest、Summary、DeepDive md、DeepDive HTML、日付 docs、`docs/publish-status.json` の `published_ok`、公開 URL sentinel、`validate_daily_quality --require-deepdive` の証跡を必ず列挙する。公開に必要なコンテンツが 1 つでも欠ける場合は、正当な欠落理由と検証 gate を明記し、完了と言わない。
- When a daily batch stops midway, publish verification fails, recovery is requested, or any News-Grasp incident investigation is requested, capture incident evidence without publishing a new report by default. Do not place new `docs/incidents/*-report.html` files in git or GitHub Pages. If an HTML evidence report is required, write it under untracked `build/incidents/` unless the user separately approves public publication; if HTML is produced, validate it with `python tools/validate_incident_report_design.py <report>` before completion.
- DeepDive chart series colors must be unique within each chart. Do not publish a chart whose legend maps different series to the same color; enforce this in `tools/render_deepdive.py` and keep a pytest contract in `tests/test_deepdive_render.py`.
