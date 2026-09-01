"""日次・DeepDive音声を単一schemaへ投影する正本境界。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit, urlunsplit
import urllib.request

from tools.safe_public_fetch import safe_urlopen


AUDIO_SCHEMA = "NEWS_GRASP_AUDIO_PROJECTION_V2"
RUN_INTENT = "scheduled_production_direct"
_TYPES = {"daily", "deepdive"}
_MAX_RECEIPT_BYTES = 1024 * 1024


def _read_receipt_no_follow(path: Path) -> bytes:
    before = os.lstat(path)
    attributes = int(getattr(before, "st_file_attributes", 0))
    if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode) or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
        raise ValueError("audio_source_receipt_unsafe")
    if before.st_size > _MAX_RECEIPT_BYTES:
        raise ValueError("audio_source_receipt_too_large")
    flags = os.O_RDONLY | int(getattr(os, "O_BINARY", 0)) | int(getattr(os, "O_NOFOLLOW", 0))
    fd = os.open(path, flags)
    try:
        opened = os.fstat(fd)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns):
            raise ValueError("audio_source_receipt_identity_changed")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, min(65536, _MAX_RECEIPT_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > _MAX_RECEIPT_BYTES:
                raise ValueError("audio_source_receipt_too_large")
        raw = b"".join(chunks)
        after = os.fstat(fd)
        if len(raw) != opened.st_size or (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError("audio_source_receipt_identity_changed")
        return raw
    finally:
        os.close(fd)


def _normalized_public_url(value: str) -> str:
    parsed = urlsplit(str(value))
    host = (parsed.hostname or "").casefold()
    netloc = host if parsed.port is None else f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme.casefold(), netloc, parsed.path, parsed.query, parsed.fragment))


def _validate_release_url(value: str, *, audio_type: str, issue_date: str) -> bool:
    parsed = urlsplit(str(value))
    expected_path = f"/HIDEPON-UMG/News-Grasp/releases/download/audio-{audio_type}/{issue_date}.mp3"
    return parsed.scheme.casefold() == "https" and (parsed.hostname or "").casefold() == "github.com" and parsed.path == expected_path and not parsed.username and not parsed.password and parsed.port in {None, 443} and not parsed.query and not parsed.fragment


def _probe_public_audio(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Range": "bytes=0-65535", "Cache-Control": "no-cache", "User-Agent": "News-Grasp-audio-verifier"})
    try:
        with safe_urlopen(request, timeout=20) as response:
            final = urlsplit(response.geturl())
            final_host = (final.hostname or "").casefold()
            if final.scheme.casefold() != "https" or not (final_host == "github.com" or final_host.endswith(".githubusercontent.com")):
                return {"ok": False, "reasonCode": "audio_redirect_target_invalid"}
            body = response.read(65537)
            content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].casefold()
            ok = 200 <= int(getattr(response, "status", 200)) < 400 and 0 < len(body) <= 65536 and content_type in {"audio/mpeg", "audio/mp3", "application/octet-stream", "binary/octet-stream"}
            return {
                "ok": ok,
                "contentType": content_type,
                "size": len(body),
                "finalHost": final_host,
                "finalPathSha256": hashlib.sha256(final.path.encode("utf-8", errors="replace")).hexdigest(),
            }
    except (OSError, ValueError) as exc:
        return {"ok": False, "reasonCode": "audio_public_probe_failed", "detail": str(exc)}


def canonical_audio_path(audio_type: str) -> Path:
    """audio typeごとの同形JSON canonical pathを返す。"""
    value = str(audio_type).strip().casefold()
    if value not in _TYPES:
        raise ValueError("audio_type_invalid")
    return Path("build") / "tts" / value / "latest_audio.json"


def _legacy_values(value: Mapping[str, Any], audio_type: str) -> tuple[str, str]:
    if audio_type == "daily":
        return (
            str(value.get("latest_audio_date") or value.get("issueDate") or ""),
            str(value.get("latest_audio_url") or value.get("publicUrl") or ""),
        )
    return (
        str(value.get("deepdive_audio_date") or value.get("issueDate") or ""),
        str(value.get("deepdive_audio_url") or value.get("publicUrl") or ""),
    )


def normalize_audio_projection(
    value: Mapping[str, Any],
    *,
    audio_type: str,
    run_id: str,
    run_intent: str = RUN_INTENT,
    source_artifact: str = "",
    runtime_state: str = "",
    public_page_href: str = "",
) -> dict[str, Any]:
    """V2またはV1をmemory上だけでV2へ正規化する。"""
    kind = str(audio_type).strip().casefold()
    if kind not in _TYPES:
        raise ValueError("audio_type_invalid")
    issue_date, public_url = _legacy_values(value, kind)
    provider = value.get("provider") if isinstance(value.get("provider"), Mapping) else {}
    completion = str(value.get("completionState") or value.get("status") or "verified")
    projection = {
        "schemaVersion": AUDIO_SCHEMA,
        "audioType": kind,
        "sourceArtifact": str(value.get("sourceArtifact") or source_artifact),
        "runtimeState": str(value.get("runtimeState") or runtime_state),
        "provider": {
            "name": str(provider.get("name") or value.get("providerName") or ""),
            "jobIdentity": str(provider.get("jobIdentity") or value.get("jobIdentity") or ""),
        },
        "publicUrl": public_url,
        "publicPageHref": str(value.get("publicPageHref") or public_page_href or public_url),
        "issueDate": issue_date,
        "runId": str(value.get("runId") or run_id),
        "runIntent": str(value.get("runIntent") or run_intent),
        "completionState": completion,
        "adapterSourceSchema": str(value.get("schemaVersion") or "legacy_v1"),
    }
    projection["ok"] = not validate_audio_projection(projection)["reasonCodes"]
    return projection


def validate_audio_projection(
    value: Mapping[str, Any],
    *,
    issue_date: str | None = None,
    run_intent: str | None = None,
    expected_run_id: str | None = None,
) -> dict[str, Any]:
    """required fieldと同一run束縛を検査する。"""
    reasons: list[str] = []
    if value.get("schemaVersion") != AUDIO_SCHEMA:
        reasons.append("audio_schema_invalid")
    if value.get("audioType") not in _TYPES:
        reasons.append("audio_type_invalid")
    if not str(value.get("publicUrl") or "").startswith(("https://", "http://")):
        reasons.append("audio_public_url_invalid")
    if not str(value.get("publicPageHref") or ""):
        reasons.append("audio_public_href_missing")
    if not str(value.get("issueDate") or ""):
        reasons.append("audio_issue_date_missing")
    if not str(value.get("runId") or ""):
        reasons.append("audio_run_id_missing")
    if not str(value.get("runIntent") or ""):
        reasons.append("audio_run_intent_missing")
    if issue_date is not None and value.get("issueDate") != issue_date:
        reasons.append("audio_issue_date_mismatch")
    if run_intent is not None and value.get("runIntent") != run_intent:
        reasons.append("audio_run_intent_mismatch")
    if expected_run_id is not None and value.get("runId") != expected_run_id:
        reasons.append("audio_run_id_mismatch")
    audio_type = str(value.get("audioType") or "")
    observed_date = str(value.get("issueDate") or "")
    public_url = str(value.get("publicUrl") or "")
    public_href = str(value.get("publicPageHref") or "")
    if public_url and public_href and _normalized_public_url(public_url) != _normalized_public_url(public_href):
        reasons.append("audio_public_href_mismatch")
    if audio_type in _TYPES and observed_date and not _validate_release_url(public_url, audio_type=audio_type, issue_date=observed_date):
        reasons.append("audio_release_url_unbound")
    if audio_type in _TYPES and observed_date and not _validate_release_url(public_href, audio_type=audio_type, issue_date=observed_date):
        reasons.append("audio_public_href_unbound")
    if str(value.get("completionState") or "").casefold() not in {
        "verified", "published", "complete", "green", "verified_with_warnings"
    }:
        reasons.append("audio_completion_state_red")
    return {"ok": not reasons, "reasonCodes": reasons}


def load_audio_projection(
    path: str | Path,
    *,
    audio_type: str,
    run_id: str,
    run_intent: str = RUN_INTENT,
) -> dict[str, Any]:
    """V1/V2 stateを読取り専用で正規化する。"""
    source = Path(path)
    value = json.loads(_read_receipt_no_follow(source).decode("utf-8-sig"))
    if not isinstance(value, Mapping):
        raise ValueError("audio_state_not_object")
    return normalize_audio_projection(
        value,
        audio_type=audio_type,
        run_id=run_id,
        run_intent=run_intent,
        runtime_state=str(source),
    )


def write_audio_projection(repo_root: str | Path, value: Mapping[str, Any]) -> Path:
    """V2 producerだけがcanonical pathへ原子的に書く。"""
    row = dict(value)
    validation = validate_audio_projection(row)
    if validation["ok"] is not True:
        raise ValueError("audio_projection_invalid:" + ",".join(validation["reasonCodes"]))
    target = Path(repo_root).resolve() / canonical_audio_path(str(row["audioType"]))
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(row, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, target)
    finally:
        try:
            Path(temp_name).unlink(missing_ok=True)
        except OSError:
            pass
    return target


def bind_existing_audio_projection(
    *,
    repo_root: str | Path,
    input_path: str | Path,
    audio_type: str,
    issue_date: str,
    run_id: str,
    run_intent: str = RUN_INTENT,
    source_artifact: str,
    runtime_state: str,
    public_page_href: str,
    provider_name: str,
    job_identity: str,
    public_probe: Callable[[str], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """既公開V1 receiptを再送せずV2へ一度だけ束縛する。"""

    source = Path(input_path)
    raw_bytes = _read_receipt_no_follow(source)
    raw = json.loads(raw_bytes.decode("utf-8-sig"))
    if not isinstance(raw, Mapping):
        raise ValueError("audio_state_not_object")
    projection = normalize_audio_projection(
        raw,
        audio_type=audio_type,
        run_id=run_id,
        run_intent=run_intent,
        source_artifact=source_artifact,
        runtime_state=runtime_state,
        public_page_href=public_page_href,
    )
    legacy_url = urlsplit(str(projection.get("publicUrl") or ""))
    if legacy_url.query:
        if not re.fullmatch(r"v=[0-9a-f]{12}", legacy_url.query) or legacy_url.fragment:
            raise ValueError("audio_projection_invalid:audio_legacy_cache_buster_invalid")
        projection["publicUrl"] = urlunsplit(
            (legacy_url.scheme, legacy_url.netloc, legacy_url.path, "", "")
        )
    projection["publicPageHref"] = str(public_page_href)
    projection["provider"] = {
        "name": provider_name,
        "jobIdentity": job_identity,
    }
    projection["sourceReceiptSha256"] = hashlib.sha256(raw_bytes).hexdigest()
    projection["completionState"] = "verified"
    if str(projection.get("issueDate") or "") != issue_date:
        raise ValueError("audio_projection_invalid:audio_issue_date_mismatch")
    if provider_name != "github-release" or job_identity != f"audio-{audio_type}/{issue_date}":
        raise ValueError("audio_projection_invalid:audio_provider_identity_unbound")
    validation = validate_audio_projection(
        projection,
        issue_date=issue_date,
        run_intent=run_intent,
        expected_run_id=run_id,
    )
    if validation["ok"] is not True:
        raise ValueError(
            "audio_projection_invalid:" + ",".join(validation["reasonCodes"])
        )
    projection["ok"] = True
    public_observation = dict((public_probe or _probe_public_audio)(str(projection.get("publicUrl") or "")))
    if public_observation.get("ok") is not True:
        raise ValueError("audio_projection_invalid:audio_public_asset_unverified")
    projection["publicObservation"] = public_observation
    output = write_audio_projection(repo_root, projection)
    return {"status": "verified", "output": str(output), "projection": projection}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    bind = subparsers.add_parser("bind-existing")
    bind.add_argument("--repo-root", type=Path, required=True)
    bind.add_argument("--input", type=Path, required=True)
    bind.add_argument("--audio-type", choices=sorted(_TYPES), required=True)
    bind.add_argument("--issue-date", required=True)
    bind.add_argument("--run-id", required=True)
    bind.add_argument("--run-intent", default=RUN_INTENT)
    bind.add_argument("--source-artifact", required=True)
    bind.add_argument("--runtime-state", required=True)
    bind.add_argument("--public-page-href", required=True)
    bind.add_argument("--provider-name", required=True)
    bind.add_argument("--job-identity", required=True)
    args = parser.parse_args(argv)
    try:
        result = bind_existing_audio_projection(
            repo_root=args.repo_root,
            input_path=args.input,
            audio_type=args.audio_type,
            issue_date=args.issue_date,
            run_id=args.run_id,
            run_intent=args.run_intent,
            source_artifact=args.source_artifact,
            runtime_state=args.runtime_state,
            public_page_href=args.public_page_href,
            provider_name=args.provider_name,
            job_identity=args.job_identity,
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "Red", "reason": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
