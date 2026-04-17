param(
  [string]$BaseDir = "c:\Workshop\科研\Paper-Library\Paper\AI4Research",
  [string[]]$paper_source_paths
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$renderScript = Join-Path $repoRoot 'skills/paper-deep-reading/scripts/render_report_html.ps1'

if (-not (Test-Path -LiteralPath $renderScript)) {
  Write-Error "render script not found: $renderScript"
  exit 3
}

$targets = if ($paper_source_paths -and $paper_source_paths.Count -gt 0) {
  $paper_source_paths
} else {
  @(
    (Join-Path $BaseDir '2024.emnlp-main.726.pdf'),
    (Join-Path $BaseDir '2502.07286v1.pdf')
  )
}

$failures = New-Object System.Collections.Generic.List[string]
foreach ($paperPath in $targets) {
  if (-not (Test-Path -LiteralPath $paperPath)) {
    $failures.Add("missing paper: $paperPath")
    continue
  }

  & $renderScript -paper_source_path $paperPath
  if ($LASTEXITCODE -ne 0) {
    $failures.Add("render failed($LASTEXITCODE): $paperPath")
  }
}

if ($failures.Count -gt 0) {
  $failures | ForEach-Object { Write-Error $_ }
  exit 3
}

Write-Output 'Rendered HTML reports via skill script.'
exit 0
