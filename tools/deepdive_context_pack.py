from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from datetime import date, timedelta
import json
import re
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARTICLE_DAYS = 7
DEFAULT_CONTEXT_DAYS = 30
MAX_CONTEXT_CANDIDATES = 8
MIN_TTS_CONTEXT_SOURCES = 2
MAX_TTS_CONTEXT_SOURCES = 4
RELATION_KINDS = ("続報", "主役共有", "波及", "対比")
GENERIC_TAGS = {"deepdive", "daily", "weekly", "news-grasp"}
LOW_SIGNAL_CONTEXT_TERMS = {"ai"}
_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_HEADING_RE = re.compile(r"^#{1,6}\s+.*$", re.MULTILINE)
_RELATED_RE = re.compile(r"```related\s*(.*?)```", re.DOTALL)
_TERM_RE = re.compile(r"[A-Za-z0-9]+|[一-龯ぁ-んァ-ンー]{2,}")
_URL_RE = re.compile(r"https?://\S+")


@dataclass(frozen=True)
class ContextSource:
    date: str
    title: str
    relation: str
    link: str
    change: str
    summary: str
    score: int


def _frontmatter_map(markdown: str) -> dict[str, str]:
    match = _FRONTMATTER_RE.match(markdown)
    if not match:
        return {}
    values: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def frontmatter_value(markdown: str, key: str, default: str = "") -> str:
    return _frontmatter_map(markdown).get(key, default) or default


def frontmatter_tags(markdown: str) -> list[str]:
    match = _FRONTMATTER_RE.match(markdown)
    if not match:
        return []
    for line in match.group(1).splitlines():
        if not line.startswith("tags:"):
            continue
        raw = line.split(":", 1)[1].strip()
        try:
            parsed = ast.literal_eval(raw)
        except Exception:
            return [part.strip().strip('"').strip("'") for part in raw.strip("[]").split(",") if part.strip()]
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    return []


def body_sentences(markdown: str, *, limit: int = 12) -> list[str]:
    body = _FRONTMATTER_RE.sub("", markdown)
    body = _CODE_FENCE_RE.sub("", body)
    body = _HEADING_RE.sub("", body)
    body = re.sub(r"\[\[([^\]]+)\]\]", r"\1", body)
    body = body.replace("__", "").replace("**", "")
    candidates = re.split(r"(?<=[。！？])\s*", body)
    sentences: list[str] = []
    for sentence in candidates:
        cleaned = re.sub(r"\s+", "", sentence).strip()
        if len(cleaned) >= 16:
            sentences.append(cleaned)
        if len(sentences) >= limit:
            break
    return sentences


def clip(text: str, limit: int = 160) -> str:
    cleaned = re.sub(r"\s+", "", text).strip()
    if not cleaned:
        return ""
    if len(cleaned) <= limit:
        return cleaned if cleaned.endswith(("。", "！", "？")) else cleaned + "。"
    cut = cleaned[:limit]
    boundary = max(cut.rfind("。"), cut.rfind("！"), cut.rfind("？"))
    if boundary >= 32:
        clipped = cut[: boundary + 1]
    else:
        clipped = cut.rstrip("、。！？") + "。"
    return clipped if clipped.endswith(("。", "！", "？")) else clipped + "。"


def date_from_source_name(source_name: str, fallback: str) -> date:
    match = re.search(r"\d{4}-\d{2}-\d{2}", source_name)
    value = match.group(0) if match else fallback
    return date.fromisoformat(value)


def _clean_term(term: str) -> str:
    return term.strip().casefold()


def _is_signal_term(term: str) -> bool:
    folded = _clean_term(term)
    if not folded or folded in GENERIC_TAGS or folded in LOW_SIGNAL_CONTEXT_TERMS:
        return False
    if folded.startswith("issue-") or folded.startswith("http"):
        return False
    return len(folded) >= 2


def tag_terms(tags: list[str]) -> set[str]:
    terms: set[str] = set()
    for tag in tags:
        folded = _clean_term(tag)
        if _is_signal_term(folded):
            terms.add(folded)
        for term in _TERM_RE.findall(tag):
            folded_term = _clean_term(term)
            if _is_signal_term(folded_term):
                terms.add(folded_term)
    return terms


def title_terms(title: str) -> set[str]:
    return {_clean_term(term) for term in _TERM_RE.findall(title) if _is_signal_term(term)}


def related_by_date(markdown: str) -> dict[str, dict[str, str]]:
    related: dict[str, dict[str, str]] = {}
    for match in _RELATED_RE.finditer(markdown):
        raw = match.group(1).strip()
        try:
            parsed = ast.literal_eval(raw)
        except Exception:
            continue
        if not isinstance(parsed, list):
            continue
        for item in parsed:
            if not isinstance(item, dict):
                continue
            item_date = str(item.get("date") or "").strip()
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", item_date):
                continue
            related[item_date] = {str(key): str(value) for key, value in item.items()}
    return related


def context_source_dates(markdown: str) -> list[str]:
    match = _FRONTMATTER_RE.match(markdown)
    if not match:
        return []
    return re.findall(r"^\s*-\s+date:\s*[\"']?(\d{4}-\d{2}-\d{2})", match.group(1), flags=re.MULTILINE)


def _related_items(markdown: str) -> list[dict[str, str]]:
    return list(related_by_date(markdown).values())


def _archive_dir_for_source(source_name: str, archive_dir: Path | None) -> Path:
    if archive_dir is not None:
        return archive_dir
    source_path = Path(source_name)
    if not source_path.is_absolute():
        source_path = REPO_ROOT / source_path
    if source_path.parent.exists():
        return source_path.parent
    return REPO_ROOT / "digest" / "DeepDive"


def _extract_terms_from_any(value: Any) -> set[str]:
    terms: set[str] = set()
    if isinstance(value, str):
        if _URL_RE.search(value):
            value = _URL_RE.sub(" ", value)
        terms |= title_terms(value)
    elif isinstance(value, dict):
        for item in value.values():
            terms |= _extract_terms_from_any(item)
    elif isinstance(value, list):
        for item in value:
            terms |= _extract_terms_from_any(item)
    return terms


def _record_date(record: dict[str, Any]) -> date | None:
    raw = str(record.get("date") or record.get("published_date") or record.get("published_at") or "")[:10]
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _article_terms(repo_root: Path, *, target_date: date, article_days: int) -> set[str]:
    path = repo_root / "data" / "articles.jsonl"
    if not path.exists():
        return set()
    start = target_date - timedelta(days=article_days)
    terms: set[str] = set()
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        item_date = _record_date(record)
        if item_date is not None and not (start <= item_date <= target_date):
            continue
        for key in ("title", "title_ja", "summary", "summary_ja", "description", "tags", "entities", "topics", "industries"):
            terms |= _extract_terms_from_any(record.get(key))
    return {term for term in terms if _is_signal_term(term)}


def _decision_issue(markdown: str, *, fallback: str) -> str:
    for match in re.finditer(r"```decision\s*(.*?)```", markdown, flags=re.DOTALL):
        try:
            parsed = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and parsed.get("issue"):
            return clip(str(parsed["issue"]), 80)
    return clip(fallback, 80)


def _relation_for_overlap(*, tag_overlap: set[str], title_overlap: set[str]) -> str:
    if tag_overlap:
        return "主役共有"
    if title_overlap:
        return "続報"
    return "対比"


def _context_change(
    *,
    related: dict[str, str] | None,
    context_title: str,
    current_title: str,
    relation: str,
) -> str:
    if related and str(related.get("change") or "").strip():
        return str(related["change"]).strip()
    if relation == "主役共有":
        return f"前回は「{context_title}」として同じ主役の論点を扱い、今回は「{current_title}」として業務への埋め込み方に焦点が移った。"
    if relation == "続報":
        return f"前回の論点を受けて、今回は「{current_title}」で新しい変化点を読む段階に進んだ。"
    if relation == "波及":
        return f"前回の構造が今回の「{current_title}」へ波及し、実務上の確認点が広がった。"
    return f"前回の「{context_title}」と今回の「{current_title}」を対比すると、同じAI導入でも価値の出方が変わっている。"


def _context_from_archive(
    *,
    path: Path,
    item_date: date,
    source_title: str,
    source_terms: set[str],
    source_title_terms: set[str],
    source_markdown: str = "",
    related: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    text = path.read_text(encoding="utf-8-sig")
    title = frontmatter_value(text, "title", path.stem)
    item_terms = tag_terms(frontmatter_tags(text)) | title_terms(title)
    tag_overlap = source_terms & item_terms
    title_overlap = source_title_terms & title_terms(title)
    if source_markdown:
        folded_source = source_markdown.casefold()
        tag_overlap |= {term for term in item_terms if len(term) >= 3 and term in folded_source}
    signal_terms = {term for term in (tag_overlap | title_overlap) if _is_signal_term(term)}
    if not related and not signal_terms:
        return None
    relation = str((related or {}).get("relation") or "").strip()
    if relation not in RELATION_KINDS:
        relation = _relation_for_overlap(tag_overlap=tag_overlap, title_overlap=title_overlap)
    evidence = []
    if signal_terms:
        evidence.append({"type": "term_overlap", "terms": sorted(signal_terms)[:10]})
    if related:
        evidence.append({"type": "explicit_related", "date": item_date.isoformat()})
    score = len(signal_terms) * 3 + (3 if title_overlap else 0) + (20 if related else 0)
    summary = clip((body_sentences(text, limit=1) or [title])[0], 140)
    return {
        "date": item_date.isoformat(),
        "title": title,
        "lens": frontmatter_value(text, "lens", ""),
        "theme": frontmatter_value(text, "theme", title),
        "tags": frontmatter_tags(text),
        "related": _related_items(text),
        "summary_excerpt": summary,
        "decision_issue": _decision_issue(text, fallback=title),
        "signal_terms": sorted(signal_terms),
        "relation": relation,
        "score": score,
        "evidence": evidence,
        "source_path": path.as_posix(),
    }


def build_context_pack(
    issue_date: str,
    *,
    repo_root: Path = REPO_ROOT,
    article_days: int = DEFAULT_ARTICLE_DAYS,
    context_days: int = DEFAULT_CONTEXT_DAYS,
    max_candidates: int = MAX_CONTEXT_CANDIDATES,
) -> dict[str, Any]:
    target_date = date.fromisoformat(issue_date)
    repo_root = Path(repo_root)
    article_terms = _article_terms(repo_root, target_date=target_date, article_days=article_days)
    archive_dir = repo_root / "digest" / "DeepDive"
    candidates: list[dict[str, Any]] = []
    if archive_dir.exists() and article_terms:
        start = target_date - timedelta(days=context_days)
        for path in sorted(archive_dir.glob("*-DeepDive.md")):
            try:
                item_date = date.fromisoformat(path.name[:10])
            except ValueError:
                continue
            if not (start <= item_date < target_date):
                continue
            context = _context_from_archive(
                path=path,
                item_date=item_date,
                source_title="",
                source_terms=article_terms,
                source_title_terms=set(),
                source_markdown="",
            )
            if context is not None:
                candidates.append(context)
    candidates.sort(key=lambda item: (int(item["score"]), str(item["date"])), reverse=True)
    selected = candidates[:max_candidates]
    return {
        "schema_version": 1,
        "date": issue_date,
        "article_window_days": article_days,
        "context_window_days": context_days,
        "max_candidates": max_candidates,
        "relation_vocab": list(RELATION_KINDS),
        "low_signal_terms": sorted(LOW_SIGNAL_CONTEXT_TERMS),
        "article_signal_terms": sorted(article_terms)[:80],
        "candidates": selected,
    }


def write_context_pack(
    issue_date: str,
    *,
    repo_root: Path = REPO_ROOT,
    output: Path,
    article_days: int = DEFAULT_ARTICLE_DAYS,
    context_days: int = DEFAULT_CONTEXT_DAYS,
    max_candidates: int = MAX_CONTEXT_CANDIDATES,
) -> dict[str, Any]:
    pack = build_context_pack(
        issue_date,
        repo_root=repo_root,
        article_days=article_days,
        context_days=context_days,
        max_candidates=max_candidates,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(pack, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return pack


def _candidate_has_evidence(candidate: dict[str, Any]) -> bool:
    signal_terms = [str(term) for term in candidate.get("signal_terms") or [] if _is_signal_term(str(term))]
    evidence = candidate.get("evidence") or []
    return bool(signal_terms and evidence)


def _candidate_to_context(
    candidate: dict[str, Any],
    *,
    current_title: str,
    related: dict[str, str] | None = None,
) -> ContextSource | None:
    if related is None and not _candidate_has_evidence(candidate):
        return None
    item_date = str(candidate.get("date") or "").strip()
    title = str(candidate.get("title") or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", item_date) or not title:
        return None
    relation = str((related or {}).get("relation") or candidate.get("relation") or "").strip()
    if relation not in RELATION_KINDS:
        relation = "主役共有"
    signal_terms = [str(term) for term in candidate.get("signal_terms") or [] if _is_signal_term(str(term))]
    link = str((related or {}).get("link") or ", ".join(signal_terms[:3]) or "過去DeepDiveとの構造比較")
    change = str((related or {}).get("change") or "").strip()
    if not change:
        change = _context_change(related=None, context_title=title, current_title=current_title, relation=relation)
    return ContextSource(
        date=item_date,
        title=title,
        relation=relation,
        link=link,
        change=change,
        summary=str(candidate.get("summary_excerpt") or title),
        score=int(candidate.get("score") or 0) + (20 if related else 0),
    )


def _load_pack_candidates(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    candidates = data.get("candidates", []) if isinstance(data, dict) else []
    return [item for item in candidates if isinstance(item, dict)]


def context_sources_for_deepdive(
    source_markdown: str,
    *,
    source_name: str,
    archive_dir: Path | None = None,
    context_pack_path: Path | None = None,
    context_days: int = DEFAULT_CONTEXT_DAYS,
    min_sources: int = MIN_TTS_CONTEXT_SOURCES,
    max_sources: int = MAX_TTS_CONTEXT_SOURCES,
) -> list[ContextSource]:
    source_title = frontmatter_value(source_markdown, "title", "DeepDive")
    source_date = date_from_source_name(source_name, frontmatter_value(source_markdown, "date", "1970-01-01"))
    source_terms = tag_terms(frontmatter_tags(source_markdown)) | title_terms(source_title) | _extract_terms_from_any(body_sentences(source_markdown))
    source_title_terms = title_terms(source_title)
    related_map = related_by_date(source_markdown)
    for item_date in context_source_dates(source_markdown):
        related_map.setdefault(
            item_date,
            {"date": item_date, "relation": "主役共有", "link": "context_sources", "change": ""},
        )
    resolved_archive_dir = _archive_dir_for_source(source_name, archive_dir)
    source_filename = Path(source_name).name
    contexts_by_date: dict[str, ContextSource] = {}

    pack_candidates = _load_pack_candidates(context_pack_path)
    pack_by_date = {str(item.get("date")): item for item in pack_candidates if item.get("date")}
    for item_date, related in related_map.items():
        candidate = pack_by_date.get(item_date)
        if candidate:
            context = _candidate_to_context(candidate, current_title=source_title, related=related)
            if context is not None:
                contexts_by_date[context.date] = context
                continue
        path = resolved_archive_dir / f"{item_date}-DeepDive.md"
        if path.exists():
            context_dict = _context_from_archive(
                path=path,
                item_date=date.fromisoformat(item_date),
                source_title=source_title,
                source_terms=source_terms,
                source_title_terms=source_title_terms,
                source_markdown=source_markdown,
                related=related,
            )
            if context_dict is not None:
                context = _candidate_to_context(context_dict, current_title=source_title, related=related)
                if context is not None:
                    contexts_by_date[context.date] = context

    for candidate in pack_candidates:
        context = _candidate_to_context(candidate, current_title=source_title)
        if context is None:
            continue
        contexts_by_date.setdefault(context.date, context)
        if len(contexts_by_date) >= max_sources:
            break

    if len(contexts_by_date) < min_sources:
        start = source_date - timedelta(days=context_days)
        for path in sorted(resolved_archive_dir.glob("*-DeepDive.md")):
            if path.name == source_filename:
                continue
            try:
                item_date = date.fromisoformat(path.name[:10])
            except ValueError:
                continue
            if not (start <= item_date < source_date):
                continue
            context_dict = _context_from_archive(
                path=path,
                item_date=item_date,
                source_title=source_title,
                source_terms=source_terms,
                source_title_terms=source_title_terms,
                source_markdown=source_markdown,
            )
            if context_dict is None:
                continue
            context = _candidate_to_context(context_dict, current_title=source_title)
            if context is not None:
                contexts_by_date.setdefault(context.date, context)
            if len(contexts_by_date) >= max_sources:
                break

    selected = sorted(contexts_by_date.values(), key=lambda item: (item.score, item.date), reverse=True)[:max_sources]
    if len(selected) < min_sources:
        raise ValueError(f"関連DeepDive文脈不足: {len(selected)}件 (必要: {min_sources}件以上)")
    return selected


def context_frontmatter(contexts: list[ContextSource]) -> str:
    lines = ["context_sources:"]
    for context in contexts:
        lines.extend(
            [
                f"  - date: {_yaml_quote(context.date)}",
                f"    title: {_yaml_quote(context.title)}",
                f"    relation: {_yaml_quote(context.relation)}",
            ]
        )
    return "\n".join(lines)


def _yaml_quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DeepDive共通Context Packを生成します。")
    parser.add_argument("--date", required=True, help="対象日 YYYY-MM-DD")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--article-days", type=int, default=DEFAULT_ARTICLE_DAYS)
    parser.add_argument("--context-days", type=int, default=DEFAULT_CONTEXT_DAYS)
    args = parser.parse_args(argv)
    pack = write_context_pack(
        args.date,
        repo_root=args.repo_root,
        output=args.output,
        article_days=args.article_days,
        context_days=args.context_days,
    )
    print(f"[deepdive-context] wrote {args.output} candidates={len(pack['candidates'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
