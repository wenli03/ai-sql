param(
  [int]$Port = 8000,
  [switch]$NoDocker
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
  throw "Python venv not found at .venv. Run scripts\setup.ps1 first."
}

if (-not $NoDocker) {
  $dockerReady = $false
  $probe = Start-Job { docker info --format '{{.ServerVersion}}' 2>&1 }
  if (Wait-Job $probe -Timeout 8) {
    $probeResult = (Receive-Job $probe) -join " "
    $dockerReady = $probeResult -match '^\d+\.\d+'
  } else {
    Stop-Job $probe -ErrorAction SilentlyContinue
  }
  Remove-Job $probe -Force -ErrorAction SilentlyContinue

  if (-not $dockerReady) {
    Write-Warning "Docker engine is not responding. Starting without PostgreSQL/MySQL demo; use 'demo-duckdb' or restart Docker Desktop."
  } else {
    Write-Host "Starting demo PostgreSQL and MySQL..." -ForegroundColor Cyan
    docker compose up -d

    Write-Host "Waiting for databases to become healthy..." -ForegroundColor Cyan
    $deadline = (Get-Date).AddSeconds(120)
    do {
      Start-Sleep -Seconds 2
      try {
        $services = docker compose ps --format json | ConvertFrom-Json
        $notReady = @($services | Where-Object { $_.Health -and $_.Health -ne "healthy" })
        $running = @($services | Where-Object { $_.State -eq "running" })
        $ready = ($notReady.Count -eq 0) -and ($running.Count -eq $services.Count) -and ($services.Count -gt 0)
      } catch {
        $ready = $false
      }
    } while (-not $ready -and (Get-Date) -lt $deadline)
    if (-not $ready) {
      Write-Warning "Databases did not become healthy in time; you can still use demo-duckdb."
    }
  }
}

$env:PYTHONUTF8 = "1"
$env:PYTHONUNBUFFERED = "1"
Set-Location (Join-Path $PSScriptRoot "wren-insight")
Write-Host "数语: http://127.0.0.1:$Port" -ForegroundColor Green
& $python -m uvicorn app.main:app --host 127.0.0.1 --port $Port
