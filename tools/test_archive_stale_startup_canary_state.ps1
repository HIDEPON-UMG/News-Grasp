# Regression test: Archive-StaleStartupCanaryState must not throw when $StateFile is '' or omitted.
# 2026-08-17 / 2026-08-18 の ScheduledProduction 起動失敗 (ParameterArgumentValidationErrorEmptyStringNotAllowed) の再発防止。
$ErrorActionPreference = 'Stop'
$bootstrapPath = Join-Path $PSScriptRoot '..\scripts\ops\news-grasp-bootstrap.ps1'
$tokens = $null; $parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($bootstrapPath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count -gt 0) { throw "PARSE_ERROR: $bootstrapPath" }
$funcAst = $ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Archive-StaleStartupCanaryState' }, $true) | Select-Object -First 1
if (-not $funcAst) { throw "FUNCTION_NOT_FOUND: Archive-StaleStartupCanaryState" }
. ([ScriptBlock]::Create($funcAst.Extent.Text))

$testRoot = Join-Path $env:TEMP ("ng-canary-test-" + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $testRoot | Out-Null
$failed = $false
try {
    try {
        Archive-StaleStartupCanaryState -StateFile '' -ExpectedRoot $testRoot
        Write-Host "PASS: StateFile='' returns without throwing"
    } catch {
        Write-Host "FAIL: StateFile='' threw: $($_.Exception.Message)"
        $failed = $true
    }
    try {
        Archive-StaleStartupCanaryState -ExpectedRoot $testRoot
        Write-Host "PASS: StateFile omitted returns without throwing"
    } catch {
        Write-Host "FAIL: StateFile omitted threw: $($_.Exception.Message)"
        $failed = $true
    }
} finally {
    Remove-Item -LiteralPath $testRoot -Recurse -Force -ErrorAction SilentlyContinue
}
if ($failed) { throw "REGRESSION_TEST_FAILED" }
Write-Host "ALL PASS"