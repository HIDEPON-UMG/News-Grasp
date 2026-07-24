[CmdletBinding(DefaultParameterSetName = 'Start')]
param(
    [Parameter(ParameterSetName = 'Start')]
    [switch] $Start,

    [Parameter(ParameterSetName = 'StartOnly')]
    [switch] $StartOnly,

    [Parameter(ParameterSetName = 'Status')]
    [switch] $Status,

    [switch] $SmokeTest,
    [switch] $RecoverOnly,
    [int] $PollSeconds = 30,
    [int] $StaleMinutes = 15,
    [int] $TimeoutMinutes = 120,
    [string] $RunnerPath = (Join-Path $env:USERPROFILE 'bin\news-grasp-runner.ps1'),
    [string] $StateFile = (Join-Path $env:USERPROFILE 'bin\news-grasp-runner-state.json'),
    [string] $LogDir = (Join-Path $env:USERPROFILE 'bin\news-grasp-logs'),
    [string] $DateStamp = (Get-Date -Format 'yyyy-MM-dd'),
    [string] $RepoDir = '',
    [string] $BinDir = ''
)

$ErrorActionPreference = 'Stop'
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Resolve-NewsGraspRepoDir {
    param([string] $Override)
    if ($Override) {
        return (Resolve-Path -LiteralPath $Override).Path
    }
    if ($env:NEWS_GRASP_REPO_DIR) {
        return (Resolve-Path -LiteralPath $env:NEWS_GRASP_REPO_DIR).Path
    }
    if ($PSScriptRoot) {
        $repoFromOps = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
        if (Test-Path -LiteralPath (Join-Path $repoFromOps 'tools\daily_self_heal.py')) {
            return $repoFromOps
        }
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

if (-not $BinDir) {
    $BinDir = Split-Path -Parent $RunnerPath
}
$RepoDir = Resolve-NewsGraspRepoDir -Override $RepoDir
$BootstrapLog = Join-Path $LogDir "bootstrap-$DateStamp.log"

function Get-LogPath {
    return Join-Path $LogDir "$DateStamp.log"
}

function Write-BootstrapLog {
    param([string] $Message)
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-ddTHH:mm:ss.fffK'), $Message
    Add-Content -LiteralPath $BootstrapLog -Value $line -Encoding UTF8
}

function Get-StringSha256Hex {
    param([string] $Text)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes([string]$Text)
        return (([System.BitConverter]::ToString($sha.ComputeHash($bytes))) -replace '-', '').ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
}

function Get-CommandLineFingerprint {
    param([string] $CommandLine)
    return Get-StringSha256Hex -Text ([string]$CommandLine).Trim().ToLowerInvariant()
}

function Get-FileSha256Hex {
    param([string] $Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return ''
    }
    try {
        if (Get-Command Get-FileHash -ErrorAction SilentlyContinue) {
            return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    } catch {
        Write-BootstrapLog "WARN: Get-FileHash failed path=$Path reason=$($_.Exception.Message)"
    }
    try {
        $stream = [System.IO.File]::OpenRead($Path)
        try {
            $sha = [System.Security.Cryptography.SHA256]::Create()
            try {
                $bytes = $sha.ComputeHash($stream)
                return ([System.BitConverter]::ToString($bytes) -replace '-', '').ToLowerInvariant()
            } finally {
                $sha.Dispose()
            }
        } finally {
            $stream.Dispose()
        }
    } catch {
        Write-BootstrapLog "ERROR: sha256 calculation failed path=$Path reason=$($_.Exception.Message)"
        return ''
    }
}

function Repair-LiveOpsFromRepo {
    $opsDir = Join-Path $RepoDir 'scripts\ops'
    if (-not (Test-Path -LiteralPath $opsDir)) {
        throw "repo ops directory not found: $opsDir"
    }
    New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
    $files = @(
        'news-grasp-bootstrap.ps1',
        'news-grasp-runner.ps1',
        'watch-news-grasp-runner.ps1',
        'news-grasp-deadman.ps1',
        'news-grasp-deadman-launcher.pyw'
    )
    $timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $backupDir = Join-Path $RepoDir "build\live-runner-self-repair\$timestamp"
    $manifestPath = Join-Path $backupDir 'auto-repair-manifest.json'
    $manifestFiles = @()
    $changed = $false

    foreach ($file in $files) {
        $source = Join-Path $opsDir $file
        $destination = Join-Path $BinDir $file
        if (-not (Test-Path -LiteralPath $source)) {
            throw "repo ops script missing: $source"
        }
        $sourceHash = Get-FileSha256Hex -Path $source
        $beforeHash = Get-FileSha256Hex -Path $destination
        $backup = Join-Path $backupDir $file
        $status = 'unchanged'
        if (-not $sourceHash) {
            throw "repo ops script hash unavailable: $source"
        }
        if ($sourceHash -ne $beforeHash) {
            New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
            if (Test-Path -LiteralPath $destination) {
                Copy-Item -LiteralPath $destination -Destination $backup -Force
            }
            Copy-Item -LiteralPath $source -Destination $destination -Force
            $changed = $true
            $status = 'repaired'
            Write-BootstrapLog "live ops repaired file=$file before=$beforeHash source=$sourceHash"
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
        Write-BootstrapLog "live ops self-repair manifest=$manifestPath"
    } else {
        Write-BootstrapLog 'live ops already in sync'
    }
}

function Read-State {
    if (-not (Test-Path $StateFile)) {
        return $null
    }
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        try {
            return Get-Content -LiteralPath $StateFile -Raw -Encoding UTF8 | ConvertFrom-Json
        } catch {
            if ($attempt -lt 3) {
                Start-Sleep -Milliseconds 100
            }
        }
    }
    $stamp = Get-Date -Format 'yyyyMMddHHmmss'
    $corrupt = "$StateFile.corrupt.$stamp.json"
    try { Copy-Item -LiteralPath $StateFile -Destination $corrupt -Force -ErrorAction SilentlyContinue } catch { }
    return [pscustomobject]@{ __corrupt = $true; corrupt_backup = $corrupt }
}

function Write-StateAtomic {
    param(
        [string] $Path,
        [object] $Payload
    )
    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    $tmp = "$Path.tmp.$PID.$([guid]::NewGuid().ToString('N'))"
    $backup = "$Path.bak"
    $json = ($Payload | ConvertTo-Json -Depth 8) + "`n"
    $bytes = [System.Text.UTF8Encoding]::new($false).GetBytes($json)
    $fs = [System.IO.File]::Open($tmp, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
    try {
        $fs.Write($bytes, 0, $bytes.Length)
        $fs.Flush($true)
    } finally {
        $fs.Dispose()
    }
    if (Test-Path -LiteralPath $Path) {
        [System.IO.File]::Replace($tmp, $Path, $backup, $true)
    } else {
        [System.IO.File]::Move($tmp, $Path)
    }
}

function Get-RunnerProcessAlive {
    param($State)
    if (-not $State -or -not $State.pid) {
        return $false
    }
    try {
        $null = Get-Process -Id ([int]$State.pid) -ErrorAction Stop
        return $true
    } catch {
        return $false
    }
}

function Get-StateTime {
    param(
        $State,
        [string] $Name
    )
    if (-not $State -or -not $State.$Name) { return $null }
    try { return [datetime]::Parse([string]$State.$Name) } catch { return $null }
}

function Get-StaleSeconds {
    param($State)
    $now = Get-Date
    $t = Get-StateTime -State $State -Name 'heartbeat_at'
    if (-not $t) { $t = Get-StateTime -State $State -Name 'updated_at' }
    if ($t) { return [int]($now - $t).TotalSeconds }
    $logPath = Get-LogPath
    if (Test-Path $logPath) {
        return [int]($now - (Get-Item -LiteralPath $logPath).LastWriteTime).TotalSeconds
    }
    return 0
}

function Write-WatchdogState {
    param(
        $State,
        [string] $Status,
        [string] $Message,
        [int] $ExitCode
    )
    $now = (Get-Date).ToString('yyyy-MM-ddTHH:mm:ss.fffK')
    $payload = [ordered]@{
        status = $Status
        message = $Message
        exit_code = $ExitCode
        updated_at = $now
        heartbeat_at = $now
        run_id = if ($State -and $State.run_id) { [string]$State.run_id } else { '' }
        pid = if ($State -and $State.pid) { [int]$State.pid } else { -1 }
        repo_dir = if ($State -and $State.repo_dir) { [string]$State.repo_dir } else { '' }
        runner_path = if ($State -and $State.runner_path) { [string]$State.runner_path } else { $RunnerPath }
        log_path = Get-LogPath
        last_observed_status = if ($State -and $State.status) { [string]$State.status } else { 'unknown' }
        last_observed_phase = if ($State -and $State.phase) { [string]$State.phase } else { '' }
        process_creation_time = if ($State -and $State.process_creation_time) { [string]$State.process_creation_time } else { '' }
        command_line_fingerprint = if ($State -and $State.command_line_fingerprint) { [string]$State.command_line_fingerprint } else { '' }
    }
    if ($State -and $State.corrupt_backup) {
        $payload.corrupt_backup = [string]$State.corrupt_backup
    }
    Write-StateAtomic -Path $StateFile -Payload $payload
}

function Test-RunnerProcessIdentity {
    param($State)
    if (-not $State -or -not $State.pid -or -not $State.run_id -or -not $State.runner_path -or -not $State.process_creation_time -or -not $State.command_line_fingerprint) {
        return $false
    }
    try {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$State.pid)" -ErrorAction Stop
    } catch {
        return $false
    }
    if (-not $proc -or -not $proc.CommandLine) { return $false }
    if ([string]$State.repo_dir -and -not ([string]$State.repo_dir).Equals((Split-Path -Parent (Split-Path -Parent $RunnerPath)), [System.StringComparison]::OrdinalIgnoreCase)) {
        # RepoDirOverride 実行では repo_dir 照合が厳密にできないため command line と runner path を優先する。
    }
    if ($proc.CommandLine -notlike "*$([string]$State.runner_path)*") { return $false }
    $fingerprint = Get-CommandLineFingerprint -CommandLine ([string]$proc.CommandLine)
    if ($fingerprint -ne [string]$State.command_line_fingerprint) { return $false }
    try {
        $expected = [datetime]::Parse([string]$State.process_creation_time)
        $actual = [datetime]$proc.CreationDate
        if ([math]::Abs(($actual - $expected).TotalSeconds) -gt 2) { return $false }
    } catch {
        return $false
    }
    return $true
}

function Stop-VerifiedRunner {
    param(
        $State,
        [string] $Status,
        [string] $Message
    )
    if (Test-RunnerProcessIdentity -State $State) {
        try { Stop-Process -Id ([int]$State.pid) -Force -ErrorAction Stop } catch { }
        Write-WatchdogState -State $State -Status $Status -Message $Message -ExitCode 124
        return 124
    }
    Write-WatchdogState -State $State -Status 'watchdog_stale_unconfirmed' -Message $Message -ExitCode 125
    return 125
}

function Write-StatusJson {
    param(
        [string] $Mode,
        [object] $State,
        [int] $ProcessId = -1,
        [string] $Message = ''
    )
    $logPath = Get-LogPath
    $logUpdatedAt = $null
    if (Test-Path $logPath) {
        $logUpdatedAt = (Get-Item -LiteralPath $logPath).LastWriteTime.ToString('yyyy-MM-ddTHH:mm:ss.fffK')
    }
    $alive = Get-RunnerProcessAlive -State $State
    $status = if ($State -and $State.status) { [string]$State.status } else { 'unknown' }
    $messageText = if ($Message) { $Message } elseif ($State -and $State.message) { [string]$State.message } else { '' }
    if ($status -eq 'running' -and -not $alive) {
        $status = 'stale'
        if (-not $Message) {
            $messageText = 'runner state is running but process is not alive'
        }
    }
    $out = [ordered]@{
        mode = $Mode
        status = $status
        message = $messageText
        pid = if ($ProcessId -ge 0) { $ProcessId } elseif ($State -and $State.pid) { [int]$State.pid } else { -1 }
        process_alive = $alive
        state_file = $StateFile
        log_path = $logPath
        log_updated_at = $logUpdatedAt
        updated_at = if ($State -and $State.updated_at) { [string]$State.updated_at } else { $null }
        run_id = if ($State -and $State.run_id) { [string]$State.run_id } else { $null }
        phase = if ($State -and $State.phase) { [string]$State.phase } else { $null }
        heartbeat_at = if ($State -and $State.heartbeat_at) { [string]$State.heartbeat_at } else { $null }
        deadline_at = if ($State -and $State.deadline_at) { [string]$State.deadline_at } else { $null }
        stale_seconds = Get-StaleSeconds -State $State
    }
    $out | ConvertTo-Json -Depth 4
}

function Write-StartedJson {
    param([System.Diagnostics.Process] $Process)
    $logPath = Get-LogPath
    $out = [ordered]@{
        mode = 'started'
        status = 'started'
        message = 'runner process started'
        pid = $Process.Id
        process_alive = -not $Process.HasExited
        state_file = $StateFile
        log_path = $logPath
        log_updated_at = if (Test-Path $logPath) { (Get-Item -LiteralPath $logPath).LastWriteTime.ToString('yyyy-MM-ddTHH:mm:ss.fffK') } else { $null }
        updated_at = (Get-Date).ToString('yyyy-MM-ddTHH:mm:ss.fffK')
    }
    $out | ConvertTo-Json -Depth 4
}

function Start-RunnerProcess {
    if (-not (Test-Path $RunnerPath)) {
        throw "runner not found: $RunnerPath"
    }
    $args = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $RunnerPath)
    if ($SmokeTest) {
        $args += '-SmokeTest'
    }
    if ($RecoverOnly) {
        $args += '-RecoverOnly'
    }
    $args += @('-DateStampOverride', $DateStamp)
    $args += @('-LogDirOverride', $LogDir)
    $args += @('-StateFileOverride', $StateFile)
    return Start-Process -FilePath 'powershell' -ArgumentList $args -WindowStyle Hidden -PassThru
}

function Test-TerminalState {
    param($State)
    if (-not $State -or -not $State.status) {
        return $false
    }
    return @('publish_complete', 'smoke_ok') -contains [string]$State.status
}

function Watch-Runner {
    param([System.Diagnostics.Process] $Process)
    $started = Get-Date
    $script:WatchExitCode = 1
    $watchRunId = ''
    while ($true) {
        Start-Sleep -Seconds $PollSeconds
        $state = Read-State
        if ($state -and $state.__corrupt) {
            Write-WatchdogState -State $state -Status 'watchdog_state_corrupt' -Message 'runner state json is corrupt' -ExitCode 125
            Write-StatusJson -Mode 'state_corrupt' -State (Read-State) -ProcessId $Process.Id -Message 'runner state json is corrupt'
            $script:WatchExitCode = 125
            return
        }
        if (-not $watchRunId -and $state -and $state.run_id) {
            $watchRunId = [string]$state.run_id
        }
        if (Test-TerminalState -State $state) {
            Write-StatusJson -Mode 'completed' -State $state -ProcessId $Process.Id
            $script:WatchExitCode = 0
            return
        }
        if ($state -and [string]$state.status -eq 'error' -and $Process.HasExited) {
            Write-StatusJson -Mode 'failed' -State $state -ProcessId $Process.Id
            $script:WatchExitCode = 1
            return
        }
        if ($Process.HasExited) {
            Write-StatusJson -Mode 'failed' -State $state -ProcessId $Process.Id -Message "runner process exited without publish_complete marker"
            $script:WatchExitCode = 1
            return
        }
        $staleSeconds = Get-StaleSeconds -State $state
        if ($staleSeconds -ge ($StaleMinutes * 60)) {
            $minutes = [int]($staleSeconds / 60)
            $message = "log has not changed for $minutes minutes"
            if ($watchRunId -and $state -and $state.run_id -and [string]$state.run_id -ne $watchRunId) {
                Write-WatchdogState -State $state -Status 'watchdog_stale_unconfirmed' -Message $message -ExitCode 125
                Write-StatusJson -Mode 'stale_unconfirmed' -State (Read-State) -ProcessId $Process.Id -Message $message
                $script:WatchExitCode = 125
                return
            }
            $script:WatchExitCode = Stop-VerifiedRunner -State $state -Status 'watchdog_stale_timeout' -Message $message
            Write-StatusJson -Mode 'stale' -State (Read-State) -ProcessId $Process.Id -Message $message
            return
        }
        $elapsed = (Get-Date) - $started
        if ($elapsed.TotalMinutes -ge $TimeoutMinutes) {
            $message = "watch timeout after $TimeoutMinutes minutes"
            $script:WatchExitCode = Stop-VerifiedRunner -State $state -Status 'watchdog_wall_timeout' -Message $message
            Write-StatusJson -Mode 'timeout' -State (Read-State) -ProcessId $Process.Id -Message $message
            return
        }
    }
}

if ($PSCmdlet.ParameterSetName -eq 'Status') {
    Write-StatusJson -Mode 'status' -State (Read-State)
    exit 0
}

Repair-LiveOpsFromRepo
$proc = Start-RunnerProcess
if ($PSCmdlet.ParameterSetName -eq 'StartOnly') {
    Write-StartedJson -Process $proc
    exit 0
}

Watch-Runner -Process $proc
exit $script:WatchExitCode
