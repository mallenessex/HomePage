#!/usr/bin/env bash
# Build the HOMEPAGE Client macOS .app bundle + .dmg installer.
# Run on a macOS machine from the repo root.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_DIR"

APP_NAME="HomepageClient"
DMG_NAME="HomepageClient-macos.dmg"

# ── 1. Create/reuse venv and install deps ─────────────────────────────────
if [ ! -d ".venv-client" ]; then
    python3 -m venv .venv-client
fi
source .venv-client/bin/activate
python -m pip install --upgrade pip
pip install -r requirements-client.txt
pip install pyinstaller

# ── 2. PyInstaller .app bundle ────────────────────────────────────────────
echo "==> Running PyInstaller..."
pyinstaller HOMEPAGEClient-macos.spec --noconfirm
echo "==> PyInstaller done."

# ── 3. Create DMG ─────────────────────────────────────────────────────────
echo "==> Creating DMG..."
DMG_DIR="$REPO_DIR/build/dmg-stage"
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
