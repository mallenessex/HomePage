param(
    [string]$Distro = "Ubuntu"
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    throw "WSL is not installed. Install WSL first: wsl --install"
}

$repoWin = (Get-Location).Path
$distros = @(wsl.exe -l -q 2>$null | ForEach-Object { $_.Trim() } | Where-Object { $_ })
if (-not $distros -or $distros.Count -eq 0) {
    throw "No WSL distros found. Install one first, e.g. Ubuntu."
}

if ($distros -notcontains $Distro) {
    $available = ($distros -join ", ")
    throw "WSL distro '$Distro' not found. Available: $available"
}

# Convert Windows path (e.g. C:\Users\me\repo) to WSL mount path (/mnt/c/Users/me/repo)
if ($repoWin -notmatch '^[A-Za-z]:\\') {
    throw "Unsupported Windows path format: $repoWin"
}
$drive = $repoWin.Substring(0,1).ToLowerInvariant()
$rest = $repoWin.Substring(2).Replace('\','/')
$repoWsl = "/mnt/$drive$rest"

# Verify path exists in selected distro
$probe = @(wsl.exe -d $Distro bash -lc "test -d '$repoWsl' && echo OK" 2>$null)
if (-not $probe -or $probe[0].ToString().Trim() -ne "OK") {
    throw "Resolved WSL path does not exist in '$Distro': $repoWsl"
}

Write-Host "Using WSL distro: $Distro" -ForegroundColor Cyan
Write-Host "Repo path in WSL: $repoWsl" -ForegroundColor Cyan

$script = @"
set -euo pipefail
cd '$repoWsl'

sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-venv python3-pip python3-tk \
    libfuse2 file

chmod +x build_client_linux.sh
./build_client_linux.sh
echo
echo 'Linux AppImage created at: $repoWsl/dist/HomepageClient-linux-x86_64.AppImage'
"@

wsl.exe -d $Distro bash -lc $script

Write-Host "Done. Check dist/HomepageClient-linux-x86_64.AppImage in this repo." -ForegroundColor Green
