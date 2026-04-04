#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

RUNTIME_SECURE_ENV="${RUNTIME_SECURE_ENV:-data/runtime_secure_mode.env}"
SECURE_MODE_ENABLED="0"
SECURE_LOCAL_DOMAIN=""
SECURE_LOCAL_IP=""

if [[ -f "$RUNTIME_SECURE_ENV" ]]; then
  while IFS='=' read -r key value; do
    case "$key" in
      SECURE_MODE_ENABLED) SECURE_MODE_ENABLED="${value}" ;;
      SECURE_LOCAL_DOMAIN) SECURE_LOCAL_DOMAIN="${value}" ;;
      SECURE_LOCAL_IP) SECURE_LOCAL_IP="${value}" ;;
    esac
  done < "$RUNTIME_SECURE_ENV"
fi

APP_HTTP_PORT="${APP_HTTP_PORT:-8001}"
APP_HTTPS_PORT="${APP_HTTPS_PORT:-8443}"
APP_PORT="${APP_HTTPS_PORT}"
FALLBACK_PORT="${FALLBACK_PORT:-8001}"
COMPOSE_FILE="${COMPOSE_FILE:-podman-compose.yml}"

if [[ -z "${HF_HOST_OS:-}" ]]; then
  case "$(uname -s 2>/dev/null || echo Linux)" in
    Darwin*) HF_HOST_OS="Darwin" ;;
    Linux*) HF_HOST_OS="Linux" ;;
    MINGW*|MSYS*|CYGWIN*) HF_HOST_OS="Windows" ;;
    *) HF_HOST_OS="Linux" ;;
  esac
fi

LAN_IP=""
if command -v hostname >/dev/null 2>&1; then
  LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
fi
if [[ "${SECURE_MODE_ENABLED,,}" == "1" && -n "$SECURE_LOCAL_IP" ]]; then
  LAN_IP="$SECURE_LOCAL_IP"
fi
if [[ -z "${APP_DOMAIN:-}" ]]; then
  if [[ "${SECURE_MODE_ENABLED,,}" == "1" && -n "$SECURE_LOCAL_DOMAIN" ]]; then
    APP_DOMAIN="$SECURE_LOCAL_DOMAIN"
  elif [[ -n "$LAN_IP" ]]; then
    APP_DOMAIN="$LAN_IP"
  else
    APP_DOMAIN="localhost"
  fi
fi
APP_LAN_IP="${APP_LAN_IP:-${LAN_IP:-127.0.0.1}}"

export APP_DOMAIN APP_LAN_IP APP_HTTP_PORT APP_HTTPS_PORT HF_HOST_OS

ensure_runtime_dirs() {
  mkdir -p "media"
  mkdir -p "$(dirname "$RUNTIME_SECURE_ENV")"
}

stop_stale_fallback_listener() {
  local pids=""
  if command -v lsof >/dev/null 2>&1; then
    pids="$(lsof -t -iTCP:"${FALLBACK_PORT}" -sTCP:LISTEN 2>/dev/null || true)"
  elif command -v ss >/dev/null 2>&1; then
    pids="$(ss -ltnp "sport = :${FALLBACK_PORT}" 2>/dev/null | sed -n 's/.*pid=\([0-9]\+\).*/\1/p' || true)"
  fi
  [[ -z "$pids" ]] && return 0

  for pid in $pids; do
    local comm
    comm="$(ps -p "$pid" -o comm= 2>/dev/null || true)"
    case "${comm}" in
      python|python3|uvicorn)
        echo "Found stale ${comm} listener on fallback port ${FALLBACK_PORT} (PID ${pid}). Stopping it..."
        kill -9 "$pid" >/dev/null 2>&1 || true
        ;;
      *)
        echo "WARN: Fallback port ${FALLBACK_PORT} is in use by ${comm:-unknown} (PID ${pid}). Leaving it unchanged."
        ;;
    esac
  done
}

run_route_preflight() {
  local py=""
  if [[ -x ".venv/bin/python" ]]; then
    py=".venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    py="$(command -v python3)"
  elif command -v python >/dev/null 2>&1; then
    py="$(command -v python)"
  fi

  if [[ -z "$py" ]]; then
    echo "WARN: Python not found; skipping route preflight."
    return 0
  fi

  echo "Running route preflight..."
  "$py" ops/route_preflight.py
}

stop_stale_fallback_listener
ensure_runtime_dirs
run_route_preflight

echo "Checking for existing listeners on HTTPS port ${APP_PORT}..."
if command -v lsof >/dev/null 2>&1; then
  lsof -nP -iTCP:"${APP_PORT}" -sTCP:LISTEN || true
elif command -v ss >/dev/null 2>&1; then
  ss -ltnp "sport = :${APP_PORT}" || true
else
  echo "lsof/ss not found; skipping listener check."
fi

compose_up() {
  if podman compose version >/dev/null 2>&1; then
    podman compose -f "${COMPOSE_FILE}" up --build -d --remove-orphans
    return 0
  fi
  if command -v podman-compose >/dev/null 2>&1; then
    podman-compose -f "${COMPOSE_FILE}" up --build -d --remove-orphans
    return 0
  fi
  return 1
}

if command -v podman >/dev/null 2>&1; then
  echo "Starting containerized stack with Podman..."
  if compose_up; then
    echo
    echo "MODE: HTTPS via Caddy (Podman stack)"
    echo "App is starting in Podman:"
    echo "- Main app:  https://localhost:${APP_HTTPS_PORT}"
    echo "- HTTP port: http://localhost:${APP_HTTP_PORT}"
    if [[ "${SECURE_MODE_ENABLED,,}" == "1" ]]; then
      echo "- Secure mode: enabled (${APP_DOMAIN})"
      echo "- First-run URL: http://${APP_LAN_IP}:${APP_HTTP_PORT}"
      echo "- Preferred secure URL: https://${APP_DOMAIN}:${APP_HTTPS_PORT}"
    fi
    echo
    echo "Use: podman compose -f ${COMPOSE_FILE} logs -f"
    echo "Note: first-run certificate is issued by local internal CA."
    echo
    echo "======================================="
    echo "LOCAL ADMIN URL: https://localhost:${APP_HTTPS_PORT}/"
    if [[ "${SECURE_MODE_ENABLED,,}" == "1" ]]; then
      if [[ "${APP_HTTPS_PORT}" == "443" ]]; then
        echo "Secure-mode URL: https://${APP_DOMAIN}/"
      else
        echo "Secure-mode URL: https://${APP_DOMAIN}:${APP_HTTPS_PORT}/"
      fi
    fi
    echo "======================================="
    exit 0
  fi
  echo "Podman startup failed. Falling back to local uvicorn."
else
  echo "Podman not found. Falling back to local uvicorn."
fi

if [[ -f ".venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

echo "MODE: HTTP fallback via local uvicorn"
echo "Starting local fallback on 0.0.0.0:${FALLBACK_PORT} (HTTP, no TLS proxy)..."
echo "======================================="
echo "LOCAL ADMIN URL: http://localhost:${FALLBACK_PORT}/"
if [[ -n "${LAN_IP}" ]]; then
  echo "LAN URL: http://${LAN_IP}:${FALLBACK_PORT}/"
fi
echo "======================================="
exec uvicorn app.main:app --host 0.0.0.0 --port "${FALLBACK_PORT}"
