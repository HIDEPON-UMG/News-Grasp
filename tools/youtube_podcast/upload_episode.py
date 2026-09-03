from __future__ import annotations

import argparse
from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Protocol


REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD_DIR = REPO_ROOT / "build" / "youtube-podcast"
DEEPDIVE_BUILD_DIR = REPO_ROOT / "build" / "youtube-podcast-deepdive"
LOCAL_STATE_DIR = Path(os.environ.get("NEWS_GRASP_LOCAL_STATE_DIR") or (Path.home() / ".news-grasp"))
SECRETS_PATH = LOCAL_STATE_DIR / "youtube-podcast-secrets.json"
STATE_PATH = LOCAL_STATE_DIR / "youtube-podcast-state.json"
DEFAULT_PLAYLIST_TITLE = "News-Grasp"
DEFAULT_PLAYLIST_DESCRIPTION = "News-Grasp Daily News Briefing の公開ポッドキャストアーカイブ。"
DEEPDIVE_PLAYLIST_TITLE = "News-Grasp DeepDive"
DEEPDIVE_PLAYLIST_DESCRIPTION = "News-Grasp DeepDive 解説対談の公開ポッドキャストアーカイブ。"
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_URL_RE = re.compile(r"https?://[^\s)>\"]+")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
OPERATION_MARKER_PREFIX = "NEWS_GRASP_OPERATION_V1:"


class PodcastClient(Protocol):
    def ensure_playlist(self, kind: str = "daily") -> str: ...
    def upload_video(self, mp4_path: Path, metadata: dict[str, Any], *, privacy_status: str) -> str: ...
    def find_videos_by_operation_marker(self, operation_marker: str) -> list[dict[str, Any]]: ...
    def get_video_privacy_status(self, *, video_id: str) -> str: ...
    def update_video_privacy(self, *, video_id: str, privacy_status: str) -> dict[str, Any]: ...
    def add_video_to_playlist(self, *, video_id: str, playlist_id: str) -> str: ...
    def list_playlist_items(self, *, playlist_id: str) -> list[dict[str, Any]]: ...
    def delete_playlist_item(self, playlist_item_id: str) -> dict[str, Any]: ...


class YouTubeOperationError(RuntimeError):
    """YouTube側の状態を安全に確定できないときの typed Red。"""


class YouTubeOperationMarkerError(YouTubeOperationError):
    """operation marker の欠落・形式不整合・identity不一致。"""


class YouTubeOperationMarkerDuplicateError(YouTubeOperationError):
    """同じmarkerを持つprovider動画が複数見つかった。"""


class YouTubeOperationUnconfirmedError(YouTubeOperationError):
    """provider state が0件または必要なfresh状態を確定できない。"""


class YouTubePlaylistMembershipDuplicateError(YouTubeOperationError):
    """同じ動画のplaylist membershipが複数ある。"""


def _warn(message: str) -> None:
    print(f"[youtube-podcast][WARN] {message}", file=sys.stderr)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    if path.exists():
        path.with_suffix(path.suffix + ".bak").write_text(path.read_text(encoding="utf-8-sig"), encoding="utf-8")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    tmp.replace(path)


def _build_dir(kind: str = "daily") -> Path:
    if kind == "daily":
        return BUILD_DIR
    if kind == "deepdive":
        return DEEPDIVE_BUILD_DIR
    raise ValueError(f"invalid podcast kind: {kind}")


def _uploads_json(kind: str = "daily") -> Path:
    return _build_dir(kind) / "uploads.json"


def _state_path(kind: str = "daily") -> Path:
    suffix = "" if kind == "daily" else "-deepdive"
    return LOCAL_STATE_DIR / f"youtube-podcast{suffix}-state.json"


def _clean_script_text(text: str) -> str:
    lines: list[str] = []
    in_frontmatter = False
    in_html_comment = False
    for raw in text.splitlines():
        line = raw.strip()
        if line == "---":
            in_frontmatter = not in_frontmatter
            continue
        if line.startswith("<!--"):
            in_html_comment = True
            if "-->" in line:
                in_html_comment = False
            continue
        if in_html_comment:
            if "-->" in line:
                in_html_comment = False
            continue
        if in_frontmatter or not line or line.startswith("#") or line.startswith("```"):
            continue
        if line.startswith("type:") or line.startswith("date:"):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _deepdive_frontmatter(day: str) -> dict[str, str]:
    md_path = REPO_ROOT / "digest" / "DeepDive" / f"{day}-DeepDive.md"
    if not md_path.exists():
        return {}
    text = md_path.read_text(encoding="utf-8-sig", errors="replace")
    if not text.startswith("---"):
        return {}
    _, _, rest = text.partition("---")
    fm_text, _, _body = rest.partition("---")
    data: dict[str, str] = {}
    for raw in fm_text.splitlines():
        if ":" not in raw:
            continue
        key, _, value = raw.partition(":")
        value = value.strip().strip('"').strip("'")
        if value:
            data[key.strip()] = value
    return data


def build_metadata(day: str, *, kind: str = "daily") -> dict[str, Any]:
    if kind == "deepdive":
        fm = _deepdive_frontmatter(day)
        title = str(fm.get("title") or "News-Grasp DeepDive")
        theme = str(fm.get("theme") or "News-Grasp DeepDive の解説対談です。")
        description_parts = [
            f"News-Grasp DeepDive Dialogue {day}",
            "",
            title,
            "",
            theme,
            "",
            "この動画は News-Grasp DeepDive の解説対談音声を YouTube Podcast 用に静止画動画化したものです。",
            "若手社員の質問に先輩社員が答える形で、DeepDive の背景・用語・論点を会話で振り返ります。",
            "",
            "Generated by News-Grasp.",
        ]
        return {
            "title": f"News-Grasp DeepDive Dialogue {day}",
            "description": "\n".join(description_parts),
            "tags": ["News-Grasp", "DeepDive", "解説対談", "Podcast", "ビジネスニュース"],
            "categoryId": "25",
        }
    if kind != "daily":
        raise ValueError(f"invalid podcast kind: {kind}")
    script = REPO_ROOT / "digest" / "Summary" / f"{day}-audio-script.md"
    body = _clean_script_text(script.read_text(encoding="utf-8-sig", errors="replace")) if script.exists() else ""
    links = sorted(set(_URL_RE.findall(body)))
    overview = body.splitlines()[0] if body else "News-Grasp の日次ニュース朗読です。"
    if len(overview) > 240:
        overview = overview[:237] + "..."
    description_parts = [
        f"News-Grasp Daily News Briefing {day}",
        "",
        overview,
        "",
        "この動画は News-Grasp の日次 TTS 音声を YouTube Podcast 用に静止画動画化したものです。",
    ]
    if links:
        description_parts.extend(["", "出典・参照リンク:", *links[:20]])
    description_parts.extend(["", "Generated by News-Grasp."])
    return {
        "title": f"News-Grasp Daily News Briefing {day}",
        "description": "\n".join(description_parts),
        "tags": ["News-Grasp", "ニュース", "Daily News", "Podcast", "AIニュース"],
        "categoryId": "25",
    }


def _build_metadata(day: str, kind: str) -> dict[str, Any]:
    try:
        return build_metadata(day, kind=kind)
    except TypeError:
        # 既存テストや簡易 monkeypatch は build_metadata(day) 形式。
        return build_metadata(day)


def _load_uploads(kind: str = "daily") -> dict[str, Any]:
    return _load_json(_uploads_json(kind))


def _write_uploads(payload: dict[str, Any], kind: str = "daily") -> None:
    _write_json(_uploads_json(kind), payload)


def _already_public(day: str, mp4_hash: str, kind: str = "daily") -> dict[str, Any] | None:
    row = _load_uploads(kind).get(day)
    if not isinstance(row, dict):
        return None
    if row.get("status") == "public" and row.get("mp4_sha256") == mp4_hash:
        return row
    return None


def _ensure_playlist(client: PodcastClient, kind: str) -> str:
    try:
        return client.ensure_playlist(kind)
    except TypeError:
        # 既存テストや簡易FakeClientは引数なし ensure_playlist を実装している。
        return client.ensure_playlist()


def _canonical_operation_identity(
    *,
    run_id: str,
    bundle_id: str,
    operation_id: str,
    payload_identity: str,
) -> dict[str, str]:
    run = str(run_id or "").strip()
    bundle = str(bundle_id or "").strip()
    operation = str(operation_id or "").strip()
    payload = str(payload_identity or "").strip().casefold()
    if not run or not bundle or not operation:
        raise YouTubeOperationMarkerError("youtube_operation_marker_identity_required")
    if not _SHA256_RE.fullmatch(payload):
        raise YouTubeOperationMarkerError("youtube_operation_marker_payload_identity_invalid")
    return {
        "runId": run,
        "bundleId": bundle,
        "operationId": operation,
        "payloadIdentity": payload,
    }


def build_operation_marker(
    *,
    run_id: str,
    bundle_id: str,
    operation_id: str,
    payload_identity: str,
) -> str:
    """sealed provider identityから再現可能なYouTube markerを生成する。"""

    identity = _canonical_operation_identity(
        run_id=run_id,
        bundle_id=bundle_id,
        operation_id=operation_id,
        payload_identity=payload_identity,
    )
    canonical = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return OPERATION_MARKER_PREFIX + hashlib.sha256(canonical).hexdigest()


def _production_operation_identity(
    *,
    run_id: str | None,
    bundle_id: str | None,
    operation_id: str | None,
    payload_identity: str | None,
    operation_marker: str | None,
    marker: str | None = None,
    default_operation_id: str,
) -> dict[str, str] | None:
    """legacy callerとproduction callerを明確に分離する。"""

    values = (run_id, bundle_id, operation_id, payload_identity, operation_marker, marker)
    if all(value is None for value in values):
        return None
    if operation_marker is not None and marker is not None and operation_marker != marker:
        raise YouTubeOperationMarkerError("youtube_operation_marker_alias_conflict")
    supplied_marker = operation_marker if operation_marker is not None else marker
    if supplied_marker is None:
        raise YouTubeOperationMarkerError("youtube_operation_marker_required")
    identity = _canonical_operation_identity(
        run_id=str(run_id or ""),
        bundle_id=str(bundle_id or ""),
        operation_id=str(operation_id or default_operation_id),
        payload_identity=str(payload_identity or ""),
    )
    expected = build_operation_marker(
        run_id=identity["runId"],
        bundle_id=identity["bundleId"],
        operation_id=identity["operationId"],
        payload_identity=identity["payloadIdentity"],
    )
    if str(supplied_marker) != expected:
        raise YouTubeOperationMarkerError("youtube_operation_marker_identity_mismatch")
    identity["operationMarker"] = expected
    return identity


def _append_operation_marker(metadata: Mapping[str, Any], operation_marker: str) -> dict[str, Any]:
    result = dict(metadata)
    description = str(result.get("description") or "")
    count = description.count(operation_marker)
    if count > 1:
        raise YouTubeOperationMarkerDuplicateError("youtube_operation_marker_embedded_duplicate")
    if count == 0:
        description = f"{description.rstrip()}\n{operation_marker}" if description.strip() else operation_marker
    result["description"] = description
    return result


def _upload_history_key(
    *,
    run_id: str,
    bundle_id: str,
    kind: str,
    payload_identity: str,
) -> str:
    identity = {
        "runId": str(run_id),
        "bundleId": str(bundle_id),
        "kind": str(kind),
        "payloadIdentity": str(payload_identity).casefold(),
    }
    canonical = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _row_identity_matches(
    row: Mapping[str, Any],
    identity: Mapping[str, str],
    *,
    kind: str | None = None,
) -> bool:
    # history keyはrun/bundle/kind/payloadのrelease identityであり、prepareと
    # finalizeではoperationId/markerが異なり得る。provider upload時のmarkerは
    # uploadOperationMarkerへ保持し、同じ履歴rowのphase遷移を許可する。
    row_kind = row.get("kind")
    row_payload = str(row.get("mp4_sha256") or "").casefold()
    return (
        (kind is None or row_kind in (None, "", kind))
        and str(row.get("runId") or row.get("run_id") or "") == identity["runId"]
        and str(row.get("bundleId") or row.get("bundle_id") or "") == identity["bundleId"]
        and str(row.get("payloadIdentity") or row.get("payload_identity") or "").casefold()
        == identity["payloadIdentity"]
        and (not row_payload or row_payload == identity["payloadIdentity"])
    )


def _row_with_identity(row: Mapping[str, Any], identity: Mapping[str, str]) -> dict[str, Any]:
    result = dict(row)
    upload_marker = str(
        result.get("uploadOperationMarker")
        or result.get("upload_operation_marker")
        or result.get("operationMarker")
        or result.get("operation_marker")
        or identity["operationMarker"]
    )
    result.update(
        {
            "operationMarker": identity["operationMarker"],
            "operationId": identity["operationId"],
            "runId": identity["runId"],
            "bundleId": identity["bundleId"],
            "payloadIdentity": identity["payloadIdentity"],
            "uploadOperationMarker": upload_marker,
        }
    )
    return result


def _preserve_legacy_v1_row(
    uploads: dict[str, Any],
    *,
    day: str,
    kind: str,
    row: Mapping[str, Any],
) -> None:
    """V1 day rowを削除せず、V2履歴へ限定的に退避する。"""

    video_id = str(row.get("videoId") or "")
    if not video_id:
        return
    history = uploads.get("uploadHistoryV2")
    history_value = dict(history) if isinstance(history, Mapping) else {}
    payload = str(row.get("payloadIdentity") or row.get("mp4_sha256") or "").casefold()
    legacy_identity = {
        "legacyV1": True,
        "day": day,
        "kind": kind,
        "videoId": video_id,
        "payloadIdentity": payload,
    }
    key = hashlib.sha256(
        json.dumps(legacy_identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if key not in history_value:
        preserved = dict(row)
        preserved["legacyV1"] = True
        preserved["kind"] = kind
        history_value[key] = preserved
        uploads["uploadHistoryV2"] = history_value


def _history_row(
    uploads: Mapping[str, Any],
    *,
    day: str,
    kind: str,
    identity: Mapping[str, str],
) -> dict[str, Any] | None:
    key = _upload_history_key(
        run_id=identity["runId"],
        bundle_id=identity["bundleId"],
        kind=kind,
        payload_identity=identity["payloadIdentity"],
    )
    history = uploads.get("uploadHistoryV2")
    if isinstance(history, Mapping) and key in history:
        candidate = history[key]
        if not isinstance(candidate, Mapping) or not _row_identity_matches(candidate, identity, kind=kind):
            raise YouTubeOperationError("youtube_upload_history_composite_conflict")
        return dict(candidate)
    active_keys = uploads.get("activeUploadKeys")
    active_key = active_keys.get(day) if isinstance(active_keys, Mapping) else None
    if active_key and str(active_key) != key:
        # 同日別releaseがactiveでも、当該identityの履歴は独立して読む。
        return None
    candidate = uploads.get(day)
    if isinstance(candidate, Mapping) and _row_identity_matches(candidate, identity, kind=kind):
        return dict(candidate)
    return None


def _persist_upload_row(
    uploads: dict[str, Any],
    *,
    day: str,
    kind: str,
    row: Mapping[str, Any],
    identity: Mapping[str, str] | None,
    activate_if_new: bool = True,
) -> dict[str, Any]:
    if identity is None:
        result = dict(row)
        uploads[day] = result
        return result

    old_active = uploads.get(day)
    if (
        isinstance(old_active, Mapping)
        and old_active.get("videoId")
        and not any(
            str(old_active.get(field) or "").strip()
            for field in ("operationMarker", "operation_marker", "runId", "run_id", "bundleId", "bundle_id")
        )
    ):
        _preserve_legacy_v1_row(uploads, day=day, kind=kind, row=old_active)
    key = _upload_history_key(
        run_id=identity["runId"],
        bundle_id=identity["bundleId"],
        kind=kind,
        payload_identity=identity["payloadIdentity"],
    )
    history = uploads.get("uploadHistoryV2")
    history_value = dict(history) if isinstance(history, Mapping) else {}
    existing = history_value.get(key)
    if key in history_value and not isinstance(existing, Mapping):
        raise YouTubeOperationError("youtube_upload_history_composite_conflict")
    if isinstance(existing, Mapping):
        existing_payload = str(existing.get("payloadIdentity") or existing.get("payload_identity") or "").casefold()
        if existing_payload and existing_payload != identity["payloadIdentity"]:
            raise YouTubeOperationError("youtube_upload_history_composite_conflict")
        if not _row_identity_matches(existing, identity, kind=kind):
            raise YouTubeOperationError("youtube_upload_history_identity_conflict")
        result = dict(existing)
        result.update(dict(row))
    else:
        result = dict(row)
    result = _row_with_identity(result, identity)
    result["kind"] = kind
    history_value[key] = result
    active_keys = uploads.get("activeUploadKeys")
    active_keys_value = dict(active_keys) if isinstance(active_keys, Mapping) else {}
    previous_active_key = str(active_keys_value.get(day) or "")
    # 旧historyの再照合で、同日新releaseのactive pointerを過去へ戻さない。
    # 未登録keyまたは同一keyだけがactive projectionを進められる。
    advance_active = (
        not previous_active_key
        or previous_active_key == key
        or (activate_if_new and existing is None)
    )
    if advance_active:
        active_keys_value[day] = key
    uploads["uploadHistoryV2"] = history_value
    uploads["activeUploadKeys"] = active_keys_value
    # 旧releaseの再照合は履歴rowだけを更新し、同日active projectionを
    # 過去へ巻き戻さない。新releaseまたは同一activeだけがday rowを進める。
    if advance_active:
        uploads[day] = result
    return result


def _persist_finalize_substep(
    uploads: dict[str, Any],
    *,
    day: str,
    kind: str,
    row: Mapping[str, Any],
    identity: Mapping[str, str],
    substep_id: str,
    provider_fields: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """fresh provider観測済みfinalize substepをrelease履歴へ原子的に保存する。"""

    current = dict(row)
    raw_substeps = current.get("providerSubsteps")
    substeps = dict(raw_substeps) if isinstance(raw_substeps, Mapping) else {}
    existing = substeps.get(substep_id)
    if existing is not None and (
        not isinstance(existing, Mapping)
        or str(existing.get("status") or "") != "completed"
    ):
        raise YouTubeOperationError("youtube_finalize_substep_history_conflict")
    record: dict[str, Any] = {"status": "completed"}
    if provider_fields:
        record.update({str(key): value for key, value in provider_fields.items()})
    if isinstance(existing, Mapping) and dict(existing) != record:
        raise YouTubeOperationError("youtube_finalize_substep_identity_conflict")
    substeps[substep_id] = record
    current["providerSubsteps"] = substeps
    persisted = _persist_upload_row(
        uploads,
        day=day,
        kind=kind,
        row=current,
        identity=identity,
        activate_if_new=False,
    )
    _write_uploads(uploads, kind)
    return persisted


def _fresh_marker_candidates(client: PodcastClient, operation_marker: str) -> list[dict[str, Any]]:
    methods = (
        "find_videos_by_operation_marker",
        "find_videos_by_marker",
        "search_videos_by_marker",
        "search_videos",
    )
    for name in methods:
        method = getattr(client, name, None)
        if callable(method):
            try:
                value = method(operation_marker)
            except TypeError:
                try:
                    value = method(marker=operation_marker)
                except TypeError:
                    value = method(operation_marker=operation_marker)
            if isinstance(value, Mapping):
                collection = value.get("candidates") or value.get("items") or value.get("videos")
                value = collection if collection is not None else ([value] if _candidate_video_id(value) else [])
            if not isinstance(value, (list, tuple)):
                raise YouTubeOperationUnconfirmedError("youtube_operation_marker_search_invalid")
            normalized = [
                dict(item) if isinstance(item, Mapping) else {"videoId": str(item)}
                for item in value
                if isinstance(item, Mapping) or str(item or "")
            ]
            filtered: list[dict[str, Any]] = []
            for item in normalized:
                raw_count = item.get("markerCount")
                if raw_count is None:
                    filtered.append(item)
                    continue
                try:
                    if int(raw_count) > 0:
                        filtered.append(item)
                except (TypeError, ValueError):
                    filtered.append(item)
            return filtered
    raise YouTubeOperationUnconfirmedError("youtube_operation_marker_search_unavailable")


def _fresh_video_privacy(client: PodcastClient, video_id: str) -> str:
    methods = ("get_video_privacy_status", "get_video_privacy", "get_video_status")
    for name in methods:
        method = getattr(client, name, None)
        if not callable(method):
            continue
        try:
            value = method(video_id=video_id)
        except TypeError:
            value = method(video_id)
        if isinstance(value, Mapping):
            status = value.get("status") if isinstance(value.get("status"), Mapping) else value
            value = status.get("privacyStatus") if isinstance(status, Mapping) else ""
        privacy = str(value or "").strip().casefold()
        if privacy:
            return privacy
        raise YouTubeOperationUnconfirmedError("youtube_video_privacy_unconfirmed")
    raise YouTubeOperationUnconfirmedError("youtube_video_privacy_read_unavailable")


def _fresh_playlist_membership(
    client: PodcastClient,
    *,
    video_id: str,
    playlist_id: str,
) -> dict[str, Any] | None:
    try:
        items = client.list_playlist_items(playlist_id=playlist_id)
    except TypeError:
        items = client.list_playlist_items(playlist_id)
    matches = [
        dict(item)
        for item in items
        if isinstance(item, Mapping)
        and str(
            item.get("videoId")
            or (
                item.get("contentDetails", {}).get("videoId")
                if isinstance(item.get("contentDetails"), Mapping)
                else ""
            )
            or ""
        )
        == video_id
    ]
    if len(matches) > 1:
        raise YouTubePlaylistMembershipDuplicateError("youtube_playlist_membership_duplicate")
    return matches[0] if matches else None


def _single_marker_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    for candidate in candidates:
        try:
            marker_count = int(candidate.get("markerCount") or 1)
        except (TypeError, ValueError) as exc:
            raise YouTubeOperationMarkerDuplicateError("youtube_operation_marker_duplicate") from exc
        if marker_count != 1:
            raise YouTubeOperationMarkerDuplicateError("youtube_operation_marker_duplicate")
    if len(candidates) > 1:
        raise YouTubeOperationMarkerDuplicateError("youtube_operation_marker_duplicate")
    return candidates[0] if candidates else None


def _candidate_video_id(candidate: Mapping[str, Any]) -> str:
    raw_id = candidate.get("id")
    nested_id = raw_id.get("videoId") if isinstance(raw_id, Mapping) else ""
    return str(
        candidate.get("videoId")
        or candidate.get("video_id")
        or nested_id
        or (raw_id if isinstance(raw_id, str) else "")
        or ""
    )


def _fresh_candidate_for_identity(
    client: PodcastClient,
    *,
    identity: Mapping[str, str],
    existing: Mapping[str, Any] | None,
    kind: str,
) -> dict[str, Any] | None:
    """phase markerを優先し、finalizeではupload markerへ安全にfallbackする。"""

    markers = [identity["operationMarker"]]
    if existing is not None:
        for field in ("uploadOperationMarker", "upload_operation_marker", "operationMarker", "operation_marker"):
            value = str(existing.get(field) or "")
            if value and value not in markers:
                markers.append(value)
    if identity["operationId"].endswith("_finalize"):
        prepare_marker = build_operation_marker(
            run_id=identity["runId"],
            bundle_id=identity["bundleId"],
            operation_id=f"youtube_{kind}_prepare",
            payload_identity=identity["payloadIdentity"],
        )
        if prepare_marker not in markers:
            markers.append(prepare_marker)
    for marker_value in markers:
        candidate = _single_marker_candidate(_fresh_marker_candidates(client, marker_value))
        if candidate is not None:
            return candidate
    return None


class YouTubePodcastClient:
    def __init__(self, service: Any):
        self.service = service

    @classmethod
    def from_local_secrets(cls, secrets_path: Path = SECRETS_PATH) -> "YouTubePodcastClient":
        secrets = _load_json(secrets_path)
        required = ["client_id", "client_secret", "refresh_token"]
        missing = [key for key in required if not secrets.get(key)]
        if missing:
            raise RuntimeError(f"YouTube OAuth secrets missing: {', '.join(missing)} ({secrets_path})")
        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise RuntimeError("google-api-python-client / google-auth が未導入です。依存関係をインストールしてください。") from exc
        credentials = Credentials(
            token=None,
            refresh_token=str(secrets["refresh_token"]),
            token_uri=str(secrets.get("token_uri") or "https://oauth2.googleapis.com/token"),
            client_id=str(secrets["client_id"]),
            client_secret=str(secrets["client_secret"]),
            scopes=[
                "https://www.googleapis.com/auth/youtube",
                "https://www.googleapis.com/auth/youtube.upload",
            ],
        )
        return cls(build("youtube", "v3", credentials=credentials))

    def ensure_playlist(self, kind: str = "daily") -> str:
        state = _load_json(_state_path(kind))
        playlist_id = str(state.get("playlist_id") or "")
        if playlist_id:
            return playlist_id
        title = DEEPDIVE_PLAYLIST_TITLE if kind == "deepdive" else DEFAULT_PLAYLIST_TITLE
        description = DEEPDIVE_PLAYLIST_DESCRIPTION if kind == "deepdive" else DEFAULT_PLAYLIST_DESCRIPTION
        request = self.service.playlists().insert(
            part="snippet,status",
            body={
                "snippet": {
                    "title": title,
                    "description": description,
                    "defaultLanguage": "ja",
                },
                "status": {"privacyStatus": "public"},
            },
        )
        response = request.execute()
        playlist_id = str(response.get("id") or "")
        if not playlist_id:
            raise RuntimeError("YouTube playlist create response did not include id")
        state["playlist_id"] = playlist_id
        _write_json(_state_path(kind), state)
        return playlist_id

    def upload_video(self, mp4_path: Path, metadata: dict[str, Any], *, privacy_status: str) -> str:
        try:
            from googleapiclient.http import MediaFileUpload
        except ImportError as exc:
            raise RuntimeError("google-api-python-client が未導入です。依存関係をインストールしてください。") from exc
        request = self.service.videos().insert(
            part="snippet,status",
            body={
                "snippet": {
                    "title": metadata["title"],
                    "description": metadata["description"],
                    "tags": metadata.get("tags", []),
                    "categoryId": metadata.get("categoryId", "25"),
                    "defaultLanguage": "ja",
                },
                "status": {
                    "privacyStatus": privacy_status,
                    "embeddable": True,
                    "publicStatsViewable": True,
                    "selfDeclaredMadeForKids": False,
                },
            },
            media_body=MediaFileUpload(str(mp4_path), mimetype="video/mp4", resumable=True),
        )
        response = request.execute()
        video_id = str(response.get("id") or "")
        if not video_id:
            raise RuntimeError("YouTube upload response did not include id")
        return video_id

    def find_videos_by_operation_marker(
        self,
        operation_marker: str | None = None,
        *,
        marker: str | None = None,
    ) -> list[dict[str, Any]]:
        """自分の動画をfresh検索し、descriptionにmarkerが一つある候補だけ返す。"""

        if operation_marker is not None and marker is not None and operation_marker != marker:
            raise YouTubeOperationMarkerError("youtube_operation_marker_alias_conflict")
        operation_marker = operation_marker if operation_marker is not None else marker
        if not re.fullmatch(rf"{re.escape(OPERATION_MARKER_PREFIX)}[0-9a-f]{{64}}", str(operation_marker or "")):
            raise YouTubeOperationMarkerError("youtube_operation_marker_invalid")
        search_items: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            kwargs: dict[str, Any] = {
                "part": "id,snippet",
                "forMine": True,
                "q": operation_marker,
                "type": "video",
                "maxResults": 50,
            }
            if page_token:
                kwargs["pageToken"] = page_token
            response = self.service.search().list(**kwargs).execute()
            if not isinstance(response, Mapping):
                raise YouTubeOperationUnconfirmedError("youtube_operation_marker_search_invalid")
            items = response.get("items")
            if isinstance(items, list):
                search_items.extend(item for item in items if isinstance(item, Mapping))
            page_token = str(response.get("nextPageToken") or "") or None
            if not page_token:
                break

        video_ids: list[str] = []
        search_by_id: dict[str, Mapping[str, Any]] = {}
        for item in search_items:
            raw_id = item.get("id")
            video_id = str(raw_id.get("videoId") or "") if isinstance(raw_id, Mapping) else str(raw_id or "")
            if video_id and video_id not in search_by_id:
                video_ids.append(video_id)
                search_by_id[video_id] = item

        detail_by_id: dict[str, Mapping[str, Any]] = {}
        for offset in range(0, len(video_ids), 50):
            video_chunk = video_ids[offset : offset + 50]
            response = self.service.videos().list(
                part="snippet,status",
                id=",".join(video_chunk),
            ).execute()
            if not isinstance(response, Mapping):
                raise YouTubeOperationUnconfirmedError("youtube_operation_marker_video_read_invalid")
            items = response.get("items")
            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, Mapping):
                        continue
                    video_id = str(item.get("id") or "")
                    if video_id:
                        detail_by_id[video_id] = item

        candidates: list[dict[str, Any]] = []
        for video_id in video_ids:
            detail = detail_by_id.get(video_id, search_by_id.get(video_id, {}))
            snippet = detail.get("snippet") if isinstance(detail.get("snippet"), Mapping) else {}
            search_snippet = search_by_id[video_id].get("snippet")
            if not isinstance(search_snippet, Mapping):
                search_snippet = {}
            description = str(snippet.get("description") or search_snippet.get("description") or "")
            marker_count = description.count(operation_marker)
            if marker_count <= 0:
                continue
            status = detail.get("status") if isinstance(detail.get("status"), Mapping) else {}
            candidates.append(
                {
                    "videoId": video_id,
                    "description": description,
                    "markerCount": marker_count,
                    "title": str(snippet.get("title") or search_snippet.get("title") or ""),
                    "privacyStatus": str(status.get("privacyStatus") or ""),
                }
            )
        return candidates

    # API名称の違いを吸収するread-only alias。実処理は上記canonical methodだけが持つ。
    def find_videos_by_marker(
        self,
        operation_marker: str | None = None,
        *,
        marker: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.find_videos_by_operation_marker(operation_marker, marker=marker)

    def search_videos_by_marker(
        self,
        operation_marker: str | None = None,
        *,
        marker: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.find_videos_by_operation_marker(operation_marker, marker=marker)

    def search_videos(
        self,
        operation_marker: str | None = None,
        *,
        marker: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.find_videos_by_operation_marker(operation_marker, marker=marker)

    def get_video_privacy_status(self, *, video_id: str) -> str:
        """videoIdのprivacyStatusをproviderからfresh取得する。"""

        response = self.service.videos().list(part="status", id=video_id).execute()
        if not isinstance(response, Mapping):
            raise YouTubeOperationUnconfirmedError("youtube_video_privacy_read_invalid")
        items = response.get("items")
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], Mapping):
            raise YouTubeOperationUnconfirmedError("youtube_video_privacy_unconfirmed")
        status = items[0].get("status")
        privacy = status.get("privacyStatus") if isinstance(status, Mapping) else ""
        if not str(privacy or "").strip():
            raise YouTubeOperationUnconfirmedError("youtube_video_privacy_unconfirmed")
        return str(privacy).strip().casefold()

    def get_video_privacy(self, *, video_id: str) -> dict[str, Any]:
        return {
            "id": video_id,
            "status": {"privacyStatus": self.get_video_privacy_status(video_id=video_id)},
        }

    def get_video_status(self, *, video_id: str) -> dict[str, Any]:
        return self.get_video_privacy(video_id=video_id)

    def update_video_privacy(self, *, video_id: str, privacy_status: str) -> dict[str, Any]:
        request = self.service.videos().update(
            part="status",
            body={
                "id": video_id,
                "status": {
                    "privacyStatus": privacy_status,
                    "embeddable": True,
                    "publicStatsViewable": True,
                    "selfDeclaredMadeForKids": False,
                },
            },
        )
        response = request.execute()
        return response if isinstance(response, dict) else {"id": video_id}

    def add_video_to_playlist(self, *, video_id: str, playlist_id: str) -> str:
        request = self.service.playlistItems().insert(
            part="snippet",
            body={
                "snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {"kind": "youtube#video", "videoId": video_id},
                }
            },
        )
        response = request.execute()
        return str(response.get("id") or "")

    def list_playlist_items(self, *, playlist_id: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            response = self.service.playlistItems().list(
                part="snippet,contentDetails",
                playlistId=playlist_id,
                maxResults=50,
                pageToken=page_token,
            ).execute()
            for item in response.get("items", []):
                snippet = item.get("snippet", {}) if isinstance(item, dict) else {}
                resource = snippet.get("resourceId", {}) if isinstance(snippet, dict) else {}
                content = item.get("contentDetails", {}) if isinstance(item, dict) else {}
                items.append(
                    {
                        "playlistItemId": item.get("id"),
                        "videoId": resource.get("videoId") or content.get("videoId") or "",
                        "title": snippet.get("title") or "",
                        "position": snippet.get("position"),
                    }
                )
            page_token = response.get("nextPageToken")
            if not page_token:
                return items

    def delete_playlist_item(self, playlist_item_id: str) -> dict[str, Any]:
        response = self.service.playlistItems().delete(id=playlist_item_id).execute()
        return response if isinstance(response, dict) else {"id": playlist_item_id}


def _mp4_and_hash(day: str, kind: str = "daily") -> tuple[Path, str]:
    if not _DATE_RE.match(day):
        raise ValueError(f"invalid date: {day}")
    mp4 = _build_dir(kind) / f"{day}.mp4"
    if not mp4.exists():
        raise FileNotFoundError(f"mp4 not found: {mp4}")
    return mp4, sha256_file(mp4)


def prepare(
    day: str,
    *,
    client: PodcastClient | None = None,
    dry_run: bool = False,
    kind: str = "daily",
    run_id: str | None = None,
    bundle_id: str | None = None,
    operation_id: str | None = None,
    payload_identity: str | None = None,
    operation_marker: str | None = None,
    marker: str | None = None,
) -> dict[str, Any]:
    mp4, mp4_hash = _mp4_and_hash(day, kind)
    identity = _production_operation_identity(
        run_id=run_id,
        bundle_id=bundle_id,
        operation_id=operation_id,
        payload_identity=payload_identity,
        operation_marker=operation_marker,
        marker=marker,
        default_operation_id=f"youtube_{kind}_prepare",
    )
    if identity is not None and identity["payloadIdentity"] != mp4_hash:
        raise YouTubeOperationMarkerError("youtube_operation_marker_payload_identity_mismatch")

    uploads = _load_uploads(kind)
    if identity is None:
        existing = uploads.get(day)
        if isinstance(existing, dict) and existing.get("mp4_sha256") == mp4_hash and existing.get("videoId"):
            return {"date": day, "skipped": True, **existing}

    metadata = _build_metadata(day, kind)
    if identity is not None:
        metadata = _append_operation_marker(metadata, identity["operationMarker"])
    if dry_run:
        result = {
            "date": day,
            "dry_run": True,
            "phase": "prepared",
            "status": "private",
            "videoId": "",
            "mp4_path": str(mp4),
            "mp4_sha256": mp4_hash,
            "metadata": metadata,
        }
        if identity is not None:
            result.update(
                {
                    "operationMarker": identity["operationMarker"],
                    "operationId": identity["operationId"],
                    "runId": identity["runId"],
                    "bundleId": identity["bundleId"],
                    "payloadIdentity": identity["payloadIdentity"],
                }
            )
        print(json.dumps(result, ensure_ascii=False))
        return result

    active_client = client or YouTubePodcastClient.from_local_secrets()
    if identity is not None:
        # local rowの有無にかかわらず、provider側のmarkerをfreshに観測して
        # crash後のupload再送を防ぐ。0件だけがuploadへ進む。
        candidate = _single_marker_candidate(
            _fresh_marker_candidates(active_client, identity["operationMarker"])
        )
        if candidate is not None:
            existing = _history_row(uploads, day=day, kind=kind, identity=identity)
            video_id = _candidate_video_id(candidate)
            if not video_id:
                raise YouTubeOperationUnconfirmedError("youtube_operation_marker_video_id_missing")
            if existing is not None and str(existing.get("videoId") or "") not in {"", video_id}:
                raise YouTubeOperationError("youtube_upload_history_provider_video_conflict")
            if existing is not None:
                base_row = dict(existing)
                base_row.update({"videoId": video_id, "mp4_sha256": mp4_hash})
                if str(base_row.get("status") or "").casefold() != "public":
                    base_row.update({"phase": "prepared", "status": "private"})
                base_row.setdefault("title", metadata["title"])
            else:
                base_row = {
                    "phase": "prepared",
                    "status": "private",
                    "videoId": video_id,
                    "mp4_sha256": mp4_hash,
                    "title": metadata["title"],
                }
            row = _row_with_identity(
                base_row,
                identity,
            )
            row = _persist_upload_row(
                uploads,
                day=day,
                kind=kind,
                row=row,
                identity=identity,
                activate_if_new=False,
            )
            _write_uploads(uploads, kind)
            result = {"date": day, "skipped": True, **row}
            print(json.dumps(result, ensure_ascii=False))
            return result

    video_id = active_client.upload_video(mp4, metadata, privacy_status="private")
    row = {
        "phase": "prepared",
        "status": "private",
        "videoId": video_id,
        "mp4_sha256": mp4_hash,
        "title": metadata["title"],
    }
    row = _persist_upload_row(
        uploads,
        day=day,
        kind=kind,
        row=row,
        identity=identity,
    )
    _write_uploads(uploads, kind)
    result = {"date": day, "skipped": False, **row}
    print(json.dumps(result, ensure_ascii=False))
    return result


def finalize(
    day: str,
    *,
    client: PodcastClient | None = None,
    kind: str = "daily",
    run_id: str | None = None,
    bundle_id: str | None = None,
    operation_id: str | None = None,
    payload_identity: str | None = None,
    operation_marker: str | None = None,
    marker: str | None = None,
) -> dict[str, Any]:
    mp4, mp4_hash = _mp4_and_hash(day, kind)
    identity = _production_operation_identity(
        run_id=run_id,
        bundle_id=bundle_id,
        operation_id=operation_id,
        payload_identity=payload_identity,
        operation_marker=operation_marker,
        marker=marker,
        default_operation_id=f"youtube_{kind}_finalize",
    )
    if identity is not None and identity["payloadIdentity"] != mp4_hash:
        raise YouTubeOperationMarkerError("youtube_operation_marker_payload_identity_mismatch")

    uploads = _load_uploads(kind)
    if identity is None:
        row = uploads.get(day)
        if not isinstance(row, dict) or not row.get("videoId"):
            raise RuntimeError(f"prepared podcast upload not found for {day}")
        needs_primary_playlist = kind == "deepdive" and not row.get("primaryPodcastPlaylistItemId")
        if row.get("status") == "public" and row.get("playlistItemId") and not needs_primary_playlist:
            return {"date": day, "skipped": True, **row}

        active_client = client or YouTubePodcastClient.from_local_secrets()
        video_id = str(row["videoId"])
        playlist_id = str(row.get("playlistId") or _ensure_playlist(active_client, kind))
        active_client.update_video_privacy(video_id=video_id, privacy_status="public")
        playlist_item_id = str(row.get("playlistItemId") or "")
        if not playlist_item_id:
            playlist_item_id = active_client.add_video_to_playlist(video_id=video_id, playlist_id=playlist_id)
        primary_playlist_id = str(row.get("primaryPodcastPlaylistId") or "")
        primary_playlist_item_id = str(row.get("primaryPodcastPlaylistItemId") or "")
        if kind == "deepdive":
            if not primary_playlist_id:
                primary_playlist_id = _ensure_playlist(active_client, "daily")
            if not primary_playlist_item_id:
                primary_playlist_item_id = active_client.add_video_to_playlist(
                    video_id=video_id,
                    playlist_id=primary_playlist_id,
                )
        row.update(
            {
                "phase": "finalized",
                "status": "public",
                "playlistId": playlist_id,
                "playlistItemId": playlist_item_id,
            }
        )
        if kind == "deepdive":
            row.update(
                {
                    "primaryPodcastPlaylistId": primary_playlist_id,
                    "primaryPodcastPlaylistItemId": primary_playlist_item_id,
                }
            )
        uploads[day] = row
        _write_uploads(uploads, kind)
        result = {"date": day, "skipped": False, **row}
        print(json.dumps(result, ensure_ascii=False))
        return result

    active_client = client or YouTubePodcastClient.from_local_secrets()
    row = _history_row(uploads, day=day, kind=kind, identity=identity)
    candidate = _single_marker_candidate(
        _fresh_marker_candidates(active_client, identity["operationMarker"])
    )
    if candidate is None:
        candidate = _fresh_candidate_for_identity(
            active_client,
            identity=identity,
            existing=row,
            kind=kind,
        )
    if candidate is None:
        raise YouTubeOperationUnconfirmedError("youtube_operation_unconfirmed")
    video_id = _candidate_video_id(candidate)
    if not video_id:
        raise YouTubeOperationUnconfirmedError("youtube_operation_marker_video_id_missing")
    if row is not None and str(row.get("videoId") or "") not in {"", video_id}:
        raise YouTubeOperationError("youtube_upload_history_provider_video_conflict")
    if row is None:
        row = {
            "phase": "prepared",
            "status": "private",
            "videoId": video_id,
            "mp4_sha256": mp4_hash,
            "title": str(candidate.get("title") or ""),
        }
    row = _row_with_identity(row, identity)
    if str(row.get("mp4_sha256") or "").casefold() != mp4_hash:
        raise YouTubeOperationError("youtube_upload_history_payload_identity_conflict")

    privacy = _fresh_video_privacy(active_client, video_id)
    changed_provider = False
    if privacy != "public":
        if privacy not in {"private", "unlisted"}:
            raise YouTubeOperationUnconfirmedError("youtube_video_privacy_unconfirmed")
        active_client.update_video_privacy(video_id=video_id, privacy_status="public")
        changed_provider = True
        privacy = _fresh_video_privacy(active_client, video_id)
    if privacy != "public":
        raise YouTubeOperationUnconfirmedError("youtube_reconcile_public_binding_unconfirmed")
    row = _persist_finalize_substep(
        uploads,
        day=day,
        kind=kind,
        row=row,
        identity=identity,
        substep_id="privacy_public",
        provider_fields={"videoId": video_id, "privacyStatus": "public"},
    )

    playlist_id = str(row.get("playlistId") or _ensure_playlist(active_client, kind))
    playlist_membership = _fresh_playlist_membership(
        active_client,
        video_id=video_id,
        playlist_id=playlist_id,
    )
    changed_provider = changed_provider or playlist_membership is None
    if playlist_membership is None:
        playlist_item_id = str(
            active_client.add_video_to_playlist(video_id=video_id, playlist_id=playlist_id) or ""
        )
        if not playlist_item_id:
            raise YouTubeOperationUnconfirmedError("youtube_playlist_membership_unconfirmed")
        playlist_membership = _fresh_playlist_membership(
            active_client,
            video_id=video_id,
            playlist_id=playlist_id,
        )
        if playlist_membership is None:
            raise YouTubeOperationUnconfirmedError("youtube_playlist_membership_unconfirmed")
        playlist_item_id = str(playlist_membership.get("playlistItemId") or playlist_item_id)
    else:
        playlist_item_id = str(playlist_membership.get("playlistItemId") or "")
        if not playlist_item_id:
            raise YouTubeOperationUnconfirmedError("youtube_playlist_membership_item_id_missing")
    row = _persist_finalize_substep(
        uploads,
        day=day,
        kind=kind,
        row=row,
        identity=identity,
        substep_id="kind_playlist_membership",
        provider_fields={"playlistId": playlist_id, "playlistItemId": playlist_item_id},
    )

    primary_playlist_id = str(row.get("primaryPodcastPlaylistId") or "")
    primary_playlist_item_id = str(row.get("primaryPodcastPlaylistItemId") or "")
    if kind == "deepdive":
        if not primary_playlist_id:
            primary_playlist_id = _ensure_playlist(active_client, "daily")
        primary_membership = _fresh_playlist_membership(
            active_client,
            video_id=video_id,
            playlist_id=primary_playlist_id,
        )
        changed_provider = changed_provider or primary_membership is None
        if primary_membership is None:
            primary_playlist_item_id = str(
                active_client.add_video_to_playlist(
                    video_id=video_id,
                    playlist_id=primary_playlist_id,
                )
                or ""
            )
            if not primary_playlist_item_id:
                raise YouTubeOperationUnconfirmedError("youtube_playlist_membership_unconfirmed")
            primary_membership = _fresh_playlist_membership(
                active_client,
                video_id=video_id,
                playlist_id=primary_playlist_id,
            )
            if primary_membership is None:
                raise YouTubeOperationUnconfirmedError("youtube_playlist_membership_unconfirmed")
            primary_playlist_item_id = str(
                primary_membership.get("playlistItemId") or primary_playlist_item_id
            )
        else:
            primary_playlist_item_id = str(primary_membership.get("playlistItemId") or "")
            if not primary_playlist_item_id:
                raise YouTubeOperationUnconfirmedError("youtube_playlist_membership_item_id_missing")
        row = _persist_finalize_substep(
            uploads,
            day=day,
            kind=kind,
            row=row,
            identity=identity,
            substep_id="primary_playlist_membership",
            provider_fields={
                "playlistId": primary_playlist_id,
                "playlistItemId": primary_playlist_item_id,
            },
        )

    row.update(
        {
            "phase": "finalized",
            "status": "public",
            "videoId": video_id,
            "mp4_sha256": mp4_hash,
            "playlistId": playlist_id,
            "playlistItemId": playlist_item_id,
        }
    )
    if kind == "deepdive":
        row.update(
            {
                "primaryPodcastPlaylistId": primary_playlist_id,
                "primaryPodcastPlaylistItemId": primary_playlist_item_id,
            }
        )
    row = _persist_upload_row(
        uploads,
        day=day,
        kind=kind,
        row=row,
        identity=identity,
        activate_if_new=False,
    )
    _write_uploads(uploads, kind)
    result = {"date": day, "skipped": not changed_provider, **row}
    print(json.dumps(result, ensure_ascii=False))
    return result


def reconcile(
    day: str,
    *,
    client: PodcastClient | None = None,
    kind: str = "daily",
    phase: str = "prepare",
    run_id: str | None = None,
    bundle_id: str | None = None,
    operation_id: str | None = None,
    payload_identity: str | None = None,
    operation_marker: str | None = None,
    marker: str | None = None,
) -> dict[str, Any]:
    """provider call後local write前の停止を、fresh readだけで復旧する。"""

    if phase not in {"prepare", "finalize"}:
        raise ValueError(f"invalid reconcile phase: {phase}")
    mp4, mp4_hash = _mp4_and_hash(day, kind)
    del mp4
    identity = _production_operation_identity(
        run_id=run_id,
        bundle_id=bundle_id,
        operation_id=operation_id,
        payload_identity=payload_identity,
        operation_marker=operation_marker,
        marker=marker,
        default_operation_id=f"youtube_{kind}_{phase}",
    )
    if identity is None:
        raise YouTubeOperationMarkerError("youtube_operation_marker_required")
    if identity["payloadIdentity"] != mp4_hash:
        raise YouTubeOperationMarkerError("youtube_operation_marker_payload_identity_mismatch")
    active_client = client or YouTubePodcastClient.from_local_secrets()
    uploads = _load_uploads(kind)
    row = _history_row(uploads, day=day, kind=kind, identity=identity)
    candidate = _single_marker_candidate(
        _fresh_marker_candidates(active_client, identity["operationMarker"])
    )
    if candidate is None:
        candidate = _fresh_candidate_for_identity(
            active_client,
            identity=identity,
            existing=row,
            kind=kind,
        )
    if candidate is None:
        raise YouTubeOperationUnconfirmedError("youtube_reconcile_video_unconfirmed")
    video_id = _candidate_video_id(candidate)
    if not video_id:
        raise YouTubeOperationUnconfirmedError("youtube_reconcile_video_unconfirmed")
    if row is not None and str(row.get("videoId") or "") not in {"", video_id}:
        raise YouTubeOperationError("youtube_reconcile_provider_video_conflict")
    if row is None:
        row = {
            "videoId": video_id,
            "mp4_sha256": mp4_hash,
            "title": str(candidate.get("title") or ""),
        }
    row = _row_with_identity(row, identity)
    row["videoId"] = video_id
    row["mp4_sha256"] = mp4_hash
    if phase == "prepare":
        row.update({"phase": "prepared", "status": "private"})
    else:
        if _fresh_video_privacy(active_client, video_id) != "public":
            raise YouTubeOperationUnconfirmedError("youtube_reconcile_public_binding_unconfirmed")
        playlist_id = str(row.get("playlistId") or _ensure_playlist(active_client, kind))
        membership = _fresh_playlist_membership(
            active_client,
            video_id=video_id,
            playlist_id=playlist_id,
        )
        if membership is None:
            raise YouTubeOperationUnconfirmedError("youtube_reconcile_public_binding_unconfirmed")
        playlist_item_id = str(membership.get("playlistItemId") or "")
        if not playlist_item_id:
            raise YouTubeOperationUnconfirmedError("youtube_reconcile_public_binding_unconfirmed")
        row.update(
            {
                "phase": "finalized",
                "status": "public",
                "playlistId": playlist_id,
                "playlistItemId": playlist_item_id,
            }
        )
        if kind == "deepdive":
            primary_playlist_id = str(row.get("primaryPodcastPlaylistId") or _ensure_playlist(active_client, "daily"))
            primary_membership = _fresh_playlist_membership(
                active_client,
                video_id=video_id,
                playlist_id=primary_playlist_id,
            )
            if primary_membership is None:
                raise YouTubeOperationUnconfirmedError("youtube_reconcile_public_binding_unconfirmed")
            primary_item_id = str(primary_membership.get("playlistItemId") or "")
            if not primary_item_id:
                raise YouTubeOperationUnconfirmedError("youtube_reconcile_public_binding_unconfirmed")
            row.update(
                {
                    "primaryPodcastPlaylistId": primary_playlist_id,
                    "primaryPodcastPlaylistItemId": primary_item_id,
                }
            )
    row = _persist_upload_row(
        uploads,
        day=day,
        kind=kind,
        row=row,
        identity=identity,
        activate_if_new=False,
    )
    _write_uploads(uploads, kind)
    result = {"date": day, "skipped": True, "reconciled": True, **row}
    print(json.dumps(result, ensure_ascii=False))
    return result


def reconcile_prepare(day: str, **kwargs: Any) -> dict[str, Any]:
    return reconcile(day, phase="prepare", **kwargs)


def reconcile_finalize(day: str, **kwargs: Any) -> dict[str, Any]:
    return reconcile(day, phase="finalize", **kwargs)


def publish(
    day: str,
    *,
    client: PodcastClient | None = None,
    dry_run: bool = False,
    privacy_status: str = "public",
    kind: str = "daily",
) -> dict[str, Any]:
    mp4, mp4_hash = _mp4_and_hash(day, kind)
    existing = _already_public(day, mp4_hash, kind)
    if existing:
        return {"date": day, "skipped": True, **existing}

    metadata = _build_metadata(day, kind)
    if dry_run:
        result = {
            "date": day,
            "dry_run": True,
            "videoId": "",
            "playlistItemId": "",
            "mp4_path": str(mp4),
            "mp4_sha256": mp4_hash,
            "metadata": metadata,
        }
        print(json.dumps(result, ensure_ascii=False))
        return result

    active_client = client or YouTubePodcastClient.from_local_secrets()
    playlist_id = _ensure_playlist(active_client, kind)
    video_id = active_client.upload_video(mp4, metadata, privacy_status=privacy_status)
    playlist_item_id = active_client.add_video_to_playlist(video_id=video_id, playlist_id=playlist_id)
    row = {
        "status": privacy_status,
        "videoId": video_id,
        "playlistId": playlist_id,
        "playlistItemId": playlist_item_id,
        "mp4_sha256": mp4_hash,
    }
    uploads = _load_uploads(kind)
    uploads[day] = row
    _write_uploads(uploads, kind)
    result = {"date": day, "skipped": False, **uploads[day]}
    print(json.dumps(result, ensure_ascii=False))
    return result


def audit_playlist_uniqueness(day: str, *, client: PodcastClient | None = None) -> dict[str, Any]:
    if not _DATE_RE.match(day):
        raise ValueError(f"invalid date: {day}")
    active_client = client or YouTubePodcastClient.from_local_secrets()
    checks: list[dict[str, str]] = []
    allowed_by_playlist: dict[str, set[str]] = {}
    for kind in ("daily", "deepdive"):
        row = _load_uploads(kind).get(day)
        if not isinstance(row, dict):
            continue
        video_id = str(row.get("videoId") or "")
        playlist_id = str(row.get("playlistId") or "")
        if video_id and playlist_id:
            checks.append({"kind": kind, "videoId": video_id, "playlistId": playlist_id})
            allowed_by_playlist.setdefault(playlist_id, set()).add(video_id)
        primary_playlist_id = str(row.get("primaryPodcastPlaylistId") or "")
        if kind == "deepdive" and video_id and primary_playlist_id:
            checks.append({"kind": "deepdive-primary", "videoId": video_id, "playlistId": primary_playlist_id})
            allowed_by_playlist.setdefault(primary_playlist_id, set()).add(video_id)

    issues: list[dict[str, Any]] = []
    surfaces: list[dict[str, Any]] = []
    for check in checks:
        items = active_client.list_playlist_items(playlist_id=check["playlistId"])
        matched = [item for item in items if item.get("videoId") == check["videoId"]]
        dated_unexpected = [
            item
            for item in items
            if day in str(item.get("title") or "")
            and item.get("videoId") not in allowed_by_playlist.get(check["playlistId"], set())
        ]
        deleted_items = [item for item in items if str(item.get("title") or "") == "Deleted video"]
        if len(matched) != 1:
            issues.append(
                {
                    "reason": "podcast_playlist_expected_video_count",
                    "kind": check["kind"],
                    "playlistId": check["playlistId"],
                    "videoId": check["videoId"],
                    "count": len(matched),
                }
            )
        for item in dated_unexpected:
            issues.append(
                {
                    "reason": "podcast_playlist_unexpected_same_date_video",
                    "kind": check["kind"],
                    "playlistId": check["playlistId"],
                    "videoId": item.get("videoId"),
                    "title": item.get("title"),
                    "playlistItemId": item.get("playlistItemId"),
                }
            )
        for item in deleted_items:
            issues.append(
                {
                    "reason": "podcast_playlist_deleted_video_item",
                    "kind": check["kind"],
                    "playlistId": check["playlistId"],
                    "videoId": item.get("videoId"),
                    "playlistItemId": item.get("playlistItemId"),
                }
            )
        surfaces.append(
            {
                "kind": check["kind"],
                "playlistId": check["playlistId"],
                "videoId": check["videoId"],
                "matched_count": len(matched),
                "unexpected_same_date_count": len(dated_unexpected),
                "deleted_item_count": len(deleted_items),
            }
        )
    return {"ok": not issues, "date": day, "surfaces": surfaces, "issues": issues}


PLAYLIST_REPAIR_AUTHORITY_SCHEMA = "NEWS_GRASP_PLAYLIST_MEMBERSHIP_REPAIR_AUTHORITY_V1"
PLAYLIST_REPAIR_RESULT_SCHEMA = "NEWS_GRASP_PLAYLIST_MEMBERSHIP_REPAIR_RESULT_V1"
_PLAYLIST_REPAIR_AUTHORITY_FIELDS = frozenset(
    {
        "schemaVersion",
        "issueDate",
        "action",
        "playlistItemIds",
        "preserveVideoObjects",
        "issuedAt",
        "expiresAt",
        "auditSha256",
        "receiptSha256",
    }
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _authority_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"playlist repair authority {field} must be an RFC3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"playlist repair authority {field} is invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"playlist repair authority {field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _validate_playlist_repair_authority(
    day: str,
    authority: Any,
    *,
    now: datetime,
) -> dict[str, Any]:
    if not isinstance(authority, dict):
        raise ValueError("playlist repair authority must be a JSON object")
    if set(authority) != _PLAYLIST_REPAIR_AUTHORITY_FIELDS:
        raise ValueError("playlist repair authority fields are incomplete or unknown")
    if authority.get("schemaVersion") != PLAYLIST_REPAIR_AUTHORITY_SCHEMA:
        raise ValueError("playlist repair authority schemaVersion is invalid")
    if not isinstance(authority.get("issueDate"), str) or authority["issueDate"] != day:
        raise ValueError("playlist repair authority issueDate does not match the requested date")
    if not _DATE_RE.match(day):
        raise ValueError(f"invalid date: {day}")
    if authority.get("action") != "delete_playlist_memberships":
        raise ValueError("playlist repair authority action is invalid")
    playlist_item_ids = authority.get("playlistItemIds")
    if not isinstance(playlist_item_ids, list) or not playlist_item_ids or any(
        not isinstance(item_id, str) or not item_id for item_id in playlist_item_ids
    ):
        raise ValueError("playlist repair authority playlistItemIds must be non-empty strings")
    if playlist_item_ids != sorted(set(playlist_item_ids)):
        raise ValueError("playlist repair authority playlistItemIds must be sorted and unique")
    if authority.get("preserveVideoObjects") is not True:
        raise ValueError("playlist repair authority must preserve video objects")
    issued_at = _authority_timestamp(authority.get("issuedAt"), "issuedAt")
    expires_at = _authority_timestamp(authority.get("expiresAt"), "expiresAt")
    if issued_at > expires_at or now < issued_at or now > expires_at:
        raise ValueError("playlist repair authority is outside its validity window")
    for field in ("auditSha256", "receiptSha256"):
        value = authority.get(field)
        if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
            raise ValueError(f"playlist repair authority {field} is invalid")
    unsigned = dict(authority)
    unsigned.pop("receiptSha256", None)
    if _canonical_sha256(unsigned) != authority["receiptSha256"]:
        raise ValueError("playlist repair authority receiptSha256 does not match its contents")
    return dict(authority)


def _audited_unexpected_playlist_item_ids(audit: Any) -> list[str]:
    if not isinstance(audit, dict) or not isinstance(audit.get("issues"), list):
        raise ValueError("playlist audit result is invalid")
    item_ids = {
        str(issue["playlistItemId"])
        for issue in audit["issues"]
        if isinstance(issue, dict)
        and issue.get("reason") == "podcast_playlist_unexpected_same_date_video"
        and issue.get("playlistItemId")
    }
    return sorted(item_ids)


def repair_playlist_memberships(
    day: str,
    authority: dict[str, Any],
    *,
    client: PodcastClient | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """承認済みの playlist membership だけを削除し、動画オブジェクトは保持する。"""
    now = datetime.now(timezone.utc)
    validated_authority = _validate_playlist_repair_authority(day, authority, now=now)
    active_client = client or YouTubePodcastClient.from_local_secrets()
    audit_before = audit_playlist_uniqueness(day, client=active_client)
    if _canonical_sha256(audit_before) != validated_authority["auditSha256"]:
        raise ValueError("playlist repair authority auditSha256 does not match a fresh audit")
    audited_ids = _audited_unexpected_playlist_item_ids(audit_before)
    authorized_ids = validated_authority["playlistItemIds"]
    if authorized_ids != audited_ids:
        raise ValueError("playlist repair authority playlistItemIds do not match the fresh audit")

    if dry_run:
        return {
            "schemaVersion": PLAYLIST_REPAIR_RESULT_SCHEMA,
            "status": "ok",
            "issueDate": day,
            "dryRun": True,
            "deletedPlaylistItemIds": [],
            "auditBefore": audit_before,
            "auditAfter": audit_before,
        }

    for playlist_item_id in authorized_ids:
        active_client.delete_playlist_item(playlist_item_id)
    audit_after = audit_playlist_uniqueness(day, client=active_client)
    if not audit_after.get("ok"):
        raise RuntimeError("playlist repair post-audit is not Green")
    return {
        "schemaVersion": PLAYLIST_REPAIR_RESULT_SCHEMA,
        "status": "ok",
        "issueDate": day,
        "dryRun": False,
        "deletedPlaylistItemIds": list(authorized_ids),
        "auditBefore": audit_before,
        "auditAfter": audit_after,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="YouTube Podcast episode を dry-run または公開 upload します。")
    parser.add_argument("date", help="YYYY-MM-DD")
    parser.add_argument("--kind", choices=["daily", "deepdive"], default="daily", help="daily=日次朗読 / deepdive=DeepDive解説対談")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--prepare", action="store_true", help="push 前に private video として upload する。")
    mode.add_argument("--finalize", action="store_true", help="Web 公開確認後に public 化して playlist に追加する。")
    mode.add_argument("--publish", action="store_true", help="YouTube へ公開 upload して podcast playlist に追加する。")
    mode.add_argument("--audit-playlists", action="store_true", help="公開 playlist に同日重複や Deleted video item が残っていないか検査する。")
    mode.add_argument("--repair-playlists", action="store_true", help="承認済み authority に従って playlist membership を修復する。")
    parser.add_argument("--authority-file", type=Path, help="playlist membership repair authority JSON のパス。")
    parser.add_argument("--dry-run", action="store_true", help="YouTube API を呼ばず mp4 と metadata を検査する。")
    parser.add_argument("--privacy-status", default="public", choices=["public", "private", "unlisted"])
    args = parser.parse_args(argv)
    try:
        if args.audit_playlists:
            result = audit_playlist_uniqueness(args.date)
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result.get("ok") else 1
        if args.repair_playlists:
            if args.authority_file is None:
                raise ValueError("--repair-playlists requires --authority-file")
            authority = _load_json(args.authority_file)
            result = repair_playlist_memberships(args.date, authority, dry_run=args.dry_run)
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result.get("status") == "ok" else 1
        if args.prepare:
            prepare(args.date, dry_run=args.dry_run, kind=args.kind)
        elif args.finalize:
            if args.dry_run:
                raise ValueError("--finalize cannot be combined with --dry-run")
            finalize(args.date, kind=args.kind)
        else:
            publish(args.date, dry_run=args.dry_run, privacy_status=args.privacy_status, kind=args.kind)
        return 0
    except Exception as exc:
        _warn(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
