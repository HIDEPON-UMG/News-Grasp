from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools import news_grasp_p08_evidence as evidence


def test_design_and_route_seals_are_recomputed(tmp_path: Path) -> None:
    design = evidence.build_design(
        workspace_root=tmp_path,
        thread_id="01a00a21-86b9-7800-ab39-3845476e5e44",
        task_root_user_event_hash="a" * 64,
        latest_actual_user_event_hash="b" * 64,
    )
    assert design["designSha256"] == evidence.canonical_sha256(
        {key: value for key, value in design.items() if key != "designSha256"}
    )
    route = evidence.build_route_manifest(
        workspace_root=tmp_path,
        task_identity=design["taskIdentity"],
        route_specs=[],
        required_route_ids=[],
    )
    assert route["manifestSha256"] == evidence.canonical_sha256(
        {key: value for key, value in route.items() if key != "manifestSha256"}
    )


def test_review_rejects_route_source_drift(tmp_path: Path) -> None:
    source = tmp_path / "route.ps1"
    source.write_text("gate\nlaunch\n", encoding="utf-8")
    route = evidence.build_route_manifest(
        workspace_root=tmp_path,
        task_identity="a" * 64,
        route_specs=[
            ("r1", source, "gate", "launch"),
        ],
        required_route_ids=["r1"],
    )
    source.write_text("launch\ngate\n", encoding="utf-8")
    with pytest.raises(evidence.P08EvidenceError, match="HIGH_COST_ROUTE_SOURCE_DRIFT"):
        evidence.validate_route_manifest(route, workspace_root=tmp_path, task_identity="a" * 64, required_route_ids=["r1"])


def test_command_receipt_is_green_only_for_zero_exit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class _Process:
        returncode = 0

        def wait(self, timeout: int) -> None:
            return None

    monkeypatch.setattr(evidence.subprocess, "Popen", lambda *args, **kwargs: _Process())
    receipt = evidence.run_verification_command(
        schema=evidence.STATIC_SCHEMA,
        command=["python", "-m", "pytest"],
        cwd=tmp_path,
    )
    assert receipt["status"] == "Green"
    assert receipt["receiptSha256"] == evidence.canonical_sha256(
        {key: value for key, value in receipt.items() if key != "receiptSha256"}
    )


def test_caller_evidence_order_excludes_red_execution(tmp_path: Path) -> None:
    paths = {}
    for name in evidence.CALLER_EVIDENCE_KINDS:
        path = tmp_path / f"{name}.json"
        path.write_text("{}\n", encoding="utf-8")
        paths[name] = path
    rows = evidence.caller_evidence_bindings(paths)
    assert [row["kind"] for row in rows] == list(evidence.CALLER_EVIDENCE_KINDS)
    assert "red_suite_execution" not in [row["kind"] for row in rows]
    for path in paths.values():
        path.unlink()
