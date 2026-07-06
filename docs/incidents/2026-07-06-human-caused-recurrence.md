# 2026-07-06 News-Grasp selected_total 再発・人的災害認定メモ

## 結論

2026-07-06 の daily-quality 停止は、`data/search_audit/<date>/<category>.json` の `selected_total` と digest article count の不一致が `unknown` issue として扱われ、既存 deterministic repair handler `search-audit-metadata-patch` に到達できなかったことが直接原因である。

この問題は 2026-07-04 に同種の `selected_total` drift を扱ったにもかかわらず、validator / coverage matrix / orchestrator / runtime repair / incident report validator への横並び調査が不足したため再発した。ユーザー指示に従い、人的災害として扱う。

## 固定した不変条件

- `selected_total=... does not match digest article count ...` は `search_audit_count_mismatch` として分類する。
- `search_audit_count_mismatch` は `search-audit-metadata-patch` に deterministic routing する。
- unknown issue は既知 deterministic issue より低優先度にし、既知修復を mask しない。
- incident report は当該 incident 固有の必須 sentinel と stale evidence 禁止 sentinel を validator で検査する。
- historical failure matrix は runner / repair / state / report の 4 レーン調査に今回の incident を含める。

## 証跡

- Red: `tests/test_validate_daily_quality.py::test_daily_quality_cli_json_classifies_search_audit_count_mismatch`
- Red: `tests/test_repair_coverage_matrix.py::test_daily_quality_selected_total_mismatch_routes_to_search_audit_metadata_patch`
- Red: `tests/test_auto_repair_orchestrator.py::test_classify_routes_search_audit_count_mismatch_to_metadata_patch`
- Red: `tests/test_repair_runtime_e2e.py::test_daily_quality_runtime_repairs_search_audit_count_mismatch`
- Red: `tests/test_incident_report_design.py::test_incident_report_content_sentinels_reject_stale_evidence`

## 残境界

この Markdown は historical failure matrix の tracked evidence であり、公開向け HTML report ではない。HTML evidence は `build/incidents/2026-07-06-human-caused-recurrence-report.html` に生成し、repo-local validator と render check で検査する。
