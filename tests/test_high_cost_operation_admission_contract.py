from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "ops" / "news-grasp-runner.ps1"
WRAPPER = ROOT / "scripts" / "ops" / "invoke-scheduled-equivalent-nopublish.ps1"
MODEL_WRAPPER = ROOT / "scripts" / "ops" / "run_codex_with_timeout.ps1"
REPAIR_SKILL = (
    Path.home() / ".codex" / "skills" / "news-grasp-repair-method" / "SKILL.md"
)


def test_nopublish_wrapper_authorizes_before_runner_launch() -> None:
    text = WRAPPER.read_text(encoding="utf-8-sig")
    authorize = text.index("& $PythonExe $highCostBudgetToolPath 'admit'")
    consume = text.index("& $PythonExe $e2eAdmissionBridgePath 'consume'")
    runner_launch = text.index("& $PowerShellExe @runnerArguments")
    assert authorize < consume < runner_launch
    assert "'full_e2e'" in text
    assert "bin\\ai-model-spawn-broker.py" in text
    assert "--ledger" not in text
    assert "resume_model" not in text
    assert "-ResumeFromStage" not in text
    assert "HIGH_COST_TRUSTED_EVIDENCE_REQUIRED" in text
    assert "-HighCostWorkspaceRoot" in text
    assert "$attemptId = \"nopublish:$DateStamp\"" in text


def test_nopublish_wrapper_persists_and_propagates_single_reservation_receipt() -> None:
    text = WRAPPER.read_text(encoding="utf-8-sig")
    assert "-HighCostAdmissionPath" in text
    assert "high_cost_admission_receipt.py" in text
    assert "WriteAllText($highCostAdmissionFullPath" in text
    assert text.index("WriteAllText($highCostAdmissionFullPath") < text.index(
        "& $PythonExe $e2eAdmissionBridgePath 'consume'"
    )


def test_runner_consumes_shared_admission_before_any_reporter_fanout() -> None:
    text = RUNNER.read_text(encoding="utf-8-sig")
    consume = text.index("& $PyExe $modelSpawnBroker 'admit'")
    invoked = text.index("runner-invoked pid=")
    reporter = text.index("reporter job START")
    assert consume < invoked < reporter
    assert "HIGH_COST_OPERATION_ADMISSION_REQUIRED" in text
    assert "model_spawn_broker.py" in text
    gate = text.split("function Assert-HighCostOperationAdmission", 1)[1].split("# ===== sentinel", 1)[0]
    assert "'admit'" in gate or '"admit"' in gate
    assert "blocked_high_cost" not in gate
    assert "if ($HighCostAdmissionPath)" in gate
    assert "high_cost_admission_receipt.py" in gate
    assert "'scheduled_production'" in gate
    assert "'scheduled_recovery'" in gate
    assert "'full_e2e'" in gate
    assert '$expectedAttemptId = "nopublish:$DateStamp"' in gate
    assert "'--expected-attempt-id' $expectedAttemptId" in gate


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
    assert wrapper_gate.count("'admit'") == 1
    nopublish_gate = runner_gate.split("if ($NoPublish)", 1)[1].split(
        "\n    if ($HighCostAdmissionPath)", 1
    )[0]
    assert "if (-not $HighCostAdmissionPath)" in nopublish_gate
    assert "high_cost_admission_receipt.py" in nopublish_gate
    assert "return" in nopublish_gate
    assert "'admit'" not in nopublish_gate


def test_every_model_process_reserves_call_budget_immediately_before_start() -> None:
    text = MODEL_WRAPPER.read_text(encoding="utf-8-sig")
    reserve = text.rindex("\nAssert-CanonicalModelBroker")
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
    assert "'--call-id', $HighCostCallId" in text
    assert "'--operation-admission', $HighCostAdmissionPath" in text
    assert "--reservation" not in text


def test_runner_passes_shared_admission_to_sequential_and_parallel_calls() -> None:
    text = RUNNER.read_text(encoding="utf-8-sig")
    helper = text.split("function Invoke-CodexWrapper", 1)[1].split("function ", 1)[0]
    reporter = text.split("function Invoke-ReporterWave", 1)[1].split("function ", 1)[0]
    for source in (helper, reporter):
        assert "HighCostWorkspaceRoot" in source
        assert "HighCostCallId" in source
        assert "HighCostAdmissionPath" in source


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
