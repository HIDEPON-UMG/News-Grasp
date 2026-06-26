from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from tools import verify_public_surface
from tools.recovery_state import (
    build_recovery_proof,
    canonical_required_surface_digest,
    validate_recovery_proof,
    write_recovery_proof,
)


NOW = datetime(2026, 6, 26, 12, 0, tzinfo=timezone.utc)
HEAD = "a" * 40


def _proof(tmp_path: Path, **overrides: object) -> dict:
    proof = build_recovery_proof(
        issue_date="2026-06-26",
        repo_root=tmp_path,
        head_sha=HEAD,
        remote_head_sha=HEAD,
        required_surface_digest=canonical_required_surface_digest(["docs/index.html", "docs/publish-status.json"]),
        overall_status="green",
        external_block_code="none",
        external_block_evidence={},
        checked_at=NOW,
        expires_at=NOW + timedelta(minutes=30),
        public_urls=["https://example.test/News-Grasp/"],
        publish_complete_manifest={"ok": True},
        errors=[],
    )
    proof.update(overrides)
    return proof


def test_recovery_proof_green_requires_no_external_block(tmp_path: Path) -> None:
    result = validate_recovery_proof(
        _proof(tmp_path),
        expected_issue_date="2026-06-26",
        expected_repo_root=tmp_path,
        expected_head_sha=HEAD,
        expected_remote_head_sha=HEAD,
        expected_required_surface_digest=canonical_required_surface_digest(["docs/index.html", "docs/publish-status.json"]),
        now=NOW,
    )

    assert result["ok"] is True
    assert result["exit_code"] == 0


def test_recovery_proof_rejects_stale_wrong_repo_wrong_head_and_legacy(tmp_path: Path) -> None:
    cases = [
        _proof(tmp_path, expires_at=(NOW - timedelta(seconds=1)).isoformat()),
        _proof(tmp_path, repo_root=str(tmp_path / "other")),
        _proof(tmp_path, head_sha="b" * 40),
        _proof(tmp_path, recovery_green=True),
    ]

    for proof in cases:
        result = validate_recovery_proof(
            proof,
            expected_issue_date="2026-06-26",
            expected_repo_root=tmp_path,
            expected_head_sha=HEAD,
            expected_remote_head_sha=HEAD,
            expected_required_surface_digest=canonical_required_surface_digest(["docs/index.html", "docs/publish-status.json"]),
            now=NOW,
        )
        assert result["ok"] is False
        assert result["exit_code"] == 1


def test_recovery_proof_yellow_allows_single_known_external_block(tmp_path: Path) -> None:
    proof = _proof(
        tmp_path,
        overall_status="yellow",
        external_block_code="google_api_external",
        external_block_evidence={"external_system": "google-api", "observed_error_code": "403"},
    )

    result = validate_recovery_proof(
        proof,
        expected_issue_date="2026-06-26",
        expected_repo_root=tmp_path,
        expected_head_sha=HEAD,
        expected_remote_head_sha=HEAD,
        expected_required_surface_digest=canonical_required_surface_digest(["docs/index.html", "docs/publish-status.json"]),
        now=NOW,
    )

    assert result["ok"] is False
    assert result["exit_code"] == 10
    assert result["external_block_code"] == "google_api_external"


def test_write_recovery_proof_is_json(tmp_path: Path) -> None:
    path = tmp_path / "proof.json"
    write_recovery_proof(path, _proof(tmp_path))

    assert json.loads(path.read_text(encoding="utf-8"))["overall_status"] == "green"


def test_verify_public_surface_returns_to_full_publish_complete_for_google_api(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(verify_public_surface, "_local_head", lambda _repo: HEAD)
    monkeypatch.setattr(verify_public_surface, "_remote_head", lambda *_args: HEAD)
    monkeypatch.setattr(
        verify_public_surface.publish_inventory,
        "required_published_repair_artifacts",
        lambda _date: ["docs/index.html", "docs/publish-status.json"],
    )
    monkeypatch.setattr(
        verify_public_surface.daily_self_heal,
        "verify_publish_complete",
        lambda **_kwargs: {"ok": False, "reason": "google_api_external"},
    )

    result = verify_public_surface.verify_public_surface(
        date="2026-06-26",
        repo_root=tmp_path,
        remote="origin",
        branch="main",
        public_base_url="https://example.test/News-Grasp/",
        wait_sec=0,
        poll_sec=30,
    )

    assert result["overall_status"] == "yellow"
    assert result["exit_code"] == 10
    assert result["proof"]["external_block_code"] == "google_api_external"
