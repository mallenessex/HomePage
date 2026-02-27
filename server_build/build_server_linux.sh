#!/usr/bin/env bash
# Build the HOMEPAGE Server Linux AppImage.
# Run on a Linux machine (or via WSL) from the repo root.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_DIR"

APPIMAGE_NAME="HomepageServer-linux-x86_64.AppImage"
APPIMAGETOOL_URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
APPIMAGETOOL="$REPO_DIR/appimagetool-x86_64.AppImage"

echo "=== HOMEPAGE Server — Linux Build ==="

# ── 1. Create/reuse venv and install deps ─────────────────────────────────
if [ ! -d ".venv-server-build" ]; then
    python3 -m venv .venv-server-build
fi
source .venv-server-build/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -r server_build/requirements-server-build.txt

# ── 2. Generate icon if missing ───────────────────────────────────────────
if [ ! -f "server_build/resources/icon.png" ]; then
    echo "==> Generating tray icon..."
    python server_build/resources/generate_icon.py
fi

# ── 3. PyInstaller onedir build ───────────────────────────────────────────
echo "==> Running PyInstaller (onedir)..."
pyinstaller server_build/HOMEPAGEServer-linux.spec --noconfirm
echo "==> PyInstaller done."

# ── 4. Assemble AppDir ────────────────────────────────────────────────────
APPDIR="$REPO_DIR/build/HomepageServer.AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin"
mkdir -p "$APPDIR/usr/share/icons/hicolor/scalable/apps"

# Copy PyInstaller output into AppDir
cp -a dist/HOMEPAGEServer-linux/* "$APPDIR/usr/bin/"

# AppRun script
cat > "$APPDIR/AppRun" << 'APPRUN_EOF'
#!/bin/bash
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/HOMEPAGEServer-linux" "$@"
APPRUN_EOF
chmod +x "$APPDIR/AppRun"

# Desktop file
cat > "$APPDIR/homepage-server.desktop" << 'DESKTOP_EOF'
[Desktop Entry]
Type=Application
Name=HOMEPAGE Server
Comment=Run your HOMEPAGE node
Exec=HOMEPAGEServer-linux
Icon=homepage-server
Categories=Network;WebServer;
Terminal=false
DESKTOP_EOF

# Simple SVG icon for the AppImage
cat > "$APPDIR/homepage-server.svg" << 'SVG_EOF'
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <circle cx="32" cy="32" r="30" fill="#4f46e5"/>
  <text x="32" y="44" text-anchor="middle" font-family="Arial,Helvetica,sans-serif" font-size="36" font-weight="bold" fill="white">H</text>
</svg>
SVG_EOF
cp "$APPDIR/homepage-server.svg" "$APPDIR/usr/share/icons/hicolor/scalable/apps/"

chmod +x "$APPDIR/usr/bin/HOMEPAGEServer-linux"

# ── 5. Download appimagetool if missing ───────────────────────────────────
if [ ! -x "$APPIMAGETOOL" ]; then
    echo "==> Downloading appimagetool..."
    curl -fSL "$APPIMAGETOOL_URL" -o "$APPIMAGETOOL"
    chmod +x "$APPIMAGETOOL"
fi

# ── 6. Build AppImage ────────────────────────────────────────────────────
echo "==> Building AppImage..."
ARCH=x86_64 "$APPIMAGETOOL" --appimage-extract-and-run "$APPDIR" "dist/$APPIMAGE_NAME"

echo ""
echo "=== AppImage built: dist/$APPIMAGE_NAME ==="
ls -lh "dist/$APPIMAGE_NAME"
