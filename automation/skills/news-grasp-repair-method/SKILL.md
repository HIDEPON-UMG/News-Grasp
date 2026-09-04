---
name: news-grasp-repair-method
description: Decide News-Grasp recovery and repair method for daily batch, public surface, distribution manifest, Podcast/YouTube, runner state, repair matrix, and public recovery proof. Use when Codex needs to recover or plan repair for News-Grasp after publish verification failure, missing public artifacts, distribution_manifest invalid, reporter/DeepDive/audio/Podcast repair, google_api_external/OAuth/quota readiness issues, deploy surface regression, rollback planning, or any request that mentions News-Grasp repair/recovery without replacing the production runner with ad hoc manual edits.
---

# News-Grasp Repair Method

## News-Grasp内の正本境界

このrepo内コピーをNews-Grasp専用のversioned sourceとする。installed copyは`config/news_grasp_automation_assets_v2.json`を読む正規installerだけが同期し、`~/.codex`、`~/.agents`、installed runtimeを直接編集しない。shared/global側と競合した場合はshared側を変更せず、確定hashを新baselineとしてこのoverlayを更新し直す。

Use this skill when News-Grasp repair or recovery is the work. This skill is not a generic incident report template and not an excuse to edit artifacts by hand. It keeps repair rooted in the product constitution, existing runner, repair matrix, public verifier, and typed evidence.

## Priority

Apply this order:

1. Latest user instruction and explicit forbidden actions.
2. `News-Grasp/docs/spec.md`, especially `Product Constitution`, `Principle 1`, `System Integrity`, and `Human Commitment`.
3. Current runner state, logs, manifests, and public surface evidence for the target issue date.
4. Existing repo-local repair boundaries:
   - `tools.publish_inventory`
   - `tools.repair_coverage_matrix`
   - `tools.auto_repair_orchestrator`
   - `tools.daily_self_heal.verify_publish_complete`
   - `tools.verify_public_surface`
   - `tools.recovery_state`
   - `tools.youtube_podcast.auth_doctor`
   - `tools.deploy_recovery_orchestrator`

Do not invent a parallel repair workflow when these boundaries can classify, repair, verify, or intentionally block the issue.

### Same-day public recovery first

`same_day_public_recovery_first` は助言ではなく、このskillの作業admissionである。対象日の公開面が Definition of Done を満たさない間、次に実行できるのは `scheduled_recovery`、その開始に必要な `minimal_recovery_unblocker`、または復旧不能をtypedに確定する `escalate_major_incident` だけとする。

public Green 前の `incident_report_polish`、`root_cause_hardening`、無関係なcleanup、Summary/title改善を禁止する。`tools.audit_recovery_control decide` のsealed decisionで `workPriority=root_cause_after_public_green` が成立した後にだけ、障害レポートと恒久修正へ進む。skill本文、automation prompt、agent判断でこの順序を上書きしない。

実行責務は次の順で固定する。

1. read-only inventoryから同日public completionを判定する。
2. incompleteならtyped recovery authorityと共有budgetを確定し、既存production recovery pathを起動する。
3. recovery authorityが成立しない、またはsame-gate再検証がGreenにならない場合は`audit_major_incident_open`を発行する。`operation_deferred`で終了しない。
4. public Green後にscheduled failureを保持したまま、runner / repair / state / reportの根本修正へ移る。

## Repair Decision Debt Covenant

Before adding downstream tests, smokes, or extra retry loops for a repair failure, define the upstream decision owner. A repair hardening is not permanent if several causes collapse into one status such as `blocked_scope_violation` or `blocked_repair_handler_unimplemented`.

Use the News-Grasp `docs/spec.md` `Repair Decision Debt Covenant` taxonomy:

- validator owns structured `issue_code`, target artifacts, date/category, and evidence.
- coverage matrix owns repair class, handler, allowed scope, verify gate, and explicit `status_on_failure`.
- orchestrator owns the ordered issue ledger; it must not hide compound failures behind the first issue only.
- registry owns handler existence, input scope mismatch, handler not-applicable, and output scope violation as separate statuses.
- runner owns selected issue artifact passing and must not round typed registry status into handler-unimplemented or generic error.

Normal daily runner fallback publish is forbidden. `fallback_ok` and `published_fallback_with_notice` are historical or separately approved manual-emergency evidence only; they are never daily-batch Green, terminal success, or completion proof.

## Horizontal Incident Investigation

Before choosing or reporting a repair, apply the News-Grasp `docs/spec.md` `Incident Bugfix Horizontal Investigation Covenant`.

Every News-Grasp bugfix, incident, recovery, E2E failure, runner failure, repair failure, and publish verification failure must inspect the same incident across these four lanes:

- runner: execution body, wrapper, stage transition, live copy, scheduler, NoPublish/RecoverOnly.
- repair: coverage matrix, registry, handler implementation, same-gate re-verify.
- state: runner state, distribution manifest, gate attempts, publish-complete, recovery proof.
- report: incident report, bug class, horizontal similar candidates, new bug candidates, permanent countermeasures.

Do not call the repair complete if any lane is uninspected. New or reclassified incidents must be reflected in `tools.historical_failure_scenarios` and covered by `tests/test_historical_failure_scenarios.py`, so past and future failures are checked with the same runner / repair / state / report lens.

## Repair Decision Tree

1. Identify the failing public experience.
   - Use `tools.publish_inventory` to resolve required web, audio, Podcast, playlist, notification, and distribution artifacts.
   - Treat `distribution manifest`, `publish-status`, DeepDive HTML/Markdown, Podcast playlists, and notification state as part of the same user-visible completion surface.
2. Classify the failure before mutating files.
   - Use `tools.repair_coverage_matrix` and `tools.auto_repair_orchestrator` when the issue is known.
   - Keep `unknown repair class` as typed Red. Do not create a free-form LLM repair path.
   - Keep `google_api_external`, OAuth, YouTube quota/permission, GitHub Pages timeout, and deploy workflow failure as typed Yellow or typed Red until the full verifier proves recovery.
3. Choose the smallest production repair path.
   - Prefer patch existing when an artifact exists and only deterministic content or metadata is wrong.
   - Use `quarantine+refill` when a bad artifact must be removed from the publish set and a required replacement can be generated safely.
   - Use `reporter retry` only for the specific failed reporter/category/date and only through the runner or repo repair entrypoint.
   - Use LLM regeneration only when typed evidence says the required artifact is missing or unrecoverably corrupt.
4. Re-verify with the same completion gate.
   - A local repair is not Green until `tools.daily_self_heal.verify_publish_complete` or the thin public wrapper `tools.verify_public_surface` verifies the full required surface for the same date.
   - `tools.recovery_state` Green requires matching repo root, HEAD, required surface digest, TTL, and `external_block_code=none`.
   - Stale proof, wrong HEAD, wrong repo, expired proof, hand-edited digest, and legacy `recovery_green` are typed Red.

## Executable Audit Recovery Decision

このskillはauthorityを発行せず、terminalを自由文で決めない。repair matrix / `tools.auto_repair_orchestrator` の構造化出力をrepo-local canonical consumer `tools.audit_recovery_control`へ渡し、次の3分類だけを使う。

- `normal`: scheduled attempt自体とsame-date completionがGreen。
- `recoverable`: 登録済みrepair classで、broker-issued recovery authorityと共有budgetが利用可能。
- `incident_required`: unknown class、authority unavailable、budget/receipt/date/lineage不整合、外部認証判断、same-date public incomplete。

`AUDIT_RECOVERY_DECISION_V1`のterminalは`audit_normal_green`、`audit_recovered_green`、`audit_major_incident_open`だけである。`operation_deferred is not a terminal`。子operationが延期されても当日公開面が不完全なら`audit_major_incident_open`へ上げる。全成果物欠落時は`ScheduledRecoveryFull`を使い、生成済みartifact前提の`RecoverOnly`を選ばない。

監査入力は次の実行契約に従う。callerはauthority、completion Green、terminal出力先を自己申告しない。

- `python -m tools.audit_recovery_control classify-repair --input <repair-payload.json>` でrepair classをtyped化する。
- `python -m tools.audit_recovery_control decide --input <audit-input.json>` を呼ぶ。`--terminal-output`は存在せず、terminalはconsumerがrepo-local `build/incidents/<issue-date>-audit-terminal.json`へatomicに書く。
- 復旧またはterminal確定は `python -m tools.audit_recovery_control execute --input <audit-input.json>` を1回だけ呼んで閉じる。audit agent は runner を直接起動しない。executorだけがledger-backed authorityを再検証し、production recoveryを1回起動し、same-gate再検証から`audit_recovered_green`または`audit_major_incident_open`を発行する。
- `<audit-input.json>` は `artifactRepoRoot`、canonical `opsRepoRoot`、`recoveryExecution` を必須にする。`recoveryExecution` は `issueDate`、`recoveryAuthorityReceiptSha256`、`artifactRepoHead`、canonical `runnerSha256` へexact bindingし、`runIntent=ScheduledRecoveryFull`、`maxExternalModelCalls=9`、`maxFullE2EAttempts=0`、`noFocusTheft=true`、`noUserMonitoring=true`、`noAutoOpen=true` を持つ。executorはartifact repoのorigin identity、ops root、authority、HEAD、canonical runner bytesを実行直前に検証し、artifact repo内のrunnerを実行しない。
- `<audit-input.json>` は `issueDate`、`repairDecision.classification`、`humanImpact` を持つ。scheduled failureでは `scheduledFailureReceiptPath` と `recoveryAuthorityPath` をrepo-local `build/**`配下の実ファイルへ束縛する。scheduled/recovery attempt statusとrunner state pathはcallerに指定させない。
- consumerは固定installed brokerの `inspect-news-grasp-attempt` でscheduled reservation、immutable failure、recovery admission eventをdurable ledgerから導出する。recovery authorityも同じledgerと照合し、callerが作ったJSONやSHA整合だけでは受理しない。
- Green判定はconsumerが固定 `%USERPROFILE%\bin\news-grasp-runner-state.json`、`validate_daily_quality --require-deepdive`、`verify_publish_complete`を実行して再構成する。callerのboolean、前日publish-status、unkeyed receiptを読むだけの`check-completion`は証拠にしない。

## DeepDive Shared Quality Repair

DeepDive記事、関係図、Podcast対談、公開HTMLは、同じ `DEEPDIVE_QUALITY_REVIEW_V2` と共有validatorで一つの品質境界として扱う。production generation、repair/publish、daily quality、Codex日次監査は全て `python -m tools.deepdive_quality --repo-root . audit-issue --date <YYYY-MM-DD>` を使い、次のissue codeだけを受理する。

- `deepdive_url_provenance_invalid`
- `deepdive_article_value_invalid`
- `deepdive_relation_quality_invalid`
- `deepdive_dialogue_value_invalid`
- `deepdive_research_evidence_insufficient`
- `deepdive_public_surface_invalid`

共有routeは `production_generation`、`repair_publish`、`daily_quality`、`codex_daily_audit` の4つだけである。未登録のissue codeまたはrouteはfail-closedにし、自由文の分類や旧経路へのフォールバックを行わない。意味品質レビューはarticle/relation/dialogueのrepo-relative pathと実bytes identityへbindし、7軸を各1〜5で評価する。`averageScore`、evidence-backed findings、`reviewRoute`、`status`を同じreceiptへ束縛し、hashは鮮度・byte一致の検出だけに使ってsemantic authorityにはしない。

| issue code | repair class | handler | 実行条件 |
|---|---|---|---|
| `deepdive_url_provenance_invalid` | deterministic | `deepdive-provenance-recapture` | URLの観測・最終URL・status・本文bytesを再取得し、同日provenanceへ再束縛する。 |
| `deepdive_article_value_invalid` | LLM_REWRITE_EXISTING_ARTIFACT | `deepdive-article-value-rewrite` | 既存記事を入力に記事固有の意味差分をLLMで書き直し、V2 reviewを再実行する。 |
| `deepdive_relation_quality_invalid` | LLM_REWRITE_EXISTING_ARTIFACT | `deepdive-relation-quality-rewrite` | 8-kindとsingleKindRationaleを満たす記事固有の関係図へLLMで書き直し、V2 reviewを再実行する。 |
| `deepdive_dialogue_value_invalid` | LLM_REWRITE_EXISTING_ARTIFACT | `deepdive-dialogue-value-rewrite` | 記事固有の根拠からLLMが可変turnの対談を生成し、先輩常体・若手敬体とV2 reviewを再実行する。固定turn、最低文字数、最低再生時間、filler、根拠言換えだけの反復で補完しない。 |
| `deepdive_research_evidence_insufficient` | LLM_RESEARCH_AND_REWRITE | `deepdive-research-and-rewrite` | 追加調査と書き直しを一回のbounded operationとして行い、根拠不足を推測で埋めない。 |
| `deepdive_public_surface_invalid` | deterministic | `deepdive-rendered-public-rebuild` | sourceのV2 reviewがGreenの場合だけ、同じvalidated sourceからsafe rerenderする。 |

TTSまたは公開HTMLの前には、共有internal-metadata stripperによるpreauditを必ず行う。raw/escaped claim-source・value・evidence・support comment、transport JSON、Markdown制御断片を表示文と`source_evidence_sentences`から除去し、残存・除去不能・再検証失敗は `deepdive_public_surface_invalid` とする。V2 source auditとmetadata preauditがGreenになるまでTTS、公開、またはsafe rerenderを開始しない。対談の意味品質レビューは記事・関係図・台本のpathとbyte identityへ束縛し、LLMが生成したvalidated staged artifactだけを採用する。

runnerはURL provenance capture、記事・関係図・対談のV2 review、metadata preaudit、TTS/公開の順で進め、RecoverOnlyでも順序を変えない。過去期間の明示監査だけ `audit-period --start <date> --end <date>` を使い、通常の日次修復で過去期間を無制限に走査しない。bot/CA差によるtransport fallbackを使う場合も、共有engineの同じprovenance検査とV2再検証を通す。

## Scheduled Audit And Safe Stop Boundary

### Authority / Budget / Entrypoint Matrix

- `production_scheduled_run`: Codex automationのscheduled triggerで起動し、同一issue-date最大9 external model callsを持つ。対話goalの有無をadmission条件にしない。
- 正規entrypointは固定Python 3.12による`python -m tools.news_grasp_direct_runtime daily`の一経路だけとする。旧Windows task、旧wrapper、旧runnerを通常日次の起動候補・fallback・next-run readyの証拠にしない。source変更は明示的promotionでinstalled automationとruntimeへ反映する。
- `production_recovery_run`: 不変の`SCHEDULED_FAILURE_RECEIPT_V1`から派生した`SCHEDULED_RECOVERY_AUTHORITY_V1`で一度だけ起動する。productionと同じissue-date ledgerの残予算を共有し、run ID・workspace・receipt名を変えて予算をresetしない。full E2E budgetは0。
- `audit_run`: `AUDIT_MISSION_AUTHORITY_V1`でread-only分類、typed recovery起動、same-gate再検証、major incident terminal発行を所有する。通常budgetはexternal model 0 / full E2E 0。audit自身がproduction model call権限を持つのではなく、brokerが発行したrecovery authorityをproduction recoveryへ渡す。
- audit agent 自身はrunnerの起動・retry・再開を所有しない。repo-local `tools.audit_recovery_control execute` が唯一の起動consumerであり、one-shot recovery後に必ずtyped terminalまで到達する。これにより自由文の「deferred」や、判断後に実行を忘れる状態を作らない。

### Recovery Workspace And Live Ops Root

- dirty canonical repoを保護するためclean recovery workspaceを使ってよいが、`artifact_repo_root`と`ops_repo_root`を混同しない。artifact rootは当日生成物・git HEAD・distribution・publish manifest、ops rootはCodex automation・installed runtime・snapshotの正本である。
- recovery runnerは`-OpsRepoRootOverride <canonical-repo>`、completion verifierは`--repo-root <artifact-recovery-workspace> --ops-repo-root <canonical-repo>`を使う。recovery cloneのcheckout改行差をlive runner driftと誤認したり、clone bytesをcanonical live sourceへ昇格したりしない。
- `verify-publish-complete` Green後にrunner stateだけがRedなら、生成・TTS・upload・notificationを再実行しない。runnerの`-FinalizeVerifiedPublishManifest`を`ScheduledRecoveryFull` intentで一度だけ使い、同一manifestのdate、public status、scheduled/recovery status、Podcast、notification、next-run readiness、local/remote commitを再検証して`publish_complete`へ遷移させる。
- typed finalizerが失敗した場合は`deferred`や再生成へ逃がさず、`audit_major_incident_open`とする。
- `blocked_startup_self_repair_failed` はfixed stateの`attempt_terminal=true`と`scheduled_failure_receipt_path`を必須証拠とする。broker ledger一致なら通常のrecovery authorityを導出し、receipt欠落・terminalizer失敗・ledger不一致は外部境界として丸めず`audit_major_incident_open`にする。手製receiptや自由文reconcileで復旧権限を作らない。

- Preserve three independent fields throughout audit and recovery: `scheduled_attempt_status`, `recovery_attempt_status`, and `public_status`. A later recovery/public Green must not rewrite a failed scheduled attempt to success.
- 通常06:00 Codex automationのhigh-cost admissionは `scheduled_production`、同日復旧は `scheduled_recovery` を使う。通常日次失敗をfinal E2Eの再試行へ読み替えず、final E2Eを起動しない。
- scheduled productionはCodex automation triggerに束縛した`SCHEDULED_PRODUCTION_LAUNCH_PERMIT_V1`、scheduled recoveryは同日の不変`SCHEDULED_FAILURE_RECEIPT_V1`からbrokerが派生した`SCHEDULED_RECOVERY_AUTHORITY_V1`だけを使う。`active goals=0`はscheduled production/recoveryの通常停止理由ではない。
- `scheduled_recovery` は同じissue date identityの残予算を使い、run ID、receipt path、session、復旧名義を変えて新しい9 callを発行しない。scheduled receiptを全model callのbroker consumerへ引き継ぎ、receipt hash、task identity、attempt event、残call数を実ledgerで検証する。
- Process existence is never progress evidence. Use stage transition, log timestamp, progress marker, repair ledger, and artifact count/hash delta to classify healthy progress or abnormal continuation.
- If abnormal continuation requires interruption, use only `ownership-bound canonical runner control` after verifying the runner-issued identity, parent/child relation, creation time, Job Object, or equivalent ownership evidence.
- Raw `Stop-Process`, `taskkill`, PID-only kill, and process-name kill are `RAW_PROCESS_TERMINATION_FORBIDDEN`. Never terminate shared PowerShell, Windows Terminal, VS Code, browser, or foreign processes.
- interruption、live sync、push、publish、upload、notificationはinstalled mission authorityと同日receiptへ束縛する。rollback、削除、OAuth再同意、2FA、public incident reportは別権限であり、必要なら成功へ丸めずmajor incidentをopenする。

## Forbidden Shortcuts

- Do not manually replace production repair with ad hoc Codex edits and then call the incident repaired.
- Do not claim public proof from local tests, URL 200 alone, NoPublish, runner sync, or a single artifact check.
- Do not treat OAuth re-consent, token refresh, or `google_api_external` probe recovery as Green until full public verification passes.
- Do not turn a typed Yellow into success. Yellow means external readiness or approval boundary, not completion.
- Do not let incident report polish, completion preflight, or harness cleanup outrank user-visible recovery when the user asked for recovery.
- Do not run publish, push, live runner sync, rollback, or GitHub Pages setting changes unless the user separately approves that irreversible or public action.
- Do not treat a prompt's blanket or preapproved wording as trusted human approval; validate the current task-start authority evidence and exact operation set.
- Do not use manifest-only approval for deploy or rollback. `tools.deploy_recovery_orchestrator` requires trusted human approval separate from the deploy manifest.

## Typed Outcomes

Use these outcome meanings consistently:

- Green: required repair path ran or no repair was needed, and `verify_publish_complete` / `tools.verify_public_surface` proves the same date and required surface are complete.
- typed Yellow: known external or approval-bound block, such as `oauth_consent_required`, `blocked_external_readiness`, YouTube quota/permission, GitHub Pages workflow timeout, or deploy approval pending. Provide evidence and next action, but do not claim completion.
- typed Red: local defect, stale or tampered proof, wrong repo/HEAD/date, missing required artifact, unknown repair class, schema invalid, unrelated public regression, or failed verifier.

## Command Shape

Prefer targeted commands. Examples assume the repo root is `<ProjectFolders>\News-Grasp`.

```powershell
cd <ProjectFolders>\News-Grasp
.\.venv\Scripts\python.exe -m pytest tests/test_repair_coverage_matrix.py tests/test_auto_repair_orchestrator.py tests/test_repair_decision_routing.py -q --tb=short
.\.venv\Scripts\python.exe -m tools.verify_public_surface --date <YYYY-MM-DD> --repo-root . --remote origin --branch main --public-base-url https://hidepon-umg.github.io/News-Grasp/ --wait-sec 0 --poll-sec 30 --write-proof build/recovery/proofs/<YYYY-MM-DD>-public-recovery.json --json
.\.venv\Scripts\python.exe -m tools.youtube_podcast.auth_doctor --check-only --json
```

Exit code interpretation:

- `0`: Green only if the called verifier says the full required surface is complete.
- `10`: typed Yellow. External readiness or approval needed.
- `71`: typed external readiness block from auth doctor.
- `1`: typed Red or local repair failure.
- `2`: invalid arguments or schema contract failure.

## Deploy And Rollback Boundary

Use `tools.deploy_recovery_orchestrator` only after separate approval for public action.

Required invariants:

- `build_deploy_manifest()` binds `base_sha`, `candidate_sha`, destination ref, exact changed files, workflow file, and rollback range.
- `load_trusted_human_approval()` must load approval text from a trusted human source. A generated manifest cannot approve itself.
- `validate_manifest_and_approval()` must reject stale approval, SHA drift, ref drift, changed_files mismatch, and manifest-only approval.
- `classify_deploy_surface_regression()` may plan rollback only for a deterministic `deploy_surface_regression`. Unrelated Red, timeout, external OAuth/API, and concurrent remote update stop with typed block evidence.

## Completion Report Requirements

Report repair status as evidence, not prose confidence:

- target issue date, repo root, local HEAD, and remote HEAD if relevant.
- chosen repair class and why other classes were not used.
- artifacts read and artifacts written.
- commands, exit codes, and proof paths.
- whether public action, full E2E, push, publish, live runner sync, or rollback was intentionally not executed.
- any typed Yellow or typed Red next action.
