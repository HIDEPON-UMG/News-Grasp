# Release gate専用。旧runnerを復活させず、direct NoPublish moduleだけを起動する。
[CmdletBinding()]
param(
    [switch] $NoPublish,
    [Parameter(Mandatory=$true)][string] $DateStampOverride,
    [Parameter(Mandatory=$true)][string] $RepoDirOverride,
    [Parameter(Mandatory=$true)][string] $StateFileOverride,
    [Parameter(Mandatory=$true)][string] $LogDirOverride,
    [Parameter(Mandatory=$true)][string] $PyExeOverride,
    [Parameter(Mandatory=$true)][string] $HighCostParentAuthorityPath,
    [Parameter(Mandatory=$true)][string] $E2EFinalAdmissionPath,
    [Parameter(Mandatory=$true)][string] $E2EFinalRunnerArgumentsPath,
    [Parameter(Mandatory=$true)][string] $E2EFinalReservationReceiptPath,
    [Parameter(Mandatory=$true)][string] $E2EFinalClaimReceiptPath,
    [Parameter(Mandatory=$true)][string] $E2EAttemptPolicyPath,
    [ValidateRange(1,2)][int] $E2ELogicalAttempt,
    [Parameter(Mandatory=$true)][string] $HighCostAttemptId,
    [Parameter(Mandatory=$true)][string] $ExternalHealthAuthorityPathOverride,
    [Parameter(Mandatory=$true)][string] $ExternalHealthAuthorityExpectedSha256,
    [Parameter(Mandatory=$true)][string] $IsolationReceiptPath,
    [Parameter(Mandatory=$true)][string] $LaunchEvidencePath,
    [string] $CodexWrapperOverride = '',
    [string] $PowerShellExe = '',
    [string] $HighCostBindingPath = '',
    [string] $HighCostBindingReceiptSha256 = '',
    [string] $GlobalHarnessGenerationManifestPath = ''
)

$ErrorActionPreference = 'Stop'
if (-not $NoPublish) { throw 'NEWS_GRASP_RELEASE_NOPUBLISH_FLAG_REQUIRED' }
$runnerExecutable = Join-Path $PSHOME 'powershell.exe'
$repo = [IO.Path]::GetFullPath((Resolve-Path -LiteralPath $RepoDirOverride -ErrorAction Stop).Path)
$python = [IO.Path]::GetFullPath((Resolve-Path -LiteralPath $PyExeOverride -ErrorAction Stop).Path)
$launchEvidence = [IO.Path]::GetFullPath($LaunchEvidencePath)
$launchEvidenceParent = [IO.Path]::GetDirectoryName($launchEvidence)
if (-not (Test-Path -LiteralPath $launchEvidenceParent -PathType Container) -or
    -not [IO.Path]::GetFullPath($launchEvidenceParent).StartsWith($repo + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -or
    (Test-Path -LiteralPath $launchEvidence)) {
    throw 'NEWS_GRASP_RELEASE_NOPUBLISH_LAUNCH_EVIDENCE_INVALID'
}
$bridge = Join-Path $repo 'tools\e2e_final_admission_bridge.py'
$module = Join-Path $repo 'tools\news_grasp_release_nopublish.py'
foreach ($path in @(
    $python, $bridge, $module, $HighCostParentAuthorityPath, $E2EFinalAdmissionPath,
    $E2EFinalRunnerArgumentsPath, $E2EFinalReservationReceiptPath,
    $ExternalHealthAuthorityPathOverride, $IsolationReceiptPath
)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "NEWS_GRASP_RELEASE_NOPUBLISH_INPUT_MISSING:$path"
    }
}
if ((Get-FileHash -LiteralPath $ExternalHealthAuthorityPathOverride -Algorithm SHA256).Hash.ToLowerInvariant() -cne $ExternalHealthAuthorityExpectedSha256.ToLowerInvariant()) {
    throw 'NEWS_GRASP_RELEASE_NOPUBLISH_EXTERNAL_FIXTURE_DRIFT'
}
if (Test-Path -LiteralPath $E2EFinalClaimReceiptPath) {
    throw 'NEWS_GRASP_RELEASE_NOPUBLISH_CLAIM_EXISTS'
}
$claimNonceSource = "$HighCostAttemptId|$PID|release-nopublish"
$hasher = [Security.Cryptography.SHA256]::Create()
try {
    $claimNonce = ([BitConverter]::ToString($hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($claimNonceSource))) -replace '-', '').ToLowerInvariant()
} finally { $hasher.Dispose() }
& $python -I $bridge 'claim-runner' `
    '--admission' $E2EFinalAdmissionPath `
    '--runner-arguments-file' $E2EFinalRunnerArgumentsPath `
    '--parent-authority' $HighCostParentAuthorityPath `
    '--reservation-receipt' $E2EFinalReservationReceiptPath `
    '--claim-output' $E2EFinalClaimReceiptPath `
    '--runner-executable' $runnerExecutable `
    '--authority-python-executable' $python `
    '--current-runner-pid' ([string]$PID) `
    '--claim-nonce' $claimNonce
$claimExitCode = $LASTEXITCODE
if ($claimExitCode -ne 0) {
    $failureCode = 'NEWS_GRASP_RELEASE_NOPUBLISH_CLAIM_REJECTED'
    $failureHasher = [Security.Cryptography.SHA256]::Create()
    try {
        $failureFingerprint = ([BitConverter]::ToString($failureHasher.ComputeHash(
            [Text.Encoding]::UTF8.GetBytes("$failureCode`0$claimExitCode`0$claimNonce")
        )) -replace '-', '').ToLowerInvariant()
    } finally { $failureHasher.Dispose() }
    & $python -I $bridge 'record-claim-failure' `
        '--admission' $E2EFinalAdmissionPath `
        '--reservation-receipt' $E2EFinalReservationReceiptPath `
        '--failure-code' $failureCode `
        '--failure-fingerprint' $failureFingerprint `
        '--runner-executable' $runnerExecutable `
        '--authority-python-executable' $python `
        '--current-runner-pid' ([string]$PID)
    $failureRecordExitCode = $LASTEXITCODE
    if ($failureRecordExitCode -ne 0) {
        throw "$failureCode`:exit=$claimExitCode`nNEWS_GRASP_RELEASE_NOPUBLISH_CLAIM_FAILURE_RECORD_REJECTED"
    }
    throw "$failureCode`:exit=$claimExitCode"
}
$admission = Get-Content -LiteralPath $E2EFinalAdmissionPath -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
$claimWitness = [IO.Path]::GetFullPath([string]$admission.expectedClaimWitnessPath)
& $python -I $bridge 'write-runner-claim-witness' `
    '--admission' $E2EFinalAdmissionPath `
    '--runner-arguments-file' $E2EFinalRunnerArgumentsPath `
    '--parent-authority' $HighCostParentAuthorityPath `
    '--reservation-receipt' $E2EFinalReservationReceiptPath `
    '--claim-receipt' $E2EFinalClaimReceiptPath `
    '--witness-output' $claimWitness `
    '--runner-executable' $runnerExecutable `
    '--authority-python-executable' $python `
    '--expected-owner-pid' ([string]$PID)
if ($LASTEXITCODE -ne 0) { throw "NEWS_GRASP_RELEASE_NOPUBLISH_WITNESS_REJECTED:exit=$LASTEXITCODE" }

$state = [IO.Path]::GetFullPath($StateFileOverride)
$logDir = [IO.Path]::GetFullPath($LogDirOverride)
$receipt = Join-Path $logDir 'direct-release-nopublish-receipt.json'
$stateRoot = Join-Path $logDir 'direct-state'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
$env:PYTHONNOUSERSITE = '1'
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONSTARTUP -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONINSPECT -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONUSERBASE -ErrorAction SilentlyContinue
$env:NEWS_GRASP_E2E_ADMISSION_PATH = [IO.Path]::GetFullPath($E2EFinalAdmissionPath)
$env:NEWS_GRASP_E2E_ARGUMENTS_PATH = [IO.Path]::GetFullPath($E2EFinalRunnerArgumentsPath)
$env:NEWS_GRASP_E2E_CLAIM_PATH = [IO.Path]::GetFullPath($E2EFinalClaimReceiptPath)
$env:NEWS_GRASP_E2E_RESERVATION_PATH = [IO.Path]::GetFullPath($E2EFinalReservationReceiptPath)
$env:NEWS_GRASP_E2E_PARENT_AUTHORITY_PATH = [IO.Path]::GetFullPath($HighCostParentAuthorityPath)
$moduleArguments = @(
    '-I', '-S', '-B', $module,
    '--repo-root', $repo,
    '--source-issue-date', $DateStampOverride,
    '--state-root', $stateRoot,
    '--state-file', $state,
    '--receipt-path', $receipt,
    '--isolation-receipt', $IsolationReceiptPath
)
Push-Location -LiteralPath $repo
try {
    & $python @moduleArguments
    $childExit = $LASTEXITCODE
} finally {
    Pop-Location
}

$currentScript = [IO.Path]::GetFullPath($PSCommandPath)
$commandIdentitySource = "$python`0$([string]::Join([char]0, $moduleArguments))`0$repo"
$commandHasher = [Security.Cryptography.SHA256]::Create()
try {
    $commandIdentitySha256 = ([BitConverter]::ToString($commandHasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($commandIdentitySource))) -replace '-', '').ToLowerInvariant()
} finally { $commandHasher.Dispose() }
$evidence = [ordered]@{
    schemaVersion = 'NEWS_GRASP_RUNNER_LAUNCH_EVIDENCE_V1'
    status = if ($childExit -eq 0) { 'terminal_state_reached' } else { 'failed_after_state_claim' }
    reasonCode = if ($childExit -eq 0) { 'NEWS_GRASP_RELEASE_NOPUBLISH_TERMINAL' } else { 'NEWS_GRASP_RELEASE_NOPUBLISH_CHILD_FAILED' }
    issueDate = $DateStampOverride
    processId = [int]$PID
    childExitCode = [int]$childExit
    stateClaimed = $true
    commandIdentitySha256 = $commandIdentitySha256
    powershellPath = $runnerExecutable
    powershellSha256 = (Get-FileHash -LiteralPath $runnerExecutable -Algorithm SHA256).Hash.ToLowerInvariant()
    runnerPath = $currentScript
    runnerSha256 = (Get-FileHash -LiteralPath $currentScript -Algorithm SHA256).Hash.ToLowerInvariant()
    workingDirectory = $repo
    stateFile = $state
    logDir = $logDir
    observedAt = (Get-Date).ToUniversalTime().ToString('o')
}
$evidenceTemporary = "$launchEvidence.$([Guid]::NewGuid().ToString('N')).tmp"
[IO.File]::WriteAllText($evidenceTemporary, (($evidence | ConvertTo-Json -Depth 5) + [Environment]::NewLine), [Text.UTF8Encoding]::new($false))
try {
    [IO.File]::Move($evidenceTemporary, $launchEvidence)
} finally {
    if (Test-Path -LiteralPath $evidenceTemporary) { Remove-Item -LiteralPath $evidenceTemporary -Force }
}
exit $childExit
