# News-Grasp Daily 45分運用 Runbook

## 適用範囲

`task_id=NG-DAILY-45M-20260902` の日次公開、Release検証、cutover、rollbackに適用する。2026-09-02の既存公開物はread-onlyであり、upload、send、再生成、再公開を行わない。

## Daily entry

ScheduledProductionが直接起動できるPython commandは次の一つだけである。launcherは同一process memoryのwriter leaseを保持し、六operationを順序どおり一回ずつ実行する。

```powershell
C:\Users\hidek\AppData\Local\Programs\Python\Python312\python.exe -m tools.news_grasp_daily_launcher
```

内部順序は `static_check` → `scoped_contract_unit` → `current_issue_integration` → `external_publication` → `consumer_public_verification` → `atomic_completion` である。stdoutはUTF-8・BOMなし・一行JSONだけを受理し、writer lease/fencing capabilityを含めない。ログはstderrまたはUTF-8 JSONLへ分離する。個別operation CLI、raw Python、raw/full pytest、Release gate、historical corpus、Playwright全件、crash/replay/drift、NoPublish、unknown operationはDailyから起動しない。

`config/news_grasp_daily_45m_contract_v1.json`の`protectedRelease`と同じissue dateは、state作成より前に`protected_release_reexecution_forbidden`で停止する。通常Dailyに解除flagはなく、必要な変更は別run intentの明示的新releaseとして扱う。productionのruntime stateとRelease ledgerはWindows Known Folderの`LocalAppData`から解決し、`LOCALAPPDATA`や`NEWS_GRASP_STATE_ROOT`による差替えをauthorityにしない。

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
- YouTube retry: local receipt未確定でもprovider-native markerに同一idempotency keyとpayload hashが存在する場合は、既存video IDをfresh照合してreceiptだけをforward確定する。marker不一致または複数候補は再uploadせずtyped Redにする。
- commit/seal recovery: `git update-ref`後に停止した場合はsame-run metadata、exact write set、parent SHA、manifest/bundle identityが一致するHEADだけを回収する。一条件でも不明なら同runへrebindしない。
- completion attestation: manifestの8 immutable asset locator、fresh bytes hash/size、JSON identity、distribution component hashを全て再取得する。保存済みreceipt、HTTP 200、caller JSONだけでは完了させない。
- completed identity: 同じ`automation_id + issue_date + run_intent`にcompleted rowがあれば、cwd変更やcaller環境変数に関係なく新runを作らない。通常復旧ではなく、次の自然issue dateまたは別authorityの明示的新releaseだけを後継にする。
- scoped test closure: Release gate、scoped broker自身、automation promotionの変更はDaily changed-source testで扱わず新Release promotionへ戻す。許可test nodeもRelease/historical/Playwright importとnested processをpytest起動前に拒否する。
- migration crash: schema完成済みなら既存migration receiptへstarted journalをCAS finalizeする。未完成ならstate root内のintegrity済みpre-migration backupへSQLite backup APIで戻して新attemptを作る。backup欠落・破損・identity不一致はstate不変Redとする。
- finalizer crash: admission receiptの同じnonce、六operation digest、consumer receipt hash、manifest、writer/fenceが一致する場合だけ、consumer再観測・外部再送・operation再実行なしでcompletion transactionを再開する。
- external substep crash: outbox startedとtiming openは一transactionで確定する。YouTube finalizeはfresh privacy/playlist観測と`providerSubsteps`を照合して不足stepだけを再開し、notificationはrecipient ledgerで既送信をskipする。recipient予約後の送信可否が不明なら再送せず`unknown_delivery`を維持する。
