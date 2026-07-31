@echo off
REM Double-click this to start DMS (do not double-click the .ps1 - Windows often opens Notepad).
setlocal
cd /d "%~dp0"
title DMS - starting stack
echo Starting DMS (OpenVault + Cortex + API + UI)...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-DMSStack.ps1" -StartSiblings -StartUi -OpenBrowser
if errorlevel 1 (
  echo.
  echo Start failed - window stays open so you can read the error.
  pause
)
endlocal
