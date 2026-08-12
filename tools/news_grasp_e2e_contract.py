"""News-Grasp E2E launch compositionのproduct-local決定論consumer。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping


class E2ECompositionContractError(RuntimeError):
    """E2E launch identityの不整合。"""


ROUTES = {
    "official_wrapper": "scripts/ops/invoke-scheduled-equivalent-nopublish.ps1",
    "launcher": "scripts/ops/news-grasp-task-launcher.pyw",
    "runner": "scripts/ops/news-grasp-runner.ps1",
    "wrapper": "scripts/ops/run_codex_with_timeout.ps1",
    "bridge": "tools/e2e_final_admission_bridge.py",
}


def _read(
    root: Path, relative: str, overrides: Mapping[str, str]
) -> str:
    if relative in overrides:
        return overrides[relative]
    try:
        return (root / relative).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise E2ECompositionContractError("NEWS_GRASP_E2E_ROUTE_UNAVAILABLE") from exc


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def validate_e2e_launch_contract(
    repo_root: Path | str,
    *,
    source_overrides: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """installed launcherからbroker execまでのidentity bundleを検証する。"""

    root = Path(repo_root).resolve(strict=True)
    overrides = dict(source_overrides or {})
    unknown = sorted(set(overrides) - set(ROUTES.values()))
    if unknown:
        raise E2ECompositionContractError("NEWS_GRASP_E2E_ROUTE_UNKNOWN")
    sources = {
        name: _read(root, relative, overrides)
        for name, relative in ROUTES.items()
    }
    official_wrapper = sources["official_wrapper"]
    launcher = sources["launcher"]
    runner = sources["runner"]
    wrapper = sources["wrapper"]
    bridge = sources["bridge"]

    official_markers = (
        "'validate-issued'",
        "$authorizeOutput =",
        "'activate'",
        "'validate-activated'",
        "'consume'",
        "& $installedTaskPythonPath @installedLauncherArguments",
    )

    launcher_markers = (
        "def _run_installed_nopublish_authority(",
        "NEWS_GRASP_INSTALLED_NOPUBLISH_LAUNCH_AUTHORITY_V1",
        "externalHealthAuthorityFixtureSha256",
        "runnerArgumentsFileSha256",
        "runnerExecutableSha256",
        "executionRepoRoot",
        "subprocess.run(",
    )
    runner_markers = (
        "'validate-activated'",
        "'claim-runner'",
        "'write-runner-claim-witness'",
        "'--expected-owner-pid' $PID",
        "'HighCostClaimWitness' = [string]$script:HighCostClaimWitness",
        "Invoke-CodexWrapper",
    )
    wrapper_markers = (
        "'validate-runner-claim-witness'",
        "'--expected-execution-root' $script:CanonicalExecutionRoot",
        "'--execution-root', $script:CanonicalExecutionRoot",
        "'--timeout-seconds', [string]$TimeoutSec",
        "'--idle-timeout-seconds', [string]$IdleTimeoutSec",
        "'--executable', $modelExecutable",
        "'--e2e-final-claim-receipt', $E2EFinalClaimReceiptPath",
        "'--e2e-final-claim-witness', $HighCostClaimWitness",
        "CreateSuspendedAssignedProcess",
    )
    bridge_markers = (
        "SHGetKnownFolderPath",
        "_managed_authority_root()",
        "_issue_execution_lock(",
        '"NEWS_GRASP_E2E_ADMISSION_WAL_V1"',
        '"runner_reserved"',
        '"runner_claimed"',
        "_recover_wal(",
    )
    for code, source, markers in (
        ("NEWS_GRASP_E2E_OFFICIAL_WRAPPER_INVALID", official_wrapper, official_markers),
        ("NEWS_GRASP_E2E_LAUNCHER_BINDING_INVALID", launcher, launcher_markers),
        ("NEWS_GRASP_E2E_RUNNER_CLAIM_INVALID", runner, runner_markers),
        ("NEWS_GRASP_E2E_BROKER_BUNDLE_INVALID", wrapper, wrapper_markers),
        ("NEWS_GRASP_E2E_WAL_INVALID", bridge, bridge_markers),
    ):
        if any(marker not in source for marker in markers):
            raise E2ECompositionContractError(code)

    prepare_at = official_wrapper.index("'validate-issued'")
    authorize_at = official_wrapper.index("$authorizeOutput =")
    parent_activate_at = official_wrapper.index("$activateOutput =")
    parent_validate_at = official_wrapper.index("$validatedOutput =")
    consume_at = official_wrapper.index("'consume'", parent_validate_at)
    installed_launch_at = official_wrapper.index(
        "& $installedTaskPythonPath @installedLauncherArguments"
    )
    if not (
        prepare_at
        < authorize_at
        < parent_activate_at
        < parent_validate_at
        < consume_at
        < installed_launch_at
    ):
        raise E2ECompositionContractError("NEWS_GRASP_E2E_PARENT_ORDER_INVALID")

    activate_at = runner.index("'validate-activated'")
    claim_at = runner.index("'claim-runner'")
    witness_at = runner.index("'write-runner-claim-witness'")
    invocation_at = runner.index("$agentRc = Invoke-CodexWrapper")
    if not activate_at < claim_at < witness_at < invocation_at:
        raise E2ECompositionContractError("NEWS_GRASP_E2E_CLAIM_ORDER_INVALID")

    witness_validate_at = wrapper.index("'validate-runner-claim-witness'")
    broker_args_at = wrapper.index("$effectiveArgs = @(")
    process_create_at = wrapper.index("CreateSuspendedAssignedProcess", broker_args_at)
    if not witness_validate_at < broker_args_at < process_create_at:
        raise E2ECompositionContractError("NEWS_GRASP_E2E_BROKER_ORDER_INVALID")

    return {
        "schemaVersion": "E2E_COMPOSITION_AUTHORITY_V1",
        "status": "green",
        "route": "installed_launcher_to_runner_claim_to_broker_exec",
        "executionRootBound": True,
        "executableBound": True,
        "ownerClaimBound": True,
        "timeoutBrokerOwned": True,
        "directEntryRejected": True,
        "oneShotWalBound": True,
        "fixedManagedRoot": True,
        "compositionOrder": [
            "prepare",
            "authorize",
            "activate",
            "consume",
            "claim",
            "launch",
        ],
        "routeHashes": {
            ROUTES[name]: _sha256(source) for name, source in sources.items()
        },
    }
