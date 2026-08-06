@echo off
REM Creates a Desktop shortcut: "DMS Demo" → Start-DMS.bat
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-DesktopShortcut.ps1"
pause
