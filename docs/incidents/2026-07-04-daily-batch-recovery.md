# 2026-07-04 News-Grasp daily batch recovery

## 概要

2026-07-04 の News-Grasp 日次バッチでは、daily-quality 以降の復旧過程で次の 4 つの不具合クラスが同一 run 内に露出した。

- search audit metadata の `selected_total` が最終 digest card 数と同期せず、公開前品質ゲートで件数不整合を起こした。
- TTS script length repair が文字数だけを満たし、背景・影響・リスク・今日の観点の薄い台本を十分に補修できなかった。
- DeepDive relation renderer が高密度レイアウトで複数 band をまたぐ edge を単一ノード行へ通し、関係線がノードを横切った。
- Deploy Pages workflow が同一 HEAD で completed/failure になった後、publish verification が新規 dispatch による収束確認を行わずに失敗へ進む可能性があった。

## 復旧結果

- `tools/repair_registry.py` で digest card 数を正本に `selected_total` を同期する repair を追加した。
- `tools/repair_audio_script_length.py` で背景・影響・リスク・今日の観点の thematic shortfall を補修し、履歴締めの再利用を避けるようにした。
- `tools/render_deepdive.py` で複数 band をまたぐ relation edge の経路を調整し、単一ノード行の横断を回避した。
- `tools/daily_self_heal.py` と `scripts/ops/news-grasp-runner.ps1` で Deploy Pages completed/failure 時に fresh workflow dispatch を試行し、再度 publish verification へ戻すようにした。

## 提出レポート

詳細な障害レポート HTML は、公開対象にせず未追跡 artifact として `build/incidents/2026-07-04-daily-batch-recovery-report.html` に生成した。新規 `docs/incidents/*-report.html` は Git / GitHub Pages に追加しない契約を維持する。

## 恒久対策の検証境界

- `tests/test_repair_registry.py::test_search_audit_metadata_patch_syncs_selected_total_from_digest_cards`
- `tests/test_tts_build_script.py::test_repair_audio_script_length_patches_thematic_shortfall_without_repeating_history`
- `tests/test_deepdive_render.py::test_relations_ai_infra_20260704_competition_edge_no_pierce`
- `tests/test_daily_self_heal.py::test_dispatch_deploy_workflow_if_failed_posts_workflow_dispatch`
- `tests/test_runner_convergence_contract.py::test_runner_fresh_dispatches_failed_deploy_workflow_before_publish_fail`
