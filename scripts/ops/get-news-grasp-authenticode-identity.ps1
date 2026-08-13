param(
    [Parameter(Mandatory = $true)]
    [string] $TargetPath
)

$ErrorActionPreference = 'Stop'
$signature = Get-AuthenticodeSignature -LiteralPath $TargetPath
[ordered]@{
    status = [string]$signature.Status
    subject = [string]$signature.SignerCertificate.Subject
    thumbprint = ([string]$signature.SignerCertificate.Thumbprint).ToLowerInvariant()
} | ConvertTo-Json -Compress
