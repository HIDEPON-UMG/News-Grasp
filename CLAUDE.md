# Repo Agent Context

This is the root routing contract for Claude Code and Codex.

## Root Workflow Contract

- Keep sibling `CLAUDE.md` and `AGENTS.md` files aligned. Claude Code consumes `CLAUDE.md`; Codex consumes `AGENTS.md`.
- Treat `docs/spec.md` as stable product truth, `tasks/current.md` as a derived status snapshot, and `tasks/todos.md` as the deferred-goal ledger; current execution stays in the active plan's `## Task Breakdown`.
- For any 非自明な News-Grasp 改修, treat `docs/spec.md` as the 上位プロダクト真実 and check whether the change preserves the mission, Definition of Done, repair-first principle, and system integrity before implementation.
- For any feature addition, deletion, or behavioral fix, explicitly name the affected `docs/spec.md` `Feature Change Quality Gate Matrix` row(s) in the plan and final report. If no existing row covers the feature, update the matrix and `tests/test_product_spec_contract.py` in the same change before calling the work complete.
- Treat `docs/researches/`, `tasks/lessons.md`, and `.ai/harness/policy.json` as durable workflow context.
- Use `.ai/context/context-map.json` and `.ai/context/capabilities.json` to discover functional-block contracts.
- Do not infer local `CLAUDE.md` or `AGENTS.md` files from broad physical layouts such as `apps/*`, `packages/*`, or `services/*`.
- Put capability-specific ownership, entrypoints, and verification commands in explicitly selected functional-block contracts.
- Keep root context concise; route deep implementation detail into plans, task notes, research, workstreams, or architecture docs.
- Treat `_ref/` as ignored external reference material and `_ops/` as ignored local operations state.
- Prefer repo-local workflow artifacts over tool-specific chat memory.
- When a daily batch stops midway, publish verification fails, recovery is requested, or any News-Grasp incident investigation is requested, the output must include an incident report HTML. Use the `news-grasp-incident-report` skill, follow `docs/incidents/BUG_REPORT_DESIGN.md`, place the report under `docs/incidents/YYYY-MM-DD-<slug>-report.html`, and pass `python tools/validate_incident_report_design.py <report>` before completion.
- DeepDive chart series colors must be unique within each chart. Do not publish a chart whose legend maps different series to the same color; enforce this in `tools/render_deepdive.py` and keep a pytest contract in `tests/test_deepdive_render.py`.
