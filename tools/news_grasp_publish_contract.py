"""News-Grasp公開manifest・semantic surface・leaseの単一authority。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import stat
import sys
import tempfile
from contextlib import closing
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping
from urllib.parse import urljoin, urlsplit, urlunsplit

from tools.news_grasp_audio_projection import AUDIO_SCHEMA, RUN_INTENT
from tools.publish_inventory import (
    digest_artifact_for_category,
    docs_artifact_for_category,
    scheduled_category_ids,
)


MANIFEST_SCHEMA = "NEWS_GRASP_PUBLISH_MANIFEST_V2"
OBSERVATION_SCHEMA = "NEWS_GRASP_RUN_OBSERVATION_V1"
PUBLIC_STATUS_VALUES = {"verified", "warning", "deferred", "blocked", "unverified"}
JST = timezone(timedelta(hours=9), name="Asia/Tokyo")
_MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
_MANIFEST_META_TEXT = re.compile(
    r'<meta name="news-grasp-manifest-id" content="[0-9a-f]{64}">'
)
_HTML_VOID_ELEMENTS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}


class _ManifestMetaLocator(HTMLParser):
    """script/comment文字列を除き、direct-head canonical markerの実offsetを得る。"""

    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=False)
        self._line_offsets = [0]
        for match in re.finditer(r"\n", source):
            self._line_offsets.append(match.end())
        self.stack: list[str] = []
        self.marker_spans: list[tuple[int, int]] = []
        self.invalid_marker = False
        self.head_end_offsets: list[int] = []

    def _offset(self) -> int:
        line, column = self.getpos()
        return self._line_offsets[line - 1] + column

    @staticmethod
    def _is_marker(attrs: list[tuple[str, str | None]]) -> bool:
        return any(name.casefold() == "name" and str(value or "").casefold() == "news-grasp-manifest-id" for name, value in attrs)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.casefold()
        raw = self.get_starttag_text()
        start = self._offset()
        if lowered == "meta" and self._is_marker(attrs):
            if self.stack and self.stack[-1] == "head" and _MANIFEST_META_TEXT.fullmatch(raw):
                self.marker_spans.append((start, start + len(raw)))
            else:
                self.invalid_marker = True
        if lowered not in _HTML_VOID_ELEMENTS:
            self.stack.append(lowered)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() == "meta" and self._is_marker(attrs):
            self.invalid_marker = True

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.casefold()
        if lowered == "head" and self.stack and self.stack[-1] == "head":
            self.head_end_offsets.append(self._offset())
        if lowered in self.stack:
            del self.stack[len(self.stack) - 1 - self.stack[::-1].index(lowered):]


def _manifest_meta_layout(text: str, *, require_head: bool) -> tuple[tuple[int, int] | None, int | None]:
    locator = _ManifestMetaLocator(text)
    locator.feed(text)
    locator.close()
    if locator.invalid_marker or len(locator.marker_spans) > 1:
        raise ValueError("manifest_meta_shape_invalid")
    if require_head and len(locator.head_end_offsets) != 1:
        raise ValueError("html_head_missing_or_ambiguous")
    marker = locator.marker_spans[0] if locator.marker_spans else None
    head_end = locator.head_end_offsets[0] if len(locator.head_end_offsets) == 1 else None
    if marker is not None:
        start, end = marker
        line_break = "\r\n" if text.startswith("\r\n", end) else "\n" if text.startswith("\n", end) else ""
        if head_end is None or start < 2 or text[start - 2:start] != "  " or not line_break or head_end != end + len(line_break):
            raise ValueError("manifest_meta_placement_invalid")
    return marker, head_end


def _marker_neutral_html_bytes(raw: bytes) -> bytes:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("manifest_html_utf8_invalid") from exc
    marker, _ = _manifest_meta_layout(text, require_head=False)
    if marker is None:
        return raw
    start, end = marker
    line_break_length = 2 if text.startswith("\r\n", end) else 1
    return (text[:start - 2] + text[end + line_break_length:]).encode("utf-8")


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _json_file_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _manifest_identity(value: Mapping[str, Any]) -> dict[str, Any]:
    """生成物自身のmarker更新と循環しないmanifest identity材料。"""
    entries = value.get("entries") if isinstance(value.get("entries"), list) else []
    return {
        "schemaVersion": value.get("schemaVersion"),
        "runId": value.get("runId"),
        "issueDate": value.get("issueDate"),
        "runIntent": value.get("runIntent"),
        "sourceBaseline": value.get("sourceBaseline"),
        "scheduledCategoryIds": value.get("scheduledCategoryIds"),
        "entries": [
            {
                "localPath": row.get("localPath"),
                "artifactKind": row.get("artifactKind"),
                "publicUrl": row.get("publicUrl"),
                "linkFrom": row.get("linkFrom"),
                "required": row.get("required"),
                "commitRole": row.get("commitRole"),
                "exists": row.get("exists"),
                "digest": row.get("digest"),
                "digestAuthority": row.get("digestAuthority"),
            }
            for row in entries
            if isinstance(row, Mapping)
        ],
        "audio": value.get("audio"),
        "exactWriteSet": value.get("exactWriteSet"),
        "commitRole": value.get("commitRole"),
    }


def _validate_issue_date(value: str) -> str:
    from datetime import date

    raw = str(value)
    try:
        parsed = date.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError("manifest_issue_date_invalid") from exc
    if parsed.isoformat() != raw:
        raise ValueError("manifest_issue_date_invalid")
    return raw


def _safe_repo_relative_path(root: Path, value: str | Path) -> tuple[str, Path]:
    raw = str(value).replace("\\", "/")
    parts = raw.split("/")
    if not raw or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw) or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("manifest_path_invalid")
    normalized = PurePosixPath(raw).as_posix()
    candidate = root / normalized
    resolved = candidate.resolve(strict=False)
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError("manifest_path_invalid")
    current = root.resolve()
    for part in PurePosixPath(normalized).parts:
        current = current / part
        if current.exists() or current.is_symlink():
            info = os.lstat(current)
            attributes = int(getattr(info, "st_file_attributes", 0))
            if stat.S_ISLNK(info.st_mode) or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
                raise ValueError("manifest_path_reparse_forbidden")
    return normalized, candidate


def _read_regular_no_follow(path: Path, *, max_bytes: int = _MAX_ARTIFACT_BYTES) -> bytes:
    before = os.lstat(path)
    attributes = int(getattr(before, "st_file_attributes", 0))
    if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode) or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
        raise ValueError("manifest_artifact_not_regular")
    if before.st_size > max_bytes:
        raise ValueError("manifest_artifact_too_large")
    flags = os.O_RDONLY | int(getattr(os, "O_BINARY", 0)) | int(getattr(os, "O_NOFOLLOW", 0))
    fd = os.open(path, flags)
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns):
            raise ValueError("manifest_artifact_identity_changed")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, min(1024 * 1024, max_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                raise ValueError("manifest_artifact_too_large")
        after = os.fstat(fd)
        if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns):
            raise ValueError("manifest_artifact_identity_changed")
        return b"".join(chunks)
    finally:
        os.close(fd)


def _directory_identity(path: Path) -> tuple[int, int]:
    info = os.lstat(path)
    attributes = int(getattr(info, "st_file_attributes", 0))
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
        raise ValueError("publish_parent_not_trusted_directory")
    return (info.st_dev, info.st_ino)


def _capture_repo_write_guard(root: Path, relative: str) -> dict[str, Any]:
    """raw parent identityとpreimageを同じrepo-relative targetへ束縛する。"""

    normalized, target = _safe_repo_relative_path(root, relative)
    _reject_external_reparse_chain(root, reason="publish_repo_root_reparse_forbidden")
    _reject_external_reparse_chain(target.parent, reason="publish_parent_reparse_forbidden")
    target.parent.mkdir(parents=True, exist_ok=True)
    _reject_external_reparse_chain(target.parent, reason="publish_parent_reparse_forbidden")
    parent_identity = _directory_identity(target.parent)
    preimage: bytes | None = None
    if target.exists() or target.is_symlink():
        preimage = _read_regular_no_follow(target)
    if _directory_identity(target.parent) != parent_identity:
        raise ValueError("publish_parent_identity_changed")
    return {"relative": normalized, "parentIdentity": parent_identity, "preimage": preimage}


def _assert_repo_write_guard(root: Path, guard: Mapping[str, Any]) -> Path:
    _, target = _safe_repo_relative_path(root, str(guard.get("relative") or ""))
    _reject_external_reparse_chain(root, reason="publish_repo_root_reparse_forbidden")
    _reject_external_reparse_chain(target.parent, reason="publish_parent_reparse_forbidden")
    observed = _directory_identity(target.parent)
    if tuple(guard.get("parentIdentity") or ()) != observed:
        raise ValueError("publish_parent_identity_changed")
    if target.exists() or target.is_symlink():
        info = os.lstat(target)
        attributes = int(getattr(info, "st_file_attributes", 0))
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise ValueError("publish_target_not_regular")
    return target


def _guarded_repo_write(root: Path, guard: Mapping[str, Any], value: bytes) -> str:
    target = _assert_repo_write_guard(root, guard)
    handle, temp_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        _assert_repo_write_guard(root, guard)
        os.replace(temp_name, target)
        _assert_repo_write_guard(root, guard)
        observed = _read_regular_no_follow(target, max_bytes=max(_MAX_ARTIFACT_BYTES, len(value)))
        if observed != value:
            raise RuntimeError("publish_target_postcondition_red")
        return hashlib.sha256(value).hexdigest()
    finally:
        Path(temp_name).unlink(missing_ok=True)


def _guarded_repo_rollback(root: Path, guard: Mapping[str, Any], *, owned_sha256: str) -> None:
    """親または現在bytesが変わったtargetには触れず、手動復旧へfail-closedする。"""

    target = _assert_repo_write_guard(root, guard)
    current = _read_regular_no_follow(target)
    if hashlib.sha256(current).hexdigest() != owned_sha256:
        raise RuntimeError("publish_rollback_target_not_owned")
    preimage = guard.get("preimage")
    if preimage is None:
        target = _assert_repo_write_guard(root, guard)
        current = _read_regular_no_follow(target)
        if hashlib.sha256(current).hexdigest() != owned_sha256:
            raise RuntimeError("publish_rollback_target_not_owned")
        target.unlink()
        _assert_repo_write_guard(root, guard)
        return
    if not isinstance(preimage, bytes):
        raise RuntimeError("publish_rollback_preimage_invalid")
    _guarded_repo_write(root, guard, preimage)


def _read_external_json_receipt(path: str | Path, *, max_bytes: int = 1_048_576) -> tuple[dict[str, Any], str]:
    """外部receiptを親junctionを含めて追跡せず、bounded snapshotとして読む。"""

    absolute = Path(os.path.abspath(os.fspath(path)))
    for current in reversed((absolute, *absolute.parents)):
        if str(current) == current.anchor or (not current.exists() and not current.is_symlink()):
            continue
        info = os.lstat(current)
        attributes = int(getattr(info, "st_file_attributes", 0))
        if stat.S_ISLNK(info.st_mode) or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise ValueError("distribution_source_reparse_forbidden")
    raw = _read_regular_no_follow(absolute, max_bytes=max_bytes)
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("distribution_source_json_invalid") from exc
    if not isinstance(value, dict):
        raise ValueError("distribution_source_not_object")
    return value, hashlib.sha256(raw).hexdigest()


def _reject_external_reparse_chain(path: str | Path, *, reason: str) -> None:
    absolute = Path(os.path.abspath(os.fspath(path)))
    for current in reversed((absolute, *absolute.parents)):
        if str(current) == current.anchor or (not current.exists() and not current.is_symlink()):
            continue
        info = os.lstat(current)
        attributes = int(getattr(info, "st_file_attributes", 0))
        if stat.S_ISLNK(info.st_mode) or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise ValueError(reason)


def _artifact_digest(path: Path, artifact_kind: str) -> str:
    raw = _read_regular_no_follow(path)
    if artifact_kind in {"public_home", "issue_index", "summary_html", "deepdive_html", "category_html"}:
        raw = _marker_neutral_html_bytes(raw)
    elif artifact_kind == "publish_status":
        value = json.loads(raw.decode("utf-8-sig"))
        if not isinstance(value, dict):
            raise ValueError("publish_status_not_object")
        for key in ("manifestId", "runId", "runIntent"):
            value.pop(key, None)
        raw = _json_bytes(value)
    return hashlib.sha256(raw).hexdigest()


def _entry(
    root: Path,
    local_path: str,
    artifact_kind: str,
    public_url: str,
    *,
    link_from: str = "",
    required: bool = True,
    commit_role: str = "publication",
) -> dict[str, Any]:
    normalized, absolute = _safe_repo_relative_path(root, local_path)
    self_excluded = artifact_kind == "publish_manifest"
    exists = True if self_excluded else absolute.is_file()
    return {
        "localPath": normalized,
        "artifactKind": artifact_kind,
        "publicUrl": public_url,
        "linkFrom": link_from,
        "required": required,
        "commitRole": commit_role,
        "exists": exists,
        "digest": "self_excluded" if self_excluded else (_artifact_digest(absolute, artifact_kind) if exists else "unverified"),
        "digestAuthority": "self_excluded" if self_excluded else "artifact_bytes_marker_neutral" if artifact_kind in {"public_home", "issue_index", "summary_html", "deepdive_html", "category_html"} else "artifact_bytes",
    }


def _load_audio(root: Path, audio_type: str, issue_date: str, run_id: str, run_intent: str) -> dict[str, Any]:
    from tools.news_grasp_audio_projection import canonical_audio_path, load_audio_projection

    path = root / canonical_audio_path(audio_type)
    if path.is_file():
        try:
            return load_audio_projection(path, audio_type=audio_type, run_id=run_id, run_intent=run_intent)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            pass
    url_name = "daily" if audio_type == "daily" else "deepdive"
    return {
        "schemaVersion": AUDIO_SCHEMA,
        "audioType": audio_type,
        "sourceArtifact": "unverified",
        "runtimeState": canonical_audio_path(audio_type).as_posix(),
        "provider": {"name": "unverified", "jobIdentity": "unverified"},
        "publicUrl": f"https://hidepon-umg.github.io/News-Grasp/audio/{issue_date}-{url_name}.mp3",
        "publicPageHref": f"https://hidepon-umg.github.io/News-Grasp/audio/{issue_date}-{url_name}.mp3",
        "issueDate": issue_date,
        "runId": run_id,
        "runIntent": run_intent,
        "completionState": "unverified",
    }


def build_publish_manifest(
    *,
    repo_root: str | Path,
    issue_date: str,
    run_id: str,
    run_intent: str = RUN_INTENT,
    source_baseline: str,
    public_base_url: str = "https://hidepon-umg.github.io/News-Grasp/",
) -> dict[str, Any]:
    """scheduled category正本から全consumer共通manifestを生成する。"""
    root = Path(repo_root).resolve()
    issue_date = _validate_issue_date(issue_date)
    base = public_base_url.rstrip("/") + "/"
    categories = scheduled_category_ids(issue_date)
    entries = [
        _entry(root, f"data/publish-manifests/{issue_date}.json", "publish_manifest", base, required=True),
        _entry(root, "docs/index.html", "public_home", base, link_from="root"),
        _entry(root, "docs/sw.js", "service_worker", base + "sw.js", link_from="docs/index.html"),
        _entry(root, f"docs/{issue_date}/index.html", "issue_index", base + f"{issue_date}/", link_from="docs/index.html"),
        _entry(root, f"digest/Summary/{issue_date}.md", "summary_source", base + f"{issue_date}/summary/", commit_role="publication"),
        _entry(root, f"docs/{issue_date}/summary/index.html", "summary_html", base + f"{issue_date}/summary/", link_from=f"docs/{issue_date}/index.html"),
        _entry(root, f"digest/DeepDive/{issue_date}-DeepDive.md", "deepdive_source", base + f"deepdive/{issue_date}/"),
        _entry(root, f"docs/deepdive/{issue_date}/index.html", "deepdive_html", base + f"deepdive/{issue_date}/", link_from="docs/index.html"),
        _entry(root, "build/tts/daily/latest_audio.json", "daily_audio_state", base, link_from="docs/index.html"),
        _entry(root, "build/tts/deepdive/latest_audio.json", "deepdive_audio_state", base, link_from=f"docs/deepdive/{issue_date}/index.html"),
        _entry(root, "build/youtube-podcast/uploads.json", "youtube_daily_state", "https://www.youtube.com/", required=True),
        _entry(root, "build/youtube-podcast-deepdive/uploads.json", "youtube_deepdive_state", "https://www.youtube.com/", required=True),
        _entry(root, f"build/distribution/{issue_date}/playlist.json", "playlist_state", "https://www.youtube.com/", required=True),
        _entry(root, f"build/distribution/{issue_date}/binding.json", "distribution_binding", base, required=True),
        _entry(root, f"build/notification/{issue_date}.json", "notification_v2", base, required=True),
        _entry(root, "docs/publish-status.json", "publish_status", base + "publish-status.json"),
        _entry(root, f"data/distribution/{issue_date}.json", "distribution", base, required=True),
    ]
    for category_id in categories:
        entries.append(_entry(root, digest_artifact_for_category(category_id, issue_date), "category_digest", base + f"{category_id}/{issue_date}/"))
        entries.append(_entry(root, docs_artifact_for_category(category_id, issue_date), "category_html", base + f"{category_id}/{issue_date}/", link_from=f"docs/{issue_date}/index.html"))
    audio = {
        kind: _load_audio(root, kind, issue_date, run_id, run_intent)
        for kind in ("daily", "deepdive")
    }
    identity = {
        "schemaVersion": MANIFEST_SCHEMA,
        "runId": run_id,
        "issueDate": issue_date,
        "runIntent": run_intent,
        "sourceBaseline": source_baseline,
        "scheduledCategoryIds": categories,
        "entries": entries,
        "audio": audio,
        "exactWriteSet": sorted(row["localPath"] for row in entries),
        "commitRole": "publication",
    }
    manifest_id = hashlib.sha256(_json_bytes(_manifest_identity(identity))).hexdigest()
    return {**identity, "manifestId": manifest_id}


def verify_manifest(
    manifest: Mapping[str, Any],
    *,
    repo_root: str | Path,
    require_files: bool = False,
) -> dict[str, Any]:
    """manifest identity・カテゴリ・exact write setをfail-closed検査する。"""
    reasons: list[str] = []
    if manifest.get("schemaVersion") != MANIFEST_SCHEMA:
        reasons.append("manifest_schema_invalid")
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        entries = []
        reasons.append("manifest_entries_invalid")
    paths = [str(row.get("localPath") or "") for row in entries if isinstance(row, Mapping)]
    folded_paths = [path.casefold() for path in paths]
    if "docs/index.html" not in paths:
        reasons.append("manifest_home_missing")
    required_kinds = {
        "public_home",
        "daily_audio_state",
        "deepdive_audio_state",
        "youtube_daily_state",
        "youtube_deepdive_state",
        "playlist_state",
        "distribution_binding",
        "notification_v2",
        "publish_status",
        "distribution",
    }
    observed_kinds = {
        str(row.get("artifactKind") or "")
        for row in entries
        if isinstance(row, Mapping)
    }
    for missing_kind in sorted(required_kinds - observed_kinds):
        reasons.append(f"manifest_required_kind_missing:{missing_kind}")
    if len(paths) != len(set(paths)) or len(folded_paths) != len(set(folded_paths)):
        reasons.append("manifest_duplicate_path")
    issue_date = str(manifest.get("issueDate") or "")
    try:
        expected_categories = scheduled_category_ids(issue_date)
    except ValueError:
        expected_categories = []
        reasons.append("manifest_issue_date_invalid")
    if list(manifest.get("scheduledCategoryIds") or []) != expected_categories:
        reasons.append("manifest_schedule_mismatch")
    expected_write_set = sorted(paths)
    if list(manifest.get("exactWriteSet") or []) != expected_write_set:
        reasons.append("manifest_exact_write_set_mismatch")
    try:
        canonical = build_publish_manifest(
            repo_root=repo_root,
            issue_date=issue_date,
            run_id=str(manifest.get("runId") or ""),
            run_intent=str(manifest.get("runIntent") or ""),
            source_baseline=str(manifest.get("sourceBaseline") or ""),
        )
    except (OSError, ValueError, json.JSONDecodeError):
        canonical = {"entries": []}
        reasons.append("manifest_canonical_policy_unavailable")
    policy_fields = ("localPath", "artifactKind", "publicUrl", "linkFrom", "required", "commitRole", "digestAuthority")
    expected_policy = sorted(
        tuple(row.get(field) for field in policy_fields)
        for row in canonical.get("entries") or []
        if isinstance(row, Mapping)
    )
    observed_policy = sorted(
        tuple(row.get(field) for field in policy_fields)
        for row in entries
        if isinstance(row, Mapping)
    )
    if observed_policy != expected_policy:
        reasons.append("manifest_entry_policy_mismatch")
    if not re.fullmatch(r"[0-9a-f]{40}", str(manifest.get("sourceBaseline") or "")):
        reasons.append("manifest_source_baseline_invalid")
    if manifest.get("audio") != canonical.get("audio"):
        reasons.append("manifest_audio_projection_mismatch")
    if require_files:
        root = Path(repo_root).resolve()
        expected_self_path = f"data/publish-manifests/{issue_date}.json"
        self_entries = [row for row in entries if isinstance(row, Mapping) and row.get("artifactKind") == "publish_manifest" and row.get("localPath") == expected_self_path]
        if len(self_entries) != 1:
            reasons.append("manifest_self_entry_invalid")
        for row in entries:
            if not isinstance(row, Mapping):
                continue
            relative = str(row.get("localPath") or "")
            try:
                _, artifact = _safe_repo_relative_path(root, relative)
            except ValueError as exc:
                reasons.append(f"{exc}:{relative}")
                continue
            is_canonical_self = row.get("artifactKind") == "publish_manifest" and relative == expected_self_path
            if row.get("digestAuthority") == "self_excluded" and not is_canonical_self:
                reasons.append(f"manifest_self_exclusion_forbidden:{relative}")
            if is_canonical_self:
                if (
                    row.get("digestAuthority") != "self_excluded"
                    or row.get("digest") != "self_excluded"
                    or row.get("required") is not True
                    or row.get("exists") is not True
                ):
                    reasons.append("manifest_self_entry_invalid")
                continue
            if row.get("required") is True and not artifact.is_file():
                reasons.append(f"manifest_required_file_missing:{relative}")
                continue
            if artifact.is_file():
                if row.get("exists") is not True:
                    reasons.append(f"manifest_exists_flag_mismatch:{relative}")
                try:
                    observed_digest = _artifact_digest(artifact, str(row.get("artifactKind") or ""))
                except (OSError, ValueError):
                    reasons.append(f"manifest_artifact_unreadable:{relative}")
                else:
                    if row.get("digest") != observed_digest:
                        reasons.append(f"manifest_artifact_digest_mismatch:{relative}")
    expected_id = hashlib.sha256(_json_bytes(_manifest_identity(manifest))).hexdigest()
    if manifest.get("manifestId") != expected_id:
        reasons.append("manifest_identity_mismatch")
    return {"ok": not reasons, "status": "verified" if not reasons else "blocked", "reasonCodes": reasons}


class _SurfaceHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.manifest_markers: list[str] = []
        self.anchor_hrefs: list[str] = []
        self.media_sources: list[str] = []
        self.visible_text: list[str] = []
        self.reflection_text: list[str] = []
        self._hidden_depth = 0
        self._reflection_depth = 0
        self._stack: list[tuple[str, bool, bool]] = []

    def _start(self, tag: str, attrs: list[tuple[str, str | None]], *, push: bool) -> None:
        values = {key.casefold(): str(value or "") for key, value in attrs}
        lowered = tag.casefold()
        attr_names = {key.casefold() for key, _ in attrs}
        style = re.sub(r"\s+", "", values.get("style", "").casefold())
        hidden_here = (
            lowered in {"script", "style", "template", "noscript"}
            or "hidden" in attr_names
            or values.get("aria-hidden", "").casefold() == "true"
            or "display:none" in style
            or "visibility:hidden" in style
        )
        visible = self._hidden_depth == 0 and not hidden_here
        reflection_here = False
        if hidden_here and push:
            self._hidden_depth += 1
        if visible and lowered == "meta" and values.get("name", "").casefold() == "news-grasp-manifest-id":
            self.manifest_markers.append(values.get("content", ""))
        if visible and lowered == "a" and values.get("href"):
            self.anchor_hrefs.append(values["href"])
        if visible and lowered in {"source", "audio"} and values.get("src"):
            self.media_sources.append(values["src"])
        classes = set(values.get("class", "").split())
        if visible and ("summary-hero__lead" in classes or "data-summary-reflection" in attr_names):
            self._reflection_depth += 1
            reflection_here = True
        if push:
            self._stack.append((lowered, hidden_here, reflection_here))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        void = tag.casefold() in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
        self._start(tag, attrs, push=not void)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs, push=False)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.casefold()
        match_index = next((index for index in range(len(self._stack) - 1, -1, -1) if self._stack[index][0] == lowered), None)
        if match_index is None:
            return
        while len(self._stack) > match_index:
            current, hidden_here, reflection_here = self._stack.pop()
            if hidden_here and self._hidden_depth:
                self._hidden_depth -= 1
            if reflection_here and self._reflection_depth:
                self._reflection_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._hidden_depth and data.strip():
            self.visible_text.append(data)
            if self._reflection_depth:
                self.reflection_text.append(data)


def _normalized_url(value: str, *, base: str) -> str:
    parsed = urlsplit(urljoin(base, value))
    host = (parsed.hostname or "").casefold()
    port = parsed.port
    netloc = host if port is None or (parsed.scheme == "https" and port == 443) else f"{host}:{port}"
    return urlunsplit((parsed.scheme.casefold(), netloc, parsed.path or "/", parsed.query, ""))


def verify_semantic_pages(manifest: Mapping[str, Any], pages: Mapping[str, str]) -> dict[str, Any]:
    """local/public HTMLへ同じsemantic predicateを適用する。"""
    reasons: list[str] = []
    issue_date = str(manifest.get("issueDate") or "")
    marker = str(manifest.get("manifestId") or "")
    home = str(pages.get("home") or "")
    summary = str(pages.get("summary") or "")
    parsed_pages: dict[str, _SurfaceHTMLParser] = {}
    category_entries = [
        row for row in manifest.get("entries") or []
        if isinstance(row, Mapping) and row.get("artifactKind") == "category_html" and row.get("required") is True
    ]
    category_names = [f"category:{category_id}" for category_id in manifest.get("scheduledCategoryIds") or []]
    for name in ("home", "daily", "summary", "deepdive", *category_names):
        body = str(pages.get(name) or "")
        if not body:
            reasons.append(f"public_surface_missing:{name}")
        parser = _SurfaceHTMLParser()
        parser.feed(body)
        parsed_pages[name] = parser
        if marker and parser.manifest_markers != [marker]:
            reasons.append(f"manifest_marker_missing:{name}")
    daily = manifest.get("audio", {}).get("daily", {}) if isinstance(manifest.get("audio"), Mapping) else {}
    daily_url = str(daily.get("publicUrl") or "") if isinstance(daily, Mapping) else ""
    home_parser = parsed_pages.get("home", _SurfaceHTMLParser())
    public_base = "https://hidepon-umg.github.io/News-Grasp/"
    expected_daily = _normalized_url(daily_url, base=public_base) if daily_url else ""
    media = {_normalized_url(value, base=public_base) for value in home_parser.media_sources}
    if not expected_daily or expected_daily not in media:
        reasons.append("daily_audio_href_missing")
    deepdive_href = _normalized_url(f"deepdive/{issue_date}/", base=public_base)
    anchors = {_normalized_url(value, base=public_base) for value in home_parser.anchor_hrefs}
    if deepdive_href not in anchors:
        reasons.append("deepdive_href_missing")
    summary_entry = next((row for row in manifest.get("entries") or [] if isinstance(row, Mapping) and row.get("artifactKind") == "summary_html"), None)
    expected_summary = _normalized_url(str((summary_entry or {}).get("publicUrl") or ""), base=public_base)
    if not expected_summary or expected_summary not in anchors:
        reasons.append("summary_href_missing")
    daily_parser = parsed_pages.get("daily", _SurfaceHTMLParser())
    daily_anchors = {_normalized_url(value, base=public_base) for value in daily_parser.anchor_hrefs}
    expected_categories = {_normalized_url(str(row.get("publicUrl") or ""), base=public_base) for row in category_entries}
    for expected in sorted(expected_categories):
        if not expected or expected not in daily_anchors:
            reasons.append(f"scheduled_category_href_missing:{expected}")
    if len(category_entries) != len(category_names):
        reasons.append("scheduled_category_entry_count_mismatch")
    for category_id in manifest.get("scheduledCategoryIds") or []:
        parser = parsed_pages.get(f"category:{category_id}", _SurfaceHTMLParser())
        if issue_date not in " ".join(parser.visible_text):
            reasons.append(f"category_issue_date_missing:{category_id}")
    summary_parser = parsed_pages.get("summary", _SurfaceHTMLParser())
    if issue_date not in " ".join(summary_parser.visible_text):
        reasons.append("summary_issue_date_missing")
    if len(" ".join(summary_parser.reflection_text).strip()) < 20:
        reasons.append("summary_reflection_missing")
    try:
        status = json.loads(str(pages.get("publish_status") or "{}"))
    except json.JSONDecodeError:
        reasons.append("publish_status_json_invalid")
    else:
        if status.get("date") != issue_date:
            reasons.append("publish_status_date_mismatch")
        if status.get("manifestId") != marker:
            reasons.append("publish_status_manifest_mismatch")
        if str(status.get("result") or "").casefold() not in {"success", "verified", "green"}:
            reasons.append("publish_status_result_red")
    return {"ok": not reasons, "status": "verified" if not reasons else "blocked", "reasonCodes": sorted(set(reasons))}


def verify_claim_binding(
    claim: Mapping[str, Any],
    *,
    issue_date: str,
    run_intent: str,
    allowed_source_urls: Iterable[str],
) -> dict[str, Any]:
    """claim provenanceを現在のmanifest contextへ束縛する。"""
    reasons: list[str] = []
    if claim.get("issueDate") != issue_date:
        reasons.append("claim_issue_date_mismatch")
    if claim.get("runIntent") != run_intent:
        reasons.append("claim_run_intent_mismatch")
    if str(claim.get("sourceUrl") or "") not in set(allowed_source_urls):
        reasons.append("claim_source_url_unbound")
    return {"ok": not reasons, "reasonCodes": reasons}


def _normalized_evidence(value: str) -> str:
    text = value.casefold().replace("％", "%")
    return re.sub(r"[\s\W_]+", "", text, flags=re.UNICODE)


def verify_claim_evidence_value(*, claim: str, evidence: str) -> dict[str, Any]:
    """claim複製とgeneric evidenceを記事価値Redに分類する。"""
    reasons: list[str] = []
    normalized_claim = _normalized_evidence(claim)
    normalized_evidence = _normalized_evidence(evidence)
    if normalized_claim and normalized_claim == normalized_evidence:
        reasons.append("claim_evidence_normalized_equal")
    generic_markers = ("詳細は記事", "詳しくは記事", "記事を参照", "今後が注目", "重要です", "注目されます")
    if not normalized_evidence or any(_normalized_evidence(marker) in normalized_evidence for marker in generic_markers):
        reasons.append("dialogue_value_generic")
    return {"ok": not reasons, "reasonCodes": reasons}


def evaluate_checkout_observation(value: Mapping[str, Any]) -> dict[str, Any]:
    """source/remote parityとclean production境界を別々に評価する。"""
    reasons: list[str] = []
    if value.get("clean") is not True:
        reasons.append("worktree_dirty")
    if value.get("detached") is True and value.get("baselineBound") is not True:
        reasons.append("detached_baseline_unbound")
    if not value.get("head") or value.get("head") != value.get("remoteHead"):
        reasons.append("remote_head_mismatch")
    return {"ok": not reasons, "status": "verified" if not reasons else "blocked", "reasonCodes": reasons}


def aggregate_external_surfaces(surfaces: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """requirednessを保ったtyped status集約を行う。"""
    blocking: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for name, raw in surfaces.items():
        row = dict(raw)
        status = str(row.get("status") or "unverified").casefold()
        if status not in PUBLIC_STATUS_VALUES:
            status = "unverified"
        item = {"surface": name, "status": status, **row}
        if row.get("required") is True and status != "verified":
            blocking.append(item)
        elif row.get("required") is not True and status in {"warning", "deferred", "blocked", "unverified"}:
            warnings.append(item)
    status = "blocked" if blocking else ("verified_with_warnings" if warnings else "verified")
    return {"ok": not blocking, "status": status, "blocking": blocking, "post_publish_issue_list": warnings}


def evaluate_pages_deployment(
    *,
    remote_head: str,
    workflow_runs: Iterable[Mapping[str, Any]],
    manifest_id: str,
    issue_date: str,
) -> dict[str, Any]:
    """workflow conclusionだけでなくremote HEADとpublication contextを照合する。"""
    matched: dict[str, Any] | None = None
    for raw in workflow_runs:
        row = dict(raw)
        if (
            row.get("head_sha") == remote_head
            and str(row.get("path") or "") == ".github/workflows/deploy-pages.yml"
            and str(row.get("event") or "") == "push"
            and str(row.get("head_branch") or "") == "main"
            and str(row.get("status") or "").casefold() == "completed"
            and str(row.get("conclusion") or "").casefold() == "success"
        ):
            matched = row
            break
    reasons: list[str] = []
    if not remote_head:
        reasons.append("pages_remote_head_unverified")
    if matched is None:
        reasons.append("pages_successful_head_missing")
    if not re.fullmatch(r"[0-9a-f]{64}", manifest_id):
        reasons.append("pages_manifest_id_invalid")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", issue_date):
        reasons.append("pages_issue_date_invalid")
    return {"ok": not reasons, "status": "verified" if not reasons else "blocked", "reasonCodes": reasons, "workflowRun": matched, "remoteHead": remote_head, "manifestId": manifest_id, "issueDate": issue_date}


def evaluate_history_promotion(*, daily_manifest_ok: bool, history_candidates: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """repair_publishのGreenだけをpromoteし、当日authorityと分離する。"""
    promoted: list[dict[str, Any]] = []
    quarantine: list[dict[str, Any]] = []
    for raw in history_candidates:
        row = dict(raw)
        (promoted if str(row.get("status") or "").casefold() in {"verified", "green"} else quarantine).append(row)
    return {
        "dailyAuthority": "verified" if daily_manifest_ok else "blocked",
        "promoted": promoted,
        "quarantine": quarantine,
    }


class PublishLeaseStore:
    """issue date＋artifact pathで単一writerをfenceするSQLite lease。"""

    def __init__(
        self,
        state_root: str | Path,
        *,
        test_only_allow_noncanonical: bool = False,
        test_only_skip_runtime_binding: bool = False,
    ) -> None:
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        if not local_app_data and not test_only_allow_noncanonical:
            raise EnvironmentError("localappdata_missing")
        raw_requested = Path(os.path.abspath(os.fspath(state_root)))
        raw_canonical = Path(os.path.abspath(os.fspath(Path(local_app_data) / "News-Grasp" / "direct-mainline"))) if local_app_data else raw_requested
        _reject_external_reparse_chain(raw_requested, reason="publish_lease_state_root_reparse_forbidden")
        _reject_external_reparse_chain(raw_canonical, reason="publish_lease_state_root_reparse_forbidden")
        if not test_only_allow_noncanonical and os.path.normcase(str(raw_requested)) != os.path.normcase(str(raw_canonical)):
            raise ValueError("publish_lease_state_root_not_canonical")
        self.root = raw_requested.resolve(strict=False)
        self.is_canonical = not test_only_allow_noncanonical
        self._skip_runtime_binding = test_only_skip_runtime_binding
        self.root.mkdir(parents=True, exist_ok=True)
        _reject_external_reparse_chain(raw_requested, reason="publish_lease_state_root_reparse_forbidden")
        self.runtime_db_path = self.root / "direct-mainline.sqlite3"
        self.db_path = self.root / "publish-leases.sqlite3" if self._skip_runtime_binding else self.runtime_db_path
        if not self._skip_runtime_binding and not self.runtime_db_path.is_file():
            raise FileNotFoundError("publish_lease_runtime_db_missing")
        if not self._skip_runtime_binding:
            self._runtime_identity()
        _reject_external_reparse_chain(self.db_path, reason="publish_lease_state_root_reparse_forbidden")
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS leases (lease_key TEXT PRIMARY KEY, run_id TEXT NOT NULL, token TEXT NOT NULL, lease_until TEXT NOT NULL, exact_path TEXT NOT NULL)")
            conn.commit()

    def _runtime_identity(self) -> tuple[int, int, int, int]:
        _reject_external_reparse_chain(self.runtime_db_path, reason="publish_lease_runtime_db_reparse_forbidden")
        info = os.lstat(self.runtime_db_path)
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise ValueError("publish_lease_runtime_db_invalid")
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)

    def _pre_connect(self) -> tuple[int, int, int, int] | None:
        _reject_external_reparse_chain(self.root, reason="publish_lease_state_root_reparse_forbidden")
        _reject_external_reparse_chain(self.db_path, reason="publish_lease_state_root_reparse_forbidden")
        return None if self._skip_runtime_binding else self._runtime_identity()

    def _runtime_identity_matches(self, expected: tuple[int, int, int, int] | None) -> bool:
        return expected is None or self._runtime_identity()[:2] == expected[:2]

    @staticmethod
    def lease_key(issue_date: str, artifact_path: str) -> str:
        _validate_issue_date(issue_date)
        raw = str(artifact_path).replace("\\", "/")
        if not raw or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw) or any(part in {"", ".", ".."} for part in raw.split("/")):
            raise ValueError("artifact_path_invalid")
        normalized = PurePosixPath(raw).as_posix().casefold()
        return f"{issue_date}:{normalized}"

    def acquire(self, *, issue_date: str, artifact_paths: Iterable[str], run_id: str, token: str, ttl_seconds: int = 600) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        if not run_id or not token:
            raise ValueError("lease_identity_required")
        if not 300 <= ttl_seconds <= 3600:
            raise ValueError("lease_ttl_out_of_policy")
        until = (now + timedelta(seconds=ttl_seconds)).isoformat()
        keys = [(self.lease_key(issue_date, path), PurePosixPath(path).as_posix()) for path in artifact_paths]
        runtime_identity = self._pre_connect()
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not self._skip_runtime_binding:
                writer = conn.execute("SELECT writer_lease,status,lease_until,issue_date FROM runs WHERE run_id=?", (run_id,)).fetchone()
                if not self._runtime_identity_matches(runtime_identity):
                    conn.rollback()
                    return {"ok": False, "status": "runtime_writer_identity_changed", "exitCode": 4}
                if writer is None or str(writer[0]) != token or str(writer[1]) not in {"active", "executing", "finalizing"} or str(writer[3]) != issue_date or datetime.fromisoformat(str(writer[2])).astimezone(timezone.utc) <= now:
                    conn.rollback()
                    return {"ok": False, "status": "runtime_writer_lease_unbound", "exitCode": 4}
            conflicts = []
            for key, path in keys:
                row = conn.execute("SELECT * FROM leases WHERE lease_key = ?", (key,)).fetchone()
                if row and datetime.fromisoformat(row[3]) > now and (row[1] != run_id or row[2] != token):
                    conflicts.append(path)
            if conflicts:
                conn.rollback()
                return {"ok": False, "status": "writer_lease_conflict", "exitCode": 4, "conflicts": conflicts}
            for key, path in keys:
                conn.execute("INSERT OR REPLACE INTO leases (lease_key, run_id, token, lease_until, exact_path) VALUES (?, ?, ?, ?, ?)", (key, run_id, token, until, path))
            conn.commit()
            if not self._runtime_identity_matches(runtime_identity):
                raise RuntimeError("publish_lease_runtime_db_identity_changed")
        return {"ok": True, "status": "verified", "leaseUntil": until, "paths": [path for _, path in keys]}

    def renew(self, *, issue_date: str, artifact_paths: Iterable[str], run_id: str, token: str, ttl_seconds: int = 600) -> dict[str, Any]:
        if not 300 <= ttl_seconds <= 3600:
            raise ValueError("lease_ttl_out_of_policy")
        now = datetime.now(timezone.utc)
        until = (now + timedelta(seconds=ttl_seconds)).isoformat()
        runtime_identity = self._pre_connect()
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not self._skip_runtime_binding:
                writer = conn.execute("SELECT writer_lease,status,lease_until,issue_date FROM runs WHERE run_id=?", (run_id,)).fetchone()
                if not self._runtime_identity_matches(runtime_identity):
                    conn.rollback()
                    return {"ok": False, "status": "runtime_writer_identity_changed", "exitCode": 4}
                if writer is None or str(writer[0]) != token or str(writer[1]) not in {"active", "executing", "finalizing"} or str(writer[3]) != issue_date or datetime.fromisoformat(str(writer[2])).astimezone(timezone.utc) <= now:
                    conn.rollback()
                    return {"ok": False, "status": "runtime_writer_lease_unbound", "exitCode": 4}
            for path in artifact_paths:
                key = self.lease_key(issue_date, path)
                changed = conn.execute("UPDATE leases SET lease_until = ? WHERE lease_key = ? AND run_id = ? AND token = ?", (until, key, run_id, token)).rowcount
                if changed != 1:
                    conn.rollback()
                    return {"ok": False, "status": "writer_lease_conflict", "exitCode": 4, "path": path}
            conn.commit()
            if not self._runtime_identity_matches(runtime_identity):
                raise RuntimeError("publish_lease_runtime_db_identity_changed")
        return {"ok": True, "status": "verified", "leaseUntil": until}

    def release(self, *, issue_date: str, artifact_paths: Iterable[str], run_id: str, token: str) -> dict[str, Any]:
        runtime_identity = self._pre_connect()
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            released = 0
            for path in artifact_paths:
                released += conn.execute(
                    "DELETE FROM leases WHERE lease_key=? AND run_id=? AND token=?",
                    (self.lease_key(issue_date, path), run_id, token),
                ).rowcount
            conn.commit()
            if not self._runtime_identity_matches(runtime_identity):
                raise RuntimeError("publish_lease_runtime_db_identity_changed")
        return {"ok": True, "status": "verified", "released": released}


def manifest_path(repo_root: str | Path, issue_date: str) -> Path:
    """issue dateごとのtracked manifest pathを返す。"""
    issue = _validate_issue_date(issue_date)
    root = Path(repo_root).resolve()
    return _safe_repo_relative_path(root, f"data/publish-manifests/{issue}.json")[1]


def write_manifest(repo_root: str | Path, manifest: Mapping[str, Any]) -> Path:
    """検証済みmanifestを原子的に保存する。"""
    root = Path(os.path.abspath(os.fspath(repo_root)))
    issue_date = str(manifest.get("issueDate") or "")
    relative = f"data/publish-manifests/{_validate_issue_date(issue_date)}.json"
    guard = _capture_repo_write_guard(root, relative)
    _guarded_repo_write(root, guard, _json_file_bytes(manifest))
    return _safe_repo_relative_path(root, relative)[1]


def _write_json_atomic(target: Path, value: Mapping[str, Any]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(_json_file_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, target)
    finally:
        Path(temp_name).unlink(missing_ok=True)


def _write_text_atomic(target: Path, text: str) -> None:
    handle, temp_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, target)
    finally:
        Path(temp_name).unlink(missing_ok=True)


def _write_bytes_atomic(target: Path, value: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, target)
    finally:
        Path(temp_name).unlink(missing_ok=True)


def bind_existing_distribution_receipts(
    *,
    repo_root: str | Path,
    issue_date: str,
    run_id: str,
    run_intent: str,
    daily_upload_state: str | Path,
    deepdive_upload_state: str | Path,
    lease_store: PublishLeaseStore,
    writer_lease: str,
) -> dict[str, Any]:
    """既公開YouTube/playlist receiptを再送せずclean runtimeへ束縛する。"""

    root = Path(os.path.abspath(os.fspath(repo_root)))
    write_set = [
        "build/youtube-podcast/uploads.json",
        "build/youtube-podcast-deepdive/uploads.json",
        f"build/distribution/{issue_date}/playlist.json",
        f"build/distribution/{issue_date}/binding.json",
    ]
    bound: dict[str, dict[str, Any]] = {}
    source_receipts: dict[str, str] = {}
    for kind, source_value in (
        ("daily", Path(daily_upload_state)),
        ("deepdive", Path(deepdive_upload_state)),
    ):
        try:
            raw, source_sha = _read_external_json_receipt(source_value)
        except (OSError, ValueError) as exc:
            raise ValueError(f"youtube_{kind}_state_invalid:{exc}") from exc
        row = raw.get(issue_date) if isinstance(raw, Mapping) else None
        if not isinstance(row, Mapping):
            raise ValueError(f"youtube_{kind}_receipt_missing")
        if str(row.get("status") or "") != "public":
            raise ValueError(f"youtube_{kind}_not_public")
        for field in ("videoId", "playlistId", "playlistItemId"):
            if not str(row.get(field) or ""):
                raise ValueError(f"youtube_{kind}_{field}_missing")
        bound[kind] = dict(row)
        source_receipts[kind] = source_sha
    playlist = {
        "schemaVersion": "NEWS_GRASP_PLAYLIST_BINDING_V2",
        "issueDate": issue_date,
        "runId": run_id,
        "runIntent": run_intent,
        "status": "verified",
        "daily": {
            key: bound["daily"][key]
            for key in ("videoId", "playlistId", "playlistItemId")
        },
        "deepdive": {
            key: bound["deepdive"][key]
            for key in ("videoId", "playlistId", "playlistItemId")
        },
        "sourceReceipts": {
            "dailySha256": source_receipts["daily"],
            "deepdiveSha256": source_receipts["deepdive"],
        },
    }
    playlist["receiptSha256"] = hashlib.sha256(_json_bytes(playlist)).hexdigest()
    daily_target = {issue_date: bound["daily"]}
    deepdive_target = {issue_date: bound["deepdive"]}
    component_sources = {
        "distributionSha256": f"data/distribution/{issue_date}.json",
        "dailyAudioProjectionSha256": "build/tts/daily/latest_audio.json",
        "deepdiveAudioProjectionSha256": "build/tts/deepdive/latest_audio.json",
        "notificationStateSha256": f"build/notification/{issue_date}.json",
    }
    component_identities: dict[str, str] = {}
    for field, relative in component_sources.items():
        source_path = _safe_repo_relative_path(root, relative)[1]
        component_identities[field] = hashlib.sha256(
            _read_regular_no_follow(source_path, max_bytes=1_048_576)
        ).hexdigest()
    distribution_binding = {
        "schemaVersion": "NEWS_GRASP_DISTRIBUTION_BINDING_V2",
        "issueDate": issue_date,
        "runId": run_id,
        "runIntent": run_intent,
        "status": "verified",
        **component_identities,
        "youtubeDailyStateSha256": hashlib.sha256(_json_file_bytes(daily_target)).hexdigest(),
        "youtubeDeepdiveStateSha256": hashlib.sha256(_json_file_bytes(deepdive_target)).hexdigest(),
        "playlistBindingStateSha256": hashlib.sha256(_json_file_bytes(playlist)).hexdigest(),
        "playlistReceiptSha256": playlist["receiptSha256"],
    }
    distribution_binding["receiptSha256"] = hashlib.sha256(_json_bytes(distribution_binding)).hexdigest()
    target_values: dict[str, dict[str, Any]] = {
        write_set[0]: daily_target,
        write_set[1]: deepdive_target,
        write_set[2]: playlist,
        write_set[3]: distribution_binding,
    }
    guards = {relative: _capture_repo_write_guard(root, relative) for relative in target_values}
    lease = lease_store.acquire(issue_date=issue_date, artifact_paths=write_set, run_id=run_id, token=writer_lease, ttl_seconds=3600)
    if lease.get("ok") is not True:
        raise PermissionError("publish_exact_write_set_lease_conflict")
    try:
        owned: dict[str, str] = {}
        for relative, value in target_values.items():
            renewed = lease_store.renew(
                issue_date=issue_date,
                artifact_paths=write_set,
                run_id=run_id,
                token=writer_lease,
                ttl_seconds=3600,
            )
            if renewed.get("ok") is not True:
                raise PermissionError("publish_exact_write_set_lease_renewal_conflict")
            owned[relative] = _guarded_repo_write(root, guards[relative], _json_file_bytes(value))
        for relative, expected in target_values.items():
            actual, _ = _read_external_json_receipt(_safe_repo_relative_path(root, relative)[1])
            if actual != expected:
                raise RuntimeError("distribution_target_postcondition_red")
    except Exception as exc:
        rollback_failures: list[str] = []
        for relative, owned_sha256 in owned.items():
            try:
                _guarded_repo_rollback(root, guards[relative], owned_sha256=owned_sha256)
            except (OSError, ValueError, RuntimeError) as rollback_exc:
                rollback_failures.append(f"{relative}:{rollback_exc}")
        if rollback_failures:
            raise RuntimeError(f"distribution_binding_rollback_red:{'|'.join(rollback_failures)}") from exc
        raise
    playlist_path = _safe_repo_relative_path(root, write_set[2])[1]
    distribution_binding_path = _safe_repo_relative_path(root, write_set[3])[1]
    return {
        "ok": True,
        "status": "verified",
        "dailyVideoId": bound["daily"]["videoId"],
        "deepdiveVideoId": bound["deepdive"]["videoId"],
        "playlistPath": str(playlist_path),
        "distributionBindingPath": str(distribution_binding_path),
        "distributionBindingReceiptSha256": distribution_binding["receiptSha256"],
    }


def load_manifest(repo_root: str | Path, issue_date: str) -> dict[str, Any]:
    """canonical manifestをobjectとして読む。"""
    root = Path(os.path.abspath(os.fspath(repo_root)))
    relative = f"data/publish-manifests/{_validate_issue_date(issue_date)}.json"
    guard = _capture_repo_write_guard(root, relative)
    target = _assert_repo_write_guard(root, guard)
    raw = _read_regular_no_follow(target, max_bytes=2 * 1024 * 1024)
    _assert_repo_write_guard(root, guard)
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("manifest_json_invalid") from exc
    if not isinstance(value, dict):
        raise ValueError("manifest_not_object")
    return value


def materialize_manifest_markers(
    repo_root: str | Path,
    manifest: Mapping[str, Any],
    *,
    lease_store: PublishLeaseStore,
    writer_lease: str,
    lease_ttl_seconds: int = 3600,
    test_only_allow_noncanonical_lease_store: bool = False,
) -> dict[str, Any]:
    """allowlist済みmetaとpublish-status markerを生成済みHTMLへ付与する。"""
    root = Path(os.path.abspath(os.fspath(repo_root)))
    _validate_issue_date(str(manifest.get("issueDate") or ""))
    marker = str(manifest.get("manifestId") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", marker):
        raise ValueError("manifest_id_invalid")
    if not lease_store.is_canonical and not test_only_allow_noncanonical_lease_store:
        raise ValueError("publish_lease_store_not_canonical")
    preflight = verify_manifest(manifest, repo_root=root, require_files=True)
    if preflight.get("ok") is not True:
        raise ValueError(f"manifest_preflight_red:{','.join(preflight.get('reasonCodes') or [])}")
    lease = lease_store.acquire(
        issue_date=str(manifest["issueDate"]),
        artifact_paths=list(manifest.get("exactWriteSet") or []),
        run_id=str(manifest.get("runId") or ""),
        token=writer_lease,
        ttl_seconds=lease_ttl_seconds,
    )
    if lease.get("ok") is not True:
        raise PermissionError("publish_exact_write_set_lease_conflict")
    meta = f'<meta name="news-grasp-manifest-id" content="{marker}">'
    changed: list[str] = []
    validated_paths: dict[str, Path] = {}
    for row in manifest.get("entries") or []:
        if isinstance(row, Mapping):
            relative = str(row.get("localPath") or "")
            validated_paths[relative] = _safe_repo_relative_path(root, relative)[1]
    status_path = validated_paths.get("docs/publish-status.json") or _safe_repo_relative_path(root, "docs/publish-status.json")[1]
    manifest_target = manifest_path(root, str(manifest.get("issueDate") or ""))
    mutation_targets = {
        path
        for row in manifest.get("entries") or []
        if isinstance(row, Mapping) and row.get("artifactKind") in {"public_home", "issue_index", "summary_html", "deepdive_html", "category_html"}
        for path in [validated_paths[str(row.get("localPath") or "")]]
    } | {status_path, manifest_target}
    relative_by_path = {path: path.relative_to(root).as_posix() for path in mutation_targets}
    guards = {path: _capture_repo_write_guard(root, relative) for path, relative in relative_by_path.items()}
    owned: dict[Path, str] = {}
    try:
        for row in manifest.get("entries") or []:
            if not isinstance(row, Mapping) or row.get("artifactKind") not in {"public_home", "issue_index", "summary_html", "deepdive_html", "category_html"}:
                continue
            path = validated_paths[str(row.get("localPath") or "")]
            renewal = lease_store.renew(issue_date=str(manifest["issueDate"]), artifact_paths=list(manifest.get("exactWriteSet") or []), run_id=str(manifest.get("runId") or ""), token=writer_lease, ttl_seconds=lease_ttl_seconds)
            if renewal.get("ok") is not True:
                raise PermissionError("publish_exact_write_set_lease_conflict")
            text = _read_regular_no_follow(path).decode("utf-8-sig")
            existing, head_end = _manifest_meta_layout(text, require_head=True)
            if existing is not None:
                start, end = existing
                line_break_length = 2 if text.startswith("\r\n", end) else 1
                text = text[:start - 2] + text[end + line_break_length:]
                head_end = int(head_end) - (end + line_break_length - (start - 2))
            if head_end is None:
                raise ValueError(f"html_head_missing:{row.get('localPath')}")
            text = text[:head_end] + f"  {meta}\n" + text[head_end:]
            owned[path] = _guarded_repo_write(root, guards[path], text.encode("utf-8"))
            changed.append(str(row.get("localPath")))
        renewal = lease_store.renew(issue_date=str(manifest["issueDate"]), artifact_paths=list(manifest.get("exactWriteSet") or []), run_id=str(manifest.get("runId") or ""), token=writer_lease, ttl_seconds=lease_ttl_seconds)
        if renewal.get("ok") is not True:
            raise PermissionError("publish_exact_write_set_lease_conflict")
        status = json.loads(_read_regular_no_follow(status_path).decode("utf-8-sig")) if status_path.is_file() else {}
        if not isinstance(status, dict):
            status = {}
        status["manifestId"] = marker
        status["runId"] = manifest.get("runId")
        status["runIntent"] = manifest.get("runIntent")
        owned[status_path] = _guarded_repo_write(root, guards[status_path], _json_file_bytes(status))
        renewal = lease_store.renew(issue_date=str(manifest["issueDate"]), artifact_paths=list(manifest.get("exactWriteSet") or []), run_id=str(manifest.get("runId") or ""), token=writer_lease, ttl_seconds=lease_ttl_seconds)
        if renewal.get("ok") is not True:
            raise PermissionError("publish_exact_write_set_lease_conflict")
        owned[manifest_target] = _guarded_repo_write(root, guards[manifest_target], _json_file_bytes(manifest))
        postflight = verify_manifest(manifest, repo_root=root, require_files=True)
        if postflight.get("ok") is not True:
            raise ValueError(f"manifest_postflight_red:{','.join(postflight.get('reasonCodes') or [])}")
    except Exception as exc:
        rollback_failures: list[str] = []
        for path, owned_sha256 in owned.items():
            try:
                _guarded_repo_rollback(root, guards[path], owned_sha256=owned_sha256)
            except (OSError, ValueError, RuntimeError) as rollback_exc:
                rollback_failures.append(f"{relative_by_path[path]}:{rollback_exc}")
        if rollback_failures:
            raise RuntimeError(f"manifest_materialization_manual_recovery_required:{'|'.join(rollback_failures)}") from exc
        raise
    return {"ok": True, "changed": changed, "manifestPath": str(manifest_target), "manifestId": marker, "lease": lease}


def _main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    build = sub.add_parser("build")
    build.add_argument("--repo-root", type=Path, default=Path.cwd())
    build.add_argument("--issue-date", required=True)
    build.add_argument("--run-id", required=True)
    build.add_argument("--run-intent", default=RUN_INTENT)
    build.add_argument("--source-baseline", required=True)
    build.add_argument("--materialize", action="store_true")
    build.add_argument("--state-root", type=Path, required=True)
    build.add_argument("--writer-lease", required=True)
    build.add_argument("--lease-ttl", type=int, default=3600)
    verify = sub.add_parser("verify")
    verify.add_argument("--repo-root", type=Path, default=Path.cwd())
    verify.add_argument("--manifest", type=Path, required=True)
    bind = sub.add_parser("bind-existing-distribution")
    bind.add_argument("--repo-root", type=Path, default=Path.cwd())
    bind.add_argument("--issue-date", required=True)
    bind.add_argument("--run-id", required=True)
    bind.add_argument("--run-intent", default=RUN_INTENT)
    bind.add_argument("--daily-upload-state", type=Path, required=True)
    bind.add_argument("--deepdive-upload-state", type=Path, required=True)
    bind.add_argument("--state-root", type=Path, required=True)
    bind.add_argument("--writer-lease", required=True)
    args = parser.parse_args()
    if args.cmd == "build":
        manifest = build_publish_manifest(repo_root=args.repo_root, issue_date=args.issue_date, run_id=args.run_id, run_intent=args.run_intent, source_baseline=args.source_baseline)
        lease_store = PublishLeaseStore(args.state_root)
        if args.materialize:
            result = materialize_manifest_markers(args.repo_root, manifest, lease_store=lease_store, writer_lease=args.writer_lease, lease_ttl_seconds=args.lease_ttl)
        else:
            lease = lease_store.acquire(issue_date=args.issue_date, artifact_paths=manifest["exactWriteSet"], run_id=args.run_id, token=args.writer_lease, ttl_seconds=args.lease_ttl)
            if lease.get("ok") is not True:
                raise PermissionError("publish_exact_write_set_lease_conflict")
            result = {"ok": True, "manifest": manifest, "manifestPath": str(write_manifest(args.repo_root, manifest)), "lease": lease}
    elif args.cmd == "verify":
        value = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
        result = verify_manifest(value, repo_root=args.repo_root, require_files=True)
    else:
        result = bind_existing_distribution_receipts(
            repo_root=args.repo_root,
            issue_date=args.issue_date,
            run_id=args.run_id,
            run_intent=args.run_intent,
            daily_upload_state=args.daily_upload_state,
            deepdive_upload_state=args.deepdive_upload_state,
            lease_store=PublishLeaseStore(args.state_root),
            writer_lease=args.writer_lease,
        )
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(_main())
