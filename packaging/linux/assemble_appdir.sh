#!/bin/bash
set -euo pipefail
cd ~/appimage-build

APPDIR=~/appimage-build/build/HomepageClient.AppDir
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin"
mkdir -p "$APPDIR/usr/share/icons/hicolor/scalable/apps"

cp -a dist/HOMEPAGEClient-linux/* "$APPDIR/usr/bin/"
cp packaging/linux/AppRun              "$APPDIR/AppRun"
cp packaging/linux/homepage-client.desktop "$APPDIR/homepage-client.desktop"
cp packaging/linux/homepage-client.svg "$APPDIR/homepage-client.svg"
cp packaging/linux/homepage-client.svg "$APPDIR/usr/share/icons/hicolor/scalable/apps/homepage-client.svg"

chmod +x "$APPDIR/AppRun"
chmod +x "$APPDIR/usr/bin/HOMEPAGEClient-linux"

echo "APPDIR_READY"
du -sh "$APPDIR"
