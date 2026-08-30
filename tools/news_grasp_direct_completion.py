"""Semantic public verifier for News-Grasp direct mainline.

この module は caller が作った completion JSON を authority にしない。既存の
repo-local validator と public probe を呼び、読者可視 surface の観測を組み立てる。
旧 runner finalizer / readiness / producer lineage は呼ばない。
"""

from __future__ import annotations

import ctypes
import json
import os
import stat
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


PUBLIC_SCHEMA = "NEWS_GRASP_DIRECT_PUBLIC_VERIFICATION_V1"
EXPECTED_PUBLIC_HOST = "hidepon-umg.github.io"
EXPECTED_PUBLIC_PATH = "/News-Grasp"
CANONICAL_NEWS_GRASP_REPO_ROOT = (
    Path.home() / "OneDrive" / "ドキュメント" / "ProjectFolders" / "News-Grasp"
)
PUBLIC_SURFACES = (
    "web",
    "daily_audio",
    "deepdive_article",
    "deepdive_audio",
    "youtube_daily",
    "youtube_deepdive",
    "playlist",
    "notification",
    "distribution",
    "publish_status",
    "remote_commit",
    "pages",
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


def _has_reparse_point(path: Path) -> bool:
    """Windows の reparse point を、利用可能な場合だけ検査する。"""

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


def _assert_no_reparse_chain(path: Path) -> None:
    expanded = path.expanduser()
    probe = expanded if _exists_no_follow(expanded) else expanded.parent
    while not _exists_no_follow(probe) and probe != probe.parent:
        probe = probe.parent
    for item in (probe, *probe.parents):
        if item.is_symlink() or _has_reparse_point(item):
            raise ValueError(f"unsafe_reparse_path:{path}")


def _is_regular_file_no_reparse(path: Path) -> bool:
    try:
        info = path.stat(follow_symlinks=False)
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode) and not path.is_symlink() and not _has_reparse_point(path)


def _open_windows_file_no_reparse(path: Path) -> tuple[int, int]:
    if os.name != "nt":  # pragma: no cover - Windows production path
        raise RuntimeError("windows_handle_unavailable")
    handle = _CreateFileW(
        str(path),
        GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None,
        OPEN_EXISTING,
        FILE_FLAG_OPEN_REPARSE_POINT,
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
        if attrs & FILE_ATTRIBUTE_DIRECTORY:
            raise ValueError(f"unexpected_path_type:{path}")
        size = (int(info.nFileSizeHigh) << 32) + int(info.nFileSizeLow)
        return int(handle), size
    except Exception:
        _CloseHandle(handle)
        raise


def _read_text_no_follow(path: Path, *, limit: int) -> str:
    _assert_no_reparse_chain(path)
    if os.name == "nt":
        handle, size = _open_windows_file_no_reparse(path)
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
    return data.decode("utf-8-sig")


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left.expanduser().resolve(strict=False))) == os.path.normcase(
        str(right.expanduser().resolve(strict=False))
    )


def _is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def resolve_trusted_repo_root(repo_root: str | Path) -> Path:
    """実行対象である既存の News-Grasp root だけを trusted root とする。"""

    candidate = Path(repo_root).expanduser()
    _assert_no_reparse_chain(candidate)
    if not candidate.exists() or not candidate.is_dir():
        raise ValueError("trusted_repo_root_missing")
    resolved = candidate.resolve(strict=True)
    canonical = CANONICAL_NEWS_GRASP_REPO_ROOT.expanduser().resolve(strict=True)
    if not _same_path(resolved, canonical):
        raise ValueError("trusted_repo_root_not_canonical")
    _assert_no_reparse_chain(resolved)
    required = (
        resolved / "tools" / "news_grasp_direct_runtime.py",
        resolved / "tools" / "news_grasp_direct_completion.py",
        resolved / "automation" / "news-grasp-6-40" / "completion_guard.py",
    )
    if any(not _is_regular_file_no_reparse(path) for path in required):
        raise ValueError("trusted_repo_root_not_news_grasp")
    return resolved


def _safe_repo_path(repo_root: Path, relative_path: str | Path) -> Path:
    candidate = repo_root / relative_path
    _assert_no_reparse_chain(candidate)
    resolved = candidate.expanduser().resolve(strict=False)
    if not _is_relative_to(resolved, repo_root):
        raise ValueError(f"repo_path_escape:{relative_path}")
    return candidate


def _safe_existing_file(repo_root: Path, relative_path: str | Path) -> Path | None:
    path = _safe_repo_path(repo_root, relative_path)
    return path if _is_regular_file_no_reparse(path) else None


def validate_public_base_url(value: str | None) -> str | None:
    """News-Grasp の公開 GitHub Pages URL だけを受け付ける。"""

    if value is None or not str(value).strip():
        return None
    raw = str(value).strip()
    try:
        parsed = urlsplit(raw)
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError("public_base_url_invalid") from exc
    if parsed.scheme.casefold() not in {"http", "https"}:
        raise ValueError("public_base_url_scheme_invalid")
    if host is None or host.casefold() != EXPECTED_PUBLIC_HOST:
        raise ValueError("public_base_url_host_invalid")
    if parsed.username is not None or parsed.password is not None or port is not None:
        raise ValueError("public_base_url_authority_invalid")
    if parsed.query or parsed.fragment:
        raise ValueError("public_base_url_suffix_invalid")
    path = parsed.path or "/"
    if path.rstrip("/") != EXPECTED_PUBLIC_PATH:
        raise ValueError("public_base_url_path_invalid")
    return f"{parsed.scheme.casefold()}://{EXPECTED_PUBLIC_HOST}{EXPECTED_PUBLIC_PATH}/"


def _run_json(repo_root: Path, args: list[str], *, timeout: int = 120) -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, *args],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    try:
        parsed = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        parsed = {}
    return {
        "ok": proc.returncode == 0,
        "exit_code": proc.returncode,
        "stdout": parsed,
        "stderr": proc.stderr,
        "command": [sys.executable, *args],
    }


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(_read_text_no_follow(path, limit=512_000))
    except FileNotFoundError:
        return {"ok": False, "reason": "missing", "path": str(path)}
    except OSError as exc:
        return {"ok": False, "reason": "missing", "path": str(path), "detail": str(exc)}
    except ValueError as exc:
        return {"ok": False, "reason": "unsafe_path", "path": str(path), "detail": str(exc)}
    except (json.JSONDecodeError, UnicodeError) as exc:
        return {"ok": False, "reason": "invalid_json", "path": str(path), "detail": str(exc)}
    if not isinstance(value, dict):
        return {"ok": False, "reason": "not_object", "path": str(path)}
    return {"ok": True, "path": str(path), "value": value}


def _required_docs(repo_root: Path, issue_date: str) -> dict[str, Any]:
    from tools.publish_inventory import required_published_docs_artifacts

    required = required_published_docs_artifacts(issue_date)
    missing: list[str] = []
    security_errors: list[str] = []
    for rel in required:
        try:
            if _safe_existing_file(repo_root, rel) is None:
                missing.append(rel)
        except ValueError as exc:
            security_errors.append(str(exc))
    return {
        "ok": not missing and not security_errors,
        "issue_date": issue_date,
        "required": required,
        "missing": missing,
        "security_errors": security_errors,
        "semantic_ok": not missing and not security_errors,
        "status": "green" if not missing and not security_errors else "red",
    }


def _required_distribution(repo_root: Path, issue_date: str) -> dict[str, Any]:
    from tools.publish_inventory import required_distribution_artifacts

    required = required_distribution_artifacts(issue_date)
    missing: list[str] = []
    security_errors: list[str] = []
    for rel in required:
        try:
            if _safe_existing_file(repo_root, rel) is None:
                missing.append(rel)
        except ValueError as exc:
            security_errors.append(str(exc))
    state = _load_json(_safe_repo_path(repo_root, Path("data") / "distribution" / f"{issue_date}.json"))
    errors: list[str] = []
    errors.extend(security_errors)
    if not state.get("ok"):
        errors.append(str(state.get("reason") or "distribution_missing"))
    value = state.get("value") if isinstance(state.get("value"), dict) else {}
    if value and value.get("date") != issue_date:
        errors.append("distribution_date_mismatch")
    for field in (
        "date",
        "primary_podcast_state",
        "deepdive_podcast_state",
        "latest_audio_state",
        "deepdive_audio_state",
        "generated_at",
    ):
        if not str(value.get(field) or "").strip():
            errors.append(f"distribution_field_missing:{field}")
    notification = value.get("notification") if isinstance(value.get("notification"), dict) else {}
    nested_value = value.get("value") if isinstance(value.get("value"), dict) else {}
    playlist = value.get("playlist") if isinstance(value.get("playlist"), dict) else {}
    return {
        "ok": not missing and not errors,
        "issue_date": issue_date,
        "required": required,
        "missing": missing,
        "state": {
            "ok": state.get("ok") is True,
            "path": state.get("path"),
            "status": value.get("status"),
            "date": value.get("date"),
            "generated_at": value.get("generated_at"),
            "primary_podcast_state": value.get("primary_podcast_state"),
            "deepdive_podcast_state": value.get("deepdive_podcast_state"),
            "latest_audio_state": value.get("latest_audio_state"),
            "deepdive_audio_state": value.get("deepdive_audio_state"),
            "playlist_status": playlist.get("status", nested_value.get("playlist")),
            "notification_status": notification.get("status", nested_value.get("notification")),
            "notification_sent_count": notification.get("sent_count"),
        },
        "failures": errors,
        "semantic_ok": not missing and not errors,
        "status": "green" if not missing and not errors else "red",
    }


def _publish_status(repo_root: Path, issue_date: str) -> dict[str, Any]:
    state = _load_json(_safe_repo_path(repo_root, Path("docs") / "publish-status.json"))
    value = state.get("value") if isinstance(state.get("value"), dict) else {}
    if state.get("ok"):
        ok = value.get("date") == issue_date and value.get("result") == "published_ok"
    else:
        ok = False
    return {
        "ok": ok,
        "issue_date": issue_date,
        "state": {
            "ok": state.get("ok") is True,
            "path": state.get("path"),
            "date": value.get("date") if isinstance(value, dict) else None,
            "result": value.get("result") if isinstance(value, dict) else None,
            "status": value.get("status") if isinstance(value, dict) else None,
            "updated_at": value.get("updated_at") if isinstance(value, dict) else None,
        },
        "semantic_ok": ok,
        "status": "green" if ok else "red",
    }


def _deepdive_quality(repo_root: Path, issue_date: str) -> dict[str, Any]:
    try:
        from tools import deepdive_quality

        result = deepdive_quality.audit_issue(
            repo_root=repo_root,
            issue_date=issue_date,
            require_rendered_public=True,
        )
    except Exception as exc:  # noqa: BLE001 - verifier reports a typed Red.
        return {
            "ok": False,
            "issue_date": issue_date,
            "reason": str(exc),
            "semantic_ok": False,
            "status": "red",
        }
    result_map = result if isinstance(result, dict) else {}
    ok = result_map.get("status") == "Green" and not result_map.get("issueCodes") and not result_map.get("issues")
    issues = result_map.get("issues") if isinstance(result_map.get("issues"), list) else []
    issue_codes = result_map.get("issueCodes") if isinstance(result_map.get("issueCodes"), list) else []
    return {
        "ok": ok,
        "issue_date": issue_date,
        "result": {
            "status": result_map.get("status"),
            "issue_count": len(issues),
            "issue_codes": issue_codes,
            "articlePath": result_map.get("articlePath"),
            "dialoguePath": result_map.get("dialoguePath"),
            "provenancePath": result_map.get("provenancePath"),
            "renderedPublicPath": result_map.get("renderedPublicPath"),
        },
        "semantic_ok": ok,
        "status": "green" if ok else "red",
    }


def _deepdive_audio(repo_root: Path, issue_date: str) -> dict[str, Any]:
    state = _load_json(_safe_repo_path(repo_root, Path("build") / "tts" / "latest_deepdive_audio.json"))
    value = state.get("value") if isinstance(state.get("value"), dict) else {}
    audio_date = value.get("deepdive_audio_date")
    audio_url = value.get("deepdive_audio_url")
    ok = (
        state.get("ok") is True
        and audio_date == issue_date
        and isinstance(audio_url, str)
        and audio_url.startswith("https://")
        and f"/{issue_date}.mp3" in audio_url
    )
    return {
        "ok": ok,
        "issue_date": issue_date,
        "state": {
            "ok": state.get("ok") is True,
            "path": state.get("path"),
            "deepdive_audio_date": audio_date,
            "deepdive_audio_url": audio_url,
        },
        "semantic_ok": ok,
        "status": "green" if ok else "red",
    }


def _daily_quality(repo_root: Path, issue_date: str) -> dict[str, Any]:
    result = _run_json(
        repo_root,
        [
            "-m",
            "tools.validate_daily_quality",
            "--date",
            issue_date,
            "--require-deepdive",
            "--json",
        ],
        timeout=180,
    )
    ok = result.get("ok") is True
    stdout = result.get("stdout") if isinstance(result.get("stdout"), dict) else {}
    return {
        "ok": ok,
        "issue_date": issue_date,
        "result": {
            "exit_code": result.get("exit_code"),
            "gate_ok": stdout.get("ok"),
            "gate_id": stdout.get("gate_id"),
            "issues": stdout.get("issues", []),
            "command": result.get("command"),
        },
        "semantic_ok": ok,
        "status": "green" if ok else "red",
    }


def _podcast_rows(repo_root: Path, issue_date: str, *, wait_sec: int, poll_sec: int) -> dict[str, dict[str, Any]]:
    from tools.daily_self_heal import verify_podcast

    daily = verify_podcast(
        date=issue_date,
        state_path=_safe_repo_path(repo_root, Path("build") / "youtube-podcast" / "uploads.json"),
        wait_sec=wait_sec,
        poll_sec=poll_sec,
        expected_title=f"News-Grasp Daily News Briefing {issue_date}",
    )
    deepdive = verify_podcast(
        date=issue_date,
        state_path=_safe_repo_path(repo_root, Path("build") / "youtube-podcast-deepdive" / "uploads.json"),
        wait_sec=wait_sec,
        poll_sec=poll_sec,
        expected_title=f"News-Grasp DeepDive Dialogue {issue_date}",
    )
    daily_ok = daily.get("ok") is True
    deepdive_ok = deepdive.get("ok") is True
    playlist_ok = daily_ok and deepdive_ok and bool(daily.get("playlistId")) and bool(deepdive.get("playlistId"))
    daily_projection = {
        "ok": daily.get("ok") is True,
        "reason": daily.get("reason", ""),
        "videoId": daily.get("videoId"),
        "playlistId": daily.get("playlistId"),
        "title": daily.get("title"),
        "verification": daily.get("verification"),
    }
    deepdive_projection = {
        "ok": deepdive.get("ok") is True,
        "reason": deepdive.get("reason", ""),
        "videoId": deepdive.get("videoId"),
        "playlistId": deepdive.get("playlistId"),
        "title": deepdive.get("title"),
        "verification": deepdive.get("verification"),
    }
    return {
        "youtube_daily": {
            "ok": daily_ok,
            "issue_date": issue_date,
            "result": daily_projection,
            "semantic_ok": daily_ok,
            "status": "green" if daily_ok else "red",
        },
        "youtube_deepdive": {
            "ok": deepdive_ok,
            "issue_date": issue_date,
            "result": deepdive_projection,
            "semantic_ok": deepdive_ok,
            "status": "green" if deepdive_ok else "red",
        },
        "playlist": {
            "ok": playlist_ok,
            "issue_date": issue_date,
            "result": {"daily": daily_projection, "deepdive": deepdive_projection},
            "semantic_ok": playlist_ok,
            "status": "green" if playlist_ok else "red",
        },
    }


def _notification(repo_root: Path, issue_date: str) -> dict[str, Any]:
    candidates = [
        _safe_repo_path(repo_root, Path("build") / "push" / f"{issue_date}.json"),
        _safe_repo_path(repo_root, Path("build") / "notification" / f"{issue_date}.json"),
        _safe_repo_path(repo_root, Path("build") / "notifications" / f"{issue_date}.json"),
    ]
    observed = [_load_json(path) for path in candidates if _is_regular_file_no_reparse(path)]
    ok = False
    for row in observed:
        value = row.get("value") if isinstance(row.get("value"), dict) else {}
        sent = value.get("sent_count", value.get("sentCount", value.get("delivered_count", 0)))
        ok = ok or (not isinstance(sent, bool) and isinstance(sent, int) and sent >= 1)
        ok = ok or str(value.get("status") or "").casefold() in {"sent", "already_sent", "green"}
    return {
        "ok": ok,
        "issue_date": issue_date,
        "observed": [
            {
                "ok": row.get("ok") is True,
                "path": row.get("path"),
                "status": (row.get("value") if isinstance(row.get("value"), dict) else {}).get("status"),
                "sent_count": (row.get("value") if isinstance(row.get("value"), dict) else {}).get("sent_count"),
                "subscription_count": (row.get("value") if isinstance(row.get("value"), dict) else {}).get("subscription_count"),
                "recorded_at": (row.get("value") if isinstance(row.get("value"), dict) else {}).get("recorded_at"),
            }
            for row in observed
        ],
        "semantic_ok": ok,
        "status": "green" if ok else "red",
    }


def _public_web(
    repo_root: Path,
    issue_date: str,
    *,
    public_base_url: str,
    remote: str,
    branch: str,
    wait_sec: int,
    poll_sec: int,
) -> dict[str, Any]:
    del repo_root, remote, branch, wait_sec, poll_sec

    base = public_base_url.rstrip("/") + "/"
    paths = {
        "home": "",
        "daily": f"{issue_date}/",
        "summary": f"{issue_date}/summary/",
        "deepdive": f"deepdive/{issue_date}/",
        "publish_status": "publish-status.json",
    }
    observed: dict[str, dict[str, Any]] = {}
    failures: list[str] = []

    for name, rel in paths.items():
        url = base + rel
        try:
            with urllib.request.urlopen(url, timeout=20) as response:  # noqa: S310 - configured public site probe.
                body = response.read(512_000).decode("utf-8", errors="replace")
                code = int(getattr(response, "status", 200))
        except (OSError, urllib.error.URLError, UnicodeError) as exc:
            observed[name] = {
                "ok": False,
                "url": url,
                "reason": str(exc),
                "semantic_ok": False,
                "status": "red",
            }
            failures.append(f"fetch_failed:{name}")
            continue

        contains_issue_date = issue_date in body
        observed[name] = {
            "ok": 200 <= code < 300,
            "url": url,
            "status_code": code,
            "contains_issue_date": contains_issue_date,
            "semantic_ok": 200 <= code < 300 and (name in {"home", "publish_status"} or contains_issue_date),
            "status": "green" if 200 <= code < 300 else "red",
        }
        if not observed[name]["ok"]:
            failures.append(f"http_red:{name}")
        if name not in {"home", "publish_status"} and not contains_issue_date:
            failures.append(f"issue_date_missing:{name}")

    status_row = observed.get("publish_status")
    if status_row and status_row.get("ok") is True:
        try:
            with urllib.request.urlopen(status_row["url"], timeout=20) as response:  # noqa: S310 - configured public site probe.
                status_value = json.loads(response.read(256_000).decode("utf-8", errors="replace"))
        except (OSError, urllib.error.URLError, UnicodeError, json.JSONDecodeError) as exc:
            status_row["semantic_ok"] = False
            status_row["json_error"] = str(exc)
            failures.append("publish_status_public_json_invalid")
        else:
            status_row["json"] = status_value
            if not isinstance(status_value, dict) or status_value.get("date") != issue_date:
                status_row["semantic_ok"] = False
                failures.append("publish_status_public_date_mismatch")

    ok = not failures and all(row.get("semantic_ok") is True for row in observed.values())
    return {
        "ok": ok,
        "issue_date": issue_date,
        "observed": observed,
        "failures": failures,
        "semantic_ok": ok,
        "status": "green" if ok else "red",
    }


def _up_to_date_observation(repo_root: Path, remote: str, branch: str) -> dict[str, Any]:
    status_proc = subprocess.run(
        ["git", "status", "--porcelain=v1", "-b"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    tracked_diff_proc = subprocess.run(
        ["git", "diff", "--quiet"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    staged_diff_proc = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    remote_contains_local_proc = subprocess.run(
        ["git", "merge-base", "--is-ancestor", "HEAD", f"{remote}/{branch}"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    local_contains_remote_proc = subprocess.run(
        ["git", "merge-base", "--is-ancestor", f"{remote}/{branch}", "HEAD"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    text = status_proc.stdout
    status_lines = [line for line in text.splitlines() if not line.startswith("##")]
    branch_ok = f"## {branch}..." in text or text.lstrip().startswith("## HEAD (no branch)")
    local_clean = (
        status_proc.returncode == 0
        and tracked_diff_proc.returncode == 0
        and staged_diff_proc.returncode == 0
        and not status_lines
    )
    remote_graph_aligned = (
        remote_contains_local_proc.returncode == 0
        and local_contains_remote_proc.returncode == 0
    )
    ok = (
        local_clean
        and branch_ok
        and remote_graph_aligned
        and "ahead" not in text
        and "behind" not in text
    )
    return {
        "ok": ok,
        "remote": remote,
        "branch": branch,
        "stdout": text,
        "exit_code": status_proc.returncode,
        "local_clean": local_clean,
        "tracked_diff_exit_code": tracked_diff_proc.returncode,
        "staged_diff_exit_code": staged_diff_proc.returncode,
        "remote_contains_local": remote_contains_local_proc.returncode == 0,
        "local_contains_remote": local_contains_remote_proc.returncode == 0,
        "remote_graph_aligned": remote_graph_aligned,
        "detached_worktree": text.lstrip().startswith("## HEAD (no branch)"),
        "semantic_ok": ok,
        "status": "green" if ok else "red",
    }


def verify_direct_public_completion(
    *,
    repo_root: Path,
    issue_date: str,
    public_base_url: str,
    remote: str = "origin",
    branch: str = "main",
    wait_sec: int = 0,
    poll_sec: int = 30,
) -> dict[str, Any]:
    public_base_url = validate_public_base_url(public_base_url)
    repo = resolve_trusted_repo_root(repo_root)
    surfaces: dict[str, dict[str, Any]] = {}
    surfaces["web"] = _required_docs(repo, issue_date)
    surfaces["deepdive_article"] = _deepdive_quality(repo, issue_date)
    surfaces["daily_audio"] = _daily_quality(repo, issue_date)
    surfaces["deepdive_audio"] = _deepdive_audio(repo, issue_date)
    surfaces["distribution"] = _required_distribution(repo, issue_date)
    surfaces["publish_status"] = _publish_status(repo, issue_date)
    surfaces.update(_podcast_rows(repo, issue_date, wait_sec=wait_sec, poll_sec=poll_sec))
    surfaces["notification"] = _notification(repo, issue_date)
    surfaces["pages"] = _public_web(
        repo,
        issue_date,
        public_base_url=public_base_url,
        remote=remote,
        branch=branch,
        wait_sec=wait_sec,
        poll_sec=poll_sec,
    )
    surfaces["remote_commit"] = {
        **_up_to_date_observation(repo, remote, branch),
        "issue_date": issue_date,
    }
    failures: list[str] = []
    for name in PUBLIC_SURFACES:
        row = surfaces.get(name)
        if not isinstance(row, dict) or row.get("ok") is not True or row.get("semantic_ok") is not True:
            failures.append(f"public_surface_red:{name}")
    return {
        "schemaVersion": PUBLIC_SCHEMA,
        "ok": not failures,
        "completion_mode": "direct_public_v1",
        "issue_date": issue_date,
        "status": "green" if not failures else "red",
        "public_surfaces": surfaces,
        "failures": failures,
    }
