#!/usr/bin/env python3
"""runner wrapper (~/bin/run_codex_with_timeout.ps1) の契約テスト。"""
from __future__ import annotations

import os
import json
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tools.news_grasp_e2e_attempt_policy import (
    append_policy_transition,
    bind_policy_admission,
    issue_logical_attempt,
    new_policy,
)

WRAPPER = Path(os.environ.get(
    "NEWS_GRASP_CODEX_WRAPPER",
    r"C:\Users\hidek\bin\run_codex_with_timeout.ps1",
))
POWERSHELL = os.environ.get("NEWS_GRASP_POWERSHELL", "powershell")
ROOT = Path(__file__).resolve().parent.parent
REPO_WRAPPER = ROOT / "scripts" / "ops" / "run_codex_with_timeout.ps1"

pytestmark = pytest.mark.skipif(not WRAPPER.exists(), reason=f"wrapper not found: {WRAPPER}")


def _write_external_health_authority(profile: Path, broker: Path) -> None:
    """外部制御面のRedを隠さず、隔離fixtureでのみGreenへ束縛する。"""
    authority_path = profile / ".codex" / "state" / "high-cost-operation" / "external-health-authority-v1.json"
    authority_path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "schemaVersion": "EXTERNAL_CONTROL_PLANE_HEALTH_AUTHORITY_V1",
        "authorityLineageId": "lineage-a",
        "authorityLineageDerivation": "sha256-utf8-lf-v1",
        "authorityGeneration": 1,
        "previousReceiptSha256": "0" * 64,
        "canonicalDescriptorPath": str((profile / "descriptor.json").resolve()),
        "canonicalDescriptorSha256": "1" * 64,
        "sourceBrokerPath": str(broker.resolve()),
        "sourceBrokerSha256": "2" * 64,
        "installedBrokerPath": str(broker.resolve()),
        "installedBrokerSha256": "2" * 64,
        "dependencyGenerationHash": "3" * 64,
        "routeGenerationHash": "4" * 64,
        "ledgerGenerationId": "ledger-fixture",
        "registryAnchorGenerationId": "registry-fixture",
        "promotionGuardGenerationId": "promotion-fixture",
        "statefulSelfTestStatus": "green",
        "statefulSelfTestId": "self-test-fixture",
        "testedAt": "2026-08-11T00:00:00+00:00",
        "publisherId": "global-control-plane-owner",
    }
    body["receiptSha256"] = hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    authority_path.write_text(json.dumps(body, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def _write_external_control_script(workspace: Path) -> None:
    target = workspace / "tools" / "news_grasp_external_control.py"
    target.write_text(
        (ROOT / "tools" / "news_grasp_external_control.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )


def _fake_codex(tmp_path: Path) -> Path:
    fake = tmp_path / "fake_codex.ps1"
    fake.write_text(
        "Write-Output ('ARGV:' + ($args -join ' '))\n"
        "Write-Output '{\"type\":\"result\",\"is_error\":false}'\n",
        encoding="utf-8-sig",
    )
    return fake


def _fake_codex_with_usage(tmp_path: Path) -> Path:
    fake = tmp_path / "fake_codex_usage.ps1"
    fake.write_text(
        "Write-Output ('ARGV:' + ($args -join ' '))\n"
        "Write-Output 'tokens used'\n"
        "Write-Output '12,345'\n",
        encoding="utf-8-sig",
    )
    return fake


def _fake_codex_usage_limit(tmp_path: Path) -> Path:
    fake = tmp_path / "fake_codex_usage_limit.ps1"
    fake.write_text(
        "Write-Output \"ERROR: You've hit your usage limit.\"\n"
        "Write-Output 'Visit https://chatgpt.com/codex/settings/usage to purchase more credits or try again at 5:36 AM.'\n"
        "exit 1\n",
        encoding="utf-8-sig",
    )
    return fake


def _fake_codex_success_with_quoted_quota_terms(tmp_path: Path) -> Path:
    fake = tmp_path / "fake_codex_success_with_quoted_quota_terms.ps1"
    fake.write_text(
        "Write-Output '調査メモ: purchase more credits という文言を誤検知してはならない。'\n"
        "Write-Output 'tokens used'\n"
        "Write-Output '123'\n"
        "exit 0\n",
        encoding="utf-8-sig",
    )
    return fake


def _fake_codex_with_delayed_exit(tmp_path: Path, sentinel: Path) -> Path:
    fake = tmp_path / "fake_codex_delayed.ps1"
    fake.write_text(
        "$sentinel = [Environment]::GetEnvironmentVariable('CODEX_FAKE_SUCCESS_SENTINEL', 'Process')\n"
        "if (-not $sentinel) { Write-Error 'CODEX_FAKE_SUCCESS_SENTINEL missing'; exit 99 }\n"
        "$childCode = \"Start-Sleep -Seconds 2; [System.IO.File]::WriteAllText(''${sentinel}'', ''mutated'', [System.Text.UTF8Encoding]::new(`$false))\"\n"
        "Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile', '-Command', $childCode) -WindowStyle Hidden | Out-Null\n"
        "Write-Output '{\"type\":\"result\",\"is_error\":false}'\n"
        "[System.IO.File]::WriteAllText($sentinel, 'green', [System.Text.UTF8Encoding]::new($false))\n"
        "Start-Sleep -Seconds 20\n"
        "exit 0\n",
        encoding="utf-8",
    )
    return fake


def _success_probe_script(tmp_path: Path, sentinel: Path) -> Path:
    probe = tmp_path / "success_probe.ps1"
    probe.write_text(
        "param([string]$Sentinel)\n"
        "if (Test-Path -LiteralPath $Sentinel) { exit 0 }\n"
        "exit 1\n",
        encoding="utf-8",
    )
    return probe


def _fake_codex_capture_ps1(tmp_path: Path) -> Path:
    fake = tmp_path / "fake_codex_capture.ps1"
    fake.write_text(
        "$capturePath = $env:CODEX_FAKE_CAPTURE_JSON\n"
        "if (-not $capturePath) { Write-Error 'CODEX_FAKE_CAPTURE_JSON missing'; exit 99 }\n"
        "$stdinBytesStream = [Console]::OpenStandardInput()\n"
        "$stdinMemory = [System.IO.MemoryStream]::new()\n"
        "$stdinBytesStream.CopyTo($stdinMemory)\n"
        "$stdinText = [System.Text.Encoding]::UTF8.GetString($stdinMemory.ToArray())\n"
        "$data = [ordered]@{\n"
        "  argv = @($args)\n"
        "  stdin = $stdinText\n"
        "  env = [ordered]@{\n"
        "    PYTHONIOENCODING = $env:PYTHONIOENCODING\n"
        "    PYTHONUTF8 = $env:PYTHONUTF8\n"
        "    CODEX_NONINTERACTIVE_SESSION = $env:CODEX_NONINTERACTIVE_SESSION\n"
        "    CODEX_OUTPUT_CONTRACT = $env:CODEX_OUTPUT_CONTRACT\n"
        "  }\n"
        "}\n"
        "$json = $data | ConvertTo-Json -Depth 5\n"
        "[System.IO.File]::WriteAllText($capturePath, $json, [System.Text.UTF8Encoding]::new($false))\n"
        "Write-Output '{\"type\":\"result\",\"is_error\":false}'\n"
        "exit 0\n",
        encoding="utf-8",
    )
    return fake


def _canonical_test_broker(tmp_path: Path) -> tuple[list[str], dict[str, str]]:
    """本番と同じ必須broker境界を、外部modelなしで通す隔離fixtureを返す。"""
    # USERPROFILEそのものを隔離rootにして、broker正本照合とログ秘匿を同時に再演する。
    profile = tmp_path
    broker = profile / "bin" / "ai-model-spawn-broker.py"
    workspace = tmp_path / "workspace"
    registry = workspace / "docs" / "harness" / "high_cost_model_routes_v1.json"
    budget_validator = workspace / "tools" / "harness" / "high_cost_operation_budget.py"
    admission = tmp_path / "scheduled-operation-admission.json"
    broker.parent.mkdir(parents=True, exist_ok=True)
    registry.parent.mkdir(parents=True, exist_ok=True)
    budget_validator.parent.mkdir(parents=True, exist_ok=True)
    broker.write_text(
        "import subprocess\n"
        "import sys\n"
        "args = sys.argv[1:]\n"
        "if args and args[0] == '-I':\n"
        "    args = args[1:]\n"
        "if not args or args[0] != 'exec':\n"
        "    raise SystemExit(97)\n"
        "try:\n"
        "    executable = args[args.index('--executable') + 1]\n"
        "    separator = args.index('--')\n"
        "except (ValueError, IndexError):\n"
        "    raise SystemExit(98)\n"
        "raise SystemExit(subprocess.run([executable, *args[separator + 1:]]).returncode)\n",
        encoding="utf-8",
    )
    registry.write_text("{}\n", encoding="utf-8")
    budget_validator.write_text(
        "# scheduled_production smoke fixture: presence-only; validator is not executed.\n",
        encoding="utf-8",
    )
    admission.write_text("{}\n", encoding="utf-8")
    _write_external_control_script(workspace)
    env = os.environ.copy()
    env["USERPROFILE"] = str(profile)
    _write_external_health_authority(profile, broker)
    args = [
        "-HighCostWorkspaceRoot", str(workspace),
        "-HighCostBudgetToolPath", str(broker),
        "-HighCostPythonExe", sys.executable,
        "-HighCostCallId", f"test-{tmp_path.name}",
        "-HighCostAdmissionPath", str(admission),
        "-HighCostExpectedOperationKind", "scheduled_production",
    ]
    return args, env


def _full_e2e_claim_fixture(tmp_path: Path, claim_mode: str) -> tuple[list[str], dict[str, str], Path]:
    profile = tmp_path / "profile"
    workspace = tmp_path / "workspace"
    execution_root = tmp_path / "execution"
    bridge_root = execution_root / "tools"
    broker = profile / "bin" / "ai-model-spawn-broker.py"
    validator = workspace / "tools" / "harness" / "high_cost_operation_budget.py"
    registry = workspace / "docs" / "harness" / "high_cost_model_routes_v1.json"
    parent = execution_root / "parent-authority.json"
    admission = execution_root / "admission.json"
    arguments = execution_root / "admission.runner-arguments.json"
    reservation = execution_root / "admission.e2e-final-reservation.json"
    claim = execution_root / "admission.e2e-final-claim.json"
    bridge = bridge_root / "e2e_final_admission_bridge.py"
    for path in (broker, validator, registry, parent, admission, arguments, reservation):
        path.parent.mkdir(parents=True, exist_ok=True)
    bridge_root.mkdir(parents=True, exist_ok=True)
    broker.write_text(
        "import os,sys\n"
        "args=sys.argv[1:]\n"
        "if args and args[0] == '-I': args=args[1:]\n"
        "if args and args[0] == 'exec':\n"
        "    open(os.environ['BROKER_SENTINEL'],'w').write('broker')\n"
        "raise SystemExit(97)\n",
        encoding="utf-8",
    )
    registry.write_text("{}\n", encoding="utf-8")
    parent_payload = {
        "schemaVersion": "HIGH_COST_OPERATION_ADMISSION_V1",
        "state": "activated",
        "attemptKind": "full_e2e",
        "executionRoot": str(execution_root.resolve()),
        "lineageEpoch": 1,
    }
    parent.write_text(json.dumps(parent_payload) + "\n", encoding="utf-8")
    validator.write_text(
        "import json,sys\n"
        "if sys.argv[1:] and sys.argv[1] == 'validate-activated':\n"
        f"    print({json.dumps(parent_payload)})\n"
        "    raise SystemExit(0)\n"
        "raise SystemExit(97)\n",
        encoding="utf-8",
    )
    admission.write_text("{}\n", encoding="utf-8")
    arguments.write_text("[]\n", encoding="utf-8")
    reservation.write_text("{}\n", encoding="utf-8")
    if claim_mode != "missing":
        claim.write_text(json.dumps({"state": claim_mode}) + "\n", encoding="utf-8")
    bridge.write_text(
        "import sys\n"
        "if sys.argv[1:] and sys.argv[1] == 'validate-runner-claim':\n"
        "    raise SystemExit(2)\n"
        "raise SystemExit(97)\n",
        encoding="utf-8",
    )
    _write_external_control_script(workspace)
    env = os.environ.copy()
    env["USERPROFILE"] = str(profile)
    _write_external_health_authority(profile, broker)
    env["BROKER_SENTINEL"] = str(tmp_path / "broker-started.txt")
    model_sentinel = tmp_path / "model-started.txt"
    codex = tmp_path / "fake-codex.ps1"
    codex.write_text(
        f"[IO.File]::WriteAllText('{model_sentinel}', 'started')\nexit 0\n",
        encoding="utf-8",
    )
    env["MODEL_SENTINEL"] = str(model_sentinel)
    args = [
        "-HighCostWorkspaceRoot", str(workspace),
        "-HighCostBudgetToolPath", str(broker),
        "-HighCostPythonExe", sys.executable,
        "-HighCostCallId", f"claim-{claim_mode}",
        "-HighCostExpectedOperationKind", "full_e2e",
        "-HighCostAttemptId", "nopublish:2026-08-10",
        "-HighCostParentAuthorityPath", str(parent),
        "-E2EFinalAdmissionPath", str(admission),
        "-E2EFinalRunnerArgumentsPath", str(arguments),
        "-E2EFinalReservationReceiptPath", str(reservation),
        "-E2EFinalClaimReceiptPath", str(claim),
        "-HighCostClaimWitness", "{}",
        "-HighCostCallReceiptPath",
        str(execution_root / "build" / "high-cost-call-receipts" / "call-receipt.json"),
    ]
    return args, env, codex


def _composed_claim_witness_fixture(
    tmp_path: Path,
) -> tuple[list[str], dict[str, str], Path, Path]:
    """claim witness fileをwrapper→brokerへ実引数で通す隔離composition fixture。"""

    powershell_executable = Path(shutil.which(POWERSHELL) or POWERSHELL).resolve(strict=True)
    profile = tmp_path / "profile"
    workspace = tmp_path / "workspace"
    execution_root = tmp_path / "execution"
    broker = profile / "bin" / "ai-model-spawn-broker.py"
    validator = workspace / "tools" / "harness" / "high_cost_operation_budget.py"
    registry = workspace / "docs" / "harness" / "high_cost_model_routes_v1.json"
    bridge = execution_root / "tools" / "e2e_final_admission_bridge.py"
    parent = execution_root / "parent-authority.json"
    admission = execution_root / "admission.json"
    arguments = execution_root / "admission.runner-arguments.json"
    reservation = execution_root / "admission.e2e-final-reservation.json"
    claim = execution_root / "admission.e2e-final-claim.json"
    witness = execution_root / "admission.e2e-final-claim-witness.json"
    child_admission = execution_root / "child-admission.json"
    model_sentinel = tmp_path / "model-started.txt"
    broker_capture = tmp_path / "broker-capture.jsonl"
    for path in (broker, validator, registry, bridge, parent, admission, arguments, reservation, claim):
        path.parent.mkdir(parents=True, exist_ok=True)
    parent_payload = {
        "schemaVersion": "HIGH_COST_OPERATION_ADMISSION_V1",
        "state": "activated",
        "attemptKind": "full_e2e",
        "executionRoot": str(execution_root.resolve()),
        "lineageEpoch": 1,
    }
    parent.write_text(json.dumps(parent_payload) + "\n", encoding="utf-8")
    admission.write_text(
        json.dumps(
            {
                "schemaVersion": "HIGH_COST_OPERATION_ADMISSION_V3",
                "state": "issued",
                "attemptKey": "News-Grasp:2026-08-10:scheduled-equivalent-nopublish",
                "issueDate": "2026-08-10",
                "expectedClaimWitnessPath": str(witness.resolve()),
                "runnerExecutablePath": str(powershell_executable),
                "authorityPythonExecutablePath": str(Path(sys.executable).resolve()),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    admission_value = json.loads(admission.read_text(encoding="utf-8"))
    admission_projection = dict(admission_value)
    admission_projection.pop("admissionId", None)
    admission_value["admissionId"] = hashlib.sha256(
        json.dumps(admission_projection, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    admission.write_text(json.dumps(admission_value, sort_keys=True) + "\n", encoding="utf-8")
    policy_path = execution_root / "e2e-attempt-policy.json"
    policy = issue_logical_attempt(
        bind_policy_admission(new_policy(), admission),
        1,
    )
    policy_path.write_text(json.dumps(policy, sort_keys=True) + "\n", encoding="utf-8")
    transition = policy["transition"]
    producer = Path(sys.executable).resolve()
    transition_receipt = policy_path.with_name("e2e-transition-1.json")
    transition_receipt.write_text(
        json.dumps(
            {
                "schemaVersion": "NEWS_GRASP_E2E_TRANSITION_RECEIPT_V1",
                "event": transition["event"],
                "sequence": transition["sequence"],
                "attemptKey": admission_value["attemptKey"],
                "issueDate": admission_value["issueDate"],
                "admissionId": admission_value["admissionId"],
                "previousStateSha256": transition["previousStateSha256"],
                "stateSha256": transition["stateSha256"],
                "producerRouteId": "news-grasp-runner",
                "status": "succeeded",
                "producerProcessId": os.getpid(),
                "producerExecutablePath": str(producer),
                "producerExecutableSha256": hashlib.sha256(producer.read_bytes()).hexdigest(),
                "outcomeSchemaVersion": "NEWS_GRASP_E2E_TRANSITION_OUTCOME_V1",
                "outcomeStatus": "admission_validated",
                "outcomeSha256": "0" * 64,
                "outcomeStatePath": "",
                "outcomeStateSha256": "",
                "outcomeExitCode": -1,
                "outcomeRunnerStatus": "not_started",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    append_policy_transition(policy_path, admission, transition_receipt_path=transition_receipt)
    # この smoke は wrapper の浅い引数契約を隔離検証するため、wrapper が受け付ける
    # 旧互換キー集合だけを渡す。完全な admission binding は上の canonical ledger に保持する。
    wrapper_policy = dict(policy)
    wrapper_policy.pop("admissionBinding", None)
    policy_path.write_text(json.dumps(wrapper_policy, sort_keys=True) + "\n", encoding="utf-8")
    arguments.write_text('["-NoPublish"]\n', encoding="utf-8")
    reservation.write_text("{}\n", encoding="utf-8")
    claim.write_text("{}\n", encoding="utf-8")
    witness_value = {
        "schemaVersion": "E2E_FINAL_RUNNER_CLAIM_WITNESS_V1",
        "claimId": "a" * 64,
        "claimReceiptPath": str(claim.resolve()),
        "claimReceiptSha256": "b" * 64,
        "ownerProcessIdentity": {
            "pid": os.getpid(),
            "parentPid": 1,
            "creationFileTimeUtc": "fixture-owner",
            "imagePath": str(powershell_executable),
            "imageSha256": "c" * 64,
        },
        "attemptKey": "News-Grasp:2026-08-10:scheduled-equivalent-nopublish",
        "admissionId": "d" * 64,
        "admissionPath": str(admission.resolve()),
        "runnerArgumentsPath": str(arguments.resolve()),
        "reservationReceiptPath": str(reservation.resolve()),
        "reservationReceiptSha256": "e" * 64,
    }
    witness.write_text(json.dumps(witness_value) + "\n", encoding="utf-8")
    validator.write_text(
        "import json,sys\n"
        "if sys.argv[1:] and sys.argv[1] == 'validate-activated':\n"
        f"    print({json.dumps(parent_payload)})\n"
        "    raise SystemExit(0)\n"
        "raise SystemExit(97)\n",
        encoding="utf-8",
    )
    registry.write_text("{}\n", encoding="utf-8")
    bridge.write_text(
        "import json,sys\n"
        "args=sys.argv[1:]\n"
        "if not args or args[0] != 'validate-runner-claim-witness': raise SystemExit(96)\n"
        "path=args[args.index('--claim-witness')+1]\n"
        "print(json.dumps(json.load(open(path,encoding='utf-8'))))\n",
        encoding="utf-8",
    )
    broker.write_text(
        "import json,os,sys\n"
        "args=sys.argv[1:]\n"
        "if args and args[0] == '-I': args=args[1:]\n"
        "witness=args[args.index('--e2e-final-claim-witness')+1]\n"
        "if not os.path.isabs(witness) or not os.path.isfile(witness): raise SystemExit(95)\n"
        "with open(os.environ['BROKER_CAPTURE'],'a',encoding='utf-8') as s: s.write(json.dumps({'command':args[0],'witness':witness})+'\\n')\n"
        "if args[0] == 'admit':\n"
        "    receipt=os.environ['CHILD_ADMISSION']\n"
        "    value={'schemaVersion':'HIGH_COST_OPERATION_ADMISSION_V3','operationKind':'full_e2e','attemptId':'nopublish:2026-08-10','route':args[args.index('--route')+1],'executionRoot':args[args.index('--execution-root')+1],'parentAuthorityPath':args[args.index('--parent-operation-authority')+1],'callIdSha256':'f'*64,'commandSha256':'1'*64,'receiptPath':receipt}\n"
        "    open(receipt,'w',encoding='utf-8').write(json.dumps(value))\n"
        "    print(json.dumps(value)); raise SystemExit(0)\n"
        "if args[0] == 'exec':\n"
        "    open(os.environ['MODEL_SENTINEL'],'w',encoding='utf-8').write('started')\n"
        "    raise SystemExit(0)\n"
        "raise SystemExit(94)\n",
        encoding="utf-8",
    )
    _write_external_control_script(workspace)
    codex = tmp_path / "unused-codex.ps1"
    codex.write_text("exit 93\n", encoding="utf-8-sig")
    env = os.environ.copy()
    env.update(
        {
            "USERPROFILE": str(profile),
            "BROKER_CAPTURE": str(broker_capture),
            "CHILD_ADMISSION": str(child_admission),
            "MODEL_SENTINEL": str(model_sentinel),
        }
    )
    _write_external_health_authority(profile, broker)
    args = [
        "-HighCostWorkspaceRoot", str(workspace),
        "-HighCostBudgetToolPath", str(broker),
        "-HighCostPythonExe", sys.executable,
        "-HighCostCallId", "claim-composition",
        "-HighCostExpectedOperationKind", "full_e2e",
        "-HighCostAttemptId", "nopublish:2026-08-10",
        "-HighCostParentAuthorityPath", str(parent),
        "-E2EFinalAdmissionPath", str(admission),
        "-E2EFinalRunnerArgumentsPath", str(arguments),
        "-E2EFinalReservationReceiptPath", str(reservation),
        "-E2EFinalClaimReceiptPath", str(claim),
        "-HighCostClaimWitness", str(witness),
        "-E2EAttemptPolicyPath", str(policy_path),
        "-E2ELogicalAttempt", "1",
        "-HighCostCallReceiptPath",
        str(
            execution_root
            / "build"
            / "high-cost-call-receipts"
            / "claim-composition.json"
        ),
    ]
    return args, env, codex, broker_capture


def test_codex_wrapper_uses_prompt_file_working_directory_and_schema(tmp_path: Path) -> None:
    prompt_file = tmp_path / "prompt.md"
    log_file = tmp_path / "wrapper.log"
    prompt_file.write_text("日本語 smoke test\n", encoding="utf-8")
    high_cost_args, env = _canonical_test_broker(tmp_path)
    result = subprocess.run(
        [
            POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(WRAPPER),
            "-CodexExe", str(_fake_codex(tmp_path)),
            "-PromptFile", str(prompt_file),
            "-LogFile", str(log_file),
            "-TimeoutSec", "30",
            "-IdleTimeoutSec", "30",
            "-WorkingDirectory", str(ROOT),
            "-OutputSchema", str(ROOT / "schemas" / "model_eval_output.schema.json"),
            "-OutputLastMessage", str(tmp_path / "last-message.txt"),
            "-Model", "test-model",
            "-FlowName", "reporter:test",
            *high_cost_args,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        env=env,
    )

    log = log_file.read_text(encoding="utf-8", errors="replace")
    assert result.returncode == 0, result.stderr + log
    assert "exec" in log
    assert "--output-schema" in log
    assert "--output-last-message" in log or " -o " in log
    assert "-C" in log
    assert "--search" not in log
    assert "test-model" in log
    assert str(tmp_path) not in log
    assert str(ROOT) not in log


def test_codex_wrapper_sets_noninteractive_artifact_gate_env_and_stdin_prompt(tmp_path: Path) -> None:
    """非対話 runner の正本は env/stdin/exit code で、人間向け完了報告に依存しない。"""
    prompt_file = tmp_path / "prompt.md"
    log_file = tmp_path / "wrapper.log"
    capture_file = tmp_path / "capture.json"
    prompt_text = "日本語の artifact gate prompt\n長大本文を argv に載せない\n"
    prompt_file.write_text(prompt_text, encoding="utf-8")
    high_cost_args, env = _canonical_test_broker(tmp_path)
    env["CODEX_FAKE_CAPTURE_JSON"] = str(capture_file)

    result = subprocess.run(
        [
            POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(WRAPPER),
            "-CodexExe", str(_fake_codex_capture_ps1(tmp_path)),
            "-PromptFile", str(prompt_file),
            "-LogFile", str(log_file),
            "-TimeoutSec", "30",
            "-IdleTimeoutSec", "30",
            "-WorkingDirectory", str(ROOT),
            "-Model", "test-model",
            "-FlowName", "reporter:test",
            *high_cost_args,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        env=env,
    )

    log = log_file.read_text(encoding="utf-8", errors="replace")
    assert result.returncode == 0, result.stderr + log
    capture = json.loads(capture_file.read_text(encoding="utf-8-sig"))
    argv_text = " ".join(capture["argv"])
    assert capture["stdin"].replace("\r\n", "\n") == prompt_text
    assert "日本語の artifact gate prompt" not in argv_text, "prompt leaked into argv"
    assert capture["env"]["CODEX_NONINTERACTIVE_SESSION"] == "1", "artifact-gate env missing"
    assert capture["env"]["CODEX_OUTPUT_CONTRACT"] == "artifact-gate", "artifact-gate env missing"
    assert capture["env"]["PYTHONUTF8"] == "1"
    assert capture["env"]["PYTHONIOENCODING"] == "utf-8:backslashreplace"


def test_codex_wrapper_has_no_legacy_agent_parameters() -> None:
    text = WRAPPER.read_text(encoding="utf-8-sig")
    assert "ClaudeExe" not in text
    assert "run_claude" not in text


def test_codex_wrapper_quotes_argument_list_for_paths_with_spaces() -> None:
    text = REPO_WRAPPER.read_text(encoding="utf-8-sig")
    assert "ConvertTo-ProcessArgumentString" in text
    assert "$effectiveArgString = ConvertTo-ProcessArgumentString" in text
    assert "[NewsGraspOwnedJob]::CreateSuspendedAssignedProcess($filePath, $effectiveArgString, $WorkingDirectory" in text
    assert "-ArgumentList $effectiveArgs" not in text


def test_codex_wrapper_rejects_direct_cross_mode_inputs_before_model_start(tmp_path: Path) -> None:
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("cross-mode rejection\n", encoding="utf-8")
    sentinel = tmp_path / "model-started.txt"

    scheduled_args, scheduled_env = _canonical_test_broker(tmp_path / "scheduled")
    scheduled_args = list(scheduled_args)
    scheduled_args += ["-HighCostParentAuthorityPath", str(tmp_path / "scheduled-parent.json")]
    scheduled_result = subprocess.run(
        [
            POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(REPO_WRAPPER),
            "-CodexExe", str(_fake_codex(tmp_path)),
            "-PromptFile", str(prompt_file),
            "-LogFile", str(tmp_path / "scheduled.log"),
            "-WorkingDirectory", str(ROOT),
            "-FlowName", "scheduled:test",
            *scheduled_args,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        env=scheduled_env,
    )
    assert scheduled_result.returncode == 126
    assert not sentinel.exists()
    assert "HIGH_COST_SCHEDULED_PARENT_AUTHORITY_FORBIDDEN" in (
        tmp_path / "scheduled.log"
    ).read_text(encoding="utf-8", errors="replace")

    full_args, full_env = _canonical_test_broker(tmp_path / "full")
    full_args = list(full_args)
    full_args[full_args.index("-HighCostExpectedOperationKind") + 1] = "full_e2e"
    full_result = subprocess.run(
        [
            POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(REPO_WRAPPER),
            "-CodexExe", str(_fake_codex(tmp_path)),
            "-PromptFile", str(prompt_file),
            "-LogFile", str(tmp_path / "full.log"),
            "-WorkingDirectory", str(ROOT),
            "-FlowName", "full:test",
            *full_args,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        env=full_env,
    )
    assert full_result.returncode == 126
    assert not sentinel.exists()
    assert "HIGH_COST_FULL_E2E_SHARED_ADMISSION_FORBIDDEN" in (
        tmp_path / "full.log"
    ).read_text(encoding="utf-8", errors="replace")


@pytest.mark.parametrize("claim_mode", ["missing", "foreign", "stale"])
def test_full_e2e_direct_wrapper_rejects_claim_before_broker_or_model(
    tmp_path: Path, claim_mode: str
) -> None:
    args, env, codex = _full_e2e_claim_fixture(tmp_path / claim_mode, claim_mode)
    (tmp_path / "prompt.md").write_text("claim validation\n", encoding="utf-8")
    log_file = tmp_path / f"{claim_mode}.log"
    result = subprocess.run(
        [
            POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(REPO_WRAPPER),
            "-CodexExe", str(codex),
            "-PromptFile", str(tmp_path / "prompt.md"),
            "-LogFile", str(log_file),
            "-WorkingDirectory", str((tmp_path / claim_mode / "execution").resolve()),
            "-FlowName", f"full:{claim_mode}",
            *args,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        env=env,
    )
    assert result.returncode == 126, result.stderr + result.stdout
    assert not (tmp_path / claim_mode / "broker-started.txt").exists()
    assert not (tmp_path / claim_mode / "model-started.txt").exists()


@pytest.mark.parametrize("launch_mode", ["sequential", "start_job"])
def test_full_e2e_claim_witness_file_composes_sequential_and_start_job(
    tmp_path: Path,
    launch_mode: str,
) -> None:
    args, env, codex, broker_capture = _composed_claim_witness_fixture(
        tmp_path / launch_mode
    )
    prompt = tmp_path / "prompt.md"
    log_file = tmp_path / f"{launch_mode}.log"
    prompt.write_text("claim composition\n", encoding="utf-8")
    invocation = [
        "-CodexExe", str(codex),
        "-PromptFile", str(prompt),
        "-LogFile", str(log_file),
        "-TimeoutSec", "30",
        "-IdleTimeoutSec", "30",
        "-WorkingDirectory", str((tmp_path / launch_mode / "execution").resolve()),
        "-FlowName", "reporter:composition",
        *args,
    ]
    if launch_mode == "sequential":
        command = [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REPO_WRAPPER),
            *invocation,
        ]
    else:
        env = env.copy()
        isolated_profile = env["USERPROFILE"]
        env["USERPROFILE"] = os.environ["USERPROFILE"]
        invocation_path = tmp_path / "invocation.json"
        invocation_path.write_text(json.dumps(invocation), encoding="utf-8")
        harness = tmp_path / "start-job-harness.ps1"
        harness.write_text(
            "param([string]$Wrapper,[string]$InvocationPath,[string]$Profile)\n"
                "$job=Start-Job -ScriptBlock { $env:USERPROFILE=$using:Profile; $raw=Get-Content -LiteralPath $using:InvocationPath -Raw -Encoding UTF8 | ConvertFrom-Json; $named=@{}; for($i=0;$i -lt $raw.Count;$i+=2){$named[[string]$raw[$i].TrimStart('-')]=[string]$raw[$i+1]}; $w=$using:Wrapper; & $w @named }\n"
            "Receive-Job -Job $job -Wait -AutoRemoveJob\n"
            "exit 0\n",
            encoding="utf-8-sig",
        )
        command = [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(harness),
            "-Wrapper",
            str(REPO_WRAPPER),
                "-InvocationPath",
                str(invocation_path),
                "-Profile",
                isolated_profile,
            ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
        env=env,
    )
    log_text = (
        log_file.read_text(encoding="utf-8", errors="replace")
        if log_file.exists()
        else ""
    )
    assert result.returncode == 0, result.stderr + result.stdout + log_text
    assert broker_capture.is_file(), result.stderr + result.stdout + log_text
    rows = [json.loads(line) for line in broker_capture.read_text(encoding="utf-8").splitlines()]
    assert [row["command"] for row in rows] == ["admit", "exec"]
    assert all(Path(row["witness"]).is_file() for row in rows)


def test_codex_wrapper_writes_flow_usage_jsonl(tmp_path: Path) -> None:
    prompt_file = tmp_path / "prompt.md"
    log_file = tmp_path / "wrapper.log"
    usage_log = tmp_path / "usage.jsonl"
    prompt_file.write_text("usage smoke\n", encoding="utf-8")
    high_cost_args, env = _canonical_test_broker(tmp_path)

    result = subprocess.run(
        [
            POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(WRAPPER),
            "-CodexExe", str(_fake_codex_with_usage(tmp_path)),
            "-PromptFile", str(prompt_file),
            "-LogFile", str(log_file),
            "-TimeoutSec", "30",
            "-IdleTimeoutSec", "30",
            "-WorkingDirectory", str(ROOT),
            "-FlowName", "reporter:ai",
            "-UsageLog", str(usage_log),
            *high_cost_args,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    text = usage_log.read_text(encoding="utf-8")
    assert '"flow":"reporter:ai"' in text
    assert '"tokens_used":12345' in text
    assert str(tmp_path) not in text
    assert str(ROOT) not in text


def test_codex_wrapper_maps_usage_limit_to_typed_external_rc(tmp_path: Path) -> None:
    """Codex quota は内部 reporter 失敗ではなく、外部 readiness の rc=123 に正規化する。"""
    prompt_file = tmp_path / "prompt.md"
    log_file = tmp_path / "wrapper.log"
    usage_log = tmp_path / "usage.jsonl"
    prompt_file.write_text("quota smoke\n", encoding="utf-8")
    high_cost_args, env = _canonical_test_broker(tmp_path)

    result = subprocess.run(
        [
            POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(WRAPPER),
            "-CodexExe", str(_fake_codex_usage_limit(tmp_path)),
            "-PromptFile", str(prompt_file),
            "-LogFile", str(log_file),
            "-TimeoutSec", "30",
            "-IdleTimeoutSec", "30",
            "-WorkingDirectory", str(ROOT),
            "-FlowName", "reporter:ai",
            "-UsageLog", str(usage_log),
            *high_cost_args,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        env=env,
    )

    log = log_file.read_text(encoding="utf-8", errors="replace")
    usage = usage_log.read_text(encoding="utf-8", errors="replace")
    assert result.returncode == 123, result.stderr + log
    assert "codex quota detected" in log
    assert '"exit_code":123' in usage


def test_codex_wrapper_never_overrides_success_rc_from_quoted_quota_terms(tmp_path: Path) -> None:
    """成功出力中の説明・prompt・memory引用を実quotaへ読み替えない。"""
    prompt_file = tmp_path / "prompt.md"
    log_file = tmp_path / "wrapper.log"
    usage_log = tmp_path / "usage.jsonl"
    prompt_file.write_text("quoted quota terms smoke\n", encoding="utf-8")
    high_cost_args, env = _canonical_test_broker(tmp_path)

    result = subprocess.run(
        [
            POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(REPO_WRAPPER),
            "-CodexExe", str(_fake_codex_success_with_quoted_quota_terms(tmp_path)),
            "-PromptFile", str(prompt_file),
            "-LogFile", str(log_file),
            "-TimeoutSec", "30",
            "-IdleTimeoutSec", "30",
            "-WorkingDirectory", str(ROOT),
            "-FlowName", "reporter:it",
            "-UsageLog", str(usage_log),
            *high_cost_args,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        env=env,
    )

    assert result.returncode == 0, log_file.read_text(encoding="utf-8", errors="replace")
    assert '"exit_code":0' in usage_log.read_text(encoding="utf-8")


def test_codex_wrapper_rejects_success_probe_early_termination(tmp_path: Path) -> None:
    """broker-owned terminal契約ではsuccess probeの早期終了を拒否する。"""
    prompt_file = tmp_path / "prompt.md"
    log_file = tmp_path / "wrapper.log"
    usage_log = tmp_path / "usage.jsonl"
    sentinel = tmp_path / "artifact-green.txt"
    prompt_file.write_text("success probe smoke\n", encoding="utf-8")
    probe = _success_probe_script(tmp_path, sentinel)
    high_cost_args, env = _canonical_test_broker(tmp_path)
    env["CODEX_FAKE_SUCCESS_SENTINEL"] = str(sentinel)

    result = subprocess.run(
        [
            POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(WRAPPER),
            "-CodexExe", str(_fake_codex_with_delayed_exit(tmp_path, sentinel)),
            "-PromptFile", str(prompt_file),
            "-LogFile", str(log_file),
            "-TimeoutSec", "30",
            "-IdleTimeoutSec", "30",
            "-WorkingDirectory", str(ROOT),
            "-FlowName", "newsroom_editor",
            "-UsageLog", str(usage_log),
            "-SuccessProbeCommand", f'powershell -NoProfile -ExecutionPolicy Bypass -File "{probe}" -Sentinel "{sentinel}"',
            "-SuccessProbeIntervalSec", "1",
            "-SuccessProbeMinElapsedSec", "1",
            *high_cost_args,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        env=env,
    )

    log = log_file.read_text(encoding="utf-8", errors="replace")
    assert result.returncode == 125, result.stderr + log
    assert "SUCCESS_PROBE_EARLY_TERMINATION_FORBIDDEN" in log
    assert not sentinel.exists()
    assert not usage_log.exists()
