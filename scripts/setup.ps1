param(
  [switch]$SkipDocker,
  [switch]$WithDev
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  throw "uv is required. Install it from https://docs.astral.sh/uv/ or use py -3.12 -m venv .venv."
}

if (-not (Test-Path "$root\.venv\Scripts\python.exe")) {
  Write-Host "Creating Python 3.12 virtual environment..." -ForegroundColor Cyan
  uv venv --python 3.12 .venv
}

$requirements = if ($WithDev) { "wren-insight\requirements-dev.txt" } else { "wren-insight\requirements.txt" }
Write-Host "Installing $requirements ..." -ForegroundColor Cyan
uv pip install --python "$root\.venv\Scripts\python.exe" -r $requirements

if (-not $SkipDocker) {
  Write-Host "Starting demo PostgreSQL and MySQL..." -ForegroundColor Cyan
  docker compose up -d
}

if (-not (Test-Path "$root\wren-insight\.env")) {
  Copy-Item "$root\wren-insight\.env.example" "$root\wren-insight\.env"
  Write-Host "Created wren-insight\.env - add your LLM key before asking questions." -ForegroundColor Yellow
}

Write-Host "Done. Start with: .\run.ps1" -ForegroundColor Green
