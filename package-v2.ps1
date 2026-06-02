param(
    [string]$OutputRoot = "dist-v2",
    [string]$PackageName = "AI_AudioVideo_Pro_V2",
    [string]$ExecutableName = "AI_AudioVideo_Pro_V2",
    [string]$LauncherPath = "packaging\AI 影音分析專業版 V2.bat",
    [string]$ReadmePath = "packaging\README-V2-同仁使用說明.md",
    [string]$AppDisplayName = "AI 影音分析專業版 V2",
    [switch]$AudioOnly,
    [switch]$IncludeEnv,
    [switch]$SkipZip
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$PackageRoot = Join-Path $ProjectRoot $OutputRoot
$PackageDir = Join-Path $PackageRoot $PackageName
$ZipPath = Join-Path $PackageRoot "$PackageName.zip"

function Copy-Directory($Source, $Destination) {
    if (!(Test-Path $Source)) {
        throw "Missing required directory: $Source"
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Destination) | Out-Null
    Copy-Item -Path $Source -Destination $Destination -Recurse -Force
}

function Copy-File($Source, $Destination) {
    if (!(Test-Path $Source)) {
        throw "Missing required file: $Source"
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Destination) | Out-Null
    Copy-Item -Path $Source -Destination $Destination -Force
}

Write-Host "Building frontend..." -ForegroundColor Cyan
Push-Location (Join-Path $ProjectRoot "webapp")
try {
    npm run build
}
finally {
    Pop-Location
}

Write-Host "Building portable executable..." -ForegroundColor Cyan
$PyInstaller = Join-Path $ProjectRoot ".venv\Scripts\pyinstaller.exe"
if (!(Test-Path $PyInstaller)) {
    throw "PyInstaller not found. Run: .\.venv\Scripts\python.exe -m pip install pyinstaller"
}
$BuildRoot = Join-Path $ProjectRoot "build-v2"
if (Test-Path $BuildRoot) {
    Remove-Item -LiteralPath $BuildRoot -Recurse -Force
}
& $PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --name $ExecutableName `
    --distpath $BuildRoot `
    --workpath (Join-Path $BuildRoot "work") `
    --specpath (Join-Path $BuildRoot "spec") `
    --paths $ProjectRoot `
    --collect-submodules "app" `
    --hidden-import "main" `
    --hidden-import "typer" `
    --hidden-import "click" `
    --hidden-import "rich" `
    --hidden-import "uvicorn.logging" `
    --hidden-import "uvicorn.loops" `
    --hidden-import "uvicorn.loops.auto" `
    --hidden-import "uvicorn.protocols" `
    --hidden-import "uvicorn.protocols.http" `
    --hidden-import "uvicorn.protocols.http.auto" `
    --hidden-import "uvicorn.protocols.websockets" `
    --hidden-import "uvicorn.protocols.websockets.auto" `
    --hidden-import "uvicorn.lifespan" `
    --hidden-import "uvicorn.lifespan.on" `
    --hidden-import "app.web" `
    --hidden-import "app.config" `
    --hidden-import "app.workbench" `
    --hidden-import "app.video_utils" `
    --hidden-import "app.gemini_client" `
    --hidden-import "app.report_writer" `
    --hidden-import "app.schemas" `
    (Join-Path $ProjectRoot "packaging\v2_server.py")

Write-Host "Preparing package directory..." -ForegroundColor Cyan
if (Test-Path $PackageDir) {
    Remove-Item -LiteralPath $PackageDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $PackageDir | Out-Null

Copy-Directory (Join-Path $ProjectRoot "app") (Join-Path $PackageDir "app")
Copy-Directory (Join-Path $ProjectRoot "webapp\dist") (Join-Path $PackageDir "webapp\dist")

Copy-File (Join-Path $ProjectRoot "main.py") (Join-Path $PackageDir "main.py")
Copy-File (Join-Path $BuildRoot "$ExecutableName.exe") (Join-Path $PackageDir "$ExecutableName.exe")
Copy-File (Join-Path $ProjectRoot "requirements.txt") (Join-Path $PackageDir "requirements.txt")
Copy-File (Join-Path $ProjectRoot ".env.example") (Join-Path $PackageDir ".env.example")
Copy-File (Join-Path $ProjectRoot "packaging\start-v2.ps1") (Join-Path $PackageDir "start-v2.ps1")
$Launcher = Join-Path $ProjectRoot $LauncherPath
$Readme = Join-Path $ProjectRoot $ReadmePath
Copy-File $Launcher (Join-Path $PackageDir (Split-Path -Leaf $Launcher))
Copy-File $Readme (Join-Path $PackageDir (Split-Path -Leaf $Readme))

if ($IncludeEnv) {
    Copy-File (Join-Path $ProjectRoot ".env") (Join-Path $PackageDir ".env")
}
else {
    $AudioOnlyValue = if ($AudioOnly) { "true" } else { "false" }
    @"
# Fill your Gemini API key below.
# Example: GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_API_KEY=your_gemini_api_key_here

# Model settings. Keep these defaults unless you know you need to change them.
GEMINI_MODEL=gemini-2.5-flash
GEMINI_FALLBACK_MODELS=gemini-2.0-flash

# App mode
APP_DISPLAY_NAME=$AppDisplayName
APP_AUDIO_ONLY=$AudioOnlyValue
"@ | Set-Content -Path (Join-Path $PackageDir ".env") -Encoding UTF8
}

foreach ($dir in @("videos", "temp", "data\jobs", "outputs\jobs", "outputs\cache")) {
    $target = Join-Path $PackageDir $dir
    New-Item -ItemType Directory -Force -Path $target | Out-Null
    New-Item -ItemType File -Force -Path (Join-Path $target ".gitkeep") | Out-Null
}

$RuntimeFfmpeg = Join-Path $PackageDir "runtime\ffmpeg\bin"
New-Item -ItemType Directory -Force -Path $RuntimeFfmpeg | Out-Null
$ffmpeg = (Get-Command ffmpeg.exe -ErrorAction SilentlyContinue).Source
$ffprobe = (Get-Command ffprobe.exe -ErrorAction SilentlyContinue).Source
if ($ffmpeg -and $ffprobe) {
    Copy-File $ffmpeg (Join-Path $RuntimeFfmpeg "ffmpeg.exe")
    Copy-File $ffprobe (Join-Path $RuntimeFfmpeg "ffprobe.exe")
}
else {
    Write-Warning "ffmpeg/ffprobe not found. The package will not include FFmpeg."
}

if (!$SkipZip) {
    Write-Host "Creating zip..." -ForegroundColor Cyan
    if (Test-Path $ZipPath) {
        Remove-Item -LiteralPath $ZipPath -Force
    }
    Compress-Archive -Path (Join-Path $PackageDir "*") -DestinationPath $ZipPath -Force
}

Write-Host "V2 package ready:" -ForegroundColor Green
Write-Host "  Folder: $PackageDir"
if (!$SkipZip) {
    Write-Host "  Zip:    $ZipPath"
}
Write-Host "Run the BAT launcher in the package folder."

