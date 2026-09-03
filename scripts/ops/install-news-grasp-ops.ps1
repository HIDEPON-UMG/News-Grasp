param(
    [string] $RepoDir = '',
    [string] $BinDir = (Join-Path $env:USERPROFILE 'bin'),
    [string] $TaskPythonwPath = '',
    [string] $EvidenceRepoDir = '',
    [string] $RunnerTaskName = 'News-Grasp Production',
    [string] $BootstrapTaskName = 'News-Grasp Bootstrap',
    [string] $DeadmanTaskName = 'News-Grasp Deadman',
    [string] $PullTaskName = 'News-Grasp Pull',
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

$canonicalTaskNames = [ordered]@{
    Runner = 'News-Grasp Production'
    Bootstrap = 'News-Grasp Bootstrap'
    Deadman = 'News-Grasp Deadman'
    Pull = 'News-Grasp Pull'
    LegacyRunner = 'News-Grasp Runner'
}
if (
    $RunnerTaskName -cne [string]$canonicalTaskNames.Runner -or
    $BootstrapTaskName -cne [string]$canonicalTaskNames.Bootstrap -or
    $DeadmanTaskName -cne [string]$canonicalTaskNames.Deadman -or
    $PullTaskName -cne [string]$canonicalTaskNames.Pull -or
    $LegacyRunnerTaskName -cne [string]$canonicalTaskNames.LegacyRunner
) {
    throw 'NEWS_GRASP_TASK_NAME_AUTHORITY_INVALID'
}
$taskWriterMutex = [Threading.Mutex]::new($false, 'Local\NewsGraspOpsInstallV1')
try {
    $taskWriterLeaseAcquired = $taskWriterMutex.WaitOne(0)
} catch [Threading.AbandonedMutexException] {
    $taskWriterLeaseAcquired = $true
}
if (-not $taskWriterLeaseAcquired) {
    throw 'NEWS_GRASP_TASK_WRITER_LEASE_UNAVAILABLE'
}

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

function Assert-NewsGraspAutomationProjectionAsset {
    param(
        [Parameter(Mandatory = $true)]$Asset,
        [Parameter(Mandatory = $true)][byte[]]$Bytes
    )
    $assetId = [string]$Asset.assetId
    if ($assetId -cne 'news-grasp-6-40-completion-guard-projection-v1') { return }
    $sourcePath = ([string]$Asset.sourcePath).Replace('/', '\')
    if ($sourcePath -cne 'automation\news-grasp-6-40\completion_guard.py') {
        throw 'NEWS_GRASP_AUTOMATION_PROJECTION_ASSET_PATH_INVALID'
    }
    $text = [Text.Encoding]::UTF8.GetString($Bytes)
    if ($text -notmatch '(?i)stdout' -or $text -notmatch '(?i)projection') {
        throw 'NEWS_GRASP_AUTOMATION_PROJECTION_CONTRACT_MISSING'
    }
    if ($text -match '(?i)(--output|write_text|write_bytes|write_atomic|set-content|out-file|new-item)') {
        throw 'NEWS_GRASP_AUTOMATION_CANONICAL_WRITE_FORBIDDEN'
    }
}

function Resolve-NewsGraspWorkspaceHarnessRoot {
    param([Parameter(Mandatory = $true)][string] $StartPath)
    $findHarnessRoot = {
        param([Parameter(Mandatory = $true)][string] $InitialPath)
        $candidate = Get-NewsGraspCanonicalPath -Path $InitialPath
        for ($depth = 0; $depth -lt 12; $depth += 1) {
            if (
                (Test-Path -LiteralPath (Join-Path $candidate 'tools\harness\task_model_routing.py') -PathType Leaf) -and
                (Test-Path -LiteralPath (Join-Path $candidate 'docs\harness\high_cost_model_routes_v1.json') -PathType Leaf)
            ) {
                return $candidate
            }
            # .NET の GetParent('C:') は現在の C: ドライブ作業ディレクトリへ
            # 折り返すため、ドライブ相対表記を親探索の終端として扱う。
            if ($candidate -match '^[A-Za-z]:$') { break }
            $parentInfo = [IO.Directory]::GetParent($candidate)
            if ($null -eq $parentInfo) { break }
            $parent = Get-NewsGraspCanonicalPath -Path $parentInfo.FullName
            if (Test-NewsGraspSamePath -Left $parent -Right $candidate) { break }
            $candidate = $parent
        }
        return $null
    }

    $resolvedStartPath = Get-NewsGraspCanonicalPath -Path $StartPath
    $ancestorRoot = & $findHarnessRoot $resolvedStartPath
    if ($ancestorRoot) { return $ancestorRoot }

    # implementation worktreeはworkspace rootの外側に置ける。候補repoのGit
    # common-dirはcanonical product repoへ戻るため、その祖先からglobal harnessを解決する。
    $gitCommand = Get-Command git.exe -ErrorAction SilentlyContinue
    if (-not $gitCommand) { $gitCommand = Get-Command git -ErrorAction SilentlyContinue }
    if ($gitCommand) {
        $gitCommonRaw = ((& $gitCommand.Source -C $resolvedStartPath rev-parse --git-common-dir 2>$null) | Out-String).Trim()
        $gitCommonExit = $LASTEXITCODE
        if ($gitCommonExit -eq 0 -and $gitCommonRaw) {
            $gitCommonPath = if ([IO.Path]::IsPathRooted($gitCommonRaw)) {
                $gitCommonRaw
            } else {
                Join-Path $resolvedStartPath $gitCommonRaw
            }
            $commonDirRoot = & $findHarnessRoot (Get-NewsGraspCanonicalPath -Path $gitCommonPath)
            if ($commonDirRoot) { return $commonDirRoot }
        }
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
        if (-not (Test-Path -LiteralPath $Override -PathType Container)) {
            throw "NEWS_GRASP_REPO_PATH_NOT_FOUND:$Override"
        }
        return (Resolve-Path -LiteralPath $Override).Path
    }
    if ($env:NEWS_GRASP_REPO_DIR) {
        if (-not (Test-Path -LiteralPath $env:NEWS_GRASP_REPO_DIR -PathType Container)) {
            throw "NEWS_GRASP_REPO_PATH_NOT_FOUND:$($env:NEWS_GRASP_REPO_DIR)"
        }
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
        (Join-Path $env:USERPROFILE 'AppData\Local\Programs\Python\Python312\pythonw.exe')
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    throw 'News-Grasp Scheduled Task用の安定したpythonw.exeが見つかりません。-TaskPythonwPathを指定してください。'
}

function Stop-NewsGraspTaskAndWait {
    param(
        [Parameter(Mandatory = $true)][string] $TaskName,
        [int] $TimeoutSeconds = 15
    )
    if ([string]::IsNullOrWhiteSpace($TaskName)) {
        throw 'NEWS_GRASP_TASK_QUIESCE_NAME_MISSING'
    }
    if ($TimeoutSeconds -lt 1 -or $TimeoutSeconds -gt 120) {
        throw 'NEWS_GRASP_TASK_QUIESCE_TIMEOUT_INVALID'
    }
    $task = Get-ScheduledTask -TaskPath '\' -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) { return }
    Disable-ScheduledTask -TaskPath '\' -TaskName $TaskName -ErrorAction Stop | Out-Null
    if ([string]$task.State -eq 'Running') {
        Stop-ScheduledTask -TaskPath '\' -TaskName $TaskName -ErrorAction Stop | Out-Null
    }
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ($true) {
        $task = Get-ScheduledTask -TaskPath '\' -TaskName $TaskName -ErrorAction Stop
        if ([string]$task.State -ne 'Running') { return }
        if ((Get-Date) -ge $deadline) {
            throw "NEWS_GRASP_TASK_QUIESCE_TIMEOUT:$TaskName"
        }
        Start-Sleep -Milliseconds 200
    }
}

function Invoke-NewsGraspRollbackJournal {
    param([string] $JournalPath, [object] $Journal)
    $journalDirectory = Split-Path -Parent $JournalPath
    foreach ($snapshot in @($Journal.task_snapshots)) {
        $taskName = [string]$snapshot.task_name
        # Stop-ScheduledTask/Disable-ScheduledTask then Get-ScheduledTask polling
        # confirms Running is gone within the bounded timeout before file restore.
        Stop-NewsGraspTaskAndWait -TaskName $taskName -TimeoutSeconds 15
    }
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
        $currentTask = Get-ScheduledTask -TaskPath '\' -TaskName $taskName -ErrorAction SilentlyContinue
        $taskNeedsRestore = $true
        if ([bool]$snapshot.existed_before) {
            $xml = Read-NewsGraspVerifiedTaskXml `
                -Path ([string]$snapshot.xml_backup) `
                -TrustedBoundary $journalDirectory `
                -ExpectedSha256 ([string]$snapshot.xml_backup_sha256)
            if ($currentTask) {
                $currentXml = Export-ScheduledTask -TaskPath '\' -TaskName $taskName
                if (
                    $currentXml.Trim() -eq $xml.Trim() -and
                    [bool]$currentTask.Settings.Enabled -eq [bool]$snapshot.enabled_before
                ) {
                    $taskNeedsRestore = $false
                }
            }
            if (-not $taskNeedsRestore) { continue }
            Register-ScheduledTask -TaskPath '\' -TaskName $taskName -Xml $xml -Force -ErrorAction Stop | Out-Null
            if ([bool]$snapshot.enabled_before) {
                Enable-ScheduledTask -TaskPath '\' -TaskName $taskName -ErrorAction Stop | Out-Null
            } else {
                Disable-ScheduledTask -TaskPath '\' -TaskName $taskName -ErrorAction Stop | Out-Null
            }
        } elseif (-not $currentTask) {
            $taskNeedsRestore = $false
            if (-not $taskNeedsRestore) { continue }
        } else {
            Unregister-ScheduledTask -TaskPath '\' -TaskName $taskName -Confirm:$false -ErrorAction Stop
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
                $script:NewsGraspInstallJournalRecoverablePhases
                $script:NewsGraspInstallJournalTerminalPhases
            )
        ) {
            throw 'NEWS_GRASP_INSTALL_JOURNAL_INGEST_INVALID'
        }
        if ([string]$journal.phase -notin $script:NewsGraspInstallJournalTerminalPhases) {
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
    foreach ($snapshot in $taskSnapshots) {
        $taskName = [string]$snapshot.task_name
        # Stop-ScheduledTask/Disable-ScheduledTask then Get-ScheduledTask polling
        # confirms Running is gone within the bounded timeout before file restore.
        Stop-NewsGraspTaskAndWait -TaskName $taskName -TimeoutSeconds 15
    }
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
        $currentTask = Get-ScheduledTask -TaskPath '\' -TaskName $taskName -ErrorAction SilentlyContinue
        $taskNeedsRestore = $true
        if ([bool]$snapshot.existed_before) {
            $xml = Read-NewsGraspVerifiedTaskXml `
                -Path ([string]$snapshot.xml_backup) `
                -TrustedBoundary $BackupDir `
                -ExpectedSha256 ([string]$snapshot.xml_backup_sha256)
            if ($currentTask) {
                $currentXml = Export-ScheduledTask -TaskPath '\' -TaskName $taskName
                if (
                    $currentXml.Trim() -eq $xml.Trim() -and
                    [bool]$currentTask.Settings.Enabled -eq [bool]$snapshot.enabled_before
                ) {
                    $taskNeedsRestore = $false
                }
            }
            if (-not $taskNeedsRestore) { continue }
            Register-ScheduledTask -TaskPath '\' -TaskName $taskName -Xml $xml -Force -ErrorAction Stop | Out-Null
            if ([bool]$snapshot.enabled_before) {
                Enable-ScheduledTask -TaskPath '\' -TaskName $taskName -ErrorAction Stop | Out-Null
            } else {
                Disable-ScheduledTask -TaskPath '\' -TaskName $taskName -ErrorAction Stop | Out-Null
            }
        } elseif (-not $currentTask) {
            $taskNeedsRestore = $false
            if (-not $taskNeedsRestore) { continue }
        } else {
            Unregister-ScheduledTask -TaskPath '\' -TaskName $taskName -Confirm:$false -ErrorAction Stop
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
        task_snapshots = @($taskSnapshots | ForEach-Object {
            [ordered]@{
                task_name = [string]$_.task_name
                existed_before = [bool]$_.existed_before
                enabled_before = [bool]$_.enabled_before
                xml_backup = [string]$_.xml_backup
                xml_backup_sha256 = [string]$_.xml_backup_sha256
            }
        })
        delivery_state = $script:DeliveryReceiptSummary
    }
    Write-AtomicUtf8Text -Path $ManifestPath -Text (($journal | ConvertTo-Json -Depth 10) + [Environment]::NewLine)
}

function Get-NewsGraspTaskXmlSha256 {
    param([Parameter(Mandatory = $true)][string] $TaskName)
    $xml = Export-ScheduledTask -TaskPath '\' -TaskName $TaskName -ErrorAction Stop
    $hasher = [Security.Cryptography.SHA256]::Create()
    try {
        return (([BitConverter]::ToString(
            $hasher.ComputeHash([Text.Encoding]::Unicode.GetBytes($xml))
        )) -replace '-', '').ToLowerInvariant()
    } finally { $hasher.Dispose() }
}

function Enter-NewsGraspTaskMutationBoundary {
    foreach ($snapshot in $taskSnapshots) {
        $taskName = [string]$snapshot.task_name
        $current = Get-ScheduledTask -TaskPath '\' -TaskName $taskName -ErrorAction SilentlyContinue
        if ([bool]$snapshot.existed_before -ne [bool]$current) {
            throw "NEWS_GRASP_TASK_PREIMAGE_EXISTENCE_DRIFT:$taskName"
        }
        if (-not $current) { continue }
        $currentSha = Get-NewsGraspTaskXmlSha256 -TaskName $taskName
        if ($currentSha -cne [string]$snapshot.xml_backup_sha256) {
            throw "NEWS_GRASP_TASK_PREIMAGE_CAS_MISMATCH:$taskName"
        }
        if ([string]$current.State -ceq 'Running') {
            throw "NEWS_GRASP_TASK_QUIESCENCE_REQUIRED:$taskName"
        }
        Disable-ScheduledTask -TaskPath '\' -TaskName $taskName -ErrorAction Stop | Out-Null
        $snapshot['quiesced_xml_sha256'] = Get-NewsGraspTaskXmlSha256 -TaskName $taskName
    }
}

function Assert-NewsGraspTaskMutationBoundaryCurrent {
    foreach ($snapshot in $taskSnapshots) {
        if ($snapshot.mutation_applied) { continue }
        if (-not [bool]$snapshot.existed_before) {
            if (Get-ScheduledTask -TaskPath '\' -TaskName ([string]$snapshot.task_name) -ErrorAction SilentlyContinue) {
                throw "NEWS_GRASP_TASK_CREATED_AFTER_PREIMAGE:$($snapshot.task_name)"
            }
            continue
        }
        $taskName = [string]$snapshot.task_name
        $current = Get-ScheduledTask -TaskPath '\' -TaskName $taskName -ErrorAction Stop
        if ([string]$current.State -ceq 'Running') {
            throw "NEWS_GRASP_TASK_QUIESCENCE_LOST:$taskName"
        }
        if (
            [string]$snapshot.quiesced_xml_sha256 -notmatch '^[0-9a-f]{64}$' -or
            (Get-NewsGraspTaskXmlSha256 -TaskName $taskName) -cne [string]$snapshot.quiesced_xml_sha256
        ) {
            throw "NEWS_GRASP_TASK_QUIESCED_CAS_MISMATCH:$taskName"
        }
    }
}

function Set-NewsGraspTaskMutationApplied {
    param([Parameter(Mandatory = $true)][string] $TaskName)
    $snapshot = @($taskSnapshots | Where-Object { $_.task_name -ceq $TaskName })
    if ($snapshot.Count -ne 1) {
        throw "NEWS_GRASP_TASK_SNAPSHOT_CARDINALITY_INVALID:$TaskName"
    }
    $snapshot[0]['mutation_applied'] = $true
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
        -not (Test-NewsGraspSamePath -Left ([string]$recoveryBinding.lineagePath) -Right (Join-Path $canonicalBinDir 'news-grasp-lineage.ps1')) -or
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
        [string]$recoveryBinding.auditControlSha256,
        [string]$recoveryBinding.recoveryCloseoutToolSha256,
        [string]$recoveryBinding.operationalContractToolSha256,
        [string]$recoveryBinding.highCostBindingReceiptSha256,
        [string]$recoveryBinding.highCostBindingFileSha256,
        [string]$recoveryBinding.highCostBindingResolverSha256,
        [string]$recoveryBinding.bootstrapSha256,
        [string]$recoveryBinding.runnerSha256,
        [string]$recoveryBinding.lineageSha256
    )) {
        if ($bindingHash -notmatch '^[0-9a-f]{64}$') { throw 'recovery runtime binding hash invalid' }
    }
    if ($SkipTaskRegistration) { return }
    $canonicalProductionArgs = "`"$taskLauncherPath`" dispatch --schedule-id news-grasp-daily-v1 --intent reconcile"
    $canonicalProductionTriggers = @('T06:00:00')
    $expected = @(
        [ordered]@{ name = $RunnerTaskName; taskPath = '\'; execute = $pythonw; arguments = $canonicalProductionArgs; working = $productionRuntimePath; starts = $canonicalProductionTriggers; policy = 'IgnoreNew'; interval = ''; duration = ''; principal = $taskPrincipalUserId },
        [ordered]@{ name = $BootstrapTaskName; taskPath = '\'; execute = $pythonw; arguments = $bootstrapArgs; working = $BinDir; starts = @('T05:55:00'); policy = 'IgnoreNew'; interval = ''; duration = ''; principal = $taskPrincipalUserId },
        [ordered]@{ name = $DeadmanTaskName; taskPath = '\'; execute = $pythonw; arguments = $deadmanArgs; working = $BinDir; starts = @('T06:40:00'); policy = 'IgnoreNew'; interval = 'PT1H'; duration = 'P1D'; executionTimeLimit = 'PT1H45M'; principal = $taskPrincipalUserId }
    )
    foreach ($spec in $expected) {
        $task = Get-ScheduledTask -TaskPath '\' -TaskName ([string]$spec.name) -ErrorAction Stop
        $actions = @($task.Actions)
        $triggers = @($task.Triggers)
        if ($actions.Count -ne 1 -or $triggers.Count -ne @($spec.starts).Count) { throw "scheduled task cardinality mismatch: $($spec.name)" }
        $action = $actions[0]
        if (-not $task.Settings.Enabled) { throw "scheduled task disabled: $($spec.name)" }
        if ([string]$task.TaskPath -cne [string]$spec.taskPath) { throw "scheduled task path mismatch: $($spec.name)" }
        if (
            [string]$task.Principal.UserId -cne [string]$spec.principal -or
            [string]$task.Principal.LogonType -cne 'Interactive' -or
            [string]$task.Principal.RunLevel -cne 'Limited'
        ) { throw "scheduled task principal mismatch: $($spec.name)" }
        if (
            [string]$action.Execute -ne [string]$spec.execute -or
            [string]$action.Arguments -ne [string]$spec.arguments -or
            [string]$action.WorkingDirectory -ne [string]$spec.working
        ) {
            throw "scheduled task action mismatch: $($spec.name)"
        }
        if (-not [bool]$task.Settings.StartWhenAvailable) { throw "scheduled task start-when-available mismatch: $($spec.name)" }
        if ([string]$task.Settings.MultipleInstances -ne [string]$spec.policy) { throw "scheduled task instance policy mismatch: $($spec.name)" }
        if ([string]$spec.executionTimeLimit -and [string]$task.Settings.ExecutionTimeLimit -ne [string]$spec.executionTimeLimit) {
            throw "scheduled task execution time limit mismatch: $($spec.name)"
        }
        for ($index = 0; $index -lt $triggers.Count; $index += 1) {
            $trigger = $triggers[$index]
            if (-not [bool]$trigger.Enabled) { throw "scheduled task trigger disabled: $($spec.name)" }
            $expectedLocalTime = ([string]$spec.starts[$index]).TrimStart('T')
            $observedLocalTime = ([datetime]$trigger.StartBoundary).ToString('HH:mm:ss')
            if ($observedLocalTime -cne $expectedLocalTime -or [int]$trigger.DaysInterval -ne 1) {
                throw "scheduled task trigger mismatch: $($spec.name)"
            }
            if ([string]$spec.interval) {
                if ([string]$trigger.Repetition.Interval -ne [string]$spec.interval) {
                    throw "scheduled task repetition mismatch: $($spec.name)"
                }
                if ([string]$trigger.Repetition.Duration -ne [string]$spec.duration) {
                    throw "scheduled task repetition duration mismatch: $($spec.name)"
                }
                if ([bool]$trigger.Repetition.StopAtDurationEnd) {
                    throw "scheduled task repetition stop policy mismatch: $($spec.name)"
                }
            } elseif ([string]$trigger.Repetition.Interval -or [string]$trigger.Repetition.Duration) {
                throw "scheduled task unexpected repetition: $($spec.name)"
            }
        }
    }
    foreach ($disabledTaskName in @($PullTaskName, $LegacyRunnerTaskName)) {
        $disabledTask = Get-ScheduledTask -TaskPath '\' -TaskName $disabledTaskName -ErrorAction SilentlyContinue
        if ($disabledTask -and (
            $disabledTask.Settings.Enabled -or
            [string]$disabledTask.TaskPath -cne '\'
        )) {
            throw "legacy task state invalid: $disabledTaskName"
        }
    }
}

function Invoke-NewsGraspProductionEntryCanary {
    <#
    Lane A launcherが提供するentry-canary actionを一時的にProductionへ結び、
    Task実起動のnonce/generation receiptをboundedに確認してからcanonical dispatchへ戻す。
    receipt不一致・timeout・復元失敗はtransaction failureとして呼び出し元のrollbackへ渡す。
    #>
    param(
        [Parameter(Mandatory = $true)][string] $TaskName,
        [Parameter(Mandatory = $true)][string] $TaskLauncherPath,
        [Parameter(Mandatory = $true)][string] $PythonwPath,
        [Parameter(Mandatory = $true)][string] $WorkingDirectory,
        [Parameter(Mandatory = $true)][string] $GenerationId,
        [Parameter(Mandatory = $true)][string] $CanonicalArguments,
        [int] $TimeoutSeconds = 45
    )
    if ([string]::IsNullOrWhiteSpace($GenerationId) -or $GenerationId -ceq 'pending-active-generation') { throw 'NEWS_GRASP_ENTRY_CANARY_GENERATION_MISSING' }
    if (-not (Test-Path -LiteralPath $TaskLauncherPath -PathType Leaf)) { throw 'NEWS_GRASP_ENTRY_CANARY_LAUNCHER_MISSING' }
    if ($TimeoutSeconds -lt 5 -or $TimeoutSeconds -gt 180) { throw 'NEWS_GRASP_ENTRY_CANARY_TIMEOUT_INVALID' }
    $nonce = [Guid]::NewGuid().ToString('N')
    $receiptPath = Join-Path $BackupDir ("entry-canary-{0}.json" -f $nonce)
    $entryCanaryArguments = "-I -S -B `"$TaskLauncherPath`" task-origin-canary --canary-nonce $nonce --canary-generation `"$GenerationId`" --canary-receipt-path `"$receiptPath`""
    $canonicalAction = New-ScheduledTaskAction `
        -Execute $PythonwPath `
        -Argument $CanonicalArguments `
        -WorkingDirectory $WorkingDirectory
    $entryCanaryAction = New-ScheduledTaskAction `
        -Execute $PythonwPath `
        -Argument $entryCanaryArguments `
        -WorkingDirectory $WorkingDirectory
    $canaryTrigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddYears(10))
    $receipt = $null
    $productionTaskSnapshotXml = ''
    $productionTaskSnapshot = $null
    $productionTaskWasEnabled = $false
    $instanceClosed = $false
    try {
        # Full XML is the transaction snapshot.  Trigger quiescence precedes the
        # temporary action so the 06:00 Production trigger cannot fire.
        $productionTaskSnapshot = Get-ScheduledTask -TaskPath '\' -TaskName $TaskName -ErrorAction Stop
        $productionTaskSnapshotXml = Export-ScheduledTask -TaskPath '\' -TaskName $TaskName -ErrorAction Stop
        if ([string]::IsNullOrWhiteSpace($productionTaskSnapshotXml)) {
            throw 'NEWS_GRASP_ENTRY_CANARY_TASK_SNAPSHOT_MISSING'
        }
        $productionTaskWasEnabled = [bool]$productionTaskSnapshot.Settings.Enabled
        Disable-ScheduledTask -TaskPath '\' -TaskName $TaskName -ErrorAction Stop | Out-Null
        # Keep a valid far-future one-shot trigger while disabled; the full
        # snapshot restores the canonical 06:00 trigger.
        Set-ScheduledTask -TaskPath '\' -TaskName $TaskName -Trigger $canaryTrigger -Action $entryCanaryAction -ErrorAction Stop | Out-Null
        Enable-ScheduledTask -TaskPath '\' -TaskName $TaskName -ErrorAction Stop | Out-Null
        Start-ScheduledTask -TaskPath '\' -TaskName $TaskName -ErrorAction Stop
        $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
        do {
            if (Test-Path -LiteralPath $receiptPath -PathType Leaf) {
                try {
                    $receiptItem = Get-Item -LiteralPath $receiptPath -ErrorAction Stop
                    if ($receiptItem.Length -le 65536) {
                        $receipt = Get-Content -LiteralPath $receiptPath -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
                    }
                } catch { $receipt = $null }
            }
            $currentTask = Get-ScheduledTask -TaskPath '\' -TaskName $TaskName -ErrorAction Stop
            $currentTaskInfo = Get-ScheduledTaskInfo -TaskPath '\' -TaskName $TaskName -ErrorAction Stop
            $instanceClosed = [string]$currentTask.State -ne 'Running'
            if (
                $receipt -and
                [string]$receipt.schemaVersion -in @('NEWS_GRASP_TASK_ORIGIN_CANARY_RECEIPT_V1', 'NEWS_GRASP_TASK_ORIGIN_CANARY_RESULT_V1') -and
                [string]$receipt.nonce -ceq $nonce -and
                [string]$receipt.generation -ceq $GenerationId -and
                [string]$receipt.status -in @('committed', 'smoke_ok', 'ok', 'verified') -and
                $instanceClosed -and
                $currentTaskInfo.LastRunTime
            ) { break }
            Start-Sleep -Milliseconds 250
        } while ((Get-Date) -lt $deadline)
        if (-not $receipt -or [string]$receipt.nonce -cne $nonce -or [string]$receipt.generation -cne $GenerationId) {
            throw 'NEWS_GRASP_ENTRY_CANARY_RECEIPT_TIMEOUT_OR_MISMATCH'
        }
        if ([string]$receipt.status -notin @('committed', 'smoke_ok', 'ok', 'verified')) {
            throw 'NEWS_GRASP_ENTRY_CANARY_RECEIPT_NOT_GREEN'
        }
        if (-not $instanceClosed) {
            throw 'NEWS_GRASP_ENTRY_CANARY_TASK_INSTANCE_STILL_RUNNING'
        }
        return [ordered]@{
            status = 'green'
            nonce = $nonce
            generationId = $GenerationId
            receiptPath = $receiptPath
        }
    } finally {
        # Full task restoration is mandatory even when receipt validation fails.
        # Stop-ScheduledTask and bounded Get-ScheduledTask Running polling must
        # finish before Register-ScheduledTask restores the full XML snapshot.
        Stop-NewsGraspTaskAndWait -TaskName $TaskName -TimeoutSeconds 15
        if ($productionTaskSnapshotXml) {
            Register-ScheduledTask -TaskPath '\' -TaskName $TaskName -Xml $productionTaskSnapshotXml -Force -ErrorAction Stop | Out-Null
            if ($productionTaskWasEnabled) {
                Enable-ScheduledTask -TaskPath '\' -TaskName $TaskName -ErrorAction Stop | Out-Null
            } else {
                Disable-ScheduledTask -TaskPath '\' -TaskName $TaskName -ErrorAction Stop | Out-Null
            }
        } else {
            Set-ScheduledTask -TaskPath '\' -TaskName $TaskName -Action $canonicalAction -ErrorAction Stop | Out-Null
        }
        Assert-NewsGraspInstalledState
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
    'news-grasp-release-nopublish.ps1',
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
$taskPrincipalUserId = [Security.Principal.WindowsIdentity]::GetCurrent().Name
if ([string]::IsNullOrWhiteSpace($taskPrincipalUserId)) {
    throw 'NEWS_GRASP_TASK_PRINCIPAL_UNAVAILABLE'
}
$taskPrincipal = New-ScheduledTaskPrincipal `
    -UserId $taskPrincipalUserId `
    -LogonType Interactive `
    -RunLevel Limited
# runtime/task/asset の決定論的promotionはexternal model readinessと分離する。
# modelを必要とするrunner stageは、tools.news_grasp_daily_controlのpure probeで
# external authorityをfail-closedに検証し、unavailableならtyped deferredへ遷移する。
$ops = Join-Path $RepoDir 'scripts\ops'
$installTrustedBoundary = (Resolve-Path -LiteralPath $env:USERPROFILE).Path
$canonicalBinDir = Join-Path $installTrustedBoundary 'bin'
$managedTaskNames = @($RunnerTaskName, $BootstrapTaskName, $DeadmanTaskName, $PullTaskName, $LegacyRunnerTaskName)
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
$activeGenerationId = 'pending-active-generation'
$activeGenerationManifestSha256 = ''
$activeGenerationPointerSha256 = ''
# launcher の generation authority は production-runtime の親（.news-grasp-runtime）に
# pointer を持つ。installer も同じ root を読むことで、pending-active-generation
# へのフォールバックと launcher の実世代検証が分岐しないようにする。
$generationAuthorityRoot = Join-Path $env:USERPROFILE '.news-grasp-runtime'
$activeGenerationPointerPath = Join-Path $generationAuthorityRoot 'active-generation-v2.json'
if (Test-Path -LiteralPath $activeGenerationPointerPath -PathType Leaf) {
    $activeGenerationPointerSnapshot = Read-NewsGraspVerifiedFile `
        -Path $activeGenerationPointerPath `
        -TrustedBoundary $generationAuthorityRoot `
        -MaxBytes 65536 `
        -RequireSingleLink
    try {
        $activeGenerationPointer = [Text.Encoding]::UTF8.GetString($activeGenerationPointerSnapshot.Bytes) | ConvertFrom-Json -ErrorAction Stop
    } catch { throw 'NEWS_GRASP_ACTIVE_GENERATION_INVALID' }
    if (
        [string]$activeGenerationPointer.schemaVersion -notin @(
            'NEWS_GRASP_ACTIVE_GENERATION_V1',
            'NEWS_GRASP_ACTIVE_GENERATION_V2'
        ) -or
        [string]::IsNullOrWhiteSpace([string]$activeGenerationPointer.generationId) -or
        [string]::IsNullOrWhiteSpace([string]$activeGenerationPointer.manifestSha256)
    ) { throw 'NEWS_GRASP_ACTIVE_GENERATION_INVALID' }
    $activeGenerationId = [string]$activeGenerationPointer.generationId
    $activeGenerationManifestSha256 = ([string]$activeGenerationPointer.manifestSha256).ToLowerInvariant()
    $activeGenerationPointerSha256 = ([string]$activeGenerationPointerSnapshot.Sha256).ToLowerInvariant()
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
foreach ($asset in $automationAssetRows) {
    Assert-NewsGraspAutomationProjectionAsset `
        -Asset $asset `
        -Bytes $assetSourceSnapshots[[string]$asset.assetId].Bytes
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
    foreach ($taskName in @($RunnerTaskName, $BootstrapTaskName, $DeadmanTaskName, $PullTaskName, $LegacyRunnerTaskName)) {
        $taskMatches = @(Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue)
        if (
            $taskMatches.Count -gt 1 -or
            @($taskMatches | Where-Object { [string]$_.TaskPath -cne '\' }).Count -gt 0
        ) {
            throw "NEWS_GRASP_TASK_PATH_AUTHORITY_INVALID:$taskName"
        }
        $task = @($taskMatches | Where-Object { [string]$_.TaskPath -ceq '\' }) | Select-Object -First 1
        $xmlPath = Join-Path $BackupDir (("task-{0}.xml" -f ($taskName -replace '[^A-Za-z0-9._-]', '_')))
        $taskXmlSha256 = ''
        if ($task) {
            $taskXml = Export-ScheduledTask -TaskPath '\' -TaskName $taskName
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
$trustedGitRemote = 'https://github.com/HIDEPON-UMG/News-Grasp.git'
$recoveryGitExe = 'C:\Program Files\Git\cmd\git.exe'
$env:GIT_TERMINAL_PROMPT = '0'
$recoveryGitSafeArgs = @(
    '-c', 'core.hooksPath=NUL',
    '-c', 'core.fsmonitor=false',
    '-c', 'core.attributesFile=NUL',
    '-c', 'http.lowSpeedLimit=1',
    '-c', 'http.lowSpeedTime=15'
)
$opsHead = (& $recoveryGitExe @recoveryGitSafeArgs -C $runtimeEvidenceRepoDir rev-parse HEAD 2>$null | Out-String).Trim().ToLowerInvariant()
$trustedRemoteHeadLine = (& $recoveryGitExe @recoveryGitSafeArgs ls-remote $trustedGitRemote refs/heads/main 2>$null | Out-String).Trim()
$trustedRemoteHead = if ($trustedRemoteHeadLine) { ($trustedRemoteHeadLine -split '\s+')[0].ToLowerInvariant() } else { '' }
$opsDirty = (& $recoveryGitExe @recoveryGitSafeArgs -C $runtimeEvidenceRepoDir status --porcelain --untracked-files=all 2>$null | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $opsHead -notmatch '^[0-9a-f]{40}$' -or $opsHead -ne $trustedRemoteHead -or $opsDirty) {
    throw 'NEWS_GRASP_RECOVERY_OPS_GENERATION_INVALID'
}
$scheduledTasks = @()
$rollbackCommands = @('Invoke-NewsGraspInstallRollback')
Write-NewsGraspInstallJournal -Phase 'prepared'
$script:InstallationMutationStarted = $true
if (-not $SkipTaskRegistration) {
    Enter-NewsGraspTaskMutationBoundary
    Write-NewsGraspInstallJournal -Phase 'tasks_quiesced'
}
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
$pythonwSnapshot = Read-NewsGraspVerifiedFile `
    -Path $TaskPythonwPath `
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
$auditControlSnapshot = Read-NewsGraspVerifiedFile `
    -Path (Join-Path $runtimeEvidenceRepoDir 'tools\audit_recovery_control.py') `
    -TrustedBoundary $installTrustedBoundary `
    -RequireSingleLink
$recoveryCloseoutToolSnapshot = Read-NewsGraspVerifiedFile `
    -Path (Join-Path $runtimeEvidenceRepoDir 'tools\news_grasp_recovery_closeout.py') `
    -TrustedBoundary $installTrustedBoundary `
    -RequireSingleLink
$operationalContractToolSnapshot = Read-NewsGraspVerifiedFile `
    -Path (Join-Path $runtimeEvidenceRepoDir 'tools\news_grasp_operational_contract.py') `
    -TrustedBoundary $installTrustedBoundary `
    -RequireSingleLink
$startupCustomizationPresent = (
    (Test-Path -LiteralPath (Join-Path $runtimeEvidenceRepoDir 'sitecustomize.py')) -or
    (Test-Path -LiteralPath (Join-Path $runtimeEvidenceRepoDir 'usercustomize.py'))
)
if ($startupCustomizationPresent) { throw 'NEWS_GRASP_RECOVERY_OPS_STARTUP_CUSTOMIZATION_FORBIDDEN' }
$pythonSignature = Get-AuthenticodeSignature -LiteralPath $runtimePythonPath
$pythonSignerSubject = [string]$pythonSignature.SignerCertificate.Subject
$pythonSignerThumbprint = ([string]$pythonSignature.SignerCertificate.Thumbprint).ToLowerInvariant()
if (
    [string]$pythonSignature.Status -cne 'Valid' -or
    $pythonSignerSubject -notlike 'CN=Python Software Foundation, O=Python Software Foundation,*' -or
    $pythonSignerThumbprint -notmatch '^[0-9a-f]{40}$'
) { throw 'NEWS_GRASP_RECOVERY_PYTHON_TRUST_ANCHOR_INVALID' }
$pythonwSignature = Get-AuthenticodeSignature -LiteralPath $TaskPythonwPath
$pythonwSignerSubject = [string]$pythonwSignature.SignerCertificate.Subject
$pythonwSignerThumbprint = ([string]$pythonwSignature.SignerCertificate.Thumbprint).ToLowerInvariant()
if (
    [string]$pythonwSignature.Status -cne 'Valid' -or
    $pythonwSignerSubject -notlike 'CN=Python Software Foundation, O=Python Software Foundation,*' -or
    $pythonwSignerThumbprint -notmatch '^[0-9a-f]{40}$' -or
    $pythonwSignerThumbprint -cne $pythonSignerThumbprint
) { throw 'NEWS_GRASP_RECOVERY_PYTHONW_TRUST_ANCHOR_INVALID' }
$recoveryRuntimeBinding = [ordered]@{
    schemaVersion = 'NEWS_GRASP_RECOVERY_RUNTIME_BINDING_V1'
    opsRepoRoot = $runtimeEvidenceRepoDir
    opsHead = $opsHead
    trustedRemote = $trustedGitRemote
    productionRuntimeRoot = $productionRuntimePath
    pythonExe = $runtimePythonPath
    pythonExeSha256 = ([string]$pythonSnapshot.Sha256).ToLowerInvariant()
    taskPythonwPath = $TaskPythonwPath
    taskPythonwSha256 = ([string]$pythonwSnapshot.Sha256).ToLowerInvariant()
    pythonTrustAnchor = 'authenticode:python-software-foundation'
    pythonSignerSubject = $pythonSignerSubject
    pythonSignerThumbprint = $pythonSignerThumbprint
    pythonwTrustAnchor = 'authenticode:python-software-foundation'
    pythonwSignerSubject = $pythonwSignerSubject
    pythonwSignerThumbprint = $pythonwSignerThumbprint
    receiptToolPath = (Join-Path $runtimeEvidenceRepoDir 'tools\news_grasp_recovery_receipts.py')
    receiptToolSha256 = ([string]$receiptToolSnapshot.Sha256).ToLowerInvariant()
    controlPlaneToolPath = (Join-Path $runtimeEvidenceRepoDir 'tools\news_grasp_control_plane.py')
    controlPlaneToolSha256 = ([string]$controlPlaneToolSnapshot.Sha256).ToLowerInvariant()
    completionGuardToolPath = (Join-Path $runtimeEvidenceRepoDir 'tools\news_grasp_completion_guard.py')
    completionGuardToolSha256 = ([string]$completionGuardToolSnapshot.Sha256).ToLowerInvariant()
    dailySelfHealPath = (Join-Path $runtimeEvidenceRepoDir 'tools\daily_self_heal.py')
    dailySelfHealSha256 = ([string]$dailySelfHealSnapshot.Sha256).ToLowerInvariant()
    auditControlPath = (Join-Path $runtimeEvidenceRepoDir 'tools\audit_recovery_control.py')
    auditControlSha256 = ([string]$auditControlSnapshot.Sha256).ToLowerInvariant()
    recoveryCloseoutToolPath = (Join-Path $runtimeEvidenceRepoDir 'tools\news_grasp_recovery_closeout.py')
    recoveryCloseoutToolSha256 = ([string]$recoveryCloseoutToolSnapshot.Sha256).ToLowerInvariant()
    operationalContractToolPath = (Join-Path $runtimeEvidenceRepoDir 'tools\news_grasp_operational_contract.py')
    operationalContractToolSha256 = ([string]$operationalContractToolSnapshot.Sha256).ToLowerInvariant()
    highCostBindingPath = $highCostBindingPath
    highCostBindingReceiptSha256 = $highCostBindingReceiptSha256
    highCostBindingFileSha256 = ([string]$highCostBindingAfterHash).ToLowerInvariant()
    highCostBindingResolverPath = $highCostBindingResolverDestination
    highCostBindingResolverSha256 = ([string]$highCostBindingResolverAfterHash).ToLowerInvariant()
    bootstrapPath = (Join-Path $BinDir 'news-grasp-bootstrap.ps1')
    bootstrapSha256 = ([string]$sourceSnapshots['news-grasp-bootstrap.ps1'].Sha256).ToLowerInvariant()
    # V1 field名は互換維持するが、identityは廃止済みrunnerではなく
    # stable direct task launcherへ束縛する。
    runnerPath = (Join-Path $BinDir 'news-grasp-task-launcher.pyw')
    runnerSha256 = ([string]$sourceSnapshots['news-grasp-task-launcher.pyw'].Sha256).ToLowerInvariant()
    lineagePath = (Join-Path $BinDir 'news-grasp-lineage.ps1')
    lineageSha256 = ([string]$sourceSnapshots['news-grasp-lineage.ps1'].Sha256).ToLowerInvariant()
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
    # 旧consumerが読むaction listを維持しつつ、production Taskの実引数を
    # clean-room dispatchへ固定する。
    action = @($TaskPythonwPath, '-I', '-S', '-B', (Join-Path $BinDir 'news-grasp-task-launcher.pyw'), 'dispatch', '--schedule-id', 'news-grasp-daily-v1', '--intent', 'reconcile')
    trigger = @{ daily = '06:00' }
    manifestAction = [ordered]@{
        entryModule = 'tools.news_grasp_cleanroom_dispatch'
        argv = @('dispatch', '--schedule-id', 'news-grasp-daily-v1', '--intent', 'reconcile')
        workingDirectoryToken = '<RUNTIME_ROOT>'
    }
    triggers = @(
        [ordered]@{ triggerId = 'scheduled-0600'; kind = 'daily'; localTime = '06:00:00'; timeZone = 'Asia/Tokyo' }
    )
    taskPath = '\'
    multipleInstancesPolicy = 'IgnoreNew'
    principal = [ordered]@{
        userId = $taskPrincipalUserId
        logonType = 'Interactive'
        runLevel = 'Limited'
    }
    workingDirectoryToken = '<RUNTIME_ROOT>'
    highCostBindingPath = $highCostBindingPath
    highCostBindingReceiptSha256 = $highCostBindingReceiptSha256
    repoArgumentCount = 0
}
$stableTaskAuthority = (($stableTaskAuthority | ConvertTo-Json -Depth 6) | ConvertFrom-Json -ErrorAction Stop)
$stableAuthorityBody = (($stableTaskAuthority | ConvertTo-Json -Depth 6 -Compress) `
    -replace '\\u0026', '&' `
    -replace '\\u0027', "'" `
    -replace '\\u003c', '<' `
    -replace '\\u003e', '>')
$stableAuthorityHasher = [Security.Cryptography.SHA256]::Create()
try {
    $stableAuthorityBytes = [Text.Encoding]::UTF8.GetBytes($stableAuthorityBody)
    $stableAuthoritySha256 = ([BitConverter]::ToString($stableAuthorityHasher.ComputeHash($stableAuthorityBytes)) -replace '-', '').ToLowerInvariant()
} finally { $stableAuthorityHasher.Dispose() }
$stableTaskAuthority | Add-Member -NotePropertyName authoritySha256 -NotePropertyValue $stableAuthoritySha256
Write-AtomicUtf8Text -Path $stableTaskAuthorityPath -Text (($stableTaskAuthority | ConvertTo-Json -Depth 6) + [Environment]::NewLine)
$stableAuthorityValidationScript = 'import json,pathlib,sys;sys.path.insert(0,sys.argv[2]);from tools.news_grasp_generation import validate_stable_task_authority;authority=json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8-sig"));print(validate_stable_task_authority(authority)["authoritySha256"])'
$stableAuthorityValidationOutput = @(
    & $runtimePythonPath -I -c $stableAuthorityValidationScript $stableTaskAuthorityPath $runtimeEvidenceRepoDir 2>&1
)
if (
    $LASTEXITCODE -ne 0 -or
    $stableAuthorityValidationOutput.Count -ne 1 -or
    [string]$stableAuthorityValidationOutput[0] -cne [string]$stableTaskAuthority.authoritySha256
) {
    throw 'NEWS_GRASP_STABLE_TASK_AUTHORITY_INVALID'
}
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
    $brokerStdoutPath = Join-Path $BackupDir 'audit-mission-broker.stdout.txt'
    $brokerStderrPath = Join-Path $BackupDir 'audit-mission-broker.stderr.txt'
    $brokerProcess = Start-Process `
        -FilePath $pythonPath `
        -ArgumentList @($brokerPath, 'issue-news-grasp-audit-mission') `
        -RedirectStandardOutput $brokerStdoutPath `
        -RedirectStandardError $brokerStderrPath `
        -WindowStyle Hidden `
        -PassThru
    if (-not $brokerProcess.WaitForExit(30000)) {
        $brokerProcess.Kill()
        $brokerProcess.WaitForExit()
        throw 'NEWS_GRASP_AUDIT_MISSION_BROKER_TIMEOUT'
    }
    $missionAuthorityJson = (Get-Content -LiteralPath $brokerStdoutPath -Raw -Encoding UTF8).Trim()
    if ($brokerProcess.ExitCode -ne 0) {
        $brokerError = (Get-Content -LiteralPath $brokerStderrPath -Raw -Encoding UTF8).Trim()
        throw "audit mission authority issuance failed exit=$($brokerProcess.ExitCode):$brokerError"
    }
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
    Assert-NewsGraspTaskMutationBoundaryCurrent
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
    if (-not (Test-Path -LiteralPath $pythonw)) { throw 'News-Grasp system Python312 pythonw.exe が見つかりません。' }
    # Scheduled Taskはstable installed launcherだけを指す。source worktreeのpathをtask定義へ封印しない。
    $runnerArgs = "-I -S -B `"$taskLauncherPath`" dispatch --schedule-id news-grasp-daily-v1 --intent reconcile"
    $runnerAction = New-ScheduledTaskAction -Execute $pythonw -Argument $runnerArgs -WorkingDirectory $productionRuntimePath
    $runnerTrigger = New-ScheduledTaskTrigger -Daily -At 6:00am
    $runnerSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew
    $runnerRegistered = $false
    $runnerRegisterError = ''
    try {
        Assert-NewsGraspTaskMutationBoundaryCurrent
        Register-ScheduledTask -TaskPath '\' -TaskName $RunnerTaskName -Action $runnerAction -Trigger $runnerTrigger -Settings $runnerSettings -Principal $taskPrincipal -Description 'News-Grasp canonical clean-room dispatch.' -Force -ErrorAction Stop | Out-Null
        Set-NewsGraspTaskMutationApplied -TaskName $RunnerTaskName
        Enable-ScheduledTask -TaskPath '\' -TaskName $RunnerTaskName -ErrorAction Stop | Out-Null
        $runnerRegistered = $true
        $scheduledTasks += [ordered]@{
            task_name = $RunnerTaskName
            execute = $pythonw
            arguments = $runnerArgs
            workingDirectory = $productionRuntimePath
            taskPath = '\'
            multipleInstancesPolicy = 'IgnoreNew'
            triggers = @(
                [ordered]@{ triggerId = 'scheduled-0600'; kind = 'daily'; localTime = '06:00:00'; timeZone = 'Asia/Tokyo' }
            )
            action = [ordered]@{
                entryModule = 'tools.news_grasp_cleanroom_dispatch'
                argv = @('dispatch', '--schedule-id', 'news-grasp-daily-v1', '--intent', 'reconcile')
                workingDirectoryToken = '<RUNTIME_ROOT>'
            }
            status = 'registered_cleanroom_dispatch'
        }
    } catch {
        $runnerRegisterError = $_.Exception.Message
        $scheduledTasks += [ordered]@{
            task_name = $RunnerTaskName
            execute = $pythonw
            arguments = $runnerArgs
            workingDirectory = $productionRuntimePath
            taskPath = '\'
            multipleInstancesPolicy = 'IgnoreNew'
            triggers = @(
                [ordered]@{ triggerId = 'scheduled-0600'; kind = 'daily'; localTime = '06:00:00'; timeZone = 'Asia/Tokyo' }
            )
            status = 'register_failed_bootstrap_required'
            error = $runnerRegisterError
        }
    }

    $bootstrapArgs = "-I -S -B `"$taskLauncherPath`" bootstrap --scheduled-task-name `"$BootstrapTaskName`" --high-cost-binding-path `"$highCostBindingPath`" --high-cost-binding-sha256 $highCostBindingReceiptSha256"
    $bootstrapAction = New-ScheduledTaskAction -Execute $pythonw -Argument $bootstrapArgs -WorkingDirectory $BinDir
    $bootstrapTrigger = New-ScheduledTaskTrigger -Daily -At 5:55am
    $bootstrapSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew
    try {
        Assert-NewsGraspTaskMutationBoundaryCurrent
        Register-ScheduledTask -TaskPath '\' -TaskName $BootstrapTaskName -Action $bootstrapAction -Trigger $bootstrapTrigger -Settings $bootstrapSettings -Principal $taskPrincipal -Description 'News-Grasp pre-run self repair bootstrap.' -Force -ErrorAction Stop | Out-Null
        Set-NewsGraspTaskMutationApplied -TaskName $BootstrapTaskName
        Enable-ScheduledTask -TaskPath '\' -TaskName $BootstrapTaskName -ErrorAction Stop | Out-Null
        $scheduledTasks += [ordered]@{
            task_name = $BootstrapTaskName
            execute = $pythonw
            arguments = $bootstrapArgs
            workingDirectory = $BinDir
            taskPath = '\'
            multipleInstancesPolicy = 'IgnoreNew'
            triggers = @(
                [ordered]@{ triggerId = 'bootstrap-0555'; kind = 'daily'; localTime = '05:55:00'; timeZone = 'Asia/Tokyo' }
            )
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
            workingDirectory = $BinDir
            taskPath = '\'
            multipleInstancesPolicy = 'IgnoreNew'
            triggers = @(
                [ordered]@{ triggerId = 'bootstrap-0555'; kind = 'daily'; localTime = '05:55:00'; timeZone = 'Asia/Tokyo' }
            )
            status = 'create_failed'
            error = $_.Exception.Message
        }
    }

    $deadmanArgs = "-I -S -B `"$deadmanLauncherPath`""
    $deadmanAction = New-ScheduledTaskAction -Execute $pythonw -Argument $deadmanArgs -WorkingDirectory $BinDir
    $deadmanTrigger = New-ScheduledTaskTrigger -Daily -At 6:40am
    $deadmanRepetition = New-CimInstance -Namespace 'Root/Microsoft/Windows/TaskScheduler' -ClassName 'MSFT_TaskRepetitionPattern' -ClientOnly -Property @{
        Interval = 'PT1H'
        Duration = 'P1D'
        StopAtDurationEnd = $false
    }
    $deadmanTrigger.Repetition = $deadmanRepetition
    $deadmanSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 105)
    Assert-NewsGraspTaskMutationBoundaryCurrent
    Register-ScheduledTask -TaskPath '\' -TaskName $DeadmanTaskName -Action $deadmanAction -Trigger $deadmanTrigger -Settings $deadmanSettings -Principal $taskPrincipal -Description 'News-Grasp hourly audit and bounded recovery control.' -Force -ErrorAction Stop | Out-Null
    Set-NewsGraspTaskMutationApplied -TaskName $DeadmanTaskName
    Enable-ScheduledTask -TaskPath '\' -TaskName $DeadmanTaskName -ErrorAction Stop | Out-Null
    $scheduledTasks += [ordered]@{
        task_name = $DeadmanTaskName
        execute = $pythonw
        arguments = $deadmanArgs
        trigger = 'daily 06:40 with hourly repetition'
        status = 'registered_deadman_control'
    }
    Assert-NewsGraspTaskMutationBoundaryCurrent
    foreach ($disabledTaskName in @($PullTaskName, $LegacyRunnerTaskName)) {
        $disabledTask = Get-ScheduledTask -TaskPath '\' -TaskName $disabledTaskName -ErrorAction SilentlyContinue
        if ($disabledTask -and $disabledTask.Settings.Enabled) {
            Disable-ScheduledTask -TaskPath '\' -TaskName $disabledTaskName -ErrorAction Stop | Out-Null
        }
        $disabledEnabledAfter = $false
        if ($disabledTask) {
            $disabledEnabledAfter = [bool](Get-ScheduledTask -TaskPath '\' -TaskName $disabledTaskName -ErrorAction Stop).Settings.Enabled
        }
        $scheduledTasks += [ordered]@{
            task_name = $disabledTaskName
            status = if ($disabledTask) { 'legacy_task_disabled' } else { 'legacy_task_absent' }
            enabled = $disabledEnabledAfter
        }
        Set-NewsGraspTaskMutationApplied -TaskName $disabledTaskName
    }
    $entryCanary = Invoke-NewsGraspProductionEntryCanary `
        -TaskName $RunnerTaskName `
        -TaskLauncherPath $taskLauncherPath `
        -PythonwPath $pythonw `
        -WorkingDirectory $productionRuntimePath `
        -GenerationId $activeGenerationId `
        -CanonicalArguments $runnerArgs `
        -TimeoutSeconds 45
    $scheduledTasks += [ordered]@{
        task_name = $RunnerTaskName
        status = 'entry_canary_green'
        nonce = [string]$entryCanary.nonce
        generationId = [string]$entryCanary.generationId
        receiptPath = [string]$entryCanary.receiptPath
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
    generationBinding = [ordered]@{
        schemaVersion = 'RUN_ENVELOPE_V1'
        generationId = $activeGenerationId
        activeGenerationManifestSha256 = $activeGenerationManifestSha256
        activeGenerationPointerSha256 = $activeGenerationPointerSha256
        stableTaskAuthoritySha256 = ([string]$stableTaskAuthorityInstalled.Sha256).ToLowerInvariant()
        automationAssetManifestSha256 = ((Get-FileHash -LiteralPath $automationAssetManifestPath -Algorithm SHA256).Hash).ToLowerInvariant()
        sourceRoot = $RepoDir
        installedRoot = $BinDir
        runtimeRoot = $runtimeEvidenceRepoDir
    }
}
$installedEvidenceJson = $installedEvidenceBody | ConvertTo-Json -Depth 10 -Compress
$installedEvidenceHasher = [Security.Cryptography.SHA256]::Create()
try {
$installedEvidenceSha256 = ([BitConverter]::ToString(
        $installedEvidenceHasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($installedEvidenceJson))
    ) -replace '-', '').ToLowerInvariant()
} finally { $installedEvidenceHasher.Dispose() }
$pendingEvidence = [ordered]@{ status = 'pending'; evidenceSha256 = ''; reasonCode = 'AWAITING_RELEASE_EVIDENCE' }
$runtimeGenerationEvidence = if (
    $activeGenerationId -and
    $activeGenerationId -cne 'pending-active-generation' -and
    $activeGenerationManifestSha256 -match '^[0-9a-f]{64}$' -and
    $activeGenerationPointerSha256 -match '^[0-9a-f]{64}$'
) {
    [ordered]@{ status = 'green'; evidenceSha256 = $installedEvidenceSha256 }
} else {
    [ordered]@{ status = 'pending'; evidenceSha256 = ''; reasonCode = 'AWAITING_ACTIVE_GENERATION' }
}
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
    runtimeGenerationFresh = $runtimeGenerationEvidence
    scheduledTaskParity = $taskEvidence
    rollbackReceipt = $greenEvidence
    noPublishE2E = $e2ePendingEvidence
}
$deliveryStateBody = [ordered]@{
    schemaVersion = 'NEWS_GRASP_PHYSICAL_DELIVERY_STATE_V1'
    generationId = $activeGenerationId
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
if ($taskWriterLeaseAcquired) {
    $taskWriterMutex.ReleaseMutex()
    $taskWriterMutex.Dispose()
    $taskWriterLeaseAcquired = $false
}
Write-Host "News-Grasp ops scripts installed to $BinDir"
Write-Host "Backup manifest: $ManifestPath"
