param(
    [switch] $Start,
    [switch] $StartOnly,
    [switch] $SmokeTest,
    [switch] $SkipSourceSync,
    [switch] $UseProductionRuntime,
    [switch] $LegacyDirectEntrypoint,
    [switch] $RecoverOnly,
    [int] $PollSeconds = 30,
    [int] $StaleMinutes = 15,
    [int] $TimeoutMinutes = 120,
    [string] $RunnerPath = '',
    [string] $StateFile = '',
    [string] $LogDir = '',
    [string] $DateStamp = '',
    [string] $RepoDir = '',
    [string] $PythonExe = '',
    [string] $EvidenceRepoDir = '',
    [string] $BinDir = (Join-Path $env:USERPROFILE 'bin'),
    [string] $ScheduledTaskName = '',
    [string] $ProductionTaskName = 'News-Grasp Production'
)

$ErrorActionPreference = 'Stop'
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$SCHEDULED_TASK_CONTEXT_REJECTED_EXIT = 67

function Write-AtomicUtf8Text {
    param([string] $Path, [string] $Text)
    $parent = Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    $temporary = Join-Path $parent ('.' + [IO.Path]::GetFileName($Path) + '.' + [Guid]::NewGuid().ToString('N') + '.tmp')
    $replacementBackup = $temporary + '.replace-backup'
    try {
        [IO.File]::WriteAllText($temporary, $Text, [Text.UTF8Encoding]::new($false))
        if (Test-Path -LiteralPath $Path -PathType Leaf) {
            [IO.File]::Replace($temporary, $Path, $replacementBackup, $true)
        } else {
            [IO.File]::Move($temporary, $Path)
        }
    } finally {
        if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force }
        if (Test-Path -LiteralPath $replacementBackup) { Remove-Item -LiteralPath $replacementBackup -Force }
    }
}

function Write-BootstrapFailureObservation {
    param(
        [Parameter(Mandatory=$true)][string] $Phase,
        [Parameter(Mandatory=$true)][string] $ReasonCode,
        [Parameter(Mandatory=$true)][string] $Detail
    )
    $boundedDetail = (($Detail -replace '[\r\n]+', ' ').Trim())
    if ($boundedDetail.Length -gt 2048) {
        $boundedDetail = $boundedDetail.Substring(0, 2048)
    }
    $effectiveReasonCode = $ReasonCode
    if ($boundedDetail -match '"reasonCode"\s*:\s*"([A-Z][A-Z0-9_]+)"') {
        $effectiveReasonCode = [string]$Matches[1]
    }
    $stateCandidate = if ($StateFile) {
        if ([System.IO.Path]::IsPathRooted($StateFile)) { $StateFile } else { Join-Path $BinDir $StateFile }
    } elseif ($SmokeTest) {
        Join-Path $BinDir 'ng-smoke-state.json'
    } else {
        Join-Path $BinDir 'news-grasp-runner-state.json'
    }
    $logRoot = if ($LogDir) {
        if ([System.IO.Path]::IsPathRooted($LogDir)) { $LogDir } else { Join-Path $BinDir $LogDir }
    } elseif ($SmokeTest) {
        Join-Path $BinDir 'ng-smoke-logs'
    } else {
        Join-Path $BinDir 'news-grasp-logs'
    }
    $now = (Get-Date).ToString('yyyy-MM-ddTHH:mm:ss.fffK')
    $causeInput = "$Phase|$effectiveReasonCode"
    $payload = [ordered]@{
        schemaVersion = 'NEWS_GRASP_BOOTSTRAP_FAILURE_OBSERVATION_V1'
        status = 'blocked_startup_self_repair_failed'
        message = "STARTUP_SELF_REPAIR_FAILED reasonCode=$effectiveReasonCode"
        exit_code = 72
        updated_at = $now
        heartbeat_at = $now
        date = $DateStamp
        run_intent = 'ScheduledProduction'
        run_id = "bootstrap-$([Guid]::NewGuid().ToString('N'))"
        phase = $Phase
        reasonCode = $effectiveReasonCode
        failureClass = $ReasonCode
        failureDetail = $boundedDetail
        causeFingerprint = Get-StringSha256Hex -Text $causeInput
        causalRetryState = 'cause_change_required'
        attempt_terminal = $true
        recovery_class = 'startup_self_repair_failure'
        scheduled_attempt_status = 'failed'
        recovery_attempt_status = 'not_started'
    }
    Write-AtomicUtf8Text -Path $stateCandidate -Text (($payload | ConvertTo-Json -Depth 6) + [Environment]::NewLine)
    New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
    [System.IO.File]::AppendAllText(
        (Join-Path $logRoot "bootstrap-$DateStamp.log"),
        "[$now] ERROR: phase=$Phase reasonCode=$effectiveReasonCode failureClass=$ReasonCode detail=$boundedDetail" + [Environment]::NewLine,
        [System.Text.UTF8Encoding]::new($false)
    )
}

function Resolve-NewsGraspRepoDir {
    param([string] $Override)
    if ($Override) {
        return (Resolve-Path -LiteralPath $Override).Path
    }
    if ($env:NEWS_GRASP_REPO_DIR) {
        return (Resolve-Path -LiteralPath $env:NEWS_GRASP_REPO_DIR).Path
    }
    $candidates = @(
        (Join-Path $env:USERPROFILE 'OneDrive\ドキュメント\ProjectFolders\News-Grasp'),
        (Join-Path $env:USERPROFILE "Obsidian\New's Grasp\News-Grasp")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath (Join-Path $candidate 'tools\daily_self_heal.py')) {
            return $candidate
        }
    }
    throw 'News-Grasp repo not found. Set NEWS_GRASP_REPO_DIR or pass -RepoDir.'
}

function Resolve-ProductionRuntimeRepo {
    param(
        [string] $SourceRepoDir,
        [string] $BootstrapOwnerReceiptPath,
        [string] $BootstrapOwnerNonce
    )

    $gitExe = 'C:\Program Files\Git\cmd\git.exe'
    if (-not (Test-Path -LiteralPath $gitExe -PathType Leaf)) {
        throw 'PRODUCTION_RUNTIME_GIT_MISSING'
    }
    $fetchExit = Invoke-BoundedGitFetch -GitExe $gitExe -WorkingDirectory $SourceRepoDir
    if ($fetchExit -ne 0) {
        throw "PRODUCTION_RUNTIME_FETCH_FAILED exit=$fetchExit"
    }
    $originSha = ((& $gitExe -C $SourceRepoDir rev-parse origin/main 2>$null) | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $originSha -notmatch '^[0-9a-fA-F]{40}$') {
        throw 'PRODUCTION_RUNTIME_ORIGIN_SHA_INVALID'
    }
    $runtimeRoot = Join-Path $env:USERPROFILE '.news-grasp-runtime'
    New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null
    # runtime convergence自体も正規installerが配置したstable launcherだけを入口にする。
    $runtimeHelper = Join-Path $env:USERPROFILE 'bin\news-grasp-task-launcher.pyw'
    if (-not (Test-Path -LiteralPath $runtimeHelper -PathType Leaf)) {
        throw 'PRODUCTION_RUNTIME_HELPER_MISSING'
    }
    $convergenceJson = (& $PythonExe $runtimeHelper 'converge-runtime' '--source-repo' $SourceRepoDir '--origin-sha' $originSha '--bootstrap-owner-pid' ([string]$PID) '--bootstrap-owner-receipt' $BootstrapOwnerReceiptPath '--bootstrap-owner-nonce' $BootstrapOwnerNonce 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "PRODUCTION_RUNTIME_CONVERGENCE_FAILED exit=$LASTEXITCODE detail=$convergenceJson"
    }
    try {
        $convergence = $convergenceJson | ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw 'PRODUCTION_RUNTIME_CONVERGENCE_RESULT_INVALID'
    }
    if ([string]$convergence.phase -ne 'committed') {
        throw 'PRODUCTION_RUNTIME_CONVERGENCE_INCOMPLETE'
    }
    $runtimeRepo = (Resolve-Path -LiteralPath ([string]$convergence.runtimePath)).Path
    $runtimeSha = ((& $gitExe -C $runtimeRepo rev-parse HEAD 2>$null) | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $runtimeSha -ne $originSha) {
        throw 'PRODUCTION_RUNTIME_HEAD_DRIFT'
    }
    return $runtimeRepo
}

function Get-FileSha256Hex {
    param([string] $Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return ''
    }
    $stream = [System.IO.File]::OpenRead($Path)
    try {
        $sha = [System.Security.Cryptography.SHA256]::Create()
        try {
            return ([System.BitConverter]::ToString($sha.ComputeHash($stream)) -replace '-', '').ToLowerInvariant()
        } finally {
            $sha.Dispose()
        }
    } finally {
        $stream.Dispose()
    }
}

function Get-StringSha256Hex {
    param([string] $Text)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([System.BitConverter]::ToString($sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($Text))) -replace '-', '').ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
}

function Get-ScheduledTaskActionSha256 {
    param([string] $TaskName = $ProductionTaskName)
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    $summary = (@($task.Actions) | ForEach-Object {
        ([string]$_.Execute + ' ' + [string]$_.Arguments).Trim()
    }) -join ' ; '
    return Get-StringSha256Hex -Text $summary.Trim().ToLowerInvariant()
}

function Assert-ScheduledTaskLaunchContext {
    param(
        [string] $TaskName,
        [bool] $IsSmokeTest,
        [bool] $AllowLegacyDirectEntrypoint
    )
    $expectedMode = if ($IsSmokeTest) { 'bootstrap' } else { 'runner' }
    if ($IsSmokeTest -and (-not $TaskName)) {
        return
    }
    if (-not $TaskName) {
        throw 'SCHEDULED_TASK_CONTEXT_REQUIRED'
    }
    $knownTaskNames = if ($IsSmokeTest) {
        @('News-Grasp Bootstrap')
    } else {
        @($ProductionTaskName, 'News-Grasp Runner')
    }
    if ($TaskName -notin $knownTaskNames) {
        throw 'SCHEDULED_TASK_CONTEXT_INVALID'
    }
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    $info = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction Stop
    $actionSummary = (@($task.Actions) | ForEach-Object {
        ([string]$_.Execute + ' ' + [string]$_.Arguments).Trim()
    }) -join ' ; '
    $ageMinutes = [math]::Abs(((Get-Date) - $info.LastRunTime).TotalMinutes)
    $modePattern = "(?i)(?:^|\s)$([regex]::Escape($expectedMode))(?:\s|$)"
    $launcherEntrypoint = (
        $actionSummary -match '(?i)news-grasp-task-launcher\.pyw' -and
        $actionSummary -match $modePattern -and
        $actionSummary -match '(?i)--scheduled-task-name' -and
        $actionSummary -match [regex]::Escape($TaskName)
    )
    $legacyDirectEntrypoint = (
        $AllowLegacyDirectEntrypoint -and
        (-not $IsSmokeTest) -and
        $actionSummary -match '(?i)powershell(?:\.exe)?' -and
        $actionSummary -match '(?i)news-grasp-runner\.ps1'
    )
    $stateOk = ([string]$task.State -eq 'Running') -or ($IsSmokeTest -and [string]$task.State -eq 'Ready')
    if (
        (-not $stateOk) -or
        $ageMinutes -gt 10 -or
        (-not ($launcherEntrypoint -or $legacyDirectEntrypoint))
    ) {
        throw 'SCHEDULED_TASK_CONTEXT_INVALID'
    }
}

function Invoke-BoundedGitFetch {
    param(
        [string] $GitExe,
        [string] $WorkingDirectory
    )
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $GitExe
    $startInfo.WorkingDirectory = $WorkingDirectory
    $startInfo.Arguments = '-c credential.interactive=never -c http.lowSpeedLimit=1 -c http.lowSpeedTime=30 fetch origin main --quiet'
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.EnvironmentVariables['GIT_TERMINAL_PROMPT'] = '0'
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    try {
        if (-not $process.Start()) { return 126 }
        if (-not $process.WaitForExit(60000)) {
            $process.Kill()
            $process.WaitForExit()
            return 124
        }
        return [int]$process.ExitCode
    } finally {
        $process.Dispose()
    }
}

function Write-PreliminaryLaunchPermit {
    param(
        [string] $SourceRepoDir,
        [string] $BinDir,
        [string] $IssueDate,
        [string] $PythonExe
    )
    $broker = Join-Path $env:USERPROFILE 'bin\ai-model-spawn-broker.py'
    $python = $PythonExe
    $liveRunner = Join-Path $BinDir 'news-grasp-runner.ps1'
    if (
        (-not (Test-Path -LiteralPath $broker -PathType Leaf)) -or
        (-not (Test-Path -LiteralPath $python -PathType Leaf)) -or
        (-not (Test-Path -LiteralPath $liveRunner -PathType Leaf))
    ) {
        throw 'PRELIMINARY_LAUNCH_AUTHORITY_INPUT_MISSING'
    }
    $authorityDir = Join-Path $BinDir 'news-grasp-authority'
    $missionPath = Join-Path $authorityDir 'audit-mission-authority-v1.json'
    $launchPermitPath = Join-Path $authorityDir "$IssueDate-launch-permit.json"
    New-Item -ItemType Directory -Force -Path $authorityDir | Out-Null
    $missionJson = (& $python $broker 'issue-news-grasp-audit-mission' 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) { throw "audit mission authority issuance failed exit=$LASTEXITCODE" }
    Write-AtomicUtf8Text -Path $missionPath -Text ($missionJson + [Environment]::NewLine)
    $taskActionSha256 = Get-ScheduledTaskActionSha256 -TaskName $ProductionTaskName
    $runnerSha256 = Get-FileSha256Hex -Path $liveRunner
    $launchNonce = "bootstrap-preliminary-$IssueDate-$([Guid]::NewGuid().ToString('N'))"
    $permitJson = (& $python $broker 'issue-news-grasp-launch-permit' '--issue-date' $IssueDate '--task-action-sha256' $taskActionSha256 '--runner-sha256' $runnerSha256 '--launch-nonce' $launchNonce '--mission-authority' $missionPath 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) { throw "preliminary scheduled launch permit issuance failed exit=$LASTEXITCODE" }
    Write-AtomicUtf8Text -Path $launchPermitPath -Text ($permitJson + [Environment]::NewLine)
    return [ordered]@{
        broker = $broker
        python = $python
        live_runner = $liveRunner
        mission_path = $missionPath
        launch_permit_path = $launchPermitPath
        task_action_sha256 = $taskActionSha256
        runner_sha256 = $runnerSha256
    }
}

function Record-StartupFailureForAudit {
    param(
        [object] $AuthorityContext,
        [string] $SourceRepoDir,
        [string] $BinDir,
        [string] $IssueDate,
        [string] $Detail
    )
    $runId = [Guid]::NewGuid().ToString('N')
    $statePath = Join-Path $BinDir 'news-grasp-runner-state.json'
    $logPath = Join-Path $BinDir "news-grasp-logs\$IssueDate.log"
    $receiptPath = Join-Path $SourceRepoDir "build\scheduled-failure-receipts\$IssueDate-bootstrap-$runId.json"
    $now = (Get-Date).ToString('yyyy-MM-ddTHH:mm:ss.fffK')
    $state = [ordered]@{
        status = 'blocked_startup_self_repair_failed'
        message = 'pre-run bootstrap failed exit=1'
        exit_code = 72
        updated_at = $now
        heartbeat_at = $now
        date = $IssueDate
        run_intent = 'ScheduledProduction'
        run_id = $runId
        phase = 'startup_self_repair'
        attempt_terminal = $true
        recovery_class = 'startup_self_repair_failure'
        scheduled_attempt_status = 'failed'
        recovery_attempt_status = 'not_started'
        first_terminal_wins = 'first-terminal-wins'
        scheduled_failure_receipt_path = $receiptPath
    }
    Write-AtomicUtf8Text -Path $statePath -Text (($state | ConvertTo-Json -Depth 6) + [Environment]::NewLine)
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $logPath) | Out-Null
    [System.IO.File]::AppendAllText(
        $logPath,
        "[$now] ERROR: STARTUP_SELF_REPAIR_FAILED detail=$Detail" + [Environment]::NewLine,
        [System.Text.UTF8Encoding]::new($false)
    )
    $admissionJson = (& $AuthorityContext.python $AuthorityContext.broker 'admit' '--operation-kind' 'scheduled_production' '--attempt-id' $IssueDate '--issue-date' $IssueDate '--authority-evidence' $AuthorityContext.launch_permit_path '--expected-task-action-sha256' $AuthorityContext.task_action_sha256 '--expected-runner-sha256' $AuthorityContext.runner_sha256 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) { throw "startup scheduled admission failed exit=$LASTEXITCODE detail=$admissionJson" }
    $stateSha256 = Get-FileSha256Hex -Path $statePath
    $logSha256 = Get-FileSha256Hex -Path $logPath
    $failureJson = (& $AuthorityContext.python $AuthorityContext.broker 'record-news-grasp-failure' '--issue-date' $IssueDate '--run-id' $runId '--last-task-result' '72' '--runner-state' 'blocked_startup_self_repair_failed' '--state-sha256' $stateSha256 '--log-sha256' $logSha256 '--task-action-sha256' $AuthorityContext.task_action_sha256 '--runner-sha256' $AuthorityContext.runner_sha256 '--failure-stage' 'startup_self_repair' 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) { throw "startup failure freeze failed exit=$LASTEXITCODE detail=$failureJson" }
    $failure = $failureJson | ConvertFrom-Json -ErrorAction Stop
    if ($failure.schemaVersion -ne 'SCHEDULED_FAILURE_RECEIPT_V1' -or $failure.issueDate -ne $IssueDate) {
        throw 'startup failure receipt invalid'
    }
    Write-AtomicUtf8Text -Path $receiptPath -Text ($failureJson + [Environment]::NewLine)
}

$SourceRepoDir = Resolve-NewsGraspRepoDir -Override $RepoDir
$PythonExe = if ($PythonExe) { (Resolve-Path -LiteralPath $PythonExe).Path } else { Join-Path $SourceRepoDir '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) { throw 'News-Grasp Python runtime is missing.' }
if ($EvidenceRepoDir) { $env:NEWS_GRASP_EVIDENCE_REPO_DIR = (Resolve-Path -LiteralPath $EvidenceRepoDir).Path }
if (-not $DateStamp) { $DateStamp = Get-Date -Format 'yyyy-MM-dd' }
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
$preliminaryAuthority = $null
$runtimeMutex = $null
$runtimeMutexOwned = $false
$runtimeOwnerReceiptStream = $null
$runtimeOwnerReceiptPath = ''
$runtimeOwnerNonce = ''
if ($UseProductionRuntime) {
    try {
        Assert-ScheduledTaskLaunchContext -TaskName $ScheduledTaskName -IsSmokeTest ([bool]$SmokeTest) -AllowLegacyDirectEntrypoint ([bool]$LegacyDirectEntrypoint)
    } catch {
        if ($_.Exception.Message -eq 'SCHEDULED_TASK_CONTEXT_INVALID') {
            exit $SCHEDULED_TASK_CONTEXT_REJECTED_EXIT
        }
        throw
    }
    $runtimeOwnerReceiptPath = Join-Path $BinDir 'news-grasp-runtime-lifecycle-owner.json'
    if (Test-Path -LiteralPath $runtimeOwnerReceiptPath) {
        $ownerItem = Get-Item -LiteralPath $runtimeOwnerReceiptPath -Force
        if (($ownerItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw 'PRODUCTION_RUNTIME_MUTEX_OWNER_RECEIPT_INVALID'
        }
    }
    $runtimeOwnerNonce = [Guid]::NewGuid().ToString('N')
    $runtimeMutexIdentity = ([System.Security.Principal.WindowsIdentity]::GetCurrent()).User.Value
    if ([string]::IsNullOrWhiteSpace($runtimeMutexIdentity)) { throw 'PRODUCTION_RUNTIME_MUTEX_OWNER_INVALID' }
    $ownerReceipt = [ordered]@{
        schemaVersion = 'NEWS_GRASP_RUNTIME_LIFECYCLE_OWNER_V1'
        ownerPid = [int]$PID
        ownerNonce = $runtimeOwnerNonce
        mutexName = "Global\NewsGraspBootstrapOrchestration-$runtimeMutexIdentity"
        ownerScriptPath = (Resolve-Path -LiteralPath $PSCommandPath).Path
        ownerProcessImage = [Diagnostics.Process]::GetCurrentProcess().MainModule.FileName
        issuedAtUtc = [DateTime]::UtcNow.ToString('o')
    }
    $ownerBytes = [System.Text.UTF8Encoding]::new($false).GetBytes(
        (($ownerReceipt | ConvertTo-Json -Depth 4 -Compress) + [Environment]::NewLine)
    )
    $runtimeOwnerReceiptStream = [System.IO.FileStream]::new(
        $runtimeOwnerReceiptPath,
        [System.IO.FileMode]::Create,
        [System.IO.FileAccess]::ReadWrite,
        [System.IO.FileShare]::Read
    )
    $runtimeOwnerReceiptStream.Write($ownerBytes, 0, $ownerBytes.Length)
    $runtimeOwnerReceiptStream.Flush($true)
    $runtimeMutex = New-Object System.Threading.Mutex($false, "Global\NewsGraspBootstrapOrchestration-$runtimeMutexIdentity")
    try {
        $runtimeMutexOwned = $runtimeMutex.WaitOne(0)
    } catch [System.Threading.AbandonedMutexException] {
        $runtimeMutexOwned = $true
    }
    if (-not $runtimeMutexOwned) {
        $runtimeOwnerReceiptStream.Dispose()
        $runtimeMutex.Dispose()
        exit 72
    }
}
try {
if ($UseProductionRuntime -and (-not $SmokeTest)) {
    $preliminaryAuthority = Write-PreliminaryLaunchPermit -SourceRepoDir $SourceRepoDir -BinDir $BinDir -IssueDate $DateStamp -PythonExe $PythonExe
}
try {
    $RepoDir = if ($UseProductionRuntime) {
        Resolve-ProductionRuntimeRepo -SourceRepoDir $SourceRepoDir -BootstrapOwnerReceiptPath $runtimeOwnerReceiptPath -BootstrapOwnerNonce $runtimeOwnerNonce
    } else {
        $SourceRepoDir
    }
} catch {
    Write-BootstrapFailureObservation -Phase 'runtime_convergence' -ReasonCode 'PRODUCTION_RUNTIME_CONVERGENCE_FAILED' -Detail $_.Exception.Message
    if ($UseProductionRuntime -and (-not $SmokeTest) -and $preliminaryAuthority) {
        try {
            Record-StartupFailureForAudit -AuthorityContext $preliminaryAuthority -SourceRepoDir $SourceRepoDir -BinDir $BinDir -IssueDate $DateStamp -Detail $_.Exception.Message
        } catch {
            $failureLog = Join-Path $BinDir "news-grasp-logs\$DateStamp.log"
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $failureLog) | Out-Null
            Add-Content -LiteralPath $failureLog -Value "WARN: STARTUP_FAILURE_TERMINALIZER_FAILED reason=$($_.Exception.Message)" -Encoding UTF8
        }
    }
    exit 72
}
$opsDir = Join-Path $RepoDir 'scripts\ops'
if ($SmokeTest) {
    if (-not $StateFile) {
        $StateFile = Join-Path $RepoDir 'build\bootstrap-task-smoke\state.json'
    }
    if (-not $LogDir) {
        $LogDir = Join-Path $RepoDir 'build\bootstrap-task-smoke\logs'
    }
}
if ($StateFile -and -not [System.IO.Path]::IsPathRooted($StateFile)) {
    $StateFile = Join-Path $BinDir $StateFile
}
if ($LogDir -and -not [System.IO.Path]::IsPathRooted($LogDir)) {
    $LogDir = Join-Path $BinDir $LogDir
}
foreach ($file in @('run_codex_with_timeout.ps1', 'news-grasp-bootstrap.ps1', 'watch-news-grasp-runner.ps1', 'news-grasp-runner.ps1', 'news-grasp-lineage.ps1', 'news-grasp-deadman.ps1', 'news-grasp-deadman-launcher.pyw', 'news-grasp-task-launcher.pyw')) {
    $source = Join-Path $opsDir $file
    $destination = Join-Path $BinDir $file
    if (-not (Test-Path -LiteralPath $source)) {
        throw "repo ops script missing: $source"
    }
}

$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$repairEvidenceRoot = if ($UseProductionRuntime) {
    Join-Path $BinDir 'news-grasp-runtime-backups'
} else {
    Join-Path $RepoDir 'build\live-bootstrap-self-repair'
}
$backupDir = Join-Path $repairEvidenceRoot $timestamp
$manifestPath = Join-Path $backupDir 'auto-repair-manifest.json'
$manifestFiles = @()
$changed = $false

foreach ($file in @('run_codex_with_timeout.ps1', 'news-grasp-bootstrap.ps1', 'watch-news-grasp-runner.ps1', 'news-grasp-runner.ps1', 'news-grasp-lineage.ps1', 'news-grasp-deadman.ps1', 'news-grasp-deadman-launcher.pyw', 'news-grasp-task-launcher.pyw')) {
    $source = Join-Path $opsDir $file
    $destination = Join-Path $BinDir $file
    $sourceHash = Get-FileSha256Hex -Path $source
    $beforeHash = Get-FileSha256Hex -Path $destination
    $backup = Join-Path $backupDir $file
    $status = 'unchanged'
    if ($sourceHash -ne $beforeHash) {
        New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
        if (Test-Path -LiteralPath $destination) {
            Copy-Item -LiteralPath $destination -Destination $backup -Force
        }
        Copy-Item -LiteralPath $source -Destination $destination -Force
        $changed = $true
        $status = 'repaired'
    }
    $afterHash = Get-FileSha256Hex -Path $destination
    $manifestFiles += [ordered]@{
        file = $file
        source = $source
        destination = $destination
        backup = if (Test-Path -LiteralPath $backup) { $backup } else { '' }
        before_sha256 = $beforeHash
        source_sha256 = $sourceHash
        after_sha256 = $afterHash
        status = $status
    }
}

if ($changed) {
    [ordered]@{
        created_at = (Get-Date).ToString('yyyy-MM-ddTHH:mm:ss.fffK')
        repo_dir = $RepoDir
        bin_dir = $BinDir
        backup_dir = $backupDir
        files = $manifestFiles
    } | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
}

$broker = Join-Path $env:USERPROFILE 'bin\ai-model-spawn-broker.py'
$python = $PythonExe
$liveRunner = Join-Path $BinDir 'news-grasp-runner.ps1'
$authorityDir = Join-Path $BinDir 'news-grasp-authority'
$missionPath = Join-Path $authorityDir 'audit-mission-authority-v1.json'
$launchPermitPath = Join-Path $authorityDir "$DateStamp-launch-permit.json"
if ((-not (Test-Path -LiteralPath $broker -PathType Leaf)) -or (-not (Test-Path -LiteralPath $python -PathType Leaf))) {
    throw 'News-Grasp authority broker or Python runtime is missing.'
}
New-Item -ItemType Directory -Force -Path $authorityDir | Out-Null
$missionJson = (& $python $broker 'issue-news-grasp-audit-mission' 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { throw "audit mission authority issuance failed exit=$LASTEXITCODE" }
Write-AtomicUtf8Text -Path $missionPath -Text ($missionJson + [Environment]::NewLine)
$taskActionSha256 = Get-ScheduledTaskActionSha256 -TaskName $ProductionTaskName
$runnerSha256 = Get-FileSha256Hex -Path $liveRunner
$launchNonce = "bootstrap-$DateStamp-$([Guid]::NewGuid().ToString('N'))"
$permitJson = (& $python $broker 'issue-news-grasp-launch-permit' '--issue-date' $DateStamp '--task-action-sha256' $taskActionSha256 '--runner-sha256' $runnerSha256 '--launch-nonce' $launchNonce '--mission-authority' $missionPath 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { throw "scheduled launch permit issuance failed exit=$LASTEXITCODE" }
Write-AtomicUtf8Text -Path $launchPermitPath -Text ($permitJson + [Environment]::NewLine)

$watcherPath = Join-Path $BinDir 'watch-news-grasp-runner.ps1'
$args = @('-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden', '-ExecutionPolicy', 'Bypass', '-File', $watcherPath)
if ($StartOnly) {
    $args += '-StartOnly'
} else {
    $args += '-Start'
}
if ($SmokeTest) { $args += '-SmokeTest' }
if ($SkipSourceSync) { $args += '-SkipSourceSync' }
if ($RecoverOnly) { $args += '-RecoverOnly' }
if ($PollSeconds -ne 30) { $args += @('-PollSeconds', [string]$PollSeconds) }
if ($StaleMinutes -ne 15) { $args += @('-StaleMinutes', [string]$StaleMinutes) }
if ($TimeoutMinutes -ne 120) { $args += @('-TimeoutMinutes', [string]$TimeoutMinutes) }
if ($UseProductionRuntime) {
    $args += @('-RunnerPath', (Join-Path $RepoDir 'scripts\ops\news-grasp-runner.ps1'))
} elseif ($RunnerPath) { $args += @('-RunnerPath', $RunnerPath) }
if ($StateFile) { $args += @('-StateFile', $StateFile) }
if ($LogDir) { $args += @('-LogDir', $LogDir) }
if ($DateStamp) { $args += @('-DateStamp', $DateStamp) }
$args += @('-RepoDir', $RepoDir, '-BinDir', $BinDir)
$args += @('-PyExeOverride', $PythonExe)

& powershell.exe @args
$watcherExit = $LASTEXITCODE
} finally {
    if ($runtimeMutex) {
        if ($runtimeMutexOwned) {
            try { $runtimeMutex.ReleaseMutex() } catch { }
        }
        if ($runtimeOwnerReceiptStream) {
            try { $runtimeOwnerReceiptStream.Dispose() } catch { }
        }
        $runtimeMutex.Dispose()
    }
}
exit $watcherExit
