"""Semantic public verifier for News-Grasp direct mainline.

この module は caller が作った completion JSON を authority にしない。既存の
repo-local validator と public probe を呼び、読者可視 surface の観測を組み立てる。
旧 runner finalizer / readiness / producer lineage は呼ばない。
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlencode, urlsplit

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


PUBLIC_SCHEMA = "NEWS_GRASP_DIRECT_PUBLIC_VERIFICATION_V1"
PUBLIC_SCHEMA_V2 = "NEWS_GRASP_DIRECT_PUBLIC_VERIFICATION_V2"
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


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        return None


def _open_public_no_redirect(request: urllib.request.Request, *, timeout: int):
    parsed = urlsplit(request.full_url)
    if parsed.scheme.casefold() != "https" or (parsed.hostname or "").casefold() != EXPECTED_PUBLIC_HOST or not parsed.path.startswith(EXPECTED_PUBLIC_PATH + "/"):
        raise ValueError("public_probe_url_invalid")
    opener = urllib.request.build_opener(_NoRedirect())
    response = opener.open(request, timeout=timeout)
    final = urlsplit(response.geturl())
    if final.scheme.casefold() != "https" or (final.hostname or "").casefold() != EXPECTED_PUBLIC_HOST or final.path != parsed.path:
        response.close()
        raise ValueError("public_probe_redirect_forbidden")
    return response


def _open_github_actions_no_redirect(request: urllib.request.Request, *, timeout: int):
    expected_path = "/repos/HIDEPON-UMG/News-Grasp/actions/workflows/deploy-pages.yml/runs"
    expected_query = "branch=main&event=push&per_page=20"
    parsed = urlsplit(request.full_url)
    if (
        parsed.scheme.casefold() != "https"
        or (parsed.hostname or "").casefold() != "api.github.com"
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != expected_path
        or parsed.query != expected_query
        or parsed.fragment
    ):
        raise ValueError("pages_workflow_api_url_invalid")
    response = urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout)
    final = urlsplit(response.geturl())
    if final != parsed or int(getattr(response, "status", response.getcode())) != 200:
        response.close()
        raise ValueError("pages_workflow_api_redirect_forbidden")
    return response


def _validate_transport_policy(*, remote: str, branch: str, wait_sec: int, poll_sec: int) -> None:
    if remote != "origin" or branch != "main":
        raise ValueError("public_git_target_not_canonical")
    if isinstance(wait_sec, bool) or isinstance(poll_sec, bool):
        raise ValueError("public_probe_timing_invalid")
    if wait_sec != 0 and not 5 <= wait_sec <= 900:
        raise ValueError("public_probe_wait_out_of_policy")
    if not 5 <= poll_sec <= 120:
        raise ValueError("public_probe_poll_out_of_policy")
    if wait_sec > 0 and poll_sec > wait_sec:
        raise ValueError("public_probe_poll_exceeds_wait")

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
    _GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
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


def _read_bytes_no_follow(path: Path, *, limit: int) -> bytes:
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
    return data


def _read_git_blob_bounded(repo_root: Path, *, commit: str, path: str, limit: int = 2 * 1024 * 1024) -> tuple[str, bytes] | None:
    """commit:pathをimmutable blob IDへ解決し、size gate後だけbounded captureする。"""
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None or path != "tools/send_push.py":
        return None
    flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    identity = subprocess.run(
        ["git", "rev-parse", f"{commit}:{path}"], cwd=str(repo_root), capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=30, check=False,
        shell=False, creationflags=flags,
    )
    blob_id = identity.stdout.strip()
    if identity.returncode != 0 or re.fullmatch(r"[0-9a-f]{40,64}", blob_id) is None:
        return None
    kind = subprocess.run(
        ["git", "cat-file", "-t", blob_id], cwd=str(repo_root), capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=30, check=False,
        shell=False, creationflags=flags,
    )
    size = subprocess.run(
        ["git", "cat-file", "-s", blob_id], cwd=str(repo_root), capture_output=True,
        text=True, encoding="ascii", errors="replace", timeout=30, check=False,
        shell=False, creationflags=flags,
    )
    try:
        byte_count = int(size.stdout.strip())
    except ValueError:
        return None
    if kind.returncode != 0 or kind.stdout.strip() != "blob" or size.returncode != 0 or not 0 <= byte_count <= limit:
        return None
    blob = subprocess.run(
        ["git", "cat-file", "blob", blob_id], cwd=str(repo_root), capture_output=True,
        timeout=30, check=False, shell=False, creationflags=flags,
    )
    if blob.returncode != 0 or len(blob.stdout) != byte_count or len(blob.stdout) > limit:
        return None
    return blob_id, blob.stdout


def _read_text_no_follow(path: Path, *, limit: int) -> str:
    return _read_bytes_no_follow(path, limit=limit).decode("utf-8-sig")


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
    """canonical checkoutまたは同じgit-common-dirのclean worktreeだけを信頼する。"""

    candidate = Path(repo_root).expanduser()
    _assert_no_reparse_chain(candidate)
    if not candidate.exists() or not candidate.is_dir():
        raise ValueError("trusted_repo_root_missing")
    resolved = candidate.resolve(strict=True)
    canonical = CANONICAL_NEWS_GRASP_REPO_ROOT.expanduser().resolve(strict=True)
    _assert_no_reparse_chain(resolved)
    required = (
        resolved / "tools" / "news_grasp_direct_runtime.py",
        resolved / "tools" / "news_grasp_direct_completion.py",
        resolved / "automation" / "news-grasp-6-40" / "completion_guard.py",
    )
    if any(not _is_regular_file_no_reparse(path) for path in required):
        raise ValueError("trusted_repo_root_not_news_grasp")
    if not _same_path(resolved, canonical):
        def common_dir(path: Path) -> Path | None:
            result = subprocess.run(
                ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                cwd=str(path),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
                shell=False,
                creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
            )
            return Path(result.stdout.strip()).resolve(strict=False) if result.returncode == 0 and result.stdout.strip() else None

        if common_dir(resolved) != common_dir(canonical):
            raise ValueError("trusted_repo_root_not_canonical_worktree")
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
    if parsed.scheme.casefold() != "https":
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
    child_env = os.environ.copy()
    child_env.pop("NEWS_GRASP_SKIP_URL_CHECK", None)
    proc = subprocess.run(
        [sys.executable, *args],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        shell=False,
        env=child_env,
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
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


def _required_docs(
    repo_root: Path,
    issue_date: str,
    *,
    manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
    semantic = {"ok": True, "reasonCodes": []}
    manifest_check = {"ok": True, "reasonCodes": []}
    if manifest is not None:
        from tools.news_grasp_publish_contract import verify_manifest, verify_semantic_pages

        manifest_check = verify_manifest(manifest, repo_root=repo_root, require_files=True)
        local_pages: dict[str, str] = {}
        page_paths = {
            "home": Path("docs/index.html"),
            "daily": Path("docs") / issue_date / "index.html",
            "summary": Path("docs") / issue_date / "summary" / "index.html",
            "deepdive": Path("docs") / "deepdive" / issue_date / "index.html",
            "publish_status": Path("docs") / "publish-status.json",
        }
        for name, relative in page_paths.items():
            try:
                local_pages[name] = _read_text_no_follow(_safe_repo_path(repo_root, relative), limit=1_000_000)
            except (OSError, UnicodeError, ValueError):
                local_pages[name] = ""
        semantic = verify_semantic_pages(manifest, local_pages)
    ok = not missing and not security_errors and manifest_check.get("ok") is True and semantic.get("ok") is True
    return {
        "ok": ok,
        "issue_date": issue_date,
        "required": required,
        "missing": missing,
        "security_errors": security_errors,
        "manifest": manifest_check,
        "semantic": semantic,
        "semantic_ok": ok,
        "status": "verified" if ok else "blocked",
    }


def _required_distribution(
    repo_root: Path,
    issue_date: str,
    *,
    manifest: dict[str, Any] | None = None,
    run_id: str = "",
    run_intent: str = "scheduled_production_direct",
) -> dict[str, Any]:
    from tools.publish_inventory import required_distribution_artifacts

    manifest_kind_errors: list[str] = []
    if manifest is None:
        required = required_distribution_artifacts(issue_date)
    else:
        distribution_kinds = {
            "daily_audio_state",
            "deepdive_audio_state",
            "youtube_daily_state",
            "youtube_deepdive_state",
            "playlist_state",
            "distribution_binding",
            "notification_v2",
            "distribution",
        }
        required = [
            str(row.get("localPath") or "")
            for row in manifest.get("entries") or []
            if isinstance(row, dict)
            and row.get("artifactKind") in distribution_kinds
            and row.get("required") is True
        ]
        observed_kinds = {
            str(row.get("artifactKind") or "")
            for row in manifest.get("entries") or []
            if isinstance(row, dict)
        }
        manifest_kind_errors.extend(
            f"manifest_distribution_kind_missing:{kind}"
            for kind in sorted(distribution_kinds - observed_kinds)
        )
    missing: list[str] = []
    security_errors: list[str] = []
    for rel in required:
        try:
            if _safe_existing_file(repo_root, rel) is None:
                missing.append(rel)
        except ValueError as exc:
            security_errors.append(str(exc))
    state = _load_json(_safe_repo_path(repo_root, Path("data") / "distribution" / f"{issue_date}.json"))
    errors: list[str] = list(manifest_kind_errors)
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
    playlist_binding = _load_json(_safe_repo_path(repo_root, Path("build") / "distribution" / issue_date / "playlist.json"))
    binding = playlist_binding.get("value") if isinstance(playlist_binding.get("value"), dict) else {}
    binding_body = {key: item for key, item in binding.items() if key != "receiptSha256"}
    binding_sha = hashlib.sha256(json.dumps(binding_body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    if (
        binding.get("schemaVersion") != "NEWS_GRASP_PLAYLIST_BINDING_V2"
        or binding.get("issueDate") != issue_date
        or binding.get("runId") != run_id
        or binding.get("runIntent") != run_intent
        or binding.get("status") != "verified"
        or binding.get("receiptSha256") != binding_sha
        or binding.get("daily") != playlist.get("daily")
        or binding.get("deepdive") != playlist.get("deepdive")
    ):
        errors.append("distribution_playlist_run_binding_invalid")
    distribution_binding_row = _load_json(
        _safe_repo_path(repo_root, Path("build") / "distribution" / issue_date / "binding.json")
    )
    distribution_binding = (
        distribution_binding_row.get("value")
        if isinstance(distribution_binding_row.get("value"), dict)
        else {}
    )
    distribution_binding_body = {
        key: item for key, item in distribution_binding.items() if key != "receiptSha256"
    }
    distribution_binding_sha = hashlib.sha256(
        json.dumps(distribution_binding_body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    identity_paths = {
        "distributionSha256": Path("data") / "distribution" / f"{issue_date}.json",
        "dailyAudioProjectionSha256": Path("build") / "tts" / "daily" / "latest_audio.json",
        "deepdiveAudioProjectionSha256": Path("build") / "tts" / "deepdive" / "latest_audio.json",
        "notificationStateSha256": Path("build") / "notification" / f"{issue_date}.json",
        "youtubeDailyStateSha256": Path("build") / "youtube-podcast" / "uploads.json",
        "youtubeDeepdiveStateSha256": Path("build") / "youtube-podcast-deepdive" / "uploads.json",
        "playlistBindingStateSha256": Path("build") / "distribution" / issue_date / "playlist.json",
    }
    live_identities: dict[str, str] = {}
    for field, relative in identity_paths.items():
        try:
            live_identities[field] = hashlib.sha256(
                _read_bytes_no_follow(_safe_repo_path(repo_root, relative), limit=1_048_576)
            ).hexdigest()
        except (OSError, ValueError):
            live_identities[field] = "unverified"
    if (
        distribution_binding.get("schemaVersion") != "NEWS_GRASP_DISTRIBUTION_BINDING_V2"
        or distribution_binding.get("issueDate") != issue_date
        or distribution_binding.get("runId") != run_id
        or distribution_binding.get("runIntent") != run_intent
        or distribution_binding.get("status") != "verified"
        or distribution_binding.get("receiptSha256") != distribution_binding_sha
        or distribution_binding.get("playlistReceiptSha256") != binding.get("receiptSha256")
        or any(distribution_binding.get(field) != identity for field, identity in live_identities.items())
    ):
        errors.append("distribution_run_binding_invalid")
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
            "distribution_binding_receipt": distribution_binding.get("receiptSha256"),
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
            route="production_generation",
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


def _audio_projection(
    repo_root: Path,
    issue_date: str,
    *,
    audio_type: str,
    run_id: str,
    run_intent: str,
) -> dict[str, Any]:
    from tools.news_grasp_audio_projection import _probe_public_audio, canonical_audio_path, load_audio_projection, validate_audio_projection

    candidates = [repo_root / canonical_audio_path(audio_type)]
    if audio_type == "daily":
        candidates.append(repo_root / "build" / "tts" / "latest_audio.json")
    else:
        candidates.extend([
            repo_root / "build" / "tts" / "latest_deepdive_audio.json",
            repo_root / "build" / "tts" / "deepdive" / "latest_audio.json",
        ])
    source = next((path for path in candidates if path.is_file()), candidates[0])
    try:
        projection = load_audio_projection(source, audio_type=audio_type, run_id=run_id, run_intent=run_intent)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return {"ok": False, "issue_date": issue_date, "reason": str(exc), "path": str(source), "semantic_ok": False, "status": "blocked"}
    validation = validate_audio_projection(projection, issue_date=issue_date, run_intent=run_intent)
    public_observation = _probe_public_audio(str(projection.get("publicUrl") or "")) if validation["ok"] is True else {"ok": False, "reasonCode": "audio_projection_red"}
    ok = validation["ok"] is True and projection.get("runId") == run_id and public_observation.get("ok") is True
    return {
        "ok": ok,
        "issue_date": issue_date,
        "state": projection,
        "path": str(source),
        "reasonCodes": validation["reasonCodes"] + ([] if projection.get("runId") == run_id else ["audio_run_id_mismatch"]) + ([] if public_observation.get("ok") is True else [str(public_observation.get("reasonCode") or "audio_public_asset_unverified")]),
        "publicObservation": public_observation,
        "semantic_ok": ok,
        "status": "verified" if ok else "blocked",
    }


def _deepdive_audio(repo_root: Path, issue_date: str, *, run_id: str = "", run_intent: str = "scheduled_production_direct") -> dict[str, Any]:
    return _audio_projection(repo_root, issue_date, audio_type="deepdive", run_id=run_id, run_intent=run_intent)


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
    child_env = os.environ.copy()
    child_env.pop("NEWS_GRASP_SKIP_URL_CHECK", None)
    live = subprocess.run(
        [sys.executable, "-m", "tools.validate_deepdive_urls", str(repo_root / "digest" / "DeepDive" / f"{issue_date}-DeepDive.md")],
        cwd=str(repo_root), capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180, check=False, shell=False, env=child_env,
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
    )
    ok = result.get("ok") is True and live.returncode == 0
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
            "liveUrlCheck": "verified" if live.returncode == 0 else "blocked",
            "liveUrlExitCode": live.returncode,
        },
        "semantic_ok": ok,
        "status": "green" if ok else "red",
    }


def _podcast_rows(
    repo_root: Path,
    issue_date: str,
    *,
    wait_sec: int,
    poll_sec: int,
    run_id: str,
    run_intent: str,
) -> dict[str, dict[str, Any]]:
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
    binding_row = _load_json(_safe_repo_path(repo_root, Path("build") / "distribution" / issue_date / "playlist.json"))
    binding = binding_row.get("value") if isinstance(binding_row.get("value"), dict) else {}
    binding_body = {key: item for key, item in binding.items() if key != "receiptSha256"}
    binding_sha = hashlib.sha256(json.dumps(binding_body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    uploads: dict[str, dict[str, Any]] = {}
    for kind, relative in (("daily", Path("build") / "youtube-podcast" / "uploads.json"), ("deepdive", Path("build") / "youtube-podcast-deepdive" / "uploads.json")):
        upload_row = _load_json(_safe_repo_path(repo_root, relative))
        upload_value = upload_row.get("value") if isinstance(upload_row.get("value"), dict) else {}
        uploads[kind] = dict(upload_value.get(issue_date) or {}) if isinstance(upload_value.get(issue_date), dict) else {}
    binding_ok = (
        binding.get("schemaVersion") == "NEWS_GRASP_PLAYLIST_BINDING_V2"
        and binding.get("issueDate") == issue_date
        and binding.get("runId") == run_id
        and binding.get("runIntent") == run_intent
        and binding.get("status") == "verified"
        and binding.get("receiptSha256") == binding_sha
        and all(
            isinstance(binding.get(kind), dict)
            and all(binding[kind].get(field) == uploads[kind].get(field) and bool(binding[kind].get(field)) for field in ("videoId", "playlistId", "playlistItemId"))
            for kind in ("daily", "deepdive")
        )
    )
    daily_ok = daily.get("ok") is True and binding_ok and daily.get("videoId") == binding.get("daily", {}).get("videoId") and daily.get("playlistId") == binding.get("daily", {}).get("playlistId")
    deepdive_ok = deepdive.get("ok") is True and binding_ok and deepdive.get("videoId") == binding.get("deepdive", {}).get("videoId") and deepdive.get("playlistId") == binding.get("deepdive", {}).get("playlistId")
    playlist_ok = daily_ok and deepdive_ok
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


def _notification(
    repo_root: Path,
    issue_date: str,
    *,
    run_id: str = "",
    run_intent: str = "scheduled_production_direct",
) -> dict[str, Any]:
    candidates = [
        _safe_repo_path(repo_root, Path("build") / "push" / f"{issue_date}.json"),
        _safe_repo_path(repo_root, Path("build") / "notification" / f"{issue_date}.json"),
        _safe_repo_path(repo_root, Path("build") / "notifications" / f"{issue_date}.json"),
    ]
    observed = [_load_json(path) for path in candidates if _is_regular_file_no_reparse(path)]
    ok = False
    v2_failures: list[str] = []
    trusted_sender_path = _safe_repo_path(repo_root, Path("tools") / "send_push.py")
    try:
        trusted_sender_sha = hashlib.sha256(_read_bytes_no_follow(trusted_sender_path, limit=2_000_000)).hexdigest()
    except (OSError, ValueError):
        trusted_sender_sha = ""
        v2_failures.append("notification_trusted_sender_source_missing")
    for row in observed:
        value = row.get("value") if isinstance(row.get("value"), dict) else {}
        sent = value.get("sent_count", value.get("sentCount", value.get("delivered_count", 0)))
        ok = ok or (not isinstance(sent, bool) and isinstance(sent, int) and sent >= 1)
        ok = ok or str(value.get("status") or "").casefold() in {"sent", "already_sent", "green"}
        receipt = value.get("deliveryReceiptV2") if isinstance(value.get("deliveryReceiptV2"), dict) else {}
        if receipt:
            body = {key: item for key, item in receipt.items() if key != "receiptSha256"}
            expected_sha = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
            if receipt.get("schemaVersion") != "NEWS_GRASP_NOTIFICATION_DELIVERY_RECEIPT_V2":
                v2_failures.append("notification_v2_schema_invalid")
            if receipt.get("issueDate") != issue_date:
                v2_failures.append("notification_v2_issue_date_mismatch")
            if run_id and receipt.get("runId") != run_id:
                v2_failures.append("notification_v2_run_id_mismatch")
            if receipt.get("runIntent") != run_intent:
                v2_failures.append("notification_v2_run_intent_mismatch")
            if receipt.get("receiptSha256") != expected_sha:
                v2_failures.append("notification_v2_receipt_hash_invalid")
            if receipt.get("status") not in {"sent", "already_sent"}:
                v2_failures.append("notification_v2_status_invalid")
            adapter = value.get("deliveryReceipt") if isinstance(value.get("deliveryReceipt"), dict) else {}
            adapter_body = {key: item for key, item in adapter.items() if key != "receiptSha256"}
            adapter_sha = hashlib.sha256(json.dumps(adapter_body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
            if (
                adapter.get("schemaVersion") != "NEWS_GRASP_NOTIFICATION_DELIVERY_RECEIPT_V1"
                or adapter.get("receiptSha256") != adapter_sha
                or adapter.get("producer") != "tools.send_push"
                or adapter.get("producerSha256") != trusted_sender_sha
                or not re.fullmatch(r"[0-9a-f]{32}", str(adapter.get("producerRunId") or ""))
            ):
                v2_failures.append("notification_trusted_sender_adapter_invalid")
            expected_ledger_id = f"{issue_date}.delivery.json"
            expected_v2_id = f"{issue_date}.delivery-v2.json"
            if receipt.get("status") == "already_sent" and adapter.get("priorDeliveryReceiptPath") != expected_ledger_id:
                v2_failures.append("notification_prior_delivery_path_id_invalid")
            v2_path_id = value.get("deliveryReceiptV2Path")
            if v2_path_id != expected_v2_id:
                v2_failures.append("notification_v2_path_id_invalid")
            else:
                state_path = Path(str(row.get("path") or ""))
                try:
                    if _safe_existing_file(repo_root, state_path.parent / expected_v2_id) is None:
                        v2_failures.append("notification_v2_evidence_missing")
                except ValueError:
                    v2_failures.append("notification_v2_evidence_invalid")
            ledger_value = value.get("evidenceLedgerPath")
            ledger_path: Path | None = None
            if ledger_value != expected_ledger_id:
                v2_failures.append("notification_sender_ledger_path_id_invalid")
            elif isinstance(ledger_value, str) and ledger_value:
                try:
                    state_path = Path(str(row.get("path") or ""))
                    candidate = state_path.parent / ledger_value
                    ledger_path = _safe_existing_file(repo_root, candidate)
                except ValueError:
                    ledger_path = None
            if ledger_path is None:
                v2_failures.append("notification_sender_ledger_missing")
            else:
                ledger_row = _load_json(ledger_path)
                ledger = ledger_row.get("value") if isinstance(ledger_row.get("value"), dict) else {}
                ledger_body = {key: item for key, item in ledger.items() if key != "receiptSha256"}
                ledger_receipt_sha = hashlib.sha256(json.dumps(ledger_body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
                ledger_file_sha = hashlib.sha256(_read_bytes_no_follow(ledger_path, limit=65536)).hexdigest()
                if ledger.get("schemaVersion") != "NEWS_GRASP_NOTIFICATION_DELIVERY_RECEIPT_V1" or ledger.get("receiptSha256") != ledger_receipt_sha:
                    v2_failures.append("notification_sender_ledger_invalid")
                if (
                    ledger.get("producer") != "tools.send_push"
                    or not re.fullmatch(r"[0-9a-f]{64}", str(ledger.get("producerSha256") or ""))
                    or not re.fullmatch(r"[0-9a-f]{32}", str(ledger.get("producerRunId") or ""))
                ):
                    v2_failures.append("notification_sender_identity_invalid")
                sender_commit = str(receipt.get("senderSourceCommit") or "")
                sender_path = str(receipt.get("senderSourcePath") or "")
                sender_blob_id = str(receipt.get("senderSourceBlobId") or "")
                sender_blob_sha = str(receipt.get("senderSourceBlobSha256") or "")
                blob = _read_git_blob_bounded(repo_root, commit=sender_commit, path=sender_path)
                if (
                    blob is None
                    or sender_blob_sha != ledger.get("producerSha256")
                    or hashlib.sha256(blob[1]).hexdigest() != sender_blob_sha
                    or blob[0] != sender_blob_id
                ):
                    v2_failures.append("notification_sender_git_blob_binding_invalid")
                if value.get("evidenceLedgerFileSha256") != ledger_file_sha:
                    v2_failures.append("notification_sender_ledger_file_identity_mismatch")
                if receipt.get("priorDeliveryReceiptSha256") != ledger.get("receiptSha256"):
                    v2_failures.append("notification_sender_ledger_receipt_mismatch")
                if not str(ledger.get("producerRunId") or ""):
                    v2_failures.append("notification_sender_event_id_missing")
                if receipt.get("senderEventId") != ledger.get("producerRunId"):
                    v2_failures.append("notification_sender_event_id_mismatch")
                if (
                    receipt.get("status") == "already_sent"
                    and (
                        adapter.get("priorDeliveryReceiptSha256") != ledger.get("receiptSha256")
                        or adapter.get("priorDeliveryReceiptFileSha256") != ledger_file_sha
                        or receipt.get("priorDeliveryReceiptSha256") != ledger.get("receiptSha256")
                    )
                ):
                    v2_failures.append("notification_prior_delivery_adapter_binding_invalid")
                if (
                    ledger.get("date") != issue_date
                    or ledger.get("payloadSha256") != receipt.get("payloadIdentity")
                    or ledger.get("audienceSetSha256") != receipt.get("audienceIdentity")
                    or ledger.get("sentCount") != receipt.get("sentCount")
                    or ledger.get("subscriptionCount") != receipt.get("subscriptionCount")
                ):
                    v2_failures.append("notification_sender_ledger_binding_mismatch")
                recipient_results = receipt.get("recipientResults") if isinstance(receipt.get("recipientResults"), list) else []
                keys = [str(item.get("recipientKey") or "") for item in recipient_results if isinstance(item, Mapping)]
                if (
                    isinstance(receipt.get("sentCount"), bool)
                    or not isinstance(receipt.get("sentCount"), int)
                    or int(receipt.get("sentCount") or 0) < 1
                    or len(recipient_results) != int(receipt.get("sentCount") or 0)
                    or len(keys) != len(recipient_results)
                    or len(keys) != len(set(keys))
                    or any(not re.fullmatch(r"[0-9a-f]{64}", key) for key in keys)
                    or any(str(item.get("status") or "") != receipt.get("status") for item in recipient_results if isinstance(item, Mapping))
                ):
                    v2_failures.append("notification_recipient_results_invalid")
        else:
            v2_failures.append("notification_v2_receipt_missing")
    ok = ok and not v2_failures
    warning = {
        "surface": "notification",
        "reasonCode": "notification_provider_delivery_ack_unavailable",
        "status": "warning",
        "evidenceRef": "immutable_preexisting_sender_ledger_git_blob_bound",
    }
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
        "failures": sorted(set(v2_failures)),
        "semantic_ok": ok,
        "status": "verified_with_warnings" if ok else "red",
        "post_publish_issue_list": [warning] if ok else [],
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
    manifest: dict[str, Any] | None = None,
    cache_bust: bool = False,
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
    if manifest is not None:
        for row in manifest.get("entries") or []:
            if not isinstance(row, Mapping) or row.get("artifactKind") != "category_html" or row.get("required") is not True:
                continue
            public_url = str(row.get("publicUrl") or "")
            if not public_url.startswith(base):
                raise ValueError("manifest_category_public_url_outside_base")
            category_id = next((value for value in manifest.get("scheduledCategoryIds") or [] if f"/{value}/{issue_date}/" in urlsplit(public_url).path), "")
            if not category_id:
                raise ValueError("manifest_category_public_url_unbound")
            paths[f"category:{category_id}"] = public_url[len(base):]
    observed: dict[str, dict[str, Any]] = {}
    bodies: dict[str, str] = {}
    failures: list[str] = []

    for name, rel in paths.items():
        url = base + rel
        if cache_bust:
            marker = str((manifest or {}).get("manifestId") or "unverified")
            nonce = secrets.token_hex(6)
            url += "?" + urlencode({"v": f"{marker}-{nonce}"})
        try:
            request = urllib.request.Request(url, headers={"Cache-Control": "no-cache", "Pragma": "no-cache"})
            with _open_public_no_redirect(request, timeout=20) as response:
                raw_body = response.read(512_001)
                if len(raw_body) > 512_000:
                    raise ValueError("public_surface_body_too_large")
                body = raw_body.decode("utf-8", errors="replace")
                code = int(getattr(response, "status", 200))
        except (OSError, urllib.error.URLError, UnicodeError, ValueError) as exc:
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
        bodies[name] = body
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
            request = urllib.request.Request(status_row["url"], headers={"Cache-Control": "no-cache", "Pragma": "no-cache"})
            with _open_public_no_redirect(request, timeout=20) as response:
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

    semantic = {"ok": True, "reasonCodes": []}
    if manifest is not None:
        from tools.news_grasp_publish_contract import verify_semantic_pages

        semantic = verify_semantic_pages(manifest, bodies)
        failures.extend(semantic.get("reasonCodes") or [])

    ok = not failures and semantic.get("ok") is True and all(row.get("semantic_ok") is True for row in observed.values())
    return {
        "ok": ok,
        "issue_date": issue_date,
        "observed": observed,
        "failures": failures,
        "semantic": semantic,
        "semantic_ok": ok,
        "status": "green" if ok else "red",
    }


def _up_to_date_observation(repo_root: Path, remote: str, branch: str) -> dict[str, Any]:
    if remote != "origin" or branch != "main":
        raise ValueError("public_git_target_not_canonical")
    def git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args], cwd=str(repo_root), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30, check=False,
            shell=False, creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        )

    status_proc = git("status", "--porcelain=v1", "-b")
    tracked_diff_proc = git("diff", "--quiet")
    staged_diff_proc = git("diff", "--cached", "--quiet")
    head_proc = git("rev-parse", "HEAD")
    remote_proc = git("ls-remote", "--end-of-options", "origin", "refs/heads/main")
    text = status_proc.stdout
    status_lines = [line for line in text.splitlines() if not line.startswith("##")]
    detached = text.lstrip().startswith("## HEAD (no branch)")
    branch_ok = f"## {branch}..." in text or detached
    local_clean = (
        status_proc.returncode == 0
        and tracked_diff_proc.returncode == 0
        and staged_diff_proc.returncode == 0
        and not status_lines
    )
    head = head_proc.stdout.strip() if head_proc.returncode == 0 else ""
    remote_head = remote_proc.stdout.split()[0] if remote_proc.returncode == 0 and remote_proc.stdout.split() else ""
    remote_graph_aligned = bool(head and remote_head and head == remote_head)
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
        "head": head,
        "remoteHead": remote_head,
        "remote_contains_local": remote_graph_aligned,
        "local_contains_remote": remote_graph_aligned,
        "remote_graph_aligned": remote_graph_aligned,
        "detached_worktree": detached,
        "baselineBound": remote_graph_aligned,
        "semantic_ok": ok,
        "status": "green" if ok else "red",
    }


def _pages_workflow_observation(*, remote_head: str, manifest_id: str, issue_date: str) -> dict[str, Any]:
    """GitHub Actionsの最新Pages成功runをremote HEADへ束縛する。"""
    from tools.news_grasp_publish_contract import evaluate_pages_deployment

    url = "https://api.github.com/repos/HIDEPON-UMG/News-Grasp/actions/workflows/deploy-pages.yml/runs?branch=main&event=push&per_page=20"
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "News-Grasp-public-verifier"},
    )
    try:
        with _open_github_actions_no_redirect(request, timeout=20) as response:
            raw = response.read(1_000_001)
            if len(raw) > 1_000_000:
                raise ValueError("pages_workflow_response_too_large")
            value = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError, UnicodeError, json.JSONDecodeError) as exc:
        return {"ok": False, "status": "blocked", "reasonCodes": ["pages_workflow_fetch_failed"], "detail": str(exc), "semantic_ok": False}
    rows = value.get("workflow_runs") if isinstance(value, dict) else []
    pages_rows = [row for row in rows or [] if isinstance(row, dict) and str(row.get("path") or "") == ".github/workflows/deploy-pages.yml"]
    result = evaluate_pages_deployment(
        remote_head=remote_head,
        workflow_runs=pages_rows,
        manifest_id=manifest_id,
        issue_date=issue_date,
    )
    return {**result, "semantic_ok": result.get("ok") is True, "apiUrl": url}


def verify_direct_public_completion(
    *,
    repo_root: Path,
    issue_date: str,
    public_base_url: str,
    remote: str = "origin",
    branch: str = "main",
    wait_sec: int = 0,
    poll_sec: int = 30,
    run_id: str = "",
    run_intent: str = "scheduled_production_direct",
    manifest_id: str = "",
    cache_bust: bool = True,
) -> dict[str, Any]:
    _validate_transport_policy(remote=remote, branch=branch, wait_sec=wait_sec, poll_sec=poll_sec)
    public_base_url = validate_public_base_url(public_base_url)
    repo = resolve_trusted_repo_root(repo_root)
    manifest: dict[str, Any] | None = None
    manifest_failures: list[str] = []
    if "NEWS_GRASP_SKIP_URL_CHECK" in os.environ:
        manifest_failures.append("production_url_skip_override_forbidden")
    try:
        from tools.news_grasp_publish_contract import load_manifest, verify_manifest

        manifest = load_manifest(repo, issue_date)
        manifest_validation = verify_manifest(manifest, repo_root=repo, require_files=True)
        manifest_failures.extend(manifest_validation.get("reasonCodes") or [])
        if manifest_id and manifest.get("manifestId") != manifest_id:
            manifest_failures.append("runtime_manifest_id_mismatch")
        if run_id and manifest.get("runId") != run_id:
            manifest_failures.append("runtime_run_id_mismatch")
        if manifest.get("runIntent") != run_intent:
            manifest_failures.append("runtime_run_intent_mismatch")
        run_id = run_id or str(manifest.get("runId") or "")
        manifest_id = manifest_id or str(manifest.get("manifestId") or "")
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        manifest_failures.append(f"manifest_load_red:{exc}")
    surfaces: dict[str, dict[str, Any]] = {}
    surfaces["web"] = _required_docs(repo, issue_date, manifest=manifest)
    if manifest_failures:
        surfaces["web"] = {
            **surfaces["web"],
            "ok": False,
            "semantic_ok": False,
            "status": "blocked",
            "manifest_failures": manifest_failures,
        }
    surfaces["deepdive_article"] = _deepdive_quality(repo, issue_date)
    daily_quality = _daily_quality(repo, issue_date)
    surfaces["daily_audio"] = _audio_projection(repo, issue_date, audio_type="daily", run_id=run_id, run_intent=run_intent)
    surfaces["daily_audio"]["qualityGate"] = daily_quality
    if daily_quality.get("ok") is not True:
        surfaces["daily_audio"]["ok"] = False
        surfaces["daily_audio"]["semantic_ok"] = False
        surfaces["daily_audio"]["status"] = "blocked"
    surfaces["deepdive_audio"] = _deepdive_audio(repo, issue_date, run_id=run_id, run_intent=run_intent)
    surfaces["distribution"] = _required_distribution(repo, issue_date, manifest=manifest, run_id=run_id, run_intent=run_intent)
    surfaces["publish_status"] = _publish_status(repo, issue_date)
    surfaces.update(_podcast_rows(repo, issue_date, wait_sec=wait_sec, poll_sec=poll_sec, run_id=run_id, run_intent=run_intent))
    surfaces["notification"] = _notification(repo, issue_date, run_id=run_id, run_intent=run_intent)
    public_web = _public_web(
        repo,
        issue_date,
        public_base_url=public_base_url,
        remote=remote,
        branch=branch,
        wait_sec=wait_sec,
        poll_sec=poll_sec,
        manifest=manifest,
        cache_bust=cache_bust,
    )
    remote_observation = _up_to_date_observation(repo, remote, branch)
    source_baseline = str((manifest or {}).get("sourceBaseline") or "")
    baseline_check = subprocess.run(
        ["git", "merge-base", "--is-ancestor", source_baseline, str(remote_observation.get("remoteHead") or "")],
        cwd=str(repo), capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30, check=False, shell=False, creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
    ) if re.fullmatch(r"[0-9a-f]{40}", source_baseline) and remote_observation.get("remoteHead") else None
    if baseline_check is None or baseline_check.returncode != 0:
        remote_observation = {**remote_observation, "ok": False, "semantic_ok": False, "status": "blocked", "sourceBaseline": source_baseline, "reason": "manifest_source_baseline_not_remote_ancestor"}
    else:
        remote_observation["sourceBaseline"] = source_baseline
        remote_observation["sourceBaselineAncestry"] = "verified"
    workflow = _pages_workflow_observation(
        remote_head=str(remote_observation.get("remoteHead") or ""),
        manifest_id=manifest_id,
        issue_date=issue_date,
    )
    pages_ok = public_web.get("ok") is True and workflow.get("ok") is True
    surfaces["pages"] = {
        "ok": pages_ok,
        "issue_date": issue_date,
        "public": public_web,
        "workflow": workflow,
        "semantic_ok": pages_ok,
        "status": "verified" if pages_ok else "blocked",
    }
    surfaces["remote_commit"] = {
        **remote_observation,
        "issue_date": issue_date,
    }
    failures: list[str] = []
    post_publish_issues: list[dict[str, Any]] = []
    for name in PUBLIC_SURFACES:
        row = surfaces.get(name)
        if not isinstance(row, dict) or row.get("ok") is not True or row.get("semantic_ok") is not True:
            failures.append(f"public_surface_red:{name}")
        if isinstance(row, dict):
            for issue in row.get("post_publish_issue_list") or []:
                if isinstance(issue, dict) and issue not in post_publish_issues:
                    post_publish_issues.append(dict(issue))
    return {
        "schemaVersion": PUBLIC_SCHEMA_V2 if manifest is not None else PUBLIC_SCHEMA,
        "ok": not failures,
        "completion_mode": "direct_public_v2" if manifest is not None else "direct_public_v1",
        "issue_date": issue_date,
        "runId": run_id,
        "runIntent": run_intent,
        "manifestId": manifest_id,
        "status": "verified" if not failures else "blocked",
        "public_surfaces": surfaces,
        "failures": failures,
        "post_publish_issue_list": post_publish_issues,
    }
