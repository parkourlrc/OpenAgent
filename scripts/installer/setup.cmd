@echo off
setlocal
cd /d "%~dp0"
powershell.exe -ExecutionPolicy Bypass -NoProfile -File "%~dp0install.ps1" -Launch
exit /b %ERRORLEVEL%

