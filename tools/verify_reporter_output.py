#!/usr/bin/env python3
"""記者 (reporter) サブエージェント出力の機械検証 CLI (Newsroom Architecture Phase 1)。

# 役割

Newsroom Architecture では、カテゴリ別の「記者」サブエージェントが当日の記事を収集し
以下の成果物を吐き出す:

  - `tmp/newsroom/{date}/{cat}.records.jsonl`  ... 記事レコード (articles.jsonl 行と同形)
  - `data/search_audit/{date}/{cat}.json`       ... 検索監査ログ (収集網羅性の証跡)
  - `digest/{Genre}/{date}-{Genre}.md`          ... カテゴリ digest md (カード形式)

本 CLI は編集長 (editor) がこれらをマージする前に、1 記者分の出力が約束した契約を
満たしているかを機械検証する境界 (= LLM の自己申告を信用しない Lv2 境界 1 箇所集約)。
検証ロジックは `tools/validate_record.py` の純粋関数を import して再利用し、二重実装を
避ける。

# 検証 5 項目 (全 PASS で exit 0 / 1 つでも FAIL で exit 1・FAIL 理由を stdout に全件列挙)

  1. records.jsonl の各行が validate_record 検証 PASS かつ `date == 号日 (--date)`
  2. 件数 1〜5 件。5 件未満なら records 行内に `quality_shortfall_reason` 必須
  3. search_audit/{date}/{cat}.json が存在し必須フィールドを持つ
     (date / category_id / queries / raw_results_total / candidates_total / selected_total)
  4. digest md のカード数 == records 件数
  5. digest md に `ng-thumb-common-` (共通サムネ fallback) の直書きがない

# CLI

  python -m tools.verify_reporter_output --date 2026-06-11 --category ai
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parent.parent
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from tools.validate_record import (  # noqa: E402
    RecordSchemaError,
    _DATE_RE,
    validate_record,
)
from tools.url_quality import (
    is_google_news_proxy_thumb,
    is_google_news_rss_url,
    looks_homepage_or_section_landing,
)

# cat (category_id) → digest フォルダ名 (= Genre = digest frontmatter の category)。
# digest/{Genre}/{date}-{Genre}.md の Genre 部分。data/search_audit と digest の
# 実フォルダ名 (AI / FX / IT-Consulting / ...) に整合させた値。
_CAT_TO_GENRE: dict[str, str] = {
    "fx": "FX",
    "ai": "AI",
    "it": "IT-Consulting",
    "mobility": "Mobility",
    "manufacturing": "Manufacturing",
    "economy": "Economy",
    "game": "Game",
    "summary": "Summary",
}

# search_audit/{date}/{cat}.json の必須フィールド (data/search_audit/ の実ファイル形式に整合)。
_SEARCH_AUDIT_REQUIRED: tuple[str, ...] = (
    "date",
    "category_id",
    "queries",
    "raw_results_total",
    "candidates_total",
    "selected_total",
)

# digest md のカード見出し (### [score] title) を数えるための正規表現。
# generate_pages._ARTICLE_HEAD_RE と同方針 (横線区切りに依存せずカード先頭を直接数える)。
_CARD_HEAD_RE = re.compile(r"^###\s+\[", re.MULTILINE)

# 共通サムネ fallback の直書き禁止対象トークン。
_THUMB_FALLBACK_TOKEN = "ng-thumb-common-"
_NEWS_GRASP_THUMB_RE = re.compile(
    r"^https?://hidepon-umg\.github\.io/News-Grasp/(?:assets/og/|assets/news-grasp)",
    re.IGNORECASE,
)


def _is_news_grasp_self_thumb(value: object) -> bool:
    return isinstance(value, str) and bool(_NEWS_GRASP_THUMB_RE.search(value))


def _read_records(records_path: Path) -> tuple[list[dict], list[str]]:
    """records.jsonl を 1 行ずつ読み (records, errs) を返す。

    JSON 不正行は errs に記録し records には積まない (検証は続行する)。
    """
    records: list[dict] = []
    errs: list[str] = []
    text = records_path.read_text(encoding="utf-8-sig")
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as e:
            errs.append(f"records.jsonl line {lineno}: JSON decode error: {e}")
            continue
        if not isinstance(rec, dict):
            errs.append(f"records.jsonl line {lineno}: record が dict でない")
            continue
        records.append(rec)
    return records, errs


def _check_records(
    records: list[dict], issue_date: str
) -> tuple[int, list[str]]:
    """項目 1+2: 各レコードの schema / date 整合 / 件数 + 品質不足理由を検査する。

    返り値: (有効レコード件数, FAIL 理由の list)。
    """
    errs: list[str] = []
    for i, rec in enumerate(records, 1):
        try:
            validate_record(rec)
        except RecordSchemaError as e:
            errs.append(f"record #{i}: schema 違反: {e}")
            continue
        url = rec.get("url")
        if isinstance(url, str) and is_google_news_rss_url(url):
            errs.append(
                f"record #{i}: Google News RSS URL のままです。"
                "元記事 URL へ解決してから記者出力に含めること。"
            )
        if isinstance(url, str) and looks_homepage_or_section_landing(url):
            errs.append(
                f"record #{i}: 媒体トップまたはカテゴリトップに丸まった URL です: {url}"
            )
        if _is_news_grasp_self_thumb(rec.get("thumb")):
            errs.append(
                f"record #{i}: News-Grasp 自己参照 thumb です: {rec.get('thumb')}"
            )
        if is_google_news_proxy_thumb(rec.get("thumb")):
            errs.append(
                f"record #{i}: Google News 代理サムネです: {rec.get('thumb')}"
            )
        if rec.get("date") != issue_date:
            errs.append(
                f"record #{i}: date={rec.get('date')!r} != 号日 {issue_date!r} "
                f"(records.jsonl の date は号日に揃えること)"
            )
        if not rec.get("date_evidence_source"):
            errs.append(
                f"record #{i}: date_evidence_source が無い "
                f"(published_date の根拠種別を記者出力に含めること)"
            )

    count = len(records)
    if count < 1:
        errs.append("records が 0 件 (1〜5 件であること)")
    elif count > 5:
        errs.append(f"records が {count} 件 (上限 5 件を超過)")
    elif count < 5:
        # 5 件未満は records 行内に quality_shortfall_reason が必須。
        has_reason = any(
            isinstance(r.get("quality_shortfall_reason"), str)
            and r["quality_shortfall_reason"].strip()
            for r in records
        )
        if not has_reason:
            errs.append(
                f"records が {count} 件 (5 件未満) だが quality_shortfall_reason が "
                f"どの行にも無い (低ニュース性で意図的に絞ったなら理由を明記すること)"
            )
    if count > 0 and all("thumb" in rec for rec in records) and all(
        rec.get("thumb") is None for rec in records
    ):
        errs.append(
            "records の thumb が全件 null です。"
            "fetch_ogp / WebSearch thumbnail の取得結果を反映してから記者出力に含めること。"
        )
    return count, errs


def _check_search_audit(audit_path: Path) -> list[str]:
    """項目 3: search_audit/{date}/{cat}.json の存在と必須フィールドを検査する。"""
    if not audit_path.exists():
        return [f"search_audit が存在しない: {audit_path}"]
    try:
        audit = json.loads(audit_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        return [f"{audit_path}: JSON decode error: {e}"]
    if not isinstance(audit, dict):
        return [f"{audit_path}: トップレベルが dict でない"]
    errs: list[str] = []
    for field in _SEARCH_AUDIT_REQUIRED:
        if field not in audit:
            errs.append(f"{audit_path}: 必須フィールド欠落: {field!r}")
    return errs


def _count_digest_cards(digest_text: str) -> int:
    """digest md 本文のカード数 (`### [...` 見出しの数) を返す。"""
    return len(_CARD_HEAD_RE.findall(digest_text))


def _check_digest(
    digest_path: Path, records_count: int
) -> list[str]:
    """項目 4+5: digest md のカード数 == records 件数 / 共通サムネ直書きなしを検査する。"""
    if not digest_path.exists():
        return [f"digest md が存在しない: {digest_path}"]
    text = digest_path.read_text(encoding="utf-8-sig")
    errs: list[str] = []
    card_count = _count_digest_cards(text)
    if card_count != records_count:
        errs.append(
            f"{digest_path}: digest カード数 {card_count} != records 件数 {records_count}"
        )
    if _THUMB_FALLBACK_TOKEN in text:
        errs.append(
            f"{digest_path}: 共通サムネ fallback '{_THUMB_FALLBACK_TOKEN}' が直書きされている "
            f"(記者出力では個別記事サムネを使うこと)"
        )
    for thumb in re.findall(r"!\[thumb\]\((https?://[^)]+)\)", text):
        if _is_news_grasp_self_thumb(thumb):
            errs.append(
                f"{digest_path}: News-Grasp 自己参照 thumb が直書きされている: {thumb}"
            )
        if is_google_news_proxy_thumb(thumb):
            errs.append(
                f"{digest_path}: Google News 代理サムネが直書きされている: {thumb}"
            )
    return errs


def verify(
    *,
    repo_root: Path,
    issue_date: str,
    category: str,
) -> list[str]:
    """記者出力 1 カテゴリ分を検証し、FAIL 理由の list を返す (空なら全 PASS)。"""
    errs: list[str] = []

    genre = _CAT_TO_GENRE.get(category, category)
    records_path = repo_root / "tmp" / "newsroom" / issue_date / f"{category}.records.jsonl"
    audit_path = repo_root / "data" / "search_audit" / issue_date / f"{category}.json"
    digest_path = repo_root / "digest" / genre / f"{issue_date}-{genre}.md"

    # 項目 1+2: records.jsonl
    if not records_path.exists():
        errs.append(f"records.jsonl が存在しない: {records_path}")
        records_count = 0
    else:
        records, read_errs = _read_records(records_path)
        errs.extend(read_errs)
        records_count, rec_errs = _check_records(records, issue_date)
        errs.extend(rec_errs)

    # 項目 3: search_audit
    errs.extend(_check_search_audit(audit_path))

    # 項目 4+5: digest md
    errs.extend(_check_digest(digest_path, records_count))

    return errs


def main(argv: list[str] | None = None) -> int:
    # 日本語版 Windows の cp932 で記号・日本語の print が UnicodeEncodeError を
    # 起こさないよう、標準出力/エラーを UTF-8/replace に再構成する。
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(
        description="記者出力 (records.jsonl / search_audit / digest md) の機械検証。"
    )
    parser.add_argument("--date", required=True, help="号日 (YYYY-MM-DD)")
    parser.add_argument("--category", required=True, help="カテゴリ id (ai / fx / it ...)")
    args = parser.parse_args(argv)

    if not _DATE_RE.match(args.date):
        print(f"FATAL: --date は 'YYYY-MM-DD' 形式: got {args.date!r}", file=sys.stderr)
        return 2

    # --category はパス組み立てに使うため、英数下線ハイフン以外を拒否する
    # (../../ 等のトラバーサル予防。未知カテゴリ自体は verify 側が「成果物不在」で FAIL にする)
    if not re.fullmatch(r"[a-z0-9_-]+", args.category):
        print(f"FATAL: --category は英小文字/数字/_/- のみ: got {args.category!r}", file=sys.stderr)
        return 2

    errs = verify(
        repo_root=_PKG_ROOT,
        issue_date=args.date,
        category=args.category,
    )

    if errs:
        print(f"FAIL: 記者出力検証 {len(errs)} 件 (date={args.date} category={args.category}):")
        for msg in errs:
            print(f"  - {msg}")
        return 1

    print(f"PASS: 記者出力検証 OK (date={args.date} category={args.category})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
