#!/bin/bash
set -euo pipefail
cd ~/appimage-build

APPIMAGE_NAME="HomepageClient-linux-x86_64.AppImage"
APPIMAGETOOL_URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
APPIMAGETOOL=~/appimage-build/appimagetool-x86_64.AppImage
APPDIR=~/appimage-build/build/HomepageClient.AppDir

if [ ! -x "$APPIMAGETOOL" ]; then
    echo "==> Downloading appimagetool..."
    curl -fSL "$APPIMAGETOOL_URL" -o "$APPIMAGETOOL"
    chmod +x "$APPIMAGETOOL"
fi

echo "==> Building AppImage..."
ARCH=x86_64 "$APPIMAGETOOL" --appimage-extract-and-run "$APPDIR" "dist/$APPIMAGE_NAME"

echo ""
echo "=== AppImage built ==="
ls -lh "dist/$APPIMAGE_NAME"
