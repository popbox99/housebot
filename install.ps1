# ==============================================================================
# HouseBot 1-Line Installer for Windows (Option 3)
# Usage in PowerShell:
#   irm https://raw.githubusercontent.com/popbox99/housebot/main/install.ps1 | iex
# ==============================================================================
$ErrorActionPreference = "Stop"

Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "            HouseBot 1-Line Quick Installer (Windows)" -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan

$InstallDir = "$env:LOCALAPPDATA\HouseBot"
$BinFile = "$InstallDir\HouseBot.exe"

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

# Check if Python is installed or download standalone binary
if (Get-Command "python" -ErrorAction SilentlyContinue) {
    Write-Host "Found Python on your system." -ForegroundColor Green
    $PyVer = python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    Write-Host "Using Python $PyVer" -ForegroundColor Gray

    # Clone or fetch source
    $ZipUrl = "https://github.com/popbox99/housebot/archive/refs/heads/main.zip"
    $ZipFile = "$env:TEMP\housebot.zip"
    Write-Host "Downloading HouseBot..." -ForegroundColor Yellow
    Invoke-WebRequest -Uri $ZipUrl -OutFile $ZipFile
    Expand-Archive -Path $ZipFile -DestinationPath $env:TEMP\housebot-extract -Force
    Copy-Item -Path "$env:TEMP\housebot-extract\housebot-main\*" -Destination $InstallDir -Recurse -Force
    Remove-Item -Path $ZipFile, "$env:TEMP\housebot-extract" -Recurse -Force

    # Create virtual environment
    $VenvPython = "$InstallDir\.venv\Scripts\python.exe"
    if (-not (Test-Path $VenvPython)) {
        Write-Host "Setting up isolated virtual environment..." -ForegroundColor Yellow
        python -m venv "$InstallDir\.venv"
    }

    # Create batch wrapper in user path
    $BatchLauncher = "$InstallDir\housebot.cmd"
    "@echo off`r`n`"$VenvPython`" -m housebot %*" | Out-File -FilePath $BatchLauncher -Encoding ASCII
} else {
    Write-Host "Downloading standalone HouseBot.exe..." -ForegroundColor Yellow
    $ExeUrl = "https://github.com/popbox99/housebot/releases/latest/download/HouseBot-Windows-x64.exe"
    Invoke-WebRequest -Uri $ExeUrl -OutFile $BinFile
}

# Create desktop / start menu shortcut
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut("$env:APPDATA\Microsoft\Windows\Start Menu\Programs\HouseBot.lnk")
if (Test-Path "$InstallDir\housebot.cmd") {
    $Shortcut.TargetPath = "$InstallDir\housebot.cmd"
} else {
    $Shortcut.TargetPath = $BinFile
}
$Shortcut.WorkingDirectory = $InstallDir
$Shortcut.Save()

Write-Host "=================================================================" -ForegroundColor Green
Write-Host "Installation complete! Starting HouseBot..." -ForegroundColor Green
Write-Host "=================================================================" -ForegroundColor Green

if (Test-Path "$InstallDir\housebot.cmd") {
    & "$InstallDir\housebot.cmd" --web-setup
} else {
    Start-Process $BinFile
}
