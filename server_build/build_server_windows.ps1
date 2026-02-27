<#
.SYNOPSIS
    Build the HOMEPAGE Server Windows executable.
.DESCRIPTION
    Creates a single-file .exe for the HOMEPAGE Server using PyInstaller.
    Output: dist\HOMEPAGEServer-windows.exe
.NOTES
    Run from the server_build/ directory (or anywhere — the script auto-navigates).
    Requires: Python 3.11+, pip.
#>

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoDir = Split-Path -Parent $ScriptDir
Set-Location $RepoDir

Write-Host "=== HOMEPAGE Server — Windows Build ===" -ForegroundColor Cyan

# 1. Virtual environment
$VenvDir = Join-Path $RepoDir ".venv-server-build"
if (-not (Test-Path $VenvDir)) {
    Write-Host "Creating virtual environment..."
    python -m venv $VenvDir
}
$ActivateScript = Join-Path $VenvDir "Scripts\Activate.ps1"
. $ActivateScript

# 2. Install dependencies
Write-Host "Installing server dependencies..."
python -m pip install --upgrade pip | Out-Null
pip install -r requirements.txt
pip install -r server_build\requirements-server-build.txt

# 3. Generate icon if missing
$IconPath = Join-Path $ScriptDir "resources\icon.png"
if (-not (Test-Path $IconPath)) {
    Write-Host "Generating tray icon..."
    python server_build\resources\generate_icon.py
}

# 4. Run PyInstaller
Write-Host "Running PyInstaller..."
pyinstaller server_build\HOMEPAGEServer-windows.spec --noconfirm

# 5. Report
$ExePath = Join-Path $RepoDir "dist\HOMEPAGEServer-windows.exe"
if (Test-Path $ExePath) {
    $Size = (Get-Item $ExePath).Length / 1MB
    Write-Host ""
    Write-Host ("=== Build complete: dist\HOMEPAGEServer-windows.exe  ({0:N1} MB) ===" -f $Size) -ForegroundColor Green
} else {
    Write-Host "ERROR: Build failed — exe not found." -ForegroundColor Red
    exit 1
}
