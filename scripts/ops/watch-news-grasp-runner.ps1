[CmdletBinding(DefaultParameterSetName = 'Start')]
param(
    [Parameter(ParameterSetName = 'Start')]
    [switch] $Start,

    [Parameter(ParameterSetName = 'StartOnly')]
    [switch] $StartOnly,

    [Parameter(ParameterSetName = 'Status')]
    [switch] $Status,

    [switch] $SmokeTest,
    [switch] $SkipSourceSync,
    [switch] $RecoverOnly,
    [int] $PollSeconds = 30,
    [int] $StaleMinutes = 15,
    [int] $TimeoutMinutes = 120,
    [string] $RunnerPath = (Join-Path $env:USERPROFILE 'bin\news-grasp-runner.ps1'),
    [string] $StateFile = (Join-Path $env:USERPROFILE 'bin\news-grasp-runner-state.json'),
    [string] $LogDir = (Join-Path $env:USERPROFILE 'bin\news-grasp-logs'),
    [string] $DateStamp = (Get-Date -Format 'yyyy-MM-dd'),
    [string] $RepoDir = '',
    [string] $PyExeOverride = '',
    [string] $BinDir = '',
    [string] $HighCostAdmissionPath = $env:NEWS_GRASP_HIGH_COST_ADMISSION_PATH,
    [string] $HighCostBudgetToolPath = $env:NEWS_GRASP_HIGH_COST_BUDGET_TOOL_PATH,
    [string] $HighCostWorkspaceRoot = $env:NEWS_GRASP_HIGH_COST_WORKSPACE_ROOT,
    [string] $RecoveryDecisionPath = ''
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

if (-not $BinDir) {
    $BinDir = Split-Path -Parent $RunnerPath
}
$RepoDir = Resolve-NewsGraspRepoDir -Override $RepoDir
$BootstrapLog = Join-Path $LogDir "bootstrap-$DateStamp.log"
$script:RunnerJobHandles = @{}

if (-not ("NewsGraspRunnerJob" -as [type])) {
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Text;

public static class NewsGraspRunnerJob
{
    private const uint JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000;
    private const uint CREATE_SUSPENDED = 0x00000004;
    private const uint CREATE_NO_WINDOW = 0x08000000;
    private const uint EXTENDED_STARTUPINFO_PRESENT = 0x00080000;
    private const uint PROC_THREAD_ATTRIBUTE_JOB_LIST = 0x0002000D;

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

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct STARTUPINFO
    {
        public int cb;
        public string lpReserved, lpDesktop, lpTitle;
        public uint dwX, dwY, dwXSize, dwYSize, dwXCountChars, dwYCountChars;
        public uint dwFillAttribute;
        public uint dwFlags;
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
    private static extern bool InitializeProcThreadAttributeList(IntPtr list, int count, int flags, ref IntPtr size);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool UpdateProcThreadAttribute(IntPtr list, uint flags, IntPtr attribute, IntPtr value, IntPtr size, IntPtr previous, IntPtr returnSize);
    [DllImport("kernel32.dll")]
    private static extern void DeleteProcThreadAttributeList(IntPtr list);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool CreateProcess(string applicationName, StringBuilder commandLine, IntPtr processAttributes, IntPtr threadAttributes, bool inheritHandles, uint creationFlags, IntPtr environment, string currentDirectory, ref STARTUPINFOEX startupInfo, out PROCESS_INFORMATION processInfo);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern uint ResumeThread(IntPtr thread);
    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool CloseHandle(IntPtr handle);

    public static OwnedLaunch CreateSuspendedJobProcess(string applicationPath, string arguments, string workingDirectory)
    {
        IntPtr job = IntPtr.Zero, attributeList = IntPtr.Zero, jobHandleList = IntPtr.Zero;
        PROCESS_INFORMATION processInfo = new PROCESS_INFORMATION();
        try {
            job = CreateJobObject(IntPtr.Zero, null);
            if (job == IntPtr.Zero) {
                throw new InvalidOperationException("RUNNER_JOB_CREATE_FAILED:" + Marshal.GetLastWin32Error());
            }
            JOBOBJECT_EXTENDED_LIMIT_INFORMATION info = new JOBOBJECT_EXTENDED_LIMIT_INFORMATION();
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            if (!SetInformationJobObject(job, 9, ref info, (uint)Marshal.SizeOf(info))) {
                throw new InvalidOperationException("RUNNER_JOB_CONFIGURATION_FAILED:" + Marshal.GetLastWin32Error());
            }
            IntPtr attributeSize = IntPtr.Zero;
            InitializeProcThreadAttributeList(IntPtr.Zero, 1, 0, ref attributeSize);
            attributeList = Marshal.AllocHGlobal(attributeSize);
            if (!InitializeProcThreadAttributeList(attributeList, 1, 0, ref attributeSize)) {
                throw new InvalidOperationException("RUNNER_ATTRIBUTE_INIT_FAILED:" + Marshal.GetLastWin32Error());
            }
            jobHandleList = Marshal.AllocHGlobal(IntPtr.Size);
            Marshal.WriteIntPtr(jobHandleList, job);
            if (!UpdateProcThreadAttribute(attributeList, 0, new IntPtr(PROC_THREAD_ATTRIBUTE_JOB_LIST), jobHandleList, new IntPtr(IntPtr.Size), IntPtr.Zero, IntPtr.Zero)) {
                throw new InvalidOperationException("RUNNER_JOB_ATTRIBUTE_FAILED:" + Marshal.GetLastWin32Error());
            }
            STARTUPINFOEX startup = new STARTUPINFOEX();
            startup.StartupInfo.cb = Marshal.SizeOf(startup);
            startup.lpAttributeList = attributeList;
            string command = "\"" + applicationPath.Replace("\"", "\\\"") + "\"" + (String.IsNullOrWhiteSpace(arguments) ? "" : " " + arguments);
            if (!CreateProcess(applicationPath, new StringBuilder(command), IntPtr.Zero, IntPtr.Zero, false, CREATE_SUSPENDED | CREATE_NO_WINDOW | EXTENDED_STARTUPINFO_PRESENT, IntPtr.Zero, workingDirectory, ref startup, out processInfo)) {
                throw new InvalidOperationException("RUNNER_PROCESS_CREATE_FAILED:" + Marshal.GetLastWin32Error());
            }
            if (ResumeThread(processInfo.hThread) == UInt32.MaxValue) {
                throw new InvalidOperationException("RUNNER_PROCESS_RESUME_FAILED:" + Marshal.GetLastWin32Error());
            }
            OwnedLaunch result = new OwnedLaunch { ProcessId = (int)processInfo.dwProcessId, JobHandle = job };
            job = IntPtr.Zero;
            return result;
        }
        finally {
            if (processInfo.hThread != IntPtr.Zero) { CloseHandle(processInfo.hThread); }
            if (processInfo.hProcess != IntPtr.Zero) { CloseHandle(processInfo.hProcess); }
            if (job != IntPtr.Zero) { CloseHandle(job); }
            if (attributeList != IntPtr.Zero) { DeleteProcThreadAttributeList(attributeList); Marshal.FreeHGlobal(attributeList); }
            if (jobHandleList != IntPtr.Zero) { Marshal.FreeHGlobal(jobHandleList); }
        }
    }

    public static void CloseOwnedJob(IntPtr job)
    {
        if (job != IntPtr.Zero && !CloseHandle(job)) {
            throw new InvalidOperationException("RUNNER_JOB_CLOSE_FAILED:" + Marshal.GetLastWin32Error());
        }
    }
}
"@
}

function Get-LogPath {
    return Join-Path $LogDir "$DateStamp.log"
}

function Write-BootstrapLog {
    param([string] $Message)
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-ddTHH:mm:ss.fffK'), $Message
    Add-Content -LiteralPath $BootstrapLog -Value $line -Encoding UTF8
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

function Get-CommandLineFingerprint {
    param([string] $CommandLine)
    return Get-StringSha256Hex -Text ([string]$CommandLine).Trim().ToLowerInvariant()
}

function Get-FileSha256Hex {
    param([string] $Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return ''
    }
    try {
        if (Get-Command Get-FileHash -ErrorAction SilentlyContinue) {
            return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    } catch {
        Write-BootstrapLog "WARN: Get-FileHash failed path=$Path reason=$($_.Exception.Message)"
    }
    try {
        $stream = [System.IO.File]::OpenRead($Path)
        try {
            $sha = [System.Security.Cryptography.SHA256]::Create()
            try {
                $bytes = $sha.ComputeHash($stream)
                return ([System.BitConverter]::ToString($bytes) -replace '-', '').ToLowerInvariant()
            } finally {
                $sha.Dispose()
            }
        } finally {
            $stream.Dispose()
        }
    } catch {
        Write-BootstrapLog "ERROR: sha256 calculation failed path=$Path reason=$($_.Exception.Message)"
        return ''
    }
}

function Repair-LiveOpsFromRepo {
    $opsDir = Join-Path $RepoDir 'scripts\ops'
    if (-not (Test-Path -LiteralPath $opsDir)) {
        throw "repo ops directory not found: $opsDir"
    }
    New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
    $files = @(
        'run_codex_with_timeout.ps1',
        'news-grasp-bootstrap.ps1',
        'news-grasp-runner.ps1',
        'news-grasp-lineage.ps1',
        'watch-news-grasp-runner.ps1',
        'news-grasp-deadman.ps1',
        'news-grasp-deadman-launcher.pyw',
        'news-grasp-task-launcher.pyw'
    )
    $timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $backupDir = Join-Path $RepoDir "build\live-runner-self-repair\$timestamp"
    $manifestPath = Join-Path $backupDir 'auto-repair-manifest.json'
    $manifestFiles = @()
    $changed = $false

    foreach ($file in $files) {
        $source = Join-Path $opsDir $file
        $destination = Join-Path $BinDir $file
        if (-not (Test-Path -LiteralPath $source)) {
            throw "repo ops script missing: $source"
        }
        $sourceHash = Get-FileSha256Hex -Path $source
        $beforeHash = Get-FileSha256Hex -Path $destination
        $backup = Join-Path $backupDir $file
        $status = 'unchanged'
        if (-not $sourceHash) {
            throw "repo ops script hash unavailable: $source"
        }
        if ($sourceHash -ne $beforeHash) {
            New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
            if (Test-Path -LiteralPath $destination) {
                Copy-Item -LiteralPath $destination -Destination $backup -Force
            }
            Copy-Item -LiteralPath $source -Destination $destination -Force
            $changed = $true
            $status = 'repaired'
            Write-BootstrapLog "live ops repaired file=$file before=$beforeHash source=$sourceHash"
        }
        $afterHash = Get-FileSha256Hex -Path $destination
        $manifestFiles += [ordered]@{
            file = $file
            source = $source
            destination = $destination
            backup = if (Test-Path -LiteralPath $backup) { $backup } else { '' }
            before_sha256 = $beforeHash
            source_sha256 = $sourceHash
            after_sha256 = $afterHash
            status = $status
        }
    }

    if ($changed) {
        [ordered]@{
            created_at = (Get-Date).ToString('yyyy-MM-ddTHH:mm:ss.fffK')
            repo_dir = $RepoDir
            bin_dir = $BinDir
            backup_dir = $backupDir
            files = $manifestFiles
        } | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
        Write-BootstrapLog "live ops self-repair manifest=$manifestPath"
    } else {
        Write-BootstrapLog 'live ops already in sync'
    }
}

function Read-State {
    if (-not (Test-Path $StateFile)) {
        return $null
    }
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        try {
            return Get-Content -LiteralPath $StateFile -Raw -Encoding UTF8 | ConvertFrom-Json
        } catch {
            if ($attempt -lt 3) {
                Start-Sleep -Milliseconds 100
            }
        }
    }
    $stamp = Get-Date -Format 'yyyyMMddHHmmss'
    $corrupt = "$StateFile.corrupt.$stamp.json"
    try { Copy-Item -LiteralPath $StateFile -Destination $corrupt -Force -ErrorAction SilentlyContinue } catch { }
    return [pscustomobject]@{ __corrupt = $true; corrupt_backup = $corrupt }
}

function Write-StateAtomic {
    param(
        [string] $Path,
        [object] $Payload
    )
    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    $tmp = "$Path.tmp.$PID.$([guid]::NewGuid().ToString('N'))"
    $backup = "$Path.bak"
    $json = ($Payload | ConvertTo-Json -Depth 8) + "`n"
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

function Get-RunnerProcessAlive {
    param($State)
    if (-not $State -or -not $State.pid) {
        return $false
    }
    try {
        $null = Get-Process -Id ([int]$State.pid) -ErrorAction Stop
        return $true
    } catch {
        return $false
    }
}

function Get-StateTime {
    param(
        $State,
        [string] $Name
    )
    if (-not $State -or -not $State.$Name) { return $null }
    try { return [datetime]::Parse([string]$State.$Name) } catch { return $null }
}

function Get-StaleSeconds {
    param($State)
    $now = Get-Date
    $t = Get-StateTime -State $State -Name 'heartbeat_at'
    if (-not $t) { $t = Get-StateTime -State $State -Name 'updated_at' }
    if ($t) { return [int]($now - $t).TotalSeconds }
    $logPath = Get-LogPath
    if (Test-Path $logPath) {
        return [int]($now - (Get-Item -LiteralPath $logPath).LastWriteTime).TotalSeconds
    }
    return 0
}

function Write-WatchdogState {
    param(
        $State,
        [string] $Status,
        [string] $Message,
        [int] $ExitCode
    )
    $now = (Get-Date).ToString('yyyy-MM-ddTHH:mm:ss.fffK')
    $payload = [ordered]@{
        status = $Status
        message = $Message
        exit_code = $ExitCode
        updated_at = $now
        heartbeat_at = $now
        run_id = if ($State -and $State.run_id) { [string]$State.run_id } else { '' }
        pid = if ($State -and $State.pid) { [int]$State.pid } else { -1 }
        repo_dir = if ($State -and $State.repo_dir) { [string]$State.repo_dir } else { '' }
        runner_path = if ($State -and $State.runner_path) { [string]$State.runner_path } else { $RunnerPath }
        log_path = Get-LogPath
        last_observed_status = if ($State -and $State.status) { [string]$State.status } else { 'unknown' }
        last_observed_phase = if ($State -and $State.phase) { [string]$State.phase } else { '' }
        process_creation_time = if ($State -and $State.process_creation_time) { [string]$State.process_creation_time } else { '' }
        command_line_fingerprint = if ($State -and $State.command_line_fingerprint) { [string]$State.command_line_fingerprint } else { '' }
    }
    if ($State -and $State.corrupt_backup) {
        $payload.corrupt_backup = [string]$State.corrupt_backup
    }
    Write-StateAtomic -Path $StateFile -Payload $payload
}

function Test-RunnerProcessIdentity {
    param($State)
    if (-not $State -or -not $State.pid -or -not $State.run_id -or -not $State.runner_path -or -not $State.process_creation_time -or -not $State.command_line_fingerprint) {
        return $false
    }
    try {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$State.pid)" -ErrorAction Stop
    } catch {
        return $false
    }
    if (-not $proc -or -not $proc.CommandLine) { return $false }
    if ([string]$State.repo_dir -and -not ([string]$State.repo_dir).Equals((Split-Path -Parent (Split-Path -Parent $RunnerPath)), [System.StringComparison]::OrdinalIgnoreCase)) {
        # RepoDirOverride 実行では repo_dir 照合が厳密にできないため command line と runner path を優先する。
    }
    if ($proc.CommandLine -notlike "*$([string]$State.runner_path)*") { return $false }
    $fingerprint = Get-CommandLineFingerprint -CommandLine ([string]$proc.CommandLine)
    if ($fingerprint -ne [string]$State.command_line_fingerprint) { return $false }
    try {
        $expected = [datetime]::Parse([string]$State.process_creation_time)
        $actual = [datetime]$proc.CreationDate
        if ([math]::Abs(($actual - $expected).TotalSeconds) -gt 2) { return $false }
    } catch {
        return $false
    }
    return $true
}

function Test-StateBelongsToRunnerProcess {
    param(
        $State,
        [System.Diagnostics.Process] $Process
    )
    if (
        -not $State -or
        -not $State.pid -or
        -not $State.process_creation_time -or
        -not $State.runner_path -or
        [int]$State.pid -ne $Process.Id
    ) {
        return $false
    }
    if (-not ([string]$State.runner_path).Equals($RunnerPath, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $false
    }
    try {
        $expectedCreation = [datetime]::Parse([string]$State.process_creation_time)
        $actualCreation = $Process.StartTime
        return [math]::Abs(($actualCreation - $expectedCreation).TotalSeconds) -le 2
    } catch {
        return $false
    }
}

function Stop-VerifiedRunner {
    param(
        $State,
        [string] $Status,
        [string] $Message
    )
    if (Test-RunnerProcessIdentity -State $State) {
        $ownedPid = [int]$State.pid
        if (-not $script:RunnerJobHandles.ContainsKey($ownedPid)) {
            Write-WatchdogState -State $State -Status 'watchdog_stale_unconfirmed' -Message $Message -ExitCode 125
            return 125
        }
        $handle = [IntPtr]$script:RunnerJobHandles[$ownedPid]
        [NewsGraspRunnerJob]::CloseOwnedJob($handle)
        $script:RunnerJobHandles.Remove($ownedPid)
        Write-WatchdogState -State $State -Status $Status -Message $Message -ExitCode 124
        return 124
    }
    Write-WatchdogState -State $State -Status 'watchdog_stale_unconfirmed' -Message $Message -ExitCode 125
    return 125
}

function Write-StatusJson {
    param(
        [string] $Mode,
        [object] $State,
        [int] $ProcessId = -1,
        [string] $Message = ''
    )
    $logPath = Get-LogPath
    $logUpdatedAt = $null
    if (Test-Path $logPath) {
        $logUpdatedAt = (Get-Item -LiteralPath $logPath).LastWriteTime.ToString('yyyy-MM-ddTHH:mm:ss.fffK')
    }
    $alive = Get-RunnerProcessAlive -State $State
    $status = if ($State -and $State.status) { [string]$State.status } else { 'unknown' }
    $messageText = if ($Message) { $Message } elseif ($State -and $State.message) { [string]$State.message } else { '' }
    if ($status -eq 'running' -and -not $alive) {
        $status = 'stale'
        if (-not $Message) {
            $messageText = 'runner state is running but process is not alive'
        }
    }
    $out = [ordered]@{
        mode = $Mode
        status = $status
        message = $messageText
        pid = if ($ProcessId -ge 0) { $ProcessId } elseif ($State -and $State.pid) { [int]$State.pid } else { -1 }
        process_alive = $alive
        state_file = $StateFile
        log_path = $logPath
        log_updated_at = $logUpdatedAt
        updated_at = if ($State -and $State.updated_at) { [string]$State.updated_at } else { $null }
        run_id = if ($State -and $State.run_id) { [string]$State.run_id } else { $null }
        phase = if ($State -and $State.phase) { [string]$State.phase } else { $null }
        heartbeat_at = if ($State -and $State.heartbeat_at) { [string]$State.heartbeat_at } else { $null }
        deadline_at = if ($State -and $State.deadline_at) { [string]$State.deadline_at } else { $null }
        stale_seconds = Get-StaleSeconds -State $State
    }
    $out | ConvertTo-Json -Depth 4
}

function Write-StartedJson {
    param([System.Diagnostics.Process] $Process)
    $logPath = Get-LogPath
    $out = [ordered]@{
        mode = 'started'
        status = 'started'
        message = 'runner process started'
        pid = $Process.Id
        process_alive = -not $Process.HasExited
        state_file = $StateFile
        log_path = $logPath
        log_updated_at = if (Test-Path $logPath) { (Get-Item -LiteralPath $logPath).LastWriteTime.ToString('yyyy-MM-ddTHH:mm:ss.fffK') } else { $null }
        updated_at = (Get-Date).ToString('yyyy-MM-ddTHH:mm:ss.fffK')
    }
    $out | ConvertTo-Json -Depth 4
}

function Start-RunnerProcess {
    param([object] $RecoveryDecision = $null)
    if (-not (Test-Path $RunnerPath)) {
        throw "runner not found: $RunnerPath"
    }
    $args = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $RunnerPath)
    if ($SmokeTest) {
        $args += '-SmokeTest'
    }
    if ($SkipSourceSync) {
        $args += '-SkipSourceSync'
    }
    if ($RecoverOnly) {
        $args += '-RecoverOnly'
    }
    $args += @('-DateStampOverride', $DateStamp)
    $args += @('-LogDirOverride', $LogDir)
    $args += @('-StateFileOverride', $StateFile)
    $args += @('-RepoDirOverride', $RepoDir)
    if ($PyExeOverride) { $args += @('-PyExeOverride', $PyExeOverride) }
    if ($HighCostAdmissionPath) {
        $args += @('-HighCostAdmissionPath', $HighCostAdmissionPath)
    }
    if ($HighCostBudgetToolPath) {
        $args += @('-HighCostBudgetToolPath', $HighCostBudgetToolPath)
    }
    if ($HighCostWorkspaceRoot) {
        $args += @('-HighCostWorkspaceRoot', $HighCostWorkspaceRoot)
    }
    if ($RecoveryDecision) {
        $args += @('-RunIntent', 'ScheduledRecoveryFull')
        $args += @('-ScheduledAuthorityEvidencePath', [string]$RecoveryDecision.scheduledAuthorityEvidencePath)
        if ([string]$RecoveryDecision.recoveryBranch -eq 'ResumeFromStage') {
            $args += @('-ResumeFromStage', [string]$RecoveryDecision.resumeStage)
            $args += @('-HighCostAdmissionPath', [string]$RecoveryDecision.sourceAdmissionPath)
        }
        $args += @('-RecoveryDecisionPath', [string]$RecoveryDecision.decisionPath)
    }
    $runnerArguments = @($args | Select-Object -Skip 5)
    $quote = {
        param([string]$Value)
        return "'" + $Value.Replace("'", "''") + "'"
    }
    $runnerLiteral = & $quote $RunnerPath
    $argumentLiterals = @($runnerArguments | ForEach-Object { & $quote ([string]$_) }) -join ', '
    $childCommand = "& $runnerLiteral @($argumentLiterals); exit `$LASTEXITCODE"
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($childCommand))
    $powershellExe = (Resolve-Path -LiteralPath (Join-Path $PSHOME 'powershell.exe')).Path
    $nativeArguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand $encoded"
    $launch = [NewsGraspRunnerJob]::CreateSuspendedJobProcess($powershellExe, $nativeArguments, $RepoDir)
    $script:RunnerJobHandles[[int]$launch.ProcessId] = [IntPtr]$launch.JobHandle
    return Get-Process -Id ([int]$launch.ProcessId) -ErrorAction Stop
}

function Get-DailyControlPython {
    if ($PyExeOverride -and (Test-Path -LiteralPath $PyExeOverride -PathType Leaf)) { return $PyExeOverride }
    $candidate = Join-Path $RepoDir '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    return 'python'
}

function Get-RecoveryDecision {
    param(
        [string] $Trigger,
        [int] $ProcessExitCode,
        [int] $RecoveryAttemptNumber = 0
    )
    $python = Get-DailyControlPython
    Push-Location $RepoDir
    try {
        $json = (& $python '-m' 'tools.news_grasp_daily_control' 'prepare' '--issue-date' $DateStamp '--trigger' $Trigger '--process-exit-code' $ProcessExitCode '--recovery-attempt-number' $RecoveryAttemptNumber 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -ne 0) {
            Write-BootstrapLog "daily control failed trigger=$Trigger exit=$LASTEXITCODE detail=$json"
            return $null
        }
        return $json | ConvertFrom-Json -ErrorAction Stop
    } finally {
        Pop-Location
    }
}

function Read-ValidatedRecoveryDecision {
    param([string] $Path)
    $python = Get-DailyControlPython
    Push-Location $RepoDir
    try {
        $json = (& $python '-m' 'tools.news_grasp_daily_control' 'validate-decision' '--path' $Path 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -ne 0) { throw "recovery decision invalid: $json" }
        return $json | ConvertFrom-Json -ErrorAction Stop
    } finally {
        Pop-Location
    }
}

function Start-RecoveryFromDecision {
    param([Parameter(Mandatory=$true)][object] $Decision)
    if ([string]$Decision.action -ne 'launch_recovery' -or [int]$Decision.maxAutomaticRecoveryAttempts -ne 1) {
        throw "typed recovery launch not admitted: action=$($Decision.action)"
    }
    return Start-RunnerProcess -RecoveryDecision $Decision
}

function Test-TerminalState {
    param($State)
    if (-not $State -or -not $State.status) {
        return $false
    }
    return @('publish_complete', 'smoke_ok') -contains [string]$State.status
}

function Watch-Runner {
    param([System.Diagnostics.Process] $Process)
    $started = Get-Date
    $script:WatchExitCode = 1
    $watchRunId = ''
    $stateDirectory = Split-Path -Parent $StateFile
    New-Item -ItemType Directory -Force -Path $stateDirectory | Out-Null
    $stateSignal = [System.Threading.AutoResetEvent]::new($true)
    $exitSignal = [System.Threading.AutoResetEvent]::new($false)
    $stateWatcher = [System.IO.FileSystemWatcher]::new($stateDirectory, (Split-Path -Leaf $StateFile))
    $stateWatcher.NotifyFilter = [System.IO.NotifyFilters]'FileName, LastWrite, Size'
    $stateChanged = [System.IO.FileSystemEventHandler]{ param($sender, $eventArgs) $null = $stateSignal.Set() }
    $stateRenamed = [System.IO.RenamedEventHandler]{ param($sender, $eventArgs) $null = $stateSignal.Set() }
    $processExited = [System.EventHandler]{ param($sender, $eventArgs) $null = $exitSignal.Set() }
    $stateWatcher.add_Changed($stateChanged)
    $stateWatcher.add_Created($stateChanged)
    $stateWatcher.add_Renamed($stateRenamed)
    $Process.EnableRaisingEvents = $true
    $Process.add_Exited($processExited)
    $stateWatcher.EnableRaisingEvents = $true
    $nextDeadline = $started.AddMinutes($TimeoutMinutes)
    try {
      while ($true) {
        $waitMilliseconds = [Math]::Max(1, [Math]::Min([int](($nextDeadline - (Get-Date)).TotalMilliseconds), [int]::MaxValue))
        $null = [System.Threading.WaitHandle]::WaitAny(@($exitSignal, $stateSignal), $waitMilliseconds)
        $state = Read-State
        if ($state -and $state.__corrupt) {
            Write-WatchdogState -State $state -Status 'watchdog_state_corrupt' -Message 'runner state json is corrupt' -ExitCode 125
            Write-StatusJson -Mode 'state_corrupt' -State (Read-State) -ProcessId $Process.Id -Message 'runner state json is corrupt'
            $script:WatchExitCode = 125
            return
        }
        $stateBoundToProcess = Test-StateBelongsToRunnerProcess -State $state -Process $Process
        if (-not $stateBoundToProcess) {
            if ($Process.HasExited) {
                Write-StatusJson -Mode 'failed' -State $state -ProcessId $Process.Id -Message 'runner process exited before claiming a fresh state identity'
                $script:WatchExitCode = 1
                return
            }
            $startupElapsed = (Get-Date) - $started
            if ($startupElapsed.TotalMinutes -ge $TimeoutMinutes) {
                $message = "runner did not claim a fresh state identity within $TimeoutMinutes minutes"
                Write-WatchdogState -State $state -Status 'watchdog_startup_identity_timeout' -Message $message -ExitCode 125
                Write-StatusJson -Mode 'startup_identity_timeout' -State (Read-State) -ProcessId $Process.Id -Message $message
                $script:WatchExitCode = 125
                return
            }
            $nextDeadline = $started.AddMinutes($TimeoutMinutes)
            continue
        }
        if (-not $watchRunId -and $state -and $state.run_id) {
            $watchRunId = [string]$state.run_id
        }
        if (Test-TerminalState -State $state) {
            if (-not $Process.HasExited) { $null = $Process.WaitForExit(30000) }
            Write-StatusJson -Mode 'completed' -State $state -ProcessId $Process.Id
            $script:WatchExitCode = 0
            return
        }
        if ($state -and [string]$state.status -eq 'error' -and $Process.HasExited) {
            Write-StatusJson -Mode 'failed' -State $state -ProcessId $Process.Id
            $script:WatchExitCode = 1
            return
        }
        if ($Process.HasExited) {
            Write-StatusJson -Mode 'failed' -State $state -ProcessId $Process.Id -Message "runner process exited without publish_complete marker"
            $script:WatchExitCode = 1
            return
        }
        $staleSeconds = Get-StaleSeconds -State $state
        if ($staleSeconds -ge ($StaleMinutes * 60)) {
            $minutes = [int]($staleSeconds / 60)
            $message = "log has not changed for $minutes minutes"
            if ($watchRunId -and $state -and $state.run_id -and [string]$state.run_id -ne $watchRunId) {
                Write-WatchdogState -State $state -Status 'watchdog_stale_unconfirmed' -Message $message -ExitCode 125
                Write-StatusJson -Mode 'stale_unconfirmed' -State (Read-State) -ProcessId $Process.Id -Message $message
                $script:WatchExitCode = 125
                return
            }
            $script:WatchExitCode = Stop-VerifiedRunner -State $state -Status 'watchdog_stale_timeout' -Message $message
            Write-StatusJson -Mode 'stale' -State (Read-State) -ProcessId $Process.Id -Message $message
            return
        }
        $elapsed = (Get-Date) - $started
        if ($elapsed.TotalMinutes -ge $TimeoutMinutes) {
            $message = "watch timeout after $TimeoutMinutes minutes"
            $script:WatchExitCode = Stop-VerifiedRunner -State $state -Status 'watchdog_wall_timeout' -Message $message
            Write-StatusJson -Mode 'timeout' -State (Read-State) -ProcessId $Process.Id -Message $message
            return
        }
        $staleAt = (Get-Date).AddSeconds([Math]::Max(1, ($StaleMinutes * 60) - $staleSeconds))
        $wallAt = $started.AddMinutes($TimeoutMinutes)
        $nextDeadline = if ($staleAt -lt $wallAt) { $staleAt } else { $wallAt }
      }
    } finally {
        $stateWatcher.EnableRaisingEvents = $false
        $stateWatcher.remove_Changed($stateChanged)
        $stateWatcher.remove_Created($stateChanged)
        $stateWatcher.remove_Renamed($stateRenamed)
        $Process.remove_Exited($processExited)
        $stateWatcher.Dispose()
        $stateSignal.Dispose()
        $exitSignal.Dispose()
        if ($script:RunnerJobHandles.ContainsKey([int]$Process.Id)) {
            [NewsGraspRunnerJob]::CloseOwnedJob([IntPtr]$script:RunnerJobHandles[[int]$Process.Id])
            $script:RunnerJobHandles.Remove([int]$Process.Id)
        }
    }
}

if ($PSCmdlet.ParameterSetName -eq 'Status') {
    Write-StatusJson -Mode 'status' -State (Read-State)
    exit 0
}

Repair-LiveOpsFromRepo
$decision = $null
if ($RecoveryDecisionPath) {
    $decision = Read-ValidatedRecoveryDecision -Path $RecoveryDecisionPath
}
$proc = if ($decision) { Start-RecoveryFromDecision -Decision $decision } else { Start-RunnerProcess }
if ($PSCmdlet.ParameterSetName -eq 'StartOnly') {
    Write-StartedJson -Process $proc
    exit 0
}

Watch-Runner -Process $proc
if ($script:WatchExitCode -ne 0 -and (-not $decision) -and (-not $SmokeTest) -and (-not $RecoverOnly)) {
    $decision = Get-RecoveryDecision -Trigger 'production_failure' -ProcessExitCode $script:WatchExitCode
    if ($decision -and [string]$decision.action -eq 'launch_recovery') {
        $recoveryProcess = Start-RecoveryFromDecision -Decision $decision
        Watch-Runner -Process $recoveryProcess
        if ($script:WatchExitCode -ne 0) {
            $null = Get-RecoveryDecision -Trigger 'production_failure' -ProcessExitCode $script:WatchExitCode -RecoveryAttemptNumber 1
        }
    }
}
exit $script:WatchExitCode
