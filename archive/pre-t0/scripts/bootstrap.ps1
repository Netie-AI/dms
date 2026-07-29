# Bootstrap DMS local appliance (dev without full Docker build).
# Starts Postgres via Docker if available; else SQLite control plane.
# API listens on :8090 (8080 often blocked on Windows).

param(
  [switch]$SkipWebInstall,
  [switch]$Compose,
  [int]$ApiPort = 8090
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

Write-Host "DMS root: $Root" -ForegroundColor Cyan

if ($Compose) {
  docker compose -f deploy/compose/docker-compose.yml --env-file deploy/presets/local-appliance.env up -d --build
  Write-Host "Compose up. Web :3000 API :8090 Caddy :80" -ForegroundColor Green
  exit 0
}

if (Get-Command docker -ErrorAction SilentlyContinue) {
  $pg = docker ps --filter "name=dms-postgres" --format "{{.Names}}" 2>$null
  if (-not $pg) {
    Write-Host "Starting Postgres container dms-postgres..."
    docker run -d --name dms-postgres `
      -e POSTGRES_USER=dms -e POSTGRES_PASSWORD=dms -e POSTGRES_DB=dms `
      -p 5432:5432 postgres:16-alpine | Out-Null
    Start-Sleep -Seconds 4
  }
  $env:DATABASE_URL = "postgresql://dms:dms@127.0.0.1:5432/dms"
  $env:DMS_USE_SQLITE = "0"
} else {
  Write-Host "Docker not found — using SQLite control plane" -ForegroundColor Yellow
  $env:DMS_USE_SQLITE = "1"
}

$env:CORTEX_URL = $(if ($env:CORTEX_URL) { $env:CORTEX_URL } else { "http://127.0.0.1:8010" })
$env:OPENVAULT_URL = $(if ($env:OPENVAULT_URL) { $env:OPENVAULT_URL } else { "http://127.0.0.1:5000" })
$env:CORTEX_PROXY = "1"
$env:DMS_JWT_SECRET = $(if ($env:DMS_JWT_SECRET) { $env:DMS_JWT_SECRET } else { "dms-dev-secret-change-me" })
$env:NEXT_PUBLIC_DMS_API_URL = "http://127.0.0.1:$ApiPort"

Write-Host "Installing API deps..."
Push-Location "$Root\services\api"
python -m pip install -q -r requirements.txt
Pop-Location

if (-not $SkipWebInstall) {
  Write-Host "Installing web deps..."
  Push-Location "$Root\apps\web"
  npm install --silent
  Pop-Location
}

Write-Host "Starting API on :$ApiPort ..."
Start-Process -FilePath "python" -ArgumentList "-m","uvicorn","main:app","--host","127.0.0.1","--port","$ApiPort" `
  -WorkingDirectory "$Root\services\api" -WindowStyle Minimized

Start-Sleep -Seconds 2
Write-Host "Starting web on :3000 ..."
Start-Process -FilePath "npm" -ArgumentList "run","dev" -WorkingDirectory "$Root\apps\web" -WindowStyle Minimized

Write-Host ""
Write-Host "DMS local appliance starting:" -ForegroundColor Green
Write-Host "  Web  http://127.0.0.1:3000/spaces"
Write-Host "  API  http://127.0.0.1:$ApiPort/health"
Write-Host "  Login admin@dms.local / admin"
Write-Host "  Cortex should be at $env:CORTEX_URL (PACK=dms)"
Write-Host "Run: .\scripts\verify.ps1"
