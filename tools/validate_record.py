#!/usr/bin/env python3
"""articles.jsonl の 1 record に対する境界 schema validation。

# 検証する「なぜ重要か」

2026-06-06 に 23 件の record が `thumb` キーを丸ごと欠落した状態で append され、
`test_thumb_contract::test_thumb_key_present_after_cutoff` が事後検出した。
事後検出だけでは「digest md 生成後 → articles.jsonl append → 翌日 build」の流れで
silently に skip / KeyError / 型エラーが起こり得る。

本モジュールは `tools/append_*` や本番 daily pipeline (claude セッション直 append) が
書き出した record に対して push 前 gate (`runner.ps1` step 2.65) で境界 1 箇所集約
([[feedback_check_design_principles]] §2) を担う:

  - `validate_record(record)`  純粋関数。違反で `RecordSchemaError` raise。
  - `validate_jsonl(path, *, recent_days=None)`  ファイル全体 / 直近 N 日を validate。
  - CLI: `python -m tools.validate_record [--recent N] [--all]` で exit 0/1。

`runner.ps1` は `--recent 7` で発火させ「直近 7 日に 1 件でも違反があれば push 阻止」。
歴史的 (cutoff 前) record は対象外で legacy 互換を維持する。

Plan v3 (`~/.claude/plans/quiet-foraging-floyd.md`) P0-B で「append_articles.py 入口の
境界」と書かれていたが、本番 append 経路は claude セッション直 append で Python script
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
_REQUIRED_KEYS: tuple[str, ...] = ("date", "title", "url", "thumb")


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


def validate_jsonl(
    jsonl_path: Path,
    *,
    recent_days: int | None = None,
    today: date | None = None,
) -> list[str]:
    """articles.jsonl 全体 (or 直近 N 日) を validate。違反一覧を str list で返す。

    Args:
        jsonl_path: articles.jsonl のパス。
        recent_days: None なら全件、int なら今日から N 日前以降の record のみ。
        today: 既定 `date.today()`。テストから固定値で渡す用。

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
    args = parser.parse_args(argv)

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
        errs = validate_jsonl(jsonl_path, recent_days=recent)
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
