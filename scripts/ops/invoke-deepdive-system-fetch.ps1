[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $Url,

    [Parameter(Mandatory = $true)]
    [string] $BodyPath,

    [ValidateRange(1, 4194304)]
    [int] $MaxBytes = 4194304,

    [ValidateRange(1, 120)]
    [int] $TimeoutSec = 20
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)

$uri = [Uri]$Url
if (-not $uri.IsAbsoluteUri -or $uri.Scheme -notin @('http', 'https')) {
    throw 'DEEPDIVE_SYSTEM_FETCH_URL_INVALID'
}

$resolvedBody = ''
$response = $null
$source = $null
$destination = $null
$client = $null
$handler = $null
$cancellation = $null
$completed = $false
try {
    $bodyItem = Get-Item -LiteralPath $BodyPath -ErrorAction Stop
    if (
        -not $bodyItem.PSIsContainer -and
        [long]$bodyItem.Length -eq 0 -and
        -not ([bool]($bodyItem.Attributes -band [IO.FileAttributes]::ReparsePoint))
    ) {
        $resolvedBody = $bodyItem.FullName
    } else {
        throw 'DEEPDIVE_SYSTEM_FETCH_BODY_PATH_INVALID'
    }
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\', '/')
    $bodyParent = [IO.Path]::GetFullPath($bodyItem.Directory.FullName).TrimEnd('\', '/')
    if (
        -not [string]::Equals($bodyParent, $tempRoot, [StringComparison]::OrdinalIgnoreCase) -or
        $bodyItem.Name -notmatch '^news-grasp-provenance-[A-Za-z0-9._-]+\.body$'
    ) {
        throw 'DEEPDIVE_SYSTEM_FETCH_BODY_PATH_INVALID'
    }
    $cursor = $bodyItem.Directory
    while ($null -ne $cursor) {
        if ([bool]($cursor.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'DEEPDIVE_SYSTEM_FETCH_BODY_PATH_INVALID'
        }
        $cursor = $cursor.Parent
    }

    Add-Type -AssemblyName System.Net.Http
    $handler = [Net.Http.HttpClientHandler]::new()
    # redirect先をPython側で再検証できないtransportでは追跡しない。
    $handler.AllowAutoRedirect = $false
    $client = [Net.Http.HttpClient]::new($handler, $true)
    $client.DefaultRequestHeaders.Accept.ParseAdd(
        'text/html, application/xhtml+xml, application/pdf;q=0.9, */*;q=0.8'
    )
    $cancellation = [Threading.CancellationTokenSource]::new(
        [TimeSpan]::FromSeconds($TimeoutSec)
    )
    $response = $client.GetAsync(
        $uri,
        [Net.Http.HttpCompletionOption]::ResponseHeadersRead,
        $cancellation.Token
    ).GetAwaiter().GetResult()
    $contentLength = $response.Content.Headers.ContentLength
    if ($null -ne $contentLength -and [long]$contentLength -gt $MaxBytes) {
        throw 'DEEPDIVE_SYSTEM_FETCH_BODY_LIMIT_EXCEEDED'
    }
    $source = $response.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
    $destination = [IO.File]::Open(
        $resolvedBody,
        [IO.FileMode]::Open,
        [IO.FileAccess]::Write,
        [IO.FileShare]::None
    )
    $destination.SetLength(0)
    $buffer = [byte[]]::new(65536)
    [long]$written = 0
    while ($true) {
        $read = $source.ReadAsync(
            $buffer,
            0,
            $buffer.Length,
            $cancellation.Token
        ).GetAwaiter().GetResult()
        if ($read -le 0) { break }
        if ($written + $read -gt $MaxBytes) {
            throw 'DEEPDIVE_SYSTEM_FETCH_BODY_LIMIT_EXCEEDED'
        }
        $destination.Write($buffer, 0, $read)
        $written += $read
    }
    $destination.Flush($true)
    $finalUri = [string]$response.RequestMessage.RequestUri.AbsoluteUri
    $completed = $true
    [ordered]@{
        schemaVersion = 'DEEPDIVE_SYSTEM_FETCH_RESULT_V1'
        httpStatus = [int]$response.StatusCode
        finalUrl = $finalUri
        bytes = $written
        transport = 'windows_system_http'
    } | ConvertTo-Json -Compress
} catch {
    throw "DEEPDIVE_SYSTEM_FETCH_FAILED line=$($_.InvocationInfo.ScriptLineNumber) $($_.Exception.Message)"
} finally {
    if ($null -ne $destination) { $destination.Dispose() }
    if ($null -ne $source) { $source.Dispose() }
    if ($null -ne $response) { $response.Dispose() }
    if ($null -ne $client) { $client.Dispose() }
    if ($null -ne $cancellation) { $cancellation.Dispose() }
    if (-not $completed -and $resolvedBody -and (Test-Path -LiteralPath $resolvedBody -PathType Leaf)) {
        [IO.File]::WriteAllBytes($resolvedBody, [byte[]]::new(0))
    }
}
