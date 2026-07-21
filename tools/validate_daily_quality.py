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
    TAG_TO_CID,
    parse_articles,
    parse_frontmatter,
    parse_reflection,
)
from tools.publish_inventory import required_published_docs_artifacts
from tools.publish_inventory import scheduled_category_ids
from tools.validate_summary_reflection import validate_summary_category_focus
from tools.url_quality import (
    is_google_news_proxy_thumb,
    is_google_news_rss_url,
    looks_homepage_or_section_landing,
)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_WEEKDAY_JA = ["月曜日", "火曜日", "水曜日", "木曜日", "金曜日", "土曜日", "日曜日"]

# dedup 強化版 (鮮度ゲートで published_date / date_evidence_source 注釈を刻印) の
# 適用開始日。この日より前の articles.jsonl レコードは注釈が無くて当然なので、
# 「全件注釈ゼロ = 強化版を通っていない疑い」の警告検査を適用しない。
_DATE_EVIDENCE_ANNOTATION_START = date(2026, 6, 11)
_DATE_EVIDENCE_SOURCE_FIELD = "date_evidence_source"
_TTS_REQUIRED_START = date(2026, 6, 17)
REQUIRED_COVERAGE_TERMS: dict[str, set[str]] = {
    "ai": {"OpenAI", "Anthropic", "Google", "Apple", "Microsoft", "Meta", "NVIDIA"},
    "fx": {"USDJPY", "EURUSD", "BOJ", "Fed", "ECB"},
    "it": {"McKinsey", "BCG", "Accenture", "Deloitte", "PwC", "NTT"},
    "mobility": {"Tesla", "Waymo", "BYD", "Toyota", "Uber"},
    "game": {"Nintendo", "Switch 2", "Sony", "Capcom", "Square Enix"},
    "manufacturing": {"TSMC", "Samsung", "Intel", "NVIDIA", "Foxconn", "Toyota"},
    "economy": {"Nikkei", "S&P 500", "Fed", "BOJ", "SoftBank", "NVIDIA"},
}
_NEWS_GRASP_THUMB_RE = re.compile(
    r"^https?://hidepon-umg\.github\.io/News-Grasp/(?:assets/og/|assets/news-grasp)",
    re.IGNORECASE,
)


def _scheduled_digest_file(md: Path, issue: date) -> bool:
    if md.parent.name in {"Summary", "DeepDive"}:
        return False
    try:
        fm, _body = parse_frontmatter(md.read_text(encoding="utf-8-sig", errors="replace"))
    except OSError:
        return False
    cat_id = str(fm.get("categoryId") or fm.get("category") or "").strip().casefold()
    return bool(cat_id and cat_id in set(scheduled_category_ids(issue)))
_INTENTIONAL_PAUSE_RE = re.compile(r"(休載|正当な休載理由|正当な欠落理由|intentionally short)", re.IGNORECASE)
_FRONTMATTER_BLOCK_RE = re.compile(r"\A---\r?\n(?P<frontmatter>.*?)\r?\n---", re.DOTALL)


def _is_news_grasp_self_thumb(value: object) -> bool:
    return isinstance(value, str) and bool(_NEWS_GRASP_THUMB_RE.search(value))


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
        and is_google_news_rss_url(rec["url"])
    ]
    rounded_urls = [
        rec.get("url")
        for rec in records
        if isinstance(rec.get("url"), str)
        and looks_homepage_or_section_landing(rec["url"])
    ]
    errs: list[str] = []
    if google_news_urls:
        errs.extend([
            f"{jsonl_path}: {issue.isoformat()} に Google News RSS URL のままです: {len(google_news_urls)} 件",
            "元記事 URL へ解決してから公開してください。Google News URL のままだと元記事 OGP を取り逃し、fallback サムネ化します。",
        ])
    if rounded_urls:
        sample = ", ".join(str(url) for url in rounded_urls[:5])
        errs.extend([
            f"{jsonl_path}: {issue.isoformat()} に媒体トップまたはカテゴリトップに丸まった URL があります: {len(rounded_urls)} 件",
            f"元記事単位の URL へ解決してから公開してください。例: {sample}",
        ])
    if all("thumb" in rec for rec in records) and all(rec.get("thumb") is None for rec in records):
        errs.extend([
            f"{jsonl_path}: {issue.isoformat()} の thumb が全件 null です。",
            "このままでは公開ページが全件 fallback サムネになります。fetch_ogp / WebSearch thumbnail の取得結果を反映してください。",
        ])
    proxy_thumbs = [
        (rec.get("genre") or "", rec.get("title_ja") or rec.get("title") or "", rec.get("thumb") or "")
        for rec in records
        if is_google_news_proxy_thumb(rec.get("thumb"))
    ]
    for genre, title, thumb in proxy_thumbs:
        errs.append(
            f"{jsonl_path}: {issue.isoformat()} [{genre}] Google News 代理サムネです: "
            f"{title} thumb={thumb}。元記事 OGP 画像またはカテゴリ既定画像へ差し替えてください。"
        )
    self_thumbs = [
        (rec.get("genre") or "", rec.get("title_ja") or rec.get("title") or "", rec.get("thumb") or "")
        for rec in records
        if _is_news_grasp_self_thumb(rec.get("thumb"))
    ]
    for genre, title, thumb in self_thumbs:
        errs.append(
            f"{jsonl_path}: {issue.isoformat()} [{genre}] News-Grasp 自己参照 thumb です: {title} thumb={thumb}"
        )
    return errs


def validate_digest_article_thumbnail_coverage(digest_root: Path, issue: date) -> list[str]:
    """digest の記事カード単位で thumb 欠落と公開 fallback 退化を検査する。"""
    errs: list[str] = []
    for md in sorted(digest_root.glob(f"*/*{issue.isoformat()}*.md")):
        if not _scheduled_digest_file(md, issue):
            continue
        _fm, body = parse_frontmatter(md.read_text(encoding="utf-8-sig", errors="replace"))
        articles = parse_articles(body)
        for idx, article in enumerate(articles, 1):
            title = str(article.get("title") or "").strip()
            thumb = str(article.get("thumb") or "").strip()
            label = f"{md}: card #{idx:02d} {title}"
            if not thumb:
                errs.append(
                    f"{label}: thumb が空です。公開ページがカテゴリ fallback サムネになります。"
                )
                continue
            if is_google_news_proxy_thumb(thumb):
                errs.append(f"{label}: Google News 代理サムネです: thumb={thumb}")
            if _is_news_grasp_self_thumb(thumb):
                errs.append(f"{label}: News-Grasp 自己参照 thumb です: thumb={thumb}")
    return errs


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
        if not _scheduled_digest_file(md, issue):
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


def validate_digest_style_quality(digest_root: Path, issue: date) -> list[str]:
    """翻訳調・文末反復・冗長さ・title_ja 不自然を記事単位で検出する。"""
    errs: list[str] = []
    redundant = ("一方で", "また、", "さらに、", "加えて、")
    translationese = ("することを発表した", "であると述べた", "することを明らかにした")
    for md in sorted(digest_root.glob(f"*/*{issue.isoformat()}*.md")):
        if not _scheduled_digest_file(md, issue):
            continue
        _fm, body = parse_frontmatter(md.read_text(encoding="utf-8-sig", errors="replace"))
        blocks = re.split(r"\r?\n---\r?\n", body)
        card_idx = 0
        for block in blocks:
            if "### " not in block:
                continue
            card_idx += 1
            title = next((line[4:].strip() for line in block.splitlines() if line.startswith("### ")), "")
            bullets = [line.strip()[2:].strip() for line in block.splitlines() if line.lstrip().startswith("- ")]
            endings = [
                re.sub(r"[。.!?）)」』]*$", "", b)[-4:]
                for b in bullets
                if re.search(r"[ぁ-んァ-ン一-龥]", b)
            ]
            if title and re.search(r"\b[A-Z][A-Za-z]+\s+[A-Z][A-Za-z]+", title) and not re.search(r"[ぁ-んァ-ン一-龥]", title):
                errs.append(f"{md}: card #{card_idx:02d} title_ja appears untranslated: {title}")
            if len(endings) >= 3 and len(set(endings[-3:])) == 1:
                errs.append(f"{md}: card #{card_idx:02d} has repetitive sentence endings")
            joined = "\n".join(bullets)
            if sum(joined.count(word) for word in redundant) >= 3:
                errs.append(f"{md}: card #{card_idx:02d} has redundant connectors")
            if any(word in joined for word in translationese):
                errs.append(f"{md}: card #{card_idx:02d} has translationese wording")
    return errs


def validate_issue_schedule(digest_root: Path, issue: date) -> list[str]:
    """日付の曜日と配信スケジュールに対してカテゴリ過不足を検査する。"""
    summary_path = digest_root / "Summary" / f"{issue.isoformat()}.md"
    if not summary_path.exists():
        return []
    fm, body = parse_frontmatter(summary_path.read_text(encoding="utf-8-sig", errors="replace"))
    weekday = str(fm.get("weekday") or "").strip()
    if not weekday:
        return []

    errs: list[str] = []
    expected_weekday = _WEEKDAY_JA[issue.weekday()]
    if weekday != expected_weekday:
        errs.append(
            f"{summary_path}: weekday={weekday} does not match date {issue.isoformat()} ({expected_weekday})."
        )

    expected = set(scheduled_category_ids(issue))
    present: set[str] = set()
    for md in sorted(digest_root.glob(f"*/*{issue.isoformat()}*.md")):
        if not _scheduled_digest_file(md, issue):
            continue
        cat_fm, _body = parse_frontmatter(md.read_text(encoding="utf-8-sig", errors="replace"))
        cat_id = str(cat_fm.get("categoryId") or "").strip().casefold()
        if cat_id:
            present.add(cat_id)

    missing = sorted(
        cat_id for cat_id in expected - present
        if not _has_intentional_pause_marker(body, cat_id)
    )
    for cat_id in missing:
        label = str(CATEGORIES.get(cat_id, {}).get("label") or cat_id)
        errs.append(
            f"scheduled category digest missing: {cat_id} ({label}) for {issue.isoformat()}."
            " 配信対象カテゴリの digest が存在しないため公開前に停止します。"
            " 意図的休載の場合は Summary に当該カテゴリ名と休載理由を明記してください。"
        )
    errs.extend(_unscheduled_summary_category_errors(summary_path, fm, body, issue, expected))
    return errs


def _frontmatter_sequence_values(text: str, key: str) -> list[str]:
    """簡易 frontmatter の `key:\n  - value` 形式だけを抽出する。"""
    match = _FRONTMATTER_BLOCK_RE.match(text)
    if not match:
        return []
    values: list[str] = []
    in_target = False
    for line in match.group("frontmatter").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not line.startswith((" ", "\t")) and ":" in line:
            head, _, tail = line.partition(":")
            in_target = head.strip() == key
            if in_target and tail.strip():
                values.append(tail.strip().strip('"').strip("'"))
            continue
        if in_target and stripped.startswith("- "):
            values.append(stripped[2:].strip().strip('"').strip("'"))
    return values


def _category_aliases(cat_id: str) -> set[str]:
    meta = CATEGORIES.get(cat_id, {})
    aliases = {
        cat_id,
        str(meta.get("label") or ""),
        str(meta.get("jp") or ""),
    }
    aliases.update({
        "it": {"IT-Consulting", "IT", "コンサル"},
        "fx": {"FX", "為替"},
        "ai": {"AI"},
        "mobility": {"Mobility", "モビリティ"},
        "manufacturing": {"Manufacturing", "製造"},
        "economy": {"Economy", "経済"},
        "game": {"Game", "ゲーム"},
    }.get(cat_id, set()))
    return {alias for alias in aliases if alias}


def _cat_id_from_ref(value: str) -> str | None:
    folded = value.strip().strip('"').strip("'").casefold()
    if folded.startswith("cat/"):
        folded = folded[4:]
    if folded in CATEGORIES and folded != "summary":
        return folded
    return None


def _unscheduled_summary_category_errors(
    summary_path: Path,
    fm: dict[str, str],
    body: str,
    issue: date,
    expected: set[str],
) -> list[str]:
    """Summary が非対象カテゴリを当日扱いしたら公開前に止める。"""
    raw_text = summary_path.read_text(encoding="utf-8-sig", errors="replace")
    known = set(CATEGORIES) - {"summary"}
    refs: dict[str, set[str]] = {cat_id: set() for cat_id in known}

    for key in ("categories", "tags"):
        for value in _frontmatter_sequence_values(raw_text, key):
            cat_id = _cat_id_from_ref(value)
            if cat_id:
                refs[cat_id].add(f"frontmatter {key}: {value}")

    theme = str(fm.get("theme") or "")
    heading_lines = [
        line.strip()
        for line in body.splitlines()
        if line.lstrip().startswith("### ")
    ]
    body_probe_lines = heading_lines + [theme]
    for cat_id in known:
        for token in _category_aliases(cat_id):
            folded = token.casefold()
            for line in body_probe_lines:
                if folded and folded in line.casefold():
                    refs[cat_id].add(f"summary text: {line[:120]}")

    errs: list[str] = []
    for cat_id in sorted(known - expected):
        if refs[cat_id]:
            label = str(CATEGORIES.get(cat_id, {}).get("label") or cat_id)
            evidence = "; ".join(sorted(refs[cat_id])[:3])
            errs.append(
                f"{summary_path}: unscheduled summary category: {cat_id} ({label}) for {issue.isoformat()}."
                f" Summary must not publish or discuss non-target categories. evidence={evidence}"
            )
    return errs


def _has_intentional_pause_marker(summary_body: str, cat_id: str) -> bool:
    """Summary 内に当該カテゴリ名つきの休載理由があるかを見る。"""
    meta = CATEGORIES.get(cat_id, {})
    tokens = {
        cat_id,
        str(meta.get("label") or ""),
        str(meta.get("jp") or ""),
    }
    tokens.update(_category_aliases(cat_id))
    tokens = {token for token in tokens if token}
    for line in summary_body.splitlines():
        if not _INTENTIONAL_PAUSE_RE.search(line):
            continue
        folded = line.casefold()
        if any(token.casefold() in folded for token in tokens):
            return True
    return False


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
        f"{label}: top article date {meta_date.isoformat()} is {age} day(s) older than issue {issue.isoformat()}: {article.get('title') or ''}; TOP STORY must be today's or yesterday's article. Move the item down, replace it with a fresh article, or mark the digest as intentionally short.",
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
        if not _scheduled_digest_file(md, issue):
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
    errs: list[str] = []
    for md in sorted(digest_root.glob(f"*/*{issue.isoformat()}*.md")):
        if not _scheduled_digest_file(md, issue):
            continue
        _fm, body = parse_frontmatter(md.read_text(encoding="utf-8-sig", errors="replace"))
        articles_count = len(parse_articles(body))
        if articles_count == 0:
            errs.append(
                f"{md}: has 0 article(s); category digest is not an article page."
            )
    return errs


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
        candidate_shortfall_reason = str(audit.get("quality_shortfall_reason") or "").strip()
        dropped = audit.get("dropped") or []
        if raw_results_total < min_articles * 2:
            errs.append(
                f"{audit_path}: raw_results_total={raw_results_total}; expected at least {min_articles * 2}."
            )
        exhaustive_shortfall = (
            candidates_total > 0
            and raw_results_total >= min_articles * 2
            and bool(candidate_shortfall_reason)
        )
        if candidates_total < min_articles and not exhaustive_shortfall:
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


def validate_deepdive_presence(*, digest_root: Path, docs_root: Path, issue: date) -> list[str]:
    """当日 DeepDive の md/html 欠落を publish 前に落とす。"""
    issue_str = issue.isoformat()
    md_path = digest_root / "DeepDive" / f"{issue_str}-DeepDive.md"
    html_path = docs_root / "deepdive" / issue_str / "index.html"
    errs: list[str] = []
    if not md_path.exists():
        errs.append(
            f"DeepDive digest が存在しません: {md_path}。"
            "日次公開は Summary/カテゴリだけでなく当日 DeepDive まで生成してから完了扱いにしてください。"
        )
    if not html_path.exists():
        errs.append(
            f"DeepDive HTML が存在しません: {html_path}。"
            "tools.render_deepdive または tools.generate_pages で docs/deepdive/{date}/index.html を生成してください。"
        )
    elif issue_str not in html_path.read_text(encoding="utf-8-sig", errors="replace"):
        errs.append(
            f"{html_path}: 対象日 {issue_str} の sentinel が HTML 内にありません。"
            "前日以前の DeepDive を誤って最新扱いしている可能性があります。"
        )
    return errs


def validate_published_docs_presence(*, docs_root: Path, issue: date) -> list[str]:
    """当日の日付 docs と配信対象カテゴリ docs の欠落を publish 前に落とす。"""
    errs: list[str] = []
    for rel in required_published_docs_artifacts(issue):
        path = docs_root / rel.removeprefix("docs/")
        if not path.exists():
            issue_str = issue.isoformat()
            if rel == f"docs/{issue_str}/index.html":
                label = "日付 docs index が存在しません"
            elif rel == f"docs/{issue_str}/summary/index.html":
                label = "Summary 日付 docs が存在しません"
            else:
                cat_id = rel.split("/", 2)[1] if rel.startswith("docs/") else "unknown"
                label = f"カテゴリ日付 docs が存在しません ({cat_id})"
            errs.append(
                f"{label}: {path}。"
                "tools.generate_pages で docs/<date>/index.html、summary、per-category docs を生成してから公開してください。"
            )
    return errs


def validate_tts_audio_presence(
    *,
    repo_root: Path = Path("."),
    digest_root: Path = Path("digest"),
    docs_root: Path = Path("docs"),
    issue: date,
) -> list[str]:
    """必須音声成果物と公開 HTML への反映を検査する。"""
    if issue < _TTS_REQUIRED_START:
        return []

    issue_str = issue.isoformat()
    errs: list[str] = []
    audio_script = digest_root / "Summary" / f"{issue_str}-audio-script.md"
    if not audio_script.exists():
        errs.append(
            f"TTS 音声原稿が存在しません: {audio_script}。"
            "digest/Summary/<date>-audio-script.md を生成してから公開してください。"
        )

    latest_path = repo_root / "build" / "tts" / "latest_audio.json"
    if not latest_path.exists():
        errs.append(
            f"TTS latest_audio.json が存在しません: {latest_path}。"
            "tools.tts.publish_audio で Release URL を確定してから公開してください。"
        )
        return errs

    try:
        latest = json.loads(latest_path.read_text(encoding="utf-8-sig"))
    except Exception as exc:  # noqa: BLE001 - gate では文脈付きで返す
        errs.append(f"TTS latest_audio.json を読めません: {latest_path}: {exc}")
        return errs

    if latest.get("latest_audio_date") != issue_str:
        errs.append(
            f"TTS latest_audio.json の日付が対象日ではありません: "
            f"{latest.get('latest_audio_date')!r} != {issue_str}"
        )
    audio_url = str(latest.get("latest_audio_url") or "").strip()
    if not audio_url:
        errs.append("TTS latest_audio.json に latest_audio_url がありません。")
        return errs
    if f"/{issue_str}.mp3" not in audio_url:
        errs.append(f"TTS latest_audio_url が対象日の mp3 を指していません: {audio_url}")

    html_targets = [
        ("home", docs_root / "index.html"),
        ("summary", docs_root / issue_str / "summary" / "index.html"),
    ]
    for label, html_path in html_targets:
        if not html_path.exists():
            errs.append(f"TTS audio URL 検査対象 HTML が存在しません ({label}): {html_path}")
            continue
        html = html_path.read_text(encoding="utf-8-sig", errors="replace")
        if "<audio" not in html or audio_url not in html:
            errs.append(
                f"TTS audio URL が {label} HTML に反映されていません: "
                f"{html_path} に {audio_url} がありません。"
            )
    return errs


def _proper_cross(
    p: tuple[float, float],
    q: tuple[float, float],
    r: tuple[float, float],
    s: tuple[float, float],
) -> bool:
    def orient(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    return orient(p, q, r) * orient(p, q, s) < 0 and orient(r, s, p) * orient(r, s, q) < 0


def _relation_row_groups(rel: dict[str, Any]) -> list[list[str]]:
    raw = (
        rel.get("rowGroups")
        or rel.get("row_groups")
        or rel.get("roleRows")
        or rel.get("role_rows")
        or []
    )
    groups: list[list[str]] = []
    if not isinstance(raw, list):
        return groups
    for item in raw:
        if isinstance(item, list):
            ids = [str(v) for v in item if str(v).strip()]
            if len(ids) >= 2:
                groups.append(ids)
    return groups


def validate_deepdive_relations_layout(*, digest_root: Path, issue: date) -> list[str]:
    """当日 DeepDive の関係図に三原則違反が無いか検査する。

    既存 DeepDive 全件へ交差ゼロを一律適用すると、過去図の legitimate/legacy 配置まで
    落ちるため、明示座標つき relations を「編集済み配置」として厳格監査する。
    rowGroups / row_groups / roleRows / role_rows があれば、指定ノードの y 行揃えも見る。
    """
    md_path = digest_root / "DeepDive" / f"{issue.isoformat()}-DeepDive.md"
    if not md_path.exists():
        return []

    from tools.output_quality import check_relations_svg
    from tools.render_deepdive import extract_blocks, layout_relations, relations_svg

    text = md_path.read_text(encoding="utf-8-sig", errors="replace")
    rels = extract_blocks(text).get("relations") or []
    errs: list[str] = []
    for idx, rel in enumerate(rels, 1):
        title = str(rel.get("title") or f"relations#{idx}")
        src = f"{md_path.name}:{title}"
        nodes = list(rel.get("nodes") or [])
        explicit_xy = bool(nodes) and all(
            isinstance(nd.get("x"), (int, float)) and isinstance(nd.get("y"), (int, float))
            for nd in nodes
        )
        try:
            lay = layout_relations(rel)
            svg = relations_svg(rel)
        except Exception as exc:  # noqa: BLE001 - gate では文脈付きで返す
            errs.append(f"{src}: 関係図レイアウト構築に失敗: {exc}")
            continue

        errs.extend(check_relations_svg(svg, src=src, strict_objects=explicit_xy))

        points = {str(n.get("id")): (float(n["x"]), float(n["y"])) for n in lay.get("nodes", [])}
        if explicit_xy:
            segments: list[tuple[str, str, tuple[float, float], tuple[float, float], str]] = []
            for e in lay.get("edges", []):
                a, b = str(e.get("from")), str(e.get("to"))
                if a in points and b in points:
                    segments.append((a, b, points[a], points[b], str(e.get("label") or "")))
            for i, (a1, a2, p, q, label_a) in enumerate(segments):
                for b1, b2, r, s, label_b in segments[i + 1:]:
                    if len({a1, a2, b1, b2}) < 4:
                        continue
                    if _proper_cross(p, q, r, s):
                        errs.append(
                            f"{src}: 明示座標つき関係図で線交差があります: "
                            f"{label_a or a1 + '->' + a2} / {label_b or b1 + '->' + b2}"
                        )

        for group in _relation_row_groups(rel):
            ys = [(node_id, points[node_id][1]) for node_id in group if node_id in points]
            if len(ys) < 2:
                continue
            y_values = [y for _node_id, y in ys]
            if max(y_values) - min(y_values) > 1.0:
                detail = ", ".join(f"{node_id}:y={y:.1f}" for node_id, y in ys)
                errs.append(f"{src}: rowGroups の同役割ノードが同じ行にありません: {detail}")

    return errs


def validate_daily_quality(
    *,
    issue_date: str,
    digest_root: Path = Path("digest"),
    jsonl_path: Path = Path("data") / "articles.jsonl",
    audit_root: Path = Path("data") / "search_audit",
    docs_root: Path = Path("docs"),
    require_deepdive: bool = False,
) -> list[str]:
    """指定日の Summary hero と記事 URL 鮮度をまとめて検査する。"""
    issue = _parse_issue_date(issue_date)
    errs: list[str] = []
    summary_path = digest_root / "Summary" / f"{issue.isoformat()}.md"
    errs.extend(validate_summary_hero(summary_path))
    errs.extend(validate_summary_emphasis(summary_path))
    errs.extend(validate_summary_category_focus(
        summary_path,
        required_category_ids=scheduled_category_ids(issue),
    ))
    errs.extend(validate_card_emphasis_coverage(digest_root, issue))
    errs.extend(validate_digest_style_quality(digest_root, issue))
    errs.extend(validate_issue_schedule(digest_root, issue))
    errs.extend(validate_digest_article_counts(digest_root, issue))
    errs.extend(validate_search_audit_for_shortfall(
        digest_root=digest_root,
        audit_root=audit_root,
        issue=issue,
    ))
    errs.extend(validate_issue_thumbnail_coverage(jsonl_path, issue))
    errs.extend(validate_digest_article_thumbnail_coverage(digest_root, issue))
    errs.extend(validate_digest_source_freshness(digest_root, issue))
    errs.extend(validate_jsonl_source_freshness(jsonl_path, issue))
    if require_deepdive:
        errs.extend(validate_published_docs_presence(
            docs_root=docs_root,
            issue=issue,
        ))
        errs.extend(validate_deepdive_presence(
            digest_root=digest_root,
            docs_root=docs_root,
            issue=issue,
        ))
        errs.extend(validate_deepdive_relations_layout(
            digest_root=digest_root,
            issue=issue,
        ))
        errs.extend(validate_tts_audio_presence(
            repo_root=Path("."),
            digest_root=digest_root,
            docs_root=docs_root,
            issue=issue,
        ))
    return errs


SEARCH_AUDIT_COUNT_MISMATCH_RE = re.compile(
    r"(?P<artifact>.*?data[\\/]+search_audit[\\/]+(?P<issue>\d{4}-\d{2}-\d{2})"
    r"[\\/]+(?P<category>[^\\/:\s]+)\.json): selected_total=(?P<selected>\d+) "
    r"does not match digest article count (?P<count>\d+)\."
)
DIGEST_ERROR_ARTIFACT_RE = re.compile(
    r"(?P<artifact>.*?digest[\\/]+(?P<folder>[^\\/:]+)[\\/]+[^:\r\n]+\.md)(?:\s+\[[^\]]+\])?:"
)


def _repo_relative_artifact(path_text: str) -> str:
    normalized = path_text.replace("\\", "/")
    marker = "data/search_audit/"
    if marker in normalized:
        return marker + normalized.split(marker, 1)[1]
    return normalized


def daily_quality_issue_metadata(message: str) -> dict[str, Any]:
    match = SEARCH_AUDIT_COUNT_MISMATCH_RE.search(message)
    if match:
        return {
            "artifact_paths": [_repo_relative_artifact(match.group("artifact"))],
            "category": match.group("category"),
            "evidence": {
                "selected_total": int(match.group("selected")),
                "digest_article_count": int(match.group("count")),
            },
        }
    digest_match = DIGEST_ERROR_ARTIFACT_RE.search(message)
    if digest_match:
        artifact = digest_match.group("artifact").replace("\\", "/")
        marker = "digest/"
        artifact = marker + artifact.split(marker, 1)[1] if marker in artifact else artifact
        folder = digest_match.group("folder")
        return {
            "artifact_paths": [artifact],
            "category": TAG_TO_CID.get(folder, folder.casefold()),
        }
    return {}


def daily_quality_issue_code(message: str) -> str:
    text = message.casefold()
    if SEARCH_AUDIT_COUNT_MISMATCH_RE.search(message) or (
        "selected_total=" in text and "does not match digest article count" in text
    ):
        return "search_audit_count_mismatch"
    if "hero_left" in text or "hero_right" in text:
        return "summary_hero_missing"
    if "card #" in text and "lacks required emphasis" in text:
        return "category_card_emphasis_missing"
    if "top article date" in text and "top story" in text:
        return "top_article_stale"
    if "lacks required emphasis" in text:
        return "summary_reflection_emphasis_missing"
    if "category hero focus" in text or "reflection category section missing" in text:
        return "summary_category_focus_invalid"
    if "title_ja appears untranslated" in text:
        return "digest_title_ja_untranslated"
    if "thumbnail" in text or "thumb" in text:
        return "thumb_invalid_or_missing"
    if "published docs" in text or "published doc" in text or "docs/" in text:
        return "published_docs_missing"
    if "deepdive" in text and "missing" in text:
        return "published_docs_missing"
    if "audio" in text or "tts" in text:
        return "audio_script_quality_invalid"
    if "search audit" in text:
        return "url_dead_or_stale"
    if "digest missing" in text or "category digest" in text:
        return "missing_artifact"
    return "unknown"


def daily_quality_json_payload(issue: str, errs: list[str]) -> dict[str, Any]:
    issues = []
    for err in errs:
        entry: dict[str, Any] = {
            "gate_id": "daily-quality",
            "issue_code": daily_quality_issue_code(err),
            "message": err,
            "issue_date": issue,
        }
        entry.update(daily_quality_issue_metadata(err))
        issues.append(entry)
    return {
        "ok": not errs,
        "gate_id": "daily-quality",
        "date": issue,
        "issues": issues,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="日次 digest の公開前品質を検査します。")
    parser.add_argument("--date", required=True, help="検査対象日 YYYY-MM-DD")
    parser.add_argument("--digest-root", type=Path, default=Path("digest"))
    parser.add_argument("--jsonl", type=Path, default=Path("data") / "articles.jsonl")
    parser.add_argument("--audit-root", type=Path, default=Path("data") / "search_audit")
    parser.add_argument("--docs-root", type=Path, default=Path("docs"))
    parser.add_argument("--require-deepdive", action="store_true")
    parser.add_argument("--json", action="store_true", help="stable issue_code を含む JSON を stdout に出す。")
    args = parser.parse_args(argv)

    errs = validate_daily_quality(
        issue_date=args.date,
        digest_root=args.digest_root,
        jsonl_path=args.jsonl,
        audit_root=args.audit_root,
        docs_root=args.docs_root,
        require_deepdive=args.require_deepdive,
    )
    # dedup 刻印検証は警告のみ (fatal にしない)。exit code には影響させず stderr に出す。
    for warn in validate_dedup_annotation_present(args.jsonl, _parse_issue_date(args.date)):
        print(warn, file=sys.stderr)
    if errs:
        if args.json:
            print(json.dumps(daily_quality_json_payload(args.date, errs), ensure_ascii=False, indent=2))
        else:
            for err in errs:
                print(f"ERROR: {err}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(daily_quality_json_payload(args.date, []), ensure_ascii=False, indent=2))
    else:
        print(f"PASS: daily quality OK ({args.date})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
