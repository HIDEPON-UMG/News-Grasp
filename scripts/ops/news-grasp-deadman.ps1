param(
    [string] $RepoDir = '',
    [string] $StateFile = (Join-Path $env:USERPROFILE 'bin\news-grasp-runner-state.json'),
    [string] $AlertDir = (Join-Path $env:USERPROFILE 'bin\news-grasp-alerts'),
    [string] $WatcherPath = (Join-Path $env:USERPROFILE 'bin\watch-news-grasp-runner.ps1'),
    [string] $DateStamp = (Get-Date -Format 'yyyy-MM-dd'),
    [int] $MaxOkAgeHours = 27
)

$ErrorActionPreference = 'Stop'

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

$RepoDir = Resolve-NewsGraspRepoDir -Override $RepoDir
$PyExe = Join-Path $RepoDir '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $PyExe)) {
    $PyExe = 'python'
}

$alertLog = Join-Path $AlertDir 'deadman-alerts.jsonl'
$marker = Join-Path $AlertDir 'deadman-last-alert.json'
$recoverMarker = Join-Path $AlertDir 'deadman-last-recover.json'
$supervisorLog = Join-Path $AlertDir 'deadman-supervisor.log'

function Write-SupervisorLog {
    param([string] $Message)
    New-Item -ItemType Directory -Force -Path $AlertDir | Out-Null
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-ddTHH:mm:ss.fffK'), $Message
    Add-Content -LiteralPath $supervisorLog -Value $line -Encoding UTF8
}

function Test-PidAlive {
    param($PidValue)
    if (-not $PidValue) {
        return $false
    }
    try {
        $null = Get-Process -Id ([int]$PidValue) -ErrorAction Stop
        return $true
    } catch {
        return $false
    }
}

function Invoke-RecoverOnlyIfStaleDeadPid {
    if (-not (Test-Path -LiteralPath $StateFile)) {
        return
    }
    $state = Get-Content -LiteralPath $StateFile -Raw -Encoding UTF8 | ConvertFrom-Json
    if ([string]$state.status -ne 'running') {
        return
    }
    if (Test-PidAlive -PidValue $state.pid) {
        return
    }

    $key = "{0}|{1}|{2}" -f $state.date, $state.pid, $state.updated_at
    if (Test-Path -LiteralPath $recoverMarker) {
        $last = Get-Content -LiteralPath $recoverMarker -Raw -Encoding UTF8 | ConvertFrom-Json
        if ([string]$last.key -eq $key) {
            Write-SupervisorLog "recover skipped: duplicate stale pid key=$key"
            return
        }
    }
    if (-not (Test-Path -LiteralPath $WatcherPath)) {
        Write-SupervisorLog "recover failed: watcher not found: $WatcherPath"
        throw "watcher not found: $WatcherPath"
    }

    Write-SupervisorLog "recover start: stale running pid=$($state.pid) key=$key"
    & powershell.exe -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File $WatcherPath -StartOnly -RecoverOnly 2>&1 |
        ForEach-Object { Write-SupervisorLog "recover output: $_" }
    if ($LASTEXITCODE -ne 0) {
        Write-SupervisorLog "recover failed: exit=$LASTEXITCODE"
        throw "recover start failed: exit=$LASTEXITCODE"
    }
    [ordered]@{
        key = $key
        recovered_at = (Get-Date).ToString('yyyy-MM-ddTHH:mm:ss.fffK')
    } | ConvertTo-Json | Set-Content -LiteralPath $recoverMarker -Encoding UTF8
    Write-SupervisorLog "recover launched: key=$key"
}

Push-Location $RepoDir
try {
    & $PyExe '-m' 'tools.daily_self_heal' 'deadman' `
        '--state-file' $StateFile `
        '--date' $DateStamp `
        '--max-ok-age-hours' $MaxOkAgeHours `
        '--alert-log' $alertLog `
        '--marker' $marker
    $exitCode = $LASTEXITCODE
    if ($exitCode -eq 2) {
        Invoke-RecoverOnlyIfStaleDeadPid
        exit 0
    }
    exit $exitCode
} finally {
    Pop-Location
}
