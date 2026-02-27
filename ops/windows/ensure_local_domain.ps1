param(
    [Parameter(Mandatory = $true)]
    [string]$Domain
)

$ErrorActionPreference = "Stop"

$clean = if ($null -eq $Domain) { "" } else { [string]$Domain }
$clean = $clean.Trim().ToLowerInvariant()
if (-not $clean) {
    throw "Domain is empty."
}

$hostsPath = Join-Path $env:SystemRoot "System32\drivers\etc\hosts"
if (-not (Test-Path $hostsPath)) {
    throw "Hosts file not found: $hostsPath"
}

$escaped = [regex]::Escape($clean)
$pattern = "(?i)(^|\s)$escaped(\s|$)"

$lines = Get-Content -Path $hostsPath -ErrorAction SilentlyContinue
if (-not $lines) { $lines = @() }

$filtered = @()
foreach ($line in $lines) {
    if ($line -match $pattern) {
        continue
    }
    $filtered += $line
}

$filtered += "127.0.0.1 $clean"
$filtered += "::1 $clean"

Set-Content -Path $hostsPath -Value $filtered -Encoding Ascii
Write-Output "Hosts updated for $clean"
