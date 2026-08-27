param(
    [string] $RepoDir = '',
    [string] $PythonExe = '',
    [string] $EvidenceRepoDir = '',
    [string] $StateFile = (Join-Path $env:USERPROFILE 'bin\news-grasp-runner-state.json'),
    [string] $AlertDir = (Join-Path $env:USERPROFILE 'bin\news-grasp-alerts'),
    [string] $DateStamp = (Get-Date -Format 'yyyy-MM-dd'),
    [int] $MaxOkAgeHours = 27
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

$RepoDir = Resolve-NewsGraspRepoDir -Override $RepoDir
if ($EvidenceRepoDir) { $env:NEWS_GRASP_EVIDENCE_REPO_DIR = (Resolve-Path -LiteralPath $EvidenceRepoDir).Path }
function Get-CanonicalRecoveryControlBinding {
    $profileRoot = [Environment]::GetFolderPath([Environment+SpecialFolder]::UserProfile)
    $canonicalPython = Join-Path $profileRoot 'AppData\Local\Programs\Python\Python312\python.exe'
    $canonicalRuntime = Join-Path $profileRoot '.news-grasp-runtime\production-runtime'
    $trustedRemote = 'https://github.com/HIDEPON-UMG/News-Grasp.git'
    $gitExe = 'C:\Program Files\Git\cmd\git.exe'
    $env:GIT_TERMINAL_PROMPT = '0'
    $gitSafeArgs = @(
        '-c', 'core.hooksPath=NUL',
        '-c', 'core.fsmonitor=false',
        '-c', 'core.attributesFile=NUL',
        '-c', 'http.lowSpeedLimit=1',
        '-c', 'http.lowSpeedTime=15'
    )
    $bindingPath = Join-Path $env:USERPROFILE 'bin\news-grasp-recovery-runtime-binding-v1.json'
    try {
        $bindingItem = Get-Item -LiteralPath $bindingPath -Force -ErrorAction Stop
        if (($bindingItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $bindingItem.LinkType) { throw 'binding path' }
        $binding = Get-Content -LiteralPath $bindingPath -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
        if ([string]$binding.schemaVersion -cne 'NEWS_GRASP_RECOVERY_RUNTIME_BINDING_V1') { throw 'schema' }
        $ops = (Resolve-Path -LiteralPath ([string]$binding.opsRepoRoot) -ErrorAction Stop).Path
        $python = (Resolve-Path -LiteralPath ([string]$binding.pythonExe) -ErrorAction Stop).Path
        $audit = (Resolve-Path -LiteralPath ([string]$binding.auditControlPath) -ErrorAction Stop).Path
        $daily = (Resolve-Path -LiteralPath ([string]$binding.dailySelfHealPath) -ErrorAction Stop).Path
        $expectedPython = (Resolve-Path -LiteralPath $canonicalPython -ErrorAction Stop).Path
        $expectedRuntime = (Resolve-Path -LiteralPath $canonicalRuntime -ErrorAction Stop).Path
        $opsHead = (& $gitExe @gitSafeArgs -C $ops rev-parse HEAD 2>$null | Out-String).Trim().ToLowerInvariant()
        $remoteLine = (& $gitExe @gitSafeArgs ls-remote $trustedRemote refs/heads/main 2>$null | Out-String).Trim()
        $remoteHead = if ($remoteLine) { ($remoteLine -split '\s+')[0].ToLowerInvariant() } else { '' }
        $opsDirty = (& $gitExe @gitSafeArgs -C $ops status --porcelain --untracked-files=all 2>$null | Out-String).Trim()
        $pythonSignature = Get-AuthenticodeSignature -LiteralPath $python
        $pythonSignerSubject = [string]$pythonSignature.SignerCertificate.Subject
        $pythonSignerThumbprint = ([string]$pythonSignature.SignerCertificate.Thumbprint).ToLowerInvariant()
        if (
            -not [string]::Equals($python, $expectedPython, [StringComparison]::OrdinalIgnoreCase) -or
            -not [string]::Equals((Resolve-Path -LiteralPath ([string]$binding.productionRuntimeRoot) -ErrorAction Stop).Path, $expectedRuntime, [StringComparison]::OrdinalIgnoreCase) -or
            [string]$binding.trustedRemote -cne $trustedRemote -or
            [string]$binding.opsHead -cne $opsHead -or
            $opsHead -notmatch '^[0-9a-f]{40}$' -or
            $opsHead -cne $remoteHead -or
            $opsDirty -or
            (Test-Path -LiteralPath (Join-Path $ops 'sitecustomize.py')) -or
            (Test-Path -LiteralPath (Join-Path $ops 'usercustomize.py')) -or
            -not [string]::Equals($audit, (Join-Path $ops 'tools\audit_recovery_control.py'), [StringComparison]::OrdinalIgnoreCase) -or
            -not [string]::Equals($daily, (Join-Path $ops 'tools\daily_self_heal.py'), [StringComparison]::OrdinalIgnoreCase) -or
            -not [string]::Equals((Get-FileHash -LiteralPath $python -Algorithm SHA256).Hash.ToLowerInvariant(), [string]$binding.pythonExeSha256, [StringComparison]::Ordinal) -or
            -not [string]::Equals((Get-FileHash -LiteralPath $audit -Algorithm SHA256).Hash.ToLowerInvariant(), [string]$binding.auditControlSha256, [StringComparison]::Ordinal) -or
            -not [string]::Equals((Get-FileHash -LiteralPath $daily -Algorithm SHA256).Hash.ToLowerInvariant(), [string]$binding.dailySelfHealSha256, [StringComparison]::Ordinal) -or
            [string]$pythonSignature.Status -cne 'Valid' -or
            $pythonSignerSubject -notlike 'CN=Python Software Foundation, O=Python Software Foundation,*' -or
            [string]$binding.pythonTrustAnchor -cne 'authenticode:python-software-foundation' -or
            [string]$binding.pythonSignerSubject -cne $pythonSignerSubject -or
            [string]$binding.pythonSignerThumbprint -cne $pythonSignerThumbprint
        ) { throw 'hash/path' }
        if ($PythonExe -and -not [string]::Equals((Resolve-Path -LiteralPath $PythonExe).Path, $python, [StringComparison]::OrdinalIgnoreCase)) { throw 'override' }
        return [pscustomobject]@{ PythonExe = $python; AuditControlPath = $audit; DailySelfHealPath = $daily }
    } catch {
        throw 'RECOVERY_RUNTIME_BINDING_INVALID'
    }
}

$controlBinding = Get-CanonicalRecoveryControlBinding
$PyExe = [string]$controlBinding.PythonExe
$AuditControlPath = [string]$controlBinding.AuditControlPath
$DailySelfHealPath = [string]$controlBinding.DailySelfHealPath

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
    $terminalJson = (& $PyExe '-I' '-S' '-B' $AuditControlPath 'ensure-0640' '--issue-date' $DateStamp '--trigger' 'deadman_0640' 2>&1 | Out-String).Trim()
    $executorExitCode = $LASTEXITCODE
    try {
        $terminal = $terminalJson | ConvertFrom-Json -ErrorAction Stop
        $summary = "terminal=$([string]$terminal.terminal) reason=$([string]$terminal.reasonCode)"
    } catch { $summary = 'terminal=unparseable' }
    Write-SupervisorLog "audit canonical executor: exit=$executorExitCode $summary"
    if ($executorExitCode -notin @(0, 2, 3)) {
        return 2
    }
    return $executorExitCode
}

Push-Location $RepoDir
try {
    & $PyExe '-I' '-S' '-B' $DailySelfHealPath 'deadman' `
        '--state-file' $StateFile `
        '--date' $DateStamp `
        '--max-ok-age-hours' $MaxOkAgeHours `
        '--alert-log' $alertLog `
        '--marker' $marker
    $deadmanExitCode = $LASTEXITCODE
    if ($deadmanExitCode -notin @(0, 2)) {
        Write-SupervisorLog "legacy deadman observer returned exit=$deadmanExitCode; audit controller remains authoritative"
    }
    # Deadmanは毎時:40に観測するが、canonical recovery auditは06:40だけ。
    # 日付が変わった00:40にterminalを先取りすると06:40がAUDIT_EVENT_REPLAYになる。
    if ((Get-Date).Hour -eq 6) {
        exit (Invoke-Audit0640Control)
    }
    exit $deadmanExitCode
} finally {
    Pop-Location
}
