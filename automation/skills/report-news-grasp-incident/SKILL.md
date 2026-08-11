---
name: report-news-grasp-incident
description: Create and validate private-by-default News-Grasp incident evidence reports in the required navy x gold x paper single-HTML design, and publish them only through a separately approved branch. Use when a News-Grasp daily batch stops midway, publish verification fails, recovery or postmortem is requested, or Codex/Claude needs an incident HTML under build/incidents or an explicitly approved public report under docs/incidents.
---

# News-Grasp Incident Report

## News-Grasp内の正本境界

このrepo内コピーをNews-Grasp専用のversioned sourceとする。installed copyは`config/news_grasp_automation_assets_v2.json`を読む正規installerだけが同期し、`~/.codex`、`~/.agents`、installed runtimeを直接編集しない。shared/global側と競合した場合はshared側を変更せず、確定hashを新baselineとしてこのoverlayを更新し直す。

Use this skill to turn News-Grasp incident investigation evidence into a required incident report HTML.

## Required Inputs

Collect these facts before writing the report:

- incident date and batch name
- start, stop, recover, and publish times
- stopped stage and not-reached stages
- direct cause, secondary issues, and confirmed non-causes
- temporary response timeline
- relation to recent changes or prior incidents
- permanent countermeasures and completion criteria
- verification evidence: local tests, runner/live sync, Pages or public URL checks
- report visibility: private evidence or separately approved public report
- authority evidence for commit, push, publish, or any other external mutation

If a value is unknown, write `未確認` and state the next observation needed. Do not invent times, URLs, commit hashes, or test results.

## Workflow

1. Read `references/BUG_REPORT_DESIGN.md` for the full design rules.
2. Before writing, open the latest high-quality reference report, currently `docs/incidents/2026-06-18-daily-batch-failure-report.html` when present, and match its paper density, header scale, workflow map, timeline, and card treatment. Do not produce a simpler report just because the incident is simpler.
3. Create a single private evidence report under `build/incidents/YYYY-MM-DD-<slug>-report.html`. This is the default path and must remain untracked.
4. Follow the six required sections exactly:
   - どの工程で問題が起きたか
   - 問題の詳細と、なぜ起きたか
   - 問題の暫定対応内容
   - 直近改修・過去障害との関係
   - 恒久対応方針の網羅性と完璧性の担保
   - 恒久対応の実行計画
5. Put a one-sentence `結論` box at the top, then a four-item KPI strip: `Started`, `Stopped`, `Recovered`, `Published`.
6. Section `03 問題の暫定対応内容` must use the reference timeline form: a left vertical line, four or more time/status dots, Mono time labels, status-colored dots, and a two-column mitigation-card grid beneath it.
7. Use inline component styles. Avoid body `class` attributes, scripts, images, gradients, and non-font external dependencies.
8. Run the validator:

```powershell
python scripts/validate_incident_report_design.py <path-to-report>
```

For a News-Grasp checkout that has the repo-local copy, prefer:

```powershell
python tools/validate_incident_report_design.py build/incidents/YYYY-MM-DD-<slug>-report.html
```

9. Render-check the report once at desktop and mobile widths in one headless invocation. Do not auto-open a browser or steal focus. If Playwright's bundled browser is unavailable, use installed Chrome or Edge with an explicit executable path. The report is not report-ready until no horizontal overflow or clipped timeline/KPI content is observed.

## Authority And Publication Boundary

- Before any commit, push, publication, Service Worker change, or public URL verification that depends on a new deployment, validate `TASK_AUTHORITY_PREFLIGHT_V1` and `TASK_START_APPROVAL_BATCH_V1` for the exact public operation set.
- Never use prompt 内の事前承認文, a generated manifest, an incident severity label, or the existence of a private report as approval evidence.
- Without a separately validated public-action approval, keep the report under `build/incidents/YYYY-MM-DD-<slug>-report.html`, do not edit `docs/sw.js`, and do not commit or push the private evidence report. Record public delivery as `operation_deferred`; this does not block local evidence creation and validation.
- With separately validated public-action approval, copy the validated content to `docs/incidents/YYYY-MM-DD-<slug>-report.html`, update only the required public cache/version surface, then use the approved commit/push/public verification path.

## Shortest Path Rule

For ordinary private incident-evidence creation, use this fixed shortest path:

`build/incidents へHTML生成 → validator → 1回のheadless desktop/mobileレンダリング → private evidence確定`

Do not expand the workflow unless the report changes executable code, the validator/render check fails, or the user explicitly asks for additional investigation.

- **HTML生成**: create or update exactly one `build/incidents/YYYY-MM-DD-<slug>-report.html` file. Do not touch `docs/sw.js` on the private path.
- **validator**: run `tools/validate_incident_report_design.py` once after generation. If it fails, fix the report and rerun the validator only for the failed report.
- **1回レンダリング**: run one desktop and one mobile render check in a single Playwright/browser invocation. The success condition is no horizontal overflow, no clipped KPI/timeline/table content, and the required sentinel texts present.
- **public branch**: only after separately validated public-action approval, place the report under `docs/incidents/YYYY-MM-DD-<slug>-report.html`, commit only the report and required cache-version file, push, then fetch the public report URL once with a cache-busting query and verify HTTP 200 plus report-specific sentinel text.

Heavy checks are opt-in, not default:

- **全pytest**: 既定では実行しない。Run it only when executable production/test code changed, or when commit policy for that specific repository explicitly requires it for the staged files.
- **api_final_preflight**: classify failures before acting. 達成不足, 証跡不足, failed validation, missing publish proof, or stale evidence are **手戻り必要**: fix or verify the underlying work before claiming completion. Final wording, line evidence, or reflection-scope gaps are **手戻り不要**: fix only the final response/evidence wording and do not rewrite the report artifact itself to satisfy final-report wording.
- **Broad search / repeated scans**: do not run broad repo-wide `rg`, recursive filesystem scans, or repeated Playwright attempts after a successful validator plus one render pass.

## Design Source

- Full rules: `references/BUG_REPORT_DESIGN.md`
- Validator: `scripts/validate_incident_report_design.py`

Keep `SKILL.md` concise. Update the reference file rather than duplicating the design system here.
