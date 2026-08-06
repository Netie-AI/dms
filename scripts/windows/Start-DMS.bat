@echo off
REM Double-click this to start DMS (do not double-click the .ps1 - Windows often opens Notepad).
setlocal
cd /d "%~dp0"
title DMS - starting stack
echo Starting DMS (OpenVault + Cortex L2 + API + UI)...
REM -EnableL2: FreeRoute governed freeform SQL (DMS_L2_MODEL=auto). Direct interact path.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-DMSStack.ps1" -StartSiblings -EnableL2 -StartUi -OpenBrowser
if errorlevel 1 (
  echo.
  echo Start failed - window stays open so you can read the error.
  pause
)
endlocal
