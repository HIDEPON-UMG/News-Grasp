from __future__ import annotations

import importlib
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _guard():
    return importlib.import_module("tools.news_grasp_completion_guard")


def _manifest() -> dict:
    return {
        "schemaVersion": "NEWS_GRASP_PUBLISH_COMPLETE_V2",
        "date": "2026-08-13",
        "ok": True,
        "public_status": "green",
        "scheduled_attempt_status": "failed_then_recovered",
        "recovery_attempt_status": "succeeded",
        "source_commit": "a" * 40,
        "artifact_commit": "b" * 40,
        "publish_commit": "c" * 40,
        "publish": {"ok": True, "deploy_head": "c" * 40},
        "distribution_artifacts": {"missing": []},
        "notification": {"ok": True},
        "podcasts": {"primary": {"ok": True}, "deepdive": {"ok": True}},
        "live_runner_readiness": {
            "ok": True,
            "next_run_readiness": {"ok": True},
        },
    }


def _runner_state() -> dict:
    return {
        "date": "2026-08-13",
        "status": "publish_complete",
        "exit_code": 0,
        "scheduled_attempt_status": "failed_then_recovered",
        "recovery_attempt_status": "succeeded",
        "publish_commit": "c" * 40,
    }


def test_completion_guard_accepts_distinct_commit_roles_and_exact_slo_boundary() -> None:
    guard = _guard()
    result = guard.evaluate(
        _manifest(),
        _runner_state(),
        "2026-08-13",
        audit_accepted_at="2026-08-13T06:40:00+09:00",
        public_green_at="2026-08-13T07:25:00+09:00",
        done_at="2026-08-13T07:40:00+09:00",
    )

    assert result["schemaVersion"] == "NEWS_GRASP_640_COMPLETION_GUARD_V1"
    assert result["ok"] is True
    assert result["scheduled_attempt_status"] == "failed_then_recovered"
    assert result["recovery_attempt_status"] == "succeeded"
    assert result["public_status"] == "green"
    assert result["runner_status"] == "publish_complete"
    assert result["slo"]["postGreenMinutes"] == 15
    assert result["slo"]["overallMinutes"] == 60


def test_completion_guard_accepts_recovered_public_green_with_historical_missed_run() -> None:
    guard = _guard()
    manifest = _manifest()
    manifest["live_runner_readiness"] = {
        "ok": False,
        "reason": "scheduled_task_missed_runs",
        "last_scheduled_attempt": {"status": "failed", "last_task_result": 1},
        "next_run_readiness": {
            "ok": False,
            "reasonCode": "scheduled_task_missed_runs",
        },
    }

    result = guard.evaluate(
        manifest,
        _runner_state(),
        "2026-08-13",
        audit_accepted_at="2026-08-13T06:40:00+09:00",
        public_green_at="2026-08-13T07:25:00+09:00",
        done_at="2026-08-13T07:40:00+09:00",
    )

    assert result["ok"] is True
    assert "live_runner_readiness_not_ok" not in result["failures"]
    assert "next_run_readiness_not_ok" not in result["failures"]


def test_completion_guard_rejects_legacy_manifest_and_commit_role_substitution() -> None:
    guard = _guard()
    manifest = _manifest()
    manifest.pop("schemaVersion")
    manifest["publish_commit"] = manifest["source_commit"]

    result = guard.evaluate(
        manifest,
        _runner_state(),
        "2026-08-13",
        audit_accepted_at="2026-08-13T06:40:00+09:00",
        public_green_at="2026-08-13T07:00:00+09:00",
        done_at="2026-08-13T07:05:00+09:00",
    )

    assert result["ok"] is False
    assert "publish_complete_schema_invalid" in result["failures"]
    assert "publish_commit_deploy_head_mismatch" in result["failures"]


def test_completion_guard_records_post_green_and_overall_slo_overrun_as_debt() -> None:
    guard = _guard()
    result = guard.evaluate(
        _manifest(),
        _runner_state(),
        "2026-08-13",
        audit_accepted_at="2026-08-13T06:40:00+09:00",
        public_green_at="2026-08-13T07:30:00+09:00",
        done_at="2026-08-13T07:46:00+09:00",
    )

    assert result["ok"] is True
    assert "post_green_slo_exceeded" in result["slo"]["failures"]
    assert "overall_slo_exceeded" in result["slo"]["failures"]


def test_completion_guard_rejects_clock_reversal_and_missing_clock() -> None:
    guard = _guard()
    reversed_result = guard.evaluate(
        _manifest(),
        _runner_state(),
        "2026-08-13",
        audit_accepted_at="2026-08-13T06:40:00+09:00",
        public_green_at="2026-08-13T07:20:00+09:00",
        done_at="2026-08-13T07:10:00+09:00",
    )
    missing_result = guard.evaluate(
        _manifest(),
        _runner_state(),
        "2026-08-13",
    )

    assert "slo_clock_order_invalid" in reversed_result["failures"]
    assert "slo_clock_missing" in missing_result["failures"]


def test_completion_guard_rejects_future_self_reported_clocks() -> None:
    guard = _guard()
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    result = guard.evaluate(
        _manifest(),
        _runner_state(),
        "2026-08-13",
        audit_accepted_at=future.isoformat(),
        public_green_at=(future + timedelta(minutes=1)).isoformat(),
        done_at=(future + timedelta(minutes=2)).isoformat(),
    )

    assert result["ok"] is False
    assert "slo_clock_future_invalid" in result["failures"]


def test_20260813_historical_four_hour_shape_fails_and_repaired_replay_passes() -> None:
    guard = _guard()
    observed = guard.evaluate(
        _manifest(),
        _runner_state(),
        "2026-08-13",
        audit_accepted_at="2026-08-13T06:40:00+09:00",
        public_green_at="2026-08-13T10:30:00+09:00",
        done_at="2026-08-13T10:40:00+09:00",
    )
    repaired = guard.evaluate(
        _manifest(),
        _runner_state(),
        "2026-08-13",
        audit_accepted_at="2026-08-13T06:40:00+09:00",
        public_green_at="2026-08-13T07:25:00+09:00",
        done_at="2026-08-13T07:40:00+09:00",
    )

    assert observed["ok"] is True
    assert observed["slo"]["overallMinutes"] == 240
    assert "overall_slo_exceeded" in observed["slo"]["failures"]
    assert repaired["ok"] is True


def test_failed_guard_replaces_stale_green_output(tmp_path: Path, monkeypatch) -> None:
    guard = _guard()
    artifact_root = tmp_path / "artifact"
    receipt_path = (
        artifact_root
        / "build"
        / "publish-complete"
        / "2026-08-13.finalization.json"
    )
    receipt_path.parent.mkdir(parents=True)
    receipt_path.write_text("{}", encoding="utf-8")
    output = receipt_path.parent / "2026-08-13.automation-guard.json"
    output.write_text(json.dumps({"ok": True}), encoding="utf-8")
    failed = {
        "schemaVersion": "NEWS_GRASP_640_COMPLETION_GUARD_V1",
        "ok": False,
        "failures": ["post_green_slo_exceeded"],
    }
    receipt = {
        "issueDate": "2026-08-13",
        "completionGuardOutputPath": str(output.resolve()),
    }
    monkeypatch.setattr(
        guard,
        "evaluate_finalization_receipt",
        lambda _path, **_kwargs: (failed, receipt),
    )

    roots = [tmp_path / name for name in ("ops", "runtime", "live")]
    for root in roots:
        root.mkdir()
    runner_state = roots[2] / "news-grasp-runner-state.json"
    runner_script = roots[0] / "runner.ps1"
    runner_state.write_text("{}", encoding="utf-8")
    runner_script.write_text("runner", encoding="utf-8")
    assert guard.main(
        [
            "--finalization-receipt",
            str(receipt_path),
            "--artifact-root",
            str(artifact_root),
            "--ops-root",
            str(roots[0]),
            "--production-runtime-root",
            str(roots[1]),
            "--live-bin-root",
            str(roots[2]),
            "--runner-state",
            str(runner_state),
            "--runner-script",
            str(runner_script),
        ]
    ) == 2
    assert json.loads(output.read_text(encoding="utf-8"))["ok"] is False


def test_p01_automation_guard_is_stdout_only(tmp_path: Path) -> None:
    root = Path(__file__).parents[1]
    script = root / "automation" / "news-grasp-6-40" / "completion_guard.py"
    manifest_path = tmp_path / "publish-complete.json"
    state_path = tmp_path / "runner-state.json"
    manifest_path.write_text(json.dumps(_manifest()), encoding="utf-8")
    state_path.write_text(json.dumps(_runner_state()), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--issue-date",
            "2026-08-13",
            "--manifest",
            str(manifest_path),
            "--runner-state",
            str(state_path),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert completed.returncode == 0
    assert json.loads(completed.stdout)["ok"] is True
    assert not list(tmp_path.glob("*.automation-guard.json"))
    source = script.read_text(encoding="utf-8")
    assert "--output" not in source
    assert "write_text" not in source
    assert "write_bytes" not in source
    assert "write_atomic" not in source


def test_p01_automation_assets_bind_projection_and_single_writer() -> None:
    root = Path(__file__).parents[1]
    manifest = json.loads(
        (root / "config" / "news_grasp_automation_assets_v2.json").read_text(
            encoding="utf-8"
        )
    )
    rows = {
        row["sourcePath"]: row
        for row in manifest["assets"]
        if row["sourcePath"].startswith("automation/news-grasp-6-40/")
    }
    assert rows["automation/news-grasp-6-40/automation.toml.template"]["installPath"] == (
        "automations/news-grasp-6-40/automation.toml"
    )
    assert rows["automation/news-grasp-6-40/completion_guard.py"]["installPath"] == (
        "automations/news-grasp-6-40/completion_guard.py"
    )

    template = (
        root / "automation" / "news-grasp-6-40" / "automation.toml.template"
    ).read_text(encoding="utf-8")
    assert 'model = "gpt-5.6-luna"' in template
    assert 'reasoning_effort = "max"' in template
    assert "stdout" in template
    assert "--output" not in template

    projection = (
        root / "automation" / "news-grasp-6-40" / "completion_guard.py"
    ).read_text(encoding="utf-8")
    product_guard = (root / "tools" / "news_grasp_completion_guard.py").read_text(
        encoding="utf-8"
    )
    installer = (root / "scripts" / "ops" / "install-news-grasp-ops.ps1").read_text(
        encoding="utf-8-sig"
    )
    assert "write_atomic_json" in product_guard
    assert "write_atomic_json" not in projection
    assert "Assert-NewsGraspAutomationProjectionAsset" in installer


def test_completion_guard_rejects_readiness_proof_after_deadman_drift(tmp_path: Path) -> None:
    guard = _guard()
    descriptor = tmp_path / "descriptor.json"
    deadman = tmp_path / "deadman.ps1"
    descriptor.write_text("descriptor-v1\n", encoding="utf-8")
    deadman.write_text("deadman-v1\n", encoding="utf-8")
    proof = {
        "schemaVersion": "NEXT_RUN_READINESS_V1",
        "generationId": "generation-001",
        "descriptorPath": str(descriptor),
        "descriptorSha256": hashlib.sha256(descriptor.read_bytes()).hexdigest(),
        "taskDefinitionSha256": hashlib.sha256(b"task-v1").hexdigest(),
        "deadmanPath": str(deadman),
        "deadmanIdentitySha256": hashlib.sha256(deadman.read_bytes()).hexdigest(),
    }
    assert guard._readiness_freshness_is_current({"freshness": proof}) is True
    deadman.write_text("deadman-v2\n", encoding="utf-8")
    assert guard._readiness_freshness_is_current({"freshness": proof}) is False


def test_parallel_hotfix_completion_ok_with_slo_debt_exits_zero_and_non_ok_exits_two(
    monkeypatch, tmp_path: Path
) -> None:
    """SLO debtはsidecarへ残し、公開okだけはCLI成功として扱う。"""
    guard = _guard()
    artifact_root = tmp_path / "artifact"
    output = artifact_root / "build" / "publish-complete" / "2026-08-21.automation-guard.json"
    calls = iter((True, False))
    manifest = {
        "live_runner_readiness": {"ok": True},
    }
    state = {"updated_at": "2026-08-21T08:00:00+09:00"}

    def fake_evaluate(*_args, **_kwargs):
        ok = next(calls)
        return (
            {
                "schemaVersion": "NEWS_GRASP_640_COMPLETION_GUARD_V1",
                "ok": ok,
                "scheduled_attempt_status": "failed_then_recovered",
                "recovery_attempt_status": "succeeded",
            },
            {
                "issueDate": "2026-08-21",
                "manifestPath": str(tmp_path / "manifest.json"),
                "publicGreenAt": "2026-08-21T07:00:00+09:00",
                "auditAcceptedAt": "2026-08-21T06:40:00+09:00",
                "completionGuardOutputPath": str(output),
                "receiptSha256": "a" * 64,
                "_validatedManifestSnapshot": manifest,
                "_validatedRunnerStateSnapshot": state,
            },
        )

    monkeypatch.setattr(guard, "evaluate_finalization_receipt", fake_evaluate)
    monkeypatch.setattr(
        guard,
        "build_completion_outcome_envelope",
        lambda **_kwargs: {
            "schemaVersion": "COMPLETION_OUTCOME_ENVELOPE_V1",
            "sloStatus": "slo_failed",
            "readinessDebt": None,
            "processExitCode": 2,
        },
    )
    artifact_root.mkdir(parents=True)
    common = [
        "--finalization-receipt", str(tmp_path / "receipt.json"),
        "--artifact-root", str(artifact_root),
        "--ops-root", str(tmp_path / "ops"),
        "--production-runtime-root", str(tmp_path / "runtime"),
        "--live-bin-root", str(tmp_path / "live"),
        "--runner-state", str(tmp_path / "state.json"),
        "--runner-script", str(tmp_path / "runner.ps1"),
    ]
    assert guard.main(common) == 0
    envelope_path = artifact_root / "build" / "publish-complete" / "2026-08-21.completion-outcome.json"
    assert json.loads(envelope_path.read_text(encoding="utf-8"))["sloStatus"] == "slo_failed"
    assert guard.main(common) == 2
