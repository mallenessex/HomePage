@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "RUN_SERVER_SELF_ELEVATED=0"
if /I "%~1"=="--elevated" (
  set "RUN_SERVER_SELF_ELEVATED=1"
  shift /1
)

cd /d %~dp0
set "HF_HOST_OS=Windows"
set "RUN_SERVER_HOLD_OPEN=1"
if /I "%RUN_SERVER_NO_HOLD%"=="1" set "RUN_SERVER_HOLD_OPEN=0"
if not defined RUN_SERVER_AUTO_ELEVATE set "RUN_SERVER_AUTO_ELEVATE=1"
if not defined RUN_SERVER_REQUIRE_LAN_PROXY set "RUN_SERVER_REQUIRE_LAN_PROXY=1"
set "RUNTIME_SECURE_ENV=data\runtime_secure_mode.env"
set "SECURE_MODE_ENABLED=0"
set "SECURE_LOCAL_DOMAIN="
set "SECURE_LOCAL_IP="
if exist "%RUNTIME_SECURE_ENV%" (
  for /f "usebackq tokens=1,* delims==" %%A in ("%RUNTIME_SECURE_ENV%") do (
    if /I "%%A"=="SECURE_MODE_ENABLED" set "SECURE_MODE_ENABLED=%%B"
    if /I "%%A"=="SECURE_LOCAL_DOMAIN" set "SECURE_LOCAL_DOMAIN=%%B"
    if /I "%%A"=="SECURE_LOCAL_IP" set "SECURE_LOCAL_IP=%%B"
  )
)
if "%SECURE_MODE_ENABLED%"=="" set "SECURE_MODE_ENABLED=0"
if not defined APP_HTTP_PORT set "APP_HTTP_PORT=8001"
if not defined APP_HTTPS_PORT set "APP_HTTPS_PORT=8443"
set "FALLBACK_PORT=8001"
set "LAN_IP="
call :detect_lan_ip
if /I "%SECURE_MODE_ENABLED%"=="1" if defined SECURE_LOCAL_IP set "LAN_IP=%SECURE_LOCAL_IP%"
if not defined APP_DOMAIN (
  if /I "%SECURE_MODE_ENABLED%"=="1" if defined SECURE_LOCAL_DOMAIN (
    set "APP_DOMAIN=%SECURE_LOCAL_DOMAIN%"
  ) else if defined LAN_IP (
    set "APP_DOMAIN=%LAN_IP%"
  ) else (
    set "APP_DOMAIN=localhost"
  )
)
if not defined PODMAN_DEFAULT_ROOTLESS_NETWORK_CMD set "PODMAN_DEFAULT_ROOTLESS_NETWORK_CMD=slirp4netns"
if not defined PODMAN_IGNORE_CGROUPSV1_WARNING set "PODMAN_IGNORE_CGROUPSV1_WARNING=1"
if not defined LAN_IP set "LAN_IP=%APP_DOMAIN%"
if /I "%LAN_IP%"=="localhost" set "LAN_IP="
if /I "%LAN_IP%"=="127.0.0.1" set "LAN_IP="
if defined LAN_IP (
  set "APP_LAN_IP=%LAN_IP%"
) else (
  set "APP_LAN_IP=127.0.0.1"
)
call :ensure_admin_for_lan_proxy
if /I "%RUN_SERVER_RELAUNCHED_AS_ADMIN%"=="1" goto :eof
set "LAN_PROXY_STATUS=skipped"
set "SIMPLE_URL_STATUS=skipped"
set "HOSTS_STATUS=skipped"
set "APP_PORT=%APP_HTTPS_PORT%"
set "COMPOSE_FILE=podman-compose.yml"
if /I not "%RUN_SERVER_CLEAN_START%"=="0" (
  call :stop_existing_background_runtime
) else (
  call :stop_stale_fallback_listener
)

set "PYTHON_BIN="
if exist ".venv\Scripts\python.exe" set "PYTHON_BIN=.venv\Scripts\python.exe"
if not defined PYTHON_BIN (
  where python >nul 2>&1
  if not errorlevel 1 set "PYTHON_BIN=python"
)
if defined PYTHON_BIN (
  echo Running route preflight...
  "%PYTHON_BIN%" ops\route_preflight.py
  if errorlevel 1 (
    echo Route preflight failed. Aborting launch.
    call :finish_and_hold 1
    goto :eof
  )
) else (
  echo WARN: Python not found; skipping route preflight.
)

echo Checking for existing listeners on HTTPS port %APP_PORT%...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%APP_PORT% .*LISTENING"') do (
  echo Existing PID %%P is listening on %APP_PORT%.
)

set "PODMAN_CMD=podman"
set "COMPOSE_FAILED=0"
where podman >nul 2>&1
if errorlevel 1 (
  if exist "C:\Program Files\RedHat\Podman\podman.exe" (
    set "PODMAN_CMD=C:\Program Files\RedHat\Podman\podman.exe"
  ) else (
    echo Podman CLI was not found in PATH.
    set "COMPOSE_FAILED=1"
  )
)

echo Starting containerized stack with Podman...
"%PODMAN_CMD%" compose version >nul 2>&1
if errorlevel 1 (
  where podman-compose >nul 2>&1
  if errorlevel 1 (
    echo podman compose and podman-compose were not found in Windows shell.
    set "COMPOSE_FAILED=1"
  ) else (
    podman-compose -f "%COMPOSE_FILE%" up --build -d --remove-orphans
    if errorlevel 1 set "COMPOSE_FAILED=1"
  )
) else (
  "%PODMAN_CMD%" compose -f "%COMPOSE_FILE%" up --build -d --remove-orphans
  if errorlevel 1 set "COMPOSE_FAILED=1"
)
if /I "%COMPOSE_FAILED%"=="1" (
  call :compose_failed_flow
  goto :eof
)
call :configure_lan_https_proxy

echo.
echo MODE: HTTPS via Caddy (Podman stack)
echo App is starting in Podman:
echo - Main app:  https://localhost:%APP_HTTPS_PORT%
echo - HTTP port: http://localhost:%APP_HTTP_PORT%
echo - Note: use `localhost` for HTTPS (not 127.0.0.1)
if /I "%SECURE_MODE_ENABLED%"=="1" (
  echo - Secure mode: enabled ^(%APP_DOMAIN%^)
  if /I "%SIMPLE_URL_STATUS%"=="configured" (
    if /I "%LAN_PROXY_STATUS%"=="configured" (
      if defined APP_LAN_IP echo - First-run URL: http://%APP_LAN_IP%/
    ) else (
      echo - First-run URL: http://localhost:%APP_HTTP_PORT%
    )
    echo - Preferred secure URL: https://%APP_DOMAIN%/
  ) else (
    echo - First-run URL: http://localhost:%APP_HTTP_PORT%
    echo - Preferred secure URL: https://%APP_DOMAIN%:%APP_HTTPS_PORT%
  )
)
if /I "%SECURE_MODE_ENABLED%"=="1" (
  if /I "%HOSTS_STATUS%"=="configured" (
    echo - Local hosts mapping: configured ^(%APP_DOMAIN% -> 127.0.0.1 / ::1^)
  ) else if /I "%HOSTS_STATUS%"=="not_admin" (
    echo - Local hosts mapping not configured ^(run as Administrator^)
  ) else if /I "%HOSTS_STATUS%"=="failed" (
    echo - Local hosts mapping failed ^(check permissions on hosts file^)
  )
)
if defined LAN_IP (
  if /I "%LAN_PROXY_STATUS%"=="configured" (
    if /I "%SIMPLE_URL_STATUS%"=="configured" (
      echo - LAN target URL: https://%LAN_IP%/
    ) else (
      echo - LAN target URL: https://%LAN_IP%:%APP_HTTPS_PORT%
    )
  ) else (
    echo - LAN target URL unavailable ^(run this script as Administrator^)
  )
  if /I "%LAN_PROXY_STATUS%"=="configured" (
    echo - LAN proxy: configured
  ) else if /I "%LAN_PROXY_STATUS%"=="not_admin" (
    echo - LAN HTTPS proxy not configured ^(run this script as Administrator for remote voice clients^)
  ) else if /I "%LAN_PROXY_STATUS%"=="failed" (
    echo - LAN HTTPS proxy setup failed ^(try launching as Administrator^)
  )
  if /I "%SIMPLE_URL_STATUS%"=="configured" (
    echo - Standard URL ports: configured ^(80/443^)
  ) else if /I "%SIMPLE_URL_STATUS%"=="not_admin" (
    echo - Standard URL ports not configured ^(run as Administrator for https://%APP_DOMAIN%/^)
  ) else if /I "%SIMPLE_URL_STATUS%"=="failed" (
    echo - Standard URL port setup failed ^(try launching as Administrator^)
  )
)
echo.
echo Use `podman compose -f %COMPOSE_FILE% logs -f` for logs.
echo Note: first-run certificate is issued by local internal CA.
echo.
echo =======================================
echo LOCAL ADMIN URL: https://localhost:%APP_HTTPS_PORT%/
if /I "%SECURE_MODE_ENABLED%"=="1" (
  if /I "%SIMPLE_URL_STATUS%"=="configured" (
    echo Secure-mode URL: https://%APP_DOMAIN%/
  ) else (
    echo Secure-mode URL: https://%APP_DOMAIN%:%APP_HTTPS_PORT%/
  )
)
echo =======================================
call :finish_and_hold 0
goto :eof

:compose_failed_flow
echo Podman startup via Windows CLI failed.
echo Attempting WSL Podman fallback (podman-machine-default)...
for /f "delims=" %%I in ('wsl.exe -d podman-machine-default -u user wslpath -a "%CD%" 2^>nul') do set "REPO_WSL=%%I"
if not defined REPO_WSL (
  echo WSL path translation failed for repo: %CD%
  goto :fallback_local
)
wsl.exe -d podman-machine-default -u user sh -lc "cd '%REPO_WSL%' && APP_DOMAIN='%APP_DOMAIN%' APP_LAN_IP='%APP_LAN_IP%' APP_HTTP_PORT='%APP_HTTP_PORT%' APP_HTTPS_PORT='%APP_HTTPS_PORT%' HF_HOST_OS='%HF_HOST_OS%' PODMAN_DEFAULT_ROOTLESS_NETWORK_CMD='%PODMAN_DEFAULT_ROOTLESS_NETWORK_CMD%' PODMAN_IGNORE_CGROUPSV1_WARNING='%PODMAN_IGNORE_CGROUPSV1_WARNING%' podman-compose -f '%COMPOSE_FILE%' up --build -d --remove-orphans"
if errorlevel 1 (
  echo WSL Podman compose fallback failed.
  goto :fallback_local
)
call :configure_lan_https_proxy
echo.
echo MODE: HTTPS via Caddy (Podman stack, WSL fallback)
echo App is starting in Podman:
echo - Main app:  https://localhost:%APP_HTTPS_PORT%
echo - HTTP port: http://localhost:%APP_HTTP_PORT%
echo - Note: use `localhost` for HTTPS (not 127.0.0.1)
if /I "%SECURE_MODE_ENABLED%"=="1" (
  echo - Secure mode: enabled ^(%APP_DOMAIN%^)
  if /I "%SIMPLE_URL_STATUS%"=="configured" (
    if /I "%LAN_PROXY_STATUS%"=="configured" (
      if defined APP_LAN_IP echo - First-run URL: http://%APP_LAN_IP%/
    ) else (
      echo - First-run URL: http://localhost:%APP_HTTP_PORT%
    )
    echo - Preferred secure URL: https://%APP_DOMAIN%/
  ) else (
    echo - First-run URL: http://localhost:%APP_HTTP_PORT%
    echo - Preferred secure URL: https://%APP_DOMAIN%:%APP_HTTPS_PORT%
  )
)
if /I "%SECURE_MODE_ENABLED%"=="1" (
  if /I "%HOSTS_STATUS%"=="configured" (
    echo - Local hosts mapping: configured ^(%APP_DOMAIN% -> 127.0.0.1 / ::1^)
  ) else if /I "%HOSTS_STATUS%"=="not_admin" (
    echo - Local hosts mapping not configured ^(run as Administrator^)
  ) else if /I "%HOSTS_STATUS%"=="failed" (
    echo - Local hosts mapping failed ^(check permissions on hosts file^)
  )
)
if defined LAN_IP (
  if /I "%LAN_PROXY_STATUS%"=="configured" (
    if /I "%SIMPLE_URL_STATUS%"=="configured" (
      echo - LAN target URL: https://%LAN_IP%/
    ) else (
      echo - LAN target URL: https://%LAN_IP%:%APP_HTTPS_PORT%
    )
  ) else (
    echo - LAN target URL unavailable ^(run this script as Administrator^)
  )
  if /I "%LAN_PROXY_STATUS%"=="configured" (
    echo - LAN proxy: configured
  ) else if /I "%LAN_PROXY_STATUS%"=="not_admin" (
    echo - LAN HTTPS proxy not configured ^(run this script as Administrator for remote voice clients^)
  ) else if /I "%LAN_PROXY_STATUS%"=="failed" (
    echo - LAN HTTPS proxy setup failed ^(try launching as Administrator^)
  )
  if /I "%SIMPLE_URL_STATUS%"=="configured" (
    echo - Standard URL ports: configured ^(80/443^)
  ) else if /I "%SIMPLE_URL_STATUS%"=="not_admin" (
    echo - Standard URL ports not configured ^(run as Administrator for https://%APP_DOMAIN%/^)
  ) else if /I "%SIMPLE_URL_STATUS%"=="failed" (
    echo - Standard URL port setup failed ^(try launching as Administrator^)
  )
)
echo.
echo Use `wsl -d podman-machine-default -u user sh -lc "PODMAN_IGNORE_CGROUPSV1_WARNING=1 podman compose -f %COMPOSE_FILE% logs -f"` for logs.
echo Note: first-run certificate is issued by local internal CA.
echo.
echo =======================================
echo LOCAL ADMIN URL: https://localhost:%APP_HTTPS_PORT%/
if /I "%SECURE_MODE_ENABLED%"=="1" (
  if /I "%SIMPLE_URL_STATUS%"=="configured" (
    echo Secure-mode URL: https://%APP_DOMAIN%/
  ) else (
    echo Secure-mode URL: https://%APP_DOMAIN%:%APP_HTTPS_PORT%/
  )
)
echo =======================================
call :finish_and_hold 0
goto :eof


:fallback_local
if exist ".venv\Scripts\activate" (
  call .venv\Scripts\activate
)
echo MODE: HTTP fallback via local uvicorn
echo Starting local fallback on 0.0.0.0:%FALLBACK_PORT% (HTTP, no TLS proxy)...
echo =======================================
echo LOCAL ADMIN URL: http://localhost:%FALLBACK_PORT%/
if defined LAN_IP echo LAN URL: http://%LAN_IP%:%FALLBACK_PORT%/
echo =======================================
uvicorn app.main:app --host 0.0.0.0 --port %FALLBACK_PORT%

call :finish_and_hold 0
goto :eof

:detect_lan_ip
for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "$all = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -notmatch '^(127|169\.254)\.' -and $_.InterfaceAlias -notmatch 'Loopback|vEthernet|VirtualBox|VMware|Hyper-V|WSL' }; $private = $all | Where-Object { $_.IPAddress -match '^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[0-1])\.)' }; $pick = if($private){$private}else{$all}; $ip = $pick | Sort-Object -Property InterfaceMetric | Select-Object -First 1 -ExpandProperty IPAddress; if($ip){$ip}"`) do (
  set "LAN_IP=%%I"
)
if defined LAN_IP exit /b 0
for /f "tokens=2 delims=:" %%I in ('ipconfig ^| findstr /R /C:"IPv4 Address"') do (
  set "CANDIDATE=%%I"
  set "CANDIDATE=!CANDIDATE: =!"
  if not "!CANDIDATE!"=="" if not "!CANDIDATE!"=="127.0.0.1" if not "!CANDIDATE:~0,8!"=="169.254." (
    set "LAN_IP=!CANDIDATE!"
    goto :detect_lan_done
  )
)
:detect_lan_done
exit /b 0

:ensure_admin_for_lan_proxy
set "RUN_SERVER_RELAUNCHED_AS_ADMIN=0"
if /I "%RUN_SERVER_REQUIRE_LAN_PROXY%"=="0" exit /b 0
if not defined LAN_IP exit /b 0

net session >nul 2>&1
if not errorlevel 1 exit /b 0

if /I "%RUN_SERVER_AUTO_ELEVATE%"=="0" (
  echo WARN: LAN proxy requires Administrator privileges. Auto-elevation is disabled.
  exit /b 0
)

if /I "%RUN_SERVER_SELF_ELEVATED%"=="1" (
  echo WARN: Elevated relaunch did not obtain Administrator privileges. Continuing without LAN proxy.
  exit /b 0
)

echo LAN proxy requires Administrator privileges. Requesting elevation...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -Verb RunAs -WorkingDirectory '%CD%' -FilePath '%~f0' -ArgumentList '--elevated'" >nul 2>&1
if errorlevel 1 (
  echo WARN: Elevation was canceled or failed. Continuing without LAN proxy.
  exit /b 0
)

echo Elevated launcher started. Closing this non-admin window.
set "RUN_SERVER_RELAUNCHED_AS_ADMIN=1"
exit /b 0

:configure_lan_https_proxy
set "LAN_PROXY_STATUS=skipped"
set "SIMPLE_URL_STATUS=skipped"
set "HOSTS_STATUS=skipped"
if not defined LAN_IP exit /b 0
net session >nul 2>&1
if errorlevel 1 (
  set "LAN_PROXY_STATUS=not_admin"
  if /I "%SECURE_MODE_ENABLED%"=="1" (
    set "SIMPLE_URL_STATUS=not_admin"
    set "HOSTS_STATUS=not_admin"
  )
  exit /b 0
)
call :configure_portproxy_rule %APP_HTTPS_PORT% %APP_HTTPS_PORT% "HTTPS %APP_HTTPS_PORT%"
if errorlevel 1 (
  set "LAN_PROXY_STATUS=failed"
  exit /b 0
)
if not "%APP_HTTP_PORT%"=="" (
  call :configure_portproxy_rule %APP_HTTP_PORT% %APP_HTTP_PORT% "HTTP %APP_HTTP_PORT%"
  if errorlevel 1 (
    set "LAN_PROXY_STATUS=failed"
    exit /b 0
  )
)
set "LAN_PROXY_STATUS=configured"

if /I "%SECURE_MODE_ENABLED%"=="1" (
  set "SIMPLE_URL_STATUS=configured"
  set "HOSTS_STATUS=configured"
  powershell -NoProfile -ExecutionPolicy Bypass -File "ops\\windows\\ensure_local_domain.ps1" -Domain "%APP_DOMAIN%" >nul 2>&1
  if errorlevel 1 set "HOSTS_STATUS=failed"
  if not "%APP_HTTPS_PORT%"=="443" (
    call :configure_portproxy_rule 443 %APP_HTTPS_PORT% "HTTPS 443"
    if errorlevel 1 set "SIMPLE_URL_STATUS=failed"
  )
  if not "%APP_HTTP_PORT%"=="80" (
    call :configure_portproxy_rule 80 %APP_HTTP_PORT% "HTTP 80"
    if errorlevel 1 set "SIMPLE_URL_STATUS=failed"
  )
)
exit /b 0

:configure_portproxy_rule
set "LISTEN_PORT=%~1"
set "TARGET_PORT=%~2"
set "RULE_SUFFIX=%~3"
if "%LISTEN_PORT%"=="" exit /b 1
if "%TARGET_PORT%"=="" exit /b 1

netsh interface portproxy delete v4tov4 listenaddress=0.0.0.0 listenport=%LISTEN_PORT% >nul 2>&1
netsh interface portproxy delete v4tov6 listenaddress=0.0.0.0 listenport=%LISTEN_PORT% >nul 2>&1
netsh interface portproxy add v4tov6 listenaddress=0.0.0.0 listenport=%LISTEN_PORT% connectaddress=::1 connectport=%TARGET_PORT% >nul 2>&1
if errorlevel 1 (
  netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport=%LISTEN_PORT% connectaddress=127.0.0.1 connectport=%TARGET_PORT% >nul 2>&1
  if errorlevel 1 exit /b 1
)

netsh advfirewall firewall show rule name="HouseFantastico %RULE_SUFFIX%" >nul 2>&1
if errorlevel 1 (
  netsh advfirewall firewall add rule name="HouseFantastico %RULE_SUFFIX%" dir=in action=allow protocol=TCP localport=%LISTEN_PORT% >nul 2>&1
)
exit /b 0

:stop_existing_background_runtime
echo Checking for existing background runtime instances...
call :stop_compose_stack_windows
call :stop_compose_stack_wsl
call :stop_listener_if_safe %APP_HTTPS_PORT%
call :stop_listener_if_safe %APP_HTTP_PORT%
call :stop_listener_if_safe %FALLBACK_PORT%
exit /b 0

:stop_compose_stack_windows
set "LOCAL_PODMAN_CMD=podman"
where podman >nul 2>&1
if errorlevel 1 (
  if exist "C:\Program Files\RedHat\Podman\podman.exe" (
    set "LOCAL_PODMAN_CMD=C:\Program Files\RedHat\Podman\podman.exe"
  ) else (
    exit /b 0
  )
)
"%LOCAL_PODMAN_CMD%" compose version >nul 2>&1
if not errorlevel 1 (
  echo Stopping existing Podman compose stack via Windows CLI...
  "%LOCAL_PODMAN_CMD%" compose -f "%COMPOSE_FILE%" down --remove-orphans >nul 2>&1
  exit /b 0
)
where podman-compose >nul 2>&1
if not errorlevel 1 (
  echo Stopping existing podman-compose stack via Windows CLI...
  podman-compose -f "%COMPOSE_FILE%" down --remove-orphans >nul 2>&1
)
exit /b 0

:stop_compose_stack_wsl
where wsl.exe >nul 2>&1
if errorlevel 1 exit /b 0
for /f "delims=" %%I in ('wsl.exe -d podman-machine-default -u user wslpath -a "%CD%" 2^>nul') do set "REPO_WSL_PRESTOP=%%I"
if not defined REPO_WSL_PRESTOP exit /b 0
echo Stopping existing Podman compose stack via WSL fallback...
wsl.exe -d podman-machine-default -u user sh -lc "cd '%REPO_WSL_PRESTOP%' && (podman compose -f '%COMPOSE_FILE%' down --remove-orphans >/dev/null 2>&1 || podman-compose -f '%COMPOSE_FILE%' down --remove-orphans >/dev/null 2>&1 || true)" >nul 2>&1
exit /b 0

:stop_listener_if_safe
set "TARGET_STOP_PORT=%~1"
if "%TARGET_STOP_PORT%"=="" exit /b 0
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%TARGET_STOP_PORT% .*LISTENING" ^| sort /unique') do (
  set "STOP_PID=%%P"
  if not "!STOP_PID!"=="" (
    set "STOP_PROC_NAME="
    for /f "tokens=1 delims=," %%N in ('tasklist /FI "PID eq !STOP_PID!" /FO CSV /NH ^| findstr /V /I "INFO:"') do (
      set "STOP_PROC_NAME=%%~N"
    )
    set "STOP_PROC_NAME=!STOP_PROC_NAME:\"=!"
    if "!STOP_PROC_NAME!"=="" (
      rem PID no longer exists; skip quietly.
    ) else if /I "!STOP_PROC_NAME!"=="python.exe" (
      echo Stopping !STOP_PROC_NAME! listener on port %TARGET_STOP_PORT% ^(PID !STOP_PID!^).
      taskkill /F /PID !STOP_PID! >nul 2>&1
    ) else if /I "!STOP_PROC_NAME!"=="uvicorn.exe" (
      echo Stopping !STOP_PROC_NAME! listener on port %TARGET_STOP_PORT% ^(PID !STOP_PID!^).
      taskkill /F /PID !STOP_PID! >nul 2>&1
    ) else if /I "!STOP_PROC_NAME!"=="wslrelay.exe" (
      echo Stopping !STOP_PROC_NAME! listener on port %TARGET_STOP_PORT% ^(PID !STOP_PID!^).
      taskkill /F /PID !STOP_PID! >nul 2>&1
    ) else (
      echo WARN: Port %TARGET_STOP_PORT% is in use by !STOP_PROC_NAME! ^(PID !STOP_PID!^). Leaving it unchanged.
    )
  )
)
exit /b 0

:stop_stale_fallback_listener
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%FALLBACK_PORT% .*LISTENING" ^| sort /unique') do (
  set "STALE_PID=%%P"
  if not "!STALE_PID!"=="" (
    set "PROC_NAME="
    for /f "tokens=1 delims=," %%N in ('tasklist /FI "PID eq !STALE_PID!" /FO CSV /NH ^| findstr /V /I "INFO:"') do (
      set "PROC_NAME=%%~N"
    )
    set "PROC_NAME=!PROC_NAME:\"=!"
    if "!PROC_NAME!"=="" (
      rem PID no longer exists; skip quietly.
    ) else if /I "!PROC_NAME!"=="python.exe" (
      echo Found stale Python listener on fallback port %FALLBACK_PORT% ^(PID !STALE_PID!^). Stopping it...
      taskkill /F /PID !STALE_PID! >nul 2>&1
    ) else if /I "!PROC_NAME!"=="uvicorn.exe" (
      echo Found stale Uvicorn listener on fallback port %FALLBACK_PORT% ^(PID !STALE_PID!^). Stopping it...
      taskkill /F /PID !STALE_PID! >nul 2>&1
    ) else (
      echo WARN: Port %FALLBACK_PORT% is in use by !PROC_NAME! ^(PID !STALE_PID!^). Leaving it unchanged.
    )
  )
)
exit /b 0

:finish_and_hold
set "EXIT_CODE=%~1"
if "%EXIT_CODE%"=="" set "EXIT_CODE=0"
if /I "%RUN_SERVER_HOLD_OPEN%"=="1" (
  echo.
  echo Launcher finished. This window is being kept open for review.
  echo Type EXIT to close it.
  cmd /k
)
exit /b %EXIT_CODE%
