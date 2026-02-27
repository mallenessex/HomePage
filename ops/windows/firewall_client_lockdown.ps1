param(
    [Parameter(Mandatory = $true)]
    [string]$ServerIP,
    [string]$ServerFQDN = "",
    [string]$RulePrefix = "HOMEPAGEClient",
    [switch]$AllowDns,
    [switch]$AllowNtp
)

$ErrorActionPreference = "Stop"

function Add-AllowRule {
    param(
        [string]$Name,
        [string]$Protocol,
        [string]$RemoteAddress,
        [string]$RemotePort
    )
    New-NetFirewallRule `
        -DisplayName "$RulePrefix - $Name" `
        -Direction Outbound `
        -Action Allow `
        -Profile Any `
        -Protocol $Protocol `
        -RemoteAddress $RemoteAddress `
        -RemotePort $RemotePort | Out-Null
}

Write-Host "Applying outbound allowlist rules for House Fantastico client..." -ForegroundColor Cyan

# 1) Allow HTTPS app traffic to your server only.
Add-AllowRule -Name "HTTPS to Server IP" -Protocol TCP -RemoteAddress $ServerIP -RemotePort "443"
if ($ServerFQDN -ne "") {
    # FQDN rules are supported on modern Windows builds; keep IP rule as primary.
    Add-AllowRule -Name "HTTPS to Server FQDN" -Protocol TCP -RemoteAddress $ServerFQDN -RemotePort "443"
}

# 2) Allow TURN control + media relay to your server only.
Add-AllowRule -Name "TURN TCP" -Protocol TCP -RemoteAddress $ServerIP -RemotePort "3478"
Add-AllowRule -Name "TURN UDP" -Protocol UDP -RemoteAddress $ServerIP -RemotePort "3478"
Add-AllowRule -Name "TURN Relay UDP Range" -Protocol UDP -RemoteAddress $ServerIP -RemotePort "49160-49200"

# 3) Optional system essentials.
if ($AllowDns) {
    Add-AllowRule -Name "DNS UDP" -Protocol UDP -RemoteAddress "Any" -RemotePort "53"
    Add-AllowRule -Name "DNS TCP" -Protocol TCP -RemoteAddress "Any" -RemotePort "53"
}
if ($AllowNtp) {
    Add-AllowRule -Name "NTP UDP" -Protocol UDP -RemoteAddress "Any" -RemotePort "123"
}

# 4) Block everything else outbound.
New-NetFirewallRule `
    -DisplayName "$RulePrefix - Block All Other Outbound" `
    -Direction Outbound `
    -Action Block `
    -Profile Any `
    -Protocol Any `
    -RemoteAddress Any | Out-Null

Write-Host "Done." -ForegroundColor Green
Write-Host "Rules created with prefix: $RulePrefix"
Write-Host "Use ops/windows/firewall_client_unlock.ps1 to remove these rules."
