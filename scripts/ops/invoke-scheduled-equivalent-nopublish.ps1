# 本番 Scheduled Task と同じ news-grasp-runner.ps1 を、公開副作用なしで最終確認する。
# installed_launcher_identity: 最終実行は正規installerが配置したstable launcher identityへ束縛する。
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string] $RepoRoot,
    [Parameter(Mandatory=$true)][string] $DateStamp,
    [Parameter(Mandatory=$true)][string] $StateFile,
    [Parameter(Mandatory=$true)][string] $LogDir,
    [Parameter(Mandatory=$true)][string] $ReceiptPath,
    [Parameter(Mandatory=$true)][string] $PythonExe,
    [Parameter(Mandatory=$true)][string] $WorkspaceRoot,
    [Parameter(Mandatory=$true)][string] $BudgetPath,
    [Parameter(Mandatory=$true)][string] $EfficiencyDesignPath,
    [Parameter(Mandatory=$true)][string] $AdversarialReviewPath,
    [Parameter(Mandatory=$true)][string] $RouteManifestPath,
    [Parameter(Mandatory=$true)][string] $StaticReceiptPath,
    [Parameter(Mandatory=$true)][string] $SimulationReceiptPath,
    [Parameter(Mandatory=$true)][string] $E2EAdmissionPath,
    [string] $CausalReplacementProofPath = '',
    [string] $SupersessionApprovalPath = '',
    [string] $HighCostParentAuthorityPath = '',
    [string] $ExternalHealthAuthorityFixturePath = '',
    [string] $PowerShellExe = 'powershell.exe'
)

$ErrorActionPreference = 'Stop'
$null = Import-Module Microsoft.PowerShell.Utility -ErrorAction Stop
$repoPath = [System.IO.Path]::GetFullPath((Resolve-Path -LiteralPath $RepoRoot -ErrorAction Stop).Path)
$statePath = [System.IO.Path]::GetFullPath($StateFile)
$logPath = [System.IO.Path]::GetFullPath($LogDir)
$receiptFullPath = [System.IO.Path]::GetFullPath($ReceiptPath)
$workspacePath = [System.IO.Path]::GetFullPath((Resolve-Path -LiteralPath $WorkspaceRoot -ErrorAction Stop).Path)
$highCostOperationBudgetPath = Join-Path $workspacePath 'tools\harness\high_cost_operation_budget.py'
$highCostModelBrokerPath = Join-Path $env:USERPROFILE 'bin\ai-model-spawn-broker.py'
$operationKind = 'full_e2e'
$attemptId = "nopublish:$DateStamp"
function Get-CanonicalExistingFile {
    param(
        [Parameter(Mandatory=$true)][string] $Path,
        [Parameter(Mandatory=$true)][string] $Label,
        [string] $Boundary = '',
        [int64] $MaxBytes = 67108864
    )
    try {
        $resolved = [System.IO.Path]::GetFullPath((Resolve-Path -LiteralPath $Path -ErrorAction Stop).Path)
        if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
            throw "$Label is not a regular file"
        }
        $item = Get-Item -LiteralPath $resolved -Force -ErrorAction Stop
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "$Label contains a reparse point"
        }
        if ($Boundary) {
            $boundaryPath = [System.IO.Path]::GetFullPath((Resolve-Path -LiteralPath $Boundary -ErrorAction Stop).Path).TrimEnd('\')
            $boundaryPrefix = $boundaryPath + '\'
            if (-not [string]::Equals($resolved, $boundaryPath, [System.StringComparison]::OrdinalIgnoreCase) -and
                -not $resolved.StartsWith($boundaryPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
                throw "$Label is outside its canonical boundary"
            }
        }
        $cursor = $resolved
        while ($cursor) {
            $cursorItem = Get-Item -LiteralPath $cursor -Force -ErrorAction Stop
            if (($cursorItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "$Label traversal contains a reparse point"
            }
            if ($Boundary -and [string]::Equals($cursor, $boundaryPath, [System.StringComparison]::OrdinalIgnoreCase)) {
                break
            }
            $cursorParent = Split-Path -Parent $cursor
            if (-not $cursorParent -or $cursorParent -eq $cursor) { break }
            $cursor = $cursorParent
        }
        if ([int64]$item.Length -gt $MaxBytes) {
            throw "$Label exceeds bounded size"
        }
        return $resolved
    } catch {
        throw "HIGH_COST_CANONICAL_FILE_INVALID label=$Label path=$Path reason=$($_.Exception.Message)"
    }
}

function Get-CanonicalFuturePath {
    param(
        [Parameter(Mandatory=$true)][string] $Path,
        [string] $Suffix = '',
        [Parameter(Mandatory=$true)][string] $Boundary,
        [Parameter(Mandatory=$true)][string] $Label
    )
    try {
        $candidate = [System.IO.Path]::GetFullPath($Path)
        if ($Suffix -and -not $candidate.EndsWith($Suffix, [System.StringComparison]::Ordinal)) {
            throw "$Label suffix mismatch"
        }
        $boundaryPath = [System.IO.Path]::GetFullPath((Resolve-Path -LiteralPath $Boundary -ErrorAction Stop).Path).TrimEnd('\')
        $boundaryPrefix = $boundaryPath + '\'
        if (-not $candidate.StartsWith($boundaryPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "$Label is outside its canonical boundary"
        }
        $cursor = Split-Path -Parent $candidate
        while ($cursor) {
            if (Test-Path -LiteralPath $cursor) {
                $item = Get-Item -LiteralPath $cursor -Force -ErrorAction Stop
                if (($item.Attributes -band [System.IO.FileAttributes]::Directory) -eq 0) {
                    throw "$Label ancestor is not a directory"
                }
                if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                    throw "$Label ancestor is a reparse point"
                }
            }
            if ([string]::Equals($cursor, $boundaryPath, [System.StringComparison]::OrdinalIgnoreCase)) { break }
            $parent = Split-Path -Parent $cursor
            if (-not $parent -or $parent -eq $cursor) { throw "$Label boundary traversal failed" }
            $cursor = $parent
        }
        if (Test-Path -LiteralPath $candidate) {
            throw "$Label output already exists"
        }
        return $candidate
    } catch {
        throw "HIGH_COST_CANONICAL_FUTURE_PATH_INVALID label=$Label path=$Path reason=$($_.Exception.Message)"
    }
}

function Get-CanonicalFutureDirectory {
    param(
        [Parameter(Mandatory=$true)][string] $Path,
        [Parameter(Mandatory=$true)][string] $Boundary,
        [Parameter(Mandatory=$true)][string] $Label,
        [switch] $AllowExisting
    )
    try {
        $candidate = [System.IO.Path]::GetFullPath($Path)
        $boundaryPath = [System.IO.Path]::GetFullPath((Resolve-Path -LiteralPath $Boundary -ErrorAction Stop).Path).TrimEnd('\')
        $boundaryPrefix = $boundaryPath + '\'
        if (-not $candidate.StartsWith($boundaryPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "$Label is outside its canonical boundary"
        }
        $cursor = Split-Path -Parent $candidate
        while ($cursor) {
            if (Test-Path -LiteralPath $cursor) {
                $item = Get-Item -LiteralPath $cursor -Force -ErrorAction Stop
                if (($item.Attributes -band [System.IO.FileAttributes]::Directory) -eq 0 -or
                    ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                    throw "$Label ancestor is invalid"
                }
            }
            if ([string]::Equals($cursor, $boundaryPath, [System.StringComparison]::OrdinalIgnoreCase)) { break }
            $parent = Split-Path -Parent $cursor
            if (-not $parent -or $parent -eq $cursor) { throw "$Label boundary traversal failed" }
            $cursor = $parent
        }
        if (Test-Path -LiteralPath $candidate) {
            $item = Get-Item -LiteralPath $candidate -Force -ErrorAction Stop
            if (($item.Attributes -band [System.IO.FileAttributes]::Directory) -eq 0 -or
                ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "$Label output is not a regular directory"
            }
            if (-not $AllowExisting) { throw "$Label output already exists" }
        } elseif ($AllowExisting) {
            throw "$Label output disappeared after creation"
        }
        return $candidate
    } catch {
        throw "HIGH_COST_CANONICAL_FUTURE_PATH_INVALID label=$Label path=$Path reason=$($_.Exception.Message)"
    }
}

$parentAuthorityFullPath = "$receiptFullPath.high-cost-parent-authority.json"
if ($HighCostParentAuthorityPath -and
    -not [string]::Equals(
        [System.IO.Path]::GetFullPath($HighCostParentAuthorityPath),
        $parentAuthorityFullPath,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
    throw "HIGH_COST_PARENT_AUTHORITY_PATH_DRIFT: expected=$parentAuthorityFullPath actual=$HighCostParentAuthorityPath"
}
$supersessionArguments = @()
if ($SupersessionApprovalPath) {
    $supersessionArguments = @('--supersession-approval', $SupersessionApprovalPath)
}
$repoPrefix = $repoPath.TrimEnd('\') + '\'
foreach ($candidate in @($statePath, $logPath, $receiptFullPath, $parentAuthorityFullPath)) {
    if (-not $candidate.StartsWith($repoPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "HIGH_COST_CANONICAL_FUTURE_OUTPUT_INVALID: $candidate"
    }
}

$binPath = [System.IO.Path]::GetFullPath((Join-Path $env:USERPROFILE 'bin'))
$stableTaskAuthorityPath = Join-Path $binPath 'news-grasp-stable-task-authority-v1.json'
$runtimeRootConfigPath = Join-Path $binPath 'news-grasp-runtime-root-v1.json'
if (-not (Test-Path -LiteralPath $stableTaskAuthorityPath -PathType Leaf)) {
    throw "HIGH_COST_EXECUTABLE_IDENTITY_INVALID: stable task authority が見つかりません: $stableTaskAuthorityPath"
}
if (-not (Test-Path -LiteralPath $runtimeRootConfigPath -PathType Leaf)) {
    throw "HIGH_COST_EXECUTABLE_IDENTITY_INVALID: production runtime config が見つかりません: $runtimeRootConfigPath"
}
try {
    $stableTaskAuthorityPath = Get-CanonicalExistingFile -Path $stableTaskAuthorityPath -Label 'stable task authority' -Boundary $binPath -MaxBytes 65536
    $runtimeRootConfigPath = Get-CanonicalExistingFile -Path $runtimeRootConfigPath -Label 'production runtime config' -Boundary $binPath -MaxBytes 65536
    $stableTaskAuthority = Get-Content -LiteralPath $stableTaskAuthorityPath -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
    $runtimeRootConfig = Get-Content -LiteralPath $runtimeRootConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
    if ([string]$stableTaskAuthority.schemaVersion -cne 'STABLE_TASK_AUTHORITY_V1' -or
        [int]$stableTaskAuthority.repoArgumentCount -ne 0 -or
        [string]$runtimeRootConfig.schemaVersion -cne 'NEWS_GRASP_RUNTIME_ROOT_V1') {
        throw 'installed authority schema mismatch'
    }
    $installedLauncherPath = Get-CanonicalExistingFile -Path ([string]$stableTaskAuthority.stableLauncherPath) -Label 'installed stable launcher' -Boundary $binPath -MaxBytes 67108864
    $installedLauncherSha256 = (Get-FileHash -LiteralPath $installedLauncherPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($installedLauncherSha256 -cne ([string]$stableTaskAuthority.stableLauncherSha256).ToLowerInvariant()) {
        throw 'installed launcher hash mismatch'
    }
    $runtimeRepoPath = [System.IO.Path]::GetFullPath((Resolve-Path -LiteralPath ([string]$runtimeRootConfig.repoDir) -ErrorAction Stop).Path)
    $expectedRuntimeRepoPath = [System.IO.Path]::GetFullPath((Join-Path $env:USERPROFILE '.news-grasp-runtime\production-runtime'))
    if (-not [string]::Equals($runtimeRepoPath, $expectedRuntimeRepoPath, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'production runtime root mismatch'
    }
    $installedTaskPythonPath = Get-CanonicalExistingFile -Path ([string]$runtimeRootConfig.pythonExe) -Label 'installed launcher Python' -Boundary $workspacePath -MaxBytes 67108864
    $runnerPath = Join-Path $repoPath 'scripts\ops\news-grasp-runner.ps1'
    $codexWrapperPath = Join-Path $repoPath 'scripts\ops\run_codex_with_timeout.ps1'
    $e2eAdmissionBridgePath = Join-Path $runtimeRepoPath 'tools\e2e_final_admission_bridge.py'
} catch {
    throw "INSTALLED_LAUNCHER_IDENTITY_INVALID: $($_.Exception.Message)"
}
if (-not (Test-Path -LiteralPath $runnerPath -PathType Leaf)) {
    throw "installed runner が見つかりません: $runnerPath"
}
if (-not (Test-Path -LiteralPath $codexWrapperPath -PathType Leaf)) {
    throw "installed Codex wrapper が見つかりません: $codexWrapperPath"
}
if (-not (Test-Path -LiteralPath $e2eAdmissionBridgePath -PathType Leaf)) {
    throw "installed E2E final admission consumer が見つかりません: $e2eAdmissionBridgePath"
}
if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw "Python 実行体が見つかりません: $PythonExe"
}
if (-not (Test-Path -LiteralPath $highCostOperationBudgetPath -PathType Leaf)) {
    throw "workspace-global high-cost operation budget consumer が見つかりません: $highCostOperationBudgetPath"
}
if (-not (Test-Path -LiteralPath $highCostModelBrokerPath -PathType Leaf)) {
    throw "installed canonical model broker が見つかりません: $highCostModelBrokerPath"
}
if (-not (Test-Path -LiteralPath (Join-Path $repoPath '.git'))) {
    throw "RepoRoot は git worktree でなければなりません: $repoPath"
}
try {
    $pythonCanonicalPath = Get-CanonicalExistingFile -Path $PythonExe -Label 'authority Python' -Boundary $workspacePath -MaxBytes 67108864
    $powerShellCommand = if (Test-Path -LiteralPath $PowerShellExe -PathType Leaf) {
        Get-Item -LiteralPath $PowerShellExe -ErrorAction Stop
    } else {
        Get-Command $PowerShellExe -CommandType Application -ErrorAction Stop | Select-Object -First 1
    }
    $powerShellCommandPath = if ($powerShellCommand -is [System.IO.FileInfo]) {
        $powerShellCommand.FullName
    } else {
        $powerShellCommand.Source
    }
    $powerShellCanonicalPath = Get-CanonicalExistingFile -Path ([string]$powerShellCommandPath) -Label 'runner executable' -MaxBytes 67108864
    $runnerPath = Get-CanonicalExistingFile -Path $runnerPath -Label 'runner script' -Boundary $repoPath -MaxBytes 67108864
    $codexWrapperPath = Get-CanonicalExistingFile -Path $codexWrapperPath -Label 'Codex wrapper' -Boundary $repoPath -MaxBytes 67108864
    $e2eAdmissionBridgePath = Get-CanonicalExistingFile -Path $e2eAdmissionBridgePath -Label 'E2E admission bridge' -Boundary $runtimeRepoPath -MaxBytes 67108864
    $highCostOperationBudgetPath = Get-CanonicalExistingFile -Path $highCostOperationBudgetPath -Label 'high-cost operation budget' -Boundary $workspacePath -MaxBytes 67108864
    $highCostModelBrokerPath = Get-CanonicalExistingFile -Path $highCostModelBrokerPath -Label 'installed model broker' -MaxBytes 67108864
    $pythonSha256 = (Get-FileHash -LiteralPath $pythonCanonicalPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $powerShellSha256 = (Get-FileHash -LiteralPath $powerShellCanonicalPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $runtimeRepoCommit = (& git -C $runtimeRepoPath rev-parse HEAD 2>$null).Trim().ToLowerInvariant()
    if ($LASTEXITCODE -ne 0) { throw 'runtime commit unavailable' }
    $executionRepoCommit = (& git -C $repoPath rev-parse HEAD 2>$null).Trim().ToLowerInvariant()
    if ($LASTEXITCODE -ne 0) { throw 'execution commit unavailable' }
    $executionTrackedDiff = @(& git -C $repoPath status --porcelain --untracked-files=no 2>$null)
    if ($LASTEXITCODE -ne 0 -or $runtimeRepoCommit -notmatch '^[0-9a-f]{40}$' -or
        $executionRepoCommit -notmatch '^[0-9a-f]{40}$' -or
        $executionRepoCommit -cne $runtimeRepoCommit -or $executionTrackedDiff.Count -ne 0) {
        throw 'execution generation is not the clean active runtime generation'
    }
} catch {
    throw "HIGH_COST_EXECUTABLE_IDENTITY_INVALID: $($_.Exception.Message)"
}

$BudgetPath = Get-CanonicalExistingFile -Path $BudgetPath -Label 'budget evidence' -Boundary $workspacePath -MaxBytes 4194304
$EfficiencyDesignPath = Get-CanonicalExistingFile -Path $EfficiencyDesignPath -Label 'efficiency evidence' -Boundary $workspacePath -MaxBytes 4194304
$AdversarialReviewPath = Get-CanonicalExistingFile -Path $AdversarialReviewPath -Label 'adversarial evidence' -Boundary $workspacePath -MaxBytes 4194304
$RouteManifestPath = Get-CanonicalExistingFile -Path $RouteManifestPath -Label 'route manifest evidence' -Boundary $workspacePath -MaxBytes 4194304
$StaticReceiptPath = Get-CanonicalExistingFile -Path $StaticReceiptPath -Label 'static evidence' -Boundary $workspacePath -MaxBytes 4194304
$SimulationReceiptPath = Get-CanonicalExistingFile -Path $SimulationReceiptPath -Label 'simulation evidence' -Boundary $workspacePath -MaxBytes 4194304
$E2EAdmissionPath = Get-CanonicalExistingFile -Path $E2EAdmissionPath -Label 'issued E2E admission' -Boundary $repoPath -MaxBytes 65536
if (-not $ExternalHealthAuthorityFixturePath) {
    throw 'HIGH_COST_NOPUBLISH_FIXTURE_REQUIRED'
}
$ExternalHealthAuthorityFixturePath = Get-CanonicalExistingFile -Path $ExternalHealthAuthorityFixturePath -Label 'NoPublish external authority fixture' -Boundary $repoPath -MaxBytes 65536
$externalHealthAuthorityFixtureSha256 = (Get-FileHash -LiteralPath $ExternalHealthAuthorityFixturePath -Algorithm SHA256).Hash.ToLowerInvariant()
$authorizationMode = 'new_attempt'
$authorizationCommand = 'authorize'
$authorizationExtraArguments = @('--attempt-kind', $operationKind)
$consumeExtraArguments = @()
if ($CausalReplacementProofPath) {
    $CausalReplacementProofPath = Get-CanonicalExistingFile -Path $CausalReplacementProofPath -Label 'causal replacement proof' -Boundary $workspacePath -MaxBytes 2097152
    $authorizationMode = 'causal_replacement'
    $authorizationCommand = 'authorize-causal-replacement'
    $authorizationExtraArguments = @('--causal-replacement-proof', $CausalReplacementProofPath)
    $consumeExtraArguments = @('--causal-replacement-proof', $CausalReplacementProofPath)
} elseif ($SupersessionApprovalPath) {
    throw 'HIGH_COST_SUPERSESSION_REQUIRES_CAUSAL_REPLACEMENT'
}
if ($SupersessionApprovalPath) {
    $SupersessionApprovalPath = Get-CanonicalExistingFile -Path $SupersessionApprovalPath -Label 'pre-admission supersession approval' -Boundary $workspacePath -MaxBytes 4194304
    try {
        # Supersession approval is bound to this exact issued admission and issue date.
        # An approval for a prior date/generation must never authorize the successor.
        $supersessionApproval = Get-Content -LiteralPath $SupersessionApprovalPath -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
        $issuedAttemptKey = "News-Grasp:${DateStamp}:scheduled-equivalent-nopublish"
        $approvedAttemptKey = [string]$supersessionApproval.canonicalAttemptKey
        $approvedIssueDate = [string]$supersessionApproval.issueDate
        if ([string]::IsNullOrWhiteSpace($issuedAttemptKey) -or
            -not [string]::Equals($approvedAttemptKey, $issuedAttemptKey, [System.StringComparison]::Ordinal) -or
            -not [string]::Equals($approvedIssueDate, $DateStamp, [System.StringComparison]::Ordinal)) {
            throw "attemptKey/issueDate mismatch issued=$issuedAttemptKey approved=$approvedAttemptKey/$approvedIssueDate expectedDate=$DateStamp"
        }
    } catch {
        throw "HIGH_COST_SUPERSESSION_BINDING_INVALID: $($_.Exception.Message)"
    }
}
$statePath = Get-CanonicalFuturePath -Path $statePath -Boundary $repoPath -Label 'state file'
$logPath = Get-CanonicalFutureDirectory -Path $logPath -Boundary $repoPath -Label 'log directory'
$receiptFullPath = Get-CanonicalFuturePath -Path $receiptFullPath -Boundary $repoPath -Label 'final receipt'
$parentAuthorityFullPath = Get-CanonicalFuturePath -Path $parentAuthorityFullPath -Suffix '.high-cost-parent-authority.json' -Boundary $repoPath -Label 'parent authority'
$runnerArgumentsPath = Get-CanonicalFuturePath -Path "$receiptFullPath.runner-arguments.json" -Suffix '.runner-arguments.json' -Boundary $repoPath -Label 'runner arguments'
$reservationReceiptPath = Get-CanonicalFuturePath -Path "$receiptFullPath.e2e-final-reservation.json" -Suffix '.e2e-final-reservation.json' -Boundary $repoPath -Label 'reservation receipt'
$claimReceiptPath = Get-CanonicalFuturePath -Path "$receiptFullPath.e2e-final-claim.json" -Suffix '.e2e-final-claim.json' -Boundary $repoPath -Label 'claim receipt'
$claimWitnessPath = Get-CanonicalFuturePath -Path "$receiptFullPath.e2e-final-claim-witness.json" -Suffix '.e2e-final-claim-witness.json' -Boundary $repoPath -Label 'claim witness'
$installedLaunchAuthorityPath = Get-CanonicalFuturePath -Path "$receiptFullPath.installed-launch-authority.json" -Suffix '.installed-launch-authority.json' -Boundary $repoPath -Label 'installed launch authority'
if ($HighCostParentAuthorityPath -and
    -not [string]::Equals(
        [System.IO.Path]::GetFullPath($HighCostParentAuthorityPath),
        $parentAuthorityFullPath,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
    throw "HIGH_COST_PARENT_AUTHORITY_PATH_DRIFT: expected=$parentAuthorityFullPath actual=$HighCostParentAuthorityPath"
}
New-Item -ItemType Directory -Path (Split-Path -Parent $statePath) -Force | Out-Null
New-Item -ItemType Directory -Path $logPath -Force | Out-Null
New-Item -ItemType Directory -Path (Split-Path -Parent $receiptFullPath) -Force | Out-Null
$logPath = Get-CanonicalFutureDirectory -Path $logPath -Boundary $repoPath -Label 'log directory' -AllowExisting
$statePath = Get-CanonicalFuturePath -Path $statePath -Boundary $repoPath -Label 'state file'
$receiptFullPath = Get-CanonicalFuturePath -Path $receiptFullPath -Boundary $repoPath -Label 'final receipt'
$parentAuthorityFullPath = Get-CanonicalFuturePath -Path $parentAuthorityFullPath -Suffix '.high-cost-parent-authority.json' -Boundary $repoPath -Label 'parent authority'
$runnerArgumentsPath = Get-CanonicalFuturePath -Path $runnerArgumentsPath -Suffix '.runner-arguments.json' -Boundary $repoPath -Label 'runner arguments'
$reservationReceiptPath = Get-CanonicalFuturePath -Path $reservationReceiptPath -Suffix '.e2e-final-reservation.json' -Boundary $repoPath -Label 'reservation receipt'
$claimReceiptPath = Get-CanonicalFuturePath -Path $claimReceiptPath -Suffix '.e2e-final-claim.json' -Boundary $repoPath -Label 'claim receipt'
$claimWitnessPath = Get-CanonicalFuturePath -Path $claimWitnessPath -Suffix '.e2e-final-claim-witness.json' -Boundary $repoPath -Label 'claim witness'
$installedLaunchAuthorityPath = Get-CanonicalFuturePath -Path $installedLaunchAuthorityPath -Suffix '.installed-launch-authority.json' -Boundary $repoPath -Label 'installed launch authority'
$runnerArguments = @(
    '-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden', '-ExecutionPolicy', 'Bypass',
    '-File', $runnerPath,
    '-NoPublish',
    '-DateStampOverride', $DateStamp,
    '-RepoDirOverride', $repoPath,
    '-CodexWrapperOverride', $codexWrapperPath,
    '-StateFileOverride', $statePath,
    '-LogDirOverride', $logPath,
    '-PyExeOverride', $pythonCanonicalPath,
    '-PowerShellExe', $powerShellCanonicalPath,
    '-HighCostWorkspaceRoot', $workspacePath,
    '-HighCostBudgetToolPath', $highCostModelBrokerPath,
    '-HighCostParentAuthorityPath', $parentAuthorityFullPath,
    '-E2EFinalAdmissionPath', $E2EAdmissionPath,
    '-E2EFinalRunnerArgumentsPath', $runnerArgumentsPath,
    '-E2EFinalReservationReceiptPath', $reservationReceiptPath,
    '-E2EFinalClaimReceiptPath', $claimReceiptPath,
    '-ExternalHealthAuthorityPathOverride', $ExternalHealthAuthorityFixturePath,
    '-ExternalHealthAuthorityExpectedSha256', $externalHealthAuthorityFixtureSha256,
    '-HighCostAttemptId', $attemptId
)
if (Test-Path -LiteralPath $runnerArgumentsPath) {
    throw "HIGH_COST_RUNNER_ARGUMENTS_OUTPUT_EXISTS: $runnerArgumentsPath"
}
$runnerArgumentsJson = $runnerArguments | ConvertTo-Json -Compress -Depth 10
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$runnerArgumentsBytes = $utf8NoBom.GetBytes($runnerArgumentsJson + "`n")
$runnerArgumentsStream = $null
try {
    $runnerArgumentsStream = [System.IO.File]::Open(
        $runnerArgumentsPath,
        [System.IO.FileMode]::CreateNew,
        [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::None
    )
    $runnerArgumentsStream.Write($runnerArgumentsBytes, 0, $runnerArgumentsBytes.Length)
    $runnerArgumentsStream.Flush()
} finally {
    if ($runnerArgumentsStream) { $runnerArgumentsStream.Dispose() }
}
$installedLaunchAuthority = [ordered]@{
    schemaVersion = 'NEWS_GRASP_INSTALLED_NOPUBLISH_LAUNCH_AUTHORITY_V1'
    issueDate = $DateStamp
    attemptId = $attemptId
    stableLauncherPath = $installedLauncherPath
    stableLauncherSha256 = $installedLauncherSha256
    stableTaskAuthorityPath = $stableTaskAuthorityPath
    stableTaskAuthorityFileSha256 = (Get-FileHash -LiteralPath $stableTaskAuthorityPath -Algorithm SHA256).Hash.ToLowerInvariant()
    runnerExecutablePath = $powerShellCanonicalPath
    runnerExecutableSha256 = $powerShellSha256
    executionRepoRoot = $repoPath
    executionRepoCommit = $executionRepoCommit
    runtimeRepoCommit = $runtimeRepoCommit
    runnerArgumentsPath = $runnerArgumentsPath
    runnerArgumentsFileSha256 = (Get-FileHash -LiteralPath $runnerArgumentsPath -Algorithm SHA256).Hash.ToLowerInvariant()
    externalHealthAuthorityFixturePath = $ExternalHealthAuthorityFixturePath
    externalHealthAuthorityFixtureSha256 = $externalHealthAuthorityFixtureSha256
}
$installedLaunchAuthorityBody = $installedLaunchAuthority | ConvertTo-Json -Depth 6 -Compress
$installedLaunchAuthorityHasher = [Security.Cryptography.SHA256]::Create()
try {
    $installedLaunchAuthorityBytes = [Text.Encoding]::UTF8.GetBytes($installedLaunchAuthorityBody)
    $installedLaunchAuthority.authoritySha256 = ([BitConverter]::ToString($installedLaunchAuthorityHasher.ComputeHash($installedLaunchAuthorityBytes)) -replace '-', '').ToLowerInvariant()
} finally { $installedLaunchAuthorityHasher.Dispose() }
$installedLaunchAuthorityJson = $installedLaunchAuthority | ConvertTo-Json -Depth 6
$installedLaunchAuthorityOutputBytes = $utf8NoBom.GetBytes($installedLaunchAuthorityJson + "`n")
$installedLaunchAuthorityStream = $null
try {
    $installedLaunchAuthorityStream = [System.IO.File]::Open(
        $installedLaunchAuthorityPath,
        [System.IO.FileMode]::CreateNew,
        [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::None
    )
    $installedLaunchAuthorityStream.Write($installedLaunchAuthorityOutputBytes, 0, $installedLaunchAuthorityOutputBytes.Length)
    $installedLaunchAuthorityStream.Flush()
} finally {
    if ($installedLaunchAuthorityStream) { $installedLaunchAuthorityStream.Dispose() }
}
$e2eAdmissionValidation = & $pythonCanonicalPath -I $e2eAdmissionBridgePath 'validate-issued' `
    '--admission' $E2EAdmissionPath `
    '--runner-arguments-file' $runnerArgumentsPath `
    '--parent-authority' $parentAuthorityFullPath `
    '--reservation-output' $reservationReceiptPath `
    '--claim-output' $claimReceiptPath `
    '--claim-witness-output' $claimWitnessPath `
    '--runner-executable' $powerShellCanonicalPath `
    '--authority-python-executable' $pythonCanonicalPath
if ($LASTEXITCODE -ne 0) {
    throw "E2E_FINAL_ISSUED_ADMISSION_REJECTED exit=$LASTEXITCODE"
}
$authorizeOutput = & $pythonCanonicalPath -I $highCostOperationBudgetPath $authorizationCommand `
    '--workspace-root' $workspacePath `
    '--budget' $BudgetPath `
    '--efficiency-design' $EfficiencyDesignPath `
    '--adversarial-review' $AdversarialReviewPath `
    '--route-manifest' $RouteManifestPath `
    '--static-receipt' $StaticReceiptPath `
    '--simulation-receipt' $SimulationReceiptPath `
    '--e2e-admission' $E2EAdmissionPath `
    '--execution-root' $repoPath `
    '--output' $parentAuthorityFullPath @authorizationExtraArguments @supersessionArguments
if ($LASTEXITCODE -ne 0) {
    throw "HIGH_COST_OPERATION_AUTHORIZATION_REJECTED exit=$LASTEXITCODE"
}
$activateOutput = & $pythonCanonicalPath -I $highCostOperationBudgetPath 'activate' `
    '--workspace-root' $workspacePath `
    '--admission' $parentAuthorityFullPath
if ($LASTEXITCODE -ne 0) {
    throw "HIGH_COST_OPERATION_ACTIVATION_REJECTED exit=$LASTEXITCODE"
}
$validatedOutput = & $pythonCanonicalPath -I $highCostOperationBudgetPath 'validate-activated' `
    '--workspace-root' $workspacePath `
    '--admission' $parentAuthorityFullPath `
    '--expected-attempt-kind' $operationKind `
    '--expected-execution-root' $repoPath
if ($LASTEXITCODE -ne 0) {
    throw "HIGH_COST_OPERATION_VALIDATION_REJECTED exit=$LASTEXITCODE"
}
& $pythonCanonicalPath -I $e2eAdmissionBridgePath 'consume' `
    '--admission' $E2EAdmissionPath `
    '--runner-arguments-file' $runnerArgumentsPath `
    '--parent-authority' $parentAuthorityFullPath `
    '--reservation-output' $reservationReceiptPath `
    '--runner-executable' $powerShellCanonicalPath `
    '--authority-python-executable' $pythonCanonicalPath @consumeExtraArguments
if ($LASTEXITCODE -ne 0) {
    throw "E2E_FINAL_ADMISSION_REJECTED exit=$LASTEXITCODE"
}

$startedAt = Get-Date
$launchPowerShellSha256 = (Get-FileHash -LiteralPath $powerShellCanonicalPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($launchPowerShellSha256 -ne $powerShellSha256) {
    throw "HIGH_COST_POWERSHELL_EXECUTABLE_DRIFT: $powerShellCanonicalPath"
}
$installedLauncherArguments = @(
    $installedLauncherPath,
    'scheduled-equivalent-nopublish',
    '--launch-authority',
    $installedLaunchAuthorityPath
)
& $installedTaskPythonPath @installedLauncherArguments
$runnerExitCode = $LASTEXITCODE

$state = $null
if (Test-Path -LiteralPath $statePath -PathType Leaf) {
    try {
        $state = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        $state = $null
    }
}
$observedStatus = if ($state) { [string] $state.status } else { '' }
$finishedAt = Get-Date
$elapsedSeconds = [int]($finishedAt - $startedAt).TotalSeconds
$durationSloLimitSeconds = 3600
$durationSloMet = $elapsedSeconds -le $durationSloLimitSeconds
$receipt = [ordered]@{
    schema = 'NEWS_GRASP_SCHEDULED_EQUIVALENT_NOPUBLISH_E2E_V1'
    scheduled_entrypoint_mode = 'installed_stable_launcher'
    authorization_mode = $authorizationMode
    expected_terminal_state = 'publish_dry_run_ok'
    no_publish = $true
    no_push = $true
    no_auto_open = $true
    no_focus_theft = $true
    date = $DateStamp
    repo_root = $runtimeRepoPath
    runner_path = $runnerPath
    installed_launcher_path = $installedLauncherPath
    installed_launcher_sha256 = $installedLauncherSha256
    installed_launch_authority_path = $installedLaunchAuthorityPath
    state_file = $statePath
    log_dir = $logPath
    started_at = $startedAt.ToString('o')
    finished_at = $finishedAt.ToString('o')
    elapsed_seconds = $elapsedSeconds
    duration_slo_limit_seconds = $durationSloLimitSeconds
    duration_slo_met = $durationSloMet
    runner_exit_code = $runnerExitCode
    observed_terminal_state = $observedStatus
    high_cost_attempt_id = $attemptId
    high_cost_parent_authority_path = $parentAuthorityFullPath
    external_health_authority_fixture_path = $ExternalHealthAuthorityFixturePath
    external_health_authority_fixture_sha256 = $externalHealthAuthorityFixtureSha256
    high_cost_parent_authority_sha256 = if (Test-Path -LiteralPath $parentAuthorityFullPath -PathType Leaf) { (Get-FileHash -LiteralPath $parentAuthorityFullPath -Algorithm SHA256).Hash.ToLowerInvariant() } else { '' }
    ok = ($runnerExitCode -eq 0 -and $observedStatus -eq 'publish_dry_run_ok' -and $durationSloMet)
}
$json = $receipt | ConvertTo-Json -Depth 6
[System.IO.File]::WriteAllText($receiptFullPath, ($json + [Environment]::NewLine), $utf8NoBom)

if (-not $receipt.ok) {
    Write-Error "scheduled-equivalent NoPublish E2E failed: exit=$runnerExitCode state=$observedStatus receipt=$receiptFullPath"
    exit 1
}
Write-Output $json
exit 0
