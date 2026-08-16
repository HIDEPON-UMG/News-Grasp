param(
    [string] $RepoDir = '',
    [string] $PythonExe = '',
    [Parameter(Mandatory = $true)][string] $IssueDate,
    [Parameter(Mandatory = $true)][string] $GenerationManifestPath,
    [Parameter(Mandatory = $true)][string] $StableTaskAuthorityPath,
    [Parameter(Mandatory = $true)][string] $RuntimeBindingPath,
    [Parameter(Mandatory = $true)][string] $TaskActionPath,
    [Parameter(Mandatory = $true)][string] $DescriptorPath,
    [Parameter(Mandatory = $true)][string] $DeadmanPath,
    [Parameter(Mandatory = $true)][string] $ActiveCapsulePath,
    [Parameter(Mandatory = $true)][string] $StandbyCapsulePath,
    [Parameter(Mandatory = $true)][string] $OutputPath
)

$ErrorActionPreference = 'Stop'
$OutputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)

if (-not $RepoDir) { $RepoDir = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path }
if (-not $PythonExe) { $PythonExe = 'py' }
$tool = Join-Path $RepoDir 'tools\news_grasp_generation.py'
if (-not (Test-Path -LiteralPath $tool -PathType Leaf)) { throw 'NG_GENERATION_TOOL_MISSING' }
foreach ($path in @($GenerationManifestPath, $StableTaskAuthorityPath, $RuntimeBindingPath, $TaskActionPath, $DescriptorPath, $DeadmanPath, $ActiveCapsulePath, $StandbyCapsulePath)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "NG_RUN_ENVELOPE_INPUT_MISSING:$path" }
}

$arguments = @(
    '-I', '-S', '-B', $tool, 'seal-envelope',
    '--issue-date', $IssueDate,
    '--generation-manifest', $GenerationManifestPath,
    '--stable-task-authority', $StableTaskAuthorityPath,
    '--runtime-binding', $RuntimeBindingPath,
    '--task-action-json', $TaskActionPath,
    '--descriptor', $DescriptorPath,
    '--deadman', $DeadmanPath,
    '--active-capsule', $ActiveCapsulePath,
    '--standby-capsule', $StandbyCapsulePath,
    '--output', $OutputPath
)
$result = (& $PythonExe @arguments 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { throw "NG_RUN_ENVELOPE_SEAL_FAILED:$result" }
if (-not (Test-Path -LiteralPath $OutputPath -PathType Leaf)) { throw 'NG_RUN_ENVELOPE_OUTPUT_MISSING' }
try {
    $envelope = Get-Content -LiteralPath $OutputPath -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
} catch { throw 'NG_RUN_ENVELOPE_OUTPUT_INVALID' }
if ([string]$envelope.schemaVersion -cne 'RUN_ENVELOPE_V1' -or -not $envelope.envelopeSha256) { throw 'NG_RUN_ENVELOPE_OUTPUT_INVALID' }
Write-Output ($envelope | ConvertTo-Json -Depth 20 -Compress)
