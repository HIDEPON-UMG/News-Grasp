param(
    [switch] $Start,
    [switch] $StartOnly,
    [switch] $SmokeTest,
    [switch] $RecoverOnly,
    [int] $PollSeconds = 30,
    [int] $StaleMinutes = 15,
    [int] $TimeoutMinutes = 120,
    [string] $RunnerPath = '',
    [string] $StateFile = '',
    [string] $LogDir = '',
    [string] $DateStamp = '',
    [string] $RepoDir = '',
    [string] $BinDir = (Join-Path $env:USERPROFILE 'bin')
)

$ErrorActionPreference = 'Stop'
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

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
    $task = Get-ScheduledTask -TaskName 'News-Grasp Runner' -ErrorAction Stop
    $summary = (@($task.Actions) | ForEach-Object {
        ([string]$_.Execute + ' ' + [string]$_.Arguments).Trim()
    }) -join ' ; '
    return Get-StringSha256Hex -Text $summary.Trim().ToLowerInvariant()
}

$RepoDir = Resolve-NewsGraspRepoDir -Override $RepoDir
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
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
foreach ($file in @('news-grasp-bootstrap.ps1', 'watch-news-grasp-runner.ps1', 'news-grasp-runner.ps1', 'news-grasp-deadman.ps1', 'news-grasp-deadman-launcher.pyw')) {
    $source = Join-Path $opsDir $file
    $destination = Join-Path $BinDir $file
    if (-not (Test-Path -LiteralPath $source)) {
        throw "repo ops script missing: $source"
    }
}

$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backupDir = Join-Path $RepoDir "build\live-bootstrap-self-repair\$timestamp"
$manifestPath = Join-Path $backupDir 'auto-repair-manifest.json'
$manifestFiles = @()
$changed = $false

foreach ($file in @('news-grasp-bootstrap.ps1', 'watch-news-grasp-runner.ps1', 'news-grasp-runner.ps1', 'news-grasp-deadman.ps1', 'news-grasp-deadman-launcher.pyw')) {
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

if (-not $DateStamp) { $DateStamp = Get-Date -Format 'yyyy-MM-dd' }
$broker = Join-Path $env:USERPROFILE 'bin\ai-model-spawn-broker.py'
$python = Join-Path $RepoDir '.venv\Scripts\python.exe'
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
$taskActionSha256 = Get-ScheduledTaskActionSha256
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
if ($RecoverOnly) { $args += '-RecoverOnly' }
if ($PollSeconds -ne 30) { $args += @('-PollSeconds', [string]$PollSeconds) }
if ($StaleMinutes -ne 15) { $args += @('-StaleMinutes', [string]$StaleMinutes) }
if ($TimeoutMinutes -ne 120) { $args += @('-TimeoutMinutes', [string]$TimeoutMinutes) }
if ($RunnerPath) { $args += @('-RunnerPath', $RunnerPath) }
if ($StateFile) { $args += @('-StateFile', $StateFile) }
if ($LogDir) { $args += @('-LogDir', $LogDir) }
if ($DateStamp) { $args += @('-DateStamp', $DateStamp) }
$args += @('-RepoDir', $RepoDir, '-BinDir', $BinDir)

& powershell.exe @args
exit $LASTEXITCODE
