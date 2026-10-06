# ==============================================================================
# HouseBot Uninstaller for Windows
# Usage in PowerShell:
#   irm https://raw.githubusercontent.com/popbox99/HouseBotInstaller/main/uninstall.ps1 | iex
# ==============================================================================
$ErrorActionPreference = "SilentlyContinue"

Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "               HouseBot Quick Uninstaller (Windows)" -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "This will stop background services and remove HouseBot." -ForegroundColor Gray
Write-Host "Your Obsidian vault and personal notes will NOT be touched." -ForegroundColor Gray
Write-Host ""

$InstallDir = "$env:LOCALAPPDATA\HouseBot"
$StartupScript = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\housebot.vbs"
$Shortcut = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\HouseBot.lnk"
$ConfigDir = "$env:APPDATA\housebot"
$DataDir = "$env:LOCALAPPDATA\housebot"

# 1. Terminate any running HouseBot process
Get-Process -Name "HouseBot" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

# 2. Remove Startup launcher
if (Test-Path $StartupScript) {
    Remove-Item -Path $StartupScript -Force
    Write-Host "✓ Removed Windows Startup script." -ForegroundColor Green
}

# 3. Remove Start Menu shortcut
if (Test-Path $Shortcut) {
    Remove-Item -Path $Shortcut -Force
    Write-Host "✓ Removed Start Menu shortcut." -ForegroundColor Green
}

# 4. Remove installed program files
if (Test-Path $InstallDir) {
    Remove-Item -Path $InstallDir -Recurse -Force
    Write-Host "✓ Removed application folder ($InstallDir)." -ForegroundColor Green
}

# 5. Prompt for config/data removal if running interactively
$Purge = Read-Host "Also delete HouseBot configuration and database files? (y/N)"
if ($Purge -match "^[yY]") {
    if (Test-Path $ConfigDir) { Remove-Item -Path $ConfigDir -Recurse -Force }
    if (Test-Path $DataDir) { Remove-Item -Path $DataDir -Recurse -Force }
    Write-Host "✓ Removed configuration and database." -ForegroundColor Green
} else {
    Write-Host "ℹ️ Preserved configuration and database." -ForegroundColor Yellow
}

Write-Host "=================================================================" -ForegroundColor Green
Write-Host "🎉 HouseBot has been successfully uninstalled from your machine." -ForegroundColor Green
Write-Host "=================================================================" -ForegroundColor Green
