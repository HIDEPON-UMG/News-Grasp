from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tools import daily_self_heal
from tools import news_grasp_recovery_closeout as closeout
from tools import news_grasp_recovery_receipts as receipts
from tools.news_grasp_operational_contract import POST_PUBLIC_GREEN_ALLOWED_OPERATIONS


ISSUE_DATE = "2026-08-27"


def _complete_public_green_manifest() -> dict:
    publish_commit = "c" * 40
    return {
        "schemaVersion": "NEWS_GRASP_PUBLISH_COMPLETE_V2",
        "date": ISSUE_DATE,
        "ok": True,
        "public_status": "green",
        "scheduled_attempt_status": "failed_then_recovered",
        "recovery_attempt_status": "succeeded",
        "source_commit": "a" * 40,
        "artifact_commit": "b" * 40,
        "publish_commit": publish_commit,
        "publish": {"ok": True, "deploy_head": publish_commit},
        "distribution_artifacts": {"missing": []},
        "live_runner_readiness": {
            "ok": True,
            "next_run_readiness": {"ok": True},
        },
        "notification": {"ok": True},
        "podcasts": {"primary": {"ok": True}, "deepdive": {"ok": True}},
    }


def _write_sealed(path: Path, body: dict) -> dict:
    value = receipts._seal(body)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return value


def _git_init(root: Path) -> None:
    root.mkdir(parents=True)
    (root / "tracked.txt").write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "fixture@example.invalid"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "user.name", "fixture"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "add", "tracked.txt"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "commit", "-q", "-m", "fixture"],
        check=True,
        capture_output=True,
    )


def _authority_witness(authority: dict, failure: dict) -> dict:
    return receipts._seal(
        {
            "schemaVersion": "SCHEDULED_RECOVERY_AUTHORITY_LEDGER_WITNESS_V1",
            "issueDate": ISSUE_DATE,
            "authorityReceiptSha256": authority["receiptSha256"],
            "failureReceiptSha256": failure["receiptSha256"],
            "ledgerEventSequence": 7,
            "ledgerEventHash": "d" * 64,
        }
    )


def test_resume_bound_receipt_builds_exact_finalizer_args(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RC-04 Red/Green: ResumeFromStageをfinalizer argvから落とさない。"""

    artifact = tmp_path / "復旧artifact"
    ops = tmp_path / "ops"
    runtime = tmp_path / "runtime"
    live = tmp_path / "live"
    for root in (artifact, ops, runtime, live):
        root.mkdir()
    runner = live / "news-grasp-runner.ps1"
    runner.write_text("# runner", encoding="utf-8")
    python = ops / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"python")
    capability = live / "binding.json"
    capability.write_text("binding", encoding="utf-8")
    authority = artifact / "build" / "authority.json"
    authority.parent.mkdir()
    authority.write_text("{}", encoding="utf-8")
    execution = receipts._seal(
        {
            "schemaVersion": receipts.EXECUTION_SCHEMA,
            "issueDate": ISSUE_DATE,
            "artifactRoot": str(artifact.resolve()),
            "opsRoot": str(ops.resolve()),
            "productionRuntimeRoot": str(runtime.resolve()),
            "liveBinRoot": str(live.resolve()),
            "runnerStatePath": str(live / "news-grasp-runner-state.json"),
            "runnerScriptPath": str(runner.resolve()),
            "recoveryBranch": "ResumeFromStage",
            "resumeStage": "generation-quality-repair",
            "pythonExecutablePath": str(python.resolve()),
            "capabilityReservationPath": str(capability.resolve()),
            "capabilityReservationReceiptSha256": "f" * 64,
            "recoveryAuthorityPath": str(authority.resolve()),
            "nonce": "a" * 32,
        }
    )
    execution_path = artifact / "build" / "execution.json"
    execution_path.write_text(json.dumps(execution), encoding="utf-8")
    finalization = artifact / "build" / "finalization.json"
    manifest = artifact / "build" / "publish-complete" / f"{ISSUE_DATE}.json"
    finalization.write_text("{}", encoding="utf-8")
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        receipts,
        "validate_recovery_execution_receipt",
        lambda **_kwargs: execution,
    )
    monkeypatch.setattr(
        receipts,
        "validate_finalization_receipt",
        lambda **_kwargs: {"manifestPath": str(manifest.resolve())},
    )
    monkeypatch.setattr(
        receipts,
        "validate_execution_finalization_chain",
        lambda **_kwargs: {"status": "Green"},
    )

    result = closeout.build_exact_finalizer_command(
        execution_receipt_path=execution_path,
        finalization_receipt_path=finalization,
        publish_manifest_path=manifest,
    )

    argv = result["argv"]
    assert Path(argv[0]).resolve() == closeout._system_powershell_executable()
    assert Path(argv[0]).is_absolute()
    assert argv[argv.index("-ResumeFromStage") + 1] == "generation-quality-repair"
    assert argv[argv.index("-RepoDirOverride") + 1] == str(artifact.resolve())
    assert argv[argv.index("-OpsRepoRootOverride") + 1] == str(ops.resolve())
    assert argv[argv.index("-PyExeOverride") + 1] == str(python.resolve())
    assert argv[argv.index("-StateFileOverride") + 1] == str(
        live / "news-grasp-runner-state.json"
    )
    assert argv[argv.index("-RecoveryRuntimeBindingPath") + 1] == str(
        live / "news-grasp-recovery-runtime-binding-v1.json"
    )


def test_recovery_runner_preserves_canonical_paths_and_seals_lineage() -> None:
    """Security Red/Green: isolated L5 seamがcanonical authorityを弱めない。"""

    root = Path(__file__).resolve().parents[1]
    runner = (root / "scripts" / "ops" / "news-grasp-runner.ps1").read_text(
        encoding="utf-8-sig"
    )
    installer = (
        root / "scripts" / "ops" / "install-news-grasp-ops.ps1"
    ).read_text(encoding="utf-8-sig")

    assert "canonical Python or production runtime path mismatch" in runner
    assert "AppData\\Local\\Programs\\Python\\Python312\\python.exe" in runner
    assert ".news-grasp-runtime\\production-runtime" in runner
    assert "[string]$binding.lineagePath" in runner
    assert "[string]$binding.lineageSha256" in runner
    assert ". $LineageScriptPath" in runner
    assert "lineagePath = (Join-Path $BinDir 'news-grasp-lineage.ps1')" in installer
    assert "lineageSha256 = ([string]$sourceSnapshots['news-grasp-lineage.ps1'].Sha256)" in installer


def test_exact_finalizer_rejects_tampered_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RC-04 adversarial: sealが壊れたreceiptからargvを推測しない。"""

    execution, finalization, _drifted = _reseal_fixture(tmp_path, monkeypatch)
    closeout.reseal_known_receipt_drift(
        execution_receipt_path=execution,
        finalization_receipt_path=finalization,
    )
    tampered = json.loads(execution.read_text(encoding="utf-8"))
    tampered["resumeStage"] = "post-deepdive"
    execution.write_text(json.dumps(tampered), encoding="utf-8")
    manifest = execution.parents[1] / "publish-complete" / f"{ISSUE_DATE}.json"

    with pytest.raises(
        closeout.PostPublicCloseoutError, match="execution_receipt_invalid"
    ):
        closeout.build_exact_finalizer_command(
            execution_receipt_path=execution,
            finalization_receipt_path=finalization,
            publish_manifest_path=manifest,
        )


def test_exact_finalizer_rejects_valid_but_mixed_execution_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RC-04 adversarial: validなA executionとB finalizationを混在できない。"""

    execution, finalization, _drifted = _reseal_fixture(tmp_path, monkeypatch)
    closeout.reseal_known_receipt_drift(
        execution_receipt_path=execution,
        finalization_receipt_path=finalization,
    )
    execution_a = json.loads(execution.read_text(encoding="utf-8"))
    execution_b = receipts._seal(
        {
            **{
                key: value
                for key, value in execution_a.items()
                if key not in {"receiptSha256", "nonce"}
            },
            "nonce": "b" * 32,
        }
    )
    execution_b_path = execution.with_name("execution-b.json")
    receipts.write_atomic_json(
        execution_b_path, execution_b, root=execution.parents[2]
    )
    mixed = json.loads(finalization.read_text(encoding="utf-8"))
    mixed.update(
        {
            "executionReceiptPath": str(execution_b_path.resolve()),
            "executionReceiptSha256": execution_b["receiptSha256"],
            "executionReceiptNonce": execution_b["nonce"],
            "executionReceiptFileSha256": receipts.file_sha256(execution_b_path),
        }
    )
    mixed = receipts._seal(
        {key: value for key, value in mixed.items() if key != "receiptSha256"}
    )
    receipts.write_atomic_json(finalization, mixed, root=execution.parents[2])
    manifest = execution.parents[1] / "publish-complete" / f"{ISSUE_DATE}.json"

    with pytest.raises(
        closeout.PostPublicCloseoutError, match="receipt_validation_failed"
    ):
        closeout.build_exact_finalizer_command(
            execution_receipt_path=execution,
            finalization_receipt_path=finalization,
            publish_manifest_path=manifest,
        )


def test_exact_finalizer_crash_reuses_same_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RC-04 recovery: state適用前の再開は同一receipt・同一argvだけになる。"""

    execution, finalization, _drifted = _reseal_fixture(tmp_path, monkeypatch)
    reseal = closeout.reseal_known_receipt_drift(
        execution_receipt_path=execution,
        finalization_receipt_path=finalization,
    )
    manifest = execution.parents[1] / "publish-complete" / f"{ISSUE_DATE}.json"

    first = closeout.build_exact_finalizer_command(
        execution_receipt_path=execution,
        finalization_receipt_path=finalization,
        publish_manifest_path=manifest,
    )
    second = closeout.build_exact_finalizer_command(
        execution_receipt_path=execution,
        finalization_receipt_path=finalization,
        publish_manifest_path=manifest,
    )

    assert first["argv"] == second["argv"]
    assert first["argvSha256"] == second["argvSha256"]
    assert first["executionReceiptSha256"] == reseal["executionReceiptSha256"]
    current = json.loads(execution.read_text(encoding="utf-8"))
    assert receipts.consumption_status(
        receipt=current,
        live_bin_root=Path(current["liveBinRoot"]),
        kind="execution",
    ) == "consumed_pending_operation"


def _reseal_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, dict]:
    artifact = tmp_path / "復旧artifact"
    ops = tmp_path / "ops"
    runtime = tmp_path / "runtime"
    live = tmp_path / "live"
    _git_init(artifact)
    _git_init(ops)
    runtime.mkdir()
    live.mkdir()
    docs = artifact / "docs"
    docs.mkdir()
    (docs / "index.html").write_text("public green", encoding="utf-8")
    runner = live / "news-grasp-runner.ps1"
    runner.write_text("# runner", encoding="utf-8")
    python = ops / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"python")
    capability = live / "binding.json"
    capability.write_text("binding", encoding="utf-8")
    failure = _write_sealed(
        artifact / "build" / "failure.json",
        {
            "schemaVersion": "SCHEDULED_FAILURE_RECEIPT_V1",
            "issueDate": ISSUE_DATE,
            "scheduledAttemptStatus": "failed",
        },
    )
    authority = _write_sealed(
        artifact / "build" / "authority.json",
        {
            "schemaVersion": "SCHEDULED_RECOVERY_AUTHORITY_V1",
            "issueDate": ISSUE_DATE,
            "failureReceiptSha256": failure["receiptSha256"],
        },
    )
    witness = _authority_witness(authority, failure)
    monkeypatch.setattr(
        receipts, "_validate_authority_via_broker", lambda **_kwargs: witness
    )
    now = datetime.now(timezone.utc)
    audit_accepted = (now - timedelta(minutes=10)).isoformat()
    execution = receipts.create_recovery_execution_receipt(
        issue_date=ISSUE_DATE,
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
        audit_accepted_at=audit_accepted,
        recovery_branch="ResumeFromStage",
        resume_stage="generation-quality-repair",
        python_executable_path=python,
        capability_reservation_path=capability,
        capability_reservation_receipt_sha256="f" * 64,
        reserved_max_external_model_calls=0,
    )
    execution_path = artifact / "build" / "recovery-authority" / "execution.json"
    execution_path.parent.mkdir(parents=True)
    execution_path.write_text(json.dumps(execution), encoding="utf-8")
    manifest_value = {
        "schemaVersion": "NEWS_GRASP_PUBLISH_COMPLETE_V2",
        "date": ISSUE_DATE,
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
        "live_runner_readiness": {"ok": True, "next_run_readiness": {"ok": True}},
    }
    manifest = artifact / "build" / "publish-complete" / f"{ISSUE_DATE}.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps(manifest_value), encoding="utf-8")
    producer = artifact / "build" / "recovery-authority" / "producer.json"
    producer.write_text(
        json.dumps(
            {
                "date": ISSUE_DATE,
                "run_id": "fixture",
                "run_intent": "ScheduledRecoveryFull",
                "repo_dir": str(artifact.resolve()),
                **daily_self_heal._producer_lineage_expected(
                    repo_root=artifact,
                    ops_root=ops,
                    date=ISSUE_DATE,
                    run_intent="ScheduledRecoveryFull",
                    run_id="fixture",
                ),
            }
        ),
        encoding="utf-8",
    )
    finalization_value = receipts.create_finalization_receipt(
        issue_date=ISSUE_DATE,
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
        producer_state_path=producer,
        producer_state_sha256=receipts.file_sha256(producer),
        audit_accepted_at=audit_accepted,
    )
    finalization = artifact / "build" / "publish-complete" / "finalization.json"
    finalization.write_text(json.dumps(finalization_value), encoding="utf-8")
    drifted = dict(execution)
    drifted.update(
        {
            "artifactRoot": str(artifact.resolve()) + "\\.",
            "opsRoot": str(ops.resolve()) + "\\.",
            "artifactHead": "0" * 40,
            "opsHead": "1" * 40,
            "issuedAt": (now - timedelta(hours=3)).isoformat(),
            "recoveryBranch": "ScheduledRecoveryFull",
            "resumeStage": "generation-quality-repair",
        }
    )
    drifted = receipts._seal(
        {key: value for key, value in drifted.items() if key != "receiptSha256"}
    )
    execution_path.write_text(json.dumps(drifted), encoding="utf-8")
    return execution_path, finalization, drifted


def test_one_shot_reseal_fixes_known_drift_and_preserves_public_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RC-05 Green: HEAD/time/Unicode path/resume driftを1 callで再封印する。"""

    execution, finalization, _drifted = _reseal_fixture(tmp_path, monkeypatch)

    result = closeout.reseal_known_receipt_drift(
        execution_receipt_path=execution,
        finalization_receipt_path=finalization,
    )

    resealed = json.loads(execution.read_text(encoding="utf-8"))
    assert result["status"] == "Green"
    assert result["publicArtifactUnchanged"] is True
    assert result["publicArtifactTreeSha256Before"] == result[
        "publicArtifactTreeSha256After"
    ]
    assert resealed["recoveryBranch"] == "ResumeFromStage"
    assert resealed["resumeStage"] == "generation-quality-repair"
    assert set(("artifactHead", "opsHead", "issuedAt", "artifactRoot", "opsRoot")) <= set(
        result["driftFields"]
    )
    assert resealed["receiptResealCount"] == 1


def test_one_shot_reseal_rejects_sequential_second_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RC-05 adversarial: 同一closeoutの逐次2回目もfail-closeする。"""

    execution, finalization, _drifted = _reseal_fixture(tmp_path, monkeypatch)
    closeout.reseal_known_receipt_drift(
        execution_receipt_path=execution,
        finalization_receipt_path=finalization,
    )

    with pytest.raises(
        closeout.PostPublicCloseoutError,
        match="receipt_reseal_already_consumed",
    ):
        closeout.reseal_known_receipt_drift(
            execution_receipt_path=execution,
            finalization_receipt_path=finalization,
        )


def test_unknown_receipt_drift_becomes_typed_closeout_blocker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RC-05 adversarial: known set外のdriftは推測再封印しない。"""

    execution, finalization, drifted = _reseal_fixture(tmp_path, monkeypatch)
    drifted["runnerSha256"] = "9" * 64
    execution.write_text(
        json.dumps(
            receipts._seal(
                {key: value for key, value in drifted.items() if key != "receiptSha256"}
            )
        ),
        encoding="utf-8",
    )

    with pytest.raises(closeout.PostPublicCloseoutError, match="unknown_receipt_drift"):
        closeout.reseal_known_receipt_drift(
            execution_receipt_path=execution,
            finalization_receipt_path=finalization,
        )


def test_applied_receipt_ledger_blocks_reseal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RC-05 recovery: state適用済みidentityを新receiptへ移管しない。"""

    execution, finalization, drifted = _reseal_fixture(tmp_path, monkeypatch)
    live = Path(str(drifted["liveBinRoot"]))
    receipts.consume_or_resume(receipt=drifted, live_bin_root=live, kind="execution")
    receipts.mark_operation_applied(
        receipt=drifted, live_bin_root=live, kind="execution"
    )

    with pytest.raises(
        closeout.PostPublicCloseoutError, match="execution_already_applied"
    ):
        closeout.reseal_known_receipt_drift(
            execution_receipt_path=execution,
            finalization_receipt_path=finalization,
        )


@pytest.mark.parametrize("failure_point", ("finalization_write", "post_ledger_validation"))
def test_reseal_transaction_rolls_back_receipts_and_ledger_on_post_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    """RC-05 fault: execution write後の任意失敗で旧2 receipt/ledgerへ戻る。"""

    execution, finalization, drifted = _reseal_fixture(tmp_path, monkeypatch)
    original_execution = execution.read_bytes()
    original_finalization = finalization.read_bytes()
    live = Path(str(drifted["liveBinRoot"]))
    original_write = receipts.write_atomic_json
    failed = False

    if failure_point == "finalization_write":
        def fail_once(path: Path, value: dict, *, root: Path | None = None) -> None:
            nonlocal failed
            if Path(path).resolve() == finalization.resolve() and not failed:
                failed = True
                raise OSError("fixture finalization write failure")
            original_write(path, value, root=root)

        monkeypatch.setattr(receipts, "write_atomic_json", fail_once)
    else:
        original_validate = receipts.validate_finalization_receipt

        def fail_validation_once(**kwargs):
            nonlocal failed
            if not failed:
                failed = True
                raise ValueError("fixture post-ledger validation failure")
            return original_validate(**kwargs)

        monkeypatch.setattr(
            receipts, "validate_finalization_receipt", fail_validation_once
        )

    with pytest.raises(closeout.PostPublicCloseoutError, match="reseal_failed"):
        closeout.reseal_known_receipt_drift(
            execution_receipt_path=execution,
            finalization_receipt_path=finalization,
        )

    restored = json.loads(execution.read_text(encoding="utf-8"))
    assert execution.read_bytes() != b""
    assert finalization.read_bytes() != b""
    assert execution.read_bytes() == original_execution
    assert finalization.read_bytes() == original_finalization
    assert json.loads(original_execution) == restored
    assert receipts.consumption_status(
        receipt=restored, live_bin_root=live, kind="execution"
    ) is None
    assert not (
        execution.parents[2]
        / "build"
        / "recovery-authority"
        / "reseal-known-drift.transaction.json"
    ).exists()


def test_reseal_transaction_rolls_back_near_limit_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RC-05 fault: 1 MiB近傍receiptでもjournalを読めてexact byte rollbackする。"""

    execution, finalization, drifted = _reseal_fixture(tmp_path, monkeypatch)
    inflated = json.loads(finalization.read_text(encoding="utf-8"))
    inflated["fixturePadding"] = "x" * (700 * 1024)
    inflated = receipts._seal(
        {key: value for key, value in inflated.items() if key != "receiptSha256"}
    )
    finalization.write_text(json.dumps(inflated), encoding="utf-8")
    assert finalization.stat().st_size < closeout.MAX_JSON_BYTES
    original_execution = execution.read_bytes()
    original_finalization = finalization.read_bytes()
    original_write = receipts.write_atomic_json
    failed = False

    def fail_once(path: Path, value: dict, *, root: Path | None = None) -> None:
        nonlocal failed
        if Path(path).resolve() == finalization.resolve() and not failed:
            failed = True
            raise OSError("fixture near-limit finalization write failure")
        original_write(path, value, root=root)

    monkeypatch.setattr(receipts, "write_atomic_json", fail_once)
    with pytest.raises(closeout.PostPublicCloseoutError, match="reseal_failed"):
        closeout.reseal_known_receipt_drift(
            execution_receipt_path=execution,
            finalization_receipt_path=finalization,
        )

    assert execution.read_bytes() == original_execution
    assert finalization.read_bytes() == original_finalization
    assert not (
        execution.parents[2]
        / "build"
        / "recovery-authority"
        / "reseal-known-drift.transaction.json"
    ).exists()


def test_reseal_transaction_recovers_uncaught_crash_on_next_one_shot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RC-05 recovery: receipt書換え直後のprocess crashを次回journalから回復する。"""

    execution, finalization, _drifted = _reseal_fixture(tmp_path, monkeypatch)
    original_write = receipts.write_atomic_json
    crashed = False

    def crash_after_execution(
        path: Path, value: dict, *, root: Path | None = None
    ) -> None:
        nonlocal crashed
        original_write(path, value, root=root)
        if Path(path).resolve() == execution.resolve() and not crashed:
            crashed = True
            raise KeyboardInterrupt("fixture process crash")

    with monkeypatch.context() as crash_patch:
        crash_patch.setattr(receipts, "write_atomic_json", crash_after_execution)
        with pytest.raises(KeyboardInterrupt, match="fixture process crash"):
            closeout.reseal_known_receipt_drift(
                execution_receipt_path=execution,
                finalization_receipt_path=finalization,
            )

    journal = (
        execution.parents[2]
        / "build"
        / "recovery-authority"
        / "reseal-known-drift.transaction.json"
    )
    assert journal.is_file()

    recovered = closeout.reseal_known_receipt_drift(
        execution_receipt_path=execution,
        finalization_receipt_path=finalization,
    )

    assert recovered["status"] == "Green"
    assert recovered["publicArtifactUnchanged"] is True
    assert not journal.exists()


def test_reseal_lock_rejects_parallel_one_shot(tmp_path: Path) -> None:
    """RC-05 adversarial: 同一artifactの二重resealをjournal作成前に拒否する。"""

    artifact = tmp_path / "artifact"
    artifact.mkdir()
    with closeout._exclusive_reseal_lock(artifact):
        with pytest.raises(
            closeout.PostPublicCloseoutError, match="reseal_lock_busy"
        ):
            with closeout._exclusive_reseal_lock(artifact, timeout=0.05):
                pytest.fail("parallel reseal lock unexpectedly acquired")


def test_post_public_green_allows_exact_five_operations(tmp_path: Path) -> None:
    """RC-06 primary: public Green後の許可集合はsingle ownerの5操作だけ。"""

    artifact = tmp_path / "artifact"
    artifact.mkdir()
    for operation in POST_PUBLIC_GREEN_ALLOWED_OPERATIONS:
        result = closeout.record_closeout_operation(
            artifact_root=artifact,
            issue_date=ISSUE_DATE,
            operation=operation,
        )
        assert result["status"] == "allowed"

    ledger = artifact / "build" / "recovery-closeout" / f"{ISSUE_DATE}.jsonl"
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert tuple(row["operation"] for row in rows) == POST_PUBLIC_GREEN_ALLOWED_OPERATIONS


@pytest.mark.parametrize(
    "issue_date",
    ("../outside", "2026-08-27/../../outside", "2026-02-30"),
)
def test_closeout_ledger_rejects_issue_date_path_escape(
    tmp_path: Path, issue_date: str
) -> None:
    """RC-06 adversarial: dateをartifact外append pathへ変換できない。"""

    artifact = tmp_path / "artifact"
    artifact.mkdir()
    with pytest.raises(closeout.PostPublicCloseoutError, match="issue_date_invalid"):
        closeout.record_closeout_operation(
            artifact_root=artifact,
            issue_date=issue_date,
            operation="final_report",
        )
    assert not (tmp_path / "outside.jsonl").exists()


@pytest.mark.parametrize(
    "operation",
    ("runner_regeneration", "deepdive_regeneration", "broad_search", "report_polish"),
)
def test_post_public_green_forbidden_operation_is_blocked(
    tmp_path: Path, operation: str
) -> None:
    """RC-06 adversarial: 再生成・探索・polishへ戻るrouteを拒否する。"""

    artifact = tmp_path / operation
    artifact.mkdir()
    with pytest.raises(
        closeout.PostPublicCloseoutError, match="post_public_closeout_blocker"
    ):
        closeout.record_closeout_operation(
            artifact_root=artifact,
            issue_date=ISSUE_DATE,
            operation=operation,
        )
    rows = [
        json.loads(line)
        for line in (
            artifact / "build" / "recovery-closeout" / f"{ISSUE_DATE}.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]
    assert len(rows) == 1
    assert rows[0]["operation"] == operation
    assert rows[0]["status"] == "post_public_closeout_blocker"


def test_actual_runner_blocks_non_finalizer_reentry_after_public_green(
    tmp_path: Path,
) -> None:
    """RC-06 integration: public Green後の実runner通常入口をbinding前に拒否する。"""

    artifact = tmp_path / "artifact"
    manifest = artifact / "build" / "publish-complete" / f"{ISSUE_DATE}.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(_complete_public_green_manifest()),
        encoding="utf-8",
    )
    runner = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "ops"
        / "news-grasp-runner.ps1"
    )
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(runner),
            "-RunIntent",
            "ScheduledRecoveryFull",
            "-DateStampOverride",
            ISSUE_DATE,
            "-RepoDirOverride",
            str(artifact),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

    assert completed.returncode == 78
    assert "post_public_closeout_blocker" in completed.stdout
    assert "non_finalizer_runner_reentry" in completed.stdout


def test_runner_public_green_predicate_rejects_partial_manifest(
    tmp_path: Path,
) -> None:
    """RC-06 adversarial: 4項目だけのpartial JSONを完了authorityにしない。"""

    runner = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "ops"
        / "news-grasp-runner.ps1"
    )
    source = runner.read_text(encoding="utf-8-sig")
    start = source.index("function Test-NewsGraspCompletePublicGreenManifest {")
    end = source.index("\nfunction ", start + len("function "))
    predicate = source[start:end]
    probe = tmp_path / "public-green-predicate.ps1"
    probe.write_text(
        "param([string] $ManifestPath, [string] $IssueDate)\n"
        + predicate
        + "\n"
        + "$verified = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json\n"
        + "if (Test-NewsGraspCompletePublicGreenManifest -Verified $verified -IssueDate $IssueDate) { exit 0 }\n"
        + "exit 4\n",
        encoding="utf-8",
    )
    complete = tmp_path / "complete.json"
    partial = tmp_path / "partial.json"
    complete.write_text(json.dumps(_complete_public_green_manifest()), encoding="utf-8")
    partial.write_text(
        json.dumps(
            {
                "schemaVersion": "NEWS_GRASP_PUBLISH_COMPLETE_V2",
                "date": ISSUE_DATE,
                "ok": True,
                "public_status": "green",
            }
        ),
        encoding="utf-8",
    )

    def invoke(path: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(probe),
                "-ManifestPath",
                str(path),
                "-IssueDate",
                ISSUE_DATE,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    assert invoke(complete).returncode == 0
    assert invoke(partial).returncode == 4


def test_post_public_green_unknown_drift_is_evidenced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RC-06 recovery: 未知driftをtyped blocker ledgerへ残す。"""

    execution, finalization, drifted = _reseal_fixture(tmp_path, monkeypatch)
    drifted["runnerSha256"] = "9" * 64
    execution.write_text(
        json.dumps(
            receipts._seal(
                {key: value for key, value in drifted.items() if key != "receiptSha256"}
            )
        ),
        encoding="utf-8",
    )
    artifact = execution.parents[2]
    with pytest.raises(
        closeout.PostPublicCloseoutError, match="unknown_receipt_drift"
    ):
        closeout.reseal_known_receipt_drift(
            execution_receipt_path=execution,
            finalization_receipt_path=finalization,
        )
    with pytest.raises(
        closeout.PostPublicCloseoutError, match="post_public_closeout_blocker"
    ):
        closeout.record_closeout_operation(
            artifact_root=artifact,
            issue_date=ISSUE_DATE,
            operation="unknown_receipt_drift",
        )
    ledger = artifact / "build" / "recovery-closeout" / f"{ISSUE_DATE}.jsonl"
    evidence = json.loads(ledger.read_text(encoding="utf-8").splitlines()[-1])
    assert evidence["operation"] == "unknown_receipt_drift"
    assert evidence["status"] == "post_public_closeout_blocker"


def test_post_public_green_consumers_delegate_to_single_admission_owner() -> None:
    """RC-06 contract: runner/guard/surface/reportがhelper単体を迂回しない。"""

    repo = Path(__file__).resolve().parents[1]
    sources = {
        "finalizer_exact_args_replay": (
            repo / "scripts" / "ops" / "news-grasp-runner.ps1"
        ).read_text(encoding="utf-8-sig"),
        "receipt_reseal": (
            repo / "tools" / "news_grasp_recovery_closeout.py"
        ).read_text(encoding="utf-8"),
        "completion_guard": (
            repo / "tools" / "news_grasp_completion_guard.py"
        ).read_text(encoding="utf-8"),
        "verify_public_surface": (
            repo / "tools" / "verify_public_surface.py"
        ).read_text(encoding="utf-8"),
        "final_report": (
            repo / "tools" / "audit_recovery_control.py"
        ).read_text(encoding="utf-8"),
    }
    automation = (
        repo / "automation" / "news-grasp-6-40" / "completion_guard.py"
    ).read_text(encoding="utf-8")

    assert tuple(sources) == POST_PUBLIC_GREEN_ALLOWED_OPERATIONS
    for operation, source in sources.items():
        assert operation in source
        assert (
            "record_closeout_operation" in source
            or "authorize-operation" in source
        )
    assert "require_post_public_green_operation" in automation
    assert 'require_post_public_green_operation("completion_guard")' in automation
