# Wrap codex exec with hard and idle timeouts for News-Grasp daily runs.
#
# Args:
#   -CodexExe       path to codex executable
#   -PromptFile     UTF-8 prompt file; content is passed to codex exec via stdin
#   -LogFile        append stdout/stderr and wrapper diagnostics here
#   -TimeoutSec     wall-clock timeout in seconds
#   -IdleTimeoutSec no-output timeout in seconds (0 = disabled)
#   -WorkingDirectory repo directory passed to codex exec -C
#   -OutputSchema   schema path passed with --output-schema
#   -OutputLastMessage path passed with --output-last-message
#   -Model          optional model name
#   -ReasoningEffort optional model reasoning effort
#   -FlowName       flow label written to UsageLog
#   -UsageLog       JSONL path for flow token usage
#   -SuccessProbeCommand optional PowerShell command; rc=0 means artifacts are green and the child can be stopped as success
#   -SuccessProbeIntervalSec probe interval in seconds
#   -SuccessProbeMinElapsedSec minimum elapsed seconds before the first success probe
#   -ExtraArgs      additional codex exec args
#   -HighCost*      workspace-global admission。model process起動直前にcall予算を原子的に消費する
#
# Exit codes:
#   0..255 forwarded from codex
#   123    codex quota / usage limit detected from output
#   124    timeout hit, process killed
#   125    wrapper/startup failure
#   126    high-cost admission / model-call budget rejected before process launch

[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)] [string] $CodexExe,
    [Parameter(Mandatory=$true)] [string] $PromptFile,
    [Parameter(Mandatory=$true)] [string] $LogFile,
    [int] $TimeoutSec = 3600,
    [int] $IdleTimeoutSec = 900,
    [int] $HeartbeatSec = 60,
    [string] $WorkingDirectory = '',
    [string] $OutputSchema = '',
    [string] $OutputLastMessage = '',
    [string] $Model = '',
    [string] $ReasoningEffort = '',
    [string] $FlowName = '',
    [string] $UsageLog = '',
    [string] $SuccessProbeCommand = '',
    [int] $SuccessProbeIntervalSec = 30,
    [int] $SuccessProbeMinElapsedSec = 0,
    [int64] $MaxCapturedOutputBytes = 52428800,
    [Parameter(Mandatory=$true)] [string] $HighCostWorkspaceRoot,
    [string] $HighCostAdmissionPath = '',
    [string] $HighCostBudgetToolPath = '',
    [Parameter(Mandatory=$true)] [string] $HighCostPythonExe,
    [Parameter(Mandatory=$true)] [string] $HighCostCallId,
    [string] $HighCostCallReceiptPath = '',
    [string[]] $ExtraArgs = @()
)

$ErrorActionPreference = 'Stop'
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::InputEncoding = [System.Text.Encoding]::UTF8

function Add-WrapperLog {
    param([string] $Text)
    $protected = $Text
    if ($env:USERPROFILE -and $protected) {
        $protected = [regex]::Replace(
            $protected,
            [regex]::Escape($env:USERPROFILE),
            '<USERPROFILE>',
            [System.Text.RegularExpressions.RegexOptions]::IgnoreCase
        )
    }
    try {
        Add-Content -Path $LogFile -Value ("[run_codex_with_timeout] " + $protected) -Encoding UTF8
    } catch {
        Write-Host "[run_codex_with_timeout] FATAL: cannot write wrapper log: $($_.Exception.GetType().Name)"
        exit 125
    }
}

function Assert-CanonicalModelBroker {
    foreach ($requiredPath in @($HighCostWorkspaceRoot, $HighCostPythonExe)) {
        if (-not (Test-Path -LiteralPath $requiredPath)) {
            Add-WrapperLog "HIGH_COST_MODEL_CALL_ADMISSION_REQUIRED missing=$([IO.Path]::GetFileName($requiredPath))"
            exit 126
        }
    }
    $expectedInstalledBroker = [System.IO.Path]::GetFullPath((Join-Path $env:USERPROFILE 'bin\ai-model-spawn-broker.py'))
    $modelSpawnBroker = if ($HighCostBudgetToolPath) { [System.IO.Path]::GetFullPath($HighCostBudgetToolPath) } else { $expectedInstalledBroker }
    $routeRegistry = Join-Path $HighCostWorkspaceRoot 'docs\harness\high_cost_model_routes_v1.json'
    if ((-not $modelSpawnBroker.Equals($expectedInstalledBroker, [System.StringComparison]::OrdinalIgnoreCase)) -or (-not (Test-Path -LiteralPath $modelSpawnBroker -PathType Leaf)) -or (-not (Test-Path -LiteralPath $routeRegistry -PathType Leaf))) {
        Add-WrapperLog 'MODEL_SPAWN_BROKER_UNAVAILABLE'
        exit 126
    }
    if ([string]::IsNullOrWhiteSpace($HighCostCallId) -or [string]::IsNullOrWhiteSpace($FlowName)) {
        Add-WrapperLog 'HIGH_COST_MODEL_CALL_ID_INVALID'
        exit 126
    }
}

if (-not (Test-Path -LiteralPath $CodexExe)) {
    Add-WrapperLog "CodexExe not found: $CodexExe"
    exit 125
}
$codexExtension = [System.IO.Path]::GetExtension($CodexExe).ToLowerInvariant()
if ($codexExtension -in @('.cmd', '.bat')) {
    Add-WrapperLog "unsupported CodexExe extension: $codexExtension; use .exe or .ps1"
    exit 125
}
if (-not (Test-Path -LiteralPath $PromptFile)) {
    Add-WrapperLog "PromptFile not found: $PromptFile"
    exit 125
}
if (-not $WorkingDirectory) {
    $WorkingDirectory = (Get-Location).Path
}
if (-not (Test-Path -LiteralPath $WorkingDirectory)) {
    Add-WrapperLog "WorkingDirectory not found: $WorkingDirectory"
    exit 125
}

$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
$promptText = Get-Content -LiteralPath $PromptFile -Raw -Encoding UTF8
if ([string]::IsNullOrWhiteSpace($promptText)) {
    Add-WrapperLog "prompt text is empty after load"
    exit 125
}

Add-WrapperLog "wrapper ALIVE: pid=$PID CodexExe=$([IO.Path]::GetFileName($CodexExe)) PromptFile=$([IO.Path]::GetFileName($PromptFile)) TimeoutSec=$TimeoutSec IdleTimeoutSec=$IdleTimeoutSec WorkingDirectory=$([IO.Path]::GetFileName($WorkingDirectory))"

$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("news-grasp-codex-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null
$stdinFile = Join-Path $tempRoot "stdin.txt"
$stdoutFile = Join-Path $tempRoot "stdout.txt"
$stderrFile = Join-Path $tempRoot "stderr.txt"
[System.IO.File]::WriteAllText($stdinFile, $promptText, $utf8NoBom)
[System.IO.File]::WriteAllText($stdoutFile, "", $utf8NoBom)
[System.IO.File]::WriteAllText($stderrFile, "", $utf8NoBom)

function Protect-LogText {
    param([string] $Text)
    if (-not $Text) { return $Text }
    $protected = $Text
    if ($env:USERPROFILE) {
        $protected = [regex]::Replace(
            $protected,
            [regex]::Escape($env:USERPROFILE),
            '<USERPROFILE>',
            [System.Text.RegularExpressions.RegexOptions]::IgnoreCase
        )
    }
    return $protected
}

function Add-NewFileBytesToLog {
    param(
        [string] $Path,
        [ref] $Offset
    )
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    $fs = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
    try {
        if ($fs.Length -le [int64]$Offset.Value) { return $false }
        [void]$fs.Seek([int64]$Offset.Value, [System.IO.SeekOrigin]::Begin)
        $available = $fs.Length - [int64]$Offset.Value
        $count = [int][Math]::Min($available, 1048576)
        $buffer = New-Object byte[] $count
        $bytesRead = $fs.Read($buffer, 0, $count)
        if ($bytesRead -le 0) { return $false }
        if ($bytesRead -lt $count) {
            $buffer = $buffer[0..($bytesRead - 1)]
        }
        $newLen = [int64]$Offset.Value + $bytesRead
        $text = Protect-LogText -Text ([System.Text.Encoding]::UTF8.GetString($buffer))
        if ($text) {
            try {
                Add-Content -Path $LogFile -Value $text -Encoding UTF8
            } catch {
                return $false
            }
        }
        $Offset.Value = $newLen
        return [bool]$text
    } finally {
        $fs.Close()
    }
}

function ConvertTo-ProcessArgumentString {
    param([string[]] $Arguments)
    $escapedArgs = @()
    foreach ($arg in $Arguments) {
        if ($arg -notmatch '[\s"]') {
            $escapedArgs += $arg
            continue
        }
        $escapedArgs += ('"' + ($arg -replace '(\\*)"', '$1$1\"' -replace '(\\+)$', '$1$1') + '"')
    }
    return ($escapedArgs -join ' ')
}

function Get-TokensUsedFromText {
    param([string] $Text)
    if (-not $Text) { return $null }
    $m = [regex]::Match($Text, 'tokens used\s*\r?\n\s*([0-9][0-9,]*)', [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
    if (-not $m.Success) { return $null }
    $raw = $m.Groups[1].Value -replace ',', ''
    try { return [int64]$raw } catch { return $null }
}

function Test-CodexQuotaText {
    param([string] $Text)
    if (-not $Text) { return $false }
    return (
        $Text -match "You've hit your usage limit" -or
        $Text -match 'purchase more credits' -or
        $Text -match 'try again at [0-9]{1,2}:[0-9]{2}\s*(AM|PM)'
    )
}

function Normalize-CodexExitCode {
    param(
        [int] $ExitCode,
        [string] $StdoutPath,
        [string] $StderrPath
    )
    # 成功したcodex出力には、prompt・memory・調査説明としてquota文言が現れ得る。
    # OS exit 0を文字列scanだけで外部障害へ上書きしてはならない。
    if ($ExitCode -eq 0) { return 0 }
    if ($ExitCode -eq 123) { return 123 }
    $stdoutText = ''
    $stderrText = ''
    try { if (Test-Path -LiteralPath $StdoutPath) { $stdoutText = Get-Content -LiteralPath $StdoutPath -Raw -Encoding UTF8 } } catch { }
    try { if (Test-Path -LiteralPath $StderrPath) { $stderrText = Get-Content -LiteralPath $StderrPath -Raw -Encoding UTF8 } } catch { }
    if ($ExitCode -ne 0 -and (Test-CodexQuotaText -Text ($stdoutText + "`n" + $stderrText))) {
        Add-WrapperLog "codex quota detected; normalizing rc=$ExitCode to rc=123"
        return 123
    }
    return $ExitCode
}

function Stop-ProcessTree {
    param([int] $ProcessId)
    $children = @()
    try {
        $children = @(Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq $ProcessId })
    } catch {
        $children = @()
    }
    $stoppedIds = @()
    foreach ($child in $children) {
        $stoppedIds += @(Stop-ProcessTree -ProcessId ([int]$child.ProcessId))
    }
    try { Stop-Process -Id $ProcessId -Force -ErrorAction Stop } catch { }
    return @($stoppedIds + $ProcessId)
}

function Wait-ProcessTreeExit {
    param(
        [int[]] $ProcessIds,
        [int] $TimeoutMilliseconds = 5000
    )
    $deadline = [DateTime]::UtcNow.AddMilliseconds($TimeoutMilliseconds)
    do {
        $alive = @($ProcessIds | Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue })
        if ($alive.Count -eq 0) { return $true }
        Start-Sleep -Milliseconds 100
    } while ([DateTime]::UtcNow -lt $deadline)
    return $false
}

function Invoke-SuccessProbe {
    param(
        [string] $Command,
        [string] $WorkingDirectory,
        [string] $TempRoot
    )
    if (-not $Command) { return $false }
    $probeStdout = Join-Path $TempRoot "success-probe-stdout.txt"
    $probeStderr = Join-Path $TempRoot "success-probe-stderr.txt"
    $probeArgs = ConvertTo-ProcessArgumentString -Arguments @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $Command)
    try {
        $probe = Start-Process -FilePath "powershell.exe" -ArgumentList $probeArgs -WorkingDirectory $WorkingDirectory -RedirectStandardOutput $probeStdout -RedirectStandardError $probeStderr -WindowStyle Hidden -Wait -PassThru
        if ($probe.ExitCode -eq 0) { return $true }
    } catch {
        Add-WrapperLog "WARN: success probe failed to execute: $($_.Exception.Message)"
    }
    return $false
}

function Write-UsageRecord {
    param(
        [int] $ExitCode,
        [string] $StdoutPath,
        [string] $StderrPath
    )
    if (-not $UsageLog) { return }
    $flow = $FlowName
    if (-not $flow) { $flow = "unknown" }
    $stdoutText = ''
    $stderrText = ''
    try { if (Test-Path -LiteralPath $StdoutPath) { $stdoutText = Get-Content -LiteralPath $StdoutPath -Raw -Encoding UTF8 } } catch { }
    try { if (Test-Path -LiteralPath $StderrPath) { $stderrText = Get-Content -LiteralPath $StderrPath -Raw -Encoding UTF8 } } catch { }
    $tokens = Get-TokensUsedFromText -Text ($stdoutText + "`n" + $stderrText)
    $record = [ordered]@{
        timestamp = (Get-Date -Format 'yyyy-MM-ddTHH:mm:ss.fffK')
        flow = $flow
        model = $Model
        reasoning_effort = $ReasoningEffort
        prompt_file = [IO.Path]::GetFileName($PromptFile)
        output_schema = [IO.Path]::GetFileName($OutputSchema)
        output_last_message = [IO.Path]::GetFileName($OutputLastMessage)
        exit_code = $ExitCode
        tokens_used = $tokens
    }
    $line = $record | ConvertTo-Json -Compress
    try {
        $parent = Split-Path -Parent $UsageLog
        if ($parent -and -not (Test-Path -LiteralPath $parent)) {
            New-Item -ItemType Directory -Path $parent -Force | Out-Null
        }
        Add-Content -Path $UsageLog -Value $line -Encoding UTF8
        Add-WrapperLog "usage recorded: flow=$flow tokens=$tokens usage_log=$([IO.Path]::GetFileName($UsageLog))"
    } catch {
        Add-WrapperLog "WARN: usage record write failed: $($_.Exception.Message)"
    }
}

$argList = @("exec", "-C", ".")
if ($Model) {
    $argList += @("--model", $Model)
}
if ($ReasoningEffort) {
    $argList += @("-c", "model_reasoning_effort=`"$ReasoningEffort`"")
}
if ($OutputSchema) {
    $argList += @("--output-schema", $OutputSchema)
}
if ($OutputLastMessage) {
    $argList += @("--output-last-message", $OutputLastMessage)
}
if ($ExtraArgs) {
    $argList += $ExtraArgs
}

Assert-CanonicalModelBroker

try {
    $oldPythonIoEncoding = [Environment]::GetEnvironmentVariable("PYTHONIOENCODING", "Process")
    $oldPythonUtf8 = [Environment]::GetEnvironmentVariable("PYTHONUTF8", "Process")
    $oldCodexNoninteractiveSession = [Environment]::GetEnvironmentVariable("CODEX_NONINTERACTIVE_SESSION", "Process")
    $oldCodexOutputContract = [Environment]::GetEnvironmentVariable("CODEX_OUTPUT_CONTRACT", "Process")
    [Environment]::SetEnvironmentVariable("PYTHONIOENCODING", "utf-8:backslashreplace", "Process")
    [Environment]::SetEnvironmentVariable("PYTHONUTF8", "1", "Process")
    [Environment]::SetEnvironmentVariable("CODEX_NONINTERACTIVE_SESSION", "1", "Process")
    [Environment]::SetEnvironmentVariable("CODEX_OUTPUT_CONTRACT", "artifact-gate", "Process")
    try {
        # MODEL_SPAWN_BROKER_V2: model processはcanonical brokerだけが生成する。
        $modelExecutable = $CodexExe
        $modelArgs = $argList
        if ($CodexExe.ToLowerInvariant().EndsWith(".ps1")) {
            $modelExecutable = "powershell.exe"
            $modelArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $CodexExe) + $argList
        }
        $modelSpawnBroker = [System.IO.Path]::GetFullPath($HighCostBudgetToolPath)
        if (-not (Test-Path -LiteralPath $modelSpawnBroker -PathType Leaf)) {
            throw 'MODEL_SPAWN_BROKER_UNAVAILABLE'
        }
        $filePath = $HighCostPythonExe
        $effectiveArgs = @($modelSpawnBroker, 'exec', '--route', $FlowName, '--call-id', $HighCostCallId, '--executable', $modelExecutable, '--') + $modelArgs
        $effectiveArgString = ConvertTo-ProcessArgumentString -Arguments $effectiveArgs
        $proc = Start-Process -FilePath $filePath -ArgumentList $effectiveArgString -WorkingDirectory $WorkingDirectory -RedirectStandardInput $stdinFile -RedirectStandardOutput $stdoutFile -RedirectStandardError $stderrFile -WindowStyle Hidden -PassThru
    } finally {
        [Environment]::SetEnvironmentVariable("PYTHONIOENCODING", $oldPythonIoEncoding, "Process")
        [Environment]::SetEnvironmentVariable("PYTHONUTF8", $oldPythonUtf8, "Process")
        [Environment]::SetEnvironmentVariable("CODEX_NONINTERACTIVE_SESSION", $oldCodexNoninteractiveSession, "Process")
        [Environment]::SetEnvironmentVariable("CODEX_OUTPUT_CONTRACT", $oldCodexOutputContract, "Process")
    }
    $null = $proc.Handle
    Add-WrapperLog "codex exec started: pid=$($proc.Id) file=$([IO.Path]::GetFileName($filePath)) arg_count=$($effectiveArgs.Count) flow=$FlowName"

    $startUtc = [DateTime]::UtcNow
    $lastOutputUtc = $startUtc
    $nextHeartbeatUtc = $startUtc.AddSeconds([Math]::Max(1, $HeartbeatSec))
    $nextSuccessProbeUtc = $startUtc.AddSeconds([Math]::Max(1, $SuccessProbeIntervalSec))
    $stdoutOffset = [int64]0
    $stderrOffset = [int64]0
    while (-not $proc.HasExited) {
        Start-Sleep -Seconds 1
        $capturedBytes = (Get-Item -LiteralPath $stdoutFile).Length + (Get-Item -LiteralPath $stderrFile).Length
        if ($MaxCapturedOutputBytes -gt 0 -and $capturedBytes -gt $MaxCapturedOutputBytes) {
            Add-WrapperLog "OUTPUT LIMIT exceeded: captured=$capturedBytes limit=$MaxCapturedOutputBytes; killing PID $($proc.Id)"
            [void](Stop-ProcessTree -ProcessId $proc.Id)
            exit 125
        }
        $hadStdout = Add-NewFileBytesToLog -Path $stdoutFile -Offset ([ref]$stdoutOffset)
        $hadStderr = Add-NewFileBytesToLog -Path $stderrFile -Offset ([ref]$stderrOffset)
        $nowUtc = [DateTime]::UtcNow
        if ($hadStdout -or $hadStderr) {
            $lastOutputUtc = $nowUtc
        }
        $elapsedSec = [int]($nowUtc - $startUtc).TotalSeconds
        $idleSec = [int]($nowUtc - $lastOutputUtc).TotalSeconds

        if ($HeartbeatSec -gt 0 -and $nowUtc -ge $nextHeartbeatUtc) {
            try {
                Add-WrapperLog "heartbeat: elapsed=$elapsedSec idle=$idleSec"
            } catch { }
            $nextHeartbeatUtc = $nowUtc.AddSeconds($HeartbeatSec)
        }
        if ($SuccessProbeCommand -and $elapsedSec -ge $SuccessProbeMinElapsedSec -and $nowUtc -ge $nextSuccessProbeUtc) {
            $nextSuccessProbeUtc = $nowUtc.AddSeconds([Math]::Max(1, $SuccessProbeIntervalSec))
            if (Invoke-SuccessProbe -Command $SuccessProbeCommand -WorkingDirectory $WorkingDirectory -TempRoot $tempRoot) {
                Add-WrapperLog "success probe passed; killing PID $($proc.Id) and returning rc=0"
                $stoppedProcessIds = @(Stop-ProcessTree -ProcessId $proc.Id)
                if (-not (Wait-ProcessTreeExit -ProcessIds $stoppedProcessIds)) {
                    Add-WrapperLog "process tree did not stop after success probe; returning rc=125"
                    exit 125
                }
                Add-WrapperLog "process tree confirmed stopped after success probe"
                if (-not (Invoke-SuccessProbe -Command $SuccessProbeCommand -WorkingDirectory $WorkingDirectory -TempRoot $tempRoot)) {
                    Add-WrapperLog "success probe changed after child stop; returning rc=125"
                    exit 125
                }
                [void](Add-NewFileBytesToLog -Path $stdoutFile -Offset ([ref]$stdoutOffset))
                [void](Add-NewFileBytesToLog -Path $stderrFile -Offset ([ref]$stderrOffset))
                Write-UsageRecord -ExitCode 0 -StdoutPath $stdoutFile -StderrPath $stderrFile
                exit 0
            }
        }
        if ($TimeoutSec -gt 0 -and $elapsedSec -ge $TimeoutSec) {
            try { Add-WrapperLog "TIMEOUT after $TimeoutSec sec, killing PID $($proc.Id)" } catch { }
            [void](Stop-ProcessTree -ProcessId $proc.Id)
            exit 124
        }
        if ($IdleTimeoutSec -gt 0 -and $idleSec -ge $IdleTimeoutSec) {
            try { Add-WrapperLog "IDLE TIMEOUT after $IdleTimeoutSec sec, killing PID $($proc.Id)" } catch { }
            [void](Stop-ProcessTree -ProcessId $proc.Id)
            exit 124
        }
    }

    $capturedBytes = (Get-Item -LiteralPath $stdoutFile).Length + (Get-Item -LiteralPath $stderrFile).Length
    if ($MaxCapturedOutputBytes -gt 0 -and $capturedBytes -gt $MaxCapturedOutputBytes) {
        Add-WrapperLog "OUTPUT LIMIT exceeded after exit: captured=$capturedBytes limit=$MaxCapturedOutputBytes"
        exit 125
    }
    [void](Add-NewFileBytesToLog -Path $stdoutFile -Offset ([ref]$stdoutOffset))
    [void](Add-NewFileBytesToLog -Path $stderrFile -Offset ([ref]$stderrOffset))
    $proc.WaitForExit()
    $rc = Normalize-CodexExitCode -ExitCode $proc.ExitCode -StdoutPath $stdoutFile -StderrPath $stderrFile
    Write-UsageRecord -ExitCode $rc -StdoutPath $stdoutFile -StderrPath $stderrFile
    Add-WrapperLog "codex exec exited: rc=$rc"
    exit $rc
} catch {
    Add-WrapperLog "FATAL: $($_.Exception.Message)"
    exit 125
} finally {
    try { Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue } catch { }
}
