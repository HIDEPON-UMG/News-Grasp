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
)


def historical_failure_scenarios() -> tuple[HistoricalFailureScenario, ...]:
    return SCENARIOS


def compound_failure_scenarios() -> tuple[CompoundFailureScenario, ...]:
    return COMPOUND_SCENARIOS
