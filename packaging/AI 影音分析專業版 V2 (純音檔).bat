@echo off
set "PACKAGE_ROOT=%~dp0"
if exist "%PACKAGE_ROOT%AI_AudioVideo_Pro_V2_AudioOnly.exe" (
  start "AI AudioVideo Pro V2 Audio Only" "%PACKAGE_ROOT%AI_AudioVideo_Pro_V2_AudioOnly.exe"
) else (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PACKAGE_ROOT%start-v2.ps1"
)
