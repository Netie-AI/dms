# Install a Desktop shortcut that launches DMS via Start-DMS.bat (not raw .ps1).
param(
  [string]$ShortcutName = "DMS Demo"
)

$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$bat = Join-Path $here "Start-DMS.bat"
if (-not (Test-Path $bat)) {
  throw "Missing Start-DMS.bat next to this script: $bat"
}

$desktop = [Environment]::GetFolderPath("Desktop")
$lnkPath = Join-Path $desktop "$ShortcutName.lnk"

$w = New-Object -ComObject WScript.Shell
$sc = $w.CreateShortcut($lnkPath)
$sc.TargetPath = $bat
$sc.WorkingDirectory = $here
$sc.WindowStyle = 1
$sc.Description = "Start DMS stack (Postgres/API/UI + Cortex/OpenVault) and open Chat"
# Prefer a simple system icon (database-ish)
$sc.IconLocation = "%SystemRoot%\System32\shell32.dll,164"
$sc.Save()

Write-Host "Desktop shortcut created:" -ForegroundColor Green
Write-Host "  $lnkPath"
Write-Host "Double-click '$ShortcutName' on your Desktop to open DMS."
