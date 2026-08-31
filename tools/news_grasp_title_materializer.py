"""Codex App の実行前 task title を Asia/Tokyo の対象日へ materialize する。"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import stat
import tempfile
import time
import tomllib
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .news_grasp_title_control import TITLE_SUFFIX, expected_title


AUTOMATION_ID = "news-grasp-6-40"
TIME_ZONE = ZoneInfo("Asia/Tokyo")
CANONICAL_TEMPLATE_NAME = TITLE_SUFFIX
_NAME_LINE = re.compile(r"^name\s*=\s*.*?(?P<newline>\r?\n)?$")
_REPARSE_FLAG = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_RECEIPT_PREFIX = ("build", "direct-mainline")


class TitleMaterializationError(ValueError):
    """実行前 title materialization の typed error。"""


def _default_repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _default_template(repo_root: Path) -> Path:
    return repo_root / "automation" / "news-grasp-6-40" / "automation.toml.template"


def _default_installed() -> Path:
    return Path.home() / ".codex" / "automations" / AUTOMATION_ID / "automation.toml"


def _default_app_db() -> Path:
    return Path.home() / ".codex" / "sqlite" / "codex-dev.db"


def _issue_date(value: str | date | None, *, now: datetime | None = None) -> date:
    if value is not None:
        try:
            parsed = date.fromisoformat(str(value))
        except ValueError as error:
            raise TitleMaterializationError("TITLE_ISSUE_DATE_INVALID") from error
        if parsed.isoformat() != str(value):
            raise TitleMaterializationError("TITLE_ISSUE_DATE_INVALID")
        return parsed
    observed = now or datetime.now(TIME_ZONE)
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=TIME_ZONE)
    return observed.astimezone(TIME_ZONE).date()


def _absolute_path(path: Path) -> Path:
    try:
        return Path(os.path.abspath(os.fspath(path)))
    except (OSError, TypeError, ValueError) as error:
        raise TitleMaterializationError("PATH_INVALID") from error


def _is_reparse_point(path: Path, info: os.stat_result | None = None) -> bool:
    """symlink 以外の Windows reparse point も no-follow で拒否する。"""

    if info is None:
        try:
            info = os.lstat(path)
        except OSError:
            return False
    return bool(int(getattr(info, "st_file_attributes", 0) or 0) & _REPARSE_FLAG)


def _existing_path_chain(path: Path, *, label: str) -> Path:
    """既存の leaf と全祖先を lstat し、symlink/reparse を拒否する。"""

    candidate = _absolute_path(path)
    cursor = candidate
    is_leaf = True
    while True:
        try:
            info = os.lstat(cursor)
        except FileNotFoundError:
            info = None
        except OSError as error:
            raise TitleMaterializationError(f"{label}_PATH_INVALID") from error
        if info is not None:
            if stat.S_ISLNK(info.st_mode) or _is_reparse_point(cursor, info):
                code = f"{label}_UNSAFE_PATH" if is_leaf else f"{label}_PARENT_UNSAFE_PATH"
                raise TitleMaterializationError(code)
            if not is_leaf and not stat.S_ISDIR(info.st_mode):
                raise TitleMaterializationError(f"{label}_PARENT_UNSAFE_PATH")
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
        is_leaf = False
    return candidate


def _assert_directory(path: Path, *, label: str, allow_missing: bool = False) -> Path:
    candidate = _existing_path_chain(path, label=label)
    try:
        info = os.lstat(candidate)
    except FileNotFoundError as error:
        if allow_missing:
            return candidate
        raise TitleMaterializationError(f"{label}_MISSING") from error
    except OSError as error:
        raise TitleMaterializationError(f"{label}_PATH_INVALID") from error
    if not stat.S_ISDIR(info.st_mode):
        raise TitleMaterializationError(f"{label}_UNSAFE_PATH")
    return candidate


def _assert_regular_file(path: Path, *, label: str) -> Path:
    candidate = _existing_path_chain(path, label=label)
    try:
        info = os.lstat(candidate)
    except FileNotFoundError as error:
        raise TitleMaterializationError(f"{label}_MISSING") from error
    except OSError as error:
        raise TitleMaterializationError(f"{label}_PATH_INVALID") from error
    if stat.S_ISLNK(info.st_mode) or _is_reparse_point(candidate, info) or not stat.S_ISREG(info.st_mode):
        raise TitleMaterializationError(f"{label}_UNSAFE_PATH")
    return candidate


def _read_text(path: Path, *, label: str) -> str:
    candidate = _assert_regular_file(path, label=label)
    try:
        return candidate.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as error:
        raise TitleMaterializationError(f"{label}_READ_FAILED") from error


def _atomic_write_text(path: Path, text: str, *, label: str) -> None:
    candidate = _assert_regular_file(path, label=label)
    parent = _assert_directory(candidate.parent, label=f"{label}_PARENT")
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{candidate.name}.", suffix=".tmp", dir=str(parent), text=True
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        # replace直前に、対象leafと全祖先をもう一度no-follow検証する。
        _assert_regular_file(candidate, label=label)
        _assert_directory(candidate.parent, label=f"{label}_PARENT")
        _assert_regular_file(temporary, label=f"{label}_TEMP")
        os.replace(temporary, candidate)
        _assert_regular_file(candidate, label=label)
    except TitleMaterializationError:
        raise
    except OSError as error:
        raise TitleMaterializationError(f"{label}_WRITE_FAILED") from error
    finally:
        _safe_remove_temporary(temporary)


def _safe_remove_temporary(path: Path) -> None:
    try:
        info = os.lstat(path)
    except OSError:
        return
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or _is_reparse_point(path, info):
        return
    try:
        os.unlink(path)
    except OSError:
        pass


def _validate_receipt_path(path: Path, repo_root: Path) -> Path:
    """receiptをrepo/build/direct-mainline直下のjson leafへ固定する。"""

    root = _assert_directory(repo_root, label="REPO_ROOT")
    raw = Path(path)
    if "\x00" in os.fspath(raw) or ".." in raw.parts:
        raise TitleMaterializationError("RECEIPT_PATH_INVALID")
    candidate = _absolute_path(raw if raw.is_absolute() else root / raw)
    prefix = root.joinpath(*_RECEIPT_PREFIX)
    try:
        relative = candidate.relative_to(prefix)
    except ValueError as error:
        raise TitleMaterializationError("RECEIPT_PATH_INVALID") from error
    if len(relative.parts) != 1 or candidate.parent != prefix or candidate.suffix.lower() != ".json":
        raise TitleMaterializationError("RECEIPT_PATH_INVALID")
    # prefixが既存の場合は、作成前にも全chainを検査する。missing componentは後段で安全に作る。
    try:
        _existing_path_chain(prefix, label="RECEIPT_PARENT")
        _existing_path_chain(candidate, label="RECEIPT")
    except TitleMaterializationError as error:
        raise TitleMaterializationError("RECEIPT_PATH_INVALID") from error
    return candidate


def _prepare_receipt_path(path: Path, repo_root: Path) -> Path:
    candidate = _validate_receipt_path(path, repo_root)
    root = _assert_directory(repo_root, label="REPO_ROOT")
    current = root
    try:
        for part in _RECEIPT_PREFIX:
            next_directory = current / part
            try:
                os.lstat(next_directory)
            except FileNotFoundError:
                _assert_directory(current, label="RECEIPT_PARENT")
                next_directory.mkdir()
            _assert_directory(next_directory, label="RECEIPT_PARENT")
            current = next_directory
    except TitleMaterializationError as error:
        raise TitleMaterializationError("RECEIPT_PATH_INVALID") from error
    except OSError as error:
        raise TitleMaterializationError("RECEIPT_PATH_INVALID") from error
    return _validate_receipt_path(candidate, root)


def _infer_repo_root(template_path: Path) -> Path:
    candidate = _absolute_path(template_path)
    try:
        return candidate.parents[2]
    except IndexError as error:
        raise TitleMaterializationError("REPO_ROOT_INVALID") from error


def _atomic_write_json(path: Path, value: dict[str, Any], *, repo_root: Path) -> None:
    candidate = _prepare_receipt_path(path, repo_root)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{candidate.name}.", suffix=".tmp", dir=str(candidate.parent), text=True
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        # replace直前にもrepo root、prefix、leafを再検証してTOCTOUを狭める。
        _validate_receipt_path(candidate, repo_root)
        _assert_directory(candidate.parent, label="RECEIPT_PARENT")
        _assert_regular_file(temporary, label="RECEIPT_TEMP")
        os.replace(temporary, candidate)
        _assert_regular_file(candidate, label="RECEIPT")
    except TitleMaterializationError:
        raise
    except OSError as error:
        raise TitleMaterializationError("RECEIPT_WRITE_FAILED") from error
    finally:
        _safe_remove_temporary(temporary)


def _replace_installed_name(text: str, title: str) -> tuple[str, bool]:
    lines = text.splitlines(keepends=True)
    indexes = [index for index, line in enumerate(lines) if _NAME_LINE.match(line)]
    if len(indexes) != 1:
        raise TitleMaterializationError("INSTALLED_NAME_FIELD_INVALID")
    index = indexes[0]
    current_line = lines[index]
    newline = "\r\n" if current_line.endswith("\r\n") else "\n" if current_line.endswith("\n") else ""
    current_name = tomllib.loads("".join(lines)).get("name")
    if not isinstance(current_name, str):
        raise TitleMaterializationError("INSTALLED_NAME_FIELD_INVALID")
    replacement = f"name = {json.dumps(title, ensure_ascii=False)}{newline}"
    changed = current_name != title
    if changed:
        lines[index] = replacement
    candidate = "".join(lines)
    try:
        parsed = tomllib.loads(candidate)
    except tomllib.TOMLDecodeError as error:
        raise TitleMaterializationError("INSTALLED_TOML_INVALID_AFTER_MATERIALIZATION") from error
    if parsed.get("name") != title:
        raise TitleMaterializationError("INSTALLED_NAME_NOT_MATERIALIZED")
    return candidate, changed


def _load_app_row(conn: sqlite3.Connection, *, automation_id: str) -> dict[str, Any]:
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "select * from automations where id = ?", (automation_id,)
        ).fetchone()
    except sqlite3.Error as error:
        raise TitleMaterializationError("APP_DB_READ_FAILED") from error
    if row is None:
        raise TitleMaterializationError("APP_DB_AUTOMATION_MISSING")
    return dict(row)


def _write_app_name(
    conn: sqlite3.Connection,
    *,
    automation_id: str,
    title: str,
    observed_at_ms: int,
) -> tuple[dict[str, Any], bool]:
    before = _load_app_row(conn, automation_id=automation_id)
    changed = before.get("name") != title
    if changed:
        try:
            conn.execute(
                "update automations set name = ?, updated_at = ? where id = ?",
                (title, max(observed_at_ms, int(before.get("updated_at") or 0) + 1), automation_id),
            )
            conn.commit()
        except (sqlite3.Error, TypeError, ValueError) as error:
            conn.rollback()
            raise TitleMaterializationError("APP_DB_NAME_UPDATE_FAILED") from error
    after = _load_app_row(conn, automation_id=automation_id)
    if after.get("name") != title:
        raise TitleMaterializationError("APP_DB_NAME_NOT_MATERIALIZED")
    for key, value in before.items():
        if key not in {"name", "updated_at"} and after.get(key) != value:
            raise TitleMaterializationError(f"APP_DB_FIELD_MUTATED:{key}")
    return after, changed


def materialize_title(
    *,
    issue_date: str | date | None = None,
    template_path: Path,
    installed_path: Path,
    app_db_path: Path,
    automation_id: str = AUTOMATION_ID,
    receipt_path: Path | None = None,
    dry_run: bool = False,
    now: datetime | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """automation の name だけを対象日の exact title へ同期する。

    source template は日付を保持せず、installed TOML と Codex App DB の
    ``name`` だけを同一 operation で materialize する。prompt、schedule、model、
    reasoning、cwd、target は変更しない。
    """

    template_path = Path(template_path)
    installed_path = Path(installed_path)
    app_db_path = Path(app_db_path)
    receipt_candidate: Path | None = None
    if receipt_path is not None:
        receipt_root = Path(repo_root) if repo_root is not None else _infer_repo_root(template_path)
        receipt_candidate = (
            _prepare_receipt_path(Path(receipt_path), receipt_root)
            if not dry_run
            else _validate_receipt_path(Path(receipt_path), receipt_root)
        )
    target_date = _issue_date(issue_date, now=now)
    title = expected_title(target_date)
    template_text = _read_text(template_path, label="TEMPLATE")
    try:
        template = tomllib.loads(template_text)
    except tomllib.TOMLDecodeError as error:
        raise TitleMaterializationError("TEMPLATE_TOML_INVALID") from error
    if template.get("name") != CANONICAL_TEMPLATE_NAME:
        raise TitleMaterializationError("TEMPLATE_NAME_NOT_CANONICAL")

    installed_before = _read_text(installed_path, label="INSTALLED")
    try:
        installed_config = tomllib.loads(installed_before)
    except tomllib.TOMLDecodeError as error:
        raise TitleMaterializationError("INSTALLED_TOML_INVALID") from error
    if installed_config.get("id") != automation_id:
        raise TitleMaterializationError("INSTALLED_AUTOMATION_ID_INVALID")
    installed_candidate, installed_changed = _replace_installed_name(installed_before, title)
    observed_at_ms = int((now or datetime.now(timezone.utc)).timestamp() * 1000)

    conn: sqlite3.Connection | None = None
    try:
        # connect直前にもleafと全祖先を再検証し、差替え後の別DBを開かない。
        app_db_candidate = _assert_regular_file(app_db_path, label="APP_DB")
        conn = sqlite3.connect(str(app_db_candidate), timeout=5)
        conn.execute("pragma busy_timeout = 5000")
        conn.row_factory = sqlite3.Row
        before_row = _load_app_row(conn, automation_id=automation_id)
        app_db_changed = before_row.get("name") != title
        if not dry_run:
            if installed_changed:
                _atomic_write_text(installed_path, installed_candidate, label="INSTALLED")
            after_row, app_db_changed = _write_app_name(
                conn,
                automation_id=automation_id,
                title=title,
                observed_at_ms=observed_at_ms,
            )
        else:
            after_row = dict(before_row)
        if after_row.get("name") != title and not dry_run:
            raise TitleMaterializationError("APP_DB_NAME_NOT_MATERIALIZED")
    except TitleMaterializationError:
        raise
    except sqlite3.Error as error:
        raise TitleMaterializationError("APP_DB_READ_FAILED") from error
    finally:
        if conn is not None:
            conn.close()

    result: dict[str, Any] = {
        "schemaVersion": "NEWS_GRASP_TITLE_MATERIALIZATION_V1",
        "ok": True,
        "automation_id": automation_id,
        "issue_date": target_date.isoformat(),
        "timezone": "Asia/Tokyo",
        "materialized_title": title,
        "template_name": template.get("name"),
        "installed_changed": installed_changed,
        "app_db_changed": app_db_changed,
        "only_name_mutated": True,
        "dry_run": dry_run,
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    if receipt_candidate is not None and not dry_run:
        _atomic_write_json(receipt_candidate, result, repo_root=Path(repo_root) if repo_root is not None else _infer_repo_root(template_path))
        result["receipt_path"] = str(receipt_candidate)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="News-Grasp task title materializer")
    parser.add_argument("--repo-root", type=Path, default=_default_repo_root())
    parser.add_argument("--issue-date")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument(
        "--receipt",
        type=Path,
        default=None,
        help="materialization receipt の出力先。既定では build/direct-mainline へ書く。",
    )
    args = parser.parse_args(argv)
    try:
        repo_root = _assert_directory(Path(args.repo_root), label="REPO_ROOT")
        if args.receipt is not None and (
            args.receipt.is_absolute() or ".." in args.receipt.parts
        ):
            raise TitleMaterializationError("RECEIPT_PATH_INVALID")
        receipt = args.receipt or repo_root / "build" / "direct-mainline" / "title-materialization.json"
        if args.verify_only:
            if args.receipt is not None:
                _validate_receipt_path(receipt, repo_root)
        else:
            receipt = _prepare_receipt_path(receipt, repo_root)
        result = materialize_title(
            issue_date=args.issue_date,
            template_path=_default_template(repo_root),
            installed_path=_default_installed(),
            app_db_path=_default_app_db(),
            receipt_path=None if args.verify_only else receipt,
            dry_run=args.verify_only,
            repo_root=repo_root,
        )
        if args.verify_only and (result["installed_changed"] or result["app_db_changed"]):
            result["ok"] = False
            result["reasonCode"] = "TITLE_MATERIALIZATION_STALE"
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") is True else 2
    except (OSError, TitleMaterializationError) as error:
        result = {
            "schemaVersion": "NEWS_GRASP_TITLE_MATERIALIZATION_V1",
            "ok": False,
            "reasonCode": str(error),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
