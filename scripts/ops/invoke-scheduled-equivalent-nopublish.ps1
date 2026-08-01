# 本番 Scheduled Task と同じ news-grasp-runner.ps1 を、公開副作用なしで最終確認する。
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
    [string] $HighCostAdmissionPath = '',
    [string] $PowerShellExe = 'powershell.exe'
)

$ErrorActionPreference = 'Stop'
$repoPath = [System.IO.Path]::GetFullPath($RepoRoot)
$statePath = [System.IO.Path]::GetFullPath($StateFile)
$logPath = [System.IO.Path]::GetFullPath($LogDir)
$receiptFullPath = [System.IO.Path]::GetFullPath($ReceiptPath)
$workspacePath = [System.IO.Path]::GetFullPath($WorkspaceRoot)
$highCostBudgetToolPath = Join-Path $env:USERPROFILE 'bin\ai-model-spawn-broker.py'
if (-not $HighCostAdmissionPath) {
    $HighCostAdmissionPath = "$receiptFullPath.high-cost-admission.json"
}
$highCostAdmissionFullPath = [System.IO.Path]::GetFullPath($HighCostAdmissionPath)
$repoPrefix = $repoPath.TrimEnd('\') + '\'
foreach ($candidate in @($statePath, $logPath, $receiptFullPath)) {
    if (-not $candidate.StartsWith($repoPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "隔離 state/log/receipt は RepoRoot 配下でなければなりません: $candidate"
    }
}

$runnerPath = Join-Path $repoPath 'scripts\ops\news-grasp-runner.ps1'
$codexWrapperPath = Join-Path $repoPath 'scripts\ops\run_codex_with_timeout.ps1'
$e2eAdmissionBridgePath = Join-Path $repoPath 'tools\e2e_final_admission_bridge.py'
if (-not (Test-Path -LiteralPath $runnerPath -PathType Leaf)) {
    throw "runner が見つかりません: $runnerPath"
}
if (-not (Test-Path -LiteralPath $codexWrapperPath -PathType Leaf)) {
    throw "repo-managed Codex wrapper が見つかりません: $codexWrapperPath"
}
if (-not (Test-Path -LiteralPath $e2eAdmissionBridgePath -PathType Leaf)) {
    throw "E2E final admission consumer が見つかりません: $e2eAdmissionBridgePath"
}
if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw "Python 実行体が見つかりません: $PythonExe"
}
if (-not (Test-Path -LiteralPath $highCostBudgetToolPath -PathType Leaf)) {
    throw "workspace-global high-cost budget consumer が見つかりません: $highCostBudgetToolPath"
}
if (-not (Test-Path -LiteralPath (Join-Path $repoPath '.git'))) {
    throw "RepoRoot は git worktree でなければなりません: $repoPath"
}

New-Item -ItemType Directory -Path (Split-Path -Parent $statePath) -Force | Out-Null
New-Item -ItemType Directory -Path $logPath -Force | Out-Null
New-Item -ItemType Directory -Path (Split-Path -Parent $receiptFullPath) -Force | Out-Null

foreach ($evidencePath in @($BudgetPath, $EfficiencyDesignPath, $AdversarialReviewPath, $RouteManifestPath, $StaticReceiptPath, $SimulationReceiptPath, $E2EAdmissionPath)) {
    if (-not (Test-Path -LiteralPath $evidencePath -PathType Leaf)) {
        throw "HIGH_COST_TRUSTED_EVIDENCE_REQUIRED missing=$([IO.Path]::GetFileName($evidencePath))"
    }
}
$runnerArguments = @(
    '-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden', '-ExecutionPolicy', 'Bypass',
    '-File', $runnerPath,
    '-NoPublish',
    '-DateStampOverride', $DateStamp,
    '-RepoDirOverride', $repoPath,
    '-CodexWrapperOverride', $codexWrapperPath,
    '-StateFileOverride', $statePath,
    '-LogDirOverride', $logPath,
    '-PyExeOverride', $PythonExe,
    '-HighCostWorkspaceRoot', $workspacePath,
    '-HighCostBudgetToolPath', $highCostBudgetToolPath
)
$runnerArgumentsPath = "$receiptFullPath.runner-arguments.json"
$runnerArgumentsJson = $runnerArguments | ConvertTo-Json
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($runnerArgumentsPath, ($runnerArgumentsJson + [Environment]::NewLine), $utf8NoBom)
$operationKind = 'full_e2e'
$attemptId = "nopublish:$DateStamp"
& $PythonExe $highCostBudgetToolPath 'admit' '--operation-kind' $operationKind '--attempt-id' $attemptId
if ($LASTEXITCODE -ne 0) {
    throw "HIGH_COST_OPERATION_ADMISSION_REJECTED exit=$LASTEXITCODE"
}
& $PythonExe $e2eAdmissionBridgePath 'consume' '--admission' $E2EAdmissionPath '--runner-arguments-file' $runnerArgumentsPath
if ($LASTEXITCODE -ne 0) {
    throw "E2E_FINAL_ADMISSION_REJECTED exit=$LASTEXITCODE"
}

$startedAt = Get-Date
& $PowerShellExe @runnerArguments
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
    scheduled_entrypoint_mode = 'same_runner_script'
    expected_terminal_state = 'publish_dry_run_ok'
    no_publish = $true
    no_push = $true
    no_auto_open = $true
    no_focus_theft = $true
    date = $DateStamp
    repo_root = $repoPath
    runner_path = $runnerPath
    state_file = $statePath
    log_dir = $logPath
    started_at = $startedAt.ToString('o')
    finished_at = $finishedAt.ToString('o')
    elapsed_seconds = $elapsedSeconds
    duration_slo_limit_seconds = $durationSloLimitSeconds
    duration_slo_met = $durationSloMet
    runner_exit_code = $runnerExitCode
    observed_terminal_state = $observedStatus
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
