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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
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
    "completion_attestation",
)

CANONICAL_EXTERNAL_OPERATION_ORDER = (
    "audio_daily_upload",
    "audio_deepdive_upload",
    "youtube_daily_prepare",
    "youtube_deepdive_prepare",
    "git_release_push",
    "pages_deployment_wait",
    "youtube_daily_finalize",
    "youtube_deepdive_finalize",
    "notification_send",
    "completion_attestation_publish",
)

OBSERVATION_SCHEMA = "NEWS_GRASP_DIRECT_PUBLIC_OBSERVATION_V1"
PODCAST_NETWORK_VERIFICATIONS = frozenset(
    {"oembed_watch_playlist", "watch_playlist_fallback"}
)


def _podcast_network_observed(value: Mapping[str, Any] | object) -> bool:
    """verify_podcastが実際の公開probeを完了した場合だけtrueにする。"""

    return isinstance(value, Mapping) and str(value.get("verification") or "") in PODCAST_NETWORK_VERIFICATIONS


def _canonical_observation_sha256(value: object) -> str:
    """公開観測の結合用に決定的なSHA-256を返す。"""

    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _observation_now() -> tuple[datetime, str]:
    observed = datetime.now(timezone.utc)
    return observed, observed.isoformat()


def _new_observation_context(*, issue_date: str, run_id: str, run_intent: str) -> dict[str, Any]:
    """caller入力に依存しない公開検証コンテキストを開始する。"""

    started, started_iso = _observation_now()
    nonce = secrets.token_hex(32)
    token = secrets.token_urlsafe(32)
    external_operation_id = f"public-verification-{secrets.token_hex(16)}"
    return {
        "schemaVersion": OBSERVATION_SCHEMA,
        "nonce": nonce,
        "token": token,
        "started": started,
        "startedAt": started_iso,
        "issueDate": issue_date,
        "runId": run_id,
        "runIntent": run_intent,
        "manifestId": "",
        "externalOperationId": external_operation_id,
    }


def _bind_observation_context(
    context: dict[str, Any],
    *,
    issue_date: str,
    run_id: str,
    run_intent: str,
    manifest_id: str,
) -> None:
    context.update(
        {
            "issueDate": issue_date,
            "runId": run_id,
            "runIntent": run_intent,
            "manifestId": manifest_id,
        }
    )
    binding = {
        "schemaVersion": OBSERVATION_SCHEMA,
        "nonce": context["nonce"],
        "token": context["token"],
        "issueDate": issue_date,
        "runId": run_id,
        "runIntent": run_intent,
        "manifestId": manifest_id,
        "externalOperationId": context["externalOperationId"],
    }
    context["bindingSha256"] = _canonical_observation_sha256(binding)


def _observation_metadata(
    context: Mapping[str, Any],
    *,
    request_started_at: str | None = None,
    response_observed_at: str | None = None,
    body: object = None,
    content: object = None,
    status_code: int | None = None,
    observation_kind: str = "local_canonical_read",
    source_identity: str = "",
    source_path: str = "",
) -> dict[str, Any]:
    """一つのlocal/network observationへ不変のidentityとcontent hashを付与する。"""

    _started, fallback_response = _observation_now()
    request_time = request_started_at or str(context.get("startedAt") or "")
    response_time = response_observed_at or fallback_response
    if body is None:
        body_sha = _canonical_observation_sha256(content if content is not None else {})
    elif isinstance(body, bytes):
        body_sha = hashlib.sha256(body).hexdigest()
    else:
        body_sha = hashlib.sha256(str(body).encode("utf-8", errors="replace")).hexdigest()
    content_sha = _canonical_observation_sha256(content if content is not None else body)
    normalized_kind = str(observation_kind or "").strip() or "local_canonical_read"
    if normalized_kind not in {"local_canonical_read", "network_fetch"}:
        normalized_kind = "local_canonical_read"
    payload = {
        "schemaVersion": OBSERVATION_SCHEMA,
        "observationKind": normalized_kind,
        "nonce": str(context.get("nonce") or ""),
        "token": str(context.get("token") or ""),
        "requestStartedAt": request_time,
        "responseObservedAt": response_time,
        "bodySha256": body_sha,
        "contentSha256": content_sha,
        "sourceIdentity": str(source_identity or ""),
        "sourcePath": str(source_path or ""),
        "issueDate": str(context.get("issueDate") or ""),
        "manifestId": str(context.get("manifestId") or ""),
        "runId": str(context.get("runId") or ""),
        "runIntent": str(context.get("runIntent") or ""),
        "externalOperationId": str(context.get("externalOperationId") or ""),
        "statusCode": status_code,
    }
    payload["bindingSha256"] = str(context.get("bindingSha256") or "")
    payload["freshNetwork"] = normalized_kind == "network_fetch" and bool(request_time and response_time)
    payload["observationSha256"] = _canonical_observation_sha256(payload)
    return payload


def _observation_content(value: object) -> object:
    """観測自身を除いたsurface値をhash対象にする。"""

    if isinstance(value, Mapping):
        return {
            str(key): _observation_content(item)
            for key, item in value.items()
            if str(key) not in {"observation", "networkObservations"}
        }
    if isinstance(value, list):
        return [_observation_content(item) for item in value]
    return value


def _attach_surface_observation(
    name: str,
    value: object,
    context: Mapping[str, Any],
    *,
    request_started_at: str | None = None,
    response_observed_at: str | None = None,
    observation_kind: str = "local_canonical_read",
    source_identity: str = "",
    source_path: str = "",
) -> object:
    """互換consumerを含む全surfaceへverifier-owned observationを束ねる。"""

    if not isinstance(value, dict):
        value = {"value": value}
    observed = dict(value)
    prior_observation = observed.get("observation")
    if (
        isinstance(prior_observation, Mapping)
        and prior_observation.get("schemaVersion") == OBSERVATION_SCHEMA
    ):
        prior_network = observed.get("networkObservations")
        if isinstance(prior_network, Mapping):
            prior_network = dict(prior_network)
            prior_network.setdefault("surface", dict(prior_observation))
        elif isinstance(prior_network, list):
            prior_network = [*prior_network, dict(prior_observation)]
        else:
            prior_network = [dict(prior_observation)]
        observed["networkObservations"] = prior_network
    body = _observation_content(observed)
    local_source_path = str(
        source_path
        or observed.get("path")
        or (observed.get("state") if isinstance(observed.get("state"), str) else "")
        or f"surface/{name}"
    )
    observed["observation"] = _observation_metadata(
        context,
        request_started_at=request_started_at,
        response_observed_at=response_observed_at,
        content={"surface": name, "value": body},
        observation_kind=observation_kind,
        source_identity=(
            source_identity
            or f"{observation_kind}:{name}:{local_source_path}"
        ),
        source_path=local_source_path,
    )
    return observed


def _observation_is_fresh(observation: Mapping[str, Any], *, started: datetime) -> bool:
    """network observationの時系列と現在時刻を検査する。"""

    try:
        request_at = datetime.fromisoformat(str(observation.get("requestStartedAt") or "").replace("Z", "+00:00"))
        response_at = datetime.fromisoformat(str(observation.get("responseObservedAt") or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if request_at.tzinfo is None or response_at.tzinfo is None:
        return False
    now = datetime.now(timezone.utc)
    request_utc = request_at.astimezone(timezone.utc)
    response_utc = response_at.astimezone(timezone.utc)
    started_utc = started.astimezone(timezone.utc)
    return (
        request_utc >= started_utc
        and response_utc >= request_utc
        and response_utc <= now
        and (now - response_utc).total_seconds() <= 30 * 60
    )


def _collect_observation_rows(
    value: object,
    *,
    path: tuple[str, ...] = (),
) -> list[tuple[tuple[str, ...], dict[str, Any]]]:
    rows: list[tuple[tuple[str, ...], dict[str, Any]]] = []
    if isinstance(value, Mapping):
        if value.get("schemaVersion") == OBSERVATION_SCHEMA:
            rows.append((path, dict(value)))
        for key, item in value.items():
            if str(key) == "nonce":
                continue
            rows.extend(_collect_observation_rows(item, path=(*path, str(key))))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            rows.extend(_collect_observation_rows(item, path=(*path, str(index))))
    return rows


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
    expected_query = "branch=main&per_page=20"
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


class _GithubReleaseRedirect(urllib.request.HTTPRedirectHandler):
    """completion attestationのredirect先をGitHub asset hostへ限定する。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        parsed = urlsplit(newurl)
        host = (parsed.hostname or "").casefold()
        if (
            parsed.scheme.casefold() != "https"
            or parsed.port is not None
            or parsed.username is not None
            or parsed.password is not None
            or not (
                host == "github.com"
                or host == "objects.githubusercontent.com"
                or host == "release-assets.githubusercontent.com"
                or host.endswith(".githubusercontent.com")
            )
        ):
            raise ValueError("completion_attestation_redirect_forbidden")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_completion_attestation(request: urllib.request.Request, *, timeout: int):
    parsed = urlsplit(request.full_url)
    if (
        parsed.scheme.casefold() != "https"
        or (parsed.hostname or "").casefold() != "github.com"
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
        or not re.fullmatch(
            r"/HIDEPON-UMG/News-Grasp/releases/download/audio-daily/"
            r"\d{4}-\d{2}-\d{2}-[0-9A-Za-z._-]+-"
            r"(?:completion|distribution|distribution-binding|playlist-binding|notification-ledger|"
            r"daily-audio-projection|deepdive-audio-projection|youtube-daily-state|youtube-deepdive-state)\.json",
            parsed.path,
        )
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("completion_attestation_url_invalid")
    return urllib.request.build_opener(_GithubReleaseRedirect()).open(request, timeout=timeout)


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
    from tools.publish_inventory import docs_artifact_for_category, required_published_docs_artifacts

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
        for category_id in manifest.get("scheduledCategoryIds") or []:
            page_paths[f"category:{category_id}"] = Path(docs_artifact_for_category(str(category_id), issue_date))
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
    manifest_id: str = "",
    bundle_id: str = "",
    release_commit_sha: str = "",
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
        or (manifest_id and binding.get("manifestId") != manifest_id)
        or (bundle_id and binding.get("bundleId") != bundle_id)
        or (release_commit_sha and binding.get("releaseCommitSha") != release_commit_sha)
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
        or (manifest_id and distribution_binding.get("manifestId") != manifest_id)
        or (bundle_id and distribution_binding.get("bundleId") != bundle_id)
        or (release_commit_sha and distribution_binding.get("releaseCommitSha") != release_commit_sha)
        or distribution_binding.get("status") != "verified"
        or distribution_binding.get("receiptSha256") != distribution_binding_sha
        or distribution_binding.get("playlistReceiptSha256") != binding.get("receiptSha256")
        or any(distribution_binding.get(field) != identity for field, identity in live_identities.items())
    ):
        errors.append("distribution_run_binding_invalid")
    release_identity_required = bool(manifest_id or bundle_id or release_commit_sha)
    if release_identity_required and (
        value.get("manifest_id") != manifest_id
        or value.get("bundle_id") != bundle_id
        or value.get("publish_commit") != release_commit_sha
        or value.get("run_id") != run_id
        or value.get("run_intent") != run_intent
    ):
        errors.append("distribution_release_identity_invalid")
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


def _publish_status(
    repo_root: Path,
    issue_date: str,
    *,
    run_id: str = "",
    run_intent: str = "",
) -> dict[str, Any]:
    state = _load_json(_safe_repo_path(repo_root, Path("docs") / "publish-status.json"))
    value = state.get("value") if isinstance(state.get("value"), dict) else {}
    if state.get("ok"):
        ok = (
            value.get("date") == issue_date
            and value.get("result") == "publication_pending"
            and value.get("status") == "awaiting_external_completion_attestation"
            and value.get("completionAuthority") == "consumer-owned_public_verifier"
            and (not run_id or value.get("runId") == run_id)
            and (not run_intent or value.get("runIntent") == run_intent)
        )
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
            "completionAuthority": value.get("completionAuthority") if isinstance(value, dict) else None,
            "runId": value.get("runId") if isinstance(value, dict) else None,
            "runIntent": value.get("runIntent") if isinstance(value, dict) else None,
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
            include_corpus=False,
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
    expected_sha256: str = "",
    observation_context: Mapping[str, Any] | None = None,
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
    probe_started = datetime.now(timezone.utc).isoformat()
    public_observation = (
        _probe_public_audio(
            str(projection.get("publicUrl") or ""),
            expected_sha256=expected_sha256,
        )
        if validation["ok"] is True
        else {"ok": False, "reasonCode": "audio_projection_red"}
    )
    probe_finished = datetime.now(timezone.utc).isoformat()
    if observation_context is not None:
        public_observation = dict(public_observation)
        public_observation["observation"] = _observation_metadata(
            observation_context,
            request_started_at=probe_started,
            response_observed_at=probe_finished,
            content={"audioType": audio_type, "publicUrl": projection.get("publicUrl"), "result": public_observation},
            observation_kind="network_fetch" if validation["ok"] is True else "local_canonical_read",
            source_identity=str(projection.get("publicUrl") or f"audio:{audio_type}"),
            source_path=str(projection.get("publicUrl") or f"audio:{audio_type}"),
        )
    ok = validation["ok"] is True and projection.get("runId") == run_id and public_observation.get("ok") is True
    result = {
        "ok": ok,
        "issue_date": issue_date,
        "state": projection,
        "path": str(source),
        "reasonCodes": validation["reasonCodes"] + ([] if projection.get("runId") == run_id else ["audio_run_id_mismatch"]) + ([] if public_observation.get("ok") is True else [str(public_observation.get("reasonCode") or "audio_public_asset_unverified")]),
        "publicObservation": public_observation,
        "semantic_ok": ok,
        "status": "verified" if ok else "blocked",
    }
    if observation_context is not None:
        result["observation"] = _observation_metadata(
            observation_context,
            request_started_at=probe_started,
            response_observed_at=probe_finished,
            content={"surface": f"{audio_type}_audio", "projection": projection, "public": public_observation},
            observation_kind="network_fetch" if validation["ok"] is True else "local_canonical_read",
            source_identity=str(projection.get("publicUrl") or f"audio:{audio_type}"),
            source_path=str(projection.get("publicUrl") or f"audio:{audio_type}"),
        )
    return result


def _deepdive_audio(
    repo_root: Path,
    issue_date: str,
    *,
    run_id: str = "",
    run_intent: str = "scheduled_production_direct",
    expected_sha256: str = "",
    observation_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return _audio_projection(
        repo_root,
        issue_date,
        audio_type="deepdive",
        run_id=run_id,
        run_intent=run_intent,
        expected_sha256=expected_sha256,
        observation_context=observation_context,
    )


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
    observation_context: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    from tools.daily_self_heal import verify_podcast
    from tools.youtube_podcast.upload_episode import audit_playlist_uniqueness

    daily_request_started = datetime.now(timezone.utc).isoformat()
    daily = verify_podcast(
        date=issue_date,
        state_path=_safe_repo_path(repo_root, Path("build") / "youtube-podcast" / "uploads.json"),
        wait_sec=wait_sec,
        poll_sec=poll_sec,
        expected_title=f"News-Grasp Daily News Briefing {issue_date}",
    )
    daily_response_observed = datetime.now(timezone.utc).isoformat()
    deepdive_request_started = datetime.now(timezone.utc).isoformat()
    deepdive = verify_podcast(
        date=issue_date,
        state_path=_safe_repo_path(repo_root, Path("build") / "youtube-podcast-deepdive" / "uploads.json"),
        wait_sec=wait_sec,
        poll_sec=poll_sec,
        expected_title=f"News-Grasp DeepDive Dialogue {issue_date}",
    )
    deepdive_response_observed = datetime.now(timezone.utc).isoformat()
    uniqueness: dict[str, Any] = {"ok": True, "status": "not_requested"}
    if observation_context is not None:
        uniqueness_started = datetime.now(timezone.utc).isoformat()
        try:
            uniqueness = dict(audit_playlist_uniqueness(issue_date))
        except Exception as exc:  # noqa: BLE001 - provider read failure is typed Red
            uniqueness = {
                "ok": False,
                "status": "provider_read_failed",
                "reason": type(exc).__name__,
            }
        uniqueness_finished = datetime.now(timezone.utc).isoformat()
        uniqueness["observation"] = _observation_metadata(
            observation_context,
            request_started_at=uniqueness_started,
            response_observed_at=uniqueness_finished,
            content={"surface": "youtube_playlist_uniqueness", "result": uniqueness},
            observation_kind="network_fetch",
            source_identity="youtube:playlist:uniqueness",
            source_path="youtube:playlist:uniqueness",
        )
    if observation_context is not None:
        daily = dict(daily)
        daily["observation"] = _observation_metadata(
            observation_context,
            request_started_at=daily_request_started,
            response_observed_at=daily_response_observed,
            content={"surface": "youtube_daily", "result": daily},
            observation_kind="network_fetch" if _podcast_network_observed(daily) else "local_canonical_read",
            source_identity=f"youtube:video:{daily.get('videoId') or 'unknown'}",
            source_path=f"youtube:video:{daily.get('videoId') or 'unknown'}",
        )
        deepdive = dict(deepdive)
        deepdive["observation"] = _observation_metadata(
            observation_context,
            request_started_at=deepdive_request_started,
            response_observed_at=deepdive_response_observed,
            content={"surface": "youtube_deepdive", "result": deepdive},
            observation_kind="network_fetch" if _podcast_network_observed(deepdive) else "local_canonical_read",
            source_identity=f"youtube:video:{deepdive.get('videoId') or 'unknown'}",
            source_path=f"youtube:video:{deepdive.get('videoId') or 'unknown'}",
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
    uniqueness_ok = uniqueness.get("ok") is True
    daily_ok = daily.get("ok") is True and binding_ok and uniqueness_ok and daily.get("videoId") == binding.get("daily", {}).get("videoId") and daily.get("playlistId") == binding.get("daily", {}).get("playlistId")
    deepdive_ok = deepdive.get("ok") is True and binding_ok and uniqueness_ok and deepdive.get("videoId") == binding.get("deepdive", {}).get("videoId") and deepdive.get("playlistId") == binding.get("deepdive", {}).get("playlistId")
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
    daily_projection["externalOperations"] = _side_effect_records(
        uploads.get("daily"), surface="youtube_daily"
    )
    deepdive_projection["externalOperations"] = _side_effect_records(
        uploads.get("deepdive"), surface="youtube_deepdive"
    )
    result = {
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
            "result": {"daily": daily_projection, "deepdive": deepdive_projection, "uniqueness": uniqueness},
            "semantic_ok": playlist_ok,
            "status": "green" if playlist_ok else "red",
        },
    }
    if observation_context is not None:
        for surface_name in ("youtube_daily", "youtube_deepdive", "playlist"):
            row = result.get(surface_name)
            if isinstance(row, dict):
                result[surface_name] = _attach_surface_observation(
                    surface_name,
                    row,
                    observation_context,
                    request_started_at=(daily_request_started if surface_name == "youtube_daily" else deepdive_request_started),
                    response_observed_at=(daily_response_observed if surface_name == "youtube_daily" else deepdive_response_observed),
                    observation_kind=(
                        "network_fetch"
                        if (
                            (surface_name == "youtube_daily" and _podcast_network_observed(daily))
                            or (surface_name == "youtube_deepdive" and _podcast_network_observed(deepdive))
                            or (surface_name == "playlist" and (_podcast_network_observed(daily) or _podcast_network_observed(deepdive)))
                        )
                        else "local_canonical_read"
                    ),
                    source_identity=(
                        f"youtube:playlist:{(daily.get('playlistId') or deepdive.get('playlistId') or 'unknown')}"
                        if surface_name == "playlist"
                        else f"youtube:video:{(daily.get('videoId') if surface_name == 'youtube_daily' else deepdive.get('videoId')) or 'unknown'}"
                    ),
                    source_path=(
                        f"youtube:playlist:{(daily.get('playlistId') or deepdive.get('playlistId') or 'unknown')}"
                        if surface_name == "playlist"
                        else f"youtube:video:{(daily.get('videoId') if surface_name == 'youtube_daily' else deepdive.get('videoId')) or 'unknown'}"
                    ),
                )
    return result


def _notification(
    repo_root: Path,
    issue_date: str,
    *,
    run_id: str = "",
    run_intent: str = "scheduled_production_direct",
    observation_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    observation_started = datetime.now(timezone.utc).isoformat()
    candidates = [
        _safe_repo_path(repo_root, Path("build") / "push" / f"{issue_date}.json"),
        _safe_repo_path(repo_root, Path("build") / "notification" / f"{issue_date}.json"),
        _safe_repo_path(repo_root, Path("build") / "notifications" / f"{issue_date}.json"),
    ]
    observed = [_load_json(path) for path in candidates if _is_regular_file_no_reparse(path)]
    ok = False
    v2_failures: list[str] = []
    external_operations: list[dict[str, Any]] = []
    validated_external_operations: list[dict[str, Any]] = []
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
            receipt_failure_start = len(v2_failures)
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
                or not re.fullmatch(r"[0-9a-f]{64}", str(adapter.get("producerSha256") or ""))
                or not re.fullmatch(r"[0-9a-f]{32}", str(adapter.get("producerRunId") or ""))
            ):
                v2_failures.append("notification_trusted_sender_adapter_invalid")
            expected_ledger_id = f"{issue_date}.delivery.json"
            expected_v2_id = (
                f"{issue_date}.already-sent-verifications.jsonl"
                if receipt.get("status") == "already_sent"
                else f"{issue_date}.delivery-v2.json"
            )
            if receipt.get("status") == "already_sent" and adapter.get("priorDeliveryReceiptPath") != expected_ledger_id:
                v2_failures.append("notification_prior_delivery_path_id_invalid")
            v2_path_id = value.get("deliveryReceiptV2Path")
            if v2_path_id != expected_v2_id:
                v2_failures.append("notification_v2_path_id_invalid")
            else:
                state_path = Path(str(row.get("path") or ""))
                try:
                    evidence_path = _safe_existing_file(repo_root, state_path.parent / expected_v2_id)
                    if evidence_path is None:
                        v2_failures.append("notification_v2_evidence_missing")
                    elif receipt.get("status") == "already_sent":
                        raw = _read_bytes_no_follow(evidence_path, limit=512_000).decode("utf-8")
                        rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
                        if receipt not in rows:
                            v2_failures.append("notification_v2_evidence_mismatch")
                    elif _load_json(evidence_path).get("value") != receipt:
                        v2_failures.append("notification_v2_evidence_mismatch")
                except (ValueError, UnicodeError, json.JSONDecodeError):
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
                sender_event_id = str(receipt.get("senderEventId") or "")
                payload_identity = str(receipt.get("payloadIdentity") or "")
                if (
                    len(v2_failures) == receipt_failure_start
                    and receipt.get("status") in {"sent", "already_sent"}
                    and sender_event_id
                    and payload_identity
                    and ledger_path is not None
                    and bool(trusted_sender_sha)
                    and isinstance(ledger, dict)
                    and str(ledger.get("producerRunId") or "") == sender_event_id
                    and str(ledger.get("payloadSha256") or "") == payload_identity
                ):
                    validated_external_operations.append(
                        {
                            "surface": "notification",
                            "operationId": sender_event_id,
                            "payloadIdentity": payload_identity,
                            "status": str(receipt.get("status") or "").casefold(),
                            "ledgerBound": True,
                            "path": str(row.get("path") or ""),
                        }
                    )
        else:
            v2_failures.append("notification_v2_receipt_missing")
    external_operations = list(validated_external_operations)
    for row in observed:
        value = row.get("value") if isinstance(row.get("value"), dict) else {}
        external_operations.extend(_side_effect_records(value, surface="notification"))
    external_operations = list(
        {
            (
                str(item.get("operationId") or ""),
                str(item.get("payloadIdentity") or ""),
                str(item.get("path") or ""),
            ): item
            for item in external_operations
        }.values()
    )
    ok = ok and not v2_failures
    warning = {
        "surface": "notification",
        "reasonCode": "notification_provider_delivery_ack_unavailable",
        "status": "unknown_unobtainable",
        "evidenceRef": "immutable_preexisting_sender_ledger_git_blob_bound",
    }
    result = {
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
        "providerAck": {
            "status": "unknown_unobtainable",
            "reasonCode": "notification_provider_delivery_ack_unavailable",
        },
        "externalOperations": external_operations,
        "post_publish_issue_list": [warning] if ok else [],
    }
    if observation_context is not None:
        result["observation"] = _observation_metadata(
            observation_context,
            request_started_at=observation_started,
            response_observed_at=datetime.now(timezone.utc).isoformat(),
            content={"surface": "notification", "observed": result["observed"], "failures": result["failures"]},
            observation_kind="local_canonical_read",
            source_identity="local_notification_ledger",
            source_path=f"build/notification/{issue_date}.json",
        )
    return result


_OPERATION_ID_KEYS = {
    "operationid",
    "operation_id",
    "externaloperationid",
    "external_operation_id",
    "uploadid",
    "upload_id",
    "uploadoperationid",
    "upload_operation_id",
    "sendid",
    "send_id",
    "sendoperationid",
    "send_operation_id",
    "eventid",
    "event_id",
    "sendereventid",
    "sender_event_id",
    "produceroperationid",
    "producer_operation_id",
}
_OPERATION_ID_LIST_KEYS = {
    "operationids",
    "operation_ids",
    "externaloperationids",
    "external_operation_ids",
    "uploadoperationids",
    "upload_operation_ids",
    "sendoperationids",
    "send_operation_ids",
}
_PAYLOAD_ID_KEYS = {
    "payloadsha256",
    "payload_sha256",
    "payloadidentity",
    "payload_identity",
    "contentsha256",
    "content_sha256",
}
_PAYLOAD_ID_LIST_KEYS = {
    "payloadidentities",
    "payload_identities",
    "payloadsha256s",
    "payload_sha256s",
}
_LEDGER_MARKERS = {
    "ledger",
    "evidenceledger",
    "deliveryreceipt",
    "deliveryreceiptv2",
    "immutableledger",
    "immutable_sender_ledger",
    "uploadledger",
    "sendledger",
}
_SUCCESS_SIDE_EFFECT_STATUSES = {
    "already_sent",
    "completed",
    "green",
    "ok",
    "public",
    "sent",
    "success",
    "succeeded",
    "uploaded",
    "verified",
}


def _side_effect_records(
    value: object,
    *,
    surface: str,
    _path: tuple[str, ...] = (),
    _ledger_bound: bool = False,
) -> list[dict[str, Any]]:
    """送信/upload ledgerの明示identityだけを抽出する。"""

    records: list[dict[str, Any]] = []
    if isinstance(value, Mapping):
        local_ledger_bound = _ledger_bound or any(
            str(key).casefold().replace("-", "_") in _LEDGER_MARKERS
            or "ledger" in str(key).casefold()
            for key in _path[-1:]
        )
        operation_id = ""
        operation_id_candidates: list[str] = []
        payload_identity = ""
        payload_identity_candidates: list[str] = []
        status = ""
        record_ledger_bound = local_ledger_bound
        for key, item in value.items():
            folded = str(key).casefold().replace("-", "_")
            if folded in _OPERATION_ID_KEYS and isinstance(item, str) and item.strip():
                operation_id = item.strip()
                operation_id_candidates.append(operation_id)
            if folded in _OPERATION_ID_LIST_KEYS and isinstance(item, list):
                operation_id_candidates.extend(
                    str(candidate).strip()
                    for candidate in item
                    if isinstance(candidate, str) and candidate.strip()
                )
            if (
                folded == "id"
                and isinstance(item, str)
                and item.strip()
                and any(
                    marker in token.casefold()
                    for token in _path
                    for marker in ("operation", "upload", "send", "external", "side_effect")
                )
            ):
                operation_id = item.strip()
            if folded in _PAYLOAD_ID_KEYS and isinstance(item, str) and item.strip():
                payload_identity = item.strip()
                payload_identity_candidates.append(payload_identity)
            if folded in _PAYLOAD_ID_LIST_KEYS and isinstance(item, list):
                payload_identity_candidates.extend(
                    str(candidate).strip()
                    for candidate in item
                    if isinstance(candidate, str) and candidate.strip()
                )
            if folded in {"status", "state", "conclusion", "result"} and isinstance(item, str):
                status = item.strip().casefold()
            if folded == "ok" and isinstance(item, bool):
                status = "verified" if item else "failed"
            if folded in {
                "ledger_bound",
                "ledgerbound",
                "immutable",
                "sealed",
                "sealed_ledger",
                "immutable_ledger_bound",
            } and item is True:
                record_ledger_bound = True
            if "ledger" in folded and isinstance(item, (Mapping, list)):
                record_ledger_bound = True
        if operation_id_candidates:
            for index, candidate in enumerate(dict.fromkeys(operation_id_candidates)):
                candidate_payload = payload_identity
                if index < len(payload_identity_candidates):
                    candidate_payload = payload_identity_candidates[index]
                records.append(
                    {
                        "surface": surface,
                        "operationId": candidate,
                        "payloadIdentity": candidate_payload,
                        "status": status or "verified",
                        "ledgerBound": record_ledger_bound,
                        "path": "/".join((*_path, candidate)),
                    }
                )
        for key, item in value.items():
            # verifier-created observation metadata is not an external side-effect ledger.
            if str(key) in {
                "observation",
                "networkObservations",
                # append-only historyは監査用であり、active releaseの重複
                # 判定へ混ぜない。active projectionだけをexpected outboxと照合する。
                "externalOutboxBindingHistoryV2",
                "activeExternalOutboxBindingKeys",
            }:
                continue
            next_path = (*_path, str(key))
            child_ledger = local_ledger_bound or "ledger" in str(key).casefold()
            records.extend(
                _side_effect_records(
                    item,
                    surface=surface,
                    _path=next_path,
                    _ledger_bound=child_ledger,
                )
            )
    elif isinstance(value, list):
        for index, item in enumerate(value):
            records.extend(
                _side_effect_records(
                    item,
                    surface=surface,
                    _path=(*_path, str(index)),
                    _ledger_bound=_ledger_bound,
                )
            )
    return records


def _validate_side_effect_identity(
    surfaces: Mapping[str, object],
    *,
    expected_outbox: Mapping[str, object] | Sequence[Mapping[str, object]] | None = None,
) -> dict[str, Any]:
    """provider ledgerをruntime outboxのexact operation/payload集合へ束縛する。"""

    failures: set[str] = set()
    by_surface: dict[str, list[dict[str, Any]]] = {}
    expected_by_surface: dict[str, set[tuple[str, str]]] = {}
    all_expected_ids: set[str] = set()
    if isinstance(expected_outbox, Mapping):
        for surface, rows in expected_outbox.items():
            if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
                failures.add("external_outbox_binding_invalid")
                continue
            pairs = {
                (
                    str(row.get("operationId") or row.get("operation_id") or ""),
                    str(row.get("payloadIdentity") or row.get("payload_identity") or ""),
                )
                for row in rows
                if isinstance(row, Mapping)
            }
            expected_by_surface[str(surface)] = {pair for pair in pairs if all(pair)}
            all_expected_ids.update(pair[0] for pair in expected_by_surface[str(surface)])
    elif isinstance(expected_outbox, Sequence) and not isinstance(expected_outbox, (str, bytes, bytearray)):
        canonical_order = CANONICAL_EXTERNAL_OPERATION_ORDER
        rows = [row for row in expected_outbox if isinstance(row, Mapping)]
        observed_order = tuple(str(row.get("operation_id") or "") for row in rows)
        if observed_order != canonical_order:
            failures.add("external_outbox_operation_set_mismatch")
        surface_for_operation = {
            "audio_daily_upload": "daily_audio",
            "audio_deepdive_upload": "deepdive_audio",
            "youtube_daily_prepare": "youtube_daily",
            "youtube_daily_finalize": "youtube_daily",
            "youtube_deepdive_prepare": "youtube_deepdive",
            "youtube_deepdive_finalize": "youtube_deepdive",
            "notification_send": "notification",
        }
        for row in rows:
            operation_id = str(row.get("operation_id") or "")
            payload_identity = str(row.get("payload_identity") or "")
            all_expected_ids.add(operation_id)
            receipt = row.get("provider_receipt")
            if not isinstance(receipt, Mapping):
                failures.add(f"external_outbox_receipt_missing:{operation_id}")
                continue
            receipt_hash = hashlib.sha256(
                json.dumps(dict(receipt), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
            ).hexdigest()
            if (
                row.get("status") != "completed"
                or receipt_hash != row.get("provider_receipt_hash")
                or receipt.get("operation_id") != operation_id
                or receipt.get("side_effect_id") != row.get("side_effect_id")
                or receipt.get("idempotency_key") != row.get("idempotency_key")
                or receipt.get("run_id") != row.get("run_id")
                or receipt.get("output_hash") != row.get("provider_output_hash")
            ):
                failures.add(f"external_outbox_receipt_unbound:{operation_id}")
            surface = surface_for_operation.get(operation_id)
            if surface:
                expected_by_surface.setdefault(surface, set()).add((operation_id, payload_identity))
    else:
        failures.add("external_outbox_binding_missing")

    for surface in ("daily_audio", "deepdive_audio", "youtube_daily", "youtube_deepdive", "notification"):
        records = _side_effect_records(surfaces.get(surface), surface=surface)
        if records:
            by_surface[surface] = records
        surface_value = surfaces.get(surface)
        successful = [
            row for row in records
            if str(row.get("status") or "verified").casefold() in _SUCCESS_SIDE_EFFECT_STATUSES
        ]
        outbox_bound = [
            row for row in successful
            if "externalOutboxBindings" in str(row.get("path") or "")
            or str(row.get("operationId") or "") in all_expected_ids
        ]
        actual_pairs = {
            (str(row.get("operationId") or ""), str(row.get("payloadIdentity") or ""))
            for row in outbox_bound
            if str(row.get("operationId") or "") and str(row.get("payloadIdentity") or "")
        }
        expected_pairs = expected_by_surface.get(surface, set())
        if isinstance(surface_value, Mapping) and surface_value.get("ok") is True:
            if not outbox_bound:
                failures.add(f"{surface}_external_operation_ledger_missing")
            if expected_outbox is not None and actual_pairs != expected_pairs:
                failures.add(f"{surface}_external_outbox_unbound")
        if outbox_bound and any(row.get("ledgerBound") is not True for row in outbox_bound):
            failures.add("immutable_side_effect_ledger_unbound")
        legacy_identities = {
            (str(row.get("operationId") or ""), str(row.get("payloadIdentity") or ""))
            for row in successful if row not in outbox_bound
        }
        if len(legacy_identities) > 1:
            failures.add("duplicate_send_detected" if surface == "notification" else "duplicate_upload_detected")

    sealed = sorted({pair[0] for pairs in expected_by_surface.values() for pair in pairs})
    payload_identity = sorted({pair[1] for pairs in expected_by_surface.values() for pair in pairs})
    sealed_identity = sorted((surface, operation_id, payload) for surface, pairs in expected_by_surface.items() for operation_id, payload in pairs)
    return {
        "ok": not failures,
        "failures": sorted(failures),
        "bySurface": by_surface,
        "sealedOperationIds": sealed,
        "payloadIdentities": payload_identity,
        "sealedSetSha256": _canonical_observation_sha256(
            {
                "identity": sealed_identity,
                "operationIds": sealed,
                "payloadIdentities": payload_identity,
            }
        ),
    }


def _completion_attestation_surface(
    *,
    issue_date: str,
    run_id: str,
    run_intent: str,
    manifest_id: str,
    bundle_id: str,
    release_commit_sha: str,
    manifest: Mapping[str, Any] | None,
    external_outbox: Sequence[Mapping[str, object]] | None,
    observation_context: Mapping[str, Any],
) -> dict[str, Any]:
    """公開済みcompletion attestationをconsumer側からfreshにexact検証する。"""

    failures: list[str] = []
    rows = [dict(row) for row in (external_outbox or ()) if isinstance(row, Mapping)]
    observed_order = tuple(str(row.get("operation_id") or "") for row in rows)
    if observed_order != CANONICAL_EXTERNAL_OPERATION_ORDER:
        failures.append("completion_attestation_outbox_order_mismatch")
    row_by_operation = {
        str(row.get("operation_id") or ""): row
        for row in rows
        if str(row.get("operation_id") or "")
    }
    attestation_row = row_by_operation.get("completion_attestation_publish")
    receipt = (
        attestation_row.get("provider_receipt")
        if isinstance(attestation_row, Mapping)
        and isinstance(attestation_row.get("provider_receipt"), Mapping)
        else {}
    )
    evidence = (
        receipt.get("provider_evidence")
        if isinstance(receipt, Mapping)
        and isinstance(receipt.get("provider_evidence"), Mapping)
        else {}
    )
    expected_url = (
        "https://github.com/HIDEPON-UMG/News-Grasp/releases/download/audio-daily/"
        f"{issue_date}-{run_id}-completion.json"
    )
    manifest_attestation = (
        manifest.get("completionAttestation")
        if isinstance(manifest, Mapping)
        and isinstance(manifest.get("completionAttestation"), Mapping)
        else {}
    )
    if manifest_attestation:
        if (
            manifest_attestation.get("required") is not True
            or manifest_attestation.get("publicUrl") != expected_url
            or manifest_attestation.get("hashAuthority") != "external_completion_receipt"
        ):
            failures.append("completion_attestation_manifest_contract_invalid")
    else:
        failures.append("completion_attestation_manifest_contract_missing")
    url = str(evidence.get("attestation_url") or "")
    expected_sha256 = str(
        (attestation_row or {}).get("payload_identity")
        or receipt.get("payload_identity")
        or ""
    ).casefold()
    if (
        url != expected_url
        or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)
        or str(evidence.get("payload_identity") or "").casefold() != expected_sha256
    ):
        failures.append("completion_attestation_outbox_binding_invalid")

    raw = b""
    value: Mapping[str, Any] = {}
    artifact_network_observations: dict[str, dict[str, Any]] = {}
    status_code: int | None = None
    request_started = datetime.now(timezone.utc).isoformat()
    response_observed = request_started
    if not failures:
        try:
            request = urllib.request.Request(
                url,
                headers={"Cache-Control": "no-cache", "Pragma": "no-cache"},
            )
            with _open_completion_attestation(request, timeout=30) as response:
                raw = response.read(1_048_577)
                status_code = int(getattr(response, "status", response.getcode()))
                response_observed = datetime.now(timezone.utc).isoformat()
            if len(raw) > 1_048_576:
                raise ValueError("completion_attestation_body_too_large")
            loaded = json.loads(raw.decode("utf-8"))
            if not isinstance(loaded, Mapping):
                raise ValueError("completion_attestation_not_object")
            value = loaded
        except (OSError, urllib.error.URLError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            response_observed = datetime.now(timezone.utc).isoformat()
            failures.append(f"completion_attestation_fetch_red:{exc}")

    observed_sha256 = hashlib.sha256(raw).hexdigest() if raw else ""
    if raw and observed_sha256 != expected_sha256:
        failures.append("completion_attestation_public_hash_mismatch")
    if value:
        canonical_bytes = (
            json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        if raw != canonical_bytes:
            failures.append("completion_attestation_public_bytes_noncanonical")
        if (
            value.get("schemaVersion") != "NEWS_GRASP_PUBLIC_COMPLETION_ATTESTATION_V1"
            or value.get("issueDate") != issue_date
            or value.get("runId") != run_id
            or value.get("runIntent") != run_intent
            or value.get("manifestId") != manifest_id
            or value.get("bundleId") != bundle_id
            or value.get("releaseCommitSha") != release_commit_sha
            or value.get("providerDeliveryAckStatus") != "unknown_unobtainable"
        ):
            failures.append("completion_attestation_release_identity_mismatch")
        operations = value.get("operations")
        if not isinstance(operations, list):
            operations = []
            failures.append("completion_attestation_operations_invalid")
        prior_order = CANONICAL_EXTERNAL_OPERATION_ORDER[:-1]
        if tuple(str(row.get("operationId") or "") for row in operations if isinstance(row, Mapping)) != prior_order:
            failures.append("completion_attestation_operation_set_mismatch")
        for operation_id, operation in zip(prior_order, operations):
            outbox_row = row_by_operation.get(operation_id, {})
            if (
                not isinstance(operation, Mapping)
                or operation.get("operationId") != operation_id
                or operation.get("payloadIdentity") != outbox_row.get("payload_identity")
                or operation.get("providerOutputHash") != outbox_row.get("provider_output_hash")
                or operation.get("providerAckStatus") != "unknown_unobtainable"
                or re.fullmatch(r"[0-9a-f]{64}", str(operation.get("providerEvidenceSha256") or "")) is None
                or re.fullmatch(r"[0-9a-f]{64}", str(operation.get("providerBindingReceiptSha256") or "")) is None
            ):
                failures.append(f"completion_attestation_operation_binding_invalid:{operation_id}")
        distribution_artifacts = value.get("distributionArtifacts")
        expected_artifacts = {
            "distribution": f"data/distribution/{issue_date}.json",
            "distribution-binding": f"build/distribution/{issue_date}/binding.json",
            "playlist-binding": f"build/distribution/{issue_date}/playlist.json",
            "notification-ledger": f"build/notification/{issue_date}.json",
            "daily-audio-projection": "build/tts/daily/latest_audio.json",
            "deepdive-audio-projection": "build/tts/deepdive/latest_audio.json",
            "youtube-daily-state": "build/youtube-podcast/uploads.json",
            "youtube-deepdive-state": "build/youtube-podcast-deepdive/uploads.json",
        }
        public_artifact_values: dict[str, Mapping[str, Any]] = {}
        public_artifact_hashes: dict[str, str] = {}
        if not isinstance(distribution_artifacts, list):
            distribution_artifacts = []
            failures.append("completion_attestation_distribution_artifacts_invalid")
        observed_artifact_ids = {
            str(row.get("artifactId") or "")
            for row in distribution_artifacts
            if isinstance(row, Mapping)
        }
        if observed_artifact_ids != set(expected_artifacts):
            failures.append("completion_attestation_distribution_artifact_set_mismatch")
        for artifact in distribution_artifacts:
            if not isinstance(artifact, Mapping):
                continue
            artifact_id = str(artifact.get("artifactId") or "")
            artifact_url = str(artifact.get("publicUrl") or "")
            artifact_hash = str(artifact.get("sha256") or "").casefold()
            expected_artifact_url = (
                "https://github.com/HIDEPON-UMG/News-Grasp/releases/download/audio-daily/"
                f"{issue_date}-{run_id}-{artifact_id}.json"
            )
            if (
                expected_artifacts.get(artifact_id) != artifact.get("localPath")
                or artifact_url != expected_artifact_url
                or re.fullmatch(r"[0-9a-f]{64}", artifact_hash) is None
                or not isinstance(artifact.get("size"), int)
                or int(artifact.get("size") or 0) <= 0
            ):
                failures.append(f"completion_distribution_artifact_contract_invalid:{artifact_id}")
                continue
            artifact_started = datetime.now(timezone.utc).isoformat()
            artifact_observed = artifact_started
            artifact_raw = b""
            artifact_status: int | None = None
            try:
                artifact_request = urllib.request.Request(
                    artifact_url,
                    headers={"Cache-Control": "no-cache", "Pragma": "no-cache"},
                )
                with _open_completion_attestation(artifact_request, timeout=30) as response:
                    artifact_raw = response.read(4_194_305)
                    artifact_status = int(getattr(response, "status", response.getcode()))
                    artifact_observed = datetime.now(timezone.utc).isoformat()
                if len(artifact_raw) > 4_194_304:
                    raise ValueError("completion_distribution_asset_too_large")
            except (OSError, urllib.error.URLError, ValueError) as exc:
                artifact_observed = datetime.now(timezone.utc).isoformat()
                failures.append(f"completion_distribution_artifact_fetch_red:{artifact_id}:{exc}")
            if (
                artifact_status != 200
                or len(artifact_raw) != int(artifact.get("size") or 0)
                or hashlib.sha256(artifact_raw).hexdigest() != artifact_hash
            ):
                failures.append(f"completion_distribution_artifact_hash_mismatch:{artifact_id}")
            else:
                try:
                    artifact_value = json.loads(artifact_raw.decode("utf-8-sig"))
                except (UnicodeError, json.JSONDecodeError):
                    failures.append(f"completion_distribution_artifact_json_invalid:{artifact_id}")
                else:
                    if isinstance(artifact_value, Mapping):
                        public_artifact_values[artifact_id] = artifact_value
                        public_artifact_hashes[artifact_id] = artifact_hash
                    else:
                        failures.append(f"completion_distribution_artifact_json_invalid:{artifact_id}")
            artifact_network_observations[artifact_id] = _observation_metadata(
                observation_context,
                request_started_at=artifact_started,
                response_observed_at=artifact_observed,
                body=artifact_raw,
                content={"url": artifact_url, "statusCode": artifact_status},
                status_code=artifact_status,
                observation_kind="network_fetch",
                source_identity=artifact_url,
                source_path=artifact_url,
            )
        public_binding = public_artifact_values.get("distribution-binding", {})
        expected_component_hashes = {
            "distributionSha256": public_artifact_hashes.get("distribution", ""),
            "dailyAudioProjectionSha256": public_artifact_hashes.get("daily-audio-projection", ""),
            "deepdiveAudioProjectionSha256": public_artifact_hashes.get("deepdive-audio-projection", ""),
            "notificationStateSha256": public_artifact_hashes.get("notification-ledger", ""),
            "youtubeDailyStateSha256": public_artifact_hashes.get("youtube-daily-state", ""),
            "youtubeDeepdiveStateSha256": public_artifact_hashes.get("youtube-deepdive-state", ""),
            "playlistBindingStateSha256": public_artifact_hashes.get("playlist-binding", ""),
        }
        if (
            not public_binding
            or public_binding.get("runId") != run_id
            or public_binding.get("runIntent") != run_intent
            or public_binding.get("manifestId") != manifest_id
            or public_binding.get("bundleId") != bundle_id
            or public_binding.get("releaseCommitSha") != release_commit_sha
            or any(
                public_binding.get(field) != digest
                for field, digest in expected_component_hashes.items()
            )
        ):
            failures.append("completion_distribution_binding_public_component_mismatch")

    ok = not failures and status_code == 200
    output = {
        "ok": ok,
        "semantic_ok": ok,
        "status": "verified" if ok else "blocked",
        "issue_date": issue_date,
        "url": url or expected_url,
        "expectedSha256": expected_sha256,
        "observedSha256": observed_sha256,
        "operationCount": len(value.get("operations") or ()) if isinstance(value, Mapping) else 0,
        "providerDeliveryAckStatus": (
            value.get("providerDeliveryAckStatus") if isinstance(value, Mapping) else ""
        ),
        "reasonCodes": sorted(set(failures)),
        "networkObservations": artifact_network_observations,
    }
    output["observation"] = _observation_metadata(
        observation_context,
        request_started_at=request_started,
        response_observed_at=response_observed,
        body=raw,
        content={"url": url or expected_url, "statusCode": status_code},
        status_code=status_code,
        observation_kind="network_fetch",
        source_identity=url or expected_url,
        source_path=url or expected_url,
    )
    return output


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
    observation_context: Mapping[str, Any] | None = None,
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
    expected_public: dict[str, Mapping[str, Any]] = {}
    if manifest is not None:
        public_kinds = {
            "public_home", "issue_index", "summary_html", "deepdive_html",
            "category_html", "publish_status",
        }
        for row in manifest.get("entries") or []:
            if (
                not isinstance(row, Mapping)
                or row.get("required") is not True
                or row.get("commitRole") != "publication"
                or row.get("artifactKind") not in public_kinds
            ):
                continue
            public_url = str(row.get("publicUrl") or "")
            if not public_url.startswith(base):
                raise ValueError("manifest_required_public_url_outside_base")
            relative = public_url[len(base):]
            if relative in expected_public:
                raise ValueError("manifest_required_public_url_duplicate")
            expected_public[relative] = row
        if set(expected_public) != set(paths.values()):
            raise ValueError("manifest_required_public_url_set_mismatch")
    observed: dict[str, dict[str, Any]] = {}
    bodies: dict[str, str] = {}
    failures: list[str] = []

    for name, rel in paths.items():
        url = base + rel
        if cache_bust:
            marker = str((manifest or {}).get("manifestId") or "unverified")
            nonce = secrets.token_hex(6)
            url += "?" + urlencode({"v": f"{marker}-{nonce}"})
        request_started = datetime.now(timezone.utc).isoformat()
        try:
            request = urllib.request.Request(url, headers={"Cache-Control": "no-cache", "Pragma": "no-cache"})
            with _open_public_no_redirect(request, timeout=20) as response:
                raw_body = response.read(512_001)
                if len(raw_body) > 512_000:
                    raise ValueError("public_surface_body_too_large")
                body = raw_body.decode("utf-8", errors="replace")
                code = int(getattr(response, "status", 200))
                response_observed = datetime.now(timezone.utc).isoformat()
        except (OSError, urllib.error.URLError, UnicodeError, ValueError) as exc:
            response_observed = datetime.now(timezone.utc).isoformat()
            observed[name] = {
                "ok": False,
                "url": url,
                "reason": str(exc),
                "semantic_ok": False,
                "status": "red",
            }
            if observation_context is not None:
                observed[name]["observation"] = _observation_metadata(
                    observation_context,
                    request_started_at=request_started,
                    response_observed_at=response_observed,
                    content={"url": url, "error": str(exc)},
                    observation_kind="network_fetch",
                    source_identity=url,
                    source_path=url,
                )
            failures.append(f"fetch_failed:{name}")
            continue

        contains_issue_date = issue_date in body
        bodies[name] = body
        digest_matches = True
        expected_row = expected_public.get(rel)
        observed_digest = ""
        expected_digest = ""
        if expected_row is not None:
            from tools.news_grasp_publish_contract import artifact_digest_bytes

            observed_digest = artifact_digest_bytes(
                raw_body,
                str(expected_row.get("artifactKind") or ""),
            )
            expected_digest = str(expected_row.get("digest") or "")
            digest_matches = observed_digest == expected_digest
        observed[name] = {
            "ok": 200 <= code < 300 and digest_matches,
            "url": url,
            "status_code": code,
            "contains_issue_date": contains_issue_date,
            "expected_digest": expected_digest,
            "observed_digest": observed_digest,
            "digest_matches": digest_matches,
            "semantic_ok": 200 <= code < 300 and digest_matches and (name in {"home", "publish_status"} or contains_issue_date),
            "status": "green" if 200 <= code < 300 and digest_matches else "red",
        }
        if observation_context is not None:
            observed[name]["observation"] = _observation_metadata(
                observation_context,
                request_started_at=request_started,
                response_observed_at=response_observed,
                body=raw_body,
                content={"url": url, "statusCode": code, "surface": name},
                status_code=code,
                observation_kind="network_fetch",
                source_identity=url,
                source_path=url,
            )
        if not observed[name]["ok"]:
            failures.append(f"http_red:{name}")
        if not digest_matches:
            failures.append(f"public_digest_mismatch:{name}")
        if name not in {"home", "publish_status"} and not contains_issue_date:
            failures.append(f"issue_date_missing:{name}")

    status_row = observed.get("publish_status")
    if status_row and status_row.get("ok") is True:
        status_request_started = datetime.now(timezone.utc).isoformat()
        try:
            request = urllib.request.Request(status_row["url"], headers={"Cache-Control": "no-cache", "Pragma": "no-cache"})
            with _open_public_no_redirect(request, timeout=20) as response:
                status_raw_body = response.read(256_000)
                status_value = json.loads(status_raw_body.decode("utf-8", errors="replace"))
                status_response_observed = datetime.now(timezone.utc).isoformat()
        except (OSError, urllib.error.URLError, UnicodeError, json.JSONDecodeError) as exc:
            status_response_observed = datetime.now(timezone.utc).isoformat()
            status_row["semantic_ok"] = False
            status_row["json_error"] = str(exc)
            if observation_context is not None:
                status_row["observation"] = _observation_metadata(
                    observation_context,
                    request_started_at=status_request_started,
                    response_observed_at=status_response_observed,
                    content={"url": status_row.get("url"), "error": str(exc)},
                    observation_kind="network_fetch",
                    source_identity=str(status_row.get("url") or ""),
                    source_path=str(status_row.get("url") or ""),
                )
            failures.append("publish_status_public_json_invalid")
        else:
            status_row["json"] = status_value
            if observation_context is not None:
                status_row["observation"] = _observation_metadata(
                    observation_context,
                    request_started_at=status_request_started,
                    response_observed_at=status_response_observed,
                    body=status_raw_body,
                    content={"url": status_row.get("url"), "value": status_value},
                    status_code=200,
                    observation_kind="network_fetch",
                    source_identity=str(status_row.get("url") or ""),
                    source_path=str(status_row.get("url") or ""),
                )
            if not isinstance(status_value, dict) or status_value.get("date") != issue_date:
                status_row["semantic_ok"] = False
                failures.append("publish_status_public_date_mismatch")

    semantic = {"ok": True, "reasonCodes": []}
    if manifest is not None:
        from tools.news_grasp_publish_contract import verify_semantic_pages

        semantic = verify_semantic_pages(manifest, bodies)
        failures.extend(semantic.get("reasonCodes") or [])

    ok = not failures and semantic.get("ok") is True and all(row.get("semantic_ok") is True for row in observed.values())
    result = {
        "ok": ok,
        "issue_date": issue_date,
        "observed": observed,
        "failures": failures,
        "semantic": semantic,
        "semantic_ok": ok,
        "status": "green" if ok else "red",
    }
    if observation_context is not None:
        result["networkObservations"] = {
            name: row.get("observation")
            for name, row in observed.items()
            if isinstance(row, Mapping) and isinstance(row.get("observation"), Mapping)
        }
        result["observation"] = _observation_metadata(
            observation_context,
            content={"surface": "web", "observed": result["networkObservations"], "failures": failures},
            observation_kind="local_canonical_read",
            source_identity="public_web_aggregate",
            source_path=base,
        )
    return result


def _up_to_date_observation(
    repo_root: Path,
    remote: str,
    branch: str,
    observation_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
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
    request_started = datetime.now(timezone.utc).isoformat()
    remote_proc = git("ls-remote", "--end-of-options", "origin", "refs/heads/main")
    response_observed = datetime.now(timezone.utc).isoformat()
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
    result = {
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
    if observation_context is not None:
        result["networkObservations"] = {
            "remote": _observation_metadata(
                observation_context,
                request_started_at=request_started,
                response_observed_at=response_observed,
                body=remote_proc.stdout,
                content={
                    "status": status_proc.stdout,
                    "head": head_proc.stdout,
                    "remote": remote_proc.stdout,
                },
                observation_kind="network_fetch",
                source_identity=f"git-ls-remote:{remote}/{branch}",
                source_path=f"{remote}:{branch}",
            )
        }
    return result


def _pages_workflow_observation(
    *,
    remote_head: str,
    manifest_id: str,
    issue_date: str,
    release_kind: str = "public",
    changed_paths: list[str] | None = None,
    observation_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """GitHub Actionsの最新Pages成功runをremote HEADへ束縛する。"""
    from tools.news_grasp_publish_contract import evaluate_pages_deployment

    url = "https://api.github.com/repos/HIDEPON-UMG/News-Grasp/actions/workflows/deploy-pages.yml/runs?branch=main&per_page=20"
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "News-Grasp-public-verifier"},
    )
    request_started = datetime.now(timezone.utc).isoformat()
    try:
        with _open_github_actions_no_redirect(request, timeout=20) as response:
            raw = response.read(1_000_001)
            if len(raw) > 1_000_000:
                raise ValueError("pages_workflow_response_too_large")
            value = json.loads(raw.decode("utf-8"))
            response_observed = datetime.now(timezone.utc).isoformat()
    except (OSError, ValueError, urllib.error.URLError, UnicodeError, json.JSONDecodeError) as exc:
        response_observed = datetime.now(timezone.utc).isoformat()
        result = {"ok": False, "status": "blocked", "reasonCodes": ["pages_workflow_fetch_failed"], "detail": str(exc), "semantic_ok": False}
        if observation_context is not None:
            result["observation"] = _observation_metadata(
                observation_context,
                request_started_at=request_started,
                response_observed_at=response_observed,
                content={"url": url, "error": str(exc)},
                observation_kind="network_fetch",
                source_identity=url,
                source_path=url,
            )
        return result
    rows = value.get("workflow_runs") if isinstance(value, dict) else []
    pages_rows = [row for row in rows or [] if isinstance(row, dict) and str(row.get("path") or "") == ".github/workflows/deploy-pages.yml"]
    result = evaluate_pages_deployment(
        remote_head=remote_head,
        workflow_runs=pages_rows,
        manifest_id=manifest_id,
        issue_date=issue_date,
        release_kind=release_kind,
        changed_paths=changed_paths,
    )
    output = {**result, "semantic_ok": result.get("ok") is True, "apiUrl": url}
    workflow_run = result.get("workflowRun") if isinstance(result.get("workflowRun"), Mapping) else {}
    workflow_sha = str(
        workflow_run.get("deployment_sha")
        or workflow_run.get("deploymentSha")
        or workflow_run.get("release_commit_sha")
        or workflow_run.get("releaseCommitSha")
        or workflow_run.get("head_sha")
        or ""
    )
    pages_binding_failures: list[str] = []
    if workflow_sha and workflow_sha != remote_head:
        pages_binding_failures.append("pages_deployment_sha_mismatch")
    for key in ("manifest_id", "manifestId", "page_manifest_id", "pageManifestId"):
        if key in workflow_run and str(workflow_run.get(key) or "") != manifest_id:
            pages_binding_failures.append("pages_manifest_binding_mismatch")
    for key in ("issue_date", "issueDate", "page_issue_date", "pageIssueDate"):
        if key in workflow_run and str(workflow_run.get(key) or "") != issue_date:
            pages_binding_failures.append("pages_issue_date_binding_mismatch")
    if pages_binding_failures:
        output["reasonCodes"] = sorted(set((output.get("reasonCodes") or []) + pages_binding_failures))
        output["ok"] = False
        output["semantic_ok"] = False
        output["status"] = "blocked"
    output["deploymentBinding"] = {
        "deploymentSha": workflow_sha,
        "remoteHead": remote_head,
        "releaseCommitSha": remote_head,
        "manifestId": manifest_id,
        "issueDate": issue_date,
        "markerRequired": True,
    }
    if observation_context is not None:
        output["observation"] = _observation_metadata(
            observation_context,
            request_started_at=request_started,
            response_observed_at=response_observed,
            body=raw,
            content={"url": url, "value": value},
            status_code=200,
            observation_kind="network_fetch",
            source_identity=url,
            source_path=url,
        )
    return output


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
    bundle_id: str = "",
    release_commit_sha: str = "",
    external_outbox: Sequence[Mapping[str, object]] | None = None,
    cache_bust: bool = True,
    observation_token: str = "",
    observed_at: str = "",
    external_operation_id: str = "",
) -> dict[str, Any]:
    _validate_transport_policy(remote=remote, branch=branch, wait_sec=wait_sec, poll_sec=poll_sec)
    # callerから渡されたfreshness値は観測authorityではない。開始時にverifier自身の
    # nonce/token/time/external operation identityを発行し、最後まで同じcontextを使う。
    caller_observation_inputs = {
        "observationToken": observation_token,
        "observedAt": observed_at,
        "externalOperationId": external_operation_id,
    }
    observation_context = _new_observation_context(
        issue_date=issue_date,
        run_id=run_id,
        run_intent=run_intent,
    )
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
    _bind_observation_context(
        observation_context,
        issue_date=issue_date,
        run_id=run_id,
        run_intent=run_intent,
        manifest_id=manifest_id,
    )
    observation_token = str(observation_context["token"])
    observed_at = str(observation_context["startedAt"])
    external_operation_id = str(observation_context["externalOperationId"])
    fresh_observation_failures: list[str] = []
    outbox_payload_identities = {
        str(row.get("operation_id") or ""): str(row.get("payload_identity") or "")
        for row in (external_outbox or ())
        if isinstance(row, Mapping)
    }
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
    surfaces["daily_audio"] = _audio_projection(
        repo,
        issue_date,
        audio_type="daily",
        run_id=run_id,
        run_intent=run_intent,
        expected_sha256=outbox_payload_identities.get("audio_daily_upload", ""),
        observation_context=observation_context,
    )
    surfaces["deepdive_audio"] = _deepdive_audio(
        repo,
        issue_date,
        run_id=run_id,
        run_intent=run_intent,
        expected_sha256=outbox_payload_identities.get("audio_deepdive_upload", ""),
        observation_context=observation_context,
    )
    surfaces["distribution"] = _required_distribution(
        repo,
        issue_date,
        manifest=manifest,
        run_id=run_id,
        run_intent=run_intent,
        manifest_id=manifest_id,
        bundle_id=bundle_id,
        release_commit_sha=release_commit_sha,
    )
    surfaces["publish_status"] = _publish_status(
        repo,
        issue_date,
        run_id=run_id,
        run_intent=run_intent,
    )
    surfaces.update(
        _podcast_rows(
            repo,
            issue_date,
            wait_sec=wait_sec,
            poll_sec=poll_sec,
            run_id=run_id,
            run_intent=run_intent,
            observation_context=observation_context,
        )
    )
    surfaces["notification"] = _notification(
        repo,
        issue_date,
        run_id=run_id,
        run_intent=run_intent,
        observation_context=observation_context,
    )
    surfaces["completion_attestation"] = _completion_attestation_surface(
        issue_date=issue_date,
        run_id=run_id,
        run_intent=run_intent,
        manifest_id=manifest_id,
        bundle_id=bundle_id,
        release_commit_sha=release_commit_sha,
        manifest=manifest,
        external_outbox=external_outbox,
        observation_context=observation_context,
    )
    side_effect_identity = _validate_side_effect_identity(
        surfaces,
        expected_outbox=external_outbox,
    )
    side_effect_identity["observationBindingSha256"] = str(
        observation_context.get("bindingSha256") or ""
    )
    side_effect_identity["externalOperationId"] = str(
        observation_context.get("externalOperationId") or ""
    )
    # 任意のkey名に ``duplicate`` が含まれるだけでは副作用の重複証拠にしない。
    # sealed operation ID とimmutable ledgerのidentity集合だけが判定authorityである。
    duplicate_side_effect_failures = sorted(set(side_effect_identity.get("failures") or []))
    if duplicate_side_effect_failures:
        surfaces["side_effect_identity"] = {
            "ok": False,
            "issue_date": issue_date,
            "reasonCodes": duplicate_side_effect_failures,
            "sealedOperationIds": side_effect_identity.get("sealedOperationIds", []),
            "payloadIdentities": side_effect_identity.get("payloadIdentities", []),
            "sealedSetSha256": side_effect_identity.get("sealedSetSha256", ""),
            "semantic_ok": False,
            "status": "blocked",
        }
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
        observation_context=observation_context,
    )
    # local canonical docsとpublic network probeを同じweb surfaceへ束ねる。
    # public probeをpagesだけの子要素に閉じると、web全体のfresh receiptが
    # surface名へ投影されず、Home/category/Summary/DeepDiveの観測を落とす。
    local_web = surfaces.get("web")
    if not isinstance(local_web, dict):
        local_web = {}
    surfaces["web"] = {
        **local_web,
        "public": public_web,
        "ok": local_web.get("ok") is True and public_web.get("ok") is True,
        "semantic_ok": local_web.get("semantic_ok") is True and public_web.get("semantic_ok") is True,
        "status": "verified" if local_web.get("ok") is True and public_web.get("ok") is True else "blocked",
    }
    # DeepDiveの文章品質predicateはcurrent_issue_integrationだけが所有する。
    # consumer verifierは再監査せず、sealed manifest上のMarkdownと、同じ
    # manifest markerを持つ公開HTMLが同一releaseへ束縛されたことだけを投影する。
    deepdive_entry = next(
        (
            dict(row)
            for row in (manifest or {}).get("entries", ())
            if isinstance(row, Mapping) and row.get("artifactKind") == "deepdive_source"
        ),
        {},
    )
    deepdive_html_entry = next(
        (
            dict(row)
            for row in (manifest or {}).get("entries", ())
            if isinstance(row, Mapping) and row.get("artifactKind") == "deepdive_html"
        ),
        {},
    )
    deepdive_bound = (
        surfaces["web"].get("ok") is True
        and bool(deepdive_entry.get("digest"))
        and deepdive_entry.get("digest") != "unverified"
        and bool(deepdive_html_entry.get("digest"))
        and deepdive_html_entry.get("digest") != "unverified"
    )
    surfaces["deepdive_article"] = {
        "ok": deepdive_bound,
        "semantic_ok": deepdive_bound,
        "status": "verified" if deepdive_bound else "blocked",
        "issue_date": issue_date,
        "predicateOwner": "current_issue_integration",
        "consumerCheck": "manifest_markdown_and_public_html_release_binding",
        "markdown": deepdive_entry,
        "html": deepdive_html_entry,
    }
    public_status = (
        public_web.get("observed", {}).get("publish_status")
        if isinstance(public_web.get("observed"), Mapping)
        else None
    )
    local_publish_status = surfaces.get("publish_status")
    if isinstance(local_publish_status, dict) and isinstance(public_status, Mapping):
        public_status_ok = public_status.get("ok") is True and public_status.get("semantic_ok") is True
        surfaces["publish_status"] = {
            **local_publish_status,
            "public": dict(public_status),
            "ok": local_publish_status.get("ok") is True and public_status_ok,
            "semantic_ok": local_publish_status.get("semantic_ok") is True and public_status_ok,
            "status": "verified" if local_publish_status.get("ok") is True and public_status_ok else "blocked",
        }
    remote_observation = _up_to_date_observation(repo, remote, branch, observation_context)
    if release_commit_sha and (
        remote_observation.get("head") != release_commit_sha
        or remote_observation.get("remoteHead") != release_commit_sha
    ):
        remote_observation = {
            **remote_observation,
            "ok": False,
            "semantic_ok": False,
            "status": "blocked",
            "reason": "release_commit_sha_not_remote_head",
            "expectedReleaseCommitSha": release_commit_sha,
        }
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
        observation_context=observation_context,
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
    for surface_name, surface_value in list(surfaces.items()):
        surfaces[surface_name] = _attach_surface_observation(
            surface_name,
            surface_value,
            observation_context,
        )
    observation_rows_with_paths: list[tuple[tuple[str, ...], dict[str, Any]]] = []
    for surface_name, surface_value in surfaces.items():
        observation_rows_with_paths.extend(
            _collect_observation_rows(surface_value, path=(surface_name,))
        )
    observation_rows = [row for _path, row in observation_rows_with_paths]
    expected_nonce = str(observation_context.get("nonce") or "")
    expected_token = str(observation_context.get("token") or "")
    expected_binding = str(observation_context.get("bindingSha256") or "")
    for _path, observation in observation_rows_with_paths:
        observation_without_digest = {
            key: value
            for key, value in observation.items()
            if key != "observationSha256"
        }
        if (
            observation.get("observationKind") not in {"local_canonical_read", "network_fetch"}
            or observation.get("schemaVersion") != OBSERVATION_SCHEMA
            or observation.get("nonce") != expected_nonce
            or observation.get("token") != expected_token
            or observation.get("issueDate") != issue_date
            or observation.get("bindingSha256") != expected_binding
            or observation.get("manifestId") != manifest_id
            or observation.get("runId") != run_id
            or observation.get("runIntent") != run_intent
            or observation.get("externalOperationId") != external_operation_id
            or not observation.get("requestStartedAt")
            or not observation.get("responseObservedAt")
            or not re.fullmatch(r"[0-9a-f]{64}", str(observation.get("bodySha256") or ""))
            or not re.fullmatch(r"[0-9a-f]{64}", str(observation.get("contentSha256") or ""))
            or (
                observation.get("observationKind") == "local_canonical_read"
                and (
                    not str(observation.get("sourceIdentity") or "")
                    or not str(observation.get("sourcePath") or "")
                )
            )
            or observation.get("observationSha256") != _canonical_observation_sha256(observation_without_digest)
            or (
                observation.get("observationKind") == "network_fetch"
                and observation.get("freshNetwork") is not True
            )
            or (
                observation.get("observationKind") == "local_canonical_read"
                and observation.get("freshNetwork") is not False
            )
            or (
                observation.get("observationKind") == "network_fetch"
                and not _observation_is_fresh(
                    observation,
                    started=observation_context["started"],
                )
            )
        ):
            fresh_observation_failures.append("fresh_public_observation_binding_invalid")
            break
    if not observation_rows:
        fresh_observation_failures.append("fresh_public_observation_missing")
    required_network_surfaces = {
        "web",
        "daily_audio",
        "deepdive_audio",
        "youtube_daily",
        "youtube_deepdive",
        "playlist",
        "pages",
        "remote_commit",
        "publish_status",
        "completion_attestation",
    }
    network_surface_names = {
        path[0]
        for path, observation in observation_rows_with_paths
        if observation.get("observationKind") == "network_fetch"
    }
    missing_network_surfaces = sorted(required_network_surfaces - network_surface_names)
    if missing_network_surfaces:
        fresh_observation_failures.append(
            "fresh_network_observation_missing:" + ",".join(missing_network_surfaces)
        )
    failures: list[str] = []
    failures.extend(fresh_observation_failures)
    failures.extend(duplicate_side_effect_failures)
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
        "observationToken": observation_token,
        "observedAt": observed_at,
        "externalOperationId": external_operation_id,
        "observation": {
            "schemaVersion": OBSERVATION_SCHEMA,
            "nonce": observation_context.get("nonce", ""),
            "token": observation_token,
            "observedAt": observed_at,
            "issueDate": issue_date,
            "externalOperationId": external_operation_id,
            "manifestId": manifest_id,
            "runId": run_id,
            "runIntent": run_intent,
            "bindingSha256": observation_context.get("bindingSha256", ""),
            "observationCount": len(observation_rows),
            "freshNetwork": not fresh_observation_failures and bool(observation_rows),
        },
        "callerObservationInputIgnored": True,
        "callerObservationInputPresence": {
            key: bool(value)
            for key, value in caller_observation_inputs.items()
        },
        "sideEffectIdentity": side_effect_identity,
        "observations": observation_rows,
        "status": "verified" if not failures else "blocked",
        "public_surfaces": surfaces,
        "failures": failures,
        "post_publish_issue_list": post_publish_issues,
    }
