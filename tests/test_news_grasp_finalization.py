from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import importlib.util
import shutil

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import news_grasp_finalization as finalization  # noqa: E402
from tools import news_grasp_verified_storage as verified_storage  # noqa: E402
from tools.news_grasp_operational_contract import (  # noqa: E402
    PUBLIC_COMPLETION_FIELDS,
    validate_completion_authority_v2,
)


def _public_manifest(issue_date: str = "2026-08-14") -> dict:
    return finalization.build_public_manifest_v2(
        issue_date=issue_date,
        generation_id="generation-20260814",
        publish_commit="a" * 40,
        producer_operation_id="b" * 64,
        evidence={field: {"ok": True, "field": field} for field in PUBLIC_COMPLETION_FIELDS},
    )


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_manifest_lease_allows_one_producer_per_lineage(tmp_path: Path) -> None:
    calls = 0
    call_lock = threading.Lock()

    def producer() -> dict:
        nonlocal calls
        with call_lock:
            calls += 1
        return _public_manifest()

    def run() -> dict:
        return finalization.get_or_produce_manifest(
            repo_root=tmp_path,
            issue_date="2026-08-14",
            generation_id="generation-20260814",
            publish_commit="a" * 40,
            cause_hash="c" * 64,
            producer=producer,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _value: run(), range(2)))
    assert calls == 1
    assert {result["manifest"]["receiptSha256"] for result in results} == {
        results[0]["manifest"]["receiptSha256"]
    }
    journal = json.loads(Path(results[0]["journalPath"]).read_text(encoding="utf-8"))
    assert journal["producerInvocationCount"] == 1


def test_stale_success_adds_observation_without_regeneration(tmp_path: Path) -> None:
    first = finalization.get_or_produce_manifest(
        repo_root=tmp_path,
        issue_date="2026-08-14",
        generation_id="generation-20260814",
        publish_commit="a" * 40,
        cause_hash="c" * 64,
        producer=_public_manifest,
    )

    observations = 0

    def observe(_manifest: dict) -> dict:
        nonlocal observations
        observations += 1
        return {
            "ok": True,
            "observationKind": "remote_head",
            "publishCommit": "a" * 40,
        }

    second = finalization.get_or_produce_manifest(
        repo_root=tmp_path,
        issue_date="2026-08-14",
        generation_id="generation-20260814",
        publish_commit="a" * 40,
        cause_hash="d" * 64,
        producer=lambda: pytest.fail("sealed success must not be regenerated"),
        observer=observe,
    )
    assert second["status"] == "existing_success_observed"
    assert second["manifest"]["receiptSha256"] == first["manifest"]["receiptSha256"]
    assert Path(second["observationPath"]).is_file()
    assert observations == 1
    observation = json.loads(Path(second["observationPath"]).read_text(encoding="utf-8"))
    assert observation["observationKind"] == "remote_head"
    assert observation["manifestRegenerated"] is False


def test_crash_journal_allows_only_one_forward_retry_for_same_cause(tmp_path: Path) -> None:
    calls = 0

    def crash() -> dict:
        nonlocal calls
        calls += 1
        raise RuntimeError("producer crashed")

    for expected in ("MANIFEST_PRODUCER_FAILED", "MANIFEST_FORWARD_RETRY_EXHAUSTED"):
        with pytest.raises(RuntimeError, match=expected):
            finalization.get_or_produce_manifest(
                repo_root=tmp_path,
                issue_date="2026-08-14",
                generation_id="generation-20260814",
                publish_commit="a" * 40,
                cause_hash="c" * 64,
                producer=crash,
            )
    assert calls == 2
    with pytest.raises(RuntimeError, match="MANIFEST_FORWARD_RETRY_EXHAUSTED"):
        finalization.get_or_produce_manifest(
            repo_root=tmp_path,
            issue_date="2026-08-14",
            generation_id="generation-20260814",
            publish_commit="a" * 40,
            cause_hash="c" * 64,
            producer=crash,
        )
    assert calls == 2


def test_common_finalizer_keeps_public_green_when_readiness_is_debt(tmp_path: Path) -> None:
    result = finalization.finalize_common(
        repo_root=tmp_path,
        public_manifest=_public_manifest(),
        run_intent="ScheduledRecoveryFull",
        transaction_started_at="2026-08-14T06:40:00+09:00",
        public_green_at="2026-08-14T07:20:00+09:00",
        done_at="2026-08-14T07:25:00+09:00",
        readiness={"ok": False, "reason": "scheduled_task_missed_runs"},
        actual_recovery_operation_count=1,
    )
    assert result["terminal"] == "audit_recovered_green"
    assert result["guardOk"] is True
    assert result["exitCode"] == 2
    assert result["stateVector"]["publicCompletionStatus"] == "green"
    assert result["stateVector"]["nextRunReadinessStatus"] == "red"
    assert result["outcomeEnvelope"]["readinessDebt"]["reason"] == "scheduled_task_missed_runs"
    validate_completion_authority_v2(
        result["completionAuthority"], issue_date="2026-08-14"
    )


def test_common_finalizer_preserves_authority_but_opens_incident_on_slo_failure(
    tmp_path: Path,
) -> None:
    result = finalization.finalize_common(
        repo_root=tmp_path,
        public_manifest=_public_manifest(),
        run_intent="ScheduledRecoveryFull",
        transaction_started_at="2026-08-14T06:40:00+09:00",
        public_green_at="2026-08-14T08:10:00+09:00",
        done_at="2026-08-14T08:11:00+09:00",
        readiness={"ok": True},
        actual_recovery_operation_count=1,
    )
    assert result["terminal"] == "audit_major_incident_open"
    assert result["guardOk"] is False
    assert result["exitCode"] == 2
    assert result["completionAuthority"]["publicManifest"]["publicStatus"] == "green"
    assert result["outcomeEnvelope"]["slo"]["status"] == "public_green_slo_failed"


def test_existing_common_finalization_rejects_resealed_semantic_tamper(
    tmp_path: Path,
) -> None:
    result = finalization.finalize_common(
        repo_root=tmp_path,
        public_manifest=_public_manifest(),
        run_intent="ScheduledRecoveryFull",
        transaction_started_at="2026-08-14T06:40:00+09:00",
        public_green_at="2026-08-14T08:10:00+09:00",
        done_at="2026-08-14T08:11:00+09:00",
        readiness={"ok": True},
        actual_recovery_operation_count=1,
    )
    path = finalization.common_finalization_path(
        tmp_path,
        issue_date="2026-08-14",
        generation_id="generation-20260814",
        publish_commit="a" * 40,
    )
    tampered = dict(result)
    tampered["terminal"] = "audit_recovered_green"
    tampered["guardOk"] = True
    tampered["exitCode"] = 0
    tampered["receiptSha256"] = finalization._sha(
        {key: value for key, value in tampered.items() if key != "receiptSha256"}
    )
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValueError, match="COMMON_FINALIZATION_RESULT_INVALID"):
        finalization.finalize_common(
            repo_root=tmp_path,
            public_manifest=_public_manifest(),
            run_intent="ScheduledRecoveryFull",
            transaction_started_at="2026-08-14T06:40:00+09:00",
            public_green_at="2026-08-14T08:10:00+09:00",
            done_at="2026-08-14T08:11:00+09:00",
            readiness={"ok": True},
            actual_recovery_operation_count=1,
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows directory handle pin contract")
def test_verified_storage_pins_managed_parent_against_junction_swap(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    managed = verified_storage.validated_managed_root(
        repo_root=repo,
        relative_parts=("build", "recovery", "transactions"),
        create=True,
        code="VERIFIED_STORAGE_INVALID",
    )
    moved = repo / "transactions-moved"
    with verified_storage.pinned_directory(
        managed, anchor=repo, code="VERIFIED_STORAGE_INVALID"
    ):
        with pytest.raises(OSError):
            managed.rename(moved)
    assert managed.is_dir()
    assert not moved.exists()


def test_installed_guard_rejects_cross_date_result_replay(tmp_path: Path) -> None:
    current = finalization.finalize_common(
        repo_root=tmp_path,
        public_manifest=_public_manifest("2026-08-14"),
        run_intent="ScheduledRecoveryFull",
        transaction_started_at="2026-08-14T06:40:00+09:00",
        public_green_at="2026-08-14T07:20:00+09:00",
        done_at="2026-08-14T07:25:00+09:00",
        readiness={"ok": True},
        actual_recovery_operation_count=1,
    )
    current_path = finalization.common_finalization_path(
        tmp_path,
        issue_date="2026-08-14",
        generation_id="generation-20260814",
        publish_commit="a" * 40,
    )
    receipt_body = {
        "schemaVersion": "NEWS_GRASP_RECOVERY_FINALIZATION_RECEIPT_V2",
        "issueDate": "2026-08-14",
        "generationId": "generation-20260814",
        "publishCommit": "a" * 40,
        "commonFinalizationResultPath": str(current_path.resolve()),
        "commonFinalizationResultFileSha256": _file_sha(current_path),
        "commonFinalizationResultReceiptSha256": current["receiptSha256"],
        "executionReceiptSha256": "b" * 64,
    }
    receipt = finalization._seal(receipt_body)
    receipt_path = tmp_path / "build" / "publish-complete" / "2026-08-14.finalization-receipt.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    stale_root = tmp_path / "stale"
    stale_root.mkdir()
    stale = finalization.finalize_common(
        repo_root=stale_root,
        public_manifest=finalization.build_public_manifest_v2(
            issue_date="2026-08-13",
            generation_id="generation-20260813",
            publish_commit="c" * 40,
            producer_operation_id="d" * 64,
            evidence={
                field: {"ok": True, "field": field}
                for field in PUBLIC_COMPLETION_FIELDS
            },
        ),
        run_intent="ScheduledRecoveryFull",
        transaction_started_at="2026-08-13T06:40:00+09:00",
        public_green_at="2026-08-13T07:20:00+09:00",
        done_at="2026-08-13T07:25:00+09:00",
        readiness={"ok": True},
        actual_recovery_operation_count=1,
    )
    current_path.write_text(json.dumps(stale), encoding="utf-8")

    guard_path = ROOT / "automation" / "guards" / "news-grasp-finalization-guard-v2.py"
    spec = importlib.util.spec_from_file_location("news_grasp_installed_guard_replay", guard_path)
    assert spec is not None and spec.loader is not None
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    with pytest.raises(ValueError):
        guard.evaluate(
            current_path,
            finalization_receipt_path=receipt_path,
            artifact_root=tmp_path,
            expected_issue_date="2026-08-14",
            expected_generation_id="generation-20260814",
            expected_publish_commit="a" * 40,
            expected_finalization_receipt_sha256=receipt["receiptSha256"],
            expected_finalization_receipt_file_sha256=_file_sha(receipt_path),
            expected_result_sha256=current["receiptSha256"],
        )


def test_installed_guard_rejects_replaced_finalization_receipt_even_when_resealed(
    tmp_path: Path,
) -> None:
    result = finalization.finalize_common(
        repo_root=tmp_path,
        public_manifest=_public_manifest("2026-08-14"),
        run_intent="ScheduledRecoveryFull",
        transaction_started_at="2026-08-14T06:40:00+09:00",
        public_green_at="2026-08-14T07:20:00+09:00",
        done_at="2026-08-14T07:25:00+09:00",
        readiness={"ok": True},
        actual_recovery_operation_count=1,
    )
    result_path = finalization.common_finalization_path(
        tmp_path,
        issue_date="2026-08-14",
        generation_id="generation-20260814",
        publish_commit="a" * 40,
    )
    receipt_path = tmp_path / "build" / "publish-complete" / "2026-08-14.finalization-receipt.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt = finalization._seal(
        {
            "schemaVersion": "NEWS_GRASP_RECOVERY_FINALIZATION_RECEIPT_V2",
            "issueDate": "2026-08-14",
            "generationId": "generation-20260814",
            "publishCommit": "a" * 40,
            "commonFinalizationResultPath": str(result_path.resolve()),
            "commonFinalizationResultFileSha256": _file_sha(result_path),
            "commonFinalizationResultReceiptSha256": result["receiptSha256"],
        }
    )
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    guard_path = ROOT / "automation" / "guards" / "news-grasp-finalization-guard-v2.py"
    spec = importlib.util.spec_from_file_location("news_grasp_installed_guard_receipt_replace", guard_path)
    assert spec is not None and spec.loader is not None
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    kwargs = {
        "finalization_receipt_path": receipt_path,
        "artifact_root": tmp_path,
        "expected_issue_date": "2026-08-14",
        "expected_generation_id": "generation-20260814",
        "expected_publish_commit": "a" * 40,
        "expected_finalization_receipt_sha256": receipt["receiptSha256"],
        "expected_finalization_receipt_file_sha256": _file_sha(receipt_path),
        "expected_result_sha256": result["receiptSha256"],
    }
    guard.evaluate(result_path, **kwargs)
    replaced = dict(receipt)
    replaced["publishCommit"] = "b" * 40
    replaced["receiptSha256"] = finalization._sha(
        {key: value for key, value in replaced.items() if key != "receiptSha256"}
    )
    receipt_path.write_text(json.dumps(replaced), encoding="utf-8")
    with pytest.raises(ValueError, match="FINALIZATION_RECEIPT_RESULT_BINDING_INVALID"):
        guard.evaluate(result_path, **kwargs)


def test_existing_manifest_rejects_tampered_legacy_observation(tmp_path: Path) -> None:
    first = finalization.get_or_produce_manifest(
        repo_root=tmp_path,
        issue_date="2026-08-14",
        generation_id="generation-20260814",
        publish_commit="a" * 40,
        cause_hash="c" * 64,
        producer=lambda: {
            "publicManifest": _public_manifest(),
            "legacyObservation": {
                "verified_at": "2026-08-14T07:20:00+09:00"
            },
        },
    )
    legacy_path = Path(first["legacyObservationPath"])
    tampered = json.loads(legacy_path.read_text(encoding="utf-8"))
    tampered["verified_at"] = "2026-08-14T06:40:00+09:00"
    legacy_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValueError, match="LEGACY_PUBLIC_OBSERVATION_INVALID"):
        finalization.get_or_produce_manifest(
            repo_root=tmp_path,
            issue_date="2026-08-14",
            generation_id="generation-20260814",
            publish_commit="a" * 40,
            cause_hash="d" * 64,
            producer=lambda: pytest.fail("sealed manifest must not be regenerated"),
            observer=lambda _manifest: {
                "ok": True,
                "observationKind": "remote_head",
                "publishCommit": "a" * 40,
            },
        )


def test_runner_and_audit_share_one_finalization_coordinator() -> None:
    runner = (ROOT / "scripts" / "ops" / "news-grasp-runner.ps1").read_text(
        encoding="utf-8-sig"
    )
    audit = (ROOT / "tools" / "audit_recovery_control.py").read_text(encoding="utf-8")
    assert "news_grasp_finalization.py" in runner
    assert "coordinate-publish" in runner
    assert "get_or_produce_manifest" in audit
    assert "verify_publish_complete(" not in audit[
        audit.index("def _fresh_reverify_publish_manifest") : audit.index(
            "def execute_audit_recovery"
        )
    ]


def test_no_side_effect_loaded_smoke_does_not_mutate_worktree() -> None:
    def status() -> str:
        return subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=ROOT,
            capture_output=True,
            check=True,
            text=True,
            encoding="utf-8",
        ).stdout

    before = status()
    process = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-B",
            str(ROOT / "tools" / "news_grasp_finalization.py"),
            "smoke-loaded",
            "--repo-root",
            str(ROOT),
        ],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    after = status()
    assert process.returncode == 0, process.stderr or process.stdout
    result = json.loads(process.stdout)
    assert result["schemaVersion"] == "NEWS_GRASP_NO_SIDE_EFFECT_LOADED_SMOKE_V1"
    assert result["ok"] is True
    assert result["mutationCount"] == 0
    assert result["externalCallCount"] == 0
    assert result["scheduledTaskObservationCount"] == 0
    assert before == after


def test_remote_publish_observation_rejects_git_option_and_url_injection(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="FINALIZATION_GIT_REMOTE_INVALID"):
        finalization.observe_remote_publish_head(
            repo_root=tmp_path,
            remote="--upload-pack=cmd.exe",
            branch="main",
            expected_commit="a" * 40,
        )
    with pytest.raises(ValueError, match="FINALIZATION_GIT_BRANCH_INVALID"):
        finalization.observe_remote_publish_head(
            repo_root=tmp_path,
            remote="origin",
            branch="main..evil",
            expected_commit="a" * 40,
        )


def test_loaded_smoke_binds_installed_python_assets_to_source_bytes(
    tmp_path: Path,
) -> None:
    installed = tmp_path / "installed"
    (installed / "prompts").mkdir(parents=True)
    (installed / "guards").mkdir(parents=True)
    shutil.copyfile(
        ROOT / "automation" / "prompts" / "news-grasp-0640-v2.md",
        installed / "prompts" / "news-grasp-0640-v2.md",
    )
    shutil.copyfile(
        ROOT / "automation" / "guards" / "news-grasp-finalization-guard-v2.py",
        installed / "guards" / "news-grasp-finalization-guard-v2.py",
    )
    (installed / "guards" / "news-grasp-finalization-guard-v2.py").write_text(
        "# drifted installed guard\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="NEWS_GRASP_LOADED_SMOKE_ASSET_DRIFT"):
        finalization.no_side_effect_loaded_smoke(
            ROOT,
            installed_asset_root=installed,
        )
