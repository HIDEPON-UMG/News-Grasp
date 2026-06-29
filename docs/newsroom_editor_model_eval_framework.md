# Newsroom Editor Model Evaluation Framework

この文書は、News-Grasp のモデル選定で「文体 editor」と「編集長 editor-in-chief」を混同しないための評価フレームである。

## Current Finding

`build/model-eval-selection/combo_summary.json` の結果では、`full__no-editor` と `full__mini-editor` がどちらも final quality 5.0 だった。したがって、現時点の有力仮説は次の通り。

- 記者 `gpt-5.4` は、prompt 制御だけで News-Grasp 文体をかなり満たせている。
- 文体 editor は常設ロールではなく、自然さ・style score・validator fail が弱い記事だけに使う補助タスクでよい。
- この結果は編集長本体のモデル選定根拠にはならない。

## Style Editor Scope

文体 editor の対象は、既存記事 draft の最終調整だけである。

- 事実保持
- 自然な日本語
- News-Grasp 文体
- 圧縮
- 強調 readiness

このタスクは `prompts/model-eval-editor-rewrite.md` と `schemas/model_eval_output.schema.json` で評価する。

## Newsroom Editor-In-Chief Scope

編集長本体の評価対象は、文体 rewrite ではなく newsroom workflow の統制である。

| Task | Required capability |
| --- | --- |
| orchestration | 対象カテゴリ、spawn 計画、並列度、retry budget を安全に決める |
| gate_repair | `verify_reporter_output` の失敗を bounded repair / quarantine に分ける |
| cross_category_dedup | カテゴリ横断重複を append 前に解消する |
| summary_planning | compact artifact から Summary 構成と `categoryId` を設計する |
| append_safety | failed / duplicate / quarantined record を append しない |
| context_budget | articles.jsonl / digest md / article body の全文 Read を避ける |
| deepdive_direction | 当日の横断テーマから DeepDive 方向性を 1 本選ぶ |

このタスクは `prompts/model-eval-newsroom-editor.md` と `schemas/newsroom_editor_eval_output.schema.json` で評価する。

## Candidate Models

| Variant | Model | Role |
| --- | --- | --- |
| newsroom-editor-mini | gpt-5.4-mini | editor-in-chief candidate |
| newsroom-editor-54 | gpt-5.4 | editor-in-chief candidate |
| newsroom-editor-55 | gpt-5.5 | editor-in-chief candidate |

## Selection Rule

採用は `build/model-eval-newsroom-editor/newsroom_editor_summary.json` のみを根拠にする。

1. 全 candidate の結果が揃っていなければ `undecided`。
2. `quality_score >= 4.5` を満たす候補だけを採用候補にする。
3. quality floor を満たす候補の中で `cost_adjusted_score` が最大のものを既定候補にする。
4. `quality_score` 最大の候補は `quality_leader_variant` として別に残し、複雑日・gate 多発日の escalation 判断に使う。
5. style rewrite 評価を編集長モデル選定に流用しない。

## Current Status

`tools/model_policy.py` は、過去に Codex CLI サブスク認証経由で生成した `build/model-eval-newsroom-editor/newsroom_editor_summary.json` の評価結果をもとに、以下の採用状態を記録している。モデル選定を変更する場合は、この build 証跡を再生成し、policy と docs を同じ変更単位で更新する。

| Variant | Quality | Cost weight | Cost-adjusted | Result |
| --- | ---: | ---: | ---: | --- |
| newsroom-editor-mini | 4.571 | 1.6 | 2.857 | default |
| newsroom-editor-54 | 4.878 | 3.3 | 1.478 | quality leader / escalation |
| newsroom-editor-55 | 4.510 | 5.0 | 0.902 | not selected |

既定は `newsroom-editor-mini`。複雑な gate repair、横断 dedup、Summary planning で品質余地が必要な日は `newsroom-editor-54` に昇格する。`newsroom-editor-55` は今回 fixture では品質・コストの両面で採用根拠がない。

## Runner Wiring Contract

runner は `newsroom_editor.default` を直取りしてはならない。`Select-NewsroomEditorModel` から `tools.model_policy.select_newsroom_editor_model()` を呼び、機械シグナルに応じて default / escalation を選ぶ。

LLM repair worker は文体 `editor` role を流用してはならない。repair は `repair` role と `tools.model_policy.select_repair_model()` を使い、missing artifact generation や複合 issue の修復判断を `gpt-5.4-mini` に委ねない。

## Operational Escalation

通常日は `gpt-5.4-mini` で開始する。次の機械シグナルが 1 つでも出た場合だけ `gpt-5.4` に昇格する。

| Signal | Escalation condition |
| --- | --- |
| reporter gate failure | `gate_fail_count >= 1` |
| cross-category dedup conflict | `dedup_conflict_count >= 1` |
| append/card mismatch | `append_mismatch == true` |
| weak Summary plan | `summary_quality_score < 4` |
| DeepDive theme ambiguity | `deepdive_theme_count > 1` |

低位モデルで対応できるのは、全 reporter が pass し、dedup conflict がなく、append 件数と md card 数が一致し、Summary の横断テーマが明確な日である。
