Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot 'install-news-grasp-verified-file-boundary.ps1')

function Test-NewsGraspWindowsAbsolutePath {
    param([Parameter(Mandatory = $true)][string] $Path)
    return $Path -match '^(?:[A-Za-z]:[\\/]|\\\\)'
}

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

function Test-NewsGraspUnsafeTraversalReparsePoint {
    param([Parameter(Mandatory = $true)][object] $Item)
    if (($Item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -eq 0) {
        return $false
    }
    $linkType = [string]$Item.LinkType
    if ($linkType -in @('SymbolicLink', 'Junction')) {
        return $true
    }
    $targets = @($Item.Target | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) })
    return $targets.Count -gt 0 -and $linkType -ne 'HardLink'
}

function Assert-NewsGraspNoReparsePath {
    param(
        [Parameter(Mandatory = $true)][string] $Path,
        [Parameter(Mandatory = $true)][string] $Boundary
    )
    $cursor = Get-NewsGraspCanonicalPath -Path $Path
    $trustedBoundary = Get-NewsGraspCanonicalPath -Path $Boundary
    $trustedPrefix = $trustedBoundary + [System.IO.Path]::DirectorySeparatorChar
    if (
        -not (Test-NewsGraspSamePath -Left $cursor -Right $trustedBoundary) -and
        -not $cursor.StartsWith($trustedPrefix, [System.StringComparison]::OrdinalIgnoreCase)
    ) {
        throw 'NEWS_GRASP_INSTALL_JOURNAL_PATH_OUTSIDE_TRUSTED_ROOT'
    }
    while ($cursor) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if (Test-NewsGraspUnsafeTraversalReparsePoint -Item $item) {
                throw 'NEWS_GRASP_INSTALL_JOURNAL_REPARSE_POINT_FORBIDDEN'
            }
        }
        if (Test-NewsGraspSamePath -Left $cursor -Right $trustedBoundary) { break }
        $parent = Split-Path -Parent $cursor
        if (-not $parent -or (Test-NewsGraspSamePath -Left $parent -Right $cursor)) {
            throw 'NEWS_GRASP_INSTALL_JOURNAL_BOUNDARY_INVALID'
        }
        $cursor = $parent
    }
}

function Read-NewsGraspVerifiedFile {
    param(
        [Parameter(Mandatory = $true)][string] $Path,
        [Parameter(Mandatory = $true)][string] $TrustedBoundary,
        [int64] $MaxBytes = 0,
        [switch] $RequireSingleLink
    )
    Assert-NewsGraspNoReparsePath -Path $Path -Boundary $TrustedBoundary
    return [NewsGraspVerifiedFileBoundary]::ReadVerified($Path, [bool]$RequireSingleLink, $MaxBytes)
}

function Write-NewsGraspAtomicFile {
    param(
        [Parameter(Mandatory = $true)][string] $Path,
        [Parameter(Mandatory = $true)][string] $TrustedBoundary,
        [Parameter(Mandatory = $true)][byte[]] $Bytes
    )
    Assert-NewsGraspNoReparsePath -Path $Path -Boundary $TrustedBoundary
    return [NewsGraspVerifiedFileBoundary]::WriteAtomic($Path, $Bytes)
}

function Remove-NewsGraspVerifiedFile {
    param(
        [Parameter(Mandatory = $true)][string] $Path,
        [Parameter(Mandatory = $true)][string] $TrustedBoundary
    )
    $parent = Split-Path -Parent (Get-NewsGraspCanonicalPath -Path $Path)
    Assert-NewsGraspNoReparsePath -Path $parent -Boundary $TrustedBoundary
    [NewsGraspVerifiedFileBoundary]::DeleteVerified($Path)
}

function Restore-NewsGraspVerifiedFile {
    param(
        [Parameter(Mandatory = $true)][string] $BackupPath,
        [Parameter(Mandatory = $true)][string] $DestinationPath,
        [Parameter(Mandatory = $true)][string] $BackupBoundary,
        [Parameter(Mandatory = $true)][string] $DestinationBoundary
    )
    $backup = Read-NewsGraspVerifiedFile `
        -Path $BackupPath `
        -TrustedBoundary $BackupBoundary `
        -RequireSingleLink
    Write-NewsGraspAtomicFile `
        -Path $DestinationPath `
        -TrustedBoundary $DestinationBoundary `
        -Bytes $backup.Bytes | Out-Null
}

function Read-NewsGraspVerifiedTaskXml {
    param(
        [Parameter(Mandatory = $true)][string] $Path,
        [Parameter(Mandatory = $true)][string] $TrustedBoundary,
        [Parameter(Mandatory = $true)][string] $ExpectedSha256
    )
    if ($ExpectedSha256 -notmatch '^[A-Fa-f0-9]{64}$') {
        throw 'NEWS_GRASP_INSTALL_JOURNAL_TASK_XML_HASH_INVALID'
    }
    $xmlFile = Read-NewsGraspVerifiedFile `
        -Path $Path `
        -TrustedBoundary $TrustedBoundary `
        -RequireSingleLink
    if ([string]$xmlFile.Sha256 -ne $ExpectedSha256.ToLowerInvariant()) {
        throw 'NEWS_GRASP_INSTALL_JOURNAL_TASK_XML_DRIFT'
    }
    return [Text.Encoding]::Unicode.GetString($xmlFile.Bytes)
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

function ConvertTo-NewsGraspWindowsProcessArgument {
    param([Parameter(Mandatory = $true)][string] $Value)
    $builder = [System.Text.StringBuilder]::new()
    [void]$builder.Append('"')
    $backslashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq '\') {
            $backslashes += 1
            continue
        }
        if ($character -eq '"') {
            if ($backslashes -gt 0) {
                [void]$builder.Append(('\' * (($backslashes * 2) + 1)))
            }
            [void]$builder.Append('"')
            $backslashes = 0
            continue
        }
        if ($backslashes -gt 0) {
            [void]$builder.Append(('\' * $backslashes))
            $backslashes = 0
        }
        [void]$builder.Append($character)
    }
    if ($backslashes -gt 0) {
        [void]$builder.Append(('\' * ($backslashes * 2)))
    }
    [void]$builder.Append('"')
    return $builder.ToString()
}

function Get-NewsGraspTrackedWorkingHashes {
    param(
        [Parameter(Mandatory = $true)][string] $GitExe,
        [Parameter(Mandatory = $true)][string] $RepoDir,
        [Parameter(Mandatory = $true)][System.Collections.Generic.List[string]] $TrackedPaths,
        [Parameter(Mandatory = $true)][int] $MaxEntries
    )
    # Windows PowerShell 5.1 adds a UTF-8 BOM to redirected stdin even when
    # BaseStream is used.  Pass bounded path batches as argv instead, while
    # retaining Git's trusted system autocrlf normalization and blocking all
    # user/global attribute and replacement-object configuration.
    $normalizedPaths = [System.Collections.Generic.List[string]]::new()
    foreach ($trackedPath in $TrackedPaths) {
        $normalizedPaths.Add(([string]$trackedPath).TrimStart([char]0xFEFF))
        if ($normalizedPaths.Count -gt $MaxEntries) {
            throw 'NEWS_GRASP_INSTALL_SOURCE_HASH_PROCESS_INVALID'
        }
    }
    $allHashes = [System.Collections.Generic.List[string]]::new()
    for ($offset = 0; $offset -lt $normalizedPaths.Count; $offset += 128) {
        $end = [Math]::Min($offset + 127, $normalizedPaths.Count - 1)
        $batch = @($normalizedPaths[$offset..$end])
        $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
        $startInfo.FileName = $GitExe
        # PS7/.NETではArgumentListでargv境界を保持し、PS5.1では同じ
        # Windows command-line quotingへフォールバックする。
        $argumentValues = @('-C', $RepoDir, 'hash-object', '--no-filters', '--') + @($batch)
        $argumentListProperty = $startInfo.PSObject.Properties['ArgumentList']
        if ($null -ne $argumentListProperty) {
            foreach ($argument in $argumentValues) {
                $argumentListProperty.Value.Add([string]$argument)
            }
        } else {
            $startInfo.Arguments = (($argumentValues | ForEach-Object {
                ConvertTo-NewsGraspWindowsProcessArgument -Value ([string]$_)
            }) -join ' ')
        }
        $startInfo.UseShellExecute = $false
        $startInfo.CreateNoWindow = $true
        $startInfo.RedirectStandardOutput = $true
        $startInfo.RedirectStandardError = $true
        $process = [System.Diagnostics.Process]::new()
        $process.StartInfo = $startInfo
        try {
            if (-not $process.Start()) {
                throw 'NEWS_GRASP_INSTALL_SOURCE_HASH_PROCESS_INVALID'
            }
            $stdoutTask = $process.StandardOutput.ReadToEndAsync()
            $stderrTask = $process.StandardError.ReadToEndAsync()
            $process.WaitForExit()
            $stdout = $stdoutTask.Result
            $stderr = $stderrTask.Result
            if ($process.ExitCode -ne 0 -or $stderr -or [string]::IsNullOrWhiteSpace($stdout)) {
                throw 'NEWS_GRASP_INSTALL_SOURCE_HASH_PROCESS_INVALID'
            }
            $hashes = @($stdout -split "`r?`n" | Where-Object { $_ -ne '' })
            if ($hashes.Count -ne $batch.Count) {
                throw 'NEWS_GRASP_INSTALL_SOURCE_HASH_PROCESS_INVALID'
            }
            foreach ($hash in $hashes) {
                if ([string]$hash -notmatch '^[0-9a-f]{40}$') {
                    throw 'NEWS_GRASP_INSTALL_SOURCE_HASH_PROCESS_INVALID'
                }
                $allHashes.Add(([string]$hash).ToLowerInvariant())
            }
        } finally {
            $process.Dispose()
        }
    }
    return @($allHashes)
}

function Test-NewsGraspPromotableInstallSource {
    param(
        [Parameter(Mandatory = $true)][string] $CurrentRepoDir,
        [Parameter(Mandatory = $true)][string] $CandidateRepoDir,
        [Parameter(Mandatory = $true)][string] $TrustedBoundary,
        [int] $MaxEntries = 16384
    )
    $gitEnvironmentBackup = @{}
    try {
        $inheritedGitNames = @(
            [System.Environment]::GetEnvironmentVariables('Process').Keys |
                ForEach-Object { [string]$_ } |
                Where-Object { $_.StartsWith('GIT_', [System.StringComparison]::OrdinalIgnoreCase) }
        )
        foreach ($gitEnvironmentName in $inheritedGitNames) {
            $gitEnvironmentBackup[$gitEnvironmentName] = [System.Environment]::GetEnvironmentVariable(
                $gitEnvironmentName,
                'Process'
            )
            [System.Environment]::SetEnvironmentVariable($gitEnvironmentName, $null, 'Process')
        }
        $fixedGitEnvironment = @{
            'GIT_CONFIG_GLOBAL' = 'NUL'
            'GIT_CONFIG_NOSYSTEM' = '1'
            'GIT_ATTR_NOSYSTEM' = '1'
            'GIT_OPTIONAL_LOCKS' = '0'
            'GIT_NO_REPLACE_OBJECTS' = '1'
            'GIT_CONFIG_COUNT' = '3'
            'GIT_CONFIG_KEY_0' = 'core.fsmonitor'
            'GIT_CONFIG_VALUE_0' = 'false'
            'GIT_CONFIG_KEY_1' = 'core.hooksPath'
            'GIT_CONFIG_VALUE_1' = 'NUL'
            'GIT_CONFIG_KEY_2' = 'core.attributesFile'
            'GIT_CONFIG_VALUE_2' = 'NUL'
        }
        foreach ($fixedGitEnvironmentName in $fixedGitEnvironment.Keys) {
            [System.Environment]::SetEnvironmentVariable(
                $fixedGitEnvironmentName,
                [string]$fixedGitEnvironment[$fixedGitEnvironmentName],
                'Process'
            )
        }
        if ($MaxEntries -lt 1 -or $MaxEntries -gt 16384) { return $false }
        $gitExe = 'C:\Program Files\Git\cmd\git.exe'
        if (-not (Test-Path -LiteralPath $gitExe -PathType Leaf)) { return $false }
        Assert-NewsGraspNoReparsePath -Path $CurrentRepoDir -Boundary $TrustedBoundary
        Assert-NewsGraspNoReparsePath -Path $CandidateRepoDir -Boundary $TrustedBoundary
        $currentTopLevelRaw = ((& $gitExe -C $CurrentRepoDir rev-parse --show-toplevel 2>$null) | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or -not $currentTopLevelRaw) { return $false }
        $candidateTopLevelRaw = ((& $gitExe -C $CandidateRepoDir rev-parse --show-toplevel 2>$null) | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or -not $candidateTopLevelRaw) { return $false }
        if (
            -not (Test-NewsGraspSamePath -Left $CurrentRepoDir -Right $currentTopLevelRaw) -or
            -not (Test-NewsGraspSamePath -Left $CandidateRepoDir -Right $candidateTopLevelRaw)
        ) {
            return $false
        }

        $candidateBuildRoot = Join-Path $CandidateRepoDir 'build'
        if (Test-Path -LiteralPath $candidateBuildRoot) {
            $buildItem = Get-Item -LiteralPath $candidateBuildRoot -Force
            if (-not $buildItem.PSIsContainer -or (Test-NewsGraspUnsafeTraversalReparsePoint -Item $buildItem)) {
                return $false
            }
            Assert-NewsGraspNoReparsePath -Path $candidateBuildRoot -Boundary $CandidateRepoDir
            $pendingBuildDirectories = [System.Collections.Generic.Stack[string]]::new()
            $pendingBuildDirectories.Push($candidateBuildRoot)
            $buildInventoryCount = 0
            while ($pendingBuildDirectories.Count -gt 0) {
                $buildDirectory = $pendingBuildDirectories.Pop()
                Get-ChildItem -LiteralPath $buildDirectory -Force -ErrorAction Stop | ForEach-Object {
                    $child = $_
                    $buildInventoryCount += 1
                    if ($buildInventoryCount -gt $MaxEntries) {
                        throw 'NEWS_GRASP_INSTALL_SOURCE_INVENTORY_LIMIT_EXCEEDED'
                    }
                    if (Test-NewsGraspUnsafeTraversalReparsePoint -Item $child) {
                        throw 'NEWS_GRASP_INSTALL_SOURCE_REPARSE_POINT_FORBIDDEN'
                    }
                    if ($child.PSIsContainer) {
                        $null = $pendingBuildDirectories.Push([string]$child.FullName)
                    }
                }
            }
        }

        $currentCommonRaw = ((& $gitExe -C $CurrentRepoDir rev-parse --git-common-dir 2>$null) | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or -not $currentCommonRaw) { return $false }
        $candidateCommonRaw = ((& $gitExe -C $CandidateRepoDir rev-parse --git-common-dir 2>$null) | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or -not $candidateCommonRaw) { return $false }
        $currentCommon = if (Test-NewsGraspWindowsAbsolutePath -Path $currentCommonRaw) {
            Get-NewsGraspCanonicalPath -Path $currentCommonRaw
        } else {
            Get-NewsGraspCanonicalPath -Path (Join-Path $CurrentRepoDir $currentCommonRaw)
        }
        $candidateCommon = if (Test-NewsGraspWindowsAbsolutePath -Path $candidateCommonRaw) {
            Get-NewsGraspCanonicalPath -Path $candidateCommonRaw
        } else {
            Get-NewsGraspCanonicalPath -Path (Join-Path $CandidateRepoDir $candidateCommonRaw)
        }
        if (-not (Test-NewsGraspSamePath -Left $currentCommon -Right $candidateCommon)) {
            return $false
        }
        Assert-NewsGraspNoReparsePath -Path $currentCommon -Boundary $TrustedBoundary

        $candidateHead = ((& $gitExe -C $CandidateRepoDir rev-parse HEAD 2>$null) | Out-String).Trim().ToLowerInvariant()
        if ($LASTEXITCODE -ne 0 -or $candidateHead -notmatch '^[0-9a-f]{40}$') { return $false }
        $originMain = ((& $gitExe -C $CandidateRepoDir rev-parse refs/remotes/origin/main 2>$null) | Out-String).Trim().ToLowerInvariant()
        if ($LASTEXITCODE -ne 0 -or $originMain -ne $candidateHead) { return $false }

        & $gitExe -C $CandidateRepoDir diff --cached --quiet --no-ext-diff --no-textconv --
        if ($LASTEXITCODE -ne 0) { return $false }
        $trackedEntries = @(& $gitExe -C $CandidateRepoDir ls-files -v 2>$null)
        if (
            $LASTEXITCODE -ne 0 -or
            $trackedEntries.Count -eq 0 -or
            $trackedEntries.Count -gt $MaxEntries
        ) { return $false }
        foreach ($trackedEntry in $trackedEntries) {
            if (-not ([string]$trackedEntry).StartsWith('H ', [System.StringComparison]::Ordinal)) {
                return $false
            }
        }
        $stageEntries = @(& $gitExe -c core.quotepath=false -C $CandidateRepoDir ls-files --stage 2>$null)
        if (
            $LASTEXITCODE -ne 0 -or
            $stageEntries.Count -ne $trackedEntries.Count -or
            $stageEntries.Count -gt $MaxEntries
        ) { return $false }
        $trackedPaths = [System.Collections.Generic.List[string]]::new()
        $expectedHashes = [System.Collections.Generic.List[string]]::new()
        $trackedPathSet = [System.Collections.Generic.HashSet[string]]::new(
            [System.StringComparer]::OrdinalIgnoreCase
        )
        foreach ($stageEntry in $stageEntries) {
            $entryText = [string]$stageEntry
            if ($entryText -notmatch '^[0-9]{6} ([0-9a-f]{40}) 0\t(.+)$') {
                return $false
            }
            $expectedHash = ([string]$Matches[1]).ToLowerInvariant()
            $relativePath = ([string]$Matches[2]).TrimStart([char]0xFEFF)
            if (
                [System.IO.Path]::IsPathRooted($relativePath) -or
                $relativePath.StartsWith('"', [System.StringComparison]::Ordinal)
            ) { return $false }
            if (-not $trackedPathSet.Add($relativePath)) { return $false }
            $trackedPaths.Add($relativePath)
            $expectedHashes.Add($expectedHash)
        }
        for ($index = 0; $index -lt $trackedPaths.Count; $index += 1) {
            $trackedPaths[$index] = ([string]$trackedPaths[$index]).TrimStart([char]0xFEFF)
        }

        $candidateRoot = Get-NewsGraspCanonicalPath -Path $CandidateRepoDir
        $candidatePrefix = $candidateRoot + [System.IO.Path]::DirectorySeparatorChar
        $observedTrackedPaths = [System.Collections.Generic.HashSet[string]]::new(
            [System.StringComparer]::OrdinalIgnoreCase
        )
        $pendingDirectories = [System.Collections.Generic.Stack[string]]::new()
        $pendingDirectories.Push($candidateRoot)
        $inventoryCount = 0
        while ($pendingDirectories.Count -gt 0) {
            $directory = $pendingDirectories.Pop()
            Get-ChildItem -LiteralPath $directory -Force -ErrorAction Stop | ForEach-Object {
                $child = $_
                $inventoryCount += 1
                if ($inventoryCount -gt $MaxEntries) {
                    throw 'NEWS_GRASP_INSTALL_SOURCE_INVENTORY_LIMIT_EXCEEDED'
                }
                if (Test-NewsGraspUnsafeTraversalReparsePoint -Item $child) {
                    throw 'NEWS_GRASP_INSTALL_SOURCE_REPARSE_POINT_FORBIDDEN'
                }
                $childPath = Get-NewsGraspCanonicalPath -Path ([string]$child.FullName)
                if (-not $childPath.StartsWith($candidatePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
                    throw 'NEWS_GRASP_INSTALL_SOURCE_PATH_OUTSIDE_ROOT'
                }
                $relativePath = $childPath.Substring($candidatePrefix.Length).Replace('\', '/')
                if ($relativePath -eq '.git') { return }
                if ($child.PSIsContainer) {
                    $null = $pendingDirectories.Push($childPath)
                    return
                }
                if ($relativePath.StartsWith('build/', [System.StringComparison]::Ordinal)) {
                    if ($trackedPathSet.Contains($relativePath)) {
                        $null = $observedTrackedPaths.Add($relativePath)
                    }
                    return
                }
                if (-not $trackedPathSet.Contains($relativePath)) {
                    throw 'NEWS_GRASP_INSTALL_SOURCE_UNTRACKED_PAYLOAD_FORBIDDEN'
                }
                $null = $observedTrackedPaths.Add($relativePath)
            }
        }
        if ($observedTrackedPaths.Count -ne $trackedPathSet.Count) { return $false }

        try {
            $workingHashes = @(
                Get-NewsGraspTrackedWorkingHashes `
                    -GitExe $gitExe `
                    -RepoDir $CandidateRepoDir `
                    -TrackedPaths $trackedPaths `
                    -MaxEntries $MaxEntries
            )
        } catch {
            return $false
        }
        if ($workingHashes.Count -ne $expectedHashes.Count) {
            return $false
        }
        for ($index = 0; $index -lt $expectedHashes.Count; $index += 1) {
            if ([string]$workingHashes[$index] -ne [string]$expectedHashes[$index]) {
                return $false
            }
        }
        return $true
    } catch {
        return $false
    } finally {
        $activeGitNames = @(
            [System.Environment]::GetEnvironmentVariables('Process').Keys |
                ForEach-Object { [string]$_ } |
                Where-Object { $_.StartsWith('GIT_', [System.StringComparison]::OrdinalIgnoreCase) }
        )
        foreach ($activeGitName in $activeGitNames) {
            [System.Environment]::SetEnvironmentVariable($activeGitName, $null, 'Process')
        }
        foreach ($backupName in $gitEnvironmentBackup.Keys) {
            [System.Environment]::SetEnvironmentVariable(
                $backupName,
                [string]$gitEnvironmentBackup[$backupName],
                'Process'
            )
        }
    }
}

function Assert-NewsGraspCanonicalInstallSource {
    param(
        [Parameter(Mandatory = $true)][string] $ResolvedRepoDir,
        [Parameter(Mandatory = $true)][string] $RequestedBinDir,
        [Parameter(Mandatory = $true)][string] $CanonicalBinDir,
        [Parameter(Mandatory = $true)][string] $TrustedBoundary,
        [string] $ExpectedRuntimeRootSha256 = '',
        [string[]] $ManagedTaskNames = @(
            'News-Grasp Production',
            'News-Grasp Bootstrap',
            'News-Grasp Deadman',
            'News-Grasp Runner'
        )
    )
    $trustedRoot = Get-NewsGraspCanonicalPath -Path $TrustedBoundary
    $trustedPrefix = $trustedRoot + [System.IO.Path]::DirectorySeparatorChar
    foreach ($candidate in @($ResolvedRepoDir, $CanonicalBinDir)) {
        $canonicalCandidate = Get-NewsGraspCanonicalPath -Path $candidate
        if (
            -not (Test-NewsGraspSamePath -Left $canonicalCandidate -Right $trustedRoot) -and
            -not $canonicalCandidate.StartsWith($trustedPrefix, [System.StringComparison]::OrdinalIgnoreCase)
        ) {
            throw 'NEWS_GRASP_INSTALL_AUTHORITY_OUTSIDE_TRUSTED_BOUNDARY'
        }
    }
    Assert-NewsGraspNoReparsePath -Path $ResolvedRepoDir -Boundary $TrustedBoundary
    if (Test-Path -LiteralPath $CanonicalBinDir -PathType Container) {
        Assert-NewsGraspNoReparsePath -Path $CanonicalBinDir -Boundary $TrustedBoundary
    }
    if (-not (Test-NewsGraspSamePath -Left $RequestedBinDir -Right $CanonicalBinDir)) {
        throw 'NEWS_GRASP_INSTALL_BIN_AUTHORITY_MISMATCH'
    }

    $runtimeRootPath = Join-Path $CanonicalBinDir 'news-grasp-runtime-root-v1.json'
    $runtimeRootEntries = @()
    if (Test-Path -LiteralPath $CanonicalBinDir -PathType Container) {
        $runtimeRootEntries = @(
            Get-ChildItem -LiteralPath $CanonicalBinDir -Force |
                Where-Object { $_.Name -ieq 'news-grasp-runtime-root-v1.json' }
        )
    }
    if ($runtimeRootEntries.Count -eq 0) {
        if ($ExpectedRuntimeRootSha256 -and $ExpectedRuntimeRootSha256 -ne 'MISSING') {
            throw 'NEWS_GRASP_RUNTIME_ROOT_CONTRACT_DRIFT'
        }
        $managedFileNames = @(
            'run_codex_with_timeout.ps1',
            'news-grasp-bootstrap.ps1',
            'news-grasp-runner.ps1',
            'news-grasp-lineage.ps1',
            'watch-news-grasp-runner.ps1',
            'news-grasp-deadman.ps1',
            'news-grasp-deadman-launcher.pyw',
            'news-grasp-task-launcher.pyw'
        )
        $managedFileFound = $false
        if (Test-Path -LiteralPath $CanonicalBinDir -PathType Container) {
            $managedFileFound = @(
                Get-ChildItem -LiteralPath $CanonicalBinDir -Force |
                    Where-Object { $_.Name -in $managedFileNames }
            ).Count -gt 0
        }
        $managedTaskFound = $false
        foreach ($taskName in @($ManagedTaskNames)) {
            if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
                $managedTaskFound = $true
                break
            }
        }
        if ($managedFileFound -or $managedTaskFound) {
            throw 'NEWS_GRASP_RUNTIME_ROOT_REQUIRED_FOR_EXISTING_INSTALL'
        }
        return 'MISSING'
    }
    if (
        $runtimeRootEntries.Count -ne 1 -or
        $runtimeRootEntries[0].Name -cne 'news-grasp-runtime-root-v1.json' -or
        [bool]$runtimeRootEntries[0].PSIsContainer -or
        (Test-NewsGraspUnsafeTraversalReparsePoint -Item $runtimeRootEntries[0])
    ) {
        throw 'NEWS_GRASP_RUNTIME_ROOT_CONTRACT_INVALID'
    }

    try {
        $runtimeRootFile = Read-NewsGraspVerifiedFile `
            -Path $runtimeRootPath `
            -TrustedBoundary $CanonicalBinDir `
            -MaxBytes 65536 `
            -RequireSingleLink
        $runtimeRootSha256 = [string]$runtimeRootFile.Sha256
        $runtimeRoot = [Text.Encoding]::UTF8.GetString($runtimeRootFile.Bytes) | ConvertFrom-Json
    } catch {
        if ($_.Exception.Message -like 'NEWS_GRASP_*') { throw }
        throw 'NEWS_GRASP_RUNTIME_ROOT_CONTRACT_INVALID'
    }
    if ($ExpectedRuntimeRootSha256 -and $ExpectedRuntimeRootSha256 -ne $runtimeRootSha256) {
        throw 'NEWS_GRASP_RUNTIME_ROOT_CONTRACT_DRIFT'
    }
    Assert-NewsGraspExactKeys -Value $runtimeRoot -Expected @(
        'schemaVersion', 'repoDir', 'pythonExe', 'evidenceRepoDir'
    ) -Code 'NEWS_GRASP_RUNTIME_ROOT_CONTRACT_INVALID'
    if (
        [string]$runtimeRoot.schemaVersion -ne 'NEWS_GRASP_RUNTIME_ROOT_V1' -or
        -not [string]$runtimeRoot.repoDir -or
        -not [string]$runtimeRoot.pythonExe -or
        -not [string]$runtimeRoot.evidenceRepoDir -or
        -not [System.IO.Path]::IsPathRooted([string]$runtimeRoot.repoDir) -or
        -not [System.IO.Path]::IsPathRooted([string]$runtimeRoot.evidenceRepoDir)
    ) {
        throw 'NEWS_GRASP_RUNTIME_ROOT_CONTRACT_INVALID'
    }
    $promotable = Test-NewsGraspPromotableInstallSource `
        -CurrentRepoDir ([string]$runtimeRoot.evidenceRepoDir) `
        -CandidateRepoDir $ResolvedRepoDir `
        -TrustedBoundary $TrustedBoundary
    if (-not $promotable) {
        throw 'NEWS_GRASP_NONCANONICAL_INSTALL_SOURCE'
    }
    return $runtimeRootSha256
}

function Assert-NewsGraspInstallDestination {
    param(
        [Parameter(Mandatory = $true)][string] $DestinationPath,
        [Parameter(Mandatory = $true)][string] $CanonicalBinDir
    )
    $canonicalDestination = Get-NewsGraspCanonicalPath -Path $DestinationPath
    $canonicalBin = Get-NewsGraspCanonicalPath -Path $CanonicalBinDir
    if (-not (Test-NewsGraspSamePath -Left (Split-Path -Parent $canonicalDestination) -Right $canonicalBin)) {
        throw 'NEWS_GRASP_INSTALL_DESTINATION_INVALID'
    }
    $expectedName = Split-Path -Leaf $canonicalDestination
    if (Test-Path -LiteralPath $canonicalBin -PathType Container) {
        $entries = @(
            Get-ChildItem -LiteralPath $canonicalBin -Force |
                Where-Object { $_.Name -ieq $expectedName }
        )
        if (
            $entries.Count -gt 1 -or
            ($entries.Count -eq 1 -and (
                $entries[0].Name -cne $expectedName -or
                [bool]$entries[0].PSIsContainer -or
                (Test-NewsGraspUnsafeTraversalReparsePoint -Item $entries[0]) -or
                -not (Test-NewsGraspSamePath -Left $entries[0].FullName -Right $canonicalDestination)
            ))
        ) {
            throw 'NEWS_GRASP_INSTALL_DESTINATION_INVALID'
        }
    }
    Assert-NewsGraspNoReparsePath -Path (Split-Path -Parent $canonicalDestination) -Boundary $canonicalBin
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

    Assert-NewsGraspNoReparsePath -Path $ExpectedBackupRoot -Boundary $ExpectedBackupRoot
    Assert-NewsGraspNoReparsePath -Path $ExpectedBinDir -Boundary $ExpectedBinDir
    Assert-NewsGraspNoReparsePath -Path $JournalPath -Boundary $ExpectedBackupRoot
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
        'news-grasp-runtime-root-v1.json',
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
        Assert-NewsGraspNoReparsePath -Path ([string]$row.destination) -Boundary $ExpectedBinDir
        $beforeSha256 = [string]$row.before_sha256
        $sourceSha256 = [string]$row.source_sha256
        $afterSha256 = [string]$row.after_sha256
        foreach ($hashValue in @($beforeSha256, $sourceSha256, $afterSha256)) {
            if ($hashValue -and $hashValue -notmatch '^[A-Fa-f0-9]{64}$') {
                throw 'NEWS_GRASP_INSTALL_JOURNAL_HASH_INVALID'
            }
        }
        if ($fileName -eq 'audit-mission-authority-v1.json') {
            $missionSource = [string]$row.source
            if (
                $missionSource -notin @('broker:issue-news-grasp-audit-mission', 'existing:validated-audit-mission') -or
                $sourceSha256 -or
                ($missionSource -eq 'existing:validated-audit-mission' -and (
                    -not $beforeSha256 -or ($afterSha256 -and $afterSha256 -ne $beforeSha256)
                ))
            ) {
                throw 'NEWS_GRASP_INSTALL_JOURNAL_SOURCE_INVALID'
            }
        } elseif ($fileName -eq 'news-grasp-runtime-root-v1.json') {
            if ([string]$row.source -ne 'generated:runtime-root' -or $sourceSha256) {
                throw 'NEWS_GRASP_INSTALL_JOURNAL_SOURCE_INVALID'
            }
        } else {
            $expectedSource = Join-Path (Join-Path $ExpectedRepoDir 'scripts\ops') $fileName
            if (
                -not (Test-NewsGraspSamePath -Left ([string]$row.source) -Right $expectedSource) -or
                -not $sourceSha256 -or
                ($afterSha256 -and $afterSha256 -ne $sourceSha256)
            ) {
                throw 'NEWS_GRASP_INSTALL_JOURNAL_SOURCE_INVALID'
            }
            $sourceFile = Read-NewsGraspVerifiedFile `
                -Path $expectedSource `
                -TrustedBoundary $ExpectedRepoDir `
                -RequireSingleLink
            if ([string]$sourceFile.Sha256 -ne $sourceSha256) {
                throw 'NEWS_GRASP_INSTALL_JOURNAL_SOURCE_DRIFT'
            }
        }
        if ([string]$row.backup) {
            $expectedBackup = Join-Path $journalDir $fileName
            if (-not (Test-NewsGraspSamePath -Left ([string]$row.backup) -Right $expectedBackup)) {
                throw 'NEWS_GRASP_INSTALL_JOURNAL_BACKUP_INVALID'
            }
            Assert-NewsGraspNoReparsePath -Path ([string]$row.backup) -Boundary $journalDir
            if (-not $beforeSha256) {
                throw 'NEWS_GRASP_INSTALL_JOURNAL_BACKUP_INVALID'
            }
            $backupFile = Read-NewsGraspVerifiedFile `
                -Path ([string]$row.backup) `
                -TrustedBoundary $journalDir `
                -RequireSingleLink
            if ([string]$backupFile.Sha256 -ne $beforeSha256) {
                throw 'NEWS_GRASP_INSTALL_JOURNAL_BACKUP_DRIFT'
            }
        } elseif ($beforeSha256) {
            throw 'NEWS_GRASP_INSTALL_JOURNAL_BACKUP_INVALID'
        }

        $destinationExists = Test-Path -LiteralPath ([string]$row.destination) -PathType Leaf
        $expectedLiveSha256 = if ($afterSha256) { $afterSha256 } else { $beforeSha256 }
        if ($expectedLiveSha256) {
            if (-not $destinationExists) {
                throw 'NEWS_GRASP_INSTALL_JOURNAL_LIVE_STATE_DRIFT'
            }
            $liveFile = Read-NewsGraspVerifiedFile `
                -Path ([string]$row.destination) `
                -TrustedBoundary $ExpectedBinDir
            if ([string]$liveFile.Sha256 -ne $expectedLiveSha256) {
                throw 'NEWS_GRASP_INSTALL_JOURNAL_LIVE_STATE_DRIFT'
            }
        } elseif ($destinationExists) {
            throw 'NEWS_GRASP_INSTALL_JOURNAL_LIVE_STATE_DRIFT'
        }
    }

    if (
        $seenFiles.Count -ne $allowedFiles.Count -or
        @($allowedFiles | Where-Object { -not $seenFiles.ContainsKey($_) }).Count -ne 0
    ) {
        throw 'NEWS_GRASP_INSTALL_JOURNAL_FILE_SET_INVALID'
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
            'task_name', 'existed_before', 'enabled_before',
            'xml_backup', 'xml_backup_sha256'
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
            Assert-NewsGraspNoReparsePath -Path ([string]$snapshot.xml_backup) -Boundary $journalDir
            Read-NewsGraspVerifiedTaskXml `
                -Path ([string]$snapshot.xml_backup) `
                -TrustedBoundary $journalDir `
                -ExpectedSha256 ([string]$snapshot.xml_backup_sha256) | Out-Null
        } elseif ([string]$snapshot.xml_backup -or [string]$snapshot.xml_backup_sha256) {
            throw 'NEWS_GRASP_INSTALL_JOURNAL_TASK_XML_INVALID'
        }
    }
    return $true
}
