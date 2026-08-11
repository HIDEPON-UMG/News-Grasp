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
    [string] $HighCostParentAuthorityPath = '',
    [string] $HighCostAttemptId = '',
    [Parameter(Mandatory=$true)] [string] $HighCostExpectedOperationKind,
    [string] $HighCostExpectedIssueDate = '',
    [string] $HighCostBudgetToolPath = '',
    [Parameter(Mandatory=$true)] [string] $HighCostPythonExe,
    [Parameter(Mandatory=$true)] [string] $HighCostCallId,
    [string] $HighCostCallReceiptPath = '',
    [string] $E2EFinalAdmissionPath = '',
    [string] $E2EFinalRunnerArgumentsPath = '',
    [string] $E2EFinalReservationReceiptPath = '',
    [string] $E2EFinalClaimReceiptPath = '',
    [string] $HighCostClaimWitness = '',
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

function Get-CanonicalFutureLeafPath {
    param(
        [Parameter(Mandatory=$true)][string] $ManagedRoot,
        [Parameter(Mandatory=$true)][string] $Candidate,
        [string] $ErrorCode = 'HIGH_COST_CANONICAL_FUTURE_PATH_INVALID'
    )
    try {
        $root = [System.IO.Path]::GetFullPath($ManagedRoot)
        $candidatePath = [System.IO.Path]::GetFullPath($Candidate)
    } catch {
        throw $ErrorCode
    }
    $rootKey = $root.TrimEnd('\') + '\'
    if (-not $candidatePath.StartsWith($rootKey, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw $ErrorCode
    }
    $leaf = Get-Item -LiteralPath $candidatePath -Force -ErrorAction SilentlyContinue
    if ($null -ne $leaf) {
        throw $ErrorCode
    }
    $cursor = Split-Path -Parent $candidatePath
    while ($cursor) {
        $existing = Get-Item -LiteralPath $cursor -Force -ErrorAction SilentlyContinue
        if ($null -ne $existing) {
            if (($existing.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
                (-not ($existing.PSIsContainer))) {
                throw $ErrorCode
            }
        }
        if ($cursor.TrimEnd('\').Equals($root.TrimEnd('\'), [System.StringComparison]::OrdinalIgnoreCase)) {
            break
        }
        $next = Split-Path -Parent $cursor
        if ($next -eq $cursor) { throw $ErrorCode }
        $cursor = $next
    }
    return $candidatePath
}

function Assert-CanonicalModelBroker {
    if ((-not (Test-Path -LiteralPath $HighCostWorkspaceRoot -PathType Container)) -or
        (-not (Test-Path -LiteralPath $HighCostPythonExe -PathType Leaf))) {
        Add-WrapperLog 'HIGH_COST_MODEL_CALL_ADMISSION_REQUIRED'
        exit 126
    }
    if ([string]::IsNullOrWhiteSpace($HighCostExpectedOperationKind)) {
        Add-WrapperLog 'HIGH_COST_OPERATION_ADMISSION_IDENTITY_REQUIRED'
        exit 126
    }
    if ($HighCostExpectedOperationKind -in @('scheduled_production', 'scheduled_recovery')) {
        if ($HighCostParentAuthorityPath) {
            Add-WrapperLog 'HIGH_COST_SCHEDULED_PARENT_AUTHORITY_FORBIDDEN'
            exit 126
        }
        if (-not (Test-Path -LiteralPath $HighCostAdmissionPath -PathType Leaf)) {
            Add-WrapperLog 'HIGH_COST_OPERATION_ADMISSION_REQUIRED'
            exit 126
        }
    } elseif ($HighCostExpectedOperationKind -eq 'full_e2e') {
        if ($HighCostAdmissionPath) {
            Add-WrapperLog 'HIGH_COST_FULL_E2E_SHARED_ADMISSION_FORBIDDEN'
            exit 126
        }
        if ($HighCostParentAuthorityPath) {
            if ((-not (Test-Path -LiteralPath $HighCostParentAuthorityPath -PathType Leaf)) -or
                [string]::IsNullOrWhiteSpace($HighCostAttemptId) -or
                [string]::IsNullOrWhiteSpace($HighCostCallReceiptPath)) {
                Add-WrapperLog 'HIGH_COST_PARENT_AUTHORITY_RECEIPT_REQUIRED'
                exit 126
            }
        } else {
            Add-WrapperLog 'HIGH_COST_PARENT_AUTHORITY_RECEIPT_REQUIRED'
            exit 126
        }
    }
    $expectedInstalledBroker = [System.IO.Path]::GetFullPath((Join-Path $env:USERPROFILE 'bin\ai-model-spawn-broker.py'))
    $modelSpawnBroker = if ($HighCostBudgetToolPath) { [System.IO.Path]::GetFullPath($HighCostBudgetToolPath) } else { $expectedInstalledBroker }
    $budgetValidator = [System.IO.Path]::GetFullPath((Join-Path $HighCostWorkspaceRoot 'tools\harness\high_cost_operation_budget.py'))
    if (-not (Test-Path -LiteralPath $budgetValidator -PathType Leaf)) {
        Add-WrapperLog 'HIGH_COST_OPERATION_BUDGET_VALIDATOR_UNAVAILABLE'
        exit 126
    }
    $script:CanonicalExecutionRoot = [System.IO.Path]::GetFullPath($WorkingDirectory)
    if (-not (Test-Path -LiteralPath $script:CanonicalExecutionRoot -PathType Container)) {
        Add-WrapperLog 'HIGH_COST_EXECUTION_ROOT_INVALID'
        exit 126
    }
    if ($HighCostExpectedOperationKind -eq 'full_e2e') {
        if (-not $E2EFinalAdmissionPath -or -not $E2EFinalRunnerArgumentsPath -or
            -not $E2EFinalReservationReceiptPath -or -not $E2EFinalClaimReceiptPath -or
            -not $HighCostClaimWitness -or [string]::IsNullOrWhiteSpace($HighCostCallReceiptPath)) {
            Add-WrapperLog 'HIGH_COST_FINAL_ADMISSION_PATHS_REQUIRED'
            exit 126
        }
        foreach ($path in @($E2EFinalAdmissionPath, $E2EFinalRunnerArgumentsPath, $E2EFinalReservationReceiptPath, $E2EFinalClaimReceiptPath)) {
            if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
                Add-WrapperLog 'HIGH_COST_FINAL_ADMISSION_PATHS_REQUIRED'
                exit 126
            }
        }
        if (-not (Test-Path -LiteralPath $E2EFinalClaimReceiptPath -PathType Leaf)) {
            Add-WrapperLog 'HIGH_COST_FINAL_CLAIM_RECEIPT_REQUIRED'
            exit 126
        }
        try {
            $callReceiptRoot = [System.IO.Path]::GetFullPath((Join-Path $script:CanonicalExecutionRoot 'build\high-cost-call-receipts'))
            $script:CanonicalHighCostCallReceiptPath = Get-CanonicalFutureLeafPath -ManagedRoot $script:CanonicalExecutionRoot -Candidate $HighCostCallReceiptPath -ErrorCode 'HIGH_COST_CANONICAL_FUTURE_OUTPUT_INVALID'
            $callRootKey = $callReceiptRoot.TrimEnd('\') + '\'
            if (-not $script:CanonicalHighCostCallReceiptPath.StartsWith($callRootKey, [System.StringComparison]::OrdinalIgnoreCase)) {
                throw 'HIGH_COST_CANONICAL_FUTURE_OUTPUT_INVALID'
            }
        } catch {
            Add-WrapperLog "HIGH_COST_CANONICAL_FUTURE_OUTPUT_INVALID reason=$($_.Exception.Message)"
            exit 126
        }
        $validatedParent = (& $HighCostPythonExe -I $budgetValidator 'validate-activated' '--workspace-root' $HighCostWorkspaceRoot '--admission' $HighCostParentAuthorityPath '--expected-attempt-kind' 'full_e2e' '--expected-execution-root' $script:CanonicalExecutionRoot 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -ne 0) {
            Add-WrapperLog "HIGH_COST_PARENT_AUTHORITY_INVALID exit=$LASTEXITCODE"
            exit 126
        }
    }
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
try {
    $WorkingDirectory = [System.IO.Path]::GetFullPath((Resolve-Path -LiteralPath $WorkingDirectory -ErrorAction Stop).Path)
} catch {
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
if ($SuccessProbeCommand) {
    Add-WrapperLog 'SUCCESS_PROBE_EARLY_TERMINATION_FORBIDDEN: broker-owned terminal only'
    exit 125
}

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
    if ($WorkingDirectory) {
        $protected = [regex]::Replace(
            $protected,
            [regex]::Escape([System.IO.Path]::GetFullPath($WorkingDirectory)),
            '<WORKING_DIRECTORY>',
            [System.Text.RegularExpressions.RegexOptions]::IgnoreCase
        )
    }
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

if (-not ("NewsGraspOwnedJob" -as [type])) {
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Text;

public static class NewsGraspOwnedJob
{
    public const uint JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000;
    private const uint CREATE_SUSPENDED = 0x00000004;
    private const uint CREATE_NO_WINDOW = 0x08000000;
    private const uint EXTENDED_STARTUPINFO_PRESENT = 0x00080000;
    private const uint STARTF_USESTDHANDLES = 0x00000100;
    private const uint PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002;
    private const uint GENERIC_READ = 0x80000000;
    private const uint GENERIC_WRITE = 0x40000000;
    private const uint FILE_SHARE_READ = 0x00000001;
    private const uint FILE_SHARE_WRITE = 0x00000002;
    private const uint FILE_SHARE_DELETE = 0x00000004;
    private const uint CREATE_ALWAYS = 2;
    private const uint OPEN_EXISTING = 3;
    private static readonly IntPtr INVALID_HANDLE_VALUE = new IntPtr(-1);

    public sealed class OwnedLaunch
    {
        public int ProcessId { get; set; }
        public IntPtr JobHandle { get; set; }
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct IO_COUNTERS
    {
        public UInt64 ReadOperationCount, WriteOperationCount, OtherOperationCount;
        public UInt64 ReadTransferCount, WriteTransferCount, OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct JOBOBJECT_BASIC_LIMIT_INFORMATION
    {
        public Int64 PerProcessUserTimeLimit, PerJobUserTimeLimit;
        public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize, MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass, SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION
    {
        public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
        public IO_COUNTERS IoInfo;
        public UIntPtr ProcessMemoryLimit, JobMemoryLimit, PeakProcessMemoryUsed, PeakJobMemoryUsed;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct SECURITY_ATTRIBUTES
    {
        public int nLength;
        public IntPtr lpSecurityDescriptor;
        [MarshalAs(UnmanagedType.Bool)] public bool bInheritHandle;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct STARTUPINFO
    {
        public int cb;
        public string lpReserved, lpDesktop, lpTitle;
        public uint dwX, dwY, dwXSize, dwYSize, dwXCountChars, dwYCountChars;
        public uint dwFillAttribute, dwFlags;
        public short wShowWindow, cbReserved2;
        public IntPtr lpReserved2, hStdInput, hStdOutput, hStdError;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct STARTUPINFOEX
    {
        public STARTUPINFO StartupInfo;
        public IntPtr lpAttributeList;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct PROCESS_INFORMATION
    {
        public IntPtr hProcess, hThread;
        public uint dwProcessId, dwThreadId;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CreateJobObject(IntPtr attributes, string name);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool SetInformationJobObject(IntPtr job, int infoClass, ref JOBOBJECT_EXTENDED_LIMIT_INFORMATION info, uint length);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool TerminateProcess(IntPtr process, uint exitCode);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool InitializeProcThreadAttributeList(IntPtr list, int count, int flags, ref IntPtr size);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool UpdateProcThreadAttribute(IntPtr list, uint flags, IntPtr attribute, IntPtr value, IntPtr size, IntPtr previous, IntPtr returnSize);
    [DllImport("kernel32.dll")]
    private static extern void DeleteProcThreadAttributeList(IntPtr list);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CreateFile(string path, uint access, uint share, ref SECURITY_ATTRIBUTES security, uint creation, uint flags, IntPtr template);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool CreateProcess(string applicationName, StringBuilder commandLine, IntPtr processAttributes, IntPtr threadAttributes, bool inheritHandles, uint creationFlags, IntPtr environment, string currentDirectory, ref STARTUPINFOEX startupInfo, out PROCESS_INFORMATION processInfo);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern uint ResumeThread(IntPtr thread);
    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool CloseHandle(IntPtr handle);

    public static OwnedLaunch CreateSuspendedAssignedProcess(string applicationPath, string arguments, string workingDirectory, string stdinPath, string stdoutPath, string stderrPath)
    {
        SECURITY_ATTRIBUTES security = new SECURITY_ATTRIBUTES();
        security.nLength = Marshal.SizeOf(security);
        security.bInheritHandle = true;
        IntPtr stdin = INVALID_HANDLE_VALUE, stdout = INVALID_HANDLE_VALUE, stderr = INVALID_HANDLE_VALUE;
        IntPtr attributeList = IntPtr.Zero, handleList = IntPtr.Zero, job = IntPtr.Zero;
        PROCESS_INFORMATION processInfo = new PROCESS_INFORMATION();
        bool processCreated = false;
        try {
            stdin = CreateFile(stdinPath, GENERIC_READ, FILE_SHARE_READ, ref security, OPEN_EXISTING, 0, IntPtr.Zero);
            stdout = CreateFile(stdoutPath, GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, ref security, CREATE_ALWAYS, 0, IntPtr.Zero);
            stderr = CreateFile(stderrPath, GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, ref security, CREATE_ALWAYS, 0, IntPtr.Zero);
            if (stdin == INVALID_HANDLE_VALUE || stdout == INVALID_HANDLE_VALUE || stderr == INVALID_HANDLE_VALUE) {
                throw new InvalidOperationException("OWNED_PROCESS_REDIRECTION_OPEN_FAILED:" + Marshal.GetLastWin32Error());
            }

            IntPtr attributeSize = IntPtr.Zero;
            InitializeProcThreadAttributeList(IntPtr.Zero, 1, 0, ref attributeSize);
            attributeList = Marshal.AllocHGlobal(attributeSize);
            if (!InitializeProcThreadAttributeList(attributeList, 1, 0, ref attributeSize)) {
                throw new InvalidOperationException("OWNED_PROCESS_ATTRIBUTE_INIT_FAILED:" + Marshal.GetLastWin32Error());
            }
            handleList = Marshal.AllocHGlobal(IntPtr.Size * 3);
            Marshal.WriteIntPtr(handleList, 0, stdin);
            Marshal.WriteIntPtr(handleList, IntPtr.Size, stdout);
            Marshal.WriteIntPtr(handleList, IntPtr.Size * 2, stderr);
            if (!UpdateProcThreadAttribute(attributeList, 0, new IntPtr(PROC_THREAD_ATTRIBUTE_HANDLE_LIST), handleList, new IntPtr(IntPtr.Size * 3), IntPtr.Zero, IntPtr.Zero)) {
                throw new InvalidOperationException("OWNED_PROCESS_ATTRIBUTE_UPDATE_FAILED:" + Marshal.GetLastWin32Error());
            }

            STARTUPINFOEX startup = new STARTUPINFOEX();
            startup.StartupInfo.cb = Marshal.SizeOf(startup);
            startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
            startup.StartupInfo.hStdInput = stdin;
            startup.StartupInfo.hStdOutput = stdout;
            startup.StartupInfo.hStdError = stderr;
            startup.lpAttributeList = attributeList;
            string command = "\"" + applicationPath.Replace("\"", "\\\"") + "\"" + (String.IsNullOrWhiteSpace(arguments) ? "" : " " + arguments);
            if (!CreateProcess(applicationPath, new StringBuilder(command), IntPtr.Zero, IntPtr.Zero, true, CREATE_SUSPENDED | CREATE_NO_WINDOW | EXTENDED_STARTUPINFO_PRESENT, IntPtr.Zero, workingDirectory, ref startup, out processInfo)) {
                throw new InvalidOperationException("OWNED_PROCESS_CREATE_FAILED:" + Marshal.GetLastWin32Error());
            }
            processCreated = true;

            job = CreateJobObject(IntPtr.Zero, null);
            if (job == IntPtr.Zero) {
                throw new InvalidOperationException("OWNED_PROCESS_JOB_CREATE_FAILED:" + Marshal.GetLastWin32Error());
            }
            JOBOBJECT_EXTENDED_LIMIT_INFORMATION info = new JOBOBJECT_EXTENDED_LIMIT_INFORMATION();
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            if (!SetInformationJobObject(job, 9, ref info, (uint)Marshal.SizeOf(info)) || !AssignProcessToJobObject(job, processInfo.hProcess)) {
                throw new InvalidOperationException("OWNED_PROCESS_JOB_ASSIGNMENT_FAILED:" + Marshal.GetLastWin32Error());
            }
            if (ResumeThread(processInfo.hThread) == UInt32.MaxValue) {
                throw new InvalidOperationException("OWNED_PROCESS_RESUME_FAILED:" + Marshal.GetLastWin32Error());
            }
            OwnedLaunch result = new OwnedLaunch { ProcessId = (int)processInfo.dwProcessId, JobHandle = job };
            job = IntPtr.Zero;
            return result;
        }
        catch {
            if (processCreated && processInfo.hProcess != IntPtr.Zero) { TerminateProcess(processInfo.hProcess, 125); }
            throw;
        }
        finally {
            if (processInfo.hThread != IntPtr.Zero) { CloseHandle(processInfo.hThread); }
            if (processInfo.hProcess != IntPtr.Zero) { CloseHandle(processInfo.hProcess); }
            if (job != IntPtr.Zero) { CloseHandle(job); }
            if (attributeList != IntPtr.Zero) { DeleteProcThreadAttributeList(attributeList); Marshal.FreeHGlobal(attributeList); }
            if (handleList != IntPtr.Zero) { Marshal.FreeHGlobal(handleList); }
            if (stdin != INVALID_HANDLE_VALUE) { CloseHandle(stdin); }
            if (stdout != INVALID_HANDLE_VALUE) { CloseHandle(stdout); }
            if (stderr != INVALID_HANDLE_VALUE) { CloseHandle(stderr); }
        }
    }

    public static void CloseOwnedJob(IntPtr job)
    {
        if (job != IntPtr.Zero && !CloseHandle(job)) {
            throw new InvalidOperationException("OWNED_PROCESS_JOB_CLOSE_FAILED:" + Marshal.GetLastWin32Error());
        }
    }
}
"@
}

$ownedJobHandle = [IntPtr]::Zero

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

if ($HighCostExpectedOperationKind -eq 'full_e2e') {
    try {
        $e2eAdmissionBridge = [System.IO.Path]::GetFullPath((Join-Path $script:CanonicalExecutionRoot 'tools\e2e_final_admission_bridge.py'))
        if (-not (Test-Path -LiteralPath $e2eAdmissionBridge -PathType Leaf)) { throw 'HIGH_COST_FINAL_ADMISSION_BRIDGE_UNAVAILABLE' }
        $canonicalClaimWitnessPath = [System.IO.Path]::GetFullPath($HighCostClaimWitness)
        if (-not (Test-Path -LiteralPath $canonicalClaimWitnessPath -PathType Leaf)) { throw 'HIGH_COST_FINAL_RUNNER_CLAIM_WITNESS_MISSING' }
        $claimWitnessOutput = (& $HighCostPythonExe -I $e2eAdmissionBridge 'validate-runner-claim-witness' '--admission' $E2EFinalAdmissionPath '--claim-witness' $canonicalClaimWitnessPath 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or $claimWitnessOutput.Length -gt 131072 -or [string]::IsNullOrWhiteSpace($claimWitnessOutput)) {
            throw 'HIGH_COST_FINAL_RUNNER_CLAIM_INVALID'
        }
        $claimWitness = $claimWitnessOutput | ConvertFrom-Json -ErrorAction Stop
        foreach ($field in @('claimId', 'claimReceiptPath', 'claimReceiptSha256', 'ownerProcessIdentity', 'attemptKey', 'admissionId')) {
            if ($null -eq $claimWitness.$field) { throw 'HIGH_COST_FINAL_RUNNER_CLAIM_WITNESS_INVALID' }
        }
        if ([System.IO.Path]::GetFullPath([string]$claimWitness.claimReceiptPath) -ne [System.IO.Path]::GetFullPath($E2EFinalClaimReceiptPath)) {
            throw 'HIGH_COST_FINAL_RUNNER_CLAIM_WITNESS_PATH_DRIFT'
        }
        $HighCostClaimWitness = $canonicalClaimWitnessPath
    } catch {
        Add-WrapperLog "HIGH_COST_FINAL_RUNNER_CLAIM_INVALID reason=$($_.Exception.Message)"
        exit 126
    }
}

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
        $operationAdmissionPath = $HighCostAdmissionPath
        if ($HighCostExpectedOperationKind -eq 'full_e2e') {
            $HighCostCallReceiptPath = $script:CanonicalHighCostCallReceiptPath
            if (Test-Path -LiteralPath $HighCostCallReceiptPath) {
                Add-WrapperLog 'HIGH_COST_CALL_RECEIPT_REUSE_FORBIDDEN'
                exit 126
            }
            try {
                $callReceiptParent = Split-Path -Parent ([System.IO.Path]::GetFullPath($HighCostCallReceiptPath))
                if ($callReceiptParent -and -not (Test-Path -LiteralPath $callReceiptParent -PathType Container)) {
                    New-Item -ItemType Directory -Path $callReceiptParent -Force | Out-Null
                }
                $null = Get-CanonicalFutureLeafPath -ManagedRoot $script:CanonicalExecutionRoot -Candidate $HighCostCallReceiptPath -ErrorCode 'HIGH_COST_CANONICAL_FUTURE_OUTPUT_INVALID'
                $admitArgs = @(
                    'admit',
                    '--operation-kind', 'full_e2e',
                    '--attempt-id', $HighCostAttemptId,
                    '--parent-operation-authority', $HighCostParentAuthorityPath,
                    '--execution-root', $script:CanonicalExecutionRoot,
                    '--route', $FlowName,
                    '--call-id', $HighCostCallId,
                    '--executable', $modelExecutable,
                    '--e2e-final-admission', $E2EFinalAdmissionPath,
                    '--e2e-final-runner-arguments-file', $E2EFinalRunnerArgumentsPath,
                    '--e2e-final-reservation-receipt', $E2EFinalReservationReceiptPath,
                    '--e2e-final-claim-receipt', $E2EFinalClaimReceiptPath,
                    '--e2e-final-claim-witness', $HighCostClaimWitness,
                    '--'
                ) + @($modelArgs)
                $childAdmissionOutput = & $HighCostPythonExe -I $modelSpawnBroker @admitArgs 2>&1
                $childAdmissionExitCode = $LASTEXITCODE
                $childAdmissionText = (@($childAdmissionOutput) | ForEach-Object { [string]$_ }) -join [Environment]::NewLine
                if ($childAdmissionExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($childAdmissionText)) {
                    Add-WrapperLog "HIGH_COST_CHILD_ADMISSION_REJECTED exit=$childAdmissionExitCode"
                    exit 126
                }
                $childAdmission = $childAdmissionText.Trim() | ConvertFrom-Json -ErrorAction Stop
                if (
                    [string]$childAdmission.schemaVersion -ne 'HIGH_COST_OPERATION_ADMISSION_V3' -or
                    [string]$childAdmission.operationKind -ne 'full_e2e' -or
                    [string]$childAdmission.attemptId -ne $HighCostAttemptId -or
                    [string]$childAdmission.route -ne $FlowName -or
                    [string]$childAdmission.executionRoot -ne [string]$script:CanonicalExecutionRoot -or
                    [System.IO.Path]::GetFullPath([string]$childAdmission.parentAuthorityPath) -ne [System.IO.Path]::GetFullPath($HighCostParentAuthorityPath) -or
                    [string]$childAdmission.callIdSha256 -notmatch '^[0-9a-f]{64}$' -or
                    [string]$childAdmission.commandSha256 -notmatch '^[0-9a-f]{64}$' -or
                    [string]$childAdmission.receiptPath -notmatch '^[^\r\n]+$' -or
                    (-not (Test-Path -LiteralPath ([string]$childAdmission.receiptPath) -PathType Leaf))
                ) {
                    Add-WrapperLog 'HIGH_COST_CHILD_ADMISSION_IDENTITY_MISMATCH'
                    exit 126
                }
                [System.IO.File]::WriteAllText($HighCostCallReceiptPath, ($childAdmissionText.Trim() + [Environment]::NewLine), $utf8NoBom)
                $operationAdmissionPath = [System.IO.Path]::GetFullPath([string]$childAdmission.receiptPath)
            } catch {
                Add-WrapperLog "HIGH_COST_CHILD_ADMISSION_INVALID reason=$($_.Exception.Message)"
                exit 126
            }
        }
        $effectiveArgs = @('-I', $modelSpawnBroker, 'exec', '--route', $FlowName, '--call-id', $HighCostCallId, '--operation-admission', $operationAdmissionPath, '--expected-operation-kind', $HighCostExpectedOperationKind, '--expected-issue-date', $HighCostExpectedIssueDate, '--execution-root', $script:CanonicalExecutionRoot)
        if ($TimeoutSec -gt 0) { $effectiveArgs += @('--timeout-seconds', [string]$TimeoutSec) }
        if ($IdleTimeoutSec -gt 0) { $effectiveArgs += @('--idle-timeout-seconds', [string]$IdleTimeoutSec) }
        if ($MaxCapturedOutputBytes -gt 0) { $effectiveArgs += @('--max-output-bytes', [string]$MaxCapturedOutputBytes) }
        $effectiveArgs += @('--executable', $modelExecutable)
        if ($HighCostExpectedOperationKind -eq 'full_e2e') {
            $effectiveArgs += @('--e2e-final-admission', $E2EFinalAdmissionPath, '--e2e-final-runner-arguments-file', $E2EFinalRunnerArgumentsPath, '--e2e-final-reservation-receipt', $E2EFinalReservationReceiptPath, '--e2e-final-claim-receipt', $E2EFinalClaimReceiptPath, '--e2e-final-claim-witness', $HighCostClaimWitness)
        }
        $effectiveArgs += @('--') + $modelArgs
        $effectiveArgString = ConvertTo-ProcessArgumentString -Arguments $effectiveArgs
        # brokerをsuspendedで生成し、専用Jobへ所属させてから初めて実行する。
        $ownedLaunch = [NewsGraspOwnedJob]::CreateSuspendedAssignedProcess($filePath, $effectiveArgString, $WorkingDirectory, $stdinFile, $stdoutFile, $stderrFile)
        $ownedJobHandle = $ownedLaunch.JobHandle
        $proc = Get-Process -Id $ownedLaunch.ProcessId -ErrorAction Stop
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

        if (
            (-not $proc.HasExited) -and
            $SuccessProbeCommand -and
            $elapsedSec -ge $SuccessProbeMinElapsedSec -and
            $nowUtc -ge $nextSuccessProbeUtc
        ) {
            if (Invoke-SuccessProbe -Command $SuccessProbeCommand -WorkingDirectory $WorkingDirectory -TempRoot $tempRoot) {
                Add-WrapperLog 'SUCCESS_PROBE_EARLY_TERMINATION_FORBIDDEN'
                exit 125
            }
            $nextSuccessProbeUtc = $nowUtc.AddSeconds([Math]::Max(1, $SuccessProbeIntervalSec))
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
    if ($ownedJobHandle -ne [IntPtr]::Zero) {
        try { [NewsGraspOwnedJob]::CloseOwnedJob($ownedJobHandle) } catch { }
        $ownedJobHandle = [IntPtr]::Zero
    }
    try { Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue } catch { }
}
