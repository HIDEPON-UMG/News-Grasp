# News-Grasp 06:40 canonical audit/recovery prompt V2

対象日の監査と復旧は、必ず次の canonical endpoint に一度だけ接続する。

```powershell
python -I -B -m tools.audit_recovery_control ensure-0640 --issue-date <YYYY-MM-DD> --trigger automation
```

- runner、daily controller、verifier、finalizerを直接起動しない。
- Deadman、watcher、Codex automation、direct CLIは同じissue-date transactionへacquire-or-attachする。
- `mode=attached`またはprocess exit `3`は、既存ownerを観測中であり新しいrunnerを起動しない。
- terminalは`audit_normal_green|audit_recovered_green|audit_observation_unverified|audit_major_incident_open`の4値だけを使う。
- public Green、SLO、readiness debtを混同しない。readiness debtだけで既存public authorityを後退させない。
- 2026-08-14の再公開、Full E2E、公開incident reportを実行しない。
- 06:40 JSTをcaller上書き不能なSLO anchorとし、receiptの90分hard deadlineを越えて新規operationを始めない。
- 最終結果はcanonical endpointのsealed transaction/terminal projectionだけから報告する。
