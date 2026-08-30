"""News-Grasp の Codex App automation 定義を repo template から同期する。

Windows Scheduled Task や旧 runner には触れない。Codex App が読む
``~/.codex/automations/news-grasp-6-40/automation.toml`` を、repo-local
template と同じ direct 本線契約へ揃えるための薄い同期器である。
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import json
import os
import sqlite3
import stat
import tempfile
import time
import tomllib
from pathlib import Path
from typing import Any


AUTOMATION_ID = "news-grasp-6-40"
CANONICAL_NEWS_GRASP_REPO_ROOT = (
    Path.home() / "OneDrive" / "ドキュメント" / "ProjectFolders" / "News-Grasp"
)
TEST_FIXTURE_ROOT_NAME = "news-grasp-sync-fixture"
MAX_AUTOMATION_TOML_BYTES = 96 * 1024
MAX_SKILL_BYTES = 96 * 1024
MAX_APP_GLOBAL_STATE_BYTES = 2 * 1024 * 1024
REQUIRED_PROMPT_PARTS = (
    "$news-grasp-direct-mainline",
    "YY/MM/DD",
    "title_status",
    "title_status=already_ok",
    "already_ok",
    "post_publish_issue_list",
    "news_grasp_direct_runtime start",
    "direct completion guard",
    "completion_guard.py",
    "direct_public_v1",
    "validate_daily_quality",
    "--require-deepdive",
)

if os.name == "nt":
    import msvcrt
    from ctypes import wintypes

    GENERIC_READ = 0x80000000
    FILE_SHARE_READ = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    OPEN_EXISTING = 3
    FILE_ATTRIBUTE_DIRECTORY = 0x00000010
    FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
    FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    _CreateFileW = ctypes.windll.kernel32.CreateFileW
    _CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _CreateFileW.restype = wintypes.HANDLE
    _GetFileInformationByHandle = ctypes.windll.kernel32.GetFileInformationByHandle
    _GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(BY_HANDLE_FILE_INFORMATION)]
    _GetFileInformationByHandle.restype = wintypes.BOOL
    _CloseHandle = ctypes.windll.kernel32.CloseHandle
    _CloseHandle.argtypes = [wintypes.HANDLE]
    _CloseHandle.restype = wintypes.BOOL


def _default_repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _default_template(repo_root: Path) -> Path:
    return repo_root / "automation" / "news-grasp-6-40" / "automation.toml.template"


def _default_source_skill(repo_root: Path) -> Path:
    return repo_root / "automation" / "skills" / "news-grasp-direct-mainline" / "SKILL.md"


def _default_installed() -> Path:
    return Path.home() / ".codex" / "automations" / AUTOMATION_ID / "automation.toml"


def _default_installed_skill() -> Path:
    return Path.home() / ".codex" / "skills" / "news-grasp-direct-mainline" / "SKILL.md"


def _default_app_db() -> Path:
    return Path.home() / ".codex" / "sqlite" / "codex-dev.db"


def _default_app_global_state() -> Path:
    return Path.home() / ".codex" / ".codex-global-state.json"


def _default_snapshot(repo_root: Path) -> Path:
    return (
        repo_root.parent
        / "AIHarnessState"
        / "snapshot"
        / "codex"
        / "automations"
        / AUTOMATION_ID
        / "automation.toml"
    )


def _default_shadow_snapshot() -> Path:
    return (
        Path.home()
        / ".codex"
        / "state"
        / "harness-worktrees"
        / "AIHarnessState-global-harness-v1"
        / "snapshot"
        / "codex"
        / "automations"
        / AUTOMATION_ID
        / "automation.toml"
    )


def _snapshot_targets(repo_root: Path, explicit_snapshot: Path | None) -> list[Path]:
    if explicit_snapshot is not None:
        return [explicit_snapshot]
    targets = [_default_snapshot(repo_root), _default_shadow_snapshot()]
    unique: list[Path] = []
    seen: set[str] = set()
    for target in targets:
        key = str(target.expanduser().resolve(strict=False)).casefold()
        if key not in seen:
            unique.append(target)
            seen.add(key)
    return unique


def _has_reparse_point(path: Path) -> bool:
    try:
        attributes = int(getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0))
    except OSError:
        return False
    return bool(attributes & 0x400)


def _exists_no_follow(path: Path) -> bool:
    try:
        path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _is_regular_file_no_reparse(path: Path) -> bool:
    try:
        info = path.stat(follow_symlinks=False)
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode) and not path.is_symlink() and not _has_reparse_point(path)


def _open_windows_no_reparse(path: Path, *, directory: bool) -> tuple[int, int]:
    if os.name != "nt":  # pragma: no cover - Windows production path
        raise RuntimeError("windows_handle_unavailable")
    flags = FILE_FLAG_OPEN_REPARSE_POINT
    if directory:
        flags |= FILE_FLAG_BACKUP_SEMANTICS
    handle = _CreateFileW(
        str(path),
        GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None,
        OPEN_EXISTING,
        flags,
        None,
    )
    if handle == INVALID_HANDLE_VALUE:
        raise OSError(ctypes.get_last_error(), f"CreateFileW failed: {path}")
    info = BY_HANDLE_FILE_INFORMATION()
    try:
        if not _GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise OSError(ctypes.get_last_error(), f"GetFileInformationByHandle failed: {path}")
        attrs = int(info.dwFileAttributes)
        if attrs & FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError(f"unsafe_reparse_path:{path}")
        is_directory = bool(attrs & FILE_ATTRIBUTE_DIRECTORY)
        if directory != is_directory:
            raise ValueError(f"unexpected_path_type:{path}")
        size = (int(info.nFileSizeHigh) << 32) + int(info.nFileSizeLow)
        return int(handle), size
    except Exception:
        _CloseHandle(handle)
        raise


@contextlib.contextmanager
def _directory_guard(path: Path):
    _assert_no_reparse_chain(path)
    if os.name == "nt":
        handle, _size = _open_windows_no_reparse(path, directory=True)
        try:
            yield
        finally:
            _CloseHandle(handle)
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        yield
    finally:
        os.close(fd)


@contextlib.contextmanager
def _existing_file_guard(path: Path, *, require_exists: bool = False):
    _assert_no_reparse_chain(path)
    if not _exists_no_follow(path):
        if require_exists:
            raise FileNotFoundError(path)
        yield None
        return
    if os.name == "nt":
        handle, _size = _open_windows_no_reparse(path, directory=False)
        try:
            yield handle
        finally:
            _CloseHandle(handle)
        return
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        yield fd
    finally:
        os.close(fd)


def _read_bytes_no_follow(path: Path, *, limit: int) -> bytes:
    _assert_no_reparse_chain(path)
    if os.name == "nt":
        handle, size = _open_windows_no_reparse(path, directory=False)
        if size > limit:
            _CloseHandle(handle)
            raise ValueError(f"file_too_large:{path}")
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        with os.fdopen(fd, "rb") as stream:
            data = stream.read(limit + 1)
    else:  # pragma: no cover - Windows production path
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as stream:
            data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"file_too_large:{path}")
    return data


def _assert_no_reparse_chain(path: Path) -> None:
    expanded = path.expanduser()
    probe = expanded if _exists_no_follow(expanded) else expanded.parent
    while not _exists_no_follow(probe) and probe != probe.parent:
        probe = probe.parent
    for item in (probe, *probe.parents):
        if item.is_symlink() or _has_reparse_point(item):
            raise ValueError(f"unsafe_reparse_path:{path}")


def _is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _approved_roots(repo_root: Path) -> tuple[Path, ...]:
    candidates = (
        repo_root,
        repo_root.parent / "AIHarnessState",
        Path.home() / ".codex",
        Path(tempfile.gettempdir()),
    )
    roots: list[Path] = []
    for candidate in candidates:
        try:
            _assert_no_reparse_chain(candidate)
            roots.append(candidate.expanduser().resolve(strict=False))
        except (OSError, ValueError):
            continue
    return tuple(roots)


def _assert_approved_path(path: Path, *, repo_root: Path, label: str) -> Path:
    _assert_no_reparse_chain(path)
    resolved = path.expanduser().resolve(strict=False)
    roots = _approved_roots(repo_root)
    if not any(_is_relative_to(resolved, root) for root in roots):
        raise ValueError(f"path_outside_approved_root:{label}:{resolved}")
    return resolved


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left.expanduser().resolve(strict=False))) == os.path.normcase(
        str(right.expanduser().resolve(strict=False))
    )


def _endswith_parts(path: Path, suffix: tuple[str, ...]) -> bool:
    folded_parts = tuple(part.casefold() for part in path.parts)
    folded_suffix = tuple(part.casefold() for part in suffix)
    return len(folded_parts) >= len(folded_suffix) and folded_parts[-len(folded_suffix) :] == folded_suffix


def _under_temp(path: Path) -> bool:
    return _is_relative_to(path.expanduser().resolve(strict=False), Path(tempfile.gettempdir()).resolve(strict=False))


def _role_default_paths(repo_root: Path, label: str) -> tuple[Path, ...]:
    if label == "template":
        return (_default_template(repo_root),)
    if label == "installed":
        return (_default_installed(),)
    if label == "snapshot":
        return (_default_snapshot(repo_root), _default_shadow_snapshot())
    if label == "source_skill":
        return (_default_source_skill(repo_root),)
    if label == "installed_skill":
        return (_default_installed_skill(),)
    if label == "app_db":
        return (_default_app_db(),)
    return ()


def _fixture_role_path_allowed(path: Path, label: str) -> bool:
    if os.environ.get("NEWS_GRASP_ALLOW_TEST_SYNC_PATHS") != "1":
        return False
    if TEST_FIXTURE_ROOT_NAME not in path.parts:
        return False
    if not _under_temp(path):
        return False
    if label == "installed":
        return path.name == "automation.toml"
    if label == "snapshot":
        return _endswith_parts(path, ("snapshot", "codex", "automations", AUTOMATION_ID, "automation.toml"))
    if label == "installed_skill":
        return _endswith_parts(path, ("skills", "news-grasp-direct-mainline", "SKILL.md"))
    if label == "app_db":
        return path.name == "codex-dev.db"
    return False


def _assert_role_path(
    path: Path,
    *,
    repo_root: Path,
    label: str,
    custom_allowed: bool,
) -> Path:
    resolved = _assert_approved_path(path, repo_root=repo_root, label=label)
    if any(_same_path(resolved, default) for default in _role_default_paths(repo_root, label)):
        return resolved
    if custom_allowed and _fixture_role_path_allowed(resolved, label):
        return resolved
    raise ValueError(f"path_not_allowed_for_role:{label}:{resolved}")


def _assert_trusted_repo_root(path: Path) -> Path:
    _assert_no_reparse_chain(path)
    resolved = path.expanduser().resolve(strict=True)
    canonical = CANONICAL_NEWS_GRASP_REPO_ROOT.expanduser().resolve(strict=True)
    if not _same_path(resolved, canonical):
        raise ValueError("repo_root_not_canonical_news_grasp")
    required = (
        resolved / "automation" / "news-grasp-6-40" / "automation.toml.template",
        resolved / "tools" / "news_grasp_direct_runtime.py",
        resolved / "tools" / "sync_news_grasp_codex_automation.py",
    )
    if any(not _is_regular_file_no_reparse(item) for item in required):
        raise ValueError("repo_root_not_trusted_news_grasp")
    return resolved


def _read_text_limited(path: Path, *, limit: int) -> str:
    return _read_bytes_no_follow(path, limit=limit).decode("utf-8-sig")


def _load_toml(path: Path) -> dict[str, Any]:
    return tomllib.loads(_read_text_limited(path, limit=MAX_AUTOMATION_TOML_BYTES))


def _resolve_app_project_target(repo_root: Path) -> dict[str, str]:
    path = _default_app_global_state()
    try:
        value = json.loads(_read_text_limited(path, limit=MAX_APP_GLOBAL_STATE_BYTES))
    except FileNotFoundError as exc:
        raise ValueError("app_project_registry_missing") from exc
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("app_project_registry_invalid") from exc
    if not isinstance(value, dict):
        raise ValueError("app_project_registry_invalid")
    projects = value.get("local-projects")
    if not isinstance(projects, dict):
        raise ValueError("app_project_registry_invalid")

    matches: list[str] = []
    for registry_id, row in projects.items():
        if not isinstance(registry_id, str) or not registry_id or not isinstance(row, dict):
            continue
        project_id = row.get("id")
        roots = row.get("rootPaths")
        if project_id != registry_id or not isinstance(roots, list):
            continue
        if any(
            isinstance(root, str) and root and _same_path(Path(root), repo_root)
            for root in roots
        ):
            matches.append(project_id)
    if not matches:
        raise ValueError("app_project_binding_missing")
    if len(matches) != 1:
        raise ValueError("app_project_binding_ambiguous")
    return {"type": "project", "project_id": matches[0]}


def _quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _inline_target(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    target_type = value.get("type")
    project_id = value.get("project_id")
    if not isinstance(target_type, str) or not isinstance(project_id, str):
        return None
    return f"target = {{ type = {_quote(target_type)}, project_id = {_quote(project_id)} }}"


def _render_installed(
    *,
    template_path: Path,
    installed_path: Path,
    repo_root: Path,
    project_target: dict[str, str],
    now_ms: int | None = None,
) -> str:
    template = _load_toml(template_path)
    installed = _load_toml(installed_path) if installed_path.is_file() else {}
    now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    created_at = installed.get("created_at")
    if not isinstance(created_at, int) or isinstance(created_at, bool) or created_at <= 0:
        created_at = now_ms
    target_line = _inline_target(project_target)

    prompt = str(template.get("prompt") or "").strip()
    repo_cwd = str(repo_root.resolve(strict=True))

    def build(updated_at: int) -> str:
        lines = [
            "version = 1",
            f"id = {_quote(AUTOMATION_ID)}",
            f"kind = {_quote(str(template.get('kind') or 'cron'))}",
            f"name = {_quote(str(template.get('name') or 'News-Grasp 臨時本線日次バッチ 6:00 記事作成・公開'))}",
            f"status = {_quote(str(template.get('status') or 'ACTIVE'))}",
            f"rrule = {_quote(str(template.get('rrule') or 'RRULE:FREQ=DAILY;BYHOUR=6;BYMINUTE=0;BYSECOND=0'))}",
            f"model = {_quote('gpt-5.6-luna')}",
            f"reasoning_effort = {_quote('max')}",
            f"execution_environment = {_quote(str(template.get('execution_environment') or 'local'))}",
        ]
        if target_line:
            lines.append(target_line)
        lines.extend(
            [
                f"cwds = [{_quote(repo_cwd)}]",
                f"created_at = {created_at}",
                f"updated_at = {updated_at}",
                "prompt = \"\"\"",
                prompt,
                "\"\"\"",
                "",
            ]
        )
        return "\n".join(lines)

    installed_updated_at = installed.get("updated_at")
    if isinstance(installed_updated_at, int) and not isinstance(installed_updated_at, bool):
        candidate = build(max(installed_updated_at, created_at))
        if installed_path.is_file() and _read_text_limited(installed_path, limit=MAX_AUTOMATION_TOML_BYTES) == candidate:
            return candidate
    return build(max(now_ms, created_at))


def _atomic_write_text(path: Path, text: str) -> None:
    _assert_no_reparse_chain(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_reparse_chain(path.parent)
    with _directory_guard(path.parent):
        with _existing_file_guard(path):
            if path.exists() and not _is_regular_file_no_reparse(path):
                raise ValueError(f"unsafe_write_target:{path}")
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=str(path.parent),
            text=True,
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
            _assert_no_reparse_chain(path)
            _assert_no_reparse_chain(path.parent)
            with _existing_file_guard(path):
                if path.exists() and not _is_regular_file_no_reparse(path):
                    raise ValueError(f"unsafe_write_target:{path}")
            os.replace(temp_path, path)
            _assert_no_reparse_chain(path)
        finally:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass


def _custom_path_args(
    *,
    repo_root: Path | None,
    template_path: Path | None,
    installed_path: Path | None,
    snapshot_path: Path | None,
    source_skill_path: Path | None,
    installed_skill_path: Path | None,
    app_db_path: Path | None,
) -> dict[str, bool]:
    return {
        "repo_root": repo_root is not None and repo_root.resolve(strict=False) != _default_repo_root().resolve(strict=True),
        "template": template_path is not None,
        "installed": installed_path is not None,
        "snapshot": snapshot_path is not None,
        "source_skill": source_skill_path is not None,
        "installed_skill": installed_skill_path is not None,
        "app_db": app_db_path is not None,
    }


def _custom_path_error(custom_path_args: dict[str, bool]) -> dict[str, Any]:
    return {
        "schemaVersion": "NEWS_GRASP_CODEX_AUTOMATION_SYNC_V1",
        "ok": False,
        "dry_run": False,
        "changed": False,
        "snapshot_changed": False,
        "skill_changed": False,
        "app_db_changed": False,
        "failures": [
            "custom_path_override_requires_explicit_allow_custom_paths"
        ],
        "custom_path_args": {
            key: value for key, value in custom_path_args.items() if value
        },
    }


def _validate_loaded_automation(
    value: dict[str, Any],
    *,
    path: Path,
    repo_root: Path,
    expected_target: dict[str, str] | None = None,
) -> dict[str, Any]:
    failures: list[str] = []

    if expected_target is None:
        try:
            expected_target = _resolve_app_project_target(repo_root)
        except ValueError as exc:
            failures.append(str(exc))
            expected_target = None

    if value.get("id") != AUTOMATION_ID:
        failures.append("id_invalid")
    if value.get("status") != "ACTIVE":
        failures.append("status_not_active")
    if str(value.get("rrule") or "").upper() != "RRULE:FREQ=DAILY;BYHOUR=6;BYMINUTE=0;BYSECOND=0":
        failures.append("schedule_not_0600")
    if str(value.get("model") or "").casefold() != "gpt-5.6-luna":
        failures.append("model_not_luna")
    if value.get("reasoning_effort") != "max":
        failures.append("reasoning_not_max")
    nested_target = value.get("target")
    if isinstance(nested_target, dict):
        target_type = nested_target.get("type")
        project_id = nested_target.get("project_id")
    else:
        target_type = value.get("target_type")
        project_id = value.get("project_id")
    if expected_target is not None:
        if target_type != expected_target["type"]:
            failures.append("target_type_not_bound_to_app_project")
        if project_id != expected_target["project_id"]:
            failures.append("project_id_not_bound_to_app_project")
    cwds = value.get("cwds")
    if not isinstance(cwds, list) or str(repo_root.resolve()) not in {str(Path(str(item)).resolve()) for item in cwds}:
        failures.append("cwd_not_bound_to_repo")
    prompt = str(value.get("prompt") or "")
    for part in REQUIRED_PROMPT_PARTS:
        if part not in prompt:
            failures.append(f"prompt_missing:{part}")
    if "public incomplete のまま最終応答しないでください" in prompt:
        failures.append("prompt_external_blocker_boundary_ambiguous")
    if "最初に `python -m tools.news_grasp_direct_runtime start" in prompt:
        failures.append("prompt_title_runtime_order_ambiguous")
    return {
        "schemaVersion": "NEWS_GRASP_CODEX_AUTOMATION_SYNC_V1",
        "ok": not failures,
        "path": str(path),
        "failures": failures,
        "model": value.get("model"),
        "reasoning_effort": value.get("reasoning_effort"),
        "target_type": target_type,
        "project_id": project_id,
        "rrule": value.get("rrule"),
        "prompt_length": len(prompt),
        "updated_at": value.get("updated_at"),
    }


def validate_semantics(
    path: Path,
    *,
    repo_root: Path,
    expected_target: dict[str, str] | None = None,
) -> dict[str, Any]:
    try:
        _assert_approved_path(path, repo_root=_assert_trusted_repo_root(repo_root), label="automation")
        value = _load_toml(path)
    except FileNotFoundError:
        return {"ok": False, "path": str(path), "failures": ["automation_missing"]}
    except (tomllib.TOMLDecodeError, UnicodeError, ValueError) as exc:
        return {"ok": False, "path": str(path), "failures": [f"automation_invalid:{exc}"]}
    return _validate_loaded_automation(
        value,
        path=path,
        repo_root=repo_root,
        expected_target=expected_target,
    )


def _automation_value_from_app_db_row(row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    value = dict(row)
    cwds_raw = value.get("cwds")
    if isinstance(cwds_raw, str):
        try:
            value["cwds"] = json.loads(cwds_raw)
        except json.JSONDecodeError:
            value["cwds"] = cwds_raw
    return value


def validate_app_db_semantics(
    path: Path,
    *,
    repo_root: Path,
    expected_target: dict[str, str] | None = None,
) -> dict[str, Any]:
    try:
        _assert_approved_path(path, repo_root=_assert_trusted_repo_root(repo_root), label="app_db")
        with _existing_file_guard(path, require_exists=True):
            conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "select * from automations where id = ?",
                (AUTOMATION_ID,),
            ).fetchone()
    except FileNotFoundError:
        return {"ok": False, "path": str(path), "failures": ["app_db_missing"]}
    except sqlite3.Error as exc:
        return {"ok": False, "path": str(path), "failures": [f"app_db_invalid:{exc}"]}
    finally:
        try:
            conn.close()
        except Exception:
            pass
    value = _automation_value_from_app_db_row(row)
    if value is None:
        return {"ok": False, "path": str(path), "failures": ["app_db_automation_missing"]}
    result = _validate_loaded_automation(
        value,
        path=path,
        repo_root=repo_root,
        expected_target=expected_target,
    )
    result["schemaVersion"] = "NEWS_GRASP_CODEX_AUTOMATION_APP_DB_V1"
    return result


def validate_skill_semantics(path: Path) -> dict[str, Any]:
    failures: list[str] = []
    try:
        text = _read_text_limited(path, limit=MAX_SKILL_BYTES)
    except FileNotFoundError:
        return {"ok": False, "path": str(path), "failures": ["skill_missing"]}
    except UnicodeError as exc:
        return {"ok": False, "path": str(path), "failures": [f"skill_invalid:{exc}"]}

    required = (
        "set_thread_title",
        "title_completion=fulfilled|deferred",
        "NEWS_GRASP_DIRECT_PUBLIC_VERIFICATION_V1",
        "caller作成の completion JSON は Green authority ではない",
        "Git commit ID は観測値としてだけ報告してよい",
        "public incompleteかつexact successorがある状態で終了しない",
    )
    for part in required:
        if part not in text:
            failures.append(f"skill_missing:{part}")
    if "最大1回だけ試す" in text:
        failures.append("skill_title_retry_old_contract")
    return {
        "schemaVersion": "NEWS_GRASP_DIRECT_SKILL_SYNC_V1",
        "ok": not failures,
        "path": str(path),
        "failures": failures,
        "line_count": len(text.splitlines()),
    }


def _desired_app_db_row(
    *,
    template_path: Path,
    repo_root: Path,
    existing: dict[str, Any] | None,
    project_target: dict[str, str],
    now_ms: int | None = None,
) -> dict[str, Any]:
    template = _load_toml(template_path)
    now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    existing = existing or {}
    created_at = existing.get("created_at")
    if not isinstance(created_at, int) or isinstance(created_at, bool) or created_at <= 0:
        created_at = now_ms
    updated_at = existing.get("updated_at")
    if not isinstance(updated_at, int) or isinstance(updated_at, bool):
        updated_at = created_at
    return {
        "id": AUTOMATION_ID,
        "name": str(template.get("name") or "News-Grasp 臨時本線日次バッチ 6:00 記事作成・公開"),
        "prompt": str(template.get("prompt") or ""),
        "status": str(template.get("status") or "ACTIVE"),
        "next_run_at": existing.get("next_run_at"),
        "last_run_at": existing.get("last_run_at"),
        "cwds": json.dumps([str(repo_root.resolve(strict=True))], ensure_ascii=False),
        "rrule": str(template.get("rrule") or "RRULE:FREQ=DAILY;BYHOUR=6;BYMINUTE=0;BYSECOND=0"),
        "model": "gpt-5.6-luna",
        "reasoning_effort": "max",
        "created_at": created_at,
        "updated_at": max(now_ms, created_at, updated_at),
        "target_type": project_target["type"],
        "project_id": project_target["project_id"],
    }


def sync_app_db(
    *,
    repo_root: Path,
    template_path: Path,
    app_db_path: Path,
    project_target: dict[str, str],
    dry_run: bool,
    allow_custom_app_db: bool = False,
) -> dict[str, Any]:
    conn: sqlite3.Connection | None = None
    try:
        repo_root = _assert_trusted_repo_root(repo_root)
        app_db_path = _assert_role_path(
            app_db_path,
            repo_root=repo_root,
            label="app_db",
            custom_allowed=allow_custom_app_db,
        )
        template_path = _assert_role_path(
            template_path,
            repo_root=repo_root,
            label="template",
            custom_allowed=False,
        )
        with _existing_file_guard(app_db_path, require_exists=True):
            conn = sqlite3.connect(str(app_db_path), timeout=5)
            conn.row_factory = sqlite3.Row
            conn.execute("pragma busy_timeout = 5000")
            row = conn.execute(
                "select * from automations where id = ?",
                (AUTOMATION_ID,),
            ).fetchone()
            existing = dict(row) if row is not None else None
            desired = _desired_app_db_row(
                template_path=template_path,
                repo_root=repo_root,
                existing=existing,
                project_target=project_target,
            )
            if existing is not None:
                comparable = {
                    key: existing.get(key)
                    for key in desired
                    if key != "updated_at"
                }
                desired_comparable = {
                    key: desired.get(key)
                    for key in desired
                    if key != "updated_at"
                }
                if comparable == desired_comparable:
                    desired["updated_at"] = existing.get("updated_at")
            changed = existing != desired
            if changed and not dry_run:
                columns = [
                    "id",
                    "name",
                    "prompt",
                    "status",
                    "next_run_at",
                    "last_run_at",
                    "cwds",
                    "rrule",
                    "model",
                    "reasoning_effort",
                    "created_at",
                    "updated_at",
                    "target_type",
                    "project_id",
                ]
                placeholders = ", ".join("?" for _ in columns)
                update_assignments = ", ".join(
                    f"{column} = excluded.{column}"
                    for column in columns
                    if column != "id"
                )
                conn.execute(
                    f"insert into automations ({', '.join(columns)}) values ({placeholders}) "
                    f"on conflict(id) do update set {update_assignments}",
                    tuple(desired[column] for column in columns),
                )
                conn.commit()
    except FileNotFoundError:
        return {
            "schemaVersion": "NEWS_GRASP_CODEX_AUTOMATION_APP_DB_V1",
            "ok": False,
            "path": str(app_db_path),
            "changed": False,
            "failures": ["app_db_missing"],
        }
    except sqlite3.Error as exc:
        return {
            "schemaVersion": "NEWS_GRASP_CODEX_AUTOMATION_APP_DB_V1",
            "ok": False,
            "path": str(app_db_path),
            "changed": False,
            "failures": [f"app_db_update_failed:{exc}"],
        }
    finally:
        if conn is not None:
            conn.close()

    if dry_run:
        result = _validate_loaded_automation(
            _automation_value_from_app_db_row(desired) or {},
            path=app_db_path,
            repo_root=repo_root,
            expected_target=project_target,
        )
        result["schemaVersion"] = "NEWS_GRASP_CODEX_AUTOMATION_APP_DB_V1"
    else:
        result = validate_app_db_semantics(
            app_db_path,
            repo_root=repo_root,
            expected_target=project_target,
        )
    result["changed"] = changed
    return result


def sync(
    *,
    repo_root: Path | None = None,
    template_path: Path | None = None,
    installed_path: Path | None = None,
    snapshot_path: Path | None = None,
    source_skill_path: Path | None = None,
    installed_skill_path: Path | None = None,
    app_db_path: Path | None = None,
    write_snapshot: bool = False,
    write_skill: bool = False,
    write_app_db: bool = False,
    dry_run: bool = False,
    allow_custom_paths: bool = False,
) -> dict[str, Any]:
    custom_path_args = _custom_path_args(
        repo_root=repo_root,
        template_path=template_path,
        installed_path=installed_path,
        snapshot_path=snapshot_path,
        source_skill_path=source_skill_path,
        installed_skill_path=installed_skill_path,
        app_db_path=app_db_path,
    )
    if any(custom_path_args.values()) and not allow_custom_paths:
        return _custom_path_error(custom_path_args)

    repo = _assert_trusted_repo_root(repo_root or _default_repo_root())
    template = _assert_role_path(
        template_path or _default_template(repo),
        repo_root=repo,
        label="template",
        custom_allowed=allow_custom_paths and custom_path_args["template"],
    )
    installed = _assert_role_path(
        installed_path or _default_installed(),
        repo_root=repo,
        label="installed",
        custom_allowed=allow_custom_paths and custom_path_args["installed"],
    )
    try:
        project_target = _resolve_app_project_target(repo)
    except ValueError as exc:
        return {
            "schemaVersion": "NEWS_GRASP_CODEX_AUTOMATION_SYNC_V1",
            "ok": False,
            "dry_run": dry_run,
            "changed": False,
            "snapshot_changed": False,
            "skill_changed": False,
            "app_db_changed": False,
            "failures": [str(exc)],
        }
    rendered = _render_installed(
        template_path=template,
        installed_path=installed,
        repo_root=repo,
        project_target=project_target,
    )
    before = _read_text_limited(installed, limit=MAX_AUTOMATION_TOML_BYTES) if installed.is_file() else ""
    changed = before != rendered
    snapshot_results: list[dict[str, Any]] = []
    if not dry_run:
        if changed:
            _atomic_write_text(installed, rendered)
        if write_snapshot:
            for snapshot in _snapshot_targets(repo, snapshot_path):
                snapshot = _assert_role_path(
                    snapshot,
                    repo_root=repo,
                    label="snapshot",
                    custom_allowed=allow_custom_paths,
                )
                before_snapshot = _read_text_limited(snapshot, limit=MAX_AUTOMATION_TOML_BYTES) if snapshot.is_file() else ""
                snapshot_changed = before_snapshot != rendered
                if snapshot_changed:
                    _atomic_write_text(snapshot, rendered)
                snapshot_result = validate_semantics(
                    snapshot,
                    repo_root=repo,
                    expected_target=project_target,
                )
                snapshot_result["changed"] = snapshot_changed
                snapshot_results.append(snapshot_result)
    skill_result = None
    skill_changed = False
    if write_skill:
        source_skill = _assert_role_path(
            source_skill_path or _default_source_skill(repo),
            repo_root=repo,
            label="source_skill",
            custom_allowed=allow_custom_paths and custom_path_args["source_skill"],
        )
        installed_skill = _assert_role_path(
            installed_skill_path or _default_installed_skill(),
            repo_root=repo,
            label="installed_skill",
            custom_allowed=allow_custom_paths and custom_path_args["installed_skill"],
        )
        skill_text = _read_text_limited(source_skill, limit=MAX_SKILL_BYTES)
        before_skill = _read_text_limited(installed_skill, limit=MAX_SKILL_BYTES) if installed_skill.is_file() else ""
        skill_changed = before_skill != skill_text
        if skill_changed and not dry_run:
            _atomic_write_text(installed_skill, skill_text)
        skill_result = validate_skill_semantics(source_skill if dry_run else installed_skill)
    app_db_result = None
    if write_app_db:
        app_db_result = sync_app_db(
            repo_root=repo,
            template_path=template,
            app_db_path=app_db_path or _default_app_db(),
            project_target=project_target,
            dry_run=dry_run,
            allow_custom_app_db=allow_custom_paths and custom_path_args["app_db"],
        )
    if dry_run:
        installed_result = _validate_loaded_automation(
            tomllib.loads(rendered),
            path=installed,
            repo_root=repo,
            expected_target=project_target,
        )
    else:
        installed_result = validate_semantics(
            installed,
            repo_root=repo,
            expected_target=project_target,
        )
    snapshot_result = None
    if write_snapshot:
        if dry_run:
            for snapshot in _snapshot_targets(repo, snapshot_path):
                snapshot = _assert_role_path(
                    snapshot,
                    repo_root=repo,
                    label="snapshot",
                    custom_allowed=allow_custom_paths,
                )
                before_snapshot = _read_text_limited(snapshot, limit=MAX_AUTOMATION_TOML_BYTES) if snapshot.is_file() else ""
                snapshot_candidate = _validate_loaded_automation(
                    tomllib.loads(rendered),
                    path=snapshot,
                    repo_root=repo,
                    expected_target=project_target,
                )
                snapshot_candidate["changed"] = before_snapshot != rendered
                snapshot_results.append(snapshot_candidate)
        else:
            if not snapshot_results:
                for snapshot in _snapshot_targets(repo, snapshot_path):
                    snapshot = _assert_role_path(
                        snapshot,
                        repo_root=repo,
                        label="snapshot",
                        custom_allowed=allow_custom_paths,
                    )
                    snapshot_result_existing = validate_semantics(
                        snapshot,
                        repo_root=repo,
                        expected_target=project_target,
                    )
                    snapshot_result_existing["changed"] = False
                    snapshot_results.append(snapshot_result_existing)
        snapshot_result = snapshot_results[0] if snapshot_results else None
    return {
        "schemaVersion": "NEWS_GRASP_CODEX_AUTOMATION_SYNC_V1",
        "ok": (
            installed_result.get("ok") is True
            and (snapshot_result is None or snapshot_result.get("ok") is True)
            and all(item.get("ok") is True for item in snapshot_results)
            and (skill_result is None or skill_result.get("ok") is True)
            and (app_db_result is None or app_db_result.get("ok") is True)
        ),
        "dry_run": dry_run,
        "changed": changed,
        "snapshot_changed": None
        if not snapshot_results
        else any(item.get("changed") is True for item in snapshot_results),
        "skill_changed": skill_changed,
        "app_db_changed": None if app_db_result is None else app_db_result.get("changed"),
        "installed": installed_result,
        "skill": skill_result,
        "app_db": app_db_result,
        "snapshot": snapshot_result,
        "snapshots": snapshot_results,
    }


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=_default_repo_root())
    parser.add_argument("--template", type=Path, default=None)
    parser.add_argument("--installed", type=Path, default=None)
    parser.add_argument("--snapshot", type=Path, default=None)
    parser.add_argument("--source-skill", type=Path, default=None)
    parser.add_argument("--installed-skill", type=Path, default=None)
    parser.add_argument("--app-db", type=Path, default=None)
    parser.add_argument("--write-snapshot", action="store_true")
    parser.add_argument("--write-skill", action="store_true")
    parser.add_argument("--write-app-db", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--allow-custom-paths",
        action="store_true",
        help="テスト・手動復旧時だけ、default以外のpath overrideを許可する。",
    )
    args = parser.parse_args(argv)
    default_repo = _default_repo_root().resolve()
    custom_path_args = _custom_path_args(
        repo_root=args.repo_root,
        template_path=args.template,
        installed_path=args.installed,
        snapshot_path=args.snapshot,
        source_skill_path=args.source_skill,
        installed_skill_path=args.installed_skill,
        app_db_path=args.app_db,
    )
    if any(custom_path_args.values()) and not args.allow_custom_paths:
        result = _custom_path_error(custom_path_args)
        result["dry_run"] = args.dry_run
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2
    result = sync(
        repo_root=args.repo_root,
        template_path=args.template,
        installed_path=args.installed,
        snapshot_path=args.snapshot,
        source_skill_path=args.source_skill,
        installed_skill_path=args.installed_skill,
        app_db_path=args.app_db,
        write_snapshot=args.write_snapshot,
        write_skill=args.write_skill,
        write_app_db=args.write_app_db,
        dry_run=args.dry_run,
        allow_custom_paths=args.allow_custom_paths,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(_main())
