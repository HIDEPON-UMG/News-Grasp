#!/usr/bin/env python3
"""日次 digest の公開前品質を検査する gate。"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

from tools.dedup import extract_source_date_from_url
from tools.generate_pages import (
    CATEGORIES,
    is_category_scheduled_on,
    parse_articles,
    parse_frontmatter,
    parse_reflection,
)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_WEEKDAY_JA = ["月曜日", "火曜日", "水曜日", "木曜日", "金曜日", "土曜日", "日曜日"]

# dedup 強化版 (鮮度ゲートで published_date / date_evidence_source 注釈を刻印) の
# 適用開始日。この日より前の articles.jsonl レコードは注釈が無くて当然なので、
# 「全件注釈ゼロ = 強化版を通っていない疑い」の警告検査を適用しない。
_DATE_EVIDENCE_ANNOTATION_START = date(2026, 6, 11)
_DATE_EVIDENCE_SOURCE_FIELD = "date_evidence_source"

REQUIRED_COVERAGE_TERMS: dict[str, set[str]] = {
    "ai": {"OpenAI", "Anthropic", "Google", "Apple", "Microsoft", "Meta", "NVIDIA"},
    "fx": {"USDJPY", "EURUSD", "BOJ", "Fed", "ECB"},
    "it": {"McKinsey", "BCG", "Accenture", "Deloitte", "PwC", "NTT"},
    "mobility": {"Tesla", "Waymo", "BYD", "Toyota", "Uber"},
    "game": {"Nintendo", "Switch 2", "Sony", "Capcom", "Square Enix"},
    "manufacturing": {"TSMC", "Samsung", "Intel", "NVIDIA", "Foxconn", "Toyota"},
    "economy": {"Nikkei", "S&P 500", "Fed", "BOJ", "SoftBank", "NVIDIA"},
}


def _parse_issue_date(value: str) -> date:
    if not _DATE_RE.match(value):
        raise ValueError(f"date は YYYY-MM-DD で指定してください: {value}")
    return date.fromisoformat(value)


def validate_summary_hero(summary_path: Path) -> list[str]:
    """LP hero がブランド文言 fallback に落ちないための短文句を検査する。"""
    if not summary_path.exists():
        return [f"Summary digest が存在しません: {summary_path}"]
    fm, _body = parse_frontmatter(summary_path.read_text(encoding="utf-8-sig", errors="replace"))
    left = (fm.get("hero_left") or "").strip()
    right = (fm.get("hero_right") or "").strip()
    if left and right:
        return []
    return [
        f"{summary_path}: frontmatter hero_left / hero_right が不足しています。",
        "このままでは LP TODAY'S THEME 見出しが「時勢を掴み、日々に新たに。」へ fallback します。",
    ]


def validate_issue_thumbnail_coverage(jsonl_path: Path, issue: date) -> list[str]:
    """当日号の全カードがサムネ fallback へ退化しないことを検査する。"""
    if not jsonl_path.exists():
        return []
    records: list[dict[str, Any]] = []
    for lineno, line in enumerate(jsonl_path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as e:
            return [f"{jsonl_path}: line {lineno}: JSON decode error: {e}"]
        if isinstance(rec, dict) and rec.get("date") == issue.isoformat():
            records.append(rec)
    if not records:
        return []
    google_news_urls = [
        rec.get("url")
        for rec in records
        if isinstance(rec.get("url"), str)
        and "news.google.com/rss/articles/" in rec["url"]
    ]
    if google_news_urls:
        return [
            f"{jsonl_path}: {issue.isoformat()} に Google News RSS URL のままです: {len(google_news_urls)} 件",
            "元記事 URL へ解決してから公開してください。Google News URL のままだと元記事 OGP を取り逃し、fallback サムネ化します。",
        ]
    if all("thumb" in rec for rec in records) and all(rec.get("thumb") is None for rec in records):
        return [
            f"{jsonl_path}: {issue.isoformat()} の thumb が全件 null です。",
            "このままでは公開ページが全件 fallback サムネになります。fetch_ogp / WebSearch thumbnail の取得結果を反映してください。",
        ]
    return []


def _missing_emphasis_kinds(text: str) -> list[str]:
    missing: list[str] = []
    if "[[" not in text or "]]" not in text:
        missing.append("[[ ]] marker")
    if "**" not in text:
        missing.append("** ** bold")
    if "__" not in text:
        missing.append("__ __ underline")
    return missing


def validate_summary_emphasis(summary_path: Path) -> list[str]:
    """考察 lead / § 本文が 3 階層の強調記法を使っているか検査する。"""
    if not summary_path.exists():
        return []
    _fm, body = parse_frontmatter(summary_path.read_text(encoding="utf-8-sig", errors="replace"))
    reflection = parse_reflection(body)
    lead = str(reflection.get("lead") or "")
    sections = reflection.get("sections") or {}
    if not lead and not sections:
        return []

    errs: list[str] = []
    missing = _missing_emphasis_kinds(lead)
    if missing:
        errs.append(
            f"{summary_path}: reflection lead lacks required emphasis: {', '.join(missing)}"
        )
    for num, sec in sorted(sections.items()):
        body_text = str((sec or {}).get("body") or "")
        missing = _missing_emphasis_kinds(body_text)
        if missing:
            errs.append(
                f"{summary_path}: reflection section §{num:02d} lacks required emphasis: {', '.join(missing)}"
            )
    return errs


def validate_card_emphasis_coverage(digest_root: Path, issue: date) -> list[str]:
    """カテゴリカード本文が 3 階層の強調記法を含むか検査する。"""
    errs: list[str] = []
    for md in sorted(digest_root.glob(f"*/*{issue.isoformat()}*.md")):
        if md.parent.name in {"Summary", "DeepDive"}:
            continue
        _fm, body = parse_frontmatter(md.read_text(encoding="utf-8-sig", errors="replace"))
        blocks = re.split(r"\r?\n---\r?\n", body)
        card_idx = 0
        for block in blocks:
            if "### " not in block:
                continue
            card_idx += 1
            bullet_text = "\n".join(
                line for line in block.splitlines()
                if line.lstrip().startswith("- ")
            )
            missing = _missing_emphasis_kinds(bullet_text)
            if missing:
                errs.append(
                    f"{md}: card #{card_idx:02d} lacks required emphasis: {', '.join(missing)}"
                )
    return errs


def validate_issue_schedule(digest_root: Path, issue: date) -> list[str]:
    """日付の曜日と配信スケジュールに対してカテゴリ過不足を検査する。"""
    summary_path = digest_root / "Summary" / f"{issue.isoformat()}.md"
    if not summary_path.exists():
        return []
    fm, _body = parse_frontmatter(summary_path.read_text(encoding="utf-8-sig", errors="replace"))
    weekday = str(fm.get("weekday") or "").strip()
    if not weekday:
        return []

    errs: list[str] = []
    expected_weekday = _WEEKDAY_JA[issue.weekday()]
    if weekday != expected_weekday:
        errs.append(
            f"{summary_path}: weekday={weekday} does not match date {issue.isoformat()} ({expected_weekday})."
        )

    expected = {
        cat_id for cat_id in CATEGORIES
        if cat_id != "summary" and is_category_scheduled_on(cat_id, issue.isoformat())
    }
    present: set[str] = set()
    for md in sorted(digest_root.glob(f"*/*{issue.isoformat()}*.md")):
        if md.parent.name in {"Summary", "DeepDive"}:
            continue
        cat_fm, _body = parse_frontmatter(md.read_text(encoding="utf-8-sig", errors="replace"))
        cat_id = str(cat_fm.get("categoryId") or "").strip().casefold()
        if cat_id:
            present.add(cat_id)

    _missing = sorted(expected - present)
    _extra = sorted(present - expected)
    return errs


def _stale_source_url_errors(*, issue: date, label: str, title: str, url: str) -> list[str]:
    src_date = extract_source_date_from_url(url)
    allowed_oldest = date.fromordinal(issue.toordinal() - 1)
    if src_date is None or src_date >= allowed_oldest:
        return []
    age = (issue - src_date).days
    return [
        f"{label}: source URL date {src_date.isoformat()} is {age} day(s) older than issue {issue.isoformat()} and outside the 1-day edition window: {title}",
        f"  url={url}",
    ]


def _article_meta_date(value: str) -> date | None:
    """digest のメタ行から parse_articles が抜いた `YYYY-MM-DD ...` の日付部分を返す。"""
    if not value or len(value) < 10:
        return None
    head = value[:10]
    if not _DATE_RE.match(head):
        return None
    try:
        return date.fromisoformat(head)
    except ValueError:
        return None


def _stale_top_article_errors(*, issue: date, label: str, article: dict[str, Any]) -> list[str]:
    """カテゴリ先頭記事が当日/前日窓を外れていれば落とす。

    LP/カテゴリページの TOP STORY は digest の先頭記事を大きく表示するため、
    ここに古い再掲が入ると「今日号なのに一週間前の記事が主役」になる。
    URL に日付が無いソースでも、digest メタ行の日付で防ぐ。
    """
    meta_date = _article_meta_date(str(article.get("date") or ""))
    allowed_oldest = date.fromordinal(issue.toordinal() - 1)
    if meta_date is None or meta_date >= allowed_oldest:
        return []
    age = (issue - meta_date).days
    return [
        f"{label}: top article date {meta_date.isoformat()} is {age} day(s) older than issue {issue.isoformat()}: {article.get('title') or ''}",
        "  TOP STORY must be today's or yesterday's article. Move the item down, replace it with a fresh article, or mark the digest as intentionally short.",
    ]


def _stale_followup_errors(*, issue: date, label: str, title: str, record: dict[str, Any]) -> list[str]:
    """古い記事を follow-up 扱いで当日掲載する再掲を検出する。"""
    if not record.get("is_followup"):
        return []
    matched_with = str(record.get("matched_with") or "").strip()
    matched_date = extract_source_date_from_url(matched_with)
    if matched_date is None or matched_date >= issue:
        return []
    review_note = str(record.get("followup_review_note") or "").strip()
    if review_note:
        return []
    age = (issue - matched_date).days
    return [
        f"{label}: follow-up matched_with URL date {matched_date.isoformat()} is {age} day(s) older than issue {issue.isoformat()}: {title}",
        f"  matched_with={matched_with}",
        "  add followup_review_note for a verified new-material follow-up, or remove the record as stale.",
    ]


def validate_digest_source_freshness(digest_root: Path, issue: date) -> list[str]:
    """当日カテゴリ digest の記事 URL パス日付が前日以前なら落とす。"""
    errs: list[str] = []
    for md in sorted(digest_root.glob(f"*/*{issue.isoformat()}*.md")):
        if md.parent.name in {"Summary", "DeepDive"}:
            continue
        fm, body = parse_frontmatter(md.read_text(encoding="utf-8-sig", errors="replace"))
        cat = fm.get("categoryId") or fm.get("category") or md.parent.name
        articles = parse_articles(body)
        if articles:
            errs.extend(_stale_top_article_errors(
                issue=issue,
                label=f"{md} [{cat} TOP]",
                article=articles[0],
            ))
        for idx, article in enumerate(articles, 1):
            url = article.get("source_url") or ""
            errs.extend(_stale_source_url_errors(
                issue=issue,
                label=f"{md} [{cat} #{idx:02d}]",
                title=article.get("title") or "",
                url=url,
            ))
    return errs


def validate_digest_article_counts(digest_root: Path, issue: date, *, min_articles: int = 5) -> list[str]:
    """当日カテゴリ digest が5件目標または品質理由付き不足を満たすか検査する。"""
    return []


def _search_audit_path(audit_root: Path, issue: date, cat_id: str) -> Path:
    return audit_root / issue.isoformat() / f"{cat_id}.json"


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def validate_search_audit_for_shortfall(
    *,
    digest_root: Path,
    audit_root: Path,
    issue: date,
    min_articles: int = 5,
) -> list[str]:
    """5件未満カテゴリは、検索監査ログで収集漏れでないことを検査する。"""
    errs: list[str] = []
    for md in sorted(digest_root.glob(f"*/*{issue.isoformat()}*.md")):
        if md.parent.name in {"Summary", "DeepDive"}:
            continue
        fm, body = parse_frontmatter(md.read_text(encoding="utf-8-sig", errors="replace"))
        articles_count = len(parse_articles(body))
        if articles_count >= min_articles:
            continue
        cat_id = str(fm.get("categoryId") or fm.get("category") or md.parent.name).strip()
        audit_path = _search_audit_path(audit_root, issue, cat_id)
        if not audit_path.exists():
            errs.append(
                f"{md}: has {articles_count} article(s); search audit missing: {audit_path}"
            )
            continue
        try:
            audit = json.loads(audit_path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            errs.append(f"{audit_path}: JSON decode error: {exc}")
            continue

        queries = audit.get("queries") or []
        if not isinstance(queries, list) or len(queries) < 3:
            errs.append(f"{audit_path}: queries must contain at least 3 search queries for shortfall review.")

        raw_results_total = _as_int(audit.get("raw_results_total"))
        candidates_total = _as_int(audit.get("candidates_total"))
        selected_total = _as_int(audit.get("selected_total"))
        dropped = audit.get("dropped") or []
        if raw_results_total < min_articles * 2:
            errs.append(
                f"{audit_path}: raw_results_total={raw_results_total}; expected at least {min_articles * 2}."
            )
        if candidates_total < min_articles:
            errs.append(
                f"{audit_path}: candidates_total={candidates_total}; expected at least {min_articles} before quality filtering."
            )
        if selected_total != articles_count:
            errs.append(
                f"{audit_path}: selected_total={selected_total} does not match digest article count {articles_count}."
            )
        if candidates_total > selected_total and not dropped:
            errs.append(f"{audit_path}: dropped reasons are required when candidates were excluded.")

        checked = {str(v).strip() for v in (audit.get("coverage_terms_checked") or []) if str(v).strip()}
        required = REQUIRED_COVERAGE_TERMS.get(cat_id.casefold())
        if required:
            missing = sorted(required - checked)
            if missing:
                errs.append(
                    f"{audit_path}: coverage_terms_checked missing required terms: {', '.join(missing)}"
                )
    return errs


def validate_jsonl_source_freshness(jsonl_path: Path, issue: date) -> list[str]:
    """data/articles.jsonl の当日 record URL パス日付が前日以前なら落とす。"""
    if not jsonl_path.exists():
        return [f"articles jsonl が存在しません: {jsonl_path}"]
    errs: list[str] = []
    for lineno, line in enumerate(jsonl_path.read_text(encoding="utf-8-sig", errors="replace").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record: dict[str, Any] = json.loads(line)
        except json.JSONDecodeError as exc:
            return [f"{jsonl_path}:{lineno}: JSON decode error: {exc}"]
        if record.get("date") != issue.isoformat():
            continue
        errs.extend(_stale_source_url_errors(
            issue=issue,
            label=f"{jsonl_path}:{lineno} [{record.get('genre', '')}]",
            title=record.get("title") or "",
            url=record.get("url") or "",
        ))
        errs.extend(_stale_followup_errors(
            issue=issue,
            label=f"{jsonl_path}:{lineno} [{record.get('genre', '')}]",
            title=record.get("title") or "",
            record=record,
        ))
    return errs


def validate_dedup_annotation_present(jsonl_path: Path, issue: date) -> list[str]:
    """当日レコード群に dedup 強化版の鮮度注釈が 1 件も無ければ警告する (fatal でない)。

    強化版 dedup (2026-06-11〜) は鮮度ゲートを通った pass 候補に
    ``date_evidence_source`` を刻印する。当日 articles.jsonl の全レコードにこの刻印が
    皆無 = 旧版 dedup (注釈なし) を通った疑い、を検知する。warn-pass 候補には注釈が
    付かないため「全件必須」にはできず、「全件ゼロ」だけを警告に留める。
    注釈導入日 (_DATE_EVIDENCE_ANNOTATION_START) より前の号には適用しない。
    """
    if issue < _DATE_EVIDENCE_ANNOTATION_START:
        return []
    if not jsonl_path.exists():
        return []
    day_records = 0
    annotated = 0
    for line in jsonl_path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            record: dict[str, Any] = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("date") != issue.isoformat():
            continue
        day_records += 1
        if str(record.get(_DATE_EVIDENCE_SOURCE_FIELD) or "").strip():
            annotated += 1
    if day_records == 0 or annotated > 0:
        return []
    return [
        f"WARNING: {jsonl_path}: {issue.isoformat()} の {day_records} 件すべてに "
        f"{_DATE_EVIDENCE_SOURCE_FIELD} 刻印が無い。強化版 dedup (鮮度ゲート) を "
        "通っていない疑いがある (warn-pass のみで注釈ゼロの可能性もあるため fatal にはしない)。",
    ]


def validate_daily_quality(
    *,
    issue_date: str,
    digest_root: Path = Path("digest"),
    jsonl_path: Path = Path("data") / "articles.jsonl",
    audit_root: Path = Path("data") / "search_audit",
) -> list[str]:
    """指定日の Summary hero と記事 URL 鮮度をまとめて検査する。"""
    issue = _parse_issue_date(issue_date)
    errs: list[str] = []
    summary_path = digest_root / "Summary" / f"{issue.isoformat()}.md"
    errs.extend(validate_summary_hero(summary_path))
    errs.extend(validate_summary_emphasis(summary_path))
    errs.extend(validate_card_emphasis_coverage(digest_root, issue))
    errs.extend(validate_issue_schedule(digest_root, issue))
    errs.extend(validate_digest_article_counts(digest_root, issue))
    errs.extend(validate_search_audit_for_shortfall(
        digest_root=digest_root,
        audit_root=audit_root,
        issue=issue,
    ))
    errs.extend(validate_issue_thumbnail_coverage(jsonl_path, issue))
    errs.extend(validate_digest_source_freshness(digest_root, issue))
    errs.extend(validate_jsonl_source_freshness(jsonl_path, issue))
    return errs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="日次 digest の公開前品質を検査します。")
    parser.add_argument("--date", required=True, help="検査対象日 YYYY-MM-DD")
    parser.add_argument("--digest-root", type=Path, default=Path("digest"))
    parser.add_argument("--jsonl", type=Path, default=Path("data") / "articles.jsonl")
    parser.add_argument("--audit-root", type=Path, default=Path("data") / "search_audit")
    args = parser.parse_args(argv)

    errs = validate_daily_quality(
        issue_date=args.date,
        digest_root=args.digest_root,
        jsonl_path=args.jsonl,
        audit_root=args.audit_root,
    )
    # dedup 刻印検証は警告のみ (fatal にしない)。exit code には影響させず stderr に出す。
    for warn in validate_dedup_annotation_present(args.jsonl, _parse_issue_date(args.date)):
        print(warn, file=sys.stderr)
    if errs:
        for err in errs:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1
    print(f"PASS: daily quality OK ({args.date})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
