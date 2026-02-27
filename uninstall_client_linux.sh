#!/usr/bin/env bash
set -euo pipefail

rm -f "${HOME}/.local/bin/HOMEPAGEClient-linux"
rm -f "${HOME}/.local/share/applications/house-fantastico-client.desktop"
rm -f "${HOME}/.local/share/icons/hicolor/scalable/apps/house-fantastico-client.svg"

update-desktop-database "${HOME}/.local/share/applications" >/dev/null 2>&1 || true
gtk-update-icon-cache "${HOME}/.local/share/icons/hicolor" >/dev/null 2>&1 || true

echo "Removed House Fantastico Client launcher and binary from user-local paths."
