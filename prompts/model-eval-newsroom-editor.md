# News-Grasp newsroom editor-in-chief evaluation

This is not a style rewrite evaluation. Do not rewrite article prose as the main task.

Evaluate whether the model can act as the News-Grasp editor-in-chief across the full newsroom workflow. The input fixture contains compact reporter artifacts, validation failures, cross-category duplication hints, a Summary requirement, append constraints, context-budget constraints, and a DeepDive theme decision.

Return JSON conforming to `schemas/newsroom_editor_eval_output.schema.json`.

Required duties to solve:

1. Build a category reporter spawn plan with safe concurrency.
2. Interpret `verify_reporter_output` results and make a bounded repair decision.
3. Resolve cross-category dedup conflicts without re-collecting articles.
4. Plan the Summary output from compact artifacts, including category coverage and `categoryId`.
5. Preserve append safety: only append records that pass validation and dedup.
6. Respect context budget: do not request full `articles.jsonl`, digest markdown, or article bodies.
7. Choose a DeepDive direction from the day's strongest cross-category theme.

Scoring dimensions:

- orchestration: spawn order, concurrency, and retry budget are coherent.
- gate_decision: validation failures lead to bounded repair or quarantine, not unlimited loops.
- dedup_resolution: cross-category duplicates are resolved before append.
- summary_planning: Summary plan follows the newsroom prompt and gamma-style requirements.
- append_safety: append scope is explicit and excludes failed/quarantined records.
- context_budget: solution avoids full-file reads and heavy payloads.
- deepdive_direction: DeepDive theme is required, specific, and cross-category; never mark the final public bundle optional.
