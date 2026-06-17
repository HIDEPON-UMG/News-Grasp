#!/usr/bin/env python3
"""生成直後 artifact の決定論的品質 gate。"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import date, timedelta
import json
from pathlib import Path
import re
import sys
from typing import Any

from tools.publish_inventory import CATEGORY_PATHS, required_generated_artifacts, scheduled_category_ids


PLACEHOLDER_RE = re.compile(r"(準備中|TODO|TBD|placeholder|coming soon|本文未生成)", re.IGNORECASE)
URL_RE = re.compile(r"https?://[^\s)\]\">]+")
SOURCE_LINK_RE = re.compile(r"元記事\]\((https?://[^)\s]+)\)")
ARTICLE_HEADING_RE = re.compile(r"^###\s+\[\d+\]", re.MULTILINE)
ARTICLE_SECTION_RE = re.compile(r"^###\s+\[\d+\].*$", re.MULTILINE)


@dataclass(frozen=True)
class GenerationQualityError:
    code: str
    artifact: str
    category: str
    reason: str
    expected: str
    actual: str
    retryable: bool = True


@dataclass(frozen=True)
class GenerationQualityResult:
    errors: list[GenerationQualityError]
    warnings: list[GenerationQualityError]
    exit_code: int


def _error(
    code: str,
    artifact: str,
    *,
    category: str = "",
    reason: str,
    expected: str,
    actual: str,
    retryable: bool = True,
) -> GenerationQualityError:
    return GenerationQualityError(
        code=code,
        artifact=artifact.replace("\\", "/"),
        category=category,
        reason=reason,
        expected=expected,
        actual=actual,
        retryable=retryable,
    )


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    frontmatter: dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        frontmatter[key.strip()] = value.strip().strip('"').strip("'")
    return frontmatter, parts[2].strip()


def _meaningful_body(body: str) -> str:
    lines = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped == "---":
            continue
        if stripped.startswith("#") or stripped.startswith("> [!"):
            continue
        lines.append(stripped)
    return "\n".join(lines).strip()


def _article_records(path: Path, issue: str) -> tuple[list[dict[str, Any]], list[GenerationQualityError]]:
    rel = "data/articles.jsonl"
    if not path.exists():
        return [], [
            _error(
                "missing_artifact",
                rel,
                reason="articles jsonl missing",
                expected="data/articles.jsonl exists",
                actual="missing",
            )
        ]
    records: list[dict[str, Any]] = []
    errors: list[GenerationQualityError] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(
                _error(
                    "articles_json_invalid",
                    rel,
                    reason=f"line {lineno}: {exc.msg}",
                    expected="valid JSONL",
                    actual="invalid JSON",
                    retryable=False,
                )
            )
            continue
        if rec.get("date") == issue:
            records.append(rec)
    return records, errors


def _validate_markdown_artifact(repo_root: Path, rel: str, issue: str) -> list[GenerationQualityError]:
    path = repo_root / rel
    category = path.parent.name
    errors: list[GenerationQualityError] = []
    if not path.exists():
        return [
            _error(
                "missing_artifact",
                rel,
                category=category,
                reason="required generated artifact is missing",
                expected="file exists",
                actual="missing",
            )
        ]
    if path.stat().st_size == 0:
        return [
            _error(
                "empty_artifact",
                rel,
                category=category,
                reason="generated markdown is empty",
                expected="non-empty markdown",
                actual="0 bytes",
            )
        ]
    text = _read_text(path)
    frontmatter, body = _split_frontmatter(text)
    meaningful = _meaningful_body(body)
    if len(meaningful) < 2:
        errors.append(
            _error(
                "frontmatter_only",
                rel,
                category=category,
                reason="markdown body has no meaningful content",
                expected="body text after frontmatter",
                actual=f"{len(meaningful)} meaningful chars",
            )
        )
    if PLACEHOLDER_RE.search(text):
        errors.append(
            _error(
                "placeholder_digest",
                rel,
                category=category,
                reason="placeholder marker remains in generated markdown",
                expected="final article body",
                actual="placeholder marker found",
            )
        )
    fm_date = frontmatter.get("date")
    if fm_date and fm_date != issue:
        errors.append(
            _error(
                "issue_date_mismatch",
                rel,
                category=category,
                reason="frontmatter date does not match issue date",
                expected=issue,
                actual=fm_date,
            )
        )
    if issue not in path.name and path.parent.name not in {"Summary", "DeepDive"}:
        errors.append(
            _error(
                "filename_date_mismatch",
                rel,
                category=category,
                reason="filename does not contain issue date",
                expected=issue,
                actual=path.name,
            )
        )
    return errors


def _validate_category_digest(
    repo_root: Path,
    *,
    rel: str,
    cat_id: str,
    issue: str,
    article_urls: set[str],
) -> list[GenerationQualityError]:
    errors = _validate_markdown_artifact(repo_root, rel, issue)
    if errors:
        return errors
    text = _read_text(repo_root / rel)
    if not ARTICLE_HEADING_RE.search(text):
        errors.append(
            _error(
                "category_article_empty",
                rel,
                category=cat_id,
                reason="scheduled category digest has no article heading",
                expected="at least one ### [NN] article",
                actual="0 article headings",
            )
        )
    for section in _article_sections(text):
        if not _article_section_has_body(section):
            errors.append(
                _error(
                    "category_article_body_missing",
                    rel,
                    category=cat_id,
                    reason="article block has no generated body bullet",
                    expected="at least one non-empty article body bullet",
                    actual="heading/link/tag only",
                )
            )
            break
    digest_urls = {url.rstrip(".,") for url in SOURCE_LINK_RE.findall(text)}
    missing_in_articles = sorted(url for url in digest_urls if url not in article_urls)
    if missing_in_articles:
        errors.append(
            _error(
                "digest_article_url_mismatch",
                rel,
                category=cat_id,
                reason="digest URL is absent from issue articles.jsonl",
                expected="all digest source URLs exist in data/articles.jsonl for the issue",
                actual=", ".join(missing_in_articles[:5]),
            )
        )
    return errors


def _article_sections(text: str) -> list[str]:
    matches = list(ARTICLE_SECTION_RE.finditer(text))
    sections: list[str] = []
    for idx, match in enumerate(matches):
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        sections.append(text[match.start() : end])
    return sections


def _article_section_has_body(section: str) -> bool:
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("- ") and len(stripped) > 4:
            return True
    return False


def _validate_summary(repo_root: Path, rel: str, issue: str) -> list[GenerationQualityError]:
    errors = _validate_markdown_artifact(repo_root, rel, issue)
    if errors:
        return errors
    text = _read_text(repo_root / rel)
    frontmatter, body = _split_frontmatter(text)
    if not frontmatter.get("hero_left") or not frontmatter.get("hero_right"):
        errors.append(
            _error(
                "summary_hero_missing",
                rel,
                category="summary",
                reason="Summary hero_left/hero_right is missing",
                expected="hero_left and hero_right",
                actual="missing hero key",
            )
        )
    if "本日のテーマ考察" not in body:
        errors.append(
            _error(
                "summary_reflection_missing",
                rel,
                category="summary",
                reason="Summary reflection section is missing",
                expected="本日のテーマ考察 section",
                actual="missing",
            )
        )
    return errors


def _validate_deepdive(repo_root: Path, rel: str, issue: str) -> list[GenerationQualityError]:
    errors = _validate_markdown_artifact(repo_root, rel, issue)
    if errors:
        return errors
    path = repo_root / rel
    try:
        from tools.render_deepdive import DeepDiveIncompleteError, extract_blocks
        from tools.render_deepdive import _require_blocks as require_deepdive_blocks

        text = _read_text(path)
        _frontmatter, body = _split_frontmatter(text)
        require_deepdive_blocks(path, extract_blocks(body))
    except ImportError as exc:
        errors.append(
            _error(
                "deepdive_validator_unavailable",
                rel,
                category="DeepDive",
                reason=str(exc),
                expected="DeepDive renderer validator can be imported",
                actual=type(exc).__name__,
                retryable=False,
            )
        )
    except DeepDiveIncompleteError as exc:
        errors.append(
            _error(
                "deepdive_structure_invalid",
                rel,
                category="DeepDive",
                reason=str(exc),
                expected="timeline/players/relations/chart>=2/table/decision blocks",
                actual="DeepDiveIncompleteError",
            )
        )
    return errors


def _recent_audio_history(repo_root: Path, issue: str) -> list[str]:
    day = date.fromisoformat(issue)
    history: list[str] = []
    for offset in (1, 2):
        path = repo_root / "digest" / "Summary" / f"{(day - timedelta(days=offset)).isoformat()}-audio-script.md"
        if path.exists():
            history.append(_read_text(path))
    return history


def _validate_audio_script(repo_root: Path, rel: str, issue: str) -> list[GenerationQualityError]:
    errors = _validate_markdown_artifact(repo_root, rel, issue)
    if errors:
        return errors
    try:
        from tools.tts.build_script import validate_script
    except ImportError as exc:
        return [
            _error(
                "audio_script_validator_unavailable",
                rel,
                category="summary",
                reason=str(exc),
                expected="TTS script validator can be imported",
                actual=type(exc).__name__,
                retryable=False,
            )
        ]

    _frontmatter, body = _split_frontmatter(_read_text(repo_root / rel))
    issues = validate_script(body, date=issue, history_texts=_recent_audio_history(repo_root, issue))
    if not issues:
        return []
    return [
        _error(
            "audio_script_quality_invalid",
            rel,
            category="summary",
            reason="; ".join(issues),
            expected="audio script passes deterministic TTS quality checks",
            actual=f"{len(issues)} issue(s)",
        )
    ]


def _validate_support_artifact(repo_root: Path, rel: str) -> list[GenerationQualityError]:
    path = repo_root / rel
    if path.exists():
        return []
    return [
        _error(
            "missing_artifact",
            rel,
            reason="required support artifact is missing",
            expected="path exists",
            actual="missing",
        )
    ]


def validate_generation_quality(repo_root: Path, issue: str) -> GenerationQualityResult:
    try:
        date.fromisoformat(issue)
    except ValueError:
        return GenerationQualityResult(
            errors=[
                _error(
                    "invalid_issue_date",
                    "",
                    reason="issue date must be YYYY-MM-DD",
                    expected="YYYY-MM-DD",
                    actual=issue,
                    retryable=False,
                )
            ],
            warnings=[],
            exit_code=2,
        )

    errors: list[GenerationQualityError] = []
    warnings: list[GenerationQualityError] = []
    try:
        artifacts = required_generated_artifacts(issue)
    except Exception as exc:
        return GenerationQualityResult(
            errors=[
                _error(
                    "manifest_error",
                    "",
                    reason=str(exc),
                    expected="generated manifest can be built",
                    actual=type(exc).__name__,
                    retryable=False,
                )
            ],
            warnings=[],
            exit_code=2,
        )

    issue_records, article_errors = _article_records(repo_root / "data" / "articles.jsonl", issue)
    errors.extend(article_errors)
    article_urls = {str(rec.get("url", "")).strip() for rec in issue_records if rec.get("url")}
    if not issue_records and not article_errors:
        errors.append(
            _error(
                "articles_issue_empty",
                "data/articles.jsonl",
                reason="no articles for issue date",
                expected=f"at least one record with date={issue}",
                actual="0 records",
            )
        )
    if issue_records and not any(rec.get("date_evidence_source") for rec in issue_records):
        errors.append(
            _error(
                "date_evidence_source_missing",
                "data/articles.jsonl",
                reason="issue records have no date_evidence_source",
                expected="at least one freshness annotation",
                actual="none",
            )
        )

    for rel in artifacts:
        if rel.startswith("data/"):
            errors.extend(_validate_support_artifact(repo_root, rel))
        elif rel.endswith("-audio-script.md"):
            errors.extend(_validate_audio_script(repo_root, rel, issue))
        elif rel.endswith(".md") and "/Summary/" in rel:
            errors.extend(_validate_summary(repo_root, rel, issue))
        elif rel.endswith(".md") and "/DeepDive/" in rel:
            errors.extend(_validate_deepdive(repo_root, rel, issue))
        elif rel.endswith(".md"):
            folder = Path(rel).parent.name
            cat_id = next((cid for cid, item in CATEGORY_PATHS.items() if item["digest_folder"] == folder), folder.lower())
            errors.extend(_validate_category_digest(repo_root, rel=rel, cat_id=cat_id, issue=issue, article_urls=article_urls))
        else:
            errors.extend(_validate_support_artifact(repo_root, rel))

    non_retryable = any(not err.retryable for err in errors)
    exit_code = 2 if non_retryable else 1 if errors else 0
    return GenerationQualityResult(errors=errors, warnings=warnings, exit_code=exit_code)


def _print_text(result: GenerationQualityResult) -> None:
    for err in result.errors:
        print(
            f"ERROR[{err.code}] {err.artifact}: {err.reason} "
            f"(expected={err.expected}, actual={err.actual}, retryable={err.retryable})",
            file=sys.stderr,
        )
    for warn in result.warnings:
        print(
            f"WARNING[{warn.code}] {warn.artifact}: {warn.reason} "
            f"(expected={warn.expected}, actual={warn.actual})",
            file=sys.stderr,
        )
    if result.exit_code == 0:
        print("PASS: generation quality OK")


def _payload(result: GenerationQualityResult) -> dict[str, Any]:
    return {
        "ok": result.exit_code == 0,
        "exit_code": result.exit_code,
        "errors": [asdict(err) for err in result.errors],
        "warnings": [asdict(warn) for warn in result.warnings],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate generated News-Grasp artifacts before publish")
    parser.add_argument("--date", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result = validate_generation_quality(Path(args.repo_root), args.date)
    if args.json:
        print(json.dumps(_payload(result), ensure_ascii=False, indent=2))
    else:
        _print_text(result)
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
