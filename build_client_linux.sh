#!/usr/bin/env bash
# Build the HOMEPAGE Client Linux AppImage.
# Run on a Linux machine (or via WSL) from the repo root.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_DIR"

APPIMAGE_NAME="HomepageClient-linux-x86_64.AppImage"
APPIMAGETOOL_URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
APPIMAGETOOL="$REPO_DIR/appimagetool-x86_64.AppImage"

# ── 1. Create/reuse venv and install deps ─────────────────────────────────
if [ ! -d ".venv-client" ]; then
    python3 -m venv .venv-client
fi
source .venv-client/bin/activate
python -m pip install --upgrade pip
pip install -r requirements-client.txt
pip install pyinstaller

# ── 2. PyInstaller onedir build ───────────────────────────────────────────
echo "==> Running PyInstaller (onedir)..."
pyinstaller HOMEPAGEClient-linux.spec --noconfirm
echo "==> PyInstaller done."

# ── 3. Assemble AppDir ────────────────────────────────────────────────────
APPDIR="$REPO_DIR/build/HomepageClient.AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin"
mkdir -p "$APPDIR/usr/share/icons/hicolor/scalable/apps"

# Copy PyInstaller output into AppDir
cp -a dist/HOMEPAGEClient-linux/* "$APPDIR/usr/bin/"

# Desktop integration files (strip Windows CRLF if present)
cp packaging/linux/AppRun              "$APPDIR/AppRun"
cp packaging/linux/homepage-client.desktop "$APPDIR/homepage-client.desktop"
cp packaging/linux/homepage-client.svg "$APPDIR/homepage-client.svg"
cp packaging/linux/homepage-client.svg "$APPDIR/usr/share/icons/hicolor/scalable/apps/homepage-client.svg"

sed -i 's/\r$//' "$APPDIR/AppRun" "$APPDIR/homepage-client.desktop"

chmod +x "$APPDIR/AppRun"
chmod +x "$APPDIR/usr/bin/HOMEPAGEClient-linux"

# ── 4. Download appimagetool if missing ───────────────────────────────────
if [ ! -x "$APPIMAGETOOL" ]; then
    echo "==> Downloading appimagetool..."
    curl -fSL "$APPIMAGETOOL_URL" -o "$APPIMAGETOOL"
    chmod +x "$APPIMAGETOOL"
fi

# ── 5. Build AppImage ────────────────────────────────────────────────────
echo "==> Building AppImage..."
# Use --appimage-extract-and-run so FUSE/libfuse2 is NOT required on the build
# machine (common in WSL, containers, CI).
ARCH=x86_64 "$APPIMAGETOOL" --appimage-extract-and-run "$APPDIR" "dist/$APPIMAGE_NAME"

echo ""
echo "=== AppImage built: dist/$APPIMAGE_NAME ==="
ls -lh "dist/$APPIMAGE_NAME"
