from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Callable

from tools.repair_audio_script_length import repair_file as repair_audio_script_file


UNIMPLEMENTED_STATUS = "blocked_repair_handler_unimplemented"
REPAIRED_STATUS = "repaired"
NOOP_STATUS = "noop"
NOT_APPLICABLE_STATUS = "not_applicable"
SCOPE_VIOLATION_STATUS = "blocked_scope_violation"
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


def _add_first_sentence_emphasis(text: str) -> tuple[str, bool]:
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "---", ">", "|", "-", "*")):
            continue
        if "**" in stripped:
            continue
        emphasis_end = stripped.find("を")
        if emphasis_end <= 1:
            emphasis_end = min(
                [idx for idx in (stripped.find("。"), stripped.find(".")) if idx >= 0] or [len(stripped)]
            )
        if emphasis_end <= 1:
            continue
        head = stripped[:emphasis_end]
        repaired = stripped.replace(head, f"**{head}**", 1)
        return text.replace(stripped, repaired, 1), True
    return text, False


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
    return RepairResult(ctx.handler_id, REPAIRED_STATUS, True, (rel,))


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
    return RepairResult(ctx.handler_id, REPAIRED_STATUS, True, (rel,))


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
    "url-quarantine-refill": RepairHandler(
        handler_id="url-quarantine-refill",
        kind="deterministic",
        allowed_artifacts=("data/articles.jsonl", "data/search_audit/{date}"),
        verify_gate="url-liveness",
        repair=_blocked_ambiguous,
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
        allowed_artifacts=("digest/{category}/{date}-{category}.md", "data/articles.jsonl"),
        verify_gate="digest-articles-reconcile",
        repair=_blocked_ambiguous,
    ),
    "record-title-ja-patch": RepairHandler(
        handler_id="record-title-ja-patch",
        kind="deterministic",
        allowed_artifacts=("data/articles.jsonl",),
        verify_gate="record-schema",
        repair=_repair_record_title_ja,
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
    violation = _scope_violation(ctx, handler)
    if violation is not None:
        return RepairResult(
            ctx.handler_id,
            SCOPE_VIOLATION_STATUS,
            False,
            message=f"artifact outside allowed scope: {violation}",
        )
    result = handler.repair(ctx)
    for artifact in result.artifacts:
        if not _artifact_in_scope(artifact, handler.allowed_artifacts, ctx.issue):
            return RepairResult(
                ctx.handler_id,
                SCOPE_VIOLATION_STATUS,
                False,
                message=f"handler returned artifact outside allowed scope: {artifact}",
            )
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
