[CmdletBinding()]
param(
    [Alias('ArtifactRoot')][string] $CliArtifactRoot = '',
    [Alias('OpsRoot')][string] $CliOpsRoot = '',
    [Alias('IssueDate')][string] $CliIssueDate = '',
    [Alias('RunIntent')][string] $CliRunIntent = '',
    [Alias('RunId')][string] $CliRunId = ''
)

Set-StrictMode -Version Latest

function Get-NewsGraspLineageSha256 {
    param([Parameter(Mandatory = $true)][string] $Text)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($Text)
        return ([System.BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
}

function New-NewsGraspProducerLineage {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string] $ArtifactRoot,
        [Parameter(Mandatory = $true)][string] $OpsRoot,
        [Parameter(Mandatory = $true)][string] $IssueDate,
        [Parameter(Mandatory = $true)][string] $RunIntent,
        [Parameter(Mandatory = $true)][string] $RunId
    )
    $resolvedArtifactRoot = [System.IO.Path]::GetFullPath($ArtifactRoot).TrimEnd('\')
    $resolvedOpsRoot = [System.IO.Path]::GetFullPath($OpsRoot).TrimEnd('\')
    $dailyRootId = Get-NewsGraspLineageSha256 -Text (
        'News-Grasp|{0}|{1}|{2}' -f (
            $IssueDate,
            $resolvedArtifactRoot.ToLowerInvariant(),
            $resolvedOpsRoot.ToLowerInvariant()
        )
    )
    $rootOperationId = Get-NewsGraspLineageSha256 -Text (
        '{0}|{1}|root-operation' -f $dailyRootId, $RunId
    )
    $producerOperationId = Get-NewsGraspLineageSha256 -Text (
        '{0}|producer|{1}' -f $rootOperationId, $RunIntent
    )
    $lineageReceiptSha256 = Get-NewsGraspLineageSha256 -Text (
        'NEWS_GRASP_PRODUCER_LINEAGE_V1|{0}|{1}|{2}|{3}|{4}|{5}|{6}|{7}' -f (
            $IssueDate,
            $resolvedArtifactRoot.ToLowerInvariant(),
            $resolvedOpsRoot.ToLowerInvariant(),
            $dailyRootId,
            $rootOperationId,
            $producerOperationId,
            $RunIntent,
            $RunId
        )
    )
    return [ordered]@{
        artifactRoot = $resolvedArtifactRoot
        opsRoot = $resolvedOpsRoot
        dailyRootId = $dailyRootId
        rootOperationId = $rootOperationId
        producerOperationId = $producerOperationId
        producerRunIntent = $RunIntent
        lineageReceiptSha256 = $lineageReceiptSha256
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    if (-not $CliArtifactRoot -or -not $CliOpsRoot -or -not $CliIssueDate -or -not $CliRunIntent -or -not $CliRunId) {
        throw 'NEWS_GRASP_LINEAGE_ARGUMENT_REQUIRED'
    }
    New-NewsGraspProducerLineage `
        -ArtifactRoot $CliArtifactRoot `
        -OpsRoot $CliOpsRoot `
        -IssueDate $CliIssueDate `
        -RunIntent $CliRunIntent `
        -RunId $CliRunId |
        ConvertTo-Json -Depth 4 -Compress
}
