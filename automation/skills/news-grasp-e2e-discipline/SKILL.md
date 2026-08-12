---
name: news-grasp-e2e-discipline
description: Run or plan News-Grasp daily-batch E2E, scheduled-equivalent NoPublish E2E, goal-run validation, or push-before-publish verification. Use whenever an action might launch the full runner, create an E2E worktree, resume an E2E, judge E2E readiness, or claim E2E completion. Enforces final-confirmation-only execution, at most two durable logical attempts per issue date (A then conditional B), upstream-first debugging, and bounded resource use.
---

# News-Grasp E2E Discipline

## News-Grasp内の正本境界

このrepo内コピーをNews-Grasp専用のversioned sourceとする。installed copyは`config/news_grasp_automation_assets_v2.json`を読む正規installerだけが同期し、`~/.codex`、`~/.agents`、installed runtimeを直接編集しない。shared/global側と競合した場合はshared側を変更せず、確定hashを新baselineとしてこのoverlayを更新し直す。

## Product Constitutionと実composition

このskillはNews-Grasp Product Constitutionの「人手なしの日次運用」「壊さない運用」「物理提出」を実現する下位手段であり、E2E実行自体を目的にしない。task contractが`review_series_closed`または`no_additional_review`なら追加review seriesを開始しない。

唯一のL8経路は `official wrapper→installed launcher→runner→broker` とする。official wrapperが発行する`NEWS_GRASP_INSTALLED_NOPUBLISH_LAUNCH_AUTHORITY_V1`は`externalHealthAuthorityFixturePath`と`externalHealthAuthorityFixtureSha256`を必須fieldとしてsealし、installed launcherはlaunch直前にfile bytes、repo containment、reparse不在、64KiB上限、runner arguments中の`-ExternalHealthAuthorityPathOverride`とのcanonical path一致を再検証する。claim witnessはcanonical file pathとして渡し、inline JSON、別path、別hashへ置換しない。

## 1. 目的

E2Eは、完成済みの運用鎖が本番相当入口で成立することを確認する最終試験である。必須のattempt A（安定化）を一度実行し、Aが失敗した場合だけ、失敗原因へ作用する最小修正と同一attempt内の再開を一回まで許可した後、完全修正後のattempt B（最終確認）を一度だけ実行する。Aが無修正で成功した場合はBを実行しない。Bで修正起因でないrandom/design failureが発生した場合は設計feedbackを記録して終端し、3回目は実行しない。

遷移receiptは実行前の自己申告で作成しない。`tools/e2e_final_admission_bridge.py validate-issued` はissueイベントだけを記録し、installed launcherが実runner process handleのcreation identity、claim、state hash、実exitを束ねた`NEWS_GRASP_E2E_RUNNER_TERMINAL_AUTHORITY_V1`を発行する。実runner終了後の同bridgeの`record-outcome`はこのterminal authorityだけを検証してterminal receiptを発行し、callerのstate JSONやexit codeを成功証拠として受け取らない。success・resume・full correctionはterminal receiptのstate hashとowner identityへ束縛される。launcherはこのreceiptとledgerのread-only検証だけを行い、caller作成receiptや実行前Greenを受理しない。

E2Eを次の用途に使ってはならない。

- 未知の欠陥を探す。
- 原因を切り分ける。
- 修正のたびに全体を再実行する。
- readinessを確認する。
- 外部API、認証、quota、公開面の状態を試し打ちする。
- 個別stage、個別validator、個別artifactの動作を確認する。
- 「念のため」成功を再確認する。

これらは全てE2Eより前の安価な検証層で行う。

## 2. E2Eの定義

News-GraspでE2Eと呼べるのは、production Scheduled Taskと同じ `scripts/ops/news-grasp-runner.ps1` を、隔離state/log、`-NoPublish`、実際のstage順、実際のquality gate、実際のrepair routingで開始し、attempt Aまたは条件付きattempt Bを所定の終端stateまで通す試験である。論理attemptは最大2件で、attempt Cは契約違反として発行前に拒否する。

### 2.1 通常日次とのidentity分離

通常06:00 Scheduled TaskはE2Eではない。通常日次は `scheduled_production`、異常終了後のproduction repair pathは `scheduled_recovery` として、final E2Eの `News-Grasp:<issue-date>:scheduled-equivalent-nopublish` から別identityへ固定する。

- 通常日次はissue date単位の最大9 model callを持つ。
- 復旧は同じ日付identityの残予算を共有し、run ID、receipt path、session、復旧名義で新しい9 callを発行しない。
- `scheduled_production` と `scheduled_recovery` はfinal E2E attemptを消費しない。
- final E2Eの消費済みattemptやCodex goalのmodel call countは、通常日次を遮断してはならない。
- 通常日次のreceiptは全model callのbroker consumerへ渡し、issue date、task identity、receipt hash、scheduled attempt eventを実ledgerと照合する。
- `-NoPublish` のfinal E2Eは公式wrapperが発行したfinal admissionを必須とし、runner単独で自己予約しない。

次はE2Eではない。

| 層 | 名称 | 用途 |
|---|---|---|
| L0 | static | 構文、schema、route、禁止API、prompt契約 |
| L1 | contract | 関数・CLI・issue code・manifestの契約 |
| L2 | fixture | 正負fixture、失敗corpus、境界値 |
| L3 | unit | 一つの関数またはmodule |
| L4 | component | generator、validator、repair handler単体 |
| L5 | integration | 複数componentの接続、fake server、隔離artifact |
| L6 | fault injection | API失敗、stale、hash drift、replay、停止・復旧 |
| L7 | live reconcile | repo、installed runner、automation、公開証跡の鮮度 |
| L8 | final E2E | scheduled-equivalent NoPublishのattempt A、必要時だけattempt Bによる最終確認（最大2論理attempt） |

テスト名やディレクトリ名に `e2e` が含まれていても、L8の条件を満たさないものはE2E試行へ数えない。逆に、full runnerを起動するものは名前に関係なくE2Eとして数える。

## 3. 不変の試行identity

試行identityは次で固定する。

`News-Grasp:<issue-date>:scheduled-equivalent-nopublish`

論理attemptは `attemptKey` の末尾で区別する。Aは上記の基底key、Bは同じkeyへ`:attempt-b`を付加する。issue date、daily lineage、source generationはA/Bで共有し、attempt Cは存在しない。

次を変えても同じ試行である。

- worktree
- branch
- run ID
- receipt path
- state path
- log path
- model session
- Codex thread
- internal continuation
- `ResumeFromStage`
- promptまたはskillの再読込

日付ごとのdurable ledgerはA/Bのlogical attempt keyを別々に記録する。Aの無修正成功後のB、原因へ作用する最小修正を経ないB、またはattempt Cは別path・別receipt・別run IDでも発行を拒否する。

## 4. admissionの意味

ファイルが存在するだけではadmissionではない。

admissionは次を全て満たす機械判定済みreceiptである。

- `schemaVersion=NEWS_GRASP_E2E_FINAL_ADMISSION_V1`
- `state=issued`
- `purpose=final_confirmation_only`
- `singleUse=true`（各logical attempt単位）
- `resumePolicy=forbidden`（admission自体の再消費は禁止。失敗原因へ作用する最小修正後の同一attempt再開は、外側のattempt policyが一回だけ許可する）
- 外側のattempt policyは `failureLocalResumeMax=1`、`thirdAttemptForbidden=true`、`attemptRole` は `stabilization`（A）または `final_confirmation`（B）を保持する。
- issue dateとcanonical product IDからattempt keyを再計算できる。
- runnerの絶対pathとSHA-256がfreshである。
- runner argumentsが完全一致し、`-NoPublish`を含み、`-ResumeFromStage`を含まない。
- 次の上流証拠が全てJSONとしてparseでき、`status=Green`で、pathとSHA-256が一致する。
  - `efficiency_design`
  - `adversarial_review`
  - `route_manifest`
  - `red_suite_coverage`（`RED_SUITE_COVERAGE_REPORT_V1`、findings空、14 Requirement、10 viewpoints、3 domain scopes、49 unique fixtures、140 pair cases、5 routes、200 traceability cells、coverage hash一致）
  - `red_suite_execution`（公式admission producerが内部で一度だけ生成する`RED_SUITE_EXECUTION_RECEIPT_V1`。caller指定は禁止。49 selector、140 traceability-only pair Red cases、exact 190 collected/passed node、collection error 0、missing outcome 0、node集合/source hash一致）
  - `static`
  - `simulation`
  - `isolation`
- admission自身のcanonical hashが一致する。
- durable attempt ledgerに同じattempt keyが存在しない。

consumerはrunner起動前に証拠を再読込し、runner hashを再計算する。さらにwrapperが実際に起動するrunner引数配列を別JSONから読み、admissionの引数と順序・値・絶対pathまで完全一致することを確認してからattempt keyを原子的に消費する。検証と消費を行わずrunnerを直接起動してはならない。

`isolation` は `tools/e2e_isolation.py` がexact source commitから新規detached worktreeを作ったreceiptでなければならない。対象issue dateのdigest、docs、search audit、distribution、DeepDive provenance、session URL、articles JSONL recordだけを除去し、他日とsource repoを不変に保ち、runnerの既存artifact述語をfalseにする。許可root外、既存target、commit不一致、壊れたJSONL、対象日artifact残存はadmission発行前に拒否する。手作業でartifactを消したworktreeや過去のdirty worktreeを再利用しない。

## 4.1 TDDのRedと要件定義

Redは一個の失敗実装で代表させない。Requirementを正常、境界、異常、復旧、replay、identity drift、scope escape、他成果物不変、人間・資源影響へ分解し、それぞれに独立した反証fixtureと期待reason codeを持たせる。

Acceptance Matrixを実装前に固定し、次を全て満たすまでGreen実装へ進まない。

- 各Requirementに最低1つの専用fixtureがあり、別Requirementのfixtureで代用されない。
- 正のfixtureだけでなく、欠落、改変、順序違反、alias、stale、並行実行、replayの負のfixtureがある。
- 一つのfixtureを削除すると、対応Requirementが未検証としてmatrix上で明確にRedになる。
- collection errorや共通の未実装例外一件で全Redを代表させず、全fixtureを収集して各観点の失敗を個別に観測する。
- 網羅的なテスト観点を用意できない場合は、実装困難ではなく要件定義未完了の反証として上流へ戻る。

News-Graspではこの思想を `RED_SUITE_COVERAGE_V2` として機械化する。E2Eを目的、非目的、L0-L8層、readiness/admission、attempt identity、checkpoint境界、探索分離、資源予算、副作用境界、停止・失敗、証跡、product完了境界の12 Requirementへ分け、DeepDive URL provenanceとPodcast読者価値を加えた14 Requirementを正本とする。観点集合は `normal/failure/boundary/substitution/drift/replay/missing/cross_lineage/recovery/human_impact` のexact 10種とし、`final_e2e`、`deepdive_url_provenance`、`podcast_reader_value` の3 domain scopeごとに固有fixtureを持たせる。`fixtures/deepdive_quality/tdd_acceptance_matrix.json` の `Requirement fixture × same-domain viewpoint fixture × route fixture` を `python -m tools.deepdive_red_suite_coverage` で検証する。12 E2E Requirement × 10観点 × final wrapperの120セルと、2 content Requirement × 10観点 × 4共有経路の80セル、合計200 traceability cellsが全て存在し、49 Green fixtureと140個別pair Red caseのexecution receiptがGreenでなければL0をGreenにしない。140 pair Redは要件と観点のbindingを個別に壊す `traceability_only` 試験であり、本番挙動の反証を代用しない。本番挙動は49 fixtureが所有する。

独立fixtureは関数名や定数だけの違いでは成立しない。docstring-only、`pass`、`return None`、定数だけの`assert`、behavior observationを持たない関数をtrivialとして拒否する。文字列・数値定数を正規化したAST bodyと同一ファイル内の到達helper closureが重複するfixtureも単一実装の別名として拒否し、helper名だけを変えた薄いwrapperを許さない。source bytes hashと意味形状hashの両方をcoverageへ束縛する。

`final_e2e`、`deepdive_url_provenance`、`podcast_reader_value` の3 domain scopeは、それぞれ固有の10観点fixtureを持つ。14個のRequirement fixture、30個のdomain固有viewpoint fixture、5個のroute fixtureの計49 fixtureは、実行可能nodeと本文SHA-256集合をcoverage receiptへ束縛する。さらに14 Requirement × 10観点を140個のaddressable pair Red caseへ展開し、各caseが対象Requirementと同一domain観点を別々に破壊して、対象ID入りreason detailを個別観測する。公式admission producerは49 selectorと140 pair caseを単一pytest invocationで一度だけ実行し、exact 190 collected/passed node、collection error、各node outcome、node集合hash、matrix・fixture・pair・historical corpus・producer・pair test source hashをreceiptへ束縛する。callerが作成したexecution receiptは受理しない。admission consumerは発行時と消費時に全sourceを再読込し、本文drift、cross-domain substitution、path escape、非Python、構文不正、過大fixture、collection error、missing outcomeをfail-closedにする。200セルは実行件数でなく49 Green fixture・140 traceability-only Red pair・5 routeのtraceabilityであり、routeごとに同じtestを再実行しない。

execution receiptはfixtureだけでなく、tools、runner、config、tests、pytest設定、requirementsのpath→bytes hash集合をproduction dependency manifestとして束縛する。発行後にvalidator、runner、helper、conftest、plugin設定のいずれかが変わった場合、consume時にsource mismatchとして拒否する。

公式admission producerは190 node実行前にoutput identityへ束縛したWindows file lockを非待機で取得する。同じoutputへの並行発行は片方だけが実行を所有し、他方を `E2E_ADMISSION_ISSUE_BUSY` で実行前に拒否する。

各観点は固有Acceptance、実行可能fixture、production consumer、expected Red、counterevidenceを持つ。同一Requirement内での単一fixtureへの集約、mock-only、route欠落、expected Red欠落、未知Requirement追加、件数だけの代用をfail-closedにする。共有engineを使う4経路は、観点fixtureと各route consumer fixtureの複合証明でcoverageを作り、同じ品質テストをrouteごとに重複実行して資源を浪費しない。

複合gate Redも一件のRedへ潰さない。validatorが複数issueを返したら、orchestratorは全issueをhandler別の有限 `repair-plan` へ変換し、artifactだけを重複除去してissue観点を保持する。runnerは全deterministic handlerを同一再検証前に各一回だけ実行する。同一handlerの別step化、scope外artifact、unknown handlerは副作用前にfail-closedとする。

reporter artifactはカテゴリ全体の一括条件でなく、各recordのthumbを個別に検証する。一件でもnull、空、非HTTP、自己参照、Google News proxyならeditorへ渡さない。`followup_review_required`をURL隔離で代用せず、`followup-review-evidence-patch`がcurrent reporter artifact、公開日、date evidence、意味差分を一致確認できるfresh recordだけにreview証拠を付与する。

高コスト正本は日本語goalの意味を保持する。`最終production-equivalent NoPublish E2E`と、重複探索・無駄な外部model起動の禁止が同じNews-Grasp goalにある場合、final logical attempt Aを必須とし、Aがfailure-local修正を要した場合だけattempt Bを一回追加できる。論理attempt上限は2、各attemptのfailure-local resumeは1回、attempt Cは設計feedback terminalとする。正常経路のmodel callはreporter 7 + editor 1 + DeepDive 1の9回へ限定し、retry/repair分を先回りで追加しない。旧parserが上限0を登録済みでも、call/E2E countがともに0の同一goalだけを一度昇格し、消費済み・曖昧goal・再変更は拒否する。

## 5. readinessの判定順序

次の順序を変えない。

1. 当該タスクの全RequirementとAcceptanceを凍結する。
2. 既存証拠を再利用し、未検証面だけを列挙する。
3. L0からL7を安価な順に一回ずつ閉じる。
4. `python -m tools.deepdive_red_suite_coverage` で200 traceability cells、49 fixture、140 traceability-only pair casesの構造を検証する。公式admission producerが内部実行するreceiptで、exact 190 collected/passed node、collection error 0、missing outcome 0、全node outcome明示を確認する。手作りreceiptを入力しない。
5. source、fixture、runner、automation、manifestのhash鮮度を再確認する。
6. 高コスト予算と独立反証reviewをGreenにする。
7. 上流証拠manifestからfinal admissionを一度発行する。
8. official wrapperでattempt Aのadmissionを消費し、Aがfailure-local修正を要した場合だけattempt Bのadmissionを消費する。attempt Cは実行しない。

一つでもRed、Yellow、stale、missing、unknownならL8を開始しない。missing evidenceを「E2Eで確かめる」ことを禁止する。

## 6. 実行入口

official wrapperは次だけである。

`scripts/ops/invoke-scheduled-equivalent-nopublish.ps1`

wrapperは次の順で動かなければならない。

1. pathと隔離境界を検証する。
2. high-cost budget consumerを一度だけ通し、reservation receiptを保存する。
3. reservation receiptを同じattempt IDで検証し、runner引数へ渡す。
4. `tools/e2e_final_admission_bridge.py consume`でfinal admissionを消費する。
5. runnerは渡されたreservation receiptを再検証して再予約せず、同じrunnerを`-NoPublish`で起動する。
6. state、log、exit code、所要時間、no-publish/no-pushをreceiptへ記録する。

wrapper外からfull runnerをE2E目的で直接起動してはならない。

## 7. E2E前のデバッグ方法

欠陥の種類ごとに最も安い層を使う。

- promptやroute文字列: static/contract
- JSON schemaやmanifest: contract/fixture
- URL、TTS、DeepDive品質: component/fixture
- runner stage接続: integration
- retry、stale、replay、停止: fault injection
- installed runnerやautomation差: live reconcile
- 外部認証やquota: dedicated readiness probe

full runnerを無制御に部分stageから再開して確認することはE2Eではなく、復旧integrationである。ただしattempt policyが失敗原因と同一generationを検証し、許可したfailure-local resume一回だけは同じlogical attemptの継続として扱う。admission自体の再消費やattempt resetは許可しない。

## 8. 失敗時の処理

L0からL7の失敗では、最初に失敗した層へ戻り、その層と直接依存する検証だけを再実行する。無関係な層と既にfreshな証拠を再実行しない。

L8が失敗した場合はattempt policyを次のように適用する。

1. 失敗したlogical attemptを消費済みのまま保持し、state、log末尾、stage、exit code、artifact差分を凍結する。
2. 失敗原因へ作用する最小修正だけを設計境界で行い、同じlogical attemptの再開を一回だけ許可する。原因に作用しない名前・引数・pathだけの再試行は禁止する。
3. attempt Aが最小修正後に成功した場合は、全体最適の完全修正とL0-L7再検証を閉じた後にattempt Bを一回だけ実行する。Aが無修正で成功した場合はBを実行しない。
4. attempt Bで修正起因でないrandom/design failureが発生した場合は、欠けていた不変条件、fixture、owner層を `UPSTREAM_DESIGN_ESCAPE_V1` として記録し、設計feedback terminalへ遷移する。
5. attempt C、同一issue dateの第三L8、patch後の無制限resume、worktree・receipt・run IDによるattempt resetは実行前に拒否する。
6. E2E内で場当たり的にpatchせず、最上流の設計・fixture・consumerを修正してから次の論理attemptへ進む。

E2E失敗を外部境界のせいにする前に、専用readiness probeで事前検出できなかった理由を上流欠陥として扱う。

## 9. 資源契約

L8前に次を固定する。

- logical E2E上限: 2回（attempt A必須、attempt BはAがfailure-local修正を要した場合だけ）
- 各logical attemptのfailure-local resume上限: 1回
- attempt C: 0回（設計feedback terminal）
- 外部model call上限
- wall-clock上限
- subprocess上限
- Temp/worktree増分上限
- peak memory上限
- network call上限
- external mutation: 0 (`NoPublish`)

上限はworktree、session、receipt、内部継続でリセットしない。

常に次の順で資源を使う。

1. 既存証拠の再読込
2. static
3. contract
4. deterministic fixture/simulation
5. targeted component/integration
6. deduplicated readiness probe
7. attempt Aのfinal E2E。Aがfailure-local修正を要した場合だけ、完全修正後にattempt Bを一回実行する。

同じ失敗shapeを名前や引数だけ変えて再試行してはならない。

## 10. News-Grasp固有の完了境界

NoPublish E2E GreenだけでNews-Grasp全体を完了としない。別の安価なcompletion gateで次を確認する。

- runner terminal state
- publish complete
- distribution manifest
- public surface
- DeepDive記事とPodcast
- local/live runner freshness
- local/remote HEAD
- 必要な公開URLとasset

URL 200だけ、ローカル生成だけ、NoPublishだけ、fallback publishだけをGreenへ読み替えない。

## 11. 証跡

最終報告には最低限次を示す。

- 不変attempt key
- L0からL7のreceipt pathとhash
- admission path、admission ID、消費state
- L8起動回数が1であるdurable ledger
- runner pathとhash
- command identity
- start/end/elapsed/exit code
- state/log/receipt path
- no-publish/no-push/no-focus-theft
- L8後のproduct completion gate
- 残るRed/Yellow/unknown

証跡が欠ける場合は完了文言を出さず、欠けた上流層へ戻る。

### HTML E2E report

L8を実行した場合は、成功・失敗を問わず `report-news-grasp-incident` skillを使い、`BUG_REPORT_DESIGN.md` を継承した single HTML を作る。見た目は News-Grasp incident report tone とし、少なくとも次を一つの成果物へ含める。

- L0からL8までを分割表示した `Workflow Map`
- production、NoPublish、外部境界を区別した `Fault boundary`
- historical failure matrix と今回の各正負シナリオ
- 複合異常系ごとの `data-compound-pattern`
- attempt key、admission、durable ledger、runner hash、exit code、no-publish/no-push証跡
- 公開が成功条件に含まれる場合は、public URL returns HTTP 200 だけでなく report-specific sentinel text まで検証した結果

E2E execution is not report-complete until this HTML report exists and passes `tools/validate_incident_report_design.py`. さらにdesktop and one mobile render checkを行い、no horizontal overflowを実測する。レポート作成やrender checkを追加attemptの理由にしてはならない。

## 12. 絶対禁止

- E2Eによるfailure discovery
- E2E内patch
- 無関係なpatch後resume
- third E2E（attempt C）
- worktreeによるattempt reset
- admission fileの存在だけを信用
- stale receiptの再利用
- 自己申告Green
- direct runner launch
- `ResumeFromStage`をfinal E2Eへ混入
- 外部modelやfull runnerの試し打ち
- E2E失敗を理由に元の成果物修復を放棄
- E2E Greenをpublish/public Greenへ読み替える

## 13. repair・公開・incident境界

E2Eは `news-grasp-repair-method` の代わりにならない。異常時は、`tools.repair_coverage_matrix` が定義する Repair Decision Debt Covenant に従い、`structured issue -> explicit matrix route/status -> ordered issue ledger` を作る。`selected artifact registry repair -> typed runner state` の順序を守り、`tools.verify_public_surface`、`tools.recovery_state`、`tools.youtube_podcast.auth_doctor`、`tools.deploy_recovery_orchestrator`、`verify_publish_complete` を該当境界の実consumerとして使う。

`fallback_ok` または `published_fallback_with_notice` を通常日次完了として扱わない。Do not treat `fallback_ok` or `published_fallback_with_notice` as normal daily completion. no generic `blocked_scope_violation`、no non-missing-handler default to `blocked_repair_handler_unimplemented` を不変条件とする。Normal daily runner fallback publish is forbidden.

公開完了は `distribution manifest`、`publish_complete`、`publish_dry_run_ok`、実公開面を別stateで確認する。Do not use stale recovery proof. Do not claim public proof from local tests. 外部境界は `typed external block`、仕様欠落は `typed Red` として分離する。

重大障害の証拠は `build/incidents/YYYY-MM-DD-<slug>-report.html` に private evidence by default で作成する。公開は separately validated public-action approval がある場合だけ許可する。
