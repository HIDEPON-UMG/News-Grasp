from __future__ import annotations

from dataclasses import dataclass


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


def historical_failure_horizontal_audits() -> tuple[HistoricalFailureHorizontalAudit, ...]:
    return tuple(_horizontal_audit_for(scenario) for scenario in SCENARIOS)


def compound_failure_scenarios() -> tuple[CompoundFailureScenario, ...]:
    return COMPOUND_SCENARIOS
