$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (!(Test-Path $Python)) {
    Write-Host "Bundled Python environment not found: $Python" -ForegroundColor Red
    Write-Host "Please copy the full package folder again."
    Read-Host "Press Enter to exit"
    exit 1
}

$BundledFfmpeg = Join-Path $Root "runtime\ffmpeg\bin"
if (Test-Path (Join-Path $BundledFfmpeg "ffmpeg.exe")) {
    $env:PATH = "$BundledFfmpeg;$env:PATH"
}

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

function Test-PortAvailable([int]$Port) {
    $connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    return $null -eq $connection
}

$Port = 8001
while ($Port -le 8010 -and !(Test-PortAvailable $Port)) {
    $Port += 1
}
if ($Port -gt 8010) {
    Write-Host "Ports 8001-8010 are busy. Cannot start." -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

$Url = "http://127.0.0.1:$Port/pro-workbench"

Write-Host "AI AudioVideo Pro V2" -ForegroundColor Green
Write-Host "Local URL: $Url"
Write-Host ""
Write-Host "Gemini API is only called after clicking Start Analysis in the web UI."
Write-Host "Paste the Gemini API Key in the web UI, or edit .env in this folder."
Write-Host "Close this window to stop the service."
Write-Host ""

Start-Job -ScriptBlock {
    param($TargetUrl)
    Start-Sleep -Seconds 2
    Start-Process $TargetUrl
} -ArgumentList $Url | Out-Null

& $Python -m uvicorn app.web:app --host 127.0.0.1 --port $Port

Read-Host "Service stopped. Press Enter to exit"
