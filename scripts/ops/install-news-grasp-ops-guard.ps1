Set-StrictMode -Version Latest

function Get-NewsGraspCanonicalPath {
    param([Parameter(Mandatory = $true)][string] $Path)
    return [System.IO.Path]::GetFullPath($Path).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
}

function Test-NewsGraspSamePath {
    param([string] $Left, [string] $Right)
    return [string]::Equals(
        (Get-NewsGraspCanonicalPath -Path $Left),
        (Get-NewsGraspCanonicalPath -Path $Right),
        [System.StringComparison]::OrdinalIgnoreCase
    )
}

function Assert-NewsGraspNoReparsePath {
    param([Parameter(Mandatory = $true)][string] $Path)
    $cursor = Get-NewsGraspCanonicalPath -Path $Path
    while ($cursor) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw 'NEWS_GRASP_INSTALL_JOURNAL_REPARSE_POINT_FORBIDDEN'
            }
        }
        $parent = Split-Path -Parent $cursor
        if (-not $parent -or (Test-NewsGraspSamePath -Left $parent -Right $cursor)) { break }
        $cursor = $parent
    }
}

function Assert-NewsGraspExactKeys {
    param([object] $Value, [string[]] $Expected, [string] $Code)
    if ($null -eq $Value) { throw $Code }
    $actual = @($Value.PSObject.Properties.Name | Sort-Object)
    $wanted = @($Expected | Sort-Object)
    if ($actual.Count -ne $wanted.Count -or @(Compare-Object $actual $wanted).Count -ne 0) {
        throw $Code
    }
}

function Assert-NewsGraspRecoveryJournal {
    param(
        [Parameter(Mandatory = $true)][string] $JournalPath,
        [Parameter(Mandatory = $true)][object] $Journal,
        [Parameter(Mandatory = $true)][string] $ExpectedBackupRoot,
        [Parameter(Mandatory = $true)][string] $ExpectedRepoDir,
        [Parameter(Mandatory = $true)][string] $ExpectedBinDir,
        [Parameter(Mandatory = $true)][string[]] $ExpectedTaskNames
    )

    Assert-NewsGraspNoReparsePath -Path $ExpectedBackupRoot
    Assert-NewsGraspNoReparsePath -Path $ExpectedBinDir
    Assert-NewsGraspNoReparsePath -Path $JournalPath
    Assert-NewsGraspExactKeys -Value $Journal -Expected @(
        'schemaVersion', 'transaction_id', 'phase', 'updated_at', 'repo_dir',
        'bin_dir', 'task_pythonw_path', 'bin_dir_existed_before', 'backup_dir',
        'files', 'rollback_commands', 'mission_authority', 'scheduled_tasks',
        'task_snapshots'
    ) -Code 'NEWS_GRASP_INSTALL_JOURNAL_SCHEMA_INVALID'
    if (
        [string]$Journal.schemaVersion -ne 'NEWS_GRASP_OPS_INSTALL_JOURNAL_V1' -or
        [string]$Journal.transaction_id -notmatch '^\d{8}-\d{6}$' -or
        [string]$Journal.phase -notin @('prepared', 'files_installed', 'authority_issued', 'tasks_registered')
    ) {
        throw 'NEWS_GRASP_INSTALL_JOURNAL_SCHEMA_INVALID'
    }

    $journalFileName = [System.IO.Path]::GetFileName($JournalPath)
    $journalDir = Split-Path -Parent (Get-NewsGraspCanonicalPath -Path $JournalPath)
    $expectedJournalDir = Join-Path (Get-NewsGraspCanonicalPath -Path $ExpectedBackupRoot) ([string]$Journal.transaction_id)
    if (
        $journalFileName -ne 'install-manifest.json' -or
        -not (Test-NewsGraspSamePath -Left $journalDir -Right $expectedJournalDir) -or
        -not (Test-NewsGraspSamePath -Left ([string]$Journal.backup_dir) -Right $journalDir) -or
        -not (Test-NewsGraspSamePath -Left ([string]$Journal.repo_dir) -Right $ExpectedRepoDir) -or
        -not (Test-NewsGraspSamePath -Left ([string]$Journal.bin_dir) -Right $ExpectedBinDir)
    ) {
        throw 'NEWS_GRASP_INSTALL_JOURNAL_PATH_INVALID'
    }

    $allowedFiles = @(
        'run_codex_with_timeout.ps1',
        'news-grasp-bootstrap.ps1',
        'news-grasp-runner.ps1',
        'news-grasp-lineage.ps1',
        'watch-news-grasp-runner.ps1',
        'news-grasp-deadman.ps1',
        'news-grasp-deadman-launcher.pyw',
        'news-grasp-task-launcher.pyw',
        'audit-mission-authority-v1.json'
    )
    $seenFiles = @{}
    foreach ($row in @($Journal.files)) {
        Assert-NewsGraspExactKeys -Value $row -Expected @(
            'file', 'source', 'destination', 'backup', 'before_sha256',
            'source_sha256', 'after_sha256'
        ) -Code 'NEWS_GRASP_INSTALL_JOURNAL_FILE_SCHEMA_INVALID'
        $fileName = [string]$row.file
        if ($fileName -notin $allowedFiles -or $seenFiles.ContainsKey($fileName)) {
            throw 'NEWS_GRASP_INSTALL_JOURNAL_FILE_NOT_ALLOWED'
        }
        $seenFiles[$fileName] = $true
        if ($fileName -eq 'audit-mission-authority-v1.json') {
            $expectedDestination = Join-Path (Join-Path $ExpectedBinDir 'news-grasp-authority') $fileName
        } else {
            $expectedDestination = Join-Path $ExpectedBinDir $fileName
        }
        if (-not (Test-NewsGraspSamePath -Left ([string]$row.destination) -Right $expectedDestination)) {
            throw 'NEWS_GRASP_INSTALL_JOURNAL_DESTINATION_INVALID'
        }
        Assert-NewsGraspNoReparsePath -Path ([string]$row.destination)
        if ([string]$row.backup) {
            $expectedBackup = Join-Path $journalDir $fileName
            if (-not (Test-NewsGraspSamePath -Left ([string]$row.backup) -Right $expectedBackup)) {
                throw 'NEWS_GRASP_INSTALL_JOURNAL_BACKUP_INVALID'
            }
            Assert-NewsGraspNoReparsePath -Path ([string]$row.backup)
        }
    }

    $rollbackCommands = @($Journal.rollback_commands)
    if ($rollbackCommands.Count -ne 1 -or [string]$rollbackCommands[0] -ne 'Invoke-NewsGraspInstallRollback') {
        throw 'NEWS_GRASP_INSTALL_JOURNAL_SCHEMA_INVALID'
    }
    $snapshots = @($Journal.task_snapshots)
    if ($snapshots.Count -notin @(0, $ExpectedTaskNames.Count)) {
        throw 'NEWS_GRASP_INSTALL_JOURNAL_TASK_INVALID'
    }
    $seenTasks = @{}
    foreach ($snapshot in $snapshots) {
        Assert-NewsGraspExactKeys -Value $snapshot -Expected @(
            'task_name', 'existed_before', 'enabled_before', 'xml_backup'
        ) -Code 'NEWS_GRASP_INSTALL_JOURNAL_TASK_SCHEMA_INVALID'
        $taskName = [string]$snapshot.task_name
        if ($taskName -notin $ExpectedTaskNames -or $seenTasks.ContainsKey($taskName)) {
            throw 'NEWS_GRASP_INSTALL_JOURNAL_TASK_INVALID'
        }
        $seenTasks[$taskName] = $true
        if ([bool]$snapshot.existed_before) {
            $expectedXml = Join-Path $journalDir (("task-{0}.xml" -f ($taskName -replace '[^A-Za-z0-9._-]', '_')))
            if (-not (Test-NewsGraspSamePath -Left ([string]$snapshot.xml_backup) -Right $expectedXml)) {
                throw 'NEWS_GRASP_INSTALL_JOURNAL_TASK_XML_INVALID'
            }
            Assert-NewsGraspNoReparsePath -Path ([string]$snapshot.xml_backup)
        } elseif ([string]$snapshot.xml_backup) {
            throw 'NEWS_GRASP_INSTALL_JOURNAL_TASK_XML_INVALID'
        }
    }
    return $true
}
