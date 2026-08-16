param(
    [string] $RepoDir = '',
    [string] $PythonExe = '',
    [Parameter(Mandatory = $true)][string] $ReceiptPath,
    [switch] $ConsumeOnly
)

$ErrorActionPreference = 'Stop'
$OutputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)

if (-not $RepoDir) { $RepoDir = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path }
if (-not $PythonExe) { $PythonExe = 'py' }
$tool = Join-Path $RepoDir 'tools\news_grasp_release_reflection.py'
if (-not (Test-Path -LiteralPath $tool -PathType Leaf)) { throw 'RELEASE_REFLECTION_TOOL_MISSING' }
if (-not (Test-Path -LiteralPath $ReceiptPath -PathType Leaf)) { throw 'RELEASE_REFLECTION_RECEIPT_MISSING' }

# L8/NoPublishは既存receiptのconsume-only検証だけを行う。push、publish、receipt再発行は禁止。
$result = (& $PythonExe '-I' '-S' '-B' $tool 'validate' '--receipt' $ReceiptPath 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { throw "RELEASE_REFLECTION_VALIDATION_FAILED:$result" }
try { $value = $result | ConvertFrom-Json -ErrorAction Stop } catch { throw 'RELEASE_REFLECTION_OUTPUT_INVALID' }
if ([string]$value.schemaVersion -cne 'NEWS_GRASP_RELEASE_REFLECTION_RECEIPT_V1' -or [int]$value.producerInvocationCount -ne 1) {
    throw 'RELEASE_REFLECTION_OUTPUT_INVALID'
}
if (-not $ConsumeOnly -and [string]$value.l8Mode -cne 'consume-only') { throw 'RELEASE_REFLECTION_L8_REISSUE_FORBIDDEN' }
Write-Output ($value | ConvertTo-Json -Depth 20 -Compress)
