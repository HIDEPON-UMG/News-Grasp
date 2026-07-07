# 2026-07-07 Summary materialize recovery evidence

## 結論

2026-07-07 の日次バッチは、newsroom editor が `build/reporter-artifacts/2026-07-07/editor-output.preview.json` に `summary_markdown` と `append_records` を出した後、runner が正式成果物へ同期しないまま `summary-reflection` gate へ進んだため異常終了した。

この Markdown は公開用 HTML report ではなく、`tools.historical_failure_scenarios` が参照する tracked evidence である。公開向けの障害レポート HTML は repo policy に従い `build/incidents/2026-07-07-daily-batch-recovery-report.html` に置く。

## Runner lane

- 停止 stage: `summary-reflection / validate_summary_reflection --date`
- 直接ログ: `ERROR: Summary digest が存在しません: digest\Summary\2026-07-07.md`
- runner 側の恒久対応: editor 成功後に preview JSON を読み、`digest/Summary/2026-07-07.md` と `data/articles.jsonl` の append records を materialize する契約へ寄せる。

## Repair lane

- 初期分類: `blocked_unknown_repair_class`
- repair の弱点: Summary 欠落、missing audio script、digest thumb、record thumb、pytest-static の外部 URL 混入が別々の typed issue として扱われず、復旧判断を遅らせた。
- 恒久対応: `summary-reflection` の Summary 欠落を `missing_artifact` として構造分類し、compound scenario で downstream repair blockers を同じ incident として固定する。

## State lane

- 復旧後 state: runner は `publish_complete`、`verify-publish-complete` は 2026-07-07 で Green。
- public proof: `build/recovery/proofs/2026-07-07-public-after-code-push.json` と `build/recovery/proofs/2026-07-07-horizontal-investigation-public.json`。
- publish anchor: local / remote HEAD は `35de084f91bf880a1a68ff8f2d5532159e108097` で一致済み。

## Report lane

- 既存 report: `build/incidents/2026-07-07-daily-batch-recovery-report.html`
- validator: `tools/validate_incident_report_design.py` pass。
- historical matrix への要件: この incident は runner / repair / state / report の4レーンを持つ scenario として登録し、07-07 の再発を unknown repair class のまま放置しない。

## 恒久対策

1. editor preview の正式 artifact materialize を runner contract に固定する。
2. Summary 欠落を `summary-reflection` の `missing_artifact` として coverage matrix に登録する。
3. downstream repair blockers を compound failure scenario に登録する。
4. public Green は `verify-publish-complete` と `verify_public_surface` で同日・同 HEAD を確認してから報告する。
