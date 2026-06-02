param(
    [string]$OutputRoot = "dist-v2-audio-only",
    [switch]$SkipZip
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
& (Join-Path $ProjectRoot "package-v2.ps1") `
    -OutputRoot $OutputRoot `
    -PackageName "AI_AudioVideo_Pro_V2_AudioOnly" `
    -ExecutableName "AI_AudioVideo_Pro_V2_AudioOnly" `
    -LauncherPath "packaging\AI 影音分析專業版 V2 (純音檔).bat" `
    -ReadmePath "packaging\README-V2-純音檔-同仁使用說明.md" `
    -AppDisplayName "AI 影音分析專業版 V2 (純音檔)" `
    -AudioOnly `
    -SkipZip:$SkipZip
