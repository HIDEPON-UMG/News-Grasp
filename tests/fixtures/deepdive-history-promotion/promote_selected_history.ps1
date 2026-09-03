$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Get-RootPrefix {
    param([Parameter(Mandatory = $true)][string]$Root)

    return $Root.TrimEnd(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    ) + [IO.Path]::DirectorySeparatorChar
}

function Assert-ContainedPath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Label
    )

    if (-not $Path.StartsWith(
        (Get-RootPrefix -Root $Root),
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "$Label escaped fixed root: $Path"
    }
}

function Get-ExistingRegularFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Label
    )

    $resolved = (Resolve-Path -LiteralPath $Path).Path
    Assert-ContainedPath -Path $resolved -Root $Root -Label $Label
    $item = Get-Item -LiteralPath $resolved -Force
    if ($item.PSIsContainer) {
        throw "$Label is not a regular file: $resolved"
    }
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "$Label is a reparse point: $resolved"
    }
    return $resolved
}

function Get-ExistingDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Label,
        [switch]$AllowRoot
    )

    $resolved = (Resolve-Path -LiteralPath $Path).Path
    if (-not ($AllowRoot -and $resolved.Equals($Root, [StringComparison]::OrdinalIgnoreCase))) {
        Assert-ContainedPath -Path $resolved -Root $Root -Label $Label
    }
    $item = Get-Item -LiteralPath $resolved -Force
    if (-not $item.PSIsContainer) {
        throw "$Label is not a directory: $resolved"
    }
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "$Label is a reparse point: $resolved"
    }
    return $resolved
}

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path
$stageRoot = Get-ExistingDirectory `
    -Path (Join-Path $repoRoot "data\deepdive-history-remediation\stage") `
    -Root $repoRoot `
    -Label "stage root"
$days = @(
    "2026-08-18",
    "2026-08-19",
    "2026-08-20",
    "2026-08-21",
    "2026-08-22",
    "2026-08-23",
    "2026-08-24",
    "2026-08-26",
    "2026-08-30",
    "2026-08-31"
)
$relativeTemplates = @(
    "digest\DeepDive\{0}-DeepDive.md",
    "digest\DeepDive\{0}-DeepDive-dialogue.md",
    "data\deepdive-quality-review\{0}.json",
    "data\deepdive-provenance\{0}.json",
    "data\deepdive-claim-source\{0}.json",
    "data\deepdive-bundles\{0}.json",
    "docs\deepdive\{0}\index.html"
)
$summaryPath = Get-ExistingRegularFile `
    -Path (Join-Path $repoRoot "data\deepdive-history-remediation\2026-08-18-to-2026-08-31-stage-materialization.json") `
    -Root $repoRoot `
    -Label "stage materialization summary"
$summary = Get-Content -LiteralPath $summaryPath -Raw -Encoding utf8 | ConvertFrom-Json
if ($summary.status -ne "Green") {
    throw "stage materialization summary is not Green"
}

# 全70件のsource/targetとreparse境界を確定してから、backupを含むmutationへ進む。
$operations = [System.Collections.Generic.List[object]]::new()
foreach ($day in $days) {
    $stageDayRoot = Get-ExistingDirectory `
        -Path (Join-Path $stageRoot $day) `
        -Root $stageRoot `
        -Label "stage day"
    foreach ($template in $relativeTemplates) {
        $relative = $template -f $day
        $source = Get-ExistingRegularFile `
            -Path (Join-Path $stageDayRoot $relative) `
            -Root $stageDayRoot `
            -Label "stage source"
        $sourceParent = Get-ExistingDirectory `
            -Path (Split-Path -Parent $source) `
            -Root $stageDayRoot `
            -Label "stage source parent" `
            -AllowRoot

        $target = [IO.Path]::GetFullPath((Join-Path $repoRoot $relative))
        Assert-ContainedPath -Path $target -Root $repoRoot -Label "canonical target"
        $targetParent = Get-ExistingDirectory `
            -Path (Split-Path -Parent $target) `
            -Root $repoRoot `
            -Label "canonical target parent" `
            -AllowRoot
        $targetExisted = Test-Path -LiteralPath $target -PathType Leaf
        if ((Test-Path -LiteralPath $target) -and -not $targetExisted) {
            throw "canonical target is not a regular file: $target"
        }
        if ($targetExisted) {
            $targetItem = Get-Item -LiteralPath $target -Force
            if (($targetItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "canonical target is a reparse point: $target"
            }
        }
        $operations.Add([pscustomobject]@{
            day = $day
            relative = $relative
            source = $source
            sourceParent = $sourceParent
            target = $target
            targetParent = $targetParent
            targetExisted = $targetExisted
            backup = $null
            temporary = $null
        })
    }
}
if ($operations.Count -ne 70) {
    throw "unexpected bounded promotion file count: $($operations.Count)"
}

$backupRoot = Join-Path $repoRoot (
    "build\deepdive-history-remediation\backup-" + (Get-Date -Format "yyyyMMdd-HHmmssfff")
)
New-Item -ItemType Directory -Path $backupRoot | Out-Null
foreach ($operation in $operations) {
    if (-not $operation.targetExisted) {
        continue
    }
    $backup = Join-Path $backupRoot $operation.relative
    New-Item -ItemType Directory -Path (Split-Path -Parent $backup) -Force | Out-Null
    Copy-Item -LiteralPath $operation.target -Destination $backup
    $operation.backup = $backup
}

try {
    foreach ($operation in $operations) {
        $temporary = $operation.target + ".history-promote-" + [Guid]::NewGuid().ToString("N")
        Assert-ContainedPath -Path $temporary -Root $repoRoot -Label "promotion temporary"
        $operation.temporary = $temporary
        Copy-Item -LiteralPath $operation.source -Destination $temporary
        Move-Item -LiteralPath $temporary -Destination $operation.target -Force
        $operation.temporary = $null
    }
}
catch {
    $originalError = $_
    foreach ($operation in $operations) {
        if ($null -ne $operation.temporary -and (Test-Path -LiteralPath $operation.temporary)) {
            Remove-Item -LiteralPath $operation.temporary -Force
        }
    }
    foreach ($operation in $operations) {
        if ($operation.targetExisted) {
            $restoreTemporary = $operation.target + ".history-restore-" + [Guid]::NewGuid().ToString("N")
            Assert-ContainedPath -Path $restoreTemporary -Root $repoRoot -Label "restore temporary"
            try {
                Copy-Item -LiteralPath $operation.backup -Destination $restoreTemporary
                Move-Item -LiteralPath $restoreTemporary -Destination $operation.target -Force
            }
            finally {
                if (Test-Path -LiteralPath $restoreTemporary) {
                    Remove-Item -LiteralPath $restoreTemporary -Force
                }
            }
        }
        elseif (Test-Path -LiteralPath $operation.target) {
            Remove-Item -LiteralPath $operation.target -Force
        }
    }
    throw $originalError
}

[pscustomobject]@{
    schemaVersion = "DEEPDIVE_HISTORY_PROMOTION_V1"
    status = "promoted"
    days = $days
    fileCount = $operations.Count
    backupRoot = $backupRoot
} | ConvertTo-Json -Depth 4
