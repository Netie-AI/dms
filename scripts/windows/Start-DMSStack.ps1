# Start DMS local stack with health checks (API + optional Cortex/OpenVault).
# Does not force-start siblings if already healthy.
param(
  [string]$ComposeDir = "$PSScriptRoot\..\deploy\compose",
  [string]$ApiUrl = "http://127.0.0.1:8090/health",
  [string]$CortexUrl = $(if ($env:CORTEX_URL) { $env:CORTEX_URL } else { "http://127.0.0.1:8010/health" }),
  [string]$OpenVaultUrl = $(if ($env:OPENVAULT_URL) { $env:OPENVAULT_URL } else { "http://127.0.0.1:5000/health" })
)

function Test-HttpOk([string]$Url) {
  try {
    $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
    return $r.StatusCode -ge 200 -and $r.StatusCode -lt 300
  } catch { return $false }
}

Write-Host "DMS compose postgres/api…"
Push-Location $ComposeDir
docker compose up -d postgres 2>$null
# API may be local uvicorn — only start compose api if image present
docker compose up -d api 2>$null
Pop-Location

$deadline = (Get-Date).AddSeconds(45)
do {
  if (Test-HttpOk $ApiUrl) { Write-Host "DMS API ok $ApiUrl"; break }
  Start-Sleep -Seconds 2
} while ((Get-Date) -lt $deadline)

if (-not (Test-HttpOk $ApiUrl)) {
  Write-Host "DMS API not up — run: cd apps/api && uvicorn dms_api.app:app --port 8090"
}

if (Test-HttpOk $CortexUrl) { Write-Host "Cortex ok $CortexUrl" }
else { Write-Host "Cortex not reachable at $CortexUrl (start engine separately)" }

if (Test-HttpOk $OpenVaultUrl) { Write-Host "OpenVault ok $OpenVaultUrl" }
else { Write-Host "OpenVault not reachable at $OpenVaultUrl (trust-root keys needed for T2 mint)" }

Write-Host "UI: cd apps/ui && npm run dev  →  http://127.0.0.1:3000"
