# HOMEPAGE Client App (Windows + Linux)

This repo includes a separate desktop client for connecting to a HOMEPAGE server: `client_app.py`.

## Goal

- Connect to a server using its `server_id`.
- Submit a join handshake request.
- Wait for admin approval (if policy is conditional).
- Open a contained browser that blocks navigation outside the selected server origin.

## Files

- `client_app.py` - desktop client UI + contained web shell
- `requirements-client.txt` - client dependencies

## Install (Windows)

```powershell
python -m venv .venv-client
.venv-client\Scripts\Activate.ps1
pip install -r requirements-client.txt
python client_app.py
```

## Usage

1. Enter `Server URL`:
   - first run: `http://<HOST_LAN_IP>:8001`
2. Click `Fetch Secure Profile`.
3. Click `Apply Secure Setup`.
   - This downloads the server CA and applies trust + hosts mapping automatically where permissions allow.
   - On Windows, run the client as Administrator if hosts update is blocked.
4. Switch `Server URL` to secure URL shown in the app guide (usually `https://<SERVER_NAME>.local:8443`).
5. Enter `Target Server ID` (footer, `/.well-known/server-id`, or `/.well-known/connect-profile`).
6. Enter desired username/display name.
7. Click `Request Join`.
8. Admin approves/rejects in `/admin/settings`.
9. Click `Connect + Open` (or `Check Status` then `Open Contained App`).

## Secure Setup In App

- New UI section: `Secure Mode Setup`
- Buttons:
  - `Fetch Secure Profile`
  - `Apply Secure Setup`
  - `Copy Instructions`
- The guide box in the app shows:
  - first-run URL
  - secure URL
  - server ID
  - domain/IP mapping
  - OS-specific permission notes

## Security behavior (current)

- The web container blocks navigation to URLs outside the configured server origin.
- `window.open` is disabled in the contained shell.
- External-origin anchor clicks are blocked client-side.
- For private/local HTTPS URLs, the client allows self-signed TLS so local deployments can connect without a public CA.

Notes:
- This is an app-level containment model, not a full OS-level network sandbox.
- For stronger child lock-down, pair this with OS controls (family safety, firewall policy, DNS allowlist).

## Packaging to a shareable EXE (Windows)

> **Note**: The binary is named `HOMEPAGEClient` by default.

```powershell
pip install pyinstaller
pyinstaller --noconsole --onefile --name HOMEPAGEClient-windows client_app.py
```

Output binary:

- `dist\HOMEPAGEClient-windows.exe`

## Install (Linux)

### 1) System packages (Ubuntu/Debian example)

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip \
  python3-tk python3-gi gir1.2-webkit2-4.1
```

### 2) Python env + run

```bash
python3 -m venv .venv-client
source .venv-client/bin/activate
pip install -r requirements-client.txt
python client_app.py
```

### 3) Build a shareable Linux binary

```bash
chmod +x build_client_linux.sh
./build_client_linux.sh
```

Output binary:

- `dist/HOMEPAGEClient-linux`

Notes:
- Build on the same distro family/version you plan to run on for best compatibility.
- For strict distribution portability, package in a containerized build environment.

## Linux app launcher install

Install user-local binary + app launcher:

```bash
chmod +x install_client_linux.sh
./install_client_linux.sh
```

This installs:
- `~/.local/bin/HOMEPAGEClient-linux`
- `~/.local/share/applications/house-fantastico-client.desktop`
- `~/.local/share/icons/hicolor/scalable/apps/house-fantastico-client.svg`

Uninstall:

```bash
chmod +x uninstall_client_linux.sh
./uninstall_client_linux.sh
```

## Build Linux binary from Windows (WSL)

From PowerShell in this repo:

```powershell
powershell -ExecutionPolicy Bypass -File .\build_client_linux_from_windows.ps1
```

If your distro name is not `Ubuntu`:

```powershell
powershell -ExecutionPolicy Bypass -File .\build_client_linux_from_windows.ps1 -Distro "Ubuntu-22.04"
```

Output after successful build:

- `dist/HOMEPAGEClient-linux`
