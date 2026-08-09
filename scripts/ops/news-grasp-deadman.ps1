param(
    [string] $RepoDir = '',
    [string] $StateFile = (Join-Path $env:USERPROFILE 'bin\news-grasp-runner-state.json'),
    [string] $AlertDir = (Join-Path $env:USERPROFILE 'bin\news-grasp-alerts'),
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
$supervisorLog = Join-Path $AlertDir 'deadman-supervisor.log'

function Write-SupervisorLog {
    param([string] $Message)
    New-Item -ItemType Directory -Force -Path $AlertDir | Out-Null
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-ddTHH:mm:ss.fffK'), $Message
    Add-Content -LiteralPath $supervisorLog -Value $line -Encoding UTF8
}

function Invoke-Audit0640Control {
    $terminalJson = (& $PyExe '-m' 'tools.news_grasp_daily_control' 'execute-audit-0640' '--issue-date' $DateStamp 2>&1 | Out-String).Trim()
    $executorExitCode = $LASTEXITCODE
    Write-SupervisorLog "audit canonical executor: exit=$executorExitCode terminal=$terminalJson"
    if ($executorExitCode -notin @(0, 2)) {
        return 2
    }
    return $executorExitCode
}

Push-Location $RepoDir
try {
    & $PyExe '-m' 'tools.daily_self_heal' 'deadman' `
        '--state-file' $StateFile `
        '--date' $DateStamp `
        '--max-ok-age-hours' $MaxOkAgeHours `
        '--alert-log' $alertLog `
        '--marker' $marker
    $deadmanExitCode = $LASTEXITCODE
    if ($deadmanExitCode -notin @(0, 2)) {
        Write-SupervisorLog "legacy deadman observer returned exit=$deadmanExitCode; audit controller remains authoritative"
    }
    exit (Invoke-Audit0640Control)
} finally {
    Pop-Location
}
