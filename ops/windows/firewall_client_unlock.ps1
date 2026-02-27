param(
    [string]$RulePrefix = "HOMEPAGEClient"
)

$ErrorActionPreference = "Stop"

Write-Host "Removing firewall rules with prefix '$RulePrefix'..." -ForegroundColor Yellow

$rules = Get-NetFirewallRule -DisplayName "$RulePrefix*" -ErrorAction SilentlyContinue
if ($null -eq $rules) {
    Write-Host "No matching rules found."
    exit 0
}

$rules | Remove-NetFirewallRule
Write-Host "Done." -ForegroundColor Green
