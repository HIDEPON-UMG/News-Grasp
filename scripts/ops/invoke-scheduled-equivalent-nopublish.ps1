# 本番Dailyと同じ六operationを、cleanなpre-promotion candidateから作った
# 隔離worktreeで実行し、公開副作用0のまま最終確認する。
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string] $RepoRoot,
    [Parameter(Mandatory=$true)][string] $DateStamp,
    [Parameter(Mandatory=$true)][string] $StateFile,
    [Parameter(Mandatory=$true)][string] $LogDir,
    [Parameter(Mandatory=$true)][string] $ReceiptPath,
    [Parameter(Mandatory=$true)][string] $PythonExe,
    [Parameter(Mandatory=$true)][string] $WorkspaceRoot,
    [Parameter(Mandatory=$true)][string] $BudgetPath,
    [Parameter(Mandatory=$true)][string] $EfficiencyDesignPath,
    [Parameter(Mandatory=$true)][string] $AdversarialReviewPath,
    [Parameter(Mandatory=$true)][string] $RouteManifestPath,
    [Parameter(Mandatory=$true)][string] $StaticReceiptPath,
    [Parameter(Mandatory=$true)][string] $SimulationReceiptPath,
    [Parameter(Mandatory=$true)][string] $E2EAdmissionPath,
    [Parameter(Mandatory=$true)][string] $IsolationReceiptPath,
    [string] $HighCostBindingPath = '',
    [string] $HighCostBindingReceiptSha256 = '',
    [string] $E2EAttemptPolicyPath = '',
    [ValidateRange(1,2)][int] $E2ELogicalAttempt = 0,
    [string] $CausalReplacementProofPath = '',
    [string] $SupersessionApprovalPath = '',
    [string] $HighCostParentAuthorityPath = '',
    [string] $ExternalHealthAuthorityFixturePath = '',
    [string] $GlobalHarnessGenerationManifestPath = '',
    [string] $PowerShellExe = ''
)

$ErrorActionPreference = 'Stop'
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$null = Import-Module Microsoft.PowerShell.Utility -ErrorAction Stop
$gitEnvironmentRedirectKeys = @(
    [Environment]::GetEnvironmentVariables([EnvironmentVariableTarget]::Process).Keys |
        ForEach-Object { [string]$_ } |
        Where-Object { $_.StartsWith('GIT_', [StringComparison]::OrdinalIgnoreCase) }
)
foreach ($gitEnvironmentRedirectKey in $gitEnvironmentRedirectKeys) {
    [Environment]::SetEnvironmentVariable(
        $gitEnvironmentRedirectKey,
        $null,
        [EnvironmentVariableTarget]::Process
    )
}
$repoPath = [System.IO.Path]::GetFullPath((Resolve-Path -LiteralPath $RepoRoot -ErrorAction Stop).Path)
$statePath = [System.IO.Path]::GetFullPath($StateFile)
$logPath = [System.IO.Path]::GetFullPath($LogDir)
$receiptFullPath = [System.IO.Path]::GetFullPath($ReceiptPath)
$workspacePath = [System.IO.Path]::GetFullPath((Resolve-Path -LiteralPath $WorkspaceRoot -ErrorAction Stop).Path)
$e2eAttemptPolicyFullPath = ''
$e2eAttemptPolicySha256 = ''
$futurePrefix = $repoPath.TrimEnd('\') + '\'
foreach ($futureCandidate in @($statePath, $receiptFullPath)) {
    if (-not $futureCandidate.StartsWith($futurePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "HIGH_COST_CANONICAL_FUTURE_PATH_INVALID: $futureCandidate"
    }
    $futureCursor = Split-Path -Parent $futureCandidate
    while ($futureCursor -and $futureCursor.StartsWith($futurePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        if (Test-Path -LiteralPath $futureCursor) {
            $futureItem = Get-Item -LiteralPath $futureCursor -Force -ErrorAction Stop
            if (($futureItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "HIGH_COST_CANONICAL_FUTURE_PATH_INVALID: $futureCandidate"
            }
        }
        if ([string]::Equals($futureCursor, $repoPath, [System.StringComparison]::OrdinalIgnoreCase)) { break }
        $futureParent = Split-Path -Parent $futureCursor
        if (-not $futureParent -or $futureParent -eq $futureCursor) { break }
        $futureCursor = $futureParent
    }
}
if (-not $E2EAttemptPolicyPath -or $E2ELogicalAttempt -notin @(1,2)) {
    throw 'NEWS_GRASP_E2E_ATTEMPT_POLICY_REQUIRED'
}
$highCostOperationBudgetPath = Join-Path $workspacePath 'tools\harness\high_cost_operation_budget.py'
$highCostModelBrokerPath = Join-Path $env:USERPROFILE 'bin\ai-model-spawn-broker.py'
$operationKind = 'full_e2e'
$attemptId = "nopublish:$DateStamp"
function Get-CanonicalExistingFile {
    param(
        [Parameter(Mandatory=$true)][string] $Path,
        [Parameter(Mandatory=$true)][string] $Label,
        [string] $Boundary = '',
        [int64] $MaxBytes = 67108864
    )
    try {
        $lexical = [System.IO.Path]::GetFullPath($Path)
        $lexicalItem = Get-Item -LiteralPath $lexical -Force -ErrorAction Stop
        if (($lexicalItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
            ($Boundary -and -not [string]::IsNullOrWhiteSpace([string]$lexicalItem.LinkType))) {
            throw "$Label is a reparse point or hard link"
        }
        $resolved = [System.IO.Path]::GetFullPath((Resolve-Path -LiteralPath $Path -ErrorAction Stop).Path)
        if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
            throw "$Label is not a regular file"
        }
        $item = Get-Item -LiteralPath $resolved -Force -ErrorAction Stop
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "$Label contains a reparse point"
        }
        if ($Boundary) {
            $boundaryPath = [System.IO.Path]::GetFullPath((Resolve-Path -LiteralPath $Boundary -ErrorAction Stop).Path).TrimEnd('\')
            $boundaryPrefix = $boundaryPath + '\'
            if (-not [string]::Equals($resolved, $boundaryPath, [System.StringComparison]::OrdinalIgnoreCase) -and
                -not $resolved.StartsWith($boundaryPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
                throw "$Label is outside its canonical boundary"
            }
        }
        $cursor = $resolved
        while ($cursor) {
            $cursorItem = Get-Item -LiteralPath $cursor -Force -ErrorAction Stop
            if (($cursorItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "$Label traversal contains a reparse point"
            }
            if ($Boundary -and [string]::Equals($cursor, $boundaryPath, [System.StringComparison]::OrdinalIgnoreCase)) {
                break
            }
            $cursorParent = Split-Path -Parent $cursor
            if (-not $cursorParent -or $cursorParent -eq $cursor) { break }
            $cursor = $cursorParent
        }
        if ([int64]$item.Length -gt $MaxBytes) {
            throw "$Label exceeds bounded size"
        }
        return $resolved
    } catch {
        throw "HIGH_COST_CANONICAL_FILE_INVALID label=$Label path=$Path reason=$($_.Exception.Message)"
    }
}

function Get-CanonicalFuturePath {
    param(
        [Parameter(Mandatory=$true)][string] $Path,
        [string] $Suffix = '',
        [Parameter(Mandatory=$true)][string] $Boundary,
        [Parameter(Mandatory=$true)][string] $Label,
        [switch] $AllowReclaimedParent
    )
    try {
        $candidate = [System.IO.Path]::GetFullPath($Path)
        if ($Suffix -and -not $candidate.EndsWith($Suffix, [System.StringComparison]::Ordinal)) {
            throw "$Label suffix mismatch"
        }
        $boundaryPath = [System.IO.Path]::GetFullPath((Resolve-Path -LiteralPath $Boundary -ErrorAction Stop).Path).TrimEnd('\')
        $boundaryPrefix = $boundaryPath + '\'
        if (-not $candidate.StartsWith($boundaryPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "$Label is outside its canonical boundary"
        }
        $cursor = Split-Path -Parent $candidate
        while ($cursor) {
            if (Test-Path -LiteralPath $cursor) {
                $item = Get-Item -LiteralPath $cursor -Force -ErrorAction Stop
                if (($item.Attributes -band [System.IO.FileAttributes]::Directory) -eq 0) {
                    throw "$Label ancestor is not a directory"
                }
                if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                    throw "$Label ancestor is a reparse point"
                }
            }
            if ([string]::Equals($cursor, $boundaryPath, [System.StringComparison]::OrdinalIgnoreCase)) { break }
            $parent = Split-Path -Parent $cursor
            if (-not $parent -or $parent -eq $cursor) { throw "$Label boundary traversal failed" }
            $cursor = $parent
        }
        if (Test-Path -LiteralPath $candidate) {
            $allowExistingReclaimed = $AllowReclaimedParent -or ($Label -ceq 'parent authority')
            if (-not $allowExistingReclaimed) {
                throw "$Label output already exists"
            }
            $existingItem = Get-Item -LiteralPath $candidate -Force -ErrorAction Stop
            if (($existingItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
                -not [string]::IsNullOrWhiteSpace([string]$existingItem.LinkType)) {
                throw "$Label existing output is a reparse point or hard link"
            }
            $existing = Read-BoundedJsonFile -Path $candidate -MaxBytes 65536
            if ([string]$existing.schemaVersion -cne 'HIGH_COST_RECLAIMED_PARENT_AUTHORITY_V1' -or
                [string]$existing.state -cne 'reclaimed') {
                throw "$Label existing path is not a reclaimed parent marker"
            }
        }
        return $candidate
    } catch {
        throw "HIGH_COST_CANONICAL_FUTURE_PATH_INVALID label=$Label path=$Path reason=$($_.Exception.Message)"
    }
}

function Read-BoundedJsonFile {
    param(
        [Parameter(Mandatory=$true)][string] $Path,
        [int64] $MaxBytes = 65536
    )
    $stream = $null
    try {
        $stream = [System.IO.File]::Open(
            $Path,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Read,
            [System.IO.FileShare]::Read
        )
        if ($stream.Length -gt $MaxBytes) { throw 'bounded JSON exceeds maximum size' }
        $buffer = New-Object byte[] ([int]$stream.Length)
        $offset = 0
        while ($offset -lt $buffer.Length) {
            $read = $stream.Read($buffer, $offset, $buffer.Length - $offset)
            if ($read -le 0) { throw 'bounded JSON read truncated' }
            $offset += $read
        }
        return ([Text.Encoding]::UTF8.GetString($buffer) | ConvertFrom-Json -ErrorAction Stop)
    } finally {
        if ($stream) { $stream.Dispose() }
    }
}

function Get-StableFileBinding {
    param(
        [Parameter(Mandatory=$true)][string] $Path,
        [Parameter(Mandatory=$true)][string] $Label,
        [int64] $MaxBytes = 67108864
    )
    $stream = $null
    $contentHasher = $null
    $gitSha1Hasher = $null
    $gitSha256Hasher = $null
    try {
        $candidate = [System.IO.Path]::GetFullPath($Path)
        $before = Get-Item -LiteralPath $candidate -Force -ErrorAction Stop
        if (($before.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
            -not [string]::IsNullOrWhiteSpace([string]$before.LinkType) -or
            [int64]$before.Length -gt $MaxBytes) {
            throw "$Label stable input is invalid"
        }
        $stream = [System.IO.File]::Open(
            $candidate,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Read,
            [System.IO.FileShare]::Read
        )
        $opened = Get-Item -LiteralPath $candidate -Force -ErrorAction Stop
        $openedResolved = [System.IO.Path]::GetFullPath((Resolve-Path -LiteralPath $candidate -ErrorAction Stop).Path)
        if (($opened.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
            -not [string]::IsNullOrWhiteSpace([string]$opened.LinkType) -or
            -not [string]::Equals($openedResolved, $candidate, [StringComparison]::OrdinalIgnoreCase) -or
            [int64]$opened.Length -ne [int64]$stream.Length -or
            [int64]$opened.Length -ne [int64]$before.Length -or
            $opened.CreationTimeUtc.Ticks -ne $before.CreationTimeUtc.Ticks -or
            $opened.LastWriteTimeUtc.Ticks -ne $before.LastWriteTimeUtc.Ticks) {
            throw "$Label changed before stable open"
        }
        $contentHasher = [System.Security.Cryptography.SHA256]::Create()
        $gitSha1Hasher = [System.Security.Cryptography.SHA1]::Create()
        $gitSha256Hasher = [System.Security.Cryptography.SHA256]::Create()
        $header = [System.Text.Encoding]::ASCII.GetBytes("blob $($stream.Length)`0")
        $null = $gitSha1Hasher.TransformBlock($header, 0, $header.Length, $header, 0)
        $null = $gitSha256Hasher.TransformBlock($header, 0, $header.Length, $header, 0)
        $buffer = New-Object byte[] 65536
        while (($read = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
            $null = $contentHasher.TransformBlock($buffer, 0, $read, $buffer, 0)
            $null = $gitSha1Hasher.TransformBlock($buffer, 0, $read, $buffer, 0)
            $null = $gitSha256Hasher.TransformBlock($buffer, 0, $read, $buffer, 0)
        }
        $empty = New-Object byte[] 0
        $null = $contentHasher.TransformFinalBlock($empty, 0, 0)
        $null = $gitSha1Hasher.TransformFinalBlock($empty, 0, 0)
        $null = $gitSha256Hasher.TransformFinalBlock($empty, 0, 0)
        $after = Get-Item -LiteralPath $candidate -Force -ErrorAction Stop
        if (($after.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
            -not [string]::IsNullOrWhiteSpace([string]$after.LinkType) -or
            [int64]$after.Length -ne [int64]$opened.Length -or
            $after.CreationTimeUtc.Ticks -ne $opened.CreationTimeUtc.Ticks -or
            $after.LastWriteTimeUtc.Ticks -ne $opened.LastWriteTimeUtc.Ticks) {
            throw "$Label changed during stable read"
        }
        return [ordered]@{
            sha256 = ([System.BitConverter]::ToString($contentHasher.Hash) -replace '-', '').ToLowerInvariant()
            gitBlobSha1 = ([System.BitConverter]::ToString($gitSha1Hasher.Hash) -replace '-', '').ToLowerInvariant()
            gitBlobSha256 = ([System.BitConverter]::ToString($gitSha256Hasher.Hash) -replace '-', '').ToLowerInvariant()
        }
    } finally {
        if ($gitSha256Hasher) { $gitSha256Hasher.Dispose() }
        if ($gitSha1Hasher) { $gitSha1Hasher.Dispose() }
        if ($contentHasher) { $contentHasher.Dispose() }
        if ($stream) { $stream.Dispose() }
    }
}

function Assert-HeadBlobMatch {
    param(
        [Parameter(Mandatory=$true)][string] $GitExe,
        [Parameter(Mandatory=$true)][string] $RepoRoot,
        [Parameter(Mandatory=$true)][string] $Path,
        [Parameter(Mandatory=$true)][string] $RelativePath,
        [Parameter(Mandatory=$true)][string] $Label
    )
    $candidate = Get-CanonicalExistingFile -Path $Path -Label $Label -Boundary $RepoRoot -MaxBytes 67108864
    $tracked = (& $GitExe -C $RepoRoot ls-files --error-unmatch -- $RelativePath 2>$null | Out-String).Trim()
    $trackedExitCode = $LASTEXITCODE
    $headBlob = (& $GitExe -C $RepoRoot rev-parse "HEAD:$RelativePath" 2>$null).Trim().ToLowerInvariant()
    $headBlobExitCode = $LASTEXITCODE
    $stableBinding = Get-StableFileBinding -Path $candidate -Label $Label -MaxBytes 67108864
    $workingBlob = if ($headBlob.Length -eq 64) { $stableBinding.gitBlobSha256 } else { $stableBinding.gitBlobSha1 }
    if ($trackedExitCode -ne 0 -or -not $tracked -or
        $workingBlob -notmatch '^[0-9a-f]{40,64}$' -or
        $headBlobExitCode -ne 0 -or $headBlob -notmatch '^[0-9a-f]{40,64}$' -or
        $workingBlob -cne $headBlob) {
        throw "NEWS_GRASP_NOPUBLISH_RUNTIME_HEAD_BLOB_INVALID label=$Label path=$candidate"
    }
    return [ordered]@{
        path = $candidate
        relativePath = $RelativePath
        blob = $headBlob
        sha256 = $stableBinding.sha256
    }
}

function Get-CanonicalFutureDirectory {
    param(
        [Parameter(Mandatory=$true)][string] $Path,
        [Parameter(Mandatory=$true)][string] $Boundary,
        [Parameter(Mandatory=$true)][string] $Label,
        [switch] $AllowExisting
    )
    try {
        $candidate = [System.IO.Path]::GetFullPath($Path)
        $boundaryPath = [System.IO.Path]::GetFullPath((Resolve-Path -LiteralPath $Boundary -ErrorAction Stop).Path).TrimEnd('\')
        $boundaryPrefix = $boundaryPath + '\'
        if (-not $candidate.StartsWith($boundaryPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "$Label is outside its canonical boundary"
        }
        $cursor = Split-Path -Parent $candidate
        while ($cursor) {
            if (Test-Path -LiteralPath $cursor) {
                $item = Get-Item -LiteralPath $cursor -Force -ErrorAction Stop
                if (($item.Attributes -band [System.IO.FileAttributes]::Directory) -eq 0 -or
                    ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                    throw "$Label ancestor is invalid"
                }
            }
            if ([string]::Equals($cursor, $boundaryPath, [System.StringComparison]::OrdinalIgnoreCase)) { break }
            $parent = Split-Path -Parent $cursor
            if (-not $parent -or $parent -eq $cursor) { throw "$Label boundary traversal failed" }
            $cursor = $parent
        }
        if (Test-Path -LiteralPath $candidate) {
            $item = Get-Item -LiteralPath $candidate -Force -ErrorAction Stop
            if (($item.Attributes -band [System.IO.FileAttributes]::Directory) -eq 0 -or
                ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "$Label output is not a regular directory"
            }
            if (-not $AllowExisting) { throw "$Label output already exists" }
        } elseif ($AllowExisting) {
            throw "$Label output disappeared after creation"
        }
        return $candidate
    } catch {
        throw "HIGH_COST_CANONICAL_FUTURE_PATH_INVALID label=$Label path=$Path reason=$($_.Exception.Message)"
    }
}

$globalGenerationManifestPath = ''
$globalGenerationManifestSha256 = ''
$globalGenerationId = ''
$globalGenerationGoalId = ''
if ($GlobalHarnessGenerationManifestPath) {
    $globalGenerationManifestPath = Get-CanonicalExistingFile -Path $GlobalHarnessGenerationManifestPath -Label 'global generation manifest' -Boundary $repoPath -MaxBytes 65536
    $globalGenerationManifestSha256 = (Get-FileHash -LiteralPath $globalGenerationManifestPath -Algorithm SHA256).Hash.ToLowerInvariant()
    try {
        $globalManifest = Read-BoundedJsonFile -Path $globalGenerationManifestPath -MaxBytes 65536
        $requiredGlobalManifestFields = @('schemaVersion','generationId','ownerRepo','ownerCommit','sourceSnapshotPath','sourceSnapshotSha256','installedRuntimePath','installedRuntimeSha256','ownerAuthorityReceiptPath','ownerAuthorityReceiptSha256','validForGoalId')
        $observedGlobalManifestFields = @($globalManifest.PSObject.Properties.Name)
        if ($globalManifest.schemaVersion -cne 'NEWS_GRASP_GLOBAL_DEPENDENCY_GENERATION_MANIFEST_V1' -or
            (@($observedGlobalManifestFields | Sort-Object) -join '|') -cne (@($requiredGlobalManifestFields | Sort-Object) -join '|') -or
            [string]$globalManifest.generationId -eq '' -or
            [string]$globalManifest.validForGoalId -eq '' -or
            [string]$globalManifest.ownerCommit -notmatch '^[0-9a-f]{40,64}$') {
            throw 'manifest schema mismatch'
        }
        $globalGenerationId = [string]$globalManifest.generationId
        $globalGenerationGoalId = [string]$globalManifest.validForGoalId
    } catch {
        throw "NEWS_GRASP_GLOBAL_GENERATION_MANIFEST_INVALID: $($_.Exception.Message)"
    }
}

$e2eAttemptPolicyFullPath = Get-CanonicalExistingFile -Path $E2EAttemptPolicyPath -Label 'E2E attempt policy' -Boundary $repoPath -MaxBytes 65536
$e2eAttemptPolicySha256 = (Get-FileHash -LiteralPath $e2eAttemptPolicyFullPath -Algorithm SHA256).Hash.ToLowerInvariant()
try {
    $attemptPolicy = Read-BoundedJsonFile -Path $e2eAttemptPolicyFullPath -MaxBytes 65536
    $attemptPolicyFields = @($attemptPolicy.PSObject.Properties.Name)
    $expectedAttemptPolicyFields = @('schemaVersion','maxLogicalAttempts','maxFailureLocalResumes','logicalAttemptIssued','attemptA','attemptB','terminal','designFeedback','transition','transitionHistory','admissionBinding')
    if ($attemptPolicy.schemaVersion -cne 'NEWS_GRASP_E2E_ATTEMPT_POLICY_V1' -or
        (@($attemptPolicyFields | Sort-Object) -join '|') -cne (@($expectedAttemptPolicyFields | Sort-Object) -join '|') -or
        [int]$attemptPolicy.maxLogicalAttempts -ne 2 -or [int]$attemptPolicy.maxFailureLocalResumes -ne 1 -or
        [int]$attemptPolicy.logicalAttemptIssued -ne $E2ELogicalAttempt -or $null -ne $attemptPolicy.terminal -or
        $null -eq $attemptPolicy.transition -or [string]$attemptPolicy.transition.event -notin @('issue_a','failure_local_resume','issue_b')) {
        throw 'policy state mismatch'
    }
} catch {
    throw "NEWS_GRASP_E2E_ATTEMPT_POLICY_INVALID: $($_.Exception.Message)"
}

$parentAuthorityFullPath = "$receiptFullPath.high-cost-parent-authority.json"
if ($HighCostParentAuthorityPath -and
    -not [string]::Equals(
        [System.IO.Path]::GetFullPath($HighCostParentAuthorityPath),
        $parentAuthorityFullPath,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
    throw "HIGH_COST_PARENT_AUTHORITY_PATH_DRIFT: expected=$parentAuthorityFullPath actual=$HighCostParentAuthorityPath"
}
$supersessionArguments = @()
if ($SupersessionApprovalPath) {
    $supersessionArguments = @('--supersession-approval', $SupersessionApprovalPath)
}
$repoPrefix = $repoPath.TrimEnd('\') + '\'
foreach ($candidate in @($statePath, $logPath, $receiptFullPath, $parentAuthorityFullPath)) {
    if (-not $candidate.StartsWith($repoPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "HIGH_COST_CANONICAL_FUTURE_OUTPUT_INVALID: $candidate"
    }
}

$releaseNoPublishModule = 'tools.news_grasp_release_nopublish'
$releaseNoPublishModulePath = Join-Path $repoPath (($releaseNoPublishModule -replace '\.', '\') + '.py')
$runnerPath = Join-Path $repoPath 'scripts\ops\news-grasp-release-nopublish.ps1'
$codexWrapperPath = Join-Path $repoPath 'scripts\ops\run_codex_with_timeout.ps1'
$e2eAdmissionBridgePath = Join-Path $repoPath 'tools\e2e_final_admission_bridge.py'
$nopublishOwnerPath = Join-Path $repoPath 'tools\news_grasp_nopublish_owner.py'
$ownedProcessPath = Join-Path $repoPath 'tools\news_grasp_owned_process.py'
$p08EvidenceToolPath = Join-Path $repoPath 'tools\news_grasp_p08_evidence.py'
$wrapperEntryPath = [System.IO.Path]::GetFullPath($PSCommandPath)
if (-not (Test-Path -LiteralPath $runnerPath -PathType Leaf)) {
    throw "Release NoPublish adapter が見つかりません: $runnerPath"
}
if (-not (Test-Path -LiteralPath $releaseNoPublishModulePath -PathType Leaf)) {
    throw "Release NoPublish module が見つかりません: $releaseNoPublishModulePath"
}
if (-not (Test-Path -LiteralPath $codexWrapperPath -PathType Leaf)) {
    throw "installed Codex wrapper が見つかりません: $codexWrapperPath"
}
if (-not (Test-Path -LiteralPath $e2eAdmissionBridgePath -PathType Leaf)) {
    throw "E2E final admission consumer が見つかりません: $e2eAdmissionBridgePath"
}
if (-not (Test-Path -LiteralPath $nopublishOwnerPath -PathType Leaf)) {
    throw "NoPublish process owner が見つかりません: $nopublishOwnerPath"
}
if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw "Python 実行体が見つかりません: $PythonExe"
}
if (-not (Test-Path -LiteralPath $highCostOperationBudgetPath -PathType Leaf)) {
    throw "workspace-global high-cost operation budget consumer が見つかりません: $highCostOperationBudgetPath"
}
if (-not (Test-Path -LiteralPath $highCostModelBrokerPath -PathType Leaf)) {
    throw "installed canonical model broker が見つかりません: $highCostModelBrokerPath"
}
if (-not (Test-Path -LiteralPath (Join-Path $repoPath '.git'))) {
    throw "RepoRoot は git worktree でなければなりません: $repoPath"
}
try {
    # fixed local Pythonと署名を直接検証し、installed runtime設定をauthorityにしない。
    $expectedPythonPath = [System.IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'))
    $pythonCanonicalPath = Get-CanonicalExistingFile -Path $PythonExe -Label 'authority Python' -MaxBytes 67108864
    $pythonCanonicalSha256 = (Get-FileHash -LiteralPath $pythonCanonicalPath -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant()
    $pythonSignature = Get-AuthenticodeSignature -FilePath $pythonCanonicalPath
    if (-not [string]::Equals($pythonCanonicalPath, $expectedPythonPath, [StringComparison]::OrdinalIgnoreCase) -or
        $pythonSignature.Status -ne 'Valid' -or
        $pythonSignature.SignerCertificate.Subject -cne 'CN=Python Software Foundation, O=Python Software Foundation, L=Beaverton, S=Oregon, C=US' -or
        $pythonSignature.SignerCertificate.Thumbprint.ToLowerInvariant() -cne '36168ee17c1a240517388540c903bb6717dd2563') {
        throw 'authority Python identity invalid'
    }
    $gitCanonicalPath = Get-CanonicalExistingFile -Path 'C:\Program Files\Git\cmd\git.exe' -Label 'authority Git' -MaxBytes 67108864
    $expectedPowerShellPath = [System.IO.Path]::GetFullPath(
        [Environment]::ExpandEnvironmentVariables('%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe')
    )
    if ($PowerShellExe -and
        -not [string]::Equals([System.IO.Path]::GetFullPath($PowerShellExe), $expectedPowerShellPath, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'runner PowerShell path is not the fixed System32 executable'
    }
    $powerShellCanonicalPath = Get-CanonicalExistingFile -Path $expectedPowerShellPath -Label 'runner executable' -MaxBytes 67108864
    $powerShellSignature = Get-AuthenticodeSignature -FilePath $powerShellCanonicalPath
    if ($powerShellSignature.Status -ne 'Valid' -or
        $powerShellSignature.SignerCertificate.Subject -cne 'CN=Microsoft Windows, O=Microsoft Corporation, L=Redmond, S=Washington, C=US') {
        throw 'runner PowerShell signature invalid'
    }
    $wrapperEntryPath = Get-CanonicalExistingFile -Path $wrapperEntryPath -Label 'NoPublish wrapper' -Boundary $repoPath -MaxBytes 67108864
    $runnerPath = Get-CanonicalExistingFile -Path $runnerPath -Label 'Release NoPublish adapter' -Boundary $repoPath -MaxBytes 67108864
    $releaseNoPublishModulePath = Get-CanonicalExistingFile -Path $releaseNoPublishModulePath -Label 'Release NoPublish module' -Boundary $repoPath -MaxBytes 67108864
    $codexWrapperPath = Get-CanonicalExistingFile -Path $codexWrapperPath -Label 'Codex wrapper' -Boundary $repoPath -MaxBytes 67108864
    $e2eAdmissionBridgePath = Get-CanonicalExistingFile -Path $e2eAdmissionBridgePath -Label 'E2E admission bridge' -Boundary $repoPath -MaxBytes 67108864
    $nopublishOwnerPath = Get-CanonicalExistingFile -Path $nopublishOwnerPath -Label 'NoPublish process owner' -Boundary $repoPath -MaxBytes 67108864
    $ownedProcessPath = Get-CanonicalExistingFile -Path $ownedProcessPath -Label 'owned process helper' -Boundary $repoPath -MaxBytes 67108864
    $p08EvidenceToolPath = Get-CanonicalExistingFile -Path $p08EvidenceToolPath -Label 'P08 evidence validator' -Boundary $repoPath -MaxBytes 4194304
    $highCostOperationBudgetPath = Get-CanonicalExistingFile -Path $highCostOperationBudgetPath -Label 'high-cost operation budget' -Boundary $workspacePath -MaxBytes 67108864
    $highCostModelBrokerPath = Get-CanonicalExistingFile -Path $highCostModelBrokerPath -Label 'installed model broker' -MaxBytes 67108864
    if ((-not $HighCostBindingPath) -or (-not $HighCostBindingReceiptSha256)) { throw 'HIGH_COST_WORKSPACE_BINDING_MISSING' }
    $highCostBindingResolverPath = Join-Path $repoPath 'tools\news_grasp_high_cost_binding.py'
    $bindingJson = (& $pythonCanonicalPath '-I' '-S' '-B' $highCostBindingResolverPath 'resolve' '--binding' $HighCostBindingPath '--expected-receipt-sha256' $HighCostBindingReceiptSha256 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) { throw "HIGH_COST_WORKSPACE_BINDING_MISSING detail=$bindingJson" }
    $resolvedBinding = $bindingJson | ConvertFrom-Json -ErrorAction Stop
    if (
        [string]$resolvedBinding.bindingSchemaVersion -cne 'NEWS_GRASP_HIGH_COST_BINDING_V1' -or
        -not [string]::Equals([string]$resolvedBinding.workspaceRoot, $workspacePath, [StringComparison]::OrdinalIgnoreCase) -or
        -not [string]::Equals([string]$resolvedBinding.brokerInstalledPath, $highCostModelBrokerPath, [StringComparison]::OrdinalIgnoreCase)
    ) { throw 'HIGH_COST_IDENTITY_DRIFT' }
    $pythonSha256 = (Get-FileHash -LiteralPath $pythonCanonicalPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $powerShellSha256 = (Get-FileHash -LiteralPath $powerShellCanonicalPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $executionRepoCommit = (& $gitCanonicalPath -C $repoPath rev-parse HEAD 2>$null).Trim().ToLowerInvariant()
    if ($LASTEXITCODE -ne 0 -or $executionRepoCommit -notmatch '^[0-9a-f]{40}$') { throw 'execution commit unavailable' }
    $candidateRuntimeBindingSpecs = @(
        [ordered]@{ key='wrapper'; path=$wrapperEntryPath; relativePath='scripts/ops/invoke-scheduled-equivalent-nopublish.ps1'; label='NoPublish wrapper' },
        [ordered]@{ key='runner'; path=$runnerPath; relativePath='scripts/ops/news-grasp-release-nopublish.ps1'; label='Release NoPublish adapter' },
        [ordered]@{ key='codexWrapper'; path=$codexWrapperPath; relativePath='scripts/ops/run_codex_with_timeout.ps1'; label='Codex wrapper' },
        [ordered]@{ key='bridge'; path=$e2eAdmissionBridgePath; relativePath='tools/e2e_final_admission_bridge.py'; label='E2E admission bridge' },
        [ordered]@{ key='owner'; path=$nopublishOwnerPath; relativePath='tools/news_grasp_nopublish_owner.py'; label='NoPublish process owner' },
        [ordered]@{ key='ownedProcess'; path=$ownedProcessPath; relativePath='tools/news_grasp_owned_process.py'; label='owned process helper' },
        [ordered]@{ key='p08'; path=$p08EvidenceToolPath; relativePath='tools/news_grasp_p08_evidence.py'; label='P08 evidence validator' },
        [ordered]@{ key='releaseModule'; path=$releaseNoPublishModulePath; relativePath='tools/news_grasp_release_nopublish.py'; label='Release NoPublish module' }
    )
    $candidateRuntimeBindings = [ordered]@{}
    foreach ($bindingSpec in $candidateRuntimeBindingSpecs) {
        $candidateRuntimeBindings[$bindingSpec.key] = Assert-HeadBlobMatch `
            -GitExe $gitCanonicalPath `
            -RepoRoot $repoPath `
            -Path $bindingSpec.path `
            -RelativePath $bindingSpec.relativePath `
            -Label $bindingSpec.label
    }
} catch {
    throw "HIGH_COST_EXECUTABLE_IDENTITY_INVALID: $($_.Exception.Message)"
}

$BudgetPath = Get-CanonicalExistingFile -Path $BudgetPath -Label 'budget evidence' -Boundary $workspacePath -MaxBytes 4194304
$EfficiencyDesignPath = Get-CanonicalExistingFile -Path $EfficiencyDesignPath -Label 'efficiency evidence' -Boundary $workspacePath -MaxBytes 4194304
$AdversarialReviewPath = Get-CanonicalExistingFile -Path $AdversarialReviewPath -Label 'adversarial evidence' -Boundary $workspacePath -MaxBytes 4194304
$RouteManifestPath = Get-CanonicalExistingFile -Path $RouteManifestPath -Label 'route manifest evidence' -Boundary $workspacePath -MaxBytes 4194304
$StaticReceiptPath = Get-CanonicalExistingFile -Path $StaticReceiptPath -Label 'static evidence' -Boundary $workspacePath -MaxBytes 4194304
$SimulationReceiptPath = Get-CanonicalExistingFile -Path $SimulationReceiptPath -Label 'simulation evidence' -Boundary $workspacePath -MaxBytes 4194304
$IsolationReceiptPath = Get-CanonicalExistingFile -Path $IsolationReceiptPath -Label 'NoPublish isolation receipt' -Boundary $workspacePath -MaxBytes 4194304
try {
    $isolationReceipt = Read-BoundedJsonFile -Path $IsolationReceiptPath -MaxBytes 4194304
    $sourceRepoPath = [System.IO.Path]::GetFullPath((Resolve-Path -LiteralPath ([string]$isolationReceipt.sourceRepo) -ErrorAction Stop).Path)
    if ([string]::Equals($sourceRepoPath, $repoPath, [StringComparison]::OrdinalIgnoreCase)) { throw 'source and execution roots must differ' }
    $sourceRepoCommit = (& $gitCanonicalPath -C $sourceRepoPath rev-parse HEAD 2>$null).Trim().ToLowerInvariant()
    if ($LASTEXITCODE -ne 0 -or $sourceRepoCommit -notmatch '^[0-9a-f]{40}$') { throw 'source commit unavailable' }
    $sourceRepoTree = (& $gitCanonicalPath -C $sourceRepoPath rev-parse 'HEAD^{tree}' 2>$null).Trim().ToLowerInvariant()
    if ($LASTEXITCODE -ne 0 -or $sourceRepoTree -notmatch '^[0-9a-f]{40}$') { throw 'source tree unavailable' }
    $sourceRepoStatus = (& $gitCanonicalPath -C $sourceRepoPath status --porcelain=v1 --untracked-files=all 2>$null | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $sourceRepoStatus) { throw 'source candidate generation is dirty' }
    if ($executionRepoCommit -cne $sourceRepoCommit) { throw 'execution generation is not the isolated candidate generation' }
    $sourceRepoCommonDir = (& $gitCanonicalPath -C $sourceRepoPath rev-parse --path-format=absolute --git-common-dir 2>$null).Trim()
    $executionRepoCommonDir = (& $gitCanonicalPath -C $repoPath rev-parse --path-format=absolute --git-common-dir 2>$null).Trim()
    if ($LASTEXITCODE -ne 0 -or -not [string]::Equals($sourceRepoCommonDir, $executionRepoCommonDir, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'execution generation does not belong to candidate lineage'
    }
} catch {
    throw "NEWS_GRASP_NOPUBLISH_CANDIDATE_IDENTITY_INVALID: $($_.Exception.Message)"
}
$p08EvidenceToolSha256 = (Get-FileHash -LiteralPath $p08EvidenceToolPath -Algorithm SHA256).Hash.ToLowerInvariant()
$p08EvidenceToolBlob = (& $gitCanonicalPath -C $repoPath hash-object -- $p08EvidenceToolPath 2>$null).Trim().ToLowerInvariant()
$p08EvidenceToolBlobExitCode = $LASTEXITCODE
$p08EvidenceToolHeadBlob = (& $gitCanonicalPath -C $repoPath rev-parse 'HEAD:tools/news_grasp_p08_evidence.py' 2>$null).Trim().ToLowerInvariant()
$p08EvidenceToolHeadBlobExitCode = $LASTEXITCODE
if (
    $p08EvidenceToolBlobExitCode -ne 0 -or
    $p08EvidenceToolHeadBlobExitCode -ne 0 -or
    $p08EvidenceToolBlob -cne $p08EvidenceToolHeadBlob
) {
    throw 'NEWS_GRASP_NOPUBLISH_RUNTIME_VALIDATOR_BLOB_INVALID'
}
$isolationValidationJson = (& $pythonCanonicalPath '-I' '-S' '-B' $p08EvidenceToolPath 'validate-isolation' '--repo-root' $repoPath '--source-repo' $sourceRepoPath '--issue-date' $DateStamp '--isolation-receipt' $IsolationReceiptPath 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0) {
    throw "NEWS_GRASP_NOPUBLISH_ISOLATION_INVALID detail=$isolationValidationJson"
}
try {
    $isolationValidation = $isolationValidationJson | ConvertFrom-Json -ErrorAction Stop
} catch {
    throw "NEWS_GRASP_NOPUBLISH_ISOLATION_INVALID detail=$isolationValidationJson"
}
if (
    [string]$isolationValidation.validation.sourceHead -cne $sourceRepoCommit -or
    [string]$isolationValidation.validation.sourceTree -cne $sourceRepoTree -or
    -not [string]::Equals([System.IO.Path]::GetFullPath([string]$isolationValidation.validation.validatorPath), $p08EvidenceToolPath, [StringComparison]::OrdinalIgnoreCase) -or
    [string]$isolationValidation.validation.validatorSha256 -cne $p08EvidenceToolSha256
) {
    throw 'NEWS_GRASP_NOPUBLISH_RUNTIME_VALIDATOR_IDENTITY_INVALID'
}
$E2EAdmissionPath = Get-CanonicalExistingFile -Path $E2EAdmissionPath -Label 'issued E2E admission' -Boundary $repoPath -MaxBytes 65536
try {
    $issuedAdmission = Read-BoundedJsonFile -Path $E2EAdmissionPath -MaxBytes 65536
    $expectedLogicalAttemptKey = "News-Grasp:${DateStamp}:scheduled-equivalent-nopublish"
    if ($E2ELogicalAttempt -eq 2) {
        $expectedLogicalAttemptKey = "${expectedLogicalAttemptKey}:attempt-b"
    }
    if ([string]$issuedAdmission.attemptKey -cne $expectedLogicalAttemptKey) {
        throw "issued=$($issuedAdmission.attemptKey) expected=$expectedLogicalAttemptKey"
    }
} catch {
    throw "NEWS_GRASP_E2E_ATTEMPT_BINDING_INVALID: $($_.Exception.Message)"
}
$isolationBindings = @($issuedAdmission.evidenceBindings | Where-Object { [string]$_.kind -ceq 'isolation' })
$isolationReceiptSha256 = (Get-FileHash -LiteralPath $IsolationReceiptPath -Algorithm SHA256).Hash.ToLowerInvariant()
if (
    $isolationBindings.Count -ne 1 -or
    -not [string]::Equals(
        [System.IO.Path]::GetFullPath([string]$isolationBindings[0].path),
        [System.IO.Path]::GetFullPath($IsolationReceiptPath),
        [System.StringComparison]::OrdinalIgnoreCase
    ) -or
    [string]$isolationBindings[0].sha256 -cne $isolationReceiptSha256
) {
    throw 'NEWS_GRASP_E2E_ISOLATION_ADMISSION_BINDING_INVALID'
}
if (-not $attemptPolicy.admissionBinding -or
    [string]$attemptPolicy.admissionBinding.attemptKey -ne [string]$issuedAdmission.attemptKey -or
    [string]$attemptPolicy.admissionBinding.issueDate -ne [string]$issuedAdmission.issueDate -or
    [string]$attemptPolicy.admissionBinding.admissionId -ne [string]$issuedAdmission.admissionId -or
    [string]$attemptPolicy.admissionBinding.admissionPath -ne [System.IO.Path]::GetFullPath($E2EAdmissionPath) -or
    [string]$attemptPolicy.admissionBinding.admissionSha256 -ne (Get-FileHash -LiteralPath $E2EAdmissionPath -Algorithm SHA256).Hash.ToLowerInvariant()) {
    throw 'NEWS_GRASP_E2E_ATTEMPT_ADMISSION_BINDING_INVALID'
}
if (-not $ExternalHealthAuthorityFixturePath) {
    throw 'HIGH_COST_NOPUBLISH_FIXTURE_REQUIRED'
}
$ExternalHealthAuthorityFixturePath = Get-CanonicalExistingFile -Path $ExternalHealthAuthorityFixturePath -Label 'NoPublish external authority fixture' -Boundary $repoPath -MaxBytes 65536
$externalHealthAuthorityFixtureSha256 = (Get-FileHash -LiteralPath $ExternalHealthAuthorityFixturePath -Algorithm SHA256).Hash.ToLowerInvariant()
$authorizationMode = 'new_attempt'
$authorizationCommand = 'authorize'
$authorizationExtraArguments = @('--attempt-kind', $operationKind)
$consumeExtraArguments = @()
if ($CausalReplacementProofPath) {
    $CausalReplacementProofPath = Get-CanonicalExistingFile -Path $CausalReplacementProofPath -Label 'causal replacement proof' -Boundary $workspacePath -MaxBytes 2097152
    $authorizationMode = 'causal_replacement'
    $authorizationCommand = 'authorize-causal-replacement'
    $authorizationExtraArguments = @('--causal-replacement-proof', $CausalReplacementProofPath)
    $consumeExtraArguments = @('--causal-replacement-proof', $CausalReplacementProofPath)
} elseif ($SupersessionApprovalPath) {
    throw 'HIGH_COST_SUPERSESSION_REQUIRES_CAUSAL_REPLACEMENT'
}
if ($SupersessionApprovalPath) {
    $SupersessionApprovalPath = Get-CanonicalExistingFile -Path $SupersessionApprovalPath -Label 'pre-admission supersession approval' -Boundary $workspacePath -MaxBytes 4194304
    try {
        # Supersession approval is bound to this exact issued admission and issue date.
        # An approval for a prior date/generation must never authorize the successor.
        $supersessionApproval = Read-BoundedJsonFile -Path $SupersessionApprovalPath -MaxBytes 4194304
        $issuedAttemptKey = "News-Grasp:${DateStamp}:scheduled-equivalent-nopublish"
        if ($E2ELogicalAttempt -eq 2) {
            $issuedAttemptKey = "${issuedAttemptKey}:attempt-b"
        }
        $approvedAttemptKey = [string]$supersessionApproval.canonicalAttemptKey
        $approvedIssueDate = [string]$supersessionApproval.issueDate
        if ([string]::IsNullOrWhiteSpace($issuedAttemptKey) -or
            -not [string]::Equals($approvedAttemptKey, $issuedAttemptKey, [System.StringComparison]::Ordinal) -or
            -not [string]::Equals($approvedIssueDate, $DateStamp, [System.StringComparison]::Ordinal)) {
            throw "attemptKey/issueDate mismatch issued=$issuedAttemptKey approved=$approvedAttemptKey/$approvedIssueDate expectedDate=$DateStamp"
        }
    } catch {
        throw "HIGH_COST_SUPERSESSION_BINDING_INVALID: $($_.Exception.Message)"
    }
}
$statePath = Get-CanonicalFuturePath -Path $statePath -Boundary $repoPath -Label 'state file'
$logPath = Get-CanonicalFutureDirectory -Path $logPath -Boundary $repoPath -Label 'log directory'
$receiptFullPath = Get-CanonicalFuturePath -Path $receiptFullPath -Boundary $repoPath -Label 'final receipt'
$parentAuthorityFullPath = Get-CanonicalFuturePath -Path $parentAuthorityFullPath -Suffix '.high-cost-parent-authority.json' -Boundary $repoPath -Label 'parent authority' -AllowReclaimedParent:$true
$runnerArgumentsPath = Get-CanonicalFuturePath -Path "$receiptFullPath.runner-arguments.json" -Suffix '.runner-arguments.json' -Boundary $repoPath -Label 'runner arguments'
$reservationReceiptPath = Get-CanonicalFuturePath -Path "$receiptFullPath.e2e-final-reservation.json" -Suffix '.e2e-final-reservation.json' -Boundary $repoPath -Label 'reservation receipt'
$claimReceiptPath = Get-CanonicalFuturePath -Path "$receiptFullPath.e2e-final-claim.json" -Suffix '.e2e-final-claim.json' -Boundary $repoPath -Label 'claim receipt'
$claimWitnessPath = Get-CanonicalFuturePath -Path "$receiptFullPath.e2e-final-claim-witness.json" -Suffix '.e2e-final-claim-witness.json' -Boundary $repoPath -Label 'claim witness'
$launchEvidencePath = Get-CanonicalFuturePath -Path "$receiptFullPath.runner-launch-evidence.json" -Suffix '.runner-launch-evidence.json' -Boundary $repoPath -Label 'runner launch evidence'
if ($HighCostParentAuthorityPath -and
    -not [string]::Equals(
        [System.IO.Path]::GetFullPath($HighCostParentAuthorityPath),
        $parentAuthorityFullPath,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
    throw "HIGH_COST_PARENT_AUTHORITY_PATH_DRIFT: expected=$parentAuthorityFullPath actual=$HighCostParentAuthorityPath"
}
New-Item -ItemType Directory -Path (Split-Path -Parent $statePath) -Force | Out-Null
New-Item -ItemType Directory -Path $logPath -Force | Out-Null
New-Item -ItemType Directory -Path (Split-Path -Parent $receiptFullPath) -Force | Out-Null
$logPath = Get-CanonicalFutureDirectory -Path $logPath -Boundary $repoPath -Label 'log directory' -AllowExisting
$statePath = Get-CanonicalFuturePath -Path $statePath -Boundary $repoPath -Label 'state file'
$receiptFullPath = Get-CanonicalFuturePath -Path $receiptFullPath -Boundary $repoPath -Label 'final receipt'
$parentAuthorityFullPath = Get-CanonicalFuturePath -Path $parentAuthorityFullPath -Suffix '.high-cost-parent-authority.json' -Boundary $repoPath -Label 'parent authority'
$runnerArgumentsPath = Get-CanonicalFuturePath -Path $runnerArgumentsPath -Suffix '.runner-arguments.json' -Boundary $repoPath -Label 'runner arguments'
$reservationReceiptPath = Get-CanonicalFuturePath -Path $reservationReceiptPath -Suffix '.e2e-final-reservation.json' -Boundary $repoPath -Label 'reservation receipt'
$claimReceiptPath = Get-CanonicalFuturePath -Path $claimReceiptPath -Suffix '.e2e-final-claim.json' -Boundary $repoPath -Label 'claim receipt'
$claimWitnessPath = Get-CanonicalFuturePath -Path $claimWitnessPath -Suffix '.e2e-final-claim-witness.json' -Boundary $repoPath -Label 'claim witness'
$launchEvidencePath = Get-CanonicalFuturePath -Path $launchEvidencePath -Suffix '.runner-launch-evidence.json' -Boundary $repoPath -Label 'runner launch evidence'
$runnerArguments = @(
    '-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden', '-ExecutionPolicy', 'Bypass',
    '-File', $runnerPath,
    '-NoPublish',
    '-DateStampOverride', $DateStamp,
    '-RepoDirOverride', $repoPath,
    '-CodexWrapperOverride', $codexWrapperPath,
    '-StateFileOverride', $statePath,
    '-LogDirOverride', $logPath,
    '-PyExeOverride', $pythonCanonicalPath,
    '-PowerShellExe', $powerShellCanonicalPath,
    '-HighCostBindingPath', $HighCostBindingPath,
    '-HighCostBindingReceiptSha256', $HighCostBindingReceiptSha256,
    '-HighCostParentAuthorityPath', $parentAuthorityFullPath,
    '-E2EFinalAdmissionPath', $E2EAdmissionPath,
    '-E2EFinalRunnerArgumentsPath', $runnerArgumentsPath,
    '-E2EFinalReservationReceiptPath', $reservationReceiptPath,
    '-E2EFinalClaimReceiptPath', $claimReceiptPath,
    '-ExternalHealthAuthorityPathOverride', $ExternalHealthAuthorityFixturePath,
    '-ExternalHealthAuthorityExpectedSha256', $externalHealthAuthorityFixtureSha256,
    '-IsolationReceiptPath', $IsolationReceiptPath,
    '-LaunchEvidencePath', $launchEvidencePath,
    '-HighCostAttemptId', $attemptId
)
 $runnerArguments += @('-E2EAttemptPolicyPath', $e2eAttemptPolicyFullPath, '-E2ELogicalAttempt', [string]$E2ELogicalAttempt)
if ($globalGenerationManifestPath) {
    $runnerArguments += @('-GlobalHarnessGenerationManifestPath', $globalGenerationManifestPath)
}
if (Test-Path -LiteralPath $runnerArgumentsPath) {
    throw "HIGH_COST_RUNNER_ARGUMENTS_OUTPUT_EXISTS: $runnerArgumentsPath"
}
$runnerArgumentsJson = $runnerArguments | ConvertTo-Json -Compress -Depth 10
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$runnerArgumentsBytes = $utf8NoBom.GetBytes($runnerArgumentsJson + "`n")
$runnerArgumentsStream = $null
try {
    $runnerArgumentsStream = [System.IO.File]::Open(
        $runnerArgumentsPath,
        [System.IO.FileMode]::CreateNew,
        [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::None
    )
    $runnerArgumentsStream.Write($runnerArgumentsBytes, 0, $runnerArgumentsBytes.Length)
    $runnerArgumentsStream.Flush()
} finally {
    if ($runnerArgumentsStream) { $runnerArgumentsStream.Dispose() }
}
$e2eAdmissionValidation = & $pythonCanonicalPath -I $e2eAdmissionBridgePath 'validate-issued' `
    '--admission' $E2EAdmissionPath `
    '--runner-arguments-file' $runnerArgumentsPath `
    '--parent-authority' $parentAuthorityFullPath `
    '--reservation-output' $reservationReceiptPath `
    '--claim-output' $claimReceiptPath `
    '--claim-witness-output' $claimWitnessPath `
    '--runner-executable' $powerShellCanonicalPath `
    '--authority-python-executable' $pythonCanonicalPath `
    '--attempt-policy' $e2eAttemptPolicyFullPath `
    '--transition-receipt' (Join-Path (Split-Path -Parent $e2eAttemptPolicyFullPath) ("e2e-transition-" + [int]$attemptPolicy.transition.sequence + ".json"))
if ($LASTEXITCODE -ne 0) {
    throw "E2E_FINAL_ISSUED_ADMISSION_REJECTED exit=$LASTEXITCODE"
}
$authorizeOutput = & $pythonCanonicalPath -I $highCostOperationBudgetPath $authorizationCommand `
    '--workspace-root' $workspacePath `
    '--budget' $BudgetPath `
    '--efficiency-design' $EfficiencyDesignPath `
    '--adversarial-review' $AdversarialReviewPath `
    '--route-manifest' $RouteManifestPath `
    '--static-receipt' $StaticReceiptPath `
    '--simulation-receipt' $SimulationReceiptPath `
    '--e2e-admission' $E2EAdmissionPath `
    '--execution-root' $repoPath `
    '--output' $parentAuthorityFullPath @authorizationExtraArguments @supersessionArguments
if ($LASTEXITCODE -ne 0) {
    throw "HIGH_COST_OPERATION_AUTHORIZATION_REJECTED exit=$LASTEXITCODE"
}
$activateOutput = & $pythonCanonicalPath -I $highCostOperationBudgetPath 'activate' `
    '--workspace-root' $workspacePath `
    '--admission' $parentAuthorityFullPath
if ($LASTEXITCODE -ne 0) {
    throw "HIGH_COST_OPERATION_ACTIVATION_REJECTED exit=$LASTEXITCODE"
}
$validatedOutput = & $pythonCanonicalPath -I $highCostOperationBudgetPath 'validate-activated' `
    '--workspace-root' $workspacePath `
    '--admission' $parentAuthorityFullPath `
    '--expected-attempt-kind' $operationKind `
    '--expected-execution-root' $repoPath
if ($LASTEXITCODE -ne 0) {
    throw "HIGH_COST_OPERATION_VALIDATION_REJECTED exit=$LASTEXITCODE"
}
& $pythonCanonicalPath -I $e2eAdmissionBridgePath 'consume' `
    '--admission' $E2EAdmissionPath `
    '--runner-arguments-file' $runnerArgumentsPath `
    '--parent-authority' $parentAuthorityFullPath `
    '--reservation-output' $reservationReceiptPath `
    '--runner-executable' $powerShellCanonicalPath `
    '--authority-python-executable' $pythonCanonicalPath @consumeExtraArguments
if ($LASTEXITCODE -ne 0) {
    throw "E2E_FINAL_ADMISSION_REJECTED exit=$LASTEXITCODE"
}

$startedAt = Get-Date
$sourceRepoCommitBeforeLaunch = (& $gitCanonicalPath -C $sourceRepoPath rev-parse HEAD 2>$null).Trim().ToLowerInvariant()
$sourceRepoCommitBeforeLaunchExitCode = $LASTEXITCODE
$sourceRepoTreeBeforeLaunch = (& $gitCanonicalPath -C $sourceRepoPath rev-parse 'HEAD^{tree}' 2>$null).Trim().ToLowerInvariant()
$sourceRepoTreeBeforeLaunchExitCode = $LASTEXITCODE
$sourceRepoStatusBeforeLaunch = (& $gitCanonicalPath -C $sourceRepoPath status --porcelain=v1 --untracked-files=all 2>$null | Out-String).Trim()
$sourceRepoStatusBeforeLaunchExitCode = $LASTEXITCODE
$executionRepoCommitBeforeLaunch = (& $gitCanonicalPath -C $repoPath rev-parse HEAD 2>$null).Trim().ToLowerInvariant()
$executionRepoCommitBeforeLaunchExitCode = $LASTEXITCODE
$executionTrackedStatusBeforeLaunch = (& $gitCanonicalPath -C $repoPath status --porcelain=v1 --untracked-files=no 2>$null | Out-String).Trim()
$executionTrackedStatusBeforeLaunchExitCode = $LASTEXITCODE
$sourceRepoCommonDirBeforeLaunch = (& $gitCanonicalPath -C $sourceRepoPath rev-parse --path-format=absolute --git-common-dir 2>$null).Trim()
$sourceRepoCommonDirBeforeLaunchExitCode = $LASTEXITCODE
$executionRepoCommonDirBeforeLaunch = (& $gitCanonicalPath -C $repoPath rev-parse --path-format=absolute --git-common-dir 2>$null).Trim()
$executionRepoCommonDirBeforeLaunchExitCode = $LASTEXITCODE
$p08EvidenceToolSha256BeforeLaunch = (Get-FileHash -LiteralPath $p08EvidenceToolPath -Algorithm SHA256).Hash.ToLowerInvariant()
$p08EvidenceToolBlobBeforeLaunch = (& $gitCanonicalPath -C $repoPath hash-object -- $p08EvidenceToolPath 2>$null).Trim().ToLowerInvariant()
$p08EvidenceToolBlobBeforeLaunchExitCode = $LASTEXITCODE
$p08EvidenceToolHeadBlobBeforeLaunch = (& $gitCanonicalPath -C $repoPath rev-parse 'HEAD:tools/news_grasp_p08_evidence.py' 2>$null).Trim().ToLowerInvariant()
$p08EvidenceToolHeadBlobBeforeLaunchExitCode = $LASTEXITCODE
if (
    $sourceRepoCommitBeforeLaunchExitCode -ne 0 -or
    $sourceRepoTreeBeforeLaunchExitCode -ne 0 -or
    $sourceRepoStatusBeforeLaunchExitCode -ne 0 -or
    $executionRepoCommitBeforeLaunchExitCode -ne 0 -or
    $executionTrackedStatusBeforeLaunchExitCode -ne 0 -or
    $sourceRepoCommonDirBeforeLaunchExitCode -ne 0 -or
    $executionRepoCommonDirBeforeLaunchExitCode -ne 0 -or
    $p08EvidenceToolBlobBeforeLaunchExitCode -ne 0 -or
    $p08EvidenceToolHeadBlobBeforeLaunchExitCode -ne 0 -or
    $sourceRepoCommitBeforeLaunch -cne $sourceRepoCommit -or
    $sourceRepoTreeBeforeLaunch -cne $sourceRepoTree -or
    $sourceRepoStatusBeforeLaunch -or
    $executionRepoCommitBeforeLaunch -cne $executionRepoCommit -or
    $executionTrackedStatusBeforeLaunch -or
    -not [string]::Equals($sourceRepoCommonDirBeforeLaunch, $executionRepoCommonDirBeforeLaunch, [StringComparison]::OrdinalIgnoreCase) -or
    $p08EvidenceToolSha256BeforeLaunch -cne $p08EvidenceToolSha256 -or
    $p08EvidenceToolBlobBeforeLaunch -cne $p08EvidenceToolBlob -or
    $p08EvidenceToolHeadBlobBeforeLaunch -cne $p08EvidenceToolHeadBlob -or
    $p08EvidenceToolBlobBeforeLaunch -cne $p08EvidenceToolHeadBlobBeforeLaunch
) {
    throw 'NEWS_GRASP_NOPUBLISH_CANDIDATE_DRIFT_BEFORE_LAUNCH'
}
$launchPowerShellSha256 = (Get-FileHash -LiteralPath $powerShellCanonicalPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($launchPowerShellSha256 -ne $powerShellSha256) {
    throw "HIGH_COST_POWERSHELL_EXECUTABLE_DRIFT: $powerShellCanonicalPath"
}
foreach ($bindingSpec in $candidateRuntimeBindingSpecs) {
    $currentBinding = Assert-HeadBlobMatch `
        -GitExe $gitCanonicalPath `
        -RepoRoot $repoPath `
        -Path $bindingSpec.path `
        -RelativePath $bindingSpec.relativePath `
        -Label $bindingSpec.label
    $expectedBinding = $candidateRuntimeBindings[$bindingSpec.key]
    if ($currentBinding.blob -cne $expectedBinding.blob -or
        $currentBinding.sha256 -cne $expectedBinding.sha256) {
        throw "NEWS_GRASP_NOPUBLISH_RUNTIME_BINDING_DRIFT label=$($bindingSpec.label)"
    }
}
foreach ($pythonEnvironmentKey in @('PYTHONPATH','PYTHONHOME','PYTHONSTARTUP','PYTHONINSPECT','PYTHONUSERBASE')) {
    [Environment]::SetEnvironmentVariable($pythonEnvironmentKey, $null, [EnvironmentVariableTarget]::Process)
}
$env:PYTHONNOUSERSITE = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
& $pythonCanonicalPath '-I' '-S' '-B' $nopublishOwnerPath `
    '--repo-root' $repoPath `
    '--python-executable' $pythonCanonicalPath `
    '--powershell-executable' $powerShellCanonicalPath `
    '--runner-arguments' $runnerArgumentsPath `
    '--attempt-policy' $e2eAttemptPolicyFullPath `
    '--logical-attempt' ([string]$E2ELogicalAttempt) `
    '--admission' $E2EAdmissionPath `
    '--state' $statePath `
    '--claim' $claimReceiptPath `
    '--launch-evidence' $launchEvidencePath `
    '--expected-owner-sha256' ([string]$candidateRuntimeBindings.owner.sha256) `
    '--expected-bridge-sha256' ([string]$candidateRuntimeBindings.bridge.sha256) `
    '--expected-owned-process-sha256' ([string]$candidateRuntimeBindings.ownedProcess.sha256)
$runnerExitCode = $LASTEXITCODE
$ownerOutput = ''

if ($runnerExitCode -eq 0) {
    $runnerOutcomeReceiptPath = Join-Path (Split-Path -Parent $e2eAttemptPolicyFullPath) ("e2e-transition-" + ([int]$attemptPolicy.transition.sequence + 1) + ".json")
    $runnerTerminalAuthorityPath = Join-Path (Split-Path -Parent $e2eAttemptPolicyFullPath) ("e2e-terminal-authority-" + ([int]$E2ELogicalAttempt) + ".json")
    & $pythonCanonicalPath -I $e2eAdmissionBridgePath 'record-outcome' `
        '--admission' $E2EAdmissionPath `
        '--attempt-policy' $e2eAttemptPolicyFullPath `
        '--terminal-authority' $runnerTerminalAuthorityPath `
        '--outcome-receipt' $runnerOutcomeReceiptPath
    if ($LASTEXITCODE -ne 0) {
        throw "E2E_FINAL_RUNNER_OUTCOME_REJECTED exit=$LASTEXITCODE"
    }
}

$state = $null
if (Test-Path -LiteralPath $statePath -PathType Leaf) {
    try {
        $state = Read-BoundedJsonFile -Path $statePath -MaxBytes 65536
    } catch {
        $state = $null
    }
}
$observedStatus = if ($state) { [string] $state.status } else { '' }
$finishedAt = Get-Date
$elapsedSeconds = [int]($finishedAt - $startedAt).TotalSeconds
$durationSloLimitSeconds = 3600
$durationSloMet = $elapsedSeconds -le $durationSloLimitSeconds
$receipt = [ordered]@{
    schema = 'NEWS_GRASP_SCHEDULED_EQUIVALENT_NOPUBLISH_E2E_V1'
    scheduled_entrypoint_mode = 'product_local_candidate_owner'
    authorization_mode = $authorizationMode
    expected_terminal_state = 'publish_dry_run_ok'
    no_publish = $true
    no_push = $true
    no_auto_open = $true
    no_focus_theft = $true
    date = $DateStamp
    repo_root = $repoPath
    source_repo_root = $sourceRepoPath
    source_repo_commit = $sourceRepoCommit
    execution_repo_commit = $executionRepoCommit
    candidate_runtime_bindings = $candidateRuntimeBindings
    release_nopublish_adapter_path = $runnerPath
    nopublish_owner_path = $nopublishOwnerPath
    nopublish_owner_sha256 = (Get-FileHash -LiteralPath $nopublishOwnerPath -Algorithm SHA256).Hash.ToLowerInvariant()
    e2e_admission_path = [System.IO.Path]::GetFullPath($E2EAdmissionPath)
    e2e_admission_sha256 = (Get-FileHash -LiteralPath $E2EAdmissionPath -Algorithm SHA256).Hash.ToLowerInvariant()
    e2e_attempt_policy_path = $e2eAttemptPolicyFullPath
    e2e_attempt_policy_sha256 = $e2eAttemptPolicySha256
    e2e_logical_attempt = $E2ELogicalAttempt
    state_file = $statePath
    log_dir = $logPath
    started_at = $startedAt.ToString('o')
    finished_at = $finishedAt.ToString('o')
    elapsed_seconds = $elapsedSeconds
    duration_slo_limit_seconds = $durationSloLimitSeconds
    duration_slo_met = $durationSloMet
    runner_exit_code = $runnerExitCode
    observed_terminal_state = $observedStatus
    high_cost_attempt_id = $attemptId
    high_cost_parent_authority_path = $parentAuthorityFullPath
    external_health_authority_fixture_path = $ExternalHealthAuthorityFixturePath
    external_health_authority_fixture_sha256 = $externalHealthAuthorityFixtureSha256
    high_cost_parent_authority_sha256 = if (Test-Path -LiteralPath $parentAuthorityFullPath -PathType Leaf) { (Get-FileHash -LiteralPath $parentAuthorityFullPath -Algorithm SHA256).Hash.ToLowerInvariant() } else { '' }
    runner_output = $ownerOutput
    ok = ($runnerExitCode -eq 0 -and $observedStatus -eq 'publish_dry_run_ok' -and $durationSloMet)
}
$json = $receipt | ConvertTo-Json -Depth 6
$receiptDirectory = Split-Path -Parent $receiptFullPath
$receiptTemporaryPath = Join-Path $receiptDirectory ('.' + [System.IO.Path]::GetFileName($receiptFullPath) + '.' + [Guid]::NewGuid().ToString('N') + '.tmp')
$receiptBytes = $utf8NoBom.GetBytes($json + [Environment]::NewLine)
$receiptStream = $null
try {
    $receiptStream = [System.IO.FileStream]::new(
        $receiptTemporaryPath,
        [System.IO.FileMode]::CreateNew,
        [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::None,
        4096,
        [System.IO.FileOptions]::WriteThrough
    )
    $receiptStream.Write($receiptBytes, 0, $receiptBytes.Length)
    $receiptStream.Flush($true)
} finally {
    if ($null -ne $receiptStream) {
        $receiptStream.Dispose()
    }
}
try {
    [System.IO.File]::Move($receiptTemporaryPath, $receiptFullPath)
} catch {
    if (Test-Path -LiteralPath $receiptTemporaryPath -PathType Leaf) {
        Remove-Item -LiteralPath $receiptTemporaryPath -Force
    }
    throw
}

if (-not $receipt.ok) {
    Write-Error "scheduled-equivalent NoPublish E2E failed: exit=$runnerExitCode state=$observedStatus receipt=$receiptFullPath"
    exit 1
}
Write-Output $json
exit 0
