from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from tools import daily_self_heal, publish_inventory
from tools.recovery_state import (
    canonical_required_surface_digest,
    build_recovery_proof,
    write_recovery_proof,
)


YELLOW_REASONS = {
    "google_api_external",
    "oauth_consent_required",
    "youtube_quota_or_permission",
    "github_pages_external",
    "github_pages_deploy_workflow_not_success",
    "deploy_workflow_not_success",
}


def _run_git(repo_root: Path, args: list[str]) -> str:
    cp = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return cp.stdout.strip() if cp.returncode == 0 else ""


def _local_head(repo_root: Path) -> str:
    return _run_git(repo_root, ["rev-parse", "HEAD"])


def _remote_head(repo_root: Path, remote: str, branch: str) -> str:
    output = _run_git(repo_root, ["ls-remote", remote, f"refs/heads/{branch}"])
    return output.split()[0] if output.split() else ""


def probe_readiness(*, root: Path | str, expected_paths: list[str], generation_id: str) -> dict[str, Any]:
    """公開面確認のread-only入口。scheduled gateからpublish mutationを呼ばない。"""
    from tools.news_grasp_operational_contract import probe_readiness as _probe

    return _probe(root=root, expected_paths=expected_paths, generation_id=generation_id)


def verify_public_surface(
    *,
    date: str,
    repo_root: Path,
    ops_repo_root: Path | None = None,
    notification_state_path: Path | None = None,
    producer_state_path: Path | None = None,
    remote: str,
    branch: str,
    public_base_url: str,
    wait_sec: int,
    poll_sec: int,
    write_proof: Path | None = None,
) -> dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    ops_repo_root = Path(ops_repo_root).resolve() if ops_repo_root is not None else repo_root
    notification_state_path = (
        Path(notification_state_path).resolve()
        if notification_state_path is not None
        else repo_root / "build" / "notification" / f"{date}.json"
    )
    head = _local_head(repo_root)
    remote_head = _remote_head(repo_root, remote, branch)
    required_surfaces = publish_inventory.required_published_repair_artifacts(date)
    required_digest = canonical_required_surface_digest(required_surfaces)
    manifest = daily_self_heal.verify_publish_complete(
        repo_root=repo_root,
        ops_repo_root=ops_repo_root,
        date=date,
        remote=remote,
        branch=branch,
        public_base_url=public_base_url,
        wait_sec=wait_sec,
        poll_sec=poll_sec,
        notification_state_path=notification_state_path,
        producer_state_path=producer_state_path,
    )

    checked_at = datetime.now(timezone.utc)
    reason = str(manifest.get("reason") or "")
    if manifest.get("ok"):
        status = "green"
        exit_code = 0
        external_code = "none"
        errors: list[str] = []
        external_evidence: dict[str, Any] = {}
    elif reason in YELLOW_REASONS:
        status = "yellow"
        exit_code = 10
        external_code = reason
        errors = [reason]
        external_evidence = {
            "external_system": "google-api" if reason == "google_api_external" else "public-surface",
            "observed_error_code": reason,
            "source_command": "python -m tools.verify_public_surface",
            "detail": reason,
            "observed_at": checked_at.isoformat(),
        }
    else:
        status = "red"
        exit_code = 1
        external_code = "none"
        errors = [reason or "publish_complete_failed"]
        external_evidence = {}

    proof = build_recovery_proof(
        issue_date=date,
        repo_root=repo_root,
        head_sha=head,
        remote_head_sha=remote_head,
        required_surface_digest=required_digest,
        overall_status=status,
        external_block_code=external_code,
        external_block_evidence=external_evidence,
        checked_at=checked_at,
        expires_at=checked_at + timedelta(minutes=30),
        public_urls=[public_base_url],
        publish_complete_manifest=manifest,
        errors=errors,
    )
    if write_proof is not None:
        write_recovery_proof(write_proof, proof)
    return {
        "ok": status == "green",
        "overall_status": status,
        "public_status": str(manifest.get("public_status") or status),
        "scheduled_attempt_status": str(manifest.get("scheduled_attempt_status") or "unknown"),
        "recovery_attempt_status": str(manifest.get("recovery_attempt_status") or "not_verified"),
        "exit_code": exit_code,
        "reason": reason,
        "required_surfaces": required_surfaces,
        "proof": proof,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify News-Grasp public surface and write recovery proof.")
    parser.add_argument("--date", required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--ops-repo-root", type=Path, default=None)
    parser.add_argument("--notification-state", type=Path, default=None)
    parser.add_argument("--producer-state", type=Path, default=None)
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--public-base-url", required=True)
    parser.add_argument("--wait-sec", type=int, default=600)
    parser.add_argument("--poll-sec", type=int, default=30)
    parser.add_argument("--write-proof", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result = verify_public_surface(
        date=args.date,
        repo_root=args.repo_root,
        ops_repo_root=args.ops_repo_root,
        notification_state_path=args.notification_state,
        producer_state_path=args.producer_state,
        remote=args.remote,
        branch=args.branch,
        public_base_url=args.public_base_url,
        wait_sec=args.wait_sec,
        poll_sec=args.poll_sec,
        write_proof=args.write_proof,
    )
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text if args.json else text)
    return int(result["exit_code"])


if __name__ == "__main__":
    sys.exit(main())
