from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from tools import deploy_recovery_orchestrator as deploy


NOW = datetime(2026, 6, 26, 12, 0, tzinfo=timezone.utc)
BASE = "a" * 40
CANDIDATE = "b" * 40


def _write(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _manifest(tmp_path: Path) -> dict:
    file_path = _write(tmp_path / "docs" / "index.html", "<html></html>")
    return deploy.build_deploy_manifest(
        goal_id="news-grasp-r7",
        repo_root=tmp_path,
        base_sha=BASE,
        candidate_sha=CANDIDATE,
        remote="origin",
        destination_ref="refs/heads/main",
        ahead_commits=[CANDIDATE],
        changed_files=[file_path],
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=30),
    )


def _approval(manifest: dict) -> dict:
    return {
        "schema_version": 1,
        "approved_by": "human",
        "approved_by_user_text": "deploy this manifest",
        "source": "transcript",
        "manifest_sha256": deploy.stable_json_sha256(manifest),
        "candidate_sha": manifest["candidate_sha"],
        "destination_ref": manifest["destination_ref"],
        "approved_at": NOW.isoformat(),
        "expires_at": (NOW + timedelta(minutes=10)).isoformat(),
    }


def test_deploy_manifest_records_exact_changed_file_digest(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)

    assert manifest["deploy_type"] == "github_pages_actions_docs_from_main"
    assert manifest["workflow_file"] == ".github/workflows/deploy-pages.yml"
    assert manifest["changed_files"][0]["path"] == "docs/index.html"
    assert len(manifest["changed_files"][0]["sha256"]) == 64


def test_manifest_only_approval_is_rejected(tmp_path: Path) -> None:
    result = deploy.validate_manifest_and_approval(_manifest(tmp_path), None, now=NOW)

    assert result["ok"] is False
    assert result["reason"] == "trusted_human_approval_missing"


def test_stale_or_tampered_approval_is_rejected(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    stale = _approval(manifest)
    stale["expires_at"] = (NOW - timedelta(seconds=1)).isoformat()
    tampered = _approval(manifest)
    tampered["manifest_sha256"] = "0" * 64

    assert deploy.validate_manifest_and_approval(manifest, stale, now=NOW)["reason"] == "approval_expired"
    assert deploy.validate_manifest_and_approval(manifest, tampered, now=NOW)["reason"] == "manifest_sha256_mismatch"


def test_valid_approval_accepts_exact_manifest(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)

    result = deploy.validate_manifest_and_approval(manifest, _approval(manifest), now=NOW)

    assert result["ok"] is True


def test_unrelated_red_is_not_rollback_regression() -> None:
    result = deploy.classify_deploy_surface_regression(
        pre_deploy={"ok": True, "surface": ["docs/index.html"]},
        post_deploy={"ok": False, "reason": "oauth_consent_required", "surface": ["youtube"]},
        manifest={"candidate_sha": CANDIDATE},
        workflow_sha=CANDIDATE,
        remote_head_sha=CANDIDATE,
        now=NOW,
        window_start=NOW - timedelta(minutes=1),
        window_end=NOW + timedelta(minutes=1),
    )

    assert result["rollback_allowed"] is False
    assert result["block_code"] == "deploy_surface_unrelated_red"


def test_load_trusted_human_approval_reads_json(tmp_path: Path) -> None:
    path = tmp_path / "approval.json"
    path.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")

    assert deploy.load_trusted_human_approval(path)["schema_version"] == 1
