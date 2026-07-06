from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import json
import re
from typing import Any, Iterable


class RepairClass(StrEnum):
    DETERMINISTIC_HANDLER = "deterministic_handler"
    LLM_GENERATE_MISSING_ARTIFACT = "llm_generate_missing_artifact"
    LLM_REWRITE_EXISTING_ARTIFACT = "llm_rewrite_existing_artifact"
    TYPED_EXTERNAL = "typed_external"
    TYPED_FATAL = "typed_fatal"
    HANDLER_UNIMPLEMENTED_RED = "handler_unimplemented_red"


@dataclass(frozen=True)
class RepairIssue:
    gate_id: str
    issue_code: str
    message: str = ""
    artifact_paths: tuple[str, ...] = ()
    issue_date: str = ""
    category: str = ""
    raw_output: str = ""
    existing_artifacts: tuple[str, ...] = ()
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RepairDecision:
    gate_id: str
    issue_code: str
    repair_class: RepairClass
    handler_id: str = ""
    allowed_artifacts: tuple[str, ...] = ()
    verify_gate: str = ""
    status_on_failure: str = ""
    artifact_paths: tuple[str, ...] = ()
    external_kind: str = ""
    external_system: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    reason: str = ""


@dataclass(frozen=True)
class CoverageRow:
    gate_id: str
    issue_code: str
    repair_class: RepairClass
    handler_id: str = ""
    allowed_artifacts: tuple[str, ...] = ()
    verify_gate: str = ""
    status_on_failure: str = ""
    external_kind: str = ""
    external_system: str = ""
    reason: str = ""

    def to_decision(self, issue: RepairIssue, *, status_override: str = "") -> RepairDecision:
        return RepairDecision(
            gate_id=issue.gate_id,
            issue_code=issue.issue_code,
            repair_class=self.repair_class,
            handler_id=self.handler_id,
            allowed_artifacts=self.allowed_artifacts,
            verify_gate=self.verify_gate or issue.gate_id,
            status_on_failure=status_override or self.status_on_failure,
            artifact_paths=issue.artifact_paths,
            external_kind=str(issue.evidence.get("external_kind") or self.external_kind),
            external_system=str(issue.evidence.get("external_system") or self.external_system),
            evidence=dict(issue.evidence),
            reason=self.reason,
        )


COVERAGE_ROWS: tuple[CoverageRow, ...] = (
    CoverageRow(
        "daily-quality",
        "summary_reflection_emphasis_missing",
        RepairClass.DETERMINISTIC_HANDLER,
        "summary-emphasis-patch",
        ("digest/Summary/{date}.md",),
        "daily-quality",
        "blocked_deterministic_repair_failed",
    ),
    CoverageRow(
        "daily-quality",
        "category_card_emphasis_missing",
        RepairClass.DETERMINISTIC_HANDLER,
        "category-card-emphasis-patch",
        ("digest/{category}/{date}-{category}.md",),
        "daily-quality",
        "blocked_deterministic_repair_failed",
    ),
    CoverageRow(
        "daily-quality",
        "summary_hero_missing",
        RepairClass.DETERMINISTIC_HANDLER,
        "summary-hero-patch",
        ("digest/Summary/{date}.md",),
        "daily-quality",
        "blocked_deterministic_repair_failed",
    ),
    CoverageRow(
        "daily-quality",
        "thumb_invalid_or_missing",
        RepairClass.DETERMINISTIC_HANDLER,
        "url-quarantine-refill",
        (
            "data/articles.jsonl",
            "data/search_audit/{date}",
            "digest/{category}/{date}-{category}.md",
            "tmp/newsroom/{date}/{category}.records.jsonl",
        ),
        "url-liveness",
        "blocked_refill_unresolved",
    ),
    CoverageRow(
        "daily-quality",
        "search_audit_metadata_missing",
        RepairClass.DETERMINISTIC_HANDLER,
        "search-audit-metadata-patch",
        ("data/search_audit/{date}",),
        "daily-quality",
        "blocked_deterministic_repair_failed",
    ),
    CoverageRow(
        "daily-quality",
        "search_audit_count_mismatch",
        RepairClass.DETERMINISTIC_HANDLER,
        "search-audit-metadata-patch",
        ("data/search_audit/{date}",),
        "daily-quality",
        "blocked_deterministic_repair_failed",
    ),
    CoverageRow(
        "daily-quality",
        "published_docs_missing",
        RepairClass.DETERMINISTIC_HANDLER,
        "published-docs-regenerate",
        ("docs/{date}/index.html", "docs/deepdive/{date}/index.html"),
        "deepdive-required",
        "blocked_deterministic_repair_failed",
    ),
    CoverageRow(
        "daily-quality",
        "audio_script_quality_invalid",
        RepairClass.LLM_REWRITE_EXISTING_ARTIFACT,
        "audio-script-depth-rewrite",
        ("digest/Summary/{date}-audio-script.md",),
        "generation-quality",
        "blocked_audio_script_rewrite_failed",
    ),
    CoverageRow(
        "daily-quality",
        "url_dead_or_stale",
        RepairClass.DETERMINISTIC_HANDLER,
        "url-quarantine-refill",
        (
            "data/articles.jsonl",
            "data/search_audit/{date}",
            "digest/Summary/{date}.md",
            "digest/{category}/{date}-{category}.md",
            "tmp/newsroom/{date}/{category}.records.jsonl",
        ),
        "url-liveness",
        "blocked_refill_unresolved",
    ),
    CoverageRow(
        "daily-quality",
        "missing_artifact",
        RepairClass.LLM_GENERATE_MISSING_ARTIFACT,
        "llm-missing-generated-artifact",
        ("required generated artifact only",),
        "daily-quality",
        "blocked_repair_budget_exhausted",
        reason="missing_artifact",
    ),
    CoverageRow(
        "generation-quality",
        "audio_script_quality_invalid",
        RepairClass.LLM_REWRITE_EXISTING_ARTIFACT,
        "audio-script-depth-rewrite",
        ("digest/Summary/{date}-audio-script.md",),
        "generation-quality",
        "blocked_audio_script_rewrite_failed",
    ),
    CoverageRow(
        "generation-quality",
        "summary_hero_missing",
        RepairClass.DETERMINISTIC_HANDLER,
        "summary-hero-patch",
        ("digest/Summary/{date}.md",),
        "generation-quality",
        "blocked_deterministic_repair_failed",
    ),
    CoverageRow(
        "generation-quality",
        "summary_reflection_missing",
        RepairClass.DETERMINISTIC_HANDLER,
        "summary-reflection-patch",
        ("digest/Summary/{date}.md",),
        "generation-quality",
        "blocked_deterministic_repair_failed",
    ),
    CoverageRow(
        "generation-quality",
        "missing_artifact",
        RepairClass.LLM_GENERATE_MISSING_ARTIFACT,
        "llm-missing-generated-artifact",
        ("required generated artifact only",),
        "generation-quality",
        "blocked_repair_budget_exhausted",
        reason="missing_artifact",
    ),
    CoverageRow(
        "generation-quality",
        "articles_issue_empty",
        RepairClass.DETERMINISTIC_HANDLER,
        "digest-articles-reconcile-patch",
        (
            "data/articles.jsonl",
            "data/_status.md",
            "data/gate_attempts/{date}.json",
            "data/search_audit/{date}",
            "digest",
            "tmp/newsroom/{date}/{category}.records.jsonl",
            "build/reporter-artifacts/{date}/editor-input-manifest.json",
        ),
        "generation-quality",
        "blocked_deterministic_repair_failed",
    ),
    CoverageRow(
        "generation-quality",
        "articles_json_invalid",
        RepairClass.TYPED_FATAL,
        "",
        ("data/articles.jsonl",),
        "generation-quality",
        "blocked_generation_input_invalid",
        "invalid_input",
        "local_artifact_inventory",
    ),
    CoverageRow(
        "generation-quality",
        "audio_script_validator_unavailable",
        RepairClass.TYPED_FATAL,
        "",
        (),
        "generation-quality",
        "blocked_validator_unavailable",
        "local_dependency",
        "python_import",
    ),
    CoverageRow(
        "generation-quality",
        "category_article_body_missing",
        RepairClass.TYPED_FATAL,
        "",
        ("digest/{category}/{date}-{category}.md",),
        "generation-quality",
        "blocked_existing_artifact_llm_recreate",
        "unsafe_existing_artifact_repair",
        "news-grasp",
    ),
    CoverageRow(
        "generation-quality",
        "category_article_empty",
        RepairClass.TYPED_FATAL,
        "",
        ("digest/{category}/{date}-{category}.md",),
        "generation-quality",
        "blocked_existing_artifact_llm_recreate",
        "unsafe_existing_artifact_repair",
        "news-grasp",
    ),
    CoverageRow(
        "generation-quality",
        "deepdive_validator_unavailable",
        RepairClass.TYPED_FATAL,
        "",
        (),
        "generation-quality",
        "blocked_validator_unavailable",
        "local_dependency",
        "python_import",
    ),
    CoverageRow(
        "generation-quality",
        "digest_article_url_mismatch",
        RepairClass.DETERMINISTIC_HANDLER,
        "digest-articles-reconcile-patch",
        (
            "digest",
            "digest/{category}/{date}-{category}.md",
            "data/articles.jsonl",
            "data/_status.md",
            "data/gate_attempts/{date}.json",
            "data/search_audit/{date}",
            "tmp/newsroom/{date}/{category}.records.jsonl",
            "build/reporter-artifacts/{date}/editor-input-manifest.json",
        ),
        "digest-articles-reconcile",
        "blocked_deterministic_repair_failed",
    ),
    CoverageRow(
        "generation-quality",
        "empty_artifact",
        RepairClass.TYPED_FATAL,
        "",
        ("digest/{category}/{date}-{category}.md", "digest/Summary/{date}.md", "digest/DeepDive/{date}.md"),
        "generation-quality",
        "blocked_existing_artifact_llm_recreate",
        "unsafe_existing_artifact_repair",
        "news-grasp",
    ),
    CoverageRow(
        "generation-quality",
        "filename_date_mismatch",
        RepairClass.TYPED_FATAL,
        "",
        ("digest/{category}/{date}-{category}.md",),
        "generation-quality",
        "blocked_invalid_artifact_identity",
        "invalid_artifact_identity",
        "news-grasp",
    ),
    CoverageRow(
        "generation-quality",
        "frontmatter_only",
        RepairClass.TYPED_FATAL,
        "",
        ("digest/{category}/{date}-{category}.md", "digest/Summary/{date}.md", "digest/DeepDive/{date}.md"),
        "generation-quality",
        "blocked_existing_artifact_llm_recreate",
        "unsafe_existing_artifact_repair",
        "news-grasp",
    ),
    CoverageRow(
        "generation-quality",
        "invalid_issue_date",
        RepairClass.TYPED_FATAL,
        "",
        (),
        "generation-quality",
        "blocked_invalid_issue_date",
        "invalid_input",
        "runner",
    ),
    CoverageRow(
        "generation-quality",
        "issue_date_mismatch",
        RepairClass.TYPED_FATAL,
        "",
        ("digest/{category}/{date}-{category}.md", "digest/Summary/{date}.md", "digest/DeepDive/{date}.md"),
        "generation-quality",
        "blocked_invalid_artifact_identity",
        "invalid_artifact_identity",
        "news-grasp",
    ),
    CoverageRow(
        "generation-quality",
        "manifest_error",
        RepairClass.TYPED_FATAL,
        "",
        (),
        "generation-quality",
        "blocked_manifest_error",
        "manifest_error",
        "publish_inventory",
    ),
    CoverageRow(
        "generation-quality",
        "placeholder_digest",
        RepairClass.TYPED_FATAL,
        "",
        ("digest/{category}/{date}-{category}.md", "digest/Summary/{date}.md", "digest/DeepDive/{date}.md"),
        "generation-quality",
        "blocked_existing_artifact_llm_recreate",
        "unsafe_existing_artifact_repair",
        "news-grasp",
    ),
    CoverageRow(
        "generation-quality",
        "date_evidence_source_missing",
        RepairClass.DETERMINISTIC_HANDLER,
        "date-evidence-source-patch",
        ("data/articles.jsonl",),
        "generation-quality",
        "blocked_deterministic_repair_failed",
    ),
    CoverageRow(
        "generation-quality",
        "deepdive_structure_invalid",
        RepairClass.DETERMINISTIC_HANDLER,
        "deepdive-structure-patch",
        ("digest/DeepDive/{date}.md",),
        "generation-quality",
        "blocked_deterministic_repair_failed",
    ),
    CoverageRow(
        "digest-articles-reconcile",
        "digest_article_url_mismatch",
        RepairClass.DETERMINISTIC_HANDLER,
        "digest-articles-reconcile-patch",
        (
            "digest",
            "digest/{category}/{date}-{category}.md",
            "data/articles.jsonl",
            "data/_status.md",
            "data/gate_attempts/{date}.json",
            "data/search_audit/{date}",
            "tmp/newsroom/{date}/{category}.records.jsonl",
            "build/reporter-artifacts/{date}/editor-input-manifest.json",
        ),
        "digest-articles-reconcile",
        "blocked_deterministic_repair_failed",
    ),
    CoverageRow(
        "record-schema",
        "title_ja_missing",
        RepairClass.DETERMINISTIC_HANDLER,
        "record-title-ja-patch",
        ("data/articles.jsonl",),
        "record-schema",
        "blocked_deterministic_repair_failed",
    ),
    CoverageRow(
        "record-schema",
        "issue_date_mismatch",
        RepairClass.DETERMINISTIC_HANDLER,
        "record-issue-date-patch",
        ("data/articles.jsonl",),
        "record-schema",
        "blocked_deterministic_repair_failed",
    ),
    CoverageRow(
        "record-schema",
        "thumb_invalid_or_missing",
        RepairClass.DETERMINISTIC_HANDLER,
        "record-thumb-quarantine-patch",
        ("data/articles.jsonl", "data/search_audit/{date}"),
        "record-schema",
        "blocked_deterministic_repair_failed",
    ),
    CoverageRow(
        "url-liveness",
        "url_dead_or_stale",
        RepairClass.DETERMINISTIC_HANDLER,
        "url-quarantine-refill",
        ("data/articles.jsonl", "data/search_audit/{date}"),
        "url-liveness",
        "blocked_refill_unresolved",
    ),
    CoverageRow(
        "public-html",
        "public_home_fallback",
        RepairClass.DETERMINISTIC_HANDLER,
        "public-home-regenerate",
        ("docs/index.html", "digest/Summary/{date}.md"),
        "public-html",
        "blocked_deterministic_repair_failed",
    ),
    CoverageRow(
        "public-surface",
        "public_sentinel_missing",
        RepairClass.TYPED_FATAL,
        "",
        ("docs/publish-status.json", "docs/sw.js"),
        "verify-publish-complete",
        "public_surface_red",
        "public_sentinel_missing",
        "github-pages",
    ),
    CoverageRow(
        "public-surface",
        "distribution_manifest_invalid",
        RepairClass.TYPED_FATAL,
        "",
        ("build/distribution/{date}.json",),
        "verify-publish-complete",
        "distribution_manifest_invalid",
        "distribution_manifest_invalid",
        "news-grasp",
    ),
    CoverageRow(
        "deepdive-required",
        "published_docs_missing",
        RepairClass.DETERMINISTIC_HANDLER,
        "published-docs-regenerate",
        ("docs/{date}/index.html", "docs/deepdive/{date}/index.html"),
        "deepdive-required",
        "blocked_deterministic_repair_failed",
    ),
    CoverageRow(
        "youtube-podcast",
        "oauth_invalid_grant",
        RepairClass.TYPED_EXTERNAL,
        "",
        ("youtube oauth secrets",),
        "youtube-podcast",
        "blocked_external_readiness",
        "oauth_consent_required",
        "youtube",
    ),
    CoverageRow(
        "youtube-podcast",
        "youtube_quota_or_permission",
        RepairClass.TYPED_EXTERNAL,
        "",
        ("upload state",),
        "youtube-podcast",
        "blocked_external_readiness",
        "quota_or_permission",
        "youtube",
    ),
    CoverageRow(
        "github-pages",
        "deploy_workflow_not_success",
        RepairClass.TYPED_EXTERNAL,
        "",
        (".github/workflows/deploy-pages.yml",),
        "verify-publish-complete",
        "blocked_external_readiness",
        "deploy_workflow_not_success",
        "github-pages",
    ),
    CoverageRow(
        "google-api",
        "google_api_external",
        RepairClass.TYPED_EXTERNAL,
        "",
        ("google api response",),
        "verify-publish-complete",
        "blocked_external_readiness",
        "google_api_external",
        "google-api",
    ),
    CoverageRow(
        "deploy",
        "deploy_surface_regression",
        RepairClass.TYPED_FATAL,
        "",
        ("docs/**",),
        "verify-publish-complete",
        "deploy_surface_regression",
        "deploy_surface_regression",
        "github-pages",
    ),
    CoverageRow(
        "deploy",
        "deploy_surface_unrelated_red",
        RepairClass.TYPED_FATAL,
        "",
        (),
        "verify-publish-complete",
        "deploy_surface_unrelated_red",
        "deploy_surface_unrelated_red",
        "github-pages",
    ),
    CoverageRow(
        "git-push",
        "remote_divergence",
        RepairClass.TYPED_FATAL,
        "",
        (),
        "git-push",
        "repository_safety_stop",
        "remote_divergence",
        "git",
    ),
    CoverageRow(
        "any",
        "unknown",
        RepairClass.TYPED_FATAL,
        "",
        (),
        "same gate",
        "blocked_unknown_repair_class",
        "unknown_repair_class",
        "news-grasp",
    ),
)


KNOWN_VALIDATOR_ISSUES: frozenset[tuple[str, str]] = frozenset(
    (row.gate_id, row.issue_code) for row in COVERAGE_ROWS
)


def _row_map() -> dict[tuple[str, str], CoverageRow]:
    return {(row.gate_id, row.issue_code): row for row in COVERAGE_ROWS}


def find_row(gate_id: str, issue_code: str) -> CoverageRow | None:
    rows = _row_map()
    key = (gate_id.strip(), issue_code.strip())
    if key in rows:
        return rows[key]
    return rows.get(("any", "unknown"))


def unimplemented_rows() -> list[CoverageRow]:
    return [row for row in COVERAGE_ROWS if row.repair_class == RepairClass.HANDLER_UNIMPLEMENTED_RED]


def missing_coverage(required: Iterable[tuple[str, str]] | None = None) -> list[tuple[str, str]]:
    covered = {(row.gate_id, row.issue_code) for row in COVERAGE_ROWS}
    required_set = set(required or KNOWN_VALIDATOR_ISSUES)
    return sorted(required_set - covered)


def classify_repair_issue(issue: RepairIssue) -> RepairDecision:
    row = find_row(issue.gate_id, issue.issue_code)
    if row is None or (row.gate_id, row.issue_code) == ("any", "unknown"):
        unknown_issue = RepairIssue(
            gate_id=issue.gate_id,
            issue_code=issue.issue_code,
            message=issue.message,
            artifact_paths=issue.artifact_paths,
            issue_date=issue.issue_date,
            category=issue.category,
            raw_output=issue.raw_output,
            existing_artifacts=issue.existing_artifacts,
            evidence=issue.evidence,
        )
        return _row_map()[("any", "unknown")].to_decision(
            unknown_issue,
            status_override="blocked_unknown_repair_class",
        )

    if row.repair_class == RepairClass.LLM_GENERATE_MISSING_ARTIFACT:
        typed_reason = str(issue.evidence.get("typed_reason", ""))
        if typed_reason != row.reason:
            return RepairDecision(
                gate_id=issue.gate_id,
                issue_code=issue.issue_code,
                repair_class=RepairClass.TYPED_FATAL,
                verify_gate=row.verify_gate,
                status_on_failure="blocked_missing_artifact_reason_required",
                evidence=dict(issue.evidence),
                reason="missing artifact generation requires typed_reason=missing_artifact",
            )
        if issue.existing_artifacts:
            return RepairDecision(
                gate_id=issue.gate_id,
                issue_code=issue.issue_code,
                repair_class=RepairClass.TYPED_FATAL,
                verify_gate=row.verify_gate,
                status_on_failure="blocked_existing_artifact_llm_recreate",
                evidence=dict(issue.evidence),
                reason="existing artifact must be locally repaired, not regenerated by LLM",
            )

    if row.repair_class == RepairClass.TYPED_EXTERNAL:
        missing = [
            key
            for key in (
                "external_kind",
                "external_system",
                "observed_error_code",
                "source_command",
                "detail",
                "observed_at",
            )
            if not str(issue.evidence.get(key, "")).strip()
        ]
        if missing:
            return RepairDecision(
                gate_id=issue.gate_id,
                issue_code=issue.issue_code,
                repair_class=RepairClass.TYPED_FATAL,
                verify_gate=row.verify_gate,
                status_on_failure="blocked_external_evidence_missing",
                evidence=dict(issue.evidence),
                reason="typed_external requires evidence: " + ", ".join(missing),
            )

    return row.to_decision(issue)


SEARCH_AUDIT_COUNT_MISMATCH_RE = re.compile(
    r"(?P<artifact>.*?data[\\/]+search_audit[\\/]+(?P<issue>\d{4}-\d{2}-\d{2})"
    r"[\\/]+(?P<category>[^\\/:\s]+)\.json): selected_total=(?P<selected>\d+) "
    r"does not match digest article count (?P<count>\d+)\."
)
DIGEST_ARTIFACT_RE = re.compile(
    r"(?P<artifact>digest[\\/]+(?P<folder>[^\\/:\s]+)[\\/]+(?P<issue>\d{4}-\d{2}-\d{2})-[^\\/:\s]+\.md):"
)
DIGEST_FOLDER_TO_CATEGORY = {
    "FX": "fx",
    "AI": "ai",
    "IT-Consulting": "it",
    "Mobility": "mobility",
    "Manufacturing": "manufacturing",
    "Economy": "economy",
    "Game": "game",
}


def _repo_relative_artifact(path_text: str) -> str:
    normalized = path_text.replace("\\", "/")
    marker = "data/search_audit/"
    if marker in normalized:
        return marker + normalized.split(marker, 1)[1]
    return normalized


def _search_audit_count_mismatch_metadata(output: str) -> dict[str, Any]:
    match = SEARCH_AUDIT_COUNT_MISMATCH_RE.search(output)
    if not match:
        return {}
    return {
        "artifact_paths": (_repo_relative_artifact(match.group("artifact")),),
        "issue_date": match.group("issue"),
        "category": match.group("category"),
        "evidence": {
            "selected_total": int(match.group("selected")),
            "digest_article_count": int(match.group("count")),
        },
    }


def _digest_artifact_metadata(output: str) -> dict[str, Any]:
    match = DIGEST_ARTIFACT_RE.search(output)
    if not match:
        return {}
    folder = match.group("folder")
    category = DIGEST_FOLDER_TO_CATEGORY.get(folder, folder.casefold())
    return {
        "artifact_paths": (_repo_relative_artifact(match.group("artifact")),),
        "issue_date": match.group("issue"),
        "category": category,
        "evidence": {"category": category, "digest_folder": folder},
    }


def _issue_code_from_text(gate_id: str, output: str) -> str:
    text = output.casefold()
    if SEARCH_AUDIT_COUNT_MISMATCH_RE.search(output) or (
        "selected_total=" in text and "does not match digest article count" in text
    ):
        return "search_audit_count_mismatch"
    if "digest_article_url_mismatch" in text or "digest url" in text:
        return "digest_article_url_mismatch"
    if "audio_script_quality_invalid" in text:
        return "audio_script_quality_invalid"
    if "summary_hero_missing" in text or "hero_left" in text or "hero_right" in text:
        return "summary_hero_missing"
    if "summary_reflection_missing" in text:
        return "summary_reflection_missing"
    if "date_evidence_source_missing" in text:
        return "date_evidence_source_missing"
    if "deepdive_structure_invalid" in text:
        return "deepdive_structure_invalid"
    if "missing_artifact" in text:
        return "missing_artifact"
    if "card #" in text and "lacks required emphasis" in text:
        return "category_card_emphasis_missing"
    if "lacks required emphasis" in text:
        return "summary_reflection_emphasis_missing"
    if "号日不整合" in text or "issue-date" in text and "date" in text:
        return "issue_date_mismatch"
    if "title_ja" in text:
        return "title_ja_missing"
    if "thumb" in text or "thumbnail" in text:
        return "thumb_invalid_or_missing"
    if "dropped reasons are required" in text or "coverage_terms_checked missing required terms" in text:
        return "search_audit_metadata_missing"
    if gate_id == "url-liveness" or "404" in text or "410" in text or "stale" in text:
        return "url_dead_or_stale"
    if "public_home_fallback" in text or "fallback" in text:
        return "public_home_fallback"
    if "public_sentinel_missing" in text or "publish-status" in text or "sentinel" in text:
        return "public_sentinel_missing"
    if "distribution_manifest_invalid" in text:
        return "distribution_manifest_invalid"
    if "published docs" in text or "deepdive" in text and "missing" in text:
        return "published_docs_missing"
    if "invalid_grant" in text:
        return "oauth_invalid_grant"
    if "youtube" in text and ("quota" in text or "permission" in text or "403" in text):
        return "youtube_quota_or_permission"
    if "deploy_workflow_not_success" in text or "workflow" in text and "not success" in text:
        return "deploy_workflow_not_success"
    if "google_api_external" in text or "google api" in text:
        return "google_api_external"
    if "deploy_surface_regression" in text:
        return "deploy_surface_regression"
    if "deploy_surface_unrelated_red" in text:
        return "deploy_surface_unrelated_red"
    if gate_id == "git-push" or "non-fast-forward" in text or "rejected" in text:
        return "remote_divergence"
    return "unknown"


def issues_from_gate_output(gate_id: str, output: str) -> list[RepairIssue]:
    output = output or ""
    stripped = output.strip().lstrip("\ufeff")
    if stripped:
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            raw_issues = payload.get("issues") or payload.get("errors") or []
            if isinstance(raw_issues, list) and raw_issues:
                issues: list[RepairIssue] = []
                for raw in raw_issues:
                    if isinstance(raw, dict):
                        issue_code = str(raw.get("issue_code") or raw.get("code") or "unknown")
                        artifacts = raw.get("artifact_paths") or raw.get("artifacts") or raw.get("artifact") or []
                        if isinstance(artifacts, str):
                            artifacts = [artifacts]
                        evidence = raw.get("evidence")
                        if not isinstance(evidence, dict):
                            evidence = {}
                        if issue_code == "missing_artifact" and not evidence.get("typed_reason"):
                            evidence = {**evidence, "typed_reason": "missing_artifact"}
                        issues.append(
                            RepairIssue(
                                gate_id=str(raw.get("gate_id") or gate_id),
                                issue_code=issue_code,
                                message=str(raw.get("message") or raw.get("error") or ""),
                                artifact_paths=tuple(str(path) for path in artifacts),
                                issue_date=str(raw.get("issue_date") or raw.get("date") or ""),
                                category=str(raw.get("category") or ""),
                                raw_output=output,
                                existing_artifacts=tuple(str(path) for path in raw.get("existing_artifacts", []) or []),
                                evidence=evidence,
                            )
                        )
                return issues
    issue_lines = [line.strip() for line in output.splitlines() if line.strip().startswith("ERROR:")]
    if issue_lines:
        issues: list[RepairIssue] = []
        for line in issue_lines:
            metadata = _search_audit_count_mismatch_metadata(line) or _digest_artifact_metadata(line)
            issues.append(
                RepairIssue(
                    gate_id=gate_id,
                    issue_code=_issue_code_from_text(gate_id, line),
                    message=line,
                    raw_output=output,
                    artifact_paths=tuple(metadata.get("artifact_paths", ())),
                    issue_date=str(metadata.get("issue_date", "")),
                    category=str(metadata.get("category", "")),
                    evidence=dict(metadata.get("evidence", {})),
                )
            )
        return issues
    metadata = _search_audit_count_mismatch_metadata(output) or _digest_artifact_metadata(output)
    return [
        RepairIssue(
            gate_id=gate_id,
            issue_code=_issue_code_from_text(gate_id, output),
            message=output.strip(),
            raw_output=output,
            artifact_paths=tuple(metadata.get("artifact_paths", ())),
            issue_date=str(metadata.get("issue_date", "")),
            category=str(metadata.get("category", "")),
            evidence=dict(metadata.get("evidence", {})),
        )
    ]


def _issue_priority(issue_code: str) -> int:
    priority = {
        "articles_json_invalid": 0,
        "articles_issue_empty": 1,
        "digest_article_url_mismatch": 2,
        "date_evidence_source_missing": 3,
        "missing_artifact": 10,
        "summary_hero_missing": 20,
        "summary_reflection_missing": 21,
        "summary_reflection_emphasis_missing": 22,
        "category_card_emphasis_missing": 23,
        "search_audit_count_mismatch": 24,
        "search_audit_metadata_missing": 25,
        "deepdive_structure_invalid": 30,
        "thumb_invalid_or_missing": 60,
        "audio_script_quality_invalid": 90,
        "unknown": 1000,
    }
    return priority.get(issue_code, 900)


def classify_gate_issues(gate_id: str, output: str) -> list[RepairDecision]:
    issues = issues_from_gate_output(gate_id, output)
    if not issues:
        issues = [RepairIssue(gate_id=gate_id, issue_code="unknown", raw_output=output)]
    ordered = sorted(issues, key=lambda item: _issue_priority(item.issue_code))
    return [classify_repair_issue(issue) for issue in ordered]


def classify_gate_output(gate_id: str, output: str) -> RepairDecision:
    return classify_gate_issues(gate_id, output)[0]
