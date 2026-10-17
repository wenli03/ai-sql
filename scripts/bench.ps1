param(
  [string]$Connection = "demo-duckdb",
  [int]$Runs = 1,
  [int]$Warmup = 1,
  [string]$Tag = "baseline",
  [switch]$Check,
  [switch]$UpdateBaseline,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
  throw "Python venv not found at .venv. Run scripts\setup.ps1 first."
}

$env:PYTHONUTF8 = "1"
$env:PYTHONUNBUFFERED = "1"
Set-Location (Join-Path $root "wren-insight")

$args = @(
  "-m", "bench.runner",
  "--connection", $Connection,
  "--runs", $Runs,
  "--warmup", $Warmup,
  "--tag", $Tag
)
if ($Check) { $args += "--check" }
if ($UpdateBaseline) { $args += "--update-baseline" }
if ($DryRun) { $args += "--dry-run" }

Write-Host "bench: $($args -join ' ')" -ForegroundColor Cyan
& $python @args
exit $LASTEXITCODE
