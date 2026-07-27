#!/usr/bin/env python3
"""digest md の記事カード URL と articles.jsonl の当日 URL が一致するか突合する gate。

# 検証する「なぜ重要か」

2026-06-12 号で digest md と `data/articles.jsonl` がずれた。片方向だけ見ると
「md には出たが jsonl に無い」append 漏れは検出できるが、freshness gate が正しく
古記事を jsonl から落としたのに digest md だけに古記事が残るケースを append 漏れと
誤判定してしまう。

本 gate は当日号のカテゴリ digest md からカード URL (`[元記事](URL)`) を抽出し、
当日号 (date == issue_date) の articles.jsonl URL 集合と **完全一致** することを突合する。
digest-only URL は「古記事が md に残った / append 漏れ」のどちらもあり得るため fatal。
articles-only URL は「jsonl にはあるがカード生成漏れ」として fatal。
freshness 済み append 集合と公開 md を一致させる境界 gate である。

対象は articles.jsonl の category record と対応するカテゴリ digest md のみ
(AI / FX / IT-Consulting / Mobility / Manufacturing / Economy / Game)。DeepDive / Summary
は articles.jsonl の category record とは別管理なので除外する。

CLI:
  python -m tools.validate_digest_articles_reconcile --issue-date 2026-06-12

ファイル名 `2026-06-12-AI.md` 形式の md が対象。digest/data の既定はリポジトリ配下。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any
from pathlib import Path, PurePosixPath

from tools.publish_inventory import CATEGORY_PATHS, scheduled_category_ids

_PKG_ROOT = Path(__file__).resolve().parent.parent

# digest md カードの正典 URL は `🔗 [元記事](https://...)`。thumb (`![thumb](...)`) や
# wikilink (`[[...]]`) は対象外なので、リンクテキストが「元記事」のものだけを拾う。
_GENMOTO_RE = re.compile(r"\[元記事\]\((https?://[^)\s]+)\)")

# articles.jsonl の category record と突合するカテゴリ digest のみ対象にする。
_EXCLUDE_DIRS: frozenset[str] = frozenset({"DeepDive", "Summary"})

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def resolve_reporter_artifact_path(
    repo_root: Path,
    issue_date: str,
    artifact: str,
) -> tuple[str, Path]:
    """manifest の reporter record を current run の repo 内 scope へ制約する。"""
    raw = artifact.strip().replace("\\", "/")
    relative = PurePosixPath(raw)
    parts = relative.parts
    if (
        not raw
        or relative.is_absolute()
        or Path(raw).is_absolute()
        or ".." in parts
        or len(parts) != 4
        or parts[:3] != ("tmp", "newsroom", issue_date)
        or not parts[3].endswith(".records.jsonl")
    ):
        raise ValueError(f"outside allowed reporter scope: {artifact}")

    root = repo_root.resolve()
    path = (root / Path(*parts)).resolve(strict=False)
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"outside repo root: {artifact}") from exc
    return relative.as_posix(), path


def _normalize_url(url: str) -> str:
    """突合用に URL を正規化 (前後空白除去 + 末尾スラッシュ除去)。"""
    return url.strip().rstrip("/")


def digest_card_urls(digest_dir: Path, issue_date: str) -> dict[str, list[str]]:
    """{genre: [card url, ...]} を返す。当日号のカテゴリ digest md のみ走査。"""
    out: dict[str, list[str]] = {}
    scheduled_folders = {
        CATEGORY_PATHS[cat_id]["digest_folder"]
        for cat_id in scheduled_category_ids(issue_date)
    }
    for md in sorted(digest_dir.glob(f"*/{issue_date}-*.md")):
        genre = md.parent.name
        if genre in _EXCLUDE_DIRS:
            continue
        if genre not in scheduled_folders:
            continue
        text = md.read_text(encoding="utf-8-sig", errors="replace")
        urls = [_normalize_url(u) for u in _GENMOTO_RE.findall(text)]
        if urls:
            out[genre] = urls
    return out


def digest_card_evidence(digest_dir: Path, issue_date: str) -> dict[str, dict[str, str]]:
    """digest URL ごとの category / artifact 証拠を返す。"""
    out: dict[str, dict[str, str]] = {}
    scheduled_folders = {
        CATEGORY_PATHS[cat_id]["digest_folder"]
        for cat_id in scheduled_category_ids(issue_date)
    }
    for md in sorted(digest_dir.glob(f"*/{issue_date}-*.md")):
        genre = md.parent.name
        if genre in _EXCLUDE_DIRS or genre not in scheduled_folders:
            continue
        text = md.read_text(encoding="utf-8-sig", errors="replace")
        target = f"digest/{genre}/{md.name}"
        for url in _GENMOTO_RE.findall(text):
            normalized = _normalize_url(url)
            out[normalized] = {
                "category": genre,
                "target_digest_path": target,
            }
    return out


def articles_records_for_issue(
    articles_path: Path,
    issue_date: str,
) -> dict[str, dict[str, Any]]:
    """当日 articles.jsonl の URL -> record を返す。"""
    records: dict[str, dict[str, Any]] = {}
    if not articles_path.exists():
        return records
    scheduled_folders = {
        CATEGORY_PATHS[cat_id]["digest_folder"]
        for cat_id in scheduled_category_ids(issue_date)
    }
    with articles_path.open(encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict) or rec.get("date") != issue_date:
                continue
            genre = str(rec.get("genre") or "unknown")
            url = rec.get("url")
            if genre not in scheduled_folders or not isinstance(url, str) or not url.strip():
                continue
            records[_normalize_url(url)] = dict(rec)
    return records


def articles_urls_for_issue(articles_path: Path, issue_date: str) -> dict[str, str]:
    """当日号 (date == issue_date) の articles.jsonl URL -> genre (正規化済)。"""
    return {
        url: str(record.get("genre") or "unknown")
        for url, record in articles_records_for_issue(articles_path, issue_date).items()
    }


def current_reporter_records_for_issue(
    repo_root: Path,
    issue_date: str,
) -> dict[str, tuple[dict[str, Any], str]] | None:
    """current reporter URL -> (record, repo-relative artifact) を返す。"""
    manifest = repo_root / "build" / "reporter-artifacts" / issue_date / "editor-input-manifest.json"
    if not manifest.exists():
        return None
    try:
        data = json.loads(manifest.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None

    scheduled_ids = data.get("scheduled_categories")
    if not isinstance(scheduled_ids, list) or not scheduled_ids:
        scheduled_ids = scheduled_category_ids(issue_date)
    scheduled_folders = {
        CATEGORY_PATHS[cat_id]["digest_folder"]
        for cat_id in scheduled_ids
        if cat_id in CATEGORY_PATHS
    }
    artifacts = data.get("reporter_artifacts")
    if not isinstance(artifacts, list):
        return None

    records: dict[str, tuple[dict[str, Any], str]] = {}
    for rel in artifacts:
        if not isinstance(rel, str) or not rel.strip():
            continue
        try:
            normalized_rel, path = resolve_reporter_artifact_path(
                repo_root,
                issue_date,
                rel,
            )
        except ValueError:
            return None
        if not path.exists():
            continue
        with path.open(encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(rec, dict) or rec.get("date") != issue_date:
                    continue
                genre = str(rec.get("genre") or "unknown")
                url = rec.get("url")
                if genre not in scheduled_folders or not isinstance(url, str) or not url.strip():
                    continue
                records[_normalize_url(url)] = (dict(rec), normalized_rel)
    return records


def current_reporter_urls_for_issue(repo_root: Path, issue_date: str) -> dict[str, str] | None:
    """現在 run の reporter artifact URL -> genre を返す。

    `data/articles.jsonl` は append-only で、同一号日の再実行前 record も残り得る。
    editor-input-manifest がある場合は、その manifest が指す reporter records を
    「今回 publish 直前に digest と一致すべき集合」として使う。manifest が無い古い
    fixture / 手動実行では None を返し、従来の articles.jsonl 当日全件突合に戻す。
    """
    records = current_reporter_records_for_issue(repo_root, issue_date)
    if records is None:
        return None
    return {
        url: str(record.get("genre") or "unknown")
        for url, (record, _) in records.items()
    }


def reconcile(
    digest_dir: Path,
    articles_path: Path,
    issue_date: str,
) -> dict[str, list[dict[str, Any]]]:
    """digest md カード URL と articles.jsonl URL の完全一致を検査。

    Returns:
        {
          "digest_only": md にあり current reporter/current articles に無い structured issue,
          "articles_only": current reporter/current articles にあり md に無い structured issue,
        }
        両方空なら公開 md と freshness 済み articles.jsonl が一致 = 突合 OK。
    """
    digest_index = digest_card_evidence(digest_dir, issue_date)
    articles_records = articles_records_for_issue(articles_path, issue_date)
    jsonl_urls = {
        url: str(record.get("genre") or "unknown")
        for url, record in articles_records.items()
    }
    repo_root = digest_dir.parent if digest_dir.name == "digest" else _PKG_ROOT
    current_records = current_reporter_records_for_issue(repo_root, issue_date)
    compare_records: dict[str, tuple[dict[str, Any], str]] = (
        current_records
        if current_records is not None
        else {url: (record, "data/articles.jsonl") for url, record in articles_records.items()}
    )

    digest_only: list[dict[str, Any]] = []
    for url, card in sorted(
        digest_index.items(),
        key=lambda item: (item[1]["category"], item[0]),
    ):
        if url in compare_records and url in jsonl_urls:
            continue
        category = card["category"]
        target = card["target_digest_path"]
        reporter_entry = compare_records.get(url)
        digest_only.append(
            {
                "gate_id": "digest-articles-reconcile",
                "issue_code": "digest_articles_digest_only",
                "direction": "digest_only",
                "message": f"{category}: {url}",
                "issue_date": issue_date,
                "category": category,
                "url": url,
                "artifact_paths": [
                    target,
                    "data/articles.jsonl",
                    *([reporter_entry[1]] if reporter_entry else []),
                ],
                "evidence": {
                    "direction": "digest_only",
                    "url": url,
                    "target_digest_path": target,
                    "current_reporter_manifest_present": current_records is not None,
                    "in_current_reporter": current_records is not None and url in current_records,
                    "in_current_articles": url in articles_records,
                    "record_source": reporter_entry[1] if reporter_entry else "",
                    "record": reporter_entry[0] if reporter_entry else articles_records.get(url),
                },
            }
        )

    articles_only: list[dict[str, Any]] = []
    for url, (record, record_source) in sorted(
        compare_records.items(),
        key=lambda item: (str(item[1][0].get("genre") or "unknown"), item[0]),
    ):
        if url in digest_index:
            continue
        category = str(record.get("genre") or "unknown")
        target = f"digest/{category}/{issue_date}-{category}.md"
        articles_only.append(
            {
                "gate_id": "digest-articles-reconcile",
                "issue_code": "digest_articles_articles_only",
                "direction": "articles_only",
                "message": f"{category}: {url}",
                "issue_date": issue_date,
                "category": category,
                "url": url,
                "artifact_paths": [target, record_source],
                "evidence": {
                    "direction": "articles_only",
                    "url": url,
                    "record_source": (
                        "current_reporter"
                        if current_records is not None
                        else "current_articles"
                    ),
                    "record_artifact": record_source,
                    "record": record,
                    "target_digest_path": target,
                },
            }
        )
    return {"digest_only": digest_only, "articles_only": articles_only}


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    ap = argparse.ArgumentParser(
        description="digest md カード URL と articles.jsonl 当日 URL が一致するか突合する gate"
    )
    ap.add_argument("--issue-date", required=True, help="号日 (YYYY-MM-DD)")
    ap.add_argument("--digest-dir", type=Path, default=_PKG_ROOT / "digest")
    ap.add_argument("--articles", type=Path, default=_PKG_ROOT / "data" / "articles.jsonl")
    args = ap.parse_args(argv)

    if not _DATE_RE.match(args.issue_date):
        print(f"FATAL: --issue-date は 'YYYY-MM-DD' 形式: got {args.issue_date!r}", file=sys.stderr)
        return 2
    if not args.articles.exists():
        print(f"FATAL: articles.jsonl not found: {args.articles}", file=sys.stderr)
        return 2

    result = reconcile(args.digest_dir, args.articles, args.issue_date)
    digest_only = result["digest_only"]
    articles_only = result["articles_only"]
    if digest_only or articles_only:
        print(
            json.dumps(
                {
                    "ok": False,
                    "gate_id": "digest-articles-reconcile",
                    "issue_date": args.issue_date,
                    "counts": {
                        "digest_only": len(digest_only),
                        "articles_only": len(articles_only),
                    },
                    "issues": [*digest_only, *articles_only],
                },
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 1

    print(f"PASS: digest md カード URL と articles.jsonl 当日 URL は一致 (号日 {args.issue_date})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
