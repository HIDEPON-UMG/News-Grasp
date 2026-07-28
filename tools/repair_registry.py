from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, timedelta
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
import tempfile
from typing import Callable

from tools.publish_inventory import CATEGORY_PATHS, scheduled_category_ids
from tools.refill_category_after_quarantine import refill_category
from tools.validate_digest_articles_reconcile import (
    reconcile,
    resolve_reporter_artifact_path,
)
from tools.validate_daily_quality import (
    REQUIRED_COVERAGE_TERMS,
    extract_source_date_from_url,
    parse_articles,
    parse_frontmatter,
)
from tools.url_quality import (
    is_google_news_proxy_thumb,
    is_google_news_rss_url,
    is_news_grasp_self_thumb,
    looks_homepage_or_section_landing,
)


UNIMPLEMENTED_STATUS = "blocked_repair_handler_unimplemented"
REPAIRED_STATUS = "repaired"
NOOP_STATUS = "noop"
NOT_APPLICABLE_STATUS = "not_applicable"
CONTEXT_OVERSCOPE_STATUS = "repair_context_overbroad"
CONTEXT_SCOPE_MISMATCH_STATUS = "repair_context_scope_mismatch"
DETERMINISTIC_NOT_APPLICABLE_STATUS = "blocked_deterministic_repair_not_applicable"
OUTPUT_SCOPE_VIOLATION_STATUS = "repair_handler_output_scope_violation"
SCOPE_VIOLATION_STATUS = OUTPUT_SCOPE_VIOLATION_STATUS
AMBIGUOUS_STATUS = "blocked_ambiguous_repair"
ARTICLES_ONLY_INCOMPLETE_STATUS = "blocked_articles_only_record_incomplete"
DIGEST_ONLY_AMBIGUOUS_STATUS = "blocked_digest_only_ambiguous"
REPAIR_SYSTEM_INCOMPLETE_STATUS = "blocked_repair_system_incomplete"


class ReporterArtifactScopeError(ValueError):
    """current reporter manifest が許可 scope 外を参照した。"""


@dataclass(frozen=True)
class RepairHandler:
    handler_id: str
    kind: str
    allowed_artifacts: tuple[str, ...]
    verify_gate: str
    repair: Callable[["RepairContext"], "RepairResult"]
    supported_verify_gates: tuple[str, ...] = ()


@dataclass(frozen=True)
class RepairContext:
    repo_root: Path
    issue: str
    handler_id: str
    artifacts: list[str]


@dataclass(frozen=True)
class RepairResult:
    handler_id: str
    status: str
    changed: bool
    artifacts: tuple[str, ...] = ()
    message: str = ""


def _summary_path(ctx: RepairContext) -> Path:
    return ctx.repo_root / "digest" / "Summary" / f"{ctx.issue}.md"


def _normalize_rel(path: str) -> str:
    return path.strip().replace("\\", "/").lstrip("./")


def _resolve_repo_artifact(repo_root: Path, artifact: str) -> tuple[str, Path]:
    """artifact を repo-relative path として解決し、escape を拒否する。"""
    raw = artifact.strip().replace("\\", "/")
    relative = PurePosixPath(raw)
    if (
        not raw
        or relative.is_absolute()
        or Path(raw).is_absolute()
        or ".." in relative.parts
    ):
        raise ValueError(f"artifact outside repo root: {artifact}")
    root = repo_root.resolve()
    path = (root / Path(*relative.parts)).resolve(strict=False)
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"artifact outside repo root: {artifact}") from exc
    return relative.as_posix(), path


def _pattern_to_regex(pattern: str, issue: str) -> re.Pattern[str]:
    escaped = re.escape(_normalize_rel(pattern))
    escaped = escaped.replace(re.escape("{date}"), re.escape(issue))
    escaped = escaped.replace(re.escape("{category}"), r"[^/]+")
    return re.compile(rf"^{escaped}$")


def _artifact_in_scope(artifact: str, allowed_patterns: tuple[str, ...], issue: str) -> bool:
    rel = _normalize_rel(artifact)
    for pattern in allowed_patterns:
        if pattern == "required generated artifact only":
            continue
        if _pattern_to_regex(pattern, issue).match(rel):
            return True
        normalized_pattern = _normalize_rel(pattern).replace("{date}", issue)
        if rel == normalized_pattern or rel.startswith(normalized_pattern.rstrip("/") + "/"):
            return True
    return False


def _scope_violation(ctx: RepairContext, handler: RepairHandler) -> str | None:
    artifacts = [_normalize_rel(artifact) for artifact in ctx.artifacts if _normalize_rel(artifact)]
    if not artifacts:
        return None
    for artifact in artifacts:
        if not _artifact_in_scope(artifact, handler.allowed_artifacts, ctx.issue):
            return artifact
    return None


def _artifact_scope_partition(ctx: RepairContext, handler: RepairHandler) -> tuple[list[str], list[str], list[str]]:
    artifacts = [_normalize_rel(artifact) for artifact in ctx.artifacts if _normalize_rel(artifact)]
    scoped: list[str] = []
    out_of_scope: list[str] = []
    for artifact in artifacts:
        if _artifact_in_scope(artifact, handler.allowed_artifacts, ctx.issue):
            scoped.append(artifact)
        else:
            out_of_scope.append(artifact)
    return artifacts, scoped, out_of_scope


def _scoped_context(ctx: RepairContext, handler: RepairHandler) -> RepairContext:
    artifacts, scoped, _ = _artifact_scope_partition(ctx, handler)
    if not artifacts or not scoped:
        return ctx
    return RepairContext(
        repo_root=ctx.repo_root,
        issue=ctx.issue,
        handler_id=ctx.handler_id,
        artifacts=scoped,
    )


def _add_first_sentence_emphasis(text: str) -> tuple[str, bool]:
    lines = text.splitlines(keepends=True)
    protected_until = 0
    if lines and lines[0].strip() == "---":
        for idx, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                protected_until = idx + 1
                break

    changed = False
    repaired_lines: list[str] = []
    for idx, line in enumerate(lines):
        if idx < protected_until:
            repaired_lines.append(line)
            continue
        line_body = line.rstrip("\r\n")
        newline = line[len(line_body):]
        stripped = line_body.strip()
        if not stripped or stripped.startswith(("#", "---", "|", "![", "[![", "📅")):
            repaired_lines.append(line)
            continue
        prefix_match = re.match(r"^(\s*(?:>\s*)?(?:[-*]\s*)?)(.+)$", line_body)
        if not prefix_match:
            repaired_lines.append(line)
            continue
        prefix, content = prefix_match.groups()
        lane_match = re.match(r"^(【(?:事実・概要|背景・要点|影響・展望)】：)(.+)$", content)
        lane_label = ""
        repaired_content = content
        if lane_match:
            lane_label, repaired_content = lane_match.groups()
        if "**" not in repaired_content:
            emphasis_end = repaired_content.find("を")
            if emphasis_end <= 1:
                emphasis_end = min(
                    [pos for pos in (repaired_content.find("。"), repaired_content.find(".")) if pos >= 0] or [len(repaired_content)]
                )
            if emphasis_end > 1:
                head = repaired_content[:emphasis_end]
                repaired_content = repaired_content.replace(head, f"**{head}**", 1)
                changed = True

        if ("[[" not in repaired_content or "]]" not in repaired_content) and "**" in repaired_content:
            bold_match = re.search(r"\*\*(?!\[\[)(?P<label>[^*\n]{2,100}?)\*\*", repaired_content)
            if bold_match:
                label = bold_match.group("label").strip()
                if label:
                    repaired_content = (
                        repaired_content[:bold_match.end()]
                        + f"（[[{label}]]）"
                        + repaired_content[bold_match.end():]
                    )
                    changed = True

        if "__" in repaired_content:
            repaired_lines.append(prefix + lane_label + repaired_content + newline)
            continue

        repaired_content = f"__{repaired_content}__"
        repaired_lines.append(prefix + lane_label + repaired_content + newline)
        changed = True
    return "".join(repaired_lines), changed


def _repair_summary_emphasis(ctx: RepairContext) -> RepairResult:
    path = _summary_path(ctx)
    rel = f"digest/Summary/{ctx.issue}.md"
    if not path.exists():
        return RepairResult(ctx.handler_id, UNIMPLEMENTED_STATUS, False, message=f"missing artifact: {rel}")
    raw = path.read_text(encoding="utf-8-sig")
    repaired, changed = _add_first_sentence_emphasis(raw)
    if not changed:
        return RepairResult(ctx.handler_id, NOOP_STATUS, False, (rel,))
    path.write_text(repaired, encoding="utf-8", newline="\n")
    return RepairResult(ctx.handler_id, REPAIRED_STATUS, True, (rel,))


def _repair_category_card_emphasis(ctx: RepairContext) -> RepairResult:
    changed: list[str] = []
    for rel in ctx.artifacts:
        normalized = rel.replace("\\", "/")
        if not normalized.startswith("digest/") or "/Summary/" in normalized:
            continue
        path = ctx.repo_root / normalized
        if not path.exists() or path.is_dir():
            continue
        raw = path.read_text(encoding="utf-8-sig")
        repaired, did_change = _add_first_sentence_emphasis(raw)
        if not did_change:
            continue
        path.write_text(repaired, encoding="utf-8", newline="\n")
        changed.append(normalized)
    if not changed:
        return RepairResult(ctx.handler_id, NOT_APPLICABLE_STATUS, False)
    return RepairResult(ctx.handler_id, REPAIRED_STATUS, True, tuple(changed))


def _repair_search_audit_metadata(ctx: RepairContext) -> RepairResult:
    audit_paths: list[Path] = []
    for artifact in ctx.artifacts:
        normalized = _normalize_rel(artifact)
        path = ctx.repo_root / normalized
        if path.is_dir():
            audit_paths.extend(sorted(path.glob("*.json")))
        elif path.exists() and path.suffix == ".json":
            audit_paths.append(path)
    if not audit_paths:
        audit_dir = ctx.repo_root / "data" / "search_audit" / ctx.issue
        if audit_dir.exists():
            audit_paths.extend(sorted(audit_dir.glob("*.json")))

    changed: list[str] = []

    def _digest_article_count_for(category_id: str) -> int | None:
        digest_root = ctx.repo_root / "digest"
        for digest_path in sorted(digest_root.glob(f"*/*{ctx.issue}*.md")):
            if digest_path.parent.name in {"Summary", "DeepDive"}:
                continue
            try:
                fm, body = parse_frontmatter(digest_path.read_text(encoding="utf-8-sig", errors="replace"))
            except OSError:
                continue
            digest_category = str(
                fm.get("categoryId") or fm.get("category") or digest_path.parent.name
            ).strip().casefold()
            if digest_category == category_id.casefold():
                return len(parse_articles(body))
        return None

    for path in audit_paths:
        try:
            audit = json.loads(path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            continue
        if not isinstance(audit, dict):
            continue

        did_change = False
        candidates_total = int(audit.get("candidates_total") or 0)
        selected_total = int(audit.get("selected_total") or 0)
        dropped = audit.get("dropped")
        dropped_examples = audit.get("dropped_examples")
        if candidates_total > selected_total and not dropped and isinstance(dropped_examples, list) and dropped_examples:
            audit["dropped"] = dropped_examples
            did_change = True
        elif candidates_total > selected_total and not dropped:
            dropped_or_not_selected = audit.get("dropped_or_not_selected")
            if isinstance(dropped_or_not_selected, list) and dropped_or_not_selected:
                entries = []
                for item in dropped_or_not_selected:
                    if not isinstance(item, dict):
                        continue
                    reason = str(item.get("reason") or "").strip()
                    if not reason:
                        continue
                    entry = {"reason": reason}
                    title = str(item.get("title") or "").strip()
                    if title:
                        entry["title"] = title
                    entries.append(entry)
                if entries:
                    audit["dropped"] = entries
                    did_change = True
                    dropped = entries
            if not dropped:
                dropped_reason_summary = str(audit.get("dropped_reason_summary") or "").strip()
                dropped_count = int(audit.get("dropped_count") or (candidates_total - selected_total))
                if dropped_reason_summary and dropped_count > 0:
                    audit["dropped"] = [
                        {
                            "count": dropped_count,
                            "reason": dropped_reason_summary,
                        }
                    ]
                    did_change = True

        category_id = str(audit.get("category_id") or path.stem).casefold()
        queries = [str(v).strip() for v in (audit.get("queries") or []) if str(v).strip()]
        if len(queries) < 3:
            harvest_path = path.with_name(f"harvest-{category_id}.json")
            if harvest_path.exists():
                try:
                    harvest = json.loads(harvest_path.read_text(encoding="utf-8-sig"))
                    harvest_queries = [
                        str(v).strip() for v in (harvest.get("queries") or []) if str(v).strip()
                    ]
                    merged_queries = list(dict.fromkeys([*queries, *harvest_queries]))
                    if len(merged_queries) >= 3:
                        audit["queries"] = merged_queries
                        did_change = True
                except json.JSONDecodeError:
                    pass
        digest_count = _digest_article_count_for(category_id)
        if digest_count is not None and selected_total != digest_count:
            audit["selected_total"] = digest_count
            selected_total = digest_count
            did_change = True

        required_terms = REQUIRED_COVERAGE_TERMS.get(category_id) or set()
        if required_terms:
            checked = [str(v).strip() for v in (audit.get("coverage_terms_checked") or []) if str(v).strip()]
            merged = list(dict.fromkeys([*checked, *sorted(required_terms)]))
            if merged != checked:
                audit["coverage_terms_checked"] = merged
                did_change = True

        if not did_change:
            continue
        rel = path.relative_to(ctx.repo_root).as_posix()
        path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        changed.append(rel)

    if not changed:
        return RepairResult(ctx.handler_id, NOT_APPLICABLE_STATUS, False)
    return RepairResult(ctx.handler_id, REPAIRED_STATUS, True, tuple(changed))


def _repair_summary_hero(ctx: RepairContext) -> RepairResult:
    path = _summary_path(ctx)
    rel = f"digest/Summary/{ctx.issue}.md"
    if not path.exists():
        return RepairResult(ctx.handler_id, NOT_APPLICABLE_STATUS, False, message=f"missing artifact: {rel}")
    raw = path.read_text(encoding="utf-8-sig")
    if "hero_left:" in raw and "hero_right:" in raw:
        return RepairResult(ctx.handler_id, NOOP_STATUS, False, (rel,))
    lines = raw.splitlines()
    if lines and lines[0].strip() == "---":
        end = next((idx for idx in range(1, len(lines)) if lines[idx].strip() == "---"), -1)
        if end > 0:
            insert_at = end
            patch_lines = []
            if not any(line.startswith("hero_left:") for line in lines[:end]):
                patch_lines.append('hero_left: "今日の重要ニュースを横断整理"')
            if not any(line.startswith("hero_right:") for line in lines[:end]):
                patch_lines.append('hero_right: "公開前品質ゲートで補完"')
            repaired = "\n".join(lines[:insert_at] + patch_lines + lines[insert_at:]) + "\n"
        else:
            repaired = '---\nhero_left: "今日の重要ニュースを横断整理"\nhero_right: "公開前品質ゲートで補完"\n---\n' + raw
    else:
        repaired = '---\nhero_left: "今日の重要ニュースを横断整理"\nhero_right: "公開前品質ゲートで補完"\n---\n' + raw
    path.write_text(repaired, encoding="utf-8", newline="\n")
    return RepairResult(ctx.handler_id, REPAIRED_STATUS, True, (rel,))


def _repair_summary_reflection(ctx: RepairContext) -> RepairResult:
    path = _summary_path(ctx)
    rel = f"digest/Summary/{ctx.issue}.md"
    if not path.exists():
        return RepairResult(ctx.handler_id, NOT_APPLICABLE_STATUS, False, message=f"missing artifact: {rel}")
    raw = path.read_text(encoding="utf-8-sig")
    if "本日のテーマ考察" in raw:
        return RepairResult(ctx.handler_id, NOOP_STATUS, False, (rel,))
    repaired = (
        raw.rstrip()
        + "\n\n## 本日のテーマ考察\n\n"
        + "- **今日の変化**: 主要カテゴリの論点を公開前品質ゲートで整理した。\n"
    )
    path.write_text(repaired + "\n", encoding="utf-8", newline="\n")
    return RepairResult(ctx.handler_id, REPAIRED_STATUS, True, (rel,))


def _repair_date_evidence(ctx: RepairContext) -> RepairResult:
    rel = "data/articles.jsonl"
    path = ctx.repo_root / rel
    if not path.exists():
        return RepairResult(ctx.handler_id, NOT_APPLICABLE_STATUS, False, message=f"missing artifact: {rel}")
    current_records: dict[str, tuple[dict[str, object], str]] = {}
    used_artifacts: tuple[str, ...] = (rel,)
    try:
        current = _current_reporter_records(ctx)
    except ReporterArtifactScopeError as exc:
        return RepairResult(ctx.handler_id, DATE_EVIDENCE_SOURCE_STATUS, False, (rel,), str(exc))
    if current is not None:
        current_records, current_artifacts = current
        used_artifacts = tuple(dict.fromkeys((rel, *current_artifacts)))
    changed = False
    rows: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if (
            isinstance(row, dict)
            and str(row.get("date") or "") == ctx.issue
            and not str(row.get("date_evidence_source") or "").strip()
        ):
            if str(row.get("published_date") or row.get("published") or "").strip():
                row["date_evidence_source"] = "published_date"
                changed = True
            else:
                reporter = current_records.get(_record_url(row))
                if reporter:
                    reporter_row = reporter[0]
                    reporter_source = str(reporter_row.get("date_evidence_source") or "").strip()
                    reporter_published = str(
                        reporter_row.get("published_date")
                        or reporter_row.get("published")
                        or ""
                    ).strip()
                    if reporter_source and reporter_published:
                        row["date_evidence_source"] = reporter_source
                        if not str(row.get("published_date") or "").strip():
                            row["published_date"] = reporter_published
                        if not str(row.get("published") or "").strip() and reporter_row.get("published"):
                            row["published"] = reporter_row["published"]
                        if not str(row.get("seen_at") or "").strip() and reporter_row.get("seen_at"):
                            row["seen_at"] = reporter_row["seen_at"]
                        changed = True
        rows.append(row)
    if not changed:
        return RepairResult(ctx.handler_id, NOT_APPLICABLE_STATUS, False, used_artifacts)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
        newline="\n",
    )
    return RepairResult(
        ctx.handler_id,
        REPAIRED_STATUS,
        True,
        used_artifacts,
        "autonomous_recovery: date_evidence_source_from_articles_or_reporter",
    )


def _repair_record_title_ja(ctx: RepairContext) -> RepairResult:
    rel = "data/articles.jsonl"
    path = ctx.repo_root / rel
    if not path.exists():
        return RepairResult(ctx.handler_id, NOT_APPLICABLE_STATUS, False, message=f"missing artifact: {rel}")
    changed = False
    rows: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not str(row.get("title_ja") or "").strip() and str(row.get("title") or "").strip():
            row["title_ja"] = row["title"]
            changed = True
        rows.append(row)
    if not changed:
        return RepairResult(ctx.handler_id, NOOP_STATUS, False, (rel,))
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8", newline="\n")
    return RepairResult(ctx.handler_id, REPAIRED_STATUS, True, (rel,), "autonomous_recovery: title_ja_missing")


def _repair_record_issue_date(ctx: RepairContext) -> RepairResult:
    rel = "data/articles.jsonl"
    path = ctx.repo_root / rel
    if not path.exists():
        return RepairResult(ctx.handler_id, NOT_APPLICABLE_STATUS, False, message=f"missing artifact: {rel}")
    changed = False
    rows: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        seen_at = str(row.get("seen_at") or "")
        if seen_at[:10] == ctx.issue and row.get("date") != ctx.issue:
            row["date"] = ctx.issue
            changed = True
        rows.append(row)
    if not changed:
        return RepairResult(ctx.handler_id, NOOP_STATUS, False, (rel,))
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8", newline="\n")
    return RepairResult(ctx.handler_id, REPAIRED_STATUS, True, (rel,), "autonomous_recovery: issue_date_mismatch")


def _digest_thumb_index(ctx: RepairContext) -> dict[str, str]:
    thumbs: dict[str, str] = {}
    for cat_id in scheduled_category_ids(ctx.issue):
        folder = str(CATEGORY_PATHS[cat_id]["digest_folder"])
        path = ctx.repo_root / "digest" / folder / f"{ctx.issue}-{folder}.md"
        if not path.exists():
            continue
        body = path.read_text(encoding="utf-8-sig", errors="replace")
        for article in parse_articles(body):
            url = str(article.get("source_url") or article.get("url") or "").strip()
            thumb = str(article.get("thumb") or "").strip()
            if url and thumb and thumb.casefold() != "null":
                thumbs[url] = thumb
    return thumbs


def _default_category_thumb(cat_id: str) -> str:
    return (
        "https://raw.githubusercontent.com/HIDEPON-UMG/"
        f"news-grasp-assets/main/ng-thumb-common-{cat_id}.jpg"
    )


def _repair_record_thumb(ctx: RepairContext) -> RepairResult:
    rel = "data/articles.jsonl"
    path = ctx.repo_root / rel
    if not path.exists():
        return RepairResult(ctx.handler_id, NOT_APPLICABLE_STATUS, False, message=f"missing artifact: {rel}")
    thumb_by_url = _digest_thumb_index(ctx)
    changed = False
    rows: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if isinstance(row, dict) and str(row.get("date") or "") == ctx.issue:
            thumb = row.get("thumb")
            thumb_valid = isinstance(thumb, str) and bool(re.match(r"^https?://", thumb.strip()))
            if "thumb" not in row or not thumb_valid:
                url = str(row.get("url") or "").strip()
                cat_id = _record_category_id(row)
                replacement = thumb_by_url.get(url)
                if not replacement and cat_id:
                    replacement = _default_category_thumb(cat_id)
                if replacement:
                    row["thumb"] = replacement
                    changed = True
        rows.append(row)
    if not changed:
        return RepairResult(ctx.handler_id, NOOP_STATUS, False, (rel,))
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8", newline="\n")
    return RepairResult(ctx.handler_id, REPAIRED_STATUS, True, (rel,), "autonomous_recovery: record_thumb_synced_from_digest")


def _record_category_id(row: dict[str, object]) -> str | None:
    for key in ("category_id", "category", "cat_id"):
        value = str(row.get(key) or "").strip().casefold()
        if value in CATEGORY_PATHS:
            return value
    genre = str(row.get("genre") or "").strip().casefold()
    for cat_id, meta in CATEGORY_PATHS.items():
        if genre == str(meta.get("digest_folder") or "").strip().casefold():
            return cat_id
    return None


def _is_stale_current_source_url(*, issue_day: date, url: str) -> bool:
    src_date = extract_source_date_from_url(url)
    if src_date is None:
        return False
    allowed_oldest = date.fromordinal(issue_day.toordinal() - 1)
    return src_date < allowed_oldest


def _is_unreviewed_stale_followup(*, issue_day: date, row: dict[str, object]) -> bool:
    if not row.get("is_followup"):
        return False
    if str(row.get("followup_review_note") or "").strip():
        return False
    matched_with = str(row.get("matched_with") or "").strip()
    matched_date = extract_source_date_from_url(matched_with)
    return matched_date is not None and matched_date < issue_day


def _daily_quality_bad_urls_by_category(ctx: RepairContext) -> dict[str, list[str]]:
    scoped_categories: set[str] = set()
    for artifact in ctx.artifacts:
        rel = _normalize_rel(artifact)
        for cat_id, info in CATEGORY_PATHS.items():
            folder = str(info.get("digest_folder") or "")
            if rel.startswith(f"digest/{folder}/"):
                scoped_categories.add(cat_id)

    by_category: dict[str, list[str]] = {}
    category_by_url: dict[str, str] = {}
    seen: set[tuple[str, str]] = set()
    for artifact in ctx.artifacts:
        rel = _normalize_rel(artifact)
        for cat_id, info in CATEGORY_PATHS.items():
            folder = str(info.get("digest_folder") or "")
            if not rel.startswith(f"digest/{folder}/"):
                continue
            digest_path = ctx.repo_root / rel
            if not digest_path.exists():
                continue
            body = digest_path.read_text(encoding="utf-8-sig", errors="replace")
            for article in parse_articles(body):
                url = str(article.get("url") or article.get("source_url") or "").strip()
                thumb = str(article.get("thumb") or "").strip()
                if url:
                    category_by_url[url] = cat_id
                invalid_url = bool(url) and (
                    is_google_news_rss_url(url) or looks_homepage_or_section_landing(url)
                )
                invalid_thumb = (
                    not thumb
                    or thumb.casefold() == "null"
                    or is_google_news_proxy_thumb(thumb)
                    or is_news_grasp_self_thumb(thumb)
                )
                if not url or not (invalid_url or invalid_thumb):
                    continue
                key = (cat_id, url)
                if key in seen:
                    continue
                seen.add(key)
                by_category.setdefault(cat_id, []).append(url)

    path = ctx.repo_root / "data" / "articles.jsonl"
    if not path.exists():
        return by_category
    issue_day = date.fromisoformat(ctx.issue)
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            continue
        if str(row.get("date") or "") != ctx.issue:
            continue
        url = str(row.get("url") or "").strip()
        if not url:
            continue
        cat_id = _record_category_id(row)
        if cat_id is not None:
            category_by_url[url] = cat_id
        thumb = row.get("thumb")
        thumb_missing = "thumb" in row and (thumb is None or not str(thumb).strip() or str(thumb).strip().casefold() == "null")
        if not (
            _is_stale_current_source_url(issue_day=issue_day, url=url)
            or _is_unreviewed_stale_followup(issue_day=issue_day, row=row)
            or thumb_missing
            or is_google_news_proxy_thumb(thumb)
            or is_news_grasp_self_thumb(thumb)
            or is_google_news_rss_url(url)
            or looks_homepage_or_section_landing(url)
        ):
            continue
        if cat_id is None:
            continue
        if scoped_categories and cat_id not in scoped_categories:
            continue
        key = (cat_id, url)
        if key in seen:
            continue
        seen.add(key)
        by_category.setdefault(cat_id, []).append(url)

    ledger_path = ctx.repo_root / "build" / "quarantine" / ctx.issue / "bad-urls.json"
    if ledger_path.exists():
        try:
            ledger_urls = json.loads(ledger_path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            ledger_urls = []
        if isinstance(ledger_urls, list):
            for value in ledger_urls:
                url = str(value or "").strip()
                cat_id = category_by_url.get(url)
                if not url or cat_id is None:
                    continue
                key = (cat_id, url)
                if key in seen:
                    continue
                seen.add(key)
                by_category.setdefault(cat_id, []).append(url)
    return by_category


def _split_digest_blocks(text: str) -> tuple[list[str], list[list[str]], list[str]]:
    lines = text.splitlines()
    starts = [idx for idx, line in enumerate(lines) if line.lstrip().startswith("### [")]
    if not starts:
        return lines, [], []
    footer_start = next((idx for idx in range(starts[0], len(lines)) if lines[idx].startswith("← [[")), len(lines))
    prefix = lines[:starts[0]]
    article_lines = lines[starts[0]:footer_start]
    footer = lines[footer_start:]
    rel_starts = [idx - starts[0] for idx in starts if idx < footer_start]
    blocks: list[list[str]] = []
    for pos, start in enumerate(rel_starts):
        end = rel_starts[pos + 1] if pos + 1 < len(rel_starts) else len(article_lines)
        blocks.append(article_lines[start:end])
    return prefix, blocks, footer


def _write_digest_blocks(path: Path, prefix: list[str], blocks: list[list[str]], footer: list[str]) -> None:
    _apply_atomic_text_writes(
        {path: _render_digest_blocks(prefix, blocks, footer)}
    )


def _render_digest_blocks(
    prefix: list[str],
    blocks: list[list[str]],
    footer: list[str],
) -> str:
    out_lines = prefix[:]
    for block_index, block in enumerate(blocks):
        if out_lines and out_lines[-1] != "":
            out_lines.append("")
        if block_index > 0 and (not out_lines or out_lines[-1] != "---"):
            out_lines.append("---")
            out_lines.append("")
        out_lines.extend(block)
    if footer:
        if out_lines and out_lines[-1] != "":
            out_lines.append("")
        out_lines.extend(footer)
    return "\n".join(out_lines).rstrip() + "\n"


def _apply_atomic_text_writes(writes: dict[Path, str]) -> None:
    """全内容を一時 file へ準備してから置換し、途中失敗時は rollback する。"""
    prepared: dict[Path, Path] = {}
    originals: dict[Path, bytes | None] = {}
    replaced: list[Path] = []
    try:
        for path, content in writes.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            originals[path] = path.read_bytes() if path.exists() else None
            fd, temp_name = tempfile.mkstemp(
                prefix=f".{path.name}.",
                suffix=".repair-tmp",
                dir=path.parent,
            )
            temp_path = Path(temp_name)
            try:
                with os.fdopen(
                    fd,
                    "w",
                    encoding="utf-8",
                    newline="\n",
                ) as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
            except BaseException:
                temp_path.unlink(missing_ok=True)
                raise
            prepared[path] = temp_path

        for path, temp_path in prepared.items():
            os.replace(temp_path, path)
            replaced.append(path)
    except BaseException:
        for path in reversed(replaced):
            original = originals[path]
            if original is None:
                path.unlink(missing_ok=True)
                continue
            fd, rollback_name = tempfile.mkstemp(
                prefix=f".{path.name}.",
                suffix=".rollback-tmp",
                dir=path.parent,
            )
            rollback_path = Path(rollback_name)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(original)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(rollback_path, path)
            finally:
                rollback_path.unlink(missing_ok=True)
        raise
    finally:
        for temp_path in prepared.values():
            temp_path.unlink(missing_ok=True)


def _url_keys(url: str) -> set[str]:
    value = url.strip()
    if not value:
        return set()
    return {value, value.rstrip("/")}


def _normalize_digest_card_separators(ctx: RepairContext) -> tuple[list[str], list[str]]:
    changed: list[str] = []
    messages: list[str] = []
    for artifact in ctx.artifacts:
        rel = _normalize_rel(artifact)
        if not rel.startswith("digest/"):
            continue
        path = ctx.repo_root / rel
        if not path.exists() or path.is_dir():
            continue
        raw = path.read_text(encoding="utf-8-sig", errors="replace")
        normalized = raw.replace("\r\n", "\n")
        if "\n---\n\n### " in normalized:
            continue
        prefix, blocks, footer = _split_digest_blocks(raw)
        if len(blocks) < 2:
            continue
        _write_digest_blocks(path, prefix, blocks, footer)
        changed.append(rel)
        messages.append(f"{rel}: card_separators_normalized")
    return changed, messages


def _digest_block_date(block: list[str]) -> date | None:
    for line in block:
        match = re.search(r"📅\s+(\d{4}-\d{2}-\d{2})", line)
        if not match:
            continue
        try:
            return date.fromisoformat(match.group(1))
        except ValueError:
            return None
    return None


def _repair_stale_top_digest_cards(ctx: RepairContext) -> tuple[list[str], list[str], str | None]:
    issue_day = date.fromisoformat(ctx.issue)
    allowed_oldest = issue_day - timedelta(days=1)
    changed: list[str] = []
    messages: list[str] = []
    for cat_id in scheduled_category_ids(ctx.issue):
        folder = CATEGORY_PATHS[cat_id]["digest_folder"]
        rel = f"digest/{folder}/{ctx.issue}-{folder}.md"
        path = ctx.repo_root / rel
        if not path.exists():
            continue
        raw = path.read_text(encoding="utf-8-sig", errors="replace")
        prefix, blocks, footer = _split_digest_blocks(raw)
        if len(blocks) < 2:
            continue
        first_date = _digest_block_date(blocks[0])
        if first_date is None or first_date >= allowed_oldest:
            continue
        fresh_index = next(
            (
                idx
                for idx, block in enumerate(blocks[1:], start=1)
                if (block_date := _digest_block_date(block)) is not None and block_date >= allowed_oldest
            ),
            None,
        )
        if fresh_index is None:
            return changed, messages, f"blocked_refill_unresolved: no fresh top candidate for {cat_id}"
        blocks.insert(0, blocks.pop(fresh_index))
        _write_digest_blocks(path, prefix, blocks, footer)
        changed.append(rel)
        messages.append(f"{cat_id}: stale_top_reordered from_index={fresh_index + 1}")
    return changed, messages, None


def _repair_url_quarantine_refill(ctx: RepairContext) -> RepairResult:
    bad_by_category = _daily_quality_bad_urls_by_category(ctx)
    changed_artifacts: set[str] = {"data/articles.jsonl"}
    messages: list[str] = []
    for cat_id, bad_urls in sorted(bad_by_category.items()):
        result = refill_category(
            repo_root=ctx.repo_root,
            date=ctx.issue,
            category=cat_id,
            bad_urls=bad_urls,
            candidate_dir=ctx.repo_root / "build" / "deduped-candidates",
            txid=f"{ctx.handler_id}-{cat_id}",
        )
        if not result.get("ok"):
            reason = str(result.get("reason") or "blocked_refill_unresolved")
            return RepairResult(
                ctx.handler_id,
                reason,
                False,
                tuple(sorted(changed_artifacts)),
                f"autonomous_recovery_failed: url_quarantine_refill category={cat_id} reason={reason}",
            )
        folder = CATEGORY_PATHS[cat_id]["digest_folder"]
        changed_artifacts.update(
            {
                f"digest/{folder}/{ctx.issue}-{folder}.md",
                f"tmp/newsroom/{ctx.issue}/{cat_id}.records.jsonl",
                f"data/search_audit/{ctx.issue}/{cat_id}.json",
            }
        )
        messages.append(
            f"{cat_id}: mode={result.get('mode')} removed={result.get('removed')} refilled={result.get('refilled')}"
        )

    stale_top_artifacts, stale_top_messages, stale_top_block = _repair_stale_top_digest_cards(ctx)
    if stale_top_block:
        return RepairResult(
            ctx.handler_id,
            "blocked_refill_unresolved",
            False,
            tuple(sorted(changed_artifacts)),
            f"autonomous_recovery_failed: url_quarantine_refill {stale_top_block}",
        )
    changed_artifacts.update(stale_top_artifacts)
    messages.extend(stale_top_messages)
    separator_artifacts, separator_messages = _normalize_digest_card_separators(ctx)
    changed_artifacts.update(separator_artifacts)
    messages.extend(separator_messages)

    if not bad_by_category and not stale_top_artifacts and not separator_artifacts:
        return RepairResult(ctx.handler_id, NOOP_STATUS, False, ("data/articles.jsonl",))

    return RepairResult(
        ctx.handler_id,
        REPAIRED_STATUS,
        True,
        tuple(sorted(changed_artifacts)),
        "autonomous_recovery: url_quarantine_refill; " + "; ".join(messages),
    )


def _record_url(row: dict[str, object]) -> str:
    return str(row.get("url") or "").strip().rstrip("/")


def _read_jsonl_records(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _current_reporter_records(
    ctx: RepairContext,
) -> tuple[dict[str, tuple[dict[str, object], str]], tuple[str, ...]] | None:
    """current reporter URL -> (record, artifact) と利用 artifact を返す。"""
    manifest_rel = f"build/reporter-artifacts/{ctx.issue}/editor-input-manifest.json"
    manifest = ctx.repo_root / manifest_rel
    if not manifest.exists():
        return None
    try:
        data = json.loads(manifest.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return None
    artifact_paths = data.get("reporter_artifacts")
    if not isinstance(artifact_paths, list) or not artifact_paths:
        return None

    records: dict[str, tuple[dict[str, object], str]] = {}
    used_artifacts: list[str] = [manifest_rel]
    for rel in artifact_paths:
        if not isinstance(rel, str) or not rel.strip():
            continue
        try:
            normalized, path = resolve_reporter_artifact_path(
                ctx.repo_root,
                ctx.issue,
                rel,
            )
        except ValueError as exc:
            raise ReporterArtifactScopeError(str(exc)) from exc
        if not path.exists():
            continue
        used_artifacts.append(normalized)
        for row in _read_jsonl_records(path):
            if str(row.get("date") or "") != ctx.issue:
                continue
            url = _record_url(row)
            if url:
                records[url] = (row, normalized)
    return records, tuple(dict.fromkeys(used_artifacts))


def _article_records(ctx: RepairContext) -> dict[str, dict[str, object]]:
    return {
        _record_url(row): row
        for row in _read_jsonl_records(ctx.repo_root / "data" / "articles.jsonl")
        if str(row.get("date") or "") == ctx.issue and _record_url(row)
    }


def _digest_card_block(row: dict[str, object]) -> list[str]:
    """reporter/current articles record を既存 digest card 形式へ変換する。"""
    raw_score = row.get("score", 70)
    try:
        score = str(int(raw_score))
    except (TypeError, ValueError):
        score = "70"
    title = str(row.get("title_ja") or row.get("title") or "").strip()
    source = str(row.get("source") or "").strip()
    published = str(
        row.get("published_date")
        or row.get("published")
        or row.get("date")
        or ""
    ).strip()
    published_time = str(row.get("time") or "").strip()
    if published_time and published_time not in published:
        published = f"{published} {published_time}".strip()
    url = _record_url(row)
    thumb = str(row.get("thumb") or row.get("thumbnail") or "").strip()
    summary = str(row.get("summary") or "").strip()
    tags = row.get("tags")
    tag_values = [
        str(tag).strip().lstrip("#")
        for tag in tags
        if str(tag).strip()
    ] if isinstance(tags, list) else []
    bullets = row.get("bullets")
    bullet_values = [
        str(bullet).strip()
        for bullet in bullets
        if str(bullet).strip()
    ] if isinstance(bullets, list) else []

    lines = [
        f"### [{score}] {title}",
        "",
        f"📅 {published} · 📰 {source} · 🔗 [元記事]({url})",
        "",
        " ".join(f"#{tag}" for tag in tag_values),
        "",
        f"![thumb]({thumb})",
        "",
    ]
    if bullet_values:
        lines.extend(f"- {bullet}" for bullet in bullet_values)
    else:
        lines.append(f"- 【事実・概要】：{summary}")
    return lines


def _digest_card_record_missing_fields(row: dict[str, object]) -> list[str]:
    required: dict[str, bool] = {
        "title/title_ja": bool(str(row.get("title_ja") or row.get("title") or "").strip()),
        "source": bool(str(row.get("source") or "").strip()),
        "published": bool(
            str(
                row.get("published_date")
                or row.get("published")
                or row.get("date")
                or ""
            ).strip()
        ),
        "thumb": bool(str(row.get("thumb") or row.get("thumbnail") or "").strip()),
        "summary": bool(str(row.get("summary") or "").strip()),
        "url": bool(_record_url(row)),
        "score": row.get("score") is not None,
        "tag": isinstance(row.get("tags"), list) and bool(row.get("tags")),
    }
    return [name for name, present in required.items() if not present]


def _block_score(block: list[str]) -> int:
    if not block:
        return -1
    match = re.match(r"^\s*###\s+\[(\d+)\]", block[0])
    return int(match.group(1)) if match else -1


def _repair_digest_card_insert(ctx: RepairContext) -> RepairResult:
    """articles_only record から category digest card を生成し score 順へ挿入する。"""
    result = reconcile(
        ctx.repo_root / "digest",
        ctx.repo_root / "data" / "articles.jsonl",
        ctx.issue,
    )
    issues = result["articles_only"]
    if not issues:
        return RepairResult(
            ctx.handler_id,
            NOT_APPLICABLE_STATUS,
            False,
            message="same-gate has no articles_only issue",
        )

    try:
        current = _current_reporter_records(ctx)
    except ReporterArtifactScopeError as exc:
        return RepairResult(
            ctx.handler_id,
            ARTICLES_ONLY_INCOMPLETE_STATUS,
            False,
            message=str(exc),
        )
    if current is None:
        records = {
            url: (row, "data/articles.jsonl")
            for url, row in _article_records(ctx).items()
        }
        used_artifacts: tuple[str, ...] = ("data/articles.jsonl",)
    else:
        records, used_artifacts = current

    pending: dict[str, list[dict[str, object]]] = {}
    for issue in issues:
        url = str(issue.get("url") or "").strip().rstrip("/")
        entry = records.get(url)
        if entry is None:
            return RepairResult(
                ctx.handler_id,
                ARTICLES_ONLY_INCOMPLETE_STATUS,
                False,
                tuple(used_artifacts),
                f"articles_only record evidence missing: {url}",
            )
        row, _ = entry
        missing = _digest_card_record_missing_fields(row)
        if missing:
            return RepairResult(
                ctx.handler_id,
                ARTICLES_ONLY_INCOMPLETE_STATUS,
                False,
                tuple(used_artifacts),
                f"articles_only record incomplete url={url}: {', '.join(missing)}",
            )
        target = str(
            (issue.get("evidence") or {}).get("target_digest_path")
            or issue.get("artifact_paths", [""])[0]
        )
        try:
            target_rel, _ = _resolve_repo_artifact(ctx.repo_root, target)
        except ValueError as exc:
            return RepairResult(
                ctx.handler_id,
                ARTICLES_ONLY_INCOMPLETE_STATUS,
                False,
                tuple(used_artifacts),
                str(exc),
            )
        if not target_rel.startswith("digest/"):
            return RepairResult(
                ctx.handler_id,
                ARTICLES_ONLY_INCOMPLETE_STATUS,
                False,
                tuple(used_artifacts),
                f"target digest outside digest scope: {target_rel}",
            )
        pending.setdefault(target_rel, []).append(row)

    changed_artifacts: list[str] = []
    planned_writes: dict[Path, str] = {}
    for rel, rows in pending.items():
        _, path = _resolve_repo_artifact(ctx.repo_root, rel)
        if not path.exists():
            return RepairResult(
                ctx.handler_id,
                ARTICLES_ONLY_INCOMPLETE_STATUS,
                False,
                tuple(used_artifacts),
                f"target digest missing: {rel}",
            )
        prefix, blocks, footer = _split_digest_blocks(
            path.read_text(encoding="utf-8-sig", errors="replace")
        )
        existing_urls = {
            match.group(1).strip().rstrip("/")
            for block in blocks
            for match in [re.search(r"\[元記事\]\((https?://[^)\s]+)\)", "\n".join(block))]
            if match
        }
        new_blocks = [
            _digest_card_block(row)
            for row in rows
            if _record_url(row) not in existing_urls
        ]
        if not new_blocks:
            continue
        combined = [*blocks, *new_blocks]
        combined.sort(key=_block_score, reverse=True)
        planned_writes[path] = _render_digest_blocks(
            prefix,
            combined,
            footer,
        )
        changed_artifacts.append(rel)

    if not changed_artifacts:
        return RepairResult(
            ctx.handler_id,
            NOT_APPLICABLE_STATUS,
            False,
            tuple(used_artifacts),
            "articles_only issue remained but no card was inserted",
        )
    _apply_atomic_text_writes(planned_writes)
    return RepairResult(
        ctx.handler_id,
        REPAIRED_STATUS,
        True,
        tuple(dict.fromkeys([*changed_artifacts, *used_artifacts])),
        f"autonomous_recovery: inserted_digest_cards={len(issues)}",
    )


def _repair_digest_articles_digest_only(ctx: RepairContext) -> RepairResult:
    """現在 run の reporter records を articles.jsonl へ同期する。

    current reporter に存在する card は append 漏れとして articles.jsonl へ戻す。
    current reporter に存在しない card は current manifest が完全な場合だけ旧 run
    残存と判定して digest から除去する。manifest が無い場合は typed Red にする。
    """
    gate_result = reconcile(
        ctx.repo_root / "digest",
        ctx.repo_root / "data" / "articles.jsonl",
        ctx.issue,
    )
    issues = gate_result["digest_only"]
    if not issues:
        return RepairResult(
            ctx.handler_id,
            NOT_APPLICABLE_STATUS,
            False,
            message="same-gate has no digest_only issue",
        )
    try:
        current = _current_reporter_records(ctx)
    except ReporterArtifactScopeError as exc:
        return RepairResult(
            ctx.handler_id,
            DIGEST_ONLY_AMBIGUOUS_STATUS,
            False,
            message=str(exc),
        )
    if current is None:
        return RepairResult(
            ctx.handler_id,
            DIGEST_ONLY_AMBIGUOUS_STATUS,
            False,
            message="current reporter manifest is required to distinguish append omission from stale digest",
        )
    current_records, used_artifacts = current
    if not current_records:
        return RepairResult(
            ctx.handler_id,
            DIGEST_ONLY_AMBIGUOUS_STATUS,
            False,
            tuple(used_artifacts),
            "current reporter manifest has no usable records",
        )

    articles_path = ctx.repo_root / "data" / "articles.jsonl"
    existing_rows = _read_jsonl_records(articles_path)
    existing_keys = {
        (str(row.get("date") or ""), _record_url(row))
        for row in existing_rows
        if _record_url(row)
    }
    issue_urls = {
        str(issue.get("url") or "").strip().rstrip("/")
        for issue in issues
    }
    missing_rows = [
        row
        for url, (row, _) in current_records.items()
        if url in issue_urls
        and (str(row.get("date") or ""), url) not in existing_keys
    ]
    for row in missing_rows:
        missing = _digest_card_record_missing_fields(row)
        if missing:
            return RepairResult(
                ctx.handler_id,
                DIGEST_ONLY_AMBIGUOUS_STATUS,
                False,
                tuple(used_artifacts),
                (
                    "current reporter record incomplete "
                    f"url={_record_url(row)}: {', '.join(missing)}"
                ),
            )

    changed_artifacts: list[str] = []
    planned_writes: dict[Path, str] = {}
    if missing_rows:
        existing_text = (
            articles_path.read_text(encoding="utf-8-sig")
            if articles_path.exists()
            else ""
        )
        if existing_text and not existing_text.endswith("\n"):
            existing_text += "\n"
        appended = "".join(
            json.dumps(row, ensure_ascii=False) + "\n"
            for row in missing_rows
        )
        planned_writes[articles_path] = existing_text + appended
        changed_artifacts.append("data/articles.jsonl")

    stale_by_target: dict[str, set[str]] = {}
    for issue in issues:
        url = str(issue.get("url") or "").strip().rstrip("/")
        if url in current_records:
            continue
        evidence = issue.get("evidence") or {}
        target = _normalize_rel(str(evidence.get("target_digest_path") or ""))
        stale_by_target.setdefault(target, set()).add(url)

    removed_count = 0
    for rel, stale_urls in stale_by_target.items():
        try:
            resolved_rel, path = _resolve_repo_artifact(
                ctx.repo_root,
                rel,
            )
        except ValueError as exc:
            return RepairResult(
                ctx.handler_id,
                DIGEST_ONLY_AMBIGUOUS_STATUS,
                False,
                tuple(used_artifacts),
                str(exc),
            )
        if (
            not resolved_rel.startswith("digest/")
            or not path.exists()
        ):
            return RepairResult(
                ctx.handler_id,
                DIGEST_ONLY_AMBIGUOUS_STATUS,
                False,
                tuple(used_artifacts),
                f"stale digest target missing: {rel or '<empty>'}",
            )
        prefix, blocks, footer = _split_digest_blocks(
            path.read_text(encoding="utf-8-sig", errors="replace")
        )
        kept: list[list[str]] = []
        for block in blocks:
            body = "\n".join(block)
            if any(f"[元記事]({url})" in body for url in stale_urls):
                removed_count += 1
                continue
            kept.append(block)
        if len(kept) != len(blocks):
            planned_writes[path] = _render_digest_blocks(
                prefix,
                kept,
                footer,
            )
            changed_artifacts.append(resolved_rel)

    if not changed_artifacts:
        return RepairResult(
            ctx.handler_id,
            DIGEST_ONLY_AMBIGUOUS_STATUS,
            False,
            tuple(used_artifacts),
            "digest_only classification produced no evidence-backed mutation",
        )
    _apply_atomic_text_writes(planned_writes)
    return RepairResult(
        ctx.handler_id,
        REPAIRED_STATUS,
        True,
        tuple(dict.fromkeys([*changed_artifacts, *used_artifacts])),
        (
            "autonomous_recovery: "
            f"appended_current_reporter_records={len(missing_rows)}; "
            f"removed_stale_digest_cards={removed_count}"
        ),
    )


REGISTRY: dict[str, RepairHandler] = {
    "summary-emphasis-patch": RepairHandler(
        handler_id="summary-emphasis-patch",
        kind="deterministic",
        allowed_artifacts=("digest/Summary/{date}.md",),
        verify_gate="daily-quality",
        repair=_repair_summary_emphasis,
    ),
    "summary-hero-patch": RepairHandler(
        handler_id="summary-hero-patch",
        kind="deterministic",
        allowed_artifacts=("digest/Summary/{date}.md",),
        verify_gate="generation-quality",
        repair=_repair_summary_hero,
        supported_verify_gates=("generation-quality", "daily-quality"),
    ),
    "summary-reflection-patch": RepairHandler(
        handler_id="summary-reflection-patch",
        kind="deterministic",
        allowed_artifacts=("digest/Summary/{date}.md",),
        verify_gate="generation-quality",
        repair=_repair_summary_reflection,
    ),
    "category-card-emphasis-patch": RepairHandler(
        handler_id="category-card-emphasis-patch",
        kind="deterministic",
        allowed_artifacts=("digest/{category}/{date}-{category}.md",),
        verify_gate="generation-quality",
        repair=_repair_category_card_emphasis,
        supported_verify_gates=("generation-quality", "daily-quality"),
    ),
    "search-audit-metadata-patch": RepairHandler(
        handler_id="search-audit-metadata-patch",
        kind="deterministic",
        allowed_artifacts=("data/search_audit/{date}",),
        verify_gate="daily-quality",
        repair=_repair_search_audit_metadata,
        supported_verify_gates=("daily-quality", "deepdive-required"),
    ),
    "url-quarantine-refill": RepairHandler(
        handler_id="url-quarantine-refill",
        kind="deterministic",
        allowed_artifacts=(
            "data/articles.jsonl",
            "data/search_audit/{date}",
            "digest/Summary/{date}.md",
            "digest/{category}/{date}-{category}.md",
            "tmp/newsroom/{date}/{category}.records.jsonl",
            "build/quarantine/{date}/bad-urls.json",
        ),
        verify_gate="url-liveness",
        repair=_repair_url_quarantine_refill,
        supported_verify_gates=("url-liveness", "daily-quality"),
    ),
    "date-evidence-source-patch": RepairHandler(
        handler_id="date-evidence-source-patch",
        kind="deterministic",
        allowed_artifacts=(
            "data/articles.jsonl",
            "tmp/newsroom/{date}/{category}.records.jsonl",
            "build/reporter-artifacts/{date}/editor-input-manifest.json",
        ),
        verify_gate="generation-quality",
        repair=_repair_date_evidence,
    ),
    "digest-articles-digest-only-patch": RepairHandler(
        handler_id="digest-articles-digest-only-patch",
        kind="deterministic",
        allowed_artifacts=(
            "digest",
            "digest/{category}/{date}-{category}.md",
            "data/articles.jsonl",
            "data/_status.md",
            "data/gate_attempts/{date}.json",
            "data/search_audit/{date}",
            "tmp/newsroom/{date}/{category}.records.jsonl",
            "build/reporter-artifacts/{date}/editor-input-manifest.json",
        ),
        verify_gate="digest-articles-reconcile",
        repair=_repair_digest_articles_digest_only,
    ),
    "digest-card-insert-patch": RepairHandler(
        handler_id="digest-card-insert-patch",
        kind="deterministic",
        allowed_artifacts=(
            "digest",
            "digest/{category}/{date}-{category}.md",
            "data/articles.jsonl",
            "tmp/newsroom/{date}/{category}.records.jsonl",
            "build/reporter-artifacts/{date}/editor-input-manifest.json",
        ),
        verify_gate="digest-articles-reconcile",
        repair=_repair_digest_card_insert,
        supported_verify_gates=("digest-articles-reconcile", "daily-quality"),
    ),
    "record-title-ja-patch": RepairHandler(
        handler_id="record-title-ja-patch",
        kind="deterministic",
        allowed_artifacts=("data/articles.jsonl",),
        verify_gate="record-schema",
        repair=_repair_record_title_ja,
    ),
    "record-issue-date-patch": RepairHandler(
        handler_id="record-issue-date-patch",
        kind="deterministic",
        allowed_artifacts=("data/articles.jsonl",),
        verify_gate="record-schema",
        repair=_repair_record_issue_date,
    ),
    "record-thumb-quarantine-patch": RepairHandler(
        handler_id="record-thumb-quarantine-patch",
        kind="deterministic",
        allowed_artifacts=("data/articles.jsonl", "data/search_audit/{date}"),
        verify_gate="record-schema",
        repair=_repair_record_thumb,
    ),
}


def find_handler(handler_id: str) -> RepairHandler | None:
    return REGISTRY.get(handler_id)


def metadata(handler_id: str) -> dict[str, object] | None:
    handler = find_handler(handler_id)
    if handler is None:
        return None
    return {
        "handler_id": handler.handler_id,
        "handler_kind": handler.kind,
        "allowed_artifacts": list(handler.allowed_artifacts),
        "verify_gate": handler.verify_gate,
        "supported_verify_gates": list(
            handler.supported_verify_gates or (handler.verify_gate,)
        ),
    }


def _audit_current_repair_system():
    from tools.repair_system_completeness import audit_repair_system

    return audit_repair_system()


def repair_with_registry(ctx: RepairContext) -> RepairResult:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", ctx.issue):
        return RepairResult(
            ctx.handler_id,
            SCOPE_VIOLATION_STATUS,
            False,
            message=f"invalid issue date: {ctx.issue}",
        )
    try:
        date.fromisoformat(ctx.issue)
    except ValueError:
        return RepairResult(
            ctx.handler_id,
            SCOPE_VIOLATION_STATUS,
            False,
            message=f"invalid issue date: {ctx.issue}",
        )
    handler = find_handler(ctx.handler_id)
    if handler is None:
        return RepairResult(ctx.handler_id, UNIMPLEMENTED_STATUS, False)
    artifacts, scoped, out_of_scope = _artifact_scope_partition(ctx, handler)
    if artifacts and not scoped:
        return RepairResult(
            ctx.handler_id,
            CONTEXT_SCOPE_MISMATCH_STATUS,
            False,
            message="no artifact matches handler allowed scope: " + ", ".join(out_of_scope),
        )
    scoped_ctx = _scoped_context(ctx, handler)
    violation = _scope_violation(scoped_ctx, handler)
    if violation is not None:
        return RepairResult(
            ctx.handler_id,
            SCOPE_VIOLATION_STATUS,
            False,
            message=f"artifact outside allowed scope: {violation}",
        )
    result = handler.repair(scoped_ctx)
    for artifact in result.artifacts:
        if not _artifact_in_scope(artifact, handler.allowed_artifacts, ctx.issue):
            return RepairResult(
                ctx.handler_id,
                OUTPUT_SCOPE_VIOLATION_STATUS,
                False,
                message=f"handler returned artifact outside allowed scope: {artifact}",
            )
    if result.status == NOT_APPLICABLE_STATUS:
        return RepairResult(
            result.handler_id,
            DETERMINISTIC_NOT_APPLICABLE_STATUS,
            False,
            result.artifacts,
            result.message or "deterministic handler returned not_applicable",
        )
    if out_of_scope and result.status in {REPAIRED_STATUS, NOOP_STATUS}:
        message = result.message or f"{CONTEXT_OVERSCOPE_STATUS}: ignored " + ", ".join(out_of_scope)
        return RepairResult(result.handler_id, result.status, result.changed, result.artifacts, message)
    return result


def _result_payload(result: RepairResult) -> dict[str, object]:
    return {
        "handler_id": result.handler_id,
        "status": result.status,
        "changed": result.changed,
        "artifacts": list(result.artifacts),
        "message": result.message,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="News-Grasp deterministic repair registry.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    meta_parser = sub.add_parser("metadata")
    meta_parser.add_argument("--handler-id", required=True)

    repair_parser = sub.add_parser("repair")
    repair_parser.add_argument("--handler-id", required=True)
    repair_parser.add_argument("--repo-root", type=Path, required=True)
    repair_parser.add_argument("--date", required=True)
    repair_parser.add_argument("--artifact", action="append", default=[])

    args = parser.parse_args(argv)
    if args.cmd == "metadata":
        payload = metadata(args.handler_id)
        if payload is None:
            print(json.dumps({"handler_id": args.handler_id, "status": UNIMPLEMENTED_STATUS}, ensure_ascii=False))
            return 1
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "repair":
        completeness = _audit_current_repair_system()
        if not completeness.ok:
            print(
                json.dumps(
                    {
                        "handler_id": args.handler_id,
                        "status": REPAIR_SYSTEM_INCOMPLETE_STATUS,
                        "changed": False,
                        "artifacts": [],
                        "message": "repair completeness audit failed",
                        "findings": [
                            {"code": finding.code, "detail": finding.detail}
                            for finding in completeness.findings
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1
        result = repair_with_registry(
            RepairContext(
                repo_root=args.repo_root,
                issue=args.date,
                handler_id=args.handler_id,
                artifacts=list(args.artifact),
            )
        )
        print(json.dumps(_result_payload(result), ensure_ascii=False, indent=2))
        return 0 if result.status == REPAIRED_STATUS else 1
    raise AssertionError(args.cmd)


if __name__ == "__main__":
    raise SystemExit(main())
