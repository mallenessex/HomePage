#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [[ ! -x "./run_server.sh" ]]; then
  chmod +x ./run_server.sh
fi

exec ./run_server.sh
