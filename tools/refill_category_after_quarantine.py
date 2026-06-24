from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

from tools.config import CATEGORIES
from tools.publish_inventory import CATEGORY_PATHS
from tools.publish_inventory import scheduled_category_ids


MIN_PUBLISHABLE_COUNT = 1
TARGET_COUNT = 5


def refill_category_ids() -> list[str]:
    """Return refill target categories from the canonical site category config."""
    return [cat_id for cat_id in CATEGORIES if cat_id != "summary"]


def refill_category_ids_for_date(issue_date: str | None) -> list[str]:
    if issue_date:
        return list(scheduled_category_ids(issue_date))
    return refill_category_ids()


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
        newline="\n",
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    tmp.replace(path)


def _copy_if_exists(src: Path, dst_dir: Path) -> None:
    if not src.exists():
        return
    dst_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst_dir / src.name)


def _count_digest_cards(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.startswith("### ["))


def _format_digest_card(row: dict[str, Any]) -> str:
    score = row.get("score", 70)
    try:
        score_text = str(int(score))
    except (TypeError, ValueError):
        score_text = "70"
    title = str(row.get("title") or row.get("title_ja") or "reserve article").strip()
    url = str(row.get("url") or "").strip()
    source = str(row.get("source") or "Reserve").strip()
    published = str(row.get("published") or row.get("date") or "").strip()
    thumb = str(row.get("thumb") or row.get("thumbnail") or "").strip()
    summary = str(row.get("summary") or "reserve candidates exhausted after quarantine").strip()
    lines = [f"### [{score_text}] {title}", ""]
    meta = " · ".join(part for part in [published, f"📰 {source}"] if part)
    if url:
        meta = f"{meta} · 🔗 [元記事]({url})" if meta else f"🔗 [元記事]({url})"
    if meta:
        lines.extend([meta, ""])
    if thumb:
        lines.extend([f"![thumb]({thumb})", ""])
    if summary:
        lines.append(f"- **補充採用**: {summary}")
    return "\n".join(lines).rstrip()


def _drop_bad_digest_cards(text: str, bad_urls: set[str]) -> list[str]:
    lines = text.splitlines()
    kept: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith("### ["):
            block: list[str] = []
            while i < len(lines):
                block.append(lines[i])
                i += 1
                if i < len(lines) and lines[i].lstrip().startswith("### ["):
                    break
            if any(url in "\n".join(block) for url in bad_urls):
                continue
            kept.extend(block)
            continue
        if not any(url in line for url in bad_urls):
            kept.append(line)
        i += 1
    return kept


def _category_folder(category: str) -> str:
    return CATEGORY_PATHS[category]["digest_folder"]


def _published_day(row: dict[str, Any], fallback: str) -> str:
    for key in ("published_date", "published", "pubDate", "date"):
        value = row.get(key)
        if isinstance(value, str) and len(value) >= 10:
            head = value[:10]
            if len(head) == 10 and head[4] == "-" and head[7] == "-":
                return head
    return fallback


def _normalize_refill_record(row: dict[str, Any], *, date: str, category: str) -> dict[str, Any]:
    """dedup raw candidate を reporter/articles record として使える形に寄せる。"""
    normalized = dict(row)
    genre = _category_folder(category)
    title = str(normalized.get("title") or normalized.get("title_ja") or "reserve article").strip()
    published_day = _published_day(normalized, date)
    normalized["date"] = date
    normalized["genre"] = str(normalized.get("genre") or genre)
    normalized["title"] = title
    normalized["title_ja"] = str(normalized.get("title_ja") or title)
    normalized["thumb"] = normalized.get("thumb")
    normalized["published_date"] = str(normalized.get("published_date") or published_day)
    normalized["published"] = str(normalized.get("published") or published_day)
    normalized["date_evidence_source"] = str(
        normalized.get("date_evidence_source")
        or ("rss-pubdate" if normalized.get("pubDate") else "refill-candidate")
    )
    return normalized


def _is_publishable_refill_candidate(row: dict[str, Any]) -> bool:
    url = str(row.get("url") or "")
    if not url.startswith("http"):
        return False
    if "news.google.com/rss/articles/" in url:
        return False
    if str(row.get("google_news_decode_status") or "").casefold() == "unresolved":
        return False
    if str(row.get("url_resolution_action") or "") == "reporter_must_resolve_canonical":
        return False
    thumb = row.get("thumb")
    if not isinstance(thumb, str) or not thumb.startswith("http"):
        return False
    title = str(row.get("title") or row.get("title_ja") or "").strip()
    return bool(title)


def _paths(repo_root: Path, date: str, category: str) -> dict[str, Path]:
    folder = _category_folder(category)
    return {
        "digest": repo_root / "digest" / folder / f"{date}-{folder}.md",
        "records": repo_root / "tmp" / "newsroom" / date / f"{category}.records.jsonl",
        "articles": repo_root / "data" / "articles.jsonl",
        "audit": repo_root / "data" / "search_audit" / date / f"{category}.json",
    }


def _backup(paths: dict[str, Path], repo_root: Path, date: str, txid: str) -> Path:
    before = repo_root / "build" / "repair-transactions" / date / txid / "before"
    for path in paths.values():
        _copy_if_exists(path, before)
    return before


def _rollback(paths: dict[str, Path], before: Path) -> None:
    for key, path in paths.items():
        saved = before / path.name
        if saved.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(saved, path)


def _candidate_paths(candidate_dir: Path, category: str) -> list[Path]:
    return [
        candidate_dir / f"{category}_candidates.jsonl",
        candidate_dir / f"{category}.jsonl",
    ]


def _reserve_candidates(candidate_dir: Path, category: str) -> list[dict[str, Any]]:
    for path in _candidate_paths(candidate_dir, category):
        if path.exists():
            return _jsonl(path)
    return []


def _sync_digest(path: Path, bad_urls: set[str], selected: list[dict[str, Any]]) -> None:
    if not path.exists():
        return
    kept = _drop_bad_digest_cards(path.read_text(encoding="utf-8-sig", errors="replace"), bad_urls)
    for row in selected:
        if str(row.get("url") or ""):
            kept.extend(["", _format_digest_card(row), "---"])
    path.write_text("\n".join(kept).rstrip() + "\n", encoding="utf-8", newline="\n")


def _sync_articles_jsonl(
    path: Path,
    bad_urls: set[str],
    selected: list[dict[str, Any]],
    drop_extra_urls: set[str] | None = None,
) -> None:
    drop_urls = set(bad_urls)
    if drop_extra_urls:
        drop_urls.update(drop_extra_urls)
    rows = _jsonl(path)
    selected_by_url = {str(row.get("url") or ""): row for row in selected if str(row.get("url") or "")}
    kept: list[dict[str, Any]] = []
    written_selected: set[str] = set()
    for row in rows:
        url = str(row.get("url") or "")
        if url in drop_urls:
            continue
        if url in selected_by_url:
            kept.append(selected_by_url[url])
            written_selected.add(url)
            continue
        kept.append(row)
    for url, row in selected_by_url.items():
        if url not in written_selected:
            kept.append(row)
    _write_jsonl(path, kept)


def _read_bad_urls(path: Path | None) -> list[str]:
    if path is None:
        return []
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8-sig")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return [line.strip() for line in text.splitlines() if line.strip()]
    if isinstance(data, list):
        return [str(item.get("url") if isinstance(item, dict) else item) for item in data]
    return []


def _read_audit(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _audit_dropped_urls(audit: dict[str, Any]) -> set[str]:
    dropped = audit.get("dropped")
    if not isinstance(dropped, list):
        return set()
    urls: set[str] = set()
    for row in dropped:
        if isinstance(row, dict) and row.get("url"):
            urls.add(str(row["url"]))
    return urls


def refill_category(
    *,
    repo_root: Path,
    date: str,
    category: str,
    bad_urls: list[str],
    candidate_dir: Path,
    txid: str,
) -> dict[str, Any]:
    paths = _paths(repo_root, date, category)
    before = _backup(paths, repo_root, date, txid)
    audit = _read_audit(paths["audit"])
    bad = {url for url in bad_urls if url}
    rejected = _audit_dropped_urls(audit)
    records = _jsonl(paths["records"])
    if not records and not paths["digest"].exists():
        return {"ok": True, "mode": "skipped", "reason": "category_not_scheduled", "selected_total": 0}
    original_count = len(records)
    kept = [row for row in records if str(row.get("url") or "") not in bad]
    removed = [row for row in records if str(row.get("url") or "") in bad]
    current_urls = {str(row.get("url") or "") for row in kept}

    selected: list[dict[str, Any]] = []
    skipped_unpublishable: set[str] = set()
    for candidate in _reserve_candidates(candidate_dir, category):
        url = str(candidate.get("url") or "")
        if not url or url in bad or url in rejected or url in current_urls:
            continue
        if not _is_publishable_refill_candidate(candidate):
            skipped_unpublishable.add(url)
            continue
        selected.append(_normalize_refill_record(candidate, date=date, category=category))
        current_urls.add(url)
        if len(kept) + len(selected) >= min(TARGET_COUNT, original_count):
            break

    final_rows = kept + selected
    if len(final_rows) < MIN_PUBLISHABLE_COUNT:
        _rollback(paths, before)
        return {
            "ok": False,
            "reason": "blocked_refill_unresolved",
            "selected_total": len(final_rows),
            "removed": len(removed),
            "refilled": len(selected),
        }

    mode = "refilled" if selected else "shortfall"
    if len(final_rows) < TARGET_COUNT:
        reason = "reserve candidates exhausted after quarantine"
        for row in final_rows:
            row.setdefault("quality_shortfall_reason", reason)

    _write_jsonl(paths["records"], final_rows)
    _sync_digest(paths["digest"], bad, selected)
    _sync_articles_jsonl(paths["articles"], bad, selected, skipped_unpublishable)

    dropped = audit.get("dropped")
    if not isinstance(dropped, list):
        dropped = []
    for row in removed:
        dropped.append({"url": row.get("url"), "reason": "quarantined by gate"})
    digest_card_count = len(final_rows)
    if paths["digest"].exists():
        digest_card_count = _count_digest_cards(paths["digest"].read_text(encoding="utf-8-sig", errors="replace"))
    audit.update(
        {
            "category_id": category,
            "date": date,
            "selected_total": digest_card_count,
            "dropped": dropped,
        }
    )
    if len(final_rows) < TARGET_COUNT:
        audit["quality_shortfall_reason"] = "reserve candidates exhausted after quarantine"
    _write_json(paths["audit"], audit)

    return {
        "ok": True,
        "mode": mode,
        "selected_total": digest_card_count,
        "removed": len(removed),
        "refilled": len(selected),
        "transaction": str(before.parent),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refill a category after article quarantine.")
    parser.add_argument("--list-categories", action="store_true", help="Print canonical refill category ids as JSON and exit.")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--date")
    parser.add_argument("--category")
    parser.add_argument("--bad-url-file", type=Path)
    parser.add_argument("--bad-url", action="append", default=[])
    parser.add_argument("--candidate-dir", type=Path, default=Path("build") / "deduped-candidates")
    parser.add_argument("--txid", default="manual")
    args = parser.parse_args(argv)

    if args.list_categories:
        print(json.dumps(refill_category_ids_for_date(args.date), ensure_ascii=False))
        return 0

    if not args.date or not args.category:
        parser.error("--date and --category are required unless --list-categories is used")
    missing_paths = [cat_id for cat_id in refill_category_ids() if cat_id not in CATEGORY_PATHS]
    if missing_paths:
        parser.error(f"refill category path config missing for: {', '.join(missing_paths)}")

    bad_urls = list(args.bad_url) + _read_bad_urls(args.bad_url_file)
    result = refill_category(
        repo_root=args.repo_root,
        date=args.date,
        category=args.category,
        bad_urls=bad_urls,
        candidate_dir=args.repo_root / args.candidate_dir if not args.candidate_dir.is_absolute() else args.candidate_dir,
        txid=args.txid,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
