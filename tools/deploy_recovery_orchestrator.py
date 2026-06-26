from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


DEPLOY_TYPE = "github_pages_actions_docs_from_main"
WORKFLOW_FILE = ".github/workflows/deploy-pages.yml"


def _iso(value: datetime | str) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _parse_iso(value: Any) -> datetime | None:
    text = str(value or "")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def stable_json_sha256(payload: dict[str, Any]) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build_changed_files_digest(repo_root: Path, changed_files: Iterable[Path]) -> list[dict[str, str]]:
    repo_root = Path(repo_root).resolve()
    rows: list[dict[str, str]] = []
    for raw in changed_files:
        path = Path(raw)
        abs_path = path if path.is_absolute() else repo_root / path
        rel = abs_path.resolve().relative_to(repo_root).as_posix()
        rows.append({"path": rel, "sha256": _file_sha256(abs_path)})
    return sorted(rows, key=lambda row: row["path"])


def build_deploy_manifest(
    *,
    goal_id: str,
    repo_root: Path,
    base_sha: str,
    candidate_sha: str,
    remote: str,
    destination_ref: str,
    ahead_commits: list[str],
    changed_files: Iterable[Path],
    created_at: datetime | str,
    expires_at: datetime | str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "goal_id": goal_id,
        "base_sha": base_sha,
        "candidate_sha": candidate_sha,
        "remote": remote,
        "destination_ref": destination_ref,
        "ahead_commits": list(ahead_commits),
        "changed_files": build_changed_files_digest(repo_root, changed_files),
        "rollback_commit_range": f"{base_sha}..{candidate_sha}",
        "deploy_type": DEPLOY_TYPE,
        "workflow_file": WORKFLOW_FILE,
        "created_at": _iso(created_at),
        "expires_at": _iso(expires_at),
    }


def load_trusted_human_approval(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def validate_manifest_and_approval(
    manifest: dict[str, Any],
    approval: dict[str, Any] | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if not approval:
        return {"ok": False, "reason": "trusted_human_approval_missing"}
    if approval.get("approved_by") != "human" or approval.get("source") != "transcript":
        return {"ok": False, "reason": "approval_not_trusted_human_transcript"}
    expires_at = _parse_iso(approval.get("expires_at"))
    if expires_at is None or now > expires_at:
        return {"ok": False, "reason": "approval_expired"}
    if approval.get("manifest_sha256") != stable_json_sha256(manifest):
        return {"ok": False, "reason": "manifest_sha256_mismatch"}
    if approval.get("candidate_sha") != manifest.get("candidate_sha"):
        return {"ok": False, "reason": "candidate_sha_mismatch"}
    if approval.get("destination_ref") != manifest.get("destination_ref"):
        return {"ok": False, "reason": "destination_ref_mismatch"}
    return {"ok": True, "reason": ""}


def classify_deploy_surface_regression(
    *,
    pre_deploy: dict[str, Any],
    post_deploy: dict[str, Any],
    manifest: dict[str, Any],
    workflow_sha: str,
    remote_head_sha: str,
    now: datetime,
    window_start: datetime,
    window_end: datetime,
) -> dict[str, Any]:
    if not (window_start <= now <= window_end):
        return {"rollback_allowed": False, "block_code": "deploy_surface_time_window_mismatch"}
    if workflow_sha != manifest.get("candidate_sha") or remote_head_sha != manifest.get("candidate_sha"):
        return {"rollback_allowed": False, "block_code": "deploy_surface_sha_drift"}
    if post_deploy.get("reason") in {
        "oauth_consent_required",
        "google_api_external",
        "youtube_quota_or_permission",
        "deploy_workflow_timeout",
    }:
        return {"rollback_allowed": False, "block_code": "deploy_surface_unrelated_red"}
    if not pre_deploy.get("ok") or post_deploy.get("ok"):
        return {"rollback_allowed": False, "block_code": "deploy_surface_not_newly_red"}

    touched = {
        row["path"]
        for row in manifest.get("changed_files", [])
        if isinstance(row, dict) and str(row.get("path") or "").startswith("docs/")
    }
    post_surface = set(str(item) for item in post_deploy.get("surface", []) or [])
    if touched and post_surface and not (touched & post_surface):
        return {"rollback_allowed": False, "block_code": "deploy_surface_unrelated_red"}
    return {"rollback_allowed": True, "block_code": "deploy_surface_regression"}


def run_fail_fast_steps(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    return {
        "ok": False,
        "reason": "approval_required",
        "message": "This orchestrator only runs public actions after separate trusted human approval.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build and validate News-Grasp deploy recovery manifests.")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--approval", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not args.manifest:
        payload = run_fail_fast_steps()
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    approval = load_trusted_human_approval(args.approval) if args.approval else None
    result = validate_manifest_and_approval(manifest, approval)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
