@echo off
set "PROJECT_ROOT=%~dp0"
start "AI Video News Analyzer Web" powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_ROOT%start_web.ps1"
timeout /t 2 >nul
start http://127.0.0.1:8000

