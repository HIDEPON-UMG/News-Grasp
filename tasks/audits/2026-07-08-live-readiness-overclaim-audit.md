# News-Grasp live readiness 完了過大報告監査

作成日: 2026-07-08
対象: 2026-06-20 以降の News-Grasp daily recovery / publish-complete / incident / runner contract

## 監査結論

過去の「完了」主張のうち、`publish_complete`、`published_ok`、`distribution manifest` までは確認していても、次回 06:00 の実行経路である live runner / Scheduled Task / 実起動 canary を同一 completion gate に含めていないものは、運用完了としては過大報告である。

この欠落は 2026-07-08 の live runner drift で顕在化した。runner 自体には drift 検出があったが、検出点が 06:00 起動後であり、前日・復旧後・完了報告時に止める gate へ接続されていなかった。

## 過大報告の分類

| id | 罪状 | 証拠 | 判定 |
|---|---|---|---|
| OC-1 | `verify-publish-complete` が public / podcast / distribution を見ても live runner readiness を見ていなかった | `tools.daily_self_heal.verify_publish_complete` の旧 manifest に `live_runner_readiness` が無い | 完了条件の欠落 |
| OC-2 | `batch_completion_history` が `publish-complete manifest + published_ok + distribution manifest` だけで `complete` にしていた | 2026-06-27 から 2026-07-08 の履歴が修正前は `complete`、修正後は `completion_overclaim` | 過去完了の過大分類 |
| OC-3 | `-SmokeTest` は `SMOKE OK` をログへ出していたが、runner state に `smoke_ok` を残していなかった | watcher は `smoke_ok` を終端状態として扱う一方、runner smoke block は旧実装で `exit 0` していた | canary 証跡の非永続化 |
| OC-4 | 2026-07-08 incident を repair coverage drift だけで閉じると、live runner drift が「開始後検知」に留まった根本欠陥が残る | 06:00 runner binary drift は起動後 fail-fast で検出された | incident 分類不足 |
| OC-5 | 完了報告で current public Green と future scheduled readiness を分けず、future run guarantee を強く言い過ぎる余地が残っていた | `docs/spec.md` は runner/live SHA を必要条件にしていたが、完了 gate がそれを要求していなかった | 完了表現境界の不備 |

## 日次履歴の再分類

修正後の `tools.batch_completion_history --days 24` では、以下の日付が `completion_overclaim` に落ちる。理由はいずれも `publish_complete lacks live runner readiness: repo/live SHA + Scheduled Task target + smoke canary`。

| status | dates |
|---|---|
| completion_overclaim | 2026-06-27, 2026-06-28, 2026-06-29, 2026-06-30, 2026-07-01, 2026-07-02, 2026-07-03, 2026-07-04, 2026-07-05, 2026-07-06, 2026-07-07, 2026-07-08 |
| unverified | 2026-06-15 から 2026-06-26 のうち publish-complete manifest または distribution manifest が揃わない日 |

## 最小恒久対策

1. `tools.daily_self_heal verify-live-runner-readiness` を正本 gate とする。
2. `verify-publish-complete` の manifest に `live_runner_readiness` を必須で含める。
3. `tools.batch_completion_history` は live readiness 欠落の publish-complete を `completion_overclaim` にする。
4. runner `-SmokeTest` は `Exit-Runner -Status 'smoke_ok'` で state を残す。
5. `docs/spec.md` の Runner / state / recovery と publish_complete の契約に repo/live SHA、Scheduled Task target、実起動 canary を明記する。

## 重複・形骸化する配置

| 配置候補 | 判定 | 理由 |
|---|---|---|
| incident report テンプレだけに追加 | 不採用 | 報告時に気付くだけで、06:00 起動前に止まらない |
| 6:40 automation prompt だけに追加 | 不採用 | 監査時点では既に起動失敗済みになる |
| runner 起動時の drift guard だけ | 不十分 | 今回のように開始後 fail-fast になる |
| `verify_public_surface` だけ | 不十分 | public Green と次回 runner readiness は別 surface |
| `verify-publish-complete` + `batch_completion_history` + runner smoke state | 採用 | 完了判定、履歴判定、実起動 canary の三点が同じ証跡を見る |

## 残る境界

この対策は live runner drift / scheduler target drift / smoke canary 欠落を完了前に Red にする。YouTube quota、GitHub outage、OAuth 再同意、外部 API 障害、Windows Task Scheduler 自体の OS 障害は typed Yellow / typed Red として別分類する。
