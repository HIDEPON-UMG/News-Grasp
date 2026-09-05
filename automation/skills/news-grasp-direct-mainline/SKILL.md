---
name: news-grasp-direct-mainline
description: Run the 06:00 News-Grasp scheduled production through its single direct runtime entry and report the machine receipt without manual orchestration.
---

# News-Grasp Direct Mainline

## Lunaの操作

実行操作は次のMCP toolを空のJSONで一回だけ呼び出すことに限る。個別phase、管理command、raw script、旧runner、NoPublish、historical、Playwright、full test、手動repair、pollingを開始しない。

```text
news_grasp_daily.run_daily {}
```

toolが返したmachine receiptを日本語で報告する。原因調査、別経路への切替え、run ID・SHA・manifest IDの指定、同じtoolの再実行をLunaが判断しない。`public incompleteかつexact successorがある状態で終了しない`責任はruntime brokerが持ち、Lunaは低位operationを列挙・実行しない。tool不在時にshell fallbackへ切り替えず、`typed_tool_unavailable`として報告する。

## Runtime契約

Daily 六phaseはruntimeが次の順序で所有する。

1. `static_check`
2. `scoped_contract_unit`
3. `current_issue_integration`
4. `external_publication`
5. `consumer_public_verification`
6. `atomic_completion`

runtimeは `automation_id + issue_date + run_intent` と開始時sealからactual runを解決する。開始後の観測SHA・設定driftは現runを止めず次回readiness debtへ分離する。lease失効時は競合writer不在を確認し、同じrunへCAS takeoverして最初の未完了phaseから続ける。Green receipt、成果物、provider receiptは再実行・再送しない。

MCP serverはinstalled TOMLのcwdを実行authorityにしない。非terminal runがない時だけ固定clean runtimeをpromotionし、broker receipt、marketplace、installed/enabled plugin、file hash、実serverの`tools/list`を同じsource generationへ束縛する。実行時はcommit ancestryとserver bytesも検証し、stdinとchild出力をboundedに処理する。90分でchildをkillせず、SLO debt後も同じrunの決定論的successor、read-only reconcile、consumer verification、finalizationをruntime内で続ける。

runtime改ざん、state DB破損、競合live writer、許可外副作用だけは`failed_integrity`とする。provider ACKが取得不能なら`unknown_unobtainable`を保持し、推測で成功または再送へ変換しない。

## 生成・品質

- 対象カテゴリは `tools.publish_inventory.scheduled_category_ids(issue_date)` の結果だけとする。
- Summaryはfrontmatter付きMarkdown、DeepDiveはcurrent issueの実bytesを正本とする。
- reporter / editor / repair / newsroom_editorはLuna/max、DeepDiveはSol/highを維持する。
- 品質Redは既存成果物を入力に対象artifactと依存下流だけを修正する。非対象artifactを作り直さない。
- checkpoint、RepairPlan、model call予算は正規runtime SQLiteでrun/issue/writer/fenceへ束縛し、同一issue dateではrun IDを変えても予算をリセットしない。reuse時も正規validatorと実file hashを確認し、不一致のartifactだけを`causeInputMask`付きでdirtyへ戻す。修復は許可されたfield/section以外の変更を拒否し、reporter shardのGreen categoryは部分保存して再生成しない。
- `DEEPDIVE_QUALITY_REVIEW_V2` のissue codeは `deepdive_url_provenance_invalid`、`deepdive_article_value_invalid`、`deepdive_relation_quality_invalid`、`deepdive_dialogue_value_invalid`、`deepdive_research_evidence_insufficient`、`deepdive_public_surface_invalid` だけとする。
- cost/ledger/binding failureは該当model operationだけをzero-call Redにし、実行可能なexact public successorを継続する。
- 45分で公開critical pathへ限定し、75分以降は新規candidate収集、model生成、高コスト派生成果物を開始しない。idempotency keyを予約済みで未送信のpublic-critical初回送信は継続し、startedまたはACK不明のprovider operationは再送せず照合する。SLOは90分だが、same-run復帰、決定論的build、read-only照合、公開検証、finalizationは停止しない。

## 完了authority

consumer-owned verifierがWeb、Daily audio、DeepDive article/audio、YouTube、playlist、notification、distribution、publish-status、remote、Pagesを同一issue date/run lineageで確認し、finalizerが連言を確定した場合だけcompletedとする。callerの`ok=true`だけではrunをcompletedにしない。

Git commit ID は観測値としてだけ報告してよい。runner state、readiness、durable goal、URL 200単独、artifact存在、saved JSON、commit/push単独はcompletion authorityではない。runner/readinessをpublic verifierの代替にしない。

## 報告

`issue_date`、`title_status`（例: `title_status=already_ok`）、public status、失敗surface、typed terminal、exact successor、`post_publish_issue_list`、elapsed、SLO差分を先に示す。公開未完了をGreenとして報告しない。
