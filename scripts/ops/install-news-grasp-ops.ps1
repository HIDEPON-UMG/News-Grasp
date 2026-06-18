param(
    [string] $RepoDir = '',
    [string] $BinDir = (Join-Path $env:USERPROFILE 'bin')
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
Copy-Item -LiteralPath (Join-Path $ops 'news-grasp-runner.ps1') -Destination (Join-Path $BinDir 'news-grasp-runner.ps1') -Force
Copy-Item -LiteralPath (Join-Path $ops 'watch-news-grasp-runner.ps1') -Destination (Join-Path $BinDir 'watch-news-grasp-runner.ps1') -Force
Copy-Item -LiteralPath (Join-Path $ops 'news-grasp-deadman.ps1') -Destination (Join-Path $BinDir 'news-grasp-deadman.ps1') -Force
Copy-Item -LiteralPath (Join-Path $ops 'news-grasp-deadman-launcher.pyw') -Destination (Join-Path $BinDir 'news-grasp-deadman-launcher.pyw') -Force
Write-Host "News-Grasp ops scripts installed to $BinDir"
