from __future__ import annotations

import json
import runpy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tools.news_grasp_e2e_attempt_policy import (
    append_policy_transition,
    bind_policy_admission,
    issue_logical_attempt,
    new_policy,
    record_failure,
)


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "ops" / "news-grasp-task-launcher.pyw"


def _write_fixture_admission(path: Path) -> None:
    body = {"state": "issued", "attemptKey": "News-Grasp:2026-08-13:scheduled-equivalent-nopublish", "issueDate": "2026-08-13", "purpose": "final_confirmation_only"}
    import hashlib
    body["admissionId"] = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    path.write_text(json.dumps(body, sort_keys=True) + "\n", encoding="utf-8")


def _write_transition_receipt(policy_path: Path, admission_path: Path) -> Path:
    value = json.loads(policy_path.read_text(encoding="utf-8"))
    admission = json.loads(admission_path.read_text(encoding="utf-8"))
    transition = value["transition"]
    path = policy_path.with_name(f"e2e-transition-{transition['sequence']}.json")
    import hashlib
    import sys
    producer = Path(sys.executable).resolve()
    receipt = {"schemaVersion": "NEWS_GRASP_E2E_TRANSITION_RECEIPT_V1", "event": transition["event"], "sequence": transition["sequence"], "attemptKey": admission["attemptKey"], "issueDate": admission["issueDate"], "admissionId": admission["admissionId"], "previousStateSha256": transition["previousStateSha256"], "stateSha256": transition["stateSha256"], "producerRouteId": "news-grasp-runner", "status": "succeeded", "producerProcessId": 1, "producerExecutablePath": str(producer), "producerExecutableSha256": hashlib.sha256(producer.read_bytes()).hexdigest(), "outcomeSchemaVersion": "NEWS_GRASP_E2E_TRANSITION_OUTCOME_V1", "outcomeStatus": "admission_validated", "outcomeSha256": "0" * 64, "outcomeStatePath": "", "outcomeStateSha256": "", "outcomeExitCode": -1, "outcomeRunnerStatus": "not_started"}
    path.write_text(json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _composition_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    include_external: bool,
    external_sha256: str | None = None,
) -> tuple[dict[str, Any], Path, dict[str, object]]:
    namespace = runpy.run_path(str(LAUNCHER), run_name="_news_grasp_launcher_fixture")
    execution_repo = tmp_path / "execution"
    runtime_repo = tmp_path / "runtime"
    for repo in (execution_repo, runtime_repo):
        runner = repo / "scripts" / "ops" / "news-grasp-release-nopublish.ps1"
        wrapper = repo / "scripts" / "ops" / "run_codex_with_timeout.ps1"
        module = repo / "tools" / "news_grasp_release_nopublish.py"
        policy_consumer = repo / "tools" / "news_grasp_e2e_attempt_policy.py"
        runner.parent.mkdir(parents=True)
        module.parent.mkdir(parents=True)
        runner.write_text("Write-Output 'runner'\n", encoding="utf-8")
        wrapper.write_text("Write-Output 'wrapper'\n", encoding="utf-8")
        module.write_text("# release module fixture\n", encoding="utf-8")
        policy_consumer.write_bytes(
            (ROOT / "tools" / "news_grasp_e2e_attempt_policy.py").read_bytes()
        )

    executable = tmp_path / "powershell.exe"
    executable.write_bytes(b"MZ composition fixture\n")
    python_executable = tmp_path / "python.exe"
    python_executable.write_bytes(b"MZ python fixture\n")
    external_fixture = execution_repo / "build" / "external-authority.json"
    external_fixture.parent.mkdir(parents=True)
    external_fixture.write_text('{"status":"fresh"}\n', encoding="utf-8")
    isolation_receipt = execution_repo / "build" / "isolation-receipt.json"
    isolation_receipt.write_text('{"status":"Green"}\n', encoding="utf-8")
    binding_path = execution_repo / "build" / "high-cost-binding.json"
    binding_path.write_text('{"status":"bound"}\n', encoding="utf-8")
    parent_authority = execution_repo / "build" / "parent-authority.json"
    parent_authority.write_text('{"status":"activated"}\n', encoding="utf-8")
    reservation_receipt = execution_repo / "build" / "reservation.json"
    reservation_receipt.write_text('{"status":"reserved"}\n', encoding="utf-8")
    claim_receipt = execution_repo / "build" / "claim.json"
    state_file = execution_repo / "build" / "state.json"
    log_dir = execution_repo / "build" / "logs"
    log_dir.mkdir()
    launch_evidence = execution_repo / "build" / "launch-evidence.json"
    reflection_receipt = execution_repo / "build" / "release-reflection.json"
    reflection_receipt.write_text('{"status":"green"}\n', encoding="utf-8")
    admission_path = execution_repo / "build" / "e2e-admission.json"
    _write_fixture_admission(admission_path)
    admission = json.loads(admission_path.read_text(encoding="utf-8"))
    policy_path = execution_repo / "build" / "e2e-attempt-policy.json"
    policy = issue_logical_attempt(bind_policy_admission(new_policy(), admission_path), 1)
    policy_path.write_text(json.dumps(policy, sort_keys=True) + "\n", encoding="utf-8")
    append_policy_transition(
        policy_path,
        admission_path,
        transition_receipt_path=_write_transition_receipt(policy_path, admission_path),
    )
    arguments_path = execution_repo / "build" / "receipt.runner-arguments.json"
    arguments = [
        "-NoProfile",
        "-NonInteractive",
        "-WindowStyle",
        "Hidden",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(execution_repo / "scripts" / "ops" / "news-grasp-release-nopublish.ps1"),
        "-NoPublish",
        "-DateStampOverride",
        "2026-08-12",
        "-RepoDirOverride",
        str(execution_repo),
        "-CodexWrapperOverride",
        str(execution_repo / "scripts" / "ops" / "run_codex_with_timeout.ps1"),
        "-StateFileOverride",
        str(state_file),
        "-LogDirOverride",
        str(log_dir),
        "-PyExeOverride",
        str(python_executable),
        "-PowerShellExe",
        str(executable),
        "-HighCostBindingPath",
        str(binding_path),
        "-HighCostBindingReceiptSha256",
        "b" * 64,
        "-HighCostParentAuthorityPath",
        str(parent_authority),
        "-E2EFinalAdmissionPath",
        str(admission_path),
        "-E2EFinalRunnerArgumentsPath",
        str(arguments_path),
        "-E2EFinalReservationReceiptPath",
        str(reservation_receipt),
        "-E2EFinalClaimReceiptPath",
        str(claim_receipt),
        "-ExternalHealthAuthorityPathOverride",
        str(external_fixture),
        "-ExternalHealthAuthorityExpectedSha256",
        namespace["_file_sha256"](external_fixture),
        "-IsolationReceiptPath",
        str(isolation_receipt),
        "-LaunchEvidencePath",
        str(launch_evidence),
        "-HighCostAttemptId",
        "nopublish:2026-08-12",
        "-E2EAttemptPolicyPath",
        str(policy_path),
        "-E2ELogicalAttempt",
        "1",
    ]
    arguments_path.write_text(json.dumps(arguments) + "\n", encoding="utf-8")
    task_authority = tmp_path / "stable-task-authority.json"
    task_authority.write_text("{}\n", encoding="utf-8")
    commit = "a" * 40
    launcher_identity = {
        "authorityPath": str(task_authority),
        "authorityFileSha256": namespace["_file_sha256"](task_authority),
        "highCostBindingPath": str(binding_path),
        "highCostBindingReceiptSha256": "b" * 64,
    }
    unsigned: dict[str, object] = {
        "schemaVersion": "NEWS_GRASP_INSTALLED_NOPUBLISH_LAUNCH_AUTHORITY_V1",
        "issueDate": "2026-08-12",
        "attemptId": "nopublish:2026-08-12",
        "stableLauncherPath": str(LAUNCHER.resolve()),
        "stableLauncherSha256": namespace["_file_sha256"](LAUNCHER.resolve()),
        "stableTaskAuthorityPath": str(task_authority),
        "stableTaskAuthorityFileSha256": namespace["_file_sha256"](task_authority),
        "runnerExecutablePath": str(executable),
        "runnerExecutableSha256": namespace["_file_sha256"](executable),
        "pythonExecutableSha256": namespace["_file_sha256"](python_executable),
        "executionRepoRoot": str(execution_repo),
        "executionRepoCommit": commit,
        "runtimeRepoCommit": commit,
        "runnerArgumentsPath": str(arguments_path),
        "runnerArgumentsFileSha256": namespace["_file_sha256"](arguments_path),
        "isolationReceiptPath": str(isolation_receipt),
        "isolationReceiptSha256": namespace["_file_sha256"](isolation_receipt),
        "launchEvidencePath": str(launch_evidence),
        "e2eAttemptPolicyPath": str(policy_path),
        "e2eAttemptPolicySha256": namespace["_file_sha256"](policy_path),
        "e2eLogicalAttempt": 1,
        "e2eAdmissionPath": str(admission_path),
        "e2eAdmissionSha256": namespace["_file_sha256"](admission_path),
        "releaseReflectionReceiptPath": str(reflection_receipt),
        "releaseReflectionReceiptSha256": namespace["_file_sha256"](reflection_receipt),
        "releaseReflectionImpactClass": "source-runtime-impacting",
    }
    if include_external:
        unsigned.update(
            {
                "externalHealthAuthorityFixturePath": str(external_fixture),
                "externalHealthAuthorityFixtureSha256": (
                    external_sha256
                    if external_sha256 is not None
                    else namespace["_file_sha256"](external_fixture)
                ),
            }
        )
    authority = {**unsigned, "authoritySha256": namespace["_sha256_json"](unsigned)}
    authority_path = execution_repo / "build" / "installed-launch-authority.json"
    authority_path.write_text(json.dumps(authority) + "\n", encoding="utf-8")

    function_globals = namespace["_run_installed_nopublish_authority"].__globals__
    monkeypatch.setitem(
        function_globals,
        "resolve_bootstrap_launch_roots",
        lambda **_kwargs: {
            "configuredRuntime": runtime_repo,
            "pythonExe": python_executable,
        },
    )
    monkeypatch.setitem(function_globals, "_CANONICAL_POWERSHELL", executable)
    monkeypatch.setitem(
        function_globals,
        "_validate_active_production_generation",
        lambda **_kwargs: None,
    )
    monkeypatch.setitem(
        function_globals,
        "_validate_nopublish_isolation",
        lambda **_kwargs: {"status": "Green", "validation": {"fixture": True}},
    )
    monkeypatch.setitem(
        function_globals,
        "_run_git",
        lambda _repo, *args: commit if args[:2] == ("rev-parse", "HEAD") else "",
    )
    common_dir = tmp_path / "shared-common-dir"
    monkeypatch.setitem(function_globals, "_git_common_dir", lambda _repo: common_dir)
    monkeypatch.setattr(
        function_globals["subprocess"],
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0),
    )
    return namespace, authority_path, launcher_identity


def test_wrapper_authority_exactly_composes_with_installed_launcher(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace, authority_path, launcher_identity = _composition_fixture(
        tmp_path,
        monkeypatch,
        include_external=True,
    )
    result = namespace["_run_installed_nopublish_authority"](
        authority_path=authority_path,
        bin_dir=tmp_path,
        launcher_identity=launcher_identity,
    )
    assert result == 0


def test_installed_launcher_rejects_missing_external_authority_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace, authority_path, launcher_identity = _composition_fixture(
        tmp_path,
        monkeypatch,
        include_external=False,
    )
    with pytest.raises(
        RuntimeError,
        match="^NEWS_GRASP_INSTALLED_NOPUBLISH_EXTERNAL_AUTHORITY_INVALID$",
    ):
        namespace["_run_installed_nopublish_authority"](
            authority_path=authority_path,
            bin_dir=tmp_path,
            launcher_identity=launcher_identity,
        )


def test_installed_launcher_rejects_external_authority_hash_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace, authority_path, launcher_identity = _composition_fixture(
        tmp_path,
        monkeypatch,
        include_external=True,
        external_sha256="0" * 64,
    )
    with pytest.raises(
        RuntimeError,
        match="^NEWS_GRASP_INSTALLED_NOPUBLISH_EXTERNAL_AUTHORITY_DRIFT$",
    ):
        namespace["_run_installed_nopublish_authority"](
            authority_path=authority_path,
            bin_dir=tmp_path,
            launcher_identity=launcher_identity,
        )


def test_installed_launcher_rejects_runner_arguments_without_external_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace, authority_path, launcher_identity = _composition_fixture(
        tmp_path,
        monkeypatch,
        include_external=True,
    )
    authority = json.loads(authority_path.read_text(encoding="utf-8"))
    arguments_path = Path(authority["runnerArgumentsPath"])
    arguments = json.loads(arguments_path.read_text(encoding="utf-8"))
    hash_index = arguments.index("-ExternalHealthAuthorityExpectedSha256")
    del arguments[hash_index : hash_index + 2]
    arguments_path.write_text(json.dumps(arguments) + "\n", encoding="utf-8")
    authority["runnerArgumentsFileSha256"] = namespace["_file_sha256"](
        arguments_path
    )
    unsigned = dict(authority)
    unsigned.pop("authoritySha256")
    authority["authoritySha256"] = namespace["_sha256_json"](unsigned)
    authority_path.write_text(json.dumps(authority) + "\n", encoding="utf-8")

    with pytest.raises(
        RuntimeError,
        match="^NEWS_GRASP_INSTALLED_NOPUBLISH_ARGUMENTS_INVALID$",
    ):
        namespace["_run_installed_nopublish_authority"](
            authority_path=authority_path,
            bin_dir=tmp_path,
            launcher_identity=launcher_identity,
        )


def test_installed_launcher_rejects_unbound_global_generation_manifest_argument(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """グローバル世代manifestをrunnerへ渡す場合はauthority bindingを要求する。"""
    namespace, authority_path, launcher_identity = _composition_fixture(
        tmp_path,
        monkeypatch,
        include_external=True,
    )
    authority = json.loads(authority_path.read_text(encoding="utf-8"))
    arguments_path = Path(authority["runnerArgumentsPath"])
    arguments = json.loads(arguments_path.read_text(encoding="utf-8"))
    manifest_path = Path(authority["executionRepoRoot"]) / "build" / "global-generation.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schemaVersion": "NEWS_GRASP_GLOBAL_DEPENDENCY_GENERATION_MANIFEST_V1",
                "generationId": "global:fixture",
                "sourceCommit": "b" * 40,
                "sourceSha256": "c" * 64,
                "validForGoalId": "019fe434-c58f-7441-9a23-6f62aaf7c23b",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    arguments.extend(["-GlobalHarnessGenerationManifestPath", str(manifest_path)])
    arguments_path.write_text(json.dumps(arguments) + "\n", encoding="utf-8")
    authority["runnerArgumentsFileSha256"] = namespace["_file_sha256"](arguments_path)
    unsigned = dict(authority)
    unsigned.pop("authoritySha256")
    authority["authoritySha256"] = namespace["_sha256_json"](unsigned)
    authority_path.write_text(json.dumps(authority) + "\n", encoding="utf-8")

    with pytest.raises(
        RuntimeError,
        match="^NEWS_GRASP_GLOBAL_GENERATION_BINDING_REQUIRED$",
    ):
        namespace["_run_installed_nopublish_authority"](
            authority_path=authority_path,
            bin_dir=tmp_path,
            launcher_identity=launcher_identity,
        )


def _bind_global_generation_manifest(
    namespace: dict[str, Any], authority_path: Path
) -> None:
    authority = json.loads(authority_path.read_text(encoding="utf-8"))
    execution_repo = Path(authority["executionRepoRoot"])
    snapshot_root = execution_repo / "build" / "global-generation"
    snapshot_root.mkdir(parents=True)
    source_snapshot = snapshot_root / "high-cost-operation-budget.py"
    installed_runtime = snapshot_root / "ai-model-spawn-broker.py"
    owner_receipt = snapshot_root / "owner-authority-receipt.json"
    source_snapshot.write_bytes(b"selected-owner-source\n")
    installed_runtime.write_bytes(b"selected-installed-runtime\n")
    owner_receipt.write_text(
        json.dumps(
            {
                "schemaVersion": "GLOBAL_OWNER_AUTHORITY_RECEIPT_V1",
                "ownerRepo": "AIHarnessState",
                "ownerCommit": "b" * 40,
                "goalId": "019fe434-c58f-7441-9a23-6f62aaf7c23b",
                "status": "selected_generation_issued",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    manifest_path = snapshot_root / "manifest.json"
    manifest = {
        "schemaVersion": "NEWS_GRASP_GLOBAL_DEPENDENCY_GENERATION_MANIFEST_V1",
        "generationId": "global:fixture",
        "ownerRepo": "AIHarnessState",
        "ownerCommit": "b" * 40,
                "sourceSnapshotPath": str(source_snapshot),
                "sourceSnapshotSha256": namespace["_file_sha256"](source_snapshot),
                "installedRuntimePath": str(installed_runtime),
                "installedRuntimeSha256": namespace["_file_sha256"](installed_runtime),
                "ownerAuthorityReceiptPath": str(owner_receipt),
                "ownerAuthorityReceiptSha256": namespace["_file_sha256"](owner_receipt),
        "ownerAuthorityReceiptPath": str(owner_receipt),
        "ownerAuthorityReceiptSha256": namespace["_file_sha256"](owner_receipt),
        "validForGoalId": "019fe434-c58f-7441-9a23-6f62aaf7c23b",
    }
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")
    arguments_path = Path(authority["runnerArgumentsPath"])
    arguments = json.loads(arguments_path.read_text(encoding="utf-8"))
    arguments.extend(["-GlobalHarnessGenerationManifestPath", str(manifest_path)])
    arguments_path.write_text(json.dumps(arguments) + "\n", encoding="utf-8")
    authority.update(
        {
            "globalGenerationManifestPath": str(manifest_path),
            "globalGenerationManifestSha256": namespace["_file_sha256"](manifest_path),
            "globalGenerationId": manifest["generationId"],
            "globalGenerationGoalId": manifest["validForGoalId"],
            "runnerArgumentsFileSha256": namespace["_file_sha256"](arguments_path),
        }
    )
    unsigned = dict(authority)
    unsigned.pop("authoritySha256")
    authority["authoritySha256"] = namespace["_sha256_json"](unsigned)
    authority_path.write_text(json.dumps(authority) + "\n", encoding="utf-8")


def test_installed_launcher_accepts_bound_global_generation_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace, authority_path, launcher_identity = _composition_fixture(
        tmp_path,
        monkeypatch,
        include_external=True,
    )
    _bind_global_generation_manifest(namespace, authority_path)
    result = namespace["_run_installed_nopublish_authority"](
        authority_path=authority_path,
        bin_dir=tmp_path,
        launcher_identity=launcher_identity,
    )
    assert result == 0


def test_installed_launcher_rejects_global_generation_manifest_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace, authority_path, launcher_identity = _composition_fixture(
        tmp_path,
        monkeypatch,
        include_external=True,
    )
    _bind_global_generation_manifest(namespace, authority_path)
    authority = json.loads(authority_path.read_text(encoding="utf-8"))
    manifest_path = Path(authority["globalGenerationManifestPath"])
    manifest_path.write_text(manifest_path.read_text(encoding="utf-8") + "drift\n", encoding="utf-8")
    with pytest.raises(
        RuntimeError,
        match="^NEWS_GRASP_GLOBAL_GENERATION_MANIFEST_DRIFT$",
    ):
        namespace["_run_installed_nopublish_authority"](
            authority_path=authority_path,
            bin_dir=tmp_path,
            launcher_identity=launcher_identity,
        )


def test_installed_launcher_rejects_third_logical_e2e_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace, authority_path, launcher_identity = _composition_fixture(
        tmp_path,
        monkeypatch,
        include_external=True,
    )
    _bind_global_generation_manifest(namespace, authority_path)
    authority = json.loads(authority_path.read_text(encoding="utf-8"))
    execution_repo = Path(authority["executionRepoRoot"])
    policy_path = execution_repo / "build" / "e2e-attempt-policy.json"
    policy = new_policy()
    policy["logicalAttemptIssued"] = 3
    admission_path = execution_repo / "build" / "e2e-admission.json"
    _write_fixture_admission(admission_path)
    policy_path.write_text(json.dumps(policy, sort_keys=True) + "\n", encoding="utf-8")
    arguments_path = Path(authority["runnerArgumentsPath"])
    arguments = json.loads(arguments_path.read_text(encoding="utf-8"))
    arguments[arguments.index("-E2EAttemptPolicyPath") + 1] = str(policy_path)
    arguments[arguments.index("-E2ELogicalAttempt") + 1] = "3"
    arguments[arguments.index("-E2EFinalAdmissionPath") + 1] = str(admission_path)
    arguments_path.write_text(json.dumps(arguments) + "\n", encoding="utf-8")
    authority.update(
        {
            "e2eAttemptPolicyPath": str(policy_path),
            "e2eAttemptPolicySha256": namespace["_file_sha256"](policy_path),
            "e2eLogicalAttempt": 3,
            "e2eAdmissionPath": str(admission_path),
            "e2eAdmissionSha256": namespace["_file_sha256"](admission_path),
            "runnerArgumentsFileSha256": namespace["_file_sha256"](arguments_path),
        }
    )
    unsigned = dict(authority)
    unsigned.pop("authoritySha256")
    authority["authoritySha256"] = namespace["_sha256_json"](unsigned)
    authority_path.write_text(json.dumps(authority) + "\n", encoding="utf-8")

    with pytest.raises(
        RuntimeError,
        match="^NEWS_GRASP_E2E_ATTEMPT_POLICY_INVALID$",
    ):
        namespace["_run_installed_nopublish_authority"](
            authority_path=authority_path,
            bin_dir=tmp_path,
            launcher_identity=launcher_identity,
        )


def test_installed_launcher_accepts_first_logical_e2e_attempt_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace, authority_path, launcher_identity = _composition_fixture(
        tmp_path,
        monkeypatch,
        include_external=True,
    )
    _bind_global_generation_manifest(namespace, authority_path)
    assert namespace["_run_installed_nopublish_authority"](
        authority_path=authority_path,
        bin_dir=tmp_path,
        launcher_identity=launcher_identity,
    ) == 0


def test_installed_launcher_allows_same_attempt_resume_after_failure_local_repair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace, authority_path, launcher_identity = _composition_fixture(
        tmp_path,
        monkeypatch,
        include_external=True,
    )
    _bind_global_generation_manifest(namespace, authority_path)
    authority = json.loads(authority_path.read_text(encoding="utf-8"))
    execution_repo = Path(authority["executionRepoRoot"])
    policy_path = execution_repo / "build" / "e2e-attempt-policy.json"
    admission_path = execution_repo / "build" / "e2e-admission.json"
    initial = json.loads(policy_path.read_text(encoding="utf-8"))
    policy = record_failure(initial, 1, "failure_local")
    policy_path.write_text(json.dumps(policy, sort_keys=True) + "\n", encoding="utf-8")
    append_policy_transition(
        policy_path,
        admission_path,
        transition_receipt_path=_write_transition_receipt(policy_path, admission_path),
    )
    arguments_path = Path(authority["runnerArgumentsPath"])
    authority.update(
        {
            "e2eAttemptPolicyPath": str(policy_path),
            "e2eAttemptPolicySha256": namespace["_file_sha256"](policy_path),
            "e2eLogicalAttempt": 1,
            "e2eAdmissionPath": str(admission_path),
            "e2eAdmissionSha256": namespace["_file_sha256"](admission_path),
            "runnerArgumentsFileSha256": namespace["_file_sha256"](arguments_path),
        }
    )
    unsigned = dict(authority)
    unsigned.pop("authoritySha256")
    authority["authoritySha256"] = namespace["_sha256_json"](unsigned)
    authority_path.write_text(json.dumps(authority) + "\n", encoding="utf-8")
    assert namespace["_run_installed_nopublish_authority"](
        authority_path=authority_path,
        bin_dir=tmp_path,
        launcher_identity=launcher_identity,
    ) == 0
