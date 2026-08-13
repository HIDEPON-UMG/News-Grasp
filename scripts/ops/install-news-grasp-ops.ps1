param(
    [string] $RepoDir = '',
    [string] $BinDir = (Join-Path $env:USERPROFILE 'bin'),
    [string] $TaskPythonwPath = '',
    [string] $EvidenceRepoDir = '',
    [string] $RunnerTaskName = 'News-Grasp Production',
    [string] $BootstrapTaskName = 'News-Grasp Bootstrap',
    [string] $DeadmanTaskName = 'News-Grasp Deadman',
    [string] $LegacyRunnerTaskName = 'News-Grasp Runner',
    [switch] $SkipTaskRegistration
)

$ErrorActionPreference = 'Stop'
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$script:InstallationCommitted = $false
$script:InstallationMutationStarted = $false
$script:DeliveryReceiptSummary = $null
$missionAuthorityPath = ''

. (Join-Path $PSScriptRoot 'install-news-grasp-ops-guard.ps1')

function Write-AtomicUtf8Text {
    param([string] $Path, [string] $Text)
    $parent = Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    Write-NewsGraspAtomicFile `
        -Path $Path `
        -TrustedBoundary $parent `
        -Bytes ([Text.UTF8Encoding]::new($false).GetBytes($Text)) | Out-Null
}

function Assert-NewsGraspAssetRelativePath {
    param([string] $Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { throw 'NEWS_GRASP_ASSET_PATH_INVALID' }
    $normalized = $Value.Replace('/', '\')
    if ([IO.Path]::IsPathRooted($normalized) -or $normalized.StartsWith('\\')) {
        throw 'NEWS_GRASP_ASSET_ABSOLUTE_PATH'
    }
    foreach ($part in $normalized.Split('\')) {
        if ([string]::IsNullOrWhiteSpace($part) -or $part -eq '.' -or $part -eq '..' -or $part.Contains(':')) {
            throw 'NEWS_GRASP_ASSET_RELATIVE_PATH_INVALID'
        }
    }
    return $normalized
}

function Assert-NewsGraspAssetInstallDestination {
    param(
        [Parameter(Mandatory = $true)][string] $DestinationPath,
        [Parameter(Mandatory = $true)][string] $CanonicalBinDir
    )
    $canonicalDestination = Get-NewsGraspCanonicalPath -Path $DestinationPath
    $canonicalBin = Get-NewsGraspCanonicalPath -Path $CanonicalBinDir
    $prefix = $canonicalBin + [IO.Path]::DirectorySeparatorChar
    if (-not $canonicalDestination.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'NEWS_GRASP_ASSET_INSTALL_DESTINATION_INVALID'
    }
    Assert-NewsGraspNoReparsePath -Path (Split-Path -Parent $canonicalDestination) -Boundary $canonicalBin
}

function Resolve-NewsGraspWorkspaceHarnessRoot {
    param([Parameter(Mandatory = $true)][string] $StartPath)
    $candidate = Get-NewsGraspCanonicalPath -Path $StartPath
    for ($depth = 0; $depth -lt 6; $depth += 1) {
        if (
            (Test-Path -LiteralPath (Join-Path $candidate 'tools\harness\task_model_routing.py') -PathType Leaf) -and
            (Test-Path -LiteralPath (Join-Path $candidate 'docs\harness\high_cost_model_routes_v1.json') -PathType Leaf)
        ) {
            return $candidate
        }
        $parent = Split-Path -Parent $candidate
        if (-not $parent -or (Test-NewsGraspSamePath -Left $parent -Right $candidate)) { break }
        $candidate = Get-NewsGraspCanonicalPath -Path $parent
    }
    throw 'NEWS_GRASP_SHARED_HARNESS_ROOT_UNAVAILABLE'
}

function Assert-NewsGraspSharedBrokerGeneration {
    param([Parameter(Mandatory = $true)][string] $ResolvedRepoDir)
    $brokerPath = Join-Path $env:USERPROFILE 'bin\ai-model-spawn-broker.py'
    $brokerBoundary = Join-Path $env:USERPROFILE 'bin'
    if (-not (Test-Path -LiteralPath $brokerPath -PathType Leaf)) {
        throw 'NEWS_GRASP_SHARED_BROKER_GENERATION_DRIFT'
    }
    $workspaceRoot = Resolve-NewsGraspWorkspaceHarnessRoot -StartPath $ResolvedRepoDir
    $brokerFile = Read-NewsGraspVerifiedFile `
        -Path $brokerPath `
        -TrustedBoundary $brokerBoundary `
        -MaxBytes 262144 `
        -RequireSingleLink
    $brokerText = [Text.Encoding]::UTF8.GetString($brokerFile.Bytes)
    $bindings = @(
        [ordered]@{ name = 'HIGH_COST_CONTROL_SHA256'; path = 'tools\harness\high_cost_control_v2.py' },
        [ordered]@{ name = 'ROUTE_REGISTRY_SHA256'; path = 'docs\harness\high_cost_model_routes_v1.json' },
        [ordered]@{ name = 'TASK_MODEL_ROUTING_SHA256'; path = 'tools\harness\task_model_routing.py' },
        [ordered]@{ name = 'RUNTIME_ASSET_COMPATIBILITY_SHA256'; path = 'tools\harness\codex_runtime_asset_compatibility.py' }
    )
    foreach ($binding in $bindings) {
        $sourcePath = Join-Path $workspaceRoot ([string]$binding.path)
        if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
            throw 'NEWS_GRASP_SHARED_BROKER_GENERATION_DRIFT'
        }
        $actualHash = (Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash.ToLowerInvariant()
        $pattern = '(?ms)' + [regex]::Escape([string]$binding.name) + '\s*=\s*\(\s*"([0-9a-f]{64})"\s*\)'
        $match = [regex]::Match($brokerText, $pattern)
        if (-not $match.Success -or $match.Groups[1].Value.ToLowerInvariant() -ne $actualHash) {
            throw 'NEWS_GRASP_SHARED_BROKER_GENERATION_DRIFT'
        }
    }
    return $true
}

function Assert-NewsGraspExternalControlPlaneReady {
    param(
        [Parameter(Mandatory = $true)][string] $ResolvedRepoDir,
        [Parameter(Mandatory = $true)][string] $ResolvedTaskPythonwPath
    )
    # 外部制御面はproductのwrite set外。固定pathのpure probeがRedなら、
    # runtime/task promotionを開始せずtyped deferredへ分岐する。
    $pythonExe = Join-Path (Split-Path -Parent $ResolvedTaskPythonwPath) 'python.exe'
    $probeScript = Join-Path $ResolvedRepoDir 'tools\news_grasp_external_control.py'
    if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf) -or
        -not (Test-Path -LiteralPath $probeScript -PathType Leaf)) {
        throw 'operation_deferred_external_dependency:EXTERNAL_CONTROL_PLANE_UNAVAILABLE'
    }
    $probeOutput = (& $pythonExe '-I' $probeScript 'probe' 2>&1 | Out-String).Trim()
    $probeExit = $LASTEXITCODE
    $probe = $null
    try { $probe = $probeOutput | ConvertFrom-Json -ErrorAction Stop } catch { $probe = $null }
    if ($probeExit -ne 0 -or -not $probe -or [string]$probe.status -cne 'ready') {
        $reason = if ($probe) { [string]$probe.reasonCode } else { 'EXTERNAL_CONTROL_PLANE_UNAVAILABLE' }
        throw "operation_deferred_external_dependency:$reason"
    }
    return $probe
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
    $journalDirectory = Split-Path -Parent $JournalPath
    foreach ($row in @($Journal.files) | Select-Object -Last 100) {
        if ([string]$row.backup -and (Test-Path -LiteralPath ([string]$row.backup) -PathType Leaf)) {
            Restore-NewsGraspVerifiedFile `
                -BackupPath ([string]$row.backup) `
                -DestinationPath ([string]$row.destination) `
                -BackupBoundary $journalDirectory `
                -DestinationBoundary ([string]$Journal.bin_dir)
        } elseif (Test-Path -LiteralPath ([string]$row.destination) -PathType Leaf) {
            Remove-NewsGraspVerifiedFile `
                -Path ([string]$row.destination) `
                -TrustedBoundary ([string]$Journal.bin_dir)
        }
    }
    foreach ($snapshot in @($Journal.task_snapshots)) {
        $taskName = [string]$snapshot.task_name
        $currentTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        $taskNeedsRestore = $true
        if ([bool]$snapshot.existed_before) {
            $xml = Read-NewsGraspVerifiedTaskXml `
                -Path ([string]$snapshot.xml_backup) `
                -TrustedBoundary $journalDirectory `
                -ExpectedSha256 ([string]$snapshot.xml_backup_sha256)
            if ($currentTask) {
                $currentXml = Export-ScheduledTask -TaskName $taskName
                if (
                    $currentXml.Trim() -eq $xml.Trim() -and
                    [bool]$currentTask.Settings.Enabled -eq [bool]$snapshot.enabled_before
                ) {
                    $taskNeedsRestore = $false
                }
            }
            if (-not $taskNeedsRestore) { continue }
            Register-ScheduledTask -TaskName $taskName -Xml $xml -Force -ErrorAction Stop | Out-Null
            if ([bool]$snapshot.enabled_before) {
                Enable-ScheduledTask -TaskName $taskName -ErrorAction Stop | Out-Null
            } else {
                Disable-ScheduledTask -TaskName $taskName -ErrorAction Stop | Out-Null
            }
        } elseif (-not $currentTask) {
            $taskNeedsRestore = $false
            if (-not $taskNeedsRestore) { continue }
        } else {
            Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction Stop
        }
    }
    $Journal.phase = 'rolled_back'
    $Journal | Add-Member -NotePropertyName 'rolled_back_at' -NotePropertyValue ((Get-Date).ToString('yyyy-MM-ddTHH:mm:ss.fffK')) -Force
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
            Where-Object { $_.Name -match '^\d{8}-\d{6}$' } |
            Sort-Object LastWriteTime
    )
    foreach ($transactionDir in $transactionDirs) {
        Assert-NewsGraspNoReparsePath -Path $transactionDir.FullName -Boundary $BackupRoot
        $journalPath = Join-Path $transactionDir.FullName 'install-manifest.json'
        if (-not (Test-Path -LiteralPath $journalPath -PathType Leaf)) {
            # prepared journalより前にはlive mutationを開始しない契約なので、
            # backup途中のorphanは証拠として保持し、次transactionを妨げない。
            Write-Warning ("journal作成前に停止したpre-mutation backupを保持します: {0}" -f $transactionDir.FullName)
            continue
        }
        try {
            $journalFile = Read-NewsGraspVerifiedFile `
                -Path $journalPath `
                -TrustedBoundary $BackupRoot `
                -RequireSingleLink
            $journal = [Text.Encoding]::UTF8.GetString($journalFile.Bytes) | ConvertFrom-Json
        } catch {
            throw 'NEWS_GRASP_INSTALL_JOURNAL_INGEST_INVALID'
        }
        if (
            [string]$journal.schemaVersion -ne 'NEWS_GRASP_OPS_INSTALL_JOURNAL_V1' -or
            [string]$journal.transaction_id -ne $transactionDir.Name -or
            [string]$journal.phase -notin @(
                'prepared', 'files_installed', 'authority_issued',
                'tasks_converged', 'verified', 'committed', 'rolled_back'
            )
        ) {
            throw 'NEWS_GRASP_INSTALL_JOURNAL_INGEST_INVALID'
        }
        if ([string]$journal.phase -notin @('committed', 'rolled_back')) {
            Assert-NewsGraspRecoveryJournal `
                -JournalPath $journalPath `
                -Journal $journal `
                -ExpectedBackupRoot $BackupRoot `
                -ExpectedRepoDir $ExpectedRepoDir `
                -ExpectedBinDir $ExpectedBinDir `
                -ExpectedTaskNames $ExpectedTaskNames | Out-Null
            Invoke-NewsGraspRollbackJournal -JournalPath $journalPath -Journal $journal
        }
    }
}

function Invoke-NewsGraspInstallRollback {
    foreach ($row in @($manifestFiles) | Select-Object -Last 100) {
        if ([string]$row.backup -and (Test-Path -LiteralPath ([string]$row.backup) -PathType Leaf)) {
            Restore-NewsGraspVerifiedFile `
                -BackupPath ([string]$row.backup) `
                -DestinationPath ([string]$row.destination) `
                -BackupBoundary $BackupDir `
                -DestinationBoundary $BinDir
        } elseif (Test-Path -LiteralPath ([string]$row.destination) -PathType Leaf) {
            Remove-NewsGraspVerifiedFile `
                -Path ([string]$row.destination) `
                -TrustedBoundary $BinDir
        }
    }
    foreach ($snapshot in $taskSnapshots) {
        $taskName = [string]$snapshot.task_name
        $currentTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        $taskNeedsRestore = $true
        if ([bool]$snapshot.existed_before) {
            $xml = Read-NewsGraspVerifiedTaskXml `
                -Path ([string]$snapshot.xml_backup) `
                -TrustedBoundary $BackupDir `
                -ExpectedSha256 ([string]$snapshot.xml_backup_sha256)
            if ($currentTask) {
                $currentXml = Export-ScheduledTask -TaskName $taskName
                if (
                    $currentXml.Trim() -eq $xml.Trim() -and
                    [bool]$currentTask.Settings.Enabled -eq [bool]$snapshot.enabled_before
                ) {
                    $taskNeedsRestore = $false
                }
            }
            if (-not $taskNeedsRestore) { continue }
            Register-ScheduledTask -TaskName $taskName -Xml $xml -Force -ErrorAction Stop | Out-Null
            if ([bool]$snapshot.enabled_before) {
                Enable-ScheduledTask -TaskName $taskName -ErrorAction Stop | Out-Null
            } else {
                Disable-ScheduledTask -TaskName $taskName -ErrorAction Stop | Out-Null
            }
        } elseif (-not $currentTask) {
            $taskNeedsRestore = $false
            if (-not $taskNeedsRestore) { continue }
        } else {
            Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction Stop
        }
    }
}

function Write-NewsGraspInstallJournal {
    param([string] $Phase)
    $missionSha = ''
    if ($missionAuthorityPath -and (Test-Path -LiteralPath $missionAuthorityPath -PathType Leaf)) {
        $missionSnapshot = Read-NewsGraspVerifiedFile `
            -Path $missionAuthorityPath `
            -TrustedBoundary $BinDir `
            -RequireSingleLink
        $missionSha = [string]$missionSnapshot.Sha256
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
        delivery_state = $script:DeliveryReceiptSummary
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
        $sourceFile = Read-NewsGraspVerifiedFile `
            -Path $source `
            -TrustedBoundary $RepoDir `
            -RequireSingleLink
        $installedFile = Read-NewsGraspVerifiedFile `
            -Path $destination `
            -TrustedBoundary $BinDir `
            -RequireSingleLink
        $sourceSha = [string]$sourceFile.Sha256
        $installedSha = [string]$installedFile.Sha256
        if ($sourceSha -ne $installedSha) { throw "installed file hash mismatch: $file" }
    }
    $resolverSource = Read-NewsGraspVerifiedFile `
        -Path $highCostBindingToolPath `
        -TrustedBoundary $RepoDir `
        -RequireSingleLink
    $resolverInstalled = Read-NewsGraspVerifiedFile `
        -Path $highCostBindingResolverDestination `
        -TrustedBoundary $BinDir `
        -RequireSingleLink
    if ([string]$resolverSource.Sha256 -ne [string]$resolverInstalled.Sha256) {
        throw 'installed high-cost binding resolver hash mismatch'
    }
    $missionFile = Read-NewsGraspVerifiedFile `
        -Path $missionAuthorityPath `
        -TrustedBoundary $BinDir `
        -RequireSingleLink
    $mission = [Text.Encoding]::UTF8.GetString($missionFile.Bytes) | ConvertFrom-Json
    $missionSchema = [string]$(if ($mission.schemaVersion) { $mission.schemaVersion } else { $mission.schema })
    if ($missionSchema -ne 'AUDIT_MISSION_AUTHORITY_V1') { throw 'audit mission authority schema mismatch' }
    $runtimeRootFile = Read-NewsGraspVerifiedFile `
        -Path $runtimeRootPath `
        -TrustedBoundary $canonicalBinDir `
        -RequireSingleLink
    $runtimeRoot = [Text.Encoding]::UTF8.GetString($runtimeRootFile.Bytes) | ConvertFrom-Json
    if (
        [string]$runtimeRoot.schemaVersion -ne 'NEWS_GRASP_RUNTIME_ROOT_V1' -or
        -not [string]::Equals(
            [System.IO.Path]::GetFullPath([string]$runtimeRoot.repoDir),
            [System.IO.Path]::GetFullPath($productionRuntimePath),
            [System.StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw 'runtime root binding mismatch'
    }
    $recoveryBindingFile = Read-NewsGraspVerifiedFile `
        -Path $recoveryRuntimeBindingPath `
        -TrustedBoundary $canonicalBinDir `
        -RequireSingleLink
    $recoveryBinding = [Text.Encoding]::UTF8.GetString($recoveryBindingFile.Bytes) | ConvertFrom-Json
    if (
        [string]$recoveryBinding.schemaVersion -ne 'NEWS_GRASP_RECOVERY_RUNTIME_BINDING_V1' -or
        -not (Test-NewsGraspSamePath -Left ([string]$recoveryBinding.opsRepoRoot) -Right $runtimeEvidenceRepoDir) -or
        -not (Test-NewsGraspSamePath -Left ([string]$recoveryBinding.productionRuntimeRoot) -Right $productionRuntimePath) -or
        -not (Test-NewsGraspSamePath -Left ([string]$recoveryBinding.pythonExe) -Right $runtimePythonPath) -or
        -not (Test-NewsGraspSamePath -Left ([string]$recoveryBinding.highCostBindingPath) -Right $highCostBindingPath) -or
        -not (Test-NewsGraspSamePath -Left ([string]$recoveryBinding.highCostBindingResolverPath) -Right $highCostBindingResolverDestination) -or
        [string]$recoveryBinding.highCostBindingReceiptSha256 -cne $highCostBindingReceiptSha256
    ) {
        throw 'recovery runtime binding mismatch'
    }
    foreach ($bindingHash in @(
        [string]$recoveryBinding.pythonExeSha256,
        [string]$recoveryBinding.receiptToolSha256,
        [string]$recoveryBinding.controlPlaneToolSha256,
        [string]$recoveryBinding.completionGuardToolSha256,
        [string]$recoveryBinding.dailySelfHealSha256,
        [string]$recoveryBinding.highCostBindingReceiptSha256,
        [string]$recoveryBinding.highCostBindingFileSha256,
        [string]$recoveryBinding.highCostBindingResolverSha256,
        [string]$recoveryBinding.bootstrapSha256,
        [string]$recoveryBinding.runnerSha256
    )) {
        if ($bindingHash -notmatch '^[0-9a-f]{64}$') { throw 'recovery runtime binding hash invalid' }
    }
    if ($SkipTaskRegistration) { return }
    $expected = @(
        [ordered]@{ name = $RunnerTaskName; execute = $pythonw; arguments = $runnerArgs; working = $BinDir; start = 'T06:00'; interval = ''; duration = '' },
        [ordered]@{ name = $BootstrapTaskName; execute = $pythonw; arguments = $bootstrapArgs; working = $BinDir; start = 'T05:55'; interval = ''; duration = '' },
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
    $legacyTask = Get-ScheduledTask -TaskName $LegacyRunnerTaskName -ErrorAction SilentlyContinue
    if ($legacyTask -and $legacyTask.Settings.Enabled) {
        throw "legacy task remains enabled: $LegacyRunnerTaskName"
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
$automationAssetManifestPath = Join-Path $RepoDir 'config\news_grasp_automation_assets_v2.json'
if (-not (Test-Path -LiteralPath $automationAssetManifestPath -PathType Leaf)) {
    throw 'NEWS_GRASP_AUTOMATION_ASSET_MANIFEST_MISSING'
}
try {
    $automationAssetManifest = Get-Content -LiteralPath $automationAssetManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
} catch {
    throw 'NEWS_GRASP_AUTOMATION_ASSET_MANIFEST_INVALID'
}
if (
    [string]$automationAssetManifest.schemaVersion -ne 'NEWS_GRASP_AUTOMATION_ASSET_MANIFEST_V2' -or
    [string]$automationAssetManifest.productId -ne 'News-Grasp' -or
    [string]$automationAssetManifest.installRoot -ne 'news-grasp-assets'
) {
    throw 'NEWS_GRASP_AUTOMATION_ASSET_MANIFEST_INVALID'
}
$automationAssetRows = @($automationAssetManifest.assets)
if (-not $automationAssetRows.Count) { throw 'NEWS_GRASP_AUTOMATION_ASSET_MANIFEST_EMPTY' }
$automationAssetIds = @{}
$automationAssetSourcePaths = @{}
$automationAssetInstallPaths = @{}
foreach ($asset in $automationAssetRows) {
    $assetId = [string]$asset.assetId
    if ([string]::IsNullOrWhiteSpace($assetId) -or $automationAssetIds.ContainsKey($assetId)) {
        throw 'NEWS_GRASP_AUTOMATION_ASSET_DUPLICATE_ID'
    }
    $automationAssetIds[$assetId] = $true
    if ([string]$asset.kind -notin @('skill', 'guard', 'automation')) {
        throw 'NEWS_GRASP_AUTOMATION_ASSET_KIND_INVALID'
    }
    $asset.sourcePath = Assert-NewsGraspAssetRelativePath ([string]$asset.sourcePath)
    $asset.installPath = Assert-NewsGraspAssetRelativePath ([string]$asset.installPath)
    if ($automationAssetSourcePaths.ContainsKey([string]$asset.sourcePath)) {
        throw 'NEWS_GRASP_AUTOMATION_ASSET_DUPLICATE_SOURCE'
    }
    if ($automationAssetInstallPaths.ContainsKey([string]$asset.installPath)) {
        throw 'NEWS_GRASP_AUTOMATION_ASSET_DUPLICATE_INSTALL_PATH'
    }
    $automationAssetSourcePaths[[string]$asset.sourcePath] = $true
    $automationAssetInstallPaths[[string]$asset.installPath] = $true
}
$TaskPythonwPath = Resolve-NewsGraspTaskPythonw -Override $TaskPythonwPath -ResolvedRepoDir $RepoDir
# runtime/task/asset の決定論的promotionはexternal model readinessと分離する。
# modelを必要とするrunner stageは、tools.news_grasp_daily_controlのpure probeで
# external authorityをfail-closedに検証し、unavailableならtyped deferredへ遷移する。
$ops = Join-Path $RepoDir 'scripts\ops'
$installTrustedBoundary = (Resolve-Path -LiteralPath $env:USERPROFILE).Path
$canonicalBinDir = Join-Path $installTrustedBoundary 'bin'
$managedTaskNames = @($RunnerTaskName, $BootstrapTaskName, $DeadmanTaskName, $LegacyRunnerTaskName)
$runtimeRootAuthoritySha = Assert-NewsGraspCanonicalInstallSource `
    -ResolvedRepoDir $RepoDir `
    -RequestedBinDir $BinDir `
    -CanonicalBinDir $canonicalBinDir `
    -TrustedBoundary $installTrustedBoundary `
    -ManagedTaskNames $managedTaskNames
$runtimeEvidenceRepoDir = if ($EvidenceRepoDir) {
    if (-not (Test-Path -LiteralPath $EvidenceRepoDir -PathType Container)) {
        throw "指定されたEvidence repoが見つかりません: $EvidenceRepoDir"
    }
    (Resolve-Path -LiteralPath $EvidenceRepoDir).Path
} else {
    throw 'NEWS_GRASP_EVIDENCE_REPO_REQUIRED'
}
Assert-NewsGraspNoReparsePath -Path $runtimeEvidenceRepoDir -Boundary $installTrustedBoundary
if (Test-NewsGraspSamePath -Left $runtimeEvidenceRepoDir -Right $RepoDir) {
    throw 'NEWS_GRASP_EVIDENCE_REPO_SELF_REFERENCE_FORBIDDEN'
}
$existingRuntimeRootPath = Join-Path $canonicalBinDir 'news-grasp-runtime-root-v1.json'
if (Test-Path -LiteralPath $existingRuntimeRootPath -PathType Leaf) {
    $existingRuntimeRootSnapshot = Read-NewsGraspVerifiedFile `
        -Path $existingRuntimeRootPath `
        -TrustedBoundary $canonicalBinDir `
        -MaxBytes 65536 `
        -RequireSingleLink
    if ([string]$existingRuntimeRootSnapshot.Sha256 -cne [string]$runtimeRootAuthoritySha) {
        throw 'NEWS_GRASP_EVIDENCE_REPO_GENERATION_DRIFT'
    }
    $existingRuntimeRoot = [Text.Encoding]::UTF8.GetString($existingRuntimeRootSnapshot.Bytes) | ConvertFrom-Json
    if (
        $existingRuntimeRoot.evidenceRepoDir -and
        -not (Test-NewsGraspSamePath -Left $runtimeEvidenceRepoDir -Right ([string]$existingRuntimeRoot.evidenceRepoDir))
    ) {
        # generation更新時はevidence pathが変わり得る。旧rootとのpath一致だけで
        # fail-closeせず、新evidence自身をcandidateと同じGit generationとして再検証する。
        $evidenceGenerationGreen = Test-NewsGraspPromotableInstallSource `
            -CurrentRepoDir $runtimeEvidenceRepoDir `
            -CandidateRepoDir $RepoDir `
            -TrustedBoundary $installTrustedBoundary
        if (-not $evidenceGenerationGreen) {
            throw 'NEWS_GRASP_EVIDENCE_REPO_GENERATION_DRIFT'
        }
    }
}
$sourceSnapshots = @{}
foreach ($file in $files) {
    $sourceSnapshots[$file] = Read-NewsGraspVerifiedFile `
        -Path (Join-Path $ops $file) `
        -TrustedBoundary $RepoDir `
        -RequireSingleLink
}
$assetSourceSnapshots = @{}
foreach ($asset in $automationAssetRows) {
    $assetSourceSnapshots[[string]$asset.assetId] = Read-NewsGraspVerifiedFile `
        -Path (Join-Path $RepoDir ([string]$asset.sourcePath)) `
        -TrustedBoundary $RepoDir `
        -RequireSingleLink
}
$backupRoot = Join-Path $RepoDir 'build\live-runner-backups'
$null = Assert-NewsGraspCanonicalInstallSource `
    -ResolvedRepoDir $RepoDir `
    -RequestedBinDir $BinDir `
    -CanonicalBinDir $canonicalBinDir `
    -TrustedBoundary $installTrustedBoundary `
    -ExpectedRuntimeRootSha256 $runtimeRootAuthoritySha `
    -ManagedTaskNames $managedTaskNames
Recover-NewsGraspInterruptedInstall `
    -BackupRoot $backupRoot `
    -ExpectedRepoDir $RepoDir `
    -ExpectedBinDir $BinDir `
    -ExpectedTaskNames $managedTaskNames
$runtimeRootAuthoritySha = Assert-NewsGraspCanonicalInstallSource `
    -ResolvedRepoDir $RepoDir `
    -RequestedBinDir $BinDir `
    -CanonicalBinDir $canonicalBinDir `
    -TrustedBoundary $installTrustedBoundary `
    -ManagedTaskNames $managedTaskNames
$binDirExistedBefore = Test-Path -LiteralPath $BinDir -PathType Container

# backup + explicit approval + rollback: live runner overwrite must leave a restorable manifest.
$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$BackupDir = Join-Path $backupRoot $timestamp
$ManifestPath = Join-Path $BackupDir 'install-manifest.json'
$taskSnapshots = @()
$manifestFiles = @()
$destinationSnapshots = @{}
$assetDestinationSnapshots = @{}
$null = Assert-NewsGraspCanonicalInstallSource `
    -ResolvedRepoDir $RepoDir `
    -RequestedBinDir $BinDir `
    -CanonicalBinDir $canonicalBinDir `
    -TrustedBoundary $installTrustedBoundary `
    -ExpectedRuntimeRootSha256 $runtimeRootAuthoritySha `
    -ManagedTaskNames $managedTaskNames
foreach ($file in $files) {
    $destination = Join-Path $BinDir $file
    if (Test-Path -LiteralPath $destination) {
        Assert-NewsGraspInstallDestination -DestinationPath $destination -CanonicalBinDir $canonicalBinDir
        $destinationSnapshots[$file] = Read-NewsGraspVerifiedFile `
            -Path $destination `
            -TrustedBoundary $canonicalBinDir
    }
}
$assetInstallRoot = Join-Path $BinDir ([string]$automationAssetManifest.installRoot)
foreach ($asset in $automationAssetRows) {
    $assetId = [string]$asset.assetId
    $destination = Join-Path $assetInstallRoot ([string]$asset.installPath)
    if (Test-Path -LiteralPath $destination) {
        Assert-NewsGraspAssetInstallDestination -DestinationPath $destination -CanonicalBinDir $canonicalBinDir
        $assetDestinationSnapshots[$assetId] = Read-NewsGraspVerifiedFile `
            -Path $destination `
            -TrustedBoundary $canonicalBinDir
    }
}
New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null

# Global側のside-effect-free adapterをinstall時に解決し、product-local bindingを
# live mutationより先に生成・検証する。authority/budget/terminal stateは複製しない。
$workspaceHarnessRoot = Resolve-NewsGraspWorkspaceHarnessRoot -StartPath $RepoDir
$highCostAdapterPath = Join-Path $workspaceHarnessRoot 'tools\harness\high_cost_capability_adapter.py'
$highCostDescriptorPath = Join-Path $env:USERPROFILE '.codex\state\high-cost-operation\capability-v1.json'
$highCostBindingToolPath = Join-Path $RepoDir 'tools\news_grasp_high_cost_binding.py'
$highCostBindingResolverDestination = Join-Path $BinDir 'news_grasp_high_cost_binding.py'
$highCostBindingCandidatePath = Join-Path $BackupDir 'news-grasp-high-cost-binding-v1.candidate.json'
$installerPythonPath = Join-Path (Split-Path -Parent $TaskPythonwPath) 'python.exe'
foreach ($requiredHighCostFile in @($highCostAdapterPath, $highCostDescriptorPath, $highCostBindingToolPath, $installerPythonPath)) {
    if (-not (Test-Path -LiteralPath $requiredHighCostFile -PathType Leaf)) {
        throw 'HIGH_COST_WORKSPACE_BINDING_MISSING'
    }
}
$highCostBindingJson = (& $installerPythonPath '-I' '-S' '-B' $highCostBindingToolPath 'create' '--adapter' $highCostAdapterPath '--descriptor' $highCostDescriptorPath '--output' $highCostBindingCandidatePath 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { throw "HIGH_COST_WORKSPACE_BINDING_MISSING detail=$highCostBindingJson" }
try { $highCostBinding = $highCostBindingJson | ConvertFrom-Json -ErrorAction Stop } catch { throw 'HIGH_COST_WORKSPACE_BINDING_MISSING' }
if (
    [string]$highCostBinding.schemaVersion -cne 'NEWS_GRASP_HIGH_COST_BINDING_V1' -or
    [string]$highCostBinding.bindingReceiptSha256 -notmatch '^[0-9a-f]{64}$'
) { throw 'HIGH_COST_WORKSPACE_BINDING_MISSING' }
$highCostBindingReceiptSha256 = [string]$highCostBinding.bindingReceiptSha256
$highCostBindingResolverSource = Read-NewsGraspVerifiedFile -Path $highCostBindingToolPath -TrustedBoundary $RepoDir -RequireSingleLink
$highCostBindingResolverBackup = Join-Path $BackupDir 'news_grasp_high_cost_binding.py.before'
$highCostBindingResolverBeforeHash = ''
if (Test-Path -LiteralPath $highCostBindingResolverDestination -PathType Leaf) {
    $highCostBindingResolverBefore = Read-NewsGraspVerifiedFile -Path $highCostBindingResolverDestination -TrustedBoundary $canonicalBinDir -RequireSingleLink
    Write-NewsGraspAtomicFile -Path $highCostBindingResolverBackup -TrustedBoundary $BackupDir -Bytes $highCostBindingResolverBefore.Bytes | Out-Null
    $highCostBindingResolverBeforeHash = [string]$highCostBindingResolverBefore.Sha256
}
$highCostBindingResolverRow = [ordered]@{
    file = 'news_grasp_high_cost_binding.py'
    source = $highCostBindingToolPath
    destination = $highCostBindingResolverDestination
    backup = if ($highCostBindingResolverBeforeHash) { $highCostBindingResolverBackup } else { '' }
    before_sha256 = $highCostBindingResolverBeforeHash
    source_sha256 = [string]$highCostBindingResolverSource.Sha256
    after_sha256 = ''
}
$manifestFiles += $highCostBindingResolverRow
$highCostBindingPath = Join-Path $BinDir 'news-grasp-high-cost-binding-v1.json'
$highCostBindingBackup = Join-Path $BackupDir 'news-grasp-high-cost-binding-v1.before.json'
$highCostBindingBeforeHash = ''
if (Test-Path -LiteralPath $highCostBindingPath -PathType Leaf) {
    $highCostBindingBefore = Read-NewsGraspVerifiedFile -Path $highCostBindingPath -TrustedBoundary $canonicalBinDir -RequireSingleLink
    Write-NewsGraspAtomicFile -Path $highCostBindingBackup -TrustedBoundary $BackupDir -Bytes $highCostBindingBefore.Bytes | Out-Null
    $highCostBindingBeforeHash = [string]$highCostBindingBefore.Sha256
}
$highCostBindingCandidate = Read-NewsGraspVerifiedFile -Path $highCostBindingCandidatePath -TrustedBoundary $BackupDir -RequireSingleLink
$highCostBindingRow = [ordered]@{
    file = 'news-grasp-high-cost-binding-v1.json'
    source = $highCostBindingCandidatePath
    destination = $highCostBindingPath
    backup = if ($highCostBindingBeforeHash) { $highCostBindingBackup } else { '' }
    before_sha256 = $highCostBindingBeforeHash
    source_sha256 = [string]$highCostBindingCandidate.Sha256
    after_sha256 = ''
}
$manifestFiles += $highCostBindingRow

if (-not $SkipTaskRegistration) {
    foreach ($taskName in @($RunnerTaskName, $BootstrapTaskName, $DeadmanTaskName, $LegacyRunnerTaskName)) {
        $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        $xmlPath = Join-Path $BackupDir (("task-{0}.xml" -f ($taskName -replace '[^A-Za-z0-9._-]', '_')))
        $taskXmlSha256 = ''
        if ($task) {
            $taskXml = Export-ScheduledTask -TaskName $taskName
            $taskXmlSha256 = Write-NewsGraspAtomicFile `
                -Path $xmlPath `
                -TrustedBoundary $BackupDir `
                -Bytes ([Text.Encoding]::Unicode.GetBytes($taskXml))
        }
        $taskSnapshots += [ordered]@{
            task_name = $taskName
            existed_before = [bool]$task
            enabled_before = [bool]($task -and $task.Settings.Enabled)
            xml_backup = if ($task) { $xmlPath } else { '' }
            xml_backup_sha256 = $taskXmlSha256
        }
    }
}

foreach ($file in $files) {
    $source = Join-Path $ops $file
    $destination = Join-Path $BinDir $file
    $backup = Join-Path $BackupDir $file
    $beforeHash = ''
    if ($destinationSnapshots.ContainsKey($file)) {
        $destinationSnapshot = $destinationSnapshots[$file]
        Write-NewsGraspAtomicFile `
            -Path $backup `
            -TrustedBoundary $BackupDir `
            -Bytes $destinationSnapshot.Bytes | Out-Null
        $beforeHash = [string]$destinationSnapshot.Sha256
    }
    $sourceSnapshot = $sourceSnapshots[$file]
    $sourceHash = [string]$sourceSnapshot.Sha256
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
foreach ($asset in $automationAssetRows) {
    $assetId = [string]$asset.assetId
    $destination = Join-Path $assetInstallRoot ([string]$asset.installPath)
    $backup = Join-Path $BackupDir (Join-Path 'news-grasp-assets' ([string]$asset.installPath))
    $beforeHash = ''
    if ($assetDestinationSnapshots.ContainsKey($assetId)) {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $backup) | Out-Null
        Write-NewsGraspAtomicFile `
            -Path $backup `
            -TrustedBoundary $BackupDir `
            -Bytes $assetDestinationSnapshots[$assetId].Bytes | Out-Null
        $beforeHash = [string]$assetDestinationSnapshots[$assetId].Sha256
    }
    $manifestFiles += [ordered]@{
        file = ('asset:' + $assetId)
        source = (Join-Path $RepoDir ([string]$asset.sourcePath))
        destination = $destination
        backup = if ($beforeHash) { $backup } else { '' }
        before_sha256 = $beforeHash
        source_sha256 = [string]$assetSourceSnapshots[$assetId].Sha256
        after_sha256 = ''
    }
}

$authorityDir = Join-Path $BinDir 'news-grasp-authority'
$missionAuthorityPath = Join-Path $authorityDir 'audit-mission-authority-v1.json'
$missionAuthorityBackup = Join-Path $BackupDir 'audit-mission-authority-v1.json'
$missionAuthorityBeforeHash = ''
$reuseExistingMissionAuthority = $false
if (Test-Path -LiteralPath $missionAuthorityPath -PathType Leaf) {
    $missionAuthoritySnapshot = Read-NewsGraspVerifiedFile `
        -Path $missionAuthorityPath `
        -TrustedBoundary $BinDir
    Write-NewsGraspAtomicFile `
        -Path $missionAuthorityBackup `
        -TrustedBoundary $BackupDir `
        -Bytes $missionAuthoritySnapshot.Bytes | Out-Null
    $missionAuthorityBeforeHash = [string]$missionAuthoritySnapshot.Sha256
    $missionValidatorPath = Join-Path $RepoDir 'tools\news_grasp_mission_authority.py'
    $missionValidatorPython = Join-Path (Split-Path -Parent $TaskPythonwPath) 'python.exe'
    if (
        (Test-Path -LiteralPath $missionValidatorPath -PathType Leaf) -and
        (Test-Path -LiteralPath $missionValidatorPython -PathType Leaf)
    ) {
        $missionValidationJson = (& $missionValidatorPython '-I' '-B' $missionValidatorPath 'validate-existing' '--path' $missionAuthorityPath 2>&1 | Out-String).Trim()
        $missionValidationExit = $LASTEXITCODE
        try { $missionValidation = $missionValidationJson | ConvertFrom-Json -ErrorAction Stop } catch { $missionValidation = $null }
        $reuseExistingMissionAuthority = (
            $missionValidationExit -eq 0 -and
            $missionValidation -and
            [string]$missionValidation.status -ceq 'Green' -and
            [string]$missionValidation.fileSha256 -ceq $missionAuthorityBeforeHash
        )
    }
}
$missionAuthorityRow = [ordered]@{
    file = 'audit-mission-authority-v1.json'
    source = if ($reuseExistingMissionAuthority) { 'existing:validated-audit-mission' } else { 'broker:issue-news-grasp-audit-mission' }
    destination = $missionAuthorityPath
    backup = if (Test-Path -LiteralPath $missionAuthorityBackup -PathType Leaf) { $missionAuthorityBackup } else { '' }
    before_sha256 = $missionAuthorityBeforeHash
    source_sha256 = ''
    after_sha256 = ''
}
$manifestFiles += $missionAuthorityRow
$runtimeRootPath = Join-Path $BinDir 'news-grasp-runtime-root-v1.json'
$runtimeRootBackup = Join-Path $BackupDir 'news-grasp-runtime-root-v1.json'
$runtimeRootBeforeHash = ''
if (Test-Path -LiteralPath $runtimeRootPath -PathType Leaf) {
    $runtimeRootSnapshot = Read-NewsGraspVerifiedFile `
        -Path $runtimeRootPath `
        -TrustedBoundary $canonicalBinDir `
        -RequireSingleLink
    Write-NewsGraspAtomicFile `
        -Path $runtimeRootBackup `
        -TrustedBoundary $BackupDir `
        -Bytes $runtimeRootSnapshot.Bytes | Out-Null
    $runtimeRootBeforeHash = [string]$runtimeRootSnapshot.Sha256
}
$runtimeRootRow = [ordered]@{
    file = 'news-grasp-runtime-root-v1.json'
    source = 'generated:runtime-root'
    destination = $runtimeRootPath
    backup = if (Test-Path -LiteralPath $runtimeRootBackup -PathType Leaf) { $runtimeRootBackup } else { '' }
    before_sha256 = $runtimeRootBeforeHash
    source_sha256 = ''
    after_sha256 = ''
}
$manifestFiles += $runtimeRootRow
$recoveryRuntimeBindingPath = Join-Path $BinDir 'news-grasp-recovery-runtime-binding-v1.json'
$recoveryRuntimeBindingBackup = Join-Path $BackupDir 'news-grasp-recovery-runtime-binding-v1.json'
$recoveryRuntimeBindingBeforeHash = ''
if (Test-Path -LiteralPath $recoveryRuntimeBindingPath -PathType Leaf) {
    $recoveryRuntimeBindingSnapshot = Read-NewsGraspVerifiedFile `
        -Path $recoveryRuntimeBindingPath `
        -TrustedBoundary $canonicalBinDir `
        -RequireSingleLink
    Write-NewsGraspAtomicFile `
        -Path $recoveryRuntimeBindingBackup `
        -TrustedBoundary $BackupDir `
        -Bytes $recoveryRuntimeBindingSnapshot.Bytes | Out-Null
    $recoveryRuntimeBindingBeforeHash = [string]$recoveryRuntimeBindingSnapshot.Sha256
}
$recoveryRuntimeBindingRow = [ordered]@{
    file = 'news-grasp-recovery-runtime-binding-v1.json'
    source = 'generated:recovery-runtime-binding'
    destination = $recoveryRuntimeBindingPath
    backup = if ($recoveryRuntimeBindingBeforeHash) { $recoveryRuntimeBindingBackup } else { '' }
    before_sha256 = $recoveryRuntimeBindingBeforeHash
    source_sha256 = ''
    after_sha256 = ''
}
$manifestFiles += $recoveryRuntimeBindingRow
$stableTaskAuthorityPath = Join-Path $BinDir 'news-grasp-stable-task-authority-v1.json'
$stableTaskAuthorityBackup = Join-Path $BackupDir 'news-grasp-stable-task-authority-v1.json'
$stableTaskAuthorityBeforeHash = ''
if (Test-Path -LiteralPath $stableTaskAuthorityPath -PathType Leaf) {
    $stableTaskAuthoritySnapshot = Read-NewsGraspVerifiedFile `
        -Path $stableTaskAuthorityPath `
        -TrustedBoundary $canonicalBinDir `
        -RequireSingleLink
    Write-NewsGraspAtomicFile `
        -Path $stableTaskAuthorityBackup `
        -TrustedBoundary $BackupDir `
        -Bytes $stableTaskAuthoritySnapshot.Bytes | Out-Null
    $stableTaskAuthorityBeforeHash = [string]$stableTaskAuthoritySnapshot.Sha256
}
$stableTaskAuthorityRow = [ordered]@{
    file = 'news-grasp-stable-task-authority-v1.json'
    source = 'generated:StableTaskAuthorityV1'
    destination = $stableTaskAuthorityPath
    backup = if ($stableTaskAuthorityBeforeHash) { $stableTaskAuthorityBackup } else { '' }
    before_sha256 = $stableTaskAuthorityBeforeHash
    source_sha256 = ''
    after_sha256 = ''
}
$manifestFiles += $stableTaskAuthorityRow
$scheduledTasks = @()
$rollbackCommands = @('Invoke-NewsGraspInstallRollback')
Write-NewsGraspInstallJournal -Phase 'prepared'
$script:InstallationMutationStarted = $true
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null

foreach ($file in $files) {
    $source = Join-Path $ops $file
    $destination = Join-Path $BinDir $file
    $null = Assert-NewsGraspCanonicalInstallSource `
        -ResolvedRepoDir $RepoDir `
        -RequestedBinDir $BinDir `
        -CanonicalBinDir $canonicalBinDir `
        -TrustedBoundary $installTrustedBoundary `
        -ExpectedRuntimeRootSha256 $runtimeRootAuthoritySha `
        -ManagedTaskNames $managedTaskNames
    Assert-NewsGraspInstallDestination -DestinationPath $destination -CanonicalBinDir $canonicalBinDir
    $afterHash = Write-NewsGraspAtomicFile `
        -Path $destination `
        -TrustedBoundary $canonicalBinDir `
        -Bytes $sourceSnapshots[$file].Bytes
    $row = @($manifestFiles | Where-Object { $_.file -eq $file })[0]
    $row['after_sha256'] = $afterHash
}
foreach ($asset in $automationAssetRows) {
    $assetId = [string]$asset.assetId
    $destination = Join-Path $assetInstallRoot ([string]$asset.installPath)
    Assert-NewsGraspAssetInstallDestination -DestinationPath $destination -CanonicalBinDir $canonicalBinDir
    $assetParent = Split-Path -Parent $destination
    New-Item -ItemType Directory -Force -Path $assetParent | Out-Null
    $afterHash = Write-NewsGraspAtomicFile `
        -Path $destination `
        -TrustedBoundary $canonicalBinDir `
        -Bytes $assetSourceSnapshots[$assetId].Bytes
    $row = @($manifestFiles | Where-Object { $_.file -eq ('asset:' + $assetId) })[0]
    $row['after_sha256'] = $afterHash
}
$highCostBindingResolverAfterHash = Write-NewsGraspAtomicFile `
    -Path $highCostBindingResolverDestination `
    -TrustedBoundary $canonicalBinDir `
    -Bytes $highCostBindingResolverSource.Bytes
$highCostBindingResolverRow['after_sha256'] = $highCostBindingResolverAfterHash
$highCostBindingAfterHash = Write-NewsGraspAtomicFile `
    -Path $highCostBindingPath `
    -TrustedBoundary $canonicalBinDir `
    -Bytes $highCostBindingCandidate.Bytes
$highCostBindingRow['after_sha256'] = $highCostBindingAfterHash
$runtimePythonPath = Join-Path (Split-Path -Parent $TaskPythonwPath) 'python.exe'
$productionRuntimePath = Join-Path $env:USERPROFILE '.news-grasp-runtime\production-runtime'
$runtimeRoot = [ordered]@{
    schemaVersion = 'NEWS_GRASP_RUNTIME_ROOT_V1'
    repoDir = $productionRuntimePath
    pythonExe = $runtimePythonPath
    evidenceRepoDir = $runtimeEvidenceRepoDir
}
$null = Assert-NewsGraspCanonicalInstallSource `
    -ResolvedRepoDir $RepoDir `
    -RequestedBinDir $BinDir `
    -CanonicalBinDir $canonicalBinDir `
    -TrustedBoundary $installTrustedBoundary `
    -ExpectedRuntimeRootSha256 $runtimeRootAuthoritySha `
    -ManagedTaskNames $managedTaskNames
Write-AtomicUtf8Text -Path $runtimeRootPath -Text (($runtimeRoot | ConvertTo-Json -Depth 3) + [Environment]::NewLine)
$runtimeRootInstalled = Read-NewsGraspVerifiedFile `
    -Path $runtimeRootPath `
    -TrustedBoundary $canonicalBinDir `
    -RequireSingleLink
$runtimeRootRow['after_sha256'] = [string]$runtimeRootInstalled.Sha256
$runtimeRootAuthoritySha = [string]$runtimeRootInstalled.Sha256
$pythonSnapshot = Read-NewsGraspVerifiedFile `
    -Path $runtimePythonPath `
    -TrustedBoundary $installTrustedBoundary `
    -RequireSingleLink
$receiptToolSnapshot = Read-NewsGraspVerifiedFile `
    -Path (Join-Path $runtimeEvidenceRepoDir 'tools\news_grasp_recovery_receipts.py') `
    -TrustedBoundary $installTrustedBoundary `
    -RequireSingleLink
$controlPlaneToolSnapshot = Read-NewsGraspVerifiedFile `
    -Path (Join-Path $runtimeEvidenceRepoDir 'tools\news_grasp_control_plane.py') `
    -TrustedBoundary $installTrustedBoundary `
    -RequireSingleLink
$completionGuardToolSnapshot = Read-NewsGraspVerifiedFile `
    -Path (Join-Path $runtimeEvidenceRepoDir 'tools\news_grasp_completion_guard.py') `
    -TrustedBoundary $installTrustedBoundary `
    -RequireSingleLink
$dailySelfHealSnapshot = Read-NewsGraspVerifiedFile `
    -Path (Join-Path $runtimeEvidenceRepoDir 'tools\daily_self_heal.py') `
    -TrustedBoundary $installTrustedBoundary `
    -RequireSingleLink
$startupCustomizationPresent = (
    (Test-Path -LiteralPath (Join-Path $runtimeEvidenceRepoDir 'sitecustomize.py')) -or
    (Test-Path -LiteralPath (Join-Path $runtimeEvidenceRepoDir 'usercustomize.py'))
)
if ($startupCustomizationPresent) { throw 'NEWS_GRASP_RECOVERY_OPS_STARTUP_CUSTOMIZATION_FORBIDDEN' }
$trustedGitRemote = 'https://github.com/HIDEPON-UMG/News-Grasp.git'
$recoveryGitExe = 'C:\Program Files\Git\cmd\git.exe'
$recoveryGitSafeArgs = @('-c', 'core.hooksPath=NUL', '-c', 'core.fsmonitor=false', '-c', 'core.attributesFile=NUL')
$opsHead = (& $recoveryGitExe @recoveryGitSafeArgs -C $runtimeEvidenceRepoDir rev-parse HEAD 2>$null | Out-String).Trim().ToLowerInvariant()
$trustedRemoteHeadLine = (& $recoveryGitExe @recoveryGitSafeArgs ls-remote $trustedGitRemote refs/heads/main 2>$null | Out-String).Trim()
$trustedRemoteHead = if ($trustedRemoteHeadLine) { ($trustedRemoteHeadLine -split '\s+')[0].ToLowerInvariant() } else { '' }
$opsDirty = (& $recoveryGitExe @recoveryGitSafeArgs -C $runtimeEvidenceRepoDir status --porcelain --untracked-files=all 2>$null | Out-String).Trim()
$opsIgnored = (& $recoveryGitExe @recoveryGitSafeArgs -C $runtimeEvidenceRepoDir ls-files --others --ignored --exclude-standard 2>$null | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $opsHead -notmatch '^[0-9a-f]{40}$' -or $opsHead -ne $trustedRemoteHead -or $opsDirty -or $opsIgnored) {
    throw 'NEWS_GRASP_RECOVERY_OPS_GENERATION_INVALID'
}
$pythonSignature = Get-AuthenticodeSignature -LiteralPath $runtimePythonPath
$pythonSignerSubject = [string]$pythonSignature.SignerCertificate.Subject
$pythonSignerThumbprint = ([string]$pythonSignature.SignerCertificate.Thumbprint).ToLowerInvariant()
if (
    [string]$pythonSignature.Status -cne 'Valid' -or
    $pythonSignerSubject -notlike 'CN=Python Software Foundation, O=Python Software Foundation,*' -or
    $pythonSignerThumbprint -notmatch '^[0-9a-f]{40}$'
) { throw 'NEWS_GRASP_RECOVERY_PYTHON_TRUST_ANCHOR_INVALID' }
$recoveryRuntimeBinding = [ordered]@{
    schemaVersion = 'NEWS_GRASP_RECOVERY_RUNTIME_BINDING_V1'
    opsRepoRoot = $runtimeEvidenceRepoDir
    opsHead = $opsHead
    trustedRemote = $trustedGitRemote
    productionRuntimeRoot = $productionRuntimePath
    pythonExe = $runtimePythonPath
    pythonExeSha256 = ([string]$pythonSnapshot.Sha256).ToLowerInvariant()
    pythonTrustAnchor = 'authenticode:python-software-foundation'
    pythonSignerSubject = $pythonSignerSubject
    pythonSignerThumbprint = $pythonSignerThumbprint
    receiptToolPath = (Join-Path $runtimeEvidenceRepoDir 'tools\news_grasp_recovery_receipts.py')
    receiptToolSha256 = ([string]$receiptToolSnapshot.Sha256).ToLowerInvariant()
    controlPlaneToolPath = (Join-Path $runtimeEvidenceRepoDir 'tools\news_grasp_control_plane.py')
    controlPlaneToolSha256 = ([string]$controlPlaneToolSnapshot.Sha256).ToLowerInvariant()
    completionGuardToolPath = (Join-Path $runtimeEvidenceRepoDir 'tools\news_grasp_completion_guard.py')
    completionGuardToolSha256 = ([string]$completionGuardToolSnapshot.Sha256).ToLowerInvariant()
    dailySelfHealPath = (Join-Path $runtimeEvidenceRepoDir 'tools\daily_self_heal.py')
    dailySelfHealSha256 = ([string]$dailySelfHealSnapshot.Sha256).ToLowerInvariant()
    highCostBindingPath = $highCostBindingPath
    highCostBindingReceiptSha256 = $highCostBindingReceiptSha256
    highCostBindingFileSha256 = ([string]$highCostBindingAfterHash).ToLowerInvariant()
    highCostBindingResolverPath = $highCostBindingResolverDestination
    highCostBindingResolverSha256 = ([string]$highCostBindingResolverAfterHash).ToLowerInvariant()
    bootstrapPath = (Join-Path $BinDir 'news-grasp-bootstrap.ps1')
    bootstrapSha256 = ([string]$sourceSnapshots['news-grasp-bootstrap.ps1'].Sha256).ToLowerInvariant()
    runnerPath = (Join-Path $BinDir 'news-grasp-runner.ps1')
    runnerSha256 = ([string]$sourceSnapshots['news-grasp-runner.ps1'].Sha256).ToLowerInvariant()
}
Write-AtomicUtf8Text -Path $recoveryRuntimeBindingPath -Text (($recoveryRuntimeBinding | ConvertTo-Json -Depth 4) + [Environment]::NewLine)
$recoveryRuntimeBindingInstalled = Read-NewsGraspVerifiedFile `
    -Path $recoveryRuntimeBindingPath `
    -TrustedBoundary $canonicalBinDir `
    -RequireSingleLink
$recoveryRuntimeBindingRow['after_sha256'] = [string]$recoveryRuntimeBindingInstalled.Sha256
$stableTaskAuthority = [ordered]@{
    schemaVersion = 'STABLE_TASK_AUTHORITY_V1'
    taskName = $RunnerTaskName
    stableLauncherPath = (Join-Path $BinDir 'news-grasp-task-launcher.pyw')
    stableLauncherSha256 = [string]$sourceSnapshots['news-grasp-task-launcher.pyw'].Sha256
    bootstrapPath = (Join-Path $BinDir 'news-grasp-bootstrap.ps1')
    bootstrapSha256 = [string]$sourceSnapshots['news-grasp-bootstrap.ps1'].Sha256
    action = @($TaskPythonwPath, (Join-Path $BinDir 'news-grasp-task-launcher.pyw'), 'runner', '--scheduled-task-name', $RunnerTaskName, '--high-cost-binding-path', $highCostBindingPath, '--high-cost-binding-sha256', $highCostBindingReceiptSha256)
    trigger = @{ daily = '06:00' }
    repoArgumentCount = 0
}
$stableAuthorityBody = $stableTaskAuthority | ConvertTo-Json -Depth 6 -Compress
$stableAuthorityHasher = [Security.Cryptography.SHA256]::Create()
try {
    $stableAuthorityBytes = [Text.Encoding]::UTF8.GetBytes($stableAuthorityBody)
    $stableTaskAuthority.authoritySha256 = ([BitConverter]::ToString($stableAuthorityHasher.ComputeHash($stableAuthorityBytes)) -replace '-', '').ToLowerInvariant()
} finally { $stableAuthorityHasher.Dispose() }
Write-AtomicUtf8Text -Path $stableTaskAuthorityPath -Text (($stableTaskAuthority | ConvertTo-Json -Depth 6) + [Environment]::NewLine)
$stableTaskAuthorityInstalled = Read-NewsGraspVerifiedFile `
    -Path $stableTaskAuthorityPath `
    -TrustedBoundary $canonicalBinDir `
    -RequireSingleLink
$stableTaskAuthorityRow['after_sha256'] = [string]$stableTaskAuthorityInstalled.Sha256
Write-NewsGraspInstallJournal -Phase 'files_installed'

$pythonPath = $runtimePythonPath
$null = Assert-NewsGraspCanonicalInstallSource `
    -ResolvedRepoDir $RepoDir `
    -RequestedBinDir $BinDir `
    -CanonicalBinDir $canonicalBinDir `
    -TrustedBoundary $installTrustedBoundary `
    -ExpectedRuntimeRootSha256 $runtimeRootAuthoritySha `
    -ManagedTaskNames $managedTaskNames
if ($reuseExistingMissionAuthority) {
    $missionAuthorityCurrent = Read-NewsGraspVerifiedFile `
        -Path $missionAuthorityPath `
        -TrustedBoundary $BinDir `
        -RequireSingleLink
    if ([string]$missionAuthorityCurrent.Sha256 -cne $missionAuthorityBeforeHash) {
        throw 'audit mission authority changed after validation'
    }
} else {
    $brokerPath = Join-Path $env:USERPROFILE 'bin\ai-model-spawn-broker.py'
    if ((-not (Test-Path -LiteralPath $brokerPath -PathType Leaf)) -or (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf))) {
        throw 'News-Grasp audit mission authority broker is unavailable.'
    }
    New-Item -ItemType Directory -Force -Path $authorityDir | Out-Null
    $missionAuthorityJson = (& $pythonPath $brokerPath 'issue-news-grasp-audit-mission' 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) { throw "audit mission authority issuance failed exit=$LASTEXITCODE" }
    Write-AtomicUtf8Text -Path $missionAuthorityPath -Text ($missionAuthorityJson + [Environment]::NewLine)
}
$null = Assert-NewsGraspCanonicalInstallSource `
    -ResolvedRepoDir $RepoDir `
    -RequestedBinDir $BinDir `
    -CanonicalBinDir $canonicalBinDir `
    -TrustedBoundary $installTrustedBoundary `
    -ExpectedRuntimeRootSha256 $runtimeRootAuthoritySha `
    -ManagedTaskNames $managedTaskNames
$missionAuthorityInstalled = Read-NewsGraspVerifiedFile `
    -Path $missionAuthorityPath `
    -TrustedBoundary $BinDir `
    -RequireSingleLink
$missionAuthorityRow['after_sha256'] = [string]$missionAuthorityInstalled.Sha256
Write-NewsGraspInstallJournal -Phase 'authority_issued'
if (-not $SkipTaskRegistration) {
    $null = Assert-NewsGraspCanonicalInstallSource `
        -ResolvedRepoDir $RepoDir `
        -RequestedBinDir $BinDir `
        -CanonicalBinDir $canonicalBinDir `
        -TrustedBoundary $installTrustedBoundary `
        -ExpectedRuntimeRootSha256 $runtimeRootAuthoritySha `
        -ManagedTaskNames $managedTaskNames
    $watcherPath = Join-Path $BinDir 'watch-news-grasp-runner.ps1'
    $bootstrapPath = Join-Path $BinDir 'news-grasp-bootstrap.ps1'
    $deadmanLauncherPath = Join-Path $BinDir 'news-grasp-deadman-launcher.pyw'
    $taskLauncherPath = Join-Path $BinDir 'news-grasp-task-launcher.pyw'
    $pythonw = $TaskPythonwPath
    if (-not (Test-Path -LiteralPath $pythonw)) { throw 'News-Grasp .venv pythonw.exe が見つかりません。' }
    # Scheduled Taskはstable installed launcherだけを指す。source worktreeのpathをtask定義へ封印しない。
    $runnerArgs = "`"$taskLauncherPath`" runner --scheduled-task-name `"$RunnerTaskName`" --high-cost-binding-path `"$highCostBindingPath`" --high-cost-binding-sha256 $highCostBindingReceiptSha256"
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

    $bootstrapArgs = "`"$taskLauncherPath`" bootstrap --scheduled-task-name `"$BootstrapTaskName`" --high-cost-binding-path `"$highCostBindingPath`" --high-cost-binding-sha256 $highCostBindingReceiptSha256"
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
    $legacyTask = Get-ScheduledTask -TaskName $LegacyRunnerTaskName -ErrorAction SilentlyContinue
    if ($legacyTask -and $legacyTask.Settings.Enabled) {
        Disable-ScheduledTask -TaskName $LegacyRunnerTaskName -ErrorAction Stop | Out-Null
    }
    $scheduledTasks += [ordered]@{
        task_name = $LegacyRunnerTaskName
        trigger = 'legacy daily 06:00'
        status = if ($legacyTask) { 'legacy_task_disabled' } else { 'legacy_task_absent' }
    }
    Write-NewsGraspInstallJournal -Phase 'tasks_converged'
}

if ((-not $SkipTaskRegistration) -and (-not $runnerRegistered)) {
    throw "failed to converge $RunnerTaskName action: $runnerRegisterError"
}
Assert-NewsGraspInstalledState
$deliveryReceiptPath = Join-Path $BackupDir 'physical-delivery-state-v1.json'
$installedEvidenceBody = [ordered]@{
    transactionId = $timestamp
    files = $manifestFiles
    scheduledTasks = $scheduledTasks
}
$installedEvidenceJson = $installedEvidenceBody | ConvertTo-Json -Depth 10 -Compress
$installedEvidenceHasher = [Security.Cryptography.SHA256]::Create()
try {
    $installedEvidenceSha256 = ([BitConverter]::ToString(
        $installedEvidenceHasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($installedEvidenceJson))
    ) -replace '-', '').ToLowerInvariant()
} finally { $installedEvidenceHasher.Dispose() }
$pendingEvidence = [ordered]@{ status = 'pending'; evidenceSha256 = ''; reasonCode = 'AWAITING_RELEASE_EVIDENCE' }
$runtimePendingEvidence = [ordered]@{ status = 'pending'; evidenceSha256 = ''; reasonCode = 'AWAITING_ACTIVE_GENERATION' }
$e2ePendingEvidence = [ordered]@{ status = 'pending'; evidenceSha256 = ''; reasonCode = 'AWAITING_FINAL_NOPUBLISH_E2E' }
$greenEvidence = [ordered]@{ status = 'green'; evidenceSha256 = $installedEvidenceSha256 }
$taskEvidence = if ($SkipTaskRegistration) {
    [ordered]@{ status = 'not_required_not_run'; evidenceSha256 = ''; reasonCode = 'TASK_REGISTRATION_EXPLICITLY_SKIPPED' }
} else {
    $greenEvidence
}
$deliveryFields = [ordered]@{
    implemented = $pendingEvidence
    committed = $pendingEvidence
    pushed = $pendingEvidence
    remoteHeadVerified = $pendingEvidence
    installed = $greenEvidence
    installedSkillsFresh = $greenEvidence
    runtimeGenerationFresh = $runtimePendingEvidence
    scheduledTaskParity = $taskEvidence
    rollbackReceipt = $greenEvidence
    noPublishE2E = $e2ePendingEvidence
}
$deliveryStateBody = [ordered]@{
    schemaVersion = 'NEWS_GRASP_PHYSICAL_DELIVERY_STATE_V1'
    generationId = 'pending-active-generation'
    fields = $deliveryFields
    operationalStatus = 'incomplete'
}
$deliveryStateJson = $deliveryStateBody | ConvertTo-Json -Depth 8 -Compress
$deliveryStateHasher = [Security.Cryptography.SHA256]::Create()
try {
    $deliveryStateBody.stateSha256 = ([BitConverter]::ToString(
        $deliveryStateHasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($deliveryStateJson))
    ) -replace '-', '').ToLowerInvariant()
} finally { $deliveryStateHasher.Dispose() }
Write-AtomicUtf8Text -Path $deliveryReceiptPath -Text (($deliveryStateBody | ConvertTo-Json -Depth 8) + [Environment]::NewLine)
$deliveryReceiptFile = Read-NewsGraspVerifiedFile `
    -Path $deliveryReceiptPath `
    -TrustedBoundary $BackupDir `
    -RequireSingleLink
$script:DeliveryReceiptSummary = [ordered]@{
    path = $deliveryReceiptPath
    sha256 = [string]$deliveryReceiptFile.Sha256
    schemaVersion = 'NEWS_GRASP_PHYSICAL_DELIVERY_STATE_V1'
    operationalStatus = 'incomplete'
}
Write-NewsGraspInstallJournal -Phase 'verified'
Write-NewsGraspInstallJournal -Phase 'committed'
$script:InstallationCommitted = $true
Write-Host "News-Grasp ops scripts installed to $BinDir"
Write-Host "Backup manifest: $ManifestPath"
