$ErrorActionPreference = 'Stop'

$TaskName = 'News-Grasp Title Materializer'
$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$PythonExe = (& py -3.12 -c 'import sys; print(sys.executable)').Trim()
if ([string]::IsNullOrWhiteSpace($PythonExe) -or -not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw 'NEWS_GRASP_TITLE_MATERIALIZER_PYTHON_NOT_FOUND'
}
$PythonW = Join-Path (Split-Path -Parent $PythonExe) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $PythonW -PathType Leaf)) {
    throw 'NEWS_GRASP_TITLE_MATERIALIZER_PYTHONW_NOT_FOUND'
}
$Entrypoint = Join-Path $RepoRoot 'scripts\ops\news-grasp-title-materializer.pyw'
if (-not (Test-Path -LiteralPath $Entrypoint -PathType Leaf)) {
    throw 'NEWS_GRASP_TITLE_MATERIALIZER_ENTRYPOINT_NOT_FOUND'
}

$action = New-ScheduledTaskAction `
    -Execute $PythonW `
    -Argument ('"{0}" --repo-root "{1}"' -f $Entrypoint, $RepoRoot) `
    -WorkingDirectory $RepoRoot
$trigger = New-ScheduledTaskTrigger -Daily -At ([datetime]::Today.AddHours(5).AddMinutes(59))
$principal = New-ScheduledTaskPrincipal `
    -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -Hidden `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 2) `
    -MultipleInstances IgnoreNew
$task = New-ScheduledTask `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "News-Grasp pre-run title materialization only; no article or publish work."
Register-ScheduledTask -TaskPath "\" -TaskName $TaskName -InputObject $task -Force | Out-Null

$registered = Get-ScheduledTask -TaskPath "\" -TaskName $TaskName -ErrorAction Stop
$registeredAction = @($registered.Actions | ForEach-Object { ([string]$_.Execute + " " + [string]$_.Arguments).Trim() }) -join " ; "
$registeredTrigger = @($registered.Triggers | ForEach-Object { [string]$_.StartBoundary }) -join " ; "
$result = [ordered]@{
    schemaVersion = "NEWS_GRASP_TITLE_MATERIALIZER_TASK_V1"
    ok = (
        [bool]$registered.Settings.Enabled -and
        [string]$registered.State -ne "Disabled" -and
        $registeredAction.ToLowerInvariant().Contains($PythonW.ToLowerInvariant()) -and
        $registeredAction.ToLowerInvariant().Contains($Entrypoint.ToLowerInvariant()) -and
        $registeredTrigger.Contains("T05:59:") -and
        [string]$registered.Settings.MultipleInstances -eq "IgnoreNew"
    )
    task_name = $TaskName
    task_path = [string]$registered.TaskPath
    state = [string]$registered.State
    enabled = [bool]$registered.Settings.Enabled
    action = $registeredAction
    trigger = $registeredTrigger
    multiple_instances = [string]$registered.Settings.MultipleInstances
}
$result | ConvertTo-Json -Depth 5
