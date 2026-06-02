@echo off
set "PACKAGE_ROOT=%~dp0"
if exist "%PACKAGE_ROOT%AI_AudioVideo_Pro_V2.exe" (
  start "AI AudioVideo Pro V2" "%PACKAGE_ROOT%AI_AudioVideo_Pro_V2.exe"
) else (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PACKAGE_ROOT%start-v2.ps1"
)
