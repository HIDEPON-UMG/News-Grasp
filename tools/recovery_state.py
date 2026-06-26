from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 1
VERIFIER_VERSION = "news-grasp-recovery-proof-r7"
KNOWN_EXTERNAL_BLOCK_CODES = {
    "google_api_external",
    "oauth_consent_required",
    "youtube_quota_or_permission",
    "github_pages_external",
    "github_pages_deploy_workflow_not_success",
    "deploy_workflow_not_success",
}


def _iso(value: datetime | str) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _parse_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def canonical_required_surface_digest(required_surfaces: Iterable[str]) -> str:
    payload = json.dumps(
        sorted(str(surface).replace("\\", "/") for surface in required_surfaces),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_recovery_proof(
    *,
    issue_date: str,
    repo_root: Path,
    head_sha: str,
    remote_head_sha: str,
    required_surface_digest: str,
    overall_status: str,
    external_block_code: str,
    external_block_evidence: dict[str, Any],
    checked_at: datetime | str,
    expires_at: datetime | str,
    public_urls: list[str],
    publish_complete_manifest: dict[str, Any],
    errors: list[str],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "issue_date": issue_date,
        "repo_root": str(Path(repo_root).resolve()),
        "head_sha": head_sha,
        "remote_head_sha": remote_head_sha,
        "required_surface_digest": required_surface_digest,
        "overall_status": overall_status,
        "external_block_code": external_block_code,
        "external_block_evidence": dict(external_block_evidence),
        "checked_at": _iso(checked_at),
        "expires_at": _iso(expires_at),
        "verifier_version": VERIFIER_VERSION,
        "public_urls": list(public_urls),
        "publish_complete_manifest": dict(publish_complete_manifest),
        "errors": list(errors),
    }


def validate_recovery_proof(
    proof: dict[str, Any],
    *,
    expected_issue_date: str,
    expected_repo_root: Path,
    expected_head_sha: str,
    expected_remote_head_sha: str,
    expected_required_surface_digest: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    errors: list[str] = []
    if "recovery_green" in proof:
        errors.append("legacy_recovery_green_field")
    if proof.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version")
    if str(proof.get("issue_date") or "") != expected_issue_date:
        errors.append("issue_date")
    if Path(str(proof.get("repo_root") or "")).resolve() != Path(expected_repo_root).resolve():
        errors.append("repo_root")
    if str(proof.get("head_sha") or "") != expected_head_sha:
        errors.append("head_sha")
    if str(proof.get("remote_head_sha") or "") != expected_remote_head_sha:
        errors.append("remote_head_sha")
    if str(proof.get("required_surface_digest") or "") != expected_required_surface_digest:
        errors.append("required_surface_digest")
    expires_at = _parse_iso(proof.get("expires_at"))
    if expires_at is None:
        errors.append("expires_at")
    elif now > expires_at:
        errors.append("expired")

    status = str(proof.get("overall_status") or "").strip().lower()
    external_code = str(proof.get("external_block_code") or "").strip()
    if status == "green" and external_code != "none":
        errors.append("green_with_external_block")
    if status == "yellow" and external_code not in KNOWN_EXTERNAL_BLOCK_CODES:
        errors.append("unknown_external_block_code")

    if errors:
        return {"ok": False, "exit_code": 1, "overall_status": "red", "errors": errors}
    if status == "green":
        return {"ok": True, "exit_code": 0, "overall_status": "green", "errors": []}
    if status == "yellow":
        return {
            "ok": False,
            "exit_code": 10,
            "overall_status": "yellow",
            "external_block_code": external_code,
            "errors": [],
        }
    return {"ok": False, "exit_code": 1, "overall_status": "red", "errors": ["overall_status"]}


def write_recovery_proof(path: Path, proof: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(proof, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
