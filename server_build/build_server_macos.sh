#!/usr/bin/env bash
# Build the HOMEPAGE Server macOS .app bundle + .dmg installer.
# Run on a macOS machine from the repo root.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_DIR"

APP_NAME="HOMEPAGEServer"
DMG_NAME="HomepageServer-macos.dmg"

echo "=== HOMEPAGE Server — macOS Build ==="

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

# ── 3. PyInstaller .app bundle ────────────────────────────────────────────
echo "==> Running PyInstaller..."
pyinstaller server_build/HOMEPAGEServer-macos.spec --noconfirm
echo "==> PyInstaller done."

# ── 4. Create DMG ─────────────────────────────────────────────────────────
echo "==> Creating DMG..."
DMG_DIR="$REPO_DIR/build/dmg-server-stage"
rm -rf "$DMG_DIR"
mkdir -p "$DMG_DIR"

# Copy the .app bundle
cp -a "dist/$APP_NAME.app" "$DMG_DIR/"

# Create a symlink to /Applications for drag-and-drop install
ln -s /Applications "$DMG_DIR/Applications"

# Build DMG
hdiutil create -volname "$APP_NAME" \
    -srcfolder "$DMG_DIR" \
    -ov -format UDZO \
    "dist/$DMG_NAME"

echo ""
echo "=== macOS DMG built: dist/$DMG_NAME ==="
ls -lh "dist/$DMG_NAME"
