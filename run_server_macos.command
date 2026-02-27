#!/usr/bin/env bash
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [[ ! -x "./run_server.sh" ]]; then
  chmod +x ./run_server.sh
fi

./run_server.sh
EXIT_CODE=$?

echo
echo "run_server.sh exited with code ${EXIT_CODE}."
echo "Press Enter to close this window."
read -r _
exit "$EXIT_CODE"
