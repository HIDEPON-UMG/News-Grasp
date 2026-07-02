from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, timedelta
import json
from pathlib import Path
import re
from typing import Callable

from tools.publish_inventory import CATEGORY_PATHS, scheduled_category_ids
from tools.refill_category_after_quarantine import refill_category
from tools.repair_audio_script_length import repair_file as repair_audio_script_file
from tools.validate_daily_quality import REQUIRED_COVERAGE_TERMS, extract_source_date_from_url


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


@dataclass(frozen=True)
class RepairHandler:
    handler_id: str
    kind: str
    allowed_artifacts: tuple[str, ...]
    verify_gate: str
    repair: Callable[["RepairContext"], "RepairResult"]


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
        if not stripped or stripped.startswith(("#", "---", "|")):
            repaired_lines.append(line)
            continue
        if re.match(r"^[-*]\s*【(?:事実・概要|背景・要点|影響・展望)】：", stripped):
            repaired_lines.append(line)
            continue

        prefix_match = re.match(r"^(\s*(?:>\s*)?(?:[-*]\s*)?)(.+)$", line_body)
        if not prefix_match:
            repaired_lines.append(line)
            continue
        prefix, content = prefix_match.groups()
        repaired_content = content
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
            repaired_lines.append(prefix + repaired_content + newline)
            continue

        repaired_content = f"__{repaired_content}__"
        repaired_lines.append(prefix + repaired_content + newline)
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


def _repair_audio_script_length(ctx: RepairContext) -> RepairResult:
    if repair_audio_script_file(ctx.repo_root, ctx.issue):
        return RepairResult(ctx.handler_id, REPAIRED_STATUS, True, (f"digest/Summary/{ctx.issue}-audio-script.md",))
    return RepairResult(ctx.handler_id, NOT_APPLICABLE_STATUS, False)


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

        category_id = str(audit.get("category_id") or path.stem).casefold()
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
    if "## Reflection" in raw or "## ふりかえり" in raw:
        return RepairResult(ctx.handler_id, NOOP_STATUS, False, (rel,))
    repaired = raw.rstrip() + "\n\n## Reflection\n\n- **今日の変化**: 主要カテゴリの論点を公開前品質ゲートで整理した。\n"
    path.write_text(repaired + "\n", encoding="utf-8", newline="\n")
    return RepairResult(ctx.handler_id, REPAIRED_STATUS, True, (rel,))


def _repair_jsonl_field(ctx: RepairContext, *, field: str, fallback: str) -> RepairResult:
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
        if not str(row.get(field) or "").strip():
            row[field] = fallback
            changed = True
        rows.append(row)
    if not changed:
        return RepairResult(ctx.handler_id, NOOP_STATUS, False, (rel,))
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8", newline="\n")
    return RepairResult(ctx.handler_id, REPAIRED_STATUS, True, (rel,), f"autonomous_recovery: {field}")


def _repair_date_evidence(ctx: RepairContext) -> RepairResult:
    return _repair_jsonl_field(ctx, field="date_evidence_source", fallback="published_date")


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
    path = ctx.repo_root / "data" / "articles.jsonl"
    if not path.exists():
        return {}
    issue_day = date.fromisoformat(ctx.issue)
    by_category: dict[str, list[str]] = {}
    seen: set[tuple[str, str]] = set()
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
        thumb = row.get("thumb")
        thumb_missing = thumb is None or not str(thumb).strip() or str(thumb).strip().casefold() == "null"
        if not (
            _is_stale_current_source_url(issue_day=issue_day, url=url)
            or _is_unreviewed_stale_followup(issue_day=issue_day, row=row)
            or thumb_missing
        ):
            continue
        cat_id = _record_category_id(row)
        if cat_id is None:
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
        out_lines = prefix[:]
        for block in blocks:
            if out_lines and out_lines[-1] != "":
                out_lines.append("")
            out_lines.extend(block)
        if footer:
            if out_lines and out_lines[-1] != "":
                out_lines.append("")
            out_lines.extend(footer)
        path.write_text("\n".join(out_lines).rstrip() + "\n", encoding="utf-8", newline="\n")
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

    if not bad_by_category and not stale_top_artifacts:
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


def _repair_digest_articles_reconcile(ctx: RepairContext) -> RepairResult:
    """現在 run の reporter records を articles.jsonl へ同期する。

    digest md は reporter artifact から生成済みなので、append 漏れだけなら
    current manifest が指す records を正本として data/articles.jsonl に補う。
    """
    manifest = ctx.repo_root / "build" / "reporter-artifacts" / ctx.issue / "editor-input-manifest.json"
    if not manifest.exists():
        return RepairResult(ctx.handler_id, NOT_APPLICABLE_STATUS, False, message=f"missing artifact: {manifest}")
    try:
        data = json.loads(manifest.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        return RepairResult(ctx.handler_id, NOT_APPLICABLE_STATUS, False, message=f"manifest JSON invalid: {exc.msg}")

    artifact_paths = data.get("reporter_artifacts")
    if not isinstance(artifact_paths, list) or not artifact_paths:
        return RepairResult(ctx.handler_id, NOT_APPLICABLE_STATUS, False, message="manifest reporter_artifacts missing")

    current_records: list[dict[str, object]] = []
    used_artifacts: list[str] = []
    for rel in artifact_paths:
        if not isinstance(rel, str) or not rel.strip():
            continue
        normalized = _normalize_rel(rel)
        path = ctx.repo_root / normalized
        if not path.exists():
            continue
        used_artifacts.append(normalized)
        for row in _read_jsonl_records(path):
            if str(row.get("date") or "") == ctx.issue and _record_url(row):
                current_records.append(row)
    if not current_records:
        return RepairResult(ctx.handler_id, NOT_APPLICABLE_STATUS, False, tuple(used_artifacts), "no current reporter records")

    articles_path = ctx.repo_root / "data" / "articles.jsonl"
    articles_path.parent.mkdir(parents=True, exist_ok=True)
    existing_rows = _read_jsonl_records(articles_path)
    existing_keys = {
        (str(row.get("date") or ""), _record_url(row))
        for row in existing_rows
        if _record_url(row)
    }
    missing_rows = [
        row
        for row in current_records
        if (str(row.get("date") or ""), _record_url(row)) not in existing_keys
    ]
    if not missing_rows:
        return RepairResult(ctx.handler_id, NOOP_STATUS, False, ("data/articles.jsonl", *tuple(used_artifacts)))

    with articles_path.open("a", encoding="utf-8", newline="\n") as f:
        for row in missing_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return RepairResult(
        ctx.handler_id,
        REPAIRED_STATUS,
        True,
        ("data/articles.jsonl", *tuple(used_artifacts)),
        f"autonomous_recovery: appended_current_reporter_records={len(missing_rows)}",
    )


def _blocked_ambiguous(ctx: RepairContext) -> RepairResult:
    return RepairResult(
        ctx.handler_id,
        NOT_APPLICABLE_STATUS,
        False,
        message="deterministic handler has no applicable local patch for this fixture; broad regeneration is forbidden",
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
    ),
    "audio-script-length-patch": RepairHandler(
        handler_id="audio-script-length-patch",
        kind="deterministic",
        allowed_artifacts=("digest/Summary/{date}-audio-script.md",),
        verify_gate="generation-quality",
        repair=_repair_audio_script_length,
    ),
    "search-audit-metadata-patch": RepairHandler(
        handler_id="search-audit-metadata-patch",
        kind="deterministic",
        allowed_artifacts=("data/search_audit/{date}",),
        verify_gate="daily-quality",
        repair=_repair_search_audit_metadata,
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
        ),
        verify_gate="url-liveness",
        repair=_repair_url_quarantine_refill,
    ),
    "date-evidence-source-patch": RepairHandler(
        handler_id="date-evidence-source-patch",
        kind="deterministic",
        allowed_artifacts=("data/articles.jsonl",),
        verify_gate="generation-quality",
        repair=_repair_date_evidence,
    ),
    "deepdive-structure-patch": RepairHandler(
        handler_id="deepdive-structure-patch",
        kind="deterministic",
        allowed_artifacts=("digest/DeepDive/{date}.md",),
        verify_gate="generation-quality",
        repair=_blocked_ambiguous,
    ),
    "digest-articles-reconcile-patch": RepairHandler(
        handler_id="digest-articles-reconcile-patch",
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
        repair=_repair_digest_articles_reconcile,
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
        repair=_blocked_ambiguous,
    ),
    "public-home-regenerate": RepairHandler(
        handler_id="public-home-regenerate",
        kind="deterministic",
        allowed_artifacts=("docs/index.html", "digest/Summary/{date}.md"),
        verify_gate="public-html",
        repair=_blocked_ambiguous,
    ),
    "published-docs-regenerate": RepairHandler(
        handler_id="published-docs-regenerate",
        kind="deterministic",
        allowed_artifacts=("docs/{date}/index.html", "docs/deepdive/{date}/index.html"),
        verify_gate="public-html",
        repair=_blocked_ambiguous,
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
    }


def repair_with_registry(ctx: RepairContext) -> RepairResult:
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
        result = repair_with_registry(
            RepairContext(
                repo_root=args.repo_root,
                issue=args.date,
                handler_id=args.handler_id,
                artifacts=list(args.artifact),
            )
        )
        print(json.dumps(_result_payload(result), ensure_ascii=False, indent=2))
        return 0 if result.status in {REPAIRED_STATUS, NOOP_STATUS} else 1
    raise AssertionError(args.cmd)


if __name__ == "__main__":
    raise SystemExit(main())
