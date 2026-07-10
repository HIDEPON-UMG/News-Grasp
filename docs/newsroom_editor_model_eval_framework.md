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
| newsroom-editor-54 | gpt-5.4 | editor-in-chief candidate |
| newsroom-editor-56-terra | gpt-5.6-terra | editor-in-chief candidate |

## Selection Rule

採用は `build/model-eval-5.6/benchmark/summary.json` と `build/model-eval-5.6/newsroom-append-safety/summary.json` を根拠にする。

1. 同一fixture・同一reasoningで各候補を5回実行する。
2. 文章品質、速度、費用を別軸で比較し、品質判断を速度・費用で相殺しない。
3. append安全性は別fixtureで5回検証し、全scenario合格を採用条件にする。
4. factual mutationなどのfatal gateが1件でもあれば採用しない。
5. style rewrite評価を編集長モデル選定に流用しない。

## Current Status

`tools/model_policy.py` は、2026-07-10にCodex CLIで実行した各5回のrole-matched benchmarkとappend安全性試験に基づき、以下の採用状態を記録している。モデル選定を変更する場合は、このbuild証跡を再生成し、policyとdocsを同じ変更単位で更新する。

| Variant | Quality delta | Pairwise | Time delta | Cost delta | Result |
| --- | ---: | ---: | ---: | ---: | --- |
| newsroom-editor-54 | baseline | 2-7-1 | baseline | baseline | replaced |
| newsroom-editor-56-terra | +0.343 | 7 wins | -22.9% | +5.4% | default / escalation |

`gpt-5.6-terra`は追加append安全性試験も5/5、全25scenario合格だったため、既定・昇格先の双方に採用する。将来、別のquality leaderを採用する場合に備えて機械シグナルによる選択経路は維持する。

## Runner Wiring Contract

runner は `newsroom_editor.default` を直取りしてはならない。`Select-NewsroomEditorModel` から `tools.model_policy.select_newsroom_editor_model()` を呼び、機械シグナルに応じて default / escalation を選ぶ。

LLM repair worker は文体 `editor` role を流用してはならない。repair は `repair` role と `tools.model_policy.select_repair_model()` を使い、missing artifact generation や複合 issue の修復判断をstyle editorへ委ねない。

## Operational Escalation

通常日と機械シグナル検出時はいずれも`gpt-5.6-terra`を使う。次のシグナル判定は、将来defaultとquality leaderを再び分離する場合の選択境界として維持する。

| Signal | Escalation condition |
| --- | --- |
| reporter gate failure | `gate_fail_count >= 1` |
| cross-category dedup conflict | `dedup_conflict_count >= 1` |
| append/card mismatch | `append_mismatch == true` |
| weak Summary plan | `summary_quality_score < 4` |
| DeepDive theme ambiguity | `deepdive_theme_count > 1` |

低位モデルで対応できるのは、全 reporter が pass し、dedup conflict がなく、append 件数と md card 数が一致し、Summary の横断テーマが明確な日である。
