# News-Grasp daily runner (PowerShell 版)
#
# Codex CLI 専用の発行 runner。LLM 呼び出しは run_codex_with_timeout.ps1 に集約し、
# ラッパー内の codex exec をサブスク認証で実行する。
#
# 機能 (2026-06-06 Plan v3 P0-A で step 順序を再構成):
#   1. invoked sentinel ログ
#   2. git fetch / pull --ff-only origin main
#   3. Codex で digest 生成 + commit (= digest commit はローカルに残る)
#   4. tools/generate_pages.py で docs/ 再生成 (失敗で exit 1 → digest commit 未 push)
#   5. docs/ commit + git push origin main 1 回 (digest commit と docs commit を同時公開)
#   6. send_push 通知
#
# 旧構造 (Plan v3 前) は「digest push → docs build → docs push」で、generate_pages.py
# 失敗時に digest md のみ origin 公開 + docs/ HTML 古いまま (= サイレント公開停止) と
# いう illegal state を表現可能だった。本構造では build 失敗 → 1 push 自体が走らない
# = illegal state unrepresentable ([[feedback_check_design_principles]] §1)。
#
# Param:
#   -SmokeTest  Codex / git push / generate_pages.py を全部スキップ。設定読み込み + ログ書き込み
#              + git fetch だけ走らせて完走するか確認する dry-run モード
#   -PreflightOnly  Codex / git push / generate_pages.py を全部スキップ。E2E 前の
#              schema / prompt / newsroom manifest 契約だけを検証する no-Codex モード
#   -RecoverOnly  生成済み digest / DeepDive を再利用し、Codex を再実行せずに
#              gate 群 → docs 再生成 → docs commit → push → 公開反映確認だけを実行する
#              復旧モード。gate failed 後、対象 md/jsonl を手修正してから使う。
#   -NoPublish  push直前E2E用。Codex / 生成 / gate は通すが、git commit / git push /
#              GitHub Releases upload / YouTube upload / send_push を止める。NoPush を含意する。
#
# 実装上の注意:
#   - すべて 1 PowerShell プロセス内で完結する (cmd.exe を介さない)
#   - 外部コマンドは Invoke-Logged 経由で呼び stdout/stderr を pipe 経由で UTF-8 ログに append
#     (`*>> $LogPath` 直接 redirect は PS 5.1 で native command の stderr が UTF-16 で混入する)
#   - $LASTEXITCODE で終了コード判定
#   - ログは旧 bat と同じ news-grasp-logs/YYYY-MM-DD.log に append (継続性のため)
#   - Windows PowerShell 5.1 互換 (PS7 専用 API は使わない)

[CmdletBinding()]
param(
    [switch] $SmokeTest,
    [switch] $SkipSourceSync,
    [switch] $PreflightOnly,
    [switch] $RecoverOnly,
    [ValidateSet('ScheduledProduction', 'ScheduledRecoveryFull')]
    [string] $RunIntent = 'ScheduledProduction',
    [switch] $NoPush,
    [switch] $NoPublish,
    [switch] $UseCodex,
    [int] $IdleTimeoutSec = 900,
    [switch] $Stage2EditorSmokeOnly,
    [switch] $StopAfterEditorStart,
    [switch] $StopBeforeDeepDive,
    [ValidateSet('', 'post-reporter', 'editor', 'deepdive', 'post-daily-quality', 'post-deepdive', 'generation-quality-repair')]
    [string] $ResumeFromStage = '',
    [string] $RepoDirOverride = '',
    [string] $OpsRepoRootOverride = '',
    [string] $CodexWrapperOverride = '',
    [string] $CodexExeOverride = '',
    [string] $PyExeOverride = '',
    [string] $DateStampOverride = '',
    [string] $LogDirOverride = '',
    [string] $StateFileOverride = '',
    [string] $HighCostAdmissionPath = '',
    [string] $HighCostParentAuthorityPath = '',
    [string] $E2EFinalAdmissionPath = '',
    [string] $E2EFinalRunnerArgumentsPath = '',
    [string] $E2EFinalReservationReceiptPath = '',
    [string] $E2EFinalClaimReceiptPath = '',
    [string] $E2EAttemptPolicyPath = '',
    [ValidateRange(1,2)][int] $E2ELogicalAttempt = 0,
    [string] $HighCostClaimWitness = '',
    [string] $HighCostAttemptId = '',
    [string] $HighCostBudgetToolPath = '',
    [string] $HighCostWorkspaceRoot = '',
    [string] $HighCostBindingPath = '',
    [string] $HighCostBindingReceiptSha256 = '',
    [string] $GlobalHarnessGenerationManifestPath = '',
    [string] $ExternalHealthAuthorityPathOverride = '',
    [string] $ExternalHealthAuthorityExpectedSha256 = '',
    [string] $PowerShellExe = 'powershell.exe',
    [string] $ScheduledAuthorityEvidencePath = '',
    [string] $ScheduledFailureReceiptRootOverride = '',
    [string] $LegacyTaskReceiptPathOverride = '',
    [string] $FinalizeVerifiedPublishManifest = '',
    [string] $RecoveryExecutionReceiptPath = '',
    [string] $RecoveryFinalizationReceiptPath = '',
    [string] $RecoveryDecisionPath = '',
    [int] $PublishVerifyWaitSec = 600,
    [int] $PublishVerifyPollSec = 30,
    [switch] $ForceFullRerun
)

# PS 5.1 で $ErrorActionPreference = 'Stop' にすると、native command (git 等) の
# stderr 出力で NativeCommandError 例外が発火し script が中断する。git fetch /
# pull は進捗を stderr に出すため、ここは Continue にして $LASTEXITCODE で判定する。
$ErrorActionPreference = 'Continue'
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Get-NewsGraspFileSha256Hex {
    param([Parameter(Mandatory = $true)][string] $Path)
    $resolved = (Resolve-Path -LiteralPath $Path -ErrorAction Stop).Path
    $stream = [IO.File]::Open(
        $resolved,
        [IO.FileMode]::Open,
        [IO.FileAccess]::Read,
        [IO.FileShare]::Read
    )
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        return (([BitConverter]::ToString($sha.ComputeHash($stream))) -replace '-', '').ToLowerInvariant()
    } finally {
        $sha.Dispose()
        $stream.Dispose()
    }
}

function Resolve-NewsGraspTrustedPython {
    <#
    Resolve the canonical runtime binding using PowerShell only.  This is the
    sole assignment source for $PyExe; no caller-provided executable or fixed
    AppData path may become the first Python process.
    #>
    $profileRoot = [Environment]::GetFolderPath([Environment+SpecialFolder]::UserProfile)
    $canonicalBin = Join-Path $profileRoot 'bin'
    $bindingPath = Join-Path $canonicalBin 'news-grasp-recovery-runtime-binding-v1.json'
    $trustedSubject = 'CN=Python Software Foundation, O=Python Software Foundation, L=Beaverton, S=Oregon, C=US'
    $trustedThumbprint = '36168ee17c1a240517388540c903bb6717dd2563'

    function Assert-TrustedRegularFile {
        param([Parameter(Mandatory=$true)][string] $Path)
        $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
        if (
            $item.PSIsContainer -or
            (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) -or
            $item.LinkType
        ) { throw 'trusted runtime file is not a regular non-reparse file' }
        return $item
    }

    try {
        $bindingItem = Assert-TrustedRegularFile -Path $bindingPath
        if ([int64]$bindingItem.Length -gt 65536) { throw 'runtime binding exceeds size limit' }
        $binding = Get-Content -LiteralPath $bindingPath -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
        if (
            $null -eq $binding -or
            [string]$binding.schemaVersion -cne 'NEWS_GRASP_RECOVERY_RUNTIME_BINDING_V1' -or
            $binding.PSObject.Properties.Name -notcontains 'pythonExe' -or
            $binding.PSObject.Properties.Name -notcontains 'pythonExeSha256' -or
            $binding.PSObject.Properties.Name -notcontains 'pythonTrustAnchor' -or
            $binding.PSObject.Properties.Name -notcontains 'pythonSignerSubject' -or
            $binding.PSObject.Properties.Name -notcontains 'pythonSignerThumbprint'
        ) { throw 'runtime binding schema mismatch' }
        $pythonValue = [string]$binding.pythonExe
        if (-not [IO.Path]::IsPathRooted($pythonValue)) { throw 'bound Python path is not absolute' }
        $python = (Assert-TrustedRegularFile -Path $pythonValue).FullName
        $pythonItem = Get-Item -LiteralPath $python -Force -ErrorAction Stop
        $cursor = $pythonItem
        while ($null -ne $cursor) {
            if (($cursor.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or $cursor.LinkType) {
                throw 'bound Python path traverses a reparse point'
            }
            $cursor = $cursor.Parent
        }
        $pythonHash = (Get-NewsGraspFileSha256Hex -Path $python).ToLowerInvariant()
        if (
            [string]$binding.pythonExeSha256 -cne $pythonHash -or
            [string]$binding.pythonTrustAnchor -cne 'authenticode:python-software-foundation' -or
            [string]$binding.pythonSignerSubject -cne $trustedSubject -or
            ([string]$binding.pythonSignerThumbprint).ToLowerInvariant() -cne $trustedThumbprint
        ) { throw 'runtime binding Python identity mismatch' }
        $signature = Get-AuthenticodeSignature -LiteralPath $python
        $actualSubject = [string]$signature.SignerCertificate.Subject
        $actualThumbprint = ([string]$signature.SignerCertificate.Thumbprint).ToLowerInvariant()
        if (
            [string]$signature.Status -cne 'Valid' -or
            $actualSubject -cne $trustedSubject -or
            $actualThumbprint -cne $trustedThumbprint
        ) { throw 'runtime binding Python signature is not trusted' }
        $script:TrustedPythonBinding = $binding
        $script:TrustedPythonBindingPath = [IO.Path]::GetFullPath($bindingPath)
        return $python
    } catch {
        throw "RECOVERY_RUNTIME_BINDING_INVALID:$($_.Exception.Message)"
    }
}

function Invoke-LegacyScheduledProductionTrampoline {
    if (
        $RepoDirOverride -or $SmokeTest -or $SkipSourceSync -or $PreflightOnly -or
        $RecoverOnly -or $NoPush -or $NoPublish -or $Stage2EditorSmokeOnly -or
        $StopAfterEditorStart -or $StopBeforeDeepDive -or $ResumeFromStage -or
        $FinalizeVerifiedPublishManifest -or
        $env:NEWS_GRASP_LEGACY_TRAMPOLINE -eq '1'
    ) {
        return
    }
    try {
        $task = Get-ScheduledTask -TaskName 'News-Grasp Runner' -ErrorAction Stop
        $info = Get-ScheduledTaskInfo -TaskName 'News-Grasp Runner' -ErrorAction Stop
    } catch {
        return
    }
    $actionSummary = (@($task.Actions) | ForEach-Object {
        ([string]$_.Execute + ' ' + [string]$_.Arguments).Trim()
    }) -join ' ; '
    $ageMinutes = [math]::Abs(((Get-Date) - $info.LastRunTime).TotalMinutes)
    if (
        [string]$task.State -ne 'Running' -or
        $ageMinutes -gt 10 -or
        $actionSummary -notmatch '(?i)powershell(?:\.exe)?' -or
        $actionSummary -notmatch '(?i)news-grasp-runner\.ps1' -or
        $actionSummary -notmatch [regex]::Escape($PSCommandPath)
    ) {
        return
    }
    $receiptPath = if ($LegacyTaskReceiptPathOverride) {
        $LegacyTaskReceiptPathOverride
    } else {
        Join-Path (Split-Path -Parent $PSCommandPath) 'news-grasp-legacy-task-tombstone.json'
    }
    $receipt = [ordered]@{
        schemaVersion = "NEWS_GRASP_LEGACY_TASK_TOMBSTONE_V1"
        status = "legacy_task_superseded"
        legacy_task_name = "News-Grasp Runner"
        canonical_task_name = "News-Grasp Production"
        scheduled_attempt_status = "not_started_legacy_tombstone"
        production_started = $false
        action_summary = $actionSummary
        task_last_run_time = $info.LastRunTime.ToString('o')
        observed_at = (Get-Date).ToString('o')
    }
    $parent = Split-Path -Parent $receiptPath
    if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    $temporary = "$receiptPath.tmp.$PID"
    $json = $receipt | ConvertTo-Json -Depth 8
    [IO.File]::WriteAllText($temporary, $json + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $temporary -Destination $receiptPath -Force
    exit 0
}

Invoke-LegacyScheduledProductionTrampoline

$UseCodex = $true
if ($SkipSourceSync -and (-not $SmokeTest)) {
    throw '-SkipSourceSync is restricted to -SmokeTest readiness canaries.'
}
if ($StopBeforeDeepDive) { $NoPublish = $true }
if ($NoPublish) { $NoPush = $true }
if ($NoPublish -and (-not $E2EAttemptPolicyPath -or $E2ELogicalAttempt -notin @(1,2))) {
    throw 'NEWS_GRASP_E2E_ATTEMPT_POLICY_REQUIRED'
}
$ResumeFromPostDailyQuality = $ResumeFromStage -in @('deepdive', 'post-daily-quality')
$ResumeAfterDeepDive = $ResumeFromStage -in @('post-deepdive')
$ResumeGenerationQualityRepair = $ResumeFromStage -eq 'generation-quality-repair'
$ResumeAfterReporter = $ResumeFromStage -in @('post-reporter', 'editor')
# 子 Python の stdin/stdout/stderr を UTF-8 に固定 (境界 1 箇所集約)。日本語版 Windows
# では子 Python の stderr が pipe 出力時 locale (CP932) になり、[Console]::OutputEncoding
# = UTF8 の reader が誤デコード → repair プロンプトへ渡る gate stderr が文字化けしていた
# (2026-06-12 実測)。PYTHONUTF8=1 (open()/filesystem まで UTF-8 化する広域 UTF-8 Mode)
# ではなく I/O stream のみの PYTHONIOENCODING を使う: 子ツールは file I/O を encoding
# 明示済で広域化は不要・誤爆面が増えるため。stderr の errorhandler は仕様上常に
# backslashreplace (PEP 540 / docs.python.org using/cmdline)。
$env:PYTHONIOENCODING = 'utf-8:backslashreplace'

# ===== 設定 =====
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
    throw 'News-Grasp repo not found. Set NEWS_GRASP_REPO_DIR or pass -RepoDirOverride.'
}

function Resolve-CodexCliExe {
    param([string] $Override)
    if ($Override) {
        if (Test-Path -LiteralPath $Override) {
            return (Resolve-Path -LiteralPath $Override).Path
        }
        return $Override
    }
    if ($env:NEWS_GRASP_CODEX_EXE) {
        if (Test-Path -LiteralPath $env:NEWS_GRASP_CODEX_EXE) {
            return (Resolve-Path -LiteralPath $env:NEWS_GRASP_CODEX_EXE).Path
        }
        return $env:NEWS_GRASP_CODEX_EXE
    }
    $extensionRoot = Join-Path $env:USERPROFILE '.vscode\extensions'
    $candidate = Get-ChildItem -LiteralPath $extensionRoot -Filter 'openai.chatgpt-*' -Directory -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        ForEach-Object { Join-Path $_.FullName 'bin\windows-x86_64\codex.exe' } |
        Where-Object { Test-Path -LiteralPath $_ } |
        Select-Object -First 1
    if ($candidate) {
        return (Resolve-Path -LiteralPath $candidate).Path
    }
    throw "codex.exe not found under: $extensionRoot. Set NEWS_GRASP_CODEX_EXE or pass -CodexExeOverride."
}

function Get-NewsGraspRecoveryRuntimeBinding {
    $profileRoot = [Environment]::GetFolderPath([Environment+SpecialFolder]::UserProfile)
    $canonicalBin = Join-Path $profileRoot 'bin'
    $bindingPath = Join-Path $canonicalBin 'news-grasp-recovery-runtime-binding-v1.json'
    try {
        $binding = Get-Content -LiteralPath $bindingPath -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
        if ([string]$binding.schemaVersion -cne 'NEWS_GRASP_RECOVERY_RUNTIME_BINDING_V1') { throw 'schema mismatch' }
        $ops = (Resolve-Path -LiteralPath ([string]$binding.opsRepoRoot) -ErrorAction Stop).Path
        $python = (Resolve-Path -LiteralPath ([string]$binding.pythonExe) -ErrorAction Stop).Path
        $runner = (Resolve-Path -LiteralPath ([string]$binding.runnerPath) -ErrorAction Stop).Path
        $gitExe = 'C:\Program Files\Git\cmd\git.exe'
        $gitSafeArgs = @('-c', 'core.hooksPath=NUL', '-c', 'core.fsmonitor=false', '-c', 'core.attributesFile=NUL')
        $trustedRemote = 'https://github.com/HIDEPON-UMG/News-Grasp.git'
        $opsHead = (& $gitExe @gitSafeArgs -C $ops rev-parse HEAD 2>$null | Out-String).Trim().ToLowerInvariant()
        $remoteLine = (& $gitExe @gitSafeArgs ls-remote $trustedRemote refs/heads/main 2>$null | Out-String).Trim()
        $remoteHead = if ($remoteLine) { ($remoteLine -split '\s+')[0].ToLowerInvariant() } else { '' }
        $opsDirty = (& $gitExe @gitSafeArgs -C $ops status --porcelain --untracked-files=all 2>$null | Out-String).Trim()
        $startupCustomizationPresent = (
            (Test-Path -LiteralPath (Join-Path $ops 'sitecustomize.py')) -or
            (Test-Path -LiteralPath (Join-Path $ops 'usercustomize.py'))
        )
        $pythonSignature = Get-AuthenticodeSignature -LiteralPath $python
        $pythonSignerSubject = [string]$pythonSignature.SignerCertificate.Subject
        $pythonSignerThumbprint = ([string]$pythonSignature.SignerCertificate.Thumbprint).ToLowerInvariant()
        if (
            [string]$binding.trustedRemote -cne $trustedRemote -or
            [string]$binding.opsHead -cne $opsHead -or
            $opsHead -notmatch '^[0-9a-f]{40}$' -or
            $opsHead -cne $remoteHead -or
            ($opsDirty -and -not $RepoDirOverride) -or
            $startupCustomizationPresent -or
            -not [string]::Equals($runner, (Resolve-Path -LiteralPath $PSCommandPath).Path, [StringComparison]::OrdinalIgnoreCase) -or
            -not [string]::Equals((Split-Path -Parent $runner), $canonicalBin, [StringComparison]::OrdinalIgnoreCase) -or
            -not [string]::Equals((Get-NewsGraspFileSha256Hex -Path $runner), [string]$binding.runnerSha256, [StringComparison]::Ordinal) -or
            -not [string]::Equals((Get-NewsGraspFileSha256Hex -Path $python), [string]$binding.pythonExeSha256, [StringComparison]::Ordinal) -or
            [string]$pythonSignature.Status -cne 'Valid' -or
            $pythonSignerSubject -notlike 'CN=Python Software Foundation, O=Python Software Foundation,*' -or
            [string]$binding.pythonTrustAnchor -cne 'authenticode:python-software-foundation' -or
            [string]$binding.pythonSignerSubject -cne $pythonSignerSubject -or
            [string]$binding.pythonSignerThumbprint -cne $pythonSignerThumbprint
        ) { throw 'entrypoint hash mismatch' }
        foreach ($tool in @(
            @('receiptToolPath', 'receiptToolSha256'),
            @('controlPlaneToolPath', 'controlPlaneToolSha256'),
            @('completionGuardToolPath', 'completionGuardToolSha256'),
            @('dailySelfHealPath', 'dailySelfHealSha256'),
            @('auditControlPath', 'auditControlSha256')
        )) {
            $toolPath = (Resolve-Path -LiteralPath ([string]$binding.($tool[0])) -ErrorAction Stop).Path
            $expectedToolPath = (Resolve-Path -LiteralPath (Join-Path $ops ('tools\' + [IO.Path]::GetFileName($toolPath))) -ErrorAction Stop).Path
            if (
                -not [string]::Equals($toolPath, $expectedToolPath, [StringComparison]::OrdinalIgnoreCase) -or
                -not [string]::Equals((Get-NewsGraspFileSha256Hex -Path $toolPath), [string]$binding.($tool[1]), [StringComparison]::Ordinal)
            ) { throw 'tool hash mismatch' }
        }
        $highCostBinding = (Resolve-Path -LiteralPath ([string]$binding.highCostBindingPath) -ErrorAction Stop).Path
        if (
            -not [string]::Equals($highCostBinding, (Join-Path $canonicalBin 'news-grasp-high-cost-binding-v1.json'), [StringComparison]::OrdinalIgnoreCase) -or
            -not [string]::Equals((Get-NewsGraspFileSha256Hex -Path $highCostBinding), [string]$binding.highCostBindingFileSha256, [StringComparison]::Ordinal) -or
            [string]$binding.highCostBindingReceiptSha256 -notmatch '^[0-9a-f]{64}$'
        ) { throw 'high-cost binding mismatch' }
        return [pscustomobject]@{
            OpsRepoRoot = $ops
            PythonExe = $python
            ProductionRuntimeRoot = (Resolve-Path -LiteralPath ([string]$binding.productionRuntimeRoot) -ErrorAction Stop).Path
            LiveBinRoot = $canonicalBin
            ReceiptToolPath = (Resolve-Path -LiteralPath ([string]$binding.receiptToolPath) -ErrorAction Stop).Path
            CompletionGuardToolPath = (Resolve-Path -LiteralPath ([string]$binding.completionGuardToolPath) -ErrorAction Stop).Path
            DailySelfHealPath = (Resolve-Path -LiteralPath ([string]$binding.dailySelfHealPath) -ErrorAction Stop).Path
            HighCostBindingPath = $highCostBinding
            HighCostBindingReceiptSha256 = [string]$binding.highCostBindingReceiptSha256
        }
    } catch {
        throw "RECOVERY_RUNTIME_BINDING_INVALID:$($_.Exception.Message)"
    }
}

$RepoDir   = Resolve-NewsGraspRepoDir -Override $RepoDirOverride
# Resolve and validate the binding before any production-intent Python call.
# This assignment intentionally precedes recovery-specific metadata loading.
$PyExe     = Resolve-NewsGraspTrustedPython
$RecoveryRuntimeBinding = if ($RunIntent -eq 'ScheduledRecoveryFull') {
    Get-NewsGraspRecoveryRuntimeBinding
} else { $null }
$OpsRepoRoot = if ($null -ne $RecoveryRuntimeBinding) {
    [string]$RecoveryRuntimeBinding.OpsRepoRoot
} elseif ($OpsRepoRootOverride) {
    (Resolve-Path -LiteralPath $OpsRepoRootOverride).Path
} elseif ($env:NEWS_GRASP_OPS_REPO_ROOT) {
    (Resolve-Path -LiteralPath $env:NEWS_GRASP_OPS_REPO_ROOT).Path
} else {
    $RepoDir
}
. (Join-Path $PSScriptRoot 'news-grasp-lineage.ps1')
$LogDir    = Join-Path $env:USERPROFILE 'bin\news-grasp-logs'
$GitExe    = 'C:\Program Files\Git\cmd\git.exe'
$GitSafeArgs = @('-c', 'core.hooksPath=NUL', '-c', 'core.fsmonitor=false', '-c', 'core.attributesFile=NUL')
$env:GIT_ATTR_NOSYSTEM = '1'
$CodexExe  = Resolve-CodexCliExe -Override $CodexExeOverride
$CodexWrapper = Join-Path $env:USERPROFILE 'bin\run_codex_with_timeout.ps1'
$TimeoutSec = 4800  # 2026-06-12: 3600→4800。日次 digest の wall-clock timeout を 80 分へ延長。真の暴走は IdleTimeoutSec 900 が先に検知する
$PromptFile = Join-Path $RepoDir 'prompts\runner-prompt.md'
$CodexOutputSchema = Join-Path $RepoDir 'schemas\model_eval_output.schema.json'
$CodexLastMessage = Join-Path $RepoDir 'build\codex-last-message.txt'
$RepoManagedRunner = Join-Path $RepoDir 'scripts\ops\news-grasp-runner.ps1'
$RepoManagedWatcher = Join-Path $RepoDir 'scripts\ops\watch-news-grasp-runner.ps1'
$E2EFinalAdmissionBridge = Join-Path $RepoDir 'tools\e2e_final_admission_bridge.py'
$canonicalMaterializer = Join-Path $OpsRepoRoot 'tools\materialize_editor_output.py'
$PublicBaseUrl = 'https://hidepon-umg.github.io/News-Grasp/'
$InvokedLog = Join-Path $env:USERPROFILE 'bin\news-grasp-invoked.log'
$StateFile  = Join-Path $env:USERPROFILE 'bin\news-grasp-runner-state.json'
$LiveBinDir = if ($PSCommandPath) { Split-Path -Parent $PSCommandPath } else { Join-Path $env:USERPROFILE 'bin' }
$TrustedProductionRuntimeRoot = if ($null -ne $RecoveryRuntimeBinding) { [string]$RecoveryRuntimeBinding.ProductionRuntimeRoot } else { Join-Path $env:USERPROFILE '.news-grasp-runtime\production-runtime' }
$TrustedRecoveryLiveBinRoot = if ($null -ne $RecoveryRuntimeBinding) { [string]$RecoveryRuntimeBinding.LiveBinRoot } else { $LiveBinDir }
$BootstrapSmokeStateFile = Join-Path $LiveBinDir 'ng-smoke-state.json'
$BootstrapSmokeLogDir = Join-Path $LiveBinDir 'ng-smoke-logs'
$BootstrapSmokeEarliestMinutes = 5 * 60 + 55
$BootstrapSmokeFreshnessMinutes = 15
$RunnerSyncReexecEnvVar = 'NEWS_GRASP_RUNNER_SYNC_REEXEC'
$MaxParallelReporterJobs = 7

if ($CodexWrapperOverride) { $CodexWrapper = $CodexWrapperOverride }
if ($PyExeOverride -and -not [string]::Equals((Resolve-Path -LiteralPath $PyExeOverride -ErrorAction Stop).Path, $PyExe, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'RECOVERY_RUNTIME_BINDING_INVALID:PyExeOverride does not match trusted binding'
}
if ($null -ne $RecoveryRuntimeBinding) {
    if (
        ($OpsRepoRootOverride -and -not [string]::Equals((Resolve-Path -LiteralPath $OpsRepoRootOverride).Path, [string]$RecoveryRuntimeBinding.OpsRepoRoot, [StringComparison]::OrdinalIgnoreCase)) -or
        ($env:NEWS_GRASP_OPS_REPO_ROOT -and -not [string]::Equals((Resolve-Path -LiteralPath $env:NEWS_GRASP_OPS_REPO_ROOT).Path, [string]$RecoveryRuntimeBinding.OpsRepoRoot, [StringComparison]::OrdinalIgnoreCase)) -or
        ($PyExeOverride -and -not [string]::Equals((Resolve-Path -LiteralPath $PyExeOverride).Path, [string]$RecoveryRuntimeBinding.PythonExe, [StringComparison]::OrdinalIgnoreCase)) -or
        ($HighCostBindingPath -and -not [string]::Equals((Resolve-Path -LiteralPath $HighCostBindingPath).Path, [string]$RecoveryRuntimeBinding.HighCostBindingPath, [StringComparison]::OrdinalIgnoreCase)) -or
        ($HighCostBindingReceiptSha256 -and [string]$HighCostBindingReceiptSha256 -cne [string]$RecoveryRuntimeBinding.HighCostBindingReceiptSha256)
    ) { throw 'RECOVERY_RUNTIME_BINDING_INVALID' }
    if (-not [string]::Equals($PyExe, [string]$RecoveryRuntimeBinding.PythonExe, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'RECOVERY_RUNTIME_BINDING_INVALID:trusted Python drift'
    }
    if (-not $HighCostBindingPath) { $HighCostBindingPath = [string]$RecoveryRuntimeBinding.HighCostBindingPath }
    if (-not $HighCostBindingReceiptSha256) { $HighCostBindingReceiptSha256 = [string]$RecoveryRuntimeBinding.HighCostBindingReceiptSha256 }
}
if ($LogDirOverride) { $LogDir = $LogDirOverride }
if ($StateFileOverride) { $StateFile = $StateFileOverride }
if (-not $HighCostAdmissionPath -and $env:NEWS_GRASP_HIGH_COST_ADMISSION_PATH) {
    $HighCostAdmissionPath = $env:NEWS_GRASP_HIGH_COST_ADMISSION_PATH
}
if ($HighCostBudgetToolPath -or $HighCostWorkspaceRoot) {
    throw 'HIGH_COST_LEGACY_ROOT_ARGUMENT_FORBIDDEN'
}
if ((-not $HighCostBindingPath) -or (-not $HighCostBindingReceiptSha256)) {
    throw 'HIGH_COST_WORKSPACE_BINDING_MISSING'
}
$highCostBindingTool = Join-Path $OpsRepoRoot 'tools\news_grasp_high_cost_binding.py'
if (-not (Test-Path -LiteralPath $highCostBindingTool -PathType Leaf)) {
    throw 'HIGH_COST_WORKSPACE_BINDING_MISSING'
}
$highCostBindingJson = (& $PyExe '-I' '-S' '-B' $highCostBindingTool 'resolve' '--binding' $HighCostBindingPath '--expected-receipt-sha256' $HighCostBindingReceiptSha256 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0) {
    try {
        $highCostBindingFailure = $highCostBindingJson | ConvertFrom-Json -ErrorAction Stop
        $highCostBindingReason = [string]$highCostBindingFailure.reason
    } catch { $highCostBindingReason = 'HIGH_COST_WORKSPACE_BINDING_MISSING' }
    if ($highCostBindingReason -notin @(
        'HIGH_COST_WORKSPACE_BINDING_MISSING',
        'HIGH_COST_BROKER_UNAVAILABLE',
        'HIGH_COST_OPERATION_ADMISSION_REQUIRED',
        'HIGH_COST_AUTHORITY_INVALID',
        'HIGH_COST_BUDGET_EXHAUSTED',
        'HIGH_COST_IDENTITY_DRIFT'
    )) { $highCostBindingReason = 'HIGH_COST_WORKSPACE_BINDING_MISSING' }
    throw $highCostBindingReason
}
try { $highCostBinding = $highCostBindingJson | ConvertFrom-Json -ErrorAction Stop } catch { throw 'HIGH_COST_WORKSPACE_BINDING_MISSING' }
if (
    [string]$highCostBinding.bindingSchemaVersion -cne 'NEWS_GRASP_HIGH_COST_BINDING_V1' -or
    [string]$highCostBinding.bindingReceiptSha256 -cne $HighCostBindingReceiptSha256.ToLowerInvariant() -or
    [string]$highCostBinding.status -cne 'available'
) { throw 'HIGH_COST_IDENTITY_DRIFT' }
$HighCostWorkspaceRoot = [string]$highCostBinding.workspaceRoot
$HighCostBudgetToolPath = [string]$highCostBinding.brokerInstalledPath
$HighCostBindingResolverPath = $highCostBindingTool
$HighCostBindingResolverSha256 = Get-NewsGraspFileSha256Hex -Path $highCostBindingTool
$env:NEWS_GRASP_HIGH_COST_BINDING_PATH = (Resolve-Path -LiteralPath $HighCostBindingPath).Path
$env:NEWS_GRASP_HIGH_COST_BINDING_RECEIPT_SHA256 = $HighCostBindingReceiptSha256.ToLowerInvariant()
$env:PYTHONSAFEPATH = '1'
$env:PYTHONNOUSERSITE = '1'
$env:PYTHONPATH = $RepoDir
$PromptFile = Join-Path $RepoDir 'prompts\runner-prompt.md'
$CodexOutputSchema = Join-Path $RepoDir 'schemas\model_eval_output.schema.json'
$CodexLastMessage = Join-Path $RepoDir 'build\codex-last-message.txt'
$RepoManagedRunner = Join-Path $RepoDir 'scripts\ops\news-grasp-runner.ps1'
$RepoManagedWatcher = Join-Path $RepoDir 'scripts\ops\watch-news-grasp-runner.ps1'

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

# YYYY-MM-DD ログファイル
$DateStamp = if ($DateStampOverride) { $DateStampOverride } else { Get-Date -Format 'yyyy-MM-dd' }
try {
    $parsedDateStamp = [DateTime]::ParseExact(
        $DateStamp,
        'yyyy-MM-dd',
        [System.Globalization.CultureInfo]::InvariantCulture,
        [System.Globalization.DateTimeStyles]::None
    )
    if ($parsedDateStamp.ToString('yyyy-MM-dd') -cne $DateStamp) {
        throw 'date round-trip mismatch'
    }
} catch {
    Write-Host 'ERROR: NEWS_GRASP_DATE_STAMP_INVALID'
    exit 64
}
$LogPath = Join-Path $LogDir ("$DateStamp.log")
$CodexUsageLog = Join-Path $RepoDir "build\codex-usage\$DateStamp.jsonl"
$CodexUsageWindowLog = Join-Path $RepoDir "build\codex-usage\$DateStamp.windows.jsonl"
$script:CodexUsageEndSnapshotWritten = $false
$RunId = [guid]::NewGuid().ToString('N')
$script:HighCostCallSequence = 0
$script:RecoveryHighCostCutoff = $null
$script:RecoveryHardDeadline = $null
$script:RecoveryMaxExternalModelCalls = 0
$script:HighCostExpectedOperationKind = ''
$script:HighCostExpectedIssueDate = ''
$script:HighCostAdmissionPath = $HighCostAdmissionPath
$script:UsesHighCostContinuationAdmission = $false
$script:ScheduledRecoveryStageBrokerPath = ''
$script:ScheduledRecoveryStageDecisionReceiptPath = ''
$script:HighCostParentAuthorityPath = $HighCostParentAuthorityPath
$script:HighCostParentAuthoritySha256 = ''
$script:E2EFinalAdmissionPath = $E2EFinalAdmissionPath
$script:E2EFinalRunnerArgumentsPath = $E2EFinalRunnerArgumentsPath
$script:E2EFinalReservationReceiptPath = $E2EFinalReservationReceiptPath
$script:E2EFinalClaimReceiptPath = $E2EFinalClaimReceiptPath
$script:HighCostClaimWitness = ''
$script:HighCostAttemptId = if ($HighCostAttemptId) { $HighCostAttemptId } else { $DateStamp }
$HighCostCallReceiptDir = Join-Path $RepoDir "build\high-cost-call-receipts\$DateStamp\$RunId"
$script:RunnerCommandLine = ''
$script:RunnerCommandLineFingerprint = ''
$script:RunnerProcessCreationTime = ''
$script:PublishCompleteManifestPath = ''
$script:PublishCompleteCommit = ''
$script:ScheduledFailureTerminalized = $false
$script:ScheduledFailureTerminalInputPath = ''
$script:ExternalHealthAuthorityPath = Join-Path $env:USERPROFILE '.codex\state\high-cost-operation\external-health-authority-v1.json'
$script:ExternalHealthAuthorityFixtureMode = $false
if ($ExternalHealthAuthorityPathOverride) {
    if (-not $NoPublish) {
        throw 'EXTERNAL_AUTHORITY_OVERRIDE_FORBIDDEN'
    }
    if ($ExternalHealthAuthorityExpectedSha256 -notmatch '^[0-9a-f]{64}$') {
        throw 'EXTERNAL_AUTHORITY_FIXTURE_HASH_INVALID'
    }
    try {
        $fixtureAuthorityPath = [System.IO.Path]::GetFullPath((Resolve-Path -LiteralPath $ExternalHealthAuthorityPathOverride -ErrorAction Stop).Path)
        $repoBoundary = [System.IO.Path]::GetFullPath((Resolve-Path -LiteralPath $RepoDir -ErrorAction Stop).Path).TrimEnd('\')
        $repoPrefix = $repoBoundary + '\'
        if (-not $fixtureAuthorityPath.StartsWith($repoPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw 'fixture authority is outside RepoDir'
        }
        $cursor = $fixtureAuthorityPath
        while ($cursor) {
            $item = Get-Item -LiteralPath $cursor -Force -ErrorAction Stop
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw 'fixture authority traversal contains reparse point'
            }
            if ([string]::Equals($cursor, $repoBoundary, [System.StringComparison]::OrdinalIgnoreCase)) { break }
            $parent = Split-Path -Parent $cursor
            if (-not $parent -or $parent -eq $cursor) { throw 'fixture authority boundary traversal failed' }
            $cursor = $parent
        }
        if (-not (Test-Path -LiteralPath $fixtureAuthorityPath -PathType Leaf)) {
            throw 'fixture authority is not a regular file'
        }
        $observedFixtureSha256 = Get-NewsGraspFileSha256Hex -Path $fixtureAuthorityPath
        if (-not [string]::Equals($observedFixtureSha256, $ExternalHealthAuthorityExpectedSha256, [System.StringComparison]::Ordinal)) {
            throw 'EXTERNAL_AUTHORITY_FIXTURE_HASH_DRIFT'
        }
        $script:ExternalHealthAuthorityPath = $fixtureAuthorityPath
        $script:ExternalHealthAuthorityFixtureMode = $true
        $script:ExternalHealthAuthorityExpectedSha256 = $ExternalHealthAuthorityExpectedSha256
    } catch {
        if ($_.Exception.Message -eq 'EXTERNAL_AUTHORITY_FIXTURE_HASH_DRIFT') { throw }
        throw "EXTERNAL_AUTHORITY_FIXTURE_INVALID: $($_.Exception.Message)"
    }
}

function Assert-GlobalHarnessGenerationManifest {
    param([Parameter(Mandatory=$true)][string] $ManifestPath)
    try {
        $resolved = [System.IO.Path]::GetFullPath((Resolve-Path -LiteralPath $ManifestPath -ErrorAction Stop).Path)
        $repoBoundary = [System.IO.Path]::GetFullPath((Resolve-Path -LiteralPath $RepoDir -ErrorAction Stop).Path).TrimEnd('\')
        if (-not $resolved.StartsWith($repoBoundary + '\', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'outside RepoDir' }
        $item = Get-Item -LiteralPath $resolved -Force -ErrorAction Stop
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or [int64]$item.Length -gt 65536) { throw 'invalid manifest file' }
        $cursor = $resolved
        while ($cursor) {
            $cursorItem = Get-Item -LiteralPath $cursor -Force -ErrorAction Stop
            if (($cursorItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'manifest traversal reparse' }
            if ([string]::Equals($cursor, $repoBoundary, [System.StringComparison]::OrdinalIgnoreCase)) { break }
            $parent = Split-Path -Parent $cursor
            if (-not $parent -or $parent -eq $cursor) { throw 'manifest boundary failed' }
            $cursor = $parent
        }
        $manifestSha = Get-NewsGraspFileSha256Hex -Path $resolved
        $value = Get-Content -LiteralPath $resolved -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
        $required = @('schemaVersion','generationId','ownerRepo','ownerCommit','sourceSnapshotPath','sourceSnapshotSha256','installedRuntimePath','installedRuntimeSha256','ownerAuthorityReceiptPath','ownerAuthorityReceiptSha256','validForGoalId')
        $observed = @($value.PSObject.Properties.Name)
        if ($value.schemaVersion -cne 'NEWS_GRASP_GLOBAL_DEPENDENCY_GENERATION_MANIFEST_V1' -or
            (@($observed | Sort-Object) -join '|') -cne (@($required | Sort-Object) -join '|') -or
            [string]$value.generationId -eq '' -or [string]$value.validForGoalId -eq '' -or
            [string]$value.ownerCommit -notmatch '^[0-9a-f]{40,64}$') { throw 'manifest schema mismatch' }
        foreach ($pair in @(@('sourceSnapshotPath','sourceSnapshotSha256'), @('installedRuntimePath','installedRuntimeSha256'), @('ownerAuthorityReceiptPath','ownerAuthorityReceiptSha256'))) {
            $payloadPath = [System.IO.Path]::GetFullPath([string]$value.($pair[0]))
            if (-not $payloadPath.StartsWith($repoBoundary + '\', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'manifest payload outside RepoDir' }
            $payload = Get-Item -LiteralPath $payloadPath -Force -ErrorAction Stop
            if (($payload.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or -not $payload.PSIsContainer -and [int64]$payload.Length -gt 67108864) { throw 'manifest payload invalid' }
            $payloadSha = Get-NewsGraspFileSha256Hex -Path $payloadPath
            if ($payloadSha -cne [string]$value.($pair[1])) { throw 'manifest payload drift' }
        }
        return [pscustomobject]@{ path = $resolved; sha256 = $manifestSha; generationId = [string]$value.generationId; goalId = [string]$value.validForGoalId }
    } catch {
        throw "NEWS_GRASP_GLOBAL_GENERATION_MANIFEST_INVALID: $($_.Exception.Message)"
    }
}

function Resolve-NewsGraspContainedRegularFile {
    param(
        [Parameter(Mandatory = $true)][string] $Path,
        [Parameter(Mandatory = $true)][string] $ExpectedPath,
        [int64] $MaxBytes = 1048576
    )
    $candidate = [System.IO.Path]::GetFullPath($Path)
    $expected = [System.IO.Path]::GetFullPath($ExpectedPath)
    if (-not [string]::Equals($candidate, $expected, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'contained file identity mismatch'
    }
    $repoBoundary = [System.IO.Path]::GetFullPath((Resolve-Path -LiteralPath $RepoDir -ErrorAction Stop).Path).TrimEnd('\')
    if (-not $candidate.StartsWith($repoBoundary + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'contained file outside RepoDir'
    }
    $cursor = $candidate
    while ($cursor) {
        $item = Get-Item -LiteralPath $cursor -Force -ErrorAction Stop
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw 'contained file traversal contains reparse point'
        }
        if ([string]::Equals($cursor, $repoBoundary, [System.StringComparison]::OrdinalIgnoreCase)) { break }
        $parent = Split-Path -Parent $cursor
        if (-not $parent -or $parent -eq $cursor) { throw 'contained file boundary failed' }
        $cursor = $parent
    }
    $leaf = Get-Item -LiteralPath $candidate -Force -ErrorAction Stop
    if ($leaf.PSIsContainer -or [int64]$leaf.Length -gt $MaxBytes) {
        throw 'contained file is invalid'
    }
    return $candidate
}

$script:GlobalHarnessGenerationManifest = $null
if ($GlobalHarnessGenerationManifestPath) {
    $script:GlobalHarnessGenerationManifest = Assert-GlobalHarnessGenerationManifest -ManifestPath $GlobalHarnessGenerationManifestPath
    $env:NEWS_GRASP_GLOBAL_GENERATION_MANIFEST_PATH = [string]$script:GlobalHarnessGenerationManifest.path
    $env:NEWS_GRASP_GLOBAL_GENERATION_MANIFEST_SHA256 = [string]$script:GlobalHarnessGenerationManifest.sha256
}
$ScheduledFailureReceiptRoot = if ($ScheduledFailureReceiptRootOverride) {
    [System.IO.Path]::GetFullPath($ScheduledFailureReceiptRootOverride)
} else {
    Join-Path $RepoDir 'build\scheduled-failure-receipts'
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

function Get-RunnerCommandLine {
    try {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$PID" -ErrorAction Stop
        if ($proc.CommandLine) { return [string]$proc.CommandLine }
    } catch { }
    return [Environment]::CommandLine
}

function Initialize-RunnerIdentity {
    $script:RunnerCommandLine = Get-RunnerCommandLine
    $script:RunnerCommandLineFingerprint = Get-StringSha256Hex -Text ($script:RunnerCommandLine.Trim().ToLowerInvariant())
    try {
        $script:RunnerProcessCreationTime = (Get-Process -Id $PID -ErrorAction Stop).StartTime.ToString('yyyy-MM-ddTHH:mm:ss.fffK')
    } catch {
        $script:RunnerProcessCreationTime = (Get-Date).ToString('yyyy-MM-ddTHH:mm:ss.fffK')
    }
}

Initialize-RunnerIdentity
$NormalPublishVerified = $false
$NotificationStatePath = ''
$script:RunnerStartedAt = Get-Date

function Convert-PublishInventoryJson {
    param([string[]] $Json)
    $jsonText = [string]::Join([Environment]::NewLine, @($Json))
    $parsed = $jsonText | ConvertFrom-Json
    $items = New-Object 'System.Collections.Generic.List[string]'
    foreach ($item in @($parsed)) {
        if ($item -is [System.Array]) {
            foreach ($nested in @($item)) {
                if ($null -ne $nested) { [void]$items.Add([string]$nested) }
            }
            continue
        }
        if ($null -ne $item) { [void]$items.Add([string]$item) }
    }
    return @($items)
}

function Get-PublishInventoryArtifacts {
    param([ValidateSet('categories', 'digest', 'generated', 'published', 'published-repair', 'distribution')] [string] $Kind)
    Push-Location $RepoDir
    try {
        $json = & $PyExe '-B' '-m' 'tools.publish_inventory' '--date' $DateStamp '--kind' $Kind '--json'
        if ($LASTEXITCODE -ne 0) {
            throw "tools.publish_inventory failed (kind=$Kind, rc=$LASTEXITCODE)"
        }
        return @(Convert-PublishInventoryJson -Json $json)
    } finally {
        Pop-Location
    }
}

$DailyDigestArtifacts = @()
$PublishedDocsArtifacts = @()
$PublishedRepairArtifacts = @()
$script:RequiredCategoriesForSlo = @()

function Get-RunnerStateMutexName {
    param([string] $Path)
    $hash = (Get-StringSha256Hex -Text ([System.IO.Path]::GetFullPath($Path).ToLowerInvariant())).Substring(0, 24)
    return "Local\NewsGraspRunnerState-$hash"
}

function Get-RunnerLogMutexName {
    param([string] $Path)
    $hash = (Get-StringSha256Hex -Text ([System.IO.Path]::GetFullPath($Path).ToLowerInvariant())).Substring(0, 24)
    return "Local\NewsGraspRunnerLog-$hash"
}

function Invoke-WithRunnerLogLock {
    param([scriptblock] $Block)
    $mutex = [System.Threading.Mutex]::new($false, (Get-RunnerLogMutexName -Path $LogPath))
    $locked = $false
    try {
        try {
            $locked = $mutex.WaitOne(5000)
        } catch [System.Threading.AbandonedMutexException] {
            $locked = $true
        }
        if (-not $locked) { throw 'blocked_runner_log_lock_timeout' }
        & $Block
    } finally {
        if ($locked) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
}

function Add-RunnerLogLine {
    param([AllowEmptyString()][string] $Text)
    Invoke-WithRunnerLogLock {
        $stream = [System.IO.FileStream]::new(
            $LogPath,
            [System.IO.FileMode]::Append,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::Read
        )
        try {
            $writer = [System.IO.StreamWriter]::new(
                $stream,
                [System.Text.UTF8Encoding]::new($false),
                1024,
                $false
            )
            try {
                $writer.WriteLine($Text)
                $writer.Flush()
                $stream.Flush($true)
            } finally {
                $writer.Dispose()
            }
        } finally {
            $stream.Dispose()
        }
    }
}

function Get-RunnerLogSha256 {
    $sha256 = Invoke-WithRunnerLogLock {
        Get-FileSha256Hex -Path $LogPath
    }
    return $sha256
}

function Test-TerminalRunnerStatus {
    param([string] $Status)
    if ([string]$Status -like 'blocked_*') { return $true }
    return @(
        'publish_complete',
        'smoke_ok',
        'preflight_ok',
        'publish_dry_run_ok',
        'watchdog_stale_timeout',
        'watchdog_wall_timeout',
        'watchdog_stale_unconfirmed',
        'watchdog_state_corrupt',
        'blocked_runner_timeout',
        'blocked_gate_timeout',
        'blocked_reporter_timeout',
        'blocked_reporter_repeated_failure',
        'blocked_repair_budget_exhausted',
        'blocked_slo_violation',
        'blocked_refill_unresolved',
        'blocked_external_readiness',
        'blocked_runner_state_lock_timeout',
        'blocked_runner_state_corrupt',
        'distribution_failed',
        'publish_failed',
        'repair_context_scope_mismatch',
        'repair_handler_output_scope_violation',
        'repository_safety_stop',
        'public_surface_red',
        'distribution_manifest_invalid',
        'deploy_surface_regression',
        'deploy_surface_unrelated_red',
        'failed',
        'error'
    ) -contains [string]$Status
}

function Write-RunnerStateAtomic {
    param(
        [string] $Path,
        [object] $Payload
    )
    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    $json = ($Payload | ConvertTo-Json -Depth 8) + "`n"
    $tmp = "$Path.tmp.$PID.$([guid]::NewGuid().ToString('N'))"
    $backup = "$Path.bak"
    $bytes = [System.Text.UTF8Encoding]::new($false).GetBytes($json)
    $fs = [System.IO.File]::Open($tmp, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
    try {
        $fs.Write($bytes, 0, $bytes.Length)
        $fs.Flush($true)
    } finally {
        $fs.Dispose()
    }
    # watcher の read handle と Windows の File.Replace が瞬間的に競合するため、
    # atomic commit 自体は維持したまま、短い bounded retry だけを許可する。
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        try {
            if (Test-Path -LiteralPath $Path) {
                [System.IO.File]::Replace($tmp, $Path, $backup, $true)
            } else {
                [System.IO.File]::Move($tmp, $Path)
            }
            return
        } catch [System.IO.IOException] {
            if ($attempt -eq 3) { throw }
            Start-Sleep -Milliseconds 50
        }
    }
}

function Write-RunnerJsonExclusive {
    param(
        [string] $Path,
        [object] $Payload
    )
    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    $json = ($Payload | ConvertTo-Json -Depth 12) + "`n"
    $bytes = [System.Text.UTF8Encoding]::new($false).GetBytes($json)
    $stream = [System.IO.File]::Open(
        $Path,
        [System.IO.FileMode]::CreateNew,
        [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::Read
    )
    try {
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
    } finally {
        $stream.Dispose()
    }
}

function New-ScheduledFailureTerminalInput {
    param(
        [object] $State,
        [string] $Status,
        [int] $ExitCode
    )
    $inputRoot = Join-Path $ScheduledFailureReceiptRoot 'inputs'
    $stateEvidencePath = Join-Path $inputRoot ("$DateStamp-$RunId-state.json")
    $logEvidencePath = Join-Path $inputRoot ("$DateStamp-$RunId-log.json")
    $terminalInputPath = Join-Path $inputRoot ("$DateStamp-$RunId-terminal-input.json")
    Write-RunnerJsonExclusive -Path $stateEvidencePath -Payload $State
    $stateEvidenceSha256 = Get-FileSha256Hex -Path $stateEvidencePath
    Invoke-WithRunnerLogLock {
        $dailyLogSha256 = Get-FileSha256Hex -Path $LogPath
        $logEvidence = [ordered]@{
            schemaVersion = 'NEWS_GRASP_RUN_LOG_EVIDENCE_V1'
            issueDate = $DateStamp
            runId = $RunId
            runIntent = $RunIntent
            status = $Status
            exitCode = $ExitCode
            dailyLogPath = [System.IO.Path]::GetFullPath($LogPath)
            dailyLogSha256 = $dailyLogSha256
        }
        Write-RunnerJsonExclusive -Path $logEvidencePath -Payload $logEvidence
    }
    $logEvidenceSha256 = Get-FileSha256Hex -Path $logEvidencePath
    $terminalInput = [ordered]@{
        schemaVersion = 'NEWS_GRASP_SCHEDULED_FAILURE_TERMINAL_INPUT_V1'
        issueDate = $DateStamp
        runId = $RunId
        runIntent = $RunIntent
        status = $Status
        exitCode = $ExitCode
        stateEvidencePath = $stateEvidencePath
        stateEvidenceSha256 = $stateEvidenceSha256
        logEvidencePath = $logEvidencePath
        logEvidenceSha256 = $logEvidenceSha256
    }
    Write-RunnerJsonExclusive -Path $terminalInputPath -Payload $terminalInput
    return $terminalInputPath
}

function Read-RunnerStateOrNull {
    param([string] $Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        try {
            return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
        } catch {
            if ($attempt -lt 3) {
                Start-Sleep -Milliseconds 100
            }
        }
    }
    $stamp = Get-Date -Format 'yyyyMMddHHmmss'
    $corrupt = "$Path.corrupt.$stamp.json"
    try { Copy-Item -LiteralPath $Path -Destination $corrupt -Force -ErrorAction SilentlyContinue } catch { }
    return [pscustomobject]@{ __corrupt = $true; corrupt_backup = $corrupt }
}

function Get-RunnerStateProperty {
    param(
        [object] $State,
        [string] $Name,
        [object] $Default = $null
    )
    if ($null -eq $State) { return $Default }
    if ($State.PSObject.Properties.Name -notcontains $Name) { return $Default }
    return $State.$Name
}

function Invoke-WithRunnerStateLock {
    param([scriptblock] $Block)
    $mutexName = Get-RunnerStateMutexName -Path $StateFile
    $mutex = [System.Threading.Mutex]::new($false, $mutexName)
    $locked = $false
    try {
        $locked = $mutex.WaitOne(5000)
        if (-not $locked) {
            throw 'blocked_runner_state_lock_timeout'
        }
        & $Block
    } finally {
        if ($locked) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
}

function Set-RunnerState {
    param(
        [string] $Status,
        [string] $Message,
        [int] $ExitCode = -1,
        [switch] $ResetStartedAt,
        [string] $Phase = '',
        [string] $Step = '',
        [string] $GateId = '',
        [string] $Category = '',
        [int] $Attempt = 0,
        [object] $ActiveJobs = $null,
        [string] $DeadlineAt = '',
        [string] $HeartbeatAt = '',
        [string] $PublishManifestPath = '',
        [string] $PublishCommit = '',
        [string] $ScheduledAttemptStatus = '',
        [string] $RecoveryAttemptStatus = '',
        [string] $PreservedScheduledFailureReceiptPath = '',
        [string] $PreservedScheduledFailureReceiptSha256 = '',
        [string] $FinalizationReceiptPath = '',
        [string] $FinalizationReceiptSha256 = '',
        [string] $ExternalKind = '',
        [string] $ExternalSystem = '',
        [string] $ExternalStatus = '',
        [string] $ExternalStderr = '',
        [string] $ExternalDetail = '',
        [ValidateSet('', 'post-reporter', 'editor', 'deepdive', 'post-daily-quality', 'post-deepdive', 'generation-quality-repair')]
        [string] $ResumeStageCheckpoint = ''
    )
    $now = Get-Date -Format 'yyyy-MM-ddTHH:mm:ss.fffK'
    $scheduledFailureReceiptPath = ''
    if (
        $RunIntent -eq 'ScheduledProduction' -and
        (-not $SmokeTest) -and
        (-not $PreflightOnly) -and
        $ExitCode -gt 0
    ) {
        $scheduledFailureReceiptPath = Join-Path $ScheduledFailureReceiptRoot "$DateStamp-$RunId.json"
    }
    try {
        Invoke-WithRunnerStateLock {
            $prev = Read-RunnerStateOrNull -Path $StateFile
            $previousStateIsCorrupt = (
                $prev -and
                ($prev.PSObject.Properties.Name -contains '__corrupt') -and
                [bool]$prev.__corrupt
            )
            if ($previousStateIsCorrupt) {
                $payload = [ordered]@{
                    status = 'blocked_runner_state_corrupt'
                    message = "runner state corrupt: $($prev.corrupt_backup)"
                    exit_code = 125
                    updated_at = $now
                    heartbeat_at = $now
                    date = $DateStamp
                    run_intent = $RunIntent
                    run_id = $RunId
                    pid = $PID
                    repo_dir = $RepoDir
                    runner_path = $PSCommandPath
                    log_path = $LogPath
                    process_creation_time = $script:RunnerProcessCreationTime
                    command_line_fingerprint = $script:RunnerCommandLineFingerprint
                    first_terminal_wins = 'first-terminal-wins'
                }
                Write-RunnerStateAtomic -Path $StateFile -Payload $payload
                return
            }
            $previousRunId = [string](Get-RunnerStateProperty -State $prev -Name 'run_id' -Default '')
            $previousStatus = [string](Get-RunnerStateProperty -State $prev -Name 'status' -Default '')
            $previousStartedAt = [string](Get-RunnerStateProperty -State $prev -Name 'started_at' -Default '')
            $previousControlEvents = @(Get-RunnerStateProperty -State $prev -Name 'immutableControlEvents' -Default @())
            $previousPhase = [string](Get-RunnerStateProperty -State $prev -Name 'phase' -Default '')
            $previousStep = [string](Get-RunnerStateProperty -State $prev -Name 'step' -Default '')
            $previousResumeStage = [string](Get-RunnerStateProperty -State $prev -Name 'resumeStage' -Default '')
            if ($previousRunId -eq $RunId -and (Test-TerminalRunnerStatus -Status $previousStatus)) {
                # typed terminal state must replace generic error: Write-Log("ERROR:*") can run before
                # a typed status such as publish_failed / distribution_failed / blocked_external_readiness.
                $typedTerminalOverridesGenericError = (
                    $previousStatus -eq 'error' -and
                    @('blocked_external_readiness', 'publish_failed', 'distribution_failed', 'publish_complete') -contains [string]$Status
                )
                if ($typedTerminalOverridesGenericError) {
                    Write-Log "typed terminal state replaces generic error: $Status"
                } else {
                # first-terminal-wins: 同一 run_id の terminal state は running にも別 terminal にも戻さない。
                return
                }
            }
            if ($ResetStartedAt -and $previousRunId -and $previousRunId -ne $RunId) {
                $previous = "$StateFile.previous.$(Get-Date -Format 'yyyyMMddHHmmss').json"
                try { Copy-Item -LiteralPath $StateFile -Destination $previous -Force -ErrorAction SilentlyContinue } catch { }
            }

            $startedAt = $now
            if (-not $ResetStartedAt -and $previousStartedAt) {
                $startedAt = $previousStartedAt
            }
            $state = [ordered]@{
                status = $Status
                message = $Message
                exit_code = $ExitCode
                updated_at = $now
                heartbeat_at = $(if ($HeartbeatAt) { $HeartbeatAt } else { $now })
                date = $DateStamp
                run_intent = $RunIntent
                run_id = $RunId
                pid = $PID
                repo_dir = $RepoDir
                runner_path = $PSCommandPath
                log_path = $LogPath
                started_at = $startedAt
                process_creation_time = $script:RunnerProcessCreationTime
                command_line_fingerprint = $script:RunnerCommandLineFingerprint
                first_terminal_wins = 'first-terminal-wins'
            }
            # Completion/recovery verifiers must consume lineage emitted by the
            # producer runner.  They are not allowed to manufacture it later.
            $lineage = New-NewsGraspProducerLineage `
                -ArtifactRoot $RepoDir `
                -OpsRoot $OpsRepoRoot `
                -IssueDate $DateStamp `
                -RunIntent $RunIntent `
                -RunId $RunId
            foreach ($field in $lineage.Keys) { $state[$field] = $lineage[$field] }
            $dailyRootId = [string]$lineage.dailyRootId
            $rootOperationId = [string]$lineage.rootOperationId
            if ($Phase) { $state.phase = $Phase } elseif ($previousPhase) { $state.phase = $previousPhase }
            if ($Step) { $state.step = $Step } elseif ($previousStep) { $state.step = $previousStep }
            if ($ResumeStageCheckpoint) { $state.resumeStage = $ResumeStageCheckpoint } elseif ($previousResumeStage) { $state.resumeStage = $previousResumeStage }
            if ($script:HighCostAdmissionPath -and (Test-Path -LiteralPath $script:HighCostAdmissionPath -PathType Leaf)) {
                $state.highCostAdmissionPath = [System.IO.Path]::GetFullPath($script:HighCostAdmissionPath)
                $state.highCostAdmissionSha256 = Get-FileSha256Hex -Path $script:HighCostAdmissionPath
            }
            $state.highCostAttemptId = [string]$script:HighCostAttemptId
            $state.highCostBindingPath = [string]$env:NEWS_GRASP_HIGH_COST_BINDING_PATH
            $state.highCostBindingReceiptSha256 = [string]$env:NEWS_GRASP_HIGH_COST_BINDING_RECEIPT_SHA256
            $state.highCostParentAuthorityPath = [string]$script:HighCostParentAuthorityPath
            if ($script:HighCostParentAuthorityPath -and (Test-Path -LiteralPath $script:HighCostParentAuthorityPath -PathType Leaf)) {
                $state.highCostParentAuthoritySha256 = Get-FileSha256Hex -Path $script:HighCostParentAuthorityPath
            } else {
                $state.highCostParentAuthoritySha256 = [string]$script:HighCostParentAuthoritySha256
            }
            $state.e2eFinalAdmissionPath = [string]$script:E2EFinalAdmissionPath
            $state.e2eFinalRunnerArgumentsPath = [string]$script:E2EFinalRunnerArgumentsPath
            $state.e2eFinalReservationReceiptPath = [string]$script:E2EFinalReservationReceiptPath
            $state.e2eFinalClaimReceiptPath = [string]$script:E2EFinalClaimReceiptPath
            $state.highCostClaimWitness = [string]$script:HighCostClaimWitness
            if ($GateId) { $state.gate_id = $GateId }
            if ($Category) { $state.category = $Category }
            if ($Attempt -gt 0) { $state.attempt = $Attempt }
            if ($null -ne $ActiveJobs) { $state.active_jobs = $ActiveJobs }
            if ($DeadlineAt) { $state.deadline_at = $DeadlineAt }
            if ($PublishManifestPath) { $state.publish_manifest_path = $PublishManifestPath }
            if ($PublishCommit) { $state.publish_commit = $PublishCommit }
            if ($ScheduledAttemptStatus) { $state.scheduled_attempt_status = $ScheduledAttemptStatus }
            if ($RecoveryAttemptStatus) { $state.recovery_attempt_status = $RecoveryAttemptStatus }
            if ($PreservedScheduledFailureReceiptPath) { $state.scheduled_failure_receipt_path = $PreservedScheduledFailureReceiptPath }
            if ($PreservedScheduledFailureReceiptSha256) { $state.scheduled_failure_receipt_sha256 = $PreservedScheduledFailureReceiptSha256 }
            if ($FinalizationReceiptPath) { $state.recovery_finalization_receipt_path = $FinalizationReceiptPath }
            if ($FinalizationReceiptSha256) { $state.recovery_finalization_receipt_sha256 = $FinalizationReceiptSha256 }
            if ($scheduledFailureReceiptPath) { $state.scheduled_failure_receipt_path = $scheduledFailureReceiptPath }
            if ($previousControlEvents.Count -gt 0) { $state.immutableControlEvents = @($previousControlEvents) }
            if ($ExternalKind -or $ExternalSystem -or $ExternalStatus -or $ExternalStderr -or $ExternalDetail) {
                $state.external_readiness = [ordered]@{
                    kind = $ExternalKind
                    system = $ExternalSystem
                    status = $ExternalStatus
                    stderr = $ExternalStderr
                    detail = $ExternalDetail
                }
            }
            if ($Status -like 'operation_rejected_high_cost*') {
                $previousEvents = @($previousControlEvents)
                $previousEventHash = if ($previousEvents.Count -gt 0) {
                    [string]$previousEvents[-1].eventHash
                } else { '0' * 64 }
                $eventBody = [ordered]@{
                    eventSequence = $previousEvents.Count + 1
                    previousEventHash = $previousEventHash
                    dailyRootId = $dailyRootId
                    rootOperationId = $rootOperationId
                    runId = $RunId
                    runIntent = $RunIntent
                    eventType = 'scheduled_attempt_failed'
                    scheduledAttemptStatus = 'failed'
                    recoveryAttemptStatus = 'not_started'
                    productionPublicOutcomeStatus = 'unknown'
                    reasonCode = $Message.Split(';')[0]
                    observedAt = $now
                }
                $eventHash = Get-StringSha256Hex -Text ($eventBody | ConvertTo-Json -Depth 8 -Compress)
                $event = [ordered]@{}
                foreach ($key in $eventBody.Keys) { $event[$key] = $eventBody[$key] }
                $event.eventHash = $eventHash
                $state.eventSequence = $eventBody.eventSequence
                $state.previousEventHash = $previousEventHash
                $state.dailyRootId = $dailyRootId
                $state.rootOperationId = $eventBody.rootOperationId
                $state.preAttemptStatus = 'runner_reached'
                $state.scheduledAttemptStatus = 'failed'
                $state.recoveryAttemptStatus = 'not_started'
                $state.productionPublicOutcomeStatus = 'unknown'
                $state.continuationState = 'scheduled_recovery_required'
                $state.scheduledFailureRetained = $true
                $state.priorStatusRetained = [bool]$previousStatus
                $state.immutableControlEvents = @($previousEvents) + @($event)
                $receiptDir = Split-Path -Parent $StateFile
                $receiptPath = Join-Path $receiptDir ("scheduled-failure-receipt-{0}.json" -f $RunId)
                $receipt = [ordered]@{
                    schemaVersion = 'SCHEDULED_FAILURE_RECEIPT_V2'
                    productId = 'News-Grasp'
                    issueDate = $DateStamp
                    runIntent = $RunIntent
                    runId = $RunId
                    dailyRootId = $dailyRootId
                    rootOperationId = $eventBody.rootOperationId
                    eventSequence = $eventBody.eventSequence
                    eventHash = $eventHash
                    scheduledAttemptStatus = 'failed'
                    continuationState = 'scheduled_recovery_required'
                }
                $receipt.receiptSha256 = Get-StringSha256Hex -Text ($receipt | ConvertTo-Json -Depth 8 -Compress)
                Write-RunnerStateAtomic -Path $receiptPath -Payload $receipt
                $state.failureReceiptPath = $receiptPath
                $state.failureReceiptSha256 = $receipt.receiptSha256
            }
            Write-RunnerStateAtomic -Path $StateFile -Payload $state
            if ($scheduledFailureReceiptPath -and -not $script:ScheduledFailureTerminalInputPath) {
                $script:ScheduledFailureTerminalInputPath = New-ScheduledFailureTerminalInput `
                    -State $state `
                    -Status $Status `
                    -ExitCode $ExitCode
            }
        }
    } catch {
        if ([string]$_.Exception.Message -eq 'blocked_runner_state_lock_timeout') {
            $fallback = [ordered]@{
                status = 'blocked_runner_state_lock_timeout'
                message = 'runner state lock timeout'
                exit_code = 125
                updated_at = $now
                heartbeat_at = $now
                date = $DateStamp
                run_intent = $RunIntent
                run_id = $RunId
                pid = $PID
                repo_dir = $RepoDir
                runner_path = $PSCommandPath
                log_path = $LogPath
                process_creation_time = $script:RunnerProcessCreationTime
                command_line_fingerprint = $script:RunnerCommandLineFingerprint
            }
            try { Write-RunnerStateAtomic -Path $StateFile -Payload $fallback } catch { }
        } else {
            throw
        }
    }
    if ($scheduledFailureReceiptPath) {
        Invoke-ScheduledFailureTerminalizer -Status $Status -ExitCode $ExitCode -ReceiptPath $scheduledFailureReceiptPath
    }
}

function Update-RunnerProgress {
    param(
        [string] $Phase,
        [string] $Step,
        [string] $GateId = '',
        [string] $Category = '',
        [int] $Attempt = 0,
        [object] $ActiveJobs = $null,
        [string] $DeadlineAt = '',
        [string] $RepairSignature = '',
        [bool] $ArtifactProgress = $false,
        [ValidateSet('', 'post-reporter', 'editor', 'deepdive', 'post-daily-quality', 'post-deepdive', 'generation-quality-repair')]
        [string] $ResumeStageCheckpoint = ''
    )
    Assert-RecoveryOperationDeadline -Stage "progress:$Phase/$Step"
    Set-RunnerState -Status 'running' -Message $Step -ExitCode -1 -Phase $Phase -Step $Step -GateId $GateId -Category $Category -Attempt $Attempt -ActiveJobs $ActiveJobs -DeadlineAt $DeadlineAt -ResumeStageCheckpoint $ResumeStageCheckpoint
    try {
        $requiredArtifacts = @($DailyDigestArtifacts)
        $completedUnits = 0
        foreach ($artifact in $requiredArtifacts) {
            $full = Join-Path $RepoDir ([string]$artifact)
            if (Test-Path -LiteralPath $full) { $completedUnits++ }
        }
        $progressRecord = [ordered]@{
            timestamp = (Get-Date).ToString('yyyy-MM-ddTHH:mm:ss.fffK')
            flow = 'runner-progress'
            phase = $Phase
            step = $Step
            gate_id = $GateId
            category = $Category
            attempt = $Attempt
            elapsed_sec = [int]((Get-Date) - $script:RunnerStartedAt).TotalSeconds
            completed_units = $completedUnits
            required_units = @($requiredArtifacts).Count
            required_categories = @($script:RequiredCategoriesForSlo)
        }
        if ($RepairSignature) {
            $progressRecord.repair_signature = $RepairSignature
            $progressRecord.artifact_progress = [bool]$ArtifactProgress
        }
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $CodexUsageLog) | Out-Null
        Add-Content -Path $CodexUsageLog -Value ($progressRecord | ConvertTo-Json -Compress -Depth 6) -Encoding UTF8
    } catch {
        # progress logging must never hide the real runner failure
    }
}

function Exit-Runner {
    param(
        [string] $Status,
        [string] $Message,
        [int] $ExitCode,
        [string] $ExternalKind = '',
        [string] $ExternalSystem = '',
        [string] $ExternalStatus = '',
        [string] $ExternalStderr = '',
        [string] $ExternalDetail = ''
    )
    Set-RunnerState -Status $Status -Message $Message -ExitCode $ExitCode -ExternalKind $ExternalKind -ExternalSystem $ExternalSystem -ExternalStatus $ExternalStatus -ExternalStderr $ExternalStderr -ExternalDetail $ExternalDetail
    if ($RunIntent -eq 'ScheduledProduction' -and $ExitCode -gt 0) {
        $failureReceiptPath = Join-Path $ScheduledFailureReceiptRoot "$DateStamp-$RunId.json"
        Invoke-ScheduledFailureTerminalizer -Status $Status -ExitCode $ExitCode -ReceiptPath $failureReceiptPath
    }
    exit $ExitCode
}

function Invoke-ScheduledFailureTerminalizer {
    param(
        [string] $Status,
        [int] $ExitCode,
        [string] $ReceiptPath
    )
    if (
        $script:ScheduledFailureTerminalized -or
        $RunIntent -ne 'ScheduledProduction' -or
        $SmokeTest -or
        $PreflightOnly -or
        $ExitCode -le 0
    ) {
        return
    }
    $script:ScheduledFailureTerminalized = $true
    try {
        $broker = $HighCostBudgetToolPath
        $terminalInputPath = $script:ScheduledFailureTerminalInputPath
        if (
            (-not $broker) -or
            (-not (Test-Path -LiteralPath $broker -PathType Leaf)) -or
            (-not $terminalInputPath) -or
            (-not (Test-Path -LiteralPath $terminalInputPath -PathType Leaf))
        ) {
            throw 'scheduled failure terminalizer input missing'
        }
        $terminalInput = Get-Content -LiteralPath $terminalInputPath -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
        if (
            $terminalInput.schemaVersion -ne 'NEWS_GRASP_SCHEDULED_FAILURE_TERMINAL_INPUT_V1' -or
            $terminalInput.issueDate -ne $DateStamp -or
            $terminalInput.runId -ne $RunId -or
            $terminalInput.runIntent -ne $RunIntent -or
            $terminalInput.status -ne $Status -or
            [int]$terminalInput.exitCode -ne $ExitCode
        ) {
            throw 'scheduled failure terminalizer run binding invalid'
        }
        $stateSha256 = Get-FileSha256Hex -Path ([string]$terminalInput.stateEvidencePath)
        $logSha256 = Get-FileSha256Hex -Path ([string]$terminalInput.logEvidencePath)
        if (
            $stateSha256 -ne [string]$terminalInput.stateEvidenceSha256 -or
            $logSha256 -ne [string]$terminalInput.logEvidenceSha256
        ) {
            throw 'scheduled failure terminalizer evidence drift'
        }
        $taskActionSha256 = Get-ScheduledTaskActionSha256
        $runnerSha256 = Get-FileSha256Hex -Path $PSCommandPath
        if ((-not $stateSha256) -or (-not $logSha256) -or (-not $taskActionSha256) -or (-not $runnerSha256)) {
            throw 'scheduled failure terminalizer hash unavailable'
        }
        $failureStage = ([regex]::Replace([string]$Status, '[^A-Za-z0-9_.-]', '_')).Trim('_')
        if (-not $failureStage) { $failureStage = 'unknown_failure' }
        $receiptJson = (& $PyExe $broker 'record-news-grasp-failure' '--issue-date' $DateStamp '--run-id' $RunId '--last-task-result' ([string]$ExitCode) '--runner-state' $Status '--state-sha256' $stateSha256 '--log-sha256' $logSha256 '--task-action-sha256' $taskActionSha256 '--runner-sha256' $runnerSha256 '--failure-stage' $failureStage 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -ne 0) {
            throw "record-news-grasp-failure failed exit=$LASTEXITCODE detail=$receiptJson"
        }
        $receipt = $receiptJson | ConvertFrom-Json -ErrorAction Stop
        if ($receipt.schemaVersion -ne 'SCHEDULED_FAILURE_RECEIPT_V1' -or $receipt.issueDate -ne $DateStamp -or $receipt.runId -ne $RunId -or $receipt.scheduledAttemptStatus -ne 'failed') {
            throw 'scheduled failure receipt invalid'
        }
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $ReceiptPath) | Out-Null
        [System.IO.File]::WriteAllText($ReceiptPath, ($receiptJson + [Environment]::NewLine), [System.Text.UTF8Encoding]::new($false))
    } catch {
        try { Add-RunnerLogLine -Text "WARN: SCHEDULED_FAILURE_TERMINALIZER_FAILED reason=$($_.Exception.Message)" } catch { }
    }
}

function Write-Log {
    param([string] $Text)
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'
    $line = "[$ts] $Text"
    # console 出力 (Task Scheduler の標準出力は捨てられるので保険)
    Write-Host $line
    Add-RunnerLogLine -Text $line
    if ($Text -like 'ERROR:*') {
        if (-not $script:CodexUsageEndSnapshotWritten -and (Get-Command Write-CodexUsageWindowSnapshot -ErrorAction SilentlyContinue)) {
            $script:CodexUsageEndSnapshotWritten = $true
            Write-CodexUsageWindowSnapshot -Phase 'error'
        }
        Set-RunnerState -Status 'error' -Message $Text -ExitCode 1
    } elseif ($Text -eq 'news-grasp-runner.ps1 OK') {
        if ($RunIntent -eq 'ScheduledRecoveryFull') {
            Set-RunnerState -Status 'publish_complete' -Message $Text -ExitCode 0 `
                -PublishManifestPath $script:PublishCompleteManifestPath `
                -PublishCommit $script:PublishCompleteCommit `
                -ScheduledAttemptStatus 'failed_then_recovered' `
                -RecoveryAttemptStatus 'succeeded' `
                -PreservedScheduledFailureReceiptPath ([string]$script:ValidatedFinalizationReceipt.scheduledFailureReceiptPath) `
                -PreservedScheduledFailureReceiptSha256 ([string]$script:ValidatedFinalizationReceipt.scheduledFailureReceiptSha256) `
                -FinalizationReceiptPath ([string]$script:IssuedFinalizationReceiptPath) `
                -FinalizationReceiptSha256 ([string]$script:ValidatedFinalizationReceipt.receiptSha256)
        } else {
            Set-RunnerState -Status 'publish_complete' -Message $Text -ExitCode 0 -PublishManifestPath $script:PublishCompleteManifestPath -PublishCommit $script:PublishCompleteCommit -ScheduledAttemptStatus 'succeeded' -RecoveryAttemptStatus 'not_required'
        }
    } elseif ($Text -eq 'news-grasp-runner.ps1 SMOKE OK') {
        Set-RunnerState -Status 'smoke_ok' -Message $Text -ExitCode 0
    } elseif ($Text -eq 'news-grasp-runner.ps1 PRE DEEPDIVE E2E OK') {
        Set-RunnerState -Status 'pre_deepdive_e2e_ok' -Message $Text -ExitCode 0 -Phase 'pre-deepdive' -Step 'summary-reflection-and-daily-quality'
    } elseif ($Text -eq 'news-grasp-runner.ps1 PUBLISH DRY RUN OK') {
        Set-RunnerState -Status 'publish_dry_run_ok' -Message $Text -ExitCode 0
    }
}

function Invoke-NewsGraspCompletionGuard {
    param(
        [Parameter(Mandatory = $true)][string] $FinalizationReceiptPath,
        [string] $CandidateStatePath = ''
    )
    if ($RunIntent -ne 'ScheduledRecoveryFull') { return $true }
    $guardOutput = Join-Path $RepoDir "build\publish-complete\$DateStamp.automation-guard.json"
    $completionGuardTool = [string]$RecoveryRuntimeBinding.CompletionGuardToolPath
    & $PyExe '-I' '-B' $completionGuardTool `
        '--finalization-receipt' $FinalizationReceiptPath `
        '--artifact-root' $RepoDir `
        '--ops-root' $OpsRepoRoot `
        '--production-runtime-root' $TrustedProductionRuntimeRoot `
        '--live-bin-root' $TrustedRecoveryLiveBinRoot `
        '--runner-state' $StateFile `
        '--runner-script' $PSCommandPath `
        $(if ($CandidateStatePath) { '--candidate-state' }) `
        $(if ($CandidateStatePath) { $CandidateStatePath }) | ForEach-Object { Add-RunnerLogLine -Text ([string]$_) }
    $guardRc = $LASTEXITCODE
    if ($guardRc -ne 0 -or (-not (Test-Path -LiteralPath $guardOutput -PathType Leaf))) {
        Add-RunnerLogLine -Text "ERROR: NEWS_GRASP_640_COMPLETION_GUARD_FAILED rc=$guardRc output=$guardOutput"
        return $false
    }
    try {
        $guard = Get-Content -LiteralPath $guardOutput -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
        if ($guard.schemaVersion -ne 'NEWS_GRASP_640_COMPLETION_GUARD_V1' -or $guard.ok -ne $true) {
            throw 'completion guard is not Green'
        }
    } catch {
        Add-RunnerLogLine -Text "ERROR: NEWS_GRASP_640_COMPLETION_GUARD_INVALID reason=$($_.Exception.Message)"
        return $false
    }
    Add-RunnerLogLine -Text "NEWS_GRASP_640_COMPLETION_GUARD_OK output=$guardOutput"
    return $true
}

function New-NewsGraspFinalizationCandidateState {
    param(
        [Parameter(Mandatory = $true)][string] $FinalizationReceiptPath,
        [Parameter(Mandatory = $true)][string] $ManifestPath,
        [Parameter(Mandatory = $true)][string] $PublishCommit
    )
    $before = Read-RunnerStateOrNull -Path $StateFile
    $beforeCorrupt = (
        $null -ne $before -and
        ($before.PSObject.Properties.Name -contains '__corrupt') -and
        [bool]$before.__corrupt
    )
    if ($null -eq $before -or $beforeCorrupt) {
        Add-RunnerLogLine -Text 'ERROR: FINALIZATION_CANDIDATE_BEFORE_STATE_INVALID'
        return ''
    }
    if ([string]$before.date -ne $DateStamp) {
        Add-RunnerLogLine -Text 'ERROR: FINALIZATION_CANDIDATE_DATE_MISMATCH'
        return ''
    }
    $candidatePath = Join-Path $TrustedRecoveryLiveBinRoot ("news-grasp-runner-state.$DateStamp.$RunId.candidate.json")
    $candidate = $before | ConvertTo-Json -Depth 20 | ConvertFrom-Json
    $candidate.status = 'publish_complete'
    $candidate.message = 'verified recovery publish complete'
    $candidate.exit_code = 0
    $candidate.updated_at = (Get-Date).ToString('o')
    $candidate | Add-Member -NotePropertyName 'publish_manifest_path' -NotePropertyValue ([IO.Path]::GetFullPath($ManifestPath)) -Force
    $candidate | Add-Member -NotePropertyName 'publish_commit' -NotePropertyValue $PublishCommit -Force
    $candidate | Add-Member -NotePropertyName 'scheduled_attempt_status' -NotePropertyValue 'failed_then_recovered' -Force
    $candidate | Add-Member -NotePropertyName 'recovery_attempt_status' -NotePropertyValue 'succeeded' -Force
    $candidate | Add-Member -NotePropertyName 'recovery_finalization_receipt_path' -NotePropertyValue ([IO.Path]::GetFullPath($FinalizationReceiptPath)) -Force
    $candidate | Add-Member -NotePropertyName 'recovery_finalization_receipt_sha256' -NotePropertyValue ([string]$script:ValidatedFinalizationReceipt.receiptSha256) -Force
    $candidate | Add-Member -NotePropertyName 'scheduled_failure_receipt_path' -NotePropertyValue ([string]$script:ValidatedFinalizationReceipt.scheduledFailureReceiptPath) -Force
    $candidate | Add-Member -NotePropertyName 'scheduled_failure_receipt_sha256' -NotePropertyValue ([string]$script:ValidatedFinalizationReceipt.scheduledFailureReceiptSha256) -Force
    $candidate | Add-Member -NotePropertyName 'finalization_candidate' -NotePropertyValue $true -Force
    try {
        Write-RunnerStateAtomic -Path $candidatePath -Payload $candidate
        return $candidatePath
    } catch {
        Add-RunnerLogLine -Text "ERROR: FINALIZATION_CANDIDATE_WRITE_FAILED reason=$($_.Exception.Message)"
        return ''
    }
}

function Commit-NewsGraspFinalizationCandidate {
    param([Parameter(Mandatory = $true)][string] $CandidateStatePath)
    try {
        if (-not (Test-Path -LiteralPath $CandidateStatePath -PathType Leaf)) { throw 'candidate missing' }
        $backup = "$StateFile.finalization-before.bak"
        if (Test-Path -LiteralPath $StateFile -PathType Leaf) {
            [IO.File]::Replace($CandidateStatePath, $StateFile, $backup, $true)
        } else {
            [IO.File]::Move($CandidateStatePath, $StateFile)
        }
        $committed = Read-RunnerStateOrNull -Path $StateFile
        $committedCorrupt = (
            $null -ne $committed -and
            ($committed.PSObject.Properties.Name -contains '__corrupt') -and
            [bool]$committed.__corrupt
        )
        if ($null -eq $committed -or $committedCorrupt -or $committed.status -ne 'publish_complete') {
            throw 'committed candidate invalid'
        }
        return $true
    } catch {
        Add-RunnerLogLine -Text "ERROR: FINALIZATION_STATE_COMMIT_FAILED reason=$($_.Exception.Message)"
        return $false
    }
}

function New-NewsGraspFinalizationReceipt {
    param([Parameter(Mandatory = $true)][string] $ManifestPath)
    if ($RunIntent -ne 'ScheduledRecoveryFull' -or (-not $RecoveryExecutionReceiptPath)) {
        return ''
    }
    $receiptOutput = Join-Path $RepoDir "build\publish-complete\$DateStamp.finalization-receipt.json"
    $recoveryReceiptTool = [string]$RecoveryRuntimeBinding.ReceiptToolPath
    $receiptJson = (& $PyExe '-I' '-S' '-B' $recoveryReceiptTool `
        'issue-finalization' `
        '--receipt' $RecoveryExecutionReceiptPath `
        '--issue-date' $DateStamp `
        '--artifact-root' $RepoDir `
        '--ops-root' $OpsRepoRoot `
        '--production-runtime-root' $TrustedProductionRuntimeRoot `
        '--live-bin-root' $TrustedRecoveryLiveBinRoot `
        '--runner-state' $StateFile `
        '--runner-script' $PSCommandPath `
        '--manifest' $ManifestPath `
        '--output' $receiptOutput 2>&1 | Out-String).Trim()
    $receiptRc = $LASTEXITCODE
    if ($receiptRc -ne 0 -or (-not (Test-Path -LiteralPath $receiptOutput -PathType Leaf))) {
        Add-RunnerLogLine -Text "ERROR: RECOVERY_FINALIZATION_RECEIPT_ISSUE_FAILED rc=$receiptRc detail=$receiptJson"
        return ''
    }
    try {
        $script:ValidatedFinalizationReceipt = $receiptJson | ConvertFrom-Json -ErrorAction Stop
        $script:IssuedFinalizationReceiptPath = $receiptOutput
    } catch {
        Add-RunnerLogLine -Text 'ERROR: RECOVERY_FINALIZATION_RECEIPT_OUTPUT_INVALID'
        return ''
    }
    return $receiptOutput
}

function Test-PreRunBootstrapSmokeMarker {
    if (-not (Test-Path -LiteralPath $BootstrapSmokeStateFile)) {
        return $false
    }
    try {
        $state = Get-Content -LiteralPath $BootstrapSmokeStateFile -Raw -Encoding UTF8 | ConvertFrom-Json
        if ([string]$state.status -ne 'smoke_ok') {
            return $false
        }
        $item = Get-Item -LiteralPath $BootstrapSmokeStateFile -ErrorAction Stop
        $updatedAt = $null
        if ($state.updated_at) {
            try { $updatedAt = [datetime]::Parse([string]$state.updated_at) } catch { $updatedAt = $null }
        }
        if (-not $updatedAt) {
            $updatedAt = $item.LastWriteTime
        }
        $now = Get-Date
        $markerMinutes = $updatedAt.Hour * 60 + $updatedAt.Minute
        $mtimeMinutes = $item.LastWriteTime.Hour * 60 + $item.LastWriteTime.Minute
        return (
            $updatedAt.ToString('yyyy-MM-dd') -eq $DateStamp -and
            $item.LastWriteTime.ToString('yyyy-MM-dd') -eq $DateStamp -and
            $markerMinutes -ge $BootstrapSmokeEarliestMinutes -and
            $mtimeMinutes -ge $BootstrapSmokeEarliestMinutes -and
            ([int]($now - $updatedAt).TotalMinutes) -le $BootstrapSmokeFreshnessMinutes -and
            ([int]($now - $item.LastWriteTime).TotalMinutes) -le $BootstrapSmokeFreshnessMinutes
        )
    } catch {
        Write-Log "WARN: pre-run bootstrap marker unreadable path=$BootstrapSmokeStateFile reason=$($_.Exception.Message)"
        return $false
    }
}

function Test-NormalDailyPublishRun {
    return (
        $RunIntent -eq 'ScheduledProduction' -and
        (-not $SmokeTest) -and
        (-not $PreflightOnly) -and
        (-not $RecoverOnly) -and
        (-not $NoPublish) -and
        (-not $NoPush) -and
        (-not $Stage2EditorSmokeOnly) -and
        (-not $StopAfterEditorStart) -and
        (-not $StopBeforeDeepDive) -and
        (-not $ResumeFromStage)
    )
}

function Assert-PreRunBootstrapInterlock {
    param([switch] $ForceRepair)

    if (-not (Test-NormalDailyPublishRun)) {
        Write-Log 'pre-run bootstrap interlock skipped: not a normal daily publish run'
        return
    }
    if ((-not $ForceRepair) -and (Test-PreRunBootstrapSmokeMarker)) {
        Write-Log "pre-run bootstrap interlock OK: marker=$BootstrapSmokeStateFile"
        return
    }
    $bootstrapPath = Join-Path $LiveBinDir 'news-grasp-bootstrap.ps1'
    if (-not (Test-Path -LiteralPath $bootstrapPath)) {
        Exit-Runner -Status 'blocked_startup_self_repair_failed' -Message "pre-run bootstrap missing: $bootstrapPath" -ExitCode 72
    }
    $reason = if ($ForceRepair) { 'forced repo/live drift repair' } else { 'marker missing or stale' }
    Write-Log "pre-run bootstrap interlock $reason; running bootstrap smoke path=$bootstrapPath"
    $bootstrapArgs = @(
        '-NoProfile',
        '-NonInteractive',
        '-ExecutionPolicy',
        'Bypass',
        '-File',
        $bootstrapPath,
        '-Start',
        '-SmokeTest',
        '-PollSeconds',
        '1',
        '-TimeoutMinutes',
        '2',
        '-StateFile',
        $BootstrapSmokeStateFile,
        '-LogDir',
        $BootstrapSmokeLogDir,
        '-RepoDir',
        $RepoDir,
        '-BinDir',
        $LiveBinDir,
        '-PythonExe',
        $PyExe,
        '-HighCostBindingPath',
        $HighCostBindingPath,
        '-HighCostBindingReceiptSha256',
        $HighCostBindingReceiptSha256
    )
    try {
        $proc = Start-Process -FilePath 'powershell' -ArgumentList $bootstrapArgs -WindowStyle Hidden -PassThru -Wait
    } catch {
        Exit-Runner -Status 'blocked_startup_self_repair_failed' -Message "pre-run bootstrap launch failed: $($_.Exception.Message)" -ExitCode 72
    }
    if ($proc.ExitCode -ne 0) {
        Exit-Runner -Status 'blocked_startup_self_repair_failed' -Message "pre-run bootstrap failed exit=$($proc.ExitCode)" -ExitCode 72
    }
    if (-not (Test-PreRunBootstrapSmokeMarker)) {
        Exit-Runner -Status 'blocked_startup_self_repair_failed' -Message 'pre-run bootstrap finished without fresh smoke_ok marker' -ExitCode 72
    }
    Write-Log "pre-run bootstrap interlock repaired startup path marker=$BootstrapSmokeStateFile"
}

function Convert-JsonStringArrayToStringList {
    param([string] $JsonText)

    $parsed = $JsonText | ConvertFrom-Json
    $items = New-Object System.Collections.Generic.List[string]
    foreach ($item in $parsed) {
        if ($null -eq $item) {
            continue
        }
        if (($item -is [System.Array]) -or (($item -is [System.Collections.IEnumerable]) -and -not ($item -is [string]))) {
            foreach ($nestedItem in $item) {
                if ($null -ne $nestedItem) {
                    $items.Add([string] $nestedItem)
                }
            }
            continue
        }
        $items.Add([string] $item)
    }
    return @($items.ToArray())
}

function New-ExternalReadinessResult {
    param(
        [bool] $Ok,
        [string] $Kind = '',
        [string] $System = '',
        [string] $Status = '',
        [string] $Stderr = '',
        [string] $Detail = ''
    )
    return [pscustomobject]@{
        ok = $Ok
        kind = $Kind
        system = $System
        status = $Status
        stderr = $Stderr
        detail = $Detail
    }
}

function Stop-ExternalReadiness {
    param(
        [Parameter(Mandatory=$true)][string] $Reason,
        [int] $ExitCode = 71,
        [Parameter(Mandatory=$true)][string] $Kind,
        [Parameter(Mandatory=$true)][string] $System,
        [string] $ExternalStatus = '',
        [string] $ExternalStderr = '',
        [string] $ExternalDetail = ''
    )
    Write-Log "ERROR: external readiness blocked: $Reason"
    Exit-Runner -Status 'blocked_external_readiness' -Message $Reason -ExitCode $ExitCode -ExternalKind $Kind -ExternalSystem $System -ExternalStatus $ExternalStatus -ExternalStderr $ExternalStderr -ExternalDetail $ExternalDetail
}

function Test-WorkspaceWriteReadiness {
    $dirs = @('build', 'tmp', 'data', 'digest', 'docs')
    foreach ($rel in $dirs) {
        $dir = Join-Path $RepoDir $rel
        try {
            if (-not (Test-Path -LiteralPath $dir)) {
                New-Item -ItemType Directory -Path $dir -Force | Out-Null
            }
            $probe = Join-Path $dir (".news-grasp-write-probe-$DateStamp-" + [guid]::NewGuid().ToString('N') + ".tmp")
            $renamed = "$probe.renamed"
            Set-Content -LiteralPath $probe -Value "probe $DateStamp" -Encoding UTF8
            Move-Item -LiteralPath $probe -Destination $renamed -Force
            Remove-Item -LiteralPath $renamed -Force
        } catch {
            Write-Log "workspace write readiness failed path=$rel reason=$($_.Exception.Message)"
            return $false
        }
    }
    return $true
}

function Test-PublishExternalReadiness {
    try {
        Invoke-Logged { & $GitExe @GitSafeArgs -C $RepoDir ls-remote --exit-code origin main }
        if ($LASTEXITCODE -ne 0) {
            Write-Log "publish external readiness failed: git ls-remote origin main rc=$LASTEXITCODE"
            return New-ExternalReadinessResult -Ok $false -Kind 'github_remote' -System 'github' -Status "rc=$LASTEXITCODE" -Detail 'git ls-remote origin main'
        }
        if (-not $NoPush) {
            Invoke-Logged { & $GitExe @GitSafeArgs -C $RepoDir push --dry-run origin HEAD:main }
            if ($LASTEXITCODE -ne 0) {
                Write-Log "publish external readiness failed: git push --dry-run origin HEAD:main rc=$LASTEXITCODE"
                return New-ExternalReadinessResult -Ok $false -Kind 'git_push_auth' -System 'github' -Status "rc=$LASTEXITCODE" -Detail 'git push --dry-run origin HEAD:main'
            }
        }
        return New-ExternalReadinessResult -Ok $true -Kind 'ok' -System 'github'
    } catch {
        Write-Log "publish external readiness failed: $($_.Exception.Message)"
        return New-ExternalReadinessResult -Ok $false -Kind 'github_exception' -System 'github' -Status 'exception' -Stderr $_.Exception.Message -Detail 'publish external readiness exception'
    }
}

function Should-SendNormalBatchNotification {
    # recoveryも公開bundleを閉じる必要がある。NoPushだけは明示的test profile。
    return ((-not $NoPush) -and $NormalPublishVerified)
}

function Write-CodexUsageWindowSnapshot {
    param([string] $Phase)

    try {
        $authPath = Join-Path $env:USERPROFILE '.codex\auth.json'
        if (-not (Test-Path -LiteralPath $authPath)) {
            Write-Log "WARN: usage window snapshot failed phase=$Phase reason=auth_json_missing"
            return
        }

        $auth = Get-Content -LiteralPath $authPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $accessToken = $auth.tokens.access_token
        if (-not $accessToken) {
            Write-Log "WARN: usage window snapshot failed phase=$Phase reason=access_token_missing"
            return
        }

        $headers = @{
            Authorization = "Bearer $accessToken"
            'User-Agent' = 'News-Grasp-Runner-UsageSnapshot/1.0'
        }
        $usage = $null
        $source = ''
        foreach ($uri in @(
            'https://chatgpt.com/backend-api/codex/usage',
            'https://chatgpt.com/backend-api/wham/usage'
        )) {
            try {
                $usage = Invoke-RestMethod -Uri $uri -Headers $headers -Method Get -TimeoutSec 20
                $source = $uri
                break
            } catch {
                $usage = $null
            }
        }

        if ($null -eq $usage) {
            Write-Log "WARN: usage window snapshot failed phase=$Phase reason=usage_endpoint_unavailable"
            return
        }

        $primary = $usage.rate_limit.primary_window
        $secondary = $usage.rate_limit.secondary_window
        $record = [ordered]@{
            timestamp = (Get-Date -Format 'yyyy-MM-ddTHH:mm:ss.fffK')
            date = $DateStamp
            phase = $Phase
            plan_type = $usage.plan_type
            source = $source
            allowed = [bool]$usage.rate_limit.allowed
            limit_reached = [bool]$usage.rate_limit.limit_reached
            primary_window = [ordered]@{
                used_percent = $primary.used_percent
                limit_window_seconds = $primary.limit_window_seconds
                reset_after_seconds = $primary.reset_after_seconds
                reset_at = $primary.reset_at
            }
            secondary_window = [ordered]@{
                used_percent = $secondary.used_percent
                limit_window_seconds = $secondary.limit_window_seconds
                reset_after_seconds = $secondary.reset_after_seconds
                reset_at = $secondary.reset_at
            }
        }

        $parent = Split-Path -Parent $CodexUsageWindowLog
        if ($parent -and -not (Test-Path -LiteralPath $parent)) {
            New-Item -ItemType Directory -Path $parent -Force | Out-Null
        }
        Add-Content -Path $CodexUsageWindowLog -Value ($record | ConvertTo-Json -Depth 6 -Compress) -Encoding UTF8
        Write-Log "usage window snapshot phase=$Phase primary_used_percent=$($primary.used_percent) secondary_used_percent=$($secondary.used_percent) log=$CodexUsageWindowLog"
    } catch {
        Write-Log "WARN: usage window snapshot failed phase=$Phase reason=$($_.Exception.Message)"
    }
}

function Get-FileSha256Hex {
    param([string] $Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return ''
    }
    try {
        return Get-NewsGraspFileSha256Hex -Path $Path
    } catch {
        Write-Log "ERROR: sha256 calculation failed path=$Path reason=$($_.Exception.Message)"
        return ''
    }
}

function Get-ScheduledTaskActionSummary {
    try {
        $task = Get-ScheduledTask -TaskName 'News-Grasp Runner' -ErrorAction Stop
        return (@($task.Actions) | ForEach-Object {
            ([string]$_.Execute + ' ' + [string]$_.Arguments).Trim()
        }) -join ' ; '
    } catch {
        return "unavailable: $($_.Exception.Message)"
    }
}

function Get-RunnerScriptArguments {
    $runnerArgs = @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', $PSCommandPath)
    if ($SmokeTest) { $runnerArgs += '-SmokeTest' }
    if ($PreflightOnly) { $runnerArgs += '-PreflightOnly' }
    if ($RecoverOnly) { $runnerArgs += '-RecoverOnly' }
    if ($RunIntent -ne 'ScheduledProduction') { $runnerArgs += @('-RunIntent', $RunIntent) }
    if ($NoPush) { $runnerArgs += '-NoPush' }
    if ($NoPublish) { $runnerArgs += '-NoPublish' }
    if ($UseCodex) { $runnerArgs += '-UseCodex' }
    if ($IdleTimeoutSec -ne 900) { $runnerArgs += @('-IdleTimeoutSec', [string]$IdleTimeoutSec) }
    if ($Stage2EditorSmokeOnly) { $runnerArgs += '-Stage2EditorSmokeOnly' }
    if ($StopAfterEditorStart) { $runnerArgs += '-StopAfterEditorStart' }
    if ($StopBeforeDeepDive) { $runnerArgs += '-StopBeforeDeepDive' }
    if ($ResumeFromStage) { $runnerArgs += @('-ResumeFromStage', $ResumeFromStage) }
    if ($RepoDirOverride) { $runnerArgs += @('-RepoDirOverride', $RepoDirOverride) }
    if ($OpsRepoRootOverride) { $runnerArgs += @('-OpsRepoRootOverride', $OpsRepoRootOverride) }
    if ($CodexWrapperOverride) { $runnerArgs += @('-CodexWrapperOverride', $CodexWrapperOverride) }
    if ($CodexExeOverride) { $runnerArgs += @('-CodexExeOverride', $CodexExeOverride) }
    if ($PyExeOverride) { $runnerArgs += @('-PyExeOverride', $PyExeOverride) }
    if ($DateStampOverride) { $runnerArgs += @('-DateStampOverride', $DateStampOverride) }
    if ($LogDirOverride) { $runnerArgs += @('-LogDirOverride', $LogDirOverride) }
    if ($StateFileOverride) { $runnerArgs += @('-StateFileOverride', $StateFileOverride) }
    if ($ExternalHealthAuthorityPathOverride) {
        $runnerArgs += @(
            '-ExternalHealthAuthorityPathOverride', $ExternalHealthAuthorityPathOverride,
            '-ExternalHealthAuthorityExpectedSha256', $ExternalHealthAuthorityExpectedSha256
        )
    }
    if ($ScheduledAuthorityEvidencePath) { $runnerArgs += @('-ScheduledAuthorityEvidencePath', $ScheduledAuthorityEvidencePath) }
    if ($RecoveryExecutionReceiptPath) { $runnerArgs += @('-RecoveryExecutionReceiptPath', $RecoveryExecutionReceiptPath) }
    if ($RecoveryFinalizationReceiptPath) { $runnerArgs += @('-RecoveryFinalizationReceiptPath', $RecoveryFinalizationReceiptPath) }
    if ($PublishVerifyWaitSec -ne 600) { $runnerArgs += @('-PublishVerifyWaitSec', [string]$PublishVerifyWaitSec) }
    if ($PublishVerifyPollSec -ne 30) { $runnerArgs += @('-PublishVerifyPollSec', [string]$PublishVerifyPollSec) }
    return $runnerArgs
}

function Invoke-RunnerBinarySyncApprovalBlock {
    param(
        [string] $LiveRunnerSha,
        [string] $RepoRunnerSha
    )
    $backupDir = Join-Path $RepoDir "build\live-runner-backups\$DateStamp"
    $message = "runner binary drift requires backup + explicit approval + rollback plan before live overwrite (live=$LiveRunnerSha repo=$RepoRunnerSha backup_dir=$backupDir)"
    Write-Log "ERROR: $message"
    Write-Log 'Live runner sync is intentionally blocked here. Prepare backup, get explicit user approval, then run scripts/ops/install-news-grasp-ops.ps1 with rollback evidence.'
    Exit-Runner -Status 'blocked_runner_sync_approval_required' -Message $message -ExitCode 72
}

function Invoke-SyncedRunnerReexec {
    param([string] $Reason)

    if ($env:NEWS_GRASP_RUNNER_SYNC_REEXEC -eq '1') {
        Exit-Runner -Status 'blocked_startup_self_repair_failed' -Message "runner binary drift remains after synced runner reexec reason=$Reason" -ExitCode 72
    }
    $runnerArgs = Get-RunnerScriptArguments
    Write-Log "runner binary drift repaired; relaunching synced runner reason=$Reason path=$PSCommandPath"
    $previousSyncReexec = $env:NEWS_GRASP_RUNNER_SYNC_REEXEC
    try {
        $env:NEWS_GRASP_RUNNER_SYNC_REEXEC = '1'
        $proc = Start-Process -FilePath 'powershell' -ArgumentList $runnerArgs -WindowStyle Hidden -PassThru -Wait
        $exitCode = [int]$proc.ExitCode
    } catch {
        Exit-Runner -Status 'blocked_startup_self_repair_failed' -Message "synced runner reexec failed: $($_.Exception.Message)" -ExitCode 72
    } finally {
        if ($null -eq $previousSyncReexec) {
            Remove-Item Env:\NEWS_GRASP_RUNNER_SYNC_REEXEC -ErrorAction SilentlyContinue
        } else {
            $env:NEWS_GRASP_RUNNER_SYNC_REEXEC = $previousSyncReexec
        }
    }
    Write-Log "synced runner reexec completed exit=$exitCode"
    exit $exitCode
}

function Assert-RunnerBinaryInSync {
    if ($RepoDirOverride) {
        Write-Log 'runner sync check skipped: RepoDirOverride is set'
        return
    }
    if (-not (Test-Path -LiteralPath $RepoManagedRunner)) {
        Write-Log "ERROR: repo-managed runner missing: $RepoManagedRunner"
        Set-RunnerState -Status 'failed' -Message 'repo-managed runner missing' -ExitCode 1
        exit 1
    }
    $liveRunnerSha = Get-FileSha256Hex -Path $PSCommandPath
    $repoRunnerSha = Get-FileSha256Hex -Path $RepoManagedRunner
    $repoWatcherSha = Get-FileSha256Hex -Path $RepoManagedWatcher
    $taskAction = Get-ScheduledTaskActionSummary
    Write-Log "runner launch snapshot repo_dir=$RepoDir repo_head=$(& $GitExe @GitSafeArgs -C $RepoDir rev-parse --short HEAD 2>$null) live_runner_sha=$liveRunnerSha repo_runner_sha=$repoRunnerSha repo_watcher_sha=$repoWatcherSha task_action=$taskAction"
    if ($liveRunnerSha -ne $repoRunnerSha) {
        if (Test-NormalDailyPublishRun) {
            if ($env:NEWS_GRASP_RUNNER_SYNC_REEXEC -eq '1') {
                Exit-Runner -Status 'blocked_startup_self_repair_failed' -Message "runner binary drift remains after reexec live=$liveRunnerSha repo=$repoRunnerSha" -ExitCode 72
            }
            Write-Log "runner binary drift detected during normal daily run; forcing bootstrap self-repair before generation live=$liveRunnerSha repo=$repoRunnerSha"
            Assert-PreRunBootstrapInterlock -ForceRepair
            $liveRunnerShaAfterRepair = Get-FileSha256Hex -Path $PSCommandPath
            $repoRunnerShaAfterRepair = Get-FileSha256Hex -Path $RepoManagedRunner
            if (($liveRunnerShaAfterRepair) -and ($liveRunnerShaAfterRepair -eq $repoRunnerShaAfterRepair)) {
                Invoke-SyncedRunnerReexec -Reason "repo/live runner drift repaired old_live=$liveRunnerSha repo=$repoRunnerShaAfterRepair"
            }
            Exit-Runner -Status 'blocked_startup_self_repair_failed' -Message "runner binary drift self-repair failed live=$liveRunnerShaAfterRepair repo=$repoRunnerShaAfterRepair" -ExitCode 72
        }
        Invoke-RunnerBinarySyncApprovalBlock -LiveRunnerSha $liveRunnerSha -RepoRunnerSha $repoRunnerSha
    }
}

function Invoke-Logged {
    # 外部コマンドを呼び stdout/stderr を pipe 経由で ToString() → UTF-8 で log に append。
    # PS 5.1 の `*>> $LogPath` 直接 redirect は native command の stderr で NativeCommandError
    # 例外と UTF-16 で append される副作用があるため、明示的に pipe で書き出す。
    # 引数の Block で外部コマンドを呼び出すだけにする (sub-process 化はしない)。
    param([scriptblock] $Block)
    Assert-RecoveryOperationDeadline -Stage 'external-command:before'
    & $Block 2>&1 | ForEach-Object {
        Add-RunnerLogLine -Text $_.ToString()
    }
    Assert-RecoveryOperationDeadline -Stage 'external-command:after'
}

function Invoke-LoggedCapture {
    param(
        [scriptblock] $Block,
        [string] $CapturePath
    )
    Assert-RecoveryOperationDeadline -Stage 'external-command-capture:before'
    if (Test-Path $CapturePath) { Remove-Item -LiteralPath $CapturePath -Force -ErrorAction SilentlyContinue }
    & $Block 2>&1 | ForEach-Object {
        $line = $_.ToString()
        Add-RunnerLogLine -Text $line
        Add-Content -Path $CapturePath -Value $line -Encoding UTF8
    }
    Assert-RecoveryOperationDeadline -Stage 'external-command-capture:after'
}

function Get-ScheduledTaskActionSha256 {
    return Get-StringSha256Hex -Text ((Get-ScheduledTaskActionSummary).Trim().ToLowerInvariant())
}

function Invoke-GitAddWithIndexLockRetry {
    param(
        [string] $Label,
        [string[]] $Pathspecs,
        [int] $MaxAttempts = 5
    )
    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        Invoke-Logged { & $GitExe @GitSafeArgs -C $RepoDir add @Pathspecs }
        $addRc = $LASTEXITCODE
        if ($addRc -eq 0) {
            return $true
        }
        if ($addRc -eq 128 -and $attempt -lt $MaxAttempts) {
            $lockPath = Join-Path $RepoDir '.git\index.lock'
            $lockState = 'absent'
            if (Test-Path -LiteralPath $lockPath) {
                try {
                    $lock = Get-Item -LiteralPath $lockPath -ErrorAction Stop
                    $lockState = "present last_write=$($lock.LastWriteTime.ToString('yyyy-MM-ddTHH:mm:ssK')) length=$($lock.Length)"
                    $lockAge = (Get-Date) - $lock.LastWriteTime
                    if ($lock.Length -eq 0 -and $lockAge.TotalSeconds -ge 60) {
                        Remove-Item -LiteralPath $lockPath -Force -ErrorAction Stop
                        Write-Log "git add $Label stale empty index.lock removed before retry age_seconds=$([int]$lockAge.TotalSeconds)"
                    }
                } catch {
                    $lockState = "present unreadable=$($_.Exception.Message)"
                }
            }
            Write-Log "git add $Label retry after rc=128 attempt=$attempt/$MaxAttempts index_lock=$lockState"
            Start-Sleep -Seconds 3
            continue
        }
        Write-Log "git add $Label failed after attempt=$attempt rc=$addRc"
        return $false
    }
    Write-Log "git add $Label failed after $MaxAttempts attempts"
    return $false
}

function Invoke-PythonStdoutFileUtf8 {
    param(
        [string[]] $PythonArgs,
        [string] $StdoutPath
    )
    $escapedArgs = @()
    foreach ($a in $PythonArgs) {
        if ($a -notmatch '[\s"]') {
            $escapedArgs += $a
            continue
        }
        $escapedArgs += ('"' + ($a -replace '(\\*)"', '$1$1\"' -replace '(\\+)$', '$1$1') + '"')
    }
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $PyExe
    $psi.Arguments = ($escapedArgs -join ' ')
    $psi.WorkingDirectory = $RepoDir
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    try {
        $psi.StandardOutputEncoding = [System.Text.UTF8Encoding]::new($false)
        $psi.StandardErrorEncoding = [System.Text.UTF8Encoding]::new($false)
    } catch { }
    $proc = [System.Diagnostics.Process]::Start($psi)
    $stdout = $proc.StandardOutput.ReadToEnd()
    $stderr = $proc.StandardError.ReadToEnd()
    $proc.WaitForExit()
    [System.IO.File]::WriteAllText($StdoutPath, $stdout, [System.Text.UTF8Encoding]::new($false))
    if ($stderr) {
        foreach ($line in $stderr -split "\r?\n") {
            if ($line) { Add-RunnerLogLine -Text $line }
        }
    }
    return $proc.ExitCode
}

function Get-ModelPolicyValue {
    param(
        [string] $Role,
        [string] $Key
    )
    Push-Location $RepoDir
    try {
        $code = "import json; from tools.model_policy import DEFAULT_MODEL_POLICY; print(DEFAULT_MODEL_POLICY['$Role']['$Key'])"
        $value = & $PyExe -c $code
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($value)) {
            throw "model_policy.py lookup failed role=$Role key=$Key rc=$LASTEXITCODE"
        }
        return [string]$value
    } finally {
        Pop-Location
    }
}

function Select-NewsroomEditorModel {
    param(
        [int] $GateFailCount,
        [int] $DedupConflictCount,
        [bool] $AppendMismatch,
        [int] $SummaryQualityScore,
        [int] $DeepDiveThemeCount
    )
    $appendMismatchLiteral = if ($AppendMismatch) { 'True' } else { 'False' }
    Push-Location $RepoDir
    try {
        $code = @"
from tools.model_policy import select_newsroom_editor_model
print(select_newsroom_editor_model(
    gate_fail_count=$GateFailCount,
    dedup_conflict_count=$DedupConflictCount,
    append_mismatch=$appendMismatchLiteral,
    summary_quality_score=$SummaryQualityScore,
    deepdive_theme_count=$DeepDiveThemeCount,
))
"@
        $value = & $PyExe -c $code
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($value)) {
            throw "model_policy.py newsroom editor selection failed rc=$LASTEXITCODE"
        }
        return [string]$value
    } finally {
        Pop-Location
    }
}

function Select-RepairModel {
    param(
        [int] $IssueCount,
        [bool] $PreviousClassifyFailed,
        [bool] $ScopeAmbiguous,
        [bool] $MissingArtifactGeneration,
        [bool] $CompoundGateFailure
    )
    $previousClassifyFailedLiteral = if ($PreviousClassifyFailed) { 'True' } else { 'False' }
    $scopeAmbiguousLiteral = if ($ScopeAmbiguous) { 'True' } else { 'False' }
    $missingArtifactGenerationLiteral = if ($MissingArtifactGeneration) { 'True' } else { 'False' }
    $compoundGateFailureLiteral = if ($CompoundGateFailure) { 'True' } else { 'False' }
    Push-Location $RepoDir
    try {
        $code = @"
from tools.model_policy import select_repair_model
print(select_repair_model(
    issue_count=$IssueCount,
    previous_classify_failed=$previousClassifyFailedLiteral,
    scope_ambiguous=$scopeAmbiguousLiteral,
    missing_artifact_generation=$missingArtifactGenerationLiteral,
    compound_gate_failure=$compoundGateFailureLiteral,
))
"@
        $value = & $PyExe -c $code
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($value)) {
            throw "model_policy.py repair selection failed rc=$LASTEXITCODE"
        }
        return [string]$value
    } finally {
        Pop-Location
    }
}

function Test-CodexAuthReadiness {
    $authPath = Join-Path $env:USERPROFILE '.codex\auth.json'
    if (-not (Test-Path -LiteralPath $authPath)) {
        Write-Log "codex auth readiness failed: auth_json_missing"
        return $false
    }
    try {
        $auth = Get-Content -LiteralPath $authPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if (-not $auth.tokens -or -not $auth.tokens.refresh_token) {
            Write-Log "codex auth readiness failed: refresh_token_missing"
            return $false
        }
    } catch {
        Write-Log "codex auth readiness failed: auth_json_unreadable reason=$($_.Exception.Message)"
        return $false
    }
    Push-Location $RepoDir
    $doctorCapture = Join-Path $env:TEMP ("news-grasp-codex-doctor-$DateStamp-$PID.log")
    try {
        Invoke-LoggedCapture -CapturePath $doctorCapture -Block { & $CodexExe 'doctor' }
        $doctorRc = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($doctorRc -ne 0) {
        $doctorText = ''
        if (Test-Path -LiteralPath $doctorCapture) {
            $doctorText = Get-Content -LiteralPath $doctorCapture -Raw -Encoding UTF8
        }
        $authConfigured = ($doctorText -match 'auth is configured' -or $doctorText -match 'stored auth mode\s+chatgpt')
        $chatGptTokens = ($doctorText -match 'stored ChatGPT tokens\s+true' -or $doctorText -match 'ChatGPT tokens\s+true')
        $mcpFailure = ($doctorText -match '(?im)^\s*[✗x]\s+mcp\b' -or $doctorText -match 'MCP configuration')
        $authFailure = (
            $doctorText -match 'auth is not configured' -or
            $doctorText -match 'stored ChatGPT tokens\s+false' -or
            $doctorText -match 'stored auth mode\s+(none|api-key)' -or
            $doctorText -match 'auth file.*missing'
        )
        if ($authConfigured -and $chatGptTokens -and -not $authFailure) {
            Write-Log "codex doctor non-auth failure ignored: rc=$doctorRc reason=auth is configured stored ChatGPT tokens true"
            return $true
        }
        Write-Log "codex auth readiness failed: codex doctor auth rc=$doctorRc"
        return $false
    }
    return $true
}

function Test-YouTubePodcastAuthReadiness {
    Push-Location $RepoDir
    try {
        Invoke-Logged { & $PyExe '-m' 'tools.youtube_podcast.auth_doctor' '--check-only' '--json' }
        $youtubeAuthRc = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($youtubeAuthRc -eq 10) {
        Stop-ExternalReadiness -Reason "youtube auth doctor failed: oauth consent required rc=$youtubeAuthRc" -Kind 'oauth_consent_required' -System 'youtube' -ExternalStatus "rc=$youtubeAuthRc" -ExternalDetail 'tools.youtube_podcast.auth_doctor --check-only --json'
    }
    if ($youtubeAuthRc -eq 71) {
        Stop-ExternalReadiness -Reason "youtube auth doctor failed: blocked external readiness rc=$youtubeAuthRc" -Kind 'youtube_quota_or_permission' -System 'youtube' -ExternalStatus "rc=$youtubeAuthRc" -ExternalDetail 'tools.youtube_podcast.auth_doctor --check-only --json'
    }
    if ($youtubeAuthRc -ne 0) {
        Write-Log "youtube auth doctor failed: rc=$youtubeAuthRc"
        return $false
    }
    return $true
}

function Test-ArtifactExecutableTreeIntegrity {
    if ($Stage2EditorSmokeOnly -or $SmokeTest -or $PreflightOnly) { return $true }
    $auditControl = Join-Path $OpsRepoRoot 'tools\audit_recovery_control.py'
    if (-not (Test-Path -LiteralPath $auditControl -PathType Leaf)) {
        Write-Log "ERROR: trusted artifact verifier missing: $auditControl"
        return $false
    }
    Push-Location $RepoDir
    try {
        Invoke-Logged { & $PyExe '-I' $auditControl 'verify-artifact-tree' '--artifact-root' $RepoDir }
        $verifyRc = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($verifyRc -ne 0) {
        Write-Log "ERROR: ARTIFACT_EXECUTABLE_TREE_INVALID rc=$verifyRc"
        return $false
    }
    return $true
}

function Assert-RecoveryOperationDeadline {
    param(
        [switch] $HighCost,
        [string] $Stage = 'operation'
    )
    if (
        $RunIntent -ne 'ScheduledRecoveryFull' -or
        $FinalizeVerifiedPublishManifest -or
        $script:UsesHighCostContinuationAdmission -or
        ($ResumeFromStage -and $HighCostAdmissionPath) -or
        $null -eq $script:RecoveryHardDeadline
    ) {
        return
    }
    $now = [DateTimeOffset]::Now
    if ($now -ge [DateTimeOffset]$script:RecoveryHardDeadline) {
        Add-RunnerLogLine -Text "ERROR: RECOVERY_EXECUTION_HARD_DEADLINE_EXCEEDED stage=$Stage"
        Exit-Runner -Status 'blocked_recovery_hard_deadline' -Message 'RECOVERY_EXECUTION_HARD_DEADLINE_EXCEEDED' -ExitCode 78
    }
    if ($HighCost -and $now -ge [DateTimeOffset]$script:RecoveryHighCostCutoff) {
        Add-RunnerLogLine -Text "ERROR: RECOVERY_EXECUTION_HIGH_COST_CUTOFF_EXCEEDED stage=$Stage"
        Exit-Runner -Status 'blocked_recovery_high_cost_cutoff' -Message 'RECOVERY_EXECUTION_HIGH_COST_CUTOFF_EXCEEDED' -ExitCode 78
    }
}

function Acquire-RecoveryHighCostBudget {
    param([string] $Stage)
    Assert-RecoveryOperationDeadline -HighCost -Stage $Stage
    if ($RunIntent -ne 'ScheduledRecoveryFull' -or $FinalizeVerifiedPublishManifest) {
        $script:HighCostCallSequence += 1
        return $script:HighCostCallSequence
    }
    $mutex = [System.Threading.Mutex]::new($false, "Local\NewsGraspRecoveryBudget-$RunId")
    $held = $false
    try {
        $held = $mutex.WaitOne(5000)
        if (-not $held) {
            Exit-Runner -Status 'blocked_recovery_budget_lock' -Message 'RECOVERY_EXECUTION_BUDGET_LOCK_TIMEOUT' -ExitCode 78
        }
        Assert-RecoveryOperationDeadline -HighCost -Stage $Stage
        if ($script:HighCostCallSequence -ge $script:RecoveryMaxExternalModelCalls) {
            Add-RunnerLogLine -Text "ERROR: RECOVERY_EXECUTION_MODEL_BUDGET_EXHAUSTED stage=$Stage used=$script:HighCostCallSequence max=$script:RecoveryMaxExternalModelCalls"
            Exit-Runner -Status 'blocked_recovery_model_budget' -Message 'RECOVERY_EXECUTION_MODEL_BUDGET_EXHAUSTED' -ExitCode 78
        }
        $script:HighCostCallSequence += 1
        return $script:HighCostCallSequence
    } finally {
        if ($held) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
}

function Invoke-CodexWrapper {
    param(
        [string] $PromptFile,
        [int] $TimeoutSec,
        [int] $IdleTimeoutSec,
        [string] $Model = '',
        [string] $ReasoningEffort = '',
        [string] $OutputSchema = $CodexOutputSchema,
        [string] $OutputLastMessage = $CodexLastMessage,
        [string] $FlowName = 'unknown',
        [string] $SuccessProbeCommand = '',
        [int] $SuccessProbeIntervalSec = 30,
        [int] $SuccessProbeMinElapsedSec = 0
    )
    $callSequence = Acquire-RecoveryHighCostBudget -Stage "model:$FlowName"
    if (
        $RunIntent -eq 'ScheduledRecoveryFull' -and
        (-not $FinalizeVerifiedPublishManifest) -and
        (-not $script:UsesHighCostContinuationAdmission) -and
        (-not ($ResumeFromStage -and $HighCostAdmissionPath))
    ) {
        $remainingSeconds = [int][Math]::Floor((([DateTimeOffset]$script:RecoveryHardDeadline) - [DateTimeOffset]::Now).TotalSeconds)
        if ($remainingSeconds -le 0) {
            Assert-RecoveryOperationDeadline -HighCost -Stage "model:$FlowName"
        }
        $TimeoutSec = [Math]::Max(1, [Math]::Min($TimeoutSec, $remainingSeconds))
        $IdleTimeoutSec = [Math]::Max(1, [Math]::Min($IdleTimeoutSec, $remainingSeconds))
    }
    $safeFlowName = $FlowName -replace '[^A-Za-z0-9._-]', '_'
    $highCostCallId = "$RunId`:$FlowName`:$callSequence"
    $highCostCallReceipt = Join-Path $HighCostCallReceiptDir ("{0:D3}-{1}.json" -f $callSequence, $safeFlowName)
    $codexArgs = @{
        'CodexExe' = $CodexExe
        'PromptFile' = $PromptFile
        'LogFile' = $LogPath
        'TimeoutSec' = $TimeoutSec
        'IdleTimeoutSec' = $IdleTimeoutSec
        'WorkingDirectory' = $RepoDir
        'OutputSchema' = $OutputSchema
        'OutputLastMessage' = $OutputLastMessage
        'FlowName' = $FlowName
        'UsageLog' = $CodexUsageLog
        'HighCostBindingPath' = $HighCostBindingPath
        'HighCostBindingReceiptSha256' = $HighCostBindingReceiptSha256
        'HighCostBindingResolverSha256' = $HighCostBindingResolverSha256
        'HighCostAdmissionPath' = [string]$script:HighCostAdmissionPath
        'HighCostParentAuthorityPath' = [string]$script:HighCostParentAuthorityPath
        'E2EFinalAdmissionPath' = [string]$script:E2EFinalAdmissionPath
        'E2EFinalRunnerArgumentsPath' = [string]$script:E2EFinalRunnerArgumentsPath
        'E2EFinalReservationReceiptPath' = [string]$script:E2EFinalReservationReceiptPath
        'E2EFinalClaimReceiptPath' = [string]$script:E2EFinalClaimReceiptPath
        'E2EAttemptPolicyPath' = [string]$E2EAttemptPolicyPath
        'E2ELogicalAttempt' = $E2ELogicalAttempt
        'HighCostClaimWitness' = [string]$script:HighCostClaimWitness
        'HighCostAttemptId' = [string]$script:HighCostAttemptId
        'HighCostExpectedOperationKind' = $script:HighCostExpectedOperationKind
        'HighCostExpectedIssueDate' = $script:HighCostExpectedIssueDate
        'HighCostPythonExe' = $PyExe
        'HighCostCallId' = $highCostCallId
        'HighCostCallReceiptPath' = $highCostCallReceipt
    }
    if ($SuccessProbeCommand) {
        $codexArgs['SuccessProbeCommand'] = $SuccessProbeCommand
        $codexArgs['SuccessProbeIntervalSec'] = $SuccessProbeIntervalSec
        $codexArgs['SuccessProbeMinElapsedSec'] = $SuccessProbeMinElapsedSec
    }
    if ($Model) { $codexArgs['Model'] = $Model }
    if ($ReasoningEffort) { $codexArgs['ReasoningEffort'] = $ReasoningEffort }
    if ($GlobalHarnessGenerationManifestPath) {
        $codexArgs['GlobalHarnessGenerationManifestPath'] = $GlobalHarnessGenerationManifestPath
    }
    & $CodexWrapper @codexArgs
    $wrapperOk = $?
    $wrapperRc = $LASTEXITCODE
    if (-not $wrapperOk) {
        if ($null -eq $wrapperRc -or $wrapperRc -eq 0) { $wrapperRc = 125 }
    }
    if ($FlowName -notlike 'reporter:*') {
        if (-not (Test-ArtifactExecutableTreeIntegrity)) { return 126 }
    }
    Assert-RecoveryOperationDeadline -Stage "model-complete:$FlowName"
    return $wrapperRc
}

function ConvertTo-JsonlLine {
    param([Parameter(Mandatory=$true)] $Value)
    return ($Value | ConvertTo-Json -Depth 20 -Compress)
}

function Add-JsonlRecordsIfMissing {
    param(
        [Parameter(Mandatory=$true)][string] $Path,
        [Parameter(Mandatory=$true)] $Records
    )
    $dir = Split-Path -Parent $Path
    if ($dir) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }

    $existingKeys = New-Object 'System.Collections.Generic.HashSet[string]'
    if (Test-Path -LiteralPath $Path) {
        foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
            if ([string]::IsNullOrWhiteSpace($line)) { continue }
            try {
                $row = $line | ConvertFrom-Json
                $key = "$($row.date)|$($row.url)"
                if (-not [string]::IsNullOrWhiteSpace($key)) { [void]$existingKeys.Add($key) }
            } catch {
                continue
            }
        }
    }

    $newLines = New-Object System.Collections.Generic.List[string]
    foreach ($record in @($Records)) {
        $key = "$($record.date)|$($record.url)"
        if ([string]::IsNullOrWhiteSpace([string]$record.url) -or $existingKeys.Contains($key)) {
            continue
        }
        [void]$existingKeys.Add($key)
        $newLines.Add((ConvertTo-JsonlLine -Value $record))
    }
    if ($newLines.Count -gt 0) {
        Add-Content -LiteralPath $Path -Value $newLines.ToArray() -Encoding UTF8
    }
    return $newLines.Count
}

function Sync-EditorOutputPreview {
    param(
        [Parameter(Mandatory=$true)][string] $PreviewPath,
        [string] $FallbackPath = '',
        [string] $CapturePath = ''
    )
    $sourcePath = $PreviewPath
    if ($FallbackPath -and (Test-Path -LiteralPath $FallbackPath)) {
        # CodexLastMessage は attempt ごとに更新される。前 attempt の preview が残っていても、
        # 現 attempt の出力を先に検証して preview へ昇格する。
        $sourcePath = $FallbackPath
    } elseif (-not (Test-Path -LiteralPath $sourcePath)) {
        Write-Log "editor output preview missing: $PreviewPath"
        return
    }
    if (-not $CapturePath) {
        $CapturePath = Join-Path ([System.IO.Path]::GetTempPath()) ("news-grasp-editor-materialize-$DateStamp-$PID.log")
    }
    $receiptPath = Join-Path $ReporterArtifactDir 'editor-materialization-receipt.json'
    Push-Location $RepoDir
    try {
        Invoke-LoggedCapture -CapturePath $CapturePath -Block {
            & $PyExe '-I' $canonicalMaterializer '--source' $sourcePath '--repo-root' $RepoDir '--date' $DateStamp '--receipt' $receiptPath
        }
        return $LASTEXITCODE
    } finally {
        Pop-Location
    }
}

function Read-RepairDecision {
    param(
        [string] $GateId,
        [string] $CapturePath,
        [string] $ClassifyPath = ''
    )
    if ($ClassifyPath -and (Test-Path -LiteralPath $ClassifyPath)) {
        $classifyOutputText = Get-Content -LiteralPath $ClassifyPath -Raw -Encoding UTF8
    } else {
        Push-Location $RepoDir
        try {
            $classifyOutput = & $PyExe '-m' 'tools.auto_repair_orchestrator' 'classify' '--gate-id' $GateId '--output-file' $CapturePath 2>&1
            $classifyRc = $LASTEXITCODE
        } finally {
            Pop-Location
        }
        if ($classifyRc -ne 0) {
            Write-Log "repair decision read failed: classify failed (gate=$GateId, rc=$classifyRc)"
            return $null
        }
        $classifyOutputText = $classifyOutput -join "`n"
    }

    try {
        return ($classifyOutputText | ConvertFrom-Json)
    } catch {
        Write-Log "repair decision read failed: classify JSON parse failed (gate=$GateId)"
        return $null
    }
}

function Set-TypedRepairTerminalState {
    param(
        [string] $GateId,
        [string] $Category,
        [object] $Decision,
        [string] $Message
    )
    $terminalStatus = if ($null -eq $Decision) { 'blocked_unknown_repair_class' } else { [string]$Decision.failure_status }
    if ([string]::IsNullOrWhiteSpace($terminalStatus)) {
        $terminalStatus = 'blocked_unknown_repair_class'
    }
    $externalKind = ''
    $externalSystem = ''
    $externalDetail = ''
    if ($null -ne $Decision) {
        if ($Decision.PSObject.Properties.Name -contains 'external_kind') {
            $externalKind = [string]$Decision.external_kind
        }
        if ($Decision.PSObject.Properties.Name -contains 'external_system') {
            $externalSystem = [string]$Decision.external_system
        }
        if ($Decision.PSObject.Properties.Name -contains 'reason') {
            $externalDetail = [string]$Decision.reason
        }
    }
    Set-RunnerState `
        -Status $terminalStatus `
        -Message $Message `
        -ExitCode 1 `
        -Phase 'repair' `
        -Step $Message `
        -GateId $GateId `
        -Category $Category `
        -ExternalKind $externalKind `
        -ExternalSystem $externalSystem `
        -ExternalStatus $terminalStatus `
        -ExternalDetail $externalDetail
    Write-Log "typed repair terminal state: gate=$GateId status=$terminalStatus message=$Message"
}

function Invoke-TargetedRepair {
    param(
        [string] $GateId,
        [string] $Category,
        [string] $CapturePath,
        [string[]] $Artifacts,
        [string] $RepairTransactionId,
        [string] $ClassifyPath = ''
    )
    Update-RunnerProgress -Phase 'repair' -Step "repair budget check: $GateId" -GateId $GateId -Category $Category
    $decision = Read-RepairDecision -GateId $GateId -CapturePath $CapturePath -ClassifyPath $ClassifyPath
    if ($null -eq $decision) {
        Set-TypedRepairTerminalState -GateId $GateId -Category $Category -Decision $null -Message "repair decision unavailable"
        return 1
    }
    if ([string]$decision.repair_class -in @('typed_external', 'typed_fatal', 'handler_unimplemented_red')) {
        Set-TypedRepairTerminalState -GateId $GateId -Category $Category -Decision $decision -Message "repair decision is terminal: $($decision.issue_code)"
        return 1
    }
    $attemptState = Join-Path $RepoDir ("data\gate_attempts\$DateStamp.json")
    # 2026-06-10: 変数名を $args から $gateAttemptArgs に変更 (致命バグ修正)。
    #   $args は PowerShell 自動変数。`Invoke-Logged { & $PyExe @args }` の
    #   scriptblock を `& $Block` 実行すると、@args は scriptblock 自身の空
    #   automatic $args に化け `& $PyExe` がスクリプト無指定で起動 → Python 3.13 の
    #   対話 REPL が立ち上がり、Task Scheduler 配下 (非 TTY) で console 寸法取得に
    #   失敗 (WinError 6/123) → 例外リトライ無限ループで runner 全体がハング
    #   (2026-06-10 daily-quality gate 失敗時に 27000 行の traceback で実害)。
    #   非自動変数名にすれば scriptblock の closure 捕捉が効く (Invoke-PythonGateWithRepair の
    #   @PythonArgs と同じ正常経路)。
    $gateAttemptArgs = @(
        '-m', 'tools.gate_attempts',
        '--state', $attemptState,
        '--repo-root', $RepoDir,
        '--gate-id', $GateId,
        '--category', $Category,
        '--output-file', $CapturePath
    )
    foreach ($artifact in $Artifacts) {
        $gateAttemptArgs += @('--artifact', $artifact)
    }

    Push-Location $RepoDir
    try {
        Invoke-Logged { & $PyExe @gateAttemptArgs }
        $decisionRc = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($decisionRc -ne 0) {
        Write-Log "gate retry ledger denied repair worker (gate=$GateId, rc=$decisionRc); preserving typed gate classification"
        Set-TypedRepairTerminalState -GateId $GateId -Category $Category -Decision $decision -Message "gate retry ledger denied repair worker"
        return 1
    }

    $repairSignature = "${GateId}:$($decision.issue_code):$($decision.handler_id)"
    Update-RunnerProgress -Phase 'repair' -Step "repair decision: $GateId $($decision.issue_code)" -GateId $GateId -Category $Category -RepairSignature $repairSignature -ArtifactProgress $false

    $registryRepairRc = Invoke-DeterministicRegistryRepair -GateId $GateId -CapturePath $CapturePath -Artifacts $Artifacts -ClassifyPath $ClassifyPath -RepairDecision $decision
    if ($registryRepairRc -eq 0) {
        Write-Log "deterministic registry repair OK (gate=$GateId)"
        Update-RunnerProgress -Phase 'repair' -Step "deterministic registry repair OK: $GateId" -GateId $GateId -Category $Category -RepairSignature $repairSignature -ArtifactProgress $true
        return 0
    }
    if ($registryRepairRc -eq 4) {
        Write-Log "deterministic registry repair produced no mutation; same-gate reverify required (gate=$GateId)"
        Update-RunnerProgress -Phase 'repair' -Step "same-gate reverify after registry noop: $GateId" -GateId $GateId -Category $Category -RepairSignature $repairSignature -ArtifactProgress $false
        return 0
    }
    if ($registryRepairRc -notin @(2, 3)) {
        Write-Log "deterministic registry repair failed (gate=$GateId, rc=$registryRepairRc)"
        return $registryRepairRc
    }

    if ([string]$decision.repair_class -notin @('llm_generate_missing_artifact', 'llm_rewrite_existing_artifact')) {
        Write-Log "repair matrix denied LLM repair worker (gate=$GateId, repair_class=$($decision.repair_class), status=$($decision.failure_status))"
        return 1
    }

    $llmRepairArtifacts = @(Get-RepairDecisionArtifacts -RepairDecision $decision -FallbackArtifacts $Artifacts)
    if ($llmRepairArtifacts.Count -eq 0) {
        $llmRepairArtifacts = @($Artifacts)
    }

    if (-not (Test-RepairWorkerPreflight -GateId $GateId -Artifacts $llmRepairArtifacts -RepairTransactionId $RepairTransactionId -RepairDecision $decision)) {
        Write-Log "pre-repair policy denied LLM repair worker (gate=$GateId, status=blocked_pre_repair_recreate)"
        return 1
    }

    Update-RunnerProgress -Phase 'repair' -Step "codex auth readiness: $GateId" -GateId $GateId -Category $Category
    Write-Log "codex auth readiness gate start (repair:$GateId)"
    if (-not (Test-CodexAuthReadiness)) {
        Exit-Runner -Status 'blocked_codex_auth' -Message "codex auth readiness failed before repair:$GateId" -ExitCode 72
    }
    Write-Log "codex auth readiness gate OK (repair:$GateId)"

    $repairPrompt = Join-Path ([System.IO.Path]::GetTempPath()) ("news-grasp-repair-$GateId-$DateStamp.md")
    $failureText = ''
    if (Test-Path $CapturePath) {
        $failureText = Get-Content -LiteralPath $CapturePath -Raw -Encoding UTF8
    }
    $artifactText = [string]::Join(', ', $llmRepairArtifacts)
    $repairTransactionDir = Get-RepairTransactionDir -TransactionId $RepairTransactionId
    $prompt = @"
News-Grasp RecoverOnly targeted repair.

目的:
- gate 失敗を 1 回だけ修復する。
- まず既存 artifact を確認し、validation failure が示す不足だけを最小差分で修正する。
- 既存 artifact を破棄して新規生成しない。再利用不能の証拠がある場合だけ、指定 artifact の再作成を許可する。
- 同じ gate を再実行したときに PASS するまでの最小修復に限定する。
- repair は runner の bounded retry 内でだけ実行される。無制限 loop にしない。
- commit / push / full rerun / 全体再生成 / publish 実行は禁止。docs 欠落が失敗原因の場合だけ、指定 artifact の docs を作る最小 build は許可する。
- 変更してよいのは下記 artifact と、その修復に必須の最小ファイルだけ。対象 artifact 以外へ作業を広げない。

gate_id: $GateId
category: $Category
artifacts: $artifactText
repair_transaction_dir: $repairTransactionDir

失敗ログ:
$failureText

作業:
1. artifacts に列挙された既存 artifact を読む。存在しない場合だけ missing として扱う。
2. 失敗ログの validation failure が示す不備だけを、既存 artifact 上で最小差分修正する。
3. artifact が存在する場合は、既存 artifact を破棄して新規生成しない。
4. artifact が存在しない、または構造破損・日付不一致・カテゴリ不一致・provenance 不正で再利用不能の証拠がある場合だけ、指定 artifact を再作成する。
   - 既存 artifact を大きく作り直す場合は、repair_transaction_dir の reuse-blocked.json に artifact_path と typed reason を必ず書く。
   - reason は missing_artifact / structure_corrupt / date_mismatch / category_mismatch / provenance_invalid のいずれか。
5. runner_python を使い、同じ gate を再実行して PASS するまで確認する。
6. 同じ gate が PASS しない場合は、追加で別作業へ広げず失敗理由を最小 artifact に残して停止する。
7. git commit / git push は絶対に実行しない。
8. 修正したら停止する。

音声台本の収束条件:
- 失敗ログの code が audio_script_quality_invalid の場合、対象の audio-script だけを修正する。
- 修正は末尾追記ではなく、論点設計メモから本文全体を再構成する。既存本文の良い事実・固有名詞・数値は保持してよいが、段落構成と橋渡しは作り直す。
- 対象ファイルの本文冒頭に `<!-- tts-outline ... -->` を必ず置く。outline には `中心論点`、`背景`、`なぜ今`、`因果関係`、`カテゴリ論点`、`リスク・未確定`、`次の観測点` を含める。
- 失敗ログの `論点設計メモ不足`、`論点充足不足`、`字数不足`、`今日の観点・考察不足` をそのまま不足観点として扱い、本文側で背景、影響、リスク、次の観測点を具体化する。
- 字数不足は 2500 字ぎりぎりを狙わず、tools.tts.build_script.effective_char_count で 2600〜2800 字に収める。字数を満たすための定型補足文、カテゴリ名だけを差し替えた文、同じ締め文の追加は禁止。
- 同じ runner_python で tools.validate_generation_quality を再実行し、audio_script_quality_invalid が消えたことを確認する。

制約:
- 検証コマンドは必ず次の Python 実行体だけを使う。
- runner_python: $PyExe
- python / py / uv / repo-local runtime の直書きは禁止。WindowsApps python や uv cache に流れる経路を作らず、署名済みsystem Python312 bindingだけを使う。
- git add / git commit / git push / git checkout / git reset は絶対に実行しない。
- rg / Get-ChildItem -Recurse / 広域 Select-String は禁止。読む場合は失敗ログと artifacts に列挙された最小ファイルだけに限定する。
"@
    [System.IO.File]::WriteAllText($repairPrompt, $prompt, [System.Text.UTF8Encoding]::new($false))
    $issueCount = 1
    if ($decision.PSObject.Properties.Name -contains 'issue_ledger' -and $decision.issue_ledger) {
        $issueCount = @($decision.issue_ledger).Count
    } elseif ($decision.PSObject.Properties.Name -contains 'issues' -and $decision.issues) {
        $issueCount = @($decision.issues).Count
    }
    $scopeAmbiguous = @('repair_context_scope_mismatch', 'repair_context_overbroad') -contains [string]$decision.failure_status
    $missingArtifactGeneration = ([string]$decision.repair_class -eq 'llm_generate_missing_artifact')
    $compoundGateFailure = ($GateId -in @('daily-quality', 'generation-quality') -and $issueCount -gt 1)
    $repairModel = Select-RepairModel -IssueCount $issueCount -PreviousClassifyFailed:$false -ScopeAmbiguous:$scopeAmbiguous -MissingArtifactGeneration:$missingArtifactGeneration -CompoundGateFailure:$compoundGateFailure
    $RepairReasoningEffort = Get-ModelPolicyValue -Role 'repair' -Key 'reasoning'
    $repairFlowName = "repair:$GateId"
    if ($HighCostAdmissionPath -and (Test-Path -LiteralPath $HighCostAdmissionPath -PathType Leaf)) {
        try {
            $activeAdmission = Get-Content -LiteralPath $HighCostAdmissionPath -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
            if (
                $GateId -eq 'generation-quality' -and
                [string]$activeAdmission.schemaVersion -eq 'HIGH_COST_SCHEDULED_INCIDENT_REPAIR_V1' -and
                $activeAdmission.PSObject.Properties.Name -contains 'allowedModelRoutes' -and
                @($activeAdmission.allowedModelRoutes) -contains 'repair:incident-publication'
            ) {
                $repairFlowName = 'repair:incident-publication'
            }
        } catch {
            Write-Log "WARN: high cost admission flow route inspection failed (gate=$GateId, reason=$($_.Exception.Message))"
        }
    }
    Write-Log "repair wrapper invoke START (agent=codex, gate=$GateId, flow=$repairFlowName, Model=$repairModel, ReasoningEffort=$RepairReasoningEffort, issue_count=$issueCount, missing_artifact_generation=$missingArtifactGeneration, TimeoutSec=900)"
    Update-RunnerProgress -Phase 'repair' -Step "repair wrapper invoke: $GateId" -GateId $GateId -Category $Category
    $repairRc = Invoke-CodexWrapper -PromptFile $repairPrompt -TimeoutSec 900 -IdleTimeoutSec 300 -Model $repairModel -ReasoningEffort $RepairReasoningEffort -FlowName $repairFlowName
    Write-Log "repair wrapper invoke END (agent=codex, gate=$GateId, rc=$repairRc)"
    Update-RunnerProgress -Phase 'repair' -Step "repair wrapper done: $GateId rc=$repairRc" -GateId $GateId -Category $Category
    return $repairRc
}

function Get-RepairDecisionArtifacts {
    param(
        [object] $RepairDecision,
        [string[]] $FallbackArtifacts = @()
    )
    $selected = New-Object System.Collections.Generic.List[string]
    if ($null -ne $RepairDecision) {
        if ($RepairDecision.PSObject.Properties.Name -contains 'selected_artifacts') {
            foreach ($artifact in @($RepairDecision.selected_artifacts)) {
                $text = ([string] $artifact).Trim()
                if ($text -and -not $selected.Contains($text)) { $selected.Add($text) }
            }
        }
        if ($RepairDecision.PSObject.Properties.Name -contains 'artifact_paths') {
            foreach ($artifact in @($RepairDecision.artifact_paths)) {
                $text = ([string] $artifact).Trim()
                if ($text -and -not $selected.Contains($text)) { $selected.Add($text) }
            }
        }
    }
    if ($selected.Count -gt 0) {
        return $selected.ToArray()
    }
    return @()
}

function Invoke-DeterministicRegistryRepair {
    # registry handler traceability: matrix が選んだ deterministic handler だけを実行する。
    param(
        [string] $GateId,
        [string] $CapturePath,
        [string[]] $Artifacts,
        [string] $ClassifyPath = '',
        [object] $RepairDecision = $null
    )
    Update-RunnerProgress -Phase 'repair' -Step "deterministic registry repair: $GateId" -GateId $GateId

    $decision = $RepairDecision
    if ($null -eq $decision) {
        $decision = Read-RepairDecision -GateId $GateId -CapturePath $CapturePath -ClassifyPath $ClassifyPath
    }
    if ($null -eq $decision) { return 2 }
    if ($decision.handler -ne 'deterministic-repair' -or -not $decision.handler_id) {
        return 2
    }
    $typedRegistryStatuses = @(
        'repair_context_scope_mismatch',
        'repair_handler_output_scope_violation',
        'blocked_deterministic_repair_not_applicable',
        'blocked_articles_only_record_incomplete',
        'blocked_digest_only_ambiguous',
        'blocked_repair_handler_unimplemented'
    )
    $repairArtifacts = @(Get-RepairDecisionArtifacts -RepairDecision $decision -FallbackArtifacts $Artifacts)
    $hasRepairPlan = (
        ($decision.PSObject.Properties.Name -contains 'batch_repair_eligible') -and
        [bool]$decision.batch_repair_eligible -and
        ($decision.PSObject.Properties.Name -contains 'repair_steps') -and
        @($decision.repair_steps).Count -gt 0
    )
    if ($hasRepairPlan) {
        $repairPlanPath = $ClassifyPath
        if (-not $repairPlanPath -or -not (Test-Path -LiteralPath $repairPlanPath)) {
            $repairPlanPath = Join-Path ([System.IO.Path]::GetTempPath()) ("news-grasp-repair-plan-$GateId-$DateStamp-$RunId.json")
            $decision | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $repairPlanPath -Encoding UTF8
        }
        $registryArgs = @(
            '-m', 'tools.repair_registry',
            'repair-plan',
            '--repo-root', $RepoDir,
            '--date', $DateStamp,
            '--plan-file', $repairPlanPath
        )
    } else {
        $registryArgs = @(
            '-m', 'tools.repair_registry',
            'repair',
            '--handler-id', $decision.handler_id,
            '--repo-root', $RepoDir,
            '--date', $DateStamp
        )
        foreach ($artifact in $repairArtifacts) {
            $registryArgs += @('--artifact', $artifact)
        }
    }
    $registryCapture = Join-Path ([System.IO.Path]::GetTempPath()) ("news-grasp-registry-repair-$GateId-$DateStamp.json")

    Push-Location $RepoDir
    try {
        Invoke-LoggedCapture -Block { & $PyExe @registryArgs } -CapturePath $registryCapture
        $registryRc = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    $registryStatus = ''
    $registryMessage = ''
    if (Test-Path -LiteralPath $registryCapture) {
        try {
            $registryPayload = Get-Content -LiteralPath $registryCapture -Raw -Encoding UTF8 | ConvertFrom-Json
            $registryStatus = [string] $registryPayload.status
            $registryMessage = [string] $registryPayload.message
        } catch {
            $registryStatus = ''
            $registryMessage = ''
        }
    }
    if ($registryStatus -eq 'noop') {
        Write-Log "registry noop; same-gate reverify required (gate=$GateId, handler=$($decision.handler_id), message=$registryMessage)"
        return 4
    }
    if ($registryRc -eq 0) {
        if ($hasRepairPlan) {
            Write-Log "compound deterministic repair OK (gate=$GateId, handlers=$(@($decision.repair_steps).Count))"
        } else {
            Write-Log "deterministic registry repair OK (gate=$GateId, handler=$($decision.handler_id))"
        }
        return 0
    }
    if ($registryStatus -and $typedRegistryStatuses -contains $registryStatus) {
        Write-Log "ERROR: deterministic registry repair typed block (gate=$GateId, handler=$($decision.handler_id), status=$registryStatus, message=$registryMessage)"
        Exit-Runner -Status $registryStatus -Message "deterministic registry repair typed block for ${GateId}: $registryMessage" -ExitCode 73
    }
    if ($decision.failure_status -eq 'blocked_repair_handler_unimplemented') {
        Write-Log "ERROR: deterministic repair handler unavailable (gate=$GateId, handler=$($decision.handler_id), status=blocked_repair_handler_unimplemented)"
        Exit-Runner -Status 'blocked_repair_handler_unimplemented' -Message "deterministic repair handler unavailable for ${GateId}: $($decision.handler_id)" -ExitCode 73
    }
    Write-Log "deterministic registry repair failed (gate=$GateId, handler=$($decision.handler_id), rc=$registryRc)"
    return $registryRc
}

function New-RepairTransactionId {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $suffix = [guid]::NewGuid().ToString('N').Substring(0, 8)
    return "$stamp-$suffix"
}

function Get-RepairTransactionDir {
    param([string] $TransactionId)
    return (Join-Path $RepoDir "build\repair-transactions\$DateStamp\$TransactionId")
}

function ConvertTo-RepairSnapshotName {
    param([string] $ArtifactPath)
    return (($ArtifactPath.Trim().Replace('\', '/')) -replace '[:\\/]+', '__')
}

function Get-RepairArtifactHash {
    param([string] $FullPath)
    if (-not (Test-Path -LiteralPath $FullPath)) {
        return ''
    }
    $item = Get-Item -LiteralPath $FullPath
    if (-not $item.PSIsContainer) {
        return (Get-NewsGraspFileSha256Hex -Path $FullPath).ToUpperInvariant()
    }
    $parts = New-Object System.Collections.Generic.List[string]
    $files = @(Get-ChildItem -LiteralPath $FullPath -Recurse -File | Sort-Object FullName)
    foreach ($file in $files) {
        $rel = $file.FullName.Substring($FullPath.Length).TrimStart('\', '/')
        $hash = (Get-NewsGraspFileSha256Hex -Path $file.FullName).ToUpperInvariant()
        [void]$parts.Add("$rel=$hash")
    }
    return (($parts.ToArray() -join "`n") | ConvertTo-Json -Compress)
}

function Snapshot-RepairArtifacts {
    param(
        [string] $TransactionId,
        [ValidateSet('before', 'after')] [string] $Phase,
        [string[]] $Artifacts
    )
    $transactionDir = Get-RepairTransactionDir -TransactionId $TransactionId
    $phaseDir = Join-Path $transactionDir $Phase
    New-Item -ItemType Directory -Force -Path $phaseDir | Out-Null
    $manifest = New-Object System.Collections.Generic.List[object]
    foreach ($artifact in @($Artifacts)) {
        $rel = ([string]$artifact).Trim().Replace('\', '/')
        if (-not $rel) { continue }
        $full = Join-Path $RepoDir $rel
        $exists = Test-Path -LiteralPath $full
        $snapshotName = ConvertTo-RepairSnapshotName -ArtifactPath $rel
        $snapshotPath = Join-Path $phaseDir $snapshotName
        $itemType = 'missing'
        $length = 0
        $hash = ''
        if ($exists) {
            $item = Get-Item -LiteralPath $full
            $itemType = if ($item.PSIsContainer) { 'directory' } else { 'file' }
            if ($item.PSIsContainer) {
                Copy-Item -LiteralPath $full -Destination $snapshotPath -Recurse -Force
                $length = @((Get-ChildItem -LiteralPath $full -Recurse -File)).Count
            } else {
                Copy-Item -LiteralPath $full -Destination $snapshotPath -Force
                $length = $item.Length
            }
            $hash = Get-RepairArtifactHash -FullPath $full
        }
        [void]$manifest.Add([pscustomobject]@{
            artifact_path = $rel
            exists = [bool]$exists
            item_type = $itemType
            hash = $hash
            length = $length
            snapshot_path = if ($exists) { $snapshotPath } else { '' }
        })
    }
    $manifestPath = Join-Path $transactionDir "$Phase-manifest.json"
    $manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
    return $manifestPath
}

function Get-RepairSignificantLines {
    param([string] $Path)
    if (-not (Test-Path -LiteralPath $Path)) { return @() }
    try {
        $text = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
    } catch {
        return @()
    }
    return @($text -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_.Length -ge 12 })
}

function Test-RepairReuseBlockedReason {
    param(
        [string] $TransactionId,
        [string] $ArtifactPath
    )
    $transactionDir = Get-RepairTransactionDir -TransactionId $TransactionId
    $reasonPath = Join-Path $transactionDir 'reuse-blocked.json'
    if (-not (Test-Path -LiteralPath $reasonPath)) { return $false }
    try {
        $payload = Get-Content -LiteralPath $reasonPath -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        return $false
    }
    $allowedReasons = @('missing_artifact', 'structure_corrupt', 'date_mismatch', 'category_mismatch', 'provenance_invalid')
    foreach ($entry in @($payload)) {
        $entryPath = ([string]$entry.artifact_path).Trim().Replace('\', '/')
        $reason = ([string]$entry.reason).Trim()
        if ($entryPath -eq $ArtifactPath -and $reason -in $allowedReasons) {
            return $true
        }
    }
    return $false
}

function Test-RepairWorkerPreflight {
    param(
        [string] $GateId,
        [string[]] $Artifacts,
        [string] $RepairTransactionId,
        [object] $RepairDecision = $null
    )
    $transactionDir = Get-RepairTransactionDir -TransactionId $RepairTransactionId
    New-Item -ItemType Directory -Force -Path $transactionDir | Out-Null
    $existing = New-Object System.Collections.Generic.List[string]
    $missing = New-Object System.Collections.Generic.List[string]
    foreach ($artifact in @($Artifacts)) {
        $rel = ([string]$artifact).Trim().Replace('\', '/')
        if (-not $rel) { continue }
        $full = Join-Path $RepoDir $rel
        if (Test-Path -LiteralPath $full) {
            [void]$existing.Add($rel)
        } else {
            [void]$missing.Add($rel)
        }
    }
    $repairClass = ''
    if ($null -ne $RepairDecision) {
        $repairClass = [string]$RepairDecision.repair_class
    }
    $allMissing = ($existing.Count -eq 0)
    $allExisting = ($missing.Count -eq 0 -and $existing.Count -gt 0)
    $generateMissingAllowed = ($repairClass -eq 'llm_generate_missing_artifact' -and $allMissing)
    $rewriteExistingAllowed = ($repairClass -eq 'llm_rewrite_existing_artifact' -and $allExisting)
    $allowed = ($generateMissingAllowed -or $rewriteExistingAllowed)
    $deniedStatus = ''
    if (-not $allowed) {
        if (-not $allMissing) {
            $deniedStatus = 'blocked_existing_artifact_llm_recreate'
        } else {
            $deniedStatus = 'blocked_llm_repair_not_allowed_by_matrix'
        }
    }
    [pscustomobject]@{
        transaction_id = $RepairTransactionId
        date = $DateStamp
        gate_id = $GateId
        allowed = [bool]$allowed
        policy = if ($rewriteExistingAllowed) { 'matrix_owned_existing_artifact_rewrite' } else { 'llm_worker_only_when_matrix_allows_missing_artifact_and_all_artifacts_missing' }
        legacy_policy = 'llm_worker_only_when_all_artifacts_missing'
        repair_class = $repairClass
        issue_code = if ($null -eq $RepairDecision) { '' } else { [string]$RepairDecision.issue_code }
        existing_artifacts = @($existing.ToArray())
        missing_artifacts = @($missing.ToArray())
        denied_status = $deniedStatus
        legacy_denied_status = if ($allowed) { '' } else { 'blocked_pre_repair_recreate' }
    } | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $transactionDir 'pre-repair-policy.json') -Encoding UTF8
    if (-not $allowed) {
        Write-Log ("pre-repair policy denied LLM repair worker before edits; status=$deniedStatus; existing artifacts require deterministic patch repair: " + ([string]::Join(', ', @($existing.ToArray()))))
        return $false
    }
    if ($rewriteExistingAllowed) {
        Write-Log "pre-repair policy OK: coverage matrix explicitly allows bounded existing artifact rewrite"
    } else {
        Write-Log "pre-repair policy OK: coverage matrix allows missing artifact generation and all target artifacts are missing"
    }
    return $true
}

function Test-RepairPatchExistingPolicy {
    param(
        [string] $TransactionId,
        [string[]] $Artifacts
    )
    $transactionDir = Get-RepairTransactionDir -TransactionId $TransactionId
    $beforePath = Join-Path $transactionDir 'before-manifest.json'
    $afterPath = Join-Path $transactionDir 'after-manifest.json'
    if (-not (Test-Path -LiteralPath $beforePath) -or -not (Test-Path -LiteralPath $afterPath)) {
        Write-Log "repair patch-existing policy failed: transaction manifests missing tx=$TransactionId"
        return $false
    }
    $before = @(Get-Content -LiteralPath $beforePath -Raw -Encoding UTF8 | ConvertFrom-Json)
    $after = @(Get-Content -LiteralPath $afterPath -Raw -Encoding UTF8 | ConvertFrom-Json)
    $afterByPath = @{}
    foreach ($entry in $after) {
        $afterByPath[[string]$entry.artifact_path] = $entry
    }
    $violations = New-Object System.Collections.Generic.List[string]
    foreach ($beforeEntry in $before) {
        $artifactPath = [string]$beforeEntry.artifact_path
        $afterEntry = $afterByPath[$artifactPath]
        if (-not [bool]$beforeEntry.exists) { continue }
        if ($null -eq $afterEntry -or -not [bool]$afterEntry.exists) {
            [void]$violations.Add("${artifactPath}: existing artifact removed")
            continue
        }
        if ([string]$beforeEntry.hash -eq [string]$afterEntry.hash) { continue }
        if ([string]$beforeEntry.item_type -ne 'file' -or [string]$afterEntry.item_type -ne 'file') { continue }
        $beforeLines = @(Get-RepairSignificantLines -Path ([string]$beforeEntry.snapshot_path))
        $afterLines = @(Get-RepairSignificantLines -Path ([string]$afterEntry.snapshot_path))
        if ($beforeLines.Count -lt 5 -or $afterLines.Count -lt 5) { continue }
        $afterSet = New-Object 'System.Collections.Generic.HashSet[string]'
        foreach ($line in $afterLines) { [void]$afterSet.Add([string]$line) }
        $kept = 0
        foreach ($line in $beforeLines) {
            if ($afterSet.Contains([string]$line)) { $kept++ }
        }
        $preservedLineRatio = [double]$kept / [double]$beforeLines.Count
        if ($preservedLineRatio -lt 0.2 -and -not (Test-RepairReuseBlockedReason -TransactionId $TransactionId -ArtifactPath $artifactPath)) {
            [void]$violations.Add("${artifactPath}: preserved_line_ratio=$([math]::Round($preservedLineRatio, 3)) without reuse-blocked.json")
        }
    }
    $policyPath = Join-Path $transactionDir 'patch-existing-policy.json'
    [pscustomobject]@{
        transaction_id = $TransactionId
        date = $DateStamp
        artifacts = @($Artifacts)
        violations = @($violations.ToArray())
    } | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $policyPath -Encoding UTF8
    if ($violations.Count -gt 0) {
        Write-Log ("repair patch-existing policy failed: " + ([string]::Join(', ', @($violations))))
        return $false
    }
    Write-Log "repair patch-existing policy OK (tx=$TransactionId)"
    return $true
}

function Snapshot-RepairWorkspace {
    $lines = @(& $GitExe @GitSafeArgs -C $RepoDir status --porcelain --untracked-files=all)
    if ($LASTEXITCODE -ne 0) {
        Write-Log "WARN: failed to snapshot repair workspace (rc=$LASTEXITCODE)"
        return @()
    }
    return @($lines | ForEach-Object { [string]$_ })
}

function Get-RepairStatusPath {
    param([string] $StatusLine)
    if (-not $StatusLine -or $StatusLine.Length -lt 4) {
        return ''
    }
    $path = $StatusLine.Substring(3).Trim()
    if ($path -like '* -> *') {
        $path = ($path -split ' -> ')[-1].Trim()
    }
    return $path.Trim('"').Replace('\', '/')
}

function Test-RepairStatusPathAllowed {
    param(
        [string] $Path,
        [string[]] $AllowedArtifacts
    )
    if (-not $Path) { return $true }
    foreach ($artifact in @($AllowedArtifacts)) {
        if ($Path -eq $artifact -or $Path.StartsWith("$artifact/")) {
            return $true
        }
    }
    # runner-owned state: bounded retry budget は repair 前後で更新されるため artifact 違反にしない。
    if ($Path -eq "data/gate_attempts/$DateStamp.json") { return $true }
    # 一時・観測系出力: pytest / wrapper / usage の副産物は永続 artifact ではない。
    foreach ($prefix in @('.pytest-tmp/', 'build/codex-usage/', 'build/reporter-artifacts/', 'build/reporter-prompts/')) {
        if ($Path.StartsWith($prefix)) { return $true }
    }
    foreach ($exact in @('build/codex-last-message.txt', 'build/codex-last-message.json')) {
        if ($Path -eq $exact) { return $true }
    }
    return $false
}

function Test-RepairArtifactScope {
    param(
        [string[]] $BeforeStatus,
        [string[]] $Artifacts
    )
    $beforeSet = New-Object 'System.Collections.Generic.HashSet[string]'
    foreach ($line in @($BeforeStatus)) {
        [void]$beforeSet.Add([string]$line)
    }
    $allowed = @($Artifacts | ForEach-Object { ([string]$_).Trim().Replace('\', '/') } | Where-Object { $_ })
    $afterStatus = Snapshot-RepairWorkspace
    $violations = New-Object 'System.Collections.Generic.List[string]'
    foreach ($line in @($afterStatus)) {
        $statusLine = [string]$line
        if ($beforeSet.Contains($statusLine)) {
            continue
        }
        $path = Get-RepairStatusPath -StatusLine $statusLine
        if (-not $path) {
            continue
        }
        if (-not (Test-RepairStatusPathAllowed -Path $path -AllowedArtifacts $allowed)) {
            [void]$violations.Add($path)
        }
    }
    if ($violations.Count -gt 0) {
        Write-Log ("repair worker changed files outside artifact scope: " + ([string]::Join(', ', @($violations))))
        return $false
    }
    return $true
}

function Test-GenerationExternalReadiness {
    $missing = New-Object System.Collections.Generic.List[string]
    $requiredPaths = @(
        'data\articles.jsonl',
        'data\_status.md',
        "data\search_audit\$DateStamp"
    )
    foreach ($rel in $requiredPaths) {
        $path = Join-Path $RepoDir $rel
        if (-not (Test-Path -LiteralPath $path)) {
            Write-Log "generation external readiness missing: $rel"
            $missing.Add($rel)
        }
    }
    $auditDir = Join-Path $RepoDir "data\search_audit\$DateStamp"
    $auditFiles = @(Get-ChildItem -LiteralPath $auditDir -File -ErrorAction SilentlyContinue)
    if ($auditFiles.Count -eq 0) {
        Write-Log "generation external readiness missing: data\search_audit\$DateStamp has no files"
        $missing.Add("data\search_audit\$DateStamp has no files")
    }
    if ($missing.Count -gt 0) {
        return New-ExternalReadinessResult -Ok $false -Kind 'generation_input_missing' -System 'local_artifact_inventory' -Status 'missing' -Detail ([string]::Join('; ', @($missing)))
    }
    return New-ExternalReadinessResult -Ok $true -Kind 'ok' -System 'local_artifact_inventory'
}

function Invoke-PythonGateWithRepair {
    param(
        [string] $GateId,
        [string] $Category,
        [string[]] $PythonArgs,
        [string[]] $Artifacts,
        [datetime] $DeadlineAt = [datetime]::MaxValue,
        [switch] $NoRepair
    )
    for ($attempt = 1; ; $attempt++) {
        if ((Get-Date) -ge $DeadlineAt) {
            Write-Log "$GateId gate deadline exceeded before attempt $attempt"
            return 124
        }
        $capturePath = Join-Path ([System.IO.Path]::GetTempPath()) ("news-grasp-gate-$GateId-$DateStamp-attempt$attempt.log")
        Write-Log "$GateId gate attempt $attempt start"
        Update-RunnerProgress -Phase 'gate' -Step "$GateId attempt $attempt start" -GateId $GateId -Category $Category -Attempt $attempt -DeadlineAt $DeadlineAt.ToString('yyyy-MM-ddTHH:mm:ss.fffK')
        Push-Location $RepoDir
        try {
            Invoke-LoggedCapture -CapturePath $capturePath -Block { & $PyExe @PythonArgs }
            $gateRc = $LASTEXITCODE
        } finally {
            Pop-Location
        }
        if ($gateRc -eq 0) {
            Write-Log "$GateId gate OK (attempt=$attempt)"
            Update-RunnerProgress -Phase 'gate' -Step "$GateId attempt $attempt OK" -GateId $GateId -Category $Category -Attempt $attempt -DeadlineAt $DeadlineAt.ToString('yyyy-MM-ddTHH:mm:ss.fffK')
            return 0
        }
        Write-Log "$GateId gate failed (attempt=$attempt, rc=$gateRc)"
        Update-RunnerProgress -Phase 'gate' -Step "$GateId attempt $attempt failed rc=$gateRc" -GateId $GateId -Category $Category -Attempt $attempt -DeadlineAt $DeadlineAt.ToString('yyyy-MM-ddTHH:mm:ss.fffK')
        if ($NoRepair) {
            Write-Log "$GateId repair disabled for this gate; returning rc=$gateRc"
            return $gateRc
        }
        $classifyPath = Join-Path ([System.IO.Path]::GetTempPath()) ("news-grasp-repair-classify-$GateId-$DateStamp-$RunId-attempt$attempt.json")
        $gateCapturePathForClassify = $capturePath
        Push-Location $RepoDir
        try {
            Invoke-LoggedCapture -CapturePath $classifyPath -Block { & $PyExe '-m' 'tools.auto_repair_orchestrator' 'classify' '--gate-id' $GateId '--output-file' $gateCapturePathForClassify }
            $classifyRc = $LASTEXITCODE
        } finally {
            Pop-Location
        }
        if ($classifyRc -ne 0) {
            Write-Log "ERROR: auto repair classify failed gate=$GateId rc=$classifyRc capture=$capturePath"
            return $gateRc
        }
        $repairBeforeStatus = Snapshot-RepairWorkspace
        $repairTransactionId = New-RepairTransactionId
        [void](Snapshot-RepairArtifacts -TransactionId $repairTransactionId -Phase 'before' -Artifacts $Artifacts)
        $repairRc = Invoke-TargetedRepair -GateId $GateId -Category $Category -CapturePath $capturePath -Artifacts $Artifacts -RepairTransactionId $repairTransactionId -ClassifyPath $classifyPath
        if ($repairRc -eq 124) {
            Write-Log "$GateId repair timeout (rc=124)"
            return 124
        }
        if ($repairRc -ne 0) {
            return $gateRc
        }
        [void](Snapshot-RepairArtifacts -TransactionId $repairTransactionId -Phase 'after' -Artifacts $Artifacts)
        # 最後の砦: 作業前 preflight を抜けた missing-artifact repair でも、
        # 実行後に予期しない再作成や scope 拡大が起きた場合はここで止める。
        if (-not (Test-RepairPatchExistingPolicy -TransactionId $repairTransactionId -Artifacts $Artifacts)) {
            return $gateRc
        }
        if (-not (Test-RepairArtifactScope -BeforeStatus $repairBeforeStatus -Artifacts $Artifacts)) {
            return $gateRc
        }
    }
    return 1
}

function Invoke-AutonomousGate {
    param(
        [string] $GateId,
        [string] $Category,
        [string[]] $PythonArgs,
        [string[]] $Artifacts,
        [int] $GateDeadlineSec = 2100,
        [switch] $NoRepair
    )
    $statePath = Join-Path $RepoDir "data\gate_attempts\$DateStamp-$GateId.json"
    $deadline = (Get-Date).AddSeconds($GateDeadlineSec)
    Write-Log "$GateId autonomous gate start (budget=deadline+typed_repair_ledger, signature_repair=1, state=$statePath)"
    Update-RunnerProgress -Phase 'gate' -Step "$GateId autonomous gate start" -GateId $GateId -Category $Category -DeadlineAt $deadline.ToString('yyyy-MM-ddTHH:mm:ss.fffK')
    $gateRc = Invoke-PythonGateWithRepair -GateId $GateId -Category $Category -PythonArgs $PythonArgs -Artifacts $Artifacts -DeadlineAt $deadline -NoRepair:$NoRepair
    if ($gateRc -eq 0) {
        Write-Log "$GateId autonomous gate OK"
        Update-RunnerProgress -Phase 'gate' -Step "$GateId autonomous gate OK" -GateId $GateId -Category $Category -DeadlineAt $deadline.ToString('yyyy-MM-ddTHH:mm:ss.fffK')
        return 0
    }
    if ($gateRc -eq 124 -or (Get-Date) -ge $deadline) {
        Write-Log "$GateId autonomous gate timeout (rc=$gateRc, deadline=$($deadline.ToString('yyyy-MM-ddTHH:mm:ss.fffK')))"
        Set-RunnerState -Status 'blocked_gate_timeout' -Message "$GateId autonomous gate timeout" -ExitCode 124 -Phase 'gate' -GateId $GateId -Category $Category -DeadlineAt $deadline.ToString('yyyy-MM-ddTHH:mm:ss.fffK')
        return 124
    }
    Write-Log "$GateId autonomous gate failed (rc=$gateRc, state=$statePath)"
    return $gateRc
}

function Preserve-UnverifiedGeneratedArtifacts {
    # fallback 公開時も、復旧可能な当日 digest/data は削除しない。
    # 未検証 artifact は build\quarantine\$DateStamp に退避し、tracked 差分だけ HEAD に戻す。
    # 虚偽記事など本当に破棄が必要な場合は、gate の理由に応じた個別 quarantine / 修復で扱う。
    $quarantineDir = Join-Path $RepoDir "build\quarantine\$DateStamp"
    New-Item -ItemType Directory -Force -Path $quarantineDir | Out-Null
    $generatedPaths = @(
        'data/articles.jsonl',
        'data/_status.md',
        "data/gate_attempts/$DateStamp.json",
        "data/search_audit/$DateStamp",
        "digest/AI/$DateStamp-AI.md",
        "digest/Economy/$DateStamp-Economy.md",
        "digest/FX/$DateStamp-FX.md",
        "digest/Game/$DateStamp-Game.md",
        "digest/IT-Consulting/$DateStamp-IT-Consulting.md",
        "digest/Manufacturing/$DateStamp-Manufacturing.md",
        "digest/Mobility/$DateStamp-Mobility.md",
        "digest/Summary/$DateStamp.md",
        "digest/DeepDive/$DateStamp-DeepDive.md"
    )

    foreach ($rel in $generatedPaths) {
        $full = Join-Path $RepoDir $rel
        if (Test-Path $full) {
            $dest = Join-Path $quarantineDir ($rel -replace '[:\\/]+', '__')
            Copy-Item -LiteralPath $full -Destination $dest -Recurse -Force
            Write-Log "preserved unverified generated artifact: $rel -> $dest"
        }
        $tracked = & $GitExe @GitSafeArgs -C $RepoDir ls-files -- $rel
        if ($tracked) {
            Invoke-Logged { & $GitExe @GitSafeArgs -C $RepoDir checkout -- $rel }
            if ($LASTEXITCODE -ne 0) {
                Write-Log "WARN: failed to restore tracked generated artifact: $rel (rc=$LASTEXITCODE)"
            }
        }
    }
}

function Resolve-LastGoodDocsRef {
    $shortDate = $DateStamp.Substring(5)
    $compactDate = $DateStamp.Replace('-', '')
    $history = & $GitExe @GitSafeArgs -C $RepoDir log "--format=%H`t%s" -- 'docs/index.html'
    foreach ($entry in $history) {
        $parts = $entry -split "`t", 2
        if ($parts.Count -lt 2) { continue }
        $hash = $parts[0]
        $subject = $parts[1]
        if ($subject -like '*publish fallback*') { continue }
        if ($subject -like "*$DateStamp*") { continue }
        if ($subject -like "*$shortDate*") { continue }
        if ($subject -like "*$compactDate*") { continue }
        return $hash
    }
    return ''
}

function Invoke-FallbackPublish {
    param([string] $Reason)
    $message = "fallback publish is disabled in the daily runner path (reason=$Reason)"
    Write-Log "ERROR: $message"
    Exit-Runner -Status 'forbidden_fallback' -Message $message -ExitCode 73
    return
}

function Invoke-AutonomousCompletionPolicy {
    param(
        [Parameter(Mandatory=$true)][ValidateSet('content', 'artifact', 'local-tool', 'external', 'publish', 'distribution')][string] $FailureKind,
        [string] $GateId = '',
        [string] $Reason = '',
        [int] $ExitCode = 1
    )
    $gateLabel = $GateId
    if (-not $gateLabel) { $gateLabel = $FailureKind }
    $message = $Reason
    if (-not $message) { $message = "$gateLabel failed" }

    if ($FailureKind -eq 'external') {
        Write-Log "ERROR: external failure classified by autonomous policy (gate=$gateLabel, rc=$ExitCode): $message"
        Exit-Runner `
            -Status 'blocked_external_readiness' `
            -Message $message `
            -ExitCode $ExitCode `
            -ExternalKind $gateLabel `
            -ExternalSystem 'external' `
            -ExternalStatus "rc=$ExitCode" `
            -ExternalDetail $message
        return
    }
    if ($FailureKind -eq 'publish') {
        Write-Log "ERROR: publish failure classified by autonomous policy (gate=$gateLabel, rc=$ExitCode): $message"
        Exit-Runner -Status 'publish_failed' -Message $message -ExitCode $ExitCode
        return
    }
    if ($FailureKind -eq 'distribution') {
        Write-Log "ERROR: distribution failure classified by autonomous policy (gate=$gateLabel, rc=$ExitCode): $message"
        Exit-Runner -Status 'distribution_failed' -Message $message -ExitCode $ExitCode
        return
    }

    $internalMessage = "internal quality gate failed (kind=$FailureKind, gate=$gateLabel, rc=$ExitCode): $message"
    Write-Log "ERROR: $internalMessage"
    Exit-Runner -Status 'blocked_internal_quality_gate' -Message $internalMessage -ExitCode $ExitCode
}

function Write-RecoverOnlyInputManifest {
    $requiredArtifacts = @(Get-PublishInventoryArtifacts -Kind 'generated')
    $missingArtifacts = New-Object System.Collections.Generic.List[string]
    foreach ($rel in $requiredArtifacts) {
        $path = Join-Path $RepoDir $rel
        if (-not (Test-Path -LiteralPath $path)) {
            $missingArtifacts.Add([string] $rel)
        }
    }

    $repoHead = 'unknown'
    try {
        $head = (& $GitExe @GitSafeArgs -C $RepoDir rev-parse HEAD 2>$null)
        if ($LASTEXITCODE -eq 0 -and $head) {
            $repoHead = [string] $head
        }
    } catch {
        $repoHead = 'unknown'
    }

    $outDir = Join-Path $RepoDir 'build\recover-only'
    New-Item -ItemType Directory -Path $outDir -Force | Out-Null
    $manifestPath = Join-Path $outDir "$DateStamp.json"
    [ordered]@{
        date = $DateStamp
        mode = 'RecoverOnly'
        required_artifacts = @($requiredArtifacts)
        missing_artifacts = @($missingArtifacts.ToArray())
        repo_head = $repoHead
        state_file = $StateFile
        created_at = (Get-Date -Format 'yyyy-MM-ddTHH:mm:ss.fffK')
    } | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
    Write-Log "RecoverOnly input manifest written: $manifestPath"
    return $manifestPath
}

function Write-DistributionManifest {
    $prePublishCommit = ''
    try {
        $head = (& $GitExe @GitSafeArgs -C $RepoDir rev-parse HEAD 2>$null)
        if ($LASTEXITCODE -eq 0 -and $head) {
            $candidate = [string] $head
            if ($candidate -match '^[0-9a-fA-F]{40}$') {
                $prePublishCommit = $candidate
            }
        }
    } catch {
        $prePublishCommit = ''
    }
    if (-not $prePublishCommit) {
        Invoke-AutonomousCompletionPolicy -FailureKind 'distribution' -GateId 'distribution-manifest' -Reason 'distribution manifest pre_publish_commit unavailable' -ExitCode 1
    }

    $distributionDir = Join-Path $RepoDir 'data\distribution'
    New-Item -ItemType Directory -Path $distributionDir -Force | Out-Null
    $distributionSummary = Join-Path $distributionDir "$DateStamp.json"
    $distributionJson = [ordered]@{
        date = $DateStamp
        pre_publish_commit = $prePublishCommit
        publish_commit = ''
        publish_commit_resolution = 'post_push_verify'
        same_publish_contract = 'pre_publish_commit_must_equal_verified_publish_commit'
        primary_podcast_state = 'build/youtube-podcast/uploads.json'
        deepdive_podcast_state = 'build/youtube-podcast-deepdive/uploads.json'
        latest_audio_state = 'build/tts/latest_audio.json'
        deepdive_audio_state = 'build/tts/deepdive/latest_audio.json'
        generated_at = (Get-Date -Format 'yyyy-MM-ddTHH:mm:ss.fffK')
    } | ConvertTo-Json -Depth 4
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($distributionSummary, ($distributionJson + [Environment]::NewLine), $utf8NoBom)
    return $distributionSummary
}

function Test-DailyArtifactsExist {
    param([Parameter(Mandatory=$true)][string] $TargetDate)
    $patterns = @(
        "digest\*\$TargetDate-*.md",
        "digest\Summary\$TargetDate.md",
        "docs\$TargetDate\index.html",
        "build\reporter-artifacts\$TargetDate\*"
    )
    foreach ($pattern in $patterns) {
        $matches = Get-ChildItem -Path (Join-Path $RepoDir $pattern) -ErrorAction SilentlyContinue
        if ($matches) { return $true }
    }
    return $false
}

function Record-HighCostClaimFailure {
    param(
        [Parameter(Mandatory=$true)][string] $AdmissionPath,
        [Parameter(Mandatory=$true)][string] $ReservationPath,
        [Parameter(Mandatory=$true)][string] $BridgePath,
        [Parameter(Mandatory=$true)][string] $PythonPath,
        [Parameter(Mandatory=$true)][string] $RunnerExecutablePath,
        [Parameter(Mandatory=$true)][string] $FailureCode,
        [string] $Detail = ''
    )
    try {
        $admissionSha = if (Test-Path -LiteralPath $AdmissionPath -PathType Leaf) { Get-FileSha256Hex -Path $AdmissionPath } else { '' }
        $reservationSha = if (Test-Path -LiteralPath $ReservationPath -PathType Leaf) { Get-FileSha256Hex -Path $ReservationPath } else { '' }
        $detailSha = Get-StringSha256Hex -Text ([string]$Detail)
        $fingerprint = Get-StringSha256Hex -Text ("$FailureCode|$admissionSha|$reservationSha|$detailSha")
        $recordOutput = (& $PythonPath -I $BridgePath 'record-claim-failure' '--admission' $AdmissionPath '--reservation-receipt' $ReservationPath '--failure-code' $FailureCode '--failure-fingerprint' $fingerprint '--runner-executable' $RunnerExecutablePath '--authority-python-executable' $PythonPath '--current-runner-pid' $PID '--observed-at' ([DateTime]::UtcNow.ToString('o')) 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -ne 0) {
            Add-RunnerLogLine -Text "WARN: HIGH_COST_CLAIM_FAILURE_RECORD_FAILED exit=$LASTEXITCODE detail=$recordOutput"
            return $false
        } else {
            Add-RunnerLogLine -Text "WARN: HIGH_COST_CLAIM_FAILURE_RECORDED code=$FailureCode"
            return $true
        }
    } catch {
        try { Add-RunnerLogLine -Text "WARN: HIGH_COST_CLAIM_FAILURE_RECORD_EXCEPTION reason=$($_.Exception.Message)" } catch { }
        return $false
    }
}

function Clear-ScheduledHighCostAuthorityEnvironment {
    foreach ($name in @(
        'AIHARNESS_SCHEDULED_NEWS_GRASP_AUTHORITY',
        'AIHARNESS_SCHEDULED_TASK_IDENTITY',
        'AIHARNESS_SCHEDULED_ACTUAL_EVENT_HASH',
        'AIHARNESS_SCHEDULED_ISSUE_DATE',
        'AIHARNESS_SCHEDULED_OPERATION_KIND'
    )) {
        Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
    }
}

function Set-ScheduledHighCostAuthorityEnvironment {
    param(
        [Parameter(Mandatory=$true)] [object] $Admission,
        [Parameter(Mandatory=$true)] [string] $ExpectedOperationKind,
        [Parameter(Mandatory=$true)] [string] $ExpectedIssueDate
    )
    $taskIdentity = [string]$Admission.taskIdentity
    $actualEventHash = [string]$Admission.latestActualUserEventHash
    $admissionIssueDate = [string]$Admission.issueDate
    $admissionOperationKind = [string]$Admission.operationKind
    if (
        $ExpectedOperationKind -notin @('scheduled_production', 'scheduled_recovery') -or
        $admissionOperationKind -cne $ExpectedOperationKind -or
        $admissionIssueDate -cne $ExpectedIssueDate -or
        $taskIdentity -notmatch '^[0-9a-f]{64}$' -or
        $actualEventHash -notmatch '^[0-9a-f]{64}$'
    ) {
        Add-RunnerLogLine -Text 'ERROR: HIGH_COST_SCHEDULED_AUTHORITY_ENV_INVALID'
        Set-RunnerState -Status 'operation_rejected_high_cost_admission' -Message 'HIGH_COST_SCHEDULED_AUTHORITY_ENV_INVALID' -ExitCode 76
        exit 76
    }
    # 子のmodel wrapperでも high_cost_control_v2.resolve_canonical_authority()
    # が再評価されるため、封印済みscheduled identityを明示伝播する。
    # 伝播しない場合、子が対話用durable-goal DBへフォールバックし、正当な
    # ScheduledProduction/ScheduledRecoveryまで拒否してしまう。
    $env:AIHARNESS_SCHEDULED_NEWS_GRASP_AUTHORITY = '1'
    $env:AIHARNESS_SCHEDULED_TASK_IDENTITY = $taskIdentity
    $env:AIHARNESS_SCHEDULED_ACTUAL_EVENT_HASH = $actualEventHash
    $env:AIHARNESS_SCHEDULED_ISSUE_DATE = $admissionIssueDate
    $env:AIHARNESS_SCHEDULED_OPERATION_KIND = $admissionOperationKind
}

function Assert-HighCostOperationAdmission {
    Clear-ScheduledHighCostAuthorityEnvironment
    $script:ScheduledRecoveryStageBrokerPath = ''
    $script:ScheduledRecoveryStageDecisionReceiptPath = ''
    if ($SmokeTest -or $PreflightOnly -or $FinalizeVerifiedPublishManifest) { return }
    $modelSpawnBroker = [System.IO.Path]::GetFullPath($HighCostBudgetToolPath)
    if ((-not $HighCostWorkspaceRoot) -or (-not (Test-Path -LiteralPath $modelSpawnBroker -PathType Leaf))) {
        Add-RunnerLogLine -Text 'ERROR: HIGH_COST_OPERATION_ADMISSION_REQUIRED'
        Set-RunnerState -Status 'operation_rejected_high_cost_admission_required' -Message 'HIGH_COST_OPERATION_ADMISSION_REQUIRED; local critical path remains available' -ExitCode 76
        exit 76
    }
    $operationKind = 'scheduled_production'
    if ($RunIntent -eq 'ScheduledRecoveryFull' -or $RecoverOnly -or $ResumeFromPostDailyQuality -or $ResumeAfterDeepDive -or $ResumeFromStage) {
        $operationKind = 'scheduled_recovery'
    }
    if ($NoPublish) {
        $operationKind = 'full_e2e'
        if ($HighCostAdmissionPath) {
            Add-RunnerLogLine -Text 'ERROR: HIGH_COST_NOPUBLISH_SHARED_ADMISSION_FORBIDDEN'
            Set-RunnerState -Status 'operation_rejected_high_cost_mode_conflict' -Message 'HIGH_COST_NOPUBLISH_SHARED_ADMISSION_FORBIDDEN' -ExitCode 76
            exit 76
        }
        if ($HighCostClaimWitness) {
            Add-RunnerLogLine -Text 'ERROR: HIGH_COST_FINAL_CLAIM_WITNESS_FORBIDDEN'
            Set-RunnerState -Status 'operation_rejected_high_cost_mode_conflict' -Message 'HIGH_COST_FINAL_CLAIM_WITNESS_FORBIDDEN' -ExitCode 76
            exit 76
        }
        if ($ResumeFromStage) {
            Add-RunnerLogLine -Text 'ERROR: HIGH_COST_NOPUBLISH_RESUME_FORBIDDEN'
            Set-RunnerState -Status 'operation_rejected_high_cost_mode_conflict' -Message 'HIGH_COST_NOPUBLISH_RESUME_FORBIDDEN' -ExitCode 76
            exit 76
        }
        $script:HighCostAdmissionPath = ''
        # Preserve the caller-supplied authority path before clearing the
        # script-scope projection.  In PowerShell a script parameter lives in
        # script scope, so assigning the same $script: variable would erase
        # the input value used by this admission gate.
        $incomingHighCostParentAuthorityPath = [string]$HighCostParentAuthorityPath
        $expectedFullE2EAttemptId = "nopublish:$DateStamp"
        if ($HighCostAttemptId -and $HighCostAttemptId -cne $expectedFullE2EAttemptId) {
            Add-RunnerLogLine -Text 'ERROR: HIGH_COST_NOPUBLISH_ATTEMPT_ID_DRIFT'
            Set-RunnerState -Status 'operation_rejected_high_cost_mode_conflict' -Message 'HIGH_COST_NOPUBLISH_ATTEMPT_ID_DRIFT' -ExitCode 76
            exit 76
        }
        $script:HighCostAttemptId = $expectedFullE2EAttemptId
        $script:HighCostParentAuthorityPath = ''
        $script:HighCostParentAuthoritySha256 = ''
        if (-not $incomingHighCostParentAuthorityPath) {
            Add-RunnerLogLine -Text 'ERROR: HIGH_COST_PARENT_AUTHORITY_RECEIPT_REQUIRED'
            Set-RunnerState -Status 'operation_rejected_high_cost_admission_required' -Message 'HIGH_COST_PARENT_AUTHORITY_RECEIPT_REQUIRED' -ExitCode 76
            exit 76
        }
        if (-not $E2EFinalAdmissionPath -or -not $E2EFinalRunnerArgumentsPath -or
            -not $E2EFinalReservationReceiptPath -or -not $E2EFinalClaimReceiptPath) {
            Add-RunnerLogLine -Text 'ERROR: HIGH_COST_FINAL_ADMISSION_PATHS_REQUIRED'
            Set-RunnerState -Status 'operation_rejected_high_cost_admission_required' -Message 'HIGH_COST_FINAL_ADMISSION_PATHS_REQUIRED' -ExitCode 76
            exit 76
        }
        $parentAuthorityReceipt = [System.IO.Path]::GetFullPath($incomingHighCostParentAuthorityPath)
        $finalAdmissionReceipt = [System.IO.Path]::GetFullPath($E2EFinalAdmissionPath)
        $finalRunnerArguments = [System.IO.Path]::GetFullPath($E2EFinalRunnerArgumentsPath)
        $finalReservationReceipt = [System.IO.Path]::GetFullPath($E2EFinalReservationReceiptPath)
        $finalClaimReceipt = [System.IO.Path]::GetFullPath($E2EFinalClaimReceiptPath)
        try {
            $finalAdmissionValue = Get-Content -LiteralPath $finalAdmissionReceipt -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
            $finalClaimWitness = [System.IO.Path]::GetFullPath([string]$finalAdmissionValue.expectedClaimWitnessPath)
        } catch {
            Add-RunnerLogLine -Text "ERROR: HIGH_COST_FINAL_CLAIM_WITNESS_PATH_INVALID reason=$($_.Exception.Message)"
            Set-RunnerState -Status 'operation_rejected_high_cost_admission' -Message 'HIGH_COST_FINAL_CLAIM_WITNESS_PATH_INVALID' -ExitCode 76
            exit 76
        }
        if ((-not (Test-Path -LiteralPath $parentAuthorityReceipt -PathType Leaf)) -or
            (-not (Test-Path -LiteralPath $finalAdmissionReceipt -PathType Leaf)) -or
            (-not (Test-Path -LiteralPath $finalRunnerArguments -PathType Leaf)) -or
            (-not (Test-Path -LiteralPath $finalReservationReceipt -PathType Leaf)) -or
            (-not (Test-Path -LiteralPath $E2EFinalAdmissionBridge -PathType Leaf))) {
            Add-RunnerLogLine -Text 'ERROR: HIGH_COST_FINAL_ADMISSION_PATHS_REQUIRED'
            Set-RunnerState -Status 'operation_rejected_high_cost_admission_required' -Message 'HIGH_COST_FINAL_ADMISSION_PATHS_REQUIRED' -ExitCode 76
            exit 76
        }
        $script:E2EFinalAdmissionPath = $finalAdmissionReceipt
        $script:E2EFinalRunnerArgumentsPath = $finalRunnerArguments
        $script:E2EFinalReservationReceiptPath = $finalReservationReceipt
        $script:E2EFinalClaimReceiptPath = $finalClaimReceipt
        if ((Test-Path -LiteralPath $finalClaimReceipt) -or (Test-Path -LiteralPath $finalClaimWitness)) {
            Add-RunnerLogLine -Text 'ERROR: HIGH_COST_FINAL_CLAIM_OUTPUT_EXISTS'
            Set-RunnerState -Status 'operation_rejected_high_cost_admission' -Message 'HIGH_COST_FINAL_CLAIM_OUTPUT_EXISTS' -ExitCode 76
            exit 76
        }
        try {
            $HighCostWorkspaceRoot = [System.IO.Path]::GetFullPath((Resolve-Path -LiteralPath $HighCostWorkspaceRoot -ErrorAction Stop).Path)
            $RepoDir = [System.IO.Path]::GetFullPath((Resolve-Path -LiteralPath $RepoDir -ErrorAction Stop).Path)
            $highCostAuthorityTool = [System.IO.Path]::GetFullPath((Resolve-Path -LiteralPath (Join-Path $HighCostWorkspaceRoot 'tools\harness\high_cost_operation_budget.py') -ErrorAction Stop).Path)
            $authorityPythonPath = [System.IO.Path]::GetFullPath((Resolve-Path -LiteralPath $PyExe -ErrorAction Stop).Path)
            $powerShellCommand = if (Test-Path -LiteralPath $PowerShellExe -PathType Leaf) {
                Get-Item -LiteralPath $PowerShellExe -ErrorAction Stop
            } else {
                Get-Command $PowerShellExe -CommandType Application -ErrorAction Stop | Select-Object -First 1
            }
            $powerShellCommandPath = if ($powerShellCommand -is [System.IO.FileInfo]) {
                $powerShellCommand.FullName
            } elseif ($powerShellCommand.Source) {
                $powerShellCommand.Source
            } else {
                $powerShellCommand.Path
            }
            $runnerExecutablePath = [System.IO.Path]::GetFullPath([string]$powerShellCommandPath)
            if (-not (Test-Path -LiteralPath $runnerExecutablePath -PathType Leaf)) {
                throw 'runner executable is not a regular file'
            }
        } catch {
            Add-RunnerLogLine -Text "ERROR: HIGH_COST_FINAL_EXECUTION_IDENTITY_INVALID reason=$($_.Exception.Message)"
            Set-RunnerState -Status 'operation_rejected_high_cost_admission' -Message 'HIGH_COST_FINAL_EXECUTION_IDENTITY_INVALID' -ExitCode 76
            exit 76
        }
        $claimFailureStatusOutput = (& $authorityPythonPath -I $E2EFinalAdmissionBridge 'claim-failure-status' '--admission' $finalAdmissionReceipt '--reservation-receipt' $finalReservationReceipt 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -ne 0) {
            Add-RunnerLogLine -Text "ERROR: HIGH_COST_FINAL_CLAIM_FAILURE_STATUS_UNAVAILABLE exit=$LASTEXITCODE"
            Set-RunnerState -Status 'operation_rejected_high_cost_admission' -Message 'HIGH_COST_FINAL_CLAIM_FAILURE_STATUS_UNAVAILABLE' -ExitCode 76
            exit 76
        }
        try {
            $claimFailureStatus = $claimFailureStatusOutput | ConvertFrom-Json -ErrorAction Stop
            if ([string]$claimFailureStatus.state -eq 'claim_failure_recorded') {
                Add-RunnerLogLine -Text "ERROR: HIGH_COST_FINAL_RUNNER_CLAIM_TERMINAL code=$([string]$claimFailureStatus.failureCode)"
                Set-RunnerState -Status 'operation_rejected_high_cost_admission' -Message 'HIGH_COST_FINAL_RUNNER_CLAIM_TERMINAL' -ExitCode 76
                exit 76
            }
            if ([string]$claimFailureStatus.state -ne 'none') { throw 'HIGH_COST_FINAL_CLAIM_FAILURE_STATUS_INVALID' }
        } catch {
            Add-RunnerLogLine -Text "ERROR: HIGH_COST_FINAL_CLAIM_FAILURE_STATUS_INVALID reason=$($_.Exception.Message)"
            Set-RunnerState -Status 'operation_rejected_high_cost_admission' -Message 'HIGH_COST_FINAL_CLAIM_FAILURE_STATUS_INVALID' -ExitCode 76
            exit 76
        }
        $canonicalValidationOutput = (& $authorityPythonPath -I $highCostAuthorityTool 'validate-activated' '--workspace-root' $HighCostWorkspaceRoot '--admission' $parentAuthorityReceipt '--expected-attempt-kind' 'full_e2e' '--expected-execution-root' $RepoDir 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -ne 0) {
            if (-not (Record-HighCostClaimFailure -AdmissionPath $finalAdmissionReceipt -ReservationPath $finalReservationReceipt -BridgePath $E2EFinalAdmissionBridge -PythonPath $authorityPythonPath -RunnerExecutablePath $runnerExecutablePath -FailureCode 'HIGH_COST_PARENT_AUTHORITY_RECEIPT_INVALID' -Detail $canonicalValidationOutput)) {
                Add-RunnerLogLine -Text 'ERROR: HIGH_COST_CLAIM_FAILURE_RECORD_UNAVAILABLE'
                Set-RunnerState -Status 'operation_rejected_high_cost_admission' -Message 'HIGH_COST_CLAIM_FAILURE_RECORD_UNAVAILABLE' -ExitCode 76
                exit 76
            }
            Add-RunnerLogLine -Text "ERROR: HIGH_COST_PARENT_AUTHORITY_RECEIPT_INVALID exit=$LASTEXITCODE"
            Set-RunnerState -Status 'operation_rejected_high_cost_admission' -Message 'HIGH_COST_PARENT_AUTHORITY_RECEIPT_INVALID' -ExitCode 76
            exit 76
        }
        try {
            $parentAuthority = $canonicalValidationOutput | ConvertFrom-Json -ErrorAction Stop
            if (
                [string]$parentAuthority.schemaVersion -ne 'HIGH_COST_OPERATION_ADMISSION_V1' -or
                [string]$parentAuthority.state -ne 'activated' -or
                [string]$parentAuthority.attemptKind -ne 'full_e2e' -or
                [System.IO.Path]::GetFullPath([string]$parentAuthority.executionRoot) -ne [System.IO.Path]::GetFullPath($RepoDir)
            ) {
                throw 'HIGH_COST_PARENT_AUTHORITY_IDENTITY_MISMATCH'
            }
            $script:HighCostParentAuthorityPath = $parentAuthorityReceipt
            $script:HighCostParentAuthoritySha256 = Get-FileSha256Hex -Path $parentAuthorityReceipt
        } catch {
            if (-not (Record-HighCostClaimFailure -AdmissionPath $finalAdmissionReceipt -ReservationPath $finalReservationReceipt -BridgePath $E2EFinalAdmissionBridge -PythonPath $authorityPythonPath -RunnerExecutablePath $runnerExecutablePath -FailureCode 'HIGH_COST_PARENT_AUTHORITY_RECEIPT_INVALID' -Detail $_.Exception.Message)) {
                Add-RunnerLogLine -Text 'ERROR: HIGH_COST_CLAIM_FAILURE_RECORD_UNAVAILABLE'
                Set-RunnerState -Status 'operation_rejected_high_cost_admission' -Message 'HIGH_COST_CLAIM_FAILURE_RECORD_UNAVAILABLE' -ExitCode 76
                exit 76
            }
            Add-RunnerLogLine -Text "ERROR: HIGH_COST_PARENT_AUTHORITY_RECEIPT_INVALID reason=$($_.Exception.Message)"
            Set-RunnerState -Status 'operation_rejected_high_cost_admission' -Message 'HIGH_COST_PARENT_AUTHORITY_RECEIPT_INVALID' -ExitCode 76
            exit 76
        }
        $claimNonce = Get-StringSha256Hex -Text "$script:HighCostAttemptId|$PID|$RunId"
        $claimOutput = (& $authorityPythonPath -I $E2EFinalAdmissionBridge 'claim-runner' '--admission' $finalAdmissionReceipt '--runner-arguments-file' $finalRunnerArguments '--parent-authority' $parentAuthorityReceipt '--reservation-receipt' $finalReservationReceipt '--claim-output' $finalClaimReceipt '--runner-executable' $runnerExecutablePath '--authority-python-executable' $authorityPythonPath '--current-runner-pid' $PID '--claim-nonce' $claimNonce 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -ne 0) {
            if (-not (Record-HighCostClaimFailure -AdmissionPath $finalAdmissionReceipt -ReservationPath $finalReservationReceipt -BridgePath $E2EFinalAdmissionBridge -PythonPath $authorityPythonPath -RunnerExecutablePath $runnerExecutablePath -FailureCode 'HIGH_COST_FINAL_RUNNER_CLAIM_REJECTED' -Detail $claimOutput)) {
                Add-RunnerLogLine -Text 'ERROR: HIGH_COST_CLAIM_FAILURE_RECORD_UNAVAILABLE'
                Set-RunnerState -Status 'operation_rejected_high_cost_admission' -Message 'HIGH_COST_CLAIM_FAILURE_RECORD_UNAVAILABLE' -ExitCode 76
                exit 76
            }
            Add-RunnerLogLine -Text "ERROR: HIGH_COST_FINAL_RUNNER_CLAIM_REJECTED exit=$LASTEXITCODE"
            Set-RunnerState -Status 'operation_rejected_high_cost_admission' -Message 'HIGH_COST_FINAL_RUNNER_CLAIM_REJECTED' -ExitCode 76
            exit 76
        }
        try {
            if ($claimOutput.Length -gt 262144) { throw 'HIGH_COST_FINAL_RUNNER_CLAIM_OUTPUT_UNBOUNDED' }
            $claimReceipt = $claimOutput | ConvertFrom-Json -ErrorAction Stop
            if (
                [string]$claimReceipt.schemaVersion -ne 'E2E_FINAL_RUNNER_CLAIM_V1' -or
                [string]$claimReceipt.state -ne 'runner_claimed' -or
                [System.IO.Path]::GetFullPath([string]$claimReceipt.admissionPath) -ne $finalAdmissionReceipt -or
                [System.IO.Path]::GetFullPath([string]$claimReceipt.reservationReceiptPath) -ne $finalReservationReceipt -or
                [string]$claimReceipt.runnerPid -ne [string]$PID
            ) { throw 'HIGH_COST_FINAL_RUNNER_CLAIM_OUTPUT_INVALID' }
        } catch {
            Add-RunnerLogLine -Text "ERROR: HIGH_COST_FINAL_RUNNER_CLAIM_OUTPUT_INVALID reason=$($_.Exception.Message)"
            Set-RunnerState -Status 'operation_rejected_high_cost_admission' -Message 'HIGH_COST_FINAL_RUNNER_CLAIM_OUTPUT_INVALID' -ExitCode 76
            exit 76
        }
        $claimWitnessOutput = (& $authorityPythonPath -I $E2EFinalAdmissionBridge 'write-runner-claim-witness' '--admission' $finalAdmissionReceipt '--runner-arguments-file' $finalRunnerArguments '--parent-authority' $parentAuthorityReceipt '--reservation-receipt' $finalReservationReceipt '--claim-receipt' $finalClaimReceipt '--witness-output' $finalClaimWitness '--runner-executable' $runnerExecutablePath '--authority-python-executable' $authorityPythonPath '--expected-owner-pid' $PID 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -ne 0) {
            Add-RunnerLogLine -Text "ERROR: HIGH_COST_FINAL_RUNNER_CLAIM_WITNESS_REJECTED exit=$LASTEXITCODE"
            Set-RunnerState -Status 'operation_rejected_high_cost_admission' -Message 'HIGH_COST_FINAL_RUNNER_CLAIM_WITNESS_REJECTED' -ExitCode 76
            exit 76
        }
        try {
            if ($claimWitnessOutput.Length -gt 131072) { throw 'HIGH_COST_FINAL_RUNNER_CLAIM_WITNESS_UNBOUNDED' }
            $claimWitness = $claimWitnessOutput | ConvertFrom-Json -ErrorAction Stop
            foreach ($field in @('claimId', 'claimReceiptPath', 'claimReceiptSha256', 'ownerProcessIdentity', 'attemptKey', 'admissionId')) {
                if ($null -eq $claimWitness.$field) { throw 'HIGH_COST_FINAL_RUNNER_CLAIM_WITNESS_INVALID' }
            }
            if ([System.IO.Path]::GetFullPath([string]$claimWitness.claimReceiptPath) -ne $finalClaimReceipt) {
                throw 'HIGH_COST_FINAL_RUNNER_CLAIM_WITNESS_PATH_DRIFT'
            }
            if (-not (Test-Path -LiteralPath $finalClaimWitness -PathType Leaf)) {
                throw 'HIGH_COST_FINAL_RUNNER_CLAIM_WITNESS_FILE_MISSING'
            }
            $script:HighCostClaimWitness = $finalClaimWitness
        } catch {
            Add-RunnerLogLine -Text "ERROR: HIGH_COST_FINAL_RUNNER_CLAIM_WITNESS_INVALID reason=$($_.Exception.Message)"
            Set-RunnerState -Status 'operation_rejected_high_cost_admission' -Message 'HIGH_COST_FINAL_RUNNER_CLAIM_WITNESS_INVALID' -ExitCode 76
            exit 76
        }
        $script:HighCostExpectedOperationKind = $operationKind
        $script:HighCostExpectedIssueDate = ''
        return
    }

    if ($HighCostParentAuthorityPath -or $E2EFinalAdmissionPath -or $E2EFinalRunnerArgumentsPath -or
        $E2EFinalReservationReceiptPath -or $E2EFinalClaimReceiptPath -or $HighCostClaimWitness) {
        Add-RunnerLogLine -Text 'ERROR: HIGH_COST_SCHEDULED_FINAL_ADMISSION_FORBIDDEN'
        Set-RunnerState -Status 'operation_rejected_high_cost_mode_conflict' -Message 'HIGH_COST_SCHEDULED_FINAL_ADMISSION_FORBIDDEN' -ExitCode 76
        exit 76
    }

    if ($HighCostAdmissionPath) {
        $script:HighCostAdmissionPath = $HighCostAdmissionPath
    }

    $stageDecisionReceipt = ''
    $script:UsesHighCostContinuationAdmission = $false
    $continuationAdmissionValidated = $false
    if ($ResumeFromStage) {
        if ($HighCostAdmissionPath) {
            # A continuation receipt is not a caller bypass.  It must be
            # chained to the scheduled authority receipt and revalidated by
            # the product-local closed-schema validator before any authority
            # environment is exported or this gate returns.
            if ([string]::IsNullOrWhiteSpace([string]$ScheduledAuthorityEvidencePath)) {
                Add-RunnerLogLine -Text 'ERROR: SCHEDULED_RECOVERY_CONTINUATION_SCHEDULED_AUTHORITY_REQUIRED'
                Set-RunnerState -Status 'operation_rejected_high_cost_admission' -Message 'SCHEDULED_RECOVERY_CONTINUATION_SCHEDULED_AUTHORITY_REQUIRED' -ExitCode 76
                exit 76
            }
            if (-not (Test-Path -LiteralPath $ScheduledAuthorityEvidencePath -PathType Leaf)) {
                Add-RunnerLogLine -Text 'ERROR: SCHEDULED_RECOVERY_CONTINUATION_SCHEDULED_AUTHORITY_MISSING'
                Set-RunnerState -Status 'operation_rejected_high_cost_admission' -Message 'SCHEDULED_RECOVERY_CONTINUATION_SCHEDULED_AUTHORITY_MISSING' -ExitCode 76
                exit 76
            }
            if (-not (Test-Path -LiteralPath $HighCostAdmissionPath -PathType Leaf)) {
                Add-RunnerLogLine -Text 'ERROR: SCHEDULED_RECOVERY_CONTINUATION_SOURCE_ADMISSION_MISSING'
                Set-RunnerState -Status 'operation_rejected_high_cost_admission' -Message 'SCHEDULED_RECOVERY_CONTINUATION_SOURCE_ADMISSION_MISSING' -ExitCode 76
                exit 76
            }
            try {
                $authority = Get-Content -LiteralPath $ScheduledAuthorityEvidencePath -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
                $authorityReceiptSha256 = [string]$authority.receiptSha256
                if ($authorityReceiptSha256 -notmatch '^[0-9a-f]{64}$') { throw 'scheduled authority receiptSha256 is invalid' }
                $continuationValidationOutput = (& $PyExe -I -B (Join-Path $RepoDir 'tools\news_grasp_operational_contract.py') 'validate-scheduled-admission' '--path' ([System.IO.Path]::GetFullPath($HighCostAdmissionPath)) '--expected-operation-kind' 'scheduled_recovery' '--expected-issue-date' $DateStamp '--expected-operation-authority-sha256' $authorityReceiptSha256 2>&1 | Out-String).Trim()
                if ($LASTEXITCODE -ne 0) { throw "product-local scheduled admission validation failed: $continuationValidationOutput" }
                $continuationAdmission = $continuationValidationOutput | ConvertFrom-Json -ErrorAction Stop
                $requiredLineageFields = @('resumeStage', 'allowedModelRoutes', 'sourceAdmissionReceiptSha256', 'sourceRunId', 'sourceRunnerStateSha256', 'sourceTerminalStatus')
                foreach ($field in $requiredLineageFields) {
                    if ($null -eq $continuationAdmission.$field) { throw "continuation lineage field missing: $field" }
                }
                $expectedRoutes = switch ($ResumeFromStage) {
                    'post-reporter' { @('newsroom_editor', 'deepdive', 'repair:generation-quality') }
                    'editor' { @('newsroom_editor', 'deepdive', 'repair:generation-quality') }
                    'deepdive' { @('deepdive') }
                    'post-daily-quality' { @('deepdive') }
                    'post-deepdive' { @() }
                    'generation-quality-repair' { @('repair:generation-quality') }
                    default { throw 'continuation resume stage is invalid' }
                }
                $actualRoutes = @($continuationAdmission.allowedModelRoutes | ForEach-Object { [string]$_ })
                if (
                    [string]$continuationAdmission.schemaVersion -cne 'HIGH_COST_SCHEDULED_RECOVERY_CONTINUATION_V1' -or
                    [string]$continuationAdmission.operationKind -cne 'scheduled_recovery' -or
                    [string]$continuationAdmission.issueDate -cne $DateStamp -or
                    [string]$continuationAdmission.operationAuthoritySha256 -cne $authorityReceiptSha256 -or
                    [string]$continuationAdmission.resumeStage -cne $ResumeFromStage -or
                    (@($actualRoutes) -join "`n") -cne (@($expectedRoutes) -join "`n") -or
                    [string]$continuationAdmission.sourceAdmissionReceiptSha256 -notmatch '^[0-9a-f]{64}$' -or
                    [string]$continuationAdmission.sourceRunId -notmatch '^[0-9a-f]{32}$' -or
                    [string]$continuationAdmission.sourceRunnerStateSha256 -notmatch '^[0-9a-f]{64}$' -or
                    [string]$continuationAdmission.sourceTerminalStatus -notmatch '^(blocked|failed|error)[a-z0-9_]*$'
                ) { throw 'continuation lineage fields are invalid or drifted' }
            } catch {
                Add-RunnerLogLine -Text "ERROR: HIGH_COST_SCHEDULED_ADMISSION_INVALID reason=$($_.Exception.Message)"
                Set-RunnerState -Status 'operation_rejected_high_cost_admission' -Message 'HIGH_COST_SCHEDULED_ADMISSION_INVALID' -ExitCode 76
                exit 76
            }
            # ここでは継続receiptをauthorityへ昇格させない。既存の
            # RecoveryDecisionPath検証とfresh broker admissionへ必ず流す。
            $continuationAdmissionValidated = $true
        }
        if ($RunIntent -ne 'ScheduledRecoveryFull' -or (-not $RecoveryDecisionPath) -or (-not $ScheduledAuthorityEvidencePath)) {
            Add-RunnerLogLine -Text 'ERROR: SCHEDULED_RECOVERY_CONTINUATION_SOURCE_ADMISSION_REQUIRED'
            Set-RunnerState -Status 'operation_rejected_high_cost_admission' -Message 'SCHEDULED_RECOVERY_CONTINUATION_SOURCE_ADMISSION_REQUIRED' -ExitCode 76
            exit 76
        }
        $decisionJson = (& $PyExe '-m' 'tools.news_grasp_daily_control' 'validate-decision' '--path' $RecoveryDecisionPath 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -ne 0) {
            Add-RunnerLogLine -Text "ERROR: RECOVERY_DECISION_INVALID exit=$LASTEXITCODE"
            Set-RunnerState -Status 'operation_rejected_high_cost_admission' -Message 'RECOVERY_DECISION_INVALID' -ExitCode 76
            exit 76
        }
        try {
            $decision = $decisionJson | ConvertFrom-Json -ErrorAction Stop
            $stageDecisionReceipt = [System.IO.Path]::GetFullPath([string]$decision.brokerStageDecisionPath)
            if (
                [string]$decision.issueDate -ne $DateStamp -or
                [string]$decision.runIntent -ne 'ScheduledRecoveryFull' -or
                [string]$decision.recoveryBranch -ne 'ResumeFromStage' -or
                [string]$decision.resumeStage -ne $ResumeFromStage -or
                [System.IO.Path]::GetFullPath([string]$decision.scheduledAuthorityEvidencePath) -ne [System.IO.Path]::GetFullPath($ScheduledAuthorityEvidencePath) -or
                (-not (Test-Path -LiteralPath $stageDecisionReceipt -PathType Leaf)) -or
                (Get-FileSha256Hex -Path $stageDecisionReceipt) -ne [string]$decision.brokerStageDecisionSha256
            ) {
                throw 'RECOVERY_DECISION_BRANCH_MISMATCH'
            }
            $HighCostAdmissionPath = ''
        } catch {
            Add-RunnerLogLine -Text "ERROR: RECOVERY_DECISION_BRANCH_MISMATCH reason=$($_.Exception.Message)"
            Set-RunnerState -Status 'operation_rejected_high_cost_admission' -Message 'RECOVERY_DECISION_BRANCH_MISMATCH' -ExitCode 76
            exit 76
        }
    }

    if (-not $ScheduledAuthorityEvidencePath) {
        if ($operationKind -eq 'scheduled_production') {
            $ScheduledAuthorityEvidencePath = Join-Path $env:USERPROFILE "bin\news-grasp-authority\$DateStamp-launch-permit.json"
        } else {
            Add-RunnerLogLine -Text 'ERROR: SCHEDULED_OPERATION_AUTHORITY_REQUIRED'
            Set-RunnerState -Status 'operation_rejected_high_cost_admission' -Message 'SCHEDULED_OPERATION_AUTHORITY_REQUIRED' -ExitCode 76
            exit 76
        }
    }
    if (-not (Test-Path -LiteralPath $ScheduledAuthorityEvidencePath -PathType Leaf)) {
        Add-RunnerLogLine -Text 'ERROR: SCHEDULED_OPERATION_AUTHORITY_REQUIRED'
        Set-RunnerState -Status 'operation_rejected_high_cost_admission' -Message 'SCHEDULED_OPERATION_AUTHORITY_REQUIRED' -ExitCode 76
        exit 76
    }
    $taskActionSha256 = Get-ScheduledTaskActionSha256
    $runnerSha256 = Get-FileSha256Hex -Path $PSCommandPath
    $admissionDir = Join-Path $RepoDir "build\high-cost-operation-admissions\$DateStamp"
    New-Item -ItemType Directory -Path $admissionDir -Force | Out-Null
    $admissionReceipt = Join-Path $admissionDir "$RunId-$operationKind.json"
    if ($HighCostAdmissionPath) {
        if (-not (Test-Path -LiteralPath $HighCostAdmissionPath -PathType Leaf)) {
            Add-RunnerLogLine -Text 'ERROR: HIGH_COST_SCHEDULED_CALLER_RECEIPT_MISSING'
            Set-RunnerState -Status 'operation_rejected_high_cost_admission' -Message 'HIGH_COST_SCHEDULED_CALLER_RECEIPT_MISSING' -ExitCode 76
            exit 76
        }
        $admissionJson = (Get-Content -LiteralPath $HighCostAdmissionPath -Raw -Encoding UTF8).Trim()
        $admissionValidationPath = [System.IO.Path]::GetFullPath($HighCostAdmissionPath)
    } else {
        $admissionJson = (& $PyExe -I $modelSpawnBroker 'admit' '--operation-kind' $operationKind '--attempt-id' $DateStamp '--issue-date' $DateStamp '--authority-evidence' $ScheduledAuthorityEvidencePath '--expected-task-action-sha256' $taskActionSha256 '--expected-runner-sha256' $runnerSha256 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -ne 0) {
            Add-RunnerLogLine -Text "ERROR: HIGH_COST_OPERATION_ADMISSION_REJECTED exit=$LASTEXITCODE"
            Set-RunnerState -Status 'operation_rejected_high_cost_admission' -Message 'HIGH_COST_OPERATION_ADMISSION_REJECTED; local critical path remains available' -ExitCode 76
            exit 76
        }
        # brokerのstdoutはproduct-local validatorでGreenになるまでcanonical
        # receiptへcopyせず、一時候補だけを検証する。
        $admissionValidationPath = Join-Path $admissionDir ".${RunId}-${operationKind}.candidate.json"
        [System.IO.File]::WriteAllText($admissionValidationPath, ($admissionJson + [Environment]::NewLine), [System.Text.UTF8Encoding]::new($false))
    }
    try {
        $authority = Get-Content -LiteralPath $ScheduledAuthorityEvidencePath -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
        $validationOutput = (& $PyExe -I -B (Join-Path $RepoDir 'tools\news_grasp_operational_contract.py') 'validate-scheduled-admission' '--path' $admissionValidationPath '--expected-operation-kind' $operationKind '--expected-issue-date' $DateStamp '--expected-operation-authority-sha256' ([string]$authority.receiptSha256) 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -ne 0) {
            throw "product-local scheduled admission validation failed: $validationOutput"
        }
        $admissionJson = $validationOutput
        $admission = $admissionJson | ConvertFrom-Json -ErrorAction Stop
        if (
            [string]$admission.schemaVersion -notin @('HIGH_COST_SCHEDULED_OPERATION_ADMISSION_V1', 'HIGH_COST_SCHEDULED_RECOVERY_CONTINUATION_V1', 'HIGH_COST_SCHEDULED_INCIDENT_REPAIR_V1') -or
            $admission.operationKind -ne $operationKind -or
            $admission.issueDate -ne $DateStamp -or
            $admission.operationAuthoritySha256 -ne $authority.receiptSha256
        ) {
            throw 'scheduled admission identity drift'
        }
        [System.IO.File]::WriteAllText($admissionReceipt, ($admissionJson + [Environment]::NewLine), [System.Text.UTF8Encoding]::new($false))
        if (-not $HighCostAdmissionPath -and (Test-Path -LiteralPath $admissionValidationPath -PathType Leaf)) {
            Remove-Item -LiteralPath $admissionValidationPath -Force -ErrorAction SilentlyContinue
        }
        $script:HighCostAdmissionPath = $admissionReceipt
        $script:HighCostExpectedOperationKind = $operationKind
        $script:HighCostExpectedIssueDate = $DateStamp
        Set-ScheduledHighCostAuthorityEnvironment -Admission $admission -ExpectedOperationKind $operationKind -ExpectedIssueDate $DateStamp
        if ($ResumeFromStage -and $stageDecisionReceipt) {
            $script:ScheduledRecoveryStageBrokerPath = [System.IO.Path]::GetFullPath($modelSpawnBroker)
            $script:ScheduledRecoveryStageDecisionReceiptPath = [System.IO.Path]::GetFullPath($stageDecisionReceipt)
        }
        if ($continuationAdmissionValidated) {
            # cutoff bypassはcontinuation単体ではなく、decision/fresh brokerの
            # 検証済み連鎖が成立した後だけ有効にする。
            $script:UsesHighCostContinuationAdmission = $true
        }
    } catch {
        if ($admissionValidationPath -and (Test-Path -LiteralPath $admissionValidationPath -PathType Leaf)) {
            Remove-Item -LiteralPath $admissionValidationPath -Force -ErrorAction SilentlyContinue
        }
        Add-RunnerLogLine -Text "ERROR: HIGH_COST_SCHEDULED_ADMISSION_INVALID reason=$($_.Exception.Message)"
        Set-RunnerState -Status 'operation_rejected_high_cost_admission' -Message 'HIGH_COST_SCHEDULED_ADMISSION_INVALID' -ExitCode 76
        exit 76
    }
}

# ===== 外部制御面pure readiness =====
function Get-NewsGraspExternalControlPlaneReadiness {
    $probeScript = Join-Path $RepoDir 'tools\news_grasp_external_control.py'
    if (-not (Test-Path -LiteralPath $probeScript -PathType Leaf)) {
        return [ordered]@{ status = 'unavailable'; reasonCode = 'EXTERNAL_CONTROL_PLANE_CONSUMER_MISSING'; modelLaunchCount = 0 }
    }
    $probeArgs = @('probe')
    if ($script:ExternalHealthAuthorityFixtureMode) {
        $observedFixtureSha256 = Get-NewsGraspFileSha256Hex -Path $script:ExternalHealthAuthorityPath
        if (-not [string]::Equals($observedFixtureSha256, $script:ExternalHealthAuthorityExpectedSha256, [System.StringComparison]::Ordinal)) {
            throw 'EXTERNAL_AUTHORITY_FIXTURE_HASH_DRIFT'
        }
        $probeArgs += @(
            '--authority-path', $script:ExternalHealthAuthorityPath,
            '--fixture-mode',
            '--expected-authority-sha256', $script:ExternalHealthAuthorityExpectedSha256
        )
    }
    $raw = (& $PyExe '-I' '-B' $probeScript @probeArgs 2>&1 | Out-String).Trim()
    $rc = $LASTEXITCODE
    try { $value = $raw | ConvertFrom-Json -ErrorAction Stop } catch {
        return [ordered]@{ status = 'unavailable'; reasonCode = 'EXTERNAL_CONTROL_PLANE_OUTPUT_INVALID'; modelLaunchCount = 0 }
    }
    if ($rc -ne 0 -or [string]$value.status -cne 'ready') { return $value }
    return $value
}

# ===== sentinel: 起動できた事実 =====
if ($RepoDirOverride -or $SmokeTest) {
    Write-Log 'RepoDirOverride mode: skipping artifact-root operational registry validation'
} else {
    Invoke-Logged { & $PyExe -m tools.news_grasp_operational_contract validate-registry --repo-root $RepoDir }
    $operationalRegistryRc = $LASTEXITCODE
    if ($operationalRegistryRc -ne 0) {
        Set-RunnerState -Status 'blocked_operational_registry_invalid' -Message 'NEWS_GRASP_OPERATIONAL_REGISTRY_INVALID' -ExitCode 78
        exit 78
    }
}
$script:ValidatedRecoveryExecutionReceipt = $null
$script:ValidatedFinalizationReceipt = $null
$script:IssuedFinalizationReceiptPath = ''
function Invoke-RecoveryReceiptValidation {
    param(
        [Parameter(Mandatory = $true)][ValidateSet('validate-execution', 'consume-execution', 'mark-execution-applied', 'validate-finalization', 'consume-finalization', 'mark-finalization-state-applied')][string] $Command,
        [Parameter(Mandatory = $true)][string] $ReceiptPath
    )
    if (-not $ReceiptPath) { return $null }
    $recoveryReceiptTool = [string]$RecoveryRuntimeBinding.ReceiptToolPath
    $receiptJson = (& $PyExe '-I' '-B' $recoveryReceiptTool $Command `
        '--receipt' $ReceiptPath `
        '--issue-date' $DateStamp `
        '--artifact-root' $RepoDir `
        '--ops-root' $OpsRepoRoot `
        '--production-runtime-root' $TrustedProductionRuntimeRoot `
        '--live-bin-root' $TrustedRecoveryLiveBinRoot `
        '--runner-state' $StateFile `
        '--runner-script' $PSCommandPath 2>&1 | Out-String).Trim()
    $receiptRc = $LASTEXITCODE
    if ($receiptRc -ne 0) { return $null }
    try { return $receiptJson | ConvertFrom-Json -ErrorAction Stop } catch { return $null }
}
if ($RunIntent -eq 'ScheduledRecoveryFull') {
    try {
        $trustedRecoveryPython = (Resolve-Path -LiteralPath ([string]$RecoveryRuntimeBinding.PythonExe) -ErrorAction Stop).Path
        $trustedLiveState = Join-Path ([string]$RecoveryRuntimeBinding.LiveBinRoot) 'news-grasp-runner-state.json'
    } catch {
        Add-RunnerLogLine -Text "ERROR: RECOVERY_RUNTIME_BINDING_INVALID catch=$($_.Exception.Message)"
        exit 76
    }
    if (
        -not [string]::Equals($PyExe, $trustedRecoveryPython, [StringComparison]::OrdinalIgnoreCase) -or
        -not [string]::Equals([IO.Path]::GetFullPath($StateFile), [IO.Path]::GetFullPath($trustedLiveState), [StringComparison]::OrdinalIgnoreCase)
    ) {
        Add-RunnerLogLine -Text "ERROR: RECOVERY_RUNTIME_BINDING_INVALID py=$PyExe trustedPy=$trustedRecoveryPython state=$StateFile trustedState=$trustedLiveState"
        exit 76
    }
    if (-not $RecoveryExecutionReceiptPath) {
        Add-RunnerLogLine -Text 'ERROR: RECOVERY_EXECUTION_RECEIPT_REQUIRED'
        exit 76
    }
    $script:ValidatedRecoveryExecutionReceipt = Invoke-RecoveryReceiptValidation `
        -Command 'validate-execution' `
        -ReceiptPath $RecoveryExecutionReceiptPath
    if ($null -eq $script:ValidatedRecoveryExecutionReceipt) {
        Add-RunnerLogLine -Text 'ERROR: RECOVERY_EXECUTION_RECEIPT_INVALID_FAIL_CLOSED'
        exit 76
    }
    $expectedRecoveryBranch = if ($ResumeFromStage) { 'ResumeFromStage' } else { 'ScheduledRecoveryFull' }
    $expectedResumeStage = if ($ResumeFromStage) { [string]$ResumeFromStage } else { '' }
    if (
        [string]$script:ValidatedRecoveryExecutionReceipt.schemaVersion -ne 'RECOVERY_EXECUTION_RECEIPT_V2' -or
        [string]$script:ValidatedRecoveryExecutionReceipt.recoveryBranch -ne $expectedRecoveryBranch -or
        [string]$script:ValidatedRecoveryExecutionReceipt.resumeStage -ne $expectedResumeStage
    ) {
        Add-RunnerLogLine -Text 'ERROR: RECOVERY_EXECUTION_BRANCH_MISMATCH'
        exit 76
    }
    if ((-not [string]::Equals(
        [IO.Path]::GetFullPath([string]$script:ValidatedRecoveryExecutionReceipt.pythonExecutablePath),
        [IO.Path]::GetFullPath($PyExe),
        [StringComparison]::OrdinalIgnoreCase
    )) -and (-not [string]::Equals(
        [string]$script:ValidatedRecoveryExecutionReceipt.pythonExecutableSha256,
        (Get-NewsGraspFileSha256Hex -Path $PyExe),
        [StringComparison]::Ordinal
    ))) {
        Add-RunnerLogLine -Text "ERROR: RECOVERY_EXECUTION_PYTHON_MISMATCH receiptPy=$($script:ValidatedRecoveryExecutionReceipt.pythonExecutablePath) py=$PyExe"
        exit 76
    }
    if (
        -not [string]::Equals(
            [IO.Path]::GetFullPath([string]$script:ValidatedRecoveryExecutionReceipt.capabilityReservationPath),
            [IO.Path]::GetFullPath($HighCostBindingPath),
            [StringComparison]::OrdinalIgnoreCase
        ) -or
        [string]$script:ValidatedRecoveryExecutionReceipt.capabilityReservationReceiptSha256 -ne $HighCostBindingReceiptSha256
    ) {
        Add-RunnerLogLine -Text 'ERROR: RECOVERY_EXECUTION_CAPABILITY_RESERVATION_MISMATCH'
        exit 76
    }
    $script:RecoveryHardDeadline = [DateTimeOffset]::Parse([string]$script:ValidatedRecoveryExecutionReceipt.hardDeadlineAt)
    if ((-not $FinalizeVerifiedPublishManifest) -and (-not $script:UsesHighCostContinuationAdmission) -and (-not ($ResumeFromStage -and $HighCostAdmissionPath)) -and [DateTimeOffset]::Now -gt [DateTimeOffset]$script:RecoveryHardDeadline) {
        Add-RunnerLogLine -Text 'ERROR: RECOVERY_EXECUTION_HARD_DEADLINE_EXCEEDED'
        exit 78
    }
    $script:RecoveryHighCostCutoff = [DateTimeOffset]::Parse([string]$script:ValidatedRecoveryExecutionReceipt.highCostCutoffAt)
    try {
        $script:RecoveryMaxExternalModelCalls = [int]$script:ValidatedRecoveryExecutionReceipt.reservedMaxExternalModelCalls
    } catch {
        Add-RunnerLogLine -Text 'ERROR: RECOVERY_EXECUTION_MODEL_BUDGET_INVALID'
        exit 76
    }
    if ($script:RecoveryMaxExternalModelCalls -lt 0 -or $script:RecoveryMaxExternalModelCalls -gt 64) {
        Add-RunnerLogLine -Text 'ERROR: RECOVERY_EXECUTION_MODEL_BUDGET_INVALID'
        exit 76
    }
    if ((-not $FinalizeVerifiedPublishManifest) -and (-not $script:UsesHighCostContinuationAdmission) -and (-not ($ResumeFromStage -and $HighCostAdmissionPath)) -and [DateTimeOffset]::Now -gt [DateTimeOffset]$script:RecoveryHighCostCutoff) {
        Add-RunnerLogLine -Text 'ERROR: RECOVERY_EXECUTION_HIGH_COST_CUTOFF_EXCEEDED'
        exit 78
    }
}
if ($FinalizeVerifiedPublishManifest) {
    if (-not $RecoveryFinalizationReceiptPath) {
        Add-RunnerLogLine -Text 'ERROR: RECOVERY_FINALIZATION_RECEIPT_REQUIRED'
        exit 76
    }
    $script:ValidatedFinalizationReceipt = Invoke-RecoveryReceiptValidation `
        -Command 'validate-finalization' `
        -ReceiptPath $RecoveryFinalizationReceiptPath
    if ($null -eq $script:ValidatedFinalizationReceipt) {
        Add-RunnerLogLine -Text 'ERROR: RECOVERY_FINALIZATION_RECEIPT_INVALID'
        exit 76
    }
}
$externalReadiness = $null
if (-not $FinalizeVerifiedPublishManifest) {
    $externalReadiness = Get-NewsGraspExternalControlPlaneReadiness
    if ([string]$externalReadiness.status -cne 'ready') {
        $externalReason = [string]$externalReadiness.reasonCode
        Add-RunnerLogLine -Text "external control plane unavailable; deterministic product path deferred reason=$externalReason authority=$script:ExternalHealthAuthorityPath"
        Set-RunnerState -Status 'external_control_plane_unavailable' -Message 'operation_deferred_external_dependency' -ExitCode 74
        exit 74
    }
    Assert-HighCostOperationAdmission
}
$pidStamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'
Add-Content -Path $InvokedLog -Value "[$pidStamp] runner-invoked pid=$PID ps1 smoke=$SmokeTest recover=$RecoverOnly run_intent=$RunIntent no_publish=$NoPublish resume_from_stage=$ResumeFromStage" -Encoding UTF8

Add-RunnerLogLine -Text ''
Add-RunnerLogLine -Text '=========================================='
if (-not $FinalizeVerifiedPublishManifest) {
    Set-RunnerState -Status 'running' -Message 'runner started' -ExitCode -1 -ResetStartedAt
    Write-Log "news-grasp-runner.ps1 start (run_id=$RunId, smoke=$SmokeTest, recover=$RecoverOnly, run_intent=$RunIntent, no_publish=$NoPublish, resume_from_stage=$ResumeFromStage, pid=$PID)"
} else {
    Add-RunnerLogLine -Text "typed recovery finalizer start run_id=$RunId pid=$PID"
}

if ($FinalizeVerifiedPublishManifest) {
    $expectedManifest = [System.IO.Path]::GetFullPath((Join-Path $RepoDir "build\publish-complete\$DateStamp.json"))
    try {
        $actualManifest = Resolve-NewsGraspContainedRegularFile -Path $FinalizeVerifiedPublishManifest -ExpectedPath $expectedManifest
    } catch {
        $actualManifest = ''
    }
    if ($RunIntent -ne 'ScheduledRecoveryFull' -or (-not $actualManifest)) {
        Add-RunnerLogLine -Text 'ERROR: publish_complete manifest identity is invalid for typed recovery finalize'
        exit 76
    }
    $manifestStream = $null
    try {
        # FileShare.Read keeps the exact bytes stable through hash, parse,
        # one-shot consumption, state mutation and completion guard.
        $manifestStream = [IO.File]::Open(
            $actualManifest,
            [IO.FileMode]::Open,
            [IO.FileAccess]::Read,
            [IO.FileShare]::Read
        )
        $memory = [IO.MemoryStream]::new()
        $manifestStream.CopyTo($memory)
        $manifestBytes = $memory.ToArray()
        $memory.Dispose()
        $manifestText = [Text.UTF8Encoding]::new($false, $true).GetString($manifestBytes)
        $verified = $manifestText | ConvertFrom-Json -ErrorAction Stop
        $sha256 = [Security.Cryptography.SHA256]::Create()
        try {
            $manifestSha256 = ([BitConverter]::ToString($sha256.ComputeHash($manifestBytes))).Replace('-', '').ToLowerInvariant()
        } finally {
            $sha256.Dispose()
        }
        $publishCommit = [string]$verified.publish_commit
        $historicalScheduledFailureRecovered = (
            [string]$verified.live_runner_readiness.reason -eq 'scheduled_task_missed_runs' -and
            [string]$verified.live_runner_readiness.last_scheduled_attempt.status -eq 'failed' -and
            [int]$verified.live_runner_readiness.last_scheduled_attempt.last_task_result -eq 1 -and
            [string]$verified.live_runner_readiness.next_run_readiness.reasonCode -eq 'scheduled_task_missed_runs'
        )
        $manifestGreen = (
            [string]$verified.schemaVersion -eq 'NEWS_GRASP_PUBLISH_COMPLETE_V2' -and
            $verified.ok -eq $true -and
            [string]$verified.date -eq $DateStamp -and
            [string]$verified.public_status -eq 'green' -and
            [string]$verified.scheduled_attempt_status -eq 'failed_then_recovered' -and
            [string]$verified.recovery_attempt_status -eq 'succeeded' -and
            [string]$verified.source_commit -match '^[0-9a-f]{40}$' -and
            [string]$verified.artifact_commit -match '^[0-9a-f]{40}$' -and
            [string]$verified.publish_commit -match '^[0-9a-f]{40}$' -and
            $verified.publish.ok -eq $true -and
            $null -ne $verified.distribution_artifacts -and
            @($verified.distribution_artifacts.missing).Count -eq 0 -and
            (
                (
                    $verified.live_runner_readiness.ok -eq $true -and
                    $verified.live_runner_readiness.next_run_readiness.ok -eq $true
                ) -or
                $historicalScheduledFailureRecovered
            ) -and
            $verified.notification.ok -eq $true -and
            $verified.podcasts.primary.ok -eq $true -and
            $verified.podcasts.deepdive.ok -eq $true -and
            $publishCommit -and
            $publishCommit -eq [string]$verified.publish.deploy_head
        )
        if (-not $manifestGreen) { throw 'FINALIZATION_MANIFEST_NOT_GREEN' }
        if ($manifestSha256 -ne [string]$script:ValidatedFinalizationReceipt.manifestSha256) {
            throw 'FINALIZATION_MANIFEST_DRIFT'
        }
        $consumedFinalization = Invoke-RecoveryReceiptValidation `
            -Command 'consume-finalization' `
            -ReceiptPath $RecoveryFinalizationReceiptPath
        if ($null -eq $consumedFinalization) { throw 'RECOVERY_FINALIZATION_RECEIPT_ALREADY_CONSUMED' }
        Add-RunnerLogLine -Text "typed recovery finalize accepted manifest=$actualManifest publish_commit=$publishCommit"
        $candidateStatePath = New-NewsGraspFinalizationCandidateState `
            -FinalizationReceiptPath $RecoveryFinalizationReceiptPath `
            -ManifestPath $actualManifest `
            -PublishCommit $publishCommit
        if (-not $candidateStatePath) { throw 'FINALIZATION_CANDIDATE_PREPARE_FAILED' }
        if (-not (Invoke-NewsGraspCompletionGuard `
                -FinalizationReceiptPath $RecoveryFinalizationReceiptPath `
                -CandidateStatePath $candidateStatePath)) {
            exit 2
        }
        if (-not (Commit-NewsGraspFinalizationCandidate -CandidateStatePath $candidateStatePath)) {
            exit 76
        }
        # Candidate renameがcommit済みであることを互換consumerへ明示する。
        # first-terminal-winsにより、ここではstateを再書込みしない。
        Set-RunnerState -Status 'publish_complete' -Message 'verified recovery publish complete' -ExitCode 0 `
            -PublishManifestPath $actualManifest -PublishCommit $publishCommit `
            -ScheduledAttemptStatus 'failed_then_recovered' -RecoveryAttemptStatus 'succeeded' `
            -PreservedScheduledFailureReceiptPath ([string]$script:ValidatedFinalizationReceipt.scheduledFailureReceiptPath) `
            -PreservedScheduledFailureReceiptSha256 ([string]$script:ValidatedFinalizationReceipt.scheduledFailureReceiptSha256) `
            -FinalizationReceiptPath $RecoveryFinalizationReceiptPath `
            -FinalizationReceiptSha256 ([string]$script:ValidatedFinalizationReceipt.receiptSha256)
        $stateAppliedJournal = Invoke-RecoveryReceiptValidation `
            -Command 'mark-finalization-state-applied' `
            -ReceiptPath $RecoveryFinalizationReceiptPath
        if ($null -eq $stateAppliedJournal) { throw 'RECOVERY_FINALIZATION_STATE_JOURNAL_INVALID' }
        $executionAppliedJournal = Invoke-RecoveryReceiptValidation `
            -Command 'mark-execution-applied' `
            -ReceiptPath $RecoveryExecutionReceiptPath
        if ($null -eq $executionAppliedJournal) { throw 'RECOVERY_EXECUTION_JOURNAL_INVALID' }
    } catch {
        Add-RunnerLogLine -Text "ERROR: typed recovery finalizer failed reason=$($_.Exception.Message)"
        exit 76
    } finally {
        if ($null -ne $manifestStream) { $manifestStream.Dispose() }
    }
    exit 0
}

# 前回 crash の WAL を、daily artifact の inventory/read と reporter/model 起動より先に回復する。
$EditorTransactionRecoveryCapture = Join-Path ([System.IO.Path]::GetTempPath()) ("news-grasp-editor-transaction-recovery-$DateStamp-$PID.log")
Push-Location $RepoDir
try {
    Invoke-LoggedCapture -CapturePath $EditorTransactionRecoveryCapture -Block {
        & $PyExe '-I' '-B' $canonicalMaterializer '--repo-root' $RepoDir '--date' $DateStamp '--recover-only'
    }
    $editorTransactionRecoveryRc = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($editorTransactionRecoveryRc -ne 0) {
    Write-Log "ERROR: EDITOR_OUTPUT_TRANSACTION_RECOVERY_REQUIRED rc=$editorTransactionRecoveryRc capture=$EditorTransactionRecoveryCapture"
    Invoke-AutonomousCompletionPolicy -FailureKind 'content' -GateId 'newsroom-editor-transaction-recovery' -Reason 'EDITOR_OUTPUT_TRANSACTION_RECOVERY_REQUIRED' -ExitCode $editorTransactionRecoveryRc
}

$DailyDigestArtifacts = Get-PublishInventoryArtifacts -Kind 'digest'
$PublishedDocsArtifacts = Get-PublishInventoryArtifacts -Kind 'published'
$PublishedRepairArtifacts = Get-PublishInventoryArtifacts -Kind 'published-repair'
$script:RequiredCategoriesForSlo = @(Get-PublishInventoryArtifacts -Kind 'categories')

$gateAttemptDir = Join-Path $RepoDir 'data\gate_attempts'
$gateAttemptArchive = Join-Path $RepoDir "build\recovery\gate-attempt-archives\$DateStamp\$RunId"
$priorGateAttempts = @(Get-ChildItem -LiteralPath $gateAttemptDir -Filter "$DateStamp*.json" -File -ErrorAction SilentlyContinue)
if ($priorGateAttempts.Count -gt 0) {
    New-Item -ItemType Directory -Path $gateAttemptArchive -Force | Out-Null
    foreach ($priorAttempt in $priorGateAttempts) {
        Move-Item -LiteralPath $priorAttempt.FullName -Destination (Join-Path $gateAttemptArchive $priorAttempt.Name) -Force
    }
    Write-Log "reset gate attempt ledger for run_id=$RunId archive=$gateAttemptArchive count=$($priorGateAttempts.Count)"
}
Assert-PreRunBootstrapInterlock
Assert-RunnerBinaryInSync
$IsE2EOrDryRun = $NoPublish -or $NoPush -or $StopBeforeDeepDive
if ($IsE2EOrDryRun -and (-not $SmokeTest) -and (-not $PreflightOnly) -and (-not $RecoverOnly) -and (-not $Stage2EditorSmokeOnly) -and (-not $ResumeAfterReporter) -and (-not $ResumeFromPostDailyQuality) -and (-not $ResumeAfterDeepDive) -and (-not $ResumeGenerationQualityRepair) -and (Test-DailyArtifactsExist -TargetDate $DateStamp)) {
    Write-Log "ERROR: E2E full rerun forbidden after existing artifacts date=$DateStamp. Use -ResumeFromStage deepdive, post-daily-quality, post-deepdive, or generation-quality-repair."
    Set-RunnerState -Status 'blocked_e2e_full_rerun_forbidden' -Message 'E2E full rerun forbidden after existing artifacts' -ExitCode 65
    exit 65
}
if ((-not $ForceFullRerun) -and (-not $SmokeTest) -and (-not $PreflightOnly) -and (-not $RecoverOnly) -and (-not $Stage2EditorSmokeOnly) -and (-not $ResumeAfterReporter) -and (-not $ResumeFromPostDailyQuality) -and (-not $ResumeAfterDeepDive) -and (-not $ResumeGenerationQualityRepair) -and (Test-DailyArtifactsExist -TargetDate $DateStamp)) {
    Write-Log "ERROR: existing daily artifacts detected; refusing full rerun for date=$DateStamp. Use -ForceFullRerun only after explicit user approval; otherwise resume from existing artifacts."
    Set-RunnerState -Status 'failed' -Message 'existing daily artifacts detected; refusing full rerun' -ExitCode 64
    exit 64
}
Write-CodexUsageWindowSnapshot -Phase 'start'
Add-RunnerLogLine -Text '=========================================='

# ===== 0. リポ存在チェック =====
if (-not (Test-Path (Join-Path $RepoDir '.git'))) {
    Write-Log "ERROR: repo not found at $RepoDir"
    exit 1
}

Write-Log 'workspace write readiness gate start'
if (-not (Test-WorkspaceWriteReadiness)) {
    Stop-ExternalReadiness -Reason 'workspace write readiness failed' -Kind 'workspace_write_unavailable' -System 'local_filesystem' -ExternalStatus 'write_probe_failed' -ExternalDetail $RepoDir
}
Write-Log 'workspace write readiness gate OK'

if ($PreflightOnly) {
    Write-Log 'PreflightOnly mode: skipping codex / git pull / push / generate_pages'
    Push-Location $RepoDir
    try {
        Invoke-Logged { & $PyExe '-m' 'tools.newsroom_preflight' '--repo-root' $RepoDir '--date' $DateStamp }
        $preflightRc = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($preflightRc -ne 0) {
        Write-Log "ERROR: newsroom preflight failed (rc=$preflightRc)"
        Exit-Runner -Status 'preflight_failed' -Message 'newsroom preflight failed' -ExitCode $preflightRc
    }
    Write-Log 'publish external readiness gate start'
    $publishReadiness = Test-PublishExternalReadiness
    if (-not $publishReadiness.ok) {
        Stop-ExternalReadiness -Reason 'publish external readiness failed' -Kind $publishReadiness.kind -System $publishReadiness.system -ExternalStatus $publishReadiness.status -ExternalStderr $publishReadiness.stderr -ExternalDetail $publishReadiness.detail
    }
    Write-Log 'publish external readiness gate OK'
    Write-Log 'news-grasp-runner.ps1 PREFLIGHT OK'
    Exit-Runner -Status 'preflight_ok' -Message 'news-grasp-runner.ps1 PREFLIGHT OK' -ExitCode 0
}

# ===== 0.5 ネット到達性待ち (再起動直後のネット未確立で git fetch 即死を防ぐ) =====
# 2026-06-11: Windows Update 自動再起動直後に Task Scheduler が起動すると、ネット未確立
#   のまま git fetch が即 exit 1 し当日公開がスキップされていた (StartWhenAvailable と
#   セットの再起動耐性)。待ちロジックは ~/bin/net_wait.py (socket.connect_ex 純 Python)
#   に 1 箇所集約し契約テスト tests/test_net_wait.py で担保 ([[feedback_check_design_principles]]
#   §2/§4)。netstat ポーリングは使わない。github.com:443 へ最大 10 回 × 30 秒待つ。
$NetWait = Join-Path $env:USERPROFILE 'bin\net_wait.py'
if ($SkipSourceSync) {
    Write-Log 'SmokeTest readiness canary: skipping net reachability wait and git sync'
} elseif ($RepoDirOverride) {
    Write-Log 'RepoDirOverride mode: skipping git sync for fixed artifact workspace'
} elseif ($Stage2EditorSmokeOnly) {
    Write-Log 'Stage2EditorSmokeOnly mode: skipping net reachability wait and git sync'
} elseif ($ResumeAfterReporter -or $ResumeFromPostDailyQuality -or $ResumeAfterDeepDive -or $ResumeGenerationQualityRepair) {
    Write-Log 'ResumeFromStage mode: skipping net reachability wait and git sync'
} else {
    if (Test-Path $NetWait) {
        Write-Log 'net reachability wait start (github.com / api.github.com :443, max 10x30s)'
        Invoke-Logged { & $PyExe $NetWait --host github.com --host api.github.com --port 443 --retries 10 --interval-sec 30 --connect-timeout-sec 5 }
        if ($LASTEXITCODE -ne 0) {
            Stop-ExternalReadiness -Reason "network unreachable (github.com:443) after wait; aborting before git fetch (rc=$LASTEXITCODE)" -ExitCode 71 -Kind 'network_unreachable' -System 'github' -ExternalStatus "rc=$LASTEXITCODE" -ExternalDetail 'github.com:443'
        }
        Write-Log 'net reachability OK'
    } else {
        Stop-ExternalReadiness -Reason "net_wait.py missing at $NetWait" -ExitCode 71 -Kind 'local_tool_missing' -System 'local_filesystem' -ExternalStatus 'missing' -ExternalDetail $NetWait
    }

    # ===== 1. git fetch / pull =====
    Write-Log 'git fetch start'
    Invoke-Logged { & $GitExe @GitSafeArgs -C $RepoDir fetch --quiet origin main }
    if ($LASTEXITCODE -ne 0) { Write-Log "ERROR: git fetch failed (rc=$LASTEXITCODE)"; exit 1 }

    Write-Log 'git pull --ff-only start'
    Invoke-Logged { & $GitExe @GitSafeArgs -C $RepoDir pull --ff-only origin main }
    if ($LASTEXITCODE -ne 0) { Stop-ExternalReadiness -Reason "git pull failed (rc=$LASTEXITCODE)" -ExitCode 71 -Kind 'github_remote' -System 'github' -ExternalStatus "rc=$LASTEXITCODE" -ExternalDetail 'git pull --ff-only origin main' }
}

if (-not (Test-ArtifactExecutableTreeIntegrity)) {
    Exit-Runner -Status 'blocked_artifact_executable_tree_invalid' -Message 'ARTIFACT_EXECUTABLE_TREE_INVALID before production tools' -ExitCode 126
}

if ($SmokeTest) {
    Write-Log 'SmokeTest mode: skipping codex / push / generate_pages'
    Write-Log 'news-grasp-runner.ps1 SMOKE OK'
    Exit-Runner -Status 'smoke_ok' -Message 'news-grasp-runner.ps1 SMOKE OK' -ExitCode 0
}

# ResumeFromStage は stage-specific 分岐より先に一度だけ開始境界を通す。
# admission gate が検証済みの script-scope path だけを後段へ渡し、ローカル変数の
# dynamic-scope 依存による broker / decision receipt の取り違えを閉じる。
if ($ResumeFromStage) {
    if (
        (-not [System.IO.Path]::IsPathFullyQualified([string]$script:ScheduledRecoveryStageBrokerPath)) -or
        (-not [System.IO.Path]::IsPathFullyQualified([string]$script:ScheduledRecoveryStageDecisionReceiptPath)) -or
        (-not (Test-Path -LiteralPath $script:ScheduledRecoveryStageBrokerPath -PathType Leaf)) -or
        (-not (Test-Path -LiteralPath $script:ScheduledRecoveryStageDecisionReceiptPath -PathType Leaf))
    ) {
        Write-Log 'ERROR: scheduled recovery stage start paths are invalid'
        Set-RunnerState -Status 'operation_rejected_high_cost_admission' -Message 'SCHEDULED_RECOVERY_STAGE_START_PATHS_INVALID' -ExitCode 76 -Phase 'resume' -Step 'stage-start-boundary'
        exit 76
    }
    Set-RunnerState -Status 'running' -Message 'scheduled recovery stage start boundary' -ExitCode -1 -Phase 'resume' -Step 'stage-start-boundary' -ResumeStageCheckpoint $ResumeFromStage
    try {
        $stageWitnessJson = (& $PyExe -I $script:ScheduledRecoveryStageBrokerPath 'start-news-grasp-recovery-stage' '--decision' $script:ScheduledRecoveryStageDecisionReceiptPath '--recovery-authority' $ScheduledAuthorityEvidencePath '--consumer-run-id' $RunId 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -ne 0) {
            throw "SCHEDULED_RECOVERY_STAGE_START_FAILED exit=$LASTEXITCODE"
        }
        $stageWitness = $stageWitnessJson | ConvertFrom-Json -ErrorAction Stop
        if (
            [string]$stageWitness.schemaVersion -ne 'SCHEDULED_RECOVERY_STAGE_STARTED_V1' -or
            [string]$stageWitness.issueDate -ne $DateStamp -or
            [string]$stageWitness.resumeStage -ne $ResumeFromStage -or
            [string]$stageWitness.consumerRunId -ne $RunId -or
            [string]$stageWitness.decisionReceiptSha256 -ne [string](Get-FileSha256Hex -Path $script:ScheduledRecoveryStageDecisionReceiptPath)
        ) {
            throw 'SCHEDULED_RECOVERY_STAGE_START_WITNESS_INVALID'
        }
    } catch {
        Write-Log "ERROR: scheduled recovery stage start boundary failed: $($_.Exception.Message)"
        Set-RunnerState -Status 'failed' -Message 'scheduled recovery stage start boundary failed' -ExitCode 76 -Phase 'resume' -Step 'stage-start-boundary'
        exit 76
    }
    if ($script:UsesHighCostContinuationAdmission) {
        Write-Log "scheduled recovery stage start boundary satisfied by continuation + recovery decision + fresh broker + stage witness for ResumeFromStage=$ResumeFromStage"
    }
}

if ($RecoverOnly) {
    $recoverOnlyInputManifest = Write-RecoverOnlyInputManifest
    Write-Log "RecoverOnly input manifest: $recoverOnlyInputManifest"
    Write-Log 'RecoverOnly mode: skipping digest codex; using current local digest/data commits and files'
} elseif ($ResumeFromPostDailyQuality -or $ResumeAfterDeepDive -or $ResumeGenerationQualityRepair) {
    if ($ResumeGenerationQualityRepair) {
        Write-Log "ResumeFromStage=${ResumeFromStage}: reusing Stage0/Reporter/Editor/daily-quality; starting at missing-artifact generation repair"
    } elseif ($ResumeAfterDeepDive) {
        Write-Log "ResumeFromStage=${ResumeFromStage}: reusing Stage0/Reporter/Editor/daily-quality/DeepDive artifacts; starting after DeepDive"
    } else {
        Write-Log "ResumeFromStage=${ResumeFromStage}: reusing Stage0/Reporter/Editor/daily-quality artifacts; starting at DeepDive"
    }
    Write-Log 'ResumeFromStage mode: skipping Stage0/Stage1/Stage1.5/Stage2/Stage3; rechecking summary/daily gates'
    $Categories = Get-PublishInventoryArtifacts -Kind 'categories'
    $resumeRequiredArtifacts = @(
        (Join-Path $RepoDir "build\reporter-artifacts\$DateStamp\editor-input-manifest.json"),
        (Join-Path $RepoDir "digest\Summary\$DateStamp.md"),
        (Join-Path $RepoDir "data\articles.jsonl")
    )
    if ($ResumeAfterDeepDive) {
        $resumeRequiredArtifacts += (Join-Path $RepoDir "digest\DeepDive\$DateStamp-DeepDive.md")
    }
    foreach ($resumeArtifact in $resumeRequiredArtifacts) {
        if (-not (Test-Path -LiteralPath $resumeArtifact)) {
            Write-Log "ERROR: ResumeFromStage missing required artifact: $resumeArtifact"
            Set-RunnerState -Status 'failed' -Message 'resume required artifact missing' -ExitCode 65 -Phase 'resume' -Step 'resume artifact check'
            exit 65
        }
    }
} else {
    if ($Stage2EditorSmokeOnly) {
        Write-Log 'Stage2EditorSmokeOnly mode: skipping publish external readiness gate'
    } else {
        Write-Log 'publish external readiness gate start'
        $publishReadiness = Test-PublishExternalReadiness
        if (-not $publishReadiness.ok) {
            Stop-ExternalReadiness -Reason 'publish external readiness failed' -Kind $publishReadiness.kind -System $publishReadiness.system -ExternalStatus $publishReadiness.status -ExternalStderr $publishReadiness.stderr -ExternalDetail $publishReadiness.detail
        }
        Write-Log 'publish external readiness gate OK'
    }

    # ===== Stage0: deterministic candidate harvest (LLM 前固定実行) =====
    $CandidateDir = Join-Path $RepoDir 'build\candidates'
    $CandidateLastGoodDir = Join-Path $RepoDir 'build\candidates-last-good'
    $DedupedCandidateDir = Join-Path $RepoDir 'build\deduped-candidates'
    $HarvestAuditDir = Join-Path $RepoDir "data\search_audit\$DateStamp"
    # tools.publish_inventory.scheduled_category_ids(issue) が当日必須カテゴリの正本。
    # 非対象カテゴリを reporter fan-out しない。Game は火木土日のみ、Manufacturing / Economy は月火水木金のみ。
    $Categories = Get-PublishInventoryArtifacts -Kind 'categories'
    if ($Categories.Count -le 0) {
        Write-Log "ERROR: scheduled category list is empty date=$DateStamp"
        exit 1
    }
    if ($Stage2EditorSmokeOnly -or $ResumeAfterReporter) {
        if ($ResumeAfterReporter) {
            Write-Log "ResumeFromStage=${ResumeFromStage}: skipping Stage0/Stage1/Stage1.5; using existing deduped candidates and reporter artifacts"
        } else {
            Write-Log 'Stage2EditorSmokeOnly mode: skipping Stage0 harvest and Stage1 dedup; using existing deduped candidates'
        }
        New-Item -ItemType Directory -Path $DedupedCandidateDir -Force | Out-Null
    } else {
        if (Test-Path $CandidateDir) { Remove-Item -LiteralPath $CandidateDir -Recurse -Force -ErrorAction SilentlyContinue }
        if (Test-Path $DedupedCandidateDir) { Remove-Item -LiteralPath $DedupedCandidateDir -Recurse -Force -ErrorAction SilentlyContinue }
        New-Item -ItemType Directory -Path $CandidateDir -Force | Out-Null
        New-Item -ItemType Directory -Path $CandidateLastGoodDir -Force | Out-Null
        New-Item -ItemType Directory -Path $DedupedCandidateDir -Force | Out-Null
        New-Item -ItemType Directory -Path $HarvestAuditDir -Force | Out-Null
        $stage0Start = Get-Date
        $candidateTotal = 0
        foreach ($cat in $Categories) {
            $outPath = Join-Path $CandidateDir "$cat.jsonl"
            Push-Location $RepoDir
            try {
                Write-Log "Stage0 harvest_candidates.py start category=$cat"
                $harvestRc = Invoke-PythonStdoutFileUtf8 -PythonArgs @('-m', 'tools.harvest_candidates', '--category', $cat, '--audit-dir', $HarvestAuditDir) -StdoutPath $outPath
            } finally {
                Pop-Location
            }
            $lastGoodPath = Join-Path $CandidateLastGoodDir "$cat.jsonl"
            if ($harvestRc -ne 0) {
                if (Test-Path -LiteralPath $lastGoodPath) {
                    Write-Log "WARN: Stage0 harvest failed category=$cat rc=$harvestRc; Stage0 harvest fallback from last-good"
                    Copy-Item -LiteralPath $lastGoodPath -Destination $outPath -Force
                } else {
                    Write-Log "ERROR: Stage0 harvest no last-good candidates category=$cat rc=$harvestRc"
                    Stop-ExternalReadiness -Reason "Stage0 harvest failed category=$cat and no last-good candidates" -Kind 'candidate_source_unavailable' -System 'source_collection' -ExternalStatus 'no_last_good' -ExternalDetail "category=$cat"
                }
            } else {
                Copy-Item -LiteralPath $outPath -Destination $lastGoodPath -Force
            }
            $count = 0
            if (Test-Path $outPath) { $count = @((Get-Content -LiteralPath $outPath -Encoding UTF8 -ErrorAction SilentlyContinue)).Count }
            $candidateTotal += $count
            Write-Log "Stage0 harvest end category=$cat candidates=$count"
        }
        $stage0Sec = [int]((Get-Date) - $stage0Start).TotalSeconds
        Write-Log "Stage0 harvest summary categories=$($Categories.Count) candidates=$candidateTotal elapsed_sec=$stage0Sec"

        # ===== Stage1: deterministic cross-category dedup/freshness (LLM 前固定実行) =====
        $stage1Start = Get-Date
        $dedupCapture = Join-Path ([System.IO.Path]::GetTempPath()) ("news-grasp-cross-dedup-$DateStamp.json")
        Push-Location $RepoDir
        try {
            Write-Log 'Stage1 cross_category_dedup.py start'
            Invoke-LoggedCapture -CapturePath $dedupCapture -Block { & $PyExe -m tools.cross_category_dedup --input-dir $CandidateDir --output-dir $DedupedCandidateDir --articles-jsonl (Join-Path $RepoDir 'data\articles.jsonl') }
            $dedupRc = $LASTEXITCODE
        } finally {
            Pop-Location
        }
        if ($dedupRc -ne 0) { Write-Log "ERROR: Stage1 cross_category_dedup failed rc=$dedupRc"; exit 1 }
        $stage1Sec = [int]((Get-Date) - $stage1Start).TotalSeconds
        try {
            $dedupJson = Get-Content -LiteralPath $dedupCapture -Raw -Encoding UTF8 | ConvertFrom-Json
            Write-Log "Stage1 dedup summary input=$($dedupJson.input_count) passed=$($dedupJson.passed) dropped=$($dedupJson.dropped) elapsed_sec=$stage1Sec"
        } catch {
            Write-Log "Stage1 dedup summary elapsed_sec=$stage1Sec"
        }

        # ===== Stage1.5: deterministic reporter candidate preparation =====
        # Google News RSS URL の元記事 URL 解決と OGP thumb 補完は、Task Scheduler 配下の
        # reporter Codex 子プロセスへ任せない。非対話セッションでは reporter 内の Python
        # 起動が失敗しうるため、runner 側の Python 境界で固定実行してから Stage2 へ渡す。
        $stage15Start = Get-Date
        $prepareCapture = Join-Path ([System.IO.Path]::GetTempPath()) ("news-grasp-prepare-reporter-candidates-$DateStamp.json")
        Push-Location $RepoDir
        try {
            Write-Log 'Stage1.5 prepare_reporter_candidates.py start'
            Invoke-LoggedCapture -CapturePath $prepareCapture -Block { & $PyExe -m tools.prepare_reporter_candidates --input-dir $DedupedCandidateDir --max-rows-per-file 25 --decode-timeout 3 --thumb-limit-per-file 5 --thumb-timeout 6 --thumb-retries 0 }
            $prepareRc = $LASTEXITCODE
        } finally {
            Pop-Location
        }
        if ($prepareRc -ne 0) { Write-Log "ERROR: Stage1.5 prepare_reporter_candidates failed rc=$prepareRc"; exit 1 }
        $stage15Sec = [int]((Get-Date) - $stage15Start).TotalSeconds
        try {
            $prepareJson = Get-Content -LiteralPath $prepareCapture -Raw -Encoding UTF8 | ConvertFrom-Json
            Write-Log "Stage1.5 prepare summary input=$($prepareJson.input_count) prepared=$($prepareJson.prepared_count) dropped=$($prepareJson.dropped_count) elapsed_sec=$stage15Sec"
        } catch {
            Write-Log "Stage1.5 prepare summary elapsed_sec=$stage15Sec"
        }
    }

    # ===== Stage2 reporter fan-out / Stage3 editor integration via Codex =====
    $ReporterArtifactDir = Join-Path $RepoDir "build\reporter-artifacts\$DateStamp"
    $ReporterPromptDir = Join-Path $RepoDir "build\reporter-prompts\$DateStamp"
    $ReporterFanoutSchema = Join-Path $RepoDir 'schemas\reporter_fanout_return.schema.json'
    $ReporterRecordSchema = Join-Path $RepoDir 'schemas\reporter_records.schema.json'
    $EditorSummarySchema = Join-Path $RepoDir 'schemas\editor_summary.schema.json'
    $CategoryGenreMap = @{
        fx = 'FX'
        ai = 'AI'
        it = 'IT-Consulting'
        mobility = 'Mobility'
        manufacturing = 'Manufacturing'
        economy = 'Economy'
        game = 'Game'
    }
    foreach ($scheduledCat in $Categories) {
        if (-not $CategoryGenreMap.ContainsKey($scheduledCat)) {
            Write-Log "ERROR: scheduled category has no genre mapping category=$scheduledCat date=$DateStamp"
            exit 1
        }
    }
    if (Test-Path $ReporterArtifactDir) { Remove-Item -LiteralPath $ReporterArtifactDir -Recurse -Force -ErrorAction SilentlyContinue }
    if (Test-Path $ReporterPromptDir) { Remove-Item -LiteralPath $ReporterPromptDir -Recurse -Force -ErrorAction SilentlyContinue }
    New-Item -ItemType Directory -Path $ReporterArtifactDir -Force | Out-Null
    New-Item -ItemType Directory -Path $ReporterPromptDir -Force | Out-Null

    $ReporterModel = Get-ModelPolicyValue -Role 'reporter' -Key 'default'
    $ReporterReasoningEffort = Get-ModelPolicyValue -Role 'reporter' -Key 'reasoning'
    $ReporterArtifacts = @()
    $ReporterMaxAttempts = 3
    if ($NoPublish) { $ReporterMaxAttempts = 1 }
    $ReporterFailureSignatures = @{}

    function Get-ReporterFailureSignature {
        param([string]$Text)

        $normalized = if ([string]::IsNullOrWhiteSpace($Text)) { 'empty-failure' } else { $Text.Trim() }
        $sha = [System.Security.Cryptography.SHA256]::Create()
        try {
            $bytes = [System.Text.Encoding]::UTF8.GetBytes($normalized)
            $hash = $sha.ComputeHash($bytes)
            return (([System.BitConverter]::ToString($hash)) -replace '-', '').Substring(0, 16)
        } finally {
            $sha.Dispose()
        }
    }

    function Test-ReporterCodexQuotaFailure {
        param($WaveResult)

        if ([int]$WaveResult.rc -eq 123) { return $true }
        $wrapperLog = [string]$WaveResult.wrapper_log
        if (-not $wrapperLog -or -not (Test-Path -LiteralPath $wrapperLog)) { return $false }
        try {
            $logText = Get-Content -LiteralPath $wrapperLog -Raw -Encoding UTF8
        } catch {
            return $false
        }
        return (
            $logText -match "You've hit your usage limit" -or
            $logText -match 'purchase more credits' -or
            $logText -match 'try again at [0-9]{1,2}:[0-9]{2}\s*(AM|PM)'
        )
    }

    function Clear-ReporterCategoryArtifacts {
        param([string]$Category)

        $genre = $CategoryGenreMap[$Category]
        $paths = @(
            (Join-Path $RepoDir "tmp\newsroom\$DateStamp\$Category.records.jsonl"),
            (Join-Path $RepoDir "digest\$genre\$DateStamp-$genre.md"),
            (Join-Path $RepoDir "data\search_audit\$DateStamp\$Category.json"),
            (Join-Path $ReporterArtifactDir "$Category.codex-last-message.json")
        )
        foreach ($pathToRemove in $paths) {
            if (Test-Path $pathToRemove) {
                Remove-Item -LiteralPath $pathToRemove -Force -ErrorAction SilentlyContinue
            }
        }
        Get-ChildItem -LiteralPath $ReporterArtifactDir -Filter "$Category.wrapper-*.log" -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
        Get-ChildItem -LiteralPath $ReporterArtifactDir -Filter "$Category.verify-*.log" -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
        Get-ChildItem -LiteralPath (Join-Path $RepoDir 'build\codex-usage') -Filter "$DateStamp.reporter-$Category-attempt*.jsonl" -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
    }

    function New-ReporterPrompt {
        param(
            [string]$Category,
            [string]$PromptFile
        )

        $catDedupFile = Join-Path $DedupedCandidateDir "$Category.jsonl"
        $reporterPrompt = @"
今日の日付は $DateStamp (JST) である。
あなたはカテゴリ $Category 専属の reporter である。

必ず prompts\newsroom-reporter-system.md と prompts\style-guide.md を読み、指示に従うこと。
Stage1 dedup 済み候補は $catDedupFile にある。広域収集と横断 dedup をやり直してはいけない。
出力成果物は reporter 契約どおり tmp/newsroom/$DateStamp/$Category.records.jsonl / digest / data/search_audit/$DateStamp/$Category.json の 3 点に限定する。
records は schemas\reporter_records.schema.json の records.items と tools.verify_reporter_output を通過する形にする。
external fan-out の返却はコンパクト JSON のみとし、フル record・記事本文・digest md 本文を返却に含めない。
"@
        [System.IO.File]::WriteAllText($PromptFile, $reporterPrompt, [System.Text.UTF8Encoding]::new($false))
    }

    function Invoke-ReporterWave {
        param(
            [int]$Attempt,
            [string[]]$WaveCategories
        )

        # 短時間で完了したjobを30秒単位でしか回収しないと、retry waveだけで
        # recovery SLOを消費する。通常運用は5秒、隔離smokeは1秒で回収する。
        $ReporterPollSeconds = if ($Stage2EditorSmokeOnly) { 1 } else { 5 }
        $ReporterHeartbeatSeconds = 60
        $ReporterJobTimeoutSec = if ($Stage2EditorSmokeOnly) { 30 } else { $TimeoutSec + 120 }
        if ($RunIntent -eq 'ScheduledRecoveryFull' -and -not $FinalizeVerifiedPublishManifest) {
            $recoveryRemainingSeconds = [int][Math]::Floor((([DateTimeOffset]$script:RecoveryHardDeadline) - [DateTimeOffset]::Now).TotalSeconds)
            if ($recoveryRemainingSeconds -le 0) {
                Assert-RecoveryOperationDeadline -HighCost -Stage 'reporter-wave:start'
            }
            $ReporterJobTimeoutSec = [Math]::Max(1, [Math]::Min($ReporterJobTimeoutSec, $recoveryRemainingSeconds))
        }
        $wrapper_log_offsets = @{}
        $jobs = @()
        try {
        foreach ($waveCat in $WaveCategories) {
            if ($Attempt -gt 1) {
                Clear-ReporterCategoryArtifacts -Category $waveCat
            }
            $promptFile = Join-Path $ReporterPromptDir "$waveCat.md"
            $lastMessage = Join-Path $ReporterArtifactDir "$waveCat.codex-last-message.json"
            $wrapperLog = Join-Path $ReporterArtifactDir "$waveCat.wrapper-attempt$Attempt.log"
            $usageLog = Join-Path $RepoDir "build\codex-usage\$DateStamp.reporter-$waveCat-attempt$Attempt.jsonl"
            New-ReporterPrompt -Category $waveCat -PromptFile $promptFile

            while (@($jobs | Where-Object { $_.JobStateInfo.State -eq 'Running' }).Count -ge $MaxParallelReporterJobs) {
                Start-Sleep -Seconds 1
                Assert-RecoveryOperationDeadline -HighCost -Stage "reporter:$waveCat:parallel-wait"
            }

            $reporterCallSequence = Acquire-RecoveryHighCostBudget -Stage "model:reporter:$waveCat:attempt:$Attempt"
            $reporterTimeoutSec = $TimeoutSec
            $reporterIdleTimeoutSec = $IdleTimeoutSec
            if ($RunIntent -eq 'ScheduledRecoveryFull' -and -not $FinalizeVerifiedPublishManifest) {
                $remainingSeconds = [int][Math]::Floor((([DateTimeOffset]$script:RecoveryHardDeadline) - [DateTimeOffset]::Now).TotalSeconds)
                if ($remainingSeconds -le 0) {
                    Assert-RecoveryOperationDeadline -HighCost -Stage "model:reporter:$waveCat:attempt:$Attempt"
                }
                $reporterTimeoutSec = [Math]::Max(1, [Math]::Min($TimeoutSec, $remainingSeconds))
                $reporterIdleTimeoutSec = [Math]::Max(1, [Math]::Min($IdleTimeoutSec, $remainingSeconds))
                $ReporterJobTimeoutSec = [Math]::Max(1, [Math]::Min($ReporterJobTimeoutSec, $remainingSeconds))
            }
            $highCostCallId = "$RunId`:reporter`:$waveCat`:$Attempt`:$reporterCallSequence"
            $highCostCallReceipt = Join-Path $HighCostCallReceiptDir ("{0:D3}-reporter-{1}-attempt-{2}.json" -f $reporterCallSequence, $waveCat, $Attempt)
            Write-Log "reporter job START (agent=codex, role=reporter, category=$waveCat, attempt=$Attempt/$ReporterMaxAttempts, Wrapper=$CodexWrapper, Model=$ReporterModel, ReasoningEffort=$ReporterReasoningEffort, TimeoutSec=$reporterTimeoutSec, IdleTimeoutSec=$reporterIdleTimeoutSec, BudgetSequence=$reporterCallSequence)"
            $job = Start-Job -ArgumentList @(
                $waveCat,
                $Attempt,
                $CodexWrapper,
                $CodexExe,
                $promptFile,
                $wrapperLog,
                $reporterTimeoutSec,
                $reporterIdleTimeoutSec,
                $RepoDir,
                $ReporterFanoutSchema,
                $lastMessage,
                $ReporterModel,
                $ReporterReasoningEffort,
                $usageLog,
                $HighCostBindingPath,
                $HighCostBindingReceiptSha256,
                $HighCostBindingResolverPath,
                $HighCostBindingResolverSha256,
                $script:HighCostAdmissionPath,
                $script:HighCostParentAuthorityPath,
                $script:E2EFinalAdmissionPath,
                $script:E2EFinalRunnerArgumentsPath,
                $script:E2EFinalReservationReceiptPath,
                $script:E2EFinalClaimReceiptPath,
                $E2EAttemptPolicyPath,
                $E2ELogicalAttempt,
                $script:HighCostClaimWitness,
                $script:HighCostAttemptId,
                $script:HighCostExpectedOperationKind,
                $script:HighCostExpectedIssueDate,
                $PyExe,
                $highCostCallId,
                $highCostCallReceipt,
                $GlobalHarnessGenerationManifestPath
            ) -ScriptBlock {
                param(
                    [string]$Category,
                    [int]$JobAttempt,
                    [string]$Wrapper,
                    [string]$CodexExePath,
                    [string]$PromptFile,
                    [string]$WrapperLog,
                    [int]$TimeoutSeconds,
                    [int]$IdleTimeoutSeconds,
                    [string]$WorkingDirectory,
                    [string]$OutputSchema,
                    [string]$OutputLastMessage,
                    [string]$Model,
                    [string]$ReasoningEffort,
                    [string]$UsageLog,
                    [string]$HighCostBindingPath,
                    [string]$HighCostBindingReceiptSha256,
                    [string]$HighCostBindingResolverPath,
                    [string]$HighCostBindingResolverSha256,
                    [string]$HighCostAdmissionPath,
                    [string]$HighCostParentAuthorityPath,
                    [string]$E2EFinalAdmissionPath,
                    [string]$E2EFinalRunnerArgumentsPath,
                    [string]$E2EFinalReservationReceiptPath,
                    [string]$E2EFinalClaimReceiptPath,
                    [string]$E2EAttemptPolicyPath,
                    [int]$E2ELogicalAttempt,
                    [string]$HighCostClaimWitness,
                    [string]$HighCostAttemptId,
                    [string]$HighCostExpectedOperationKind,
                    [string]$HighCostExpectedIssueDate,
                    [string]$HighCostPythonExe,
                    [string]$HighCostCallId,
                    [string]$HighCostCallReceiptPath,
                    [string]$GlobalHarnessGenerationManifestPath
                )

                $started = Get-Date
                $wrapperException = ''
                try {
                    & $Wrapper `
                        -CodexExe $CodexExePath `
                        -PromptFile $PromptFile `
                        -LogFile $WrapperLog `
                        -TimeoutSec $TimeoutSeconds `
                        -IdleTimeoutSec $IdleTimeoutSeconds `
                        -WorkingDirectory $WorkingDirectory `
                        -OutputSchema $OutputSchema `
                        -OutputLastMessage $OutputLastMessage `
                        -Model $Model `
                        -ReasoningEffort $ReasoningEffort `
                        -FlowName "reporter:$Category" `
                        -UsageLog $UsageLog `
                        -HighCostBindingPath $HighCostBindingPath `
                        -HighCostBindingReceiptSha256 $HighCostBindingReceiptSha256 `
                        -HighCostBindingResolverPath $HighCostBindingResolverPath `
                        -HighCostBindingResolverSha256 $HighCostBindingResolverSha256 `
                        -HighCostAdmissionPath $HighCostAdmissionPath `
                        -HighCostParentAuthorityPath $HighCostParentAuthorityPath `
                        -E2EFinalAdmissionPath $E2EFinalAdmissionPath `
                        -E2EFinalRunnerArgumentsPath $E2EFinalRunnerArgumentsPath `
                        -E2EFinalReservationReceiptPath $E2EFinalReservationReceiptPath `
                        -E2EFinalClaimReceiptPath $E2EFinalClaimReceiptPath `
                        -E2EAttemptPolicyPath $E2EAttemptPolicyPath `
                        -E2ELogicalAttempt $E2ELogicalAttempt `
                        -HighCostClaimWitness $HighCostClaimWitness `
                        -HighCostAttemptId $HighCostAttemptId `
                        -HighCostExpectedOperationKind $HighCostExpectedOperationKind `
                        -HighCostExpectedIssueDate $HighCostExpectedIssueDate `
                        -HighCostPythonExe $HighCostPythonExe `
                        -HighCostCallId $HighCostCallId `
                        -HighCostCallReceiptPath $HighCostCallReceiptPath `
                        -GlobalHarnessGenerationManifestPath $GlobalHarnessGenerationManifestPath
                    $wrapperOk = $?
                    $rc = $LASTEXITCODE
                } catch {
                    $wrapperOk = $false
                    $rc = 125
                    $wrapperException = "{0}: {1}" -f $_.Exception.GetType().Name, $_.Exception.Message
                }
                if ($null -eq $rc) {
                    if ($wrapperOk) { $rc = 0 } else { $rc = 125 }
                }

                [pscustomobject]@{
                    category = $Category
                    attempt = $JobAttempt
                    rc = [int]$rc
                    elapsed_sec = [int]((Get-Date) - $started).TotalSeconds
                    wrapper_log = $WrapperLog
                    usage_log = $UsageLog
                    last_message = $OutputLastMessage
                    failure_status = if ($wrapperException) { 'wrapper_invocation_exception' } else { '' }
                    failure_detail = $wrapperException
                }
            }
            if ($null -eq $job) {
                throw "REPORTER_JOB_START_FAILED category=$waveCat attempt=$Attempt"
            }
            $job | Add-Member -NotePropertyName Category -NotePropertyValue $waveCat
            $job | Add-Member -NotePropertyName Attempt -NotePropertyValue $Attempt
            $job | Add-Member -NotePropertyName StartedAt -NotePropertyValue (Get-Date)
            $job | Add-Member -NotePropertyName WrapperLog -NotePropertyValue $wrapperLog
            $job | Add-Member -NotePropertyName UsageLog -NotePropertyValue $usageLog
            $job | Add-Member -NotePropertyName LastMessage -NotePropertyValue $lastMessage
            $jobs += $job
        }

        if ($jobs.Count -eq 0) { return @() }

        function Append-ReporterWrapperLog {
            param([string]$Path)
            if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return }
            $text = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
            $key = [string]$Path
            $offset = if ($wrapper_log_offsets.ContainsKey($key)) { [int]$wrapper_log_offsets[$key] } else { 0 }
            if ($text.Length -gt $offset) {
                Add-RunnerLogLine -Text $text.Substring($offset)
                $wrapper_log_offsets[$key] = $text.Length
            }
        }

        $results = @()
        $pending = @($jobs)
        $lastHeartbeat = (Get-Date).AddSeconds(-1 * $ReporterHeartbeatSeconds)
        while ($pending.Count -gt 0) {
            $now = Get-Date
            $jobStates = @(
                $pending | ForEach-Object {
                    [pscustomobject]@{
                        category = [string]$_.Category
                        state = [string]$_.JobStateInfo.State
                    }
                }
            )
            $activeJobs = @(
                $pending | Where-Object { $_.JobStateInfo.State -eq 'Running' } | ForEach-Object {
                    [pscustomobject]@{
                        category = [string]$_.Category
                        attempt = [int]$_.Attempt
                        elapsed_sec = [int]($now - $_.StartedAt).TotalSeconds
                    }
                }
            )
            foreach ($job in @($pending)) {
                Append-ReporterWrapperLog -Path $job.WrapperLog
            }
            if (($now - $lastHeartbeat).TotalSeconds -ge $ReporterHeartbeatSeconds) {
                $stateText = @($jobStates | ForEach-Object { "$($_.category):$($_.state)" }) -join ','
                Write-Log "reporter supervisor heartbeat attempt=$Attempt active_jobs=$($activeJobs.Count) job_states=$stateText"
                Update-RunnerProgress -Phase 'reporter' -Step "reporter wave attempt=$Attempt active_jobs=$($activeJobs.Count) job_states=$stateText" -Attempt $Attempt -ActiveJobs $activeJobs
                $lastHeartbeat = $now
            }

            foreach ($job in @($pending)) {
                $elapsed = [int]((Get-Date) - $job.StartedAt).TotalSeconds
                $jobState = [string]$job.JobStateInfo.State
                if ($jobState -in @('Running', 'NotStarted') -and $elapsed -gt $ReporterJobTimeoutSec) {
                    Write-Log "ERROR: reporter job timeout category=$($job.Category) attempt=$Attempt elapsed_sec=$elapsed limit_sec=$ReporterJobTimeoutSec"
                    Append-ReporterWrapperLog -Path $job.WrapperLog
                    Stop-Job -Job $job -ErrorAction SilentlyContinue
                    Stop-Job -Job $job -Force -ErrorAction SilentlyContinue
                    $partial = @(Receive-Job -Id $job.Id -ErrorAction SilentlyContinue)
                    Remove-Job -Job $job -Force -ErrorAction SilentlyContinue
                    $timeoutResult = [pscustomobject]@{
                        category = [string]$job.Category
                        attempt = [int]$job.Attempt
                        rc = 124
                        elapsed_sec = $elapsed
                        wrapper_log = [string]$job.WrapperLog
                        usage_log = [string]$job.UsageLog
                        last_message = [string]$job.LastMessage
                        failure_status = 'blocked_reporter_timeout'
                    }
                    $results += $timeoutResult
                    Write-Log "reporter job END category=$($timeoutResult.category) attempt=$($timeoutResult.attempt)/$ReporterMaxAttempts rc=$($timeoutResult.rc) elapsed_sec=$($timeoutResult.elapsed_sec)"
                    Set-RunnerState -Status 'blocked_reporter_timeout' -Message "reporter job timeout category=$($job.Category)" -ExitCode 124 -Phase 'reporter' -Category ([string]$job.Category) -Attempt $Attempt -ActiveJobs $activeJobs
                    $pending = @($pending | Where-Object { $_.Id -ne $job.Id })
                    continue
                }

                if ($jobState -notin @('Running', 'NotStarted')) {
                    Append-ReporterWrapperLog -Path $job.WrapperLog
                    $received = @(Receive-Job -Id $job.Id -ErrorAction SilentlyContinue)
                    Remove-Job -Job $job -Force -ErrorAction SilentlyContinue
                    if ($received.Count -eq 0) {
                        $received = @([pscustomobject]@{
                            category = [string]$job.Category
                            attempt = [int]$job.Attempt
                            rc = 125
                            elapsed_sec = $elapsed
                            wrapper_log = [string]$job.WrapperLog
                            usage_log = [string]$job.UsageLog
                            last_message = [string]$job.LastMessage
                        })
                    }
                    foreach ($item in $received) {
                        $results += $item
                        Write-Log "reporter job END category=$($item.category) attempt=$($item.attempt)/$ReporterMaxAttempts rc=$($item.rc) elapsed_sec=$($item.elapsed_sec)"
                    }
                    $pending = @($pending | Where-Object { $_.Id -ne $job.Id })
                }
            }
            if ($pending.Count -gt 0) {
                Start-Sleep -Seconds $ReporterPollSeconds
            }
        }

        foreach ($result in @($results)) {
            if ($result.wrapper_log) { Append-ReporterWrapperLog -Path $result.wrapper_log }
            if ($result.usage_log -and (Test-Path $result.usage_log)) {
                Add-Content -Path $CodexUsageLog -Value (Get-Content -LiteralPath $result.usage_log -Raw -Encoding UTF8) -Encoding UTF8
            }
        }

        return @($results)
        } finally {
            # このwaveが所有するjobだけを例外時にも必ず回収する。生PID/process名は使わない。
            foreach ($ownedJob in @($jobs)) {
                $liveOwnedJob = Get-Job -Id $ownedJob.Id -ErrorAction SilentlyContinue
                if ($null -eq $liveOwnedJob) { continue }
                if ([string]$liveOwnedJob.JobStateInfo.State -in @('Running', 'NotStarted')) {
                    Write-Log "reporter job CLEANUP category=$($ownedJob.Category) attempt=$($ownedJob.Attempt)"
                    Stop-Job -Job $liveOwnedJob -ErrorAction SilentlyContinue
                }
                Receive-Job -Id $liveOwnedJob.Id -ErrorAction SilentlyContinue | Out-Null
                Remove-Job -Job $liveOwnedJob -Force -ErrorAction SilentlyContinue
            }
        }
    }

    $retryCategories = @($Categories)
    $terminalFailures = @{}
    $ReporterTerminalExitCode = 1
    if ($ResumeAfterReporter) {
        Write-Log "ResumeFromStage=${ResumeFromStage}: skipping reporter fan-out; verifying existing reporter artifacts"
        $retryCategories = @()
    } else {
        for ($attempt = 1; $attempt -le $ReporterMaxAttempts -and $retryCategories.Count -gt 0; $attempt++) {
            $waveResults = Invoke-ReporterWave -Attempt $attempt -WaveCategories $retryCategories
            if (-not (Test-ArtifactExecutableTreeIntegrity)) {
                Exit-Runner -Status 'blocked_artifact_executable_tree_invalid' -Message 'ARTIFACT_EXECUTABLE_TREE_INVALID immediately after reporter wave' -ExitCode 126
            }
            $nextRetryCategories = @()
            $failedCategories = @()

            foreach ($waveResult in $waveResults) {
            $catName = [string]$waveResult.category
            $failureReason = $null
            $verifyReporterArgs = @('-m', 'tools.verify_reporter_output', '--date', $DateStamp, '--category', $catName)
            $verifyCapture = Join-Path $ReporterArtifactDir "$catName.verify-attempt$attempt.log"
            Push-Location $RepoDir
            try {
                Invoke-LoggedCapture -CapturePath $verifyCapture -Block { & $PyExe @verifyReporterArgs }
                $verifyReporterRc = $LASTEXITCODE
            } finally {
                Pop-Location
            }
            if ($verifyReporterRc -eq 0) {
                if ([int]$waveResult.rc -ne 0) {
                    Write-Log "WARN: reporter wrapper rc=$($waveResult.rc) ignored after reporter artifact verification Green category=$catName attempt=$attempt"
                }
            } elseif (Test-ReporterCodexQuotaFailure -WaveResult $waveResult) {
                Stop-ExternalReadiness -Reason "codex CLI rate limit / out of credits during reporter category=$catName attempt=$attempt" -ExitCode 123 -Kind 'codex_quota' -System 'openai_codex' -ExternalStatus "rc=$($waveResult.rc)" -ExternalDetail "reporter:$catName attempt=$attempt wrapper_log=$($waveResult.wrapper_log)"
            } elseif ([int]$waveResult.rc -ne 0) {
                $failureReason = "wrapper_rc=$($waveResult.rc)"
                if ([string]$waveResult.failure_status) {
                    $failureReason += " status=$([string]$waveResult.failure_status) detail=$([string]$waveResult.failure_detail)"
                }
                if ([int]$waveResult.rc -eq 124) {
                    $ReporterTerminalExitCode = 124
                }
            } else {
                $verifyText = if (Test-Path $verifyCapture) { (Get-Content -LiteralPath $verifyCapture -Raw -Encoding UTF8).Trim() } else { '' }
                $failureReason = "verify_rc=$verifyReporterRc $verifyText"
            }

            if ($null -eq $failureReason) {
                continue
            }

            $failedCategories += $catName
            $failureSignature = Get-ReporterFailureSignature -Text $failureReason
            $previousSignature = $ReporterFailureSignatures[$catName]
            if ($previousSignature -and $previousSignature -eq $failureSignature) {
                Write-Log "ERROR: reporter same failure signature category=$catName attempt=$attempt signature=$failureSignature; stop retrying this category"
                Set-RunnerState -Status 'blocked_reporter_repeated_failure' -Message "reporter repeated failure category=$catName" -ExitCode 1 -Phase 'reporter' -Category $catName -Attempt $attempt
                $terminalFailures[$catName] = $failureReason
            } elseif ($attempt -ge $ReporterMaxAttempts) {
                Write-Log "ERROR: reporter exhausted attempts category=$catName attempt=$attempt signature=$failureSignature"
                $terminalFailures[$catName] = $failureReason
            } else {
                Write-Log "WARN: reporter failed category=$catName attempt=$attempt signature=$failureSignature; scheduling retry"
                $ReporterFailureSignatures[$catName] = $failureSignature
                $nextRetryCategories += $catName
            }
            }

            if ($failedCategories.Count -gt 0) {
                Write-Log "Stage2 reporter failed categories attempt=${attempt}: $($failedCategories -join ',')"
            }
            $retryCategories = @($nextRetryCategories)
        }
    }

    if ($terminalFailures.Count -gt 0 -or $retryCategories.Count -gt 0) {
        foreach ($failedCat in @($terminalFailures.Keys | Sort-Object)) {
            Write-Log "ERROR: reporter terminal failure category=$failedCat reason=$($terminalFailures[$failedCat])"
        }
        foreach ($retryCat in $retryCategories) {
            Write-Log "ERROR: reporter terminal failure category=$retryCat reason=retry loop ended before success"
        }
        Write-Log 'ERROR: Stage2 reporter fan-out failed; Stage3 editor integration is skipped'
        exit $ReporterTerminalExitCode
    }

    if (-not (Test-ArtifactExecutableTreeIntegrity)) {
        Exit-Runner -Status 'blocked_artifact_executable_tree_invalid' -Message 'ARTIFACT_EXECUTABLE_TREE_INVALID after reporter fan-out' -ExitCode 126
    }

    Push-Location $RepoDir
    try {
        Invoke-Logged { & $PyExe '-m' 'tools.prepare_editor_workspace' '--repo-root' $RepoDir '--date' $DateStamp }
        if ($LASTEXITCODE -ne 0) {
            Invoke-AutonomousCompletionPolicy -FailureKind 'content' -GateId 'newsroom-editor-workspace' -Reason 'failed to prepare issue-date editor workspace' -ExitCode $LASTEXITCODE
        }
    } finally {
        Pop-Location
    }

    foreach ($artifactCat in $Categories) {
        $catDedupFile = Join-Path $DedupedCandidateDir "$artifactCat.jsonl"
        $ReporterLastMessage = Join-Path $ReporterArtifactDir "$artifactCat.codex-last-message.json"
        $genre = $CategoryGenreMap[$artifactCat]
        $ReporterArtifacts += [pscustomobject]@{
            category = $artifactCat
            dedup_file = $catDedupFile
            digest_file = "digest/$genre/$DateStamp-$genre.md"
            records_file = "tmp/newsroom/$DateStamp/$artifactCat.records.jsonl"
            search_audit = "data/search_audit/$DateStamp/$artifactCat.json"
            last_message = $ReporterLastMessage
            schema = $ReporterRecordSchema
        }
    }
    $EditorInputManifest = Join-Path $ReporterArtifactDir 'editor-input-manifest.json'
    $audioHistoryPaths = @()
    for ($audioOffset = 1; $audioOffset -le 2; $audioOffset++) {
        $audioDay = ([datetime]::ParseExact($DateStamp, 'yyyy-MM-dd', $null)).AddDays(-$audioOffset).ToString('yyyy-MM-dd')
        $audioHistoryPaths += "digest/Summary/$audioDay-audio-script.md"
    }
    $editorManifest = [pscustomobject]@{
        date = $DateStamp
        scheduled_categories = @($Categories)
        reporter_artifacts = @($ReporterArtifacts | ForEach-Object { $_.records_file })
        reporter_artifact_details = $ReporterArtifacts
        dedup_file = $DedupedCandidateDir
        audio_script_history = $audioHistoryPaths
        source_policy = 'no_recollection'
    }
    $editorManifest | ConvertTo-Json -Depth 8 | Set-Content -Path $EditorInputManifest -Encoding UTF8

    $EditorPromptFile = Join-Path ([System.IO.Path]::GetTempPath()) ("news-grasp-editor-prompt-$DateStamp.md")
    $EditorProducerContractFile = Join-Path ([System.IO.Path]::GetTempPath()) ("news-grasp-editor-contract-$DateStamp-$PID.md")
    $ScheduledCategoryList = ($Categories -join ', ')
    $DateHeader = "今日の日付は $DateStamp (JST) である。Stage2 reporter artifact manifest は $EditorInputManifest にある。manifest の scheduled_categories は [$ScheduledCategoryList] で、Summary frontmatter categories/tags/sections は scheduled_categories のみ。非対象カテゴリの section を作らない。Stage1 dedup は build/deduped-candidates にある。音声原稿を作る場合は manifest の audio_script_history にある過去 2 日の path を確認し、構成・感想・締めの反復禁止と例文コピー禁止を守る。編集長は再収集せず、検証済み reporter 成果物の統合・横断 dedup 判断・Summary planning・append だけを行う。"
    $PromptBody = Get-Content -Path $PromptFile -Raw -Encoding UTF8
    Push-Location $RepoDir
    try {
        Invoke-LoggedCapture -CapturePath $EditorProducerContractFile -Block { & $PyExe '-m' 'tools.validate_editor_output_preview' '--print-producer-contract' }
        $editorProducerContractRc = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($editorProducerContractRc -ne 0) {
        Invoke-AutonomousCompletionPolicy -FailureKind 'content' -GateId 'newsroom-editor-contract' -Reason 'editor preview producer contract export failed' -ExitCode $editorProducerContractRc
    }
    $EditorProducerContract = Get-Content -Path $EditorProducerContractFile -Raw -Encoding UTF8
    Set-Content -Path $EditorPromptFile -Value ($DateHeader + "`n`n" + $EditorProducerContract + "`n`n" + $PromptBody) -Encoding UTF8
    Write-Log "editor prompt date injected: header='$DateHeader' -> $EditorPromptFile"

    function Resolve-EditorArtifactPath {
        param([Parameter(Mandatory=$true)][string] $RelativePath)
        if ([System.IO.Path]::IsPathRooted($RelativePath)) {
            throw "EDITOR_SNAPSHOT_PATH_INVALID: rooted path"
        }
        $normalized = $RelativePath.Replace('\', '/').TrimStart('/')
        $escapedDate = [regex]::Escape($DateStamp)
        $allowedPatterns = @(
            '^data/articles\.jsonl$',
            "^digest/Summary/$escapedDate(?:-audio-script)?\.md$",
            "^digest/[A-Za-z0-9_-]+/$escapedDate-[A-Za-z0-9_-]+\.md$",
            "^tmp/newsroom/$escapedDate/[A-Za-z0-9_-]+\.records\.jsonl$",
            "^data/search_audit/$escapedDate/[A-Za-z0-9_-]+\.json$"
        )
        if (-not @($allowedPatterns | Where-Object { $normalized -match $_ })) {
            throw "EDITOR_SNAPSHOT_PATH_INVALID: path is outside the artifact allowlist"
        }
        $repoFull = [System.IO.Path]::GetFullPath($RepoDir).TrimEnd('\', '/')
        $candidate = [System.IO.Path]::GetFullPath((Join-Path $repoFull $normalized))
        $repoPrefix = $repoFull + [System.IO.Path]::DirectorySeparatorChar
        if (-not $candidate.StartsWith($repoPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "EDITOR_SNAPSHOT_PATH_INVALID: path escaped repo root"
        }
        $current = $repoFull
        foreach ($segment in $normalized.Split('/')) {
            $current = Join-Path $current $segment
            if (Test-Path -LiteralPath $current) {
                $attributes = [System.IO.File]::GetAttributes($current)
                if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                    throw "EDITOR_SNAPSHOT_PATH_INVALID: reparse point"
                }
            }
        }
        return $candidate
    }

    function Get-EditorSnapshotSha256 {
        param([Parameter(Mandatory=$true)][string] $Path)
        return Get-NewsGraspFileSha256Hex -Path $Path
    }

    function New-EditorAttemptSnapshot {
        param([int] $Attempt)
        $tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\', '/')
        $tempAttributes = [System.IO.File]::GetAttributes($tempRoot)
        if (($tempAttributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw 'EDITOR_SNAPSHOT_PATH_INVALID: temp root is a reparse point'
        }
        $snapshotName = "news-grasp-editor-snapshot-$DateStamp-$PID-$Attempt-$([Guid]::NewGuid().ToString('N'))"
        $snapshotDir = [System.IO.Path]::GetFullPath((Join-Path $tempRoot $snapshotName))
        if ((Split-Path -Parent $snapshotDir) -ne $tempRoot -or (Test-Path -LiteralPath $snapshotDir)) {
            throw 'EDITOR_SNAPSHOT_PATH_INVALID: snapshot directory is not fresh'
        }
        New-Item -ItemType Directory -Path $snapshotDir -Force | Out-Null
        $snapshotAttributes = [System.IO.File]::GetAttributes($snapshotDir)
        if (($snapshotAttributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw 'EDITOR_SNAPSHOT_PATH_INVALID: snapshot directory is a reparse point'
        }
        $paths = @('data/articles.jsonl', "digest/Summary/$DateStamp.md", "digest/Summary/$DateStamp-audio-script.md")
        foreach ($artifact in $ReporterArtifacts) {
            foreach ($artifactPath in @($artifact.records_file, $artifact.digest_file, $artifact.search_audit)) {
                foreach ($scalarPath in @($artifactPath)) {
                    if (-not [string]::IsNullOrWhiteSpace([string]$scalarPath)) {
                        $paths += [string]$scalarPath
                    }
                }
            }
        }
        $entries = @()
        foreach ($relativePath in @($paths | Select-Object -Unique)) {
            $source = Resolve-EditorArtifactPath -RelativePath $relativePath
            $sha = [System.Security.Cryptography.SHA256]::Create()
            try {
                $snapshotName = ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($relativePath)))).Replace('-', '').ToLowerInvariant()
            } finally {
                $sha.Dispose()
            }
            $snapshotPath = Join-Path $snapshotDir $snapshotName
            $exists = Test-Path -LiteralPath $source
            if ($exists) {
                if (Test-Path -LiteralPath $source -PathType Container) {
                    throw "EDITOR_SNAPSHOT_DIRECTORY_FORBIDDEN: $relativePath"
                }
                Copy-Item -LiteralPath $source -Destination $snapshotPath -Force | Out-Null
            }
            $entries += [pscustomobject]@{
                relative_path = $relativePath
                existed = $exists
                snapshot_name = $snapshotName
                snapshot_sha256 = if ($exists) { Get-EditorSnapshotSha256 -Path $snapshotPath } else { '' }
            }
        }
        $manifestPath = Join-Path $snapshotDir 'manifest.json'
        $entries | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
        return [pscustomobject]@{
            manifest_path = $manifestPath
            manifest_sha256 = Get-EditorSnapshotSha256 -Path $manifestPath
            snapshot_dir = $snapshotDir
        }
    }

    function Restore-EditorAttemptSnapshot {
        param([object] $Snapshot)
        $manifestFull = [System.IO.Path]::GetFullPath([string]$Snapshot.manifest_path)
        $snapshotDir = Split-Path -Parent $manifestFull
        $tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\', '/')
        if ((Split-Path -Parent $snapshotDir) -ne $tempRoot -or
            [System.IO.Path]::GetFullPath([string]$Snapshot.snapshot_dir) -ne $snapshotDir -or
            -not (Test-Path -LiteralPath $snapshotDir -PathType Container) -or
            -not (Test-Path -LiteralPath $manifestFull -PathType Leaf)) {
            throw 'EDITOR_SNAPSHOT_MANIFEST_TAMPERED: snapshot identity mismatch'
        }
        foreach ($protectedPath in @($snapshotDir, $manifestFull)) {
            $attributes = [System.IO.File]::GetAttributes($protectedPath)
            if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw 'EDITOR_SNAPSHOT_MANIFEST_TAMPERED: reparse point'
            }
        }
        $expectedManifestSha = ([string]$Snapshot.manifest_sha256).ToLowerInvariant()
        if ($expectedManifestSha -notmatch '^[0-9a-f]{64}$' -or (Get-EditorSnapshotSha256 -Path $manifestFull) -ne $expectedManifestSha) {
            throw 'EDITOR_SNAPSHOT_MANIFEST_TAMPERED: manifest hash mismatch'
        }
        $entries = @(Get-Content -LiteralPath $manifestFull -Raw -Encoding UTF8 | ConvertFrom-Json)
        foreach ($entry in $entries) {
            $destination = Resolve-EditorArtifactPath -RelativePath ([string]$entry.relative_path)
            if ([bool]$entry.existed) {
                $snapshotName = [string]$entry.snapshot_name
                if ($snapshotName -notmatch '^[0-9a-f]{64}$') {
                    throw 'EDITOR_SNAPSHOT_MANIFEST_TAMPERED: snapshot name invalid'
                }
                $snapshotPath = [System.IO.Path]::GetFullPath((Join-Path $snapshotDir $snapshotName))
                $snapshotDirPrefix = [System.IO.Path]::GetFullPath($snapshotDir).TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
                if (-not $snapshotPath.StartsWith($snapshotDirPrefix, [System.StringComparison]::OrdinalIgnoreCase) -or
                    -not (Test-Path -LiteralPath $snapshotPath -PathType Leaf) -or
                    (([System.IO.File]::GetAttributes($snapshotPath) -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) -or
                    (Get-EditorSnapshotSha256 -Path $snapshotPath) -ne ([string]$entry.snapshot_sha256).ToLowerInvariant()) {
                    throw 'EDITOR_SNAPSHOT_MANIFEST_TAMPERED: snapshot hash mismatch'
                }
                $parent = Split-Path -Parent $destination
                if ($parent) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
                if (Test-Path -LiteralPath $destination -PathType Container) {
                    throw "EDITOR_SNAPSHOT_DIRECTORY_FORBIDDEN: $($entry.relative_path)"
                }
                Copy-Item -LiteralPath $snapshotPath -Destination $destination -Force | Out-Null
            } elseif (Test-Path -LiteralPath $destination) {
                if (Test-Path -LiteralPath $destination -PathType Container) {
                    throw "EDITOR_SNAPSHOT_DIRECTORY_FORBIDDEN: $($entry.relative_path)"
                }
                Remove-Item -LiteralPath $destination -Force
            }
        }
        Write-Log "editor attempt workspace restored from snapshot: $manifestFull"
        Remove-EditorAttemptSnapshot -Snapshot $Snapshot
    }

    function Remove-EditorAttemptSnapshot {
        param([object] $Snapshot)
        $snapshotDir = [System.IO.Path]::GetFullPath([string]$Snapshot.snapshot_dir)
        $tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\', '/')
        if ((Split-Path -Parent $snapshotDir) -ne $tempRoot -or
            -not ([System.IO.Path]::GetFileName($snapshotDir)).StartsWith("news-grasp-editor-snapshot-$DateStamp-$PID-", [System.StringComparison]::OrdinalIgnoreCase)) {
            throw 'EDITOR_SNAPSHOT_PATH_INVALID: cleanup identity mismatch'
        }
        if (-not (Test-Path -LiteralPath $snapshotDir)) { return }
        $snapshotAttributes = [System.IO.File]::GetAttributes($snapshotDir)
        if (($snapshotAttributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw 'EDITOR_SNAPSHOT_PATH_INVALID: cleanup reparse point'
        }
        foreach ($item in @(Get-ChildItem -LiteralPath $snapshotDir -Force)) {
            if ($item.PSIsContainer -or
                (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) -or
                ($item.Name -ne 'manifest.json' -and $item.Name -notmatch '^[0-9a-f]{64}$')) {
                throw 'EDITOR_SNAPSHOT_MANIFEST_TAMPERED: unexpected cleanup entry'
            }
            Remove-Item -LiteralPath $item.FullName -Force
        }
        Remove-Item -LiteralPath $snapshotDir -Force
    }

    $MaxAgentAttempts = 3
    if ($NoPublish) { $MaxAgentAttempts = 1 }
    $preHead = (& $GitExe @GitSafeArgs -C $RepoDir rev-parse HEAD 2>$null)
    $agentRc = $null
    $EditorRetryFeedback = ''
    $NewsroomEditorReasoningEffort = Get-ModelPolicyValue -Role 'newsroom_editor' -Key 'reasoning'
    for ($attempt = 1; $attempt -le $MaxAgentAttempts; $attempt++) {
        $editorAttemptSnapshot = New-EditorAttemptSnapshot -Attempt $attempt
        $EditorAttemptPromptFile = Join-Path ([System.IO.Path]::GetTempPath()) ("news-grasp-editor-prompt-$DateStamp-$PID-attempt-$attempt.md")
        $EditorAttemptPromptBody = Get-Content -Path $EditorPromptFile -Raw -Encoding UTF8
        if ($EditorRetryFeedback) {
            $EditorAttemptPromptBody += "`n`nEDITOR_PREVIEW_RETRY_FEEDBACK_V1`n前回出力は以下のsemantic validator理由で不採用。完成artifactを再生成し、指摘箇所を全件修正すること。`n" + $EditorRetryFeedback
        }
        Set-Content -Path $EditorAttemptPromptFile -Value $EditorAttemptPromptBody -Encoding UTF8
        $priorGateFailCount = [Math]::Max(0, $attempt - 1)
        $NewsroomEditorModel = Select-NewsroomEditorModel -GateFailCount $priorGateFailCount -DedupConflictCount 0 -AppendMismatch:$false -SummaryQualityScore 5 -DeepDiveThemeCount 1
        Write-Log "wrapper invoke START (agent=codex, role=newsroom_editor, attempt=$attempt/$MaxAgentAttempts, Wrapper=$CodexWrapper, Model=$NewsroomEditorModel, ReasoningEffort=$NewsroomEditorReasoningEffort, gate_fail_count=$priorGateFailCount, TimeoutSec=$TimeoutSec, IdleTimeoutSec=$IdleTimeoutSec)"
        $agentRc = Invoke-CodexWrapper -PromptFile $EditorAttemptPromptFile -TimeoutSec $TimeoutSec -IdleTimeoutSec $IdleTimeoutSec -Model $NewsroomEditorModel -ReasoningEffort $NewsroomEditorReasoningEffort -OutputSchema $EditorSummarySchema -FlowName 'newsroom_editor'
        Write-Log "wrapper invoke END (agent=codex, role=newsroom_editor, attempt=$attempt/$MaxAgentAttempts, rc=$agentRc)"

        if ($agentRc -eq 0) {
            $editorOutputPreview = Join-Path $ReporterArtifactDir 'editor-output.preview.json'
            $EditorPreviewValidationCapture = Join-Path ([System.IO.Path]::GetTempPath()) ("news-grasp-editor-materialization-$DateStamp-$PID-attempt-$attempt.log")
            $editorPreviewRc = Sync-EditorOutputPreview -PreviewPath $editorOutputPreview -FallbackPath $CodexLastMessage -CapturePath $EditorPreviewValidationCapture
            if ($editorPreviewRc -eq 0) {
                Remove-EditorAttemptSnapshot -Snapshot $editorAttemptSnapshot
                break
            }
            Write-Log "WARN: editor preview semantic validation failed attempt=$attempt rc=$editorPreviewRc; output was not materialized"
            $EditorRetryFeedback = Get-Content -Path $EditorPreviewValidationCapture -Raw -Encoding UTF8
            Restore-EditorAttemptSnapshot -Snapshot $editorAttemptSnapshot
            if ($attempt -ge $MaxAgentAttempts) {
                Invoke-AutonomousCompletionPolicy -FailureKind 'content' -GateId 'newsroom-editor-preview' -Reason 'editor preview semantic validation failed' -ExitCode $editorPreviewRc
            }
            continue
        }

        Restore-EditorAttemptSnapshot -Snapshot $editorAttemptSnapshot

        if ($agentRc -eq 124) {
            $postHead = (& $GitExe @GitSafeArgs -C $RepoDir rev-parse HEAD 2>$null)
            if ($postHead -ne $preHead) {
                Invoke-AutonomousCompletionPolicy -FailureKind 'content' -GateId 'newsroom-editor-timeout' -Reason "codex timeout after partial output (HEAD changed $preHead -> $postHead)" -ExitCode 124
            }
            if ($attempt -lt $MaxAgentAttempts) {
                Write-Log "WARN: codex idle/timeout (rc=124, HEAD unchanged = no output/commits): intermittent startup hang suspected, retrying (next attempt=$($attempt + 1)/$MaxAgentAttempts)"
                continue
            }
            Stop-ExternalReadiness -Reason "codex timeout after $MaxAgentAttempts attempts" -ExitCode 124 -Kind 'codex_timeout' -System 'openai_codex' -ExternalStatus "rc=124" -ExternalDetail "attempts=$MaxAgentAttempts"
        }

        if ($agentRc -eq 123) {
            Stop-ExternalReadiness -Reason "codex CLI rate limit / out of credits" -ExitCode 123 -Kind 'codex_quota' -System 'openai_codex' -ExternalStatus "rc=123" -ExternalDetail 'codex CLI rate limit or out of credits'
        }

        Stop-ExternalReadiness -Reason "codex exited with $agentRc" -ExitCode $agentRc -Kind 'codex_cli_failed' -System 'openai_codex' -ExternalStatus "rc=$agentRc" -ExternalDetail 'codex newsroom editor invocation'
    }
    if ($StopAfterEditorStart) {
        Write-Log 'StopAfterEditorStart mode: editor wrapper succeeded; skipping downstream gates'
        Write-Log 'news-grasp-runner.ps1 SMOKE OK'
        exit 0
    }
}

# ===== 2.1 Summary reflection gate (digest 生成直後・DeepDive/pytest 前) =====
# 2026-06-08: Summary md は生成されたが `## § 本日のテーマ考察` ブロックが欠落し、
# 後段 pytest で初めて止まった。生成直後の境界で fail loud にし、どの Summary を
# 直せばよいかを runner log に明示する。判定は `tools.generate_pages.parse_reflection`
# を使う `tools.validate_summary_reflection` に集約し、公開 HTML 側の抽出仕様と分岐させない。
Write-Log 'summary reflection gate start (validate_summary_reflection --date)'
$summaryReflectionRc = Invoke-AutonomousGate -GateId 'summary-reflection' -Category 'summary' -PythonArgs @('-m', 'tools.validate_summary_reflection', '--date', $DateStamp) -Artifacts @("digest/Summary/$DateStamp.md")
if ($summaryReflectionRc -ne 0) {
    Invoke-AutonomousCompletionPolicy -FailureKind 'content' -GateId 'summary-reflection' -Reason 'summary reflection autonomous gate failed' -ExitCode $summaryReflectionRc
}
Write-Log 'summary reflection gate OK'

# ===== 2.2 daily quality gate (hero fallback / stale source URL date) =====
# 2026-06-08: Summary の reflection は存在していても frontmatter hero_left / hero_right
# が欠落し、LP TODAY'S THEME がブランド fallback「時勢を掴み、日々に新たに。」へ
# 落ちた。また、記事 record の date は収集日であり、URL パス上の発行日が前日以前
# でも pre-push gate が検出できなかった。日次公開境界で両方を fail loud にする。
Write-Log "daily quality gate start (validate_daily_quality --date $DateStamp)"
$dailyQualityRc = Invoke-AutonomousGate -GateId 'daily-quality' -Category 'daily' -PythonArgs @('-m', 'tools.validate_daily_quality', '--date', $DateStamp, '--json') -Artifacts $DailyDigestArtifacts
if ($dailyQualityRc -ne 0) {
    Invoke-AutonomousCompletionPolicy -FailureKind 'content' -GateId 'daily-quality' -Reason 'daily quality autonomous gate failed' -ExitCode $dailyQualityRc
}
Write-Log 'daily quality gate OK'
Update-RunnerProgress -Phase 'checkpoint' -Step 'daily quality checkpoint committed' -ResumeStageCheckpoint 'post-daily-quality'

if ($StopBeforeDeepDive) {
    Write-Log 'pre-DeepDive production volume gate start'
    $ProductionVolumeTarget = 5
    $ProductionVolumeLedger = @()
    foreach ($volumeCat in $Categories) {
        $volumeGenre = $CategoryGenreMap[$volumeCat]
        $volumeRecordsPath = Join-Path $RepoDir "tmp\newsroom\$DateStamp\$volumeCat.records.jsonl"
        $volumeDigestPath = Join-Path $RepoDir "digest\$volumeGenre\$DateStamp-$volumeGenre.md"
        $volumeRecordCount = 0
        $volumeDigestCardCount = 0
        if (Test-Path -LiteralPath $volumeRecordsPath) {
            $volumeRecordCount = @(
                Get-Content -LiteralPath $volumeRecordsPath -Encoding UTF8 |
                    Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
            ).Count
        }
        if (Test-Path -LiteralPath $volumeDigestPath) {
            $volumeDigestCardCount = @(
                Get-Content -LiteralPath $volumeDigestPath -Encoding UTF8 |
                    Where-Object { $_ -match '^###\s+\[' }
            ).Count
        }
        $volumeStatus = if (($volumeRecordCount -ge $ProductionVolumeTarget) -and ($volumeDigestCardCount -ge $ProductionVolumeTarget)) { 'Green' } else { 'Yellow' }
        $ProductionVolumeLedger += [pscustomobject]@{
            category = $volumeCat
            digest_genre = $volumeGenre
            records_path = $volumeRecordsPath
            digest_path = $volumeDigestPath
            records_count = $volumeRecordCount
            digest_card_count = $volumeDigestCardCount
            target_count = $ProductionVolumeTarget
            status = $volumeStatus
        }
    }
    $ProductionVolumeLedgerPath = Join-Path $ReporterArtifactDir 'predeepdive-production-volume.json'
    $ProductionVolumeLedger | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $ProductionVolumeLedgerPath -Encoding UTF8
    $ProductionVolumeShortfalls = @($ProductionVolumeLedger | Where-Object { $_.status -ne 'Green' })
    if ($ProductionVolumeShortfalls.Count -gt 0) {
        $shortfallText = [string]::Join(
            ', ',
            @($ProductionVolumeShortfalls | ForEach-Object { "$($_.category):records=$($_.records_count),digest_cards=$($_.digest_card_count),target=$($_.target_count)" })
        )
        Write-Log "pre-DeepDive production volume gate failed ledger=$ProductionVolumeLedgerPath shortfall=$shortfallText"
        Set-RunnerState -Status 'failed_predeepdive_production_volume' -Message "pre-DeepDive production volume shortfall: $shortfallText" -ExitCode 65 -Phase 'gate' -Step 'predeepdive-production-volume' -GateId 'predeepdive-production-volume'
        exit 65
    }
    Write-Log "pre-DeepDive production volume gate OK ledger=$ProductionVolumeLedgerPath"
    Write-Log 'StopBeforeDeepDive mode: summary-reflection and daily-quality gates succeeded; stopping before Stage4 DeepDive'
    Write-Log 'news-grasp-runner.ps1 PRE DEEPDIVE E2E OK'
    exit 0
}

# ===== Stage4: Codex DeepDive 生成 + commit (テーマゲート式日次・非致命) =====
# 2026-06-01: 旧 news-grasp-weekly-runner.ps1 (毎週日曜 23:00 の別タスク) を日次に統合した step。
#   - digest とは別の agent プロセスで走らせ、コンテキスト/トークン予算を完全に分離する
#     (1 セッション統合は 2026-05 の 415 万トークン破綻の再来リスクがあるため採らない)。
#   - テーマが立たない日は prompts/deepdive-runner-prompt.md 側のテーマゲートで休載 (commit しない)。
#     = コストは「出す価値がある日だけ」に自己制御される。
#   - DeepDive は付随機能なので非致命: 失敗 / timeout / 休載でも digest の公開は絶対に止めない
#     (digest が主、DeepDive は additive)。エラーは WARN ログのみで step 3 以降に進む。
$DeepDivePromptFile = Join-Path $RepoDir 'prompts\deepdive-runner-prompt.md'
$DeepDiveContextPack = Join-Path $RepoDir ("build\deepdive-context\$DateStamp.json")
$DeepDiveTimeoutSec = 1800
$DeepDiveModel = Get-ModelPolicyValue -Role 'deepdive' -Key 'default'
$DeepDiveReasoningEffort = Get-ModelPolicyValue -Role 'deepdive' -Key 'reasoning'
$DeepDiveContextPackFailed = $false
if ((-not $RecoverOnly) -and (-not $ResumeAfterDeepDive) -and (-not $ResumeGenerationQualityRepair)) {
    Write-Log "deepdive context pack build start ($DeepDiveContextPack)"
    Push-Location $RepoDir
    try {
        Invoke-Logged { & $PyExe '-m' 'tools.deepdive_context_pack' '--date' $DateStamp '--repo-root' $RepoDir '--output' $DeepDiveContextPack }
        $DeepDiveContextPackRc = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($DeepDiveContextPackRc -ne 0) {
        $DeepDiveContextPackFailed = $true
        Write-Log "WARN: deepdive context pack failed rc=$DeepDiveContextPackRc; skipping deepdive codex because context pack failed"
    } else {
        Write-Log 'deepdive context pack build OK'
    }
}
if ($RecoverOnly) {
    Write-Log "RecoverOnly mode: skipping deepdive codex; keeping existing DeepDive state"
} elseif ($ResumeAfterDeepDive) {
    Write-Log 'ResumeFromStage mode: skipping deepdive codex; using existing DeepDive artifact'
} elseif ($ResumeGenerationQualityRepair) {
    Write-Log 'ResumeFromStage mode: skipping deepdive codex; generation-quality gate owns missing artifact repair'
} elseif ($DeepDiveContextPackFailed) {
    Write-Log 'skipping deepdive codex because context pack failed'
} else {
    Write-Log "deepdive wrapper invoke START (agent=codex, Model=$DeepDiveModel, ReasoningEffort=$DeepDiveReasoningEffort, TimeoutSec=$DeepDiveTimeoutSec, IdleTimeoutSec=$IdleTimeoutSec)"
    # 2026-06-10: IdleTimeoutSec 0 → 900 (digest 側と同じ理由。stream-json 既定化で
    # 15 分無出力 = 真のハング検知が成立。DeepDive は非致命なので誤検知しても digest は止まらない)
    $ddRc = Invoke-CodexWrapper -PromptFile $DeepDivePromptFile -TimeoutSec $DeepDiveTimeoutSec -IdleTimeoutSec $IdleTimeoutSec -Model $DeepDiveModel -ReasoningEffort $DeepDiveReasoningEffort -FlowName 'deepdive'
    Write-Log "deepdive wrapper invoke END (agent=codex, rc=$ddRc)"
    if ($ddRc -eq 124) {
        Write-Log "WARN: deepdive codex TIMEOUT after $DeepDiveTimeoutSec sec (non-fatal, digest は続行)"
    } elseif ($ddRc -ne 0) {
        Write-Log "WARN: deepdive codex exited with $ddRc (non-fatal, digest は続行)"
    } else {
    Write-Log "deepdive codex OK (1 本生成 or テーマゲート休載)"
    }
}

$GeneratedArtifacts = Get-PublishInventoryArtifacts -Kind 'generated'

Write-Log 'generation external readiness gate start'
$generationReadiness = Test-GenerationExternalReadiness
if (-not $generationReadiness.ok) {
    Stop-ExternalReadiness -Reason 'generation external readiness failed' -Kind 'generation_input_missing' -System 'local_artifact_inventory' -ExternalStatus $generationReadiness.status -ExternalStderr $generationReadiness.stderr -ExternalDetail $generationReadiness.detail
}
Write-Log 'generation external readiness gate OK'

Write-Log 'generation artifact normalize start (normalize_generated_artifacts)'
Push-Location $RepoDir
try {
    Invoke-Logged { & $PyExe '-m' 'tools.normalize_generated_artifacts' '--date' $DateStamp '--repo-root' $RepoDir }
    $generationNormalizeRc = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($generationNormalizeRc -ne 0) {
    Write-Log "generation artifact normalize failed (rc=$generationNormalizeRc)"
    Invoke-AutonomousCompletionPolicy -FailureKind 'artifact' -GateId 'generation-normalize' -Reason 'generation artifact normalize failed' -ExitCode $generationNormalizeRc
}
Write-Log 'generation artifact normalize OK'

Write-Log 'generation quality gate start (validate_generation_quality)'
Update-RunnerProgress -Phase 'checkpoint' -Step 'deepdive generation checkpoint committed' -ResumeStageCheckpoint 'generation-quality-repair'
$generationQualityRc = Invoke-AutonomousGate -GateId 'generation-quality' -Category 'generated' -PythonArgs @('-m', 'tools.validate_generation_quality', '--date', $DateStamp, '--repo-root', $RepoDir, '--json') -Artifacts $GeneratedArtifacts
if ($generationQualityRc -ne 0) {
    Write-Log "generation quality autonomous gate failed (rc=$generationQualityRc)"
    Invoke-AutonomousCompletionPolicy -FailureKind 'content' -GateId 'generation-quality' -Reason 'generation quality autonomous gate failed' -ExitCode $generationQualityRc
}
Write-Log 'generation quality gate OK'

# ===== 2.6 URL 生存検証ゲート (commit 後・push 前) =====
# 2026-06-03 三菱UFJ FX_Monthly 捏造事故 + 33 件の死リンク発覚を受けた構造防止。
# 旧 LLM セッション (日次 digest + DeepDive) は URL を記憶から補完して捏造することが
# 実測で判明。push 前に articles.jsonl + DeepDive md の URL を一括 HEAD/GET し、
# 1 件でも 404/410 等の fatal が出たら push を阻止する境界。境界 1 箇所集約により
# 「生成側が commit したが死リンクのまま公開」を構造的に消す。
# 検証窓は直近 7 日 (--gate): 公開直後の記事のみ、歴史的死リンクは別 ad-hoc 監査で扱う。
#
# 2026-06-04 追加 --match-session (案②-Lite): HEAD/GET だけでは LLM が記憶から
# 引いた「200 は返るが本来の WebSearch 結果に無い別記事 URL」までは弾けない。
# 日次 digest セッションが書き出す data/_session_urls.json と articles.jsonl 当日 URL
# を物理照合する。ただし非対話 codex exec では PostToolUse hook 由来の session 台帳が
# 必ず成立するとは限らないため、台帳不在だけでは止めず、URL 物理 gate と日付証拠を本線にする。
Write-Log 'URL liveness gate start (audit_all_article_urls --gate --match-session)'
$urlGateRc = Invoke-AutonomousGate -GateId 'url-liveness' -Category 'urls' -PythonArgs @('tools\audit_all_article_urls.py', '--gate', '--match-session', '--issue-date', $DateStamp) -Artifacts @('data/articles.jsonl', 'data/_session_urls.json', 'data/_session_urls.d') -NoRepair
if ($urlGateRc -ne 0) {
    Write-Log "URL liveness quarantine start (audit_all_article_urls --gate --match-session --quarantine-articles --apply)"
    Push-Location $RepoDir
    try {
        Invoke-Logged { & $PyExe 'tools\audit_all_article_urls.py' '--gate' '--match-session' '--issue-date' $DateStamp '--quarantine-articles' '--apply' }
        $urlQuarantineRc = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($urlQuarantineRc -eq 0) {
        $badUrlFile = Join-Path $RepoDir "build\quarantine\$DateStamp\bad-urls.json"
        if (Test-Path -LiteralPath $badUrlFile) {
            Write-Log "URL liveness refill start (tools.refill_category_after_quarantine, bad-url-file=$badUrlFile)"
            Push-Location $RepoDir
            try {
                $refillCategoriesJson = & $PyExe '-m' 'tools.refill_category_after_quarantine' '--list-categories' '--date' $DateStamp
                $refillCategoryListRc = $LASTEXITCODE
            } finally {
                Pop-Location
            }
            if ($refillCategoryListRc -ne 0) {
                Write-Log "URL liveness refill category list failed rc=$refillCategoryListRc"
                Invoke-AutonomousCompletionPolicy -FailureKind 'content' -GateId 'url-liveness' -Reason 'URL liveness refill category list failed' -ExitCode $refillCategoryListRc
            }
            try {
                # ConvertFrom-Json は Convert-JsonStringArrayToStringList の中で扱う。
                $refillCategories = Convert-JsonStringArrayToStringList -JsonText $refillCategoriesJson
            } catch {
                Write-Log "URL liveness refill category list parse failed: $($_.Exception.Message)"
                Invoke-AutonomousCompletionPolicy -FailureKind 'content' -GateId 'url-liveness' -Reason 'URL liveness refill category list parse failed' -ExitCode 1
            }
            foreach ($refillCat in $refillCategories) {
                if ([string]::IsNullOrWhiteSpace($refillCat)) {
                    continue
                }
                if ($refillCat -match '\s') {
                    Write-Log "URL liveness refill category contains whitespace: $refillCat"
                    Invoke-AutonomousCompletionPolicy -FailureKind 'content' -GateId 'url-liveness' -Reason "URL liveness refill category contains whitespace: $refillCat" -ExitCode 1
                }
                Push-Location $RepoDir
                try {
                    Invoke-Logged { & $PyExe '-m' 'tools.refill_category_after_quarantine' '--date' $DateStamp '--category' $refillCat '--bad-url-file' $badUrlFile '--candidate-dir' 'build\deduped-candidates' '--txid' "url-liveness-$refillCat" }
                    $refillRc = $LASTEXITCODE
                } finally {
                    Pop-Location
                }
                if ($refillRc -ne 0) {
                    Write-Log "URL liveness refill failed category=$refillCat rc=$refillRc"
                    Invoke-AutonomousCompletionPolicy -FailureKind 'content' -GateId 'url-liveness' -Reason "URL liveness refill failed category=$refillCat" -ExitCode $refillRc
                }
            }
            Write-Log 'URL liveness refill OK'
        } else {
            Write-Log 'URL liveness refill skipped: bad URL ledger not found'
        }
        Write-Log 'URL liveness gate recheck after quarantine'
        Push-Location $RepoDir
        try {
            Invoke-Logged { & $PyExe 'tools\audit_all_article_urls.py' '--gate' '--match-session' '--issue-date' $DateStamp }
            $urlRecheckRc = $LASTEXITCODE
        } finally {
            Pop-Location
        }
        if ($urlRecheckRc -eq 0) {
            Write-Log 'URL liveness gate OK after per-article quarantine'
            $urlGateRc = 0
        } else {
            Write-Log "URL liveness recheck failed after quarantine (rc=$urlRecheckRc). normal publish is blocked."
            Invoke-AutonomousCompletionPolicy -FailureKind 'content' -GateId 'url-liveness' -Reason 'URL liveness recheck failed after quarantine/refill' -ExitCode $urlRecheckRc
        }
    } else {
        Write-Log "URL liveness quarantine failed (rc=$urlQuarantineRc). normal publish is blocked."
        Invoke-AutonomousCompletionPolicy -FailureKind 'content' -GateId 'url-liveness' -Reason 'URL liveness quarantine failed' -ExitCode $urlQuarantineRc
    }
}
Write-Log 'URL liveness gate OK'

# ===== 2.65 record schema gate (commit 後・push 前) =====
# 2026-06-06 Plan v3 P0-B で導入。articles.jsonl の record schema 境界 1 箇所集約。
# `thumb` キー欠落 / `date` 形式不正 / `url` 欠落 / `genre` 未定義値 / 型ドリフトを
# 直近 7 日窓で検出し push 阻止する。2026-06-06 23 件 thumb 欠落事故 (test_thumb_contract
# が事後検出) と同 class of bugs を append 時境界として locked-in。
# `tools/validate_record.py` は純粋関数 + CLI を提供、本 gate は本番 daily append
# LLM append でも ad-hoc script (`append_*.py`) でも効く位置にいる。
Write-Log "record schema gate start (validate_record --recent 7 --issue-date $DateStamp)"
$recordGateRc = Invoke-AutonomousGate -GateId 'record-schema' -Category 'records' -PythonArgs @('-m', 'tools.validate_record', '--recent', '7', '--issue-date', $DateStamp) -Artifacts @('data/articles.jsonl')
if ($recordGateRc -ne 0) {
    Invoke-AutonomousCompletionPolicy -FailureKind 'content' -GateId 'record-schema' -Reason 'record schema autonomous gate failed' -ExitCode $recordGateRc
}
Write-Log 'record schema gate OK'

# ===== 2.66 digest/articles 突合 gate (commit 後・push 前) =====
# 2026-06-13 Phase 3: digest md と articles.jsonl の当日 URL 集合を完全一致させる。
# 片方向の「digest md ⊆ articles.jsonl」だけでは、freshness gate が古記事を jsonl から
# 正しく落としたのに md にだけ残ったケースを append 漏れと誤検出する。双方向一致により
# digest-only は「古記事残存または append 漏れ」、articles-only は「カード生成漏れ」として
# push 前に止める。
Write-Log "digest/articles reconcile gate start (validate_digest_articles_reconcile --issue-date $DateStamp)"
$reconcileGateRc = Invoke-AutonomousGate -GateId 'digest-articles-reconcile' -Category 'digest' -PythonArgs @('-m', 'tools.validate_digest_articles_reconcile', '--issue-date', $DateStamp) -Artifacts @('digest', 'data/articles.jsonl')
if ($reconcileGateRc -ne 0) {
    Invoke-AutonomousCompletionPolicy -FailureKind 'content' -GateId 'digest-articles-reconcile' -Reason 'digest/articles reconcile autonomous gate failed' -ExitCode $reconcileGateRc
}
Write-Log 'digest/articles reconcile gate OK'

# ===== 2.7 [!ja] 和訳 callout 必須ゲート (commit 後・push 前) =====
# 2026-06-06 朝の SSG 失敗を受けた構造防止 ([[feedback_check_design_principles]] §2/§4)。
# 既存の validate_ja_callout_coverage() (generate_pages.py の Lv1 illegal state guard) は
# docs/ 反映を物理ブロックするが、Runner では「ステップ 3 digest push → ステップ 4
# generate_pages.py で初めて検知 → docs/ 未更新」という経路で digest だけ public に出て
# docs/ が古いままという中途半端な状態を作った (2026-06-06 [82] Microsoft/Google 記事の
# [!ja] 欠落事故)。push 前に既存 Lv4 契約テスト test_english_articles_require_ja_callout
# を強制発火し、1 件でも欠落があれば push 全停止 → digest commit も push されない構造に
# する。URL liveness gate と同じ pre-push 境界に集約。
Write-Log 'ja-callout gate start (test_english_articles_require_ja_callout)'
$jaGateRc = Invoke-AutonomousGate -GateId 'ja-callout' -Category 'digest' -PythonArgs @('-m', 'pytest', 'tests/test_title_ja_coverage.py::test_english_articles_require_ja_callout', '-q', '--tb=short', '--no-header') -Artifacts @('digest')
if ($jaGateRc -ne 0) {
    Invoke-AutonomousCompletionPolicy -FailureKind 'content' -GateId 'ja-callout' -Reason 'ja-callout autonomous gate failed' -ExitCode $jaGateRc
}
Write-Log 'ja-callout gate OK'

# ===== 2.8 pytest 全件 PASS ゲート (commit 後・push 前) =====
# 2026-06-06 Plan v2 で導入された B-1 ロックダウン。既存の 2.6 URL liveness と
# 2.7 [!ja] callout はピンポイント検査だが、生成セッション側で「test_thumb_contract
# が FAIL のまま judgement bypass で push 通過」した実害が発覚 (同日 2 commit
# 4e610c4 / efc8fa9)。tests/ の test FAIL を 1 件でも残したまま public へ
# 出るのを物理ブロックする。
#
# 2026-06-06 Plan v3 P2 で `-m "not network"` 方式に標準化。外部 HTTP 実打鍵 test
# (`@pytest.mark.network` 付与) は本 gate では除外し、静的検査のみを全件 PASS
# させる。実 HTTP 検証は 2.6 URL liveness gate (= audit_all_article_urls --gate)
# で別途担保している。News-Grasp/conftest.py が `NEWS_GRASP_SKIP_URL_CHECK=1`
# 互換 wrapper を持つので、旧呼び出し経路 (env で skip) も移行期は引き続き効く。
# 「別件」「無関係」judgement での bypass は禁止 — 1 件でも FAIL なら修正してから
# 再 push する ([[feedback_check_design_principles]] 1 段 illegal state unrepresentable
# + 2 段 境界 1 箇所集約)。
if (-not $NoPublish) {
    Write-Log 'ReleaseGateProfile deferred from scheduled/recovery path (pytest full regression is final-only)'
} else {
Write-Log 'pytest gate start (pytest tests/ -q -m "not network")'
$PytestBaseTempRoot = Join-Path ([System.IO.Path]::GetTempPath()) 'ng-pytest'
New-Item -ItemType Directory -Force -Path $PytestBaseTempRoot | Out-Null
$pytestRootItem = Get-Item -LiteralPath $PytestBaseTempRoot -Force -ErrorAction Stop
if (($pytestRootItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw 'PYTEST_BASETEMP_ROOT_REPARSE_POINT_FORBIDDEN'
}
$pytestRootOwner = [string](Get-Acl -LiteralPath $PytestBaseTempRoot -ErrorAction Stop).Owner
if (-not $pytestRootOwner -or $pytestRootOwner -notmatch [regex]::Escape([string]$env:USERNAME)) {
    throw 'PYTEST_BASETEMP_ROOT_OWNER_INVALID'
}
$PytestBaseTemp = Join-Path $PytestBaseTempRoot "$DateStamp-$RunId-$([Guid]::NewGuid().ToString('N'))"
if (Test-Path -LiteralPath $PytestBaseTemp) {
    throw 'PYTEST_BASETEMP_LEAF_COLLISION'
}
$previousPytestAddopts = $env:PYTEST_ADDOPTS
$previousSkipUrlCheck = $env:NEWS_GRASP_SKIP_URL_CHECK
try {
    if ([string]::IsNullOrWhiteSpace($previousPytestAddopts)) {
        $env:PYTEST_ADDOPTS = "--basetemp=$PytestBaseTemp"
    } elseif ($previousPytestAddopts -notmatch '--basetemp(?:=|\s)') {
        $env:PYTEST_ADDOPTS = "$previousPytestAddopts --basetemp=$PytestBaseTemp"
    }
    $env:NEWS_GRASP_SKIP_URL_CHECK = '1'
    $pytestGateRc = Invoke-AutonomousGate -GateId 'pytest-static' -Category 'tests' -PythonArgs @('-m', 'pytest', 'tests/', '-q', '--tb=line', '--no-header', '-m', 'not network') -Artifacts @('tests', 'tools', 'prompts', 'digest', 'data/articles.jsonl')
} finally {
    if ($null -eq $previousPytestAddopts) {
        Remove-Item Env:\PYTEST_ADDOPTS -ErrorAction SilentlyContinue
    } else {
        $env:PYTEST_ADDOPTS = $previousPytestAddopts
    }
    if ($null -eq $previousSkipUrlCheck) {
        Remove-Item Env:\NEWS_GRASP_SKIP_URL_CHECK -ErrorAction SilentlyContinue
    } else {
        $env:NEWS_GRASP_SKIP_URL_CHECK = $previousSkipUrlCheck
    }
}
if ($pytestGateRc -ne 0) {
    Write-Log "pytest gate failed after bounded repair (rc=$pytestGateRc). normal publish is blocked."
    Invoke-AutonomousCompletionPolicy -FailureKind 'local-tool' -GateId 'pytest-static' -Reason 'pytest autonomous gate failed' -ExitCode $pytestGateRc
}
Write-Log 'pytest gate OK'
}

# ===== 2.81 batch SLO gate (commit 後・publish 前) =====
# 1時間 / 300万token を超える自走は goal 未達として止める。
Write-Log 'batch SLO gate start'
Push-Location $RepoDir
try {
    Invoke-Logged { & $PyExe '-m' 'tools.validate_batch_slo' '--usage-log' $CodexUsageLog '--max-total-tokens' '3000000' '--max-window-sec' '3600' '--since' $script:RunnerProcessCreationTime }
    $batchSloRc = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($batchSloRc -ne 0) {
    Exit-Runner -Status 'blocked_slo_violation' -Message 'batch SLO gate failed before publish' -ExitCode $batchSloRc -Phase 'gate' -GateId 'batch-slo' -Category 'runner'
}
Write-Log 'batch SLO gate OK'

# ===== 2.85 Daily TTS audio (fatal, editor 後・generate_pages 前) =====
# 2026-06-16: 編集長が生成した digest/Summary/{date}-audio-script.md を AivisSpeech で
# mp3 化し、GitHub Releases audio-daily へ公開する。2026-06-17 以降は通常公開必須
# 成果物なので、失敗時は公開・fallback・通知へ進ませない。
$dailyTtsPublishArgs = @('-m', 'tools.tts.publish_audio', $DateStamp, '--json')
if ($NoPublish) { $dailyTtsPublishArgs = @('-m', 'tools.tts.publish_audio', $DateStamp, '--dry-run', '--json') }
foreach ($ttsStep in @(
    @{ Name = 'tts build_script'; Args = @('-m', 'tools.tts.build_script', $DateStamp) },
    @{ Name = 'tts synthesize_daily'; Args = @('-m', 'tools.tts.synthesize_daily', $DateStamp) },
    @{ Name = 'tts publish_audio'; Args = $dailyTtsPublishArgs }
)) {
    Write-Log "$($ttsStep.Name) start"
    try {
        Push-Location $RepoDir
        try {
            Invoke-Logged { & $PyExe @($ttsStep.Args) }
            $ttsRc = $LASTEXITCODE
        } finally {
            Pop-Location
        }
        if ($ttsRc -ne 0) {
            if ($ttsRc -eq 71 -and $ttsStep.Name -eq 'tts publish_audio') {
                Stop-ExternalReadiness `
                    -Reason 'GitHub Release audio upload service unavailable' `
                    -ExitCode 71 `
                    -Kind 'github_release_upload_transient' `
                    -System 'github-release' `
                    -ExternalStatus 'service_unavailable' `
                    -ExternalDetail 'tools.tts.publish_audio --json; typed evidence is recorded in the runner log'
            }
            Write-Log "ERROR: $($ttsStep.Name) exited with $ttsRc. TTS is required for normal publish."
            Invoke-AutonomousCompletionPolicy -FailureKind 'local-tool' -GateId 'daily-tts' -Reason "$($ttsStep.Name) failed" -ExitCode $ttsRc
        }
        Write-Log "$($ttsStep.Name) done"
    } catch {
        Write-Log "ERROR: $($ttsStep.Name) failed: $($_.Exception.Message). TTS is required for normal publish."
        Invoke-AutonomousCompletionPolicy -FailureKind 'local-tool' -GateId 'daily-tts' -Reason "$($ttsStep.Name) failed" -ExitCode 1
    }
}

# ===== 2.86 DeepDive dialogue audio (fatal, generate_pages 前) =====
# DeepDive 記事の理解補助として、対談台本を AivisSpeech で mp3 化し、
# GitHub Releases audio-deepdive へ公開する。generate_pages はこの URL を
# LP/DeepDive 記事へ埋め込むため、docs 生成前に完了させる。
$DeepDiveMarkdown = Join-Path $RepoDir ("digest\DeepDive\$DateStamp-DeepDive.md")
$DeepDiveDialogueScript = Join-Path $RepoDir ("digest\DeepDive\$DateStamp-DeepDive-dialogue.md")
$DeepDiveProvenanceManifest = Join-Path $RepoDir ("data\deepdive-provenance\$DateStamp.json")
Write-Log "current DeepDive provenance capture start ($DateStamp)"
Push-Location $RepoDir
try {
    Invoke-Logged { & $PyExe '-m' 'tools.deepdive_quality' 'capture' '--article' $DeepDiveMarkdown '--output' $DeepDiveProvenanceManifest }
    $currentDeepDiveProvenanceRc = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($currentDeepDiveProvenanceRc -ne 0) {
    Write-Log "current DeepDive provenance capture failed (rc=$currentDeepDiveProvenanceRc). dialogue synthesis and normal publish are blocked."
    Invoke-AutonomousCompletionPolicy -FailureKind 'content' -GateId 'deepdive-shared-quality' -Reason 'deepdive_url_provenance_invalid' -ExitCode $currentDeepDiveProvenanceRc
}
Write-Log 'current DeepDive provenance capture OK'

$deepDiveTtsPublishArgs = @('-m', 'tools.tts.deepdive_audio', $DateStamp, '--json')
if ($NoPublish) { $deepDiveTtsPublishArgs = @('-m', 'tools.tts.deepdive_audio', $DateStamp, '--dry-run', '--json') }
foreach ($deepDiveTtsStep in @(
    @{ Name = 'deepdive dialogue script build'; Args = @('-m', 'tools.tts.build_deepdive_dialogue_script', $DeepDiveMarkdown, '--output', $DeepDiveDialogueScript, '--context-pack', $DeepDiveContextPack); FailureKind = 'local-tool'; GateId = 'deepdive-tts'; UseAutonomousGate = $false; Artifacts = @($DeepDiveMarkdown, $DeepDiveDialogueScript) },
    @{ Name = 'deepdive shared quality gate'; Args = @('-m', 'tools.deepdive_quality', '--repo-root', $RepoDir, 'audit-issue', '--date', $DateStamp); FailureKind = 'content'; GateId = 'deepdive-shared-quality'; UseAutonomousGate = $true; Artifacts = @($DeepDiveMarkdown, $DeepDiveDialogueScript, $DeepDiveProvenanceManifest) },
    @{ Name = 'deepdive dialogue synthesize'; Args = @('-m', 'tools.tts.deepdive_dialogue', $DeepDiveDialogueScript, '--out-name', $DateStamp); FailureKind = 'local-tool'; GateId = 'deepdive-tts'; UseAutonomousGate = $false; Artifacts = @($DeepDiveDialogueScript) },
    @{ Name = 'deepdive dialogue publish'; Args = $deepDiveTtsPublishArgs; FailureKind = 'local-tool'; GateId = 'deepdive-tts'; UseAutonomousGate = $false; Artifacts = @($DeepDiveDialogueScript) }
)) {
    Write-Log "$($deepDiveTtsStep.Name) start"
    try {
        Push-Location $RepoDir
        try {
            if ($deepDiveTtsStep.UseAutonomousGate) {
                $deepDiveTtsRc = Invoke-AutonomousGate -GateId 'deepdive-shared-quality' -Category 'deepdive' -PythonArgs @($deepDiveTtsStep.Args) -Artifacts @($deepDiveTtsStep.Artifacts)
            } else {
                Invoke-Logged { & $PyExe @($deepDiveTtsStep.Args) }
                $deepDiveTtsRc = $LASTEXITCODE
            }
        } finally {
            Pop-Location
        }
        if ($deepDiveTtsRc -ne 0) {
            Write-Log "ERROR: $($deepDiveTtsStep.Name) exited with $deepDiveTtsRc. DeepDive dialogue audio is required for normal publish."
            if ($deepDiveTtsRc -eq 71 -and $deepDiveTtsStep.Name -eq 'deepdive dialogue publish') {
                Stop-ExternalReadiness `
                    -Reason 'GitHub Release DeepDive audio upload service unavailable' `
                    -GateId 'deepdive-tts' `
                    -Kind 'github_release_upload_transient' `
                    -System 'github-release' `
                    -ExternalStatus 'service_unavailable' `
                    -ExternalDetail 'tools.tts.deepdive_audio --json; typed evidence is recorded in the runner log'
            }
            $failureReason = "$($deepDiveTtsStep.Name) failed"
            if ($deepDiveTtsStep.GateId -eq 'deepdive-shared-quality') {
                Invoke-AutonomousCompletionPolicy -FailureKind 'content' -GateId 'deepdive-shared-quality' -Reason $failureReason -ExitCode $deepDiveTtsRc
            } else {
                Invoke-AutonomousCompletionPolicy -FailureKind 'local-tool' -GateId 'deepdive-tts' -Reason $failureReason -ExitCode $deepDiveTtsRc
            }
        }
        Write-Log "$($deepDiveTtsStep.Name) done"
    } catch {
        Write-Log "ERROR: $($deepDiveTtsStep.Name) failed: $($_.Exception.Message). DeepDive dialogue audio is required for normal publish."
        $failureReason = "$($deepDiveTtsStep.Name) failed: $($_.Exception.Message)"
        if ($deepDiveTtsStep.GateId -eq 'deepdive-shared-quality') {
            Invoke-AutonomousCompletionPolicy -FailureKind 'content' -GateId 'deepdive-shared-quality' -Reason $failureReason -ExitCode 1
        } else {
            Invoke-AutonomousCompletionPolicy -FailureKind 'local-tool' -GateId 'deepdive-tts' -Reason $failureReason -ExitCode 1
        }
    }
}

# ===== 2.9 digest/data commit (全 content gate 通過後・docs 生成前) =====
# 2026-06-09 改定で生成側は commit しなくなった (routine-system.md ステップ 6:
# 「commit / push は ps1 が代行」)。しかし旧実装は docs/ しか git add しておらず、
# digest md / data/articles.jsonl が永久に未コミットになる片手落ちだった
# (2026-06-10 発覚)。gate 通過済みの digest / data のみを path 指定で stage し、
# 無関係な作業ツリー変更 (SETUP.md / tests 等) は巻き込まない。fallback 経路は
# この step を通らないため「未検証 digest commit が fallback push に乗る」事故は
# 引き続き構造的に起きない。
if ($NoPublish) {
    Write-Log 'NoPublish mode: skipping digest/data git add + commit'
} else {
    if (-not (Invoke-GitAddWithIndexLockRetry -Label 'digest/data' -Pathspecs @('digest/', 'data/'))) {
        Write-Log "ERROR: git add digest/data failed"
        exit 1
    }
    Invoke-Logged { & $GitExe @GitSafeArgs -C $RepoDir diff --cached --quiet }
    $digestDiffRc = $LASTEXITCODE
    if ($digestDiffRc -eq 1) {
        Write-Log 'digest/data has changes, committing'
        Invoke-Logged { & $GitExe @GitSafeArgs -C $RepoDir commit -m "daily: digest and data for $DateStamp" }
        if ($LASTEXITCODE -ne 0) { Write-Log "ERROR: digest commit failed (rc=$LASTEXITCODE)"; exit 1 }
    } elseif ($digestDiffRc -eq 0) {
        Write-Log 'digest/data no changes (commit skip)'
    } else {
        Write-Log "ERROR: git diff --cached (digest) returned unexpected rc=$digestDiffRc"
        exit 1
    }
}

# ===== 3. docs/ 再生成 (旧 step 4 を前倒し / Plan v3 P0-A) =====
# 2026-06-06 Plan v3 P0-A: 旧構造は「digest push → docs build → docs push」で
# generate_pages.py 失敗時に digest md のみ origin 公開 + docs HTML 古いままという
# illegal state を表現可能だった。新構造は build 失敗で exit 1 → push 自体が走らない
# = サイレント公開停止が構造的に消える。
Write-Log 'generate_pages.py start'
$previousGeneratePagesSkipUrlCheck = $env:NEWS_GRASP_SKIP_URL_CHECK
Push-Location $RepoDir
try {
    # 本日 DeepDive URL は直前 gate で検証済み。SSG は HTML 生成責務に限定し、
    # 過去 DeepDive の経年 404 を本日 publish の内部停止要因にしない。
    $env:NEWS_GRASP_SKIP_URL_CHECK = '1'
    Invoke-Logged { & $PyExe 'tools\generate_pages.py' }
    $pagesRc = $LASTEXITCODE
} finally {
    if ([string]::IsNullOrEmpty($previousGeneratePagesSkipUrlCheck)) {
        Remove-Item Env:\NEWS_GRASP_SKIP_URL_CHECK -ErrorAction SilentlyContinue
    } else {
        $env:NEWS_GRASP_SKIP_URL_CHECK = $previousGeneratePagesSkipUrlCheck
    }
    Pop-Location
}
if ($pagesRc -ne 0) {
    Write-Log "generate_pages.py exited with $pagesRc. normal publish is blocked."
    Invoke-AutonomousCompletionPolicy -FailureKind 'local-tool' -GateId 'generate-pages' -Reason 'generate-pages failed' -ExitCode $pagesRc
}
Write-Log 'generate_pages.py done'

# ===== 3.05 DeepDive 必須 gate (generate 後・push 前) =====
# 2026-06-15: RecoverOnly 復旧時に Stage4 DeepDive を skip したまま Summary/カテゴリだけ
# 公開完了扱いにしてしまった。通常公開の完了条件は digest + docs + 当日 DeepDive まで
# 揃っていることなので、generate_pages.py 後に md/html の存在を fail loud にする。
Write-Log "deepdive required gate start (validate_daily_quality --date $DateStamp --require-deepdive)"
$deepDiveRequiredRc = Invoke-AutonomousGate -GateId 'deepdive-required' -Category 'daily' -PythonArgs @('-m', 'tools.validate_daily_quality', '--date', $DateStamp, '--docs-root', 'docs', '--require-deepdive') -Artifacts $PublishedRepairArtifacts
if ($deepDiveRequiredRc -ne 0) {
    Invoke-AutonomousCompletionPolicy -FailureKind 'content' -GateId 'deepdive-required' -Reason 'deepdive required autonomous gate failed' -ExitCode $deepDiveRequiredRc
}
Write-Log 'deepdive required gate OK'

# ===== 3.1 公開HTML smoke gate (generate 後・push 前) =====
# Summary md / digest md の構造 gate を通っても、最終成果物 docs/index.html 側で
# TOP STORY 画像や hero lead が退化する経路が残っていた。公開される HTML を
# 1 箇所で検査し、画像なし TOP STORY / 色面 fallback / 短文 lead を push 前に止める。
Write-Log "public HTML gate start (validate_public_home --date $DateStamp)"
$publicHomeRc = Invoke-AutonomousGate -GateId 'public-html' -Category 'docs' -PythonArgs @('-m', 'tools.validate_public_home', '--date', $DateStamp) -Artifacts @('docs/index.html')
if ($publicHomeRc -ne 0) {
    Write-Log "public HTML gate failed after bounded repair (rc=$publicHomeRc). normal publish is blocked."
    Invoke-AutonomousCompletionPolicy -FailureKind 'content' -GateId 'public-html' -Reason 'public HTML autonomous gate failed' -ExitCode $publicHomeRc
}
Write-Log 'public HTML gate OK'

Write-Log 'availability gate start (validate_availability)'
Push-Location $RepoDir
try {
    Invoke-Logged { & $PyExe '-m' 'tools.validate_availability' }
    $availabilityGateRc = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($availabilityGateRc -ne 0) {
    Write-Log "ERROR: availability gate failed (rc=$availabilityGateRc). docs/index.html が公開可能状態ではないため push を中止"
    exit 1
}
Write-Log 'availability gate OK'

# ===== 3.5 publish-status を published_ok にリセット (手動/歴史 fallback 状態の同期) =====
# 通常日次経路の fallback publish は禁止。ただし過去または手動緊急公開の
# published_fallback_with_notice が残ると send_push が通知を抑止するため、
# 成功経路では必ず published_ok に戻す。
# docs/ 配下なので直後の git add docs/ で commit + push され、公開面の状態が同期する。
Push-Location $RepoDir
try {
    Invoke-Logged { & $PyExe '-m' 'tools.publish_fallback' 'mark-ok' '--date' $DateStamp }
    $markOkRc = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($markOkRc -ne 0) { Write-Log "WARN: publish_fallback mark-ok exited $markOkRc (non-fatal)" }

# ===== 4. docs/ commit (差分があれば) =====
if ($NoPublish) {
    Write-Log 'NoPublish mode: skipping docs git add + commit'
} else {
    if (-not (Invoke-GitAddWithIndexLockRetry -Label 'docs' -Pathspecs @('docs/'))) {
        Write-Log "ERROR: git add docs/ failed"
        exit 1
    }

    # git diff --cached --quiet docs/ は差分があると exit 1、無いと exit 0。
    Invoke-Logged { & $GitExe @GitSafeArgs -C $RepoDir diff --cached --quiet -- 'docs/' }
    $diffRc = $LASTEXITCODE
    if ($diffRc -eq 1) {
        Write-Log 'docs/ has changes, committing'
        Invoke-Logged { & $GitExe @GitSafeArgs -C $RepoDir commit -m "docs: generate public pages for $DateStamp" }
        if ($LASTEXITCODE -ne 0) { Write-Log "ERROR: docs commit failed (rc=$LASTEXITCODE)"; exit 1 }
    } elseif ($diffRc -eq 0) {
        Write-Log 'docs no changes (digest commit のみを push します)'
    } else {
        Write-Log "ERROR: git diff --cached returned unexpected rc=$diffRc"
        exit 1
    }
}

# ===== 4.5 YouTube Podcast prepare (fatal, push 直前) =====
# push 前は private upload までに留め、Web publish が失敗したときに YouTube だけ public
# になる一時不整合を避ける。rerun は uploads.json の mp4_sha256/videoId で skip する。
Write-Log 'youtube oauth readiness gate start'
Update-RunnerProgress -Phase 'youtube-oauth-ready' -Step 'youtube oauth readiness gate start'
if (-not (Test-YouTubePodcastAuthReadiness)) {
    Invoke-AutonomousCompletionPolicy -FailureKind 'distribution' -GateId 'youtube-podcast-auth' -Reason 'youtube oauth readiness failed' -ExitCode 1
}
Write-Log 'youtube oauth readiness gate OK'

$youtubePodcastPrepareArgs = @('-m', 'tools.youtube_podcast.upload_episode', $DateStamp, '--prepare')
$deepDiveYoutubePodcastPrepareArgs = @('-m', 'tools.youtube_podcast.upload_episode', $DateStamp, '--kind', 'deepdive', '--prepare')
if ($NoPublish) {
    $youtubePodcastPrepareArgs = @('-m', 'tools.youtube_podcast.upload_episode', $DateStamp, '--prepare', '--dry-run')
    $deepDiveYoutubePodcastPrepareArgs = @('-m', 'tools.youtube_podcast.upload_episode', $DateStamp, '--kind', 'deepdive', '--prepare', '--dry-run')
}
foreach ($youtubePodcastStep in @(
    @{ Name = 'youtube podcast build_video'; Args = @('-m', 'tools.youtube_podcast.build_video', $DateStamp) },
    @{ Name = 'deepdive youtube podcast build_video'; Args = @('-m', 'tools.youtube_podcast.build_video', $DateStamp, '--kind', 'deepdive') },
    @{ Name = 'youtube podcast prepare'; Args = $youtubePodcastPrepareArgs },
    @{ Name = 'deepdive youtube podcast prepare'; Args = $deepDiveYoutubePodcastPrepareArgs }
)) {
    Write-Log "$($youtubePodcastStep.Name) start"
    try {
        Push-Location $RepoDir
        try {
            Invoke-Logged { & $PyExe @($youtubePodcastStep.Args) }
            $youtubePodcastRc = $LASTEXITCODE
        } finally {
            Pop-Location
        }
        if ($youtubePodcastRc -ne 0) {
            Write-Log "ERROR: $($youtubePodcastStep.Name) exited with $youtubePodcastRc. YouTube Podcast is required for normal publish."
            Invoke-AutonomousCompletionPolicy -FailureKind 'distribution' -GateId 'youtube-podcast-prepare' -Reason "$($youtubePodcastStep.Name) failed" -ExitCode $youtubePodcastRc
        }
        Write-Log "$($youtubePodcastStep.Name) done"
    } catch {
        Write-Log "ERROR: $($youtubePodcastStep.Name) failed: $($_.Exception.Message). YouTube Podcast is required for normal publish."
        Invoke-AutonomousCompletionPolicy -FailureKind 'distribution' -GateId 'youtube-podcast-prepare' -Reason "$($youtubePodcastStep.Name) failed" -ExitCode 1
    }
}

$distributionSummary = Write-DistributionManifest
Write-Log "distribution manifest written before push: $distributionSummary"
if ($NoPublish) {
    Write-Log 'NoPublish mode: skipping distribution manifest git add + commit'
} else {
    Invoke-Logged { & $GitExe @GitSafeArgs -C $RepoDir add "data/distribution/$DateStamp.json" }
    if ($LASTEXITCODE -ne 0) { Write-Log "ERROR: git add distribution manifest failed (rc=$LASTEXITCODE)"; exit 1 }
    Invoke-Logged { & $GitExe @GitSafeArgs -C $RepoDir diff --cached --quiet -- "data/distribution/$DateStamp.json" }
    $distributionDiffRc = $LASTEXITCODE
    if ($distributionDiffRc -eq 1) {
        Write-Log 'distribution manifest has changes, committing'
        Invoke-Logged { & $GitExe @GitSafeArgs -C $RepoDir commit -m "distribution: record publish state for $DateStamp" }
        if ($LASTEXITCODE -ne 0) { Write-Log "ERROR: distribution manifest commit failed (rc=$LASTEXITCODE)"; exit 1 }
    } elseif ($distributionDiffRc -eq 0) {
        Write-Log 'distribution manifest no changes'
    } else {
        Write-Log "ERROR: git diff distribution manifest returned unexpected rc=$distributionDiffRc"
        exit 1
    }
}

# ===== 5. digest + docs を 1 回の push で同時公開 (Plan v3 P0-A) =====
# 旧構造の「digest push → docs build → docs push」を統合。失敗時には digest commit が
# ローカルにのみ残るので、翌日 runner の git pull --ff-only が可能な状態を維持する。
# CLAUDE.md グローバル git safety protocol「Always create NEW commits rather than
# amending」に従い amend は使わず、digest commit と docs commit を別 commit として
# 同時 push する。
if ($NoPush) {
    Write-Log 'NoPush mode: skipping git push origin HEAD:main'
    Write-Log 'NoPush mode: skipping send_push'
} else {
    Write-Log 'push origin HEAD:main start (digest + docs を同時公開)'
    Invoke-Logged { & $GitExe @GitSafeArgs -C $RepoDir push origin HEAD:main }
    if ($LASTEXITCODE -ne 0) { Write-Log "ERROR: push failed (rc=$LASTEXITCODE)"; exit 1 }
    Write-Log 'push origin HEAD:main done (digest + docs pushed)'

    Write-Log 'publish verification start (remote HEAD + public publish-status sentinel + public audio sentinel)'
    Update-RunnerProgress -Phase 'publish-verify' -Step 'publish verification start'
    Push-Location $RepoDir
    try {
        Invoke-Logged { & $PyExe '-m' 'tools.daily_self_heal' 'verify-publish' '--repo-root' $RepoDir '--date' $DateStamp '--remote' 'origin' '--branch' 'main' '--public-base-url' $PublicBaseUrl '--wait-sec' $PublishVerifyWaitSec '--poll-sec' $PublishVerifyPollSec }
        $publishVerifyRc = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($publishVerifyRc -ne 0) {
        Write-Log "WARN: publish verification failed (rc=$publishVerifyRc). trying Deploy Pages fresh workflow dispatch if the same-head workflow completed with failure."
        Push-Location $RepoDir
        try {
            Invoke-Logged { & $PyExe '-m' 'tools.daily_self_heal' 'dispatch-deploy-workflow' '--repo-root' $RepoDir '--remote' 'origin' '--branch' 'main' }
            $deployDispatchRc = $LASTEXITCODE
        } finally {
            Pop-Location
        }
        if ($deployDispatchRc -eq 0) {
            Write-Log 'Deploy Pages fresh workflow dispatch issued; waiting for same-head workflow convergence'
            Push-Location $RepoDir
            try {
                Invoke-Logged { & $PyExe '-m' 'tools.daily_self_heal' 'wait-deploy-workflow' '--repo-root' $RepoDir '--remote' 'origin' '--branch' 'main' '--wait-sec' $PublishVerifyWaitSec '--poll-sec' $PublishVerifyPollSec }
                $deployWaitRc = $LASTEXITCODE
            } finally {
                Pop-Location
            }
            if ($deployWaitRc -ne 0) {
                Write-Log "WARN: Deploy Pages fresh workflow did not converge yet (rc=$deployWaitRc). retrying publish verification once with typed evidence."
            } else {
                Write-Log 'Deploy Pages fresh workflow convergence OK; retrying publish verification'
            }
            Push-Location $RepoDir
            try {
                Invoke-Logged { & $PyExe '-m' 'tools.daily_self_heal' 'verify-publish' '--repo-root' $RepoDir '--date' $DateStamp '--remote' 'origin' '--branch' 'main' '--public-base-url' $PublicBaseUrl '--wait-sec' $PublishVerifyWaitSec '--poll-sec' $PublishVerifyPollSec }
                $publishVerifyRc = $LASTEXITCODE
            } finally {
                Pop-Location
            }
        } else {
            Write-Log "Deploy Pages fresh workflow dispatch was not applicable or failed (rc=$deployDispatchRc)."
        }
    }
    if ($publishVerifyRc -ne 0) {
        Write-Log "ERROR: publish verification failed (rc=$publishVerifyRc). remote/pages/public/audio sentinel did not converge."
        Invoke-AutonomousCompletionPolicy -FailureKind 'publish' -GateId 'publish-verify' -Reason 'publish verification failed' -ExitCode $publishVerifyRc
    }
    Write-Log 'publish verification OK'
    $NormalPublishVerified = $true

    Write-Log 'youtube podcast finalize start'
    Push-Location $RepoDir
    try {
        Invoke-Logged { & $PyExe '-m' 'tools.youtube_podcast.upload_episode' $DateStamp '--finalize' }
        $youtubeFinalizeRc = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($youtubeFinalizeRc -ne 0) {
        Write-Log "ERROR: youtube podcast finalize failed (rc=$youtubeFinalizeRc). public podcast sentinel cannot converge."
        Invoke-AutonomousCompletionPolicy -FailureKind 'distribution' -GateId 'youtube-podcast-finalize' -Reason 'youtube podcast finalize failed' -ExitCode $youtubeFinalizeRc
    }
    Write-Log 'youtube podcast finalize OK'

    Write-Log 'deepdive youtube podcast finalize start'
    Push-Location $RepoDir
    try {
        Invoke-Logged { & $PyExe '-m' 'tools.youtube_podcast.upload_episode' $DateStamp '--kind' 'deepdive' '--finalize' }
        $deepDiveYoutubeFinalizeRc = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($deepDiveYoutubeFinalizeRc -ne 0) {
        Write-Log "ERROR: deepdive youtube podcast finalize failed (rc=$deepDiveYoutubeFinalizeRc). public DeepDive podcast sentinel cannot converge."
        Invoke-AutonomousCompletionPolicy -FailureKind 'distribution' -GateId 'deepdive-youtube-podcast-finalize' -Reason 'deepdive youtube podcast finalize failed' -ExitCode $deepDiveYoutubeFinalizeRc
    }
    Write-Log 'deepdive youtube podcast finalize OK'

    Write-Log 'podcast verification start (public podcast sentinel)'
    Update-RunnerProgress -Phase 'podcast-verify' -Step 'podcast verification start'
    Push-Location $RepoDir
    try {
        Invoke-Logged { & $PyExe '-m' 'tools.daily_self_heal' 'verify-podcast' '--date' $DateStamp '--state' (Join-Path $RepoDir 'build\youtube-podcast\uploads.json') '--wait-sec' '1200' '--poll-sec' '30' }
        $podcastVerifyRc = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($podcastVerifyRc -ne 0) {
        Write-Log "ERROR: podcast verification failed (rc=$podcastVerifyRc). public podcast sentinel did not converge."
        Invoke-AutonomousCompletionPolicy -FailureKind 'distribution' -GateId 'podcast-verify' -Reason 'podcast verification failed' -ExitCode $podcastVerifyRc
    }
    Write-Log 'podcast verification OK'

    Write-Log 'deepdive podcast verification start (public podcast sentinel)'
    Update-RunnerProgress -Phase 'deepdive-podcast-verify' -Step 'deepdive podcast verification start'
    Push-Location $RepoDir
    try {
        Invoke-Logged { & $PyExe '-m' 'tools.daily_self_heal' 'verify-podcast' '--date' $DateStamp '--state' (Join-Path $RepoDir 'build\youtube-podcast-deepdive\uploads.json') '--expected-title' "News-Grasp DeepDive Dialogue $DateStamp" '--wait-sec' '1200' '--poll-sec' '30' }
        $deepDivePodcastVerifyRc = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($deepDivePodcastVerifyRc -ne 0) {
        Write-Log "ERROR: deepdive podcast verification failed (rc=$deepDivePodcastVerifyRc). public DeepDive podcast sentinel did not converge."
        Invoke-AutonomousCompletionPolicy -FailureKind 'distribution' -GateId 'deepdive-podcast-verify' -Reason 'deepdive podcast verification failed' -ExitCode $deepDivePodcastVerifyRc
    }
    Write-Log 'deepdive podcast verification OK'

    Write-Log 'podcast playlist audit start'
    Update-RunnerProgress -Phase 'podcast-playlist-audit' -Step 'podcast playlist audit start'
    Push-Location $RepoDir
    try {
        Invoke-Logged { & $PyExe '-m' 'tools.youtube_podcast.upload_episode' $DateStamp '--audit-playlists' }
        $podcastPlaylistAuditRc = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($podcastPlaylistAuditRc -ne 0) {
        Write-Log "ERROR: podcast playlist audit failed (rc=$podcastPlaylistAuditRc). same-date duplicate or deleted playlist item remains."
        Invoke-AutonomousCompletionPolicy -FailureKind 'distribution' -GateId 'podcast-playlist-audit' -Reason 'podcast playlist audit failed' -ExitCode $podcastPlaylistAuditRc
    }
    Write-Log 'podcast playlist audit OK'

    # ===== 6. Web Push 通知（docs 公開後・publish-complete 前に state 化） =====
    # 通知自体は付随機能として非致命だが、System Integrity 上は notification の結果も
    # completion proof に含める。send_push は --record-state で machine-readable JSON を残す。
    $NotificationStatePath = Join-Path $RepoDir "build\notification\$DateStamp.json"
    if (Should-SendNormalBatchNotification) {
        Write-Log 'send_push start'
        Push-Location $RepoDir
        try {
            Invoke-Logged { & $PyExe 'tools\send_push.py' '--record-state' $NotificationStatePath }
            $pushRc = $LASTEXITCODE
        } finally {
            Pop-Location
        }
        if ($pushRc -ne 0) { Write-Log "WARN: send_push exited $pushRc (non-fatal)" }
        Write-Log "send_push done rc=$pushRc"
    } else {
        if ($RecoverOnly) {
            Write-Log 'RecoverOnly mode: skipping send_push (not a normal batch)'
        } elseif (-not $NormalPublishVerified) {
            Write-Log 'send_push skipped: publish verification not confirmed'
        } else {
            Write-Log 'send_push skipped: not a normal batch'
        }
        $notificationDir = Split-Path -Parent $NotificationStatePath
        New-Item -ItemType Directory -Path $notificationDir -Force | Out-Null
        [ordered]@{
            date = $DateStamp
            status = 'skipped_not_normal'
            ok = $false
            source = 'runner'
            subscription_count = 0
            sent_count = 0
            detail = 'NoPush/RecoverOnly/not-normal-batch'
            recorded_at = (Get-Date).ToString('o')
        } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $NotificationStatePath -Encoding UTF8
    }

    Write-Log 'publish-complete manifest verification start'
    $publishCompleteManifest = Join-Path $RepoDir "build\publish-complete\$DateStamp.json"
    Push-Location $RepoDir
    try {
        $dailySelfHealTool = if ($null -ne $RecoveryRuntimeBinding) {
            [string]$RecoveryRuntimeBinding.DailySelfHealPath
        } else {
            Join-Path $OpsRepoRoot 'tools\daily_self_heal.py'
        }
        Invoke-Logged { & $PyExe '-I' '-S' '-B' $dailySelfHealTool 'verify-publish-complete' '--repo-root' $RepoDir '--ops-repo-root' $OpsRepoRoot '--date' $DateStamp '--remote' 'origin' '--branch' 'main' '--public-base-url' $PublicBaseUrl '--wait-sec' '0' '--poll-sec' $PublishVerifyPollSec '--notification-state' $NotificationStatePath '--producer-state' $StateFile '--output' $publishCompleteManifest }
        $publishCompleteRc = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($publishCompleteRc -ne 0) {
        Write-Log "ERROR: publish-complete manifest verification failed (rc=$publishCompleteRc)."
        Invoke-AutonomousCompletionPolicy -FailureKind 'publish' -GateId 'publish-complete' -Reason 'publish-complete manifest verification failed' -ExitCode $publishCompleteRc
    }
    try {
        $publishComplete = Get-Content -LiteralPath $publishCompleteManifest -Raw -Encoding UTF8 | ConvertFrom-Json
        $script:PublishCompleteManifestPath = $publishCompleteManifest
        $script:PublishCompleteCommit = [string]$publishComplete.publish_commit
    } catch {
        Write-Log "ERROR: publish-complete manifest parse failed: $($_.Exception.Message)"
        Invoke-AutonomousCompletionPolicy -FailureKind 'publish' -GateId 'publish-complete' -Reason 'publish-complete manifest parse failed' -ExitCode 1
    }
    if (-not $script:PublishCompleteCommit) {
        Write-Log 'ERROR: publish-complete manifest missing publish_commit'
        Invoke-AutonomousCompletionPolicy -FailureKind 'publish' -GateId 'publish-complete' -Reason 'publish-complete manifest missing publish_commit' -ExitCode 1
    }
    Write-Log 'publish-complete manifest verification OK'
}

Write-CodexUsageWindowSnapshot -Phase 'end'
if ($NoPublish) {
    Write-Log 'news-grasp-runner.ps1 PUBLISH DRY RUN OK'
} elseif ($NoPush) {
    Write-Log 'news-grasp-runner.ps1 SMOKE OK'
} else {
    $normalFinalizationReceipt = ''
    if ($RunIntent -eq 'ScheduledRecoveryFull') {
        $normalFinalizationReceipt = New-NewsGraspFinalizationReceipt -ManifestPath $script:PublishCompleteManifestPath
        if (-not $normalFinalizationReceipt) { exit 2 }
        $consumedNormalFinalization = Invoke-RecoveryReceiptValidation `
            -Command 'consume-finalization' `
            -ReceiptPath $normalFinalizationReceipt
        if ($null -eq $consumedNormalFinalization) { exit 2 }
        $normalCandidateStatePath = New-NewsGraspFinalizationCandidateState `
            -FinalizationReceiptPath $normalFinalizationReceipt `
            -ManifestPath $script:PublishCompleteManifestPath `
            -PublishCommit $script:PublishCompleteCommit
        if (-not $normalCandidateStatePath) { exit 76 }
        if (-not (Invoke-NewsGraspCompletionGuard `
                -FinalizationReceiptPath $normalFinalizationReceipt `
                -CandidateStatePath $normalCandidateStatePath)) {
            exit 2
        }
        if (-not (Commit-NewsGraspFinalizationCandidate -CandidateStatePath $normalCandidateStatePath)) {
            exit 76
        }
    }
    Write-Log 'news-grasp-runner.ps1 OK'
    if ($RunIntent -eq 'ScheduledRecoveryFull') {
        $normalStateAppliedJournal = Invoke-RecoveryReceiptValidation `
            -Command 'mark-finalization-state-applied' `
            -ReceiptPath $normalFinalizationReceipt
        if ($null -eq $normalStateAppliedJournal) { exit 2 }
    }
    if ($RunIntent -eq 'ScheduledRecoveryFull') {
        $normalExecutionAppliedJournal = Invoke-RecoveryReceiptValidation `
            -Command 'mark-execution-applied' `
            -ReceiptPath $RecoveryExecutionReceiptPath
        if ($null -eq $normalExecutionAppliedJournal) { exit 2 }
    }
}
exit 0
