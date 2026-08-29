"""News-Grasp 日次 automation の task title 契約を検証・記録する。"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import date
from pathlib import Path
from typing import Any, Iterable


TITLE_SUFFIX = "News-Grasp 臨時本線日次バッチ 6:00 記事作成・公開"
TITLE_PATTERN = re.compile(rf"^\d{{2}}/\d{{2}}/\d{{2}} {re.escape(TITLE_SUFFIX)}$")
TITLE_STATUSES = {"updated", "already_ok", "unavailable", "failed", "skipped"}
SUCCESS_STATUSES = {"updated", "already_ok"}


class TitleControlError(ValueError):
    """title receipt が契約を満たさない場合の typed error。"""


def _issue_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    try:
        parsed = date.fromisoformat(str(value))
    except ValueError as error:
        raise TitleControlError("TITLE_ISSUE_DATE_INVALID") from error
    if parsed.isoformat() != str(value):
        raise TitleControlError("TITLE_ISSUE_DATE_INVALID")
    return parsed


def expected_title(issue_date: str | date) -> str:
    """対象日の exact task title を返す。"""

    return f"{_issue_date(issue_date).strftime('%y/%m/%d')} {TITLE_SUFFIX}"


def validate_title(title: str, issue_date: str | date) -> dict[str, Any]:
    """実 title を対象日の exact contract と照合する。"""

    actual = str(title or "")
    expected = expected_title(issue_date)
    if actual == expected:
        return {"ok": True, "reason": "exact_match"}
    if actual.startswith(expected):
        return {"ok": False, "reason": "unexpected_suffix"}
    if actual.startswith("TT") and actual[2:] == expected:
        return {"ok": False, "reason": "unexpected_prefix"}
    if re.match(r"^\d{4}/\d{2}/\d{2}(?:\s|$)", actual):
        return {"ok": False, "reason": "invalid_format"}
    if TITLE_SUFFIX in actual and not re.match(r"^\d{2}/\d{2}/\d{2}\s", actual):
        if re.search(r"\d{2}/\d{2}/\d{2}", actual):
            return {"ok": False, "reason": "unexpected_prefix"}
        return {"ok": False, "reason": "date_missing"}
    if TITLE_PATTERN.fullmatch(actual):
        return {"ok": False, "reason": "wrong_issue_date"}
    if re.match(r"^\d{2}/\d{2}/\d{2}\s", actual):
        return {"ok": False, "reason": "wrong_issue_date"}
    return {"ok": False, "reason": "invalid_format"}


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def record_title_status(
    *,
    issue_date: str | date,
    status: str,
    actual_title: str,
    reason: str,
    post_publish_issue_list: Iterable[str] | None,
    output_path: Path | None = None,
) -> dict[str, Any]:
    """title status を非阻害 receipt として記録する。"""

    normalized_status = str(status or "").strip()
    if normalized_status not in TITLE_STATUSES:
        raise TitleControlError("TITLE_STATUS_INVALID")
    issue = _issue_date(issue_date).isoformat()
    validation = validate_title(actual_title, issue)
    issues = [str(item) for item in (post_publish_issue_list or []) if str(item)]
    if normalized_status in SUCCESS_STATUSES:
        if validation["ok"] is not True:
            raise TitleControlError(
                f"TITLE_STATUS_ACTUAL_TITLE_INVALID:{validation['reason']}"
            )
    else:
        marker = f"title_status={normalized_status}"
        if not any(marker in item or item.startswith("title:") for item in issues):
            issues.append(f"{marker}: {reason or validation['reason']}")
    receipt = {
        "schemaVersion": "NEWS_GRASP_TITLE_STATUS_V1",
        "issue_date": issue,
        "title_status": normalized_status,
        "expected_title": expected_title(issue),
        "actual_title": str(actual_title or ""),
        "validation": validation,
        "reason": str(reason or ""),
        "publication_blocked": False,
        "post_publish_issue_list": issues,
    }
    if output_path is not None:
        _atomic_write_json(Path(output_path), receipt)
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="News-Grasp task title contract")
    parser.add_argument("--issue-date", required=True)
    parser.add_argument("--status", required=True, choices=sorted(TITLE_STATUSES))
    parser.add_argument("--actual-title", default="")
    parser.add_argument("--reason", default="")
    parser.add_argument("--post-publish-issue", action="append", default=[])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        receipt = record_title_status(
            issue_date=args.issue_date,
            status=args.status,
            actual_title=args.actual_title,
            reason=args.reason,
            post_publish_issue_list=args.post_publish_issue,
            output_path=args.output,
        )
    except TitleControlError as error:
        print(json.dumps({"ok": False, "reasonCode": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
