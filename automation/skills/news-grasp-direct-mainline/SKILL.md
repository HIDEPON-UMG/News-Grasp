---
name: news-grasp-direct-mainline
description: Run the 06:00 News-Grasp scheduled production directly with Codex, without the legacy runner. Use for same-day article generation, quality repair, Web/audio/YouTube/playlist/notification publication, public-only completion verification, and same-day direct recovery.
---

# News-Grasp Direct Mainline

当日版の読者可視公開を最上位目的にする。旧 runner、runner/readiness、NoPublish、fallback、監査成果物、durable goal、URL 200 単独を完了 authority にしない。

## 開始契約

1. 固定runtime `C:\Users\hidek\AppData\Local\Programs\Python\Python312\python.exe` だけを使う。WindowsApps alias、裸の `python`、別interpreterへfallbackしない。
2. `static_check` がscheduler triggerをT0として、Asia/Tokyoの `issue_date`、`run_intent`、DB発行のactual run ID、writer lease/fencing token、source baseline、runtime generation、remote base SHA、許可外部副作用をstart sealへ固定する。`final`等のrun aliasを作らない。
3. single-flight identityは `automation_id + issue_date + run_intent` である。既存active writerへattachしたcallerはobserverであり、writer leaseを再利用してmutationしない。inflight/unknown deliveryがあれば新runを作らず照合へ進む。
4. V1 runtime stateとnotification ledgerは、run作成より前にV2へ正規migrationする。migration receiptが無い状態でstageを開始しない。
5. ScheduledProductionが実行可能なentryは下記の単一launcherだけである。launcherが同一process memory内のwriter leaseで六operationを順に一回ずつ実行する。UTF-8一行JSON receiptの`ok=true`、前receipt hash、同一seal identity、exact successorを確認する。個別operation CLI、raw Python、旧runtimeの`start/advance`、Release gate、NoPublish、historical、Playwright、full pytest、未登録commandは使わない。
6. `tools.publish_inventory.scheduled_category_ids(issue_date)` を当日の対象カテゴリ正本にする。7カテゴリはpublic verifierのuniverse coverageであり、毎日の固定生成対象へ読み替えない。

## Daily 六phase

次の単一commandだけを実行する。内部順序を変えず、同じoperationを二回実行しない。各phase内部ではbrokerが固定したproducer/consumerだけを使い、同じacceptance predicateを別phaseで再評価しない。

```text
C:\Users\hidek\AppData\Local\Programs\Python\Python312\python.exe -m tools.news_grasp_daily_launcher
```

launcher内部の正規順序は `static_check` → `scoped_contract_unit` → `current_issue_integration` → `external_publication` → `consumer_public_verification` → `atomic_completion` である。writer lease/fencing capabilityはprocess外へ投影しない。

task contractの`protectedRelease`は通常launcherから解除不能であり、同じissue dateはstate作成前に`protected_release_reexecution_forbidden`とする。新しい公開が必要なら別run intentと明示的な新release authorityへ戻る。

1. `static_check`
   - source、installed、loaded runtime、snapshot、remoteを別観測として検証する。
   - 固定Pythonのresolved pathとbinary hash、automation prompt四surface parity、route registry、V2 migration、single-flightを確認する。
2. `scoped_contract_unit`
   - source変更が無ければ同じsource SHAへ署名されたpromotion receiptを読む。
   - source変更がある場合は変更file→登録test node mappingのexact nodeだけを一回実行する。full collectionや任意selectorを使わない。
3. `current_issue_integration`
   - 当日source snapshotから、scheduledカテゴリ記事、Summary Markdown、DeepDive Markdown、HTML、Daily/DeepDive音声、distribution/publish manifestを各canonical producerで一回だけ生成・検証する。
   - Summaryはfrontmatter付きMarkdown、DeepDiveはcurrent issueだけを意味品質正本にする。HTML、音声、distributionは同じsource snapshotの派生物とする。
   - 外部公開直前にrelease commit、`docs/index.html`を含むexact write set、全file hash、manifest ID、bundle ID、external operation ID一覧をpublish sealへ固定する。
4. `external_publication`
   - publish seal済みtransactional outboxだけを順にclaimし、commit/push、Pages、Release audio、YouTube、playlist、notification、distributionをprovider idempotency key付きで一回だけ実行する。
   - 送信後にstdoutが失われた場合はimmutable receiptを照会し、再upload・再送しない。provider ACKだけが得られない通知は`unknown_unobtainable`を保持する。
5. `consumer_public_verification`
   - verifier自身が新しいnonce、時刻、content hashを発行してnetworkからHome、当日カテゴリ、Summary/DeepDive HTML、Daily/DeepDive音声、YouTube、playlist、publish-status、Pagesを観測する。
   - canonical Markdown、notification immutable ledger、distribution manifestを同一issue date/run intent/run ID/bundle/manifestへ照合し、remote SHA、release SHA、Pages deployment SHAを一致させる。
6. `atomic_completion`
   - 上記fresh observationと全required surfaceの論理積を唯一のfinalizerへ渡す。URL 200、commit、push、publish-status、保存済みJSON、callerの`ok=true`だけではrunをcompletedにしない。
   - completion時のelapsedをscheduler T0から固定し、以後のinspectで増加させない。

Reporter/editor/repair/newsroom_editor は repo-local model policy の Luna/max、DeepDive は Sol/high の独立routeを維持する。単一親モデルへ統合しない。

## Quality・公開gate

- `quality`より前にupload/publish完了を主張しない。
- DeepDiveのMarkdown存在だけでなく、provenance、dialogue、rendered publicをGreenにする。
- direct completion は caller が作った receipt JSON ではなく、canonical runtime state と consumer-owned public verifier で、Web、Daily audio、DeepDive article/audio、Daily/DeepDive YouTube、playlist、notification、distribution、publish-status、remote observation、Pagesを同一issue-dateで連言評価する。
- `NEWS_GRASP_DIRECT_PUBLIC_VERIFICATION_V1` は `tools.news_grasp_direct_runtime.verify_public_completion` が作る public-only projection だけを authority にする。caller作成の completion JSON は Green authority ではない。
- Git commit ID は観測値としてだけ報告してよい。distribution、remote observation、Pages反映の制御 authority には content-derived ID を使わない。
- title失敗は非阻害だが、`updated/already_ok`の不正title claimと失敗statusのissue未記録は拒否する。
- runner state、readiness、durable goal、URL 200単独、publish-status単独、NoPublish、fallback は public completion authorityではない。

## DeepDive Publication Quality V2

DeepDiveの共有品質契約は `DEEPDIVE_QUALITY_REVIEW_V2` とし、次のissue codeだけを受理する。

- `deepdive_url_provenance_invalid`
- `deepdive_article_value_invalid`
- `deepdive_relation_quality_invalid`
- `deepdive_dialogue_value_invalid`
- `deepdive_research_evidence_insufficient`
- `deepdive_public_surface_invalid`

共有routeは `production_generation`、`repair_publish`、`daily_quality`、`codex_daily_audit` の4つだけである。未登録のissue codeまたはrouteはfail-closedにし、自由文分類や旧handlerへフォールバックしない。意味品質レビューは記事・関係図・対談のrepo-relative pathと実bytes identityへbindし、evidence-backed findings、7軸の1〜5評価、`averageScore`、`reviewRoute`、`status`を再検証する。hashは鮮度・byte一致の検出だけに使い、semantic authorityにしない。

TTSまたは公開HTMLを生成する前に、同じV2 gateでmetadata preauditを行う。共有internal-metadata stripperでraw/escaped claim-source・value・evidence・support comment、transport JSON、Markdown制御断片を除去し、除去後の表示文と`source_evidence_sentences`を検証する。残存または検証不能なら `deepdive_public_surface_invalid` として停止する。V2 source auditがGreenになるまで公開HTMLの再構築・safe rerender・TTSを開始しない。

対談は記事固有の調査結果を入力にLLMが生成し、7価値区間の順序を維持しながらturn数を可変にする。先輩は常体、若手は敬体とし、fillerや根拠の言換えだけの反復を拒否する。最低文字数・最低再生時間・固定turn数を品質条件にせず、暴走防止の最大値だけを適用する。

## 速度・回復

- 45分を目標にし、45分時点で残工程を公開critical pathへ絞る。
- 75分以降は新規の任意high-cost stage、追加review、polishを開始しない。
- 90分超過はSLO debtとして記録し、実行可能なexact public successorを継続する。
- cost/ledger/binding failureは該当model operationだけをzero-call Redにする。fresh artifact、deterministic tool、公開可能なlocal successorがあれば同じrunで進む。
- OAuth、2FA、quota、外部障害は具体的証拠があるsurfaceだけをdeferする。他surfaceを継続し、全体Greenを偽らない。
- quality Redは該当artifactだけを修復し、原因入力が変わったcausal remediation receiptを伴う新generationでowner predicateを一回だけ評価する。成功済みpredicateの再実行や、旧 runner、NoPublish、fallbackへの切替えは行わない。

## 禁止

- `news_grasp_runner.py`、`news_grasp_nopublish.py`、`scripts/ops/news-grasp-runner.ps1`を起動しない。
- runner state、readiness、goal、audit/report、artifact existence、URL 200、fallback statusをcompletion authorityにしない。
- public incompleteかつexact successorがある状態で終了しない。
- raw process kill、focus theft、auto-open、user monitoringを行わない。
