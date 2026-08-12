"""WP-02のNG2-A01〜A14、各3観点を実consumerへ束縛するRed/Green matrix。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Callable

import pytest

from tools import audit_recovery_control as audit
from tools import news_grasp_change_control as change_control
from tools import news_grasp_checkpoint as checkpoint
from tools import news_grasp_deterministic_builders as builders
from tools import news_grasp_generation as generation
from tools import news_grasp_gate_profiles as gates
from tools import news_grasp_human_impact as human_impact
from tools import news_grasp_nopublish as nopublish
from tools import news_grasp_operational_contract as operational
from tools import news_grasp_runner as runner
from tools import verify_public_surface


MATRIX_PATH = Path(__file__).resolve().parent / "fixtures" / "operational-redesign" / "acceptance-matrix-v1.json"
ROOT = Path(__file__).resolve().parents[1]


def _task_constitution(path: str) -> dict[str, Any]:
    graph = json.loads(
        (ROOT / "config" / "news_grasp_skill_cross_layer_graph_v1.json").read_text(
            encoding="utf-8"
        )
    )
    row = next(
        item for item in graph["skills"] if item["skillId"] == "ops-write-operational-plan"
    )
    return {
        "schemaVersion": "NEWS_GRASP_TASK_CONSTITUTION_REQUEST_V2",
        "taskId": "TODO-196",
        "durableGoalId": "b3c2f6bd-e729-58bd-9dfd-6c1d19bbe3d0",
        "todoDefinitionSetSha256": "a" * 64,
        "reviewPolicy": "no_additional_review",
        "reviewAttemptCount": 0,
        "clauseIds": row["clauseIds"],
        "requirementIds": ["R08"],
        "acceptanceIds": ["A08"],
        "writeSet": [path],
        "skillIds": [row["skillId"]],
        "purposeIds": row["purposeIds"],
        "flowIds": row["flowIds"],
        "taskIds": row["taskIds"],
        "consumerRoutes": row["consumerRoutes"],
        "stateIds": row["stateIds"],
        "evidenceIds": row["evidenceIds"],
        "efficiencyCandidates": [
            {"candidateId": "single-consumer", "goalFidelity": True, "safetyComplete": True, "expectedTotalResource": 1.0, "resourceVector": {"modelCalls": 0, "toolCalls": 2, "expectedRetries": 0, "broadRegressions": 0, "e2eAttempts": 0, "humanOperations": 0, "wallClockMinutes": 5}},
            {"candidateId": "duplicate-consumers", "goalFidelity": True, "safetyComplete": True, "expectedTotalResource": 2.0, "resourceVector": {"modelCalls": 0, "toolCalls": 4, "expectedRetries": 0, "broadRegressions": 0, "e2eAttempts": 0, "humanOperations": 0, "wallClockMinutes": 10}},
        ],
        "selectedCandidateId": "single-consumer",
        "unresolvedDecisionIds": [],
    }


def _small_repo(root: Path) -> Path:
    repo = root / "repo"
    (repo / "tools").mkdir(parents=True)
    (repo / "config").mkdir()
    (repo / "tests").mkdir()
    (repo / "tools" / "news_grasp_change_control.py").write_text("baseline\n", encoding="utf-8")
    (repo / "config" / "news_grasp_product_write_allowlist_v1.json").write_text(
        '{"allowedPaths":["tools/news_grasp_change_control.py"],"productId":"News-Grasp","schemaVersion":"NEWS_GRASP_PRODUCT_WRITE_ALLOWLIST_V1"}\n',
        encoding="utf-8",
    )
    (repo / "config" / "news_grasp_product_change_routes_v1.json").write_text(
        json.dumps(
            {
                "schemaVersion": "NEWS_GRASP_PRODUCT_CHANGE_ROUTES_V1",
                "productId": "News-Grasp",
                "unknownRoutePolicy": "fail_closed",
                "consumer": "tools.news_grasp_change_control.apply_packet",
                "routes": [
                    {"routeId": route_id, "producer": route_id, "executor": executor}
                    for route_id, executor in change_control.EXPECTED_ROUTE_EXECUTORS.items()
                ],
            }
        ),
        encoding="utf-8",
    )
    (repo / "tests" / "marker.txt").write_text("marker\n", encoding="utf-8")
    return repo


def _change_snapshot(repo: Path, tmp_path: Path, owner: str = "matrix-owner") -> Path:
    path = tmp_path / f"snapshot-{owner}.json"
    change_control.snapshot(
        repo_root=repo,
        target_manifest={"targets": ["tools/news_grasp_change_control.py"], "ownerThreadId": owner, "actorRouteId": "luna"},
        output=path,
    )
    return path


def _change_packet(repo: Path, snapshot: Path, path: str = "tools/news_grasp_change_control.py") -> dict[str, Any]:
    return {
        "schemaVersion": "NEWS_GRASP_CHANGE_PACKET_V1",
        "packetId": "NG2-WP02-MATRIX",
        "ownerThreadId": "matrix-owner",
        "actorRouteId": "luna",
        "executor": {"model": "gpt-5.6-luna", "reasoningEffort": "max", "noSubstitution": True},
        "snapshotPath": str(snapshot),
        "changes": [{"path": path, "operation": "replace", "content": "candidate\n"}],
        "unresolvedDecisionIds": [],
        "allowedWriteSet": ["tools/news_grasp_change_control.py"],
        "taskConstitution": _task_constitution(path),
        "repoRoot": str(repo),
    }


def _generation(tmp_path: Path) -> dict[str, Any]:
    source = _small_repo(tmp_path / "generation")
    for command in (
        ["git", "-C", str(source), "init", "-q"],
        ["git", "-C", str(source), "config", "user.name", "News-Grasp Matrix"],
        ["git", "-C", str(source), "config", "user.email", "test@example.invalid"],
        ["git", "-C", str(source), "remote", "add", "origin", "https://example.invalid/news-grasp.git"],
        ["git", "-C", str(source), "add", "--all"],
        ["git", "-C", str(source), "commit", "-q", "-m", "matrix generation"],
    ):
        subprocess.run(command, check=True, capture_output=True)
    source_head = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()
    subprocess.run(
        ["git", "-C", str(source), "update-ref", "refs/remotes/origin/main", source_head],
        check=True,
        capture_output=True,
    )
    runtime = tmp_path / "generation" / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "launcher.txt").write_text("runtime\n", encoding="utf-8")
    launcher = tmp_path / "generation" / "installed-launcher.pyw"
    launcher.write_text("launcher\n", encoding="utf-8")
    action = ["pythonw.exe", "news-grasp-task-launcher.pyw", "--mode", "production"]
    manifest = generation.create_manifest(
        source_root=source,
        source_paths=["tools/news_grasp_change_control.py"],
        runtime_root=runtime,
        runtime_paths=["launcher.txt"],
        config_path=source / "config" / "news_grasp_product_write_allowlist_v1.json",
        launcher_paths=[launcher],
        task_action=action,
        task_trigger={"daily": "06:00"},
        generation_id="matrix-generation",
        previous_generation_id=None,
        output=tmp_path / "generation.json",
    )
    return {"source": source, "runtime": runtime, "launcher": launcher, "manifest": manifest, "action": action}


def _checkpoint(tmp_path: Path) -> dict[str, Any]:
    fp = checkpoint.cause_fingerprint(
        issue_date="2026-08-11",
        daily_operation_lineage_id="lineage-001",
        artifact_key="deepdive",
        stage_id="article",
        producer_route_id="deepdive-article-model",
        failure_class="wrapper_failure",
        reason_code="WRAPPER_RC126",
        cause_input_mask=["sourceHash", "promptHash"],
        input_hashes={"sourceHash": "source-a", "promptHash": "prompt-a", "noise": "noise-a"},
    )
    return checkpoint.create_checkpoint(
        issue_date="2026-08-11",
        daily_operation_lineage_id="lineage-001",
        stage="article",
        artifact_key="deepdive",
        input_hashes={"sourceHash": "source-a", "promptHash": "prompt-a"},
        output_hash="output-a",
        schema="MARKDOWN_V1",
        oracle_id="deepdive-v1",
        producer_route_id="deepdive-article-model",
        next_deterministic_step="dialogue",
        cause_fingerprint_value=fp,
        output_path=tmp_path / "checkpoint.json",
    )


def _audit_decision() -> dict[str, Any]:
    return audit.seal_audit_decision(
        {
            "schemaVersion": "AUDIT_RECOVERY_DECISION_V1",
            "issueDate": "2026-08-11",
            "terminal": "audit_observation_unverified",
            "publicStatus": "green",
            "workPriority": "root_cause_after_public_green",
            "action": "verify_public_completion",
            "reasonCode": "PRIMARY_VERIFIER_UNAVAILABLE",
            "completionAuthorityId": "authority-matrix",
            "causeFingerprint": "cause-matrix",
        }
    )


def _cases() -> list[tuple[str, Callable[[Path, Any], Any]]]:
    def a01_primary(tmp: Path, _monkey: Any) -> Any:
        repo = _small_repo(tmp)
        snap = _change_snapshot(repo, tmp)
        return change_control.validate_packet(repo_root=repo, packet=_change_packet(repo, snap))["status"] == "validated"

    def a01_boundary(tmp: Path, _monkey: Any) -> Any:
        repo = _small_repo(tmp)
        snap = _change_snapshot(repo, tmp)
        with pytest.raises(change_control.NewsGraspChangeControlError, match="NG_CHANGE_PATH_INVALID"):
            change_control.validate_packet(repo_root=repo, packet=_change_packet(repo, snap, "../shared.txt"))
        return True

    def a01_recovery(tmp: Path, _monkey: Any) -> Any:
        repo = _small_repo(tmp)
        _change_snapshot(repo, tmp, "owner-a")
        with pytest.raises(change_control.NewsGraspChangeControlError, match="NG_CONCURRENT_OWNER_PRESENT"):
            _change_snapshot(repo, tmp, "owner-b")
        return True

    def a02_primary(tmp: Path, _monkey: Any) -> Any:
        f = _generation(tmp)
        result = generation.verify_parity(
            manifest=f["manifest"], source_root=f["source"], runtime_root=f["runtime"],
            config_path=f["source"] / "config/news_grasp_product_write_allowlist_v1.json",
            launcher_paths=[f["launcher"]], task_action=f["action"], task_trigger={"daily": "06:00"},
        )
        return result["status"] == "green"

    def a02_boundary(tmp: Path, _monkey: Any) -> Any:
        f = _generation(tmp)
        (f["runtime"] / "launcher.txt").write_text("drift\n", encoding="utf-8")
        with pytest.raises(generation.NewsGraspGenerationError, match="NG_GENERATION_DRIFT"):
            generation.verify_parity(
                manifest=f["manifest"], source_root=f["source"], runtime_root=f["runtime"],
                config_path=f["source"] / "config/news_grasp_product_write_allowlist_v1.json",
                launcher_paths=[f["launcher"]], task_action=f["action"], task_trigger={"daily": "06:00"},
            )
        return True

    def a02_recovery(tmp: Path, _monkey: Any) -> Any:
        f = _generation(tmp)
        pointer = tmp / "active.json"
        return generation.rollback(
            previous_manifest=f["manifest"], active_pointer=pointer, source_root=f["source"], runtime_root=f["runtime"],
            config_path=f["source"] / "config/news_grasp_product_write_allowlist_v1.json",
            launcher_paths=[f["launcher"]], task_action=f["action"], task_trigger={"daily": "06:00"},
        )["generationId"] == "matrix-generation"

    def a03_primary(_tmp: Path, _monkey: Any) -> Any:
        return operational.evaluate_completion(
            scheduled_attempt={"status": "completed"}, recovery_attempt={"status": "not_needed"},
            public_receipt={"status": "verified_green", "authorityId": "authority-1"},
            readiness_probe={"status": "red"}, audit_observation={"status": "observed"},
        )["publicCompletionStatus"] == "green"

    def a03_boundary(_tmp: Path, _monkey: Any) -> Any:
        result = operational.evaluate_completion(
            scheduled_attempt={"status": "completed"}, recovery_attempt={"status": "not_needed"},
            public_receipt={"status": "verification_unavailable", "previousVerifiedGreen": True, "authorityId": "authority-1"},
            readiness_probe={"status": "verification_unavailable"}, audit_observation={"status": "unverified"},
        )
        return result["publicCompletionStatus"] == "green"

    def a03_recovery(tmp: Path, _monkey: Any) -> Any:
        root = tmp / "runtime"
        root.mkdir(parents=True)
        (root / "launcher.ok").write_text("ok\n", encoding="utf-8")
        return operational.verify_repaired_readiness(root=root, expected_paths=["launcher.ok"], generation_id="g")["status"] == "green"

    def a04_primary(tmp: Path, _monkey: Any) -> Any:
        result = verify_public_surface.probe_readiness(root=tmp, expected_paths=["missing"], generation_id="g")
        return result["status"] == "red" and result["mutationCount"] == 0

    def a04_boundary(tmp: Path, _monkey: Any) -> Any:
        with pytest.raises(ValueError, match="NG_READINESS_ROOT_INVALID"):
            verify_public_surface.probe_readiness(root=tmp, expected_paths=["../outside"], generation_id="g")
        return True

    def a04_recovery(tmp: Path, _monkey: Any) -> Any:
        tmp.mkdir(parents=True, exist_ok=True)
        (tmp / "ready").write_text("ready\n", encoding="utf-8")
        first = verify_public_surface.probe_readiness(root=tmp, expected_paths=["ready"], generation_id="g")
        second = verify_public_surface.probe_readiness(root=tmp, expected_paths=["ready"], generation_id="g")
        return first == second and second["mutationCount"] == 0

    def a05_primary(tmp: Path, _monkey: Any) -> Any:
        return checkpoint.resume_stage(checkpoint=_checkpoint(tmp), wrapper_result={"checkpointAlreadyMaterialized": True, "exitCode": 126})["modelCalls"] == 0

    def a05_boundary(tmp: Path, _monkey: Any) -> Any:
        value = _checkpoint(tmp)
        value["outputHash"] = "tampered"
        with pytest.raises(checkpoint.NewsGraspCheckpointError, match="NG_CHECKPOINT_INVALID"):
            checkpoint.resume_stage(checkpoint=value, wrapper_result={"checkpointAlreadyMaterialized": True, "exitCode": 126})
        return True

    def a05_recovery(tmp: Path, _monkey: Any) -> Any:
        result = checkpoint.resume_stage(checkpoint=_checkpoint(tmp), wrapper_result={"checkpointAlreadyMaterialized": True, "exitCode": "timeout"})
        return result["status"] == "continue_deterministic" and result["modelCalls"] == 0

    def a06_primary(tmp: Path, _monkey: Any) -> Any:
        ledger = checkpoint.retry_ledger(tmp / "retry.json")
        key = "2026-08-11|lineage-001|deepdive|route|failure"
        ledger.admit_retry(key=key, fingerprint="fp", cause_hash="cause")
        return ledger.admit_retry(key=key, fingerprint="fp", cause_hash="cause")["retry"] == 0

    def a06_boundary(tmp: Path, _monkey: Any) -> Any:
        ledger = checkpoint.retry_ledger(tmp / "retry.json")
        key = "2026-08-11|lineage-001|deepdive|route|failure"
        ledger.admit_retry(key=key, fingerprint="fp-a", cause_hash="cause")
        return ledger.admit_retry(key=key, fingerprint="fp-b", cause_hash="cause")["retry"] == 0

    def a06_recovery(tmp: Path, _monkey: Any) -> Any:
        ledger = checkpoint.retry_ledger(tmp / "retry.json")
        key = "2026-08-11|lineage-001|deepdive|route|failure"
        ledger.admit_retry(key=key, fingerprint="fp-a", cause_hash="cause-a")
        return ledger.admit_retry(key=key, fingerprint="fp-b", cause_hash="cause-b")["retry"] == 1

    def a07_primary(_tmp: Path, _monkey: Any) -> Any:
        return runner.daily_gate({oracle: True for oracle in gates.DAILY_ORACLES})["status"] == "green"

    def a07_boundary(_tmp: Path, _monkey: Any) -> Any:
        with pytest.raises(gates.NewsGraspGateProfileError, match="NG_RELEASE_GATE_REACHED_FROM_DAILY"):
            runner.validate_scheduled_calls(["artifact_schema_quality", "pytest"])
        return True

    def a07_recovery(_tmp: Path, _monkey: Any) -> Any:
        return runner.release_gate({oracle: True for oracle in gates.RELEASE_ORACLES})["status"] == "green"

    def a08_primary(_tmp: Path, _monkey: Any) -> Any:
        called: list[str] = []
        result = builders.build_summary_audio_script({"issueDate": "2026-08-11", "title": "焦点", "sections": ["一"]})
        called.append(result["schemaVersion"])
        return called == ["SUMMARY_AUDIO_SCRIPT_V1"]

    def a08_boundary(_tmp: Path, _monkey: Any) -> Any:
        with pytest.raises(builders.NewsGraspBuilderError, match="NG_BUILDER_BUNDLE_INCOMPLETE"):
            builders.build_distribution_manifest({"summary": {"hash": "a"}})
        return True

    def a08_recovery(_tmp: Path, _monkey: Any) -> Any:
        result = builders.build_public_republish({"issueDate": "2026-08-11", "artifactKey": "deepdive", "outputHash": "h", "oracleId": "o"})
        return result["modelCalls"] == 0 and result["sourceWriteCount"] == 0

    def a09_primary(_tmp: Path, _monkey: Any) -> Any:
        return operational.evaluate_bundle({"summary": {"h": "1"}})["status"] == "incomplete"

    def a09_boundary(_tmp: Path, _monkey: Any) -> Any:
        value = {"summary": {"h": "1"}, "deepdive": {"h": "2"}, "dialogue": {"h": "3"}, "audio": {"h": "4"}, "existingArtifactHashes": {"deepdive": "2"}, "artifactHashes": {"deepdive": "2"}, "modelCalls": 9}
        result = operational.evaluate_bundle(value)
        return result["reuseExisting"] is True and result["modelCalls"] == 0

    def a09_recovery(tmp: Path, _monkey: Any) -> Any:
        result = checkpoint.resume_stage(checkpoint=_checkpoint(tmp), wrapper_result={"checkpointAlreadyMaterialized": True, "exitCode": 126})
        return result["nextStep"] == "dialogue"

    def a10_primary(_tmp: Path, monkey: Any) -> Any:
        _tmp.mkdir(parents=True, exist_ok=True)
        root = _tmp / "build" / "incidents"
        root.mkdir(parents=True)
        monkey.setattr(audit, "CANONICAL_REPO_ROOT", _tmp)
        monkey.setattr(audit, "CANONICAL_TERMINAL_ROOT", root)
        terminal = audit.append_observation(_audit_decision())
        return terminal["eventSequence"] == 1

    def a10_boundary(_tmp: Path, monkey: Any) -> Any:
        _tmp.mkdir(parents=True, exist_ok=True)
        root = _tmp / "build" / "incidents"
        root.mkdir(parents=True)
        monkey.setattr(audit, "CANONICAL_REPO_ROOT", _tmp)
        monkey.setattr(audit, "CANONICAL_TERMINAL_ROOT", root)
        value = _audit_decision()
        audit.append_observation(value)
        with pytest.raises(ValueError, match="AUDIT_EVENT_REPLAY"):
            audit.append_observation(value)
        return True

    def a10_recovery(_tmp: Path, _monkey: Any) -> Any:
        previous = {"eventHash": "0" * 64, "completionAuthorityId": "authority"}
        event = audit._build_audit_observation_event(decision={"issueDate": "2026-08-11", "completionAuthorityId": "authority", "terminal": "audit_recovered_green", "receiptSha256": "r"}, previous=previous, sequence=2)
        return event["previousEventHash"] == previous["eventHash"] and event["completionAuthorityId"] == "authority"

    def a11_primary(_tmp: Path, _monkey: Any) -> Any:
        return human_impact.validate({"noFocusTheft": True, "noAutoOpen": True, "noUserMonitoring": True, "ownedProcessOnly": True})["status"] == "green"

    def a11_boundary(_tmp: Path, _monkey: Any) -> Any:
        with pytest.raises(human_impact.HumanImpactContractError, match="NG_RAW_PROCESS_TERMINATION_FORBIDDEN"):
            human_impact.validate({"noFocusTheft": True, "noAutoOpen": True, "noUserMonitoring": True, "ownedProcessOnly": True, "rawProcessKill": True})
        return True

    def a11_recovery(_tmp: Path, _monkey: Any) -> Any:
        return human_impact.validate({"noFocusTheft": True, "noAutoOpen": True, "noUserMonitoring": True, "ownedProcessOnly": True})["ownedProcessOnly"] is True

    def a12_primary(tmp: Path, _monkey: Any) -> Any:
        f = _generation(tmp)
        return generation.verify_parity(manifest=f["manifest"], source_root=f["source"], runtime_root=f["runtime"], config_path=f["source"] / "config/news_grasp_product_write_allowlist_v1.json", launcher_paths=[f["launcher"]], task_action=f["action"], task_trigger={"daily": "06:00"})["status"] == "green"

    def a12_boundary(tmp: Path, _monkey: Any) -> Any:
        f = _generation(tmp)
        f["launcher"].write_text("stale\n", encoding="utf-8")
        with pytest.raises(generation.NewsGraspGenerationError, match="NG_GENERATION_DRIFT"):
            generation.verify_parity(manifest=f["manifest"], source_root=f["source"], runtime_root=f["runtime"], config_path=f["source"] / "config/news_grasp_product_write_allowlist_v1.json", launcher_paths=[f["launcher"]], task_action=f["action"], task_trigger={"daily": "06:00"})
        return True

    def a12_recovery(tmp: Path, _monkey: Any) -> Any:
        f = _generation(tmp)
        pointer = tmp / "active.json"
        return generation.rollback(previous_manifest=f["manifest"], active_pointer=pointer, source_root=f["source"], runtime_root=f["runtime"], config_path=f["source"] / "config/news_grasp_product_write_allowlist_v1.json", launcher_paths=[f["launcher"]], task_action=f["action"], task_trigger={"daily": "06:00"})["status"] == "green"

    def a13_primary(tmp: Path, _monkey: Any) -> Any:
        repo = _small_repo(tmp)
        snap = _change_snapshot(repo, tmp)
        return change_control.validate_packet(repo_root=repo, packet=_change_packet(repo, snap))["executor"]["model"] == "gpt-5.6-luna"

    def a13_boundary(tmp: Path, _monkey: Any) -> Any:
        repo = _small_repo(tmp)
        snap = _change_snapshot(repo, tmp)
        packet = _change_packet(repo, snap)
        packet["executor"]["model"] = "gpt-5.6-sol"
        with pytest.raises(change_control.NewsGraspChangeControlError, match="NG_PACKET_CONTRACT_INVALID"):
            change_control.validate_packet(repo_root=repo, packet=packet)
        return True

    def a13_recovery(tmp: Path, _monkey: Any) -> Any:
        repo = _small_repo(tmp)
        snap = _change_snapshot(repo, tmp)
        (repo / "tools" / "news_grasp_change_control.py").write_text("owner\n", encoding="utf-8")
        with pytest.raises(change_control.NewsGraspChangeControlError, match="NG_BASELINE_DRIFT"):
            change_control.validate_packet(repo_root=repo, packet=_change_packet(repo, snap))
        return True

    def a14_primary(_tmp: Path, _monkey: Any) -> Any:
        return nopublish.execute_no_publish({"executionMode": "scheduled-equivalent-nopublish", "status": "green"})["status"] == "green"

    def a14_boundary(_tmp: Path, _monkey: Any) -> Any:
        with pytest.raises(nopublish.NewsGraspNoPublishError, match="NG_NOPUBLISH_SIDE_EFFECT"):
            nopublish.execute_no_publish({"executionMode": "scheduled-equivalent-nopublish", "status": "green", "publishCount": 1})
        return True

    def a14_recovery(_tmp: Path, _monkey: Any) -> Any:
        return nopublish.execute_no_publish({"executionMode": "scheduled-equivalent-nopublish", "status": "red", "failed": True, "resumeForbidden": True})["attemptFrozen"] is True

    return [
        (f"NG2-A{i:02d}-{perspective}", fn)
        for i, group in enumerate([
            (a01_primary, a01_boundary, a01_recovery), (a02_primary, a02_boundary, a02_recovery),
            (a03_primary, a03_boundary, a03_recovery), (a04_primary, a04_boundary, a04_recovery),
            (a05_primary, a05_boundary, a05_recovery), (a06_primary, a06_boundary, a06_recovery),
            (a07_primary, a07_boundary, a07_recovery), (a08_primary, a08_boundary, a08_recovery),
            (a09_primary, a09_boundary, a09_recovery), (a10_primary, a10_boundary, a10_recovery),
            (a11_primary, a11_boundary, a11_recovery), (a12_primary, a12_boundary, a12_recovery),
            (a13_primary, a13_boundary, a13_recovery), (a14_primary, a14_boundary, a14_recovery),
        ], start=1)
        for perspective, fn in zip(("primary_behavior", "adversarial_boundary", "operational_recovery"), group)
    ]


@pytest.mark.parametrize("node_id,case", _cases(), ids=lambda value: value[0] if isinstance(value, tuple) else str(value))
def test_ng2_wp02_matrix_red_green(node_id: str, case: Callable[[Path, Any], Any], tmp_path: Path, monkeypatch: Any) -> None:
    assert case(tmp_path / node_id.replace("/", "-"), monkeypatch) is True


def test_ng2_wp02_matrix_matches_declared_nodes() -> None:
    declared = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))["nodes"]
    declared_ids = {row["nodeId"] for row in declared}
    observed_ids = {node_id for node_id, _ in _cases()}
    assert observed_ids == declared_ids
