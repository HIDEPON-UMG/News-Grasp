# News-Grasp live readiness 完了過大報告監査

作成日: 2026-07-08
対象: 2026-06-20 以降の News-Grasp daily recovery / publish-complete / incident / runner contract

## 監査結論

過去の「完了」主張のうち、`publish_complete`、`published_ok`、`distribution manifest` までは確認していても、次回 06:00 の実行経路である live runner / live watcher / live bootstrap / Scheduled Task / 実起動 canary を同一 completion gate に含めていないものは、運用完了としては過大報告である。

この欠落は 2026-07-08 の live runner drift で顕在化した。runner 自体には drift 検出があったが、検出点が 06:00 起動後であり、前日・復旧後・完了報告時に止める gate へ接続されていなかった。さらに初回修正は「異常終了にする gate」を追加しただけで、6:00 起動前に repo-managed ops へ自己修復する入口を持っていなかったため、自己修復運用としては未達だった。

## 過大報告の分類

| id | 罪状 | 証拠 | 判定 |
|---|---|---|---|
| OC-1 | `verify-publish-complete` が public / podcast / distribution を見ても live runner readiness を見ていなかった | `tools.daily_self_heal.verify_publish_complete` の旧 manifest に `live_runner_readiness` が無い | 完了条件の欠落 |
| OC-2 | `batch_completion_history` が `publish-complete manifest + published_ok + distribution manifest` だけで `complete` にしていた | 2026-06-27 から 2026-07-08 の履歴が修正前は `complete`、修正後は `completion_overclaim` | 過去完了の過大分類 |
| OC-3 | `-SmokeTest` は `SMOKE OK` をログへ出していたが、runner state に `smoke_ok` を残していなかった | watcher は `smoke_ok` を終端状態として扱う一方、runner smoke block は旧実装で `exit 0` していた | canary 証跡の非永続化 |
| OC-4 | 2026-07-08 incident を repair coverage drift だけで閉じると、live runner drift が「開始後検知」に留まった根本欠陥が残る | 06:00 runner binary drift は起動後 fail-fast で検出された | incident 分類不足 |
| OC-5 | 完了報告で current public Green と future scheduled readiness を分けず、future run guarantee を強く言い過ぎる余地が残っていた | `docs/spec.md` は runner/live SHA を必要条件にしていたが、完了 gate がそれを要求していなかった | 完了表現境界の不備 |
| OC-6 | 初回の live readiness gate は失敗を検知するだけで、起動前に repo-managed ops へ自己修復する stable bootstrap を持っていなかった | `News-Grasp Runner` task は権限上 runner 直叩きから変更できず、`News-Grasp Bootstrap` 05:55 task と `news-grasp-bootstrap.ps1` を追加するまで self-repair path が無かった | 自己修復入口の欠落 |
| OC-7 | canary が本番 state/log を汚染し得た | 旧 bootstrap canary が `StateFile/LogDir/DateStamp` を watcher へ透過せず、`%USERPROFILE%\bin\news-grasp-runner-state.json` を `smoke_ok` にして履歴分類を壊した | 検証隔離の欠落 |
| OC-8 | Scheduled Task の Action 文字列だけを見て、task の有効状態、時刻、LastTaskResult を見ていなかった | 攻撃的別セッションレビューで、disabled task / 06:05 task / LastTaskResult 非0でも通り得ると指摘された | scheduler 証跡の欠落 |
| OC-9 | bootstrap の repo-to-live overwrite が backup/manifest 無しだった | `news-grasp-bootstrap.ps1` が watcher より先に `Copy-Item -Force` できるため、実際の初回 mutation に証跡が残らなかった | live overwrite 証跡の欠落 |
| OC-10 | 修正後も verifier が証跡 JSON に出した Runner trigger / NextRunTime / NumberOfMissedRuns / Bootstrap Action shape を pass/fail 判定へ使い切っていなかった | 二度目の攻撃的別セッションレビューで、06:00 Runner 証明、05:55 Bootstrap NextRunTime、missed run、`-SmokeTest`/short timeout/isolated state/log が欠けても pass し得ると指摘された | 証跡と判定の分離不備 |
| OC-11 | 06:00 Runner task が watcher/bootstrap を指していても、その Action が本番起動 mode かを見ていなかった。さらに direct runner 残存時に 05:55 bootstrap と 06:00 本番開始の runtime coupling が無かった | 三度目の攻撃的別セッションレビューで、`-SmokeTest` / `-Status` / `-StartOnly` action でも pass し得ること、direct runner は Bootstrap 失敗時に止まらないことを指摘された | 起動 mode と runtime coupling の欠落 |
| OC-12 | direct runner pre-run interlock を文字列存在だけで通す余地があり、当日 `smoke_ok` marker の時刻鮮度も保証していなかった | 四度目の攻撃的別セッションレビューで、dead helper function だけでも verifier が通り得ること、前回 run の古い marker でも同日なら本番生成へ進み得ることを指摘された | interlock 構造検証と marker freshness の欠落 |
| OC-13 | direct runner が bootstrap self-repair 後に同期済み runner へ再起動せず、fresh marker 後の repo/live drift も approval block で異常終了へ戻っていた | 五度目の攻撃的別セッションレビューで、disk 上の runner だけを直しても現在実行中の stale PowerShell code が本番生成へ進むこと、05:55 後 06:00 前の repo 更新が exit 72 に戻ることを指摘された | self-repair 後 re-exec の欠落 |

## 日次履歴の再分類

修正後の `tools.batch_completion_history --days 14 --json` では、2026-07-08 は new gate を満たすため `complete`、過去分は `completion_overclaim` に落ちる。理由はいずれも `publish_complete lacks live ops readiness: repo/live runner SHA + repo/live watcher SHA + repo/live bootstrap SHA + Runner 06:00 production action/NextRunTime/missed-run + direct runner pre-run bootstrap interlock/reexec + Bootstrap 05:55 smoke contract/fresh canary`。二度目の修正後は、薄い readiness manifest、Runner 06:00 / Bootstrap 05:55 / NextRunTime / NumberOfMissedRuns / Bootstrap Action shape 欠落も `completion_overclaim` に落ちる。三度目の修正後は、06:00 Runner Action が本番起動 mode でない manifest と、direct runner pre-run interlock 欠落も `completion_overclaim` に落ちる。四度目の修正後は、helper function の文字列だけを含む薄い runner source と、古い当日 `smoke_ok` marker で本番生成へ進む実装も Red になる。五度目の修正後は、fresh marker 後の repo/live drift が bootstrap self-repair + 同期済み runner re-exec へ進まない実装も Red になる。

| status | dates |
|---|---|
| complete | 2026-07-08 |
| completion_overclaim | 2026-06-27, 2026-06-28, 2026-06-29, 2026-06-30, 2026-07-01, 2026-07-02, 2026-07-03, 2026-07-04, 2026-07-05, 2026-07-06, 2026-07-07 |
| unverified | 2026-06-25, 2026-06-26 |

## 最小恒久対策

1. `news-grasp-bootstrap.ps1` を stable bootstrap とし、5:55 の `News-Grasp Bootstrap` task で repo-managed ops を live bin へ自己修復してから canary smoke を実行する。`-SmokeTest` の既定 state/log は `build/bootstrap-task-smoke/` に隔離し、本番 runner state/log を汚染しない。
2. `watch-news-grasp-runner.ps1` は runner 起動前に `news-grasp-bootstrap.ps1`、runner、watcher、deadman、deadman launcher を backup 付きで repo から self-repair する。
3. `news-grasp-bootstrap.ps1` 自身も repo-to-live overwrite 前に `build/live-bootstrap-self-repair/<timestamp>/auto-repair-manifest.json` を残す。
4. `tools.daily_self_heal verify-live-runner-readiness` を正本 gate とし、repo/live runner SHA、repo/live watcher SHA、repo/live bootstrap SHA、Runner task の 06:00 trigger / NextRunTime / NumberOfMissedRuns=0、Runner Action 本番起動 mode、Scheduled Task watcher/bootstrap target、direct runner pre-run interlock、Bootstrap task の有効状態、05:55 trigger / NextRunTime / NumberOfMissedRuns=0、LastTaskResult=0、Action の `-SmokeTest` / short timeout / isolated state/log、bootstrap 経由 canary `smoke_ok` を見る。
5. `verify-publish-complete` の manifest に `live_runner_readiness` を必須で含める。
6. `tools.batch_completion_history` は `tools.daily_self_heal.live_runner_readiness_manifest_ok` と同じ strict 判定を使い、live ops readiness 欠落や薄い scheduler 証跡の publish-complete を `completion_overclaim` にする。
7. runner `-SmokeTest` は `Exit-Runner -Status 'smoke_ok'` で state を残す。
8. canary は専用 `build/live-runner-canary/<date>/state.json` と log を使い、実行前に stale log を削除して本番 state/log を汚染しない。
9. direct runner は、本番生成前に bootstrap smoke marker の `updated_at` と file mtime が当日 05:55 以降かつ現在から 15 分以内であることを確認し、fresh marker が無ければ live bootstrap smoke を起動し、失敗時は `blocked_startup_self_repair_failed` で停止する。
10. direct runner は、fresh marker 後でも repo/live runner drift を検出した場合、bootstrap self-repair を強制実行し、repo/live SHA 一致を確認したうえで同期済み runner を `Start-Process -Wait` で再起動し、親は子の exit code を返す。手動・検証系 drift は従来どおり `blocked_runner_sync_approval_required` に落とす。
11. `tools.daily_self_heal` は direct runner interlock を単なる文字列ではなく、marker freshness 変数、`updated_at` / `LastWriteTime` 検証、`Start-Process` の bootstrap smoke args、`Assert-RunnerBinaryInSync` より前の呼び出し順序、drift repair 後の re-exec contract まで構造検証する。
12. `docs/spec.md` の Runner / state / recovery と publish_complete の契約に repo/live runner、repo/live watcher、repo/live bootstrap、Runner 06:00、Bootstrap 05:55、NextRunTime、NumberOfMissedRuns=0、Scheduled Task watcher/bootstrap target、Bootstrap LastTaskResult=0、Action smoke contract、direct runner pre-run interlock/reexec、fresh 実起動 canary を明記する。

## 攻撃的レビューと追加証跡

別セッションレビュー `019f400a-3608-7252-857d-1448d3b84422` は初回回答を `FAIL` とした。blocker は、05:55 Bootstrap task の本番 state/log 汚染、Scheduled Task 状態/時刻/LastTaskResult 未検証、Scheduler 経由成功未証明、bootstrap overwrite の backup 無し、spec と実機例外の不一致、テスト不足である。

修正後の追加証跡:

| evidence | result |
|---|---|
| `schtasks.exe /Run /TN "News-Grasp Bootstrap"` | exit 0、Task Scheduler 経由で起動 |
| `Get-ScheduledTaskInfo -TaskName "News-Grasp Bootstrap"` | LastTaskResult=0、NextRunTime=2026-07-09 05:55、NumberOfMissedRuns=0 |
| `%USERPROFILE%\bin\news-grasp-runner-state.json` | `publish_complete` のまま、Bootstrap smoke で汚染されていない |
| `build/bootstrap-task-smoke/state.json` | isolated `smoke_ok` |
| `build/live-runner-readiness/2026-07-09-after-bootstrap-task-proof.json` | repo/live runner・watcher・bootstrap SHA一致、Bootstrap 05:55 < Runner 06:00、LastTaskResult=0、canary `smoke_ok` |

二度目の別セッションレビュー `019f4018-3669-75f1-8bd2-8b1e34e2e63c` も `FAIL` とした。blocker は、Runner task の 06:00 trigger / NextRunTime 未判定、Bootstrap task の NumberOfMissedRuns / NextRunTime 未判定、Bootstrap Action の `-SmokeTest` / short timeout / isolated state/log 未判定、履歴 classifier の薄い readiness 受け入れ、direct runner 残存時の runtime coupling 不足である。

二度目レビュー後の追加 Red/Green:

| evidence | result |
|---|---|
| `test_verify_live_runner_readiness_rejects_runner_without_0600_next_run` | Runner trigger / NextRunTime 欠落を Red にする |
| `test_verify_live_runner_readiness_rejects_bootstrap_missed_or_unscheduled` | Bootstrap missed run / NextRunTime 欠落を Red にする |
| `test_verify_live_runner_readiness_rejects_bootstrap_without_isolated_smoke_action` | Bootstrap Action の smoke contract 欠落を Red にする |
| `test_batch_completion_history_rejects_thin_live_readiness_manifest` | 履歴側の薄い readiness manifest を `completion_overclaim` にする |
| `test_batch_completion_history_rejects_missed_bootstrap_readiness` | direct runner 例外でも Bootstrap missed run を `completion_overclaim` にする |

三度目の別セッションレビュー `019f402f-13a7-7560-808a-94e1741b4402` も `FAIL` とした。blocker は、06:00 task が watcher/bootstrap を指していても `-SmokeTest` / `-Status` / `-StartOnly` の非本番 action を pass し得ること、履歴 manifest も同じ穴を持つこと、direct runner 残存時に Bootstrap 失敗と 06:00 本番起動の runtime coupling が無いことである。

三度目レビュー後の追加 Red/Green:

| evidence | result |
|---|---|
| `test_verify_live_runner_readiness_rejects_nonproduction_runner_task_action` | 06:00 task の非本番 action を Red にする |
| `test_batch_completion_history_rejects_nonproduction_runner_action` | 履歴 manifest でも非本番 action を `completion_overclaim` にする |
| `test_direct_runner_has_pre_run_bootstrap_interlock_before_generation` | direct runner が本番生成前に bootstrap smoke interlock を持つことを契約化する |

四度目の別セッションレビュー `019f4045-cc45-7892-abbf-e0def7eee9c0` も `FAIL` とした。blocker は、direct runner interlock が dead helper function の文字列だけでも verifier を通過し得ること、runner が同日なら古い `smoke_ok` marker を fresh と誤認し得ること、`tools.daily_self_heal` が interlock の呼び出し順序と bootstrap args contract を十分に見ていないことである。

四度目レビュー後の追加 Red/Green:

| evidence | result |
|---|---|
| `test_verify_live_runner_readiness_rejects_thin_direct_interlock_marker` | helper function の文字列だけを含む薄い runner source を Red にする |
| `test_direct_runner_has_pre_run_bootstrap_interlock_before_generation` | marker freshness、`updated_at` / `LastWriteTime`、`Start-Process` の bootstrap smoke args、`Assert-RunnerBinaryInSync` より前の呼び出し順序を契約化する |

五度目の別セッションレビュー `019f4058-a7d6-7d93-836f-15cfae94c6d2` も `FAIL` とした。blocker は、bootstrap self-repair が live runner file を更新しても現在実行中の PowerShell process は stale code のまま本番生成へ進むこと、fresh marker がある direct runner で repo/live drift が起きると従来の `blocked_runner_sync_approval_required` exit 72 へ戻ることである。

五度目レビュー後の追加 Red/Green:

| evidence | result |
|---|---|
| `test_direct_runner_reexecutes_synced_runner_after_bootstrap_repairs_hash_drift` | fresh marker 後の repo/live drift を bootstrap self-repair + 同期済み runner re-exec へ収束させる |
| `tools.daily_self_heal._runner_has_pre_run_bootstrap_interlock` | direct runner source に `NEWS_GRASP_RUNNER_SYNC_REEXEC`、`Invoke-SyncedRunnerReexec`、`Start-Process -Wait`、`Assert-PreRunBootstrapInterlock -ForceRepair` を要求する |

## 重複・形骸化する配置

| 配置候補 | 判定 | 理由 |
|---|---|---|
| incident report テンプレだけに追加 | 不採用 | 報告時に気付くだけで、06:00 起動前に止まらない |
| 6:40 automation prompt だけに追加 | 不採用 | 監査時点では既に起動失敗済みになる |
| runner 起動時の drift guard だけ | 不十分 | 今回のように開始後 fail-fast になる |
| `verify_public_surface` だけ | 不十分 | public Green と次回 runner readiness は別 surface |
| `verify-publish-complete` + `batch_completion_history` + bootstrap/watcher self-repair + isolated canary | 採用 | 完了判定、履歴判定、起動前自己修復、実起動 canary の四点が同じ証跡を見る |

## 残る境界

この対策は live runner / watcher / bootstrap drift、scheduler target drift、Runner 06:00 trigger 欠落、Runner Action 非本番 mode、direct runner pre-run interlock 欠落、direct runner interlock 構造不足、self-repair 後 re-exec 欠落、fresh marker 欠落、Bootstrap 05:55 trigger 欠落、NextRunTime 欠落、NumberOfMissedRuns 残存、Bootstrap Action smoke contract 欠落、smoke canary 欠落を完了前に Red にし、既知の repo-to-live ops drift は 5:55 bootstrap、direct runner 本番生成前 interlock、watcher 起動前 repair で自己修復する。`News-Grasp Runner` 既存 task の Action は現権限で Access denied のため直接 bootstrap へ変更できないが、`News-Grasp Bootstrap` task が 05:55 に live bootstrap を実行し、06:00 runner 直叩き前に live ops を収束させる。さらに direct runner 本体は、本番生成へ進む前に `ng-smoke-state.json` の当日 `smoke_ok` marker が 05:55 以降かつ現在から 15 分以内であることを確認し、無ければ bootstrap smoke を起動し、fresh marker 後でも repo/live drift があれば bootstrap self-repair を強制し、同期済み runner を待機付きで再起動してから本番生成へ進む。自己修復不能時は `blocked_startup_self_repair_failed` で停止する。YouTube quota、GitHub outage、OAuth 再同意、外部 API 障害、Windows Task Scheduler 自体の OS 障害は typed Yellow / typed Red として別分類する。
