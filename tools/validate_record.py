#!/usr/bin/env python3
"""articles.jsonl の 1 record に対する境界 schema validation。

# 検証する「なぜ重要か」

2026-06-06 に 23 件の record が `thumb` キーを丸ごと欠落した状態で append され、
`test_thumb_contract::test_thumb_key_present_after_cutoff` が事後検出した。
事後検出だけでは「digest md 生成後 → articles.jsonl append → 翌日 build」の流れで
silently に skip / KeyError / 型エラーが起こり得る。

本モジュールは `tools/append_*` や本番 daily pipeline (Codex セッション直 append) が
書き出した record に対して push 前 gate (`runner.ps1` step 2.65) で境界 1 箇所集約
([[feedback_check_design_principles]] §2) を担う:

  - `validate_record(record)`  純粋関数。違反で `RecordSchemaError` raise。
  - `validate_jsonl(path, *, recent_days=None)`  ファイル全体 / 直近 N 日を validate。
  - CLI: `python -m tools.validate_record [--recent N] [--all]` で exit 0/1。

`runner.ps1` は `--recent 7` で発火させ「直近 7 日に 1 件でも違反があれば push 阻止」。
歴史的 (cutoff 前) record は対象外で legacy 互換を維持する。

Plan v3 (`~/.codex/plans/quiet-foraging-floyd.md`) P0-B で「append_articles.py 入口の
境界」と書かれていたが、本番 append 経路は Codex セッション直 append で Python script
を経由しない事実を発見し、共通モジュール + 本番 gate 方式に進路変更した
(harness_mapping.md 2026-06-06 章「実装上の進路変更」参照)。
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable


class RecordSchemaError(ValueError):
    """articles.jsonl record の境界 schema 違反。"""


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)

# CATEGORIES の小文字キー (= category_id) + genre 大文字表記 (本番 articles.jsonl 互換)。
# tools/config.py CATEGORIES と同期。新カテゴリ追加時は両方更新する。
_VALID_GENRES: frozenset[str] = frozenset({
    # CATEGORIES の小文字 category_id (= normalized)
    "fx", "ai", "it", "mobility", "manufacturing", "economy", "game", "summary",
    # genre 大文字表記 (append_*.py / digest プロンプト由来の生 genre)
    "FX", "AI", "IT", "IT-Consulting", "Mobility", "Manufacturing", "Economy", "Game",
})

# 必須キー: 1 件でも欠落で fatal。
#   - date / title / url / thumb は値の不在自体が後段の KeyError 源になる。
#   - thumb はキー必須 (= 2026-06-06 23 件欠落事故の class of bugs)、値は str / None どちらでも OK。
_REQUIRED_KEYS: tuple[str, ...] = ("date", "title", "title_ja", "url", "thumb")


def validate_record(record: Any) -> None:
    """articles.jsonl 1 record の境界 schema validation。

    Args:
        record: validate 対象。dict 以外は即 fatal。

    Raises:
        RecordSchemaError: 必須キー欠落 / 型違反 / 形式違反のいずれか。
    """
    if not isinstance(record, dict):
        raise RecordSchemaError(
            f"record は dict であること: got {type(record).__name__}"
        )

    for key in _REQUIRED_KEYS:
        if key not in record:
            raise RecordSchemaError(f"必須キー欠落: {key!r}")

    date_v = record["date"]
    if not isinstance(date_v, str) or not _DATE_RE.match(date_v):
        raise RecordSchemaError(
            f"date は 'YYYY-MM-DD' 形式の str: got {date_v!r}"
        )
    try:
        datetime.strptime(date_v, "%Y-%m-%d")
    except ValueError as e:
        raise RecordSchemaError(
            f"date は実在する日付であること: got {date_v!r} ({e})"
        ) from e

    url = record["url"]
    if not isinstance(url, str) or not _URL_RE.match(url):
        raise RecordSchemaError(
            f"url は 'http(s)://' で始まる str: got {url!r}"
        )

    title = record["title"]
    if not isinstance(title, str) or not title.strip():
        raise RecordSchemaError(f"title は非空 str: got {title!r}")

    title_ja = record["title_ja"]
    if not isinstance(title_ja, str) or not title_ja.strip():
        raise RecordSchemaError(f"title_ja は非空 str: got {title_ja!r}")

    thumb = record["thumb"]
    if thumb is not None:
        if not isinstance(thumb, str) or not _URL_RE.match(thumb):
            raise RecordSchemaError(
                f"thumb は 'http(s)://' で始まる str または None: got {thumb!r}"
            )

    genre = record.get("genre")
    if genre is not None and genre not in _VALID_GENRES:
        raise RecordSchemaError(
            f"genre は定義済み {sorted(_VALID_GENRES)} のいずれか: got {genre!r}"
        )


def iter_records(jsonl_path: Path) -> Iterable[tuple[int, dict[str, Any]]]:
    """articles.jsonl を 1 行ずつ (lineno, record) で返す。JSON 不正行は飛ばさず raise。"""
    with jsonl_path.open(encoding="utf-8-sig") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield lineno, json.loads(line)
            except json.JSONDecodeError as e:
                raise RecordSchemaError(
                    f"line {lineno}: JSON decode error: {e}"
                ) from e


def _seen_at_date_part(rec: dict[str, Any]) -> str | None:
    """record の `seen_at` (例 `2026-06-11T06:00:00+09:00`) から日付部分のみを取り出す。

    `seen_at` が無い / str でない / `YYYY-MM-DD` で始まらない場合は None を返す
    (= issue-date 検証の対象外として扱う)。
    """
    seen = rec.get("seen_at")
    if not isinstance(seen, str) or len(seen) < 10:
        return None
    head = seen[:10]
    return head if _DATE_RE.match(head) else None


def validate_jsonl(
    jsonl_path: Path,
    *,
    recent_days: int | None = None,
    today: date | None = None,
    issue_date: str | None = None,
) -> list[str]:
    """articles.jsonl 全体 (or 直近 N 日) を validate。違反一覧を str list で返す。

    Args:
        jsonl_path: articles.jsonl のパス。
        recent_days: None なら全件、int なら今日から N 日前以降の record のみ。
        today: 既定 `date.today()`。テストから固定値で渡す用。
        issue_date: 号日 (`YYYY-MM-DD`)。指定時は「`seen_at` の日付部分 == issue_date
            なのに `date != issue_date`」の record を fatal にする (2026-06-11 21 件
            誤記事故の機械検査)。articles.jsonl の `date` は号日 (= digest ファイル名と
            一致) であって記事公開日ではないため、当日生成 record の `date` を号日に
            揃える契約を locked-in する。`seen_at` の日付部分が issue_date と異なる
            過去 record は対象外 (当日生成でないため誤記とは言えない)。

    Returns:
        違反メッセージの list。空なら全件 PASS。
    """
    if today is None:
        today = date.today()
    cutoff: date | None = None
    if recent_days is not None:
        cutoff = today - timedelta(days=recent_days)

    errs: list[str] = []
    for lineno, rec in iter_records(jsonl_path):
        if cutoff is not None:
            try:
                rec_date = datetime.strptime(rec.get("date", ""), "%Y-%m-%d").date()
            except (TypeError, ValueError):
                errs.append(
                    f"line {lineno}: date 不正で recent 判定不能: {rec.get('date')!r}"
                )
                continue
            if rec_date < cutoff:
                continue
        try:
            validate_record(rec)
        except RecordSchemaError as e:
            title = str(rec.get("title", ""))[:50]
            errs.append(f"line {lineno}: {e} (title={title!r})")
        # 号日整合チェック: 当日生成 (seen_at の日付部分 == issue_date) の record は
        # date が号日と一致していなければ誤記扱い (fatal)。過去 record は対象外。
        # schema 違反があっても continue で隠さず独立に報告する: 2026-06-12 慣らしで
        # thumb 欠落が号日エラーをマスクし、bounded repair (予算 1 回) に全エラーが
        # 渡らず attempt 2 で新エラーが露出 → 予算切れ → fallback publish に落ちた。
        # gate は 1 attempt で全違反クラスを開示しないと修復予算が機能しない。
        if issue_date is not None and isinstance(rec, dict):
            seen_day = _seen_at_date_part(rec)
            if seen_day == issue_date and rec.get("date") != issue_date:
                title = str(rec.get("title", ""))[:50]
                errs.append(
                    f"line {lineno}: 号日不整合: seen_at={seen_day} (当日生成) なのに "
                    f"date={rec.get('date')!r} != issue-date={issue_date!r} "
                    f"(articles.jsonl の date は号日に揃えること) (title={title!r})"
                )
    return errs


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="articles.jsonl の境界 schema を validate (Plan v3 P0-B)。"
    )
    parser.add_argument(
        "--articles",
        type=Path,
        default=None,
        help="articles.jsonl のパス。既定はリポジトリ data/articles.jsonl。",
    )
    parser.add_argument(
        "--recent",
        type=int,
        default=7,
        help="直近 N 日を対象 (既定 7)。--all 指定時は無視。",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="全件 validate (cutoff 前の legacy 含む)。",
    )
    parser.add_argument(
        "--issue-date",
        type=str,
        default=None,
        help="号日 (YYYY-MM-DD)。指定時は seen_at が当日 (= issue-date) の record の "
             "date が号日と一致しなければ fatal にする (2026-06-11 21 件誤記事故対策)。",
    )
    args = parser.parse_args(argv)

    if args.issue_date is not None and not _DATE_RE.match(args.issue_date):
        print(
            f"FATAL: --issue-date は 'YYYY-MM-DD' 形式: got {args.issue_date!r}",
            file=sys.stderr,
        )
        return 2

    jsonl_path = args.articles
    if jsonl_path is None:
        jsonl_path = (
            Path(__file__).resolve().parent.parent / "data" / "articles.jsonl"
        )

    if not jsonl_path.exists():
        print(f"FATAL: articles.jsonl not found: {jsonl_path}", file=sys.stderr)
        return 2

    recent = None if args.all else args.recent
    try:
        errs = validate_jsonl(jsonl_path, recent_days=recent, issue_date=args.issue_date)
    except RecordSchemaError as e:
        print(f"FATAL: {e}", file=sys.stderr)
        return 1

    if errs:
        scope = "全件" if args.all else f"直近 {args.recent} 日"
        print(
            f"FAIL: record schema 違反 {len(errs)} 件 ({scope}):", file=sys.stderr
        )
        for msg in errs[:20]:
            print(f"  - {msg}", file=sys.stderr)
        if len(errs) > 20:
            print(f"  ... and {len(errs) - 20} more", file=sys.stderr)
        return 1

    scope = "全件" if args.all else f"直近 {args.recent} 日"
    print(f"PASS: record schema OK ({scope})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
