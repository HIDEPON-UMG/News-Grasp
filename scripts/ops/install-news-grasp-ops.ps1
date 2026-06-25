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

# backup + explicit approval + rollback: live runner overwrite must leave a restorable manifest.
$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$BackupDir = Join-Path $RepoDir "build\live-runner-backups\$timestamp"
$ManifestPath = Join-Path $BackupDir 'install-manifest.json'
New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null

$files = @(
    'news-grasp-runner.ps1',
    'watch-news-grasp-runner.ps1',
    'news-grasp-deadman.ps1',
    'news-grasp-deadman-launcher.pyw'
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
    "Copy-Item -LiteralPath `"$BackupDir\news-grasp-runner.ps1`" -Destination `"$BinDir\news-grasp-runner.ps1`" -Force",
    "Copy-Item -LiteralPath `"$BackupDir\watch-news-grasp-runner.ps1`" -Destination `"$BinDir\watch-news-grasp-runner.ps1`" -Force",
    "Copy-Item -LiteralPath `"$BackupDir\news-grasp-deadman.ps1`" -Destination `"$BinDir\news-grasp-deadman.ps1`" -Force",
    "Copy-Item -LiteralPath `"$BackupDir\news-grasp-deadman-launcher.pyw`" -Destination `"$BinDir\news-grasp-deadman-launcher.pyw`" -Force"
)

[ordered]@{
    created_at = (Get-Date).ToString('yyyy-MM-ddTHH:mm:ss.fffK')
    repo_dir = $RepoDir
    bin_dir = $BinDir
    backup_dir = $BackupDir
    files = $manifestFiles
    rollback_commands = $rollbackCommands
} | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ManifestPath -Encoding UTF8

Write-Host "News-Grasp ops scripts installed to $BinDir"
Write-Host "Backup manifest: $ManifestPath"
