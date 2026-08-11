# News-Grasp 運用復旧 skill v1

この資産は News-Grasp 専用の運用復旧入口であり、共有ハーネス、共有broker、他productの設定を変更しない。

## 適用順序

1. `CompletionStateVectorV2` の `publicCompletionStatus`、`nextRunReadinessStatus`、`auditObservationStatus` を別々に読む。
2. 検証済み公開Greenがある場合は、それをreadiness失敗や検証不能でRedへ戻さない。
3. `probe-readiness` は純粋観測だけを行い、修復は登録済みhandlerの `operational_recovery_registry_v1.json` に限定する。
4. 有効な `ArtifactCheckpointV1` があるstageは、wrapperの異常終了・timeout・hangでもモデルを再実行せず、決定論的後続工程を再開する。
5. retryは `issueDate | dailyOperationLineageId | artifactKey | producerRouteId | failureClass` とcauseInputMaskで判定し、無関係なrun/session/path変更では再許可しない。
6. unknown reasonはmajor incidentへ追記するだけで、shell・model・source・rule・test writeへ到達させない。

## Luna実行境界

Luna Maxは、確定済みpacketの機械編集、fixture、テスト、hash、JSON検証だけを実行する。未確定decision、共有ハーネス変更、public semantics変更、未登録failure classを検出したら実行を止め、`return_to_sol_before_execution` を返す。共有/global pathはread-onlyである。

## 完了証拠

scheduled attempt、recovery attempt、public completion、readiness、audit observation、operational statusを個別に提出する。自然scheduled runの待機やユーザー目視を証拠にしない。NoPublish E2Eは上流証拠が全てGreenになった後に一回だけ実行する。
