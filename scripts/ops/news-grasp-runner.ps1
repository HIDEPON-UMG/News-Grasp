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
    [switch] $PreflightOnly,
    [switch] $RecoverOnly,
    [switch] $NoPush,
    [switch] $NoPublish,
    [switch] $UseCodex,
    [int] $IdleTimeoutSec = 900,
    [switch] $Stage2EditorSmokeOnly,
    [switch] $StopAfterEditorStart,
    [switch] $StopBeforeDeepDive,
    [ValidateSet('', 'deepdive', 'post-daily-quality', 'post-deepdive')]
    [string] $ResumeFromStage = '',
    [string] $RepoDirOverride = '',
    [string] $CodexWrapperOverride = '',
    [string] $CodexExeOverride = '',
    [string] $PyExeOverride = '',
    [string] $DateStampOverride = '',
    [string] $LogDirOverride = '',
    [string] $StateFileOverride = '',
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
$UseCodex = $true
if ($StopBeforeDeepDive) { $NoPublish = $true }
if ($NoPublish) { $NoPush = $true }
$ResumeFromPostDailyQuality = $ResumeFromStage -in @('deepdive', 'post-daily-quality')
$ResumeAfterDeepDive = $ResumeFromStage -in @('post-deepdive')
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

$RepoDir   = Resolve-NewsGraspRepoDir -Override $RepoDirOverride
$LogDir    = Join-Path $env:USERPROFILE 'bin\news-grasp-logs'
$GitExe    = 'C:\Program Files\Git\cmd\git.exe'
$CodexExe  = Resolve-CodexCliExe -Override $CodexExeOverride
$PyExe     = Join-Path $RepoDir '.venv\Scripts\python.exe'
$CodexWrapper = Join-Path $env:USERPROFILE 'bin\run_codex_with_timeout.ps1'
$TimeoutSec = 4800  # 2026-06-12: 3600→4800。日次 digest の wall-clock timeout を 80 分へ延長。真の暴走は IdleTimeoutSec 900 が先に検知する
$PromptFile = Join-Path $RepoDir 'prompts\runner-prompt.md'
$CodexOutputSchema = Join-Path $RepoDir 'schemas\model_eval_output.schema.json'
$CodexLastMessage = Join-Path $RepoDir 'build\codex-last-message.txt'
$RepoManagedRunner = Join-Path $RepoDir 'scripts\ops\news-grasp-runner.ps1'
$RepoManagedWatcher = Join-Path $RepoDir 'scripts\ops\watch-news-grasp-runner.ps1'
$PublicBaseUrl = 'https://hidepon-umg.github.io/News-Grasp/'
$InvokedLog = Join-Path $env:USERPROFILE 'bin\news-grasp-invoked.log'
$StateFile  = Join-Path $env:USERPROFILE 'bin\news-grasp-runner-state.json'
$MaxParallelReporterJobs = 7

if ($CodexWrapperOverride) { $CodexWrapper = $CodexWrapperOverride }
if ($PyExeOverride) { $PyExe = $PyExeOverride }
if ($LogDirOverride) { $LogDir = $LogDirOverride }
if ($StateFileOverride) { $StateFile = $StateFileOverride }
if (-not $PyExeOverride) { $PyExe = Join-Path $RepoDir '.venv\Scripts\python.exe' }
$PromptFile = Join-Path $RepoDir 'prompts\runner-prompt.md'
$CodexOutputSchema = Join-Path $RepoDir 'schemas\model_eval_output.schema.json'
$CodexLastMessage = Join-Path $RepoDir 'build\codex-last-message.txt'
$RepoManagedRunner = Join-Path $RepoDir 'scripts\ops\news-grasp-runner.ps1'
$RepoManagedWatcher = Join-Path $RepoDir 'scripts\ops\watch-news-grasp-runner.ps1'

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

# YYYY-MM-DD ログファイル
$DateStamp = if ($DateStampOverride) { $DateStampOverride } else { Get-Date -Format 'yyyy-MM-dd' }
$LogPath = Join-Path $LogDir ("$DateStamp.log")
$CodexUsageLog = Join-Path $RepoDir "build\codex-usage\$DateStamp.jsonl"
$CodexUsageWindowLog = Join-Path $RepoDir "build\codex-usage\$DateStamp.windows.jsonl"
$script:CodexUsageEndSnapshotWritten = $false
$RunId = [guid]::NewGuid().ToString('N')
$script:RunnerCommandLine = ''
$script:RunnerCommandLineFingerprint = ''
$script:RunnerProcessCreationTime = ''
$script:PublishCompleteManifestPath = ''
$script:PublishCompleteCommit = ''

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
        $json = & $PyExe '-m' 'tools.publish_inventory' '--date' $DateStamp '--kind' $Kind '--json'
        if ($LASTEXITCODE -ne 0) {
            throw "tools.publish_inventory failed (kind=$Kind, rc=$LASTEXITCODE)"
        }
        return @(Convert-PublishInventoryJson -Json $json)
    } finally {
        Pop-Location
    }
}

$DailyDigestArtifacts = Get-PublishInventoryArtifacts -Kind 'digest'
$PublishedDocsArtifacts = Get-PublishInventoryArtifacts -Kind 'published'
$PublishedRepairArtifacts = Get-PublishInventoryArtifacts -Kind 'published-repair'
$script:RequiredCategoriesForSlo = @(Get-PublishInventoryArtifacts -Kind 'categories')

function Get-RunnerStateMutexName {
    param([string] $Path)
    $hash = (Get-StringSha256Hex -Text ([System.IO.Path]::GetFullPath($Path).ToLowerInvariant())).Substring(0, 24)
    return "Local\NewsGraspRunnerState-$hash"
}

function Test-TerminalRunnerStatus {
    param([string] $Status)
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
    if (Test-Path -LiteralPath $Path) {
        [System.IO.File]::Replace($tmp, $Path, $backup, $true)
    } else {
        [System.IO.File]::Move($tmp, $Path)
    }
}

function Read-RunnerStateOrNull {
    param([string] $Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    try {
        return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        $stamp = Get-Date -Format 'yyyyMMddHHmmss'
        $corrupt = "$Path.corrupt.$stamp.json"
        try { Copy-Item -LiteralPath $Path -Destination $corrupt -Force -ErrorAction SilentlyContinue } catch { }
        return [pscustomobject]@{ __corrupt = $true; corrupt_backup = $corrupt }
    }
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
        [string] $ExternalKind = '',
        [string] $ExternalSystem = '',
        [string] $ExternalStatus = '',
        [string] $ExternalStderr = '',
        [string] $ExternalDetail = ''
    )
    $now = Get-Date -Format 'yyyy-MM-ddTHH:mm:ss.fffK'
    try {
        Invoke-WithRunnerStateLock {
            $prev = Read-RunnerStateOrNull -Path $StateFile
            if ($prev -and $prev.__corrupt) {
                $payload = [ordered]@{
                    status = 'blocked_runner_state_corrupt'
                    message = "runner state corrupt: $($prev.corrupt_backup)"
                    exit_code = 125
                    updated_at = $now
                    heartbeat_at = $now
                    date = $DateStamp
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
            if ($prev -and $prev.run_id -eq $RunId -and (Test-TerminalRunnerStatus -Status ([string]$prev.status))) {
                # typed terminal state must replace generic error: Write-Log("ERROR:*") can run before
                # a typed status such as publish_failed / distribution_failed / blocked_external_readiness.
                $typedTerminalOverridesGenericError = (
                    [string]$prev.status -eq 'error' -and
                    @('blocked_external_readiness', 'publish_failed', 'distribution_failed', 'publish_complete') -contains [string]$Status
                )
                if ($typedTerminalOverridesGenericError) {
                    Write-Log "typed terminal state replaces generic error: $Status"
                } else {
                # first-terminal-wins: 同一 run_id の terminal state は running にも別 terminal にも戻さない。
                return
                }
            }
            if ($ResetStartedAt -and $prev -and $prev.run_id -and $prev.run_id -ne $RunId) {
                $previous = "$StateFile.previous.$(Get-Date -Format 'yyyyMMddHHmmss').json"
                try { Copy-Item -LiteralPath $StateFile -Destination $previous -Force -ErrorAction SilentlyContinue } catch { }
            }

            $startedAt = $now
            if (-not $ResetStartedAt -and $prev -and $prev.started_at) {
                $startedAt = [string]$prev.started_at
            }
            $state = [ordered]@{
                status = $Status
                message = $Message
                exit_code = $ExitCode
                updated_at = $now
                heartbeat_at = $(if ($HeartbeatAt) { $HeartbeatAt } else { $now })
                date = $DateStamp
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
            if ($Phase) { $state.phase = $Phase }
            if ($Step) { $state.step = $Step }
            if ($GateId) { $state.gate_id = $GateId }
            if ($Category) { $state.category = $Category }
            if ($Attempt -gt 0) { $state.attempt = $Attempt }
            if ($null -ne $ActiveJobs) { $state.active_jobs = $ActiveJobs }
            if ($DeadlineAt) { $state.deadline_at = $DeadlineAt }
            if ($PublishManifestPath) { $state.publish_manifest_path = $PublishManifestPath }
            if ($PublishCommit) { $state.publish_commit = $PublishCommit }
            if ($ExternalKind -or $ExternalSystem -or $ExternalStatus -or $ExternalStderr -or $ExternalDetail) {
                $state.external_readiness = [ordered]@{
                    kind = $ExternalKind
                    system = $ExternalSystem
                    status = $ExternalStatus
                    stderr = $ExternalStderr
                    detail = $ExternalDetail
                }
            }
            Write-RunnerStateAtomic -Path $StateFile -Payload $state
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
        [bool] $ArtifactProgress = $false
    )
    Set-RunnerState -Status 'running' -Message $Step -ExitCode -1 -Phase $Phase -Step $Step -GateId $GateId -Category $Category -Attempt $Attempt -ActiveJobs $ActiveJobs -DeadlineAt $DeadlineAt
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
    exit $ExitCode
}

function Write-Log {
    param([string] $Text)
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'
    $line = "[$ts] $Text"
    # console 出力 (Task Scheduler の標準出力は捨てられるので保険)
    Write-Host $line
    Add-Content -Path $LogPath -Value $line -Encoding UTF8
    if ($Text -like 'ERROR:*') {
        if (-not $script:CodexUsageEndSnapshotWritten -and (Get-Command Write-CodexUsageWindowSnapshot -ErrorAction SilentlyContinue)) {
            $script:CodexUsageEndSnapshotWritten = $true
            Write-CodexUsageWindowSnapshot -Phase 'error'
        }
        Set-RunnerState -Status 'error' -Message $Text -ExitCode 1
    } elseif ($Text -eq 'news-grasp-runner.ps1 OK') {
        Set-RunnerState -Status 'publish_complete' -Message $Text -ExitCode 0 -PublishManifestPath $script:PublishCompleteManifestPath -PublishCommit $script:PublishCompleteCommit
    } elseif ($Text -eq 'news-grasp-runner.ps1 SMOKE OK') {
        Set-RunnerState -Status 'smoke_ok' -Message $Text -ExitCode 0
    } elseif ($Text -eq 'news-grasp-runner.ps1 PRE DEEPDIVE E2E OK') {
        Set-RunnerState -Status 'pre_deepdive_e2e_ok' -Message $Text -ExitCode 0 -Phase 'pre-deepdive' -Step 'summary-reflection-and-daily-quality'
    } elseif ($Text -eq 'news-grasp-runner.ps1 PUBLISH DRY RUN OK') {
        Set-RunnerState -Status 'publish_dry_run_ok' -Message $Text -ExitCode 0
    }
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
        Invoke-Logged { & $GitExe -C $RepoDir ls-remote --exit-code origin main }
        if ($LASTEXITCODE -ne 0) {
            Write-Log "publish external readiness failed: git ls-remote origin main rc=$LASTEXITCODE"
            return New-ExternalReadinessResult -Ok $false -Kind 'github_remote' -System 'github' -Status "rc=$LASTEXITCODE" -Detail 'git ls-remote origin main'
        }
        if (-not $NoPush) {
            Invoke-Logged { & $GitExe -C $RepoDir push --dry-run origin HEAD:main }
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
    return ((-not $NoPush) -and (-not $RecoverOnly) -and $NormalPublishVerified)
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
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
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
    if ($NoPush) { $runnerArgs += '-NoPush' }
    if ($NoPublish) { $runnerArgs += '-NoPublish' }
    if ($UseCodex) { $runnerArgs += '-UseCodex' }
    if ($IdleTimeoutSec -ne 900) { $runnerArgs += @('-IdleTimeoutSec', [string]$IdleTimeoutSec) }
    if ($Stage2EditorSmokeOnly) { $runnerArgs += '-Stage2EditorSmokeOnly' }
    if ($StopAfterEditorStart) { $runnerArgs += '-StopAfterEditorStart' }
    if ($StopBeforeDeepDive) { $runnerArgs += '-StopBeforeDeepDive' }
    if ($ResumeFromStage) { $runnerArgs += @('-ResumeFromStage', $ResumeFromStage) }
    if ($RepoDirOverride) { $runnerArgs += @('-RepoDirOverride', $RepoDirOverride) }
    if ($CodexWrapperOverride) { $runnerArgs += @('-CodexWrapperOverride', $CodexWrapperOverride) }
    if ($CodexExeOverride) { $runnerArgs += @('-CodexExeOverride', $CodexExeOverride) }
    if ($PyExeOverride) { $runnerArgs += @('-PyExeOverride', $PyExeOverride) }
    if ($DateStampOverride) { $runnerArgs += @('-DateStampOverride', $DateStampOverride) }
    if ($LogDirOverride) { $runnerArgs += @('-LogDirOverride', $LogDirOverride) }
    if ($StateFileOverride) { $runnerArgs += @('-StateFileOverride', $StateFileOverride) }
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
    Write-Log "runner launch snapshot repo_dir=$RepoDir repo_head=$(& $GitExe -C $RepoDir rev-parse --short HEAD 2>$null) live_runner_sha=$liveRunnerSha repo_runner_sha=$repoRunnerSha repo_watcher_sha=$repoWatcherSha task_action=$taskAction"
    if ($liveRunnerSha -ne $repoRunnerSha) {
        Invoke-RunnerBinarySyncApprovalBlock -LiveRunnerSha $liveRunnerSha -RepoRunnerSha $repoRunnerSha
    }
}

function Invoke-Logged {
    # 外部コマンドを呼び stdout/stderr を pipe 経由で ToString() → UTF-8 で log に append。
    # PS 5.1 の `*>> $LogPath` 直接 redirect は native command の stderr で NativeCommandError
    # 例外と UTF-16 で append される副作用があるため、明示的に pipe で書き出す。
    # 引数の Block で外部コマンドを呼び出すだけにする (sub-process 化はしない)。
    param([scriptblock] $Block)
    & $Block 2>&1 | ForEach-Object {
        Add-Content -Path $LogPath -Value $_.ToString() -Encoding UTF8
    }
}

function Invoke-LoggedCapture {
    param(
        [scriptblock] $Block,
        [string] $CapturePath
    )
    if (Test-Path $CapturePath) { Remove-Item -LiteralPath $CapturePath -Force -ErrorAction SilentlyContinue }
    & $Block 2>&1 | ForEach-Object {
        $line = $_.ToString()
        Add-Content -Path $LogPath -Value $line -Encoding UTF8
        Add-Content -Path $CapturePath -Value $line -Encoding UTF8
    }
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
            if ($line) { Add-Content -Path $LogPath -Value $line -Encoding UTF8 }
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
    try {
        Invoke-Logged { & $CodexExe 'doctor' }
        $doctorRc = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($doctorRc -ne 0) {
        Write-Log "codex auth readiness failed: codex doctor rc=$doctorRc"
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

function Invoke-CodexWrapper {
    param(
        [string] $PromptFile,
        [int] $TimeoutSec,
        [int] $IdleTimeoutSec,
        [string] $Model = '',
        [string] $OutputSchema = $CodexOutputSchema,
        [string] $OutputLastMessage = $CodexLastMessage,
        [string] $FlowName = 'unknown',
        [string] $SuccessProbeCommand = '',
        [int] $SuccessProbeIntervalSec = 30,
        [int] $SuccessProbeMinElapsedSec = 0
    )
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
    }
    if ($SuccessProbeCommand) {
        $codexArgs['SuccessProbeCommand'] = $SuccessProbeCommand
        $codexArgs['SuccessProbeIntervalSec'] = $SuccessProbeIntervalSec
        $codexArgs['SuccessProbeMinElapsedSec'] = $SuccessProbeMinElapsedSec
    }
    if ($Model) { $codexArgs['Model'] = $Model }
    & $CodexWrapper @codexArgs
    $wrapperOk = $?
    $wrapperRc = $LASTEXITCODE
    if (-not $wrapperOk) {
        if ($null -eq $wrapperRc -or $wrapperRc -eq 0) { return 125 }
    }
    return $wrapperRc
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
        Write-Log "repair worker denied by retry budget (gate=$GateId, rc=$decisionRc)"
        return 1
    }

    $decision = Read-RepairDecision -GateId $GateId -CapturePath $CapturePath -ClassifyPath $ClassifyPath
    if ($null -eq $decision) {
        Write-Log "repair worker denied: repair decision unavailable (gate=$GateId)"
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
    if ($registryRepairRc -notin @(2, 3)) {
        Write-Log "deterministic registry repair failed (gate=$GateId, rc=$registryRepairRc)"
        return $registryRepairRc
    }

    if ([string]$decision.repair_class -ne 'llm_generate_missing_artifact') {
        Write-Log "repair matrix denied LLM repair worker (gate=$GateId, repair_class=$($decision.repair_class), status=$($decision.failure_status))"
        return 1
    }

    if (-not (Test-RepairWorkerPreflight -GateId $GateId -Artifacts $Artifacts -RepairTransactionId $RepairTransactionId -RepairDecision $decision)) {
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
    $artifactText = [string]::Join(', ', $Artifacts)
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
- python / py / uv / .venv\Scripts\python.exe の直書きは禁止。WindowsApps python や uv cache に流れる経路を作らない。
- git add / git commit / git push / git checkout / git reset は絶対に実行しない。
- rg / Get-ChildItem -Recurse / 広域 Select-String は禁止。読む場合は失敗ログと artifacts に列挙された最小ファイルだけに限定する。
"@
    [System.IO.File]::WriteAllText($repairPrompt, $prompt, [System.Text.UTF8Encoding]::new($false))
    $issueCount = 1
    if ($decision.issue_ledger) {
        $issueCount = @($decision.issue_ledger).Count
    } elseif ($decision.issues) {
        $issueCount = @($decision.issues).Count
    }
    $scopeAmbiguous = @('repair_context_scope_mismatch', 'repair_context_overbroad') -contains [string]$decision.failure_status
    $missingArtifactGeneration = ([string]$decision.repair_class -eq 'llm_generate_missing_artifact')
    $compoundGateFailure = ($GateId -in @('daily-quality', 'generation-quality') -and $issueCount -gt 1)
    $repairModel = Select-RepairModel -IssueCount $issueCount -PreviousClassifyFailed:$false -ScopeAmbiguous:$scopeAmbiguous -MissingArtifactGeneration:$missingArtifactGeneration -CompoundGateFailure:$compoundGateFailure
    Write-Log "repair wrapper invoke START (agent=codex, gate=$GateId, Model=$repairModel, issue_count=$issueCount, missing_artifact_generation=$missingArtifactGeneration, TimeoutSec=900)"
    Update-RunnerProgress -Phase 'repair' -Step "repair wrapper invoke: $GateId" -GateId $GateId -Category $Category
    $repairRc = Invoke-CodexWrapper -PromptFile $repairPrompt -TimeoutSec 900 -IdleTimeoutSec 300 -Model $repairModel -FlowName "repair:$GateId"
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
        foreach ($propName in @('artifact_paths', 'selected_artifacts')) {
            if ($RepairDecision.PSObject.Properties.Name -contains $propName) {
                foreach ($artifact in @($RepairDecision.$propName)) {
                    $text = ([string] $artifact).Trim()
                    if ($text) { $selected.Add($text) }
                }
            }
        }
    }
    if ($selected.Count -gt 0) {
        return $selected.ToArray()
    }
    return @()
}

function Invoke-DeterministicRegistryRepair {
    # registry handler traceability: summary-emphasis-patch / category-card-emphasis-patch / audio-script-length-patch
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
        'blocked_repair_handler_unimplemented'
    )
    $repairArtifacts = @(Get-RepairDecisionArtifacts -RepairDecision $decision -FallbackArtifacts $Artifacts)

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
    if ($registryRc -eq 0) {
        Write-Log "deterministic registry repair OK (gate=$GateId, handler=$($decision.handler_id))"
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
        return (Get-FileHash -LiteralPath $FullPath -Algorithm SHA256).Hash
    }
    $parts = New-Object System.Collections.Generic.List[string]
    $files = @(Get-ChildItem -LiteralPath $FullPath -Recurse -File | Sort-Object FullName)
    foreach ($file in $files) {
        $rel = $file.FullName.Substring($FullPath.Length).TrimStart('\', '/')
        $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
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
    $allowed = ($repairClass -eq 'llm_generate_missing_artifact' -and $allMissing)
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
        policy = 'llm_worker_only_when_matrix_allows_missing_artifact_and_all_artifacts_missing'
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
    Write-Log "pre-repair policy OK: coverage matrix allows missing artifact generation and all target artifacts are missing"
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
    $lines = @(& $GitExe -C $RepoDir status --porcelain --untracked-files=all)
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
    $maxGateAttempts = 5
    for ($attempt = 1; $attempt -le $maxGateAttempts; $attempt++) {
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
        if ($attempt -ge $maxGateAttempts) {
            Write-Log "$GateId gate final attempt failed; skipping repair"
            return $gateRc
        }
        $classifyPath = Join-Path ([System.IO.Path]::GetTempPath()) ("news-grasp-repair-classify-$GateId-$DateStamp-attempt$attempt.json")
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
    Write-Log "$GateId autonomous gate start (budget=max_gate_attempts=5, signature_repair=1, state=$statePath)"
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
        $tracked = & $GitExe -C $RepoDir ls-files -- $rel
        if ($tracked) {
            Invoke-Logged { & $GitExe -C $RepoDir checkout -- $rel }
            if ($LASTEXITCODE -ne 0) {
                Write-Log "WARN: failed to restore tracked generated artifact: $rel (rc=$LASTEXITCODE)"
            }
        }
    }
}

function Resolve-LastGoodDocsRef {
    $shortDate = $DateStamp.Substring(5)
    $compactDate = $DateStamp.Replace('-', '')
    $history = & $GitExe -C $RepoDir log "--format=%H`t%s" -- 'docs/index.html'
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
        $head = (& $GitExe -C $RepoDir rev-parse HEAD 2>$null)
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
        $head = (& $GitExe -C $RepoDir rev-parse HEAD 2>$null)
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

# ===== sentinel: 起動できた事実 =====
$pidStamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'
Add-Content -Path $InvokedLog -Value "[$pidStamp] runner-invoked pid=$PID ps1 smoke=$SmokeTest recover=$RecoverOnly no_publish=$NoPublish resume_from_stage=$ResumeFromStage" -Encoding UTF8

Add-Content -Path $LogPath -Value '' -Encoding UTF8
Add-Content -Path $LogPath -Value '==========================================' -Encoding UTF8
Set-RunnerState -Status 'running' -Message 'runner started' -ExitCode -1 -ResetStartedAt
Write-Log "news-grasp-runner.ps1 start (smoke=$SmokeTest, recover=$RecoverOnly, no_publish=$NoPublish, resume_from_stage=$ResumeFromStage, pid=$PID)"
Assert-RunnerBinaryInSync
$IsE2EOrDryRun = $NoPublish -or $NoPush -or $StopBeforeDeepDive
if ($IsE2EOrDryRun -and (-not $SmokeTest) -and (-not $PreflightOnly) -and (-not $RecoverOnly) -and (-not $Stage2EditorSmokeOnly) -and (-not $ResumeFromPostDailyQuality) -and (-not $ResumeAfterDeepDive) -and (Test-DailyArtifactsExist -TargetDate $DateStamp)) {
    Write-Log "ERROR: E2E full rerun forbidden after existing artifacts date=$DateStamp. Use -ResumeFromStage deepdive, post-daily-quality, or post-deepdive."
    Set-RunnerState -Status 'blocked_e2e_full_rerun_forbidden' -Message 'E2E full rerun forbidden after existing artifacts' -ExitCode 65
    exit 65
}
if ((-not $ForceFullRerun) -and (-not $SmokeTest) -and (-not $PreflightOnly) -and (-not $RecoverOnly) -and (-not $Stage2EditorSmokeOnly) -and (-not $ResumeFromPostDailyQuality) -and (-not $ResumeAfterDeepDive) -and (Test-DailyArtifactsExist -TargetDate $DateStamp)) {
    Write-Log "ERROR: existing daily artifacts detected; refusing full rerun for date=$DateStamp. Use -ForceFullRerun only after explicit user approval; otherwise resume from existing artifacts."
    Set-RunnerState -Status 'failed' -Message 'existing daily artifacts detected; refusing full rerun' -ExitCode 64
    exit 64
}
Write-CodexUsageWindowSnapshot -Phase 'start'
Add-Content -Path $LogPath -Value '==========================================' -Encoding UTF8

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
if ($Stage2EditorSmokeOnly) {
    Write-Log 'Stage2EditorSmokeOnly mode: skipping net reachability wait and git sync'
} elseif ($ResumeFromPostDailyQuality -or $ResumeAfterDeepDive) {
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
    Invoke-Logged { & $GitExe -C $RepoDir fetch --quiet origin main }
    if ($LASTEXITCODE -ne 0) { Write-Log "ERROR: git fetch failed (rc=$LASTEXITCODE)"; exit 1 }

    Write-Log 'git pull --ff-only start'
    Invoke-Logged { & $GitExe -C $RepoDir pull --ff-only origin main }
    if ($LASTEXITCODE -ne 0) { Stop-ExternalReadiness -Reason "git pull failed (rc=$LASTEXITCODE)" -ExitCode 71 -Kind 'github_remote' -System 'github' -ExternalStatus "rc=$LASTEXITCODE" -ExternalDetail 'git pull --ff-only origin main' }
}

if ($SmokeTest) {
    Write-Log 'SmokeTest mode: skipping codex / push / generate_pages'
    Write-Log 'news-grasp-runner.ps1 SMOKE OK'
    exit 0
}

if ($RecoverOnly) {
    $recoverOnlyInputManifest = Write-RecoverOnlyInputManifest
    Write-Log "RecoverOnly input manifest: $recoverOnlyInputManifest"
    Write-Log 'RecoverOnly mode: skipping digest codex; using current local digest/data commits and files'
} elseif ($ResumeFromPostDailyQuality -or $ResumeAfterDeepDive) {
    if ($ResumeAfterDeepDive) {
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
    if ($Stage2EditorSmokeOnly) {
        Write-Log 'Stage2EditorSmokeOnly mode: skipping Stage0 harvest and Stage1 dedup; using existing deduped candidates'
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
    $ReporterArtifacts = @()
    $ReporterMaxAttempts = 3
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

        $ReporterPollSeconds = 30
        $ReporterHeartbeatSeconds = 60
        $ReporterJobTimeoutSec = $TimeoutSec + 120
        $wrapper_log_offsets = @{}
        $jobs = @()
        foreach ($waveCat in $WaveCategories) {
            if ($Attempt -gt 1) {
                Clear-ReporterCategoryArtifacts -Category $waveCat
            }
            $promptFile = Join-Path $ReporterPromptDir "$waveCat.md"
            $lastMessage = Join-Path $ReporterArtifactDir "$waveCat.codex-last-message.json"
            $wrapperLog = Join-Path $ReporterArtifactDir "$waveCat.wrapper-attempt$Attempt.log"
            $usageLog = Join-Path $RepoDir "build\codex-usage\$DateStamp.reporter-$waveCat-attempt$Attempt.jsonl"
            New-ReporterPrompt -Category $waveCat -PromptFile $promptFile

            while (@($jobs | Where-Object { $_.State -eq 'Running' }).Count -ge $MaxParallelReporterJobs) {
                Start-Sleep -Seconds 1
            }

            Write-Log "reporter job START (agent=codex, role=reporter, category=$waveCat, attempt=$Attempt/$ReporterMaxAttempts, Wrapper=$CodexWrapper, Model=$ReporterModel, TimeoutSec=$TimeoutSec, IdleTimeoutSec=$IdleTimeoutSec)"
            $job = Start-Job -ArgumentList @(
                $waveCat,
                $Attempt,
                $CodexWrapper,
                $CodexExe,
                $promptFile,
                $wrapperLog,
                $TimeoutSec,
                $IdleTimeoutSec,
                $RepoDir,
                $ReporterFanoutSchema,
                $lastMessage,
                $ReporterModel,
                $usageLog
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
                    [string]$UsageLog
                )

                $started = Get-Date
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
                    -FlowName "reporter:$Category" `
                    -UsageLog $UsageLog
                $wrapperOk = $?
                $rc = $LASTEXITCODE
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
                    wrapper_pid = -1
                }
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
                Add-Content -Path $LogPath -Value $text.Substring($offset) -Encoding UTF8
                $wrapper_log_offsets[$key] = $text.Length
            }
        }

        $results = @()
        $pending = @($jobs)
        $lastHeartbeat = (Get-Date).AddSeconds(-1 * $ReporterHeartbeatSeconds)
        while ($pending.Count -gt 0) {
            $now = Get-Date
            $activeJobs = @(
                $pending | Where-Object { $_.State -eq 'Running' } | ForEach-Object {
                    [ordered]@{
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
                Write-Log "reporter supervisor heartbeat attempt=$Attempt active_jobs=$($activeJobs.Count)"
                Update-RunnerProgress -Phase 'reporter' -Step "reporter wave attempt=$Attempt active_jobs=$($activeJobs.Count)" -Attempt $Attempt -ActiveJobs $activeJobs
                $lastHeartbeat = $now
            }

            foreach ($job in @($pending)) {
                $elapsed = [int]((Get-Date) - $job.StartedAt).TotalSeconds
                if ($job.State -eq 'Running' -and $elapsed -gt $ReporterJobTimeoutSec) {
                    Write-Log "ERROR: reporter job timeout category=$($job.Category) attempt=$Attempt elapsed_sec=$elapsed limit_sec=$ReporterJobTimeoutSec"
                    Append-ReporterWrapperLog -Path $job.WrapperLog
                    Stop-Job -Job $job -Force -ErrorAction SilentlyContinue
                    $partial = @($job | Receive-Job -ErrorAction SilentlyContinue)
                    Remove-Job -Job $job -Force -ErrorAction SilentlyContinue
                    foreach ($item in $partial) {
                        if ($item.wrapper_pid -and [int]$item.wrapper_pid -gt 0) {
                            try { Stop-Process -Id ([int]$item.wrapper_pid) -Force -ErrorAction SilentlyContinue } catch { }
                        }
                    }
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

                if ($job.State -ne 'Running') {
                    Append-ReporterWrapperLog -Path $job.WrapperLog
                    $received = @($job | Receive-Job -ErrorAction SilentlyContinue)
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
    }

    $retryCategories = @($Categories)
    $terminalFailures = @{}
    $ReporterTerminalExitCode = 1
    for ($attempt = 1; $attempt -le $ReporterMaxAttempts -and $retryCategories.Count -gt 0; $attempt++) {
        $waveResults = Invoke-ReporterWave -Attempt $attempt -WaveCategories $retryCategories
        $nextRetryCategories = @()
        $failedCategories = @()

        foreach ($waveResult in $waveResults) {
            $catName = [string]$waveResult.category
            $failureReason = $null
            if (Test-ReporterCodexQuotaFailure -WaveResult $waveResult) {
                Stop-ExternalReadiness -Reason "codex CLI rate limit / out of credits during reporter category=$catName attempt=$attempt" -ExitCode 123 -Kind 'codex_quota' -System 'openai_codex' -ExternalStatus "rc=$($waveResult.rc)" -ExternalDetail "reporter:$catName attempt=$attempt wrapper_log=$($waveResult.wrapper_log)"
            }
            if ([int]$waveResult.rc -ne 0) {
                $failureReason = "wrapper_rc=$($waveResult.rc)"
                if ([int]$waveResult.rc -eq 124) {
                    $ReporterTerminalExitCode = 124
                }
            } else {
                $verifyReporterArgs = @('-m', 'tools.verify_reporter_output', '--date', $DateStamp, '--category', $catName)
                $verifyCapture = Join-Path $ReporterArtifactDir "$catName.verify-attempt$attempt.log"
                Push-Location $RepoDir
                try {
                    Invoke-LoggedCapture -CapturePath $verifyCapture -Block { & $PyExe @verifyReporterArgs }
                    $verifyReporterRc = $LASTEXITCODE
                } finally {
                    Pop-Location
                }
                if ($verifyReporterRc -ne 0) {
                    $verifyText = if (Test-Path $verifyCapture) { (Get-Content -LiteralPath $verifyCapture -Raw -Encoding UTF8).Trim() } else { '' }
                    $failureReason = "verify_rc=$verifyReporterRc $verifyText"
                }
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
    $ScheduledCategoryList = ($Categories -join ', ')
    $DateHeader = "今日の日付は $DateStamp (JST) である。Stage2 reporter artifact manifest は $EditorInputManifest にある。manifest の scheduled_categories は [$ScheduledCategoryList] で、Summary frontmatter categories/tags/sections は scheduled_categories のみ。非対象カテゴリの section を作らない。Stage1 dedup は build/deduped-candidates にある。音声原稿を作る場合は manifest の audio_script_history にある過去 2 日の path を確認し、構成・感想・締めの反復禁止と例文コピー禁止を守る。編集長は再収集せず、検証済み reporter 成果物の統合・横断 dedup 判断・Summary planning・append だけを行う。"
    $PromptBody = Get-Content -Path $PromptFile -Raw -Encoding UTF8
    Set-Content -Path $EditorPromptFile -Value ($DateHeader + "`n`n" + $PromptBody) -Encoding UTF8
    Write-Log "editor prompt date injected: header='$DateHeader' -> $EditorPromptFile"

    $MaxAgentAttempts = 3
    $preHead = (& $GitExe -C $RepoDir rev-parse HEAD 2>$null)
    $agentRc = $null
    for ($attempt = 1; $attempt -le $MaxAgentAttempts; $attempt++) {
        $priorGateFailCount = [Math]::Max(0, $attempt - 1)
        $NewsroomEditorModel = Select-NewsroomEditorModel -GateFailCount $priorGateFailCount -DedupConflictCount 0 -AppendMismatch:$false -SummaryQualityScore 5 -DeepDiveThemeCount 1
        Write-Log "wrapper invoke START (agent=codex, role=newsroom_editor, attempt=$attempt/$MaxAgentAttempts, Wrapper=$CodexWrapper, Model=$NewsroomEditorModel, gate_fail_count=$priorGateFailCount, TimeoutSec=$TimeoutSec, IdleTimeoutSec=$IdleTimeoutSec)"
        $editorSuccessProbe = "py -3.12 -m tools.validate_summary_reflection; if (`$LASTEXITCODE -ne 0) { exit `$LASTEXITCODE }; py -3.12 -m tools.validate_daily_quality --date $DateStamp; if (`$LASTEXITCODE -ne 0) { exit `$LASTEXITCODE }; py -3.12 -m tools.validate_generation_quality --date $DateStamp"
        $agentRc = Invoke-CodexWrapper -PromptFile $EditorPromptFile -TimeoutSec $TimeoutSec -IdleTimeoutSec $IdleTimeoutSec -Model $NewsroomEditorModel -OutputSchema $EditorSummarySchema -FlowName 'newsroom_editor' -SuccessProbeCommand $editorSuccessProbe -SuccessProbeIntervalSec 30 -SuccessProbeMinElapsedSec 120
        Write-Log "wrapper invoke END (agent=codex, role=newsroom_editor, attempt=$attempt/$MaxAgentAttempts, rc=$agentRc)"

        if ($agentRc -eq 0) { break }

        if ($agentRc -eq 124) {
            $postHead = (& $GitExe -C $RepoDir rev-parse HEAD 2>$null)
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
Write-Log 'summary reflection gate start (validate_summary_reflection --latest)'
$summaryReflectionRc = Invoke-AutonomousGate -GateId 'summary-reflection' -Category 'summary' -PythonArgs @('-m', 'tools.validate_summary_reflection') -Artifacts @("digest/Summary/$DateStamp.md")
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
$dailyQualityRc = Invoke-AutonomousGate -GateId 'daily-quality' -Category 'daily' -PythonArgs @('-m', 'tools.validate_daily_quality', '--date', $DateStamp) -Artifacts $DailyDigestArtifacts
if ($dailyQualityRc -ne 0) {
    Invoke-AutonomousCompletionPolicy -FailureKind 'content' -GateId 'daily-quality' -Reason 'daily quality autonomous gate failed' -ExitCode $dailyQualityRc
}
Write-Log 'daily quality gate OK'

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
$DeepDiveContextPackFailed = $false
if ((-not $RecoverOnly) -and (-not $ResumeAfterDeepDive)) {
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
} elseif ($DeepDiveContextPackFailed) {
    Write-Log 'skipping deepdive codex because context pack failed'
} else {
    Write-Log "deepdive wrapper invoke START (agent=codex, Model=$DeepDiveModel, TimeoutSec=$DeepDiveTimeoutSec, IdleTimeoutSec=$IdleTimeoutSec)"
    # 2026-06-10: IdleTimeoutSec 0 → 900 (digest 側と同じ理由。stream-json 既定化で
    # 15 分無出力 = 真のハング検知が成立。DeepDive は非致命なので誤検知しても digest は止まらない)
    $ddRc = Invoke-CodexWrapper -PromptFile $DeepDivePromptFile -TimeoutSec $DeepDiveTimeoutSec -IdleTimeoutSec $IdleTimeoutSec -Model $DeepDiveModel -FlowName 'deepdive'
    Write-Log "deepdive wrapper invoke END (agent=codex, rc=$ddRc)"
    if ($ddRc -eq 124) {
        Write-Log "WARN: deepdive codex TIMEOUT after $DeepDiveTimeoutSec sec (non-fatal, digest は続行)"
    } elseif ($ddRc -ne 0) {
        Write-Log "WARN: deepdive codex exited with $ddRc (non-fatal, digest は続行)"
    } else {
    Write-Log "deepdive $AgentName OK (1 本生成 or テーマゲート休載)"
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
Write-Log 'pytest gate start (pytest tests/ -q -m "not network")'
$PytestBaseTemp = Join-Path $RepoDir '.pytest-tmp'
New-Item -ItemType Directory -Force -Path $PytestBaseTemp | Out-Null
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
$dailyTtsPublishArgs = @('-m', 'tools.tts.publish_audio', $DateStamp)
if ($NoPublish) { $dailyTtsPublishArgs = @('-m', 'tools.tts.publish_audio', $DateStamp, '--dry-run') }
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
$deepDiveTtsPublishArgs = @('-m', 'tools.tts.deepdive_audio', $DateStamp)
if ($NoPublish) { $deepDiveTtsPublishArgs = @('-m', 'tools.tts.deepdive_audio', $DateStamp, '--dry-run') }
foreach ($deepDiveTtsStep in @(
    @{ Name = 'deepdive dialogue script build'; Args = @('-m', 'tools.tts.build_deepdive_dialogue_script', $DeepDiveMarkdown, '--output', $DeepDiveDialogueScript, '--context-pack', $DeepDiveContextPack) },
    @{ Name = 'deepdive dialogue synthesize'; Args = @('-m', 'tools.tts.deepdive_dialogue', $DeepDiveDialogueScript, '--out-name', $DateStamp) },
    @{ Name = 'deepdive dialogue publish'; Args = $deepDiveTtsPublishArgs }
)) {
    Write-Log "$($deepDiveTtsStep.Name) start"
    try {
        Push-Location $RepoDir
        try {
            Invoke-Logged { & $PyExe @($deepDiveTtsStep.Args) }
            $deepDiveTtsRc = $LASTEXITCODE
        } finally {
            Pop-Location
        }
        if ($deepDiveTtsRc -ne 0) {
            Write-Log "ERROR: $($deepDiveTtsStep.Name) exited with $deepDiveTtsRc. DeepDive dialogue audio is required for normal publish."
            Invoke-AutonomousCompletionPolicy -FailureKind 'local-tool' -GateId 'deepdive-tts' -Reason "$($deepDiveTtsStep.Name) failed" -ExitCode $deepDiveTtsRc
        }
        Write-Log "$($deepDiveTtsStep.Name) done"
    } catch {
        Write-Log "ERROR: $($deepDiveTtsStep.Name) failed: $($_.Exception.Message). DeepDive dialogue audio is required for normal publish."
        Invoke-AutonomousCompletionPolicy -FailureKind 'local-tool' -GateId 'deepdive-tts' -Reason "$($deepDiveTtsStep.Name) failed" -ExitCode 1
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
    Invoke-Logged { & $GitExe -C $RepoDir add 'digest/' 'data/' }
    if ($LASTEXITCODE -ne 0) { Write-Log "ERROR: git add digest/data failed (rc=$LASTEXITCODE)"; exit 1 }
    Invoke-Logged { & $GitExe -C $RepoDir diff --cached --quiet }
    $digestDiffRc = $LASTEXITCODE
    if ($digestDiffRc -eq 1) {
        Write-Log 'digest/data has changes, committing'
        Invoke-Logged { & $GitExe -C $RepoDir commit -m "daily: digest and data for $DateStamp" }
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
Write-Log "current DeepDive URL gate start (validate_deepdive_urls $DateStamp)"
Push-Location $RepoDir
try {
    Invoke-Logged { & $PyExe '-m' 'tools.validate_deepdive_urls' "digest\DeepDive\$DateStamp-DeepDive.md" }
    $currentDeepDiveUrlRc = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($currentDeepDiveUrlRc -ne 0) {
    Write-Log "current DeepDive URL gate failed (rc=$currentDeepDiveUrlRc). normal publish is blocked."
    Invoke-AutonomousCompletionPolicy -FailureKind 'content' -GateId 'current-deepdive-url' -Reason 'current DeepDive URL validation failed' -ExitCode $currentDeepDiveUrlRc
}
Write-Log 'current DeepDive URL gate OK'

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
    Invoke-Logged { & $GitExe -C $RepoDir add 'docs/' }
    if ($LASTEXITCODE -ne 0) { Write-Log "ERROR: git add docs/ failed (rc=$LASTEXITCODE)"; exit 1 }

    # git diff --cached --quiet docs/ は差分があると exit 1、無いと exit 0。
    Invoke-Logged { & $GitExe -C $RepoDir diff --cached --quiet -- 'docs/' }
    $diffRc = $LASTEXITCODE
    if ($diffRc -eq 1) {
        Write-Log 'docs/ has changes, committing'
        Invoke-Logged { & $GitExe -C $RepoDir commit -m "docs: generate public pages for $DateStamp" }
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
    Invoke-Logged { & $GitExe -C $RepoDir add "data/distribution/$DateStamp.json" }
    if ($LASTEXITCODE -ne 0) { Write-Log "ERROR: git add distribution manifest failed (rc=$LASTEXITCODE)"; exit 1 }
    Invoke-Logged { & $GitExe -C $RepoDir diff --cached --quiet -- "data/distribution/$DateStamp.json" }
    $distributionDiffRc = $LASTEXITCODE
    if ($distributionDiffRc -eq 1) {
        Write-Log 'distribution manifest has changes, committing'
        Invoke-Logged { & $GitExe -C $RepoDir commit -m "distribution: record publish state for $DateStamp" }
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
    Write-Log 'NoPush mode: skipping git push origin main'
} else {
    Write-Log 'push origin main start (digest + docs を同時公開)'
    Invoke-Logged { & $GitExe -C $RepoDir push origin main }
    if ($LASTEXITCODE -ne 0) { Write-Log "ERROR: push failed (rc=$LASTEXITCODE)"; exit 1 }
    Write-Log 'push origin main done (digest + docs pushed)'

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
        Write-Log "ERROR: publish verification failed (rc=$publishVerifyRc). remote/pages/public/audio sentinel did not converge."
        Set-RunnerState -Status 'publish_failed' -Message 'publish verification failed' -ExitCode 1
        exit 1
    }
    Write-Log 'publish verification OK'

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

    Write-Log 'publish-complete manifest verification start'
    $publishCompleteManifest = Join-Path $RepoDir "build\publish-complete\$DateStamp.json"
    Push-Location $RepoDir
    try {
        Invoke-Logged { & $PyExe '-m' 'tools.daily_self_heal' 'verify-publish-complete' '--repo-root' $RepoDir '--date' $DateStamp '--remote' 'origin' '--branch' 'main' '--public-base-url' $PublicBaseUrl '--wait-sec' '0' '--poll-sec' $PublishVerifyPollSec '--output' $publishCompleteManifest }
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
    $NormalPublishVerified = $true
    Write-Log 'publish-complete manifest verification OK'
}

# ===== 6. Web Push 通知（docs 公開後・.venv python = $PyExe で送る） =====
# 2026-05-30 に push を .bat 側へ移したが、タスクスケジューラが実行する実行体は本 .ps1。
# .ps1 に push ステップが無く 2026-05-31 朝の通知が一度も飛ばなかった事故の恒久修正
# （.bat と .ps1 の二重管理で修正が実行経路に入らなかった）。
# 2026-06-16: 通知は正常な通常公開バッチだけに限定し、fallback / RecoverOnly / NoPush /
# publish verify 未完了では send_push.py 自体を呼ばない。
# push は付随機能なので非致命（send_push 自身が購読 0 / 鍵無しでも exit 0 を返す）。
if (Should-SendNormalBatchNotification) {
    Write-Log 'send_push start'
    Push-Location $RepoDir
    try {
        Invoke-Logged { & $PyExe 'tools\send_push.py' }
        $pushRc = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($pushRc -ne 0) { Write-Log "WARN: send_push exited $pushRc (non-fatal)" }
    Write-Log "send_push done rc=$pushRc"
} elseif ($NoPush) {
    Write-Log 'NoPush mode: skipping send_push'
} elseif ($RecoverOnly) {
    Write-Log 'RecoverOnly mode: skipping send_push (not a normal batch)'
} elseif (-not $NormalPublishVerified) {
    Write-Log 'send_push skipped: publish verification not confirmed'
} else {
    Write-Log 'send_push skipped: not a normal batch'
}

Write-CodexUsageWindowSnapshot -Phase 'end'
if ($NoPublish) {
    Write-Log 'news-grasp-runner.ps1 PUBLISH DRY RUN OK'
} elseif ($NoPush) {
    Write-Log 'news-grasp-runner.ps1 SMOKE OK'
} else {
    Write-Log 'news-grasp-runner.ps1 OK'
}
exit 0
