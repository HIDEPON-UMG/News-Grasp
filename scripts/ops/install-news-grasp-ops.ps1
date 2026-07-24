param(
    [string] $RepoDir = '',
    [string] $BinDir = (Join-Path $env:USERPROFILE 'bin'),
    [string] $RunnerTaskName = 'News-Grasp Runner',
    [string] $BootstrapTaskName = 'News-Grasp Bootstrap',
    [string] $DeadmanTaskName = 'News-Grasp Deadman',
    [switch] $SkipTaskRegistration
)

$ErrorActionPreference = 'Stop'
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

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

$RepoDir = Resolve-NewsGraspRepoDir -Override $RepoDir
$ops = Join-Path $RepoDir 'scripts\ops'
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null

# backup + explicit approval + rollback: live runner overwrite must leave a restorable manifest.
$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$BackupDir = Join-Path $RepoDir "build\live-runner-backups\$timestamp"
$ManifestPath = Join-Path $BackupDir 'install-manifest.json'
New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null

$files = @(
    'run_codex_with_timeout.ps1',
    'news-grasp-bootstrap.ps1',
    'news-grasp-runner.ps1',
    'watch-news-grasp-runner.ps1',
    'news-grasp-deadman.ps1',
    'news-grasp-deadman-launcher.pyw',
    'news-grasp-task-launcher.pyw'
)

$manifestFiles = @()
foreach ($file in $files) {
    $source = Join-Path $ops $file
    $destination = Join-Path $BinDir $file
    $backup = Join-Path $BackupDir $file
    $beforeHash = ''
    if (Test-Path -LiteralPath $destination) {
        Copy-Item -LiteralPath $destination -Destination $backup -Force
        $beforeHash = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash
    }
    Copy-Item -LiteralPath $source -Destination $destination -Force
    $afterHash = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash
    $sourceHash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
    $manifestFiles += [ordered]@{
        file = $file
        source = $source
        destination = $destination
        backup = if (Test-Path -LiteralPath $backup) { $backup } else { '' }
        before_sha256 = $beforeHash
        source_sha256 = $sourceHash
        after_sha256 = $afterHash
    }
}

$rollbackCommands = @(
    "Copy-Item -LiteralPath `"$BackupDir\run_codex_with_timeout.ps1`" -Destination `"$BinDir\run_codex_with_timeout.ps1`" -Force",
    "Copy-Item -LiteralPath `"$BackupDir\news-grasp-bootstrap.ps1`" -Destination `"$BinDir\news-grasp-bootstrap.ps1`" -Force",
    "Copy-Item -LiteralPath `"$BackupDir\news-grasp-runner.ps1`" -Destination `"$BinDir\news-grasp-runner.ps1`" -Force",
    "Copy-Item -LiteralPath `"$BackupDir\watch-news-grasp-runner.ps1`" -Destination `"$BinDir\watch-news-grasp-runner.ps1`" -Force",
    "Copy-Item -LiteralPath `"$BackupDir\news-grasp-deadman.ps1`" -Destination `"$BinDir\news-grasp-deadman.ps1`" -Force",
    "Copy-Item -LiteralPath `"$BackupDir\news-grasp-deadman-launcher.pyw`" -Destination `"$BinDir\news-grasp-deadman-launcher.pyw`" -Force",
    "Copy-Item -LiteralPath `"$BackupDir\news-grasp-task-launcher.pyw`" -Destination `"$BinDir\news-grasp-task-launcher.pyw`" -Force"
)

$scheduledTasks = @()
if (-not $SkipTaskRegistration) {
    $watcherPath = Join-Path $BinDir 'watch-news-grasp-runner.ps1'
    $bootstrapPath = Join-Path $BinDir 'news-grasp-bootstrap.ps1'
    $deadmanLauncherPath = Join-Path $BinDir 'news-grasp-deadman-launcher.pyw'
    $taskLauncherPath = Join-Path $BinDir 'news-grasp-task-launcher.pyw'
    $pythonw = Join-Path $RepoDir '.venv\Scripts\pythonw.exe'
    if (-not (Test-Path -LiteralPath $pythonw)) { throw 'News-Grasp .venv pythonw.exe が見つかりません。' }
    $existingRunner = Get-ScheduledTask -TaskName $RunnerTaskName -ErrorAction SilentlyContinue
    $runnerWasEnabled = $existingRunner -and $existingRunner.Settings.Enabled
    $existingBootstrap = Get-ScheduledTask -TaskName $BootstrapTaskName -ErrorAction SilentlyContinue
    $bootstrapWasEnabled = $existingBootstrap -and $existingBootstrap.Settings.Enabled

    $runnerArgs = "`"$taskLauncherPath`" runner"
    $runnerAction = New-ScheduledTaskAction -Execute $pythonw -Argument $runnerArgs -WorkingDirectory $BinDir
    $runnerTrigger = New-ScheduledTaskTrigger -Daily -At 6:00am
    $runnerSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew
    $runnerRegistered = $false
    $runnerRegisterError = ''
    try {
        if ($existingRunner) {
            Set-ScheduledTask -TaskName $RunnerTaskName -Action $runnerAction -ErrorAction Stop | Out-Null
        } else {
            Register-ScheduledTask -TaskName $RunnerTaskName -Action $runnerAction -Trigger $runnerTrigger -Settings $runnerSettings -Description 'News-Grasp daily runner bootstrap. Repairs live ops from repo before starting runner.' -Force -ErrorAction Stop | Out-Null
        }
        if (-not $runnerWasEnabled) { Disable-ScheduledTask -TaskName $RunnerTaskName -ErrorAction Stop | Out-Null }
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

    $bootstrapArgs = "`"$taskLauncherPath`" bootstrap"
    $bootstrapAction = New-ScheduledTaskAction -Execute $pythonw -Argument $bootstrapArgs -WorkingDirectory $BinDir
    $bootstrapTrigger = New-ScheduledTaskTrigger -Daily -At 5:55am
    $bootstrapSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew
    try {
        Register-ScheduledTask -TaskName $BootstrapTaskName -Action $bootstrapAction -Trigger $bootstrapTrigger -Settings $bootstrapSettings -Description 'News-Grasp pre-run self repair bootstrap.' -Force -ErrorAction Stop | Out-Null
        if (-not $bootstrapWasEnabled) { Disable-ScheduledTask -TaskName $BootstrapTaskName | Out-Null }
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

    & schtasks.exe /Query /TN $DeadmanTaskName | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $scheduledTasks += [ordered]@{
            task_name = $DeadmanTaskName
            execute = $pythonw
            arguments = "`"$deadmanLauncherPath`""
            trigger = 'existing'
            status = 'already_registered'
        }
    } else {
        & schtasks.exe /Create /TN $DeadmanTaskName /SC HOURLY /MO 1 /ST 06:40 /TR "`"$pythonw`" `"$deadmanLauncherPath`"" /F | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "failed to create $DeadmanTaskName"
        }
        $scheduledTasks += [ordered]@{
            task_name = $DeadmanTaskName
            execute = $pythonw
            arguments = "`"$deadmanLauncherPath`""
            trigger = 'hourly from 06:40'
            status = 'registered_deadman'
        }
    }
}

[ordered]@{
    created_at = (Get-Date).ToString('yyyy-MM-ddTHH:mm:ss.fffK')
    repo_dir = $RepoDir
    bin_dir = $BinDir
    backup_dir = $BackupDir
    files = $manifestFiles
    rollback_commands = $rollbackCommands
    scheduled_tasks = $scheduledTasks
} | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ManifestPath -Encoding UTF8

Write-Host "News-Grasp ops scripts installed to $BinDir"
Write-Host "Backup manifest: $ManifestPath"
if ((-not $SkipTaskRegistration) -and (-not $runnerRegistered)) {
    throw "failed to converge $RunnerTaskName action: $runnerRegisterError"
}
