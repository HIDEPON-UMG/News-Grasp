from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tools import news_grasp_recovery_receipts as receipts
from tools import news_grasp_completion_guard


def _write_sealed(path: Path, body: dict) -> dict:
    value = dict(body)
    value["receiptSha256"] = hashlib.sha256(receipts.canonical_bytes(value)).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return value


def _roots(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    roots = tuple(tmp_path / name for name in ("artifact", "ops", "runtime", "live"))
    for root in roots:
        root.mkdir()
    return roots  # type: ignore[return-value]


def _authority_witness(authority: dict, failure: dict) -> dict:
    value = {
        "schemaVersion": "SCHEDULED_RECOVERY_AUTHORITY_LEDGER_WITNESS_V1",
        "issueDate": "2026-08-13",
        "authorityReceiptSha256": authority["receiptSha256"],
        "failureReceiptSha256": failure["receiptSha256"],
        "ledgerEventSequence": 3,
        "ledgerEventHash": "d" * 64,
    }
    value["receiptSha256"] = hashlib.sha256(receipts.canonical_bytes(value)).hexdigest()
    return value


def test_finalization_receipt_rejects_manifest_mutation(
    tmp_path: Path, monkeypatch
) -> None:
    artifact, ops, runtime, live = _roots(tmp_path)
    manifest = artifact / "build" / "publish-complete" / "2026-08-13.json"
    manifest.parent.mkdir(parents=True)
    failure = _write_sealed(
        artifact / "build" / "failure.json",
        {
            "schemaVersion": "SCHEDULED_FAILURE_RECEIPT_V1",
            "issueDate": "2026-08-13",
            "scheduledAttemptStatus": "failed",
        },
    )
    authority = _write_sealed(
        artifact / "build" / "authority.json",
        {
            "schemaVersion": "SCHEDULED_RECOVERY_AUTHORITY_V1",
            "issueDate": "2026-08-13",
            "failureReceiptSha256": failure["receiptSha256"],
        },
    )
    witness = _authority_witness(authority, failure)
    monkeypatch.setattr(
        receipts, "_validate_authority_via_broker", lambda **_kwargs: witness
    )
    runner = live / "news-grasp-runner.ps1"
    runner.write_text("runner", encoding="utf-8")
    now = datetime.now(timezone.utc)
    manifest_value = {
        "schemaVersion": "NEWS_GRASP_PUBLISH_COMPLETE_V2",
        "date": "2026-08-13",
        "ok": True,
        "public_status": "green",
        "scheduled_attempt_status": "failed_then_recovered",
        "recovery_attempt_status": "succeeded",
        "source_commit": "a" * 40,
        "artifact_commit": "b" * 40,
        "publish_commit": "c" * 40,
        "verified_at": now.isoformat(),
        "publish": {"ok": True, "deploy_head": "c" * 40},
        "distribution_artifacts": {"missing": []},
        "notification": {"ok": True},
        "podcasts": {"primary": {"ok": True}, "deepdive": {"ok": True}},
        "live_runner_readiness": {
            "ok": True,
            "next_run_readiness": {"ok": True},
        },
    }
    manifest.write_text(json.dumps(manifest_value), encoding="utf-8")
    execution = receipts.create_recovery_execution_receipt(
        issue_date="2026-08-13",
        artifact_root=artifact,
        ops_root=ops,
        production_runtime_root=runtime,
        live_bin_root=live,
        runner_state_path=live / "news-grasp-runner-state.json",
        runner_script_path=runner,
        recovery_authority_path=artifact / "build" / "authority.json",
        recovery_authority=authority,
        scheduled_failure_receipt_path=artifact / "build" / "failure.json",
        scheduled_failure_receipt=failure,
        authority_ledger_witness=witness,
        audit_accepted_at=(now - timedelta(minutes=10)).isoformat(),
    )
    execution_path = artifact / "build" / "recovery-authority" / "execution.json"
    execution_path.parent.mkdir(parents=True)
    execution_path.write_text(json.dumps(execution), encoding="utf-8")
    receipts.consume_once(receipt=execution, live_bin_root=live, kind="execution")
    from tools import daily_self_heal

    producer_snapshot = artifact / "build" / "recovery-authority" / "producer.json"
    producer_lineage = daily_self_heal._producer_lineage_expected(
        repo_root=artifact,
        ops_root=ops,
        date="2026-08-13",
        run_intent="ScheduledRecoveryFull",
        run_id="fixture",
    )
    producer_snapshot.write_text(
        json.dumps(
            {
                "date": "2026-08-13",
                "run_id": "fixture",
                "run_intent": "ScheduledRecoveryFull",
                "repo_dir": str(artifact.resolve()),
                **producer_lineage,
            }
        ),
        encoding="utf-8",
    )
    receipt = receipts.create_finalization_receipt(
        issue_date="2026-08-13",
        artifact_root=artifact,
        ops_root=ops,
        production_runtime_root=runtime,
        live_bin_root=live,
        runner_state_path=live / "news-grasp-runner-state.json",
        runner_script_path=runner,
        manifest_path=manifest,
        manifest=manifest_value,
        recovery_authority_path=artifact / "build" / "authority.json",
        recovery_authority=authority,
        scheduled_failure_receipt_path=artifact / "build" / "failure.json",
        scheduled_failure_receipt=failure,
        authority_ledger_witness=witness,
        execution_receipt_path=execution_path,
        execution_receipt=execution,
        producer_state_path=producer_snapshot,
        producer_state_sha256=receipts.file_sha256(producer_snapshot),
        audit_accepted_at=(now - timedelta(minutes=10)).isoformat(),
    )
    receipt_path = artifact / "build" / "publish-complete" / "2026-08-13.finalization.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    (live / "news-grasp-runner-state.json").write_text(
        json.dumps(
            {
                "date": "2026-08-13",
                "status": "publish_complete",
                "exit_code": 0,
                "updated_at": now.isoformat(),
                "scheduled_attempt_status": "failed_then_recovered",
                "recovery_attempt_status": "succeeded",
                "publish_commit": "c" * 40,
                "publish_manifest_path": str(manifest),
                "recovery_finalization_receipt_path": str(receipt_path.resolve()),
                "recovery_finalization_receipt_sha256": receipt["receiptSha256"],
                "scheduled_failure_receipt_path": str(
                    (artifact / "build" / "failure.json").resolve()
                ),
                "scheduled_failure_receipt_sha256": failure["receiptSha256"],
            }
        ),
        encoding="utf-8",
    )
    receipts.consume_once(receipt=receipt, live_bin_root=live, kind="finalization")
    receipts.mark_finalization_state_applied(receipt=receipt, live_bin_root=live)
    valid = receipts.validate_finalization_receipt(
        receipt_path=receipt_path,
        issue_date="2026-08-13",
        artifact_root=artifact,
        ops_root=ops,
        production_runtime_root=runtime,
        live_bin_root=live,
        runner_state_path=live / "news-grasp-runner-state.json",
        runner_script_path=runner,
    )
    assert valid["publishCommit"] == "c" * 40
    guard, guard_receipt = news_grasp_completion_guard.evaluate_finalization_receipt(
        receipt_path,
        artifact_root=artifact,
        ops_root=ops,
        production_runtime_root=runtime,
        live_bin_root=live,
        runner_state_path=live / "news-grasp-runner-state.json",
        runner_script_path=runner,
    )
    assert guard["ok"] is True
    assert guard_receipt["receiptSha256"] == receipt["receiptSha256"]
    manifest.write_text('{"ok":false}', encoding="utf-8")

    with pytest.raises(ValueError, match="FINALIZATION_MANIFEST_DRIFT"):
        receipts.validate_finalization_receipt(
            receipt_path=receipt_path,
            issue_date="2026-08-13",
            artifact_root=artifact,
            ops_root=ops,
            production_runtime_root=runtime,
            live_bin_root=live,
            runner_state_path=live / "news-grasp-runner-state.json",
            runner_script_path=runner,
        )


def test_control_plane_repair_receipt_is_consumed_by_drift_change(
    tmp_path: Path, monkeypatch
) -> None:
    artifact, ops, runtime, live = _roots(tmp_path)
    failure = _write_sealed(
        artifact / "build" / "failure.json",
        {
            "schemaVersion": "SCHEDULED_FAILURE_RECEIPT_V1",
            "issueDate": "2026-08-13",
            "scheduledAttemptStatus": "failed",
        },
    )
    authority = _write_sealed(
        artifact / "build" / "authority.json",
        {
            "schemaVersion": "SCHEDULED_RECOVERY_AUTHORITY_V1",
            "issueDate": "2026-08-13",
            "failureReceiptSha256": failure["receiptSha256"],
        },
    )
    witness = _authority_witness(authority, failure)
    monkeypatch.setattr(
        receipts, "_validate_authority_via_broker", lambda **_kwargs: witness
    )
    drift = {
        "reasonCode": "LIVE_BIN_DRIFT",
        "managedFiles": [
            {
                "name": "news-grasp-runner.ps1",
                "ops": {"sha256": "a" * 64},
                "runtime": {"sha256": "a" * 64},
                "live": {"sha256": "b" * 64},
            }
        ],
    }
    receipt = receipts.create_control_plane_repair_receipt(
        issue_date="2026-08-13",
        artifact_root=artifact,
        ops_root=ops,
        production_runtime_root=runtime,
        live_bin_root=live,
        preflight=drift,
        recovery_authority_path=artifact / "build" / "authority.json",
        recovery_authority=authority,
        authority_ledger_witness=witness,
    )
    receipt_path = artifact / "build" / "control-plane" / "repair.json"
    receipt_path.parent.mkdir(parents=True)
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    receipts.validate_control_plane_repair_receipt(
        receipt_path=receipt_path,
        issue_date="2026-08-13",
        artifact_root=artifact,
        ops_root=ops,
        production_runtime_root=runtime,
        live_bin_root=live,
        current_preflight=drift,
    )
    repaired = json.loads(json.dumps(drift))
    repaired["managedFiles"][0]["live"]["sha256"] = "a" * 64
    with pytest.raises(ValueError, match="CONTROL_PLANE_REPAIR_WITNESS_DRIFT"):
        receipts.validate_control_plane_repair_receipt(
            receipt_path=receipt_path,
            issue_date="2026-08-13",
            artifact_root=artifact,
            ops_root=ops,
            production_runtime_root=runtime,
            live_bin_root=live,
            current_preflight=repaired,
        )


def test_receipt_copy_cannot_replay_canonical_consumption(tmp_path: Path) -> None:
    *_, live = _roots(tmp_path)
    receipt = receipts._seal(
        {
            "schemaVersion": receipts.EXECUTION_SCHEMA,
            "issueDate": "2026-08-13",
            "nonce": "a" * 32,
        }
    )
    receipts.consume_once(receipt=receipt, live_bin_root=live, kind="execution")
    with pytest.raises(ValueError, match="RECOVERY_RECEIPT_ALREADY_CONSUMED"):
        receipts.consume_once(receipt=dict(receipt), live_bin_root=live, kind="execution")


def test_semantic_execution_reissue_is_rejected(tmp_path: Path) -> None:
    *_, live = _roots(tmp_path)
    body = {
        "schemaVersion": receipts.EXECUTION_SCHEMA,
        "issueDate": "2026-08-13",
        "recoveryAuthorityReceiptSha256": "b" * 64,
        "scheduledFailureReceiptSha256": "c" * 64,
        "artifactRoot": str(tmp_path / "artifact"),
        "opsRoot": str(tmp_path / "ops"),
        "nonce": "a" * 32,
    }
    first = receipts._seal(body)
    second = receipts._seal({**body, "nonce": "d" * 32})
    initial = receipts.consume_or_resume(
        receipt=first, live_bin_root=live, kind="execution"
    )
    resumed = receipts.consume_or_resume(
        receipt=first, live_bin_root=live, kind="execution"
    )
    assert initial["status"] == resumed["status"] == "consumed_pending_operation"

    with pytest.raises(ValueError, match="RECOVERY_RECEIPT_ALREADY_CONSUMED"):
        receipts.consume_or_resume(receipt=second, live_bin_root=live, kind="execution")
    receipts.mark_operation_applied(
        receipt=first, live_bin_root=live, kind="execution"
    )
    with pytest.raises(ValueError, match="RECOVERY_RECEIPT_ALREADY_CONSUMED"):
        receipts.consume_or_resume(receipt=first, live_bin_root=live, kind="execution")


def test_execution_reissue_with_new_runtime_identity_is_a_bounded_resume(
    tmp_path: Path,
) -> None:
    *_, live = _roots(tmp_path)
    body = {
        "schemaVersion": receipts.EXECUTION_SCHEMA,
        "issueDate": "2026-08-13",
        "recoveryAuthorityReceiptSha256": "b" * 64,
        "scheduledFailureReceiptSha256": "c" * 64,
        "artifactRoot": str(tmp_path / "artifact"),
        "opsRoot": str(tmp_path / "ops"),
        "artifactHead": "1" * 40,
        "opsHead": "2" * 40,
        "runnerSha256": "3" * 64,
        "nonce": "a" * 32,
    }
    first = receipts._seal(body)
    refreshed = receipts._seal(
        {
            **body,
            "artifactHead": "4" * 40,
            "nonce": "d" * 32,
        }
    )

    receipts.consume_or_resume(receipt=first, live_bin_root=live, kind="execution")
    resumed = receipts.consume_or_resume(
        receipt=refreshed, live_bin_root=live, kind="execution"
    )
    assert resumed["status"] == "consumed_pending_operation"


def test_finalization_parent_is_unique_but_exact_retry_is_idempotent(
    tmp_path: Path,
) -> None:
    *_, live = _roots(tmp_path)
    execution = receipts._seal(
        {
            "schemaVersion": receipts.EXECUTION_SCHEMA,
            "issueDate": "2026-08-13",
            "recoveryAuthorityReceiptSha256": "b" * 64,
            "scheduledFailureReceiptSha256": "c" * 64,
            "artifactRoot": str(tmp_path / "artifact"),
            "opsRoot": str(tmp_path / "ops"),
            "nonce": "a" * 32,
        }
    )
    first = receipts._seal(
        {
            "schemaVersion": receipts.FINALIZATION_SCHEMA,
            "issueDate": "2026-08-13",
            "executionReceiptSha256": execution["receiptSha256"],
            "nonce": "e" * 32,
        }
    )
    second = receipts._seal(
        {
            "schemaVersion": receipts.FINALIZATION_SCHEMA,
            "issueDate": "2026-08-13",
            "executionReceiptSha256": execution["receiptSha256"],
            "nonce": "f" * 32,
        }
    )

    initial = receipts.consume_finalization_chain(
        finalization=first, execution=execution, live_bin_root=live
    )
    retried = receipts.consume_finalization_chain(
        finalization=first, execution=execution, live_bin_root=live
    )
    assert initial["status"] == "consumed_pending_state"
    assert retried["status"] == "consumed_pending_state"
    applied = receipts.mark_operation_applied(
        receipt=execution, live_bin_root=live, kind="execution"
    )
    assert applied["status"] == "operation_applied"
    with pytest.raises(ValueError, match="RECOVERY_RECEIPT_ALREADY_CONSUMED"):
        receipts.consume_finalization_chain(
            finalization=second, execution=execution, live_bin_root=live
        )


def test_producer_lineage_validator_rejects_wrong_run_intent(tmp_path: Path) -> None:
    artifact, ops, *_ = _roots(tmp_path)
    from tools import daily_self_heal

    expected = daily_self_heal._producer_lineage_expected(
        repo_root=artifact,
        ops_root=ops,
        date="2026-08-13",
        run_intent="ScheduledRecoveryFull",
        run_id="recovery-run",
    )
    state = {
        "date": "2026-08-13",
        "run_id": "recovery-run",
        "run_intent": "ScheduledProduction",
        "repo_dir": str(artifact.resolve()),
        **expected,
    }

    with pytest.raises(ValueError, match="FINALIZATION_PRODUCER_LINEAGE_INVALID"):
        receipts.validate_producer_lineage(
            producer_state=state,
            issue_date="2026-08-13",
            artifact_root=artifact,
            ops_root=ops,
        )


def test_self_sealed_authority_is_rejected_without_broker_ledger(
    tmp_path: Path, monkeypatch
) -> None:
    artifact, ops, runtime, live = _roots(tmp_path)
    failure = _write_sealed(
        artifact / "build" / "failure.json",
        {
            "schemaVersion": "SCHEDULED_FAILURE_RECEIPT_V1",
            "issueDate": "2026-08-13",
            "scheduledAttemptStatus": "failed",
        },
    )
    authority = _write_sealed(
        artifact / "build" / "authority.json",
        {
            "schemaVersion": "SCHEDULED_RECOVERY_AUTHORITY_V1",
            "issueDate": "2026-08-13",
            "failureReceiptSha256": failure["receiptSha256"],
        },
    )
    witness = _authority_witness(authority, failure)
    drift = {"reasonCode": "LIVE_BIN_DRIFT", "managedFiles": []}
    receipt = receipts.create_control_plane_repair_receipt(
        issue_date="2026-08-13",
        artifact_root=artifact,
        ops_root=ops,
        production_runtime_root=runtime,
        live_bin_root=live,
        preflight=drift,
        recovery_authority_path=artifact / "build" / "authority.json",
        recovery_authority=authority,
        authority_ledger_witness=witness,
    )
    receipt_path = artifact / "build" / "control-plane" / "forged.json"
    receipt_path.parent.mkdir(parents=True)
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    monkeypatch.setattr(
        receipts,
        "_validate_authority_via_broker",
        lambda **_kwargs: (_ for _ in ()).throw(
            ValueError("RECOVERY_AUTHORITY_LEDGER_INVALID")
        ),
    )
    with pytest.raises(ValueError, match="RECOVERY_AUTHORITY_LEDGER_INVALID"):
        receipts.validate_control_plane_repair_receipt(
            receipt_path=receipt_path,
            issue_date="2026-08-13",
            artifact_root=artifact,
            ops_root=ops,
            production_runtime_root=runtime,
            live_bin_root=live,
            current_preflight=drift,
        )
