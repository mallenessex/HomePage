param(
    [int]$AppHttpsPort = 8443,
    [int]$AppHttpPort = 8001,
    [int]$FallbackPort = 8001,
    [string]$ComposeFile = "podman-compose.yml"
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
$RunningOnWindows = ($env:OS -eq "Windows_NT")
if ($RunningOnWindows) {
    $env:HF_HOST_OS = "Windows"
} elseif ($IsMacOS) {
    $env:HF_HOST_OS = "Darwin"
} else {
    $env:HF_HOST_OS = "Linux"
}

$runtimeSecureEnv = Join-Path $PSScriptRoot "data\runtime_secure_mode.env"
$secureModeEnabled = "0"
$secureLocalDomain = ""
$secureLocalIp = ""
if (Test-Path $runtimeSecureEnv) {
    Get-Content $runtimeSecureEnv | ForEach-Object {
        if ($_ -match "^\s*SECURE_MODE_ENABLED=(.*)$") { $secureModeEnabled = $Matches[1].Trim() }
        if ($_ -match "^\s*SECURE_LOCAL_DOMAIN=(.*)$") { $secureLocalDomain = $Matches[1].Trim() }
        if ($_ -match "^\s*SECURE_LOCAL_IP=(.*)$") { $secureLocalIp = $Matches[1].Trim() }
    }
}

$lanIp = $null
try {
    if ($RunningOnWindows) {
        $cand = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object { $_.IPAddress -notmatch '^(127|169\.254)\.' -and $_.InterfaceAlias -notmatch 'Loopback|vEthernet|VirtualBox|VMware|Hyper-V|WSL' } |
            Sort-Object -Property InterfaceMetric |
            Select-Object -First 1 -ExpandProperty IPAddress
        if ($cand) { $lanIp = $cand }
    } else {
        $cand = (& sh -lc "hostname -I 2>/dev/null | awk '{print `$1}'").Trim()
        if ($cand) { $lanIp = $cand }
    }
} catch { }

if ($secureModeEnabled -in @("1", "true", "True", "TRUE") -and $secureLocalIp) {
    $lanIp = $secureLocalIp
}

if (-not $env:APP_DOMAIN) {
    if ($secureModeEnabled -in @("1", "true", "True", "TRUE") -and $secureLocalDomain) {
        $env:APP_DOMAIN = $secureLocalDomain
    } elseif ($lanIp) {
        $env:APP_DOMAIN = $lanIp
    } else {
        $env:APP_DOMAIN = "localhost"
    }
}

$env:APP_HTTP_PORT = "$AppHttpPort"
$env:APP_HTTPS_PORT = "$AppHttpsPort"
$env:APP_LAN_IP = if ($lanIp) { $lanIp } else { "127.0.0.1" }
$lanProxyStatus = "skipped"
$simpleUrlStatus = "skipped"
$hostsStatus = "skipped"

function Stop-StaleFallbackListener {
    if (-not $RunningOnWindows) { return }
    try {
        $listeners = Get-NetTCPConnection -LocalPort $FallbackPort -State Listen -ErrorAction SilentlyContinue
        if (-not $listeners) { return }
        foreach ($entry in $listeners) {
            $pid = [int]$entry.OwningProcess
            $proc = Get-Process -Id $pid -ErrorAction SilentlyContinue
            if (-not $proc) { continue }
            $name = ""
            if ($proc.ProcessName) {
                $name = $proc.ProcessName.ToLowerInvariant()
            }
            if ($name -in @("python", "python3", "uvicorn")) {
                Write-Host "Found stale $($proc.ProcessName) listener on fallback port $FallbackPort (PID $pid). Stopping it..."
                Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
            } else {
                Write-Host "WARN: Fallback port $FallbackPort is in use by $($proc.ProcessName) (PID $pid). Leaving it unchanged."
            }
        }
    } catch {
        Write-Host "WARN: Fallback listener cleanup failed: $($_.Exception.Message)"
    }
}

function Invoke-RoutePreflight {
    $pythonBin = $null
    if ($RunningOnWindows -and (Test-Path ".venv\Scripts\python.exe")) {
        $pythonBin = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
    } elseif ((Test-Path ".venv/bin/python")) {
        $pythonBin = Join-Path $PSScriptRoot ".venv/bin/python"
    } else {
        $py = Get-Command python -ErrorAction SilentlyContinue
        if (-not $py) { $py = Get-Command python3 -ErrorAction SilentlyContinue }
        if ($py) { $pythonBin = $py.Source }
    }

    if (-not $pythonBin) {
        Write-Host "WARN: Python not found; skipping route preflight."
        return
    }

    Write-Host "Running route preflight..."
    & $pythonBin "ops/route_preflight.py"
    if ($LASTEXITCODE -ne 0) {
        throw "Route preflight failed. Aborting launch."
    }
}

function Set-PortProxyRule {
    param(
        [int]$ListenPort,
        [int]$TargetPort,
        [string]$RuleName
    )

    & netsh interface portproxy delete v4tov4 listenaddress=0.0.0.0 listenport=$ListenPort *> $null
    & netsh interface portproxy delete v4tov6 listenaddress=0.0.0.0 listenport=$ListenPort *> $null

    & netsh interface portproxy add v4tov6 listenaddress=0.0.0.0 listenport=$ListenPort connectaddress=::1 connectport=$TargetPort *> $null
    if ($LASTEXITCODE -ne 0) {
        & netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport=$ListenPort connectaddress=127.0.0.1 connectport=$TargetPort *> $null
        if ($LASTEXITCODE -ne 0) {
            return $false
        }
    }

    & netsh advfirewall firewall show rule name=$RuleName *> $null
    if ($LASTEXITCODE -ne 0) {
        & netsh advfirewall firewall add rule name=$RuleName dir=in action=allow protocol=TCP localport=$ListenPort *> $null
    }
    return $true
}

function Configure-LanHttpsProxy {
    $script:lanProxyStatus = "skipped"
    $script:simpleUrlStatus = "skipped"
    $script:hostsStatus = "skipped"
    if (-not $RunningOnWindows) { return }
    if (-not $lanIp) { return }

    & net session *> $null
    if ($LASTEXITCODE -ne 0) {
        $script:lanProxyStatus = "not_admin"
        if ($secureModeEnabled -in @("1", "true", "True", "TRUE")) {
            $script:simpleUrlStatus = "not_admin"
            $script:hostsStatus = "not_admin"
        }
        return
    }

    if (Set-PortProxyRule -ListenPort $AppHttpsPort -TargetPort $AppHttpsPort -RuleName "HouseFantastico HTTPS $AppHttpsPort") {
        $script:lanProxyStatus = "configured"
    } else {
        $script:lanProxyStatus = "failed"
        return
    }
    if (-not (Set-PortProxyRule -ListenPort $AppHttpPort -TargetPort $AppHttpPort -RuleName "HouseFantastico HTTP $AppHttpPort")) {
        $script:lanProxyStatus = "failed"
        return
    }

    if ($secureModeEnabled -in @("1", "true", "True", "TRUE")) {
        $script:simpleUrlStatus = "configured"
        $script:hostsStatus = "configured"
        $hostsScript = Join-Path $PSScriptRoot "ops\windows\ensure_local_domain.ps1"
        if (Test-Path $hostsScript) {
            try {
                & $hostsScript -Domain $env:APP_DOMAIN *> $null
                if ($LASTEXITCODE -ne 0) {
                    $script:hostsStatus = "failed"
                }
            } catch {
                $script:hostsStatus = "failed"
            }
        } else {
            $script:hostsStatus = "failed"
        }
        if ($AppHttpsPort -ne 443) {
            if (-not (Set-PortProxyRule -ListenPort 443 -TargetPort $AppHttpsPort -RuleName "HouseFantastico HTTPS 443")) {
                $script:simpleUrlStatus = "failed"
            }
        }
        if ($AppHttpPort -ne 80) {
            if (-not (Set-PortProxyRule -ListenPort 80 -TargetPort $AppHttpPort -RuleName "HouseFantastico HTTP 80")) {
                $script:simpleUrlStatus = "failed"
            }
        }
    }
}

Stop-StaleFallbackListener
Invoke-RoutePreflight

Write-Host "Checking for existing listeners on HTTPS port $AppHttpsPort..."
try {
    if ($RunningOnWindows) {
        Get-NetTCPConnection -LocalPort $AppHttpsPort -State Listen -ErrorAction SilentlyContinue |
            Select-Object LocalAddress, LocalPort, OwningProcess | Format-Table -AutoSize
    } else {
        & sh -lc "lsof -nP -iTCP:$AppHttpsPort -sTCP:LISTEN || true"
    }
} catch {
    Write-Host "Listener check skipped: $($_.Exception.Message)"
}

function Start-ComposeStack {
    try {
        & podman compose version *> $null
        if ($LASTEXITCODE -eq 0) {
            & podman compose -f $ComposeFile up --build -d --remove-orphans
            return ($LASTEXITCODE -eq 0)
        }
    } catch { }

    try {
        & podman-compose -f $ComposeFile up --build -d --remove-orphans
        return ($LASTEXITCODE -eq 0)
    } catch { }

    return $false
}

$hasPodman = $null -ne (Get-Command podman -ErrorAction SilentlyContinue)
if ($hasPodman) {
    Write-Host "Starting containerized stack with Podman..."
    if (Start-ComposeStack) {
        Configure-LanHttpsProxy
        Write-Host ""
        Write-Host "MODE: HTTPS via Caddy (Podman stack)"
        Write-Host "App is starting in Podman:"
        Write-Host "- Main app:  https://localhost:$AppHttpsPort"
        Write-Host "- HTTP port: http://localhost:$AppHttpPort"
        if ($secureModeEnabled -in @("1", "true", "True", "TRUE")) {
            Write-Host "- Secure mode: enabled ($($env:APP_DOMAIN))"
            if ($simpleUrlStatus -eq "configured") {
                Write-Host "- First-run URL: http://$($env:APP_LAN_IP)/"
                Write-Host "- Preferred secure URL: https://$($env:APP_DOMAIN)/"
            } else {
                Write-Host "- First-run URL: http://$($env:APP_LAN_IP):$AppHttpPort"
                Write-Host "- Preferred secure URL: https://$($env:APP_DOMAIN):$AppHttpsPort"
            }
            if ($hostsStatus -eq "configured") {
                Write-Host "- Local hosts mapping: configured ($($env:APP_DOMAIN) -> 127.0.0.1 / ::1)"
            } elseif ($hostsStatus -eq "not_admin") {
                Write-Host "- Local hosts mapping not configured (run as Administrator)"
            } elseif ($hostsStatus -eq "failed") {
                Write-Host "- Local hosts mapping failed (check permissions on hosts file)"
            }
        }
        if ($lanIp) {
            if ($simpleUrlStatus -eq "configured") {
                Write-Host "- LAN target URL: https://$lanIp/"
            } else {
                Write-Host "- LAN target URL: https://${lanIp}:$AppHttpsPort"
            }
            if ($lanProxyStatus -eq "configured") {
                Write-Host "- LAN proxy: configured"
            } elseif ($lanProxyStatus -eq "not_admin") {
                Write-Host "- LAN HTTPS proxy not configured (run this script as Administrator for remote voice clients)"
            } elseif ($lanProxyStatus -eq "failed") {
                Write-Host "- LAN HTTPS proxy setup failed (try launching as Administrator)"
            }
            if ($simpleUrlStatus -eq "configured") {
                Write-Host "- Standard URL ports: configured (80/443)"
            } elseif ($simpleUrlStatus -eq "not_admin") {
                Write-Host "- Standard URL ports not configured (run as Administrator for https://$($env:APP_DOMAIN)/)"
            } elseif ($simpleUrlStatus -eq "failed") {
                Write-Host "- Standard URL port setup failed (try launching as Administrator)"
            }
        }
        Write-Host ""
        Write-Host "Use: podman compose -f $ComposeFile logs -f"
        Write-Host "Note: first-run certificate is issued by local internal CA."
        Write-Host ""
        Write-Host "======================================="
        Write-Host "LOCAL ADMIN URL: https://localhost:$AppHttpsPort/"
        if ($secureModeEnabled -in @("1", "true", "True", "TRUE")) {
            if ($simpleUrlStatus -eq "configured") {
                Write-Host "Secure-mode URL: https://$($env:APP_DOMAIN)/"
            } else {
                Write-Host "Secure-mode URL: https://$($env:APP_DOMAIN):$AppHttpsPort/"
            }
        }
        Write-Host "======================================="
        exit 0
    }
    Write-Host "Podman startup failed. Falling back to local uvicorn."
} else {
    Write-Host "Podman not found. Falling back to local uvicorn."
}

if ($RunningOnWindows) {
    if (Test-Path ".venv\Scripts\activate") {
        & ".venv\Scripts\Activate.ps1"
    }
} else {
    if (Test-Path ".venv/bin/activate") {
        & sh -lc ". .venv/bin/activate; uvicorn app.main:app --host 0.0.0.0 --port $FallbackPort"
        exit $LASTEXITCODE
    }
}

Write-Host "MODE: HTTP fallback via local uvicorn"
Write-Host "Starting local fallback on 0.0.0.0:$FallbackPort (HTTP, no TLS proxy)..."
Write-Host "======================================="
Write-Host "LOCAL ADMIN URL: http://localhost:$FallbackPort/"
if ($lanIp) {
    Write-Host "LAN URL: http://${lanIp}:$FallbackPort/"
}
Write-Host "======================================="
& uvicorn app.main:app --host 0.0.0.0 --port $FallbackPort
