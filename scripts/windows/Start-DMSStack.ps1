# Start DMS local stack - double-click Start-DMS.bat (or Desktop "DMS Demo" shortcut).
# Raw .ps1 double-click often opens Notepad or hits execution policy - use the .bat.
# ASCII-only: Windows PowerShell 5.1 misparses UTF-8 em-dash / ellipsis as broken strings.
param(
  [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
  [string]$ComposeDir = "",
  [string]$ApiUrl = "http://127.0.0.1:8090/health",
  [string]$UiUrl = "http://127.0.0.1:3000",
  [string]$CortexUrl = $(if ($env:CORTEX_URL) { $env:CORTEX_URL } else { "http://127.0.0.1:8010" }),
  [string]$OpenVaultUrl = $(if ($env:OPENVAULT_URL) { $env:OPENVAULT_URL } else { "http://127.0.0.1:5000" }),
  [switch]$StartSiblings,
  [switch]$StartUi,
  [switch]$OpenBrowser,
  [switch]$NoLocalApiFallback,
  # Basic L2 FreeRoute on Cortex (EPIC-012). Off by default; pass -EnableL2 for freeform SQL.
  [switch]$EnableL2,
  [string]$L2Model = ""
)

if (-not $ComposeDir) {
  $ComposeDir = Join-Path $RepoRoot "deploy\compose"
}

function Test-HttpOk([string]$Url) {
  try {
    $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
    return $r.StatusCode -ge 200 -and $r.StatusCode -lt 500
  } catch { return $false }
}

function Wait-HttpOk([string]$Url, [int]$Seconds = 60) {
  $deadline = (Get-Date).AddSeconds($Seconds)
  do {
    if (Test-HttpOk $Url) { return $true }
    Start-Sleep -Seconds 2
  } while ((Get-Date) -lt $deadline)
  return $false
}

$env:OPENVAULT_HOME = if ($env:OPENVAULT_HOME) { $env:OPENVAULT_HOME } else { "D:\OpenVault\.openvault" }
$env:OPENVAULT_URL = $OpenVaultUrl
$env:CORTEX_URL = $CortexUrl
$env:DMS_ASK_MODE = if ($env:DMS_ASK_MODE) { $env:DMS_ASK_MODE } else { "live" }
if ($EnableL2) {
  $env:DMS_L2_ENABLED = "1"
  # Bakeoff winner (docs/L2_MODEL_BAKEOFF.md): only ``auto`` clears FreeRoute;
  # named ids 404/400. Pin auto unless caller overrides -L2Model.
  if (-not $L2Model) { $L2Model = "auto" }
  $env:DMS_L2_MODEL = $L2Model
  Write-Host "L2 FreeRoute enabled on Cortex (DMS_L2_ENABLED=1, DMS_L2_MODEL=$L2Model)" -ForegroundColor Cyan
} elseif (-not $env:DMS_L2_ENABLED) {
  $env:DMS_L2_ENABLED = "0"
}
# Demo-ready default = no silent fallback. Opt in with DMS_DEMO_FALLBACK=1 for local bring-up.
# Force-clear accidental shell leftovers that leave the UI stuck in DEMO ASK MODE.
if (-not $env:DMS_DEMO_FALLBACK) { $env:DMS_DEMO_FALLBACK = "0" }
if ($env:DMS_ASK_MODE -eq "" -or $null -eq $env:DMS_ASK_MODE) { $env:DMS_ASK_MODE = "live" }

# Warehouse identity (S4 / TAS-DMS §6). Ingest writes DMS_WAREHOUSE_DB; Cortex
# answers from CORTEX_HOME\data\dms_demo.duckdb unless pointed elsewhere.
# Two files is the measured bug. Sync bronze into the serving file *before*
# Cortex starts (DuckDB lock). Demo schemas stay split (outbound vs OUT).
if (-not $env:DMS_WAREHOUSE_DB) {
  $env:DMS_WAREHOUSE_DB = Join-Path $RepoRoot "data\dms_demo.duckdb"
}
$cortexHome = if ($env:CORTEX_HOME) { $env:CORTEX_HOME } else { "D:\Cortex" }
if (-not $env:CORTEX_WAREHOUSE_DB) {
  $discovered = Join-Path $cortexHome "data\dms_demo.duckdb"
  if (Test-Path $discovered) { $env:CORTEX_WAREHOUSE_DB = $discovered }
}
if (-not $env:DMS_ORACLE_WAREHOUSE -and $env:CORTEX_WAREHOUSE_DB) {
  $env:DMS_ORACLE_WAREHOUSE = $env:CORTEX_WAREHOUSE_DB
}

Write-Host "DMS open-all - repo $RepoRoot" -ForegroundColor Cyan
Write-Host "OPENVAULT_HOME=$($env:OPENVAULT_HOME)"
Write-Host ("DMS warehouse    {0}" -f $env:DMS_WAREHOUSE_DB)
if ($env:CORTEX_WAREHOUSE_DB) {
  Write-Host ("Cortex warehouse {0}" -f $env:CORTEX_WAREHOUSE_DB)
  $dmsWh = [System.IO.Path]::GetFullPath($env:DMS_WAREHOUSE_DB)
  $cxWh = [System.IO.Path]::GetFullPath($env:CORTEX_WAREHOUSE_DB)
  if ($dmsWh -ne $cxWh) {
    $syncScript = Join-Path $RepoRoot "scripts\sync_bronze_to_serving.py"
    if (Test-Path $syncScript) {
      Write-Host "Two warehouse files - copying bronze into the file chat reads..."
      python $syncScript
      if ($LASTEXITCODE -ne 0) {
        Write-Host "Bronze sync failed. Stop Cortex and run: python scripts\sync_bronze_to_serving.py" -ForegroundColor Yellow
      }
    }
  } else {
    Write-Host "Warehouse identity: one file"
  }
}

# --- Postgres (compose) ---
# The base compose file deliberately leaves postgres on `expose: 5432` so the
# appliance keeps Caddy as its only public port. This script runs the API on the
# host, so it must name the hostdb overlay too - without it the container is
# healthy and unreachable, the API silently falls back to the in-process Space
# store, and Spaces stop persisting across a restart.
Write-Host "Compose postgres (with host binding)..."
Push-Location $ComposeDir
try {
  docker compose -f docker-compose.yml -f docker-compose.hostdb.yml up -d postgres 2>$null | Out-Null
  docker compose up -d api 2>$null | Out-Null
} catch {
  Write-Host "Docker compose skipped/failed: $_" -ForegroundColor Yellow
}
Pop-Location

# --- Siblings ---
if ($StartSiblings) {
  $ovHealth = "$OpenVaultUrl/api/healthz"
  if (-not (Test-HttpOk $ovHealth)) {
    $ovScript = "D:\OpenVault\scripts\windows\Start-OpenVaultDemo.ps1"
    if (Test-Path $ovScript) {
      Write-Host "Starting OpenVault..."
      Start-Process powershell.exe -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $ovScript
      ) -WindowStyle Minimized
    } else {
      Write-Host "OpenVault start script missing: $ovScript" -ForegroundColor Yellow
    }
  } else {
    Write-Host "OpenVault already ok"
  }

  if (-not (Test-HttpOk "$CortexUrl/health")) {
    $cxScript = "D:\Cortex\scripts\start_cortex_engine.ps1"
    if (Test-Path $cxScript) {
      Write-Host "Starting Cortex on :8010..."
      # Kill stale listener if Force-style reclaim needed
      try {
        Get-NetTCPConnection -LocalPort 8010 -State Listen -ErrorAction SilentlyContinue |
          ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Seconds 1
      } catch { }
      $l2Flag = if ($env:DMS_L2_ENABLED) { $env:DMS_L2_ENABLED } else { "0" }
      $l2ModelEnv = if ($env:DMS_L2_MODEL) { "`$env:DMS_L2_MODEL='$($env:DMS_L2_MODEL)'; " } else { "" }
      Start-Process powershell.exe -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-Command",
        "`$env:PACK='dms'; `$env:OPENVAULT_HOME='$($env:OPENVAULT_HOME)'; `$env:OPENVAULT_URL='$OpenVaultUrl'; `$env:DMS_L2_ENABLED='$l2Flag'; `$env:CORTEX_WAREHOUSE_DB='$($env:CORTEX_WAREHOUSE_DB)'; ${l2ModelEnv}Set-Location 'D:\Cortex'; python -m uvicorn CortexOS.api.main:app --host 127.0.0.1 --port 8010"
      ) -WindowStyle Minimized
    } else {
      Write-Host "Cortex start script missing: $cxScript" -ForegroundColor Yellow
    }
  } else {
    Write-Host "Cortex already ok"
  }
}

# --- API ---
$apiOk = Wait-HttpOk $ApiUrl 45
if (-not $apiOk -and -not $NoLocalApiFallback) {
  Write-Host "Compose API not up - starting local uvicorn on :8090..." -ForegroundColor Yellow
  $pyCmd = @(
    "`$env:OPENVAULT_HOME='$($env:OPENVAULT_HOME)'",
    "`$env:OPENVAULT_URL='$OpenVaultUrl'",
    "`$env:CORTEX_URL='$CortexUrl'",
    "`$env:DMS_ASK_MODE='$($env:DMS_ASK_MODE)'",
    "`$env:DMS_DEMO_FALLBACK='$($env:DMS_DEMO_FALLBACK)'",
    "`$env:DMS_WAREHOUSE_DB='$($env:DMS_WAREHOUSE_DB)'",
    "`$env:CORTEX_WAREHOUSE_DB='$($env:CORTEX_WAREHOUSE_DB)'",
    "Set-Location '$RepoRoot'",
    "python -m uvicorn dms_api.app:app --app-dir apps/api --host 127.0.0.1 --port 8090"
  ) -join "; "
  Start-Process powershell.exe -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $pyCmd) -WindowStyle Minimized
  $apiOk = Wait-HttpOk $ApiUrl 60
}

if ($apiOk) { Write-Host "DMS API ok $ApiUrl" -ForegroundColor Green }
else { Write-Host "DMS API still DOWN at $ApiUrl" -ForegroundColor Red }

# --- UI ---
if ($StartUi) {
  if (-not (Test-HttpOk $UiUrl)) {
    $uiDir = Join-Path $RepoRoot "apps\ui"
    Write-Host "Starting Vite UI on :3000..."
    Start-Process powershell.exe -ArgumentList @(
      "-NoProfile", "-ExecutionPolicy", "Bypass",
      "-Command",
      "Set-Location '$uiDir'; npm run dev -- --host 127.0.0.1 --port 3000"
    ) -WindowStyle Minimized
    [void](Wait-HttpOk $UiUrl 90)
  } else {
    Write-Host "UI already ok $UiUrl"
  }
}

$cxOk = Test-HttpOk "$CortexUrl/health"
$ovOk = Test-HttpOk "$OpenVaultUrl/api/healthz"
Write-Host ("Cortex    {0}  {1}" -f $(if ($cxOk) { "ok" } else { "DOWN" }), "$CortexUrl/health")
Write-Host ("OpenVault {0}  {1}" -f $(if ($ovOk) { "ok" } else { "DOWN" }), "$OpenVaultUrl/api/healthz")

# Warm the answer path before saying we are ready (#43).
#
# /health answering is liveness, not readiness. A cold engine returns 200 on
# /health while its first real submit still takes long enough to time out, so
# the launcher printed "Cortex ok" and the founder asked the first question
# straight into the slow path. That first question is the demo's first
# question, on a cold laptop.
#
# One throwaway ask absorbs the warm-up here instead. Its answer is discarded -
# what matters is that the slow path has been walked once before anyone is
# watching. Never fatal: if this cannot complete, the stack is still up and the
# operator should see the real failure on a real question, not a launcher
# refusing to finish.
if ($apiOk) {
  $apiBase = $ApiUrl -replace '/health$',''
  # A real Space id is required. An invalid one 404s on space_not_found in
  # well under a second, before the engine is touched at all - so it would
  # print "warm" having warmed nothing, which is worse than not trying.
  $warmSpace = $null
  try {
    $spaces = Invoke-RestMethod -Uri "$apiBase/v1/spaces" -UseBasicParsing -TimeoutSec 15
    if ($spaces -is [array] -and $spaces.Count -gt 0) { $warmSpace = $spaces[0].id }
    elseif ($spaces.spaces -and $spaces.spaces.Count -gt 0) { $warmSpace = $spaces.spaces[0].id }
  } catch { }

  if ($warmSpace) {
    Write-Host "Warming the answer path (first submit on a cold engine is slow)..."
    $warmBody = (@{ question = "warmup"; space_id = $warmSpace } | ConvertTo-Json -Compress)
    $warmStart = Get-Date
    try {
      Invoke-WebRequest -Uri "$apiBase/v1/chat/ask" -Method POST -Body $warmBody `
        -ContentType "application/json" -UseBasicParsing -TimeoutSec 180 | Out-Null
    } catch {
      # An abstain, a refusal, even a timeout are all fine. The answer is
      # discarded; what matters is that the slow path has been walked once
      # before anyone is watching. Never fatal - the stack is up either way,
      # and the operator should meet a real failure on a real question rather
      # than a launcher that refuses to finish.
    }
    $warmSecs = [math]::Round(((Get-Date) - $warmStart).TotalSeconds, 1)
    if ($warmSecs -lt 0.5) {
      Write-Host ("Warm-up returned in {0}s - too fast to have reached the engine; first real ask may still be slow" -f $warmSecs) -ForegroundColor Yellow
    } else {
      Write-Host ("Answer path warm ({0}s absorbed here instead of on your first question)" -f $warmSecs) -ForegroundColor Green
    }
  } else {
    Write-Host "Skipped answer-path warm-up (no Space to ask against yet)" -ForegroundColor Yellow
  }
}

Write-Host ""
Write-Host "URLs" -ForegroundColor Cyan
Write-Host "  Chat     $UiUrl/"
Write-Host "  Library  $UiUrl/library"
Write-Host "  Studio   $UiUrl/studio"
Write-Host "  API      $ApiUrl"
Write-Host "  Cortex   $CortexUrl"
Write-Host "  OpenVault $OpenVaultUrl"
Write-Host ""

if ($OpenBrowser) {
  $target = if (Test-HttpOk $UiUrl) { $UiUrl } else { "http://127.0.0.1:8090/health" }
  Write-Host "Opening browser -> $target"
  Start-Process $target
}

Write-Host "Live smoke: python scripts/smoke_live_ask.py"
Write-Host "After a Studio upload (if chat cannot see it): stop Cortex, python scripts/sync_bronze_to_serving.py, restart"
Write-Host "Desktop shortcut: double-click Install-DesktopShortcut.bat once"
