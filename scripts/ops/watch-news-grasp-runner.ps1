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
    [string] $LogDir = (Join-Path $env:USERPROFILE 'bin\news-grasp-logs')
)

$ErrorActionPreference = 'Stop'
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Get-LogPath {
    $date = Get-Date -Format 'yyyy-MM-dd'
    return Join-Path $LogDir "$date.log"
}

function Read-State {
    if (-not (Test-Path $StateFile)) {
        return $null
    }
    try {
        return Get-Content -LiteralPath $StateFile -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        return $null
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
    return Start-Process -FilePath 'powershell' -ArgumentList $args -WindowStyle Hidden -PassThru
}

function Test-TerminalState {
    param($State)
    if (-not $State -or -not $State.status) {
        return $false
    }
    return @('ok', 'smoke_ok') -contains [string]$State.status
}

function Watch-Runner {
    param([System.Diagnostics.Process] $Process)
    $started = Get-Date
    $script:WatchExitCode = 1
    while ($true) {
        Start-Sleep -Seconds $PollSeconds
        $state = Read-State
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
            Write-StatusJson -Mode 'failed' -State $state -ProcessId $Process.Id -Message "runner process exited without ok marker"
            $script:WatchExitCode = 1
            return
        }
        $logPath = Get-LogPath
        if (Test-Path $logPath) {
            $age = (Get-Date) - (Get-Item -LiteralPath $logPath).LastWriteTime
            if ($age.TotalMinutes -ge $StaleMinutes) {
                Write-StatusJson -Mode 'stale' -State $state -ProcessId $Process.Id -Message "log has not changed for $([int]$age.TotalMinutes) minutes"
                $script:WatchExitCode = 124
                return
            }
        }
        $elapsed = (Get-Date) - $started
        if ($elapsed.TotalMinutes -ge $TimeoutMinutes) {
            Write-StatusJson -Mode 'timeout' -State $state -ProcessId $Process.Id -Message "watch timeout after $TimeoutMinutes minutes"
            $script:WatchExitCode = 124
            return
        }
    }
}

if ($PSCmdlet.ParameterSetName -eq 'Status') {
    Write-StatusJson -Mode 'status' -State (Read-State)
    exit 0
}

$proc = Start-RunnerProcess
if ($PSCmdlet.ParameterSetName -eq 'StartOnly') {
    Write-StartedJson -Process $proc
    exit 0
}

Watch-Runner -Process $proc
exit $script:WatchExitCode
