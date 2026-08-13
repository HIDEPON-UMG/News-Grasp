"""WP-00 NG2-A01/A13 のproduct-local change admission契約。"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
from typing import Any, Mapping

import pytest

from tools import news_grasp_change_control as control
from tools import operational_recovery_registry as recovery_registry
from tools import news_grasp_generation as generation
from tools import news_grasp_operational_contract as operational
from tools import news_grasp_checkpoint as checkpoint
from tools import news_grasp_deterministic_builders as builders
from tools import news_grasp_human_impact as human_impact
from tools import news_grasp_gate_profiles as gates
from tools import news_grasp_daily_control as daily_control
from tools import news_grasp_asset_manifest as assets


ALLOWED = [
    "tools/news_grasp_change_control.py",
    "config/news_grasp_product_write_allowlist_v1.json",
    "tests/test_operational_redesign_contract.py",
]
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
            {
                "candidateId": "single-consumer",
                "goalFidelity": True,
                "safetyComplete": True,
                "expectedTotalResource": 1.0,
                "resourceVector": {
                    "modelCalls": 0,
                    "toolCalls": 2,
                    "expectedRetries": 0,
                    "broadRegressions": 0,
                    "e2eAttempts": 0,
                    "humanOperations": 0,
                    "wallClockMinutes": 5,
                },
            },
            {
                "candidateId": "duplicate-consumers",
                "goalFidelity": True,
                "safetyComplete": True,
                "expectedTotalResource": 2.0,
                "resourceVector": {
                    "modelCalls": 0,
                    "toolCalls": 4,
                    "expectedRetries": 0,
                    "broadRegressions": 0,
                    "e2eAttempts": 0,
                    "humanOperations": 0,
                    "wallClockMinutes": 10,
                },
            },
        ],
        "selectedCandidateId": "single-consumer",
        "unresolvedDecisionIds": [],
    }


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    (repo / "config").mkdir()
    (repo / "tests").mkdir()
    (repo / "tools/news_grasp_change_control.py").write_text("baseline-tools\n", encoding="utf-8")
    (repo / "config/news_grasp_product_write_allowlist_v1.json").write_text(
        json.dumps(
            {
                "schemaVersion": "NEWS_GRASP_PRODUCT_WRITE_ALLOWLIST_V1",
                "productId": "News-Grasp",
                "allowedPaths": ALLOWED,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (repo / "config/news_grasp_product_change_routes_v1.json").write_text(
        json.dumps(
            {
                "schemaVersion": "NEWS_GRASP_PRODUCT_CHANGE_ROUTES_V1",
                "productId": "News-Grasp",
                "unknownRoutePolicy": "fail_closed",
                "consumer": "tools.news_grasp_change_control.apply_packet",
                "routes": [
                    {"routeId": route_id, "producer": route_id, "executor": executor}
                    for route_id, executor in control.EXPECTED_ROUTE_EXECUTORS.items()
                ],
            }
        ),
        encoding="utf-8",
    )
    (repo / "tests/test_operational_redesign_contract.py").write_text(
        "baseline-tests\n", encoding="utf-8"
    )
    return repo


def _snapshot(repo: Path, tmp_path: Path, owner: str = "owner-a") -> Path:
    output = tmp_path / f"snapshot-{owner}.json"
    control.snapshot(
        repo_root=repo,
        target_manifest={"targets": [ALLOWED[0]], "ownerThreadId": owner, "actorRouteId": "luna"},
        output=output,
    )
    return output


def _packet(repo: Path, snapshot: Path, *, owner: str = "owner-a", path: str = ALLOWED[0]) -> dict:
    return {
        "schemaVersion": "NEWS_GRASP_CHANGE_PACKET_V1",
        "packetId": "NG2-WP00-TEST-001",
        "ownerThreadId": owner,
        "actorRouteId": "luna",
        "executor": {"model": "gpt-5.6-luna", "reasoningEffort": "max", "noSubstitution": True},
        "snapshotPath": str(snapshot),
        "changes": [{"path": path, "operation": "replace", "content": "candidate\n"}],
        "unresolvedDecisionIds": [],
        "allowedWriteSet": [path],
        "taskConstitution": _task_constitution(path),
        "repoRoot": str(repo),
    }


def test_ng2_a01_primary_product_write_allowlist(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    snapshot = _snapshot(repo, tmp_path)
    result = control.validate_packet(repo_root=repo, packet=_packet(repo, snapshot))
    assert result["status"] == "validated"
    assert result["allowedWriteSet"] == [ALLOWED[0]]


def test_ng2_a01_adversarial_scope_escape(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    snapshot = _snapshot(repo, tmp_path)
    packet = _packet(repo, snapshot, path="../shared-harness.txt")
    with pytest.raises(control.NewsGraspChangeControlError, match="NG_CHANGE_PATH_INVALID"):
        control.validate_packet(repo_root=repo, packet=packet)


def test_ng2_a01_recovery_concurrent_owner(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _snapshot(repo, tmp_path, owner="owner-a")
    with pytest.raises(control.NewsGraspChangeControlError, match="NG_CONCURRENT_OWNER_PRESENT"):
        _snapshot(repo, tmp_path, owner="owner-b")


@pytest.mark.parametrize("route_id", sorted(control.EXPECTED_ROUTE_EXECUTORS))
def test_ng2_a01_all_registered_routes_use_the_single_packet_consumer(
    tmp_path: Path, route_id: str
) -> None:
    repo = _repo(tmp_path)
    snapshot = tmp_path / f"snapshot-{route_id}.json"
    control.snapshot(
        repo_root=repo,
        target_manifest={
            "targets": [ALLOWED[0]],
            "ownerThreadId": "owner-a",
            "actorRouteId": route_id,
        },
        output=snapshot,
    )
    packet = _packet(repo, snapshot)
    packet["packetId"] = f"NG2-WP00-ROUTE-{route_id}"
    packet["actorRouteId"] = route_id
    packet["executor"] = control.EXPECTED_ROUTE_EXECUTORS[route_id]
    result = control.validate_packet(repo_root=repo, packet=packet)
    assert result["status"] == "validated"
    assert result["allowedWriteSet"] == [ALLOWED[0]]


def test_ng2_a13_primary_luna_packet_decision_complete(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    snapshot = _snapshot(repo, tmp_path)
    result = control.validate_packet(repo_root=repo, packet=_packet(repo, snapshot))
    assert result["executor"] == {
        "model": "gpt-5.6-luna",
        "reasoningEffort": "max",
        "noSubstitution": True,
    }
    assert result["unresolvedDecisionIds"] == []
    assert len(result["taskConstitutionAdmissionSha256"]) == 64


def test_ng2_a13_boundary_rejects_missing_task_constitution(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    snapshot = _snapshot(repo, tmp_path)
    packet = _packet(repo, snapshot)
    packet.pop("taskConstitution")
    with pytest.raises(
        control.NewsGraspChangeControlError,
        match="NG_TASK_CONSTITUTION_ADMISSION_REQUIRED",
    ):
        control.validate_packet(repo_root=repo, packet=packet)


def test_ng2_a13_adversarial_packet_scope_or_model_drift(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    snapshot = _snapshot(repo, tmp_path)
    packet = _packet(repo, snapshot, path="tools/other.py")
    packet["executor"]["model"] = "gpt-5.6-sol"
    with pytest.raises(control.NewsGraspChangeControlError, match="NG_PACKET_CONTRACT_INVALID"):
        control.validate_packet(repo_root=repo, packet=packet)


def test_ng2_a13_recovery_rebase_packet_after_owner_commit(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    snapshot = _snapshot(repo, tmp_path)
    (repo / ALLOWED[0]).write_text("owner-commit\n", encoding="utf-8")
    with pytest.raises(control.NewsGraspChangeControlError, match="NG_BASELINE_DRIFT"):
        control.validate_packet(repo_root=repo, packet=_packet(repo, snapshot))


def test_ng2_wp01_registry_has_exact_registered_handlers() -> None:
    repo = Path(__file__).resolve().parents[1]
    result = recovery_registry.validate_registry(repo)
    assert result["status"] == "validated"
    assert result["handlerCount"] == 16
    assert result["unknownReasonHandler"] == "major_incident_terminal"


def test_ng2_wp01_unknown_reason_is_append_only_major_incident() -> None:
    repo = Path(__file__).resolve().parents[1]
    seen: list[Mapping[str, object]] = []

    def major_incident(context: Mapping[str, object]) -> Mapping[str, object]:
        seen.append(context)
        return {"event": "audit_major_incident_open"}

    result = recovery_registry.dispatch(
        repo_root=repo,
        reason_code="UNREGISTERED_REASON",
        context={"mutationCount": 0},
        handlers={"major_incident_terminal": major_incident},
    )
    assert result.handler_id == "major_incident_terminal"
    assert result.reason_code == "UNKNOWN_REASON"
    assert seen == [{"mutationCount": 0}]


def test_ng2_wp01_registered_reason_has_one_exact_owner() -> None:
    repo = Path(__file__).resolve().parents[1]
    called: list[str] = []

    def reconcile(context: Mapping[str, object]) -> Mapping[str, object]:
        called.append(str(context["reason"]))
        return {"action": "reconcile"}

    result = recovery_registry.dispatch(
        repo_root=repo,
        reason_code="GENERATION_DRIFT",
        context={"reason": "GENERATION_DRIFT"},
        handlers={"active_generation_reconcile": reconcile},
    )
    assert result.handler_id == "active_generation_reconcile"
    assert called == ["GENERATION_DRIFT"]


def test_ng2_wp06_default_registry_handlers_are_registered_and_typed() -> None:
    repo = Path(__file__).resolve().parents[1]
    handlers = recovery_registry.default_handlers()
    assert set(handlers) == set(recovery_registry.EXPECTED_HANDLER_IDS)
    result = recovery_registry.dispatch(
        repo_root=repo,
        reason_code="UNKNOWN_REASON",
        context={"reasonCode": "UNKNOWN_REASON"},
        handlers=handlers,
    )
    assert result.handler_id == "major_incident_terminal"
    assert result.result["mutationCount"] == 0


def test_ng2_wp06_all_registered_reason_codes_dispatch_once() -> None:
    repo = Path(__file__).resolve().parents[1]
    handlers = recovery_registry.default_handlers()
    registry = json.loads((repo / "config/operational_recovery_registry_v1.json").read_text(encoding="utf-8"))
    for entry in registry["handlers"]:
        handler_id = entry["handlerId"]
        reason_code = entry["reasonCodes"][0]
        context: dict[str, Any] = {"reasonCode": reason_code, "dailyOperationLineageId": "lineage-1"}
        if handler_id == "summary_audio_script_builder":
            context["summary"] = {"issueDate": "2026-08-11", "title": "焦点", "sections": ["第一論点"]}
        elif handler_id == "deepdive_dialogue_builder":
            context["article"] = {"issueDate": "2026-08-11", "title": "記事", "body": "根拠", "provenanceHash": "p"}
        elif handler_id == "deepdive_audio_builder":
            context["turns"] = [{"speaker": "編集者", "text": "記事"}]
        elif handler_id == "distribution_manifest_builder":
            context["artifacts"] = {"summary": {"hash": "s"}, "deepdive": {"hash": "d"}, "audio": {"hash": "a"}}
        elif handler_id in {"reporter_artifact_model_route", "summary_model_route", "deepdive_article_model_route"}:
            context["routeReceipt"] = {"routeId": handler_id}
        result = recovery_registry.dispatch(repo_root=repo, reason_code=reason_code, context=context, handlers=handlers)
        assert result.handler_id == handler_id
        assert isinstance(result.result, Mapping)


def test_ng2_wp02_acceptance_matrix_has_42_unique_nodes() -> None:
    repo = Path(__file__).resolve().parents[1]
    matrix = json.loads(
        (repo / "tests/fixtures/operational-redesign/acceptance-matrix-v1.json").read_text(encoding="utf-8")
    )
    nodes = matrix["nodes"]
    assert len(nodes) == 42
    assert len({node["nodeId"] for node in nodes}) == 42
    assert {node["perspective"] for node in nodes} == {
        "primary_behavior",
        "adversarial_boundary",
        "operational_recovery",
    }
    assert {node["acceptanceId"] for node in nodes} == {f"NG2-A{i:02d}" for i in range(1, 15)}


def test_ng2_wp02_historical_fixture_preserves_public_and_lineage() -> None:
    repo = Path(__file__).resolve().parents[1]
    fixture = json.loads(
        (repo / "tests/fixtures/operational-redesign/historical-2026-08-11.json").read_text(encoding="utf-8")
    )
    assert fixture["issueDate"] == "2026-08-11"
    assert fixture["expected"]["publicAuthorityPreserved"] is True
    assert fixture["expected"]["dailyOperationLineagePreserved"] is True
    assert sum(event["modelCalls"] for event in fixture["events"]) == 1


def test_ng2_wp02_matrix_does_not_admit_placeholder_or_mock_only_consumer() -> None:
    repo = Path(__file__).resolve().parents[1]
    matrix = json.loads(
        (repo / "tests/fixtures/operational-redesign/acceptance-matrix-v1.json").read_text(encoding="utf-8")
    )
    for node in matrix["nodes"]:
        assert "placeholder" not in node["oracle"]
        assert "mock" not in node["consumer"]


def test_ng3_wp17_r6_additional_matrix_has_exact_15_nodes() -> None:
    repo = Path(__file__).resolve().parents[1]
    matrix = json.loads(
        (repo / "tests/fixtures/operational-redesign/r6-additional-matrix.json").read_text(encoding="utf-8")
    )
    nodes = matrix["nodes"]
    assert len(nodes) == 15
    assert len({node["nodeId"] for node in nodes}) == 15
    assert {node["perspective"] for node in nodes} == {
        "primary_behavior", "adversarial_boundary", "operational_recovery"
    }
    assert all("mock" not in node["consumer"] and "placeholder" not in node["oracle"] for node in nodes)


def _generation_fixture(tmp_path: Path) -> dict[str, object]:
    source = _repo(tmp_path)
    for command in (
        ["git", "-C", str(source), "init", "-q"],
        ["git", "-C", str(source), "config", "user.name", "News-Grasp Test"],
        ["git", "-C", str(source), "config", "user.email", "test@example.invalid"],
        ["git", "-C", str(source), "remote", "add", "origin", "https://example.invalid/news-grasp.git"],
        ["git", "-C", str(source), "add", "--all"],
        ["git", "-C", str(source), "commit", "-q", "-m", "generation fixture"],
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
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "runtime.txt").write_text("runtime-v1\n", encoding="utf-8")
    launcher = tmp_path / "news-grasp-task-launcher.pyw"
    launcher.write_text("launcher-v1\n", encoding="utf-8")
    output = tmp_path / "generation.json"
    action = ["pythonw.exe", "news-grasp-task-launcher.pyw", "--mode", "production"]
    manifest = generation.create_manifest(
        source_root=source,
        source_paths=["tools/news_grasp_change_control.py"],
        runtime_root=runtime,
        runtime_paths=["runtime.txt"],
        config_path=source / "config/news_grasp_product_write_allowlist_v1.json",
        launcher_paths=[launcher],
        task_action=action,
        task_trigger={"daily": "06:00"},
        generation_id="generation-001",
        previous_generation_id=None,
        output=output,
    )
    return {"source": source, "runtime": runtime, "launcher": launcher, "manifest": manifest, "action": action}


def test_ng2_a02_primary_generation_parity_green(tmp_path: Path) -> None:
    fixture = _generation_fixture(tmp_path)
    result = generation.verify_parity(
        manifest=fixture["manifest"],
        source_root=fixture["source"],
        runtime_root=fixture["runtime"],
        config_path=fixture["source"] / "config/news_grasp_product_write_allowlist_v1.json",
        launcher_paths=[fixture["launcher"]],
        task_action=fixture["action"],
        task_trigger={"daily": "06:00"},
    )
    assert result["status"] == "green"


def test_ng2_a02_adversarial_runtime_drift_rejected(tmp_path: Path) -> None:
    fixture = _generation_fixture(tmp_path)
    Path(fixture["runtime"] / "runtime.txt").write_text("runtime-tampered\n", encoding="utf-8")
    with pytest.raises(generation.NewsGraspGenerationError, match="NG_GENERATION_DRIFT"):
        generation.verify_parity(
            manifest=fixture["manifest"],
            source_root=fixture["source"],
            runtime_root=fixture["runtime"],
            config_path=fixture["source"] / "config/news_grasp_product_write_allowlist_v1.json",
            launcher_paths=[fixture["launcher"]],
            task_action=fixture["action"],
            task_trigger={"daily": "06:00"},
        )


def test_ng2_a02_recovery_rollback_previous_generation(tmp_path: Path) -> None:
    fixture = _generation_fixture(tmp_path)
    pointer = tmp_path / "active.json"
    generation.activate(
        manifest=fixture["manifest"],
        active_pointer=pointer,
        source_root=fixture["source"],
        runtime_root=fixture["runtime"],
        config_path=fixture["source"] / "config/news_grasp_product_write_allowlist_v1.json",
        launcher_paths=[fixture["launcher"]],
        task_action=fixture["action"],
        task_trigger={"daily": "06:00"},
    )
    active = json.loads(pointer.read_text(encoding="utf-8"))
    assert active["generationId"] == "generation-001"


def test_ng2_wp03_installer_tasks_bind_stable_launcher_without_worktree_path() -> None:
    repo = Path(__file__).resolve().parents[1]
    installer = (repo / "scripts/ops/install-news-grasp-ops.ps1").read_text(encoding="utf-8-sig")
    assert '$runnerArgs = "`"$taskLauncherPath`" runner --scheduled-task-name `"$RunnerTaskName`" --high-cost-binding-path' in installer
    assert '$bootstrapArgs = "`"$taskLauncherPath`" bootstrap --scheduled-task-name `"$BootstrapTaskName`" --high-cost-binding-path' in installer
    assert '$deadmanArgs = "`"$deadmanLauncherPath`""' in installer
    assert '--repo-dir `"$RepoDir`"' not in installer


def test_ng2_wp03_generation_manifest_rejects_worktree_bound_task_action() -> None:
    with pytest.raises(generation.NewsGraspGenerationError, match="NG_TASK_ACTION_WORKTREE_OVERRIDE"):
        generation.validate_task_action(["pythonw.exe", "launcher.pyw", "--repo-dir", "C:/worktree"])


def test_ng3_wp03_runtime_transaction_seals_active_generation(tmp_path: Path) -> None:
    source_repo = Path(__file__).resolve().parents[1]
    launcher_path = source_repo / "scripts/ops/news-grasp-task-launcher.pyw"
    loader = SourceFileLoader("news_grasp_task_launcher_generation_test", str(launcher_path))
    spec = spec_from_loader(loader.name, loader)
    assert spec is not None
    launcher = module_from_spec(spec)
    loader.exec_module(launcher)
    bin_dir = tmp_path / "bin"
    runtime_root = tmp_path / ".news-grasp-runtime"
    repo = tmp_path / "clean-generation"
    bin_dir.mkdir()
    runtime_root.mkdir()
    repo.mkdir()
    generation_paths = (
        "scripts/ops/news-grasp-runner.ps1",
        "scripts/ops/news-grasp-task-launcher.pyw",
        "scripts/ops/news-grasp-bootstrap.ps1",
        "tools/daily_self_heal.py",
        "tools/news_grasp_daily_control.py",
        "tools/news_grasp_operational_contract.py",
        "tools/news_grasp_checkpoint.py",
        "tools/news_grasp_generation.py",
        "tools/operational_recovery_registry.py",
        "config/operational_recovery_registry_v1.json",
    )
    for relative in generation_paths:
        source = source_repo / relative
        destination = repo / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
    for command in (
        ["git", "-C", str(repo), "init", "-q"],
        ["git", "-C", str(repo), "config", "user.name", "News-Grasp Test"],
        ["git", "-C", str(repo), "config", "user.email", "test@example.invalid"],
        ["git", "-C", str(repo), "add", "--all"],
        ["git", "-C", str(repo), "commit", "-q", "-m", "fixture generation"],
    ):
        subprocess.run(command, check=True, capture_output=True)
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()
    subprocess.run(
        ["git", "-C", str(repo), "update-ref", "refs/remotes/origin/main", head],
        check=True,
        capture_output=True,
    )
    authority = {
        "schemaVersion": "STABLE_TASK_AUTHORITY_V1",
        "taskName": "News-Grasp Runner",
        "stableLauncherPath": str(launcher_path.resolve()),
        "stableLauncherSha256": hashlib.sha256(launcher_path.read_bytes()).hexdigest(),
        "bootstrapPath": str((source_repo / "scripts/ops/news-grasp-bootstrap.ps1").resolve()),
        "bootstrapSha256": "b" * 64,
        "action": ["pythonw.exe", str(launcher_path.resolve()), "runner"],
        "trigger": {"daily": "06:00"},
        "repoArgumentCount": 0,
    }
    authority["authoritySha256"] = launcher._sha256_json(authority)
    (bin_dir / "news-grasp-stable-task-authority-v1.json").write_text(
        json.dumps(authority, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (bin_dir / "news-grasp-runtime-root-v1.json").write_text(
        json.dumps(
            {
                "schemaVersion": "NEWS_GRASP_RUNTIME_ROOT_V1",
                "repoDir": str(repo),
                "pythonExe": sys.executable,
                "evidenceRepoDir": str(repo),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    pointer = launcher._seal_active_production_generation(
        source_repo=repo,
        runtime_repo=repo,
        runtime_root=runtime_root,
        origin_sha=head,
        bin_dir=bin_dir,
    )
    assert len(str(pointer["generationId"])) == 64
    assert pointer["phase"] == "transaction_committed"
    assert (runtime_root / "active-generation-v2.json").is_file()
    assert (runtime_root / "generations" / f"{pointer['generationId']}.json").is_file()
    repeated = launcher._seal_active_production_generation(
        source_repo=repo,
        runtime_repo=repo,
        runtime_root=runtime_root,
        origin_sha=head,
        bin_dir=bin_dir,
    )
    assert repeated == pointer


def test_ng2_wp05_daily_lineage_does_not_reset_on_run_or_session_change() -> None:
    first = daily_control._daily_operation_lineage_id(
        issue_date="2026-08-11",
        runner_state={"scheduledAuthorityId": "authority-1", "run_id": "run-1", "sessionId": "s-1"},
    )
    second = daily_control._daily_operation_lineage_id(
        issue_date="2026-08-11",
        runner_state={"scheduledAuthorityId": "authority-1", "run_id": "run-2", "sessionId": "s-2", "receiptPath": "other"},
    )
    assert first == second


def test_ng2_wp07_runner_defers_release_regression_until_final_nopublish() -> None:
    repo = Path(__file__).resolve().parents[1]
    runner_text = (repo / "scripts/ops/news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    assert "ReleaseGateProfile deferred from scheduled/recovery path" in runner_text
    assert "if (-not $NoPublish)" in runner_text
    assert "pytest tests/ -q -m \"not network\"" in runner_text


def test_ng2_wp08_historical_2026_08_11_closes_without_model_reexecution(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[1]
    fixture = json.loads((repo / "tests/fixtures/operational-redesign/historical-2026-08-11.json").read_text(encoding="utf-8"))
    checkpoint_value = _checkpoint_fixture(tmp_path)
    resumed = checkpoint.resume_stage(
        checkpoint=checkpoint_value,
        wrapper_result={"checkpointAlreadyMaterialized": True, "exitCode": 126},
    )
    vector = operational.evaluate_completion(
        scheduled_attempt={"status": "failed"},
        recovery_attempt={"status": "succeeded"},
        public_receipt={"status": "verified_green", "authorityId": "authority-2026-08-11"},
        readiness_probe={"status": "green"},
        audit_observation={"status": "recovered", "causeFingerprint": "cause-2026-08-11"},
    )
    assert fixture["expected"]["publicAuthorityPreserved"] is True
    assert fixture["expected"]["dailyOperationLineagePreserved"] is True
    assert resumed["modelCalls"] == 0
    assert vector["publicCompletionStatus"] == "green"
    assert vector["nextRunReadinessStatus"] == "green"


def test_ng2_a03_primary_readiness_red_keeps_public_green() -> None:
    result = operational.evaluate_completion(
        scheduled_attempt={"status": "completed"},
        recovery_attempt={"status": "not_needed"},
        public_receipt={"status": "verified_green", "authorityId": "authority-001"},
        readiness_probe={"status": "red"},
        audit_observation={"status": "observed", "causeFingerprint": "cause-001"},
    )
    assert result["publicCompletionStatus"] == "green"
    assert result["nextRunReadinessStatus"] == "red"
    assert result["operationalStatus"] == "degraded"


def test_ng2_a03_adversarial_verification_unavailable_preserves_authority() -> None:
    result = operational.evaluate_completion(
        scheduled_attempt={"status": "completed"},
        recovery_attempt={"status": "not_needed"},
        public_receipt={"status": "verification_unavailable", "previousVerifiedGreen": True, "authorityId": "authority-001"},
        readiness_probe={"status": "verification_unavailable"},
        audit_observation={"status": "unverified"},
    )
    assert result["publicCompletionStatus"] == "green"
    assert result["nextRunReadinessStatus"] == "verification_unavailable"


def test_ng3_v3_completion_vector_separates_external_and_constitution_status() -> None:
    result = operational.evaluate_completion_v3(
        scheduled_attempt={"status": "completed"},
        recovery_attempt={"status": "not_needed"},
        public_receipt={"status": "verified_green", "authorityId": "authority-001"},
        readiness_probe={"status": "red"},
        audit_observation={"status": "observed", "causeFingerprint": "cause-001"},
        external_dependency={"status": "external_deferred", "evidenceHash": "e" * 64},
        constitution_admission={"status": "green", "constitutionHash": "c" * 64},
    )
    assert result["schemaVersion"] == "COMPLETION_STATE_VECTOR_V3"
    assert result["publicCompletionStatus"] == "green"
    assert result["nextRunReadinessStatus"] == "red"
    assert result["externalDependencyStatus"] == "external_deferred"
    assert result["constitutionStatus"] == "green"
    assert result["operationalStatus"] == "degraded"


def test_ng3_v3_verified_public_regression_is_not_hidden_by_readiness_green() -> None:
    result = operational.evaluate_completion_v3(
        scheduled_attempt={"status": "failed"},
        recovery_attempt={"status": "failed"},
        public_receipt={"status": "verified_regression", "authorityId": "authority-002"},
        readiness_probe={"status": "green"},
        audit_observation={"status": "observed"},
        external_dependency={"status": "ready", "evidenceHash": "e" * 64},
        constitution_admission={"status": "green", "constitutionHash": "c" * 64},
    )
    assert result["publicCompletionStatus"] == "red"
    assert result["operationalStatus"] == "red"


def test_ng2_a03_recovery_pure_probe_after_registered_repair(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    authority = {"authorityId": "authority-001", "generationId": "generation-001", "causeFingerprint": "cause-001"}

    def handler(context: Mapping[str, object]) -> Mapping[str, object]:
        (root / "launcher.ok").write_text("ready\n", encoding="utf-8")
        return {"mutationCount": 1, "handler": "active_generation_reconcile"}

    repaired = operational.repair_readiness(authority=authority, reason_code="GENERATION_DRIFT", handler=handler)
    assert repaired["status"] == "repair_completed"
    verified = operational.verify_repaired_readiness(root=root, expected_paths=["launcher.ok"], generation_id="generation-001")
    assert verified["status"] == "green"
    assert verified["mutationCount"] == 0


def test_ng2_a04_readiness_probe_is_pure_and_root_bound(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    result = operational.probe_readiness(root=root, expected_paths=["missing.file"], generation_id="generation-001")
    assert result["status"] == "red"
    assert result["mutationCount"] == 0
    assert not (root / "missing.file").exists()


def _checkpoint_fixture(tmp_path: Path) -> dict[str, object]:
    fingerprint = checkpoint.cause_fingerprint(
        issue_date="2026-08-11",
        daily_operation_lineage_id="lineage-001",
        artifact_key="deepdive-article",
        stage_id="deepdive",
        producer_route_id="deepdive-article-model",
        failure_class="producer_source_failure",
        reason_code="WRAPPER_RC126",
        cause_input_mask=["sourceDependencyHash", "producerPromptHash"],
        input_hashes={"sourceDependencyHash": "source-a", "producerPromptHash": "prompt-a", "unrelated": "noise-a"},
    )
    return checkpoint.create_checkpoint(
        issue_date="2026-08-11",
        daily_operation_lineage_id="lineage-001",
        stage="deepdive",
        artifact_key="deepdive-article",
        input_hashes={"sourceDependencyHash": "source-a", "producerPromptHash": "prompt-a"},
        output_hash="output-a",
        schema="MARKDOWN_V1",
        oracle_id="deepdive-quality-v1",
        producer_route_id="deepdive-article-model",
        next_deterministic_step="build-dialogue",
        cause_fingerprint_value=fingerprint,
        output_path=tmp_path / "checkpoint.json",
    )


def test_ng2_a05_primary_valid_checkpoint_skips_model_after_wrapper_failure(tmp_path: Path) -> None:
    value = _checkpoint_fixture(tmp_path)
    result = checkpoint.resume_stage(checkpoint=value, wrapper_result={"checkpointAlreadyMaterialized": True, "exitCode": 126})
    assert result == {"status": "continue_deterministic", "modelCalls": 0, "nextStep": "build-dialogue"}


def test_ng2_a05_adversarial_invalid_checkpoint_is_rejected(tmp_path: Path) -> None:
    value = _checkpoint_fixture(tmp_path)
    value["outputHash"] = "tampered"
    with pytest.raises(checkpoint.NewsGraspCheckpointError, match="NG_CHECKPOINT_INVALID"):
        checkpoint.resume_stage(checkpoint=value, wrapper_result={"checkpointAlreadyMaterialized": True, "exitCode": 126})


def test_ng2_a05_recovery_missing_checkpoint_allows_one_producer_call(tmp_path: Path) -> None:
    result = checkpoint.resume_stage(
        checkpoint=None,
        wrapper_result={"checkpointAlreadyMaterialized": False, "exitCode": 126},
    )
    assert result == {"status": "producer_required", "modelCalls": 1, "nextStep": "stage_start"}


def test_ng2_a06_same_cause_fingerprint_retry_is_zero(tmp_path: Path) -> None:
    ledger = checkpoint.RetryLedger(tmp_path / "retry.json")
    first = ledger.admit_retry(key="2026-08-11|lineage-001|deepdive|route|producer_source_failure", fingerprint="fp-a", cause_hash="cause-a")
    second = ledger.admit_retry(key="2026-08-11|lineage-001|deepdive|route|producer_source_failure", fingerprint="fp-a", cause_hash="cause-a")
    assert first["retry"] == 0
    assert second == {"retry": 0, "reason": "same_cause_fingerprint"}


def test_ng2_a06_adversarial_unrelated_hash_does_not_reset_retry(tmp_path: Path) -> None:
    ledger = checkpoint.RetryLedger(tmp_path / "retry.json")
    key = "2026-08-11|lineage-001|deepdive|route|producer_source_failure"
    ledger.admit_retry(key=key, fingerprint="fp-a", cause_hash="cause-a")
    result = ledger.admit_retry(key=key, fingerprint="fp-b", cause_hash="cause-a")
    assert result == {"retry": 0, "reason": "cause_not_changed_or_budget_consumed"}


def test_ng2_a06_recovery_causal_hash_change_allows_one_retry(tmp_path: Path) -> None:
    ledger = checkpoint.RetryLedger(tmp_path / "retry.json")
    key = "2026-08-11|lineage-001|deepdive|route|producer_source_failure"
    ledger.admit_retry(key=key, fingerprint="fp-a", cause_hash="cause-a")
    result = ledger.admit_retry(key=key, fingerprint="fp-b", cause_hash="cause-b")
    assert result == {"retry": 1, "reason": "causal_input_changed"}


def test_ng2_a09_valid_checkpoint_is_not_regenerated(tmp_path: Path) -> None:
    value = _checkpoint_fixture(tmp_path)
    result = checkpoint.resume_stage(checkpoint=value, wrapper_result={"checkpointAlreadyMaterialized": True, "exitCode": "timeout"})
    assert result["modelCalls"] == 0


def test_ng2_a08_registered_deterministic_builder_reuses_artifact() -> None:
    result = builders.build_summary_audio_script(
        {"issueDate": "2026-08-11", "title": "本日の焦点", "sections": ["第一論点", "第二論点"]}
    )
    assert result["schemaVersion"] == "SUMMARY_AUDIO_SCRIPT_V1"
    assert result["text"].count("論点") == 2


def test_ng2_a08_unknown_builder_input_does_not_fallback_to_model() -> None:
    with pytest.raises(builders.NewsGraspBuilderError, match="NG_BUILDER_BUNDLE_INCOMPLETE"):
        builders.build_distribution_manifest({"summary": {"hash": "a"}, "deepdive": {"hash": "b"}})


def test_ng2_a08_public_republish_uses_checkpoint_provenance() -> None:
    result = builders.build_public_republish(
        {"issueDate": "2026-08-11", "artifactKey": "deepdive", "outputHash": "out-a", "oracleId": "oracle-a"}
    )
    assert result["outputHash"] == "out-a"
    assert result["oracleId"] == "oracle-a"


def test_ng2_a11_human_impact_contract_is_green() -> None:
    result = human_impact.validate_human_impact(
        {"noFocusTheft": True, "noAutoOpen": True, "noUserMonitoring": True, "ownedProcessOnly": True}
    )
    assert result["status"] == "green"


def test_ng2_a11_raw_process_termination_is_rejected() -> None:
    with pytest.raises(human_impact.HumanImpactContractError, match="NG_RAW_PROCESS_TERMINATION_FORBIDDEN"):
        human_impact.validate_human_impact(
            {"noFocusTheft": True, "noAutoOpen": True, "noUserMonitoring": True, "ownedProcessOnly": True, "rawProcessKill": True}
        )


def test_ng2_a07_daily_and_release_profiles_are_disjoint() -> None:
    result = gates.validate_profiles()
    assert result["status"] == "validated"
    assert not set(result["daily"]) & set(result["release"])


def test_ng2_a07_adversarial_release_oracle_from_daily_is_rejected() -> None:
    with pytest.raises(gates.NewsGraspGateProfileError, match="NG_RELEASE_GATE_REACHED_FROM_DAILY"):
        gates.scheduled_call_graph(calls=["artifact_schema_quality", "pytest"])


def test_ng2_a07_recovery_release_profile_is_explicit_one_call() -> None:
    result = gates.evaluate_release({oracle: True for oracle in gates.RELEASE_ORACLES})
    assert result["profile"] == "ReleaseGateProfileV1"
    assert result["status"] == "green"


def test_ng2_a09_product_automation_asset_manifest_is_versioned_and_bound() -> None:
    manifest_path = Path(__file__).parents[1] / "config" / "news_grasp_automation_assets_v2.json"
    manifest = assets.load_manifest(manifest_path)
    snapshot = assets.snapshot_assets(manifest_path.parents[1], manifest)
    assert snapshot["schemaVersion"] == assets.SCHEMA
    assert snapshot["productId"] == "News-Grasp"
    assert {row["kind"] for row in snapshot["assets"]} == {"skill", "guard", "automation"}
    assert assets.verify_snapshot(manifest_path.parents[1], snapshot)


def test_ng2_a09_asset_manifest_rejects_escape_and_absolute_paths(tmp_path: Path) -> None:
    base = {
        "schemaVersion": assets.SCHEMA,
        "productId": "News-Grasp",
        "installRoot": "news-grasp-assets",
        "assets": [{"assetId": "a", "kind": "skill", "sourcePath": "../secret", "installPath": "x"}],
    }
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(base), encoding="utf-8")
    with pytest.raises(assets.AssetManifestError, match="NG_ASSET_RELATIVE_PATH_INVALID"):
        assets.load_manifest(bad)


def test_ng2_a09_installer_declares_versioned_asset_sync() -> None:
    installer = (Path(__file__).parents[1] / "scripts" / "ops" / "install-news-grasp-ops.ps1").read_text(encoding="utf-8-sig")
    assert "news_grasp_automation_assets_v2.json" in installer
    assert "news-grasp-assets" in installer
    assert "source_sha256" in installer
    assert "Assert-NewsGraspAssetInstallDestination" in installer
