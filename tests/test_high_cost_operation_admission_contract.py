import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "ops" / "news-grasp-runner.ps1"
WRAPPER = ROOT / "scripts" / "ops" / "invoke-scheduled-equivalent-nopublish.ps1"
MODEL_WRAPPER = ROOT / "scripts" / "ops" / "run_codex_with_timeout.ps1"
REPAIR_SKILL = (
    Path.home() / ".codex" / "skills" / "news-grasp-repair-method" / "SKILL.md"
)


def test_nopublish_wrapper_authorizes_and_activates_parent_before_runner_launch() -> None:
    text = WRAPPER.read_text(encoding="utf-8-sig")
    validate_issued = text.index("'validate-issued'")
    authorize = text.index("& $pythonCanonicalPath -I $highCostOperationBudgetPath 'authorize-causal-replacement'")
    activate = text.index("& $pythonCanonicalPath -I $highCostOperationBudgetPath 'activate'")
    validate_parent = text.index("'validate-activated'")
    consume = text.index("& $pythonCanonicalPath -I $e2eAdmissionBridgePath 'consume'")
    runner_launch = text.index("& $installedTaskPythonPath @installedLauncherArguments")
    assert validate_issued < authorize < activate < validate_parent < consume < runner_launch
    assert "'full_e2e'" in text
    assert "tools\\harness\\high_cost_operation_budget.py" in text
    assert "--ledger" not in text
    assert "resume_model" not in text
    assert "-ResumeFromStage" not in text
    assert "HIGH_COST_CANONICAL_FILE_INVALID" in text
    assert "-HighCostWorkspaceRoot" in text
    assert "$attemptId = \"nopublish:$DateStamp\"" in text
    assert "Get-Content -LiteralPath $E2EAdmissionPath" not in text
    assert "'--reservation-output' $reservationReceiptPath" in text
    assert "NEWS_GRASP_INSTALLED_NOPUBLISH_LAUNCH_AUTHORITY_V1" in text
    assert "& $PowerShellExe @runnerArguments" not in text


def test_nopublish_wrapper_propagates_parent_authority_not_shared_child_receipt() -> None:
    text = WRAPPER.read_text(encoding="utf-8-sig")
    assert "-HighCostParentAuthorityPath" in text
    assert "-HighCostAdmissionPath" not in text
    assert "high_cost_admission_receipt.py" not in text
    assert "'--output' $parentAuthorityFullPath" in text
    assert "'--admission' $parentAuthorityFullPath" in text
    assert "'--output', $parentAuthorityFullPath" not in text
    assert "'--admission', $parentAuthorityFullPath" not in text


def test_nopublish_wrapper_threads_optional_user_supersession_approval_fail_closed() -> None:
    text = WRAPPER.read_text(encoding="utf-8-sig")
    assert "[string] $SupersessionApprovalPath = ''" in text
    assert "pre-admission supersession approval" in text
    assert "$supersessionArguments = @('--supersession-approval', $SupersessionApprovalPath)" in text
    authorize = text.index("'authorize-causal-replacement'")
    approval = text.index("@supersessionArguments", authorize)
    runner = text.index("& $installedTaskPythonPath @installedLauncherArguments")
    assert authorize < approval < runner
    assert "SupersessionApprovalPath" not in text[text.index("$runnerArguments = @("):runner]


def test_supersession_approval_is_bound_to_issued_attempt_and_issue_date() -> None:
    text = WRAPPER.read_text(encoding="utf-8-sig")
    guard = text.split("if ($SupersessionApprovalPath)", 1)[1].split(
        "$statePath = Get-CanonicalFuturePath", 1
    )[0]
    assert "$supersessionApproval = Get-Content -LiteralPath $SupersessionApprovalPath" in guard
    assert '$issuedAttemptKey = "News-Grasp:${DateStamp}:scheduled-equivalent-nopublish"' in guard
    assert "$supersessionApproval.canonicalAttemptKey" in guard
    assert "$supersessionApproval.issueDate" in guard
    assert "HIGH_COST_SUPERSESSION_BINDING_INVALID" in guard
    assert "System.StringComparison]::Ordinal" in guard


def test_runner_requires_activated_parent_before_any_reporter_fanout() -> None:
    text = RUNNER.read_text(encoding="utf-8-sig")
    consume = text.index("HIGH_COST_PARENT_AUTHORITY_RECEIPT_REQUIRED")
    invoked = text.index("runner-invoked pid=")
    reporter = text.index("reporter job START")
    assert consume < invoked < reporter
    assert "HIGH_COST_OPERATION_ADMISSION_REQUIRED" in text
    assert "model_spawn_broker.py" in text
    gate = text.split("function Assert-HighCostOperationAdmission", 1)[1].split("# ===== sentinel", 1)[0]
    assert "'admit'" in gate or '"admit"' in gate
    assert "blocked_high_cost" not in gate
    assert "if (-not $incomingHighCostParentAuthorityPath)" in gate
    assert "HIGH_COST_PARENT_AUTHORITY_RECEIPT_REQUIRED" in gate
    assert "HIGH_COST_OPERATION_ADMISSION_V1" in gate
    assert "activated" in gate
    assert "'scheduled_production'" in gate
    assert "'scheduled_recovery'" in gate
    assert "'full_e2e'" in gate
    assert '$expectedFullE2EAttemptId = "nopublish:$DateStamp"' in gate
    assert '$script:HighCostAttemptId = $expectedFullE2EAttemptId' in gate
    assert "$script:HighCostAdmissionPath = ''" in gate
    assert "E2EFinalReservationReceiptPath" in gate
    assert "E2EFinalClaimReceiptPath" in gate
    assert "'--reservation-receipt' $finalReservationReceipt" in gate
    assert "'--claim-output' $finalClaimReceipt" in gate
    assert gate.index("'claim-runner'") < text.index("runner-invoked pid=")


def test_runner_claim_failure_marker_is_checked_before_parent_validation() -> None:
    text = RUNNER.read_text(encoding="utf-8-sig")
    gate = text.split("function Assert-HighCostOperationAdmission", 1)[1].split(
        "# ===== sentinel", 1
    )[0]
    status = gate.index("'claim-failure-status'")
    parent_validation = gate.index("'validate-activated'")
    assert status < parent_validation
    assert "'record-claim-failure'" in text
    assert "HIGH_COST_FINAL_RUNNER_CLAIM_TERMINAL" in gate


def test_runner_claim_failure_recording_unavailable_is_typed_terminal_exit() -> None:
    text = RUNNER.read_text(encoding="utf-8-sig")
    helper = text.split("function Record-HighCostClaimFailure", 1)[1].split(
        "function Assert-HighCostOperationAdmission", 1
    )[0]
    assert "return $false" in helper
    assert "return $true" in helper
    gate = text.split("function Assert-HighCostOperationAdmission", 1)[1].split(
        "# ===== sentinel", 1
    )[0]
    assert gate.count("HIGH_COST_CLAIM_FAILURE_RECORD_UNAVAILABLE") == 6
    for failure_code in (
        "HIGH_COST_PARENT_AUTHORITY_RECEIPT_INVALID",
        "HIGH_COST_FINAL_RUNNER_CLAIM_REJECTED",
    ):
        assert f"-FailureCode '{failure_code}'" in gate
    assert "Set-RunnerState -Status 'operation_rejected_high_cost_admission'" in gate
    assert "-Message 'HIGH_COST_CLAIM_FAILURE_RECORD_UNAVAILABLE'" in gate
    assert "-ExitCode 76" in gate


def test_normal_daily_runner_never_reserves_final_e2e_budget() -> None:
    text = RUNNER.read_text(encoding="utf-8-sig")
    gate = text.split("function Assert-HighCostOperationAdmission", 1)[1].split(
        "# ===== sentinel", 1
    )[0]
    assert "$operationKind = 'scheduled_production'" in gate
    assert "if ($NoPublish)" in gate
    assert "$operationKind = 'full_e2e'" in gate
    assert gate.index("$operationKind = 'scheduled_production'") < gate.index(
        "if ($NoPublish)"
    )


def test_wrapper_and_runner_do_not_reserve_two_full_e2e_attempts() -> None:
    wrapper = WRAPPER.read_text(encoding="utf-8-sig")
    runner = RUNNER.read_text(encoding="utf-8-sig")
    wrapper_gate = wrapper.split("$startedAt = Get-Date", 1)[0]
    runner_gate = runner.split("function Assert-HighCostOperationAdmission", 1)[1].split(
        "# ===== sentinel", 1
    )[0]
    assert wrapper_gate.count("'authorize-causal-replacement'") == 1
    assert wrapper_gate.count("'activate'") == 1
    assert "'admit'" not in wrapper_gate
    nopublish_gate = runner_gate.split("if ($NoPublish)", 1)[1].split(
        "\n    if ($HighCostAdmissionPath)", 1
    )[0]
    assert "if (-not $incomingHighCostParentAuthorityPath)" in nopublish_gate
    assert "incomingHighCostParentAuthorityPath" in nopublish_gate
    assert "return" in nopublish_gate
    assert "'admit'" not in nopublish_gate


def test_every_full_e2e_model_process_issues_exact_child_immediately_before_start() -> None:
    text = MODEL_WRAPPER.read_text(encoding="utf-8-sig")
    reserve = text.index("'--parent-operation-authority', $HighCostParentAuthorityPath")
    launch = text.index("$ownedLaunch = [NewsGraspOwnedJob]::CreateSuspendedAssignedProcess(", reserve)
    assert reserve < launch
    native = text.split("public static OwnedLaunch CreateSuspendedAssignedProcess", 1)[1].split(
        "public static void CloseOwnedJob", 1
    )[0]
    assert native.index("CreateProcess(") < native.index("AssignProcessToJobObject(") < native.index("ResumeThread(")
    body = text.split("function Assert-CanonicalModelBroker", 1)[1].split("function ", 1)[0]
    assert "bin\\ai-model-spawn-broker.py" in body
    assert "expectedInstalledBroker" in body
    assert "OrdinalIgnoreCase" in body
    assert "HighCostWorkspaceRoot" in text
    assert "HighCostCallId" in text
    assert "HighCostAttemptId" in text
    assert "HighCostParentAuthorityPath" in text
    assert "HIGH_COST_OPERATION_ADMISSION_V3" in text
    assert "WriteAllText($HighCostCallReceiptPath" in text
    assert "'--call-id', $HighCostCallId" in text
    assert "'--operation-admission', $operationAdmissionPath" in text
    assert "--e2e-final-reservation-receipt" in text
    assert "--e2e-final-claim-receipt" in text
    assert "--e2e-final-claim-witness" in text
    assert "HIGH_COST_FULL_E2E_SHARED_ADMISSION_FORBIDDEN" in text
    assert "HIGH_COST_SCHEDULED_PARENT_AUTHORITY_FORBIDDEN" in text


def test_runner_passes_parent_authority_to_sequential_and_parallel_calls() -> None:
    text = RUNNER.read_text(encoding="utf-8-sig")
    helper = text.split("function Invoke-CodexWrapper", 1)[1].split("function ", 1)[0]
    reporter = text.split("function Invoke-ReporterWave", 1)[1].split("function ", 1)[0]
    for source in (helper, reporter):
        assert "HighCostWorkspaceRoot" in source
        assert "HighCostCallId" in source
        assert "HighCostParentAuthorityPath" in source
        assert "HighCostAttemptId" in source


def test_scheduled_model_calls_keep_existing_shared_admission_route() -> None:
    text = MODEL_WRAPPER.read_text(encoding="utf-8-sig")
    assert "$operationAdmissionPath = $HighCostAdmissionPath" in text
    assert "if ($HighCostParentAuthorityPath)" in text
    assert "HIGH_COST_SCHEDULED_OPERATION_ADMISSION_V1" in RUNNER.read_text(
        encoding="utf-8-sig"
    )


def test_runner_rejects_cross_mode_authority_and_nopublish_resume_inputs() -> None:
    text = RUNNER.read_text(encoding="utf-8-sig")
    gate = text.split("function Assert-HighCostOperationAdmission", 1)[1].split(
        "# ===== sentinel", 1
    )[0]
    nopublish_gate = gate.split("if ($NoPublish)", 1)[1]
    assert "if ($HighCostAdmissionPath)" in nopublish_gate
    assert "HIGH_COST_NOPUBLISH_SHARED_ADMISSION_FORBIDDEN" in nopublish_gate
    assert "if ($ResumeFromStage)" in nopublish_gate
    assert "HIGH_COST_NOPUBLISH_RESUME_FORBIDDEN" in nopublish_gate
    assert "HIGH_COST_SCHEDULED_FINAL_ADMISSION_FORBIDDEN" in gate


def test_resume_and_zero_external_modes_do_not_require_full_e2e_attempt() -> None:
    text = RUNNER.read_text(encoding="utf-8-sig")
    assert "$SmokeTest" in text
    assert "$PreflightOnly" in text
    assert "$RecoverOnly" in text
    assert "$ResumeFromPostDailyQuality" in text
    assert "$ResumeAfterDeepDive" in text
    assert "$operationKind = 'scheduled_recovery'" in text


def test_repair_skill_reuses_scheduled_identity_without_e2e_or_budget_reset() -> None:
    text = REPAIR_SKILL.read_text(encoding="utf-8-sig")
    for phrase in (
        "scheduled_production",
        "scheduled_recovery",
        "final E2Eを起動しない",
        "同じissue date identityの残予算",
        "新しい9 callを発行しない",
    ):
        assert phrase in text


def test_nopublish_wrapper_uses_deterministic_parent_and_final_argument_paths() -> None:
    text = WRAPPER.read_text(encoding="utf-8-sig")
    assert '$receiptFullPath.high-cost-parent-authority.json' in text
    assert "NewGuid" not in text
    for flag in (
        "-HighCostParentAuthorityPath",
        "-E2EFinalAdmissionPath",
        "-E2EFinalRunnerArgumentsPath",
        "-E2EFinalReservationReceiptPath",
        "-E2EFinalClaimReceiptPath",
    ):
        assert flag in text
    assert '$receiptFullPath.e2e-final-reservation.json' in text
    assert '$receiptFullPath.e2e-final-claim.json' in text
    assert "ConvertTo-Json -Compress" in text
    assert "[System.IO.FileMode]::CreateNew" in text
    validate = text.index("'validate-activated'")
    consume = text.index("'consume'")
    runner = text.index("& $installedTaskPythonPath @installedLauncherArguments")
    assert validate < consume < runner


def test_p1_p2_p3_authority_contract_is_wired_at_the_bridge_boundary() -> None:
    bridge = (ROOT / "tools" / "e2e_final_admission_bridge.py").read_text(
        encoding="utf-8"
    )
    validate_signature = bridge.split("def validate_issued_admission(", 1)[1].split(
        ") ->", 1
    )[0]
    assert "reservation_output" in validate_signature
    assert "claim_output" in validate_signature
    assert "E2E_PARENT_AUTHORITY_DRIFT" in bridge
    assert "E2E_RESERVATION_RECEIPT_PATH_DRIFT" in bridge
    assert "E2E_CLAIM_RECEIPT_PATH_DRIFT" in bridge
    assert "validate-runner-claim" in bridge
    assert "ownerProcessIdentity" in bridge
    assert "creationFileTimeUtc" in bridge
    assert "E2E_RUNNER_PROCESS_IDENTITY_UNAVAILABLE" in bridge
    assert "SHGetKnownFolderPath" in bridge
    assert "LOCALAPPDATA" not in bridge.split("def default_attempt_ledger_path", 1)[1].split(
        "def _read_runner_arguments", 1
    )[0]


def test_p4_preflights_all_outputs_before_any_directory_creation() -> None:
    text = WRAPPER.read_text(encoding="utf-8-sig")
    first_directory_creation = text.index("New-Item -ItemType Directory")
    first_future_validation = text.index("Get-CanonicalFuturePath")
    assert first_future_validation < first_directory_creation
    for label in ("state", "log", "final receipt", "parent authority", "runner arguments", "reservation receipt", "claim receipt"):
        assert label in text
    assert "HIGH_COST_CANONICAL_FUTURE_PATH_INVALID" in text
    assert "HIGH_COST_CANONICAL_FUTURE_OUTPUT_INVALID" in text


def test_official_wrapper_rejects_junction_outputs_before_outside_write(tmp_path: Path) -> None:
    """公式wrapperのfuture output preflightがjunction外部を書かないことを実測する。"""
    repo = tmp_path / "repo"
    outside = tmp_path / "outside"
    repo.mkdir()
    outside.mkdir()
    (repo / ".git").mkdir()
    for relative in (
        "scripts/ops/news-grasp-runner.ps1",
        "scripts/ops/run_codex_with_timeout.ps1",
        "tools/e2e_final_admission_bridge.py",
    ):
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")
    (repo / "admission.json").write_text("{}\n", encoding="utf-8")
    marker = outside / "marker.txt"
    marker.write_text("outside-stable\n", encoding="utf-8")
    junction = repo / "managed-output"
    junction_literal = str(junction).replace("'", "''")
    outside_literal = str(outside).replace("'", "''")
    junction_command = (
        "$ErrorActionPreference='Stop'; "
        f"New-Item -ItemType Junction -Path '{junction_literal}' "
        f"-Target '{outside_literal}' | Out-Null"
    )
    junction_result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            junction_command,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        env={
            **os.environ,
            "PSModulePath": r"C:\Windows\System32\WindowsPowerShell\v1.0\Modules",
        },
    )
    if junction_result.returncode != 0:
        raise AssertionError(
            f"junction fixture setup failed: {junction_result.stderr}"
        )

    workspace = tmp_path / "workspace"
    evidence = workspace / "tools" / "harness" / "high_cost_operation_budget.py"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text("fixture\n", encoding="utf-8")
    profile_python = workspace / "python.exe"
    profile_python.write_bytes(b"MZ synthetic authority executable\n")
    args = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(WRAPPER),
        "-RepoRoot",
        str(repo),
        "-DateStamp",
        "2026-08-10",
        "-StateFile",
        str(junction / "state.json"),
        "-LogDir",
        str(junction / "logs"),
        "-ReceiptPath",
        str(junction / "receipt.json"),
        "-PythonExe",
        str(profile_python),
        "-WorkspaceRoot",
        str(workspace),
        "-BudgetPath",
        str(evidence),
        "-EfficiencyDesignPath",
        str(evidence),
        "-AdversarialReviewPath",
        str(evidence),
        "-RouteManifestPath",
        str(evidence),
        "-StaticReceiptPath",
        str(evidence),
            "-SimulationReceiptPath",
            str(evidence),
            "-CausalReplacementProofPath",
            str(evidence),
        "-E2EAdmissionPath",
        str(repo / "admission.json"),
        "-PowerShellExe",
        "powershell.exe",
    ]
    before = sorted(path.relative_to(outside).as_posix() for path in outside.rglob("*"))
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        env={
            **os.environ,
            "PSModulePath": r"C:\Windows\System32\WindowsPowerShell\v1.0\Modules",
        },
    )
    after = sorted(path.relative_to(outside).as_posix() for path in outside.rglob("*"))
    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert (
        "HIGH_COST_CANONICAL_FUTURE_PATH_INVALID" in output
        or "HIGH_COST_EXECUTABLE_IDENTITY_INVALID" in output
    )
    assert before == after
    assert marker.read_text(encoding="utf-8") == "outside-stable\n"


def test_p5_claim_witness_reaches_every_model_broker_packet() -> None:
    runner = RUNNER.read_text(encoding="utf-8-sig")
    model = MODEL_WRAPPER.read_text(encoding="utf-8-sig")
    bridge = (ROOT / "tools" / "e2e_final_admission_bridge.py").read_text(
        encoding="utf-8"
    )
    assert "write-runner-claim-witness" in runner
    assert "HighCostClaimWitness" in runner
    assert "HighCostClaimWitness" in model
    for flag in (
        "--e2e-final-admission",
        "--e2e-final-runner-arguments-file",
        "--e2e-final-reservation-receipt",
        "--e2e-final-claim-receipt",
        "--e2e-final-claim-witness",
    ):
        assert flag in model
    assert model.index("validate-runner-claim-witness") < model.index("'admit'")
    assert model.count("--e2e-final-claim-witness") >= 2
    assert "--e2e-final-claim-witness" not in bridge


def test_reporter_timeout_never_uses_raw_process_termination() -> None:
    """reporter timeoutもowner brokerへ委譲し、PID単独killを残さない。"""

    runner = RUNNER.read_text(encoding="utf-8-sig")
    assert "Stop-Process" not in runner
    assert "wrapper_pid" not in runner


def _bridge_fixture_support():
    support_path = ROOT / "tests" / "test_e2e_final_admission_bridge.py"
    spec = importlib.util.spec_from_file_location("_ng_bridge_fixture_support", support_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _claim_fixture_admission(bridge_module, admission: Path, ledger: Path, *, reservation: Path | None = None):
    value = json.loads(admission.read_text(encoding="utf-8"))
    original_require = bridge_module._require_trusted_workspace_root
    bridge_module._require_trusted_workspace_root = lambda _repo: None
    try:
        return bridge_module.claim_runner(
            admission_path=admission,
            ledger_path=ledger,
            runner_arguments=list(value["runnerArguments"]),
            parent_authority_path=Path(value["expectedParentAuthorityPath"]),
            runner_arguments_path=Path(value["expectedRunnerArgumentsPath"]),
            reservation_receipt=reservation or Path(value["expectedReservationReceiptPath"]),
            claim_output=Path(value["expectedClaimReceiptPath"]),
            actual_runner_executable_path=Path(value["runnerExecutablePath"]),
            actual_authority_python_executable_path=Path(value["authorityPythonExecutablePath"]),
            current_runner_pid=os.getpid(),
            claim_nonce="a" * 64,
        )
    finally:
        bridge_module._require_trusted_workspace_root = original_require


def test_immutable_admission_bytes_and_typed_reservation_rejections(tmp_path: Path) -> None:
    """production bridgeのreservation/claim境界をtmp rootで接続する。"""
    support = _bridge_fixture_support()
    bridge_module = support.bridge_module
    original_executor = bridge_module.execute_red_suite
    bridge_module.execute_red_suite = lambda *, matrix_path, root: support._synthetic_execution_receipt(root)
    try:
        root = tmp_path / "ng-final-admission-contract"
        admission, ledger = support._issue(root / "valid")
        reserved = support.consume_admission(admission_path=admission, ledger_path=ledger)
        issued_bytes = admission.read_bytes()
        claimed = _claim_fixture_admission(bridge_module, admission, ledger)
        assert reserved["state"] == "runner_reserved"
        assert claimed["state"] == "runner_claimed"
        assert Path(json.loads(admission.read_text(encoding="utf-8"))["expectedClaimReceiptPath"]).is_file()
        assert admission.read_bytes() == issued_bytes
        with pytest.raises(bridge_module.E2EFinalAdmissionError):
            _claim_fixture_admission(bridge_module, admission, ledger)
        assert admission.read_bytes() == issued_bytes

        cases = ("missing", "forged", "stale", "divergent")
        for case in cases:
            case_root = root / case
            case_admission, case_ledger = support._issue(case_root)
            support.consume_admission(admission_path=case_admission, ledger_path=case_ledger)
            case_value = json.loads(case_admission.read_text(encoding="utf-8"))
            expected_reservation = Path(case_value["expectedReservationReceiptPath"])
            if case == "missing":
                expected_reservation.unlink()
                reservation_arg = None
            elif case == "forged":
                expected_reservation.write_text("{}\n", encoding="utf-8")
                reservation_arg = None
            elif case == "stale":
                stale_admission, stale_ledger = support._issue(root / "stale-source")
                support.consume_admission(admission_path=stale_admission, ledger_path=stale_ledger)
                stale_value = json.loads(stale_admission.read_text(encoding="utf-8"))
                expected_reservation.write_bytes(Path(stale_value["expectedReservationReceiptPath"]).read_bytes())
                reservation_arg = None
            else:
                divergent = case_root / "caller.e2e-final-reservation.json"
                shutil.copyfile(expected_reservation, divergent)
                reservation_arg = divergent
            with pytest.raises(bridge_module.E2EFinalAdmissionError):
                _claim_fixture_admission(bridge_module, case_admission, case_ledger, reservation=reservation_arg)
            assert not Path(case_value["expectedClaimReceiptPath"]).exists(), case
    finally:
        bridge_module.execute_red_suite = original_executor
