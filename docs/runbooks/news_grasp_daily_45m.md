# News-Grasp Daily 45分運用 Runbook

## 適用範囲

`task_id=NG-DAILY-45M-20260902` の日次公開、Release検証、cutover、rollbackに適用する。2026-09-02の既存公開物はread-onlyであり、upload、send、再生成、再公開を行わない。

## Daily entry

ScheduledProductionが直接起動できるPython commandは次の六つだけである。順序を入れ替えず、各operation receiptを一回だけ消費する。

```powershell
C:\Users\hidek\AppData\Local\Programs\Python\Python312\python.exe -m tools.news_grasp_daily_gate static_check
C:\Users\hidek\AppData\Local\Programs\Python\Python312\python.exe -m tools.news_grasp_daily_gate scoped_contract_unit
C:\Users\hidek\AppData\Local\Programs\Python\Python312\python.exe -m tools.news_grasp_daily_gate current_issue_integration
C:\Users\hidek\AppData\Local\Programs\Python\Python312\python.exe -m tools.news_grasp_daily_gate external_publication
C:\Users\hidek\AppData\Local\Programs\Python\Python312\python.exe -m tools.news_grasp_daily_gate consumer_public_verification
C:\Users\hidek\AppData\Local\Programs\Python\Python312\python.exe -m tools.news_grasp_daily_gate atomic_completion
```

stdoutはUTF-8・BOMなし・一行JSONだけを受理する。ログはstderrまたはUTF-8 JSONLへ分離する。raw Python、raw/full pytest、Release gate、historical corpus、Playwright全件、crash/replay/drift、NoPublish、unknown operationはDailyから起動しない。

## Release gate

Release authorityがあるときだけ `tools.news_grasp_release_gate` を使う。collection nodeは `scoped_changed`、`known_constitution_regressions`、`historical`、`playwright`、`crash_replay_drift`、`general_complement` の排他的partitionに分ける。集合の欠落・重複・unknown、同一nodeの二重実行が一件でもあればRelease receiptはRedである。既存成功nodeは失敗node修正後に再実行しない。

## 状態確認

- canonical state root: `%LOCALAPPDATA%\News-Grasp\direct-mainline`
- single-flight identity: `automation_id + issue_date + run_intent`
- active writer: 常に一件以下
- completion authority: fresh consumer-owned public verifier
- provider ACKなし: `unknown_unobtainable`。成功・失敗・再送要求へ変換しない

source、installed、loaded runtime、snapshot、remote、public surfaceを別の観測行として残す。完了時はscheduler triggerからのelapsedを固定し、queue、external wait、retry、handoff、user wait、unmeasuredを推測で相互変換しない。

## SLO branch

- 45分超: `method_change`。optional diagnosticsを凍結し、検証済みartifact/receiptを再利用してpublic critical pathだけを続ける。
- 75分到達: `scope_reduce`。Release-only検証と任意polishをDailyから除外する。公開必須inventoryは削らない。
- 90分超: `deadline_revision`。新run、model fan-out、同一原因retryを禁止し、同一runのforward recoveryを続ける。

## rollback

1. 外部公開開始前はpromotion receiptに記録されたbackupとpreimage SHAを照合する。
2. automation/runtimeのcurrent bytesがpromotion receiptのpostimage SHAと一致することを確認する。違えば自動rollbackせずdrift Redで止める。
3. sync toolの明示rollback operationでinstalled TOML、App DB、snapshot、stable runtime pointerを同じpromotion ID単位で戻す。
4. DB migration後かつ新run開始前はSQLite backupのhashとmigration receiptを照合して戻せる。
5. 新run開始後のbinary rollbackはadditive schemaを旧runtimeが読めるrehearsalがGreenの場合だけ許可する。
6. 外部公開開始後は同runのsource/manifestを巻き戻さない。`superseded_after_external_start`でterminalizeし、別run intent・新generationのforward recoveryを作る。
7. Git remoteはreset/force pushを使わずrevert commitを作る。公開bundleを変えるrevertも新releaseとして扱う。

rollback rehearsalはfixture/simulationで行い、2026-09-02の公開副作用を起こさない。

## maintenance

- 毎日: route registry、Python 3.12実体hash、automation prompt parity、runtime generation、state/notification V2、active writer一件以下を`static_check`で確認する。
- 毎週: Release partitionの全node和集合、historical、Playwright、crash/replay/drift、failure injectionをRelease authorityで一回ずつ実行する。
- adapter、Python、Pages action、automation prompt変更時: installed promotion receiptを失効させ、明示promotionとrollback rehearsalを再実行する。
- failure ledger: append-only。owner、Red fixture、Green test、operational recovery、independent evidence、maintenance conditionが欠けるentryをclosedにしない。
- public verifier: category universe、Summary/DeepDive Markdown・HTML、両音声、YouTube、playlist、notification、distribution、publish-status、Home、Pages、remote/release/Pages SHA、run/bundle identityを同じfresh observationで確認する。
