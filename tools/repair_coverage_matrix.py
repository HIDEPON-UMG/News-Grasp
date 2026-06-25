from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import json
from typing import Any, Iterable


class RepairClass(StrEnum):
    DETERMINISTIC_HANDLER = "deterministic_handler"
    LLM_GENERATE_MISSING_ARTIFACT = "llm_generate_missing_artifact"
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
    status_on_failure: str = "blocked_repair_handler_unimplemented"
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
    ),
    CoverageRow(
        "daily-quality",
        "category_card_emphasis_missing",
        RepairClass.DETERMINISTIC_HANDLER,
        "category-card-emphasis-patch",
        ("digest/{category}/{date}-{category}.md",),
        "daily-quality",
    ),
    CoverageRow(
        "daily-quality",
        "summary_hero_missing",
        RepairClass.DETERMINISTIC_HANDLER,
        "summary-hero-patch",
        ("digest/Summary/{date}.md",),
        "daily-quality",
    ),
    CoverageRow(
        "daily-quality",
        "thumb_invalid_or_missing",
        RepairClass.DETERMINISTIC_HANDLER,
        "record-thumb-quarantine-patch",
        ("data/articles.jsonl", "data/search_audit/{date}"),
        "record-schema",
    ),
    CoverageRow(
        "daily-quality",
        "published_docs_missing",
        RepairClass.DETERMINISTIC_HANDLER,
        "published-docs-regenerate",
        ("docs/{date}/index.html", "docs/deepdive/{date}/index.html"),
        "deepdive-required",
    ),
    CoverageRow(
        "daily-quality",
        "audio_script_quality_invalid",
        RepairClass.DETERMINISTIC_HANDLER,
        "audio-script-length-patch",
        ("digest/Summary/{date}-audio-script.md",),
        "generation-quality",
    ),
    CoverageRow(
        "daily-quality",
        "url_dead_or_stale",
        RepairClass.DETERMINISTIC_HANDLER,
        "url-quarantine-refill",
        ("data/articles.jsonl", "data/search_audit/{date}"),
        "url-liveness",
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
        RepairClass.DETERMINISTIC_HANDLER,
        "audio-script-length-patch",
        ("digest/Summary/{date}-audio-script.md",),
        "generation-quality",
    ),
    CoverageRow(
        "generation-quality",
        "summary_hero_missing",
        RepairClass.DETERMINISTIC_HANDLER,
        "summary-hero-patch",
        ("digest/Summary/{date}.md",),
        "generation-quality",
    ),
    CoverageRow(
        "generation-quality",
        "summary_reflection_missing",
        RepairClass.DETERMINISTIC_HANDLER,
        "summary-reflection-patch",
        ("digest/Summary/{date}.md",),
        "generation-quality",
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
        RepairClass.TYPED_FATAL,
        "",
        ("data/articles.jsonl",),
        "generation-quality",
        "blocked_generation_input_empty",
        "invalid_input",
        "local_artifact_inventory",
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
        ("digest/{category}/{date}-{category}.md", "data/articles.jsonl"),
        "digest-articles-reconcile",
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
    ),
    CoverageRow(
        "generation-quality",
        "deepdive_structure_invalid",
        RepairClass.DETERMINISTIC_HANDLER,
        "deepdive-structure-patch",
        ("digest/DeepDive/{date}.md",),
        "generation-quality",
    ),
    CoverageRow(
        "digest-articles-reconcile",
        "digest_article_url_mismatch",
        RepairClass.DETERMINISTIC_HANDLER,
        "digest-articles-reconcile-patch",
        ("digest/{category}/{date}-{category}.md", "data/articles.jsonl"),
        "digest-articles-reconcile",
    ),
    CoverageRow(
        "record-schema",
        "title_ja_missing",
        RepairClass.DETERMINISTIC_HANDLER,
        "record-title-ja-patch",
        ("data/articles.jsonl",),
        "record-schema",
    ),
    CoverageRow(
        "record-schema",
        "thumb_invalid_or_missing",
        RepairClass.DETERMINISTIC_HANDLER,
        "record-thumb-quarantine-patch",
        ("data/articles.jsonl", "data/search_audit/{date}"),
        "record-schema",
    ),
    CoverageRow(
        "url-liveness",
        "url_dead_or_stale",
        RepairClass.DETERMINISTIC_HANDLER,
        "url-quarantine-refill",
        ("data/articles.jsonl", "data/search_audit/{date}"),
        "url-liveness",
    ),
    CoverageRow(
        "public-html",
        "public_home_fallback",
        RepairClass.DETERMINISTIC_HANDLER,
        "public-home-regenerate",
        ("docs/index.html", "digest/Summary/{date}.md"),
        "public-html",
    ),
    CoverageRow(
        "deepdive-required",
        "published_docs_missing",
        RepairClass.DETERMINISTIC_HANDLER,
        "published-docs-regenerate",
        ("docs/{date}/index.html", "docs/deepdive/{date}/index.html"),
        "deepdive-required",
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


def _issue_code_from_text(gate_id: str, output: str) -> str:
    text = output.casefold()
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
    if "digest_article_url_mismatch" in text or "digest url" in text:
        return "digest_article_url_mismatch"
    if "title_ja" in text:
        return "title_ja_missing"
    if "thumb" in text or "thumbnail" in text:
        return "thumb_invalid_or_missing"
    if gate_id == "url-liveness" or "404" in text or "410" in text or "stale" in text:
        return "url_dead_or_stale"
    if "public_home_fallback" in text or "fallback" in text:
        return "public_home_fallback"
    if "published docs" in text or "deepdive" in text and "missing" in text:
        return "published_docs_missing"
    if "youtube" in text and ("quota" in text or "permission" in text or "403" in text):
        return "youtube_quota_or_permission"
    if gate_id == "git-push" or "non-fast-forward" in text or "rejected" in text:
        return "remote_divergence"
    return "unknown"


def issues_from_gate_output(gate_id: str, output: str) -> list[RepairIssue]:
    output = output or ""
    stripped = output.strip()
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
                        artifacts = raw.get("artifact_paths") or raw.get("artifacts") or []
                        if isinstance(artifacts, str):
                            artifacts = [artifacts]
                        evidence = raw.get("evidence")
                        if not isinstance(evidence, dict):
                            evidence = {}
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
    return [
        RepairIssue(
            gate_id=gate_id,
            issue_code=_issue_code_from_text(gate_id, output),
            message=output.strip(),
            raw_output=output,
            evidence={},
        )
    ]


def classify_gate_output(gate_id: str, output: str) -> RepairDecision:
    issues = issues_from_gate_output(gate_id, output)
    if not issues:
        return classify_repair_issue(RepairIssue(gate_id=gate_id, issue_code="unknown", raw_output=output))
    return classify_repair_issue(issues[0])
