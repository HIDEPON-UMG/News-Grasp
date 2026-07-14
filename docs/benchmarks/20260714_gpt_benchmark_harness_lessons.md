# GPT比較 benchmark harness 作業の反省点と改善計画

## 目的

ユーザー提示の参照HTML `build/external-benchmark-matrix/20260714-source-truth-r5/external-benchmark-report.html` を、比較レポートの構造契約として扱い直す。今回の失敗を、評価ハーネス、レポート生成、完了報告、untracked成果物管理の複合欠陥として記録する。

## 参照構造契約

- H1: `GPT External Benchmark Matrix`
- section order: `用途別判断` -> `Score Method` -> `Local LLM Method Projection` -> `Task Type Mean Scores` -> `Usecase Winners` -> `Operational Gate` -> `Measurement Limit` -> `External Benchmark Grounding` -> `Case Library` -> `Harness Audit`
- required classes: `hero-grid`, `score-explorer`, `winner-grid`, `case-card`
- forbidden regression: `tabs` / `tablist` で線形資料を別UIへ置き換えない
- measurement boundary: `新規live実行ではなく既存run再集計`

## 反省点

| id | 反省点 | 既存ハーネスへの改善計画 |
|---|---|---|
| H-00 | 資料作成・調査・実装報告が、意思決定者の意思決定を補佐する情報提供になっていなかった。 | first viewportに「採用判断 / 判断理由 / 主要リスク / 次アクション」を必須化し、単一の総合点・見た目・存在確認だけで完了できないdecision-support contractを追加する。 |
| H-01 | 参照HTMLのDOM構造を最初に抽出しなかった。 | source report structural inventory を生成前Redテストにする。 |
| H-02 | r5の線形資料をタブUIへ置き換えた。 | section order contract で tablist 退化を禁止する。 |
| H-03 | 見出し一致だけを見て資料の主従関係を見なかった。 | Hero / Decision / Evidence / Detail の階層をテストする。 |
| H-04 | Score Explorerを大型比較図として扱わなかった。 | `score-explorer` class と大型比較領域を必須化する。 |
| H-05 | Usecase Winnersを単なる補助表にした。 | `winner-grid` と用途別勝者の存在を必須化する。 |
| H-06 | Decision Matrixの英語ラベルを欠落させた。 | `Decision Matrix` 文字列と用途×モデル表をsentinel化する。 |
| H-07 | Evaluation DesignをScore Methodだけに畳んだ。 | 採点設計セクションと測定限界を分離する。 |
| H-08 | Case Libraryを消した。 | 全caseを `case-card` として出すDOM契約を追加する。 |
| H-09 | Audits / Harness Auditを薄くした。 | 反省行30件以上を最低条件にする。 |
| H-10 | r5との差分を人間目視だけで済ませた。 | reference HTML diff summary をテスト入力にする。 |
| H-11 | `report_quality_gate` passを見た目の十分条件に誤用した。 | quality gateとは別に `source_style_gate` を設ける。 |
| H-12 | 新規live実行と既存run再集計の境界が弱かった。 | `run_origin` をfirst viewportとMeasurement Limitに出す。 |
| H-13 | 3回平均の主張をHTML上で目立たせきれなかった。 | minimum repetitions とbalanced coverageを複数箇所に表示する。 |
| H-14 | coding差分の見せ方を外部benchmark型に寄せきれなかった。 | HumanEval/MBPP/SWE-bench型のoracle説明をEvaluation Designへ置く。 |
| H-15 | 日本語能力と要約能力を1軸で丸めかけた。 | JA_NLUとJA_SUMMARYを別用途行として維持する。 |
| H-16 | 運用ゲートと品質点の分離を視覚的に弱くした。 | Operational Gateを独立カード/表にする。 |
| H-17 | tool-calling測定不能の注意を弱めた。 | Measurement Limitのダーク帯でproxy禁止を明示する。 |
| H-18 | ユーザー提示コマンド内のHTMLパスを正本として扱うのが遅れた。 | 提示パスを source_status=UserConfirmed 相当として扱う。 |
| H-19 | 過去ローカルLLM資料の「見せ方」を方法論だけに矮小化した。 | visual structure と method contract を別々に検査する。 |
| H-20 | untracked成果物が消える状態を放置した。 | runner/test `file_exists` を検証前に確認する。 |
| H-21 | 復旧コピーから戻した後の永続化確認が弱かった。 | `git status --untracked-files=all` をevidenceに入れる。 |
| H-22 | テストがr6の誤ったタブ構造を肯定していた。 | 誤った期待値をRedに差し替える。 |
| H-23 | HTML再生成後に構造抽出を再実行しなかった。 | 生成後にheading/class/order inventoryを再チェックする。 |
| H-24 | スクリーンショットでfirst viewportだけ見て構造不足を見逃した。 | visual check とDOM contractを両方必須にする。 |
| H-25 | 反省点を8件程度で済ませた。 | 失敗分類を工程別に30件以上で列挙する。 |
| H-26 | 「改善計画」と「実装済み恒久対策」を混同しやすい報告にした。 | 改善案は案、実装は未実施と明記する。 |
| H-27 | preflight通過を成果物品質の代替にした。 | preflightは最終矛盾検査であり成果物検査ではないと分離する。 |
| H-28 | 既存dirty worktreeの副作用分類が遅かった。 | 関係ファイル/無関係ファイルを最初に分ける。 |
| H-29 | 評価レポート用DESIGN.mdの高忠実度参照を後回しにした。 | `review-eval-scoring-design` のreport contractを先に読む。 |
| H-30 | case libraryの原データと表示件数の対応を固定しなかった。 | `case-card`数 >= `build_matrix_cases()`数をテストする。 |
| H-31 | モデル記号M1/M2/M3の一貫性を弱めた。 | legendと表の記号表示を必須化する。 |
| H-32 | 用途別判断と総合判断を混ぜた。 | 用途別判断を先、総合は補助として配置する。 |
| H-33 | 外部benchmark本体スコアではない境界が弱かった。 | External Benchmark Groundingで設計利用に限定する。 |
| H-34 | ユーザーの不満に対して成果物更新より説明が先行した。 | 不満指摘後はRed testか成果物再生成を同一ターンで行う。 |
| H-35 | 反省をチャットだけに閉じかけた。 | 反省と改善計画をHTML内とMarkdown成果物の両方に置く。 |
| H-36 | 完了報告で大きな未実装改善案を埋もれさせた。 | 将来改善は明示的な残タスク分類に残す。 |

## ハーネス改善の実装状態

root hook / memory dispatch / report_quality_gate は本件の所有境界ではないため未変更とする。benchmark raw artifact の lifecycle は product-local 境界へ実装した。

1. `source_style_gate`: 参照HTMLのsection order、required classes、forbidden UI regressionを検査する。
2. `run_origin`: 新規live実行、既存run再集計、fixture replayをHTMLとsummary JSONへ必須出力する。
3. `untracked artifact guard`: **実装済み**。raw output は `_ops/benchmark-runs/**` に限定し、`tools/artifact_lifecycle.py` が Git の未追跡判定、排他lock、SHA-256 archive、journal resume/rollback、retention選択を担う。公開用のcanonical reportだけを従来のtracked `build/**` に残す。
4. `report_quality_gate`拡張: generic品質ではなく、source-style contractの合否を扱う。
5. `benchmark report canary`: 生成後にheading/class/order inventoryを再抽出し、参照資料との差分を保存する。
6. `decision-support contract`: 調査・比較・実装報告で、意思決定者向けの採用判断、判断理由、主要リスク、次アクションが欠けていれば資料品質Redにする。

## 検証方針

- pytest: `tests/test_external_benchmark_matrix.py`
- report gate: `%USERPROFILE%\.codex\tools\report_quality_gate.py`
- content sentinel: H-00..H-36、`source_style_gate`、`run_origin`、`untracked`、`report_quality_gate`、`decision-support contract`
