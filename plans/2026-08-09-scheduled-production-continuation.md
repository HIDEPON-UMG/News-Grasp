# 2026-08-09 scheduled production 継続修復計画

## 目的

2026-08-03〜08-09 の scheduled production 障害を、2026-08-10 06:00 の通常 scheduled context と 06:40 audit/recovery が同一日付・同一budget lineageで自走する状態へ修復する。2026-08-07 は既存3 reporter artifactを保持し、9/9消費済みbudgetをリセットせず、正規権限内での復旧不能証拠を維持する。

## 確定前提

- 表示タイトルは `News-Grasp｜8/3〜8/9重大障害完全閉鎖・8/7公開復旧・定時実行完全自走化` とし、8/7単日やincident report作成へ目的を縮小しない。
- `origin/main` のclean runtimeが翌06:00に読まれる正本である。root-fix worktreeとdirty worktreeは比較証拠であり、file単位で公開mainを置換しない。
- 公開mainにはlauncher→bootstrap経路とpytest失敗保持が既に存在する。今回のproduction差分は、実ログで確認した契約driftだけへ限定する。
- 8/7の9/9 budgetとimmutable failure lineageは、修復やmodel routing変更を理由にresetしない。
- 要件定義、Acceptance、task class、scope、統合判断はSolaが所有する。
- 設計判断を含まないmechanical edit、bounded copy、targeted test、evidence extractionだけを `gpt-5.6-luna` / `reasoning_effort=max` へ委譲する。Lunaが利用不能またはadmission未成立ならSol/Terraへ代替せず、当該model operationだけをdeferする。

## 一次証拠と原因

- durable goal thread: `019fe434-c58f-7441-9a23-6f62aaf7c23b`。
- 2026-08-09 06:00 `News-Grasp Production`: `LastTaskResult=72`。同日recoveryはpublic Greenへ到達済み。
- startup failure producerは現行brokerの必須`run_id`を渡しておらず、failure receiptを凍結できない。
- 06:40 auditのGreen decisionはcompletion receipt hashだけを保持し、後段consumerが完全なcompletion evidenceを再検証できない。
- 旧branchからのfile-level移植は公開mainの既存処理・テストを約2,500行後退させるため不採用とする。

## TDD impactとRed

- 変更対象: `scripts/ops/news-grasp-bootstrap.ps1`、`tools/audit_recovery_control.py`、対応する2テストファイル。
- Red 1: bootstrap terminalizerに`'--run-id' $runId`がなく、現行broker契約へ適合しない。
- Red 2: `audit_normal_green`が`completionEvidence`を保持せず、完全証跡consumerがKeyErrorになる。
- Red 3: test helperのfailure receipt生成が現行broker必須`run_id`を渡さず、同日recovery契約が実行前に落ちる。
- positive route: startup failureは同じrun identityでimmutable receiptへ凍結し、audit Greenはreceipt hashと完全証跡を同時保持する。
- negative route: run-id欠落、cross-date、hash substitution、replay、canonical runtimeとartifact rootの混同を拒否する。
- human impact: hidden/noninteractive/no-focus-theft/no-auto-openを維持し、ユーザー操作や常駐監視を追加しない。

## 実行順序

1. 上記Redを公開main由来のfresh branchで観測する。
2. bootstrapへrun-id binding、audit decisionへ完全completion evidenceを最小実装する。
3. targeted Green、PowerShell/Python AST、full static pytestを一度だけ実行する。
4. safe-commit後に`origin/main`へ反映し、clean runtime・installed bin・Scheduled Task・AIHarnessState snapshotを同期する。
5. 2026-08-10実06:00と06:40のfresh scheduled-context evidenceを取得するまでgoalをactiveに保つ。

## 完了条件

- 06:00 Task actionがlauncher→bootstrap→permit→runnerの実経路を持つ。
- startup失敗時も`run_id`付きfailure receiptが凍結され、06:40監査が同日lineageを回収できる。
- audit Greenは完全completion evidenceを保持し、scheduled production / recovery / auditのauthority・budget・terminal stateを混同しない。
- source、installed runtime、Scheduled Task action、AIHarnessState snapshotの必要bytesが一致する。
- 2026-08-10 06:00がpublic completionまで自走し、06:40が独立再構成でGreenを確認する。失敗時は同日正規recoveryまたはtyped major incident terminalへ到達する。
