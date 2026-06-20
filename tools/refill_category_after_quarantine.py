from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

from tools.publish_inventory import CATEGORY_PATHS


MIN_SHORTFALL_COUNT = 3
TARGET_COUNT = 5


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


def _category_folder(category: str) -> str:
    return CATEGORY_PATHS[category]["digest_folder"]


def _paths(repo_root: Path, date: str, category: str) -> dict[str, Path]:
    folder = _category_folder(category)
    return {
        "digest": repo_root / "digest" / folder / f"{date}-{folder}.md",
        "records": repo_root / "tmp" / "newsroom" / date / f"{category}.records.jsonl",
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
    kept = [
        line
        for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        if not any(url in line for url in bad_urls)
    ]
    for row in selected:
        title = str(row.get("title") or row.get("title_ja") or "reserve article")
        url = str(row.get("url") or "")
        if url:
            kept.append(f"- {title} {url}")
    path.write_text("\n".join(kept).rstrip() + "\n", encoding="utf-8", newline="\n")


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
    bad = {url for url in bad_urls if url}
    records = _jsonl(paths["records"])
    if not records and not paths["digest"].exists():
        return {"ok": True, "mode": "skipped", "reason": "category_not_scheduled", "selected_total": 0}
    original_count = len(records)
    kept = [row for row in records if str(row.get("url") or "") not in bad]
    removed = [row for row in records if str(row.get("url") or "") in bad]
    current_urls = {str(row.get("url") or "") for row in kept}

    selected: list[dict[str, Any]] = []
    for candidate in _reserve_candidates(candidate_dir, category):
        url = str(candidate.get("url") or "")
        if not url or url in bad or url in current_urls:
            continue
        selected.append(candidate)
        current_urls.add(url)
        if len(kept) + len(selected) >= min(TARGET_COUNT, original_count):
            break

    final_rows = kept + selected
    if len(final_rows) < MIN_SHORTFALL_COUNT:
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

    audit: dict[str, Any] = {}
    if paths["audit"].exists():
        try:
            audit = json.loads(paths["audit"].read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            audit = {}
    dropped = audit.get("dropped")
    if not isinstance(dropped, list):
        dropped = []
    for row in removed:
        dropped.append({"url": row.get("url"), "reason": "quarantined by gate"})
    audit.update(
        {
            "category_id": category,
            "date": date,
            "selected_total": len(final_rows),
            "dropped": dropped,
        }
    )
    if len(final_rows) < TARGET_COUNT:
        audit["quality_shortfall_reason"] = "reserve candidates exhausted after quarantine"
    _write_json(paths["audit"], audit)

    return {
        "ok": True,
        "mode": mode,
        "selected_total": len(final_rows),
        "removed": len(removed),
        "refilled": len(selected),
        "transaction": str(before.parent),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refill a category after article quarantine.")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--date", required=True)
    parser.add_argument("--category", required=True)
    parser.add_argument("--bad-url-file", type=Path)
    parser.add_argument("--bad-url", action="append", default=[])
    parser.add_argument("--candidate-dir", type=Path, default=Path("build") / "deduped-candidates")
    parser.add_argument("--txid", required=True)
    args = parser.parse_args(argv)

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
