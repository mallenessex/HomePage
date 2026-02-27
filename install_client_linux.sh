#!/usr/bin/env bash
set -euo pipefail

BIN_NAME="HOMEPAGEClient-linux"
SRC_BIN="dist/${BIN_NAME}"
DESKTOP_SRC="packaging/linux/house-fantastico-client.desktop"
ICON_SRC="packaging/linux/house-fantastico-client.svg"

LOCAL_BIN_DIR="${HOME}/.local/bin"
LOCAL_APP_DIR="${HOME}/.local/share/applications"
LOCAL_ICON_DIR="${HOME}/.local/share/icons/hicolor/scalable/apps"

if [[ ! -f "${SRC_BIN}" ]]; then
  echo "Missing ${SRC_BIN}. Building first..."
  ./build_client_linux.sh
fi

mkdir -p "${LOCAL_BIN_DIR}" "${LOCAL_APP_DIR}" "${LOCAL_ICON_DIR}"

install -m 0755 "${SRC_BIN}" "${LOCAL_BIN_DIR}/${BIN_NAME}"
install -m 0644 "${ICON_SRC}" "${LOCAL_ICON_DIR}/house-fantastico-client.svg"
install -m 0644 "${DESKTOP_SRC}" "${LOCAL_APP_DIR}/house-fantastico-client.desktop"

update-desktop-database "${LOCAL_APP_DIR}" >/dev/null 2>&1 || true
gtk-update-icon-cache "${HOME}/.local/share/icons/hicolor" >/dev/null 2>&1 || true

echo "Installed:"
echo "  Binary:  ${LOCAL_BIN_DIR}/${BIN_NAME}"
echo "  Desktop: ${LOCAL_APP_DIR}/house-fantastico-client.desktop"
echo "  Icon:    ${LOCAL_ICON_DIR}/house-fantastico-client.svg"
echo
echo "Open from your app launcher: 'House Fantastico Client'"
