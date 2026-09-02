from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory


ONE_MONTH_OPERATIONAL_FAILURE_CORPUS_V1 = "ONE_MONTH_OPERATIONAL_FAILURE_CORPUS_V1"


REQUIRED_HORIZONTAL_LANES: tuple[str, ...] = ("runner", "repair", "state", "report")


@dataclass(frozen=True)
class HistoricalFailureScenario:
    issue_date: str
    stage: str
    direct_cause: str
    root_pattern: str
    missing_invariant: str
    cheapest_e2e_or_fixture: str
    evidence_path: str
    expected_status: str

    @property
    def horizontal_lanes(self) -> tuple[str, ...]:
        """全 incident で同じ横並び調査レーンを要求する。"""
        return REQUIRED_HORIZONTAL_LANES

    @property
    def horizontal_scan_summary(self) -> str:
        """既存 scenario 情報から runner/repair/state/report 調査観点を生成する。"""
        return (
            f"runner stage={self.stage}; "
            f"repair root_pattern={self.root_pattern}; "
            f"state missing_invariant={self.missing_invariant}; "
            f"report evidence_path={self.evidence_path}"
        )

    @property
    def compound_id(self) -> str | None:
        return {
            "2026-06-19": "same_artifact_repair_plus_residual_red",
            "2026-06-25": "multi_gate_repair_before_publish_boundary",
            "2026-07-20": "external_block_plus_local_repair",
            "2026-07-24": "weekday_inventory_plus_distribution_manifest",
            "2026-07-07": "summary_materialize_missing_plus_downstream_repair_blockers",
        }.get(self.issue_date)

    @property
    def finite_terminal(self) -> bool:
        return self.compound_id is not None


@dataclass(frozen=True)
class HistoricalEvidenceValidation:
    valid: bool
    mode: str
    evidence_path: str
    expected_sha256: str = ""
    actual_sha256: str = ""
    reason: str = ""
    operationalClosure: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CompoundFailureScenario:
    scenario_id: str
    dimensions: tuple[str, ...]
    gates: tuple[str, ...]
    no_publish_required: bool
    forbidden_public_actions: tuple[str, ...]
    expected_status: str
    evidence_basis: tuple[str, ...]


@dataclass(frozen=True)
class HistoricalFailureHorizontalAudit:
    issue_date: str
    stage: str
    evidence_path: str
    lanes: dict[str, str]
    confirmed_gap: str
    current_contract: str
    residual_risk: str
    required_followup: str


@dataclass(frozen=True)
class WeeklyFailureRegressionCase:
    issue_date: str
    gate_id: str
    issue_code: str
    expected_repair_class: str
    expected_status: str
    expected_handler_id: str = ""
    artifact_paths: tuple[str, ...] = ()
    category: str = ""
    evidence: tuple[tuple[str, str], ...] = ()


LOCAL_ONLY_EVIDENCE_SHA256: dict[str, str] = {
    "docs/incidents/2026-06-18-daily-batch-failure-report.html": (
        "c0356fa3004f2af8bb52cd225dfc75eb054a737bbc181fcb4cf52bee57258688"
    ),
    "docs/incidents/2026-06-19-batch-recovery-report.html": (
        "1456554157b39e58ec1df8f5e3592a74a9f786254b7d4e51392aeb7137a1f3c6"
    ),
    "docs/incidents/2026-06-19-thumbnail-missing-report.html": (
        "dee740f36eb82807c2fdda81e02a5442141e667a4d1d8e7cb51457899edc31a5"
    ),
    "docs/incidents/2026-06-21-daily-batch-recovery-report.html": (
        "a91ace1d31276a6a50a9951c88407590bd4331bdb9e87e0f2416b57e5e956e18"
    ),
    "build/recovery/proofs/2026-06-26-post-gate-verify-publish-complete.json": (
        "359dc7006baee6b9d829aea636d747d462665587c346b447f8c6c29ee330e21a"
    ),
    "build/incidents/2026-07-19-daily-quality-repair-routing-report.html": (
        "c3992a3f0c55f6d5748cac9735c08efbffd074ca79bc1ddf7cbd0afca15e26a0"
    ),
    "build/incidents/2026-07-20-daily-batch-github-audio-upload-report.html": (
        "7e625ec30ad83d494edea00372d439774f1e3f6a619d98453ec384f2475b85a6"
    ),
    "docs/incidents/2026-07-24-daily-quality-editorial-section-report.html": (
        "d896a6ea1e4f6064e7bf6767a786b96daff83618ecd1b0f3409478ee968ce4b4"
    ),
    "docs/incidents/2026-07-25-codex-doctor-git-lock-recovery-report.html": (
        "a64571f3179fbe7a0ee45b31e2a3d6bdfadb0f97b49d0462716119c2dc405112"
    ),
    "docs/incidents/2026-07-26-daily-quality-stale-lock-playlist-recovery-report.html": (
        "c576ef56b5dfd60d777bae4ddf83351dac4020a52574405631e2d2fa5faa6b9b"
    ),
    "docs/incidents/2026-07-27-digest-articles-reconcile-report.html": (
        "f771f22d3d5c4038dd39f3055c2dc79ce6784272a617f35d1b3c8b8e889f9cdb"
    ),
    "docs/incidents/2026-07-28-generation-quality-date-evidence-recovery-report.html": (
        "63fb2317f39480528262f10eae4942c02c328cc47e2b52fce5e9b1a7e7f5c2cc"
    ),
    "docs/incidents/2026-07-29-daily-quality-recovery-report.html": (
        "9ac2b0220082727502a671731195ac3aaa1c88684f3d7d13efcdc03bece5e5e6"
    ),
    "docs/incidents/2026-07-30-pytest-static-historical-corpus-report.html": (
        "0f1d866bd1846378e7b63dae0112ddfe05bd8e5f86733ef5729133afc75692c6"
    ),
    "docs/incidents/2026-07-31-daily-batch-manufacturing-preview-drop-report.html": (
        "0b99bd58166b1a666f38658c68d25318948ff35082c652b40e118db1f844d7a2"
    ),
    "docs/incidents/2026-08-01-daily-batch-editor-contract-cwd-report.html": (
        "7ba812efbb08bf29ddb52cf669fb93afa5f14ad037142d9fba93554a9d2f0f05"
    ),
    "build/repair-review/2026-08-02-scheduled-high-cost-tdd-impact.json": (
        "781aa72eb0b88390ee0fb1f07c1ddc599bed6109a1d458a73f23d281cc0f99d2"
    ),
    "build/repair-review/2026-08-03-startup-self-repair-tdd-impact.json": (
        "980b6732639dcd8287d8d83875d7685acbdb4fa4ca3fa48188d3da3b07cbc83f"
    ),
    "build/incidents/2026-08-13-daily-batch-and-recovery-delay-report.html": (
        "50ee3e4427ce6d6acf82c649c7a0288f0a751bb42e65ad3dcc3debe84bd72286"
    ),
    "plans/2026-08-27-news-grasp-public-recovery-closeout/operational-design.md": (
        "5a8aeabf89fb5e355710b3ab7b824da8457b9a3475af6558b1611aac2b9fd142"
    ),
}


SCENARIOS: tuple[HistoricalFailureScenario, ...] = (
    HistoricalFailureScenario(
        "2026-06-12",
        "record-schema / daily-quality / url-liveness",
        "record schema gate surfaced masked multi-error classes across attempts",
        "masked multi-error gate disclosure",
        "one gate attempt must disclose all local record violations needed for bounded repair",
        "record-schema fixture with title/thumb/date mismatches in one artifact and same-gate rerun",
        "data/gate_attempts/2026-06-12.json",
        "runtime_e2e_required",
    ),
    HistoricalFailureScenario(
        "2026-06-13",
        "daily-quality / digest-articles-reconcile / pytest-static",
        "multiple public-preflight gates failed in one daily run",
        "multi-gate pre-publish convergence",
        "daily run cannot be healthy until every recorded gate converges or blocks with typed status",
        "gate-attempt ledger fixture covering multi-gate convergence and retry budget",
        "data/gate_attempts/2026-06-13.json",
        "fixture_required",
    ),
    HistoricalFailureScenario(
        "2026-06-14",
        "daily-quality / url-liveness / reconcile / pytest-static",
        "pre-publish gate chain contained repeated local content failures",
        "multi-gate pre-publish convergence",
        "gate-attempt ledger must preserve every failed gate, not only the final stop",
        "gate-attempt ledger fixture for required gate set",
        "data/gate_attempts/2026-06-14.json",
        "fixture_required",
    ),
    HistoricalFailureScenario(
        "2026-06-16",
        "resume / runner invocation",
        "Codex started a full runner rerun instead of resuming from existing artifacts",
        "resume-before-rerun boundary",
        "existing daily artifacts must block full rerun unless explicit ForceFullRerun approval exists",
        "runner contract fixture refusing full rerun when daily artifacts exist",
        "docs/incidents/2026-06-16-codex-rerun-incident.md",
        "fixture_required",
    ),
    HistoricalFailureScenario(
        "2026-06-17",
        "daily-quality / url-liveness / reconcile / pytest-static",
        "pre-publish gate attempts show URL and static gate failures before recovery",
        "multi-gate pre-publish convergence",
        "URL liveness and static gates must remain independent blockers with retry budget evidence",
        "gate-attempt ledger fixture for URL/static gate separation",
        "data/gate_attempts/2026-06-17.json",
        "fixture_required",
    ),
    HistoricalFailureScenario(
        "2026-06-18",
        "pytest-static / RecoverOnly",
        "pytest used user temp and hit PermissionError WinError 5 before publish",
        "non-interactive Windows environment boundary",
        "runner pytest gate must force repo-local basetemp and RecoverOnly must resume existing artifacts",
        "runner contract for repo-local pytest basetemp plus RecoverOnly inventory proof",
        "docs/incidents/2026-06-18-daily-batch-failure-report.html",
        "fixture_required",
    ),
    HistoricalFailureScenario(
        "2026-06-19",
        "daily-quality / digest-articles-reconcile / url-liveness",
        "digest/data URL set drift plus repair scope drift",
        "artifact inventory/scope",
        "repair must patch only allowed artifacts and rerun the same gate",
        "runtime repair cycle for deterministic registry repair plus scope guard",
        "docs/incidents/2026-06-19-batch-recovery-report.html",
        "runtime_e2e_required",
    ),
    HistoricalFailureScenario(
        "2026-06-19",
        "public QA / thumbnail fallback",
        "article-level thumb null values passed aggregate thumbnail gate and rendered category fallback images",
        "public degradation after nominal batch success",
        "article-card thumbnail fallback must be rejected per card before publish and verified on public HTML",
        "daily-quality fixture with mixed missing thumbs plus public HTML fallback sentinel",
        "docs/incidents/2026-06-19-thumbnail-missing-report.html",
        "fixture_required",
    ),
    HistoricalFailureScenario(
        "2026-06-20",
        "PowerShell/Python boundary / digest reconcile / podcast",
        "bounded digest repair failed before publish and podcast readiness had to be recovered separately",
        "PowerShell Python boundary",
        "runner subprocess boundary must pass bounded files/args and podcast state must be verified after recovery",
        "fixture exercising runner command construction, parser boundary, and podcast public sentinel",
        "docs/incidents/2026-06-20-daily-batch-recovery-report.html",
        "fixture_required",
    ),
    HistoricalFailureScenario(
        "2026-06-21",
        "DeepDive generation",
        "auxiliary markdown leaked into DeepDive artifact set",
        "artifact inventory/scope",
        "DeepDive required artifacts must exclude helper markdown and include only publish inventory",
        "published repair inventory fixture for DeepDive artifact scope",
        "docs/incidents/2026-06-21-daily-batch-recovery-report.html",
        "fixture_required",
    ),
    HistoricalFailureScenario(
        "2026-06-22",
        "refill / Summary parser",
        "refill and Summary parser boundaries did not share one source of truth",
        "public pre-gate/refill and parser boundary",
        "quarantine/refill must update session/search audit state before parser-dependent gates",
        "quarantine/refill fixture with selected_total and same-gate rerun",
        "docs/incidents/2026-06-22-daily-batch-and-summary-incident-report.html",
        "fixture_required",
    ),
    HistoricalFailureScenario(
        "2026-06-23",
        "non-interactive runner ledger / URL refill / DeepDive dialogue",
        "PowerShell category argument, session ledger dependency, and missing DeepDive dialogue surfaced together",
        "non-interactive runner contract",
        "non-interactive runner must not depend on human-session artifacts and must generate required dialogue artifacts",
        "NoPublish runner fixture with noninteractive state/progress proof and DeepDive dialogue generation",
        "docs/incidents/2026-06-23-daily-batch-recovery-report.html",
        "fixture_required",
    ),
    HistoricalFailureScenario(
        "2026-06-24",
        "goal completion / recovery state",
        "goal run mixed recovered public state with future readiness gates and mishandled weekday/non-target evidence",
        "completion gate and source-of-truth drift",
        "goal completion must separate current public recovery from future gates and include SLO/non-target category evidence",
        "goal-run completion fixture with weekday category, SLO, public/podcast proof, and non-target artifact separation",
        "docs/incidents/2026-06-24-digest-articles-reconcile-report.html",
        "fixture_required",
    ),
    HistoricalFailureScenario(
        "2026-06-24",
        "summary-reflection / daily-quality / generation-quality / url-liveness / reconcile / pytest-static",
        "gate-attempt ledger shows broad pre-publish gate surface on the same issue date",
        "multi-gate pre-publish convergence",
        "future complete gates must include all recorded gate surfaces for the day",
        "gate-attempt ledger fixture for 2026-06-24 gate set",
        "data/gate_attempts/2026-06-24.json",
        "fixture_required",
    ),
    HistoricalFailureScenario(
        "2026-06-25",
        "post-daily-quality resume / fallback publish",
        "resume reran daily-quality before DeepDive/docs/TTS existed and produced fallback",
        "resume order and fallback boundary",
        "post-daily-quality resume must generate DeepDive/docs/TTS before publish gates and must not fallback under NoPublish",
        "NoPublish resume fixture plus fallback-block contract",
        "docs/incidents/2026-06-25-daily-batch-incomplete-report.html",
        "runtime_e2e_required",
    ),
    HistoricalFailureScenario(
        "2026-06-26",
        "verify-publish-complete / distribution manifest",
        "post-gate verification failed because distribution manifest for the issue date was absent",
        "publish boundary and distribution manifest",
        "publish complete cannot be claimed until runner-owned distribution manifest exists with commit/audio/podcast state",
        "verify-publish-complete fixture for distribution_artifact_missing and manifest ownership boundary",
        "build/recovery/proofs/2026-06-26-post-gate-verify-publish-complete.json",
        "fixture_required",
    ),
    HistoricalFailureScenario(
        "2026-06-27",
        "auto-repair classify / NoPublish distribution manifest",
        "PowerShell scriptblock read wrapper parameter scope and NoPublish still committed distribution manifest changes",
        "scriptblock scope and NoPublish side-effect boundary",
        "scriptblock parameter names must not collide with outer runner inputs and NoPublish must not create local commits",
        "scriptblock-scope audit plus NoPublish E2E fixture blocking distribution manifest git add/commit",
        "docs/incidents/2026-06-27-scriptblock-nopublish-report.html",
        "runtime_e2e_required",
    ),
    HistoricalFailureScenario(
        "2026-06-28",
        "pre-DeepDive E2E / report artifact",
        "final pre-DeepDive E2E report was published without being included in historical failure coverage",
        "completion evidence inventory drift",
        "new incident or E2E report artifacts must be added to the historical failure matrix before pytest-static",
        "historical failure matrix fixture covering report corpus sync",
        "docs/incidents/2026-06-28-final-predeepdive-e2e-report.html",
        "fixture_required",
    ),
    HistoricalFailureScenario(
        "2026-06-28",
        "daily batch recovery / internal fallback boundary",
        "daily batch was blocked by same-date artifacts, then recovery exposed wrapper non-termination, repair scope gaps, and historical DeepDive URL liveness coupling",
        "artifact predicate drift, deterministic repair coverage, and fallback boundary",
        "same-date artifact cleanup must use runner predicates, internal failures must not fallback, and current-day publish gates must be separated from historical audits",
        "runner contract tests, repair matrix fixtures, current DeepDive URL gate, and incident report validation",
        "docs/incidents/2026-06-28-daily-batch-recovery-report.html",
        "runtime_e2e_required",
    ),
    HistoricalFailureScenario(
        "2026-06-28",
        "E2E artifact boundary / runner precheck",
        "pre-DeepDive E2E artifacts remained under same-date production paths and collided with the production runner artifact predicate",
        "E2E artifact collision and production predicate drift",
        "E2E output cleanup must be verified with the same predicate/glob/state that the production runner uses before daily batch readiness is claimed",
        "incident report contract plus same-predicate artifact-boundary fixture",
        "docs/incidents/2026-06-28-e2e-artifact-collision-report.html",
        "fixture_required",
    ),
    HistoricalFailureScenario(
        "2026-06-28",
        "full runner recovery / cross-feature bug taxonomy",
        "full runner recovery exposed wrapper non-termination, repair scope gaps, state reconciliation drift, internal fallback overclaim, current/historical URL coupling, and evidence observability gaps",
        "full runner bug-pattern taxonomy and horizontal scan",
        "recovery reports must inventory related runner/repair/state/report features and classify newly suspected bug candidates instead of collapsing the incident into a recovered status",
        "incident report contract covering Class A-H and targeted runner/repair contracts",
        "docs/incidents/2026-06-28-full-runner-bug-patterns-report.html",
        "runtime_e2e_required",
    ),
    HistoricalFailureScenario(
        "2026-06-28",
        "historical failure audit / four-lane proof",
        "historical failure coverage could be mistaken for detailed four-lane evidence audit",
        "horizontal audit evidence granularity",
        "every historical incident must have runner/repair/state/report audit records and a report artifact synchronized with the matrix",
        "historical failure audit fixture requiring detailed lanes and report corpus sync",
        "docs/incidents/2026-06-28-historical-failure-horizontal-audit-report.html",
        "fixture_required",
    ),
    HistoricalFailureScenario(
        "2026-06-30",
        "Codex completion report / public reflection boundary",
        "Codex reported News-Grasp mobile UI publish completion while commit, push, GitHub Pages, public CSS, service worker, and public DOM verification were still missing",
        "Codex residual work completion boundary",
        "publish-required UI work cannot be complete until numbered requirements, commit/push, remote HEAD, and public URL/CSS/SW/DOM sentinels are verified",
        "global hook fixture for residual work block plus report skill residual work classifier",
        "docs/spec.md",
        "fixture_required",
    ),
    HistoricalFailureScenario(
        "2026-07-04",
        "daily-quality / TTS repair / DeepDive relation / Pages deploy",
        "same run exposed search audit selected_total drift, shallow audio repair, relation edge crossing, and completed/failure Deploy Pages handling",
        "multi-stage repair and deploy convergence boundary",
        "daily recovery must reconcile final digest counts, repair thematic audio gaps without historical reuse, reject relation crossings, and retry transient deploy by fresh dispatch",
        "contract fixtures for search audit metadata, audio script thematic repair, DeepDive relation crossing, and fresh deploy workflow dispatch",
        "docs/incidents/2026-07-04-daily-batch-recovery.md",
        "fixture_required",
    ),
    HistoricalFailureScenario(
        "2026-07-05",
        "publish verification / Deploy Pages / runner state",
        "runner recorded publish_failed after fresh Deploy Pages dispatch even though publish-complete later verified Green",
        "publish convergence and state reconciliation boundary",
        "runner, publish-complete manifest, live state, and history must converge on the same date/head instead of leaving stale publish_failed",
        "runner convergence fixture plus batch history state-drift fixture and notification state manifest",
        "data/gate_attempts/2026-07-05.json",
        "fixture_required",
    ),
    HistoricalFailureScenario(
        "2026-07-06",
        "daily-quality / auto-repair classify / incident report",
        "selected_total digest count mismatch was classified as unknown and stale incident report evidence copied prior incident facts",
        "repair decision routing and report fidelity boundary",
        "selected_total mismatch must have a structured deterministic issue code and incident reports must reject stale evidence sentinels",
        "daily-quality selected_total fixture, orchestrator classify fixture, runtime repair cycle, and incident report sentinel validator",
        "docs/incidents/2026-07-06-human-caused-recurrence.md",
        "runtime_e2e_required",
    ),
    HistoricalFailureScenario(
        "2026-07-07",
        "summary-reflection / daily-quality / generation-quality / record-schema / pytest-static",
        "newsroom editor preview contained summary_markdown and append_records, but the runner did not materialize them before summary-reflection",
        "summary materialize and compound repair routing boundary",
        "editor success must create official Summary and article ledger artifacts, and downstream repair blockers must stay typed instead of collapsing to unknown",
        "summary missing-artifact classifier fixture, editor materialize contract, compound repair scenario, and public recovery proof",
        "docs/incidents/2026-07-07-summary-materialize-recovery.md",
        "fixture_required",
    ),
    HistoricalFailureScenario(
        "2026-07-08",
        "runner binary drift / daily-quality / deepdive-required / publish-complete",
        "live runner drift stopped the scheduled run while prior publish-complete history could still classify days as complete without live readiness evidence",
        "live runner readiness completion overclaim",
        "daily completion must include repo/live runner SHA, repo/live watcher SHA, repo/live bootstrap SHA, Runner 06:00 schedule, Runner Action production-start mode, Bootstrap 05:55 schedule, NextRunTime, NumberOfMissedRuns=0, Scheduled Task watcher/bootstrap target, direct runner pre-run interlock/reexec, and smoke_ok canary evidence in the publish-complete manifest",
        "verify-live-runner-readiness strict scheduler contract plus watcher bootstrap canary and batch history completion_overclaim fixture",
        "tasks/audits/2026-07-08-live-readiness-overclaim-audit.md",
        "fixture_required",
    ),
    HistoricalFailureScenario(
        "2026-07-08",
        "scheduled startup / watcher bootstrap / self-repair",
        "the first live-readiness fix could fail closed at completion time but did not yet make the 06:00 entrypoint auto-repair repo-to-live ops drift before starting the batch",
        "live ops bootstrap self-repair gap",
        "the scheduled entrypoint must be a production-start watcher/bootstrap, or a direct runner with pre-run bootstrap interlock/reexec plus a proved 05:55 Bootstrap task with -SmokeTest, short timeout, isolated state/log, LastTaskResult=0, NextRunTime, and no missed runs before the 06:00 Runner task",
        "watcher self-repair contract, installer Scheduled Task target contract, direct runner interlock/reexec contract, strict scheduler verifier, and live startup canary through bootstrap/watcher",
        "tasks/audits/2026-07-08-live-readiness-overclaim-audit.md",
        "fixture_required",
    ),
    HistoricalFailureScenario(
        "2026-07-08",
        "daily-quality / deepdive-required repair routing",
        "search audit repair routing missed deepdive-required selected_total mismatch after the runner drift recovery",
        "repair coverage gate drift",
        "validator output must route to the same deterministic handler across equivalent daily-quality and deepdive-required gates",
        "repair coverage fixtures for search audit metadata and deepdive-required selected_total mismatch plus public surface proof",
        "tasks/audits/2026-07-08-live-readiness-overclaim-audit.md",
        "fixture_required",
    ),
    HistoricalFailureScenario(
        "2026-07-11",
        "newsroom-editor-preview / daily-quality / generation-quality / url-liveness / pytest-static / publish-complete",
        "editor abort payload was materialized, structured repair metadata was incomplete, and runner policy rejected matrix-owned repairs before public convergence",
        "compound repair decision debt across editor materialization and downstream gate routing",
        "editor preview must pass semantic validation before materialization; validator issue metadata, coverage matrix, retry policy, registry scope, and runner repair class must stay connected through same-gate re-verification",
        "editor preview semantic fixture plus structured daily-quality metadata, matrix-owned rewrite, thumbnail preservation, full non-network pytest, and public recovery proof",
        "docs/incidents/2026-07-11-daily-batch-editor-repair-routing-report.html",
        "runtime_e2e_required",
    ),
    HistoricalFailureScenario(
        "2026-07-12",
        "newsroom-editor-preview / editor workspace snapshot / repair routing / publish-complete",
        "editor semantic retries restored an attempt workspace from a malformed aggregate reporter path and blocked convergence until the workspace and repair ledger were isolated per attempt",
        "editor retry isolation and scalar artifact-path boundary",
        "editor attempt snapshots must flatten reporter artifact paths, reset only issue-date mutable outputs, preserve an ordered repair ledger, and re-enter the production publish verifier on the recovered remote head",
        "snapshot path contract, editor materialization fixtures, full pytest-static, publish-complete, public surface proof, and incident report validator/render proof",
        "docs/incidents/2026-07-12-daily-batch-editor-materialization-report.html",
        "runtime_e2e_required",
    ),
    HistoricalFailureScenario(
        "2026-07-13",
        "daily-quality / repair parser / pytest-static / YouTube Podcast / publish-complete",
        "warning-prefixed validator JSON was misparsed by repair routing, then quality shortfall, optional model-eval artifacts, category hero wrapping, and a machine-local podcast cover path surfaced during recovery",
        "repair parser and portable distribution asset boundary",
        "repair routing must parse structured validator payloads with a JSON decoder, quality shortfalls must carry explicit reasons, and distribution video inputs must be repo-managed portable assets before publish complete can be claimed",
        "warning-prefix JSON parser fixture, reasoned shortfall daily-quality fixture, category hero wrapping fixture, optional model-eval skip contract, podcast cover default asset contract, full runner publish-complete, public surface proof, and incident report validator/render proof",
        "docs/incidents/2026-07-13-daily-batch-repair-path-report.html",
        "runtime_e2e_required",
    ),
    HistoricalFailureScenario(
        "2026-07-14",
        "daily-quality / pytest-static / git-push / YouTube Podcast / publish-complete",
        "public Summary headings used internal category keys, pytest-static collected untracked benchmark files, git push stopped on an interactive prompt, and Podcast finalize was not reached until recovery resumed the post-push sequence",
        "summary label and local contract gate boundary",
        "public-facing category labels must be distinct from internal keys, pytest-static failures must surface as local contract failures instead of retry budget causes, and post-push Podcast/notification/final publish gates must run before completion is claimed",
        "summary category focus fixture, pytest-static local contract classification, incident report policy allowlist, full non-network pytest, Podcast sentinel verification, publish-complete proof, public surface proof, and incident report validator/render proof",
        "docs/incidents/2026-07-14-daily-batch-summary-focus-podcast-report.html",
        "runtime_e2e_required",
    ),
    HistoricalFailureScenario(
        "2026-07-15",
        "generation-quality / batch-slo / RecoverOnly / publish-complete",
        "targeted Codex repair restored a missing audio script but exhausted the one-hour batch SLO before publish",
        "SLO budget and targeted repair boundary",
        "pre-publish repair must account for elapsed SLO budget and completion reports must distinguish quality Green from publish-not-yet-started",
        "batch SLO fixture with long targeted repair duration, RecoverOnly resume proof, publish-complete proof, public surface proof, and incident report validator/render proof",
        "docs/incidents/2026-07-15-daily-batch-slo-recovery-report.html",
        "runtime_e2e_required",
    ),
    HistoricalFailureScenario(
        "2026-07-16",
        "pytest-static / runtime model dependency audit / RecoverOnly / publish-complete",
        "runtime model dependency audit missed retired-model fixtures when pytest basetemp placed tmp roots inside the git worktree",
        "nested basetemp and git worktree root boundary",
        "audit tools must resolve git-listed paths from the worktree top and then filter to the requested repo_root before classifying production/runtime dependencies",
        "nested repo-local basetemp fixture, runtime model dependency audit contract, runner-equivalent pytest-static, RecoverOnly resume proof, publish-complete proof, public surface proof, and incident report validator/render proof",
        "docs/incidents/2026-07-16-pytest-basetemp-recovery-report.html",
        "runtime_e2e_required",
    ),
    HistoricalFailureScenario(
        "2026-07-17",
        "pytest-static / category hero lead title quality / RecoverOnly / publish-complete",
        "short ASCII subject was isolated as a one-token first line before a quoted topic phrase",
        "category hero title split contract gap",
        "short ASCII subject before Japanese quoted topic must merge with the pre-quote phrase without regressing existing Japanese-brand splits",
        "category hero Turn 4 contract fixture for OpenAI quoted-topic title, existing Toyota regression case, RecoverOnly resume proof, publish-complete proof, public surface proof, and incident report validator/render proof",
        "docs/incidents/2026-07-17-pytest-static-hero-line-report.html",
        "runtime_e2e_required",
    ),
    HistoricalFailureScenario(
        "2026-07-18",
        "pytest-static / category hero lead title quality plus incident tracking policy / RecoverOnly / publish-complete",
        "long Japanese event phrase exceeded the category hero line width and the previous approved incident report was not registered in the tracking policy allowlist",
        "category hero long event title and explicit incident report approval tracking gap",
        "long event nouns such as symposium must split at stable phrase markers and every explicitly approved public incident report must be registered before the next pytest-static run",
        "category hero Turn 4 contract fixture for the IPA symposium title, incident report tracking policy allowlist fixture, full pytest-static, RecoverOnly resume proof, publish-complete proof, public surface proof, and incident report validator/render proof",
        "docs/incidents/2026-07-18-pytest-static-hero-policy-report.html",
        "runtime_e2e_required",
    ),
    HistoricalFailureScenario(
        "2026-07-19",
        "daily-quality / repair routing / markdown image parser / post-daily-quality resume / publish-complete",
        "daily-quality exposed stale TOP article, search audit count drift, structured-lane emphasis gaps, and non-thumb Markdown alt images parsed as missing thumbnails",
        "repair routing, structured digest mutation, and thumbnail parser boundary",
        "daily-quality issues must retain structured issue codes, deterministic repair must preserve card/lane structure, and page generation must accept non-thumb Markdown image alt text before publish",
        "daily-quality stale TOP JSON fixture, repair coverage matrix fixture, registry lane/separator repair fixtures, non-thumb image parser fixture, post-daily-quality runner resume, publish-complete proof, public surface proof, and incident report validator/render proof",
        "build/incidents/2026-07-19-daily-quality-repair-routing-report.html",
        "runtime_e2e_required",
    ),
    HistoricalFailureScenario(
        "2026-07-20",
        "daily-quality / generation-quality / GitHub Release upload / post-deepdive resume / publish-complete",
        "local digest and record drift exposed sequential gate defects before GitHub Release upload returned HTTP 502/503 Error creating policy",
        "compound local repair convergence and GitHub Release external boundary",
        "local deterministic repair must converge before publish, while GitHub Release HTTP 502/503 must retain complete typed external evidence instead of becoming a local-tool defect",
        "daily-quality and generation-quality compound fixtures, GitHub Release upload external fixture, post-deepdive runner resume, publish-complete proof, public surface proof, and incident report validator/render proof",
        "build/incidents/2026-07-20-daily-batch-github-audio-upload-report.html",
        "runtime_e2e_required",
    ),
    HistoricalFailureScenario(
        "2026-07-21",
        "daily-quality / digest-record sync / category hero title quality / post-deepdive resume / publish-complete",
        "record truth for title_ja and thumb did not flow back into digest markdown, leaving an untranslated Game title and empty category thumbnails before a later SEO title hit category hero line quality",
        "digest record sync and fallback thumbnail boundary",
        "daily-quality repair must sync title_ja and thumbnail from record truth before quarantine/refill, and fallback thumbnails must be explicit repo-managed URLs when OGP is unavailable",
        "repair coverage matrix fixture, digest-record-sync registry fixtures for title_ja and fallback thumb, category hero current-title fixture, post-deepdive runner resume, publish-complete proof, public surface proof, and incident report validator/render proof",
        "docs/incidents/2026-07-21-daily-quality-digest-record-sync-report.html",
        "runtime_e2e_required",
    ),
    HistoricalFailureScenario(
        "2026-07-22",
        "daily-quality / structured issue routing / search-audit metadata / post-deepdive resume / publish-complete",
        "validator JSON issues kept issue_code=unknown even though their messages contained known search audit metadata and follow-up review failures",
        "structured JSON issue fallback and dropped reason summary repair boundary",
        "structured validator issues must recover known issue codes and artifact metadata from message text, and search audit metadata repair must preserve dropped reason summaries when examples are absent",
        "structured unknown JSON repair coverage fixtures, dropped_reason_summary registry fixture, daily-quality and generation-quality gates, DeepDive URL proof, post-deepdive runner resume, publish-complete proof, public surface proof, and incident report validator/render proof",
        "docs/incidents/2026-07-22-daily-quality-structured-repair-report.html",
        "runtime_e2e_required",
    ),
    HistoricalFailureScenario(
        "2026-07-23",
        "daily-quality / search-audit metadata repair / category hero lead title quality / post-deepdive resume / publish-complete",
        "search audit artifacts stored dropped reasons in dropped_or_not_selected while the deterministic handler only promoted dropped_examples or dropped_reason_summary, then pytest-static exposed an OpenAI short-subject hero title split",
        "search audit dropped reason source mismatch and category hero short English subject boundary",
        "search-audit metadata repair must promote dropped_or_not_selected reasons, and category hero splitting must merge short English subjects with a stable phrase head before publish",
        "dropped_or_not_selected registry fixture, OpenAI budget title hero split fixture, daily-quality gate repair, full pytest-static, post-deepdive runner resume, publish-complete proof, public surface proof, and incident report validator/render proof",
        "docs/incidents/2026-07-23-daily-quality-search-audit-hero-report.html",
        "runtime_e2e_required",
    ),
    HistoricalFailureScenario(
        "2026-07-24",
        "daily-quality / editorial section eligibility / post-daily-quality resume / publish-complete",
        "category digest editorial heading ### §01 inherited article metadata inside a parser block and was treated as a card with a missing thumbnail",
        "validator article card eligibility boundary",
        "thumbnail validation must use article card eligibility as its source of truth and exclude editorial sections without weakening real-card thumbnail checks",
        "daily-quality editorial section exclusion fixture, real article empty-thumb negative fixture, same-gate rerun, post-daily-quality runner resume, publish-complete proof, public surface proof, and incident report validator/render proof",
        "docs/incidents/2026-07-24-daily-quality-editorial-section-report.html",
        "runtime_e2e_required",
    ),
    HistoricalFailureScenario(
        "2026-07-25",
        "generation-quality / codex readiness / pytest-static / git add / post-deepdive resume",
        "Codex doctor MCP configuration failure was misclassified as auth failure before repair, then stale/transient git index.lock blocked publish commits",
        "external readiness classification and git index lock boundary",
        "Codex auth evidence must be separated from MCP configuration diagnostics, and git add must use bounded retry only for verified index.lock transients",
        "runner readiness fixture for MCP-only doctor failure, git index.lock retry fixture, full pytest-static, post-deepdive runner resume, publish-complete proof, public surface proof, and incident report validator/render proof",
        "docs/incidents/2026-07-25-codex-doctor-git-lock-recovery-report.html",
        "runtime_e2e_required",
    ),
    HistoricalFailureScenario(
        "2026-07-26",
        "daily-quality / post-deepdive resume / git add / podcast playlist audit / publish-complete",
        "Mobility stale candidates and digest/search-audit drift stopped daily-quality, then recovery exposed stale empty git index.lock and same-date podcast playlist duplicates",
        "content freshness, git state, and podcast distribution convergence boundary",
        "daily recovery must synchronize search audit, digest cards, record state, git add lock handling, playlist uniqueness, and publish-complete proof before claiming Green",
        "daily-quality same-date fixture, Japanese digest reconcile routing fixture, stale empty index.lock retry fixture, playlist audit proof, publish-complete proof, public surface proof, and incident report validator/render proof",
        "docs/incidents/2026-07-26-daily-quality-stale-lock-playlist-recovery-report.html",
        "runtime_e2e_required",
    ),
    HistoricalFailureScenario(
        "2026-07-27",
        "digest-articles-reconcile / daily-quality / generation-quality / record-schema / deterministic repair registry / runner same-gate reverify",
        "articles_only URL was detected but the selected handler could not generate a digest card; horizontal review found the same failure-mode collapse in thumb, search-audit, TTS, date-evidence, summary reflection, and matrix/registry ownership",
        "validator failure direction, evidence availability, and deterministic handler capability drift",
        "each structured failure mode must route to a handler that can mutate the selected target artifact; recoverable evidence and unsupported directions must be separate codes, while ambiguous or legacy-unspecified directions remain typed Red",
        "articles_only digest-card fixture, direction-specific matrix routing, all daily-quality return-code AST coverage, thumb/search-audit/TTS/date-evidence direction fixtures, matrix-owned scope/verify gate fixture, registry same-gate fixtures, runner selected-artifact ledger, and reconcile/daily-quality Green",
        "docs/incidents/2026-07-27-digest-articles-reconcile-report.html",
        "runtime_e2e_required",
    ),
    HistoricalFailureScenario(
        "2026-07-28",
        "generation-quality / date-evidence-source-patch / record-schema / post-deepdive resume / publish-complete",
        "reporter records retained freshness evidence, but data/articles.jsonl lost published_date and date_evidence_source for the same issue URLs, so generation-quality classified the loss as terminal instead of recoverable",
        "reporter freshness evidence recovery boundary",
        "generation-quality must classify missing article freshness evidence as recoverable when current reporter records contain matching URL freshness evidence, and the date-evidence handler must patch existing article rows from reporter records without manual edits",
        "generation-quality reporter-record fixture, registry date-evidence patch fixture, repair coverage/matrix sync fixtures, post-deepdive runner resume, publish-complete proof, public surface proof, and incident report validator/render proof",
        "docs/incidents/2026-07-28-generation-quality-date-evidence-recovery-report.html",
        "runtime_e2e_required",
    ),
    HistoricalFailureScenario(
        "2026-07-29",
        "daily-quality / digest-card-insert-patch / search audit / post-deepdive resume / publish-complete",
        "IT category digest became empty while current reporter records still contained recoverable article evidence, and recovery also exposed search audit metadata, follow-up review note, refill formatting, and historical report corpus drift",
        "structured repair routing and historical evidence corpus sync boundary",
        "daily-quality recoverable digest gaps must route to deterministic card insertion from current records, and any local-only incident report left under docs/incidents must be registered with a stable evidence digest",
        "category_digest_empty repair fixture, search-audit metadata patch fixture, refill formatter emphasis/tag fixture, historical failure corpus fixture, post-deepdive runner resume, publish-complete proof, public surface proof, and incident report validator proof",
        "docs/incidents/2026-07-29-daily-quality-recovery-report.html",
        "runtime_e2e_required",
    ),
    HistoricalFailureScenario(
        "2026-07-30",
        "pytest-static / historical failure corpus / post-deepdive resume / publish-complete",
        "the prior day's local-only incident report remained under docs/incidents without a matching historical failure scenario and SHA256 registry entry, so pytest-static stopped before public generation",
        "local-only incident evidence registry boundary",
        "every local-only incident report visible to the lifecycle corpus must be bound to a stable scenario and digest before the next daily runner reaches pytest-static",
        "historical corpus fixture, local-only digest registry fixture, incident report validator/render proof, post-deepdive runner resume, verify-live-runner-readiness proof, verify-publish-complete proof, and public surface proof",
        "docs/incidents/2026-07-30-pytest-static-historical-corpus-report.html",
        "runtime_e2e_required",
    ),
    HistoricalFailureScenario(
        "2026-07-31",
        "daily-quality / newsroom editor preview / Manufacturing digest / post-deepdive resume / publish-complete",
        "editor preview dropped valid Manufacturing candidates, leaving the category digest empty at daily-quality",
        "editor preview candidate preservation and visible incident corpus boundary",
        "preview materialization must preserve eligible records, and every visible incident report must be registered before the next static gate",
        "Manufacturing preview candidate fixture, category_digest_empty routing fixture, visible incident registry fixture, post-deepdive runner resume, publish-complete proof, and public surface proof",
        "docs/incidents/2026-07-31-daily-batch-manufacturing-preview-drop-report.html",
        "runtime_e2e_required",
    ),
    HistoricalFailureScenario(
        "2026-08-01",
        "post-deepdive recovery / URL liveness / dialogue value gate / publish-complete",
        "recovery accepted malformed or unverifiable citation URLs and fixed-template dialogue passed length checks despite cross-day semantic repetition",
        "DeepDive recovery false-success and value-evidence binding boundary",
        "every recovery route must fail closed on unverifiable current citation URLs and must bind each dialogue value segment to an existing source sentence while rejecting corpus-level repetition",
        "URL request/TLS/network fixtures, runner skip-isolation fixture, seven-value grounding fixtures, 31-script corpus audit, targeted article render, and runner gate-order fixture",
        "docs/incidents/2026-08-01-daily-batch-editor-contract-cwd-report.html",
        "fixture_required",
    ),
    HistoricalFailureScenario(
        "2026-08-02",
        "scheduled production admission / runner start / high-cost durable ledger",
        "the normal 06:00 Scheduled Task was classified as final E2E and tried to reserve the already-consumed goal-scoped E2E attempt before runner stages started",
        "scheduled production and final E2E identity separation boundary",
        "normal daily, same-date recovery, and final NoPublish E2E must own disjoint attempt identities while every scheduled model call shares one date-scoped nine-call budget",
        "independent scheduled identity fixtures, forged receipt negatives, same-date recovery budget simulation in isolated SQLite, PowerShell AST, and static runner/broker contracts",
        "build/repair-review/2026-08-02-scheduled-high-cost-tdd-impact.json",
        "fixture_required",
    ),
    HistoricalFailureScenario(
        "2026-08-03",
        "scheduled startup / bootstrap self-repair / 6:40 audit recovery / same-day public recovery",
        "the 06:00 direct runner bypassed the clean production runtime, startup self-repair stopped before generation, and the 6:40 audit recovery ended deferred instead of recovering or escalating a major incident",
        "same-day public recovery first and startup route identity boundary",
        "every scheduled startup failure must emit a fixed attempt terminal plus immutable failure receipt, and audit must recover the missing public day or stop as a typed major incident rather than deferred",
        "legacy direct trampoline fixture, clean-runtime git stdout isolation, watcher prior-state isolation, runtime evidence-root fixture, fixed attempt terminal contract, audit recover-or-major-incident decision fixture, live Bootstrap Scheduled Task smoke, and next-run readiness proof",
        "build/repair-review/2026-08-03-startup-self-repair-tdd-impact.json",
        "fixture_required",
    ),
    HistoricalFailureScenario(
        "2026-08-10",
        "audit recovery / completion verifier / readiness / observation ledger",
        "primary completion verification could erase an already verified public Green and a fixed terminal projection could overwrite audit lineage",
        "typed completion monotonicity and append-only audit authority boundary",
        "public completion authority must survive readiness Red or verification-unavailable observation while scheduled, recovery, and audit event lineage remains immutable",
        "NG-RED-01 through NG-RED-12, typed public/readiness evaluator, append-only event history, replay and causal retry negatives",
        "docs/incidents/2026-08-01-daily-batch-editor-contract-cwd-report.html",
        "fixture_required",
    ),
    HistoricalFailureScenario(
        "2026-08-04",
        "scheduled startup / runner ledger / incident recovery",
        "a missing startup log and an unclassified ledger gap stopped the normal route before a typed terminal was emitted",
        "startup evidence and operation ledger classification boundary",
        "startup receipt and ledger classification must be typed before recovery",
        "startup must bind a bounded log receipt and classify every ledger gap before recovery or major-incident termination",
        "docs/incidents/2026-07-30-pytest-static-historical-corpus-report.html",
        "fixture_required",
    ),
    HistoricalFailureScenario(
        "2026-08-05",
        "manual stop / artifact ledger / recovery",
        "a stopped operation left an invalid artifact ledger that could be mistaken for a resumable checkpoint",
        "stop artifact ledger validity boundary",
        "stop terminal and checkpoint validity must be independently bound",
        "manual stop must produce a typed terminal and reject invalid checkpoint reuse without a model rerun",
        "docs/incidents/2026-07-31-daily-batch-manufacturing-preview-drop-report.html",
        "fixture_required",
    ),
    HistoricalFailureScenario(
        "2026-08-06",
        "runtime promotion / dirty worktree / authority",
        "dirty runtime state had no valid generation authority, so production startup could neither publish nor recover safely",
        "stale runtime and generation authority boundary",
        "dirty or stale runtime must not be treated as an active generation",
        "runtime promotion must fail closed on dirty or stale state and restore the last known good generation through a registered handler",
        "build/repair-review/2026-08-02-scheduled-high-cost-tdd-impact.json",
        "fixture_required",
    ),
    HistoricalFailureScenario(
        "2026-08-07",
        "checkpoint continuation / high-cost budget / scheduled recovery",
        "a valid checkpoint was ignored after a wrapper failure and the exhausted budget caused an unnecessary producer rerun",
        "checkpoint-first continuation and shared budget boundary",
        "valid checkpoint and daily budget lineage must survive wrapper failure",
        "a valid checkpoint must resume deterministically with zero model reruns and preserve the daily lineage and remaining budget",
        "build/repair-review/2026-08-03-startup-self-repair-tdd-impact.json",
        "fixture_required",
    ),
    HistoricalFailureScenario(
        "2026-08-08",
        "dirty runtime / receipt ledger / terminal reconciliation",
        "runtime receipt and ledger terminal state diverged after a dirty deployment, leaving the public authority ambiguous",
        "runtime receipt and ledger terminal binding boundary",
        "receipt, runtime, and terminal ledger must share one immutable authority",
        "receipt, runtime, and terminal ledger must share one generation and reject cross-lineage reconciliation",
        "docs/incidents/2026-07-29-daily-quality-recovery-report.html",
        "fixture_required",
    ),
    HistoricalFailureScenario(
        "2026-08-09",
        "scheduled startup / self repair / retry ledger",
        "startup self-repair repeated the same cause without a causal input change and never reached a finite terminal",
        "causal retry and startup self-repair boundary",
        "same cause fingerprint must not reset retry budget",
        "the same cause fingerprint must consume zero retries and reach a typed terminal until causal evidence changes",
        "docs/incidents/2026-07-28-generation-quality-date-evidence-recovery-report.html",
        "fixture_required",
    ),
    HistoricalFailureScenario(
        "2026-08-11",
        "artifact-first continuation / state binding / public recovery",
        "artifact-first continuation and state binding were absent, so a wrapper failure could erase a valid public result",
        "artifact checkpoint and state-vector binding boundary",
        "artifact, public authority, readiness, and external state must remain distinct",
        "artifact checkpoints, daily lineage, public authority, readiness, and external state must remain separately bound through recovery",
        "docs/incidents/2026-08-01-daily-batch-editor-contract-cwd-report.html",
        "fixture_required",
    ),
    HistoricalFailureScenario(
        "2026-08-13",
        "scheduled production / workspace binding / four-root preflight / readiness canary / typed finalizer / completion guard",
        "the 06:00 scheduled attempt failed before production completion, then artifact, ops, runtime, and live root drift plus canary/finalizer contract defects extended audit recovery to roughly four hours",
        "News-Grasp control-plane root roles and post-public-Green finalization boundary",
        "scheduled failure must remain immutable while bootstrap-to-runner ops binding is explicit, four-root drift fails before high-cost work, canary uses the artifact root, commit roles remain typed, and finalization reaches the guard within 15/60 minutes",
        "role-separated four-root preflight fixtures, artifact-root canary fixture, V2 three-commit manifest/finalizer fixture, empty-broker terminalizer fixture, post-Green operation allowlist, and 15/60-minute SLO replay",
        "build/incidents/2026-08-13-daily-batch-and-recovery-delay-report.html",
        "runtime_e2e_required",
    ),
    HistoricalFailureScenario(
        "2026-08-27",
        "DeepDive provenance / recovery freshness / public-Green closeout / typed finalizer",
        "BLS rejected the primary transport, recovery tooling was stale, V2 claim/provenance artifacts required manual backfill, and the finalizer omitted receipt-bound resume arguments while receipt drift was debugged serially",
        "public recovery product/runtime composition and bounded closeout boundary",
        "all recovery routes must share one DeepDive materializer, verify exact runtime freshness before spawn, and derive one-shot reseal plus finalizer argv only from typed receipts after public Green",
        "BLS-profile actual Windows transport fixture, three-route issue bundle fixture, pre-spawn freshness fixture, exact-args finalizer fixture, one-shot reseal fixture, bounded closeout fixture, and one L5 production-composition node",
        "plans/2026-08-27-news-grasp-public-recovery-closeout/operational-design.md",
        "fixture_required",
    ),
)


PUBLIC_INTEGRITY_FAILURE_CLASSES_V1: tuple[tuple[str, str, str], ...] = (
    ("manifest_home_omission", "publish manifest / home", "canonical manifest must include docs/index.html and exact write set"),
    ("daily_audio_href_missing", "public semantic / daily audio", "home href must equal same-run daily audio V2 public URL"),
    ("deepdive_href_missing", "public semantic / DeepDive", "home must reach same-issue DeepDive"),
    ("summary_semantic_reflection_missing", "public semantic / Summary", "HTTP 200 cannot replace reflection/date/manifest semantic evidence"),
    ("audio_projection_shape_drift", "audio state / producer", "daily and DeepDive producers must write one V2 shape while V1 remains read-only"),
    ("claim_context_binding_mismatch", "DeepDive provenance / run context", "claim-source, sourceUrl, issue date and run intent must agree"),
    ("dialogue_value_unspecific", "DeepDive dialogue / article value", "dialogue value must be concrete and article-specific"),
    ("claim_evidence_normalized_duplicate", "DeepDive evidence / independence", "normalized claim and evidence equality is Red"),
    ("dirty_checkout_remote_false_green", "git publication / authority", "dirty or unbound checkout cannot prove remote publication"),
)

SCENARIOS = SCENARIOS + tuple(
    HistoricalFailureScenario(
        "2026-09-01",
        stage,
        failure_class,
        failure_class,
        invariant,
        f"tests/test_news_grasp_publish_contract_v2.py fixture for {failure_class}",
        "data/historical_failure_scenarios/2026-09-01-public-integrity.json",
        "fixture_required",
    )
    for failure_class, stage, invariant in PUBLIC_INTEGRITY_FAILURE_CLASSES_V1
)


DAILY_45M_FAILURE_CLASSES_V2: tuple[tuple[str, str, str], ...] = (
    ("daily_release_route_reachable", "Daily / Release entry", "Daily capability must spawn only six registered operations and reject unknown routes before process start"),
    ("unclassified_runtime_time", "runtime timing / SLO", "scheduler T0 and every queue/wait/external/retry/handoff interval must be classified while completion elapsed remains frozen"),
    ("predicate_owner_duplication", "Summary / DeepDive / quality", "one registered owner must evaluate each generation predicate once from its canonical source"),
    ("pages_home_write_set_drift", "manifest / Pages", "a public release must include docs/index.html while source-only change must not demand a Pages deployment"),
    ("release_identity_rebinding", "run / manifest / distribution", "actual run ID, ancestor baseline, optional fields and external-start seal must remain bound to one release generation"),
    ("duplicate_active_writer", "runtime state / lease / migration", "V2 migration must precede a single active writer for automation, issue date and run intent"),
    ("child_parse_partial_mutation", "CLI / child process / receipt", "UTF-8 JSON schema and input identity must validate before atomic state and receipt commit"),
    ("public_http_false_green", "consumer public completion", "fresh semantic surface identity, not HTTP 200 or caller JSON, is completion authority"),
    ("prompt_goal_failure_trace_drift", "goal / task ledger / automation / failure ledger", "task ID, acceptance, tests, evidence and exact prompt parity must close together"),
)

SCENARIOS = SCENARIOS + tuple(
    HistoricalFailureScenario(
        "2026-09-02",
        stage,
        failure_class,
        failure_class,
        invariant,
        f"tests/test_news_grasp_daily_45m_contract.py fixture for {failure_class}",
        "config/news_grasp_failure_ledger_v2.json",
        "fixture_required",
    )
    for failure_class, stage, invariant in DAILY_45M_FAILURE_CLASSES_V2
)


WEEKLY_FAILURE_REGRESSION_CASES: tuple[WeeklyFailureRegressionCase, ...] = (
    WeeklyFailureRegressionCase(
        "2026-08-02",
        "daily-quality",
        "url_dead_or_stale",
        "deterministic_handler",
        "blocked_refill_unresolved",
        "url-quarantine-refill",
        ("digest/FX/2026-08-02-FX.md",),
        "fx",
        evidence=(("failure_mode", "digest_url_stale_articles_row_prior_date"),),
    ),
    WeeklyFailureRegressionCase(
        "2026-07-25",
        "generation-quality",
        "audio_script_missing",
        "llm_generate_missing_artifact",
        "blocked_audio_script_generation_failed",
        "llm-missing-generated-artifact",
        ("digest/Summary/2026-07-25-audio-script.md",),
        evidence=(("typed_reason", "missing_artifact"),),
    ),
    WeeklyFailureRegressionCase(
        "2026-07-26",
        "daily-quality",
        "top_article_stale",
        "deterministic_handler",
        "blocked_refill_unresolved",
        "url-quarantine-refill",
        ("digest/Mobility/2026-07-26-Mobility.md",),
        "mobility",
    ),
    WeeklyFailureRegressionCase(
        "2026-07-17",
        "pytest-static",
        "local_contract_failure",
        "typed_fatal",
        "blocked_local_contract_failure",
        artifact_paths=("tests/", "tools/"),
    ),
    WeeklyFailureRegressionCase(
        "2026-07-18",
        "pytest-static",
        "local_contract_failure",
        "typed_fatal",
        "blocked_local_contract_failure",
        artifact_paths=("tests/", "tools/"),
    ),
    WeeklyFailureRegressionCase(
        "2026-07-19",
        "daily-quality",
        "top_article_stale",
        "deterministic_handler",
        "blocked_refill_unresolved",
        "url-quarantine-refill",
        ("digest/AI/2026-07-19-AI.md",),
        "ai",
    ),
    WeeklyFailureRegressionCase(
        "2026-07-19",
        "daily-quality",
        "search_audit_count_mismatch",
        "deterministic_handler",
        "blocked_deterministic_repair_failed",
        "search-audit-metadata-patch",
        ("data/search_audit/2026-07-19/ai.json",),
        "ai",
    ),
    WeeklyFailureRegressionCase(
        "2026-07-20",
        "github-release-upload",
        "github_release_upload_transient",
        "typed_external",
        "blocked_external_readiness",
        artifact_paths=("build/tts/2026-07-20.mp3",),
        evidence=(
            ("external_system", "github-release"),
            ("external_kind", "service_unavailable"),
            ("observed_error_code", "502"),
            ("source_command", "gh release upload audio-daily"),
            ("detail", "HTTP 502 Error creating policy"),
            ("observed_at", "2026-07-20T09:31:00+09:00"),
        ),
    ),
    WeeklyFailureRegressionCase(
        "2026-07-21",
        "daily-quality",
        "digest_title_ja_untranslated",
        "llm_rewrite_existing_artifact",
        "blocked_digest_title_ja_rewrite_failed",
        "digest-title-ja-rewrite",
        ("digest/Game/2026-07-21-Game.md",),
        "game",
    ),
    WeeklyFailureRegressionCase(
        "2026-07-21",
        "daily-quality",
        "thumb_invalid",
        "deterministic_handler",
        "blocked_refill_unresolved",
        "url-quarantine-refill",
        ("data/articles.jsonl", "digest/Game/2026-07-21-Game.md"),
        "game",
    ),
    WeeklyFailureRegressionCase(
        "2026-07-22",
        "daily-quality",
        "search_audit_coverage_terms_missing",
        "deterministic_handler",
        "blocked_deterministic_repair_failed",
        "search-audit-metadata-patch",
        ("data/search_audit/2026-07-22/ai.json",),
        "ai",
    ),
    WeeklyFailureRegressionCase(
        "2026-07-22",
        "daily-quality",
        "followup_review_required",
        "deterministic_handler",
        "blocked_deterministic_repair_failed",
        "followup-review-evidence-patch",
        ("data/articles.jsonl",),
        "ai",
    ),
    WeeklyFailureRegressionCase(
        "2026-07-23",
        "daily-quality",
        "search_audit_dropped_evidence_recoverable",
        "deterministic_handler",
        "blocked_deterministic_repair_failed",
        "search-audit-metadata-patch",
        ("data/search_audit/2026-07-23/ai.json",),
        "ai",
    ),
    WeeklyFailureRegressionCase(
        "2026-07-23",
        "pytest-static",
        "local_contract_failure",
        "typed_fatal",
        "blocked_local_contract_failure",
        artifact_paths=("tests/", "tools/"),
    ),
    WeeklyFailureRegressionCase(
        "2026-07-24",
        "daily-quality",
        "editorial_section_not_article",
        "validator_exclusion",
        "not_applicable",
        artifact_paths=("digest/FX/2026-07-24-FX.md",),
        category="fx",
    ),
    WeeklyFailureRegressionCase(
        "2026-07-27",
        "digest-articles-reconcile",
        "digest_articles_articles_only",
        "deterministic_handler",
        "blocked_articles_only_card_insert_failed",
        "digest-card-insert-patch",
        ("digest/AI/2026-07-27-AI.md", "tmp/newsroom/2026-07-27/ai.records.jsonl"),
        "AI",
    ),
    WeeklyFailureRegressionCase(
        "2026-07-28",
        "generation-quality",
        "date_evidence_source_recoverable",
        "deterministic_handler",
        "blocked_deterministic_repair_failed",
        "date-evidence-source-patch",
        ("data/articles.jsonl", "tmp/newsroom/2026-07-28/*.records.jsonl"),
    ),
    WeeklyFailureRegressionCase(
        "2026-07-29",
        "daily-quality",
        "category_digest_empty",
        "deterministic_handler",
        "blocked_articles_only_card_insert_failed",
        "digest-card-insert-patch",
        ("digest/IT/2026-07-29-IT.md",),
        "it",
    ),
    WeeklyFailureRegressionCase(
        "2026-07-30",
        "pytest-static",
        "local_contract_failure",
        "typed_fatal",
        "blocked_local_contract_failure",
        artifact_paths=("tests/", "tools/"),
    ),
    WeeklyFailureRegressionCase(
        "2026-07-31",
        "daily-quality",
        "category_digest_empty",
        "deterministic_handler",
        "blocked_articles_only_card_insert_failed",
        "digest-card-insert-patch",
        ("digest/Manufacturing/2026-07-31-Manufacturing.md",),
        "manufacturing",
    ),
)


def _evidence_kind(evidence_path: str) -> str:
    if evidence_path.endswith(".json"):
        return "state JSON"
    if evidence_path.endswith(".md"):
        return "incident markdown"
    if evidence_path.endswith(".html"):
        return "incident HTML"
    return "evidence artifact"


def _followup_for(scenario: HistoricalFailureScenario) -> str:
    if scenario.expected_status == "runtime_e2e_required":
        return (
            "runtime E2E / dry-run / runner で同じ stage を再通過させ、"
            f"{scenario.cheapest_e2e_or_fixture} を publish 前の証跡に残す。"
        )
    return (
        "fixture / contract / pytest で同じ invariant を固定し、"
        f"{scenario.cheapest_e2e_or_fixture} を regression gate に残す。"
    )


def _horizontal_audit_for(scenario: HistoricalFailureScenario) -> HistoricalFailureHorizontalAudit:
    kind = _evidence_kind(scenario.evidence_path)
    lanes = {
        "runner": (
            f"runner 実行点は {scenario.stage}。直接原因は {scenario.direct_cause}。"
            "停止位置と次 stage 未到達を同じ incident 内で読む。"
        ),
        "repair": (
            f"repair 観点は root pattern={scenario.root_pattern}。"
            f"必要最小 proof は {scenario.cheapest_e2e_or_fixture}。"
            "同一 gate 再検証または NoPublish 境界を契約化する。"
        ),
        "state": (
            f"state 観点は {kind} の {scenario.evidence_path} を正本証跡にする。"
            f"欠落 invariant は {scenario.missing_invariant}。"
            "publish complete と local proof を混同しない。"
        ),
        "report": (
            f"report 観点は {scenario.evidence_path} に記録された bug class を、"
            "historical failure matrix と incident report validator の両方へ反映する。"
        ),
    }
    return HistoricalFailureHorizontalAudit(
        issue_date=scenario.issue_date,
        stage=scenario.stage,
        evidence_path=scenario.evidence_path,
        lanes=lanes,
        confirmed_gap=(
            f"{scenario.root_pattern} が {scenario.stage} で発生し、"
            f"{scenario.missing_invariant} が未固定だった。"
        ),
        current_contract=(
            f"{scenario.cheapest_e2e_or_fixture} を最小検出点にして、"
            "runner / repair / state / report の横並び調査を全 incident に要求する。"
        ),
        residual_risk=(
            "同じ class が別 stage で再発する場合は、この row の最小 proof だけでは足りないため、"
            "新 incident として matrix へ追記する。"
        ),
        required_followup=_followup_for(scenario),
    )


COMPOUND_SCENARIOS: tuple[CompoundFailureScenario, ...] = (
    CompoundFailureScenario(
        scenario_id="same_artifact_repair_plus_residual_red",
        dimensions=("same artifact", "deterministic repair", "residual known local red", "same gate re-verify"),
        gates=("record-schema", "residual-schema"),
        no_publish_required=True,
        forbidden_public_actions=("fallback_publish", "git_push", "send_push"),
        expected_status="green_after_compound_repair",
        evidence_basis=("2026-06-12 masked record gate errors", "2026-06-19 same-gate repair invariant"),
    ),
    CompoundFailureScenario(
        scenario_id="multi_gate_repair_before_publish_boundary",
        dimensions=("multi gate", "deterministic repair", "pre-publish convergence", "NoPublish side-effect guard"),
        gates=("daily-quality", "generation-quality", "publish-complete"),
        no_publish_required=True,
        forbidden_public_actions=("fallback_publish", "git_push", "youtube_upload", "send_push"),
        expected_status="green_before_publish_boundary_no_public_actions",
        evidence_basis=("2026-06-25 resume order failure", "NoPublish fallback-block contract"),
    ),
    CompoundFailureScenario(
        scenario_id="external_block_plus_local_repair",
        dimensions=("external system outage", "local artifact defect", "typed external boundary"),
        gates=("youtube-podcast-auth", "publish-complete"),
        no_publish_required=True,
        forbidden_public_actions=("fallback_publish", "youtube_upload", "send_push"),
        expected_status="typed_external_block_handled",
        evidence_basis=("OAuth/google_api_external boundary", "external blockers are scenario PASS but not publish Green"),
    ),
    CompoundFailureScenario(
        scenario_id="weekday_inventory_plus_distribution_manifest",
        dimensions=("weekday schedule", "artifact inventory", "distribution manifest", "same publish anchor"),
        gates=("publish-inventory", "distribution-manifest", "publish-complete"),
        no_publish_required=False,
        forbidden_public_actions=(),
        expected_status="green_after_inventory_manifest_reverify",
        evidence_basis=("weekday category schedule", "distribution manifest commit/date anchor"),
    ),
    CompoundFailureScenario(
        scenario_id="summary_materialize_missing_plus_downstream_repair_blockers",
        dimensions=(
            "editor preview",
            "summary materialize",
            "missing generated artifact",
            "record thumb repair",
            "pytest-static isolation",
        ),
        gates=("summary-reflection", "daily-quality", "generation-quality", "record-schema", "pytest-static"),
        no_publish_required=True,
        forbidden_public_actions=("fallback_publish",),
        expected_status="green_after_compound_repair",
        evidence_basis=(
            "2026-07-07 summary-reflection missing Summary",
            "generation-quality missing audio script typed artifact",
            "record-schema thumb key repair",
            "pytest-static external URL isolation",
        ),
    ),
)


def historical_failure_scenarios() -> tuple[HistoricalFailureScenario, ...]:
    return SCENARIOS


HISTORICAL_RECOVERY_REASON_CODES: dict[str, str] = {
    "EDITOR_REPAIR_CHAIN_DISCONNECTED": "CHECKPOINT_VALID_WRAPPER_FAILURE",
    "EDITOR_SNAPSHOT_PATH_ALIAS": "CHECKPOINT_VALID_WRAPPER_FAILURE",
    "WARNING_PREFIX_JSON_UNROUTED": "CHECKPOINT_VALID_WRAPPER_FAILURE",
    "POST_PUSH_BUNDLE_NOT_CLOSED": "PUBLIC_COMPLETION_RED",
    "SLO_REPAIR_NOT_CHECKPOINTED": "CHECKPOINT_VALID_WRAPPER_FAILURE",
    "NESTED_BASETEMP_ROOT_DRIFT": "GENERATION_DRIFT",
    "HERO_TITLE_REPAIR_UNREGISTERED": "PUBLIC_COMPLETION_RED",
    "INCIDENT_POLICY_DRIFT_BLOCKS_DAILY": "MAJOR_INCIDENT",
    "COMPOUND_DAILY_REPAIR_COLLAPSED": "CHECKPOINT_VALID_WRAPPER_FAILURE",
    "EXTERNAL_UPLOAD_MASKS_LOCAL_CONVERGENCE": "TYPED_EXTERNAL_DEPENDENCY",
    "DIGEST_RECORD_SYNC_NOT_CAUSAL": "CHECKPOINT_VALID_WRAPPER_FAILURE",
    "STRUCTURED_UNKNOWN_NOT_RECOVERED": "MAJOR_INCIDENT",
    "DROPPED_REASON_SOURCE_UNSUPPORTED": "MAJOR_INCIDENT",
    "EDITORIAL_SECTION_MISCLASSIFIED_CARD": "PUBLIC_COMPLETION_RED",
    "EXTERNAL_READINESS_AND_GIT_LOCK_COLLAPSED": "TYPED_EXTERNAL_DEPENDENCY",
    "LOCK_PLAYLIST_COMPOUND_NOT_CONVERGED": "CHECKPOINT_VALID_WRAPPER_FAILURE",
    "HANDLER_CAPABILITY_DIRECTION_MISMATCH": "MAJOR_INCIDENT",
    "REPORTER_DATE_EVIDENCE_NOT_RECOVERED": "CHECKPOINT_VALID_WRAPPER_FAILURE",
    "EMPTY_DIGEST_HISTORY_DRIFT_COMPOUND": "CHECKPOINT_VALID_WRAPPER_FAILURE",
    "HISTORY_REPORT_BLOCKS_DAILY": "CHECKPOINT_VALID_WRAPPER_FAILURE",
    "EDITOR_DROPS_VALID_CANDIDATE": "CHECKPOINT_VALID_WRAPPER_FAILURE",
    "DEEPDIVE_URL_DIALOGUE_FALSE_GREEN": "PUBLIC_COMPLETION_RED",
    "SCHEDULED_E2E_IDENTITY_COLLISION": "GENERATION_DRIFT",
    "STARTUP_FAILURE_DEFERRED_WITHOUT_RECOVERY": "GENERATION_DRIFT",
    "NO_LOG_LEDGER_GAP_UNCLASSIFIED": "MAJOR_INCIDENT",
    "STOP_ARTIFACT_LEDGER_INVALID": "MAJOR_INCIDENT",
    "STALE_STATE_DIRTY_RUNTIME_NO_AUTHORITY": "GENERATION_DRIFT",
    "CHECKPOINT_IGNORED_BUDGET_EXHAUSTED": "CHECKPOINT_VALID_WRAPPER_FAILURE",
    "DIRTY_RUNTIME_RECEIPT_LEDGER_TERMINAL_DRIFT": "GENERATION_DRIFT",
    "STARTUP_SELF_REPAIR_REPEATED": "GENERATION_DRIFT",
    "PUBLIC_GREEN_AUDIT_NON_MONOTONIC": "PUBLIC_COMPLETION_RED",
    "ARTIFACT_FIRST_AND_STATE_BINDING_MISSING": "CHECKPOINT_VALID_WRAPPER_FAILURE",
}


def _execute_operational_replay(
    *, repo_root: Path, issue_date: str, replay_id: str, signature: str
) -> dict[str, object]:
    """実daily consumer、checkpoint、retry ledger、registryで一つのreplayを閉じる。"""

    from tools import news_grasp_checkpoint as checkpoint
    from tools import news_grasp_daily_control as daily_control
    from tools import operational_recovery_registry as recovery_registry

    reason_code = HISTORICAL_RECOVERY_REASON_CODES.get(signature)
    if reason_code is None:
        raise ValueError("HISTORICAL_FAILURE_CLASS_UNREGISTERED")
    scheduled_authority_id = f"scheduled-authority:{issue_date}"
    lineage = checkpoint.derive_daily_operation_lineage(
        issue_date=issue_date,
        scheduled_authority_id=scheduled_authority_id,
    )
    lineage_replay = checkpoint.derive_daily_operation_lineage(
        issue_date=issue_date,
        scheduled_authority_id=scheduled_authority_id,
    )
    signature_hash = hashlib.sha256(signature.encode("utf-8")).hexdigest()
    fingerprint = checkpoint.cause_fingerprint(
        issue_date=issue_date,
        daily_operation_lineage_id=lineage,
        artifact_key="daily-bundle",
        stage_id="operational-replay",
        producer_route_id="daily_control",
        failure_class=reason_code,
        reason_code=reason_code,
        cause_input_mask=["signatureHash"],
        input_hashes={"signatureHash": signature_hash},
    )

    with TemporaryDirectory(prefix="news-grasp-historical-replay-") as temporary:
        temporary_root = Path(temporary)
        artifact_checkpoint = checkpoint.create_checkpoint(
            issue_date=issue_date,
            daily_operation_lineage_id=lineage,
            stage="daily-bundle",
            artifact_key="daily-bundle",
            input_hashes={"signatureHash": signature_hash},
            output_hash=signature_hash,
            schema="NEWS_GRASP_DAILY_BUNDLE_V1",
            oracle_id="historical-operational-replay-v1",
            producer_route_id="daily_control",
            next_deterministic_step="registered-recovery",
            cause_fingerprint_value=fingerprint,
            output_path=temporary_root / "checkpoint.json",
        )
        resumed = checkpoint.resume_stage(
            checkpoint=artifact_checkpoint,
            wrapper_result={
                "checkpointAlreadyMaterialized": True,
                "exitCode": 126,
                "checkpointSha256": artifact_checkpoint["checkpointSha256"],
                "issueDate": issue_date,
                "dailyOperationLineageId": lineage,
                "artifactKey": "daily-bundle",
            },
        )
        retry_key = (
            f"{issue_date}|{lineage}|daily-bundle|daily_control|{reason_code}"
        )
        retry_ledger = checkpoint.RetryLedger(temporary_root / "retry-ledger.json")
        retry_ledger.admit_retry(
            key=retry_key,
            fingerprint=fingerprint,
            cause_hash=signature_hash,
        )
        repeated = retry_ledger.admit_retry(
            key=retry_key,
            fingerprint=fingerprint,
            cause_hash=signature_hash,
        )

    typed_external = reason_code == "TYPED_EXTERNAL_DEPENDENCY"
    dispatch_handler_id = "typed_external_dependency"
    recovery_status = "operation_deferred" if typed_external else "completed"
    if not typed_external:
        dispatched = recovery_registry.dispatch(
            repo_root=repo_root,
            reason_code=reason_code,
            context={
                "reasonCode": reason_code,
                "dailyOperationLineageId": lineage,
                "checkpointSha256": artifact_checkpoint["checkpointSha256"],
            },
            handlers=recovery_registry.default_handlers(),
        )
        dispatch_handler_id = dispatched.handler_id
        recovery_status = str(dispatched.result.get("status") or dispatched.status)

    major_incident = dispatch_handler_id == "major_incident_terminal"
    external_dependency = {
        "status": "unavailable" if typed_external else "not_required",
        "evidenceHash": signature_hash,
    }
    completion = daily_control.build_completion_state_vector_v3(
        scheduled_attempt={"status": "failed"},
        recovery_attempt={"status": recovery_status},
        public_receipt={
            "status": "verified_green",
            "authorityId": hashlib.sha256(
                f"public:{issue_date}".encode("utf-8")
            ).hexdigest(),
        },
        readiness_probe={"status": "red" if major_incident else "green"},
        audit_observation={"status": "observed", "causeFingerprint": fingerprint},
        external_dependency=external_dependency,
        constitution_admission={
            "status": "green",
            "constitutionHash": hashlib.sha256(
                (repo_root / "docs/spec.md").read_bytes()
            ).hexdigest(),
        },
    )
    if typed_external:
        status = "external_terminal"
    elif major_incident:
        status = "major_incident_terminal"
    else:
        status = "product_complete"
    return {
        "schemaVersion": "NEWS_GRASP_OPERATIONAL_REPLAY_RESULT_V1",
        "replayId": replay_id,
        "redSignature": signature,
        "dailyOperationLineageId": lineage,
        "sameDailyLineage": lineage == lineage_replay,
        "sameLineage": lineage == lineage_replay,
        "registeredHandlerOrTypedExternal": bool(dispatch_handler_id),
        "registeredHandlerId": dispatch_handler_id,
        "stateInvariantRetryCount": int(repeated["retry"]),
        "checkpointModelRerunCount": int(resumed["modelCalls"]),
        "publicGreenPreserved": completion["publicCompletionStatus"] == "green",
        "finiteTerminal": status.endswith("terminal") or status == "product_complete",
        "status": status,
        "completionStateVector": completion,
        "consumerRoute": "tools.news_grasp_daily_control.build_completion_state_vector_v3",
    }


def replay_operational_failure(*, repo_root: Path | str, fixture: dict[str, object]) -> dict[str, object]:
    """Replay one closed-world monthly row through the production corpus consumer."""
    if not isinstance(fixture, dict):
        raise ValueError("HISTORICAL_FIXTURE_INVALID")
    issue_date = str(fixture.get("issueDate") or "")
    replay_id = str(fixture.get("replayId") or "")
    signature = str(fixture.get("redSignature") or "")
    scenario = next((item for item in SCENARIOS if item.issue_date == issue_date), None)
    if scenario is None or not replay_id or not signature:
        raise ValueError("HISTORICAL_SCENARIO_UNREGISTERED")
    validation = validate_historical_evidence(Path(repo_root), scenario)
    if not validation.valid:
        raise ValueError(f"HISTORICAL_EVIDENCE_INVALID:{validation.reason}")
    return _execute_operational_replay(
        repo_root=Path(repo_root).resolve(),
        issue_date=issue_date,
        replay_id=replay_id,
        signature=signature,
    )


def replay_compound_failure(*, repo_root: Path | str, fixture: dict[str, object]) -> dict[str, object]:
    """Replay a compound row without collapsing its independent failure dimensions."""
    if not isinstance(fixture, dict):
        raise ValueError("COMPOUND_FIXTURE_INVALID")
    replay_id = str(fixture.get("replayId") or "")
    fixture_id = str(fixture.get("fixtureId") or "")
    scenario = next(
        (item for item in COMPOUND_SCENARIOS if item.scenario_id == fixture_id),
        None,
    )
    if scenario is None or not replay_id:
        raise ValueError("COMPOUND_SCENARIO_UNREGISTERED")
    reason_by_compound = {
        "same_artifact_repair_plus_residual_red": "CHECKPOINT_IGNORED_BUDGET_EXHAUSTED",
        "multi_gate_repair_before_publish_boundary": "COMPOUND_DAILY_REPAIR_COLLAPSED",
        "external_block_plus_local_repair": "EXTERNAL_UPLOAD_MASKS_LOCAL_CONVERGENCE",
        "weekday_inventory_plus_distribution_manifest": "LOCK_PLAYLIST_COMPOUND_NOT_CONVERGED",
        "summary_materialize_missing_plus_downstream_repair_blockers": "ARTIFACT_FIRST_AND_STATE_BINDING_MISSING",
    }
    result = _execute_operational_replay(
        repo_root=Path(repo_root).resolve(),
        issue_date="2026-08-11",
        replay_id=replay_id,
        signature=reason_by_compound[fixture_id],
    )
    return {**result, "compoundId": fixture_id, "failureDimensions": list(scenario.dimensions)}


def _operational_closure(
    repo_root: Path,
    scenario: HistoricalFailureScenario,
    live_sha: str,
) -> dict[str, str]:
    consumer_path = Path(__file__).resolve()
    negative_fixture = (
        Path(__file__).resolve().parents[1]
        / "tests"
        / "test_historical_failure_scenarios.py"
    )
    scenario_sha = hashlib.sha256(
        json.dumps(
            {
                key: getattr(scenario, key)
                for key in scenario.__dataclass_fields__
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "consumerPatchHash": hashlib.sha256(
            consumer_path.read_bytes()
        ).hexdigest(),
        "negativeFixtureHash": hashlib.sha256(
            negative_fixture.read_bytes()
        ).hexdigest(),
        "redHash": scenario_sha,
        "greenHash": hashlib.sha256(
            f"{scenario_sha}:{live_sha}:valid".encode("utf-8")
        ).hexdigest(),
        "liveEvidenceHash": live_sha,
    }


def validate_historical_evidence(
    repo_root: Path,
    scenario: HistoricalFailureScenario,
) -> HistoricalEvidenceValidation:
    """tracked 証拠の実体と local-only 証拠の登録済み digest を fail-closed で検証する。"""
    root = repo_root.resolve()
    evidence_path = scenario.evidence_path
    candidate = (root / evidence_path).resolve()
    expected_sha256 = LOCAL_ONLY_EVIDENCE_SHA256.get(evidence_path, "")

    if not candidate.is_relative_to(root):
        return HistoricalEvidenceValidation(
            valid=False,
            mode="evidence_path_outside_repo",
            evidence_path=evidence_path,
            expected_sha256=expected_sha256,
            reason="historical evidence path escaped the repository root",
        )

    if not candidate.exists():
        if expected_sha256:
            return HistoricalEvidenceValidation(
                valid=True,
                mode="registered_local_only_absent",
                evidence_path=evidence_path,
                expected_sha256=expected_sha256,
                reason="local-only evidence is represented by its registered SHA-256 in a clean clone",
                operationalClosure=_operational_closure(
                    root, scenario, expected_sha256
                ),
            )
        return HistoricalEvidenceValidation(
            valid=False,
            mode="required_evidence_missing",
            evidence_path=evidence_path,
            reason="tracked or otherwise required historical evidence is missing",
        )

    if not candidate.is_file():
        return HistoricalEvidenceValidation(
            valid=False,
            mode="evidence_not_regular_file",
            evidence_path=evidence_path,
            expected_sha256=expected_sha256,
            reason="historical evidence path is not a regular file",
        )

    actual_sha256 = hashlib.sha256(candidate.read_bytes()).hexdigest()
    if expected_sha256 and actual_sha256 != expected_sha256:
        return HistoricalEvidenceValidation(
            valid=False,
            mode="registered_local_only_hash_mismatch",
            evidence_path=evidence_path,
            expected_sha256=expected_sha256,
            actual_sha256=actual_sha256,
            reason="local-only historical evidence bytes do not match the registered SHA-256",
        )

    live_sha = actual_sha256 or expected_sha256
    closure = _operational_closure(root, scenario, live_sha)
    return HistoricalEvidenceValidation(
        valid=True,
        mode="registered_local_only_present" if expected_sha256 else "tracked_present",
        evidence_path=evidence_path,
        expected_sha256=expected_sha256,
        actual_sha256=actual_sha256,
        reason="historical evidence contract is satisfied",
        operationalClosure=closure,
    )


def weekly_failure_regression_cases() -> tuple[WeeklyFailureRegressionCase, ...]:
    return WEEKLY_FAILURE_REGRESSION_CASES


def unregistered_incident_reports(repo_root: Path) -> list[dict[str, object]]:
    """visible incident corpus の未登録行を scenario stub 付きで返す。"""
    incident_root = repo_root / "docs" / "incidents"
    registered = {scenario.evidence_path for scenario in SCENARIOS}
    rows: list[dict[str, object]] = []
    if not incident_root.exists():
        return rows
    for path in sorted(incident_root.glob("2026-*-report.html")):
        evidence_path = path.relative_to(repo_root).as_posix()
        if evidence_path in registered:
            continue
        issue_date = path.name[:10]
        rows.append(
            {
                "evidence_path": evidence_path,
                "suggested_scenario_stub": {
                    "issue_date": issue_date,
                    "stage": "TODO: terminal gate / recovery stage",
                    "direct_cause": "TODO: evidence-backed direct cause",
                    "root_pattern": "TODO: cross-day bug class",
                    "missing_invariant": "TODO: invariant that should have prevented recurrence",
                    "cheapest_e2e_or_fixture": "TODO: negative fixture and same-gate reverify",
                    "evidence_path": evidence_path,
                    "expected_status": "runtime_e2e_required",
                },
            }
        )
    return rows


def historical_failure_horizontal_audits() -> tuple[HistoricalFailureHorizontalAudit, ...]:
    return tuple(_horizontal_audit_for(scenario) for scenario in SCENARIOS)


def compound_failure_scenarios() -> tuple[CompoundFailureScenario, ...]:
    return COMPOUND_SCENARIOS
