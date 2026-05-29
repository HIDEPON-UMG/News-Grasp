# Mobility backfill ループ実行スクリプト (一時ファイル / build/ 配下なので git ignore 範囲)
# 5/22-5/27 の Mobility digest を 1 日ずつ claude --print で生成

$ErrorActionPreference = "Continue"
$repoRoot = "C:\Users\hidek\Obsidian\New's Grasp\News-Grasp"
Set-Location -Path $repoRoot

$promptFile = Join-Path $repoRoot "prompts\backfill-mobility.md"
$promptBody = Get-Content -Path $promptFile -Raw -Encoding UTF8

$dates = @(
  "2026-05-22",
  "2026-05-23",
  "2026-05-24",
  "2026-05-25",
  "2026-05-26",
  "2026-05-27"
)

foreach ($d in $dates) {
  $startTime = Get-Date
  Write-Host "==================================================="
  Write-Host "=== Mobility backfill START: $d ($($startTime.ToString('HH:mm:ss')))"
  Write-Host "==================================================="

  $fullPrompt = $promptBody + "`n" + $d
  $logPath = Join-Path $repoRoot "build\backfill_mobility_$($d).log"

  # claude --print --dangerously-skip-permissions で 1 日分を生成
  $fullPrompt | claude --print --dangerously-skip-permissions 2>&1 | Tee-Object -FilePath $logPath

  $endTime = Get-Date
  $elapsed = ($endTime - $startTime).TotalSeconds
  Write-Host ""
  Write-Host "=== Mobility backfill END: $d (elapsed=$([math]::Round($elapsed,1))s)"
  Write-Host ""
}

Write-Host "==================================================="
Write-Host "=== All 6 days finished. Verifying generated files..."
Write-Host "==================================================="

foreach ($d in $dates) {
  $f = Join-Path $repoRoot "digest\Mobility\$d-Mobility.md"
  if (Test-Path -Path $f) {
    $lines = (Get-Content -Path $f -Encoding UTF8).Length
    Write-Host "  OK $d : $lines lines"
  } else {
    Write-Host "  MISSING $d"
  }
}
