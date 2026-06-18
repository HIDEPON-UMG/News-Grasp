# Repo Agent Context

This is the root routing contract for Claude Code and Codex.

## Root Workflow Contract

- Keep sibling `CLAUDE.md` and `AGENTS.md` files aligned. Claude Code consumes `CLAUDE.md`; Codex consumes `AGENTS.md`.
- Treat `docs/spec.md` as stable product truth, `tasks/current.md` as a derived status snapshot, and `tasks/todos.md` as the deferred-goal ledger; current execution stays in the active plan's `## Task Breakdown`.
- Treat `docs/researches/`, `tasks/lessons.md`, and `.ai/harness/policy.json` as durable workflow context.
- Use `.ai/context/context-map.json` and `.ai/context/capabilities.json` to discover functional-block contracts.
- Do not infer local `CLAUDE.md` or `AGENTS.md` files from broad physical layouts such as `apps/*`, `packages/*`, or `services/*`.
- Put capability-specific ownership, entrypoints, and verification commands in explicitly selected functional-block contracts.
- Keep root context concise; route deep implementation detail into plans, task notes, research, workstreams, or architecture docs.
- Treat `_ref/` as ignored external reference material and `_ops/` as ignored local operations state.
- Prefer repo-local workflow artifacts over tool-specific chat memory.
- When a daily batch stops midway, publish verification fails, recovery is requested, or any News-Grasp incident investigation is requested, the output must include an incident report HTML. Use the `news-grasp-incident-report` skill, follow `docs/incidents/BUG_REPORT_DESIGN.md`, place the report under `docs/incidents/YYYY-MM-DD-<slug>-report.html`, and pass `python tools/validate_incident_report_design.py <report>` before completion.
