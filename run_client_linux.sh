#!/usr/bin/env bash
set -euo pipefail

if [[ ! -d ".venv-client" ]]; then
  python3 -m venv .venv-client
fi

source .venv-client/bin/activate
python -m pip install --upgrade pip
pip install -r requirements-client.txt

python client_app.py
