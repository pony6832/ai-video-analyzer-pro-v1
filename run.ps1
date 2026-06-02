param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$VideoPath,

    [Parameter(Position = 1)]
    [int]$ChunkMinutes = 10,

    [switch]$Force
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (!(Test-Path $Python)) {
    Write-Host "找不到虛擬環境 Python：$Python" -ForegroundColor Red
    exit 1
}

$FfmpegBin = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.1-full_build\bin"
if (Test-Path $FfmpegBin) {
    $env:PATH = "$FfmpegBin;$env:PATH"
}

$ArgsList = @("main.py", "analyze", $VideoPath, "--chunk-minutes", "$ChunkMinutes")
if ($Force) {
    $ArgsList += "--force"
}

& $Python @ArgsList

