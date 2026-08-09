param(
    [string] $RepoDir = '',
    [string] $BinDir = (Join-Path $env:USERPROFILE 'bin'),
    [string] $TaskPythonwPath = '',
    [string] $RunnerTaskName = 'News-Grasp Production',
    [string] $BootstrapTaskName = 'News-Grasp Bootstrap',
    [string] $DeadmanTaskName = 'News-Grasp Deadman',
    [switch] $SkipTaskRegistration
)

$ErrorActionPreference = 'Stop'
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$script:InstallationCommitted = $false
$script:InstallationMutationStarted = $false

. (Join-Path $PSScriptRoot 'install-news-grasp-ops-guard.ps1')

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

function Resolve-NewsGraspRepoDir {
    param([string] $Override)
    if ($Override) {
        return (Resolve-Path -LiteralPath $Override).Path
    }
    if ($env:NEWS_GRASP_REPO_DIR) {
        return (Resolve-Path -LiteralPath $env:NEWS_GRASP_REPO_DIR).Path
    }
    $repoFromOps = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
    if (Test-Path -LiteralPath (Join-Path $repoFromOps 'tools\daily_self_heal.py')) {
        return $repoFromOps
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

function Resolve-NewsGraspTaskPythonw {
    param([string] $Override, [string] $ResolvedRepoDir)
    if ($Override) {
        if (-not (Test-Path -LiteralPath $Override -PathType Leaf)) {
            throw "指定されたTask Pythonが見つかりません: $Override"
        }
        return (Resolve-Path -LiteralPath $Override).Path
    }
    if ($env:NEWS_GRASP_TASK_PYTHONW) {
        if (-not (Test-Path -LiteralPath $env:NEWS_GRASP_TASK_PYTHONW -PathType Leaf)) {
            throw "NEWS_GRASP_TASK_PYTHONWが指すPythonが見つかりません: $env:NEWS_GRASP_TASK_PYTHONW"
        }
        return (Resolve-Path -LiteralPath $env:NEWS_GRASP_TASK_PYTHONW).Path
    }
    $candidates = @(
        (Join-Path $env:USERPROFILE 'OneDrive\ドキュメント\ProjectFolders\News-Grasp\.venv\Scripts\pythonw.exe'),
        (Join-Path $ResolvedRepoDir '.venv\Scripts\pythonw.exe')
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    throw 'News-Grasp Scheduled Task用の安定したpythonw.exeが見つかりません。-TaskPythonwPathを指定してください。'
}

function Invoke-NewsGraspRollbackJournal {
    param([string] $JournalPath, [object] $Journal)
    foreach ($row in @($Journal.files) | Select-Object -Last 100) {
        if ([string]$row.backup -and (Test-Path -LiteralPath ([string]$row.backup) -PathType Leaf)) {
            Copy-Item -LiteralPath ([string]$row.backup) -Destination ([string]$row.destination) -Force
        } elseif (Test-Path -LiteralPath ([string]$row.destination) -PathType Leaf) {
            Remove-Item -LiteralPath ([string]$row.destination) -Force
        }
    }
    foreach ($snapshot in @($Journal.task_snapshots)) {
        $taskName = [string]$snapshot.task_name
        if ([bool]$snapshot.existed_before) {
            $xml = Get-Content -LiteralPath ([string]$snapshot.xml_backup) -Raw -Encoding Unicode
            Register-ScheduledTask -TaskName $taskName -Xml $xml -Force | Out-Null
            if ([bool]$snapshot.enabled_before) {
                Enable-ScheduledTask -TaskName $taskName | Out-Null
            } else {
                Disable-ScheduledTask -TaskName $taskName | Out-Null
            }
        } elseif (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        }
    }
    if (-not [bool]$Journal.bin_dir_existed_before -and (Test-Path -LiteralPath ([string]$Journal.bin_dir) -PathType Container)) {
        if (-not (Get-ChildItem -LiteralPath ([string]$Journal.bin_dir) -Force)) {
            Remove-Item -LiteralPath ([string]$Journal.bin_dir) -Force
        }
    }
    $Journal.phase = 'rolled_back'
    $Journal.rolled_back_at = (Get-Date).ToString('yyyy-MM-ddTHH:mm:ss.fffK')
    Write-AtomicUtf8Text -Path $JournalPath -Text (($Journal | ConvertTo-Json -Depth 10) + [Environment]::NewLine)
}

function Recover-NewsGraspInterruptedInstall {
    param(
        [string] $BackupRoot,
        [string] $ExpectedRepoDir,
        [string] $ExpectedBinDir,
        [string[]] $ExpectedTaskNames
    )
    if (-not (Test-Path -LiteralPath $BackupRoot -PathType Container)) { return }
    $transactionDirs = @(
        Get-ChildItem -LiteralPath $BackupRoot -Directory |
            Where-Object { ($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -eq 0 } |
            Sort-Object LastWriteTime
    )
    foreach ($transactionDir in $transactionDirs) {
        $journalFile = Get-Item -LiteralPath (Join-Path $transactionDir.FullName 'install-manifest.json') -ErrorAction SilentlyContinue
        if (-not $journalFile -or ($journalFile.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { continue }
        try {
            $journal = Get-Content -LiteralPath $journalFile.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
        } catch {
            continue
        }
        if ($journal.schemaVersion -eq 'NEWS_GRASP_OPS_INSTALL_JOURNAL_V1' -and $journal.phase -notin @('committed', 'rolled_back')) {
            Assert-NewsGraspRecoveryJournal `
                -JournalPath $journalFile.FullName `
                -Journal $journal `
                -ExpectedBackupRoot $BackupRoot `
                -ExpectedRepoDir $ExpectedRepoDir `
                -ExpectedBinDir $ExpectedBinDir `
                -ExpectedTaskNames $ExpectedTaskNames | Out-Null
            Invoke-NewsGraspRollbackJournal -JournalPath $journalFile.FullName -Journal $journal
        }
    }
}

function Invoke-NewsGraspInstallRollback {
    foreach ($row in @($manifestFiles) | Select-Object -Last 100) {
        if ([string]$row.backup -and (Test-Path -LiteralPath ([string]$row.backup) -PathType Leaf)) {
            Copy-Item -LiteralPath ([string]$row.backup) -Destination ([string]$row.destination) -Force
        } elseif (Test-Path -LiteralPath ([string]$row.destination) -PathType Leaf) {
            Remove-Item -LiteralPath ([string]$row.destination) -Force
        }
    }
    foreach ($snapshot in $taskSnapshots) {
        $taskName = [string]$snapshot.task_name
        if ([bool]$snapshot.existed_before) {
            $xml = Get-Content -LiteralPath ([string]$snapshot.xml_backup) -Raw -Encoding Unicode
            Register-ScheduledTask -TaskName $taskName -Xml $xml -Force | Out-Null
            if ([bool]$snapshot.enabled_before) {
                Enable-ScheduledTask -TaskName $taskName | Out-Null
            } else {
                Disable-ScheduledTask -TaskName $taskName | Out-Null
            }
        } elseif (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        }
    }
    if (-not $binDirExistedBefore -and (Test-Path -LiteralPath $BinDir -PathType Container)) {
        if (-not (Get-ChildItem -LiteralPath $BinDir -Force)) {
            Remove-Item -LiteralPath $BinDir -Force
        }
    }
}

function Write-NewsGraspInstallJournal {
    param([string] $Phase)
    $missionSha = ''
    if ($missionAuthorityPath -and (Test-Path -LiteralPath $missionAuthorityPath -PathType Leaf)) {
        $missionSha = (Get-FileHash -LiteralPath $missionAuthorityPath -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    $journal = [ordered]@{
        schemaVersion = 'NEWS_GRASP_OPS_INSTALL_JOURNAL_V1'
        transaction_id = $timestamp
        phase = $Phase
        updated_at = (Get-Date).ToString('yyyy-MM-ddTHH:mm:ss.fffK')
        repo_dir = $RepoDir
        bin_dir = $BinDir
        task_pythonw_path = $TaskPythonwPath
        bin_dir_existed_before = $binDirExistedBefore
        backup_dir = $BackupDir
        files = $manifestFiles
        rollback_commands = @('Invoke-NewsGraspInstallRollback')
        mission_authority = [ordered]@{
            path = $missionAuthorityPath
            sha256 = $missionSha
            schema = 'AUDIT_MISSION_AUTHORITY_V1'
        }
        scheduled_tasks = $scheduledTasks
        task_snapshots = $taskSnapshots
    }
    Write-AtomicUtf8Text -Path $ManifestPath -Text (($journal | ConvertTo-Json -Depth 10) + [Environment]::NewLine)
}

function Assert-NewsGraspInstalledState {
    foreach ($file in $files) {
        $source = Join-Path $ops $file
        $destination = Join-Path $BinDir $file
        if (-not (Test-Path -LiteralPath $destination -PathType Leaf)) {
            throw "installed file missing: $destination"
        }
        $sourceSha = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
        $installedSha = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash
        if ($sourceSha -ne $installedSha) { throw "installed file hash mismatch: $file" }
    }
    $mission = Get-Content -LiteralPath $missionAuthorityPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $missionSchema = [string]$(if ($mission.schemaVersion) { $mission.schemaVersion } else { $mission.schema })
    if ($missionSchema -ne 'AUDIT_MISSION_AUTHORITY_V1') { throw 'audit mission authority schema mismatch' }
    if ($SkipTaskRegistration) { return }
    $expected = @(
        [ordered]@{ name = $RunnerTaskName; execute = $pythonw; arguments = $runnerArgs; working = $BinDir; start = 'T06:00' },
        [ordered]@{ name = $BootstrapTaskName; execute = $pythonw; arguments = $bootstrapArgs; working = $BinDir; start = 'T05:55' },
        [ordered]@{ name = $DeadmanTaskName; execute = $pythonw; arguments = $deadmanArgs; working = $BinDir; start = 'T06:40'; interval = 'PT1H'; duration = 'P1D' }
    )
    foreach ($spec in $expected) {
        $task = Get-ScheduledTask -TaskName ([string]$spec.name) -ErrorAction Stop
        $actions = @($task.Actions)
        $triggers = @($task.Triggers)
        if ($actions.Count -ne 1 -or $triggers.Count -ne 1) { throw "scheduled task cardinality mismatch: $($spec.name)" }
        $action = $actions[0]
        $trigger = $triggers[0]
        if (-not $task.Settings.Enabled) { throw "scheduled task disabled: $($spec.name)" }
        if (
            [string]$action.Execute -ne [string]$spec.execute -or
            [string]$action.Arguments -ne [string]$spec.arguments -or
            [string]$action.WorkingDirectory -ne [string]$spec.working
        ) {
            throw "scheduled task action mismatch: $($spec.name)"
        }
        if (-not [bool]$task.Settings.StartWhenAvailable) { throw "scheduled task start-when-available mismatch: $($spec.name)" }
        if ([string]$task.Settings.MultipleInstances -ne 'IgnoreNew') { throw "scheduled task instance policy mismatch: $($spec.name)" }
        if ([string]$trigger.StartBoundary -notlike "*$($spec.start)*") { throw "scheduled task trigger mismatch: $($spec.name)" }
        if ([int]$trigger.DaysInterval -ne 1) { throw "scheduled task daily interval mismatch: $($spec.name)" }
        if ($spec.interval -and [string]$trigger.Repetition.Interval -ne [string]$spec.interval) {
            throw "scheduled task repetition mismatch: $($spec.name)"
        }
        if ($spec.interval) {
            if ([string]$trigger.Repetition.Duration -ne [string]$spec.duration) { throw "scheduled task repetition duration mismatch: $($spec.name)" }
            if ([bool]$trigger.Repetition.StopAtDurationEnd) { throw "scheduled task repetition stop policy mismatch: $($spec.name)" }
        } elseif ([string]$trigger.Repetition.Interval -or [string]$trigger.Repetition.Duration) {
            throw "scheduled task unexpected repetition: $($spec.name)"
        }
    }
}

trap {
    $originalError = $_
    if ($script:InstallationMutationStarted -and -not $script:InstallationCommitted) {
        try {
            Invoke-NewsGraspInstallRollback
            if (Test-Path -LiteralPath $BackupDir -PathType Container) {
                Write-NewsGraspInstallJournal -Phase 'rolled_back'
            }
        } catch {
            Write-Warning ("News-Grasp installer rollback failed: {0}" -f $_.Exception.Message)
        }
    }
    Write-Error -ErrorRecord $originalError
    exit 1
}

$files = @(
    'run_codex_with_timeout.ps1',
    'news-grasp-bootstrap.ps1',
    'news-grasp-runner.ps1',
    'news-grasp-lineage.ps1',
    'watch-news-grasp-runner.ps1',
    'news-grasp-deadman.ps1',
    'news-grasp-deadman-launcher.pyw',
    'news-grasp-task-launcher.pyw'
)

$RepoDir = Resolve-NewsGraspRepoDir -Override $RepoDir
$TaskPythonwPath = Resolve-NewsGraspTaskPythonw -Override $TaskPythonwPath -ResolvedRepoDir $RepoDir
$ops = Join-Path $RepoDir 'scripts\ops'
$backupRoot = Join-Path $RepoDir 'build\live-runner-backups'
Recover-NewsGraspInterruptedInstall `
    -BackupRoot $backupRoot `
    -ExpectedRepoDir $RepoDir `
    -ExpectedBinDir $BinDir `
    -ExpectedTaskNames @($RunnerTaskName, $BootstrapTaskName, $DeadmanTaskName)
$binDirExistedBefore = Test-Path -LiteralPath $BinDir -PathType Container

# backup + explicit approval + rollback: live runner overwrite must leave a restorable manifest.
$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$BackupDir = Join-Path $backupRoot $timestamp
$ManifestPath = Join-Path $BackupDir 'install-manifest.json'
$taskSnapshots = @()
$manifestFiles = @()
$script:InstallationMutationStarted = $true
New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null

if (-not $SkipTaskRegistration) {
    foreach ($taskName in @($RunnerTaskName, $BootstrapTaskName, $DeadmanTaskName)) {
        $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        $xmlPath = Join-Path $BackupDir (("task-{0}.xml" -f ($taskName -replace '[^A-Za-z0-9._-]', '_')))
        if ($task) {
            Export-ScheduledTask -TaskName $taskName | Set-Content -LiteralPath $xmlPath -Encoding Unicode
        }
        $taskSnapshots += [ordered]@{
            task_name = $taskName
            existed_before = [bool]$task
            enabled_before = [bool]($task -and $task.Settings.Enabled)
            xml_backup = if ($task) { $xmlPath } else { '' }
        }
    }
}

foreach ($file in $files) {
    $source = Join-Path $ops $file
    $destination = Join-Path $BinDir $file
    $backup = Join-Path $BackupDir $file
    $beforeHash = ''
    if (Test-Path -LiteralPath $destination) {
        Copy-Item -LiteralPath $destination -Destination $backup -Force
        $beforeHash = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash
    }
    $sourceHash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
    $manifestFiles += [ordered]@{
        file = $file
        source = $source
        destination = $destination
        backup = if (Test-Path -LiteralPath $backup) { $backup } else { '' }
        before_sha256 = $beforeHash
        source_sha256 = $sourceHash
        after_sha256 = ''
    }
}

$authorityDir = Join-Path $BinDir 'news-grasp-authority'
$missionAuthorityPath = Join-Path $authorityDir 'audit-mission-authority-v1.json'
$missionAuthorityBackup = Join-Path $BackupDir 'audit-mission-authority-v1.json'
$missionAuthorityBeforeHash = ''
if (Test-Path -LiteralPath $missionAuthorityPath -PathType Leaf) {
    Copy-Item -LiteralPath $missionAuthorityPath -Destination $missionAuthorityBackup -Force
    $missionAuthorityBeforeHash = (Get-FileHash -LiteralPath $missionAuthorityPath -Algorithm SHA256).Hash
}
$missionAuthorityRow = [ordered]@{
    file = 'audit-mission-authority-v1.json'
    source = 'broker:issue-news-grasp-audit-mission'
    destination = $missionAuthorityPath
    backup = if (Test-Path -LiteralPath $missionAuthorityBackup -PathType Leaf) { $missionAuthorityBackup } else { '' }
    before_sha256 = $missionAuthorityBeforeHash
    source_sha256 = ''
    after_sha256 = ''
}
$manifestFiles += $missionAuthorityRow
$scheduledTasks = @()
$rollbackCommands = @('Invoke-NewsGraspInstallRollback')
Write-NewsGraspInstallJournal -Phase 'prepared'
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null

foreach ($file in $files) {
    $source = Join-Path $ops $file
    $destination = Join-Path $BinDir $file
    Copy-Item -LiteralPath $source -Destination $destination -Force
    $row = @($manifestFiles | Where-Object { $_.file -eq $file })[0]
    $row['after_sha256'] = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash
}
Write-NewsGraspInstallJournal -Phase 'files_installed'

$brokerPath = Join-Path $env:USERPROFILE 'bin\ai-model-spawn-broker.py'
$pythonPath = Join-Path (Split-Path -Parent $TaskPythonwPath) 'python.exe'
if ((-not (Test-Path -LiteralPath $brokerPath -PathType Leaf)) -or (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf))) {
    throw 'News-Grasp audit mission authority broker is unavailable.'
}
New-Item -ItemType Directory -Force -Path $authorityDir | Out-Null
$missionAuthorityJson = (& $pythonPath $brokerPath 'issue-news-grasp-audit-mission' 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { throw "audit mission authority issuance failed exit=$LASTEXITCODE" }
Write-AtomicUtf8Text -Path $missionAuthorityPath -Text ($missionAuthorityJson + [Environment]::NewLine)
$missionAuthorityRow['after_sha256'] = (Get-FileHash -LiteralPath $missionAuthorityPath -Algorithm SHA256).Hash
Write-NewsGraspInstallJournal -Phase 'authority_issued'
if (-not $SkipTaskRegistration) {
    $watcherPath = Join-Path $BinDir 'watch-news-grasp-runner.ps1'
    $bootstrapPath = Join-Path $BinDir 'news-grasp-bootstrap.ps1'
    $deadmanLauncherPath = Join-Path $BinDir 'news-grasp-deadman-launcher.pyw'
    $taskLauncherPath = Join-Path $BinDir 'news-grasp-task-launcher.pyw'
    $pythonw = $TaskPythonwPath
    if (-not (Test-Path -LiteralPath $pythonw)) { throw 'News-Grasp .venv pythonw.exe が見つかりません。' }
    $runnerArgs = "`"$taskLauncherPath`" runner --scheduled-task-name `"$RunnerTaskName`""
    $runnerAction = New-ScheduledTaskAction -Execute $pythonw -Argument $runnerArgs -WorkingDirectory $BinDir
    $runnerTrigger = New-ScheduledTaskTrigger -Daily -At 6:00am
    $runnerSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew
    $runnerRegistered = $false
    $runnerRegisterError = ''
    try {
        Register-ScheduledTask -TaskName $RunnerTaskName -Action $runnerAction -Trigger $runnerTrigger -Settings $runnerSettings -Description 'News-Grasp daily runner bootstrap. Repairs live ops from repo before starting runner.' -Force -ErrorAction Stop | Out-Null
        Enable-ScheduledTask -TaskName $RunnerTaskName -ErrorAction Stop | Out-Null
        $runnerRegistered = $true
        $scheduledTasks += [ordered]@{
            task_name = $RunnerTaskName
            execute = $pythonw
            arguments = $runnerArgs
            trigger = 'daily 06:00'
            status = 'registered_watcher_entrypoint'
        }
    } catch {
        $runnerRegisterError = $_.Exception.Message
        $scheduledTasks += [ordered]@{
            task_name = $RunnerTaskName
            execute = $pythonw
            arguments = $runnerArgs
            trigger = 'daily 06:00'
            status = 'register_failed_bootstrap_required'
            error = $runnerRegisterError
        }
    }

    $bootstrapArgs = "`"$taskLauncherPath`" bootstrap --scheduled-task-name `"$BootstrapTaskName`""
    $bootstrapAction = New-ScheduledTaskAction -Execute $pythonw -Argument $bootstrapArgs -WorkingDirectory $BinDir
    $bootstrapTrigger = New-ScheduledTaskTrigger -Daily -At 5:55am
    $bootstrapSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew
    try {
        Register-ScheduledTask -TaskName $BootstrapTaskName -Action $bootstrapAction -Trigger $bootstrapTrigger -Settings $bootstrapSettings -Description 'News-Grasp pre-run self repair bootstrap.' -Force -ErrorAction Stop | Out-Null
        Enable-ScheduledTask -TaskName $BootstrapTaskName -ErrorAction Stop | Out-Null
        $scheduledTasks += [ordered]@{
            task_name = $BootstrapTaskName
            execute = $pythonw
            arguments = $bootstrapArgs
            trigger = 'daily 05:55'
            status = 'registered_pre_run_self_repair'
        }
    } catch {
        if (-not $runnerRegistered) {
            throw "failed to register $RunnerTaskName and failed to create $BootstrapTaskName"
        }
        $scheduledTasks += [ordered]@{
            task_name = $BootstrapTaskName
            execute = $pythonw
            arguments = $bootstrapArgs
            trigger = 'daily 05:55'
            status = 'create_failed'
            error = $_.Exception.Message
        }
    }

    $deadmanArgs = "`"$deadmanLauncherPath`""
    $deadmanAction = New-ScheduledTaskAction -Execute $pythonw -Argument $deadmanArgs -WorkingDirectory $BinDir
    $deadmanTrigger = New-ScheduledTaskTrigger -Daily -At 6:40am
    $deadmanRepetition = New-CimInstance -Namespace 'Root/Microsoft/Windows/TaskScheduler' -ClassName 'MSFT_TaskRepetitionPattern' -ClientOnly -Property @{
        Interval = 'PT1H'
        Duration = 'P1D'
        StopAtDurationEnd = $false
    }
    $deadmanTrigger.Repetition = $deadmanRepetition
    $deadmanSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $DeadmanTaskName -Action $deadmanAction -Trigger $deadmanTrigger -Settings $deadmanSettings -Description 'News-Grasp hourly audit and bounded recovery control.' -Force -ErrorAction Stop | Out-Null
    Enable-ScheduledTask -TaskName $DeadmanTaskName -ErrorAction Stop | Out-Null
    $scheduledTasks += [ordered]@{
        task_name = $DeadmanTaskName
        execute = $pythonw
        arguments = $deadmanArgs
        trigger = 'daily 06:40 with hourly repetition'
        status = 'registered_deadman_control'
    }
    Write-NewsGraspInstallJournal -Phase 'tasks_converged'
}

if ((-not $SkipTaskRegistration) -and (-not $runnerRegistered)) {
    throw "failed to converge $RunnerTaskName action: $runnerRegisterError"
}
Assert-NewsGraspInstalledState
Write-NewsGraspInstallJournal -Phase 'verified'
Write-NewsGraspInstallJournal -Phase 'committed'
$script:InstallationCommitted = $true
Write-Host "News-Grasp ops scripts installed to $BinDir"
Write-Host "Backup manifest: $ManifestPath"
