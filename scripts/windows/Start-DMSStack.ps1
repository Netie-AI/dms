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
  [switch]$NoLocalApiFallback
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
# Demo-ready default = no silent fallback. Opt in with DMS_DEMO_FALLBACK=1 for local bring-up.
# Force-clear accidental shell leftovers that leave the UI stuck in DEMO ASK MODE.
if (-not $env:DMS_DEMO_FALLBACK) { $env:DMS_DEMO_FALLBACK = "0" }
if ($env:DMS_ASK_MODE -eq "" -or $null -eq $env:DMS_ASK_MODE) { $env:DMS_ASK_MODE = "live" }

Write-Host "DMS open-all - repo $RepoRoot" -ForegroundColor Cyan
Write-Host "OPENVAULT_HOME=$($env:OPENVAULT_HOME)"

# --- Postgres (compose) ---
Write-Host "Compose postgres..."
Push-Location $ComposeDir
try {
  docker compose up -d postgres 2>$null | Out-Null
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
      Start-Process powershell.exe -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-Command",
        "`$env:PACK='dms'; `$env:OPENVAULT_HOME='$($env:OPENVAULT_HOME)'; `$env:OPENVAULT_URL='$OpenVaultUrl'; Set-Location 'D:\Cortex'; python -m uvicorn CortexOS.api.main:app --host 127.0.0.1 --port 8010"
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
Write-Host "Desktop shortcut: double-click Install-DesktopShortcut.bat once"
