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
#              gate 群 → docs 再生成 → docs commit → push → send_push だけを実行する
#              復旧モード。gate failed 後、対象 md/jsonl を手修正してから使う。
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
    [switch] $UseCodex,
    [int] $IdleTimeoutSec = 900,
    [switch] $Stage2EditorSmokeOnly,
    [switch] $StopAfterEditorStart,
    [string] $RepoDirOverride = '',
    [string] $CodexWrapperOverride = '',
    [string] $CodexExeOverride = '',
    [string] $PyExeOverride = '',
    [string] $DateStampOverride = '',
    [string] $LogDirOverride = '',
    [string] $StateFileOverride = '',
    [int] $PublishVerifyWaitSec = 600,
    [int] $PublishVerifyPollSec = 30
)

# PS 5.1 で $ErrorActionPreference = 'Stop' にすると、native command (git 等) の
# stderr 出力で NativeCommandError 例外が発火し script が中断する。git fetch /
# pull は進捗を stderr に出すため、ここは Continue にして $LASTEXITCODE で判定する。
$ErrorActionPreference = 'Continue'
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$UseCodex = $true
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

$RepoDir   = Resolve-NewsGraspRepoDir -Override $RepoDirOverride
$LogDir    = Join-Path $env:USERPROFILE 'bin\news-grasp-logs'
$GitExe    = 'C:\Program Files\Git\cmd\git.exe'
$CodexExe  = Join-Path $env:USERPROFILE 'bin\codex.ps1'
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
if ($CodexExeOverride) { $CodexExe = $CodexExeOverride }
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

function Set-RunnerState {
    param(
        [string] $Status,
        [string] $Message,
        [int] $ExitCode = -1,
        [switch] $ResetStartedAt
    )
    $now = Get-Date -Format 'yyyy-MM-ddTHH:mm:ss.fffK'
    $state = [ordered]@{
        status = $Status
        message = $Message
        exit_code = $ExitCode
        updated_at = $now
        date = $DateStamp
        pid = $PID
        repo_dir = $RepoDir
        log_path = $LogPath
    }
    if ($ResetStartedAt) {
        $state.started_at = $now
    } elseif (Test-Path $StateFile) {
        try {
            $prev = Get-Content -LiteralPath $StateFile -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($prev.started_at) {
                $state.started_at = [string]$prev.started_at
            }
        } catch {
            $state.started_at = $now
        }
    } else {
        $state.started_at = $now
    }
    $json = $state | ConvertTo-Json -Depth 4
    [System.IO.File]::WriteAllText($StateFile, $json, [System.Text.UTF8Encoding]::new($false))
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
        Set-RunnerState -Status 'ok' -Message $Text -ExitCode 0
    } elseif ($Text -eq 'news-grasp-runner.ps1 OK (published_fallback_with_notice)') {
        Set-RunnerState -Status 'fallback_ok' -Message $Text -ExitCode 0
    } elseif ($Text -eq 'news-grasp-runner.ps1 SMOKE OK') {
        Set-RunnerState -Status 'smoke_ok' -Message $Text -ExitCode 0
    }
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
        Write-Log "ERROR: runner binary drift detected (live=$liveRunnerSha repo=$repoRunnerSha). Run scripts/ops/install-news-grasp-ops.ps1 before scheduled execution."
        Set-RunnerState -Status 'failed' -Message 'runner binary drift' -ExitCode 1
        exit 1
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

function Invoke-CodexWrapper {
    param(
        [string] $PromptFile,
        [int] $TimeoutSec,
        [int] $IdleTimeoutSec,
        [string] $Model = '',
        [string] $OutputSchema = $CodexOutputSchema,
        [string] $OutputLastMessage = $CodexLastMessage,
        [string] $FlowName = 'unknown'
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
    if ($Model) { $codexArgs['Model'] = $Model }
    & $CodexWrapper @codexArgs
    $wrapperOk = $?
    $wrapperRc = $LASTEXITCODE
    if (-not $wrapperOk) {
        if ($null -eq $wrapperRc -or $wrapperRc -eq 0) { return 125 }
    }
    return $wrapperRc
}

function Invoke-TargetedRepair {
    param(
        [string] $GateId,
        [string] $Category,
        [string] $CapturePath,
        [string[]] $Artifacts
    )
    if ($RecoverOnly) {
        Write-Log "repair worker skipped: RecoverOnly mode (gate=$GateId)"
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
        Write-Log "repair worker denied by retry budget (gate=$GateId, rc=$decisionRc)"
        return 1
    }

    $repairPrompt = Join-Path ([System.IO.Path]::GetTempPath()) ("news-grasp-repair-$GateId-$DateStamp.md")
    $failureText = ''
    if (Test-Path $CapturePath) {
        $failureText = Get-Content -LiteralPath $CapturePath -Raw -Encoding UTF8
    }
    $artifactText = [string]::Join(', ', $Artifacts)
    $prompt = @"
News-Grasp RecoverOnly targeted repair.

目的:
- gate 失敗を 1 回だけ修復する。
- commit / push / docs 生成 / 全体再生成は禁止。
- 変更してよいのは下記 artifact と、その修復に必須の最小ファイルだけ。

gate_id: $GateId
category: $Category
artifacts: $artifactText

失敗ログ:
$failureText

作業:
1. 失敗ログが示す不備だけを修正する。
2. 同じ gate を通すための最小修正に留める。
3. git commit / git push は絶対に実行しない。
4. 修正したら停止する。
"@
    [System.IO.File]::WriteAllText($repairPrompt, $prompt, [System.Text.UTF8Encoding]::new($false))
    $repairModel = Get-ModelPolicyValue -Role 'editor' -Key 'default'
    Write-Log "repair wrapper invoke START (agent=codex, gate=$GateId, Model=$repairModel, TimeoutSec=900)"
    $repairRc = Invoke-CodexWrapper -PromptFile $repairPrompt -TimeoutSec 900 -IdleTimeoutSec 300 -Model $repairModel -FlowName "repair:$GateId"
    Write-Log "repair wrapper invoke END (agent=codex, gate=$GateId, rc=$repairRc)"
    return $repairRc
}

function Invoke-PythonGateWithRepair {
    param(
        [string] $GateId,
        [string] $Category,
        [string[]] $PythonArgs,
        [string[]] $Artifacts
    )
    for ($attempt = 1; $attempt -le 2; $attempt++) {
        $capturePath = Join-Path ([System.IO.Path]::GetTempPath()) ("news-grasp-gate-$GateId-$DateStamp-attempt$attempt.log")
        Write-Log "$GateId gate attempt $attempt start"
        Push-Location $RepoDir
        try {
            Invoke-LoggedCapture -CapturePath $capturePath -Block { & $PyExe @PythonArgs }
            $gateRc = $LASTEXITCODE
        } finally {
            Pop-Location
        }
        if ($gateRc -eq 0) {
            Write-Log "$GateId gate OK (attempt=$attempt)"
            return 0
        }
        Write-Log "$GateId gate failed (attempt=$attempt, rc=$gateRc)"
        $repairRc = Invoke-TargetedRepair -GateId $GateId -Category $Category -CapturePath $capturePath -Artifacts $Artifacts
        if ($repairRc -ne 0) {
            return $gateRc
        }
    }
    return 1
}

function Restore-UnverifiedGeneratedArtifacts {
    # fallback 公開時は docs notice だけを残し、未検証の当日 digest/data を次回 run に持ち越さない。
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
        $tracked = & $GitExe -C $RepoDir ls-files -- $rel
        if ($tracked) {
            Invoke-Logged { & $GitExe -C $RepoDir checkout -- $rel }
            if ($LASTEXITCODE -ne 0) {
                Write-Log "WARN: failed to restore tracked generated artifact: $rel (rc=$LASTEXITCODE)"
            }
        } elseif (Test-Path $full) {
            Remove-Item -LiteralPath $full -Recurse -Force
            Write-Log "removed untracked generated artifact: $rel"
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
    Write-Log "fallback publish start (reason=$Reason)"
    Restore-UnverifiedGeneratedArtifacts
    $lastGoodDocsRef = Resolve-LastGoodDocsRef
    if ($lastGoodDocsRef) {
        Write-Log "fallback docs restore from last-good ref $lastGoodDocsRef"
        Invoke-Logged { & $GitExe -C $RepoDir checkout $lastGoodDocsRef -- 'docs/' }
    } else {
        Write-Log "WARN: last-good docs ref not found; restoring docs from HEAD"
        Invoke-Logged { & $GitExe -C $RepoDir checkout -- 'docs/' }
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Log "ERROR: docs restore for fallback failed (rc=$LASTEXITCODE)"
        exit 1
    }

    Push-Location $RepoDir
    try {
        Invoke-Logged { & $PyExe '-m' 'tools.publish_fallback' 'fallback' '--date' $DateStamp '--reason' $Reason }
        $fallbackRc = $LASTEXITCODE
        if ($fallbackRc -eq 0) {
            Invoke-Logged { & $PyExe '-m' 'tools.validate_availability' '--expect-fallback' }
            $availabilityRc = $LASTEXITCODE
        } else {
            $availabilityRc = 1
        }
    } finally {
        Pop-Location
    }
    if ($fallbackRc -ne 0 -or $availabilityRc -ne 0) {
        Write-Log "ERROR: fallback availability gate failed (fallbackRc=$fallbackRc, availabilityRc=$availabilityRc)"
        exit 1
    }

    Invoke-Logged { & $GitExe -C $RepoDir add 'docs/' }
    if ($LASTEXITCODE -ne 0) { Write-Log "ERROR: git add fallback docs failed (rc=$LASTEXITCODE)"; exit 1 }
    Invoke-Logged { & $GitExe -C $RepoDir diff --cached --quiet -- 'docs/' }
    $fallbackDiffRc = $LASTEXITCODE
    if ($fallbackDiffRc -eq 1) {
        Invoke-Logged { & $GitExe -C $RepoDir commit -m "docs: publish fallback notice for $DateStamp" }
        if ($LASTEXITCODE -ne 0) { Write-Log "ERROR: fallback docs commit failed (rc=$LASTEXITCODE)"; exit 1 }
    } elseif ($fallbackDiffRc -eq 0) {
        Write-Log 'fallback docs no changes; pushing existing public state'
    } else {
        Write-Log "ERROR: fallback diff failed (rc=$fallbackDiffRc)"
        exit 1
    }
    Invoke-Logged { & $GitExe -C $RepoDir push origin main }
    if ($LASTEXITCODE -ne 0) { Write-Log "ERROR: fallback push failed (rc=$LASTEXITCODE)"; exit 1 }
    Write-Log 'fallback push origin main done'

    Push-Location $RepoDir
    try {
        Invoke-Logged { & $PyExe 'tools\send_push.py' }
        $fallbackPushRc = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($fallbackPushRc -ne 0) { Write-Log "WARN: fallback send_push exited $fallbackPushRc (non-fatal)" }
    Write-CodexUsageWindowSnapshot -Phase 'end'
    Write-Log 'news-grasp-runner.ps1 OK (published_fallback_with_notice)'
    exit 0
}

function Stop-ContentGateWithoutFallback {
    param(
        [Parameter(Mandatory=$true)][string] $GateId,
        [Parameter(Mandatory=$true)][int] $ExitCode
    )
    Write-Log "ERROR: $GateId gate failed after bounded repair (rc=$ExitCode). content gate failure does not publish fallback notice; leaving existing public state unchanged."
    Write-Log "RECOVER: fix the reported digest/data issue, rerun the specific gate, then rerun runner with -RecoverOnly or publish manually after all gates pass."
    exit 1
}

# ===== sentinel: 起動できた事実 =====
$pidStamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'
Add-Content -Path $InvokedLog -Value "[$pidStamp] runner-invoked pid=$PID ps1 smoke=$SmokeTest recover=$RecoverOnly" -Encoding UTF8

Add-Content -Path $LogPath -Value '' -Encoding UTF8
Add-Content -Path $LogPath -Value '==========================================' -Encoding UTF8
Set-RunnerState -Status 'running' -Message 'runner started' -ExitCode -1 -ResetStartedAt
Write-Log "news-grasp-runner.ps1 start (smoke=$SmokeTest, recover=$RecoverOnly, pid=$PID)"
Assert-RunnerBinaryInSync
Write-CodexUsageWindowSnapshot -Phase 'start'
Add-Content -Path $LogPath -Value '==========================================' -Encoding UTF8

# ===== 0. リポ存在チェック =====
if (-not (Test-Path (Join-Path $RepoDir '.git'))) {
    Write-Log "ERROR: repo not found at $RepoDir"
    exit 1
}

if ($PreflightOnly) {
    Write-Log 'PreflightOnly mode: skipping codex / git pull / push / generate_pages'
    Push-Location $RepoDir
    try {
        Invoke-Logged { & $PyExe '-m' 'tools.newsroom_preflight' '--repo-root' $RepoDir }
        $preflightRc = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($preflightRc -ne 0) {
        Write-Log "ERROR: newsroom preflight failed (rc=$preflightRc)"
        exit $preflightRc
    }
    Write-Log 'news-grasp-runner.ps1 PREFLIGHT OK'
    exit 0
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
} else {
    if (Test-Path $NetWait) {
        Write-Log 'net reachability wait start (github.com / api.github.com :443, max 10x30s)'
        Invoke-Logged { & $PyExe $NetWait --host github.com --host api.github.com --port 443 --retries 10 --interval-sec 30 --connect-timeout-sec 5 }
        if ($LASTEXITCODE -ne 0) {
            Write-Log "ERROR: network unreachable (github.com:443) after wait; aborting before git fetch (rc=$LASTEXITCODE)"
            exit 1
        }
        Write-Log 'net reachability OK'
    } else {
        Write-Log "WARN: net_wait.py not found at $NetWait; skipping net reachability wait"
    }

    # ===== 1. git fetch / pull =====
    Write-Log 'git fetch start'
    Invoke-Logged { & $GitExe -C $RepoDir fetch --quiet origin main }
    if ($LASTEXITCODE -ne 0) { Write-Log "ERROR: git fetch failed (rc=$LASTEXITCODE)"; exit 1 }

    Write-Log 'git pull --ff-only start'
    Invoke-Logged { & $GitExe -C $RepoDir pull --ff-only origin main }
    if ($LASTEXITCODE -ne 0) { Write-Log "ERROR: git pull failed (rc=$LASTEXITCODE, manual resolve required)"; exit 1 }
}

if ($SmokeTest) {
    Write-Log 'SmokeTest mode: skipping codex / push / generate_pages'
    Write-Log 'news-grasp-runner.ps1 SMOKE OK'
    exit 0
}

if ($RecoverOnly) {
    Write-Log 'RecoverOnly mode: skipping digest codex; using current local digest/data commits and files'
} else {
    # ===== Stage0: deterministic candidate harvest (LLM 前固定実行) =====
    $CandidateDir = Join-Path $RepoDir 'build\candidates'
    $DedupedCandidateDir = Join-Path $RepoDir 'build\deduped-candidates'
    $HarvestAuditDir = Join-Path $RepoDir "data\search_audit\$DateStamp"
    $Categories = @('fx','ai','it','mobility','manufacturing','economy','game')
    if ($Stage2EditorSmokeOnly) {
        Write-Log 'Stage2EditorSmokeOnly mode: skipping Stage0 harvest and Stage1 dedup; using existing deduped candidates'
        New-Item -ItemType Directory -Path $DedupedCandidateDir -Force | Out-Null
    } else {
        if (Test-Path $CandidateDir) { Remove-Item -LiteralPath $CandidateDir -Recurse -Force -ErrorAction SilentlyContinue }
        if (Test-Path $DedupedCandidateDir) { Remove-Item -LiteralPath $DedupedCandidateDir -Recurse -Force -ErrorAction SilentlyContinue }
        New-Item -ItemType Directory -Path $CandidateDir -Force | Out-Null
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
            if ($harvestRc -ne 0) { Write-Log "ERROR: Stage0 harvest failed category=$cat rc=$harvestRc"; exit 1 }
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
                Wait-Job -Job @($jobs | Where-Object { $_.State -eq 'Running' }) -Any | Out-Null
            }

            Write-Log "reporter job START (agent=codex, role=reporter, category=$waveCat, attempt=$Attempt/$ReporterMaxAttempts, Wrapper=$CodexWrapper, Model=$ReporterModel, TimeoutSec=$TimeoutSec, IdleTimeoutSec=$IdleTimeoutSec)"
            $jobs += Start-Job -ArgumentList @(
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
                }
            }
        }

        if ($jobs.Count -eq 0) { return @() }
        Wait-Job -Job $jobs | Out-Null
        $results = @($jobs | Receive-Job)
        Remove-Job -Job $jobs -Force -ErrorAction SilentlyContinue

        foreach ($result in $results) {
            if ($result.wrapper_log -and (Test-Path $result.wrapper_log)) {
                Add-Content -Path $LogPath -Value (Get-Content -LiteralPath $result.wrapper_log -Raw -Encoding UTF8) -Encoding UTF8
            }
            if ($result.usage_log -and (Test-Path $result.usage_log)) {
                Add-Content -Path $CodexUsageLog -Value (Get-Content -LiteralPath $result.usage_log -Raw -Encoding UTF8) -Encoding UTF8
            }
            Write-Log "reporter job END category=$($result.category) attempt=$($result.attempt)/$ReporterMaxAttempts rc=$($result.rc) elapsed_sec=$($result.elapsed_sec)"
        }

        return $results
    }

    $retryCategories = @($Categories)
    $terminalFailures = @{}
    for ($attempt = 1; $attempt -le $ReporterMaxAttempts -and $retryCategories.Count -gt 0; $attempt++) {
        $waveResults = Invoke-ReporterWave -Attempt $attempt -WaveCategories $retryCategories
        $nextRetryCategories = @()
        $failedCategories = @()

        foreach ($waveResult in $waveResults) {
            $catName = [string]$waveResult.category
            $failureReason = $null
            if ([int]$waveResult.rc -ne 0) {
                $failureReason = "wrapper_rc=$($waveResult.rc)"
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
        exit 1
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
    $editorManifest = [pscustomobject]@{
        date = $DateStamp
        reporter_artifacts = @($ReporterArtifacts | ForEach-Object { $_.records_file })
        reporter_artifact_details = $ReporterArtifacts
        dedup_file = $DedupedCandidateDir
        source_policy = 'no_recollection'
    }
    $editorManifest | ConvertTo-Json -Depth 8 | Set-Content -Path $EditorInputManifest -Encoding UTF8

    $EditorPromptFile = Join-Path ([System.IO.Path]::GetTempPath()) ("news-grasp-editor-prompt-$DateStamp.md")
    $DateHeader = "今日の日付は $DateStamp (JST) である。Stage2 reporter artifact manifest は $EditorInputManifest にある。Stage1 dedup は build/deduped-candidates にある。編集長は再収集せず、検証済み reporter 成果物の統合・横断 dedup 判断・Summary planning・append だけを行う。"
    $PromptBody = Get-Content -Path $PromptFile -Raw -Encoding UTF8
    Set-Content -Path $EditorPromptFile -Value ($DateHeader + "`n`n" + $PromptBody) -Encoding UTF8
    Write-Log "editor prompt date injected: header='$DateHeader' -> $EditorPromptFile"

    $MaxAgentAttempts = 3
    $NewsroomEditorModel = Get-ModelPolicyValue -Role 'newsroom_editor' -Key 'default'
    $preHead = (& $GitExe -C $RepoDir rev-parse HEAD 2>$null)
    $agentRc = $null
    for ($attempt = 1; $attempt -le $MaxAgentAttempts; $attempt++) {
        Write-Log "wrapper invoke START (agent=codex, role=newsroom_editor, attempt=$attempt/$MaxAgentAttempts, Wrapper=$CodexWrapper, Model=$NewsroomEditorModel, TimeoutSec=$TimeoutSec, IdleTimeoutSec=$IdleTimeoutSec)"
        $agentRc = Invoke-CodexWrapper -PromptFile $EditorPromptFile -TimeoutSec $TimeoutSec -IdleTimeoutSec $IdleTimeoutSec -Model $NewsroomEditorModel -OutputSchema $EditorSummarySchema -FlowName 'newsroom_editor'
        Write-Log "wrapper invoke END (agent=codex, role=newsroom_editor, attempt=$attempt/$MaxAgentAttempts, rc=$agentRc)"

        if ($agentRc -eq 0) { break }

        if ($agentRc -eq 124) {
            $postHead = (& $GitExe -C $RepoDir rev-parse HEAD 2>$null)
            if ($postHead -ne $preHead) {
                Write-Log "ERROR: codex TIMEOUT (rc=124) and HEAD changed ($preHead -> $postHead): partial commits exist, NOT retrying"
                Write-Log "RECOVER: Check git status plus digest/$DateStamp*.md and data/articles.jsonl, repair if needed, then run: powershell -NoProfile -ExecutionPolicy Bypass -File $PSCommandPath -RecoverOnly"
                exit 124
            }
            if ($attempt -lt $MaxAgentAttempts) {
                Write-Log "WARN: codex idle/timeout (rc=124, HEAD unchanged = no output/commits): intermittent startup hang suspected, retrying (next attempt=$($attempt + 1)/$MaxAgentAttempts)"
                continue
            }
            Write-Log "ERROR: codex TIMEOUT (rc=124) after $MaxAgentAttempts attempts, giving up"
            Write-Log "RECOVER: partial artifacts may exist. Check git status plus digest/$DateStamp*.md and data/articles.jsonl, repair if needed, then run: powershell -NoProfile -ExecutionPolicy Bypass -File $PSCommandPath -RecoverOnly"
            exit 124
        }

        if ($agentRc -eq 123) {
            Write-Log "ERROR: codex CLI rate limit / out of credits (rc=123; wrapper RESULT line に api_error_status / result あり)。リトライしない。"
            Write-Log "RECOVER: API 上限/クレジット回復後に再実行: powershell -NoProfile -ExecutionPolicy Bypass -File $PSCommandPath (部分生成済なら -RecoverOnly)"
            exit 123
        }

        Write-Log "ERROR: codex exited with $agentRc (not a timeout; no retry)"
        exit 1
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
$summaryReflectionRc = Invoke-PythonGateWithRepair -GateId 'summary-reflection' -Category 'summary' -PythonArgs @('-m', 'tools.validate_summary_reflection') -Artifacts @("digest/Summary/$DateStamp.md")
if ($summaryReflectionRc -ne 0) {
    Stop-ContentGateWithoutFallback -GateId 'summary-reflection' -ExitCode $summaryReflectionRc
}
Write-Log 'summary reflection gate OK'

# ===== 2.2 daily quality gate (hero fallback / stale source URL date) =====
# 2026-06-08: Summary の reflection は存在していても frontmatter hero_left / hero_right
# が欠落し、LP TODAY'S THEME がブランド fallback「時勢を掴み、日々に新たに。」へ
# 落ちた。また、記事 record の date は収集日であり、URL パス上の発行日が前日以前
# でも pre-push gate が検出できなかった。日次公開境界で両方を fail loud にする。
Write-Log "daily quality gate start (validate_daily_quality --date $DateStamp)"
$dailyQualityRc = Invoke-PythonGateWithRepair -GateId 'daily-quality' -Category 'daily' -PythonArgs @('-m', 'tools.validate_daily_quality', '--date', $DateStamp) -Artifacts @("digest/Summary/$DateStamp.md", "data/articles.jsonl")
if ($dailyQualityRc -ne 0) {
    Stop-ContentGateWithoutFallback -GateId 'daily-quality' -ExitCode $dailyQualityRc
}
Write-Log 'daily quality gate OK'

# ===== Stage4: Codex DeepDive 生成 + commit (テーマゲート式日次・非致命) =====
# 2026-06-01: 旧 news-grasp-weekly-runner.ps1 (毎週日曜 23:00 の別タスク) を日次に統合した step。
#   - digest とは別の agent プロセスで走らせ、コンテキスト/トークン予算を完全に分離する
#     (1 セッション統合は 2026-05 の 415 万トークン破綻の再来リスクがあるため採らない)。
#   - テーマが立たない日は prompts/deepdive-runner-prompt.md 側のテーマゲートで休載 (commit しない)。
#     = コストは「出す価値がある日だけ」に自己制御される。
#   - DeepDive は付随機能なので非致命: 失敗 / timeout / 休載でも digest の公開は絶対に止めない
#     (digest が主、DeepDive は additive)。エラーは WARN ログのみで step 3 以降に進む。
$DeepDivePromptFile = Join-Path $RepoDir 'prompts\deepdive-runner-prompt.md'
$DeepDiveTimeoutSec = 1800
$DeepDiveModel = Get-ModelPolicyValue -Role 'deepdive' -Key 'default'
if ($RecoverOnly) {
    Write-Log "RecoverOnly mode: skipping deepdive codex; keeping existing DeepDive state"
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
# を物理照合し、session 未登録 URL を捏造疑いで fatal 化する。session ファイル不在
# 時は本番 runner では fatal にする。対話・ad-hoc の audit 既定は互換維持し、
# runner が --require-session を渡す経路だけ完走扱いを禁止する。
Write-Log 'URL liveness gate start (audit_all_article_urls --gate --match-session --require-session)'
$urlGateRc = Invoke-PythonGateWithRepair -GateId 'url-liveness' -Category 'urls' -PythonArgs @('tools\audit_all_article_urls.py', '--gate', '--match-session', '--require-session') -Artifacts @('data/articles.jsonl', 'data/_session_urls.json', 'data/_session_urls.d')
if ($urlGateRc -ne 0) {
    Write-Log "URL liveness quarantine start (audit_all_article_urls --gate --match-session --require-session --quarantine-articles --apply)"
    Push-Location $RepoDir
    try {
        Invoke-Logged { & $PyExe 'tools\audit_all_article_urls.py' '--gate' '--match-session' '--require-session' '--quarantine-articles' '--apply' }
        $urlQuarantineRc = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($urlQuarantineRc -eq 0) {
        Write-Log 'URL liveness gate recheck after quarantine'
        Push-Location $RepoDir
        try {
            Invoke-Logged { & $PyExe 'tools\audit_all_article_urls.py' '--gate' '--match-session' '--require-session' }
            $urlRecheckRc = $LASTEXITCODE
        } finally {
            Pop-Location
        }
        if ($urlRecheckRc -eq 0) {
            Write-Log 'URL liveness gate OK after per-article quarantine'
            $urlGateRc = 0
        } else {
            Write-Log "ERROR: URL liveness recheck failed after quarantine (rc=$urlRecheckRc). fallback publish へ切替"
            Invoke-FallbackPublish -Reason 'url-liveness-gate-failed-after-quarantine'
        }
    } else {
        Write-Log "ERROR: URL liveness quarantine failed (rc=$urlQuarantineRc). fallback publish へ切替"
        Invoke-FallbackPublish -Reason 'url-liveness-quarantine-failed'
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
$recordGateRc = Invoke-PythonGateWithRepair -GateId 'record-schema' -Category 'records' -PythonArgs @('-m', 'tools.validate_record', '--recent', '7', '--issue-date', $DateStamp) -Artifacts @('data/articles.jsonl')
if ($recordGateRc -ne 0) {
    Stop-ContentGateWithoutFallback -GateId 'record-schema' -ExitCode $recordGateRc
}
Write-Log 'record schema gate OK'

# ===== 2.66 digest/articles 突合 gate (commit 後・push 前) =====
# 2026-06-13 Phase 3: digest md と articles.jsonl の当日 URL 集合を完全一致させる。
# 片方向の「digest md ⊆ articles.jsonl」だけでは、freshness gate が古記事を jsonl から
# 正しく落としたのに md にだけ残ったケースを append 漏れと誤検出する。双方向一致により
# digest-only は「古記事残存または append 漏れ」、articles-only は「カード生成漏れ」として
# push 前に止める。
Write-Log "digest/articles reconcile gate start (validate_digest_articles_reconcile --issue-date $DateStamp)"
$reconcileGateRc = Invoke-PythonGateWithRepair -GateId 'digest-articles-reconcile' -Category 'digest' -PythonArgs @('-m', 'tools.validate_digest_articles_reconcile', '--issue-date', $DateStamp) -Artifacts @('digest', 'data/articles.jsonl')
if ($reconcileGateRc -ne 0) {
    Stop-ContentGateWithoutFallback -GateId 'digest-articles-reconcile' -ExitCode $reconcileGateRc
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
$jaGateRc = Invoke-PythonGateWithRepair -GateId 'ja-callout' -Category 'digest' -PythonArgs @('-m', 'pytest', 'tests/test_title_ja_coverage.py::test_english_articles_require_ja_callout', '-q', '--tb=short', '--no-header') -Artifacts @('digest')
if ($jaGateRc -ne 0) {
    Stop-ContentGateWithoutFallback -GateId 'ja-callout' -ExitCode $jaGateRc
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
$pytestGateRc = Invoke-PythonGateWithRepair -GateId 'pytest-static' -Category 'tests' -PythonArgs @('-m', 'pytest', 'tests/', '-q', '--tb=line', '--no-header', '-m', 'not network') -Artifacts @('tests', 'tools', 'prompts', 'digest', 'data/articles.jsonl')
if ($pytestGateRc -ne 0) {
    Write-Log "ERROR: pytest gate failed after bounded repair (rc=$pytestGateRc). 通常号公開を中止し fallback publish へ切替"
    Invoke-FallbackPublish -Reason 'pytest-static-gate-failed'
}
Write-Log 'pytest gate OK'

# ===== 2.9 digest/data commit (全 content gate 通過後・docs 生成前) =====
# 2026-06-09 改定で生成側は commit しなくなった (routine-system.md ステップ 6:
# 「commit / push は ps1 が代行」)。しかし旧実装は docs/ しか git add しておらず、
# digest md / data/articles.jsonl が永久に未コミットになる片手落ちだった
# (2026-06-10 発覚)。gate 通過済みの digest / data のみを path 指定で stage し、
# 無関係な作業ツリー変更 (SETUP.md / tests 等) は巻き込まない。fallback 経路は
# この step を通らないため「未検証 digest commit が fallback push に乗る」事故は
# 引き続き構造的に起きない。
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

# ===== 3. docs/ 再生成 (旧 step 4 を前倒し / Plan v3 P0-A) =====
# 2026-06-06 Plan v3 P0-A: 旧構造は「digest push → docs build → docs push」で
# generate_pages.py 失敗時に digest md のみ origin 公開 + docs HTML 古いままという
# illegal state を表現可能だった。新構造は build 失敗で exit 1 → push 自体が走らない
# = サイレント公開停止が構造的に消える。
Write-Log 'generate_pages.py start'
Push-Location $RepoDir
try {
    Invoke-Logged { & $PyExe 'tools\generate_pages.py' }
    $pagesRc = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($pagesRc -ne 0) {
    Write-Log "ERROR: generate_pages.py exited with $pagesRc. 通常号公開を中止し fallback publish へ切替"
    Invoke-FallbackPublish -Reason 'generate-pages-failed'
}
Write-Log 'generate_pages.py done'

# ===== 3.05 DeepDive 必須 gate (generate 後・push 前) =====
# 2026-06-15: RecoverOnly 復旧時に Stage4 DeepDive を skip したまま Summary/カテゴリだけ
# 公開完了扱いにしてしまった。通常公開の完了条件は digest + docs + 当日 DeepDive まで
# 揃っていることなので、generate_pages.py 後に md/html の存在を fail loud にする。
Write-Log "deepdive required gate start (validate_daily_quality --date $DateStamp --require-deepdive)"
$deepDiveRequiredRc = Invoke-PythonGateWithRepair -GateId 'deepdive-required' -Category 'daily' -PythonArgs @('-m', 'tools.validate_daily_quality', '--date', $DateStamp, '--docs-root', 'docs', '--require-deepdive') -Artifacts @("digest/DeepDive/$DateStamp-DeepDive.md", "docs/deepdive/$DateStamp/index.html")
if ($deepDiveRequiredRc -ne 0) {
    Stop-ContentGateWithoutFallback -GateId 'deepdive-required' -ExitCode $deepDiveRequiredRc
}
Write-Log 'deepdive required gate OK'

# ===== 3.1 公開HTML smoke gate (generate 後・push 前) =====
# Summary md / digest md の構造 gate を通っても、最終成果物 docs/index.html 側で
# TOP STORY 画像や hero lead が退化する経路が残っていた。公開される HTML を
# 1 箇所で検査し、画像なし TOP STORY / 色面 fallback / 短文 lead を push 前に止める。
Write-Log "public HTML gate start (validate_public_home --date $DateStamp)"
$publicHomeRc = Invoke-PythonGateWithRepair -GateId 'public-html' -Category 'docs' -PythonArgs @('-m', 'tools.validate_public_home', '--date', $DateStamp) -Artifacts @('docs/index.html')
if ($publicHomeRc -ne 0) {
    Write-Log "ERROR: public HTML gate failed after bounded repair (rc=$publicHomeRc). 通常号公開を中止し fallback publish へ切替"
    Invoke-FallbackPublish -Reason 'public-html-gate-failed'
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

# ===== 3.5 publish-status を published_ok にリセット (fallback 抑止の状態同期) =====
# fallback publish は docs/publish-status.json に published_fallback_with_notice を残すが、
# 通常号が成功してもこれを戻す機構が無く stale なままだった (2026-06-12)。send_push は
# この状態を読んで fallback 中の通知を抑止するため、成功経路で必ず published_ok に戻す。
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
    Write-Log 'publish verification start (remote HEAD + public publish-status sentinel)'
    Push-Location $RepoDir
    try {
        Invoke-Logged { & $PyExe '-m' 'tools.daily_self_heal' 'verify-publish' '--repo-root' $RepoDir '--date' $DateStamp '--remote' 'origin' '--branch' 'main' '--public-base-url' $PublicBaseUrl '--wait-sec' $PublishVerifyWaitSec '--poll-sec' $PublishVerifyPollSec }
        $publishVerifyRc = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($publishVerifyRc -ne 0) {
        Write-Log "ERROR: publish verification failed (rc=$publishVerifyRc). remote/pages/public sentinel did not converge."
        Set-RunnerState -Status 'publish_failed' -Message 'publish verification failed' -ExitCode 1
        exit 1
    }
    Write-Log 'publish verification OK'
}

# ===== 6. Web Push 通知（docs 公開後・.venv python = $PyExe で送る） =====
# 2026-05-30 に push を .bat 側へ移したが、タスクスケジューラが実行する実行体は本 .ps1。
# .ps1 に push ステップが無く 2026-05-31 朝の通知が一度も飛ばなかった事故の恒久修正
# （.bat と .ps1 の二重管理で修正が実行経路に入らなかった）。
# push は付随機能なので非致命（send_push 自身が購読 0 / 鍵無しでも exit 0 を返す）。
if ($NoPush) {
    Write-Log 'NoPush mode: skipping send_push'
} else {
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
}

Write-CodexUsageWindowSnapshot -Phase 'end'
if ($NoPush) {
    Write-Log 'news-grasp-runner.ps1 SMOKE OK'
} else {
    Write-Log 'news-grasp-runner.ps1 OK'
}
exit 0
