from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "ops" / "news-grasp-runner.ps1"
WRAPPER = ROOT / "scripts" / "ops" / "invoke-scheduled-equivalent-nopublish.ps1"
MODEL_WRAPPER = ROOT / "scripts" / "ops" / "run_codex_with_timeout.ps1"


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


def test_every_model_process_reserves_call_budget_immediately_before_start() -> None:
    text = MODEL_WRAPPER.read_text(encoding="utf-8-sig")
    reserve = text.rindex("\nAssert-CanonicalModelBroker")
    launch = text.index("$proc = Start-Process -FilePath $filePath")
    assert reserve < launch
    body = text.split("function Assert-CanonicalModelBroker", 1)[1].split("function ", 1)[0]
    assert "bin\\ai-model-spawn-broker.py" in body
    assert "expectedInstalledBroker" in body
    assert "OrdinalIgnoreCase" in body
    assert "HighCostWorkspaceRoot" in text
    assert "HighCostCallId" in text
    assert "'--call-id', $HighCostCallId" in text
    assert "--reservation" not in text


def test_runner_passes_shared_admission_to_sequential_and_parallel_calls() -> None:
    text = RUNNER.read_text(encoding="utf-8-sig")
    helper = text.split("function Invoke-CodexWrapper", 1)[1].split("function ", 1)[0]
    reporter = text.split("function Invoke-ReporterWave", 1)[1].split("function ", 1)[0]
    for source in (helper, reporter):
        assert "HighCostWorkspaceRoot" in source
        assert "HighCostCallId" in source


def test_resume_and_zero_external_modes_do_not_require_full_e2e_attempt() -> None:
    text = RUNNER.read_text(encoding="utf-8-sig")
    assert "$SmokeTest" in text
    assert "$PreflightOnly" in text
    assert "$RecoverOnly" in text
    assert "$ResumeFromPostDailyQuality" in text
    assert "$ResumeAfterDeepDive" in text
    assert "$operationKind = 'resume_model'" in text
